"""
A stated request in the intake is the week's ANCHOR, not an order to satisfy
in a vacuum (Emily, 2026-09-05, decision 11a). These tests pin the wording so
a later prompt edit can't quietly revert the stance.
"""
from app import agent
from conftest import prompt_literals

# The day-based `instructions` block, and only it — the docstring above it and
# the short code strings under it are not prompt. prompt_literals rather than
# inspect.getsource (see tests/conftest.py): the slice markers are the first
# and last sentences of the prompt itself rather than the `instructions = f"""`
# line of code, so the same block comes back without reading agent.py at test
# time. Not byte-identical to the getsource slice it replaces — the line of
# code at the front is gone, the line-continuation backslashes are resolved,
# and each `{interpolation}` reads as a newline — but every sentence below
# was checked against both.
_OPENS = "Generate a full menu for this household's planning period"
_CLOSES = "Call submit_weekly_plan with the result."


def _day_based_instructions() -> str:
    text = prompt_literals(agent.generate_weekly_plan_llm)
    assert _OPENS in text and _CLOSES in text, (
        "the day-based prompt no longer opens and closes with the sentences "
        "this file slices on — move _OPENS/_CLOSES to the new ones rather "
        "than dropping the slice, or these four tests start reading the "
        "function's docstring as if it were prompt"
    )
    start = text.index(_OPENS)
    return text[start:text.index(_CLOSES, start)]


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
