"""
Review is the week; approval ends in one receipt (Emily's approved design,
2026-09-08, branch `flows-3-review-and-receipt`).

THE PROBLEM. On the new Week root, the draft review band was about 410px
tall and, after approval, the receipt plus two full nudge cards filled the
whole phone viewport — so the seven-row week card, which is the answer the
screen exists to give, started below the fold in both states.

THE DESIGN. A DRAFT is the week card itself: a DRAFT badge, "a draft, your
turn" in the subtitle, one urgent-tint "One thing to settle" card above the
card when (and only when) there is a hard allergen clash, one apricot
"Approve this week" and one quiet "Tweak it with me" below it. A SET week
gets one celadon receipt — a counted sentence and a thaw line — plus the two
remaining asks as LINES rather than cards, until "See the week" dismisses
the lot and the card is the top of the screen.

Two kinds of test, the same split as tests/test_meals_week_day_meal.py:

  * BEHAVIOUR, for the counting the receipt's sentence is built out of
    (weekly_plan.week_receipt) and the two sentences the draft's clash
    cards are built out of (coordination._settle / _soft_note). Both are
    server-side deliberately: the numbers and the sentence they sit in must
    not be able to drift apart, and shell.js has no JS test harness here.
  * SOURCE MARKERS, for the front end — what the draft renders, what it no
    longer renders, and what the receipt's two lines say.
"""
from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from app import tools
from app.db import get_conn


TODAY = datetime.date.today()
# Two days back, so today is never the plan's first day: a made-ahead source
# has to sit on an earlier day of the same plan.
WEEK_START = (TODAY - datetime.timedelta(days=2)).isoformat()
ISO_TODAY = TODAY.isoformat()
ISO_YESTERDAY = (TODAY - datetime.timedelta(days=1)).isoformat()
ISO_TOMORROW = (TODAY + datetime.timedelta(days=1)).isoformat()

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _plan() -> int:
    return tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]


def _entry_id(day: str, slot: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


def _defrost_task(plan_id: int, task_date: str, description: str, entry_id: int | None = None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
        "related_meal, status, task_type, meal_plan_entry_id) "
        "VALUES (?, ?, ?, ?, '', 'pending', 'defrost', ?)",
        (tools.household_id(), plan_id, task_date, description, entry_id),
    )
    conn.commit()
    conn.close()


def _receipt(plan_id: int) -> dict:
    """
    week_receipt over the plan's own days. Called directly rather than
    through an approval so the grocery list can be whatever the test needs
    it to be — approving builds the list, which is the one input this helper
    doesn't derive from the plan.
    """
    return tools.week_receipt(tools.get_week_menu()["days"], plan_id)


# ---------- the receipt's counted sentence ----------

def test_a_reheat_night_is_a_meal_but_not_a_cook():
    """
    "Sixteen meals, five cooks" only means anything if the two numbers
    differ, and a reheat is exactly where they do: somebody eats, nobody
    cooks. Same rule as the week card's own "4 cooks, 3 made ahead"
    subtitle, deliberately — one week must not carry two different counts of
    itself.
    """
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_YESTERDAY, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TODAY, "Leftovers from last night", slot="dinner", weekly_plan_id=plan_id)

    receipt = _receipt(plan_id)

    assert receipt["meals"] == 2
    assert receipt["cooks"] == 1
    assert receipt["title"].startswith("Two meals, one cook")


def test_an_away_night_is_neither_a_meal_nor_a_cook():
    """A night the household is out has nothing to eat and nothing to cook.
    Counting it as a meal would put a number on the receipt that nobody
    sits down to."""
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_YESTERDAY, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_slot_empty(plan_id, ISO_TODAY, "dinner", "Out for dinner")

    receipt = _receipt(plan_id)

    assert receipt["meals"] == 1
    assert receipt["cooks"] == 1


def test_an_empty_list_says_so_instead_of_promising_a_list_of_nothing():
    """A household whose kitchen already had everything is not a failure —
    "one list of 0 things" is."""
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chili", slot="dinner", weekly_plan_id=plan_id)

    receipt = _receipt(plan_id)

    assert receipt["list_count"] == 0
    assert receipt["title"] == "One meal, one cook, nothing left to buy."


def test_the_list_size_is_what_is_still_to_buy():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chili", slot="dinner", weekly_plan_id=plan_id)
    for item in ("beans", "rice", "onions"):
        tools.add_grocery_item(item)

    receipt = _receipt(plan_id)

    assert receipt["list_count"] == 3
    assert "one list of three things" in receipt["title"]


@pytest.mark.parametrize(
    "count,expected",
    [(1, "one list of one thing"), (12, "one list of twelve things"), (13, "one list of 13 things")],
)
def test_numbers_run_out_at_twelve(count, expected):
    """Emily, 2026-09-08: one to twelve as words, digits above."""
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chili", slot="dinner", weekly_plan_id=plan_id)
    for i in range(count):
        tools.add_grocery_item(f"item {i}")

    assert expected in _receipt(plan_id)["title"]


# ---------- the receipt's thaw line ----------

def test_something_to_thaw_is_counted_not_listed():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TOMORROW, "Chili", slot="dinner", weekly_plan_id=plan_id)
    _defrost_task(plan_id, ISO_TODAY, "Move the beef to the fridge",
                  _entry_id(ISO_TOMORROW, "dinner"))
    # A ready-made earmark has no entry of its own and is still something to
    # move, so the receipt — which counts the whole week — counts it too.
    _defrost_task(plan_id, ISO_TODAY, "Move the lasagne to the fridge")

    receipt = _receipt(plan_id)

    assert receipt["thaw_count"] == 2
    assert receipt["thaw_line"] == "Two things to move to the fridge this week."


def test_one_thing_to_thaw_is_singular():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TOMORROW, "Chili", slot="dinner", weekly_plan_id=plan_id)
    _defrost_task(plan_id, ISO_TODAY, "Move the beef to the fridge")

    assert _receipt(plan_id)["thaw_line"] == "One thing to move to the fridge this week."


def test_nothing_to_thaw_names_the_next_cook():
    """"Nothing to thaw before Wednesday" — Wednesday being the next day of
    this plan somebody cooks, which is the day the question would next come
    up. It is the free-until fact, not a promise about Wednesday."""
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TOMORROW, "Chili", slot="dinner", weekly_plan_id=plan_id)

    expected = (TODAY + datetime.timedelta(days=1)).strftime("%A")
    assert _receipt(plan_id)["thaw_line"] == f"Nothing to thaw before {expected}."


def test_nothing_to_thaw_and_nothing_left_to_cook_names_no_day():
    """A week whose cooking is all behind it has no day to name, so the line
    says the week rather than inventing one."""
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_YESTERDAY, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TOMORROW, "Leftovers from Monday", slot="dinner", weekly_plan_id=plan_id)

    assert _receipt(plan_id)["thaw_line"] == "Nothing to thaw this week."


def test_an_approved_week_carries_its_receipt_and_a_draft_does_not():
    """The payload half: a receipt is what you get for having decided."""
    tools.add_member("Emily")
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TOMORROW, "Chili", slot="dinner", weekly_plan_id=plan_id)

    assert tools.get_week_menu()["receipt"] is None
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    approved = tools.get_week_menu()

    assert approved["receipt"]["meals"] == 1
    assert approved["receipt"]["title"].startswith("One meal, one cook,")


# ---------- the draft's two clash sentences ----------

def test_a_hard_clash_names_the_night_the_dish_and_the_person():
    """The "One thing to settle" card's whole content. Server-worded so the
    sentence lives with the data it describes."""
    tools.add_member("Emily")
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    tools.add_recipe("Pineapple Salsa", ingredients=[{"item": "pineapple", "qty": "1"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TOMORROW, "Pineapple Salsa", slot="dinner", weekly_plan_id=plan_id)

    settle = tools.check_plan_conflicts(plan_id)["settle"]
    when = (TODAY + datetime.timedelta(days=1)).strftime("%A")

    assert settle["count"] == 1
    assert settle["meal"] == "Pineapple Salsa"
    assert settle["date"] == ISO_TOMORROW
    # The allergen is in the dish's own name, so the sentence does not
    # repeat it back — "Pineapple Salsa has pineapple" is noise.
    assert settle["note"] == f"{when}’s Pineapple Salsa, which Emily is allergic to."


def test_a_hard_clash_that_is_not_an_allergy_says_cannot_have():
    """"is allergic to" is a claim about someone's health — only made when
    the restriction they wrote down actually says allergy."""
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["pork"])
    tools.add_recipe("Roast Pork", ingredients=[{"item": "pork shoulder", "qty": "1"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TOMORROW, "Roast Pork", slot="dinner", weekly_plan_id=plan_id)

    note = tools.check_plan_conflicts(plan_id)["settle"]["note"]

    assert "can’t have" in note
    assert "allergic" not in note


def test_no_hard_clash_means_no_card_at_all():
    tools.add_member("Emily")
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TOMORROW, "Chili", slot="dinner", weekly_plan_id=plan_id)

    found = tools.check_plan_conflicts(plan_id)
    assert found["settle"] is None
    assert found["soft_note"] is None


def test_a_soft_conflict_is_one_line_and_never_a_card():
    """A dislike is a preference: it gets no card, no button, and never
    gates approval — one quiet line under the week card is the whole of it."""
    tools.add_member("Vineeth")
    tools.add_recipe("Thai Green Curry", ingredients=[{"item": "coconut milk", "qty": "1 tin"}])
    tools.attribute_recipe_feedback("Thai Green Curry", "Vineeth", rating="disliked")
    plan_id = _plan()
    tools.plan_meal(ISO_TOMORROW, "Thai Green Curry", slot="dinner", weekly_plan_id=plan_id)

    found = tools.check_plan_conflicts(plan_id)
    when = (TODAY + datetime.timedelta(days=1)).strftime("%A")

    assert found["settle"] is None
    assert found["soft_note"] == f"Vineeth isn’t keen on {when}’s Thai Green Curry."


def test_the_week_payload_carries_both_halves_for_a_draft_only():
    tools.add_member("Emily")
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    tools.add_recipe("Pineapple Salsa", ingredients=[{"item": "pineapple", "qty": "1"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TOMORROW, "Pineapple Salsa", slot="dinner", weekly_plan_id=plan_id)

    draft = tools.get_week_menu()
    assert draft["settle"]["meal"] == "Pineapple Salsa"

    # An approved week has had its decision made; a warning about it would
    # be a scold rather than a help (same rule conflicts_note already
    # follows in get_week_menu).
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    assert tools.get_week_menu()["settle"] is None


# ---------- the front end, by source marker ----------

def _assert_in(needle: str, haystack: str, what: str, where: str) -> None:
    assert needle in haystack, (
        f"{what} is missing from static/{where}.\n"
        f"Expected to find: {needle!r}\n"
        "This is Emily's approved 2026-09-08 review-and-receipt design. If "
        "the change is deliberate, update this test in the same commit and "
        "say why."
    )


def _assert_gone(needle: str, haystack: str, what: str) -> None:
    assert needle not in haystack, (
        f"{what} is back: {needle!r}.\n"
        "The approved design removes the draft review band and the stacked "
        "receipt + two nudge cards — between them they filled a phone "
        "viewport and pushed the week card below the fold, which is the "
        "whole problem this redesign exists to fix."
    )


@pytest.mark.parametrize("gone", [
    "function renderWeekReviewBand(",
    '<div id="week-review-band"></div>',
    "week-review-eyebrow",
    ">DRAFT · YOUR TURN<",
    "function groceryPromiseText(",
])
def test_the_draft_renders_no_review_band(gone):
    _assert_gone(gone, SHELL_JS, "the review band")


@pytest.mark.parametrize("gone", [
    "function receiptBodyText(",
    "week-receipt-handoff",
    "Take me to the list",
    "plan-nudge-card defrost-ask-card",
    "plan-nudge-card cook-ahead-ask-card",
    "FREEZER CHECK",
    "One batch, several days?",
])
def test_the_old_stacked_receipt_and_nudge_cards_are_gone(gone):
    _assert_gone(gone, SHELL_JS, "the old stacked receipt/nudge cards")


def test_the_settle_card_renders_only_when_there_is_a_hard_clash():
    """`data.settle` is None unless a HARD clash exists (coordination._settle
    above), and the card's own guard returns an empty band without it — a
    soft conflict must never grow a card."""
    _assert_in("function renderWeekSettle(", SHELL_JS, "the settle card", "shell.js")
    _assert_in("if (!settle || !settle.note) { row.innerHTML = ''; return; }", SHELL_JS,
               "the settle card's guard", "shell.js")
    _assert_in("'One thing to settle'", SHELL_JS, "the settle card's title", "shell.js")
    _assert_in("Keep it anyway", SHELL_JS, "the keep-it-anyway segment", "shell.js")
    _assert_in("'Swap the ' + dishShortName(settle.meal)", SHELL_JS,
               "the swap segment", "shell.js")
    _assert_in("var(--urgent-tint)", SHELL_CSS, "the settle card's urgent tint", "shell.css")


def test_keeping_it_anyway_still_costs_the_confirm_tap():
    """The hard-clash confirm is untouched: "Keep it anyway" posts the
    ordinary approval, which comes back needs_confirmation, and
    showApproveConfirm turns the Approve button into the confirm."""
    _assert_in("function showApproveConfirm(", SHELL_JS, "showApproveConfirm", "shell.js")
    _assert_in("function submitWeekApproval(", SHELL_JS, "submitWeekApproval", "shell.js")
    _assert_in("Approve anyway — I’ve seen the clash", SHELL_JS,
               "the confirm button's copy", "shell.js")
    _assert_in("var card = panel.querySelector('.wk-decide');", SHELL_JS,
               "the confirm finding the button under the week card", "shell.js")


def test_the_draft_has_exactly_one_apricot_and_one_quiet_action():
    """DESIGN_SYSTEM Rule 5. The apricot is .week-approve-btn; "Tweak it with
    me" shares .week-reset-link's quiet italic idiom rather than being a
    second button competing with it."""
    _assert_in("function weekDecideHtml(", SHELL_JS, "the decision row", "shell.js")
    _assert_in("'Approve this week'", SHELL_JS, "the Approve button's copy", "shell.js")
    _assert_in('week-reset-link week-tweak-link" id="week-tweak-btn">Tweak it with me',
               SHELL_JS, "the quiet tweak link", "shell.js")
    assert SHELL_JS.count("class=\"btn-gold week-approve-btn\"") == 1, (
        "More than one apricot Approve button is rendered on Meals "
        "(DESIGN_SYSTEM Rule 5: one apricot primary per screen)."
    )
    _assert_in("background: var(--apricot)", SHELL_CSS, "the apricot fill", "shell.css")


def test_the_draft_subtitle_says_whose_turn_it_is():
    _assert_in("'a draft, your turn'", SHELL_JS, "the draft subtitle", "shell.js")


def test_a_soft_conflict_is_a_line_under_the_card():
    _assert_in("function weekNotesHtml(", SHELL_JS, "the quiet notes row", "shell.js")
    _assert_in("data.soft_note", SHELL_JS, "the soft-conflict line", "shell.js")


def test_the_receipt_is_one_card_with_two_segments():
    _assert_in("function renderWeekReceipt(", SHELL_JS, "the receipt", "shell.js")
    _assert_in("YOUR WEEK IS SET", SHELL_JS, "the receipt eyebrow", "shell.js")
    _assert_in("receipt.title", SHELL_JS, "the counted sentence", "shell.js")
    _assert_in("receipt.thaw_line", SHELL_JS, "the thaw line", "shell.js")
    _assert_in(">Open the list<", SHELL_JS, "the Open the list segment", "shell.js")
    _assert_in(">See the week<", SHELL_JS, "the See the week segment", "shell.js")
    _assert_in("var(--celadon-tint)", SHELL_CSS, "the receipt's celadon fill", "shell.css")


def test_the_two_asks_are_lines_that_expand_in_place():
    """The defrost and cook-ahead asks keep their own UI and their own
    routes — only the presentation folds into a line with an "Ask"."""
    _assert_in("Two quick ones before you go", SHELL_JS, "the asks card title", "shell.js")
    _assert_in("One quick one before you go", SHELL_JS, "the single-ask title", "shell.js")
    _assert_in("'Anything in the freezer?'", SHELL_JS, "the freezer line", "shell.js")
    _assert_in("'. Cook ahead?'", SHELL_JS, "the cook-ahead line", "shell.js")
    _assert_in("function defrostAskSummary(", SHELL_JS, "the collapsed meat chips", "shell.js")
    _assert_in("items.slice(0, 3).join(' · ')", SHELL_JS,
               "the chips collapsed to one line", "shell.js")
    # The asks themselves are the same code and the same endpoints.
    _assert_in("/defrost-confirm", SHELL_JS, "the defrost route", "shell.js")
    _assert_in("/cook-ahead-confirm", SHELL_JS, "the cook-ahead route", "shell.js")
    _assert_in("data.defrost_asked_at", SHELL_JS, "the defrost asked_at gate", "shell.js")
    _assert_in("data.cook_ahead_asked_at", SHELL_JS, "the cook-ahead asked_at gate", "shell.js")
    _assert_in(".wk-quick-body", SHELL_CSS, "the expanded ask's styling", "shell.css")
    # Found in the browser, not in review: an author `display` beats the UA
    # sheet's [hidden] rule, so without this guard both asks rendered open
    # and the card was as tall as the two it replaced.
    _assert_in(".wk-quick-body[hidden] { display: none; }", SHELL_CSS,
               "the collapsed ask's hidden guard", "shell.css")


def test_the_receipt_is_dismissed_per_plan_for_this_session_only():
    """sessionStorage keyed by weekly_plan_id: it survives a tab switch (the
    panel re-renders every time Meals comes back) but not a new session — a
    week approved yesterday opens on the week card, not on its receipt."""
    _assert_in("'pomona.weekReceiptDismissed.'", SHELL_JS, "the dismissal key", "shell.js")
    _assert_in("sessionStorage.setItem(WEEK_RECEIPT_DISMISS_KEY", SHELL_JS,
               "the dismissal write", "shell.js")
    _assert_in("function weekReceiptDismissed(", SHELL_JS, "the dismissal read", "shell.js")
    _assert_in("setWeekReceiptDismissed(data.weekly_plan_id, true);", SHELL_JS,
               "'See the week' dismissing the receipt", "shell.js")


def test_reopening_the_week_kept_an_entry_point():
    """It left the receipt with the rest of that card's buttons, so it has to
    have landed somewhere — the More sheet, with every other rare action."""
    _assert_in("wk-more-reopen", SHELL_JS, "the Reopen the week row", "shell.js")
    _assert_in("reopenWeek(panel, data)", SHELL_JS, "its handler", "shell.js")


def test_the_next_step_chips_are_unchanged():
    """Emily's design touches the Meals surfaces, not chat's handoff chips."""
    _assert_in("function computeNextStepChips(", SHELL_JS, "computeNextStepChips", "shell.js")
    _assert_in("label: 'See your week'", SHELL_JS, "the See your week chip", "shell.js")
    _assert_in("label: 'Approve this week'", SHELL_JS, "the Approve this week chip", "shell.js")
