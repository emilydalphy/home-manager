"""
"How did X go?" is about a cook, never a reheat (overnight 2026-10-22).

Ticking a leftovers / made-ahead night "Mark eaten" stamps cooked_at like
any tick, and get_feedback_nudge read every done row with a cooked_at — so
the banner could ask about a night nobody cooked. Cook's own toast already
treats that tick as eaten, not cooked (no "Rate it"); the nudge now skips
the same nights the cook view marks is_leftovers (a confirmed chain, or a
portion out of the freezer).
"""
from conftest import household_date

from app import tools


def _chili() -> None:
    tools.add_recipe("Chili", ingredients=[{"item": "black beans", "qty": "2 cans"}])


def _cook_and_reheat() -> tuple[int, int]:
    """Yesterday cooks double, today reheats it — a confirmed chain."""
    _chili()
    plan_id = tools.create_weekly_plan(household_date(-1))["weekly_plan_id"]
    cook = tools.plan_meal(
        household_date(-1), "Chili", slot="dinner", weekly_plan_id=plan_id,
        derived_from={"make_double_for": [f"{household_date(0)}:dinner"]},
    )["entry_id"]
    reheat = tools.plan_meal(
        household_date(0), "Chili", slot="dinner", weekly_plan_id=plan_id,
        derived_from={"links_to": f"{household_date(-1)}:dinner"},
    )["entry_id"]
    return cook, reheat


def test_a_ticked_reheat_alone_asks_nothing():
    """CATCH. Only the reheat is ticked: there is no cook to ask about."""
    _, reheat = _cook_and_reheat()
    tools.check_off_meal(reheat, "done")
    assert tools.get_feedback_nudge() == {"has_nudge": False}
    assert not [i for i in tools.get_attention_items() if i["kind"] == "feedback_nudge"]


def test_cook_shows_that_night_as_a_reheat():
    """
    GUARD. The fixture really is the night Cook's toast treats as eaten
    (is_leftovers on the cook view), so the two surfaces agree about it.
    """
    _, reheat = _cook_and_reheat()
    card = next(m for m in tools.get_cooker_view()["meals"] if m["entry_id"] == reheat)
    assert card.get("is_leftovers") is True


def test_the_cook_behind_a_newer_reheat_is_still_asked_about():
    """
    GUARD. Skipping the reheat must not hide the cook it eats from: both
    ticked, the reheat newest, the question is still about the cook.
    """
    cook, reheat = _cook_and_reheat()
    tools.check_off_meal(cook, "done")
    tools.check_off_meal(reheat, "done")
    nudge = tools.get_feedback_nudge()
    assert nudge["has_nudge"] is True
    assert nudge["meal"] == "Chili"


def test_an_ordinary_cook_is_still_asked_about():
    """GUARD. The nudge's whole job."""
    _chili()
    entry = tools.plan_meal(household_date(0), "Chili", slot="dinner")["entry_id"]
    tools.check_off_meal(entry, "done")
    assert tools.get_feedback_nudge()["has_nudge"] is True


def test_a_portion_out_of_the_freezer_asks_nothing():
    """CATCH. A freezer night ticked eaten is a reheat too (cook view: is_leftovers)."""
    _chili()
    plan_id = tools.create_weekly_plan(household_date(-1))["weekly_plan_id"]
    entry = tools.plan_meal(
        household_date(0), "Chili", slot="dinner", weekly_plan_id=plan_id,
        derived_from={"from_freezer": {"dish": "Chili"}},
    )["entry_id"]
    card = next(m for m in tools.get_cooker_view()["meals"] if m["entry_id"] == entry)
    assert card.get("is_leftovers") is True
    tools.check_off_meal(entry, "done")
    assert tools.get_feedback_nudge() == {"has_nudge": False}
