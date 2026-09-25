"""
The Plan rows, option 1A (Emily, 2026-09-25), run against shell.js's own
renderers under node:

  1. The dish name on a row carries a small --apricot-label chevron (the
     title is the way into the recipe) and keeps its --ink.
  2. Under it, one line: the time as plain text, the reason after it
     (no dotted underline, no tap, no pop), and the row's sand buttons at
     its right — Swap and a new "Tweak" on a draft / Check the week,
     Done and Swap on the approved root.
  3. "Tweak" only where plateCanChange() says the plate can change, and
     on a What we're eating row only for a dish that is one cook ahead.
  4. The "Tweak" sheet: the dish as its head, the plate's parts as rows
     with Change / Add, a missing part as the dashed chip, "Leave it as it
     is" — and its buttons hand off to the existing part sheets, which
     mark the row "Changed" (S6) when they save.
  5. The "Changed" pill has room to breathe.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from test_week_seven_tiles import _extract, _extract_var
from test_plan_cards_2026_09_18 import _prelude, _entry, _day, _MON, _TUE, _WED

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
DESIGN = (REPO / "DESIGN_SYSTEM.md").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the renderers")


def _run(harness: str):
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _plate_prelude() -> str:
    return (
        _prelude()
        + _extract("isRealCook", SHELL_JS) + "\n"
        + _extract("plateCanChange", SHELL_JS) + "\n"
        + _extract_var("PLATE_CARET", SHELL_JS) + "\n"
        + _extract_var("PLATE_PLUS", SHELL_JS) + "\n"
        + _extract_var("PLATE_NO_NAME", SHELL_JS) + "\n"
        + _extract("platePartChipHtml", SHELL_JS) + "\n"
        + _extract("tweakSheetBodyHtml", SHELL_JS) + "\n"
    )


_PARTS = [
    {"role": "protein", "word": "Protein", "name": "Chicken thighs", "source": "dish", "missing": False},
    {"role": "vegetable", "word": "Veg", "name": "Green beans", "source": "side", "missing": False},
    {"role": "carb", "word": "Carb", "name": None, "source": None, "missing": True},
]


def _plated(title, **kw):
    return _entry(title, plate_parts=[dict(p) for p in _PARTS], **kw)


def _row(html: str, slot: str) -> str:
    start = html.index(f'data-wk-row="{slot}"')
    nxt = html.find('data-wk-row="', start + 1)
    return html[start:nxt if nxt != -1 else len(html)]


# ---------------------------------------------------------------------------
# 1–3. The row
# ---------------------------------------------------------------------------

@_needs_node
def test_a_draft_row_has_the_chevron_title_then_the_time_line_with_swap_and_tweak_it():
    day = _day(_TUE, dinner=_plated("Lemon chicken & orzo", meta="30 min", reason="lighter than the chops."),
               lunch=_entry("Chickpea salad jars", meta="10 min"), breakfast=_plated("Eggs", meta="10 min"))
    html = _run(_plate_prelude() + f"weekState.days = [{json.dumps(day)}];\n"
                f"console.log(JSON.stringify(wkDayCardHtml({json.dumps(day)}, 0, {{ done: false, swapLabel: 'Swap' }})));")
    dinner = _row(html, "dinner")
    assert '<div class="wk-row has-foot" data-wk-row="dinner">' in html
    # The title: still the recipe link, its text then the chevron.
    title = re.search(r'<button type="button" class="wk-row-name dish-link" data-wk-meal="dinner">(.*?)</button>', dinner).group(1)
    assert title.startswith("Lemon chicken &amp; orzo<svg class=\"wk-row-chev\"")
    assert 'stroke="currentColor"' in title and 'stroke-width="2.2"' in title
    # The line under it: the time and the reason as text — no button, no pop.
    foot = dinner[dinner.index('<div class="wk-row-foot">'):]
    assert '<span class="wk-row-meta">30 min · Lighter than the chops</span>' in foot
    assert "data-wk-why" not in html and "wk-why-pop" not in html and "wk-row-why" not in html
    # Swap (same handler as ever) then Tweak, both mini buttons on that line.
    acts = foot[foot.index('<div class="wk-row-acts">'):]
    assert acts.index('data-wk-swap-sheet="dinner"') < acts.index('data-wk-tweak="dinner"')
    assert 'class="wk-mini wk-mini-swap"' in acts and 'class="wk-mini wk-mini-tweak"' in acts
    assert ">Swap</button>" in acts and "Tweak</button>" in acts
    assert 'aria-label="Tweak — Lemon chicken &amp; orzo"' in acts
    # A plate with no parts on record can't change: no Tweak.
    assert 'data-wk-tweak="lunch"' not in html and 'data-wk-swap-sheet="lunch"' in html
    assert 'data-wk-tweak="breakfast"' in html


@_needs_node
def test_tweak_it_is_not_offered_where_the_plate_cant_change():
    past = _day(_MON, past=True, dinner=_plated("Tacos", meta="25 min"))
    reheat = _day(_TUE, dinner=_plated("Leftover orzo", meta="reheat", source="leftovers"))
    out = _run(_plate_prelude() + f"weekState.days = [{json.dumps(past)}, {json.dumps(reheat)}];\n"
               f"console.log(JSON.stringify([wkDayCardHtml({json.dumps(past)}, 0, {{ done: false, swapLabel: 'Swap' }}),"
               f" wkDayCardHtml({json.dumps(reheat)}, 1, {{ done: false, swapLabel: 'Swap' }})]));")
    assert all("data-wk-tweak" not in h for h in out)


@_needs_node
def test_the_approved_roots_rows_keep_done_and_swap_on_the_new_line_and_no_tweak_it():
    day = _day(_TUE, dinner=_plated("Black bean tacos", meta="25 min"))
    html = _run(_plate_prelude() + f"weekState.days = [{json.dumps(day)}];\n"
                f"console.log(JSON.stringify(wkDayCardHtml({json.dumps(day)}, 0, {{ done: true, swapLabel: 'Swap' }})));")
    foot = _row(html, "dinner")
    foot = foot[foot.index('<div class="wk-row-foot">'):]
    assert '<span class="wk-row-meta">25 min</span>' in foot
    assert foot.index('data-wk-done="dinner"') < foot.index('data-wk-swap-sheet="dinner"')
    assert "data-wk-tweak" not in html


@_needs_node
def test_an_open_slot_keeps_its_pick_beside_the_name():
    day = _day(_WED, dinner={"title": "x", "state": "open", "source": "open", "entry_id": 33, "options": []})
    html = _run(_plate_prelude() + f"weekState.days = [{json.dumps(day)}];\n"
                f"console.log(JSON.stringify(wkDayCardHtml({json.dumps(day)}, 0, {{ done: false, swapLabel: 'Swap' }})));")
    dinner = _row(html, "dinner")
    assert "wk-row-foot" not in dinner and "has-foot" not in dinner
    assert dinner.index('<div class="wk-row-acts">') < dinner.index('id="wk-open-dinner"')
    assert ">Pick</button>" in dinner


@_needs_node
def test_a_menu_row_has_the_chevron_the_line_and_tweak_it_only_for_one_cook_ahead():
    days = [
        _day(_MON, breakfast=_plated("Overnight oats", entry_id=11, meta="5 min"),
             dinner=_plated("Salmon and rice", entry_id=13, meta="30 min", asked="Mexican, as asked",
                            reason="Mexican, as you asked")),
        _day(_TUE, breakfast=_plated("Overnight oats", entry_id=21, meta="5 min"),
             dinner=_plated("Made ahead", entry_id=23, meta="reheat",
                            leftover_from={"date": _MON, "meal": "Salmon and rice", "cook_ahead": True})),
        _day(_WED, dinner=_plated("Tofu stir-fry", entry_id=33, meta="20 min")),
    ]
    html = _run(_plate_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify(wkMenuHtml({json.dumps(days)})));")
    assert html.count('class="wk-row wk-menu-row has-foot"') >= 3
    # Oats are cooked on two mornings ahead: Tweak would change one of them.
    oats = _row(html, "breakfast")
    assert 'data-wk-swap-sheet="breakfast"' in oats and "data-wk-tweak" not in oats
    # Salmon is cooked once; Tuesday's reheat doesn't count as a second cook.
    salmon = html[html.index(">Salmon and rice"):]
    salmon = salmon[:salmon.index('data-wk-row="')]
    assert 'data-wk-tweak="dinner"' in salmon
    assert '<svg class="wk-row-chev"' in salmon
    assert ">Mon, Tue · Mexican, as asked</span>" in salmon, "the asked fact, and its echo of a reason left off"
    assert html.count('data-wk-tweak="dinner"') == 2


@_needs_node
def test_a_changed_menu_row_wears_the_pill_in_an_eyebrow_of_its_own():
    days = [_day(_WED, dinner=_plated("Tofu stir-fry", entry_id=33, meta="20 min"))]
    html = _run(_plate_prelude() + "function wasRecentlyChanged(date, slot) { return date === '" + _WED + "' && slot === 'dinner'; }\n"
                + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify(wkMenuHtml({json.dumps(days)})));")
    assert '<span class="wk-row-eyebrow"><span class="wk-changed">Changed</span></span>' in html
    assert html.index('class="wk-changed"') < html.index('data-wk-meal="dinner"')


# ---------------------------------------------------------------------------
# 4. The sheet
# ---------------------------------------------------------------------------

@_needs_node
def test_the_tweak_sheet_is_the_dish_then_its_parts_then_the_quiet_way_out():
    entry = _plated("Lemon chicken & orzo", meta="30 min")
    entry["plate_parts"].append({"role": "side", "word": "Side", "name": "Salsa verde", "source": "side", "missing": False})
    day = _day(_TUE, dinner=entry)
    html = _run(_plate_prelude() + f"console.log(JSON.stringify(tweakSheetBodyHtml({json.dumps(day)}, 'dinner', {json.dumps(entry)})));")
    assert html.startswith('<h2 class="wk-swap-title" id="wk-tweak-title">Tweak</h2>')
    assert '<p class="wk-swap-eyebrow">Dinner · Tuesday</p>' in html
    assert '<p class="wk-tweak-dish">Lemon chicken &amp; orzo</p>' in html
    rows = re.findall(r'<div class="plate-row"><span class="plate-row-role">([^<]+)</span>'
                      r'<span class="plate-row-name">([^<]+)</span><button [^>]*>([^<]+)</button>', html)
    assert rows == [("Protein", "Chicken thighs", "Change"), ("Veg", "Green beans", "Change"), ("Side", "Salsa verde", "Change")]
    # The same data-plate-* hand-off the Meal step's rows give openMealAddSheet.
    assert 'data-plate-part="protein" data-plate-slot="dinner" data-plate-source="dish"' in html
    assert 'data-plate-part="vegetable" data-plate-slot="dinner" data-plate-source="side" data-plate-side="Green beans"' in html
    # The missing carb: the Day card's dashed chip, not a row.
    assert '<div class="plate wk-tweak-adds"><button type="button" class="plate-part is-missing" data-plate-part="carb"' in html
    assert "Add a carb</button>" in html
    assert html.endswith('<button type="button" class="wk-swap-quiet" id="wk-tweak-leave">Leave it as it is</button>')
    assert "Save" not in html, "the sheet commits nothing — the part sheets do"


@_needs_node
def test_an_empty_carb_reads_add():
    entry = _entry("Kofte", plate_parts=[
        {"role": "protein", "word": "Protein", "name": "Lamb", "source": "dish", "missing": False},
        {"role": "carb", "word": "Carb", "name": "None", "source": None, "missing": False, "empty": True}])
    day = _day(_TUE, dinner=entry)
    html = _run(_plate_prelude() + f"console.log(JSON.stringify(tweakSheetBodyHtml({json.dumps(day)}, 'dinner', {json.dumps(entry)})));")
    assert 'aria-label="Add a carb">Add</button>' in html
    assert "wk-tweak-adds" not in html


def test_the_sheets_buttons_open_the_existing_part_sheets_and_the_save_marks_the_row():
    wire = _extract("wireMealsStep", SHELL_JS)
    assert "steps.querySelectorAll('[data-wk-tweak]')" in wire
    assert "openTweakSheet(panel, day, btn.getAttribute('data-wk-tweak'))" in wire
    assert "var day = wkDayForTap(btn);" in wire.split("[data-wk-tweak]")[1][:200]
    opened = _extract("openTweakSheet", SHELL_JS)
    assert "if (!plateCanChange(day, slot, entry)) return;" in opened
    assert opened.index("closeTweakSheet();") < opened.index("openMealAddSheet(panel, day, slot, {")
    assert "markRow: true" in opened
    assert "openSheet(tweakSheetEl, tweakScrimEl);" in opened
    assert "closeSheet(tweakSheetEl, tweakScrimEl);" in _extract("closeTweakSheet", SHELL_JS)
    assert "markRow: !!(part && part.markRow)" in _extract("openMealAddSheet", SHELL_JS)
    for fn in ("runMealChangePart", "runMealAdd", "runMealAddRemove"):
        body = _extract(fn, SHELL_JS)
        assert "mealAddMarkRow(st);" in body, fn
        assert body.index("mealAddMarkRow(st);") < body.index("await loadWeekMenu("), fn
    assert "markRecentlyChanged(st.date, st.slot)" in _extract("mealAddMarkRow", SHELL_JS)
    # Check the week's rows get their re-draw when the eight seconds end, too.
    assert "weekState.step === 'review'" in _extract("markRecentlyChanged", SHELL_JS)


# ---------------------------------------------------------------------------
# CSS and the design system
# ---------------------------------------------------------------------------

def _rule(selector: str) -> str:
    i = SHELL_CSS.index(selector + " {")
    return SHELL_CSS[i:SHELL_CSS.index("}", i)]


def test_the_changed_pill_has_room_and_a_gap():
    assert ".wk-changed { height: auto; padding: 3px 8px; line-height: 1.2; }" in SHELL_CSS
    shared = SHELL_CSS[SHELL_CSS.index(".ask-change-kept,\n.wk-changed {"):]
    shared = shared[:shared.index("}")]
    assert "font-size: 10px" in shared and "font-weight: 800" in shared and "text-transform: uppercase" in shared
    assert "border-radius: var(--radius-badge-sm)" in shared
    assert ".wk-row-eyebrow .wk-changed { margin-top: 0; margin-left: 10px;" in SHELL_CSS


def test_the_row_css_uses_tokens_and_keeps_the_targets():
    chev = _rule(".wk-row-chev")
    assert "color: var(--apricot-label)" in chev
    assert ".wk-row-foot .wk-mini:not(.is-done) { background: var(--sand); border-color: transparent; }" in SHELL_CSS
    assert '.wk-mini::before { content: ""; position: absolute; inset: -4px 0; }' in SHELL_CSS, "36px box, 44px target"
    assert "flex-wrap: wrap" in _rule(".wk-row-foot")
    block = SHELL_CSS[SHELL_CSS.index("/* A planned row (Emily, 2026-09-25"):SHELL_CSS.index(".wk-mini {")]
    assert re.search(r"#[0-9a-fA-F]{3,6}\b", block) is None
    tweak = SHELL_CSS[SHELL_CSS.index('/* "Tweak" (Emily, 2026-09-25'):SHELL_CSS.index(".wk-tweak-adds")]
    assert re.search(r"#[0-9a-fA-F]{3,6}\b", tweak) is None
    assert "#wk-tweak-scrim[hidden], #wk-tweak-sheet[hidden] { display: none; }" in SHELL_CSS


def test_the_design_system_says_what_the_reason_is_now():
    assert "The reason a tap away |" not in DESIGN
    assert "| The reason, said on the line |" in DESIGN
    assert "| Plan row (1A) |" in DESIGN
    assert "Tweak" in DESIGN


def test_undo_takes_the_changed_pill_off_the_row():
    """Found in review: after Undo the row kept saying "Changed" for the rest
    of its eight seconds. Both undo paths now drop the row's mark."""
    import re
    src = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")
    for fn in ("runMealAddUndo", "runSwapUndo"):
        start = src.index("async function " + fn + "(")
        body = src[start:src.index("\n  }\n", start)]
        assert re.search(r"delete recentlyChanged\[", body), fn
