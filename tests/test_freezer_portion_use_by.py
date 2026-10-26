"""
A frozen portion is never planned past its use-by (hunt-leftovers-1024,
2026-10-24).

A night off's portion row carries its own use-by (tonight._freeze_portion:
the cook date + FROZEN_COOKED_KEEPS_DAYS). Nothing that plans the portion
read it. Measured on a throwaway DB: a portion a month past its use-by was
the next draft's "You froze a portion of Old Chili — this is the night to
eat it.", and the use-soon note called it "worth eating soon".

Every test says whether it is a CATCH (red on main) or a GUARD.
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import freezer_portions as _fp

from test_freezer_portion_planned import (  # noqa: F401  (fixtures used by name)
    _a_frozen_portion, _dinners, _draft_next_week, _monday, household, stub_model,
)


def _use_by(item_id: int, days_from_today: int) -> str:
    use_by = (household_today() + datetime.timedelta(days=days_from_today)).isoformat()
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET expiration_date = ? WHERE id = ?", (use_by, item_id))
    conn.commit()
    conn.close()
    return use_by


def test_a_portion_past_its_use_by_is_not_waiting_to_be_planned(household):
    """CATCH — it was offered like any other."""
    item_id = _a_frozen_portion("Kofte")
    _use_by(item_id, -1)
    assert _fp.portions_to_plan() == []


def test_a_portion_still_inside_its_use_by_is_waiting(household):
    """GUARD — the use-by day itself still counts as good."""
    item_id = _a_frozen_portion("Kofte")
    _use_by(item_id, 0)
    assert [p["dish"] for p in _fp.portions_to_plan()] == ["Kofte"]


def test_the_night_chosen_is_inside_the_use_by_even_when_a_later_night_is_busier():
    """CATCH — the tightest-cap night won whatever the portion's use-by."""
    dates = tools._week_dates(_monday(1))
    portion = {"dish": "Kofte", "inventory_item_id": 7, "expiration_date": dates[1]}
    caps = {dates[4]: 20}
    placed, missed = _fp.choose_nights(
        [portion], dates, {}, cap_for=lambda d, s: caps.get(d),
    )
    assert [p["on"] for p in placed] == [dates[0]] and missed == []


def test_a_portion_that_keeps_through_none_of_the_week_is_left_out():
    """CATCH — placed on the first night of a week it had already expired by."""
    dates = tools._week_dates(_monday(1))
    portion = {"dish": "Kofte", "inventory_item_id": 7,
               "expiration_date": (datetime.date.fromisoformat(dates[0]) - datetime.timedelta(days=1)).isoformat()}
    placed, missed = _fp.choose_nights([portion], dates, {})
    assert placed == [] and [m["dish"] for m in missed] == ["Kofte"]


def test_a_drafted_week_does_not_plan_an_expired_portion(household, stub_model):
    """CATCH, through generation with the model stubbed: no reheat night,
    no "this is the night to eat it"."""
    item_id = _a_frozen_portion("Kofte")
    _use_by(item_id, -3)
    out = _draft_next_week(stub_model, rush=tools._week_dates(_monday(1))[2])
    assert not any("from the freezer" in m for m in _dinners(out["plan_id"]).values())


def test_no_use_soon_note_says_to_eat_an_expired_portion(household):
    """CATCH — "has been in the freezer 17 weeks — worth eating soon"."""
    item_id = _a_frozen_portion("Kofte", frozen_on=(household_today() - datetime.timedelta(days=120)).isoformat())
    _use_by(item_id, -30)
    assert _fp.sweep_use_soon() == []
    conn = get_conn()
    pending = conn.execute(
        "SELECT summary FROM attention_items WHERE status = 'pending' AND summary LIKE '%worth eating soon%'"
    ).fetchall()
    conn.close()
    assert pending == []


def test_a_use_soon_note_already_said_settles_once_the_portion_expires(household):
    """CATCH — the note said in week four stayed pending after the use-by."""
    item_id = _a_frozen_portion("Kofte", frozen_on=(household_today() - datetime.timedelta(days=40)).isoformat())
    assert [q["dish"] for q in _fp.sweep_use_soon()] == ["Kofte"]
    _use_by(item_id, -1)
    _fp.sweep_use_soon()
    conn = get_conn()
    status = conn.execute(
        "SELECT status FROM attention_items WHERE summary LIKE 'Kofte has been in the freezer%'"
    ).fetchone()["status"]
    conn.close()
    assert status == "resolved"
