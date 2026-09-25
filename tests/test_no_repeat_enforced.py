"""
A dinner or lunch from the last two weeks never reaches the draft.

Emily, 2026-09-20: "you're continuously giving me the same food
recommendations as previous weeks." The rule has existed three times over
and was enforced nowhere — the drafting prompt asks for it ("the no-repeat
rule against recent_history is about DINNER and LUNCH"), plan_quality
warned about the breach at severity "warn", and the draft's own opening
line reported it to the household's face ("… Chili back from the last two
weeks"). Nothing put it right. meal_variety.repick_recent_repeats does:
every dinner and lunch inside the window is replaced with one they have
not had, the whole dish and all its nights together, through the swap's own
picker and the one write every swap in the app uses.

WHAT IS RED AGAINST `main`, AND WHY, SINCE THE TWO ARE NOT THE SAME
THING. Three shapes live in this file and each test's own docstring says
which it is:

  CATCH        — drives generation and fails on `main` on the assertion it
                 is named for: the repeat survives.
  NAME         — calls a function `main` has not got, so it dies on an
                 AttributeError rather than on its claim. Worth having and
                 worth nothing as evidence; each one names the mutation
                 that pins it instead.
  GUARD        — green on `main` AND here, for opposite reasons: `main`
                 leaves everything alone because it enforces nothing, and
                 this leaves this one alone because it was told to. Pinned
                 by mutation, never by redness.

Measured against `main`'s app/ with this file dropped in: 28 red, 12
green. Of the 28, ELEVEN fail on the assertion they are named for, TWO
are red on the bug by an indirect route and say so, and FIFTEEN die on a
name `main` has not got. The number that means anything is the eleven,
and the sixteen mutations the green ones name.

Every test stubs the model: the week's generation at
agent.generate_weekly_plan_llm, the re-pick at
swap_in_place._pick_replacement.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import allergen_gate, draft_opener, leftovers, meal_variety, plan_quality
from app.tools import swap_in_place as sip


# ---------- the world ----------

def _monday(offset_weeks: int = 0) -> str:
    """The Monday of the week `offset_weeks` from the one containing today.
    Seeded off the HOUSEHOLD's clock through _week_dates' own anchor, so a
    run whose process day and household day disagree still asks about one
    week (CLAUDE.md, "A dated test seeds off the HOUSEHOLD's clock")."""
    from conftest import household_today
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _slot(date: str, slot: str, name: str, **extra) -> dict:
    d = {
        "date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
        "ingredients": [{"item": f"{name} stuff", "qty": "1", "category": "pantry"}],
        "instructions": [f"Cook the {name.lower()} over medium heat for 10 minutes.", "Serve."],
        "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
        "prep_time_minutes": 10, "cook_time_minutes": 15,
    }
    d.update(extra)
    return d


def _week(week: str, dinners, lunches=None, breakfasts=None, snacks=None) -> list[dict]:
    dates = tools._week_dates(week)
    lunches = lunches or ["Chickpea salad"] * 7
    breakfasts = breakfasts or ["Overnight oats"] * 7
    snacks = snacks or ["Apple"] * 7
    out = []
    for i, date in enumerate(dates):
        out.append(_slot(date, "breakfast", breakfasts[i]))
        out.append(_slot(date, "snack", snacks[i]))
        out.append(_slot(date, "lunch", lunches[i]))
        out.append(_slot(date, "dinner", dinners[i]))
    return out


LAST_WEEK_DINNERS = ["Bean chili", "Beef tacos", "Salmon", "Kofte", "Burgers", "Shrimp", "Pad thai"]
FRESH_DINNERS = ["Lamb stew", "Miso cod", "Bibimbap", "Ratatouille", "Jerk chicken", "Pierogi", "Dal"]


@pytest.fixture
def stub_model(monkeypatch):
    seen = {}

    def _stub(days):
        def fake(ctx):
            seen["ctx"] = ctx
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake)
        return seen
    return _stub


@pytest.fixture
def picker(monkeypatch):
    """The re-pick, stubbed. Hands out a different dish each call so a
    week with several repeats does not look like a week with one."""
    calls = []
    names = iter(["Moussaka", "Laksa", "Gumbo", "Paella", "Risotto", "Bigos", "Cassoulet", "Pho"])

    def pick(context):
        calls.append(context)
        name = next(names)
        return {
            "meal_name": name, "reason": f"{name} because",
            "ingredients": [{"item": f"{name} stuff", "qty": "1", "category": "pantry"}],
            "instructions": [f"Cook the {name.lower()} over medium heat.", "Serve."],
            "food_groups": ["protein", "vegetable", "carb"],
            "prep_time_minutes": 10, "cook_time_minutes": 20,
        }

    monkeypatch.setattr(sip, "_pick_replacement", pick)
    return calls


def _approve_in_place(plan_id: int) -> None:
    """Mark a plan approved without running approve_weekly_plan's own
    grocery ingest — what several tests in this repo already do to put a
    week inside the variety window (test_draft_says_what_it_did)."""
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()


@pytest.fixture
def last_week(stub_model):
    """Last week, approved: chili on Monday, beef tacos on Tuesday, and
    Greek salad every lunch. Its Monday is seven days before this week's,
    so its dinners are between one and seven days old."""
    week = _monday(-1)
    stub_model(_week(week, LAST_WEEK_DINNERS, ["Greek salad"] * 7))
    plan = agent.generate_weekly_plan(week)
    _approve_in_place(plan["weekly_plan_id"])
    return plan


def _meals(plan_id: int) -> list[dict]:
    return tools.get_weekly_plan(plan_id)["meals"]


def _at(plan_id: int, date: str, slot: str) -> dict:
    return next(m for m in _meals(plan_id) if m["date"] == date and m["slot"] == slot)


def _names(plan_id: int, slot: str) -> list[str]:
    return [m["meal"] for m in _meals(plan_id) if m["slot"] == slot]


def _derived(entry_id: int) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return json.loads((row["derived_from_json"] if row else None) or "{}") or {}


# ---------- the reported bug ----------

def test_a_dinner_from_last_week_is_replaced_not_merely_logged(last_week, stub_model, picker):
    """
    CATCH. The reproduction: an approved plan carrying Bean chili eight
    days ago, a draft bringing Bean chili back. On `main` Monday's dinner
    is still Bean chili and the picker is never called.
    """
    week = _monday(0)
    dates = tools._week_dates(week)
    dinners = ["Bean chili"] + FRESH_DINNERS[1:]
    stub_model(_week(week, dinners))

    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    assert _at(plan_id, dates[0], "dinner")["meal"] == "Moussaka"
    assert "Bean chili" not in _names(plan_id, "dinner")
    assert len(picker) == 1
    # The repeat, and only the repeat: every other night is the model's.
    assert _names(plan_id, "dinner")[1:] == FRESH_DINNERS[1:]
    assert tools.audit_plan_slots(plan_id)["complete"] is True


def test_the_repeat_and_the_window_are_on_avoid_and_named_in_the_ask(last_week, stub_model, picker):
    """
    CATCH, and red on `main` for the right reason by an indirect route:
    it dies on an IndexError reading picker[0], because on `main` the
    picker is never asked at all. That IS the bug and it is not this
    test's own claim, which is about what the ask says.
    """
    week = _monday(0)
    stub_model(_week(week, ["Bean chili"] + FRESH_DINNERS[1:]))
    agent.generate_weekly_plan(week)

    context = picker[0]
    assert context["slot"] == "dinner"
    assert "Bean chili" in context["avoid"]
    assert "Bean chili" in context["replacing_because"]
    assert "last two weeks" in context["replacing_because"]


def test_a_lunch_repeat_goes_too(last_week, stub_model, picker):
    """
    CATCH. The prompt's rule is dinner AND lunch; only dinner was ever
    even detected. Last week's lunch was Greek salad every day.
    """
    week = _monday(0)
    stub_model(_week(week, FRESH_DINNERS, ["Greek salad"] * 7))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    assert "Greek salad" not in _names(plan_id, "lunch")
    assert set(_names(plan_id, "lunch")) == {"Moussaka"}
    # ONE model call for seven nights of one dish: they were the same dish,
    # and a second pick would spend the shared budget to make the week less
    # coherent rather than more.
    assert len(picker) == 1


def test_a_breakfast_or_snack_repeat_is_left_exactly_as_it_is(last_week, stub_model, picker):
    """
    GUARD — green on `main` too, because `main` leaves EVERYTHING alone.
    Pinned instead by the mutation that widens
    meal_variety.NO_REPEAT_SLOTS to every slot, which reddens it.

    Breakfasts and snacks are meant to repeat across weeks: the prompt
    asks for a breakfast two or three times a week, and the same oats
    every morning is the rhythm working.
    """
    week = _monday(0)
    stub_model(_week(week, FRESH_DINNERS, ["Chickpea salad"] * 7))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    assert set(_names(plan_id, "breakfast")) == {"Overnight oats"}
    assert set(_names(plan_id, "snack")) == {"Apple"}
    assert picker == []


# ---------- their words beat the rule ----------

def test_a_repeat_the_model_marked_as_theirs_is_kept(last_week, stub_model, picker):
    """
    GUARD — green on `main` for the other reason (it keeps everything).
    Pinned by the mutation that stops _group_dishes' `protected` being read
    here, which reddens it.

    derived_from.freeform is the model's own record that a slot came from
    the household's words. The week's anchor is not a repeat to repair.
    """
    week = _monday(0)
    dates = tools._week_dates(week)
    tools.save_week_intake(week, freeform="bean chili on Monday please")
    days = _week(week, ["Bean chili"] + FRESH_DINNERS[1:])
    next(d for d in days if d["date"] == dates[0] and d["slot"] == "dinner")["derived_from"] = {
        "freeform": "bean chili on Monday please"
    }
    stub_model(days)

    plan = agent.generate_weekly_plan(week)
    assert _at(plan["weekly_plan_id"], dates[0], "dinner")["meal"] == "Bean chili"
    assert picker == []


def test_a_repeat_they_typed_but_the_model_forgot_to_mark_is_kept_too(last_week, stub_model, picker):
    """
    GUARD, same shape as the one above and worth its own test for the
    reason this module exists at all: telling the generator to stamp
    derived_from.freeform is not the same as preventing it from
    forgetting. Pinned by the mutation that stops repick_recent_repeats
    reading `asks`, which reddens it.

    Nothing marks Monday here; the words say chili and that is enough.
    """
    week = _monday(0)
    dates = tools._week_dates(week)
    tools.save_week_intake(week, freeform="can we have the chili again this week")
    stub_model(_week(week, ["Bean chili"] + FRESH_DINNERS[1:]))

    plan = agent.generate_weekly_plan(week)
    assert _at(plan["weekly_plan_id"], dates[0], "dinner")["meal"] == "Bean chili"
    assert picker == []


def test_the_dish_they_said_they_are_bringing_to_a_holiday_is_kept(last_week, stub_model, picker):
    """
    CATCH against the mutation rather than against `main`. Reproduced
    before it was closed: a household taking a chili they ate eight days
    earlier to a holiday had it swapped away for a dish nobody named.

    holidays._plan_dish writes that row itself, so the model never stamps
    derived_from.freeform on it — their words as plainly, recorded
    somewhere else. Driven by writing the row the way that pass writes it,
    since a holiday inside an arbitrary test week cannot be arranged.
    """
    week = _monday(0)
    dates = tools._week_dates(week)
    stub_model(_week(week, FRESH_DINNERS))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]
    from app.tools import weekly_plan as wp
    wp._replace_slot_entries(
        plan_id, [_at(plan_id, dates[2], "dinner")["entry_id"]], dates[2], "dinner", "Bean chili",
        reasoning="You're bringing it.",
        derived_from={"holiday": "Thanksgiving", "holiday_dish": True, "constraint": "bring_a_dish"},
    )

    before = len(picker)
    out = meal_variety.repick_recent_repeats(plan_id, week, allergen_gate.CallBudget())
    assert out["repeats"] == 1 and out["repicked"] == 0 and out["left"] == ["Bean chili"]
    assert len(picker) == before
    assert _at(plan_id, dates[2], "dinner")["meal"] == "Bean chili"


def test_a_night_already_cooked_is_never_touched(last_week, stub_model, picker):
    """
    NAME — it calls repick_recent_repeats, which `main` has not got. It
    is also red on `main` at an EARLIER assertion than its own: the
    precondition that generation replaced the repeat, which is the first
    test's claim rather than this one's. Pinned by the mutation that stops
    `protected` being read.

    A freshly generated week has no cooked night, so this is driven
    directly: the invariant is the one every pass in this module holds,
    and it costs nothing to keep.
    """
    week = _monday(0)
    dates = tools._week_dates(week)
    stub_model(_week(week, ["Bean chili"] + FRESH_DINNERS[1:]))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]
    # Put the repeat back, by hand, and mark it cooked.
    assert _at(plan_id, dates[0], "dinner")["meal"] == "Moussaka"
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET freeform_meal = 'Bean chili', recipe_id = NULL, "
        "cooked_status = 'done' WHERE id = ?",
        (_at(plan_id, dates[0], "dinner")["entry_id"],),
    )
    conn.commit()
    conn.close()

    before = len(picker)
    out = meal_variety.repick_recent_repeats(plan_id, week, allergen_gate.CallBudget())
    assert out["repeats"] == 1 and out["repicked"] == 0
    assert out["left"] == ["Bean chili"]
    assert len(picker) == before
    assert _at(plan_id, dates[0], "dinner")["meal"] == "Bean chili"


# ---------- no note under the dish ----------

def test_the_replacement_carries_no_note(last_week, stub_model, picker):
    """Emily, 2026-09-25: "no note". The swapped-in dish has an empty
    reason, so the week row offers nothing to tap; what it replaced is
    still on the record for anything reading the plan back."""
    week = _monday(0)
    dates = tools._week_dates(week)
    stub_model(_week(week, ["Bean chili"] + FRESH_DINNERS[1:]))
    plan = agent.generate_weekly_plan(week)

    monday = _at(plan["weekly_plan_id"], dates[0], "dinner")
    assert monday["meal"] != "Bean chili"
    assert (monday["reasoning"] or "") == ""
    assert _derived(monday["entry_id"])["repeat_repick"]["dropped"] == "Bean chili"


def test_the_note_is_empty_by_decision():
    assert meal_variety.REPEAT_REASON == ""


# ---------- the opening line stays truthful ----------

def test_the_opener_says_nothing_from_the_last_two_weeks_only_after_the_repair(last_week, stub_model, picker):
    """
    CATCH. On `main` the same week reads "Seven new dishes; Bean chili and
    Greek salad back from the last two weeks." — the app reporting a rule
    it had just broken. The count is the repaired week's.
    """
    week = _monday(0)
    stub_model(_week(week, ["Bean chili"] + FRESH_DINNERS[1:], ["Greek salad"] * 7))
    plan = agent.generate_weekly_plan(week)

    assert tools.get_week_menu(plan["weekly_plan_id"])["draft_opener"][-1] == \
        "Eight new dishes — nothing from the last two weeks."


def test_a_repeat_the_repair_could_not_better_is_still_reported_honestly(last_week, stub_model, monkeypatch):
    """
    CATCH on the count, GUARD on the sentence. A picker with nothing new to
    offer leaves the dish standing — a week with one repeat beats a lost
    week — and the opener says so rather than claiming a fresh week.
    """
    week = _monday(0)
    stub_model(_week(week, ["Bean chili"] + FRESH_DINNERS[1:], ["Chickpea salad"] * 7))
    tried = []

    def stubborn(context):
        tried.append(context)
        return {"meal_name": "Beef tacos", "reason": "also had it",  # also in the window
                "ingredients": [{"item": "shell", "qty": "1", "category": "pantry"}],
                "instructions": ["Cook.", "Serve."], "food_groups": ["protein", "vegetable", "carb"],
                "prep_time_minutes": 5, "cook_time_minutes": 10}

    monkeypatch.setattr(sip, "_pick_replacement", stubborn)
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    assert len(tried) == sip.MAX_PICK_ATTEMPTS, "each attempt, then the dish stands"
    assert "Beef tacos" in tried[1]["avoid"], "the refused pick joins avoid"
    assert _at(plan_id, tools._week_dates(week)[0], "dinner")["meal"] == "Bean chili"
    assert tools.audit_plan_slots(plan_id)["complete"] is True
    assert tools.get_week_menu(plan_id)["draft_opener"][-1] == \
        "Seven new dishes; Bean chili back from the last two weeks."


# ---------- a chain goes whole, or not at all ----------

def _chained_week(week: str, dish: str) -> list[dict]:
    """A week whose Monday dinner is cooked double for Tuesday's."""
    dates = tools._week_dates(week)
    days = _week(week, [dish, dish] + FRESH_DINNERS[2:])
    monday = next(d for d in days if d["date"] == dates[0] and d["slot"] == "dinner")
    tuesday = next(d for d in days if d["date"] == dates[1] and d["slot"] == "dinner")
    monday["derived_from"] = {"make_double_for": [f"{dates[1]}:dinner"]}
    tuesday["derived_from"] = {"links_to": f"{dates[0]}:dinner"}
    return days


def test_a_cook_once_eat_twice_chain_is_replaced_as_a_whole(last_week, stub_model, picker):
    """
    CATCH. On `main` both nights stay Bean chili. Here both become the one
    replacement, Tuesday still eats from Monday, and no night is left
    reheating a dish nobody is cooking.
    """
    week = _monday(0)
    dates = tools._week_dates(week)
    stub_model(_chained_week(week, "Bean chili"))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    monday = _at(plan_id, dates[0], "dinner")
    tuesday = _at(plan_id, dates[1], "dinner")
    assert monday["meal"] == "Moussaka" and tuesday["meal"] == "Moussaka"
    assert len(picker) == 1, "one ask for the dish, not one per night"

    chains = leftovers.plan_leftover_chains(plan_id)
    assert tuesday["entry_id"] in chains["leftovers"], "still a reheat, and of the new cook"
    assert chains["leftovers"][tuesday["entry_id"]]["source"]["entry_id"] == monday["entry_id"]
    assert monday["entry_id"] in chains["sources"]
    assert _derived(monday["entry_id"])["make_double_for"] == [f"{dates[1]}:dinner"]
    assert tools.audit_plan_slots(plan_id)["complete"] is True


def test_a_chain_reaching_out_of_the_dish_leaves_no_reheat_of_nothing(last_week, stub_model, picker):
    """
    CATCH against the mutation rather than against `main` (on `main`
    nothing moves at all, so it is green there): with _replaceable's
    cross-dish check removed, the dinner is replaced and Tuesday's lunch
    is left reading "Leftovers — Monday's Bean chili" over a Monday
    nobody is cooking chili on. The two ends are looked at on different
    passes and the lunch's own name is not the repeat, so nothing later
    catches it.

    A lunch eating the evening before's dinner is a chain
    repair_leftover_chains confirms, and the dinner is the repeat.
    """
    week = _monday(0)
    dates = tools._week_dates(week)
    days = _week(week, ["Bean chili"] + FRESH_DINNERS[1:], ["Chickpea salad"] * 7)
    monday_dinner = next(d for d in days if d["date"] == dates[0] and d["slot"] == "dinner")
    tuesday_lunch = next(d for d in days if d["date"] == dates[1] and d["slot"] == "lunch")
    monday_dinner["derived_from"] = {"make_double_for": [f"{dates[1]}:lunch"]}
    tuesday_lunch["meal_name"] = "Leftovers — Monday's Bean chili"
    tuesday_lunch["derived_from"] = {"links_to": f"{dates[0]}:dinner"}
    stub_model(days)

    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]
    dinner = _at(plan_id, dates[0], "dinner")
    lunch = _at(plan_id, dates[1], "lunch")

    # Left whole: the chain this pass will not move is the chain it does
    # not touch, and the lunch still names the dish Monday really cooks.
    assert dinner["meal"] == "Bean chili"
    assert "Bean chili" in lunch["meal"]
    assert picker == []
    assert tools.audit_plan_slots(plan_id)["complete"] is True


def test_a_night_eating_from_the_freezer_keeps_the_whole_dish(last_week):
    """
    NAME/CATCH-against-the-mutation. `from_freezer` is not one of
    weekly_plan._CHAIN_KEYS, so a night carrying one would be written
    across verbatim and point at a cook that has gone. Asserted on
    _replaceable directly: the shape takes a freezer night, which nothing
    writes until the batching pass AFTER this one, so a generated week
    cannot produce it to drive through.
    """
    dish = {"name": "Bean chili", "nights": [
        {"id": 1, "date": "2026-10-05", "slot": "dinner", "derived_from_json": None},
        {"id": 2, "date": "2026-10-08", "slot": "dinner",
         "derived_from_json": json.dumps({leftovers.FROM_FREEZER_KEY: {"cook": "entry_id:1", "dish": "Bean chili"}})},
    ]}
    empty = {"sources": {}, "leftovers": {}}
    assert meal_variety._replaceable(dish, empty) is None
    # Without that night it is an ordinary two-cook dish and goes whole.
    dish["nights"] = dish["nights"][:1]
    assert meal_variety._replaceable(dish, empty) == dish["nights"]


# ---------- the window is the opener's window ----------

def test_a_draft_nobody_approved_is_not_food_they_had(stub_model, picker):
    """
    GUARD. draft_opener.recent_dish_names counts approved plans only, and
    this pass reads that function rather than a second query — so a week
    they were offered and sent back is not a repeat to repair.
    """
    previous = _monday(-1)
    stub_model(_week(previous, LAST_WEEK_DINNERS))
    agent.generate_weekly_plan(previous)  # left a DRAFT

    week = _monday(0)
    stub_model(_week(week, ["Bean chili"] + FRESH_DINNERS[1:]))
    plan = agent.generate_weekly_plan(week)
    assert _at(plan["weekly_plan_id"], tools._week_dates(week)[0], "dinner")["meal"] == "Bean chili"
    assert picker == []


def test_a_dish_older_than_the_window_is_not_a_repeat(stub_model, picker):
    """GUARD. Three weeks back is outside VARIETY_WINDOW_WEEKS."""
    old = _monday(-3)
    stub_model(_week(old, LAST_WEEK_DINNERS))
    plan = agent.generate_weekly_plan(old)
    _approve_in_place(plan["weekly_plan_id"])

    week = _monday(0)
    stub_model(_week(week, ["Bean chili"] + FRESH_DINNERS[1:]))
    fresh = agent.generate_weekly_plan(week)
    assert _at(fresh["weekly_plan_id"], tools._week_dates(week)[0], "dinner")["meal"] == "Bean chili"
    assert picker == []


def test_the_repair_and_the_opener_read_one_window(last_week, stub_model, picker):
    """
    NAME. The point of reading draft_opener.recent_dish_names instead of
    re-deriving the window: whatever the opener would call a repeat, the
    repair has already seen. Asserted as the set inclusion it is.
    """
    week = _monday(0)
    stub_model(_week(week, FRESH_DINNERS))
    plan = agent.generate_weekly_plan(week)
    seen = draft_opener.recent_dish_names(week, plan["weekly_plan_id"]) or set()
    assert seen, "last week is approved and inside the window"
    assert {meal_variety.repeat_key(n) for n in seen} >= {n.lower() for n in seen}


# ---------- what counts as the same dish ----------

@pytest.mark.parametrize("drafted", ["Bean Chili", "bean chili!", "Bean chili night", "  Bean   chili  "])
def test_light_normalisation_reads_these_as_the_same_dish(last_week, stub_model, picker, drafted):
    """
    CATCH. Emily, 2026-09-25: exact name plus case, punctuation and a
    trailing word like "night". On `main` every one of these stands.
    """
    week = _monday(0)
    stub_model(_week(week, [drafted] + FRESH_DINNERS[1:]))
    plan = agent.generate_weekly_plan(week)
    assert _at(plan["weekly_plan_id"], tools._week_dates(week)[0], "dinner")["meal"] == "Moussaka"


@pytest.mark.parametrize("drafted", ["Bean chili bowls", "White bean chili", "Chili"])
def test_a_dish_that_merely_shares_words_is_a_different_dish(last_week, stub_model, picker, drafted):
    """
    GUARD, and the reason there is no fuzzy matching: taking a dinner away
    that nobody repeated costs the household a dish they never asked to
    lose. Pinned by the mutation that matches on a shared word.
    """
    week = _monday(0)
    stub_model(_week(week, [drafted] + FRESH_DINNERS[1:]))
    plan = agent.generate_weekly_plan(week)
    assert _at(plan["weekly_plan_id"], tools._week_dates(week)[0], "dinner")["meal"] == drafted
    assert picker == []


def test_repeat_key_is_a_superset_of_the_openers_own_comparison():
    """NAME. The opener lowercases and strips and does nothing else; a key
    narrower than that would let it report a repeat this pass ignored."""
    for name in ["Bean chili", "  Kofte ", "Pad Thai"]:
        assert meal_variety.repeat_key(name) == name.strip().lower()
    assert meal_variety.repeat_key("Taco Night") == "taco"
    assert meal_variety.repeat_key("") == "" and meal_variety.repeat_key(None) == ""


@pytest.mark.parametrize("words,kept", [
    ("bean chili on Monday please", True),
    ("chili again this week", True),
    ("chilli again", False),          # not the same word; nothing to go on
    ("something with beans", False),  # the head word is not the dish
    ("", False),
    (None, False),
])
def test_asked_for_by_name_errs_toward_keeping(words, kept):
    """NAME. Pinned by the mutation that makes it always False, which
    reddens the two protection tests above."""
    assert meal_variety.asked_for_by_name("Bean chili", (words,)) is kept


def test_a_generic_last_word_is_not_a_request(last_week, stub_model, picker):
    """GUARD. "salad on Tuesday" is not "keep the Greek salad" — the head
    word has to say something. Pinned by the mutation that drops
    _TOO_GENERIC_TO_ASK_BY."""
    assert meal_variety.asked_for_by_name("Greek salad", ("salad on Tuesday",)) is False
    assert meal_variety.asked_for_by_name("Greek salad", ("greek salad on Tuesday",)) is True


# ---------- the list follows the dish ----------

def test_the_grocery_list_follows_the_replacement(last_week, stub_model, picker):
    """
    NAME. The pass runs at generation, when nothing has reached the list —
    so the criterion is exercised where it is real: the same call on an
    APPROVED week, which is the shape _replace_slot_entries exists to get
    right. The dropped dish's line goes and the new dish's arrives,
    because this pass owns no grocery code of its own.
    """
    week = _monday(0)
    dates = tools._week_dates(week)
    stub_model(_week(week, ["Lamb stew"] + FRESH_DINNERS[1:], ["Chickpea salad"] * 7))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    def on_list():
        return {i["item"] for i in tools.list_grocery_list(status="all")}

    assert "Lamb stew stuff" in on_list()
    # Now make Monday a repeat and run the pass by hand.
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET freeform_meal = 'Bean chili', recipe_id = NULL WHERE id = ?",
        (_at(plan_id, dates[0], "dinner")["entry_id"],),
    )
    conn.commit()
    conn.close()
    out = meal_variety.repick_recent_repeats(plan_id, week, allergen_gate.CallBudget())

    assert out["repicked"] == 1
    after = on_list()
    assert "Moussaka stuff" in after
    assert "Bean chili stuff" not in after


# ---------- it never costs the week ----------

def test_a_picker_that_raises_leaves_the_week_exactly_as_generated(last_week, stub_model, monkeypatch):
    """GUARD on the outcome, and the rule this whole pass is bound by: a
    week with one repeat beats a lost week."""
    week = _monday(0)
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: (_ for _ in ()).throw(RuntimeError("no")))
    stub_model(_week(week, ["Bean chili"] + FRESH_DINNERS[1:]))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    assert _at(plan_id, tools._week_dates(week)[0], "dinner")["meal"] == "Bean chili"
    assert tools.audit_plan_slots(plan_id)["complete"] is True
    assert all(m["slot_state"] != "open" for m in _meals(plan_id)), "never a slot handed back"


def test_the_window_read_failing_leaves_the_week_as_generated(monkeypatch):
    """NAME. Nothing in here raises out of generation."""
    monkeypatch.setattr(draft_opener, "recent_dish_names",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no window")))
    out = meal_variety.repick_recent_repeats(1, _monday(0), allergen_gate.CallBudget())
    assert out == {"repeats": 0, "repicked": 0, "left": []}


def test_the_shared_budget_bounds_a_week_of_repeats(last_week, stub_model, monkeypatch):
    """
    CATCH-ish (on `main` nothing is spent at all, so it is really a bound
    on what this pass may cost). Every dinner a repeat and a picker that
    only ever offers repeats: the calls stop at the generation's budget
    and the week is still whole.
    """
    week = _monday(0)
    stub_model(_week(week, LAST_WEEK_DINNERS, ["Greek salad"] * 7))
    calls = []

    def stubborn(context):
        calls.append(context)
        return {"meal_name": "Kofte", "reason": "had it",
                "ingredients": [{"item": "kofte stuff", "qty": "1", "category": "pantry"}],
                "instructions": ["Cook.", "Serve."], "food_groups": ["protein", "vegetable", "carb"],
                "prep_time_minutes": 5, "cook_time_minutes": 10}

    monkeypatch.setattr(sip, "_pick_replacement", stubborn)
    plan = agent.generate_weekly_plan(week)

    assert len(calls) <= allergen_gate.MAX_REPICK_CALLS
    assert tools.audit_plan_slots(plan["weekly_plan_id"])["complete"] is True


def test_a_component_plan_is_not_this_passs_business(last_week, stub_model, picker):
    """NAME/GUARD. Component rows carry a component_category and are read
    by nothing here (_load_slot_entries filters them), so a household
    planning by components is left alone."""
    out = meal_variety.repick_recent_repeats(last_week["weekly_plan_id"] + 99, _monday(0),
                                             allergen_gate.CallBudget())
    assert out["repicked"] == 0


# ---------- the morning report ----------

def test_plan_quality_now_names_a_lunch_repeat_as_well_as_a_dinner():
    """
    CATCH. _dinner_repeat_in_history only ever looked at dinner, so a
    lunch back from last week was breached, drafted, and reported by the
    opener with nothing in the report saying so.
    """
    entries = [
        {"date": "2026-10-05", "slot": "dinner", "meal_name": "Bean chili", "slot_state": "planned"},
        {"date": "2026-10-05", "slot": "lunch", "meal_name": "Greek salad", "slot_state": "planned"},
        {"date": "2026-10-05", "slot": "breakfast", "meal_name": "Overnight oats", "slot_state": "planned"},
        {"date": "2026-10-05", "slot": "snack", "meal_name": "Apple", "slot_state": "planned"},
    ]
    history = [
        {"date": "2026-10-01", "slot": "dinner", "meal": "Bean chili"},
        {"date": "2026-10-01", "slot": "lunch", "meal": "Greek salad"},
        {"date": "2026-10-01", "slot": "breakfast", "meal": "Overnight oats"},
        {"date": "2026-10-01", "slot": "snack", "meal": "Apple"},
    ]
    found = plan_quality._dinner_repeat_in_history(entries, {"recent_history": history})
    assert sorted(v.slot for v in found) == ["dinner", "lunch"]
    assert all("history" in v.message for v in found)


def test_a_lunch_repeating_last_weeks_dinner_is_not_the_rule_being_broken():
    """
    GUARD — green on `main`, which only ever read dinner. The comparison is
    within a slot: last week's dinner eaten as this week's lunch is a
    household using a dish up.
    """
    entries = [{"date": "2026-10-05", "slot": "lunch", "meal_name": "Bean chili", "slot_state": "planned"}]
    history = [{"date": "2026-10-01", "slot": "dinner", "meal": "Bean chili"}]
    assert plan_quality._dinner_repeat_in_history(entries, {"recent_history": history}) == []
