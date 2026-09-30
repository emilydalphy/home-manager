"""
CHARACTERISATION ONLY — what the app does today when a SNACK is marked
away, pinned so that whoever settles the open decision on the Loop Board
card "A snack's attendance is written and then silently dropped from the
week payload" gets a red test the moment they change it, in either
direction.

Nothing here asserts that the current behaviour is RIGHT. Two of these
tests pin something that is arguably wrong, and each says so in its own
docstring. They exist because the card offers Emily two answers and the
reproduction was living in a Notion paragraph rather than in the suite.

Found 2026-09-30 overnight, by driving that card's own reproduction one
step further than the card did.

THE TWO THINGS PINNED

1. The card's own report: a snack attendance row is written and then
   never read back onto the snack cards, while the dinner on the same
   day carries the full set.

2. The one the card did not have: marking a snack away DESTROYS BOTH of
   the day's snacks and the round trip back gives ONE open question
   where TWO planned dishes were. `clear_plan_slot` deletes every row at
   its (plan, date, slot) and `plan_slot_empty` writes one shell; a
   snack slot is the only slot that holds more than one meal. It is the
   third instance of a rule this repo has written down twice — "a slot
   is not a meal" (the 2026-09-13 batch-cook entry), and the reason
   swap_meal_in_plan grew its `old_meal` parameter.

   Bounded, and that was swept rather than assumed: of the fifteen
   clear_plan_slot call sites in app/, the only two that can be handed a
   snack slot without meaning "clear every snack on this day" are the
   two halves of this conversion, slot_needs._settle_slot_empty and
   slot_needs._reopen_away_slot. typed_requests looked like a second
   live door and is not one — week_intake subtracts {"snack"} from the
   meal words a typed sentence may name.

WHEN THE DECISION IS MADE
  * Option 2 (refuse snack attendance): these all go red at the
    set_slot_attendance call, which is the answer — delete them.
  * Option 1 (decorate snacks): test 1 inverts, and tests 2 and 3 want
    the away conversion taught that a snack slot holds several meals.
"""
import datetime

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import attendance, weekly_plan


def _seed():
    """One day, two adults, a dinner and two planned snacks."""
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")
    day = household_today().isoformat()
    plan = tools.create_weekly_plan(day, day_count=1)["weekly_plan_id"]
    tools.plan_meal(day, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(day, "Apple Slices", slot="snack", weekly_plan_id=plan)
    tools.plan_meal(day, "Greek Yogurt", slot="snack", weekly_plan_id=plan)
    return plan, day


def _snack_rows(plan):
    conn = get_conn()
    rows = [
        {k: r[k] for k in r.keys()}
        for r in conn.execute(
            "SELECT id, COALESCE(freeform_meal, '') AS meal, slot_state "
            "FROM meal_plan_entries WHERE weekly_plan_id = ? AND slot = 'snack' "
            "ORDER BY id",
            (plan,),
        ).fetchall()
    ]
    conn.close()
    return rows


def test_a_snacks_attendance_is_written_and_never_read_back():
    """CHARACTERISATION of the card's own report. The snack row is real
    and the snack cards come back with nothing on them, while the dinner
    on the same day carries serves, away_names and the summary line."""
    plan, day = _seed()
    attendance.set_slot_attendance(day, "dinner", present_member_ids=["Emily"])
    attendance.set_slot_attendance(day, "snack", present_member_ids=["Emily"])

    conn = get_conn()
    written = sorted(
        r["slot"] for r in conn.execute(
            "SELECT slot FROM slot_attendance WHERE date = ?", (day,)
        ).fetchall()
    )
    conn.close()
    assert written == ["dinner", "snack"], "both rows are really written"

    menu = weekly_plan.get_week_menu(plan)["days"][0]
    assert menu["dinner"]["serves"] == 1
    assert menu["dinner"]["away_names"] == ["Vineeth"]
    assert menu["dinner"]["attendance_summary"]

    # ...and the snacks get none of it. This is the bug the card reports.
    for snack in menu["snacks"]:
        assert snack.get("serves") is None
        assert snack.get("away_names") is None
        assert snack.get("attendance_summary") is None


def test_marking_a_snack_away_destroys_BOTH_of_the_days_snacks():
    """CHARACTERISATION of the defect the card did not have. Two planned
    snacks go in; one planned_empty shell comes out. Arguably the day
    really has no snacks if nobody is there — but the SECOND dish is
    gone with nothing recording it, which is what the round trip below
    makes expensive."""
    plan, day = _seed()
    assert [r["meal"] for r in _snack_rows(plan)] == ["Apple Slices", "Greek Yogurt"]

    attendance.set_slot_attendance(day, "snack", present_member_ids=[])

    after = _snack_rows(plan)
    assert len(after) == 1, "both rows were deleted and one shell written"
    assert after[0]["meal"] == ""
    assert after[0]["slot_state"] == "planned_empty"


def test_the_round_trip_gives_ONE_question_where_TWO_dishes_were():
    """CHARACTERISATION, and the one with the cost in it. Away and back
    is an ordinary thing to do — the household changed its mind — and it
    leaves the day one snack short for good. Nothing repairs it:
    meal_variety.enforce_snacks_per_day runs at generation only.

    The DINNER round trip beside it is the control. It is the same
    machinery and it is correct there, because a dinner slot holds one
    row — which is what makes this a slot-versus-meal bug rather than
    the away conversion being wrong."""
    plan, day = _seed()
    attendance.set_slot_attendance(day, "snack", present_member_ids=[])
    attendance.set_slot_attendance(day, "snack", present_member_ids=["Emily", "Vineeth"])

    back = _snack_rows(plan)
    assert len(back) == 1, "two planned snacks became one open question"
    assert back[0]["slot_state"] == "open"

    # The control: one dinner in, one open question out. Nothing lost.
    conn = get_conn()
    before = conn.execute(
        "SELECT COUNT(*) AS n FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND slot = 'dinner'", (plan,),
    ).fetchone()["n"]
    conn.close()
    assert before == 1

    attendance.set_slot_attendance(day, "dinner", present_member_ids=[])
    attendance.set_slot_attendance(day, "dinner", present_member_ids=["Emily", "Vineeth"])

    conn = get_conn()
    dinners = [
        {k: r[k] for k in r.keys()}
        for r in conn.execute(
            "SELECT slot_state FROM meal_plan_entries "
            "WHERE weekly_plan_id = ? AND slot = 'dinner'", (plan,),
        ).fetchall()
    ]
    conn.close()
    assert len(dinners) == 1 and dinners[0]["slot_state"] == "open"
