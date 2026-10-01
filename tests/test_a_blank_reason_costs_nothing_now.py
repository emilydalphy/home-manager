"""
A blank "why this night" costs nothing in the morning report any more —
and three comments in app/ still said it did.

Found 2026-10-01 while reading the morning error check. Household 1's
FOOD section spent ELEVEN of its thirty findings on reasoning_is_specific
in the 7-day window, and every one the report printed read "<date> snack
('Roasted Chickpeas') has no reasoning at all."

That is not a live defect: the rule stopped producing that message on
2026-09-30 (45e2bc0) — the week call is no longer asked for a per-slot
line, so a blank is the normal case and `_reasoning_is_specific` returns
early on it. Those rows are historical, logged before the deploy, and
age out of the window by 2026-10-07. Checked rather than assumed: the
string "has no reasoning at all" survives nowhere in app/ except in the
comments this branch corrects.

WHAT WAS ACTUALLY WRONG was those comments. Three of them, in two
modules, each giving a MEASURED reason for choosing a non-blank reason —
and the measurement had expired:

  * cap_enforce.REPICK_REASON  ("...it was measured to be worse: fires
    'has no reasoning at all' for every row it leaves blank")
  * cap_enforce.MOVE_REASON    ("...emptying it re-introduces the
    warnings above")  <- an active instruction to a future session, and
                           now false
  * meal_variety.GAP_FILL_REASON ("NOT blank, deliberately — ...duly
    warns 'has no reasoning at all' for every blank row it sees")

Measured on this tree before the comments were touched: a blank reason
produces ZERO reasoning_is_specific findings; only generic filler is
caught. So emptying any of those three costs nothing in the report, and
MOVE_REASON's comment was telling the next reader the opposite. An
unmeasured cost claim in a comment is what the next reader acts on —
this log has had to unpick that before.

THE DECISIONS THEMSELVES ARE UNCHANGED. All three constants keep their
current values, on the reasons that never depended on the warning. What
changed is the justification written beside them, plus one thing worth
Emily's eyes: rewording or emptying MOVE_REASON is now a pure copy
decision with no engineering reason pushing back, which it was not when
that comment was written.

WHAT THIS FILE PINS, and what it deliberately does not. The rule's own
behaviour on a blank is already pinned (test_plan_quality.py::
test_a_blank_reason_is_not_a_finding, and test_speed_first_week_2026_09_30
drives it end to end through a generated week) — this file does not
duplicate either. What nothing pinned is the RELATIONSHIP between the
reason constants the app writes and the rule that judges them: that no
sentence Pomona puts in a "why this night" is itself generic filler. A
sweep, derived from the modules rather than hand-listed, so a reason
constant added tomorrow is covered without anybody editing this file.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import re
import textwrap
from pathlib import Path

import pytest

from app.tools import plan_quality

REPO = Path(__file__).resolve().parent.parent


def _planned(reasoning):
    """One planned dinner carrying this reason and nothing else unusual."""
    return [{
        "date": "2026-10-05", "slot": "dinner", "meal_name": "Chili",
        "slot_state": "planned", "reasoning": reasoning,
        "food_groups": ["protein", "carb", "vegetable"],
    }]


def _findings(reasoning):
    return plan_quality._reasoning_is_specific(_planned(reasoning), {})


# --------------------------------------------------------------------------
# 1. The sweep: no reason Pomona writes is generic filler
# --------------------------------------------------------------------------

def _reason_constants():
    """
    Every public module-level constant in app/tools whose name says it is
    a reason, derived rather than hand-listed — the shape this repo's
    other sweeps take, so a reason added tomorrow is covered without
    anybody remembering this file exists.

    Private names (a leading underscore) are left out: _BANNED_REASONING_
    PHRASES and _OPEN_REASON_RE are the rule's own machinery, not
    sentences the app writes, and _MAX_REASON is a length.
    """
    import app.tools as tools_pkg

    found = {}
    for mod_info in pkgutil.iter_modules(tools_pkg.__path__):
        mod = importlib.import_module(f"app.tools.{mod_info.name}")
        for name, value in vars(mod).items():
            if name.startswith("_") or not name.endswith("REASON"):
                continue
            if not isinstance(value, (str, type(None))):
                continue
            found[f"{mod_info.name}.{name}"] = value
    return found


def _filled(text):
    """
    A reason template with its placeholders filled, so the sweep judges
    the sentence a household actually reads. Two carry one today
    (bring_over.REASON's {weekday}, freezer_portions.REASON's {dish});
    anything else gets a plausible filler rather than being skipped.
    """
    fillers = {"weekday": "Tuesday", "dish": "Bean Chili"}
    for key in re.findall(r"\{(\w+)\}", text):
        fillers.setdefault(key, "Bean Chili")
    return text.format(**fillers)


def test_the_sweep_finds_the_reasons_it_is_supposed_to():
    """
    GUARD ON THE GUARD. This repo has recorded a sweep silently stopping
    matching twice, so the sweep's own reach is asserted: the four
    constants this branch is about must be among what it finds, by name.
    Mutation: narrow the name test to `== "REASON"` and this reddens.
    """
    found = _reason_constants()
    for name in (
        "cap_enforce.REPICK_REASON",
        "cap_enforce.MOVE_REASON",
        "meal_variety.REPEAT_REASON",
        "meal_variety.GAP_FILL_REASON",
        "bring_over.REASON",
        "freezer_portions.REASON",
    ):
        assert name in found, f"the sweep no longer finds {name}"
    assert len(found) >= 8, f"the sweep's reach shrank: {sorted(found)}"


@pytest.mark.parametrize("name", sorted(_reason_constants()))
def test_no_reason_the_app_writes_is_generic_filler(name):
    """
    GUARD, and the gap this file exists to close — nothing pinned the
    relationship between the reasons the app writes and the rule that
    judges them. Mutation: reword any of them to a phrase in
    plan_quality._BANNED_REASONING_PHRASES (e.g. MOVE_REASON = "A
    balanced meal.") and its case reddens.

    A None or an empty reason passes on purpose: since 2026-09-30 a blank
    is the normal case and is not a finding. That is the whole point of
    this branch, and it is pinned positively in section 2 below rather
    than left as the absence of a failure here.
    """
    value = _reason_constants()[name]
    if not value:
        return
    assert _findings(_filled(value)) == [], (
        f"{name} is itself generic filler, so every row carrying it warns"
    )


# --------------------------------------------------------------------------
# 2. The claim the three comments now make, pinned positively
# --------------------------------------------------------------------------

def test_a_blank_reason_produces_no_finding_and_filler_still_does():
    """
    GUARD. Deliberately one test covering both halves rather than two:
    the claim the corrected comments make is a CONTRAST — blank is free,
    filler is not — and two separate tests could each pass with the rule
    gutted in a different direction. Mutation: drop
    `_reasoning_is_specific`'s `if not reasoning: continue` and the first
    assertion reddens; empty _BANNED_REASONING_PHRASES and the second
    does.
    """
    assert _findings("") == []
    assert _findings(None) == []
    assert len(_findings("A balanced meal.")) == 1


@pytest.mark.parametrize("name", [
    "cap_enforce.REPICK_REASON",
    "cap_enforce.MOVE_REASON",
    "meal_variety.GAP_FILL_REASON",
])
def test_emptying_one_of_the_three_now_costs_nothing_in_the_report(name):
    """
    GUARD, and the label is corrected rather than quietly — the first
    draft of this docstring called it a CATCH. It is green on main,
    because main's RULE already does not warn on a blank; only main's
    COMMENTS say otherwise, and prose is not what this test reads. The
    one genuine catch in this file is the prose sweep below.

    What this pins is the sentence the three corrected comments now
    assert and the old ones denied: emptying any of these three produces
    no reasoning_is_specific finding. NOT that the choice is free: a
    review measured (2026-10-01) that emptying MOVE_REASON or
    REPICK_REASON each costs one red test in
    tests/test_rush_cap_enforced.py, which forbids a blank for the very
    reason retired here. Only GAP_FILL_REASON is free overall. It reddens
    on any tree where the rule warns on a blank again — which is the
    mutation named in test_a_blank_reason_produces_no_finding above, and
    the day that happens all three comments are false again.

    Nothing here empties the constants; it asserts what it would cost if
    Emily chose to.
    """
    assert _findings("") == [], (
        f"a blank {name} would warn, so the old comment was right after all"
    )


# --------------------------------------------------------------------------
# 3. The stale claim is gone from app/
# --------------------------------------------------------------------------

def test_no_module_claims_in_the_present_tense_that_a_blank_reason_warns():
    """
    CATCH, and the one source-level test here, because the defect WAS
    prose. Read comment-stripped is not possible for the thing being
    checked — the claim lives IN a comment — so this reads the raw text
    and requires every surviving mention of the old message to sit beside
    a word that dates it ("stopped", "expired", "used to", "historical",
    "corrected", "no longer"). On main all three mentions are bare
    present-tense claims and this reddens.

    CASE-INSENSITIVE, and that is a correction rather than a tidy-up
    (found by review, 2026-10-01). The first cut matched the lower-case
    string against the raw line, so a capitalised claim was invisible to
    it — and there IS one, `app/tools/plan_quality.py`'s own "No
    reasoning at all is no longer a finding", inside the very rule this
    file is about. That one is correctly dated, so the sweep stays green;
    what was wrong is that an UNDATED capitalised twin would have sailed
    through. Reproduced: the identical attack sentence capitalised passed
    while its lower-case form failed.

    THE GATE IS WEAK, AND THE WEAKNESS IS MEASURED RATHER THAN WAVED AT:
    the dating words are this codebase's ordinary prose, so a bare
    present-tense claim within a line or two of an unrelated one passes.
    Counted across app/ on this branch: "used to" 139, "no longer" 82,
    "expired" 49, "stopped" 32, "corrected" 23 — 325 occurrences in all,
    against a window of nineteen lines. So this catches a claim standing
    on its own and cannot catch one standing next to somebody else's
    retraction. The honest alternative is a shorter window, which trades
    that for false alarms on the long comment blocks this repo writes;
    the window is 12/6 because the claim is a sentence, not a line.
    """
    stale = []
    for path in sorted((REPO / "app").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if "no reasoning at all" not in line.lower():
                continue
            # The dating word may be a line or two either side — these are
            # long comment blocks, and the claim is a sentence, not a line.
            window = "\n".join(text.splitlines()[max(0, n - 12):n + 6]).lower()
            if not any(w in window for w in
                       ("stopped", "expired", "used to", "historical",
                        "corrected", "no longer")):
                stale.append(f"{path.relative_to(REPO)}:{n}")
    assert stale == [], (
        "these still claim a blank reason warns, which stopped being true "
        f"on 2026-09-30: {stale}"
    )


def test_the_rule_itself_no_longer_carries_the_old_message():
    """
    GUARD. The message only exists in comments and one test fixture now;
    if it comes back in the rule, the comments this branch corrected are
    false again and so is the test above. Mutation: re-add the blank-
    reason branch with its old message and this reddens.

    Comment-stripped (ast.unparse drops comments by construction) AND
    case-insensitive, for the same reason the sweep above is: the rule's
    own explaining comment names the message in order to say it is gone,
    so a raw case-insensitive read of the source would be reddened by the
    correction rather than by a regression. Reading the CODE is what lets
    the casing stop mattering. The idiom is `_code_of`'s, from
    test_holiday_reopen_atomic.py — an assertion prose can satisfy is not
    an assertion, and the inverse holds too.
    """
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(plan_quality._reasoning_is_specific)))
    body = tree.body[0].body
    if body and isinstance(body[0], ast.Expr) \
            and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    code = "\n".join(ast.unparse(node) for node in body)
    assert "no reasoning at all" not in code.lower()
