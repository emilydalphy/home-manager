"""
Week 1 — the day-card carousel (Loop Board "Week 1: day-card carousel
replaces 'sample week' and the 15-meals stat", Emily 2026-09-18, mockup
09-week1).

The reveal used to end on "Here's your sample week." over a big "15 meals"
number, a celadon receipt and a list of days, with "Review my week" under
it. It is one card per day now, side by side in the gutter, a Swap under
every meal, the day tabs and dots over and under it, and "Approve · Open
grocery list" as the one apricot — approving IS the yes, through the app's
own approve route, then the app opens on the list.

Three kinds of test, the usual split for this repo:

  * NODE, running the page's own renderer (revealDayCardHtml and the slot
    normalisers) lifted out of static/onboarding.html — the markup a
    browser would be handed, asserted as behaviour.
  * BEHAVIOUR, against the real routes the screen calls: /swap-options and
    /swap-choose (tools.swap_options with the model call injected).
  * SOURCE, for the copy and the wiring that has no pure function to run —
    the word "sample" gone from the screen, the hand-off URL, the `?`.
"""
from __future__ import annotations

import datetime
import importlib
import json
import re
import shutil
from pathlib import Path

import nodeharness
import pytest

from conftest import household_today

from app import tools
from app.db import get_conn
# The module, not the function tools re-exports under the same name.
sop = importlib.import_module("app.tools.swap_options")


STATIC = Path(__file__).resolve().parent.parent / "static"
ONBOARDING = (STATIC / "onboarding.html").read_text()
SHELL_JS = (STATIC / "shell.js").read_text()

# The same source with HTML comments and full-line JS comments removed —
# "the word is gone" has to mean gone from the SCREEN, not from the file,
# since a comment naming the copy it replaced is what this project wants
# people to keep writing.
ONBOARDING_VISIBLE = re.sub(r"<!--.*?-->", "", ONBOARDING, flags=re.S)
ONBOARDING_VISIBLE = re.sub(r"^\s*//.*$", "", ONBOARDING_VISIBLE, flags=re.M)
ONBOARDING_VISIBLE = re.sub(r"/\*.*?\*/", "", ONBOARDING_VISIBLE, flags=re.S)

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the page's own renderer"
)


def _balanced(source: str, start: int) -> str:
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start : j + 1]


def _fn(name: str) -> str:
    return _balanced(ONBOARDING, ONBOARDING.index(f"function {name}("))


def _const(name: str) -> str:
    start = ONBOARDING.index(f"const {name} = ")
    rest = ONBOARDING[start:]
    return rest[: rest.index("\n")]


def _step_markup(step_id: str) -> str:
    start = ONBOARDING.index(f'<div id="{step_id}"')
    nxt = ONBOARDING.find('<div id="step-', start + 1)
    end = ONBOARDING.index("<script>", start) if nxt == -1 else nxt
    return ONBOARDING[start:end]


def _renderer_harness() -> str:
    return "\n".join([
        _fn("escapeHtmlLocal"),
        _const("REVEAL_WEEK_SLOTS"),
        _const("REVEAL_SLOT_LABELS"),
        _const("REVEAL_DAY_SHORT"),
        _const("REVEAL_DAY_LONG"),
        _const("REVEAL_SWAP_ICON"),
        _fn("revealWeekdayIndex"),
        _fn("formatPlanDate"),
        _fn("revealMinutesMeta"),
        _fn("revealSlotFromStream"),
        _fn("revealSlotFromMenu"),
        _fn("revealSlotFromPlanMeal"),
        _fn("groupRevealMealsByDay"),
        _fn("revealDaysFromMenu"),
        _fn("revealSlotDishHtml"),
        _fn("revealMakes"),
        _fn("revealSlotMeta"),
        _fn("revealDayCardHtml"),
        _fn("revealTabsHtml"),
        _fn("revealPagerHtml"),
    ])


def _run(body: str):
    res = nodeharness.run_node(_renderer_harness() + "\n" + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout)


# ---------- the card, run rather than read ----------

_MENU_DAY = {
    "date": "2026-09-21", "before_plan_start": False,
    "dinner": {"title": "Lemon chicken & orzo", "meta": "35 min", "state": "planned", "entry_id": 31, "source": "plan"},
    "breakfast": {"title": "Overnight oats", "meta": None, "state": "planned", "entry_id": 29, "source": "plan"},
    "lunch": {"title": "Chickpea salad jars", "meta": "10 min", "state": "planned", "entry_id": 30, "source": "plan"},
    "snacks": [], "snack": None,
}


@_needs_node
def test_the_card_lists_the_meals_in_eating_order_with_a_swap_under_each():
    """Breakfast, lunch, dinner — in that order whatever order the data
    came in — each as an eyebrow, the dish, and a Swap carrying the entry
    id a swap needs."""
    out = _run(f"""
const days = revealDaysFromMenu([{json.dumps(_MENU_DAY)}]);
console.log(JSON.stringify(revealDayCardHtml(days[0], days)));
""")
    labels = re.findall(r'class="reveal-slot-label">([^<]+)<', out)
    assert labels == ["Breakfast", "Lunch", "Dinner"]
    assert out.index("Overnight oats") < out.index("Chickpea salad jars") < out.index("Lemon chicken &amp; orzo")
    assert out.count('class="reveal-swap"') == 3, "one Swap under every planned meal"
    assert 'data-entry-id="31"' in out and 'data-swap-slot="dinner"' in out
    assert 'stroke-width="2.2"' in out, "the swap-arrows icon is a stroke icon at the one width"
    assert '<p class="reveal-day-name">Monday</p>' in out
    assert '<span class="reveal-day-count">3 meals</span>' in out
    # The minutes ride along as the meta line; a slot without any says nothing.
    assert '<p class="reveal-slot-meta">35 min</p>' in out
    assert out.count("reveal-slot-meta") == 2


@_needs_node
def test_a_leftover_night_says_where_it_is_from_and_the_cook_night_says_what_it_makes():
    tue = {
        "date": "2026-09-22", "before_plan_start": False, "breakfast": None, "lunch": None, "snacks": [], "snack": None,
        "dinner": {"title": "Leftovers — Monday’s Lemon chicken & orzo", "meta": "reheat", "state": "planned",
                   "entry_id": 40, "source": "leftovers",
                   "leftover_from": {"date": "2026-09-21", "meal": "Lemon chicken & orzo", "cook_ahead": False}},
    }
    out = _run(f"""
const days = revealDaysFromMenu([{json.dumps(_MENU_DAY)}, {json.dumps(tue)}]);
console.log(JSON.stringify([revealDayCardHtml(days[0], days), revealDayCardHtml(days[1], days)]));
""")
    mon, tue_html = out
    assert '<p class="reveal-slot-meta">35 min · makes 2</p>' in mon
    # Only the meal the leftover eats — not every slot on that day.
    assert mon.count("makes 2") == 1
    assert '<p class="reveal-slot-dish">Lemon chicken &amp; orzo</p>' in tue_html, "the dish, not the whole headline"
    assert '<p class="reveal-slot-meta">from Monday</p>' in tue_html
    assert "reheat" not in tue_html


@_needs_node
def test_an_open_or_empty_slot_has_no_swap_and_says_so_plainly():
    day = {
        "date": "2026-09-23", "before_plan_start": False, "breakfast": None, "snacks": [], "snack": None,
        "lunch": {"title": "Your call", "meta": None, "state": "open", "entry_id": 50, "source": "plan"},
        "dinner": {"title": "Nobody home", "meta": None, "state": "planned_empty", "entry_id": 51, "source": "plan"},
    }
    out = _run(f"""
const days = revealDaysFromMenu([{json.dumps(day)}]);
console.log(JSON.stringify(revealDayCardHtml(days[0], days)));
""")
    assert "Still deciding" in out and "Nothing planned" in out
    assert "reveal-swap" not in out
    assert '<span class="reveal-day-count">0 meals</span>' in out


@_needs_node
def test_the_stream_and_the_saved_plan_draw_the_same_row():
    """A streamed item carries meal_name and minutes; the saved plan's
    menu carries entry_id and meta. Both land as the same row — the only
    difference is that the streamed row cannot yet be swapped."""
    out = _run("""
const streamed = revealSlotFromStream({ date: '2026-09-21', slot: 'dinner', meal_name: 'Chili', prep_time_minutes: 10, cook_time_minutes: 25 });
const saved = revealSlotFromMenu('dinner', { title: 'Chili', meta: '35 min', state: 'planned', entry_id: 7 });
const fallback = revealSlotFromPlanMeal({ date: '2026-09-21', slot: 'dinner', meal: 'Chili', entry_id: 7, slot_state: 'planned' });
console.log(JSON.stringify([streamed, saved, fallback]));
""")
    streamed, saved, fallback = out
    assert streamed["dish"] == saved["dish"] == fallback["dish"] == "Chili"
    assert streamed["meta"] == saved["meta"] == "35 min"
    assert streamed["entryId"] is None and saved["entryId"] == fallback["entryId"] == 7


@_needs_node
def test_days_before_the_plan_starts_and_empty_days_are_not_cards():
    blank = {"date": "2026-09-20", "before_plan_start": True, "breakfast": None, "lunch": None, "dinner": None, "snacks": [], "snack": None}
    empty = {"date": "2026-09-27", "before_plan_start": False, "breakfast": None, "lunch": None, "dinner": None, "snacks": [], "snack": None}
    out = _run(f"""
const days = revealDaysFromMenu([{json.dumps(blank)}, {json.dumps(_MENU_DAY)}, {json.dumps(empty)}]);
console.log(JSON.stringify(days.map(d => d.date)));
""")
    assert out == ["2026-09-21"]


@_needs_node
def test_the_tabs_and_dots_name_the_days_and_mark_the_one_in_view():
    out = _run(f"""
const days = revealDaysFromMenu([{json.dumps(_MENU_DAY)}, {json.dumps(dict(_MENU_DAY, date="2026-09-22"))}]);
console.log(JSON.stringify([revealTabsHtml(days, 1), revealPagerHtml(days, 1)]));
""")
    tabs, dots = out
    assert re.findall(r'data-day-index="\d"[^>]*>([^<]+)<', tabs) == ["Mon", "Tue"]
    assert tabs.count('class="reveal-tab is-on"') == 1 and 'aria-selected="true" data-day-index="1"' in tabs
    assert dots == '<span></span><span class="is-on"></span>'


# ---------- the words on the screen ----------

def test_the_word_sample_is_gone_from_the_screen():
    step = _step_markup("step-reveal")
    step = re.sub(r"<!--.*?-->", "", step, flags=re.S)
    assert "sample" not in step.lower()
    assert "sample" not in ONBOARDING_VISIBLE.lower(), "'sample' still appears in the page's live copy or code"
    assert 'const REVEAL_TITLE_READY = "Here\'s week 1.";' in ONBOARDING
    assert "Let's tweak my first sample week" not in SHELL_JS
    assert "Let's tweak my first week — " in SHELL_JS


def test_the_head_says_what_the_screen_is_for_and_nothing_else():
    step = _step_markup("step-reveal")
    assert '<p class="reveal-eyebrow">Your first week</p>' in step
    assert "Swipe through the days. Swap anything you don&rsquo;t fancy." in step
    assert 'id="reveal-number"' not in step and 'id="reveal-receipt"' not in step
    for gone in ("Review my week", "YOU'RE SET UP", "That's everything I need."):
        assert gone not in ONBOARDING_VISIBLE, gone
    assert ">Approve &middot; Open grocery list</button>" in step
    assert ">or tweak it with me</button>" in step


def test_the_strip_and_its_controls_are_hidden_until_a_day_lands():
    step = _step_markup("step-reveal")
    for el in ("reveal-tabs", "reveal-days", "reveal-pager"):
        assert re.search(rf'<div id="{el}"[^>]*\bhidden\b', step), f"#{el} ships visible"
        assert f".{el}[hidden]" in ONBOARDING
    show = _fn("revealShowDays")
    for el in ("reveal-tabs", "reveal-pager", "reveal-lead"):
        assert f"getElementById('{el}').hidden = false" in show
    assert "revealShowDays()" in _fn("upsertRevealDay")
    css = ONBOARDING[ONBOARDING.index("<style>"): ONBOARDING.index("</style>")]
    assert "scroll-snap-type: x mandatory" in css
    assert "@media (prefers-reduced-motion: reduce) { .reveal-days { scroll-behavior: auto; } }" in css


def test_approve_is_the_apps_own_approve_route_and_hands_off_to_the_list():
    approve = _fn("approveFirstWeek")
    assert "'/api/week/' + encodeURIComponent(firstPlanWeekStart) + '/approve'" in approve
    assert "confirm_hard_conflicts: revealApproveConfirming" in approve
    assert "data.status === 'needs_confirmation'" in approve, "a hard clash is said, never approved past silently"
    assert "'/grocery?after=approve'" in _fn("revealAfterApproveUrl")
    # The other end: Shop reads it, says it took, and scrubs it.
    assert "get('after') === 'approve'" in SHELL_JS
    assert "showToast('Approved. Here’s your list.');" in SHELL_JS


def test_a_failed_week_keeps_try_again_and_take_me_in_anyway():
    step = _step_markup("step-reveal")
    assert 'id="reveal-retry-btn" type="button">Try again<' in step
    assert 'id="reveal-anyway-btn" type="button">Take me in anyway<' in step
    generate = _fn("generateFirstPlanAndReveal")
    catch = generate[generate.index("} catch (err) {"):]
    assert "renderRevealFailed()" in catch
    assert "const REVEAL_FAILED_TITLE = 'Your answers are saved.';" in ONBOARDING


def test_something_else_opens_the_chat_with_the_slot_named():
    assert "revealGoTweak(cur ? revealSlotName(cur.date, cur.slot) : '')" in _fn("wireRevealSwapSheet")
    assert "'&about=' + encodeURIComponent(about)" in _fn("revealGoTweak")
    assert 'new URLSearchParams(window.location.search).get(\'about\')' in SHELL_JS


def test_the_help_button_opens_the_shared_sheet_named_for_this_screen():
    step = _step_markup("step-reveal")
    assert 'id="reveal-help-btn" aria-label="Need a hand?"' in step
    assert "window.openHelpSheet({ screenName: 'Week 1' })" in ONBOARDING
    assert '<script src="/static/help-sheet.js"></script>' in ONBOARDING


# ---------- three picks: the routes ----------

TODAY = household_today()
WEEK_START = TODAY.isoformat()
DAY1 = WEEK_START
DAY2 = (TODAY + datetime.timedelta(days=1)).isoformat()


def _pick(name, reason="Lighter, and nothing to thaw.", protein="chicken"):
    return {
        "meal_name": name, "reason": reason, "is_new_recipe": True,
        "ingredients": [{"item": protein.title(), "qty": "1 lb", "category": "meat/seafood"},
                        {"item": "Lemons", "qty": "2", "category": "produce"}],
        "instructions": ["Cook it."], "food_groups": ["protein", "vegetable", "carb"],
        "main_protein": protein, "prep_time_minutes": 10, "cook_time_minutes": 25, "default_servings": 2,
    }


def _asker(*options):
    seen = []

    def ask(context):
        seen.append(context)
        return list(options)

    ask.contexts = seen
    return ask


@pytest.fixture
def week():
    tools.add_member("Emily")
    for name in ("Pork Chops", "Chili"):
        tools.add_recipe(name, ingredients=[{"item": name.split()[0], "qty": "1 lb", "category": "meat/seafood"}],
                         food_groups=["protein"], prep_time_minutes=10, cook_time_minutes=20)
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(DAY1, "Pork Chops", slot="dinner", weekly_plan_id=plan_id, reasoning="fits")
    tools.plan_meal(DAY2, "Chili", slot="dinner", weekly_plan_id=plan_id, reasoning="fits")
    sop._OPTIONS_CACHE.clear()
    return plan_id


def _entry_id(plan_id, day):
    conn = get_conn()
    row = conn.execute("SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
                       (plan_id, day)).fetchone()
    conn.close()
    return row["id"]


def test_three_picks_come_back_gated_and_nothing_is_written(week):
    ask = _asker(_pick("Lemon Chicken Traybake"), _pick("Pork Chops"), _pick("Fish Tacos", protein="cod"),
                 _pick("Veg Curry", protein="chickpeas"))
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=ask)
    names = [o["meal"] for o in out["options"]]
    assert names == ["Lemon Chicken Traybake", "Fish Tacos", "Veg Curry"], "the dish being replaced is never offered back"
    assert out["options"][0] == {"index": 0, "meal": "Lemon Chicken Traybake", "reason": "Lighter, and nothing to thaw.", "minutes": 35}
    assert out["options_unavailable"] is False
    assert ask.contexts[0]["avoid"] == ["Pork Chops"] and ask.contexts[0]["replacing"] == "Pork Chops"
    conn = get_conn()
    meal = conn.execute("SELECT COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
                        "LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE mpe.id = ?", (_entry_id(week, DAY1),)).fetchone()["meal"]
    conn.close()
    assert meal == "Pork Chops"


def test_a_pick_the_house_cannot_have_is_dropped_not_shown(week):
    tools.set_member_dietary_restrictions("Emily", ["peanut allergy"])
    bad = _pick("Peanut Noodles", protein="peanuts")
    bad["ingredients"] = [{"item": "Peanuts", "qty": "1 cup", "category": "pantry"}]
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=_asker(bad, _pick("Fish Tacos", protein="cod")))
    assert [o["meal"] for o in out["options"]] == ["Fish Tacos"]


def test_a_failed_call_is_said_apart_from_finding_nothing(week):
    def broken(context):
        raise RuntimeError("no api")
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=broken)
    assert out["options"] == [] and out["options_unavailable"] is True


def test_choosing_a_pick_applies_it_through_the_swaps_own_door(week):
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=_asker(_pick("Lemon Chicken Traybake"), _pick("Fish Tacos", protein="cod")))
    out = tools.choose_swap_option(week, entry_id, 1)
    assert out["status"] == "swapped"
    assert out["meal"] == "Fish Tacos" and out["replaced"] == "Pork Chops" and out["can_undo"] is True
    assert out["day"]["dinner"]["title"] == "Fish Tacos"
    # Undo is the swap's own: the old dish is on the new entry's note.
    restored = tools.undo_meal_swap(week, out["entry_id"])
    assert restored["meal"] == "Pork Chops"
    # And the picks are forgotten with the slot they were for.
    with pytest.raises(ValueError):
        tools.choose_swap_option(week, out["entry_id"], 0)


def test_the_routes_are_week_scoped_and_answer_with_the_day(signed_in, week, monkeypatch):
    monkeypatch.setattr(tools, "swap_options",
                        lambda plan_id, entry_id, avoid=None: sop.swap_options(
                            plan_id, entry_id, avoid=avoid, asker=_asker(_pick("Lemon Chicken Traybake"), _pick("Fish Tacos", protein="cod"))))
    entry_id = _entry_id(week, DAY1)
    res = signed_in.post(f"/api/week/{WEEK_START}/swap-options", json={"entry_id": entry_id, "avoid": []})
    assert res.status_code == 200
    assert [o["meal"] for o in res.json()["options"]] == ["Lemon Chicken Traybake", "Fish Tacos"]
    res = signed_in.post(f"/api/week/{WEEK_START}/swap-choose", json={"entry_id": entry_id, "option": 0})
    assert res.status_code == 200
    assert res.json()["status"] == "swapped" and res.json()["day"]["dinner"]["title"] == "Lemon Chicken Traybake"
    assert signed_in.post(f"/api/week/{WEEK_START}/swap-options", json={"entry_id": 99999}).status_code == 404
    assert signed_in.post(f"/api/week/{WEEK_START}/swap-choose", json={"entry_id": entry_id, "option": 9}).status_code == 404
