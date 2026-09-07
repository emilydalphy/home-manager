"""
A plain (no-chain) cook card kept the recipe's own default_servings even
when the table it's actually for is a different size — Emily, 2026-09-07,
seeing "for 2" in a card's own text (from attendance) right next to a
"Serves 4" stepper (from the untouched recipe default). Both numbers were
answering the same question with different data: attendance for the text,
the recipe's baseline for the stepper.

get_cooker_view (app/tools/cooker.py) now scales a plain day-based card's
default_servings (and ingredients) to leftovers.eaters_at(date, slot) —
the same helper and the same recipe-scaling arithmetic a leftover chain's
source night already uses to scale to its batch — so the stepper agrees
with the attendance-based "for N" text. It deliberately leaves `servings`
itself untouched (None) on a plain card: that field is reserved for "this
card covers a batch bigger than one night" (a chain source's "for 6"
chip), and test_leftovers_batch.py already pins servings=None for an
ordinary, unchained cook. Chain cards (source or leftover) are left
exactly as _apply_leftover_chains already set them.
"""
import datetime

from app import tools


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


MON, TUE, THU = _day(0), _day(1), _day(3)


def _by_date(view):
    return {m["date"]: m for m in view["meals"]}


def _soup(default_servings=4):
    tools.add_recipe(
        "Soup",
        ingredients=[{"item": "stock", "qty": "1 L"}, {"item": "carrot", "qty": "2"}],
        default_servings=default_servings,
    )


def test_plain_card_scales_to_eaters_for_that_date_and_slot():
    tools.add_member("Alex")
    tools.add_member("Sam")
    _soup(default_servings=4)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(MON, "Soup", slot="dinner", weekly_plan_id=plan_id)

    card = _by_date(tools.get_cooker_view(plan_id))[MON]

    # Two people on record, nobody marked away: eaters_at(MON, "dinner")
    # is 2, not the recipe's own baseline of 4 — the stepper now agrees
    # with the attendance-based "for 2" text instead of showing "Serves 4".
    assert card["default_servings"] == 2
    assert card["servings"] is None, "not a batch chip — this is one ordinary night, scaled"
    assert {i["item"]: i["qty"] for i in card["ingredients"]} == {
        "stock": "0.5 l", "carrot": "1",
    }


def test_plain_card_follows_attendance_not_just_household_size():
    tools.add_member("Alex")
    tools.add_member("Sam")
    tools.add_member("Rae")
    _soup(default_servings=4)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(MON, "Soup", slot="dinner", weekly_plan_id=plan_id)
    tools.set_member_attendance(MON, "dinner", "Rae", present=False)

    card = _by_date(tools.get_cooker_view(plan_id))[MON]

    assert card["default_servings"] == 2
    assert card["servings"] is None


def test_unknown_eaters_leaves_the_recipe_default():
    # No household members on record at all: eaters_at returns 0, which
    # callers treat as "don't scale" rather than "cook nothing" — the card
    # keeps showing one honest number, the recipe's own default, instead
    # of a fabricated "Serves 0".
    _soup(default_servings=4)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(MON, "Soup", slot="dinner", weekly_plan_id=plan_id)

    card = _by_date(tools.get_cooker_view(plan_id))[MON]

    assert card["default_servings"] == 4
    assert card["servings"] is None


def test_chain_source_card_is_unchanged_by_the_new_scaling():
    tools.add_member("Alex")
    tools.add_member("Sam")
    tools.add_member("Rae")
    _soup(default_servings=3)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(TUE, "Soup", slot="dinner", weekly_plan_id=plan_id,
                    reasoning="Quick on a Tuesday")
    tools.plan_meal(THU, "Soup", slot="dinner", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"{TUE}:dinner"})
    tools.repair_leftover_chains(plan_id)

    by_date = _by_date(tools.get_cooker_view(plan_id))
    tuesday = by_date[TUE]
    thursday = by_date[THU]

    # Still the chain's own batch-of-6 (3 at Tuesday's table + 3 at
    # Thursday's) — not re-scaled down to Tuesday-alone's headcount of 3,
    # and not touched a second time by the new plain-card loop.
    assert tuesday["servings"] == 6
    assert tuesday["is_leftovers"] is False
    # The leftover night is still a reheat with its own real table, exactly
    # as _apply_leftover_chains already set it — no recipe to scale.
    assert thursday["is_leftovers"] is True
    assert thursday["servings"] == 3
    assert thursday["has_full_recipe"] is False
