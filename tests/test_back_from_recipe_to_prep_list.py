"""Back from a recipe opened on the prep list goes back to the prep list.

Emily, 2026-11-01: "when I'm on the prep schedule right now, then I click a
recipe, if I want to go back it'll bring me to the cook screen vs the prep
schedule". The focus click cleared the origin as if every recipe were opened
from the Cook root, so cookExitFocus always landed on the root.
"""

from test_tap_a_meal_opens_recipe import _extract, _needs_node, _run_node


def _harness(body: str) -> str:
    return (
        "var scrollEl = { scrollTop: 420 };\n"
        "var renders = [];\n"
        "var cookState = { screen: 'session', sessionDate: '2026-11-01', focusOrigin: null,\n"
        "  pendingScrollTop: false, pendingScrollTo: null,\n"
        "  data: { prep_sessions: [{ date: '2026-11-01', weekday: 'Sunday' }] } };\n"
        "function dayName(){ return 'Sunday'; }\n"
        "function stopCookVoice(){}\n"
        "function renderCook(){ renders.push(cookState.screen); }\n"
        "function activateTab(){ renders.push('activateTab'); }\n"
        "function openAskSheet(){}\n"
        "function goMealsStep(){}\n"
        + _extract("cookSessionOn") + "\n"
        + _extract("cookSessionOrigin") + "\n"
        + _extract("cookExitFocus") + "\n"
        + body
    )


@_needs_node
def test_back_from_a_recipe_opened_on_the_prep_list_lands_on_the_list():
    out = _run_node(_harness(
        "cookState.focusOrigin = cookSessionOrigin(cookState.sessionDate);\n"
        "var label = cookState.focusOrigin.label;\n"
        "cookState.screen = 'focus';\n"
        "cookExitFocus();\n"
        "console.log(JSON.stringify({ label: label, screen: cookState.screen,\n"
        "  date: cookState.sessionDate, scrollTo: cookState.pendingScrollTo,\n"
        "  origin: cookState.focusOrigin, renders: renders }));\n"
    ))
    assert out == {
        "label": "Sunday prep",
        "screen": "session",
        "date": "2026-11-01",
        "scrollTo": 420,
        "origin": None,
        "renders": ["session"],
    }


@_needs_node
def test_a_prep_day_that_is_gone_falls_back_to_the_cook_root():
    out = _run_node(_harness(
        "cookState.focusOrigin = cookSessionOrigin(cookState.sessionDate);\n"
        "cookState.data.prep_sessions = [];\n"
        "cookState.screen = 'focus';\n"
        "cookExitFocus();\n"
        "console.log(JSON.stringify(cookState.screen));\n"
    ))
    assert out == "overview"


def test_the_focus_click_keeps_the_prep_list_as_the_origin():
    click = _extract("onCookClick")
    focus_branch = click.split("if (what === 'focus')", 1)[1].split("return;", 1)[0]
    assert "cookState.screen === 'session'" in focus_branch
    assert "cookSessionOrigin(cookState.sessionDate)" in focus_branch


def test_the_scroll_restore_honours_a_place_to_land_on():
    fn = _extract("renderCook")
    assert "cookState.pendingScrollTo !== null ? cookState.pendingScrollTo" in fn
    assert "cookState.pendingScrollTo = null;" in fn
