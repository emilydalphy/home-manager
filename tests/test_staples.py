"""
Staples — "tell me before we run out" (Loop Board, Emily, 2026-09-11).

The first feature built straight from the mental load model: Pomona takes
the *noticing* ("we're nearly out of dish soap") without asking anyone to
keep inventory. These tests pin the promises the card makes:

- a staple has a cadence (default by category, or told, or learned from
  bought dates — median interval, once there are two);
- when it is probably due it becomes ONE ordinary grocery line, in its
  section, linked by staple_id, the moment the list is read;
- nothing is added twice, and a line a person already added counts;
- "we have plenty" and "not this trip" each take the line off and move
  the due date the right distance; three skips running pauses it; Undo
  reverses exactly one answer and puts the line back;
- buying it — by any route — teaches the rhythm and resets the skips;
- none of it touches inventory_items.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from app import tools
from app.db import get_conn
from app.tools import staples as st

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    """Every test starts on a fixed Wednesday, and can move the clock with
    `travel(days)`. Real date.today() would make the due-date maths drift."""
    base = date(2026, 9, 16)
    monkeypatch.setattr(st, "_TODAY_OVERRIDE", base)
    yield


def travel(days: int) -> date:
    st._TODAY_OVERRIDE = st._TODAY_OVERRIDE + timedelta(days=days)
    return st._TODAY_OVERRIDE


def needed_names() -> list[str]:
    return sorted(r["item"] for r in tools.list_grocery_list(status="needed"))


def staple_lines() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT id, item, staple_id, added_by, status FROM grocery_items WHERE staple_id IS NOT NULL").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def inventory_count() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM inventory_items").fetchone()[0]
    conn.close()
    return n


# ------------------------------------------------------------- adding ----


def test_add_staple_defaults_cadence_by_category_and_is_not_due_yet():
    s = tools.add_staple("Dish soap", category="household")
    assert s["created"] is True
    assert s["cadence_days"] == st.DEFAULT_CADENCE_DAYS["household"]
    assert s["cadence_source"] == "default"
    assert s["due"] is False
    assert s["last_bought_at"] == "2026-09-16"
    assert s["next_due_at"] == "2026-10-16"
    assert s["cadence_words"] == "about every month"


def test_add_staple_with_a_told_cadence_uses_it():
    s = tools.add_staple("Coffee beans", every_days=14, category="pantry")
    assert s["cadence_days"] == 14
    assert s["cadence_source"] == "told"
    assert s["cadence_words"] == "about every 2 weeks"


def test_add_staple_running_low_is_due_today_and_lands_on_the_list():
    s = tools.add_staple("Toilet paper", category="household", running_low=True)
    assert s["due"] is True
    assert s["due_words"] == "probably running low"
    # Reading the list is what puts it there — the one place the household
    # is already looking.
    tools.get_grocery_list_by_section(status="needed")
    assert needed_names() == ["Toilet paper"]
    (line,) = staple_lines()
    assert line["added_by"] == st.ADDED_BY_STAPLE
    assert line["staple_id"] == s["id"]


def test_adding_the_same_staple_twice_updates_not_duplicates():
    a = tools.add_staple("coffee beans", category="pantry")
    b = tools.add_staple("Coffee Beans", every_days=10)
    assert b["created"] is False and b["id"] == a["id"]
    assert b["cadence_days"] == 10 and b["cadence_source"] == "told"
    assert len(tools.list_staples()) == 1


def test_add_staple_seeds_history_from_purchased_lines_and_learns():
    # Three purchased "coffee" lines two weeks apart, made before the staple
    # existed — each line's created_at stands in for a bought date.
    conn = get_conn()
    for d in ("2026-08-05", "2026-08-19", "2026-09-02"):
        conn.execute(
            "INSERT INTO grocery_items (household_id, item, status, created_at) VALUES (?, ?, 'purchased', ?)",
            (tools.household_id(), "Coffee", d + " 10:00:00"),
        )
    conn.commit()
    conn.close()
    s = tools.add_staple("coffee", category="pantry")
    assert s["cadence_source"] == "learned"
    assert s["cadence_days"] == 14
    assert s["last_bought_at"] == "2026-09-02"
    assert s["next_due_at"] == "2026-09-16"
    assert s["due"] is True  # today, on the fixture's clock


def test_add_staple_never_creates_inventory():
    tools.add_staple("Cat litter", category="household", running_low=True)
    tools.get_grocery_list_by_section(status="needed")
    assert inventory_count() == 0


# ---------------------------------------------------------- surfacing ----


def test_due_staple_becomes_one_line_and_only_one():
    tools.add_staple("Oat milk", category="dairy")
    travel(8)  # past the 7-day dairy default
    tools.get_grocery_list_by_section(status="needed")
    tools.get_grocery_list_by_section(status="needed")
    tools.get_grocery_list_by_store(status="needed")
    assert needed_names() == ["Oat milk"]
    assert len(staple_lines()) == 1


def test_due_staple_sits_in_its_own_section():
    tools.add_staple("Oat milk", category="dairy", running_low=True)
    view = tools.get_grocery_list_by_section(status="needed")
    sections = {s["section"]: [it["item"] for it in s["items"]] for s in view["sections"]}
    assert sections == {"dairy": ["Oat milk"]}
    # And the row the screen gets carries the link it renders the flag from.
    (row,) = view["sections"][0]["items"]
    assert row["staple_id"] is not None


def test_a_line_the_household_already_added_is_left_alone():
    tools.add_staple("Coffee", category="pantry", running_low=True)
    tools.add_grocery_item("coffee", quantity="1 bag", category="pantry")
    tools.get_grocery_list_by_section(status="needed")
    assert needed_names() == ["coffee"]
    assert staple_lines() == []  # Pomona added nothing of its own


def test_a_paused_staple_is_never_suggested():
    s = tools.add_staple("Oat milk", category="dairy", running_low=True)
    tools.pause_staple(s["id"], paused=True)
    tools.get_grocery_list_by_section(status="needed")
    assert needed_names() == []
    shown = tools.list_staples()[0]
    assert shown["paused"] is True and shown["due_words"] == "paused"


def test_reading_purchased_or_in_cart_views_does_not_surface_anything():
    tools.add_staple("Oat milk", category="dairy", running_low=True)
    tools.get_grocery_list_by_section(status="purchased")
    tools.get_grocery_list_by_store(status="in_cart")
    assert staple_lines() == []


# --------------------------------------------------------- deciding ----


def _due_line(name="Oat milk", category="dairy", **kw) -> tuple[dict, dict]:
    s = tools.add_staple(name, category=category, running_low=True, **kw)
    tools.get_grocery_list_by_section(status="needed")
    (line,) = staple_lines()
    return s, line


def test_we_have_plenty_removes_the_line_and_pushes_a_whole_cadence():
    s, line = _due_line(every_days=21)
    out = tools.decide_staple_line(line["id"], "plenty")
    assert out["decision"] == "plenty"
    assert needed_names() == []
    assert out["next_due_at"] == "2026-10-07"  # today + 21
    assert out["skip_streak"] == 0
    assert out["cadence_days"] == 21  # the rhythm itself is untouched
    # Reading the list again does not bring it straight back.
    tools.get_grocery_list_by_section(status="needed")
    assert needed_names() == []


def test_not_this_trip_removes_the_line_and_asks_again_in_a_week():
    s, line = _due_line(every_days=21)
    out = tools.decide_staple_line(line["id"], "skip")
    assert needed_names() == []
    assert out["next_due_at"] == "2026-09-23"
    assert out["skip_streak"] == 1 and out["paused"] is False and out["just_paused"] is False


def test_three_skips_running_pause_the_staple_visibly():
    s, line = _due_line()
    tools.decide_staple_line(line["id"], "skip")
    for _ in range(2):
        travel(7)
        tools.get_grocery_list_by_section(status="needed")
        (line,) = staple_lines()
        out = tools.decide_staple_line(line["id"], "skip")
    assert out["skip_streak"] == 3 and out["paused"] is True and out["just_paused"] is True
    travel(30)
    tools.get_grocery_list_by_section(status="needed")
    assert needed_names() == []  # paused means quiet
    # ...and resuming brings it back, streak cleared.
    back = tools.pause_staple(s["id"], paused=False)
    assert back["paused"] is False and back["skip_streak"] == 0
    tools.get_grocery_list_by_section(status="needed")
    assert needed_names() == ["Oat milk"]


def test_undo_reverses_exactly_one_answer_and_puts_the_line_back():
    s, line = _due_line(every_days=21)
    tools.decide_staple_line(line["id"], "skip")
    assert needed_names() == []
    out = tools.undo_staple_decision(s["id"])
    assert out["skip_streak"] == 0 and out["paused"] is False
    assert needed_names() == ["Oat milk"]
    conn = get_conn()
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM staple_events WHERE staple_id = ? ORDER BY id", (s["id"],))]
    conn.close()
    assert "skipped" not in kinds


def test_undo_after_the_third_skip_also_lifts_the_pause():
    s, line = _due_line()
    for i in range(3):
        if i:
            travel(7)
            tools.get_grocery_list_by_section(status="needed")
            (line,) = staple_lines()
        tools.decide_staple_line(line["id"], "skip")
    assert tools.list_staples()[0]["paused"] is True
    out = tools.undo_staple_decision(s["id"])
    assert out["paused"] is False and out["skip_streak"] == 2
    assert needed_names() == ["Oat milk"]


def test_deciding_on_a_line_that_is_not_a_staple_is_refused():
    tools.add_grocery_item("Bananas", category="produce")
    (row,) = tools.list_grocery_list(status="needed")
    with pytest.raises(ValueError):
        tools.decide_staple_line(row["id"], "plenty")


def test_plenty_by_name_from_chat():
    s, line = _due_line(every_days=14)
    out = tools.mark_staple_plenty("oat milk")
    assert out["found"] is True and out["next_due_at"] == "2026-09-30"
    assert needed_names() == []


# ---------------------------------------------------------- buying ----


def test_buying_the_suggested_line_records_a_purchase_and_resets_skips():
    s, line = _due_line(every_days=21)
    tools.decide_staple_line(line["id"], "skip")
    tools.undo_staple_decision(s["id"])
    (line,) = staple_lines()
    tools.mark_grocery_item(line["id"], "purchased")
    shown = tools.list_staples()[0]
    assert shown["last_bought_at"] == "2026-09-16"
    assert shown["next_due_at"] == "2026-10-07"
    assert shown["skip_streak"] == 0
    assert shown["due"] is False


def test_buying_a_hand_added_line_teaches_the_staple_too():
    tools.add_staple("Coffee", category="pantry")
    tools.add_grocery_item("coffee", category="pantry")  # same thing on the merge key (case)
    (row,) = tools.list_grocery_list(status="needed")
    travel(20)
    tools.mark_grocery_item(row["id"], "purchased")
    assert tools.list_staples()[0]["last_bought_at"] == "2026-10-06"


def test_cadence_is_learned_from_the_median_of_real_intervals():
    s = tools.add_staple("Coffee", category="pantry")  # default 21; adding is not a purchase
    # Bought after 10 days, then 12 later, then 11, then a 40-day gap (a
    # holiday): the intervals are [12, 11, 40], median 12 — the outlier
    # doesn't drag it, and the mean (21) would have.
    for gap in (10, 12, 11, 40):
        travel(gap)
        tools.add_grocery_item("Coffee", category="pantry")
        (row,) = [r for r in tools.list_grocery_list(status="needed") if r["item"] == "Coffee"]
        tools.mark_grocery_item(row["id"], "purchased")
    shown = tools.list_staples()[0]
    assert shown["cadence_source"] == "learned"
    assert shown["cadence_days"] == 12
    assert shown["last_bought_at"] == "2026-11-28"


def test_one_interval_is_not_enough_to_learn_from():
    tools.add_staple("Coffee", every_days=30, category="pantry")
    travel(5)
    tools.add_grocery_item("Coffee", category="pantry")
    (row,) = tools.list_grocery_list(status="needed")
    tools.mark_grocery_item(row["id"], "purchased")
    shown = tools.list_staples()[0]
    assert shown["cadence_source"] == "told" and shown["cadence_days"] == 30


def test_two_purchases_on_one_day_are_one_purchase():
    tools.add_staple("Coffee", category="pantry")
    travel(14)
    for _ in range(2):
        tools.add_grocery_item("Coffee", category="pantry")
        (row,) = tools.list_grocery_list(status="needed")
        tools.mark_grocery_item(row["id"], "purchased")
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM staple_events WHERE kind = 'bought'").fetchone()[0]
    conn.close()
    assert n == 1  # one bought date, not two; adding the staple was not a purchase


def test_buying_a_non_staple_changes_nothing_here():
    tools.add_grocery_item("Bananas", category="produce")
    (row,) = tools.list_grocery_list(status="needed")
    tools.mark_grocery_item(row["id"], "purchased")
    assert tools.list_staples() == []


# ------------------------------------ the other ways a line leaves ----
# Found by the 2026-09-11 build verifier: a staple's line taken off by the
# row's ordinary Remove, or by a pre-shop "Drop it", came straight back on
# the next list read, because the staple was still due. Each of these
# fails on the first cut of the feature.


def test_removing_the_line_by_the_row_menu_counts_as_not_this_trip():
    s, line = _due_line(every_days=21)
    tools.remove_grocery_item(line["id"])
    tools.get_grocery_list_by_section(status="needed")
    assert needed_names() == []  # no boomerang
    shown = tools.list_staples()[0]
    assert shown["skip_streak"] == 1 and shown["next_due_at"] == "2026-09-23"
    # ...and the staple's own Undo puts the same row back.
    tools.undo_staple_decision(s["id"])
    assert needed_names() == ["Oat milk"]


def test_pre_shop_drop_counts_as_we_have_plenty():
    s, line = _due_line(every_days=21)
    tools.drop_grocery_item_pre_shop(line["id"], author="Emily")
    tools.get_grocery_list_by_section(status="needed")
    assert needed_names() == []
    shown = tools.list_staples()[0]
    assert shown["next_due_at"] == "2026-10-07" and shown["skip_streak"] == 0
    # The pre-shop screen's own undo still works on that row.
    tools.undo_pre_shop_drop(line["id"])
    assert needed_names() == ["Oat milk"]
    tools.get_grocery_list_by_section(status="needed")
    assert len(tools.list_grocery_list(status="needed")) == 1  # not two lines


def test_undo_puts_back_the_same_row_with_its_store():
    s, line = _due_line(every_days=21)
    tools.set_grocery_item_store(line["id"], "Costco")
    tools.decide_staple_line(line["id"], "skip")
    tools.undo_staple_decision(s["id"])
    (row,) = tools.list_grocery_list(status="needed")
    assert row["id"] == line["id"] and row["store"] == "Costco"


def test_undo_with_nothing_to_undo_changes_nothing():
    s = tools.add_staple("Coffee", category="pantry")  # not due for 21 days
    out = tools.undo_staple_decision(s["id"])
    assert out["undone"] is False and out["due"] is False
    tools.get_grocery_list_by_section(status="needed")
    assert needed_names() == []


def test_yesterdays_answer_does_not_block_a_staple_that_is_due_again():
    s, line = _due_line(every_days=21)
    tools.decide_staple_line(line["id"], "skip")  # due again in 7 days
    travel(8)
    tools.get_grocery_list_by_section(status="needed")
    assert needed_names() == ["Oat milk"]
    assert len(staple_lines()) == 1  # the old removed row was tidied away


def test_unknown_category_becomes_other():
    s = tools.add_staple("Batteries", category="kitchen sink")
    assert s["category"] == "other"


# --------------------------------------------------------- removing ----


def test_remove_staple_keeps_the_line_but_drops_the_link():
    s, line = _due_line()
    out = tools.remove_staple("Oat Milk")
    assert out["removed"] is True
    assert tools.list_staples() == []
    assert needed_names() == ["Oat milk"]  # still a thing to buy this trip
    assert staple_lines() == []


# --------------------------------------------------------------- API ----


def test_api_round_trip(signed_in):
    r = signed_in.post("/api/staples/add", json={"item": "Dish soap", "category": "household", "running_low": True})
    assert r.status_code == 200
    sid = r.json()["id"]
    view = signed_in.get("/api/grocery-list?status=needed").json()
    rows = [it for s in view["sections"] for it in s["items"]]
    assert [it["item"] for it in rows] == ["Dish soap"]
    assert rows[0]["staple_id"] == sid
    d = signed_in.post(f"/api/grocery-list/{rows[0]['id']}/staple", json={"decision": "skip"})
    assert d.status_code == 200 and d.json()["skip_streak"] == 1
    assert signed_in.get("/api/grocery-list?status=needed").json()["sections"] == []
    u = signed_in.post(f"/api/staples/{sid}/undo")
    assert u.status_code == 200 and u.json()["skip_streak"] == 0
    listed = signed_in.get("/api/staples").json()["staples"]
    assert listed[0]["item"] == "Dish soap" and listed[0]["cadence_words"] == "about every month"
    assert signed_in.post(f"/api/staples/{sid}/pause").json()["paused"] is True
    assert signed_in.post(f"/api/staples/{sid}/resume").json()["paused"] is False
    assert signed_in.post(f"/api/staples/{sid}/remove").json()["removed"] is True
    assert signed_in.get("/api/staples").json()["staples"] == []


def test_api_rejects_a_bad_decision(signed_in):
    r = signed_in.post("/api/grocery-list/1/staple", json={"decision": "maybe"})
    assert r.status_code == 400


def test_api_needs_a_signed_in_household(client):
    assert client.get("/api/staples").status_code in (401, 303, 307)


# ------------------------------------------------- the chat tools ----


def test_the_four_staple_tools_are_offered_to_the_model():
    from app import agent

    names = {d["name"] for d in agent.TOOL_DEFINITIONS}
    for n in ("add_staple", "list_staples", "mark_staple_plenty", "remove_staple"):
        assert n in names and n in agent.TOOL_FUNCTIONS
    assert "add_staple" in agent.SYSTEM_PROMPT


# ------------------------------------------- the Grocery screen ----


def test_grocery_screen_source_markers():
    """shell.js has no JS harness (see tests/test_grocery_steps.py); these are
    the tripwires for what the screen renders."""
    for needle in (
        "function groStapleLineHtml",
        "Probably running low",
        'data-gro="staple-decide" data-decision="plenty"',
        'data-gro="staple-decide" data-decision="skip"',
        "We have plenty",
        "Not this trip",
        "Make it a staple",
        "function groStaplesHtml",
        "/api/grocery-list/' + stLineId + '/staple",
        "/api/staples/' + stResult.id + '/undo",
        "groLoadStaples()",
    ):
        assert needle in SHELL_JS, needle
    for cls in (".gro-staple-line", ".gro-staple-btn", ".gro-staples", ".gro-staple-row", ".gro-rowmenu-staple"):
        assert cls in SHELL_CSS, cls
    # Rule 9: no literal colours in the new CSS.
    tail = SHELL_CSS[SHELL_CSS.index("---------- Staples (app/tools/staples.py)"):]
    assert "#" not in tail.replace("§", ""), "a literal colour crept into the staples CSS"
