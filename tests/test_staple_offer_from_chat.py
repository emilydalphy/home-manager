"""
"Something you run out of, mentioned in chat, is offered as a staple"
(Loop Board, Medium, Phase 1.5 -- Paid-ready, Improvement).

Observed 2026-09-15: "add dish soap to the list" -> "Dish soap is on your
list." + a Grocery updated card, and nothing else -- no offer to keep an
eye on it, so the same gap notices it again in six weeks.

The fix lives in app/tools/staples.py:
  - `_is_supply` reuses section_for's own pantry/household classifier to
    tell a running-low SUPPLY (dish soap, coffee, toilet paper) from a
    recipe ingredient for a specific meal (shrimp, parsley) -- never a
    separate keyword list.
  - `offer_for_chat_grocery_add` decides whether THIS grocery line is
    worth asking about, and marks it asked (grocery_items.
    staple_offer_made) in the same breath so it is never asked twice for
    the same line, on any answer (yes, no, or silence).
  - `add_grocery_item_for_chat` is the chat tool's own add_grocery_item --
    identical to grocery.add_grocery_item, plus the `staple_offer` key.
    The Shop tab's own add route still calls grocery.add_grocery_item
    directly and is untouched.

app/main.py's `_staple_offer_from_turn` (mirroring `_proposal_from_turn`)
pulls the last genuine offer out of a chat turn's tool results onto
ChatResponse.staple_offer, which is None whenever the item was already a
staple -- there is nothing to tap in that case, only a sentence to say.
"""
from __future__ import annotations

import json
import shutil
import types
from pathlib import Path

import nodeharness
import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import staples as st
from app import main as app_main

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def _staples_count() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM staples").fetchone()[0]
    conn.close()
    return n


def _staple_offer_made(item_id: int) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT staple_offer_made FROM grocery_items WHERE id = ?", (item_id,)
    ).fetchone()
    conn.close()
    return bool(row["staple_offer_made"])


# --------------------------------------------------------- _is_supply ----


def test_household_item_is_a_supply():
    assert st._is_supply("Dish soap", "household") is True


def test_pantry_basic_is_a_supply():
    assert st._is_supply("Coffee", "pantry") is True


def test_fridge_category_ingredient_is_not_a_supply():
    assert st._is_supply("Shrimp", "meat/seafood") is False
    assert st._is_supply("Parsley", "produce") is False


def test_spice_is_not_offered_here_it_already_staples_itself():
    # seed_spice_staples/add_grocery_item already turns a spice into its
    # own staple the moment it's added (2026-09-13 decision) -- offering
    # it again here would be a second, redundant ask.
    assert st._is_supply("Cumin", "pantry") is False


# ------------------------------------------------- add_grocery_item_for_chat ----


def test_supply_item_with_no_staple_is_offered():
    out = tools.add_grocery_item_for_chat("Dish soap", category="household")
    assert out["staple_offer"] == {"item": "Dish soap", "already_staple": False}
    assert _staple_offer_made(out["item_id"]) is True
    # The offer is a question, not a write -- nothing is on the staples
    # table just because it was asked about.
    assert _staples_count() == 0


def test_supply_item_already_a_staple_says_so_and_adds_nothing_twice():
    tools.add_staple("Dish soap", category="household")
    assert _staples_count() == 1

    out = tools.add_grocery_item_for_chat("dish soap", category="household")

    assert out["staple_offer"] == {"item": out["item"], "already_staple": True}
    assert _staples_count() == 1, "already being watched -- nothing duplicated"


def test_recipe_ingredient_is_never_offered():
    shrimp = tools.add_grocery_item_for_chat("Shrimp", category="meat/seafood")
    parsley = tools.add_grocery_item_for_chat("Parsley", category="produce")
    assert "staple_offer" not in shrimp
    assert "staple_offer" not in parsley


def test_offer_is_asked_once_per_grocery_line():
    first = tools.add_grocery_item_for_chat("Dish soap", category="household")
    assert "staple_offer" in first

    # Same line (still 'needed'), asked about again in the same message,
    # or a different message later in the same trip -- merges into the
    # same row and must not raise it a second time.
    again = tools.add_grocery_item_for_chat("Dish soap", category="household")
    assert again["item_id"] == first["item_id"]
    assert "staple_offer" not in again


def test_a_new_trip_after_the_line_is_bought_can_be_offered_again():
    first = tools.add_grocery_item_for_chat("Dish soap", category="household")
    tools.mark_grocery_item(first["item_id"], status="purchased")

    # Bought and off the list -- a fresh mention starts a brand new line,
    # which has never been asked about.
    second = tools.add_grocery_item_for_chat("Dish soap", category="household")
    assert second["item_id"] != first["item_id"]
    assert second["staple_offer"] == {"item": "Dish soap", "already_staple": False}


def test_declining_or_ignoring_the_offer_changes_nothing():
    out = tools.add_grocery_item_for_chat("Dish soap", category="household")
    item_id = out["item_id"]
    # "No answer, or No, changes nothing" -- there is no decline tool to
    # call; the offer having been raised is itself what suppresses the
    # next ask (see test above). Confirm the grocery line and the
    # (non-)existence of a staple are exactly as a plain add would leave
    # them.
    assert _staples_count() == 0
    row = tools.list_grocery_list(status="needed")
    assert [r["item"] for r in row] == ["Dish soap"]
    assert _staple_offer_made(item_id) is True


def test_the_yes_path_creates_the_staple_exactly_like_the_staples_card():
    out = tools.add_grocery_item_for_chat("Dish soap", category="household")
    assert out["staple_offer"]["already_staple"] is False

    # "Yes, keep an eye on dish soap." -> the agent calls add_staple, same
    # as the Staples card's own add flow.
    created = tools.add_staple("Dish soap", category="household")
    assert created["created"] is True
    assert _staples_count() == 1
    # Already on the list this trip (added directly, not because it's
    # "due"), so the cadence starts fresh rather than firing again
    # immediately -- see add_staple's own running_low default.
    assert created["due"] is False


def test_shop_tab_route_is_never_offered_anything():
    """grocery.add_grocery_item -- what the direct-add HTTP route calls --
    must behave exactly as before: no staple_offer key, ever, for any
    caller other than the chat tool wrapper."""
    out = tools.add_grocery_item("Dish soap", category="household")
    assert "staple_offer" not in out
    # And a second Shop-tab add of the very same supply item never
    # retroactively grows one either.
    out2 = tools.add_grocery_item("Dish soap", category="household")
    assert "staple_offer" not in out2


# ------------------------------------------- app.main: turn -> ChatResponse ----


def _turn_with_tool_result(name: str, args: dict, result: dict):
    before = [{"role": "user", "content": "add dish soap"}]
    after = before + [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": name, "input": args},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": json.dumps(result), "is_error": False},
        ]},
        {"role": "assistant", "content": [{"type": "text", "text": "On the list."}]},
    ]
    return before, after


def test_staple_offer_from_turn_surfaces_a_genuine_offer():
    result = {"item_id": 1, "item": "Dish soap", "quantity": "", "merged": False,
              "units_reconciled": True, "staple_offer": {"item": "Dish soap", "already_staple": False}}
    before, after = _turn_with_tool_result("add_grocery_item", {"item": "Dish soap"}, result)
    assert app_main._staple_offer_from_turn(before, after) == {"item": "Dish soap"}


def test_staple_offer_from_turn_is_none_when_already_a_staple():
    result = {"item_id": 1, "item": "Dish soap", "quantity": "", "merged": False,
              "units_reconciled": True, "staple_offer": {"item": "Dish soap", "already_staple": True}}
    before, after = _turn_with_tool_result("add_grocery_item", {"item": "Dish soap"}, result)
    assert app_main._staple_offer_from_turn(before, after) is None


def test_staple_offer_from_turn_is_none_with_no_offer_at_all():
    result = {"item_id": 1, "item": "Shrimp", "quantity": "", "merged": False, "units_reconciled": True}
    before, after = _turn_with_tool_result("add_grocery_item", {"item": "Shrimp"}, result)
    assert app_main._staple_offer_from_turn(before, after) is None


# ---------------------------------------------------------- end to end ----


class _Usage:
    def __init__(self, output_tokens=0):
        self.input_tokens = 0
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0
        self.output_tokens = output_tokens


def _text_block(text):
    return types.SimpleNamespace(type="text", text=text)


def _tool_block(name, tool_input, block_id="tu_1"):
    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        return self._responses.pop(0)


def _stub_client(monkeypatch, responses):
    fake = types.SimpleNamespace(messages=_FakeMessages(responses))
    monkeypatch.setattr(agent, "_client", lambda: fake)


def test_the_real_tool_mapping_reaches_the_chat_wrapper():
    # The whole point of swapping the TOOLS entry (app/agent.py) is that
    # Claude's tool name never changes -- only what it dispatches to.
    assert agent.TOOL_FUNCTIONS["add_grocery_item"] is tools.add_grocery_item_for_chat


def test_end_to_end_chat_turn_carries_the_staple_offer(signed_in, monkeypatch):
    _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[_tool_block("add_grocery_item", {"item": "Dish soap", "category": "household"})],
            stop_reason="tool_use",
            usage=_Usage(),
        ),
        types.SimpleNamespace(
            content=[_text_block("On the list. Want me to keep an eye on dish soap so you don't have to?")],
            stop_reason="end_turn",
            usage=_Usage(),
        ),
    ])

    res = signed_in.post("/api/chat", json={"session_id": "default", "message": "add dish soap to the list"})
    assert res.status_code == 200
    assert res.json()["staple_offer"] == {"item": "Dish soap"}


def test_end_to_end_chat_turn_has_no_offer_for_a_recipe_ingredient(signed_in, monkeypatch):
    _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[_tool_block("add_grocery_item", {"item": "Shrimp", "category": "meat/seafood"})],
            stop_reason="tool_use",
            usage=_Usage(),
        ),
        types.SimpleNamespace(
            content=[_text_block("Shrimp is on your list.")],
            stop_reason="end_turn",
            usage=_Usage(),
        ),
    ])

    res = signed_in.post("/api/chat", json={"session_id": "default", "message": "add shrimp"})
    assert res.status_code == 200
    assert res.json()["staple_offer"] is None


def test_the_yes_message_reaches_add_staple_and_creates_it_through_the_real_tool(signed_in, monkeypatch):
    """
    Verification (2026-09-15): the Yes -> add_staple binding lived only in
    prose (the system-prompt bullet above), untested. add_staple's own
    tool description (app/agent.py) now spells out that a "Yes, keep an
    eye on <item>." message -- the exact text the chip sends, see
    static/shell.js's offerNextStepChips -- means call add_staple for that
    item. This stubs only the model's decision to call the tool (the
    scripted response below stands in for what that description is meant
    to make the real model do); everything downstream -- TOOL_FUNCTIONS
    dispatch, tools.add_staple itself, the database write -- is real.
    """
    _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[_tool_block("add_staple", {"item": "Dish soap", "category": "household"})],
            stop_reason="tool_use",
            usage=_Usage(),
        ),
        types.SimpleNamespace(
            content=[_text_block("I'll watch for it.")],
            stop_reason="end_turn",
            usage=_Usage(),
        ),
    ])

    res = signed_in.post(
        "/api/chat", json={"session_id": "default", "message": "Yes, keep an eye on dish soap."}
    )

    assert res.status_code == 200
    assert res.json()["reply"] == "I'll watch for it."
    staples = tools.list_staples()
    assert [s["item"] for s in staples] == ["Dish soap"]


# --------------------------------------------- the front end, run rather than read ----
#
# Verification (2026-09-15): a "Yes, keep an eye on dish soap." chip
# persisted across later turns that offered no chip of their own, because
# renderAskChips only ever ADDS to #ask-chips and offerNextStepChips's own
# `if (chips.length) renderAskChips(chips)` skipped calling it at all when
# there was nothing new. A tap days later sent that stale literal message
# to the model. Fixed by clearing #ask-chips unconditionally at the top of
# offerNextStepChips, before that length guard -- see static/shell.js.
#
# Node, not source-marker: the bug is about what's left in the DOM across
# two calls, which no grep for a string can see (test_coaching.py's own
# "NODE" tests are the house pattern for exactly this).

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _chip_functions_block() -> str:
    """renderAskChips through offerNextStepChips, in that order in the
    file, with nothing else pulled in -- computeNextStepChips (which
    offerNextStepChips calls) sits between them and comes along for free.
    askChipTargets is defined much further down the file (it's shared
    with the message/input targets beside it) so the stub below defines
    the one-line equivalent itself rather than slicing a second,
    non-contiguous region out of the file."""
    start = "  function renderAskChips(actions) {"
    end = "  function focusChangedWeekDay(date, slot) {"
    a = SHELL_JS.index(start)
    b = SHELL_JS.index(end, a)
    return SHELL_JS[a:b]


# A DOM stub in the same shape test_coaching.py's own _DOM_STUB uses:
# innerHTML/hidden set by the code under test, read back by regex rather
# than a real parser -- honest about the two things renderAskChips
# actually does to an element.
_CHIP_DOM_STUB = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
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
const ELS = { 'ask-chips': makeEl(), 'ask-examples': null };
const document = { getElementById: function (id) { return ELS[id] || null; } };
var askChipsEl = document.getElementById('ask-chips');
function askChipTargets() { return askChipsEl ? [askChipsEl] : []; }
var SENT = [];
function sendAskMessage(t) { SENT.push(t); }
"""


@_needs_node
def test_a_staple_offer_chip_does_not_survive_a_later_turn_with_no_chips_of_its_own():
    script = (
        _CHIP_DOM_STUB + _chip_functions_block() + """
var out = {};
offerNextStepChips([], { item: 'dish soap' });
out.withOffer = { hidden: ELS['ask-chips'].hidden, labels: ELS['ask-chips'].labels() };
// The very next turn asks nothing and offers no action chip either --
// the "Yes" chip from the turn above must be gone, not just unreachable.
offerNextStepChips([], null);
out.nextTurn = { hidden: ELS['ask-chips'].hidden, labels: ELS['ask-chips'].labels(), html: ELS['ask-chips'].innerHTML };
console.log(JSON.stringify(out));
"""
    )
    out = _node(script)
    assert out["withOffer"] == {"hidden": False, "labels": ["Yes"]}
    assert out["nextTurn"] == {"hidden": True, "labels": [], "html": ""}


@_needs_node
def test_a_staple_offer_chip_does_not_survive_a_later_turn_that_has_its_own_chip():
    """Not just cleared to nothing -- replaced. A later turn that DOES have
    an action chip (e.g. "Plan my stops") must show only its own, not the
    earlier "Yes" beside or before it."""
    script = (
        _CHIP_DOM_STUB + _chip_functions_block() + """
offerNextStepChips([], { item: 'dish soap' });
var groceryAction = { tab: 'grocery', change: 'Added milk' };
offerNextStepChips([groceryAction], null);
console.log(JSON.stringify({ hidden: ELS['ask-chips'].hidden, labels: ELS['ask-chips'].labels() }));
"""
    )
    assert _node(script) == {"hidden": False, "labels": ["Plan my stops"]}


@_needs_node
def test_the_staple_offer_chip_still_renders_and_sends_the_expected_message():
    """The fix must only clear stale chips, not the offer's own -- same
    behaviour as before the fix for the turn that actually carries one."""
    script = (
        _CHIP_DOM_STUB + _chip_functions_block() + """
offerNextStepChips([], { item: 'dish soap' });
var chip = ELS['ask-chips'].querySelectorAll('.ask-chip')[0];
ELS['ask-chips'].handlers[chip.dataset.i]();
console.log(JSON.stringify({ labels: ELS['ask-chips'].labels(), sent: SENT }));
"""
    )
    assert _node(script) == {"labels": ["Yes"], "sent": ["Yes, keep an eye on dish soap."]}
