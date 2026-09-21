""""Who's eating?" becomes "Is anyone out?" — the initials under each meal.

Emily, flow 0 walk, 2026-09-15, from the Monday sheet: "this who's eating
and then the clicking doesn't make sense. the click of the initial removes
them from the attendee." Her decision the same day: "for the who's eating
just put 'is anyone out' instead."

The label said one thing and the tap did the opposite, so the household
learned the rule by getting it wrong — and a wrong guess here is not
cosmetic, it writes an absence to the week.

Four things are pinned here:

  1. Every presence row asks "Is anyone out?" — dinner, lunch and breakfast.
     Before this, dinner alone carried a caption ("Who's eating?") that its
     own block wrote, and lunch and breakfast carried none.
  2. The out state stays greyed and struck through, and a caption under the
     initials says it in words ("Dinner for 2 — Vineeth's out.").
  3. No apricot ring is left on an initial after a pointer tap. The keyboard
     ring is a floor and survives.
  4. Tapping again puts the person back and the caption clears.

And the thing that must NOT change: what is saved. The attendance write is
byte-identical to what it was, and there is a test saying so.

The front-end half runs the page's own functions under node rather than
reading the source for markers, because "the label says the opposite of what
the tap does" and "the caption is wrong for two people" are both behaviour a
marker test cannot see. Three claims genuinely cannot be run — two CSS rules
and one block of markup that only exists inside a DOM write — and each says
so in its own docstring.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from app.tools import attendance as attendance_tools
from tests import nodeharness

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")
THEME = (REPO / "static" / "theme.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the page's own functions"
)


def _extract(name: str, source: str = PAGE) -> str:
    """One function, by brace matching — the same slicing test_planning_exit.py
    uses on this page."""
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


def _copy_constant(name: str) -> str:
    """The page's own copy constant, so a rewording there is a rewording here.

    A page that hasn't got it yet (main) gets a placeholder rather than a
    ReferenceError, so these tests fail on their own assertion — "the question
    isn't in the markup" — instead of dying in the harness.
    """
    m = re.search(r"^\s*var %s = .*$" % re.escape(name), PAGE, re.M)
    return m.group(0).strip() if m else "var %s = '(this page has no such constant)';" % name


# The markup half: what a presence row renders.
_MARKUP = (
    _copy_constant("PRESENCE_QUESTION") + "\n"
    + "var attendance = { members: [{id:1,name:'Emily'},{id:2,name:'Vineeth'},{id:3,name:'Reid'}], byDate: {} };\n"
    + "function stepperHtml(f, l) { return '<div class=\"stepper\" data-field=\"' + f + '\">' + l + '</div>'; }\n"
    + _extract("esc") + "\n"
    + _extract("initialFor") + "\n"
    + _extract("presenceHtml") + "\n"
    + _extract("mealBlockHtml") + "\n"
)

# The sentence half: the caption under the initials.
_CAPTION = (
    _extract("capitalize") + "\n"
    + _extract("joinNames") + "\n"
    + _extract("localSummary") + "\n"
)


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# 1. The question
# ---------------------------------------------------------------------------

@_needs_node
@pytest.mark.parametrize("slot", ["dinner", "lunch", "breakfast"])
def test_every_presence_row_asks_whether_anyone_is_out(slot):
    """CATCH — on main this function emits a "WHO" eyebrow and no question at
    all, so the only label a household ever saw was the one dinner's own block
    wrote above it: "Who's eating?"."""
    html = _node(_MARKUP + "console.log(JSON.stringify(presenceHtml(%s)));" % json.dumps(slot))
    assert "Is anyone out?" in html
    assert html.index("Is anyone out?") < html.index("class=\"avatar\""), "above the initials, not below"
    assert "Who" not in html.replace("Is anyone out?", ""), "no 'WHO' eyebrow left beside them"


@_needs_node
@pytest.mark.parametrize("slot,label", [("lunch", "Lunch"), ("breakfast", "Breakfast")])
def test_lunch_and_breakfast_carry_the_question_too(slot, label):
    """CATCH — these two blocks had no presence caption of any kind on main:
    three rows of initials in one sheet, one of them explained."""
    html = _node(_MARKUP + "console.log(JSON.stringify(mealBlockHtml(%s, %s)));"
                 % (json.dumps(slot), json.dumps(label)))
    assert html.count("Is anyone out?") == 1
    assert html.index(label) < html.index("Is anyone out?") < html.index("class=\"avatar\"")


def test_the_dinner_block_no_longer_writes_a_caption_of_its_own():
    """CATCH, and a SOURCE MARKER because it has to be: the dinner block is
    built inside openDaySheet's innerHTML write, so there is no function to
    call. One producer of the question means the three rows cannot drift."""
    sheet = _extract("openDaySheet")
    assert "presenceHtml('dinner')" in sheet
    assert "day-section-label presence-section-label" not in sheet
    # Exactly two mentions in the whole page: the constant, and the one use.
    assert PAGE.count("PRESENCE_QUESTION") == 2
    assert "Is anyone out?" in PAGE


def test_nothing_anywhere_still_asks_who_is_eating():
    """CATCH — the old label and the old eyebrow, gone from the markup and the
    CSS. Read off the three functions that build a day sheet rather than off
    the whole file, whose comments record what changed and why."""
    for name in ("presenceHtml", "mealBlockHtml", "openDaySheet"):
        fn = _extract(name)
        assert "eating" not in fn.lower(), name
        assert "presence-label" not in fn, name
    assert "Who&rsquo;s eating" not in PAGE
    assert "presence-label" not in PAGE, "the 'WHO' eyebrow beside the initials is gone"


def test_the_control_lives_in_this_page_and_nowhere_else():
    """GUARD — the criterion says "anywhere else the same control is reused",
    so this pins that there is nowhere else. The shell has no attendance
    control at all; the away sheet's who-picker is a different question (full
    names, and tapping means that person IS travelling)."""
    for name in ("shell.js", "onboarding.html", "meal-setup.html", "chores-setup.html"):
        other = (REPO / "static" / name).read_text(encoding="utf-8")
        assert "presenceHtml" not in other
        assert 'class="avatar"' not in other
    assert 'class="who-opt"' in PAGE and "All of us" in PAGE  # the away picker, untouched


# ---------------------------------------------------------------------------
# 2. The caption
# ---------------------------------------------------------------------------

def _caption(absent, guests=0, members=3, slot="dinner"):
    att = {
        "absent_names": absent,
        "guest_count": guests,
        "headcount": (members - len(absent)) + guests,
    }
    return _node(_CAPTION + "console.log(JSON.stringify(localSummary(%s, %s)));"
                 % (json.dumps(att), json.dumps(slot)))


@_needs_node
def test_nobody_out_says_nothing():
    """GUARD — an empty caption is hidden by .presence-summary:empty, so a day
    nobody has touched carries no line. Unchanged by this ticket."""
    assert _caption([]) == ""
    assert ".presence-summary:empty { display: none; }" in PAGE


@_needs_node
def test_one_person_out_is_named():
    """GUARD — the wording the card asked for was already the wording here and
    on the server; this pins it against a later reword of either."""
    assert _caption(["Vineeth"]) == "Dinner for 2 — Vineeth’s out."


@_needs_node
def test_two_people_out_take_the_plural():
    """GUARD — "are out", and the names joined, not a count."""
    assert _caption(["Vineeth", "Reid"]) == "Dinner for 1 — Vineeth and Reid are out."


@_needs_node
def test_everybody_out_is_the_away_state_and_says_what_follows_from_it():
    """GUARD — this is the one case that is not "N people are out": nothing is
    planned and nothing is bought for that meal, so the caption says that
    instead of counting to zero."""
    assert _caption(["Emily", "Vineeth", "Reid"]) == (
        "Dinner skipped — nobody’s home. Nothing planned, nothing bought."
    )


@_needs_node
def test_a_household_of_one_is_the_same_two_answers():
    """GUARD — nobody out says nothing; the one person out is the away state,
    never "Dinner for 0"."""
    assert _caption([], members=1) == ""
    assert _caption(["Emily"], members=1) == (
        "Dinner skipped — nobody’s home. Nothing planned, nothing bought."
    )


@_needs_node
def test_everybody_out_but_guests_coming_is_not_the_away_state():
    """Half GUARD, half CATCH, and red on the unmodified page for the second
    half. GUARD: headcount is people plus guests on both sides, so a table
    with only visitors at it still gets planned and shopped for — unchanged.
    CATCH: main joined the two clauses with "and" where the server says
    "with"."""
    assert _caption(["Emily", "Vineeth", "Reid"], guests=2) == (
        "Dinner for 2 — Emily, Vineeth and Reid are out with 2 guests."
    )


@_needs_node
def test_the_caption_says_what_the_server_says():
    """CATCH — main's local echo joined an absence and a guest with "and"
    while the server joins them with "with", and the local one is what every
    day sheet opens reading (the week GET carries no summary), so the caption
    reworded itself the moment it was re-saved.

    Still not byte-identical: the server writes a straight apostrophe in
    "'s out" and this page writes a curly one everywhere. Normalised away
    below rather than fixed — the server's is a string with its own test
    (tests/test_attendance.py) and this page is not the place to settle it —
    and named here so nobody reads this test as claiming more than it does."""
    for absent, guests, slot in (
        (["Vineeth"], 0, "dinner"),
        (["Vineeth"], 1, "lunch"),
        (["Vineeth", "Reid"], 3, "breakfast"),
        ([], 2, "dinner"),
        (["Emily", "Vineeth", "Reid"], 0, "dinner"),
    ):
        att = {
            "slot": slot,
            "absent_names": absent,
            "guest_count": guests,
            "headcount": (3 - len(absent)) + guests,
            "nobody_home": (3 - len(absent)) + guests == 0,
        }
        assert _caption(absent, guests, slot=slot).replace("’", "'") == \
            attendance_tools.summary_line(att)


def test_the_caption_sits_under_the_initials_and_above_the_offer():
    """CATCH — one producer now, and in this order: the line confirms the tap
    that was just made, and "Just this week?" only follows from it. On main
    the three blocks each wrote their own .presence-summary AFTER the offer."""
    fn = _extract("presenceHtml")
    assert fn.count("presence-summary") == 1
    assert fn.index("class=\"avatar\"") < fn.index("presence-summary") < fn.index("class=\"remember\"")
    assert PAGE.count("'<div class=\"presence-summary\"></div>'") == 1


@_needs_node
def test_tapping_again_puts_the_person_back_and_clears_the_caption():
    """GUARD — criterion 4, through the page's own toggle rather than by
    re-deriving it: out, then in, and the line is empty again."""
    harness = (
        "var attendance = { members: [{id:1,name:'Emily'},{id:2,name:'Vineeth'}], byDate: {} };\n"
        + _CAPTION
        + _extract("attendanceFor") + "\n"
        + _extract("ensureLocalAttendance") + "\n"
        + _extract("applyLocalToggle") + "\n"
        + "var out = [];\n"
        + "applyLocalToggle('2026-09-14', 'dinner', 'Vineeth', false);\n"
        + "out.push(attendanceFor('2026-09-14','dinner').summary);\n"
        + "applyLocalToggle('2026-09-14', 'dinner', 'Vineeth', true);\n"
        + "out.push(attendanceFor('2026-09-14','dinner').summary);\n"
        + "out.push(attendanceFor('2026-09-14','dinner').absent_names.length);\n"
        + "console.log(JSON.stringify(out));\n"
    )
    assert _node(harness) == ["Dinner for 1 — Vineeth’s out.", "", 0]


# ---------------------------------------------------------------------------
# 3. The ring
# ---------------------------------------------------------------------------

def test_the_ring_is_keyboard_only_in_css_and_is_never_removed():
    """GUARD, and a SOURCE MARKER because a stylesheet has no behaviour to
    run: every focus rule that can reach an initial is :focus-visible, and
    none of them is an outline:none. Verified in Chromium besides — after a
    touch tap the outline computes to 0px and document.activeElement is the
    body; after Tab it is 3px apricot."""
    avatar_rules = [ln for ln in PAGE.splitlines() if ".avatar" in ln and ":focus" in ln]
    assert avatar_rules, "the page still styles the initial's focus"
    for rule in avatar_rules:
        assert ":focus-visible" in rule
        assert re.search(r"\.avatar:focus\b(?!-visible)", rule) is None
    assert "button:focus-visible" in THEME
    assert re.search(r"^button:focus\b(?!-visible)", THEME, re.M) is None
    assert re.search(r"\.avatar[^{]*\{[^}]*outline:\s*none", PAGE) is None


def test_the_initials_are_still_44px_targets_and_the_out_state_still_reads_as_out():
    """GUARD — hard rule 6, and criterion 2's "as today". Nothing here moved;
    this is the promise that the relabelling did not quietly restyle them."""
    avatar = PAGE[PAGE.index("  .avatar {"):PAGE.index("  .avatar.out {")]
    assert "width: 44px; height: 44px;" in avatar
    out = PAGE[PAGE.index("  .avatar.out {"):PAGE.index("  .avatar.out {") + 250]
    assert "text-decoration: line-through" in out
    assert "var(--ink-inactive)" in out


def test_the_day_sheet_gains_no_second_apricot():
    """GUARD on the fills, CATCH on the eyebrow.

    Hard rule 5, stated the way it actually measures. With a day sheet open at
    390px, TWO visible elements fill rgb(224, 145, 92) — the footer #cta and
    the sheet's own #day-done — and exactly ONE of them is reachable, because
    elementFromPoint over #cta returns the #day-done sitting above it. That is
    identical on the unmodified page, so the rule holds here in the sense it
    has always held. An earlier version of this docstring claimed "exactly one
    element fills", which was simply wrong.

    The second half is red on the unmodified page: the "WHO" eyebrow beside
    the initials was --apricot-label, and it went with the word."""
    # Four fills since the 2026-09-21 intake motion: .cta and .failed-retry
    # as before, plus the 4px progress bars (.bar.on) and the Surprise me
    # card's star tile — neither a button, so the sheet still competes with
    # one apricot action.
    assert PAGE.count("background: var(--apricot);") == 4
    assert "--apricot" not in _extract("presenceHtml")
    presence_css = PAGE[PAGE.index("  .presence {"):PAGE.index("  .remember {")]
    assert "--apricot-label" not in presence_css


# ---------------------------------------------------------------------------
# What is saved
# ---------------------------------------------------------------------------

@_needs_node
def test_the_attendance_write_is_byte_identical():
    """GUARD, and the one that matters most: this ticket is a label, a caption
    and a focus style. The tap still sends the same method, the same URL and
    the same body it sent before, and exactly one request."""
    harness = (
        "var weekStart = '2026-09-14';\n"
        "var attendance = { members: [{id:1,name:'Emily'},{id:2,name:'Vineeth'}], byDate: {} };\n"
        "var document = { activeElement: null };\n"
        "var sent = [];\n"
        "function paintPresence() {}\n"
        "function toastSaved() {}\n"
        "function fetch(url, opts) { sent.push({ url: url, method: opts.method, body: opts.body });\n"
        "  return Promise.resolve({ ok: true, json: function () { return Promise.resolve({ summary: 'x' }); } }); }\n"
        + _CAPTION
        + _extract("attendanceFor") + "\n"
        + _extract("ensureLocalAttendance") + "\n"
        + _extract("applyLocalToggle") + "\n"
        + "async " + _extract("toggleAvatar") + "\n"
        + "var btn = { dataset: { slot: 'dinner', member: 'Vineeth' },\n"
        + "            classList: { contains: function () { return false; } } };\n"
        + "toggleAvatar({ dataset: { date: '2026-09-14' } }, btn)\n"
        + "  .then(function () { console.log(JSON.stringify(sent)); });\n"
    )
    assert _node(harness) == [{
        "url": "/api/week/2026-09-14/attendance",
        "method": "POST",
        "body": '{"date":"2026-09-14","slot":"dinner","member":"Vineeth","present":false}',
    }]


def test_the_initials_are_still_a_tap_to_exclude_control():
    """GUARD — the tap sends the opposite of the state it found, which is what
    makes the initials a way OUT of a meal rather than a way into one. That
    half of the control is unchanged."""
    assert "present: wasOut" in _extract("toggleAvatar")
    assert "btn.classList.toggle('out', out);" in _extract("paintPresence")


@_needs_node
def test_pressed_means_out_because_that_is_what_the_question_asks():
    """CATCH — on the unmodified page aria-pressed meant IN. That was right
    under "Who's eating?" and, once the heading says "Is anyone out?", it is
    Emily's own complaint one layer down: a screen reader announced "V, toggle
    button, pressed" beneath a question asking who is OUT, which reads as the
    opposite of the truth.

    Inverted rather than dropped. Dropping aria-pressed would leave the state
    carried only by `title`, which is a description rather than a name and is
    not announced by every AT — a screen-reader user would then have no way to
    tell who is out at all.

    Runs the real paintPresence against stub buttons, so what is pinned is the
    polarity rather than the spelling of a line."""
    harness = (
        "var attendance = { members: [{ id: 1, name: 'Emily' }, { id: 2, name: 'Vineeth' }],"
        "  byDate: { '2026-09-14': { dinner:"
        "    { absent_names: ['Vineeth'], summary: 'Dinner for 1' } } } };\n"
        "var seen = { Emily: { cls: {}, attrs: {} }, Vineeth: { cls: {}, attrs: {} } };\n"
        "function stub(name) { return { dataset: { member: name }, title: '',\n"
        "  classList: { toggle: function (c, on) { seen[name].cls[c] = on; } },\n"
        "  setAttribute: function (k, v) { seen[name].attrs[k] = v; } }; }\n"
        "var buttons = [stub('Emily'), stub('Vineeth')];\n"
        "var block = { querySelectorAll: function () { return buttons; },\n"
        "              querySelector: function () { return null; } };\n"
        "var dayEl = { dataset: { date: '2026-09-14' },\n"
        "              querySelector: function () { return block; } };\n"
        + _extract("attendanceFor") + "\n"
        + _extract("paintPresence") + "\n"
        + "paintPresence(dayEl, 'dinner');\n"
        + "console.log(JSON.stringify(seen));\n"
    )
    assert _node(harness) == {
        # home: not struck through, not pressed
        "Emily": {"cls": {"out": False}, "attrs": {"aria-pressed": "false"}},
        # out: struck through, and pressed
        "Vineeth": {"cls": {"out": True}, "attrs": {"aria-pressed": "true"}},
    }


def test_the_row_ships_with_nobody_pressed():
    """CATCH — nobody is out until somebody says so, so the markup ships
    aria-pressed="false". The unmodified page shipped "true", which under the
    new heading would announce the whole household as out for the beat before
    paintPresence runs."""
    fn = _extract("presenceHtml")
    assert 'aria-pressed="false"' in fn
    assert 'aria-pressed="true"' not in fn
