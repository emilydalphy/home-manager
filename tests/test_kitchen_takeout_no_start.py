"""
Kitchen offers no "Start cooking" for a written-in takeout night (overnight
hunt, 2026-10-18; branch overnight/kitchen-takeout-no-start — the other half
of overnight/takeout-night-not-a-cook).

Today stopped calling "Takeout pizza" a cook; Kitchen's Tonight card still
read it as one ("Start cooking" in the dock) and start_cooking wrote a start
on it. The server now marks the card (`is_takeout`, from
moves.is_takeout_night) and refuses the start; the shell reads the mark.

Each test says whether it is a CATCH (red without the fix) or a GUARD.
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from conftest import household_today
import json

from test_cook_shelf import MON, _MOVE, _function, _meal, _needs_node, _node, _tonight

TODAY = household_today()
ISO_TODAY = TODAY.isoformat()
WEEK_START = (TODAY - dt.timedelta(days=2)).isoformat()
TORONTO = ZoneInfo("America/Toronto")


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(TODAY, dt.time(hour, minute), tzinfo=TORONTO).astimezone(timezone.utc)


def _plan_tonight(dinner: str, day: str = ISO_TODAY) -> int:
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(day, dinner, slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=False)
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = 'dinner'",
        (tools.household_id(), day),
    ).fetchone()
    conn.close()
    return row["id"]


def _card(entry_id: int) -> dict:
    return next(m for m in tools.get_cooker_view()["meals"] if m["entry_id"] == entry_id)


def _started_at(entry_id: int):
    conn = get_conn()
    row = conn.execute("SELECT cook_started_at FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return row["cook_started_at"]


# ---------- the server ----------


def test_a_takeout_night_is_marked_and_its_start_is_refused():
    """CATCH. The card says takeout, and start_cooking refuses with a plain
    sentence and writes nothing."""
    entry = _plan_tonight("Takeout pizza")
    assert _card(entry)["is_takeout"] is True
    out = tools.start_cooking(entry, now_utc=_utc(17, 30))
    assert out == {"status": "refused", "entry_id": entry, "message": "Nothing to cook on a takeout night."}
    assert _started_at(entry) is None


def test_the_route_refuses_it_too(signed_in):
    """CATCH. A stale screen or chat posting /api/cooker/start gets the same
    refusal (200, status refused — the route's neighbours' shape)."""
    entry = _plan_tonight("Takeout pizza", _cooker.household_now().date().isoformat())
    res = signed_in.post("/api/cooker/start", json={"entry_id": entry})
    assert res.status_code == 200
    assert res.json()["status"] == "refused"
    assert _started_at(entry) is None


def test_a_saved_takeout_recipe_and_a_takeout_style_home_cook_still_start():
    """GUARD. A saved recipe named "Takeout Fakeout Chicken" and a written-in
    "Thai takeout-style curry" are cooks: not marked, and the start lands."""
    tools.add_recipe("Takeout Fakeout Chicken", ingredients=[{"item": "Chicken", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=2)
    saved = _plan_tonight("Takeout Fakeout Chicken")
    assert _card(saved)["is_takeout"] is False
    assert tools.start_cooking(saved, now_utc=_utc(17, 30)).get("status") != "refused"
    assert _started_at(saved)

    conn = get_conn()
    conn.execute("DELETE FROM meal_plan_entries WHERE id = ?", (saved,))
    conn.commit()
    conn.close()
    style = _plan_tonight("Thai takeout-style curry")
    assert _card(style)["is_takeout"] is False
    assert tools.start_cooking(style, now_utc=_utc(17, 30)).get("status") != "refused"
    assert _started_at(style)


# ---------- the Kitchen root (shell.js, run under node) ----------


@_needs_node
def test_kitchen_names_a_takeout_night_with_no_start_and_no_clock():
    """CATCH. The Tonight card names the dinner, says there is nothing to
    cook, shows no Start / On the table tiles, and the dock is empty."""
    meals = [_meal(1, MON, "Takeout pizza", is_takeout=True, prep_time_minutes=None, cook_time_minutes=None)]
    out = _tonight(meals, [], {"cook_name": "Emily"}, MON, 0)
    assert '<h2 class="cook-tonight-dish">Takeout pizza</h2>' in out["html"]
    assert ">Nothing to cook for this one.<" in out["html"]
    assert "cook-tonight-times" not in out["html"]
    assert out["dock"] == ""
    sub = _node(
        _function("kitchenSubtitle") + "\nvar meals = " + json.dumps(meals) + ";\n"
        "console.log(JSON.stringify(kitchenSubtitle(kitchenTodayRows(meals, [], '" + MON + "'), meals, '" + MON + "')));"
    )
    assert sub == "nothing to cook today"


@_needs_node
def test_a_cook_lunch_beside_a_takeout_dinner_still_gets_the_card_and_start():
    """GUARD. The cook is tonight's card and keeps Start cooking; the takeout
    row under it is a plain line, not a way into a recipe."""
    meals = [_meal(1, MON, "Weeknight Chili", slot="lunch"),
             _meal(2, MON, "Takeout pizza", is_takeout=True)]
    out = _tonight(meals, [_MOVE], {}, MON)
    assert '<h2 class="cook-tonight-dish">Weeknight Chili</h2>' in out["html"]
    assert 'data-cook="start-tonight" data-idx="0">Start cooking</button>' in out["dock"]
    assert '<span class="cook-week-name">Takeout pizza</span>' in out["html"]
    assert 'data-idx="1"' not in out["html"]


@_needs_node
def test_a_takeout_night_opened_from_the_shelf_has_no_recipe_ask_and_no_dock():
    """CATCH. The tile opened the recipe screen: "No saved recipe for this
    one — ask me for it in the chat." over "Done — on the table"."""
    html = _node(
        _function("cookTakeoutFocusHtml") + "\n"
        "function cookBackLabel(){ return 'Cook'; }\n"
        "function recipeTitleHtml(m){ return '<h1>' + escapeHtml(m.meal) + '</h1>'; }\n"
        "function recipeForLine(){ return ''; }\n"
        + _function("cookFocusHtml").split("var steps")[0] + "}\n"
        "console.log(JSON.stringify(cookFocusHtml({}, " + json.dumps([_meal(1, MON, "Takeout pizza", is_takeout=True)]) + ", 0)));"
    )
    assert "<h1>Takeout pizza</h1>" in html
    assert ">Nothing to cook for this one.<" in html
    assert "dock" not in html and "ask me for it" not in html
