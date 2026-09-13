"""Prep questions are a clear step, not a footnote (Emily, 2026-09-13).

On her phone: "make the prep questions more obvious, we don't want it to be
a subtle piece to skip — it's something they should be able to give a
quick response to", and "the dark font on the 'all set' screen is hard to
read on the dark background."

So on the All set screen the freezer check and the cook-ahead offer sit
above the counters, open from the start (one tap to say "None — all
fresh", two to name a thing and add it), under a headline rather than an
eyebrow, and each collapses to its one-line confirmation once answered.
The card's helper text takes the on-spruce ink tokens and is measured,
not eyeballed (DESIGN_SYSTEM Rule 8). The root's receipt card keeps its
fold — there the week is the point and the asks are its footnote.
"""
import re
from pathlib import Path

from test_contrast import contrast

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
THEME = (REPO / "static" / "theme.css").read_text(encoding="utf-8")

AA_NORMAL = 4.5
AA_UI = 3.0


def _fn(name):
    marker = "  async function %s(" % name if ("  async function %s(" % name) in SHELL_JS else "  function %s(" % name
    start = SHELL_JS.index(marker)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end]


def _tokens(block: str) -> dict:
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^\s*(--[\w-]+):\s*([^;]+);", block, re.MULTILINE)}


def _light_and_dark():
    """theme.css's :root values, and the same names as the dark block
    overrides them — a token the dark block leaves alone keeps its light
    value there, exactly as the cascade does."""
    # The header comment names the block too; the block itself starts a line.
    dark_at = THEME.index("\n@media (prefers-color-scheme: dark)")
    light = _tokens(THEME[:dark_at])
    dark = dict(light)
    dark.update(_tokens(THEME[dark_at:]))
    return light, dark


# ---------- placement and shape ----------

def test_the_questions_sit_above_the_counters():
    allset = _fn("allSetStepHtml")
    asks = allset.index('<div id="wk-allset-asks"></div>')
    nums = allset.index('<div class="wk-allset-nums">')
    assert asks < nums, "the asks row must come before the MEALS / COOKS / TO BUY tiles"
    # And the counters themselves are untouched (a separate card owns their wording).
    assert "label: 'meals'" in allset and "label: 'cooks'" in allset and "label: 'to buy'" in allset


def test_the_heading_lost_its_apology():
    assert "Two quick ones before you go" in SHELL_JS
    assert "One quick one before you go" in SHELL_JS
    assert "'One quick one, if you like'" not in SHELL_JS
    assert "'Two quick ones, if you like'" not in SHELL_JS
    # On spruce it is a card headline, not a 10px eyebrow.
    rule = SHELL_CSS[SHELL_CSS.index(".wk-allset .wk-quick-card.on-spruce .wk-quick-title {"):]
    rule = rule[:rule.index("}")]
    assert "font-family: var(--font-display)" in rule and "font-size: 21px" in rule
    assert "text-transform: none" in rule


def test_on_all_set_each_ask_is_open_and_is_not_a_toggle():
    receipt = _fn("renderWeekReceipt")
    assert "defrostAskCardHtml(asksOnly || weekQuickOpen.defrost)" in receipt
    assert "cookAheadAskCardHtml(asksOnly || weekQuickOpen.cookAhead)" in receipt
    assert "fixed: asksOnly" in receipt
    line = _fn("weekQuickLineHtml")
    # A fixed line is a heading with the body under it — no "Ask", no button.
    fixed = line[line.index("if (line.fixed) {"):line.index("return '<div class=\"wk-quick-line\">' +\n      '<button")]
    assert '<div class="wk-quick-head is-fixed">' in fixed
    assert "data-quick" not in fixed and "wk-quick-ask" not in fixed and "wk-quick-sub" not in fixed
    # The root's receipt still folds: the toggle path is intact.
    assert 'data-quick="\' + line.key' in line
    assert "(open ? 'Close' : 'Ask')" in line
    # The bodies take their openness from the caller, not from weekQuickOpen alone.
    assert "function defrostAskCardHtml(open)" in SHELL_JS
    assert "function cookAheadAskCardHtml(open)" in SHELL_JS
    assert "(open ? '' : ' hidden')" in _fn("defrostAskCardHtml")
    assert "(open ? '' : ' hidden')" in _fn("cookAheadAskCardHtml")


# ---------- answered questions collapse to one line ----------

def test_an_answered_ask_collapses_to_its_confirmation_in_place():
    assert "var weekQuickDone = { planId: null, defrost: '', cookAhead: '' };" in SHELL_JS
    defrost = _fn("submitDefrostAsk")
    assert "setWeekQuickDone(data.weekly_plan_id, 'defrost', defrostDoneLine(items, body.created || [], body.notes || []));" in defrost
    cook = _fn("submitCookAheadAsk")
    assert "setWeekQuickDone(data.weekly_plan_id, 'cookAhead'," in cook
    # Read back from the items BEFORE the state is cleared, or there is nothing to name.
    assert cook.index("setWeekQuickDone(") < cook.index("cookAheadAskState.items = null;")
    receipt = _fn("renderWeekReceipt")
    # Each answered ask takes the place its question had, so the first
    # answer does not shuffle the second question under a thumb.
    assert "} else if (answered.defrost) {\n      lines.push({ done: answered.defrost });" in receipt
    assert "} else if (answered.cookAhead) {\n      lines.push({ done: answered.cookAhead });" in receipt
    assert "line.done ? weekQuickDoneHtml(line.done) : weekQuickLineHtml(line)" in receipt
    # Keyed to the plan, so a re-plan starts clean.
    assert "weekQuickDone.planId === data.weekly_plan_id" in receipt


def test_the_confirmation_lines_say_the_thing():
    defrost = _fn("defrostDoneLine")
    assert "'Nothing in the freezer — all fresh.'" in defrost
    assert "' night'" in defrost and "'move to the fridge '" in defrost
    assert "dayName(c.task_date, { weekday: 'long' })" in defrost
    # One clause per item, its nights joined — never the item named twice.
    assert "nights[item].join(' and ')" in defrost
    # A re-ask from Cook is the question again, not its old answer.
    for fn, key in (("openDefrostAskFromCook", "defrost"), ("openCookAheadAskFromCook", "cookAhead")):
        assert f"setWeekQuickDone(weekState.data.weekly_plan_id, '{key}', '')" in _fn(fn)
    cook = _fn("cookAheadDoneLine")
    assert "'Cooking each on its own.'" in cook
    assert "': one batch '" in cook and "' covers '" in cook
    done = _fn("weekQuickDoneHtml")
    assert "TICK_ICON" in done and 'class="wk-quick-line wk-quick-done"' in done


# ---------- the bug card: dark text on the dark background ----------

def test_the_cook_ahead_sentence_is_not_said_twice():
    """One repeated dish: the line above the body already reads "Roasted
    Chickpeas on 2 nights. Cook ahead?", so the block does not repeat it."""
    assert "function cookAheadAskBlockHtml(item, named)" in SHELL_JS
    block = _fn("cookAheadAskBlockHtml")
    assert "(named\n        ? '<div class=\"ca-ask-line\">'" in block
    card = _fn("cookAheadAskCardHtml")
    assert "cookAheadAskBlockHtml(item, items.length > 1)" in card


def _rule(selector: str) -> str:
    start = SHELL_CSS.index(selector + " {") if (selector + " {") in SHELL_CSS else SHELL_CSS.index(selector + ",")
    return SHELL_CSS[start:SHELL_CSS.index("}", start)]


def test_the_helper_text_on_spruce_uses_the_on_spruce_tokens():
    on = ".wk-allset .wk-quick-card.on-spruce"
    helper = _rule(f"{on} .wk-quick-body-line")
    assert "color: var(--ivory-ink-muted)" in helper
    assert f"{on} .ca-ask-count" in helper  # the "Makes 2 nights · for 4" line shares it
    assert "color: var(--ivory-ink)" in _rule(f"{on} .ca-ask-line")
    sand = _rule(f"{on} .ny-actions .btn-sand")
    assert "color: var(--ivory-ink)" in sand and "border-color: var(--apricot-rule)" in sand
    ticked = _rule(f"{on} .defrost-chip.is-selected")
    assert "background: var(--celadon)" in ticked and "color: var(--on-accent-ink)" in ticked
    # The ivory receipt card's own inks are untouched — this is scoped to spruce.
    assert "color: var(--ink-secondary)" in _rule(".wk-quick-body-line")
    assert "color: var(--ink)" in _rule(".ca-ask-line")


def test_every_text_colour_on_the_spruce_card_clears_aa_in_both_modes():
    """Measured against --spruce-raised, the card's fill, the way theme.css
    records its own dark-mode ratios (DESIGN_SYSTEM Rule 8)."""
    light, dark = _light_and_dark()
    text = ["--ivory-ink", "--ivory-ink-muted"]
    for mode, tokens in (("light", light), ("dark", dark)):
        card = tokens["--spruce-raised"]
        for name in text:
            ratio = contrast(tokens[name], card)
            assert ratio >= AA_NORMAL, f"{name} on --spruce-raised is {ratio:.2f}:1 in {mode}"
        # A ticked chip: dark ink on the celadon fill (Rule 1), and the fill
        # itself readable as a control against the card.
        assert contrast(tokens["--on-accent-ink"], tokens["--celadon"]) >= AA_NORMAL
        assert contrast(tokens["--celadon"], card) >= AA_UI, f"celadon on the card is under 3:1 in {mode}"
        # And the thing Emily saw: the ivory card's helper ink, on spruce, fails.
        assert contrast(tokens["--ink-secondary"], card) < AA_NORMAL, (
            f"--ink-secondary on --spruce-raised passes in {mode} now — the on-spruce override may be redundant"
        )
