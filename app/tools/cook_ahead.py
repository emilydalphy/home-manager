"""
Cooking one batch for several days of the same dish.

Emily, 2026-09-07, looking at a plan with Egg White Bites on every
morning: "We don't want to make egg bites every morning. Add a thing
asking how many days do we want to cook it now, and the user can mark off
the days of the week it's on the plan that we should cook the portions
for now."

A day-based plan writes the same breakfast on five mornings as five
separate meal_plan_entries, and the Cook screen showed five separate
cooks. The machinery for "one cook feeds several days" already existed —
the leftover chains (leftovers.py, weekly_plan.repair_leftover_chains) —
but only the planner could create one. This module is the household's own
way in: the days a card COULD cook ahead for (cook_ahead_options /
attach_cook_ahead) and the write that records what they picked
(set_cook_ahead).

It writes exactly the shape repair_leftover_chains writes, because a
chain nothing else recognises is a chain that quietly stops working: the
source's derived_from.make_double_for is a sorted list of
"YYYY-MM-DD:slot" targets, each covered entry's derived_from.links_to
names the source, and leftovers.plan_leftover_chains honours a pairing
only when both halves agree. The one addition is a `cook_ahead: true`
flag on the covered entry. It changes nothing about how the chain works
and only changes what the screens SAY about it: "Made ahead — Monday's
Egg White Bites" is what a batch of breakfasts cooked on purpose actually
is, where "Leftovers" reads like the end of something.

Groceries are deliberately untouched. meal_plan_grocery_links is per
entry and the household still eats the same number of portions this week
— consolidating the COOKS doesn't change what has to be bought — so
nothing here rescales a grocery line. That is the difference between this
and weekly_plan._unlink_leftover_target, which does rescale: a night
being swapped away really is a portion nobody eats, and a day being cooked
ahead for really is one they still do.
"""
from __future__ import annotations

import json
from datetime import date

from ..db import get_conn
from ._shared import household_id
from . import leftovers as _leftovers

# What a covered day's derived_from carries beyond links_to, so the
# screens can tell a chosen batch from a planner-written leftovers night.
COOK_AHEAD_FLAG = "cook_ahead"


def _weekday(date_str: str) -> str:
    return date.fromisoformat(date_str).strftime("%A")


def _derived(row) -> dict:
    return json.loads(row["derived_from_json"] or "{}")


def _plan_rows(weekly_plan_id: int) -> list:
    """
    Every day-based entry on one plan, with the dish's name resolved the
    same way the Cook view resolves it (recipe name, or the freeform meal
    when there is no saved recipe).
    """
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
    conn.close()
    return rows


def _is_cookable(row) -> bool:
    return (
        row["slot_state"] == "planned"
        and bool(row["recipe_id"] or row["freeform_meal"])
        and bool((row["meal"] or "").strip())
    )


def _same_dish(a, b) -> bool:
    return (a["meal"] or "").strip().lower() == (b["meal"] or "").strip().lower()


def cook_ahead_options(weekly_plan_id: int) -> dict[int, list[dict]]:
    """
    For every cookable entry on this plan, the later days it could cook
    ahead for: {source_entry_id: [{entry_id, date, slot, eaters,
    selected}, ...]}.

    A day is offered when it is the SAME dish in the SAME slot on a LATER
    date — the shape a repeat actually takes in a day-based plan, and the
    only shape where cooking the portions now is a real answer. Sorted by
    date, so the chips read in the order the week falls.

    Three kinds of day are left out, each for its own reason:
    - a day already covered by a DIFFERENT source, which is somebody
      else's batch and not this card's to claim;
    - a day that is itself a source, because a chain of chains is exactly
      the tangle repair_leftover_chains exists to refuse;
    - the card's own day.

    A day covered by THIS source is kept and comes back selected=True —
    that is what lets the picker show the current choice ticked, and
    un-ticking it release the day. A reheat night gets no options at all
    (it is not a cook, so it has nothing to cook ahead).
    """
    rows = [r for r in _plan_rows(weekly_plan_id) if _is_cookable(r)]
    chains = _leftovers.plan_leftover_chains(weekly_plan_id)
    covered = chains["leftovers"]
    source_ids = set(chains["sources"])

    options: dict[int, list[dict]] = {}
    for row in rows:
        if row["id"] in covered:
            continue  # a reheat night is not a cook
        days = []
        for other in rows:
            if other["id"] == row["id"] or other["slot"] != row["slot"]:
                continue
            if other["date"] <= row["date"] or not _same_dish(other, row):
                continue
            if other["id"] in source_ids:
                continue
            covering = covered.get(other["id"])
            if covering and covering["source"]["entry_id"] != row["id"]:
                continue
            days.append({
                "entry_id": other["id"],
                "date": other["date"],
                "slot": other["slot"],
                "eaters": _leftovers.eaters_at(other["date"], other["slot"]),
                "selected": covering is not None,
            })
        days.sort(key=lambda d: d["date"])
        options[row["id"]] = days
    return options


def cook_ahead_repeats(weekly_plan_id: int) -> list[dict]:
    """
    The same question the Cook card's chips ask, gathered for the whole
    week: every dish this plan repeats in the same slot on two or more
    days with no chain on it yet.

    Emily, 2026-09-08: ask at approval too, not only on the Cook card — the
    moment a week is approved is when a household is actually looking at
    the shape of it, and "Egg White Bites is on 5 mornings" is easier to
    answer then than five cards later.

    One entry per repeated dish:
    {dish, slot, first: {entry_id, date, eaters}, later: [{entry_id, date,
    eaters}], eaters_total}. `first` is the earliest day — the one that
    would do the cooking — and `later` is exactly what cook_ahead_options
    offers that day, so the two surfaces can never disagree about which
    days are claimable or why.

    A dish with ANY chain already on it (a cook-ahead the household
    already made, a leftovers night the planner wrote) is left out
    entirely: that batch has an owner, and the Cook card's own picker is
    where it gets changed. So is a repeat whose first day is a reheat, and
    a dish that appears once — neither has a "cook it all now" to offer.
    """
    rows = [r for r in _plan_rows(weekly_plan_id) if _is_cookable(r)]
    options = cook_ahead_options(weekly_plan_id)
    chains = _leftovers.plan_leftover_chains(weekly_plan_id)
    chained = set(chains["leftovers"]) | set(chains["sources"])

    groups: dict[tuple[str, str], list] = {}
    for row in rows:
        groups.setdefault(((row["meal"] or "").strip().lower(), row["slot"]), []).append(row)

    items: list[dict] = []
    for members in groups.values():
        if len(members) < 2 or any(m["id"] in chained for m in members):
            continue
        members.sort(key=lambda r: r["date"])
        first = members[0]
        later = options.get(first["id"]) or []
        if not later:
            continue
        first_eaters = _leftovers.eaters_at(first["date"], first["slot"])
        items.append({
            "dish": (first["meal"] or "").strip(),
            "slot": first["slot"],
            "first": {"entry_id": first["id"], "date": first["date"], "eaters": first_eaters},
            "later": [
                {"entry_id": d["entry_id"], "date": d["date"], "eaters": d["eaters"]}
                for d in later
            ],
            "eaters_total": first_eaters + sum(d["eaters"] or 0 for d in later),
        })
    # The week's own order: the day that cooks first comes first.
    items.sort(key=lambda i: (i["first"]["date"], i["slot"], i["dish"].lower()))
    return items


def mark_cook_ahead_asked(weekly_plan_id: int) -> None:
    """
    Records that the approval-time cook-ahead card has been answered for
    this plan — its own gate, the same shape defrost.mark_defrost_asked
    uses and for the same reason. Set unconditionally: "cook each on its
    own" answers the question as completely as ticking days does, and the
    Cook view's "Cooking ahead?" link may reopen and re-answer it any
    number of times.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET cook_ahead_asked_at = datetime('now') WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()


def attach_cook_ahead(weekly_plan_id: int, meals: list[dict]) -> None:
    """
    Hang the picker's data on the Cook view's cards as
    `cook_ahead: {"days": [...]}` — empty for a card with no repeat to
    consolidate, and for a reheat night. Called from get_cooker_view after
    the chains have already been applied, so `selected` and the reheat
    cards it produced always agree with each other.
    """
    options = cook_ahead_options(weekly_plan_id)
    for card in meals:
        card["cook_ahead"] = {"days": options.get(card.get("entry_id"), [])}


def _target_key(row) -> str:
    return f"{row['date']}:{row['slot']}"


def _targets_of(derived: dict) -> list[str]:
    targets = derived.get("make_double_for") or []
    if isinstance(targets, str):  # tolerate the pre-fix scalar shape
        targets = [targets]
    return list(targets)


def _cook_ahead_note(source_row, target_keys: list[str]) -> str:
    """
    The source's own stored note ("One batch on Monday covers Wednesday
    and Friday too.").

    Written for the same reason repair_leftover_chains writes
    make_double_note: the Cook view builds its live sentence from real
    headcounts (leftovers.cook_ahead_note), and falls back to this one for
    a household with nobody on record to count.
    """
    days = _leftovers._join_days([_weekday(t.split(":")[0]) for t in sorted(target_keys)])
    return f"One batch on {_weekday(source_row['date'])} covers {days} too."


def _save_derived(entry_id: int, derived: dict) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(derived), entry_id, household_id()),
    )
    conn.commit()
    conn.close()


def set_cook_ahead(source_entry_id: int, covered_entry_ids: list[int]) -> dict | str:
    """
    Record "cook this one batch for these days" — or, on a refusal, return
    the sentence to show instead.

    Writes both halves of the chain (the source's make_double_for and each
    covered day's links_to), replaces whatever this source was covering
    before, and releases the days that were dropped by clearing their
    links_to. Passing an empty list is how a household undoes the whole
    thing: the source loses make_double_for and is an ordinary cook again,
    exactly as it is when its last target is swapped away
    (weekly_plan._unlink_leftover_target).

    Returns a plain STRING on refusal rather than raising, because every
    refusal here is a sentence a person needs to read on the card they
    just tapped — not a server error. Refuses when the source is itself a
    reheat, and when a chosen day already belongs to another batch or is
    a batch of its own; both would produce a chain
    plan_leftover_chains would then decline to honour, so refusing plainly
    beats writing something that silently does nothing.

    A target this source was covering but which the picker never offered
    (a differently-named leftovers night the planner chained, say) is left
    exactly as it is — the picker governs the days it shows and nothing
    else.
    """
    conn = get_conn()
    source = conn.execute(
        "SELECT id, weekly_plan_id, date, slot, slot_state, derived_from_json "
        "FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (source_entry_id, household_id()),
    ).fetchone()
    conn.close()
    if source is None or not source["weekly_plan_id"]:
        return "I can’t find that meal on this week’s plan."

    plan_id = source["weekly_plan_id"]
    rows = {r["id"]: r for r in _plan_rows(plan_id)}
    source_row = rows.get(source_entry_id)
    if source_row is None or not _is_cookable(source_row):
        return "There’s nothing to cook on that day."

    options = cook_ahead_options(plan_id)
    if source_entry_id not in options:
        return "That one reheats an earlier batch, so it isn’t the day that cooks."

    offered = {d["entry_id"]: d for d in options[source_entry_id]}
    chains = _leftovers.plan_leftover_chains(plan_id)
    chosen = []
    for entry_id in dict.fromkeys(covered_entry_ids or []):
        row = rows.get(entry_id)
        if row is None or not _is_cookable(row):
            return "One of those days isn’t on the plan anymore."
        if entry_id in chains["sources"]:
            return f"{_weekday(row['date'])} is already cooking ahead for other days — free that one up first."
        covering = chains["leftovers"].get(entry_id)
        if covering and covering["source"]["entry_id"] != source_entry_id:
            return f"{_weekday(row['date'])} already comes from another batch, so it can’t come from this one too."
        if entry_id not in offered:
            return "That day isn’t one this batch can cover."
        chosen.append(row)

    chosen_keys = sorted(_target_key(r) for r in chosen)
    offered_keys = {_target_key(rows[e]) for e in offered}
    source_derived = _derived(source_row)
    # Everything this source covered that the picker never showed stays
    # put; everything it did show is now exactly what was ticked.
    kept = [t for t in _targets_of(source_derived) if t not in offered_keys]
    targets = sorted(set(kept) | set(chosen_keys))

    if targets:
        source_derived["make_double_for"] = targets
        source_derived["make_double_note"] = _cook_ahead_note(source_row, targets)
    else:
        source_derived.pop("make_double_for", None)
        source_derived.pop("make_double_note", None)
    _save_derived(source_entry_id, source_derived)

    chosen_ids = {r["id"] for r in chosen}
    for entry_id in offered:
        row = rows[entry_id]
        derived = _derived(row)
        if entry_id in chosen_ids:
            derived["links_to"] = _target_key(source_row)
            derived[COOK_AHEAD_FLAG] = True
        elif derived.get("links_to"):
            # Released: it cooks for itself again. The flag goes with the
            # link — a day with no source has nothing to have been made
            # ahead of.
            derived.pop("links_to", None)
            derived.pop(COOK_AHEAD_FLAG, None)
        else:
            continue
        _save_derived(entry_id, derived)

    return {
        "source_entry_id": source_entry_id,
        "covered_entry_ids": sorted(chosen_ids),
        "make_double_for": targets,
    }
