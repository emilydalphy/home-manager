"""
Source-marker guard for the front-end work that merge 2d69951 silently
dropped.

WHAT HAPPENED (2026-09-08). `2d69951` ("Merge custom-date-range", made by a
different session on 2026-09-06) hit a conflict in static/shell.js and
resolved it by taking the branch side of the file WHOLESALE:
`git show 2d69951:static/shell.js` is byte-identical to
`git show 2d69951^2:static/shell.js`. The branch had forked at `eb54fd3`,
so everything merged to main between `eb54fd3` and `ca97720` — nine
first-parent merges landed on 2026-09-05 — was erased from shell.js in one
commit (890 lines changed, net -681). Nothing failed: shell.js has no JS
test harness in this repo (no package.json/jest/mocha), and the Python
suite stayed green at 1161 because every backend half of that work was
untouched. The loss was only visible by reading the file.

WHY THIS FILE EXISTS. The features below were restored on
`restore-dropped-frontend-work` by redoing 2d69951 as a proper three-way
merge and re-applying the result onto today's main. Each assertion here is
a cheap source-level tripwire for one of them, so a future wholesale
conflict resolution fails a test instead of shipping quietly.

These are deliberately SOURCE assertions, not behaviour tests: there is no
way to exercise shell.js from pytest, and a marker that is present but
mis-wired is still a far better failure mode than a marker that is gone.
Where a string is user-facing copy it is asserted verbatim — if the copy is
deliberately reworded, update the constant here in the same commit and say
so; do not delete the test.

The restored merges, newest first:
  606f655 first-loop-chores            dbe458a leftover-chain-followups
  a2c4973 loop-handoffs-slice-3        0725d09 small-fixes-3
  6e9899c loop-handoffs-slice-2        30a7b0b allergy-backfill-and-gluten
  fd64a56 fix-produce-quantities       ea41834 allergy-confirm-tap
  0d26635 fix-full-plate
(30a7b0b and fd64a56 touched no static/ file, so they have no marker here.)

Two things are deliberately NOT asserted, because newer work on main
supersedes them rather than main having lost them: 0725d09's
weekRangeLabel/periodNoun planning-period wording (replaced by
custom-date-range's periodRangeLabel/planEntryLabel) and 6e9899c's singular
computeNextStepChip (replaced by tweak-sheet-see-your-week's plural — see
test_next_step_chips_after_a_change, which guards the plural and asserts
the singular stays gone).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
ONBOARDING_HTML = (REPO / "static" / "onboarding.html").read_text(encoding="utf-8")


def _assert_in(needle: str, haystack: str, what: str, where: str) -> None:
    assert needle in haystack, (
        f"{what} is missing from static/{where}.\n"
        f"Expected to find: {needle!r}\n"
        "This is front-end work that merge 2d69951 dropped once already by "
        "resolving a shell.js conflict one-side-wholesale. If you hit this "
        "from a merge conflict, resolve shell.js HUNK BY HUNK — keep both "
        "sides — rather than taking either file whole. If the change is "
        "deliberate, update this test in the same commit."
    )


# --- 0d26635 fix-full-plate: every meal is a full plate -------------------

def test_full_plate_note_and_side_chips_render():
    """The week view shows the plates note and each dinner's side chips."""
    _assert_in("data.plates_note", SHELL_JS, "the full-plate week note", "shell.js")
    _assert_in("wg2-dinner-chip", SHELL_JS, "the dinner chip row", "shell.js")
    _assert_in("entry.plate_note", SHELL_JS, "the per-entry plate note chip", "shell.js")
    _assert_in("meal.sides_label", SHELL_JS, "the sides label chip", "shell.js")


# --- ea41834 allergy-confirm-tap: a hard clash needs a confirm tap --------

@pytest.mark.parametrize(
    "copy",
    [
        "Approve anyway — I’ve seen the clash",
        "Let me fix it first",
    ],
)
def test_hard_allergy_clash_confirm_sheet_copy(copy):
    """Both buttons of the confirm-tap sheet. The backend half of this
    (weekly_plan.approve_weekly_plan's needs_confirmation branch) is covered
    by tests/test_allergy_safety.py; without these the user can never send
    the confirm the backend is waiting for."""
    _assert_in(copy, SHELL_JS, "the hard-allergy confirm sheet", "shell.js")


def test_approval_goes_through_the_confirm_capable_path():
    _assert_in("function showApproveConfirm(", SHELL_JS, "showApproveConfirm", "shell.js")
    _assert_in("function submitWeekApproval(", SHELL_JS, "submitWeekApproval", "shell.js")


# --- 6e9899c loop-handoffs-slice-2 ---------------------------------------

def test_next_step_chips_after_a_change():
    """"Every step should end by offering the next one" (Emily, 2026-09-04).

    The only piece of the dropped work that was already back before this
    restoration: `tweak-sheet-see-your-week` (merged as 462d506) re-added
    the pair in its PLURAL form — computeNextStepChips/offerNextStepChips,
    which offers "See your week" alongside the primary chip. That version
    supersedes 6e9899c's singular computeNextStepChip, so the singular is
    deliberately NOT restored and must not come back beside it.
    """
    _assert_in("function computeNextStepChips(", SHELL_JS, "computeNextStepChips", "shell.js")
    _assert_in("function offerNextStepChips(", SHELL_JS, "offerNextStepChips", "shell.js")
    assert "function computeNextStepChip(" not in SHELL_JS, (
        "The singular computeNextStepChip is back alongside the plural "
        "computeNextStepChips. The plural (tweak-sheet-see-your-week, "
        "462d506) is the live one — a merge has re-introduced the older "
        "singular rather than resolving in its favour."
    )


def test_today_surfaces_an_open_dinner():
    _assert_in("resolveOpenDinner", SHELL_JS, "the open-dinner resolver", "shell.js")
    _assert_in("data-card-type=\"dinner_open\"", SHELL_JS, "the open-dinner card", "shell.js")


def test_cooked_and_never_mind_toasts():
    _assert_in("function toastMealLogged(", SHELL_JS, "the cooked/never-mind toast", "shell.js")
    _assert_in("Left as it was.", SHELL_JS, "the never-mind toast copy", "shell.js")


# --- 0725d09 small-fixes-3: the "Somewhere else" triage chip --------------

def test_somewhere_else_triage_chip():
    """The grocery triage row's neutral third option, and the sand
    treatment that keeps it from reading as one more store."""
    _assert_in("Somewhere else", SHELL_JS, "the 'Somewhere else' triage chip", "shell.js")
    _assert_in("gro-pill-else", SHELL_JS, "the 'Somewhere else' chip class", "shell.js")
    _assert_in(".gro-pill-else", SHELL_CSS, "the 'Somewhere else' chip style", "shell.css")


# --- a2c4973 loop-handoffs-slice-3 ---------------------------------------

def test_cook_empty_state_links_to_the_plan_tab():
    """Cook with nothing planned offers the way out instead of dead-ending."""
    _assert_in("cook-empty-link", SHELL_JS, "the Cook empty-state link", "shell.js")
    _assert_in("data-cook=\"goto-plan\"", SHELL_JS, "the Cook empty-state link target", "shell.js")
    _assert_in(".cook-empty-link", SHELL_CSS, "the Cook empty-state link style", "shell.css")


def test_prep_section_says_what_is_next_once_prep_is_done():
    _assert_in("Prep’s done — the rest is tonight.", SHELL_JS, "the finished-prep note", "shell.js")


def test_end_of_cook_offers_the_next_step():
    _assert_in("function cookFocusEndHtml(", SHELL_JS, "the end-of-cook panel", "shell.js")
    _assert_in(".cook-focus-end", SHELL_CSS, "the end-of-cook panel style", "shell.css")
    _assert_in("Show me tomorrow", SHELL_JS, "the 'Show me tomorrow' handoff", "shell.js")
    _assert_in("function cookTomorrowHasPrepOrDefrost(", SHELL_JS, "its guard", "shell.js")


def test_defrost_and_share_handoffs():
    """
    UPDATED 2026-09-08 (flows-1-today-next-up), deliberately, per this
    file's own instruction at the top: `undoSkipDefrostTask` — the undo on
    Today's standalone defrost tile — is gone because the tile is gone.
    Emily's approved Today redesign replaced every separate card on that
    screen with one timeline of moves, so a defrost reminder is now a
    "fridge move" line with a tick, and undoing it is the same tick tapped
    again (toggleTodayMove, which posts done:false). The reversibility this
    assertion was guarding is still there — it just isn't a toast action on
    a tile any more, so the marker moved rather than the behaviour being
    dropped.
    """
    _assert_in("function toggleTodayMove(", SHELL_JS, "the move tick (and its undo)", "shell.js")
    _assert_in("data-move-tick", SHELL_JS, "the tick control", "shell.js")
    _assert_in(".tick.is-done", SHELL_CSS, "the filled tick style", "shell.css")
    _assert_in(
        "Link copied. Anyone with it sees this week’s meals, nothing else.",
        SHELL_JS,
        "the share-link confirmation",
        "shell.js",
    )


def test_onboarding_offers_a_retry():
    """a2c4973's onboarding half — the retry after a failed first run."""
    assert "retry" in ONBOARDING_HTML.lower(), (
        "static/onboarding.html no longer offers a retry after a failed "
        "first run (loop-handoffs-slice-3, a2c4973)."
    )


# --- dbe458a leftover-chain-followups: a reheat reads as leftovers --------

def test_plan_tab_labels_a_reheat_as_leftovers():
    """The Plan tab's action says "reheat", not "Cook this", when the night
    is a leftover of an earlier batch."""
    _assert_in("day.dinner.source === 'leftovers'", SHELL_JS, "the reheat test", "shell.js")
    _assert_in("REHEAT_ACTION_LABEL", SHELL_JS, "the reheat action label", "shell.js")
    _assert_in("meal.reheat_note", SHELL_JS, "the reheat accent note", "shell.js")


# --- 606f655 first-loop-chores -------------------------------------------

def test_chores_setup_stays_reachable_from_today():
    """Chores setup moved out of first-run onboarding onto its own page, so
    the Today chores card carries the only way back to it. Currently inside
    the SHOW_CHORES_ON_TODAY gate (beta is meals-only) — the link must still
    exist in the source so it comes back with the card."""
    _assert_in("id=\"chores-setup-link\"", SHELL_JS, "the chores-setup link", "shell.js")
    _assert_in(
        "Want help with chores too? Set them up",
        SHELL_JS,
        "the chores-setup link copy",
        "shell.js",
    )
    _assert_in("function renderChores(", SHELL_JS, "renderChores", "shell.js")
    _assert_in("choresSetUp", SHELL_JS, "the chores_set_up flag wiring", "shell.js")


def test_grocery_asks_for_stores_just_in_time():
    """Stores are asked for on Grocery when they're first needed, not in a
    first-run questionnaire. Backend half: tests/test_stores_just_in_time.py."""
    _assert_in("function groStoresPromptShouldShow(", SHELL_JS, "the stores prompt guard", "shell.js")
    _assert_in("function groStoresPromptHtml(", SHELL_JS, "the stores prompt", "shell.js")
    _assert_in("GRO_STORE_PROMPT_CHIPS", SHELL_JS, "the suggested-store chips", "shell.js")
    _assert_in("function groAddUsualStore(", SHELL_JS, "the usual-store add", "shell.js")
    _assert_in("storesPromptDismissed", SHELL_JS, "the stores prompt dismissal", "shell.js")


# --- the file as a whole -------------------------------------------------

def test_shell_js_did_not_lose_a_third_of_itself():
    """A blunt size floor. The dropped merge took shell.js from 7412 lines
    to 6982 in one commit; a wholesale resolution is always a large sudden
    shrink, and this catches the shape of it even for work that has no
    named marker above. Raise the floor when the file grows; only lower it
    for a deletion you can point at."""
    lines = SHELL_JS.count("\n") + 1
    assert lines > 7600, (
        f"static/shell.js is {lines} lines — suspiciously short. Merge "
        "2d69951 shrank it from 7412 to 6982 by resolving a conflict "
        "one-side-wholesale. Check `git log -p --follow static/shell.js` "
        "for a merge that took one side whole before assuming this is fine."
    )
