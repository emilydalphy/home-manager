"""
Loop-handoffs slice 1 (Emily, 2026-09-04): "the process should walk me
through the loop more ... every step should end by offering the next one."

Covers the two backend pieces of that ask that aren't visible from the
shell.js side alone:

- The chat action-card summariser (app/main.py summarize_chat_actions /
  _humanize_change) used to print the category kicker twice on any tool
  call whose readable argument wasn't in _CHANGE_TEXT_FIELDS yet, and
  never told approve_weekly_plan's two changed screens (the week, the
  list) apart. See _CATEGORY_FALLBACK_CHANGES and the approve_weekly_plan
  special case in summarize_chat_actions.
- The "week approved" notification pointed at Meals, even though the
  thing it just said got built lives on Grocery.

NOT covered here, and not covered by any automated test — there is no JS
test harness in this repo (no package.json/jest/mocha, checked 2026-09-05):
the shell.js "that's the shopping done" handoff (groPlanHtml /
groceryState.justFinishedTrip / the 'done-here' click handler). It was
originally gated on groTotals().done > 0, which sums purchased + in_cart
grocery rows over the household's entire LIFETIME — nothing ever resets a
purchased row (clear_stale_grocery_items only touches 'needed' rows), so
that count stays > 0 forever after the first-ever trip. That meant
approving a later week that added zero new items (see
test_approving_the_week_with_nothing_added_skips_the_grocery_card above —
the backend correctly reports groceries_added_count: 0) would still show a
false "that's the shopping done" congratulations on the Grocery tab,
because the list was simply empty, not just-finished. The fix replaced the
lifetime count with groceryState.justFinishedTrip, a page-view-only flag
set true only by a successful finish-this-stop ('done-here') in the
current visit, and cleared either when the list gains needed items again
or when the Grocery tab is left for any other tab (see the flag's
declaration and the activateTab check in static/shell.js). If a JS test
harness is ever added to this repo, that state-transition logic (flag set
on done-here, cleared on restock, cleared on tab-away, and the empty-state
branch reading it instead of groTotals().done) is what should be covered.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import main as app_main
from app import tools


def _today(offset_days: int = 0) -> str:
    return (datetime.date.today() + datetime.timedelta(days=offset_days)).isoformat()


def _week_start() -> str:
    today = datetime.date.today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


def _turn_with_tool_call(name: str, args: dict, result: dict):
    """Minimal before/after history pair in the same shape
    summarize_chat_actions reads off a real run_agent_turn call: one
    assistant tool_use block, one user tool_result block naming it."""
    before: list = []
    after = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": name, "input": args},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": json.dumps(result), "is_error": False},
        ]},
    ]
    return before, after


# ---------- _humanize_change / summarize_chat_actions ----------

def test_a_list_valued_arg_produces_a_readable_change_and_never_the_kicker():
    """add_grocery_items' `items` is a list, not a string — the old field
    scan only ever matched a bare string, so this fell all the way through
    to the kicker twice before this slice."""
    before, after = _turn_with_tool_call(
        "add_grocery_items",
        {"items": ["milk", "eggs", "bread"]},
        {"added": ["milk", "eggs", "bread"], "merged_with_existing": []},
    )
    actions = app_main.summarize_chat_actions(before, after)
    assert len(actions) == 1
    action = actions[0]
    assert action.change != action.kicker
    assert "milk" in action.change
    assert "eggs" in action.change
    assert "+1 more" in action.change


def test_a_two_item_list_is_joined_with_and_not_a_plus_count():
    before, after = _turn_with_tool_call(
        "add_grocery_items", {"items": ["milk", "eggs"]}, {"added": ["milk", "eggs"], "merged_with_existing": []},
    )
    actions = app_main.summarize_chat_actions(before, after)
    assert actions[0].change.endswith("milk and eggs")


def test_restrictions_list_is_the_change_not_the_members_name():
    """set_member_dietary_restrictions(name, restrictions) has both a
    `name` and a `restrictions` field — the restrictions are the actual
    new information, not the member's (already-known) name."""
    before, after = _turn_with_tool_call(
        "set_member_dietary_restrictions",
        {"name": "Jamie", "restrictions": ["gluten-free", "dairy-free"]},
        {"restrictions": ["gluten-free", "dairy-free"]},
    )
    actions = app_main.summarize_chat_actions(before, after)
    assert len(actions) == 1
    assert actions[0].change != actions[0].kicker
    assert "gluten-free" in actions[0].change and "dairy-free" in actions[0].change


def test_swap_meal_in_plan_names_the_new_meal_not_the_kicker_twice():
    before, after = _turn_with_tool_call(
        "swap_meal_in_plan",
        {"weekly_plan_id": 1, "meal_date": _today(), "new_meal": "Tacos", "slot": "dinner"},
        {"status": "ok"},
    )
    actions = app_main.summarize_chat_actions(before, after)
    assert len(actions) == 1
    assert actions[0].change != actions[0].kicker
    assert "Tacos" in actions[0].change


def test_a_tool_with_no_known_field_still_gets_a_distinct_fallback_sentence():
    """The regression this whole file is named for: a write tool whose
    useful arguments aren't in _CHANGE_TEXT_FIELDS used to render the
    category kicker twice ("Household info updated / Household info
    updated") instead of falling back to a different sentence."""
    before, after = _turn_with_tool_call("set_household_meal_preferences", {"unrelated_arg": 1}, {"ok": True})
    actions = app_main.summarize_chat_actions(before, after)
    assert len(actions) == 1
    assert actions[0].change != actions[0].kicker
    assert actions[0].change == app_main._CATEGORY_FALLBACK_CHANGES["memory"]


def test_approving_the_week_raises_a_week_card_and_a_grocery_card():
    """Approving is the one write that changes two screens (the week, and
    the list it just built) — it should read as two action cards, not a
    single "week" one with no mention of the list Emily's ask is walking
    her toward."""
    before, after = _turn_with_tool_call(
        "approve_weekly_plan",
        {"weekly_plan_id": 1, "approved_by": "Emily"},
        {"weekly_plan_id": 1, "status": "approved", "groceries_added_count": 12, "already_have_skipped_count": 2},
    )
    actions = app_main.summarize_chat_actions(before, after)
    by_tab = {a.tab: a for a in actions}
    assert set(by_tab) == {"week", "grocery"}
    assert by_tab["week"].change == "Week approved — your list is ready"
    assert by_tab["grocery"].change == "12 items ready to shop"
    assert by_tab["grocery"].kicker == app_main._CATEGORY_KICKERS["grocery"]


def test_approving_the_week_with_nothing_added_skips_the_grocery_card():
    """A week that needed nothing has nowhere useful to send Emily — no
    second card, same as the receipt's own "already had everything" case."""
    before, after = _turn_with_tool_call(
        "approve_weekly_plan",
        {"weekly_plan_id": 1, "approved_by": "Emily"},
        {"weekly_plan_id": 1, "status": "approved", "groceries_added_count": 0, "already_have_skipped_count": 0},
    )
    actions = app_main.summarize_chat_actions(before, after)
    assert {a.tab for a in actions} == {"week"}


def test_approving_the_week_pluralizes_a_single_item_correctly():
    before, after = _turn_with_tool_call(
        "approve_weekly_plan",
        {"weekly_plan_id": 1, "approved_by": "Emily"},
        {"weekly_plan_id": 1, "status": "approved", "groceries_added_count": 1, "already_have_skipped_count": 0},
    )
    actions = app_main.summarize_chat_actions(before, after)
    by_tab = {a.tab: a for a in actions}
    assert by_tab["grocery"].change == "1 item ready to shop"


# ---------- notifications ----------

def test_week_approved_notification_points_at_grocery():
    """The notification says the shopping list is built — its action
    should take you to the list, not back to the (already-settled) week."""
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    notifications = tools.get_active_notifications()
    approved = [n for n in notifications if n["type"] == "week_approved"]
    assert approved, "expected a week_approved notification after approving with a named approver"
    assert approved[0]["tab"] == "grocery"
    assert approved[0]["action_label"] == "See the list"
