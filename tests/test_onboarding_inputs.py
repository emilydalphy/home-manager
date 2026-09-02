"""
Onboarding must not throw away an answer someone has typed.

Two onboarding steps collect a list by typing an item and pressing Enter:
"anything the house just won't eat" and the custom cuisine on "what are you
excited to eat more of". Both used to commit the typed text on Enter and
nowhere else — so typing a food and tapping Continue, which is the obvious
thing to do on a phone where the keyboard's return key is easy to miss,
silently discarded it. Verified in a real browser against `main` before the
fix: the answer came back as an empty list, while the text was still sitting
in a box on a step that had already scrolled away.

That failure is invisible. Nothing errors, the wizard advances normally, and
the household simply never gets asked again — their "no bell peppers" just
isn't in the app, and every week is planned as though they'd never said it.

**Scope and limits, stated plainly.** This repo has no JavaScript test
harness — `tests/` is Python only, and putting a browser in CI is a bigger
change than this fix warrants. So the behaviour was verified by driving the
real page in Chromium (type -> tap Continue -> read back the collected
list), and what is pinned here are the two things a future edit is most
likely to break:

1. that each Continue handler still commits the pending input, and
2. that each commit function still actually does the work.

The second one exists because the first is not enough on its own: an earlier
version of this file checked only the wiring, and a reviewer showed it
passed happily with the commit functions gutted to empty bodies — the exact
bug back, with a green suite. A regression test that misses the regression
is worse than none, because it makes the next person confident.

These are still static checks over source text, not behaviour. They cannot
catch a subtle logic error inside a commit function; they can catch it being
removed, emptied, or unhooked.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


ONBOARDING = (Path(__file__).resolve().parent.parent / "static" / "onboarding.html").read_text()


def _click_handler_body(element_id: str) -> str:
    """
    The body of the click handler for an element, however it is attached.

    Accepts both `.onclick = () => {...}` and
    `.addEventListener('click', () => {...})`, because the file already
    uses both styles and switching between them must not fail a test about
    something else entirely. Anchored to the element id and bounded by the
    next handler registration, so it cannot silently match a different
    block further down the file.
    """
    pattern = (
        r"getElementById\('" + re.escape(element_id) + r"'\)\s*\.\s*"
        r"(?:onclick\s*=|addEventListener\(\s*'click'\s*,)"
    )
    start = re.search(pattern, ONBOARDING)
    assert start, (
        f"could not find a click handler for #{element_id} in onboarding.html — "
        f"if it was renamed or rewired, update this test rather than deleting it"
    )
    rest = ONBOARDING[start.end():]
    # Stop at the next handler registration, so the body cannot bleed into
    # unrelated code and accidentally satisfy an assertion.
    nxt = re.search(r"document\.getElementById\(|document\.querySelector", rest)
    return rest[: nxt.start()] if nxt else rest


def _function_body(name: str) -> str:
    """The body of a top-level `function name(...) { ... }`, brace-matched."""
    start = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", ONBOARDING)
    assert start, f"{name} is missing from onboarding.html"
    i, depth = start.end(), 1
    while i < len(ONBOARDING) and depth:
        if ONBOARDING[i] == "{":
            depth += 1
        elif ONBOARDING[i] == "}":
            depth -= 1
        i += 1
    return ONBOARDING[start.end(): i - 1]


@pytest.mark.parametrize(
    "step, button_id, commit_fn",
    [
        ("won't eat", "wont-eat-next", "commitWontEatInput"),
        ("excited about", "excited-next", "commitExcitedCustom"),
    ],
)
def test_continue_commits_whatever_is_still_typed_in_the_box(step, button_id, commit_fn):
    """
    Pressing Enter is how you add another one; it is not the only way
    people finish. Continue has to sweep up the pending text too.
    """
    assert f"{commit_fn}()" in _click_handler_body(button_id), (
        f"the Continue button on the '{step}' onboarding step no longer calls "
        f"{commit_fn}(), so anything typed but not entered with the Enter key "
        f"is silently discarded. This is invisible to the household — the "
        f"wizard advances normally and the answer is simply gone."
    )


@pytest.mark.parametrize(
    "input_id, commit_fn",
    [("wont-eat-input", "commitWontEatInput"), ("excited-custom", "commitExcitedCustom")],
)
def test_enter_still_adds_an_item(input_id, commit_fn):
    """
    The fix must have joined Enter, not replaced it — adding several items
    in a row is the whole point of these steps.
    """
    match = re.search(
        r"getElementById\('" + re.escape(input_id) + r"'\)\.addEventListener\('keydown'(.*?)\n\s*\}\);",
        ONBOARDING,
        re.S,
    )
    assert match, f"the Enter-key handler for #{input_id} is gone"
    assert f"{commit_fn}()" in match.group(1), (
        f"#{input_id}'s Enter handler no longer calls {commit_fn}(), so typing "
        f"an item and pressing Enter adds nothing"
    )


def test_the_wont_eat_commit_actually_records_the_value():
    """
    Wiring alone is not the fix.

    A reviewer emptied this function's body and every wiring check above
    still passed while the original bug was fully back. So the body is
    pinned too: it has to read the input and put the value in the list
    that gets submitted.
    """
    body = _function_body("commitWontEatInput")
    assert "wont-eat-input" in body, "commitWontEatInput no longer reads the input box"
    assert "wontEatItems.push" in body, (
        "commitWontEatInput no longer adds the typed value to wontEatItems — "
        "the list that is actually submitted as wont_eat. Anything typed is "
        "silently discarded again."
    )


def test_the_excited_commit_actually_records_the_value():
    """Same reasoning as the won't-eat commit above."""
    body = _function_body("commitExcitedCustom")
    assert "excited-custom" in body, "commitExcitedCustom no longer reads the input box"
    assert "addCustomChip" in body, (
        "commitExcitedCustom no longer turns the typed cuisine into a chip, so "
        "it never reaches excited_about"
    )


def test_typing_a_cuisine_that_is_already_a_chip_does_not_duplicate_it():
    """
    Tapping the "Italian" preset and then also typing "Italian" used to
    submit it twice. Harmless-looking, but these answers feed week
    generation, where a repeated cuisine reads as a stronger preference
    than the household expressed.

    Reachable before this work via Enter; committing on Continue made it
    easy to hit by accident, so it is fixed and pinned here.
    """
    body = _function_body("addCustomChip")
    assert "toLowerCase()" in body and "querySelectorAll('.chip')" in body, (
        "addCustomChip no longer checks whether a chip with this value already "
        "exists, so typing the name of a preset adds a second copy of it"
    )
