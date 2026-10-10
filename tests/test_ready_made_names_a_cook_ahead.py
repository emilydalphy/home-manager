"""
"First one back" names a dinner still to cook (overnight hunt, 2026-10-10).

A trip's first meal back is a `ready_made` slot need, and
slot_needs._recommend_ready_made offers "I'll set aside a double batch of
<the latest dinner before it> — sound good?". The latest dinner before it,
full stop — so, reproduced against a running app on a Saturday:

- away Saturday breakfast and lunch: Saturday dinner's card asked to double
  FRIDAY's tacos, eaten the night before;
- away Monday after a Saturday roast whose leftovers are Sunday's dinner:
  Monday's card asked to double "Sunday's roast chicken" — Sunday is the
  double batch, a reheat, not a cook.

The rule now (slot_needs._batch_candidate): the latest dinner before the
meal that is still to cook — today or later on the HOUSEHOLD's clock, not
ticked cooked, and a real cook (weekly_plan._cooks_that_night). With none,
there is no suggestion, which the card already says honestly.

Every test is a CATCH — red on origin/main 1328b1a — unless its docstring
says GUARD.
"""
from __future__ import annotations

import datetime

from conftest import household_pin

from app import tools
from app.db import get_conn
from app.tools import slot_needs

# A fixed week: the bug is about which of its days have gone by, so the day
# it is read on is the thing under test.
MON = datetime.date(2026, 10, 5)
DAYS = [(MON + datetime.timedelta(days=i)).isoformat() for i in range(7)]
FRI, SAT, SUN = DAYS[4], DAYS[5], DAYS[6]
NEXT_MON = (MON + datetime.timedelta(days=7)).isoformat()


def _week(*, roast_feeds_sunday: bool = False) -> dict[str, int]:
    """A dinner every night, tacos Friday, roast Saturday; Sunday is either
    its own pasta or the roast's leftovers. Returns {date: dinner entry id}."""
    for name in ("Pasta", "Tacos", "Roast Chicken"):
        tools.add_recipe(name, ingredients=[{"item": f"Main for {name}", "qty": "1"}], default_servings=2)
    plan_id = tools.create_weekly_plan(DAYS[0])["weekly_plan_id"]
    ids = {}
    for day in DAYS[:4]:
        ids[day] = tools.plan_meal(day, "Pasta", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    ids[FRI] = tools.plan_meal(FRI, "Tacos", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    ids[SAT] = tools.plan_meal(SAT, "Roast Chicken", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    if roast_feeds_sunday:
        ids[SUN] = tools.plan_meal(SUN, "Roast Chicken", slot="dinner", weekly_plan_id=plan_id,
                                   derived_from={"links_to": f"{SAT}:dinner"})["entry_id"]
        confirmed = tools.repair_leftover_chains(plan_id)["confirmed"]
        assert confirmed, "the fixture's chain is a real one"
    else:
        ids[SUN] = tools.plan_meal(SUN, "Pasta", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")
    return ids


def _on(frozen_today, day: str, hour: int = 9, minute: int = 0) -> None:
    frozen_today(household_pin(hour, minute, on=datetime.date.fromisoformat(day)))


def test_a_dinner_already_gone_by_is_not_offered_to_double(frozen_today):
    """The reported shape: on Saturday, away for breakfast and lunch."""
    _on(frozen_today, SAT)
    _week()

    out = slot_needs.set_away_stretch(SAT, "breakfast", SAT, "lunch")

    assert out["ready_made_slot"] == {"date": SAT, "slot": "dinner"}
    need = slot_needs.get_slot_need(SAT, "dinner")
    assert need["need"] == "ready_made", "the first meal back is still marked"
    assert need["recommended_batch_from_entry_id"] is None, "Friday's tacos were last night"
    assert slot_needs.describe_ready_made(SAT, "dinner") is None


def test_the_same_trip_read_earlier_in_the_week_still_offers_friday(frozen_today):
    """GUARD — green on both sides. Set up on Thursday, Friday is still ahead."""
    ids = _week()
    _on(frozen_today, DAYS[3])

    slot_needs.set_away_stretch(SAT, "breakfast", SAT, "lunch")

    assert slot_needs.get_slot_need(SAT, "dinner")["recommended_batch_from_entry_id"] == ids[FRI]
    assert "Friday’s tacos" in slot_needs.describe_ready_made(SAT, "dinner")["sentence"]


def test_a_reheat_night_is_not_offered_as_a_cook_to_double(frozen_today):
    """Away Monday breakfast and lunch; Sunday is the roast's leftovers. The
    cook to double is Saturday's roast — tonight's, still to cook."""
    _on(frozen_today, SAT)
    ids = _week(roast_feeds_sunday=True)

    slot_needs.set_away_stretch(NEXT_MON, "breakfast", NEXT_MON, "lunch")

    need = slot_needs.get_slot_need(NEXT_MON, "dinner")
    assert need["recommended_batch_from_entry_id"] != ids[SUN], "Sunday is a reheat"
    assert need["recommended_batch_from_entry_id"] == ids[SAT]


def test_a_dinner_already_ticked_cooked_is_not_offered(frozen_today):
    """Tonight's roast, ticked cooked before the trip is entered: it was
    cooked single, and there is nothing left before Monday to double."""
    _on(frozen_today, SAT, 20)
    ids = _week(roast_feeds_sunday=True)
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?", (ids[SAT],))
    conn.commit()
    conn.close()

    slot_needs.set_away_stretch(NEXT_MON, "breakfast", NEXT_MON, "lunch")

    assert slot_needs.get_slot_need(NEXT_MON, "dinner")["recommended_batch_from_entry_id"] is None


def test_late_on_saturday_tonight_is_still_saturday(frozen_today):
    """The clock. 11:30pm in Toronto is already Sunday on a UTC server: read
    off the server's date, Saturday's roast would count as gone by. The
    household's clock still says it is tonight's. (Red on main for the
    reheat reason above; this is the test that fails if the rule is ever
    read off date.today() instead — on a process not itself in Toronto.)"""
    _on(frozen_today, SAT, 23, 30)
    ids = _week(roast_feeds_sunday=True)

    slot_needs.set_away_stretch(NEXT_MON, "breakfast", NEXT_MON, "lunch")

    assert slot_needs.get_slot_need(NEXT_MON, "dinner")["recommended_batch_from_entry_id"] == ids[SAT]


def test_a_suggestion_stored_on_thursday_is_not_asked_on_saturday(frozen_today):
    """Review, 2026-10-10. The trip is entered on Thursday, when Friday's
    tacos are rightly the suggestion; on Saturday they are eaten. Unconfirmed,
    the card asks nothing rather than repeating the reported sentence."""
    ids = _week()
    _on(frozen_today, DAYS[3])
    slot_needs.set_away_stretch(SAT, "breakfast", SAT, "lunch")
    assert slot_needs.get_slot_need(SAT, "dinner")["recommended_batch_from_entry_id"] == ids[FRI]

    _on(frozen_today, SAT)

    assert slot_needs.describe_ready_made(SAT, "dinner") is None


def test_a_confirmed_suggestion_is_kept_after_its_night(frozen_today):
    """GUARD — green on both sides. Confirmed on Thursday, so Friday was
    cooked double on purpose: Saturday still says it is settled."""
    _week()
    _on(frozen_today, DAYS[3])
    slot_needs.set_away_stretch(SAT, "breakfast", SAT, "lunch")
    slot_needs.confirm_slot_recommendation(SAT, "dinner")

    _on(frozen_today, SAT)

    described = slot_needs.describe_ready_made(SAT, "dinner")
    assert described["confirmed"] and described["label"] == "Friday’s tacos"


def test_written_in_takeout_is_not_a_cook_to_double(frozen_today):
    """Friday is "Takeout pizza", no recipe: the suggestion skips it for
    Thursday's pasta, the Plan screen's own takeout check."""
    _on(frozen_today, DAYS[2])
    ids = _week()
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET recipe_id = NULL, freeform_meal = 'Takeout pizza' WHERE id = ?",
                 (ids[FRI],))
    conn.commit()
    conn.close()

    slot_needs.set_away_stretch(SAT, "breakfast", SAT, "lunch")

    assert slot_needs.get_slot_need(SAT, "dinner")["recommended_batch_from_entry_id"] == ids[DAYS[3]]
