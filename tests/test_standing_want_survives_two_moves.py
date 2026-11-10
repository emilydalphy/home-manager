"""
A household's own pound of beef survives a batch cook moving twice.

Loop Board bug (Low), 2026-10-09: "Moving a batch cook twice turns the
household's own 1 lb of beef into '12 oz'". Hand-added 1 lb of ground beef,
a cook feeding two later nights; drop the cook night (the cook moves onto
the first fed night), then drop that one (it moves onto the last). The card
read the list as "12 oz" and the pound as gone, coming back once the last
night went too.

NOT REPRODUCIBLE AS A LOSS on main (1328b1a), and these pin why. After the
second move the list holds TWO rows named "Ground beef": the household's
own "1 lb" (standing, source_weekly_plan_id NULL, restated correctly by
_restate_standing_want) and the plan's "12 oz" on a plan-owned line of its
own. Twelve ounces is under a pound, so the re-ingest's amount is written
in oz, and a plan's share in another unit never joins a household's line
(_merge_target / _try_consolidate_quantity — kept that way on purpose, see
the "Same-family units add up on a plan's line" decision-log entry: summing
across units on a standing want ratchets it). Read the list as
{item: quantity} and the second row overwrites the first — "12 oz", the
pound "gone", and "back" when the 12 oz row is deleted with the last night.
That is exactly the card's symptom, and it is the reading, not the list.

So these read ROWS, never a dict keyed by name.
"""
from __future__ import annotations

import datetime
import json

from conftest import household_today
from app import tools
from app.db import get_conn

TODAY = household_today()
WEEK_START = (TODAY - datetime.timedelta(days=1)).isoformat()


def D(n: int) -> str:
    return (TODAY + datetime.timedelta(days=n)).isoformat()


def _ids(day: str, slot: str) -> list[int]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ? ORDER BY id",
        (tools.household_id(), day, slot)).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _beef() -> list[tuple[str, bool]]:
    """Every needed Ground beef row, as (quantity, is the household's own)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity, source_weekly_plan_id FROM grocery_items "
        "WHERE household_id = ? AND status = 'needed' AND item = 'Ground beef' ORDER BY id",
        (tools.household_id(),)).fetchall()
    conn.close()
    return [(r["quantity"], r["source_weekly_plan_id"] is None) for r in rows]


def _week() -> tuple[int, int]:
    """Four at the table, a recipe for four wanting 12 oz, a hand-added
    1 lb, and tomorrow's dinner cooked for two later dinners too (the
    card's Tuesday/Thursday/Friday, put on the household clock). Approved:
    1 lb + three nights' 2.25 lbs = 3.25 lbs, one line."""
    for name in ("Emily", "Vineeth", "Kid", "Baby"):
        tools.add_member(name)
    tools.add_recipe(
        "Beef Ragu",
        ingredients=[{"item": "Ground beef", "qty": "12 oz", "category": "meat"}],
        default_servings=4, instructions=["Brown the beef."],
        prep_time_minutes=10, cook_time_minutes=20,
    )
    tools.add_grocery_item("Ground beef", quantity="1 lb", category="meat")
    plan = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    cook, fed = D(1), [D(3), D(4)]
    tools.plan_meal(cook, "Beef Ragu", slot="dinner", weekly_plan_id=plan)
    source = _ids(cook, "dinner")[-1]
    reheats = []
    for day in fed:
        tools.plan_meal(day, "Beef Ragu", slot="dinner", weekly_plan_id=plan)
        reheats.append(_ids(day, "dinner")[-1])
    # Written after the plan_meal calls, never between them: each opens its
    # own connection, and an open write here would lock it out.
    conn = get_conn()
    for entry_id in reheats:
        conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                     (json.dumps({"links_to": f"{cook}:dinner"}), entry_id))
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"make_double_for": [f"{d}:dinner" for d in fed]}), source))
    conn.commit()
    conn.close()
    assert source in tools.plan_leftover_chains(plan)["sources"]
    tools.approve_weekly_plan(plan)
    assert _beef() == [("3.25 lbs", True)]
    return plan, source


def test_the_households_pound_is_still_on_the_list_after_two_moves():
    """The card's sequence through the Review "−". The pound is its own
    line, the plan's one remaining night is 12 oz beside it — 1.75 lbs in
    all, nothing of the household's taken."""
    plan, source = _week()

    assert tools.drop_dish_from_day(plan, source)["moved_to"] == D(3)
    assert _beef() == [("2.5 lbs", True)]

    assert tools.drop_dish_from_day(plan, source)["moved_to"] == D(4)
    assert _beef() == [("1 lb", True), ("12 oz", False)]

    # The last night going takes only the plan's line with it.
    tools.drop_dish_from_day(plan, source)
    assert _beef() == [("1 lb", True)]


def test_undoing_both_moves_puts_one_line_back_at_its_size():
    """The undo record restores the line it changed, and the plan's own
    12 oz line goes with it — never 2.5 lbs AND 12 oz."""
    plan, source = _week()
    first = tools.drop_dish_from_day(plan, source)
    second = tools.drop_dish_from_day(plan, source)

    assert tools.drop_dish_undo(plan, second["undo_entry_id"])["status"] == "restored"
    assert _beef() == [("2.5 lbs", True)]
    assert tools.drop_dish_undo(plan, first["undo_entry_id"])["status"] == "restored"
    assert _beef() == [("3.25 lbs", True)]


def test_everyone_out_twice_moves_the_cook_without_taking_the_pound():
    """The card's other door: nobody home on the cook night, then on the
    night it moved to. Same shared rescale, same answer."""
    plan, source = _week()

    tools.set_slot_need(D(1), "dinner", "away")
    tools.set_slot_need(D(3), "dinner", "away")

    assert _ids(D(4), "dinner") == [source], "the cook moved twice, onto the last night"
    assert _beef() == [("1 lb", True), ("12 oz", False)]


# ---------- Shop: the two rows are ONE line (Emily 2026-11-09) ----------
#
# "One line, 1¾ lbs": the household's own amount and the plan's amount of
# the same thing are one Shop row with the combined amount. Display only
# (app/tools/week_lines.py stamps the pair with one `week_group`; the
# screen's groCollapseWeeks draws it as one row and every tap goes to both
# ids). Underneath, the standing want stays its own row — the tests above
# keep reading rows for exactly that.

import shop_harness  # noqa: E402


def _beef_on_shop(client, status: str = "needed") -> list[dict]:
    if status == "needed":
        body = client.get("/api/grocery-list/by-store?status=needed").json()
        items = [it for st in body["stores"] for s in st["sections"] for it in s["items"]]
    else:
        body = client.get(f"/api/grocery-list?status={status}").json()
        items = [it for s in body["sections"] for it in s["items"]]
    return [it for it in items if it["item"] == "Ground beef"]


def _two_moves() -> tuple[int, int]:
    """The card's state: the pound beside the plan's 12 oz. (plan line id, own line id)"""
    plan, source = _week()
    tools.drop_dish_from_day(plan, source)
    tools.drop_dish_from_day(plan, source)
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, source_weekly_plan_id FROM grocery_items WHERE household_id = ? AND item = 'Ground beef' "
        "AND status = 'needed' ORDER BY id", (tools.household_id(),)).fetchall()
    conn.close()
    own = [r["id"] for r in rows if r["source_weekly_plan_id"] is None]
    planned = [r["id"] for r in rows if r["source_weekly_plan_id"] is not None]
    assert len(own) == 1 and len(planned) == 1
    return planned[0], own[0]


def test_shop_shows_one_ground_beef_line_of_one_and_three_quarter_pounds(signed_in):
    planned, own = _two_moves()
    lines = _beef_on_shop(signed_in)
    assert len(lines) == 2, "two rows underneath"
    group = lines[0]["week_group"]
    assert lines[1]["week_group"] == group
    assert group["lead_id"] == planned and group["ids"] == [planned, own]
    assert group["total"] == "1.75 lbs"
    assert group["note"] == "", "one plan and the household's own: just the amount"
    assert [(p["id"], p["quantity"], p["label"]) for p in group["parts"]] == [
        (planned, "12 oz", "this week"), (own, "1 lb", "your own")]
    # Whatever counts the list counts it as Shop draws it.
    from app.tools import week_lines
    assert len(week_lines.as_shop_rows(lines)) == 1
    # Display only: the household's own pound is still its own row.
    assert _beef() == [("1 lb", True), ("12 oz", False)]


def test_ticking_the_line_ticks_both_and_unticking_puts_both_back(signed_in):
    planned, own = _two_moves()
    for i in (planned, own):  # what the screen sends for one tap (groIdsFor)
        assert signed_in.post(f"/api/grocery-list/{i}/status", json={"status": "purchased"}).status_code == 200
    assert _beef_on_shop(signed_in) == []
    ticked = _beef_on_shop(signed_in, "bought")
    assert ticked and ticked[0]["week_group"]["ids"] == [planned, own]
    assert ticked[0]["week_group"]["total"] == "1.75 lbs"
    for i in (planned, own):
        assert signed_in.post(f"/api/grocery-list/{i}/status", json={"status": "needed"}).status_code == 200
    back = _beef_on_shop(signed_in)
    assert back[0]["week_group"]["ids"] == [planned, own]
    assert _beef() == [("1 lb", True), ("12 oz", False)]


def _raw_line(item: str, qty: str, plan: int | None, staple: int | None = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, category, added_by, source_weekly_plan_id, status, staple_id) "
        "VALUES (?, ?, ?, 'meat', 'Emily', ?, 'needed', ?)",
        (tools.household_id(), item, qty, plan, staple))
    conn.commit()
    conn.close()
    return cur.lastrowid


def test_amounts_that_dont_add_up_stay_two_lines_and_so_does_a_regular(signed_in):
    plan = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    # "1 bag" beside "12 oz" is two things to buy, never "1 bag + 12 oz".
    bag = _raw_line("Ground beef", "1 bag", None)
    oz = _raw_line("Ground beef", "12 oz", plan)
    # A regular's line stands beside the week's (2026-10-24) — never folded.
    conn = get_conn()
    staple = conn.execute(
        "INSERT INTO staples (household_id, item, category, next_due_at) VALUES (?, 'Chicken thighs', 'meat', '2026-01-01')",
        (tools.household_id(),)).lastrowid
    conn.commit()
    conn.close()
    regular = _raw_line("Chicken thighs", "1 lb", None, staple=staple)
    _raw_line("Chicken thighs", "8 oz", plan)
    body = signed_in.get("/api/grocery-list/by-store?status=needed").json()
    rows = {it["id"]: it for st in body["stores"] for s in st["sections"] for it in s["items"]}
    assert "week_group" not in rows[bag] and "week_group" not in rows[oz]
    assert "week_group" not in rows[regular]


_SCREEN = """
const plan = { id: 21, item: 'Ground beef', quantity: '12 oz', store: '', store_decided: 1, category: 'meat', status: 'needed',
  source_weekly_plan_id: 1, week_group: { lead_id: 21, ids: [21, 22], total: '1.75 lbs', note: '',
  parts: [{ id: 21, quantity: '12 oz', label: 'this week' }, { id: 22, quantity: '1 lb', label: 'your own' }] } };
const own = Object.assign({}, plan, { id: 22, quantity: '1 lb', source_weekly_plan_id: null });
groceryState.data = { stores: { Unassigned: { sections: [{ section: 'meat', items: groCollapseWeeks([plan, own]) }], purchased: [], inCart: [] } } };
groceryState.usualStores = [];
groceryState.storesPromptDismissed = true;
groceryState.step = 'list';
"""


@shop_harness.needs_node
def test_the_screen_draws_one_line_and_a_tick_or_an_edit_reaches_the_right_rows():
    out = shop_harness.run(_SCREEN + """
const html = screenHtml();
const shown = {
  rows: (html.match(/<span class="gro-name">Ground beef<\\/span>/g) || []).length,
  qty: /gro-name">Ground beef<\\/span><span class="gro-line-qty">([^<]*)</.exec(html)[1],
  note: html.indexOf('gro-line-weeks') !== -1
};
groceryState.openRowId = '21';
const sheet = screenHtml();
const boxes = (sheet.match(/gro-rowmenu-week-label">([^<]*)</g) || []).map(function (m) { return m.replace(/.*>/, '').replace('<', ''); });
clickIfRendered({ gro: 'line-tick', id: '21', bought: '0' });
settle(function () {
  console.log(JSON.stringify(Object.assign(shown, { boxes: boxes, ticked: purchases().sort() })));
});
""")
    assert out["rows"] == 1
    assert out["qty"] == "1.75 lbs"
    assert out["note"] is False
    # Editing the line: one box per row, so the household's own amount is
    # changed only in its own box and the plan's line keeps its ledger.
    assert out["boxes"] == ["This week", "Your own"]
    assert out["ticked"] == ["/api/grocery-list/21/status", "/api/grocery-list/22/status"]
