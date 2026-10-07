"""
Shop: with next week approved early, "Two rows of eggs · Merge" piled up,
and Merge deleted next week's amount (defect hunt 2026-10-07, High).

    This week approved; Plan › Plan next week › questions › Approve. Shop
    opened on 18-27 "Two rows of X · Merge" lines, and Merge on eggs
    (this week's 1 dozen, next week's 2 dozen) left "1 dozen" — next
    week's amount gone from the list and from its meals' ledger.

Root cause: the Shop tab grouped needed lines by lowercased name and its
Merge kept the first line and removed the rest. The list keeps one line per
week ON PURPOSE (docs/DECISION_LOG.md, 2026-10-02), so most of those pairs
were never duplicates. Now the server says which lines would really fold
(tools.mergeable_duplicate_groups — consolidate's own rule: never two
weeks, never two amounts that can't be added), and Merge is a server-side
consolidate of exactly those lines: amounts added, ledger moved across.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import grocery as _grocery

from test_grocery_steps import SHELL_JS


def _today() -> datetime.date:
    return _grocery._household_today()


def _plan(start: datetime.date, days: int = 7) -> int:
    return tools.create_weekly_plan(start.isoformat(), day_count=days)["weekly_plan_id"]


@pytest.fixture
def omelette():
    tools.add_recipe("Omelette", ingredients=[
        {"item": "Eggs", "qty": "12", "category": "dairy"},
        {"item": "Chives", "qty": "1 bunch", "category": "produce"},
    ])


def _two_weeks():
    """This week running with tonight's omelette approved; next week
    approved early with its own omelette. Returns (this, next, tonight)."""
    today = _today()
    this_week = _plan(today - datetime.timedelta(days=2), days=5)
    tonight = tools.plan_meal(today.isoformat(), "Omelette", slot="dinner", weekly_plan_id=this_week)["entry_id"]
    tools.approve_weekly_plan(this_week, approved_by="Emily")
    start = today + datetime.timedelta(days=3)
    nxt = _plan(start)
    tools.plan_meal(start.isoformat(), "Omelette", slot="dinner", weekly_plan_id=nxt)
    tools.plan_meal((start + datetime.timedelta(days=1)).isoformat(), "Omelette", slot="dinner", weekly_plan_id=nxt)
    tools.approve_weekly_plan(nxt, approved_by="Emily")
    return this_week, nxt, tonight


def _needed(item: str) -> list[tuple[str, int | None]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity, source_weekly_plan_id FROM grocery_items "
        "WHERE item = ? AND status = 'needed' ORDER BY id", (item,),
    ).fetchall()
    conn.close()
    return [(r["quantity"], r["source_weekly_plan_id"]) for r in rows]


def _insert(item: str, qty: str, plan: int | None) -> int:
    """A second line the add path would have folded — the shape older data
    and other routes leave behind, which is what Merge is for."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, category, added_by, source_weekly_plan_id, status) "
        "VALUES (1, ?, ?, 'dairy', 'Emily', ?, 'needed')", (item, qty, plan),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def _by_store(client) -> dict:
    r = client.get("/api/grocery-list/by-store?status=needed")
    assert r.status_code == 200
    return r.json()


def _lines(view: dict) -> dict[int, dict]:
    return {it["id"]: it for st in view["stores"] for s in st["sections"] for it in s["items"]}


def test_two_weeks_lines_are_never_offered_as_a_merge(omelette, signed_in):
    """CATCH. The card's repro: both weeks approved, each week's eggs on
    its own line — no "Two rows of" for them, and each line says its week."""
    this_week, nxt, _ = _two_weeks()
    assert _needed("Eggs") == [("1 dozen", this_week), ("2 dozen", nxt)]
    view = _by_store(signed_in)
    lines = _lines(view)
    assert view["duplicates"] == [], "lines from two weeks are not duplicates"
    assert sorted(it["source_weekly_plan_id"] for it in lines.values() if it["item"] == "Eggs") == sorted([this_week, nxt])


def test_merge_posted_across_two_weeks_changes_nothing(omelette, signed_in):
    """GUARD. Even asked directly, the server will not fold two weeks."""
    this_week, nxt, _ = _two_weeks()
    ids = [i for i, it in _lines(_by_store(signed_in)).items() if it["item"] == "Eggs"]
    r = signed_in.post("/api/grocery-list/merge", json={"ids": ids})
    assert r.status_code == 200 and r.json()["lines_merged_away"] == 0
    assert _needed("Eggs") == [("1 dozen", this_week), ("2 dozen", nxt)]


def test_a_same_week_pair_merges_to_the_sum_and_keeps_the_ledger(omelette, signed_in):
    """CATCH. A genuine same-week pair is offered, and Merge adds the
    amounts (the old Merge kept the first and lost the rest) and moves the
    removed line's meal share to the line that stays."""
    today = _today()
    plan = _plan(today - datetime.timedelta(days=1), days=4)
    tonight = tools.plan_meal(today.isoformat(), "Omelette", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.approve_weekly_plan(plan, approved_by="Emily")
    first = next(iter(i for i, it in _lines(_by_store(signed_in)).items() if it["item"] == "Eggs"))
    # Swap the ids' roles: the meal's line is the one Merge folds away.
    extra = _insert("eggs", "1 dozen", plan)
    conn = get_conn()
    conn.execute("UPDATE meal_plan_grocery_links SET grocery_item_id = ? WHERE grocery_item_id = ?", (extra, first))
    conn.commit()
    conn.close()

    view = _by_store(signed_in)
    assert view["duplicates"] == [[first, extra]]
    r = signed_in.post("/api/grocery-list/merge", json={"ids": [first, extra]})
    assert r.status_code == 200 and r.json()["lines_merged_away"] == 1
    assert _needed("Eggs") == [("2 dozen", plan)], "the sum, not the first line's amount"
    conn = get_conn()
    linked = conn.execute(
        "SELECT grocery_item_id FROM meal_plan_grocery_links WHERE meal_plan_entry_id = ? AND item = 'Eggs'",
        (tonight,),
    ).fetchall()
    conn.close()
    assert [r[0] for r in linked] == [first], "tonight's eggs share moved with the amount"


def test_amounts_that_cant_be_added_are_not_offered(signed_in):
    """GUARD. "1" beside "2 cups" can't be summed, so a Merge could only
    keep one of them — not offered, and a direct post leaves both."""
    a = _insert("Milk", "1", None)
    b = _insert("milk", "2 cups", None)
    assert _by_store(signed_in)["duplicates"] == []
    signed_in.post("/api/grocery-list/merge", json={"ids": [a, b]})
    assert sorted(q for q, _ in _needed("Milk") + _needed("milk")) == ["1", "2 cups"]


def test_the_shop_tab_reads_the_servers_groups_and_merges_on_the_server():
    """CATCH. The frontend's own same-name grouping and remove-the-rest
    Merge are gone."""
    groups = SHELL_JS.split("function groDuplicateGroups(", 1)[1][:600]
    assert "data.duplicates" in groups
    assert "toLowerCase" not in groups
    handler = SHELL_JS.split("case 'merge': {", 1)[1][:700]
    assert "'/api/grocery-list/merge'" in handler
    assert "/remove" not in handler
    assert "duplicates: byStore.duplicates || []" in SHELL_JS


# ---------- review, 2026-10-07: what the line that stays owns ----------

def _row(line_id: int) -> dict | None:
    conn = get_conn()
    r = conn.execute("SELECT * FROM grocery_items WHERE id = ?", (line_id,)).fetchone()
    conn.close()
    return dict(r) if r else None


def test_a_plan_line_that_takes_a_households_line_becomes_theirs(omelette, signed_in):
    """CATCH. The plan's chives (older id) absorbed the household's own
    "2 bunches" and stayed plan-owned, so dropping the meal deleted the
    line and the household's 2 bunches with it. Now the merged line is a
    standing want and only the meal's share comes off."""
    today = _today()
    plan = _plan(today - datetime.timedelta(days=1), days=4)
    entry = tools.plan_meal(today.isoformat(), "Omelette", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.approve_weekly_plan(plan, approved_by="Emily")
    chives = next(i for i, it in _lines(_by_store(signed_in)).items() if it["item"] == "Chives")
    mine = _insert("chives", "2 bunches", None)
    assert _by_store(signed_in)["duplicates"] == [[chives, mine]]
    signed_in.post("/api/grocery-list/merge", json={"ids": [chives, mine]})
    merged = _row(chives)
    assert merged["quantity"] == "3 bunches" and merged["source_weekly_plan_id"] is None
    _grocery._reverse_meal_grocery_contributions(entry)
    left = _row(chives)
    assert left is not None and left["status"] == "needed", "the household's own chives stay"
    assert left["quantity"] == "2 bunches"


def test_a_merged_staple_line_keeps_its_staple(signed_in):
    """CATCH. The household's oat milk (older id) took the staple's line
    and dropped its staple link, so Remove couldn't say "not this trip" and
    the next read put the staple straight back."""
    mine = _insert("oat milk", "1 carton", None)
    tools.add_staple("Oat milk", every_days=7, quantity="1 carton")
    conn = get_conn()
    staple = {"staple_id": conn.execute("SELECT id FROM staples WHERE item = 'Oat milk'").fetchone()["id"]}
    staple_line = conn.execute(
        "SELECT id FROM grocery_items WHERE staple_id IS NOT NULL AND status = 'needed'").fetchone()
    if staple_line is None:
        cur = conn.execute(
            "INSERT INTO grocery_items (household_id, item, quantity, category, added_by, staple_id, status) "
            "VALUES (1, 'Oat milk', '1 carton', 'dairy', 'Pomona', ?, 'needed')", (staple["staple_id"],))
        staple_line_id = cur.lastrowid
        conn.commit()
    else:
        staple_line_id = staple_line["id"]
    conn.close()
    signed_in.post("/api/grocery-list/merge", json={"ids": [mine, staple_line_id]})
    merged = _row(mine)
    assert merged["quantity"] == "2 cartons"
    assert merged["staple_id"] == staple["staple_id"]
    tools.remove_grocery_item(mine)
    assert _row(mine)["status"] == "removed", "Remove is the staple's 'not this trip'"


def test_a_households_line_never_joins_two_weeks_together(omelette, signed_in):
    """CATCH (review nit). The household's own eggs, lowest id, could take
    this week's line and then next week's — "Three rows of eggs" across two
    weeks. It takes one week at most."""
    this_week, nxt, _ = _two_weeks()
    # The household's own line, older than both weeks' (lowest id), as the
    # ordering of older data leaves it.
    conn = get_conn()
    conn.execute("INSERT INTO grocery_items (id, household_id, item, quantity, category, added_by, status) "
                 "VALUES (0, 1, 'eggs', '1 dozen', 'dairy', 'Emily', 'needed')")
    conn.commit()
    conn.close()
    view = _by_store(signed_in)
    plans = {i: it["source_weekly_plan_id"] for i, it in _lines(view).items()}
    assert view["duplicates"], "the household's line may still join one week"
    for g in view["duplicates"]:
        assert len({plans[i] for i in g} - {None}) <= 1, view["duplicates"]
    signed_in.post("/api/grocery-list/merge", json={"ids": [i for g in view["duplicates"] for i in g]})
    left = _needed("Eggs") + _needed("eggs")
    assert len(left) == 2, "two weeks stay two lines"
    assert any(p == nxt for _, p in left), "next week's line is still its own"
