"""
A dinner whose cook has started stays on its night (overnight 2026-10-14).

Found by driving the app: once "Start cooking" was tapped on tonight's
dinner (meal_plan_entries.cook_started_at — the signal the evening nudge
already goes quiet on), Plan → Move still offered to trade it with another
night, the nights swap said yes, and doing it moved a dinner already on
the stove. A cooked dinner has always been refused there; a started one is
now refused by the same rule, and "Mark not cooked" (which clears the
start) makes it movable again.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import cooker, meal_move

# A Wednesday afternoon, so tonight has later nights in its own week.
pytestmark = pytest.mark.today("2026-10-14 16:00")


def _week():
    from conftest import household_today
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    days = tools._week_dates(monday.isoformat())
    return monday.isoformat(), today.isoformat(), days


def _setup():
    week, today, days = _week()
    later = [d for d in days if d > today]
    for name in ("Chicken Skewers", "Bean Chili", "Salmon Bowls"):
        tools.add_recipe(name, ingredients=[{"item": f"{name} onion", "qty": "1", "category": "produce"}],
                         prep_time_minutes=10, cook_time_minutes=25, default_servings=4)
    plan = tools.create_weekly_plan(week)["weekly_plan_id"]
    tonight = tools.plan_meal(today, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)["entry_id"]
    other = tools.plan_meal(later[0], "Bean Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    third = tools.plan_meal(later[1], "Salmon Bowls", slot="dinner", weekly_plan_id=plan)["entry_id"]
    return {"plan": plan, "today": today, "later": later,
            "tonight": tonight, "other": other, "third": third}


def _date_of(entry_id: int) -> str:
    conn = get_conn()
    row = conn.execute("SELECT date FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return row["date"]


def _start(w):
    cooker.start_cooking(w["tonight"])
    conn = get_conn()
    started = conn.execute("SELECT cook_started_at FROM meal_plan_entries WHERE id = ?",
                           (w["tonight"],)).fetchone()["cook_started_at"]
    conn.close()
    assert started, "start_cooking should stamp tonight's dinner"


def test_a_started_dinner_is_not_offered_a_move_and_the_move_is_refused():
    w = _setup()
    _start(w)

    days = {d["date"]: d for d in meal_move.move_options(w["plan"], w["tonight"])["days"]}
    for d in w["later"][:2]:
        assert days[d]["ok"] is False
        assert days[d]["reason"] == "Already cooking"

    out = meal_move.move_meal(w["plan"], w["tonight"], w["later"][0])
    assert out["status"] == "refused"
    assert _date_of(w["tonight"]) == w["today"]


def test_no_other_dinner_is_moved_onto_a_night_whose_cook_has_started():
    w = _setup()
    _start(w)

    days = {d["date"]: d for d in meal_move.move_options(w["plan"], w["other"])["days"]}
    assert days[w["today"]]["ok"] is False
    assert days[w["today"]]["reason"] == "Already cooking"
    # The night that hasn't started is untouched by the rule.
    assert days[w["later"][1]]["ok"] is True

    out = meal_move.move_meal(w["plan"], w["other"], w["today"])
    assert out["status"] == "refused"
    assert _date_of(w["tonight"]) == w["today"]


def test_the_nights_swap_and_chat_refuse_a_started_dinner_in_a_plain_sentence():
    w = _setup()
    _start(w)

    out = tools.swap_dinner_nights(w["plan"], w["today"], w["later"][0])
    assert out["status"] == "refused"
    assert out["message"].startswith("Chicken Skewers on ")
    assert "already cooking" in out["message"] and "leave that one where it is" in out["message"]
    assert _date_of(w["tonight"]) == w["today"]


def test_mark_not_cooked_clears_the_start_and_it_moves_again():
    w = _setup()
    _start(w)
    tools.check_off_meal(w["tonight"], "pending")

    days = {d["date"]: d for d in meal_move.move_options(w["plan"], w["tonight"])["days"]}
    assert days[w["later"][0]]["ok"] is True
    assert tools.swap_dinner_nights(w["plan"], w["today"], w["later"][0])["status"] == "swapped"
    assert _date_of(w["tonight"]) == w["later"][0]


def test_an_unstarted_tonight_still_moves():
    w = _setup()
    out = meal_move.move_meal(w["plan"], w["tonight"], w["later"][0])
    assert out["status"] == "moved"
    assert _date_of(w["tonight"]) == w["later"][0]


# ------------------------------------------------ the review's four doors
# (independent review of 34fbd520: three more ways a started cook moved, and
# a past night that read "Already cooking")

import json  # noqa: E402

from app.tools import tonight as _tonight  # noqa: E402

CHICKEN = "Seared Garlic Chicken Thighs"


def _full_week_chicken_feeds_a_later_night():
    """Every night planned (no free night), tonight's chicken cooked double
    for the next night — the night off's 'cook_on_fed' case."""
    week, today, days = _week()
    fed = [d for d in days if d > today][0]
    for name in ("Emily", "Julia"):
        tools.add_member(name)
    plan = tools.create_weekly_plan(week)["weekly_plan_id"]
    for day in days:
        if day == fed:
            tools.plan_meal(day, CHICKEN, slot="dinner", weekly_plan_id=plan,
                            derived_from={"links_to": f"{today}:dinner"})
            continue
        dish = CHICKEN if day == today else f"Filler {day}"
        tools.add_recipe(dish, ingredients=[{"item": f"Main for {dish}", "qty": "1 lb", "category": "meat"}],
                         prep_time_minutes=10, cook_time_minutes=25, default_servings=2)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    tools.repair_leftover_chains(plan)
    tools.approve_weekly_plan(plan)
    conn = get_conn()
    tonight = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE date = ? AND slot = 'dinner' AND household_id = ?",
        (today, tools.household_id())).fetchone()["id"]
    conn.close()
    assert tonight in tools.plan_leftover_chains(plan)["sources"]
    return plan, tonight, today


def _rows():
    conn = get_conn()
    out = [tuple(r) for r in conn.execute("SELECT * FROM meal_plan_entries ORDER BY id").fetchall()]
    conn.close()
    return out


def test_a_night_off_does_not_carry_a_started_cook_onto_the_night_it_feeds():
    plan, tonight, today = _full_week_chicken_feeds_a_later_night()
    cooker.start_cooking(tonight)
    before = _rows()

    out = _tonight.tonight_night_off()

    assert out["status"] == "refused"
    assert out["message"] == f"The {CHICKEN} is already cooking, so it stays on tonight."
    assert _rows() == before
    assert _date_of(tonight) == today


def test_the_review_minus_does_not_move_a_started_cook_onto_its_fed_night():
    plan, tonight, today = _full_week_chicken_feeds_a_later_night()
    cooker.start_cooking(tonight)
    before = _rows()

    out = tools.drop_dish_from_day(plan, tonight)

    assert out["status"] == "refused"
    assert "is already cooking — I’ll leave that one on the week" in out["message"]
    assert _rows() == before


def test_the_week_payload_says_cooking_and_the_move_button_reads_it():
    w = _setup()
    assert '"cooking": true' not in json.dumps(tools.get_week_menu(w["plan"]))
    _start(w)
    assert '"cooking": true' in json.dumps(tools.get_week_menu(w["plan"]))
    from pathlib import Path
    shell = (Path(__file__).resolve().parents[1] / "static" / "shell.js").read_text(encoding="utf-8")
    body = shell.split("function wkCanMove(", 1)[1].split("\n  }\n", 1)[0]
    assert "entry.cooking" in body, "a started dinner gets no Move button, same as a cooked one"


def test_a_past_night_reads_gone_by_not_cooking():
    w = _setup()
    _, today, days = _week()
    monday = days[0]
    tools.add_recipe("Old Soup", ingredients=[{"item": "Soup onion", "qty": "1", "category": "produce"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=4)
    past = tools.plan_meal(monday, "Old Soup", slot="dinner", weekly_plan_id=w["plan"])["entry_id"]
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cook_started_at = ? WHERE id = ?", (f"{monday}T18:00:00", past))
    conn.commit()
    conn.close()

    days_out = {d["date"]: d for d in meal_move.move_options(w["plan"], past)["days"]}
    assert days_out[w["later"][0]]["reason"] == "Already gone by"
