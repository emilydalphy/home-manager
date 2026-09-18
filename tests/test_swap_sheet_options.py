"""
The Plan tab's Swap sheet, on the Week 1 screen's picks (2026-09-18).

Two branches built "three swap picks" the same day — the Week 1 reveal's
(app/tools/swap_options.py: POST /swap-options, POST /swap-choose with the
pick's index) and Plan's (a second module and two more routes). The
integration kept Week 1's and rewired Plan's sheet (shell.js openSwapSheet /
runSwapPick) onto it. What is here is what Plan's own tests covered and
Week 1's did not (tests/test_week1_carousel.py has the rest): a duplicate
pick is offered once, the gate runs again on the tap, and the sheet's
markup carries the pick's index.
"""
from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import tools
# The package re-exports the function under the module's name, so the
# module itself is reached the way test_week1_carousel reaches it.
sop = importlib.import_module("app.tools.swap_options")
from test_week1_carousel import DAY1, WEEK_START, _asker, _entry_id, _pick, week  # noqa: F401
from test_week_seven_tiles import _extract

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the sheet's own renderer")


def test_a_duplicate_pick_is_offered_once_whatever_its_case(week):
    ask = _asker(_pick("Chicken Fajitas"), _pick("chicken fajitas"), _pick("Veggie Quesadillas", protein="chickpeas"))
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=ask)
    assert [o["meal"] for o in out["options"]] == ["Chicken Fajitas", "Veggie Quesadillas"]


def test_the_gate_runs_again_on_the_tap(week):
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=_asker(_pick("Lemon Chicken Traybake"), _pick("Fish Tacos", protein="cod")))
    # The house changed between the sheet opening and the tap.
    tools.set_member_dietary_restrictions("Emily", ["chicken allergy"])
    out = tools.choose_swap_option(week, entry_id, 0)
    assert out["status"] == "refused" and "!" not in out["message"]
    assert "Lemon Chicken Traybake" in out["message"], "says which pick, and why it stayed off"
    assert sop.choose_swap_option(week, entry_id, 1)["status"] == "swapped", "the other pick is still on offer"


def test_the_route_says_why_a_pick_is_refused_and_a_bad_index_is_a_404(signed_in, week, monkeypatch):
    monkeypatch.setattr(tools, "swap_options",
                        lambda plan_id, entry_id, avoid=None: sop.swap_options(
                            plan_id, entry_id, avoid=avoid, asker=_asker(_pick("Lemon Chicken Traybake"))))
    entry_id = _entry_id(week, DAY1)
    assert signed_in.post(f"/api/week/{WEEK_START}/swap-options", json={"entry_id": entry_id}).status_code == 200
    res = signed_in.post(f"/api/week/{WEEK_START}/swap-choose", json={"entry_id": entry_id, "option": -1})
    assert res.status_code == 404 and res.json()["detail"] == "That isn't one of the picks."


@_needs_node
def test_the_sheet_draws_each_pick_with_its_own_index():
    js = (
        "function escapeHtml(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;'); }\n"
        "var WK_ICONS = { chev: '<svg></svg>' };\n"
        + _extract("swapPickHtml", (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8"))
        + "\nvar picks = " + json.dumps([
            {"index": 0, "meal": "Sheet-Pan Sausages", "reason": "uses the sausages", "minutes": 30},
            {"index": 2, "meal": "Veggie quesadillas", "reason": "", "minutes": None},
        ])
        + ";\nconsole.log(JSON.stringify(picks.map(swapPickHtml)));"
    )
    res = nodeharness.run_node(js, timeout=30)
    assert res.returncode == 0, res.stderr
    first, third = json.loads(res.stdout.strip())
    assert 'data-wk-swap-pick="0"' in first and "Sheet-Pan Sausages" in first and "30 min · uses the sausages" in first
    assert 'data-wk-swap-pick="2"' in third and "wk-swap-pick-why" not in third, "nothing to say, no line"
