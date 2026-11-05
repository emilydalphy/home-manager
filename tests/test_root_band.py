"""How every tab root opens (Emily, 2026-09-11; Bright Dusk, 2026-11-05).

Plan, Shop, Kitchen and Cook open with ONE component: a plain title and one
line on cream, with the round header buttons at the right (rootBandHtml; the
name is history, it was a spruce band). Sub-screens keep their own way back.

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
    + SHELL_JS[SHELL_JS.index("  function bandDateLabel("):SHELL_JS.index("  function rootBandHtml(")]
    + _function("prefsGearHtml") + "\n  }\n"
    + _function("rootBandHtml") + "\n  }\n"
    + _function("emptyMomentHtml") + "\n  }\n"
    + SHELL_JS[SHELL_JS.index("  var EMPTY_ICONS = {"):SHELL_JS.index("  function emptyMomentHtml(")]
)


# --------------------------------------------------------------------------
# The band itself
# --------------------------------------------------------------------------

def test_the_head_is_one_builder_with_its_parts():
    fn = _function("rootBandHtml")
    assert '<header class="root-band screen-head"' in fn
    for part in ("root-band-eyebrow", "root-band-badge", "root-band-title", "root-band-sub", "root-band-tools"):
        assert part in fn, f"the head is missing its {part}"
    assert "prefsGearHtml()" in fn, "the gear (and the bell's slot) ride in the head"


@_needs_node
def test_the_head_renders_its_parts_and_hides_the_empty_ones():
    out = _node(
        _BAND_JS
        + "console.log(JSON.stringify({"
        + " full: rootBandHtml({ id: 'x', eyebrow: 'Friday, Sep 11', title: 'Now', sub: '3 of 4 done', badge: 'Draft' }),"
        + " bare: rootBandHtml({ id: 'y', title: 'Shop' })"
        + "}));"
    )
    full, bare = out["full"], out["bare"]
    assert full.count('class="root-band screen-head"') == 1
    assert '<h1 class="root-band-title screen-title" id="x-title">Now</h1>' in full
    assert 'id="x-eyebrow" hidden>Friday, Sep 11<' in full
    assert 'id="x-badge">Draft<' in full and 'id="x-badge" hidden' not in full
    assert 'id="x-sub" data-sub="3 of 4 done">Friday, Sep 11 · 3 of 4 done<' in full
    assert 'data-bell-slot' in full and 'class="prefs-gear"' in full
    # Nothing to say is nothing shown — no empty chip, no empty line.
    assert 'id="y-badge" hidden' in bare and 'id="y-sub" data-sub="" hidden' in bare
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
    # "Next week" when the empty state names a period that hasn't started
    # (stale-draft fix, 2026-09-11 — see weekBandData), and since
    # 2026-09-15 a plan on screen is placed against today the same way
    # (periodRelation): "This week" only when it covers today. The
    # behaviour itself is pinned in tests/test_week_set_covers_today.py.
    assert "? (data.period_is_ahead ? 'next' : 'current')" in parts
    assert "periodRelation(data.period_start_date || data.week_start_date, dayCount, today)" in parts
    assert "? (relation === 'next' ? 'Next week' : 'This week')" in parts
    assert "weekBandParts(weekBandData(data), weekState.days || [])" in step
    # The retired in-flow head is gone from the root's two forms.
    assert "weekStepHeadHtml" not in SHELL_JS
    assert "prefsGearRowHtml" not in SHELL_JS


def test_shop_shows_the_band_on_the_root_and_the_crumb_and_head_on_steps():
    render = _function("renderGrocery")
    assert "var onRoot = step === 'list';" in render
    assert "band.hidden = !onRoot;" in render and "head.hidden = onRoot;" in render
    # The band's subtitle ("This week · 14 things, two stores.", Emily's
    # shopping-list mockup 2026-09-18) was removed 2026-09-25 (declutter):
    # the per-store counts on each card already say what's left, so the
    # band now carries no eyebrow or sub-line. groBandEyebrow/groBandLine
    # still exist and are still tested directly
    # (test_shops_band_says_this_week_and_counts_the_list below) — they're
    # just no longer wired into the band.
    assert "setRootBand(panel, 'gro-band', { eyebrow: '', sub: '' });" in render


@_needs_node
def test_shops_band_says_this_week_and_counts_the_list():
    """"This week" over "14 things, two stores." — counted from the data
    (every row on the cards, bought or not; the stores with a card). Under
    'wordmark' the eyebrow's text leads the sub-line
    (tests/test_identity_build.py)."""
    script = (
        "function groPlural(n, one, many){ return n + ' ' + (n === 1 ? one : many); }\n"
        "function groTotals(d){ return { all: d.all }; }\n"
        "function groStoresOnList(d){ return d.stops; }\n"
        "var GRO_ONE_LIST_STOP = 'Your list';\n"
        "function groPillStores(d){ return d.shops || d.stops || []; }\n"
        + _function("groIsStandIn") + "\n  }\n"
        + "var BAND_IDENTITY = 'none';\n"
        + _function("bandDateLabel") + "\n  }\n"
        # The band's constants and the three builders, as one region.
        + SHELL_JS[SHELL_JS.index("  var GRO_BAND_EYEBROW = "):SHELL_JS.index("  // ---------- LIST ----------")]
        + "console.log(JSON.stringify({"
        + " eyebrow: groBandEyebrow({ all: 60, stops: ['A'] }),"
        + " one: groBandLine({ all: 60, stops: ['A'] }),"
        + " two: groBandLine({ all: 14, stops: ['A', 'B'] }),"
        + " oneList: groBandLine({ all: 3, stops: ['Your list'], shops: [] }),"
        + " loose: groBandLine({ all: 2, stops: [] }),"
        + " none: groBandLine({ all: 0, stops: [] }),"
        + " nodata: groBandLine(null)"
        + "}));"
    )
    out = _node(script)
    assert out["eyebrow"] == "This week"
    assert out["one"] == "60 things, one store."
    assert out["two"] == "14 things, two stores."
    assert out["oneList"] == "3 things.", "a household that named no shop is not told it has one"
    assert out["loose"] == "2 things."
    # With nothing to buy the empty moment says so; the band's line is empty.
    assert out["none"] == ""
    assert out["nodata"] == ""


def test_the_band_says_one_thing_once():
    """The band's line and the empty moment must never say the same thing."""
    # Now: no line at all — no "3 of 4 done" score (Emily, 2026-09-24).
    moves = _function("renderTodayMoves")
    assert "sub: ''" in moves and "' done'" not in moves
    # Cook: no line on a day with nothing to cook; the day is the eyebrow.
    sub = _function("kitchenSubtitle")
    assert "return rows.length ? 'nothing left to cook today' : '';" in sub
    # Since Option A (2026-11-01, mockup A-Thu) the empty day is "Night off".
    assert "Nothing to cook today." in _function("cookNightOffHtml")
    # The chips: no "nothing planned" chip over a moment that says so.
    assert "var WEEK_STATE_LABELS = { set: 'Week set', draft: 'Draft' };" in SHELL_JS
    # Only a draft has a chip: an approved week is the plain title (Z-Week).
    assert "var WEEK_BADGES = { draft: 'Draft' };" in SHELL_JS


def test_the_head_css_is_one_section_built_from_tokens():
    start = SHELL_CSS.rindex("/* ====", 0, SHELL_CSS.index("THE ROOT BAND"))
    end = SHELL_CSS.index("/* ---------- Today (Pomona — InnToday) ----------", start)
    section = SHELL_CSS[start:end]
    for rule in (".root-band-badge {", ".root-band-line {", ".empty-moment {",
                 ".empty-moment-icon {", ".empty-moment-line {", ".dock-row {"):
        assert rule in section, f"missing {rule}"
    # Rule 9: every colour goes through a token.
    body = re.sub(r"/\*.*?\*/", "", section, flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", body), "a literal colour in the head CSS"
    # No band: nothing paints the head, and no curve pours over its foot.
    assert ".root-band::after" not in body and "background: var(--spruce);" not in body
    chip = section.split(".root-band-badge {", 1)[1][:500]
    assert "background: var(--blush);" in chip and "color: var(--ink);" in chip
    # Rule 6: the gear and the bell are 44px in the head, round on blush.
    tools = section.split(".root-band-tools .bell-slot .notif-bell {", 1)[1][:300]
    assert "width: 44px;" in tools and "height: 44px;" in tools
    assert "border-radius: var(--radius-pill);" in tools and "background: var(--blush);" in tools


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
    # Cook: "Night off" and the next cook, and no dock on an empty day
    # (Option A, 2026-11-01, mockup A-Thu — it replaced the empty moment on a
    # day with a plan but nothing on it; a household with no plan still gets
    # the moment, below). cookRootDockHtml has nothing for no meal unless the
    # night-before list is open.
    off = _function("cookNightOffHtml")
    assert ">Night off</h2>" in off and "kitchenNextCookLine(meals, todayIso)" in off
    assert "dock-primary" not in off
    dock = _function("cookRootDockHtml")
    assert "if (!row || row.done) {" in dock and ": '';" in dock
    assert "emptyMomentHtml('pot', lastPlanned ? 'Nothing planned this week yet.' : 'Nothing to cook tonight.'," in _function("renderKitchen")


def test_the_moment_sits_in_the_middle_of_the_content_area():
    """Flex centring in a container that fills the panel — not a line at the
    top with air under it."""
    moment = SHELL_CSS.split(".empty-moment {", 1)[1][:400]
    assert "flex: 1 0 auto;" in moment and "justify-content: center;" in moment
    assert ".gro-body.is-empty, .kit-body.is-empty { flex: 1 0 auto; }" in SHELL_CSS
    assert "body.classList.toggle('is-empty', !!body.querySelector('.empty-moment'));" in _function("renderGrocery")
    # Cook carries the moment only with no plan at all (Option A's "Night off"
    # is a card in the list's place, not a moment).
    kit = _function("renderKitchen")
    assert "body.classList.add('is-empty');" in kit and "body.classList.remove('is-empty');" in kit
    for container in (".grocery-content {", ".kitchen-content {"):
        assert "flex: 1 0 auto;" in SHELL_CSS.split(container, 1)[1][:600], f"{container} should fill the panel"


def test_the_prep_days_card_left_the_cook_root():
    assert 'data-cook="prep-days"' not in SHELL_JS
    assert "Tell me which days you prep" not in SHELL_JS


# --------------------------------------------------------------------------
# Docks on the roots
# --------------------------------------------------------------------------

def test_shops_dock_sits_at_the_foot_of_the_panel():
    """Shop's dock element (SORT ALL's finish, "Go to Plan" over an empty
    list — the root itself has none since 2026-09-18) pins to the foot of
    the panel rather than sitting under the last card, exactly as Now's
    does."""
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

def test_design_system_names_the_screen_title():
    assert "| Screen title |" in DESIGN, "section 5's table needs the row"
    assert "rootBandHtml(opts)" in DESIGN
    assert "No band" in DESIGN
