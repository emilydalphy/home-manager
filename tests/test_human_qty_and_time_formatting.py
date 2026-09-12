"""
Quantities and times as a person writes them (design-tidy pass 2026-09-11,
item 14; DESIGN_SYSTEM §8: "time as a person would say it").

Ingredients were rendering the server's plain decimal straight through
(app/tools/quantities.py's _format_quantity produces "0.5 lb", "0.75 cup" —
correct math, not how a person writes it down), and the one place shell.js
already converted a 24-hour clock into words (morningClock, for the morning
text preference) had no equivalent for a measured amount. This adds one
formatter for each — humanQty/humanQtyText for amounts, humanTime for
clocks — and both are used at cookIngredientLabel (meal screen ingredients,
cook focus's "Everything out" list and its own cook-getout rows) and
morningClock (the morning-text preference's time picker), respectively.

There is no browser-only behaviour here to speak of — these are pure string
formatters — so this runs them under node against a small stub, the way
tests/test_stores_multiselect.py and friends do, rather than only asserting
their presence as a source marker.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute shell.js's own functions"
)


def _extract(name: str) -> str:
    """Lift one brace-balanced `function name(...) {...}` out of shell.js."""
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


def _var(name: str) -> str:
    """Lift one `var NAME = [ ... ];` array literal out of shell.js."""
    start = SHELL_JS.index(f"var {name} = [")
    end = SHELL_JS.index("];", start) + 2
    return SHELL_JS[start:end]


_HARNESS = (
    _var("HUMAN_QTY_FRACTIONS")
    + "\n"
    + _extract("humanQtyAmount")
    + "\n"
    + _extract("humanQty")
    + "\n"
    + _extract("humanQtyText")
    + "\n"
    + _extract("humanTime")
    + "\n"
    + "function morningClock(hhmm) { return humanTime(hhmm || '07:00'); }\n"
)


def _run(expr: str):
    script = _HARNESS + f"console.log(JSON.stringify({expr}));\n"
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# --- humanQty / humanQtyAmount --------------------------------------------

@_needs_node
@pytest.mark.parametrize(
    "n,unit,expected",
    [
        (0.5, "lb", "½ lb"),
        (0.75, "cup", "¾ cup"),
        (0.25, "cup", "¼ cup"),
        (1, "head", "1 head"),
        (2, "lbs", "2 lbs"),
        (1.5, "cups", "1 ½ cups"),
        (0.125, "tsp", "⅛ tsp"),
        (3, "", "3"),
    ],
)
def test_human_qty_pairs_amount_and_unit(n, unit, expected):
    assert _run(f"humanQty({json.dumps(n)}, {json.dumps(unit)})") == expected


@_needs_node
def test_human_qty_amount_leaves_an_unrecognized_decimal_alone():
    """0.05 isn't within the 0.05 tolerance of any nice fraction this app's
    own quantity math produces — better a plain decimal than a
    wrong-looking guess."""
    assert _run("humanQtyAmount(0.05)") == "0.05"


# --- humanQtyText (the shape cookIngredientLabel actually has to work with) -

@_needs_node
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0.5 lb", "½ lb"),
        ("0.75 cup", "¾ cup"),
        ("2 heads", "2 heads"),
        ("1 head", "1 head"),
        ("", ""),
        ("to taste", "to taste"),  # no leading number: left alone, not mangled
    ],
)
def test_human_qty_text_reformats_only_the_leading_amount(raw, expected):
    assert _run(f"humanQtyText({json.dumps(raw)})") == expected


@_needs_node
def test_cook_ingredient_label_renders_human_quantities():
    """The actual call site: cookIngredientLabel composes {qty}{item} — this
    is the exact "0.5 lbs Flank steak" -> "½ lb Flank steak" the ticket
    named. Extracted fresh (not part of _HARNESS) since it pulls in the
    formatter under its own name, matching production wiring."""
    harness = _HARNESS + _extract("cookIngredientLabel") + "\n"
    script = harness + "console.log(JSON.stringify(cookIngredientLabel({ qty: '0.5 lb', item: 'Flank steak' })));\n"
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    assert json.loads(res.stdout.strip()) == "½ lb Flank steak"


# --- humanTime / morningClock ----------------------------------------------

@_needs_node
@pytest.mark.parametrize(
    "hhmm,expected",
    [
        ("18:30", "6:30 pm"),
        ("07:00", "7:00 am"),
        ("00:00", "12:00 am"),
        ("12:00", "12:00 pm"),
        ("12:30", "12:30 pm"),
        ("23:59", "11:59 pm"),
    ],
)
def test_human_time_reads_a_24_hour_clock_the_way_a_person_says_it(hhmm, expected):
    assert _run(f"humanTime({json.dumps(hhmm)})") == expected


@_needs_node
def test_morning_clock_keeps_its_own_default_through_human_time():
    """morningClock is a thin wrapper now (item 14) — its one behavioural
    difference from a bare humanTime call is the 7:00 am default for a
    blank/unset time, which this checks survives the refactor."""
    assert _run("morningClock('')") == "7:00 am"
    assert _run("morningClock(null)") == "7:00 am"
    assert _run("morningClock('18:30')") == "6:30 pm"
