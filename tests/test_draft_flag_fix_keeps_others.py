"""
Taking one draft flag's fix clears THAT flag — never the other lines the
same meal is carrying.

A meal can carry more than one draft line: an over-cap line with its
"Keep on Wednesday" / "Move it to Saturday" / "Prep it Tuesday night"
offers, and beside it a line with no buttons at all (an allergy swap, a
"tonight" that came in late, a "nothing else fit"). The flag-fix route
used to call `draft_flags.dismiss(plan, entry_id)`, which removed EVERY
stored line for the entry — so tapping Keep on the time line silently
took the allergy sentence with it. And because the route picked the first
live flag for the entry, a line with no fixes stored ahead of the over-cap
one answered every tap with "gone".

The second half: the Move fix trades two nights through
`swap_dinner_nights`, exactly as the swap-nights route does, but did not
re-check the draft's lines afterwards the way that route does
(`_refresh_draft_flags`), so a "nothing else fit" line whose run the
trade had just broken stayed on screen.

Every draft here is generated through the stubbed model and the stubbed
re-picker of test_rush_cap_enforced — never a paid call.
"""
from __future__ import annotations

import json

import pytest

from app import tools
from app.db import get_conn
from app.tools import draft_flags

from test_draft_snag_flags import _asked_for, _flags, _monday
from test_rush_cap_enforced import (  # noqa: E402
    _dinners, _filler, _recipe, _week,
    capped, picker, run, stub_model,  # noqa: F401 — pytest fixtures
)


def _lasagna_week(stub_model, run):
    """Wednesday's asked-for 60-minute Lasagna on a 20-minute rush night —
    flagged, with prep / move-to-Saturday / keep offered."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    flags = _flags(plan_id)
    assert len(flags) == 1 and flags[0]["kind"] == draft_flags.OVER_CAP_REQUEST
    return week, dates, plan_id, flags[0]


def _allergy_line(flag: dict) -> dict:
    """An allergy line on the same meal as `flag` — what the allergen gate's
    own producer writes, stored the way draft_flags.add stores it."""
    return {"kind": draft_flags.ALLERGY_REQUEST,
            "text": "You asked for peanut noodles — Sam is allergic to peanuts, so it’s Lasagna.",
            "entry_id": flag["entry_id"], "date": flag["date"], "slot": "dinner",
            "dish": flag["dish"]}


def _kinds_on(plan_id: int, entry_id: int) -> list[str]:
    return sorted(f["kind"] for f in _flags(plan_id) if f["entry_id"] == entry_id)


@pytest.mark.parametrize("action", ["keep", "prep_ahead"])
def test_taking_a_fix_clears_that_flag_and_keeps_the_allergy_line_beside_it(
        signed_in, capped, stub_model, run, action):
    """CATCH. The reported bug: Keep (and Prep, the other fix that leaves
    the meal where it is) took the allergy line off with the time line."""
    week, dates, plan_id, flag = _lasagna_week(stub_model, run)
    draft_flags.add(plan_id, [_allergy_line(flag)])
    assert _kinds_on(plan_id, flag["entry_id"]) == sorted(
        [draft_flags.OVER_CAP_REQUEST, draft_flags.ALLERGY_REQUEST])

    res = signed_in.post(f"/api/week/{week}/flag-fix",
                         json={"entry_id": flag["entry_id"], "action": action})

    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True
    assert _kinds_on(plan_id, flag["entry_id"]) == [draft_flags.ALLERGY_REQUEST]
    # And the week the screen loads says the same.
    menu = tools.get_week_menu(plan_id)
    assert [f["kind"] for f in menu.get("draft_flags") or []] == [draft_flags.ALLERGY_REQUEST]


def test_keep_leaves_another_meals_lines_alone(signed_in, capped, stub_model, run):
    """Edge: a line on a DIFFERENT meal was never at risk from dismiss's
    entry match, and must stay that way under the narrower one."""
    week, dates, plan_id, flag = _lasagna_week(stub_model, run)
    monday = next(r for r in _dinners(plan_id) if r["date"] == dates[0])
    draft_flags.add(plan_id, [{"kind": draft_flags.LATE_REQUEST,
                               "text": "Monday dinner had already gone by — it’s planned anyway.",
                               "entry_id": monday["id"], "date": dates[0], "slot": "dinner",
                               "dish": monday["meal"]}])

    signed_in.post(f"/api/week/{week}/flag-fix",
                   json={"entry_id": flag["entry_id"], "action": "keep"})

    assert [f["kind"] for f in _flags(plan_id)] == [draft_flags.LATE_REQUEST]


def test_a_line_with_no_fixes_stored_first_does_not_swallow_the_tap(signed_in, capped,
                                                                    stub_model, run):
    """CATCH. Edge: the route took the FIRST live flag for the entry. With
    the allergy line stored ahead of the time line, that flag offers no
    fixes, so Keep answered "gone" and did nothing."""
    week, dates, plan_id, flag = _lasagna_week(stub_model, run)
    draft_flags.record(plan_id, [_allergy_line(flag), flag])
    assert _flags(plan_id)[0]["kind"] == draft_flags.ALLERGY_REQUEST

    res = signed_in.post(f"/api/week/{week}/flag-fix",
                         json={"entry_id": flag["entry_id"], "action": "keep"})

    assert res.json()["applied"] is True, res.json()
    assert _kinds_on(plan_id, flag["entry_id"]) == [draft_flags.ALLERGY_REQUEST]


def _set_lunch(plan_id: int, date: str, name: str) -> int:
    conn = get_conn()
    rid = conn.execute("SELECT id FROM recipes WHERE name = ?", (name,)).fetchone()["id"]
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'lunch' "
        "AND component_category IS NULL", (plan_id, date)).fetchone()
    conn.execute("UPDATE meal_plan_entries SET recipe_id = ?, freeform_meal = NULL WHERE id = ?",
                 (rid, row["id"]))
    conn.commit()
    conn.close()
    return row["id"]


def test_the_move_fix_takes_off_a_nothing_else_fit_line_its_trade_broke(signed_in, capped,
                                                                       stub_model, run):
    """
    CATCH. Quick Eggs runs Friday dinner → Saturday lunch → Saturday dinner
    → Sunday lunch, and Sunday lunch carries "nothing else fit". Moving the
    Lasagna to Saturday puts it in the middle of that run, so the line is
    no longer true — swap-nights re-checks it; the Move fix did not.
    """
    week, dates, plan_id, flag = _lasagna_week(stub_model, run)
    _set_lunch(plan_id, dates[5], "Quick Eggs")
    sun_lunch = _set_lunch(plan_id, dates[6], "Quick Eggs")
    run_line = {"kind": draft_flags.RUN_LEFT,
                "text": draft_flags.run_left_text(dates[6], "lunch", "Quick Eggs", dates[4]),
                "entry_id": sun_lunch, "date": dates[6], "slot": "lunch", "dish": "Quick Eggs"}
    draft_flags.add(plan_id, [run_line])
    assert draft_flags._still_in_a_long_run(plan_id, run_line), "the run is real before the move"
    move = next(f for f in flag["fixes"] if f["action"] == draft_flags.FIX_MOVE)
    assert move["date"] == dates[5]

    res = signed_in.post(f"/api/week/{week}/flag-fix",
                         json={"entry_id": flag["entry_id"], "action": "move"})

    assert res.json()["applied"] is True, res.json()
    by_date = {r["date"]: r["meal"] for r in _dinners(plan_id)}
    assert by_date[dates[5]] == "Lasagna"
    assert not draft_flags._still_in_a_long_run(plan_id, run_line)
    assert _flags(plan_id) == [], "the broken run's line is still being said"


def test_a_stored_line_with_no_kind_is_still_cleared_by_keep(signed_in, capped, stub_model, run):
    """Edge: plan_flags reads a kind-less stored line as over-cap and offers
    its fixes, so Keep must clear it like one rather than leave it standing."""
    week, dates, plan_id, flag = _lasagna_week(stub_model, run)
    legacy = {k: v for k, v in flag.items() if k not in ("kind", "fixes")}
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET draft_flags_json = ? WHERE id = ?",
                 (json.dumps([legacy]), plan_id))
    conn.commit()
    conn.close()
    assert [x["action"] for x in _flags(plan_id)[0]["fixes"]].count("keep") == 1

    res = signed_in.post(f"/api/week/{week}/flag-fix",
                         json={"entry_id": flag["entry_id"], "action": "keep"})

    assert res.json()["applied"] is True
    assert _flags(plan_id) == []
