"""
A meal is planned onto one of the four meals of a day, and nothing else.

The fifth instance of a family this repo has now fixed four times —
cooker.InvalidMealStatus (2026-09-16), chores.InvalidChoreStatus,
grocery.InvalidGroceryStatus, attention.InvalidAttentionStatus — and the
widest of the five. A bad STATUS leaves a row nothing reads. A bad SLOT
leaves a row one tab acts on and another cannot see.

MEASURED ON MAIN, on a throwaway database, before the guard:
plan_meal(today, "Brunch Hash", "brunch") was accepted and wrote a
`planned` row carrying a real recipe_id. The Cook view showed it as a card,
Today's moves showed it as a cook — so the household was TOLD TO COOK IT —
and the Plan tab did not, because get_week_menu only builds the four slots
a day has. audit_plan_slots reported `present: 1` for a day holding two
planned rows. Meanwhile attendance.set_slot_attendance and
slot_needs.set_slot_need both refused the same word, so that meal could
never be marked away or capped.

Real enough to be cooked; not real enough to be planned, changed, or said
no to.

WHAT IS RED ON MAIN, measured: 5 of 13, and only ONE of those five is a
behaviour catch (test_the_row_used_to_show_on_cook_and_not_on_plan, written
to be one). The other four die on the missing name, which is the only kind
of red a test of a new symbol can have. The evidence for the rest is
mutation, and each says which mutation pins it.

HOW REACHABLE, honestly: no HTTP route can get here. Every route that takes
a slot off the wire either validates it itself (resolve_week_slot answers
400) or hands it to a function that already does (set_week_attendance,
confirm_slot_recommendation). The one door is the chat tool
plan_meal_for_chat, whose schema DOES enumerate the four — which is exactly
the cooked tick's situation, and that card's own reasoning applies: telling
the generator something is not the same as preventing it. That is also why
this branch adds NO route handler: an `except InvalidSlot` on a route
nothing can reach would be dead code, and this repo has already recorded
one piece of unpinnable belt-and-braces it wishes it had not written.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import cooker, moves, weekly_plan
from conftest import household_today


def _a_plan():
    today = household_today()
    mon = today - datetime.timedelta(days=today.weekday())
    tools.set_member_age_group("Emily", "adult")
    tools.add_recipe(
        "Turkey Chili",
        ingredients=[{"item": "Ground turkey", "qty": "1.5 lbs", "category": "meat"}],
        instructions=["Brown it."], prep_time_minutes=10, cook_time_minutes=25,
    )
    tools.add_recipe(
        "Brunch Hash",
        ingredients=[{"item": "Potatoes", "qty": "2 lbs", "category": "produce"}],
        instructions=["Fry it."], prep_time_minutes=10, cook_time_minutes=20,
    )
    plan = tools.create_weekly_plan(mon.isoformat())
    return plan["weekly_plan_id"], today.isoformat()


def _slots_on(day):
    conn = get_conn()
    rows = [r["slot"] for r in conn.execute(
        "SELECT slot FROM meal_plan_entries WHERE date = ? ORDER BY id", (day,))]
    conn.close()
    return rows


# ------------------------------------------------------------------ the bug


def test_a_meal_cannot_be_planned_onto_a_slot_a_day_does_not_have():
    """
    Red on main, where this returns a dict and writes the row — but it dies
    on `AttributeError: module 'app.tools' has no attribute 'InvalidSlot'`
    before it reaches its own assertion, so read the redness as a GUARD and
    the mutation as what pins it: delete validate_slot's call in plan_meal
    and this goes red. The second assertion is the one that matters — a refusal that had already written the row would be worse than
    no refusal.
    """
    pid, day = _a_plan()
    tools.plan_meal(day, "Turkey Chili", "dinner", weekly_plan_id=pid)
    with pytest.raises(tools.InvalidSlot):
        tools.plan_meal(day, "Brunch Hash", "brunch", weekly_plan_id=pid)
    assert _slots_on(day) == ["dinner"], "nothing may be written for a slot that cannot exist"


def test_the_row_used_to_show_on_cook_and_not_on_plan():
    """
    THE ONE REAL BEHAVIOUR CATCH in this file, and deliberately written to be
    one. Every other test here reaches for tools.InvalidSlot, which main has
    not got, so on main they die on the name before they reach their own
    assertion — the only kind of red a test of a new symbol can have, and not
    evidence of anything. This one swallows whatever the call does and then
    asks the SCREENS, so on main it runs to completion and fails on the
    disagreement itself:

        assert [('dinner', 'Turkey Chili'), ('brunch', 'Brunch Hash')]
            == [('dinner', 'Turkey Chili')]

    which is the defect as the household meets it — a row the Cook tab acts
    on and the Plan tab cannot see. Written as one test because the
    DISAGREEMENT is the bug; splitting it into three would pin three facts
    and lose the thing that makes them a defect.
    """
    pid, day = _a_plan()
    tools.plan_meal(day, "Turkey Chili", "dinner", weekly_plan_id=pid)
    try:
        tools.plan_meal(day, "Brunch Hash", "brunch", weekly_plan_id=pid)
    except ValueError:
        pass  # the guard, once it exists — the point is what the screens say next

    cook = [(m.get("slot"), m.get("meal")) for m in cooker.get_cooker_view()["meals"]
            if m.get("date") == day]
    assert cook == [("dinner", "Turkey Chili")]
    assert "Brunch Hash" not in str(moves.today_moves()["moves"])
    audit = tools.audit_plan_slots(pid)
    assert audit["present"] == 1, "one planned row, and the audit can see all of it"


@pytest.mark.parametrize("slot", ["breakfast", "lunch", "dinner", "snack"])
def test_every_real_slot_still_works(slot):
    """
    GUARD — green on main, and the thing a too-tight guard breaks. SNACK is
    the one that matters: WEEK_SLOTS is the three-meal 21-slot guarantee and
    a snack is not part of it, so a guard written against WEEK_SLOTS instead
    of DAY_SLOTS would refuse a real snack. Pinned by exactly that mutation.
    """
    pid, day = _a_plan()
    tools.plan_meal(day, "Turkey Chili", slot, weekly_plan_id=pid)
    assert _slots_on(day) == [slot]


def test_the_default_slot_is_dinner_and_is_not_refused():
    """
    GUARD. Both component-mode callers (agent.py's component branch and
    swap_component_in_plan) omit `slot` entirely and take this default, so if
    the guard refused it, component plans would stop being writable. Checked
    rather than assumed — that was the one way this fix could have been unsafe.
    """
    pid, day = _a_plan()
    tools.plan_meal(day, "Turkey Chili", weekly_plan_id=pid)
    assert _slots_on(day) == ["dinner"]


def test_a_component_row_is_still_written():
    """
    GUARD, driving the component shape directly: plan_meal's docstring says
    slot is ignored for a component item, but the column is still written.
    """
    pid, day = _a_plan()
    tools.plan_meal(day, "Turkey Chili", weekly_plan_id=pid, component_category="protein")
    conn = get_conn()
    row = conn.execute(
        "SELECT slot, component_category FROM meal_plan_entries WHERE date = ?", (day,)
    ).fetchone()
    conn.close()
    assert (row["slot"], row["component_category"]) == ("dinner", "protein")


# ------------------------------------------------- the other two writers


@pytest.mark.parametrize("writer", ["plan_slot_open", "plan_slot_empty"])
def test_the_other_two_writers_refuse_it_too(writer):
    """
    Red on main on the missing name. The mutation that pins it is dropping
    validate_slot from that one writer.

    They matter for the same reason plan_meal does: `open` and
    `planned_empty` are slot STATES, and a state recorded against a slot no
    screen builds is a question nobody will ever be asked.
    """
    pid, day = _a_plan()
    fn = getattr(tools, writer)
    with pytest.raises(tools.InvalidSlot):
        fn(pid, day, "brunch", "because")
    assert _slots_on(day) == []


def test_the_guard_runs_before_a_connection_is_opened():
    """
    GUARD, pinned by mutation: move validate_slot below `own_conn = conn is
    None` and this goes red. It matters more here than in the status siblings
    — these two take a CALLER'S connection, often one holding an open write
    transaction, and spending that transaction on a slot that cannot exist is
    worse than opening a connection of your own and leaking it.
    """
    opened = []
    real = weekly_plan.get_conn

    def counting():
        opened.append(1)
        return real()

    weekly_plan.get_conn = counting
    try:
        with pytest.raises(tools.InvalidSlot):
            weekly_plan.plan_slot_open(1, "2026-09-25", "brunch", "because")
    finally:
        weekly_plan.get_conn = real
    assert opened == []


# --------------------------------------------------------------- the rule


def test_the_vocabulary_has_one_source():
    """
    GUARD on the thing that was already right, so a later tidy-up cannot
    quietly break it. attendance and slot_needs each build their own
    _ALL_SLOTS from weekly_plan.WEEK_SLOTS, so all three agree about which
    slots exist by construction. What was duplicated before this branch was
    only the REFUSAL, and what was missing was any refusal on the write side.
    """
    from app.tools import attendance as _att, slot_needs as _needs

    assert tools.DAY_SLOTS == ("breakfast", "lunch", "dinner", "snack")
    assert _att._ALL_SLOTS == tools.DAY_SLOTS
    assert _needs._ALL_SLOTS == tools.DAY_SLOTS


def test_the_chat_tools_schema_still_enumerates_exactly_these_four():
    """
    GUARD, and the reason this guard exists at all: the schema already said
    the four and the function still took a fifth. Two statements of one rule,
    which is this codebase's named recurring bug generator — so if the enum
    is ever widened, the tool starts producing a word the function refuses,
    which is a dead end for the model rather than a garbage row. The better
    failure, and still one somebody should mean to create.
    """
    from app import agent

    schema = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "plan_meal")
    assert sorted(schema["input_schema"]["properties"]["slot"]["enum"]) == sorted(tools.DAY_SLOTS)
