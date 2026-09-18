"""
A week-tagged chat change refreshes the Shop tab — once.

Loop Board bug, Phase 1: "A chat change that alters the shopping list leaves
the Shop tab stale until you reload." `refreshStaleTabsFromActions`
(static/shell.js) is the one thing standing between CLAUDE.md's "tab panels
build once per page load" gotcha and a household shopping from a list the app
already knows is out of date, and its `week` branch called `loadWeekMenu` and
`refreshTonightFromPlan` and nothing else.

WHAT THE GAP ACTUALLY IS, because the first version of this file got it wrong
and said so confidently. An ordinary `approve_weekly_plan` was ALREADY
covered: `summarize_chat_actions` gives it TWO cards, `week` AND `grocery`,
so the branch below already re-read Shop for it. Driven for real and
measured on origin/main:

    approve, groceries_added_count > 0  -> ['week', 'grocery']   1 refresh
    approve, 0 added but carried > 0    -> ['week']              0 refreshes
    swap_meal_in_plan / take_the_night_off / discard / ...  -> ['week']

So the honest gap is the week tools that move the list and emit a `week` card
alone — including approval in ONE shape, the one that adds nothing and
carries last week's unbought lines over, where the rows really do move
`needed` -> `carried` and Shop goes on showing them as needed.

And because a turn CAN carry both cards, the refresh de-duplicates per turn:
two cards pointing at one panel are one re-read, not two.

HOW THIS FILE IS BUILT, and why it is not a source-marker file. The defect is
a MISSING CALL, which is exactly what reading the source for a name cannot
see (CLAUDE.md, 2026-09-13). So every behavioural test here RUNS shell.js's
own functions under node (tests/nodeharness.py): section 3 drives
`refreshStaleTabsFromActions` against stubs, over action lists taken from the
REAL `summarize_chat_actions` rather than hand-built, and section 4 runs the
real `refreshGroceryPanel` -> `loadGrocery` -> `renderGrocery` against a
stubbed fetch and a small fake DOM.

LABELS. There are THREE baselines, and a single "red on main" number would
hide most of what this file covers, so every docstring names the ones it is
red against:

  * MAIN      — origin/main at 5702234, the shipped app.
  * FIRST CUT — this branch's first commit, which fixed the bug and
                introduced four problems of its own (a double refresh, a
                falsified comment, a redundant call, and a background
                refresh that navigated).
  * ROUND 2   — which fixed those four and left two: three of
                `refreshGrocerySurfaces`' four callers still yanking the
                Shop panel, and the `opts.background` mechanism pinned by
                nothing at all.

Measured, as pytest failure counts against this file:
MAIN 14, FIRST CUT 10, ROUND 2 2.

Of the 14 red against main, only THREE are independent behavioural catches
— `swap_meal_in_plan`, `take_the_night_off`, and the approval that adds
nothing while carrying last week's lines over. The rest are the same one
missing line from another angle, or (in the case of `discard_draft_plan`)
red for a refresh this file itself proves is waste. Each says which it is.

AND REDNESS IS NOT COVERAGE, which is the lesson round 3 paid for. The
docstring audit this file runs compares each CLAIM against the measured red
sets; a mechanism no test exercises has no claim to be wrong about, so the
audit cannot see it. `opts.background` was in exactly that state after
round 2 — deleting it left all 36 tests green while the bug came back in
the chat-approval case the ticket is about. Only mutation against a green
suite finds that.
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import nodeharness
import pytest

from app import tools
from app.main import _WEEK_TOOLS, _categorize_tool, summarize_chat_actions
from conftest import household_date, household_today

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to execute the shell's own functions",
)

# The week tools this file drives end to end. Each one is asserted to still
# be IN _WEEK_TOOLS below rather than trusted from here — the tab is the
# contract between the two halves, and a tool quietly re-tagged `grocery`
# would make these tests pass while covering nothing.
WEEK_TOOLS_UNDER_TEST = [
    "approve_weekly_plan",
    "swap_meal_in_plan",
    "discard_draft_plan",
    "take_the_night_off",
]


# ==========================================================================
# Seeding — a real week, on the household's own clock
# ==========================================================================

def _week_start() -> str:
    """The Monday of the household's week. `household_today`, never
    date.today(): the app reads households.timezone and the test process
    reads TZ, and those are different days for four hours of every UTC day
    (CLAUDE.md's dated-test rule)."""
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


WEEK = _week_start()
DAYS = tools._week_dates(WEEK)


def _recipe(name: str, own: str) -> None:
    """One shared ingredient and one of its own, so a change to a single
    night both trims a merged line and removes a line outright."""
    tools.add_recipe(
        name,
        ingredients=[
            {"item": "Rice", "qty": "1 cup", "category": "pantry"},
            {"item": own, "qty": "1 bag", "category": "produce"},
        ],
        prep_time_minutes=5,
        cook_time_minutes=20,
        default_servings=4,
    )


def _lines() -> list[tuple[str, str]]:
    return sorted((r["item"], r["quantity"]) for r in tools.list_grocery_list())


def _seed_week(approve: bool = True) -> int:
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    for i, day in enumerate(DAYS):
        name = "Dish %d" % i
        _recipe(name, "Greens %d" % i)
        tools.plan_meal(day, name, slot="dinner", weekly_plan_id=plan)
    if approve:
        tools.approve_weekly_plan(plan)
    return plan


# ==========================================================================
# 1. The premise: these tools really do change the shopping list
# ==========================================================================
# GUARDS, all of them — green with or without the shell change. They are
# here because the bug's whole claim is "a week-tagged change alters the
# list", and a fix for a claim nobody measured is a fix for a story. Run
# against the real tools on a throwaway database, never a stub.

def test_approve_weekly_plan_writes_the_whole_list():
    """GUARD (green either way). Approval is the one thing that puts a
    week's ingredients on the list at all — nothing before it does."""
    plan = _seed_week(approve=False)
    before = _lines()
    tools.approve_weekly_plan(plan)
    after = _lines()
    assert before == [], "a draft must not have reached the list"
    assert len(after) == 8, after
    assert ("Rice", "7 cups") in after


def test_swap_meal_in_plan_moves_lines_between_dishes():
    """GUARD (green either way). The outgoing dish's own line goes and the
    new dish's arrives — the list is a different list afterwards."""
    plan = _seed_week()
    before = _lines()
    _recipe("Something Else", "Kale")
    tools.swap_meal_in_plan(plan, DAYS[2], "Something Else", slot="dinner", old_meal="Dish 2")
    after = _lines()
    assert ("Greens 2", "1 bag") in before and ("Greens 2", "1 bag") not in after
    assert ("Kale", "1 bag") not in before and ("Kale", "1 bag") in after


def test_take_the_night_off_puts_back_what_the_dropped_dinner_had_bought():
    """GUARD (green either way). A called-off night reverses whatever is
    still `needed` — its own line goes and the merged one is recomputed
    down, which is the case the tonight sheet's own comment describes."""
    _seed_week()
    today = household_date(0)
    index = DAYS.index(today) if today in DAYS else None
    if index is None:
        pytest.skip("today is outside the seeded week — nothing to call off")
    before = _lines()
    out = tools.tonight_night_off(today)
    assert out.get("status") == "night_off", out
    after = _lines()
    assert ("Greens %d" % index, "1 bag") in before
    assert ("Greens %d" % index, "1 bag") not in after
    assert ("Rice", "7 cups") in before and ("Rice", "6 cups") in after


def test_discarding_a_draft_does_NOT_change_the_list_and_the_card_was_wrong():
    """
    GUARD, and a correction to the ticket rather than support for it.

    The card lists `discard_draft_plan` among the week tools that
    "demonstrably change the list". Measured here, twice — a lone draft and
    a draft sitting over an approved week — it changes nothing, and it
    cannot: a draft never reaches the shopping list (that is the whole point
    of the 2026-09-13 draft-waits-for-approval work, and it is what the drop
    dialog's own second line says out loud, "Nothing from it is on your
    list").

    So the refresh it now triggers is one wasted request on this tool, which
    is the stated cost of refreshing unconditionally — see the branch's own
    comment. Written down rather than quietly dropped from the list: the
    next reader of that comment should not have to re-measure it.
    """
    plan = _seed_week()
    approved = _lines()
    draft = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    _recipe("Draft Dish", "Draft Greens")
    tools.plan_meal(DAYS[0], "Draft Dish", slot="dinner", weekly_plan_id=draft)
    assert _lines() == approved, "a draft's meals must not reach the list"
    tools.discard_draft_plan(draft)
    assert _lines() == approved
    assert plan  # the approved week is untouched underneath


# ==========================================================================
# 2. The tab is the contract
# ==========================================================================

@pytest.mark.parametrize("tool", WEEK_TOOLS_UNDER_TEST)
def test_each_tool_is_still_tagged_week(tool):
    """GUARD. _categorize_tool gives each action exactly ONE tab, so
    re-tagging any of these `grocery` would swap this bug for a stale Plan
    tab. Asked of the app rather than hard-coded, so a re-tag fails here."""
    assert tool in _WEEK_TOOLS
    assert _categorize_tool(tool)[1] == "week"


# ==========================================================================
# 3. ...so the Shop tab refreshes. The catches.
# ==========================================================================

_REFRESH_STUB = """
var CALLS = [];
var panels = { today: { dataset: { built: '1' } }, week: { dataset: { built: '1' } } };
function refreshHolding() { CALLS.push('holding'); }
function loadWeekMenu() { CALLS.push('weekmenu'); }
// The real one calls refreshTodayMoves itself (both are no-ops on an
// unbuilt Today), so the stub records both — otherwise a test asking
// "does the week branch need its own refreshTodayMoves?" would be asking
// about a call this stub had swallowed.
function refreshTonightFromPlan() { CALLS.push('tonight'); refreshTodayMoves(); }
function refreshDishIndex() { CALLS.push('dishindex'); }
function refreshKitchenPanel() { CALLS.push('kitchen'); }
function refreshTodayMoves() { CALLS.push('todaymoves'); }
function hrefSheetKey(h) { return h === '/memory' ? 'memory' : null; }
function prefsInvalidate() { CALLS.push('prefs'); }
function refreshGroceryPanel() { CALLS.push('grocery'); }
function loadNeedsYou() { CALLS.push('needsyou'); }
function loadTodayMoves() { CALLS.push('todaypanel'); }
function loadChores() { CALLS.push('chores-now'); }
function loadPlanChores() { CALLS.push('chores-plan'); }
"""


def _function(name: str) -> str:
    """The body of one top-level function in shell.js, to its closing brace."""
    marker = "  async function %s(" % name
    if marker not in SHELL_JS:
        marker = "  function %s(" % name
    start = SHELL_JS.index(marker)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end] + "\n  }\n"


def _strip_comments(js: str) -> str:
    """Whole-line `//` comments out. Never a trailing `//` on a code line —
    shell.js is full of `https://` inside strings (test_refresh_policy's own
    reasoning, borrowed whole)."""
    return "\n".join(l for l in js.splitlines() if not l.lstrip().startswith("//"))


def _refresh(actions: list[dict], week_built: bool = True, opts=None) -> list[str]:
    """Run the real refreshStaleTabsFromActions and report what it called."""
    call = "refreshStaleTabsFromActions(%s%s);\n" % (
        json.dumps(actions),
        "" if opts is None else ", " + json.dumps(opts),
    )
    script = (
        _REFRESH_STUB
        + _function("refreshStaleTabsFromActions")
        + ("\n" if week_built else "\ndelete panels.week.dataset.built;\n")
        + call
        + "console.log(JSON.stringify(CALLS));\n"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, "node failed: %s" % res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def _after_tonight_swap(opts=None) -> list[str]:
    """Run the real afterTonightSwap — the NON-chat caller of the same
    function, a tap on Now rather than a chat turn."""
    script = (
        _REFRESH_STUB
        + "function loadTonightAsk() { CALLS.push('tonightask'); }\n"
        + _function("refreshStaleTabsFromActions")
        + _function("afterTonightSwap")
        + "afterTonightSwap(panels.today%s);\n" % ("" if opts is None else ", " + json.dumps(opts))
        + "console.log(JSON.stringify(CALLS));\n"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, "node failed: %s" % res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


class _ToolUse:
    """One assistant tool_use block, in the shape summarize_chat_actions
    reads (it uses getattr first, then dict access)."""

    def __init__(self, name: str, block_id: str):
        self.type = "tool_use"
        self.name = name
        self.id = block_id
        self.input = {}


def real_cards(tool: str, result: dict) -> list[dict]:
    """The action list the SERVER really produces for one tool call.

    This is the chain, and `_categorize_tool` is not it: app/main.py
    special-cases `approve_weekly_plan` and gives it a second, `grocery`
    card whenever it added anything. Building `[{'tab': 'week'}]` by hand
    for that tool tests a payload the app never sends — which is exactly
    what the first version of this file did, and why it reported a gap that
    was already closed.
    """
    actions = summarize_chat_actions(
        [],
        [
            {"role": "assistant", "content": [_ToolUse(tool, "t1")]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": json.dumps(result)}
            ]},
        ],
    )
    return [{"tab": a.tab, "href": a.href} for a in actions]


# The action list the SERVER really sends for each tool, measured. The
# `result` dicts are the shapes summarize_chat_actions branches on.
REAL_TURNS = {
    # The commonest approval. TWO cards — and so it was already covered.
    "approve_weekly_plan (ordinary)": (
        "approve_weekly_plan",
        {"status": "approved", "groceries_added_count": 8, "carried_over_count": 0},
    ),
    # THE REAL APPROVE GAP: nothing added, last week's lines set aside.
    "approve_weekly_plan (0 added, carried)": (
        "approve_weekly_plan",
        {"status": "approved", "groceries_added_count": 0, "carried_over_count": 3},
    ),
    "swap_meal_in_plan": ("swap_meal_in_plan", {"meal_date": "2026-09-18", "slot": "dinner"}),
    "take_the_night_off": ("take_the_night_off", {"status": "night_off"}),
    "discard_draft_plan": ("discard_draft_plan", {"ok": True}),
}


def test_the_server_gives_an_ordinary_approval_two_cards():
    """
    GUARD, and the correction that this whole file's framing rests on.
    app/main.py hangs a second `grocery` card on `groceries_added_count`,
    so an ordinary approval was never the gap — and an approval that adds
    nothing while carrying last week's lines over emits `week` alone, which
    is.
    """
    assert real_cards(*REAL_TURNS["approve_weekly_plan (ordinary)"]) == [
        {"tab": "week", "href": None}, {"tab": "grocery", "href": None},
    ]
    assert real_cards(*REAL_TURNS["approve_weekly_plan (0 added, carried)"]) == [
        {"tab": "week", "href": None},
    ]


def test_an_approval_that_adds_nothing_still_moves_rows_off_the_needed_list():
    """
    GUARD (green either way) — the premise behind the approve case, over
    the real tools on a throwaway DB rather than reasoned about.

    A week with nothing to buy (every night takeout) approved over a
    previous week with an unbought line: `groceries_added_count` 0,
    `carried_over_count` 1, and the row really moves `needed` -> `carried`.
    Shop was showing it as a needed row; it is now a keep-or-drop question.
    """
    prev_monday = household_today() - datetime.timedelta(days=household_today().weekday() + 7)
    prev = tools.create_weekly_plan(prev_monday.isoformat())["weekly_plan_id"]
    _recipe("Old Dish", "Spinach")
    tools.plan_meal(tools._week_dates(prev_monday.isoformat())[0], "Old Dish",
                    slot="dinner", weekly_plan_id=prev)
    tools.approve_weekly_plan(prev)
    assert [r["status"] for r in tools.list_grocery_list("all")].count("needed") >= 1

    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    for day in DAYS:
        tools.plan_meal(day, "Takeout", slot="dinner", weekly_plan_id=plan)
    out = tools.approve_weekly_plan(plan)

    assert out["groceries_added_count"] == 0
    assert out["carried_over_count"] >= 1
    statuses = [r["status"] for r in tools.list_grocery_list("all")]
    assert statuses and set(statuses) == {"carried"}, statuses
    # ...and the server tells the shell about it with a `week` card only.
    assert real_cards("approve_weekly_plan", out) == [{"tab": "week", "href": None}]


@_needs_node
@pytest.mark.parametrize("label", sorted(REAL_TURNS))
def test_the_shop_tab_refreshes_for_each_week_tool(label):
    """
    Driven over the REAL `summarize_chat_actions` output for each tool, not
    a hand-built `[{'tab': 'week'}]` — see real_cards for why that
    distinction is the difference between testing the app and testing a
    payload it never sends.

    WHICH OF THESE ARE REAL CATCHES, stated rather than implied.

      * swap_meal_in_plan          RED on main   — a real behavioural catch
      * take_the_night_off         RED on main   — a real behavioural catch
      * approve (0 added, carried) RED on main   — a real behavioural catch
      * approve (ordinary)         RED on FIRST CUT, green on main — it was
                                   ALREADY covered by its own grocery card,
                                   and the job here is that it stays at
                                   exactly one re-read rather than two
      * discard_draft_plan         RED on main, and proves nothing about the
                                   bug: that tool changes no line (the test
                                   above measures it), so this is red for a
                                   refresh that is pure waste. Kept because
                                   the waste is a deliberate, stated cost of
                                   the unconditional rule, and a test is
                                   where a cost should be visible.
    """
    tool, result = REAL_TURNS[label]
    cards = real_cards(tool, result)
    assert cards, "the server sends no card at all for %s" % label
    calls = _refresh(cards)
    assert calls.count("grocery") == 1, (
        "%s should leave Shop re-read exactly once: %r" % (label, calls)
    )


@_needs_node
def test_it_refreshes_shop_even_when_meals_has_never_been_opened():
    """
    RED on main, for the same one missing call as the per-tool tests.

    The two arms of the `week` branch split on whether MEALS was built, and
    Shop is built independently of it — somebody can have opened Shop and
    never Plan. This is the same lesson the `today` branch was taught on
    2026-09-12, when loadPlanChores was put outside its panels.today guard.
    """
    calls = _refresh([{"tab": "week"}], week_built=False)
    assert "grocery" in calls, calls
    # and the arm still does its own job
    assert "dishindex" in calls, calls


@_needs_node
def test_the_week_branch_does_not_ask_today_twice():
    """
    GUARD — green against the unmodified shell.js, because the duplicate it
    forbids is one the fix could have introduced and did not. (Said that way
    round deliberately: a test that reads as a catch and is not is the one
    kind of error this project keeps having to unpick.)

    The `grocery` branch calls refreshTodayMoves() beside its panel refresh,
    and the obvious move was to copy that pair wholesale. It would have been
    wrong: refreshTonightFromPlan already calls refreshTodayMoves, so the
    week branch reaches Today either way and a second call is two fetches
    for one answer (the same waste tests/test_kitchen_refresh_once.py exists
    for).
    """
    assert _refresh([{"tab": "week"}]).count("todaymoves") == 1
    assert _refresh([{"tab": "week"}], week_built=False).count("todaymoves") == 1
    assert "refreshTodayMoves();" in _function("refreshTonightFromPlan")


@_needs_node
def test_shop_is_refreshed_once_for_one_action():
    """
    RED on main, and weak — it is red for the same one missing call as the
    per-tool tests, not for a reason of its own. What it adds is the other
    direction: both arms carry the call, and one action must take exactly
    one of them, so a restructure letting both run would double every
    refresh.
    """
    assert _refresh([{"tab": "week"}]).count("grocery") == 1
    assert _refresh([{"tab": "week"}], week_built=False).count("grocery") == 1


@_needs_node
def test_a_turn_that_changed_nothing_about_the_week_leaves_shop_alone():
    """
    GUARD (green either way). The refresh is unconditional WITHIN the week
    branch, not unconditional full stop: a kitchen or memory action still
    must not reach Shop, or every "we finished the chicken" would refetch
    the list.
    """
    assert "grocery" not in _refresh([{"tab": "kitchen"}])
    assert "grocery" not in _refresh([{"href": "/memory"}])
    assert "grocery" not in _refresh([{"tab": "today"}])
    assert _refresh([]) == []


@_needs_node
def test_kitchen_goes_stale_in_the_same_arm_and_is_NOT_fixed_here():
    """
    CHARACTERISATION of a SEPARATE bug, found while measuring this one and
    deliberately left. Invert this test when it is fixed.

    `refreshKitchenPanel()` is called from inside `loadWeekMenu` — on
    purpose, so that the six things which reload the plan get the cook's tab
    for free (that function's own comment says so). But `loadWeekMenu` only
    runs in the arm where MEALS was built. So on a page view where somebody
    has opened Shop and Kitchen and never Plan, a week-tagged chat change
    refreshes Now, Shop and the dish index, and leaves KITCHEN — today's
    cooks, the rest of the week, cook mode — reading whatever it loaded at
    build time.

    Exactly the shape this ticket fixed for Shop, one branch over, and not
    this ticket's to widen: the fix is to hoist `refreshKitchenPanel()` out
    of `loadWeekMenu` the way the grocery refresh is hoisted here, which
    changes behaviour for every one of that function's callers.
    """
    assert "kitchen" not in _refresh([{"tab": "week"}], week_built=False)
    # And it is reached in the other arm only through loadWeekMenu, which is
    # what makes the two arms disagree.
    assert "refreshKitchenPanel();" in _function("loadWeekMenu")
    body = _function("refreshStaleTabsFromActions")
    week_arms = body[body.index("action.tab === 'week'"):body.index("action.tab === 'kitchen'")]
    assert "refreshKitchenPanel" not in week_arms


@_needs_node
def test_the_grocery_branch_still_refreshes_both_surfaces():
    """GUARD (green either way). The branch below this one is the model the
    fix was read off; it must not be disturbed by it."""
    calls = _refresh([{"tab": "grocery"}])
    assert calls == ["grocery", "todaymoves"], calls


@_needs_node
def test_one_turn_re_reads_the_shop_panel_once_however_many_cards_say_so():
    """
    RED on FIRST CUT, green on main — worth stating that way round, because
    an earlier version of this test asserted the OPPOSITE (that two refreshes
    were correct) and framed it as a feature.

    An ordinary approval sends TWO cards, `week` and `grocery`, and both
    branches want the SAME single Shop panel. One re-read is the right
    answer; two is 16 requests and two concurrent loadGrocery() runs over
    the same eight endpoints, on exactly the turn this ticket is about.
    Main did one (via the grocery card alone); the first commit here did
    two; this does one.
    """
    cards = real_cards(*REAL_TURNS["approve_weekly_plan (ordinary)"])
    assert len(cards) == 2, cards
    calls = _refresh(cards)
    assert calls.count("grocery") == 1, calls
    # Each card still does its own OTHER work.
    assert "weekmenu" in calls


@_needs_node
def test_a_hand_built_pair_of_week_and_grocery_cards_is_also_one_re_read():
    """
    RED on main AND on FIRST CUT, for opposite reasons — which is the point
    of having it beside the real-cards test above. Main does nothing for a
    `week` card, so `[week, week]` gives 0; the first cut did one per card,
    so `[week, grocery]` gave 2. The rule this pins is independent of what
    the server happens to send today: any cards in one turn that mean "the
    list moved" are ONE re-read of the one panel.
    """
    assert _refresh([{"tab": "week"}, {"tab": "grocery"}]).count("grocery") == 1
    assert _refresh([{"tab": "grocery"}, {"tab": "week"}]).count("grocery") == 1
    assert _refresh([{"tab": "week"}, {"tab": "week"}]).count("grocery") == 1


# --------------------------------------------------------------------------
# The OTHER caller: a tap on Now, not a chat turn
# --------------------------------------------------------------------------
# refreshStaleTabsFromActions has two call sites. afterTonightSwap is the
# second, and it knows something a chat action never can — exactly which
# write it just made.

@_needs_node
def test_a_nights_swap_tap_still_leaves_shop_alone():
    """
    RED on FIRST CUT, green on main — a pure regression guard.
    `afterTonightSwap`'s own comment says "the grocery list is untouched by
    a nights swap, so Shop is left alone" — swap_dinner_nights
    re-dates the rows in place and the grocery links ride along. The first
    commit here made that sentence false by routing the tap through a week
    branch that had begun re-reading Shop unconditionally: main 0
    refreshes, that commit 1, under a comment saying the opposite.
    """
    calls = _after_tonight_swap()
    assert "grocery" not in calls, calls
    # ...and it still does everything it always did.
    assert "weekmenu" in calls and "kitchen" in calls and "tonightask" in calls


@_needs_node
def test_a_night_off_tap_re_reads_shop_exactly_once():
    """
    RED on main (where afterTonightSwap takes no second argument, so the
    flag is ignored and nothing re-reads Shop). GREEN on FIRST CUT — and
    that is worth saying, because it is NOT the test that catches the
    double this fix removed.

    The double lived one level up, in runTonightNightOff, which called
    afterTonightSwap and then refreshGroceryPanel() again; this test calls
    afterTonightSwap directly and so cannot see it. The test that does is
    test_the_night_off_tap_no_longer_carries_its_own_grocery_call, and it
    is a source pin because there is no harness for a route handler here.
    Said plainly rather than letting the pair read as one catch.

    What this one does claim: a night off DOES move the list — a dropped
    dish puts back whatever it had put on it that nobody has bought — so
    the flag has to reach Shop, exactly once.
    """
    calls = _after_tonight_swap({"listMoved": True})
    assert calls.count("grocery") == 1, calls


def test_the_night_off_tap_no_longer_carries_its_own_grocery_call():
    """
    RED on main and on FIRST CUT (both carry the explicit call). A shape
    pin rather than a behaviour one: the duplicate really is gone from the
    CODE, not merely coalesced at runtime by refreshGroceryOnce — which is
    scoped to
    one refreshStaleTabsFromActions call and could not have caught it,
    because the old explicit call sat outside.

    Comments are stripped first, the way tests/test_refresh_policy.py does
    it and for its reason: this function is more prose than code now, and
    the prose names the call it is explaining. An assertion a comment can
    satisfy is not an assertion."""
    body = _strip_comments(_function("runTonightNightOff"))
    assert "refreshGroceryPanel(" not in body
    assert "afterTonightSwap(panel, { listMoved: true });" in body


@_needs_node
def test_a_chat_action_can_never_say_the_list_stayed_put():
    """
    RED on main for the same one missing call as everything else in this
    section, and RED on FIRST CUT, where the parameter does not exist at
    all. Its own claim is the narrow one: `listMoved` is for the LOCAL
    caller only — the chat door omits the second argument and gets the keen
    behaviour, which is the whole point of the unconditional rule, and an
    absent or empty opts must never be read as "the list stayed put".
    """
    assert "grocery" in _refresh([{"tab": "week"}])
    assert "grocery" in _refresh([{"tab": "week"}], opts={})
    assert "grocery" not in _refresh([{"tab": "week"}], opts={"listMoved": False})


# ==========================================================================
# 4. What the refresh does to somebody already using the Shop tab
# ==========================================================================
# The acceptance criterion says the scroll position and any in-flight
# sorting state survive, and that VERIFYING it is the job rather than
# assuming it. So these run the real refreshGroceryPanel -> loadGrocery ->
# renderGrocery against a stubbed fetch and a fake DOM, and read the state
# back afterwards.
#
# All GUARDS — refreshGroceryPanel behaved this way before the fix. What
# changes is that this branch now reaches it, so "does reaching it hurt?" is
# a question the fix has to answer.


def _grocery_block() -> str:
    """The whole Grocery region, up to the hands-free voice code (which
    wants a SpeechRecognition engine). The same slice
    tests/test_grocery_fast_sort.py takes."""
    start = SHELL_JS.index("  var GRO_CATEGORY_LABELS = {")
    end = SHELL_JS.index("  // ---------- Hands-free voice ----------", start)
    return SHELL_JS[start:end]


def _band_identity() -> str:
    """shell.js's own BAND_IDENTITY line. groBandEyebrow reads it, and a
    stubbed copy would go on saying 'wordmark' after the real one moved."""
    line = "  var BAND_IDENTITY = "
    start = SHELL_JS.index(line)
    return SHELL_JS[start:SHELL_JS.index("\n", start) + 1]


_SHOP_STUB = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function setRootBand() {}
function bandDateLabel() { return 'Wednesday, Sep 17'; }
function emptyMomentHtml() { return '<div class="empty-moment"></div>'; }
function snwLink() { return ''; }
function showToast() {}
function activateTab() {}
var coachState = { householdId: 1 };
var scrollEl = { scrollTop: 0 };
const STORE = new Map();
const window = {
  localStorage: {
    getItem: function (k) { return STORE.has(k) ? STORE.get(k) : null; },
    setItem: function (k, v) { STORE.set(k, String(v)); },
    removeItem: function (k) { STORE.delete(k); }
  },
  history: { pushState: function () {} },
  confirm: function () { return true; }
};
// Enough of an element for renderGrocery to write into. It reads nothing
// back that these tests assert on — the claims here are about groceryState
// and the scroll, not about markup.
//
// WRITING TO innerHTML DROPS THE SCROLL TO 0, and that is the whole reason
// the scroll test has any teeth. A browser does this for real: replacing
// the content under a scroller with something shorter puts the scroll
// wherever it can, which for a list rebuilt from nothing is the top. A
// scrollTop that simply sat where it was put would make
// "the scroll survives" pass whether or not renderGrocery restores it —
// checked by mutation, and the first version of this harness did exactly
// that.
function el() {
  var node = { hidden: false, textContent: '', value: '',
    classList: { toggle: function () {}, add: function () {}, remove: function () {},
                 contains: function () { return false; } },
    querySelectorAll: function () { return []; },
    addEventListener: function () {}, setAttribute: function () {},
    getAttribute: function () { return null; },
    focus: function () {}, setSelectionRange: function () {} };
  var html = '';
  // AN ID-ADDRESSABLE CHILD REGISTRY, not a stub that answers null.
  //
  // A fake node that returns null from every querySelector is why the first
  // round of this work could not see the add-row loss at all: the capture/
  // restore pair that carries a half-typed "oat milk" across a re-render
  // (groCaptureAddRow / groRestoreAddRow, and the two input helpers beside
  // them) look their field up BY ID, so against a null-returning node they
  // are no-ops and the loss is invisible. Round 2 special-cased
  // #gro-add-item; round 3 generalised it, because a test now depends on
  // this and the next one should not have to add its own case.
  //
  // Any id this render actually wrote is findable, and the same object
  // comes back every time so a value set on it survives until the id stops
  // being written — which is exactly the real behaviour under test: the
  // field goes when the foot stops rendering it.
  node._byId = {};
  node.querySelector = function (sel) {
    var m = /^#([A-Za-z0-9_-]+)$/.exec(sel || '');
    if (!m) return null;
    var id = m[1];
    if (html.indexOf('id="' + id + '"') === -1) return null;
    if (!node._byId[id]) node._byId[id] = el();
    return node._byId[id];
  };
  Object.defineProperty(node, 'innerHTML', {
    get: function () { return html; },
    set: function (v) { html = v; if (scrollEl) scrollEl.scrollTop = 0; }
  });
  return node;
}
var GRO_NODES = {};
['#gro-back','#gro-head','#gro-band','#gro-title','#gro-sub','#gro-body','#gro-foot','#gro-dock']
  .forEach(function (s) { GRO_NODES[s] = el(); });
var document = { activeElement: null, createElement: function () { return el(); } };
var panels = { grocery: { dataset: { built: '1' },
  querySelector: function (s) { return GRO_NODES[s] || null; } } };
var FETCHED = [];
// The list the server hands back AFTER the chat change — two shops, one
// row each, plus one row nobody has sorted yet.
function fetch(url) {
  FETCHED.push(url);
  var body = {};
  if (url.indexOf('by-store') !== -1) {
    body = { stores: [
      { store: 'Loblaws', sections: [{ section: 'other', items: [
        { id: 1, item: 'Rice', quantity: '1 cup', store: 'Loblaws', store_decided: 1 }] }] },
      { store: 'Costco', sections: [{ section: 'other', items: [
        { id: 3, item: 'Oats', quantity: '1 bag', store: 'Costco', store_decided: 1 }] }] }
    ] };
    if (UNSORTED) {
      // The bucket is named 'Unassigned' and its ROWS carry store: '' —
      // what /api/grocery-list/by-store really sends, and what groUnsorted
      // looks for by name.
      body.stores.push({ store: 'Unassigned', sections: [{ section: 'other', items: [
        { id: 2, item: 'Kale', quantity: '1 bag', store: '', store_decided: 0 }] }] });
    }
  } else if (url.indexOf('/api/grocery-list?') !== -1) body = { sections: [] };
  else if (url.indexOf('carried') !== -1) body = { items: CARRIED };
  else if (url.indexOf('pre-shop') !== -1) body = { flags: [] };
  else if (url.indexOf('already-have') !== -1) body = { already_have: [], elsewhere: [] };
  else if (url.indexOf('staples') !== -1) body = { staples: [], sections: [] };
  else if (url.indexOf('spices') !== -1) body = { items: [], recently_bought: [] };
  return Promise.resolve({ ok: true, status: 200,
    json: function () { return Promise.resolve(body); } });
}
var UNSORTED = true;
var CARRIED = [];
"""


# refreshGrocerySurfaces sits far outside the Grocery region (it belongs to
# the week's approval code), so the two tests that drive it append it.
_SURFACES = ("""
function refreshTodayMoves() {}
""" + _function("refreshGrocerySurfaces"))


def _shop(body: str) -> dict:
    script = _SHOP_STUB + _band_identity() + _grocery_block() + body
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, "node failed: %s" % res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@_needs_node
def test_a_refresh_mid_trip_does_not_yank_the_shopper_out_of_the_shop():
    """
    GUARD. The worst thing this fix could do is end somebody's trip from a
    phone in another room. It doesn't: loadGrocery never touches
    groceryState.step or the trip snapshot, so the stop stays open, the
    stops already behind stay behind, and what came home this trip is still
    counted. All it changes is the list the stop is drawn from — which is
    the whole point.
    """
    out = _shop("""
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
groceryState.step = 'trip';
groceryState.tripStops = ['Loblaws', 'Costco'];
groceryState.tripIndex = 1;
groceryState.tripDone = { Loblaws: true };
groceryState.tripStartedAt = Date.now();
groceryState.tripTotal = 9;
groceryState.tripBought = 4;
groceryState.tripRestored = true;
scrollEl.scrollTop = 412;
refreshGroceryPanel();
setTimeout(function () {
  console.log(JSON.stringify({
    step: groceryState.step, stops: groceryState.tripStops,
    index: groceryState.tripIndex, done: groceryState.tripDone,
    bought: groceryState.tripBought, scroll: scrollEl.scrollTop,
    fetched: FETCHED.length
  }));
}, 60);
""")
    assert out["step"] == "trip"
    assert out["stops"] == ["Loblaws", "Costco"]
    assert out["index"] == 1
    assert out["done"] == {"Loblaws": True}
    assert out["bought"] == 4
    assert out["scroll"] == 412, "a refresh must not scroll the shopper's stop away"
    assert out["fetched"] > 0, "the harness never reached the network at all"


@_needs_node
def test_the_scroll_position_survives_the_refresh():
    """
    GUARD, and the one the 2026-09-02 entry asks to be matched: the native
    Grocery panel replaced an iframe whose src-reload threw the whole screen
    away, "scroll position and all". renderGrocery reads scrollTop before it
    writes and puts it back after.
    """
    out = _shop("""
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
scrollEl.scrollTop = 733;
refreshGroceryPanel();
setTimeout(function () {
  console.log(JSON.stringify({ scroll: scrollEl.scrollTop, step: groceryState.step }));
}, 60);
""")
    assert out["scroll"] == 733
    assert out["step"] == "list"


@_needs_node
def test_a_refresh_mid_sort_leaves_the_sorting_screen_where_it_was():
    """
    GUARD. SORT and SORT ALL stay up, and so does the row whose "Use
    something else" field is open.

    Note what this does and does not measure: the STEP and the open row
    survive, which is what is asserted. Whether the half-typed text inside
    that field survives is groCaptureSubstInput/groRestoreSubstInput's job
    and is READ from shell.js rather than measured here — the fake DOM
    below answers null to every querySelector, so those two run as no-ops
    in this harness. Said rather than implied, because a claim nobody
    measured is the kind this log keeps having to unpick.

    Worth knowing while reading this: there are no STAGED sort picks to lose
    any more. SORT ALL staged every pick behind a save button when it shipped
    (2026-09-09) and stopped on 2026-09-13 — a tap WRITES now and the row
    leaves the screen, which is why groSortAllRender exists. The ticket's
    "staged sort picks" describes a screen that no longer works that way.
    """
    out = _shop("""
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
groceryState.step = 'sortall';
groceryState.substOpenId = '2';
groceryState.sortTotal = 3;
refreshGroceryPanel();
setTimeout(function () {
  console.log(JSON.stringify({
    step: groceryState.step, subst: groceryState.substOpenId,
    total: groceryState.sortTotal
  }));
}, 60);
""")
    assert out["step"] == "sortall"
    assert out["subst"] == "2"
    assert out["total"] == 3


@_needs_node
def test_sorting_folds_back_to_the_list_when_the_change_left_nothing_to_sort():
    """
    CHARACTERISATION (green either way), so the one screen change a chat
    refresh really can make is written down rather than found later.

    If the week-tagged change removed the very rows somebody was sorting,
    renderGrocery's own fallback puts them on LIST — "a step that stopped
    making sense under its own feet falls back to the root rather than
    rendering a screen about nothing". That rule predates this fix and is
    right; the fix only makes it reachable from chat as well as from the
    shopper's own taps.
    """
    out = _shop("""
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
groceryState.step = 'sort';
UNSORTED = false;   // the chat change sorted, bought or dropped the last one
refreshGroceryPanel();
setTimeout(function () { console.log(JSON.stringify({ step: groceryState.step })); }, 60);
""")
    assert out["step"] == "list"


@_needs_node
def test_a_background_refresh_does_not_take_the_household_to_another_screen():
    """
    RED on main AND on FIRST CUT. The first cut shipped this loss and
    DOCUMENTED it as an acceptable cost; it was worse than documented, and
    the measurement below is why it is fixed rather than written down.
    (It is red on main too, for the same reason — main navigates as well;
    main simply never reached this path from a chat turn.)

    On LIST, with last week's leftovers waiting and "later" already said,
    one background refresh used to go:

        step `list` -> `carry`,  scrollTop 733 -> 0,
        and the half-typed "oat milk" in the add row GONE

    — the add row because groFootHtml renders `#gro-add-item` on LIST only,
    so groRestoreAddRow had nothing to put the text back into. That is the
    exact loss renderGrocery's own capture/restore comment exists to
    prevent, arriving through a refresh nobody asked for, from a chat turn
    or another tab's tap.

    Nothing is lost by not navigating: LIST already carries
    groCarryRowHtml's "N things from last week · Keep or drop?" row, whose
    own comment calls it "the way back into CARRY once Later was said".
    """
    out = _shop("""
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
// An ordinary LIST load first, so the foot writes its real add row.
loadGrocery();
setTimeout(function () {
  var foot = GRO_NODES['#gro-foot'];
  var addRow = foot.querySelector('#gro-add-item');
  if (addRow) addRow.value = 'oat milk';
  scrollEl.scrollTop = 733;
  var before = { step: groceryState.step, scroll: scrollEl.scrollTop,
                 typed: addRow ? addRow.value : null,
                 hasAddRow: /id="gro-add-item"/.test(foot.innerHTML) };
  // Now an approval sets last week's lines aside, and this household has
  // already said "later" to them once this page view.
  CARRIED = [{ id: 9, item: 'Spinach', quantity: '1 bag' }];
  groceryState.carryDeferred = true;
  refreshGroceryPanel();
  setTimeout(function () {
    var f2 = GRO_NODES['#gro-foot'];
    var a2 = f2.querySelector('#gro-add-item');
    console.log(JSON.stringify({ before: before, after: {
      step: groceryState.step, scroll: scrollEl.scrollTop,
      deferred: groceryState.carryDeferred,
      typed: a2 ? a2.value : null,
      hasAddRow: /id="gro-add-item"/.test(f2.innerHTML)
    }}));
  }, 60);
}, 60);
""")
    assert out["before"] == {"step": "list", "scroll": 733, "typed": "oat milk",
                             "hasAddRow": True}, out["before"]
    assert out["after"]["step"] == "list", "a background refresh must not navigate"
    assert out["after"]["scroll"] == 733, "a background refresh must not scroll"
    assert out["after"]["typed"] == "oat milk", "it must not eat a half-typed add"
    assert out["after"]["hasAddRow"] is True
    assert out["after"]["deferred"] is True, (
        "'later' is the household's answer; a background re-read is not a "
        "reason to forget it"
    )


@_needs_node
def test_a_refill_still_opens_the_leftovers_question_the_way_it_always_did():
    """
    GUARD, and the reason the fix above is a parameter rather than a
    deletion. Approve and Start over are FOREGROUND rebuilds the household
    just asked for: a refill really is a new list, so the leftovers become
    an open question again and the tab may land on CARRY — the 2026-09-13
    rule that the amounts are settled before the list is read. Byte for
    byte what refreshGroceryPanel did for every caller before this change;
    now it is what it does for the two that mean it.
    """
    out = _shop("""
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
loadGrocery();
setTimeout(function () {
  CARRIED = [{ id: 9, item: 'Spinach', quantity: '1 bag' }];
  groceryState.carryDeferred = true;
  refreshGroceryPanel({ refill: true });
  setTimeout(function () {
    console.log(JSON.stringify({ step: groceryState.step,
                                 deferred: groceryState.carryDeferred }));
  }, 60);
}, 60);
""")
    assert out["step"] == "carry"
    assert out["deferred"] is False


def test_only_the_approve_button_and_start_over_ask_for_a_refill():
    """
    RED on main and on FIRST CUT, where the parameter does not exist — a
    wiring pin rather than a behavioural catch, and it says so.

    THE COUNT THIS TEST USED TO MAKE WAS WRONG, and the wrong version is
    worth keeping in view because it read as more than it was. It was named
    `..._the_two_foreground_rebuilds_...` and its docstring said "Approve
    (refreshGrocerySurfaces) and Start over (refreshAfterReset) are the only
    two callers that mean 'a new list'; every other path is a background
    re-read." Both halves were false: `refreshGrocerySurfaces` has FOUR
    callers, and three of them are Plan-tab edits to an already-approved
    week. What the assertion actually pinned was two CALL SITES OF
    `refreshGroceryPanel` — true, and a different claim from the one the
    name made.

    So it counts CALL SITES now, of both functions, and names them.
    """
    # The two direct refills: the Approve button's helper, and Start over.
    assert "refreshGroceryPanel(opts);" in _strip_comments(
        _function("refreshGrocerySurfaces"))
    assert "refreshGroceryPanel({ refill: true })" in _strip_comments(
        _function("refreshAfterReset"))
    # refreshGrocerySurfaces forwards rather than deciding, and exactly ONE
    # of its four callers asks for a refill.
    body = _strip_comments(SHELL_JS)
    assert body.count("refreshGrocerySurfaces({ refill: true });") == 1
    # Three until 2026-09-18: the Review stepper's "−" and "+" went with the
    # stepper; the swap sheet's pick took one of their places.
    assert body.count("refreshGrocerySurfaces();") == 2
    # The chat door never refills.
    assert "refill" not in _strip_comments(_function("refreshStaleTabsFromActions"))


@_needs_node
def test_a_plan_tab_edit_to_an_approved_week_does_not_move_the_shop_panel():
    """
    RED on main and on FIRST CUT and on ROUND 2 — the round-2 mechanism did
    not reach this far, which is what round 3 is for.

    `refreshGrocerySurfaces` has four callers and only the Approve button is
    an approval. The other three — the Review stepper's "−" and "+", and
    resolving an open slot — are edits to a week that ALREADY has a list,
    made while the household is looking at the PLAN tab. Until this they
    passed `refill: true` like the approve path, so a stepper tap on Plan
    moved the hidden Shop panel to CARRY, zeroed its scroll, cleared an
    answered "later" and ate a half-typed add row. Measured, and identical
    on main — not a regression, but the harm this branch's own comment
    describes, reachable without chat at all.

    Decided rather than inherited: those three are the SAME logical change
    as a chat `swap_meal_in_plan` or `take_the_night_off`, which have gone
    through the background path since round 2. Two doors onto one change
    must not disagree about whether the shopper gets moved.
    """
    out = _shop(_SURFACES + """
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
loadGrocery();
setTimeout(function () {
  var foot = GRO_NODES['#gro-foot'];
  var addRow = foot.querySelector('#gro-add-item');
  if (addRow) addRow.value = 'oat milk';
  scrollEl.scrollTop = 733;
  CARRIED = [{ id: 9, item: 'Spinach', quantity: '1 bag' }];
  groceryState.carryDeferred = true;
  // What the three Review / open-slot call sites do: no opts at all.
  refreshGrocerySurfaces();
  setTimeout(function () {
    var a2 = GRO_NODES['#gro-foot'].querySelector('#gro-add-item');
    console.log(JSON.stringify({
      step: groceryState.step, scroll: scrollEl.scrollTop,
      deferred: groceryState.carryDeferred, typed: a2 ? a2.value : null
    }));
  }, 60);
}, 60);
""")
    assert out["step"] == "list", "a Plan-tab edit must not move the Shop panel"
    assert out["scroll"] == 733
    assert out["deferred"] is True
    assert out["typed"] == "oat milk"


@_needs_node
def test_the_approve_button_still_refills_through_the_same_helper():
    """
    GUARD, and the one that must not break: Approve is a refill and must go
    on landing on CARRY. Same helper, same call, one argument.
    """
    out = _shop(_SURFACES + """
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
loadGrocery();
setTimeout(function () {
  CARRIED = [{ id: 9, item: 'Spinach', quantity: '1 bag' }];
  groceryState.carryDeferred = true;
  refreshGrocerySurfaces({ refill: true });
  setTimeout(function () {
    console.log(JSON.stringify({ step: groceryState.step,
                                 deferred: groceryState.carryDeferred }));
  }, 60);
}, 60);
""")
    assert out["step"] == "carry"
    assert out["deferred"] is False


@_needs_node
def test_a_background_refresh_leaves_list_alone_even_with_nothing_deferred():
    """
    RED on main and on FIRST CUT (both navigate). GREEN on ROUND 2, which
    is the whole point of it: round 2 had the mechanism and nothing pinned
    it, so this test's value is not its redness against any commit — it is
    that it reddens under the mutation below.

    THE ONE THAT PINS `opts.background`, and it exists because the test
    above it did not.

    Six mutations were run against round 2 and five bit. The one that did
    not: make `loadGrocery` ignore `opts.background` entirely — the whole
    mechanism deleted — and all 36 tests still passed. The reason is that
    `test_a_background_refresh_does_not_take_the_household_to_another_screen`
    sets `carryDeferred = true` first, and `groMaybeCarryFirst` returns
    early on `!carryDeferred` whether or not it is called. That test pins
    the `carryDeferred` half and never the `background` half.

    This is the case that has no `carryDeferred` to hide behind, and it is
    the ordinary one: Shop sitting on LIST with nothing carried, so nothing
    was ever deferred, and then a chat approval carries a line over for the
    first time. Exactly the turn this ticket is about.

    The lesson, worth more than the test: the docstring-label audit this
    file runs checks CLAIMS against redness, and an unpinned mechanism is
    invisible to it by construction — it has no claim to be wrong about.
    Mutation is the only thing that finds that, and it has to be run against
    a green suite.
    """
    out = _shop("""
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
loadGrocery();
setTimeout(function () {
  var foot = GRO_NODES['#gro-foot'];
  var addRow = foot.querySelector('#gro-add-item');
  if (addRow) addRow.value = 'oat milk';
  scrollEl.scrollTop = 733;
  // NOTHING deferred — there was nothing to defer until now. This is what
  // makes the assertion reach opts.background instead of stopping at
  // groMaybeCarryFirst's own carryDeferred guard.
  CARRIED = [{ id: 9, item: 'Spinach', quantity: '1 bag' }];
  groceryState.carryDeferred = false;
  refreshGroceryPanel();
  setTimeout(function () {
    var a2 = GRO_NODES['#gro-foot'].querySelector('#gro-add-item');
    console.log(JSON.stringify({
      step: groceryState.step, scroll: scrollEl.scrollTop,
      deferred: groceryState.carryDeferred, typed: a2 ? a2.value : null
    }));
  }, 60);
}, 60);
""")
    assert out["deferred"] is False, "the harness must not have deferred it"
    assert out["step"] == "list", (
        "a background re-read must not open CARRY — this is the assertion "
        "that reaches opts.background rather than carryDeferred"
    )
    assert out["scroll"] == 733
    assert out["typed"] == "oat milk"


@_needs_node
def test_a_trip_at_its_last_stop_folds_to_the_wrap_up():
    """
    CHARACTERISATION (green either way), the sibling of the SORT fold and
    undisclosed in the first round. renderGrocery folds `next` -> `wrap`
    when nothing is left to choose between — so a chat change that empties
    the remaining stops moves a shopper from "where next?" to the wrap-up.
    Pre-existing and right (there is no question left to ask); named here
    so the list of what a refresh can move is complete.
    """
    out = _shop("""
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
groceryState.step = 'next';
groceryState.tripStops = ['Loblaws', 'Costco'];
groceryState.tripDone = { Loblaws: true, Costco: true };   // both behind us
groceryState.tripStartedAt = Date.now();
groceryState.tripRestored = true;
refreshGroceryPanel();
setTimeout(function () { console.log(JSON.stringify({ step: groceryState.step })); }, 60);
""")
    assert out["step"] == "wrap"


@_needs_node
def test_refreshing_a_shop_tab_that_was_never_opened_costs_nothing():
    """
    GUARD. The call is unconditional inside the week branch, so it runs for
    every household — including the many who never open Shop in a page view.
    An unbuilt panel costs nothing, which is what makes "refresh
    unconditionally" cheap enough to be the right answer.

    It pins the PROPERTY, not one line: there are two guards behind it
    (refreshGroceryPanel's `if (groIsBuilt())` and loadGrocery's own
    `if (!panel || !panel.dataset.built) return;`), so removing either one
    alone leaves this green. Measured — removing both makes it fail with
    eight requests where it wants none. Defence in depth is the right shape
    here and the test says what it can honestly see.
    """
    out = _shop("""
delete panels.grocery.dataset.built;
refreshGroceryPanel();
setTimeout(function () {
  console.log(JSON.stringify({ fetched: FETCHED.length, data: groceryState.data }));
}, 60);
""")
    assert out["fetched"] == 0
    assert out["data"] is None
