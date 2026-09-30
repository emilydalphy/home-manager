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
    that removes the leftovers `continue` outright, which takes the turkey
    to 4.5 lbs.

    NOT by "`reheat_buys_it` ignored" — measured on review, that mutation
    makes the check STRICTER (every reheat dropped again, i.e. main), so
    the turkey stays at 3 lbs and this test stays green. The two were
    named as one in the first draft of this docstring and they are
    opposite: ignoring the flag reddens 7 tests here and none of them is
    this one; removing the `continue` reddens 11 and this is among
    them."""
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


def test_a_side_on_each_end_of_the_chain_buys_the_cooks_for_two_and_the_reheats_for_one():
    """CATCH — the two halves together, which is where a fix that shared
    one flag between the dish and the side would show up as either a
    missing salad or a doubled one.

    It is named for what it measures rather than for what would be tidy,
    because the number is not obviously the right one and a reader should
    see that. Three heads buy TWO salads: the cook night's own salad is
    scaled to cover the night it feeds (the 2026-09-13 round-2 decision,
    untouched here) and Thursday's own salad is bought on top. Un-batch
    the same week and the app settles on two, so the approved state is one
    head heavier than the app's own answer once the chain is gone.

    Reachable only by a deliberate tap — the plate pass never puts a side
    on a reheat (agent._complete_plates_pass) — so it is a household that
    asked for a salad on Thursday being sold one, over a cook-night salad
    they were never shown on Thursday's plate. Whether the cook night's
    side should still cover a fed night that has a side of its own is the
    open half of the 2026-09-13 decision and is NOT this card's to
    settle."""
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


# ---------- found on review, characterised rather than fixed ----------

def test_a_freeform_reheats_side_is_still_not_bought_and_is_NOT_fixed_here():
    """CHARACTERISATION — the residue, so nobody reports it as new. This
    fix reaches a reheat backed by a RECIPE, which is the shape every
    batch the app writes has (meal_variety._write_batches and
    agent._expand_repeated_dates both re-plan the same dish, so the reheat
    row carries the cook's recipe_id). A reheat written as freeform text
    ("Leftover chili" — the shape submit_weekly_plan's own schema still
    allows) is not reached, and the reason is one door up:
    _plan_grocery_candidate_entries is `JOIN recipes r ON r.id =
    mpe.recipe_id`, so a freeform entry is not a candidate at all.

    That is wider than this card and pre-existing: measured on main and
    here, a freeform entry's side buys nothing whether it is chained or
    not. Invert this when that JOIN becomes a LEFT JOIN."""
    _household()
    _chili()
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    cook = tools.plan_meal(MON, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    reheat = tools.plan_meal(THU, "Leftover chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
        (json.dumps({"links_to": f"{MON}:dinner"}), reheat),
    )
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
        (json.dumps({"make_double_for": [f"{THU}:dinner"]}), cook),
    )
    conn.commit()
    conn.close()
    assert list(tools.plan_leftover_chains(plan_id)["leftovers"]) == [reheat]
    _plates.attach_sides(reheat, [SALAD], ["vegetable"])

    tools.approve_weekly_plan(plan_id)

    assert "Lettuce" not in _list()
    # And it is the candidate query rather than the chain: the same
    # freeform entry with no chain at all buys nothing for its side either.
    assert _links(reheat) == []


# ---------- a night off on a reheat that owns a side (Emily's option (b), 2026-09-30) ----------
#
# Was the characterisation test
# `test_taking_the_night_off_on_a_reheat_strands_its_sides_line_and_is_NOT_fixed_here`.
# It pinned that tonight_night_off (kind `freeze_reheat`) left the lettuce on
# the list with ZERO ledger rows behind it, and that Undo only put the link
# back. Inverted rather than deleted so the history reads: the row's side now
# comes off the list with the row (weekly_plan.delete_plan_entry reverses it),
# and plan_undo carries the grocery LINES so Undo puts the line back exactly.
#
# Every test here is a CATCH (red on main) unless its docstring says GUARD,
# and a GUARD names the mutation that pins it (each was run).

GREENS = {
    "name": "Side greens",
    "ingredients": [{"item": "Lettuce", "qty": "1 head", "category": "produce"}],
    "covers": ["vegetable"],
}


def _dump():
    """Everything a night off may touch, byte for byte."""
    conn = get_conn()
    out = {
        t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()]
        for t in ("meal_plan_entries", "prep_tasks", "meal_plan_grocery_links", "grocery_items", "inventory_items")
    }
    conn.close()
    return out


def _lettuce_links() -> int:
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM meal_plan_grocery_links l "
        "JOIN grocery_items g ON g.id = l.grocery_item_id WHERE g.item = 'Lettuce'"
    ).fetchone()["n"]
    conn.close()
    return n


def _lettuce_id() -> int:
    return next(i["id"] for i in tools.list_grocery_list(status="all") if i["item"] == "Lettuce")


def _reheat_tonight(shared: bool = False):
    """Yesterday cooks Turkey Chili double, tonight reheats it with a green
    salad. `shared` puts a second, unrelated dinner with its own salad on
    the week, so both merge onto ONE Lettuce line (2 heads, 2 links).

    The period is anchored on YESTERDAY, not on the household's Monday:
    the cook has to be the night before tonight, and on a Monday that
    night falls outside a Monday-start week — the weekday cliff CI's
    `clock` matrix exists to catch."""
    today = household_today()
    yesterday = today - datetime.timedelta(days=1)
    plan_id = tools.create_weekly_plan(yesterday.isoformat())["weekly_plan_id"]
    _household()
    _chili()
    cook = tools.plan_meal(
        yesterday.isoformat(), "Turkey Chili", slot="dinner", weekly_plan_id=plan_id,
    )["entry_id"]
    reheat = tools.plan_meal(
        today.isoformat(), "Turkey Chili", slot="dinner", weekly_plan_id=plan_id,
    )["entry_id"]
    tools.set_cook_ahead(cook, [reheat])
    _plates.attach_sides(reheat, [SALAD], ["vegetable"])
    other = None
    if shared:
        tools.add_recipe(
            "Fish Tacos",
            ingredients=[{"item": "White fish", "qty": "1 lb", "category": "meat"}],
            default_servings=2,
        )
        later = (today + datetime.timedelta(days=2)).isoformat()
        other = tools.plan_meal(later, "Fish Tacos", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
        _plates.attach_sides(other, [GREENS], ["vegetable"])
    tools.approve_weekly_plan(plan_id)
    return plan_id, cook, reheat, other


def test_taking_the_night_off_on_a_reheat_takes_its_sides_line_with_it():
    """CATCH — the card's first acceptance criterion. On main the lettuce
    stays at 1 head with no ledger row behind it."""
    _reheat_tonight()
    assert _list()["Lettuce"] == "1 head"

    assert tools.tonight_night_off()["kind"] == "freeze_reheat"

    assert "Lettuce" not in _list()
    assert _lettuce_links() == 0
    # The dish's own shopping is the batch's, and the batch keeps its size.
    assert _list()["Ground turkey"] == "3 lbs"


def test_undo_puts_the_reheat_and_its_line_back_exactly():
    """GUARD — the constraint that made this non-trivial, driven rather
    than reasoned about: every table the night off touched, byte for byte,
    the Lettuce line back under its OWN id. Green on main only because main
    never took the line off; pinned by the mutation that drops
    plan_undo._restore_lines (the line stays gone and so does the link)."""
    _plan, _cook, reheat, _ = _reheat_tonight()
    line_id = _lettuce_id()
    before = _dump()

    tools.tonight_night_off()
    assert _dump() != before
    assert tools.tonight_night_off_undo()["status"] == "restored"

    assert _dump() == before
    assert _lettuce_id() == line_id
    assert _links(reheat) == ["Lettuce"]


def test_a_shared_line_is_trimmed_and_undo_puts_the_quantity_back():
    """CATCH — two nights each carry a green salad, merged onto ONE line.
    The night off takes only the reheat's head; the line stays for the
    other night; Undo puts the head back while nobody has touched it."""
    _plan, _cook, reheat, other = _reheat_tonight(shared=True)
    assert _list()["Lettuce"] == "2 heads" and _lettuce_links() == 2
    before = _dump()

    tools.tonight_night_off()
    assert _list()["Lettuce"] == "1 head"
    assert _links(other) == ["Lettuce", "White fish"]
    assert _lettuce_links() == 1

    assert tools.tonight_night_off_undo()["status"] == "restored"
    assert _dump() == before


def test_a_shared_line_the_household_edited_since_keeps_their_number():
    """GUARD (the assumption Emily can override) — the night off trimmed a
    shared line to 1 head, then somebody typed 3. Undo puts the reheat
    back and re-links its salad, and leaves THEIR 3 alone. Pinned by the
    mutation that restores a trimmed line without the still-as-left
    comparison (it writes 2 heads over their 3)."""
    _plan, _cook, reheat, _other = _reheat_tonight(shared=True)
    tools.tonight_night_off()
    tools.update_grocery_item(_lettuce_id(), quantity="3 heads")

    assert tools.tonight_night_off_undo()["status"] == "restored"

    assert _list()["Lettuce"] == "3 heads"
    assert _links(reheat) == ["Lettuce"]
    assert _lettuce_links() == 2


def test_a_shared_line_ticked_into_the_cart_since_is_left_in_the_cart():
    """CATCH — "exactly as left" is every column, not just the quantity:
    a line ticked into the cart after the night off keeps its tick and its
    trimmed quantity rather than being put back as 'needed'."""
    _reheat_tonight(shared=True)
    tools.tonight_night_off()
    tools.mark_grocery_item(_lettuce_id(), "in_cart")

    tools.tonight_night_off_undo()

    line = next(i for i in tools.list_grocery_list(status="all") if i["item"] == "Lettuce")
    assert (line["quantity"], line["status"]) == ("1 head", "in_cart")


def test_a_line_already_in_the_cart_is_left_alone_both_ways():
    """GUARD — the reversal's standing rule (a line in a cart is the
    shopper's) holds here too, and so Undo has nothing of the list to put
    back and the week comes back exactly. Pinned by the mutation that
    makes _reverse_meal_grocery_contributions reverse in_cart lines."""
    _reheat_tonight()
    tools.mark_grocery_item(_lettuce_id(), "in_cart")
    before = _dump()

    tools.tonight_night_off()
    line = next(i for i in tools.list_grocery_list(status="all") if i["item"] == "Lettuce")
    assert (line["quantity"], line["status"]) == ("1 head", "in_cart")

    tools.tonight_night_off_undo()
    assert _dump() == before


def test_a_second_undo_puts_nothing_back_twice():
    """GUARD — the record goes with the holder it was stamped on, so a
    second tap (the other phone) is "nothing to put back" and the line is
    not inserted twice. Pinned by dropping _restore_lines (the first undo
    then leaves the shared line at 1 head)."""
    _reheat_tonight(shared=True)
    tools.tonight_night_off()
    tools.tonight_night_off_undo()
    after_first = _dump()

    assert tools.tonight_night_off_undo()["status"] == "refused"
    assert _dump() == after_first
    assert _list()["Lettuce"] == "2 heads"


def test_undo_after_an_unrelated_grocery_change_still_puts_the_line_back():
    """GUARD — adding something else to the list in between is not a
    change to anything the night off touched: Undo still restores the
    lettuce exactly, and the new line stays. Pinned by dropping
    _restore_lines, and would catch a still_as_left widened to the whole
    list."""
    _reheat_tonight()
    tools.tonight_night_off()
    tools.add_grocery_item("Milk", quantity="1 carton", category="dairy")

    assert tools.tonight_night_off_undo()["status"] == "restored"

    assert _list()["Lettuce"] == "1 head"
    assert _list()["Milk"] == "1 carton"


def test_a_refused_undo_leaves_the_line_off_too():
    """GUARD — an Undo that refuses because the week changed writes
    nothing at all, the list included: the night off stands, whole.
    Pinned by the mutation that restores lines before still_as_left."""
    plan_id, cook, _reheat, _ = _reheat_tonight()
    tools.tonight_night_off()
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?", (cook,))
    conn.commit()
    conn.close()
    before = _dump()

    assert tools.tonight_night_off_undo()["status"] == "refused"
    assert _dump() == before
    assert "Lettuce" not in _list()


# ---------- the second door: cook_on_fed (weekly_plan.move_cook_onto_fed_night) ----------

def _cook_tonight_for_tomorrow(greens_on_filler: bool = False):
    """The card's 2026-09-30 measurement: tonight's chili is cooked double
    for tomorrow, tomorrow's reheat carries a green salad, and every other
    night of the period has a dinner of its own, so nothing is free and the
    night off answers `cook_on_fed`."""
    today = household_today()
    plan_id = tools.create_weekly_plan(today.isoformat())["weekly_plan_id"]
    _household()
    _chili()
    cook = tools.plan_meal(today.isoformat(), "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tomorrow = (today + datetime.timedelta(days=1)).isoformat()
    reheat = tools.plan_meal(tomorrow, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.set_cook_ahead(cook, [reheat])
    for offset in range(2, 7):
        name = f"Filler {offset}"
        tools.add_recipe(name, ingredients=[{"item": f"Thing {offset}", "qty": "1 lb", "category": "meat"}],
                         default_servings=2)
        day = (today + datetime.timedelta(days=offset)).isoformat()
        filler = tools.plan_meal(day, name, slot="dinner", weekly_plan_id=plan_id)["entry_id"]
        if greens_on_filler and offset == 2:
            # A second salad on the week, merged onto the same Lettuce line.
            _plates.attach_sides(filler, [GREENS], ["vegetable"])
    _plates.attach_sides(reheat, [SALAD], ["vegetable"])
    tools.approve_weekly_plan(plan_id)
    return plan_id, cook, reheat


def test_cook_on_fed_takes_the_replaced_reheats_side_off_the_list():
    """CATCH — reproduced on main by the card: "Ground turkey 3 lbs,
    Lettuce 1 head | lettuce links: 0" after the move. The side lived on
    the ENTRY that was deleted, so it is on no night at all; its line now
    goes with it."""
    _plan, cook, reheat = _cook_tonight_for_tomorrow()
    assert _list()["Lettuce"] == "1 head"

    out = tools.tonight_night_off()
    assert out["kind"] == "cook_on_fed"

    assert "Lettuce" not in _list()
    assert _lettuce_links() == 0
    assert _list()["Ground turkey"] == "3 lbs"


def test_cook_on_fed_undo_puts_the_reheat_and_its_line_back_exactly():
    """GUARD — the same door, the undo driven: every table byte for byte,
    the reheat row under its own id with its salad re-linked. Pinned by
    the mutation that leaves `grocery` out of tonight's undo record."""
    _plan, _cook, reheat = _cook_tonight_for_tomorrow()
    before = _dump()

    assert tools.tonight_night_off()["kind"] == "cook_on_fed"
    assert tools.tonight_night_off_undo()["status"] == "restored"

    assert _dump() == before
    assert _links(reheat) == ["Lettuce"]


def test_the_review_steppers_minus_shares_the_door_and_its_undo_is_exact():
    """CATCH — drop_dish_from_day (the Review stepper's "−") moves a cook
    onto its fed night through the SAME move_cook_onto_fed_night, so the
    replaced reheat's side comes off the list there too, and drop_dish_undo
    puts the line back with the rest of the week.

    Exact by the "−"'s own standard (test_drop_dish_self_solving): the
    plan's rows and prep byte for byte, and the list by what it says. The
    BATCH's line is not id-stable on this door and never was — the rescale
    after the commit (_rescale_after_a_chain_moved) reverses and re-adds
    the recipe group's lines, SIDES INCLUDED (the reheat carries the
    chili's recipe_id, so it is in the group), on the way down and again on
    Undo. Measured: plan_undo puts the lettuce back under its own id and the
    rescale then re-adds it under a new one, same quantity, same links."""
    plan_id, cook, reheat = _cook_tonight_for_tomorrow()
    before = _dump()
    before_list = _list()

    out = tools.drop_dish_from_day(plan_id, cook)
    assert out["status"] == "dropped"
    assert "Lettuce" not in _list()

    assert tools.drop_dish_undo(plan_id, out["undo_entry_id"])["status"] == "restored"
    after = _dump()
    for table in ("meal_plan_entries", "prep_tasks", "inventory_items"):
        assert after[table] == before[table], table
    assert _list() == before_list
    assert _links(reheat) == ["Lettuce"]
    assert _lettuce_links() == 1


def test_on_the_minus_door_the_rescale_has_the_last_word_on_an_edited_side_line():
    """CHARACTERISATION — found by the branch's independent review, and the
    one place the "keep the household's number" rule does NOT hold. The
    night off keeps an edited shared line (test above); the "−" cannot,
    because drop_dish_undo's rescale (_rescale_after_a_chain_moved) runs
    AFTER plan_undo and re-ingests the chili's recipe group, sides
    included — the reheat carries the chili's recipe_id — recomputing the
    plan line from the ledger. That recompute is how the "−" has always
    treated the batch's own line; the side now shares it. Pre-existing in
    kind, bounded (the number is the plan's own answer, never less than
    the meals need), and the _rescale_leftover_source_grocery card's
    territory rather than this one's. Invert it if that rescale learns to
    leave a side's line alone."""
    plan_id, cook, _reheat = _cook_tonight_for_tomorrow(greens_on_filler=True)
    assert _list()["Lettuce"] == "2 heads"

    out = tools.drop_dish_from_day(plan_id, cook)
    assert _list()["Lettuce"] == "1 head"
    tools.update_grocery_item(_lettuce_id(), quantity="5 heads")
    tools.drop_dish_undo(plan_id, out["undo_entry_id"])

    assert _list()["Lettuce"] == "2 heads", "the rescale recomputed it; if 5, invert this"
    assert _lettuce_links() == 2
