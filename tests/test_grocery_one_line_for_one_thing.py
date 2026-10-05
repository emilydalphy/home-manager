"""
One line for one thing: "plain yogurt", "yogurt, plain" and "plain Greek
yogurt" are one grocery line.

Loop Board bug, Gowthami's household 2026-10-04: "It's showing the same
ingredients multiple times (3 variations of a plain yogurt)". Measured on a
throwaway DB before the fix -- three recipes wanting plain yogurt gave
THREE lines, because `_merge_key` only lowercased and singularised the last
word: ", plain" was not normalised and yogurt was in no same-purchase pair.

Three narrow rules, each deliberately narrower than "drop the adjectives":

  * a comma-inverted name is written back out the way a shopper says it
    ("yogurt, plain" -> "plain yogurt"), and a post-comma segment that is
    only PREPARATION is dropped ("Baby spinach, chopped");
  * a short closed list of filler words that cannot name a different
    product comes out of the key ("Store-bought hummus" / "Hummus");
  * the yogurt pairs join `_SAME_PURCHASE`, the existing allow-list.

The whole back half of this file is the other half of the job: every
dangerous pair that must NOT merge. A duplicate line is visible and mildly
annoying; a wrong merge is invisible and means something never gets bought.

TWELVE MUTATIONS RUN AND EVERY ONE BITES, red counts read off the runs
over this file plus test_grocery_same_purchase.py and
test_grocery_plurals.py (control: 0 red):

  13  the whole fix reverted, i.e. main's own behaviour
  10  comma names no longer un-inverted
   6  the yogurt pairs removed
   4  a post-comma prep segment MOVED rather than dropped
   3  filler no longer stripped
   2  the refold a no-op, so the merge stays pairwise
   2  "fresh" added to the filler list
   2  a plan's add no longer takes the more specific name
   1  prep matched on the whole segment only, never its last word
   1  an all-filler name keyed to nothing
   1  the refold folding on the candidate rather than the reconciled flag
   1  bare "yogurt" mapped to plain
"""
import pytest

from app import tools
from app.tools import grocery


# --------------------------------------------------------------------------
# 1. The reported bug
# --------------------------------------------------------------------------

def test_the_testers_three_yogurt_names_are_one_line_with_the_amounts_added():
    """CATCH. Three lines before the fix, one after, amounts summed."""
    tools.add_grocery_item("Plain yogurt", "500 g")
    tools.add_grocery_item("Yogurt, plain", "250 g")
    tools.add_grocery_item("Plain Greek yogurt", "200 g")
    rows = [r for r in tools.list_grocery_list() if "yogurt" in r["item"].lower()]
    assert len(rows) == 1, [r["item"] for r in rows]
    assert rows[0]["quantity"] == "950 g"


def test_a_plans_three_recipes_land_on_one_line_named_for_the_variety():
    """
    CATCH. The tester's real shape: three recipes, not three hand adds.

    The line keeps the GREEK name, and that is the safe direction rather
    than an accident -- buying Greek satisfies a recipe that asked for
    plain, where buying plain fails one that asked for Greek. It is
    `_more_specific_name`'s existing rule doing the work.
    """
    plan = tools.create_weekly_plan("2026-10-05", day_count=7)["weekly_plan_id"]
    tools.add_grocery_item("Plain yogurt", "500 g", source_weekly_plan_id=plan)
    tools.add_grocery_item("Yogurt, plain", "250 g", source_weekly_plan_id=plan)
    tools.add_grocery_item("Plain Greek yogurt", "200 g", source_weekly_plan_id=plan)
    rows = [r for r in tools.list_grocery_list() if "yogurt" in r["item"].lower()]
    assert len(rows) == 1, [r["item"] for r in rows]
    assert rows[0]["item"] == "Plain Greek yogurt"
    assert rows[0]["quantity"] == "950 g"


def test_a_persons_own_wording_is_not_renamed_under_them():
    """
    GUARD. A hand-added line keeps the words they typed even when a Greek
    line joins it -- `add_grocery_item` only takes the more specific name
    for a PLAN's add, and the shop sheet's Put back restores amount and
    store only, so a rename it caused could not be put back.

    Pinned by the mutation that drops the `source_weekly_plan_id is not
    None` condition from that branch (2 red).
    """
    tools.add_grocery_item("Plain yogurt", "500 g")
    tools.add_grocery_item("Plain Greek yogurt", "200 g")
    rows = [r for r in tools.list_grocery_list() if "yogurt" in r["item"].lower()]
    assert len(rows) == 1
    assert rows[0]["item"] == "Plain yogurt"


def test_a_hand_add_joins_an_existing_line_rather_than_duplicating_it():
    """CATCH. The card's "the same merge applies when a person adds by hand"."""
    plan = tools.create_weekly_plan("2026-10-05", day_count=7)["weekly_plan_id"]
    tools.add_grocery_item("Yogurt, plain", "250 g", source_weekly_plan_id=plan)
    tools.add_grocery_item("Plain yogurt", "500 g")
    rows = [r for r in tools.list_grocery_list() if "yogurt" in r["item"].lower()]
    assert len(rows) == 1
    assert rows[0]["quantity"] == "750 g"


# --------------------------------------------------------------------------
# 2. The three rules, stated one at a time
# --------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("Plain yogurt", "Yogurt, plain"),
    ("Green beans", "Beans, green"),
    ("Chicken stock", "Stock, chicken"),
])
def test_a_comma_inverted_name_is_the_same_thing(a, b):
    """CATCH. Word order and commas, the card's first criterion."""
    assert grocery._merge_key(a) == grocery._merge_key(b)


@pytest.mark.parametrize("a,b", [
    ("Baby spinach, chopped", "Baby spinach"),
    ("Parsley, finely chopped", "Parsley, chopped"),
    ("Tomatoes, halved", "Tomatoes"),
    ("Butter, softened", "Butter"),
])
def test_preparation_after_a_comma_is_not_part_of_the_purchase(a, b):
    """
    CATCH for the first and third. "Parsley, finely chopped" is here
    because an unrecognised post-comma segment is KEPT as a modifier rather
    than dropped, so "finely chopped" and "chopped" both reduce to the same
    modifier set -- a word the list has never seen can never silently
    vanish from a name.
    """
    assert grocery._merge_key(a) == grocery._merge_key(b)


@pytest.mark.parametrize("a,b", [
    ("Store-bought hummus", "Hummus"),
    ("Full-fat plain yogurt", "Plain yogurt"),
    ("Shop-bought puff pastry", "Puff pastry"),
])
def test_filler_that_cannot_name_a_different_product_comes_out(a, b):
    """CATCH. The card's filler-descriptor criterion."""
    assert grocery._merge_key(a) == grocery._merge_key(b)


@pytest.mark.parametrize("a,b", [
    ("Plain yogurt", "Plain Greek yogurt"),
    ("Plain yogurt", "Greek yogurt"),
    ("Plain yogurt", "Plain yoghurt"),
    ("Plain Greek yogurt", "Greek yoghurt"),
])
def test_greek_yogurt_is_the_same_purchase_as_plain(a, b):
    """CATCH. Greek yogurt is a plain yogurt, strained."""
    assert grocery._merge_key(a) == grocery._merge_key(b)


def test_a_name_that_is_nothing_but_filler_keeps_itself():
    """
    GUARD. "Store-bought" alone is a bad line, and keying it to "" would
    merge it with every other bad line on the list. Pinned by the mutation
    that drops `or cleaned` from `_drop_name_filler` (1 red).
    """
    assert grocery._merge_key("Store-bought") == "store-bought"
    assert grocery._merge_key("Store-bought") != grocery._merge_key("Full-fat")


# --------------------------------------------------------------------------
# 3. Amounts: a third one still finds its own unit
# --------------------------------------------------------------------------

def test_a_third_amount_folds_into_the_segment_that_shares_its_unit():
    """
    CATCH. `_try_consolidate_quantity` merges a PAIR, so once a line read
    "500 g + 1 cup" neither side parsed and every later add just lengthened
    the string. Measured before the fix: "500 g + 1 cup + 200 g".
    """
    assert grocery._refold_quantity_segments("500 g + 1 cup + 200 g") == "700 g + 1 cup"
    assert grocery._refold_quantity_segments("2 cups + 1 lb + 3 cups + 2 lb") == "5 cups + 3 lbs"


def test_what_genuinely_does_not_reconcile_is_left_exactly_as_it_was():
    """
    GUARD. Mass against volume is not converted and never guessed, and the
    fold is idempotent so the repair is safe to run twice. Pinned by the
    mutation that folds on the candidate rather than the reconciled flag,
    which re-concatenates (1 red).
    """
    assert grocery._refold_quantity_segments("700 g + 1 cup") == "700 g + 1 cup"
    assert grocery._refold_quantity_segments("500 g") == "500 g"
    assert grocery._refold_quantity_segments("") == ""


def test_the_historical_prep_descriptor_mangle_folds_to_one_number():
    """
    CATCH against the old loop. The 2026-08-30 shape
    repair_grocery_quantities exists for; its own left-to-right fold could
    not do this, because the accumulator stopped parsing after the first
    pair that failed to reconcile.
    """
    assert grocery._refold_quantity_segments("3, diced + 1, diced + 1, diced") == "5"


# --------------------------------------------------------------------------
# 4. NO OVER-MERGING. The half with teeth.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    # The card names these three explicitly.
    ("Coconut milk", "Milk"),
    ("Sweet potato", "Potato"),
    ("Green onion", "Onion"),
    # Varieties a shopper buys separately -- the allow-list's whole point.
    ("Red onion", "Onion"),
    ("Brown rice", "Rice"),
    ("Chicken thighs", "Chicken"),
    ("Whole-wheat flour", "Flour"),
    ("Brown sugar", "Sugar"),
    # Flavoured yogurt is a different purchase.
    ("Strawberry yogurt", "Plain yogurt"),
    ("Vanilla Greek yogurt", "Plain Greek yogurt"),
    ("Strawberry yogurt", "Vanilla yogurt"),
    # Fresh is NOT filler in this app: spices.py counts basil as a rack
    # item only when written "dried", so letting a dried line absorb a
    # fresh one would lose a distinction the app reads elsewhere.
    ("Fresh basil", "Basil"),
    ("Fresh basil", "Dried basil"),
    # A LEADING adjective can name the product. A tin of diced tomatoes is
    # not a fresh tomato -- which is why prep words are dropped only after
    # a comma.
    ("Diced tomatoes", "Tomatoes"),
    ("Crushed tomatoes", "Diced tomatoes"),
    ("Shredded cheese", "Cheese"),
    # Fat level changes the product; only "full-fat" is filler.
    ("Low-fat milk", "Full-fat milk"),
    ("Skim milk", "Milk"),
    # The one this repo has been bitten by before.
    ("Olive oil", "Olives"),
])
def test_these_stay_two_lines(a, b):
    """CATCH against any widening of the rules. A wrong merge is invisible."""
    assert grocery._merge_key(a) != grocery._merge_key(b), (
        f"{a!r} and {b!r} both key to {grocery._merge_key(a)!r}"
    )


def test_bare_yogurt_is_deliberately_left_on_its_own_line():
    """
    GUARD, and an ASSUMPTION worth Emily's eyes: "Yogurt" with nothing in
    front of it is NOT mapped to plain, because it could be flavoured, and
    this module's stated bias is to fail toward two lines. One entry in
    `_SAME_PURCHASE` reverses it.

    Pinned by the mutation that adds `"yogurt": "plain yogurt"` (1 red).
    """
    assert grocery._merge_key("Yogurt") != grocery._merge_key("Plain yogurt")


def test_the_filler_list_stays_short():
    """
    GUARD on the rule rather than on a case: every widening of these two
    lists is a product call, and "fresh" and the fat levels are out on
    purpose. Pinned by the mutation that adds "fresh" to `_NAME_FILLER`
    (2 red).
    """
    joined = " ".join(grocery._NAME_FILLER)
    assert "fresh" not in joined
    assert "low-fat" not in joined and "low fat" not in joined
    assert "organic" not in joined
