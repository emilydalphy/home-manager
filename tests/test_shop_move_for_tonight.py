"""
"Shop for tonight" only when something on the list is for tonight.

Loop Board, 2026-09-15 (HIGH · Bug), from Emily's own walk of Flow 0: Now
said "Shop for tonight · 3 items · by 6:05" while tonight's Garlic Butter
Shrimp was already thawed in the fridge and the three needed lines — whole
chicken, rice, dish soap — were for nothing that night. The rule was
"anything on the list, any cook inside the 36-hour horizon", which is two
true facts standing next to each other pretending to be one.

The rule now, stated once:

    A deadline is claimed only when the SOONEST cook in the horizon that
    the list is actually waiting on can name one. "Waiting on" is the
    per-meal ledger (meal_plan_grocery_links) — grocery.entry_ids_awaiting_
    a_shop — never an ingredient-name match, which would be a second answer
    to a question the ledger already answers exactly.

    With a cook in the horizon and nothing on the list for it, the list is
    stated without a clock ("3 things on the list") and never features, so
    Now's one apricot is not "Open the list".

    With nothing to cook against at all there is no move, exactly as
    before — which is also what keeps the empty moment empty for a
    household with a standing list and no plan.

Every test below says in its own docstring whether it is a CATCH (red
against the code before this change) or a no-regression GUARD. Measured,
not claimed: 14 of the 22 are red against the merge base — but TWO of those
14 are red for a reason other than the one they are named after (one calls
a function that does not exist there at all, one trips over the sibling bug
before it reaches its own claim), and both say so and are pinned by
mutation instead. So 12 are catches. Eight mutations were checked to bite,
one per rule: the household filter, the entry_ids reading, the
nothing-to-cook-against guard, the needed-status filter, the standing-want
trap, the big-meal merge, `timed` in featured_move_id, and the morning
text's own skip.
"""
from __future__ import annotations

import datetime
from datetime import time

from app import tools
from app.db import get_conn
from app.tools import moves as _moves


TODAY = datetime.date.today()
TOMORROW = TODAY + datetime.timedelta(days=1)
ISO_TODAY = TODAY.isoformat()
ISO_TOMORROW = TOMORROW.isoformat()
# Two days back so today is never the plan's first day, matching test_moves.
WEEK_START = (TODAY - datetime.timedelta(days=2)).isoformat()


def _at(hour: int, minute: int = 0, day: datetime.date = TODAY) -> datetime.datetime:
    return datetime.datetime.combine(day, time(hour, minute))


def _household():
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)


def _recipe(name="Garlic Butter Shrimp", item="Shrimp", prep=10, cook=15):
    tools.add_recipe(
        name,
        ingredients=[{"item": item, "qty": "1 lb", "category": "meat-seafood"}],
        prep_time_minutes=prep,
        cook_time_minutes=cook,
        default_servings=2,
    )


def _plan() -> int:
    return tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]


def _unrelated_list():
    """The three things Emily actually had on hers, for nothing that night."""
    tools.add_grocery_item("Whole chicken", quantity="1", category="meat-seafood")
    tools.add_grocery_item("Rice", quantity="1 bag", category="pantry")
    tools.add_grocery_item("Dish soap", quantity="1", category="household")


def _shop(now: datetime.datetime) -> dict | None:
    payload = tools.today_moves(now=now)
    hits = [m for m in payload["moves"] if m["kind"] == "shop"]
    return hits[0] if hits else None


def _entry_id(day: str, slot: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


# ---------- the reported walk ----------

def test_a_dinner_already_in_the_house_gets_no_shop_deadline():
    """
    CATCH. Emily's own walk: tonight's shrimp was thawed and placed by chat,
    so nothing of its was ever put on the list, and the three needed lines
    are for nothing that night. Before this, Now said "Shop for tonight ·
    3 items · by 6:05".
    """
    _household()
    _recipe()
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Garlic Butter Shrimp", slot="dinner", weekly_plan_id=plan_id)
    _unrelated_list()

    shop = _shop(_at(7))

    assert shop is not None, "the list is still worth naming"
    assert shop["title"] != "Shop for tonight"
    assert "by" not in shop["detail"], shop["detail"]
    assert shop["time_label"] == ""
    assert shop.get("timed", True) is False


def test_the_untimed_line_says_what_is_actually_true():
    """CATCH. Un-timed and a plain statement of fact, not a job with a clock
    on it: "3 things on the list", with the stop count beside it only when
    the rows themselves name shops."""
    _household()
    _recipe()
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Garlic Butter Shrimp", slot="dinner", weekly_plan_id=plan_id)
    _unrelated_list()

    shop = _shop(_at(7))

    assert shop["title"] == "3 things on the list"
    assert shop["detail"] == "", "no shops named on the rows yet, so nothing claimed"
    assert shop["overdue"] is False
    assert shop["weight"] == _moves.WEIGHT_LOW, "the quietest thing on the day"


def test_the_untimed_line_names_the_stops_once_the_rows_do():
    """CATCH. The other half of Emily's example wording. Read off the rows'
    own stores only — the Shop tab's stop count has a fallback this one
    deliberately does not copy, so this can be quieter than that tab but
    never louder."""
    _household()
    _recipe()
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Garlic Butter Shrimp", slot="dinner", weekly_plan_id=plan_id)
    _unrelated_list()
    for item in tools.list_grocery_list(status="needed"):
        tools.set_grocery_item_store(
            item["id"], "Costco" if item["item"] != "Dish soap" else "Metro"
        )

    assert _shop(_at(7))["detail"] == "2 stops"


def test_one_thing_and_one_stop_are_singular():
    """CATCH. Plain counting, said the way a person says it."""
    _household()
    _recipe()
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Garlic Butter Shrimp", slot="dinner", weekly_plan_id=plan_id)
    item = tools.add_grocery_item("Dish soap", quantity="1", category="household")["item_id"]
    tools.set_grocery_item_store(item, "Metro")

    shop = _shop(_at(7))
    assert shop["title"] == "1 thing on the list"
    assert shop["detail"] == "1 stop"


def test_the_untimed_line_is_never_the_card_and_never_the_dock():
    """
    CATCH, and the whole of acceptance criterion 3. Now's one apricot is the
    featured move's action (shell.js renderTodayDock), so a shop that can
    never be featured can never make "Open the list" the primary action.
    Before this, the shop move's start-of-day window made it the card all
    morning.
    """
    _household()
    _recipe()
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Garlic Butter Shrimp", slot="dinner", weekly_plan_id=plan_id)
    _unrelated_list()

    payload = tools.today_moves(now=_at(7))

    assert payload["featured"] != f"shop:{ISO_TODAY}"
    # Not merely out-ranked at this hour: it is not a candidate at any hour.
    for hour in range(0, 24):
        assert tools.today_moves(now=_at(hour))["featured"] != f"shop:{ISO_TODAY}"


def test_a_meal_whose_ingredients_are_already_in_the_house_never_asks_for_a_shop():
    """
    CATCH, acceptance criterion 4, from the other end: the shrimp IS tracked
    in the kitchen, so the ingest skips it (recipes._KitchenStock) and no
    line for it ever reaches the list. Nothing that meal is waiting on means
    no deadline it can impose.
    """
    _household()
    _recipe()
    tools.update_inventory("Shrimp", action="add", quantity="2 lbs", location="freezer")
    plan_id = _plan()
    res = tools.plan_meal(ISO_TODAY, "Garlic Butter Shrimp", slot="dinner",
                          weekly_plan_id=plan_id, add_ingredients_to_grocery_list=True)
    assert res["already_have_skipped"], "the kitchen covers it — nothing bought"
    _unrelated_list()

    assert _shop(_at(7)).get("timed", True) is False


# ---------- what must still work ----------

def test_a_cook_the_list_is_waiting_on_still_names_its_deadline():
    """GUARD. The positive case, unchanged: a line this dinner put on the
    list still gives "Shop for tonight · by <the cook's start>", and still
    outranks the dinner it is for."""
    _household()
    _recipe("Sunday Roast", item="Chicken Thighs", prep=20, cook=90)  # 110 min
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Sunday Roast", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    tools.set_dinner_window("5_6ish")  # dinner lands at 5:30

    payload = tools.today_moves(now=_at(12))
    shop = [m for m in payload["moves"] if m["kind"] == "shop"][0]

    assert shop["title"] == "Shop for tonight"
    assert shop["detail"] == "1 item · by 3:40"
    assert shop.get("timed", True) is True
    assert payload["featured"] == f"shop:{ISO_TODAY}"


def test_the_count_is_the_whole_list_even_though_the_deadline_is_one_cooks():
    """
    GUARD. Once there IS a shop to do you are buying the lot, so the count
    stays the whole needed list — only the CLOCK belongs to the cook. This
    is the line between the two halves of the fix and it is easy to break
    by "tidying" the count to the linked rows.
    """
    _household()
    _recipe("Sunday Roast", item="Chicken Thighs", prep=20, cook=90)
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Sunday Roast", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    _unrelated_list()

    shop = _shop(_at(12))
    assert shop["title"] == "Shop for tonight"
    assert shop["detail"].startswith("4 items · by ")


def test_a_standing_list_with_nothing_to_cook_against_is_still_no_move_at_all():
    """
    GUARD, and it matters more than it looks: Now's empty moment and its
    "Let's plan the week" dock both key off there being NO moves at all
    (shell.js todayIsEmpty), so a household with a list and no plan must
    still get a completely empty day rather than a line about its list.
    """
    _household()
    _unrelated_list()
    _plan()

    assert tools.today_moves(now=_at(9))["moves"] == []


def test_an_empty_list_is_no_move_either():
    """GUARD. Nothing to buy, nothing to say."""
    _household()
    _recipe()
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Garlic Butter Shrimp", slot="dinner", weekly_plan_id=plan_id)

    assert _shop(_at(7)) is None


# ---------- which cook the deadline belongs to ----------

def test_the_deadline_follows_the_soonest_cook_the_list_is_waiting_on():
    """
    CATCH. Tonight is already in the house; tomorrow's roast is what the
    chicken is for. The honest deadline is tomorrow's cook, so the move says
    "Shop before tomorrow" — before this it claimed tonight's, which is a
    deadline for a meal that needs nothing.
    """
    _household()
    _recipe()
    _recipe("Sunday Roast", item="Chicken Thighs", prep=20, cook=90)
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Garlic Butter Shrimp", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TOMORROW, "Sunday Roast", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)

    shop = _shop(_at(7))

    assert shop["title"] == "Shop before tomorrow"
    assert shop.get("timed", True) is True
    assert shop["window_end"].startswith(ISO_TOMORROW)


def test_a_cook_already_ticked_off_does_not_claim_the_list():
    """GUARD on an existing skip, worth pinning now that the skip decides
    whether a deadline exists at all: a dinner already cooked has nothing
    left to shop for, so there is no cook to count against and no move —
    the "nothing to cook against" branch, reached from the other side."""
    _household()
    _recipe("Sunday Roast", item="Chicken Thighs")
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Sunday Roast", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    tools.check_off_meal(_entry_id(ISO_TODAY, "dinner"), "done")

    assert _shop(_at(7)) is None


def test_a_cook_beyond_the_horizon_claims_nothing():
    """GUARD. The 36-hour horizon is untouched by this change — a cook three
    days out is not today's shop, however much of the list it is waiting
    on."""
    _household()
    _recipe("Sunday Roast", item="Chicken Thighs")
    plan_id = tools.create_weekly_plan(ISO_TODAY)["weekly_plan_id"]
    far = (TODAY + datetime.timedelta(days=3)).isoformat()
    tools.plan_meal(far, "Sunday Roast", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)

    assert tools.today_moves(now=_at(9))["moves"] == []


# ---------- which lines count as "waiting" ----------

def test_a_line_already_in_the_trolley_is_not_a_reason_to_go_shopping():
    """CATCH. in_cart means the shop is already happening; it is not what
    makes a deadline. The move goes untimed, because the rest of the list
    is for nothing tonight."""
    _household()
    _recipe("Sunday Roast", item="Chicken Thighs")
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Sunday Roast", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    _unrelated_list()
    linked = next(i for i in tools.list_grocery_list(status="needed")
                  if i["item"].lower() == "chicken thighs")
    tools.mark_grocery_item(linked["id"], "in_cart")

    assert _shop(_at(7)).get("timed", True) is False


def test_a_line_set_aside_to_get_elsewhere_is_not_a_reason_either():
    """CATCH. An excluded line is off the list by the household's own say-so
    (grocery.exclude_grocery_item), so it must not be what puts a clock on
    the day."""
    _household()
    _recipe("Sunday Roast", item="Chicken Thighs")
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Sunday Roast", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    _unrelated_list()
    linked = next(i for i in tools.list_grocery_list(status="needed")
                  if i["item"].lower() == "chicken thighs")
    tools.exclude_grocery_item(linked["id"])

    assert _shop(_at(7)).get("timed", True) is False


def test_a_hand_added_line_a_meal_also_contributed_to_does_count():
    """
    GUARD — green against main, which claims a deadline for any list at
    all, so it is pinned by MUTATION: add `AND g.source_weekly_plan_id IS
    NOT NULL` to grocery.entry_ids_awaiting_a_shop and this fails.

    That is the trap in the obvious reading of this rule. A standing want
    keeps source_weekly_plan_id NULL even after a plan's amount merges into
    it (grocery.add_grocery_item's keep_standing), so reading that column as
    "nobody's meal put this here" would miss exactly the lines a cook really
    is waiting on. The ledger is the only thing asked.
    """
    _household()
    _recipe("Sunday Roast", item="Chicken Thighs")
    plan_id = _plan()
    hand = tools.add_grocery_item("Chicken Thighs", quantity="1 lb")["item_id"]
    tools.plan_meal(ISO_TODAY, "Sunday Roast", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)

    row = next(i for i in tools.list_grocery_list(status="needed") if i["id"] == hand)
    conn = get_conn()
    standing = conn.execute(
        "SELECT source_weekly_plan_id FROM grocery_items WHERE id = ?", (hand,)
    ).fetchone()["source_weekly_plan_id"]
    conn.close()
    assert standing is None, "still the household's own line — the premise of this test"
    assert row["item"].lower() == "chicken thighs"

    assert _shop(_at(7))["title"] == "Shop for tonight"


def test_a_pure_standing_want_never_puts_a_clock_on_the_day():
    """CATCH. The mirror of the test above, and the shape of the reported
    bug: a hand-added line no meal ever contributed to has no ledger row at
    all, so it is never what a cook is waiting on."""
    _household()
    _recipe()
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Garlic Butter Shrimp", slot="dinner", weekly_plan_id=plan_id)
    tools.add_grocery_item("Kitchen roll")

    assert _shop(_at(7)).get("timed", True) is False


def test_a_component_plans_merged_card_is_read_by_all_of_its_entries():
    """
    GUARD, pinned by MUTATION. It is red against main, but only with
    `AttributeError: module 'app.tools' has no attribute
    'entry_ids_awaiting_a_shop'` — a harness artifact, not a catch, and it
    never reaches an assertion there. A component-based
    plan's Cook card stands for several entries at once (cooker's
    `entry_ids`), and only one of them may hold the ledger row; reading
    `entry_id` alone drops the deadline. Replace `meal.get("entry_ids") or
    [meal.get("entry_id")]` in moves._shop_move with `[meal.get("entry_id")]`
    and this fails.
    """
    _household()
    _recipe("Sunday Roast", item="Chicken Thighs")
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Sunday Roast", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    waiting = sorted(tools.entry_ids_awaiting_a_shop())
    assert waiting, "the roast really is waiting on the list"

    # The merged card's own shape: a stand-in id first, the real entries in
    # `entry_ids` — exactly what get_cooker_view builds for a component week.
    view = {"meals": [{"date": ISO_TODAY, "slot": "dinner",
                       "entry_id": 999999, "entry_ids": [999999] + waiting}]}
    move = _moves._shop_move(view, TODAY, _at(7), time(18, 30))

    assert move and move[0].get("timed", True) is True


# ---------- the neighbours ----------

def test_the_morning_text_does_not_read_out_an_untimed_list():
    """
    CATCH. digest's own comment promises "the shop, when there is a cook
    close enough for it to matter" — a list nothing today is waiting on is
    not that, and the text is today's moves, not an inventory of the app.
    Before this it texted "Shop for tonight — 3 items · by 6:05".
    """
    _household()
    _recipe()
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Garlic Butter Shrimp", slot="dinner", weekly_plan_id=plan_id)
    _unrelated_list()

    text = tools.build_morning_text(_at(7)) or ""

    assert "Shop" not in text
    assert "on the list" not in text
    assert "Tonight: Garlic Butter Shrimp." in text


def test_the_morning_text_still_reads_out_a_real_shop():
    """GUARD. The half that must not go quiet with it."""
    _household()
    _recipe("Sunday Roast", item="Chicken Thighs", prep=20, cook=90)
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Sunday Roast", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)

    text = tools.build_morning_text(_at(7)) or ""

    assert "Shop for tonight — 1 item, by" in text


def test_a_holidays_own_shop_row_is_not_overwritten_with_a_stop_count():
    """
    CATCH. moves_for_day folds the generic shop's ITEM COUNT onto a big
    meal's named trip (big_meal.SHOP_MARK) so the day never asks for one
    trip twice. The untimed line's detail is a STOP count, so transplanting
    it would put "2 stops" where "3 items · by 6:05" belongs — and with no
    guard it does exactly that.
    """
    _household()
    _recipe()
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Garlic Butter Shrimp", slot="dinner", weekly_plan_id=plan_id)
    _unrelated_list()
    for item in tools.list_grocery_list(status="needed"):
        tools.set_grocery_item_store(
            item["id"], "Costco" if item["item"] != "Dish soap" else "Metro"
        )
    conn = get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
        "related_meal, status, task_type) VALUES (?, ?, ?, ?, 'Shop', 'pending', 'holiday')",
        (tools.household_id(), plan_id, ISO_TODAY, "Big shop for Thanksgiving"),
    )
    conn.commit()
    conn.close()

    shops = [m for m in tools.today_moves(now=_at(7))["moves"] if m["kind"] == "shop"]

    assert len(shops) == 1, "one trip, asked for once"
    assert shops[0]["title"] == "Big shop for Thanksgiving"
    assert shops[0]["detail"] == "shop · by tonight", shops[0]["detail"]


def test_another_households_ledger_cannot_put_a_clock_on_this_days_list():
    """
    GUARD. It IS red against main, and for the wrong reason (main claims a
    deadline for any list at all), so that redness proves nothing about
    isolation — hence the mutation check below, and hence the second
    household getting a real dinner of its own rather than an empty day.

    What the mutation showed, worth writing down rather than implying:
    dropping the household clauses from grocery.entry_ids_awaiting_a_shop
    reddens NOTHING downstream, because meal_plan_entries ids are globally
    unique, so a leaked set can never intersect this household's own meals.
    Isolation on this path is structural. The assertion that does bite is
    the direct one on the read itself, which is why it is here: this log
    records three separate cross-household leaks, and this read decides
    what a household is told to do today.
    """
    _household()
    _recipe()
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Garlic Butter Shrimp", slot="dinner", weekly_plan_id=plan_id)
    _unrelated_list()
    assert _shop(_at(7)).get("timed", True) is False

    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO households (id, name) VALUES (2, 'Next door')")
    conn.commit()
    conn.close()

    # Next door really is waiting on its own list, for its own dinner.
    with tools.use_household(2):
        tools.add_member("Jo")
        _recipe("Sunday Roast", item="Chicken Thighs")
        other_plan = _plan()
        tools.plan_meal(ISO_TODAY, "Sunday Roast", slot="dinner", weekly_plan_id=other_plan,
                        add_ingredients_to_grocery_list=True)
        assert _shop(_at(7)).get("timed", True) is True
        next_doors = tools.entry_ids_awaiting_a_shop()
        assert next_doors, "next door really is waiting on its own list"

    assert _shop(_at(7)).get("timed", True) is False, "and this household's day is untouched"
    assert not (tools.entry_ids_awaiting_a_shop() & next_doors), \
        "next door's ledger is none of this household's business"
