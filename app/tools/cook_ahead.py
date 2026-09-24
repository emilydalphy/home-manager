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
               mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal,
               r.prep_time_minutes, r.cook_time_minutes, r.instructions_json
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


def _is_a_cook(row) -> bool:
    """
    Is this dish actually COOKED? A different question from _is_cookable
    above, which asks a structural one — "is there a dish on this row a
    chain could hang off" — and is what the picker and set_cook_ahead
    need, since a chain already written has to stay editable whatever the
    dish is.

    Emily, walking flow 2 on 2026-09-15: approving her week asked her nine
    batch-cook questions, including "Trail mix … it'd cook on Tuesday.
    Which other days should it cover?", plus apple slices, cheese and
    crackers, cucumbers and yogurt. Nothing about any of those is cooked,
    so there is no batch to make, and a nonsense question at the moment of
    leaving undoes the approval.

    It is built on the app's existing notions rather than invented beside
    them, and it is NOT identical to either of them — say that plainly,
    because "same rule" was the first version of this docstring and it was
    not true:

      weekly_plan._is_cook   planned, not a reheat, not takeout. Covered
                             here between _is_cookable and
                             cook_ahead_repeats' own chain check. It reads
                             no times at all.
      shell.js isRealCook    that, AND the slot shows a time (build_slot's
                             `meta`, i.e. prep + cook is more than
                             nothing). Used for snacks only.
      this                   that, OR the recipe has a written method.

    The extra clause is deliberate: a recipe with steps and no minutes is
    somebody standing at a stove with a number missing from the recipe,
    not a bowl of fruit. The direction is safe — this only ever offers
    MORE than isRealCook would — but three surfaces now give three answers
    to "is this a cook", and two consequences follow that Emily should see
    rather than discover. A repeated FREEFORM dinner counts toward the "4
    cooks" on the week receipt (_is_cook reads no times) and is never
    offered for batching two inches below it. And a saved recipe with
    steps and no times is a cook here, a cook on the receipt, and not a
    cook to the snack rows on Plan. Folding the three into one is its own
    card; it cannot be done from here, because weekly_plan._is_cook takes
    a menu-entry dict rather than a row.

    A freeform meal has neither times nor steps, so it is not a cook here
    — which is at least what the SCREENS say about one: build_slot has no
    recipe to read times off and hands back `meta: null`.
    """
    if not _is_cookable(row):
        return False
    minutes = (row["prep_time_minutes"] or 0) + (row["cook_time_minutes"] or 0)
    if minutes > 0:
        return True
    try:
        steps = json.loads(row["instructions_json"] or "[]")
    except (TypeError, ValueError):
        steps = []
    return bool(steps)


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

    ONE option per (date, slot). A chip is a DAY — it reads "Wed" — and
    the record behind it is a day and a slot (make_double_for), so a day
    holding the same dish in BOTH of its snack slots used to offer two
    chips both saying "Tue" and then write one key for the pair: the note
    read "enough for Monday, Tuesday, and Tuesday", and taking either row
    away removed the key they shared and collapsed the whole batch. The
    cost is stated rather than hidden — a day that really does repeat one
    snack gets one of the two covered and cooks the other — and it is a
    shape the app already treats as a mistake
    (plan_quality.snacks_distinct_per_day). Covering the whole day instead
    would mean re-keying make_double_for, which six other readers parse as
    a date.

    WHICH row the chip stands for is the row THIS source is already
    covering, and only failing that the earliest claimable one. Taking the
    earliest unconditionally was wrong and reachable in four ordinary
    taps: with Wednesday's other snack claimed by Tuesday's card, Monday's
    chip was the later row and covered it — and the moment Tuesday let go,
    the earlier row was free again, won the chip on id alone, and reported
    `selected: False` for a day Monday's own card said in words that it
    covered. Ticking that chip then put TWO rows behind one key, which is
    the pair of symptoms this dedupe exists to remove, arriving through
    the dedupe itself.
    """
    # Sorted, because "the earliest row of a day" has to mean the same row
    # on every read: _plan_rows has no ORDER BY, and which of a day's two
    # snacks an unordered read happens to hand back first is exactly the
    # ambiguity this whole fix is about.
    rows = sorted(
        (r for r in _plan_rows(weekly_plan_id) if _is_cookable(r)),
        key=lambda r: (r["date"], r["slot"], r["id"]),
    )
    chains = _leftovers.plan_leftover_chains(weekly_plan_id)
    covered = chains["leftovers"]
    source_ids = set(chains["sources"])

    options: dict[int, list[dict]] = {}
    for row in rows:
        if row["id"] in covered:
            continue  # a reheat night is not a cook
        by_day: dict[tuple[str, str], dict] = {}
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
            option = {
                "entry_id": other["id"],
                "date": other["date"],
                "slot": other["slot"],
                "eaters": _leftovers.eaters_at(other["date"], other["slot"]),
                "selected": covering is not None,
            }
            # One chip per day, standing for the row this source already
            # covers where there is one — see the docstring. `rows` is
            # sorted, so the first candidate for a day is the earliest,
            # and only a covered sibling displaces it.
            key = (other["date"], other["slot"])
            current = by_day.get(key)
            if current is None or (option["selected"] and not current["selected"]):
                by_day[key] = option
        options[row["id"]] = sorted(by_day.values(), key=lambda d: d["date"])
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

    And so is anything that is not actually COOKED (_is_a_cook): trail
    mix, apple slices, cheese and crackers. This is the only surface that
    applies that rule — cook_ahead_options and set_cook_ahead deliberately
    keep the wider _is_cookable, because a chain already written has to
    stay editable and releasable whatever the dish is, and because on the
    Cook card the household is standing on one dish having chosen to look
    at it. The two are different questions: "is this worth asking about"
    and "what days can this row claim".
    """
    rows = [r for r in _plan_rows(weekly_plan_id) if _is_a_cook(r)]
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


def apply_prep_day_batches(weekly_plan_id: int) -> dict:
    """
    Batch cooking is assumed from prep days (Emily, 2026-09-18, Loop Board
    "Batch cooking is assumed from prep days"): the approval-time
    "Do you want to batch cook…" ask is gone from All set and the Plan
    root, and this is the rule that stands in for it.

    When a household has one or more prep days (rhythm.prep_days) and a
    dish is on the plan more than once in the same slot, the FIRST day it
    appears is the cook and the later days are covered by that batch —
    exactly the chain a "yes" on the old ask wrote (set_cook_ahead, with
    every later day ticked), so servings and the shopping list scale as
    they did then. With no prep days nothing is batched, and nothing is
    asked either way. cook_ahead_asked_at is set in both cases so no
    surface re-asks.

    Kept simple on purpose: the first occurrence cooks, whether or not it
    falls on a prep day. Choosing the prep day itself as the cook would
    mean cooking on a day the dish isn't eaten, which the chain machinery
    (make_double_for on a planned entry) can't represent.

    The same rule, one level down (2026-09-21, Loop Board "Batch a shared
    ingredient automatically when the household preps"): a COMPONENT two
    or more different dishes each cook the same way — the eggs a
    breakfast and a salad both boil — is batched too, exactly as a "yes"
    on the old ask's component block wrote it
    (batch_components.set_batch_component with every dish ticked): one
    prep row on the first dish's day, the later dishes reading "boiled
    Monday" on their Cook screens. On the first dish's day, not the prep
    day, for the same reason the repeated dish cooks on its first day —
    the detector knows nothing about how long a boiled egg keeps, and a
    row dated on a day no dish uses it is a row nobody can tick from a
    cook screen. `components` reports what landed; a component already
    batched (shared_components' `batched`) is left exactly as it is, so
    running twice writes nothing twice.

    A batch the household has already un-batched is left alone, however
    many times the week is approved (`declined` names what was skipped and
    why) — see batch_undo.py. That memory lives on the entries themselves,
    not here.

    Called from approve_weekly_plan once the yes has really done something
    — never on a no-op re-approval. Not all-or-nothing: a repeat the
    chain refuses (a day claimed by another batch) is reported in
    `refused` and the rest still land, the shape confirm_week_cook_ahead
    used.
    """
    from . import rhythm as _rhythm
    from . import batch_components as _batch_components
    from . import batch_undo as _batch_undo

    prep_days = _rhythm.get_household_rhythm().get("prep_days") or []
    applied: list[dict] = []
    refused: list[dict] = []
    components: list[dict] = []
    declined: list[dict] = []
    if prep_days:
        # A batch the household already took apart is not offered back.
        # Re-approving a week (reopen, then approve again) is a genuine
        # approval, so without this the rule would quietly put back the
        # very thing they said no to — "the automatic batching never traps
        # me" is the whole point of batch_undo.py, and a choice that lasts one
        # approval is not a choice. Read off the entries themselves
        # (derived_from.no_batch / .no_batch_components), so a day swapped
        # away takes its own objection with it.
        no_batch_days = _batch_undo.declined_dish_entry_ids(weekly_plan_id)
        no_batch_keys = _batch_undo.declined_component_keys(weekly_plan_id)
        for item in cook_ahead_repeats(weekly_plan_id):
            later = [d["entry_id"] for d in item["later"] if d["entry_id"] not in no_batch_days]
            if not later:
                declined.append({"kind": "dish", "dish": item["dish"]})
                continue
            result = set_cook_ahead(item["first"]["entry_id"], later)
            if isinstance(result, str):
                refused.append({"source_entry_id": item["first"]["entry_id"], "dish": item["dish"], "note": result})
            else:
                applied.append(dict(result, dish=item["dish"]))
        for comp in _batch_components.shared_components(weekly_plan_id):
            if comp["batched"]:
                continue
            if comp["key"] in no_batch_keys:
                declined.append({"kind": "component", "key": comp["key"], "label": comp["label"]})
                continue
            result = _batch_components.set_batch_component(
                weekly_plan_id, comp["key"], [u["entry_id"] for u in comp["uses"]]
            )
            if isinstance(result, str):
                refused.append({"key": comp["key"], "label": comp["label"], "note": result})
            else:
                components.append(result)
    mark_cook_ahead_asked(weekly_plan_id)
    return {"prep_days": bool(prep_days), "applied": applied, "components": components,
            "refused": refused, "declined": declined}


def batched_dishes(weekly_plan_id: int) -> list[dict]:
    """
    Every dish on this plan cooked once for several days ON PURPOSE — a
    chain whose covered days carry the cook_ahead flag, whether the
    prep-day rule wrote it at approval or the household ticked it on the
    Cook card. A leftovers night the planner wrote is not one: nobody
    batched anything there, and the All set line (weekly_plan.
    batched_line) is only allowed to claim what was actually batched.

    [{source_entry_id, date, slot, dish, covered: [{entry_id, date, slot}]}]
    in the week's order, covered days in theirs (plan_leftover_chains
    already sorts a source's targets by date and slot).
    """
    out = []
    for src in _leftovers.plan_leftover_chains(weekly_plan_id)["sources"].values():
        covered = [
            {"entry_id": t["entry_id"], "date": t["date"], "slot": t["slot"]}
            for t in src["targets"] if t.get("cook_ahead")
        ]
        if not covered:
            continue
        out.append({
            "source_entry_id": src["entry_id"],
            "date": src["date"],
            "slot": src["slot"],
            "dish": (src.get("meal") or "").strip(),
            "covered": covered,
        })
    out.sort(key=lambda g: (g["date"], g["slot"], g["dish"].lower()))
    return out


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


def _source_ref(row) -> str:
    """
    What a covered day's links_to says to name the day that cooks:
    "entry_id:<n>", the other shape weekly_plan's resolvers have always
    accepted, rather than the "YYYY-MM-DD:slot" one the planner writes.

    A date and a slot name a day's DINNER unambiguously and a day's SNACK
    not at all — preferences.resolve_snacks_per_day gives a day two of
    them by default, and both rows are filed under "snack". So a chain
    written that way was resolved against whichever of the day's snacks
    the query happened to hand back last: usually the other one, which
    confirms nothing, so the chain was dropped and the batch never
    scaled (Emily, 2026-09-14, Roasted Chickpeas on three afternoons).

    Unlike the planner, this module is writing about a row it is holding,
    so it can name that row.

    THE TARGET SIDE IS STILL "date:slot", AND THAT IS A KNOWN COMPROMISE
    RATHER THAN A SAFE ONE. An earlier version of this docstring said the
    target side "is never resolved by key" and that "only one row on a day
    carries it". Both are false and were believed for a day: those keys
    are resolved by key in leftovers.plan_leftover_chains' agreement
    check, in weekly_plan._unlink_leftover_target's removal, in
    _apply_dinner_nights_swap's re-dating map and in
    repair_leftover_chains, and two rows of one day really can carry one
    — which is precisely why cook_ahead_options offers one chip per day
    and releases a hidden sibling with it. Re-keying make_double_for to
    entry ids is the honest fix and is its own card: six readers parse
    those keys as dates.

    Written for every slot, not only snacks. A dinner day holds one
    dinner, so "date:dinner" would have gone on working — but naming the
    row is strictly safer even there (a source that is deleted and
    replaced leaves a date key a replacement row can capture, and an id
    simply dangles), and one form beats two.
    """
    return f"entry_id:{row['id']}"


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
    # Every row the chips speak for: the offered rows, plus any SIBLING on
    # the same day this source is still covering. One chip stands for a
    # whole day (cook_ahead_options), so a sibling the dedupe hid must not
    # be left pointing at a batch the household has just re-answered — and
    # it is what heals a day that already has two rows behind one key.
    # Scoped to rows linked to THIS source: a sibling covered by somebody
    # else's batch is not this picker's to release.
    my_ref = _source_ref(source_row)
    siblings = [
        r["id"] for r in rows.values()
        if r["id"] not in offered
        and _target_key(r) in offered_keys
        and (_derived(r).get("links_to") or "") == my_ref
    ]
    for entry_id in list(offered) + siblings:
        row = rows[entry_id]
        derived = _derived(row)
        if entry_id in chosen_ids:
            derived["links_to"] = _source_ref(source_row)
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
