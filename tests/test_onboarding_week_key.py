"""
Onboarding must file its first plan under the same week key as everywhere else.

A "week" in this app is not a rolling seven days — it is a specific Monday.
The front end computes the key by subtracting today's weekday from today,
and `get_plan_id_for_week` matches that key exactly. Onboarding was the one
place in the codebase that wrote a different key: it started the week on
whatever day the household happened to sign up.

Nothing errored, which is why it survived. A household onboarding on a
Wednesday got a real plan filed under Wednesday while every screen looked
for Monday, so the Meals tab offered to plan a week it was already showing,
taking that offer produced a second overlapping plan for the same real week,
and chat judged the plan stale and wanted to rebuild it.

The whole existing suite computes Mondays — two helpers even say so in their
docstrings — so every other test assumed the invariant this one line broke.
Nothing asserted it. That is what these tests are for.

Fixed by snapping to Monday (Emily's call, 2026-09-02), accepting that a
household onboarding late in the week sees days that have already passed.
A genuine part-week filed under Monday is real work and has its own ticket.
"""
from __future__ import annotations

import datetime
import types

import pytest

from app import agent, tools


# A Saturday — the worst real case, five days into the week. Pinned so the
# guard below does not depend on which day the suite happens to run: with a
# live clock these tests pass by luck every Monday, which is exactly the
# week the bug is invisible.
FROZEN_TODAY = datetime.date(2026, 9, 5)
FROZEN_MONDAY = "2026-08-31"


def _this_monday() -> str:
    today = datetime.date.today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


@pytest.fixture
def stub_model(monkeypatch):
    """
    Return a fixed week from the model, dated against whatever week start
    the route chooses — so the test measures the KEY the route writes, not
    the dates the stub happens to contain.
    """
    monkeypatch.setattr(
        agent,
        "generate_weekly_plan_llm",
        lambda ctx: [
            {"date": _this_monday(), "slot": "dinner", "meal_name": "Chili", "food_groups": []}
        ],
    )


def test_onboarding_files_its_first_plan_under_this_weeks_monday(signed_in, stub_model):
    """The property, stated directly: the key written is a Monday."""
    res = signed_in.post("/api/onboarding/generate-first-plan")
    assert res.status_code == 200

    row = tools.get_weekly_plan(res.json()["weekly_plan_id"])
    week_start = row["week_start_date"]

    assert week_start == _this_monday(), (
        f"onboarding filed its first plan under {week_start}, but the rest of "
        f"the app keys this week to {_this_monday()}"
    )
    assert datetime.date.fromisoformat(week_start).weekday() == 0, "must be a Monday"


def test_the_screens_can_actually_find_the_plan_onboarding_just_made(signed_in, stub_model):
    """
    The consequence, not the mechanism.

    `get_plan_id_for_week` is the exact-match lookup the week screen drives.
    Asked for this week's Monday and handed a Wednesday-keyed plan, it found
    nothing — which is how a household ended up being offered a plan for a
    week that was already planned.
    """
    res = signed_in.post("/api/onboarding/generate-first-plan")
    assert res.status_code == 200
    expected_id = res.json()["weekly_plan_id"]

    found = tools.get_plan_id_for_week(_this_monday())

    assert found == expected_id, (
        "the week screen looks up this week's plan by its Monday key and could "
        "not find the plan onboarding had just created"
    )


def test_chat_and_the_screens_agree_about_the_plan_onboarding_made(signed_in, stub_model):
    """
    The third symptom, closed.

    Chat resolves "this week" by range — a plan whose seven days contain
    today — while the screens match the Monday key exactly. With a
    Wednesday key those two disagreed: chat found a plan, judged it not to
    be "this week's" and wanted to regenerate it, while the screens insisted
    there was nothing there.

    Asserted as the property that matters: both routes land on the SAME
    plan.
    """
    from app.db import get_conn

    res = signed_in.post("/api/onboarding/generate-first-plan")
    assert res.status_code == 200
    plan_id = res.json()["weekly_plan_id"]

    screens_see = tools.get_plan_id_for_week(_this_monday())

    conn = get_conn()
    try:
        chat_sees = tools._current_weekly_plan_row(conn)
    finally:
        conn.close()

    assert screens_see == plan_id, "the screens' exact-key lookup missed it"
    assert chat_sees is not None, "chat's range lookup found no current plan"
    assert chat_sees["id"] == plan_id, (
        "chat and the week screen resolved to different plans for the same "
        "real week — the disagreement that made chat want to rebuild a plan "
        "the screens were already showing"
    )


def test_onboarding_on_a_saturday_still_files_the_plan_under_monday(signed_in, monkeypatch):
    """
    The same property with the clock pinned, so it holds every day.

    The three tests above read the real date, which means that on a Monday
    they pass whether the fix is present or not — and Monday is precisely
    the day the bug cannot be seen. This one freezes onboarding to a
    Saturday, five days into the week, where a "start today" key is most
    obviously wrong.
    """
    from app import main as main_module

    class _FrozenDate(datetime.date):
        @classmethod
        def today(cls):
            return FROZEN_TODAY

    # Only the names main.py actually uses on this path, but kept complete
    # enough that an unrelated datetime call elsewhere in the request would
    # still work rather than raising something confusing.
    monkeypatch.setattr(
        main_module,
        "datetime",
        types.SimpleNamespace(
            date=_FrozenDate,
            timedelta=datetime.timedelta,
            datetime=datetime.datetime,
            timezone=datetime.timezone,
        ),
    )
    monkeypatch.setattr(
        agent,
        "generate_weekly_plan_llm",
        lambda ctx: [
            {"date": FROZEN_MONDAY, "slot": "dinner", "meal_name": "Chili", "food_groups": []}
        ],
    )

    res = signed_in.post("/api/onboarding/generate-first-plan")
    assert res.status_code == 200

    week_start = tools.get_weekly_plan(res.json()["weekly_plan_id"])["week_start_date"]
    assert week_start == FROZEN_MONDAY, (
        f"onboarding on Saturday {FROZEN_TODAY} filed the plan under "
        f"{week_start}; it belongs under {FROZEN_MONDAY}"
    )
