"""
Generating a week from the household's answers.

The model call itself is stubbed throughout — what's under test is the
contract around it: that the intake reaches the generator, that the tags
produce the behaviour the household was promised, and above all that no
slot ever comes out of this missing. "Week generation silently leaves
random meal slots empty" is a real reported bug, and the only reason it
was possible is that nothing downstream ever checked.
"""
import datetime

import pytest

from app import agent, tools


def _week_start(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _full_week(week: str, meal: str = "Chili", **extra) -> list[dict]:
    """A complete, well-behaved model response: all 21 slots planned."""
    return [
        {
            "date": day, "slot": slot, "meal_name": meal, "is_new_recipe": False,
            "reasoning": "fits the week", **extra,
        }
        for day in tools._week_dates(week)
        for slot in tools.WEEK_SLOTS
    ]


@pytest.fixture
def recipe():
    tools.add_recipe(
        "Chili",
        ingredients=[{"item": "beans", "qty": "1 tin"}],
        prep_time_minutes=10, cook_time_minutes=20,
    )
    tools.add_recipe("Toast", ingredients=[{"item": "bread", "qty": "1 loaf"}])


@pytest.fixture
def stub_model(monkeypatch):
    """
    Replace the model call with a canned response, and record the context
    it was handed so tests can assert on what generation was actually told.
    """
    seen = {}

    def _stub(days):
        def _fake(context):
            seen["context"] = context
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", _fake)
        return seen

    return _stub


# ---------- the 21-slot guarantee ----------

def test_a_generated_week_has_every_slot(recipe, stub_model):
    week = _week_start()
    stub_model(_full_week(week))

    plan = agent.generate_weekly_plan(week)

    audit = tools.audit_plan_slots(plan["weekly_plan_id"])
    assert audit["complete"] is True
    assert audit["present"] == 21


def test_slots_the_model_forgets_become_open_questions_not_holes(recipe, stub_model):
    """
    The reported bug, reproduced: the model returns an incomplete week.
    The household must end up with a question, never a blank — and the
    reason must say plainly that the app couldn't settle it rather than
    inventing a constraint it didn't have.
    """
    week = _week_start()
    days = _full_week(week)
    dropped = [d for d in days if not (d["date"] == tools._week_dates(week)[2] and d["slot"] == "dinner")]
    assert len(dropped) == 20
    stub_model(dropped)

    plan = agent.generate_weekly_plan(week)

    audit = tools.audit_plan_slots(plan["weekly_plan_id"])
    assert audit["complete"] is True, "the gap must have been filled, not left"
    assert audit["present"] == 21

    slots = _slots_for(plan["weekly_plan_id"])
    gap = slots[(tools._week_dates(week)[2], "dinner")]
    assert gap["slot_state"] == "open"
    assert "rather ask than guess" in gap["open_reason"]


def test_an_open_slot_the_model_asked_for_keeps_its_reason_and_options(recipe, stub_model):
    week = _week_start()
    wednesday = tools._week_dates(week)[2]
    days = [d for d in _full_week(week) if not (d["date"] == wednesday and d["slot"] == "dinner")]
    days.append({
        "date": wednesday, "slot": "dinner", "meal_name": "", "is_new_recipe": False,
        "reasoning": "", "slot_state": "open",
        "open_reason": "Wednesday I’d rather ask than guess: after Monday’s chili, everything "
                       "I have under 20 minutes repeats something you’ve just eaten.",
        "open_options": [
            {"label": "Breakfast for dinner", "meta": "12 min"},
            {"label": "Chili one more night", "meta": "0 min"},
            {"label": "Takeout, don’t plan it", "meta": ""},
        ],
    })
    stub_model(days)

    plan = agent.generate_weekly_plan(week)

    gap = _slots_for(plan["weekly_plan_id"])[(wednesday, "dinner")]
    assert gap["slot_state"] == "open"
    assert "under 20 minutes" in gap["open_reason"]
    assert len(gap["derived_from"]["options"]) == 3


# ---------- leftovers must eat something that actually happened ----------

def test_a_leftovers_night_pointing_at_the_future_is_reopened_not_saved(recipe, stub_model):
    """
    The reported bug, reproduced directly: an EARLIER date's dinner
    (Monday) is sent as leftovers with links_to pointing at a LATER date's
    dinner (Wednesday) — the exact shape of "the planner scheduled
    Wednesday as leftovers of Thursday's cook." It must never be saved as
    a planned leftovers entry; it must come back open, with the repair
    kept on derived_from rather than silently dropped.
    """
    week = _week_start()
    dates = tools._week_dates(week)
    monday, wednesday = dates[0], dates[2]
    days = [d for d in _full_week(week) if not (d["date"] == monday and d["slot"] == "dinner")]
    days.append({
        "date": monday, "slot": "dinner", "meal_name": "Chili leftovers", "is_new_recipe": False,
        "reasoning": "leftovers", "derived_from": {"links_to": f"{wednesday}:dinner"},
    })
    stub_model(days)

    plan = agent.generate_weekly_plan(week)

    entry = _slots_for(plan["weekly_plan_id"])[(monday, "dinner")]
    assert entry["slot_state"] == "open"
    assert "hasn’t happened yet" in entry["open_reason"]
    assert entry["derived_from"]["repaired"] == "leftovers_backwards"
    assert entry["derived_from"]["original_links_to"] == f"{wednesday}:dinner"
    assert "links_to" not in entry["derived_from"]
    assert tools.audit_plan_slots(plan["weekly_plan_id"])["complete"] is True


def test_a_valid_leftovers_chain_is_left_intact_and_the_cook_night_gets_a_note(recipe, stub_model):
    week = _week_start()
    dates = tools._week_dates(week)
    tuesday, wednesday = dates[1], dates[2]
    days = [d for d in _full_week(week) if not (d["date"] == wednesday and d["slot"] == "dinner")]
    days.append({
        "date": wednesday, "slot": "dinner", "meal_name": "Chili leftovers", "is_new_recipe": False,
        "reasoning": "leftovers", "derived_from": {"links_to": f"{tuesday}:dinner"},
    })
    stub_model(days)

    plan = agent.generate_weekly_plan(week)

    slots = _slots_for(plan["weekly_plan_id"])
    leftover = slots[(wednesday, "dinner")]
    assert leftover["slot_state"] == "planned"
    assert "repaired" not in leftover["derived_from"]

    source = slots[(tuesday, "dinner")]
    assert "double batch" in source["derived_from"]["make_double_note"]
    assert source["derived_from"]["make_double_for"] == f"{wednesday}:dinner"
    assert tools.audit_plan_slots(plan["weekly_plan_id"])["complete"] is True


def test_a_leftovers_night_pointing_at_nothing_is_reopened(recipe, stub_model):
    week = _week_start()
    dates = tools._week_dates(week)
    wednesday = dates[2]
    days = [d for d in _full_week(week) if not (d["date"] == wednesday and d["slot"] == "dinner")]
    days.append({
        "date": wednesday, "slot": "dinner", "meal_name": "Mystery leftovers", "is_new_recipe": False,
        "reasoning": "leftovers", "derived_from": {"links_to": "2099-01-01:dinner"},
    })
    stub_model(days)

    plan = agent.generate_weekly_plan(week)

    entry = _slots_for(plan["weekly_plan_id"])[(wednesday, "dinner")]
    assert entry["slot_state"] == "open"
    assert entry["derived_from"]["repaired"] == "leftovers_backwards"
    assert "can’t find" in entry["open_reason"]


def test_leftover_repair_resolves_the_entry_id_form(recipe):
    """
    An older design doc used "entry_id:<n>" for links_to before the
    prompt/schema settled on "YYYY-MM-DD:slot" — both forms are accepted.
    Exercised directly against tools.repair_leftover_chains rather than
    through a stubbed generation, since a real entry_id can only exist
    once the source row has actually been written.
    """
    week = _week_start()
    plan = tools.create_weekly_plan(week, content_start_date=week, day_count=7)
    plan_id = plan["weekly_plan_id"]
    dates = tools._week_dates(week)
    tuesday, wednesday = dates[1], dates[2]

    source = tools.plan_meal(tuesday, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(
        wednesday, "Chili leftovers", slot="dinner", weekly_plan_id=plan_id,
        derived_from={"links_to": f"entry_id:{source['entry_id']}"},
    )

    tools.repair_leftover_chains(plan_id)

    slots = _slots_for(plan_id)
    assert slots[(wednesday, "dinner")]["slot_state"] == "planned"
    assert slots[(tuesday, "dinner")]["derived_from"]["make_double_for"] == f"{wednesday}:dinner"


# ---------- the tags do what the household was told they would ----------

def test_a_nobody_home_night_is_planned_empty_and_never_asked_about(recipe, stub_model):
    """
    The one deliberately empty slot in a week. It needs no decision, so it
    must never be offered as one — and nothing for it may reach the
    shopping list.
    """
    week = _week_start()
    friday = tools._week_dates(week)[4]
    intake = tools.save_week_intake(week, night_tags={friday: ["out"]})
    seen = stub_model([d for d in _full_week(week) if not (d["date"] == friday and d["slot"] == "dinner")])

    plan = agent.generate_weekly_plan(week, intake_id=intake["intake_id"])

    # The model was never even told about that dinner — the surest way for
    # it not to be offered as a decision.
    assert seen["context"]["intake"]["skip_dinner_dates"] == [friday]
    assert friday not in seen["context"]["intake"]["night_tags"]

    slot = _slots_for(plan["weekly_plan_id"])[(friday, "dinner")]
    assert slot["slot_state"] == "planned_empty"
    assert slot["slot_state"] != "open", "a night nobody is home is not a question"
    assert "planned nothing and bought nothing" in slot["reasoning"]

    # And approving the week buys nothing for it.
    tools.approve_weekly_plan(plan["weekly_plan_id"], approved_by="Emily")
    assert "beans" in [i["item"] for i in tools.list_grocery_list()]
    links = _grocery_links_for_date(plan["weekly_plan_id"], friday, "dinner")
    assert links == [], "nothing for an out night reaches the shopping list"


def test_an_out_night_buys_nothing_even_when_the_model_plans_one_anyway(recipe, stub_model):
    """
    The failure the original test missed by stubbing the dinner out: the
    model is TOLD not to send a dinner for a night nobody is home, but being
    told is not being prevented. If it sends one, the deliberate empty row
    must REPLACE it, not land beside it — otherwise the slot holds two
    entries and approving buys ingredients for a night the household was
    promised nothing would be bought for.
    """
    week = _week_start()
    friday = tools._week_dates(week)[4]
    intake = tools.save_week_intake(week, night_tags={friday: ["out"]})
    # A disobedient model: a complete week INCLUDING the out night.
    stub_model(_full_week(week))

    plan = agent.generate_weekly_plan(week, intake_id=intake["intake_id"])
    plan_id = plan["weekly_plan_id"]

    audit = tools.audit_plan_slots(plan_id)
    assert audit["duplicated"] == [], "one slot holds exactly one entry"
    assert audit["complete"] is True

    slot = _slots_for(plan_id)[(friday, "dinner")]
    assert slot["slot_state"] == "planned_empty", "the tag wins over the model"

    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert _grocery_links_for_date(plan_id, friday, "dinner") == [], \
        "nothing is bought for a night nobody is home"


def test_a_meal_count_of_zero_buys_nothing_even_when_the_model_plans_one(recipe, stub_model):
    """Same rule, reached the other way: an explicit "none, thanks"."""
    week = _week_start()
    tools.edit_preference("breakfasts_per_week", 0)
    stub_model(_full_week(week))

    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    assert tools.audit_plan_slots(plan_id)["duplicated"] == []
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    for day in tools._week_dates(week):
        assert _grocery_links_for_date(plan_id, day, "breakfast") == []


def test_a_short_week_does_not_invent_questions_about_days_nobody_asked_for(recipe, stub_model):
    """
    generate_weekly_plan takes day_count, and chat can ask for a short week.
    Auditing five requested days against seven produced six spurious "what
    would you prefer?" questions about a Saturday and Sunday nobody asked to
    have planned.
    """
    week = _week_start()
    five_days = tools._week_dates(week)[:5]
    stub_model([d for d in _full_week(week) if d["date"] in five_days])

    plan = agent.generate_weekly_plan(week, day_count=5)

    slots = _slots_for(plan["weekly_plan_id"])
    assert len(slots) == 15
    assert not any(s["slot_state"] == "open" for s in slots.values()), \
        "no questions about days that were never requested"
    assert tools.audit_plan_slots(plan["weekly_plan_id"], day_count=5)["complete"] is True


def test_a_rush_night_reaches_the_generator_as_a_real_cap(recipe, stub_model):
    """
    Whether the model then honours the cap is a prompt matter and can't be
    asserted against a stub. What CAN be asserted, and is worth guarding, is
    that the tag and the number actually reach it — a silently dropped tag
    would look exactly like a model that ignored it.
    """
    week = _week_start()
    wednesday = tools._week_dates(week)[2]
    intake = tools.save_week_intake(week, night_tags={wednesday: ["rush"]})
    seen = stub_model(_full_week(week))

    agent.generate_weekly_plan(week, intake_id=intake["intake_id"])

    assert seen["context"]["intake"]["night_tags"][wednesday] == ["rush"]
    assert tools.RUSH_MAX_MINUTES == 20


def test_packed_lunch_days_reach_the_generator_without_unplanning_lunch(recipe, stub_model):
    """
    Packed-lunch days don't decide WHETHER a lunch is planned — every lunch
    is planned either way. They constrain those days to food that travels
    cold.
    """
    week = _week_start()
    days = tools._week_dates(week)[:3]
    intake = tools.save_week_intake(week, packed_lunch_days=days)
    seen = stub_model(_full_week(week))

    plan = agent.generate_weekly_plan(week, intake_id=intake["intake_id"])

    assert seen["context"]["intake"]["packed_lunch_days"] == days
    slots = _slots_for(plan["weekly_plan_id"])
    assert all(slots[(d, "lunch")]["slot_state"] == "planned" for d in tools._week_dates(week))


def test_the_generator_is_told_the_whole_table_not_just_the_extra_guests(recipe, stub_model):
    """
    The steppers collect extras; portions need the total. Doing that sum
    here rather than in the prompt means the model gets a number instead
    of an arithmetic problem.
    """
    week = _week_start()
    saturday = tools._week_dates(week)[5]
    tools.add_member("Emily"); tools.set_member_age_group("Emily", "Adult")
    tools.add_member("Marcus"); tools.set_member_age_group("Marcus", "Adult")
    tools.add_member("Sam"); tools.set_member_age_group("Sam", "Child")
    intake = tools.save_week_intake(
        week,
        night_tags={saturday: ["guests"]},
        guest_counts={saturday: {"adults": 2, "children": 1}},
    )
    seen = stub_model(_full_week(week))

    agent.generate_weekly_plan(week, intake_id=intake["intake_id"])

    totals = seen["context"]["intake"]["guest_totals"][saturday]
    assert totals == {"adults": 4, "children": 2}, "household of 2+1, plus 2 adults and 1 child"


def test_the_intake_reaches_the_generator_without_being_asked_for(recipe, stub_model):
    """
    A week planned through the question screens and then re-generated from
    chat must still respect what the household said, rather than quietly
    reverting to a blank slate.
    """
    week = _week_start()
    tools.save_week_intake(week, moods=["Comfort food"], cuisines=["Thai"], freeform="Friday is pizza night.")
    seen = stub_model(_full_week(week))

    agent.generate_weekly_plan(week)  # no intake_id passed

    assert seen["context"]["intake"]["moods"] == ["Comfort food"]
    assert seen["context"]["intake"]["cuisines"] == ["Thai"]
    assert seen["context"]["intake"]["freeform"] == "Friday is pizza night."


def test_a_generated_plan_records_which_answers_produced_it(recipe, stub_model):
    week = _week_start()
    intake = tools.save_week_intake(week, moods=["Comfort food"])
    stub_model(_full_week(week))

    plan = agent.generate_weekly_plan(week, intake_id=intake["intake_id"])

    assert tools.get_weekly_plan(plan["weekly_plan_id"])["intake_id"] == intake["intake_id"]


def test_generating_from_an_intake_that_does_not_exist_is_an_error(recipe, stub_model):
    stub_model(_full_week(_week_start()))
    with pytest.raises(ValueError):
        agent.generate_weekly_plan(_week_start(), intake_id=9999)


def test_per_slot_provenance_is_recorded(recipe, stub_model):
    week = _week_start()
    monday = tools._week_dates(week)[0]
    days = _full_week(week)
    for d in days:
        if d["date"] == monday and d["slot"] == "dinner":
            d["derived_from"] = {
                "tags": ["rush"], "constraint": "max_minutes:20",
                "inputs": ["mood:comfort_food"], "freeform": "use the lamb in the freezer",
            }
    stub_model(days)

    plan = agent.generate_weekly_plan(week)

    provenance = _slots_for(plan["weekly_plan_id"])[(monday, "dinner")]["derived_from"]
    assert provenance["constraint"] == "max_minutes:20"
    assert provenance["tags"] == ["rush"]
    assert provenance["freeform"] == "use the lamb in the freezer"


def test_a_meal_count_of_zero_empties_that_slot_all_week(recipe, stub_model):
    """
    "None, thanks" is a valid answer to the setup screen's stepper. Above
    zero, a count is how many DISTINCT meals to plan, never how many days —
    so it never empties anything.
    """
    week = _week_start()
    tools.edit_preference("breakfasts_per_week", 0)
    stub_model(_full_week(week))

    plan = agent.generate_weekly_plan(week)

    slots = _slots_for(plan["weekly_plan_id"])
    breakfasts = [s for (d, sl), s in slots.items() if sl == "breakfast"]
    assert len(breakfasts) == 7
    assert all(b["slot_state"] == "planned_empty" for b in breakfasts)
    # Lunch and dinner are untouched.
    assert all(slots[(d, "dinner")]["slot_state"] == "planned" for d in tools._week_dates(week))
    assert tools.audit_plan_slots(plan["weekly_plan_id"])["complete"] is True


def test_a_draft_week_still_puts_nothing_on_the_grocery_list(recipe, stub_model):
    week = _week_start()
    stub_model(_full_week(week))

    agent.generate_weekly_plan(week)

    assert tools.list_grocery_list() == [], "generating is not a yes; approving is"


# ---------- helpers ----------

def _slots_for(plan_id: int) -> dict:
    import json
    from app.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT date, slot, slot_state, open_reason, reasoning, derived_from_json "
        "FROM meal_plan_entries WHERE weekly_plan_id = ? AND component_category IS NULL",
        (plan_id,),
    ).fetchall()
    conn.close()
    return {
        (r["date"], r["slot"]): {
            "slot_state": r["slot_state"],
            "open_reason": r["open_reason"],
            "reasoning": r["reasoning"],
            "derived_from": json.loads(r["derived_from_json"]),
        }
        for r in rows
    }


def _grocery_links_for_date(plan_id: int, day: str, slot: str) -> list:
    from app.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpgl.item FROM meal_plan_grocery_links mpgl
        JOIN meal_plan_entries mpe ON mpe.id = mpgl.meal_plan_entry_id
        WHERE mpe.weekly_plan_id = ? AND mpe.date = ? AND mpe.slot = ?
        """,
        (plan_id, day, slot),
    ).fetchall()
    conn.close()
    return [r["item"] for r in rows]
