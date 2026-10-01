"""
A fault in a RECIPE is one line in the morning report, however many nights
that recipe is planned on.

Found 2026-10-01 in the live morning report, on the demo household:

    FOOD — 11 in the last 7d: 5 steps_match_ingredients,
           2 longest_thing_not_first, 2 weekday_lunch_cap_respected, ...
      info  2026-10-11 dinner ('Vietnamese Lemongrass Pork Chops ...'):
            the oven, water or rice is only started at step 5 of 10 ...
      info  2026-10-09 dinner ('Vietnamese Lemongrass Pork Chops ...'):
            the oven, water or rice is only started at step 5 of 10 ...

TWO of the section's SIX slots, for ONE recipe's step order, said twice
with nothing different in it but a date — and that section had eleven
findings and room for six, so the duplicate cost it a piece of news it had
space for.

WHY IT SURVIVED THE 2026-09-30 FIX, which is the whole of this. That
branch made the FOOD section collapse identical lines and counted the
nights beside them, and its own docstring states the rule it works by:
"a RECIPE-level rule ... writes a message that names the recipe and not
the night", and "a rule whose message already names its own night
(snack_echoes_a_meal, reasoning_is_specific) is unaffected". So the
MESSAGE is what decides. Three recipe-level rules obeyed that
(steps_match_ingredients, quantities_plausible, produce_variety_named —
the three the entry measured on) and SIX did not: every one of them led
its sentence with `{entry['date']} dinner ('{meal_name}')`, so no two
nights of one recipe could ever collapse.

**THE CARD SAID FOUR AND IT IS SIX** — counted mechanically off
`_RECIPE_RULES` rather than from the one rule the report happened to be
printing that morning: seasoning_never_mentioned, method_is_assembly,
steps_have_no_cue, no_heat_named, longest_thing_not_first,
minutes_vs_steps.

WHAT IS AND IS NOT LOST. The date leaves the SENTENCE and stays on the
Violation's own `date` field, so `usage._QUALITY_KEY` is still
`(rule, date, slot, message)` and two nights are still TWO violations in
`total` and in `by_rule`. Only the printed line collapses, and `(2
nights)` says how many. Driven end to end over a real database in
section 2 rather than reasoned from the key.

Nothing in `observability_report.py` changed. The reporter's one rule was
right; six messages were breaking it.
"""

from __future__ import annotations

import datetime
import ast
import inspect
import re
import textwrap
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import observability_report as rep  # noqa: E402

from app.tools import plan_quality as pq  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures: one dish, two nights, one fault each.
# --------------------------------------------------------------------------

DISH = "Vietnamese Lemongrass Pork Chops with Rice and Pickled Slaw"
OTHER = "Thai Basil Chicken Stir-Fry with Jasmine Rice"

# A method that is fine except for the ONE thing each rule is about, so a
# fixture aimed at one rule does not light up the other five and make a
# collapse look like it worked when two rules were being folded together.
GOOD_STEPS = [
    "Season the pork with salt and pepper.",
    "Start the rice.",
    "Heat the pan to medium-high.",
    "Sear the pork 4 minutes a side until well browned.",
    "Rest it 5 minutes, then slice.",
]


def _entry(date: str, name: str = DISH, **over) -> dict:
    entry = {
        "date": date,
        "slot": "dinner",
        "slot_state": "planned",
        "meal_name": name,
        "recipe_id": 7,
        "reasoning": "a reason that is specific to this night",
        "instructions": list(GOOD_STEPS),
        "food_groups": ["protein", "carb", "vegetable"],
        "prep_time_minutes": 15,
        "cook_time_minutes": 20,
        "ingredients": [
            {"item": "Pork chops", "qty": "2 lbs"},
            {"item": "Rice", "qty": "2 cups"},
            {"item": "Shallots", "qty": "2"},
        ],
    }
    entry.update(over)
    return entry


# Each of the six, with the one thing wrong that fires it. Keyed by rule
# name so a failure says which rule rather than which index.
FAULTS: dict[str, dict] = {
    "seasoning_never_mentioned": {
        "instructions": [
            "Put the pork in the pan.",
            "Cook it until it is done.",
            "Put the rice on a plate.",
        ],
    },
    "method_is_assembly": {
        "instructions": [
            "Put the pork in the pan with salt.",
            "Cook it 12 minutes until cooked through.",
            "Serve with the rice.",
        ],
    },
    "steps_have_no_cue": {
        "instructions": [
            "Season the pork with salt and pepper.",
            "Brown it in the pan.",
            "Slice it and serve with the rice.",
        ],
    },
    "no_heat_named": {
        "instructions": [
            "Season the pork with salt and pepper.",
            "Brown it in the pan for 8 minutes until caramelised.",
            "Slice it and serve with the rice.",
        ],
    },
    "longest_thing_not_first": {
        "instructions": [
            "Chop the shallots.",
            "Slice the pork and season it with salt.",
            "Mix the dressing with the vinegar.",
            "Pickle the slaw for 10 minutes.",
            "Start the rice.",
            "Heat the pan to high.",
            "Sear the pork 4 minutes a side until browned.",
            "Rest it 5 minutes.",
            "Slice it.",
            "Plate it with the rice.",
        ],
    },
    "minutes_vs_steps": {
        "prep_time_minutes": 5,
        "cook_time_minutes": 8,
        "instructions": [
            "Season the pork with salt and pepper.",
            "Heat the pan to medium-high.",
            "Start the rice.",
            "Chop the shallots.",
            "Mix the dressing with vinegar.",
            "Pickle the slaw 10 minutes.",
            "Sear the pork until browned.",
            "Rest it 5 minutes.",
            "Slice it.",
            "Plate it with the rice.",
        ],
    },
}

RULE_FN = {
    "seasoning_never_mentioned": pq._seasoning_never_mentioned,
    "method_is_assembly": pq._method_is_assembly,
    "steps_have_no_cue": pq._steps_have_no_cue,
    "no_heat_named": pq._no_heat_named,
    "longest_thing_not_first": pq._longest_thing_not_first,
    "minutes_vs_steps": pq._minutes_vs_steps,
}

TWO_NIGHTS = ("2026-10-09", "2026-10-11")


def _fire(rule: str, entries: list[dict]) -> list:
    """That one rule's violations, so a fixture aimed at it cannot be
    satisfied by a different rule firing instead."""
    return [v for v in RULE_FN[rule](entries, {}) if v.rule == rule]


def _printed(violations) -> list[str]:
    """What the FOOD section would print for these violations, through the
    reporter's own two functions rather than a copy of their rule."""
    recent = [
        {"rule": v.rule, "severity": v.severity, "date": v.date,
         "slot": v.slot, "message": v.message}
        for v in violations
    ]
    return [rep._food_line_text(*line) for line in rep._food_lines(recent)]


# --------------------------------------------------------------------------
# 1. The reported defect, for each of the six
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rule", sorted(FAULTS))
def test_one_recipes_fault_on_two_nights_is_one_line(rule):
    """
    CATCH, and the reproduction. Two nights of ONE recipe with ONE fault
    print ONE line, with the night count beside it. Against main each of
    these prints two lines that differ only in a date.

    The fixture is checked to have fired the named rule TWICE first, so a
    rule that stopped firing could never make this pass by collapsing
    nothing.
    """
    entries = [_entry(d, **FAULTS[rule]) for d in TWO_NIGHTS]
    found = _fire(rule, entries)
    assert len(found) == 2, f"{rule} did not fire on both nights: {found}"

    lines = _printed(found)
    assert len(lines) == 1, f"{rule} still spends a line per night: {lines}"
    assert "(2 nights)" in lines[0], lines[0]
    assert DISH in lines[0]
    for night in TWO_NIGHTS:
        assert night not in lines[0], (
            f"{rule} still names a night the fault has nothing to do with"
        )


@pytest.mark.parametrize("rule", sorted(FAULTS))
def test_two_different_recipes_with_the_same_fault_still_get_a_line_each(rule):
    """
    GUARD, and the half that stops the fix going too far: the collapse is
    per RECIPE, not per rule. Two dishes each written badly in the same way
    are two things to fix and must stay two lines. Pinned by the mutation
    that drops the dish name from the message as well as the date — then
    this reads 1 and the test above still reads 1.
    """
    entries = [_entry(TWO_NIGHTS[0], **FAULTS[rule]),
               _entry(TWO_NIGHTS[1], name=OTHER, **FAULTS[rule])]
    found = _fire(rule, entries)
    assert len(found) == 2, f"{rule} did not fire on both dishes: {found}"
    lines = _printed(found)
    assert len(lines) == 2, f"two different dishes collapsed into one: {lines}"
    assert not any("nights)" in line for line in lines)


def test_the_six_sentences_stay_distinct_from_each_other():
    """
    GUARD on the one risk this fix CREATES, rather than on the fix.

    `_food_lines` keys on (severity, message) and NOT on the rule — so two
    rules whose messages became identical would fold into one line and one
    of the two findings would vanish from the report. While the date led
    every sentence they could not collide; without it they are six
    sentences about one dish, and two of them open with the same seven
    words ("the method uses an oven or a pan but never ...").

    Measured: all six are distinct today. This fails on any reword that
    makes two of them the same.
    """
    sentences = {}
    for rule in sorted(FAULTS):
        found = _fire(rule, [_entry(TWO_NIGHTS[0], **FAULTS[rule])])
        assert len(found) == 1, f"{rule} did not fire once: {found}"
        sentences.setdefault(found[0].message, []).append(rule)
    collided = {m: rules for m, rules in sentences.items() if len(rules) > 1}
    assert collided == {}, (
        "two recipe rules now write the same sentence, so the FOOD section "
        f"would print one of them and silently drop the other: {collided}"
    )


# --------------------------------------------------------------------------
# 2. Nothing is lost: the counts, over a real database
# --------------------------------------------------------------------------

def test_two_nights_are_still_two_findings_in_the_counts():
    """
    CATCH on the thing a reader of this change would reasonably fear: that
    taking the date out of the sentence makes two nights of one recipe
    indistinguishable, so the report's own TOTAL drops.

    It does not, and this is driven rather than argued from the key:
    `usage._QUALITY_KEY` is (rule, date, slot, message) and the Violation
    still carries its own `date`. So two nights are two rows, `total` is 2,
    `by_rule` is 2 — and the section prints one line saying "(2 nights)".

    RED AGAINST MAIN AND NOT FOR THE REASON IT IS NAMED FOR, which is said
    here rather than left to be counted: measured, every one of its four
    COUNT assertions passes on main — the count was never the problem —
    and it fails on the fifth, the collapsed line, which is the same claim
    the six parametrized catches above make. So it is one behaviour catch
    plus four assertions that are green either way, and it earns its place
    by pinning the half the change rests on, which nothing pinned before.
    """
    from app import tools
    from app.db import get_conn
    from app.tools import usage
    from conftest import household_today

    # The HOUSEHOLD's clock, never the process's — CLAUDE.md's own gotcha,
    # and the first cut of this test broke it. Harmless as it stood (nothing
    # here compares the seeded date against a clock: the window is on
    # created_at and the plan is forced to approved by hand, measured green
    # in two verified straddles) and it is the shape the rule names, so it
    # is not left as the example a later test copies.
    today = household_today()
    created = tools.create_weekly_plan(today.isoformat(), day_count=7)
    plan_id = created["weekly_plan_id"]
    conn = get_conn()
    try:
        conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?",
                     (plan_id,))
        conn.commit()
    finally:
        conn.close()

    nights = [(today + datetime.timedelta(days=n)).isoformat() for n in (1, 3)]
    entries = [_entry(d, **FAULTS["longest_thing_not_first"]) for d in nights]
    found = _fire("longest_thing_not_first", entries)
    assert len(found) == 2
    usage.record_plan_quality(plan_id, found)

    got = usage.get_recent_plan_quality(days=7)
    assert got["total"] == 2, got
    assert got["by_rule"]["longest_thing_not_first"] == 2, got["by_rule"]
    assert len(got["recent"]) == 2, got["recent"]
    assert {r["date"] for r in got["recent"]} == set(nights)

    lines = [rep._food_line_text(*line) for line in rep._food_lines(got["recent"])]
    assert len(lines) == 1 and "(2 nights)" in lines[0], lines


# --------------------------------------------------------------------------
# 3. A per-night rule still names its night
# --------------------------------------------------------------------------

def test_a_rule_about_the_NIGHT_still_names_the_night():
    """
    GUARD, and the reason this is six messages rather than a sweep over
    every rule in the module. `_full_plate` reads the ENTRY's own
    `food_groups` — the plate planned for that night, which a side can
    change on one night and not another — so its date is a fact about the
    night and has to stay. Two nights of it are two lines, as they should
    be.

    It is the seventh `{date} dinner (...)` in plan_quality and the one
    deliberately left. Pinned so a later pass that strips dates by grep
    rather than by meaning goes red here.
    """
    entries = [_entry(d, food_groups=["carb"]) for d in TWO_NIGHTS]
    found = [v for v in pq._full_plate(entries, {}) if v.rule == "full_plate"]
    assert len(found) == 2
    lines = _printed(found)
    assert len(lines) == 2, f"full_plate collapsed two nights into one: {lines}"
    for night, line in zip(TWO_NIGHTS, sorted(lines)):
        assert night in line


def test_the_two_rules_that_name_their_own_night_are_unaffected():
    """
    GUARD. The 2026-09-30 entry names snack_echoes_a_meal and
    reasoning_is_specific as the rules whose messages name a night on
    purpose. Nothing here touches them, and the sweep below must not
    report them — so this pins that their sentences still carry a date.
    """
    for fn, token in ((pq._snack_variety, "clash['date']"),
                      (pq._reasoning_is_specific, "entry['date']")):
        src = inspect.getsource(fn)
        assert f'f"{{{token}}}' in src, (
            f"{fn.__name__} no longer leads its sentence with its own date"
        )


# --------------------------------------------------------------------------
# 4. The sweep, derived from the module's own list
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# The sweep's reader. ast, never a line regex — this repo's own log
# prescribes that twice for exactly this class ("THE FIRST SWEEP WAS
# DEFEATED BY A PURE REFORMAT … It is `ast` now, which is both shorter and
# right", 2026-09-24; "with `ast`, never grepped", 2026-09-26) and the
# first cut of this file ignored both. Measured by an adversarial review:
# a line regex for f"{entry['date']} caught ONE of eight plausible
# spellings of the same defect and missed seven — a local variable,
# entry.get('date'), a triple-quoted f-string, .format(), one leading
# space, a single-quoted outer f-string, and + concatenation — while a
# mere mention of the shape in a DOCSTRING reddened it over correct code,
# which is how a guard gets switched off. ast is blind to comments and
# docstrings by construction and sees every one of those spellings.
# --------------------------------------------------------------------------

def _is_entry_date(node, aliases) -> bool:
    """entry['date'], entry.get('date'), or a local name assigned from one."""
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Subscript):
        return (isinstance(node.value, ast.Name) and node.value.id == "entry"
                and isinstance(node.slice, ast.Constant) and node.slice.value == "date")
    if isinstance(node, ast.Call):
        f = node.func
        return (isinstance(f, ast.Attribute) and f.attr == "get"
                and isinstance(f.value, ast.Name) and f.value.id == "entry"
                and len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "date")
    return False


def _date_aliases(tree) -> set:
    """Local names this function assigns the entry's date to."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_entry_date(node.value, out):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, ast.AnnAssign) and node.value is not None \
                and _is_entry_date(node.value, out):
            if isinstance(node.target, ast.Name):
                out.add(node.target.id)
    return out


def _leads_with_the_date(node, aliases) -> bool:
    """Does this expression's rendered text OPEN with the entry's date?"""
    if node is None:
        return False
    if _is_entry_date(node, aliases):
        return True
    if isinstance(node, ast.JoinedStr):
        for part in node.values:
            if isinstance(part, ast.Constant):
                if isinstance(part.value, str) and part.value.strip() == "":
                    continue          # one leading space is still leading
                return False
            if isinstance(part, ast.FormattedValue):
                return _is_entry_date(part.value, aliases)
            return False
        return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _leads_with_the_date(node.left, aliases)
    if isinstance(node, ast.Call):
        f = node.func
        # "{} dinner ...".format(entry['date'], ...)
        if isinstance(f, ast.Attribute) and f.attr == "format" and node.args:
            lead = f.value
            if isinstance(lead, ast.Constant) and isinstance(lead.value, str) \
                    and lead.value.lstrip().startswith("{"):
                return _is_entry_date(node.args[0], aliases)
        return False
    return False


def _messages_in(src: str):
    """Every `message=` expression in this source, with its line number."""
    tree = ast.parse(textwrap.dedent(src))
    aliases = _date_aliases(tree)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "message":
                    out.append((kw.value, aliases, node.lineno))
    return out


def _messages_of(fn):
    """The same, for a real rule."""
    return _messages_in(inspect.getsource(fn))


def test_no_recipe_level_rule_leads_its_sentence_with_a_date():
    """
    THE GUARD THAT MAKES THIS STICK, and it is DERIVED rather than
    hand-listed: `plan_quality._RECIPE_RULES` is the module's own register
    of the rules re-run once the recipe pass has written the ingredients
    and the steps (2026-09-21) — i.e. of exactly the rules whose findings
    are about a recipe. So a twelfth recipe rule added tomorrow is covered
    without anybody editing this file.

    It reads the SOURCE and not a driven message, deliberately: several of
    the eleven need a fixture of their own to fire at all, and a sweep that
    only checks the rules it happens to have fixtures for is a sweep with
    holes in it. It reads it with ast — see the note above this test for
    the eight spellings that measurement forced, seven of which a line
    regex missed.
    """
    offenders = []
    for fn in pq._RECIPE_RULES:
        for message, aliases, lineno in _messages_of(fn):
            if _leads_with_the_date(message, aliases):
                offenders.append(f"{fn.__name__}:{lineno}")
    assert offenders == [], (
        "a recipe-level rule leads its sentence with a date, so two nights "
        f"of one recipe will spend two lines of the FOOD section: {offenders}"
    )


def test_the_sweep_sees_every_spelling_of_the_defect_and_no_prose():
    """
    GUARD ON THE READER, which is what the line regex did not have. The
    eight spellings a review wrote to defeat the first version, plus the
    docstring mention that made it cry wolf over correct code, driven
    against the reader directly rather than by editing plan_quality.
    """
    def read(src: str) -> bool:
        return any(_leads_with_the_date(m, a) for m, a, _ in _messages_in(src))

    caught = {
        "the shipped shape": '''
            def _rule(entry):
                return Violation(message=f"{entry['date']} dinner ('x'): no.")
        ''',
        "a local variable": '''
            def _rule(entry):
                day = entry["date"]
                return Violation(message=f"{day} dinner ('x'): no.")
        ''',
        "entry.get": '''
            def _rule(entry):
                return Violation(message=f"{entry.get('date')} dinner: no.")
        ''',
        "a triple-quoted f-string": '''
            def _rule(entry):
                return Violation(message=f"""{entry['date']} dinner: no.""")
        ''',
        "str.format": '''
            def _rule(entry):
                return Violation(message="{} dinner: no.".format(entry["date"]))
        ''',
        "one leading space": '''
            def _rule(entry):
                return Violation(message=f" {entry['date']} dinner: no.")
        ''',
        "a single-quoted outer f-string": '''
            def _rule(entry):
                return Violation(message=f'{entry["date"]} dinner: no.')
        ''',
        "+ concatenation": '''
            def _rule(entry):
                return Violation(message=entry["date"] + " dinner: no.")
        ''',
    }
    missed = [name for name, src in caught.items() if not read(src)]
    assert missed == [], f"the reader cannot see these spellings of the defect: {missed}"

    passed_over = {
        "the dish name, which is the fix": '''
            def _rule(entry):
                return Violation(message=f"{entry['meal_name']}: no.")
        ''',
        "a docstring naming the shape": '''
            def _rule(entry):
                """Do not write f"{entry['date']} dinner (...)" here."""
                return Violation(message=f"{entry['meal_name']}: no.")
        ''',
        "a comment naming the shape": '''
            def _rule(entry):
                # never f"{entry['date']} dinner (...)"
                return Violation(message=f"{entry['meal_name']}: no.")
        ''',
        "the date later in the sentence": '''
            def _rule(entry):
                return Violation(message=f"{entry['meal_name']} on {entry['date']}: no.")
        ''',
    }
    wrong = [name for name, src in passed_over.items() if read(src)]
    assert wrong == [], f"the reader cries wolf over correct code: {wrong}"


def test_the_sweep_can_see_all_eleven_recipe_rules():
    """
    GUARD ON THE GUARD. A sweep that silently stops finding the rules
    passes for ever; this repo has recorded that failure more than once.
    Eleven today, and the floor is what fails if the register is emptied or
    the import drifts.
    """
    assert len(pq._RECIPE_RULES) >= 11, pq._RECIPE_RULES
    names = {fn.__name__ for fn in pq._RECIPE_RULES}
    assert {f"_{r}" for r in FAULTS} <= names, sorted(names)


def test_every_one_of_the_six_is_still_a_recipe_rule():
    """
    GUARD. The six are fixed because they are recipe-level; if one of them
    ever leaves `_RECIPE_RULES` — becomes a week-level rule, say — then the
    argument for taking its date away has gone and this says so.
    """
    names = {fn.__name__ for fn in pq._RECIPE_RULES}
    for rule in sorted(FAULTS):
        assert f"_{rule}" in names, rule
