"""
Whose move is it — one answer, read by Today, by Cook, and by nothing else.

Loop Board "Every move has an owner (slice 1)": *as an adult in the
household, I want each cook, prep and shop on Today to show whose it is,
so that we both know without asking.*

This is NOT a second "who cooks" source. `cooking_role` (rhythm.py, the
third of the six locked rhythm questions) has been the app's answer since
2026-09-03 and `cooker._cook_name` has been reading its `one_person` half
onto Cook's Tonight card since 2026-09-13. That function now delegates
here in one line; this module is that same answer widened to the other two
values of the same fact, and put on a move rather than on the view.

Three values, three answers:

    one_person     the name the household gave, on every cook.
    turns          the adults, in the order setup named them, one cook DAY
                   each, round and round from the plan's first cook day.
    whoever_free   nobody. The household said it isn't fixed; saying a
                   name would be inventing one.

An unanswered question is `whoever_free` in every way that matters here:
nothing is claimed. That is what makes this safe to ship — a household
that has never answered renders exactly what it rendered before.

WHAT CARRIES AN OWNER, and what deliberately does not:

- A COOK does. That is what `cooking_role` is an answer about.
- A FRIDGE MOVE or a PREP TASK does, when it says which meal it is for
  (`prep_tasks.meal_plan_entry_id`) and that meal is a cook: it takes that
  cook's owner, on the cook's night rather than its own. This is reading a
  link that is already there, not a guess — the app files a thaw under the
  meal it feeds everywhere else (defrost._describe writes "for Thursday's
  skewers"; cookFocusPrepTasks shows it on that meal's own screen), and
  under `turns` "you're cooking Thursday, so the chicken is yours to move"
  is the exact thing the household would otherwise have to ask about. A
  task with no entry behind it gets nobody: matching by dish NAME would be
  a guess, and this module doesn't make any.
- A REHEAT does NOT. Nothing is cooked on a reheat night — the whole app
  is careful about that (moves.py never features one, cooker never scales
  one, time_caps exempts one), so "Emily's cooking" over a plate being
  warmed is a thing that isn't true, and inventing a second verb for it
  would be inventing a fact nobody stated.
- A SHOP does NOT, and it is worth saying why plainly rather than leaving
  it looking like an oversight: THERE IS NO "WHO SHOPS" ANSWER ANYWHERE IN
  THIS APP. Checked when this was built — `household_rhythm` holds seven
  fact types (lunch_location, meals_together, cooking_role, dinner_window,
  planning_anchor, leftovers_stance, prep_days) and none of them asks;
  `meal_preferences` has no such column; nothing in `members`; and a grep
  of app/ and static/ for shopper/who_shops/shopping_role finds only the
  grocery list's own use of the word "shopper" for whoever happens to be
  holding the phone. Guessing that the one person who COOKS also shops
  would be exactly the kind of traditional assumption rhythm.py's own
  docstring exists to refuse. So the shop is empty until somebody is
  asked, and asking is a question for Emily, not for this module.

COST, measured rather than reasoned about. Resolving this is ONE read of
the rhythm — which moves_for_day was already making for `_dinner_clock`, so
it is threaded rather than repeated — plus, for the two answers that name
somebody (`turns` AND `one_person`), one read of the members table.
`whoever_free` and an unanswered household spend nothing beyond the rhythm
read they already paid for, and that is every household this ships to on
day one. Measured at sqlite3.connect over a whole Today payload on a busy
day of five moves: main 71; this branch 71 unanswered, 71 whoever_free, 72
one_person, 72 turns.

It is resolved ONCE per moves_for_day call and handed to the stamping pass,
never per move: a per-move read would be a connection per row, which is the
mistake CLAUDE.md's clock entries record being made twice already.

SLICE 2 (2026-10-07): "actually I've got tonight". A household can change
whose ONE move is, by a tap on Today or by saying so in chat, and the
answer above becomes the DEFAULT rather than the last word. The change
lives in `move_owner_overrides` (schema.sql), one row per move per day,
read in the same `resolve` pass every reader already goes through — so
Today, Cook, the morning text and the evening nudge cannot disagree about
it. It is never a standing rule: tomorrow's cook is still whoever
cooking_role says. Two rules about what a row reaches:

- A COOK's row reaches the fridge move and prep behind that cook, exactly
  the way the default does: "Vineeth's got Thursday" makes Thursday's
  thaw his too, unless that thaw has a row of its own.
- A row is keyed by the day the move sat on when it was claimed. A dish
  later swapped onto another night leaves its row behind, rather than
  carrying "Vineeth's got it" onto a night he never said.

A SHOP can carry an owner now, but only one somebody SAID. The no-"who
shops"-answer reasoning above still holds for the default; a person
tapping "Me" on the shop is not a guess.
"""
from __future__ import annotations

import logging
from datetime import timedelta as _timedelta

from ..db import get_conn
from . import _shared
from ._shared import household_id
from . import moves as _moves
from . import rhythm as _rhythm

logger = logging.getLogger(__name__)


def _adults() -> list[dict]:
    """
    The adults on record, in the order setup named them.

    `ORDER BY id` IS that order — members are inserted as the household
    step of onboarding adds them — and it is the same rule
    household.first_run_hints and db._backfill_member_colors already use
    for "which adult", so the turns rotation and the avatar colours agree
    about who is first. LOWER() because onboarding writes "Adult" and older
    rows say "adult" (the 2026-08-31 entry: both of those readers matched
    'adult' exactly and found nobody).
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, name FROM members
            WHERE household_id = ? AND LOWER(age_group) = 'adult' AND TRIM(name) != ''
            ORDER BY id
            """,
            (household_id(),),
        ).fetchall()
    finally:
        conn.close()
    return [{"id": r["id"], "name": (r["name"] or "").strip()} for r in rows]


def _overrides() -> dict[tuple[str, str], tuple[int | None, str | None]]:
    """
    Every per-move owner the household has said, as
    {(move_id, on_date): (member id, name)} — (None, None) being "Nobody
    yet", said on purpose. ONE read per resolve, never per move.

    The JOIN is what keeps a name honest: a row whose adult has since been
    re-marked a child, or is somehow not this household's, is dropped and
    the move falls back to its default rather than naming them.
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT o.move_id, o.on_date, o.member_id, m.name
            FROM move_owner_overrides o
            LEFT JOIN members m
              ON m.id = o.member_id AND m.household_id = o.household_id
             AND {_shared._ADULT_SQL}
            WHERE o.household_id = ?
            """,
            (household_id(),),
        ).fetchall()
    finally:
        conn.close()
    out: dict[tuple[str, str], tuple[int | None, str | None]] = {}
    for r in rows:
        if r["member_id"] is None:
            out[(r["move_id"], r["on_date"])] = (None, None)
        elif r["name"] and r["name"].strip():
            out[(r["move_id"], r["on_date"])] = (r["member_id"], r["name"].strip())
    return out


def _is_a_cook(meal: dict) -> bool:
    """
    Exactly what becomes a cook move in moves._cook_and_reheat_moves: a
    dated entry that is not a reheat. Kept as one predicate so the
    rotation can never count a night the screen doesn't draw a cook on.
    """
    return bool(meal.get("date")) and not meal.get("is_leftovers")


class MoveOwners:
    """
    The day's answer to "whose is it", resolved once and asked many times.

    Built by `resolve` below; `for_meal` and `for_task` are the only two
    questions. Everything it needs is read in the constructor, so asking
    it costs nothing.
    """

    def __init__(self, role: dict | None, adults: list[dict], view: dict | None,
                 overrides: dict | None = None):
        self.value = (role or {}).get("value") or ""
        self.who = ((role or {}).get("who") or "").strip()
        self.adults = adults
        # Slice 2: what the household SAID about one move on one day. See
        # override() below and the module docstring.
        self._overrides = overrides or {}
        # entry_id -> the cook move's own id, so a fridge or prep task can
        # find its cook's override. Not always "cook:<entry_id>": a merged
        # component card is ONE move named after its first entry.
        self._cook_move_by_entry: dict[int, str] = {}
        # entry_id -> the date its cook sits on, so a prep task can find
        # the night it is for. Both keys, because a component plan's merged
        # card carries `entry_ids` and every other carries `entry_id` —
        # the same reading cooker.start_cooking makes of one.
        self._cook_date_by_entry: dict[int, str] = {}
        cook_days: set[str] = set()
        floor = ((view or {}).get("period_start_date") or "") if view else ""
        for meal in (view or {}).get("meals") or []:
            if not _is_a_cook(meal):
                continue
            day = meal["date"]
            for entry_id in (meal.get("entry_ids") or [meal.get("entry_id")]):
                if entry_id is not None:
                    self._cook_date_by_entry[entry_id] = day
                    if meal.get("entry_id") is not None:
                        self._cook_move_by_entry[entry_id] = f"cook:{meal['entry_id']}"
            # A cook day from BEFORE the plan's period is a leftover of an
            # earlier week (get_cooker_view folds loose meals in beside the
            # plan's own); counting it would move the rotation's anchor
            # without the household having changed anything.
            if not floor or day >= floor:
                cook_days.add(day)
        self._cook_days = sorted(cook_days)

    # ---------- the three answers ----------

    def _turns_owner(self, day: str) -> dict | None:
        """
        THE JUDGEMENT CALL, and it is Emily's to overrule in one line (the
        `% len` here, and `_adults`' ORDER BY above).

        "We take turns" records WHO but no ORDER and no ANCHOR, so both are
        picked here:

          ORDER — the adults as setup named them (members.id ascending).
          ANCHOR — the plan's first cook DAY is the first adult's; the
                   second cook day is the second adult's; round and round.

        Cook DAYS, not cook meals: Emily's own words are "alternates cook
        nights between the adults", and a 21-slot week alternating by MEAL
        would hand one adult breakfast and the other lunch on the same day,
        which is not what anybody means by taking turns. Every cook on a
        day therefore belongs to that day's cook.

        It is deterministic from the plan alone — a tick, a start, a swap
        of dish, a re-read all leave it exactly where it was, which is the
        one thing this must not get wrong. TWO honest limits:

        - Adding or dropping a cook DAY mid-week re-indexes every day after
          it. That is a change to the week rather than a wobble between two
          reads of the same one, and the alternative (anchor on the date's
          distance from period_start) is worse: a household cooking Monday,
          Wednesday and Friday with two adults would give every night to
          the same person, since 0, 2 and 4 are all even.
        - With NO plan at all the anchor is the earliest cook day the view
          holds, and that view is a sliding window of loose meals ahead —
          so it can move as days pass. Such a household has essentially
          nothing on Today anyway; a plan is what makes this stable.

        One adult is a rotation of one — every cook is theirs, which is the
        truthful answer rather than a special case. Three or more rotate in
        the same order, one cook day each.
        """
        if not self.adults:
            return None
        try:
            i = self._cook_days.index(day)
        except ValueError:
            # A cook on a day the rotation never counted (before the plan's
            # period). Nobody, rather than a name picked off the end.
            return None
        return self.adults[i % len(self.adults)]

    def for_meal(self, meal: dict) -> tuple[int | None, str | None]:
        """(member id, name) for a cook, or (None, None). A reheat is never
        a cook — see the module docstring."""
        if not _is_a_cook(meal):
            return (None, None)
        if self.value == "one_person":
            if not self.who:
                return (None, None)
            # The answer is free text (rhythm.set_cooking_role takes a
            # name, not a member), so it may or may not be somebody on
            # record. The name is what the screen says either way; the id
            # rides along only when it is genuinely known, because slice 3
            # will credit a tick with it and a guessed id would credit the
            # wrong person.
            match = next(
                (a for a in self.adults if a["name"].casefold() == self.who.casefold()),
                None,
            )
            return ((match or {}).get("id"), self.who)
        if self.value == "turns":
            adult = self._turns_owner(meal.get("date") or "")
            return ((adult or {}).get("id"), (adult or {}).get("name"))
        return (None, None)

    def override(self, move_id: str | None, day: str | None) -> tuple[int | None, str | None] | None:
        """
        What the household said about this one move on this day, or None
        when they said nothing (the default applies). (None, None) is a
        real answer — "Nobody yet" — and is returned as such.
        """
        if not move_id or not day:
            return None
        return self._overrides.get((move_id, day))

    def for_task(self, task: dict) -> tuple[int | None, str | None]:
        """
        (member id, name) for a fridge move or a prep task: whoever cooks
        the meal it is for — including a cook somebody has taken over for
        that night (slice 2). Nobody when it names no meal, or names one
        that is not a cook.
        """
        entry_id = task.get("meal_plan_entry_id")
        if entry_id is None:
            return (None, None)
        day = self._cook_date_by_entry.get(entry_id)
        if not day:
            return (None, None)
        said = self.override(self._cook_move_by_entry.get(entry_id), day)
        if said is not None:
            return said
        return self.for_meal({"date": day, "is_leftovers": False})


def resolve(view: dict | None = None, rhythm: dict | None = None) -> MoveOwners:
    """
    One resolver for one day's moves. `rhythm` lets the caller hand in the
    read it has already made (moves_for_day makes one for the dinner
    clock); `view` is the cooker view the moves are built from, which is
    where the rotation's cook days come from.

    Anything raising is "nobody owns anything" rather than a screen that
    doesn't render: a name is a nicety and the moves are the point.
    """
    try:
        rhythm = rhythm if rhythm is not None else _rhythm.get_household_rhythm()
        role = rhythm.get("cooking_role") or {}
        # Both answers that name somebody need the adults on record, for
        # different reasons: `turns` rotates through them, and `one_person`
        # matches its free-text `who` against them so the move can carry a
        # member id (see for_meal). `whoever_free` and an unanswered
        # household claim nobody, so they spend no query at all beyond the
        # rhythm read the caller already made.
        adults = _adults() if role.get("value") in ("turns", "one_person") else []
        try:
            said = _overrides()
        except Exception:
            # The defaults still stand on their own; one unreadable table
            # must not take the rhythm's answer down with it.
            logger.exception("Couldn't read the per-move owners; using the defaults")
            said = {}
        return MoveOwners(role, adults, view, said)
    except Exception:
        logger.exception("Couldn't work out whose moves these are; leaving them unowned")
        return MoveOwners(None, [], None)


def one_person_cook_name(rhythm: dict | None = None) -> str | None:
    """
    The one person who cooks, by name, or None — cooker._cook_name's whole
    body, moved here so there is one reader of `cooking_role` rather than
    two that can drift. Deliberately still `one_person` only: it is a
    HOUSEHOLD-level answer on the cooker view, and under `turns` there
    isn't one (the name is per night, and rides on the move).
    """
    try:
        rhythm = rhythm if rhythm is not None else _rhythm.get_household_rhythm()
        role = rhythm.get("cooking_role") or {}
    except Exception:
        return None
    if role.get("value") != "one_person":
        return None
    return (role.get("who") or "").strip() or None


# ---------- slice 2: changing whose one move is ----------

# The kinds a person can put a name on. A reheat is out for the reason the
# module docstring gives: nobody cooks it, so "Vineeth's" over it would be
# inventing a job.
OWNABLE_KINDS = ("cook", "fridge", "prep", "shop")

_NOBODY_WORDS = {"", "nobody", "no one", "noone", "nobody yet", "none", "no-one"}
_ME_WORDS = {"me", "i", "myself", "i'll", "ill", "i've", "ive"}


def _day_iso(day) -> str:
    """The household's own today when `day` is omitted — never the
    server's (moves._household_now says why that matters at 8pm)."""
    if day in (None, "", "today", "tonight"):
        return _moves._household_now().date().isoformat()
    if day == "tomorrow":
        return (_moves._household_now().date() + _timedelta(days=1)).isoformat()
    return _moves._as_date(day).isoformat()


def _title(move: dict) -> str:
    return (move.get("title") or "").strip() or "that"


def _said_line(title: str, name: str | None) -> str:
    """The pop-up's words (S10: name the thing). Plain, one clause."""
    if name:
        return f"{name}’s on {title}"
    return f"Nobody’s on {title} yet"


def set_move_owner(move_id: str, member_id: int | None = None, day: str | None = None,
                   clear: bool = False) -> dict:
    """
    Put one adult — or "Nobody yet" (member_id None) — on ONE move on ONE
    day. Not a standing rule: the next move like it still takes the
    household's default. `clear=True` takes the household's word back
    off the move entirely, so the default shows again — what Undo uses
    when there was no word before.

    Raises ValueError, in words a person can read, for a move that isn't on
    that day, a reheat, or somebody who isn't one of this household's
    adults.
    """
    on_date = _day_iso(day)
    move_id = (move_id or "").strip()
    move = next((m for m in _moves.moves_for_day(on_date) if m.get("id") == move_id), None)
    if move is None:
        raise ValueError("That isn’t on the day any more.")
    if move.get("kind") not in OWNABLE_KINDS:
        raise ValueError("Nobody cooks a reheat, so there’s nobody to put on it.")

    name = None
    if not clear and member_id is not None:
        adult = next((a for a in _shared.household_adults() if a["id"] == int(member_id)), None)
        if adult is None:
            raise ValueError("That’s not one of the adults here.")
        member_id, name = adult["id"], (adult.get("name") or "").strip()

    conn = get_conn()
    try:
        prior = conn.execute(
            "SELECT member_id FROM move_owner_overrides "
            "WHERE household_id = ? AND move_id = ? AND on_date = ?",
            (household_id(), move_id, on_date),
        ).fetchone()
        if clear:
            conn.execute(
                "DELETE FROM move_owner_overrides "
                "WHERE household_id = ? AND move_id = ? AND on_date = ?",
                (household_id(), move_id, on_date),
            )
        else:
            conn.execute(
                "INSERT INTO move_owner_overrides (household_id, move_id, on_date, member_id, set_by) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (household_id, move_id, on_date) DO UPDATE SET "
                "member_id = excluded.member_id, set_by = excluded.set_by, set_at = datetime('now')",
                (household_id(), move_id, on_date, member_id, _shared.member_id()),
            )
        conn.commit()
    finally:
        conn.close()

    # Read back rather than assume: after a clear, the default is what
    # shows, and only the real resolver knows what that is.
    after = next((m for m in _moves.moves_for_day(on_date) if m.get("id") == move_id), move)
    return {
        "status": "cleared" if clear else "set",
        "move_id": move_id,
        "date": on_date,
        "kind": move.get("kind"),
        "title": _title(move),
        "owner": after.get("owner"),
        "owner_name": after.get("owner_name"),
        # Enough for Undo to put things back EXACTLY: whether the household
        # had said anything before this, and what.
        "previous": {
            "owner": move.get("owner"),
            "owner_name": move.get("owner_name"),
            "had_override": prior is not None,
            "override_member_id": prior["member_id"] if prior is not None else None,
        },
        "said": _said_line(_title(move), after.get("owner_name")),
    }


def _find_adult(who: str) -> dict | None:
    """
    `who` as one of this household's adults: an exact name first, then a
    single adult whose name starts with it ("Vin" -> Vineeth). Two matches
    is no match — picking one would be a guess.
    """
    want = (who or "").strip().casefold()
    adults = _shared.household_adults()
    exact = [a for a in adults if (a.get("name") or "").strip().casefold() == want]
    if exact:
        return exact[0]
    first = [a for a in adults if ((a.get("name") or "").strip().casefold().split() or [""])[0] == want]
    if len(first) == 1:
        return first[0]
    starts = [a for a in adults if (a.get("name") or "").strip().casefold().startswith(want)]
    return starts[0] if len(starts) == 1 else None


def change_move_owner(who: str, what: str = "cook", day: str | None = None,
                      meal: str | None = None) -> dict:
    """
    The chat's way in: "Vineeth's cooking tonight", "I'll do the shop",
    "nobody's on Thursday's dinner yet". Finds the one move meant and hands
    it to set_move_owner — the same write the tap makes, so the two can't
    differ.

    `what` is 'cook' (the default) or 'shop'; a fridge move or prep follows
    its cook on its own. `meal` narrows a cook to breakfast / lunch /
    dinner; without it, a day's dinner is the one meant when it has one —
    "tonight" is what people say. `who` is a name, "me", or "nobody".
    """
    on_date = _day_iso(day)
    kind = (what or "cook").strip().lower()
    if kind not in ("cook", "shop"):
        raise ValueError("I can change who's cooking or who's shopping. Which did you mean?")

    words = (who or "").strip().casefold()
    if words in _NOBODY_WORDS:
        member = None
    elif words in _ME_WORDS:
        member = _shared.current_member()
        if member is None:
            names = [a["name"] for a in _shared.household_adults()]
            raise ValueError(f"Which of you is that? ({', '.join(names)})" if names
                             else "There’s nobody on record to put on it yet.")
    else:
        member = _find_adult(who)
        if member is None:
            names = [a["name"] for a in _shared.household_adults()]
            raise ValueError(
                f"I don’t have a {who.strip()} among the adults here"
                + (f" — did you mean {' or '.join(names)}?" if names else ".")
            )

    moves = [m for m in _moves.moves_for_day(on_date) if m.get("kind") == kind]
    if kind == "cook" and meal:
        moves = [m for m in moves if (m.get("slot") or "").lower() == meal.strip().lower()]
    elif kind == "cook" and len(moves) > 1:
        dinner = [m for m in moves if (m.get("slot") or "") == "dinner"]
        moves = dinner or moves
    if not moves:
        noun = "shop" if kind == "shop" else (f"{meal.strip().lower()} to cook" if meal else "cook")
        raise ValueError(f"There’s no {noun} on {on_date}.")
    if len(moves) > 1:
        raise ValueError("There’s more than one cook that day: "
                         + ", ".join(f"{m.get('slot')} ({_title(m)})" for m in moves)
                         + ". Which one?")
    return set_move_owner(moves[0]["id"], (member or {}).get("id"), on_date)
