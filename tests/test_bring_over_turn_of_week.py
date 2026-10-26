"""
Bring over from last week, at the turn of the week.

Two defects, both found by planning Monday's week on the household's own
clock (America/Toronto) and shopping it the day before:

1. A dish brought over was bought twice. The new week's ingest buys what
   last week's list didn't (bring_over.bought_items), but last week's
   unbought lines for the same dish stayed: approved and shopped on the
   Sunday, Shop showed "Chicken tikka masala base" for both weeks, one
   tikka; approved on the Monday, it came back as "Still on the list from
   last week — keep?". Approval now takes the brought-over meal's share
   off last week's lines still to buy (bring_over.release_last_weeks_share).

2. Planned late on the week's first day, the brought-over dish went on
   tonight's dinner, which had already gone by — choose_nights never heard
   about today's gone meals, and apply_to_plan wrote over the "already gone
   by" row. Now those slots are not open to it (nor to a freezer portion).

Dates are fixed (last week Mon 12 Oct 2026, this week Mon 19 Oct) and every
hour is the HOUSEHOLD's (conftest.household_pin), so the tests read the same
in any process zone.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import bring_over
from conftest import household_pin
from test_bring_over import FRESH_DINNERS, LAST_DINNERS, _week, picker, stub_model  # noqa: F401

LAST = "2026-10-12"
NEW = "2026-10-19"
SUNDAY = datetime.date(2026, 10, 18)
MONDAY = datetime.date(2026, 10, 19)
LAST_LUNCHES = [f"Lunch bowl {i}" for i in range(7)]
NEW_LUNCHES = [f"Grain salad {i}" for i in range(7)]


def _lines(name: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM grocery_items WHERE LOWER(item) = LOWER(?) ORDER BY id", (name,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _to_buy(name: str) -> list[dict]:
    return [r for r in _lines(name) if r["status"] == "needed"]


def _last_week(stub_model, frozen_today, days=None) -> int:
    frozen_today(household_pin(8, on=datetime.date(2026, 10, 12)))
    tools.save_week_intake(LAST, moods=["Comfort food"])
    stub_model(days or _week(LAST, LAST_DINNERS, LAST_LUNCHES, snacks=["Fig bars"] * 7))
    plan = agent.generate_weekly_plan(LAST)["weekly_plan_id"]
    tools.approve_weekly_plan(plan)
    return plan


def _bring_tikka() -> dict:
    offer = tools.get_week_intake_prefill(NEW)["last_week_uncooked"]
    tikka = next(o for o in offer if o["meal"] == "Chicken tikka masala")
    tools.save_week_intake(NEW, brought_over=[{"entry_ids": tikka["entry_ids"]}])
    return tikka


def _new_week(stub_model) -> int:
    stub_model(_week(NEW, FRESH_DINNERS, NEW_LUNCHES))
    return agent.generate_weekly_plan(NEW)["weekly_plan_id"]


def _buy(entry_ids: list[int], item: str) -> None:
    conn = get_conn()
    marks = ",".join("?" * len(entry_ids))
    row = conn.execute(
        f"SELECT DISTINCT grocery_item_id FROM meal_plan_grocery_links WHERE LOWER(item) = LOWER(?) "
        f"AND meal_plan_entry_id IN ({marks})", (item, *entry_ids),
    ).fetchone()
    conn.close()
    tools.mark_grocery_item(row["grocery_item_id"], "purchased")
    # The tick also stocks the kitchen (beta), which the ingest reads on
    # its own; cleared so this proves the bring-over's reading.
    conn = get_conn()
    conn.execute("DELETE FROM inventory_items")
    conn.commit()
    conn.close()


class TestBoughtOnce:
    def test_approved_and_shopped_the_sunday_before_the_dish_is_on_the_list_once(
        self, stub_model, picker, frozen_today,
    ):
        last = _last_week(stub_model, frozen_today)
        # Sunday 9:30pm in Toronto — already Monday on a UTC server.
        frozen_today(household_pin(21, 30, on=SUNDAY))
        _bring_tikka()
        new = _new_week(stub_model)
        tools.approve_weekly_plan(new)

        for item in ("Chicken tikka masala base", "Chicken tikka masala greens"):
            assert [r["source_weekly_plan_id"] for r in _to_buy(item)] == [new], item
        # Sunday's own dinner is still last week's to cook tonight: untouched.
        assert [r["source_weekly_plan_id"] for r in _to_buy("Pad thai base")] == [last]
        # And a missed dinner nobody brought over is still last week's.
        assert [r["source_weekly_plan_id"] for r in _to_buy("Bean chili base")] == [last]

    def test_approved_on_the_monday_it_is_not_asked_about_as_last_weeks(
        self, stub_model, picker, frozen_today,
    ):
        _last_week(stub_model, frozen_today)
        frozen_today(household_pin(16, on=SUNDAY))
        _bring_tikka()
        new = _new_week(stub_model)
        frozen_today(household_pin(9, on=MONDAY))
        tools.approve_weekly_plan(new)

        carried = {c["item"] for c in tools.list_carried_over_items()}
        assert "Chicken tikka masala base" not in carried
        assert "Chicken tikka masala greens" not in carried
        assert "Bean chili base" in carried  # the rest of last week is still asked
        assert [r["source_weekly_plan_id"] for r in _to_buy("Chicken tikka masala base")] == [new]

    def test_what_last_week_bought_stays_bought_and_is_not_bought_again(
        self, stub_model, picker, frozen_today,
    ):
        last = _last_week(stub_model, frozen_today)
        frozen_today(household_pin(15, on=SUNDAY))
        tikka_ids = _bring_tikka()["entry_ids"]
        _buy(tikka_ids, "Chicken tikka masala base")
        new = _new_week(stub_model)
        tools.approve_weekly_plan(new)

        base = _lines("Chicken tikka masala base")
        assert [(r["status"], r["source_weekly_plan_id"]) for r in base] == [("purchased", last)]
        # The ticked line keeps its ledger row: it is bought_items' evidence,
        # read again by the re-buy after any swap.
        assert bring_over.bought_items(tikka_ids) == {"chicken tikka masala base"}
        assert [r["source_weekly_plan_id"] for r in _to_buy("Chicken tikka masala greens")] == [new]

    def test_a_line_another_missed_meal_shares_keeps_that_meals_amount(
        self, stub_model, picker, frozen_today,
    ):
        days = _week(LAST, LAST_DINNERS, LAST_LUNCHES, snacks=["Fig bars"] * 7)
        for d in days:
            if d["slot"] == "dinner" and d["meal_name"] in ("Chicken tikka masala", "Kofte"):
                d["ingredients"] = d["ingredients"] + [{"item": "Shallots", "qty": "3", "category": "produce"}]
        last = _last_week(stub_model, frozen_today, days=days)
        assert [r["quantity"] for r in _to_buy("Shallots")] == ["6"]
        frozen_today(household_pin(15, on=SUNDAY))
        _bring_tikka()
        new = _new_week(stub_model)
        tools.approve_weekly_plan(new)

        by_plan = {r["source_weekly_plan_id"]: r["quantity"] for r in _to_buy("Shallots")}
        assert by_plan == {last: "3", new: "3"}  # Kofte's on last week's, tikka's on this

    def test_no_brought_over_meal_changes_nothing(self, stub_model, picker, frozen_today):
        last = _last_week(stub_model, frozen_today)
        frozen_today(household_pin(15, on=SUNDAY))
        new = _new_week(stub_model)
        tools.approve_weekly_plan(new)
        assert [r["source_weekly_plan_id"] for r in _to_buy("Chicken tikka masala base")] == [last]


@pytest.mark.real_time_of_day
class TestNotOnAGoneNight:
    def _dinners(self, plan_id: int) -> dict[str, dict]:
        conn = get_conn()
        rows = conn.execute(
            "SELECT mpe.date, mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.derived_from_json "
            "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
            "WHERE mpe.weekly_plan_id = ? AND mpe.slot = 'dinner' AND mpe.component_category IS NULL",
            (plan_id,),
        ).fetchall()
        conn.close()
        return {r["date"]: dict(r) for r in rows}

    def test_planned_after_dinner_on_the_first_day_it_goes_on_tomorrow(
        self, stub_model, picker, frozen_today,
    ):
        _last_week(stub_model, frozen_today)
        # Monday 9:45pm in Toronto (1:45am Tuesday on a UTC server): the
        # week starts today and tonight's dinner has gone by.
        frozen_today(household_pin(21, 45, on=MONDAY))
        _bring_tikka()
        new = _new_week(stub_model)
        dinners = self._dinners(new)
        assert dinners["2026-10-19"]["slot_state"] == "planned_empty"
        assert json.loads(dinners["2026-10-19"]["derived_from_json"])["constraint"] == "already_past"
        assert dinners["2026-10-20"]["meal"] == "Chicken tikka masala"
        assert bring_over.is_brought_over(json.loads(dinners["2026-10-20"]["derived_from_json"]))

    def test_planned_that_afternoon_it_still_goes_on_tonight(self, stub_model, picker, frozen_today):
        _last_week(stub_model, frozen_today)
        frozen_today(household_pin(15, on=MONDAY))
        _bring_tikka()
        new = _new_week(stub_model)
        assert self._dinners(new)["2026-10-19"]["meal"] == "Chicken tikka masala"

    def test_choose_nights_passes_over_a_gone_slot(self):
        item = {"entry_ids": [1], "meal": "Kofte", "recipe_id": None, "slot": "dinner", "date": "2026-10-13"}
        placed, _ = bring_over.choose_nights(
            [item], ["2026-10-19", "2026-10-20"], {}, gone={("2026-10-19", "dinner")},
        )
        assert placed[0]["on"] == "2026-10-20"


def test_a_brought_over_night_gone_by_at_approval_leaves_last_weeks_line(stub_model, picker, frozen_today):
    """Approval buys nothing for a night already gone by, so last week's
    line is still the only one and is not taken off."""
    last = _last_week(stub_model, frozen_today)
    frozen_today(household_pin(15, on=SUNDAY))
    _bring_tikka()
    new = _new_week(stub_model)  # tikka on Monday
    frozen_today(household_pin(9, on=MONDAY + datetime.timedelta(days=1)))
    tools.approve_weekly_plan(new)
    assert [r["source_weekly_plan_id"] for r in _lines("Chicken tikka masala base")
            if r["status"] in ("needed", "carried")] == [last]
