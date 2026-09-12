"""
Kitchen is the cook's tab, and "what Pomona knows about us" is a sheet.

Emily's approved design, 2026-09-08 (branch
`flows-4-kitchen-and-preferences`). Two moves, and this file guards both:

1. **Kitchen answers "what's cooking, and what's in the house?"** Its root
   is Cooking today / Prep sessions / The rest of the week, plus two quiet
   tiles (Inventory, Recipes). Cook mode — the focused single-meal screen,
   its steps, its cook-ahead picker and its "Mark it cooked" — is a STEP of
   this tab now rather than a state of the Meals tab, so Meals lost its
   Plan | Cook segmented control and every entry point into cooking aims at
   Kitchen with a `cookFocus` target.
2. **Preferences is a sheet behind a gear** in the header of every root
   screen. The "What we know" hero and the "Something not working?" tile
   left Kitchen for it.

These are SOURCE assertions, the same kind and for the same reason as
tests/test_frontend_restored_2026_09_08.py: shell.js has no JS test harness
in this repo, and a marker that is present but mis-wired is still a far
better failure mode than a marker that is gone. Where a string is
user-facing copy it is asserted verbatim — if the copy is deliberately
reworded, update the constant here in the same commit and say so; do not
delete the test.

The one behavioural half is at the bottom: the moves payload really does
name the Kitchen tab, which is what makes Today's "Cook this" land in cook
mode rather than on a tab that no longer has a Cook state.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from app.tools import moves as moves_mod

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _assert_in(needle: str, haystack: str, what: str, where: str) -> None:
    assert needle in haystack, (
        f"{what} is missing from static/{where}.\nExpected to find: {needle!r}"
    )


def _function(name: str, source: str = SHELL_JS) -> str:
    """One brace-balanced `function name(...) {...}`, so an assertion can be
    scoped to the renderer it is about rather than to the whole file."""
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


# --- 1. the Kitchen root --------------------------------------------------

def test_the_kitchen_root_is_the_cooks_tab():
    """Three sections, in this order, and the day above them."""
    root = _function("renderKitchen")
    for call in ("kitchenCookingTodayHtml(rows, meals, todayIso)", "cookPrepSessionsHtml(data)",
                 "cookRestOfWeekHtml(", "kitchenTilesHtml()"):
        assert call in root, f"the Kitchen root no longer renders {call}"
    _assert_in("Cooking today", SHELL_JS, "the Cooking today eyebrow", "shell.js")
    _assert_in("Prep sessions", SHELL_JS, "the Prep sessions eyebrow", "shell.js")
    _assert_in("The rest of the week", SHELL_JS, "the rest-of-week eyebrow", "shell.js")


def test_the_kitchen_root_says_the_day_and_the_count():
    """"Monday · 1 cook tonight" — and "today" rather than "tonight" the
    moment a cook left today is not a dinner."""
    fn = _function("kitchenSubtitle")
    assert "' tonight'" in fn and "' today'" in fn
    assert "slot === 'dinner'" in fn, "the tonight/today test no longer reads the slot"
    _assert_in("nothing to cook today", SHELL_JS, "the empty-day subtitle", "shell.js")


def test_a_cooking_today_line_carries_the_start_by_and_the_badge():
    line = _function("kitchenTodayLine")
    assert "'start by '" in line, "the start-by chip is no longer read off the move"
    rows = _function("kitchenTodayRows")
    assert "'cooked'" in rows and "'Reheat'" in rows and "'Cook'" in rows, (
        "the Cook / cooked / Reheat badge is gone"
    )


def test_the_rest_of_the_week_collapses_after_three():
    fn = _function("cookRestOfWeekHtml")
    _assert_in("var KITCHEN_REST_VISIBLE = 3;", SHELL_JS, "the collapse point", "shell.js")
    # One line per day since 2026-09-11 (Build 8): the expand names the
    # days it hides ("Show Fri–Sun"), and three DAYS show before it.
    assert "'Show ' + dayNameShort(first.date)" in fn, "the 'Show Fri–Sun' link is gone"
    assert "groups.slice(0, KITCHEN_REST_VISIBLE)" in fn
    _assert_in('data-cook="rest-more"', SHELL_JS, "the expand control", "shell.js")


def test_the_two_quiet_tiles_are_inventory_and_recipes():
    """Rows in one card since 2026-09-11 (Build 8), above the fold, one fact
    each — still quiet, still no apricot."""
    tiles = _function("kitchenTilesHtml")
    # Inventory's title is followed by the "In development" pill (Loop
    # Board: mark inventory as still being built) rather than closing
    # straight away — see tests/test_inventory_in_development_marker.py
    # for that pill's own coverage.
    assert 'kit-row-title">Inventory' in tiles and ">Recipes<" in tiles
    assert 'class="kit-row"' in tiles, "Kitchen's entry points are quiet rows"
    assert "btn-primary" not in tiles and "apricot" not in tiles, (
        "Kitchen's root has no primary action and no apricot (DESIGN_SYSTEM Rule 5)"
    )


def test_what_we_know_and_the_snw_tile_left_the_kitchen_tab():
    """Both moved into Preferences. The Kitchen renderer must not render
    either one, or the tab is the settings drawer again."""
    root = _function("renderKitchen")
    assert "snwTile()" not in root, "the Something-not-working tile is back on Kitchen"
    assert "What we know" not in root, "the What-we-know hero is back on Kitchen"
    assert "kit-hero" not in root and "kitChip" not in SHELL_JS, (
        "the household hero and its count chips are back"
    )


def test_the_kitchen_root_lost_its_two_italic_re_ask_links():
    """Emily, 2026-09-11 (decision E, DESIGN_SYSTEM §2b S4): two italic
    headings with nothing under them left the root. The questions are asked
    on the All set screen and answered by the chat any time."""
    root = _function("renderKitchen")
    assert "cookDefrostLinkHtml()" not in root and "cookAheadAskLinkHtml()" not in root


# --- 2. cook mode is a step of Kitchen ------------------------------------

def test_cook_mode_lives_in_the_kitchen_panel():
    _assert_in("function cookPanel() { return panels['kitchen']; }", SHELL_JS,
               "cook mode's panel", "shell.js")
    _assert_in('id="kit-cook-view"', SHELL_JS, "the cook step's container", "shell.js")
    assert "week-cook-view" not in SHELL_JS, (
        "the Meals tab still has a cook view — cooking is Kitchen's step now"
    )
    assert "week-cook-view" not in SHELL_CSS


def test_the_back_link_says_where_it_came_from():
    """Back links go UP a level by name and never call history.back().

    Updated 2026-09-09 (branch `overnight/tap-a-meal-opens-recipe`): the
    focused cook screen's link used to be the literal "&lsaquo; Kitchen",
    and this test asserted that string. It is now cookBackLabel(), which
    says the origin's own name for a dish name tapped somewhere else, and
    falls back to the tab's name otherwise. The rule the test is about — up
    one level, BY NAME, never history.back() — is unchanged; only the source
    of the name is.

    Updated again on merging, 2026-09-10: the tab that name falls back to is
    called Cook now, not Kitchen (Emily's rename, same week). Two branches
    met on these three buttons — one renaming them, one making them dynamic
    — and the dynamic one won, so the rename lives in cookBackLabel's
    default. The prep session's link stayed literal, so it carries the new
    name directly.
    """
    _assert_in('data-cook="exit-focus">&lsaquo; \' +\n          escapeHtml(cookBackLabel())',
               SHELL_JS, "the focused screen's back link", "shell.js")
    _assert_in('data-cook="exit-session">&lsaquo; Cook</button>', SHELL_JS,
               "the prep session's back link", "shell.js")
    assert "history.back()" not in _function("cookExitFocus")
    fn = _function("cookBackLabel")
    assert "'Cook'" in fn, "the tab's name is still the answer when nothing set an origin"
    assert "Back to the week" not in SHELL_JS, "a cook screen still points back at Plan"


def test_cook_mode_keeps_its_apricot_and_its_end_state():
    """Kitchen's root has no apricot; cook mode's "Mark it cooked" IS the
    tab's one apricot (the rule that changed with this slice)."""
    _assert_in("Mark it cooked", SHELL_JS, "the end-of-cook primary", "shell.js")
    _assert_in("function cookFocusEndHtml(", SHELL_JS, "the end-of-cook panel", "shell.js")
    _assert_in("function cookFocusHtml(", SHELL_JS, "the focused cook screen", "shell.js")
    _assert_in("cookAheadHtml(meal)", SHELL_JS, "the cook-ahead picker", "shell.js")
    _assert_in("cookPrepCutHtml(data, meal)", SHELL_JS, "the prep-cut offer", "shell.js")


def test_no_call_site_still_asks_for_the_meals_cook_state():
    """`mealsView`/`mealsFocus` were the Meals tab's cook plumbing. Every one
    of them is a bug now: the Meals panel has no cook state to switch to, so
    the tap would land on the plan and do nothing."""
    for dead in ("mealsView:", "mealsFocus:", "setMealsView("):
        assert dead not in SHELL_JS, f"{dead!r} is still a live call in static/shell.js"
    assert "mealsView" not in (REPO / "app" / "tools" / "moves.py").read_text().replace(
        '# a state of Meals ({tab: "week", mealsView: "cook"}) until', ""
    ), "moves.py still emits the old Meals cook target"


@pytest.mark.parametrize(
    "entry_point",
    [
        # Today's Next up card and its move lines (runTodayMoveAction). It
        # goes through openRecipeFor as of 2026-09-10 for the same reason
        # Meals' "Cook this" does — the back link has to name where the tap
        # actually came from. Same {entryId, date, slot, title} payload.
        # Updated 2026-09-10 (nav v2 part 2): the crumb names the tab as the
        # household reads it, and that tab has been "Now" since the part 1
        # rename. It said "Today" here because the rename grepped markup and
        # these labels are JS object values.
        "openRecipeFor(target.cookFocus, { label: 'Now', tab: 'today' })",
        # Meals' Day/Meal "Cook this" (wireMealsStep). It goes through
        # openRecipeFor as of 2026-09-09 so cook mode's back link can name
        # the Meals step it came from; the payload it passes is the same
        # {entryId, date, slot, title} target it always was.
        "openRecipeFor({\n          entryId: entry ? entry.entry_id : null,",
        # Grocery's shop-done handoff — through openRecipeFor as of
        # 2026-09-10, and on the exact meal rather than "whatever tonight
        # turns out to be" whenever Meals has the week cached.
        # "Grocery" -> "Shop" for the same reason as the Today/Now line above.
        "openRecipeFor(tonightDinnerRecipeTarget(), { label: 'Shop', tab: 'grocery' })",
    ],
)
def test_every_former_cook_entry_point_passes_cookfocus(entry_point):
    _assert_in(entry_point, SHELL_JS, "a cook-mode entry point", "shell.js")


def test_the_focus_target_is_still_resolved_the_same_way():
    """id -> date+slot -> title -> nothing. Never a different meal than the
    one that was tapped (42d422a)."""
    fn = _function("cookResolveFocusIndex")
    assert "target.entryId" in fn and "target.date && target.slot" in fn and "target.title" in fn
    assert "return null;" in fn, "an unresolvable target must land on the root, not on a guess"
    _assert_in("if (tab.kitchen && opts && opts.cookFocus) kitchenEnterCook(opts.cookFocus);",
               SHELL_JS, "the cookFocus entry in activateTab", "shell.js")


def test_the_meals_tab_has_no_plan_cook_control_any_more():
    for dead in ("meals-seg", "data-meals-view", "ICONS.flame + '<span>Cook</span>'"):
        assert dead not in SHELL_JS, f"the Plan | Cook control is back ({dead!r})"
    assert ".meals-seg-btn" not in SHELL_CSS, "the Plan | Cook control's style is back"


def test_cook_voice_is_still_gated_after_the_move():
    """The hands-free code moved tabs with the screen; it must still be
    behind COOK_VOICE_ENABLED, and its status panel must render with the
    mics rather than on a screen that no longer exists."""
    _assert_in("var COOK_VOICE_ENABLED = false;", SHELL_JS, "the voice flag", "shell.js")
    panel = _function("cookVoicePanelHtml")
    assert "COOK_VOICE_ENABLED ?" in panel
    assert SHELL_JS.count('data-cook="voice"') == 2, (
        "expected exactly two cook mics (the recipe's and the prep section's)"
    )
    for pos in [m.start() for m in re.finditer(r'data-cook="voice"', SHELL_JS)]:
        assert "COOK_VOICE_ENABLED" in SHELL_JS[max(0, pos - 250) : pos]


# --- 3. the gear, and the Preferences sheet -------------------------------

def test_the_gear_is_in_the_header_of_every_root_screen():
    """Today, Meals, Grocery, Kitchen — and nowhere deeper."""
    assert "prefsGearHtml()" in _function("buildTodayPanel"), "Today has no gear"
    assert "prefsGearHtml()" in _function("buildKitchenPanel"), "Kitchen has no gear"
    assert "prefsGearHtml()" in _function("buildGroceryPanel"), "Grocery has no gear"
    assert "prefsGearRowHtml('meals-gear-row')" in _function("buildWeekPanel"), "Meals has no gear"
    # ...and it is hidden on the deeper steps of the two tabs that have any.
    assert "gearRow.hidden = !onRoot;" in _function("renderMealsStep"), (
        "the gear still shows on Meals' Day and Meal steps"
    )
    assert "groGear.hidden = step !== 'list';" in _function("renderGrocery"), (
        "the gear still shows on a Grocery step other than the list (merged with flows 5: Grocery is steps now)"
    )


def test_the_preferences_sheet_says_what_it_is():
    _assert_in(">Preferences<", SHELL_JS, "the sheet title", "shell.js")
    _assert_in("What Pomona knows about your household", SHELL_JS, "the sheet subtitle", "shell.js")
    assert "apricot" not in _function("renderPrefsRows"), "Preferences has no apricot"


@pytest.mark.parametrize(
    "row",
    ["Who’s here", "Your rhythm", "Prep days", "How you eat", "Stores"],
)
def test_the_five_knowledge_rows(row):
    _assert_in(row, SHELL_JS, "a Preferences row", "shell.js")


def test_the_second_group_is_a_way_out_of_trouble_and_a_way_out_of_the_app():
    rows = _function("renderPrefsRows")
    assert "snwTile()" in rows, "Preferences no longer offers 'Something not working?'"
    assert ">Sign out<" in rows, "Preferences no longer offers Sign out"
    signout = SHELL_JS[SHELL_JS.index("if (what === 'signout')") :][:400]
    assert "window.confirm(" in signout, "signing out asks first"
    assert "'/logout'" in signout, "sign out no longer hits GET /logout"
    _assert_in("passphrase", SHELL_JS, "the sign-out sub-line ('passphrase', never 'password')",
               "shell.js")
    assert "your password" not in SHELL_JS.lower()


def test_each_row_opens_the_tab_of_what_we_know_that_owns_the_answer():
    rows = SHELL_JS[SHELL_JS.index("var PREFS_ROWS = ["):]
    rows = rows[: rows.index("];")]
    for tab in ("'people'", "'rhythm'", "'rhythm/prep-days'", "'taste'", "'stores'"):
        assert f"tab: {tab}" in rows, f"no Preferences row opens What we know's {tab}"
    _assert_in("openKitchenSheet('memory', target.getAttribute('data-tab'))", SHELL_JS,
               "the row -> What we know handoff", "shell.js")


def test_the_row_subtitles_are_one_cached_read():
    """One /api/memory per open, dropped when a chat turn writes to it —
    not a fetch per row and not a stale sheet."""
    load = _function("loadPrefs")
    assert "if (prefsState.memory) { renderPrefsRows(); return; }" in load
    assert load.count("fetch(") == 1, "Preferences should cost one read, not several"
    assert "'/api/memory'" in load
    assert "prefsInvalidate();" in _function("refreshStaleTabsFromActions")


def test_the_subtitles_read_the_household_back_plainly():
    """Not "dinner_window: 6_8" — the answer, the way a person says it."""
    _assert_in("'dinner around 7'", SHELL_JS, "the dinner-window wording", "shell.js")
    _assert_in("'plan ready '", SHELL_JS, "the planning-anchor wording", "shell.js")
    _assert_in("'leftovers welcome'", SHELL_JS, "the leftovers wording", "shell.js")
    _assert_in("' snack'", SHELL_JS, "the snacks-a-week wording", "shell.js")
    _assert_in("'Not set yet'", SHELL_JS, "the unanswered-question wording", "shell.js")


def test_the_chat_carries_one_line_on_every_tab():
    """Since 2026-09-11 the chat is an icon and its composer says the same
    thing on every tab — the topic isn't always today (Emily). The per-tab
    hints, Kitchen's included, went with the always-open bar."""
    _assert_in("_default: 'What\\u2019s on your mind?'", SHELL_JS,
               "the one chat hint", "shell.js")
    assert "kitchen: 'What" not in SHELL_JS


# --- 4. the backend half --------------------------------------------------

def test_a_cook_move_targets_the_kitchen_tab(monkeypatch):
    """The payload Today's "Cook this" acts on. It named the Meals tab's
    Cook state until 2026-09-08; a stale target here is a tap that lands on
    the plan and does nothing."""
    today = date.today()
    view = {
        "weekly_plan_id": 1,
        "status": "approved",
        "meals": [{
            "entry_id": 42, "date": today.isoformat(), "slot": "dinner",
            "meal": "Sesame Salmon Bowls", "cooked_status": "pending",
            "is_leftovers": False, "prep_time_minutes": 10, "cook_time_minutes": 15,
        }],
        "prep_tasks": [],
    }
    monkeypatch.setattr(moves_mod._grocery, "list_grocery_list", lambda **kw: [])
    now = datetime.combine(today, time(9, 0))
    day_moves = moves_mod.moves_for_day(today, now=now, view=view)
    cook = [m for m in day_moves if m["kind"] == "cook"][0]
    target = cook["action"]["target"]
    assert target["tab"] == "kitchen"
    assert "mealsView" not in target and "mealsFocus" not in target
    assert target["cookFocus"] == {
        "entryId": 42,
        "date": today.isoformat(),
        "slot": "dinner",
        "title": "Sesame Salmon Bowls",
    }


def test_a_cook_move_still_carries_the_start_by_the_kitchen_line_shows(monkeypatch):
    """"start by 5:35 · 55 min" on the Kitchen root is these two chips,
    lowercased — so the chips have to keep their shape."""
    today = date.today()
    view = {
        "weekly_plan_id": 1, "status": "approved", "prep_tasks": [],
        "meals": [{
            "entry_id": 7, "date": today.isoformat(), "slot": "dinner",
            "meal": "Bulgogi", "cooked_status": "pending", "is_leftovers": False,
            "prep_time_minutes": 20, "cook_time_minutes": 35,
        }],
    }
    monkeypatch.setattr(moves_mod._grocery, "list_grocery_list", lambda **kw: [])
    cook = [m for m in moves_mod.moves_for_day(
        today, now=datetime.combine(today, time(9, 0)), view=view) if m["kind"] == "cook"][0]
    assert cook["chips"][0] == "55 min"
    assert cook["chips"][1].startswith("Start by ")


# --- 5. the fixes of 2026-09-08 (verifier findings on this branch) --------
#
# Five defects, all of them the same shape: the screen said something that
# was not true, or said nothing where something existed. The tests below
# RUN the screen's own functions under node wherever they are pure, the way
# tests/test_leftovers_batch.py does, rather than reading the source for a
# marker — what these are about is what the screen shows.

import json
import shutil
import subprocess

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)

_JS_PRELUDE = (
    "function escapeHtml(s){return String(s == null ? '' : s)"
    ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}\n"
    "const ICONS = { arrow: '<svg data-icon=\"arrow\"></svg>' };\n"
    "const COOK_ICONS = { check: '<svg data-icon=\"check\"></svg>' };\n"
    "const REHEAT_ACTION_LABEL = 'Mark eaten';\n"
    "const REHEAT_UNDO_LABEL = 'Mark not eaten';\n"
)


def _node(script: str):
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _prefs_block() -> str:
    """The whole Preferences read-back region, lifted in one slice: the five
    row functions, the two wording maps they read, and PREFS_ROWS itself."""
    start = SHELL_JS.index("function prefsPeopleLine(mem) {")
    end = SHELL_JS.index("];", SHELL_JS.index("var PREFS_ROWS = [")) + 2
    return SHELL_JS[start:end]


def _prefs_lines(memory: dict) -> dict:
    """{row title: the line the sheet shows} for one /api/memory payload,
    produced by the sheet's own functions."""
    script = (
        _prefs_block() + "\n"
        + f"const MEM = {json.dumps(memory)};\n"
        + "const out = {};\n"
        + "PREFS_ROWS.forEach(function (r) { out[r.title] = r.line(MEM); });\n"
        + "console.log(JSON.stringify(out));\n"
    )
    return _node(script)


# --- 5a. Preferences may not print a default as a fact --------------------

def test_a_brand_new_household_is_told_nothing_it_never_said(signed_in):
    """The sheet is titled "What Pomona knows about your household". Before
    the household has said anything, the honest answer for every row is
    that it does not know — snacks included.

    snacks_per_week was the one that lied: it is NOT NULL DEFAULT 3, so
    "How you eat" read "3 snacks a week" at a household that had never been
    asked. The other four rows read nullable facts and were already honest;
    this pins all five together so the next default added here is caught.
    """
    memory = signed_in.get("/api/memory").json()
    assert memory["snacks_per_week"] == 3, "the default itself is unchanged"
    assert memory["snacks_per_week_set"] is False, "nobody has answered it"
    lines = _prefs_lines(memory)
    # Two of the empty rows say what the answer does for the household
    # rather than "Not set yet" (copy cleanse, 2026-09-11) — still nothing
    # the household never said.
    assert lines == {
        "Who’s here": "Not set yet",
        "Your rhythm": "Sets when to start cooking",
        "Prep days": "Batches the week around them",
        "How you eat": "Not set yet",
        # The calendar row (2026-09-11) reads /api/calendar, not memory; with
        # nothing connected its honest answer is this, and nothing more.
        "Your calendar": "Not connected",
        "Stores": "Not set yet",
    }


def test_a_household_that_answered_snacks_sees_its_own_number(signed_in):
    """...and the fix must not swallow a real answer."""
    signed_in.post("/api/memory/edit", json={"field": "snacks_per_week", "value": 2})
    memory = signed_in.get("/api/memory").json()
    assert memory["snacks_per_week_set"] is True
    assert _prefs_lines(memory)["How you eat"] == "2 snacks a week"


def test_answering_zero_snacks_is_an_answer_too(signed_in):
    """0 is falsy and used to disappear; "no snacks" is a thing someone
    said, and the row says it."""
    signed_in.post("/api/memory/edit", json={"field": "snacks_per_week", "value": 0})
    memory = signed_in.get("/api/memory").json()
    assert memory["snacks_per_week_set"] is True
    assert _prefs_lines(memory)["How you eat"] == "no snacks"


def test_forgetting_the_snacks_answer_forgets_that_there_was_one(signed_in):
    """delete_preference puts the number back to the default — so it has to
    put the flag back too, or the sheet keeps reading 3 back as a fact."""
    signed_in.post("/api/memory/edit", json={"field": "snacks_per_week", "value": 5})
    signed_in.post("/api/memory/delete", json={"field": "snacks_per_week"})
    memory = signed_in.get("/api/memory").json()
    assert memory["snacks_per_week"] == 3
    assert memory["snacks_per_week_set"] is False
    assert _prefs_lines(memory)["How you eat"] == "Not set yet"


def test_onboarding_counts_as_answering_the_snacks_question(signed_in):
    """A household that finished the wizard HAS answered the snacks
    question — even when their answer happens to equal the default.

    CORRECTED 2026-09-08: this used to say "the wizard always sends
    snacks_per_week". It doesn't any more — static/onboarding.html asks
    snacks per DAY now (Julia, first beta tester) and sends
    snacks_per_day. An explicit snacks_per_week is still accepted and
    still counts, which is what this pins; the per-day path has its own
    test in tests/test_onboarding_copy_v2.py."""
    res = signed_in.post("/api/onboarding/answers", json={
        "member_names": ["Emily"],
        "household_restrictions": {},
        "eating_style": "",
        "wont_eat": [],
        "excited_about": [],
        "dinners_per_week": 5,
        "snacks_per_week": 3,
    })
    assert res.status_code == 200
    memory = signed_in.get("/api/memory").json()
    assert memory["snacks_per_week_set"] is True
    assert _prefs_lines(memory)["How you eat"] == "3 snacks a week"


# --- 5b. no prep task is invisible ----------------------------------------

_PREP_JS = (
    _JS_PRELUDE
    + "function dayNameShort(d){ return 'Wed'; }\n"
    + _function("cookFocusPrepTasks") + "\n"
    + _function("kitchenLoosePrepTasks") + "\n"
    + _function("kitchenPrepTodoHtml") + "\n"
)


def _prep_todo(data: dict) -> dict:
    """What the Kitchen root's "Prep to do" list is given, and what it
    renders — plus, for the "nowhere twice" half, which task ids the cook
    screens already carry."""
    script = (
        _PREP_JS
        + f"const data = {json.dumps(data)};\n"
        + "const loose = kitchenLoosePrepTasks(data);\n"
        + "const onCookScreens = [];\n"
        + "(data.meals || []).forEach(function (m) {\n"
        + "  cookFocusPrepTasks(data, m).forEach(function (t) { onCookScreens.push(t.id); });\n"
        + "});\n"
        + "console.log(JSON.stringify({\n"
        + "  loose: loose.map(function (t) { return t.id; }),\n"
        + "  html: kitchenPrepTodoHtml(loose),\n"
        + "  onCookScreens: onCookScreens\n"
        + "}));\n"
    )
    return _node(script)


_ORPHAN_DATA = {
    "weekly_plan_id": 1,
    "meals": [
        {"entry_id": 3, "date": "2026-09-09", "slot": "dinner", "meal": "Bulgogi Wraps",
         "cooked_status": "pending", "is_leftovers": False},
    ],
    "prep_tasks": [
        # The orphan: no meal_plan_entry_id, a related_meal that matches no
        # dish on the plan, and a date that is not a prep day. It rendered
        # nowhere at all before 2026-09-08.
        {"id": 11, "task_date": "2026-09-10", "description": "Soak the beans",
         "related_meal": "", "status": "pending", "task_type": "general",
         "meal_plan_entry_id": None},
        # Belongs to a cook screen (it names the meal) — must NOT be here.
        {"id": 12, "task_date": "2026-09-09", "description": "Marinate the beef",
         "related_meal": "Bulgogi Wraps", "status": "pending", "task_type": "general",
         "meal_plan_entry_id": None},
        # Belongs to Sunday's session — must NOT be here.
        {"id": 13, "task_date": "2026-09-13", "description": "Chop the onions",
         "related_meal": "", "status": "pending", "task_type": "prep_cut",
         "meal_plan_entry_id": None},
        # Already done: not lost, so not collected.
        {"id": 14, "task_date": "2026-09-10", "description": "Toast the sesame seeds",
         "related_meal": "", "status": "done", "task_type": "general",
         "meal_plan_entry_id": None},
    ],
    "prep_sessions": [
        {"date": "2026-09-13", "weekday": "Sunday", "items_done": 0, "items_total": 1,
         "total_minutes_estimate": 10, "covers": [], "note": "", "minutes_planned": 10,
         "items": [{"kind": "prep_cut", "prep_task_id": 13, "entry_id": None,
                    "title": "Chop the onions", "done": False}]},
    ],
}


@_needs_node
def test_an_orphan_prep_task_is_collected_by_prep_to_do():
    """"Soak the beans", two days out, with no entry link and no name match:
    no session shows it, no cook screen shows it, and Today only ever shows
    today. A task the app wrote and then hid is worse than one it never
    wrote."""
    out = _prep_todo(_ORPHAN_DATA)
    assert out["loose"] == [11], "only the orphan belongs in Prep to do"
    assert "Soak the beans" in out["html"]
    assert "Prep to do" in out["html"]
    assert 'data-cook="check-prep" data-prep-id="11"' in out["html"], "it has to be tickable"
    assert "WED" in out["html"], "and dated"


@_needs_node
def test_prep_to_do_never_shows_a_task_twice():
    """It is the net under the other two lists, not a third copy of them."""
    out = _prep_todo(_ORPHAN_DATA)
    assert 12 in out["onCookScreens"], "the marinate task is on its cook screen"
    session_ids = [
        item["prep_task_id"]
        for session in _ORPHAN_DATA["prep_sessions"]
        for item in session["items"]
    ]
    assert 13 in session_ids, "the prep-cut is in Sunday's session"
    for already_shown in out["onCookScreens"] + session_ids:
        assert already_shown not in out["loose"], (
            f"prep task {already_shown} renders in two places on the Kitchen tab"
        )
    assert 14 not in out["loose"], "a finished task is not a lost one"
    assert "Toast the sesame seeds" not in out["html"]


@_needs_node
def test_prep_to_do_is_nothing_at_all_when_nothing_is_orphaned():
    data = dict(_ORPHAN_DATA, prep_tasks=[
        t for t in _ORPHAN_DATA["prep_tasks"] if t["id"] != 11
    ])
    out = _prep_todo(data)
    assert out["loose"] == []
    assert out["html"] == "", "an empty net is not a section"


def test_the_kitchen_root_renders_the_net_under_the_sessions():
    root = _function("renderKitchen")
    assert (
        "cookPrepSessionsHtml(data) +\n"
        "      kitchenPrepTodoHtml(kitchenLoosePrepTasks(data)) +" in root
    ), "Prep to do belongs directly under Prep sessions"
    _assert_in("Prep to do", SHELL_JS, "the Prep to do eyebrow", "shell.js")


# --- 5c. "Show me tomorrow" lands on tomorrow -----------------------------

_TOMORROW_JS = (
    "var COOK_SLOT_ORDER = ['breakfast', 'lunch', 'dinner', 'snack'];\n"
    + _function("tomorrowLocalStr") + "\n"
    + _function("cookSlotRank") + "\n"
    + _function("cookTomorrowHasPrepOrDefrost") + "\n"
    + _function("cookTomorrowFocusTarget") + "\n"
    + _function("cookTomorrowHasSomethingToShow") + "\n"
)


def _tomorrow_target(meals: list, prep_tasks: list | None = None):
    script = (
        _TOMORROW_JS
        + "var cookState = { data: { meals: "
        + json.dumps(meals) + ", prep_tasks: " + json.dumps(prep_tasks or []) + " } };\n"
        + "console.log(JSON.stringify({ target: cookTomorrowFocusTarget(),\n"
        + "  offered: cookTomorrowHasSomethingToShow() }));\n"
    )
    return _node(script)


@_needs_node
def test_show_me_tomorrow_opens_tomorrows_first_cook():
    """It used to land on the Kitchen root with no focus, which shows
    tomorrow only when tomorrow happens to be a prep-session day. Now it
    opens the cook — in slot order, so breakfast before dinner."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    out = _tomorrow_target([
        {"entry_id": 9, "date": tomorrow, "slot": "dinner", "meal": "Bulgogi Wraps",
         "is_leftovers": False},
        {"entry_id": 8, "date": tomorrow, "slot": "breakfast", "meal": "Egg Bites",
         "is_leftovers": False},
        {"entry_id": 7, "date": date.today().isoformat(), "slot": "dinner", "meal": "Chili",
         "is_leftovers": False},
    ])
    assert out["target"] == {
        "entryId": 8, "date": tomorrow, "slot": "breakfast", "title": "Egg Bites",
    }
    assert out["offered"] is True


@_needs_node
def test_show_me_tomorrow_skips_a_reheat_night():
    """A reheat has no cook screen at all, so focusing it would land on a
    card rather than on tomorrow."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    out = _tomorrow_target([
        {"entry_id": 9, "date": tomorrow, "slot": "dinner", "meal": "Bulgogi Wraps",
         "is_leftovers": True, "leftovers_headline": "Leftovers"},
    ], prep_tasks=[{"id": 1, "task_date": tomorrow, "description": "Defrost the chicken"}])
    assert out["target"] is None
    assert out["offered"] is True, "there is still prep to show"


@_needs_node
def test_show_me_tomorrow_is_not_offered_when_tomorrow_has_nothing():
    out = _tomorrow_target([
        {"entry_id": 7, "date": date.today().isoformat(), "slot": "dinner",
         "meal": "Chili", "is_leftovers": False},
    ])
    assert out["target"] is None
    assert out["offered"] is False


def test_show_me_tomorrow_falls_back_to_the_prep_it_promised():
    """No cook to open: the root, scrolled onto the prep, never the top of
    a tab where tomorrow sits below the fold."""
    fn = _function("cookShowTomorrow")
    assert "activateTab('kitchen', true, { cookFocus: focus })" in fn
    assert "kitchenState.scrollToPrep = true;" in fn
    render = _function("renderCook")
    assert "kitchenState.scrollToPrep" in render, "nothing consumes the flag"
    assert "'#kit-prep-todo'" in render and "'#kit-prep-sessions'" in render
    _assert_in("onClick: cookShowTomorrow", SHELL_JS, "the toast's action", "shell.js")


# --- 5d. the four nits ----------------------------------------------------

_SUBTITLE_JS = (
    _JS_PRELUDE
    + "function dayName(d, o){ return 'Monday'; }\n"
    + _function("kitchenTodayLine") + "\n"
    + _function("kitchenTodayRows") + "\n"
    + _function("kitchenSubtitle") + "\n"
)


def _subtitle(meals: list, moves: list, iso: str = "2026-09-07"):
    script = (
        _SUBTITLE_JS
        + f"const meals = {json.dumps(meals)}, moves = {json.dumps(moves)};\n"
        + f"const rows = kitchenTodayRows(meals, moves, {json.dumps(iso)});\n"
        + "console.log(JSON.stringify({ subtitle: kitchenSubtitle(rows, meals, "
        + json.dumps(iso) + "), lines: rows.map(function (r) { return r.line; }) }));\n"
    )
    return _node(script)


def _three_dinners(done_count: int) -> list:
    return [
        {"entry_id": i, "date": "2026-09-07", "slot": "dinner", "meal": f"Dish {i}",
         "is_leftovers": False, "prep_time_minutes": 10, "cook_time_minutes": 20,
         "cooked_status": "done" if i <= done_count else "pending"}
        for i in (1, 2, 3)
    ]


@_needs_node
def test_the_subtitle_counts_what_is_left_once_anything_is_cooked():
    """At eight in the evening with two of three cooked, "3 cooks today" is
    a number nobody recognises."""
    assert _subtitle(_three_dinners(0), [])["subtitle"] == "Monday · 3 cooks tonight"
    assert _subtitle(_three_dinners(2), [])["subtitle"] == "Monday · 1 cook left tonight"
    assert _subtitle(_three_dinners(3), [])["subtitle"] == "Monday · nothing left to cook today"
    assert _subtitle([], [])["subtitle"] == "Monday · nothing to cook today"


@_needs_node
def test_the_subtitle_believes_the_moves_done_state_too():
    """The plan row and the move are the same fact from two reads; either
    can be the fresher one after a tick."""
    meals = _three_dinners(0)
    moves = [{"kind": "cook", "entry_id": 1, "done": True, "chips": []},
             {"kind": "cook", "entry_id": 2, "done": True, "chips": []}]
    assert _subtitle(meals, moves)["subtitle"] == "Monday · 1 cook left tonight"


@_needs_node
def test_a_cooked_row_loses_its_start_by_line():
    """The moves payload keeps the chip — it is arithmetic about the slot,
    not about the tick — so the row drops it rather than telling someone
    who has just cooked when they should have started."""
    meals = _three_dinners(1)
    moves = [{"kind": "cook", "entry_id": 1, "done": True,
              "chips": ["30 min", "Start by 5:35"], "detail": "dinner"},
             {"kind": "cook", "entry_id": 2, "done": False,
              "chips": ["30 min", "Start by 5:35"], "detail": "dinner"}]
    lines = _subtitle(meals, moves)["lines"]
    assert lines[0] == "30 min", "a cooked row has no start-by left to make"
    assert lines[1] == "start by 5:35 · 30 min"


def test_a_check_off_refreshes_the_moves_the_kitchen_root_reads():
    """renderCookFrom refreshes cookState.data off the write's own response;
    /api/today/moves is a separate read and nothing was re-reading it."""
    fn = _function("refreshPlanSurfacesAfterCook")
    assert "refreshKitchenMoves();" in fn
    refresh = _function("refreshKitchenMoves")
    assert "'/api/today/moves'" in refresh
    assert "kitchenState.moves =" in refresh
    assert "renderCook();" in refresh


def test_the_cook_mode_apricot_and_the_end_button_say_the_same_thing():
    """One action, written once, said the same way in both places a cook
    meets it.

    UPDATED 2026-09-10 (branch `overnight/cook-journey-step-by-step`): cook
    mode became three stages — Before you start, one step at a time, the
    whole method — so its apricot moved out of cookFocusHtml and into the
    dock those stages share (cookDockCookedHtml). The rule this test is
    about did not move with it: the button still says "Mark it cooked", the
    same words as the row that closes the whole method's last step. Only
    the function the assertion is scoped to changed.
    """
    cooked = _function("cookDockCookedHtml")
    assert "'Mark it cooked'" in cooked, "the apricot still says 'Mark it cooked'"
    assert "'Mark cooked'" not in cooked
    assert ">Mark it cooked<" in _function("cookFocusEndHtml")
    # The Kitchen root's own checkboxes keep "Mark cooked" as their
    # aria-label — cookCheckMeal reads that exact string to tell a real
    # cook from a reheat before it offers the "Rate it" toast.
    assert "'Mark cooked'" in _function("kitchenTodayRowHtml")
    assert "el.getAttribute('aria-label') === 'Mark cooked'" in _function("cookCheckMeal")


_ACTION_JS = (
    "var calls = [];\n"
    "function activateTab(key, real, opts){ calls.push(['activateTab', key, opts || null]); }\n"
    # Updated 2026-09-10: a cook opened from Today goes through openRecipeFor,
    # like every other way into cook mode, so its back link names that tab
    # instead of inheriting whatever origin an earlier deep link left on
    # cookState. Same target, one door further in. The words are "‹ Now"
    # rather than "‹ Today" as of nav v2 part 2 — same tab, current name.
    "function openRecipeFor(target, origin){ calls.push(['openRecipeFor', target, origin || null]); }\n"
    "function toggleTodayMove(panel, id, next){ calls.push(['tick', id, next]); }\n"
    + _function("runTodayMoveAction") + "\n"
)

_FROM_TODAY = {"label": "Now", "tab": "today"}


def _run_move_action(target: dict):
    script = (
        _ACTION_JS
        + "var panel = { _moves: { moves: [ { id: 'cook:42', done: false, action: { target: "
        + json.dumps(target) + " } } ] } };\n"
        + "runTodayMoveAction(panel, 'cook:42');\n"
        + "console.log(JSON.stringify(calls));\n"
    )
    return _node(script)


@_needs_node
def test_a_stale_cached_move_still_lands_in_cook_mode():
    """A payload cached before 2026-09-08 names the Meals tab's cook state.
    That tab has no cook state any more, so the tap used to land on the plan
    and silently do nothing; it is translated instead."""
    focus = {"entryId": 42, "date": "2026-09-07", "slot": "dinner", "title": "Bulgogi"}
    calls = _run_move_action({"tab": "week", "mealsView": "cook", "mealsFocus": focus})
    assert calls == [["openRecipeFor", focus, _FROM_TODAY]]


@_needs_node
def test_a_stale_cached_move_with_no_focus_still_lands_in_cook_mode():
    """The legacy `true` target — "tonight, whatever that turns out to be"."""
    calls = _run_move_action({"tab": "week", "mealsView": "cook"})
    assert calls == [["openRecipeFor", True, _FROM_TODAY]]


@_needs_node
def test_todays_current_move_shape_is_untouched_by_that_tolerance():
    focus = {"entryId": 42, "date": "2026-09-07", "slot": "dinner", "title": "Bulgogi"}
    assert _run_move_action({"tab": "kitchen", "cookFocus": focus}) == [
        ["openRecipeFor", focus, _FROM_TODAY]
    ]
    assert _run_move_action({"kind": "check_meal", "entryId": 42}) == [["tick", "cook:42", True]]
