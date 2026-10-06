"""
Settings -> Who's here shows "Main person" beside them, and offers to move
it to another adult (2026-10-05).

The backend half of that card shipped first and is covered by
tests/test_onboarding_your_name.py (the route, the two refusal sentences,
the household scoping). This file is the rendering half: the label, who is
offered the control, and the write it makes.

RUN, not read, for `wwkPeopleHtml` — the whole point of this card is
whether a label appears for exactly one person and a control for the
others, which is behaviour over a payload rather than a marker in the
source. The write and the dispatch are source assertions, because there is
no DOM in this harness to click.

Measured against the base commit a0c5dc1 (the backend half, no
rendering): 8 of these 11 red, 3 green. Each docstring says which it
is and, where it is green either way, the mutation that pins it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests import nodeharness

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"
SHELL_JS = (STATIC / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (STATIC / "shell.css").read_text(encoding="utf-8")


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


def _people_block() -> str:
    """wwkPeopleHtml with the REAL functions it calls, not stubs.

    Deliberate (2026-10-05): the two things this file is about — which
    person gets the label, which gets the control — are built out of
    `wwkChip` and `escapeHtml`, so stubbing either would make every
    assertion here about the stub. The invite row and the facts block are
    stubbed to the empty string instead: they are other cards' surfaces,
    they read state this harness has no reason to build, and nothing
    asserted below is inside them.
    """
    ages = SHELL_JS.index("var WWK_AGE_GROUPS = ")
    return (
        _function("escapeHtml") + "\n"
        + SHELL_JS[ages : SHELL_JS.index("];", ages) + 2] + "\n"
        + _function("wwkChip") + "\n"
        + _function("wwkFactChip") + "\n"
        + _function("wwkAddChip") + "\n"
        + _function("wwkLead") + "\n"
        + _function("wwkNote") + "\n"
        # Ages (2026-10-06): the chips read wwkAgeKey, and a Child's row
        # carries "How old is [name]?" (wwkAgeHtml).
        + _function("wwkAgeKey") + "\n"
        + "var WWK_INFANT_UNDER_YEARS = 1;\n"
        + _function("wwkIsInfant") + "\n"
        + _function("wwkAgeHtml") + "\n"
        + _function("prefsRestrictionWords") + "\n"
        + "function inviteRowHtml() { return ''; }\n"
        + "function inviteAdultNamed() { return null; }\n"
        + "function inviteNewHtml() { return ''; }\n"
        + "function wwkFactsHtml() { return ''; }\n"
        + _function("wwkPeopleHtml") + "\n"
    )


def _render(memory: dict) -> str:
    script = _people_block() + (
        "console.log(JSON.stringify(wwkPeopleHtml(%s)));\n" % json.dumps(memory)
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


TWO_ADULTS_AND_A_CHILD = {
    "members": [
        {"id": 7, "name": "Emily", "age_group": "adult", "dietary_restrictions": [], "is_primary": True},
        {"id": 8, "name": "Vineeth", "age_group": "adult", "dietary_restrictions": [], "is_primary": False},
        {"id": 9, "name": "Reid", "age_group": "child", "dietary_restrictions": [], "is_primary": False},
    ]
}


def _person(html: str, name: str) -> str:
    """The one <div class="wwk-person"> block for this person."""
    blocks = html.split('<div class="wwk-person">')[1:]
    for b in blocks:
        if ">" + name + "<" in b or ">" + name + " <" in b:
            return b
    raise AssertionError(f"no block for {name!r} in {html[:400]}")


# --- 1. the label -----------------------------------------------------------

def test_the_main_person_is_named_as_such_and_nobody_else_is():
    """CATCH: `is_primary` has been on the wire since the backend half
    shipped and nothing rendered it, so Settings could not answer "whose
    household is this set up as"."""
    html = _render(TWO_ADULTS_AND_A_CHILD)
    assert html.count("Main person") == 1, "exactly one member carries it"
    assert "Main person" in _person(html, "Emily")
    assert "Main person" not in _person(html, "Vineeth")
    assert "Main person" not in _person(html, "Reid")


def test_a_household_with_nobody_primary_says_main_person_nowhere():
    """GUARD, measured green on the base commit for the uninteresting
    reason that nothing rendered the label there at all — pinned instead
    by the mutation that renders it unconditionally. A household with
    nobody on record carries no primary (primary_member_id resolves to
    None), and inventing one would be the app saying a thing that isn't
    true (Rule 8)."""
    html = _render({"members": [
        {"id": 3, "name": "Sam", "age_group": "adult", "dietary_restrictions": [], "is_primary": False},
    ]})
    assert "Main person" not in html


def test_the_label_is_the_design_systems_own_neutral_pill():
    """CATCH (the rule does not exist on the base commit) and also pinned
    by the mutation that gives it --celadon instead: a
    fact about who this is, not something to do — so .pill-neutral's
    celadon-tint, never an apricot and never a chip. Rule One: a
    --celadon FILL takes --on-accent-ink, which is why this one takes the
    tint and the pairing .pill-neutral already ships."""
    html = _render(TWO_ADULTS_AND_A_CHILD)
    assert 'class="pill pill-neutral wwk-person-main">Main person</span>' in html
    assert "<button" not in _person(html, "Emily").split("</p>")[0], "the label is not tappable"
    rule = SHELL_CSS[SHELL_CSS.index(".wwk-person-main {"):]
    rule = rule[: rule.index("}")]
    assert "--apricot" not in rule
    # `var(--celadon)` as a FILL would want --on-accent-ink (Rule One);
    # the tint .pill-neutral already carries is what this takes, so the
    # rule adds no background of its own at all. The first cut of this
    # assertion read "--celadon:" and so could not see
    # `background: var(--celadon)` — the mutation measured 0 red and the
    # assertion was the thing that was wrong (2026-10-05).
    assert "background" not in rule and "var(--celadon)" not in rule
    assert re.search(r"#[0-9a-fA-F]{3,6}\b", rule) is None, "every colour goes through a token (Rule 9)"


# --- 2. the control ---------------------------------------------------------

def test_moving_it_is_offered_to_the_other_adults_and_to_nobody_else():
    """CATCH: without this there is no way to move the main person from any
    screen — the route shipped with no caller."""
    html = _render(TWO_ADULTS_AND_A_CHILD)
    assert html.count('data-wwk="primary"') == 1
    assert 'data-wwk="primary" data-member-id="8"' in _person(html, "Vineeth")
    assert 'data-wwk="primary"' not in _person(html, "Emily"), "they already are"
    assert 'data-wwk="primary"' not in _person(html, "Reid"), "the server refuses a child"


def test_the_control_reads_an_older_households_capitalised_age_group():
    """GUARD on the claim, CATCH on the base commit (no control there at all); GUARD (pinned by dropping the .toLowerCase()): the column is
    freeform and older households carry "Adult" — the same reading the age
    chips beside it already do, or an older household's second adult is
    never offered the move."""
    html = _render({"members": [
        {"id": 1, "name": "Emily", "age_group": "Adult", "dietary_restrictions": [], "is_primary": True},
        {"id": 2, "name": "Vineeth", "age_group": "Adult", "dietary_restrictions": [], "is_primary": False},
    ]})
    assert 'data-wwk="primary" data-member-id="2"' in html


def test_the_control_is_addressed_by_id_and_not_by_name():
    """GUARD on the claim, CATCH on the base commit (no control there at all); GUARD (pinned by the mutation that sends data-member instead): a
    name is the only identity the rest of this app has for a person, and
    two people called Sam are indistinguishable to it — this is the one
    write where that would move the wrong person."""
    html = _render({"members": [
        {"id": 4, "name": "Sam", "age_group": "adult", "dietary_restrictions": [], "is_primary": True},
        {"id": 5, "name": "Sam", "age_group": "adult", "dietary_restrictions": [], "is_primary": False},
    ]})
    assert 'data-member-id="5"' in html
    assert html.count('data-wwk="primary"') == 1


def test_the_control_is_a_44px_chip_and_not_a_second_apricot():
    """GUARD on the claim, CATCH on the base commit (no control there at all); GUARD (pinned by the mutation that makes it a .wk-primary): the
    sheet's chips are 44px by their own rule and nothing in Settings is
    urgent (Rule 5, Rule 6)."""
    html = _render(TWO_ADULTS_AND_A_CHILD)
    row = _person(html, "Vineeth")
    assert 'class="wwk-chip" aria-pressed="false" data-wwk="primary"' in row
    chip = SHELL_CSS[SHELL_CSS.index(".wwk-chip {"):SHELL_CSS.index(".wwk-chip:hover")]
    assert "min-height: 44px" in chip


def test_the_control_is_not_an_answer_to_what_this_person_is():
    """GUARD (pinned by moving it inside the age group's own container):
    the age chips are a radio group labelled "Emily is" — "Make main
    person" is not one of the answers to that question, so it gets a row
    of its own."""
    row = _person(_render(TWO_ADULTS_AND_A_CHILD), "Vineeth")
    group = row[row.index('role="group"'):]
    group = group[: group.index("</div>")]
    assert 'data-wwk="primary"' not in group


# --- 3. the write -----------------------------------------------------------

def test_the_tap_posts_the_member_id_and_takes_the_servers_answer():
    """GUARD (pinned by emptying wwkSetPrimary's body): the route answers
    the whole memory payload like its two neighbours, so wwkAdoptMemory
    folds it in and the sheet does not patch its own copy."""
    fn = _function("wwkSetPrimary")
    assert "wwkCommit('people'" in fn
    assert "'/api/memory/primary-member', { member_id: parseInt(id, 10) }" in fn
    assert "wwkAdoptMemory" in fn
    assert "m.is_primary = String(m.id) === String(id);" in fn, "the flip is optimistic and exclusive"
    assert "case 'primary': return wwkSetPrimary(t.getAttribute('data-member-id'));" in SHELL_JS


def test_a_refusal_is_shown_in_the_servers_own_words():
    """CATCH: wwkPost throws a bare "<path> <status>", so wwkCommit's
    failure path would have shown the generic line over two sentences
    already written for a reader ("The main person needs to be one of the
    adults.")."""
    fn = _function("wwkPostSaying")
    assert "if (res.status === 400)" in fn
    assert "err.userMessage = detail;" in fn
    assert "wwkPostSaying('/api/memory/primary-member'" in _function("wwkSetPrimary")
    commit = _function("wwkCommit")
    assert "if (err && err.userMessage) showToast(err.userMessage);" in commit


def test_wwkPost_itself_is_left_exactly_as_it_was():
    """GUARD (pinned by folding the 400 branch into wwkPost instead): every
    other /api/memory/* caller keeps the behaviour it has today rather than
    starting to show whatever detail its route happens to raise."""
    assert "userMessage" not in _function("wwkPost")
