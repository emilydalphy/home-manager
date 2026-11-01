"""
Re-plan's notice names exactly the approved days (Emily, 2026-10-31): on a
Thursday, Re-plan opens Oct 29–Nov 4 over a plan that ends Nov 1, and the
notice said "Oct 29–Nov 4 is already approved" — Nov 2–4 had no plan. The
prefill now carries `approved_label` ("Oct 29–Nov 1") and the notice says
that. The period itself still reaches into next week, as today.
"""
from __future__ import annotations

from app import tools
from app.tools import week_intake as _wi
from test_intake_fixes_2026_09_21b import _insert_plan
from test_same_as_last_week import _extract


def test_the_prefill_names_only_the_approved_days():
    _insert_plan("2026-10-26", 7)  # Mon Oct 26 – Sun Nov 1, approved
    pre = tools.get_week_intake_prefill("2026-10-29", day_count=7)
    assert pre["week_label"] == "Oct 29–Nov 4", "the period still reaches into next week"
    assert pre["approved_overlap"] is True
    assert pre["approved_label"] == "Oct 29–Nov 1"


def test_a_fully_approved_period_names_it_whole_and_none_names_nothing():
    _insert_plan("2026-10-26", 7)
    assert tools.get_week_intake_prefill("2026-10-26", day_count=7)["approved_label"] == "Oct 26–Nov 1"
    _insert_plan("2026-11-09", 7, status="draft")
    assert tools.get_week_intake_prefill("2026-11-09", day_count=7)["approved_label"] == ""


def test_broken_runs_are_said_as_ranges():
    assert _wi._dates_label(["2026-10-29", "2026-10-30", "2026-11-02"]) == "Oct 29–30 and Nov 2"
    assert _wi._dates_label(["2026-10-29"]) == "Oct 29"


def test_the_notice_reads_the_approved_label():
    fetch = _extract("fetchPeriod")
    assert "(data.approved_label || data.week_label) + ' is already approved and on your shopping list. '" in fetch
