"""
The suite's own prompt-text helper: `conftest.prompt_literals()`, and its two
source-reading siblings `agent_source()` / `agent_function_source()`.

Why this file exists. Nine test files assert on the wording of the planner
prompts — the allergy rules, the plate rule, the anchor rule, the
recipe-quality guidance. Every one of them used to reach that wording through
`inspect.getsource(agent.<fn>)`, which pairs line numbers baked into the
function object at IMPORT time against app/agent.py as it reads on disk at the
moment the assertion runs. On 2026-09-15 that handed a test the right text
under the wrong function and turned the suite red for a reason that did not
exist.

`prompt_literals` reads `fn.__code__.co_consts` instead, so its answer is
fixed the moment the module is imported. That is the whole claim, and the
tests below are here to prove it rather than assert it — each one builds a
throwaway module in a tmp directory, imports it, then rewrites the file
underneath and checks what each helper says afterwards. Nothing here goes
anywhere near app/agent.py's own file: a test that edited the module the rest
of the suite is reading would be a worse bug than the one it is guarding.

`test_the_flake_this_helper_exists_for_is_real` is the reproduction. It swaps
two functions' positions in a file after import, which is exactly what a merge
landing mid-run does to a line number, and watches `inspect.getsource` return
the other function's body while `prompt_literals` stays right.
"""
import importlib.util
import inspect
import linecache
import sys

import pytest

from app import agent
from conftest import agent_function_source, agent_source, prompt_literals


ORIGINAL = '''
def first():
    return "FIRST FUNCTION SENTENCE"


def second():
    return "SECOND FUNCTION SENTENCE"
'''

# The same two functions, swapped. Every line number that used to point at one
# now points at the other — a torn or mid-merge file, in miniature.
SWAPPED = '''
def second():
    return "SECOND FUNCTION SENTENCE"


def first():
    return "FIRST FUNCTION SENTENCE"
'''

REPLACED = '''
def first():
    return "REWRITTEN ON DISK"


def second():
    return "ALSO REWRITTEN"
'''


@pytest.fixture
def throwaway_module(tmp_path):
    """
    A module compiled from a tmp file, plus a `rewrite(text)` that changes that
    file afterwards the way a merge would — linecache invalidated, so
    inspect.getsource genuinely re-reads rather than serving a stale cache.
    """
    path = tmp_path / "throwaway_prompt_module.py"
    path.write_text(ORIGINAL, encoding="utf-8")

    name = "throwaway_prompt_module_%d" % id(path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    def rewrite(text):
        path.write_text(text, encoding="utf-8")
        linecache.checkcache(str(path))
        linecache.clearcache()

    try:
        yield module, rewrite
    finally:
        sys.modules.pop(name, None)
        linecache.clearcache()


def test_the_flake_this_helper_exists_for_is_real(throwaway_module):
    """
    The reproduction, and the reason none of this is theoretical.

    Two functions trade places in the file after it has been imported.
    `inspect.getsource(first)` now returns `second`'s body — right text, wrong
    function, no error anywhere. That is the shape of the 2026-09-15 failure:
    a prompt-text assertion looking at a real function's real source and
    finding somebody else's words in it.
    """
    module, rewrite = throwaway_module
    assert "FIRST FUNCTION SENTENCE" in inspect.getsource(module.first)

    rewrite(SWAPPED)

    misattributed = inspect.getsource(module.first)
    assert "SECOND FUNCTION SENTENCE" in misattributed, (
        "the flake did not reproduce — if getsource has stopped pairing stale "
        "line numbers against fresh file text, this whole ticket is moot"
    )
    assert "FIRST FUNCTION SENTENCE" not in misattributed


def test_prompt_literals_is_unmoved_by_the_same_swap(throwaway_module):
    """The mirror of the test above: the same file, the same swap, the answer
    that does not change. This is the guard — break `prompt_literals` back into
    a getsource call and it fails here."""
    module, rewrite = throwaway_module
    rewrite(SWAPPED)

    assert prompt_literals(module.first) == "FIRST FUNCTION SENTENCE"
    assert prompt_literals(module.second) == "SECOND FUNCTION SENTENCE"


def test_prompt_literals_survives_the_file_being_rewritten_outright(throwaway_module):
    """Not just reordered — replaced. The compiled constant is the answer, so
    the text now on disk is not an input to it at all."""
    module, rewrite = throwaway_module
    rewrite(REPLACED)

    assert prompt_literals(module.first) == "FIRST FUNCTION SENTENCE"
    assert "REWRITTEN ON DISK" not in prompt_literals(module.first)


def test_prompt_literals_survives_the_file_being_deleted(tmp_path):
    """The strongest form of the claim: with no file left to read at all, the
    helper still answers and `inspect.getsource` cannot."""
    path = tmp_path / "vanishing_prompt_module.py"
    path.write_text(ORIGINAL, encoding="utf-8")
    name = "vanishing_prompt_module_%d" % id(path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        path.unlink()
        linecache.clearcache()

        assert prompt_literals(module.first) == "FIRST FUNCTION SENTENCE"
        with pytest.raises(OSError):
            inspect.getsource(module.first)
    finally:
        sys.modules.pop(name, None)
        linecache.clearcache()


# ---------- what it returns, for the real prompts ----------

def test_it_walks_into_nested_code_objects():
    """A comprehension or a nested def gets its own code object, and a prompt
    sentence living inside one must still be findable."""
    def outer():
        def inner():
            return "INNER SENTENCE"
        rows = [x.upper() for x in ("a",)]
        return inner, rows, "OUTER SENTENCE"

    text = prompt_literals(outer)
    assert "OUTER SENTENCE" in text
    assert "INNER SENTENCE" in text


def test_a_string_inside_a_CONSTANT_TUPLE_is_the_one_thing_it_cannot_see():
    """
    Characterising a known blind spot rather than leaving it to be found.

    `_walk` yields strings and recurses into code objects; a tuple or frozenset
    constant is neither, so `("A", "B")` written inline is invisible. It costs
    nothing today — measured on both planners, the only strings this misses are
    the keyword-name tuples the compiler stores for keyword calls
    (`model`, `max_tokens`, `cache_control` …), which are code, not prompt, and
    which the helper is better off not carrying: they would show up in the
    negative assertions as noise.

    Widen `_walk` if a prompt sentence ever moves into a tuple. Do not widen it
    to make this test pass.
    """
    def has_a_tuple():
        return ("SENTENCE IN A TUPLE",)

    assert "SENTENCE IN A TUPLE" not in prompt_literals(has_a_tuple)


def test_a_wrapped_line_reads_as_one_sentence():
    """
    The difference that made two files strip line-continuation backslashes by
    hand before asserting anything.

    agent.py wraps its prompt with line-continuation backslashes, so a
    sentence can straddle two source lines and be unfindable in getsource
    output while being perfectly findable in the compiled constant. Shown on a
    function defined right here, so the demonstration cannot go stale when
    somebody rewraps agent.py — and then checked against the real prompt, in
    the positive direction only, for the same reason.
    """
    def wrapped():
        return "a rule that is CAPPED AT THREE and wraps \
mid-sentence in the file"

    assert "CAPPED AT THREE and wraps mid-sentence" in prompt_literals(wrapped)
    assert "CAPPED AT THREE and wraps mid-sentence" not in inspect.getsource(wrapped)

    # And the real one this bought: test_produce_quantities asserts on it.
    assert "AT MOST 3" in prompt_literals(agent.generate_weekly_plan_llm)


def test_an_fstring_interpolation_is_not_a_literal():
    """
    The cost of the switch, stated where it can be checked.

    `{COOK_DONT_ASSEMBLE}` is a LOAD_GLOBAL, not a constant, so a test about
    WHERE that block is spliced cannot use prompt_literals — it is one of the
    three that use agent_function_source instead. If this ever stops being
    true, those three can move over.
    """
    fn = agent.generate_component_plan_llm
    assert "{COOK_DONT_ASSEMBLE}" in agent_function_source(fn.__name__)
    assert "{COOK_DONT_ASSEMBLE}" not in prompt_literals(fn)
    # And the constant's own text is not pulled in either — a prompt-wording
    # assertion about that block reads agent.COOK_DONT_ASSEMBLE directly,
    # which is what test_recipe_quality_guidance.py does.
    assert "COOK, DON'T ASSEMBLE" not in prompt_literals(fn)
    assert "COOK, DON'T ASSEMBLE" in agent.COOK_DONT_ASSEMBLE


# ---------- the source-reading siblings ----------

def _still_the_file_conftest_read():
    """
    The two tests below compare the cached read against a fresh one, which is
    only a meaningful comparison while the file has not moved under the run.
    If it has, that is the very thing this module is about — worth saying, not
    worth failing over, since the answer says nothing about the helper.
    """
    if agent_source() != inspect.getsource(agent):
        pytest.skip(
            "app/agent.py changed on disk since collection — nothing to "
            "compare, and every source-reading assertion in this run is "
            "suspect for the same reason"
        )


def test_agent_function_source_is_exactly_what_getsource_would_have_said():
    """
    The three structural tests must see the same bytes they saw before, or
    this ticket has quietly changed what they assert. Same text, different
    route to it: one cached read, sliced by parsing that same string.
    """
    _still_the_file_conftest_read()
    for name in ("generate_weekly_plan_llm", "generate_component_plan_llm"):
        assert agent_function_source(name) == inspect.getsource(getattr(agent, name))


def test_agent_source_is_the_module_read_once():
    """Two calls are one read — the point of caching it — and it is the whole
    file, which is what the `defined once` counts need."""
    assert agent_source() is agent_source()
    _still_the_file_conftest_read()
    assert agent_source() == inspect.getsource(agent)


def test_agent_function_source_refuses_a_name_it_cannot_find():
    """It raises rather than returning the nearest thing. A torn file that
    lost a def should stop a test, not silently hand it a neighbour's body —
    which is the original bug wearing a different hat."""
    with pytest.raises(ValueError, match="no top-level"):
        agent_function_source("a_function_that_is_not_in_agent_py")
