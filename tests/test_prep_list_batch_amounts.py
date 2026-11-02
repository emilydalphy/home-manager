"""
A prep-list step for a cook that is a BATCH says the batch's amount — the
same amount the Cook card for that night says (defect hunt, 2026-11-02;
prep_list._cooking_list / _chain_reads).

Before: a Tuesday skewers dinner doubled to cover Friday (cook-ahead) put
"Marinate the chicken thighs, 1 lb" on Sunday's prep list while Tuesday's
card said 2 lbs, so half the batch went into the pan unmarinated. The prep
list read the recipe's own baseline; the card is scaled to the batch
(cooker._scale_card_to_batch, off leftovers.batch_for_entry).
"""
import datetime

from conftest import household_today

from app import tools
from app.tools import cooker, prep_sessions


def _d(n: int) -> str:
    return (household_today() + datetime.timedelta(days=n)).isoformat()


def _week():
    for n in ("Alex", "Sam"):
        tools.add_member(n)
    today = household_today()
    tools.set_prep_days([{"weekday": today.strftime("%A").lower(), "minutes": 120}])
    return tools.create_weekly_plan(
        today.isoformat(), content_start_date=today.isoformat(), day_count=7
    )["weekly_plan_id"]


def _plan(plan_id, day, dish):
    return tools.plan_meal(_d(day), dish, slot="dinner", weekly_plan_id=plan_id)["entry_id"]


def _skewers():
    tools.add_recipe("Chicken Skewers", ingredients=[
        {"item": "Boneless skinless chicken thighs", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Lemon", "qty": "1", "category": "produce"},
    ], instructions=["Marinate the chicken in the lemon and oil.", "Grill the skewers."],
        default_servings=2, prep_time_minutes=15, cook_time_minutes=20)


def _soup():
    tools.add_recipe("Onion Soup", ingredients=[
        {"item": "Yellow onion", "qty": "2", "category": "produce"},
        {"item": "Carrots", "qty": "2", "category": "produce"},
    ], instructions=["Slice the onions and carrots.", "Simmer for 30 minutes."],
        default_servings=2, prep_time_minutes=15, cook_time_minutes=30)


def _items(plan_id):
    [session] = prep_sessions.prep_sessions_for_plan(plan_id)
    return [i for i in session["items"] if i["on_list"]]


def _card(plan_id, entry):
    return next(m for m in cooker.get_cooker_view(plan_id)["meals"] if m["entry_id"] == entry)


def test_a_doubled_marinade_says_the_batch_the_card_cooks():
    plan_id = _week()
    _skewers()
    tue = _plan(plan_id, 2, "Chicken Skewers")
    fri = _plan(plan_id, 5, "Chicken Skewers")
    tools.set_cook_ahead(tue, [fri])

    card = _card(plan_id, tue)
    chicken = next(i for i in card["ingredients"] if "chicken" in i["item"].lower())
    assert chicken["qty"] == "2 lbs"
    [marinade] = [i for i in _items(plan_id) if i["kind"] == "marinate"]
    assert marinade["title"] == "Marinate the chicken thighs, 2 lbs"


def test_an_ordinary_marinade_keeps_the_recipe_amount():
    plan_id = _week()
    _skewers()
    _plan(plan_id, 2, "Chicken Skewers")
    [marinade] = [i for i in _items(plan_id) if i["kind"] == "marinate"]
    assert marinade["title"] == "Marinate the chicken thighs, 1 lb"


def test_a_doubled_dinner_chops_for_the_batch():
    plan_id = _week()
    _soup()
    mon = _plan(plan_id, 1, "Onion Soup")
    wed = _plan(plan_id, 3, "Onion Soup")
    tools.set_cook_ahead(mon, [wed])

    [chop] = [i for i in _items(plan_id) if i["key"].startswith("chop:")]
    assert chop["title"] == "4 carrots, 4 yellow onions"
    # Wednesday is a reheat: nothing of its own to chop, never a second count.
    assert str(wed) not in chop["key"]
