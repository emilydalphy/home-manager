"""
Plan tab labeling a confirmed leftovers chain (Loop Board: "Korean Beef
Bulgogi Lettuce Wraps · 35 min · Cook this" on a night that only reheats).

get_week_menu's build_slot only ever looked at the entry's own freeform
text to decide whether a slot was a reheat — "leftover"/"leftovers" in the
words the household or the model typed. A confirmed chain entry
(leftovers.plan_leftover_chains — see test_leftovers_batch.py, which
exercises the same chain shape for the Cook view) can carry a REAL
recipe_id instead, naming the source's own dish so the reheat night can
say what it's actually eating, with nothing in its freeform text for that
regex to catch. So a validated reheat night rendered exactly like an
ordinary cook: the dish's own name, its own prep+cook time chip, nothing
marking it as a reheat.

These tests pin down build_slot reading the SAME chain leftovers.py
already gives the Cook view, so the Plan (Week) tab and Cook screen agree
on which nights are reheats.
"""
import datetime

from app import tools

TIMESTAMP_HH = "%Y-%m-%d"


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


TUE, THU = _day(1), _day(3)


def _household():
    for n in ("Alex", "Sam", "Rae"):
        tools.add_member(n)


def _wraps():
    tools.add_recipe(
        "Bulgogi Wraps",
        ingredients=[{"item": "beef", "qty": "1 lb"}],
        default_servings=3,
        prep_time_minutes=15,
        cook_time_minutes=20,
    )


def _chain():
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(TUE, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)
    # Same real-recipe-on-both-nights shape the generator produces: the
    # reheat night names the SAME dish by recipe_id, not freeform
    # "leftovers" text — this is exactly the shape the freeform heuristic
    # can't see.
    tools.plan_meal(
        THU, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
        derived_from={"links_to": f"{TUE}:dinner"},
    )
    tools.repair_leftover_chains(plan_id)
    return plan_id


def _dinner(menu, day):
    by_date = {d["date"]: d for d in menu["days"]}
    return by_date[day]["dinner"]


def test_a_confirmed_chains_reheat_night_is_labeled_leftovers_not_a_cook():
    _household()
    _wraps()
    plan_id = _chain()

    thursday = _dinner(tools.get_week_menu(plan_id), THU)

    assert thursday["source"] == "leftovers"
    assert thursday["meta"] == "reheat"
    assert thursday["title"] == "Leftovers — Tuesday’s Bulgogi Wraps"
    # Nothing was cooked tonight, so no time chip and no "this app assembled
    # your plate" note either — same rule the freeform-text reheat case
    # already followed (build_slot's plate_note override).
    assert thursday["plate_note"] == ""


def test_the_cook_night_of_a_confirmed_chain_is_unaffected():
    _household()
    _wraps()
    plan_id = _chain()

    tuesday = _dinner(tools.get_week_menu(plan_id), TUE)

    assert tuesday["source"] == "plan"
    assert tuesday["title"] == "Bulgogi Wraps"
    assert tuesday["meta"] == "35 min"


def test_an_unvalidated_chain_claim_is_left_exactly_as_written():
    """
    A links_to that repair_leftover_chains never confirmed (nothing ran it,
    or it rejected the pairing) is only a claim — same rule
    leftovers.plan_leftover_chains itself applies everywhere else it's
    read. build_slot must not act on an unconfirmed pairing: the night
    still renders as whatever its own text/recipe says, exactly as before
    this change existed.
    """
    _household()
    _wraps()
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(TUE, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(
        THU, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
        derived_from={"links_to": f"{TUE}:dinner"},
    )
    # repair_leftover_chains deliberately never called.

    thursday = _dinner(tools.get_week_menu(plan_id), THU)

    assert thursday["source"] == "plan"
    assert thursday["title"] == "Bulgogi Wraps"


def test_the_freeform_text_reheat_case_still_works_without_a_chain():
    """The pre-existing path (a household typing "leftovers" with no real
    chain behind it) must keep working unchanged."""
    _household()
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(TUE, "Tuesday's leftovers", slot="dinner", weekly_plan_id=plan_id)

    tuesday = _dinner(tools.get_week_menu(plan_id), TUE)

    assert tuesday["source"] == "leftovers"
    assert tuesday["meta"] == "reheat"
    assert tuesday["title"] == "Tuesday's leftovers"
