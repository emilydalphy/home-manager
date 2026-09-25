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
  2. **A "Helpful tips" sheet**, behind a Preferences row and a "?" beside
     the ask bar.

There used to be a third — one how-and-why sheet the first time the shell
opened after setup, dismissed once per household on the server
(`households.coaching_seen_at`). It went on 2026-09-18 with the Week 1
"Need a hand?" sheet (static/help-sheet.js, tests/test_help_sheet.py);
the column stays in the schema (retired, never dropped) but nothing reads
or writes it, and /api/coaching no longer reports it.

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

import nodeharness
from pathlib import Path

import pytest

from app import tools
from app.db import _run_migrations, get_conn


REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


# --- 1. the route ----------------------------------------------------------

def test_the_retired_column_is_still_there_but_nothing_reports_it():
    """The migration list is append-only (a column is never dropped), so
    coaching_seen_at survives — and /api/coaching stopped saying it."""
    conn = get_conn()
    _run_migrations(conn)
    conn.commit()
    names = [row["name"] for row in conn.execute("PRAGMA table_info(households)")]
    conn.close()
    assert names.count("coaching_seen_at") == 1
    assert "coaching_seen_at" not in tools.get_coaching_state()


def test_a_household_fresh_out_of_setup(signed_in):
    state = signed_in.get("/api/coaching").json()
    assert state["household_id"] == 1
    assert state["has_plan"] is False


def test_has_plan_says_whether_setup_actually_finished(signed_in):
    tools.create_weekly_plan("2026-09-07")
    assert signed_in.get("/api/coaching").json()["has_plan"] is True


def test_the_dismissal_route_is_gone(signed_in):
    assert signed_in.post("/api/coaching/seen").status_code in (404, 405)
    assert not hasattr(tools, "mark_coaching_seen")


# --- 2. the front end, run rather than read -------------------------------

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
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
const ELS = { 'ask-examples': makeEl(), 'today-ask-examples': makeEl() };
const document = { getElementById: function (id) { return ELS[id] || null; } };
let askConversationStarted = false;
const SENT = [], OPENED = [];
function openAskSheet(p) { OPENED.push(p === undefined ? null : p); }
function sendAskMessage(t) { SENT.push(t); }
"""


def _add_adult(name: str) -> int:
    """An adult, spelled the way onboarding spells it — "Adult", capitalised."""
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, "Adult")
    return member_id


def _examples_block() -> str:
    return _slice("var COACH_VISITS_TO_SHOW = 3;", "  // ---------- \"Morning text\" ----------")


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
        "today": ["What should I cook tonight?", "Swap tonight for something quicker", "We’re nearly out of…"],
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
def test_the_away_example_speaks_in_first_person_when_the_only_adult_is_you():
    """
    /api/coaching sets example_is_you when the household's one adult is the
    one signed in (household.get_coaching_state) — the chip should then
    read "I'm out Thursday", not "Emily is out Thursday" back at Emily.
    """
    script = (
        _DOM_STUB + _examples_block() + """
coachState.ready = true;
coachState.householdId = 1;
coachState.exampleName = 'Emily';
coachState.exampleIsYou = true;
coachOnTabShown('week');
console.log(JSON.stringify(ELS['ask-examples'].labels()));
"""
    )
    assert _node(script) == ["Plan the rest of my week", "Less chicken this week", "I’m out Thursday"]


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


def test_the_example_names_the_other_adult_when_someone_is_signed_in():
    """
    Reading the signed-in adult's own name back to them as "X is out
    Thursday" is the same mistake the hardcoded name was, just scoped to
    the household instead of the whole beta — the chip should be about
    somebody ELSE having plans.
    """
    emily = _add_adult("Emily")
    marcus = _add_adult("Marcus")
    with tools.use_member(emily):
        state = tools.get_coaching_state()
        assert state["example_name"] == "Marcus"
        assert state["example_is_you"] is False
    with tools.use_member(marcus):
        state = tools.get_coaching_state()
        assert state["example_name"] == "Emily"
        assert state["example_is_you"] is False


def test_a_lone_adult_is_their_own_example_and_flagged_as_you():
    """
    With exactly one adult, current_member() resolves to them with no
    session pick needed (see _shared.current_member) — so the example is
    always that adult, and example_is_you tells the shell to say "I'm out
    Thursday" rather than naming them in the third person.
    """
    _add_adult("Emily")
    state = tools.get_coaching_state()
    assert state["example_name"] == "Emily"
    assert state["example_is_you"] is True


def test_no_member_picked_with_two_adults_keeps_the_lowest_id_rule():
    """A script or tool call with nobody chosen (member_id() is None and
    more than one adult, so current_member() can't resolve one either)
    falls back to today's rule rather than guessing who's "you"."""
    _add_adult("Emily")
    _add_adult("Marcus")
    with tools.use_member(None):
        state = tools.get_coaching_state()
        assert state["example_name"] == "Emily"
        assert state["example_is_you"] is False


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
        "What should I cook tonight?", "Swap tonight for something quicker", "We’re nearly out of…"
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
def test_todays_two_complete_sentence_chips_still_send_unchanged():
    """Today's third slot became a fill-in-the-blank starter 2026-09-15 (see
    the test below); its other two chips are complete requests on their own
    and must keep sending exactly as before."""
    script = (
        _DOM_STUB + _examples_block() + """
coachState.ready = true;
coachState.householdId = 1;
coachOnTabShown('today');
ELS['ask-examples'].handlers[0]();
ELS['ask-examples'].handlers[1]();
console.log(JSON.stringify({ sent: SENT, opened: OPENED.length }));
"""
    )
    out = _node(script)
    assert out["sent"] == ["What should I cook tonight?", "Swap tonight for something quicker"]
    assert out["opened"] == 2


@_needs_node
def test_the_held_thing_chip_fills_the_box_instead_of_sending():
    """
    Loop Board: "Ask: the door says 'hold this', not 'meal edits'"
    (2026-09-15). Today's third example, "We're nearly out of…", is
    deliberately not a complete sentence — only the household knows what
    they're low on — so tapping it must insert a starter into the composer
    (openAskSheet's prefill argument, ASK_EXAMPLE_INSERT_ONLY in shell.js)
    rather than send the unfinished sentence as a message on its own.
    """
    script = (
        _DOM_STUB + _examples_block() + """
coachState.ready = true;
coachState.householdId = 1;
coachOnTabShown('today');
ELS['ask-examples'].handlers[2]();
console.log(JSON.stringify({ sent: SENT, opened: OPENED }));
"""
    )
    out = _node(script)
    assert out["sent"] == [], "the held-thing chip must not send anything on its own"
    assert out["opened"] == ["We’re nearly out of "], (
        "tapping it must insert a starter into the box for the household to finish"
    )


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


def test_the_one_time_sheet_is_gone():
    """Neither the card on Now nor the sheet that replaced it: the Week 1
    "Need a hand?" sheet says the same things where they are needed."""
    for marker in ("coachCardHtml", "renderCoachCard", "COACH_CARD_LINES", "data-coach=",
                   "coachState.seen", "Tap for the usual. Type for the rest."):
        assert marker not in SHELL_JS, marker
    assert "coach-card-slot" not in SHELL_HTML
    assert "#coach-sheet" not in SHELL_CSS and ".coach-line" not in SHELL_CSS


@_needs_node
def test_the_tips_sheet_is_one_screen_of_at_most_ten_lines():
    """Mockup "6A" (Emily-approved 2026-09-25, with her correction that Cook
    follows the week's own recipes rather than deciding what to cook): one
    opening line, four groups (a headline plus one example each), and the
    closer/AFTER YOU SEND lines are gone — replaced by the help card, which
    isn't a copy line so it doesn't count toward the budget."""
    script = (
        _DOM_STUB + _tips_block() + """
console.log(JSON.stringify({
  opening: TIPS_OPENING,
  groups: TIPS_GROUPS
}));
"""
    )
    tips = _node(script)
    lines = 1 + len(tips["groups"])
    assert lines <= 10, f"the tips sheet is {lines} lines"
    # Renamed 2026-09-09 (Emily): the tips sheet names each tab, so it
    # follows the tab bar (and the first tab back to Today, 2026-09-17).
    # Words only — the keys behind them are unchanged.
    assert [g["tab"] for g in tips["groups"]] == ["Today", "Plan", "Shop", "Cook"]
    assert [g["headline"] for g in tips["groups"]] == [
        "Check what’s next",
        "Change the week",
        "Add or remove things",
        "Follow tonight’s recipe",
    ]
    assert [g["example"] for g in tips["groups"]] == [
        "What’s for dinner tonight?",
        "Swap Thursday for something lighter",
        "Add oat milk and lemons",
        "How long does the chicken go in for?",
    ]
    assert tips["opening"] == "Tap the Pomona button on any screen, then type or talk."


def test_the_tips_sheet_ends_with_the_help_card_not_an_email_address():
    """The mockup's bottom card opens the existing "Something not working?"
    form (the same POST /api/feedback path every other entry point uses),
    tagged with this sheet's own name — and never shows or links an email
    address (Emily: in-app submission only)."""
    assert 'class="tips-help-row" data-snw="open" data-snw-screen="Helpful tips"' in SHELL_JS
    assert "Need help with something?" in SHELL_JS
    assert "Send Emily a note" in SHELL_JS
    tips_sheet = SHELL_JS[SHELL_JS.index("function buildTipsSheet"):SHELL_JS.index("function openTipsSheet")]
    assert "@" not in tips_sheet


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
