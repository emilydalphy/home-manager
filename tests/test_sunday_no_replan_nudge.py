"""
Today doesn't ask to plan a week that is already planned.

Loop Board "As a household on Sunday, Today doesn't ask to plan a week I've
already planned" (High bug, tester-reported). The tester had approved next
week, Mon Oct 26 – Sun Nov 1, and opened Today on Sunday Oct 25: Today asked
"Shall I put Oct 25–31 together?" under THIS WEEK, with "Let's plan the
week" in the dock — six of those seven days already approved.

Root cause: nothing covered the Sunday, so get_week_planning_nudge offered
suggest_planning_period's current period, which for a household whose week
starts on Sunday (any anchor but the default) is clamped to today and runs
Oct 25–31. The "already planned" check ran only for a NEXT-week suggestion.
Now the current one is cut to the gap before the next held day: a gap of
just today offers nothing (that night is tonight's dinner card's question,
"Tonight needs a dinner", which is untouched); a longer gap is offered as
that stretch, never the held days after it.

The dock button reads the same payload (shell.js todayNeedsPlan:
`nudge.week_start`), so `week_start: None` is what keeps it away.
"""
import pytest

from app import agent, tools
from app.db import get_conn

SUNDAY = "2026-10-25"
MONDAY = "2026-10-26"


def _anchor(value: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO household_rhythm (household_id, fact_type, weekday, value) "
        "VALUES (1, 'planning_anchor', '', ?)",
        (value,),
    )
    conn.commit()
    conn.close()


def _insert_plan(start: str, days: int, status: str = "approved") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, "
        "day_count, planning_mode) VALUES (1, ?, ?, ?, ?, 'day_based')",
        (start, status, start, days),
    )
    plan_id = cur.lastrowid
    for day in tools.period_dates(start, days):
        conn.execute(
            "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal) "
            "VALUES (1, ?, ?, 'dinner', 'Chili')",
            (plan_id, day),
        )
    conn.commit()
    conn.close()
    return plan_id


@pytest.mark.today(SUNDAY)
@pytest.mark.parametrize("anchor", ["saturday", "friday", "as_we_go"])
def test_an_approved_week_from_tomorrow_is_not_offered_again(anchor, client, signed_in):
    """CATCH: red on main — main offered Oct 25–31 (Oct 25–27 as-we-go)."""
    _anchor(anchor)
    _insert_plan(MONDAY, 7)

    nudge = client.get("/api/week/plan-nudge").json()

    assert nudge["show"] is False
    # What the dock's "Let's plan the week" reads (todayNeedsPlan).
    assert nudge["week_start"] is None


@pytest.mark.today(SUNDAY)
def test_tonight_is_still_asked_about_on_its_own(client, signed_in):
    """GUARD: the uncovered Sunday still gets "Tonight needs a dinner"."""
    _anchor("saturday")
    _insert_plan(MONDAY, 7)
    tools.add_recipe(
        "Tacos", ingredients=[{"item": "tortillas", "qty": "8"}], instructions=["Cook."],
        prep_time_minutes=10, cook_time_minutes=15,
    )

    items = client.get("/api/needs-you").json()["items"]

    tonight = [i for i in items if i["type"] == "dinner_decision"]
    assert [(i["title"], i["date"]) for i in tonight] == [("Tonight needs a dinner", SUNDAY)]


@pytest.mark.today(SUNDAY)
def test_a_draft_from_tomorrow_is_not_offered_again_either(client, signed_in):
    """CATCH: a draft in progress holds those days too; offering them again
    is an offer to throw it away (the next-week check always counted drafts)."""
    _anchor("saturday")
    _insert_plan(MONDAY, 7, status="draft")

    assert client.get("/api/week/plan-nudge").json()["show"] is False


@pytest.mark.today(SUNDAY)
def test_with_nothing_planned_the_week_is_still_offered(client, signed_in):
    """GUARD: the check only silences days that are already held."""
    _anchor("saturday")

    nudge = client.get("/api/week/plan-nudge").json()

    assert nudge["show"] is True
    assert (nudge["week_start"], nudge["week_label"], nudge["is_current_week"]) == (
        SUNDAY, "Oct 25–31", True,
    )


@pytest.mark.today(SUNDAY)
@pytest.mark.parametrize("plan_start, label, days", [
    ("2026-10-28", "Oct 25–27", 3),  # F: the plan starts Wednesday
    ("2026-10-31", "Oct 25–30", 6),  # G: the plan starts Saturday
])
def test_a_longer_gap_is_offered_as_just_the_gap(plan_start, label, days, client, signed_in):
    """GUARD (review of the first cut, which offered nothing here): more than
    tonight is uncovered, so the gap is offered — and only the gap."""
    _anchor("saturday")
    _insert_plan(plan_start, 7)

    nudge = client.get("/api/week/plan-nudge").json()

    assert nudge["show"] is True
    assert (nudge["week_start"], nudge["day_count"], nudge["week_label"], nudge["is_current_week"]) == (
        SUNDAY, days, label, True,
    )


@pytest.mark.today(SUNDAY)
def test_planning_the_gap_takes_nothing_from_the_plan_after_it(client, signed_in, monkeypatch):
    """GUARD: the offered gap generates exactly its own days, and the
    approved plan it stops short of keeps all seven."""
    _anchor("saturday")
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    later = _insert_plan("2026-10-28", 7)
    nudge = client.get("/api/week/plan-nudge").json()
    start, days = nudge["week_start"], nudge["day_count"]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: [
        {"date": d, "slot": slot, "meal_name": "Chili", "is_new_recipe": False, "reasoning": "fits"}
        for d in tools.period_dates(start, days) for slot in tools.WEEK_SLOTS
    ])

    plan = agent.generate_weekly_plan(start, day_count=days, period_start=start)

    conn = get_conn()
    row = conn.execute("SELECT * FROM weekly_plans WHERE id = ?", (plan["weekly_plan_id"],)).fetchone()
    later_row = conn.execute("SELECT * FROM weekly_plans WHERE id = ?", (later,)).fetchone()
    conn.close()
    assert tools.plan_period(row) == (SUNDAY, 3)
    assert tools.plan_period(later_row) == ("2026-10-28", 7)
    assert later_row["status"] == "approved"
