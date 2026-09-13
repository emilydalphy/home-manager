"""
Cook: the real start time moves the clock (and says so once) — Loop Board,
Emily 2026-09-13: "It's good to set the planned start time, but if the user
ends up starting at a different time it should auto connect to whatever
time it is for them and update the done time accordingly too. And it can
make a little pop up note that it adjusted for actual timing."

Until this every clock in the app was PLANNED only: moves.py worked the
start back from the dinner hour, the Meal step's stops did the same
arithmetic in shell.js, and "Start cooking" wrote nothing down. Now the tap
records the real start on the entry (meal_plan_entries.cook_started_at,
household local), POST /api/cooker/start answers with the refreshed view
and a receipt, and every reader of a start or an on-the-table time rebases
from it — falling back to the plan when there is nothing there.

Two kinds of test, the split every cook file uses:

  * BEHAVIOUR for the backend — what start_cooking writes and on whose
    clock, that a second tap keeps the first time, that another
    household's entry is a 404 and not a start, what the cooker view and
    Now's moves say before and after, and that "Mark not cooked" forgets
    the start.
  * The shell's own functions under node (tests/nodeharness.py) — the
    stops rebased from the real start, the Meal step's hero and dock,
    Cook's Tonight card and hero, and the "Start cooking" handler itself:
    one pop-up when the plan was two minutes or more off, none inside two
    minutes, none the second time, a calm line on failure and nothing
    else.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from datetime import time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import nodeharness
from app import households, tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools._shared import use_household

from test_cook_journey import _STUBS as _COOK_STUBS, _extract as _extract_async, _var_block
from test_cook_shelf import _MOVE, _meal, _node as _shelf_node, MON as SHELF_MON
from test_meal_clock import _COOK_CARD, _DINNER, _ESCAPE, _PURE, _extract, _monday, _var

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute shell.js's own functions"
)

TODAY = datetime.date.today()
ISO_TODAY = TODAY.isoformat()
WEEK_START = (TODAY - datetime.timedelta(days=2)).isoformat()


def _at(hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(TODAY, time(hour, minute))


def _utc(hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(TODAY, time(hour, minute), tzinfo=timezone.utc)


def _set_timezone(name: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE households SET timezone = ? WHERE id = ?", (name, tools.household_id()))
    conn.commit()
    conn.close()


@pytest.fixture
def tonight() -> int:
    """
    A plan with one real dinner tonight (10 + 25 minutes) and the household
    on a UTC clock, so a stamp at 18:02Z reads "18:02" and the arithmetic
    in every assertion is the one on the page. Returns the entry id.
    """
    tools.add_member("Emily")
    tools.add_recipe(
        "Chicken Skewers",
        ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
        prep_time_minutes=10, cook_time_minutes=25, default_servings=2,
    )
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    _set_timezone("UTC")
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = 'dinner'",
        (tools.household_id(), ISO_TODAY),
    ).fetchone()
    conn.close()
    return row["id"]


def _started_at(entry_id: int):
    conn = get_conn()
    row = conn.execute("SELECT cook_started_at FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return row["cook_started_at"]


def _cook_move(now):
    return {m["kind"]: m for m in tools.today_moves(now=now)["moves"]}["cook"]


# ---------- the write ----------


def test_the_column_is_in_the_schema_and_the_migration():
    schema = (REPO / "app" / "schema.sql").read_text(encoding="utf-8")
    db = (REPO / "app" / "db.py").read_text(encoding="utf-8")
    assert "cook_started_at TEXT," in schema
    assert '("meal_plan_entries", "cook_started_at", "TEXT")' in db


def test_start_cooking_writes_the_real_start_and_answers_with_the_receipt(tonight):
    out = tools.start_cooking(tonight, now_utc=_utc(18, 2))
    assert out["started_at"] == f"{ISO_TODAY}T18:02:00"
    assert _started_at(tonight) == f"{ISO_TODAY}T18:02:00"
    # started + the meal's 35 minutes.
    assert out["on_the_table"] == f"{ISO_TODAY}T18:37:00"
    # What the plan said: dinner at 6:30, minus 35.
    assert out["planned_start"] == f"{ISO_TODAY}T17:55:00"
    assert out["already_started"] is False
    assert out["entry_id"] == tonight
    # The refreshed view rides along, the way every /api/cooker/* write's does.
    assert [m["entry_id"] for m in out["meals"]] == [tonight]
    assert out["meals"][0]["cook_started_at"] == f"{ISO_TODAY}T18:02:00"


def test_the_start_is_on_the_households_clock_not_the_servers():
    """
    The deployed container runs in UTC; the household's dinner is in
    Toronto. Expected values come from ZoneInfo rather than being typed,
    so this holds whichever side of a clock change it runs on.
    """
    tools.add_recipe("Chili", ingredients=[{"item": "Beans", "qty": "1 can"}],
                     prep_time_minutes=5, cook_time_minutes=20)
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(ISO_TODAY, "Chili", slot="dinner", weekly_plan_id=plan_id)
    entry_id = {m["meal"]: m["entry_id"] for m in tools.get_cooker_view()["meals"]}["Chili"]
    for zone in ("America/Toronto", "America/Vancouver"):
        _set_timezone(zone)
        conn = get_conn()
        conn.execute("UPDATE meal_plan_entries SET cook_started_at = NULL WHERE id = ?", (entry_id,))
        conn.commit()
        conn.close()
        instant = _utc(22, 2)
        out = tools.start_cooking(entry_id, now_utc=instant)
        local = instant.astimezone(ZoneInfo(zone)).replace(tzinfo=None)
        assert out["started_at"] == local.isoformat(timespec="seconds"), zone
        assert out["on_the_table"] == (local + datetime.timedelta(minutes=25)).isoformat(timespec="seconds")


def test_a_zone_name_nobody_can_read_falls_back_to_toronto(tonight):
    _set_timezone("Mars/Olympus_Mons")
    instant = _utc(22, 2)
    out = tools.start_cooking(tonight, now_utc=instant)
    local = instant.astimezone(ZoneInfo("America/Toronto")).replace(tzinfo=None)
    assert out["started_at"] == local.isoformat(timespec="seconds")


def test_a_second_tap_keeps_the_first_time(tonight):
    first = tools.start_cooking(tonight, now_utc=_utc(18, 2))
    second = tools.start_cooking(tonight, now_utc=_utc(18, 40))
    assert second["started_at"] == first["started_at"] == f"{ISO_TODAY}T18:02:00"
    assert second["on_the_table"] == first["on_the_table"]
    assert second["already_started"] is True
    assert _started_at(tonight) == f"{ISO_TODAY}T18:02:00"


def test_another_households_meal_cannot_be_started(tonight):
    with use_household(2):
        with pytest.raises(ValueError, match="No meal"):
            tools.start_cooking(tonight, now_utc=_utc(18, 2))
    assert _started_at(tonight) is None


def test_a_meal_with_no_minutes_has_a_start_but_no_table_time():
    tools.add_recipe("Takeaway", ingredients=[{"item": "Menu", "qty": "1"}])
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(ISO_TODAY, "Takeaway", slot="dinner", weekly_plan_id=plan_id)
    _set_timezone("UTC")
    entry_id = tools.get_cooker_view()["meals"][0]["entry_id"]
    out = tools.start_cooking(entry_id, now_utc=_utc(18, 2))
    assert out["started_at"] == f"{ISO_TODAY}T18:02:00"
    assert out["on_the_table"] is None
    assert out["planned_start"] is None


# ---------- one total, every clock ----------


def test_cook_total_minutes_is_the_recipe_or_the_longest_side():
    assert _cooker.cook_total_minutes({"prep_time_minutes": 10, "cook_time_minutes": 25}) == 35
    # Twenty-five-minute potatoes beside a fifteen-minute stir-fry: the
    # potatoes set the clock, exactly as shell.js's mealClockTotal says.
    assert _cooker.cook_total_minutes({"prep_time_minutes": 5, "cook_time_minutes": 10,
                                       "sides": [{"name": "Roasted potatoes", "minutes": 25, "instructions": ["Roast."]}]}) == 25
    assert _cooker.cook_total_minutes({"prep_time_minutes": 5, "cook_time_minutes": 30,
                                       "sides": [{"name": "Green salad", "minutes": 5, "instructions": ["Toss."]}]}) == 35
    # A side with minutes but no steps is nothing on the clock — the Meal
    # step's stops skip it (mealClockSides), so the total does too.
    assert _cooker.cook_total_minutes({"prep_time_minutes": 5, "cook_time_minutes": 10,
                                       "sides": [{"name": "Rice", "minutes": 25}]}) == 15
    assert _cooker.cook_total_minutes({"prep_time_minutes": None, "cook_time_minutes": None}) is None
    assert _cooker.cook_total_minutes({"sides": [{"name": "Salad", "minutes": "nope"}]}) is None
    assert _cooker.cook_total_minutes(None) is None


def test_the_shell_and_the_server_add_up_the_same_total():
    """The rule is written once in each language; this pins that the two
    say the same thing about the same card."""
    if shutil.which("node") is None:
        pytest.skip("node is needed to execute shell.js's own functions")
    card = {"prep_time_minutes": 5, "cook_time_minutes": 10,
            "sides": [{"name": "Roasted potatoes", "minutes": 25, "instructions": ["Roast."]}]}
    res = nodeharness.run_node(_PURE + f"console.log(JSON.stringify(mealClockTotal({json.dumps(card)})));\n")
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout.strip()) == _cooker.cook_total_minutes(card) == 25


# ---------- the readers ----------


def test_the_cooker_view_carries_the_start_and_nothing_until_then(tonight):
    assert tools.get_cooker_view()["meals"][0]["cook_started_at"] is None
    tools.start_cooking(tonight, now_utc=_utc(18, 2))
    assert tools.get_cooker_view()["meals"][0]["cook_started_at"] == f"{ISO_TODAY}T18:02:00"


def test_nows_cook_move_reads_the_plan_until_the_cook_begins(tonight):
    cook = _cook_move(_at(17))
    assert cook["chips"] == ["35 min", "Start by 5:55"]
    assert cook["window_start"] == _at(17, 55).isoformat()
    assert cook["time_label"] == "6:30 tonight"
    assert cook["started_at"] is None
    assert cook["planned_start"] == _at(17, 55).isoformat()


def test_nows_cook_move_moves_with_the_real_start(tonight):
    tools.start_cooking(tonight, now_utc=_utc(18, 2))
    cook = _cook_move(_at(18, 10))
    # "Started 6:02" where "Start by 5:55" was; the table time follows.
    assert cook["chips"] == ["35 min", "Started 6:02"]
    assert cook["window_start"] == _at(18, 2).isoformat()
    assert cook["time_label"] == "6:37 tonight"
    assert cook["detail"] == "dinner · 35 min · 6:37"
    # Still tonight's for two hours after it actually lands.
    assert cook["window_end"] == _at(20, 37).isoformat()
    assert cook["started_at"] == _at(18, 2).isoformat()
    assert cook["planned_start"] == _at(17, 55).isoformat()
    assert cook["duration_min"] == 35
    # The same cook, the same card: nothing else about the move moved.
    assert cook["id"] == f"cook:{tonight}"
    assert cook["done"] is False and cook["tickable"] is True


def test_a_cook_begun_early_lands_early_and_the_window_still_reaches_dinner(tonight):
    tools.start_cooking(tonight, now_utc=_utc(17, 30))
    cook = _cook_move(_at(17, 40))
    assert cook["chips"] == ["35 min", "Started 5:30"]
    assert cook["time_label"] == "6:05 tonight"
    # Two hours past DINNER, not past 6:05 — the later of the two clocks.
    assert cook["window_end"] == _at(20, 30).isoformat()


def test_mark_not_cooked_forgets_the_start_and_every_reader_falls_back(tonight):
    tools.start_cooking(tonight, now_utc=_utc(18, 2))
    # Marking it cooked keeps the start — it is a true fact about the cook.
    tools.check_off_meal(tonight, "done")
    assert _started_at(tonight) == f"{ISO_TODAY}T18:02:00"
    # Putting it back to not-cooked forgets it.
    tools.check_off_meal(tonight, "pending")
    assert _started_at(tonight) is None
    assert tools.get_cooker_view()["meals"][0]["cook_started_at"] is None
    cook = _cook_move(_at(17))
    assert cook["chips"] == ["35 min", "Start by 5:55"]
    assert cook["started_at"] is None
    # ...and the next "Start cooking" is a fresh first time.
    out = tools.start_cooking(tonight, now_utc=_utc(18, 20))
    assert out["started_at"] == f"{ISO_TODAY}T18:20:00"
    assert out["already_started"] is False


# ---------- the route ----------


def test_the_route_answers_with_the_view_and_the_receipt(signed_in, tonight):
    res = signed_in.post("/api/cooker/start", json={"entry_id": tonight})
    assert res.status_code == 200
    body = res.json()
    assert body["already_started"] is False
    assert body["started_at"] and body["started_at"].startswith(ISO_TODAY + "T")
    assert body["on_the_table"] and body["on_the_table"].startswith(ISO_TODAY + "T")
    assert body["meals"][0]["cook_started_at"] == body["started_at"]
    assert _started_at(tonight) == body["started_at"]
    # The second tap: the same time, and the shell is told it is a repeat.
    again = signed_in.post("/api/cooker/start", json={"entry_id": tonight}).json()
    assert again["started_at"] == body["started_at"]
    assert again["already_started"] is True


def test_the_route_404s_on_a_meal_that_is_not_this_households(signed_in, tonight):
    assert signed_in.post("/api/cooker/start", json={"entry_id": 99999}).status_code == 404
    other = households.create_household("The Beta Testers", "a-distinct-beta-passphrase")
    with use_household(other):
        tools.add_recipe("Theirs", ingredients=[{"item": "Rice", "qty": "1 cup"}])
        plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
        tools.plan_meal(ISO_TODAY, "Theirs", slot="dinner", weekly_plan_id=plan_id)
        theirs = tools.get_cooker_view()["meals"][0]["entry_id"]
    res = signed_in.post("/api/cooker/start", json={"entry_id": theirs})
    assert res.status_code == 404
    assert _started_at(theirs) is None


# ---------- the Meal step's clock, under node ----------


def _live_helpers() -> str:
    """The real-start readers, lifted from shell.js."""
    return (
        _var("START_SLACK_MINUTES") + "\n"
        + "".join(_extract(n) + "\n" for n in (
            "isoClockMinutes", "cookStartedMinutes", "cookRealClock", "cookStartOffsetLabel"))
    )


def _run(expr: str):
    res = nodeharness.run_node(_PURE + _live_helpers() + f"console.log(JSON.stringify({expr}));\n", timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_the_stops_are_rebased_from_the_real_start():
    """
    Thirty minutes planned for half six starts at six; begun at 6:02 the
    stops run from 6:02 to 6:32 — "Everything out" at the minute it really
    happened, not the nearest five, the last stop on the new table time.
    """
    stops = _run(f"mealClockStops({json.dumps(_COOK_CARD)}, {{ tableMinutes: 18 * 60 + 30, startMinutes: 18 * 60 + 2 }})")
    assert [s["title"] for s in stops][:2] == ["Everything out", "Heat the oil"]
    assert stops[0]["time"] == "6:02"
    assert stops[0]["minutes"] == 18 * 60 + 2
    assert stops[-1]["time"] == "6:32"
    assert all(s["minutes"] >= 18 * 60 + 2 for s in stops)
    assert [s["minutes"] for s in stops] == sorted(s["minutes"] for s in stops)
    # Without a real start the same card keeps the plan's clock exactly.
    planned = _run(f"mealClockStops({json.dumps(_COOK_CARD)}, {{ tableMinutes: 18 * 60 + 30 }})")
    assert [s["time"] for s in planned] == ["6:00", "6:05", "6:10", "6:20", "6:25", "6:30"]


@_needs_node
def test_a_side_keeps_its_own_timing_off_the_new_table():
    card = dict(_COOK_CARD, sides=[{"name": "Roasted potatoes", "minutes": 25,
                                    "instructions": ["Halve the potatoes.", "Into the oven."]}],
                instructions=_COOK_CARD["instructions"] + [
                    "Alongside — Roasted potatoes: Halve the potatoes.", "Alongside: Into the oven."])
    stops = _run(f"mealClockStops({json.dumps(card)}, {{ tableMinutes: 18 * 60 + 30, startMinutes: 18 * 60 + 2 }})")
    sides = [s for s in stops if s["kind"] == "side"]
    # The side's last step is at table − 25 = 6:07, rounded to the grid.
    assert [s["time"] for s in sides] == ["6:02", "6:05"]
    assert stops[-1]["time"] == "6:32"


@_needs_node
def test_the_real_start_readers():
    out = _run("[isoClockMinutes('2026-09-13T18:02:00'), isoClockMinutes(null), isoClockMinutes('nope'),"
               " cookStartedMinutes({ cook_started_at: '2026-09-13T06:15:00' }), cookStartedMinutes({}),"
               " cookRealClock({ cook_started_at: '2026-09-13T18:02:00', prep_time_minutes: 10, cook_time_minutes: 25 }),"
               " cookRealClock({ cook_started_at: '2026-09-13T18:02:00' }), cookRealClock({ prep_time_minutes: 10 }),"
               " cookStartOffsetLabel(18 * 60 + 2, 17 * 60 + 45), cookStartOffsetLabel(17 * 60 + 40, 18 * 60),"
               " cookStartOffsetLabel(18 * 60 + 1, 18 * 60), cookStartOffsetLabel(18 * 60 + 2, 18 * 60),"
               " cookStartOffsetLabel(18 * 60 + 1, 18 * 60 + 2), cookStartOffsetLabel(19 * 60 + 10, 18 * 60),"
               " cookStartOffsetLabel(null, 18 * 60), cookStartOffsetLabel(18 * 60, null)]")
    assert out == [18 * 60 + 2, None, None, 6 * 60 + 15, None,
                   {"start": 18 * 60 + 2, "table": 18 * 60 + 37},
                   {"start": 18 * 60 + 2, "table": None}, None,
                   "17 minutes late", "20 minutes early", "", "2 minutes late", "", "an hour and ten minutes late",
                   "", ""]


def _screen(day: dict, slot: str, cook_meals: list, ticked: list | None = None) -> str:
    """tests/test_meal_clock.py's screen harness, with the real-start readers in scope."""
    harness = (
        _ESCAPE
        + "function dayName(d, opts){ return 'Monday'; }\n"
        + "var weekState = { data: { slot_times: { breakfast: '8:00', lunch: '12:30', dinner: '6:30' } }, rhythm: null };\n"
        + "var swapState = null;\n"
        + "var REHEAT_ACTION_LABEL = 'Mark eaten';\n"
        + "var SWAP_LABEL = 'Swap · I’ll pick';\n"
        + f"var cookState = {{ data: {{ meals: {json.dumps(cook_meals)} }}, cookAheadPicks: {{}} }};\n"
        + "var GRO_ICONS = { chevRight: '<svg class=\"chev\"></svg>' };\n"
        + "var SLOT_LABELS = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };\n"
        + "function cookMealKey(m) { return 'e' + m.entry_id; }\n"
        + f"var TICKED = {json.dumps(ticked or [])};\n"
        + "function cookTicked(kind, key) { return TICKED.indexOf(kind + ':' + key) !== -1; }\n"
        + "function cookAheadHtml() { return ''; }\n"
        + "var WK_ADD_ICON = '<svg/>'; function humanQtyText(t) { return String(t == null ? '' : t); }\n"
        + "function cookIngredientLabel(i) { return ((i.qty ? i.qty + ' ' : '') + i.item).trim(); }\n"
        + _PURE + _live_helpers()
        + "".join(_extract(n) + "\n" for n in (
            "daySlotEntry", "slotWord", "isRealCook", "mealDisplayName", "cookMealForEntry",
            "mealCookName", "mealCookUnderway", "mealClockFor", "mealHeroLine", "mealHeroHtml",
            "mealStopHtml", "mealClockHtml", "swapStateFor", "swapLineHtml", "slotEyebrowLabel",
            "dishSizeClass", "mealDockHtml", "mealWhatsInHtml", "mealStepHtml"))
        + f"console.log(JSON.stringify(mealStepHtml({json.dumps(day)}, {json.dumps(slot)})));\n"
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_the_meal_steps_hero_stops_and_dock_follow_the_real_start():
    card = dict(_COOK_CARD, cook_started_at="2026-09-14T18:02:00")
    html = _screen(_monday(_DINNER), "dinner", [card])
    # The hero: the rebased table time in words, and "Started" for "Start at".
    assert 'class="wk-meal-by">On the table by 6:32<' in html
    assert re.findall(r'hero-chip">([^<]*)<', html) == ["Started 6:02"]
    # The stops are true to the real start.
    times = re.findall(r'wk-stop-time">([^<]*)<', html)
    assert times[0] == "6:02" and times[-1] == "6:32"
    # A cook that has begun is not offered "Start at 6:02".
    assert 'data-wk-cook="dinner">Keep cooking<' in html
    assert "Start at" not in html
    # And the same screen without a start is exactly the plan's.
    html = _screen(_monday(_DINNER), "dinner", [_COOK_CARD])
    assert 'class="wk-meal-by">On the table by half six<' in html
    assert re.findall(r'hero-chip">([^<]*)<', html) == ["Start at 6:00"]
    assert 'data-wk-cook="dinner">Start at 6:00<' in html


@_needs_node
def test_a_started_card_marked_cooked_is_back_to_the_plan_on_the_meal_step():
    """cook_started_at is kept on a cooked meal (a true fact); the readers
    that show a clock still fall back, since mealClockFor is only asked
    for a cook, and the dock offers the start again once it is unticked."""
    card = dict(_COOK_CARD, cook_started_at="2026-09-14T18:02:00", cooked_status="done")
    html = _screen(_monday(_DINNER), "dinner", [card])
    # The clock still reads off the real start while the card carries it —
    # the server clears it the moment "Mark not cooked" is tapped.
    assert 'class="wk-meal-by">On the table by 6:32<' in html


# ---------- Cook's Tonight card ----------


def _tonight(meals, moves, data, today, tonight_idx):
    return _shelf_node(
        _var("NUMBER_WORDS") + "\n" + _var("TENS_WORDS") + "\n"
        + "".join(_extract(n) + "\n" for n in (
            "numberWord", "minutesInWords", "clockLabel", "isSnackSlot", "slotTableMinutes",
            "mealTotalMinutes", "mealClockSides", "mealClockTotal"))
        + _live_helpers()
        + "cookState.tonightIdx = " + json.dumps(tonight_idx) + ";\n"
        + "var meals = " + json.dumps(meals) + ";\n"
        + "var rows = kitchenTodayRows(meals, " + json.dumps(moves) + ", " + json.dumps(today) + ");\n"
        + "console.log(JSON.stringify(kitchenCookingTodayHtml(rows, meals, " + json.dumps(today) + ", " + json.dumps(data) + ")));"
    )


def _tiles(html: str) -> list[tuple[str, str]]:
    return re.findall(r'cook-tonight-tile-label">([^<]*)</span><span class="cook-tonight-tile-value">([^<]*)<', html)


@_needs_node
def test_the_tonight_card_reads_started_and_the_new_table_time_and_says_so_once():
    started = _meal(1, SHELF_MON, "Chicken Tikka Masala", cook_started_at=SHELF_MON + "T18:12:00")
    html = _tonight([started], [_MOVE], {"cook_name": "Emily"}, SHELF_MON, 0)
    assert _tiles(html) == [("Started", "6:12"), ("On the table", "6:42")]
    # Twelve minutes after the plan's 6:00 (off the move's own "Start by").
    assert ">Started 12 minutes late — the clock’s moved with you.<" in html
    assert "Nothing to thaw" not in html
    # The fresher moves payload carries planned_start; the line is the same.
    fresh = dict(_MOVE, chips=["30 min", "Started 6:12"], time_label="6:42 tonight",
                 planned_start=SHELF_MON + "T18:00:00", started_at=SHELF_MON + "T18:12:00")
    html = _tonight([started], [fresh], {"cook_name": "Emily"}, SHELF_MON, 0)
    assert _tiles(html) == [("Started", "6:12"), ("On the table", "6:42")]
    assert ">Started 12 minutes late — the clock’s moved with you.<" in html


@_needs_node
def test_the_tonight_card_says_nothing_about_a_start_inside_two_minutes():
    on_time = _meal(1, SHELF_MON, "Chicken Tikka Masala", cook_started_at=SHELF_MON + "T18:01:00")
    html = _tonight([on_time], [_MOVE], {}, SHELF_MON, 0)
    assert _tiles(html) == [("Started", "6:01"), ("On the table", "6:31")]
    assert "moved with you" not in html
    assert ">Nothing to thaw or prep ahead.<" in html


@_needs_node
def test_the_tonight_card_says_early_too_and_stays_quiet_once_cooked():
    early = _meal(1, SHELF_MON, "Chicken Tikka Masala", cook_started_at=SHELF_MON + "T17:40:00")
    html = _tonight([early], [_MOVE], {}, SHELF_MON, 0)
    assert _tiles(html) == [("Started", "5:40"), ("On the table", "6:10")]
    assert ">Started 20 minutes early — the clock’s moved with you.<" in html
    done = _meal(1, SHELF_MON, "Chili", cook_started_at=SHELF_MON + "T17:40:00", cooked_status="done")
    html = _tonight([done], [_MOVE], {}, SHELF_MON, 0)
    assert ">Cooked.<" in html
    assert "cook-tonight-times" not in html and "moved with you" not in html


@_needs_node
def test_the_tonight_card_without_a_start_is_exactly_what_it_was():
    html = _tonight([_meal(1, SHELF_MON, "Chicken Tikka Masala")], [_MOVE], {"cook_name": "Emily"}, SHELF_MON, 0)
    assert _tiles(html) == [("Start", "6:00"), ("On the table", "6:30")]
    assert ">Nothing to thaw or prep ahead.<" in html


# ---------- the cook hero ----------


def _hero(meal: dict, stage: str) -> str:
    harness = (
        _COOK_STUBS
        + "var cookState = { stepIdx: 1 };\n"
        + _var("NUMBER_WORDS") + "\n" + _var("TENS_WORDS") + "\n"
        + "".join(_extract(n) + "\n" for n in (
            "numberWord", "minutesInWords", "clockLabel", "mealTotalMinutes", "mealClockSides", "mealClockTotal",
            "cookAttendanceChip", "cookBatchNote", "cookMadeAheadLinesHtml", "cookFocusHeroHtml"))
        + _live_helpers()
        + f"console.log(JSON.stringify(cookFocusHeroHtml({{}}, {json.dumps(meal)}, 0, {json.dumps(stage)})));\n"
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_HERO_MEAL = {"entry_id": 7, "meal": "Ginger Beef Stir-Fry", "date": "2026-09-14", "slot": "dinner",
              "cooked_status": "pending", "prep_time_minutes": 10, "cook_time_minutes": 20,
              "instructions": ["Heat the oil.", "Add the steak.", "Serve."], "ingredients": [], "sides": []}


def _chips(html: str) -> list[tuple[str, str]]:
    return re.findall(r'<span class="cook-meta-chip( is-live)?">([^<]*)</span>', html)


@_needs_node
def test_the_cook_hero_says_started_on_every_stage_in_the_live_chip():
    live = dict(_HERO_MEAL, cook_started_at="2026-09-14T18:02:00")
    for stage in ("prep", "step", "method"):
        chips = _chips(_hero(live, stage))
        assert (" is-live", "Started 6:02") in chips, stage
        assert ("", "On the table 6:32") in chips, stage
        # Only the one live chip is celadon.
        assert [c for c in chips if c[0]] == [(" is-live", "Started 6:02")], stage
    # Before you start still carries its own facts first.
    assert _chips(_hero(live, "prep"))[0] == ("", "Tuesday, Sep 9")
    # A cook not yet begun: no live chip anywhere, and the slim stages
    # carry no chips at all, as before.
    assert not any(c[0] for c in _chips(_hero(_HERO_MEAL, "prep")))
    assert _chips(_hero(_HERO_MEAL, "step")) == []
    # A cooked meal keeps its start on the row but the hero says nothing
    # about a clock that has finished.
    assert _chips(_hero(dict(live, cooked_status="done"), "step")) == []


def test_the_live_chip_is_celadon_with_dark_ink_and_every_colour_a_token():
    block = SHELL_CSS[SHELL_CSS.index(".cook-meta-chip.is-live"):]
    block = block[:block.index("}") + 1]
    assert "background: var(--celadon)" in block
    assert "color: var(--on-accent-ink)" in block  # Rule 1
    assert "#" not in block


# ---------- "Start cooking" itself ----------


def _handler(reply: dict, meal: dict, fail: bool = False) -> dict:
    harness = (
        "var TOASTS = [], POSTS = [], STAGES = [], RENDERS = [], REFRESHES = 0;\n"
        "function showToast(m, action, hold) { TOASTS.push({ msg: m, action: action || null, hold: hold || null }); }\n"
        f"var REPLY = {json.dumps(reply)}; var FAIL = {json.dumps(fail)};\n"
        "function fetch(url, opts) {\n"
        "  POSTS.push({ url: url, body: opts && opts.body ? JSON.parse(opts.body) : null });\n"
        "  if (FAIL) return Promise.resolve({ ok: false, json: function () { return Promise.resolve({}); } });\n"
        "  return Promise.resolve({ ok: true, json: function () { return Promise.resolve(REPLY); } });\n"
        "}\n"
        f"var MEAL = {json.dumps(meal)};\n"
        "var cookState = { stepIdx: 0 };\n"
        "function cookFocusMeal() { return MEAL; }\n"
        "function cookFirstUndoneStep() { return 0; }\n"
        "function cookGoStage(s) { STAGES.push(s); }\n"
        "function renderCookFrom(v) { RENDERS.push(v); }\n"
        "function refreshPlanSurfacesAfterCook() { REFRESHES += 1; }\n"
        + _var("NUMBER_WORDS") + "\n" + _var("TENS_WORDS") + "\n"
        + "".join(_extract(n) + "\n" for n in ("numberWord", "minutesInWords", "clockLabel"))
        + _live_helpers()
        + _var("START_MOVED_TROUBLE") + "\n"
        + _extract_async("cookPost") + "\n"
        + _extract_async("cookRecordStart") + "\n"
        + _extract_async("cookStartCooking") + "\n"
        + "cookStartCooking();\n"
        "setTimeout(function () { console.log(JSON.stringify({ toasts: TOASTS, posts: POSTS, stages: STAGES,"
        " renders: RENDERS.length, refreshes: REFRESHES })); }, 20);\n"
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


_TAP_MEAL = {"entry_id": 7, "meal": "Chili", "cooked_status": "pending", "instructions": ["Cook."]}
_MOVED = {"meals": [], "started_at": "2026-09-13T18:02:00", "on_the_table": "2026-09-13T18:47:00",
          "planned_start": "2026-09-13T17:45:00", "already_started": False}


@_needs_node
def test_start_cooking_enters_the_steps_at_once_and_says_the_clock_moved_once():
    out = _handler(_MOVED, _TAP_MEAL)
    # The step stage first, the post in the background.
    assert out["stages"] == ["step"]
    assert out["posts"] == [{"url": "/api/cooker/start", "body": {"entry_id": 7}}]
    # The refreshed view lands, and the other surfaces are told.
    assert out["renders"] == 1 and out["refreshes"] == 1
    # ONE pop-up, in the app's own words, held long enough to read.
    assert out["toasts"] == [{"msg": "You started at 6:02, so the clock moved. On the table by 6:47.",
                              "action": None, "hold": 6000}]
    assert "!" not in out["toasts"][0]["msg"]


@_needs_node
def test_a_start_inside_two_minutes_of_the_plan_gets_no_pop_up():
    close = dict(_MOVED, started_at="2026-09-13T17:46:00", on_the_table="2026-09-13T18:31:00")
    out = _handler(close, _TAP_MEAL)
    assert out["stages"] == ["step"] and len(out["posts"]) == 1
    assert out["toasts"] == []
    # Exactly on the plan: nothing either.
    out = _handler(dict(_MOVED, started_at="2026-09-13T17:45:00", on_the_table="2026-09-13T18:30:00"), _TAP_MEAL)
    assert out["toasts"] == []
    # Two minutes is where it starts to be worth a word.
    out = _handler(dict(_MOVED, started_at="2026-09-13T17:47:00", on_the_table="2026-09-13T18:32:00"), _TAP_MEAL)
    assert [t["msg"] for t in out["toasts"]] == ["You started at 5:47, so the clock moved. On the table by 6:32."]


@_needs_node
def test_a_second_tap_never_says_it_again():
    # The server's "already started" answer: the view lands, no pop-up.
    out = _handler(dict(_MOVED, already_started=True), _TAP_MEAL)
    assert out["renders"] == 1 and out["toasts"] == []
    # And a card that already carries its start doesn't even post.
    out = _handler(_MOVED, dict(_TAP_MEAL, cook_started_at="2026-09-13T18:02:00"))
    assert out["stages"] == ["step"] and out["posts"] == [] and out["toasts"] == []


@_needs_node
def test_a_reheat_or_a_cooked_meal_never_records_a_start():
    assert _handler(_MOVED, dict(_TAP_MEAL, is_leftovers=True))["posts"] == []
    assert _handler(_MOVED, dict(_TAP_MEAL, cooked_status="done"))["posts"] == []


@_needs_node
def test_a_start_with_no_minutes_behind_it_has_no_clock_to_move():
    out = _handler(dict(_MOVED, on_the_table=None, planned_start=None), _TAP_MEAL)
    assert out["renders"] == 1 and out["toasts"] == []


@_needs_node
def test_a_failed_post_is_one_calm_line_and_nothing_else():
    out = _handler(_MOVED, _TAP_MEAL, fail=True)
    assert out["stages"] == ["step"]
    assert out["renders"] == 0 and out["refreshes"] == 0
    assert [t["msg"] for t in out["toasts"]] == ["Couldn’t note the start time — the plan’s clock stands."]
    assert "!" not in out["toasts"][0]["msg"]


# ---------- source markers ----------


def test_every_string_a_person_reads_goes_through_escape_html():
    hero = _extract("cookFocusHeroHtml")
    assert "escapeHtml(text)" in hero
    card = _extract("cookTonightCardHtml")
    assert "escapeHtml(note)" in card and "escapeHtml(t.value)" in card
    assert "escapeHtml(c)" in _extract("mealHeroHtml")


def test_the_dispatch_and_the_route_are_wired():
    assert "if (what === 'start-cooking') return cookStartCooking();" in SHELL_JS
    assert "cookPost('/api/cooker/start', { entry_id: meal.entry_id })" in SHELL_JS
    main = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert '@app.post("/api/cooker/start")' in main
    assert "tools.start_cooking(req.entry_id)" in main


# ---------- after the verifier ----------

def test_only_tonights_cook_can_be_started(tonight):
    """Tomorrow's recipe read tonight is normal; a start on it would sit
    there for days. Refused, with the day named, and nothing written."""
    tomorrow = (TODAY + datetime.timedelta(days=1)).isoformat()
    plan_id = tools.get_plan_id_for_date(ISO_TODAY)
    tools.plan_meal(tomorrow, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    conn = get_conn()
    row = conn.execute("SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ?",
                       (tools.household_id(), tomorrow)).fetchone()
    conn.close()
    out = tools.start_cooking(row["id"], now_utc=_utc(20, 15))
    assert out["status"] == "refused"
    assert datetime.date.fromisoformat(tomorrow).strftime("%A") in out["message"]
    assert _started_at(row["id"]) is None
    # Tonight's still starts.
    assert tools.start_cooking(tonight, now_utc=_utc(18, 2))["started_at"] == f"{ISO_TODAY}T18:02:00"


def test_mark_not_cooked_clears_a_stray_start_even_on_a_row_that_was_never_cooked(tonight):
    tools.start_cooking(tonight, now_utc=_utc(18, 2))
    assert _started_at(tonight) is not None
    out = tools.check_off_meal(tonight, "pending")
    assert out.get("unchanged") is True
    assert _started_at(tonight) is None


def test_a_meal_with_no_minutes_keeps_the_plans_table_time_on_now():
    tools.add_member("Emily")
    tools.add_recipe("Mystery", ingredients=[{"item": "Something", "qty": "1"}])
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(ISO_TODAY, "Mystery", slot="dinner", weekly_plan_id=plan_id)
    _set_timezone("UTC")
    conn = get_conn()
    entry_id = conn.execute("SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ?",
                            (tools.household_id(), ISO_TODAY)).fetchone()["id"]
    conn.close()
    before = _cook_move(_at(17, 0))["detail"]
    tools.start_cooking(entry_id, now_utc=_utc(18, 2))
    after = _cook_move(_at(18, 5))
    assert after["detail"] == before  # the plan's table time stands
    assert "Started 6:02" in after["chips"]


def test_the_shell_handles_a_refused_start_and_the_kitchen_row_drops_the_chip_once_cooked():
    src = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
    i = src.index("  async function cookRecordStart(meal) {")
    body = src[i:i + 1400]
    assert "if (out && out.status === 'refused') {" in body
    assert "weekState.cookView = null;" in body
    assert "else if (/^Started /.test(chip)) { if (!done) bits.push(chip); }" in src
