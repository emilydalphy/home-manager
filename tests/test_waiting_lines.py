"""
The rotating line under the spinner while a week is being planned
(static/waiting-lines.js — Loop Board "Make loading screens do more than
just wait", Emily 2026-09-08: "add some personality").

Two kinds of test here, for two different failure modes:

- Source-level, because the whole value of the change is that ONE
  component serves every screen that plans a week. A second screen quietly
  growing its own hardcoded list is exactly the drift worth catching, and
  is invisible to any behavioural test of the helper alone.
- Behavioural under node, because the one thing this must never do is
  throw or hand back `undefined` on a stage nobody anticipated. A wait
  screen that breaks is strictly worse than the static label it replaced.
"""
import json
import re
import shutil

import nodeharness
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
WAITING_JS = STATIC / "waiting-lines.js"

# Every screen that shows a week-generation wait. onboarding is the
# first-week reveal, plan-week is the drafting step, shell.js is the Meals
# "Try again" rebuild.
ENTRY_POINTS = ("onboarding.html", "plan-week.html", "shell.js")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the helper's own code"
)


def _lines_by_stage() -> dict[str, list[str]]:
    """The STAGE_LINES object, read out of the source under node."""
    res = nodeharness.run_node(
        f"console.log(JSON.stringify(require({str(WAITING_JS)!r}).STAGE_LINES))",
        timeout=30,
    )
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout)


# ---------- source-level: one component, used everywhere ----------


def test_every_wait_screen_uses_the_shared_helper():
    """
    Both entry points named in the ticket, plus the drafting step they
    share a stream with, go through PomonaWaiting rather than rolling
    their own list of lines.
    """
    for name in ENTRY_POINTS:
        src = (STATIC / name).read_text()
        assert "PomonaWaiting" in src, f"{name} does not use the shared waiting-line helper"
        assert "startWaitingLines" in src, f"{name} never starts a waiting line"


def test_the_pages_load_the_helper():
    """A page that calls it but never loads it silently falls back forever."""
    for name in ("onboarding.html", "plan-week.html", "shell.html"):
        src = (STATIC / name).read_text()
        assert "/static/waiting-lines.js" in src, f"{name} never loads waiting-lines.js"


def test_styling_lives_in_one_css_block():
    """
    theme.css is the one stylesheet all three pages load, so the component
    is styled once there — not re-declared per page.
    """
    theme = (STATIC / "theme.css").read_text()
    assert ".waiting-line {" in theme
    # Reduced motion: opacity only, no transform. The blanket rule further
    # down theme.css already cuts animation duration, but this component
    # states its own intent so a future edit to that rule can't hand the
    # slide back.
    reduced = re.search(
        r"@media \(prefers-reduced-motion: reduce\) \{\s*\.waiting-line-in.*?\n\}", theme, re.S
    )
    assert reduced, "no reduced-motion rule for the waiting line"
    assert "transform" not in reduced.group(0), "reduced motion must animate opacity only"


# ---------- the copy rules ----------


@_needs_node
def test_the_line_set_is_the_right_size():
    stages = _lines_by_stage()
    all_lines = [line for lines in stages.values() for line in lines]
    assert 8 <= len(all_lines) <= 12, f"{len(all_lines)} lines; the set should be 8-12"
    assert len(set(all_lines)) == len(all_lines), "a line is duplicated across stages"
    # Every stage needs enough to rotate through without repeating itself
    # inside a single ~30-second wait.
    for stage, lines in stages.items():
        assert len(lines) >= 2, f"stage {stage!r} has too few lines to rotate"


@_needs_node
def test_the_lines_follow_the_voice_rules():
    """
    DESIGN_SYSTEM.md section 8, as a check rather than a vibe: sparing
    punctuation, no emoji, and nothing long enough to be unreadable in the
    four seconds it is on screen.
    """
    stages = _lines_by_stage()
    all_lines = [line for lines in stages.values() for line in lines]
    exclamations = sum(line.count("!") for line in all_lines)
    assert exclamations <= 1, f"{exclamations} exclamation marks; at most one in the whole set"
    # Emoji, not "non-ascii" — the copy uses the app's curly apostrophe.
    emoji = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")
    for line in all_lines:
        assert not emoji.search(line), f"emoji in {line!r}"
        assert line.endswith("."), f"{line!r} should be a plain sentence"
        assert len(line) <= 62, f"{line!r} is too long to read in one rotation"


@_needs_node
def test_no_line_promises_the_grocery_list():
    """
    Groceries are written when the household APPROVES a week, not when one
    is drafted (add_ingredients_to_grocery_list in app/tools/weekly_plan.py).
    A wait line about the list would be the exact thing this component
    exists not to do: fake progress through work that isn't happening.
    """
    stages = _lines_by_stage()
    for lines in stages.values():
        for line in lines:
            assert "grocery" not in line.lower(), f"{line!r} promises the list too early"
            assert "shopping" not in line.lower(), f"{line!r} promises the list too early"


# ---------- behavioural: the stage mapping never returns undefined ----------


@_needs_node
def test_stage_mapping_never_returns_undefined():
    """
    Run waitingLinesForStage against every stage that exists plus a pile of
    things that don't — a renamed stage, a typo, null, a number, an object.
    Each must come back a non-empty array of strings.
    """
    harness = f"""
    const w = require({str(WAITING_JS)!r});
    const inputs = Object.keys(w.STAGE_LINES).concat([
      'done', 'finishing', 'READING', '', 'undefined', 'constructor',
      'toString', '__proto__', 'hasOwnProperty',
    ]);
    const weird = [undefined, null, 0, 1, true, false, {{}}, [], NaN];
    const results = [];
    for (const s of inputs.concat(weird)) {{
      const got = w.waitingLinesForStage(s);
      results.push({{
        stage: String(s),
        ok: Array.isArray(got) && got.length > 0 && got.every(l => typeof l === 'string' && l.length > 0),
      }});
    }}
    console.log(JSON.stringify(results));
    """
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    results = json.loads(res.stdout)
    # 'constructor'/'__proto__'/'toString' matter: a plain object lookup
    # finds Object.prototype members, so a naive `if (!lines)` guard would
    # hand a function back to the renderer and the wait screen would die on
    # `lines.length`.
    bad = [r["stage"] for r in results if not r["ok"]]
    assert not bad, f"waitingLinesForStage returned nothing usable for: {bad}"


@_needs_node
def test_the_renderer_survives_a_missing_element_and_a_double_stop():
    """
    startWaitingLines runs on unhappy paths too — a failed generation, a
    band torn down mid-rebuild. Neither a null element nor stopping twice
    may throw.
    """
    harness = f"""
    const w = require({str(WAITING_JS)!r});
    const c = w.startWaitingLines(null, {{ stage: 'nope' }});
    c.setStage('filling');
    c.stop();
    c.stop();
    console.log('ok');
    """
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    assert res.stdout.strip() == "ok"


@_needs_node
def test_a_stage_change_restarts_at_the_top_of_the_new_stage():
    """
    Rotation happens WITHIN a stage; moving stages starts that stage's
    first line rather than carrying an index across into a shorter list
    (which is how a stage change would land mid-way through, or off the
    end, of the lines it just switched to).
    """
    harness = f"""
    const w = require({str(WAITING_JS)!r});
    const painted = [];
    const el = {{
      textContent: '',
      offsetWidth: 0,
      classList: {{ add() {{}}, remove() {{}} }},
    }};
    Object.defineProperty(el, 'textContent', {{
      get() {{ return painted[painted.length - 1] || ''; }},
      set(v) {{ painted.push(v); }},
    }});
    // A huge interval so nothing rotates on its own; every paint below is
    // one this test asked for.
    const c = w.startWaitingLines(el, {{ stage: 'reading', intervalMs: 1e9 }});
    c.setStage('filling');
    c.setStage('filling');   // same stage twice must not repaint
    c.setStage('nonsense');  // unknown -> generic
    c.stop();
    console.log(JSON.stringify(painted));
    """
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    painted = json.loads(res.stdout)
    stages = _lines_by_stage()
    assert painted == [
        stages["reading"][0],
        stages["filling"][0],
        stages["generic"][0],
    ], painted
