"""A dish name is a link to its recipe, everywhere it appears.

Emily, 2026-09-09, after testing the app herself: "There also needs to be a
way where if you click the meal anywhere throughout the app, it should bring
you to the screen with the recipe on it."

There is exactly one screen in this app with a recipe on it — cook mode, the
focused single-meal step of Kitchen — plus, as of this branch, the same
recipe panel rendered read-only on Meals' Meal step (`plain` mode of the
same renderer, so the two can never disagree about a dish). Everything below
runs the screen's OWN functions under node against a small stub rather than
reading the source for the right words: the bug being fixed is "the name
looks tappable and nothing happens", and a source-marker test cannot see
that.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text()
SHELL_CSS = (REPO / "static" / "shell.css").read_text()

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to execute the screen's own functions",
)


def _extract(name: str, source: str = SHELL_JS) -> str:
    """Lift one brace-balanced `function name(...) {...}` out of the file."""
    start = source.index(f"function {name}(")
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


def _run_node(harness: str):
    res = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_ESCAPE = (
    "function escapeHtml(s){return String(s == null ? '' : s)"
    ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')"
    ".replace(/\"/g,'&quot;').replace(/'/g,'&#39;');}\n"
)


# ---------------------------------------------------------------- the door

def test_one_door_every_dish_tap_goes_through():
    """openRecipeFor is where the tab switch, the focus target and the back
    link are decided — once, not five times."""
    assert "function openRecipeFor(target, origin)" in SHELL_JS
    fn = _extract("openRecipeFor")
    assert "cookState.focusOrigin = origin || null;" in fn
    assert "activateTab('kitchen', true, { cookFocus: target });" in fn


def test_the_back_link_never_calls_history_back():
    """This repo's rule: back LINKS go up a level by name; the back GESTURE
    keeps history's own meaning through the one popstate listener."""
    assert "history.back()" not in _extract("cookExitFocus")
    assert "history.back()" not in _extract("openRecipeFor")


@_needs_node
def test_the_back_link_says_kitchen_until_something_else_says_otherwise():
    harness = (
        "var cookState = { focusOrigin: null };\n"
        + _extract("cookBackLabel") + "\n"
        + "var out = [cookBackLabel()];\n"
        + "cookState.focusOrigin = { label: 'Today', tab: 'today' };\n"
        + "out.push(cookBackLabel());\n"
        + "cookState.focusOrigin = { label: 'Monday', tab: 'week' };\n"
        + "out.push(cookBackLabel());\n"
        + "console.log(JSON.stringify(out));\n"
    )
    assert _run_node(harness) == ["Kitchen", "Today", "Monday"]


def test_leaving_the_recipe_lands_back_where_it_was_opened_from():
    fn = _extract("cookExitFocus")
    # Kitchen's own screen is reset first, whichever tab we end up on —
    # otherwise the tab reopens on a cook screen nobody left it on.
    assert fn.index("cookState.screen = 'overview';") < fn.index("origin.tab")
    assert "activateTab(origin.tab, true);" in fn
    # ...and back to the exact Meals step, not just the tab.
    assert "goMealsStep(origin.mealsStep || 'meal'" in fn
    # ...and, for a dish tapped in a reply, back to the conversation.
    assert "openAskSheet()" in fn


def test_entering_from_inside_kitchen_clears_the_origin():
    """A row tapped on the Kitchen root really did come from Kitchen — a
    stale origin would send its back link to a tab nobody was on."""
    click = _extract("onCookClick")
    focus_branch = click.split("if (what === 'focus')", 1)[1].split("return;", 1)[0]
    assert "cookState.focusOrigin = null;" in focus_branch
    assert "cookState.focusOrigin = null;" in _extract("cookEnterSession")


# --------------------------------------------- what does and doesn't link

@_needs_node
def test_a_reheat_and_a_handed_back_slot_are_not_links():
    """A freeform meal with no saved recipe still opens (and says plainly
    there is no recipe); a reheat night, an away night and an open slot are
    not ways into a recipe at all — the rule Kitchen's own rows already
    follow."""
    harness = (
        _extract("isSnackSlot") + "\n"
        + _extract("recipeTargetForEntry") + "\n"
        + "var cases = {\n"
        + "  planned: { state: 'planned', entry_id: 7, title: 'Chicken Tacos', source: 'plan' },\n"
        + "  freeform: { state: 'planned', entry_id: 8, title: 'Toast', source: 'plan' },\n"
        + "  reheat: { state: 'planned', entry_id: 9, title: 'Bulgogi', source: 'leftovers' },\n"
        + "  away: { state: 'planned_empty', entry_id: 10, title: 'Out' },\n"
        + "  open: { state: 'open', entry_id: 11, title: 'Your call' },\n"
        + "  missing: null\n"
        + "};\n"
        + "var out = {};\n"
        + "Object.keys(cases).forEach(function (k) {\n"
        + "  out[k] = recipeTargetForEntry(cases[k], '2026-09-10', 'dinner');\n"
        + "});\n"
        + "out.snack2 = recipeTargetForEntry(cases.planned, '2026-09-10', 'snack2');\n"
        + "console.log(JSON.stringify(out));\n"
    )
    got = _run_node(harness)
    assert got["planned"] == {
        "entryId": 7, "date": "2026-09-10", "slot": "dinner", "title": "Chicken Tacos",
    }
    assert got["freeform"]["entryId"] == 8
    for dead in ("reheat", "away", "open", "missing"):
        assert got[dead] is None, f"{dead} should not be a link into a recipe"
    # Kitchen's rows carry the backend's plain 'snack', never our index key.
    assert got["snack2"]["slot"] == "snack"


@_needs_node
def test_todays_dish_names_are_links_and_only_a_cook_has_a_recipe():
    cook = {
        "id": "cook:7", "kind": "cook", "title": "Chicken Tacos", "entry_id": 7,
        "date": "2026-09-10", "slot": "dinner",
        "action": {"label": "Cook this", "target": {
            "tab": "kitchen",
            "cookFocus": {"entryId": 7, "date": "2026-09-10", "slot": "dinner", "title": "Chicken Tacos"},
        }},
    }
    reheat = {"id": "reheat:9", "kind": "reheat", "title": "Bulgogi", "entry_id": 9,
              "action": {"label": "Mark eaten", "target": {"kind": "check_meal", "entryId": 9}}}
    shop = {"id": "shop", "kind": "shop", "title": "Shop for the week", "entry_id": None,
            "action": {"label": "Open the list", "target": {"tab": "grocery"}}}
    # A payload cached before moves.py carried the focus target still works.
    old_cook = {"id": "cook:7", "kind": "cook", "title": "Chicken Tacos", "entry_id": 7,
                "date": "2026-09-10", "slot": "dinner", "action": {"label": "Cook this", "target": {}}}
    harness = (
        _ESCAPE
        + _extract("moveRecipeTarget") + "\n"
        + _extract("moveDishHtml") + "\n"
        + "console.log(JSON.stringify({\n"
        + f"  cook: moveDishHtml({json.dumps(cook)}, 'hero-dish'),\n"
        + f"  reheat: moveDishHtml({json.dumps(reheat)}, 'rest-row-title'),\n"
        + f"  shop: moveDishHtml({json.dumps(shop)}, 'rest-row-title'),\n"
        + f"  old: moveRecipeTarget({json.dumps(old_cook)})\n"
        + "}));\n"
    )
    got = _run_node(harness)
    assert 'data-move-dish="cook:7"' in got["cook"]
    assert 'class="hero-dish dish-link"' in got["cook"]
    # Nothing to open behind either of these, so neither pretends.
    assert "<button" not in got["reheat"] and "Bulgogi" in got["reheat"]
    assert "<button" not in got["shop"]
    assert got["old"] == {
        "entryId": 7, "date": "2026-09-10", "slot": "dinner", "title": "Chicken Tacos",
    }


def test_a_done_row_keeps_its_dish_name_tappable():
    """A cooked dinner is exactly the name someone taps wanting to see what
    went into it. The ROW stops being the move's button (the tick is the
    only control left); the NAME goes on being a link."""
    fn = _extract("moveRowHtml")
    assert "moveRecipeTarget(move)" in fn
    assert 'rest-row-title dish-link' in fn
    # And a tap on the name must never run the row's own action — a reheat's
    # action IS the tick, and tapping a name is "show me this", not "do it".
    wiring = SHELL_JS.split("data-move-dish]", 1)[1][:600]
    assert "openRecipeFor" in wiring and "runTodayMoveAction" not in wiring


def test_todays_three_plain_dish_names_now_go_through_the_link_helper():
    for site in (
        "moveDishHtml(move, 'hero-dish nextup-dish' + dishSizeClass(move.title))",
        "moveDishHtml(move, 'tomorrow-title')",
    ):
        assert site in SHELL_JS, f"{site} — a Today dish name is still plain text"
    # Tomorrow's move is not in the day's own list, so the lookup has to
    # know about it or the tap does nothing.
    assert "data.tomorrow && data.tomorrow.id === id" in _extract("todayMoveById")


# ------------------------------------------------- the recipe, on Meals

@_needs_node
def test_the_meal_step_shows_the_recipe_and_none_of_its_controls():
    """One renderer, two frames: cook mode gets the working recipe, Meals'
    Meal step gets the same words with nothing on them that writes. Every
    control in cookDetailHtml lands through onCookClick/renderCook, which
    only ever redraw the Kitchen panel."""
    meal = {
        "meal": "Chicken Tacos", "has_full_recipe": True, "default_servings": 4,
        "cooked_status": "pending", "entry_id": 7, "reasoning": "Quick on a school night.",
        "advance_prep_notes": "Marinate the night before.",
        "ingredients": [{"qty": "500 g", "item": "chicken thigh"}, {"qty": "8", "item": "tortillas"}],
        "instructions": ["Marinate the chicken.", "Griddle for 4 minutes a side."],
        "advance_prep_step_indices": [1],
    }
    harness = (
        _ESCAPE
        + "var COOK_VOICE_ENABLED = false;\n"
        + "var cookState = { focusStepsChecked: {}, focusOrigin: null };\n"
        + "var COOK_ICONS = { check: '<svg/>', mic: '<svg/>' };\n"
        + _extract("cookBackLabel") + "\n"
        + _extract("cookFocusEndHtml") + "\n"
        + _extract("cookStepLi") + "\n"
        + _extract("cookInstructionsHtml") + "\n"
        + _extract("cookDetailHtml") + "\n"
        + _extract("mealRecipeCardHtml") + "\n"
        + "console.log(JSON.stringify({\n"
        + f"  plain: cookDetailHtml({json.dumps(meal)}, 'meal', false, true),\n"
        + f"  cooking: cookDetailHtml({json.dumps(meal)}, 3, false, false),\n"
        + f"  card: mealRecipeCardHtml({json.dumps(meal)}),\n"
        + "  reheat: mealRecipeCardHtml({ meal: 'Bulgogi', is_leftovers: true }),\n"
        + "  none: mealRecipeCardHtml(null)\n"
        + "}));\n"
    )
    got = _run_node(harness)
    plain, cooking = got["plain"], got["cooking"]

    # The recipe really is there.
    for wanted in ("Ingredients", "Instructions", "chicken thigh", "tortillas",
                   "Griddle for 4 minutes a side.", "Advance prep", "Do ahead", "Day of"):
        assert wanted in plain, f"the reading copy lost {wanted!r}"

    # ...and not one thing on it writes.
    for control in ('data-cook="serves"', 'data-cook="voice"', 'data-cook="check-step"',
                    'data-cook="fill"', 'data-cook="focus-check"', 'data-cook="why"',
                    "cook-focus-end", "cook-serves", "cook-mic"):
        assert control not in plain, f"the reading copy still carries {control!r}"
    # The ids are handles for exactly those controls; a second copy of one
    # would have getElementById reaching the wrong screen.
    assert "id=" not in plain

    # The cooking copy is untouched — every one of those is still there.
    for control in ('data-cook="serves"', 'data-cook="check-step"', 'id="cook-ings-3"',
                    "cook-focus-end"):
        assert control in cooking, f"cook mode lost {control!r}"

    # The card wraps it, and says nothing at all where there is nothing to
    # say: a reheat night is a line, and an entry the Cook view has no card
    # for hasn't loaded (or isn't cookable).
    assert "The recipe" in got["card"] and "wk-recipe-card" in got["card"]
    assert got["reheat"] == "" and got["none"] == ""


@_needs_node
def test_a_dish_with_no_saved_recipe_says_so_rather_than_pretending():
    harness = (
        _ESCAPE
        + "var COOK_VOICE_ENABLED = false;\n"
        + "var cookState = { focusStepsChecked: {} };\n"
        + _extract("cookStepLi") + "\n"
        + _extract("cookInstructionsHtml") + "\n"
        + _extract("cookDetailHtml") + "\n"
        + _extract("mealRecipeCardHtml") + "\n"
        + "console.log(JSON.stringify(mealRecipeCardHtml("
        + "{ meal: 'Takeaway', has_full_recipe: false })));\n"
    )
    html = _run_node(harness)
    assert "no saved recipe detail" in html


def test_the_meal_step_renders_the_recipe_card():
    assert "mealRecipeCardHtml(cookMeal)" in _extract("mealStepHtml")
    # And the picker above it is borrowed from the same screen in the same
    # way — this is the established pattern here, not a new one.
    assert "cookAheadHtml(cookMeal)" in _extract("mealStepHtml")


# ------------------------------------------------ dish names in chat prose

_WEEK_MENU = {
    "days": [
        {
            "date": "2026-09-10",
            "breakfast": {"state": "planned", "entry_id": 1, "title": "Baked Oatmeal Cups", "source": "plan"},
            "lunch": {"state": "planned_empty", "entry_id": 2, "title": "Out", "need": "away"},
            "dinner": {"state": "planned", "entry_id": 3, "title": "Chicken Tacos", "source": "plan"},
            "snacks": [{"state": "planned", "entry_id": 4, "title": "Apple slices", "source": "plan"}],
        },
        {
            "date": "2026-09-11",
            "breakfast": {"state": "planned", "entry_id": 5, "title": "Chicken", "source": "plan"},
            "lunch": None,
            "dinner": {"state": "planned", "entry_id": 6, "title": "Bulgogi", "source": "leftovers"},
            "snacks": [],
        },
    ]
}


def _linkify(reply_html: str, menu: dict = None):
    harness = (
        _ESCAPE
        + "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
        + _extract("isSnackSlot") + "\n"
        + _extract("snackSlotKey") + "\n"
        + _extract("daySlotEntry") + "\n"
        + _extract("recipeTargetForEntry") + "\n"
        + _extract("escapeRegExp") + "\n"
        + "var dishIndex = { entries: [], re: null };\n"
        + _extract("setDishIndex") + "\n"
        + _extract("linkifyDishNames") + "\n"
        + f"setDishIndex({json.dumps(menu if menu is not None else _WEEK_MENU)});\n"
        + f"console.log(JSON.stringify({{ html: linkifyDishNames({json.dumps(reply_html)}), "
        + "titles: dishIndex.entries.map(function (e) { return e.title; }) }));\n"
    )
    return _run_node(harness)


@_needs_node
def test_the_index_holds_only_the_dishes_that_have_a_recipe_to_open():
    got = _linkify("nothing")
    # Longest first, so a plan carrying both "Chicken Tacos" and "Chicken"
    # links the longer name rather than half of it.
    assert got["titles"] == ["Baked Oatmeal Cups", "Chicken Tacos", "Apple slices", "Chicken"]
    # The away night and the reheat night are not in it, and neither is the
    # slot with nothing in it.
    assert "Out" not in got["titles"] and "Bulgogi" not in got["titles"]


@_needs_node
def test_a_reply_links_the_dishes_it_names_and_leaves_the_rest_as_prose():
    got = _linkify(
        "I moved Chicken Tacos to Friday and left Baked Oatmeal Cups where it was. "
        "Bulgogi is still Thursday, and I didn&#39;t touch the Lentil Soup."
    )
    html = got["html"]
    dishes = re.findall(r'<button type="button" class="ask-dish" data-dish="(\d+)">([^<]*)</button>', html)
    assert [d[1] for d in dishes] == ["Chicken Tacos", "Baked Oatmeal Cups"]
    # Bulgogi is a reheat night — it has no recipe screen, so it is not a
    # link. Lentil Soup is not on the plan at all.
    assert "Bulgogi" in html and ">Bulgogi<" not in html
    assert "Lentil Soup" in html and ">Lentil Soup<" not in html
    # Every index the markup points at resolves to a real entry.
    assert [int(d[0]) for d in dishes] == [1, 0]


@_needs_node
def test_the_longer_dish_name_wins_and_a_word_inside_a_word_never_matches():
    got = _linkify("Chicken Tacos on Thursday, plain Chicken on Friday. Chickens are birds.")
    names = re.findall(r'class="ask-dish" data-dish="\d+">([^<]*)</button>', got["html"])
    assert names == ["Chicken Tacos", "Chicken"]
    # "Chickens" is a different word, and a substring match would have made
    # half of it a link.
    assert "Chickens are birds" in got["html"]


@_needs_node
def test_two_dish_names_in_a_row_both_link():
    """The boundary character is consumed by the match, so a naive
    implementation drops the second of an adjacent pair."""
    got = _linkify("Chicken Tacos, Apple slices.")
    names = re.findall(r'class="ask-dish" data-dish="\d+">([^<]*)</button>', got["html"])
    assert names == ["Chicken Tacos", "Apple slices"]


@_needs_node
def test_nothing_this_inserts_is_ever_rescanned_as_prose():
    """One pass over the whole reply, so a dish name cannot end up inside
    the markup of another one."""
    got = _linkify("Chicken Tacos and Chicken Tacos again.")
    assert got["html"].count("<button") == 2
    assert "data-dish=\"<button" not in got["html"]


@_needs_node
def test_an_empty_plan_links_nothing_and_breaks_nothing():
    got = _linkify("Nothing is planned yet.", {"days": []})
    assert got["html"] == "Nothing is planned yet."
    assert got["titles"] == []


def test_only_the_assistants_side_is_rewritten():
    fn = _extract("buildAskMessageEl")
    assert "role === 'assistant'" in fn and "linkifyDishNames(renderMarkdownLite(text))" in fn
    # ...and a tap closes the sheet and opens the recipe, the same shape an
    # action card's View already has.
    assert "closeAskSheet();" in fn and "openRecipeFor(target," in fn


def test_the_index_is_fed_from_data_the_app_already_fetches():
    """No request of its own in the ordinary case: renderWeekMenu has the
    payload, and the ask sheet's own quick-action fetch is /api/week-menu
    too."""
    assert "setDishIndex(data);" in _extract("renderWeekMenu")
    assert "if (weekMenu) setDishIndex(weekMenu);" in _extract("loadQuickActionChips")


def test_a_chat_change_to_a_week_nobody_has_opened_still_updates_the_index():
    """The "panels build once per page load" gotcha, one level down: a chat
    swap on a Meals tab that was never opened has no panel to reload, and
    the index would go on naming last week's dinners in every reply."""
    fn = _extract("refreshStaleTabsFromActions")
    assert "refreshDishIndex();" in fn
    assert "/api/week-menu" in _extract("refreshDishIndex")


# ---------------------------------------------------------------- the floor

def test_every_new_tap_target_clears_44px():
    """Hard rule 6. A dish name is set at whatever size its screen wants —
    15px on a dense row, inline in a sentence in chat — so the target is an
    invisible box, the same trick .wg2-why and .gro-icon-btn already use."""
    link = SHELL_CSS.split(".dish-link::after", 1)[1].split("}", 1)[0]
    assert "height: 44px" in link and "min-height: 100%" in link
    ask = SHELL_CSS.split(".ask-dish::after", 1)[1].split("}", 1)[0]
    assert "inset: -12px -4px" in ask, "an inline dish name needs its hit area grown"


def test_the_new_css_goes_through_tokens():
    """DESIGN_SYSTEM.md rule 9: a literal hex outside theme.css is a review
    failure."""
    for block in (".ask-dish {", ".ask-dish:hover"):
        rule = SHELL_CSS.split(block, 1)[1].split("}", 1)[0]
        assert "#" not in rule, f"{block} carries a literal colour"
    assert "var(--apricot-label)" in SHELL_CSS.split(".ask-dish {", 1)[1].split("}", 1)[0]


def test_the_dish_link_class_only_removes_button_furniture():
    """The name keeps its own type. A dish name that suddenly went apricot
    would be a second accent on a screen that already has its one."""
    rule = SHELL_CSS.split(".dish-link {", 1)[1].split("}", 1)[0]
    assert "color: inherit" in rule
    assert "font: inherit" in rule
    # Declared before the classes it rides with, since those set their own
    # family/size/weight and win an equal-specificity tie by being later.
    for later in (".rest-row-title {", ".tomorrow-title {", ".hero-dish {"):
        assert SHELL_CSS.index(".dish-link {") < SHELL_CSS.index(later), (
            f"{later} must come after .dish-link or it loses its own type"
        )
