"""
A takeover that surrenders a reheat night tells the cook night it kept.

When an approved week gives up its last days to a newer period (re-plan
from Thursday, a custom range over the end of next week), a cook night
the old plan KEEPS can have been batched for a reheat night it GIVES UP:
Tuesday's wraps made double so Friday eats the leftovers. The takeover
deleted Friday's row and left Tuesday saying "make double — Friday eats
the leftovers" over a Friday that now belongs to another plan, with the
shopping list still buying the double batch. Every other door that takes
a reheat night away (swap, clear, the "−") unlinks it from its cook
first (weekly_plan._unlink_leftover_target) and re-sizes the cook's line;
the takeover now does the same, inside its own transaction.

And the other way round: a takeover that surrenders the COOK night while a
night it fed stays (the new period takes Monday, the old week keeps
Wednesday) left Wednesday a reheat of a batch nobody would cook, with
nothing on the list for it. The cook now moves onto the first night it
was feeding — Emily's option A (2026-09-22), the "−"'s own answer.
"""
import datetime
import json

from conftest import household_today, on_the_weeks_monday
from app import agent, tools
from app.db import get_conn


def _next_monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)


MON = _next_monday()


def _day(n: int) -> str:
    return (MON + datetime.timedelta(days=n)).isoformat()


TUE, WED, THU, FRI = _day(1), _day(2), _day(3), _day(4)

# The whole seeded week is ahead of the household, so approval buys all of it.
pytestmark = on_the_weeks_monday(MON - datetime.timedelta(days=7))


def _setup():
    for n in ("Alex", "Sam", "Rae"):
        tools.add_member(n)
    tools.add_recipe("Bulgogi Wraps", ingredients=[{"item": "beef", "qty": "1 lb"}], default_servings=3)
    tools.add_recipe("Katsu", ingredients=[{"item": "crackers", "qty": "1 bag"}], default_servings=3)


def _approved_week_with_a_chain(target_day: str) -> tuple[int, int, int]:
    plan_id = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    tue = tools.plan_meal(TUE, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    reheat = tools.plan_meal(
        target_day, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
        derived_from={"links_to": f"{TUE}:dinner"},
    )["entry_id"]
    tools.repair_leftover_chains(plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id, tue, reheat


def _derived(entry_id: int) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return json.loads(row["derived_from_json"] or "{}")


def _beef() -> str:
    lines = [g for g in tools.list_grocery_list() if g["item"] == "beef"]
    assert len(lines) == 1, lines
    return lines[0]["quantity"]


def _take_over_from(start: str, days: int, monkeypatch):
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: [
        {"date": d, "slot": s, "meal_name": "Katsu", "is_new_recipe": False, "reasoning": "fits"}
        for d in tools.period_dates(start, days) for s in tools.WEEK_SLOTS
    ])
    plan = agent.generate_weekly_plan(start, day_count=days, period_start=start, confirm_takeover=True)
    return tools.approve_weekly_plan(plan["weekly_plan_id"], approved_by="Emily")


def test_the_kept_cook_stops_making_double_for_a_surrendered_night(monkeypatch):
    _setup()
    plan_id, tue, fri = _approved_week_with_a_chain(FRI)
    assert _derived(tue)["make_double_for"] == [f"{FRI}:dinner"]
    assert _beef() == "2 lbs"  # Tuesday's table plus Friday's reheat

    approval = _take_over_from(THU, 4, monkeypatch)
    assert FRI in approval["took_over"]["surrendered_dates"]

    derived = _derived(tue)
    assert "make_double_for" not in derived
    assert "make_double_note" not in derived
    # Tuesday is an ordinary cook again, and the list buys for it alone.
    assert _beef() == "1 lb"


def test_a_chain_wholly_inside_the_kept_days_is_untouched(monkeypatch):
    _setup()
    plan_id, tue, wed = _approved_week_with_a_chain(WED)

    _take_over_from(THU, 4, monkeypatch)

    assert _derived(tue)["make_double_for"] == [f"{WED}:dinner"]
    assert _beef() == "2 lbs"


def test_a_double_batch_already_bought_is_not_bought_again(monkeypatch):
    _setup()
    plan_id, tue, fri = _approved_week_with_a_chain(FRI)
    beef = next(g for g in tools.list_grocery_list() if g["item"] == "beef")
    tools.mark_grocery_item(beef["id"], status="purchased")

    _take_over_from(THU, 4, monkeypatch)

    assert "make_double_for" not in _derived(tue)
    # Somebody owns the two pounds; re-sizing Tuesday's share never puts
    # beef back on the list to buy.
    assert not [g for g in tools.list_grocery_list(status="needed") if g["item"] == "beef"]


def _approved_week_cooking_monday_for(targets: list[str]) -> tuple[int, int, list[int]]:
    plan_id = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    mon = tools.plan_meal(MON.isoformat(), "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    fed = [
        tools.plan_meal(day, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
                        derived_from={"links_to": f"{MON.isoformat()}:dinner"})["entry_id"]
        for day in targets
    ]
    tools.repair_leftover_chains(plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id, mon, fed


def _row(entry_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return row


def test_a_surrendered_cook_moves_onto_the_night_it_was_feeding(monkeypatch):
    _setup()
    plan_id, mon, (wed,) = _approved_week_cooking_monday_for([WED])
    assert _beef() == "2 lbs"

    _take_over_from(MON.isoformat(), 2, monkeypatch)

    # Wednesday is the cook now — the same dish, cooked fresh, not a
    # reheat of a Monday that belongs to another plan.
    cook = _row(mon)
    assert cook is not None and cook["date"] == WED and cook["weekly_plan_id"] == plan_id
    assert _row(wed) is None
    assert "links_to" not in _derived(mon)
    # ...and the list buys for Wednesday's table alone.
    assert _beef() == "1 lb"


def test_a_moved_cook_still_feeds_the_nights_after_it(monkeypatch):
    _setup()
    plan_id, mon, (tue, wed) = _approved_week_cooking_monday_for([TUE, WED])

    _take_over_from(MON.isoformat(), 1, monkeypatch)

    assert _row(mon)["date"] == TUE
    assert _derived(mon)["make_double_for"] == [f"{WED}:dinner"]
    assert _derived(wed)["links_to"] == f"{TUE}:dinner"
    assert _beef() == "2 lbs"  # Tuesday's table plus Wednesday's reheat
