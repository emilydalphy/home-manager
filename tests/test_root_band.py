"""The root band — how every tab root opens (Emily, 2026-09-11, Option B).

Now, Plan, Shop and Cook used to lay their date, title, badge and gear out
four different ways. They open with ONE component now: a full-bleed spruce
band (eyebrow, title, at most one chip and one line, the gear and the
bell's slot top-right), with the ivory content pouring over its foot with
the hero's radius. Sub-screens keep their crumb and title. The band never
carries a button; the dock is where the one thing to press lives.

Alongside it: the empty states as designed moments (one shared component,
the next step in the dock), and the dock rows on the roots.

Checkable claims, so they are checked here rather than trusted — by reading
the markup each root builds, and by running the pure builders under node
where one exists.
"""

import json
import re
import shutil
import nodeharness
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
DESIGN = (REPO / "DESIGN_SYSTEM.md").read_text(encoding="utf-8")


def _function(name: str) -> str:
    """The body of one top-level function in shell.js, to its closing brace."""
    marker = "  async function %s(" % name
    if marker not in SHELL_JS:
        marker = "  function %s(" % name
    start = SHELL_JS.index(marker)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end]


_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own builders"
)


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


# The builders the band needs, plus stubs for what they lean on.
_BAND_JS = (
    "function escapeHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
    "var PREFS_GEAR_ICON = '<svg></svg>';\n"
    + _function("prefsGearHtml") + "\n  }\n"
    + _function("rootBandHtml") + "\n  }\n"
    + _function("emptyMomentHtml") + "\n  }\n"
    + SHELL_JS[SHELL_JS.index("  var EMPTY_ICONS = {"):SHELL_JS.index("  function emptyMomentHtml(")]
)


# --------------------------------------------------------------------------
# The band itself
# --------------------------------------------------------------------------

def test_the_band_is_one_builder_with_the_five_parts():
    fn = _function("rootBandHtml")
    assert '<header class="root-band"' in fn
    for part in ("root-band-eyebrow", "root-band-badge", "root-band-title", "root-band-sub", "root-band-tools"):
        assert part in fn, f"the band is missing its {part}"
    assert "prefsGearHtml()" in fn, "the gear (and the bell's slot) ride in the band"
    assert "<button" not in fn, "the band never carries a button of its own"


@_needs_node
def test_the_band_renders_its_parts_and_hides_the_empty_ones():
    out = _node(
        _BAND_JS
        + "console.log(JSON.stringify({"
        + " full: rootBandHtml({ id: 'x', eyebrow: 'Friday, Sep 11', title: 'Now', sub: '3 of 4 done', badge: 'Draft' }),"
        + " bare: rootBandHtml({ id: 'y', title: 'Shop' })"
        + "}));"
    )
    full, bare = out["full"], out["bare"]
    assert full.count('class="root-band"') == 1
    assert '<h1 class="root-band-title" id="x-title">Now</h1>' in full
    assert 'id="x-eyebrow">Friday, Sep 11<' in full
    assert 'id="x-badge">Draft<' in full and 'id="x-badge" hidden' not in full
    assert 'id="x-sub">3 of 4 done<' in full
    assert 'data-bell-slot' in full and 'class="prefs-gear"' in full
    # Nothing to say is nothing shown — no empty chip, no empty line.
    assert 'id="y-badge" hidden' in bare and 'id="y-sub" hidden' in bare
    assert "<button" not in full.replace('<button type="button" class="prefs-gear"', "")


@pytest.mark.parametrize("root, builder, band_id", [
    ("Now", "buildTodayPanel", "today-band"),
    ("Shop", "buildGroceryPanel", "gro-band"),
    ("Cook", "buildKitchenPanel", "kit-band"),
])
def test_each_root_builds_the_band_once(root, builder, band_id):
    build = _function(builder)
    assert build.count("rootBandHtml({") == 1, f"{root} should build exactly one band"
    assert f"id: '{band_id}'" in build
    # And the retired headers are not built beside it.
    for dead in ("today-heading", "today-datestrip", "kit-titlerow", "kit-title", "kit-sub", "gro-sortbadge"):
        assert f'class="{dead}"' not in build, f"{root} still builds the old {dead}"


def test_plan_fills_its_band_on_the_root_and_hides_it_on_every_step():
    """Plan's band is data-driven (the week's dates, its state), so it is
    filled by renderMealsStep into a slot above #week-steps — once, and only
    on the root."""
    assert 'id="week-band-slot" hidden' in _function("buildWeekPanel")
    step = _function("renderMealsStep")
    assert "bandSlot.hidden = !onRoot;" in step
    assert "bandSlot.innerHTML = rootBandHtml(parts);" in step
    assert step.count("rootBandHtml(") == 1
    parts = _function("weekBandParts")
    assert "id: 'week-band'" in parts
    # "Next week" only when the empty state names a period that hasn't
    # started (stale-draft fix, 2026-09-11) — see weekBandData.
    assert "title = isWeek || !range ? (data.period_is_ahead ? 'Next week' : 'This week') : range;" in parts
    assert "weekBandParts(weekBandData(data), weekState.days || [])" in step
    # The retired in-flow head is gone from the root's two forms.
    assert "weekStepHeadHtml" not in SHELL_JS
    assert "prefsGearRowHtml" not in SHELL_JS


def test_shop_shows_the_band_on_the_root_and_the_crumb_and_head_on_steps():
    render = _function("renderGrocery")
    assert "var onRoot = step === 'list';" in render
    assert "band.hidden = !onRoot;" in render and "head.hidden = onRoot;" in render
    assert "setRootBand(panel, 'gro-band', { eyebrow: groBandEyebrow(data), sub: '' });" in render


@_needs_node
def test_shops_eyebrow_counts_the_list_and_falls_back_to_the_date():
    script = (
        "function groPlural(n, one, many){ return n + ' ' + (n === 1 ? one : many); }\n"
        "function groTotals(d){ return { needed: d.needed }; }\n"
        "function groStoresWithNeeded(d){ return d.stops; }\n"
        + _function("groBandEyebrow") + "\n  }\n"
        + "console.log(JSON.stringify({"
        + " many: groBandEyebrow({ needed: 60, stops: ['A'] }),"
        + " two: groBandEyebrow({ needed: 2, stops: ['A', 'B'] }),"
        + " none: groBandEyebrow({ needed: 0, stops: [] }),"
        + " nodata: groBandEyebrow(null)"
        + "}));"
    )
    out = _node(script)
    assert out["many"] == "60 things · 1 stop"
    assert out["two"] == "2 things · 2 stops"
    # With nothing to buy the empty moment says so; the eyebrow says the day.
    assert re.match(r"^[A-Z][a-z]+day, [A-Z][a-z]{2} \d{1,2}$", out["none"]), out["none"]
    assert out["nodata"] == out["none"]


def test_the_band_says_one_thing_once():
    """The band's line and the empty moment must never say the same thing."""
    # Now: no line on a day with no moves.
    moves = _function("renderTodayMoves")
    assert "sub: moves.length ? (data.done || 0) + ' of ' + moves.length + ' done' : ''" in moves
    # Cook: no line on a day with nothing to cook; the day is the eyebrow.
    sub = _function("kitchenSubtitle")
    assert "return rows.length ? 'nothing left to cook today' : '';" in sub
    assert "Nothing to cook tonight." in _function("kitchenCookingTodayHtml")
    # The chips: no "nothing planned" chip over a moment that says so.
    assert "var WEEK_STATE_LABELS = { set: 'Week set', draft: 'Draft' };" in SHELL_JS
    assert "var WEEK_BADGES = { set: 'Approved', draft: 'Draft' };" in SHELL_JS


def test_the_band_css_is_one_section_built_from_tokens():
    start = SHELL_CSS.rindex("/* ====", 0, SHELL_CSS.index("THE ROOT BAND"))
    end = SHELL_CSS.index("/* ---------- Today (Pomona — InnToday) ----------", start)
    section = SHELL_CSS[start:end]
    for rule in (".root-band {", ".root-band::after {", ".root-band-eyebrow {", ".root-band-badge {",
                 ".root-band-title {", ".root-band-sub {", ".root-band-tools {", ".empty-moment {",
                 ".empty-moment-icon {", ".empty-moment-line {", ".dock-row {"):
        assert rule in section, f"missing {rule}"
    # Rule 9: every colour goes through a token — no literal hex outside the
    # measured-contrast comment.
    body = re.sub(r"/\*.*?\*/", "", section, flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", body), "a literal colour in the band CSS"
    assert "background: var(--spruce);" in section
    assert "color: var(--apricot-light);" in section.split(".root-band-eyebrow {", 1)[1][:400]
    assert "border-radius: var(--radius-hero) var(--radius-hero) 0 0;" in section
    assert "border-top: 1.5px solid var(--spruce-edge);" in section
    # The chip: raised spruce and the muted ivory, never an accent fill.
    chip = section.split(".root-band-badge {", 1)[1][:500]
    assert "background: var(--spruce-raised);" in chip and "color: var(--ivory-ink-muted);" in chip
    # Rule 6: the gear and the bell are 44px in the band.
    tools = section.split(".root-band-tools .bell-slot .notif-bell {", 1)[1][:300]
    assert "width: 44px;" in tools and "height: 44px;" in tools
    # Rule 8: the ratios were measured and written down.
    assert ":1" in section and "Contrast, measured" in section


def test_the_old_header_rule_sets_are_retired():
    for dead in (".today-heading", ".today-datestrip", ".today-date", ".today-hairline", ".today-greeting",
                 ".today-progress", ".today-weekstate", ".kit-titlerow", ".kit-eyebrow", ".kit-hairline",
                 ".kit-title", ".kit-sub", ".gro-sortbadge", ".prefs-gear-row", ".wk-state.is-none"):
        assert not re.search(r"(^|[\s,}])%s\s*[,{:\[]" % re.escape(dead), SHELL_CSS, re.M), (
            f"{dead} still has a CSS rule — the root band replaced it"
        )


# --------------------------------------------------------------------------
# Empty states as designed moments
# --------------------------------------------------------------------------

@_needs_node
def test_the_empty_moment_is_an_icon_a_sentence_and_no_button():
    out = _node(
        _BAND_JS
        + "console.log(JSON.stringify({"
        + " one: emptyMomentHtml('bag', 'Nothing to buy. Approve a week and I\\u2019ll build the list.'),"
        + " two: emptyMomentHtml('pot', 'Nothing to cook tonight.', 'Next: Saturday, pancakes.')"
        + "}));"
    )
    one, two = out["one"], out["two"]
    assert one.startswith('<div class="empty-moment">')
    assert 'stroke="currentColor" stroke-width="2.2" stroke-linecap="round"' in one
    assert "empty-moment-line" in one and "empty-moment-detail" not in one
    assert "<button" not in one and "<button" not in two
    assert '<p class="empty-moment-detail">Next: Saturday, pancakes.</p>' in two


def test_now_shop_and_cook_each_have_their_moment_and_its_next_step():
    # Now: the sentence, with the plan and tonight in the dock.
    today = _function("renderTodayEmpty")
    assert "Quiet day. Want me to sort dinner, or the whole week?" in today
    dock = _function("renderTodayDock")
    assert 'id="plan-nudge-go">Let’s plan the week</button>' in dock
    assert 'class="dock-link" id="today-just-tonight">Just tonight</button>' in dock
    assert 'class="dock-link" id="plan-nudge-dismiss">Not now</button>' in dock
    # "Just tonight" is the old card's "Pick": it unfolds tonight's card.
    assert "card.classList.remove('is-folded');" in dock
    assert "openAskSheet('For tonight’s dinner, I’d like ');" in dock
    # The offer card and tonight's card step aside for the moment.
    assert "nudgeWrap.hidden = hideFurniture;" in today
    assert "tonightCard.hidden = hideFurniture && !panel._justTonight;" in today
    # Shop: the sentence, with Plan in the dock.
    assert "emptyMomentHtml('bag', 'Nothing to buy. Approve a week and I’ll build the list.')" in _function("groListHtml")
    gro_dock = SHELL_JS[SHELL_JS.index("function groDockHtml("):SHELL_JS.index('// ---------- "Maybe already home" ----------')]
    assert '<button type="button" class="dock-primary" data-gro="goto-plan">Go to Plan</button>' in gro_dock
    assert "case 'goto-plan':\n        activateTab('week', true);" in SHELL_JS
    # Cook: the sentence and the next cook; no dock (nav rule: Cook's root has none).
    assert "emptyMomentHtml('pot', 'Nothing to cook tonight.', kitchenNextCookLine(meals, todayIso))" in _function("kitchenCookingTodayHtml")
    kitchen = re.sub(r"//[^\n]*", "", _function("renderKitchen"))
    assert "dock" not in kitchen and "dock-primary" not in _function("kitchenCookingTodayHtml")


def test_the_moment_sits_in_the_middle_of_the_content_area():
    """Flex centring in a container that fills the panel — not a line at the
    top with air under it."""
    moment = SHELL_CSS.split(".empty-moment {", 1)[1][:400]
    assert "flex: 1 0 auto;" in moment and "justify-content: center;" in moment
    assert ".gro-body.is-empty, .kit-body.is-empty { flex: 1 0 auto; }" in SHELL_CSS
    assert "body.classList.toggle('is-empty', !!body.querySelector('.empty-moment'));" in _function("renderGrocery")
    assert "body.classList.toggle('is-empty', !rows.length);" in _function("renderKitchen")
    for container in (".grocery-content {", ".kitchen-content {"):
        assert "flex: 1 0 auto;" in SHELL_CSS.split(container, 1)[1][:600], f"{container} should fill the panel"


def test_the_prep_days_card_left_the_cook_root():
    assert 'data-cook="prep-days"' not in SHELL_JS
    assert "if (!sessions.length) return '';" in _function("cookPrepSessionsHtml")


# --------------------------------------------------------------------------
# Docks on the roots
# --------------------------------------------------------------------------

def test_shops_action_sits_at_the_foot_of_the_panel():
    """"Start the trip" was already in the dock element, but on a short list
    the element sat straight under the last card — it read as a button in
    the page. The container fills the panel now and the dock pins to its
    foot, exactly as Now's does."""
    assert ".grocery-content .gro-dock { margin-top: auto; }" in SHELL_CSS
    assert ".today-content .today-dock { margin-top: auto; }" in SHELL_CSS


def test_nows_plan_offer_is_one_row_with_not_now_beside_it():
    dock = _function("renderTodayDock")
    row = dock.split("} else if (nudge && nudge.show && !panel._nudgeDismissed) {", 1)[1]
    row = row[:row.index("}")]
    assert '<div class="dock-row">' in row
    assert row.index("dock-primary") < row.index("dock-link"), "the apricot is on the left, the quiet link beside it"
    assert ".dock-row .dock-primary { flex: 1 1 auto; width: auto; min-width: 0; }" in SHELL_CSS


# --------------------------------------------------------------------------
# The doc moved with the code (Tier 2)
# --------------------------------------------------------------------------

def test_design_system_names_the_root_band():
    assert "| Root band |" in DESIGN, "§5's component table needs a Root band row"
    assert "root-band" in DESIGN
    assert "Every tab root opens with the root band" in DESIGN, "§6 needs its one sentence"
