"""
"This is how to talk to me" — onboarding coaching (Emily + Julia,
2026-09-08, branch `coaching-how-to-talk-to-me`).

Julia is the first beta tester who arrived never having talked to an app.
She finished setup, landed on Today, and did not know what she was supposed
to say. Three parts answer that, and this file guards all three:

  1. **Example prompts under the ask bar**, three per tab, on that tab's
     first three visits and then gone. Counted per household in
     localStorage. (Two per tab, and the same generic four-chip row
     layered permanently on top of them regardless of tab, until
     2026-09-11 — item 15 of the design-tidy pass retired that generic row
     and gave each tab three of its own; see COACH_EXAMPLES in shell.js and
     tests/test_ask_sheet_named_intents.py's own header for the retirement
     note.)
  2. **One how-and-why card** on Today, the first time the shell opens after
     setup — the household has a plan and `households.coaching_seen_at` is
     still null. "Got it" writes that column.
  3. **A "Helpful tips" sheet**, behind a Preferences row and a "?" beside
     the ask bar.

Three kinds of test, and the split is the usual one for this repo:

  * BEHAVIOUR, against the real route and the real column — the migration,
    the read, and the write-once dismissal.
  * NODE, running shell.js's own functions (the visit counter, the chip
    render and its tap, the card's gate) rather than reading the source for
    a marker — see tests/test_kitchen_and_preferences.py §5 for the pattern.
  * SOURCE, for the copy and the wiring that has no pure function to run.
    Every user-facing string here is asserted verbatim: if the copy is
    deliberately reworded, change the constant in the same commit and say
    so — do not delete the test.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import tools
from app.db import _run_migrations, get_conn


REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _forget_coaching():
    """conftest keeps household id=1 between tests, so the dismissal it
    carries would leak from whichever test wrote it into every test after."""
    conn = get_conn()
    try:
        conn.execute("UPDATE households SET coaching_seen_at = NULL")
        conn.commit()
    finally:
        conn.close()
    yield


# --- 1. the column, the route, and the write-once dismissal ---------------

def test_the_migration_adds_the_column_once_and_survives_being_re_run():
    conn = get_conn()
    _run_migrations(conn)
    _run_migrations(conn)
    conn.commit()
    names = [row["name"] for row in conn.execute("PRAGMA table_info(households)")]
    conn.close()
    assert names.count("coaching_seen_at") == 1


def test_a_household_fresh_out_of_setup_has_not_been_coached(signed_in):
    state = signed_in.get("/api/coaching").json()
    assert state["household_id"] == 1
    assert state["has_plan"] is False
    assert state["coaching_seen_at"] is None


def test_the_card_only_becomes_due_once_there_is_a_plan_to_talk_about(signed_in):
    """The card is the first thing after setup, and setup ends with a week —
    so "has a plan" is how the shell knows setup actually finished rather
    than being abandoned halfway."""
    tools.create_weekly_plan("2026-09-07")
    assert signed_in.get("/api/coaching").json()["has_plan"] is True


def test_got_it_records_that_the_household_read_it(signed_in):
    assert signed_in.post("/api/coaching/seen").status_code == 200
    assert signed_in.get("/api/coaching").json()["coaching_seen_at"] is not None


def test_a_second_got_it_leaves_the_first_reading_alone(signed_in):
    first = signed_in.post("/api/coaching/seen").json()["coaching_seen_at"]
    again = signed_in.post("/api/coaching/seen").json()["coaching_seen_at"]
    assert first is not None and again == first


def test_the_dismissal_is_the_households_own_and_not_the_apps(signed_in):
    """Second household, same server: one reading the card must not silence
    it for the other. (Every table here carries household_id for exactly
    this reason — see schema.sql's header.)"""
    conn = get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (2, 'Next door')")
    conn.commit()
    conn.close()
    signed_in.post("/api/coaching/seen")
    with tools.use_household(2):
        assert tools.get_coaching_state()["coaching_seen_at"] is None
    assert tools.get_coaching_state()["coaching_seen_at"] is not None


# --- 2. the front end, run rather than read -------------------------------

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)


def _node(script: str):
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _slice(start: str, end: str) -> str:
    a = SHELL_JS.index(start)
    b = SHELL_JS.index(end, a)
    return SHELL_JS[a:b]


# A DOM small enough to state in one screen and honest about the two things
# renderAskExamples actually does to an element: set innerHTML/hidden, and
# find the chips it just wrote. querySelectorAll reads the markup back with
# a regex rather than parsing it — which is the point, because the markup
# is what a browser would be handed.
_DOM_STUB = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
const STORE = {};
const window = { localStorage: {
  getItem: function (k) { return Object.prototype.hasOwnProperty.call(STORE, k) ? STORE[k] : null; },
  setItem: function (k, v) { STORE[k] = String(v); }
} };
function makeEl() {
  return {
    innerHTML: '', hidden: false, dataset: {}, handlers: {},
    querySelectorAll: function () {
      const el = this, out = [], re = /data-i="(\\d+)"/g;
      let m;
      while ((m = re.exec(el.innerHTML)) !== null) {
        (function (i) {
          out.push({ dataset: { i: String(i) },
                     addEventListener: function (_evt, fn) { el.handlers[i] = fn; } });
        })(Number(m[1]));
      }
      return out;
    },
    labels: function () {
      const out = [], re = />([^<]+)<\\/button>/g;
      let m;
      while ((m = re.exec(this.innerHTML)) !== null) out.push(m[1]);
      return out;
    }
  };
}
const ELS = { 'ask-examples': makeEl(), 'today-ask-examples': makeEl(), 'coach-card-slot': makeEl() };
const document = { getElementById: function (id) { return ELS[id] || null; } };
let askConversationStarted = false;
const SENT = [], OPENED = [];
function openAskSheet(p) { OPENED.push(p === undefined ? null : p); }
function sendAskMessage(t) { SENT.push(t); }
"""


def _add_adult(name: str) -> None:
    """An adult, spelled the way onboarding spells it — "Adult", capitalised."""
    tools.add_member(name)
    tools.set_member_age_group(name, "Adult")


def _examples_block() -> str:
    return _slice("var COACH_VISITS_TO_SHOW = 3;", "  // ---------- the how-and-why card ----------")


def _card_block() -> str:
    return _slice("var COACH_CARD_LINES = [", "  // Both buttons dismiss it")


def _tips_block() -> str:
    return _slice("var TIPS_OPENING = ", "  var tipsSheetEl = null;")


@_needs_node
def test_each_tab_gets_its_own_three_prompts_for_three_visits_and_no_more():
    script = (
        _DOM_STUB + _examples_block() + """
coachState.ready = true;
coachState.householdId = 1;
const out = {};
['today', 'week', 'grocery', 'kitchen'].forEach(function (tab) {
  const seen = [];
  for (let visit = 1; visit <= 4; visit++) {
    coachOnTabShown(tab);
    seen.push(ELS['ask-examples'].hidden ? [] : ELS['ask-examples'].labels());
  }
  out[tab] = seen;
});
console.log(JSON.stringify(out));
"""
    )
    seen = _node(script)
    expected = {
        "today": ["What should I cook tonight?", "Swap tonight for something quicker", "What do I need to defrost?"],
        # No exampleName set in this script, so the away example falls back
        # to the name-free sentence — see the two tests below for both
        # branches.
        "week": ["Plan the rest of my week", "Less chicken this week", "One of us is out Thursday"],
        "grocery": ["Add what we’re low on", "Move this to Costco", "What’s this for?"],
        "kitchen": ["Walk me through tonight", "What can I prep now?", "How long will dinner take?"],
    }
    for tab, prompts in expected.items():
        assert seen[tab][0] == prompts, tab
        assert seen[tab][1] == prompts, tab
        assert seen[tab][2] == prompts, tab
        assert seen[tab][3] == [], f"{tab} kept coaching past the third visit"


@_needs_node
def test_plans_away_example_uses_the_households_own_adult():
    """
    This chip shipped hardcoded as "Vineeth is out Thursday" — the
    developer's own partner, read by every household in the beta as an
    example about their week. The name now comes from /api/coaching.

    Lives on Plan's third slot since 2026-09-11 (item 15, design-tidy
    pass) — the ticket's own text for that slot was "Jamie's out
    Thursday", the exact same mistake in a new place, so it goes through
    coachAwayExample() here too rather than being typed in literally.
    """
    script = (
        _DOM_STUB + _examples_block() + """
coachState.ready = true;
coachState.householdId = 1;
coachState.exampleName = 'Marcus';
coachOnTabShown('week');
console.log(JSON.stringify(ELS['ask-examples'].labels()));
"""
    )
    assert _node(script) == ["Plan the rest of my week", "Less chicken this week", "Marcus is out Thursday"]


@_needs_node
def test_the_away_example_still_teaches_when_no_name_is_known_yet():
    """
    /api/coaching has not answered, or the household has nobody on record.
    The lesson is "you can just tell me someone is out", and it survives
    without a name — an empty chip, or one reading "undefined is out
    Thursday", would not.
    """
    script = (
        _DOM_STUB + _examples_block() + """
coachState.ready = true;
coachState.householdId = 1;
coachState.exampleName = null;
coachOnTabShown('week');
console.log(JSON.stringify(ELS['ask-examples'].labels()));
"""
    )
    assert _node(script) == ["Plan the rest of my week", "Less chicken this week", "One of us is out Thursday"]


def test_no_real_persons_name_is_written_into_the_example_chips():
    """
    A source check, deliberately: the bug was not that the sentence was
    wrong, it was that a name from outside the household was baked into the
    build. Names in these chips belong in the payload, never in the file.
    """
    block = _examples_block()
    # Only the literal table, not the comment above it that explains why the
    # name was taken out of it.
    examples = block[block.index("var COACH_EXAMPLES"):block.index("function coachAwayExample")]
    assert "Vineeth" not in examples, "a real person's name is back in COACH_EXAMPLES"


def test_the_example_name_is_an_adult_of_this_household():
    _add_adult("Emily")
    _add_adult("Marcus")
    tools.add_member("Sam")
    tools.set_member_age_group("Sam", "Child")
    # Lowest id, so the chip does not reshuffle between visits.
    assert tools.get_coaching_state()["example_name"] == "Emily"


def test_a_lowercase_age_group_still_counts_as_an_adult():
    """Onboarding writes "Adult"; older rows say "adult"."""
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    assert tools.get_coaching_state()["example_name"] == "Emily"


def test_a_household_with_nobody_on_record_gets_no_example_name():
    """The shell has a name-free sentence for exactly this."""
    assert tools.get_coaching_state()["example_name"] is None


def test_a_household_of_children_only_gets_no_example_name():
    """
    Not a real household shape, but the query has to answer something: a
    child's name in "X is out Thursday" is a worse example than no name.
    """
    tools.add_member("Sam")
    tools.set_member_age_group("Sam", "Child")
    assert tools.get_coaching_state()["example_name"] is None


@_needs_node
def test_one_tabs_three_visits_do_not_spend_anothers():
    script = (
        _DOM_STUB + _examples_block() + """
coachState.ready = true;
coachState.householdId = 1;
for (let i = 0; i < 5; i++) coachOnTabShown('today');
coachOnTabShown('grocery');
console.log(JSON.stringify(ELS['ask-examples'].labels()));
"""
    )
    assert _node(script) == ["Add what we’re low on", "Move this to Costco", "What’s this for?"]


@_needs_node
def test_the_count_is_kept_per_household():
    """Two households sharing a browser (Emily's laptop, the beta) must not
    share a counter — the second one has never been coached."""
    script = (
        _DOM_STUB + _examples_block() + """
coachState.ready = true;
coachState.householdId = 1;
for (let i = 0; i < 4; i++) coachOnTabShown('today');
const spentForOne = ELS['ask-examples'].hidden;
coachState.householdId = 2;
coachOnTabShown('today');
console.log(JSON.stringify({ spentForOne: spentForOne, freshForTwo: ELS['ask-examples'].labels() }));
"""
    )
    out = _node(script)
    assert out["spentForOne"] is True
    assert out["freshForTwo"] == [
        "What should I cook tonight?", "Swap tonight for something quicker", "What do I need to defrost?"
    ]


@_needs_node
def test_tapping_a_prompt_sends_it_through_the_existing_ask_path():
    script = (
        _DOM_STUB + _examples_block() + """
coachState.ready = true;
coachState.householdId = 1;
coachOnTabShown('week');
ELS['ask-examples'].handlers[1]();
console.log(JSON.stringify({ sent: SENT, opened: OPENED.length }));
"""
    )
    out = _node(script)
    assert out["sent"] == ["Less chicken this week"]
    assert out["opened"] == 1, "the chip must open the surface the reply lands on"


@_needs_node
def test_nothing_is_suggested_once_the_household_has_said_something_itself():
    script = (
        _DOM_STUB + _examples_block() + """
coachState.ready = true;
coachState.householdId = 1;
askConversationStarted = true;
coachOnTabShown('today');
console.log(JSON.stringify({ hidden: ELS['ask-examples'].hidden, html: ELS['ask-examples'].innerHTML }));
"""
    )
    out = _node(script)
    assert out["hidden"] is True and out["html"] == ""


@_needs_node
def test_nothing_is_suggested_before_the_household_is_known():
    """coachState.ready is false until /api/coaching answers — counting a
    visit against household 'x' would spend a real household's three."""
    script = (
        _DOM_STUB + _examples_block() + """
coachOnTabShown('today');
console.log(JSON.stringify({ html: ELS['ask-examples'].innerHTML, stored: STORE }));
"""
    )
    out = _node(script)
    assert out["html"] == "" and out["stored"] == {}


@_needs_node
def test_the_card_renders_only_for_a_household_with_a_plan_that_has_not_read_it():
    script = (
        _DOM_STUB + _card_block() + """
const coachState = { ready: false, hasPlan: false, seen: true };
function run(state) {
  Object.assign(coachState, state);
  ELS['coach-card-slot'] = makeEl();
  renderCoachCard();
  return ELS['coach-card-slot'].innerHTML.indexOf('Tap for the usual. Type for the rest.') !== -1;
}
console.log(JSON.stringify({
  notReady: run({ ready: false, hasPlan: true, seen: false }),
  noPlan: run({ ready: true, hasPlan: false, seen: false }),
  alreadyRead: run({ ready: true, hasPlan: true, seen: true }),
  due: run({ ready: true, hasPlan: true, seen: false })
}));
"""
    )
    assert _node(script) == {"notReady": False, "noPlan": False, "alreadyRead": False, "due": True}


@_needs_node
def test_the_sheet_says_its_three_lines_and_offers_two_ways_out():
    """Since 2026-09-11 (Build 2 of the screen-by-screen redesign) this is a
    SHEET shown once, not a card on Now: Emily cut the card ("'a quick word,
    once' — what does that even mean? Remove it") and the copy with it.
    Title and lines are the proposed draft from the copy document; they
    change there, not here, when she hands it back."""
    script = (
        _DOM_STUB + _card_block() + """
console.log(JSON.stringify(coachCardHtml()));
"""
    )
    html = _node(script)
    assert "A QUICK WORD" not in html
    assert 'id="coach-sheet"' in html and 'id="coach-scrim"' in html
    assert "Tap for the usual. Type for the rest." in html
    # Tightened 2026-09-11 (copy cleanse): fewest words that still teach the
    # three things, in the welcome flow's own register.
    for line in [
        "Buttons do the everyday things.",
        "Everything else, type in the chat.",
        "If I get it wrong, say so there.",
    ]:
        assert line in html, line
    assert 'data-coach="got-it"' in html and ">Got it<" in html
    assert 'data-coach="tips"' in html and ">More tips<" in html
    # The sheet's Got it is its one apricot (a sheet has no other), and the
    # scrim dismisses too.
    assert 'class="dock-primary coach-got-it"' in html
    assert '<div id="coach-scrim" data-coach="got-it"></div>' in html


@_needs_node
def test_the_tips_sheet_is_one_screen_of_at_most_ten_lines():
    script = (
        _DOM_STUB + _tips_block() + """
console.log(JSON.stringify({
  opening: TIPS_OPENING,
  groups: TIPS_GROUPS,
  closers: TIPS_CLOSERS,
  after: TIPS_AFTER_SEND
}));
"""
    )
    tips = _node(script)
    lines = 1 + len(tips["groups"]) + len(tips["closers"]) + 1
    assert lines <= 10, f"the tips sheet is {lines} lines"
    # Renamed 2026-09-09 (Emily): the tips sheet names each tab, so it
    # follows the tab bar. Words only — the keys behind them are unchanged.
    assert [g["tab"] for g in tips["groups"]] == ["Now", "Plan", "Shop", "Cook"]
    assert [g["example"] for g in tips["groups"]] == [
        "What’s next tonight?",
        "Swap Thursday for something lighter",
        "Add oat milk and lemons",
        "What can I make with the chicken thighs?",
    ]
    assert tips["after"] == (
        "I’ll say what changed, and the screen updates. If I couldn’t, I’ll say that too."
    )
    # The second sentence ("There's no right way to phrase it.") restated the
    # first and was cut 2026-09-11 (copy cleanse); so was the closer that
    # repeated the coach card's title.
    assert tips["opening"] == "Say it however it comes out."
    assert tips["closers"] == ["The more you tell me about your week, the better the plan fits."]


# --- 3. the wiring that has no pure function to run -----------------------

def test_the_preferences_sheet_carries_the_way_back_to_the_tips():
    assert 'data-tips="open"' in SHELL_JS
    assert "<span class=\"prefs-row-title\">Helpful tips</span>" in SHELL_JS
    assert "How to ask me for things" in SHELL_JS


def test_the_ask_bar_carries_a_small_way_into_the_tips():
    # In the sheet's markup, in its title row since the chat became an icon
    # (2026-09-11). A second copy used to be written by buildTodayPanel for
    # the desktop Ask column; that column (and its own "?" button) is gone
    # with the rest of the old desktop rail/column shell.
    assert 'id="ask-tips-btn"' in SHELL_HTML
    assert 'aria-label="Helpful tips"' in SHELL_HTML
    assert SHELL_JS.count('class="ask-tips-btn" data-tips="open"') == 0
    assert SHELL_HTML.count('id="ask-tips-btn"') == 1


def test_the_examples_row_exists():
    assert 'id="ask-examples"' in SHELL_HTML
    # The desktop Ask column's own copy (#today-ask-examples, written by
    # buildTodayPanel) is gone — the ask sheet is the only ask surface now.
    assert 'today-ask-examples' not in SHELL_JS


def test_a_tab_switch_counts_a_visit():
    """The counter hangs off activateTab, not off each build*Panel — a tab
    you come BACK to is already built and is still a visit."""
    activate = SHELL_JS[SHELL_JS.index("    setAskHintForTab(key);"):]
    assert "coachOnTabShown(key);" in activate[:600]


def test_the_first_message_of_the_households_own_clears_the_examples():
    hide = SHELL_JS[SHELL_JS.index("  function hideAskChips() {"):]
    assert "renderAskExamples(null);" in hide[: hide.index("\n  }")]


def test_a_flex_chips_row_can_actually_be_hidden():
    """.ask-chips is display:flex, which beats the bare [hidden] attribute —
    the same trap #reveal-days hit on the onboarding branch."""
    assert ".ask-chips[hidden] { display: none; }" in SHELL_CSS


def test_the_tips_sheet_reuses_the_preferences_sheets_own_rules():
    # The selector group grew a third member on 2026-09-11 — the "Morning
    # text" sheet ("Reach me before the moment") shares the same frame.
    assert "#prefs-scrim,\n#tips-scrim,\n#morning-scrim {" in SHELL_CSS
    assert "#prefs-sheet,\n#tips-sheet,\n#morning-sheet {" in SHELL_CSS
