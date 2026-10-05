"""
A count on a recipe is the DISH's amount, not each person's.

Gowthami's household, 2026-10-04: "The quantities are off for some of the
grocery list items. We need to revisit the logic, because it's assuming a
whole 'apple' or 'tomato' for each one, when it's not a whole one per
person per recipe so it's way too many."

MEASURED FIRST, on a throwaway week for her household's shape (2 adults +
1 child, 5 dinners, 5 lunches, a fruit snack every day), against
origin/main:

    Apples      15      a snack recipe for ONE naming "1 apple", 5 days
    Cucumbers   15      a packed lunch for TWO naming "2 cucumbers", 5 days
    Tomatoes    21      the lunches' 2 + two dinners' 4 each

and the mechanism was NOT the rounding (which the 2026-09-05 "17 peppers"
work deliberately does once per line, and which this change leaves alone).
It was attendance.servings_scale_factor, which is eaters / default_servings
and so MULTIPLIES a stated count whenever the recipe's own table is smaller
than the household: the snack's factor was 3/1 = 3.0, so each day's apple
became three and the week bought fifteen.

Two rules, both about the number that goes IN:

  - attendance.count_scale_factor — the same composition with the RECIPE
    anchor capped at 1.0. A per-portion amount really does scale with the
    eaters (the week's chicken is untouched, and there is a GUARD on that);
    a bare count of a whole thing is the dish's description and is never
    multiplied above what the recipe wrote.
  - recipes.per_person_count_problem / plausible_count_quantity — the
    plausibility guard the card asked for, in the card's own words
    ("> 1 tomato per person per meal, > 1 apple per person per snack ...
    recomputes from the recipe rather than shipping it"). It recomputes to
    the plausible MAXIMUM for the recipe's own table, not to the app's
    typical figure; see the function for why.

After, on the same seeded week: Apples 5, Cucumbers 10, Tomatoes 16.

RED AGAINST MAIN IS 12 OF 32, AND THE NUMBER IS DECOMPOSED HERE RATHER
THAN QUOTED, because it means less than it looks. Measured with the three
new names stubbed to main's behaviour (count_scale_factor =
servings_scale_factor, the two count rules inert) so every test reaches its
own assertion rather than dying on an import: NINE fail on the claim they
are named for, with the number main produced in each docstring; THREE are
red only because the function they call is not there
(..._never_hands_back_none_of_something, ..._note_rides_through...,
..._reported_in_the_same_shape...), which is the only kind of red a test of
a brand-new function can have, and each says so. Eleven of the twenty green
call a new name directly, so for nine of those "green on main" is not
measurable either and they are pinned by a mutation that was actually run;
the two exceptions are the count_scale_factor ones, where the stub really
is main's own arithmetic. The red counts for every mutation are in the
branch's report.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import attendance, recipes
from conftest import household_today, prompt_literals


# ---------- seeding ----------

def _monday() -> str:
    """
    The Monday of NEXT week, off the household's own clock — far enough
    ahead that every day of the seeded week is in the future whatever
    weekday the suite runs on, and never datetime.date.today().
    """
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)).isoformat()


def _days() -> list[str]:
    return tools._week_dates(_monday())


def _household(*names: str) -> None:
    for name in names:
        tools.add_member(name)


@pytest.fixture
def week() -> int:
    return tools.create_weekly_plan(_monday())["weekly_plan_id"]


def _bought() -> dict[str, str]:
    """Every line the shop would show, to-buy and the spice section both."""
    rows = tools.list_grocery_list() + tools.list_grocery_list(status="spice")
    return {r["item"]: r["quantity"] for r in rows}


def _qty(item: str) -> str | None:
    return _bought().get(item)


def _plan(week: int, name: str, lines: list[dict], servings: int, slot: str, days: list[int]) -> None:
    tools.add_recipe(name, ingredients=lines, default_servings=servings)
    for index in days:
        tools.plan_meal(_days()[index], name, slot=slot, weekly_plan_id=week)


def _count(item: str, qty: str) -> dict:
    return {"item": item, "qty": qty, "category": "produce"}


def _link_qtys(item: str) -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity FROM meal_plan_grocery_links WHERE item = ? ORDER BY id", (item,)
    ).fetchall()
    conn.close()
    return [r["quantity"] for r in rows]


# ---------- the card's own two tests ----------

def test_three_recipes_each_using_one_tomato_for_four_people_buys_three(week):
    """
    CATCH — main buys 12.

    The card's first test, verbatim: "a week with three recipes each using
    '1 tomato' for 4 people buys 3 tomatoes, not 12". Twelve is what the
    recipe anchor produces when a recipe's own table understates the
    household — three recipes, each written for one, eaten by four, so
    4/1 = 4 tomatoes a dinner. The count the recipe wrote is the dish's,
    so three dinners want three tomatoes.
    """
    _household("A", "B", "C", "D")
    for i in range(3):
        _plan(week, f"Dinner {i}", [_count("Tomatoes", "1")], 1, "dinner", [i])

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Tomatoes") == "3"


def test_a_fruit_snack_for_one_child_five_days_buys_five_apples(week):
    """
    CATCH — main buys 15.

    The card's second test, verbatim: "a fruit snack for 1 child x 5 days
    buys 5 apples, not 15". This is the line the tester was looking at: a
    snack recipe written for ONE, planned every day, in a household of
    three, so servings_scale_factor was 3/1 and every apple became three.
    """
    _household("A", "B", "Child")
    _plan(week, "Apple slices", [_count("Apples", "1")], 1, "snack", list(range(5)))

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Apples") == "5"


# ---------- the rule: a stated count is never multiplied up ----------

def test_a_count_is_never_multiplied_above_what_the_recipe_wrote(week):
    """
    CATCH — main buys 18.

    A household of SIX eating three dinners each written for four and each
    naming "4 tomatoes". Main scales the count by 6/4 and buys eighteen;
    here it buys the twelve the three recipes actually name.

    THE ONE DEBATABLE JUDGEMENT ON THIS CARD, named rather than buried: a
    bigger-than-the-recipe household now buys what the recipe wrote rather
    than a proportional share, so for a bulk count (tomatoes in a sauce)
    this can under-buy. It is deliberate and it is bounded — never below
    the recipe's own number. Three reasons. The report is OVERBUYING and
    this direction can only reduce. default_servings is the least
    trustworthy field a recipe has (add_recipe defaults it to four, nothing
    backfills it, an import or a chat add can say anything), so multiplying
    by it amplifies its error in the one direction that hurts. And it is a
    no-op in the steady state the app is being driven towards: generation
    is told to write default_servings for the real table, and where it does
    the factor is 1.0 either way.
    """
    _household("A", "B", "C", "D", "E", "F")
    for i in range(3):
        _plan(week, f"Dinner {i}", [_count("Tomatoes", "4")], 4, "dinner", [i])

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Tomatoes") == "12"


def test_a_count_written_for_a_bigger_table_is_still_divided_down(week):
    """
    GUARD — green on main, and the half of the 2026-09-05 "17 peppers" work
    that must NOT be undone: the cap is on multiplying only. Five dinners
    written for four, eaten by three, is still 0.75 of each count.

    Pinned by the mutation that makes count_scale_factor return `base`
    outright (i.e. drops the recipe anchor altogether), which takes this
    to 17.
    """
    _household("A", "B", "C")
    for index, amount in enumerate(("3", "4", "2", "4", "4")):
        _plan(week, f"Pepper dinner {index}", [_count("Bell peppers", amount)], 4, "dinner", [index])

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Bell peppers") == "13"


def test_a_per_portion_amount_still_scales_with_the_eaters(week):
    """
    GUARD — green on main, and the line between the two factors. A weight
    of meat is not a count: a recipe for four eaten by six needs half again
    as much chicken, and servings_scale_factor is right about all of it.

    THE BIGGER TABLE IS THE WHOLE TEST, and the first version of it used a
    SMALLER one (three eaters against a recipe for four) and was therefore
    worthless: the cap is `min(1.0, household / servings)`, so below the
    recipe's own table the two factors are the SAME NUMBER and nothing can
    tell them apart. Measured — pointing the per-portion branch at
    count_scale_for_entry reddened nothing at all until this was seeded the
    other way round. Both directions are asserted now.

    Pinned by the mutation that points the per-portion branch at
    count_scale_for_entry too, which leaves the six-eater line at 1.5 lbs.
    """
    _household("A", "B", "C", "D", "E", "F")
    _plan(
        week, "Roast chicken",
        [{"item": "Chicken breast", "qty": "1.5 lb", "category": "meat"}],
        4, "dinner", [0],
    )
    _plan(
        week, "Pulled pork",
        [{"item": "Pork shoulder", "qty": "1.5 lb", "category": "meat"}],
        8, "dinner", [1],
    )

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Chicken breast") == "2.25 lbs", "six eaters, a recipe for four"
    assert _qty("Pork shoulder") == "1 lb", "1.125 lbs — six eaters, a recipe for eight"


def test_a_guest_night_still_buys_more(week):
    """
    GUARD — green on main. Only the RECIPE anchor is capped; attendance is
    the household's own word and still applies in both directions, so a
    dinner for six at a table of three still buys for six.

    Pinned by the mutation that caps the whole factor
    (`min(1.0, base * ...)`), which takes this to 2.
    """
    _household("A", "B", "C")
    _plan(week, "Taco night", [_count("Avocados", "2")], 3, "dinner", [0])
    tools.set_guest_count(_days()[0], "dinner", 3)

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Avocados") == "4", "six at the table against a recipe for three"


def test_a_recipe_written_for_this_table_is_left_byte_identical(week):
    """
    GUARD — green on main, and the shape the whole app is being driven
    towards: three eaters, three servings, both factors 1.0, the counts as
    written.

    Pinned by the mutation that makes count_scale_factor return
    `base * 0.5`, which halves every line here.
    """
    _household("A", "B", "C")
    _plan(
        week, "Greek salad",
        [_count("Tomatoes", "3"), _count("Cucumbers", "2"), _count("Lemons", "1")],
        3, "dinner", [0],
    )

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Tomatoes") == "3"
    assert _qty("Cucumbers") == "2"
    assert _qty("Lemons") == "1"


def test_a_household_with_nobody_on_record_shops_as_it_always_has(week):
    """
    GUARD — green on main. No members yet, a household mid-onboarding:
    there is no table to anchor to and guessing one is how someone who has
    told the app nothing ends up with a quarter of a dinner. Both factors
    fall back to attendance alone, which is 1.0.

    Pinned by the mutation that drops count_scale_factor's household_size
    guard, which makes it divide by zero or clamp to nothing.
    """
    _plan(week, "Greek salad", [_count("Tomatoes", "4")], 4, "dinner", [0])

    tools.approve_weekly_plan(week, approved_by="A")

    assert attendance.count_scale_factor(_days()[0], "dinner", 4) == 1.0
    assert _qty("Tomatoes") == "4", "no table to anchor to, so the count as written"


def test_nobody_home_keeps_grocery_scale_factors_own_convention():
    """
    GUARD — green on main. An away slot contributes nothing to the list at
    all, so its factor is never used, and 0.0 would be a trap for any
    future caller that did use it. count_scale_factor inherits that
    convention rather than inventing a second one.

    Pinned by the mutation that drops the nobody_home guard, which returns
    0.0 here.
    """
    _household("A", "B", "C")
    day = _days()[0]
    tools.set_slot_attendance(day, "dinner", present_member_ids=[])

    assert attendance.count_scale_factor(day, "dinner", 4) == 1.0


# ---------- the plausibility guard ----------

def test_a_wild_count_is_recomputed_rather_than_shipped(week):
    """
    CATCH — main buys 36.

    Three tomatoes a head is past anything a dish uses, whatever the kind,
    and the card says so in those words. Each dinner's twelve is recomputed
    to the most a plausible dish for four uses — four — so the week buys
    twelve rather than thirty-six.
    """
    _household("A", "B", "C", "D")
    for i in range(3):
        _plan(week, f"Dinner {i}", [_count("Tomatoes", "12")], 4, "dinner", [i])

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Tomatoes") == "12"


def test_a_wild_snack_count_is_recomputed_and_then_not_multiplied(week):
    """
    CATCH — main buys 45.

    Both rules on one line, which is the shape that produced the worst
    number: a snack written for ONE naming three apples, planned five days,
    in a household of three. The guard takes three apples for one person
    down to one, and the cap stops the 3/1 recipe anchor multiplying it
    back up.
    """
    _household("A", "B", "Child")
    _plan(week, "Apple pile", [_count("Apples", "3")], 1, "snack", list(range(5)))

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Apples") == "5"


def test_a_count_at_exactly_one_per_person_is_left_alone(week):
    """
    GUARD — green on main, and the card's own threshold read literally: it
    says flag "> 1 tomato per person per meal", so four tomatoes for four
    is a dish amount and is not touched. A pasta sauce for four really does
    use four tomatoes, and three such dinners really do want twelve.

    Pinned by the mutation that drops the tomato ceiling to 0.5, which
    takes this to 6. NOT by the one that makes per_person_count_problem
    compare with `>=`: measured, that reddens only
    test_the_recompute_floors_so_it_can_never_be_flagged_again, because at
    exactly the ceiling the recompute hands back the number it was given —
    the guard fires and changes nothing. An earlier draft of this docstring
    claimed that mutation and was wrong.
    """
    _household("A", "B", "C", "D")
    for i in range(3):
        _plan(week, f"Dinner {i}", [_count("Tomatoes", "4")], 4, "dinner", [i])

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Tomatoes") == "12"


def test_every_ceiling_allows_at_least_one_whole_thing_per_person():
    """
    GUARD, pinned by mutation: the table is new, so this is a claim about
    the table rather than a behaviour catch, and redness against main means
    nothing. The line the card draws and the line this module's standing
    bias wants: this rule clamps a number DOWN, and the bias is the other
    way (quantities._PACKAGE_UNITS — an extra line beats a missing dinner).
    So nothing a plausible recipe writes is overruled.

    Pinned by the mutation that sets any ceiling below 1.
    """
    for word, ceiling in recipes._PER_PERSON_COUNT_CEILING:
        assert ceiling >= 1, f"{word} would overrule one whole thing each"


def test_the_recompute_floors_so_it_can_never_be_flagged_again():
    """
    GUARD, pinned by mutation and NOT by redness: this calls a function
    main has not got, so "green on main" is not measurable for it (against a
    stub that makes the rule inert it is green for the stub's sake, which is
    not evidence). The claim is the one place this does not err generously
    and has to not: the ceiling IS the maximum a plausible dish uses, so
    rounding past it would hand back a number this very function flags.

    Pinned by the mutation that rounds the recompute up (math.ceil), which
    makes the recomputed onion line flaggable again.
    """
    for item, servings in (("Onions", 3), ("Shallots", 3), ("Peppers", 5), ("Carrots", 3)):
        fixed = recipes.plausible_count_quantity(item, "40", servings)
        assert recipes.per_person_count_problem(item, fixed, servings) is None, (item, fixed)


def test_the_recompute_never_hands_back_none_of_something():
    """
    GUARD, pinned by mutation. It is red against main, but only because
    the function is not there — not for a behaviour difference — so read the
    mutation and not the redness. A recipe that names a count wants some of
    it; floored at one whole thing, however small the table.

    Pinned by the mutation that drops the `max(1.0, ...)` floor, which
    gives "0" for a table of one against a half-per-person ceiling.
    """
    assert recipes.plausible_count_quantity("Tomatoes", "9", 1) == "1"
    assert recipes.plausible_count_quantity("Apples", "9", 1) == "1"


# ---------- what is NOT a bare count ----------

def test_the_two_count_tables_are_independent_judgements(week):
    """
    GUARD — green on main, pinned by the mutation that folds the two count
    tables into one shared number.

    _PRODUCE_COUNT_PER_SERVING (the 2026-09-13 "which KIND did you mean?"
    flag) and _PER_PERSON_COUNT_CEILING (this card's "more than a dish
    uses?" recompute) share all six of the older table's nouns and answer
    two different questions about them, so neither is the other's ceiling
    and they are free to disagree. They DO disagree about two of the six.

    This asserts only that the disagreement is real, never which four
    agree: the comment at the older table says in as many words that the
    four agreeing today is a coincidence of two separate judgements rather
    than a shared number, and a test freezing those four would be exactly
    the thing that comment tells the next reader not to build. So what is
    pinned is independence — fold them together, or make one read the
    other, and this goes red.
    """
    kind = {row[0]: row[-1] for row in recipes._PRODUCE_COUNT_PER_SERVING}
    ceiling = dict(recipes._PER_PERSON_COUNT_CEILING)

    # Every noun the older table judges is also judged by the new one, or
    # a count the kind rule passes would reach the list unexamined.
    assert set(kind) <= set(ceiling)

    differ = {n for n in kind if kind[n] != ceiling[n]}
    assert differ, (
        "the two count tables now agree about every noun they share. If "
        "that is deliberate, say so at _PRODUCE_COUNT_PER_SERVING; if one "
        "has been made to read the other, that is the fold its comment "
        "warns against."
    )
    # The two the comment names, so the comment cannot drift from the code.
    assert ("tomato", kind["tomato"], ceiling["tomato"]) == ("tomato", 2, 1)
    assert ("apple", kind["apple"], ceiling["apple"]) == ("apple", 2, 1)


def test_a_named_small_kind_is_never_second_guessed(week):
    """
    GUARD — green on main, and the 2026-09-13 decision kept intact: six
    Persian cucumbers are six Persian cucumbers, and twenty cherry tomatoes
    are twenty cherry tomatoes. The count alone says which kind was meant,
    so a name that says a kind is left exactly as written.

    TWO GUARDS, AND THE TEST NEEDS BOTH, which was measured rather than
    reasoned. For the six nouns the kind table owns, "is a kind named?" is
    _produce_class's judgement; for every other name it is
    _SMALL_KIND_WORDS. "Persian" and "cherry" are in BOTH, so dropping the
    _produce_class branch reddened nothing at all until a name only
    _produce_class knows was added — "Roma tomatoes" is a kind to that
    table (so it stands down) and just a word to the word list.

    Pinned by two mutations: dropping the _produce_class branch, which
    clamps the Roma line to 2; and dropping the _SMALL_KIND_WORDS check,
    which clamps the Persian cucumbers.
    """
    _household("A", "B")
    _plan(
        week, "Shirazi salad",
        [_count("Persian cucumbers", "6"), _count("Cherry tomatoes", "20")],
        2, "dinner", [0],
    )
    _plan(week, "Roma sauce", [_count("Roma tomatoes", "20")], 2, "dinner", [1])

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Persian cucumbers") == "6"
    assert _qty("Cherry tomatoes") == "20"
    assert _qty("Roma tomatoes") == "20", "a kind only _produce_class knows"


def test_a_small_kind_said_in_the_amount_is_not_second_guessed():
    """
    GUARD, pinned by mutation (the function is new, so redness against
    main says nothing). "8 small" is the kind said in the amount rather than
    in the name, which _SMALL_KIND_NOTES already reads for the kind
    question; this one reads it the same way rather than keeping a second
    opinion.

    Pinned by TWO mutations, because two different lists answer "was a kind
    named": dropping the note check (which flags the "8 small" line), and
    gutting _SMALL_KIND_WORDS (which flags the three NAMED small kinds
    below). That second list is reachable ONLY for a noun the kind table
    does not own — for its six, _per_person_count_ceiling returns before
    the word list is consulted — so gutting it reddened nothing at all
    until these three lines existed. Measured, not reasoned.
    """
    assert recipes.per_person_count_problem("Tomatoes", "8 small", 2) is None
    assert recipes.plausible_count_quantity("Tomatoes", "8 small", 2) == "8 small"
    assert recipes.per_person_count_problem("Baby carrots", "40", 2) is None
    assert recipes.per_person_count_problem("Baby zucchini", "20", 2) is None
    assert recipes.per_person_count_problem("Mandarin oranges", "30", 2) is None


def test_a_spice_rack_name_is_not_a_count_of_the_thing():
    """
    GUARD, pinned by mutation (the function is new). "2" of "Red pepper
    flakes" is not two peppers, and the spice rack already owns that
    judgement (spices.is_spice).

    THE FIRST TWO LINES ARE SAVED BY A DIFFERENT GUARD, measured:
    "pepper" is one of the six nouns the kind table owns, so _produce_class
    declines them ("flakes" and "seasoning" name no kind of pepper it
    knows) and dropping is_spice changes nothing for either. "Chili powder"
    is the one that needs the spice rack — "chili" is in the ceiling table
    and in neither the kind nouns nor _SMALL_KIND_WORDS — so it is the line
    the mutation reddens.

    Pinned by the mutation that drops the is_spice guard from
    _per_person_count_ceiling, which reads "Chili powder" as two chillies.
    """
    assert recipes.per_person_count_problem("Red pepper flakes", "2", 1) is None
    assert recipes.per_person_count_problem("Lemon pepper seasoning", "3", 1) is None
    assert recipes.per_person_count_problem("Chili powder", "2", 1) is None
    assert recipes.per_person_count_problem("Chilli flakes", "3", 1) is None


def test_a_weight_is_not_a_count(week):
    """
    GUARD — green on main. "1.5 lb" of potatoes is a per-portion amount and
    takes the per-portion factor and the per-portion plausibility rule; the
    count rule has nothing to say about it. Turning a count line into a
    weight line (or the reverse) would also break the merge the grocery
    list does by name and unit.

    THE BIG WEIGHT IS THE PART THAT PINS IT, measured. "1.5 lb" of
    potatoes for four is 0.375 per serving, well inside the potato ceiling
    of 2, so dropping the unit check reddened nothing — the arithmetic was
    saving the line, not the rule. A weight whose NUMBER is past the
    ceiling is what shows the difference: twelve pounds of potatoes for
    four is three "per serving" to anything that forgets lb is not a count,
    and would be rewritten to eight — a weight silently cut by a third.

    Pinned by the mutation that drops the unit check from
    per_person_count_problem, which flags the twelve-pound line.
    """
    assert recipes.per_person_count_problem("Potatoes", "12 lb", 4) is None
    assert recipes.per_person_count_problem("Tomatoes", "5 lb", 2) is None
    _household("A", "B", "C")
    _plan(
        week, "Mash",
        [{"item": "Potatoes", "qty": "1.5 lb", "category": "produce"}],
        4, "dinner", [0],
    )

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Potatoes") == "1 lb", "1.125 lbs, the per-portion factor"


def test_a_counted_pack_keeps_its_own_path(week):
    """
    GUARD — green on main. A dozen of anything is a PACK, and
    _counted_pack_share owns that question: eggs are written as the NUMBER
    the recipe uses, the week's eggs add up, and the line is written in
    whole cartons once. The pieces really do scale with the eaters, so a
    pack takes the per-portion factor and not the capped one.

    SEEDED SIX EATERS AGAINST A RECIPE FOR FOUR ON PURPOSE, and the first
    version was seeded three against four and could not fail: below the
    recipe's own table the two factors are the same number, and "egg" is in
    neither count table, so nothing about the line differed either way.
    Measured — the mutation below reddened nothing until this was re-seeded.
    Six eaters, 6 eggs a morning for three mornings is 27 pieces, which is
    three cartons; the capped factor would buy two.

    Pinned by the mutation that drops `not pack_share` from the bare_count
    test, which takes the eggs line onto the capped factor and buys 2 dozen.
    """
    _household("A", "B", "C", "D", "E", "F")
    _plan(
        week, "Omelette",
        [{"item": "Eggs", "qty": "6", "category": "dairy"}],
        4, "breakfast", [0, 1, 2],
    )

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Eggs") == "3 dozen", "27 eggs, written in whole cartons once"


def test_a_package_keeps_its_own_path(week):
    """
    GUARD — green on main. One bottle is one bottle however many nights
    name it — the 2026-09-04 package work — and a package is not a count of
    a whole thing.

    NOTHING PINS THE `not package` CLAUSE AND THAT IS SAID RATHER THAN
    CLAIMED, because this file has already had to unpick two mutations that
    did not bite. A package is excluded from the count path TWICE: by that
    clause and, independently, by the unit check beside it — the unit of "1
    bottle" parses as "bottle", which is not a bare-count unit. Measured:
    dropping `not package` reddens nothing at all, in this file or in the
    five quantity files beside it. It is belt and braces, kept because it
    says what is meant where the classification happens, and this test
    guards the BEHAVIOUR (which the unit check holds up) rather than that
    clause.
    """
    _household("A", "B", "C")
    for i in range(3):
        _plan(
            week, f"Dinner {i}",
            [{"item": "Olive oil", "qty": "1 bottle", "category": "pantry"}],
            4, "dinner", [i],
        )

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Olive oil") == "1 bottle"


def test_a_freeform_amount_is_not_treated_as_a_count():
    """
    GUARD, pinned by mutation (the names are new). "A bunch", "to taste"
    and a blank name no number, so there is nothing to hold to a ceiling and
    nothing to scale. The unit of a bare count is None; an unparseable
    amount has no unit at all, and the two must not be read as the same
    thing — which is a one-character difference in a frozenset and the
    reason it is asserted directly.

    Pinned by the mutation that puts "" into _PER_PERSON_COUNT_UNITS beside
    None, which makes every freeform line a count.
    """
    assert "" not in recipes._PER_PERSON_COUNT_UNITS
    for qty in ("a bunch", "to taste", "", "a handful"):
        assert recipes.per_person_count_problem("Tomatoes", qty, 2) is None
        assert recipes.plausible_count_quantity("Tomatoes", qty, 2) == qty


def test_a_note_rides_through_the_recompute():
    """
    GUARD, pinned by mutation. Red against main for the missing function
    rather than for a behaviour difference. The amount and the note that
    rides with it come apart and go back together the same way everywhere
    else on the list ("1 bag (2 lb), frozen"), so a recomputed line keeps
    its note.

    Pinned by the mutation that returns the formatted amount without
    _with_note.
    """
    assert recipes.plausible_count_quantity("Tomatoes", "12 (frozen)", 4) == "4, frozen"


# ---------- the batch factor ----------

def test_a_batch_really_is_more_food_so_a_count_scales_with_it(week):
    """
    GUARD — green on main, and the one factor that is deliberately NOT
    capped for a count: a batch is more of the dish, so it uses more whole
    lemons.

    MEASURED, because the composition is easy to get wrong by reading. Two
    eaters, a lunch recipe written for four naming "2 lemons", cooked once
    on Monday to cover Monday and Tuesday. The recipe anchor divides to the
    cook night's own table (min(1, 2/4) = 0.5) and the batch multiplies
    back up (batch["servings"] 4 / cook_eaters 2 = 2.0), so the week buys
    the two lemons the recipe actually names. The two factors are inverses
    here BY CONSTRUCTION — the batch exists to undo the per-night division
    when one cook feeds several nights — which is exactly why dropping
    either one is a silent halving rather than a visible error.

    Pinned by the mutation that drops `count_scale *= batch_factor` from
    the ingest, which buys ONE lemon for a dish that names two.
    """
    _household("A", "B")
    _plan(week, "Lemon orzo", [_count("Lemons", "2")], 4, "lunch", [0, 1])
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE slot = 'lunch' ORDER BY date"
    ).fetchall()
    conn.close()
    written = tools.set_cook_ahead(rows[0]["id"], [rows[1]["id"]])
    assert written["covered_entry_ids"] == [rows[1]["id"]], written

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Lemons") == "2", "the batch's own four portions, as written"


# ---------- the properties the 2026-09-05 work established ----------

def test_rounding_still_happens_once_per_line_not_once_per_recipe(week):
    """
    GUARD — green on main, and the property the "17 peppers" entry is
    emphatic about: five dinners wanting 0.75 of a count each is 3.75
    peppers, which is 4 in a basket, not five ones.

    Pinned by the mutation that rounds each recipe's share in
    _add_recipe_ingredients_for_entries, which gives 5.
    """
    _household("A", "B", "C")
    for index in range(5):
        _plan(week, f"Dinner {index}", [_count("Bell peppers", "1")], 4, "dinner", [index])

    tools.approve_weekly_plan(week, approved_by="A")

    assert _qty("Bell peppers") == "4"


def test_the_per_meal_ledger_still_holds_unrounded_shares(week):
    """
    GUARD — green on main. The 2026-09-30 rule: the ledger holds each
    meal's UNROUNDED share, because that is what lets a line be re-derived
    from whoever is left when a night is dropped. A count takes a different
    FACTOR and must not start taking a rounded share.

    Pinned by the mutation that writes the rounded week total into the
    ledger, which gives ['4', '4', '4', '4', '4'].
    """
    _household("A", "B", "C")
    for index in range(5):
        _plan(week, f"Dinner {index}", [_count("Bell peppers", "1")], 4, "dinner", [index])

    tools.approve_weekly_plan(week, approved_by="A")

    assert _link_qtys("Bell peppers") == ["0.75"] * 5


# ---------- one rule, two readers ----------

def test_the_cook_view_and_the_list_hold_a_wild_count_to_the_same_number(week):
    """
    CATCH — on main the cook view shows the twelve as written.

    One entry point rather than two (plausible_cooking_quantity reads the
    count question as well as the measured one), so the cook standing at
    the counter and the shopper holding the list cannot end up with
    different opinions about the same line.
    """
    _household("A", "B", "C", "D")
    _plan(week, "Dinner", [_count("Tomatoes", "12")], 4, "dinner", [0])

    tools.approve_weekly_plan(week, approved_by="A")
    cooked = recipes.cooking_ingredients([_count("Tomatoes", "12")], servings=4)

    assert _qty("Tomatoes") == "4"
    assert cooked[0]["qty"] == "4"


def test_the_pre_save_pass_writes_a_cook_qty_for_a_wild_count():
    """
    CATCH — on main settle_cooking_quantities returns the line untouched.

    The same rule at save time, so a recipe written from here on carries
    the honest cooking amount; the shopping qty is deliberately NOT
    rewritten, because the ingest holds the line to the same rule on the
    way to the list and that is what covers every recipe already on disk.
    """
    lines = [_count("Apples", "9")]
    settled = recipes.settle_cooking_quantities([dict(lines[0])], 3)

    assert settled[0]["cook_qty"] == "3"
    assert settled[0]["qty"] == "9", "the shopping qty is never rewritten here"


def test_a_wild_count_is_reported_in_the_same_shape_as_a_wild_measure():
    """
    HALF CATCH, half GUARD, and said that way because the halves differ.
    _implausible_lines picking the line up at all is a real behaviour catch
    (main's returns nothing for it). The SHAPE assertion is the guard, and
    it is red against main only for the missing function. Both questions
    answer in implausible_quantity's shape, so _implausible_lines has one
    reader rather than two and the morning report needs no second message
    function.

    Pinned by the mutation that drops `family` from the count problem,
    which breaks the shared message path.
    """
    problem = recipes.per_person_count_problem("Tomatoes", "12", 4)
    assert problem is not None
    assert set(problem) == {"class", "family", "per_serving", "low", "high"}
    assert problem["family"] == "count"
    lines = recipes._implausible_lines([_count("Tomatoes", "12")], 4)
    assert [line["item"] for line in lines] == ["Tomatoes"]
    assert recipes.implausible_quantity_message(lines[0], 4)


def test_the_two_new_rules_are_on_the_tools_package():
    """
    GUARD, pinned by mutation; green on main is not available, because the
    names are new. app/tools is a package whose __init__ is its public face:
    a function not re-exported there is one agent.py and main.py cannot
    see.

    Pinned by the mutation that removes either name from __init__.
    """
    assert tools.per_person_count_problem is recipes.per_person_count_problem
    assert tools.plausible_count_quantity is recipes.plausible_count_quantity
    assert tools.count_scale_factor is attendance.count_scale_factor


# ---------- the prompt: the half the arithmetic cannot supply ----------

def test_the_generation_prompt_says_a_count_is_the_dishs_not_each_persons():
    """
    CATCH — the rule is not in main's prompt at all.

    The arithmetic can stop a stated count being multiplied and can catch a
    wild one; it cannot know that "4 tomatoes" on a recipe for four was
    meant per person. Only the writer can, so the writer is told — and told
    which amounts DO scale, so the rule cannot be read as "never write for
    the table".
    """
    from app import agent

    # RECIPE_DETAILS_INSTRUCTIONS and not generate_weekly_plan_llm's own
    # prompt: since the 2026-09-21 two-pass split the menu call chooses
    # dishes and writes no quantities at all, and the recipe writer is
    # where an ingredient amount is decided. It sits directly under the
    # `serves` bullet it qualifies, so the two cannot be read apart.
    prompt = agent.RECIPE_DETAILS_INSTRUCTIONS
    assert "means the amount THE DISH uses, not an amount per person" in prompt
    assert "A salad for four wants ONE lemon, not four" in prompt
    assert "DO" in prompt and "scale with the number of people" in prompt, (
        "the rule must not read as 'never write for the table'"
    )
    # The measurement it quotes is the one on the card, not an invented one.
    assert "FIFTEEN apples for five days of apple slices" in prompt


# ---------- the week the tester was looking at ----------

def test_the_whole_week_the_tester_reported(week):
    """
    CATCH — main buys Apples 15, Cucumbers 15, Tomatoes 21.

    Her household's shape, end to end: 2 adults + 1 child, five dinners
    naming counts, five packed lunches from one recipe written for two, and
    a fruit snack every day written for one. Nothing in it is implausible
    for its own stated table, so the whole difference here is the cap — the
    plausibility guard flags none of these lines, which is why both rules
    were needed and not just one.
    """
    _household("Gowthami", "Partner", "Child")
    for index, lines in enumerate((
        [_count("Tomatoes", "4"), {"item": "Chicken breast", "qty": "1.5 lb", "category": "meat"}],
        [_count("Tomatoes", "4"), _count("Onions", "4")],
        [_count("Bell peppers", "4")],
        [_count("Lemons", "4")],
        [_count("Avocados", "4")],
    )):
        _plan(week, f"Dinner {index}", lines, 4, "dinner", [index])
    _plan(
        week, "Chopped salad",
        [_count("Cucumbers", "2"), _count("Tomatoes", "2")],
        2, "lunch", list(range(5)),
    )
    _plan(week, "Apple slices", [_count("Apples", "1")], 1, "snack", list(range(5)))

    tools.approve_weekly_plan(week, approved_by="Gowthami")
    bought = _bought()

    assert bought["Apples"] == "5", "was 15"
    assert bought["Cucumbers"] == "10", "was 15"
    assert bought["Tomatoes"] == "16", "was 21"
    # The per-portion line in the same week is untouched by any of it.
    assert bought["Chicken breast"] == "1 lb"
