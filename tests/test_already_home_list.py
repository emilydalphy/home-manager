"""
Card B4 (Option B, 2026-10-26): the Shop list leaves off what's already home.

A plan line the kitchen can be shown to cover, from food bought, added or
counted in the last 14 days, is set aside (removed_by 'kitchen') and shown
under the list's "Already home · N" section; one tap puts it back, and the
put-back sticks through a rebuild. Older rows, partial amounts, a
household's own line, staples and spices all stay as they were.
See app/tools/already_home.py.
"""
from __future__ import annotations

import datetime

from conftest import household_today
from app import db, tools
from app.tools import already_home


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _day(n: int) -> str:
    return (_monday() + datetime.timedelta(days=n)).isoformat()


def _future_days():
    """Dinner nights still to come this week (the section shows lines with a
    meal still ahead), always at least two, rolling into next week if needed."""
    today = household_today()
    return [(today + datetime.timedelta(days=n)).isoformat() for n in (1, 2)]


def _recipe(name, ingredients):
    tools.add_recipe(name, ingredients=ingredients, default_servings=2,
                     prep_time_minutes=10, cook_time_minutes=20, instructions=["Cook."])


def _week(meals, week_start=None):
    """meals: [(date, recipe name)] — planned and approved."""
    for name in ("Emily", "Vineeth"):
        if not any(m["name"] == name for m in tools.list_members()):
            tools.add_member(name)
    plan_id = tools.create_weekly_plan(week_start or _monday().isoformat())["weekly_plan_id"]
    for d, name in meals:
        tools.plan_meal(d, name, slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


def _rows():
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, item, quantity, status, removed_by, source_weekly_plan_id, already_have_reviewed "
        "FROM grocery_items ORDER BY id").fetchall()]
    conn.close()
    return rows


def _lines(item):
    return [r for r in _rows() if r["item"].lower() == item.lower()]


def _age_inventory(item, days):
    """Make a kitchen row look like it was put there `days` ago, uncounted."""
    then = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_conn()
    conn.execute("UPDATE inventory_items SET created_at = ?, counted_on = NULL WHERE LOWER(item) = LOWER(?)",
                 (then, item))
    conn.commit()
    conn.close()


def _beef_dinner():
    _recipe("Beef Tacos", [
        {"item": "Ground beef", "qty": "1 lb", "category": "meat"},
        {"item": "Red onion", "qty": "1", "category": "produce"},
    ])


def test_a_covered_plan_line_is_left_off_and_listed_as_already_home():
    _beef_dinner()
    tools.update_inventory("Ground beef", "add", quantity="2 lbs", location="fridge")
    _week([(_future_days()[0], "Beef Tacos")])

    beef = _lines("Ground beef")
    assert len(beef) == 1, beef  # on main: no line at all, skipped silently
    assert beef[0]["status"] == "removed" and beef[0]["removed_by"] == already_home.REMOVED_BY_KITCHEN
    assert [r["item"] for r in _lines("Red onion") if r["status"] == "needed"] == ["Red onion"]

    shown = tools.already_home_lines()
    assert [r["item"] for r in shown] == ["Ground beef"]
    assert shown[0]["quantity"] == "1 lb"


def test_a_row_older_than_two_weeks_stays_on_the_list():
    _beef_dinner()
    tools.update_inventory("Ground beef", "add", quantity="2 lbs", location="fridge")
    _age_inventory("Ground beef", 20)
    _week([(_future_days()[0], "Beef Tacos")])

    beef = _lines("Ground beef")
    assert [(r["status"], r["quantity"]) for r in beef] == [("needed", "1 lb")]
    assert tools.already_home_lines() == []


def test_an_old_row_counted_this_week_is_fresh():
    _beef_dinner()
    tools.update_inventory("Ground beef", "add", quantity="2 lbs", location="fridge")
    _age_inventory("Ground beef", 30)
    conn = db.get_conn()
    conn.execute("UPDATE inventory_items SET counted_on = ?", (household_today().isoformat(),))
    conn.commit()
    conn.close()
    _week([(_future_days()[0], "Beef Tacos")])
    assert [r["status"] for r in _lines("Ground beef")] == ["removed"]


def test_a_confirmed_on_column_counts_when_it_exists():
    """A4 (inventory/pre-draft-check) adds inventory_items.confirmed_on; the
    recency rule reads it whenever the column is there."""
    conn = db.get_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(inventory_items)").fetchall()}
    added = "confirmed_on" not in cols
    if added:
        conn.execute("ALTER TABLE inventory_items ADD COLUMN confirmed_on TEXT")
        conn.commit()
    conn.close()
    try:
        _beef_dinner()
        tools.update_inventory("Ground beef", "add", quantity="2 lbs", location="fridge")
        _age_inventory("Ground beef", 30)
        conn = db.get_conn()
        conn.execute("UPDATE inventory_items SET confirmed_on = ?", (household_today().isoformat(),))
        conn.commit()
        conn.close()
        _week([(_future_days()[0], "Beef Tacos")])
        assert [r["status"] for r in _lines("Ground beef")] == ["removed"]
    finally:
        if added:  # leave the shared test database's schema as main has it
            conn = db.get_conn()
            conn.execute("ALTER TABLE inventory_items DROP COLUMN confirmed_on")
            conn.commit()
            conn.close()


def test_partly_home_leaves_the_whole_line_on():
    # Two different dinners each want a pound; one pound is home. The line
    # stays on at the full two pounds rather than guessing the difference.
    _beef_dinner()
    _recipe("Beef Chili", [{"item": "Ground beef", "qty": "1 lb", "category": "meat"}])
    tools.update_inventory("Ground beef", "add", quantity="1 lb", location="fridge")
    d1, d2 = _future_days()
    _week([(d1, "Beef Tacos"), (d2, "Beef Chili")])
    beef = _lines("Ground beef")
    assert [(r["status"], r["quantity"]) for r in beef] == [("needed", "2 lbs")]
    assert tools.already_home_lines() == []


def test_a_household_line_is_never_left_off_or_grown():
    _beef_dinner()
    tools.add_grocery_item("Ground beef", quantity="1 lb", category="meat")
    tools.update_inventory("Ground beef", "add", quantity="3 lbs", location="fridge")
    _week([(_future_days()[0], "Beef Tacos")])
    beef = _lines("Ground beef")
    assert [(r["status"], r["quantity"], r["source_weekly_plan_id"]) for r in beef] == [("needed", "1 lb", None)]
    assert tools.already_home_lines() == []


def test_spices_keep_their_own_rule():
    _recipe("Cumin Beans", [
        {"item": "Cumin", "qty": "1 tsp", "category": "spices"},
        {"item": "Black beans", "qty": "1 can", "category": "pantry"},
    ])
    tools.update_inventory("Cumin", "add", quantity="1 jar", location="pantry")
    _week([(_future_days()[0], "Cumin Beans")])
    assert all(r["removed_by"] != already_home.REMOVED_BY_KITCHEN for r in _rows())


def test_staples_keep_their_own_rule():
    _recipe("Rice Bowl", [{"item": "Rice", "qty": "2 cups", "category": "pantry"}])
    tools.add_staple("Rice")
    tools.update_inventory("Rice", "add", quantity="4 cups", location="pantry")
    _week([(_future_days()[0], "Rice Bowl")])
    assert all(r["removed_by"] != already_home.REMOVED_BY_KITCHEN for r in _rows())


def test_add_it_back_puts_the_same_line_back_and_it_sticks(client, signed_in):
    _beef_dinner()
    tools.update_inventory("Ground beef", "add", quantity="2 lbs", location="fridge")
    plan_id = _week([(_future_days()[0], "Beef Tacos")])
    line = _lines("Ground beef")[0]

    summary = client.get("/api/grocery-list/already-have-summary").json()
    kitchen = [r for r in summary["already_have"] if r["removed_by"] == "kitchen"]
    assert [r["id"] for r in kitchen] == [line["id"]]

    res = client.post(f"/api/grocery-list/{line['id']}/pre-shop-undo")
    assert res.status_code == 200 and res.json()["status"] == "needed"
    back = _lines("Ground beef")
    assert [(r["id"], r["status"]) for r in back] == [(line["id"], "needed")]
    summary = client.get("/api/grocery-list/already-have-summary").json()
    assert not [r for r in summary["already_have"] if r["removed_by"] == "kitchen"]

    # A rebuild rewrites the plan's lines: simulate the new line (no review
    # mark) and run the pass again — the put-back is remembered for the week.
    conn = db.get_conn()
    conn.execute("UPDATE grocery_items SET already_have_reviewed = 0 WHERE id = ?", (line["id"],))
    conn.commit()
    conn.close()
    assert already_home.leave_off_what_is_home(plan_id) == []
    assert [r["status"] for r in _lines("Ground beef")] == ["needed"]


def test_a_line_no_longer_covered_comes_back_on_the_next_pass():
    _beef_dinner()
    tools.update_inventory("Ground beef", "add", quantity="1 lb", location="fridge")
    plan_id = _week([(_future_days()[0], "Beef Tacos")])
    assert [r["status"] for r in _lines("Ground beef")] == ["removed"]
    tools.update_inventory("Ground beef", "remove")
    already_home.leave_off_what_is_home(plan_id)
    assert [r["status"] for r in _lines("Ground beef")] == ["needed"]


def test_one_pound_at_home_is_already_home_for_one_week_only():
    _beef_dinner()
    tools.update_inventory("Ground beef", "add", quantity="1 lb", location="fridge")
    this_week = _week([(_future_days()[0], "Beef Tacos")])
    next_monday = _monday() + datetime.timedelta(days=7)
    _week([((next_monday + datetime.timedelta(days=1)).isoformat(), "Beef Tacos")],
          week_start=next_monday.isoformat())
    beef = sorted(_lines("Ground beef"), key=lambda r: r["id"])
    by_plan = {r["source_weekly_plan_id"]: r["status"] for r in beef}
    assert by_plan[this_week] == "removed"
    assert sorted(by_plan.values()) == ["needed", "removed"], beef


# ---- verify 2026-10-27: judge a line on its meals STILL TO COME ----------

def _two_taco_nights_two_pounds_home():
    """Tacos tonight and in two days (1 lb each), 2 lb at home, approved."""
    _beef_dinner()
    _recipe("Pasta", [{"item": "Spaghetti", "qty": "1 lb", "category": "pantry"}])
    tools.update_inventory("Ground beef", "add", quantity="2 lbs", location="fridge")
    today = household_today()
    d0, d2, d3 = (today + datetime.timedelta(days=n) for n in (0, 2, 3))
    monday = today - datetime.timedelta(days=today.weekday())
    plan_id = _week([(d0.isoformat(), "Beef Tacos"), (d2.isoformat(), "Beef Tacos"),
                     (d3.isoformat(), "Pasta")], week_start=monday.isoformat())
    conn = db.get_conn()
    tonight = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
        (plan_id, d0.isoformat())).fetchone()["id"]
    conn.close()
    return plan_id, tonight, d3.isoformat()


def test_a_cooked_night_does_not_put_the_rest_of_the_week_back_on_the_list():
    """Issue 1: eat tonight's tacos (1 lb leaves the kitchen), then swap an
    unrelated night. Thursday's pound is still home — nothing to buy."""
    plan_id, tonight, pasta_day = _two_taco_nights_two_pounds_home()
    assert [r["status"] for r in _lines("Ground beef")] == ["removed"]
    tools.check_off_meal(tonight, "done")
    _recipe("Soup", [{"item": "Carrots", "qty": "2", "category": "produce"}])
    tools.swap_meal_in_plan(plan_id, pasta_day, "Soup", slot="dinner")
    assert not [r for r in _lines("Ground beef") if r["status"] == "needed"], _lines("Ground beef")


def test_a_meal_added_while_its_food_is_set_aside_is_one_row_not_two():
    """Issue 2: swap a second beef dinner in while the beef line is set aside
    and the kitchen covers both — one set-aside row, not two."""
    _beef_dinner()
    _recipe("Pasta", [{"item": "Spaghetti", "qty": "1 lb", "category": "pantry"}])
    _recipe("Beef Chili", [{"item": "Ground beef", "qty": "1 lb", "category": "meat"}])
    tools.update_inventory("Ground beef", "add", quantity="2 lbs", location="fridge")
    d1, d2 = _future_days()
    plan_id = _week([(d1, "Beef Tacos"), (d2, "Pasta")])
    tools.swap_meal_in_plan(plan_id, d2, "Beef Chili", slot="dinner")
    beef = _lines("Ground beef")
    assert [(r["status"], r["quantity"]) for r in beef] == [("removed", "2 lbs")], beef
    assert [r["item"] for r in tools.already_home_lines()] == ["Ground beef"]


def test_partly_covered_after_an_add_puts_the_whole_line_on_the_list():
    """Issue 2, the partial case: one pound home, a second beef dinner swapped
    in — the whole two pounds is on the list, nothing left under Already home."""
    _beef_dinner()
    _recipe("Pasta", [{"item": "Spaghetti", "qty": "1 lb", "category": "pantry"}])
    _recipe("Beef Chili", [{"item": "Ground beef", "qty": "1 lb", "category": "meat"}])
    tools.update_inventory("Ground beef", "add", quantity="1 lb", location="fridge")
    d1, d2 = _future_days()
    plan_id = _week([(d1, "Beef Tacos"), (d2, "Pasta")])
    assert [r["status"] for r in _lines("Ground beef")] == ["removed"]
    tools.swap_meal_in_plan(plan_id, d2, "Beef Chili", slot="dinner")
    beef = _lines("Ground beef")
    assert [(r["status"], r["quantity"]) for r in beef] == [("needed", "2 lbs")], beef
    assert tools.already_home_lines() == []


def test_a_set_aside_line_with_no_meal_left_is_cleaned_up():
    _beef_dinner()
    _recipe("Pasta", [{"item": "Spaghetti", "qty": "1 lb", "category": "pantry"}])
    tools.update_inventory("Ground beef", "add", quantity="2 lbs", location="fridge")
    d1 = _future_days()[0]
    plan_id = _week([(d1, "Beef Tacos")])
    tools.swap_meal_in_plan(plan_id, d1, "Pasta", slot="dinner")
    assert _lines("Ground beef") == []


def test_a_cooked_week_does_not_hold_its_food_against_next_week():
    """Issue 3: tacos today with 2 lb home (set aside), eaten (1 lb left);
    next week's tacos find that pound home."""
    _beef_dinner()
    tools.update_inventory("Ground beef", "add", quantity="2 lbs", location="fridge")
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    this_week = _week([(today.isoformat(), "Beef Tacos")], week_start=monday.isoformat())
    conn = db.get_conn()
    tonight = conn.execute("SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ?",
                           (this_week,)).fetchone()["id"]
    conn.close()
    tools.check_off_meal(tonight, "done")
    next_monday = monday + datetime.timedelta(days=7)
    nxt = _week([((next_monday + datetime.timedelta(days=1)).isoformat(), "Beef Tacos")],
                week_start=next_monday.isoformat())
    by_plan = {r["source_weekly_plan_id"]: r["status"] for r in _lines("Ground beef")}
    assert by_plan[nxt] == "removed", _lines("Ground beef")


# ---------------------------------------------------------------------------
# The section on Shop, run under node from shell.js itself.
# ---------------------------------------------------------------------------
import json  # noqa: E402
import pathlib  # noqa: E402
import shutil  # noqa: E402

import nodeharness  # noqa: E402
import pytest  # noqa: E402

SHELL_JS = (pathlib.Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")


def _render(already, *, stored=None, toggles=0):
    start = SHELL_JS.index("  function groNotNeededHtml() {")
    end = SHELL_JS.index('  // ---------- "Add something": the dock\'s button and its sheet ----------')
    script = (
        "var store = " + json.dumps(stored or {}) + ";\n"
        "var window = { localStorage: { getItem: function (k) { return k in store ? store[k] : null; },"
        " setItem: function (k, v) { store[k] = String(v); } } };\n"
        "var GRO_ICONS = { tick: '', chevDown: 'v', chevRight: '>' };\n"
        "function escapeHtml(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }\n"
        "var groceryState = { openFlagKey: null, alreadyHomeLookedAt: null, alreadyHomeSummary: null };\n"
        + SHELL_JS[start:end] +
        "\ngroceryState.alreadyHaveSummary = { already_have: " + json.dumps(already) + " };\n"
        "var first = groNotNeededHtml();\n"
        "for (var i = 0; i < " + str(toggles) + "; i++) {"
        "  if (groceryState.openFlagKey === 'already-have') groAlreadyHomeClosed();"
        "  groceryState.openFlagKey = groceryState.openFlagKey === 'already-have' ? null : 'already-have'; }\n"
        "console.log(JSON.stringify({ first: first, after: groNotNeededHtml(), store: store }));\n"
    )
    res = nodeharness.run_node(script)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


_HOME = [
    {"id": 7, "item": "Rice", "quantity": "2 cups", "removed_by": "kitchen", "weekly_plan_id": 3},
    {"id": 8, "item": "Ground beef", "quantity": "1 lb", "removed_by": "kitchen", "weekly_plan_id": 3},
]

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@needs_node
def test_the_section_is_titled_already_home_opens_and_asks_for_a_check():
    out = _render(_HOME + [{"id": 9, "item": "Milk", "quantity": "", "removed_by": "user"}])
    html = out["first"]
    # N counts what Pomona left off, not the household's own Milk row.
    assert "Already home &middot; 2" in html
    assert 'aria-expanded="true"' in html  # open the first time this week
    assert "Check these are really home before you go. Bought some anyway? Tap to add it back." in html
    # One tap on a row is the put-back, through the section's one undo.
    assert html.count('data-gro="undo-already-have"') == 3
    assert 'class="gro-home-row" data-gro="undo-already-have" data-id="7"' in html
    assert "Add Rice back to the list" in html


@needs_node
def test_closed_it_names_what_was_left_off_and_stays_closed_that_week():
    out = _render(_HOME, toggles=1)
    assert out["store"] == {"pomona.alreadyHomeClosed.3": "1"}
    assert 'aria-expanded="false"' in out["after"]
    assert "Rice, ground beef. Left off the list. Check these are really home before you go." in out["after"]
    # Next load, same week: closed. A new week opens again.
    again = _render(_HOME, stored=out["store"])
    assert 'aria-expanded="false"' in again["first"]
    new_week = [dict(r, weekly_plan_id=4) for r in _HOME]
    assert 'aria-expanded="true"' in _render(new_week, stored=out["store"])["first"]


@needs_node
def test_one_thing_left_off_reads_in_the_singular():
    out = _render(_HOME[:1], toggles=1)
    assert "Already home &middot; 1" in out["after"]
    assert "Rice. Left off the list. Check this is really home before you go." in out["after"]


@needs_node
def test_without_kitchen_lines_the_section_is_unchanged():
    out = _render([{"id": 9, "item": "Milk", "quantity": "1 L", "removed_by": "user"}])
    assert "Already had on hand &middot; 1" in out["first"]
    assert 'aria-expanded="false"' in out["first"]
    assert "gro-home-note" not in out["first"]
