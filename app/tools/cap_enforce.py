"""
"Short on time" is held to by CODE, not only asked for.

Emily, 2026-09-26, off her own morning report: five dinners on three nights
she had tagged `rush`, every one over her 20-minute cap — 35, 32, 29, 28
and 24 minutes. The cap was TOLD to the model (three times in agent.py's
prompts), MEASURED afterwards (plan_quality's `rush_cap_respected`, severity
`warn`, into the morning report and no screen the household sees), and
enforced nowhere in between. The swap door has held it since 2026-09-22
(swap_in_place.cap_gate refuses a pick in the app's own words — "I left it
as it was — X takes 35 minutes, and Friday only has 20"); generation did
not. One door held the household's constraint and the other did not.

This module is the enforcement. `time_caps` holds the RULE — what a cap is
for one meal — and imports nothing from the app so every reader can use it;
this holds what happens when a cap is broken, and is the only place that
writes. One rule, one enforcer.

Two stages, gentlest first (the card's own instruction — "the cost of
enforcement should be plainer weeknight dinners, never nights handed back
empty"):

 1. RE-ARRANGE, and it spends nothing. The week's own dinners are traded
    between nights until no trade makes it better, through
    weekly_plan.swap_dinner_nights — the write the Plan tab's seven tiles
    already use, which re-dates the rows IN PLACE (ids kept, so groceries,
    the cooked tick and the plate's sides ride along), moves the defrost
    reminders with them and touches the shopping list not at all. A week
    with a long dish and a free Saturday needs nothing else.

 2. RE-PICK what is still over cap, through meal_variety._repick_entry —
    the picker every other re-pick pass in generation uses — with the cap
    itself as the pick's own refusal (`reject_pick`), because pick_gate is
    the allergen and taste gate and has never included the clock. It spends
    the generation's EXISTING shared re-pick budget (allergen_gate.
    CallBudget, 6 calls for the whole generation), so it adds no new
    ceiling; when the earlier passes have spent it, the week stands as
    generated and the warn is what is left, which is the same safe
    direction the allergen sweep already documents.

A night is never handed back as `open` here. Emily's 20 may be tighter than
she meant, and a night with a plainer dinner on it is worth more than an
empty one.

WHAT IS LEFT ALONE, and why each:

  * a night the household spoke for — their own typed words for it
    (derived_from.freeform) or a meal they ticked to bring over from last
    week (meal_variety.theirs) — and a night already cooked. Every repair
    pass in generation leaves those standing; a cap is not a reason to
    overrule a choice.
  * a reheat night, and the cook that feeds it. Nothing is cooked on a
    reheat, so the clock does not apply to it; and moving one end of a
    cook-once-eat-twice pair can stretch the gap past
    leftovers.MAX_LEFTOVER_DAYS, which repair_leftover_chains would then
    tear down — a worse outcome than a long dinner.
    **THIS IS THE BIGGEST HOLE IN THE CARD, and generation's own fold is
    what opens it.** A household asking for fewer dinners than nights gets
    repeats, and meal_variety's fold turns a repeat within three days into
    exactly such a chain — so an over-cap dinner cooked on a rush Tuesday
    and reheated on the Wednesday is reached by NEITHER stage here, and
    plan_quality goes on warning about both. Measured through generation,
    not reasoned: test_generations_own_fold_puts_a_repeated_over_cap_
    dinner_out_of_reach. The reheat end is plainly right; the COOK end is
    Emily's call, since the household really is cooking 35 minutes on a
    night they said was short on time and it is deliberate batching. What
    would close it is moving a chain WHOLE, both nights together, which
    swap_dinner_nights cannot do. Its own card. Worth knowing with it: a
    repeat spread FURTHER than three days is not chained and IS reached —
    measured on Emily's own shape (three dinners over seven nights, the
    repeat four days apart), where the pass takes the week to zero.
  * a dish whose recipe records no minutes. Unknown minutes cannot be
    judged, exactly as time_caps' own readers let them through, and moving
    one buys nothing measurable.
  * `planned_empty` and `open` slots, which are not dinners.
  * lunch and breakfast in STAGE 1. See "LUNCH AND BREAKFAST" below.

LUNCH AND BREAKFAST (2026-10-02, Loop Board "A weekday lunch you said you'd
cook in 20 minutes is drafted at 35"). A weekday lunch the household said is
cooked that day has its own cap (time_caps.WEEKDAY_LUNCH_MAX_MINUTES), and
until this the loader below read dinners only, so nothing held a lunch to
it: the 35-minute lunch stayed, `left` never named it, and plan_quality's
`weekday_lunch_cap_respected` warned into the morning report. Lunch and
breakfast now get STAGE 2 ONLY — the same re-pick, the same shared budget,
the same "never hand the slot back as a question", run after the dinners so
dinner keeps first call on the budget. The cap is time_caps.minutes_cap's,
asked with the week's own weekday-lunches answer (`lunch_kind`) and with
`is_leftovers` for either end of a chain, exactly as plan_quality asks it,
so every carve-out (a chain, a standing prep day unless that day's answer
is "cooked", the weekend, a meal they asked for, a meal already cooked) is
the rule's and not re-derived here. Breakfast has no cap in time_caps today,
so for breakfast this runs and finds nothing; it is wired so that a
breakfast cap, the day the household is asked for one, is enforced without
another card.

  STAGE 1 (THE FREE TRADE) DOES NOT EXIST FOR LUNCH, deliberately.
  swap_dinner_nights is dinner-only by name and by contract, and there is
  no lunch door that re-dates rows in place. Building one means deciding
  what a trade does to a leftovers chain (a lunch is often the reheat end of
  a dinner) and to a prepped-lunch batch (weekday_lunches' prep-day cook and
  the lunches it feeds share one dish across days) — the fiddliest part of
  the week, and its own card. Until then an over-cap lunch is re-picked or
  left as drafted with the reason in `left`; never traded.

FOUND WHILE MEASURING AND DELIBERATELY NOT FIXED: on a week with a
leftovers chain, plan_quality goes on warning and this pass cannot help.
time_caps.minutes_cap consults `is_leftovers` for LUNCH only, so for dinner
a 90-minute batch on a capped Monday AND the Tuesday that merely reheats it
both come out over cap — and `_weekday_lunch_cap_respected`, one rule down
in the same file, already exempts a chain. Exempting a chained DINNER inside
minutes_cap would also lift the cap on swap_in_place's gate, where a swap
onto a reheat night breaks the chain and the replacement really is cooked
that day: two different questions on one (date, slot). Its own card, and
Emily's decision — is a deliberate 90-minute Monday batch a breach at all?
See test_a_chain_on_a_weeknight_still_warns_and_is_NOT_fixed_here.

A TRADE IS REFUSED WHEN IT WOULD PUT A DISH IN FRONT OF SOMEONE WHO HAS
SAID NO TO IT. Who is at the table is per night, so the taste verdict is
the one gate a move can break (weekly_plan._taste_verdict_for_slot, Emily's
one-veto rule). The allergen answer is household-wide and invariant under a
move (coordination.check_meal_conflicts takes no date), and anything a move
does create is caught by the allergen sweep, which runs after this.
"""
from __future__ import annotations

import datetime
import json
import logging

from . import time_caps as _time_caps
from . import weekly_plan as _weekly_plan
from . import meal_variety as _meal_variety
from . import leftovers as _leftovers
from . import draft_flags as _draft_flags
from ._shared import household_id
from ..db import get_conn

logger = logging.getLogger("home_manager")

# What derived_from records about a dinner this pass re-picked, so the draft
# and the log can say why it is here. Read by nothing else yet.
REPICK_KEY = "cap_repick"
# ...and about a dinner this pass moved to another night. swap_dinner_nights
# writes its own `moved_from` token (that is what its Undo needs); this is
# the separate record of WHY, on the same row.
MOVED_KEY = "cap_moved"

# A RE-PICKED DINNER KEEPS THE PICK'S OWN REASON, which is _repick_entry's
# default and what every caller but one uses. Emily's "no note" decision
# (2026-09-25, meal_variety.REPEAT_REASON) was about not ANNOUNCING what
# the app does anyway — "X back from the last two weeks" — and nothing here
# announces the cap either: the pick's reason is about the DISH ("quick on
# the night"), and what happened is on record in derived_from[REPICK_KEY].
#
# An EMPTY reason was the first answer, and the measurement that ruled it
# out HAS SINCE EXPIRED — corrected 2026-10-01 rather than left standing,
# because the next reader acts on it. What was measured (2026-09-26) is
# that `reasoning_is_specific` fired "has no reasoning at all" for every
# blank row, so the pass traded 3 cap warnings for 5 reasoning warnings.
# That rule stopped firing on a blank on 2026-09-30 (45e2bc0): the week
# call is no longer asked for a per-slot line at all, so blank is the
# normal case and `_reasoning_is_specific` returns early on it. Measured
# again on this tree: a blank reason produces ZERO findings, and only
# generic filler is caught.
#
# None STILL STANDS, on the reason that never depended on the warning:
# the pick's own reason is about the DISH ("quick on the night") and what
# happened is on record in derived_from[REPICK_KEY], so a re-picked
# dinner keeping the pick's default is the honest answer. Emily's "no
# note" decision (2026-09-25, meal_variety.REPEAT_REASON) was about not
# ANNOUNCING what the app does anyway, and nothing here announces the
# cap. None means "the pick speaks for itself".
REPICK_REASON = None

# A MOVE IS THE ONE THAT DOES CARRY A SENTENCE. A moved dinner keeps its
# dish, so it keeps a reason the model wrote about the night it is no
# longer on ("lighter after Monday's chili") — which can be plainly false
# once it has moved. THAT is why it carries one, and that reason is
# untouched.
#
# THE ARGUMENT AGAINST EMPTYING IT HAS EXPIRED, and saying so is the
# point of this correction (2026-10-01). It used to read "emptying it
# re-introduces the warnings above", on the 2026-09-26 measurement that
# `reasoning_is_specific` fired "has no reasoning at all" for every blank
# row — so a two-night trade put two fresh warnings into the morning
# report this pass exists to quiet. That rule stopped firing on a blank
# on 2026-09-30 (45e2bc0), when the week call stopped being asked for a
# per-slot line and blank became the normal case. Measured again on this
# tree: a blank reason produces ZERO findings. So emptying this costs
# nothing in the report, and a future session must not be told otherwise
# — an unmeasured cost claim in a comment is what the next reader acts
# on, which is a mistake this log has had to unpick before.
#
# What is left is one short sentence that is true of BOTH sides of any
# trade this pass makes, whether or not the cap ends up met — the app
# moved them so the week fits the time the household has. It is behind a
# tap, never in the face, so it is not an announcement in the sense
# Emily's plain-copy rule is about. The cost, named: the model's own
# reason is lost, including the part that may have been about the DISH
# rather than the night.
#
# HERS, AND CHEAPER THAN IT WAS — but NOT free, and this comment said
# free until an adversarial review measured it (2026-10-01). Emptying it
# costs nothing in the MORNING REPORT: plan_quality stopped flagging a
# blank row on 2026-09-30 (45e2bc0), and a real generated capped week
# with this blank returns zero findings from any rule. It still costs
# exactly ONE red test — tests/test_rush_cap_enforced.py::
# test_a_moved_dinner_loses_a_reason_written_about_the_night_it_left
# asserts the moved row's reasoning is non-blank, measured across the
# full 8947. (REPICK_REASON's own sibling test does the same for it.) So
# emptying this is one copy decision plus one test correction — and that
# test's stated reason for forbidding a blank is the very measurement
# retired above, so whoever empties it has to retire that too rather
# than rediscover it.
MOVE_REASON = "Moved here so the week fits the time you have."

# A week has seven nights, so a handful of trades settles it; the bound is
# there so a swap that reports success without changing anything cannot
# spin. Each round is a strict improvement on the one before it, so real
# work on a seven-day plan can never reach this.
_MAX_TRADES = 12


def _minutes(row: dict) -> int | None:
    prep, cook = row.get("prep_time_minutes"), row.get("cook_time_minutes")
    if prep is None and cook is None:
        return None
    total = (prep or 0) + (cook or 0)
    return total or None


def _weekday(meal_date: str) -> str:
    try:
        return datetime.date.fromisoformat(meal_date).strftime("%A")
    except (TypeError, ValueError):
        return meal_date


# The slots held to their caps by the re-pick alone, after the dinners (see
# "LUNCH AND BREAKFAST" in the module docstring). Lunch first: it is the one
# with a cap today, so it has first call on what the dinners left of the
# shared budget.
REPICK_ONLY_SLOTS = ("lunch", "breakfast")


def _load_meals(plan_id: int, slot: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.cooked_status,
               mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal,
               r.prep_time_minutes, r.cook_time_minutes
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.slot = ?
          AND mpe.component_category IS NULL
        ORDER BY mpe.date ASC, mpe.id ASC
        """,
        (plan_id, household_id(), slot),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _load_dinners(plan_id: int) -> list[dict]:
    return _load_meals(plan_id, "dinner")


def nights(plan_id: int, intake: dict | None, memory: dict | None, slot: str = "dinner") -> list[dict]:
    """
    Every meal of one slot on this plan (dinner unless told otherwise), with
    its own cap and whether this pass may touch it. One read of the plan and
    one read of the rules, so the two stages below and the tests can never
    disagree about a night.
    """
    from . import weekday_lunches as _weekday_lunches

    night_tags = ((intake or {}).get("night_tags") or {})
    lunch_kinds = _weekday_lunches.kinds_by_date(intake) if slot == "lunch" else {}
    chains = _leftovers.plan_leftover_chains(plan_id)
    out = []
    for row in _load_meals(plan_id, slot):
        derived = {}
        try:
            derived = json.loads(row.get("derived_from_json") or "{}") or {}
        except (TypeError, ValueError):
            derived = {}
        frozen = derived.get(_leftovers.FROM_FREEZER_KEY)
        reheat = bool(row["id"] in chains["leftovers"] or derived.get("links_to")
                      or (isinstance(frozen, dict) and (frozen.get("dish") or "").strip()))
        source = bool(row["id"] in chains["sources"] or derived.get("make_double_for"))
        if slot != "dinner" and (derived.get("prep_date") or derived.get("prep_day_cook")
                                 or derived.get("constraint") == _weekday_lunches.CONSTRAINT):
            # A prepped-lunch batch's cook (weekday_lunches.apply_to_plan):
            # a batch, not a cook on the day, and the household's own answer.
            source = True
        minutes = _minutes(row)
        if slot == "dinner":
            cap = _time_caps.minutes_cap(row["date"], "dinner", night_tags.get(row["date"]) or [], memory)
        else:
            # Asked exactly as plan_quality._weekday_lunch_cap_respected asks
            # it, so the pass and the tripwire agree about every lunch. Night
            # tags are about dinner and are not passed.
            cap = _time_caps.minutes_cap(row["date"], slot, [], memory,
                                         is_leftovers=reheat or source,
                                         lunch_kind=lunch_kinds.get(row["date"]))
        out.append({
            "id": row["id"],
            "date": row["date"],
            "meal": row["meal"],
            "minutes": minutes,
            "cap": cap,
            "derived": derived,
            # Carried for draft_flags, which asks a different question of
            # the same rows: `movable` collapses every reason a night is
            # untouchable into one boolean, and the flag has to tell a
            # night the household asked for (worth saying out loud) from
            # one already cooked (nothing left to offer).
            "slot_state": row["slot_state"],
            "cooked_status": row["cooked_status"] or "",
            "tags": night_tags.get(row["date"]) or [],
            # Over its own cap, and known to be: an unjudgeable dish is not
            # a violation (time_caps' own readers let it through).
            "over": bool(cap and minutes and minutes > cap),
            # Whether this pass may move or re-pick it. Each reason is in
            # the module docstring; they are kept apart from `over` because
            # a night can be over cap AND untouchable, which is exactly the
            # residue plan_quality goes on warning about.
            "movable": (
                row["slot_state"] == "planned"
                and bool((row["meal"] or "").strip())
                and (row["cooked_status"] or "") != "done"
                and not _meal_variety.theirs(derived)
                and not reheat and not source
                and minutes is not None
            ),
            "reheat": reheat,
            "source": source,
        })
    return out


def _violations(assignment: dict[str, dict]) -> tuple[int, int]:
    """
    How bad one arrangement is: the number of nights over their cap, then
    the total minutes of overrun. Sorting on the pair means a trade that
    cannot remove a violation may still shorten one, and a trade that does
    neither is never made.
    """
    count = overrun = 0
    for date, night in assignment.items():
        cap, minutes = night["cap"], night["minutes"]
        if cap and minutes and minutes > cap:
            count += 1
            overrun += minutes - cap
    return count, overrun


def _trades(assignment: dict[str, dict], caps: dict[str, int | None]) -> list[tuple[str, str]]:
    """
    Every pair of nights whose dinners are worth trading, best first, and
    only pairs that make the week STRICTLY better by _violations. Empty
    when no trade does.

    A list rather than one answer because a trade can be refused — by the
    taste rule, or by swap_dinner_nights itself — and the next-best trade
    is then still worth making. Greedy on _violations: every step is an
    improvement on the one before it, so stopping part-way can never leave
    the week worse than it was found.

    For the shape this card is actually about — one cap across Monday to
    Friday and none at the weekend — greedy reaches the floor: it stops
    only when every uncapped night holds an over-cap dish, or every
    over-cap dish is already on an uncapped night, and no assignment does
    better than that. With several different caps it is a heuristic and is
    not claimed to be more.
    """
    before = _violations(assignment)
    dates = sorted(assignment)
    out = []
    for i, a in enumerate(dates):
        for b in dates[i + 1:]:
            trial = dict(assignment)
            trial[a] = dict(assignment[b], date=a, cap=caps[a])
            trial[b] = dict(assignment[a], date=b, cap=caps[b])
            after = _violations(trial)
            if after < before:
                out.append((after, a, b))
    out.sort()
    return [(a, b) for _, a, b in out]


def would_offend(meal: str, meal_date: str) -> str | None:
    """
    Why this dish must not move to this night — somebody eating that night
    has said no to it (Emily's one-veto rule, the same call pick_gate
    makes). Allergens are household-wide and so cannot change under a move;
    see the module docstring.

    PUBLIC because `draft_flags.move_target` asks it too, and must. That
    module offers the household a one-tap "Move it to Saturday"; without
    this, the button would sail past the veto this pass refuses its own
    trades on, and a dish somebody has said no to would land in front of
    them on one tap with nothing said. One rule, two callers — a second
    copy of it is the class this repo keeps getting bitten by.
    """
    verdict = _weekly_plan._taste_verdict_for_slot(meal, meal_date, "dinner")
    if verdict and verdict.get("verdict") == "avoid":
        who = ", ".join(verdict.get("vetoed_by") or []) or "someone at the table"
        return f"{who} would rather not"
    return None


def _note_move(moved: list[dict], caps: dict[str, int | None]) -> None:
    """
    Record on each moved row where it came from, and replace the reasoning
    that was written about the night it is no longer on (see MOVE_REASON).
    Its own connection, AFTER swap_dinner_nights' transaction has
    committed — never inside it: a nested connection in an open write
    transaction is this repo's "database is locked" trap.
    """
    conn = get_conn()
    try:
        for m in moved:
            row = conn.execute(
                "SELECT derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
                (m["entry_id"], household_id()),
            ).fetchone()
            if row is None:
                continue
            try:
                derived = json.loads(row["derived_from_json"] or "{}") or {}
            except (TypeError, ValueError):
                derived = {}
            derived[MOVED_KEY] = {"from": m["from"], "cap": caps.get(m["from"])}
            conn.execute(
                "UPDATE meal_plan_entries SET derived_from_json = ?, reasoning = ? "
                "WHERE id = ? AND household_id = ?",
                (json.dumps(derived), MOVE_REASON, m["entry_id"], household_id()),
            )
        conn.commit()
    finally:
        conn.close()


def rearrange(plan_id: int, intake: dict | None, memory: dict | None) -> list[dict]:
    """
    Trade the week's own dinners between its own nights until no trade
    makes it better. Spends nothing — no model call, no grocery write, the
    same dishes on the same week, and the shopping list untouched
    (swap_dinner_nights re-dates the rows in place). Returns one record per
    trade made.
    """
    made: list[dict] = []
    for _ in range(_MAX_TRADES):
        rows = nights(plan_id, intake, memory)
        movable = {n["date"]: n for n in rows if n["movable"]}
        if len(movable) < 2:
            break
        caps = {d: n["cap"] for d, n in movable.items()}
        traded = False
        for a, b in _trades(movable, caps):
            # Who is at the table is per night, so this is the one gate a
            # move can break. See the module docstring on why the allergen
            # half cannot change under a move.
            offends = (would_offend(movable[b]["meal"], a)
                       or would_offend(movable[a]["meal"], b))
            if offends:
                logger.info("Plan %s: not trading %s and %s — %s", plan_id, a, b, offends)
                continue
            try:
                result = _weekly_plan.swap_dinner_nights(plan_id, a, b)
            except Exception:
                logger.exception("Plan %s: trading %s and %s to fit their time caps failed",
                                 plan_id, a, b)
                continue
            if result.get("status") != "swapped":
                # A refusal is an answer, in swap_dinner_nights' own words
                # (a night nobody is home, one already cooked, a chain that
                # would run backwards). Try the next-best trade.
                logger.info("Plan %s: %s and %s would not trade — %s",
                            plan_id, a, b, result.get("message") or "refused")
                continue
            _note_move(result.get("moved") or [], caps)
            made.append({"from": a, "to": b,
                         "moved": [{"meal": m["meal"], "from": m["from"], "to": m["to"]}
                                   for m in (result.get("moved") or [])]})
            logger.info("Plan %s: traded %s and %s so the week fits its time caps", plan_id, a, b)
            traded = True
            break
        if not traded:
            break
    return made


def _cap_reason(night: dict, cap: int, slot: str = "dinner") -> str:
    """
    Why this night is capped, in the household's own terms — their tag, or
    their standing weeknight answer. This is what the MODEL is told
    (`replacing_because`) and what goes on the row's derived_from record;
    nothing of it is shown — the dish's own reason is what the row keeps,
    per REPICK_REASON.
    """
    day = _weekday(night["date"])
    if slot == "lunch":
        return f"{day}'s lunch is cooked that day here — {cap} minutes"
    if slot != "dinner":
        return f"{day}'s {slot} has {cap} minutes"
    if "rush" in (night["tags"] or []):
        return f"you said {day} is short on time — {cap} minutes"
    return f"weeknights here are {cap} minutes"


def repick(plan_id: int, intake: dict | None, memory: dict | None, *,
           budget=None, picker=None, slot: str = "dinner") -> list[dict]:
    """
    Re-pick every meal of `slot` (dinner unless told otherwise) still over
    its cap, worst overrun first, through the swap's own picker with the cap
    as the pick's own refusal. Spends the generation's shared re-pick
    budget; a meal whose pick never arrives stands as generated rather than
    being handed back empty.
    """
    from . import allergen_gate as _allergen_gate
    from . import swap_in_place as _swap

    budget = budget or _allergen_gate.CallBudget()
    done: list[dict] = []
    rows = nights(plan_id, intake, memory, slot)
    week_dishes = {(n["meal"] or "").strip().lower() for n in rows if (n["meal"] or "").strip()}
    targets = sorted(
        (n for n in rows if n["over"] and n["movable"]),
        key=lambda n: (-(n["minutes"] - n["cap"]), n["date"]),
    )
    gone: set = set()  # meals already re-picked as part of a whole dish
    for night in targets:
        if night["id"] in gone:
            continue
        cap = night["cap"]
        because = _cap_reason(night, cap, slot)
        # The household's number of different dinners (Emily, 2026-09-28:
        # "My dinner settings is 3 but it gave me 4 meal types"). A dish on
        # other nights too, re-picked on this night alone, ADDS a dish to a
        # week that already has its number — so when it has, the WHOLE dish
        # is re-picked, every night of it at once, and the count stays.
        also = _whole_dish_nights(plan_id, night, memory, slot)
        if also is None:
            logger.info("Plan %s: %s %s %r stays over its cap — re-picking it alone would add a dish "
                        "past the household's number", plan_id, night["date"], slot, night["meal"])
            continue
        if also:
            caps_on = [cap] + [c for c in (_cook_cap(o, intake, memory) for o in also) if c]
            cap = min(caps_on)  # the pick is cooked on each of them

        def _too_long(candidate, cap=cap, night=night):
            minutes = _swap._pick_minutes(candidate)
            if minutes and minutes > cap:
                return f"takes {minutes} minutes, and {_weekday(night['date'])} only has {cap}"
            return None

        entry = {"id": night["id"], "date": night["date"], "slot": slot,
                 "meal": night["meal"], "derived_from_json": json.dumps(night["derived"])}
        replaced = _meal_variety._repick_entry(
            plan_id, entry, budget,
            avoid=sorted(week_dishes),
            because=because,
            # `week_dishes` is MUTATED in place rather than rebound, so this
            # closure sees what the week holds now — a later night cannot be
            # given a dish an earlier re-pick has just put on it.
            reject=lambda name: name.strip().lower() in week_dishes,
            reject_pick=_too_long,
            derived_key=REPICK_KEY,
            reason_line=REPICK_REASON,
            picker=picker,
            also=also or None,
        )
        if replaced is None:
            logger.warning(
                "Plan %s: %s %s %r is %d minutes against a %d-minute cap and nothing quicker "
                "came back; it stands as generated",
                plan_id, night["date"], slot, night["meal"], night["minutes"], cap,
            )
            continue
        # The dish that just left STAYS on `avoid`: it was refused for being
        # too long. UNPINNED defence in depth, measured rather than claimed —
        # _repick_entry prepends entry["meal"] to `tried` itself, so the dish
        # is on avoid for its own night regardless, and any other night is
        # covered by _too_long refusing it. Discarding it leaves every test
        # in tests/test_rush_cap_enforced.py green.
        gone.update(o["id"] for o in also)
        new_name = (replaced.get("meal") or "").strip()
        if new_name:
            week_dishes.add(new_name.lower())
        done.append({"date": night["date"], "slot": slot, "dropped": night["meal"], "with": new_name,
                     "cap": cap})
        logger.info("Plan %s: %s %s was %d minutes against a %d-minute cap — %r -> %r",
                    plan_id, night["date"], slot, night["minutes"], cap, night["meal"], new_name)
    return done


def _whole_dish_nights(plan_id: int, night: dict, memory: dict | None,
                       slot: str = "dinner") -> list[dict] | None:
    """
    The dish's OTHER meals, when re-picking this night alone would put the
    week over the household's number of different dinners; [] when a
    single-night re-pick is fine (no number set, the week is under it, the
    dish is on no other meal); None when neither can be made without going
    over their number (another night of it is cooked, or theirs — asked
    for, brought over). `slot` is the slot being re-picked, whose number
    (meal_variety.COUNT_FIELDS) is the one that matters.
    """
    memory = memory or {}
    field = _meal_variety.COUNT_FIELDS.get(slot)
    target = memory.get(field) if (field and memory.get("meal_counts_set")) else None
    if not target or len(_meal_variety.distinct_dishes(plan_id, slot)) < int(target):
        return []
    # Lunch and dinner share dishes (a lunch is often a dinner's reheat), so
    # a whole dish spans both; breakfast is its own.
    slots = ("lunch", "dinner") if slot in ("lunch", "dinner") else (slot,)
    key = _leftovers.dish_identity(night["meal"])
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.cooked_status, mpe.derived_from_json,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.component_category IS NULL
          AND mpe.slot IN ({",".join("?" for _ in slots)}) AND mpe.slot_state = 'planned'
          AND mpe.id != ?
        ORDER BY mpe.date, mpe.id
        """,
        (plan_id, household_id(), *slots, night["id"]),
    ).fetchall()
    conn.close()
    others = [dict(r) for r in rows if _leftovers.dish_identity(r["meal"]) == key]
    if not others:
        return []
    for o in others:
        derived = json.loads(o["derived_from_json"] or "{}") or {}
        if (o["cooked_status"] or "") == "done" or _meal_variety.theirs(derived):
            # The dish can't be re-picked whole, and this night alone would
            # add a dish over their number: their number wins and the night
            # stands (plan_quality warns; break_long_runs makes the same call).
            return None
    return others


def _cook_cap(row: dict, intake: dict | None, memory: dict | None) -> int | None:
    """The cap on a meal of the dish being re-picked that is COOKED on its
    day (a reheat has no cap to fit). A lunch is asked the way `nights`
    asks it (2026-10-02): before, a whole-dish re-pick ignored a weekday
    lunch's cap, so the new dish could land on a lunch cooked that day at
    any length."""
    derived = json.loads(row.get("derived_from_json") or "{}") or {}
    if derived.get("links_to") or _leftovers.frozen_portion_on(derived):
        return None
    if row["slot"] == "lunch":
        from . import weekday_lunches as _weekday_lunches
        batch = bool(derived.get("make_double_for") or derived.get("prep_date")
                     or derived.get("prep_day_cook")
                     or derived.get("constraint") == _weekday_lunches.CONSTRAINT)
        return _time_caps.minutes_cap(row["date"], "lunch", [], memory, is_leftovers=batch,
                                      lunch_kind=_weekday_lunches.kinds_by_date(intake).get(row["date"]))
    if row["slot"] != "dinner":
        return None
    tags = ((intake or {}).get("night_tags") or {}).get(row["date"]) or []
    return _time_caps.minutes_cap(row["date"], "dinner", tags, memory)


def enforce_minutes_caps(plan_id: int, intake: dict | None, memory: dict | None, *,
                         budget=None, picker=None) -> dict:
    """
    Make "short on time" true of the week rather than merely asked for.
    Re-arranges first (free), then re-picks what no night can take. Never
    raises: a dinner that is too long is worth a repair, never a lost week.

    Returns {"moved": [...], "repicked": [...], "left": [...],
    "flagged": [...]} — `left` being every meal still over its cap after
    both stages, each with why this pass could not touch it, which is what
    plan_quality then warns about; `flagged` the subset the household is
    told about on the draft, which is the ones they asked for by name
    (dinners only — draft_flags reads dinners). Every `repicked` and `left`
    record carries its `slot`: lunch and breakfast are re-picked after the
    dinners, re-pick only (see "LUNCH AND BREAKFAST" in the module
    docstring).
    """
    from . import allergen_gate as _allergen_gate

    # ONE budget for every slot, so lunch spends what the dinners left
    # rather than a fresh six of its own.
    budget = budget or _allergen_gate.CallBudget()
    moved: list[dict] = []
    repicked: list[dict] = []
    try:
        moved = rearrange(plan_id, intake, memory)
    except Exception:
        logger.exception("Plan %s: re-arranging the week around its time caps failed", plan_id)
    try:
        repicked = repick(plan_id, intake, memory, budget=budget, picker=picker)
    except Exception:
        logger.exception("Plan %s: re-picking a dinner over its time cap failed", plan_id)
    for slot in REPICK_ONLY_SLOTS:
        try:
            repicked += repick(plan_id, intake, memory, budget=budget, picker=picker, slot=slot)
        except Exception:
            logger.exception("Plan %s: re-picking a %s over its time cap failed", plan_id, slot)
    left = []
    flagged: list[dict] = []
    read_back = []
    for slot in ("dinner",) + REPICK_ONLY_SLOTS:
        try:
            rows = nights(plan_id, intake, memory, slot)
            if slot == "dinner":
                read_back = rows
            for night in rows:
                if not night["over"]:
                    continue
                why = "nothing quicker came back"
                if not night["movable"]:
                    if night["reheat"] or night["source"]:
                        why = "it is a batch, not a cook on the day"
                    elif _meal_variety.theirs(night["derived"]):
                        why = "the household asked for this one by name"
                    else:
                        why = "it is already cooked"
                left.append({"date": night["date"], "slot": slot, "meal": night["meal"],
                             "minutes": night["minutes"], "cap": night["cap"], "why": why})
        except Exception:
            logger.exception("Plan %s: reading back the week's %s time caps failed", plan_id, slot)
    # The one night this pass deliberately leaves alone AND the household
    # will see — a dish they asked for by name on a night that hasn't the
    # time for it (Emily's scenario 3, 2026-09-23). Built from the SAME
    # read-back as `left` above, so the flag the draft shows and the warn
    # the morning report carries can never disagree about which nights
    # were kept — but in a try of its OWN, so a failure writing the flags
    # can never swallow that warning. See tools/draft_flags.py.
    try:
        flagged = _draft_flags.flags_for_kept_over_cap(read_back) + _draft_flags.flags_for_late_requests(plan_id)
        _draft_flags.record(plan_id, flagged)
    except Exception:
        logger.exception("Plan %s: recording the draft's time-cap flags failed", plan_id)
        flagged = []
    if left:
        logger.warning(
            "Plan %s still has %d meal(s) over their time cap after re-arranging and re-picking: %s",
            plan_id, len(left),
            "; ".join(f"{x['date']} {x['slot']} {x['meal']!r} {x['minutes']}min > {x['cap']}min ({x['why']})"
                      for x in left),
        )
    return {"moved": moved, "repicked": repicked, "left": left, "flagged": flagged}
