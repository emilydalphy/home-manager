"""
After approve: only real questions, asked once — not ten.

Emily, walking flow 2 on 2026-09-15: "Two quick ones before you go" was
TEN questions. A freezer ask listing tonight's already-thawed shrimp and a
whole chicken that was on the shopping list she had just been handed, plus
NINE batch-cook asks — including *"Trail mix … it'd cook on Tuesday. Which
other days should it cover?"*, and apple slices, cheese and crackers,
cucumbers and yogurt with it. A nonsense question at the moment of leaving
undoes the approval's good feeling.

Three things changed, and this file is one section each:

  1. cook_ahead._is_a_cook — the batch offer is only ever about a dish
     somebody actually cooks.
  2. defrost's rules — the freezer ask only lists what the app has no
     other record of: what the fridge demonstrably covers, a move the app
     booked itself off tracked inventory, and a night it is too late to
     thaw for. The first is _KitchenStock's answer, not a second one; the
     other two are per NIGHT. (Until 2026-09-21 there were four: "a line
     still to buy" left off, which emptied the ask at the one moment it is
     shown. Section 3 below is now the reverse promise — the line is what
     a tapped chip takes off — and a move THIS step booked is offered
     again with its chip on, so the answer can be changed. See
     tests/test_freezer_at_approval_all_meat.py.)
  3. shell.js — the ask card itself, since retired (2026-09-18): see
     section 3 below for what replaced it.

WHAT IS RED WHERE, measured rather than claimed, 2026-09-15.

Against `main`'s app/ and static/, with this file's `_function` lookups
stubbed (it cannot be COLLECTED against main otherwise — the prelude names
five functions main has not got): **33 of 56 fail.** 24 are behavioural —
they call functions main has and get a different answer, the last of them
being the heading, which main writes out as a literal. 9 are red only
because they name something main has not got (the seven node tests of the
one-line shape, and the two CSS markers), and are pinned by MUTATION
instead. The other 23 are green on main and say so in their own
docstrings.

**That split flatters section 2 and the number to read is the other one.**
Main asks about everything, so every test here of the shape "this is still
asked" passes there for free. The real evidence for the freezer rules is
redness against THIS BRANCH'S OWN FIRST COMMIT, which needs no stubbing:
**11 of 56 are red on `259b166`** — every false negative an adversarial
review reproduced (a shelf the app guessed from a purchase, a pantry row,
two ounces against a three-pound need, a capital F on Freezer, an
unanswered carried line, a skipped move, one booked night silencing a
whole week) plus two found while fixing them (the need summed across
nights, and the ask coming back after the shop).

**24 mutations were run and every one reddens at least one test.** In
cook_ahead: the `_is_a_cook` filter, its instructions clause. In defrost:
each of the four rules dropped outright; clause 1 widened to any shelf,
to a guessed shelf, and to name-only; clause 2 given every status, keyed
by name rather than by night, and unscoped from its plan; clause 3 given
`carried`, given every status, and unscoped from its household; clause 4
never firing; and the need not summed across nights. In recipes:
_KitchenStock ignoring its new location filter. In shell.js:
cookAheadPrimePicks' write, the lines' sub-line, the several-blocks
branch, the heading's count. In shell.css: the .ca-ask-pick rule and the
empty-row rule.
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import pytest

from conftest import household_today
from app import agent, db, tools
from app.tools import cook_ahead, defrost, inventory
from tests import nodeharness

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _day(n: int) -> str:
    return (_monday() + datetime.timedelta(days=n)).isoformat()


WEEK = _monday().isoformat()
MON, TUE, WED, THU, FRI = _day(0), _day(1), _day(2), _day(3), _day(4)


def _household():
    for n in ("Emily", "Vineeth", "Reid"):
        tools.add_member(n)


def _no_cook(name="Trail mix"):
    """What Emily was asked about: a real recipe, and nothing about it is
    cooked — no prep time, no cook time, no steps."""
    tools.add_recipe(name, ingredients=[{"item": name.lower(), "qty": "1 cup"}], default_servings=3)


def _cooked(name="Roasted Chickpeas", **kw):
    tools.add_recipe(
        name, ingredients=[{"item": "chickpeas", "qty": "1 can"}], default_servings=3,
        prep_time_minutes=kw.pop("prep", 5), cook_time_minutes=kw.pop("cook", 30),
        instructions=kw.pop("instructions", ["Heat the oven to 200C.", "Roast 30 minutes."]),
    )


def _plan(*meals):
    """(date, dish, slot) triples on one approved-shaped plan."""
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    ids = []
    for date_str, dish, slot in meals:
        ids.append(tools.plan_meal(date_str, dish, slot=slot, weekly_plan_id=plan_id)["entry_id"])
    return plan_id, ids


def _dishes(plan_id):
    return [i["dish"] for i in cook_ahead.cook_ahead_repeats(plan_id)]


# ---------------------------------------------------------------------------
# 1. Batch cook is only ever about a dish somebody cooks
# ---------------------------------------------------------------------------

def test_a_repeated_snack_nobody_cooks_is_not_offered_as_a_batch():
    """CATCH — main offers "Trail mix … which other days should it cover?"."""
    _household()
    _no_cook()
    plan_id, _ = _plan((MON, "Trail mix", "snack"), (TUE, "Trail mix", "snack"),
                       (WED, "Trail mix", "snack"))

    assert _dishes(plan_id) == []


@pytest.mark.parametrize("dish", ["Apple slices", "Cheese and crackers", "Cucumbers", "Greek yogurt"])
def test_the_rest_of_the_walks_nine_are_gone_too(dish):
    """CATCH — every one of these is its own question on main."""
    _household()
    _no_cook(dish)
    plan_id, _ = _plan((MON, dish, "snack"), (TUE, dish, "snack"))

    assert _dishes(plan_id) == []


def test_a_repeated_dish_that_is_actually_cooked_is_still_offered():
    """GUARD — main is right about this one, and the narrowing must not
    take it with the others. The whole ticket fails if this goes."""
    _household()
    _cooked()
    plan_id, _ = _plan((TUE, "Roasted Chickpeas", "dinner"), (THU, "Roasted Chickpeas", "dinner"))

    items = cook_ahead.cook_ahead_repeats(plan_id)
    assert [i["dish"] for i in items] == ["Roasted Chickpeas"]
    assert [d["date"] for d in items[0]["later"]] == [THU]


def test_a_week_of_snacks_around_one_real_cook_asks_about_the_cook_alone():
    """CATCH — the reported shape end to end: eight questions become one."""
    _household()
    for name in ("Trail mix", "Apple slices", "Cheese and crackers", "Cucumbers", "Greek yogurt"):
        _no_cook(name)
    _cooked()
    plan_id, _ = _plan(
        (MON, "Trail mix", "snack"), (TUE, "Trail mix", "snack"), (WED, "Trail mix", "snack"),
        (MON, "Apple slices", "snack"), (TUE, "Apple slices", "snack"),
        (MON, "Cheese and crackers", "breakfast"), (TUE, "Cheese and crackers", "breakfast"),
        (WED, "Cucumbers", "lunch"), (THU, "Cucumbers", "lunch"),
        (THU, "Greek yogurt", "breakfast"), (FRI, "Greek yogurt", "breakfast"),
        (TUE, "Roasted Chickpeas", "dinner"), (THU, "Roasted Chickpeas", "dinner"),
    )

    assert _dishes(plan_id) == ["Roasted Chickpeas"]


def test_a_written_method_with_no_times_on_it_is_still_a_cook():
    """CATCH by mutation rather than against main (main offers it either
    way). A recipe with steps and no minutes is somebody at a stove with a
    number missing from the recipe — take the instructions clause out of
    _is_a_cook and this goes red."""
    _household()
    tools.add_recipe("Braised Beans", ingredients=[{"item": "beans", "qty": "1 can"}],
                     default_servings=3, instructions=["Soften the onion.", "Simmer for an hour."])
    plan_id, _ = _plan((TUE, "Braised Beans", "dinner"), (THU, "Braised Beans", "dinner"))

    assert _dishes(plan_id) == ["Braised Beans"]


def test_prep_time_alone_is_a_cook():
    """GUARD by mutation — a no-oven dish that still takes twenty minutes
    of somebody's hands is a batch worth making once."""
    _household()
    tools.add_recipe("Chopped Salad Jars", ingredients=[{"item": "romaine", "qty": "2 heads"}],
                     default_servings=3, prep_time_minutes=20)
    plan_id, _ = _plan((TUE, "Chopped Salad Jars", "lunch"), (THU, "Chopped Salad Jars", "lunch"))

    assert _dishes(plan_id) == ["Chopped Salad Jars"]


def test_a_repeated_freeform_meal_is_not_offered():
    """CATCH — nothing behind a typed meal says it is cooked, and the
    screens already agree: get_week_menu has no recipe to read times off
    and hands back meta: null, which shell.js's isRealCook reads as not a
    cook."""
    _household()
    plan_id, _ = _plan((MON, "Whatever's in the fridge", "lunch"),
                       (TUE, "Whatever's in the fridge", "lunch"))

    assert _dishes(plan_id) == []


def test_the_route_asks_about_the_cook_and_nothing_else(signed_in):
    """CATCH — the same thing over the wire the All set screen uses."""
    _household()
    _no_cook()
    _cooked()
    plan_id, _ = _plan((MON, "Trail mix", "snack"), (TUE, "Trail mix", "snack"),
                       (TUE, "Roasted Chickpeas", "dinner"), (THU, "Roasted Chickpeas", "dinner"))

    body = signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()
    assert [i["dish"] for i in body["items"]] == ["Roasted Chickpeas"]


# ---------- the narrowing is the ASK's, not the picker's ----------

def test_the_cook_cards_own_picker_is_deliberately_unchanged():
    """GUARD — cook_ahead_options keeps the wider _is_cookable. Two
    different questions: "is this worth asking about" and "what days can
    this row claim". A chain already written has to stay editable and
    releasable whatever the dish is."""
    _household()
    _no_cook()
    plan_id, ids = _plan((MON, "Trail mix", "snack"), (TUE, "Trail mix", "snack"))

    options = cook_ahead.cook_ahead_options(plan_id)
    assert [d["entry_id"] for d in options[ids[0]]] == [ids[1]]


def test_a_batch_already_made_on_a_no_cook_dish_can_still_be_undone():
    """GUARD — the reason the picker keeps the wider rule. If set_cook_ahead
    had been narrowed too, a household who batched their trail mix before
    this shipped could never release it again."""
    _household()
    _no_cook()
    plan_id, ids = _plan((MON, "Trail mix", "snack"), (TUE, "Trail mix", "snack"))
    assert isinstance(cook_ahead.set_cook_ahead(ids[0], [ids[1]]), dict)

    released = cook_ahead.set_cook_ahead(ids[0], [])
    assert released == {"source_entry_id": ids[0], "covered_entry_ids": [], "make_double_for": []}


def test_another_households_repeat_is_never_offered_here():
    """GUARD by mutation — _plan_rows is household-scoped; drop that and
    this goes red. The read this branch narrowed is the one that decides
    what a household is asked about its own week."""
    _household()
    _cooked()
    plan_id, _ = _plan((TUE, "Roasted Chickpeas", "dinner"), (THU, "Roasted Chickpeas", "dinner"))

    conn = db.get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (99, 'Next door')")
    conn.commit()
    conn.close()
    assert [i["dish"] for i in cook_ahead.cook_ahead_repeats(plan_id)] == ["Roasted Chickpeas"]


# ---------------------------------------------------------------------------
# 2. The freezer ask lists only what the app has no other record of
# ---------------------------------------------------------------------------
# These plan NEXT week, the way tests/test_defrost_confirm.py already does,
# and for a reason worth knowing: this ask now drops a night it is too late
# to thaw for, so a cook night close to today makes every other rule in
# this section untestable. Two of these passed for the wrong reason before
# that was spotted — a "Whole Chicken" needs 48 hours, so a Thursday
# dinner read as too late on a Wednesday and dropped out for the wrong
# clause, and under the CI clock matrix's Friday and Sunday pins that
# happens to nearly all of them.

NEXT_WEEK = (_monday() + datetime.timedelta(days=7)).isoformat()
NEXT_TUE = (_monday() + datetime.timedelta(days=8)).isoformat()
NEXT_THU = (_monday() + datetime.timedelta(days=10)).isoformat()
NEXT_SAT = (_monday() + datetime.timedelta(days=12)).isoformat()


def _meat(name="Chicken Skewers", item="Chicken Thighs", qty="1 lb"):
    tools.add_recipe(name, ingredients=[{"item": item, "qty": qty, "category": "meat/seafood"}],
                     default_servings=3, prep_time_minutes=10, cook_time_minutes=15)


def _meat_week(*meals):
    """(date, dish) pairs on a plan for NEXT week, so nothing is too late
    to thaw for. Defaults to one chicken dinner on the Thursday."""
    plan_id = tools.create_weekly_plan(NEXT_WEEK)["weekly_plan_id"]
    for date_str, dish in (meals or ((NEXT_THU, "Chicken Skewers"),)):
        tools.plan_meal(date_str, dish, slot="dinner", weekly_plan_id=plan_id)
    return plan_id


def _items(plan_id):
    return [i["item"] for i in defrost.meat_items_for_plan(plan_id)]


def _nights(plan_id):
    return [(i["item"], [n["date"] for n in i["nights"]])
            for i in defrost.meat_items_for_plan(plan_id)]


def _on_list(plan_id):
    return [(i["item"], i["on_list"]) for i in defrost.meat_items_for_plan(plan_id)]


def _frozen(plan_id):
    return [(i["item"], i["frozen"]) for i in defrost.meat_items_for_plan(plan_id)]


def _sql(query, *args):
    conn = db.get_conn()
    conn.execute(query, args)
    conn.commit()
    conn.close()


def test_a_thing_in_the_freezer_the_app_knows_nothing_else_about_is_still_asked():
    """GUARD, and the one that matters most: this is the real "you need to
    thaw this". The household has it, so the grocery ingest never put it on
    the list, and nothing has scheduled a move. A missed thaw is worse than
    an extra question, so this test is the floor the narrowing sits on."""
    _household()
    _meat()
    plan_id = _meat_week()

    assert _items(plan_id) == ["Chicken Thighs"]


# ---------- 1. what the fridge demonstrably covers ----------

def test_enough_of_it_in_the_fridge_is_not_asked_about():
    """CATCH — tonight's shrimp. It is out of the freezer already, so
    "is it in the freezer?" is a question about a thing the app can see."""
    _household()
    _meat("Shrimp Skewers", "Shrimp")
    plan_id = _meat_week((NEXT_THU, "Shrimp Skewers"))
    tools.update_inventory("Shrimp", "add", quantity="3 lbs", location="fridge", category="meat/seafood")

    assert _items(plan_id) == []


def test_two_ounces_in_the_fridge_does_not_silence_a_three_pound_need():
    """CATCH — and it is the defect `overnight/inventory-covers-the-amount`
    removed from the grocery ingest on 2026-09-14, arriving one door over.
    The first cut of this branch asked the name-only question that branch
    exists to have replaced; this one reuses its _KitchenStock, so the two
    read one shelf by one rule."""
    _household()
    _meat(qty="1.5 lbs")
    plan_id = _meat_week((NEXT_TUE, "Chicken Skewers"), (NEXT_THU, "Chicken Skewers"))
    tools.update_inventory("Chicken Thighs", "add", quantity="2 oz", location="fridge", category="meat/seafood")

    assert _items(plan_id) == ["Chicken Thighs"]


def test_the_need_is_the_whole_weeks_need_not_one_nights():
    """CATCH by mutation — a pound in the fridge against two dinners of a
    pound each is enough for one of them and not for the week. Count only
    the first night and the second dinner's thaw goes unasked, which is
    the same "told about the same stock twice" failure _KitchenStock's own
    claim ledger exists to stop one level up."""
    _household()
    _meat(qty="1 lb")
    plan_id = _meat_week((NEXT_TUE, "Chicken Skewers"), (NEXT_THU, "Chicken Skewers"))
    tools.update_inventory("Chicken Thighs", "add", quantity="1.5 lbs",
                           location="fridge", category="meat/seafood")

    assert _items(plan_id) == ["Chicken Thighs"]


def test_a_shelf_the_app_guessed_from_a_purchase_is_never_a_reason_not_to_ask():
    """CATCH — the bulk-meat buyer, and the sharpest false negative this
    branch had. Tick a 5 lb pack purchased and _add_to_inventory files it
    under the meat category's default shelf, 'fridge', having never seen
    one. It is in the freezer, it feeds two dinners, and the first cut of
    this branch said nothing at all about it."""
    _household()
    _meat()
    plan_id = _meat_week((NEXT_TUE, "Chicken Skewers"), (NEXT_THU, "Chicken Skewers"))
    tools.add_grocery_item("Chicken Thighs", quantity="5 lbs", category="meat/seafood")
    conn = db.get_conn()
    line = conn.execute("SELECT id FROM grocery_items WHERE item = 'Chicken Thighs'").fetchone()["id"]
    conn.close()
    tools.mark_grocery_item(line, "purchased")

    conn = db.get_conn()
    row = conn.execute("SELECT location, source FROM inventory_items").fetchone()
    conn.close()
    # The premise: the app really did file it in the fridge, on its own.
    assert (row["location"], row["source"]) == ("fridge", "grocery_checkoff")
    assert _items(plan_id) == ["Chicken Thighs"]


def test_a_pantry_row_is_neither_thawed_nor_in_the_fridge():
    """CATCH — criterion 3 says "already recorded as thawed or in the
    fridge". A mis-categorised receipt or a pantry scan is neither, and the
    first cut read every shelf but the freezer as a reason to stay quiet."""
    _household()
    _meat()
    plan_id = _meat_week()
    tools.update_inventory("Chicken Thighs", "add", quantity="3 lbs", location="pantry", category="meat/seafood")

    assert _items(plan_id) == ["Chicken Thighs"]


def test_a_freezer_spelled_with_a_capital_is_still_the_freezer():
    """CATCH — and it was a DOUBLE miss: defrost_candidates_for_plan's own
    `== "freezer"` already skips it, so the automatic task never happens,
    and the first cut's `!= "freezer"` then read it as known and took the
    ask away too. The auto path is untouched and still misses it; what is
    restored is that the ask catches what the auto path drops."""
    _household()
    _meat()
    plan_id = _meat_week()
    tools.update_inventory("Chicken Thighs", "add", quantity="3 lbs", location="Freezer", category="meat/seafood")

    assert _items(plan_id) == ["Chicken Thighs"]


def test_a_row_with_no_location_on_it_is_read_the_way_every_screen_reads_it():
    """GUARD by mutation — a legacy row saved before the location column
    takes its category's default (quantities._display_location), which is
    what get_inventory hands back and what defrost_candidates_for_plan
    already treats as frozen. Ask the raw column instead and a
    frozen-category row reads as "not in the freezer"."""
    _household()
    _meat("Fish Pie", "Cod Fillets")
    plan_id = _meat_week((NEXT_THU, "Fish Pie"))
    _sql("INSERT INTO inventory_items (household_id, item, quantity, category) "
         "VALUES (1, 'Cod Fillets', '2 lbs', 'frozen')")

    assert _items(plan_id) == ["Cod Fillets"]


# ---------- 3. a line still to buy is asked about, and says so ----------
# Reversed on 2026-09-21 (Loop Board "Freezer question at approval asks
# about every meat in the week"): the whole chicken on the list IS the
# question — "do you already have this frozen?" — and a yes takes the
# line off. So a line to buy no longer silences, and each item says
# whether it has one (`on_list`), which is what the step's "off the
# shopping list" half is promised on.

def test_something_still_on_the_shopping_list_is_asked_about_and_says_so():
    """CATCH — the whole chicken. Until 2026-09-21 this list was empty at
    the one moment the step is shown, because approval had just put the
    week's meat on the list."""
    _household()
    _meat("Roast Chicken", "Whole Chicken")
    plan_id = _meat_week((NEXT_THU, "Roast Chicken"))
    tools.add_grocery_item("Whole Chicken", quantity="1", category="meat/seafood")

    assert _on_list(plan_id) == [("Whole Chicken", True)]


@pytest.mark.parametrize("status", ["needed", "in_cart", "spice"])
def test_every_kind_of_line_still_to_buy_counts_as_on_the_list(status):
    """CATCH — a line waiting in the sorting queue is as much "you are
    going to buy this" as a plain needed one, so a yes takes it off too."""
    _household()
    _meat()
    plan_id = _meat_week()
    tools.add_grocery_item("Chicken Thighs", quantity="1 lb", category="meat/seafood")
    _sql("UPDATE grocery_items SET status = ?", status)

    assert _on_list(plan_id) == [("Chicken Thighs", True)]


def test_an_unanswered_carried_line_is_not_this_steps_to_answer():
    """CATCH against the first cut of this rule, which counted 'carried'
    as on the list. A carried line is last week's line waiting for a keep
    or a drop — the carry-over step's question — so this step neither
    reads it as a line to take off nor sets it aside."""
    _household()
    _meat()
    plan_id = _meat_week()
    tools.add_grocery_item("Chicken Thighs", quantity="1 lb", category="meat/seafood")
    _sql("UPDATE grocery_items SET status = 'carried'")

    assert _on_list(plan_id) == [("Chicken Thighs", False)]


@pytest.mark.parametrize("status", ["purchased", "removed"])
def test_a_line_already_bought_or_taken_off_is_not_on_the_list(status):
    """GUARD by mutation — widen the status clause to every row and this
    goes red. A purchased line is in the kitchen, not on the list, and a
    removed one is nowhere; the question is still asked, with only the
    fridge half to promise."""
    _household()
    _meat()
    plan_id = _meat_week()
    tools.add_grocery_item("Chicken Thighs", quantity="1 lb", category="meat/seafood")
    _sql("UPDATE grocery_items SET status = ?", status)

    assert _on_list(plan_id) == [("Chicken Thighs", False)]


def test_a_line_excluded_from_the_list_is_not_on_the_list():
    """GUARD by mutation — "Getting it elsewhere" takes a line off the
    list, and this step does not take it off twice."""
    _household()
    _meat()
    plan_id = _meat_week()
    tools.add_grocery_item("Chicken Thighs", quantity="1 lb", category="meat/seafood")
    _sql("UPDATE grocery_items SET excluded_from_list = 1")

    assert _on_list(plan_id) == [("Chicken Thighs", False)]


def test_a_plural_spelling_is_still_the_same_thing():
    """CATCH — the list line and the recipe line rarely agree about a
    trailing s, and "Chicken Thigh" on the list is "Chicken Thighs" in the
    plan. Same plural tolerance the rest of this module already matches
    names with."""
    _household()
    _meat()
    plan_id = _meat_week()
    tools.add_grocery_item("Chicken Thigh", quantity="1 lb", category="meat/seafood")

    assert _on_list(plan_id) == [("Chicken Thighs", True)]


# ---------- 2 and 4: a move already settled, night by night ----------
# A move THIS step booked is not settled any more (2026-09-21): the Plan
# tab's freezer row reopens the step to change the answer, so the chip is
# offered again, already on. A move the app booked itself off tracked
# freezer inventory still is — the app knows, and a second yes would book
# it twice (tests/test_freezer_at_approval_all_meat.py has that one).

def test_a_move_already_booked_is_offered_again_with_its_chip_on():
    """CATCH — answering writes the defrost rows; reopening the step must
    show that answer rather than an empty list, or there is no way to take
    it back."""
    _household()
    _meat()
    plan_id = _meat_week()
    assert _frozen(plan_id) == [("Chicken Thighs", False)]

    defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])
    assert _frozen(plan_id) == [("Chicken Thighs", True)]


def test_a_move_already_ticked_done_still_reads_as_frozen():
    """CATCH — "already recorded as thawed", in Emily's own words: the
    answer was yes, and the row says so whether or not the move is done."""
    _household()
    _meat()
    plan_id = _meat_week()
    defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])
    _sql("UPDATE prep_tasks SET status = 'done'")

    assert _frozen(plan_id) == [("Chicken Thighs", True)]


def test_a_skipped_move_leaves_the_question_askable():
    """CATCH against the first cut, which had no status filter at all.
    'skipped' is the Now tile's one-tap decline (cooker.check_off_prep_step)
    — the household saying they will not move it tonight, which says
    nothing about whether the food is frozen."""
    _household()
    _meat()
    plan_id = _meat_week()
    defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])
    _sql("UPDATE prep_tasks SET status = 'skipped'")

    assert _frozen(plan_id) == [("Chicken Thighs", False)]


def test_one_night_booked_keeps_every_night_and_a_dinner_added_afterwards_can_still_be_booked():
    """CATCH against the first cut, which keyed this by NAME: one booked
    night made the item unbookable for the whole rest of the week, so a
    second chicken dinner swapped in after the answer could never have its
    thaw scheduled. Both nights are offered (the booked one is what makes
    the chip read as on), and answering again books the new one."""
    _household()
    _meat()
    plan_id = _meat_week((NEXT_TUE, "Chicken Skewers"))
    defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])
    tools.plan_meal(NEXT_THU, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)

    assert _nights(plan_id) == [("Chicken Thighs", [NEXT_TUE, NEXT_THU])]
    assert _frozen(plan_id) == [("Chicken Thighs", True)]
    created = defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])["created"]
    assert sorted(c["date"] for c in created) == [NEXT_TUE, NEXT_THU]


def test_a_move_booked_on_a_different_week_says_nothing_about_this_one():
    """GUARD by mutation — the task read is scoped to this plan."""
    _household()
    _meat()
    after = (_monday() + datetime.timedelta(days=14)).isoformat()
    other = tools.create_weekly_plan(after)["weekly_plan_id"]
    tools.plan_meal((_monday() + datetime.timedelta(days=17)).isoformat(),
                    "Chicken Skewers", slot="dinner", weekly_plan_id=other)
    # The premise: a task really was booked, on that other week.
    assert defrost.confirm_frozen_items(other, ["Chicken Thighs"])["created"]
    plan_id = _meat_week()

    assert _frozen(plan_id) == [("Chicken Thighs", False)]


# A period that starts TODAY and runs a week, so "tonight" and "four days
# out" are both on the plan whatever weekday the suite is run on — which
# the CI clock matrix makes a real requirement, not a nicety.
#
# The HOUSEHOLD's today, like everything else in this file. This used to be
# the one place that read the server's date.today(), because the rule under
# test did — "to match confirm_frozen_items", which at the time also read
# the server's. confirm_frozen_items moved onto the household's clock on
# 2026-09-17 (tests/test_defrost_household_clock.py), the ask's too-late
# rule moved with it in the same commit, and so did this: seeding from the
# other clock makes these two tests red under any straddling zone for the
# two clocks disagreeing rather than for anything about thawing.
def _from_today_plan(*meals):
    today = household_today()
    plan_id = tools.create_weekly_plan(
        today.isoformat(), content_start_date=today.isoformat(), day_count=7,
    )["weekly_plan_id"]
    for offset, dish in meals:
        tools.plan_meal((today + datetime.timedelta(days=offset)).isoformat(),
                        dish, slot="dinner", weekly_plan_id=plan_id)
    return plan_id


def test_a_night_it_is_too_late_to_thaw_for_is_not_offered():
    """CATCH — Emily's "tonight's already-eaten shrimp", settled without
    guessing at a shelf. confirm_frozen_items refuses to write a task whose
    move date has gone by and hands back TOO_LATE_TO_THAW_NOTE instead, so
    asking about such a night can only ever produce a note."""
    _household()
    _meat("Shrimp Skewers", "Shrimp")
    plan_id = _from_today_plan((0, "Shrimp Skewers"))

    assert _items(plan_id) == []


def test_a_night_still_ahead_survives_a_night_that_is_too_late():
    """CATCH — the too-late rule is per NIGHT like the booked one, so a
    dish cooked tonight AND later in the week keeps the night that can
    still be thawed for."""
    _household()
    _meat("Shrimp Skewers", "Shrimp")
    plan_id = _from_today_plan((0, "Shrimp Skewers"), (4, "Shrimp Skewers"))

    later = (household_today() + datetime.timedelta(days=4)).isoformat()
    assert _nights(plan_id) == [("Shrimp", [later])]


# ---------- where a shelf came from: the scan paths ----------
# A re-review (2026-09-15) found the guessed-shelf blocker still open one
# path over: agent.scan_receipt_image returns no location key at all, so a
# 5 lb pack photographed off the receipt landed under the meat category's
# default, 'fridge', and the thaw was never mentioned. The fridge and
# pantry scans do tag every row (_tag_scan_location), so they are a real
# statement about a shelf and still count.

def _confirm_scan(client, kind=None, location=None, item="Chicken Thighs", quantity="5 lbs"):
    body = {"items": [{"item": item, "quantity": quantity, "category": "meat/seafood"}]}
    if location is not None:
        body["items"][0]["location"] = location
    if kind is not None:
        body["kind"] = kind
    assert client.post("/api/inventory/confirm-scan", json=body).status_code == 200


def _inventory_row():
    conn = db.get_conn()
    row = conn.execute("SELECT item, location, source FROM inventory_items").fetchone()
    conn.close()
    return dict(row)


def test_a_receipt_scan_never_takes_the_thaw_away(signed_in):
    """CATCH against 0ab91f4 — the blocker again, through a path the first
    two source names did not cover. The shipped client fills the review
    sheet's location select from guessLocationForCategory, so the row
    reaches the server looking stated; it is not, and a receipt cannot say
    where anything went."""
    _household()
    _meat()
    plan_id = _meat_week()
    _confirm_scan(signed_in, kind="receipt", location="fridge")

    assert _inventory_row()["source"] == "scan_receipt"
    assert _items(plan_id) == ["Chicken Thighs"]


def test_a_scan_that_says_nothing_about_itself_is_read_as_placing_nothing(signed_in):
    """CATCH against 0ab91f4 — the raw agent.scan_receipt_image payload,
    which is what the reviewer posted: no `kind`, no `location`, so the
    shelf is _resolve_location's category default twice over. A caller
    that tells us nothing is the case the fallback exists for."""
    _household()
    _meat()
    plan_id = _meat_week()
    _confirm_scan(signed_in)

    assert _inventory_row() == {"item": "Chicken Thighs", "location": "fridge", "source": "scan"}
    assert _items(plan_id) == ["Chicken Thighs"]


def test_a_fridge_scan_really_did_look_at_a_shelf(signed_in):
    """GUARD — the other half, and the reason this is about the SOURCE and
    not about scans in general. scan_fridge_photo tags every row through
    _tag_scan_location, so 'fridge' there is a photograph of a fridge, not
    a category default, and it still answers the question."""
    _household()
    _meat()
    plan_id = _meat_week()
    _confirm_scan(signed_in, kind="fridge", location="fridge")

    assert _inventory_row()["source"] == "scan_fridge"
    assert _items(plan_id) == []


def test_a_fridge_scan_that_saw_the_freezer_compartment_still_asks(signed_in):
    """GUARD — the same scan, the other shelf. Nothing about the source
    rule reaches a row that is actually in the freezer."""
    _household()
    _meat()
    plan_id = _meat_week()
    _confirm_scan(signed_in, kind="fridge", location="freezer")

    assert _items(plan_id) == ["Chicken Thighs"]


@pytest.mark.parametrize("kind,location,expected", [
    ("receipt", "fridge", "scan_receipt"),
    ("fridge", "fridge", "scan_fridge"),
    ("pantry", "pantry", "scan_pantry"),
    ("fridge", None, "scan"),        # tagged nothing, whatever it says it is
    ("", "fridge", "scan"),          # an old client, or none
    ("nonsense", "fridge", "scan"),
])
def test_scan_source_falls_back_to_the_one_that_claims_nothing(kind, location, expected):
    """GUARD by mutation — every unknown road leads to "scan", which is IN
    the guessed set, so the fallback costs a question rather than a thaw."""
    assert inventory.scan_source(kind, location) == expected
    if expected == "scan":
        assert expected in inventory.GUESSED_LOCATION_SOURCES


def test_the_chat_tool_still_writes_chat_and_cannot_be_told_otherwise():
    """GUARD — `source` is threaded for the routes, and the assistant must
    not be able to set it: it is absent from this tool's schema, so a model
    call always takes the default. Both halves asserted, because the
    default alone would be true of a schema that offered it."""
    _household()
    tools.update_inventory("Chicken Thighs", "add", quantity="2 lbs", category="meat/seafood")
    assert _inventory_row()["source"] == "chat"

    for tool in agent.TOOL_DEFINITIONS:
        if tool["name"] in ("update_inventory", "update_inventory_items"):
            assert "source" not in tool["input_schema"]["properties"]


# ---------- isolation, scope, and the route ----------

def test_another_households_fridge_does_not_silence_this_one():
    """GUARD by mutation — drop household_id from the grocery read and this
    goes red; the inventory side is _KitchenStock's own scoping. Three
    cross-household leaks are on record in this repo, and a read that
    decides what a household is asked is exactly their shape."""
    _household()
    _meat()
    plan_id = _meat_week()
    conn = db.get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (99, 'Next door')")
    conn.execute("INSERT INTO inventory_items (household_id, item, quantity, location, category) "
                 "VALUES (99, 'Chicken Thighs', '9 lbs', 'fridge', 'meat/seafood')")
    conn.execute("INSERT INTO grocery_items (household_id, item, quantity, status) "
                 "VALUES (99, 'Chicken Thighs', '1 lb', 'needed')")
    conn.commit()
    conn.close()

    assert _items(plan_id) == ["Chicken Thighs"]


def test_something_not_in_this_weeks_plan_is_never_asked_about():
    """GUARD — already true, and it is one of the criterion's own clauses,
    so it is said here rather than left implied elsewhere."""
    _household()
    _meat()
    tools.add_recipe("Pork Chops", ingredients=[{"item": "Pork Chops", "qty": "3", "category": "meat/seafood"}],
                     default_servings=3, prep_time_minutes=5, cook_time_minutes=20)
    plan_id = _meat_week()

    assert _items(plan_id) == ["Chicken Thighs"]


def test_the_route_serves_the_narrowed_list(signed_in):
    """CATCH — over the wire: the shrimp the fridge covers is left off, and
    the whole chicken on the list is asked about (with its line) beside
    the one that always was."""
    _household()
    _meat("Shrimp Skewers", "Shrimp")
    _meat("Roast Chicken", "Whole Chicken")
    _meat()
    plan_id = _meat_week((NEXT_TUE, "Shrimp Skewers"), (NEXT_THU, "Roast Chicken"),
                         (NEXT_SAT, "Chicken Skewers"))
    tools.update_inventory("Shrimp", "add", quantity="3 lbs", location="fridge", category="meat/seafood")
    tools.add_grocery_item("Whole Chicken", quantity="1", category="meat/seafood")

    body = signed_in.get(f"/api/week/{NEXT_WEEK}/defrost-items").json()
    assert body["weekly_plan_id"] == plan_id
    assert [(i["item"], i["on_list"], i["frozen"]) for i in body["items"]] == [
        ("Chicken Thighs", False, False), ("Whole Chicken", True, False),
    ]


def test_after_an_approval_a_household_that_tracks_nothing_is_asked_about_all_of_it():
    """CATCH — Emily's screen, reversed on 2026-09-21. The step renders
    right after approval, and approval has just put the week's meat on the
    shopping list — which is now exactly the question: the chip is offered
    WITH its line, and a yes takes the line off."""
    _household()
    _meat()
    plan_id = _meat_week()
    assert _on_list(plan_id) == [("Chicken Thighs", False)]  # the premise: before the list exists

    tools.approve_weekly_plan(plan_id)
    assert _on_list(plan_id) == [("Chicken Thighs", True)]


def test_and_it_stays_askable_once_the_food_is_home():
    """CATCH — ticking the line purchased takes it off the list and files
    it under the meat category's default shelf — a guess — so the step
    still asks about it, now with only the fridge half to promise."""
    _household()
    _meat()
    plan_id = _meat_week((NEXT_TUE, "Chicken Skewers"), (NEXT_THU, "Chicken Skewers"))
    tools.approve_weekly_plan(plan_id)

    conn = db.get_conn()
    line = conn.execute("SELECT id FROM grocery_items WHERE item LIKE 'Chicken%'").fetchone()["id"]
    conn.close()
    tools.mark_grocery_item(line, "purchased")

    assert _nights(plan_id) == [("Chicken Thighs", [NEXT_TUE, NEXT_THU])]
    assert _on_list(plan_id) == [("Chicken Thighs", False)]


def test_when_the_app_knows_about_all_of_it_there_is_nothing_to_ask():
    """CATCH — the screen Emily should have got: neither ask has anything
    real to say, so neither is shown."""
    _household()
    _meat("Shrimp Skewers", "Shrimp")
    _no_cook()
    plan_id = _meat_week((NEXT_TUE, "Shrimp Skewers"), (NEXT_THU, "Trail mix"))
    tools.plan_meal(NEXT_SAT, "Trail mix", slot="snack", weekly_plan_id=plan_id)
    tools.plan_meal(NEXT_THU, "Trail mix", slot="snack", weekly_plan_id=plan_id)
    tools.update_inventory("Shrimp", "add", quantity="3 lbs", location="fridge", category="meat/seafood")

    assert defrost.meat_items_for_plan(plan_id) == []
    assert cook_ahead.cook_ahead_repeats(plan_id) == []


# ---------------------------------------------------------------------------
# 3. The screen
# ---------------------------------------------------------------------------
# The ask card these questions were drawn on (All set's "Two quick ones
# before you go", the root receipt's fold) went on 2026-09-18: the freezer
# question is a step of its own and batch cooking is assumed from prep days
# (tests/test_plan_cards_2026_09_18.py, tests/test_batch_from_prep_days.py).
# Sections 1 and 2 above are what the server still promises.

def test_the_ask_card_and_its_heading_are_gone():
    for gone in ("cookAheadAskCardHtml", "quick ones before you go", "cookAheadPickLinesHtml", "wk-quick-card"):
        assert gone not in SHELL_JS, gone
    assert ".ca-ask-pick" not in SHELL_CSS and ".wk-quick-card" not in SHELL_CSS
