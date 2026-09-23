"""
The Shop screen's non-toast copy, swept 2026-09-23.

Emily, 2026-09-22: *"Can we make sure that copy throughout is more straight
forward like this. I don't like the AI written style."*
`COPY_SWEEP_2026-09-23.md` turned that into a findings list; this file pins
the eleven it gave the Shop screen's own lines (4, 5, 6, 7, 8, 9, 10, 11,
12, 13 and 15) so they can't grow back on the next screen anyone builds.

The rules being applied are rule 2 and rule 3 of
`.claude/skills/pomona-copywriter/SKILL.md`: a button says the action, a
toast says what happened and names the thing, and nothing announces what
the app was going to do anyway — including a helper line that restates the
controls right under it.

Three of these cut a line rather than reword it, so each of those also
asserts what the reader keeps: the stores question still stands on its own,
the spices line still says the section holds EVERY spice the week needs
(not only the missing ones), and the rolled-up card still tells a screen
reader it opens.
"""
from __future__ import annotations

from pathlib import Path

from shop_harness import needs_node, run

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. "Where do you usually shop?" — the question stands on its own
# ---------------------------------------------------------------------------

@needs_node
def test_the_stores_question_asks_and_does_not_narrate_the_sorting():
    out = run("""
groceryState.usualStores = ['Costco'];
console.log(JSON.stringify({ html: groStoresPromptHtml() }));
""")
    html = out["html"]
    assert "Where do you usually shop?" in html, "the question itself stays"
    assert "sort the list by store" not in html, "announcing what I'd do with the answer"
    assert "gro-stores-prompt-sub" not in html
    assert 'data-store="Costco" aria-pressed="true"' in html, "the chips still carry the answer"
    assert "1 shop picked" in html, "and the count still says it out loud"


def test_the_dead_sub_rule_went_with_the_line():
    assert ".gro-stores-prompt-sub {" not in SHELL_CSS


# ---------------------------------------------------------------------------
# 5. A paused staple: what happened, not where the control lives
# ---------------------------------------------------------------------------

def test_the_paused_staple_toast_stops_after_what_happened():
    assert "' was paused — three trips skipped'" in SHELL_JS
    assert "under Staples if you want it back" not in SHELL_JS, (
        "a sentence about where a control lives — the Undo chip is the way back"
    )
    # And the way back is real: the Staples section draws every paused
    # staple with a Resume button on it.
    row = SHELL_JS[SHELL_JS.index("function groStapleRowHtml("):]
    row = row[:row.index("\n  }\n")]
    assert "st.paused" in row and "Resume" in row


# ---------------------------------------------------------------------------
# 6. The scanned photo's review line
# ---------------------------------------------------------------------------

def test_the_scan_review_line_is_the_one_thing_to_do():
    assert 'Untick anything that&rsquo;s wrong.' in SHELL_JS
    assert "untick anything I got wrong" not in SHELL_JS
    assert "Here&rsquo;s what I read" not in SHELL_JS, "narrating I, over boxes that show it"


# ---------------------------------------------------------------------------
# 7. Spices: the line says what the list IS, not what the boxes do
# ---------------------------------------------------------------------------

def test_the_spices_line_keeps_the_fact_and_drops_the_instruction():
    assert "All the spices this week&rsquo;s recipes need." in SHELL_JS, (
        "the fact a person can't see: the section holds every spice the week "
        "needs, not only the ones they're short of"
    )
    assert "Tick the ones you need to buy" not in SHELL_JS


# ---------------------------------------------------------------------------
# 8. The rolled-up done card: the count alone
# ---------------------------------------------------------------------------

def test_the_rolled_up_card_says_the_count_and_still_opens_for_a_screen_reader():
    card = SHELL_JS[SHELL_JS.index("function groRolledCardHtml("):]
    card = card[:card.index("\n  }\n")]
    assert "tap to see them" not in card, "the whole card is a button with a chevron on it"
    assert "'<span class=\"gro-rolled-sub\">' + count + '</span>'" in card
    assert "(open ? ', hide them' : ', see them')" in card, "the affordance stays in the label"


# ---------------------------------------------------------------------------
# 9 and 11. Two buttons that were phrases
# ---------------------------------------------------------------------------

def test_the_finished_moment_offers_a_verb_not_a_phrase():
    done = SHELL_JS[SHELL_JS.index("function groShopDoneHtml("):]
    done = done[:done.index("\n  }\n")]
    assert 'data-gro="shop-done-later">Not now</button>' in done
    assert "come back to it" not in done
    assert "Show me tonight" in done, "the pair it sits beside is unchanged"


def test_the_way_back_off_already_had_on_hand_is_the_action():
    foot = SHELL_JS[SHELL_JS.index("function groNotNeededHtml("):]
    foot = foot[:foot.index("\n  }\n")]
    assert ">Put back on the list</button>" in foot
    assert "Actually, I need it" not in foot


# ---------------------------------------------------------------------------
# 10 and 12. Two toasts
# ---------------------------------------------------------------------------

def test_keeping_everything_says_what_is_true_once():
    assert "showToast('Everything stays on the list.');" in SHELL_JS
    assert "Kept all" not in SHELL_JS, "the second half restated the first"
    assert "nothing dropped" not in SHELL_JS, "and 'dropped' is the euphemism she corrected"


def test_the_approve_hand_off_names_what_was_approved():
    assert SHELL_JS.count("showToast('Your week was approved')") == 2, (
        "both ways in from the Week 1 approval: straight to the list, and "
        "via the freezer step"
    )
    assert "Approved. Here’s your list." not in SHELL_JS, "it restated the screen it landed on"


# ---------------------------------------------------------------------------
# 13. The carry screen's subtitle matches its own buttons
# ---------------------------------------------------------------------------

@needs_node
def test_the_carry_subtitle_asks_what_its_buttons_answer():
    out = run("""
groceryState.carried = [{ id: 1, item: 'Rice' }, { id: 2, item: 'Oats' }];
console.log(JSON.stringify({ head: groHeadFor(groceryState.data, 'carry'),
  body: groCarryHtml(groceryState.data) }));
""")
    assert out["head"]["sub"] == "2 things · still need them?"
    assert "keep or drop" not in out["head"]["sub"]
    assert "Keep</button>" in out["body"] and "Don&rsquo;t need</button>" in out["body"], (
        "the subtitle now asks the question those two buttons answer"
    )


# ---------------------------------------------------------------------------
# 15. Plan's ready-made card, which reads like Shop's pills
# ---------------------------------------------------------------------------

def test_the_ready_made_card_offers_pick_another():
    assert 'class="ready-made-other" data-date="\' + day.date + \'">Pick another</button>' in SHELL_JS
    assert "Choose differently" not in SHELL_JS


# ---------------------------------------------------------------------------
# The sweep's own boundaries: what this branch deliberately left alone
# ---------------------------------------------------------------------------

def test_the_lines_the_sweep_kept_are_still_there():
    """`COPY_SWEEP_2026-09-23.md` "Considered and kept": lines that look like
    violations and aren't, because they carry the one thing the person can't
    see anywhere else."""
    assert "Freezing it? I’ll remind you ' + escapeHtml(f.moveLabel)" in SHELL_JS
    assert "' off the list — I\\u2019ll ask again in '" in SHELL_JS
    assert "is a staple now — I\\u2019ll put it on the list before you run out" in SHELL_JS


def test_the_two_findings_that_are_emilys_call_were_not_touched():
    """Findings 14 and 30 are flagged, not recommended — the sweep says the
    reasoning behind both is sound and the decision is hers."""
    assert "var done = count ? 'That&rsquo;s where we shop' : 'One list is fine';" in SHELL_JS
    assert "'Started ' + offset + ' — the clock’s moved with you.'" in SHELL_JS


def test_chores_was_left_exactly_as_it_was():
    """Paused by Emily, 2026-09-18 — same habits, no edits."""
    start = SHELL_JS.index("function renderChores(")
    chores = SHELL_JS[start:SHELL_JS.index("// ---------- Cook's root:", start)]
    assert "Put back." in chores, "untouched while Chores is paused"
