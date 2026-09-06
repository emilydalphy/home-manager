"""
A stated request in the intake is the week's ANCHOR, not an order to satisfy
in a vacuum (Emily, 2026-09-05, decision 11a). These tests pin the wording so
a later prompt edit can't quietly revert the stance.
"""
import inspect

from app import agent


def _day_based_instructions() -> str:
    src = inspect.getsource(agent.generate_weekly_plan_llm)
    start = src.index('instructions = f"""')
    end = src.index("Call submit_weekly_plan with the result.", start)
    return src[start:end]


def test_a_stated_request_is_the_weeks_anchor_not_an_isolated_order():
    text = _day_based_instructions()
    assert "is the week's ANCHOR" in text
    assert "build the" in text and "days around it" in text
    assert "Delivering the literal request and nothing else" in text


def test_the_old_honour_it_exactly_wording_is_gone():
    assert "honour it exactly" not in _day_based_instructions()


def test_the_week_is_asked_to_read_as_composed():
    text = _day_based_instructions()
    assert "A week should read as composed" in text
    assert "not seven independent daily decisions" in text


def test_the_tag_collision_rule_is_the_one_stated_exception():
    text = _day_based_instructions()
    assert "this is the ONE exception to putting" in text
