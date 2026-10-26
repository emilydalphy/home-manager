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
Now it runs for the current one too; the one uncovered night is tonight's
dinner card's question ("Tonight needs a dinner"), which is untouched.

The dock button reads the same payload (shell.js todayNeedsPlan:
`nudge.week_start`), so `week_start: None` is what keeps it away.
"""
import pytest

from app import tools
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
