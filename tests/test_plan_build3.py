"""Plan, Build 3 of the screen-by-screen redesign (Emily, 2026-09-11).

A draft opens on Review; the clash sits on its dish; a dish name opens the
recipe and the crumb brings you back; cook times on Which days; approval
lands on an All set screen; the week root's today row says TODAY.
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
    assert "weekStepHeadHtml(data, days)" in review  # the week's own head on the root form
    assert "rv-invite" in review
    assert "weekDecideHtml(data) {\n    return '';" in SHELL_JS
    assert 'id="week-check-btn"' not in SHELL_JS
    assert "Tweak it with me" not in SHELL_JS


def test_the_clash_sits_on_its_dish_and_is_a_word_on_its_day():
    row = _fn("reviewDishRowHtml")
    assert "reviewSettleFor(dish)" in row
    assert 'class="rv-settle"' in row and "data-rv-settle-swap" in row and "data-rv-settle-keep" in row
    assert "'not for ' + settle.member" in SHELL_JS
    assert ".rv-day-clash {" in SHELL_CSS
    # The old card above the title is not rendered for a draft any more.
    assert "if (approve) approve.hidden = !onRoot || draft;" in SHELL_JS


def test_a_dish_name_opens_the_recipe_and_names_this_view_on_the_way_back():
    assert 'class="rv-dish-name dish-link" data-rv-recipe="' in SHELL_JS
    assert "data-rv-recipe-date" in SHELL_JS
    wiring = SHELL_JS[SHELL_JS.index("function reviewOrigin()"):]
    assert "reviewState.view === 'days' ? 'Which days' : 'What we’re eating'" in wiring[:300]
    assert "tab: 'week'" in wiring[:300]


def test_which_days_carries_the_cook_time_on_every_line():
    line = _fn("reviewSlotLineHtml")
    assert "entry.meta" in line and 'class="rv-slot-meta"' in line
    face = _fn("reviewDayFaceLine")
    assert "else if (dinner.meta) note = dinner.meta;" in face


def test_approval_lands_on_an_all_set_screen_in_spruce():
    approve = _fn("approveWeek")
    assert "weekState.step = 'allset';" in approve
    allset = _fn("allSetStepHtml")
    assert "All set." in allset and "is planned, and the list is built." in allset
    assert "receipt.meals" in allset and "receipt.cooks" in allset and "receipt.list_count" in allset
    assert '<div class="dock wk-allset-dock">' in allset
    assert ".tab-panel.is-allset { background: var(--spruce); }" in SHELL_CSS
    # The two asks ride along as lines, and only there — the root's receipt
    # card is dismissed by the screen.
    assert "renderWeekReceipt(row, panel, data, true);" in _fn("renderAllSetAsks")
    assert "if (data.weekly_plan_id) setWeekReceiptDismissed(data.weekly_plan_id, true);" in approve


def test_the_today_row_says_so():
    assert "(day.isToday ? 'TODAY' : dayName(day.date, { weekday: 'short' }).slice(0, 3).toUpperCase())" in _fn("weekRowHtml")
    assert ".wk-day-row.is-today { background: var(--sand); }" in SHELL_CSS
