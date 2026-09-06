"""
Reversing a meal's grocery contribution can strand a line when its display
unit rolls between two measurable units partway through a week's reversals.

Repro: two ordinary dinners each contribute "0.6 lb ground beef" to the
same recipe-week (household of 3, a recipe written for 3 — unscaled), so
approve_weekly_plan rounds the line ONCE to "1.25 lbs" and splits it across
two meal_plan_grocery_links rows (0.75 lb + 0.5 lb, largest-remainder
apportioned). Reversing the first meal used to subtract its 0.75 lb out of
"1.25 lbs" the way _subtract_quantity always had — landing on "0.5 lbs",
which _humanize_grocery_quantity then re-rolls to "8 oz" because it is
under a pound. The second meal's ledger row is still denominated in lb, so
reversing it against a line now displayed in oz found two units that
didn't reconcile and left the line exactly as-is: stranded.

The fix (see grocery._reverse_meal_grocery_contributions and
quantities._sum_ledger_quantities) stops subtracting one contribution out
of the CURRENT display and instead recomputes the line from whatever every
OTHER meal still on the ledger for it adds up to, converting between units
in the same family as it sums. That makes reversal order-independent,
which is what clear_weekly_plan actually needs: it reverses a week's
entries with no particular ordering of its own.

_subtract_quantity itself is still exercised here (see
test_subtract_quantity_reconciles_units_before_giving_up) because it stays
the fallback for a line the ledger can't fully account for — a person's
own standing want, or a hand-added quantity edit.
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


MON, TUE, WED, THU, FRI = (_day(i) for i in range(5))


def _household() -> None:
    for name in ("Alex", "Sam", "Rae"):
        tools.add_member(name)


def _grocery_row(item: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT item, quantity, status, source_weekly_plan_id FROM grocery_items "
        "WHERE item = ? AND status = 'needed'",
        (item,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _ledger_qtys(entry_id: int) -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity FROM meal_plan_grocery_links WHERE meal_plan_entry_id = ?",
        (entry_id,),
    ).fetchall()
    conn.close()
    return [r["quantity"] for r in rows]


def _in_base_unit(qty: str, unit: str) -> float:
    """The numeric amount `qty` represents, expressed in `unit` — a
    conversion-agnostic way to check two differently-labelled quantities
    ("8 oz" vs "0.5 lb") are the same amount, without hardcoding which
    label _humanize_grocery_quantity happens to pick."""
    amount, parsed_unit = quantities._parse_quantity(qty)
    converted = quantities._convert_to_unit(amount, parsed_unit, unit)
    assert converted is not None, f"{qty!r} didn't convert to {unit!r}"
    return converted


# ---------- direct unit coverage ----------


def test_sum_ledger_quantities_converts_across_a_unit_family():
    # A line's own ledger rows, written in whichever unit each was true to
    # at ingest time — exactly what a lb/oz or cup/tbsp roll leaves behind.
    assert quantities._sum_ledger_quantities(["0.5 lb", "8 oz"]) == quantities._humanize_grocery_quantity(1.0, "lb")
    assert quantities._sum_ledger_quantities([]) == ""
    # A freeform contribution can't be summed — callers fall back instead
    # of guessing what "a bunch" adds up to.
    assert quantities._sum_ledger_quantities(["2 cups", "a bunch"]) is None
    # Bare counts (no unit at all) just add.
    assert quantities._sum_ledger_quantities(["2", "1"]) == "3"


def test_subtract_quantity_reconciles_units_before_giving_up():
    # Same amount, different unit — the fallback path has to convert too,
    # for the lines that have no ledger rows to recompute from at all.
    assert grocery._subtract_quantity("1.25 lbs", "0.75 lb") == (
        quantities._humanize_grocery_quantity(0.5, "lb"), False,
    )
    assert grocery._subtract_quantity("8 oz", "0.5 lb") == ("", True)
    # Genuinely different units still refuse to guess.
    assert grocery._subtract_quantity("2 cups", "1 lb") == ("2 cups", False)


# ---------- the repro, both reversal orders ----------


def _beef_plan() -> tuple[int, int, int]:
    """Two ordinary (non-leftover) dinners of the same recipe, each
    contributing an unscaled 0.6 lb of ground beef. Returns (plan_id,
    first_entry_id, second_entry_id)."""
    _household()
    tools.add_recipe(
        "Beef Skillet",
        ingredients=[{"item": "ground beef", "qty": "0.6 lb"}],
        default_servings=3,
    )
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    first = tools.plan_meal(TUE, "Beef Skillet", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    second = tools.plan_meal(THU, "Beef Skillet", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id, first, second


def test_beef_line_rounds_once_to_a_pound_and_a_quarter():
    plan_id, first, second = _beef_plan()
    row = _grocery_row("ground beef")
    assert row is not None
    assert row["quantity"] == "1.25 lbs"
    # The two ledger rows apportion that whole quantum-for-quantum.
    ledger_total = sum(_in_base_unit(q, "oz") for e in (first, second) for q in _ledger_qtys(e))
    assert abs(ledger_total - _in_base_unit("1.25 lbs", "oz")) < 1e-6


def test_reversing_first_meal_then_second_leaves_the_line_exact_then_empty():
    plan_id, first, second = _beef_plan()
    remaining_qty = _ledger_qtys(second)[0]

    grocery._reverse_meal_grocery_contributions(first)
    row = _grocery_row("ground beef")
    assert row is not None, "the line should still exist — one meal still wants it"
    assert abs(_in_base_unit(row["quantity"], "oz") - _in_base_unit(remaining_qty, "oz")) < 1e-6

    grocery._reverse_meal_grocery_contributions(second)
    assert _grocery_row("ground beef") is None


def test_reversing_second_meal_then_first_leaves_the_line_exact_then_empty():
    plan_id, first, second = _beef_plan()
    remaining_qty = _ledger_qtys(first)[0]

    grocery._reverse_meal_grocery_contributions(second)
    row = _grocery_row("ground beef")
    assert row is not None, "the line should still exist — one meal still wants it"
    assert abs(_in_base_unit(row["quantity"], "oz") - _in_base_unit(remaining_qty, "oz")) < 1e-6

    grocery._reverse_meal_grocery_contributions(first)
    assert _grocery_row("ground beef") is None


# ---------- the cup/tbsp analogue ----------


def _broth_plan() -> tuple[int, int, int]:
    _household()
    tools.add_recipe(
        "Herb Broth",
        ingredients=[{"item": "chicken broth", "qty": "0.6 cup"}],
        default_servings=3,
    )
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    first = tools.plan_meal(TUE, "Herb Broth", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    second = tools.plan_meal(THU, "Herb Broth", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id, first, second


def test_broth_line_survives_a_cup_to_tablespoon_roll_in_either_order():
    plan_id, first, second = _broth_plan()
    row = _grocery_row("chicken broth")
    assert row is not None
    assert row["quantity"] == "1.25 cups"

    remaining_qty = _ledger_qtys(second)[0]
    grocery._reverse_meal_grocery_contributions(first)
    row = _grocery_row("chicken broth")
    assert row is not None
    assert abs(_in_base_unit(row["quantity"], "tsp") - _in_base_unit(remaining_qty, "tsp")) < 1e-6
    grocery._reverse_meal_grocery_contributions(second)
    assert _grocery_row("chicken broth") is None


# ---------- packages are untouched by this change ----------


def test_package_line_survives_reversals_until_its_last_link():
    _household()
    tools.add_recipe(
        "Pancakes", ingredients=[{"item": "maple syrup", "qty": "1 bottle"}], default_servings=3,
    )
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    first = tools.plan_meal(MON, "Pancakes", slot="breakfast", weekly_plan_id=plan_id)["entry_id"]
    second = tools.plan_meal(FRI, "Pancakes", slot="breakfast", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _grocery_row("maple syrup")["quantity"] == "1 bottle"

    grocery._reverse_meal_grocery_contributions(first)
    row = _grocery_row("maple syrup")
    assert row is not None and row["quantity"] == "1 bottle", "still wanted by the second meal"

    grocery._reverse_meal_grocery_contributions(second)
    assert _grocery_row("maple syrup") is None


# ---------- a hand-added standing want is never recomputed away ----------


def test_hand_added_standing_want_survives_its_meals_being_reversed():
    _household()
    tools.add_grocery_item("carrots", quantity="")
    tools.add_recipe("Roast Veg", ingredients=[{"item": "carrots", "qty": "2"}], default_servings=3)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    entry = tools.plan_meal(TUE, "Roast Veg", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")

    row = _grocery_row("carrots")
    assert row is not None
    assert row["quantity"] == "2"
    assert row["source_weekly_plan_id"] is None, "merging a plan's amount into a standing want must not take it over"

    grocery._reverse_meal_grocery_contributions(entry)
    row = _grocery_row("carrots")
    assert row is not None, "the person's own want survives even once every meal that used it is gone"
    assert row["quantity"] == ""


# ---------- full-cycle regression: shared ingredient + a leftover chain ----------


def test_approve_then_clear_empties_a_week_with_shared_ingredients_and_a_leftover_chain():
    _household()
    tools.add_recipe(
        "Tacos",
        ingredients=[{"item": "cheese", "qty": "0.5 cup"}, {"item": "tortillas", "qty": "1 bag"}],
        default_servings=3,
    )
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "2 cups"}], default_servings=3)

    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(MON, "Tacos", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(FRI, "Tacos", slot="dinner", weekly_plan_id=plan_id)
    tue = tools.plan_meal(TUE, "Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.plan_meal(
        THU, "Chili", slot="dinner", weekly_plan_id=plan_id,
        derived_from={"links_to": f"{TUE}:dinner"},
    )
    tools.repair_leftover_chains(plan_id)

    tools.approve_weekly_plan(plan_id, "Emily")
    assert _grocery_row("cheese") is not None
    assert _grocery_row("tortillas") is not None
    assert _grocery_row("beans") is not None

    tools.clear_weekly_plan(plan_id)
    assert tools.list_grocery_list() == []
