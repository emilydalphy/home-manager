"""
"Before you shop" — the pass in front of sorting the grocery list.

Loop Board 'Shop: "Before you shop" — regulars, then spices and oils, then
already-have-it, ending on Sort the list' (High, Phase 1). Gowthami's
household, 2026-10-04: "Sorting option is hidden and needs to be clearer
that's the next step", the staples card was never seen, and the spices
section's instructions were unclear.

Started overnight as a frame with no steps (2026-10-05 WIP) and finished
the same day. Emily asked for fewer tests, so this file covers what the card
itself names — the steps appear in order, staples added in step 1 appear on
the list, a spice tapped in step 2 becomes a needed line, items ticked in
step 3 leave the sort count — plus the once-a-week stamp, the pre-tick rule
and "Already on the list", and that skipping every step changes nothing.
"""
from __future__ import annotations

import datetime
import json
import shutil
import sqlite3
from pathlib import Path
from unittest import mock

import nodeharness
import pytest
from conftest import household_today
from shop_harness import CLICK, FIXTURE, STUB, cook_progress, grocery_block

from app import tools
from app.db import get_conn

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node runs the screen's own functions"
)


# ---------------------------------------------------------------------------
# 1. The stamp: once per week, and the week is the plan
# ---------------------------------------------------------------------------


def _plan(start=None, days=7, approved=False):
    start = start or household_today()
    pid = tools.create_weekly_plan(start.isoformat(), day_count=days)["weekly_plan_id"]
    if approved:
        # A DRAFT whose last day has passed is deliberately never the
        # current plan (Emily, 2026-09-11 — the Plan tab opened on a
        # twelve-day-dead draft). An approved one still is: it was the
        # household's real week. So last week's plan has to be approved
        # for "this used to be the current plan" to be true of it, which
        # is also what it would be in life.
        conn = get_conn()
        try:
            conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (pid,))
            conn.commit()
        finally:
            conn.close()
    return pid


def _asked(plan_id):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT before_shop_asked_at FROM weekly_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        return row["before_shop_asked_at"] if row else None
    finally:
        conn.close()


def test_a_fresh_week_has_not_been_through_the_pass():
    """CATCH. NULL means "never run", which is what makes the pass get
    offered — the same reading defrost_asked_at and cook_ahead_asked_at
    already have."""
    pid = _plan()
    assert _asked(pid) is None
    assert tools.before_shop_state() == {"done": False, "weekly_plan_id": pid}


def test_finishing_it_once_is_what_stops_it_coming_back():
    """CATCH. The whole card criterion: "Done once per week; afterwards the
    list opens straight to its sorted view.\""""
    pid = _plan()
    assert tools.mark_before_shop_done() == {"done": True, "weekly_plan_id": pid}
    assert tools.before_shop_state() == {"done": True, "weekly_plan_id": pid}
    assert _asked(pid) is not None




def test_a_new_week_is_a_new_plan_so_the_pass_comes_back():
    """CATCH, and it is the reason the stamp is a column on the plan rather
    than a date anything compares. "Once per week" falls out of the row
    being new — which is also what makes it right for a household planning
    five days, or two weeks at once, where "a week" is not seven days and a
    date comparison would have to guess."""
    today = household_today()
    first = _plan(today - datetime.timedelta(days=7), approved=True)
    tools.mark_before_shop_done()
    assert tools.before_shop_state()["weekly_plan_id"] == first

    _plan(today)
    state = tools.before_shop_state()
    assert state["weekly_plan_id"] != first
    assert state["done"] is False, "a new week asks again"
    assert _asked(first) is not None, "and last week's answer is left where it was"


def test_a_household_with_no_plan_at_all_is_offered_the_pass_and_stamps_nothing():
    """CATCH. There is nowhere to record it, so the honest answer is to
    offer it: an extra tap beats hiding the pass from a household whose
    list came from somewhere other than a week. Marking it must not raise
    — the only caller is the grocery payload."""
    assert tools.before_shop_state() == {"done": False, "weekly_plan_id": None}
    assert tools.mark_before_shop_done() == {"done": False, "weekly_plan_id": None}




def test_a_read_that_fails_does_not_take_the_grocery_list_down_with_it():
    """CATCH. before_shop_state is called from inside the list payload, so
    raising there is a list that will not load — much worse than a pass
    offered one time too many."""
    _plan()
    with mock.patch("app.tools.before_shop.get_conn", side_effect=sqlite3.OperationalError("nope")):
        assert tools.before_shop_state() == {"done": False, "weekly_plan_id": None}




# ---------------------------------------------------------------------------
# 2. The routes
# ---------------------------------------------------------------------------


def test_the_state_rides_on_the_view_the_shop_tab_actually_opens(signed_in):
    """CATCH, and it is the bug the first cut had. groLoadAllData reads
    /api/grocery-list/by-store for the needed half; the plain
    /api/grocery-list is only read for the bought one. Stamping the plain
    view alone put the answer somewhere the screen never looks."""
    pid = _plan()
    tools.add_grocery_item("Orzo", "1 box", category="pantry")
    by_store = signed_in.get("/api/grocery-list/by-store?status=needed").json()
    assert by_store["before_shop"] == {"done": False, "weekly_plan_id": pid}
    plain = signed_in.get("/api/grocery-list?status=needed").json()
    assert plain["before_shop"] == by_store["before_shop"], "both needed views agree"




def test_the_route_records_the_pass_and_is_safe_to_call_twice(signed_in):
    """CATCH."""
    pid = _plan()
    first = signed_in.post("/api/grocery-list/before-shop-done")
    assert first.status_code == 200
    assert first.json() == {"done": True, "weekly_plan_id": pid}
    assert signed_in.post("/api/grocery-list/before-shop-done").json() == first.json()



# ---------------------------------------------------------------------------
# 3. What the steps show (app/tools/before_shop.py)
# ---------------------------------------------------------------------------


def _bought(staple_item, days_ago):
    conn = get_conn()
    try:
        sid = conn.execute("SELECT id FROM staples WHERE item = ?", (staple_item,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO staple_events (household_id, staple_id, kind, source, on_date) VALUES (1, ?, 'bought', 'seed', ?)",
            (sid, (household_today() - datetime.timedelta(days=days_ago)).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _choices(steps, part="regulars"):
    return {c["name"]: (c["ticked"], c["reason"]) for c in steps[part]["choices"]}


def test_a_household_with_no_regulars_gets_the_starter_set_unticked():
    """CATCH. The card's starter list, in its order; no history, no tick
    and no reason."""
    steps = tools.before_shop_steps()
    assert steps["regulars"]["starter"] is True
    assert [c["name"] for c in steps["regulars"]["choices"]] == tools.before_shop.STARTER_REGULARS
    assert all(c["ticked"] is False and c["reason"] == "" for c in steps["regulars"]["choices"])


def test_regulars_are_pre_ticked_from_what_pomona_already_knows():
    """CATCH. The card's rule of thumb, one row per branch: in inventory ->
    unticked with where; bought longer ago than it usually lasts -> ticked;
    bought recently -> unticked with when; no history -> unticked, silent."""
    for item, cat in [("Coffee", "pantry"), ("Cream", "dairy"), ("Bread", "pantry"), ("Dish soap", "household")]:
        tools.add_staple(item, category=cat)
    _bought("Coffee", 20)
    _bought("Bread", 3)
    tools.update_inventory_items([{"item": "Cream", "quantity": "1 carton", "location": "fridge"}], action="add")
    got = _choices(tools.before_shop_steps())
    assert got["Coffee"] == (True, "usually lasts 2 weeks")
    assert got["Cream"] == (False, "in the fridge")
    assert got["Bread"] == (False, "bought 3 days ago")
    assert got["Dish soap"] == (False, "")


def test_a_regular_already_on_the_list_is_not_a_choice_and_says_who_and_when():
    """CATCH. Emily's change on the card: anything already on this week's
    list moves to its own "Already on the list" section, with who added it
    and when — "you" for the person looking."""
    tools.add_staple("Milk", category="dairy")
    tools.add_staple("Eggs", category="dairy")
    me = tools.add_member("Emily")["member_id"]
    tools.add_member("Ravi")
    conn = get_conn()
    conn.execute("UPDATE members SET age_group = 'adult'")
    conn.commit()
    conn.close()
    with tools.use_member(me):
        tools.add_grocery_item("Milk", "2 L", category="dairy")
        tools.add_grocery_item("Eggs", "12", category="dairy", added_by="Ravi")
        steps = tools.before_shop_steps()
    assert steps["regulars"]["choices"] == []
    on = {r["name"]: r["who"] for r in steps["regulars"]["on_list"]}
    assert on == {"Milk": "you added it today", "Eggs": "Ravi added it today"}


def test_regulars_ticked_in_step_1_go_on_the_list_once_and_undo_takes_them_off():
    """CATCH — the card's own test: staples added in step 1 appear on the
    list. A starter name becomes a staple; a name already on the list never
    gets a second line; Undo takes off exactly what was added."""
    tools.add_grocery_item("Milk", "2 L", category="dairy")
    out = tools.add_regulars(["Coffee", "Milk", "coffee"])
    assert [a["item"] for a in out["added"]] == ["Coffee"]
    needed = [i["item"] for i in tools.list_grocery_list(status="needed")]
    assert sorted(needed) == ["Coffee", "Milk"]
    assert {s["item"] for s in tools.list_staples()} >= {"Coffee", "Milk"}
    tools.undo_add_regulars([a["item_id"] for a in out["added"]])
    assert [i["item"] for i in tools.list_grocery_list(status="needed")] == ["Milk"]


def test_a_spice_tapped_in_step_2_becomes_a_needed_line():
    """CATCH — the card's own test. Step 2's chip is the existing spice
    tick, so the line it makes is an ordinary needed one, and on the next
    read it is "Already on the list", not a choice."""
    item_id = tools.add_grocery_item("Garam masala", "1 tsp", category="pantry", added_by="ai")["item_id"]
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET status = 'spice' WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    assert [c["name"] for c in tools.before_shop_steps()["spices"]["choices"]] == ["Garam masala"]
    tools.tick_spice(item_id, ticked=True)
    assert [i["item"] for i in tools.list_grocery_list(status="needed")] == ["Garam masala"]
    spices = tools.before_shop_steps()["spices"]
    assert spices["choices"] == [] and [r["name"] for r in spices["on_list"]] == ["Garam masala"]


def test_step_3_pre_ticks_the_flags_but_never_a_line_somebody_added_by_hand():
    """CATCH. The pre-shop flags are step 3's rows, ticked; a line a person
    put on the list starts unticked and says who added it — "never offers
    to drop an item a person added by hand this week without saying so"."""
    pid = _plan(approved=True)
    onion = tools.add_grocery_item("Onions", "3", category="produce", added_by="ai")["item_id"]
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET source_weekly_plan_id = ? WHERE id = ?", (pid, onion))
    conn.commit()
    conn.close()
    tools.add_grocery_item("Rice", "1 lb", category="pantry", added_by="Ravi")
    tools.update_inventory_items([{"item": "Onions", "quantity": "6"}, {"item": "Rice", "quantity": "5 lb"}], action="add")
    rows = {r["name"]: (r["ticked"], r["reason"]) for r in tools.before_shop_steps()["have"]["rows"]}
    assert rows["Onions"][0] is True
    assert rows["Rice"] == (False, "Ravi added it today")


def test_the_steps_route_answers_signed_in(signed_in):
    """GUARD on the one read the screen makes."""
    body = signed_in.get("/api/grocery-list/before-shop").json()
    assert set(body) == {"regulars", "spices", "have"}
    added = signed_in.post("/api/grocery-list/before-shop/regulars", json={"items": ["Tea"]}).json()["added"]
    assert [a["item"] for a in added] == ["Tea"]


# ---------------------------------------------------------------------------
# 4. The screen (node, the region's own functions)
# ---------------------------------------------------------------------------

_SCREEN = """
function bsPayload() {
  return {
    regulars: {
      choices: [
        { name: 'Coffee', staple_id: 1, ticked: true, reason: 'usually lasts 2 weeks' },
        { name: 'Cream', staple_id: 2, ticked: false, reason: 'in the fridge' }
      ],
      on_list: [{ item_id: 50, name: 'Milk', who: 'you added it Wednesday', quantity: '2 L', category: 'dairy' }]
    },
    spices: { choices: [{ item_id: 60, name: 'Garam masala' }], on_list: [{ item_id: 61, name: 'Ghee', who: 'since Tuesday' }] },
    have: { rows: [{ item_id: 2, name: 'Thing 2', ticked: true, reason: 'bought Sep 26' },
                   { item_id: 3, name: 'Thing 3', ticked: false, reason: 'Ravi added it Monday' }] }
  };
}
function ready(payload) {
  const data = setUp(5, [], ['Costco', 'Loblaws']);
  data.beforeShop = { done: false, weekly_plan_id: 7 };
  groceryState.bsData = payload || bsPayload();
  return data;
}
function stepTitle() { const m = /gro-bs-title">([^<]*)</.exec(screenHtml()); return m ? m[1] : null; }
function dockOf() { return groDockHtml(groceryState.data, groScreenStep()); }
"""


def _screen(body: str):
    res = nodeharness.run_node(STUB + cook_progress() + grocery_block() + CLICK + FIXTURE + _SCREEN + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@needs_node
def test_the_dock_says_before_you_shop_until_the_pass_is_done_then_sort_the_list_n():
    """CATCH. The tester could not find the sort: the list's one apricot now
    names the next thing."""
    out = _screen("""
ready();
const first = dockOf();
groceryState.data.beforeShop.done = true;
const after = dockOf();
console.log(JSON.stringify({ first: first, after: after }));
""")
    assert 'data-gro="goto-beforeshop"' in out["first"] and "Before you shop" in out["first"]
    assert 'data-gro="goto-sort"' in out["after"] and "Sort the list (5)" in out["after"]


@needs_node
def test_the_steps_appear_in_order_and_the_last_button_is_sort_the_list():
    """CATCH — the card's own test: regulars, then spices and oils, then
    already-have-it, one primary each, ending on Sort the list."""
    out = _screen("""
ready();
clickIfRendered({ gro: 'goto-beforeshop' });
const seen = [];
seen.push([stepTitle(), dockOf()]);
clickIfRendered({ gro: 'bs-next' });          // "None this week"
seen.push([stepTitle(), dockOf()]);
clickIfRendered({ gro: 'bs-next' });          // spices' "Next"
seen.push([stepTitle(), dockOf()]);
console.log(JSON.stringify(seen));
""")
    titles = [s[0] for s in out]
    assert titles == ["Need any of your regulars?", "Out of any of these?", "Already have these?"]
    assert "Add 1 to the list" in out[0][1] and "None this week" in out[0][1]
    assert ">Next<" in out[1][1]
    assert "Sort the list" in out[2][1] and "Keep them all on the list" in out[2][1]
    for _title, dock in out:
        assert dock.count("dock-primary") == 1, "one apricot per screen"


@needs_node
def test_already_on_the_list_is_its_own_quiet_section_and_asks_before_taking_off():
    """CATCH. Emily's change: no pill, a section at the foot of the step;
    a tap asks "Take milk off the list?" before anything is written."""
    out = _screen("""
ready();
clickIfRendered({ gro: 'goto-beforeshop' });
const html = screenHtml();
clickIfRendered({ gro: 'bs-off', id: '50' });
const asking = screenHtml();
const postsBefore = POSTS.length;
clickIfRendered({ gro: 'bs-off-yes', id: '50', kind: 'line' });
settle(function () {
  console.log(JSON.stringify({
    html: html, asking: asking, wrote: POSTS.slice(postsBefore).map(function (p) { return p.url; }),
    after: screenHtml(), toast: lastToast()
  }));
});
""")
    assert "Already on the list" in out["html"] and "you added it Wednesday" in out["html"]
    assert out["html"].index("Already on the list") > out["html"].index("Cream"), "at the bottom of the step"
    assert "Take milk off the list?" not in out["html"]
    assert "Take milk off the list?" in out["asking"]
    assert out["wrote"] == ["/api/grocery-list/50/remove"]
    assert 'data-gro="bs-off" data-id="50"' not in out["after"], "the row leaves the section"
    assert out["toast"]["msg"] == "Milk was taken off the list" and out["toast"]["action"] == "Undo"


@needs_node
def test_ticked_regulars_are_posted_and_a_tapped_spice_is_written_at_once():
    """CATCH. Step 1's primary sends what is ticked (Pomona's pre-tick plus
    the person's); step 2's chip writes on the tap and says so."""
    out = _screen("""
ready();
clickIfRendered({ gro: 'goto-beforeshop' });
clickIfRendered({ gro: 'bs-reg-tick', idx: '1' });
clickIfRendered({ gro: 'bs-reg-go' });
settle(function () {
  const regPost = posts('/before-shop/regulars')[0];
  const title2 = stepTitle();
  clickIfRendered({ gro: 'bs-spice', id: '60' });
  settle(function () {
    console.log(JSON.stringify({
      reg: regPost && regPost.body, title2: title2,
      spice: posts('/api/grocery-list/60/spice').map(function (p) { return p.body; }),
      said: /gro-bs-said" role="status">([^<]*)</.exec(screenHtml())[1]
    }));
  });
});
""")
    assert out["reg"] == {"items": ["Coffee", "Cream"]}
    assert out["title2"] == "Out of any of these?"
    assert out["spice"] == [{"ticked": True}]
    assert out["said"] == "Garam masala goes on the list."


@needs_node
def test_items_ticked_in_step_3_leave_the_sort_count():
    """CATCH — the card's own test. The footer counts what will be left to
    sort, and the primary drops the ticked rows (and keeps the rest)."""
    out = _screen("""
ready();
clickIfRendered({ gro: 'goto-beforeshop' });
clickIfRendered({ gro: 'bs-next' });
clickIfRendered({ gro: 'bs-next' });
const foot = /gro-bs-foot">([^<]*)</.exec(screenHtml())[1];
const line = /gro-bs-line">([^<]*)</.exec(screenHtml())[1];
clickIfRendered({ gro: 'bs-have-go' });
settle(function () {
  console.log(JSON.stringify({
    foot: foot, line: line,
    drops: POSTS.filter(function (p) { return /\\/pre-shop$/.test(p.url); }).map(function (p) { return [p.url, p.body.decision]; }),
    step: groceryState.step, finished: posts('/before-shop-done').length
  }));
});
""")
    assert out["foot"] == "Only 4 things left to sort, down from 5."
    assert out["line"].startswith("Pomona thinks you have 1 of these at home.")
    assert sorted(out["drops"]) == [["/api/grocery-list/2/pre-shop", "drop"], ["/api/grocery-list/3/pre-shop", "keep"]]
    assert out["step"] == "sortall" and out["finished"] == 1


@needs_node
def test_skipping_every_step_writes_nothing_but_the_once_a_week_stamp():
    """CATCH. "Nothing here is required; skipping every step lands on the
    same list as today." The only write is the stamp that stops the pass
    coming back this week."""
    out = _screen("""
ready();
clickIfRendered({ gro: 'goto-beforeshop' });
clickIfRendered({ gro: 'bs-next' });
clickIfRendered({ gro: 'bs-next' });
clickIfRendered({ gro: 'bs-next' });
settle(function () {
  console.log(JSON.stringify({ urls: POSTS.map(function (p) { return p.url; }), step: groceryState.step }));
});
""")
    assert out["urls"] == ["/api/grocery-list/before-shop-done"]
    assert out["step"] == "sortall"


@needs_node
def test_a_step_with_nothing_to_ask_is_skipped_and_the_frame_takes_a_step_in_front():
    """CATCH. Step 3 is skipped entirely when there's nothing to show, so
    spices' button becomes the sort. And the frame reads no step by name:
    a step put at the FRONT (step 0, "Update your inventory?", is coming)
    is simply the first screen."""
    out = _screen("""
const p = bsPayload(); p.have.rows = [];
ready(p);
clickIfRendered({ gro: 'goto-beforeshop' });
clickIfRendered({ gro: 'bs-next' });
const spicesDock = dockOf();
groceryState.step = 'list';
BEFORE_SHOP_STEPS.unshift({ key: 'zero', title: 'Step zero', line: '', has: function () { return true; },
  body: function () { return ''; }, dock: null });
clickIfRendered({ gro: 'goto-beforeshop' });
console.log(JSON.stringify({ spicesDock: spicesDock, first: stepTitle() }));
""")
    assert "Sort the list" in out["spicesDock"]
    assert out["first"] == "Step zero"
