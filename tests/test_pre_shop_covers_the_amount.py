"""
The pre-shop check only pins a line behind "Maybe already home" when the
kitchen can be SHOWN to cover the amount that line asks for (Loop Board
bug, reproduced over HTTP 2026-09-14).

pre_shop.get_pre_shop_flags asked inventory one question — "is this name
in there with a non-blank quantity?" — and then rendered BOTH amounts in
the sentence it showed, so the card could read "You want 3 lbs. Fridge
shows 2 lbs." while holding that line off the shop-from list. The flag is
not a remark: /api/grocery-list's "needed" view filters flagged ids out,
so a week shopped normally (every ticked line writes an inventory row)
sent the whole of the next week's list behind the card and opened the Shop
tab empty.

It is the same defect the parent branch just fixed one step earlier in
recipes._add_recipe_ingredients_for_entries, so the answer is the same
class and not a second copy of the arithmetic: recipes._KitchenStock.
Everything it cannot compare stays on the list — a freeform wanted amount,
an unreadable row, two unit families that do not convert. An extra line
beats a missing dinner.

Tests below say in their own docstrings whether they are a CATCH (red on
0633cdd, the parent commit) or a no-regression GUARD. Nine of the
twenty-one are catches; two of the guards were mutation-checked instead
(dropping _KitchenStock's claim ledger, and asking it before the card's
wording guards rather than after), and say so.
"""
from __future__ import annotations

from app import tools
from app.db import get_conn


def _need(item: str, qty: str, category: str = "produce") -> int:
    """One 'needed' grocery line, exactly as the list holds them."""
    return tools.add_grocery_item(item, quantity=qty, category=category)["item_id"]


def _have(item: str, qty: str, category: str = "produce") -> None:
    tools.update_inventory(item, "add", quantity=qty, category=category)


def _flagged() -> set[str]:
    return {f["name"] for f in tools.get_pre_shop_flags()}


def _sentence(name: str) -> str | None:
    for f in tools.get_pre_shop_flags():
        if f["name"] == name:
            return f["sentence"]
    return None


# ------------------------------------------------------------ the reported bug

def test_two_pounds_on_hand_does_not_pin_a_three_pound_line():
    """THE REPORTED BUG, in one line. CATCH — red on 0633cdd, where the
    card flagged this and printed the two amounts that disprove it."""
    _need("Chicken thighs", "3 lbs", "meat/seafood")
    _have("Chicken thighs", "2 lbs", "meat/seafood")

    assert "Chicken thighs" not in _flagged()


def test_four_pounds_on_hand_does_pin_a_three_pound_line():
    """The other half of the rule: enough really is enough, and the card
    keeps working. GUARD — green on 0633cdd too."""
    _need("Chicken thighs", "3 lbs", "meat/seafood")
    _have("Chicken thighs", "4 lbs", "meat/seafood")

    assert "Chicken thighs" in _flagged()


def test_exactly_enough_is_enough():
    """The boundary: enough to the gram is enough. GUARD — green on
    0633cdd, which flagged everything it recognised. It pins the boundary
    rather than catching anything; _KitchenStock compares with an epsilon,
    so a `<=` there is not distinguishable from a `<` at exact
    equality."""
    _need("Rice", "2 cups", "pantry")
    _have("Rice", "2 cups", "pantry")

    assert "Rice" in _flagged()


def test_a_hair_short_is_still_short():
    """CATCH — red on 0633cdd."""
    _need("Ground beef", "2 lbs", "meat/seafood")
    _have("Ground beef", "1.75 lbs", "meat/seafood")

    assert "Ground beef" not in _flagged()


def test_the_sentence_still_names_both_amounts_when_it_does_flag():
    """The card's whole content is that one sentence, and the fix must not
    have quietly changed what it says. GUARD."""
    _need("Rice", "2 cups", "pantry")
    _have("Rice", "3 cups", "pantry")

    assert _sentence("Rice") == "You want 2 cups. Fridge shows 3 cups."


# --------------------------------------------- what cannot be compared is bought

def test_units_that_do_not_convert_are_not_flagged():
    """A bag against a pound is not a comparison. CATCH — red on 0633cdd,
    where the name alone settled it."""
    _need("Spinach", "2 lbs", "produce")
    _have("Spinach", "1 bag", "produce")

    assert "Spinach" not in _flagged()


def test_a_freeform_wanted_amount_is_not_flagged():
    """"A bunch" names no amount, so nothing can be shown to cover it.
    CATCH — red on 0633cdd, which flagged it and read "You want a bunch.
    Fridge shows 1 bunch." Deliberate cost of the fix, and the safe
    direction: an extra line beats a missing dinner."""
    _need("Parsley", "a handful", "produce")
    _have("Parsley", "2 bunches", "produce")

    assert "Parsley" not in _flagged()


def test_an_unreadable_row_on_the_shelf_is_not_flagged():
    """One row nobody can parse means the total on hand is unknown, which
    is not the same as enough. CATCH — red on 0633cdd."""
    _need("Olive oil", "2 cups", "pantry")
    _have("Olive oil", "a bit left", "pantry")

    assert "Olive oil" not in _flagged()


def test_one_unreadable_row_spoils_the_sum_for_that_name():
    """Two rows of one food, one of them freeform: the total is unknown,
    so it is bought. CATCH — red on 0633cdd. (_KitchenStock's own rule;
    pinned here because the pre-shop card is now a second reader of it.)"""
    _need("Butter", "2 lbs", "dairy")
    _have("Butter", "3 lbs", "dairy")
    conn = get_conn()
    conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, category, location) "
        "VALUES (?, 'Butter', 'a bit left', 'dairy', 'fridge')",
        (tools.household_id(),),
    )
    conn.commit()
    conn.close()

    assert "Butter" not in _flagged()


def test_a_row_with_no_quantity_at_all_is_still_not_flagged():
    """The old guard, kept. GUARD — green on 0633cdd."""
    _need("Cumin", "2 tbsp", "pantry")
    _have("Cumin", "", "pantry")

    assert "Cumin" not in _flagged()


def test_two_rows_of_one_food_are_summed_not_picked_between():
    """The opened jar in the fridge and the unopened one in the pantry are
    two rows of one food, and "do we have enough" is a question about the
    food. GUARD (0633cdd flagged it on the name alone) — but it is the
    test that fails if the coverage check ever starts reading one row."""
    _need("Butter", "2 lbs", "dairy")
    _have("Butter", "1 lb", "dairy")
    conn = get_conn()
    conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, category, location) "
        "VALUES (?, 'Butter', '1.5 lbs', 'dairy', 'pantry')",
        (tools.household_id(),),
    )
    conn.commit()
    conn.close()

    assert "Butter" in _flagged()


# ------------------------------------------------- one shelf, two lines of a food
#
# Two lines of one food really can sit on the list at once, and the plainest
# way in is grocery._NUMBER_CHANGES_MEANING: "Pea" and "Peas" are kept apart
# by _merge_key on purpose (number changes the meaning of that word), while
# cooker._singularize reads both as the same thing, so both lines match one
# inventory row confidently. Two comparable lines of one food is exactly the
# shape the claim ledger is for.


def test_one_shelfs_worth_answers_one_line_not_two():
    """THE CLAIMING DECISION. Two cups on the shelf is an answer to one of
    two two-cup lines, not to both — and the flag takes a line OFF what the
    household shops from, so flagging both sends them home with two cups
    where four were wanted. CATCH — red on 0633cdd, which flagged both,
    and red again against a _KitchenStock with its claim ledger removed."""
    _need("Pea", "2 cups")
    _need("Peas", "2 cups")
    _have("Peas", "2 cups")

    lines = [it["item"] for it in tools.list_grocery_list(status="needed")]
    assert sorted(lines) == ["Pea", "Peas"], "the two lines have to co-exist for this to mean anything"
    assert len(tools.get_pre_shop_flags()) == 1


def test_enough_for_both_lines_flags_both():
    """The other side of claiming: a shelf that really does cover both
    answers both, so this is not a one-flag-per-food rule. GUARD — green on
    0633cdd."""
    _need("Pea", "2 cups")
    _need("Peas", "2 cups")
    _have("Peas", "8 cups")

    assert _flagged() == {"Pea", "Peas"}


def test_a_line_the_card_cannot_phrase_spends_nothing():
    """covers() claims what it grants, so it is asked LAST. The 2.75 tbsp
    line's sentence runs past the card's 60-character limit and is never
    shown, so it must not spend the shelf the 2 tbsp line behind it is
    measured against. GUARD — green on 0633cdd, which compared no amount
    at all and flagged the short line for the wrong reason. Its teeth are
    a mutation instead, checked: move the coverage check above the wording
    guards and this is the only test in the file that goes red, because
    three and a half tablespoons less a claimed 2.75 is not two."""
    _need("Pea", "2.75 tablespoons")
    _need("Peas", "2 tablespoons")
    _have("Peas", "3.5 tablespoons")

    assert _flagged() == {"Peas"}
    assert _sentence("Peas") == "You want 2 tbsp. Fridge shows 3 tbsp and a half."


# ------------------------------------------------------------- the whole sequence

def test_shopping_one_week_does_not_empty_the_next_weeks_list(signed_in):
    """END TO END, over the real routes: shop a week (every ticked line
    writes an inventory row), eat some of it, approve the same three
    dinners again. The list has to have something on it. CATCH — red on
    0633cdd, where /api/grocery-list?status=needed came back with zero
    sections and all six lines sat behind the card under sentences saying
    there was not enough."""
    import datetime

    recipes = [
        ("Sheet Pan Chicken", [
            {"item": "Chicken thighs", "qty": "3 lbs", "category": "meat/seafood"},
            {"item": "Broccoli", "qty": "2 lbs", "category": "produce"},
        ]),
        ("Beef Chili", [
            {"item": "Ground beef", "qty": "2 lbs", "category": "meat/seafood"},
            {"item": "Onions", "qty": "3", "category": "produce"},
        ]),
    ]
    for name, ings in recipes:
        tools.add_recipe(name=name, ingredients=ings, instructions=["Cook it."], default_servings=4)

    def _week(offset: int):
        monday = datetime.date.today() + datetime.timedelta(days=offset)
        return monday.isoformat(), [(monday + datetime.timedelta(days=i)).isoformat() for i in range(2)]

    w1, days1 = _week(7)
    p1 = tools.create_weekly_plan(w1)["weekly_plan_id"]
    for day, (name, _) in zip(days1, recipes):
        tools.plan_meal(day, name, "dinner", weekly_plan_id=p1)
    tools.approve_weekly_plan(p1, approved_by="Julia")
    for it in tools.list_grocery_list(status="needed"):
        tools.mark_grocery_item(it["id"], "purchased")
    for item, used in [("Chicken thighs", "1 lb"), ("Broccoli", "1 lb"),
                       ("Ground beef", "1 lb"), ("Onions", "1")]:
        tools.update_inventory(item, "use", quantity=used)

    w2, days2 = _week(14)
    p2 = tools.create_weekly_plan(w2)["weekly_plan_id"]
    for day, (name, _) in zip(days2, recipes):
        tools.plan_meal(day, name, "dinner", weekly_plan_id=p2)
    tools.approve_weekly_plan(p2, approved_by="Julia")

    res = signed_in.get("/api/grocery-list?status=needed")
    assert res.status_code == 200
    on_list = {it["item"] for s in res.json()["sections"] for it in s["items"]}
    assert on_list == {"Chicken thighs", "Broccoli", "Ground beef", "Onions"}

    flags = signed_in.get("/api/grocery-list/pre-shop-flags")
    assert flags.status_code == 200
    assert flags.json()["flags"] == []


# --------------------------------------------------------------- the sibling read

def test_the_assistants_already_have_read_compares_the_amount_too():
    """get_grocery_already_have_items is the same name-only check one door
    over, and it is a chat tool — the assistant reads it back in words, so
    a wrong "you already have that" is said out loud. CATCH — red on
    0633cdd."""
    _need("Chicken thighs", "3 lbs", "meat/seafood")
    _have("Chicken thighs", "2 lbs", "meat/seafood")

    assert [m["item"] for m in tools.get_grocery_already_have_items()] == []


def test_the_assistants_already_have_read_still_reports_a_covered_item():
    """GUARD — green on 0633cdd; the tool has to keep working."""
    _need("Chicken thighs", "3 lbs", "meat/seafood")
    _have("Chicken thighs", "4 lbs", "meat/seafood")

    assert [m["item"] for m in tools.get_grocery_already_have_items()] == ["Chicken thighs"]


# ------------------------------------------------------------------ unchanged rules

def test_a_reviewed_line_is_still_never_flagged():
    """"Yes, I still need it" takes a line out of the conversation for
    good, whatever the kitchen says. GUARD — green on 0633cdd."""
    item_id = _need("Rice", "2 cups", "pantry")
    _have("Rice", "9 cups", "pantry")
    assert "Rice" in _flagged()

    tools.mark_grocery_item_already_have_reviewed(item_id)
    assert "Rice" not in _flagged()


def test_a_loose_name_match_is_still_not_confident_enough():
    """"Garlic" against a tracked "Garlic bulb" was never a confident match
    and still is not, however much of it is on the shelf. GUARD — green on
    0633cdd."""
    _need("Garlic", "2 heads", "produce")
    _have("Garlic bulb", "9 heads", "produce")

    assert "Garlic" not in _flagged()


def test_another_households_kitchen_is_not_read():
    """Household isolation, which the query has always had and must keep.
    GUARD — green on 0633cdd."""
    from app import households

    other = households.create_household("Beta House", "beta-passphrase-xyz")
    with tools.use_household(other):
        _have("Chicken thighs", "50 lbs", "meat/seafood")

    _need("Chicken thighs", "3 lbs", "meat/seafood")

    assert "Chicken thighs" not in _flagged()


def test_another_households_list_is_not_flagged_from_this_kitchen():
    """The other direction of the same boundary: a well-stocked kitchen
    here says nothing about a line over there. GUARD — green on 0633cdd."""
    from app import households

    _have("Chicken thighs", "50 lbs", "meat/seafood")
    other = households.create_household("Beta House", "beta-passphrase-abc")
    with tools.use_household(other):
        _need("Chicken thighs", "3 lbs", "meat/seafood")
        assert "Chicken thighs" not in _flagged()
