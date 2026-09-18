"""
One component in several dishes, cooked once (tools/batch_components.py).

Emily, 2026-09-13: "if there is something that is repeated it should be in
the same group. For example, I'm seeing boiled eggs in two different
recipes, ask me if I want them all in the same bulk cook."

What these pin down: the component is found from the recipe's own steps
and ingredient lines (never from dish titles); a component one dish cooks
is not offered however many nights the dish is on; the same-dish repeat
stays cook_ahead.py's question; saying yes writes ONE prep row on the
earliest day sized for every dish, and the Cook view reads it back onto
both the cook day and the later dishes; saying no writes nothing; and the
grocery list is the same either way.
"""
import datetime
import json

from app import db, tools
from app.tools import batch_components as bc


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


WEEK = _monday().isoformat()
MON, TUE, WED, THU, FRI = _day(0), _day(1), _day(2), _day(3), _day(4)


def _household(*names):
    for n in names or ("Alex", "Sam"):
        tools.add_member(n)


def _egg_toast():
    tools.add_recipe(
        "Hard-Boiled Eggs and Avocado Toast",
        ingredients=[
            {"item": "Eggs", "qty": "4", "category": "dairy"},
            {"item": "Avocado", "qty": "2", "category": "produce"},
            {"item": "Sourdough", "qty": "1 loaf", "category": "pantry"},
        ],
        instructions=[
            "Hard-boil the eggs for 9 minutes, then cool in ice water.",
            "Toast the bread and mash the avocado on top.",
            "Slice the eggs over the toast.",
        ],
        default_servings=2,
    )


def _egg_salad():
    tools.add_recipe(
        "Egg Salad Sandwiches",
        ingredients=[
            {"item": "Large eggs", "qty": "6", "category": "dairy"},
            {"item": "Mayonnaise", "qty": "1 jar", "category": "pantry"},
        ],
        instructions=["Boil the eggs 10 minutes.", "Peel, chop and mix with mayo."],
        default_servings=2,
    )


def _imported_eggs():
    """A recipe brought in from a link that kept the descriptor on the
    ingredient line and has no boiling step of its own."""
    tools.add_recipe(
        "Cobb Salad",
        ingredients=[
            {"item": "Hard-boiled eggs", "qty": "2", "category": "dairy"},
            {"item": "Romaine", "qty": "1 head", "category": "produce"},
        ],
        instructions=["Chop everything and toss."],
        default_servings=2,
    )


def _chili():
    tools.add_recipe(
        "Turkey Chili",
        ingredients=[{"item": "Ground turkey", "qty": "1 lb", "category": "meat/seafood"}],
        instructions=["Brown the turkey, add the beans, simmer."],
        default_servings=2,
    )


def _plan(*meals):
    """meals: (date, recipe name, slot) — one entry each, in one plan."""
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


def _grocery_snapshot():
    return sorted((g["item"].lower(), g["quantity"]) for g in tools.list_grocery_list())


# ---------- finding the component ----------

def test_the_component_is_read_from_the_steps_not_the_titles():
    comps = bc._recipe_components({
        "ingredients": [{"item": "Eggs", "qty": "4"}, {"item": "Avocado", "qty": "2"}],
        "instructions": ["Hard-boil the eggs for 9 minutes.", "Mash the avocado."],
    })
    assert list(comps) == ["boiled:egg"]
    assert comps["boiled:egg"]["label"] == "Boiled eggs"
    assert comps["boiled:egg"]["imperative"] == "Boil"


def test_a_descriptor_on_the_ingredient_line_counts_too():
    by_item = bc._recipe_components({"ingredients": [{"item": "Hard-boiled eggs", "qty": "2"}], "instructions": []})
    by_qty = bc._recipe_components({"ingredients": [{"item": "Eggs", "qty": "4, hard-boiled"}], "instructions": []})
    assert list(by_item) == ["boiled:egg"] and list(by_qty) == ["boiled:egg"]
    # The name the ask says is the plain ingredient, not the descriptor.
    assert by_item["boiled:egg"]["label"] == "Boiled eggs"


def test_boil_as_a_noun_and_a_verb_with_nothing_after_it_are_not_read():
    comps = bc._recipe_components({
        "ingredients": [{"item": "Beef", "qty": "1 lb"}, {"item": "Eggs", "qty": "4"}],
        "instructions": ["Bring a pot of water to a boil. Add the eggs and cook 10 minutes.", "Boil and simmer."],
    })
    assert comps == {}


def test_cook_is_only_read_for_grains_and_legumes():
    onions = bc._recipe_components({"ingredients": [{"item": "Onions", "qty": "2"}], "instructions": ["Cook the onions until soft."]})
    rice = bc._recipe_components({"ingredients": [{"item": "Rice", "qty": "1 bag"}], "instructions": ["Cook the rice."]})
    assert onions == {}
    assert list(rice) == ["cooked:rice"]


def test_the_first_ingredient_after_the_verb_is_the_one_cooked():
    comps = bc._recipe_components({
        "ingredients": [{"item": "Chickpeas", "qty": "2 cans"}, {"item": "Olive oil", "qty": "1 bottle"}],
        "instructions": ["Roast the chickpeas with olive oil at 400 for 25 minutes."],
    })
    assert list(comps) == ["roasted:chickpea"]


# ---------- what the ask offers ----------

def test_two_different_dishes_that_boil_eggs_are_one_block(signed_in):
    _household()
    _egg_toast()
    _egg_salad()
    plan_id, (toast, salad) = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"), (THU, "Egg Salad Sandwiches", "lunch"))

    res = signed_in.get(f"/api/week/{WEEK}/cook-ahead-items")

    assert res.status_code == 200
    body = res.json()
    assert body["items"] == []  # no dish repeats — that is cook_ahead's list
    assert len(body["components"]) == 1
    comp = body["components"][0]
    assert comp["key"] == "boiled:egg"
    assert comp["label"] == "Boiled eggs"
    assert comp["first"] == {"entry_id": toast, "date": TUE}
    assert [(u["entry_id"], u["date"], u["dish"]) for u in comp["uses"]] == [
        (toast, TUE, "Hard-Boiled Eggs and Avocado Toast"),
        (salad, THU, "Egg Salad Sandwiches"),
    ]
    assert comp["batched"] is False
    # Sized for both dishes together: 4 + 6, for two eaters against
    # recipes written for two.
    assert comp["quantity"] == "10"


def test_a_component_in_one_dish_only_is_not_offered(signed_in):
    _household()
    _egg_toast()
    _chili()
    _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"), (THU, "Turkey Chili", "dinner"))

    assert signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()["components"] == []


def test_the_same_dish_on_two_days_stays_the_per_dish_question(signed_in):
    """Egg toast on Monday and Wednesday is a repeat of one dish — the
    cook-ahead block already asks about it, so no component block doubles
    the question."""
    _household()
    _egg_toast()
    _plan((MON, "Hard-Boiled Eggs and Avocado Toast", "breakfast"), (WED, "Hard-Boiled Eggs and Avocado Toast", "breakfast"))

    body = signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()
    assert len(body["items"]) == 1
    assert body["components"] == []


def test_three_dishes_and_a_repeat_all_land_in_the_one_block(signed_in):
    """The toast twice, the salad once, an imported Cobb salad once: one
    block, four cooks, in the week's order."""
    _household()
    _egg_toast()
    _egg_salad()
    _imported_eggs()
    plan_id, ids = _plan(
        (MON, "Hard-Boiled Eggs and Avocado Toast", "breakfast"),
        (TUE, "Egg Salad Sandwiches", "lunch"),
        (WED, "Hard-Boiled Eggs and Avocado Toast", "breakfast"),
        (FRI, "Cobb Salad", "dinner"),
    )

    comps = signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()["components"]
    assert len(comps) == 1
    assert [u["entry_id"] for u in comps[0]["uses"]] == ids


def test_a_reheat_night_does_not_count_as_a_cook(signed_in):
    """Toast Monday cooking ahead for Wednesday: Wednesday boils nothing, so
    the eggs block lists Monday and Thursday's salad, not Wednesday."""
    _household()
    _egg_toast()
    _egg_salad()
    plan_id, (mon, wed, thu) = _plan(
        (MON, "Hard-Boiled Eggs and Avocado Toast", "breakfast"),
        (WED, "Hard-Boiled Eggs and Avocado Toast", "breakfast"),
        (THU, "Egg Salad Sandwiches", "lunch"),
    )
    assert isinstance(tools.set_cook_ahead(mon, [wed]), dict)

    comps = signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()["components"]
    assert [u["entry_id"] for u in comps[0]["uses"]] == [mon, thu]


def test_two_dishes_with_different_components_are_two_blocks(signed_in):
    _household()
    _egg_toast()
    _egg_salad()
    tools.add_recipe("Chickpea Bowls", ingredients=[{"item": "Chickpeas", "qty": "2 cans"}], instructions=["Roast the chickpeas."], default_servings=2)
    tools.add_recipe("Chickpea Wraps", ingredients=[{"item": "Chickpeas", "qty": "1 can"}], instructions=["Roast the chickpeas until crisp."], default_servings=2)
    _plan(
        (MON, "Chickpea Bowls", "dinner"),
        (TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"),
        (WED, "Chickpea Wraps", "lunch"),
        (THU, "Egg Salad Sandwiches", "lunch"),
    )

    comps = signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()["components"]
    assert [c["label"] for c in comps] == ["Roasted chickpeas", "Boiled eggs"]


# ---------- saying yes ----------

def test_yes_writes_one_prep_row_on_the_earliest_day_for_the_lot(signed_in):
    _household()
    _egg_toast()
    _egg_salad()
    plan_id, (toast, salad) = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"), (THU, "Egg Salad Sandwiches", "lunch"))
    before = _grocery_snapshot()

    res = signed_in.post(
        f"/api/week/{WEEK}/cook-ahead-confirm",
        json={"choices": [], "components": [{"key": "boiled:egg", "entry_ids": [salad, toast]}]},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["refused"] == []
    assert body["applied"] == []
    assert len(body["components_applied"]) == 1
    done = body["components_applied"][0]
    assert done["source_entry_id"] == toast
    assert done["covered_entry_ids"] == [salad]
    assert done["quantity"] == "10"
    assert done["description"] == "Boil the eggs for Thursday’s Egg Salad Sandwiches too — 10 in all"

    rows = _batch_rows(plan_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["task_date"] == TUE
    assert row["meal_plan_entry_id"] == toast
    assert row["related_meal"] == "Hard-Boiled Eggs and Avocado Toast"
    assert row["status"] == "pending"
    assert row["quantity"] == "10"
    detail = json.loads(row["detail_json"])
    assert detail["key"] == "boiled:egg"
    assert detail["covered_entry_ids"] == [salad]
    assert [d["entry_id"] for d in detail["dishes"]] == [toast, salad]

    # The ask is answered for this plan, and the eggs are not offered again.
    conn = db.get_conn()
    asked = conn.execute("SELECT cook_ahead_asked_at FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()[0]
    conn.close()
    assert asked is not None
    assert signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()["components"] == []

    # No leftover chain was written: the salad is still its own cook.
    assert tools.plan_leftover_chains(plan_id)["leftovers"] == {}
    # And the shopping list is exactly what it was.
    assert _grocery_snapshot() == before


def test_the_cook_view_reads_the_batch_back_onto_both_cards(signed_in):
    _household()
    _egg_toast()
    _egg_salad()
    plan_id, (toast, salad) = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"), (THU, "Egg Salad Sandwiches", "lunch"))
    assert isinstance(tools.set_batch_component(plan_id, "boiled:egg", [toast, salad]), dict)

    view = tools.get_cooker_view(plan_id)
    cards = {m["entry_id"]: m for m in view["meals"]}

    source = cards[toast]
    assert len(source["batch_components"]) == 1
    batch = source["batch_components"][0]
    assert batch["label"] == "Boiled eggs"
    assert batch["quantity"] == "10"
    assert batch["done"] is False
    assert batch["covers"] == [{"entry_id": salad, "date": THU, "dish": "Egg Salad Sandwiches"}]
    assert source["components_made_ahead"] == []

    covered = cards[salad]
    assert covered["batch_components"] == []
    assert covered["components_made_ahead"] == [{
        "label": "Boiled eggs", "ingredient": "eggs", "source_date": TUE,
        "source_meal": "Hard-Boiled Eggs and Avocado Toast", "done": False,
    }]
    eggs = next(i for i in covered["ingredients"] if i["item"] == "Large eggs")
    assert eggs["made_ahead"] == "boiled Tuesday"
    # The salad is still a cook — not a reheat — with its own recipe.
    assert covered["is_leftovers"] is False and covered["instructions"]

    # The same prep row is a tickable task on the cook day's screen, and
    # ticking it is what turns "made ahead" into "done".
    task = next(t for t in view["prep_tasks"] if t["task_type"] == "batch_component")
    assert task["meal_plan_entry_id"] == toast and task["task_date"] == TUE
    tools.check_off_prep_step(task["id"], "done")
    view = tools.get_cooker_view(plan_id)
    cards = {m["entry_id"]: m for m in view["meals"]}
    assert cards[salad]["components_made_ahead"][0]["done"] is True
    assert cards[toast]["batch_components"][0]["done"] is True


def test_answering_again_replaces_the_batch_rather_than_stacking_one(signed_in):
    _household()
    _egg_toast()
    _egg_salad()
    _imported_eggs()
    plan_id, (toast, salad, cobb) = _plan(
        (TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"),
        (THU, "Egg Salad Sandwiches", "lunch"),
        (FRI, "Cobb Salad", "dinner"),
    )
    assert isinstance(tools.set_batch_component(plan_id, "boiled:egg", [toast, salad, cobb]), dict)
    assert isinstance(tools.set_batch_component(plan_id, "boiled:egg", [salad, cobb]), dict)

    rows = _batch_rows(plan_id)
    assert len(rows) == 1
    assert rows[0]["task_date"] == THU and rows[0]["meal_plan_entry_id"] == salad
    assert json.loads(rows[0]["detail_json"])["covered_entry_ids"] == [cobb]
    # The toast, left out, reads nothing about its eggs.
    cards = {m["entry_id"]: m for m in tools.get_cooker_view(plan_id)["meals"]}
    assert cards[toast]["components_made_ahead"] == [] and cards[toast]["batch_components"] == []


def test_fewer_than_two_dishes_and_an_unknown_key_are_refused_in_words(signed_in):
    _household()
    _egg_toast()
    _egg_salad()
    plan_id, (toast, salad) = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"), (THU, "Egg Salad Sandwiches", "lunch"))

    res = signed_in.post(
        f"/api/week/{WEEK}/cook-ahead-confirm",
        json={"components": [
            {"key": "boiled:egg", "entry_ids": [toast]},
            {"key": "roasted:chickpea", "entry_ids": [toast, salad]},
        ]},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["components_applied"] == []
    assert [r["key"] for r in body["refused"]] == ["boiled:egg", "roasted:chickpea"]
    assert body["refused"][0]["note"] == "Pick at least two dishes to make them at once."
    assert _batch_rows(plan_id) == []


def test_a_refused_component_does_not_take_the_dish_answer_with_it(signed_in):
    _household()
    _egg_toast()
    _egg_salad()
    _chili()
    plan_id, (toast, salad, c1, c2) = _plan(
        (TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"),
        (THU, "Egg Salad Sandwiches", "lunch"),
        (WED, "Turkey Chili", "dinner"),
        (FRI, "Turkey Chili", "dinner"),
    )

    res = signed_in.post(
        f"/api/week/{WEEK}/cook-ahead-confirm",
        json={
            "choices": [{"source_entry_id": c1, "covered_entry_ids": [c2]}],
            "components": [{"key": "boiled:egg", "entry_ids": [toast]}],
        },
    )

    body = res.json()
    assert len(body["applied"]) == 1 and len(body["refused"]) == 1
    assert set(tools.plan_leftover_chains(plan_id)["leftovers"]) == {c2}


# ---------- saying no ----------

def test_no_writes_nothing_and_each_dish_still_cooks_its_own(signed_in):
    _household()
    _egg_toast()
    _egg_salad()
    plan_id, (toast, salad) = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"), (THU, "Egg Salad Sandwiches", "lunch"))
    before = _grocery_snapshot()

    res = signed_in.post(f"/api/week/{WEEK}/cook-ahead-confirm", json={"choices": [], "components": []})

    assert res.status_code == 200
    assert _batch_rows(plan_id) == []
    cards = {m["entry_id"]: m for m in tools.get_cooker_view(plan_id)["meals"]}
    for e in (toast, salad):
        assert cards[e]["batch_components"] == [] and cards[e]["components_made_ahead"] == []
        assert cards[e]["is_leftovers"] is False
    assert _grocery_snapshot() == before


# ---------- the week moving under it ----------

def test_reopening_the_week_keeps_the_batch_and_the_gate(signed_in):
    _household()
    _egg_toast()
    _egg_salad()
    plan_id, (toast, salad) = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"), (THU, "Egg Salad Sandwiches", "lunch"))
    tools.approve_weekly_plan(plan_id)
    signed_in.post(f"/api/week/{WEEK}/cook-ahead-confirm", json={"components": [{"key": "boiled:egg", "entry_ids": [toast, salad]}]})

    assert signed_in.post(f"/api/week/{WEEK}/reopen").status_code == 200

    assert len(_batch_rows(plan_id)) == 1
    body = signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()
    assert body["components"] == []  # still answered — not asked twice


def test_swapping_the_cook_day_away_takes_the_batch_with_it(signed_in):
    _household()
    _egg_toast()
    _egg_salad()
    _chili()
    plan_id, (toast, salad) = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"), (THU, "Egg Salad Sandwiches", "lunch"))
    assert isinstance(tools.set_batch_component(plan_id, "boiled:egg", [toast, salad]), dict)

    tools.swap_meal_in_plan(plan_id, TUE, "Turkey Chili", slot="breakfast")

    # The row outlives the swap in the table (swap deletes the entry, not
    # its prep rows) and is pruned on the next read, so nothing shows a
    # "Boil the eggs" task for a dish that is gone.
    cards = {m["entry_id"]: m for m in tools.get_cooker_view(plan_id)["meals"]}
    assert cards[salad]["components_made_ahead"] == []
    assert _batch_rows(plan_id) == []
    # And the eggs are asked about again only if two dishes still boil them
    # — they don't, so the ask stays quiet.
    assert signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()["components"] == []


def test_the_batch_is_a_prep_session_item_on_a_prep_day(signed_in):
    _household()
    _egg_toast()
    _egg_salad()
    plan_id, (toast, salad) = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"), (THU, "Egg Salad Sandwiches", "lunch"))
    tools.set_prep_days([{"weekday": datetime.date.fromisoformat(TUE).strftime("%A").lower()}])
    assert isinstance(tools.set_batch_component(plan_id, "boiled:egg", [toast, salad]), dict)

    sessions = tools.prep_sessions_for_plan(plan_id)
    assert len(sessions) == 1
    item = next(i for i in sessions[0]["items"] if i["kind"] == "batch_component")
    assert item["title"] == "Boil the eggs for Thursday’s Egg Salad Sandwiches too — 10 in all"
    assert item["covers"] == [TUE, THU]


def test_another_household_sees_nothing(client):
    """Same shape as test_cook_ahead_approval's: the week resolves to no
    plan for another household, so both routes 404 and nothing is written."""
    from app import households, security

    _household()
    _egg_toast()
    _egg_salad()
    plan_id, (toast, salad) = _plan((TUE, "Hard-Boiled Eggs and Avocado Toast", "breakfast"), (THU, "Egg Salad Sandwiches", "lunch"))
    assert bc.shared_components(plan_id)

    households.create_household("The Beta Testers", "beta-tester-passphrase")
    res = client.post("/login", data={"password": "beta-tester-passphrase", "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    assert security.COOKIE_NAME in client.cookies

    assert client.get(f"/api/week/{WEEK}/cook-ahead-items").status_code == 404
    confirm = client.post(
        f"/api/week/{WEEK}/cook-ahead-confirm",
        json={"components": [{"key": "boiled:egg", "entry_ids": [toast, salad]}]},
    )
    assert confirm.status_code == 404
    assert _batch_rows(plan_id) == []


# ---------- the block's words (static/shell.js) ----------
# The approval-time ask block went on 2026-09-18 (batch cooking is assumed
# from prep days); the component batch itself is still the Cook view's.

