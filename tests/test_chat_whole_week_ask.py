"""
Loop Board "Chat on the draft — do the whole-week ask, then 'Back to your
week'" (Bug, High; Emily, 2026-09-20, her phone).

She opened chat from Monday's breakfast row and typed "For all the
breakfasts let's do boiled eggs and avocado toast". Pomona replied "This
message is about Monday's Greek yogurt breakfast, not the whole week's
breakfasts. Shall I swap today's breakfast … and handle changing the rest
of the week's breakfasts separately?" — and then, once done, left "See
your week", "Approve this week" and a "WEEK UPDATED · View" card all on
screen: "It's confusing where the user needs to go from here."

Two roots, tested where each lives:

(1) The meal card's context block (agent._build_chat_context_block, kind
    planned_meal) told the model the ONE meal was the subject and to
    confirm once — nothing about a message that names a wider scope, and
    no way to act on one (it only carried that meal's date). Now it says
    the card is a starting point, not a fence; lists the rest of the week
    so the swaps need no lookup; and gives the one-breath reply.
    Stubbed client, same idea as tests/test_tell_me_instead_context.py.

(2) The receipt and the way on — one "Week updated · 7 breakfasts
    swapped" card (app/main.py, tested in tests/test_tweak_the_week.py),
    one primary "Back to your week" (computeNextStepChips, same file),
    the sheet's Back named the same, and the landing scrolled to the
    first change. Source markers here for the parts shell.js has no
    harness for.
"""
from __future__ import annotations

import datetime
import re
import types
from pathlib import Path

import pytest

from app import agent, tools
from app.db import get_conn


TODAY = datetime.date.today()
WEEK_START = (TODAY - datetime.timedelta(days=TODAY.weekday())).isoformat()
DAYS = [(datetime.date.fromisoformat(WEEK_START) + datetime.timedelta(days=i)).isoformat()
        for i in range(7)]
MONDAY = DAYS[0]

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

THE_ASK = "For all the breakfasts let's do boiled eggs and avocado toast"


# ---------- fixtures ----------


@pytest.fixture
def week():
    """A draft week with Greek yogurt every morning and a dinner on Monday.
    Returns the plan id."""
    tools.add_recipe(
        "Greek Yogurt with Berries",
        ingredients=[{"item": "Greek yogurt", "qty": "1 tub", "category": "dairy"}],
        food_groups=["protein"],
    )
    tools.add_recipe(
        "Chili", ingredients=[{"item": "Beans", "qty": "2 cans", "category": "pantry"}],
        food_groups=["protein"],
    )
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    for day in DAYS:
        tools.plan_meal(day, "Greek Yogurt with Berries", slot="breakfast", weekly_plan_id=plan_id, reasoning="quick")
    tools.plan_meal(MONDAY, "Chili", slot="dinner", weekly_plan_id=plan_id, reasoning="easy")
    return plan_id


def _entry_id(plan_id: int, day: str, slot: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND weekly_plan_id = ? "
        "AND date = ? AND slot = ? ORDER BY id DESC",
        (tools.household_id(), plan_id, day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


class _Usage:
    input_tokens = 0
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    output_tokens = 0


class _CapturingMessages:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="Done.")],
            stop_reason="end_turn",
            usage=_Usage(),
        )


def _block_for(monkeypatch, plan_id: int, message: str = THE_ASK) -> str:
    """The meal-card block for a turn sent from Monday's breakfast row."""
    captured = _CapturingMessages()
    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=captured))
    context = {"kind": "planned_meal", "entry_id": _entry_id(plan_id, MONDAY, "breakfast"),
               "date": MONDAY, "slot": "breakfast"}
    agent.run_agent_turn([], message, context=context)
    assert captured.calls
    return captured.calls[0]["system"][-1]["text"]


# ---------- (1) the card is a starting point, not a fence ----------


def test_a_wider_scope_in_the_message_wins_over_the_card(week, monkeypatch):
    block = _block_for(monkeypatch, week)
    # Still about Monday's breakfast by default…
    assert "Monday's breakfast" in block
    # …but a wider scope is the whole scope, done in this turn, no question.
    assert "NOT A FENCE" in block
    assert "\"all the breakfasts\"" in block and "\"every dinner\"" in block and "\"the whole week\"" in block
    assert "do all of it in this turn" in block
    assert "Never ask whether they meant only this one" in block
    assert "handle the rest separately" in block  # named as the thing not to do
    assert "call swap_meal_in_plan once per matching slot" in block


def test_the_rest_of_the_week_rides_along_so_the_swaps_need_no_lookup(week, monkeypatch):
    """swap_meal_in_plan wants each slot's date and the dish there now. The
    block used to carry only Monday's — half of why the model did one and
    offered to do the rest 'separately'."""
    block = _block_for(monkeypatch, week)
    for day in DAYS:
        assert day in block, f"{day}'s breakfast is missing from the block"
    listing = block[block.index("The week this card is on"):]
    assert listing.count('breakfast: "Greek Yogurt with Berries"') == 7
    assert 'dinner: "Chili"' in listing


def test_the_reply_is_one_breath_and_says_where_the_list_stands(week, monkeypatch):
    block = _block_for(monkeypatch, week)
    assert ("\"Done — every breakfast this week is boiled eggs and avocado toast. "
            "Nothing's on the list yet; the week's still a draft.\"") in block
    assert "Nothing else — the card under your reply carries the count." in block


def test_on_an_approved_week_the_reply_says_the_list_followed(week, monkeypatch):
    tools.approve_weekly_plan(week)
    block = _block_for(monkeypatch, week)
    assert "The list's updated to match." in block
    assert "week's still a draft" not in block


def test_the_one_meal_rule_is_still_there_for_a_plain_ask(week, monkeypatch):
    """"Make it beef" about the one card still confirms once (Emily,
    2026-09-13) — the wider-scope rule sits in front of it, not instead."""
    block = _block_for(monkeypatch, week, "make it porridge")
    assert "Confirm ONCE" in block
    assert block.index("NOT A FENCE") < block.index("Confirm ONCE")


def test_a_week_that_cannot_be_read_still_leaves_the_card_its_own_meal(week, monkeypatch):
    monkeypatch.setattr(tools, "describe_plan_for_chat", lambda **kw: 1 / 0)
    block = _block_for(monkeypatch, week)
    assert "Monday's breakfast" in block
    assert "NOT A FENCE" in block
    assert "The week this card is on" not in block


def test_the_plan_tab_block_says_the_same_about_scope(week, monkeypatch):
    """Chat opened from the Plan tab's ask bar goes through the change card;
    "all the breakfasts" there is every matching row in ONE card."""
    block = agent._build_chat_context_block({"kind": "weekly_plan", "weekly_plan_id": week})["text"]
    assert "\"All the breakfasts\", \"every dinner\", \"the whole week\" mean every matching row" in block
    assert "never one row and a question about the rest" in block


# ---------- (2) the way on ----------


def test_the_draft_edit_card_is_a_receipt_with_no_view():
    assert "function isDraftWeekAction(action)" in SHELL_JS
    assert "receipt.className = 'ask-action-card is-receipt';" in SHELL_JS
    receipt = SHELL_JS[SHELL_JS.index("if (isDraftWeekAction(action)) {"):]
    receipt = receipt[:receipt.index("return;")]
    assert "ask-action-tick" in receipt
    assert "View" not in receipt, "the receipt is not a door — the primary button under it is"
    assert ".ask-action-card.is-receipt {" in SHELL_CSS


def test_back_to_your_week_is_the_sheet_s_one_primary():
    assert "label: 'Back to your week'" in SHELL_JS
    assert "primary: true" in SHELL_JS
    assert "ask-chip-primary" in SHELL_JS
    rule = re.search(r"\.ask-chip-primary\s*\{([^}]*)\}", SHELL_CSS, re.S)
    assert rule, "static/shell.css has no .ask-chip-primary rule"
    assert "var(--apricot)" in rule.group(1)
    assert "var(--on-accent-ink)" in rule.group(1), "dark ink on an apricot fill (hard rule 1)"
    assert "100%" in rule.group(1), "full width — a button, not a pill"
    # And the two competing pills are gone from the sheet.
    assert "label: 'See your week'" not in SHELL_JS
    assert "label: 'Approve this week'" not in SHELL_JS


def test_the_sheet_s_own_back_is_named_the_same_when_it_knows():
    assert 'id="ask-sheet-back-label"' in SHELL_HTML
    assert "function askBackLabel(context)" in SHELL_JS
    assert "setAskBackLabel(askBackLabel(askContext));" in SHELL_JS
    body = SHELL_JS[SHELL_JS.index("function askBackLabel(context)"):]
    body = body[:body.index("function setAskBackLabel")]
    assert "kind === 'planned_meal' || kind === 'weekly_plan'" in body
    assert "currentTabKey() === 'week'" in body
    assert body.count("return 'Back to your week';") == 2
    assert "return 'Back';" in body, "plain Back anywhere the week isn't underneath"


def test_landing_scrolls_the_first_changed_row_into_view():
    body = SHELL_JS[SHELL_JS.index("function applyPendingDayFocus(panel)"):]
    body = body[:body.index("function splitTableRow")]
    assert "target.classList.add('just-changed');" in body
    assert "target.scrollIntoView({ block: 'center', behavior: 'smooth' })" in body
