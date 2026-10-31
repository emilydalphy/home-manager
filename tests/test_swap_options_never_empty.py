"""
"As a household swapping a lunch, I get options — not 'I couldn't think of
options just now'" (Loop Board, Emily 2026-10-31).

What happened: Swap on Sunday's lunch said the sentence. Railway's log for
that minute has ONE swap-options call, answered 200 with "3 of 3 picks
kept" in 4.4 s — the server never saw the sheet's own ask, so it was lost
between the phone and the server, and fetchSwapPicks gave up on the first
failure. Now the sheet asks once more (and never waits forever), and when
the server genuinely has nothing — the model call failed, or every pick it
named was refused — it offers dishes from the recipe library that fit the
slot, through the same gates.
"""
from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import tools
from app.tools import recipe_library as lib
sop = importlib.import_module("app.tools.swap_option_picks")
from test_week1_carousel import DAY1, DAY2, _asker, _entry_id, _pick, week  # noqa: F401
from test_week_seven_tiles import _extract, _extract_async

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the sheet's own code")


# ---- the library fallback (server) ---------------------------------------

def _library(monkeypatch, dishes):
    """File each (name, meal, main item) as a written library base and make
    them the starter list the fallback reads."""
    seed = []
    for name, meal, item in dishes:
        lib.file_entry(name, ingredients=[{"item": item, "qty": "1 lb", "category": "produce"},
                                          {"item": "Onion", "qty": "1", "category": "produce"}],
                       instructions=["Cook it."], default_servings=4, prep_time_minutes=10,
                       cook_time_minutes=15, origin="seed")
        seed.append({"dish_name": name, "meal": meal, "main_protein": "vegetarian", "cuisine": "Any"})
    monkeypatch.setattr(sop, "_SEED_DISHES", seed)


def _broken(context):
    raise RuntimeError("no api")


def test_a_failed_call_offers_library_dishes_for_the_meal_instead(week, monkeypatch):
    _library(monkeypatch, [("Lentil Soup", "dinner", "Lentils"), ("Bean Chili", "dinner", "Black beans"),
                           ("Veg Fried Rice", "dinner", "Rice"), ("Egg Salad Sandwich", "lunch", "Eggs"),
                           ("Fruit Cup", "snack", "Apples")])
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=_broken)
    names = sorted(o["meal"] for o in out["options"])
    assert names == ["Bean Chili", "Lentil Soup", "Veg Fried Rice"], "a dinner gets dinner dishes only"
    assert out["options_unavailable"] is False, "the sheet has picks, so it says nothing went wrong"
    assert all(o["minutes"] == 25 for o in out["options"])


def test_every_pick_refused_by_a_gate_falls_back_and_the_library_is_gated_too(week, monkeypatch):
    tools.set_member_dietary_restrictions("Emily", ["peanut allergy"])
    _library(monkeypatch, [("Peanut Stew", "dinner", "Peanuts"), ("Lentil Soup", "dinner", "Lentils")])
    bad = _pick("Satay Noodles", protein="peanuts")
    bad["ingredients"] = [{"item": "Peanuts", "qty": "1 cup", "category": "pantry"}]
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=_asker(bad))
    assert [o["meal"] for o in out["options"]] == ["Lentil Soup"]


def test_the_library_never_offers_the_dish_going_one_turned_down_or_one_on_the_week(week, monkeypatch):
    _library(monkeypatch, [("Pork Chops", "dinner", "Pork"), ("Chili", "dinner", "Beef"),
                           ("Lentil Soup", "dinner", "Lentils"), ("Bean Tacos", "dinner", "Beans")])
    out = tools.swap_options(week, _entry_id(week, DAY1), avoid=["Bean Tacos"], asker=_broken)
    assert [o["meal"] for o in out["options"]] == ["Lentil Soup"]


def test_model_picks_that_survive_are_never_topped_up_from_the_library(week, monkeypatch):
    _library(monkeypatch, [("Lentil Soup", "dinner", "Lentils")])
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=_asker(_pick("Fish Tacos", protein="cod")))
    assert [o["meal"] for o in out["options"]] == ["Fish Tacos"]


def test_a_diet_the_gate_cant_read_gets_no_library_dishes(week, monkeypatch):
    _library(monkeypatch, [("Lentil Soup", "dinner", "Lentils")])
    tools.set_member_dietary_restrictions("Emily", ["kosher"])
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=_broken)
    assert out["options"] == [] and out["options_unavailable"] is True


def test_the_library_switch_off_leaves_the_old_answer(week, monkeypatch):
    _library(monkeypatch, [("Lentil Soup", "dinner", "Lentils")])
    monkeypatch.setenv("RECIPE_LIBRARY", "off")
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=_broken)
    assert out["options"] == [] and out["options_unavailable"] is True


def test_a_library_pick_tapped_on_a_draft_is_planned(week, monkeypatch):
    _library(monkeypatch, [("Lentil Soup", "dinner", "Lentils")])
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=_broken)
    out = tools.choose_swap_option(week, entry_id, 0, meal="Lentil Soup")
    assert out["status"] == "swapped" and out["meal"] == "Lentil Soup"


def test_a_typed_request_puts_matching_library_dishes_first(week, monkeypatch):
    _library(monkeypatch, [("Lentil Soup", "dinner", "Lentils"), ("Bean Chili", "dinner", "Beans"),
                           ("Veg Fried Rice", "dinner", "Rice"), ("Mushroom Risotto", "dinner", "Mushrooms")])
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=_broken, request="something with mushroom")
    assert out["options"][0]["meal"] == "Mushroom Risotto"


def test_the_starter_list_the_fallback_reads_says_which_meal_each_dish_is_for():
    dishes = json.loads(sop.SEED_FILE.read_text(encoding="utf-8"))["dishes"]
    assert {d["meal"] for d in dishes} == {"breakfast", "lunch", "dinner", "snack"}
    assert set(sop.LIBRARY_MEALS_FOR_SLOT) == {"breakfast", "lunch", "dinner", "snack"}


# ---- the sheet asks once more (client) -----------------------------------

def _run_fetch(responses):
    """Drive fetchSwapPicks against a stub Api. Each response is 'throw' (a
    request lost on the way) or {status, body}."""
    js = (
        _extract("escapeHtml", SHELL_JS) + "\n"
        + "var calls = 0; var plan = " + json.dumps(responses) + ";\n"
        + "var Api = { fetch: async function (url, init) { calls += 1; var r = plan.shift();"
          " if (r === 'throw') throw new TypeError('Load failed');"
          " return { ok: r.status < 300, status: r.status, json: async function () { return r.body; } }; } };\n"
        + "function drawSwapSheet() {}\n"
        + "function swapRouteMessage(b) { return b && b.detail || ''; }\n"
        + "var st = { entryId: 5, weekStart: '2026-11-01', wholeDish: false, options: null, trouble: '' };\n"
        + "var swapSheetState = st;\n"
        + _extract("withPlanId", SHELL_JS) + "\n"
        + _extract_async("fetchSwapPicks", SHELL_JS) + "\n"
        + "(async function () { await fetchSwapPicks(st, {});"
          " console.log(JSON.stringify({ calls: calls, options: st.options, trouble: st.trouble })); })();"
    )
    res = nodeharness.run_node(js, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


_PICKS = {"options": [{"index": 0, "meal": "Lentil Soup", "reason": "", "minutes": 25}], "options_unavailable": False}


@_needs_node
def test_a_request_lost_on_the_way_is_asked_once_more_and_the_picks_land():
    out = _run_fetch(["throw", {"status": 200, "body": _PICKS}])
    assert out["calls"] == 2 and out["options"][0]["meal"] == "Lentil Soup" and out["trouble"] == ""


@_needs_node
def test_a_server_error_is_asked_once_more():
    out = _run_fetch([{"status": 502, "body": None}, {"status": 200, "body": _PICKS}])
    assert out["calls"] == 2 and out["options"]


@_needs_node
def test_two_failures_say_so_and_a_404_is_never_asked_again():
    lost = _run_fetch(["throw", "throw"])
    assert lost["calls"] == 2 and lost["trouble"].startswith("I couldn’t think of options just now")
    gone = _run_fetch([{"status": 404, "body": {"detail": "There's no meal on that slot to swap."}}])
    assert gone["calls"] == 1 and gone["trouble"] == "There's no meal on that slot to swap."


def test_the_wait_has_an_end():
    body = _extract_async("fetchSwapPicks", SHELL_JS)
    assert "AbortController" in body and "timeoutMs = 30000" in body
    assert "err.name === 'AbortError'" in body, "a wait that ran out is not asked again"
