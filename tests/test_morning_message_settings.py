"""
Settings → Morning text: "What should it include? · for everyone"
(Loop Board "Morning message: the household chooses what it includes",
2026-10-05 — the screen half; the backend is tests/test_morning_message_parts.py).

Six boxes drawn off the server's part_choices, ticks that repaint a Preview
from the server's own composer, and a Save that posts the household's one
answer. The renderer and the tick are run under node (shell.js's own
functions, the house standard); the save and the preview's URL are read off
the source because they need a network.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import nodeharness
import pytest

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute shell.js's own functions"
)


def _extract(name: str) -> str:
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
    return SHELL_JS[start: j + 1]


def _var(name: str, opener: str, closer: str) -> str:
    start = SHELL_JS.index(f"var {name} = {opener}")
    return SHELL_JS[start: SHELL_JS.index(closer, start) + len(closer)]


_HARNESS = "\n".join([
    _var("CLOCK_WORDS", "[", "];"), _var("MORNING_TICK_SVG", "'", "';"),
    _extract("humanTime"), _extract("clockWord"), _extract("escapeHtml"),
    _extract("morningPartsChosen"), _extract("morningPreviewInnerHtml"), _extract("morningPartsHtml"),
    _extract("renderMorningSheet"), _extract("toggleMorningPart"),
    "var body = { innerHTML: '' };",
    "var morningSheetEl = { querySelector: function () { return body; } };",
    "var morningParts = null; var morningPreview = null;",
    "var refreshed = []; function refreshMorningPreview(ms) { refreshed.push(ms); }",
    "var KEYS = ['meals','freezer','prep','start','shop','away'];",
    "var LABELS = {meals:\"Today's meals\",freezer:'What to take out of the freezer',prep:'Prep to do today',"
    "start:'When to start cooking dinner',shop:'Shopping day reminder',away:\"Who's away tonight\"};",
    "function settings(parts, adults) { return { time: '07:00', parts: parts, adults: adults === undefined ? "
    "[{ member_id: 7, name: 'Emily', phone: '+14165550100', on: true }] : adults, part_choices: KEYS.map(function (k) {"
    " return { key: k, label: LABELS[k], says: '', on: parts.indexOf(k) !== -1 }; }) }; }",
    "var prefsState = { morningText: null, eveningNudge: null };",
])


def _node(script: str):
    res = nodeharness.run_node(_HARNESS + "\n" + script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _render(parts, preview="null", adults="undefined") -> str:
    return _node(
        f"prefsState.morningText = settings({json.dumps(parts)}, {adults});\n"
        f"morningPreview = {preview};\n"
        "renderMorningSheet();\nconsole.log(JSON.stringify(body.innerHTML));"
    )


@_needs_node
def test_the_six_boxes_are_drawn_off_the_servers_words_with_the_answer_ticked():
    html = _render(["meals", "freezer", "prep"])
    assert "What should it include?" in html and "· for everyone" in html
    boxes = re.findall(r'role="checkbox" aria-checked="(true|false)" data-morning-part="(\w+)"', html)
    assert [k for _, k in boxes] == ["meals", "freezer", "prep", "start", "shop", "away"]
    assert [k for on, k in boxes if on == "true"] == ["meals", "freezer", "prep"]
    assert "Today&#39;s meals" in html or "Today's meals" in html
    # The section sits above the one Save, which keeps it (§2b S10).
    assert html.index("morning-parts") < html.index('id="morning-save"')
    assert html.index('id="morning-preview"') < html.index('id="morning-save"')


@_needs_node
def test_with_nobody_to_send_to_there_are_no_boxes_and_no_preview():
    html = _render(["meals"], adults="[]")
    assert "data-morning-part" not in html and "morning-preview" not in html


@_needs_node
@pytest.mark.parametrize("preview, says, not_says", [
    ("{ state: 'ready', data: { would_send: true, push_text: 'Tonight: tacos.', text: 'Tonight: tacos. https://x' } }",
     "Tonight: tacos.", "https://x"),
    ("{ state: 'ready', data: { would_send: false, push_text: null, text: null } }",
     "so no message would go out", "Tonight"),
    ("{ state: 'failed' }", "The preview didn’t load. Your choices still save.", "Tonight"),
    ("null", "Reading today’s plan…", "Tonight"),
])
def test_the_preview_says_the_message_or_plainly_that_none_goes(preview, says, not_says):
    html = _render(["meals"], preview=preview)
    box = html[html.index('id="morning-preview"'):]
    assert says in box and not_says not in box


@_needs_node
def test_a_tick_changes_the_answer_in_the_boxes_order_and_asks_for_a_new_preview():
    out = _node(
        "prefsState.morningText = settings(['meals','freezer','prep']);\n"
        "function btn(key, on) { var a = { 'data-morning-part': key, 'aria-checked': on ? 'true' : 'false' };"
        " return { getAttribute: function (n) { return a[n]; }, setAttribute: function (n, v) { a[n] = v; }, a: a }; }\n"
        "var away = btn('away', false); toggleMorningPart(away);\n"
        "var start = btn('start', false); toggleMorningPart(start);\n"
        "var meals = btn('meals', true); toggleMorningPart(meals);\n"
        "console.log(JSON.stringify({ parts: morningParts, saved: prefsState.morningText.parts,"
        " checked: [away.a['aria-checked'], start.a['aria-checked'], meals.a['aria-checked']], refreshed: refreshed }));"
    )
    assert out["parts"] == ["freezer", "prep", "start", "away"]
    assert out["saved"] == ["meals", "freezer", "prep"]  # a tick saves nothing
    assert out["checked"] == ["true", "true", "false"]
    assert len(out["refreshed"]) == 3


def test_the_preview_asks_the_composer_with_the_ticks_and_save_posts_the_households_answer():
    load = _extract("loadMorningPreview")
    assert "Api.fetch('/api/morning-text/preview?parts=' + encodeURIComponent(parts.join(',')))" in load
    assert "seq !== morningPreviewSeq" in load  # a late answer never paints over a newer one
    save = SHELL_JS[SHELL_JS.index("async function saveMorningSheet()"):SHELL_JS.index("function openMorningSheet(")]
    post = save.index("Api.fetch('/api/morning-text/parts'")
    assert post < save.index("for (var i = 0; i < rows.length; i++)")
    assert "member_id" not in save[post:save.index("for (var i = 0")]
    # Every box is a whole row at 48px, drawn in tokens only.
    css = SHELL_CSS[SHELL_CSS.index("What should it include? · for everyone"):SHELL_CSS.index(".morning-preview-quiet")]
    assert "min-height: 48px; /* Rule 6 */" in css
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", css)


_SAVE_HARNESS = "\n".join([
    _var("MORNING_PARTS_FAILED", "'", "';"), _extract("morningPartsChosen"), _extract("paintMorningPreview"),
    "async " + _extract("saveMorningSheet"),
    "var morningParts = ['meals']; var morningPreview = null; var morningPreviewSeq = 0;",
    "var prefsState = { open: false, morningText: { time: '07:00', parts: ['meals'], adults: [{ member_id: 7, name: 'Emily', on: true }] } };",
    "function renderPrefsRows() {} function morningClock() { return '7'; }",
    "var note = { hidden: true, textContent: '' };",
    "function el(attrs) { return { value: attrs.value, getAttribute: function (n) { return attrs[n]; } }; }",
    "var row = { getAttribute: function () { return '7'; }, querySelector: function (q) {",
    "  if (q.indexOf('tel') !== -1) return el({ value: '+14165550100' });",
    "  if (q.indexOf('data-morning-toggle') !== -1) return el({ 'aria-pressed': 'true' });",
    "  return null; } };",
    "var body = { querySelector: function (q) { if (q === '#morning-note') return note;",
    "  if (q === '[data-morning-part]') return {}; return null; },",
    "  querySelectorAll: function () { return [row]; } };",
    "var morningSheetEl = { querySelector: function (q) { return q === '#morning-body' ? body : null; } };",
])


@_needs_node
@pytest.mark.parametrize("parts_ok, says", [
    (False, "Everything saved except what it should include. Try Save again."),
    (True, "Saved. Next one’s at 7."),
])
def test_a_failed_parts_save_says_which_part_failed(parts_ok, says):
    script = _SAVE_HARNESS + "\n" + (
        "var Api = { fetch: async function (url) { var ok = url.indexOf('/parts') === -1 || " + ("true" if parts_ok else "false") + ";"
        " return { ok: ok, json: async function () { return ok ? { parts: ['meals'], settings: prefsState.morningText, configured: true } : { detail: 'no' }; } }; } };\n"
        "saveMorningSheet().then(function () { console.log(JSON.stringify(note.textContent)); });"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout.strip()) == says
