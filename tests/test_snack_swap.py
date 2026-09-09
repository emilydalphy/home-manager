"""
Julia, first beta tester, 2026-09-08, three complaints about snacks:

(a) "The chat said it changed a snack and it didn't change it in the meal
    plan." Two separate causes, both covered here: get_week_menu — the ONE
    backend ask behind the Meals screen — never returned snacks at all, so
    a swap that really did write had nowhere to show itself; and nothing
    in the chat loop ever checked that a reply claiming a change was a
    reply that made one.
(b) "Gave the same recommendation for breakfast and for snack on the same
    day, or super similar ones."
(c) Snacks should default to two DIFFERENT snacks per day.
"""
from __future__ import annotations

import datetime
import json
import types

import pytest

from app import agent, main as app_main, tools
from app.db import get_conn
from app.tools import plan_quality, preferences


def _week_start() -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


def _week(week: str, snacks: list[str] | None = None, breakfast: str = "Oatmeal") -> list[dict]:
    """
    A complete week the stubbed model "returns": the three guaranteed
    slots every day, plus one entry per name in `snacks` every day. No
    food_groups anywhere, which keeps the full-plate pass (a model call)
    out of these tests — see _complete_plates_pass.
    """
    snacks = ["Apple slices", "Cheese and crackers"] if snacks is None else snacks
    days = []
    for day in tools._week_dates(week):
        for slot in tools.WEEK_SLOTS:
            days.append({
                "date": day, "slot": slot,
                "meal_name": breakfast if slot == "breakfast" else "Chili",
                "is_new_recipe": False, "reasoning": "fits the week",
            })
        for snack in snacks:
            days.append({
                "date": day, "slot": "snack", "meal_name": snack,
                "is_new_recipe": False, "reasoning": "something small",
            })
    return days


@pytest.fixture
def planned_week(monkeypatch):
    """A generated week with two distinct snacks a day."""
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _week(_week_start()))
    plan = agent.generate_weekly_plan(_week_start())
    return plan["weekly_plan_id"]


def _snacks_on(plan_id: int, day: str) -> list[str]:
    return [
        m["meal"] for m in tools.get_weekly_plan(plan_id)["meals"]
        if m["date"] == day and m["slot"] == "snack"
    ]


# ---------- (a) the swap tool itself ----------


def test_a_snack_is_a_slot_the_swap_tool_accepts(planned_week):
    day = tools._week_dates(_week_start())[2]
    tools.swap_meal_in_plan(
        planned_week, day, "Hummus and carrot sticks", slot="snack", old_meal="Apple slices",
    )
    assert sorted(_snacks_on(planned_week, day)) == ["Cheese and crackers", "Hummus and carrot sticks"]


def test_swapping_one_snack_leaves_the_day_s_other_snack_alone(planned_week):
    """The whole reason old_meal exists: a day has two snacks, and a swap
    about one of them must not quietly delete both."""
    day = tools._week_dates(_week_start())[0]
    tools.swap_meal_in_plan(
        planned_week, day, "Trail mix", slot="snack", old_meal="Cheese and crackers",
    )
    assert sorted(_snacks_on(planned_week, day)) == ["Apple slices", "Trail mix"]


def test_a_swap_naming_a_snack_that_isn_t_there_raises_rather_than_adding_a_third(planned_week):
    day = tools._week_dates(_week_start())[0]
    with pytest.raises(ValueError, match="Popcorn"):
        tools.swap_meal_in_plan(
            planned_week, day, "Trail mix", slot="snack", old_meal="Popcorn",
        )
    assert len(_snacks_on(planned_week, day)) == 2


def test_a_slot_that_isn_t_a_slot_is_refused(planned_week):
    day = tools._week_dates(_week_start())[0]
    with pytest.raises(ValueError, match="dessert"):
        tools.swap_meal_in_plan(planned_week, day, "Ice cream", slot="dessert")


def test_the_chat_tool_schema_offers_snack_as_a_slot():
    schema = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "swap_meal_in_plan")
    assert "snack" in schema["input_schema"]["properties"]["slot"]["enum"]
    assert "old_meal" in schema["input_schema"]["properties"]


# ---------- (a) ...and the screen it has to show up on ----------


def test_the_week_menu_carries_the_day_s_snacks(planned_week):
    """The root cause of "it didn't change it in the meal plan": the Meals
    screen's own endpoint dropped every snack row on the floor."""
    day = tools._week_dates(_week_start())[1]
    menu_day = next(d for d in tools.get_week_menu(planned_week)["days"] if d["date"] == day)
    assert [s["title"] for s in menu_day["snacks"]] == ["Apple slices", "Cheese and crackers"]
    assert menu_day["snack"]["title"] == "Apple slices"


def test_a_snack_swap_is_visible_on_the_week_menu_afterwards(planned_week):
    day = tools._week_dates(_week_start())[1]
    tools.swap_meal_in_plan(
        planned_week, day, "Trail mix", slot="snack", old_meal="Apple slices",
    )
    menu_day = next(d for d in tools.get_week_menu(planned_week)["days"] if d["date"] == day)
    assert sorted(s["title"] for s in menu_day["snacks"]) == ["Cheese and crackers", "Trail mix"]


def test_get_weekly_plan_s_menu_lists_both_snacks_too(planned_week):
    day = tools._week_dates(_week_start())[3]
    menu_day = next(d for d in tools.get_weekly_plan(planned_week)["menu"] if d["date"] == day)
    assert menu_day["snacks"] == ["Apple slices", "Cheese and crackers"]
    assert menu_day["snack"] == "Apple slices", "the single key holds the first, not the last"


# ---------- (a) the action card that sends the screen to the right day ----------


def test_a_snack_swap_produces_a_week_card_naming_the_day_and_slot():
    day = tools._week_dates(_week_start())[4]
    before, after = [], [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "swap_meal_in_plan", "input": {
                "weekly_plan_id": 1, "meal_date": day, "new_meal": "Trail mix", "slot": "snack",
            }},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1",
             "content": json.dumps({"status": "ok"}), "is_error": False},
        ]},
    ]
    cards = [a for a in app_main.summarize_chat_actions(before, after) if a.tab == "week"]
    assert len(cards) == 1
    assert (cards[0].date, cards[0].slot) == (day, "snack")


# ---------- (a) a reply that claims a change the turn never made ----------


class _Usage:
    input_tokens = cache_read_input_tokens = cache_creation_input_tokens = output_tokens = 0


def _text(text):
    return types.SimpleNamespace(type="text", text=text)


def _tool(name, tool_input, block_id="tu_1"):
    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def _stub_client(monkeypatch, responses):
    class _Messages:
        def __init__(self, responses):
            self._responses = list(responses)

        def create(self, **kwargs):
            return self._responses.pop(0)

    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=_Messages(responses)))


def _response(content, stop_reason="end_turn"):
    return types.SimpleNamespace(content=content, stop_reason=stop_reason, usage=_Usage())


def test_a_claimed_change_with_no_tool_call_is_rewritten(monkeypatch):
    _stub_client(monkeypatch, [
        _response([_text("Done — I've swapped Tuesday's snack for trail mix.")]),
    ])
    reply, _ = agent.run_agent_turn([], "change Tuesday's snack")
    assert reply == agent.CHANGE_CLAIM_RETRACTION


def test_a_claimed_change_after_a_real_write_is_left_exactly_as_written(monkeypatch, planned_week):
    day = tools._week_dates(_week_start())[5]
    _stub_client(monkeypatch, [
        _response([_tool("swap_meal_in_plan", {
            "weekly_plan_id": planned_week, "meal_date": day, "new_meal": "Trail mix",
            "slot": "snack", "old_meal": "Apple slices",
        })], stop_reason="tool_use"),
        _response([_text("Done — I've swapped that snack for trail mix.")]),
    ])
    reply, _ = agent.run_agent_turn([], "change that snack")
    assert reply == "Done — I've swapped that snack for trail mix."
    assert "Trail mix" in _snacks_on(planned_week, day)


def test_a_claimed_change_whose_tool_call_FAILED_is_rewritten(monkeypatch, planned_week):
    """The worst version of this bug: the write raised, the model absorbed
    the error and wrote a smooth success sentence anyway."""
    day = tools._week_dates(_week_start())[5]
    _stub_client(monkeypatch, [
        _response([_tool("swap_meal_in_plan", {
            "weekly_plan_id": planned_week, "meal_date": day, "new_meal": "Trail mix",
            "slot": "snack", "old_meal": "Something nobody planned",
        })], stop_reason="tool_use"),
        _response([_text("All set — that snack is swapped.")]),
    ])
    reply, _ = agent.run_agent_turn([], "change that snack")
    assert reply == agent.CHANGE_CLAIM_RETRACTION


def test_an_ordinary_answer_that_changed_nothing_is_not_rewritten(monkeypatch):
    _stub_client(monkeypatch, [
        _response([_text("You've got chili on Tuesday and tacos on Wednesday.")]),
    ])
    reply, _ = agent.run_agent_turn([], "what's for dinner this week?")
    assert reply.startswith("You've got chili")


def test_a_read_only_lookup_does_not_count_as_a_write(monkeypatch):
    _stub_client(monkeypatch, [
        _response([_tool("get_meal_plan", {})], stop_reason="tool_use"),
        _response([_text("I've updated that for you.")]),
    ])
    reply, _ = agent.run_agent_turn([], "what's for dinner?")
    assert reply == agent.CHANGE_CLAIM_RETRACTION


# ---------- (b) one day never eats the same thing twice ----------


BREAKFAST_DAY = "2026-09-07"


def _entry(date: str, slot: str, meal: str) -> dict:
    return {
        "date": date, "slot": slot, "slot_state": "planned", "meal_name": meal,
        "reasoning": "something small", "food_groups": [], "main_protein": None,
        "prep_time_minutes": None, "cook_time_minutes": None, "is_new_recipe": True,
        "links_to": None, "ingredients": [],
    }


def _rules(violations) -> set[str]:
    return {v.rule for v in violations}


def test_a_snack_that_repeats_the_day_s_breakfast_is_caught():
    entries = [
        _entry(BREAKFAST_DAY, "breakfast", "Banana pancakes"),
        _entry(BREAKFAST_DAY, "snack", "Banana with peanut butter"),
    ]
    assert "snack_echoes_a_meal" in _rules(plan_quality.check_week(entries, {}))


def test_the_same_snack_on_a_different_day_is_fine():
    entries = [
        _entry(BREAKFAST_DAY, "breakfast", "Banana pancakes"),
        _entry("2026-09-08", "snack", "Banana with peanut butter"),
    ]
    assert _rules(plan_quality.check_week(entries, {})) == set()


def test_two_snacks_in_one_day_that_are_the_same_food_are_caught():
    entries = [
        _entry(BREAKFAST_DAY, "snack", "Yogurt with berries"),
        _entry(BREAKFAST_DAY, "snack", "Berry yogurt cup"),
    ]
    assert "snacks_distinct_per_day" in _rules(plan_quality.check_week(entries, {}))


def test_two_genuinely_different_snacks_pass():
    entries = [
        _entry(BREAKFAST_DAY, "breakfast", "Oatmeal"),
        _entry(BREAKFAST_DAY, "snack", "Apple slices"),
        _entry(BREAKFAST_DAY, "snack", "Cheese and crackers"),
    ]
    assert _rules(plan_quality.check_week(entries, {})) == set()


def test_shared_food_ignores_the_words_every_dish_name_has():
    assert plan_quality.shared_food("Toast with jam", "Apple with peanut butter") is None
    assert plan_quality.shared_food("Apple slices", "Sliced apple") == "apple"


# ---------- (b) ...and the repair ----------


def test_a_breakfast_snack_clash_is_traded_onto_a_day_it_fits(monkeypatch):
    """
    Monday's snack repeats Monday's breakfast, and Tuesday's breakfast is
    something else — so the two snacks change places. A trade rather than
    an invention: the week's own snacks have already been through the
    restrictions, the dislikes and the allergy facts; a hard-coded
    substitute would have been through none of them.
    """
    week = _week_start()
    monday, tuesday = tools._week_dates(week)[0], tools._week_dates(week)[1]
    days = _week(week, snacks=["Apple slices"], breakfast="Oatmeal")
    for day in days:
        if day["date"] == monday and day["slot"] == "snack":
            day["meal_name"] = "Oatmeal cookies"
        if day["date"] == tuesday and day["slot"] == "breakfast":
            day["meal_name"] = "Scrambled eggs on toast"

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert _snacks_on(plan_id, monday) == ["Apple slices"], "the clashing snack moved off Monday"
    assert _snacks_on(plan_id, tuesday) == ["Oatmeal cookies"], "and landed where it doesn't clash"


def test_a_clash_with_no_day_to_move_to_takes_another_day_s_snack_instead(monkeypatch):
    """
    Every breakfast this week is oatmeal, so the oatmeal cookies fit
    nowhere — no trade is possible. The day still must not eat oatmeal
    twice, so another day's snack takes the slot and the cookies are
    dropped. One snack idea on an extra day is a far smaller cost than the
    repeat, and it is still a snack this week already vouched for.
    """
    week = _week_start()
    monday = tools._week_dates(week)[0]
    days = _week(week, snacks=["Apple slices"], breakfast="Oatmeal")
    for day in days:
        if day["date"] == monday and day["slot"] == "snack":
            day["meal_name"] = "Oatmeal cookies"

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert _snacks_on(plan_id, monday) == ["Apple slices"]
    entry = next(
        m for m in tools.get_weekly_plan(plan_id)["meals"]
        if m["date"] == monday and m["slot"] == "snack"
    )
    assert entry["reasoning"], "a repaired slot still owes the card a reason"


def test_two_identical_snacks_in_a_day_are_pulled_apart_at_generation(monkeypatch):
    week = _week_start()
    monday, tuesday = tools._week_dates(week)[0], tools._week_dates(week)[1]
    days = _week(week, snacks=["Apple slices", "Cheese and crackers"], breakfast="Oatmeal")
    for day in days:
        if day["date"] == monday and day["meal_name"] == "Cheese and crackers":
            day["meal_name"] = "Sliced apple"

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    monday_snacks = _snacks_on(plan_id, monday)
    assert len(monday_snacks) == 2
    assert plan_quality.shared_food(*monday_snacks) is None, monday_snacks


def test_a_clash_with_nowhere_to_go_is_left_alone_and_logged(monkeypatch, caplog):
    """Every day's snack is the same food, so no trade fixes anything.
    Honest failure: the week is untouched and the log says why."""
    week = _week_start()
    days = _week(week, snacks=["Oatmeal cookies"], breakfast="Oatmeal")
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)

    with caplog.at_level("WARNING", logger="home_manager"):
        plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert _snacks_on(plan_id, tools._week_dates(week)[0]) == ["Oatmeal cookies"]
    assert "snack_echoes_a_meal" in caplog.text


def test_the_repair_never_raises_into_a_generation(monkeypatch):
    monkeypatch.setattr(plan_quality, "_load_plan_entries", lambda plan_id: 1 / 0)
    assert plan_quality.repair_snack_clashes(999999) == []


# ---------- (c) two different snacks a day, by default ----------


def test_the_default_is_two_snacks_a_day_when_nobody_has_been_asked():
    """snacks_per_week's stored 3 is a column default nobody answered —
    deriving 3/7 -> 0 snacks a day out of it would be inventing an answer,
    and the wrong one."""
    assert tools.resolve_snacks_per_day({"snacks_per_week": 3, "snacks_per_week_set": False}) == 2
    assert preferences.DEFAULT_SNACKS_PER_DAY == 2


def test_an_explicit_snacks_per_day_wins():
    """The onboarding question being added on another branch — read here
    whether or not it exists yet, so neither branch has to land first."""
    memory = {"snacks_per_day": 1, "snacks_per_week": 3, "snacks_per_week_set": False}
    assert tools.resolve_snacks_per_day(memory) == 1
    assert tools.resolve_snacks_per_day({"snacks_per_day": 0}) == 0


@pytest.mark.parametrize("per_week,expected", [
    (0, 0),      # "none, thanks" is a real answer
    (3, 1),      # rounds to nothing, floored at one: they asked for snacks
    (7, 1),
])
def test_an_explicit_weekly_answer_is_spread_across_the_days(per_week, expected):
    memory = {"snacks_per_week": per_week, "snacks_per_week_set": True}
    assert tools.resolve_snacks_per_day(memory) == expected


def test_the_household_s_own_row_answers_when_no_memory_is_passed():
    assert tools.resolve_snacks_per_day() == 2, "an untouched household gets the default"
    tools.set_household_meal_preferences(snacks_per_week=7)
    assert tools.resolve_snacks_per_day() == 1


def test_generation_is_told_how_many_snacks_a_day_to_plan(monkeypatch):
    seen = {}

    def _fake(context):
        seen["context"] = context
        return _week(_week_start())

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", _fake)
    agent.generate_weekly_plan(_week_start())

    assert seen["context"]["household_memory"]["snacks_per_day"] == 2


def test_the_generation_prompt_asks_for_distinct_snacks_per_day(monkeypatch):
    """The prompt half of the same rule — the repair is the backstop, not
    the plan."""
    captured = {}

    def _fake_stream(client, **kwargs):
        captured["text"] = kwargs["content"][0]["text"]
        return []

    monkeypatch.setattr(agent, "_stream_forced_tool_call", _fake_stream)
    monkeypatch.setattr(agent, "_client", lambda: object())
    agent.generate_weekly_plan_llm({"week_start_date": _week_start(), "day_count": 7})

    assert "snacks_per_day" in captured["text"]
    assert "ONE DAY NEVER EATS THE SAME THING TWICE" in captured["text"]


def test_a_household_that_asked_for_no_snacks_still_gets_none(monkeypatch):
    """The zero-count pass predates all of this and must keep working: a
    household that said "none, thanks" is not given two a day."""
    tools.set_household_meal_preferences(snacks_per_week=0)
    week = _week_start()
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    conn = get_conn()
    states = conn.execute(
        "SELECT DISTINCT slot_state FROM meal_plan_entries WHERE weekly_plan_id = ? AND slot = 'snack'",
        (plan_id,),
    ).fetchall()
    conn.close()
    assert [r["slot_state"] for r in states] == ["planned_empty"]
