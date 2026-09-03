"""
Per-person, per-meal attendance — Emily's deepened week-planning model
(Loop Board "Week planning: away-stretches and per-meal needs", the "Model
deepened" section, 2026-09-03).

The invariant under test throughout: attendance is the atomic fact, and
"away" is what an EMPTY attendance means rather than a separate flag. The
generation-level tests stub the model, same convention as
test_slot_needs.py — what's being pinned down is that the headcount holds
regardless of what the model does, not the model itself.
"""
import datetime

import pytest

from app import agent, tools
from app.db import get_conn


def _week_start(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _full_week(week: str, meal: str = "Chili", **extra) -> list[dict]:
    return [
        {
            "date": day, "slot": slot, "meal_name": meal, "is_new_recipe": False,
            "reasoning": "fits the week", **extra,
        }
        for day in tools._week_dates(week)
        for slot in tools.WEEK_SLOTS
    ]


def _grocery_qty_for(plan_id: int, day: str, slot: str, item: str) -> str | None:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT mpgl.quantity FROM meal_plan_grocery_links mpgl
        JOIN meal_plan_entries mpe ON mpe.id = mpgl.meal_plan_entry_id
        WHERE mpe.weekly_plan_id = ? AND mpe.date = ? AND mpe.slot = ? AND mpgl.item = ?
        """,
        (plan_id, day, slot, item),
    ).fetchone()
    conn.close()
    return row["quantity"] if row else None


@pytest.fixture
def couple():
    """Emily and Vineeth — the two-adult household the deepened model was designed against."""
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    return {m["name"]: m["id"] for m in tools.list_members()}


@pytest.fixture
def recipe():
    tools.add_recipe(
        "Chili",
        ingredients=[{"item": "beans", "qty": "4 cups"}, {"item": "salt", "qty": "a pinch"}],
        prep_time_minutes=10, cook_time_minutes=20,
    )


@pytest.fixture
def stub_model(monkeypatch):
    seen = {}

    def _stub(days):
        def _fake(context):
            seen["context"] = context
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", _fake)
        return seen

    return _stub


# ---------- the default: absence of a row means everyone's home ----------

def test_a_meal_with_no_attendance_row_has_everyone_home(couple):
    """
    The ordinary case is stored as nothing at all. This is what lets a
    member added next month be present by default at every meal, rather
    than retroactively absent from every meal already planned.
    """
    att = tools.get_slot_attendance(_week_start(), "dinner")

    assert att["present_names"] == ["Emily", "Vineeth"]
    assert att["headcount"] == 2
    assert att["everyone_home"] is True
    assert att["explicit"] is False, "the ordinary case should not need a row"


def test_a_member_added_later_is_present_at_meals_already_recorded(couple):
    """A new person joins the table; they don't have to be re-added to every meal."""
    day = _week_start()
    tools.set_guest_count(day, "dinner", 1)  # forces a row to exist

    tools.add_member("Robin")

    att = tools.get_slot_attendance(day, "dinner")
    assert "Robin" not in att["present_names"], (
        "an explicit attendance row is authoritative about who is there"
    )
    other_day = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    assert tools.get_slot_attendance(other_day, "dinner")["headcount"] == 3


# ---------- the one-off gesture: one person out of one meal ----------

def test_toggling_one_person_out_leaves_a_meal_for_the_other(couple):
    """
    Emily's own example: "just Emily is home for dinner on Thursday, but
    Vineeth is out." The meal still happens — for one.
    """
    thursday = (datetime.date.fromisoformat(_week_start()) + datetime.timedelta(days=3)).isoformat()

    att = tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)

    assert att["present_names"] == ["Emily"]
    assert att["absent_names"] == ["Vineeth"]
    assert att["headcount"] == 1
    assert att["nobody_home"] is False
    assert tools.get_slot_need(thursday, "dinner")["need"] == "normal", (
        "one person out is not the household being away"
    )


def test_the_day_card_summary_names_who_is_missing(couple):
    """The line under the presence avatars, in the house voice (DESIGN_SYSTEM §7)."""
    thursday = _week_start()
    att = tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)

    assert tools.attendance_summary_line(att) == "Dinner for 1 — Vineeth's out."


def test_an_unknown_name_is_an_error_rather_than_a_silent_no_op(couple):
    """Silently dropping a name would produce a trip that marks nobody away and looks like it worked."""
    with pytest.raises(ValueError, match="No household member named"):
        tools.set_member_attendance(_week_start(), "dinner", "Nobody", present=False)


# ---------- away IS an empty attendance, in both directions ----------

def test_emptying_a_meal_makes_it_away_and_refilling_it_undoes_that(couple):
    """
    'away' stops being a flag someone sets. Both directions matter: without
    the reverse, tapping an avatar back on would leave the slot blanked
    forever with nothing to explain why.
    """
    day = _week_start()

    tools.set_member_attendance(day, "dinner", "Vineeth", present=False)
    tools.set_member_attendance(day, "dinner", "Emily", present=False)
    assert tools.get_slot_need(day, "dinner")["need"] == "away"

    tools.set_member_attendance(day, "dinner", "Emily", present=True)
    assert tools.get_slot_need(day, "dinner")["need"] == "normal", (
        "putting someone back at the table should un-blank the meal"
    )
    assert tools.get_slot_attendance(day, "dinner")["headcount"] == 1


def test_refilling_a_meal_leaves_a_hand_set_need_alone(couple):
    """Only 'away' is attendance's to clear — a 'quick' tag is somebody's actual decision."""
    day = _week_start()
    tools.set_slot_need(day, "dinner", "quick")

    tools.set_member_attendance(day, "dinner", "Vineeth", present=False)

    assert tools.get_slot_need(day, "dinner")["need"] == "quick"


def test_guests_are_the_same_model_with_the_headcount_up(couple):
    """'Hosting guests' unifies into attendance rather than being a parallel notion of table size."""
    day = _week_start()

    att = tools.set_guest_count(day, "dinner", 3)

    assert att["headcount"] == 5
    assert att["guest_count"] == 3
    assert att["everyone_home"] is False, "a bigger table is still a table that differs from the default"


def test_the_hosting_guests_chip_writes_attendance(couple):
    """
    The night-tag chip and the presence avatars must move the SAME number —
    otherwise the chip's "and shop for that" promise can't reach groceries.
    """
    week = _week_start()
    saturday = tools._week_dates(week)[5]

    tools.save_week_intake(
        week, night_tags={saturday: ["guests"]},
        guest_counts={saturday: {"adults": 2, "children": 1}},
    )

    assert tools.get_slot_attendance(saturday, "dinner")["guest_count"] == 3
    assert tools.headcount_for_slot(saturday, "dinner") == 5


# ---------- person-scoped away stretches ----------

def test_a_partial_trip_does_not_blank_the_meals_the_rest_of_the_house_eats(couple):
    """
    The core of the deepening. Vineeth away Saturday lunch -> Sunday lunch
    leaves Emily eating at home the whole time: those meals must still be
    planned and shopped for, just for one.
    """
    week = _week_start()
    saturday, sunday = tools._week_dates(week)[5], tools._week_dates(week)[6]

    result = tools.set_away_stretch(saturday, "lunch", sunday, "lunch", member_names=["Vineeth"])

    assert result["whole_household"] is False
    assert result["away_slots"] == [], "nobody's meal should be cancelled — Emily is still home"
    reduced = {(r["date"], r["slot"]) for r in result["reduced_slots"]}
    assert reduced == {
        (saturday, "lunch"), (saturday, "dinner"), (sunday, "breakfast"), (sunday, "lunch"),
    }
    for d, s in reduced:
        assert tools.get_slot_need(d, s)["need"] == "normal"
        assert tools.get_slot_attendance(d, s)["present_names"] == ["Emily"]


def test_a_partial_trips_edges_belong_to_the_traveler_not_the_household(couple):
    """
    If only Vineeth is away, the meal before he leaves is quick FOR HIM —
    tagging the whole household's Saturday breakfast as grab-and-go would
    be exactly the "one layer too shallow" problem this fixes.
    """
    week = _week_start()
    saturday, sunday = tools._week_dates(week)[5], tools._week_dates(week)[6]
    vineeth_id = couple["Vineeth"]

    tools.set_away_stretch(saturday, "lunch", sunday, "lunch", member_names=["Vineeth"])

    quick = tools.get_slot_need(saturday, "breakfast")
    assert quick["need"] == "quick"
    assert quick["for_member_ids"] == [vineeth_id]
    assert quick["for_member_names"] == ["Vineeth"]
    assert "Vineeth" in quick["reason"], f"the reason should name the traveler, got {quick['reason']!r}"

    ready = tools.get_slot_need(sunday, "dinner")
    assert ready["need"] == "ready_made"
    assert ready["for_member_names"] == ["Vineeth"]


def test_a_whole_household_trip_still_reads_as_the_households_own(couple):
    """
    Regression against the merged household-level behavior: everyone away
    is stored as '[]' ("all of us"), not as a coincidence of two
    individual trips, and keeps the plain household wording.
    """
    week = _week_start()
    saturday, sunday = tools._week_dates(week)[5], tools._week_dates(week)[6]

    result = tools.set_away_stretch(saturday, "lunch", sunday, "lunch")

    assert result["whole_household"] is True
    assert result["member_ids"] == []
    away = {(a["date"], a["slot"]) for a in result["away_slots"]}
    assert away == {
        (saturday, "lunch"), (saturday, "dinner"), (sunday, "breakfast"), (sunday, "lunch"),
    }
    quick = tools.get_slot_need(saturday, "breakfast")
    assert quick["need"] == "quick"
    assert quick["for_member_ids"] == [], "an everyone-trip's edge is the household's"
    assert "Vineeth" not in quick["reason"]


def test_a_travelers_edge_walks_past_meals_they_were_already_out_for(couple):
    """
    Vineeth is already out Friday dinner, then away from Saturday lunch.
    His last real meal at home is Friday LUNCH — tagging the Friday dinner
    he was never going to eat as his grab-and-go would be nonsense.
    """
    week = _week_start()
    friday, saturday, sunday = (tools._week_dates(week)[i] for i in (4, 5, 6))
    tools.set_member_attendance(friday, "dinner", "Vineeth", present=False)
    tools.set_member_attendance(saturday, "breakfast", "Vineeth", present=False)

    tools.set_away_stretch(saturday, "lunch", sunday, "lunch", member_names=["Vineeth"])

    assert tools.get_slot_need(saturday, "breakfast")["need"] == "normal", (
        "he isn't at Saturday breakfast either — it can't be his last meal in"
    )
    assert tools.get_slot_need(friday, "dinner")["need"] == "normal"
    quick = tools.get_slot_need(friday, "lunch")
    assert quick["need"] == "quick", "his last meal actually at the table is Friday lunch"
    assert quick["for_member_names"] == ["Vineeth"]


def test_a_trip_everyone_is_on_empties_the_meals_and_marks_them_away(couple):
    """Both people gone is the same gesture, and lands on the household-level 'away' outcome."""
    week = _week_start()
    saturday = tools._week_dates(week)[5]

    tools.set_away_stretch(saturday, "lunch", saturday, "dinner", member_names=["Emily", "Vineeth"])

    for slot in ("lunch", "dinner"):
        assert tools.get_slot_attendance(saturday, slot)["nobody_home"] is True
        assert tools.get_slot_need(saturday, slot)["need"] == "away"


# ---------- headcount reaches generation and the shopping ----------

def test_generation_is_told_how_many_each_changed_meal_serves(couple, recipe, stub_model):
    """The model gets the real number, and only for the meals that differ."""
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)

    seen = stub_model(_full_week(week))
    agent.generate_weekly_plan(week)

    attendance_ctx = seen["context"]["attendance"]
    assert attendance_ctx["default_serves"] == 2
    changed = {(s["date"], s["slot"]): s for s in attendance_ctx["slots_with_a_different_table"]}
    assert list(changed) == [(thursday, "dinner")], "only the deviating meal should be listed"
    assert changed[(thursday, "dinner")]["serves"] == 1
    assert changed[(thursday, "dinner")]["away"] == ["Vineeth"]
    assert changed[(thursday, "dinner")]["present"] == ["Emily"]


def test_emily_solo_thursday_shops_for_one(couple, recipe, stub_model):
    """
    The brief's named scenario, end to end: Vineeth out Thursday dinner ->
    that night's groceries are scaled to one, while every other night of
    the same week is untouched.
    """
    week = _week_start()
    thursday, friday = tools._week_dates(week)[3], tools._week_dates(week)[4]
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)

    stub_model(_full_week(week))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert _grocery_qty_for(plan_id, thursday, "dinner", "beans") == "2 cups", (
        "half the table should buy half the beans"
    )
    assert _grocery_qty_for(plan_id, friday, "dinner", "beans") == "4 cups", (
        "a night everyone is home must shop exactly as it always has"
    )


def test_a_bigger_table_buys_more(couple, recipe, stub_model):
    """The same arithmetic upward — guests are not a special case."""
    week = _week_start()
    saturday = tools._week_dates(week)[5]
    tools.set_guest_count(saturday, "dinner", 2)

    stub_model(_full_week(week))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert _grocery_qty_for(plan_id, saturday, "dinner", "beans") == "8 cups"


def test_freeform_quantities_are_left_alone_rather_than_guessed_at(couple, recipe, stub_model):
    """"A pinch" does not become "half a pinch"."""
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)

    stub_model(_full_week(week))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert _grocery_qty_for(plan_id, thursday, "dinner", "salt") == "a pinch"


def test_a_week_where_everyone_is_home_shops_exactly_as_before(couple, recipe, stub_model):
    """
    The safety property behind anchoring the scale factor to the household
    rather than to recipes.default_servings: shipping this must not
    silently re-quantify every meal in the app.
    """
    week = _week_start()
    stub_model(_full_week(week))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    for day in tools._week_dates(week):
        assert _grocery_qty_for(plan_id, day, "dinner", "beans") == "4 cups"


def test_an_away_meal_still_buys_nothing_at_all(couple, recipe, stub_model):
    """The non-negotiable invariant, re-checked through the attendance path that now produces it."""
    week = _week_start()
    saturday = tools._week_dates(week)[5]
    tools.set_away_stretch(saturday, "lunch", saturday, "dinner")

    stub_model(_full_week(week))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert _grocery_qty_for(plan_id, saturday, "dinner", "beans") is None
    assert _grocery_qty_for(plan_id, saturday, "lunch", "beans") is None


# ---------- chat parity: the same gestures, reached conversationally ----------

def test_every_declared_tool_has_a_function_behind_it():
    """
    TOOL_DEFINITIONS and TOOL_FUNCTIONS are two hand-maintained lists that
    must agree; a name in one and not the other is a tool the model can
    call into a KeyError, or one it can never discover.
    """
    declared = {d["name"] for d in agent.TOOL_DEFINITIONS}
    implemented = set(agent.TOOL_FUNCTIONS)

    assert declared - implemented == set(), "declared to the model but not callable"
    assert implemented - declared == set(), "callable but never offered to the model"


@pytest.mark.parametrize("name", ["set_member_attendance", "set_guest_count", "get_week_attendance"])
def test_the_attendance_tools_are_offered_to_chat(name):
    assert name in {d["name"] for d in agent.TOOL_DEFINITIONS}
    assert name in agent.TOOL_FUNCTIONS


def test_each_attendance_tool_schema_matches_its_python_signature():
    """
    The schema is written by hand beside the function, so its parameter
    names can drift from the real ones — and the failure mode is a
    TypeError only at the moment a household actually says the thing.
    """
    import inspect

    for definition in agent.TOOL_DEFINITIONS:
        if definition["name"] not in (
            "set_member_attendance", "set_guest_count", "get_week_attendance", "set_away_stretch",
        ):
            continue
        fn = agent.TOOL_FUNCTIONS[definition["name"]]
        real_params = set(inspect.signature(fn).parameters)
        declared = set(definition["input_schema"]["properties"])
        assert declared <= real_params, (
            f"{definition['name']} declares {declared - real_params}, which it cannot accept"
        )


def test_vineeths_out_thursday_works_through_the_chat_tool(couple):
    """The conversational form of the presence-avatar tap, called exactly as the model would."""
    thursday = _week_start()

    result = agent.TOOL_FUNCTIONS["set_member_attendance"](
        date_str=thursday, slot="dinner", member="Vineeth", present=False,
    )

    assert result["headcount"] == 1
    assert result["present_names"] == ["Emily"]


def test_a_person_scoped_trip_works_through_the_chat_tool(couple):
    """"Vineeth's away this weekend" — the same range gesture the intake makes, with a WHO."""
    week = _week_start()
    saturday, sunday = tools._week_dates(week)[5], tools._week_dates(week)[6]

    result = agent.TOOL_FUNCTIONS["set_away_stretch"](
        from_date=saturday, from_slot="lunch", to_date=sunday, to_slot="lunch",
        member_names=["Vineeth"],
    )

    assert result["whole_household"] is False
    assert result["member_names"] == ["Vineeth"]
    assert result["away_slots"] == []
    assert len(result["reduced_slots"]) == 4
