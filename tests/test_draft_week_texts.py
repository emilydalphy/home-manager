"""
A draft week's texts ask for approval and name no dish (Emily, 2026-10-31).

With this week only a draft — never approved, so nothing was added to the
list — the 18:00 nudge still said "Tonight: Sheet-Pan Salmon — 30 min. Tap
to start." and the morning text named the draft's dinners. Now both say one
line, "This week’s plan is still a draft — approve it?", linking to Plan.
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import digest
from conftest import household_today
from test_evening_nudge import _adults, _seed_dinner

pytestmark = pytest.mark.draft_week_texts

TODAY = household_today()
LINK = "https://pomona.example"
DISH = "Sheet-Pan Salmon"


@pytest.fixture(autouse=True)
def _link(monkeypatch):
    monkeypatch.setenv("HOME_MANAGER_URL", LINK)


def _at(h, m=0):
    return datetime.combine(TODAY, dt.time(h, m))


def _approve(plan_id):
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()


def test_the_morning_text_about_a_draft_is_one_line_with_no_dish():
    """CATCH."""
    _adults("Emily")
    _seed_dinner(name=DISH)
    text = tools.build_morning_text(_at(7))
    assert text == f"{digest.DRAFT_WEEK_LINE} {LINK}/week"
    assert DISH not in text


def test_the_evening_nudge_about_a_draft_is_the_same_line():
    """CATCH: the card's own 18:00 case."""
    _adults("Emily")
    _seed_dinner(name=DISH)
    text = tools.build_evening_nudge(_at(17))
    assert text == f"{digest.DRAFT_WEEK_LINE} {LINK}/week"
    assert "Tap to start" not in text and DISH not in text


def test_push_bodies_carry_no_address():
    _adults("Emily")
    _seed_dinner(name=DISH)
    assert tools.build_morning_text(_at(7), link=False) == digest.DRAFT_WEEK_LINE
    assert tools.build_evening_nudge(_at(17), link=False) == digest.DRAFT_WEEK_LINE


def test_the_preview_shows_what_would_be_sent():
    _adults("Emily")
    _seed_dinner(name=DISH)
    out = tools.morning_text_preview(_at(7))
    assert out["text"] == f"{digest.DRAFT_WEEK_LINE} {LINK}/week"
    assert out["lines"] == [digest.DRAFT_WEEK_LINE]
    assert out["would_send"] is True


def test_once_approved_the_texts_name_the_dinner_again():
    """GUARD: approval is what turns the dish back on."""
    _adults("Emily")
    _approve(_seed_dinner(name=DISH))
    assert DISH in tools.build_morning_text(_at(7))
    assert tools.build_evening_nudge(_at(17)).startswith(f"Tonight: {DISH}")


def test_a_draft_never_adds_a_nudge_the_household_was_not_getting():
    """GUARD: a draft night nobody's cooking — dinner already ticked —
    stays silent rather than asking for approval at six."""
    _adults("Emily")
    _seed_dinner(name=DISH)
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE date = ? AND slot = 'dinner'",
                 (TODAY.isoformat(),))
    conn.commit()
    conn.close()
    assert tools.build_evening_nudge(_at(17)) is None
