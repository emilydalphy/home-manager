"""
A leftovers night's SIDE was never bought, so the salad beside a reheat
had no lettuce on the list.

A leftovers night is deliberately excluded from the grocery ingest — it
eats an earlier night's batch, so buying its ingredients again would
double the shop. That reasoning is exactly right about the DISH and was
never asked about the night's SIDE, which is a different dish, cooked
fresh that evening, and which nothing else on the week buys.

Reproduced on main before anything was changed: Monday's Turkey Chili
cooked double for Thursday, a green salad on Thursday, approve the week,
and the list reads `Ground turkey 3 lbs` and nothing else. No lettuce at
all.

recipes._add_recipe_ingredients_for_entries dropped every leftovers entry
out of the group whatever it was being handed, so the side pass — which
calls it once per entry with that entry's own sides — got the dish's
answer to a question about the salad. `reheat_buys_it` is now asked about
the INGREDIENTS rather than about the night, and
weekly_plan._entry_side_groups / plates.is_big_meal_dish decide it: a
big-meal dish belongs to the holiday table alone and still buys nothing
on a reheat, everything else is cooked on the night it sits on.

Every test here says in its own docstring whether it is a CATCH (red
against main's app/) or a GUARD (green either way, here so the fix can't
take something working with it), and a GUARD names the mutation that
pins it.
"""
import datetime
import json

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import plates as _plates


def _monday() -> datetime.date:
    # The HOUSEHOLD's Monday, not the process's — see conftest.household_today.
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


MON, TUE, THU = _day(0), _day(1), _day(3)

SALAD = {
    "name": "Green salad",
    "ingredients": [{"item": "Lettuce", "qty": "1 head", "category": "produce"}],
    "covers": ["vegetable"],
}
# The one kind of side that belongs to the holiday table rather than to the
# night it sits on: the only one written with a `role` (big_meal.clean_dish).
STUFFING = {
    "name": "Stuffing",
    "role": "side",
    "servings": 7,
    "ingredients": [{"item": "Bread cubes", "qty": "6 cups", "category": "pantry"}],
    "covers": ["carb"],
}


def _household():
    tools.add_member("Emily")
    tools.add_member("Jamie")


def _chili():
    tools.add_recipe(
        "Turkey Chili",
        ingredients=[{"item": "Ground turkey", "qty": "1.5 lbs", "category": "meat"}],
        default_servings=2,
    )


def _chain(cook_day=MON, reheat_day=THU):
    """The reported week: Monday cooks double, Thursday reheats it."""
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    cook = tools.plan_meal(cook_day, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    reheat = tools.plan_meal(reheat_day, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    assert isinstance(tools.set_cook_ahead(cook, [reheat]), dict)
    # Both halves of the chain have to agree or plan_leftover_chains
    # declines to honour it, and then this whole file is about nothing.
    chains = tools.plan_leftover_chains(plan_id)
    assert list(chains["sources"]) == [cook] and list(chains["leftovers"]) == [reheat]
    return plan_id, cook, reheat


def _list():
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list()}


def _links(entry_id: int) -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT g.item FROM meal_plan_grocery_links l JOIN grocery_items g ON g.id = l.grocery_item_id "
        "WHERE l.meal_plan_entry_id = ?",
        (entry_id,),
    ).fetchall()
    conn.close()
    return sorted(r["item"] for r in rows)


# ---------- the reported bug ----------

def test_the_salad_beside_a_reheat_reaches_the_shopping_list():
    """CATCH — the whole report in one. On main the list comes back with
    the turkey and nothing else, so the household is shown a plate with a
    green salad on it and has no lettuce."""
    _household()
    _chili()
    plan_id, _cook, reheat = _chain()
    _plates.attach_sides(reheat, [SALAD], ["vegetable"])

    assert _list() == {}
    tools.approve_weekly_plan(plan_id)

    assert _list() == {"Ground turkey": "3 lbs", "Lettuce": "1 head"}


def test_the_reheated_dish_itself_is_still_bought_only_once():
    """GUARD — the half of the exclusion that was always right, and the
    thing a careless fix breaks: the chili is bought for the batch on the
    cook night and not again on the reheat night. Pinned by the mutation
    that drops the leftovers check entirely (`reheat_buys_it` ignored, or
    the `continue` removed), which takes the turkey to 4.5 lbs."""
    _household()
    _chili()
    plan_id, _cook, reheat = _chain()
    _plates.attach_sides(reheat, [SALAD], ["vegetable"])

    tools.approve_weekly_plan(plan_id)

    assert _list()["Ground turkey"] == "3 lbs"


def test_the_reheats_side_is_bought_for_that_night_and_not_for_the_batch():
    """CATCH — and the reason the chain factor must not follow the side
    onto a reheat. One salad is eaten on Thursday; the batch behind the
    chili has nothing to say about it. (On main nothing is bought at all,
    so this is red there for the reported reason.)"""
    _household()
    _chili()
    plan_id, _cook, reheat = _chain()
    _plates.attach_sides(reheat, [SALAD], ["vegetable"])

    tools.approve_weekly_plan(plan_id)

    # Not "2 heads" — that is the cook night's answer, below.
    assert _list()["Lettuce"] == "1 head"


# ---------- the mirror case the card asks about ----------

def test_a_side_on_the_cook_night_is_still_bought_exactly_once():
    """GUARD — the mirror the card names: the fix must not make the cook
    night's side arrive twice. It is one line, at the batch amount it has
    been bought at since 2026-09-13 (`chain_scale` reaching plate sides),
    and one ledger row against the cook entry. Pinned by the mutation that
    makes the side pass run over the whole group rather than one entry."""
    _household()
    _chili()
    plan_id, cook, reheat = _chain()
    _plates.attach_sides(cook, [SALAD], ["vegetable"])

    tools.approve_weekly_plan(plan_id)

    # Two nights eat from that batch, so its salad is bought for two —
    # unchanged by this fix, and deliberately not this card's to revisit.
    assert _list() == {"Ground turkey": "3 lbs", "Lettuce": "2 heads"}
    assert _links(cook) == ["Ground turkey", "Lettuce"]
    assert _links(reheat) == []


def test_a_side_on_each_end_of_the_chain_buys_each_of_them_once():
    """CATCH — the two halves together, which is where a fix that shared
    one flag between the dish and the side would show up as either a
    missing salad or a doubled one."""
    _household()
    _chili()
    plan_id, cook, reheat = _chain()
    _plates.attach_sides(cook, [SALAD], ["vegetable"])
    _plates.attach_sides(reheat, [SALAD], ["vegetable"])

    tools.approve_weekly_plan(plan_id)

    # 2 for the cook night's batch + 1 for Thursday's own.
    assert _list() == {"Ground turkey": "3 lbs", "Lettuce": "3 heads"}


# ---------- the route a household actually reaches today ----------

def test_adding_a_side_to_a_reheat_on_an_approved_week_puts_it_on_the_list():
    """CATCH — and the reachable one. The plate pass deliberately never
    attaches a side to a reheat (agent._complete_plates_pass), so a side
    on one is there because somebody put it there from the meal screen. On
    main that tap answers "added" with an empty grocery_added and writes
    nothing: the salad is on the meal and on no list."""
    _household()
    _chili()
    plan_id, _cook, reheat = _chain()
    tools.approve_weekly_plan(plan_id)
    before = _list()

    result = _plates.add_component(reheat, key="green-salad")

    assert result["status"] == "added"
    assert result["grocery_added"], "the tap said it added a salad and bought nothing for it"
    after = _list()
    assert set(after) - set(before) == {"Mixed greens", "Lemon"}
    assert _links(reheat) == ["Lemon", "Mixed greens"]


def test_the_same_addition_on_the_cook_night_is_unchanged():
    """GUARD — the other end of the same control, measured against main
    rather than reasoned about: these three lines are byte-identical on
    both trees, so the fix moved the reheat and nothing else.

    It does NOT pin _buy_side_now's chain_scale, and saying so beats
    claiming a mutation that does not bite: at two eaters against the
    catalogue's four servings the lemon rounds up to 1 with the batch
    factor and without it. The four-eater sibling below is the one that
    can tell those apart."""
    _household()
    _chili()
    plan_id, cook, _reheat = _chain()
    tools.approve_weekly_plan(plan_id)

    _plates.add_component(cook, key="green-salad")

    assert _list() == {"Ground turkey": "3 lbs", "Mixed greens": "1 bag", "Lemon": "1"}


def test_a_cook_nights_addition_is_still_bought_for_the_whole_batch():
    """GUARD — four eaters, where the batch factor is visible: the salad
    added to the night that cooks covers the night it feeds, which is the
    2026-09-13 round-2 decision this fix must not quietly undo. Pinned by
    the mutation that makes _buy_side_now pass chain_scale=False, which
    takes the lemon from 2 to 1."""
    _household()
    tools.add_member("Sam")
    tools.add_member("Ada")
    _chili()
    plan_id, cook, _reheat = _chain()
    tools.approve_weekly_plan(plan_id)

    _plates.add_component(cook, key="green-salad")

    assert _list()["Lemon"] == "2"


# ---------- what must still buy nothing ----------

def test_a_big_meal_dish_on_a_reheat_night_still_buys_nothing():
    """GUARD — the one kind of side the exclusion is genuinely about. A
    stuffing written for a hosted holiday's table belongs to that table,
    not to the night it sits on, so a reheat night buys nothing new for
    it.

    Green against main for the trivial reason that main buys NOTHING on a
    reheat, so it proves nothing there. What pins it is the mutation that
    passes `reheat_buys_it=True` unconditionally from the side ingest
    (or drops the big-meal half of _entry_side_groups), which puts the
    bread cubes on the list."""
    _household()
    _chili()
    plan_id, _cook, reheat = _chain()
    _plates.attach_sides(reheat, [STUFFING], ["carb"])

    tools.approve_weekly_plan(plan_id)

    assert "Bread cubes" not in _list()


def test_a_reheat_night_buys_the_salad_beside_the_stuffing_and_not_the_stuffing():
    """CATCH — the two kinds of side on one reheat night, which is the
    only arrangement in which "whose side is this" is doing any work. On
    main neither is bought; here the salad is and the stuffing is not."""
    _household()
    _chili()
    plan_id, _cook, reheat = _chain()
    _plates.attach_sides(reheat, [SALAD], ["vegetable"])
    _plates.attach_sides(reheat, [STUFFING], ["carb"])

    tools.approve_weekly_plan(plan_id)

    assert _list() == {"Ground turkey": "3 lbs", "Lettuce": "1 head"}


def test_a_reheat_with_no_side_at_all_buys_nothing_and_links_nothing():
    """GUARD — the ordinary chain, which is most of them. A reheat night
    with nothing beside it is exactly as it was: no shop, and no ledger
    row that could hold a line on the list past the cook night that
    earned it. Pinned by the mutation that makes the leftovers check
    unconditional in the other direction."""
    _household()
    _chili()
    plan_id, cook, reheat = _chain()

    tools.approve_weekly_plan(plan_id)

    assert _list() == {"Ground turkey": "3 lbs"}
    assert _links(reheat) == []
    assert _links(cook) == ["Ground turkey"]


def test_an_unchained_night_with_a_side_is_unchanged():
    """GUARD — a week with no chain in it at all must be byte-identical,
    and it is: none of the eight mutations run over this file reddens it,
    because none of them can reach a plan plan_leftover_chains reports
    nothing about. It is here so that a later change which starts firing
    the chain rules on an unchained week has something to break."""
    _household()
    _chili()
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    mon = tools.plan_meal(MON, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    _plates.attach_sides(mon, [SALAD], ["vegetable"])

    tools.approve_weekly_plan(plan_id)

    assert _list() == {"Ground turkey": "1.5 lbs", "Lettuce": "1 head"}


# ---------- the line has to be removable again ----------

def test_clearing_the_reheat_takes_its_side_back_off_the_list():
    """CATCH — a line nothing can remove is worse than a missing one, and
    this is the half that says the new ledger row is real. Clearing the
    slot takes the lettuce off AND re-rounds the chili back down to the
    one night still cooking it. (Red against main only because there is no
    lettuce there to remove; the chili half is green either way, and says
    so.)"""
    _household()
    _chili()
    plan_id, _cook, reheat = _chain()
    _plates.attach_sides(reheat, [SALAD], ["vegetable"])
    tools.approve_weekly_plan(plan_id)
    assert _list() == {"Ground turkey": "3 lbs", "Lettuce": "1 head"}

    tools.clear_plan_slot(plan_id, THU, "dinner")

    assert _list() == {"Ground turkey": "1.5 lbs"}


def test_approving_twice_does_not_buy_the_reheats_side_twice():
    """CATCH — the new ledger row earns its keep here. A second approval
    re-runs the ingest over whatever has never contributed
    (_plan_grocery_candidate_entries), and the reheat entry used to be a
    candidate for ever because it never held a link. (Red against main
    for the reported reason: there is no lettuce to count.)"""
    _household()
    _chili()
    plan_id, _cook, reheat = _chain()
    _plates.attach_sides(reheat, [SALAD], ["vegetable"])
    tools.approve_weekly_plan(plan_id)

    tools.approve_weekly_plan(plan_id)

    assert _list() == {"Ground turkey": "3 lbs", "Lettuce": "1 head"}


# ---------- the rule, stated where it is decided ----------

def test_only_a_big_meal_dish_is_the_holiday_tables():
    """GUARD — plates.is_big_meal_dish is the one place that answers
    "whose is this side", and all three ingest call sites read it, so
    they cannot drift about a reheat night.

    It IS red against main's app/, and not for the reason it is named
    after: the name does not exist there, so it dies on an AttributeError
    without reaching an assertion. Read it as a guard and not as evidence
    of the bug. What pins it is the mutation that widens the predicate to
    any side carrying `servings`, which is every side the household added
    from the meal screen."""
    assert _plates.is_big_meal_dish(STUFFING) is True
    assert _plates.is_big_meal_dish(SALAD) is False
    assert _plates.is_big_meal_dish({"name": "Rice", "servings": 4, "added_by": "household"}) is False
    assert _plates.is_big_meal_dish(None) is False
    assert _plates.is_big_meal_dish("not a side") is False


def test_the_side_groups_say_which_sides_are_cooked_on_the_night():
    """GUARD — the shape the two approval-time call sites unpack. A
    big-meal dish and a household side on one entry come back as two
    groups with opposite answers, which is what stops one flag being read
    for both. Pinned by the mutation that stops _entry_side_groups
    telling the two apart (`big_meal_dish = False`); NOT by widening
    plates.is_big_meal_dish, which these two sides are chosen either side
    of and so cannot see."""
    from app.tools import weekly_plan as _weekly_plan

    row = {"sides_json": json.dumps([SALAD, STUFFING])}
    groups = _weekly_plan._entry_side_groups(row)

    assert [(servings, cooked) for _ings, servings, cooked in groups] == [(None, True), (7, False)]
