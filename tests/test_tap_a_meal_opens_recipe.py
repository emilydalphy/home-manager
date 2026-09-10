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
def test_the_back_link_says_the_tab_until_something_else_says_otherwise():
    """
    Renamed and re-expected on merging, 2026-09-10: the fallback tab is
    called Cook now, not Kitchen (Emily's rename, landed the same week on
    another branch). The two origins below are literals this harness sets
    itself, so they are unaffected — only the no-origin fallback moved.
    """
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
    assert _run_node(harness) == ["Cook", "Today", "Monday"]


def test_leaving_the_recipe_lands_back_where_it_was_opened_from():
    fn = _extract("cookExitFocus")
    # Kitchen's own screen is reset first, whichever tab we end up on —
    # otherwise the tab reopens on a cook screen nobody left it on.
    assert fn.index("cookState.screen = 'overview';") < fn.index("origin.tab")
    # Neither hop adds a history entry (2026-09-10): going back up a level
    # moves the entry you are standing on. See
    # test_the_back_link_adds_no_history_entries below.
    assert "activateTab(origin.tab, false, { replaceHistory: true });" in fn
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
        + "var cookState = { focusOrigin: null };\n"
        + "var COOK_ICONS = { check: '<svg/>', mic: '<svg/>' };\n"
        # 2026-09-10: the ingredient line is one shared helper now
        # (cookIngredientLabel), rather than the same quantity-then-item
        # expression written out in the renderer and again in the serving
        # stepper's rewrite. cookState lost focusStepsChecked in the same
        # change — step ticks live in the tick store now (cookReadTicks),
        # which the plain frame deliberately never touches, so the stub
        # here no longer needs to carry one.
        # The CHECKABLE copy really does read the tick store now, so the
        # harness has to answer it. Nothing is ticked here on purpose: this
        # test is about which controls each frame renders, and the plain
        # frame's whole point is that it renders none of them.
        + "function cookTicked(){ return false; }\n"
        + _extract("cookMealKey") + "\n"
        + _extract("cookIngredientLabel") + "\n"
        # 2026-09-10, review round: the "eyeball these" note is rendered off
        # the meal now rather than poked into a hidden <p> after a rescale,
        # so both frames call one helper for it. The plain frame carries no
        # stepper, so it only ever renders the empty string — which is the
        # point of asserting below that it gains no control.
        + _extract("cookUnscaledHtml") + "\n"
        # ...and the serving count is read through one helper now, because
        # the cook's own count lives beside the meal's while a rescale is in
        # flight. The plain frame renders no stepper at all, which is the
        # point of the assertions below.
        + _extract("cookServesShown") + "\n"
        + _extract("cookBackLabel") + "\n"
        + _extract("cookFocusEndHtml") + "\n"
        + _extract("cookStepLi") + "\n"
        + _extract("cookInstructionsHtml") + "\n"
        + _extract("cookDetailHtml") + "\n"
        + _extract("isSnackSlot") + "\n"
        + _extract("mealRecipeCardHtml") + "\n"
        + "console.log(JSON.stringify({\n"
        + f"  plain: cookDetailHtml({json.dumps(meal)}, 'meal', false, true),\n"
        + f"  cooking: cookDetailHtml({json.dumps(meal)}, 3, false, false),\n"
        + f"  card: mealRecipeCardHtml({json.dumps(meal)}, 'dinner'),\n"
        + "  reheat: mealRecipeCardHtml({ meal: 'Bulgogi', is_leftovers: true }, 'dinner'),\n"
        + "  none: mealRecipeCardHtml(null, 'dinner')\n"
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
        + _extract("isSnackSlot") + "\n"
        + _extract("mealRecipeCardHtml") + "\n"
        + "console.log(JSON.stringify({\n"
        + "  dinner: mealRecipeCardHtml({ meal: 'Takeaway', has_full_recipe: false }, 'dinner'),\n"
        + "  snack: mealRecipeCardHtml({ meal: 'Apple slices', has_full_recipe: false }, 'snack'),\n"
        + "  snack2: mealRecipeCardHtml({ meal: 'Apple slices', has_full_recipe: false }, 'snack2'),\n"
        + "  realsnack: mealRecipeCardHtml("
        + "{ meal: 'Energy Balls', has_full_recipe: true, ingredients: [], instructions: [] }, 'snack')\n"
        + "}));\n"
    )
    got = _run_node(harness)
    # A meal with nothing written up says so, and names the way to fill it in.
    assert "no saved recipe detail" in got["dinner"]
    # A grab-and-go snack does not: "Apple slices" is not a recipe somebody
    # forgot to write, so a card whose whole content is "there isn't one" is
    # an empty card — the same reason the plate card is already hidden here.
    assert got["snack"] == "" and got["snack2"] == ""
    # ...and a snack that IS a recipe keeps it.
    assert "The recipe" in got["realsnack"]


def test_the_meal_step_renders_the_recipe_card():
    assert "mealRecipeCardHtml(cookMeal, slot)" in _extract("mealStepHtml")
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


# A stand-in for the handful of DOM calls linkifyDishNamesIn makes, so the
# walk itself runs for real (node has no DOM). Elements carry their own
# children, so a test can build the exact tree renderMarkdownLite produces —
# a table, a <strong> — and see what the walk does to it.
_DOM_STUB = """
function TextNode(v) { this.nodeType = 3; this.nodeValue = v; }
function El(tag, type) { this.nodeType = type || 1; this.tagName = tag; this.childNodes = []; this.attrs = {}; }
El.prototype.appendChild = function (n) {
  var kids = n.nodeType === 11 ? n.childNodes.slice() : [n];
  var self = this;
  kids.forEach(function (k) { self.childNodes.push(k); });
  return n;
};
El.prototype.setAttribute = function (k, v) { this.attrs[k] = v; };
El.prototype.replaceChild = function (nw, old) {
  var i = this.childNodes.indexOf(old);
  var kids = nw.nodeType === 11 ? nw.childNodes.slice() : [nw];
  this.childNodes.splice.apply(this.childNodes, [i, 1].concat(kids));
};
Object.defineProperty(El.prototype, 'textContent', {
  set: function (v) { this.childNodes = [new TextNode(v)]; }
});
var document = {
  createTextNode: function (v) { return new TextNode(v); },
  createElement: function (t) { return new El(t.toUpperCase()); },
  createDocumentFragment: function () { return new El('#fragment', 11); }
};
function el(tag, kids) {
  var e = new El(tag.toUpperCase());
  (kids || []).forEach(function (k) { e.appendChild(typeof k === 'string' ? new TextNode(k) : k); });
  return e;
}
function ser(node) {
  if (node.nodeType === 3) return node.nodeValue;
  var inner = node.childNodes.map(ser).join('');
  if (node.nodeType === 11) return inner;
  var a = '';
  if (node.type) a += ' type="' + node.type + '"';
  if (node.className) a += ' class="' + node.className + '"';
  Object.keys(node.attrs).forEach(function (k) { a += ' ' + k + '="' + node.attrs[k] + '"'; });
  var t = node.tagName.toLowerCase();
  return '<' + t + a + '>' + inner + '</' + t + '>';
}
"""


def _dish_harness(menu: dict = None, extra: str = "") -> str:
    return (
        _ESCAPE
        + _DOM_STUB
        + "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
        + _extract("isSnackSlot") + "\n"
        + _extract("snackSlotKey") + "\n"
        + _extract("daySlotEntry") + "\n"
        + _extract("recipeTargetForEntry") + "\n"
        + _extract("escapeRegExp") + "\n"
        + "var dishIndex = { entries: [], byName: {}, re: null };\n"
        + "var DISH_SKIP_TAGS = { BUTTON: 1, A: 1, CODE: 1, PRE: 1 };\n"
        + _extract("setDishIndex") + "\n"
        + _extract("dishTargetForName") + "\n"
        + _extract("dishSegments") + "\n"
        + _extract("linkifyDishNamesIn") + "\n"
        + f"setDishIndex({json.dumps(menu if menu is not None else _WEEK_MENU)});\n"
        + extra
    )


def _linkify(reply_text: str, menu: dict = None):
    """Runs the real walk over a one-paragraph reply and serializes it back."""
    return _run_node(
        _dish_harness(menu)
        + f"var root = el('p', [{json.dumps(reply_text)}]);\n"
        + "linkifyDishNamesIn(root);\n"
        + "console.log(JSON.stringify({ html: ser(root), "
        + "titles: dishIndex.entries.map(function (e) { return e.title; }) }));\n"
    )


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
        "Bulgogi is still Thursday, and I didn't touch the Lentil Soup."
    )
    html = got["html"]
    dishes = re.findall(r'<button type="button" class="ask-dish" data-dish="([^"]*)">([^<]*)</button>', html)
    assert [d[1] for d in dishes] == ["Chicken Tacos", "Baked Oatmeal Cups"]
    # The link carries the dish's NAME, never a position in an index that is
    # rebuilt on every plan read — see the blocker fixed 2026-09-10.
    assert [d[0] for d in dishes] == ["Chicken Tacos", "Baked Oatmeal Cups"]
    # Bulgogi is a reheat night — it has no recipe screen, so it is not a
    # link. Lentil Soup is not on the plan at all.
    assert "Bulgogi" in html and ">Bulgogi<" not in html
    assert "Lentil Soup" in html and ">Lentil Soup<" not in html


@_needs_node
def test_the_longer_dish_name_wins_and_a_word_inside_a_word_never_matches():
    got = _linkify("Chicken Tacos on Thursday, plain Chicken on Friday. Chickens are birds.")
    names = re.findall(r'class="ask-dish" data-dish="[^"]*">([^<]*)</button>', got["html"])
    assert names == ["Chicken Tacos", "Chicken"]
    # "Chickens" is a different word, and a substring match would have made
    # half of it a link.
    assert "Chickens are birds" in got["html"]


@_needs_node
def test_two_dish_names_in_a_row_both_link():
    """The boundary character is consumed by the match, so a naive
    implementation drops the second of an adjacent pair."""
    got = _linkify("Chicken Tacos, Apple slices.")
    names = re.findall(r'class="ask-dish" data-dish="[^"]*">([^<]*)</button>', got["html"])
    assert names == ["Chicken Tacos", "Apple slices"]


@_needs_node
def test_nothing_this_inserts_is_ever_rescanned_as_prose():
    """The walk replaces a text node with finished nodes, so a dish name can
    never end up inside the markup of another one."""
    got = _linkify("Chicken Tacos and Chicken Tacos again.")
    assert got["html"].count("<button") == 2
    assert "data-dish=\"<button" not in got["html"]


@_needs_node
def test_an_empty_plan_links_nothing_and_breaks_nothing():
    got = _linkify("Nothing is planned yet.", {"days": []})
    assert got["html"] == "<p>Nothing is planned yet.</p>"
    assert got["titles"] == []


def test_only_the_assistants_side_is_rewritten():
    fn = _extract("buildAskMessageEl")
    assert "bubble.innerHTML = renderMarkdownLite(text);" in fn
    assert "if (role === 'assistant') linkifyDishNamesIn(bubble);" in fn
    # ...and a tap closes the sheet and opens the recipe, the same shape an
    # action card's View already has.
    assert "openDishFromChat(btn.getAttribute('data-dish'));" in fn
    door = _extract("openDishFromChat")
    assert "closeAskSheet();" in door and "openRecipeFor(target," in door


# ------------------------------------- what review found, and what fixed it
# Every test below fails on the commit before 2026-09-10's review pass.

@_needs_node
def test_a_link_opens_the_dish_it_names_even_after_the_index_is_rebuilt():
    """THE BLOCKER. The index is rebuilt and re-sorted on every
    /api/week-menu read — including the one sendAskMessage fires on the line
    straight after it renders the bubble. The first version wrote the dish's
    POSITION in that array into the markup and read it back at click time, so
    a bubble labelled "Chicken Tacos" opened "Apple slices": a different meal,
    on a screen whose apricot primary is "Mark it cooked"."""
    later_week = {
        "days": [
            {
                "date": "2026-09-17",
                # Same four names, deliberately in an order that re-sorts:
                # under the old positional scheme index 2 was "Apple slices"
                # before and "Baked Oatmeal Cups" after.
                "breakfast": {"state": "planned", "entry_id": 31, "title": "Apple slices", "source": "plan"},
                "lunch": {"state": "planned", "entry_id": 32, "title": "Chicken", "source": "plan"},
                "dinner": {"state": "planned", "entry_id": 33, "title": "Chicken Tacos", "source": "plan"},
                "snacks": [{"state": "planned", "entry_id": 34, "title": "Baked Oatmeal Cups", "source": "plan"}],
            }
        ]
    }
    got = _run_node(
        _dish_harness()
        + "var root = el('p', ['I swapped Chicken Tacos onto Friday.']);\n"
        + "linkifyDishNamesIn(root);\n"
        + "var btn = root.childNodes[1];\n"
        # The week changes underneath the reply that describes the change.
        + f"setDishIndex({json.dumps(later_week)});\n"
        + "console.log(JSON.stringify({\n"
        + "  label: btn.childNodes[0].nodeValue,\n"
        + "  opens: dishTargetForName(btn.attrs['data-dish'])\n"
        + "}));\n"
    )
    assert got["label"] == "Chicken Tacos"
    # The label and the target still name the same dish, and the target is
    # the CURRENT plan's entry for it — not the one recorded when the bubble
    # was drawn, and certainly not somebody else's.
    assert got["opens"] == {"title": "Chicken Tacos"}


@_needs_node
def test_a_dish_that_has_left_the_plan_opens_nothing_at_all():
    """A stale link fails closed. Opening the wrong meal is the failure this
    whole mechanism exists to avoid."""
    got = _run_node(
        _dish_harness()
        + "setDishIndex({ days: [] });\n"
        + "console.log(JSON.stringify({ gone: dishTargetForName('Chicken Tacos'), "
        + "blank: dishTargetForName('') }));\n"
    )
    assert got["gone"] is None and got["blank"] is None
    # ...and the door says so rather than leaving a dead tap.
    door = _extract("openDishFromChat")
    assert "dishTargetForName(name)" in door
    assert "not on the plan any more" in door


@_needs_node
def test_a_dish_on_two_nights_is_not_linked_at_all():
    """CONCERN 3. The index used to keep the first occurrence, so a reply
    saying "Chicken Tacos is on Saturday" opened Thursday — and cook mode's
    check-off writes against that entry_id, so "Mark it cooked" would have
    ticked the wrong night. Which night a sentence means is not something
    this can read out of generated prose, so the honest answer is to leave
    the name as prose."""
    twice = {
        "days": [
            {
                "date": "2026-09-10",
                "breakfast": None, "lunch": None,
                "dinner": {"state": "planned", "entry_id": 3, "title": "Chicken Tacos", "source": "plan"},
                "snacks": [{"state": "planned", "entry_id": 4, "title": "Apple slices", "source": "plan"}],
            },
            {
                "date": "2026-09-12",
                "breakfast": None, "lunch": None,
                "dinner": {"state": "planned", "entry_id": 8, "title": "Chicken Tacos", "source": "plan"},
                "snacks": [],
            },
        ]
    }
    got = _linkify("Chicken Tacos is on Saturday, and Apple slices are in the bowl.", twice)
    assert got["titles"] == ["Apple slices"], "a two-night dish is still in the link set"
    assert "Chicken Tacos" in got["html"] and ">Chicken Tacos<" not in got["html"]
    assert ">Apple slices<" in got["html"]


@_needs_node
def test_the_reply_is_walked_as_text_never_rewritten_as_markup():
    """CONCERN 6. The first version ran the regex over renderMarkdownLite's
    HTML string, so a household with a dish called "table" or "strong" got
    the reply's markdown table flattened into literal escaped tags. Nothing
    could be injected — the escaping was correct — but the fix is the same
    one the blocker needed: work on the real thing, not on a string that
    looks like it."""
    odd_names = {
        "days": [
            {
                "date": "2026-09-10",
                "breakfast": {"state": "planned", "entry_id": 1, "title": "table", "source": "plan"},
                "lunch": {"state": "planned", "entry_id": 2, "title": "strong", "source": "plan"},
                "dinner": {"state": "planned", "entry_id": 3, "title": "Chicken Tacos", "source": "plan"},
                "snacks": [],
            }
        ]
    }
    got = _run_node(
        _dish_harness(odd_names)
        # The shape renderMarkdownLite really produces for a reply carrying a
        # table and a bold word.
        + "var root = el('div', [\n"
        + "  el('strong', ['Chicken Tacos']),\n"
        + "  ' is the strong one. ',\n"
        + "  el('table', [el('thead', [el('tr', [el('th', ['Day'])])]),\n"
        + "               el('tbody', [el('tr', [el('td', ['Chicken Tacos'])])])]),\n"
        + "  el('button', ['Chicken Tacos'])\n"
        + "]);\n"
        + "linkifyDishNamesIn(root);\n"
        + "console.log(JSON.stringify({ html: ser(root) }));\n"
    )
    html = got["html"]
    # The table is still a table, and every tag is still a tag.
    assert "<table><thead><tr><th>" in html and "&lt;" not in html
    # The dish inside the bold word and the dish inside the cell both link,
    # in place, without disturbing the elements around them.
    assert "<strong><button type=\"button\" class=\"ask-dish\" data-dish=\"Chicken Tacos\">Chicken Tacos</button></strong>" in html
    assert "<td><button type=\"button\" class=\"ask-dish\" data-dish=\"Chicken Tacos\">Chicken Tacos</button></td>" in html
    # "strong" is a dish on this household's plan and it links as a word in
    # prose — but nothing went near the element of the same name.
    assert "is the <button type=\"button\" class=\"ask-dish\" data-dish=\"strong\">strong</button> one." in html
    # A dish name already inside a button is not linked a second time.
    assert html.endswith("<button>Chicken Tacos</button></div>")


@_needs_node
def test_every_way_into_cook_mode_says_where_it_came_from():
    """CONCERN 2. Two entry points called activateTab straight instead of
    going through openRecipeFor, so they inherited whatever origin an earlier
    deep link had left on cookState: Meals -> Thursday -> Cook this -> leave
    by the tab bar -> tap a cook row on Today, and back said "‹ Thursday" and
    dropped you on Meals. With no stale state at all it still made one screen
    disagree with itself — Today's Next up DISH NAME said "‹ Today" while the
    row's own button, 200px below, said "‹ Kitchen"."""
    focus = {"entryId": 42, "date": "2026-09-07", "slot": "dinner", "title": "Bulgogi"}
    calls = _run_node(
        "var calls = [];\n"
        "function activateTab(k, r, o){ calls.push(['activateTab', k, o || null]); }\n"
        "function openRecipeFor(t, origin){ calls.push(['openRecipeFor', t, origin || null]); }\n"
        "function toggleTodayMove(p, id, next){ calls.push(['tick', id, next]); }\n"
        + _extract("runTodayMoveAction") + "\n"
        + "var panel = { _moves: { moves: [ { id: 'cook:42', done: false, action: { target: "
        + json.dumps({"tab": "kitchen", "cookFocus": focus}) + " } } ] } };\n"
        + "runTodayMoveAction(panel, 'cook:42');\n"
        + "console.log(JSON.stringify(calls));\n"
    )
    assert calls == [["openRecipeFor", focus, {"label": "Today", "tab": "today"}]]
    # ...and Today's dish name, the tap right beside it, says the same thing.
    wiring = SHELL_JS.split("data-move-dish]", 1)[1][:600]
    assert "{ label: 'Today', tab: 'today' }" in wiring
    # Grocery's shop-done handoff is the other one, and it names Grocery.
    assert (
        "openRecipeFor(tonightDinnerRecipeTarget(), { label: 'Grocery', tab: 'grocery' });"
        in SHELL_JS
    )


@_needs_node
def test_the_back_link_adds_no_history_entries():
    """CONCERN 4. cookExitFocus pushed in activateTab and pushed again in
    goMealsStep, so one press of "‹ Thursday" grew history by two and the
    next back gesture skipped the Day step onto a state never visited.
    Stepping back UP a level moves the entry you are standing on."""
    calls = _run_node(
        "var calls = [];\n"
        "var cookState = { screen: 'focus', focusOrigin: "
        "{ label: 'Thursday', tab: 'week', mealsStep: 'meal', mealsDay: 3, mealsSlot: 'dinner' } };\n"
        "function stopCookVoice(){}\n"
        "function renderCook(){}\n"
        "function openAskSheet(){ calls.push(['openAskSheet']); }\n"
        "function activateTab(k, push, opts){ calls.push(['activateTab', k, push, opts || null]); }\n"
        "function goMealsStep(step, opts){ calls.push(['goMealsStep', step, opts]); }\n"
        + _extract("cookExitFocus") + "\n"
        + "cookExitFocus();\n"
        + "console.log(JSON.stringify(calls));\n"
    )
    assert calls == [
        # false, plus replaceHistory: the entry that said "kitchen" now says
        # where you actually are, rather than sitting behind a new one.
        ["activateTab", "week", False, {"replaceHistory": True}],
        ["goMealsStep", "meal", {"dayIndex": 3, "slot": "dinner", "replace": True}],
    ]
    # Both halves of that really exist: activateTab honours replaceHistory...
    fn = _extract("activateTab")
    assert "if (opts && opts.replaceHistory) {" in fn
    assert "window.history.replaceState({ tab: key }, '', tab.path);" in fn
    # ...and so does goMealsStep.
    assert "if (opts.replace) replaceMealsStepHistory();" in _extract("goMealsStep")
    assert "window.history.replaceState(mealsStepHistoryState(), '', '/week');" in _extract(
        "replaceMealsStepHistory"
    )


def test_coming_back_to_kitchen_by_the_tab_bar_redraws_the_cook_screen():
    """CONCERN 5. Clearing focusOrigin without redrawing left a still-mounted
    cook screen reading "‹ Today" while it now landed on Kitchen — the state
    was right and the button lied about it."""
    branch = SHELL_JS.split("else if (tab.kitchen && cookState && cookState.focusOrigin)", 1)[1]
    branch = branch[: branch.index("\n    }")]
    assert "cookState.focusOrigin = null;" in branch
    assert "renderCook();" in branch, "the mounted cook screen is never redrawn"
    assert "cookState.screen !== 'overview'" in branch, (
        "the Kitchen root has no back link to redraw — don't repaint it for nothing"
    )


def test_grocery_names_exactly_one_dish_and_it_is_a_link_like_every_other():
    """CONCERN 7. The decision log claimed "Grocery names no dishes at all".
    It names one, in the shop-done handoff, and Emily's rule has no exception
    for it. Same door as the button beside it — one data-gro, one handler."""
    fn = _extract("groShopDoneHtml")
    assert 'class="dish-link is-inline" data-gro="shop-done-tonight"' in fn
    assert "escapeHtml(dish)" in fn, "the dish name is still escaped"


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
    assert "/api/week-menu" in _extract("readDishIndex")
    assert "readDishIndex()" in _extract("refreshDishIndex")


# ------------- the hole under the blocker: a real swap, real payloads
# `swap_meal_in_plan` DELETES the old plan entry and creates a new one, so
# the entry id a reply's link was built from is ALWAYS a miss afterwards —
# and `cookResolveFocusIndex`'s next fallback is date + slot with no name
# check, which lands on whatever dish now occupies that night. Found by a
# second review, in a real browser, through the app's own write path. The
# whole point of this test is that nothing here is hand-edited: it plans a
# week, builds the index from the REAL /api/week-menu shape, runs the REAL
# swap, and then resolves the link against the REAL cooker view.

def _seed_week_for_swap():
    """A one-week plan with a different dinner on Monday and Tuesday."""
    from app import tools

    monday = "2026-09-07"
    plan = tools.create_weekly_plan(week_start_date=monday)
    plan_id = plan["weekly_plan_id"] if isinstance(plan, dict) else plan
    for name in ("Chicken Tacos", "Bean Chili"):
        tools.add_recipe(name, ingredients=[{"item": "something", "qty": "1"}],
                         instructions=["Cook it."])
    tools.plan_meal(monday, "Chicken Tacos", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal("2026-09-08", "Lentil Soup", slot="dinner", weekly_plan_id=plan_id)
    return plan_id, monday


@_needs_node
def test_a_real_swap_cannot_make_a_chat_link_open_the_new_dish():
    from app import tools

    plan_id, monday = _seed_week_for_swap()

    # 1. The reply is written, and its link is built from the plan as it
    #    stands: Chicken Tacos, Monday, dinner.
    before = tools.get_week_menu()
    entry_ids_before = sorted(
        e["entry_id"] for d in before["days"] for e in [d.get("dinner")] if e and e.get("entry_id")
    )

    # 2. A real swap of that exact night, through the app's own write path.
    tools.swap_meal_in_plan(plan_id, monday, "Bean Chili", slot="dinner")

    after = tools.get_week_menu()
    cooker = tools.get_cooker_view()
    entry_ids_after = sorted(
        e["entry_id"] for d in after["days"] for e in [d.get("dinner")] if e and e.get("entry_id")
    )
    # The premise: the id really is gone, so an id-based match really does
    # miss. If this ever stops being true the test above it is toothless.
    assert entry_ids_before != entry_ids_after, "swap_meal_in_plan no longer recreates the entry"
    monday_dish = [d for d in after["days"] if d["date"] == monday][0]["dinner"]["title"]
    assert monday_dish == "Bean Chili"

    # 3. What the household taps: the link in the OLD reply, still saying
    #    Chicken Tacos, resolved and focused by the real front-end pair.
    got = _run_node(
        _dish_harness(before)
        + "var meals = " + json.dumps(cooker.get("meals") or []) + ";\n"
        + "var cookState = { tonightIdx: 0 };\n"
        + _extract("cookResolveFocusIndex") + "\n"
        + "var target = dishTargetForName('Chicken Tacos');\n"
        + "var idx = target ? cookResolveFocusIndex(meals, target) : null;\n"
        + "console.log(JSON.stringify({ target: target, "
        + "opens: (idx === null || idx === undefined) ? null : (meals[idx] || {}).meal }));\n"
    )
    # The target carries the NAME and nothing else — no entry id and no
    # night, so neither of the resolver's earlier fallbacks can fire.
    assert got["target"] == {"title": "Chicken Tacos"}
    # ...and so it opens Chicken Tacos or it opens nothing. It must never
    # open the dish that took that night: the only entry-bearing control on
    # the screen it lands on is "Mark it cooked".
    assert got["opens"] != "Bean Chili", (
        "a chat link opened the dish that replaced the one it named"
    )
    assert got["opens"] in (None, "Chicken Tacos")


@_needs_node
def test_a_stale_index_after_a_real_swap_still_cannot_open_another_dish():
    """The second reproduction: the index itself went stale because a
    /api/week-menu read was dropped (refreshDishIndex is silent on failure
    by design). The link is then built from a plan that no longer exists —
    and must still never open the dish that took that slot."""
    from app import tools

    plan_id, monday = _seed_week_for_swap()
    stale = tools.get_week_menu()          # the index the old reply was linkified with
    tools.swap_meal_in_plan(plan_id, monday, "Bean Chili", slot="dinner")
    cooker = tools.get_cooker_view()       # ...but the cook screen is current

    got = _run_node(
        _dish_harness(stale)
        + "var meals = " + json.dumps(cooker.get("meals") or []) + ";\n"
        + "var cookState = { tonightIdx: 0 };\n"
        + _extract("cookResolveFocusIndex") + "\n"
        + "var idx = cookResolveFocusIndex(meals, dishTargetForName('Chicken Tacos'));\n"
        + "console.log(JSON.stringify({ opens: (idx === null || idx === undefined) "
        + "? null : (meals[idx] || {}).meal }));\n"
    )
    assert got["opens"] != "Bean Chili"
    assert got["opens"] in (None, "Chicken Tacos")
    # ...and the tap re-reads the plan first, so this case says "that's not
    # on the plan any more" rather than opening a dish nobody is cooking.
    assert "readDishIndex().then(" in _extract("openDishFromChat")


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
