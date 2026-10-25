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
(Since 2026-10-18 cap_gate holds only what POMONA picks: a dish the
household chose themselves goes on with a line instead —
swap_in_place.over_cap_said.)

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

  THE LUNCH CAP APPLIES ALWAYS (Emily, 2026-10-03). The 2026-10-02 build
  re-picked only a lunch the household answered "cooked that day" in step
  3 and only recorded an unanswered one. Emily's call: the time limit holds
  whether or not the question was answered, so every weekday lunch over
  time_caps' cap is re-picked; the carve-outs (a chain, a prep day unless
  answered "cooked", the weekend, a meal they asked for, a meal already
  cooked) are unchanged and still the rule's. The 24 tests that went red
  when this was first tried pinned the old rule, not something real: their
  model-drafted 25-30 minute weekday lunches were over a cap nobody had
  answered, and the fixtures now say what those tests are about (see the
  card "A weekday lunch over the lunch time limit gets swapped…"). The
  budget hold (_held_for_later_passes) is what stops many long lunches
  starving an open dinner or the allergen sweep; what it cannot fix is
  left as drafted with "nothing quicker came back" in `left`, and
  plan_quality goes on warning about it.

  TWO THINGS WORTH KNOWING, found by review. (1) A lunch cap never
  reaches a dinner (2026-10-03): a whole-dish LUNCH re-pick
  (_whole_dish_nights, when the household's lunch number is met) spans
  the dish's other lunches only, and a dinner's whole-dish re-pick is
  never tightened by a same-dish lunch's cap (_cook_cap). A lunch that
  rides along on a dinner's pick and lands over its cap is re-picked by
  the lunch stage on its own. (2) The pass and plan_quality
  do not quite agree on a prepped-lunch batch: `nights` reads its markers
  (prep_date / prep_day_cook / constraint) as a batch with no cap, while
  plan_quality's `fed` check compares "date:lunch" against "entry_id:N"
  links and so never matches — the tripwire's own gap, left for its card.

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
import re

from . import time_caps as _time_caps
from . import weekly_plan as _weekly_plan
from . import meal_variety as _meal_variety
from . import leftovers as _leftovers
from . import draft_flags as _draft_flags
from . import dinner_gaps as _dinner_gaps
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


# ---------- a dish they asked for that fits no night (Emily's rule 5) ----------
#
# "A requested dish that fits no night goes on the night with the most
# time, and Pomona says so. It's never dropped silently." (2026-09-23.)
# Before this, such a dish stayed wherever the MODEL had put it — `theirs`
# makes it untouchable to both stages below — and the draft said
# scenario 3's "Braise takes 90 minutes, and Wednesday is short on time."
# about a Wednesday nobody chose, with no move to offer (no night fits).
# Telling the generator is not the same as preventing it, so this is code.
#
# What is stamped on the row: that it was placed by this rule, and the
# night it was on before. The draft's line is still decided off the live
# week (draft_flags.flags_for_kept_over_cap); the stamp only answers the
# one question the week can't — did their words name a day?
FITS_NOWHERE_KEY = "fits_nowhere"

# A day their words named: full names, every short form a person types
# ("Wed", "thu", "Tues"), and the words that name a day without a weekday
# in them. A short form directly followed by a hyphen is not a day —
# "sun-dried" — though a range ("Mon-Thu") still names its far end. "sat"
# the verb and "sun" the noun standing alone are rare in a dinner request
# and are ACCEPTED as days, deliberately: when unsure, a named day
# outranks a time limit, so the dish stays where they put it (scenario 3)
# rather than being moved.
_NAMED_DAY_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun"
    r"|today|tonight|tomorrow|weekend|weekends)\b(?!-)",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"[.;!?\n]+")
# One request inside a sentence: "a long braise sometime, and tacos on
# Friday" is two, and Friday belongs to the tacos.
_CLAUSE_SPLIT_RE = re.compile(r"[,;:\n]+|\band\b|\bbut\b|\bplus\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z]+")
_NOT_A_NAME_WORD = {"with", "and", "the", "for", "from", "into", "over", "style"}


def _stem(word: str) -> str:
    word = word.lower()
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _name_stems(meal: str | None) -> set[str]:
    return {_stem(w) for w in _WORD_RE.findall((meal or "").lower())
            if len(w) >= 3 and w not in _NOT_A_NAME_WORD}


def _outside_the_dish(clause: str, stems: set[str]) -> str:
    """The clause with the dish's own name taken out — "Sunday Roast" and
    "Taco Tuesday" are dishes, not days. Each name word is taken out ONCE,
    so "Sunday roast on Sunday" still names Sunday."""
    # Hyphenated words stay whole, so "sun-dried" never loses the hyphen
    # that tells _NAMED_DAY_RE it is not a day.
    words = re.findall(r"[a-z]+(?:-[a-z]+)*", clause.lower())
    for stem in stems:
        for i, w in enumerate(words):
            if _stem(w) == stem:
                del words[i]
                break
    return " ".join(words)


def _names_a_day_outside_the_dish(clause: str, stems: set[str]) -> bool:
    return bool(_NAMED_DAY_RE.search(_outside_the_dish(clause, stems)))


def named_a_day(derived: dict, asks: tuple[str | None, ...] = (), meal: str | None = None) -> bool:
    """
    Did the household's own words for THIS dish name a day? Read off the
    span the model cited (derived_from.freeform) AND every sentence of
    their words that span came from — the model often quotes half a
    sentence ("a braise" out of "a braise Wed please"), and the half it
    dropped is the half that matters here. A dish moved onto a day their
    words named (`moved_for`, typed_requests.place_day_requests) always
    named one.

    Only the CLAUSE about this dish counts (review, 2026-10-25): "a long
    braise sometime, and tacos on Friday" names Friday for the tacos, not
    the braise. A clause is about the dish when it shares a word with the
    dish's name; when no clause does, every clause counts — unsure leans
    to "a day was named", which leaves the dish where they put it.
    """
    derived = derived if isinstance(derived, dict) else {}
    if derived.get("moved_for"):
        return True
    clauses, stems = _clauses_about(derived, asks, meal)
    return any(_names_a_day_outside_the_dish(c, stems) for c in clauses)


def _clauses_about(derived: dict, asks: tuple[str | None, ...], meal: str | None,
                   strict: bool = False) -> tuple[list[str], set[str]]:
    """The clauses of their words about this dish (see named_a_day), and
    the dish's name stems. `strict` drops the lean-to-"named" fallback:
    rule 5 leaves a dish alone when unsure, but rule 8 MOVES one and says
    "you asked for it Friday", so it needs the day in a clause about the
    dish or in the model's own span ("Something mexican please, we're out
    Friday" names no day for the tacos — review, 2026-10-25)."""
    span = str(derived.get("freeform") or "").strip()
    from . import draft_opener as _draft_opener  # lazy: it reaches back through meal_variety
    texts = [span] + [
        sentence for text in asks or () for sentence in _SENTENCE_SPLIT_RE.split(text or "")
        if sentence.strip() and _draft_opener._cited(sentence, span)
    ]
    clauses = [c for t in texts for c in _CLAUSE_SPLIT_RE.split(t) if c and c.strip()]
    stems = _name_stems(meal)
    mine = [c for c in clauses if stems & {_stem(w) for w in _WORD_RE.findall(c.lower())}]
    if strict:
        # Positive evidence only: a clause that names the dish, or the span
        # the model cited for it. No fallback to every clause.
        return mine + [c for c in _CLAUSE_SPLIT_RE.split(span) if c and c.strip() and c not in mine], stems
    return (mine or clauses), stems


# A weekday their words named, Monday = 0 — every form _NAMED_DAY_RE takes.
_WEEKDAY_OF_WORD = {
    **{w: 0 for w in ("monday", "mon")}, **{w: 1 for w in ("tuesday", "tue", "tues")},
    **{w: 2 for w in ("wednesday", "wed", "weds")}, **{w: 3 for w in ("thursday", "thu", "thur", "thurs")},
    **{w: 4 for w in ("friday", "fri")}, **{w: 5 for w in ("saturday", "sat")},
    **{w: 6 for w in ("sunday", "sun")},
}


def weekdays_named(derived: dict, asks: tuple[str | None, ...] = (), meal: str | None = None) -> set[int]:
    """The weekdays (Monday = 0) their words for THIS dish named — the
    clauses named_a_day reads, strictly (see _clauses_about), with the
    dish's own name taken out. "Today",
    "tomorrow" and "weekend" name no single weekday and are not here."""
    derived = derived if isinstance(derived, dict) else {}
    clauses, stems = _clauses_about(derived, asks, meal, strict=True)
    return {
        _WEEKDAY_OF_WORD[m.group(1).lower()]
        for c in clauses for m in _NAMED_DAY_RE.finditer(_outside_the_dish(c, stems))
        if m.group(1).lower() in _WEEKDAY_OF_WORD
    }


def longest_nights(night: dict, rows: list[dict]) -> list[str] | None:
    """
    The week's longest nights, earliest first, when this dish fits NONE of
    its nights; None when it fits one (or its minutes are unknown).

    "The week's nights" are the dinners someone is home for that are still
    to come: a `planned_empty` night is not a night (CLAUDE.md, "a meal
    slot is never absent"), and a night already gone is not somewhere a
    dinner can go. An `open` night counts — it is a night they're home —
    so a longer open night makes "your longest night is Sunday" false
    rather than quietly skipped; place_fits_nowhere can't reach it and
    leaves the dish where it is. The dish's own night always counts. An
    uncapped night has all the time there is, so the dish fits it.
    """
    minutes = night.get("minutes")
    if not minutes:
        return None
    caps: dict[str, int] = {}
    for row in rows:
        if row.get("slot_state") == "planned_empty":
            continue
        if row["date"] != night["date"] and _weekly_plan.night_has_gone(row["date"]):
            continue
        cap = row.get("cap")
        if not cap or minutes <= cap:
            return None
        caps[row["date"]] = cap
    if not caps:
        return None
    most = max(caps.values())
    return sorted(d for d, cap in caps.items() if cap == most)


def fits_nowhere_candidate(night: dict, asks: tuple[str | None, ...] = ()) -> bool:
    """A dinner they TYPED (rule 5 is about a dish they asked for, not one
    brought over or a frozen portion), naming no day, still to cook, and
    not either end of a chain (see the module docstring on chains)."""
    derived = night.get("derived") or {}
    return (
        night.get("slot_state") == "planned"
        and bool((night.get("meal") or "").strip())
        and (night.get("cooked_status") or "") != "done"
        and bool(str(derived.get("freeform") or "").strip())
        and not night.get("reheat") and not night.get("source")
        and not named_a_day(derived, asks, night.get("meal"))
    )


def _stamp(entry_id: int, key: str, value) -> None:
    """One key onto one row's derived_from, in its own connection — after
    any swap's transaction has committed (see _note_move)."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
            (entry_id, household_id()),
        ).fetchone()
        if row is None:
            return
        try:
            derived = json.loads(row["derived_from_json"] or "{}") or {}
        except (TypeError, ValueError):
            derived = {}
        derived[key] = value
        conn.execute(
            "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
            (json.dumps(derived), entry_id, household_id()),
        )
        conn.commit()
    finally:
        conn.close()


def place_fits_nowhere(plan_id: int, intake: dict | None, memory: dict | None,
                       asks: tuple[str | None, ...] = ()) -> list[dict]:
    """
    Rule 5: put each dish they asked for, naming no day, that fits none of
    the week's nights on the longest one — by TRADING it with that night's
    dinner through swap_dinner_nights, the write stage 1 uses (rows
    re-dated in place, so groceries, sides and defrosts ride along and the
    week's dish count is unchanged). Runs BEFORE stage 1, so the dish that
    comes back to the short night is the app's to trade on or re-pick.

    THE TIE-BREAK, and why: when the dish is already on one of the longest
    nights it stays — nothing needs to move, and the model's placement is
    as good as any equal night. Otherwise it goes to the EARLIEST of the
    tied longest nights: deterministic, so a test can name the answer, and
    of two equal nights the earlier is nearer the shop, so a long cook
    gets the freshest ingredients.

    A longest night it cannot reach — someone there has said no to the
    dish, that night's dinner is theirs, cooked or half of a chain, it is
    an open night, or the swap refuses — is skipped for the next tied one.
    When none is reachable the dish stays where it was, UNSTAMPED, and the
    draft says scenario 3's line instead, which is true of where it is.
    Allergens cannot change under a move (module docstring). Never raises.
    """
    placed: list[dict] = []
    done: set[int] = set()
    for _ in range(_MAX_TRADES):
        try:
            rows = nights(plan_id, intake, memory)
        except Exception:
            logger.exception("Plan %s: reading the week for a dish that fits no night failed", plan_id)
            break
        night, longest = None, None
        for row in rows:
            if row["id"] in done or not fits_nowhere_candidate(row, asks):
                continue
            longest = longest_nights(row, rows)
            if longest:
                night = row
                break
        if night is None:
            break
        done.add(night["id"])
        try:
            if night["date"] in longest:
                _stamp(night["id"], FITS_NOWHERE_KEY, {"from": night["date"]})
                placed.append({"meal": night["meal"], "from": night["date"], "to": night["date"]})
                continue
            by_date = {r["date"]: r for r in rows}
            for target in longest:
                there = by_date.get(target)
                if there is None or not there["movable"]:
                    continue
                offends = would_offend(night["meal"], target) or would_offend(there["meal"], night["date"])
                if offends:
                    logger.info("Plan %s: not putting %s on %s — %s", plan_id, night["meal"], target, offends)
                    continue
                try:
                    result = _weekly_plan.swap_dinner_nights(plan_id, night["date"], target)
                except ValueError as exc:
                    logger.info("Plan %s: %s and %s would not trade — %s", plan_id, night["date"], target, exc)
                    continue
                if result.get("status") != "swapped":
                    logger.info("Plan %s: %s and %s would not trade — %s",
                                plan_id, night["date"], target, result.get("message") or "refused")
                    continue
                _note_move(result.get("moved") or [], {r["date"]: r["cap"] for r in rows})
                _stamp(night["id"], FITS_NOWHERE_KEY, {"from": night["date"]})
                placed.append({"meal": night["meal"], "from": night["date"], "to": target})
                logger.info("Plan %s: %s (%s min) fits no night; moved %s -> %s, the longest",
                            plan_id, night["meal"], night["minutes"], night["date"], target)
                break
        except Exception:
            logger.exception("Plan %s: putting %s on its longest night failed", plan_id, night["meal"])
    return placed


# ---------- a dish they asked for on a night nobody is home (Emily's rule 8) ----------
#
# "A request on a day out or away moves to the nearest night that fits the
# time rules, always flagged." (2026-09-23.) Who's home outranks the day
# they named, and the day they named outranks a time limit — so the dish
# leaves the night they're out, and the night it goes to is the nearest one
# whose time it fits. Before this the prompt asked the model to move it and
# nothing checked: a model that left it on the out night lost it (the
# out-night pass writes planned_empty over whatever is there — the tag
# wins), and one that moved it put it wherever it liked, with nothing said.
#
# The stamp on the row is what the draft's line reads
# (draft_flags.flags_for_days_out): the night it was asked for, whether
# that night is out or away, and whether their words named it — a dish the
# MODEL happened to put on an out night is rescued the same way, but the
# household never asked for that night, so "You're out Friday, so …" would
# be about nothing they said.
DAY_OUT_KEY = "day_out"


def requests_on_nights_out(plan_id: int, nobody_home: dict[str, bool]) -> list[dict]:
    """
    The dinners they asked for (their typed words cite it) that the model
    put on a night nobody is home — read BEFORE the out-night and away
    passes clear those nights, because after them the dish is gone.
    `nobody_home` is {date: away?} for every such night. Never raises.
    """
    if not nobody_home:
        return []
    try:
        conn = get_conn()
        rows = conn.execute(
            """SELECT mpe.date, mpe.slot_state, mpe.reasoning, mpe.food_groups_json, mpe.derived_from_json,
                      COALESCE(r.name, mpe.freeform_meal) AS meal
               FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
               WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.slot = 'dinner'
                 AND mpe.component_category IS NULL ORDER BY mpe.date, mpe.id""",
            (plan_id, household_id()),
        ).fetchall()
        conn.close()
    except Exception:
        logger.exception("Plan %s: reading the dinners on nights nobody is home failed", plan_id)
        return []
    out = []
    for r in rows:
        if r["date"] not in nobody_home or r["slot_state"] != "planned" or not (r["meal"] or "").strip():
            continue
        try:
            derived = json.loads(r["derived_from_json"] or "{}") or {}
            groups = json.loads(r["food_groups_json"] or "[]") or None
        except (TypeError, ValueError):
            continue
        if str(derived.get("freeform") or "").strip():
            out.append({"date": r["date"], "meal": r["meal"], "derived": derived,
                        "food_groups": groups, "reasoning": r["reasoning"] or ""})
    return out


def _day_out_target(meal: str, minutes: int | None, from_date: str, rows: list[dict],
                    here: dict | None = None) -> tuple[str | None, bool]:
    """
    The night a dish asked for on `from_date` goes to, and whether it fits
    none (then it is the longest night, rule 5's answer, and the second
    value is True).

    NEAREST by days from the night they asked for; A TIE GOES TO THE LATER
    NIGHT — draft_flags.move_target's own tie-break, and Emily's example
    (out Friday: Thursday and Saturday are both one night away, and her
    line says Saturday). A night it can go to is one someone is home for
    and still to come, holding a dinner Pomona chose (never theirs, a
    reheat or either end of a chain, never a night tagged Leftovers), that
    nobody eating there has said no to. `here` is the night the dish is
    already on, which counts as one it can stay on.

    When it fits none of them, Who's home and the day they named are
    already spent, and what is left is a requested dish that fits no night
    — rule 5: the longest night, nearest of a tie. Never "the nearest home
    night, short on time": a time limit outranks an unnamed request, and
    once the day they named is gone that is what this is.
    """
    def distance(row: dict) -> tuple[int, int]:
        gap = (datetime.date.fromisoformat(row["date"]) - datetime.date.fromisoformat(from_date)).days
        return (abs(gap), 0 if gap > 0 else 1)

    open_to = []
    for row in rows:
        if here is not None and row["id"] == here["id"]:
            open_to.append(row)
            continue
        if (row["slot_state"] != "planned" or not (row.get("meal") or "").strip()
                or row["date"] == from_date or (row.get("cooked_status") or "") == "done"
                or _meal_variety.theirs(row.get("derived") or {}) or row.get("reheat") or row.get("source")
                or "left" in (row.get("tags") or []) or _weekly_plan.night_has_gone(row["date"])):
            continue
        if would_offend(meal, row["date"]):
            continue
        if here is not None and would_offend(row["meal"], here["date"]):
            continue  # a trade puts theirs on the night ours leaves
        open_to.append(row)
    if not open_to:
        return None, False
    fits = [r for r in open_to if not r.get("cap") or not minutes or minutes <= r["cap"]]
    if fits:
        return min(fits, key=distance)["date"], False
    most = max(r["cap"] for r in open_to)
    return min((r for r in open_to if r["cap"] == most), key=distance)["date"], True


def place_off_nights_out(plan_id: int, intake: dict | None, memory: dict | None,
                         nobody_home: dict[str, bool], rescued: list[dict],
                         asks: tuple[str | None, ...] = (), report: dict | None = None) -> list[dict]:
    """
    Rule 8, after the out, away, holiday, brought-over and freezer passes
    have settled which nights nobody is home (every one of them is now a
    planned_empty row, so "a night it can go to" is read off the week
    itself):

      * each dish in `rescued` (requests_on_nights_out, read before those
        passes cleared it) goes on its night (_day_out_target) in place of
        the dinner Pomona chose there — through _replace_slot_entries,
        which takes the old dinner's groceries off and the prep rows with
        it. Skipped when the same dish is on the week anyway: the second
        bullet then speaks for it.
      * a dish they asked for on a named night nobody is home that the
        MODEL already moved is put on that same nearest night too — traded
        through swap_dinner_nights, the write stage 1 uses — or stays
        where it is when that is the nearest.

    Each is stamped (DAY_OUT_KEY) for the draft's line, and its words go
    in report["moved_requests"] with no line of its own, as a late move's
    do: the flag says it, so line one does not ("Pizza Friday Saturday, as
    you asked."). A dish nowhere could take is left where it was, and
    logged. Never raises.
    """
    placed: list[dict] = []
    if not nobody_home:
        return placed

    def _said(derived: dict, meal: str, from_date: str) -> bool:
        if derived.get("moved_for"):
            return True  # "tomorrow" (typed_requests.place_day_requests): a day they named
        return datetime.date.fromisoformat(from_date).weekday() in weekdays_named(derived, asks, meal)

    def _record(derived: dict, meal: str, from_date: str, to_date: str, said: bool) -> None:
        placed.append({"meal": meal, "from": from_date, "to": to_date, "said": said})
        if said and report is not None:
            report.setdefault("moved_requests", []).append(
                {"words": str(derived.get("freeform") or "").strip(), "line": ""})
        logger.info("Plan %s: %s was asked for on %s, when nobody is home; now on %s",
                    plan_id, meal, from_date, to_date)

    for dish in rescued or []:
        try:
            rows = nights(plan_id, intake, memory)
            if any((r.get("meal") or "").strip().lower() == dish["meal"].strip().lower()
                   and r["slot_state"] == "planned" for r in rows):
                continue
            minutes = _recipe_minutes(dish["meal"])
            target, nowhere = _day_out_target(dish["meal"], minutes, dish["date"], rows)
            if target is None:
                logger.info("Plan %s: nowhere to put %s off %s", plan_id, dish["meal"], dish["date"])
                continue
            said = _said(dish["derived"], dish["meal"], dish["date"])
            derived = {k: v for k, v in dish["derived"].items()
                       if k not in ("links_to", "make_double_for", "constraint", "tags")}
            derived[DAY_OUT_KEY] = {"from": dish["date"], "away": nobody_home[dish["date"]], "said": said}
            if nowhere:
                derived[FITS_NOWHERE_KEY] = {"from": dish["date"]}
            _weekly_plan._replace_slot_entries(
                plan_id, [r["id"] for r in rows if r["date"] == target], target, "dinner", dish["meal"],
                food_groups=dish["food_groups"], reasoning=dish["reasoning"], derived_from=derived,
            )
            _record(dish["derived"], dish["meal"], dish["date"], target, said)
        except Exception:
            logger.exception("Plan %s: moving %s off a night nobody is home failed", plan_id, dish.get("meal"))

    try:
        ids = [r["id"] for r in nights(plan_id, intake, memory)]
    except Exception:
        logger.exception("Plan %s: reading the week for requests on nights out failed", plan_id)
        return placed
    for entry_id in ids:
        try:
            # Read afresh each time: a trade made for an earlier dish
            # re-dated rows.
            rows = nights(plan_id, intake, memory)
            night = next((r for r in rows if r["id"] == entry_id), None)
            derived = (night or {}).get("derived") or {}
            if (night is None or night["slot_state"] != "planned" or DAY_OUT_KEY in derived
                    or not str(derived.get("freeform") or "").strip()
                    or (night.get("cooked_status") or "") == "done" or night.get("reheat") or night.get("source")):
                continue
            named = weekdays_named(derived, asks, night["meal"])
            if datetime.date.fromisoformat(night["date"]).weekday() in named:
                continue  # they asked for this night too
            asked_on = sorted(d for d in nobody_home if datetime.date.fromisoformat(d).weekday() in named)
            if not asked_on:
                continue
            from_date = asked_on[0]
            target, nowhere = _day_out_target(night["meal"], night.get("minutes"), from_date, rows, here=night)
            to_date = night["date"]
            if target and target != night["date"]:
                result = _weekly_plan.swap_dinner_nights(plan_id, night["date"], target)
                if result.get("status") == "swapped":
                    to_date = target
                else:
                    logger.info("Plan %s: %s and %s would not trade — %s",
                                plan_id, night["date"], target, result.get("message") or "refused")
            _stamp(entry_id, DAY_OUT_KEY, {"from": from_date, "away": nobody_home[from_date], "said": True})
            if nowhere and to_date == target:
                _stamp(entry_id, FITS_NOWHERE_KEY, {"from": night["date"]})
            _record(derived, night["meal"], from_date, to_date, True)
        except ValueError as exc:
            logger.info("Plan %s: could not move entry %s off a night out — %s", plan_id, entry_id, exc)
        except Exception:
            logger.exception("Plan %s: placing entry %s off a night out failed", plan_id, entry_id)
    return placed


# ---------- a dish they asked for at a meal, naming no day (Emily's rule 2) ----------
#
# "A request with a meal but no day goes at that meal on a day with enough
# time and everyone home. Dishes over 30 minutes stay off short-on-time
# nights." (2026-09-23; her example, "Tacos for dinner once this week".)
# Before this the model was told and nothing checked: the tacos stayed
# wherever it put them — `theirs` makes a typed dish untouchable to both
# stages below — so a night Vineeth is out got them, and a rush night got
# them with scenario 3's "…Wednesday is short on time." about a night
# nobody chose.
#
# No line was decided for this rule (rule 1's "tells you where it went" is
# for a request with no meal either), so it is a SILENT repair: the dish
# trades places with a dish Pomona chose, through meal_move.move_meal —
# the Move sheet's own write, the one door that trades a lunch as well as
# a dinner (rows re-dated in place, so groceries, sides and thaws ride
# along; a veto at the new table refuses it). The meal they named is kept
# by construction: a lunch only ever trades with a lunch.
#
# "Everyone home" is slot_attendance, per person per meal — the day sheet's
# toggles, a trip, the usual-week grid — read as "nobody from the household
# is marked out of it". Guests don't make a meal less everyone's.
MEAL_NO_DAY_KEY = "meal_no_day"


def meals_named(derived: dict, asks: tuple[str | None, ...] = (), meal: str | None = None) -> set[str]:
    """The meals ("dinner", "lunch", "breakfast") their words for THIS dish
    named — the clauses rule 8 reads (strict), the dish's own name taken
    out, so a "Breakfast Burrito" is not a breakfast request."""
    from . import week_intake as _week_intake  # lazy: it reaches the whole app
    clauses, stems = _clauses_about(derived if isinstance(derived, dict) else {}, asks, meal, strict=True)
    return {
        _week_intake._MEAL_OF_WORD[m.group(1).lower()]
        for c in clauses for m in _week_intake._MEAL_WORD_RE.finditer(_outside_the_dish(c, stems))
    }


def _someone_out(meal_date: str, slot: str) -> bool:
    from . import attendance as _attendance  # lazy: it imports weekly_plan
    try:
        return bool(_attendance.get_slot_attendance(meal_date, slot).get("absent_member_ids"))
    except Exception:
        logger.exception("Reading who is at %s %s failed", meal_date, slot)
        return False


def place_meal_requests(plan_id: int, intake: dict | None, memory: dict | None,
                        asks: tuple[str | None, ...] = ()) -> list[dict]:
    """
    Rule 2: each dish they typed for a meal, naming no day, that sits on a
    meal someone is out of or hasn't the time for, goes to the nearest meal
    of that kind with everyone home and enough time. Runs BEFORE rule 5 and
    stage 1, so the dish it trades back onto the bad night is the app's to
    trade on or re-pick.

    The order is the card's: who's home, then time. When no meal has both,
    a dish OVER its night's time goes to the nearest meal it fits (time
    beats a partial table — her second sentence is absolute); a dish that
    fits where it is stays; and one that fits no meal at all is left for
    rule 5 (the longest night, and its line), which runs next. Nearest by
    days, a tie to the LATER day (move_target's rule, and rule 8's).

    A meal it can go to holds a dish Pomona chose (never theirs, a reheat or
    either end of a chain, never cooked, gone by, planned_empty or open,
    never a dinner tagged Leftovers). Never raises.
    """
    placed: list[dict] = []
    done: set[int] = set()
    for slot in ("dinner", "lunch"):  # meal_move.MOVABLE_SLOTS: breakfast has no move door
        for _ in range(_MAX_TRADES):
            try:
                rows = nights(plan_id, intake, memory, slot)
            except Exception:
                logger.exception("Plan %s: reading the week's %ss for a meal request failed", plan_id, slot)
                break
            out = {r["date"]: _someone_out(r["date"], slot) for r in rows}
            night = None
            for row in rows:
                derived = row.get("derived") or {}
                if (row["id"] in done or not fits_nowhere_candidate(row, asks)
                        or DAY_OUT_KEY in derived or FITS_NOWHERE_KEY in derived
                        or _weekly_plan.night_has_gone(row["date"])
                        or slot not in meals_named(derived, asks, row["meal"])):
                    continue
                if out[row["date"]] or row["over"]:
                    night = row
                    break
            if night is None:
                break
            done.add(night["id"])
            minutes = night["minutes"]

            def fits(r: dict) -> bool:
                return not r.get("cap") or not minutes or minutes <= r["cap"]

            def distance(r: dict) -> tuple[int, int]:
                gap = (datetime.date.fromisoformat(r["date"]) - datetime.date.fromisoformat(night["date"])).days
                return (abs(gap), 0 if gap > 0 else 1)

            open_to = [
                r for r in rows
                if r["id"] != night["id"] and r["slot_state"] == "planned" and (r.get("meal") or "").strip()
                and (r.get("cooked_status") or "") != "done" and not _meal_variety.theirs(r.get("derived") or {})
                and not r.get("reheat") and not r.get("source")
                and not (slot == "dinner" and "left" in (r.get("tags") or []))
                and not _weekly_plan.night_has_gone(r["date"]) and fits(r)
            ]
            best = sorted((r for r in open_to if not out[r["date"]]), key=distance)
            if not best and night["over"]:
                best = sorted(open_to, key=distance)
            try:
                from . import meal_move as _meal_move  # lazy: it imports weekly_plan and tonight
                for target in best:
                    result = _meal_move.move_meal(plan_id, night["id"], target["date"])
                    if result.get("status") != "moved":
                        logger.info("Plan %s: %s would not move to %s — %s", plan_id, night["meal"],
                                    target["date"], result.get("message") or "refused")
                        continue
                    _stamp(night["id"], MEAL_NO_DAY_KEY, {"from": night["date"]})
                    placed.append({"meal": night["meal"], "slot": slot, "from": night["date"], "to": target["date"]})
                    logger.info("Plan %s: %s was asked for at %s with no day; moved %s -> %s", plan_id,
                                night["meal"], slot, night["date"], target["date"])
                    break
            except Exception:
                logger.exception("Plan %s: placing %s at %s failed", plan_id, night["meal"], slot)
    return placed


def _recipe_minutes(meal: str) -> int | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT prep_time_minutes, cook_time_minutes FROM recipes WHERE household_id = ? "
            "AND lower(name) = lower(?) ORDER BY id DESC LIMIT 1",
            (household_id(), meal),
        ).fetchone()
    finally:
        conn.close()
    return _minutes(dict(row)) if row else None


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
           budget=None, picker=None, slot: str = "dinner",
           asks: tuple[str | None, ...] = (), stood: dict | None = None) -> list[dict]:
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
        (n for n in rows if n["over"] and n["movable"] and not _typed_for(n, asks, slot)),
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
            if stood is not None:
                stood[night["id"]] = "re-picking it alone would break the household's dish count or a batch"
            logger.info("Plan %s: %s %s %r stays over its cap — re-picking it alone would add a dish "
                        "past the household's number", plan_id, night["date"], slot, night["meal"])
            continue
        if also:
            caps_on = [cap] + [c for c in (_cook_cap(o, intake, memory, slot) for o in also) if c]
            cap = min(caps_on)  # the pick is cooked on each of them

        # A cuisine chip this dish is the week's only answer to keeps its
        # cuisine through the re-pick (adversarial review, 2026-10-03: the
        # chip pass had already counted a 25-minute Pad Thai lunch as the
        # Thai chip's answer, then this pass swapped it for anything quick
        # and the week lost its Thai dish with nothing said).
        chips = _sole_chips(plan_id, intake, {night["id"]} | {o["id"] for o in also or []})

        def _too_long(candidate, cap=cap, night=night, chips=chips):
            minutes = _swap._pick_minutes(candidate)
            if minutes and minutes > cap:
                return f"takes {minutes} minutes, and {_weekday(night['date'])} only has {cap}"
            from . import typed_requests as _typed_requests
            for chip in chips:
                why = _typed_requests._pick_is_cuisine(candidate, chip)
                if why:
                    return why
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
            context_extra={"must_be_cuisine": chips[0]} if chips else None,
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


def _typed_for(night: dict, asks: tuple[str | None, ...], slot: str) -> bool:
    """Whether the household's own words for this week name this LUNCH (or
    breakfast) — meal_variety.asked_for_by_name, the no-repeat pass's
    reading. The model is asked to stamp `freeform` on a meal a request
    shaped (`theirs`), and a meal they typed that it forgot to stamp is
    still one they asked for by name (Emily's exception, 2026-10-03; found
    by review).

    Never for a DINNER (second review, 2026-10-03): asked_for_by_name errs
    toward keeping ("less pasta this week" names every pasta), and on a
    dinner that would leave a 60-minute dish on a rush night that main
    fixed — with no draft flag, since draft_flags only flags `theirs`.
    Dinner keeps main's rule; this card is about lunch."""
    if slot == "dinner" or not asks:
        return False
    # The WHOLE name, not asked_for_by_name's last-word fallback (third
    # review: "less pasta this week" kept a 45-minute Creamy Tomato Pasta
    # lunch). A cap is a promise about the day; only their own naming of
    # the dish outranks it.
    key = _meal_variety.repeat_key(night["meal"] or "")
    if not key:
        return False
    return any(re.search(rf"\b{re.escape(key)}\b", _meal_variety.repeat_key(text) or "")
               for text in asks if text)


def _sole_chips(plan_id: int, intake: dict | None, group: set) -> list[str]:
    """This week's cuisine chips (intake.cuisines) whose every planned
    lunch or dinner answering them is in `group` — the meals about to be
    re-picked — so the re-pick must answer them too."""
    from . import typed_requests as _typed_requests
    chips = _typed_requests._unique_chips((intake or {}).get("cuisines"))
    if not chips:
        return []
    entries = [e for e in _typed_requests._load_entries(plan_id)
               if e["slot"] in ("lunch", "dinner") and e["slot_state"] == "planned" and e["meal"]]
    out = []
    for chip in chips:
        answering = {e["id"] for e in entries
                     if _typed_requests.dish_is_cuisine(chip, e["meal"], e.get("cuisine"))}
        if answering and answering <= group:
            out.append(chip)
    return out


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
    # A DINNER's whole dish spans the week's lunches too (a lunch is often a
    # dinner's reheat, and the household's dinner number counts the dish
    # once). A LUNCH's whole dish is lunches only (2026-10-03): the lunch
    # cap applies to every weekday lunch now, and a lunch re-pick that
    # rewrote a dinner of the same dish would be a lunch cap deciding a
    # dinner. The lunch number (meal_variety.distinct_dishes "lunch")
    # counts lunches only, so re-picking the lunches of a dish alone keeps
    # it. Breakfast is its own.
    slots = ("lunch", "dinner") if slot == "dinner" else (slot,)
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
    if slot == "lunch" and others:
        # A lunch's whole dish is lunches only, so a chain that crosses to
        # a dinner must not be half-rewritten (adversarial review,
        # 2026-10-03: Tue's reheat of Monday's dinner was rewritten with
        # Thursday's fresh lunch, and Monday's batch lost its double). A
        # lunch eating a dinner's cook is the dinner's, and the lunch
        # number does not count it (meal_variety.distinct_dishes), so it
        # is simply not part of this dish; a lunch cooking for a dinner
        # cannot be re-picked without that dinner, so the night stands.
        chains = _leftovers.plan_leftover_chains(plan_id)
        dinner_fed = _meal_variety._dinner_fed_lunch_ids(plan_id, chains)
        kept = []
        for o in others:
            derived = json.loads(o["derived_from_json"] or "{}") or {}
            if o["id"] in dinner_fed or str(derived.get("links_to") or "").endswith(":dinner"):
                continue
            source = chains["sources"].get(o["id"])
            feeds = [str(t) for t in (derived.get("make_double_for") or [])]
            if (source and any(t.get("slot") != "lunch" for t in source.get("targets") or [])) \
                    or any(t.endswith(":dinner") for t in feeds):
                return None
            kept.append(o)
        others = kept
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


def _cook_cap(row: dict, intake: dict | None, memory: dict | None,
             slot: str = "dinner") -> int | None:
    """The cap on a meal of the dish being re-picked that is COOKED on its
    day (a reheat has no cap to fit). `slot` is the slot being re-picked.

    A LUNCH's cap never tightens a DINNER re-pick (Emily, 2026-10-03:
    "never let a lunch cap leak into a dinner swap"). The 2026-10-02 build
    held a dinner's whole-dish pick to a same-dish lunch's 20 minutes; the
    third review caught it for an unanswered lunch, and now that every
    weekday lunch is capped it would catch every one. A lunch that rides
    along on a dinner's whole-dish pick and lands over its own cap is the
    lunch stage's to fix, after the dinners, on its own. Within a lunch
    re-pick, a lunch is asked the way `nights` asks it."""
    derived = json.loads(row.get("derived_from_json") or "{}") or {}
    if derived.get("links_to") or _leftovers.frozen_portion_on(derived):
        return None
    if row["slot"] == "lunch":
        if slot != "lunch":
            return None
        from . import weekday_lunches as _weekday_lunches
        kind = _weekday_lunches.kinds_by_date(intake).get(row["date"])
        batch = bool(derived.get("make_double_for") or derived.get("prep_date")
                     or derived.get("prep_day_cook")
                     or derived.get("constraint") == _weekday_lunches.CONSTRAINT)
        return _time_caps.minutes_cap(row["date"], "lunch", [], memory, is_leftovers=batch,
                                      lunch_kind=kind)
    if row["slot"] != "dinner":
        return None
    tags = ((intake or {}).get("night_tags") or {}).get(row["date"]) or []
    return _time_caps.minutes_cap(row["date"], "dinner", tags, memory)


def _held_for_later_passes(plan_id: int, dates: list[str] | None = None) -> int:
    """
    The calls the lunch and breakfast re-picks must leave in the shared
    budget: one dish's worth (swap_in_place.MAX_PICK_ATTEMPTS) for every
    dinner gap dinner_gaps.fill_open_dinners will plan after this — counted
    the way IT counts them (dinner_gaps._dinner_gaps: a date with no dinner
    row, or only an `open` one that does not keep its question), over the
    same `dates` the generation hands it — plus one for the allergen sweep,
    which that fill holds back too. An open dinner is Emily's decision A
    (2026-09-27: never when the household is home); a lunch over 20 minutes
    is the smaller cost.

    Found by adversarial review of this branch (2026-10-02): five over-cap
    lunches spent all six calls and an open Wednesday dinner stayed open.
    The first version counted `slot_state = 'open'` rows and so missed
    exactly that case — generation never WRITES the model's open dinner
    ("left unwritten, so fill_open_dinners plans it below"), so the gap is
    a date with no dinner row. `dates` None (a direct call) reads every
    date the plan has a row on.
    """
    from . import swap_in_place as _swap
    rows = _dinner_gaps._plan_rows(plan_id)
    if dates is None:
        dates = sorted({r["date"] for r in rows})
    gaps = len(_dinner_gaps._dinner_gaps(rows, list(dates)))
    return _swap.MAX_PICK_ATTEMPTS * (1 + gaps)


def enforce_minutes_caps(plan_id: int, intake: dict | None, memory: dict | None, *,
                         budget=None, picker=None, dates: list[str] | None = None,
                         asks: tuple[str | None, ...] = ()) -> dict:
    """
    Make "short on time" true of the week rather than merely asked for.
    Re-arranges first (free), then re-picks what no night can take. Never
    raises: a dinner that is too long is worth a repair, never a lost week.

    Returns {"moved": [...], "repicked": [...], "left": [...],
    "flagged": [...]} — `left` being every meal still over its cap after
    both stages, each with why this pass could not touch it, which is what
    plan_quality then warns about; `flagged` the subset the household is
    told about on the draft, which is the ones they asked for by name
    (dinners only — draft_flags reads dinners). `dates` is the generation's
    own days, so the lunch stage knows how many dinner gaps the fill after
    it must still plan (_held_for_later_passes). Every `repicked` and `left`
    record carries its `slot`: lunch and breakfast are re-picked after the
    dinners, re-pick only (see "LUNCH AND BREAKFAST" in the module
    docstring).
    """
    from . import allergen_gate as _allergen_gate

    # ONE budget for every slot, so lunch spends what the dinners left
    # rather than a fresh six of its own — and only down to what the passes
    # after this one need (_held_for_later_passes).
    budget = budget or _allergen_gate.CallBudget()
    moved: list[dict] = []
    repicked: list[dict] = []
    stood: dict[int, str] = {}  # meals a re-pick never tried, and why
    # Rule 2 first: a dish they asked for at a meal, naming no day, goes to
    # a meal of that kind with everyone home and the time for it. What it
    # cannot place for time is rule 5's, next.
    try:
        moved = [{"from": p["from"], "to": p["to"], "meal_no_day": True, "slot": p["slot"],
                  "moved": [{"meal": p["meal"], "from": p["from"], "to": p["to"]}]}
                 for p in place_meal_requests(plan_id, intake, memory, asks)]
    except Exception:
        logger.exception("Plan %s: placing a dish asked for at a meal failed", plan_id)
    # Rule 5: a dish they asked for that fits no night goes on the
    # longest one, so the dinner it trades onto the short night is there
    # in time for stage 1 and 2 to put right.
    try:
        moved += [{"from": p["from"], "to": p["to"], "fits_nowhere": True,
                   "moved": [{"meal": p["meal"], "from": p["from"], "to": p["to"]}]}
                  for p in place_fits_nowhere(plan_id, intake, memory, asks) if p["from"] != p["to"]]
    except Exception:
        logger.exception("Plan %s: placing a dish that fits no night failed", plan_id)
    try:
        moved += rearrange(plan_id, intake, memory)
    except Exception:
        logger.exception("Plan %s: re-arranging the week around its time caps failed", plan_id)
    try:
        repicked = repick(plan_id, intake, memory, budget=budget, picker=picker, asks=asks, stood=stood)
    except Exception:
        logger.exception("Plan %s: re-picking a dinner over its time cap failed", plan_id)
    try:
        held = _held_for_later_passes(plan_id, dates)
    except Exception:
        # Unknown is held as "the whole budget": a lunch over its cap is the
        # smaller cost than a dinner left open.
        logger.exception("Plan %s: counting the dinners still to plan failed", plan_id)
        held = budget.left
    lunch_budget = _dinner_gaps._Reserved(budget, held)
    for slot in REPICK_ONLY_SLOTS:
        try:
            repicked += repick(plan_id, intake, memory, budget=lunch_budget, picker=picker, slot=slot,
                               asks=asks, stood=stood)
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
                if night["movable"] and _typed_for(night, asks, slot):
                    why = "the household asked for this one by name"
                elif night["id"] in stood:
                    why = stood[night["id"]]
                elif not night["movable"]:
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
        # A move off a night nobody is home first (rule 8): it says why the
        # dish is where it is, which any time line about it then builds on.
        flagged = (_draft_flags.flags_for_days_out(plan_id) + _draft_flags.flags_for_kept_over_cap(read_back)
                   + _draft_flags.flags_for_late_requests(plan_id))
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
