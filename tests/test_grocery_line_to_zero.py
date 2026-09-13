"""
Dropping one night of a repeated dish could take the grocery line to zero,
and which night you dropped decided whether the line was right.

Per-entry ledger shares used to be recorded ROUNDED — each meal's
largest-remainder share of the already-rounded line (recipes._apportion,
2026-09-05) — while grocery._reverse_meal_grocery_contributions recomputed
the line by summing the survivors' shares (2026-09-05, later the same day).
Summing rounded shares is lossy, and the loss lands on the shopping list:

  A. Three nights of a recipe written for 12, in a household of 3, want a
     quarter of a lemon each. The line rounds once to "1"; the shares
     apportion to 1 / 0 / 0. Drop the FIRST night and the two survivors sum
     to nothing — "Lemon · 0" on the Grocery tab with two dinners still
     planned.

  B. Two nights of a recipe written for 4 wanting 2 cans each, household of
     3: 1.5 + 1.5 = 3 cans on the line, shares 2 / 1. Drop the second night
     and the line reads 2 cans; drop the first and it reads 1 — same plan,
     same surviving dinner, two different answers.

The fix (recipes._ledger_share) records what each meal actually asked for,
unrounded, so the survivors can be summed and rounded ONCE — the same
single rounding _week_bought_amount does on the way in. The phantom
remainder _apportion was written to prevent cannot come back: nothing
subtracts any more, the last meal off a line takes the row with it, and
every meal before that re-derives the line from scratch.

quantities._sum_ledger_quantities also floors a line that still has meals
behind it at the smallest buyable amount, which is what protects ledger
rows written BEFORE this change — nothing can recover their unrounded
value, but "0" is never the honest answer for a line something still cooks
from.
"""
import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import grocery, quantities, recipes, weekly_plan


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


MON, TUE, WED = _day(0), _day(1), _day(2)


@pytest.fixture
def family_of_three():
    for name in ("Emily", "Vineeth", "Rae"):
        tools.add_member(name)


def _qty(item: str) -> str | None:
    return next((i["quantity"] for i in tools.list_grocery_list() if i["item"] == item), None)


def _links(item: str) -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity FROM meal_plan_grocery_links WHERE item = ? AND household_id = ? ORDER BY id",
        (item, tools.household_id()),
    ).fetchall()
    conn.close()
    return [r["quantity"] for r in rows]


def _plan(recipe: str, ingredient: dict, servings: int, days: list[str]) -> tuple[int, list[int]]:
    tools.add_recipe(recipe, ingredients=[ingredient], default_servings=servings)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    entries = [
        tools.plan_meal(d, recipe, slot="dinner", weekly_plan_id=plan_id)["entry_id"]
        for d in days
    ]
    tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id, entries


LEMON = {"item": "Lemon", "qty": "1", "category": "produce"}
TOMATOES = {"item": "Tomatoes", "qty": "2 cans", "category": "pantry"}


def _lemon_week():
    """Reproduction A: three nights, a recipe for twelve, a household of
    three — a quarter of a lemon per night, one lemon on the list."""
    return _plan("Lemon Cake", LEMON, 12, [MON, TUE, WED])


def _tomato_week():
    """Reproduction B: two nights, a recipe for four wanting two cans, a
    household of three — one and a half cans a night, three on the list."""
    return _plan("Tomato Stew", TOMATOES, 4, [MON, TUE])


# ---------- reproduction A: the line went to zero ----------

def test_dropping_the_first_of_three_nights_does_not_take_the_line_to_zero(family_of_three):
    plan_id, entries = _lemon_week()
    assert _qty("Lemon") == "1"

    grocery._reverse_meal_grocery_contributions(entries[0])

    assert _qty("Lemon") == "1", "two dinners still cook this; the list must still say to buy one"


def test_dropping_a_later_night_leaves_the_same_line_as_dropping_the_first(family_of_three):
    plan_id, entries = _lemon_week()
    grocery._reverse_meal_grocery_contributions(entries[1])
    assert _qty("Lemon") == "1"


def test_the_last_night_off_a_lemon_line_takes_the_row_with_it(family_of_three):
    plan_id, entries = _lemon_week()
    for entry_id in entries:
        grocery._reverse_meal_grocery_contributions(entry_id)
    assert _qty("Lemon") is None, "nothing cooks it any more — no phantom quarter-lemon left behind"


# ---------- reproduction B: the answer depended on which night went ----------

@pytest.mark.parametrize("dropped", [0, 1])
def test_which_night_is_dropped_does_not_change_what_the_survivor_needs(dropped, family_of_three):
    plan_id, entries = _tomato_week()
    assert _qty("Tomatoes") == "3 cans"

    grocery._reverse_meal_grocery_contributions(entries[dropped])

    assert _qty("Tomatoes") == "2 cans", "one dinner wanting 1.5 cans buys 2, whichever night went"


# ---------- the same seam through the writes people actually use ----------

def test_swapping_a_night_out_does_not_take_the_line_to_zero(family_of_three):
    """swap_meal_in_plan is the write behind every swap in the app — chat,
    the Review stepper's 'Change one', swap_in_place, resolve_open_slot,
    add_dish_day."""
    plan_id, entries = _lemon_week()
    tools.add_recipe(
        "Carrot Soup", ingredients=[{"item": "Carrot", "qty": "3", "category": "produce"}],
        default_servings=3,
    )

    tools.swap_meal_in_plan(plan_id, MON, "Carrot Soup", slot="dinner", old_entry_id=entries[0])

    assert _qty("Lemon") == "1"
    assert _qty("Carrot") == "3"


def test_the_review_steppers_minus_does_not_take_the_line_to_zero(family_of_three):
    """drop_dish_from_day is the '−' on Check the week."""
    plan_id, entries = _lemon_week()

    result = weekly_plan.drop_dish_from_day(plan_id, entries[0])

    assert result.get("refused") is None
    assert _qty("Lemon") == "1"


def test_a_partial_period_takeover_leaves_the_days_it_kept_shopped_for(family_of_three):
    """_release_plan_days takes SOME of a plan's days off it — the atomic
    period takeover. It is the one caller that removes part of a rounding
    group rather than all of it, so it is where reproduction A bites
    without any single-meal control being touched at all."""
    plan_id, entries = _lemon_week()

    weekly_plan._release_plan_days(plan_id, [MON])

    assert _qty("Lemon") == "1", "Tuesday and Wednesday still cook it"


def test_clearing_the_whole_week_still_leaves_nothing_behind(family_of_three):
    """The invariant _apportion was written to protect. It holds without
    apportionment because reversal recomputes rather than subtracts."""
    _lemon_week()
    assert tools.list_grocery_list() != []

    tools.clear_weekly_plan()

    assert tools.list_grocery_list() == []


# ---------- what the ledger now holds ----------

def test_the_ledger_holds_each_meals_unrounded_share_and_rounds_once_to_the_line(family_of_three):
    _lemon_week()
    shares = _links("Lemon")
    assert shares == ["0.25", "0.25", "0.25"], "a quarter of a lemon each, not 1/0/0"
    assert quantities._sum_ledger_quantities(shares) == _qty("Lemon") == "1"


def test_a_measurable_share_is_recorded_in_the_lines_own_unit(family_of_three):
    """0.6 lb apiece, rounded once to 1.25 lbs, and re-derivable from
    either survivor — the lb/oz roll the 2026-09-05 reversal fix exists
    for, now with nothing lost to rounding on the way in."""
    plan_id, entries = _plan(
        "Beef Skillet", {"item": "Ground beef", "qty": "0.6 lb", "category": "meat/seafood"},
        3, [MON, TUE],
    )
    assert _qty("Ground beef") == "1.25 lbs"
    assert _links("Ground beef") == ["0.6 lb", "0.6 lb"]

    grocery._reverse_meal_grocery_contributions(entries[0])

    assert _qty("Ground beef") == "9.5 oz"


# ---------- the floor, for rows written before this change ----------

def test_a_line_with_meals_behind_it_never_reads_zero_even_on_an_old_ledger(family_of_three):
    """
    Ledger rows written before 2026-09-13 carry apportioned shares and
    nothing can recover what those meals actually wanted. The line still
    must not read "0" while something is cooking from it, so the recompute
    floors at the smallest amount a shopper can buy.
    """
    plan_id, entries = _lemon_week()
    conn = get_conn()
    for entry_id, legacy in zip(entries, ("1", "0", "0")):
        conn.execute(
            "UPDATE meal_plan_grocery_links SET quantity = ? WHERE meal_plan_entry_id = ?",
            (legacy, entry_id),
        )
    conn.commit()
    conn.close()

    grocery._reverse_meal_grocery_contributions(entries[0])

    assert _qty("Lemon") == "1"


def test_the_floor_reaches_a_measurable_line_too():
    assert quantities._sum_ledger_quantities(["0", "0"]) == "1"
    assert quantities._sum_ledger_quantities(["0 cups", "0 cups"]) == "0.25 tsp"
    # An empty ledger is still "nothing left", not "a little of it".
    assert quantities._sum_ledger_quantities([]) == ""


# ---------- everything this change must NOT touch ----------

def test_a_sealed_package_still_survives_until_its_last_meal(family_of_three):
    """A package is added once for the whole recipe-week and is never
    apportioned or re-derived — it comes off when the last meal holding a
    link to it does."""
    plan_id, entries = _plan(
        "Pancakes", {"item": "Maple syrup", "qty": "1 bottle", "category": "pantry"},
        3, [MON, TUE, WED],
    )
    assert _qty("Maple syrup") == "1 bottle"
    assert _links("Maple syrup") == ["1 bottle", "1 bottle", "1 bottle"]

    grocery._reverse_meal_grocery_contributions(entries[0])
    assert _qty("Maple syrup") == "1 bottle"
    grocery._reverse_meal_grocery_contributions(entries[1])
    assert _qty("Maple syrup") == "1 bottle"
    grocery._reverse_meal_grocery_contributions(entries[2])
    assert _qty("Maple syrup") is None


def test_a_line_already_in_the_cart_is_left_exactly_alone(family_of_three):
    plan_id, entries = _lemon_week()
    item_id = next(i["id"] for i in tools.list_grocery_list() if i["item"] == "Lemon")
    tools.mark_grocery_item(item_id, "purchased")

    grocery._reverse_meal_grocery_contributions(entries[0])

    row = next(i for i in tools.list_grocery_list(status="purchased") if i["item"] == "Lemon")
    assert row["quantity"] == "1"


def test_one_households_reversal_cannot_read_or_move_anothers_line():
    from app import households

    for name in ("Emily", "Vineeth", "Rae"):
        tools.add_member(name)
    _lemon_week()
    beta = households.create_household("The Beta Testers", "a-distinct-passphrase")

    with tools.use_household(beta):
        for name in ("Alex", "Sam", "Rae"):
            tools.add_member(name)
        _, beta_entries = _lemon_week()
        assert _qty("Lemon") == "1"
        grocery._reverse_meal_grocery_contributions(beta_entries[0])
        assert _qty("Lemon") == "1"

    assert _qty("Lemon") == "1"
    assert len(_links("Lemon")) == 3, "household 1's own ledger rows are untouched"


# ---------- the one path that still SUBTRACTS ----------
#
# A household's own standing want can never be recomputed: the person's own
# amount is in the line and no ledger row describes it. So it subtracts —
# and what it subtracts has to be the delta of the ROUNDED plan
# contribution, because the ingest only ever added a rounded total. Taking
# back each meal's unrounded share instead leaves a remainder that rounds
# straight back up, and the line climbs a little every week for ever.

def _onion_week(monday: datetime.date) -> int:
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    for i in range(3):
        tools.plan_meal(
            (monday + datetime.timedelta(days=i)).isoformat(), "Onion Bake",
            slot="dinner", weekly_plan_id=plan_id,
        )
    tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id


def test_a_standing_want_comes_back_to_exactly_what_the_household_asked_for(family_of_three):
    """
    Four approve-and-clear cycles on a hand-added "3 Onions", under three
    dinners of a recipe written for 4 that wants 2 onions — 1.5 apiece, so
    the week rounds to 5 and the line reads 8.

    Before this was fixed the line read 3 -> 5 -> 7 -> 9 -> 11 after each
    clear: +2 onions a week, never coming back, on every hand add, chat
    add, offline add, photo-scan add and every staples line (they are all
    standing wants — add_grocery_item keeps source_weekly_plan_id NULL on
    merge by design), and clear_stale_grocery_items exempts them so nothing
    ever corrects it.
    """
    tools.add_recipe(
        "Onion Bake", ingredients=[{"item": "Onions", "qty": "2", "category": "produce"}],
        default_servings=4,
    )
    tools.add_grocery_item("Onions", quantity="3", category="produce")

    for week in range(4):
        plan_id = _onion_week(_monday() + datetime.timedelta(days=7 * week))
        assert _qty("Onions") == "8", f"week {week}: 3 of the household's own plus 5 for the week"
        tools.clear_weekly_plan(plan_id)
        assert _qty("Onions") == "3", f"week {week}: back to exactly what the household asked for"


def test_a_standing_want_loses_the_rounded_delta_when_one_night_is_swapped(family_of_three):
    """One dinner off three: the week's 5 onions becomes 3, so 2 come off
    the line — not the 1.5 that dinner wanted, which would leave 7."""
    tools.add_recipe(
        "Onion Bake", ingredients=[{"item": "Onions", "qty": "2", "category": "produce"}],
        default_servings=4,
    )
    tools.add_recipe(
        "Carrot Soup", ingredients=[{"item": "Carrot", "qty": "3", "category": "produce"}],
        default_servings=3,
    )
    tools.add_grocery_item("Onions", quantity="3", category="produce")
    plan_id = _onion_week(_monday())
    assert _qty("Onions") == "8"

    tools.swap_meal_in_plan(plan_id, MON, "Carrot Soup", slot="dinner")

    assert _qty("Onions") == "6"


def test_a_standing_want_is_only_ever_blanked_never_deleted(family_of_three):
    """
    A line the household asked for directly. The plan borrowed it; it
    doesn't own it — so the plan's share comes off and the person's own
    amount stays, exactly.
    """
    tools.add_grocery_item("Lemon", quantity="2", category="produce")
    tools.add_recipe("Lemon Cake", ingredients=[LEMON], default_servings=12)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    entry = tools.plan_meal(MON, "Lemon Cake", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _qty("Lemon") == "3", "two of the household's own, plus one for the dinner"

    grocery._reverse_meal_grocery_contributions(entry)

    assert _qty("Lemon") == "2", "the household's own want survives, unchanged"


def test_a_standing_want_the_ledger_cannot_read_still_falls_back(family_of_three):
    """A freeform contribution ("a bunch") can't be rounded or summed, so
    the delta declines and the old subtract-what-is-in-front-of-you answer
    is still the best available one."""
    tools.add_grocery_item("Parsley", quantity="a bunch", category="produce")
    tools.add_recipe(
        "Herby Rice", ingredients=[{"item": "Parsley", "qty": "a bunch", "category": "produce"}],
        default_servings=3,
    )
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    entry = tools.plan_meal(MON, "Herby Rice", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")

    grocery._reverse_meal_grocery_contributions(entry)

    assert _qty("Parsley") is not None


# ---------- the rounding this all rests on ----------

def test_a_countable_thing_rounds_UP_and_a_measurable_one_to_the_nearest_quarter():
    """
    Emily's 2026-09-05 rule: you cannot buy 12.75 peppers, and an extra
    pepper costs a pepper while a missing one costs the dinner. This is the
    only test that pins the DIRECTION — turning that ceil into a round
    passed the whole suite before this existed, and the reversal fix rests
    on _week_bought_amount and _humanize_grocery_quantity rounding
    identically (they are one function now, quantities._shopping_round).
    """
    assert quantities._shopping_round(12.25, None) == (13.0, None)
    assert quantities._shopping_round(12.75, None) == (13.0, None)
    assert recipes._week_bought_amount(12.25, None) == (13.0, None)
    assert quantities._humanize_grocery_quantity(12.25, None) == "13"
    # Measurable: rolled up to the unit that reads best, nearest quarter.
    assert quantities._shopping_round(1.2, "lb") == (1.25, "lb")
    assert quantities._humanize_grocery_quantity(52, "tbsp") == "3.25 cups"


def test_a_week_that_wants_a_hair_over_twelve_peppers_buys_thirteen(family_of_three):
    """The same rule end to end, so it is pinned where the household sees
    it and not only at the helper."""
    tools.add_recipe(
        "Pepper Bake", ingredients=[{"item": "Peppers", "qty": "5.44", "category": "produce"}],
        default_servings=4,
    )
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    for day in (MON, TUE, WED):
        tools.plan_meal(day, "Pepper Bake", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")

    # 5.44 x 3/4 x 3 nights = 12.24 — rounded down it would be 12.
    assert _qty("Peppers") == "13"


# ---------- the neighbouring write that had the same bug ----------

def test_marking_one_night_away_does_not_take_the_line_to_zero(family_of_three):
    """An attendance edit reverses that slot's ingredients through the same
    helper — no stepper, no swap, nobody near the Review screen."""
    _lemon_week()
    assert _qty("Lemon") == "1"

    tools.set_away_stretch(MON, "dinner", MON, "dinner", reason="out")

    assert _qty("Lemon") == "1", "Tuesday and Wednesday still cook it"


# ---------- a ledger row too small for %g ----------

def test_a_vanishingly_small_share_cannot_delete_a_line_other_meals_want():
    """
    "%g" writes anything under 1e-4 as "1e-05", which _parse_quantity reads
    as nothing — and one unreadable row makes the WHOLE line unaccountable,
    dropping reversal into _subtract_quantity, where an identical
    current-and-remove string means "this contribution is the whole line"
    and deletes a row other meals still link to. _plain_number writes it
    out instead.
    """
    assert quantities._plain_number(0.00001) == "0.00001"
    assert quantities._plain_number(1e6) == "1000000"
    assert quantities._format_quantity(0.00001, "cup") == "0.00001 cup"
    assert quantities._parse_quantity(quantities._format_quantity(0.00001, "cup")) is not None
    assert quantities._sum_ledger_quantities(["0.00001 cup", "0.00001 cup"]) is not None


def test_a_bucket_contributing_nothing_is_not_printed_beside_one_that_does():
    """The floor guarantees a line never reads "0"; it must not invent a
    whole pepper next to a real "2 cups"."""
    assert quantities._sum_ledger_quantities(["0", "2 cups"]) == "2 cups"
    assert quantities._sum_ledger_quantities(["0", "0"]) == "1"


def test_thirds_of_a_thing_add_back_up_to_a_whole_one(family_of_three):
    """
    The ledger is written finer than the list (recipes._LEDGER_SIG). At the
    six significant figures a person reads, two thirds written three times
    is 2.000001, which rounds UP to three — so reversal thought the plan
    had put three onions on a line it had only put two on, and took the
    household's own one away with them. Household of four, a recipe for six
    wanting one: two thirds a night.
    """
    tools.add_member("Sam")
    tools.add_recipe(
        "Third Pie", ingredients=[{"item": "Onions", "qty": "1", "category": "produce"}],
        default_servings=6,
    )
    tools.add_grocery_item("Onions", quantity="1", category="produce")
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    for day in (MON, TUE, WED):
        tools.plan_meal(day, "Third Pie", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _qty("Onions") == "3", "the household's own 1, plus 2 for three two-thirds nights"

    tools.clear_weekly_plan(plan_id)

    assert _qty("Onions") == "1", "the household's own onion, still there"


def test_a_measurable_standing_want_does_not_creep_up_a_quarter_a_week(family_of_three):
    """
    The other half of the same lesson. Rounding the plan's before and after
    each in whatever unit it rolls to — 1.25 cups against 10.75 tbsp —
    leaves a difference that is not a quarter of a cup, so the line ends
    the week above where it started. quantities._round_in_unit rounds both
    in the line's own unit. Household of one, a recipe for twelve wanting
    four cups: a third of a cup a night, over four nights.
    """
    conn = get_conn()
    conn.execute("DELETE FROM members WHERE household_id = ? AND name != 'Emily'", (tools.household_id(),))
    conn.commit()
    conn.close()
    tools.add_recipe(
        "Cup Cake", ingredients=[{"item": "Milk", "qty": "4 cups", "category": "dairy"}],
        default_servings=12,
    )
    tools.add_grocery_item("Milk", quantity="2 cups", category="dairy")
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    entries = [
        tools.plan_meal(_day(o), "Cup Cake", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
        for o in range(4)
    ]
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _qty("Milk") == "3.25 cups"

    # Every intermediate matters, not just the end: rounding each side in
    # its own rolled unit lands on 3 / 12.75 / 7.25 / 2 cups, which happens
    # to telescope back to 2 while showing the household nonsense on the
    # way. A quarter-cup at a time, ending where it started.
    steps = []
    for entry_id in entries:
        grocery._reverse_meal_grocery_contributions(entry_id)
        steps.append(_qty("Milk"))
    assert steps == ["3 cups", "2.75 cups", "2.25 cups", "2 cups"]


# ---------- the display unit rolling mid-sequence ----------
#
# A standing want's line is a rounded string, and _restate_standing_want
# re-derives the household's own amount out of it at every removal. When
# the line's display unit ROLLS between removals (1.25 lbs, then ounces),
# the quantum the plan's share is rounded at changes underneath the
# sequence — the first removal over-credits the plan at a quarter-POUND and
# the later ones, now on a quarter-ounce, never give it back. Every one of
# these went below the household's own amount before the plan's share was
# anchored to the unit the INGEST rounded it in, and the second took a
# hand-typed "2 oz" down to no quantity at all.

@pytest.mark.parametrize("standing,recipe_qty,servings,household,nights,expected", [
    ("8 oz", "1 lb", 6, 2, 2, "8 oz"),        # was 6.75 oz; main 9.25 oz
    ("2 oz", "0.6 lb", 8, 4, 3, "1.5 oz"),    # was BLANK; main 1.5 oz
    ("500 ml", "1 l", 6, 1, 3, "500 ml"),     # was 416.75 ml and still falling
    ("0.5 cup", "4 tbsp", 6, 3, 4, "8 tbsp"), # was 6 tbsp
])
def test_a_rolling_display_unit_cannot_eat_the_households_own_amount(
    standing, recipe_qty, servings, household, nights, expected,
):
    for i in range(household):
        tools.add_member(f"M{i}")
    tools.add_recipe(
        "Roller", ingredients=[{"item": "Thing", "qty": recipe_qty, "category": "other"}],
        default_servings=servings,
    )
    tools.add_grocery_item("Thing", quantity=standing, category="other")
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    entries = [
        tools.plan_meal(_day(o), "Roller", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
        for o in range(nights)
    ]
    tools.approve_weekly_plan(plan_id, "Emily")

    for entry_id in entries:
        grocery._reverse_meal_grocery_contributions(entry_id)

    assert _qty("Thing") == expected


def test_a_hand_typed_line_is_never_left_with_no_quantity_at_all(family_of_three):
    """
    The strongest of the two rules this path is held to. "2 oz" of
    something, three dinners of a recipe that wants more than that: the
    plan's share is bigger than the household's own, and every removal
    re-derives that own out of a line displayed in POUNDS.
    """
    tools.add_member("Sam")
    tools.add_recipe(
        "Roller", ingredients=[{"item": "Thing", "qty": "0.6 lb", "category": "other"}],
        default_servings=8,
    )
    tools.add_grocery_item("Thing", quantity="2 oz", category="other")
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    entries = [
        tools.plan_meal(_day(o), "Roller", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
        for o in range(3)
    ]
    tools.approve_weekly_plan(plan_id, "Emily")

    for entry_id in entries:
        grocery._reverse_meal_grocery_contributions(entry_id)
        assert (_qty("Thing") or "").strip(), "a line the household typed always says how much"


def test_the_snap_breaks_a_tie_upward_not_downward():
    """
    Exactly-half is the case that keeps happening, because the display
    rounding that puts the error there rounds half DOWN. A household's 8 oz
    on a line shown in pounds derives as 0.375 lb, dead between two
    quarters; rounding that down hands them 4 oz.
    """
    assert quantities._snap_to_unit_quantum(0.375, "lb") == 0.5
    assert quantities._snap_to_unit_quantum(0.625, "lb") == 0.75
    assert quantities._snap_to_unit_quantum(2.078125, "cup") == 2.0
    assert quantities._snap_to_unit_quantum(2.5, None) == 3.0
    assert quantities._snap_to_unit_quantum(3.0, None) == 3.0


def test_the_snap_never_annihilates_a_small_amount_on_a_coarse_line():
    """Two ounces is less than half a quarter-pound. Snapping it to the
    line's quantum would be zero, and zero blanks the line — so the snap
    stands down rather than round a real amount away."""
    tools.add_grocery_item("Thing", quantity="2 oz", category="other")
    restated = grocery._restate_standing_want("1 lb", 14.4, 9.6, "oz")
    assert restated is not None
    assert restated[1] is False
    assert restated[0] == "11 oz"


def test_a_hand_edit_below_the_plans_share_does_not_blank_the_line(family_of_three):
    """
    The `max(0.0, ...)` floor, which was named in the docstring and pinned
    by nothing. Hand-edit the line down to less than what the plan put on
    it and the derived "household's own" goes negative; without the floor
    the next removal takes the line to nothing.
    """
    tools.add_recipe(
        "Onion Bake", ingredients=[{"item": "Onions", "qty": "2", "category": "produce"}],
        default_servings=4,
    )
    tools.add_grocery_item("Onions", quantity="3", category="produce")
    plan_id = _onion_week(_monday())
    assert _qty("Onions") == "8"
    item_id = next(i["id"] for i in tools.list_grocery_list() if i["item"] == "Onions")
    tools.update_grocery_item(item_id, quantity="2")

    grocery._reverse_meal_grocery_contributions(
        next(
            r["meal_plan_entry_id"] for r in _link_rows("Onions")
        )
    )

    assert _qty("Onions") == "3", "two dinners still want 3 onions; the line is not blanked"


def _link_rows(item: str):
    conn = get_conn()
    rows = conn.execute(
        "SELECT meal_plan_entry_id FROM meal_plan_grocery_links "
        "WHERE item = ? AND household_id = ? ORDER BY id",
        (item, tools.household_id()),
    ).fetchall()
    conn.close()
    return rows
