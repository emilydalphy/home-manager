"""Add a cuisine that isn't on your list — one pill, one screen, remembered.

Loop Board card (Emily, 2026-09-21), boards B2 / B4a / B4b. At the end of
the cuisine chips, a dashed apricot "+ Add one". Tapping it opens its own
screen in the intake motion — ‹ Back, "Add a cuisine", "Type it and I'll
remember it for next week too.", the field already focused — with a "Did
you mean" row as you type (Mex → Mexican · Tex-Mex · Mediterranean). The
button reads "Add Mexican" and does nothing until there's something to
add; "Never mind" goes back with nothing changed. On Add, the new cuisine
is the first chip on the mood screen and already chosen, with one green
line: "Mexican's in." Next week it is on the list
without being asked (What we know's cuisine_preferences), and the draft
honours it the way the existing ones are honoured.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import agent, tools
from app.tools import week_intake

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the page's own functions"
)


def _extract(name: str, source: str = PAGE) -> str:
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
    return source[start:j + 1]


def _var(name: str) -> str:
    m = re.search(rf"  var {name} = [\s\S]*?;\n", PAGE)
    assert m, name
    return m.group(0)


def _section(qid: str) -> str:
    start = PAGE.index(f'<section id="{qid}"')
    return PAGE[start:PAGE.index("</section>", start)]


def _node(script: str) -> str:
    prelude = (
        "function esc(s) { return String(s == null ? '' : s); }\n"
        + _var("CUISINE_COPY")
        + _extract("cuisineSuggestions") + "\n" + _extract("cleanCuisine") + "\n"
    )
    res = nodeharness.run_node(prelude + script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return res.stdout.strip().splitlines()[-1]


# ==========================================================================
# The pill and the screen
# ==========================================================================

class TestThePillAndTheScreen:
    def test_add_one_is_the_last_chip_dashed_apricot(self):
        render = _extract("renderCuisines")
        assert 'class="chip small add" id="cuisine-add"' in render
        assert render.index("data.cuisines.map(") < render.index('id="cuisine-add"')
        assert "$('cuisine-add').addEventListener('click', openCuisineScreen);" in render
        css = PAGE[PAGE.index("  .chip.add {"):PAGE.index("  .chip.add:hover")]
        assert "border: 1.5px dashed var(--apricot)" in css and "background: transparent" in css

    def test_the_screen_has_the_bones_and_the_words(self):
        q = _section("q-cuisine")
        assert "<h1>Add a cuisine</h1>" in q
        assert "Type it and I&rsquo;ll remember it for next week too." in q
        assert '<input type="text" class="field" id="cuisine-field" maxlength="40"' in q
        assert 'id="did-you-mean-label" hidden>Did you mean</p>' in q
        assert '<div class="chip-row" id="did-you-mean"></div>' in q
        # The field is the screen: focused on open, so the keyboard is up.
        assert "setTimeout(function () { $('cuisine-field').focus(); }, 0);" in _extract("openCuisineScreen")
        # Not a step: no eyebrow, no bars, and ‹ Back closes it.
        opener = _extract("openCuisineScreen")
        assert "$('progress').hidden = true;" in opener and "$('eyebrow').textContent = '';" in opener
        assert "if (cuisineOpen) closeCuisineScreen();" in _extract("crumbTap")
        # The field's type is 16px+ so a phone doesn't zoom on focus.
        field = PAGE[PAGE.index("  .field {"):PAGE.index("  .field:focus")]
        assert "font-size: 17px" in field

    def test_the_foot_is_add_what_they_typed_and_never_mind(self):
        assert "var CUISINE_COPY = {" in PAGE
        assert "add: 'Add', addWhat: 'Add a cuisine', never: 'Never mind'," in PAGE
        cta = _extract("paintCta")
        assert "$('cta').textContent = name ? CUISINE_COPY.add + ' ' + name : CUISINE_COPY.addWhat;" in cta
        assert "$('cta').disabled = !name || cuisineAdding;" in cta
        assert "$('cta').addEventListener('click', function () { if (cuisineOpen) addCuisine(); else advance(); });" in PAGE
        assert "$('skip').addEventListener('click', function () { if (cuisineOpen) closeCuisineScreen(); else advance(); });" in PAGE
        # Never mind and ‹ Back both go back with nothing changed.
        close = _extract("closeCuisineScreen")
        assert "fetch(" not in close and "answers" not in close and "showStep(4)" in close

    def test_the_green_line_under_the_chips(self):
        q4 = _section("q4")
        assert '<div class="covers" id="cuisine-added" hidden role="status">' in q4
        assert q4.index('id="cuisines"') < q4.index('id="cuisine-added"')
        assert "inLine: '’s in.'" in PAGE


# ==========================================================================
# Did you mean — the same three the server would offer
# ==========================================================================

@_needs_node
class TestDidYouMean:
    def test_mex_offers_the_mockups_three(self):
        known = json.dumps(week_intake.KNOWN_CUISINES)
        got = json.loads(_node(f"console.log(JSON.stringify(cuisineSuggestions('Mex', {known})));"))
        assert got == ["Mexican", "Tex-Mex", "Mediterranean"]
        assert got == week_intake.cuisine_suggestions("Mex")

    @pytest.mark.parametrize("typed", ["", "   ", "eth", "ETHIOPIAN", "Sri", "zzz", "Tex"])
    def test_the_screen_and_the_server_agree(self, typed):
        known = json.dumps(week_intake.KNOWN_CUISINES)
        got = json.loads(_node(f"console.log(JSON.stringify(cuisineSuggestions({json.dumps(typed)}, {known})));"))
        assert got == week_intake.cuisine_suggestions(typed)

    def test_free_text_that_matches_nothing_is_still_something_to_add(self):
        assert week_intake.cuisine_suggestions("Nan's cooking") == []
        got = json.loads(_node("console.log(JSON.stringify([cleanCuisine('  nan\\u2019s   cooking '), cleanCuisine(''), cleanCuisine('x'.repeat(60)).length]));"))
        assert got == ["Nan’s cooking", "", 40]

    def test_add_takes_the_new_cuisine_to_the_front_already_chosen(self):
        script = (
            "var data = { cuisines: ['Indian', 'Thai'] };\n"
            "var answers = { cuisines: ['Thai'] };\n"
            "var addedCuisines = [];\n"
            "var lines = {};\n"
            "var els = { 'cuisine-added-text': { textContent: '' }, 'cuisine-added': { hidden: true } };\n"
            "function $(id) { return els[id]; }\n"
            "function renderCuisines() { lines.rendered = data.cuisines.slice(); }\n"
            + _extract("takeAddedCuisine") + "\n"
            "takeAddedCuisine('Mexican', true);\n"
            "var first = { cuisines: data.cuisines.slice(), chosen: answers.cuisines.slice(), line: els['cuisine-added-text'].textContent, shown: !els['cuisine-added'].hidden };\n"
            "takeAddedCuisine('Thai', false);\n"
            "console.log(JSON.stringify({ first: first, second: { cuisines: data.cuisines, chosen: answers.cuisines, line: els['cuisine-added-text'].textContent, added: addedCuisines } }));\n"
        )
        got = json.loads(_node(script))
        assert got["first"] == {
            "cuisines": ["Mexican", "Indian", "Thai"], "chosen": ["Thai", "Mexican"],
            "line": "Mexican’s in.", "shown": True,
        }
        # One already on the list is ticked where it is, not added twice.
        assert got["second"]["cuisines"] == ["Mexican", "Indian", "Thai"]
        assert got["second"]["chosen"] == ["Thai", "Mexican"]
        assert got["second"]["line"] == "Thai’s already on your list — I’ve ticked it."
        assert got["second"]["added"] == ["Mexican", "Thai"]

    def test_the_fallback_write_back_keeps_what_was_added(self):
        # A household with no saved list yet writes its taps back on "Draft
        # my week"; a cuisine added this session goes with them even if its
        # chip was tapped off again — adding one joins the standing list.
        advance = _extract("advance")
        assert "var remembered = addedCuisines.concat(answers.cuisines)" in advance
        assert "body: JSON.stringify({ field: 'cuisine_preferences', value: remembered })" in advance


# ==========================================================================
# The server: remembered, and honoured by the draft
# ==========================================================================

def test_a_new_cuisine_goes_first_on_the_households_list():
    tools.set_household_meal_preferences(cuisine_preferences=["Indian", "Thai"], mark_complete=True)
    out = tools.add_household_cuisine("mexican")
    assert out == {"cuisine": "Mexican", "added": True, "cuisines": ["Mexican", "Indian", "Thai"]}
    prefill = tools.get_week_intake_prefill("2027-03-01")
    assert prefill["cuisines"] == ["Mexican", "Indian", "Thai"]
    assert prefill["cuisines_are_fallback"] is False
    assert prefill["known_cuisines"] == week_intake.KNOWN_CUISINES
    assert tools.get_household_memory()["cuisine_preferences"] == ["Mexican", "Indian", "Thai"]


def test_one_already_there_is_not_added_twice_and_keeps_its_spelling():
    tools.set_household_meal_preferences(cuisine_preferences=["Tex-mex", "Thai"], mark_complete=True)
    out = tools.add_household_cuisine("  TEX-MEX ")
    assert out == {"cuisine": "Tex-mex", "added": False, "cuisines": ["Tex-mex", "Thai"]}


def test_free_text_is_kept_as_typed_and_blanks_are_refused():
    out = tools.add_household_cuisine("nan’s  cooking")
    assert out["cuisine"] == "Nan’s cooking" and out["added"] is True
    assert tools.get_week_intake_prefill("2027-03-01")["cuisines"] == ["Nan’s cooking"]
    with pytest.raises(ValueError):
        tools.add_household_cuisine("   ")
    assert len(tools.add_household_cuisine("x" * 80)["cuisine"]) == week_intake.CUISINE_MAX_LENGTH


def test_the_route(signed_in):
    res = signed_in.post("/api/cuisines", json={"name": "Ethiopian"})
    assert res.status_code == 200, res.text
    assert res.json()["cuisine"] == "Ethiopian" and res.json()["added"] is True
    assert signed_in.post("/api/cuisines", json={"name": ""}).status_code == 400
    prefill = signed_in.get("/api/week/2027-03-01/intake?day_count=7").json()
    assert prefill["cuisines"][0] == "Ethiopian"


def test_the_draft_is_handed_the_cuisine_the_way_it_is_handed_the_others():
    week = "2027-03-01"
    tools.add_household_cuisine("Mexican")
    saved = tools.save_week_intake(week, moods=["Comfort food"], cuisines=["Mexican", "Thai"])
    ctx = agent._intake_generation_context(saved)
    assert ctx["cuisines"] == ["Mexican", "Thai"]
    # And the prompt names them as this week's ask, above the usual rotation.
    assert "`intake.cuisines` are what the household asked for THIS week" in Path(agent.__file__).read_text(encoding="utf-8")
