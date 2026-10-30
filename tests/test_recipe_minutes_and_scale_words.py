"""
A recipe's minutes are minutes, and scaling is for at least one person
(2026-10-29, overnight hunt of the recipe chat tools).

1. Chat's update_recipe_details(cook_time_minutes="1 hour") stored the
   words in the INTEGER column. Every screen that times a dinner adds
   prep + cook, so /api/today/moves and /api/week-menu then answered 500
   on every day the dish was planned. Words that read as a time save as
   minutes; words that don't are refused in a sentence; rows already
   saved as words are repaired at startup.
2. Chat's scale_recipe(target_servings=0 or -2) answered "-4 tortillas"
   and "0 lb" for the reply to read out; "1.5" was Python's own
   "unsupported operand" text.
"""
from __future__ import annotations

import datetime

import pytest

from app import agent, tools
from app.db import get_conn, init_db
from conftest import household_today


def _chat(_tool, **inp):
    return agent.TOOL_FUNCTIONS[_tool](**agent.read_tool_input(_tool, inp))


def _stew(**extra):
    return tools.add_recipe(
        "Beef Stew",
        ingredients=[{"item": "beef chuck", "qty": "2 lb"}, {"item": "Tortillas", "qty": "8"}],
        instructions=["Brown the beef.", "Braise."],
        default_servings=4,
        **extra,
    )


def _plan_stew():
    tools.add_member("Alex")
    today = household_today()
    plan_id = tools.create_weekly_plan((today - datetime.timedelta(days=1)).isoformat(), day_count=7)["weekly_plan_id"]
    for d in range(-1, 3):
        tools.plan_meal((today + datetime.timedelta(days=d)).isoformat(), "Beef Stew",
                        slot="dinner", weekly_plan_id=plan_id)
    return plan_id


def _stored(column):
    conn = get_conn()
    row = conn.execute(f"SELECT {column} AS v, typeof({column}) AS t FROM recipes WHERE name = 'Beef Stew'").fetchone()
    conn.close()
    return row["v"], row["t"]


@pytest.mark.parametrize("words, minutes", [
    ("1 hour", 60), ("1 hr 30 min", 90), ("45 minutes", 45), ("1 1/2 hours", 90),
    ("half an hour", 30), ("20-25 minutes", 25), ("1h30m", 90), ("90", 90), (0, 0),
])
def test_a_time_in_words_saves_as_minutes(words, minutes):
    assert tools.recipe_minutes(words) == minutes


@pytest.mark.parametrize("junk", ["a while", "-5", -5, True, "overnight", "3 days", 10**9])
def test_words_that_are_not_a_time_read_as_nothing(junk):
    assert tools.recipe_minutes(junk) is None


def test_chat_edit_of_cook_time_in_words_saves_minutes_and_the_screens_still_load(signed_in):
    """The reported 500: one worded cook time broke Today and the week menu."""
    _stew(prep_time_minutes=20)
    _plan_stew()
    saved = _chat("update_recipe_details", recipe_name="beef stew", cook_time_minutes="1 hour")
    assert saved["cook_time_minutes"] == 60
    assert _stored("cook_time_minutes") == (60, "integer")
    for path in ("/api/today/moves", "/api/week-menu"):
        res = signed_in.get(path)
        assert res.status_code == 200, (path, res.text[:300])


def test_chat_edit_of_a_time_that_is_not_one_is_refused_and_nothing_is_saved():
    _stew(cook_time_minutes=90)
    with pytest.raises(ValueError, match="number of minutes"):
        _chat("update_recipe_details", recipe_name="beef stew", cook_time_minutes="a while")
    with pytest.raises(ValueError, match="number of minutes"):
        _chat("update_recipe_details", recipe_name="beef stew", prep_time_minutes="-5")
    assert _stored("cook_time_minutes") == (90, "integer")


def test_a_new_recipe_never_stores_minutes_as_words():
    """add_recipe is every model door's save (the week, a swap): a time it
    can read is minutes, one it can't is left unsaid, never stored."""
    _chat("add_recipe", name="Beef Stew", ingredients=[{"item": "beef chuck", "qty": "2 lb"}],
          instructions=["Braise."], prep_time_minutes="15 min", cook_time_minutes="about forever")
    assert _stored("prep_time_minutes") == (15, "integer")
    assert _stored("cook_time_minutes") == (None, "null")


def test_rows_already_saved_as_words_are_repaired_at_startup(signed_in):
    _stew(prep_time_minutes=20, cook_time_minutes=30)
    conn = get_conn()
    conn.execute("UPDATE recipes SET cook_time_minutes = '1 hour', prep_time_minutes = 'a bit' WHERE name = 'Beef Stew'")
    conn.commit()
    conn.close()
    init_db()
    assert _stored("cook_time_minutes") == (60, "integer")
    assert _stored("prep_time_minutes") == (None, "null")
    _plan_stew()
    assert signed_in.get("/api/today/moves").status_code == 200


@pytest.mark.parametrize("servings", [0, -2, "0", "1.5", "6 people", 2.5])
def test_chat_scaling_to_no_one_or_a_fraction_is_refused_in_a_sentence(servings):
    _stew()
    with pytest.raises(ValueError, match="whole number of people"):
        _chat("scale_recipe", recipe_name="beef stew", target_servings=servings)


def test_chat_scaling_still_scales_a_count_sent_as_text():
    _stew()
    scaled = _chat("scale_recipe", recipe_name="beef stew", target_servings="8")
    assert scaled["target_servings"] == 8
    assert {i["item"]: i["qty"] for i in scaled["scaled_ingredients"]}["Tortillas"] == "16"


@pytest.mark.parametrize("blank", ["", "   "])
def test_chat_cannot_save_a_recipe_with_no_name(blank):
    with pytest.raises(ValueError, match="needs a name"):
        _chat("add_recipe", name=blank, ingredients=[{"item": "rice", "qty": "1 cup"}])
    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 0
    conn.close()


def test_a_rewrite_whose_writer_words_its_time_still_saves_the_steps(monkeypatch):
    """The person's words are refused; the WRITER's are read or left out —
    a rewrite must not be lost over a time it put in words."""
    _stew(cook_time_minutes=90)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", lambda spec: {
        "instructions": ["Brown the beef in the air fryer.", "Braise."],
        "prep_time_minutes": "about 10 minutes", "cook_time_minutes": "until tender",
    })
    saved = _chat("update_recipe_details", recipe_name="beef stew", change="make it in the air fryer")
    assert saved["instructions"][0] == "Brown the beef in the air fryer."
    assert _stored("prep_time_minutes") == (10, "integer")
    assert _stored("cook_time_minutes") == (90, "integer")
