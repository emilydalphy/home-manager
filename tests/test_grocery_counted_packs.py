"""
Counted packs — Loop Board 2026-09-13, "Eggs · 4 dozen".

Every recipe the app had written said eggs were "1 dozen" (the prompt asked
for the store's unit), and a week with four egg meals added up to four
cartons. Emily: "it should add up how many eggs, not the whole dozen for
each recipe." Garlic had the same shape on the same list: "6 heads + 8
cloves".

What is pinned here:

1. A recipe that wrote the PACK ("1 dozen") contributes what the dish uses
   (the cooking table's eggs for its own table), so four meals of a
   two-serving egg recipe buy one carton, not four.
2. A recipe that wrote the PIECES ("4" eggs) is taken at its word, and the
   week's pieces add up and round to whole packs once: 4 + 2 + 2 + 6 = 14
   eggs is 2 dozen.
3. Reversal recomputes in the same family — dropping a meal leaves the
   line in dozens, never "8 egg".
4. A head of lettuce is not ten cloves of anything: "head" still rounds up
   to whole heads exactly as before.
"""
import datetime

from app import tools
from app.db import get_conn
from app.tools import grocery, quantities


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


MON, TUE, WED, THU, FRI, SAT = (_day(i) for i in range(6))


def _household(size: int = 2) -> None:
    for name in ("Alex", "Sam", "Rae", "Kit")[:size]:
        tools.add_member(name)


def _row(item: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity, status FROM grocery_items WHERE lower(item) = lower(?) "
        "AND status = 'needed'",
        (item,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _ledger(item: str) -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity FROM meal_plan_grocery_links WHERE lower(item) = lower(?) ORDER BY id",
        (item,),
    ).fetchall()
    conn.close()
    return [r["quantity"] for r in rows]


# ---------- the unit family ----------


def test_eggs_and_dozens_are_one_family_written_in_whole_dozens():
    assert quantities._humanize_grocery_quantity(14, "egg") == "2 dozen"
    assert quantities._humanize_grocery_quantity(4, "egg") == "1 dozen"
    assert quantities._humanize_grocery_quantity(12, "egg") == "1 dozen"
    assert quantities._humanize_grocery_quantity(1, "dozen") == "1 dozen"
    # Ledger rows written in either unit add up in eggs and come back as packs.
    assert quantities._sum_ledger_quantities(["2 eggs", "2 egg", "1 dozen"]) == "2 dozen"
    assert quantities._sum_ledger_quantities(["0.166666666667 dozen", "0.5 dozen"]) == "1 dozen"


def test_the_recipes_own_words_for_eggs_parse_as_the_family():
    assert quantities._parse_quantity("4 eggs") == (4.0, "egg")
    assert quantities._parse_quantity("4 large eggs") == (4.0, "egg")
    assert quantities._parse_quantity("2 dozen eggs") == (2.0, "dozen")
    assert quantities._parse_quantity("1 dozen") == (1.0, "dozen")


def test_cloves_round_up_to_heads_but_a_head_of_lettuce_is_untouched():
    assert quantities._humanize_grocery_quantity(8, "clove") == "1 head"
    assert quantities._humanize_grocery_quantity(26, "clove") == "3 heads"
    # A lettuce line: 1.5 heads rounds up to 2, exactly as it always did.
    assert quantities._humanize_grocery_quantity(1.5, "head") == "2 heads"
    assert quantities._humanize_grocery_quantity(2, "head") == "2 heads"


# ---------- the week ----------


def test_four_meals_of_a_one_dozen_recipe_buy_one_carton():
    # Emily's exact list: Hard-Boiled Eggs (serves 2) twice, Hard-Boiled Eggs
    # and Avocado Toast (serves 2) twice, each saved as "1 dozen".
    _household()
    tools.add_recipe("Hard-Boiled Eggs", ingredients=[{"item": "Eggs", "qty": "1 dozen"}], default_servings=2)
    tools.add_recipe(
        "Hard-Boiled Eggs and Avocado Toast",
        ingredients=[{"item": "Eggs", "qty": "1 dozen"}, {"item": "Avocado", "qty": "2"}],
        default_servings=2,
    )
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(MON, "Hard-Boiled Eggs and Avocado Toast", slot="breakfast", weekly_plan_id=plan_id)
    tools.plan_meal(TUE, "Hard-Boiled Eggs", slot="snack", weekly_plan_id=plan_id)
    tools.plan_meal(WED, "Hard-Boiled Eggs", slot="snack", weekly_plan_id=plan_id)
    tools.plan_meal(THU, "Hard-Boiled Eggs and Avocado Toast", slot="breakfast", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")

    # Four meals × the 2 eggs a two-serving egg dish uses = 8 eggs → 1 dozen.
    assert _row("Eggs")["quantity"] == "1 dozen"
    ledger = _ledger("Eggs")
    assert len(ledger) == 4
    assert quantities._sum_ledger_quantities(ledger) == "1 dozen"


def test_eggs_written_as_counts_add_up_and_round_to_dozens_once():
    _household()
    tools.add_recipe("Frittata", ingredients=[{"item": "Eggs", "qty": "6"}], default_servings=2)
    tools.add_recipe("Shakshuka", ingredients=[{"item": "Eggs", "qty": "4"}], default_servings=2)
    tools.add_recipe("Egg Salad", ingredients=[{"item": "Eggs", "qty": "2 eggs"}], default_servings=2)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(MON, "Frittata", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(TUE, "Shakshuka", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(WED, "Egg Salad", slot="lunch", weekly_plan_id=plan_id)
    tools.plan_meal(THU, "Egg Salad", slot="lunch", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")

    # 6 + 4 + 2 + 2 = 14 eggs → 2 dozen, rounded once for the week.
    assert _row("Eggs")["quantity"] == "2 dozen"


def test_dropping_a_meal_recomputes_the_line_in_dozens():
    _household()
    tools.add_recipe("Frittata", ingredients=[{"item": "Eggs", "qty": "6"}], default_servings=2)
    tools.add_recipe("Shakshuka", ingredients=[{"item": "Eggs", "qty": "8"}], default_servings=2)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    frittata = tools.plan_meal(MON, "Frittata", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    shakshuka = tools.plan_meal(TUE, "Shakshuka", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _row("Eggs")["quantity"] == "2 dozen"  # 14 eggs

    grocery._reverse_meal_grocery_contributions(shakshuka)
    assert _row("Eggs")["quantity"] == "1 dozen"  # 6 eggs left, still a carton
    grocery._reverse_meal_grocery_contributions(frittata)
    assert _row("Eggs") is None


def test_garlic_by_the_head_and_by_the_clove_lands_on_one_line_in_heads():
    _household(4)  # four eaters, recipes for four: no attendance scaling
    tools.add_recipe("Garlic Shrimp", ingredients=[{"item": "Garlic", "qty": "1 head"}], default_servings=4)
    tools.add_recipe("Hummus", ingredients=[{"item": "Garlic", "qty": "4 cloves"}], default_servings=4)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    for day in (MON, TUE, WED):
        tools.plan_meal(day, "Garlic Shrimp", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(THU, "Hummus", slot="snack", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")

    # Three "1 head" meals use the table's 3 cloves each (9) + 4 cloves = 13
    # cloves → 2 heads. Not "3 heads + 4 cloves".
    assert _row("Garlic")["quantity"] == "2 heads"


def test_a_head_of_lettuce_still_buys_one_per_meal():
    _household()
    tools.add_recipe("Caesar Salad", ingredients=[{"item": "Romaine lettuce", "qty": "1 head"}], default_servings=2)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(MON, "Caesar Salad", slot="lunch", weekly_plan_id=plan_id)
    tools.plan_meal(TUE, "Caesar Salad", slot="lunch", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _row("Romaine lettuce")["quantity"] == "2 heads"


def test_a_measured_amount_of_a_pack_item_is_left_alone():
    # "2 tbsp minced garlic" and "1 cup egg whites" are measures, not counts.
    _household()
    tools.add_recipe("Meringue", ingredients=[{"item": "Egg whites", "qty": "1 cup"}], default_servings=2)
    tools.add_recipe("Stir Fry", ingredients=[{"item": "Garlic", "qty": "2 tbsp"}], default_servings=2)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(MON, "Meringue", slot="snack", weekly_plan_id=plan_id)
    tools.plan_meal(TUE, "Stir Fry", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _row("Egg whites")["quantity"] == "1 cup"
    assert _row("Garlic")["quantity"] == "2 tbsp"


def test_a_hand_added_dozen_and_a_plan_do_not_glue_together():
    # A person's own "1 dozen" is a standing want on its own line; the plan's
    # eggs are the plan's (see grocery._merge_target). Neither becomes
    # "1 dozen + 8 egg".
    _household()
    tools.add_grocery_item("Eggs", quantity="1 dozen", added_by="Emily")
    tools.add_recipe("Frittata", ingredients=[{"item": "Eggs", "qty": "8"}], default_servings=2)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(MON, "Frittata", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT quantity, source_weekly_plan_id FROM grocery_items WHERE item = 'Eggs' ORDER BY id"
    ).fetchall()]
    conn.close()
    # The person's dozen absorbed the plan's 8 eggs cleanly (same family),
    # as any same-unit add does, and the row stays the person's.
    assert len(rows) == 1
    assert rows[0]["quantity"] == "2 dozen"
    assert rows[0]["source_weekly_plan_id"] is None


def test_a_meal_added_after_approval_does_not_add_a_carton_to_a_carton():
    # Two ingest passes onto one line: the week's approval, then dinners
    # planned afterwards. Each pass rounds its own eggs up to a carton; the
    # line is re-read from the ledger so 3 + 3 eggs is one dozen, not two.
    _household()
    tools.add_recipe("Egg Salad", ingredients=[{"item": "Eggs", "qty": "3"}], default_servings=2)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(MON, "Egg Salad", slot="lunch", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _row("Eggs")["quantity"] == "1 dozen"

    tools.plan_meal(TUE, "Egg Salad", slot="lunch", weekly_plan_id=plan_id, add_ingredients_to_grocery_list=True)
    assert _row("Eggs")["quantity"] == "1 dozen"  # 6 eggs
    for day in (WED, THU, FRI):
        tools.plan_meal(day, "Egg Salad", slot="lunch", weekly_plan_id=plan_id, add_ingredients_to_grocery_list=True)
    assert _row("Eggs")["quantity"] == "2 dozen"  # 15 eggs
