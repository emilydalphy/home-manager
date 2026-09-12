"""
"What we know" is native — one sheet, collapsible sections, every change
saving as it is made (2026-09-12).

Two Loop Board cards from the 2026-09-11 design audit: "What we know:
rebuild it native, in the Preferences sheet's vocabulary" and "What we know
screen needs autosave and collapse". Until this, the sheet loaded
static/memory.html inside #kit-sheet's iframe — the last surface still
running the old app (dotted chips, "Age group" / "Dietary restrictions"
form labels, a bordered-pill segmented control, a Save button). It is DOM
the shell builds now (shell.js's "What we know (native, 2026-09-12)"
section), and Inventory is the only page left in a frame.

Source assertions, the same kind and for the same reason as
tests/test_kitchen_and_preferences.py: shell.js has no JS test harness in
this repo, and a marker that is present but mis-wired is still a far
better failure mode than a marker that is gone. User-facing copy is
asserted verbatim — if it is deliberately reworded, update it here in the
same commit and say so; do not delete the test.

The fact checklist at the bottom is the ticket's own acceptance criterion:
every fact the old page could edit still has a control here.
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
SHELL_CSS = (STATIC / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (STATIC / "shell.html").read_text(encoding="utf-8")


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


def _wwk_block() -> str:
    """The whole native section, from its banner to the recipe-link sheet
    that follows it."""
    start = SHELL_JS.index("// ---------- What we know (native, 2026-09-12) ----------")
    end = SHELL_JS.index('// ---------- "Add from a link" (recipe import, 2026-09-11) ----------')
    return SHELL_JS[start:end]


WWK = _wwk_block()


# --- 1. native, not framed ------------------------------------------------

def test_what_we_know_is_no_longer_an_iframe():
    sheets = SHELL_JS[SHELL_JS.index("var KITCHEN_SHEETS = {"):]
    sheets = sheets[: sheets.index("};")]
    assert "memory: { title: 'What we know', native: true" in sheets
    assert "stores: { title: 'What we know', native: true, section: 'stores' }" in sheets
    assert "memory.html" not in sheets, "What we know still points at the old page"
    # Inventory keeps its frame: deferred beta, Emily's call.
    assert "inventory: { title: 'Inventory', src: '/static/inventory.html' }" in sheets


def test_the_sheet_has_a_native_body_beside_the_frame():
    assert '<div class="wwk-body" id="wwk-body" hidden></div>' in SHELL_HTML
    assert '<iframe id="kit-sheet-frame" class="kit-sheet-frame" title="" hidden></iframe>' in SHELL_HTML
    opener = _function("openKitchenSheet")
    assert "if (meta.native) {" in opener
    assert "openWhatWeKnow(section || meta.section);" in opener
    assert "frame.hidden = true;" in opener and "body.hidden = true;" in opener, (
        "one body shows at a time"
    )
    assert "showKitchenTab" not in SHELL_JS, "the iframe tab handoff should be gone"


def test_nothing_in_the_shell_links_to_the_old_page():
    assert "/static/memory.html" not in SHELL_JS
    assert re.search(r"""src=["']/static/memory\.html""", SHELL_HTML) is None


# --- 2. the Preferences sheet's vocabulary ----------------------------------

def test_one_sheet_of_collapsible_sections_and_no_segmented_control():
    assert "wwk-seg" not in SHELL_CSS and "wwk-tab" not in SHELL_CSS, "no segmented control"
    head = _function("wwkSectionInnerHtml")
    assert 'class="prefs-row wwk-head"' in head, "a section head IS a Preferences row"
    assert '<span class="prefs-row-title">' in head and '<span class="prefs-row-sub">' in head
    assert 'aria-expanded="' in head and "RV_CHEVRON_SVG" in head
    assert "wwkToggle(t.getAttribute('data-section'))" in WWK, "tap to open in place"


@pytest.mark.parametrize(
    "key, title, line",
    [
        ("people", "Who’s here", "prefsPeopleLine"),
        ("wont-eat", "Won’t eat", "wwkWontEatLine"),
        ("rhythm", "Your rhythm", "prefsRhythmLine"),
        ("prep-days", "Prep days", "prefsPrepLine"),
        ("taste", "How you eat", "prefsEatingLine"),
        ("calendar", "Your calendar", "prefsCalendarLine"),
        ("stores", "Stores", "prefsStoresLine"),
    ],
)
def test_each_section_reads_its_line_with_the_preferences_rows_own_function(key, title, line):
    """Tapping through from a Preferences row never changes the words: the
    row and the section it opens call the same function."""
    assert f"{{ key: '{key}', title: '{title}', line: {line}, body: " in WWK


@pytest.mark.parametrize(
    "title, section",
    [
        ("Who’s here", "people"),
        ("Your rhythm", "rhythm"),
        ("Prep days", "prep-days"),
        ("How you eat", "taste"),
        ("Your calendar", "calendar"),
        ("Stores", "stores"),
    ],
)
def test_each_preferences_row_targets_its_section(title, section):
    rows = SHELL_JS[SHELL_JS.index("var PREFS_ROWS = ["):]
    rows = rows[: rows.index("];")]
    assert f"{{ title: '{title}', section: '{section}', line: " in rows
    sections = WWK[WWK.index("var WWK_SECTIONS = ["):]
    sections = sections[: sections.index("];")]
    assert f"key: '{section}'" in sections, f"no section {section} for the {title} row to open"


def test_opening_from_a_row_expands_that_section_alone_and_scrolls_to_it():
    opener = _function("openWhatWeKnow")
    assert "wwkState.openSections = {};" in opener
    assert "if (section && wwkSection(section)) wwkState.openSections[section] = true;" in opener
    assert "wwkState.scrollTo = section || null;" in opener
    assert "wwkScrollTo(wwkState.scrollTo);" in _function("renderWhatWeKnow")


def test_chips_are_the_apps_own_selectable_chip():
    """Surface + hairline at rest, celadon-tint when selected — the same
    recipe as .defrost-chip. No dotted borders anywhere."""
    assert ".wwk-chip.is-on {" in SHELL_CSS
    on = SHELL_CSS[SHELL_CSS.index(".wwk-chip.is-on {"):]
    on = on[: on.index("}")]
    assert "var(--celadon-tint)" in on and "var(--celadon-edge)" in on and "var(--ink-on-celadon)" in on
    wwk_css = SHELL_CSS[SHELL_CSS.index("What we know (native, 2026-09-12)"):SHELL_CSS.index('"Add from a link" (recipe import')]
    assert "dashed" not in wwk_css and "dotted" not in wwk_css
    assert re.search(r"#[0-9a-fA-F]{3,6}\b", wwk_css) is None, "every colour goes through a token (Rule 9)"
    assert "min-height: 44px" in SHELL_CSS[SHELL_CSS.index(".wwk-chip {"):SHELL_CSS.index(".wwk-chip:hover")]


def test_a_selected_age_group_reads_as_selected_whatever_case_it_was_stored_in():
    """The audit's bug: on the old page no age group ever looked selected.
    Real households carry "Adult" as well as "adult"."""
    people = _function("wwkPeopleHtml")
    assert "String(m.age_group || '').toLowerCase() === o.key ? 'on' : ''" in people


def test_copy_is_kitchen_table_not_form_labels():
    for label in ("Age group", "Dietary restrictions", "Eating style", "Not set yet"):
        assert f"'{label}'" not in WWK and f">{label}<" not in WWK, f"form label {label!r} survived"
    for lead in ("Never on the plate", "When dinner lands", "Plan ready by", "Who cooks",
                 "Meals eaten together", "Lunch, on a normal day", "Roughly how long",
                 "How meals lean", "Excited about", "Rounding out meals", "Each week I plan",
                 "In your kitchen", "Anything else", "Paste a whole list"):
        assert lead in WWK, f"lead-in {lead!r} missing"
    assert "Tap anything to change it. It saves as you go." in WWK, "the one line under the title"
    assert "Things I never suggest" in WWK, "an empty Won't eat says what it does, not 'Not set yet'"


# --- 3. autosave ------------------------------------------------------------

def test_every_change_saves_optimistically_and_reverts_on_failure():
    commit = _function("wwkCommit")
    assert "apply();" in commit and "redraw(sectionKey);" in commit
    assert commit.index("apply();") < commit.index("await request()"), "the row updates before the request goes"
    assert "prefsState.memory = before.memory;" in commit and "wwkState.facts = before.facts;" in commit
    assert "showToast('That didn’t save. Try it again.');" in commit
    assert "wwkFlashSaved(sectionKey);" in commit
    assert "if (seq === wwkState.seq" in commit, "a late reply never overwrites a later tap"


def test_there_is_no_save_button():
    assert ">Save<" not in WWK
    assert "wwk-save-btn" not in WWK and "wwk-save-btn" not in SHELL_CSS


def test_text_saves_on_blur_and_on_enter():
    wiring = _function("wireWhatWeKnow")
    assert "body.addEventListener('focusout'" in wiring
    assert "if (kind === 'eating_style') return wwkSaveEatingStyle(t.value.trim());" in wiring
    assert "if (kind === 'fact') return wwkUpdateFact(t.getAttribute('data-id'), t.value.trim());" in wiring
    assert "if (kind === 'add') return wwkCommitAdd(t);" in wiring
    assert "if (e.key === 'Enter' && t.tagName !== 'TEXTAREA') {" in wiring
    # A redraw's own blur is not the household leaving the field.
    assert "if (wwkRedrawing || !t.isConnected) return;" in wiring


def test_the_preferences_rows_stay_right_without_a_reread():
    """The two sheets share one cached /api/memory: a save here changes it,
    and Preferences re-renders from it."""
    assert "function wwkMem() { return prefsState.memory; }" in WWK
    assert "if (prefsState.open) renderPrefsRows();" in _function("wwkCommit")
    assert "if (wwkState.open) loadWhatWeKnow();" in _function("prefsInvalidate")


def test_the_calendar_read_is_shared_with_preferences():
    load = _function("loadWhatWeKnow")
    assert "loadPrefsCalendar()" in load
    assert "fetch('/api/memory')" in load and "fetch('/api/facts')" in load


# --- 4. the fact checklist ----------------------------------------------------
# Every fact static/memory.html could edit, and the control that edits it now.

CHECKLIST = {
    # People
    "age group": ("data-wwk=\"age\"", "/api/memory/member/age-group"),
    "restriction add": ("data-kind=\"restriction\"", "restrictions: [text], replace: false"),
    "restriction remove": ("data-wwk=\"restriction-remove\"", "restrictions: next, replace: true"),
    # Won't eat (household dislikes)
    "dislike add": ("data-kind=\"dislike\"", "wwkListAdd('wont-eat', 'dislikes', 'dislikes', text)"),
    "dislike remove": ("data-wwk=\"dislike-remove\"", "wwkListRemove('wont-eat', 'dislikes', 'dislikes', value)"),
    # Taste
    "eating style": ("data-wwk-input=\"eating_style\"", "wwkSavePreference('taste', 'eating_style'"),
    "cuisine add": ("data-kind=\"cuisine\"", "wwkListAdd('taste', 'cuisine_preferences', 'cuisine_preferences', text)"),
    "cuisine remove": ("data-wwk=\"cuisine-remove\"", "wwkListRemove('taste', 'cuisine_preferences', 'cuisine_preferences', value)"),
    "protein 3-state": ("data-wwk=\"protein\"", "function wwkCycleProtein(key)"),
    "complete plates": ("data-wwk=\"plates\"", "wwkSavePreference('taste', 'complete_plates'"),
    "dinners per week": ("field: 'dinners_per_week'", "data-wwk=\"count\""),
    "breakfasts per week": ("field: 'breakfasts_per_week'", "data-wwk=\"count\""),
    "lunches per week": ("field: 'lunches_per_week'", "data-wwk=\"count\""),
    "snacks a day": ("field: 'snacks_per_day'", "max: 6"),
    "kitchen kit": ("data-wwk=\"kit\"", "wwkSavePreference('taste', 'kitchen_kit'"),
    # Rhythm
    "lunch location": ("data-wwk=\"lunch\"", "lunch_location: {}"),
    "meals together": ("data-field=\"meals_together\"", "/api/onboarding/rhythm"),
    "cooking role": ("data-field=\"cooking_role\"", "wwkState.pendingCookWho = true;"),
    "cooking role who": ("data-wwk=\"cooking-who\"", "cooking_role: 'one_person', cooking_role_who: name"),
    "dinner window": ("data-field=\"dinner_window\"", "/api/onboarding/rhythm"),
    "planning anchor": ("data-field=\"planning_anchor\"", "/api/onboarding/rhythm"),
    "leftovers stance": ("data-field=\"leftovers_stance\"", "field === 'leftovers_stance' ? 'taste' : 'rhythm'"),
    "prep days": ("data-wwk=\"prep-day\"", "WWK_MAX_PREP_DAYS"),
    "prep minutes": ("data-wwk=\"prep-minutes\"", "current === picked ? null : picked"),
    # Calendar
    "calendar check": ("data-wwk=\"cal-check\"", "/api/calendar/check"),
    "calendar connect": ("data-wwk=\"cal-save\"", "/api/calendar/connect"),
    "calendar refresh": ("data-wwk=\"cal-refresh\"", "/api/calendar/refresh"),
    "calendar disconnect": ("data-wwk=\"cal-disconnect\"", "/api/calendar/disconnect"),
    # Stores
    "store add": ("data-kind=\"store\"", "wwkSavePreference('stores', 'usual_stores'"),
    "store item add": ("data-kind=\"store-item\"", "/api/memory/store-items/add"),
    "store item forget": ("data-wwk=\"item-forget\"", "/api/memory/store-items/remove"),
    "store item move": ("data-wwk=\"item-move\"", "function wwkMoveStoreItem(store, item)"),
    "store import list": ("data-wwk=\"import-save\"", "function wwkImportSave()"),
    # Freeform facts, all three categories
    "fact add": ("data-kind=\"fact\"", "/api/facts/add"),
    "fact edit": ("data-wwk-input=\"fact\"", "'/update'"),
    "fact delete": ("data-wwk=\"fact-delete\"", "'/delete'"),
}


@pytest.mark.parametrize("fact", sorted(CHECKLIST))
def test_every_fact_the_old_page_edited_is_still_editable(fact):
    control, save = CHECKLIST[fact]
    assert control in WWK, f"{fact}: no control ({control!r})"
    assert save in WWK, f"{fact}: no save path ({save!r})"


def test_the_freeform_facts_live_in_the_section_that_owns_their_category():
    assert "wwkFactsHtml('people')" in _function("wwkPeopleHtml")
    assert "wwkFactsHtml('rhythm')" in _function("wwkRhythmHtml")
    assert "wwkFactsHtml('taste')" in _function("wwkTasteHtml")


# --- 5. the lines, run for real -----------------------------------------------

def _prefs_block() -> str:
    start = SHELL_JS.index("function prefsPeopleLine(mem) {")
    end = SHELL_JS.index("];", SHELL_JS.index("var PREFS_ROWS = [")) + 2
    return SHELL_JS[start:end]


def _run(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def test_the_people_line_says_allergic_to_the_way_a_person_would():
    """"allergy: peanuts" is how onboarding stores it (the clash checker
    keys on the prefix); nobody says it that way. Older households store
    "allergic to pineapple" already, which passes through."""
    memory = {"members": [
        {"name": "Emily", "dietary_restrictions": ["allergy: pineapple", "allergic to kiwi"]},
        {"name": "Sam", "dietary_restrictions": ["Vegetarian"]},
    ]}
    out = _run(_prefs_block() + f"\nconsole.log(JSON.stringify(prefsPeopleLine({json.dumps(memory)})));")
    assert out == "Emily, Sam · allergic to pineapple, kiwi +1"


def test_protein_state_reads_ratings_and_the_older_more_less_shape():
    """A real household's stored answers (the 2026-09-11 backup) carry
    "more"/"less"/"neutral" under keys like "Fish / seafood" — the old page
    read none of them, so every chip sat unrated over an answer that was
    there. An exact lowercase key wins over a legacy one."""
    fn = _function("wwkProteinState")
    memory = {"protein_preferences": {
        "Chicken": "more", "Pork": "less", "Fish / seafood": "more", "Plant-based / tofu": "neutral",
        "fish": 1, "beans": 5, "eggs": 3,
    }}
    script = fn + f"\nconst MEM = {json.dumps(memory)};\n" + \
        "console.log(JSON.stringify(['chicken','pork','fish','tofu','beans','eggs','shrimp'].map(k => wwkProteinState(MEM, k))));"
    out = _run(script)
    assert [o["state"] for o in out] == ["on", "off", "off", "", "on", "", ""]
    assert out[2]["keys"] == ["fish", "Fish / seafood"], "clearing fish clears both stored keys"
    assert out[0]["keys"] == ["Chicken"]
