"""
A recipe's "Do ahead" markers follow their step when the chat rewrites the steps.

`update_recipe_details` (the chat's "fix this recipe's steps" tool, and the
save behind "Fill in this recipe") stores `advance_prep_step_indices` as
1-based POSITIONS into `instructions`. Passing new instructions without new
markers used to leave the old positions in place, so they pointed at
whatever step now sat there: the marinade step lost its "Do ahead", "Serve"
gained one, and a position past the end marked the side dish's steps that
cook mode appends after the recipe's own.
"""
from __future__ import annotations

from app import tools


OLD_STEPS = [
    "Marinate the chicken overnight.",
    "Heat the grill.",
    "Grill the chicken.",
    "Serve.",
]


def _recipe() -> None:
    tools.add_recipe(
        "Grilled Chicken",
        ingredients=[{"item": "Chicken thighs", "qty": "1 lb", "category": "meat"}],
        instructions=OLD_STEPS,
        default_servings=4,
        advance_prep_notes="Marinate the night before.",
        advance_prep_step_indices=[1],
    )


def _marked() -> list[int]:
    return tools.get_recipe("Grilled Chicken")["advance_prep_step_indices"]


def test_a_new_step_in_front_moves_the_marker_with_its_step():
    _recipe()
    new = ["Pat the chicken dry."] + OLD_STEPS
    tools.update_recipe_details("Grilled Chicken", instructions=new)
    assert _marked() == [2]
    assert tools.get_recipe("Grilled Chicken")["instructions"][1] == "Marinate the chicken overnight."


def test_a_step_that_is_gone_stops_being_marked():
    _recipe()
    tools.update_recipe_details(
        "Grilled Chicken", instructions=["Season the chicken.", "Grill it.", "Serve."],
    )
    assert _marked() == []


def test_markers_given_with_the_steps_win_and_are_kept_to_real_steps():
    _recipe()
    tools.update_recipe_details(
        "Grilled Chicken", instructions=["Brine the chicken.", "Grill it."],
        advance_prep_step_indices=[1, 5],
    )
    assert _marked() == [1]


def test_changing_only_the_timing_leaves_the_markers_alone():
    _recipe()
    tools.update_recipe_details("Grilled Chicken", cook_time_minutes=25)
    assert _marked() == [1]


def test_through_chat_a_rewritten_recipe_keeps_do_ahead_on_the_marinade(monkeypatch):
    """End to end with only the model stubbed: the real dispatch loop runs
    update_recipe_details with new steps and no markers, as the model does
    when asked to "add a step to dry the chicken first"."""
    import types

    from app import agent

    _recipe()
    usage = types.SimpleNamespace(
        input_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, output_tokens=0,
    )
    seq = [
        types.SimpleNamespace(
            content=[types.SimpleNamespace(
                type="tool_use", name="update_recipe_details",
                input={"recipe_name": "Grilled Chicken",
                       "instructions": ["Pat the chicken dry."] + OLD_STEPS},
                id="tu_1")],
            stop_reason="tool_use", usage=usage,
        ),
        types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="Added that step.")],
            stop_reason="end_turn", usage=usage,
        ),
    ]
    messages = types.SimpleNamespace(create=lambda **kwargs: seq.pop(0))
    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=messages))
    agent.run_agent_turn([], "add a step to pat the chicken dry first")

    assert _marked() == [2]


def test_a_repeated_step_keeps_the_marker_on_the_one_that_was_marked():
    tools.add_recipe(
        "Risotto",
        ingredients=[{"item": "Arborio rice", "qty": "1 cup", "category": "pantry"}],
        instructions=["Stir.", "Add stock.", "Stir."],
        default_servings=4,
        advance_prep_step_indices=[3],
    )
    tools.update_recipe_details("Risotto", instructions=["Stir.", "Add stock.", "Stir.", "Serve."])
    assert tools.get_recipe("Risotto")["advance_prep_step_indices"] == [3]


def test_a_reworded_marked_step_keeps_its_do_ahead():
    """Review catch: same number of steps, the marked one reworded — the
    marker stays where it was rather than vanishing beside a live note."""
    tools.add_recipe(
        "G",
        ingredients=[{"item": "Chicken thighs", "qty": "1 lb", "category": "meat"}],
        instructions=["Marinate the chicken overnight.", "Grill.", "Serve."],
        default_servings=4,
        advance_prep_notes="Marinate overnight.",
        advance_prep_step_indices=[1],
    )
    tools.update_recipe_details(
        "G", instructions=["Marinate the chicken at least 4 hours or overnight.", "Grill.", "Serve."],
    )
    recipe = tools.get_recipe("G")
    assert recipe["advance_prep_step_indices"] == [1]
    assert recipe["advance_prep_notes"] == "Marinate overnight."
