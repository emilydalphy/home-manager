"""Plan, Build 3 of the screen-by-screen redesign (Emily, 2026-09-11).

A draft opens on Review; the clash sits on its dish; a dish name opens the
recipe and the crumb brings you back; approval lands on an All set screen;
the week root's today row says TODAY.

Since 2026-09-18 the review is a carousel of day cards and All set is one
button (tests/test_plan_cards_2026_09_18.py); what is pinned here is the
part of Build 3 that still stands.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _fn(name):
    marker = "  async function %s(" % name if ("  async function %s(" % name) in SHELL_JS else "  function %s(" % name
    start = SHELL_JS.index(marker)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end]


def test_a_draft_root_is_the_review_with_no_crumb_and_approve_in_the_dock():
    step = _fn("renderMealsStep")
    assert "if (weekState.step === 'review' && draft) weekState.step = 'week';" in step
    assert "steps.innerHTML = reviewStepHtml(data, weekState.days, true);" in step
    review = _fn("reviewStepHtml")
    # The root form has no in-flow title since the root band (2026-09-11):
    # the week's dates, title and DRAFT chip are the band's. Only the
    # deeper, approved-week form draws the "Check the week." title under
    # its crumb.
    assert "? weekSuggestedNoteHtml(data)" in review.split("var head = root", 1)[1][:80]
    assert review.count('<h1 class="wk-title">Check the week.</h1>') == 1
    assert "weekStepHeadHtml" not in SHELL_JS
    assert "rv-invite" not in review
    assert 'id="week-check-btn"' not in SHELL_JS
    assert "Tweak it with me" not in SHELL_JS


def test_no_clash_card_sits_on_a_dish_any_more():
    """Build 3 put the hard clash on its dish's row, with "Keep it anyway".
    Emily, 2026-09-20: "if there is a conflict for an allergy, just don't
    suggest anything that fits that" — so since 2026-09-21 a dish someone
    can't have is never drafted (app/tools/allergen_gate.py), and the card,
    its button, its lookup and its CSS are gone with it — including the
    layout bug where the card overlapped the rows beneath it."""
    row = _fn("wkMealRowHtml")
    assert "wkSettleFor" not in SHELL_JS
    assert "rv-settle" not in row and "data-wk-settle-keep" not in SHELL_JS
    assert "Keep it anyway" not in SHELL_JS
    assert "data-rv-settle-swap" not in SHELL_JS
    assert ".rv-settle" not in SHELL_CSS and ".wk-settle" not in SHELL_CSS


def test_a_dish_name_opens_the_meal_step_with_a_crumb_back_to_the_week():
    # The name on a row opens the Meal step (the same screen the Day step's
    # card opens), not cook mode — see
    # tests/test_meal_opens_the_same_way_everywhere.py for why.
    assert 'class="wk-row-name dish-link" data-wk-meal="' in SHELL_JS
    wiring = SHELL_JS[SHELL_JS.index("[data-wk-meal]"):]
    assert "goMealsStep('meal', { slot: btn.getAttribute('data-wk-meal'), back: back });" in wiring[:1200]
    assert "openRecipeFor" not in wiring[:1200]
    # ...and from Check the week the crumb says so.
    assert "'Check the week'" in _fn("mealStepHtml")


def test_each_row_carries_the_cook_time():
    meta = _fn("wkRowMeta")
    assert "return entry.meta || '';" in meta
    assert "'from ' + dayName(entry.leftover_from.date, { weekday: 'long' })" in meta


def test_approval_lands_on_an_all_set_screen_in_spruce():
    approve = _fn("submitWeekApproval")
    assert "weekState.step = 'allset';" in approve
    allset = _fn("allSetStepHtml")
    assert "All set." in allset and "' is planned.'" in allset and "'Week 1 is planned.'" in allset
    assert "receipt.meals" in allset and "receipt.recipes" in allset and "receipt.list_count" not in allset
    assert '<div class="dock wk-allset-dock">' in allset
    assert ".tab-panel.is-allset { background: var(--spruce); }" in SHELL_CSS
    # No asks on it any more: the freezer question is the next step.
    assert "wk-allset-asks" not in SHELL_JS and "renderAllSetAsks" not in SHELL_JS
    assert "goAfterWeekSet(panel, data)" in _fn("wireAllSetStep")


def test_the_today_tile_says_so():
    assert "(day.isToday ? 'TODAY' : dayName(day.date, { weekday: 'short' }).slice(0, 3).toUpperCase())" in _fn("weekTileHtml")
    assert ".wk-tile.is-today { background: var(--sand); border-color: var(--hairline-deep); }" in SHELL_CSS
