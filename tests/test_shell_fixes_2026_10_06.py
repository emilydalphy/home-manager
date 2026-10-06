"""Four shell fixes from the 2026-10-06 walkthrough (branch shell-fixes-2026-10-06).

Same pattern as tests/test_cook_shelf.py: run shell.js's own functions under
node (tests/nodeharness.py) and read shell.css as text.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from tests import nodeharness

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _function(name: str) -> str:
    m = re.search(r"^  (?:async )?function " + re.escape(name) + r"\(", SHELL_JS, re.M)
    assert m, f"{name} not found in shell.js"
    start = m.start()
    i = SHELL_JS.index("{", SHELL_JS.index(")", start))
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


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_ESC = (
    "function escapeHtml(s){return String(s == null ? '' : s)"
    ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
)


# --------------------------------------------------------------------------
# Card 1 - the Cook chip on a Cook row: one line, and a tap opens the recipe
# --------------------------------------------------------------------------

def _row_html(row):
    return _node(
        _ESC
        + "const COOK_ICONS = { check: '<svg></svg>' };\n"
        + "const REHEAT_ACTION_LABEL = 'Mark eaten'; const REHEAT_UNDO_LABEL = 'Mark not eaten';\n"
        + _function("kitchenTodayRowHtml")
        + "\nconsole.log(JSON.stringify(kitchenTodayRowHtml(" + json.dumps(row) + ")));"
    )


def _row(**kw):
    base = {"idx": 3, "entryId": 11, "isReheat": False, "done": False, "title": "Chicken Tikka Masala",
            "line": "start by 5:35 · 55 min", "badge": "Cook", "prepped": None, "move": None}
    base.update(kw)
    return base


@_needs_node
def test_the_cook_chip_is_a_button_that_opens_the_recipe():
    html = _row_html(_row())
    m = re.search(r'<button[^>]*class="cook-badge[^"]*"[^>]*>Cook</button>', html)
    assert m, html
    assert 'data-cook="focus"' in m.group(0) and 'data-idx="3"' in m.group(0)


@_needs_node
def test_a_reheat_chip_stays_a_plain_label():
    html = _row_html(_row(isReheat=True, badge="Reheat"))
    assert '<span class="cook-badge cook-badge-tag cook-badge-warm"' not in html  # reheat is celadon
    assert re.search(r'<span class="cook-badge cook-badge-tag">Reheat</span>', html), html


def test_the_one_word_chip_never_wraps_or_shrinks():
    m = re.search(r"\.cook-badge-tag\s*\{([^}]*)\}", SHELL_CSS)
    assert m, ".cook-badge-tag rule is missing"
    rule = m.group(1)
    assert "white-space: nowrap" in rule
    assert "flex: 0 0 auto" in rule
    assert "max-width: none" in rule
    # A 44px hit area around the 24px chip (DESIGN_SYSTEM hard rule 6).
    assert re.search(r"button\.cook-badge-tap::after\s*\{[^}]*inset:\s*-10px", SHELL_CSS)


# --------------------------------------------------------------------------
# Card 2 - Today's snack row names who the snacks are for
# --------------------------------------------------------------------------

def _seed_snack_day(adult_snacks, child_snacks):
    import datetime  # noqa: F401
    from conftest import household_today
    from app.tools import day_meals, meal_plans, member_needs, recipes, weekly_plan
    from app.tools import household as household_tools

    day = household_today()
    for name, age in (("Dana", "adult"), ("Sam", "adult"), ("Leo", "child")):
        household_tools.add_member(name)
        household_tools.set_member_age_group(name, age)
    recipes.add_recipe("Apple Slices", ingredients=[{"item": "apple", "qty": "1", "category": "produce"}],
                       instructions=["Slice"], prep_time_minutes=5, cook_time_minutes=0, default_servings=4)
    pid = meal_plans.create_weekly_plan(day.isoformat(), day_count=1)["weekly_plan_id"]
    meal_plans.plan_meal(day.isoformat(), "Apple Slices", "snack", weekly_plan_id=pid)
    weekly_plan.approve_weekly_plan(pid)
    member_needs.save_member_needs(None, {"Dana": adult_snacks, "Sam": adult_snacks, "Leo": child_snacks})
    return day_meals


def test_snacks_only_the_child_has_are_labelled_with_the_child_not_everyone():
    from app.tools import moves
    _seed_snack_day(0, 2)
    row = [r for r in moves.today_moves()["day_meals"] if r["slot"] == "snack"][0]
    line = row["lines"][0]
    assert line["who"] == "Leo"
    assert line["initials"] == ["L"]
    assert "everyone" not in json.dumps(row)


def test_snacks_everyone_has_still_say_everyone():
    from app.tools import moves
    _seed_snack_day(1, 2)
    row = [r for r in moves.today_moves()["day_meals"] if r["slot"] == "snack"][0]
    assert row["lines"][0]["who"] == "everyone"
