"""
Onboarding asks "What's your name?" first, then "Who else lives with you?".

Loop Board card 3f01f4c0523181e1a422dd10877b1fa6 (High, Phase 1 — Beta),
Emily's approved mockup of 2026-10-04 (4FyLE2jHTK99qWzCtqHeDL#onboard,
first two phones). The household step is two screens, and the household
has a MAIN PERSON.

WHAT THIS REPLACES, which is the whole of why there is a new field. The
first question was "Who are we planning for?" — a list of names — and from
that moment `members[0]` WAS SILENTLY THE USER. Two readers leaned on it:
`helperAdults` built "does anyone else help run the house?" as
`members.slice(1)`, i.e. everyone but whichever row happened to be first,
and `record_setup_adult` takes the first ADULT among the members onboarding
saved (and the route pins this device's session cookie to whatever it
returns). Neither had anywhere to read "who is the main person" FROM. The
order of a list was doing a field's job.

So: `households.primary_member_id`, an id, resolved and moved by
app/tools/primary_member.py. Four things about it are worth being slow
about, and each has its own section below.

  A — the two screens. The copy, the one box, the pinned "You" row with
      its editable age chip and no remove control, ONE blank row rather
      than two, and the one apricot.
  B — nothing is written until setup finishes. Screen 1's Next does not
      post, and changing the name prunes every answer keyed by it. That is
      the 2026-09-09 rule (`add_member` is get-or-create by name and
      NOTHING in this app deletes a member, so a name typed, corrected and
      continued past used to leave a household holding the same person
      twice) plus the 2026-09-04 one (a rename could hand one person's
      allergy to another, which this app treats as a safety bug). The
      pinned You row makes the rename path MORE reachable, not less: going
      back one screen and retyping is now the only way to change it.
  C — the field. Set from screen 1, an ID and never a name, and an
      existing household resolving the card's way.
  D — "Just me", and the helpers step listing only the others.

WHY THE PRIMARY IS NOT `households.set_up_by_member_id`, which already
exists and looks like the same thing: that one is WHO SET THE HOUSEHOLD
UP, read by the first-open welcome to say "Emily's set up your household",
and its own module says in as many words that "a second pass through
onboarding never moves it" — the welcome's correctness rests on it being a
fact about the past. The card asks for the main person to be MOVED from
Settings. A field whose whole job is not moving cannot be the one that
moves. Two patterns answering two different questions is correct; one name
serving both is not.

FIFTEEN MUTATIONS RUN; FOURTEEN BITE. Red counts read off the runs, over
this file plus the four flow-order files hazard 3 names (control: 179
passed). The first nine are the ones the card asked to be sure of.

  1. the primary not set from screen 1 (the `record_primary_member` call
     taken out of the household route) -- 5 red
  2. the pinned You row removable (addMemberRow's x rendered on it
     too) -- 1
  3. the helpers step including the primary (`helperAdults` back to the
     `.slice(1)` this card replaces) -- 1
  4. "Just me" leaving the other rows, so a one-person household saves
     two members -- 3
  5. screen 1 posting the name early (`your-name-next` calling
     `saveHouseholdMembers`) -- 3
  6. `record_primary_member` overwriting a non-NULL column, so a second
     pass through setup MOVES the main person -- 1
  7. the lazy resolve re-POINTING the column instead of only filling
     it -- 1
  8. `is_primary` dropped from /api/memory's members -- 3
  9. the rename not pruning (`pruneMemberKeyedAnswers` off the name
     box), so one person's allergy transfers to another -- 1

And six more, four of them on the module's own rules:

 10. the db.py backfill removed -- 0, see below
 11. the lazy resolve removed, so an existing household answers
     None -- 9
 12. the route reading `saved_members[0]` instead of the name -- the
     implicit convention this card replaces -- 1
 13. `set_primary_member` dropping its adult/eats-here check -- 2
 14. `set_primary_member` dropping its household filter -- 1
 15. `_EATS_HERE_SQL` neutered, so a helper who does not eat here can be
     the main person -- 1

FOUR OF THOSE FOUND REAL BUGS OR REAL GAPS RATHER THAN CONFIRMING THE
CODE, which is the reason to run them rather than reason about them:

  * Mutation 15 reddened NOTHING on the first run. The eats_here clause
    is in all four of this module's statements and was pinned by none of
    them, so a nanny could have been resolved into the job by being the
    first adult on file. test_a_helper_who_does_not_eat_here_is_never_
    the_main_person is that gap closed; it bites at 1, and takes
    mutation 13 from 1 red to 2.
  * Mutation 2 originally passed. The pinned row IS built by a different
    function from `addMemberRow`, so "no remove control" was true by
    construction and nothing asserted it; a later hand giving that row
    addMemberRow's markup would not have been caught.
    test_the_pinned_you_row_has_no_remove_control reads the row the page
    actually builds.
  * Mutation 6 was first written against the wrong one of two
    near-identical UPDATEs (`replace(..., 1)` hit the lazy resolve's),
    so it reddened a test about reading past a stale primary and left
    the second-pass test green -- which looked like the second-pass test
    being toothless and was the mutation missing its target. They are
    two separate rules and are now two separate mutations, 6 and 7, each
    reddening exactly the test named for it.
  * Mutation 10 reddens nothing because `primary_member_id()` resolves
    lazily as well and the two are in SERIES. That is belt-and-braces
    rather than a weak test, and both halves are worth keeping -- the
    backfill answers for a household nothing has read yet, the lazy
    resolve for one made between two startups (create_household.py, or a
    test). The mutation that bites is 11, which removes the lazy half.
"""
from __future__ import annotations

import json
import re

import pytest

from app import tools
from app.db import get_conn
import tests.test_onboarding_go_back as _go_back

ONBOARDING = _go_back.ONBOARDING
_fn, _const = _go_back._fn, _go_back._const
_step_markup, _run, _needs_node = _go_back._step_markup, _go_back._run, _go_back._needs_node

NAME_MARKUP = _step_markup("step-your-name")
HOUSEHOLD_MARKUP = _step_markup("step-household")

# The card's own words for screen 1's line, verbatim here so a rewrite is
# a decision somebody makes rather than a drift.
THE_LINE = "You’ll be the main person for the home. You can add others next."


def _members_harness(name: str = "Emily", extra: str = "") -> str:
    """
    The member rows and the main person, running the PAGE's own functions
    against the go_back DOM stub. What is being pinned is behaviour —
    which rows exist, what currentMembers() answers, what a rename prunes
    — none of which a source-marker test can see.
    """
    return "\n".join([
        _go_back._DOM_STUB,
        """
ELS['members'] = makeEl('div');
ELS['your-name-input'] = makeEl('input');
ELS['your-name-input'].value = %s;
ELS['add-member'] = makeEl('button');
// The three controls the lifted wiring below assigns onclick onto.
ELS['your-name-next'] = makeEl('button');
ELS['household-next'] = makeEl('button');
ELS['household-just-me'] = makeEl('button');
ELS['household-empty'] = makeEl('p'); ELS['household-empty'].hidden = true;
ELS['your-name-empty'] = makeEl('p'); ELS['your-name-empty'].hidden = true;
const membersDiv = ELS['members'];
function escapeHtmlLocal(s) { return String(s === undefined || s === null ? '' : s); }
// Keyed-by-name answers, so a rename can be watched pruning them. The
// page's own two pruners are lifted below.
var restrictionAnswers = {};
var helperPicks = [];
var helperContacts = {};
var helperSomeoneName = '';
function goForward() {}
""" % json.dumps(name),
        _const("AGE_GROUP_OPTIONS"),
        _const("HELPER_SOMEONE"),
        _const("HELPER_ME"),
        _fn("buildSingleSelectChips"),
        _fn("renderMemberAgeChips"),
        _fn("addMemberRow"),
        _fn("primaryMemberName"),
        "var primaryAgeGroup = 'adult';",
        _fn("buildHouseholdStep"),
        _fn("currentMembers"),
        _fn("pruneRestrictionAnswers"),
        _fn("helperAdults"),
        _fn("helperOptions"),
        _fn("pruneHelperAnswers"),
        _fn("pruneMemberKeyedAnswers"),
        _fn("showHouseholdEmptyNote"),
        _fn("showYourNameEmptyNote"),
        # The page's own wiring for the three controls this file drives,
        # lifted rather than restated: half of what is pinned here is what
        # a tap does, and a hand-written copy would be free to be wrong.
        _wiring_block := _go_back.ONBOARDING[
            _go_back.ONBOARDING.index("document.getElementById('your-name-next').onclick"):
            _go_back.ONBOARDING.index("document.getElementById('helpers-next').onclick")
        ],
        # One pre-added blank row, exactly as the page adds it.
        "addMemberRow('', 'adult');",
        extra,
    ])


def _seed(members):
    """A household with these (name, age_group) people, in that order."""
    for name, age in members:
        tools.add_member(name)
        tools.set_member_age_group(name, age)


def _row(sql, *args):
    conn = get_conn()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


# ------------------------------------------------- A: the two screens


def test_screen_one_asks_the_cards_question_in_the_cards_words():
    """CATCH."""
    assert '<h1 class="q-title">What&rsquo;s your name?</h1>' in NAME_MARKUP


def test_screen_one_says_you_will_be_the_main_person():
    """
    CATCH. The card's line, verbatim — it is the only thing on that screen
    that says what typing your own name here MEANS.
    """
    line = re.search(r'<p class="q-line">(.*?)</p>', NAME_MARKUP, re.S)
    assert line, "screen 1 has no line under its title"
    said = line.group(1).replace("&rsquo;", "’").replace("&ll", "ll").strip()
    assert said == THE_LINE, said


def test_screen_one_is_one_text_field_and_its_button_says_next():
    """CATCH. One box, one apricot, and the card's own button word."""
    assert '<input type="text" id="your-name-input"' in NAME_MARKUP
    assert NAME_MARKUP.count('<input') == 1, "screen 1 asks for more than one thing"
    assert '<button class="btn-primary q-next" id="your-name-next" type="button">Next</button>' in NAME_MARKUP
    assert NAME_MARKUP.count("btn-primary") == 1


def test_screen_two_asks_who_else_lives_with_you():
    """
    CATCH. The title is the only copy that changed on this screen — Emily
    confirmed on 2026-10-04 that it otherwise reuses the old one exactly.
    """
    assert '<h1 class="q-title">Who else lives with you?</h1>' in HOUSEHOLD_MARKUP
    assert "Who are we planning for?" not in ONBOARDING, "the old title came back"


def test_screen_two_keeps_the_same_rows_and_renames_the_add_control():
    """
    GUARD, pinned by mutation 3 and 4. The card is explicit that this is
    the SAME screen: the existing member rows (#members, addMemberRow's
    name box plus the four age chips) and an add control, whose words are
    "+ Add someone" now.
    """
    assert '<div id="members"></div>' in HOUSEHOLD_MARKUP
    assert '<button class="add-btn" id="add-member" type="button">+ Add someone</button>' in HOUSEHOLD_MARKUP
    assert "+ Add person" not in ONBOARDING, "the old add control's words came back"
    # The row itself is untouched: the same name box and the same four chips.
    assert 'class="member-name" placeholder="Name"' in ONBOARDING
    for label in ("Adult", "Teen", "Child", "Little one"):
        assert f"label: '{label}'" in ONBOARDING


def test_screen_two_has_one_blank_row_not_two():
    """
    CATCH. The household's first adult is the pinned You row now, so a
    second pre-added Adult row would be guessing at a second one.
    """
    assert ONBOARDING.count("addMemberRow('', 'adult');") == 1, (
        "two pre-added rows, or none"
    )


def test_screen_twos_two_buttons_are_next_and_just_me_with_one_apricot():
    """
    CATCH. The card asks for both buttons. Rule 5 forbids a second
    APRICOT, not a second button — "Just me" is the sand .btn-soft above
    the one .btn-primary, the same call the shell's own .dock-secondary
    made.
    """
    assert '<button class="btn-soft" id="household-just-me" type="button">Just me</button>' in HOUSEHOLD_MARKUP
    assert '<button class="btn-primary q-next" id="household-next" type="button">Next</button>' in HOUSEHOLD_MARKUP
    assert HOUSEHOLD_MARKUP.count("btn-primary") == 1, "two apricots on one screen"
    assert "background: var(--sand)" in ONBOARDING.split(".btn-soft {")[1].split("}")[0]


def test_the_two_screens_use_tokens_only():
    """
    GUARD. Rule 9 — no literal colour in anything added here. Both new
    rules are read whole rather than grepped for a hash across the file.
    """
    for cls in (".btn-soft {", ".member-you-badge {", ".member-you-name {"):
        block = ONBOARDING.split(cls)[1].split("}")[0]
        assert "#" not in block, f"{cls} carries a literal colour"
        assert "var(--" in block


@_needs_node
def test_the_pinned_you_row_is_the_first_row_and_carries_the_name_and_a_you_badge():
    """
    CATCH. The card: the list "starts with the person from screen 1,
    pinned at the top with a 'You' badge". First MATTERS beyond the
    drawing — currentMembers() reads the rows in order, the route saves
    them in that order, and record_setup_adult takes the first adult of
    them, which is what pins this device's session to the main person.
    """
    out = _run(_members_harness(name="Emily") + """
buildHouseholdStep();
const rows = [...membersDiv.querySelectorAll('.member-block')];
console.log(JSON.stringify({
  first: rows[0]._classes.has('is-primary'),
  count: rows.length,
  name: rows[0].querySelector('.member-you-name').textContent,
  badge: rows[0].querySelector('.member-you-badge') !== null,
  members: currentMembers()
}));
""")
    assert out["first"] is True, "the You row is not the first row"
    assert out["count"] == 2, "the You row plus the one blank row"
    assert out["name"] == "Emily"
    assert out["badge"] is True
    assert out["members"] == [{"name": "Emily", "age_group": "adult"}]


@_needs_node
def test_the_pinned_you_row_has_no_remove_control():
    """
    CATCH (mutation 2). The card: "not removable". Read off the row the
    page actually builds rather than trusted to be true because a
    different function builds it — a later hand giving the pinned row
    addMemberRow's own markup is exactly what this is for.
    """
    out = _run(_members_harness() + """
buildHouseholdStep();
const rows = [...membersDiv.querySelectorAll('.member-block')];
console.log(JSON.stringify({
  youRemove: rows[0].querySelector('.remove-btn') !== null,
  otherRemove: rows[1].querySelector('.remove-btn') !== null
}));
""")
    assert out["youRemove"] is False, "the main person can be removed"
    # The others still can be — the control is not gone from the screen.
    assert out["otherRemove"] is True


@_needs_node
def test_the_pinned_you_rows_age_chip_is_editable_and_the_edit_sticks():
    """
    CATCH. The card asks for the age chip to be editable. "Sticks" is the
    half worth testing: the row is REDRAWN on every arrival at the step
    (STEP_BUILDERS), so an answer held on the row itself would be
    forgotten by the next arrival.
    """
    out = _run(_members_harness() + """
buildHouseholdStep();
const you = membersDiv.querySelector('.member-block.is-primary');
const chips = [...you.querySelectorAll('.rhythm-chip')];
const labels = chips.map(c => c.textContent);
chips.filter(c => c.textContent === 'Teen')[0].click();
const afterTap = currentMembers()[0].age_group;
buildHouseholdStep();   // arriving again
const afterRedraw = currentMembers()[0].age_group;
console.log(JSON.stringify({ labels: labels, afterTap: afterTap, afterRedraw: afterRedraw }));
""")
    assert out["labels"] == ["Adult", "Teen", "Child", "Little one"], (
        "the You row doesn't carry the ordinary age chips"
    )
    assert out["afterTap"] == "teen", "the chip isn't editable"
    assert out["afterRedraw"] == "teen", "the edit was forgotten by the next arrival"


# --------------------------------------- B: nothing is written until the end


def test_screen_ones_next_does_not_post_the_name():
    """
    CATCH (mutation 5). The 2026-09-09 rule: nothing reaches the household
    until setup finishes, because `add_member` is get-or-create BY NAME and
    nothing in this app deletes a member — so a name typed, corrected and
    continued past used to leave a household holding the same person twice
    with no way to take one out. Screen 1 is the single most likely place
    to break it, because a name is exactly the sort of thing a screen
    wants to save as you leave it.
    """
    wiring = ONBOARDING.split("document.getElementById('your-name-next').onclick")[1]
    wiring = wiring.split("document.getElementById('household-next').onclick")[0]
    assert "saveHouseholdMembers" not in wiring
    assert "Api.fetch" not in wiring and "fetch(" not in wiring
    assert "finishSetupAndReveal" not in wiring
    # And the name travels with the one write that does happen.
    assert "primary_name: primaryMemberName()" in ONBOARDING


@_needs_node
def test_changing_the_name_carries_through_to_the_pinned_row():
    """
    CATCH. Going back from screen 2, changing the name, coming forward
    again. This is why "Who else lives with you?" is in STEP_BUILDERS
    where the old household step was not: the others' rows hold their own
    text, but the pinned row is DRAWN from the name typed on the screen
    before, so arriving has to redraw it.
    """
    out = _run(_members_harness(name="Jamie") + """
buildHouseholdStep();
const before = currentMembers()[0].name;
// Back to screen 1, correct the name, forward again.
ELS['your-name-input'].value = 'James';
buildHouseholdStep();
const after = currentMembers()[0].name;
const rows = [...membersDiv.querySelectorAll('.member-block')].length;
console.log(JSON.stringify({ before: before, after: after, rows: rows }));
""")
    assert out["before"] == "Jamie"
    assert out["after"] == "James", "the corrected name didn't carry through"
    assert out["rows"] == 2, "the redraw added a second You row"


@_needs_node
def test_changing_the_name_orphans_no_answer_keyed_by_it():
    """
    CATCH (mutation 9), and the one in here that is a SAFETY test rather
    than a tidiness one. Restrictions and the helpers picks are keyed by a
    member NAME, and the 2026-09-04 allergy work is why that matters: a
    rename that leaves the old key behind can hand one person's allergy to
    another, and this app treats a wrong allergy attribution as a safety
    bug rather than an untidiness.

    The pinned You row makes this path MORE reachable than it was, not
    less — going back one screen and retyping is now the whole of the
    rename path, where before it was one box among several.
    """
    out = _run(_members_harness(name="Jamie") + """
buildHouseholdStep();
restrictionAnswers['Jamie'] = ['allergy: peanuts'];
helperPicks = ['adult:Greg'];
// Greg is on the list too, so his pick is a real one that must survive.
addMemberRow('Greg', 'adult');
const nameBox = ELS['your-name-input'];
nameBox.value = 'James';
nameBox._listeners.input.forEach(function (fn) { fn(); });
buildHouseholdStep();
console.log(JSON.stringify({
  restrictions: Object.keys(restrictionAnswers),
  helpers: helperPicks,
  members: currentMembers().map(m => m.name)
}));
""")
    assert out["restrictions"] == [], "Jamie's allergy outlived Jamie"
    assert out["helpers"] == ["adult:Greg"], "an unrelated helper pick was pruned"
    assert out["members"] == ["James", "Greg"]


@_needs_node
def test_a_rename_onto_someone_elses_name_does_not_inherit_their_allergy():
    """
    CATCH. The sharp version of the test above, and the shape the
    2026-09-09 entry describes: two people, the second's answer on file,
    the second removed, and the MAIN PERSON renamed to their name. The
    prune has to have happened on the EDIT, while the name was still gone
    — a prune that only ran when the restrictions step was next drawn
    would find a "Sam" in the household by then and hand Sam's allergy to
    the main person.
    """
    out = _run(_members_harness(name="Alex") + """
buildHouseholdStep();
addMemberRow('Sam', 'adult');
restrictionAnswers['Sam'] = ['allergy: peanuts'];
// Sam goes.
const samRow = [...membersDiv.querySelectorAll('.member-block')]
  .filter(b => !b._classes.has('is-primary') && b.querySelector('.member-name').value === 'Sam')[0];
samRow.querySelector('.remove-btn').onclick();
const afterRemove = Object.keys(restrictionAnswers);
// The main person takes the name.
const nameBox = ELS['your-name-input'];
nameBox.value = 'Sam';
nameBox._listeners.input.forEach(function (fn) { fn(); });
buildHouseholdStep();
console.log(JSON.stringify({
  afterRemove: afterRemove,
  afterRename: restrictionAnswers['Sam'] || null,
  members: currentMembers().map(m => m.name)
}));
""")
    assert out["afterRemove"] == [], "removing Sam left Sam's allergy behind"
    assert out["afterRename"] is None, "the main person inherited Sam's allergy"
    assert out["members"] == ["Sam"]


@_needs_node
def test_screen_ones_next_refuses_an_empty_name_in_a_line_on_the_step():
    """
    CATCH. DESIGN_SYSTEM §8: a problem is stated plainly on the step that
    can fix it, not in an alert that covers the box it is about.
    """
    out = _run(_members_harness(name="") + """
const moved = [];
goForward = function (k) { moved.push(k); };
ELS['your-name-next'].click();
const blocked = { moved: moved.slice(), note: !ELS['your-name-empty'].hidden };
ELS['your-name-input'].value = 'Emily';
ELS['your-name-next'].click();
console.log(JSON.stringify({ blocked: blocked, moved: moved, note: !ELS['your-name-empty'].hidden }));
""")
    assert out["blocked"]["moved"] == [], "an empty name went forward"
    assert out["blocked"]["note"] is True, "nothing said why"
    assert out["moved"] == ["your-name"], "a name didn't go forward"
    assert out["note"] is False, "the line stayed up after it was fixed"
    assert "alert(" not in ONBOARDING.split("'your-name-next').onclick")[1].split("};")[0]


# ------------------------------------------------------------ C: the field


def test_the_primary_is_set_from_screen_one(signed_in):
    """
    CATCH (mutation 1), and the card's first named test. The name typed on
    screen 1 is first in the members the route saves, and the household's
    primary_member_id is THAT member — resolved by name among the members
    this request saved, and stored as the id.
    """
    res = signed_in.post("/api/onboarding/household", json={
        "members": [
            {"name": "Emily", "age_group": "adult"},
            {"name": "Vineeth", "age_group": "adult"},
            {"name": "Reid", "age_group": "child"},
        ],
        "pets": [], "goals": "", "primary_name": "Emily",
    })
    assert res.status_code == 200, res.text
    emily = _row("SELECT id FROM members WHERE name = 'Emily'")
    assert _row("SELECT primary_member_id FROM households WHERE id = 1")[0] == emily["id"]


def test_the_primary_is_the_one_named_not_whichever_row_came_first(signed_in):
    """
    CATCH (mutation 1, and the point of the whole card). `members[0]` was
    an implicit convention; the field is a real answer. So a request that
    names the SECOND member as the main person is believed — which is the
    only way to tell a stored field from the old positional reading.
    """
    res = signed_in.post("/api/onboarding/household", json={
        "members": [
            {"name": "Vineeth", "age_group": "adult"},
            {"name": "Emily", "age_group": "adult"},
        ],
        "pets": [], "goals": "", "primary_name": "Emily",
    })
    assert res.status_code == 200, res.text
    emily = _row("SELECT id FROM members WHERE name = 'Emily'")
    assert _row("SELECT primary_member_id FROM households WHERE id = 1")[0] == emily["id"]


def test_the_primary_is_stored_as_an_id_and_not_a_name(signed_in):
    """
    CATCH (mutation 6). A member's only identity in this app is their NAME
    and two people called Sam are indistinguishable to the backend — a
    known limit with its own Decision log entry. A name-keyed primary
    would inherit it AND would move the moment somebody was renamed, so
    the column holds an id. Pinned by reading the column's type as well as
    its value: a name in an INTEGER column reads back as 0 or None rather
    than failing loudly.
    """
    signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Emily", "age_group": "adult"}],
        "pets": [], "goals": "", "primary_name": "Emily",
    })
    stored = _row("SELECT primary_member_id FROM households WHERE id = 1")[0]
    assert isinstance(stored, int), f"the primary is stored as {stored!r}"
    assert stored == _row("SELECT id FROM members WHERE name = 'Emily'")["id"]
    col = [c for c in _row("SELECT 1") and
           get_conn().execute("PRAGMA table_info(households)").fetchall()
           if c["name"] == "primary_member_id"]
    assert col and col[0]["type"].upper() == "INTEGER", col


def test_a_request_that_names_nobody_leaves_the_resolver_to_answer(signed_in):
    """
    GUARD, pinned by mutation 1. The onboarding routes' own convention:
    None is "this request isn't saying", not a default to write down. Every
    other caller of this route — the standalone chores-setup page, the
    tests that post members directly — sends no primary_name, and must get
    exactly what it got before plus a resolved answer.
    """
    res = signed_in.post("/api/onboarding/household", json={
        "members": [
            {"name": "Vineeth", "age_group": "adult"},
            {"name": "Emily", "age_group": "adult"},
        ],
        "pets": [], "goals": "",
    })
    assert res.status_code == 200, res.text
    # Nothing was named, so the resolver answers: the setter-up (which this
    # route records as the first adult saved) — never a crash, never blank.
    assert tools.primary_member_id() == _row("SELECT id FROM members WHERE name = 'Vineeth'")["id"]


def test_a_name_that_matches_nobody_is_not_written_down(signed_in):
    """
    GUARD. A primary_name the members list doesn't contain names nobody,
    so the resolver answers instead — rather than the route inventing a
    member or storing a dangling id.
    """
    signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Emily", "age_group": "adult"}],
        "pets": [], "goals": "", "primary_name": "Nobody At All",
    })
    emily = _row("SELECT id FROM members WHERE name = 'Emily'")
    assert tools.primary_member_id() == emily["id"]
    assert _row("SELECT COUNT(*) AS n FROM members")["n"] == 1


def test_a_child_is_never_recorded_as_the_main_person(signed_in):
    """
    GUARD. The You row's age chip is editable (the card asks for it), so a
    household CAN mark the main person a Teen. The field refuses it and
    the resolver names an adult instead: "the main person" is read by
    Settings as somebody who can be moved it, and the move is adults only.
    """
    signed_in.post("/api/onboarding/household", json={
        "members": [
            {"name": "Robin", "age_group": "teen"},
            {"name": "Emily", "age_group": "adult"},
        ],
        "pets": [], "goals": "", "primary_name": "Robin",
    })
    assert tools.primary_member_id() == _row("SELECT id FROM members WHERE name = 'Emily'")["id"]


def test_an_existing_household_resolves_to_the_setup_member(signed_in):
    """
    CATCH (mutation 8), the card's "existing households" rule, half one:
    "primary = the member already pinned on the setup device". Being
    pinned lives in the signed session COOKIE and cannot be read back from
    the database — but households.set_up_by_member_id IS the member that
    pin is written for (this route sets the cookie to the id
    record_setup_adult returns), so it is the readable form of the same
    answer.
    """
    _seed([("Vineeth", "adult"), ("Emily", "adult")])
    vineeth = _row("SELECT id FROM members WHERE name = 'Vineeth'")["id"]
    emily = _row("SELECT id FROM members WHERE name = 'Emily'")["id"]
    conn = get_conn()
    # An existing household: somebody set it up, nobody is the main person.
    conn.execute("UPDATE households SET set_up_by_member_id = ?, primary_member_id = NULL WHERE id = 1", (emily,))
    conn.commit()
    conn.close()
    assert tools.primary_member_id() == emily, "the setter-up isn't the main person"
    assert tools.primary_member_id() != vineeth
    # And it is RECORDED, not re-derived on every read.
    assert _row("SELECT primary_member_id FROM households WHERE id = 1")[0] == emily


def test_an_existing_household_with_no_setup_member_resolves_to_the_first_adult(signed_in):
    """
    CATCH (mutation 8), half two of the same rule: "or the first adult".
    A household made by create_household.py, or one from before the
    first-open work, has no setter-up either.
    """
    _seed([("Reid", "child"), ("Vineeth", "adult"), ("Emily", "adult")])
    conn = get_conn()
    conn.execute("UPDATE households SET set_up_by_member_id = NULL, primary_member_id = NULL WHERE id = 1")
    conn.commit()
    conn.close()
    assert tools.primary_member_id() == _row("SELECT id FROM members WHERE name = 'Vineeth'")["id"], (
        "the first ADULT by creation order, not the first member"
    )


def test_a_household_with_nobody_in_it_yet_has_no_main_person(signed_in):
    """
    GUARD. One mid-onboarding, before its people are written: nothing to
    record, no crash, and asked again next time rather than a dangling id.
    """
    assert tools.primary_member_id() is None
    assert _row("SELECT primary_member_id FROM households WHERE id = 1")[0] is None


def test_the_backfill_resolves_every_household_the_same_way(signed_in):
    """
    GUARD, pinned by mutation 8's first form (see the docstring above —
    deleting the backfill alone reddens nothing, because the lazy resolve
    is in series with it). Both halves are worth keeping: the backfill
    answers for a household nothing has read yet, the lazy resolve for one
    made between two startups.
    """
    from app.db import _backfill_primary_member
    _seed([("Reid", "child"), ("Emily", "adult")])
    conn = get_conn()
    conn.execute("UPDATE households SET set_up_by_member_id = NULL, primary_member_id = NULL WHERE id = 1")
    conn.commit()
    _backfill_primary_member(conn)
    conn.commit()
    conn.close()
    assert _row("SELECT primary_member_id FROM households WHERE id = 1")[0] == (
        _row("SELECT id FROM members WHERE name = 'Emily'")["id"]
    )


def test_a_second_pass_through_onboarding_never_moves_the_main_person(signed_in):
    """
    GUARD. The same rule record_setup_adult states for its own field, for
    a reason of this one's own: MOVING the main person is Settings' job by
    the card's own wording, so a re-run of setup must not silently
    overrule a choice somebody made there. It is one tap in Who's here.
    """
    signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Emily", "age_group": "adult"}],
        "pets": [], "goals": "", "primary_name": "Emily",
    })
    emily = _row("SELECT id FROM members WHERE name = 'Emily'")["id"]
    signed_in.post("/api/onboarding/household", json={
        "members": [
            {"name": "Vineeth", "age_group": "adult"},
            {"name": "Emily", "age_group": "adult"},
        ],
        "pets": [], "goals": "", "primary_name": "Vineeth",
    })
    assert _row("SELECT primary_member_id FROM households WHERE id = 1")[0] == emily


# ---------------------------------------- C2: Settings shows and moves it


def test_settings_who_is_here_can_see_who_the_main_person_is(signed_in):
    """
    CATCH (mutation 7). The card: "Settings → Who's here shows 'Main
    person' next to them". Who's here is rendered from /api/memory's
    members (static/shell.js, wwkPeopleHtml), so what it needs is the fact
    on those rows — the id to move it by, and which one it is.
    """
    _seed([("Emily", "adult"), ("Vineeth", "adult"), ("Reid", "child")])
    mem = signed_in.get("/api/memory").json()
    rows = {m["name"]: m for m in mem["members"]}
    assert set(rows) == {"Emily", "Vineeth", "Reid"}
    assert [m["name"] for m in mem["members"] if m["is_primary"]] == ["Emily"]
    assert all(isinstance(m["id"], int) for m in mem["members"]), (
        "Who's here has no id to move the main person by"
    )


def test_settings_can_move_the_main_person_to_another_adult(signed_in):
    """
    CATCH (mutation 7). The other half of the card's criterion: "and lets
    it be moved to another adult". By id rather than by name, for the
    reason the field is an id.
    """
    _seed([("Emily", "adult"), ("Vineeth", "adult")])
    vineeth = _row("SELECT id FROM members WHERE name = 'Vineeth'")["id"]
    res = signed_in.post("/api/memory/primary-member", json={"member_id": vineeth})
    assert res.status_code == 200, res.text
    assert [m["name"] for m in res.json()["members"] if m["is_primary"]] == ["Vineeth"]
    assert _row("SELECT primary_member_id FROM households WHERE id = 1")[0] == vineeth


def test_the_main_person_cannot_be_moved_to_a_child_or_a_stranger(signed_in):
    """
    CATCH. "Moved to another ADULT" — and the refusal is a sentence
    written for a reader, because Who's here shows it as it stands.
    """
    _seed([("Emily", "adult"), ("Reid", "child")])
    reid = _row("SELECT id FROM members WHERE name = 'Reid'")["id"]
    res = signed_in.post("/api/memory/primary-member", json={"member_id": reid})
    assert res.status_code == 400
    assert res.json()["detail"] == tools.PRIMARY_NOT_AN_ADULT
    res = signed_in.post("/api/memory/primary-member", json={"member_id": 9999})
    assert res.status_code == 400
    assert res.json()["detail"] == tools.PRIMARY_NOT_A_MEMBER
    # Nothing moved.
    assert tools.primary_member_id() == _row("SELECT id FROM members WHERE name = 'Emily'")["id"]


def test_a_helper_who_does_not_eat_here_is_never_the_main_person(signed_in):
    """
    CATCH (mutation 15). Found by running the mutations, not by reading:
    neutering _EATS_HERE_SQL reddened NOTHING, so the clause was in all
    four statements and pinned by none of them.

    "Someone not eating here" (setup's helpers question, 2026-09-30) is a
    nanny or a parent who helps with dinners -- members.eats_here = 0, an
    adult who can sign in and whom nobody plans a meal for. The main
    person is the household's own, so a helper must not be resolved into
    the job by being the first adult on file, and Settings must refuse
    being pointed at one. Seeded through the invite route rather than by
    hand, so eats_here is 0 for the reason the app sets it.
    """
    maria = signed_in.post("/api/household/invites",
                           json={"name": "Maria", "eats_here": False}).json()["member"]["id"]
    assert _row("SELECT eats_here FROM members WHERE id = ?", maria)["eats_here"] == 0, (
        "the fixture is not a helper, so this test proves nothing"
    )
    _seed([("Emily", "adult")])
    emily = _row("SELECT id FROM members WHERE name = 'Emily'")["id"]
    assert maria < emily, "Maria must be the FIRST adult by id or the fallback is not exercised"

    assert tools.primary_member_id() == emily, "a helper was resolved into the job"
    res = signed_in.post("/api/memory/primary-member", json={"member_id": maria})
    assert res.status_code == 400
    assert res.json()["detail"] == tools.PRIMARY_NOT_AN_ADULT
    assert _row("SELECT primary_member_id FROM households WHERE id = 1")[0] == emily


def test_another_households_member_cannot_become_this_ones_main_person(signed_in):
    """
    GUARD. Every statement in primary_member.py is scoped by
    household_id(), so another household's member id is "I don't have that
    person down" rather than a move. This repo has three Decision log
    entries about cross-household leaks; a new field gets the test rather
    than the assumption.
    """
    _seed([("Emily", "adult")])
    emily = _row("SELECT id FROM members WHERE name = 'Emily'")["id"]
    conn = get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (2, 'Theirs')")
    conn.execute("INSERT INTO members (household_id, name, age_group) VALUES (2, 'Stranger', 'adult')")
    conn.commit()
    theirs = conn.execute("SELECT id FROM members WHERE household_id = 2").fetchone()["id"]
    conn.close()
    with pytest.raises(ValueError) as exc:
        tools.set_primary_member(theirs)
    assert str(exc.value) == tools.PRIMARY_NOT_A_MEMBER
    assert tools.primary_member_id() == emily
    assert _row("SELECT primary_member_id FROM households WHERE id = 2")[0] is None


def test_a_main_person_who_stops_being_an_adult_is_read_past_not_moved(signed_in):
    """
    GUARD. The one case the stored value is not simply believed. Re-marking
    the main person a child in Who's here must not leave the household with
    a main person who cannot be one — but quietly re-POINTING a field
    somebody set is worse than reading past it, so the answer moves and the
    column doesn't.
    """
    _seed([("Emily", "adult"), ("Vineeth", "adult")])
    emily = _row("SELECT id FROM members WHERE name = 'Emily'")["id"]
    assert tools.primary_member_id() == emily
    tools.set_member_age_group("Emily", "child")
    assert tools.primary_member_id() == _row("SELECT id FROM members WHERE name = 'Vineeth'")["id"]
    assert _row("SELECT primary_member_id FROM households WHERE id = 1")[0] == emily


# --------------------------------- D: "Just me", and who helps run the house


@_needs_node
def test_just_me_leaves_exactly_one_person_on_the_list(signed_in):
    """
    CATCH (mutation 4), the card's second named test. "Just me" is an
    ANSWER, not a skip: it says the household is one person. The pinned
    You row is left where it is — it isn't one of the others.
    """
    out = _run(_members_harness(name="Emily") + """
buildHouseholdStep();
addMemberRow('Vineeth', 'adult');
addMemberRow('Reid', 'child');
const before = currentMembers().map(m => m.name);
const moved = [];
goForward = function (k) { moved.push(k); };
ELS['household-just-me'].click();
console.log(JSON.stringify({
  before: before, after: currentMembers(), moved: moved,
  note: !ELS['household-empty'].hidden
}));
""")
    assert out["before"] == ["Emily", "Vineeth", "Reid"]
    assert out["after"] == [{"name": "Emily", "age_group": "adult"}], (
        "'Just me' left somebody else on the list"
    )
    assert out["moved"] == ["household"], "'Just me' didn't go forward"
    assert out["note"] is False


@_needs_node
def test_just_me_prunes_the_answers_of_whoever_it_took_off(signed_in):
    """
    CATCH. The rows "Just me" removes may have been NAMED, and restrictions
    and helper picks are keyed by name — the same reason the row's own ×
    prunes. Without this, "Vineeth, allergic to peanuts, actually just me"
    leaves an allergy belonging to nobody in the payload.
    """
    out = _run(_members_harness(name="Emily") + """
buildHouseholdStep();
addMemberRow('Vineeth', 'adult');
restrictionAnswers['Vineeth'] = ['allergy: peanuts'];
restrictionAnswers['Emily'] = ['vegetarian'];
helperPicks = ['adult:Vineeth'];
goForward = function () {};
ELS['household-just-me'].click();
console.log(JSON.stringify({
  restrictions: Object.keys(restrictionAnswers), helpers: helperPicks
}));
""")
    assert out["restrictions"] == ["Emily"], "an allergy outlived the person it belonged to"
    assert out["helpers"] == [], "a helper who isn't here any more is still picked"


def test_just_me_creates_a_one_person_household_end_to_end(signed_in):
    """
    CATCH (mutation 4). The card's own words, through the real route: ONE
    member on file afterwards, and that member the main person.
    """
    res = signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Emily", "age_group": "adult"}],
        "pets": [], "goals": "", "primary_name": "Emily",
    })
    assert res.status_code == 200, res.text
    assert _row("SELECT COUNT(*) AS n FROM members WHERE household_id = 1")["n"] == 1
    emily = _row("SELECT id, name FROM members WHERE household_id = 1")
    assert emily["name"] == "Emily"
    assert _row("SELECT primary_member_id FROM households WHERE id = 1")[0] == emily["id"]
    mem = signed_in.get("/api/memory").json()
    assert [m["is_primary"] for m in mem["members"]] == [True]


@_needs_node
def test_the_helpers_step_lists_only_the_other_adults(signed_in):
    """
    CATCH (mutation 3), the card's third named test. "Does anyone else help
    run the house?" is a question about the OTHERS. It was `.slice(1)` —
    everyone but whichever row happened to be first, i.e. the
    members[0]-is-the-user convention — and it excludes the main person BY
    NAME now.
    """
    out = _run(_members_harness(name="Emily") + """
buildHouseholdStep();
addMemberRow('Vineeth', 'adult');
addMemberRow('Reid', 'child');
console.log(JSON.stringify({
  adults: helperAdults(currentMembers()),
  titles: helperOptions(helperAdults(currentMembers())).map(o => o.title)
}));
""")
    assert out["adults"] == ["Vineeth"], "the helpers step offered the main person"
    assert out["titles"] == ["Yes, Vineeth does", "Someone not eating here", "Just me"]


@_needs_node
def test_the_helpers_step_excludes_the_main_person_even_last_on_the_list(signed_in):
    """
    CATCH (mutation 3), and the one that tells a NAME-based exclusion from
    a positional one. With `.slice(1)` the answer depends on where the main
    person's row sits; with the field it doesn't. Driven by moving the
    pinned row to the END of #members, which is the only way to ask the
    question of the page's own function.
    """
    out = _run(_members_harness(name="Emily") + """
buildHouseholdStep();
addMemberRow('Vineeth', 'adult');
// The pinned row, moved to the bottom: nothing on screen does this, but
// it is what tells "the main person" from "the first row".
const you = membersDiv.querySelector('.member-block.is-primary');
membersDiv._children = membersDiv._children.filter(c => c !== you).concat([you]);
console.log(JSON.stringify({
  order: currentMembers().map(m => m.name),
  adults: helperAdults(currentMembers())
}));
""")
    assert out["order"] == ["Vineeth", "Emily"]
    assert out["adults"] == ["Vineeth"], (
        "helperAdults read a position rather than the main person"
    )


@_needs_node
def test_just_me_leaves_the_helpers_step_with_nobody_to_offer(signed_in):
    """
    GUARD. A one-person household has no other adult, so the helpers step
    offers only its two standing options — not an empty "Yes, does".
    """
    out = _run(_members_harness(name="Emily") + """
buildHouseholdStep();
addMemberRow('Vineeth', 'adult');
goForward = function () {};
ELS['household-just-me'].click();
console.log(JSON.stringify(helperOptions(helperAdults(currentMembers())).map(o => o.title)));
""")
    assert out == ["Someone not eating here", "Just me"]
