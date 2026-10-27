"""
Two freezer-portion calls Emily went with on 2026-10-26.

#28 — a cooked portion past its use-by was never mentioned. The planner
leaves it off every plate (portions_to_plan), so nothing said it was there.
The draft now carries one plain line suggesting it's tossed.

#32 — a night off loaded a cook with a portion for the freezer; a later week
was already planned with a night eating that portion; then the cook was
answered "We skipped it". That night pointed at food that doesn't exist,
silently. Now the skip asks — on the attention queue, answered on Today —
"Plan something else for <night>?", and only re-plans on a yes.

Every model call is stubbed; nothing reaches the network.
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import freezer_portions as _fp

from test_second_week_seams import _approved, _freezer, _night_off_onto_the_fed_night


# ---------- #28: past its use-by ----------

def _set_use_by(item_id: int, use_by: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET expiration_date = ? WHERE id = ?", (use_by, item_id))
    conn.commit()
    conn.close()


def test_a_portion_past_its_use_by_gets_a_line():
    _plan, _cook, today, _d1 = _night_off_onto_the_fed_night()
    [row] = _freezer()
    assert _fp.past_use_by_line() is None, "a good portion was called out"

    _set_use_by(row["id"], (today - datetime.timedelta(days=3)).isoformat())

    assert _fp.past_use_by_line() == "Pasta Primavera in the freezer is past its use-by — best to toss it."


def test_the_line_rides_on_a_draft_not_an_approved_week():
    _plan, _cook, today, _d1 = _night_off_onto_the_fed_night()
    [row] = _freezer()
    _set_use_by(row["id"], (today - datetime.timedelta(days=3)).isoformat())
    start = (today + datetime.timedelta(days=7)).isoformat()
    draft = tools.create_weekly_plan(start, day_count=2)["weekly_plan_id"]

    assert tools.get_week_menu(draft)["freezer_toss_note"].startswith("Pasta Primavera in the freezer")
    approved = _approved((today + datetime.timedelta(days=14)).isoformat(), 2)
    assert tools.get_week_menu(approved)["freezer_toss_note"] is None


def test_two_portions_are_said_together():
    rows = [{"dish": "Chili", "expiration_date": "2000-01-01"},
            {"dish": "Dal", "expiration_date": "2000-01-01"}]
    original = _fp._frozen_rows
    try:
        _fp._frozen_rows = lambda conn: rows
        assert _fp.past_use_by_line() == "Chili and Dal in the freezer are past their use-by — best to toss them."
    finally:
        _fp._frozen_rows = original


def test_the_draft_shows_the_line():
    src = open("static/shell.js", encoding="utf-8").read()
    start = src.index("function draftSoftNoteHtml(")
    assert "data.freezer_toss_note" in src[start:src.index("function weekNotesHtml(", start)]


# ---------- #32: a skipped cook a later night counts on ----------

def _later_night_on_the_portion(today):
    [row] = _freezer()
    night = (today + datetime.timedelta(days=2)).isoformat()
    later = _approved(night, 1)
    [placed] = _fp.apply_to_plan(later, [{"inventory_item_id": row["id"], "dish": "Pasta Primavera",
                                          "quantity": row["quantity"], "on": night}])
    return later, night


def _questions():
    return [i for i in tools.get_attention_items() if i["kind"] == _fp.MISSING_PORTION_KIND]


def _dinner(plan_id: int, day: str) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT mpe.*, COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
        (plan_id, day)).fetchone()
    conn.close()
    return dict(row)


def _picker(context):
    return {"meal_name": "Lemon Chicken", "reason": "a quick one",
            "ingredients": [{"item": "chicken", "qty": "1 lb", "category": "meat/seafood"}],
            "instructions": ["Cook the chicken.", "Serve it."], "food_groups": ["protein"],
            "prep_time_minutes": 5, "cook_time_minutes": 20}


def test_skipping_the_cook_asks_about_the_later_night():
    _plan, cook, today, _d1 = _night_off_onto_the_fed_night()
    later, night = _later_night_on_the_portion(today)
    before = _dinner(later, night)

    tools.answer_yesterday(cook, "skipped", today)

    [q] = _questions()
    weekday = datetime.date.fromisoformat(night).strftime("%A")
    assert q["summary"] == (f"Pasta Primavera was skipped, so there’s no frozen portion for {weekday}’s dinner. "
                            f"Plan something else for {weekday}?")
    assert _dinner(later, night)["id"] == before["id"], "it re-planned without asking"


def test_plan_something_else_re_plans_the_night_and_closes_the_question():
    _plan, cook, today, _d1 = _night_off_onto_the_fed_night()
    later, night = _later_night_on_the_portion(today)
    tools.answer_yesterday(cook, "skipped", today)
    [q] = _questions()

    out = _fp.replan_missing_portion(q["id"], picker=_picker)

    assert out == {"status": "filled", "meal": "Lemon Chicken", "date": night}
    assert _dinner(later, night)["meal"] == "Lemon Chicken"
    assert _fp.portion_on(_dinner(later, night)["derived_from_json"]) is None
    assert _questions() == []


def test_ticking_it_cooked_after_all_settles_the_question():
    _plan, cook, today, _d1 = _night_off_onto_the_fed_night()
    later, night = _later_night_on_the_portion(today)
    tools.answer_yesterday(cook, "skipped", today)
    assert len(_questions()) == 1

    tools.check_off_meal(cook, "done")

    assert _questions() == [], "the portion is real again, but the question still stands"
    assert _fp.portion_on(_dinner(later, night)["derived_from_json"]), "the night was touched"


def test_a_question_that_no_longer_stands_writes_nothing():
    _plan, cook, today, _d1 = _night_off_onto_the_fed_night()
    later, night = _later_night_on_the_portion(today)
    tools.answer_yesterday(cook, "skipped", today)
    [q] = _questions()
    tools.check_off_meal(cook, "done")
    before = _dinner(later, night)

    out = _fp.replan_missing_portion(q["id"], picker=_picker)

    assert out["status"] == "refused"
    assert _dinner(later, night) == before


def test_a_skip_with_no_later_night_asks_nothing():
    _plan, cook, today, _d1 = _night_off_onto_the_fed_night()
    tools.answer_yesterday(cook, "skipped", today)
    assert _questions() == []


def test_the_route(signed_in, monkeypatch):
    from app.tools import swap_in_place
    _plan, cook, today, _d1 = _night_off_onto_the_fed_night()
    later, night = _later_night_on_the_portion(today)
    tools.answer_yesterday(cook, "skipped", today)
    [q] = _questions()
    monkeypatch.setattr(swap_in_place, "_pick_replacement", _picker)

    res = signed_in.post(f"/api/attention/{q['id']}/replan-night")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "filled" and body["meal"] == "Lemon Chicken"
    assert not [i for i in body["items"] if i["kind"] == _fp.MISSING_PORTION_KIND]


def test_today_answers_it_with_two_buttons():
    src = open("static/shell.js", encoding="utf-8").read()
    start = src.index("function renderUseSoon(")
    body = src[start:src.index("async function resolveUseSoon(", start)]
    assert "Plan something else" in body and "Leave it" in body
    assert "/replan-night" in body
