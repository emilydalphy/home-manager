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
    it gets no back link. Since the root band (2026-09-11) LIST never asks
    groHeadFor at all — renderGrocery shows the band and hides the crumb on
    the root, and the crumb-and-head pair on every deeper step."""
    render = SHELL_JS[SHELL_JS.index("function renderGrocery("):SHELL_JS.index("function groCaptureStoresPromptInput(")]
    assert "var onRoot = step === 'list';" in render
    assert "if (onRoot) {" in render and "back.hidden = true;" in render.split("if (onRoot) {", 1)[1][:200], (
        "the Shop root should render no crumb"
    )
    assert "var headFor = groHeadFor(data, step);" in render.split("} else {", 1)[1][:200], (
        "only the deeper steps take a head from groHeadFor"
    )


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


# --------------------------------------------------------------------------
# Rule 2 — one dock, in the same place on every screen
# --------------------------------------------------------------------------

def test_the_dock_is_one_component_not_one_per_screen():
    """"The same place on every screen" is not something two implementations
    can promise.

    Cook shipped a dock of its own with the cook journey (2026-09-10). Rule 2
    is that same strip on the other two screens that have a single action, so
    .cook-dock lost the Cook out of its name rather than gaining two siblings
    that drift apart.
    """
    assert re.search(r"^\.dock \{", SHELL_CSS, re.M), ".dock is not defined"
    # No screen defines a sticky strip of its own any more.
    assert not re.search(r"^\.cook-dock \{", SHELL_CSS, re.M)

    block = SHELL_CSS[SHELL_CSS.index(".dock {"):][:600]
    assert "position: sticky" in block
    assert "bottom: 0" in block
    assert "#" not in block, "Rule 9 — every colour goes through a token"


def test_all_three_docked_screens_use_the_shared_box():
    """Cook, Plan (both the Week root and the Review step) and Shop."""
    assert '<div class="dock cook-dock">' in SHELL_JS
    # One since 2026-09-11: a draft's root is the review, so only
    # reviewDecideHtml docks an Approve button (the week root has none).
    assert SHELL_JS.count('<div class="wk-decide dock">') == 1, (
        "the Review docks the Approve button, and nothing else does"
    )
    assert '<div class="dock wk-allset-dock">' in SHELL_JS  # the All set screen's own
    assert '<div class="dock gro-dock" id="gro-dock">' in SHELL_JS


def _gro_dock_body():
    start = SHELL_JS.index("function groDockHtml(")
    end = SHELL_JS.index('// ---------- "Maybe already home" ----------', start)
    return SHELL_JS[start:end]


def test_shops_action_left_the_scrolling_foot():
    """It used to sit at the end of .gro-foot, under the whole list.

    On a real week's groceries that put "Start the trip" — the button the
    screen exists for — a hundred rows below the fold. The foot keeps the add
    row, which is a side errand rather than what the screen is for.
    """
    start = SHELL_JS.index("function groFootHtml(")
    foot = SHELL_JS[start:SHELL_JS.index("function groDockHtml(")]
    assert "gro-primary" not in foot, "the foot still renders the screen's action"
    assert "gro-add-item" in foot, "the add row should stay in the foot"

    dock = _gro_dock_body()
    assert "gro-add-item" not in dock, "the add row is a second job; it is not the dock's"
    # Five steps have an action, and each puts exactly one fill in the dock.
    assert dock.count("gro-primary") == 5


def test_the_dock_is_the_last_thing_in_its_container():
    """A sticky footer's flow position has to be the END of the screen.

    Anywhere else and the bottom of the scroll lifts it off the ask bar with
    content stranded underneath — which is exactly what the first build did
    on the Week root: .wk-foot ("Plan next week", "More ···") still came
    after weekDecideHtml, so scrolling to the bottom left the strip floating
    82px up with the rare-actions row below it. Measured in a browser, not
    reasoned about; this test is the version that would have caught it.
    """
    # Shop: the dock is the last child of the panel's markup.
    build = SHELL_JS[SHELL_JS.index("function buildGroceryPanel("):]
    build = build[:build.index("panel.addEventListener")]
    assert build.index('id="gro-dock"') > build.index('id="gro-foot"')
    assert build.index('id="gro-foot"') > build.index('id="gro-body"')

    # Plan's two docks: the call that builds them ends its function's return
    # expression, so what follows is ";" and never " +" (another chunk of
    # markup concatenated after the strip).
    for fn, call in (("weekStepHtml", "weekDecideHtml(data)"),
                     ("reviewStepHtml", "reviewDecideHtml(data)")):
        body = SHELL_JS[SHELL_JS.index("function %s(" % fn):]
        at = body.index(call) + len(call)
        assert body[at] == ";", (
            "%s renders something after its dock (found %r) — a sticky strip's "
            "flow position has to be the end of the screen"
            % (fn, body[at:at + 60])
        )


def test_a_screen_with_no_single_action_has_no_dock():
    """The other half of rule 2, and the half that is easy to lose: Emily's
    line is "a screen with no single action has NO dock — Cook's root stays
    that way", not "every screen grows one".

    Shop's LIST returns the empty string while the shops question is up,
    because the card owns the screen's one apricot then; and an empty dock
    collapses rather than leaving a bare hairline across the bottom of a
    screen with nothing to say.
    """
    dock = _gro_dock_body()
    assert "if (canGo) return '<button type=\"button\" class=\"gro-primary\" data-gro=\"start-trip\">" in dock
    # Over the shops question, and on the just-finished trip's own screen,
    # LIST renders no dock at all. (An empty list is the exception since
    # 2026-09-11: its empty moment's next step, "Go to Plan", is the dock.)
    assert "!groStoresPromptShouldShow() && !groceryState.justFinishedTrip" in dock
    assert "return '';" in dock.split("data-gro=\"goto-plan\"", 1)[1][:80], "LIST should be able to render no dock"
    assert ".gro-dock:empty { display: none; }" in SHELL_CSS
