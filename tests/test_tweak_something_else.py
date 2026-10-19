"""
The Tweak sheet's "Something else" (Loop Board, Emily 2026-10-18: "an
option to select that's 'share something else' where it can be a free
text place to share feedback in case it doesn't fit into these options").

A fourth row under the carb opens a box in the same sheet. What's typed
goes through "Tell Pomona what to change"'s own route
(POST /api/meal-recipe/rewrite → agent.rewrite_meal_recipe) — this meal
only, recipe and shopping together, the allergy gate before anything is
saved — and is filed on the meal (recipe_change_requests) whether or not
it lands. Two things are new on that path, both pinned here: every way it
can fail to land is filed (an error and a meal with no recipe used to go
unrecorded), and a draft dish whose recipe isn't written yet is written
first instead of refused. The screen half runs shell.js's own functions
under node.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import agent, tools
from app.db import get_conn
from app.tools import recipe_change
from test_week_seven_tiles import _extract
from test_plan_rows_tweak_it import _plate_prelude, _run, _PARTS
from test_plan_cards_2026_09_18 import _entry as _js_entry, _day as _js_day, _TUE as _JS_TUE

from conftest import household_date

MON = household_date(0)
TUE = household_date(1)
WED = household_date(2)

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the renderers")


# ---------- fixtures ----------

def _recipe(name, items, **kw):
    return tools.add_recipe(
        name, [{"item": i, "qty": q, "category": "other"} for i, q in items],
        instructions=kw.pop("instructions", ["Cook it.", "Serve it."]),
        default_servings=4, prep_time_minutes=10, cook_time_minutes=20, **kw,
    )["recipe_id"]


def _plan(status="draft"):
    plan = tools.create_weekly_plan(MON, content_start_date=MON, day_count=3)["weekly_plan_id"]
    if status == "approved":
        conn = get_conn()
        try:
            conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan,))
            conn.commit()
        finally:
            conn.close()
    return plan


def _meal(plan, date, name, buy=False):
    return tools.plan_meal(date, name, slot="dinner", weekly_plan_id=plan,
                           add_ingredients_to_grocery_list=buy)["entry_id"]


def _ingredients(recipe_id):
    conn = get_conn()
    try:
        return json.loads(conn.execute("SELECT ingredients_json FROM recipes WHERE id = ?",
                                       (recipe_id,)).fetchone()[0])
    finally:
        conn.close()


def _needed():
    conn = get_conn()
    try:
        return {r["item"] for r in conn.execute(
            "SELECT item FROM grocery_items WHERE status IN ('needed', 'in_cart', 'spice')")}
    finally:
        conn.close()


def _requests():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT dish_name, request_text, outcome, meal_plan_entry_id FROM recipe_change_requests ORDER BY id")]
    finally:
        conn.close()


def _writer(*details):
    """A stand-in recipe writer answering each call with the next detail."""
    calls = []

    def write(spec):
        calls.append(spec)
        return details[min(len(calls), len(details)) - 1]
    write.calls = calls
    return write


_NO_CILANTRO = {
    "ingredients": [{"item": "Chicken thighs", "qty": "1 lb"}, {"item": "Lime", "qty": "2"}],
    "instructions": ["Grill the chicken.", "Squeeze over the lime."],
    "default_servings": 4,
}


# ---------- the change ----------

def test_a_typed_change_rewrites_this_meal_and_its_shopping_and_nothing_else(signed_in, monkeypatch):
    plan = _plan("approved")
    tacos = _recipe("Chicken Tacos", [("Chicken thighs", "1 lb"), ("Cilantro", "1 bunch")])
    curry = _recipe("Chickpea Curry", [("Chickpeas", "2 cans"), ("Cilantro", "1 bunch")])
    entry = _meal(plan, TUE, "Chicken Tacos", buy=True)
    _meal(plan, WED, "Chickpea Curry", buy=True)
    writer = _writer(_NO_CILANTRO)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", writer)

    res = signed_in.post("/api/meal-recipe/rewrite", json={"entry_id": entry, "text": "No cilantro"})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "rewritten", body
    assert body["request_id"]
    assert "remember_offer" not in body, "one meal's change is not a standing rule"
    assert writer.calls[0]["name"] == "Chicken Tacos"
    assert writer.calls[0]["change_requested"] == "No cilantro"
    assert "Cilantro" not in {i["item"] for i in _ingredients(tacos)}
    # The other meal is not this one, and keeps its recipe — and its share
    # of the cilantro on the list.
    assert "Cilantro" in {i["item"] for i in _ingredients(curry)}
    assert {"Lime", "Cilantro", "Chickpeas"} <= _needed()
    assert _requests() == [{"dish_name": "Chicken Tacos", "request_text": "No cilantro",
                            "outcome": "rewritten", "meal_plan_entry_id": entry}]


def test_a_standing_rule_is_offered_to_be_remembered(signed_in, monkeypatch):
    plan = _plan("draft")
    _recipe("Chicken Tacos", [("Chicken thighs", "1 lb"), ("Cilantro", "1 bunch")])
    entry = _meal(plan, TUE, "Chicken Tacos")
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _writer(_NO_CILANTRO))

    body = signed_in.post("/api/meal-recipe/rewrite",
                          json={"entry_id": entry, "text": "We never want cilantro"}).json()

    assert body["status"] == "rewritten"
    assert body["remember_offer"] is True


@pytest.mark.parametrize("text,lasting", [
    ("we never want cilantro", True), ("The kids won’t eat the sauce", True),
    ("less spicy", False), ("make it one pan", False), ("every bit of it, but milder", False),
])
def test_what_reads_as_a_standing_rule(text, lasting):
    assert recipe_change.reads_as_lasting(text) is lasting


# ---------- when it can't be done ----------

def test_a_change_that_would_bring_back_a_restricted_food_is_refused_and_filed(signed_in, monkeypatch):
    """Household restrictions still apply: the writer is held to them, and
    a rewrite that puts one back in twice is refused with nothing saved."""
    tools.add_member("Reid")
    tools.set_member_dietary_restrictions("Reid", ["peanuts"])
    plan = _plan("draft")
    rid = _recipe("Chicken Tacos", [("Chicken thighs", "1 lb")])
    entry = _meal(plan, TUE, "Chicken Tacos")
    nutty = {"ingredients": [{"item": "Chicken thighs", "qty": "1 lb"}, {"item": "Peanuts", "qty": "1 cup"}],
             "instructions": ["Cook it with peanuts."]}
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _writer(nutty))

    res = signed_in.post("/api/meal-recipe/rewrite", json={"entry_id": entry, "text": "Add peanut sauce"})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "blocked"
    assert "peanut" in body["said"].lower()
    assert {i["item"] for i in _ingredients(rid)} == {"Chicken thighs"}
    assert _requests() == [{"dish_name": "Chicken Tacos", "request_text": "Add peanut sauce",
                            "outcome": "failed", "meal_plan_entry_id": entry}]


def test_a_meal_with_no_recipe_says_so_and_the_words_are_still_filed(signed_in):
    plan = _plan("draft")
    entry = tools.plan_meal(TUE, "Takeout from the Thai place", slot="dinner", weekly_plan_id=plan,
                            add_ingredients_to_grocery_list=False)["entry_id"]
    conn = get_conn()
    try:
        conn.execute("UPDATE meal_plan_entries SET recipe_id = NULL, freeform_meal = ? WHERE id = ?",
                     ("Takeout from the Thai place", entry))
        conn.commit()
    finally:
        conn.close()

    res = signed_in.post("/api/meal-recipe/rewrite", json={"entry_id": entry, "text": "No cilantro ever"})

    assert res.status_code == 400
    assert res.json()["detail"] == recipe_change.REWRITE_NO_RECIPE
    rows = _requests()
    assert [(r["request_text"], r["outcome"], r["meal_plan_entry_id"]) for r in rows] == [
        ("No cilantro ever", "failed", entry)]


def test_an_error_mid_write_is_filed_once_not_twice(signed_in, monkeypatch):
    plan = _plan("draft")
    _recipe("Chicken Tacos", [("Chicken thighs", "1 lb")])
    entry = _meal(plan, TUE, "Chicken Tacos")
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _writer({"ingredients": [], "instructions": []}))

    res = signed_in.post("/api/meal-recipe/rewrite", json={"entry_id": entry, "text": "Less spicy"})

    assert res.status_code == 400
    assert [r["outcome"] for r in _requests()] == ["failed"]


def test_another_households_meal_files_nothing_of_theirs(signed_in):
    from app import households

    other = households.create_household("Tweak Else Isolation", "tweak-else-pass")
    with tools.use_household(other):
        plan = tools.create_weekly_plan(MON, content_start_date=MON, day_count=3)["weekly_plan_id"]
        tools.add_recipe("Their Rajma", [{"item": "Kidney beans", "qty": "2 cans"}],
                         instructions=["Cook."])
        theirs = tools.plan_meal(TUE, "Their Rajma", slot="dinner", weekly_plan_id=plan,
                                 add_ingredients_to_grocery_list=False)["entry_id"]

    res = signed_in.post("/api/meal-recipe/rewrite", json={"entry_id": theirs, "text": "Less spicy"})

    assert res.status_code == 400
    assert _requests() == [{"dish_name": "", "request_text": "Less spicy", "outcome": "failed",
                            "meal_plan_entry_id": None}]


# ---------- a draft dish with no recipe written yet ----------

def test_a_draft_dish_not_written_yet_is_written_then_changed(signed_in, monkeypatch):
    """Most of a fresh draft is details_pending (the recipe pass writes it
    at approval). Refusing those would refuse nearly every meal the Tweak
    sheet is opened on, so it is written now — fill_in_recipe, the Cook
    screen's own early write — and then changed."""
    plan = _plan("draft")
    tools.add_recipe("Chicken Tacos", [], details_pending=True, prep_time_minutes=10, cook_time_minutes=20)
    entry = _meal(plan, TUE, "Chicken Tacos")
    first = {"ingredients": [{"item": "Chicken thighs", "qty": "1 lb"}, {"item": "Cilantro", "qty": "1 bunch"}],
             "instructions": ["Grill.", "Top with cilantro."], "default_servings": 4}
    writer = _writer(first, _NO_CILANTRO)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", writer)

    body = signed_in.post("/api/meal-recipe/rewrite", json={"entry_id": entry, "text": "No cilantro"}).json()

    assert body["status"] == "rewritten", body
    assert len(writer.calls) == 2
    assert "change_requested" not in writer.calls[0]
    assert writer.calls[1]["change_requested"] == "No cilantro"
    recipe = tools.get_recipe("Chicken Tacos")
    assert not recipe.get("details_pending")
    assert "Cilantro" not in {i["item"] for i in recipe["ingredients"]}


# ---------- the screen ----------

@_needs_node
def test_the_fourth_row_sits_under_the_carb_and_its_box_is_closed_until_tapped():
    entry = _js_entry("Lemon chicken & orzo", plate_parts=[
        {"role": "protein", "word": "Protein", "name": "Chicken thighs", "source": "dish", "missing": False},
        {"role": "vegetable", "word": "Veg", "name": "Green beans", "source": "dish", "missing": False},
        {"role": "carb", "word": "Carb", "name": "Orzo", "source": "dish", "missing": False}])
    day = _js_day(_JS_TUE, dinner=entry)
    html = _run(_plate_prelude() + f"console.log(JSON.stringify(tweakSheetBodyHtml({json.dumps(day)}, 'dinner', {json.dumps(entry)})));")

    rows = html[html.index('<div class="plate-rows">'):html.index('<div class="wk-tweak-else"')]
    assert rows.index("Carb") < rows.index("Something else"), "the fourth row is under CARB"
    assert '>Tell me</button>' in rows
    box = html[html.index('<div class="wk-tweak-else"'):]
    assert box.startswith('<div class="wk-tweak-else" id="wk-tweak-else" hidden>')
    assert "Tell me what to change" in box
    assert '<button type="button" class="cr-go" id="wk-tweak-go">Change it</button>' in box
    assert html.count('class="cr-go"') == 1, "one apricot fill on the sheet"
    assert html.endswith('id="wk-tweak-leave">Leave it as it is</button>')


def _drive(answer: dict, ok: bool = True) -> dict:
    """runTweakElse under node, with the sheet's elements as plain objects
    and the route answering `answer`."""
    prelude = """
function el() { return { hidden: false, textContent: '', value: '', readOnly: false, disabled: false,
  focus: function () {}, setAttribute: function () {} }; }
var els = { 'wk-tweak-else': el(), 'wk-tweak-text': el(), 'wk-tweak-go': el(), 'wk-tweak-said': el(),
  'wk-tweak-done': el(), 'wk-tweak-done-row': el(), 'wk-tweak-undo': el(), 'wk-tweak-remember': el(),
  'wk-tweak-leave': el() };
els['wk-tweak-done-row'].hidden = true;
els['wk-tweak-said'].hidden = true; els['wk-tweak-done'].hidden = true; els['wk-tweak-remember'].hidden = true;
var label = el();
var tweakSheetEl = { hidden: false, classList: { contains: function () { return true; } },
  querySelector: function (sel) { return sel.charAt(0) === '#' ? els[sel.slice(1)] : label; } };
var log = { closed: 0, after: null, sent: null, marked: null, toasts: [] };
var panels = {};
function closeTweakSheet() { log.closed += 1; }
function markRecentlyChanged(d, s) { log.marked = [d, s]; }
async function crAfterChange(said, rid, quiet) { log.after = [said, rid, !!quiet]; }
function showToast(t) { log.toasts.push(t); }
function loadWeekMenu() {}
var Api = { fetch: async function (url, init) { log.sent = [url, JSON.parse(init.body)];
  return { ok: OK, json: async function () { return ANSWER; } }; } };
var tweakElseState = { panel: {}, date: '2026-10-20', slot: 'dinner', entryId: 41, busy: false, text: '' };
els['wk-tweak-text'].value = 'We never want cilantro';
""".replace("OK", "true" if ok else "false").replace("ANSWER", json.dumps(answer))
    harness = (prelude + _extract("tweakElseEl", SHELL_JS) + _extract("tweakElseSay", SHELL_JS)
               + "async " + _extract("runTweakElse", SHELL_JS)
               + "runTweakElse().then(function () { console.log(JSON.stringify({ log: log, els: els })); });\n")
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_the_box_sends_this_meal_and_a_change_closes_the_sheet_with_put_it_back():
    out = _drive({"status": "rewritten", "said": "Chicken Tacos is rewritten.", "request_id": 7})
    assert out["log"]["sent"] == ["/api/meal-recipe/rewrite", {"entry_id": 41, "text": "We never want cilantro"}]
    assert out["log"]["closed"] == 1
    assert out["log"]["after"] == ["Chicken Tacos is rewritten.", 7, False]
    assert out["log"]["marked"] == ["2026-10-20", "dinner"]


@_needs_node
def test_a_standing_rule_keeps_the_sheet_up_with_put_it_back_and_the_offer():
    out = _drive({"status": "rewritten", "said": "Chicken Tacos is rewritten.", "request_id": 7,
                  "remember_offer": True})
    els = out["els"]
    assert out["log"]["closed"] == 0
    assert out["log"]["after"] == ["Chicken Tacos is rewritten.", 7, True], "no toast over the offer"
    assert els["wk-tweak-done-row"]["hidden"] is False and els["wk-tweak-undo"]["hidden"] is False
    assert els["wk-tweak-remember"]["hidden"] is False
    assert els["wk-tweak-go"]["hidden"] is True
    assert els["wk-tweak-leave"]["textContent"] == "Just this once"


@_needs_node
def test_a_refusal_says_why_in_one_line_and_keeps_the_words():
    out = _drive({"status": "blocked", "said": "I couldn’t write that one without peanut in it.",
                  "remember_offer": True})
    els = out["els"]
    assert out["log"]["closed"] == 0 and out["log"]["after"] is None
    assert els["wk-tweak-text"]["value"] == "We never want cilantro"
    assert els["wk-tweak-text"]["readOnly"] is False
    assert els["wk-tweak-said"]["hidden"] is False
    assert els["wk-tweak-said"]["textContent"] == "I couldn’t write that one without peanut in it."
    assert els["wk-tweak-remember"]["hidden"] is False
    assert els["wk-tweak-go"]["disabled"] is False and els["wk-tweak-go"]["textContent"] == "Change it"


@_needs_node
def test_an_error_keeps_the_words_too():
    out = _drive({"detail": "That meal has no saved recipe yet."}, ok=False)
    assert out["els"]["wk-tweak-text"]["value"] == "We never want cilantro"
    assert out["els"]["wk-tweak-said"]["textContent"] == "That meal has no saved recipe yet."


def test_remember_it_goes_through_the_chat():
    body = _extract("rememberTweakElse", SHELL_JS)
    assert "sendAskMessage('Remember this for every week: ' + text);" in body
