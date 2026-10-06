"""Emily, 2026-10-05, on the route-3 recipe samples: when the best part of a
dish is an overnight step (tikka masala's marinade), keep it and put it on
the night before's Prep list — don't cut it to fit the weeknight cap. The
Prep card already schedules "the night before" notes for the day before
(generate_prep_schedule); this pins the rule that makes recipes say so."""
from app import agent


def test_recipe_writer_keeps_the_overnight_step_and_flags_it_for_prep():
    text = agent.RECIPE_DETAILS_INSTRUCTIONS
    assert "KEEP THE STEP THAT MAKES THE DISH" in text
    assert '"The night before"' in text
    assert "advance_prep_notes" in text and "Prep card" in text


def test_prep_schedule_still_moves_night_before_notes_to_the_day_before():
    import inspect
    src = inspect.getsource(agent)
    assert 'if it says "overnight" or "the night before," schedule' in src


def test_steps_are_written_like_a_friend_at_the_stove():
    assert "SAY IT LIKE A FRIEND AT THE STOVE" in agent.WRITE_IT_DOWN
