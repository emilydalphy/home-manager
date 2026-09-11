"""
How setup ends (Emily + Julia, 2026-09-08): "tell me plainly that setup is
complete, what Pomona did, and the one thing to do next, so I never wonder
whether I missed a step."

The reveal used to end on "Looks good — take me in" over a list of days,
which named no next step and landed on whichever week today happens to sit
in. It now ends on a receipt — the same eyebrow/title/lines block the
approved week gets on Meals (shell.js renderWeekReceipt) — and one apricot
button into the draft review that receipt just told you to do.

Two kinds of test, for the two things that can go wrong:

- Source-level, because this repo has no JavaScript test harness and the
  copy IS the feature: a line quietly changing back, the old button copy
  coming home, or the destination sliding back to the plain week are all
  invisible to anything else here. (Same reasoning tests/
  test_onboarding_inputs.py sets out at length.)
- Behavioural under node for the three line builders, which are pure —
  answers in, sentence out — so they can be lifted out and run for real
  rather than grepped for keywords. That is where "built from the answers
  actually given" is actually checked.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


STATIC = Path(__file__).resolve().parent.parent / "static"
ONBOARDING_PATH = STATIC / "onboarding.html"
ONBOARDING = ONBOARDING_PATH.read_text()

# The same source with HTML comments and full-line JS comments removed. A
# comment naming the copy it replaced is exactly what this project wants
# people to keep writing, so "the old wording is gone" has to mean gone from
# the SCREEN, not gone from the file.
ONBOARDING_VISIBLE = re.sub(r"<!--.*?-->", "", ONBOARDING, flags=re.S)
ONBOARDING_VISIBLE = re.sub(r"^\s*//.*$", "", ONBOARDING_VISIBLE, flags=re.M)

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to run the page's own line builders",
)


def _balanced(source: str, start: int) -> str:
    """From `start`, return the text through the matching close brace."""
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start : j + 1]


def _fn(name: str) -> str:
    """Lift one brace-balanced `function name(...) {...}` out of the page."""
    return _balanced(ONBOARDING, ONBOARDING.index(f"function {name}("))


def _const(name: str) -> str:
    """Lift one `const NAME = ...;` — object literal or single line."""
    start = ONBOARDING.index(f"const {name} = ")
    rest = ONBOARDING[start:]
    if rest[len(f"const {name} = ")] == "{":
        return _balanced(ONBOARDING, start) + ";"
    return rest[: rest.index("\n")]


def _step_markup(step_id: str) -> str:
    """The markup of one wizard step, from its opening div to the next one."""
    start = ONBOARDING.index(f'<div id="{step_id}"')
    nxt = ONBOARDING.find('<div id="step-', start + 1)
    end = ONBOARDING.index("<script>", start) if nxt == -1 else nxt
    return ONBOARDING[start:end]


def _run_js(body: str):
    """Run the page's real builders under node and hand back the JSON result."""
    harness = "\n".join([
        _const("REVEAL_DINNER_WINDOW_FACT"),
        _const("REVEAL_NOT_A_COOK"),
        _fn("revealTitleCase"),
        _fn("revealJoinWords"),
        _fn("revealSetupLine"),
        _fn("revealPlanCounts"),
        _fn("revealPlanLine"),
        body,
    ])
    res = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout)


# ---------- the block itself ----------


def test_the_reveal_carries_a_receipt_block_above_the_day_list():
    step = _step_markup("step-reveal")
    assert '<div id="reveal-receipt"' in step, "the reveal has no receipt block"
    assert step.index('id="reveal-receipt"') < step.index('id="reveal-days"'), (
        "the receipt renders below the week it is a receipt for — it is the "
        "answer to 'am I done?', so it goes above the days"
    )
    m = re.search(r'<div id="reveal-receipt"[^>]*>(.*?)</div>', step, re.S)
    assert m and m.group(1).strip() == "", "#reveal-receipt ships with markup inside it"
    assert re.search(r'<div id="reveal-receipt"[^>]*\bhidden\b', step), (
        "#reveal-receipt is not hidden, so a receipt shape sits on screen "
        "while the week is still being built"
    )
    assert ".reveal-receipt[hidden]" in ONBOARDING, (
        "a display:flex container needs its own [hidden] rule or the attribute "
        "loses — the same guard pair .reveal-status and .reveal-days carry"
    )


def test_the_receipt_reuses_the_approval_receipts_own_pattern():
    """Eyebrow, title, lines — the same block Meals shows on approval."""
    assert 'const REVEAL_RECEIPT_EYEBROW = "YOU\'RE SET UP";' in ONBOARDING
    assert 'const REVEAL_RECEIPT_TITLE = "That\'s everything I need.";' in ONBOARDING
    for cls in ("reveal-receipt-eyebrow", "reveal-receipt-title", "reveal-receipt-line"):
        assert f".{cls} {{" in ONBOARDING, f"{cls} has no styling of its own"
    body = _fn("renderRevealReceipt")
    assert "REVEAL_RECEIPT_EYEBROW" in body and "REVEAL_RECEIPT_TITLE" in body


def test_the_receipt_is_three_lines_in_one_order():
    """
    What was saved, what Pomona did, the one thing to do next. The order is
    the whole point — a next step read before the count of what happened is
    an instruction with no reason attached.
    """
    body = _fn("renderRevealReceipt")
    saved = body.index("revealSetupLine")
    did = body.index("revealPlanLine")
    nxt = body.index("REVEAL_RECEIPT_NEXT")
    assert saved < did < nxt, "the receipt's three lines are out of order"
    assert (
        'const REVEAL_RECEIPT_NEXT = "Next: look it over and approve it, '
        "then I'll write your list.\";" in ONBOARDING
    )


def test_the_receipt_is_told_once_and_never_again():
    """
    Preferences already holds the answers; this block belongs to the end of
    setup and nowhere else. (It is also why the receipt has one home in the
    markup rather than being appended per render.)
    """
    assert ONBOARDING.count('id="reveal-receipt"') == 1
    shell = (STATIC / "shell.js").read_text()
    for line in ("That's everything I need.", "Your first week is drafted:"):
        assert line not in shell, "the end-of-setup receipt is repeated inside the app"


# ---------- line 1: what was saved, from the answers actually given ----------


@_needs_node
def test_one_adult_no_allergies_reads_back_as_itself():
    facts = {"people": 1, "allergies": 0, "dinnerWindow": "", "prepDays": []}
    out = _run_js(
        f"console.log(JSON.stringify(revealSetupLine({json.dumps(facts)})))"
    )
    assert out == "Just you, no allergies."


@_needs_node
def test_two_adults_one_allergy_and_prep_days_reads_back_as_itself():
    facts = {
        "people": 2, "allergies": 1, "dinnerWindow": "6_8",
        "prepDays": ["sunday", "wednesday"],
    }
    out = _run_js(
        f"console.log(JSON.stringify(revealSetupLine({json.dumps(facts)})))"
    )
    assert out == "2 of you, 1 allergy, dinner between 6 and 8, prep on Sunday and Wednesday."


@_needs_node
def test_a_fact_nobody_gave_is_left_out_rather_than_guessed():
    """
    "All over the place" is not a dinner window anyone can be told back, and
    no prep days means no prep clause — the line says only what was said.
    """
    facts = {
        "people": 4, "allergies": 3, "dinnerWindow": "all_over", "prepDays": [],
    }
    out = _run_js(
        f"console.log(JSON.stringify(revealSetupLine({json.dumps(facts)})))"
    )
    assert out == "4 of you, 3 allergies."


def test_the_allergy_count_comes_from_the_saved_restrictions():
    """
    currentRestrictions() writes an allergy as "allergy: peanuts", one entry
    per thing — so the count has to look for that prefix, not for members.
    """
    body = _fn("revealSetupFacts")
    assert "'allergy:'" in body
    assert "member_names" in body and "household_restrictions" in body
    assert "rhythmDinnerWindow" in body and "currentPrepDays()" in body


# ---------- line 2: what Pomona did, counted off the plan ----------


@_needs_node
def test_the_counts_come_from_the_plan_and_a_reheat_is_not_a_cook():
    """
    Same rule as the approved week's receipt (weekly_plan._is_cook): a
    reheat or takeout night is a meal but not a cook, and an away night or
    an open slot is neither.
    """
    meals = (
        [{"meal": f"Dish {i}", "slot_state": "planned"} for i in range(5)]
        + [{"meal": "Leftovers from Monday", "slot_state": "planned"} for _ in range(6)]
        + [{"meal": "Takeout night", "slot_state": "planned"} for _ in range(5)]
        + [{"meal": None, "slot_state": "planned_empty"}]
        + [{"meal": "Still deciding", "slot_state": "open"}]
    )
    out = _run_js(
        "const c = revealPlanCounts(" + json.dumps(meals) + ");"
        "console.log(JSON.stringify([c, revealPlanLine(c)]));"
    )
    counts, line = out
    assert counts == {"meals": 16, "cooks": 5}
    assert line == "Your first week is drafted: 16 meals, 5 cooks."


@_needs_node
def test_a_week_with_no_cooks_in_it_does_not_claim_any():
    out = _run_js(
        "const c = revealPlanCounts("
        + json.dumps([{"meal": "Leftovers from Sunday", "slot_state": "planned"}])
        + "); console.log(JSON.stringify(revealPlanLine(c)));"
    )
    assert out == "Your first week is drafted: 1 meal."


# ---------- the one button, and where it lands ----------


def test_the_old_button_copy_is_gone():
    for old in ("Looks good — take me in", "I'm all set — show me my week"):
        assert old not in ONBOARDING_VISIBLE, f"{old!r} is still on the screen"
    assert ">Review my week</button>" in ONBOARDING_VISIBLE


def test_review_my_week_lands_on_the_draft_review_not_the_plain_week():
    """
    The first plan is not always the week today sits in — "Next week", or a
    Sunday household folding forward, files it under a different Monday, and
    Meals otherwise opens on whichever week contains today. ?drafted=<that
    Monday> is the existing hand-back that makes it the right one, and the
    week it lands on is a DRAFT, so flows 3's review is what renders.
    """
    handler = ONBOARDING[ONBOARDING.index("document.getElementById('reveal-done-btn')"):]
    handler = handler[: handler.index("\n};") + 3]
    assert "'/week?drafted=' + encodeURIComponent(firstPlanWeekStart)" in handler
    assert "firstplan=1" not in handler, (
        "the reveal still leaves on ?firstplan=1, whose toast the receipt now "
        "says at more length"
    )
    assert "firstPlanWeekStart = data.week_start_date" in _fn("generateFirstPlanAndReveal")

    # The param only means "show me this week" for as long as shell.js reads
    # it that way; a rename there would strand this handler silently.
    shell = (STATIC / "shell.js").read_text()
    assert "weekState.showWeekStart = drafted" in shell
    assert "'drafted'" in shell


def test_the_draft_review_is_what_that_week_renders():
    """The destination is a review because the plan is a draft — shell.js
    puts Approve under the week card for exactly that state (flows 3)."""
    # Since 2026-09-11 (Build 3) the draft OPENS on the review, and that
    # is where Approve lives (reviewDecideHtml); weekDecideHtml is empty.
    shell = (STATIC / "shell.js").read_text()
    decide = shell[shell.index("function reviewDecideHtml("):]
    assert "weekPlanState(data) !== 'draft'" in decide[:400]
    assert "Approve and build my shopping list" in decide[:1200]
    step = shell[shell.index("function renderMealsStep("):]
    assert "steps.innerHTML = reviewStepHtml(data, weekState.days, true);" in step[:6000]


# ---------- the failure receipt ----------


def test_a_failed_week_gets_the_plain_receipt_and_try_again():
    assert "const REVEAL_FAILED_TITLE = 'Your answers are saved.';" in ONBOARDING
    assert (
        'const REVEAL_FAILED_LINE = "The first week didn\'t come together — '
        "tap Try again, or I'll draft it when you open the app.\";" in ONBOARDING
    )
    body = _fn("renderRevealFailedReceipt")
    assert "REVEAL_FAILED_TITLE" in body and "REVEAL_FAILED_LINE" in body
    assert "reveal-receipt-failed" in body, (
        "the failure block keeps the settled celadon tint — a receipt that "
        "looks pleased with itself over a week that never arrived"
    )
    assert ".reveal-receipt-failed {" in ONBOARDING
    step = _step_markup("step-reveal")
    assert 'id="reveal-retry-btn" type="button">Try again<' in step


def test_the_cheerful_receipt_never_renders_over_a_failure():
    generate = _fn("generateFirstPlanAndReveal")
    catch = generate[generate.index("} catch (err) {"):]
    assert "renderRevealFailedReceipt()" in catch
    assert "renderRevealReceipt(" not in catch
    # A generation that finished and produced nothing is the same news, and
    # takes the same path rather than a cheerful receipt counting zero meals.
    assert "if (!meals.length) throw" in generate
    # And the receipt is cleared at the start of every attempt, so "Try
    # again" doesn't run under the last attempt's answer.
    assert "receiptBox.hidden = true" in generate
