"""
Today's moves timeline — app/tools/moves.py.

Emily's approved Today design (2026-09-08) turns the screen into two blocks:
ONE compact "Next up" card and a plain list of everything else. That only
works if the app can rank a day's cooks, reheats, fridge moves, prep and
shopping against each other, which is what this module does — so the
ranking is the thing worth pinning down, and most of what follows is a
clock and an assertion about which move is the card.

The rule under test, stated once:

    Next up = among the moves that are not done, are not a reheat, and
    whose window is open now or opens within four hours — highest weight
    first, then earliest window_start.

`moves_for_day`/`today_moves` take a `now` for exactly this reason; the UI
never passes one. Everything else here is derivation, not new state: a
move's `done` is read off cooked_status / prep_tasks.status, and ticking one
dispatches back to check_off_meal / check_off_prep_step rather than writing
anywhere of its own.

The last section is source-level rather than behavioural: shell.js has no JS
test harness in this repo (see tests/test_frontend_restored_2026_09_08.py's
docstring), so Today's new structure is guarded by markers.
"""
from __future__ import annotations

import datetime
from datetime import time
from pathlib import Path

import pytest

from app import households, security, tools
from app.db import get_conn


TODAY = datetime.date.today()
YESTERDAY = TODAY - datetime.timedelta(days=1)
TOMORROW = TODAY + datetime.timedelta(days=1)
# Anchored two days back so today is never the plan's first day — the
# breakfast cook-ahead source has to sit on a day inside the same plan.
WEEK_START = (TODAY - datetime.timedelta(days=2)).isoformat()

ISO_TODAY = TODAY.isoformat()
ISO_YESTERDAY = YESTERDAY.isoformat()
ISO_TOMORROW = TOMORROW.isoformat()


def _at(hour: int, minute: int = 0, day: datetime.date = TODAY) -> datetime.datetime:
    return datetime.datetime.combine(day, time(hour, minute))


def _household(*names):
    for n in names or ("Alex", "Sam", "Rae"):
        tools.add_member(n)


def _recipe(name="Chicken Skewers", prep=10, cook=25, servings=3):
    tools.add_recipe(
        name,
        ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
        prep_time_minutes=prep,
        cook_time_minutes=cook,
        default_servings=servings,
    )


def _plan() -> int:
    return tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]


def _fridge_task(plan_id: int, day: str = ISO_TODAY, description: str | None = None) -> int:
    """
    A pending defrost row due `day`. Written directly rather than through
    defrost.sync_defrost_tasks because that one deliberately never schedules
    a move for the cook's own day (defrost._move_date floors at one full
    day of buffer), and a fridge move due TODAY is the case under test.
    """
    description = description or "Move the chicken thighs to the fridge — for Thursday’s skewers."
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
        "related_meal, status, task_type) VALUES (?, ?, ?, ?, ?, 'pending', 'defrost')",
        (tools.household_id(), plan_id, day, description, "Chicken Skewers"),
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def _entry_id(day: str, slot: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


def _by_kind(payload: dict) -> dict:
    return {m["kind"]: m for m in payload["moves"]}


# ---------- the ranking ----------

def test_a_morning_features_the_fridge_move_and_leaves_the_reheat_a_line():
    """
    Seven in the morning, a batch of egg bites made yesterday and a thing to
    take out of the freezer. The fridge move is the card; breakfast is a
    line. Emily, 2026-09-08: made-ahead food "is a line, never the card".
    """
    _household()
    _recipe("Egg White Bites", prep=10, cook=20)
    plan_id = _plan()
    tools.plan_meal(ISO_YESTERDAY, "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TODAY, "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    tools.set_cook_ahead(_entry_id(ISO_YESTERDAY, "breakfast"), [_entry_id(ISO_TODAY, "breakfast")])
    task_id = _fridge_task(plan_id)

    payload = tools.today_moves(now=_at(7))

    assert payload["featured"] == f"fridge:{task_id}"
    reheat = _by_kind(payload)["reheat"]
    assert reheat["id"] != payload["featured"]
    assert "made ahead" in reheat["detail"], reheat["detail"]
    assert "reheat" in reheat["detail"]


def test_a_reheat_is_never_the_card_even_when_it_is_the_only_move_left():
    """The stronger form of the same rule: no card at all beats a card
    promoting a plate that only needs warming up."""
    _household()
    _recipe("Egg White Bites")
    plan_id = _plan()
    tools.plan_meal(ISO_YESTERDAY, "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TODAY, "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    tools.set_cook_ahead(_entry_id(ISO_YESTERDAY, "breakfast"), [_entry_id(ISO_TODAY, "breakfast")])

    payload = tools.today_moves(now=_at(7))

    assert [m["kind"] for m in payload["moves"]] == ["reheat"]
    assert payload["featured"] is None


def test_midday_features_the_lunch_cook_not_the_dinner_four_hours_out():
    _household()
    _recipe("Chicken Skewers", prep=10, cook=25)
    _recipe("Chopped Salad", prep=15, cook=0)
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chopped Salad", slot="lunch", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)

    payload = tools.today_moves(now=_at(12))

    assert payload["featured"] == f"cook:{_entry_id(ISO_TODAY, 'lunch')}"


def test_the_evening_features_dinner_once_lunch_has_gone_by():
    _household()
    _recipe("Chicken Skewers", prep=10, cook=25)
    _recipe("Chopped Salad", prep=15, cook=0)
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chopped Salad", slot="lunch", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)

    payload = tools.today_moves(now=_at(18))

    assert payload["featured"] == f"cook:{_entry_id(ISO_TODAY, 'dinner')}"
    # Lunch's window closed two hours after lunchtime — it is still a line
    # on the list, it is simply no longer what's next.
    assert f"cook:{_entry_id(ISO_TODAY, 'lunch')}" in [m["id"] for m in payload["moves"]]


def test_shopping_outranks_the_dinner_it_is_for_while_the_list_still_has_items():
    """
    Both are high-weight, so the tie-break decides — and the shop's window
    opened at the start of the day, which is deliberately what makes it win:
    cooking a dinner you have not shopped for is not the next move.
    """
    _household()
    _recipe("Chicken Skewers", prep=10, cook=25)
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    tools.add_grocery_item("Chicken Thighs", quantity="1 lb")

    payload = tools.today_moves(now=_at(18))
    assert payload["featured"] == f"shop:{ISO_TODAY}"
    assert _by_kind(payload)["shop"]["title"] == "Shop for tonight"

    # Shopped. The move stops existing rather than being marked done — see
    # moves._shop_move — and the dinner is what's next again.
    for item in tools.list_grocery_list(status="needed"):
        tools.mark_grocery_item(item["id"], "purchased")

    payload = tools.today_moves(now=_at(18))
    assert "shop" not in _by_kind(payload)
    assert payload["featured"] == f"cook:{_entry_id(ISO_TODAY, 'dinner')}"


def test_a_shop_with_no_cook_close_enough_is_not_a_move():
    """A standing list with nothing to cook against is not today's problem."""
    _household()
    tools.add_grocery_item("Kitchen Roll")
    _plan()

    assert _by_kind(tools.today_moves(now=_at(9))) == {}


def test_nothing_left_today_features_nothing_and_names_tomorrow():
    _household()
    _recipe("Chicken Skewers")
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TOMORROW, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    tools.check_off_meal(_entry_id(ISO_TODAY, "dinner"), "done")

    payload = tools.today_moves(now=_at(19))

    assert payload["featured"] is None
    assert payload["done"] == 1 and payload["total"] == 1
    assert payload["tomorrow"] is not None
    assert payload["tomorrow"]["title"] == "Chicken Skewers"
    assert payload["tomorrow"]["date"] == ISO_TOMORROW


def test_a_cook_more_than_four_hours_out_is_not_yet_next_up():
    _household()
    _recipe("Chicken Skewers", prep=10, cook=25)
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)

    assert tools.today_moves(now=_at(9))["featured"] is None
    assert tools.today_moves(now=_at(15))["featured"] == f"cook:{_entry_id(ISO_TODAY, 'dinner')}"


# ---------- windows ----------

def test_a_cooks_window_opens_a_recipes_length_before_the_meal():
    _household()
    _recipe("Chicken Skewers", prep=10, cook=25)
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)

    cook = _by_kind(tools.today_moves(now=_at(12)))["cook"]

    assert cook["duration_min"] == 35
    assert cook["window_start"] == _at(17, 55).isoformat()   # 18:30 − 35 min
    assert cook["window_end"] == _at(20, 30).isoformat()     # 18:30 + 2h
    assert cook["chips"] == ["35 min", "Start by 5:55"]
    assert cook["time_label"] == "6:30 tonight"


@pytest.mark.parametrize("window,hour,minute,label", [
    ("5_6ish", 17, 30, "5:30 tonight"),
    ("6_8", 19, 0, "7:00 tonight"),
    ("later", 20, 0, "8:00 tonight"),
    # No honest clock behind 'all_over' — the default stands rather than a
    # made-up household fact.
    ("all_over", 18, 30, "6:30 tonight"),
])
def test_dinners_hour_comes_from_the_households_rhythm(window, hour, minute, label):
    _household()
    _recipe("Chicken Skewers", prep=0, cook=0)
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    tools.set_dinner_window(window)

    cook = _by_kind(tools.today_moves(now=_at(12)))["cook"]

    assert cook["window_start"] == _at(hour, minute).isoformat()
    assert cook["time_label"] == label


def test_a_fridge_move_at_07_00_says_by_tonight():
    """
    Before the household's ordinary evening hour, the window is fully open
    and forward-looking — never a specific dinner time the fridge move
    doesn't actually mean (it's for a LATER day's meal; see
    defrost._move_date). window_end itself is 22:00, not dinner.
    """
    _household()
    plan_id = _plan()
    _fridge_task(plan_id)
    tools.set_dinner_window("5_6ish")

    fridge = _by_kind(tools.today_moves(now=_at(7)))["fridge"]

    assert fridge["window_start"] == _at(0, 0).isoformat()
    assert fridge["window_end"] == _at(22, 0).isoformat()
    assert fridge["time_label"] == "by tonight"
    assert fridge["overdue"] is False
    assert fridge["tickable"] is True
    # The sentence splits into a title and the one italic line under it.
    assert fridge["title"] == "Move the chicken thighs to the fridge"
    assert fridge["reason"] == "for Thursday’s skewers"


def test_an_undone_fridge_move_is_still_featured_and_overdue_once_evening_has_come():
    """
    The bug: at 19:30 the old dinner-clock deadline (6:30) had already
    passed, so an undone fridge move failed featured_move_id's
    window_end >= now test and dropped out of the candidate set entirely —
    the evening card could never say "take tomorrow's chicken out". It now
    stays the card, flagged overdue, with "still to do" copy instead of a
    dinner time it never meant.
    """
    _household()
    plan_id = _plan()
    task_id = _fridge_task(plan_id)

    payload = tools.today_moves(now=_at(19, 30))

    assert payload["featured"] == f"fridge:{task_id}"
    fridge = _by_kind(payload)["fridge"]
    assert fridge["overdue"] is True
    assert fridge["time_label"] == "still to do"
    # The hard end of the day, not tonight's dinner — still hours away.
    assert fridge["window_end"] == _at(22, 0).isoformat()


def test_general_prep_is_a_move_too_and_is_not_a_fridge_move():
    _household()
    plan_id = _plan()
    tools.save_prep_tasks(plan_id, [{"task_date": ISO_TODAY, "description": "Marinate the beef"}])

    prep = _by_kind(tools.today_moves(now=_at(9)))["prep"]

    assert prep["title"] == "Marinate the beef"
    assert prep["detail"].startswith("prep · by ")
    assert prep["weight"] == tools.moves_for_day.__globals__["WEIGHT_MEDIUM"]


def test_moves_come_back_sorted_by_window_start():
    _household()
    _recipe("Chicken Skewers", prep=10, cook=25)
    _recipe("Chopped Salad", prep=15, cook=0)
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TODAY, "Chopped Salad", slot="lunch", weekly_plan_id=plan_id)
    _fridge_task(plan_id)

    moves = tools.moves_for_day(ISO_TODAY, now=_at(9))

    assert [m["kind"] for m in moves] == ["fridge", "cook", "cook"]
    assert [m["window_start"] for m in moves] == sorted(m["window_start"] for m in moves)


# ---------- done, derived ----------

def test_done_is_read_off_the_rows_that_already_own_it():
    _household()
    _recipe("Chicken Skewers")
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    task_id = _fridge_task(plan_id)

    assert tools.today_moves(now=_at(18))["done"] == 0

    tools.check_off_meal(_entry_id(ISO_TODAY, "dinner"), "done")
    tools.check_off_prep_step(task_id, "done")

    payload = tools.today_moves(now=_at(18))
    assert payload["done"] == 2 and payload["total"] == 2
    assert all(m["done"] for m in payload["moves"])


def test_a_skipped_fridge_move_counts_as_handled_not_as_still_waiting():
    _household()
    plan_id = _plan()
    task_id = _fridge_task(plan_id)
    tools.check_off_prep_step(task_id, "skipped")

    assert _by_kind(tools.today_moves(now=_at(9)))["fridge"]["done"] is True


def test_set_move_done_dispatches_to_the_tool_that_owns_the_state():
    _household()
    _recipe("Chicken Skewers")
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    entry_id = _entry_id(ISO_TODAY, "dinner")
    task_id = _fridge_task(plan_id)

    assert tools.set_move_done(f"cook:{entry_id}", True)["dispatched_to"] == "check_off_meal"
    assert tools.set_move_done(f"fridge:{task_id}", True)["dispatched_to"] == "check_off_prep_step"
    # Shopping is done when the LIST says so; a flag here would make Today
    # disagree with Grocery.
    assert tools.set_move_done(f"shop:{ISO_TODAY}", True)["dispatched_to"] is None

    conn = get_conn()
    assert conn.execute("SELECT cooked_status FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()[0] == "done"
    assert conn.execute("SELECT status FROM prep_tasks WHERE id = ?", (task_id,)).fetchone()[0] == "done"
    conn.close()

    tools.set_move_done(f"cook:{entry_id}", False)
    conn = get_conn()
    assert conn.execute("SELECT cooked_status FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()[0] == "pending"
    conn.close()

    with pytest.raises(ValueError):
        tools.set_move_done("nonsense:1", True)


def test_a_shop_move_is_not_tickable_and_ticking_it_is_a_no_op():
    """
    The bug: every move used to render a tick, including shop's — but
    set_move_done's "shop" branch dispatches to nothing (there is no flag
    behind a standing grocery list to flip), so the tick filled in and then
    silently snapped back, with a toast that lied about what happened.
    `tickable` says so up front, and the dispatch really is a no-op.
    """
    _household()
    _recipe("Chicken Skewers")
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    tools.add_grocery_item("Chicken Thighs", quantity="1 lb")

    shop = _by_kind(tools.today_moves(now=_at(18)))["shop"]
    assert shop["tickable"] is False

    result = tools.set_move_done(shop["id"], True)
    assert result["dispatched_to"] is None
    assert result["done"] is False
    # Nothing about the shop move changed — it's derived from the list, not
    # from a flag this call could have flipped.
    after = _by_kind(tools.today_moves(now=_at(18)))["shop"]
    assert after["done"] is False


# ---------- the routes ----------

def test_the_endpoints_answer_and_the_tick_round_trips(signed_in):
    _household()
    _recipe("Chicken Skewers")
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    entry_id = _entry_id(ISO_TODAY, "dinner")

    payload = signed_in.get("/api/today/moves").json()
    assert [m["id"] for m in payload["moves"]] == [f"cook:{entry_id}"]
    assert payload["week_state"] == "draft"

    res = signed_in.post(f"/api/today/moves/cook:{entry_id}/done", json={"done": True})
    assert res.status_code == 200
    assert res.json()["moves"][0]["done"] is True

    res = signed_in.post(f"/api/today/moves/cook:{entry_id}/done", json={"done": False})
    assert res.json()["moves"][0]["done"] is False


def test_posting_done_with_a_date_renders_that_days_moves(signed_in):
    """
    The GET has always taken ?date=; the POST used to ignore it entirely and
    always hand back today's timeline — so ticking a move from a non-today
    row (tomorrow's card, say) rendered the wrong day back. Accepts the date
    as a query param or in the body; either should work.
    """
    _household()
    _recipe("Chicken Skewers")
    plan_id = _plan()
    tools.plan_meal(ISO_TOMORROW, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    entry_id = _entry_id(ISO_TOMORROW, "dinner")

    res = signed_in.post(f"/api/today/moves/cook:{entry_id}/done?date={ISO_TOMORROW}", json={"done": True})
    assert res.status_code == 200
    body = res.json()
    assert body["date"] == ISO_TOMORROW
    assert body["moves"][0]["id"] == f"cook:{entry_id}"
    assert body["moves"][0]["done"] is True

    # Same thing, date in the body instead of the query string.
    res = signed_in.post(f"/api/today/moves/cook:{entry_id}/done", json={"done": False, "date": ISO_TOMORROW})
    assert res.json()["date"] == ISO_TOMORROW
    assert res.json()["moves"][0]["done"] is False


def test_an_unknown_move_is_a_404_not_a_500(signed_in):
    assert signed_in.post("/api/today/moves/nonsense:1/done", json={"done": True}).status_code == 404


def test_the_week_state_badge_reads_the_plans_own_status(signed_in):
    assert signed_in.get("/api/today/moves").json()["week_state"] == "none"
    _household()
    _recipe("Chicken Skewers")
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    assert signed_in.get("/api/today/moves").json()["week_state"] == "draft"
    tools.approve_weekly_plan(plan_id)
    assert signed_in.get("/api/today/moves").json()["week_state"] == "set"


# ---------- household scoping ----------

def test_one_households_moves_are_never_another_households(client):
    """
    Through the real HTTP stack with real cookies, the way
    tests/test_multi_household.py does it — a `with use_household(...)`
    assertion would pass just as happily against a broken server.
    """
    beta_id = households.create_household("The Beta Testers", "beta-tester-passphrase")

    _household()
    _recipe("Chicken Skewers")
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    emily_entry = _entry_id(ISO_TODAY, "dinner")

    with tools.use_household(beta_id):
        tools.add_member("Robin")
        tools.add_recipe("Miso Soup", ingredients=[{"item": "miso", "qty": "2 tbsp"}], prep_time_minutes=5, cook_time_minutes=10)
        beta_plan = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
        tools.plan_meal(ISO_TODAY, "Miso Soup", slot="dinner", weekly_plan_id=beta_plan)

    client.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    emily_moves = client.get("/api/today/moves").json()["moves"]
    assert [m["title"] for m in emily_moves] == ["Chicken Skewers"]

    client.post("/login", data={"password": "beta-tester-passphrase", "next": "/"}, follow_redirects=False)
    beta_moves = client.get("/api/today/moves").json()["moves"]
    assert [m["title"] for m in beta_moves] == ["Miso Soup"]

    # And one household cannot tick the other's move: the id names a row
    # that its own household_id-scoped write will never find.
    res = client.post(f"/api/today/moves/cook:{emily_entry}/done", json={"done": True})
    assert res.status_code == 404
    conn = get_conn()
    assert conn.execute(
        "SELECT cooked_status FROM meal_plan_entries WHERE id = ?", (emily_entry,)
    ).fetchone()[0] == "pending"
    conn.close()
    assert security  # imported for the cookie name the client fixture sets


# ---------- Today's structure (source markers) ----------
# shell.js has no JS test harness in this repo, so these guard the SHAPE of
# the rebuilt screen: the two blocks, the ticks, and the things Emily's
# design removed. A marker that is present but mis-wired is still a far
# better failure mode than a marker that is gone.

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


@pytest.mark.parametrize("marker", [
    "id=\"today-next-up\"",          # the Next up card
    "function nextUpCardHtml(",
    "NEXT UP",                        # its eyebrow, verbatim
    "id=\"today-rest\"",             # the rest-of-today list
    "The rest of today",
    "Done today",                     # the done group
    "function moveRowHtml(",
    "data-move-tick",                 # the tick control
    "function toggleTodayMove(",
    "/api/today/moves",               # one fetch, not four
    "id=\"today-week-state\"",       # WEEK SET / DRAFT / NOTHING PLANNED
    "' of ' + moves.length + ' done'",
])
def test_today_renders_the_two_blocks_and_their_ticks(marker):
    assert marker in SHELL_JS, f"Today is missing {marker!r} from static/shell.js"


@pytest.mark.parametrize("style", [".tick", ".tick.is-done", ".nextup-hero", ".rest-row", ".tomorrow-card"])
def test_today_carries_the_styles_for_them(style):
    assert style in SHELL_CSS, f"static/shell.css is missing {style}"


def test_move_tick_html_is_guarded_by_tickable():
    """
    Source-level guard for the shop-tick bug: moveTickHtml has to check
    move.tickable and render nothing (a same-size, non-interactive spacer)
    rather than a live tick for a move whose done dispatch is a no-op — see
    moves.py's `tickable` field and set_move_done's "shop" branch.
    """
    body = SHELL_JS[SHELL_JS.index("function moveTickHtml("):SHELL_JS.index("function nextUpCardHtml(")]
    assert "move.tickable" in body, "moveTickHtml doesn't consult move.tickable"
    assert "data-move-tick" not in body.split("if (!move.tickable)")[0], (
        "the tickable guard must come before the live tick markup, not after it"
    )


def test_the_bell_is_gone_from_the_ui_but_not_from_the_codebase():
    """
    Emily, 2026-09-08: the notifications feed leaves Today. Everything
    time-bound it carried is a move now, and a second inbox beside the
    timeline is what the redesign existed to remove. Gated behind one
    constant rather than deleted, the same shape as SHOW_CHORES_ON_TODAY —
    the routes and the panel code stay, so this is reversible in one line.
    """
    assert "var SHOW_NOTIF_BELL = false;" in SHELL_JS
    assert "if (!SHOW_NOTIF_BELL) return;" in SHELL_JS, "loadNotifications should not fetch a feed nobody can open"
    # The backend is untouched — the plan-week nudge still dismisses through it.
    assert "/api/notifications/dismiss" in SHELL_JS


@pytest.mark.parametrize("gone", [
    "today-dinner-card",     # the old tall dinner hero
    "today-prep-tile",       # the "Before bed" prep tile
    "today-defrost-tile",    # the standalone defrost tile
    "grocery-summary-open",  # the grocery-count tile
    "loadTonightsDinner",
])
def test_the_cards_the_redesign_replaced_are_actually_gone(gone):
    assert gone not in SHELL_JS, (
        f"{gone!r} is back in static/shell.js. Today is a moves timeline now "
        "(app/tools/moves.py) — everything that card showed is a move on it."
    )


def test_today_still_has_no_day_rail_and_keeps_the_open_dinner_card():
    """
    Two halves of the same design note. The day rail the mockup asked to
    remove from Today was never on Today (it belongs to Meals, where it
    stays) — this pins that it never arrives. And the open-dinner needs-you
    card is the one card allowed to stand in for Next up.
    """
    today_panel = SHELL_JS[SHELL_JS.index("async function buildTodayPanel("):SHELL_JS.index("// ---------- The offer to plan a week")]
    assert "day-rail" not in today_panel
    assert "function renderNeedsYou(" in SHELL_JS
    assert "panel._openDinnerCard" in SHELL_JS


def test_chores_stay_gated_off_rather_than_being_swept_away():
    assert "var SHOW_CHORES_ON_TODAY = false;" in SHELL_JS
    assert "function renderChores(" in SHELL_JS
