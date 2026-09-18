"""The cook-ahead ask asks the plain question (Emily, 2026-09-13).

Loop Board "Cook-ahead ask: ask the plain question". From her phone, on
the week-approval receipt: "what is this supposed to mean by the way the
question is being asked it's confusing." The block read "Roasted Chickpeas
is on 5 nights. Cook ahead?" over a row of day chips and "Makes 2 nights ·
for 4" — and she couldn't tell what tapping a chip did, because the night
the batch actually cooks on (the first of the run) was never named, so
the chips read as "pick a night to cook", the opposite of what they do.

Her rewrite, now DESIGN_SYSTEM §8 Sounding-human rule 7 ("Clear beats
warm"): *"Do you want to batch cook this? Which meals should be
included?"* — the question, then the choice, then the buttons that
commit, nothing decorative in between.

These run shell.js's own functions under node (tests/nodeharness.py) so
they see the words a phone would, not markers in the source.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from tests import nodeharness

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


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


_PRELUDE = (
    _function("escapeHtml") + "\n"  # the real one: it turns ' into &#39;, and the question has one
    "function dayName(dateStr, opts){ return new Date(dateStr + 'T00:00:00').toLocaleDateString('en-US', opts); }\n"
    "function dayNameShort(iso){ return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short' }); }\n"
    "var cookAheadAskState = { planId: null, items: null, picks: {}, forceShow: false };\n"
    "var cookAheadComponentState = { components: [], picks: {} };\n"
    "var cookState = { cookAheadPicks: {} };\n"
    + _function("cookSlotWord") + "\n"
    + _function("cookAheadAskPicks") + "\n"
    # The shared-component block shares the fold (batch_components.py).
    + _function("cookAheadComponents") + "\n"
    + _function("cookAheadComponentPicks") + "\n"
    + _function("cookAheadComponentRecipeCount") + "\n"
    + _function("cookAheadComponentPlural") + "\n"
    + _function("cookAheadComponentRepeatLine") + "\n"
    + _function("cookAheadComponentQuestion") + "\n"
    + _function("cookAheadComponentTallyLine") + "\n"
    + _function("cookAheadComponentBlockHtml") + "\n"
    + _function("cookAheadComponentBlocksHtml") + "\n"
    + _function("cookAheadComponentReset") + "\n"
    + _function("cookAheadRepeatLine") + "\n"
    + _function("cookAheadTallyLine") + "\n"
    + _function("cookAheadAskBlockHtml") + "\n"
    # Two or more things to batch is one line each (2026-09-15) — the card
    # reaches these whenever it is not drawing a lone block.
    + _function("cookAheadPickHtml") + "\n"
    + _function("cookAheadDishPickHtml") + "\n"
    + _function("cookAheadComponentPickHtml") + "\n"
    + _function("cookAheadPickLinesHtml") + "\n"
    + _function("cookAheadPrimePicks") + "\n"
    + _function("cookAheadAskCardHtml") + "\n"
    + _function("cookAheadAskQuestion") + "\n"
    + _function("cookAheadPicks") + "\n"
    + _function("cookAheadHtml") + "\n"
)


def _node(script: str):
    res = nodeharness.run_node(_PRELUDE + script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


MON, TUE, THU, SAT, SUN = "2026-09-14", "2026-09-15", "2026-09-17", "2026-09-19", "2026-09-20"


def _item(dish="Roasted Chickpeas", slot="dinner", eaters=2, later_dates=(TUE, THU, SAT, SUN), first=MON):
    return {
        "dish": dish, "slot": slot,
        "first": {"entry_id": 1, "date": first, "eaters": eaters},
        "later": [{"entry_id": 10 + i, "date": d, "eaters": eaters} for i, d in enumerate(later_dates)],
    }


def _component(label="Boiled eggs", ingredient="eggs", quantity="6",
               uses=((TUE, "Hard-Boiled Eggs and Avocado Toast"), (THU, "Egg Salad Sandwiches"), (SAT, "Hard-Boiled Eggs and Avocado Toast"))):
    """A component two different recipes cook the same way — the shape
    tools/batch_components.py hands over beside `items`."""
    return {
        "key": "boiled:egg", "label": label, "ingredient": ingredient, "quantity": quantity,
        "uses": [{"entry_id": 200 + i, "date": d, "slot": "breakfast", "dish": dish} for i, (d, dish) in enumerate(uses)],
        "first": {"entry_id": 200, "date": uses[0][0]},
    }


# ---------- the block: question, chips, tally, in that order ----------

@_needs_node
def test_one_dish_opens_with_the_plain_question_and_names_the_cook_night():
    out = _node(
        "var items = [%s]; cookAheadAskState.items = items;"
        "console.log(JSON.stringify({q: cookAheadAskQuestion(), card: cookAheadAskCardHtml(true)}));"
        % json.dumps(_item())
    )
    assert out["q"] == "Roasted Chickpeas is on 5 nights. Do you want to batch cook it?"
    card = out["card"]
    # The lone block does not repeat the dish line the heading just said…
    assert 'class="ca-ask-line"' not in card
    # …and opens with the choice, naming the night the batch happens.
    assert "It&#39;d cook on Monday. Which other nights should it cover?" in card
    # Order on screen: question, chips, tally, then the buttons that commit.
    q = card.index('class="ca-ask-q"')
    chips = card.index('class="ca-ask-days"')
    tally = card.index('class="ca-ask-count"')
    buttons = card.index('id="cook-ahead-ask-confirm"')
    assert q < chips < tally < buttons
    # Nothing decorative between the heading and the question.
    assert "wk-quick-body-line" not in card
    assert "Tick the days" not in card
    # The buttons answer the question in its own word.
    assert ">Batch cook these<" in card and ">Cook each on its own<" in card
    # The chips are the four later nights, none ticked to start.
    assert card.count('class="ca-ask-day"') == 4 and "is-on" not in card


@_needs_node
def test_several_dishes_share_the_heading_and_are_one_line_each():
    """WHAT MOVED, 2026-09-15: this used to assert that each dish got its
    own block — its own "Which other nights should it cover?" and its own
    row of day chips. That is the thing Emily reported: approving her week
    asked nine of them. Several dishes are one line each now, under the
    one heading, answered by the one pair of buttons
    (tests/test_after_approve_real_questions.py owns the new shape). What
    this test still pins is unchanged: the heading is shared, both dishes
    are named, and the heading's own sentence is never said twice."""
    items = [_item(), _item(dish="Egg Bites", slot="breakfast", later_dates=(TUE, THU))]
    for i, it in enumerate(items):
        it["first"]["entry_id"] = 100 + i
    out = _node(
        "var items = %s; cookAheadAskState.items = items; cookAheadPrimePicks();"
        "console.log(JSON.stringify({q: cookAheadAskQuestion(), card: cookAheadAskCardHtml(true)}));"
        % json.dumps(items)
    )
    assert out["q"] == "Do you want to batch cook any of these?"
    card = out["card"]
    assert card.count('class="ca-ask-day ca-ask-pick') == 2
    assert card.index("Roasted Chickpeas") < card.index("Egg Bites")
    assert "should it cover?" not in card
    # The heading's sentence is never said twice.
    assert "Do you want to batch cook" not in card


# ---------- a shared component asks in the same shapes ----------

@_needs_node
def test_a_component_block_reads_like_a_dish_block():
    """Boiled eggs in two recipes (batch_components) sit in the same fold
    and say it the same way: the fact, the question naming the cook day,
    the chips, the tally — and the chips start ticked, because the
    question is "all at once?" and a chip is for leaving one out."""
    # A component ALONE, since 2026-09-15: with a dish beside it the fold
    # is one line each and neither block draws (the new shape has its own
    # file). The block itself is untouched, which is what this pins.
    out = _node(
        "cookAheadAskState.items = []; cookAheadComponentReset([%s]);"
        "console.log(JSON.stringify(cookAheadAskCardHtml(true)));" % json.dumps(_component())
    )
    assert "They&#39;d cook on Tuesday. Which meals should be included?" in out
    assert "One cook on Tuesday for 3 meals · 6 eggs" in out
    assert out.count('data-ca-use="') == 3 and out.count('ca-comp-dish is-on') == 3
    # A singular component keeps its number through the sentences.
    out = _node(
        "cookAheadAskState.items = []; cookAheadComponentReset([%s]);"
        "console.log(JSON.stringify({q: cookAheadAskQuestion(), card: cookAheadAskCardHtml(true)}));"
        % json.dumps(_component(label="Cooked rice", ingredient="rice", quantity="3 cups"))
    )
    assert out["q"] == "Cooked rice is in 2 recipes this week. Do you want to batch cook it?"
    assert "It&#39;d cook on Tuesday. Which meals should be included?" in out["card"]
    assert "One cook on Tuesday for 3 meals · 3 cups rice" in out["card"]


@_needs_node
def test_unticking_a_meal_moves_the_component_cook_day_and_tally():
    comp = _component()
    out = _node(
        "cookAheadAskState.items = []; cookAheadComponentReset([%s]);"
        "cookAheadComponentPicks(cookAheadComponents()[0]);"  # all ticked to start
        "delete cookAheadComponentState.picks['boiled:egg'][200];"  # leave Tuesday out
        "var two = cookAheadComponentBlockHtml(cookAheadComponents()[0], false);"
        "delete cookAheadComponentState.picks['boiled:egg'][201];"  # and Thursday
        "var one = cookAheadComponentBlockHtml(cookAheadComponents()[0], false);"
        "console.log(JSON.stringify({two: two, one: one}));" % json.dumps(comp)
    )
    assert "They&#39;d cook on Thursday. Which meals should be included?" in out["two"]
    assert "One cook on Thursday for 2 meals · 6 eggs" in out["two"]
    assert out["two"].count("is-on") == 2
    # One meal left is no batch, and the tally says so plainly.
    assert "Each recipe makes its own." in out["one"]


# ---------- the tally is a sentence ----------

@_needs_node
def test_the_tally_reads_as_a_sentence_not_arithmetic():
    out = _node(
        "console.log(JSON.stringify(["
        "  cookAheadTallyLine('%s', [], 2),"
        "  cookAheadTallyLine('%s', ['%s'], 4),"
        "  cookAheadTallyLine('%s', ['%s','%s','%s'], 8),"
        "  cookAheadTallyLine('%s', ['%s'], 0),"
        "  cookAheadTallyLine('%s', [], 1),"
        "  cookAheadTallyLine(null, ['%s'], 2)"
        "]));" % (MON, MON, TUE, MON, TUE, THU, SAT, MON, TUE, MON, TUE)
    )
    assert out == [
        "One cook on Monday, for Monday only · 2 plates",
        "One cook on Monday feeds Mon and Tue · 4 plates",
        "One cook on Monday feeds Mon, Tue, Thu and Sat · 8 plates",
        "One cook on Monday feeds Mon and Tue",   # nobody on record: the days are still true
        "One cook on Monday, for Monday only · 1 plate",
        "One cook today feeds Tue · 2 plates",    # a dateless card can never say "on undefined"
    ]
    assert not any(line.startswith("Makes ") for line in out)


@_needs_node
def test_ticking_a_chip_moves_the_tally_in_the_block():
    out = _node(
        "var item = %s; cookAheadAskState.items = [item];"
        "cookAheadAskState.picks[1] = { 11: true, 13: true };"
        "console.log(JSON.stringify(cookAheadAskBlockHtml(item, false)));" % json.dumps(_item())
    )
    assert "One cook on Monday feeds Mon, Thu and Sun · 6 plates" in out
    assert out.count("is-on") == 2


# ---------- the Cook card's own picker says the same thing ----------

@_needs_node
def test_the_cook_card_picker_asks_the_same_plain_question():
    meal = {
        "entry_id": 1, "date": MON, "slot": "dinner",
        "attendance": {"headcount": 2},
        "cook_ahead": {"days": [
            {"entry_id": 11, "date": TUE, "eaters": 2, "selected": False},
            {"entry_id": 12, "date": THU, "eaters": 2, "selected": True},
        ]},
    }
    out = _node("console.log(JSON.stringify(cookAheadHtml(%s)));" % json.dumps(meal))
    assert "Do you want to batch cook this? Which other nights should it cover?" in out
    assert "One cook on Monday feeds Mon and Thu · 4 plates" in out
    assert "Makes " not in out and "Cooking ahead?" not in out


# ---------- two states on touch ----------

def _hover_is_pointer_only(selector: str) -> None:
    """Every :hover rule for this chip sits inside @media (hover: hover)."""
    for m in re.finditer(re.escape(selector) + r":hover", SHELL_CSS):
        before = SHELL_CSS[: m.start()]
        opened = before.rfind("@media (hover: hover)")
        assert opened != -1, f"{selector}:hover is not wrapped in @media (hover: hover)"
        # The media block opened last must still be open: its closing brace
        # would be a "}" on its own line after the rule inside it.
        closed = before.find("\n}", opened)
        assert closed == -1, f"{selector}:hover sits outside the last @media (hover: hover) block"


def test_chip_hover_is_only_for_pointers_so_a_tap_leaves_no_ghost():
    _hover_is_pointer_only(".ca-ask-day")
    _hover_is_pointer_only(".defrost-chip")
    _hover_is_pointer_only(".cook-ahead-day")
    _hover_is_pointer_only(".wk-card .cook-ahead-day")


def test_the_question_line_is_inked_for_both_cards():
    assert ".ca-ask-q {" in SHELL_CSS
    on = ".wk-allset .wk-quick-card.on-spruce"
    start = SHELL_CSS.index(f"{on} .ca-ask-q")
    assert "color: var(--ivory-ink)" in SHELL_CSS[start : SHELL_CSS.index("}", start)]


# ---------- the words themselves (DESIGN_SYSTEM §8) ----------

def test_no_old_arithmetic_or_dashboard_phrasing_survives():
    for gone in ("'Makes '", "'. Cook ahead?'", "Cook anything ahead?", "Cook ahead for these",
                 "Cooking ahead? Tick", "Tick the days a batch should cover", "Make them all at once",
                 "' all at once?'"):
        assert gone not in SHELL_JS, f"{gone!r} is back"
