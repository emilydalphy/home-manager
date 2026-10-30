"""
Loop Board B5 (Option B screen 4, Emily 2026-10-26): when a skipped dinner
leaves food going off, Today offers a one-tap swap — and something going
off with no plan for it gets one quiet "use soon" line.

Server: tools/going_off.py (today_going_off, swap_for_going_off,
leave_going_off, undo_going_off_swap) and its four /api/today/going-off
routes. Client: renderGoingOffCard / renderGoingOffLine in shell.js.
Every test here fails on main, where none of it exists.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from conftest import household_today
from app import tools
from app.db import get_conn
from app.tools import going_off, leftovers

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_WD = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _d(n: int) -> str:
    return (household_today() + datetime.timedelta(days=n)).isoformat()


def _wd(n: int) -> str:
    return _WD[(household_today() + datetime.timedelta(days=n)).weekday()]


def _plan(start: int = -2, days: int = 6, approved: bool = True) -> int:
    """An approved plan from two days ago (by default) on the household's
    clock — a period named by its days, never a Monday-anchored week, so
    no weekday can fall the wrong side of it."""
    plan = tools.create_weekly_plan(_d(start), day_count=days)["weekly_plan_id"]
    if approved:
        conn = get_conn()
        conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan,))
        conn.commit()
        conn.close()
    return plan


def _recipe(name: str, *items: str):
    tools.add_recipe(name, ingredients=[{"item": i, "qty": "1", "category": "produce"} for i in items],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=4)


def _meal(plan, n, name, derived=None, recipe_items=None):
    if recipe_items is not None:
        _recipe(name, *recipe_items)
    return tools.plan_meal(_d(n), name, slot="dinner", weekly_plan_id=plan, derived_from=derived)["entry_id"]


def _stock(item: str, turns_in: int, category: str = "produce") -> int:
    tools.update_inventory(item, "add", quantity="1", expiration_date=_d(turns_in),
                           category=category, location="fridge")
    conn = get_conn()
    row = conn.execute("SELECT id FROM inventory_items WHERE item = ? ORDER BY id DESC LIMIT 1", (item,)).fetchone()
    conn.close()
    return row["id"]


def _dinner_rows(plan):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT mpe.id, mpe.date, mpe.slot_state, mpe.skipped_at, mpe.reasoning, mpe.derived_from_json, "
        "COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.slot = 'dinner' ORDER BY mpe.date, mpe.id", (plan,))]
    conn.close()
    return rows


def _by_date(plan):
    out = {}
    for r in _dinner_rows(plan):
        out.setdefault(r["date"], []).append(r)
    return out


def _mockup_scene():
    """Option B screen 4 exactly: yesterday's pasta didn't happen, the
    spinach turns tomorrow, tonight is leftover chili (cooked two nights
    ago), and tomorrow is free."""
    plan = _plan()
    _recipe("Chili", "Kidney beans", "Ground beef")
    cook = _meal(plan, -2, "Chili", derived={"make_double_for": [f"{_d(0)}:dinner"]})
    tools.check_off_meal(cook, "done")
    reheat = _meal(plan, 0, "Chili", derived={"links_to": f"{_d(-2)}:dinner"})
    assert set(leftovers.plan_leftover_chains(plan)["leftovers"]) == {reheat}, "premise: a real chain"
    pasta = _meal(plan, -1, "Spinach Pasta", recipe_items=["Baby spinach, chopped", "Penne"])
    spinach = _stock("Spinach", 1)
    return plan, pasta, cook, reheat, spinach


def _double_cook_week():
    """The other shape: the pasta two nights ago didn't happen, the
    spinach turns tomorrow, tonight is the chili cooked double for
    tomorrow (and nowhere free to move it)."""
    plan = _plan()
    pasta = _meal(plan, -2, "Spinach Pasta", recipe_items=["Baby spinach, chopped", "Penne"])
    _recipe("Chili", "Kidney beans", "Ground beef")
    chili = _meal(plan, 0, "Chili", derived={"make_double_for": [f"{_d(1)}:dinner"]})
    fed = _meal(plan, 1, "Chili", derived={"links_to": f"{_d(0)}:dinner"})
    assert set(leftovers.plan_leftover_chains(plan)["leftovers"]) == {fed}, "premise: a real chain"
    spinach = _stock("Spinach", 1)
    return plan, pasta, chili, fed, spinach


# The name this helper had at 1a05681, which the verify pass's probes import.
_mockup_week = _double_cook_week


# ---------------------------------------------------------------- the card

def test_the_mockup_scene_yesterday_card_leads_then_the_swap_card():
    plan, pasta, cook, reheat, spinach = _mockup_scene()
    # While "Did you have it?" is asking about the pasta, nothing here
    # contradicts it: no swap card, and no line about the spinach.
    before = tools.today_going_off()
    assert before["card"] is None and before["line"] is None
    tools.answer_yesterday(pasta, "skipped", household_today())
    card = tools.today_going_off()["card"]
    assert card["title"] == "The spinach is still home"
    assert card["body"] == (
        f"{_wd(-1)}’s Spinach Pasta didn’t happen, and the spinach turns tomorrow. "
        f"Swap tonight to the Spinach Pasta and do the leftover Chili {_wd(1)}?"
    )
    assert card["swap_label"] == "Swap them" and card["leave_label"] == "Leave it"
    assert card["entry_id"] == pasta and card["item_id"] == spinach
    assert card["kind"] == "move" and card["to"] == _d(1)

    out = tools.swap_for_going_off(spinach, pasta)
    assert out["said"] == f"Spinach Pasta moved to tonight and leftover Chili to {_wd(1)}."
    rows = _by_date(plan)
    assert [r["id"] for r in rows[_d(0)]] == [pasta]
    assert [r["id"] for r in rows[_d(1)]] == [reheat]
    chains = leftovers.plan_leftover_chains(plan)
    assert chains["leftovers"][reheat]["source"]["entry_id"] == cook, "still the chili's leftovers"
    back = tools.undo_going_off_swap(out["undo_entry_id"])
    assert back["said"] == "Spinach Pasta and leftover Chili are back where they were."
    assert [r["id"] for r in _by_date(plan)[_d(0)]] == [reheat]


def test_leftovers_never_move_past_the_leftovers_keep_window():
    """Tonight's leftovers may move on only while still within
    leftovers.MAX_LEFTOVER_DAYS of their cook on the new night (the nights
    swap's own age rule). Cooked three days ago, the next free night would
    make them four days old: no card, just the line."""
    plan = _plan(start=-3, days=6)
    _recipe("Chili", "Kidney beans")
    cook = _meal(plan, -3, "Chili", derived={"make_double_for": [f"{_d(0)}:dinner"]})
    tools.check_off_meal(cook, "done")
    _meal(plan, 0, "Chili", derived={"links_to": f"{_d(-3)}:dinner"})
    pasta = _meal(plan, -2, "Spinach Pasta", recipe_items=["Spinach"])
    _stock("Spinach", 1)
    out = tools.today_going_off()
    assert out["card"] is None, "the chili would be four days old tomorrow"
    assert out["line"]["text"] == "Spinach is best used by tomorrow."
    assert pasta


def test_a_double_cook_tonight_lands_on_its_leftovers_night_and_the_extra_is_frozen():
    plan, pasta, chili, fed, spinach = _double_cook_week()
    card = tools.today_going_off()["card"]
    assert card["body"] == (
        f"{_wd(-2)}’s Spinach Pasta didn’t happen, and the spinach turns tomorrow. "
        f"Swap tonight to the Spinach Pasta and do the Chili {_wd(1)}?"
    )
    assert card["kind"] == "cook_on_fed" and card["to"] == _d(1)
    out = tools.swap_for_going_off(spinach, pasta)
    assert out["said"] == f"Spinach Pasta moved to tonight and Chili to {_wd(1)}. The extra goes in the freezer."
    conn = get_conn()
    frozen = conn.execute("SELECT id FROM inventory_items WHERE location = 'freezer' AND item LIKE 'Chili%'").fetchall()
    conn.close()
    assert len(frozen) == 1
    tools.undo_going_off_swap(out["undo_entry_id"])
    conn = get_conn()
    left = conn.execute("SELECT id FROM inventory_items WHERE location = 'freezer' AND item LIKE 'Chili%'").fetchall()
    conn.close()
    assert left == [], "Undo takes the freezer portion back"


def test_the_yesterday_undo_refuses_a_going_off_swap():
    """/api/today/yesterday/undo with the swap's holder id used to restore the
    plan and leave the freezer portion behind. Only the swap's own undo may
    take it back."""
    plan, pasta, chili, fed, spinach = _double_cook_week()
    before = {r["id"]: r["date"] for r in _dinner_rows(plan)}
    out = tools.swap_for_going_off(spinach, pasta)
    after = {r["id"]: r["date"] for r in _dinner_rows(plan)}
    refused = tools.undo_skipped_move(out["undo_entry_id"])
    assert refused["status"] == "refused"
    assert {r["id"]: r["date"] for r in _dinner_rows(plan)} == after, "nothing moved"
    back = tools.undo_going_off_swap(out["undo_entry_id"])
    assert back["status"] == "restored"
    assert {r["id"]: r["date"] for r in _dinner_rows(plan)} == before
    conn = get_conn()
    left = conn.execute("SELECT id FROM inventory_items WHERE location = 'freezer'").fetchall()
    conn.close()
    assert left == []


def test_leftovers_of_a_skipped_cook_are_not_a_plan_for_its_food():
    """Verify pass, 2026-10-27: the pasta's own leftovers night, still
    ahead, read as a plan for the spinach — no card, no line."""
    plan = _plan()
    pasta = _meal(plan, -2, "Spinach Pasta", derived={"make_double_for": [f"{_d(2)}:dinner"]},
                  recipe_items=["Spinach"])
    _meal(plan, 2, "Spinach Pasta", derived={"links_to": f"{_d(-2)}:dinner"})
    _recipe("Chili", "Kidney beans")
    _meal(plan, 0, "Chili")
    _stock("Spinach", 1)
    out = tools.today_going_off()
    assert out["card"] is not None and out["card"]["entry_id"] == pasta


@pytest.mark.parametrize("ingredient,item", [
    ("Butter", "Peanut butter"), ("Peanut butter", "Butter"),
    ("Milk", "Coconut milk"), ("Coconut milk", "Milk"),
    ("Cream", "Sour cream"), ("Potatoes", "Sweet potatoes"),
])
def test_the_matcher_never_takes_a_different_food_for_the_same_word(ingredient, item):
    assert not going_off._matches(ingredient, {"item": item})


@pytest.mark.parametrize("ingredient,item", [
    ("Baby spinach, chopped", "Spinach"), ("Spinach", "Baby spinach"),
    ("Eggs", "Egg"), ("Fresh basil", "Basil"), ("Feta, crumbled", "Feta"),
    # Re-verify, 2026-10-27: descriptors are read before singularizing
    # ("boneless" is not "boneles"), and "leaves" is the form, not the food.
    ("Boneless skinless chicken thighs", "Chicken thighs"),
    ("Chicken thighs", "Boneless skinless chicken thighs"),
    ("Fresh spinach leaves", "Spinach"), ("Spinach", "Baby spinach leaves"),
])
def test_the_matcher_still_finds_the_same_food(ingredient, item):
    assert going_off._matches(ingredient, {"item": item})


def test_the_item_is_lowercased_mid_sentence_but_names_keep_their_capitals():
    assert going_off._mid("Cremini Mushrooms") == "cremini mushrooms"
    assert going_off._mid("Brussels sprouts") == "Brussels sprouts"


def test_a_bare_weekday_is_never_said_twice_for_two_different_days():
    today = household_today().isoformat()
    week_ago_plus = _d(-6)
    # Six days back and moving to tomorrow: the same weekday, two days.
    assert going_off._skipped_day(week_ago_plus, _d(1), today) == f"Last {_wd(1)}"
    assert going_off._skipped_day(_d(-2), _d(1), today) == _wd(-2)
    assert going_off._skipped_day(_d(-7), None, today) == f"Last {_wd(0)}"


def test_only_one_card_for_the_most_urgent_item():
    plan = _plan()
    _meal(plan, -2, "Spinach Pasta", recipe_items=["Spinach"])
    _meal(plan, -1, "Feta Salad", recipe_items=["Feta"])  # last night, answered below
    _recipe("Chili", "Kidney beans")
    _meal(plan, 0, "Chili")
    feta_row = _by_date(plan)[_d(-1)][0]["id"]
    tools.answer_yesterday(feta_row, "skipped", household_today())
    _stock("Spinach", 1)
    feta = _stock("Feta", 0, category="dairy")
    out = tools.today_going_off()
    assert out["card"]["item_id"] == feta, "the feta turns today, the spinach tomorrow"
    assert out["card"]["title"] == "The feta is still home"
    assert "turns today" in out["card"]["body"]


def test_yesterdays_unanswered_dinner_is_left_to_the_yesterday_card():
    plan = _plan()
    entry = _meal(plan, -1, "Spinach Pasta", recipe_items=["Spinach"])
    _recipe("Chili", "Kidney beans")
    _meal(plan, 0, "Chili")
    _stock("Spinach", 1)
    assert tools.today_going_off()["card"] is None
    tools.answer_yesterday(entry, "skipped", household_today())
    assert tools.today_going_off()["card"]["entry_id"] == entry


def test_a_dinner_that_was_cooked_is_not_a_skipped_one():
    plan, pasta, chili, fed, spinach = _double_cook_week()
    tools.check_off_meal(pasta, "done")
    out = tools.today_going_off()
    assert out["card"] is None


def test_food_a_later_dinner_still_uses_gets_no_card_and_no_line():
    # On or before the day it turns (tomorrow) — tomorrow's lunch, since
    # its dinner is the chili's reheat. A meal after that is no plan for
    # it — tests/test_going_off_hunt_fixes.py.
    plan, pasta, chili, fed, spinach = _double_cook_week()
    _recipe("Spinach Omelette", "Spinach", "Eggs")
    tools.plan_meal(_d(1), "Spinach Omelette", slot="lunch", weekly_plan_id=plan)
    assert not tools.audit_plan_slots(plan).get("duplicated"), "premise: one meal per slot"
    out = tools.today_going_off()
    assert out["card"] is None and out["line"] is None


def test_leave_it_is_remembered_for_that_item_for_the_day():
    plan, pasta, chili, fed, spinach = _double_cook_week()
    out = tools.leave_going_off(spinach)
    assert out["card"] is None
    assert out["line"] is None, "the household just said they know about the spinach"
    assert tools.today_going_off()["card"] is None
    # Tomorrow is another day's key.
    assert going_off.LEAVE_KEY.format(date=_d(1), item_id=spinach) != \
        going_off.LEAVE_KEY.format(date=_d(0), item_id=spinach)


# ---------------------------------------------------------------- no swap onto empty nights

def test_no_card_when_nobody_is_home_tonight():
    plan = _plan()
    _meal(plan, -2, "Spinach Pasta", recipe_items=["Spinach"])
    tools.plan_slot_empty(plan, _d(0), "dinner", reason="You're out — nothing planned.")
    _stock("Spinach", 1)
    out = tools.today_going_off()
    assert out["card"] is None
    # Still going off, and nothing ahead plans for it: the quiet line.
    assert out["line"]["text"] == "Spinach is best used by tomorrow."


def test_tonights_dinner_never_moves_onto_a_nobody_home_night():
    plan = _plan()
    _meal(plan, -2, "Spinach Pasta", recipe_items=["Spinach"])
    _recipe("Chili", "Kidney beans")
    chili = _meal(plan, 0, "Chili")
    tools.plan_slot_empty(plan, _d(1), "dinner", reason="You're out — nothing planned.")
    tools.plan_slot_open(plan, _d(2), "dinner", open_reason="Nothing fits this night yet.")
    _stock("Spinach", 1)
    card = tools.today_going_off()["card"]
    assert card["kind"] == "move" and card["to"] == _d(2), "past the away night, onto the open one"
    assert card["body"].endswith(f"do the Chili {_wd(2)}?")
    tools.swap_for_going_off(card["item_id"], card["entry_id"])
    rows = _by_date(plan)
    assert [r["slot_state"] for r in rows[_d(1)]] == ["planned_empty"]
    assert [r["id"] for r in rows[_d(2)]] == [chili]


def test_no_card_when_tonights_dinner_has_nowhere_to_go():
    plan = _plan(days=4)
    _meal(plan, -2, "Spinach Pasta", recipe_items=["Spinach"])
    _recipe("Chili", "Kidney beans")
    _recipe("Tacos", "Tortillas")
    _meal(plan, 0, "Chili")
    _meal(plan, 1, "Tacos")
    assert tools.today_going_off()["card"] is None


def test_an_open_tonight_just_takes_the_dish():
    plan = _plan()
    pasta = _meal(plan, -2, "Spinach Pasta", recipe_items=["Spinach"])
    tools.plan_slot_open(plan, _d(0), "dinner", open_reason="Nothing fits tonight yet.")
    _stock("Spinach", 1)
    card = tools.today_going_off()["card"]
    assert card["kind"] == "open"
    assert card["body"].endswith("Make the Spinach Pasta tonight?")
    assert card["swap_label"] == "Make it tonight"
    out = tools.swap_for_going_off(card["item_id"], pasta)
    assert out["said"] == "Spinach Pasta moved to tonight."
    rows = _by_date(plan)
    assert [(r["id"], r["slot_state"]) for r in rows[_d(0)]] == [(pasta, "planned")]


# ---------------------------------------------------------------- swap + undo

def test_swap_them_moves_both_dinners_and_undo_puts_them_back():
    plan, pasta, chili, fed, spinach = _double_cook_week()
    before = {r["id"]: r["date"] for r in _dinner_rows(plan)}

    out = tools.swap_for_going_off(spinach, pasta)
    assert out["status"] == "swapped" and out["can_undo"]
    assert out["said"] == f"Spinach Pasta moved to tonight and Chili to {_wd(1)}. The extra goes in the freezer."
    rows = _by_date(plan)
    # Tonight is the pasta, a cook and no longer skipped.
    [tonight] = rows[_d(0)]
    assert tonight["id"] == pasta and tonight["slot_state"] == "planned" and tonight["skipped_at"] is None
    # The chili is cooked on the night it was feeding; its leftovers row went.
    [tomorrow] = rows[_d(1)]
    assert tomorrow["id"] == chili
    assert "make_double_for" not in json.loads(tomorrow["derived_from_json"] or "{}")
    assert leftovers.plan_leftover_chains(plan)["leftovers"] == {}
    # The night the pasta left is never absent: an empty night saying where it went.
    [left] = rows[_d(-2)]
    assert left["slot_state"] == "planned_empty"
    assert left["reasoning"] == f"Skipped — the Spinach Pasta moved to {_wd(0)}."
    audit = tools.audit_plan_slots(plan, day_count=6)
    assert not [d for d in audit["duplicated"] if d["slot"] == "dinner"]
    # The card has said its piece.
    assert out["going_off"]["card"] is None
    # Yesterday's card never asks about the holder.
    assert all(m["meal"] != "Spinach Pasta" for m in tools.yesterday_check()["meals"])

    back = tools.undo_going_off_swap(out["undo_entry_id"])
    assert back["status"] == "restored"
    assert back["said"] == "Spinach Pasta and Chili are back where they were."
    assert {r["id"]: r["date"] for r in _dinner_rows(plan)} == before
    assert set(leftovers.plan_leftover_chains(plan)["leftovers"]) == {fed}
    assert back["going_off"]["card"]["entry_id"] == pasta, "the question stands again"


def test_swap_through_a_free_night_uses_the_nights_swap():
    plan = _plan()
    pasta = _meal(plan, -2, "Spinach Pasta", recipe_items=["Spinach"])
    _recipe("Chili", "Kidney beans")
    chili = _meal(plan, 0, "Chili")
    spinach = _stock("Spinach", 1)
    card = tools.today_going_off()["card"]
    assert card["kind"] == "move" and card["to"] == _d(1), "no row tomorrow — the next free night"
    out = tools.swap_for_going_off(spinach, pasta)
    rows = _by_date(plan)
    assert [r["id"] for r in rows[_d(0)]] == [pasta]
    assert [r["id"] for r in rows[_d(1)]] == [chili]
    tools.undo_going_off_swap(out["undo_entry_id"])
    rows = _by_date(plan)
    assert [r["id"] for r in rows[_d(0)]] == [chili]
    assert [r["id"] for r in rows[_d(-2)]] == [pasta]


def test_a_stale_tap_is_refused_and_writes_nothing():
    plan, pasta, chili, fed, spinach = _double_cook_week()
    tools.check_off_meal(pasta, "done")
    before = _dinner_rows(plan)
    with pytest.raises(going_off.WeekChanged):
        tools.swap_for_going_off(spinach, pasta)
    assert _dinner_rows(plan) == before


# ---------------------------------------------------------------- the quiet line

def test_something_going_off_with_no_plan_gets_one_quiet_line():
    _stock("Greek yogurt", 0, category="dairy")
    _stock("Milk", 1, category="dairy")
    out = tools.today_going_off()
    assert out["card"] is None
    assert out["line"]["text"] == "Greek yogurt is best today.", "one line, the most urgent"


def test_the_line_says_tomorrow_for_tomorrow():
    _stock("Greek yogurt", 1, category="dairy")
    assert tools.today_going_off()["line"]["text"] == "Greek yogurt is best used by tomorrow."


def test_nothing_urgent_means_no_card_and_no_line():
    plan, pasta, chili, fed, spinach = _double_cook_week()
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET expiration_date = ? WHERE id = ?", (_d(3), spinach))
    conn.commit()
    conn.close()
    _stock("Old milk", -2, category="dairy")  # already past: Kitchen's, not this
    out = tools.today_going_off()
    assert out["card"] is None and out["line"] is None


def test_the_freezer_is_never_going_off_here():
    tools.update_inventory("Chicken thighs", "add", quantity="1", expiration_date=_d(0),
                           category="meat/seafood", location="freezer")
    assert tools.today_going_off()["line"] is None


# ---------------------------------------------------------------- routes

def test_routes_read_swap_undo_and_leave(signed_in):
    plan, pasta, chili, fed, spinach = _double_cook_week()
    res = signed_in.get("/api/today/going-off")
    assert res.status_code == 200 and res.json()["card"]["entry_id"] == pasta
    res = signed_in.post("/api/today/going-off/swap", json={"item_id": spinach, "entry_id": pasta})
    assert res.status_code == 200
    holder = res.json()["undo_entry_id"]
    again = signed_in.post("/api/today/going-off/swap", json={"item_id": spinach, "entry_id": pasta})
    assert again.status_code == 409
    res = signed_in.post("/api/today/going-off/undo", json={"undo_entry_id": holder})
    assert res.status_code == 200 and res.json()["status"] == "restored"
    res = signed_in.post("/api/today/going-off/leave", json={"item_id": spinach})
    assert res.status_code == 200 and res.json()["card"] is None


# ---------------------------------------------------------------- the screen

def test_the_screen_draws_the_card_and_the_line_through_api_js():
    for marker in ('id="going-off-card"', 'id="today-going-off-line"', "loadGoingOff(panel)",
                   "'/api/today/going-off'", "'/api/today/going-off/swap'",
                   "'/api/today/going-off/leave'", "'/api/today/going-off/undo'"):
        assert marker in SHELL_JS, marker
    region = SHELL_JS[SHELL_JS.index("// ---------- Food going off"):SHELL_JS.index("// ---------- The offer to plan a week")]
    assert "fetch('/api" not in region, "new code talks to the server through api.js"
    assert "label: 'Undo'" in region
    # One use-soon at a time: the line stands down while the freezer card is up.
    assert "useSoonState.items" in region
    assert ".today-area-goingoff:empty { display: none; }" in SHELL_CSS
