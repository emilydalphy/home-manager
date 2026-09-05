"""
Loop Board "Onboarding asks about leftovers twice" and "Onboarding / meal
setup: add a Snacks & desserts count" (Emily, 2026-09-05).

Item A merges the rhythm step's "how do you feel about leftovers?" and the
final onboarding step's "how do you feel about repeats?" into the ONE
rhythm-step question, with household_rhythm.leftovers_stance as the single
source of truth generation reads. meal_preferences.repeats_tolerance is
kept as a column (old data, and something for the one-time migration below
to read from) but is no longer asked, shown, or read by generation.

Item B adds a fourth "Snacks & desserts" count (meal_preferences.
snacks_per_week, default 3) alongside the existing breakfasts/lunches/
dinners counts, following the same distinct-count-then-rotate rule.
"""
import inspect

import pytest

from app import agent, db, tools


def _week_start():
    import datetime
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


def _full_week(week: str, meal: str = "Chili") -> list[dict]:
    return [
        {"date": day, "slot": slot, "meal_name": meal, "is_new_recipe": False, "reasoning": "fits the week"}
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


# ---------- Item A: the repeats_tolerance -> leftovers_stance migration ----------

class TestRepeatsToleranceMigration:
    def _set_repeats_tolerance(self, value: str):
        # A raw write straight to the deprecated column — standing in for
        # data left behind by the OLD onboarding flow, before
        # edit_preference existed to go through set_household_meal_preferences.
        conn = db.get_conn()
        conn.execute(
            """
            INSERT INTO meal_preferences (household_id, repeats_tolerance, updated_at)
            VALUES (1, ?, datetime('now'))
            ON CONFLICT(household_id) DO UPDATE SET repeats_tolerance = excluded.repeats_tolerance
            """,
            (value,),
        )
        conn.commit()
        conn.close()

    @pytest.mark.parametrize("old_value,expected", [
        ("cook_once_eat_twice", "love_them"),
        ("one_a_week", "fine_sometimes"),
        ("all_different", "fresh_each_night"),
    ])
    def test_migration_maps_each_old_value(self, old_value, expected):
        self._set_repeats_tolerance(old_value)
        assert tools.get_household_rhythm()["leftovers_stance"] is None

        conn = db.get_conn()
        db._migrate_repeats_tolerance_to_leftovers_stance(conn)
        conn.commit()
        conn.close()

        assert tools.get_household_rhythm()["leftovers_stance"] == expected

    def test_a_household_with_leftovers_stance_already_set_is_untouched(self):
        # Answered the NEW rhythm question directly — never touched the old
        # repeats question at all, so repeats_tolerance is still blank. The
        # migration must not invent a "wrong" answer from nothing.
        tools.set_leftovers_stance("fresh_each_night")

        conn = db.get_conn()
        db._migrate_repeats_tolerance_to_leftovers_stance(conn)
        conn.commit()
        conn.close()

        assert tools.get_household_rhythm()["leftovers_stance"] == "fresh_each_night"

    def test_a_newer_rhythm_answer_beats_an_older_conflicting_repeats_answer(self):
        # Answered leftovers_stance directly (love_them) AND separately has
        # an old, disagreeing repeats_tolerance on record (all_different).
        # The newer, more specific answer must win — the migration is only
        # a backfill for a household that never answered the new question.
        tools.set_leftovers_stance("love_them")
        self._set_repeats_tolerance("all_different")

        conn = db.get_conn()
        db._migrate_repeats_tolerance_to_leftovers_stance(conn)
        conn.commit()
        conn.close()

        assert tools.get_household_rhythm()["leftovers_stance"] == "love_them"

    def test_repeats_tolerance_itself_is_left_alone(self):
        """The column stays — this is a backfill, not a delete."""
        self._set_repeats_tolerance("one_a_week")

        conn = db.get_conn()
        db._migrate_repeats_tolerance_to_leftovers_stance(conn)
        conn.commit()
        conn.close()

        assert tools.get_meal_planning_preferences()["repeats_tolerance"] == "one_a_week"

    def test_an_unmapped_or_blank_repeats_tolerance_is_a_no_op(self):
        # Blank (never answered) is the common case, exercised by every
        # other test's session-scope init_db() run — just confirm directly
        # that it doesn't raise or invent an answer.
        conn = db.get_conn()
        db._migrate_repeats_tolerance_to_leftovers_stance(conn)
        conn.commit()
        conn.close()
        assert tools.get_household_rhythm()["leftovers_stance"] is None


# ---------- Item A: the planner prompt reads only leftovers_stance ----------

def test_prompt_no_longer_mentions_repeats_tolerance():
    source = inspect.getsource(agent.generate_weekly_plan_llm)
    assert "repeats_tolerance" not in source


def test_repeats_tolerance_is_stripped_from_generation_context(recipe, stub_model):
    tools.edit_preference("repeats_tolerance", "cook_once_eat_twice")
    tools.set_leftovers_stance("love_them")
    week = _week_start()
    seen = stub_model(_full_week(week))

    agent.generate_weekly_plan(week)

    memory = seen["context"]["household_memory"]
    assert "repeats_tolerance" not in memory
    assert memory["rhythm"]["leftovers_stance"] == "love_them"


def test_what_we_know_shows_and_edits_the_merged_leftovers_value(signed_in):
    tools.set_leftovers_stance("fine_sometimes")

    body = signed_in.get("/api/facts?category=rhythm").json()
    assert body["preferences"]["leftovers_stance"] == "fine_sometimes"

    res = signed_in.post("/api/onboarding/rhythm", json={"leftovers_stance": "fresh_each_night"})
    assert res.status_code == 200

    body = signed_in.get("/api/facts?category=rhythm").json()
    assert body["preferences"]["leftovers_stance"] == "fresh_each_night"


# ---------- Item B: snacks_per_week ----------

class TestSnacksPerWeek:
    def test_defaults_to_three(self):
        assert tools.get_meal_planning_preferences()["meal_counts"]["snacks_per_week"] == 3
        assert tools.get_household_memory()["snacks_per_week"] == 3

    def test_counts_round_trip(self):
        tools.edit_preference("snacks_per_week", 5)
        assert tools.get_meal_planning_preferences()["meal_counts"]["snacks_per_week"] == 5
        assert tools.get_household_memory()["snacks_per_week"] == 5

    def test_refuses_an_out_of_range_value(self):
        with pytest.raises(ValueError):
            tools.edit_preference("snacks_per_week", 8)
        with pytest.raises(ValueError):
            tools.edit_preference("snacks_per_week", -1)

    def test_onboarding_answers_post_round_trips_snacks_per_week(self, signed_in):
        res = signed_in.post("/api/onboarding/answers", json={
            "member_names": ["Emily"],
            "household_restrictions": {},
            "eating_style": "",
            "wont_eat": [],
            "excited_about": [],
            "dinners_per_week": 5,
            "breakfasts_per_week": 7,
            "lunches_per_week": 7,
            "snacks_per_week": 2,
        })
        assert res.status_code == 200
        assert tools.get_meal_planning_preferences()["meal_counts"]["snacks_per_week"] == 2

    def test_deleting_resets_to_the_default_of_three(self):
        tools.edit_preference("snacks_per_week", 6)
        tools.delete_preference("snacks_per_week")
        assert tools.get_meal_planning_preferences()["meal_counts"]["snacks_per_week"] == 3


def test_two_snacks_reaches_the_generator(recipe, stub_model):
    """The prompt's household_memory carries whatever count was set — this
    is what the extended distinct-count-then-rotate rule for snacks reads."""
    tools.edit_preference("snacks_per_week", 2)
    week = _week_start()
    seen = stub_model(_full_week(week))

    agent.generate_weekly_plan(week)

    assert seen["context"]["household_memory"]["snacks_per_week"] == 2


def test_zero_snacks_are_planned_empty_all_week(recipe, stub_model):
    """
    Same rule as the existing breakfasts/lunches/dinners zero-count
    handling (see test_week_generation.py's
    test_a_meal_count_of_zero_empties_that_slot_all_week) — snack now
    follows it too. The model's own response never mentions snack at all
    (tools.WEEK_SLOTS is breakfast/lunch/dinner only), so this also proves
    the empty snack slots are written regardless of what the model sent.
    """
    tools.edit_preference("snacks_per_week", 0)
    week = _week_start()
    stub_model(_full_week(week))

    plan = agent.generate_weekly_plan(week)

    from app.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT date, slot_state FROM meal_plan_entries WHERE weekly_plan_id = ? AND slot = 'snack'",
        (plan["weekly_plan_id"],),
    ).fetchall()
    conn.close()

    dates = tools._week_dates(week)
    assert {r["date"] for r in rows} == set(dates)
    assert all(r["slot_state"] == "planned_empty" for r in rows)
