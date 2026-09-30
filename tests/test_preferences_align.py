"""
Preferences says one thing in one place (Emily, 2026-09-25 — all five
approved from the read-only Preferences review the same day).

1. "Each week I plan" -> "Different dishes a week", with one line under it:
   "Fewer means more leftovers and batch cooking." The counts have been
   targets for DIFFERENT dishes since 373f009 (app/tools/meal_variety.py).
2. One name for the cuisine list: "Cuisines you like" — Preferences, the
   weekly step (plan-week.html) and onboarding's step title. Preferences
   also shows onboarding's fifteen cuisine chips, each toggling membership
   in the same stored list (cuisine_preferences).
3. "Meals eaten together" left Your rhythm — nothing reads meals_together
   (§2b S4). The stored answer is left alone.
4. Two settings say what they do: "Sets which days I suggest each week."
   under "Plan ready by"; the lunch chip "Out" reads "On the go" (stored
   key still 'out') with "On-the-go days are pre-ticked for packed lunches
   each week." under it.
5. /meal-setup folded into Preferences: the weeknight limit ("On a
   weeknight"), "At the table" and "A normal week at yours" moved into What
   we know, Plan's "Adjust your setup →" opens the Preferences sheet, and
   /meal-setup (and /onboarding on a set-up household) redirects to the
   shell with the sheet open.

Behaviour is run under node against shell.js's own functions (the house
standard — tests/nodeharness.py); the routes through the TestClient.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests import nodeharness

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"
SHELL_JS = (STATIC / "shell.js").read_text(encoding="utf-8")
ONBOARDING = (STATIC / "onboarding.html").read_text(encoding="utf-8")
PLAN_WEEK = (STATIC / "plan-week.html").read_text(encoding="utf-8")


def _function(name: str, source: str = SHELL_JS) -> str:
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


def _constants() -> str:
    """Every WWK_* list, from the age groups to the section table."""
    start = SHELL_JS.index("  var WWK_AGE_GROUPS = [")
    end = SHELL_JS.index("  var WWK_SECTIONS = [")
    return SHELL_JS[start:end]


def _usual_week_constants() -> str:
    """Your rhythm's usual-week words and state (2026-09-30)."""
    start = SHELL_JS.index("  var UW_MEALS = [")
    end = SHELL_JS.index("  // A cell as the server sends it")
    return SHELL_JS[start:end]


USUAL_WEEK_FNS = [
    "wwkUsualWeekHtml", "uwSetRowHtml", "uwFromServer", "uwCells", "uwDaysOn", "uwNames", "uwWho",
    "uwCellClass", "uwCellAria", "uwDayRowHtml", "uwVarietyLine", "uwPrepDaysLine", "uwSnacksLabel",
]


def _harness(*extra: str) -> str:
    names = [
        "escapeHtml", "wwkChip", "wwkFactChip", "wwkAddChip", "wwkLead", "wwkNote",
        "wwkFactsHtml", "wwkStepperHtml", "wwkSnackDishes", "wwkProteinState", "wwkMem",
        "wwkRhythmHtml", "wwkWeeknightHtml", "wwkTasteHtml",
        "wwkCuisineStored", "wwkIsPresetCuisine", *USUAL_WEEK_FNS, *extra,
    ]
    return (
        "var wwkState = { facts: [], pendingCookWho: false, openSections: {} };\n"
        "var prefsState = { memory: null };\n"
        + _constants() + "\n"
        + _usual_week_constants() + "\n"
        + "\n".join(_function(n) for n in names) + "\n"
    )


def _run(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


MEMORY = {
    "members": [{"name": "Emily"}, {"name": "Sam"}],
    "rhythm": {
        "planning_anchor": "sunday",
        "meals_together": "most_meals",
        "lunch_location": {"Emily": {"standing": "out", "overrides": {}}},
    },
    "cuisine_preferences": ["thai", "Peruvian"],
    "dinners_per_week": 5, "breakfasts_per_week": 7, "lunches_per_week": 7, "snacks_per_day": 2,
    "weeknight_max_minutes": 45,
    "table_style": "kids_differ",
    "typical_week": "Tuesdays are <swim> so we eat at 5.",
}


def _render(fn: str, memory: dict) -> str:
    return _run(_harness() + f"""
prefsState.memory = {json.dumps(memory)};
console.log(JSON.stringify({fn}(prefsState.memory)));
""")


# --- 1. Different dishes a week ---------------------------------------------

def test_the_different_dishes_steppers_are_gone():
    """2026-09-30: breakfasts, lunches and dinners are Your rhythm's usual
    week (days, who's eating, variety) — the "Different dishes a week"
    steppers and "Snacks a day" left How you eat."""
    html = _render("wwkTasteHtml", MEMORY)
    assert "Different dishes a week" not in html
    assert "Fewer means more leftovers" not in html
    for field in ("dinners_per_week", "breakfasts_per_week", "lunches_per_week", "snacks_per_day"):
        assert f'data-field="{field}"' not in html, f"{field} still has a stepper"
    assert "WWK_COUNTS" not in SHELL_JS and "'Snacks a day'" not in SHELL_JS


def test_different_snacks_a_week_keeps_its_stepper():
    html = _render("wwkTasteHtml", MEMORY)
    assert '<p class="wwk-lead">Different snacks a week</p>' in html
    assert html.index("Different snacks a week") < html.index('data-field="snack_dishes_per_week"')
    assert '<span class="wwk-count-label">Snacks</span>' in html
    # Two when the household never set it, and the minus stops at snacks a day (2).
    assert re.search(r'data-field="snack_dishes_per_week" data-delta="-1" data-max="7" data-min="2" '
                     r'aria-label="Fewer different snacks">&minus;</button><span class="cook-serves-count">2<', html)


def test_snacks_a_day_above_snacks_brings_snacks_up_on_screen():
    out = _run(_harness("wwkSetCount") + f"""
var saves = [];
function wwkSavePreference(section, field, value, apply) {{ saves.push([field, value]); apply(); }}
prefsState.memory = {json.dumps(dict(MEMORY, snack_dishes_per_week=2))};
wwkSetCount('snacks_per_day', 1, 6, 0);    // 2 -> 3: Snacks comes up to 3
var afterRaise = prefsState.memory.snack_dishes_per_week;
wwkSetCount('snack_dishes_per_week', -1, 7, 3);  // at the floor: nothing
console.log(JSON.stringify([saves, afterRaise, prefsState.memory.snack_dishes_per_week]));
""")
    assert out == [[["snacks_per_day", 3]], 3, 3]


def test_the_snacks_stepper_shows_a_clamped_value_so_minus_never_jumps_up():
    """An older row with Snacks below Snacks a day shows Snacks a day, and
    "−" at that floor does nothing (review round 2)."""
    html = _render("wwkTasteHtml", dict(MEMORY, snacks_per_day=3, snack_dishes_per_week=1))
    assert re.search(r'data-field="snack_dishes_per_week" data-delta="-1" data-max="7" data-min="3" '
                     r'aria-label="Fewer different snacks">&minus;</button><span class="cook-serves-count">3<', html)
    out = _run(_harness("wwkSetCount") + f"""
var saves = [];
function wwkSavePreference(section, field, value, apply) {{ saves.push([field, value]); apply(); }}
prefsState.memory = {json.dumps(dict(MEMORY, snacks_per_day=3, snack_dishes_per_week=1))};
wwkSetCount('snack_dishes_per_week', -1, 7, 3);
console.log(JSON.stringify(saves));
""")
    assert out == []


def test_the_snacks_count_saves_and_reads_back(signed_in):
    assert signed_in.get("/api/memory").json()["snack_dishes_per_week"] == 2
    res = signed_in.post("/api/memory/edit", json={"field": "snack_dishes_per_week", "value": 3})
    assert res.status_code == 200, res.text
    assert signed_in.get("/api/memory").json()["snack_dishes_per_week"] == 3
    assert signed_in.post("/api/memory/edit", json={"field": "snack_dishes_per_week", "value": 0}).status_code >= 400


# --- 2. Cuisines you like ------------------------------------------------------

def _onboarding_cuisines() -> list[str]:
    block = ONBOARDING[ONBOARDING.index("const CUISINES = ["):]
    block = block[: block.index("];")]
    return re.findall(r"'([^']+)'", block)


def test_preferences_shows_onboardings_fifteen_cuisines():
    presets = _run(_harness() + "console.log(JSON.stringify(WWK_CUISINES));")
    assert presets == _onboarding_cuisines()
    assert len(presets) == 15


def test_the_cuisine_chips_read_the_stored_list_in_any_case():
    html = _render("wwkTasteHtml", MEMORY)
    assert '<p class="wwk-lead">Cuisines you like</p>' in html and "Excited about" not in html
    # "thai" stored lower-case still lights Thai; Greek isn't on the list.
    assert re.search(r'class="wwk-chip is-on" aria-pressed="true" data-wwk="cuisine" data-value="Thai"', html)
    assert re.search(r'class="wwk-chip" aria-pressed="false" data-wwk="cuisine" data-value="Greek"', html)
    # Something typed that isn't a preset keeps its own × chip; a preset
    # is never drawn twice.
    assert 'data-wwk="cuisine-remove" data-value="Peruvian"' in html
    assert 'data-wwk="cuisine-remove" data-value="thai"' not in html
    assert 'data-kind="cuisine"' in html, "+ Add stays for anything not on the chips"


def test_a_cuisine_chip_toggles_membership_of_the_one_stored_list():
    out = _run(_harness("wwkToggleCuisine") + f"""
var calls = [];
function wwkListAdd() {{ calls.push(['add'].concat([].slice.call(arguments))); }}
function wwkListRemove() {{ calls.push(['remove'].concat([].slice.call(arguments))); }}
prefsState.memory = {json.dumps(MEMORY)};
wwkToggleCuisine('Thai');
wwkToggleCuisine('Greek');
console.log(JSON.stringify(calls));
""")
    assert out == [
        # Off comes the STORED spelling, so /api/memory/delete matches it.
        ["remove", "taste", "cuisine_preferences", "cuisine_preferences", "thai"],
        ["add", "taste", "cuisine_preferences", "cuisine_preferences", "Greek"],
    ]


def test_the_click_handler_routes_a_cuisine_chip():
    assert "case 'cuisine': return wwkToggleCuisine(value);" in SHELL_JS


def test_the_weekly_step_and_onboarding_use_the_same_name():
    assert '<p class="eyebrow group-eyebrow">Cuisines you like</p>' in PLAN_WEEK
    assert "Cuisines you fancy" not in PLAN_WEEK
    assert "'excited-about': 'Cuisines you like'," in ONBOARDING
    assert "What you're into" not in ONBOARDING


# --- 3 and 4. Your rhythm ------------------------------------------------------

def test_meals_eaten_together_is_gone_from_your_rhythm():
    html = _render("wwkRhythmHtml", MEMORY)
    assert "Meals eaten together" not in html and "meals_together" not in html
    assert "WWK_MEALS_TOGETHER" not in SHELL_JS


def test_plan_ready_by_says_what_it_does():
    html = _render("wwkRhythmHtml", MEMORY)
    lead = html.index('<p class="wwk-lead">Plan ready by</p>')
    note = html.index('<p class="wwk-note">Sets which days I suggest each week.</p>')
    assert lead < note < html.index('data-field="planning_anchor"')


def test_lunch_out_reads_on_the_go_and_keeps_its_stored_key():
    html = _render("wwkRhythmHtml", MEMORY)
    assert '<p class="wwk-note">On-the-go days are pre-ticked for packed lunches each week.</p>' in html
    assert re.search(r'is-on" aria-pressed="true" data-wwk="lunch" data-member="Emily" data-value="out">On the go<', html)
    assert ">Out<" not in html


# --- 5. What moved in from /meal-setup ----------------------------------------

def test_the_weeknight_limit_is_in_your_rhythm():
    html = _render("wwkRhythmHtml", MEMORY)
    assert '<p class="wwk-lead">On a weeknight</p>' in html
    assert '<span class="wwk-count-label">45 minutes at most</span>' in html
    none = _render("wwkRhythmHtml", {**MEMORY, "weeknight_max_minutes": 0})
    assert '<span class="wwk-count-label">No limit</span>' in none


def test_the_weeknight_stepper_moves_in_tens_between_0_and_120():
    out = _run(_harness("wwkSetWeeknight") + f"""
var saves = [];
function wwkSavePreference(section, field, value, apply) {{ saves.push([section, field, value]); apply(); }}
prefsState.memory = {json.dumps({**MEMORY, "weeknight_max_minutes": 110})};
wwkSetWeeknight(10);   // 120
wwkSetWeeknight(10);   // already at the top: nothing
prefsState.memory.weeknight_max_minutes = 0;
wwkSetWeeknight(-10);  // already 0: nothing
wwkSetWeeknight(10);   // 10
console.log(JSON.stringify(saves));
""")
    assert out == [["rhythm", "weeknight_max_minutes", 120], ["rhythm", "weeknight_max_minutes", 10]]


def test_a_normal_week_at_yours_is_a_box_in_your_rhythm_saved_on_blur():
    html = _render("wwkRhythmHtml", MEMORY)
    assert '<p class="wwk-lead">A normal week at yours</p>' in html
    assert 'data-wwk-input="typical_week"' in html
    assert "Tuesdays are &lt;swim&gt; so we eat at 5.</textarea>" in html
    assert "if (kind === 'typical_week') return wwkSaveTypicalWeek(t.value.trim());" in SHELL_JS


def test_at_the_table_is_in_how_you_eat():
    html = _render("wwkTasteHtml", MEMORY)
    assert '<p class="wwk-lead">At the table</p>' in html
    assert re.search(r'is-on" aria-pressed="true" data-wwk="table-style" data-value="kids_differ">The children often eat something else<', html)
    out = _run(_harness("wwkSetTableStyle") + f"""
var saves = [];
function wwkSavePreference(section, field, value, apply) {{ saves.push([section, field, value]); apply(); }}
prefsState.memory = {json.dumps(MEMORY)};
wwkSetTableStyle('kids_differ');     // already the answer: nothing
wwkSetTableStyle('everyone_same');
console.log(JSON.stringify([saves, prefsState.memory.table_style]));
""")
    assert out == [[["taste", "table_style", "everyone_same"]], "everyone_same"]


def test_the_moved_settings_save_and_read_back_through_memory(signed_in):
    for field, value in (("weeknight_max_minutes", 40), ("table_style", "plate_your_own"),
                         ("typical_week", "Fridays are takeout.")):
        res = signed_in.post("/api/memory/edit", json={"field": field, "value": value})
        assert res.status_code == 200, (field, res.text)
    mem = signed_in.get("/api/memory").json()
    assert mem["weeknight_max_minutes"] == 40
    assert mem["table_style"] == "plate_your_own"
    assert mem["typical_week"] == "Fridays are takeout."


def test_meal_setup_redirects_to_the_preferences_sheet(signed_in):
    res = signed_in.get("/meal-setup", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/week?prefs=open"
    assert not (STATIC / "meal-setup.html").exists()


def test_the_shell_opens_preferences_from_the_param_and_scrubs_it():
    boot = SHELL_JS[SHELL_JS.index("(async function checkOnboarding() {"):]
    boot = boot[: boot.index("})();")]
    assert "get('prefs') === 'open'" in boot
    assert boot.index("replaceState") < boot.index("openPrefsSheet();")


def test_adjust_your_setup_opens_the_preferences_sheet():
    assert "row.querySelector('#week-setup-standing').addEventListener('click', openPrefsSheet);" in SHELL_JS
