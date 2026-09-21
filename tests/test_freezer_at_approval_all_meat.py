"""
Loop Board "Freezer question at approval asks about every meat in the week;
a yes takes it off the list and books the defrost" (Emily, 2026-09-19 /
2026-09-21, board F-A).

Emily: "it is for the user to be able to flag if they have meat in the
freezer, that needs to be defrosted in time to be cooked, and takes the
mental energy off the user by asking the question so they don't need to
think about it."

What changed, and the section of this file for each:

  1. meat_items_for_plan lists EVERY meat in the week — the "still to buy"
     rule (the old rule 3) is gone, since approval had just put the whole
     week's meat on the list and the step was empty at the one moment it
     is shown. Each item says whether it has a line (`on_list`) and whether
     the household already said it is frozen (`frozen`).
  2. confirm_frozen_items is two writes per tapped chip: the grocery line
     set aside — the exact write Shop's "Have it" makes
     (pre_shop.drop_grocery_item_pre_shop), marked removed_by 'freezer' —
     and the move booked with the item's own lead. "Nothing frozen" on a
     first answer writes nothing at all.
  3. The way back: "Actually, I need it" on Shop's "Not needed this week"
     foot (pre_shop.undo_pre_shop_drop) puts the line back AND cancels the
     move; reopening the step and un-tapping the chip does the same
     through confirm_frozen_items. Re-tapping is idempotent.
  4. The screen: the two-part "What that means" line and its one-part
     fallback; the title, the line and the quiet answer; the root row's
     "from the freezer"; a chip that starts on when the step is reopened.
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import pytest

from conftest import household_today
from app import db, tools
from app.tools import defrost, pre_shop
from tests import nodeharness
from test_week_seven_tiles import _extract, _extract_var

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


# ---------------------------------------------------------------------------
# Fixtures: a plan for NEXT week, so nothing is too late to thaw for
# ---------------------------------------------------------------------------

def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


NEXT_WEEK = (_monday() + datetime.timedelta(days=7)).isoformat()
NEXT_TUE = (_monday() + datetime.timedelta(days=8)).isoformat()
NEXT_THU = (_monday() + datetime.timedelta(days=10)).isoformat()
NEXT_SAT = (_monday() + datetime.timedelta(days=12)).isoformat()


def _meat(name="Chicken Skewers", item="Chicken Thighs", qty="1 lb"):
    tools.add_recipe(name, ingredients=[{"item": item, "qty": qty, "category": "meat/seafood"}],
                     default_servings=3, prep_time_minutes=10, cook_time_minutes=15)


def _week(*meals):
    plan_id = tools.create_weekly_plan(NEXT_WEEK)["weekly_plan_id"]
    for date_str, dish in (meals or ((NEXT_THU, "Chicken Skewers"),)):
        tools.plan_meal(date_str, dish, slot="dinner", weekly_plan_id=plan_id)
    return plan_id


def _line(item="Chicken Thighs", qty="1 lb", plan_id=None):
    return tools.add_grocery_item(item, quantity=qty, category="meat/seafood",
                                  source_weekly_plan_id=plan_id)["item_id"]


def _row(item_id):
    conn = db.get_conn()
    row = conn.execute("SELECT status, removed_by FROM grocery_items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return (row["status"], row["removed_by"])


def _moves(plan_id=None):
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT task_date, status, description FROM prep_tasks WHERE task_type = 'defrost' "
        + ("AND weekly_plan_id = ? " if plan_id else "") + "ORDER BY task_date",
        (plan_id,) if plan_id else (),
    ).fetchall()
    conn.close()
    return [(r["task_date"], r["status"]) for r in rows]


def _items(plan_id):
    return [(i["item"], i["on_list"], i["frozen"]) for i in defrost.meat_items_for_plan(plan_id)]


def _days_before(date_str, n):
    return (datetime.date.fromisoformat(date_str) - datetime.timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------------
# 1. Every meat is listed, whatever the list says
# ---------------------------------------------------------------------------

def test_a_meat_still_on_the_shopping_list_is_listed_with_its_line():
    """CATCH — red on main, where rule 3 left it off."""
    _meat()
    plan_id = _week()
    _line(plan_id=plan_id)

    assert _items(plan_id) == [("Chicken Thighs", True, False)]


def test_a_meat_with_no_line_is_listed_without_one():
    """The one-part fallback's premise: bought already, or never on the
    list — the chip is still there, with only the fridge half to say."""
    _meat()
    plan_id = _week()

    assert _items(plan_id) == [("Chicken Thighs", False, False)]


def test_approving_the_week_lists_every_meat_with_its_fresh_line():
    """CATCH — Emily's screen: right after Approve, the whole week's meat,
    each with the line approval just wrote. On main this was []."""
    _meat()
    _meat("Beef Tacos", "Ground Beef")
    _meat("Shrimp Skewers", "Shrimp")
    plan_id = _week((NEXT_TUE, "Beef Tacos"), (NEXT_THU, "Chicken Skewers"), (NEXT_SAT, "Shrimp Skewers"))
    tools.approve_weekly_plan(plan_id)

    assert _items(plan_id) == [("Chicken Thighs", True, False), ("Ground Beef", True, False),
                               ("Shrimp", True, False)]


def test_the_route_carries_both_flags(signed_in):
    _meat()
    plan_id = _week()
    _line(plan_id=plan_id)

    body = signed_in.get(f"/api/week/{NEXT_WEEK}/defrost-items").json()
    assert body["weekly_plan_id"] == plan_id
    assert body["items"][0]["on_list"] is True and body["items"][0]["frozen"] is False
    assert body["items"][0]["nights"][0]["move_date"] == _days_before(NEXT_THU, 2)


def test_a_move_the_app_booked_off_tracked_freezer_inventory_is_still_left_off():
    """GUARD — the one "already booked" rule that stays. The app tracked
    the pack in the freezer and booked the move itself
    (sync_defrost_tasks); a chip here would book it a second time."""
    _meat()
    plan_id = _week()
    tools.update_inventory("Chicken Thighs", "add", quantity="1 lb", category="meat/seafood", location="freezer")
    assert defrost.sync_defrost_tasks(plan_id)["inserted"] == 1

    assert _items(plan_id) == []


def test_the_still_to_buy_rule_is_gone_from_the_source():
    src = (REPO / "app" / "tools" / "defrost.py").read_text(encoding="utf-8")
    assert "def _still_to_buy(" not in src
    assert "_grocery_lines_by_item(" in src


# ---------------------------------------------------------------------------
# 2. A yes is two writes; nothing frozen is none
# ---------------------------------------------------------------------------

def test_a_yes_sets_the_line_aside_the_way_have_it_does_and_books_the_move():
    """CATCH — the card's centre. The line lands under "Not needed this
    week" (status removed, the pre-shop drop's write) marked as the
    freezer's, and the move is dated two nights ahead for a standard cut."""
    _meat()
    plan_id = _week()
    line = _line(plan_id=plan_id)

    result = defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])

    assert result["set_aside"] == [line] and result["put_back"] == [] and result["cancelled"] == 0
    assert _row(line) == ("removed", "freezer")
    assert _moves(plan_id) == [(_days_before(NEXT_THU, 2), "pending")]
    assert [d["item"] for d in pre_shop.get_already_have_decisions()] == ["Chicken Thighs"]
    assert pre_shop.get_already_have_decisions()[0]["removed_by"] == "freezer"


@pytest.mark.parametrize("dish, item, nights", [
    ("Shrimp Skewers", "Shrimp", 1),        # small and thin: one night
    ("Chicken Skewers", "Chicken Thighs", 2),  # the standard cut: two
    ("Roast Chicken", "Whole Chicken", 3),  # a large piece: three
])
def test_the_move_is_dated_with_the_items_own_lead(dish, item, nights):
    _meat(dish, item)
    plan_id = _week((NEXT_SAT, dish))
    _line(item, plan_id=plan_id)

    created = defrost.confirm_frozen_items(plan_id, [item])["created"]

    assert [c["task_date"] for c in created] == [_days_before(NEXT_SAT, nights)]


def test_a_yes_on_a_line_in_the_cart_or_the_spice_box_sets_it_aside_too():
    _meat()
    plan_id = _week()
    line = _line(plan_id=plan_id)
    conn = db.get_conn()
    conn.execute("UPDATE grocery_items SET status = 'in_cart'")
    conn.commit()
    conn.close()

    defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])

    assert _row(line) == ("removed", "freezer")


def test_a_yes_with_no_line_books_the_move_alone():
    _meat()
    plan_id = _week()

    result = defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])

    assert result["set_aside"] == [] and len(result["created"]) == 1
    assert _moves(plan_id) == [(_days_before(NEXT_THU, 2), "pending")]


def test_a_plural_spelling_on_the_list_is_set_aside_too():
    _meat()
    plan_id = _week()
    line = _line("Chicken Thigh", plan_id=plan_id)

    defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])

    assert _row(line) == ("removed", "freezer")


def test_a_staple_line_set_aside_here_tells_the_staple_plenty(monkeypatch):
    """GUARD — "the same write Have it makes, so staples stay consistent":
    the pre-shop drop is what runs, staple bookkeeping included."""
    calls = []
    monkeypatch.setattr(pre_shop, "drop_grocery_item_pre_shop",
                        lambda item_id, author="": calls.append((item_id, author)) or {"item_id": item_id, "status": "removed"})
    _meat()
    plan_id = _week()
    line = _line(plan_id=plan_id)

    defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])

    assert calls == [(line, "freezer")]


def test_nothing_frozen_stamps_the_answer_and_writes_nothing_else(signed_in):
    """CATCH — "Nothing frozen — I'm buying it all": defrost_asked_at is
    set, the line stays on the list, no move exists."""
    _meat()
    plan_id = _week()
    line = _line(plan_id=plan_id)

    res = signed_in.post(f"/api/week/{NEXT_WEEK}/defrost-confirm", json={"items": []})

    assert res.status_code == 200
    assert res.json() == {"weekly_plan_id": plan_id, "created": [], "notes": [],
                          "set_aside": [], "put_back": [], "cancelled": 0}
    assert tools.get_weekly_plan(plan_id)["defrost_asked_at"] is not None
    assert _row(line) == ("needed", "")
    assert _moves(plan_id) == []


def test_the_confirm_route_stamps_the_answer_and_returns_both_halves(signed_in):
    _meat()
    plan_id = _week()
    line = _line(plan_id=plan_id)

    res = signed_in.post(f"/api/week/{NEXT_WEEK}/defrost-confirm", json={"items": ["Chicken Thighs"]})

    assert res.status_code == 200
    body = res.json()
    assert body["set_aside"] == [line] and len(body["created"]) == 1
    assert tools.get_weekly_plan(plan_id)["defrost_asked_at"] is not None
    # And the week reads the answer back on the entry the move feeds — what
    # the Plan root's freezer row lists.
    menu = signed_in.get(f"/api/week-menu?weekly_plan_id={plan_id}").json()
    notes = [e["defrost"]["note"] for d in menu["days"] for e in [d.get("dinner")] if e and e.get("defrost")]
    assert notes == ["Move the Chicken Thighs to the fridge — for Thursday's Chicken Skewers."]


# ---------------------------------------------------------------------------
# 3. The way back
# ---------------------------------------------------------------------------

def test_actually_i_need_it_puts_the_line_back_and_cancels_the_move():
    """CATCH — one write path, both halves undone."""
    _meat()
    plan_id = _week()
    line = _line(plan_id=plan_id)
    defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])
    assert _moves(plan_id) and _row(line)[0] == "removed"

    result = pre_shop.undo_pre_shop_drop(line)

    assert result == {"item_id": line, "status": "needed", "moves_cancelled": 1}
    # The mark goes with the removal, so a later soft-remove that names
    # nobody cannot read as the freezer step's.
    assert _row(line) == ("needed", "")
    assert _moves(plan_id) == []
    assert _items(plan_id) == [("Chicken Thighs", True, False)]


def test_the_put_back_route_says_how_many_moves_went(signed_in):
    _meat()
    plan_id = _week((NEXT_TUE, "Chicken Skewers"), (NEXT_THU, "Chicken Skewers"))
    line = _line(plan_id=plan_id)
    signed_in.post(f"/api/week/{NEXT_WEEK}/defrost-confirm", json={"items": ["Chicken Thighs"]})
    assert len(_moves(plan_id)) == 2

    res = signed_in.post(f"/api/grocery-list/{line}/pre-shop-undo")

    assert res.status_code == 200 and res.json()["moves_cancelled"] == 2
    assert _moves(plan_id) == []
    assert _items(plan_id) == [("Chicken Thighs", True, False)]


def test_a_plain_have_it_undone_cancels_no_move():
    """GUARD — only a line the freezer step set aside takes a move with
    it. A person's own "Have it" on Shop, undone, leaves a move the step
    booked separately alone."""
    _meat()
    plan_id = _week()
    line = _line(plan_id=plan_id)
    defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])
    conn = db.get_conn()
    conn.execute("UPDATE grocery_items SET removed_by = 'Emily' WHERE id = ?", (line,))
    conn.commit()
    conn.close()

    result = pre_shop.undo_pre_shop_drop(line)

    assert result["moves_cancelled"] == 0
    assert len(_moves(plan_id)) == 1


def test_a_move_already_done_is_not_cancelled_by_a_put_back():
    """The food is in the fridge whatever the list says; only a pending
    move is a reminder for something no longer true."""
    _meat()
    plan_id = _week()
    line = _line(plan_id=plan_id)
    defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])
    conn = db.get_conn()
    conn.execute("UPDATE prep_tasks SET status = 'done'")
    conn.commit()
    conn.close()

    assert pre_shop.undo_pre_shop_drop(line)["moves_cancelled"] == 0
    assert _moves(plan_id) == [(_days_before(NEXT_THU, 2), "done")]


def test_a_move_the_app_booked_off_inventory_is_never_cancelled_here():
    """GUARD — the sync's rows are the sync's to keep."""
    _meat()
    plan_id = _week()
    tools.update_inventory("Chicken Thighs", "add", quantity="1 lb", category="meat/seafood", location="freezer")
    defrost.sync_defrost_tasks(plan_id)

    assert defrost._release_frozen_item("Chicken Thighs", plan_id) == 0
    assert len(_moves(plan_id)) == 1


def test_reopening_the_step_shows_the_answer_and_a_re_tap_is_idempotent():
    """CATCH — the Plan root's freezer row reopens the step to CHANGE the
    answer, so the chip is offered again, on; answering again the same
    way books nothing twice and sets nothing aside twice."""
    _meat()
    plan_id = _week()
    line = _line(plan_id=plan_id)
    first = defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])
    assert _items(plan_id) == [("Chicken Thighs", True, True)]

    second = defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])

    assert second["set_aside"] == [] and second["put_back"] == [] and second["cancelled"] == 0
    assert second["created"][0]["prep_task_id"] == first["created"][0]["prep_task_id"]
    assert len(_moves(plan_id)) == 1 and _row(line) == ("removed", "freezer")


def test_un_tapping_the_chip_puts_the_line_back_and_cancels_the_move():
    """CATCH — deselecting on a reopened step is the same two facts
    reversed, through the same undo the Shop foot runs."""
    _meat()
    _meat("Beef Tacos", "Ground Beef")
    plan_id = _week((NEXT_TUE, "Beef Tacos"), (NEXT_THU, "Chicken Skewers"))
    chicken = _line(plan_id=plan_id)
    beef = _line("Ground Beef", plan_id=plan_id)
    defrost.confirm_frozen_items(plan_id, ["Chicken Thighs", "Ground Beef"])
    assert len(_moves(plan_id)) == 2

    result = defrost.confirm_frozen_items(plan_id, ["Ground Beef"])

    assert result["put_back"] == [chicken] and result["cancelled"] == 1 and result["set_aside"] == []
    assert _row(chicken)[0] == "needed" and _row(beef) == ("removed", "freezer")
    assert _moves(plan_id) == [(_days_before(NEXT_TUE, 2), "pending")]
    assert _items(plan_id) == [("Chicken Thighs", True, False), ("Ground Beef", True, True)]


def test_nothing_frozen_after_a_yes_takes_everything_back():
    _meat()
    plan_id = _week()
    line = _line(plan_id=plan_id)
    defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])

    result = defrost.confirm_frozen_items(plan_id, [])

    assert result["put_back"] == [line] and result["cancelled"] == 1
    assert _row(line)[0] == "needed" and _moves(plan_id) == []


def test_un_tapping_a_chip_that_had_no_line_cancels_the_move_alone():
    _meat()
    plan_id = _week()
    defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])
    assert _items(plan_id) == [("Chicken Thighs", False, True)]

    result = defrost.confirm_frozen_items(plan_id, [])

    assert result["cancelled"] == 1 and result["put_back"] == []
    assert _moves(plan_id) == []


def test_a_put_back_on_one_week_leaves_another_weeks_move_alone():
    """GUARD — a line the freezer step set aside belongs to its plan."""
    _meat()
    later_week = (_monday() + datetime.timedelta(days=14)).isoformat()
    later = tools.create_weekly_plan(later_week)["weekly_plan_id"]
    tools.plan_meal((_monday() + datetime.timedelta(days=17)).isoformat(), "Chicken Skewers",
                    slot="dinner", weekly_plan_id=later)
    defrost.confirm_frozen_items(later, ["Chicken Thighs"])
    plan_id = _week()
    line = _line(plan_id=plan_id)
    defrost.confirm_frozen_items(plan_id, ["Chicken Thighs"])
    assert len(_moves()) == 2

    assert pre_shop.undo_pre_shop_drop(line)["moves_cancelled"] == 1
    assert _moves(plan_id) == [] and len(_moves(later)) == 1


def test_the_clock_is_still_read_before_the_write_opens():
    """The ordering guard test_defrost_household_clock pins, restated for
    the rewritten function: the plan walk and the clock both resolve
    before this function's own connection."""
    import inspect
    src = inspect.getsource(defrost.confirm_frozen_items)
    assert src.index("_cooker.household_today()") < src.index("conn = get_conn()")
    assert src.index("list(_iter_plan_meat_ingredients(") < src.index("conn = get_conn()")


# ---------------------------------------------------------------------------
# 4. The screen
# ---------------------------------------------------------------------------

def _run(harness: str):
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_DAYNAME_STUB = (
    "function dayName(d, opts){ var names = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];"
    " return names[new Date(d + 'T00:00:00').getDay()]; }\n"
)


def _prelude() -> str:
    return (
        _extract("escapeHtml", SHELL_JS) + "\n"
        + _DAYNAME_STUB
        + "var weekState = { days: [], data: null, selectedIndex: 0, step: 'week' };\n"
        + "var defrostAskState = { planId: null, items: null, selected: {} };\n"
        + _extract_var("WK_ICONS", SHELL_JS) + "\n"
        + _extract("isSnackSlot", SHELL_JS) + "\n"
        + _extract("slotWord", SHELL_JS) + "\n"
        + _extract("defrostAskChipHtml", SHELL_JS) + "\n"
        + _extract("defrostSelectedItems", SHELL_JS) + "\n"
        + _extract("defrostMeaningLines", SHELL_JS) + "\n"
        + _extract("defrostMeaningHtml", SHELL_JS) + "\n"
        + _extract("freezerStepHtml", SHELL_JS) + "\n"
        + "async " + _extract("ensureDefrostAskItems", SHELL_JS) + "\n"
    )


_CHICKEN = {"item": "Chicken thighs", "on_list": True, "frozen": False,
            "nights": [{"date": "2026-09-21", "meal": "Lemon chicken & orzo", "weekday": "Monday",
                        "slot": "dinner", "move_date": "2026-09-19", "move_weekday": "Saturday"}]}
_BEEF = {"item": "Ground beef", "on_list": True, "frozen": False,
         "nights": [{"date": "2026-09-24", "meal": "Tacos", "weekday": "Thursday",
                     "slot": "dinner", "move_date": "2026-09-22", "move_weekday": "Tuesday"}]}
_SALMON_HOME = {"item": "Salmon", "on_list": False, "frozen": False,
                "nights": [{"date": "2026-09-23", "meal": "Salmon", "weekday": "Wednesday",
                            "slot": "dinner", "move_date": "2026-09-21", "move_weekday": "Monday"}]}


@_needs_node
def test_what_that_means_says_both_halves_per_item_and_only_the_fridge_half_without_a_line():
    """CATCH — board F-A's line, and the one-part fallback for a meat with
    no grocery line to take off."""
    lines = _run(_prelude() + f"console.log(JSON.stringify(defrostMeaningLines({json.dumps([_CHICKEN, _BEEF, _SALMON_HOME])})));")
    assert lines == [
        "Chicken thighs → off the shopping list · into the fridge Saturday night, for Monday’s dinner.",
        "Ground beef → off the shopping list · into the fridge Tuesday night, for Thursday’s dinner.",
        "Salmon → into the fridge Monday night, for Wednesday’s dinner.",
    ]


@_needs_node
def test_the_step_has_the_title_the_line_the_apricot_answer_and_the_quiet_one():
    html = _run(_prelude() + f"defrostAskState = {{ planId: 7, items: {json.dumps([_CHICKEN, _BEEF, _SALMON_HOME])}, selected: {{ 'Chicken thighs': true }} }};\n"
                "console.log(JSON.stringify(freezerStepHtml({ weekly_plan_id: 7 })));")
    assert '<h1 class="wk-title">Anything already in the freezer?</h1>' in html
    assert "Tap what you’ve got frozen. I’ll take it off the shopping list and tell you when to move it to the fridge." in html
    assert html.count('class="defrost-chip') == 3 and html.count("defrost-chip is-selected") == 1
    assert "What that means" in html
    assert "Chicken thighs → off the shopping list · into the fridge Saturday night, for Monday’s dinner." in html
    assert "Ground beef →" not in html, "only what is tapped"
    assert 'id="wk-freezer-go">Add to the schedule · Open grocery list</button>' in html
    assert 'id="wk-freezer-none">Nothing frozen — I’m buying it all</button>' in html
    assert html.count("dock-primary") == 1, "one apricot per screen"
    assert "None — all fresh" not in html


@_needs_node
def test_a_chip_the_household_already_said_yes_to_starts_on_when_the_items_land():
    """CATCH — reopening the step from the Plan root's freezer row reads
    the answer back, so an un-tap can take it back."""
    items = [dict(_CHICKEN, frozen=True), _BEEF, dict(_SALMON_HOME, frozen=True)]
    out = _run(_prelude()
               + "var rendered = 0; function renderMealsStep() { rendered++; }\n"
               + f"async function fetch() {{ return {{ ok: true, json: async function () {{ return {{ items: {json.dumps(items)} }}; }} }}; }}\n"
               + "weekState.step = 'freezer';\n"
               + "defrostAskState.selected = { 'Ground beef': true };\n"  # a stale tap from an earlier plan must not survive
               + "ensureDefrostAskItems(null, { weekly_plan_id: 7, week_start_date: '2026-09-21' }).then(function () {"
               + "  console.log(JSON.stringify({ selected: defrostAskState.selected, rendered: rendered,"
               + "    chosen: defrostSelectedItems().map(function (it) { return it.item; }) })); });")
    assert out["selected"] == {"Chicken thighs": True, "Salmon": True}
    assert out["chosen"] == ["Chicken thighs", "Salmon"]
    assert out["rendered"] == 1


@_needs_node
def test_the_roots_freezer_row_reads_from_the_freezer():
    harness = (
        _extract("escapeHtml", SHELL_JS) + "\n"
        + "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
        + _extract_var("WK_ICONS", SHELL_JS) + "\n"
        + _extract("isSnackSlot", SHELL_JS) + "\n"
        + _extract("snackSlotKey", SHELL_JS) + "\n"
        + _extract("daySlotEntry", SHELL_JS) + "\n"
        + _extract("daySlotKeys", SHELL_JS) + "\n"
        + _extract("weekFrozenItems", SHELL_JS) + "\n"
        + _extract("weekFreezerRowHtml", SHELL_JS) + "\n"
    )
    days = [
        {"date": "2026-09-21", "dinner": {"title": "Lemon chicken", "entry_id": 1,
                                          "defrost": {"date": "2026-09-19", "note": "Move the Chicken thighs to the fridge — for Monday's Lemon chicken."}}},
        {"date": "2026-09-24", "dinner": {"title": "Tacos", "entry_id": 2,
                                          "defrost": {"date": "2026-09-22", "note": "Move the ground beef to the fridge — for Thursday's Tacos."}}},
    ]
    html = _run(harness + f"console.log(JSON.stringify(weekFreezerRowHtml({{ defrost_asked_at: '2026-09-20T10:00' }}, {json.dumps(days)})));")
    assert "Chicken thighs, ground beef — from the freezer" in html
    assert "in the schedule" not in html


def test_actually_i_need_it_says_put_back_and_re_reads_the_week_when_a_move_went():
    """Source markers for the handler behind the foot's button: one toast,
    and the Plan root / Today re-read only when a move was cancelled."""
    start = SHELL_JS.index("case 'undo-already-have':")
    handler = SHELL_JS[start:SHELL_JS.index("case 'elsewhere-back':", start)]
    assert "/pre-shop-undo" in handler
    assert "showToast('Put back.')" in handler
    assert "body.moves_cancelled" in handler
    assert "loadWeekMenu(panels.week)" in handler and "refreshTodayMoves()" in handler


def test_the_not_needed_foot_names_a_freezer_line_as_such():
    foot = _extract("groNotNeededHtml", SHELL_JS)
    assert "it.removed_by === 'freezer'" in foot and "from the freezer" in foot
