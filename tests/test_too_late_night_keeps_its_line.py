"""
The freezer step, for a night it is already too late to thaw for
(2026-10-10, overnight defect hunt).

Reproduced over HTTP on a throwaway database before anything changed:

  * A week approved the day before it starts, chicken thighs (a 48-hour
    thaw) tomorrow and four days out. The step offered only the later
    night — right — but a yes set the WHOLE "Chicken thighs 4 lbs" line
    aside, so tomorrow's 2 lb was neither thawed nor bought, and the toast
    said "Too late to thaw safely for tonight" about tomorrow's dinner.
  * Reopening the step mid-week with chicken eaten two days ago toasted
    "Too late to thaw safely for tonight… or cook something else" about
    that long-gone dinner, on a night whose dinner was bean chili.

Now: a night still ahead that the freezer cannot reach keeps its line on
the list (the move alone is booked, the way a line another week shares
already does), the step says so (`on_list` False, ON_LIST_TOO_LATE), and
the note names the night it is about. A night already eaten, cooked or
thawed on time says nothing.
"""
import datetime

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import defrost
from conftest import household_today

ITEM = "Chicken Thighs"  # "chicken thigh" is the 48-hour tier


def _seed(start: datetime.date, chicken_on: list[datetime.date]) -> int:
    tools.add_recipe(
        "Chicken Skewers",
        ingredients=[{"item": ITEM, "qty": "2 lb", "category": "meat/seafood"}],
        prep_time_minutes=15, cook_time_minutes=20, default_servings=4,
    )
    tools.add_recipe(
        "Bean Chili",
        ingredients=[{"item": "Black Beans", "qty": "2 cans", "category": "pantry"}],
        prep_time_minutes=10, cook_time_minutes=40, default_servings=4,
    )
    plan_id = tools.create_weekly_plan(
        start.isoformat(), content_start_date=start.isoformat(), day_count=7
    )["weekly_plan_id"]
    for i in range(7):
        d = start + datetime.timedelta(days=i)
        dish = "Chicken Skewers" if d in chicken_on else "Bean Chili"
        tools.plan_meal(d.isoformat(), dish, slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id)
    return plan_id


def _chicken_lines() -> list[tuple[str, str, str]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity, status, COALESCE(removed_by, '') AS removed_by FROM grocery_items "
        "WHERE LOWER(item) LIKE 'chicken%'"
    ).fetchall()
    conn.close()
    return [(r["quantity"], r["status"], r["removed_by"]) for r in rows]


def test_tomorrows_too_late_night_keeps_the_line_and_is_named(signed_in):
    """CATCH (both halves). Red before: set_aside [line], note "tonight"."""
    today = household_today()
    tomorrow, later = today + datetime.timedelta(days=1), today + datetime.timedelta(days=4)
    plan_id = _seed(tomorrow, [tomorrow, later])
    week = tomorrow.isoformat()

    step = signed_in.get(f"/api/week/{week}/defrost-items").json()["items"]
    assert [n["date"] for n in step[0]["nights"]] == [later.isoformat()]
    assert step[0]["on_list"] is False, "the chip must not promise the line comes off"
    assert step[0]["on_list_reason"] == defrost.ON_LIST_TOO_LATE

    body = signed_in.post(f"/api/week/{week}/defrost-confirm", json={"items": [ITEM]}).json()

    assert [c["date"] for c in body["created"]] == [later.isoformat()], "the later night is still booked"
    assert body["set_aside"] == []
    assert [s for _q, s, _r in _chicken_lines()] == ["needed"], "tomorrow's chicken is still to buy"
    assert [n["date"] for n in body["notes"]] == [tomorrow.isoformat()]
    assert body["notes"][0]["note"].startswith("Too late to thaw safely for tomorrow night — ")
    assert plan_id == body["weekly_plan_id"]


def test_a_night_already_eaten_says_nothing_and_the_line_still_comes_off(signed_in):
    """CATCH. Red before: a note about a dinner two days gone."""
    today = household_today()
    start = today - datetime.timedelta(days=3)
    gone, ahead = today - datetime.timedelta(days=2), today + datetime.timedelta(days=3)
    _seed(start, [gone, ahead])

    body = signed_in.post(
        f"/api/week/{start.isoformat()}/defrost-confirm", json={"items": [ITEM]}
    ).json()

    assert body["notes"] == []
    assert [c["date"] for c in body["created"]] == [ahead.isoformat()]
    assert [(s, r) for _q, s, r in _chicken_lines()] == [("removed", defrost.FREEZER_REMOVED_BY)]


def test_a_night_thawed_on_time_is_not_too_late_when_the_step_is_answered_again(monkeypatch):
    """CATCH. Answered while there was time, answered again the next day:
    before, the booked night read as too late (a note) and nothing else."""
    today = household_today()
    night = today + datetime.timedelta(days=2)  # its 48-hour move is today
    plan_id = _seed(today, [night])

    first = defrost.confirm_frozen_items(plan_id, [ITEM])
    assert [c["task_date"] for c in first["created"]] == [today.isoformat()]
    assert first["set_aside"], "the line came off with the first yes"

    monkeypatch.setattr(_cooker, "household_today", lambda *a, **k: today + datetime.timedelta(days=1))
    again = defrost.confirm_frozen_items(plan_id, [ITEM])

    assert again["notes"] == []
    assert [(s, r) for _q, s, r in _chicken_lines()] == [("removed", defrost.FREEZER_REMOVED_BY)]


def test_tonight_already_cooked_is_not_a_night_short_of_food():
    """CATCH. A dinner already ticked cooked needs nothing bought (before: a note)."""
    today = household_today()
    later = today + datetime.timedelta(days=4)
    plan_id = _seed(today, [today, later])
    tonight = [m for m in tools.get_weekly_plan(plan_id)["meals"]
               if m["date"] == today.isoformat() and m["slot"] == "dinner"][0]
    tools.check_off_meal(tonight["entry_id"], "done")

    assert defrost.meat_items_for_plan(plan_id)[0]["on_list"] is True
    result = defrost.confirm_frozen_items(plan_id, [ITEM])
    assert result["notes"] == []
    assert result["set_aside"], "nothing still ahead is short of time"


def test_the_note_names_the_night():
    today = datetime.date(2026, 10, 10)  # a Saturday
    note = defrost.too_late_to_thaw_note
    assert note("2026-10-10", "dinner", today) == defrost.TOO_LATE_TO_THAW_NOTE
    assert "for tomorrow night —" in note("2026-10-11", "dinner", today)
    assert "for Tuesday's dinner —" in note("2026-10-13", None, today)
    assert "for tomorrow's lunch —" in note("2026-10-11", "lunch", today)


def test_a_plural_spelling_on_another_recipe_still_keeps_the_line():
    """CATCH (review). "Chicken Thigh" on the too-late night, "Chicken
    Thighs" on the later one: one line on the list, two spellings in the
    plan. The too-late night's spelling must keep the line however the
    other spelling is walked."""
    today = household_today()
    tomorrow, later = today + datetime.timedelta(days=1), today + datetime.timedelta(days=4)
    tools.add_recipe(
        "Thigh Tacos",
        ingredients=[{"item": "Chicken Thigh", "qty": "2 lb", "category": "meat/seafood"}],
        prep_time_minutes=15, cook_time_minutes=20, default_servings=4,
    )
    plan_id = _seed(tomorrow, [later])
    tools.plan_meal(tomorrow.isoformat(), "Thigh Tacos", slot="dinner", weekly_plan_id=plan_id)

    # The read side agrees with the write (coordinator review): neither
    # spelling's chip promises the shared line comes off.
    step = defrost.meat_items_for_plan(plan_id)
    assert step and all(i["on_list"] is False for i in step), step

    result = defrost.confirm_frozen_items(plan_id, ["Chicken Thigh", ITEM])

    assert [n["date"] for n in result["notes"]] == [tomorrow.isoformat()]
    assert result["set_aside"] == []
    assert "needed" in [s for _q, s, _r in _chicken_lines()]
