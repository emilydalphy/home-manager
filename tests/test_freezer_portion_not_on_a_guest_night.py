"""
A frozen portion is never planned onto a night with guests.

Defect hunt, 2026-10-22: freezer_portions.choose_nights skipped out / left
/ away / holiday nights but not a guest night, so a portion of "3
servings" (the household's own share from a night off) could be the whole
dinner on the Friday tagged "Hosting guests" for 4 — and a freeform reheat
night buys nothing, so nothing made up the difference.
"""
import datetime

from app import tools
from app.tools import freezer_portions as _fp
from conftest import household_today

PORTION = [{"inventory_item_id": 1, "dish": "Kofte", "quantity": "3 servings"}]


def _dates() -> list[str]:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)
    return [(monday + datetime.timedelta(days=i)).isoformat() for i in range(7)]


def test_the_busy_night_with_guests_is_passed_over_for_the_next_busy_night():
    dates = _dates()
    caps = {dates[2]: 20, dates[4]: 30}
    intake = {"night_tags": {dates[2]: ["rush", "guests"]},
              "guest_counts": {dates[2]: {"adults": 4, "children": 0}}}
    placed, _ = _fp.choose_nights(PORTION, dates, intake, cap_for=lambda d, s: caps.get(d))
    assert [p["on"] for p in placed] == [dates[4]]


def test_a_guest_count_alone_or_attendance_guests_rule_the_night_out():
    dates = _dates()
    placed, _ = _fp.choose_nights(PORTION, dates, {"guest_counts": {dates[0]: {"adults": 0, "children": 2}}})
    assert [p["on"] for p in placed] == [dates[1]]
    placed, _ = _fp.choose_nights(PORTION, dates, None, guest_nights={dates[0], dates[1]})
    assert [p["on"] for p in placed] == [dates[2]]
    # Zero guests is no guests.
    placed, _ = _fp.choose_nights(PORTION, dates, {"guest_counts": {dates[0]: {"adults": 0, "children": 0}}})
    assert [p["on"] for p in placed] == [dates[0]]


def test_guest_nights_reads_attendance_the_planner_hands_over():
    tools.add_member("Emily")
    dates = _dates()
    tools.set_guest_count(dates[3], "dinner", 2)
    assert _fp.guest_nights(dates) == {dates[3]}
