"""
"A regular week for a family of 3 shouldn't have 17 peppers, it's not
normal." — Emily, 2026-09-05, looking at the same approved week the
package fix had already cleaned up.

She was right, and the seventeen were not a rounding bug or a summing bug.
Two things were true at once:

  1. Every recipe in the app carries default_servings=4, and the grocery
     path anchored its only scaling factor to the HOUSEHOLD rather than to
     the recipe. So three people bought four people's dinner, every night,
     forever. attendance.servings_scale_factor is the fix — the recipe
     anchor, composed with the attendance one so each applies exactly once.

  2. Per-portion amounts were rounded up to a whole unit once per recipe,
     then summed. Five dinners wanting 2.25, 3, 1.5, 3 and 3 peppers each
     round to 3, 3, 2, 3, 3 = 14, when the week wants 12.75. A shopper buys
     peppers once, so recipes.WeekGroceryBuffer rounds once: 13.

The other half of the same complaint — WHY peppers were in five of seven
dinners at all — is a prompt rule and a plan_quality check; see
test_the_generation_prompt_asks_for_ingredient_variety below and
tests/test_plan_quality.py.
"""
import datetime
import inspect

import pytest

from app import agent, tools
from app.db import get_conn


def _week_start() -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


def _days() -> list[str]:
    return tools._week_dates(_week_start())


def _qty(item: str) -> str | None:
    return next((i["quantity"] for i in tools.list_grocery_list() if i["item"] == item), None)


def _link_qtys(item: str) -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity FROM meal_plan_grocery_links WHERE item = ? ORDER BY id", (item,)
    ).fetchall()
    conn.close()
    return [r["quantity"] for r in rows]


@pytest.fixture
def week() -> int:
    return tools.create_weekly_plan(_week_start())["weekly_plan_id"]


@pytest.fixture
def family_of_three():
    """Emily's actual household."""
    for name in ("Emily", "Vineeth", "Rae"):
        tools.add_member(name)


def _pepper_week(week: int, amounts=("3", "4", "2", "4", "4"), default_servings: int = 4):
    """
    Emily's five dinners. Five DIFFERENT recipes, which is the whole reason
    the rounding has to happen above the recipe-week group: each of these
    is its own call into the ingest.
    """
    for index, amount in enumerate(amounts):
        tools.add_recipe(
            f"Pepper dinner {index}",
            ingredients=[{"item": "Bell peppers", "qty": amount, "category": "produce"}],
            default_servings=default_servings,
        )
    for index, date in enumerate(_days()[:len(amounts)]):
        tools.plan_meal(date, f"Pepper dinner {index}", slot="dinner", weekly_plan_id=week)


# ---------- the number Emily was looking at ----------

def test_emilys_pepper_week_comes_to_thirteen_not_seventeen(week, family_of_three):
    """
    THE test. Five dinners written for 4, eaten by 3: 3, 4, 2, 4 and 4
    peppers become 2.25, 3, 1.5, 3 and 3, which is 12.75 peppers, which is
    13 peppers in a basket.

    Seventeen was the old answer. Fourteen is what you get if you keep the
    servings scaling but round each dinner's share up on its own, which is
    why the buffer exists.
    """
    _pepper_week(week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Bell peppers") == "13"


def test_rounding_happens_once_for_the_week_not_once_per_dinner(week, family_of_three):
    """
    The same five dinners, checked against the number the per-recipe
    rounding would have produced. If this ever comes back 14, the buffer
    stopped spanning the whole approval and each recipe is rounding its own
    share again.
    """
    _pepper_week(week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Bell peppers") != "14", "each dinner rounded up on its own"
    assert _qty("Bell peppers") == "13"


def test_a_recipe_written_for_this_household_is_left_exactly_alone(week, family_of_three):
    """
    The no-op, and the shape the app should converge on now that generation
    is told to write default_servings for the real table: three eaters,
    three servings, factor 1.0, quantities byte-for-byte as written.
    """
    _pepper_week(week, amounts=("3", "4", "2", "4", "4"), default_servings=3)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Bell peppers") == "17", "written for this table, so nothing to rescale"


def test_a_household_with_nobody_on_record_shops_as_it_always_has(week):
    """
    No members yet — a household mid-onboarding. There is no table to
    anchor to, and guessing one is how someone who has told the app nothing
    ends up with a quarter of a dinner. servings_scale_factor falls back to
    attendance alone, which is 1.0.
    """
    _pepper_week(week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Bell peppers") == "17"


# ---------- composing with attendance, exactly once ----------

def test_an_attendance_override_composes_with_the_servings_anchor(week, family_of_three):
    """
    One of the three out on the second night. That night is 2 eaters out of
    a recipe for 4, the other is 3 out of 4 — the two factors multiply to
    eaters/default_servings and neither is applied twice.

    4 peppers each: 3 + 2 = 5 peppers, not 8 (no scaling), not 6 (attendance
    only), and not 3.75 (attendance applied on top of itself).
    """
    days = _days()
    tools.set_member_attendance(days[1], "dinner", "Rae", present=False)
    tools.add_recipe("Pepper dinner", ingredients=[
        {"item": "Bell peppers", "qty": "4", "category": "produce"}], default_servings=4)
    for date in days[:2]:
        tools.plan_meal(date, "Pepper dinner", slot="dinner", weekly_plan_id=week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Bell peppers") == "5"


def test_guests_push_it_back_up(week, family_of_three):
    """Upward is the same arithmetic. Three plus one guest is the recipe's
    own four, so a guest night buys the recipe as written."""
    days = _days()
    tools.set_guest_count(days[0], "dinner", 1)
    tools.add_recipe("Pepper dinner", ingredients=[
        {"item": "Bell peppers", "qty": "4", "category": "produce"}], default_servings=4)
    tools.plan_meal(days[0], "Pepper dinner", slot="dinner", weekly_plan_id=week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Bell peppers") == "4"


def test_a_leftovers_batch_still_scales_on_top(week, family_of_three):
    """
    The third factor in the chain, unchanged by this work. A Tuesday cook
    for three that also feeds a Thursday for three is a batch of six out of
    a recipe written for four: 4 peppers becomes 6, and the Thursday reheat
    buys nothing of its own.
    """
    days = _days()
    tools.add_recipe("Pepper dinner", ingredients=[
        {"item": "Bell peppers", "qty": "4", "category": "produce"}], default_servings=4)
    tools.plan_meal(days[0], "Pepper dinner", slot="dinner", weekly_plan_id=week)
    tools.plan_meal(days[2], "Pepper dinner", slot="dinner", weekly_plan_id=week,
                    derived_from={"links_to": f"{days[0]}:dinner"})
    tools.repair_leftover_chains(week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Bell peppers") == "6"


def test_a_package_is_still_one_package_however_small_the_table(week, family_of_three):
    """Three quarters of a table still buys one whole bottle. The servings
    anchor is a per-portion rule and must not reach the package path."""
    tools.add_recipe("Stir fry", ingredients=[
        {"item": "Olive oil", "qty": "1 bottle", "category": "pantry"},
        {"item": "Baby spinach", "qty": "1 bag", "category": "produce"},
    ], default_servings=4)
    for date in _days()[:3]:
        tools.plan_meal(date, "Stir fry", slot="dinner", weekly_plan_id=week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Olive oil") == "1 bottle"
    assert _qty("Baby spinach") == "1 bag"


def test_a_descriptor_survives_the_scaling(week, family_of_three):
    """
    "Frozen" describes the product, not the amount, and it has to still be
    on the line after the amount has been through the buffer — the shopper
    is looking for it in a different aisle. (It used to be dropped whenever
    an amount was scaled at all; before this branch that was rare, and now
    it would be almost every line.)
    """
    tools.add_recipe("Berry bowl", ingredients=[
        {"item": "Mixed berries", "qty": "4 cups, frozen", "category": "frozen"}],
        default_servings=4)
    for date in _days()[:2]:
        tools.plan_meal(date, "Berry bowl", slot="breakfast", weekly_plan_id=week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Mixed berries") == "6 cups, frozen"


def test_a_prep_descriptor_is_still_not_a_note(week, family_of_three):
    """"3, diced" is a recipe instruction, not something a shopper reads.
    It comes off the amount and does not come back as a note."""
    tools.add_recipe("Salsa night", ingredients=[
        {"item": "Tomatoes", "qty": "4, diced", "category": "produce"}], default_servings=4)
    tools.plan_meal(_days()[0], "Salsa night", slot="dinner", weekly_plan_id=week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Tomatoes") == "3"


def test_a_freeform_quantity_is_still_not_guessed_at(week, family_of_three):
    """"A pinch" has no number in it, so it never enters the buffer and
    three quarters of a pinch is not a thing anyone writes down."""
    tools.add_recipe("Pepper dinner", ingredients=[
        {"item": "Salt", "qty": "a pinch", "category": "pantry"}], default_servings=4)
    tools.plan_meal(_days()[0], "Pepper dinner", slot="dinner", weekly_plan_id=week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Salt") == "a pinch"


# ---------- the ledger has to add back up to the line ----------

def test_the_meals_ledger_sums_to_exactly_what_went_on_the_list(week, family_of_three):
    """
    Rounding once means the five dinners' unrounded shares (2.25, 3, 1.5, 3,
    3) no longer add to the line. They are apportioned instead — whole
    peppers, largest remainder — so the ledger and the list agree.
    """
    _pepper_week(week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    shares = [float(q) for q in _link_qtys("Bell peppers")]
    assert sum(shares) == 13.0
    assert all(share == int(share) for share in shares), "whole peppers per meal, not 2.25"


def test_approving_and_then_clearing_the_week_leaves_nothing_behind(week, family_of_three):
    """
    The symmetry that apportionment buys. With unrounded shares in the
    ledger, clearing this week would subtract 12.75 from 13 and leave a
    quarter of a pepper on the list — which then displays as one whole
    pepper for a dinner nobody is cooking.
    """
    _pepper_week(week)
    tools.approve_weekly_plan(week, approved_by="Emily")
    assert tools.list_grocery_list() != []

    tools.clear_weekly_plan(week)

    assert tools.list_grocery_list() == []


def test_swapping_one_dinner_takes_its_own_share_and_no_more(week, family_of_three):
    """A swap trims the line by that dinner's apportioned share and leaves
    the rest of the week's peppers alone."""
    _pepper_week(week)
    tools.add_recipe("Soup", ingredients=[
        {"item": "Carrots", "qty": "4", "category": "produce"}], default_servings=3)
    tools.approve_weekly_plan(week, approved_by="Emily")
    before = int(_qty("Bell peppers"))
    first_share = int(_link_qtys("Bell peppers")[0])

    tools.swap_meal_in_plan(week, _days()[0], "Soup", slot="dinner")

    assert int(_qty("Bell peppers")) == before - first_share
    assert 0 < before - first_share < before


def test_a_measurable_amount_rounds_once_too(week, family_of_three):
    """
    Not just countables. Three dinners wanting 4 cups each, written for 4
    and eaten by 3, is 9 cups exactly — and the ledger is denominated in the
    same unit as the line, so a swap can actually subtract from it.
    """
    tools.add_recipe("Chili", ingredients=[
        {"item": "Beans", "qty": "4 cups", "category": "pantry"}], default_servings=4)
    for date in _days()[:3]:
        tools.plan_meal(date, "Chili", slot="dinner", weekly_plan_id=week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Beans") == "9 cups"
    assert _link_qtys("Beans") == ["3 cups", "3 cups", "3 cups"]


# ---------- the prompt half ----------

def _prompt_text(fn) -> str:
    """The instructions as the model actually reads them — the source's
    line-continuation backslashes joined back up, so a test can assert on a
    sentence rather than on where it happened to wrap."""
    return inspect.getsource(fn).replace("\\\n", "")


def test_the_generation_prompt_asks_for_ingredient_variety():
    """
    The root cause Emily actually asked about. The prompt had variety rules
    for protein and for cuisine and none for an ingredient, so nothing
    stopped the model putting peppers in five of seven dinners. Both
    instruction blocks now carry the rule.
    """
    for fn in (agent.generate_weekly_plan_llm, agent.generate_component_plan_llm):
        text = _prompt_text(fn)
        assert "AT MOST 3" in text, "the same fresh ingredient, capped at 3 meals"
        assert "17 bell peppers" in text, "say why the rule exists"
        assert "onion, garlic, cooking oil" in text, "a genuine staple is exempt"


def test_the_generation_prompt_asks_for_the_households_own_servings():
    """A new recipe is written for the table that will eat it, not a
    generic 4 — which is what stops the ingest having to rescale at all."""
    for fn in (agent.generate_weekly_plan_llm, agent.generate_component_plan_llm):
        text = _prompt_text(fn)
        assert "attendance.default_serves" in text
        assert "not a generic 4" in text
