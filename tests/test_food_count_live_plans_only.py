"""
The morning report's FOOD count is DISTINCT violations on LIVE plans.

Two independent bugs, one symptom, both found 2026-09-26 by mining the live
app to size the rush-cap card and noticing the numbers did not survive
deduplication:

- `usage.record_plan_quality` is a bare INSERT with no dedupe, and three
  rules (`_steps_match_ingredients`, `_ingredient_repeat`,
  `_quantities_plausible`) run in BOTH `check_week` at generation and
  `check_recipes_and_log` at approval — so one live plan logs the same
  violation twice, and a re-drafted week logs it once per draft.
- `usage.get_recent_plan_quality` never joined `weekly_plans`, so a RETIRED
  draft's violations counted exactly like the week that was cooked.

Measured on Emily's own 30-day window that morning: 28 distinct violations
in a 50-row sample (1.8x, up to 4x for one violation); `rush_cap_respected`
read 12 and was 5 distinct; `dinner_repeat_in_history` read 19 and was 5.

Every test here says in its own docstring whether it fails on main (CATCH)
or is a no-regression promise (GUARD).
"""
from __future__ import annotations

import datetime
from pathlib import Path

from app import tools
from app.db import get_conn
from app.tools import usage
from app.tools.plan_quality import Violation

REPO = Path(__file__).resolve().parents[1]


def _plan(status: str, start: str | None = None) -> int:
    from conftest import household_today
    start = start or (household_today() - datetime.timedelta(days=2)).isoformat()
    pid = tools.create_weekly_plan(start)["weekly_plan_id"]
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = ? WHERE id = ?", (status, pid))
    conn.commit()
    conn.close()
    return pid


def _v(rule="rush_cap_respected", date="2026-09-23", slot="dinner", message="over the cap", severity="warn"):
    return Violation(rule=rule, severity=severity, date=date, slot=slot, message=message)


# ------------------------------------------------------- a discarded draft

def test_a_retired_drafts_violations_are_not_counted():
    """CATCH — fails on main, which counts every row in the window whatever
    its plan's status. A draft that was re-drafted away is not a week anybody
    cooked, so what it got wrong about the food is not a fact about this
    household's food."""
    usage.record_plan_quality(_plan("retired"), [_v(rule="novelty_floor", message="Nothing new.")])
    q = usage.get_recent_plan_quality(days=7)
    assert q["total"] == 0
    assert q["by_rule"] == {}
    assert q["recent"] == []


def test_a_row_whose_plan_is_gone_is_not_counted():
    """CATCH — fails on main. plan_quality_events.weekly_plan_id carries no
    foreign key, so a plan deleted outright (reset_household.py) leaves
    orphan rows behind. The JOIN drops them, which is the same judgement as
    the retired case: no plan, no week, no fact."""
    pid = _plan("approved")
    usage.record_plan_quality(pid, [_v()])
    conn = get_conn()
    conn.execute("DELETE FROM weekly_plans WHERE id = ?", (pid,))
    conn.commit()
    conn.close()
    assert usage.get_recent_plan_quality(days=7)["total"] == 0


def test_the_live_week_beside_two_discarded_drafts_is_all_that_counts():
    """CATCH — the reported shape, end to end. Three drafts of one week, two
    of them retired, each logging the same two violations, plus one violation
    only the first draft had. Main counts seven; the honest answer is two."""
    live = _plan("approved")
    d1, d2 = _plan("retired"), _plan("retired")
    pair = [_v(), _v(rule="dinner_repeat_in_history", date="2026-09-24", message="a repeat")]
    for pid in (d1, d2, live):
        usage.record_plan_quality(pid, pair)
    usage.record_plan_quality(d1, [_v(rule="novelty_floor", date="2026-09-22", message="Nothing new.")])
    q = usage.get_recent_plan_quality(days=7)
    assert q["total"] == 2, q["by_rule"]
    assert q["by_rule"] == {"dinner_repeat_in_history": 1, "rush_cap_respected": 1}
    assert "novelty_floor" not in q["by_rule"]


# ------------------------------------------------------------- the dedupe

def test_one_live_plan_logging_the_same_violation_twice_counts_once():
    """CATCH — fails on main, and this is the half that bites WITHOUT any
    re-drafting. _steps_match_ingredients, _ingredient_repeat and
    _quantities_plausible each run in check_week at generation AND in
    check_recipes_and_log at approval, over the same live plan."""
    pid = _plan("approved")
    v = _v(rule="steps_match_ingredients", severity="info", date="2026-09-24", slot="lunch",
           message="Garlic Shrimp: step(s) use salt, which isn't on the ingredient list.")
    usage.record_plan_quality(pid, [v])   # generation
    usage.record_plan_quality(pid, [v])   # approval
    q = usage.get_recent_plan_quality(days=7)
    assert q["total"] == 1
    assert q["by_rule"] == {"steps_match_ingredients": 1}
    assert len(q["recent"]) == 1


def test_logged_carries_the_raw_count_beside_the_deduped_one():
    """CATCH — `logged` does not exist on main. The re-draft signal is worth
    keeping ("the same 5 things across 3 drafts"); what it must not do is be
    the headline."""
    pid = _plan("approved")
    v = _v()
    for _ in range(4):
        usage.record_plan_quality(pid, [v])
    q = usage.get_recent_plan_quality(days=7)
    assert q["total"] == 1
    assert q["logged"] == 4


def test_the_count_does_not_go_quietly_to_zero_on_a_genuinely_bad_week():
    """GUARD — the thing a dedupe most easily breaks. Five DIFFERENT
    violations on one live plan still read as five.

    NOT pinned by the "drop `message` from the key" mutation, and an earlier
    version of this docstring claimed it was: these five differ by DATE, so
    that mutation still tells them apart and reddens nothing here. It is
    pinned by the coarser mutation that groups on the rule alone, and
    `message` is pinned by
    test_two_violations_of_one_rule_with_no_date_or_slot_are_two."""
    pid = _plan("approved")
    usage.record_plan_quality(pid, [
        _v(date=f"2026-09-2{n}", message=f"dinner {n} is over the cap") for n in range(1, 6)
    ])
    q = usage.get_recent_plan_quality(days=7)
    assert q["total"] == 5
    assert q["by_rule"] == {"rush_cap_respected": 5}


def test_two_violations_of_one_rule_with_no_date_or_slot_are_two():
    """CATCH — and it is the ONLY thing that pins `message` in the key, which
    matters because the guard above does not: its five differ by DATE, so
    dropping `message` still tells them apart. Measured 2026-09-26: that
    mutation reddened nothing until this test existed, and the docstring
    above wrongly claimed otherwise.

    Reachable rather than contrived: several rules emit with an empty date
    and slot (_ingredient_repeat, _novelty_floor), so two findings of one
    such rule differ in nothing BUT the message."""
    pid = _plan("approved")
    usage.record_plan_quality(pid, [
        _v(rule="ingredient_repeat", date="", slot="", message="peppers in 5 dinners"),
        _v(rule="ingredient_repeat", date="", slot="", message="chicken in 5 dinners"),
    ])
    q = usage.get_recent_plan_quality(days=7)
    assert q["total"] == 2
    assert len(q["recent"]) == 2


def test_recent_shows_every_distinct_violation_of_one_rule_not_just_one():
    """CATCH — pins the BREADTH of `recent`'s key. Measured 2026-09-26:
    grouping `recent` by rule alone reddened nothing until this test, because
    every other test here either uses one rule once or uses two rules. Five
    over-cap dinners must be five lines under the FOOD heading, or the
    examples are less informative than the count above them."""
    pid = _plan("approved")
    usage.record_plan_quality(pid, [
        _v(date=f"2026-09-2{n}", message=f"dinner {n} is over the cap") for n in range(1, 6)
    ])
    recent = usage.get_recent_plan_quality(days=7)["recent"]
    assert len(recent) == 5
    assert sorted(r["date"] for r in recent) == [f"2026-09-2{n}" for n in range(1, 6)]


def test_two_violations_that_differ_only_in_slot_are_two():
    """GUARD — the key includes the slot, so the same rule about the same day
    at lunch and at dinner is two things. Pinned by the mutation that drops
    `slot` from the key."""
    pid = _plan("approved")
    usage.record_plan_quality(pid, [_v(slot="lunch"), _v(slot="dinner")])
    assert usage.get_recent_plan_quality(days=7)["total"] == 2


def test_the_key_separator_cannot_be_forged_from_a_message():
    """GUARD — the key is joined with char(31) (ASCII UNIT SEPARATOR), so no
    message can contain the separator and collide two different violations
    into one. Pinned by the mutation that joins on a printable character."""
    pid = _plan("approved")
    usage.record_plan_quality(pid, [
        _v(date="2026-09-23", slot="", message="a|b"),
        _v(date="2026-09-23", slot="a", message="b"),
    ])
    assert usage.get_recent_plan_quality(days=7)["total"] == 2


# ----------------------------------------------------------- what is shown

def test_recent_keeps_the_newest_row_for_each_distinct_violation():
    """CATCH — on main `recent` repeats one dish three times, so the lines
    under the FOOD heading are less informative than the heading. The
    surviving row is the LATEST time the same thing was said, which is the
    created_at a reader would expect."""
    pid = _plan("approved")
    v = _v(message="the same complaint")
    usage.record_plan_quality(pid, [v])
    first = get_conn()
    first_id = first.execute("SELECT MAX(id) AS i FROM plan_quality_events").fetchone()["i"]
    first.close()
    usage.record_plan_quality(pid, [v])
    q = usage.get_recent_plan_quality(days=7)
    assert len(q["recent"]) == 1
    assert q["recent"][0]["id"] > first_id


def test_the_report_names_the_redraft_count_only_when_it_differs():
    """CATCH — the clause does not exist on main. Two assertions in one
    because they are one rule: say it when the raw count is bigger, and say
    nothing when it is not."""
    src = (REPO / "observability_report.py").read_text(encoding="utf-8")
    assert 'logged {redrafts} times across re-drafts' in src
    assert 'if redrafts > quality["total"]' in src


# --------------------------------------------------------------- isolation

def test_another_households_violations_are_never_counted_here():
    """GUARD — isolation. Pinned by MUTATION rather than by redness: main
    already scopes the events table correctly, so what is new is the JOIN,
    and a join written without `p.household_id = ?` would let another
    household's plan vouch for this household's rows.

    Seeded inside `with tools.use_household(...)`, because a tool called
    straight from a test runs on the ContextVar's default and would
    otherwise write into household 1 — the trap CLAUDE.md records this
    suite falling into before, where the isolation test passed while
    proving nothing because it never crossed the boundary it names."""
    conn = get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (99, 'Other') "
                 "ON CONFLICT(id) DO NOTHING")
    conn.commit()
    conn.close()

    with tools.use_household(99):
        usage.record_plan_quality(_plan("approved"), [_v(message="their week")])

    usage.record_plan_quality(_plan("approved"), [_v(message="my week")])

    q = usage.get_recent_plan_quality(days=7)
    assert q["total"] == 1, q["recent"]
    assert q["recent"][0]["message"] == "my week"

    # And the seed really did cross the boundary — without this the test
    # above passes on a broken app because nothing of household 99's exists.
    with tools.use_household(99):
        theirs = usage.get_recent_plan_quality(days=7)
    assert [r["message"] for r in theirs["recent"]] == ["their week"]
