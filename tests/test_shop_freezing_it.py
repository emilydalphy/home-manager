"""
Shop: "Freezing it?" under a just-ticked meat line (Loop Board
3e21f4c0-5231-8153-bb6a-c064eaf421f4, 2026-09-21; mockup F-C-when-ticked).

Ticking a meat/seafood line bought is the moment the household knows where
that pack is going, and the freezer step at approval could not ask (the
line was still on the list). So the checklist asks, once, under the row —
"Freezing it? I'll remind you Saturday night to move it to the fridge for
Monday." — with two mini buttons. Yes books the same defrost row the
freezer step writes, through a new route; Straight to the fridge writes
nothing; ignoring it collapses it silently.

Server side here: the offer stamped on the list, the route (yes → a
defrost task at the right lead; put back → gone; straight → nothing
written; 401; a non-meat or loose line → 400), and Today's Cook group
showing the move exactly as any other. The screen's own behaviour runs
under node in tests/test_shop_freezing_it_screen.py.
"""
from __future__ import annotations

import datetime

from app import tools
from app.db import get_conn
from app.tools import defrost, quantities
from app.tools._shared import household_id
from conftest import household_today

TODAY = household_today()


def _monday_ahead() -> datetime.date:
    # Next week, so every move date is still ahead on the household's clock.
    return TODAY - datetime.timedelta(days=TODAY.weekday()) + datetime.timedelta(days=7)


def _week():
    week = _monday_ahead().isoformat()
    return week, tools._week_dates(week)


def _recipe(name, item, category="meat/seafood", qty="2 lb"):
    tools.add_recipe(name, ingredients=[{"item": item, "qty": qty, "category": category}],
                     prep_time_minutes=10, cook_time_minutes=15)


def _plan_with(meals):
    """One plan, one dish per (date, name, item[, category]), every ingredient on the list."""
    week, dates = _week()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    for m in meals:
        day, name, item = m[:3]
        _recipe(name, item, category=m[3] if len(m) > 3 else "meat/seafood")
        tools.plan_meal(dates[day], name, slot="dinner", weekly_plan_id=plan_id,
                        add_ingredients_to_grocery_list=True)
    return plan_id, dates


def _line(client, name):
    view = client.get("/api/grocery-list/by-store?status=needed").json()
    for store in view["stores"]:
        for s in store["sections"]:
            for it in s["items"]:
                if it["item"].lower() == name.lower():
                    return it
    raise AssertionError(f"{name} is not on the list")


def _defrost_rows():
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, task_date, description, status, meal_plan_entry_id, inventory_item_id, quantity FROM prep_tasks "
        "WHERE household_id = ? AND task_type = 'defrost' ORDER BY id", (household_id(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- the offer on the list ----------

def test_a_meat_line_a_meal_recorded_carries_the_offer_and_a_non_meat_line_does_not(signed_in):
    plan_id, dates = _plan_with([(3, "Chicken Skewers", "Chicken thighs"), (2, "Orzo Salad", "Orzo", "pantry")])
    thighs = _line(signed_in, "Chicken thighs")
    orzo = _line(signed_in, "Orzo")

    assert "freezing" not in orzo
    offer = thighs["freezing"]
    cook = datetime.date.fromisoformat(dates[3])
    assert offer["cook_date"] == dates[3] and offer["cook_weekday"] == "Thursday"
    # No dinner_window: the 48h everyday-cut lead rounds to two days before.
    assert offer["move_date"] == (cook - datetime.timedelta(days=2)).isoformat()
    assert offer["move_label"] == "Tuesday night"
    assert offer["meal"] == "Chicken Skewers" and offer["lead_tier"] == "standard"


def test_a_loose_meat_line_with_no_meal_has_no_offer(signed_in):
    signed_in.post("/api/grocery-list/add", json={"item": "Pork chops", "quantity": "4", "category": "meat/seafood"})
    assert "freezing" not in _line(signed_in, "Pork chops")


def test_a_line_feeding_two_meals_is_asked_about_the_first_cook_night(signed_in):
    _recipe("Chicken Skewers", "Chicken thighs")
    week, dates = _week()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    for day in (4, 1):
        tools.plan_meal(dates[day], "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id,
                        add_ingredients_to_grocery_list=True)
    offer = _line(signed_in, "Chicken thighs")["freezing"]
    assert offer["cook_date"] == dates[1], "the first night, not the one planned first"


def test_a_line_whose_move_is_already_booked_has_no_offer(signed_in):
    """Answered on the freezer step (confirm_frozen_items + defrost_asked_at):
    the checklist never asks again — the per-item booked state is read, not
    the timestamp alone. Since 2026-09-21 a tapped chip also sets the line
    aside (removed_by 'freezer'), so it is looked up by id, whatever its
    status, and the offer is asked of the line itself."""
    plan_id, dates = _plan_with([(3, "Chicken Skewers", "Chicken thighs")])
    thighs = _line(signed_in, "Chicken thighs")
    assert "freezing" in thighs
    defrost.confirm_frozen_items(plan_id, ["Chicken thighs"])
    defrost.mark_defrost_asked(plan_id)
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, category, status, removed_by FROM grocery_items WHERE id = ? AND household_id = ?",
        (thighs["id"], household_id()),
    ).fetchone()
    conn.close()
    assert row["status"] == "removed" and row["removed_by"] == defrost.FREEZER_REMOVED_BY
    assert defrost.freezing_offer_for_grocery_line(dict(row)) is None
    stamped = defrost.stamp_freezing_offers([dict(row)])
    assert "freezing" not in stamped[0]
    every = signed_in.get("/api/grocery-list/by-store?status=all").json()
    listed = [it for store in every["stores"] for s in store["sections"] for it in s["items"] if it["id"] == thighs["id"]]
    assert all("freezing" not in it for it in listed)


def test_the_lead_follows_the_cut(signed_in):
    _plan_with([(5, "Roast Night", "Whole chicken"), (5, "Shrimp Bowls", "Shrimp")])
    sat = datetime.date.fromisoformat(_week()[1][5])
    assert _line(signed_in, "Whole chicken")["freezing"]["move_date"] == (sat - datetime.timedelta(days=3)).isoformat()
    assert _line(signed_in, "Shrimp")["freezing"]["move_date"] == (sat - datetime.timedelta(days=1)).isoformat()


# ---------- the route ----------

def test_yes_books_the_defrost_move_dated_with_the_right_lead(signed_in):
    plan_id, dates = _plan_with([(3, "Chicken Skewers", "Chicken thighs")])
    thighs = _line(signed_in, "Chicken thighs")
    signed_in.post(f"/api/grocery-list/{thighs['id']}/status", json={"status": "purchased"})

    res = signed_in.post(f"/api/grocery-list/{thighs['id']}/freezing", json={"answer": "freezer"})

    assert res.status_code == 200, res.text
    body = res.json()
    expected = (datetime.date.fromisoformat(dates[3]) - datetime.timedelta(days=2)).isoformat()
    assert body["freezing"] is True and body["task_date"] == expected and body["move_label"] == "Tuesday night"
    rows = _defrost_rows()
    assert len(rows) == 1
    assert rows[0]["task_date"] == expected and rows[0]["status"] == "pending"
    assert rows[0]["inventory_item_id"] is None, "a fact about the plan, never an inventory write"
    assert rows[0]["description"] == "Move the Chicken thighs to the fridge — for Thursday's Chicken Skewers."
    assert rows[0]["meal_plan_entry_id"] is not None
    assert rows[0]["quantity"] == "2 lb", "the recipe's own amount, exactly as the freezer step writes it"


def test_the_two_doors_write_the_same_row(signed_in):
    """A yes on Shop and a tapped chip on the freezer step book one and the
    same row: same date, description, entry, NULL inventory id — and, since
    the 2026-09-21 integration, the same quantity."""
    plan_id, dates = _plan_with([(3, "Chicken Skewers", "Chicken thighs")])
    thighs = _line(signed_in, "Chicken thighs")
    signed_in.post(f"/api/grocery-list/{thighs['id']}/freezing", json={"answer": "freezer"})
    shop_row = _defrost_rows()[0]
    signed_in.post(f"/api/grocery-list/{thighs['id']}/freezing", json={"answer": "fridge"})
    assert _defrost_rows() == []

    defrost.confirm_frozen_items(plan_id, ["Chicken thighs"])

    step_row = _defrost_rows()[0]
    keys = ("task_date", "description", "status", "meal_plan_entry_id", "inventory_item_id", "quantity")
    assert {k: step_row[k] for k in keys} == {k: shop_row[k] for k in keys}, "byte for byte, quantity included"
    assert shop_row["quantity"] == "2 lb"


def test_a_line_with_no_recipe_amount_to_read_carries_its_own_normalised(signed_in):
    """A freeform meal recorded the line, so there is no recipe ingredient
    for the shop door to read: the line's own amount stands in, through the
    app's one parser and formatter rather than as the list spelt it."""
    week, dates = _week()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.plan_meal(dates[3], "Steak night", slot="dinner", weekly_plan_id=plan_id)
    steak = tools.add_grocery_item("Steak", quantity="2 lbs", category="meat/seafood", source_weekly_plan_id=plan_id)["item_id"]
    conn = get_conn()
    entry = conn.execute("SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan_id,)).fetchone()["id"]
    conn.execute("INSERT INTO meal_plan_grocery_links (household_id, meal_plan_entry_id, grocery_item_id, item, quantity) "
                 "VALUES (?, ?, ?, 'Steak', '2 lbs')", (household_id(), entry, steak))
    conn.commit()
    conn.close()

    res = signed_in.post(f"/api/grocery-list/{steak}/freezing", json={"answer": "freezer"})

    assert res.status_code == 200, res.text
    assert _defrost_rows()[0]["quantity"] == quantities._format_quantity(*quantities._parse_quantity("2 lbs"))


def test_yes_twice_is_one_move_and_the_freezer_step_sees_it_as_booked(signed_in):
    """A replayed yes (offline queue) or a double tap books nothing twice,
    and the freezer step's own list shows the night as already frozen."""
    plan_id, dates = _plan_with([(3, "Chicken Skewers", "Chicken thighs")])
    item_id = _line(signed_in, "Chicken thighs")["id"]
    first = signed_in.post(f"/api/grocery-list/{item_id}/freezing", json={"answer": "freezer"}).json()
    second = signed_in.post(f"/api/grocery-list/{item_id}/freezing", json={"answer": "freezer"})
    assert second.status_code == 200 and second.json()["prep_task_id"] == first["prep_task_id"]
    assert second.json()["already_booked"] is True
    assert len(_defrost_rows()) == 1
    signed_in.post(f"/api/grocery-list/{item_id}/status", json={"status": "purchased"})
    # The freezer step still lists the item (2026-09-21: every meat in the
    # week is offered, so the answer can be changed) — with the move this
    # yes booked read as given, and no line left to take off the list.
    step = defrost.meat_items_for_plan(plan_id)
    assert [(e["item"], e["frozen"], e["on_list"]) for e in step] == [("Chicken thighs", True, False)]


def test_put_back_removes_the_move_and_leaves_a_done_one_alone(signed_in):
    plan_id, dates = _plan_with([(3, "Chicken Skewers", "Chicken thighs")])
    item_id = _line(signed_in, "Chicken thighs")["id"]
    booked = signed_in.post(f"/api/grocery-list/{item_id}/freezing", json={"answer": "freezer"}).json()

    res = signed_in.post(f"/api/grocery-list/{item_id}/freezing", json={"answer": "fridge"})
    assert res.status_code == 200 and res.json()["removed_prep_task_id"] == booked["prep_task_id"]
    assert _defrost_rows() == []
    assert "freezing" in _line(signed_in, "Chicken thighs"), "askable again"

    # Put back with nothing booked is an ordinary answer.
    again = signed_in.post(f"/api/grocery-list/{item_id}/freezing", json={"answer": "fridge"})
    assert again.status_code == 200 and again.json()["removed_prep_task_id"] is None

    # A move already done stays done.
    signed_in.post(f"/api/grocery-list/{item_id}/freezing", json={"answer": "freezer"})
    conn = get_conn()
    conn.execute("UPDATE prep_tasks SET status = 'done' WHERE task_type = 'defrost'")
    conn.commit()
    conn.close()
    signed_in.post(f"/api/grocery-list/{item_id}/freezing", json={"answer": "fridge"})
    assert [r["status"] for r in _defrost_rows()] == ["done"]


def test_straight_to_the_fridge_never_posts_so_a_tick_alone_writes_no_move(signed_in):
    """The screen's "Straight to the fridge" writes nothing (see the node
    tests); on the server a tick with no answer is exactly that."""
    _plan_with([(3, "Chicken Skewers", "Chicken thighs")])
    item_id = _line(signed_in, "Chicken thighs")["id"]
    signed_in.post(f"/api/grocery-list/{item_id}/status", json={"status": "purchased"})
    assert _defrost_rows() == []


def test_the_route_needs_a_session(client):
    assert client.post("/api/grocery-list/1/freezing", json={"answer": "freezer"}).status_code == 401


def test_a_non_meat_line_and_a_loose_line_are_refused(signed_in):
    _plan_with([(2, "Orzo Salad", "Orzo", "pantry")])
    orzo = _line(signed_in, "Orzo")["id"]
    res = signed_in.post(f"/api/grocery-list/{orzo}/freezing", json={"answer": "freezer"})
    assert res.status_code == 400

    loose = signed_in.post("/api/grocery-list/add", json={"item": "Pork chops", "quantity": "4", "category": "meat/seafood"}).json()["item_id"]
    res = signed_in.post(f"/api/grocery-list/{loose}/freezing", json={"answer": "freezer"})
    assert res.status_code == 400
    assert _defrost_rows() == []

    assert signed_in.post("/api/grocery-list/999999/freezing", json={"answer": "freezer"}).status_code == 404
    assert signed_in.post(f"/api/grocery-list/{orzo}/freezing", json={"answer": "maybe"}).status_code == 400


def test_a_meal_too_close_to_thaw_for_is_not_offered_and_a_yes_is_refused(signed_in):
    """Tomorrow's dinner with a 48h cut: the move night has gone by."""
    _recipe("Chicken Skewers", "Chicken thighs")
    tomorrow = (TODAY + datetime.timedelta(days=1)).isoformat()
    # Anchored on TODAY rather than on this week's Monday. The period has
    # to CONTAIN tomorrow, and a Monday-anchored week does not on a Sunday
    # — TODAY.weekday() is 6, so the week ends today and plan_meal refuses
    # the day after it. That made `clock (sunday)` red on main on every
    # push. This test is about a move night that has gone by, not about
    # where a week begins, so it names the days it actually needs.
    plan_id = tools.create_weekly_plan(TODAY.isoformat())["weekly_plan_id"]
    tools.plan_meal(tomorrow, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    line = _line(signed_in, "Chicken thighs")
    assert "freezing" not in line
    assert signed_in.post(f"/api/grocery-list/{line['id']}/freezing", json={"answer": "freezer"}).status_code == 400


# ---------- Today shows the move ----------

def test_todays_cook_group_shows_the_booked_move_exactly_as_any_other(signed_in):
    plan_id, dates = _plan_with([(3, "Chicken Skewers", "Chicken thighs")])
    item_id = _line(signed_in, "Chicken thighs")["id"]
    signed_in.post(f"/api/grocery-list/{item_id}/status", json={"status": "purchased"})
    booked = signed_in.post(f"/api/grocery-list/{item_id}/freezing", json={"answer": "freezer"}).json()

    payload = signed_in.get(f"/api/today/moves?date={booked['task_date']}").json()
    fridge = [m for m in payload["moves"] if m["kind"] == "fridge"]
    assert len(fridge) == 1
    move = fridge[0]
    assert move["title"] == "Move the Chicken thighs to the fridge"
    assert move["reason"] == "for Thursday's Chicken Skewers"
    assert move["id"] == f"fridge:{booked['prep_task_id']}" and move["tickable"] is True
    assert move["action"] == {"label": "Done", "target": {"kind": "check_prep", "taskId": booked["prep_task_id"]}}
