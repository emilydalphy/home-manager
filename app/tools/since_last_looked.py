"""
"Since you last looked" — what the OTHER adult did, the first card on Today.

Loop Board 'Household: "Since you last looked" — show the other adult what
changed' (Emily, 2026-10-18, "Yes to all"). As the adult picked on "Who's
this?", when I open Today I see the handful of things my partner did since
I last opened it — once, then gone — so we don't have to ask each other
"did you approve the week?" across the kitchen.

ONLY FACTS POMONA ALREADY RECORDS. No second recording path: every line
below reads a column some other feature already writes, and a thing nothing
records (who moved or swapped a dinner) is left out rather than guessed:

  - week approved          weekly_plans.approved_by_member_id / approved_at
  - questions answered     week_intake.created_by / created_at (a NAME)
  - added to the list      grocery_items.added_by / created_at (a NAME)
  - dropped before a shop  grocery_items.removed_by / removed_at (a NAME —
                           only the pre-shop drop and "use what's at home"
                           write a person's name there; Remove writes
                           'list', staples 'staple', the carry-over step
                           its own markers)
  - a cook / fridge / prep move_credits.ticked_by_member_id (the one place
                           a tick is recorded — move_credits.py says so; who
                           TAPPED it, not the owner it was credited to),
                           counted only while its row still reads done
  - "put you on"           move_owner_overrides.set_by / set_at, naming
                           THIS adult as member_id

The three NAME columns are matched to the other adult's name, case-folded —
the convention _shared.acting_name writes them in. Two adults with the same
name can't be told apart that way, so those three are skipped for them.

WHEN IT SHOWS. `members.last_looked_at` (UTC, SQLite's datetime('now')
shape, like every timestamp read here). A look reads everything after it
and stamps it, so the lines show on this open and are gone on the next
unless something new happened. NULL — this adult's very first look, or
the first since this shipped — stamps and returns nothing: never a history
dump. A household with one adult, or no adult picked on this device, gets
nothing and stamps nothing. Only a LOOK stamps (look() below, which
/api/today/moves calls for ?look=1 alone): Today re-reads its moves after
every tick and chat turn, and a stamp on each of those would make the card
vanish while the adult is still reading it. See shell.js
renderSinceLooked for the client half.

Strictly after the stamp, at one-second grain: something done in the same
second as a look is counted as seen. A rare miss, never a repeat.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from ..db import get_conn
from . import _shared
from ._shared import household_id
from . import cooker as _cooker

# Rows the card draws before "and N more" (the card: max 3).
SHOWN_ROWS = 3

# Grocery statuses that are still on the list — a line added and since
# taken off, or carried from an ended week, is not "added".
_ON_LIST_EXCLUDED = ("removed", "carried")

_IRREGULAR_PAST = {
    "make": "made", "take": "took", "put": "put", "cut": "cut", "set": "set",
    "get": "got", "freeze": "froze", "run": "ran", "do": "did", "leave": "left",
    "shred": "shredded", "chop": "chopped", "prep": "prepped", "stir": "stirred",
    "trim": "trimmed", "pat": "patted", "dip": "dipped", "whip": "whipped",
    "wrap": "wrapped", "scrub": "scrubbed", "bring": "brought", "grind": "ground",
}
_REGULAR_VERB = re.compile(r"^[a-z]{3,}$")


def _lower_first(text: str) -> str:
    """'Move the chicken…' → 'move the chicken…'; 'IKEA bags' stays."""
    text = (text or "").strip()
    if len(text) > 1 and text[0].isupper() and not text[1].isupper():
        return text[0].lower() + text[1:]
    return text


def _past_tense(title: str) -> str | None:
    """
    An imperative move title in the past: 'Move the chicken thighs to the
    fridge' → 'moved the chicken thighs to the fridge'. None when the
    first word doesn't look like a verb this can conjugate — the caller
    then says it a plainer way rather than inventing a word.
    """
    words = _lower_first(title).split(" ", 1)
    verb = words[0].lower()
    rest = (" " + words[1]) if len(words) > 1 else ""
    if verb in _IRREGULAR_PAST:
        return _IRREGULAR_PAST[verb] + rest
    if not _REGULAR_VERB.match(verb) or verb.endswith(("ed", "ing")) or (
        verb.endswith("s") and not verb.endswith("ss")
    ):
        return None
    if verb.endswith("e"):
        past = verb + "d"
    elif verb.endswith("y") and verb[-2] not in "aeiou":
        past = verb[:-1] + "ied"
    else:
        past = verb + "ed"
    return past + rest


def _names_line(items: list[str]) -> str:
    """'milk' / 'milk and eggs' / 'milk, eggs and bread' / 'milk, eggs and 3 more'."""
    if len(items) == 1:
        return items[0]
    if len(items) <= 3:
        return ", ".join(items[:-1]) + " and " + items[-1]
    return f"{items[0]}, {items[1]} and {len(items) - 2} more"


def _distinct(names: list[str]) -> list[str]:
    seen, out = set(), []
    for n in names:
        key = (n or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(_lower_first(n))
    return out


def _day_word(on: date, today: date) -> str:
    if on == today:
        return "today’s"
    if on == today + timedelta(days=1):
        return "tomorrow’s"
    return on.strftime("%A") + "’s"


def _move_title(description: str) -> str:
    # Task descriptions are "<the move> — <what it's for>." (defrost._describe);
    # the title is the first half, the same split moves.py makes.
    head, _, _ = (description or "").strip().partition(" — ")
    return head.rstrip(".").strip()


def _lines_for(conn, other: dict, me: dict, since: str, today: date, name_ok: bool) -> list[dict]:
    hid = household_id()
    who = other["name"]
    key = who.strip().lower()
    out: list[dict] = []

    def line(text: str, at: str, *, for_you: bool = False, target: dict | None = None):
        out.append({"text": f"{who} {text}", "at": at, "for_you": for_you, "target": target})

    # "Who's on it?" picks that name me — the things left FOR me.
    for r in conn.execute(
        "SELECT move_id, on_date, set_at FROM move_owner_overrides "
        "WHERE household_id = ? AND set_by = ? AND member_id = ? AND set_at > ? AND on_date >= ? "
        "ORDER BY set_at",
        (hid, other["id"], me["id"], since, today.isoformat()),
    ):
        kind, _, ref = r["move_id"].partition(":")
        on = date.fromisoformat(r["on_date"])
        day = _day_word(on, today)
        if kind == "cook" and ref.isdigit():
            e = conn.execute(
                "SELECT e.id, e.date, e.slot, COALESCE(rc.name, e.freeform_meal, '') AS dish "
                "FROM meal_plan_entries e LEFT JOIN recipes rc ON rc.id = e.recipe_id "
                "WHERE e.id = ? AND e.household_id = ?",
                (int(ref), hid),
            ).fetchone()
            # A dish swapped off the day it was claimed on leaves its row
            # behind unread (move_owner's rule) — so does this.
            if e is None or e["date"] != r["on_date"]:
                continue
            line(f"put you on {day} {e['slot'] or 'dinner'}.", r["set_at"], for_you=True, target={
                "tab": "kitchen",
                "cookFocus": {"entryId": e["id"], "date": e["date"], "slot": e["slot"], "title": e["dish"]},
            })
        elif kind in ("fridge", "prep") and ref.isdigit():
            t = conn.execute(
                "SELECT task_date FROM prep_tasks WHERE id = ? AND household_id = ?", (int(ref), hid),
            ).fetchone()
            if t is None or t["task_date"] != r["on_date"]:
                continue
            word = "fridge move" if kind == "fridge" else "prep"
            line(f"put you on {day} {word}.", r["set_at"], for_you=True, target={"tab": "kitchen"})
        elif kind == "shop":
            line(f"put you on {day} shop.", r["set_at"], for_you=True, target={"tab": "grocery"})

    # The week approved — one line however many weeks it was.
    approved = conn.execute(
        "SELECT MAX(approved_at) AS at FROM weekly_plans "
        "WHERE household_id = ? AND approved_by_member_id = ? AND status = 'approved' AND approved_at > ?",
        (hid, other["id"], since),
    ).fetchone()
    if approved and approved["at"]:
        line("approved the week.", approved["at"])

    # Moves THEY ticked — ticked_by, never the credited owner: with Vineeth
    # as the standing cook, a dinner Emily ticked is not one he made. A
    # credit with nobody picked on the device (NULL) is no one's to tell.
    for r in conn.execute(
        "SELECT c.kind, c.credited_at, COALESCE(rc.name, e.freeform_meal, '') AS dish, t.description "
        "FROM move_credits c "
        "LEFT JOIN meal_plan_entries e ON e.id = c.meal_plan_entry_id AND e.household_id = c.household_id "
        "LEFT JOIN recipes rc ON rc.id = e.recipe_id "
        "LEFT JOIN prep_tasks t ON t.id = c.prep_task_id AND t.household_id = c.household_id "
        "WHERE c.household_id = ? AND c.ticked_by_member_id = ? AND c.credited_at > ? "
        "AND c.kind IN ('cook', 'fridge', 'prep') "
        "AND (e.cooked_status = 'done' OR t.status = 'done') ORDER BY c.credited_at, c.id",
        (hid, other["id"], since),
    ):
        if r["kind"] == "cook":
            if (r["dish"] or "").strip():
                line(f"made the {r['dish'].strip()}.", r["credited_at"])
            continue
        title = _move_title(r["description"])
        if not title:
            continue
        past = _past_tense(title)
        line((past + ".") if past else f"did the prep: {_lower_first(title)}.", r["credited_at"])

    if not name_ok:
        return out

    intake = conn.execute(
        "SELECT week_start, MAX(created_at) AS at FROM week_intake "
        "WHERE household_id = ? AND lower(trim(created_by)) = ? AND created_at > ? "
        "GROUP BY week_start ORDER BY at",
        (hid, key, since),
    ).fetchall()
    # "Next" is a period that starts after today, not after this calendar
    # week's Monday (defect hunt 2026-10-26): every period the app offers
    # starts no earlier than today, so a Sunday-start household answering
    # on its Sunday — or anyone answering mid-week for the rest of this
    # week — was told it was next week's.
    for r in intake:
        which = "next week’s" if r["week_start"] > today.isoformat() else "this week’s"
        line(f"answered {which} questions.", r["at"])

    added = conn.execute(
        "SELECT item, created_at FROM grocery_items "
        f"WHERE household_id = ? AND lower(trim(added_by)) = ? AND created_at > ? "
        f"AND status NOT IN ({','.join('?' * len(_ON_LIST_EXCLUDED))}) AND excluded_from_list = 0 "
        "ORDER BY created_at, id",
        (hid, key, since, *_ON_LIST_EXCLUDED),
    ).fetchall()
    names = _distinct([r["item"] for r in added])
    if names:
        line(f"added {_names_line(names)}.", added[-1]["created_at"])

    # A line they added AND took off since the last look is nothing to tell.
    dropped = conn.execute(
        "SELECT g.item, g.removed_at FROM grocery_items g "
        "WHERE g.household_id = ? AND g.status = 'removed' AND lower(trim(g.removed_by)) = ? "
        "AND g.removed_at > ? "
        "AND NOT (g.created_at > ? AND lower(trim(g.added_by)) = ?) "
        "AND NOT EXISTS (SELECT 1 FROM grocery_substitutions s WHERE s.household_id = g.household_id "
        "AND s.grocery_item_id = g.id AND s.at_home = 1) "
        "ORDER BY g.removed_at, g.id",
        (hid, key, since, since, key),
    ).fetchall()
    names = _distinct([r["item"] for r in dropped])
    if names:
        line(f"took {_names_line(names)} off the list.", dropped[-1]["removed_at"])
    return out


def look(*, stamp: bool = True) -> list[dict]:
    """
    The lines for this adult's open of Today, newest facts since their last
    look: [{text, for_you, target}], things left FOR them first, the rest
    in the order they happened. Stamps the look (unless `stamp` is False).
    [] — and no stamp — when there is no picked adult or only one adult.
    """
    me = _shared.current_member()
    adults = _shared.household_adults()
    if me is None or len(adults) < 2:
        return []
    others = [a for a in adults if a["id"] != me["id"]]
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT last_looked_at, datetime('now') AS now FROM members WHERE id = ? AND household_id = ?",
            (me["id"], household_id()),
        ).fetchone()
        since, now = row["last_looked_at"], row["now"]
        lines: list[dict] = []
        if since:
            today = _cooker.household_today(conn=conn)
            for other in others:
                name_ok = bool(other["name"]) and not any(
                    a["name"].strip().lower() == other["name"].strip().lower()
                    for a in adults if a["id"] != other["id"]
                )
                lines.extend(_lines_for(conn, other, me, since, today, name_ok))
        if stamp:
            # MAX: two looks racing (two tabs, a resume and a reload) must
            # never move the stamp backwards and show the same lines again.
            conn.execute(
                "UPDATE members SET last_looked_at = MAX(COALESCE(last_looked_at, ''), ?) "
                "WHERE id = ? AND household_id = ?",
                (now, me["id"], household_id()),
            )
            conn.commit()
    finally:
        conn.close()
    lines.sort(key=lambda l: (not l["for_you"], l["at"]))
    return [{"text": l["text"], "for_you": l["for_you"], "target": l["target"]} for l in lines]
