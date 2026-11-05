"""
Bright Dusk, stage 3: Cook, Prep, Recipe, Cooking and Ask (Emily, 2026-11-05;
Z-CookDay, Z-Cook, Z-Prep, Z-Recipe, Z-Steps, Z-CookMode, Z-Ask).

The boards' own measurements are pinned here, with the rules a board can't
draw: a fold outlives a tick, a thaw has no ×, a running timer is an end time,
and dusk stays off the meals. Cook's root is pinned in test_cook_option_a.py,
the recipe's ··· in test_recipe_page.py.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from tests import nodeharness

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _function(name: str) -> str:
    start = SHELL_JS.index(f"function {name}(")
    i = SHELL_JS.index("{", start)
    depth, j = 0, i
    while True:
        if SHELL_JS[j] == "{":
            depth += 1
        elif SHELL_JS[j] == "}":
            depth -= 1
            if depth == 0:
                return SHELL_JS[start: j + 1]
        j += 1


def _block() -> str:
    return SHELL_CSS[SHELL_CSS.index("COOK, PREP, RECIPE, COOKING, ASK (Bright Dusk, stage 3)"):]


_PREP = (
    "function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;')"
    ".replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
    "var COOK_ICONS = { check: '<svg/>' };\n"
    "var RECIPE_ICONS = { chevLeft: '<svg/>' };\n"
    "var PREP_LIST_ICONS = { chevron: '<svg/>', close: '<svg/>' };\n"
    "var cookState = { data: null };\n"
    "var prepListFolded = {};\n"
    "function renderCook() {}\n"
    "function cookSessionOn(d, date) { return d.prep_sessions[0]; }\n"
    "function cookResolveFocusIndex() { return null; }\n"
    + "".join(_function(n) + "\n" for n in (
        "roundBtnHtml", "prepSectionFolded", "prepListItem", "cookPrepBudgetWords", "prepSectionTime",
        "cookSessionHtml", "cookPrepSectionHtml", "cookSessionItemHtml", "cookPrepFreshHtml",
        "cookTogglePrepSection"))
)


def _item(key, title, can_remove=True, done=False):
    return {"key": key, "title": title, "line": "Fri dinner", "kind": "chop", "can_remove": can_remove,
            "done": done, "on_list": True, "state": "on"}


def _session(**extra):
    s = {
        "date": "2026-11-05", "weekday": "Thursday", "weekly_plan_id": 3, "items_total": 4, "items_done": 1,
        "header": "Up to 2 hr · about 1 hr 25 hands-on", "over_budget_minutes": 0,
        "made_fresh_line": "These didn’t fit in your 2 hours, so they’re cooked fresh on the day. Tap Add it to prep one now.",
        "made_fresh": ["cook:9", "fresh:1"],
        "items": [_item("a", "Chicken thighs, fridge", can_remove=False), _item("b", "Onions", done=True),
                  _item("c", "Marinate the pork", can_remove=False), _item("d", "Roast the potatoes"),
                  dict(_item("cook:9", "Jalfrezi sauce"), state="didnt_fit", off_line="20 min · Cooked fresh on Fri"),
                  dict(_item("fresh:1", "Avocado"), state="better_fresh", off_line="Better fresh.")],
        "sections": [
            {"key": "thaw", "title": "Move to thaw", "line": "1 thing · 5 min hands-on", "step_keys": ["a"], "done": False},
            {"key": "chop", "title": "Chop and wash", "line": "1 thing · 25 min hands-on", "step_keys": ["b"], "done": True},
            {"key": "sauces", "title": "Sauces, marinades, drinks", "line": "1 thing · 20 min hands-on", "step_keys": ["c"], "done": False},
            {"key": "oven", "title": "In the oven", "line": "1 thing · 1 hr 5 hands-on", "step_keys": ["d"], "done": False},
        ],
    }
    s.update(extra)
    return s


def _prep(body: str, session=None):
    script = _PREP + f"var S = {json.dumps(session or _session())};\n" + body
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_prep_is_the_board_head_bar_and_two_columns_in_the_servers_order():
    html = _prep("console.log(JSON.stringify(cookSessionHtml({}, S, [])));")
    assert '<h1 class="screen-title">Prep</h1>' in html
    assert '<p class="screen-sub">Thursday · up to 2 hours</p>' in html
    assert 'class="cook-bar-fill" style="width:25%"' in html and 'aria-valuenow="1"' in html
    assert html.count('class="prep-col"') == 2
    titles = re.findall(r'prep-sec-title">([^<]+)<', html)
    assert titles == ["Move to thaw", "Chop and wash", "Sauces, marinades, drinks", "In the oven"]
    assert html.index("Chop and wash") < html.index('class="prep-col"', html.index('class="prep-col"') + 1) < html.index("Sauces")
    # The section's time, at the board's length; a done section says Done.
    assert re.findall(r'prep-sec-line">([^<]+)<', html) == ["5 min", "Done", "20 min", "1 hr 5"]


@_needs_node
def test_every_section_is_open_until_folded_and_a_fold_survives_a_redraw():
    got = _prep(
        "var open = cookSessionHtml({}, S, []);\n"
        "cookState.data = { prep_sessions: [S] };\n"
        "var el = { getAttribute: function (k) { return k === 'data-date' ? S.date : 'chop'; } };\n"
        "cookTogglePrepSection(el);\n"
        "S.items_done = 2;\n"
        "var folded = cookSessionHtml({}, S, []);\n"
        "cookTogglePrepSection(el);\n"
        "var again = cookSessionHtml({}, S, []);\n"
        "console.log(JSON.stringify([open, folded, again]));"
    )
    opened, folded, again = got
    assert opened.count('aria-expanded="true"') == 4 and "aria-expanded=\"false\"" not in opened
    assert folded.count('aria-expanded="true"') == 3 and folded.count('aria-expanded="false"') == 1
    assert ">Onions<" not in folded and ">Marinate the pork<" in folded
    assert again.count('aria-expanded="true"') == 4


@_needs_node
def test_a_thaw_and_a_marinade_have_no_x_and_everything_else_does():
    html = _prep("console.log(JSON.stringify(cookSessionHtml({}, S, [])));")
    assert html.count('data-cook="prep-off"') == 2
    assert 'Take off the list: Onions' in html and 'Take off the list: Roast the potatoes' in html
    assert "Take off the list: Chicken thighs" not in html and "Take off the list: Marinate" not in html


@_needs_node
def test_made_on_the_night_keeps_its_sentence_and_add_it():
    html = _prep("console.log(JSON.stringify(cookSessionHtml({}, S, [])));")
    assert '<h3 class="prep-fresh-title">Made on the night</h3>' in html
    assert "Tap Add it to prep one now." in html
    assert html.count('data-cook="prep-add"') == 1, "better fresh is never prepped"
    assert ">Add it</button>" in html


@_needs_node
def test_prep_says_when_it_is_over_the_budget_and_when_it_is_done():
    over = _session(over_budget_minutes=10, header="Up to 1 hr · about 1 hr 10 hands-on · 10 min over")
    assert "Thursday · up to 1 hour · 10 min over" in _prep("console.log(JSON.stringify(cookSessionHtml({}, S, [])));", over)
    done = _session(items_done=4)
    assert "That’s the prep done." in _prep("console.log(JSON.stringify(cookSessionHtml({}, S, [])));", done)


# ---------- timers ----------

_TIMERS = (
    "var COOK_TIMER_VERBS = " + SHELL_JS[SHELL_JS.index("var COOK_TIMER_VERBS = ") + len("var COOK_TIMER_VERBS = "):
                                         SHELL_JS.index("];", SHELL_JS.index("var COOK_TIMER_VERBS = ")) + 1] + ";\n"
    + _function("cookStepTimers") + "\n" + _function("cookTimerClock") + "\n"
)


def _timers(step):
    res = nodeharness.run_node(_TIMERS + f"console.log(JSON.stringify(cookStepTimers({json.dumps(step)})));\n", timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip())


@_needs_node
@pytest.mark.parametrize("step,want", [
    ("Add the salsa verde and let it simmer for 15 minutes.", [(900, "Simmer")]),
    ("Roast for 1 hour.", [(3600, "Roast")]),
    ("Bake 20 to 25 minutes, until golden.", [(1200, "Bake")]),
    ("Stir-fry them for 8 minutes. Rest for 2 minutes.", [(480, "Fry"), (120, "Rest")]),
    ("Marinate for 30 minutes or up to 2 hours.", [(1800, "Marinate")]),
    ("Leave it for 10 min.", [(600, "Timer")]),
    ("Chop the onion.", []),
    ("Use 2 cups of stock.", []),
])
def test_a_step_offers_the_times_it_says(step, want):
    assert [(t["seconds"], t["label"]) for t in _timers(step)] == want


@_needs_node
def test_the_clock_reads_the_way_the_board_does():
    res = nodeharness.run_node(_TIMERS + "console.log(JSON.stringify([cookTimerClock(900), cookTimerClock(5400), cookTimerClock(59.2), cookTimerClock(-3)]));\n", timeout=30)
    assert json.loads(res.stdout.strip()) == ["15:00", "1:30:00", "1:00", "0:00"]


def test_a_timer_is_dusk_and_a_running_one_is_an_end_time_in_the_tick_record():
    css = _block()
    rule = css[css.index(".cook-timer {"):]
    assert "background: var(--dusk)" in rule[:rule.index("}")]
    assert "Date.now() + parseInt(el.getAttribute('data-seconds'), 10) * 1000" in _function("cookToggleTimer")
    assert "timers: cookState.ticks.timers" in _function("cookWriteTicks")
    # No lookbehind: a regex Safari before 16.4 can't parse takes the shell down.
    assert "(?<" not in _function("cookStepTimers")


# ---------- Cook and the recipe, on the page ----------

def test_dusk_stays_off_cook_prep_and_the_recipe_but_for_timers():
    css = _block()
    ask_at = css.index("/* --- Ask (Z-Ask) --- */")
    body = css[:ask_at]
    uses = [m.start() for m in re.finditer(r"background: var\(--dusk\)", body)]
    assert len(uses) == 1 and uses[0] == body.index("background: var(--dusk)", body.index(".cook-timer {"))
    assert "var(--spruce)" not in body
    for name in ("cookPrepDayCardHtml", "cookBedCardHtml", "cookReheatFocusHtml", "cookSessionHtml"):
        assert "on-spruce" not in _function(name), name


def test_cook_no_longer_draws_an_empty_moment():
    assert "emptyMomentHtml" not in _function("renderKitchen")
    assert "note-card cook-error" in _function("renderKitchen")


def test_the_whole_screen_steps_have_no_tab_bar_and_every_way_out_redraws_it():
    assert "body.is-full-screen #tab-bar { display: none; }" in SHELL_CSS
    sync = _function("syncFullScreen")
    assert "key === 'kitchen' && cookState.screen !== 'overview'" in sync
    assert "syncFullScreen(key)" in _function("activateTab")
    assert "syncFullScreen()" in _function("renderCook")
    assert "syncFullScreen()" in _function("renderMealsStep")


def test_with_no_tab_bar_ask_is_still_a_tap_away():
    assert "name: 'Ask Pomona', attrs: ' data-cook=\"recipe-ask\"'" in _function("openCookerMoreSheet")
    assert "row('Ask Pomona', ' data-cook=\"recipe-ask\"')" in _function("recipeMoreRowsHtml")


def test_the_recipe_plate_is_216_and_the_no_source_line_rule_holds():
    assert "Plates.html(plateKey, 216)" in _function("recipeTopHtml")
    page = _function("cookRecipeHtml")
    assert "recipeCreditHtml" not in page and "recipeForLine" not in page
    assert "recipeCreditHtml(extra)" in _function("openRecipeSourcesSheet")


def test_recipe_tabs_are_the_shared_switcher_on_both_doors():
    assert "segHtml(" in _function("recipeTabsHtml")
    step = _function("mealStepHtml")
    assert "segHtml([" in step and 'data-wk-recipe-tab="steps"' in step
    assert "wireMealRecipeTabs(steps)" in _function("wireMealsStep")


# ---------- Ask ----------

def test_the_sheet_is_the_boards():
    assert '<div class="ask-sheet-title">Ask Pomona</div>' in SHELL_HTML
    assert '<button type="submit" id="ask-send-btn" class="ask-composer-send">Send</button>' in SHELL_HTML
    assert 'class="ask-composer-field"' in SHELL_HTML
    css = _block()
    send = css[css.index(".ask-composer-send {"):]
    send = send[:send.index("}")]
    assert "width: 76px" in send and "height: 52px" in send and "background: var(--apricot)" in send
    assert "var(--spruce)" not in send
    user = css[css.index(".ask-msg.user .ask-bubble {"):]
    assert "background: var(--dusk)" in user[:user.index("}")]
    assert "border-radius: 22px 22px 6px 22px" in user[:user.index("}")]
    ai = css[css.index(".ask-msg.assistant .ask-bubble, .ask-bubble.loading {"):]
    assert "background: var(--white)" in ai[:ai.index("}")]
    assert "height: 92%" in css[css.index("#ask-sheet {"):css.index("#ask-sheet {") + 60]


def test_voice_photo_and_send_all_stay():
    for hook in ('id="ask-input"', 'id="ask-mic-btn"', 'id="ask-photo-btn"', 'id="ask-send-btn"', 'id="ask-sheet-back"',
                 'id="ask-tips-btn"', 'id="ask-composer"', 'id="ask-messages"', 'id="ask-chips"', 'id="ask-save-dock"',
                 'id="ask-context"', 'id="ask-sheet-back-label"'):
        assert hook in SHELL_HTML, hook


_ASK = (
    "function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;')"
    ".replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
    "function dayName(d, o){ return new Date(d + 'T00:00:00').toLocaleDateString('en-US', o); }\n"
    "var dishIndex = { tiles: { 'pork tacos': { plate: 'pork-tacos', date: '2026-11-05', slot: 'dinner' },"
    " 'a': { plate: 'a', date: '2026-11-06', slot: 'lunch' }, 'b': { plate: 'b', date: '2026-11-06', slot: 'lunch' },"
    " 'c': { plate: 'c', date: '2026-11-06', slot: 'lunch' }, 'd': { plate: 'd', date: '2026-11-06', slot: 'lunch' } } };\n"
    "var Plates = { html: function (k, s) { return '<img data-k=\"' + k + '\" data-s=\"' + s + '\">'; } };\n"
    "function bubble(names) { return { querySelectorAll: function () { return names.map(function (n) {"
    " return { getAttribute: function () { return n; } }; }); } }; }\n"
    + _function("tileHtml") + "\n" + _function("cookIsSnack") + "\n" + _function("cookSlotLabel") + "\n"
    + _function("askDishTilesHtml") + "\n"
)


@_needs_node
def test_a_dish_a_reply_names_is_a_tile_under_it():
    res = nodeharness.run_node(
        _ASK + "console.log(JSON.stringify([askDishTilesHtml(bubble(['Pork Tacos', 'Pork Tacos'])),"
        " askDishTilesHtml(bubble(['Unknown'])), askDishTilesHtml(bubble(['a', 'b', 'c', 'd', 'Pork Tacos']))]));\n",
        timeout=30)
    assert res.returncode == 0, res.stderr
    one, none, many = json.loads(res.stdout.strip())
    assert one.count('class="tile"') == 1 and 'data-dish="Pork Tacos"' in one
    assert '<span class="tile-sub">Thursday, dinner</span>' in one and 'data-k="pork-tacos"' in one
    assert none == ""
    assert many.count('class="tile"') == 4, "a reply reading out the week is a list, not a shelf"
