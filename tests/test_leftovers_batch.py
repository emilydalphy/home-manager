"""
Cook once, eat twice — and only cook once.

Emily, 2026-09-04, looking at the Cook view showing "Korean Beef Bulgogi
Lettuce Wraps · for 3" on Tuesday AND again as a cook on Thursday: "It
should show the one night it's being cooked as 6 servings, make a little
note that this covers tonight + leftovers, and not have it on another
night for cooking."

The branch under this one (fix-leftovers-ordering) made the chains
trustworthy — it validates every "this night eats an earlier night's
batch" claim and records the confirmed ones on the source entry's
derived_from as `make_double_for`. Nothing read that back, so the second
night still behaved in every way like a second cook: its own card, its own
ingredients, its own groceries, its own defrost reminder, its own prep.

These tests pin down the four places that now read it (app/tools/
leftovers.py is the shared reader): the Cook view, the grocery
contribution, the defrost candidates, and the prep-schedule context — plus
the two things that must NOT change, a plan whose chains were never
validated and the component-based bulk-cook path this borrowed its
scaling helper from.
"""
import datetime

import pytest

from app import agent, tools


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


TUE, WED, THU, FRI = _day(1), _day(2), _day(3), _day(4)


def _household(*names):
    for n in names or ("Alex", "Sam", "Rae"):
        tools.add_member(n)


def _wraps(default_servings=3, **kw):
    tools.add_recipe(
        "Bulgogi Wraps",
        ingredients=[
            {"item": "beef", "qty": "1 lb"},
            {"item": "lettuce", "qty": "1 head"},
            {"item": "salt", "qty": "to taste"},
        ],
        default_servings=default_servings,
        **kw,
    )


def _chain(leftover_days=(THU,), cook_day=TUE):
    """
    A Tuesday cook with one or more later nights eating its leftovers,
    validated the way generation validates it. repair_leftover_chains is
    what writes make_double_for onto the source; without that call the
    chain is only a claim, and nothing downstream is allowed to act on it.
    """
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(cook_day, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
                    reasoning="Quick on a Tuesday")
    for d in leftover_days:
        tools.plan_meal(d, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
                        derived_from={"links_to": f"{cook_day}:dinner"})
    tools.repair_leftover_chains(plan_id)
    return plan_id


def _by_date(view):
    return {m["date"]: m for m in view["meals"]}


def _qty(meal):
    return {i["item"]: i["qty"] for i in meal["ingredients"]}


# ---------- the Cook view ----------

def test_the_cook_night_carries_the_whole_batch():
    _household("Alex", "Sam", "Rae")
    _wraps()
    plan_id = _chain()

    tuesday = _by_date(tools.get_cooker_view(plan_id))[TUE]

    assert tuesday["servings"] == 6, "three at Tuesday's table plus three at Thursday's"
    assert tuesday["is_leftovers"] is False
    assert _qty(tuesday) == {"beef": "2 lbs", "lettuce": "2 heads", "salt": "to taste"}
    assert tuesday["covers"] == [{"date": THU, "slot": "dinner", "eaters": 3}]


def test_the_cook_nights_note_names_the_nights_it_covers():
    _household("Alex", "Sam", "Rae")
    _wraps()
    plan_id = _chain()

    note = _by_date(tools.get_cooker_view(plan_id))[TUE]["covers_note"]

    assert "for 6" in note
    assert "Thursday" in note
    # "tonight" only when the cook night really is today — read on Sunday
    # this card still has to be honest about which night it means.
    assert ("tonight" in note) == (TUE == datetime.date.today().isoformat())


def test_the_leftover_night_is_a_reheat_not_a_cook():
    _household("Alex", "Sam", "Rae")
    _wraps()
    plan_id = _chain()

    thursday = _by_date(tools.get_cooker_view(plan_id))[THU]

    assert thursday["is_leftovers"] is True
    assert thursday["leftovers_headline"] == "Leftovers — Tuesday’s Bulgogi Wraps"
    assert thursday["leftovers_from"]["date"] == TUE
    assert thursday["ingredients"] == []
    assert thursday["instructions"] == []
    assert thursday["has_full_recipe"] is False
    assert thursday["advance_prep_notes"] == ""
    assert thursday["servings"] == 3, "still a real night, with a real table"
    # It is still an entry someone can check off — it just isn't a cook.
    assert thursday["entry_id"]


def test_someone_away_on_the_leftover_night_lowers_the_batch():
    _household("Alex", "Sam", "Rae")
    _wraps()
    plan_id = _chain()

    tools.set_member_attendance(THU, "dinner", "Rae", present=False)

    tuesday = _by_date(tools.get_cooker_view(plan_id))[TUE]
    assert tuesday["servings"] == 5
    assert "for 5" in tuesday["covers_note"]


def test_two_leftover_nights_off_one_cook_are_summed():
    _household("Alex", "Sam", "Rae")
    _wraps()
    plan_id = _chain(leftover_days=(THU, FRI))

    view = _by_date(tools.get_cooker_view(plan_id))
    tuesday = view[TUE]

    assert tuesday["servings"] == 9
    assert "Thursday" in tuesday["covers_note"] and "Friday" in tuesday["covers_note"]
    assert [c["date"] for c in tuesday["covers"]] == [THU, FRI]
    assert view[THU]["is_leftovers"] is True
    assert view[FRI]["is_leftovers"] is True


def test_a_reheat_night_shows_the_recipes_reheating_advice_when_it_has_any():
    _household("Alex", "Sam")
    tools.add_recipe(
        "Bulgogi Wraps",
        ingredients=[{"item": "beef", "qty": "1 lb"}],
        default_servings=2,
        notes="Freezes well. Reheat in a hot pan, not the microwave.",
    )
    plan_id = _chain()

    assert _by_date(tools.get_cooker_view(plan_id))[THU]["reheat_note"] == (
        "Reheat in a hot pan, not the microwave."
    )


def test_a_chain_nobody_validated_changes_nothing():
    """
    A links_to that repair_leftover_chains never confirmed is still only a
    claim. Both nights stay ordinary cooks, exactly as before this change
    — the agreement check in leftovers.plan_leftover_chains is what keeps
    an unvalidated plan out of all of this.
    """
    _household("Alex", "Sam", "Rae")
    _wraps()
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(TUE, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(THU, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"{TUE}:dinner"})

    view = _by_date(tools.get_cooker_view(plan_id))
    assert view[TUE]["servings"] is None
    assert not view[TUE].get("covers_note")
    assert view[THU]["is_leftovers"] is False
    assert view[THU]["has_full_recipe"] is True


# ---------- the grocery list ----------

def test_the_shop_buys_the_batch_once():
    _household("Alex", "Sam", "Rae")
    _wraps()
    plan_id = _chain()

    tools.approve_weekly_plan(plan_id, "Emily")

    bought = {g["item"]: g["quantity"] for g in tools.list_grocery_list()}
    assert bought == {"beef": "2 lbs", "lettuce": "2 heads", "salt": "to taste"}


def test_the_reheat_night_contributes_nothing_to_the_shop():
    _household("Alex", "Sam", "Rae")
    _wraps()
    plan_id = _chain()
    thursday_entry = _by_date(tools.get_cooker_view(plan_id))[THU]["entry_id"]

    tools.approve_weekly_plan(plan_id, "Emily")

    from app.db import get_conn
    conn = get_conn()
    links = conn.execute(
        "SELECT meal_plan_entry_id FROM meal_plan_grocery_links"
    ).fetchall()
    conn.close()
    assert thursday_entry not in [r["meal_plan_entry_id"] for r in links]


def test_clearing_the_plan_takes_back_exactly_what_approving_it_added():
    _household("Alex", "Sam", "Rae")
    _wraps()
    plan_id = _chain()
    before = tools.list_grocery_list()

    tools.approve_weekly_plan(plan_id, "Emily")
    tools.clear_weekly_plan(plan_id)

    assert tools.list_grocery_list() == before == []


def test_clearing_leaves_a_standing_want_alone_but_removes_the_scaled_line():
    """
    The reversal has to be symmetric with the scaled contribution, not with
    the recipe as written — a doubled line taken back at single strength
    would leave half a pound of beef on the list forever.
    """
    _household("Alex", "Sam", "Rae")
    _wraps()
    tools.add_grocery_item("beef", quantity="1 lb", added_by="user")
    plan_id = _chain()

    tools.approve_weekly_plan(plan_id, "Emily")
    tools.clear_weekly_plan(plan_id)

    remaining = {g["item"]: g["quantity"] for g in tools.list_grocery_list()}
    assert remaining == {"beef": "1 lb"}, "the household's own line survives, at its own amount"


# ---------- prep and defrost ----------

def test_the_prep_context_holds_the_cook_night_only(monkeypatch):
    _household("Alex", "Sam", "Rae")
    _wraps(advance_prep_notes="Marinate the beef at least 4 hours ahead")
    plan_id = _chain()

    seen = {}

    def _capture(ctx):
        seen["ctx"] = ctx
        return []

    monkeypatch.setattr(agent, "generate_prep_schedule_llm", _capture)
    agent.generate_prep_schedule(plan_id)

    meals = seen["ctx"]["meals"]
    assert [m["date"] for m in meals] == [TUE], "nothing is prepped for a night nothing is cooked on"
    assert meals[0]["servings"] == 6
    assert "Thursday" in meals[0]["covers"]
    assert {i["item"]: i["qty"] for i in meals[0]["ingredients"]}["beef"] == "2 lbs"


def test_defrost_reminds_once_for_the_cook_night_and_sizes_it_to_the_batch():
    _household("Alex", "Sam", "Rae")
    _wraps()
    tools.update_inventory("beef", "add", quantity="4 lbs", category="meat/seafood", location="freezer")
    plan_id = _chain()

    candidates = tools.defrost_candidates_for_plan(plan_id)

    assert len(candidates) == 1, "a reheat night has nothing to take out of the freezer"
    assert candidates[0]["meal_plan_entry_id"] == _by_date(tools.get_cooker_view(plan_id))[TUE]["entry_id"]
    assert candidates[0]["quantity"] == "2 lbs"


def test_marking_leftovers_eaten_does_not_deplete_the_kitchen_twice():
    _household("Alex", "Sam", "Rae")
    _wraps()
    tools.update_inventory("beef", "add", quantity="4 lbs", category="meat/seafood")
    plan_id = _chain()
    view = _by_date(tools.get_cooker_view(plan_id))

    tools.check_off_meal(view[THU]["entry_id"], "done")

    assert tools.check_off_meal(view[THU]["entry_id"], "done")["inventory_depleted"] == []
    assert {i["item"]: i["quantity"] for i in tools.get_inventory()}["beef"] == "4 lbs"


# ---------- what must not have moved ----------

def test_a_component_based_bulk_card_still_merges_and_scales_the_same_way():
    """
    The day-based path borrowed component mode's scaling into a shared
    helper (cooker._scale_card_to_batch). This is the guard that the
    borrowing didn't change the lender.
    """
    _household("Alex", "Sam")
    tools.add_recipe("Jello Bowl", ingredients=[{"item": "jello", "qty": "1 box"}], default_servings=2)
    week = _monday().isoformat()
    tools.set_planning_mode("component_based")
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    for _ in range(3):
        tools.plan_meal(week, "Jello Bowl", weekly_plan_id=plan_id, component_category="treat")

    meals = tools.get_cooker_view(plan_id)["meals"]

    assert len(meals) == 1, "three planned uses, one card"
    assert meals[0]["meal_count"] == 3
    assert meals[0]["default_servings"] == 6
    assert meals[0]["batch_note"] == "Bulk-cook once — makes enough for all 3 meals this week."
    assert _qty(meals[0]) == {"jello": "3 boxes"}
    assert meals[0]["is_leftovers"] is False


# ---------- the card itself, run rather than read ----------
#
# The Cook screen is vanilla JS in static/shell.js with no test harness, but
# these four functions are pure — a meal dict in, a string of HTML out — so
# they can be lifted out and executed under node the same way
# test_cooker_today.py already runs the page's own todaysMealIndex. That
# makes these behaviour tests of the rendered card rather than the
# "assert the source mentions the right word" kind, which matters here
# because the whole point of Emily's rule is what the screen shows.

import json
import shutil
import subprocess
from pathlib import Path

SHELL_JS = Path(__file__).resolve().parent.parent / "static" / "shell.js"

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)


def _extract(name: str, source: str) -> str:
    """Lift one brace-balanced `function name(...) {...}` out of the file."""
    start = source.index(f"function {name}(")
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


def _render(fn: str, arg, extra_args: str = "") -> str:
    """Run one of the screen's render functions under node and return its HTML."""
    src = SHELL_JS.read_text()
    harness = (
        "function escapeHtml(s){return String(s == null ? '' : s)"
        ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}\n"
        "const ICONS = { arrow: '<svg data-icon=\"arrow\"></svg>' };\n"
        "const COOK_ICONS = { check: '<svg data-icon=\"check\"></svg>' };\n"
        "const REHEAT_ACTION_LABEL = 'Mark eaten';\n"
        "const REHEAT_UNDO_LABEL = 'Mark not eaten';\n"
        "const cookState = { tonightIdx: 99 };\n"
        "function dayName(d){ return 'Tue'; }\n"
        + _extract("cookServesChip", src) + "\n"
        + _extract("cookReheatCardHtml", src) + "\n"
        + _extract("cookReheatHeroHtml", src) + "\n"
        + _extract("cookHeroHtml", src) + "\n"
        + _extract("cookRestOfWeekHtml", src) + "\n"
        + f"console.log(JSON.stringify({fn}({json.dumps(arg)}{extra_args})));\n"
    )
    res = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_REHEAT_MEAL = {
    "entry_id": 7, "date": THU, "slot": "dinner", "meal": "Bulgogi Wraps",
    "cooked_status": "pending", "is_leftovers": True, "servings": 3,
    "leftovers_headline": "Leftovers — Tuesday’s Bulgogi Wraps",
    "leftovers_from": {"date": TUE, "slot": "dinner", "meal": "Bulgogi Wraps"},
    "reheat_note": "", "has_full_recipe": False, "ingredients": [], "instructions": [],
    "advance_prep_notes": "", "batch_note": None, "default_servings": None,
}

_COOK_MEAL = {
    "entry_id": 3, "date": TUE, "slot": "dinner", "meal": "Bulgogi Wraps",
    "cooked_status": "pending", "is_leftovers": False, "servings": 6,
    "covers_note": "Cooking for 6 — covers Tuesday and leftovers on Thursday.",
    "default_servings": 6, "has_full_recipe": True, "batch_note": None,
    "advance_prep_notes": "", "reasoning": "Quick on a Tuesday",
}


@_needs_node
def test_the_reheat_hero_offers_one_action_and_no_cook_flow():
    html = _render("cookHeroHtml", _REHEAT_MEAL, ", 0")
    assert "Leftovers — Tuesday’s Bulgogi Wraps" in html
    assert "Mark eaten" in html
    assert "Start cooking" not in html
    assert 'data-cook="focus"' not in html, "nothing to open — there is no recipe here"
    assert "Bulk ×" not in html
    assert "for 3" in html


@_needs_node
def test_the_cook_hero_leads_with_the_batch_and_the_note():
    html = _render("cookHeroHtml", _COOK_MEAL, ", 0")
    assert "for 6" in html
    assert "Serves" not in html
    assert "covers Tuesday and leftovers on Thursday" in html
    assert "Start cooking" in html


@_needs_node
def test_a_reheat_row_in_the_week_list_is_not_a_way_into_a_recipe():
    html = _render("cookRestOfWeekHtml", [_REHEAT_MEAL, _COOK_MEAL])
    assert '<span class="cook-week-name">Leftovers — Tuesday’s Bulgogi Wraps</span>' in html
    assert ">Reheat<" in html
    assert 'data-cook="focus" data-idx="0"' not in html, "the reheat row is text, not a button"
    assert 'data-cook="focus" data-idx="1"' in html, "the cook row still opens its recipe"
