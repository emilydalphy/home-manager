"""
Defect hunt 2026-10-24, Today's moves with two adults (branch
overnight/hunt-moves-owners-1024).

1. An adult who said "I've got tonight" and then left the household: the
   generic member-column sweep in household_deletion.remove_member nulled
   their move_owner_overrides row, and a NULL there is "Nobody yet" said on
   purpose — so the adult left behind saw nobody on a cook the rotation
   gives them. The claim now goes with the person, back to the default.
2. Two adults ticking the same dinner at once: times_cooked went up by two
   for one dinner. The depletion was claimed under a lock; the count wasn't.
"""
from __future__ import annotations

import threading
import time

from app import household_deletion, households, tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import move_credits as _move_credits
from app.tools import moves as _moves

from test_move_owner import DAYS, ISO_TODAY, _at, _plan, _recipe


def _beta_two_adults_taking_turns():
    hid = households.create_household("The Beta Testers", "beta-tester-passphrase")
    with tools.use_household(hid):
        ids = {}
        for n in ("Emily", "Vineeth"):
            ids[n] = tools.add_member(n)["member_id"]
            tools.set_member_age_group(n, "adult")
        _recipe()
        _plan()
        tools.set_cooking_role("turns")
    return hid, ids


def _cooks(hid):
    with tools.use_household(hid):
        return {m["date"]: m for d in DAYS for m in _moves.moves_for_day(d, now=_at())
                if m["kind"] == "cook"}


# ---------- 1. a claim leaves with the person who made it ----------

def test_a_claim_by_an_adult_who_left_falls_back_to_the_default():
    """CATCH. Vineeth takes Emily's night, then leaves: Emily's night is
    hers again, not "Nobody yet"."""
    hid, ids = _beta_two_adults_taking_turns()
    emilys_night = next(d for d, m in _cooks(hid).items() if m["owner"] == ids["Emily"])
    move_id = _cooks(hid)[emilys_night]["id"]
    with tools.use_household(hid):
        tools.set_move_owner(move_id, ids["Vineeth"], emilys_night)
    assert _cooks(hid)[emilys_night]["owner_name"] == "Vineeth"

    household_deletion.remove_member(hid, ids["Vineeth"])

    after = _cooks(hid)
    # A rotation of one: every cook is Emily's, the claimed night included.
    assert {m["owner_name"] for m in after.values()} == {"Emily"}
    conn = get_conn()
    left = conn.execute("SELECT COUNT(*) FROM move_owner_overrides WHERE household_id = ?",
                        (hid,)).fetchone()[0]
    conn.close()
    assert left == 0


def test_a_nobody_yet_said_by_the_one_who_stays_survives_the_other_leaving():
    """EDGE. "Nobody yet" (a NULL member_id) is an answer the leaver didn't
    give about themselves; it stays exactly as said."""
    hid, ids = _beta_two_adults_taking_turns()
    night = DAYS[0]
    with tools.use_household(hid):
        tools.set_move_owner(_cooks(hid)[night]["id"], None, night)

    household_deletion.remove_member(hid, ids["Vineeth"])

    assert _cooks(hid)[night]["owner_name"] is None
    assert _cooks(hid)[DAYS[1]]["owner_name"] == "Emily"


# ---------- 2. two phones, one dinner, one count ----------

def _times_cooked() -> int:
    return next(r["times_cooked"] for r in tools.list_recipes() if r["name"] == "Chicken Skewers")


def test_two_adults_ticking_tonights_dinner_at_once_count_it_once(monkeypatch):
    """CATCH. The credit lookup builds a whole cooker view between the
    unlocked read and the write; widened here so the two ticks always both
    read "not done" — which is what two phones did over HTTP (2 of 2)."""
    _recipe()
    _plan()
    entry = next(m for m in _moves.moves_for_day(ISO_TODAY, now=_at()) if m["kind"] == "cook")["entry_id"]
    real = _move_credits.meal_credit

    def slow(*a, **kw):
        out = real(*a, **kw)
        time.sleep(0.3)
        return out

    monkeypatch.setattr(_move_credits, "meal_credit", slow)
    barrier = threading.Barrier(2)
    results, errors = [], []

    def go():
        barrier.wait()
        try:
            results.append(_cooker.check_off_meal(entry, "done"))
        except Exception as exc:  # pragma: no cover - a raise is the failure
            errors.append(exc)

    workers = [threading.Thread(target=go) for _ in range(2)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert not errors, errors
    assert _times_cooked() == 1
    assert sum(1 for r in results if r.get("unchanged")) == 1
    conn = get_conn()
    credits = conn.execute("SELECT COUNT(*) FROM move_credits WHERE meal_plan_entry_id = ?",
                           (entry,)).fetchone()[0]
    conn.close()
    assert credits == 1


def test_tick_untick_tick_still_counts_one_dinner():
    """EDGE. The ordinary toggle path is untouched by the lock."""
    _recipe()
    _plan()
    entry = next(m for m in _moves.moves_for_day(ISO_TODAY, now=_at()) if m["kind"] == "cook")["entry_id"]
    _cooker.check_off_meal(entry, "done")
    _cooker.check_off_meal(entry, "pending")
    assert _times_cooked() == 0
    _cooker.check_off_meal(entry, "done")
    assert _times_cooked() == 1
    assert _cooker.check_off_meal(entry, "done").get("unchanged") is True
    assert _times_cooked() == 1
