"""
Per-slot planning needs and away-stretch range derivation — Loop Board
"Week planning: away-stretches and per-meal needs (the road-trip
weekend)". The model call is stubbed for the generation-level tests,
matching test_week_generation.py's own convention — what's under test
there is that the invariant holds regardless of what the model does, not
the model itself.
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


def _slots_for(plan_id: int) -> dict:
    from app.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT date, slot, slot_state, reasoning FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND component_category IS NULL",
        (plan_id,),
    ).fetchall()
    conn.close()
    return {(r["date"], r["slot"]): {"slot_state": r["slot_state"], "reasoning": r["reasoning"]} for r in rows}


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
    seen = {}

    def _stub(days):
        def _fake(context):
            seen["context"] = context
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", _fake)
        return seen

    return _stub


# ---------- away-stretch range derivation ----------

def test_away_stretch_matches_emilys_road_trip_scenario():
    """
    Emily's exact scenario (Loop Board): away Saturday lunch through Sunday
    lunch, back in time for Sunday dinner. Four slots go away (Sat lunch,
    Sat dinner, Sun breakfast, Sun lunch); Saturday breakfast — the last
    real meal before leaving — becomes 'quick'; Sunday dinner — the first
    real meal back — becomes 'ready_made'.
    """
    week = _week_start()
    dates = tools._week_dates(week)
    saturday, sunday = dates[5], dates[6]

    result = tools.set_away_stretch(saturday, "lunch", sunday, "lunch")

    assert result["away_slots"] == [
        {"date": saturday, "slot": "lunch"},
        {"date": saturday, "slot": "dinner"},
        {"date": sunday, "slot": "breakfast"},
        {"date": sunday, "slot": "lunch"},
    ]
    assert result["quick_slot"] == {"date": saturday, "slot": "breakfast"}
    assert result["ready_made_slot"] == {"date": sunday, "slot": "dinner"}

    for entry in result["away_slots"]:
        assert tools.get_slot_need(entry["date"], entry["slot"])["need"] == "away"
    assert tools.get_slot_need(saturday, "breakfast")["need"] == "quick"
    ready_made = tools.get_slot_need(sunday, "dinner")
    assert ready_made["need"] == "ready_made"
    assert ready_made["recommendation_confirmed"] is False

    # Nothing outside the range is touched.
    friday = dates[4]
    assert tools.get_slot_need(friday, "dinner")["need"] == "normal"


def test_away_stretch_spanning_a_whole_week_still_derives_both_edges_across_week_boundaries():
    """
    A stretch covering every slot of the week (Monday breakfast through
    Sunday dinner) has no slot inside the week itself to derive an edge
    from — both edges land on the adjacent weeks' dates instead, which is
    the correct behaviour (the last real meal before an 8-day trip really
    is the Sunday before it), not a missing edge.
    """
    week = _week_start()
    dates = tools._week_dates(week)
    monday, sunday = dates[0], dates[6]
    day_before = (datetime.date.fromisoformat(monday) - datetime.timedelta(days=1)).isoformat()
    day_after = (datetime.date.fromisoformat(sunday) + datetime.timedelta(days=1)).isoformat()

    result = tools.set_away_stretch(monday, "breakfast", sunday, "dinner")

    assert len(result["away_slots"]) == 21
    assert result["quick_slot"] == {"date": day_before, "slot": "dinner"}
    assert result["ready_made_slot"] == {"date": day_after, "slot": "breakfast"}


def test_away_stretch_rejects_start_after_end():
    week = _week_start()
    dates = tools._week_dates(week)
    with pytest.raises(ValueError):
        tools.set_away_stretch(dates[6], "dinner", dates[0], "breakfast")


def test_set_slot_need_rejects_unknown_need_or_slot():
    week = _week_start()
    d = tools._week_dates(week)[0]
    with pytest.raises(ValueError):
        tools.set_slot_need(d, "dinner", "vacation")
    with pytest.raises(ValueError):
        tools.set_slot_need(d, "brunch", "away")


def test_set_slot_need_normal_clears_the_row():
    week = _week_start()
    d = tools._week_dates(week)[0]
    tools.set_slot_need(d, "lunch", "quick")
    assert tools.get_slot_need(d, "lunch")["need"] == "quick"
    tools.set_slot_need(d, "lunch", "normal")
    assert tools.get_slot_need(d, "lunch")["need"] == "normal"


def test_get_week_slot_needs_only_lists_declared_slots():
    week = _week_start()
    dates = tools._week_dates(week)
    tools.set_slot_need(dates[0], "lunch", "quick")
    needs = tools.get_week_slot_needs(week)
    assert set(needs.keys()) == {dates[0]}
    assert set(needs[dates[0]].keys()) == {"lunch"}


# ---------- marking an already-planned slot away converts it immediately ----------

def test_marking_an_already_planned_slot_away_converts_it_and_reverses_groceries(recipe):
    week = _week_start()
    dates = tools._week_dates(week)
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(
        dates[0], "Chili", slot="lunch", weekly_plan_id=plan["weekly_plan_id"],
        add_ingredients_to_grocery_list=True,
    )
    assert "beans" in [i["item"] for i in tools.list_grocery_list()]

    result = tools.set_slot_need(dates[0], "lunch", "away")

    assert result["converted_existing_plan_slot"] is True
    slots = _slots_for(plan["weekly_plan_id"])
    assert slots[(dates[0], "lunch")]["slot_state"] == "planned_empty"
    assert "beans" not in [i["item"] for i in tools.list_grocery_list()]


def test_marking_away_before_any_plan_exists_just_records_the_need():
    week = _week_start()
    d = tools._week_dates(week)[0]
    result = tools.set_slot_need(d, "lunch", "away")
    assert result["converted_existing_plan_slot"] is False
    assert tools.get_slot_need(d, "lunch")["need"] == "away"


# ---------- ready_made recommendations ----------

def test_ready_made_recommends_a_freezer_item_over_a_batch(recipe):
    week = _week_start()
    dates = tools._week_dates(week)
    saturday, sunday = dates[5], dates[6]
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(dates[4], "Chili", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.update_inventory("Frozen Lasagna", "add", quantity="1", category="frozen", location="freezer")

    result = tools.set_away_stretch(saturday, "lunch", sunday, "lunch")

    rec = result["ready_made_recommendation"]
    assert rec["recommended_defrost_item"] == "Frozen Lasagna"
    assert rec["recommended_batch_from_entry_id"] is None


def test_ready_made_falls_back_to_batching_an_earlier_dinner(recipe):
    week = _week_start()
    dates = tools._week_dates(week)
    saturday, sunday = dates[5], dates[6]
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(dates[4], "Chili", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    result = tools.set_away_stretch(saturday, "lunch", sunday, "lunch")

    rec = result["ready_made_recommendation"]
    assert rec["recommended_batch_from_entry_id"] is not None
    assert rec["recommended_defrost_item"] is None


def test_ready_made_recommends_nothing_when_theres_nothing_to_recommend():
    week = _week_start()
    dates = tools._week_dates(week)
    saturday, sunday = dates[5], dates[6]

    result = tools.set_away_stretch(saturday, "lunch", sunday, "lunch")

    rec = result["ready_made_recommendation"]
    assert rec["recommended_batch_from_entry_id"] is None
    assert rec["recommended_defrost_item"] is None
    assert rec["recommendation_confirmed"] is False


def test_confirming_a_recommendation_round_trips():
    week = _week_start()
    dates = tools._week_dates(week)
    saturday, sunday = dates[5], dates[6]
    tools.update_inventory("Frozen Lasagna", "add", quantity="1", category="frozen", location="freezer")
    tools.set_away_stretch(saturday, "lunch", sunday, "lunch")

    confirmed = tools.confirm_slot_recommendation(sunday, "dinner", confirmed=True)
    assert confirmed["recommendation_confirmed"] is True

    declined = tools.confirm_slot_recommendation(sunday, "dinner", confirmed=False)
    assert declined["recommendation_confirmed"] is False


def test_setting_a_new_recommendation_resets_confirmation():
    week = _week_start()
    dates = tools._week_dates(week)
    saturday, sunday = dates[5], dates[6]
    tools.set_away_stretch(saturday, "lunch", sunday, "lunch")
    tools.set_slot_recommendation(sunday, "dinner", defrost_item="Soup")
    tools.confirm_slot_recommendation(sunday, "dinner")
    assert tools.get_slot_need(sunday, "dinner")["recommendation_confirmed"] is True

    tools.set_slot_recommendation(sunday, "dinner", defrost_item="Different Soup")
    assert tools.get_slot_need(sunday, "dinner")["recommendation_confirmed"] is False
    assert tools.get_slot_need(sunday, "dinner")["recommended_defrost_item"] == "Different Soup"


# ---------- generation respects the needs (the non-negotiable half) ----------

def test_generation_skips_away_slots_at_the_slot_and_week_level(recipe, stub_model):
    """
    The extended invariant: not just dinner (the existing planned_empty
    case), but any slot an away stretch covers — enforced even when the
    model is a disobedient one that plans the whole week anyway, exactly
    like the existing out-night test does for dinner alone.
    """
    week = _week_start()
    dates = tools._week_dates(week)
    saturday, sunday = dates[5], dates[6]
    tools.set_away_stretch(saturday, "lunch", sunday, "lunch")

    seen = stub_model(_full_week(week))  # disobedient: plans every slot, including the away ones

    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    # The data-layer hook reached the generator's context (see
    # slot_needs.generation_context_for_week) even though today's prompt
    # text doesn't yet act on it — see the TODO on that function.
    away_pairs_in_context = {(e["date"], e["slot"]) for e in seen["context"]["slot_needs"]["away_slots"]}
    assert away_pairs_in_context == {
        (saturday, "lunch"), (saturday, "dinner"), (sunday, "breakfast"), (sunday, "lunch"),
    }

    away_slots = [(saturday, "lunch"), (saturday, "dinner"), (sunday, "breakfast"), (sunday, "lunch")]
    slots = _slots_for(plan_id)
    for d, s in away_slots:
        assert slots[(d, s)]["slot_state"] == "planned_empty", f"{d} {s} should have been forced empty"

    # Week-level: the plan is still complete and holds no duplicates despite
    # four slots being overridden after the model already planned them.
    audit = tools.audit_plan_slots(plan_id)
    assert audit["complete"] is True
    assert audit["duplicated"] == []

    # And nothing for any away slot reaches the grocery list.
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    for d, s in away_slots:
        assert _grocery_links_for_date(plan_id, d, s) == [], f"nothing should be bought for {d} {s}"
    # A non-away slot's ingredients still made it onto the list.
    assert "beans" in [i["item"] for i in tools.list_grocery_list()]


def test_generation_computes_a_ready_made_recommendation_after_the_fact(recipe, stub_model):
    """
    A need set BEFORE any plan exists has nothing to recommend against yet
    (see test_ready_made_recommends_nothing_when_theres_nothing_to_recommend).
    Once the week is generated, apply_slot_needs_to_plan should compute the
    recommendation using the real plan just built.
    """
    week = _week_start()
    dates = tools._week_dates(week)
    saturday, sunday = dates[5], dates[6]
    tools.set_away_stretch(saturday, "lunch", sunday, "lunch")
    assert tools.get_slot_need(sunday, "dinner")["recommended_batch_from_entry_id"] is None

    stub_model(_full_week(week))
    agent.generate_weekly_plan(week)

    # Friday's dinner (planned by generation, before the away stretch) is
    # now a real candidate to batch from.
    ready_made = tools.get_slot_need(sunday, "dinner")
    assert ready_made["recommended_batch_from_entry_id"] is not None
    assert ready_made["recommendation_confirmed"] is False


def test_generation_context_includes_slot_needs_even_with_no_rhythm_or_away(recipe, stub_model):
    """A week with nothing declared still gets a (empty) slot_needs key — the hook is always present, not conditionally wired."""
    week = _week_start()
    seen = stub_model(_full_week(week))
    agent.generate_weekly_plan(week)
    assert seen["context"]["slot_needs"] == {"away_slots": [], "quick_slots": [], "ready_made_slots": []}
