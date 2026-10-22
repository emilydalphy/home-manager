"""
Re-planning over part of an approved week: what the question says about
the shopping list, when the lines those days hold are pantry staples.

A recipe's spices, sauces and pantry staples land on the list with status
'spice' — "assumed at home, waiting in its section" (app/tools/pantry.py),
never bought. The takeover reverses them like any line still to buy
(grocery._reverse_meal_grocery_contributions treats 'spice' as live), but
the question asked before it (preview_approved_takeover) counted every
status that was not 'needed' as BOUGHT. So a re-plan over nights whose only
lines were pasta and stock was told "Nothing comes off the shopping list —
it's all bought already." — nothing had been bought, and both lines then
came off the list on approval.
"""
from __future__ import annotations

import datetime

from conftest import household_today
from app import agent, tools
from app.db import get_conn


def _day(n: int) -> str:
    return (household_today() + datetime.timedelta(days=n)).isoformat()


def _period(start: str, count: int, dinner: str) -> list[dict]:
    return [
        {"date": d, "slot": "dinner", "meal_name": dinner, "is_new_recipe": False, "reasoning": "x"}
        for d in tools.period_dates(start, count)
    ] + [
        {"date": d, "slot": s, "meal_name": f"{s.title()} plate", "is_new_recipe": False, "reasoning": "x"}
        for d in tools.period_dates(start, count) for s in ("breakfast", "lunch")
    ]


def _approved_week_then_draft_over_it(monkeypatch, *, purchase: str | None = None) -> tuple[int, int]:
    # Both ingredients are pantry staples, so both land as 'spice'.
    tools.add_recipe("Pasta", ingredients=[{"item": "penne", "qty": "1 box"},
                                           {"item": "chicken broth", "qty": "1 carton"}],
                     prep_time_minutes=5, cook_time_minutes=15)
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=5, cook_time_minutes=15)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                        lambda c: _period(c["week_start_date"], c["day_count"], "Pasta"))
    week = agent.generate_weekly_plan(_day(7), day_count=7, period_start=_day(7), confirm_takeover=True)
    tools.approve_weekly_plan(week["weekly_plan_id"])
    conn = get_conn()
    statuses = {r["item"]: r["status"] for r in conn.execute("SELECT item, status FROM grocery_items")}
    if purchase:
        conn.execute("UPDATE grocery_items SET status = 'purchased' WHERE item = ?", (purchase,))
        conn.commit()
    conn.close()
    assert statuses == {"penne": "spice", "chicken broth": "spice"}, statuses
    monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                        lambda c: _period(c["week_start_date"], c["day_count"], "Chili"))
    draft = agent.generate_weekly_plan(_day(10), day_count=7, period_start=_day(10), confirm_takeover=True)
    return week["weekly_plan_id"], draft["weekly_plan_id"]


def test_pantry_lines_are_not_called_bought(monkeypatch):
    _approved_week_then_draft_over_it(monkeypatch)

    preview = tools.preview_approved_takeover(_day(10), 7)

    assert preview["grocery_bought_line_count"] == 0
    assert preview["grocery_line_count"] == 2
    assert "bought already" not in preview["note"]
    assert "2 things on your shopping list would change" in preview["note"]


def test_the_count_matches_what_approval_takes_off(monkeypatch):
    _, draft_id = _approved_week_then_draft_over_it(monkeypatch)
    preview = tools.preview_approved_takeover(_day(10), 7)

    result = tools.retire_overlapping_plans(draft_id, _day(10), 7)

    # Both pantry lines lost a share (the week keeps three Pasta nights,
    # so they are trimmed, not removed) — and neither is reported as kept
    # because somebody bought it.
    assert result["grocery_kept_bought"] == []
    changed = set(result["grocery_removed"]) | set(result["grocery_trimmed"])
    assert len(changed) == preview["grocery_line_count"]


def test_a_purchased_line_is_still_bought(monkeypatch):
    # The edge the old rule existed for: a line somebody bought is kept,
    # counted as bought, and said so.
    _, draft_id = _approved_week_then_draft_over_it(monkeypatch, purchase="penne")
    preview = tools.preview_approved_takeover(_day(10), 7)
    assert preview["grocery_bought_line_count"] == 1
    assert preview["grocery_line_count"] == 1

    result = tools.retire_overlapping_plans(draft_id, _day(10), 7)
    assert result["grocery_kept_bought"] == ["penne"]


def _jar_week(monkeypatch) -> None:
    tools.add_recipe("Pasta", ingredients=[{"item": "ground cumin", "qty": "1 jar"},
                                           {"item": "olive oil", "qty": "1 bottle"},
                                           {"item": "soy sauce", "qty": "2 tbsp"}],
                     prep_time_minutes=5, cook_time_minutes=15)
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=5, cook_time_minutes=15)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                        lambda c: _period(c["week_start_date"], c["day_count"], "Pasta"))
    week = agent.generate_weekly_plan(_day(7), day_count=7, period_start=_day(7), confirm_takeover=True)
    tools.approve_weekly_plan(week["weekly_plan_id"])
    monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                        lambda c: _period(c["week_start_date"], c["day_count"], "Chili"))


def test_a_jar_no_surviving_night_names_is_counted(monkeypatch):
    # The other side of the jar rule: replace every night and the jar, the
    # bottle and the soy sauce all go — three lines, said as three.
    _jar_week(monkeypatch)
    agent.generate_weekly_plan(_day(7), day_count=7, period_start=_day(7), confirm_takeover=True)
    assert tools.preview_approved_takeover(_day(7), 7)["grocery_line_count"] == 3


def test_a_jar_a_surviving_night_still_names_is_not_counted(monkeypatch):
    # Review, 2026-10-22: a jar of cumin and a bottle of oil are held whole
    # while any meal still names them, so replacing four of seven nights
    # leaves both lines exactly as they were — only the soy sauce (a
    # measured amount) is trimmed. The question has to say one, not three.
    tools.add_recipe("Pasta", ingredients=[{"item": "ground cumin", "qty": "1 jar"},
                                           {"item": "olive oil", "qty": "1 bottle"},
                                           {"item": "soy sauce", "qty": "2 tbsp"}],
                     prep_time_minutes=5, cook_time_minutes=15)
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=5, cook_time_minutes=15)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                        lambda c: _period(c["week_start_date"], c["day_count"], "Pasta"))
    week = agent.generate_weekly_plan(_day(7), day_count=7, period_start=_day(7), confirm_takeover=True)
    tools.approve_weekly_plan(week["weekly_plan_id"])
    conn = get_conn()
    before = {r["item"].lower(): (r["status"], r["quantity"]) for r in conn.execute("SELECT item, status, quantity FROM grocery_items")}
    conn.close()
    assert set(before) == {"ground cumin", "olive oil", "soy sauce"}, before
    monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                        lambda c: _period(c["week_start_date"], c["day_count"], "Chili"))
    draft = agent.generate_weekly_plan(_day(10), day_count=7, period_start=_day(10), confirm_takeover=True)

    preview = tools.preview_approved_takeover(_day(10), 7)
    result = tools.retire_overlapping_plans(draft["weekly_plan_id"], _day(10), 7)

    conn = get_conn()
    after = {r["item"].lower(): (r["status"], r["quantity"]) for r in conn.execute("SELECT item, status, quantity FROM grocery_items")}
    conn.close()
    changed = {item for item in before if after.get(item) != before[item]}
    assert changed == {"soy sauce"}, (before, after)
    assert preview["grocery_line_count"] == len(changed) == 1
    assert "one thing on your shopping list would change" in preview["note"]
    assert {i.lower() for i in result["grocery_removed"] + result["grocery_trimmed"]} == {"soy sauce"}
