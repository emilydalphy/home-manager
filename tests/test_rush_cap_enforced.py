"""
"Short on time" is held to by CODE, not only asked for.

Emily, 2026-09-26, off her own morning report (30-day window): twelve
`rush_cap_respected` warnings, five distinct dinners on three distinct rush
nights, every one over her 20-minute cap — 35, 32, 29, 28 and 24 minutes.
The cap was told to the model three times in agent.py's prompts, measured
afterwards by plan_quality at severity `warn`, and enforced nowhere in
between, while the swap door has refused an over-cap pick since 2026-09-22
(swap_in_place.cap_gate). One door held the household's constraint and the
other did not.

What is under test is app/tools/cap_enforce.py: re-arrange the week's own
dinners first (free), re-pick what no night can take, and never hand a
night back as a question.

RED AGAINST `origin/main`, MEASURED — AND THE HEADLINE NUMBER IS A PROPERTY
OF THE STUB RATHER THAN OF THIS FILE, so it is measured twice and both are
given. Against origin/main ITSELF the file cannot be COLLECTED: it imports
app.tools.cap_enforce, which is not there, so zero tests run and there is no
count to quote.

  Stub A - the module present but NOT wired into agent.py: 32 red / 1 green,
  and 28 of those 32 die on `AttributeError: agent._cap_enforce`, i.e. in
  this file's own fixture, never reaching an assertion. Recorded because it
  is the flattering number and it is worth nothing.

  Stub B - main's BEHAVIOUR: everything present and wired, with only the two
  enforcement stages no-opped, so the single difference from this branch is
  that nothing enforces the cap. 22 red / 11 green, and ALL 22 fail on the
  assertion they are named for - a named message or a value comparison, with
  no AttributeError and no KeyError anywhere. That is the behaviour evidence.

The 11 green under stub B are the left-alone, nothing-to-do and
characterisation guards: main leaves everything alone too, so green there is
correct rather than reassuring, and each is pinned by one of the 24
mutations in the branch's Decision-log entry. Two of them
(test_the_pass_runs_before_..., test_the_pass_shares_...) are green only
because stub B carries this branch's own agent.py; against real main they
are red on the missing call site.

Every per-test label below (CATCH / NOT WIRED / STUB / CHAR) was written
against stub A, which is why several say "under stub A": they record where
that run died, not what main's behaviour is. Stub B's 22 is the figure to
read.

One of the 22 is red for a reason other than its own name:
test_another_households_over_cap_dinner... fails at its PRECONDITION ("this
household's week was still fixed") rather than on the isolation it is named
for. Pinned by mutation instead.

So the evidence that this works is those 22 behaviour failures under stub B
plus the 24 mutations recorded in the branch's Decision-log entry - never
the raw count against a tree the file cannot even be collected on.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import cap_enforce, plan_quality


CAP = 20          # Emily's own weeknight_max_minutes
RUSH_CAP = 20     # min(RUSH_MAX_MINUTES=30, 20) on a Monday-Friday


def _monday(offset_weeks: int = 1) -> str:
    from conftest import household_today
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _recipe(name: str, minutes: int, **extra) -> str:
    """A dish whose prep+cook is exactly `minutes`."""
    tools.add_recipe(
        name, ingredients=[{"item": f"{name} stuff", "qty": "1", "category": "pantry"}],
        prep_time_minutes=0, cook_time_minutes=minutes,
        food_groups=["protein", "vegetable", "carb"],
        instructions=[f"Cook the {name} stuff.", "Serve it."], **extra,
    )
    return name


def _slot(date: str, slot: str, name: str, **extra) -> dict:
    return {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": False,
            "ingredients": [{"item": f"{name} stuff", "qty": "1"}],
            "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
            **extra}


def _week(dates: list[str], dinners: list[str], **per_date_extra) -> list[dict]:
    """One entry per slot per date; `dinners` is one dish per date."""
    out = []
    for i, d in enumerate(dates):
        out.append(_slot(d, "breakfast", "Oats"))
        out.append(_slot(d, "lunch", "Wrap"))
        out.append(_slot(d, "dinner", dinners[i], **(per_date_extra.get(d) or {})))
        out.append(_slot(d, "snack", "Apple"))
    return out


def _filler():
    _recipe("Oats", 5)
    _recipe("Wrap", 5)
    _recipe("Apple", 0)


def _dinners(plan_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT mpe.id, mpe.date, mpe.slot_state, mpe.reasoning, mpe.derived_from_json,
                  COALESCE(r.name, mpe.freeform_meal) AS meal,
                  r.prep_time_minutes AS p, r.cook_time_minutes AS c
           FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
           WHERE mpe.weekly_plan_id = ? AND mpe.slot = 'dinner' ORDER BY mpe.date, mpe.id""",
        (plan_id,)).fetchall()
    conn.close()
    return [dict(r, minutes=(r["p"] or 0) + (r["c"] or 0)) for r in rows]


def _over_cap(plan_id: int, week: str) -> list[dict]:
    """Every dinner over its own cap, read exactly as plan_quality reads it."""
    memory = tools.get_household_memory()
    tags = (tools.get_week_intake(week) or {}).get("night_tags") or {}
    out = []
    for row in _dinners(plan_id):
        if row["slot_state"] != "planned" or not row["meal"]:
            continue
        cap = tools.minutes_cap(row["date"], "dinner", tags.get(row["date"]) or [], memory)
        if cap and row["minutes"] and row["minutes"] > cap:
            out.append({"date": row["date"], "meal": row["meal"],
                        "minutes": row["minutes"], "cap": cap})
    return out


def _cap_warnings(plan_id: int, week: str) -> list:
    memory = tools.get_household_memory()
    tags = (tools.get_week_intake(week) or {}).get("night_tags") or {}
    context = {
        "rush_dates": {d for d, t in tags.items() if "rush" in t},
        "unrushed_dates": {d for d, t in tags.items() if "unrushed" in t},
        "weeknight_max_minutes": memory.get("weeknight_max_minutes"),
    }
    return [v for v in plan_quality.check_week(plan_quality._load_plan_entries(plan_id), context)
            if v.rule in ("rush_cap_respected", "weeknight_cap_respected")]


@pytest.fixture
def capped():
    """Emily's own answer: every Monday-Friday dinner is 20 minutes."""
    tools.edit_preference("weeknight_max_minutes", CAP)


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)
    return _stub


@pytest.fixture
def picker():
    """
    A stub 'model' for the re-pick, and a record of every call. Hands back
    a dish of `minutes` (15 by default, inside the cap), never the same
    name twice, and never a name already on `avoid`.
    """
    class _Picker:
        def __init__(self):
            self.calls = []
            self.minutes = 15
            self.names = [f"Quick Dish {i}" for i in range(1, 12)]

        def __call__(self, context):
            self.calls.append(context)
            avoid = {a.strip().lower() for a in (context.get("avoid") or [])}
            while self.names:
                name = self.names.pop(0)
                if name.lower() in avoid:
                    continue
                return {"meal_name": name, "reason": "quick on the night",
                        "ingredients": [{"item": f"{name} stuff", "qty": "1", "category": "pantry"}],
                        "instructions": [f"Cook {name}.", "Serve."],
                        "food_groups": ["protein", "vegetable", "carb"],
                        "prep_time_minutes": 0, "cook_time_minutes": self.minutes}
            return {}
    return _Picker()


@pytest.fixture
def generate_only(monkeypatch):
    """
    Generate a week with the cap pass NO-OPPED, so a test can then drive one
    stage by hand. Four tests below are about the RE-PICK's own rules, and
    running them through generation let the free trade fix the week first —
    which measured nothing at all (found by running them).
    """
    def _run(week, days=None):
        monkeypatch.setattr(agent._cap_enforce, "enforce_minutes_caps",
                            lambda *a, **k: {"moved": [], "repicked": [], "left": []})
        return agent.generate_weekly_plan(week)["weekly_plan_id"]
    return _run


@pytest.fixture
def run(monkeypatch, picker):
    """
    Generate a week with the cap pass running against the stub picker, and
    hand back (plan_id, what the pass reported). Nothing here reaches the
    API: the model call is stubbed by `stub_model` and the picker by this.
    """
    def _run(week, days, *, pick=None):
        real = cap_enforce.enforce_minutes_caps
        seen = {}

        def _wrapped(plan_id, intake, memory, **kwargs):
            seen["result"] = real(plan_id, intake, memory,
                                  budget=kwargs.get("budget"), picker=pick or picker)
            seen["budget"] = kwargs.get("budget")
            return seen["result"]

        monkeypatch.setattr(agent._cap_enforce, "enforce_minutes_caps", _wrapped)
        plan = agent.generate_weekly_plan(week)
        return plan["weekly_plan_id"], seen
    return _run


# ---------- 1. the reported bug, end to end ----------

def test_a_rush_night_gets_a_dinner_it_can_actually_cook(capped, stub_model, run):
    """
    CATCH. Emily's own table, seeded: three nights tagged `rush`, dinners of
    35, 29 and 32 minutes against a 20-minute cap, with the two longest
    dishes of the week (28 and 24) sitting on the uncapped weekend. On main
    all three rush nights stay over cap and plan_quality only warns.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    for name, minutes in [("Seared Garlic Chicken Thighs", 35), ("Korean Chicken Pancake", 29),
                          ("Japanese Nikujaga", 32), ("Sheet-Pan Chicken Breast", 28),
                          ("Seared Sesame-Soy Salmon", 24), ("Quick Egg Fried Rice", 15),
                          ("Ten-Minute Tuna Pasta", 10)]:
        _recipe(name, minutes)
    tools.save_week_intake(week, night_tags={d: ["rush"] for d in dates[1:4]})
    stub_model(_week(dates, [
        "Quick Egg Fried Rice", "Seared Garlic Chicken Thighs", "Korean Chicken Pancake",
        "Japanese Nikujaga", "Ten-Minute Tuna Pasta", "Sheet-Pan Chicken Breast",
        "Seared Sesame-Soy Salmon",
    ]))

    plan_id, _ = run(week, None)

    assert _over_cap(plan_id, week) == [], "every rush night has to fit the time she said she had"
    states = {r["date"]: r["slot_state"] for r in _dinners(plan_id)}
    assert set(states.values()) == {"planned"}, "no night was handed back as a question"


def test_a_plain_weeknight_cap_is_held_to_as_well(capped, stub_model, run):
    """CATCH. No tag at all — just the household's standing weeknight answer,
    which is the other half of the acceptance criterion."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week)
    stub_model(_week(dates, ["Long Braise"] + ["Quick Eggs"] * 6))

    plan_id, _ = run(week, None)

    assert _over_cap(plan_id, week) == []


def test_the_tripwire_goes_to_zero_on_the_week_it_just_fixed(capped, stub_model, run):
    """
    CATCH. plan_quality's own rule is what proves this worked, and it keeps
    warning — it was never removed. On main it reports three warnings for
    this week; here it reports none.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Braise", 60)
    _recipe("Roast", 55)
    _recipe("Stew", 50)
    _recipe("Fast Eggs", 10)
    tools.save_week_intake(week, night_tags={d: ["rush"] for d in dates[0:3]})
    stub_model(_week(dates, ["Braise", "Roast", "Stew"] + ["Fast Eggs"] * 4))

    plan_id, _ = run(week, None)

    assert _cap_warnings(plan_id, week) == []


def test_the_free_trade_alone_fixes_a_week_with_somewhere_to_put_the_long_dish(
    capped, stub_model, run,
):
    """
    CATCH, and the one that matters most for cost: one long dinner on a rush
    Tuesday and a quick one on an uncapped Saturday need no model call at
    all. The picker RAISES if it is reached.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[1]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs", "Long Braise", "Quick Eggs", "Quick Eggs",
                             "Quick Eggs", "Quick Eggs", "Quick Eggs"]))

    def _never(context):
        raise AssertionError("a trade within the week must not cost a model call")

    plan_id, seen = run(week, None, pick=_never)

    assert _over_cap(plan_id, week) == []
    assert len(seen["result"]["moved"]) == 1, "one trade, no picks"
    assert seen["result"]["repicked"] == []
    dinners = {r["date"]: r["meal"] for r in _dinners(plan_id)}
    assert dinners[dates[1]] == "Quick Eggs"
    assert dinners[dates[5]] == "Long Braise" or dinners[dates[6]] == "Long Braise"


# ---------- 2. what is left alone ----------

def test_an_unrushed_night_keeps_its_long_dinner(capped, stub_model, run):
    """NOT WIRED under stub A (no call site, so no result to read). Emily,
    2026-09-23: `unrushed` lifts the cap for its own night only. Pinned by
    the mutation that makes the pass read no tags at all, which moves the
    braise off Wednesday."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["unrushed"]})
    stub_model(_week(dates, ["Quick Eggs", "Quick Eggs", "Long Braise"] + ["Quick Eggs"] * 4))

    plan_id, seen = run(week, None)

    assert {r["date"]: r["meal"] for r in _dinners(plan_id)}[dates[2]] == "Long Braise"
    assert seen["result"] == {"moved": [], "repicked": [], "left": []}


def test_a_night_the_household_asked_for_by_name_is_left_alone(capped, stub_model, run):
    """
    NOT WIRED under stub A. What it really pins is the mutation that drops
    meal_variety.theirs from `movable`, which re-picks the dish she named —
    main leaves it alone by doing nothing at all, which is not the same
    thing and is not evidence.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Slow Ribs", 120)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[1]: ["rush"]})
    days = _week(dates, ["Quick Eggs", "Slow Ribs"] + ["Quick Eggs"] * 5,
                 **{dates[1]: {"derived_from": {"freeform": "ribs on Tuesday please"}}})
    stub_model(days)

    plan_id, seen = run(week, None)

    assert {r["date"]: r["meal"] for r in _dinners(plan_id)}[dates[1]] == "Slow Ribs"
    left = seen["result"]["left"]
    assert [x["date"] for x in left] == [dates[1]]
    assert left[0]["why"] == "the household asked for this one by name"


def test_a_dish_with_no_minutes_on_record_is_left_where_it_is(capped, stub_model, run):
    """NOT WIRED under stub A. Unknown minutes cannot be judged — the same call
    every reader of time_caps makes. Pinned by the mutation that drops
    `minutes is not None` from `movable`, which only shows on the rule
    itself: see the comment at the last assertion."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    tools.add_recipe("Mystery Dish", ingredients=[{"item": "stuff", "qty": "1"}],
                     food_groups=["protein", "vegetable", "carb"])
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[1]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs", "Mystery Dish"] + ["Quick Eggs"] * 5))

    plan_id, seen = run(week, None)

    assert {r["date"]: r["meal"] for r in _dinners(plan_id)}[dates[1]] == "Mystery Dish"
    assert seen["result"] == {"moved": [], "repicked": [], "left": []}
    # Asserted on the rule directly, because the week-level outcome cannot
    # tell the mutation apart: a dish with no minutes is never a violation
    # either way, so nothing moves whether it is `movable` or not. Found by
    # running the mutation and getting 30 green.
    by_date = {n["date"]: n for n in cap_enforce.nights(
        plan_id, tools.get_week_intake(week), tools.get_household_memory())}
    assert by_date[dates[1]]["minutes"] is None
    assert by_date[dates[1]]["movable"] is False, "a dish nobody timed is not a dish to move"


def test_a_weekend_dinner_is_never_repicked(capped, stub_model, run):
    """NOT WIRED under stub A. Saturday and Sunday have no weeknight cap; a long
    dinner there is the point of the weekend. Pinned by the mutation that
    makes every night `movable` regardless of its cap."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week)
    stub_model(_week(dates, ["Quick Eggs"] * 5 + ["Long Braise", "Long Braise"]))

    plan_id, seen = run(week, None)

    dinners = {r["date"]: r["meal"] for r in _dinners(plan_id)}
    assert dinners[dates[5]] == dinners[dates[6]] == "Long Braise"
    assert seen["result"] == {"moved": [], "repicked": [], "left": []}


def test_a_night_nobody_is_home_for_is_never_touched(capped, stub_model, run):
    """CATCH (fails on main at "the braise went to the other free night").
    An `out` night is written planned_empty by the pass above this one and
    must never be offered as somewhere to put a long dinner."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[1]: ["rush"], dates[5]: ["out"]})
    stub_model(_week(dates, ["Quick Eggs", "Long Braise"] + ["Quick Eggs"] * 5))

    plan_id, seen = run(week, None)

    rows = {r["date"]: r for r in _dinners(plan_id)}
    assert rows[dates[5]]["slot_state"] == "planned_empty"
    assert rows[dates[6]]["meal"] == "Long Braise", "the braise went to the other free night"
    assert _over_cap(plan_id, week) == []


def _chained_week(week, dates, source_i=0, reheat_i=1):
    """A week whose Monday cooks a big dish double and whose Tuesday reheats
    it — both halves written the way repair_leftover_chains writes them."""
    days = []
    for i, d in enumerate(dates):
        days += [_slot(d, "breakfast", "Oats"), _slot(d, "lunch", "Wrap"),
                 _slot(d, "snack", "Apple")]
        if i == source_i:
            days.append(_slot(d, "dinner", "Big Chili",
                              derived_from={"make_double_for": [f"{dates[reheat_i]}:dinner"]}))
        elif i == reheat_i:
            days.append(_slot(d, "dinner", "Big Chili",
                              derived_from={"links_to": f"{dates[source_i]}:dinner"}))
        else:
            days.append(_slot(d, "dinner", "Quick Eggs"))
    return days


def test_a_reheat_night_and_the_batch_that_feeds_it_are_never_touched(capped, stub_model, run):
    """
    NOT WIRED under stub A. Nothing is cooked on a reheat, so the clock does not
    apply to it;
    and moving one end of a cook-once-eat-twice pair can stretch the gap past
    leftovers.MAX_LEFTOVER_DAYS, which repair_leftover_chains would then tear
    down. Pinned by the mutation that drops the reheat/source terms from
    `movable`. The picker RAISES if it is reached.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Big Chili", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[1]: ["rush"]})
    stub_model(_chained_week(week, dates))

    def _never(context):
        raise AssertionError("a batch is not a cook on the day")

    plan_id, seen = run(week, None, pick=_never)

    dinners = {r["date"]: r["meal"] for r in _dinners(plan_id)}
    assert dinners[dates[0]] == dinners[dates[1]] == "Big Chili"
    assert seen["result"]["moved"] == [] and seen["result"]["repicked"] == []
    assert {x["why"] for x in seen["result"]["left"]} == {"it is a batch, not a cook on the day"}


def test_a_chain_on_a_weeknight_still_warns_and_is_NOT_fixed_here(capped, stub_model, run):
    """
    CHAR, and it is why "the tripwire goes to zero" is a claim about a week
    with no leftovers chain in it. Measured on main as well as here:
    time_caps.minutes_cap ignores `is_leftovers` for DINNER (it is consulted
    for lunch only), and plan_quality's two dinner rules read the recipe's
    own prep+cook — so a 90-minute batch on a capped Monday AND the Tuesday
    that merely reheats it both warn, for a night on which nothing is cooked.
    `_weekday_lunch_cap_respected`, one rule down, already exempts a chain.

    Deliberately NOT fixed: exempting a chained dinner inside minutes_cap
    would also lift the cap on swap_in_place's gate, where a swap onto a
    reheat night breaks the chain and the replacement really is cooked that
    day. Two different questions on one (date, slot). Its own card, and
    Emily's decision — is a deliberate 90-minute Monday batch a breach at
    all? Invert this when it is settled.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Big Chili", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[1]: ["rush"]})
    stub_model(_chained_week(week, dates))

    plan_id, _ = run(week, None, pick=lambda context: {})

    rules = sorted(v.rule for v in _cap_warnings(plan_id, week))
    assert rules == ["rush_cap_respected", "weeknight_cap_respected"]


# ---------- 3. the trade's own rules ----------

def test_the_trade_arithmetic_reaches_the_floor_for_one_weekday_cap():
    """
    STUB against main (cap_enforce is a canned stub there) and so worth
    nothing as red evidence — but it is the claim the module's docstring
    makes, so it is asserted rather than trusted: with one cap Monday to
    Friday and none at the weekend, greedy pairwise trading leaves exactly
    (dishes over cap − uncapped nights) violations, which is the minimum any
    arrangement can reach.
    """
    caps = {f"2026-09-{28 + i}": (CAP if i < 5 else None) for i in range(7)}
    minutes = [15, 35, 29, 32, 10, 28, 24]
    assignment = {d: {"date": d, "cap": caps[d], "minutes": m}
                  for d, m in zip(sorted(caps), minutes)}
    for _ in range(20):
        trades = cap_enforce._trades(assignment, caps)
        if not trades:
            break
        a, b = trades[0]
        assignment[a], assignment[b] = (dict(assignment[b], date=a, cap=caps[a]),
                                        dict(assignment[a], date=b, cap=caps[b]))
    over = len([m for m in minutes if m > CAP])
    uncapped = len([c for c in caps.values() if c is None])
    assert cap_enforce._violations(assignment)[0] == over - uncapped == 3


def test_no_trade_is_made_when_none_makes_the_week_better():
    """GREEN on main FOR THE WRONG REASON — the stub answers "no trade" to
    everything, so this one test cannot tell the two apart. Pinned by the
    mutation that returns every pair whether it improves the week or not.
    A week already at its floor is left alone: _trades returns nothing, so
    rearrange writes nothing."""
    caps = {"2026-09-28": CAP, "2026-09-29": CAP, "2026-10-03": None}
    assignment = {"2026-09-28": {"date": "2026-09-28", "cap": CAP, "minutes": 10},
                  "2026-09-29": {"date": "2026-09-29", "cap": CAP, "minutes": 15},
                  "2026-10-03": {"date": "2026-10-03", "cap": None, "minutes": 90}}
    assert cap_enforce._trades(assignment, caps) == []


def test_a_trade_shortens_an_overrun_it_cannot_remove():
    """STUB under stub A. The second element of _violations: with more long dishes than
    free nights, the LONGEST goes to the free night. This is why Emily's
    week improves even where it cannot be made clean."""
    caps = {"2026-09-28": CAP, "2026-10-03": None}
    assignment = {"2026-09-28": {"date": "2026-09-28", "cap": CAP, "minutes": 90},
                  "2026-10-03": {"date": "2026-10-03", "cap": None, "minutes": 25}}
    assert cap_enforce._trades(assignment, caps) == [("2026-09-28", "2026-10-03")]


def test_a_trade_that_would_put_a_dish_in_front_of_a_hater_is_refused(capped, stub_model, run):
    """
    NOT WIRED under stub A (which trades nothing at all). Pinned by the mutation
    that drops _would_offend from rearrange: without it the risotto lands on
    the night Vineeth is eating.
    """
    week = _monday()
    dates = tools._week_dates(week)
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    _filler()
    _recipe("Mushroom Risotto", 90)
    _recipe("Quick Eggs", 10)
    ids = {m["name"]: m["id"] for m in tools.list_members()}
    tools.attribute_recipe_feedback("Mushroom Risotto", "Vineeth", rating="disliked")
    # Vineeth eats at the weekend only, so the risotto has nowhere to go.
    for i in range(5):
        tools.set_slot_attendance(dates[i], "dinner", [ids["Emily"]])
    for i in (5, 6):
        tools.set_slot_attendance(dates[i], "dinner", [ids["Emily"], ids["Vineeth"]])
    tools.save_week_intake(week, night_tags={dates[1]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs", "Mushroom Risotto"] + ["Quick Eggs"] * 5))

    plan_id, seen = run(week, None)

    assert seen["result"]["moved"] == [], "no trade — the only free nights are Vineeth's"
    # It was re-picked instead, which is the right next answer.
    assert {r["date"]: r["meal"] for r in _dinners(plan_id)}[dates[1]] != "Mushroom Risotto"


def test_a_trade_keeps_the_entry_ids_and_touches_no_shopping_list(
    capped, stub_model, generate_only,
):
    """
    CATCH-shaped, NOT WIRED under stub A. swap_dinner_nights re-dates the rows IN
    PLACE, which is why it is the write this pass uses rather than a
    delete-and-insert: ids, groceries, the cooked tick and the plate's sides
    all ride along. Pinned by the mutation that trades through
    swap_meal_in_plan instead.

    Driven at cap_enforce.rearrange so the ids can be read on both sides of
    the trade — the first version asserted `ids[dish] in ids.values()`,
    which cannot fail, and is the kind of assertion this repo keeps having
    to unpick.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[1]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs", "Long Braise"] + ["Quick Eggs"] * 5))
    plan_id = generate_only(week)

    before = {r["date"]: r["id"] for r in _dinners(plan_id)}
    braise_id = [r["id"] for r in _dinners(plan_id) if r["meal"] == "Long Braise"][0]
    assert before[dates[1]] == braise_id, "the precondition: the braise starts on the rush night"

    made = cap_enforce.rearrange(plan_id, tools.get_week_intake(week),
                                 tools.get_household_memory())

    assert len(made) == 1
    after = {r["date"]: (r["id"], r["meal"]) for r in _dinners(plan_id)}
    landed = [d for d, (i, _m) in after.items() if i == braise_id]
    assert landed and landed[0] != dates[1], "the braise's own row moved to another night"
    assert after[landed[0]][1] == "Long Braise", "and it is still that dish"
    assert sorted(before.values()) == sorted(i for i, _m in after.values()), \
        "every row is the row it was — re-dated, not deleted and re-inserted"
    conn = get_conn()
    lines = conn.execute("SELECT COUNT(*) AS n FROM grocery_items").fetchone()["n"]
    conn.close()
    assert lines == 0, "a draft's trade puts nothing on the shopping list"


def test_a_moved_dinner_loses_a_reason_written_about_the_night_it_left(capped, stub_model, run):
    """
    CATCH. The model's reasoning is often about the NIGHT ("lighter after
    Monday's chili"); once the dish is on another night that can be plainly
    false, so it is replaced with one sentence that is true either way and
    where it came from is recorded (MOVE_REASON). Pinned by the mutation
    that leaves the reasoning alone.

    The sentence rather than an empty string, measured: emptying it makes
    plan_quality's `reasoning_is_specific` fire for every moved row, which
    is one warning traded for another.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[1]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs", "Long Braise"] + ["Quick Eggs"] * 5))

    plan_id, _ = run(week, None)

    row = [r for r in _dinners(plan_id) if r["meal"] == "Long Braise"][0]
    assert row["reasoning"] == cap_enforce.MOVE_REASON
    assert row["reasoning"] and "Long Braise because" not in row["reasoning"]
    note = json.loads(row["derived_from_json"] or "{}")[cap_enforce.MOVED_KEY]
    assert note["from"] == dates[1] and note["cap"] == RUSH_CAP
    assert "reasoning_is_specific" not in {
        v.rule for v in plan_quality.check_week(plan_quality._load_plan_entries(plan_id), {})
    }, "an empty reason would trade a cap warning for a reasoning warning"


# ---------- 4. the re-pick's own rules ----------

def test_a_pick_over_the_cap_is_refused_and_the_next_one_taken(capped, stub_model, run, picker):
    """
    CATCH. pick_gate is the allergen and taste gate and has never included
    the clock, so the cap has to be the pick's OWN refusal (`reject_pick`).
    The first pick offered here is 45 minutes and must be turned down.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    tools.save_week_intake(week, night_tags={d: ["rush"] for d in dates})
    stub_model(_week(dates, ["Long Braise"] * 7))

    offered = []

    def _pick(context):
        offered.append(len(offered))
        minutes = 45 if len(offered) % 2 == 1 else 12
        name = f"Dish {len(offered)}"
        return {"meal_name": name, "reason": "r",
                "ingredients": [{"item": "x", "qty": "1", "category": "pantry"}],
                "instructions": ["Cook.", "Serve."],
                "food_groups": ["protein", "vegetable", "carb"],
                "prep_time_minutes": 0, "cook_time_minutes": minutes}

    plan_id, seen = run(week, None, pick=_pick)

    assert len(offered) >= 2, "the 45-minute pick has to be refused and another asked for"
    for row in _dinners(plan_id):
        if row["meal"] and row["meal"] != "Long Braise":
            assert row["minutes"] <= RUSH_CAP, f"{row['meal']} is {row['minutes']} minutes"


def test_a_repick_never_lands_a_dish_already_on_the_week(capped, stub_model, generate_only):
    """CATCH (fails on main at "the braise had to be re-picked"). `avoid`
    carries the week's own dinners and `reject` refuses one anyway; pinned
    by the mutation that passes an empty avoid list. Driven at
    cap_enforce.repick so the free trade cannot settle the week first."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[0]: ["rush"]})
    stub_model(_week(dates, ["Long Braise"] + ["Quick Eggs"] * 6))
    plan_id = generate_only(week)

    asked = []

    def _pick(context):
        asked.append(sorted(a.lower() for a in context.get("avoid") or []))
        return {"meal_name": "Quick Eggs", "reason": "r",
                "ingredients": [{"item": "x", "qty": "1", "category": "pantry"}],
                "instructions": ["Cook.", "Serve."],
                "food_groups": ["protein", "vegetable", "carb"],
                "prep_time_minutes": 0, "cook_time_minutes": 10}

    done = cap_enforce.repick(plan_id, tools.get_week_intake(week),
                              tools.get_household_memory(), picker=_pick)

    assert asked, "the braise had to be re-picked"
    assert "quick eggs" in asked[0] and "long braise" in asked[0]
    # Offered nothing but a dish already on the week, the night stands.
    assert done == []
    assert [r["meal"] for r in _dinners(plan_id) if r["date"] == dates[0]] == ["Long Braise"]


def test_a_repeated_over_cap_dinner_is_not_handed_back_to_its_other_night(
    capped, stub_model, generate_only,
):
    """
    GUARD, and the docstring says what it does NOT pin, because the first
    version of it claimed more than it could see.

    One dish on two capped nights, both re-picked. What is pinned: the dish
    that was just refused for being too long is still on `avoid` when the
    second night is asked about, and so is the dish that replaced the first.

    What is NOT pinned, measured: cap_enforce keeping the outgoing dish in
    its own avoid set is unobservable defence in depth — `_repick_entry`
    prepends `entry["meal"]` to `tried` itself, so the dish is on avoid for
    its OWN night regardless, and any other night is covered by the cap
    gate refusing it for being too long. The mutation that discards it
    leaves all 32 green. It stays because it says what is meant where it
    happens; a reader counting this file's evidence should know it is not
    covered by it.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    tools.save_week_intake(week, night_tags={dates[0]: ["rush"], dates[1]: ["rush"]})
    stub_model(_week(dates, ["Long Braise"] * 7))
    plan_id = generate_only(week)

    asked = []

    def _pick(context):
        asked.append(sorted(a.strip().lower() for a in context.get("avoid") or []))
        return {"meal_name": f"Quick Thing {len(asked)}", "reason": "quick",
                "ingredients": [{"item": "x", "qty": "1", "category": "pantry"}],
                "instructions": ["Cook.", "Serve."],
                "food_groups": ["protein", "vegetable", "carb"],
                "prep_time_minutes": 0, "cook_time_minutes": 10}

    cap_enforce.repick(plan_id, tools.get_week_intake(week),
                       tools.get_household_memory(), picker=_pick)

    assert len(asked) >= 2, "both capped nights had to be asked about"
    assert "long braise" in asked[1], "the dish just refused is still on avoid"
    assert "quick thing 1" in asked[1], "and so is the one that replaced it"
    # ...and whichever way the avoid set is kept, the cap gate is what makes
    # it impossible for the long dish to come back: offered nothing else,
    # the night stands as generated.
    dinners = {r["date"]: (r["meal"], r["minutes"]) for r in _dinners(plan_id)}
    for d in dates[:2]:
        assert dinners[d][1] <= RUSH_CAP, dinners[d]


def test_nothing_quicker_coming_back_leaves_the_night_as_generated_never_open(
    capped, stub_model, run,
):
    """
    NOT WIRED under stub A, and it is the card's own instruction: "never nights
    handed back empty". Pinned by the mutation that opens the slot instead.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    tools.save_week_intake(week, night_tags={d: ["rush"] for d in dates})
    stub_model(_week(dates, ["Long Braise"] * 7))

    plan_id, seen = run(week, None, pick=lambda context: {})

    rows = _dinners(plan_id)
    assert {r["slot_state"] for r in rows} == {"planned"}
    assert {r["meal"] for r in rows} == {"Long Braise"}
    # All seven nights are tagged, so the weekend is capped too — at
    # RUSH_MAX_MINUTES (30), since weeknight_max_minutes is Monday-Friday.
    assert [x["why"] for x in seen["result"]["left"]] == ["nothing quicker came back"] * 7


def test_the_repick_spends_the_generations_shared_budget_and_no_more(capped, stub_model, run):
    """
    NOT WIRED under stub A. The pass takes no budget of its own:
    it spends allergen_gate.CallBudget's six calls for the whole generation,
    so it adds no new ceiling. Seven over-cap nights, each refused twice,
    cannot cost more than six calls.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    tools.save_week_intake(week, night_tags={d: ["rush"] for d in dates})
    stub_model(_week(dates, ["Long Braise"] * 7))

    calls = []

    def _pick(context):
        calls.append(1)
        return {"meal_name": f"Slow Thing {len(calls)}", "reason": "r",
                "ingredients": [{"item": "x", "qty": "1", "category": "pantry"}],
                "instructions": ["Cook.", "Serve."],
                "food_groups": ["protein", "vegetable", "carb"],
                "prep_time_minutes": 0, "cook_time_minutes": 99}

    plan_id, seen = run(week, None, pick=_pick)

    from app.tools import allergen_gate
    assert len(calls) <= allergen_gate.MAX_REPICK_CALLS
    assert seen["budget"].left == 0


def test_the_worst_overrun_is_repicked_first(capped, stub_model, generate_only):
    """
    STUB under stub A. With a budget that cannot cover every night, the 90-minute dinner
    is the one worth spending it on. Pinned by the mutation that sorts the
    targets by date. Driven at cap_enforce.repick: through generation the
    free trade moves the 90 off first and the order says nothing (found by
    running it).
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Slightly Long", 25)
    _recipe("Very Long", 90)
    tools.save_week_intake(week, night_tags={dates[0]: ["rush"], dates[1]: ["rush"]})
    stub_model(_week(dates, ["Slightly Long", "Very Long"] + ["Slightly Long"] * 5))
    plan_id = generate_only(week)

    order = []

    def _pick(context):
        order.append(context.get("replacing_because") or "")
        return {}

    cap_enforce.repick(plan_id, tools.get_week_intake(week),
                       tools.get_household_memory(), picker=_pick)

    assert order and "Very Long" in order[0], f"asked about {order[0]!r} first"


def test_the_model_is_told_which_cap_and_why(capped, stub_model, generate_only):
    """STUB under stub A. The household's own terms — their tag, or their
    standing weeknight answer. Pinned by the mutation that drops
    `because`."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[0]: ["rush"]})
    stub_model(_week(dates, ["Long Braise"] + ["Quick Eggs"] * 6))
    plan_id = generate_only(week)

    told = []
    cap_enforce.repick(plan_id, tools.get_week_intake(week), tools.get_household_memory(),
                       picker=lambda c: told.append(c.get("replacing_because")) or {})

    assert told and "short on time" in told[0] and "20 minutes" in told[0]
    night = {"date": dates[3], "tags": []}
    assert cap_enforce._cap_reason(night, CAP) == "weeknights here are 20 minutes"


def test_a_repicked_dinner_keeps_the_picks_own_reason(capped, stub_model, generate_only):
    """
    CATCH, and it is the measured half of the REPICK_REASON decision: an
    empty reason makes plan_quality's `reasoning_is_specific` fire "has no
    reasoning at all", which would trade one warning for another. Pinned by
    the mutation that sets REPICK_REASON to "".
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[0]: ["rush"]})
    stub_model(_week(dates, ["Long Braise"] + ["Quick Eggs"] * 6))
    plan_id = generate_only(week)

    cap_enforce.repick(plan_id, tools.get_week_intake(week), tools.get_household_memory(),
                       picker=lambda c: {
                           "meal_name": "Fast Frittata", "reason": "ten minutes, one pan",
                           "ingredients": [{"item": "eggs", "qty": "6", "category": "dairy"}],
                           "instructions": ["Cook.", "Serve."],
                           "food_groups": ["protein", "vegetable", "carb"],
                           "prep_time_minutes": 0, "cook_time_minutes": 10})

    row = [r for r in _dinners(plan_id) if r["date"] == dates[0]][0]
    assert row["meal"] == "Fast Frittata"
    assert row["reasoning"] == "ten minutes, one pan"
    assert "reasoning_is_specific" not in {
        v.rule for v in plan_quality.check_week(plan_quality._load_plan_entries(plan_id), {})
    }


# ---------- 5. nothing to do, nothing done ----------

def test_a_week_with_no_cap_and_no_tag_is_untouched(stub_model, run):
    """
    NOT WIRED under stub A, and the acceptance criterion measured rather than
    asserted: the
    whole plan is compared row for row against a run with the pass no-opped.
    Pinned by the mutation that makes every night `movable` regardless of
    its cap.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    for name in ("Braise", "Roast", "Lasagne", "Curry", "Paella", "Pot Roast", "Ragu"):
        _recipe(name, 90)
    tools.save_week_intake(week)
    stub_model(_week(dates, ["Braise", "Roast", "Lasagne", "Curry", "Paella", "Pot Roast", "Ragu"]))

    def _never(context):
        raise AssertionError("no cap anywhere — nothing may be re-picked")

    plan_id, seen = run(week, None, pick=_never)

    assert seen["result"] == {"moved": [], "repicked": [], "left": []}
    assert [(r["date"], r["meal"], r["reasoning"], r["derived_from_json"]) for r in _dinners(plan_id)] == [
        (d, n, f"{n} because", "{}")
        for d, n in zip(dates, ["Braise", "Roast", "Lasagne", "Curry", "Paella", "Pot Roast", "Ragu"])
    ]


def test_a_capped_week_whose_dinners_all_fit_is_untouched(capped, stub_model, run):
    """NOT WIRED under stub A. The common case: a cap and a tag, and every dinner
    already inside them. Nothing moves, nothing is asked for."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Fast Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[1]: ["rush"], dates[3]: ["rush"]})
    stub_model(_week(dates, ["Fast Eggs"] * 7))

    plan_id, seen = run(week, None, pick=lambda c: (_ for _ in ()).throw(AssertionError("no")))

    assert seen["result"] == {"moved": [], "repicked": [], "left": []}
    assert {r["reasoning"] for r in _dinners(plan_id)} == {"Fast Eggs because"}


def test_a_failure_inside_the_pass_never_costs_the_week(capped, stub_model, run, monkeypatch):
    """NOT WIRED under stub A. The pass swallows its own failures, like every other repair in
    _finish_week_slots: a dinner that is too long is worth a repair, never a
    lost week."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[1]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs", "Long Braise"] + ["Quick Eggs"] * 5))

    def _boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(cap_enforce, "_trades", _boom)
    monkeypatch.setattr(cap_enforce, "nights", _boom)

    plan_id, seen = run(week, None)

    assert seen["result"] == {"moved": [], "repicked": [], "left": []}
    audit = tools.audit_plan_slots(plan_id)
    assert audit["complete"] is True and audit["present"] == 21


def test_another_households_over_cap_dinner_is_none_of_this_passes_business(
    capped, stub_model, run,
):
    """
    GUARD, and this repo has recorded three separate cross-household leaks.
    Both raw statements in cap_enforce (_load_dinners and _note_move) are
    household-scoped, and swap_dinner_nights is too.

    THE END-TO-END HALF CANNOT FAIL, and is written down as such rather
    than counted: _load_dinners is scoped by weekly_plan_id as well, so
    dropping `household_id()` from its WHERE still returns only this plan's
    rows — measured, that mutation leaves the whole file green. The plan id
    is the real scope and the household filter is defence in depth. What
    DOES pin it is the last assertion, which hands the pass another
    household's plan id directly; that is the one way a foreign row could
    ever reach these statements, and the mutation reddens it.

    `use_household` is not decoration: a tool called straight from a test
    runs on the ContextVar's default, so seeding the other household's week
    without it writes into household 1 and the test passes proving nothing.
    """
    from app import households

    other = households.create_household("The Other Family", "another-one-entirely")
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)

    # The other household: a long dinner on a rush night of its own, which
    # this household's generation must not reach.
    with tools.use_household(other):
        tools.edit_preference("weeknight_max_minutes", CAP)
        _recipe("Their Braise", 95)
        their_plan = tools.create_weekly_plan(week)["weekly_plan_id"]
        tools.plan_meal(dates[1], "Their Braise", slot="dinner", weekly_plan_id=their_plan,
                        add_ingredients_to_grocery_list=False)
        before = [(r["date"], r["meal"], r["reasoning"]) for r in _dinners(their_plan)]

    tools.save_week_intake(week, night_tags={dates[1]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs", "Long Braise"] + ["Quick Eggs"] * 5))
    plan_id, seen = run(week, None)

    assert _over_cap(plan_id, week) == [], "this household's week was still fixed"
    with tools.use_household(other):
        assert [(r["date"], r["meal"], r["reasoning"]) for r in _dinners(their_plan)] == before
    assert {x["date"] for x in seen["result"]["left"]} == set()

    # Handed the other household's plan id, as this household, the pass
    # sees no nights at all — so it can neither move nor re-pick one.
    assert cap_enforce.nights(their_plan, tools.get_week_intake(week),
                              tools.get_household_memory()) == []


# ---------- 6. where it runs ----------

def _agent_source() -> str:
    from conftest import agent_function_source
    return agent_function_source("_finish_week_slots")


def _code_only(src: str) -> str:
    """The code with comments and docstrings out — this repo has been bitten
    three times by a source marker a COMMENT satisfied."""
    import ast
    tree = ast.parse("if 1:\n" + "\n".join("    " + line for line in src.splitlines()))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body.pop(0)
    return ast.unparse(tree)


def test_the_pass_runs_before_the_plates_pass_the_quality_log_and_the_sweep():
    """
    MARKER. Reads agent.py's own compiled source, comments
    stripped. The order is the whole safety argument — before the plates
    pass so a side is written for the night the dish finally sits on, before
    plan_quality.check_and_log so the tripwire measures the result, and
    before allergen_gate.sweep_plan so a clash a move creates is caught.
    """
    code = _code_only(_agent_source())
    here = code.index("enforce_minutes_caps")
    assert here < code.index("_complete_plates_pass")
    assert here < code.index("check_and_log")
    assert here < code.index("sweep_plan")
    # ...and AFTER every pass that can move or replace a dinner, so one
    # place answers for the whole week.
    for earlier in ("repair_leftover_chains", "repick_repeats", "repick_recent_repeats",
                    "enforce_distinct_meal_count", "use_requested_ingredients"):
        assert code.index(earlier) < here, earlier


def test_the_pass_shares_the_generations_re_pick_budget():
    """MARKER. A budget of its own would double the worst case. Pinned by
    the mutation that passes no budget at all."""
    code = _code_only(_agent_source())
    call = code[code.index("enforce_minutes_caps"):]
    call = call[:call.index(")\n") + 1] if ")\n" in call else call[:400]
    assert "repick_budget" in call


# ---------- 7. deliberately NOT fixed ----------

def test_generations_own_fold_puts_a_repeated_over_cap_dinner_out_of_reach(
    capped, stub_model, run,
):
    """
    CHAR, and the biggest hole in this card — measured through GENERATION
    rather than a hand-written chain, because the point is that the app
    itself makes this shape. (Red on main only because it reads this pass's
    own report of what it left alone; the claim holds there too.)

    A household asking for fewer dinners than nights gets repeats, and
    `meal_variety`'s fold turns a repeat within three days into a
    cook-once-eat-twice chain. Both ends of a chain are exempt here (the
    card's own instruction), so a 35-minute dinner cooked on a rush Tuesday
    and reheated on the Wednesday is reached by NEITHER stage, and
    plan_quality goes on warning about both.

    The REHEAT end is plainly right: nothing is cooked on it. The COOK end
    is the open question — the household really is cooking 35 minutes on a
    night they said was short on time, and it is deliberate batching, which
    is the whole point of asking for fewer dinners. EMILY'S CALL. The
    follow-up that would close it is moving a chain WHOLE (both nights
    together) rather than one night at a time, which swap_dinner_nights
    cannot do and which risks stretching the gap past
    leftovers.MAX_LEFTOVER_DAYS. Its own card; invert this when it lands.

    Measured on a household asking for three dinners across seven nights,
    the repeat landing on the two adjacent rush nights.
    """
    tools.set_household_meal_preferences(dinners_per_week=3)
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Seared Garlic Chicken Thighs", 35)
    _recipe("Quick Egg Fried Rice", 15)
    _recipe("Korean Chicken Pancake", 29)
    tools.save_week_intake(week, night_tags={d: ["rush"] for d in dates[1:4]})
    stub_model(_week(dates, [
        "Quick Egg Fried Rice", "Seared Garlic Chicken Thighs",
        "Seared Garlic Chicken Thighs", "Quick Egg Fried Rice",
        "Quick Egg Fried Rice", "Korean Chicken Pancake", "Korean Chicken Pancake",
    ]))

    plan_id, seen = run(week, None, pick=lambda context: {})

    rows = {n["date"]: n for n in cap_enforce.nights(
        plan_id, tools.get_week_intake(week), tools.get_household_memory())}
    # The fold really did chain the repeat -- asserted, so this test cannot
    # go quiet if that stops happening.
    assert rows[dates[1]]["source"] and rows[dates[2]]["reheat"], \
        "the premise: generation's own fold made this a batch"
    assert seen["result"]["moved"] == [] and seen["result"]["repicked"] == []
    assert [x["why"] for x in seen["result"]["left"]] == \
        ["it is a batch, not a cook on the day"] * 2
    assert len(_cap_warnings(plan_id, week)) == 2, "and plan_quality goes on warning"


def test_a_lunch_over_its_cap_is_still_only_warned_about(capped, stub_model, run):
    """
    CHAR. The weekday lunch cap is the week's own answer now (step 3,
    "Weekday lunches") and swap_dinner_nights is dinner-only; this card is
    about dinner. A weekday lunch cooked that day is still only warned
    about. Invert this when lunch is enforced too.
    """
    week = _monday()
    dates = tools._week_dates(week)
    tools.add_recipe("Oats", ingredients=[{"item": "o", "qty": "1"}],
                     prep_time_minutes=5, cook_time_minutes=0)
    tools.add_recipe("Apple", ingredients=[{"item": "a", "qty": "1"}],
                     prep_time_minutes=0, cook_time_minutes=0)
    _recipe("Long Lunch", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week)
    days = []
    for d in dates:
        days += [_slot(d, "breakfast", "Oats"), _slot(d, "lunch", "Long Lunch"),
                 _slot(d, "dinner", "Quick Eggs"), _slot(d, "snack", "Apple")]
    stub_model(days)

    plan_id, seen = run(week, None)

    assert seen["result"]["repicked"] == [] and seen["result"]["moved"] == []
    lunch_warnings = [v for v in plan_quality.check_week(
        plan_quality._load_plan_entries(plan_id),
        {"prep_days": [], "lunch_kinds": {}},
    ) if v.rule == "weekday_lunch_cap_respected"]
    assert len(lunch_warnings) == 5, "the five weekday lunches, warned about and not repaired"
