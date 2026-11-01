"""
The morning report's own "Refused" line (Emily, 2026-10-31).

A gate telling the household no — an allergy clash, a night already gone,
an approved week — is an answer, not a breakage, and since
overnight/a-refusal-is-not-a-breakage it produced nothing anywhere: twelve
allergy refusals in a day read as "Chat was for — 12 plan_meal", i.e. as
twelve meals planned. That hides exactly the bug that matters most here,
an allergen matcher that has started over-firing.

So a refusal is now COUNTED, by a short reason code per gate, under its
own line — and still never under BROKEN, never in `total`, never in the
exit code.
"""
from __future__ import annotations

import contextlib
import io

import pytest

from app import agent, tools
from app import main as main_module
from app.db import get_conn
from app.tools import weekly_plan as _weekly_plan

import observability_report as report
from test_a_refusal_is_not_a_breakage import _one_tool_turn


def _refused_rows():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT where_, detail, occurrences FROM error_events WHERE kind = 'refused' ORDER BY id"
        )]
    finally:
        conn.close()


def _chat_refusal(monkeypatch, exc, tool="plan_meal"):
    def _refuse(**kwargs):
        raise exc
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, tool, _refuse)
    _one_tool_turn(monkeypatch, tool, {"meal_date": "2026-10-05", "meal": "Chicken Satay"})
    agent.run_agent_turn([], "put chicken satay on Monday")


def test_chat_refusals_are_counted_by_reason_and_never_as_breakage(monkeypatch):
    """CATCH: the card's own example — 3 allergy clashes, 1 night gone."""
    for _ in range(3):
        _chat_refusal(monkeypatch, tools.SlotRefused(
            "Sam can't have peanuts.", reason="allergy"))
    _chat_refusal(monkeypatch, tools.SlotRefused(_weekly_plan.NIGHT_GONE), tool="swap_meal_in_plan")

    errors = tools.get_recent_errors(days=1)
    assert errors["total"] == 0
    assert errors["by_kind"] == {}
    assert errors["recent"] == []
    assert errors["refused"] == {"total": 4, "by_reason": {"allergy": 3, "night_gone": 1}}


def test_the_sentence_never_reaches_the_table():
    """The sentence names a dish and somebody's restriction; only the code
    is stored."""
    tools.record_refusal(tools.SlotRefused("Sam can't have the Peanut Satay.", reason="allergy"),
                         "plan_meal")
    assert _refused_rows() == [{"where_": "plan_meal", "detail": "allergy", "occurrences": 1}]


def test_a_real_error_beside_refusals_is_still_the_only_breakage():
    """GUARD: setting refusals aside must not hide a real error, nor count
    refusals into it."""
    tools.record_refusal(tools.SlotRefused("x", reason="allergy"), "plan_meal")
    tools.record_error("tool", where="plan_meal", detail="KeyError")
    errors = tools.get_recent_errors(days=1)
    assert errors["total"] == 1
    assert errors["by_kind"] == {"tool": 1}
    assert [r["kind"] for r in errors["recent"]] == ["tool"]
    assert errors["refused"]["total"] == 1


def test_the_real_allergy_gate_files_as_allergy():
    """The code comes from the gate itself, not from a test's say-so."""
    tools.add_member("Sam")
    tools.set_member_dietary_restrictions("Sam", ["peanut allergy"])
    from app.tools import allergen_gate
    with pytest.raises(tools.SlotRefused) as caught:
        allergen_gate.refuse_if_clashing(
            "Peanut Noodles", [{"item": "Peanut butter", "qty": "3 tbsp", "category": "pantry"}])
    assert caught.value.reason == "allergy"


def test_reason_codes_for_untagged_and_other_markers():
    assert tools.refusal_reason(tools.SlotRefused(_weekly_plan.NIGHT_GONE)) == "night_gone"
    assert tools.refusal_reason(tools.SlotRefused("Something new.")) == "other"
    assert tools.refusal_reason(tools.MemberRefused("Emily is the only adult.")) == "last_adult"
    assert tools.refusal_reason(tools.MoveOwnerRefused("No Priya here.")) == "no_such_adult"


def test_a_route_refusal_is_counted_too(signed_in, monkeypatch):
    """The screens' doors, not only chat: a refused draft discard."""
    monkeypatch.setattr(main_module.tools, "discard_draft_plan",
                        lambda *a, **k: (_ for _ in ()).throw(tools.SlotRefused(
                            "That week's approved — reopen it or re-plan it instead.",
                            reason="approved_week")))
    monkeypatch.setattr(main_module, "_plan_id_for_week", lambda week_start: 1)
    res = signed_in.post("/api/week/2026-10-05/discard", json={})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "refused"
    assert tools.get_recent_errors(days=1)["refused"]["by_reason"] == {"approved_week": 1}
    assert tools.get_recent_errors(days=1)["total"] == 0


def _printed(h):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        report._print_household(h, 1)
    return out.getvalue()


def test_the_report_prints_its_own_line_and_still_says_nothing_broke():
    tools.record_refusal(tools.SlotRefused("x", reason="allergy"), "plan_meal")
    tools.record_refusal(tools.SlotRefused("x", reason="allergy"), "plan_meal")
    tools.record_refusal(tools.SlotRefused("x", reason="allergy"), "swap_meal_in_plan")
    tools.record_refusal(tools.SlotRefused(_weekly_plan.NIGHT_GONE), "swap_meal_in_plan")
    h = {"household": "demo", "household_id": 1,
         "errors": tools.get_recent_errors(days=1), "usage": None}
    text = _printed(h)
    assert "Nothing broke." in text
    assert "BROKEN" not in text
    assert "Refused — 3 allergy clashes, 1 night already gone" in text


def test_no_refusals_no_line_and_an_old_deployment_without_the_key():
    h = {"household": "demo", "household_id": 1,
         "errors": tools.get_recent_errors(days=1), "usage": None}
    assert "Refused" not in _printed(h)
    old = dict(h, errors={k: v for k, v in h["errors"].items() if k != "refused"})
    assert "Refused" not in _printed(old)


def test_an_unknown_code_prints_as_itself():
    assert report._refusal_phrases({"brand_new_gate": 2, "allergy": 1}) == \
        "2 brand_new_gate, 1 allergy clash"


def test_a_crashed_allergy_checker_is_breakage_not_an_allergy_clash():
    """Review, 2026-11-01: when check_meal_conflicts throws, the gate holds
    the dish back with a placeholder clash (source 'check_failed') — and
    that was filed as an "allergy clash", so a broken matcher would read
    "Refused — 12 allergy clashes" with exit code 0. It is a breakage."""
    from app.tools import allergen_gate, usage
    clashes = [{"meal": "Satay", "source": "check_failed", "severity": "hard"}]
    assert allergen_gate.refusal_reason_for(clashes) == "check_failed"
    assert allergen_gate.refusal_reason_for([{"source": "allergy"}]) == "allergy"

    usage.record_refusal(_weekly_plan.SlotRefused("held back", reason="check_failed"), where="plan_meal")
    assert _refused_rows() == []
    assert usage.get_recent_errors(days=1)["total"] >= 1
