"""
"How you eat" reads the whole section back, not just leftovers and snacks.

Loop Board "Preferences: the 'How you eat' line should reflect what you
just changed" (2026-09-13). The row's sub-line named the leftovers stance
and the snack count and nothing else, so telling Pomona you love Thai food
saved correctly and left the row word-for-word as it was — which reads as
"it didn't take", and is exactly the doubt a read-back row exists to
settle.

Every test here RUNS static/shell.js's own `prefsEatingLine` under node
against a real /api/memory payload, rather than reading the source for a
marker: the bug was a line that didn't move, which is behaviour a marker
test cannot see.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests import nodeharness

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


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
                break
        j += 1
    return SHELL_JS[start : j + 1]


def _block() -> str:
    """The Preferences read-back region, plus the two things the eating line
    reaches out of it for: WWK_PROTEINS and wwkProteinState, which live with
    the section body they were written for."""
    start = SHELL_JS.index("function prefsPeopleLine(mem) {")
    end = SHELL_JS.index("];", SHELL_JS.index("var PREFS_ROWS = [")) + 2
    proteins = SHELL_JS.index("var WWK_PROTEINS = [")
    return (
        SHELL_JS[proteins : SHELL_JS.index("\n", proteins)]
        + "\n"
        + _function("wwkProteinState")
        + "\n"
        + SHELL_JS[start:end]
    )


def _line(memory: dict) -> str:
    """The words the "How you eat" row shows for one /api/memory payload."""
    script = (
        _block() + "\n"
        + f"console.log(JSON.stringify(prefsEatingLine({json.dumps(memory)})));\n"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# --- the report: a cuisine add has to move the line -----------------------

def test_adding_a_cuisine_changes_the_line(signed_in):
    """The card's own user story, driven through the real save path the
    sheet uses (POST /api/memory/edit with the whole list, which is what
    wwkListAdd sends) rather than against a hand-built payload."""
    before = _line(signed_in.get("/api/memory").json())
    assert "Thai" not in before

    signed_in.post("/api/memory/edit", json={
        "field": "cuisine_preferences", "value": ["Thai"],
    })
    after = _line(signed_in.get("/api/memory").json())
    assert after != before, "the row said the same thing after the save"
    assert "Thai" in after


def test_the_newest_cuisines_are_the_ones_named(signed_in):
    """Nothing in /api/memory is timestamped, so "what you just added" is
    read off the list's own order — wwkListAdd appends, so the tail is the
    most recent answer. Four cuisines: the last two by name, the rest as
    "+N"."""
    signed_in.post("/api/memory/edit", json={
        "field": "cuisine_preferences",
        "value": ["Italian", "Greek", "Thai", "Mexican"],
    })
    assert _line(signed_in.get("/api/memory").json()) == "Thai, Mexican +2"


def test_a_household_with_no_cuisines_gets_no_cuisine_words():
    """Nothing is padded. A part nobody has answered contributes nothing —
    not a filler phrase, and not "Not set yet" wedged mid-line."""
    assert _line({"cuisine_preferences": [], "rhythm": {"leftovers_stance": "love_them"}}) \
        == "leftovers welcome"
    assert _line({}) == "Not set yet"


# --- the rest of the section --------------------------------------------

def test_the_whole_section_reads_back_in_the_order_its_controls_run():
    """Leftovers, how meals lean, excited about, proteins, then the counts —
    the order wwkTasteHtml draws them, so the row and the screen it opens
    name things the same way round."""
    line = _line({
        "rhythm": {"leftovers_stance": "love_them"},
        "eating_style": "Keto",
        "cuisine_preferences": ["Thai"],
        "protein_preferences": {"fish": 1},
        "snacks_per_day": 0, "snacks_per_day_set": True,
    })
    assert line == "leftovers welcome · Keto · Thai · less fish · no snacks"


@pytest.mark.parametrize(
    "prefs, expected",
    [
        # The shape this sheet writes: a 1-5 rating under a lowercase key.
        ({"chicken": 5}, "more chicken"),
        ({"fish": 1}, "less fish"),
        ({"chicken": 3}, ""),  # neutral is not a leaning
        # The shape a real household's stored answers came in (2026-09-12,
        # the pre-reset backup): "more"/"less" under a fuller key.
        ({"Fish / seafood": "less"}, "less fish"),
        ({"Chicken": "more"}, "more chicken"),
        # Favourites before skips, and at most two of them.
        # A third leaning is counted, not silently dropped — the same "+N"
        # the cuisines use, and what lets the clause shrink under pressure
        # instead of being evicted.
        ({"beef": 5, "fish": 1, "chicken": 5}, "more chicken, more beef +1"),
    ],
)
def test_the_protein_leanings_are_read_the_way_the_chips_read_them(prefs, expected):
    """One protein reader for the row and for the section's chips
    (wwkProteinState), so the line can never claim a leaning the chips
    don't show — including for the older stored shapes."""
    line = _line({"protein_preferences": prefs})
    assert line == (expected or "Not set yet")


def test_the_eating_style_is_one_clause_of_the_households_own_words():
    """eating_style is ONE freeform value and onboarding joins every tapped
    preset and the typed line into it with commas. The row takes the first
    clause — a reminder, not a transcript — cased as it was typed."""
    assert _line({"eating_style": "High-protein, Low-carb, no red meat on weeknights"}) \
        == "High-protein"


def test_a_snacks_answer_still_reads_back(signed_in):
    """The two facts the line used to carry are still in it, and still only
    when the household actually answered (snacks_per_week is NOT NULL
    DEFAULT 3 — see tests/test_kitchen_and_preferences.py)."""
    assert _line(signed_in.get("/api/memory").json()) == "Not set yet"
    signed_in.post("/api/memory/edit", json={"field": "snacks_per_day", "value": 2})
    assert _line(signed_in.get("/api/memory").json()) == "2 snacks a day"


# --- it has to fit on the row --------------------------------------------

def test_what_gives_way_is_the_settled_answer_not_the_newest_one():
    """The first cut of this dropped whole answers off the END of the line,
    which is where the newest ones sit — so a household with two ordinary
    cuisine names lost the cuisine clause outright and adding one still
    moved nothing, the reported bug one layer down. The cuisines and the
    protein leanings are the last to go now; the settled answers give way."""
    line = _line({
        "rhythm": {"leftovers_stance": "fine_sometimes"},
        "eating_style": "Mediterranean",
        "cuisine_preferences": ["Italian", "Greek", "Vietnamese", "Ethiopian"],
        "protein_preferences": {"chicken": 5},
        "snacks_per_day": 2, "snacks_per_day_set": True,
    })
    assert len(line) <= 55, line
    assert line == "Vietnamese, Ethiopian +2 · more chicken…"
    # The part that blocks here is the LEFTOVERS stance, not the style:
    # cuisines (24) + proteins (12) is 39 used, and "leftovers now and then"
    # costs 25 more against a 54 working limit, so admission stops there and
    # the style is never reached. Fitting the style would mean shrinking the
    # cuisines to "Ethiopian +3" — trading the newest answer for a settled
    # one, which is the rule these two passes exist to enforce. 40 of 55 is
    # the deliberate price.


def test_tapping_a_second_protein_shrinks_that_clause_instead_of_losing_it():
    """The reported defect, one clause over. The protein clause had no way
    to shorten, so tapping Shrimp GREW it past the room left and it was
    evicted — the household changed one thing and watched a different thing
    happen. It gets the "+N" the cuisines have now, so it can shrink."""
    household = {
        "eating_style": "Whole foods",
        "cuisine_preferences": ["Middle Eastern", "Mediterranean"],
        "protein_preferences": {"chicken": 5},
    }
    before = _line(household)
    assert before == "Middle Eastern, Mediterranean · more chicken…"

    after = _line(dict(household, protein_preferences={"chicken": 5, "shrimp": 5}))
    assert after == "Middle Eastern, Mediterranean · more chicken +1…"
    assert after != before, "the tap has to show"
    assert "more chicken" in after, "the clause they changed is still there"
    assert "Whole foods" not in after, "an answer nobody touched has appeared"


def test_a_settled_answer_never_backfills_over_the_one_that_gave_way():
    """Admission is strict, not first-fit: when a part cannot fit even at
    its shortest, nothing of lower rank goes in behind it.

    Here the protein clause cannot fit at 'more chicken +1' (15), and
    'Keto' (4) can. First-fit puts the eating style in its place, which
    says the protein tap did something else; strict leaves the room unused
    and says nothing it cannot stand behind. Mutating the loop back to
    first-fit reddens this test and nothing else."""
    line = _line({
        "eating_style": "Keto",
        "cuisine_preferences": ["Thai", "Modern Australian", "Eastern European"],
        "protein_preferences": {"chicken": 5, "fish": 1},
    })
    assert line == "Modern Australian, Eastern European +1…"
    assert "Keto" not in line, "a rank-3 answer backfilled over a rank-1 one"


def test_two_ordinary_cuisine_names_survive_the_middle_leftovers_answer():
    """"leftovers now and then" is 22 characters and one of three ordinary
    onboarding answers; "Middle Eastern, Caribbean" is 25. Together with a
    preset eating style that is over the row, and the cuisines were the
    half that used to disappear."""
    line = _line({
        "rhythm": {"leftovers_stance": "fine_sometimes"},
        "eating_style": "High-protein",
        "cuisine_preferences": ["Middle Eastern", "Caribbean"],
    })
    assert line == "leftovers now and then · Middle Eastern, Caribbean…"


def test_adding_a_cuisine_moves_the_line_when_it_has_to_compete_for_room(signed_in):
    """The reviewer's own reproduction, driven through the real save path:
    a household whose leftovers stance and typed eating style already fill
    the row. test_adding_a_cuisine_changes_the_line starts from an empty
    household and cannot see this."""
    signed_in.post("/api/onboarding/rhythm", json={"leftovers_stance": "love_them"})
    signed_in.post("/api/memory/edit", json={
        "field": "eating_style", "value": "Mostly plant-based with fish twice a week",
    })
    before = _line(signed_in.get("/api/memory").json())

    signed_in.post("/api/memory/edit", json={"field": "cuisine_preferences", "value": ["Thai"]})
    after = _line(signed_in.get("/api/memory").json())
    assert after != before, "the row said the same thing after the save"
    assert "Thai" in after
    assert after == "leftovers welcome · Mostly plant-based… · Thai"


def test_a_typed_eating_style_is_read_back_whole_when_there_is_room():
    """The budget is a fallback, not a haircut everybody gets. A household
    whose one answer is a typed sentence sees their own words: 41 characters
    inside a 55-character row, so there is nothing to save room for."""
    assert _line({"eating_style": "Mostly plant-based with fish twice a week"}) \
        == "Mostly plant-based with fish twice a week"
    assert _line({"eating_style": "Budget-friendly"}) == "Budget-friendly"


def test_a_typed_eating_style_gets_a_budget_of_its_own_under_pressure():
    """It is the one answer here with no natural length. Left unbounded a
    typed sentence fills the row on its own and takes the cuisine somebody
    just added down with it — so under pressure it is cut on a word, and
    says so."""
    assert _line({
        "rhythm": {"leftovers_stance": "love_them"},
        "eating_style": "Mostly plant-based with fish twice a week",
        "cuisine_preferences": ["Thai"],
    }) == "leftovers welcome · Mostly plant-based… · Thai"


def test_a_cut_answer_is_never_given_a_second_ellipsis():
    """A leftovers stance, a typed style over the budget and an answered
    snacks question — a household straight out of onboarding who typed
    rather than tapped — used to read "…plant-based……". prefsCut's own
    rule is that an ellipsis reads as "more of this", and "……" reads as a
    typo."""
    line = _line({
        "rhythm": {"leftovers_stance": "fine_sometimes"},
        "eating_style": "Mostly plant-based with fish twice a week",
        "snacks_per_day": 2, "snacks_per_day_set": True,
    })
    assert "……" not in line
    assert line == "leftovers now and then · Mostly plant-based…"


def test_an_eating_style_stored_as_an_object_is_not_read_back_as_a_fact():
    """/api/memory/edit stores what it is handed. Not throwing is not the
    same as not inventing: String()ing an object would print
    "[object Object]" into the row as something the household said."""
    assert _line({"eating_style": {"a": 1}, "cuisine_preferences": ["Thai"]}) == "Thai"


def test_cutting_an_answer_never_splits_an_emoji_in_half():
    """prefsCut slices by UTF-16 index, so half an emoji is a replacement
    glyph in the middle of somebody's own words."""
    script = _block() + "\n" + (
        "const t = 'AAAAAAAAAAAAAAAAAAAA🥦🥦🥦🥦';\n"
        "const lone = /[\\uD800-\\uDBFF](?![\\uDC00-\\uDFFF])/;\n"
        "console.log(JSON.stringify(prefsStyleForms(t).map(f => lone.test(f))));\n"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    assert json.loads(res.stdout.strip()) == [False, False]


def test_one_answer_longer_than_the_whole_row_is_cut_rather_than_dropped():
    """The only case with nothing left to keep. A row reading just "…" would
    be worse than a cut one."""
    line = _line({"cuisine_preferences": ["A" * 90]})
    assert len(line) == 55 and line.endswith("…")


def test_the_answer_that_gets_cut_there_is_the_highest_RANKED_one():
    """...and telling which one that is needs a household with something
    else in it, or the branch reads the same either way.

    The parts are built in the section's order, so parts[0] is the leftovers
    stance while the rank-first part is the cuisine — cutting parts[0] would
    print a settled answer nobody asked about and drop the one that is
    actually too long. Reachable only with a single cuisine name over 54
    characters, which is why it went unnoticed; a test that cannot fail is
    worse than no test."""
    line = _line({
        "rhythm": {"leftovers_stance": "fine_sometimes"},
        "cuisine_preferences": ["Modern Australian and Pacific Rim with a Mediterranean lean"],
    })
    assert line == "Modern Australian and Pacific Rim with a…"
    assert "leftovers" not in line, "the section-order-first answer was cut, not the ranked one"


def test_a_short_answer_is_not_given_an_ellipsis_it_doesnt_need():
    assert _line({"cuisine_preferences": ["Thai"]}) == "Thai"


# --- a wrong-typed answer may not brick the sheet -------------------------

def test_a_cuisine_field_stored_as_a_bare_string_still_renders_every_row():
    """Defense in depth, now that both write paths (`/api/memory/edit` and
    the chat tool of the same name) refuse or coerce a bare string instead
    of storing one, and `/api/memory` itself normalises one on the way out
    (see app/tools/memory.py's _coerce_str_list/_as_str_list, Loop Board
    2026-09-13 — this test used to hit that bug end to end through the live
    route; it's fixed there now, see tests/test_memory_field_types.py).

    A row written before either guard existed can still reach this sheet
    with cuisine_preferences (or any list-valued field) as a bare string,
    so the frontend's own tolerance stays worth pinning on its own: this
    line is a PREFS_ROWS line function, and a throw in it is not one
    section failing to draw — it is every row in the Preferences sheet
    stuck on "Reading it back…" forever."""
    memory = {"cuisine_preferences": "Thai"}  # what a pre-fix row can still hold

    script = (
        _block() + "\n"
        + f"const MEM = {json.dumps(memory)};\n"
        + "const out = {};\n"
        + "PREFS_ROWS.forEach(function (r) { out[r.title] = r.line(MEM); });\n"
        + "console.log(JSON.stringify(out));\n"
    )
    run = nodeharness.run_node(script, timeout=30)
    assert run.returncode == 0, f"the Preferences sheet threw: {run.stderr}"
    lines = json.loads(run.stdout.strip())
    # Not a guess at what they meant: a shape this row cannot read is a
    # shape it says nothing about.
    assert lines["How you eat"] == "Not set yet"
    assert lines["Who\u2019s here"] and lines["Stores"]


# --- one helper, two places ----------------------------------------------

def test_the_section_head_and_the_preferences_row_say_the_same_words():
    """AC 2: What we know's "How you eat" head reads its line with the
    Preferences row's own function, so tapping through never changes the
    words. Run rather than grepped — both entries are resolved and called,
    and the two are asserted to be the SAME function object."""
    memory = {
        "rhythm": {"leftovers_stance": "love_them"},
        "cuisine_preferences": ["Thai", "Mexican"],
        "protein_preferences": {"fish": 1},
    }
    sections = SHELL_JS.index("var WWK_SECTIONS = [")
    sections = SHELL_JS[sections : SHELL_JS.index("];", sections) + 2]
    bodies = "".join(
        f"function {name}() {{ return ''; }}\n"
        for name in ("wwkHoldingHtml", "wwkPeopleHtml", "wwkWontEatHtml", "wwkRhythmHtml",
                     "wwkPrepDaysHtml", "wwkTasteHtml", "wwkCalendarHtml",
                     "wwkStoresHtml")
    )
    script = (
        _block() + "\n"
        + _function("wwkWontEatLine") + "\n"
        + bodies
        + sections + "\n"
        + f"const MEM = {json.dumps(memory)};\n"
        + "const row = PREFS_ROWS.filter(r => r.section === 'taste')[0];\n"
        + "const sec = WWK_SECTIONS.filter(s => s.key === 'taste')[0];\n"
        + "console.log(JSON.stringify([row.line(MEM), sec.line(MEM), row.line === sec.line]));\n"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    row_line, section_line, same = json.loads(res.stdout.strip())
    assert row_line == section_line == "leftovers welcome · Thai, Mexican · less fish"
    assert same, "the row and the section head no longer share one function"
