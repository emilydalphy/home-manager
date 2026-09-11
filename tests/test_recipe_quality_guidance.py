"""Route 1 of "Make the recipes themselves better" — tell the planner what
good food is.

Emily, 2026-09-09: "we need to do research on ways to enhance it, whether
it's with better prompting, or open to connecting to an API." The research
(Loop Board, 2026-09-10) found the planner's ~490 lines of instructions
contained exactly ONE guideline about the food being good, and it fired only
when a dish named a specific regional style. Zero mentions of seasoning —
both matches for "season" were the calendar.

The evidence that prompting is the right lever was already sitting in the
household's own database. Two dinners, same model, same week:

  "Baked Lemon Herb Salmon"  — sheet pan, drizzle, bake. Nothing browned,
                               lemon added before baking, one texture, and
                               the salt and pepper in the method were never
                               on the shopping list.
  "Chicken Tikka Masala"     — marinate, sear, sweat the aromatics in the
                               same pan, bloom the spices, build the sauce,
                               finish with cilantro.

The second one names a cuisine, so the one quality guideline fired. The first
one is "American", so nothing did. These tests hold the generalised version of
that guideline in place, and hold it in the part of the prompt where it is
nearly free to carry.
"""

import inspect

import pytest

from app import agent


PROMPT = inspect.getsource(agent.generate_weekly_plan_llm)


def _instructions_block() -> str:
    """The guidance exactly as the model receives it.

    agent.COOK_DONT_ASSEMBLE is the rendered constant, not source text, so
    these assertions test the words that actually ship. Line continuations are
    already resolved by Python; nothing here depends on where someone happened
    to wrap the text in the file.
    """
    return agent.COOK_DONT_ASSEMBLE


def test_the_planner_is_told_to_cook_rather_than_assemble():
    """The gap the research found, closed.

    Not "mentions flavour somewhere" — the specific failure mode, which is
    combining ingredients and applying heat and calling it a recipe.
    """
    block = _instructions_block()
    assert "COOK, DON'T ASSEMBLE" in block


def test_the_guidance_covers_every_real_cook_not_just_named_cuisines():
    """The old rule's whole problem was that it was conditional.

    A Chettinad dish got technique; "Baked Lemon Herb Salmon" got nothing,
    because it doesn't name a regional style — and most of a normal week
    doesn't. Whatever the wording, the guidance must not re-narrow to the
    cuisine case.
    """
    block = _instructions_block()
    marker = block[block.index("COOK, DON'T ASSEMBLE"):]
    marker = marker[:marker.index("WORKED EXAMPLE")]
    assert "EVERY real cook" in marker, (
        "the quality guidance has to apply beyond dishes that name a cuisine"
    )
    # And the original conditional rule is still there, in the prompt around
    # it — this generalises that rule, it does not replace it.
    assert "Chettinad" in PROMPT


@pytest.mark.parametrize("technique", [
    "BROWN SOMETHING",      # the salmon's actual failure
    "SEASON IN STAGES",     # zero mentions of seasoning before this
    "BLOOM DRY SPICES",     # what the Tikka Masala did and the salmon didn't
    "FINISH OFF THE HEAT",  # the salmon's lemon went in before baking
    "GIVE IT TEXTURE",      # everything on that sheet pan ended up soft
])
def test_each_technique_that_costs_no_extra_time_is_named(technique):
    """Named individually rather than gestured at.

    "Make it taste better" is not an instruction. Each of these is a concrete
    move, each one is free in time, and each one is something the dull example
    demonstrably failed to do.
    """
    assert technique in _instructions_block()


def test_the_guidance_does_not_quietly_buy_itself_more_time():
    """Emily, 2026-09-10, choosing how this should work: the household decides
    how long it wants to spend cooking, not the planner.

    So better food has to come out of the minutes the slot already has. The
    risk in a prompt like this is that it reads as licence to write a
    45-minute braise onto a Tuesday — the guidance has to say the opposite,
    out loud, or the rush and weeknight caps above it get quietly eroded.
    """
    block = _instructions_block()
    marker = block[block.index("COOK, DON'T ASSEMBLE"):]
    assert "NOT a licence to write longer recipes" in marker
    assert "rush" in marker and "weeknight_max_minutes" in marker, (
        "the guidance must restate the existing caps, not leave them implied"
    )


def test_confident_not_aggressive():
    """Emily's call, 2026-09-10, on how bold the food should be: season
    properly and finish brightly, but assume no appetite for heat or
    unfamiliar flavours unless this household has actually said so. There is
    no per-household dial for this yet, so the default is every household's
    default."""
    block = _instructions_block()
    assert "CONFIDENT, NOT AGGRESSIVE" in block


def test_seasonings_have_to_reach_the_shopping_list():
    """Julia, 2026-09-08: "The dishes sound good, but the recipe details are
    not accurate."

    The salmon is the exact case: its method reaches for salt and pepper, and
    neither is in its ingredients. plan_quality's _steps_match_ingredients
    already watches for this after the fact; this tells the model up front.
    """
    assert "must ALSO appear in the ingredients list" in _instructions_block()


def test_the_worked_example_is_real_output_not_an_invention():
    """A made-up good/bad pair teaches the model a made-up distinction.

    Both halves are real rows from the household database that motivated this
    ticket, which is also why they are safe to quote: they are this app's own
    output, and the contrast between them is the finding.
    """
    block = _instructions_block()
    assert "WORKED EXAMPLE" in block
    assert "Baked Lemon Herb Salmon" in block
    assert "Chicken Tikka Masala" in block


def test_the_guidance_rides_in_the_cached_block():
    """The cost-critical invariant, and the one most likely to be broken by
    someone tidying later.

    `instructions` is identical on every call for every household and carries
    the cache_control breakpoint; `context_block` is the household's own JSON
    and is paid for in full every time. ~3,800 characters (~940 tokens) costs
    about $0.0002 per generated week where it is now. Moved into the dynamic
    block it would be charged at full input price on every generation, for
    every household, forever — the exact mistake the 2026-08-31 measurement
    caught and fixed.
    """
    for fn in (agent.generate_weekly_plan_llm, agent.generate_component_plan_llm):
        body = inspect.getsource(fn)
        ins_at = body.index('instructions = f"""')
        ctx_at = body.index("context_block")
        guidance_at = body.index("{COOK_DONT_ASSEMBLE}")
        assert ins_at < guidance_at < ctx_at, (
            "%s carries the guidance outside its cached instructions block"
            % fn.__name__
        )
        # And the block it sits in is genuinely the cached one.
        assert '"text": instructions, "cache_control"' in body


def test_the_rush_cap_is_interpolated_not_left_as_a_literal_brace():
    """The guidance names the rush cap, and it must name the real number.

    It sits inside an f-string, so `{rush_max}` renders — but a later edit
    that moves this text into a plain string would ship a literal "{rush_max}
    minutes" to the model, which reads as a broken template rather than a
    rule. tools.RUSH_MAX_MINUTES stays the single source of that number.
    """
    from app import tools
    shipped = _instructions_block()
    # This one caught a real bug while it was being written. Extracting the
    # guidance into a shared constant broke it: each prompt interpolates the
    # constant's TEXT into its own f-string, and an f-string does not
    # re-evaluate what it inserts — so a `{rush_max}` placeholder inside the
    # constant reached the model as those literal characters. The constant is
    # an f-string over tools.RUSH_MAX_MINUTES itself now.
    assert "{" not in shipped and "}" not in shipped, (
        "an unrendered template placeholder is being shipped to the model"
    )
    assert f"{tools.RUSH_MAX_MINUTES} minutes" in shipped, (
        "the guidance should name the real rush cap, from tools.RUSH_MAX_MINUTES"
    )


def test_both_planners_carry_it_not_just_the_day_based_one():
    """A component-based household plans by category rather than by day, down
    an entirely separate prompt — and it had the same conditional cuisine rule
    and the same gap. Fixing only the day-based planner would have left those
    households exactly where they started, which is the kind of half-fix
    plan_quality.py's own module docstring already records as a known gap in
    this codebase."""
    for fn in (agent.generate_weekly_plan_llm, agent.generate_component_plan_llm):
        assert "{COOK_DONT_ASSEMBLE}" in inspect.getsource(fn), (
            "%s never picks up the quality guidance" % fn.__name__
        )


def test_the_guidance_is_defined_once():
    """Two copies of a 900-token block drift, and nobody notices which one a
    household got."""
    import app.agent as mod
    source = inspect.getsource(mod)
    assert source.count("COOK_DONT_ASSEMBLE = ") == 1
