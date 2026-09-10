"""Navigation v2, part 2 — one way back, and one dock for the screen's action.

Emily, 2026-09-09, reviewing the navigation mockups. Both rules answer
struggles Julia reported in her first beta session: she could not tell how to
get back out of a screen, or where the thing she came to do had gone.

  Rule 1 — every screen deeper than a tab root carries exactly ONE breadcrumb,
           and it NAMES its parent ("‹ This week", "‹ Shop"), never a bare
           arrow and never the browser's back.
  Rule 2 — the screen's single action lives in a fixed dock above the tab bar,
           the same place on every screen, and does not scroll away. A screen
           with no single action has no dock.

These are checkable claims, so they are checked here rather than trusted.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Rule 1 — one way back, and it names its parent
# --------------------------------------------------------------------------

# The four tab names as a household reads them since part 1 (2026-09-09).
# Their predecessors are listed beside them because the failure this guards
# against is a crumb that still says the OLD word above a tab bar showing the
# new one — which is exactly what shipped: part 1 grepped markup, and four
# crumb labels are JS object values, so they were missed.
RETIRED_TAB_WORDS = {"Today": "Now", "Meals": "Plan", "Grocery": "Shop", "Kitchen": "Cook"}


def _crumb_buttons():
    """Every back link shell.js renders, as (class attribute, label) pairs."""
    return re.findall(r'<button type="button" class="(crumb[^"]*)"[^>]*>([^<\']*)', SHELL_JS)


def test_every_back_link_is_the_one_crumb_component():
    """One class, not three.

    .wk-back (Meals) and .gro-back (Grocery) were byte-identical rule blocks
    kept in sync by hand, and .cook-focus-back was a third at a different font
    size — so the three screens a household actually moves between disagreed
    about how the same control looks. They are one .crumb now.
    """
    for dead in (".wk-back", ".gro-back", ".cook-focus-back"):
        assert not re.search(r"^\s*\%s\s*[,{:\[]" % re.escape(dead), SHELL_CSS, re.M), (
            "%s still has a CSS rule of its own — the crumb is one component now" % dead
        )
        assert 'class="%s"' % dead.lstrip(".") not in SHELL_JS, (
            "%s is still rendered — use class=\"crumb\"" % dead
        )

    assert re.search(r"^\.crumb \{", SHELL_CSS, re.M), ".crumb is not defined"
    # The spruce variant is a modifier, not a fourth component.
    assert re.search(r"^\.crumb\.on-spruce \{", SHELL_CSS, re.M)

    # And every deeper screen actually uses it: Meals' three steps, Grocery's
    # shared one, Cook's three.
    assert len(_crumb_buttons()) >= 7


def test_no_crumb_names_a_tab_by_its_retired_name():
    """A crumb names its parent as the household reads it today.

    Before this ticket, tapping tonight's dinner on Now opened cook mode with
    a back link reading "‹ Today" — a tab that no longer exists by that name
    anywhere on screen.
    """
    origins = re.findall(r"\{ label: '([^']+)', tab: '([^']+)' \}", SHELL_JS)
    assert origins, "no crumb origins found — has openRecipeFor's shape changed?"
    for label, tab in origins:
        assert label not in RETIRED_TAB_WORDS, (
            "a crumb still names the %r tab; it is called %r now"
            % (label, RETIRED_TAB_WORDS.get(label))
        )

    # The literal crumb labels in markup, same rule.
    for _cls, label in _crumb_buttons():
        first_word = label.replace("&lsaquo;", "").replace("‹", "").strip()
        assert first_word not in RETIRED_TAB_WORDS, (
            "crumb %r names a retired tab name" % label
        )


def _gro_head_body():
    start = SHELL_JS.index("function groHeadFor(")
    return SHELL_JS[start:SHELL_JS.index("// ---------- LIST ----------", start)]


# Grocery's deeper steps. LIST is the tab root and correctly has no crumb.
GRO_DEEPER_STEPS = ["sort", "sorthow", "sortall", "next", "trip", "wrap"]


@pytest.mark.parametrize("step", GRO_DEEPER_STEPS)
def test_every_deeper_grocery_step_offers_a_way_back(step):
    """WRAP UP and WHERE NEXT shipped with `back: ''` — no crumb at all.

    On those two screens the tab bar was the only way out, which drops the
    whole branch rather than going up one level. Both already LAND on the list
    (see the 'step-back' handler), so this is the label telling the truth
    about what the handler does, not a new destination.
    """
    body = _gro_head_body()
    block = body[body.index("'%s'" % step):]
    back = re.search(r"back: ([^,\n]+)", block)
    assert back, "step %r has no back at all" % step
    expr = back.group(1).strip()

    # Every branch of the expression, not just the expression as a whole:
    # WHERE NEXT's crumb is a ternary, and it was the ELSE branch that was
    # empty — reachable whenever the step is entered without a stop just
    # finished behind it. A test that only looked at the whole expression
    # called that one fine.
    branches = [p.strip() for p in re.split(r"[?:]", expr)] if "?" in expr else [expr]
    for branch in branches:
        if not branch or branch.startswith("groceryState") or branch.startswith("("):
            continue  # the condition itself, not a label
        assert branch not in ("''", '""'), (
            "step %r can render no crumb (branch %r of %s) — every screen "
            "deeper than the root gets one" % (step, branch, expr)
        )


def test_the_grocery_root_has_no_crumb():
    """The other half of the rule: a tab root is not deeper than anything, so
    it gets no back link. LIST is the fallthrough return at the end."""
    body = _gro_head_body()
    tail = body[body.rindex("return {"):]
    assert "back: ''" in tail, "the Shop root should render no crumb"


def test_a_crumb_never_uses_the_browsers_back():
    """"Up one level, named" is not the same as history.back(), which after
    any wandering (a day, a meal, back, another day) points at the previous
    VIEW rather than the parent — so a link reading "‹ This week" could land
    on a meal. The back GESTURE keeps its own correct meaning through the
    shell's popstate listener; the crumb never calls it."""
    # Comments stripped first — both handlers carry a comment explaining why
    # they do NOT call it, and a test that trips over the explanation would be
    # checking the prose rather than the code.
    for handler, marker in (
        ("wireMealsStep", "data-wk-back]"),
        ("groHandleClick", "case 'step-back':"),
    ):
        start = SHELL_JS.index(marker)
        window = SHELL_JS[start:start + 1200]
        code = re.sub(r"//[^\n]*", "", window)
        assert "history.back()" not in code, (
            "%s's crumb reaches for the browser's back" % handler
        )
