"""
Batch a shared ingredient automatically when the household preps (Loop
Board, 2026-09-21).

The approval-time "batch cook?" ask left on 2026-09-18 and took the only
caller of batch_components.set_batch_component with it. This is the rule
that stands in for it: at a genuine approval, with at least one prep day,
a component two or more DIFFERENT dishes cook the same way (the eggs a
breakfast and a salad both boil) is batched with no question — one prep
row on the first dish's day, the later dishes reading "boiled Monday" on
their Cook screens — exactly what a yes on the old ask wrote. With no prep
days nothing is batched. And the household is told once, on All set, in
one line the server writes (weekly_plan.batched_line) and the shell only
prints.
"""
from __future__ import annotations

import datetime
import json
import shutil

import pytest

import nodeharness
from conftest import household_today
from app import db, tools
from app.tools import cook_ahead, weekly_plan
from test_allset_week_path import _PLAN_PRELUDE, _APPROVED


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _day(n: int) -> str:
    return (_monday() + datetime.timedelta(days=n)).isoformat()


WEEK = _monday().isoformat()
MON, TUE, WED, THU, FRI = _day(0), _day(1), _day(2), _day(3), _day(4)


def _weekday(date_str: str) -> str:
    return datetime.date.fromisoformat(date_str).strftime("%A")


def _household():
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)


def _egg_toast():
    tools.add_recipe(
        "Hard-Boiled Eggs and Avocado Toast",
        ingredients=[{"item": "Eggs", "qty": "4", "category": "dairy"}, {"item": "Avocado", "qty": "2", "category": "produce"}],
        instructions=["Hard-boil the eggs for 9 minutes.", "Mash the avocado on toast."],
        default_servings=2,
    )


def _egg_salad():
    tools.add_recipe(
        "Egg Salad Sandwiches",
        ingredients=[{"item": "Large eggs", "qty": "6", "category": "dairy"}, {"item": "Mayonnaise", "qty": "1 jar", "category": "pantry"}],
        instructions=["Boil the eggs 10 minutes.", "Peel, chop and mix with mayo."],
        default_servings=2,
    )


def _rice_bowl():
    tools.add_recipe(
        "Chicken Rice Bowl",
        ingredients=[{"item": "Rice", "qty": "2 cups", "category": "pantry"}, {"item": "Chicken thighs", "qty": "1 lb", "category": "meat/seafood"}],
        instructions=["Cook the rice.", "Sear the chicken and serve over the rice."],
        default_servings=2,
    )


def _fried_rice():
    tools.add_recipe(
        "Veggie Fried Rice",
        ingredients=[{"item": "Rice", "qty": "2 cups", "category": "pantry"}, {"item": "Peas", "qty": "1 cup", "category": "produce"}],
        instructions=["Cook the rice and let it cool.", "Fry with the peas."],
        default_servings=2,
    )


def _chili():
    tools.add_recipe(
        "Turkey Chili",
        ingredients=[{"item": "Ground turkey", "qty": "1 lb", "category": "meat/seafood"}],
        instructions=["Brown the turkey, add the beans, simmer."],
        default_servings=2, prep_time_minutes=10, cook_time_minutes=40,
    )


def _plan(*meals):
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    ids = []
    for d, name, slot in meals:
        ids.append(tools.plan_meal(d, name, slot=slot, weekly_plan_id=plan_id)["entry_id"])
    return plan_id, ids


def _batch_rows(plan_id):
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM prep_tasks WHERE weekly_plan_id = ? AND task_type = 'batch_component' ORDER BY id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _asked_at(plan_id):
    conn = db.get_conn()
    row = conn.execute("SELECT cook_ahead_asked_at FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    return row["cook_ahead_asked_at"]


def _sunday_prep():
    tools.set_prep_days([{"weekday": "sunday", "minutes": 90}])


def _cards(plan_id):
    return {m["entry_id"]: m for m in tools.get_cooker_view(plan_id)["meals"]}


# ---------- the rule ----------


def test_a_shared_component_with_a_prep_day_is_batched_on_the_first_dish():
    _household()
    _egg_toast()
    _egg_salad()
    _sunday_prep()
    plan_id, (toast, salad) = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"),
                                    (THU, "Egg Salad Sandwiches", "lunch"))

    out = cook_ahead.apply_prep_day_batches(plan_id)

    assert out["prep_days"] is True and out["refused"] == [] and out["applied"] == []
    assert [c["key"] for c in out["components"]] == ["boiled:egg"]
    assert out["components"][0]["source_entry_id"] == toast
    assert out["components"][0]["covered_entry_ids"] == [salad]
    # ONE prep row, exactly the one a manual yes wrote: on the first dish's
    # day, attached to that dish, sized for both.
    rows = _batch_rows(plan_id)
    assert len(rows) == 1
    assert rows[0]["task_date"] == TUE and rows[0]["meal_plan_entry_id"] == toast
    assert rows[0]["description"] == f"Boil the eggs for {_weekday(THU)}’s Egg Salad Sandwiches too — 10 in all"
    assert json.loads(rows[0]["detail_json"])["covered_entry_ids"] == [salad]
    # The later dish's Cook screen says so where the eggs are read off.
    cards = _cards(plan_id)
    eggs = next(i for i in cards[salad]["ingredients"] if i["item"] == "Large eggs")
    assert eggs["made_ahead"] == f"boiled {_weekday(TUE)}"
    assert cards[salad]["components_made_ahead"] == [{
        "label": "Boiled eggs", "ingredient": "eggs", "source_date": TUE,
        "source_meal": "Hard-Boiled Eggs and Avocado Toast", "done": False,
    }]
    assert cards[toast]["batch_components"][0]["covers"] == [{"entry_id": salad, "date": THU, "dish": "Egg Salad Sandwiches"}]
    # The gate every surface reads: answered, nothing re-asks.
    assert _asked_at(plan_id) is not None
    assert [c["batched"] for c in tools.shared_components(plan_id)] == [True]


def test_with_no_prep_days_no_component_is_batched():
    _household()
    _egg_toast()
    _egg_salad()
    plan_id, (toast, salad) = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"),
                                    (THU, "Egg Salad Sandwiches", "lunch"))

    out = cook_ahead.apply_prep_day_batches(plan_id)

    assert out == {"prep_days": False, "applied": [], "components": [], "refused": []}
    assert _batch_rows(plan_id) == []
    cards = _cards(plan_id)
    assert cards[salad]["components_made_ahead"] == []
    assert "made_ahead" not in next(i for i in cards[salad]["ingredients"] if i["item"] == "Large eggs")
    assert _asked_at(plan_id) is not None
    assert weekly_plan.batched_line(plan_id) == ""


def test_running_the_rule_twice_writes_one_batch():
    _household()
    _egg_toast()
    _egg_salad()
    _sunday_prep()
    plan_id, _ = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"), (THU, "Egg Salad Sandwiches", "lunch"))

    first = cook_ahead.apply_prep_day_batches(plan_id)
    second = cook_ahead.apply_prep_day_batches(plan_id)

    assert len(first["components"]) == 1
    assert second["components"] == [] and second["refused"] == []
    assert len(_batch_rows(plan_id)) == 1


def test_a_component_only_one_dish_cooks_is_left_alone_however_many_days():
    _household()
    _egg_toast()
    _sunday_prep()
    plan_id, (a, b) = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"),
                            (THU, "Hard-Boiled Eggs and Avocado Toast", "breakfast"))

    out = cook_ahead.apply_prep_day_batches(plan_id)

    # The repeated DISH is batched (the 2026-09-18 rule); no component
    # row is written on top of it.
    assert [x["dish"] for x in out["applied"]] == ["Hard-Boiled Eggs and Avocado Toast"]
    assert out["components"] == []
    assert _batch_rows(plan_id) == []


# ---------- at approval ----------


def test_approval_batches_the_component_tells_the_household_once_and_never_asks(signed_in):
    _household()
    _egg_toast()
    _egg_salad()
    _sunday_prep()
    plan_id, (toast, salad) = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"),
                                    (THU, "Egg Salad Sandwiches", "lunch"))

    res = signed_in.post(f"/api/week/{WEEK}/approve", json={"approved_by": "Emily"})

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "approved"
    # The removed ask never comes back through the approve response.
    assert "cook_ahead" not in body and "ask" not in body and "components" not in body
    assert len(_batch_rows(plan_id)) == 1
    menu = signed_in.get("/api/week-menu").json()
    assert menu["cook_ahead_asked_at"] is not None
    assert menu["receipt"]["batched_line"] == (
        f"I’ve batched the eggs: one pot {_weekday(TUE)} covers {_weekday(THU)}."
    )
    # ...and the later dish's Cook screen carries the note.
    cards = _cards(plan_id)
    assert next(i for i in cards[salad]["ingredients"] if i["item"] == "Large eggs")["made_ahead"] == f"boiled {_weekday(TUE)}"
    assert signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()["components"] == []


def test_approval_without_prep_days_batches_no_component_and_has_no_line(signed_in):
    _household()
    _egg_toast()
    _egg_salad()
    plan_id, _ = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"), (THU, "Egg Salad Sandwiches", "lunch"))

    res = signed_in.post(f"/api/week/{WEEK}/approve", json={"approved_by": "Emily"})

    assert res.status_code == 200 and res.json()["status"] == "approved"
    assert _batch_rows(plan_id) == []
    assert signed_in.get("/api/week-menu").json()["receipt"]["batched_line"] == ""


def test_a_re_approval_does_not_batch_twice(signed_in):
    _household()
    _egg_toast()
    _egg_salad()
    _sunday_prep()
    plan_id, _ = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"), (THU, "Egg Salad Sandwiches", "lunch"))
    signed_in.post(f"/api/week/{WEEK}/approve", json={"approved_by": "Emily"})
    first_id = _batch_rows(plan_id)[0]["id"]

    res = signed_in.post(f"/api/week/{WEEK}/approve", json={"approved_by": "Emily"})

    assert res.json()["was_already_approved"] is True
    rows = _batch_rows(plan_id)
    assert [r["id"] for r in rows] == [first_id]
    # The line is still there on the (still approved) week — same words.
    assert signed_in.get("/api/week-menu").json()["receipt"]["batched_line"].startswith("I’ve batched the eggs:")


# ---------- the words ----------


def test_one_component_covering_two_later_dishes_names_the_pot_and_the_days():
    _household()
    _rice_bowl()
    _fried_rice()
    _sunday_prep()
    plan_id, _ = _plan((MON, "Chicken Rice Bowl", "dinner"), (WED, "Veggie Fried Rice", "dinner"),
                       (FRI, "Chicken Rice Bowl", "lunch"))
    cook_ahead.apply_prep_day_batches(plan_id)

    line = weekly_plan.batched_line(plan_id)

    assert line == f"I’ve batched the rice: one pot {_weekday(MON)} covers {_weekday(WED)} and {_weekday(FRI)}."


def test_a_repeated_dish_alone_reads_big_enough_for():
    """The 2026-09-18 rule already batched a repeated dish silently; it gets
    the same one line (Emily: show the value)."""
    _household()
    _chili()
    _sunday_prep()
    plan_id, _ = _plan((MON, "Turkey Chili", "dinner"), (THU, "Turkey Chili", "dinner"))
    cook_ahead.apply_prep_day_batches(plan_id)

    assert weekly_plan.batched_line(plan_id) == f"I’ve made {_weekday(MON)}’s Turkey Chili big enough for {_weekday(THU)} too."


def test_several_batches_read_as_one_line_with_the_count():
    _household()
    _rice_bowl()
    _fried_rice()
    _chili()
    _sunday_prep()
    plan_id, _ = _plan((MON, "Turkey Chili", "dinner"), (TUE, "Chicken Rice Bowl", "dinner"),
                       (WED, "Veggie Fried Rice", "dinner"), (THU, "Turkey Chili", "dinner"))
    cook_ahead.apply_prep_day_batches(plan_id)

    line = weekly_plan.batched_line(plan_id)

    # Cook-day order (the chili cooks Monday, the rice Tuesday); the count
    # is the later dinners the two batches feed, in digits like the tiles
    # above it (_receipt_number: one number style per screen).
    assert line == "I’ve batched Turkey Chili and the rice — one cook each, covering 2 dinners."


def test_mixed_slots_say_meals():
    _household()
    _egg_toast()
    _egg_salad()
    _chili()
    _sunday_prep()
    plan_id, _ = _plan((MON, "Turkey Chili", "dinner"), (TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"),
                       (THU, "Egg Salad Sandwiches", "lunch"), (THU, "Turkey Chili", "dinner"))
    cook_ahead.apply_prep_day_batches(plan_id)

    assert weekly_plan.batched_line(plan_id) == "I’ve batched Turkey Chili and the eggs — one cook each, covering 2 meals."


def test_a_planner_leftovers_night_is_not_called_a_batch():
    """The planner's own cook-once-eat-twice chain has no cook_ahead flag:
    nobody batched anything, so the line stays quiet about it."""
    _household()
    _chili()
    plan_id, (mon, thu) = _plan((MON, "Turkey Chili", "dinner"), (THU, "Turkey Chili", "dinner"))
    conn = db.get_conn()
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"make_double_for": [f"{THU}:dinner"]}), mon))
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"links_to": f"{MON}:dinner"}), thu))
    conn.commit()
    conn.close()
    assert set(tools.plan_leftover_chains(plan_id)["leftovers"]) == {thu}

    assert cook_ahead.batched_dishes(plan_id) == []
    assert weekly_plan.batched_line(plan_id) == ""


def test_the_line_never_uses_a_dashboard_word():
    _household()
    _rice_bowl()
    _fried_rice()
    _sunday_prep()
    plan_id, _ = _plan((MON, "Chicken Rice Bowl", "dinner"), (WED, "Veggie Fried Rice", "dinner"))
    cook_ahead.apply_prep_day_batches(plan_id)
    line = weekly_plan.batched_line(plan_id)
    assert line.startswith("I’ve ") and line.endswith(".")
    for word in ("batch_component", "entry", "task", "status", "component"):
        assert word not in line.lower()


# ---------- All set prints it, and only when it is there ----------

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the screen's own function")


def _render(receipt_extra: dict) -> str:
    data = dict(_APPROVED, receipt=dict(_APPROVED["receipt"], **receipt_extra))
    res = nodeharness.run_node(_PLAN_PRELUDE + f"console.log(JSON.stringify(allSetStepHtml({json.dumps(data)}, [])));", timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip())


@_needs_node
def test_all_set_prints_the_batched_line_under_the_numbers():
    line = "I’ve batched the rice: one pot Sunday covers Tuesday and Thursday."
    html = _render({"batched_line": line})
    assert html.count('class="wk-allset-batched"') == 1
    assert f'<p class="wk-allset-batched">{line}</p>' in html
    # Under the numbers, above the one button.
    assert html.index("wk-allset-nums") < html.index("wk-allset-batched") < html.index("wk-allset-dock")


@_needs_node
def test_all_set_has_no_batched_line_when_nothing_was_batched():
    for receipt in ({"batched_line": ""}, {}):
        html = _render(receipt)
        assert "wk-allset-batched" not in html
        assert "batched" not in html.lower()
