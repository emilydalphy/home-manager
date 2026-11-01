"""
Re-planning over an approved week keeps what was already cooked, and
counts what is already in the cart as bought.

Loop Board "Decide: re-planning from today wipes what we already cooked
today — and re-planning mid-shop puts the cart's food on the list again",
Emily 2026-10-31:

1. Option A — a meal ticked done (or whose cook has started) on a day the
   new period covers is carried INTO the new plan, tick and all, in place
   of the new plan's own row for that slot. Before, approving a re-plan
   from Friday deleted Friday's cooked breakfast, lunch and dinner.
2. Yes — approval counts an in-cart line as already bought. Before, the
   salmon in the cart for the old Saturday stayed in the cart AND the new
   week's Saturday put "Salmon fillets 1 lb" on the list to buy.
"""
import datetime

import pytest

from conftest import household_pin, household_today
from app import agent, tools
from app.db import get_conn


def _next_monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)


MON = _next_monday()


def _day(n: int) -> str:
    return (MON + datetime.timedelta(days=n)).isoformat()


FRI, SAT, SUN = _day(4), _day(5), _day(6)

# Friday morning of the seeded week, on the household's clock.
pytestmark = pytest.mark.today(household_pin(10, on=MON + datetime.timedelta(days=4)))


def _setup():
    for n in ("Alex", "Sam"):
        tools.add_member(n)
    tools.add_recipe("Fish Tacos", ingredients=[{"item": "tortillas", "qty": "8"}], default_servings=2)
    tools.add_recipe("Oats", ingredients=[{"item": "rolled oats", "qty": "1 cup"}], default_servings=2)
    tools.add_recipe("Salmon Bake", ingredients=[{"item": "salmon fillets", "qty": "1 lb"}], default_servings=2)
    tools.add_recipe("Chili", ingredients=[{"item": "kidney beans", "qty": "2 tins"}], default_servings=2)


def _approved_old_week(salmon_days=(SAT,)) -> dict:
    plan_id = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    ids = {
        "fri_breakfast": tools.plan_meal(FRI, "Oats", slot="breakfast", weekly_plan_id=plan_id)["entry_id"],
        "fri_dinner": tools.plan_meal(FRI, "Fish Tacos", slot="dinner", weekly_plan_id=plan_id)["entry_id"],
    }
    for d in salmon_days:
        ids[f"{d}_salmon"] = tools.plan_meal(d, "Salmon Bake", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    ids["plan_id"] = plan_id
    return ids


def _stub_model(monkeypatch, menu):
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: menu)


def _draft(start: str, days: int, salmon_on=(SAT,)) -> int:
    menu = [
        {"date": d, "slot": s,
         "meal_name": "Salmon Bake" if (s == "dinner" and d in salmon_on) else "Chili",
         "is_new_recipe": False, "reasoning": "fits"}
        for d in tools.period_dates(start, days) for s in tools.WEEK_SLOTS
    ]
    return menu


def _replan(monkeypatch, start: str, days: int, salmon_on=(SAT,)) -> tuple[int, dict]:
    _stub_model(monkeypatch, _draft(start, days, salmon_on))
    plan = agent.generate_weekly_plan(start, day_count=days, period_start=start, confirm_takeover=True)
    return plan["weekly_plan_id"], plan


def _entry(entry_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT weekly_plan_id, date, slot, cooked_status, cook_started_at FROM meal_plan_entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
    conn.close()
    return row


def _rows_for(plan_id: int, day: str, slot: str):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ? "
        "AND component_category IS NULL",
        (plan_id, day, slot),
    ).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _lines(item: str, status: str | None = None):
    return [
        g for g in tools.list_grocery_list(status="all")
        if g["item"].lower() == item and (status is None or g["status"] == status)
    ]


def test_cooked_meals_carry_into_the_new_plan(monkeypatch):
    _setup()
    old = _approved_old_week()
    tools.check_off_meal(old["fri_breakfast"])
    tools.check_off_meal(old["fri_dinner"])

    new_id, _ = _replan(monkeypatch, FRI, 7)
    tools.approve_weekly_plan(new_id, approved_by="Emily")

    for key in ("fri_breakfast", "fri_dinner"):
        row = _entry(old[key])
        assert row is not None, f"{key} was deleted"
        assert row["weekly_plan_id"] == new_id
        assert row["cooked_status"] == "done"
    # And it is the only row in its slot: the new plan's Chili made way.
    assert _rows_for(new_id, FRI, "dinner") == [old["fri_dinner"]]
    assert _rows_for(new_id, FRI, "breakfast") == [old["fri_breakfast"]]
    audit = tools.audit_plan_slots(new_id, day_count=7)
    assert audit["complete"], audit
    # Friday's lunch was not cooked, so the new plan's own lunch stands.
    assert len(_rows_for(new_id, FRI, "lunch")) == 1


def test_a_started_cook_carries_too_and_is_not_bought_again(monkeypatch):
    _setup()
    old = _approved_old_week()
    tools.start_cooking(old["fri_dinner"])
    tortillas_before = [(g["id"], g["quantity"]) for g in _lines("tortillas")]

    new_id, _ = _replan(monkeypatch, FRI, 7)
    tools.approve_weekly_plan(new_id, approved_by="Emily")

    row = _entry(old["fri_dinner"])
    assert row["weekly_plan_id"] == new_id and row["cook_started_at"]
    assert _rows_for(new_id, FRI, "dinner") == [old["fri_dinner"]]
    # The carried cook's own line is the one it had — nothing bought twice.
    # (Its status may be "carried": an older week's unbought line is set
    # aside for keep-or-drop at every approval, as it always was.)
    assert [(g["id"], g["quantity"]) for g in _lines("tortillas")] == tortillas_before


def test_the_takeover_question_says_the_cooked_meal_stays(monkeypatch):
    _setup()
    old = _approved_old_week()
    tools.check_off_meal(old["fri_dinner"])

    preview = tools.preview_approved_takeover(FRI, 7)
    named = [m["meal_name"] for d in preview["days"] for m in d["meals"]]
    assert "Fish Tacos" not in named
    assert "Fish Tacos is already made, so it stays." in preview["note"]


def test_an_uncooked_meal_is_still_replaced(monkeypatch):
    _setup()
    old = _approved_old_week()

    new_id, _ = _replan(monkeypatch, FRI, 7)
    tools.approve_weekly_plan(new_id, approved_by="Emily")

    assert _entry(old["fri_dinner"]) is None
    assert len(_rows_for(new_id, FRI, "dinner")) == 1


def test_an_in_cart_line_counts_as_bought_on_approval(monkeypatch):
    _setup()
    _approved_old_week()
    salmon = _lines("salmon fillets", "needed")
    assert len(salmon) == 1 and salmon[0]["quantity"] == "1 lb"
    tools.mark_grocery_item(salmon[0]["id"], status="in_cart")

    new_id, _ = _replan(monkeypatch, FRI, 7)
    result = tools.approve_weekly_plan(new_id, approved_by="Emily")

    assert _lines("salmon fillets", "needed") == []
    assert [g["status"] for g in _lines("salmon fillets")] == ["in_cart"]
    assert "salmon fillets" in [n.lower() for n in result["already_have_skipped"]]


def test_cart_food_a_kept_meal_still_needs_is_not_offered(monkeypatch):
    """The old week keeps Friday (today, salmon) and gives up Saturday; the
    in-cart line holds both nights' salmon. Only Saturday's pound is free,
    so a new period wanting two pounds still buys."""
    _setup()
    _approved_old_week(salmon_days=(FRI, SAT))
    salmon = _lines("salmon fillets", "needed")
    assert len(salmon) == 1 and salmon[0]["quantity"] == "2 lbs"
    tools.mark_grocery_item(salmon[0]["id"], status="in_cart")

    new_id, _ = _replan(monkeypatch, SAT, 2, salmon_on=(SAT, SUN))
    tools.approve_weekly_plan(new_id, approved_by="Emily")

    assert [g["quantity"] for g in _lines("salmon fillets", "needed")] == ["2 lbs"]


def test_cart_food_freed_by_the_takeover_covers_a_one_night_need(monkeypatch):
    _setup()
    _approved_old_week(salmon_days=(FRI, SAT))
    salmon = _lines("salmon fillets", "needed")[0]
    tools.mark_grocery_item(salmon["id"], status="in_cart")

    new_id, _ = _replan(monkeypatch, SAT, 2, salmon_on=(SAT,))
    tools.approve_weekly_plan(new_id, approved_by="Emily")

    assert _lines("salmon fillets", "needed") == []
