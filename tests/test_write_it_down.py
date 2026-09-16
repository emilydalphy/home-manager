"""Round 2 of "Make the recipes themselves better" — write the step, order the
minutes, sauce the plate.

Emily, 2026-09-14: "The recipe prompts have gotten way better than before,
but would like to see if we can take it up a level again." The research pass
(68 sources, eleven themes) found COOK_DONT_ASSEMBLE covers how the food gets
good and says nothing about how the recipe is written down: no rule that a
step carries a heat, a time and a doneness cue (only the small fill-in prompt
asked for that), nothing about ordering steps the way a person stands at a
stove, nothing about what the minutes should count, and no sauce on the
plate. Four of the eleven themes were never mentioned at all.

These tests hold the second block in place — in the cached part of both
planners, beside the first one — and hold the floor under it: four narrow
checks that read how a method is written, never whether it tastes good.
"""

import pytest

from app import agent
from app.tools import plan_quality as q
from tests.test_food_quality_floor import SALMON, TIKKA, PINEAPPLE_FREE, AVOIDS, _dinner
from conftest import agent_function_source, agent_source


ROUND_2_RULES = {
    "steps_have_no_cue", "no_heat_named", "longest_thing_not_first", "minutes_vs_steps",
}

HEADINGS = (
    "WRITE THE STEP, NOT THE IDEA",
    "STEPS ARE A TIMELINE",
    "HONEST MINUTES",
    "SOMETHING WET, ON PURPOSE",
    "THE OTHER THREE",
    "AROMATICS IN ORDER",
    "PROTEIN, PROPERLY",
    "VEGETABLES THAT TASTE OF SOMETHING",
    "CRUNCH ON TOP",
    "BOLD ON THE SIDE",
    "COOK-ONCE NIGHTS",
)


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------

def test_every_move_from_the_research_ships():
    """The card lists eleven moves. A later edit that trims one for length
    should have to say so here."""
    for heading in HEADINGS:
        assert heading in agent.WRITE_IT_DOWN, f"missing: {heading}"


def test_it_sits_in_the_cached_block_of_both_planners_right_after_its_sibling():
    """Same placement test its sibling has, and one more: it follows
    COOK_DONT_ASSEMBLE directly. "The moves above" in its first line refers
    to that block, so the order is part of the meaning, not just the cost."""
    # agent_function_source, not inspect.getsource: adjacency of two f-string
    # splice points is a fact about the file, not about the compiled prompt,
    # so it needs the text — from conftest's single cached read, ast-sliced.
    for fn in (agent.generate_weekly_plan_llm, agent.generate_component_plan_llm):
        body = agent_function_source(fn.__name__)
        ins_at = body.index('instructions = f"""')
        ctx_at = body.index("context_block")
        sibling_at = body.index("{COOK_DONT_ASSEMBLE}")
        mine_at = body.index("{WRITE_IT_DOWN}")
        assert ins_at < sibling_at < mine_at < ctx_at, (
            "%s carries WRITE_IT_DOWN outside the cached block, or not after its sibling"
            % fn.__name__
        )
        assert body[sibling_at:mine_at].strip() == "{COOK_DONT_ASSEMBLE}", (
            "something has been wedged between the two quality blocks in %s" % fn.__name__
        )
        assert '"text": instructions, "cache_control"' in body


def test_it_is_defined_once_and_ships_no_unrendered_placeholder():
    assert agent_source().count("WRITE_IT_DOWN = ") == 1
    assert "{" not in agent.WRITE_IT_DOWN and "}" not in agent.WRITE_IT_DOWN


def test_it_does_not_loosen_a_time_cap():
    """The one thing this block must never do. It says so in its first lines,
    and the honest-minutes rule points the other way: choose a different
    dish, never shave the estimate."""
    block = agent.WRITE_IT_DOWN
    assert "Every time cap stays exactly as hard as it is" in block
    assert "never shave the estimate" in block
    for word in ("longer recipe", "extra time is fine", "may exceed"):
        assert word not in block.lower()


def test_temperatures_ride_beside_the_cue_never_instead_of_it():
    """Most households don't own a thermometer. The number is for the cook
    who does; the cue is for everyone."""
    assert "alongside the cue, never instead of it" in agent.WRITE_IT_DOWN


def test_the_chat_path_carries_the_same_step_standard():
    """A recipe built from the user's own idea in chat goes through add_recipe,
    not the planners. It gets one sentence of the same bar."""
    add_recipe = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "add_recipe")
    assert "Write each instruction so a person can cook from it" in add_recipe["description"]
    assert "longest thing first" in add_recipe["description"]


# --------------------------------------------------------------------------
# The floor
# --------------------------------------------------------------------------

def _round2(week, context=None):
    ctx = AVOIDS if context is None else context
    return {v.rule for v in q.check_week(week, ctx) if v.rule in ROUND_2_RULES}


BLIND = _dinner("2026-09-14", "Chicken with Sauce", [
    "Cook the chicken in a pan.",
    "Make the sauce.",
    "Add the vegetables.",
    "Serve with rice.",
])

OVEN_REMEMBERED_LATE = _dinner("2026-09-15", "Roast Chicken Thighs with Potatoes", [
    "Pat the thighs dry and salt them.",
    "Dice the potatoes and toss with oil, salt and rosemary.",
    "Slice the onion and mince the garlic.",
    "Mix the yogurt sauce with lemon and dill.",
    "Preheat the oven to 425°F.",
    "Roast the potatoes 20 minutes, then add the thighs skin-side up and roast until 175°F, 25 minutes more.",
])

TOO_MANY_STEPS_TOO_FEW_MINUTES = _dinner("2026-09-16", "Fifteen-Minute Everything", [
    "Preheat the oven to 425°F.",
    "Pat the chicken dry and season with 1 tsp salt.",
    "Sear in a hot skillet over medium-high until browned, 4 minutes a side.",
    "Meanwhile, chop the onion, garlic and peppers.",
    "Remove the chicken; sweat the onion until soft, 6 minutes.",
    "Add garlic and cook 30 seconds until fragrant.",
    "Add tomatoes and simmer until thickened, 10 minutes.",
    "Return the chicken and simmer until cooked through, 8 minutes.",
    "Roast the broccoli until charred at the edges, 15 minutes.",
    "Finish with parsley and lemon; taste for salt.",
], prep_time_minutes=5, cook_time_minutes=10)


def test_a_method_with_no_doneness_cue_anywhere_is_caught():
    """"Cook the chicken. Make the sauce." The household is cooking blind."""
    assert "steps_have_no_cue" in _round2([BLIND])


def test_a_method_that_uses_a_pan_but_never_says_how_hot_is_caught():
    assert "no_heat_named" in _round2([BLIND])


def test_an_oven_preheated_at_step_five_is_caught():
    """Chopping for ten minutes, then waiting for the oven — the case where a
    25-minute dinner takes 40."""
    fired = _round2([OVEN_REMEMBERED_LATE])
    assert "longest_thing_not_first" in fired
    # It has cues, heat and no minutes claimed, so nothing else fires.
    assert fired == {"longest_thing_not_first"}


def test_ten_real_steps_in_fifteen_minutes_is_caught():
    fired = _round2([TOO_MANY_STEPS_TOO_FEW_MINUTES])
    assert "minutes_vs_steps" in fired
    assert fired == {"minutes_vs_steps"}, "the method itself is written well; only the number is wrong"


def test_the_same_method_with_honest_minutes_is_clean():
    honest = dict(TOO_MANY_STEPS_TOO_FEW_MINUTES, prep_time_minutes=15, cook_time_minutes=35)
    assert _round2([honest]) == set()


def test_the_good_dinner_stays_clean():
    """The control, again the most important test here. The Tikka Masala
    fixture is real output and the example the prompt holds up."""
    assert _round2([TIKKA]) == set()


def test_the_dull_but_well_written_dinner_stays_clean_here():
    """The salmon's problem is flavour (method_is_assembly catches it). Its
    steps are written fine — preheat first, 400F, "until it flakes" — so
    none of the round-2 checks have anything to say about it."""
    assert _round2([SALMON]) == set()


@pytest.mark.parametrize("label,steps", [
    ("a terse stir-fry", [
        "Heat the wok until smoking.",
        "Stir-fry the beef in two batches until browned, 2 minutes each.",
        "Add garlic and ginger, then the sauce; toss 1 minute and serve.",
    ]),
    ("a dressed salad", [
        "Whisk the lemon juice, mustard and olive oil into a dressing.",
        "Toss the leaves and shaved fennel through it.",
        "Top with toasted hazelnuts and shaved parmesan, and season well.",
    ]),
    ("a cold soba bowl", [
        "Boil the soba until just tender, then rinse under cold water.",
        "Whisk soy, sesame oil, rice vinegar and grated ginger together.",
        "Toss the noodles through with cucumber and scallion.",
    ]),
    ("a pot of soup with no oven or skillet", [
        "Sweat the onion, carrot and celery in a pot until soft.",
        "Add stock and lentils; simmer 25 minutes until tender.",
        "Finish with lemon and parsley; taste for salt.",
    ]),
    ("a dinner that serves over rice without starting it", [
        "Sear the tofu in a hot pan until golden, 3 minutes a side.",
        "Add the curry paste and coconut milk; simmer 8 minutes.",
        "Stir in spinach until wilted.",
        "Serve over rice.",
    ]),
])
def test_a_reasonable_method_is_not_flagged(label, steps):
    """Each of these is a dinner a household would be glad of. A floor that
    fires on any of them is noise, and the last round's lesson was that noise
    is worse than no check."""
    assert _round2([_dinner("2026-09-14", label.title(), steps)]) == set()


def test_a_cold_plate_is_not_asked_when_it_is_done():
    """Cottage cheese and fruit never meets heat, so nothing in it can be
    "done". The first draft of the cue check fired on it."""
    assert _round2([PINEAPPLE_FREE]) == set()


@pytest.mark.parametrize("entry", [
    _dinner("2026-09-14", "Leftover Chili", ["Reheat.", "Serve.", "Eat."], source="leftovers"),
    _dinner("2026-09-14", "Something Quick", ["Heat it.", "Eat it."]),
    {"date": "2026-09-14", "slot": "breakfast", "state": "planned",
     "meal_name": "Eggs", "instructions": ["Cook the eggs in a pan.", "Butter the toast.", "Eat."]},
    {"date": "2026-09-14", "slot": "lunch", "state": "planned",
     "meal_name": "Quesadilla", "instructions": ["Cook it in a pan.", "Cut it.", "Eat."]},
])
def test_the_floor_does_not_judge_what_it_cannot_judge(entry):
    """Same gate as the first three checks: cooked dinners with a method long
    enough to judge. Breakfast and lunch are meant to be low-effort."""
    assert _round2([entry]) == set()


def test_all_four_are_registered_in_check_week():
    week = [BLIND, OVEN_REMEMBERED_LATE, TOO_MANY_STEPS_TOO_FEW_MINUTES]
    assert _round2(week) == ROUND_2_RULES


# --------------------------------------------------------------------------
# The verifier's findings (independent pass against d1f951e)
# --------------------------------------------------------------------------

def test_a_cue_on_the_prep_does_not_excuse_a_cooking_step_with_none():
    """"Marinate until you have time" is an "until", but not one about the
    cooking. The first draft searched the whole method for any cue and let
    this through; the cue has to sit on a step that applies heat."""
    week = [_dinner("2026-09-14", "Marinated Chicken", [
        "Marinate the chicken until you have time to cook it, up to two days ahead.",
        "Heat oil in a pan and add the marinated chicken.",
        "Cook the chicken, stirring occasionally, then add the sauce and combine.",
        "Serve.",
    ])]
    assert "steps_have_no_cue" in _round2(week)


def test_a_grill_with_no_heat_named_is_caught():
    """The first draft only asked when an oven or a pan was in the method,
    so a grilled dinner was never checked at all."""
    week = [_dinner("2026-09-14", "Grilled Chicken", [
        "Pat the chicken dry and season with salt.",
        "Grill the chicken until cooked through and the juices run clear.",
        "Rest 5 minutes, then slice and serve.",
    ])]
    assert "no_heat_named" in _round2(week)


def test_broil_names_its_own_heat():
    """The verifier offered a broiled salmon as a second case. A home
    broiler has one setting, so "broil" says how hot the way "simmer" does —
    this one stays clean on purpose."""
    week = [_dinner("2026-09-14", "Broiled Salmon", [
        "Pat the salmon dry and season with salt and pepper.",
        "Broil until the top is golden and it flakes easily, about 8 minutes.",
        "Serve with lemon.",
    ])]
    assert "no_heat_named" not in _round2(week)


def test_until_smoking_counts_as_naming_the_heat():
    """A wok heated until it smokes has been told exactly how hot to be."""
    week = [_dinner("2026-09-14", "Beef Stir-Fry", [
        "Heat the wok until smoking.",
        "Stir-fry the beef in two batches until browned, 2 minutes each.",
        "Add garlic and ginger, then the sauce; toss 1 minute and serve.",
    ])]
    assert "no_heat_named" not in _round2(week)


def test_rice_from_the_packet_plus_a_dressing_is_not_asked_how_hot():
    """The heat came from the packet. A bowl built on that has no level of
    its own to name, and the check prefers silence there."""
    week = [_dinner("2026-09-14", "Rice Bowl", [
        "Cook the rice according to the packet.",
        "Whisk the dressing: soy, lime, sesame oil and honey.",
        "Toss the rice with the edamame, cucumber and dressing; top with avocado.",
    ])]
    assert "no_heat_named" not in _round2(week)
