"""
Reading back the leftover chains a generated week left behind.

`repair_leftover_chains` (weekly_plan.py) is the WRITE side: it validates
every "this night eats an earlier night's batch" claim and, for the ones
that hold up, records the pairing on the SOURCE entry's derived_from_json
as `make_double_for` (a sorted list of "YYYY-MM-DD:slot" targets) plus a
`make_double_note`. The leftover entries keep their own
`derived_from.links_to` pointing back at the source.

This module is the READ side, and nothing in it writes. It exists because
four separate places need the same answer — "is this entry a cook that
feeds other nights, a night that only reheats, or an ordinary meal?" — and
each of them was previously free to treat a leftovers night as a second,
independent cook:

- the Cook view (cooker.get_cooker_view): showed the same dish twice, once
  per night, each "for 3";
- the grocery contribution (recipes._add_recipe_ingredients_to_grocery_list
  and approve_weekly_plan's caller): bought the ingredients twice and
  scaled neither to the batch;
- defrost (defrost._candidates_from_plan): a second "move the beef to the
  fridge" for a night nothing is cooked on;
- the prep-schedule context (agent.generate_prep_schedule): prep tasks for
  a reheat night, sized to a single night's portion.

Emily's rule, 2026-09-04, seeing the same dish on two nights of the Cook
view: "It should show the one night it's being cooked as 6 servings, make
a little note that this covers tonight + leftovers, and not have it on
another night for cooking."

A chain is only honoured here when BOTH halves agree — the leftover entry
points at the source AND the source lists that leftover in
`make_double_for`. That is deliberate rather than belt-and-braces. A plan
whose chains were never validated (nothing ran repair_leftover_chains over
it) has no `make_double_for` at all and so behaves exactly as it did
before this module existed; and a source still pointing at a slot that has
since been cleared or reopened (see _finish_week_slots on exactly that
hazard) contributes nothing to the batch, because that slot no longer
points back.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from ..db import get_conn
from ._shared import household_id
from . import attendance as _attendance


def _weekday(date_str: str) -> str:
    return date.fromisoformat(date_str).strftime("%A")


def _resolve(links_to: str, by_date_slot: dict, by_id: dict):
    """
    The row a leftover entry's links_to names, or None. Both accepted
    shapes are parsed by weekly_plan's own regexes rather than a second
    copy of them here — links_to has one agreed format and one place that
    defines it. Imported at call time, not import time: weekly_plan
    reaches back into this package's other modules, and the alias
    convention (see app/tools/__init__.py) is what keeps those cycles
    resolvable.
    """
    from . import weekly_plan as _weekly_plan

    m = _weekly_plan._LINKS_TO_DATE_SLOT_RE.match(links_to)
    if m:
        return by_date_slot.get((m.group(1), m.group(2)))
    m = _weekly_plan._LINKS_TO_ENTRY_ID_RE.match(links_to)
    if m:
        return by_id.get(int(m.group(1)))
    return None


# The portions a cook makes on purpose for the freezer, on the entry's own
# derived_from — {"servings": 3, ...}. Written by tonight.tonight_night_off
# (Emily, 2026-09-22: "the job of Pomona is to do all that planning work")
# when a night off moves a batch onto the night it was feeding, or takes a
# reheat night's portion out of a chain: the batch stays the SAME SIZE (the
# groceries for it are already bought or on the list), and whatever the
# called-off night would have eaten goes in the freezer instead. Read by
# every batch reader through batch_for_source / batch_for_entry, so the Cook
# card, the fridge-move quantities and any later grocery rescale all keep
# counting those portions rather than quietly cooking less.
FREEZER_EXTRA_KEY = "freezer_extra"

# Leftovers are eaten within this many days of the cook (Emily,
# 2026-09-23, the food-safety default). A later night either starts a
# batch of its own or eats a portion frozen on the cook night. One
# constant for every place that writes or checks a chain: the fold
# (meal_variety), the repeated-dates expansion (agent._expand_repeated_
# dates), the chain repair (weekly_plan.repair_leftover_chains) and the
# prep-day batches (cook_ahead.apply_prep_day_batches).
MAX_LEFTOVER_DAYS = 3

# On a night Pomona turned into leftovers because the household asked for
# fewer recipes than meals (Emily, 2026-09-23, "Decision E": "If I want 2
# types of lunches, but need 4 lunches, you should assume Im making double
# of each of the recipes. thats the batch cooking point"). The chain itself
# is the ordinary one (links_to / make_double_for); this only says Pomona
# made it, so the draft's opener can say so in one line.
BATCH_KEY = "batch_leftovers"

# On a night that eats a portion frozen on an earlier cook: the cook's
# "entry_id:N". The night itself is a freeform "Leftovers from the
# freezer — Monday’s Chili" row, which buys nothing (freeform never
# reaches the list) and reads as a reheat on every screen (the leftovers
# regex); the cook carries the portion as FREEZER_EXTRA_KEY, so the batch
# is bought and cooked big enough.
FROM_FREEZER_KEY = "from_freezer"


_SLOT_ORDER = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}


def _eaten_order(row) -> tuple[str, int]:
    """(date, place in the day): the order meals are eaten in."""
    return (row["date"], _SLOT_ORDER.get(row["slot"], len(_SLOT_ORDER)))


def days_apart(earlier: str, later: str) -> int:
    return (date.fromisoformat(later) - date.fromisoformat(earlier)).days


def freezer_night_name(dish: str, cook_date: str) -> str:
    """"Leftovers from the freezer — Monday’s Chili"."""
    return f"Leftovers from the freezer — {_weekday(cook_date)}’s {dish}"


def frozen_portion_night_name(dish: str) -> str:
    """
    "Leftovers from the freezer — Chili", for a night eating a portion a
    NIGHT OFF froze rather than one this week's own cook put by
    (freezer_portions.apply_to_plan).

    Names no weekday, unlike freezer_night_name above, and that is the
    whole difference: that portion was frozen inside the plan being read,
    so "Monday's" means the Monday on the screen. This one may have been
    frozen three weeks and two plans ago, and "Monday's Chili" would be a
    true-sounding sentence about the wrong Monday. The word "Leftovers" is
    load-bearing either way — weekly_plan.build_slot's regex is what makes
    the night read as a reheat rather than a cook.
    """
    return f"Leftovers from the freezer — {dish}"


def frozen_portion_on(derived) -> str | None:
    """
    The dish a night is eating out of the freezer, or None — the one door
    onto FROM_FREEZER_KEY for the readers that only want to know "is this a
    reheat, and of what". Takes the parsed derived_from or its JSON.
    """
    if isinstance(derived, str) or derived is None:
        try:
            derived = json.loads(derived or "{}")
        except (TypeError, ValueError):
            return None
    value = (derived or {}).get(FROM_FREEZER_KEY)
    if not isinstance(value, dict):
        return None
    dish = (value.get("dish") or "").strip()
    return dish or None


def freezer_servings(derived) -> int:
    """How many portions of this entry's cook are meant for the freezer —
    0 for nearly every entry. Takes the parsed derived_from or its JSON."""
    if isinstance(derived, str) or derived is None:
        try:
            derived = json.loads(derived or "{}")
        except (TypeError, ValueError):
            return 0
    extra = (derived or {}).get(FREEZER_EXTRA_KEY) or {}
    try:
        return max(0, int(extra.get("servings") or 0)) if isinstance(extra, dict) else 0
    except (TypeError, ValueError):
        return 0


def plan_leftover_chains(weekly_plan_id: int, conn=None) -> dict:
    """
    Every confirmed cook-once-eat-twice chain on this plan.

    Returns:
      {
        "sources":   {source_entry_id: {"entry_id", "date", "slot", "meal",
                                        "targets": [{"entry_id","date","slot"}, ...]}},
        "leftovers": {leftover_entry_id: {"entry_id", "date", "slot",
                                          "source": {"entry_id","date","slot","meal"}}},
        "freezer":   {entry_id: servings},   # only when any; FREEZER_EXTRA_KEY
      }

    `freezer` (2026-09-22) lists every planned cook carrying portions for
    the freezer, chained or not; a SOURCE also carries its own count as
    `freezer_servings`, which batch_for_source adds to the batch. A cook
    whose only extra is the freezer is NOT a source — it feeds no night,
    and every reader of `sources` means "feeds a night" — so a batch reader
    asks batch_for_entry, which covers both.

    Targets are sorted by (date, slot) so a source that feeds two nights
    always reads in the order the nights actually fall. Both maps are
    empty for a plan with no chains, which is most plans.

    `conn` is for the grocery ingest and is not part of the assistant-facing
    API: recipes._add_recipe_ingredients_for_entries asks this whether the
    meal it is buying for is a reheat, and when that runs inside
    swap_meal_in_plan's one write transaction the plan has to be read on
    that same connection — both so the read sees the rows the transaction
    has just written and deleted, and because a nested get_conn inside an
    open write transaction is the "database is locked" trap. Given a
    connection this only reads on it and never closes it; left unset it
    behaves exactly as before.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.recipe_id, mpe.freeform_meal,
               mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.component_category IS NULL
        """,
        (weekly_plan_id, household_id()),
    ).fetchall()
    if own_conn:
        conn.close()

    by_date_slot = {(r["date"], r["slot"]): r for r in rows}
    by_id = {r["id"]: r for r in rows}

    sources: dict[int, dict] = {}
    leftovers: dict[int, dict] = {}
    freezer: dict[int, int] = {}
    for r in rows:
        if r["slot_state"] != "planned":
            continue
        derived = json.loads(r["derived_from_json"] or "{}")
        extra = freezer_servings(derived)
        if extra and (r["recipe_id"] or r["freeform_meal"]):
            freezer[r["id"]] = extra
        links_to = (derived.get("links_to") or "").strip()
        if not links_to:
            continue
        source = _resolve(links_to, by_date_slot, by_id)
        if source is None or source["id"] == r["id"]:
            continue
        if source["slot_state"] != "planned" or not (source["recipe_id"] or source["freeform_meal"]):
            continue
        if _eaten_order(source) >= _eaten_order(r):
            # In EATING order (Emily, 2026-09-27): a lunch cooked big can
            # feed that evening's dinner; nothing feeds a meal eaten first.
            continue
        # The source has to name this night back. Without the agreement
        # check a half-written chain would still scale a batch up — see
        # the module docstring.
        source_derived = json.loads(source["derived_from_json"] or "{}")
        confirmed = source_derived.get("make_double_for") or []
        if isinstance(confirmed, str):  # tolerate the pre-fix scalar shape
            confirmed = [confirmed]
        if f"{r['date']}:{r['slot']}" not in confirmed:
            continue

        # Was this pairing the household's own "cook these days now" pick
        # (cook_ahead.set_cook_ahead) rather than the planner's leftovers?
        # The chain itself is identical either way — this only decides
        # which words the screens use for it, so it is read here rather
        # than making every caller open derived_from a second time.
        chosen_ahead = bool(derived.get("cook_ahead"))

        entry = sources.setdefault(source["id"], {
            "entry_id": source["id"], "date": source["date"], "slot": source["slot"],
            "meal": source["meal"], "note": source_derived.get("make_double_note") or "",
            "cook_ahead": False, "targets": [],
        })
        # Only when there is any: the shape every other plan has always
        # had is left exactly as it was (callers read it with .get).
        if freezer_servings(source_derived):
            entry["freezer_servings"] = freezer_servings(source_derived)
        entry["cook_ahead"] = entry["cook_ahead"] or chosen_ahead
        entry["targets"].append({
            "entry_id": r["id"], "date": r["date"], "slot": r["slot"], "cook_ahead": chosen_ahead,
        })
        leftovers[r["id"]] = {
            "entry_id": r["id"], "date": r["date"], "slot": r["slot"],
            "cook_ahead": chosen_ahead,
            "source": {
                "entry_id": source["id"], "date": source["date"],
                "slot": source["slot"], "meal": source["meal"],
            },
        }

    for entry in sources.values():
        entry["targets"].sort(key=lambda t: (t["date"], t["slot"]))
    out = {"sources": sources, "leftovers": leftovers}
    if freezer:
        # Present only when some cook carries portions for the freezer —
        # the two-key shape is what every plan without one has always had.
        out["freezer"] = freezer
    return out


def eaters_at(date_str: str, slot: str, conn=None) -> int:
    """
    How many people this one meal actually feeds — attendance's headcount
    (members present plus guests). get_slot_attendance already falls back
    to "everyone's home" when no attendance was ever recorded for the
    slot, so an ordinary week answers with the household's own size. 0
    only for a household with no members yet, or a slot everyone is away
    for; callers treat 0 as "don't scale" rather than "cook nothing".
    """
    try:
        return int(_attendance.get_slot_attendance(date_str, slot, conn=conn)["headcount"] or 0)
    except Exception:
        return 0


def batch_for_source(source: dict, conn=None) -> dict:
    """
    What one source night actually has to cook: its own table plus every
    night eating its leftovers.

    Returns {"servings", "cook_eaters", "targets": [{date, slot, eaters}]}.
    `servings` is 0 when nothing could be counted (no members on record) —
    the signal to leave the recipe's own quantities alone rather than
    scale to nothing.

    `conn` rides through to the attendance reads for the grocery ingest —
    see plan_leftover_chains.
    """
    cook_eaters = eaters_at(source["date"], source["slot"], conn=conn)
    targets = [
        {**t, "eaters": eaters_at(t["date"], t["slot"], conn=conn)}
        for t in source["targets"]
    ]
    # Portions for the freezer (FREEZER_EXTRA_KEY) are part of the batch:
    # they were bought for, and the cook makes them — they just aren't
    # eaten on a night of this plan.
    extra = int(source.get("freezer_servings") or 0)
    total = cook_eaters + sum(t["eaters"] for t in targets) + extra
    out = {"servings": total, "cook_eaters": cook_eaters, "targets": targets}
    if extra:
        out["freezer"] = extra
    return out


def batch_for_entry(entry_id: int, chains: dict, conn=None) -> dict | None:
    """
    The batch one cook has to make, or None when it is an ordinary cook for
    its own table (or a reheat, which cooks nothing).

    A chain SOURCE answers batch_for_source. A cook that feeds no night but
    carries portions for the freezer (chains["freezer"], 2026-09-22) is a
    batch too — its own table plus the freezer's share — and this is the
    one place that says so, so the Cook card, the fridge-move quantities
    and the grocery ingest cannot disagree about how much it makes.
    """
    source = (chains.get("sources") or {}).get(entry_id)
    if source:
        return batch_for_source(source, conn=conn)
    extra = int((chains.get("freezer") or {}).get(entry_id) or 0)
    if not extra:
        return None
    own_conn = conn is None
    c = get_conn() if own_conn else conn
    try:
        row = c.execute(
            "SELECT date, slot FROM meal_plan_entries WHERE id = ? AND household_id = ?",
            (entry_id, household_id()),
        ).fetchone()
    finally:
        if own_conn:
            c.close()
    if row is None:
        return None
    cook_eaters = eaters_at(row["date"], row["slot"], conn=conn)
    return {
        "servings": cook_eaters + extra, "cook_eaters": cook_eaters,
        "targets": [], "freezer": extra,
    }


def _join_days(days: list[str]) -> str:
    if len(days) == 1:
        return days[0]
    if len(days) == 2:
        return f"{days[0]} and {days[1]}"
    return ", ".join(days[:-1]) + f", and {days[-1]}"


# ---------- "Double batch: 4 tonight, 4 for lunch tomorrow" ----------
# Gowthami's household, 2026-10-04: "It needs to call out that it's double
# the quantity because its calling for leftovers." Measured on a throwaway
# DB first (2026-10-05): a Day-0 dinner chained to Day-1's lunch for a
# household of three comes back from get_cooker_view with servings 6 and
# "1.5 lbs" of beef, under a "Cooking for 6" stepper, and the ONE sentence
# that said why — covers_note — is rendered on no client surface at all
# (grep: shell.js reads it twice, both times as the boolean `was_batch`).
# So the cook saw doubled amounts with nothing explaining them.
#
# THREE SURFACES, ONE READ. The recipe screen's line, Cook's Tonight card
# and cook mode's first and last steps all come out of _batch_parts below,
# so they cannot disagree about a number, a day or a meal. Two copies of
# one sentence is this codebase's named recurring bug generator.
#
# "ABOUT 2x" IS A BAND, AND THE CARD DOES NOT GIVE ONE. These two
# constants are it. A household of three cooking six is 2.00x and reads as
# double; cooking seven (3 at the table, 4 packed) is 2.33x and still
# reads as double to anybody holding the pot. Three times the table does
# not — that is a big batch, and saying "double" about it would be the app
# saying a thing that isn't true (DESIGN_SYSTEM §8). Under 1.75x the word
# overstates it in the other direction: half again is not double.
# To reverse this, change these two numbers — nothing else reads them.
DOUBLE_BATCH_RATIO_MIN = 1.75
DOUBLE_BATCH_RATIO_MAX = 2.5


def _slot_word(slot: str) -> str:
    """"lunch" / "dinner" — the meal as a person says it, lower case,
    because it always lands mid-sentence here."""
    return (slot or "dinner").strip().lower() or "dinner"


def _household_today_iso() -> str:
    """
    Today where the HOUSEHOLD lives, as an ISO date — the default for every
    "tonight"/"today" decision in this module. The server's date.today() is
    already tomorrow from 8pm Toronto (container is UTC), which made
    tonight's card say "covers Monday" and tomorrow's say "tonight".
    Imported at call time: cooker imports this module.
    """
    from .cooker import household_today
    return household_today().isoformat()


def _when_phrase(cook_date: str, target_date: str, slot: str, today: str) -> str:
    """
    When a leftover gets eaten, said the way a person would say it
    (DESIGN_SYSTEM §8): "lunch tomorrow" for the next day, "Tuesday's
    lunch" otherwise, and "lunch later today" for a lunch cooked big that
    feeds that same evening — which _eaten_order allows and which
    "tomorrow" would be plainly wrong about.

    Relative to the COOK night, not to today: the sentence is about this
    batch, and "tomorrow" means the day after it is cooked wherever the
    screen is read from. The one exception is the cook night being today,
    where "tomorrow" is both.
    """
    meal = _slot_word(slot)
    if target_date == cook_date:
        return f"{meal} later today" if cook_date == today else f"{meal} the same day"
    if days_apart(cook_date, target_date) == 1:
        return f"{meal} tomorrow"
    return f"{_weekday(target_date)}’s {meal}"


def _batch_parts(source: dict, batch: dict, today: str | None = None) -> dict | None:
    """
    The one read every batch sentence is built from, or None when there is
    nothing to say.

    `source` is a chains["sources"] entry (or the freezer-only shape
    cooker.py builds for batch_for_entry); `batch` is what
    batch_for_source / batch_for_entry answered. Returns:

      {"word": "Double batch" | "Big batch",
       "cook_servings": 3, "cook_label": "tonight" | "Thursday",
       "targets": [{"servings": 3, "when": "lunch tomorrow"}, ...],
       "freezer": 0, "servings": 6}

    None for anything that is not a batch: no servings countable (a
    household with nobody on record), nothing beyond this night's own
    table, or a cook whose own table could not be counted — the same
    silence covers_note's own `servings <= 0` fallback gives, and the same
    silence an unhonoured chain gets for free, because
    plan_leftover_chains never hands one to a caller.
    """
    today = today or _household_today_iso()
    servings = int(batch.get("servings") or 0)
    cook = int(batch.get("cook_eaters") or 0)
    extra = int(batch.get("freezer") or 0)
    targets = batch.get("targets") or []
    if servings <= 0 or cook <= 0:
        return None
    if not targets and not extra:
        return None
    ratio = servings / cook
    word = ("Double batch"
            if DOUBLE_BATCH_RATIO_MIN <= ratio <= DOUBLE_BATCH_RATIO_MAX
            else "Big batch")
    cook_date = source["date"]
    return {
        "word": word,
        "cook_servings": cook,
        "cook_label": "tonight" if cook_date == today else _weekday(cook_date),
        "targets": [
            {
                "servings": int(t.get("eaters") or 0),
                "when": _when_phrase(cook_date, t["date"], t["slot"], today),
            }
            for t in targets
        ],
        "freezer": extra,
        "servings": servings,
    }


def _servings_words(n: int) -> str:
    """"1 serving" / "4 servings" — agreement, because these land in a
    sentence a person reads at the stove."""
    return f"{n} serving" if n == 1 else f"{n} servings"


def _join_tally(clauses: list[str]) -> str:
    """
    "3 tonight, 3 for lunch tomorrow" — a TALLY, so two parts take a comma
    rather than _join_days' "and". That is the card's own locked copy
    ("Double batch: 4 tonight, 4 for Tuesday\'s lunch") and it is the
    right reading: these are the shares of one batch being counted off,
    not two things being listed in a sentence. Three or more keep the
    final "and", which is where a bare comma would start to read as a
    sentence that had lost a word.
    """
    if len(clauses) < 3:
        return ", ".join(clauses)
    return ", ".join(clauses[:-1]) + f", and {clauses[-1]}"


def batch_line(source: dict, batch: dict, today: str | None = None) -> str:
    """
    "Double batch: 3 tonight, 3 for lunch tomorrow." — the line directly
    under the "Cooking for" stepper on the recipe, and on Cook's Tonight
    card (Emily's card, 2026-10-05).

    The numbers are the CHAIN's, never the stepper's: this night's table
    and each leftover night's, from attendance, plus any portions put by
    for the freezer. A cook who taps the stepper is overriding how much to
    make; what the week is FOR does not change with it.

    '' when there is nothing to say — see _batch_parts.
    """
    parts = _batch_parts(source, batch, today)
    if not parts:
        return ""
    # "3 tonight" reads as one clause; "3 Wednesday" does not, so a cook
    # night that is not today takes the same "for" the leftovers do. Found
    # by a test, not by reading it back.
    first = (f"{parts['cook_servings']} tonight" if parts["cook_label"] == "tonight"
             else f"{parts['cook_servings']} for {parts['cook_label']}")
    clauses = [first] + [
        f"{t['servings']} for {t['when']}" for t in parts["targets"]
    ]
    said = _join_tally(clauses)
    frozen = f", plus {parts['freezer']} for the freezer" if parts["freezer"] else ""
    return f"{parts['word']}: {said}{frozen}."


def _packing_phrase(parts: dict) -> str:
    """
    What the extra is for, as one phrase: "for lunch tomorrow", "for
    Tuesday's lunch and the freezer", "for the freezer".
    """
    wheres = [f"{t['when']}" for t in parts["targets"]]
    if parts["freezer"]:
        wheres.append("the freezer")
    return "for " + _join_days(wheres) if wheres else ""


def batch_first_step_note(source: dict, batch: dict, today: str | None = None) -> str:
    """
    "Double batch: half goes in containers for lunch tomorrow." — cook
    mode's first step, so the cook meets the fact at the pot rather than
    only on the screen before it (Emily's card, 2026-10-05).

    "half" ONLY when it really is half — one leftover night, no freezer
    portion, and the two shares equal. Anything else names the servings,
    because "half" about a third of a three-night batch is a sentence
    somebody would act on and get wrong.
    """
    parts = _batch_parts(source, batch, today)
    if not parts:
        return ""
    where = _packing_phrase(parts)
    if not where:
        return ""
    share = sum(t["servings"] for t in parts["targets"]) + parts["freezer"]
    is_half = (
        len(parts["targets"]) == 1
        and not parts["freezer"]
        and parts["targets"][0]["servings"] == parts["cook_servings"]
    )
    if is_half:
        goes = "half goes"
    else:
        goes = _servings_words(share) + (" goes" if share == 1 else " go")
    return f"{parts['word']}: {goes} in containers {where}."


def batch_last_step_note(source: dict, batch: dict, today: str | None = None) -> str:
    """
    "Pack 3 servings for lunch tomorrow." — cook mode's last step, which
    is where the packing actually happens.
    """
    parts = _batch_parts(source, batch, today)
    if not parts:
        return ""
    where = _packing_phrase(parts)
    if not where:
        return ""
    share = sum(t["servings"] for t in parts["targets"]) + parts["freezer"]
    if share <= 0:
        return ""
    return f"Pack {_servings_words(share)} {where}."


def covers_note(source: dict, servings: int, today: str | None = None) -> str:
    """
    The little note that goes under the "for 6" chip on the one night this
    is cooked: "Cooking for 6 — covers tonight and leftovers on Thursday."

    Names the cook night as "tonight" only when it really is today, and as
    the weekday otherwise, so the same card is honest read on Sunday and
    read on the night itself (DESIGN_SYSTEM.md §8: time the way a person
    would say it). The leftover nights are always named — "and leftovers"
    on its own would leave the person counting.
    """
    today = today or _household_today_iso()
    cook_label = "tonight" if source["date"] == today else _weekday(source["date"])
    # Portions for the freezer are said, not hidden inside the number: a
    # cook told "for 6" at a table of 3 with no reason given halves it.
    extra = int(source.get("freezer_servings") or 0)
    frozen = f", plus {extra} for the freezer" if extra else ""
    if not source.get("targets"):
        # A cook whose only extra is the freezer (batch_for_entry).
        return f"Cooking for {servings} — covers {cook_label}{frozen}."
    days = _join_days([_weekday(t["date"]) for t in source["targets"]])
    return f"Cooking for {servings} — covers {cook_label} and leftovers on {days}{frozen}."


def cook_ahead_note(source: dict, servings: int) -> str:
    """
    covers_note's wording for a batch the household chose to cook ahead
    (cook_ahead.set_cook_ahead): "Cooking for 6 — enough for Monday,
    Wednesday, and Friday."

    Same sentence, different truth. covers_note says "covers tonight and
    leftovers on Thursday" because that is what a planner-written chain
    is: one dinner, then what is left of it. A household that ticked three
    mornings is not making leftovers, it is making three mornings' worth
    at once — so this names every day the batch is for, the cook day
    included, and never says "leftovers" about portions nobody has eaten
    yet.
    """
    days = _join_days([_weekday(source["date"])] + [_weekday(t["date"]) for t in source["targets"]])
    extra = int(source.get("freezer_servings") or 0)
    frozen = f", plus {extra} for the freezer" if extra else ""
    return f"Cooking for {servings} — enough for {days}{frozen}."


def leftovers_headline(source_meal: str, source_date: str) -> str:
    """"Leftovers — Wednesday's Korean Beef Bulgogi Lettuce Wraps"."""
    return f"Leftovers — {_weekday(source_date)}’s {source_meal}"


def made_ahead_headline(source_meal: str, source_date: str) -> str:
    """"Made ahead — Monday's Egg White Bites" — leftovers_headline for a
    day the household deliberately cooked the portions for in advance.
    Same shape, one honest word different: a portion set aside on purpose
    on Monday morning isn't leftovers by Wednesday, it's what Wednesday
    was always going to be."""
    return f"Made ahead — {_weekday(source_date)}’s {source_meal}"


def served_cold(recipe: dict | None, slot: str | None) -> bool:
    """
    Is a made-ahead portion eaten as it comes out of the fridge, with no
    reheating? (Emily, 2026-09-25, on "Made ahead — Wednesday's Cucumber
    Slices with Tzatziki" wearing a Reheat pill: "The snacks don't need to
    be reheated they're cold. Just cause they're made ahead doesn't mean
    they need to be reheated.")

    Recipes carry no serve-temperature field, so this reads the best
    signals already on the row, conservatively — a dish only counts as cold
    on positive evidence, and anything unclear keeps "Reheat":

      - The recipe's own notes talk about reheating (reheat_note) -> hot.
        That is the household's or the recipe's own word, and it wins.
      - A snack -> cold. A made-ahead snack is grabbed from the fridge;
        even a baked one (a muffin, an oat bar) is eaten as it is.
      - A method that exists and never applies heat -> cold. The same
        heat vocabulary plan_quality uses to tell a cold plate from a
        cooked one (_APPLIES_HEAT_WORDS): no oven, pan, pot, boil, roast...
      - cook_time_minutes recorded as exactly 0 -> cold (a no-cook
        recipe). None means "not known", not zero.
    """
    if reheat_note(recipe):
        return False
    if (slot or "").strip().lower() == "snack":
        return True
    if not recipe:
        return False
    steps = [s for s in (recipe.get("instructions") or []) if str(s).strip()]
    if steps:
        # Imported here, not at module top: plan_quality imports half the
        # tools package, and leftovers is imported early by most of it.
        from .plan_quality import _APPLIES_HEAT_WORDS
        words = set(re.findall(r"[a-zé]+", " ".join(str(s) for s in steps).lower()))
        if not (words & _APPLIES_HEAT_WORDS):
            return True
    return recipe.get("cook_time_minutes") == 0


# ---------- does a dish keep as a leftover? ----------
#
# A cook feeding tomorrow's lunch is one pot, so a swap of it puts the new
# dish on both meals (swap_in_place.fed_days, 2026-10-04). A dish that is
# only itself fresh — a salad, a plate of nachos — must not be the thing
# in tomorrow's container, so the fed meal becomes a question Pomona
# answers instead.
#
# The generation prompt has asked for "something that keeps and reheats
# well" on a batch since prepped lunches shipped (agent.py), and there has
# never been a predicate behind it. This is the predicate, and it is a
# WORD LIST on purpose — the call plates.is_low_carb already made, for the
# same reasons: it runs per swap, the cost of a wrong answer is one meal,
# and a list anyone can read and argue with beats a judgement nobody can
# see. Extend it when a real miss shows up.
#
# POSITIVE EVIDENCE ONLY, and the bias is deliberate: an unrecognised dish
# KEEPS. A wrong "doesn't keep" takes a leftover lunch away from a
# household that wanted one and hands them a repeat; a wrong "keeps" is one
# soggy lunch. Neither is good, and the first is the one that argues with
# a household about their own week — so when in doubt a word stays out.
#
# `_DOES_NOT_KEEP` is matched against the dish's NAME only, whole word or
# whole phrase. Not the ingredients: "lettuce" is in a wrap that keeps and
# in a salad that doesn't, so the ingredient says nothing the name hasn't
# already said.
_DOES_NOT_KEEP = (
    "salad", "slaw", "ceviche", "sashimi", "sushi", "tartare", "tempura",
    "souffle", "soufflé", "smoothie", "omelette", "omelet", "nachos",
    "bruschetta", "grilled cheese", "quesadilla", "fried egg", "poached egg",
    "scrambled eggs", "avocado toast", "french toast", "caesar",
)
# ...except where the word is standing there and is not the thing — the
# same shape coordination._COMPOUND_EXCEPTIONS takes for an allergen word
# inside a compound food. A pasta salad, a potato salad and a tuna salad
# are all made ahead on purpose.
_KEEPS_ANYWAY = (
    "pasta salad", "potato salad", "bean salad", "grain salad", "lentil salad",
    "chickpea salad", "quinoa salad", "rice salad", "couscous salad", "farro salad",
    "barley salad", "egg salad", "tuna salad", "chicken salad", "salmon salad",
    "noodle salad", "three bean salad",
)


def keeps_as_leftovers(pick: dict | None) -> bool:
    """
    Whether a dish may be the thing in tomorrow's container.

    `pick` is a swap's chosen dish (swap_in_place), so two signals are
    read, in this order:

      * `keeps_as_leftovers` false, when the pick says so — the model has
        volunteered a problem with its own dish and there is no reason to
        argue with it. A pick that says nothing is not a pick that said
        yes;
      * the dish's NAME against _DOES_NOT_KEEP above, so "telling the
        generator something is not the same as preventing it" (CLAUDE.md's
        own standing rule) holds here too.

    True for everything else, including every dish with no name at all —
    see the bias note above.
    """
    if (pick or {}).get("keeps_as_leftovers") is False:
        return False
    name = ((pick or {}).get("meal_name") or "").strip().lower()
    if not name:
        return True
    if any(exception in name for exception in _KEEPS_ANYWAY):
        return True
    for word in _DOES_NOT_KEEP:
        if " " in word:
            if word in name:
                return False
        elif re.search(r"\b" + re.escape(word) + r"\b", name):
            return False
    return True


def reheat_note(recipe: dict | None) -> str:
    """
    A recipe's own reheating advice, if it happens to carry any.

    There is no reheat field on recipes (see schema.sql) and adding one is
    a bigger change than this needs, so this reads the freeform `notes`
    and returns the first sentence that is actually about reheating.
    Returns "" for the overwhelming majority of recipes, and the reheat
    card simply shows the source link instead — which is the honest
    outcome, not a degraded one.
    """
    notes = ((recipe or {}).get("notes") or "").strip()
    if not notes:
        return ""
    for sentence in [s.strip() for s in notes.replace("\n", ". ").split(".") if s.strip()]:
        low = sentence.lower()
        if "reheat" in low or "warm through" in low or "warms up" in low or "warm up" in low:
            return sentence if sentence.endswith(("!", "?")) else sentence + "."
    return ""


# ---------- no dish on more than two meals in a row ----------
#
# Emily, 2026-09-27, decision B: counting lunches and dinners in the order
# they are eaten (date, then breakfast → lunch → dinner), no dish is on
# more than MAX_MEALS_IN_A_ROW of them in a row. Thursday's dinner and
# Friday's lunch off it is fine; Friday's dinner is then something else —
# a quick one when Friday is short on time.
#
# ONE rule, read here, because several places can make a run: the weekday
# lunch from the dinner before (weekday_lunches), the fold's batches
# (meal_variety._plan_batches), the model's own chains
# (weekly_plan.repair_leftover_chains), the Leftovers-night pass and the
# re-pick of a gap (dinner_gaps). Each asks this module before it writes;
# dinner_gaps.break_long_runs makes it true of whatever still slipped
# through, and plan_quality._no_long_runs is the tripwire.
#
# What breaks a run: a lunch or dinner with a DIFFERENT dish, and a meal
# with no dish on it at all (nobody home, a day left out, a question).
# Breakfast is not counted — the rule is about lunches and dinners.
#
# Nothing here writes, like the rest of this module.

MAX_MEALS_IN_A_ROW = 2
RUN_SLOTS = ("lunch", "dinner")

_LEADING_REHEAT = re.compile(
    r"^(?:leftovers?\s+from\s+the\s+freezer\s*[—–-]\s*(?:\w+[’']s\s+)?"
    r"|leftovers?\s*[—–:-]\s*(?:\w+[’']s\s+)?|leftovers?\s+(?:of\s+)?|reheated\s+)",
    re.IGNORECASE,
)
_TRAILING_REHEAT = re.compile(r"\s*\(?\b(?:leftovers?|reheated)\b\)?\s*$", re.IGNORECASE)


def dish_identity(name: str | None) -> str:
    """The dish a meal IS, for the run rule: "Chili", "Leftover chili",
    "Chili leftovers" and "Leftovers from the freezer — Monday’s Chili"
    are all "chili"."""
    text = (name or "").strip()
    text = _LEADING_REHEAT.sub("", text)
    text = _TRAILING_REHEAT.sub("", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _position(date_str: str, slot: str) -> tuple[str, int]:
    return (date_str, RUN_SLOTS.index(slot))


def _before(pos: tuple[str, int]) -> tuple[str, int]:
    d, i = pos
    if i == 1:
        return (d, 0)
    return ((date.fromisoformat(d) - timedelta(days=1)).isoformat(), 1)


def _after(pos: tuple[str, int]) -> tuple[str, int]:
    d, i = pos
    if i == 0:
        return (d, 1)
    return ((date.fromisoformat(d) + timedelta(days=1)).isoformat(), 0)


def run_keys(weekly_plan_id: int, conn=None) -> dict[tuple[str, str], str]:
    """
    {(date, slot): the dish it is} for every planned lunch and dinner of
    the plan. A reheat is the dish it reheats — its links_to (confirmed or
    not: this is read mid-generation too) or the dish on its freezer
    portion — so "Leftovers — Monday’s Chili" and the Chili are one dish.
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.derived_from_json,
                   COALESCE(r.name, mpe.freeform_meal) AS meal
            FROM meal_plan_entries mpe
            LEFT JOIN recipes r ON r.id = mpe.recipe_id
            WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.component_category IS NULL
              AND mpe.slot IN ('lunch', 'dinner')
            ORDER BY mpe.id ASC
            """,
            (weekly_plan_id, household_id()),
        ).fetchall()
    finally:
        if own:
            conn.close()
    return run_keys_from_rows(rows)


def run_keys_from_rows(rows) -> dict[tuple[str, str], str]:
    """run_keys over rows already in hand — {id, date, slot, slot_state,
    meal} plus either `derived` (a dict) or `derived_from_json` — so a
    writer can ask the rule of a week it has only rearranged in memory
    (meal_move, 2026-09-28). Rows of other slots are ignored."""
    by_date_slot: dict = {}
    by_id: dict = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        if r["slot"] not in RUN_SLOTS:
            continue
        by_date_slot.setdefault((r["date"], r["slot"]), r)
        by_id[r["id"]] = r
    keys: dict[tuple[str, str], str] = {}
    for (d, slot), r in by_date_slot.items():
        if r["slot_state"] != "planned" or not (r["meal"] or "").strip():
            continue
        derived = r.get("derived") if isinstance(r, dict) else None
        if derived is None:
            try:
                derived = json.loads(r["derived_from_json"] or "{}") or {}
            except (TypeError, ValueError):
                derived = {}
        name = frozen_portion_on(derived) or r["meal"]
        links_to = str(derived.get("links_to") or "").strip()
        if links_to:
            source = _resolve(links_to, by_date_slot, by_id)
            if source is not None and (source["meal"] or "").strip():
                name = source["meal"]
        keys[(d, slot)] = dish_identity(name)
    return keys


def _key_at(keys: dict, pos: tuple[str, int]) -> str | None:
    return keys.get((pos[0], RUN_SLOTS[pos[1]]))


def run_before(keys: dict, date_str: str, slot: str, dish: str | None = None) -> int:
    """How many lunches and dinners in a row this meal would END, holding
    `dish` (default: what it holds now) — counting backwards only. What a
    writer asks when the meals after this one are not its to change. 0
    for a breakfast or snack, which the rule does not count."""
    if slot not in RUN_SLOTS:
        return 0
    key = dish_identity(dish) if dish is not None else keys.get((date_str, slot))
    if not key:
        return 0
    n = 1
    pos = _before(_position(date_str, slot))
    while _key_at(keys, pos) == key:
        n += 1
        pos = _before(pos)
    return n


def run_through(keys: dict, date_str: str, slot: str, dish: str | None = None) -> int:
    """How many lunches and dinners in a row this meal would be part of,
    holding `dish` (default: what it holds now) — both directions."""
    back = run_before(keys, date_str, slot, dish)
    if not back:
        return 0
    key = dish_identity(dish) if dish is not None else keys.get((date_str, slot))
    n = back
    pos = _after(_position(date_str, slot))
    while _key_at(keys, pos) == key:
        n += 1
        pos = _after(pos)
    return n


def too_many_in_a_row(keys: dict, date_str: str, slot: str, dish: str | None = None) -> bool:
    """Whether `dish` at this meal would put it on more than
    MAX_MEALS_IN_A_ROW lunches and dinners in a row. A different lunch
    between two dinners breaks the run (decision B as Emily stated it;
    whether three dinners of one dish with other lunches between should
    also count is her open question, 2026-09-27)."""
    return run_through(keys, date_str, slot, dish) > MAX_MEALS_IN_A_ROW


def ends_too_long_a_run(keys: dict, date_str: str, slot: str, dish: str | None = None) -> bool:
    """too_many_in_a_row counting BACKWARDS only: whether this meal would be
    the third (or later) of one dish — what a writer asks when the meals
    after this one are not its to change."""
    return run_before(keys, date_str, slot, dish) > MAX_MEALS_IN_A_ROW


def long_runs(keys: dict) -> list[list[tuple[str, str]]]:
    """Every run longer than the rule allows, each as its meals in eating
    order — [(date, slot), ...]. Empty for a week that keeps the rule."""
    runs: list[list[tuple[str, str]]] = []
    seen: set = set()
    for (d, slot) in sorted(keys, key=lambda k: _position(*k)):
        if (d, slot) in seen:
            continue
        key = keys[(d, slot)]
        run = [(d, slot)]
        pos = _after(_position(d, slot))
        while _key_at(keys, pos) == key:
            run.append((pos[0], RUN_SLOTS[pos[1]]))
            pos = _after(pos)
        seen.update(run)
        if len(run) > MAX_MEALS_IN_A_ROW:
            runs.append(run)
    return runs
