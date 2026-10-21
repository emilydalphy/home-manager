"""
Solo-night taste learning reads the COOK, never a reheat of it (defect
hunt 2026-10-21).

mark_recipe_feedback credits a rating to one person when the last time
the dish was made only that person was home. "Last time" was the newest
ticked row for the recipe — and a leftover lunch is a ticked row for the
same recipe. So the dinner both adults ate Monday, plus Tuesday's leftover
lunch that only Emily was home for, turned the household's "Not a hit"
into Emily's own verdict. A reheat is skipped by the rule the cook counter
already uses (cooker._is_leftovers_night).
"""
from __future__ import annotations

from app import tools
from app.db import get_conn
from conftest import household_date


def _couple_and_chili():
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    tools.add_recipe("Chili", ingredients=[{"item": "beef", "qty": "1 lb"}])


def _per_person_rows() -> list[dict]:
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT m.name, mrf.rating, mrf.source FROM member_recipe_feedback mrf "
            "JOIN members m ON m.id = mrf.member_id"
        )]
    finally:
        conn.close()


def _leftover_lunch(cook_date: str, lunch_date: str, away: str | None) -> int:
    if away:
        tools.set_member_attendance(lunch_date, "lunch", away, present=False)
    return tools.plan_meal(
        lunch_date, "Chili", slot="lunch", derived_from={"links_to": f"{cook_date}:dinner"},
    )["entry_id"]


def test_a_shared_dinner_is_not_credited_to_whoever_ate_its_leftovers_alone(signed_in):
    """The "How did it go?" card's Not a hit, through the real route."""
    _couple_and_chili()
    mon, tue = household_date(-2), household_date(-1)
    dinner = tools.plan_meal(mon, "Chili", slot="dinner")["entry_id"]
    lunch = _leftover_lunch(mon, tue, away="Vineeth")
    tools.check_off_meal(dinner)
    tools.check_off_meal(lunch)

    res = signed_in.post("/api/recipe-feedback", json={"recipe_name": "Chili", "rating": "disliked"})

    assert res.status_code == 200, res.text
    assert res.json()["solo_auto_attribution"] is None
    assert _per_person_rows() == [], "both ate the dinner — the verdict is the household's"
    assert res.json()["rating"] == "disliked"


def test_a_solo_dinner_still_teaches_its_cook_after_a_shared_reheat():
    """Emily alone Monday, everyone on the leftovers Tuesday: still Emily's."""
    _couple_and_chili()
    mon, tue = household_date(-2), household_date(-1)
    tools.set_member_attendance(mon, "dinner", "Vineeth", present=False)
    dinner = tools.plan_meal(mon, "Chili", slot="dinner")["entry_id"]
    lunch = _leftover_lunch(mon, tue, away=None)
    tools.check_off_meal(dinner)
    tools.check_off_meal(lunch)

    result = tools.mark_recipe_feedback("Chili", rating="liked")

    assert result["solo_auto_attribution"] == "Emily"
    assert _per_person_rows() == [{"name": "Emily", "rating": "liked", "source": "solo_auto"}]


def test_a_ticked_reheat_with_no_ticked_cook_teaches_nobody():
    _couple_and_chili()
    mon, tue = household_date(-2), household_date(-1)
    tools.plan_meal(mon, "Chili", slot="dinner")
    tools.check_off_meal(_leftover_lunch(mon, tue, away="Vineeth"))

    assert tools.mark_recipe_feedback("Chili", rating="liked")["solo_auto_attribution"] is None
    assert _per_person_rows() == []


def test_a_reheat_never_reaches_back_to_an_older_solo_cook():
    # Review find: with this week's shared dinner unticked and only Emily's
    # leftover lunch ticked, skipping the reheat walked on to a solo bowl
    # Vineeth had three weeks ago and made "Not a hit" his. The reheat
    # stands for its own cook — shared — so nobody is credited.
    _couple_and_chili()
    old, mon, tue = household_date(-20), household_date(-2), household_date(-1)
    tools.set_member_attendance(old, "dinner", "Emily", present=False)
    tools.check_off_meal(tools.plan_meal(old, "Chili", slot="dinner")["entry_id"])
    tools.plan_meal(mon, "Chili", slot="dinner")
    tools.check_off_meal(_leftover_lunch(mon, tue, away="Vineeth"))
    result = tools.mark_recipe_feedback("Chili", rating="disliked")
    assert result["solo_auto_attribution"] is None
    assert _per_person_rows() == []
