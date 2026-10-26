"""
Emily's picks, 2026-10-26 ("go with your picks"), on the card "As two adults
ticking tonight's dinner from two phones...":

58. "I've got Sunday" means the NIGHT, not the dish. Swapping Sunday's dish
    to Tacos used to drop the claim (a swap writes a new entry, and the claim
    was keyed to the old one's cook:<entry> id), so Sunday went back to the
    usual cook. Now the claim stays on the night.
59. An adult who leaves takes a "Nobody yet" THEY set with them, so the
    adult who stays sees the usual cook again. A "Nobody yet" the one who
    stays set still stands (tests/test_moves_owners_hunt.py).
"""
from __future__ import annotations

from app import household_deletion, households, tools
from app.db import get_conn
from app.tools import moves as _moves

from test_move_owner import DAYS, _at, _plan, _recipe

NIGHT = DAYS[3]  # a day ahead of today, so a swap there is allowed


def _two_adults_taking_turns():
    hid = households.create_household("The Beta Testers", "beta-tester-passphrase")
    with tools.use_household(hid):
        ids = {}
        for n in ("Emily", "Vineeth"):
            ids[n] = tools.add_member(n)["member_id"]
            tools.set_member_age_group(n, "adult")
        _recipe()
        _recipe("Tacos")
        plan_id = _plan()
        tools.set_cooking_role("turns")
    return hid, ids, plan_id


def _cook(hid, day=NIGHT):
    with tools.use_household(hid):
        return next(m for m in _moves.moves_for_day(day, now=_at()) if m["kind"] == "cook")


def _not_owner(ids, owner):
    return next(i for n, i in ids.items() if i != owner)


def test_a_claim_stays_on_the_night_when_the_dish_is_swapped():
    """CATCH. Red before: after the swap the night was the default cook's."""
    hid, ids, plan_id = _two_adults_taking_turns()
    before = _cook(hid)
    claimer = _not_owner(ids, before["owner"])
    with tools.use_household(hid):
        tools.set_move_owner(before["id"], claimer, NIGHT)
        tools.swap_meal_in_plan(plan_id, NIGHT, "Tacos", slot="dinner")
    after = _cook(hid)
    assert after["id"] != before["id"], "the swap is expected to write a new entry"
    assert after["title"] == "Tacos"
    assert after["owner"] == claimer


def test_a_nobody_yet_stays_on_the_night_when_the_dish_is_swapped():
    hid, ids, plan_id = _two_adults_taking_turns()
    before = _cook(hid)
    with tools.use_household(hid):
        tools.set_move_owner(before["id"], None, NIGHT)
        tools.swap_meal_in_plan(plan_id, NIGHT, "Tacos", slot="dinner")
    after = _cook(hid)
    assert after["owner"] is None and after["owner_name"] is None


def test_clearing_a_carried_claim_brings_the_default_back():
    """The carried claim is the move's word now: a clear (Undo, or "back to
    usual") really clears it, and the next swap doesn't bring it back."""
    hid, ids, plan_id = _two_adults_taking_turns()
    before = _cook(hid)
    default = before["owner"]
    with tools.use_household(hid):
        tools.set_move_owner(before["id"], _not_owner(ids, default), NIGHT)
        tools.swap_meal_in_plan(plan_id, NIGHT, "Tacos", slot="dinner")
        moved = _cook(hid)
        res = tools.set_move_owner(moved["id"], clear=True, day=NIGHT)
        assert res["previous"]["had_override"] is True
    assert _cook(hid)["owner"] == default
    with tools.use_household(hid):
        tools.swap_meal_in_plan(plan_id, NIGHT, "Chicken Skewers", slot="dinner")
    assert _cook(hid)["owner"] == default


def test_a_claim_does_not_reach_another_night():
    hid, ids, plan_id = _two_adults_taking_turns()
    other_before = _cook(hid, DAYS[4])
    before = _cook(hid)
    with tools.use_household(hid):
        tools.set_move_owner(before["id"], _not_owner(ids, before["owner"]), NIGHT)
        tools.swap_meal_in_plan(plan_id, NIGHT, "Tacos", slot="dinner")
    assert _cook(hid, DAYS[4])["owner"] == other_before["owner"]


def test_a_nobody_yet_the_leaver_set_goes_with_them():
    """CATCH (59). Red before: Emily's night still said nobody."""
    hid, ids, _ = _two_adults_taking_turns()
    with tools.use_household(hid):
        emilys = next(d for d in DAYS if _cook(hid, d)["owner"] == ids["Emily"])
        with tools.use_member(ids["Vineeth"]):
            tools.set_move_owner(_cook(hid, emilys)["id"], None, emilys)
    assert _cook(hid, emilys)["owner"] is None

    household_deletion.remove_member(hid, ids["Vineeth"])

    assert _cook(hid, emilys)["owner_name"] == "Emily"
    conn = get_conn()
    left = conn.execute("SELECT COUNT(*) FROM move_owner_overrides WHERE household_id = ?",
                        (hid,)).fetchone()[0]
    conn.close()
    assert left == 0


def test_a_nobody_yet_the_one_who_stays_set_still_stands():
    hid, ids, _ = _two_adults_taking_turns()
    with tools.use_household(hid):
        with tools.use_member(ids["Emily"]):
            tools.set_move_owner(_cook(hid, DAYS[0])["id"], None, DAYS[0])
    household_deletion.remove_member(hid, ids["Vineeth"])
    assert _cook(hid, DAYS[0])["owner_name"] is None
