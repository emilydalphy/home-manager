"""
A double-batch dinner says so up top (card 9, 2026-10-05).

Gowthami's household, 2026-10-04: "It needs to call out that it's double
the quantity because its calling for leftovers."

MEASURED BEFORE ANYTHING WAS TOUCHED, on a throwaway DB: a household of
three with a Day-0 dinner chained to Day-1's lunch gets, from
get_cooker_view, `servings: 6` and ingredients reading "1.5 lbs" of beef,
under a "Cooking for 6" stepper — and the ONE sentence that said why,
`covers_note` ("Cooking for 6 — covers tonight and leftovers on Tuesday."),
is rendered on NO client surface at all. `grep covers_note static/shell.js`
finds it twice and NEITHER is a render: one is the boolean `was_batch`
inside cookApplyServesOverride, the other the comment two lines above it,
which says in as many words that "the recipe screen no longer shows it". So the card's premise is understated: the cook was not
reading a small note under a big list, there was no note. Doubled amounts,
nothing explaining them.

AFTER: the recipe screen, Cook's Tonight card and cook mode's first and
last steps all say it, out of ONE read (leftovers._batch_parts), and the
ingredients heading carries the batch size.

WHAT THE NUMBERS ARE. The chain's, never the stepper's: this night's table
and each leftover night's (attendance), the day and meal of each, plus any
portions put by for the freezer (FREEZER_EXTRA_KEY). A cook who taps the
stepper is deciding how much to make; what the week is FOR does not move
with it — so the line holds still while the ingredients heading follows
the amounts under it. Both are deliberate and both are pinned here.

"ABOUT 2x" IS leftovers.DOUBLE_BATCH_RATIO_MIN/MAX (1.75–2.5), the one
place the word is decided. 3 at the table cooking 6 is 2.00x and 7 is
2.33x — both read as double to anybody holding the pot; 9 is three times
the table and is a "Big batch", because calling that double would be the
app saying a thing that isn't true (§8). Reverse it by changing those two
numbers; nothing else reads them.

THE STEPPER IS UNTOUCHED. Emily, 2026-10-04: "I like how we had a servings
adjustments before." `git diff` touches neither recipeServesHtml nor
cookStepServings nor .recipe-serves, and there is a test below that says so
by reading the source.

TWENTY-TWO MUTATIONS RUN, red counts read off the runs over this file.
Twenty-one bite; the one that does not is written down with its reason
rather than dropped.

  the no-extra guard removed, so a cook with nothing beyond its own
    table gets the line                                              1
  every card given the sentences, batch or not                       3
  the line's numbers made to follow the cook's stepper               1
  the day and the meal dropped from a leftover's clause             11
  the freezer clause dropped                                         3
  the "about 2x" band inverted                                      13
  the line copied onto the reheat night                              1
  the old covers_note printed beside it                              1
  the client never renders the line                                  3
  the ingredients heading drops the batch size                       2
  the heading frozen at the chain instead of the stepper             2
  "half" said whenever there is any leftover night                   1
  Cook's Tonight card stops saying it                                1
  cook mode's first and last step sentences dropped                  1
  the cook night always reads "tonight"                              2
  "tomorrow" measured from today rather than the cook night          2
  the same-day clause removed                                        1
  the tally joined with _join_days' "and" rather than a comma        7
  servings loses its singular ("1 servings")                         1
  the sentence interpolated raw rather than escaped                  1
  the freezer-only cook loses its sentences (the 2nd call site)      1
  the three keys written even when the sentence is empty             0

TWO OF THOSE WERE BADLY AIMED ON THE FIRST RUN and are recorded rather
than quietly re-run, because one of them found a real hole here.
"Every card given the sentences" first handed _set_batch_sentences a
shape with no targets and no freezer — which _batch_parts refuses — so
it wrote nothing and reddened 0: a badly chosen mutation, not a
toothless test. Re-aimed with a real batch shape it reddens 3. "The old
covers_note printed beside it" reddened 0 as well, and that one WAS a
hole: the card's own "the batch is said ONCE" rule was pinned only one
file over, in test_recipe_screen.py's banned-string tripwire.
test_the_batch_is_said_once_and_the_old_note_is_not_said_beside_it was
added for it and the mutation now reddens 1.

AND THE ONE THAT DOES NOT BITE, with its reason: writing the three keys
as "" rather than leaving them out changes NO screen, because every
client reads them with a falsy check — so there is no household-visible
difference for a test to catch. The two "says nothing" tests below assert
`key not in card`, and neither a plain dinner nor a reheat reaches
_set_batch_sentences at all, so the mutation cannot reach them either.
"Absent rather than empty" is a claim about the payload's shape, kept
because a future reader should not find a key that means nothing; it is
not something a behaviour test can hold up.
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import pytest

import nodeharness
from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import cooker as ck
from app.tools import leftovers as lo

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the screen's own functions"
)

TODAY = household_today()


def _day(n: int) -> str:
    return (TODAY + datetime.timedelta(days=n)).isoformat()


D0, D1, D2, D3 = _day(0), _day(1), _day(2), _day(3)


# ===================================================== the sentence itself
# Driven straight at the builders, because the card's arithmetic is the
# risk and a DB round trip would only hide which number came from where.
# `source` here is a chains["sources"] entry and `batch` is what
# batch_for_source answered — the exact pair cooker.py hands over.

def _src(date=D0, slot="dinner", **kw):
    return {"date": date, "slot": slot, "targets": [], **kw}


def _batch(cook, targets=(), freezer=0):
    out = {
        "servings": cook + sum(t[2] for t in targets) + freezer,
        "cook_eaters": cook,
        "targets": [{"date": d, "slot": s, "eaters": n} for d, s, n in targets],
    }
    if freezer:
        out["freezer"] = freezer
    return out


def test_a_chained_dinner_says_double_batch_with_the_chains_numbers():
    """The card's first test, and its own example sentence shape."""
    said = lo.batch_line(_src(), _batch(3, [(D1, "lunch", 3)]), today=D0)
    assert said == "Double batch: 3 tonight, 3 for lunch tomorrow."


def test_a_plain_dinner_says_nothing():
    """The card's second test. A dinner for its own table is not a batch,
    and the field is ABSENT rather than empty on the card, so every client
    reads it with one falsy check."""
    assert lo.batch_line(_src(), _batch(3), today=D0) == ""
    assert lo.batch_first_step_note(_src(), _batch(3), today=D0) == ""
    assert lo.batch_last_step_note(_src(), _batch(3), today=D0) == ""


def test_a_leftover_that_is_not_tomorrow_names_the_day_and_the_meal():
    said = lo.batch_line(_src(), _batch(4, [(D3, "lunch", 4)]), today=D0)
    assert said == f"Double batch: 4 tonight, 4 for {lo._weekday(D3)}’s lunch."
    # the MEAL as well as the day — a Wednesday dinner and a Wednesday
    # lunch are different containers.
    dinner = lo.batch_line(_src(), _batch(4, [(D3, "dinner", 4)]), today=D0)
    assert dinner == f"Double batch: 4 tonight, 4 for {lo._weekday(D3)}’s dinner."


def test_a_freezer_portion_adds_its_clause():
    said = lo.batch_line(_src(), _batch(3, [(D1, "lunch", 3)], freezer=2), today=D0)
    assert said.endswith(", plus 2 for the freezer.")
    assert "3 for lunch tomorrow" in said


def test_a_cook_whose_only_extra_is_the_freezer_still_says_it():
    """A night off's portion (FREEZER_EXTRA_KEY) with no leftover night:
    the amounts are still double and still need explaining."""
    said = lo.batch_line(_src(), _batch(3, freezer=3), today=D0)
    assert said == "Double batch: 3 tonight, plus 3 for the freezer."


def test_one_and_a_half_times_is_a_big_batch_not_a_double():
    said = lo.batch_line(_src(), _batch(3, [(D1, "lunch", 2)]), today=D0)
    assert said.startswith("Big batch: ")
    assert said == "Big batch: 3 tonight, 2 for lunch tomorrow."


def test_three_times_the_table_is_a_big_batch():
    said = lo.batch_line(_src(), _batch(3, [(D1, "lunch", 3), (D2, "lunch", 3)]), today=D0)
    assert said == (
        f"Big batch: 3 tonight, 3 for lunch tomorrow, and 3 for {lo._weekday(D2)}’s lunch."
    )


def test_the_band_is_the_two_named_constants_and_nothing_else_decides_it():
    """2.00x and 2.33x both read as double; 1.5x and 3x do not. The word is
    DOUBLE_BATCH_RATIO_MIN..MAX and that pair is the one lever."""
    assert lo.DOUBLE_BATCH_RATIO_MIN == 1.75
    assert lo.DOUBLE_BATCH_RATIO_MAX == 2.5
    for cook, extra, want in ((3, 3, "Double"), (3, 4, "Double"), (3, 2, "Big"), (3, 6, "Big")):
        said = lo.batch_line(_src(), _batch(cook, [(D1, "lunch", extra)]), today=D0)
        assert said.startswith(want + " batch:"), (cook, extra, said)


def test_the_cook_night_is_named_tonight_only_when_it_really_is_today():
    """covers_note's own rule (§8: time the way a person would say it) —
    the same card read on Sunday must not call Thursday "tonight"."""
    said = lo.batch_line(_src(date=D2), _batch(3, [(D3, "lunch", 3)]), today=D0)
    assert said == f"Double batch: 3 for {lo._weekday(D2)}, 3 for lunch tomorrow."


def test_tomorrow_is_relative_to_the_cook_night_not_to_today():
    """The sentence is about this batch, so "tomorrow" means the day after
    it is cooked wherever the screen is read from."""
    said = lo.batch_line(_src(date=D2), _batch(3, [(D3, "lunch", 3)]), today=D0)
    assert "for lunch tomorrow" in said


def test_a_lunch_cooked_big_for_that_evening_does_not_say_tomorrow():
    """_eaten_order allows a lunch to feed that night's dinner. "tomorrow"
    there would be plainly wrong."""
    said = lo.batch_line(_src(slot="lunch"), _batch(3, [(D0, "dinner", 3)]), today=D0)
    assert said == "Double batch: 3 tonight, 3 for dinner later today."
    # read from another day, the same night is "the same day"
    other = lo.batch_line(_src(date=D2, slot="lunch"), _batch(3, [(D2, "dinner", 3)]), today=D0)
    assert "for dinner the same day" in other


def test_the_tally_takes_a_comma_for_two_and_an_and_for_three():
    """The card's locked copy is "4 tonight, 4 for Tuesday's lunch" — a
    tally of one batch's shares, not two things in a sentence."""
    two = lo.batch_line(_src(), _batch(3, [(D1, "lunch", 3)]), today=D0)
    assert ", 3 for lunch tomorrow." in two and " and 3 for lunch tomorrow" not in two
    three = lo.batch_line(_src(), _batch(3, [(D1, "lunch", 3), (D2, "lunch", 3)]), today=D0)
    assert ", and 3 for" in three


def test_nothing_countable_says_nothing():
    """A household with nobody on record: the same silence covers_note's
    own servings<=0 fallback gives."""
    assert lo.batch_line(_src(), _batch(0), today=D0) == ""
    assert lo.batch_line(_src(), {"servings": 0, "cook_eaters": 0, "targets": []}, today=D0) == ""


def test_cook_modes_first_step_says_half_only_when_it_is_half():
    half = lo.batch_first_step_note(_src(), _batch(3, [(D1, "lunch", 3)]), today=D0)
    assert half == "Double batch: half goes in containers for lunch tomorrow."
    # two leftover nights is not half, so it names the servings
    thirds = lo.batch_first_step_note(
        _src(), _batch(3, [(D1, "lunch", 3), (D2, "lunch", 3)]), today=D0)
    assert thirds == (
        f"Big batch: 6 servings go in containers for lunch tomorrow and {lo._weekday(D2)}’s lunch."
    )
    # and an uneven share is not half either
    uneven = lo.batch_first_step_note(_src(), _batch(3, [(D1, "lunch", 4)]), today=D0)
    assert uneven == "Double batch: 4 servings go in containers for lunch tomorrow."


def test_cook_modes_last_step_is_the_packing_job():
    said = lo.batch_last_step_note(_src(), _batch(3, [(D1, "lunch", 3)]), today=D0)
    assert said == "Pack 3 servings for lunch tomorrow."
    frozen = lo.batch_last_step_note(_src(), _batch(3, freezer=4), today=D0)
    assert frozen == "Pack 4 servings for the freezer."
    both = lo.batch_last_step_note(_src(), _batch(3, [(D1, "lunch", 3)], freezer=2), today=D0)
    assert both == "Pack 5 servings for lunch tomorrow and the freezer."


def test_one_serving_is_singular_everywhere_it_is_said():
    """These land in a sentence read at the stove."""
    assert lo.batch_last_step_note(_src(), _batch(1, freezer=1), today=D0) == (
        "Pack 1 serving for the freezer.")
    assert lo.batch_first_step_note(_src(), _batch(2, freezer=1), today=D0) == (
        "Big batch: 1 serving goes in containers for the freezer.")


def test_all_three_sentences_come_out_of_one_read():
    """Three surfaces, one source of numbers. Two copies of one sentence is
    this codebase's named recurring bug generator — so the day, the meal
    and the servings in the step notes are the line's own."""
    src, batch = _src(), _batch(4, [(D3, "lunch", 4)])
    line = lo.batch_line(src, batch, today=D0)
    first = lo.batch_first_step_note(src, batch, today=D0)
    last = lo.batch_last_step_note(src, batch, today=D0)
    day = f"{lo._weekday(D3)}’s lunch"
    assert day in line and day in first and day in last
    assert line.startswith("Double batch") and first.startswith("Double batch")


# ======================================================= on the real cards

def _week():
    """A household of three: Day 0's dinner cooks double for Day 1's lunch,
    plus a plain dinner for the control. Built through the real tools; only
    the chain's two halves are written by hand, exactly as
    repair_leftover_chains writes them."""
    for name in ("Emily", "Vineeth", "Reid"):
        tools.add_member(name)
    plan = tools.create_weekly_plan(D0, day_count=7)["weekly_plan_id"]
    tools.add_recipe(
        "Beef Chilli",
        [{"item": "Ground beef", "qty": "1 lb", "category": "meat"}],
        instructions=["Brown the beef.", "Simmer 25 minutes.", "Taste and season."],
        prep_time_minutes=10, cook_time_minutes=35, default_servings=4)
    tools.add_recipe(
        "Omelette", [{"item": "Eggs", "qty": "6", "category": "dairy"}],
        instructions=["Beat the eggs.", "Cook in a pan."],
        prep_time_minutes=5, cook_time_minutes=10, default_servings=4)
    tools.plan_meal(D0, "Beef Chilli", "dinner", weekly_plan_id=plan,
                    add_ingredients_to_grocery_list=False)
    tools.plan_meal(D1, "Beef Chilli", "lunch", weekly_plan_id=plan,
                    add_ingredients_to_grocery_list=False)
    tools.plan_meal(D1, "Omelette", "dinner", weekly_plan_id=plan,
                    add_ingredients_to_grocery_list=False)
    conn = get_conn()
    rows = {(r["date"], r["slot"]): r["id"] for r in conn.execute(
        "SELECT id, date, slot FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan,))}
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"make_double_for": [f"{D1}:lunch"],
                              "make_double_note": "Cooking double."}), rows[(D0, "dinner")]))
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"links_to": f"{D0}:dinner"}), rows[(D1, "lunch")]))
    conn.commit()
    conn.close()
    return plan, rows


def _cards(plan):
    return {(m["date"], m["slot"]): m for m in ck.get_cooker_view(weekly_plan_id=plan)["meals"]}


def test_the_cooker_view_carries_all_three_sentences_on_the_source():
    plan, _ = _week()
    cards = _cards(plan)
    src = cards[(D0, "dinner")]
    assert src["batch_line"] == "Double batch: 3 tonight, 3 for lunch tomorrow."
    assert src["batch_first_step"] == (
        "Double batch: half goes in containers for lunch tomorrow.")
    assert src["batch_last_step"] == "Pack 3 servings for lunch tomorrow."
    # and the batch really is scaled, which is what the sentence explains
    assert src["servings"] == 6


def test_the_reheat_night_says_nothing():
    """A reheat is not a cook, and this app is careful about that
    everywhere. The line belongs on the night that cooks double."""
    plan, _ = _week()
    reheat = _cards(plan)[(D1, "lunch")]
    assert reheat["is_leftovers"] is True
    for key in ("batch_line", "batch_first_step", "batch_last_step"):
        assert key not in reheat, key


def test_a_plain_dinner_on_the_same_week_says_nothing():
    plan, _ = _week()
    plain = _cards(plan)[(D1, "dinner")]
    for key in ("batch_line", "batch_first_step", "batch_last_step"):
        assert key not in plain, key


def test_a_chain_only_one_half_of_which_agrees_says_nothing():
    """plan_leftover_chains honours a pairing only when BOTH halves agree
    (its module docstring is explicit that this is deliberate). An
    unvalidated plan must behave exactly as it did — so no line."""
    plan, rows = _week()
    conn = get_conn()
    # take the SOURCE's half away; the leftover still points back
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = '{}' WHERE id = ?",
                 (rows[(D0, "dinner")],))
    conn.commit()
    conn.close()
    cards = _cards(plan)
    assert "batch_line" not in cards[(D0, "dinner")]
    # ...and the night that only reheats is an ordinary cook again, as before
    assert not cards[(D1, "lunch")].get("is_leftovers")


def test_a_freezer_only_cook_gets_the_line_through_the_same_helper():
    """A night off's portion (FREEZER_EXTRA_KEY) is a batch too, and
    batch_for_entry is the one place that says so."""
    plan, rows = _week()
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({lo.FREEZER_EXTRA_KEY: {"servings": 3}}), rows[(D1, "dinner")]))
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = '{}' WHERE id = ?",
                 (rows[(D1, "lunch")],))
    conn.commit()
    conn.close()
    card = _cards(plan)[(D1, "dinner")]
    assert card["batch_line"] == "Double batch: 3 for %s, plus 3 for the freezer." % (
        lo._weekday(D1))
    assert card["batch_last_step"] == "Pack 3 servings for the freezer."


def test_the_old_covers_note_is_still_there_for_its_other_readers():
    """covers_note has two readers left and neither is a client surface:
    moves.py's Today reason line and agent.py's prep-schedule context.
    This card does not touch either, so the sentence stays — what changes
    is that the three COOK surfaces now say the batch in their own words
    rather than in none."""
    plan, _ = _week()
    src = _cards(plan)[(D0, "dinner")]
    assert src["covers_note"].startswith("Cooking for 6 — covers tonight")


@pytest.mark.live_clock("holds its own instant: the server and the household must be on different days")
def test_tonight_is_the_households_tonight_not_the_servers():
    """cook-card-household-day (2026-10-05). 01:00 UTC Monday is 21:00 Sunday
    in Toronto: the server is on Monday, the household is still on Sunday.
    With no `today` given, covers_note and batch_line must call Sunday's
    dinner "tonight" and name Monday's dinner by its day. They used to read
    date.today() and said the opposite — "covers Monday" for tonight."""
    from freezegun import freeze_time

    sunday, monday = "2026-10-04", "2026-10-05"
    targets = [{"date": "2026-10-06", "slot": "lunch", "eaters": 3}]
    batch = _batch(3, [("2026-10-06", "lunch", 3)])
    with freeze_time("2026-10-05T01:00:00+00:00"):
        assert datetime.date.today().isoformat() == monday  # the server's day
        assert ck.household_today().isoformat() == sunday   # the household's
        tonight = lo.covers_note(_src(date=sunday, targets=targets), 6)
        tomorrow = lo.covers_note(_src(date=monday, targets=targets), 6)
        said = lo.batch_line(_src(date=sunday), batch)
        other = lo.batch_line(_src(date=monday), batch)
    assert tonight == "Cooking for 6 — covers tonight and leftovers on Tuesday."
    assert tomorrow == "Cooking for 6 — covers Monday and leftovers on Tuesday."
    assert said.startswith("Double batch: 3 tonight")
    assert other.startswith("Double batch: 3 for Monday")


def test_the_grocery_note_for_those_ingredients_is_unchanged():
    """The card: "The grocery line note for those ingredients is unchanged
    — already scaled; no new note there." Asserted rather than assumed."""
    plan, _ = _week()
    tools.approve_weekly_plan(plan)
    lines = {i["item"]: i for i in tools.list_grocery_list(status="all")}
    beef = lines.get("Ground beef")
    assert beef is not None
    # the batch's own amount, and nothing about the batch said on the line
    assert beef["quantity"] == "1.5 lbs"
    said = json.dumps(beef)
    for word in ("Double batch", "Big batch", "containers", "freezer"):
        assert word not in said, word


# ====================================================== the client surfaces
# Run under node, because the risk on a renderer is a line that never
# appears — which a source-marker test cannot see.

def _extract(name: str) -> str:
    start = SHELL_JS.index(f"function {name}(")
    i = SHELL_JS.index("{", start)
    depth, j = 0, i
    while True:
        if SHELL_JS[j] == "{":
            depth += 1
        elif SHELL_JS[j] == "}":
            depth -= 1
            if depth == 0:
                return SHELL_JS[start:j + 1]
        j += 1


_PRELUDE = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function cookMealKey(m){ return 'k' + (m.entry_id || m.meal); }
function cookGetOutRowHtml(){ return '<li></li>'; }
function recipeIngredientRowHtml(){ return '<li></li>'; }
function cookUnscaledHtml(){ return ''; }
function cookIngredientLabel(i){ return String(i.item || ''); }
"""


def _render(fn: str, *args: object) -> str:
    body = ", ".join(json.dumps(a) for a in args)
    script = (
        _PRELUDE
        + _extract("cookServesShown") + "\n"
        + _extract("recipeBatchLineHtml") + "\n"
        + _extract("recipeIngredientsHtml") + "\n"
        + f"console.log(JSON.stringify({fn}({body})));\n"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_the_line_renders_on_the_recipe_and_only_for_a_batch():
    said = "Double batch: 3 tonight, 3 for lunch tomorrow."
    assert said in _render("recipeBatchLineHtml", {"batch_line": said})
    assert 'class="recipe-batch"' in _render("recipeBatchLineHtml", {"batch_line": said})
    # a plain dinner, a reheat, an older payload: nothing at all
    assert _render("recipeBatchLineHtml", {}) == ""
    assert _render("recipeBatchLineHtml", {"batch_line": ""}) == ""
    assert _render("recipeBatchLineHtml", None) == ""


@_needs_node
def test_the_line_is_escaped_not_composed_by_the_client():
    """The sentence is the server's, whole — the client adds no words and
    reads nothing out of the chain itself."""
    out = _render("recipeBatchLineHtml", {"batch_line": "Double <b>batch</b>"})
    assert "&lt;b&gt;" in out and "<b>" not in out


@_needs_node
def test_the_batch_is_said_once_and_the_old_note_is_not_said_beside_it():
    """The card's own rule: the batch is said ONCE, in this sentence. Added
    after a mutation that printed covers_note beside it reddened nothing
    in this file — the claim was only pinned one file over
    (test_recipe_screen.py's banned-string tripwire), and it belongs with
    the card that makes it."""
    meal = {"batch_line": "Double batch: 3 tonight, 3 for lunch tomorrow.",
            "covers_note": "Cooking for 6 — covers tonight and lunch tomorrow."}
    out = _render("recipeBatchLineHtml", meal)
    assert out.count("recipe-batch") == 1
    for gone in ("Cooking for", "covers ", "enough for"):
        assert gone not in out, gone


@_needs_node
def test_the_ingredients_heading_carries_the_batch_size():
    meal = {"batch_line": "Double batch: 3 tonight, 3 for lunch tomorrow.",
            "default_servings": 6, "ingredients": [{"item": "Ground beef", "qty": "1.5 lbs"}]}
    out = _render("recipeIngredientsHtml", meal, "x", False)
    assert "Ingredients &middot; 6 servings" in out


@_needs_node
def test_the_heading_follows_the_cooks_own_stepper_not_the_chain():
    """The amounts under it follow the stepper (cookApplyServesOverride
    rewrites them), so the heading has to as well — a heading claiming the
    batch over a list the cook had just halved would be the wrong one of
    the two to be honest about."""
    meal = {"batch_line": "Double batch: 3 tonight, 3 for lunch tomorrow.",
            "default_servings": 6, "serves_target": 4,
            "ingredients": [{"item": "Ground beef", "qty": "1 lb"}]}
    out = _render("recipeIngredientsHtml", meal, "x", False)
    assert "Ingredients &middot; 4 servings" in out


@_needs_node
def test_a_plain_dinners_heading_still_says_just_ingredients():
    meal = {"default_servings": 3, "ingredients": [{"item": "Eggs", "qty": "6"}]}
    out = _render("recipeIngredientsHtml", meal, "x", False)
    assert ">Ingredients<" in out
    assert "servings" not in out


# ------------------------------------------------- source markers
# Where a renderer is too tangled to lift (the Tonight card wants the
# shelf, the moves and the clock; the step card wants cookState), the
# claim is pinned by reading the source — and the browser drive in the
# report is what checked it really renders.

def test_both_recipe_surfaces_render_the_line_under_the_stepper():
    """Cook mode's recipe screen AND Plan's Meal step — one recipe, two
    doors, and the line must not be on only one of them."""
    # UPDATED 2026-10-06 (the recipe page): cook mode's stepper and line
    # are on the recipe's Overview tab (recipeOverviewHtml) now.
    for call in ("recipeServesHtml(meal, idx) +\n      recipeBatchLineHtml(meal) +",
                 "recipeServesHtml(cookMeal, 'wk') : '') +\n        recipeBatchLineHtml(cookMeal) +"):
        assert call in SHELL_JS, call


def test_cooks_tonight_card_says_it_too():
    i = SHELL_JS.index("function cookTonightCardHtml(")
    j = SHELL_JS.index("function kitchenCookingTodayHtml(", i)
    block = SHELL_JS[i:j]
    assert "cook-tonight-batch" in block
    assert "meal.batch_line" in block
    # ...and not over a card that already reads "Cooked."
    assert "!row.done && meal.batch_line" in block


def test_cook_modes_first_and_last_steps_carry_their_sentences():
    i = SHELL_JS.index("function cookCookerHtml(")
    j = SHELL_JS.index("function cookCookerDockHtml(", i)
    block = SHELL_JS[i:j]
    assert "pos === 0 && meal.batch_first_step" in block
    assert "pos === steps.length - 1 && meal.batch_last_step" in block


def test_the_servings_stepper_is_untouched():
    """Emily, 2026-10-04: "I like how we had a servings adjustments
    before." Nothing replaces it, and the line is a SIBLING under it."""
    i = SHELL_JS.index("function recipeServesHtml(")
    j = SHELL_JS.index("}", SHELL_JS.index("return '<div class=\"cook-serves recipe-serves\"", i))
    stepper = SHELL_JS[i:j]
    assert "cook-serves-label\">Cooking for<" in stepper
    assert "data-cook=\"serves\"" in stepper
    # the batch line is not inside it
    assert "batch" not in stepper.lower()


def test_the_three_inks_are_each_chosen_for_their_own_background():
    """--celadon-label is a dark ink for celadon tiles; on the spruce
    Tonight card it is nearly invisible, so that one takes --celadon.
    Three surfaces, three measured pairs, tokens only (rule 9)."""
    for sel, token in ((".recipe-batch", "--celadon-label"),
                       (".cook-tonight-batch", "--celadon"),
                       (".cook-step-batch", "--celadon-label")):
        i = SHELL_CSS.index(sel + " {")
        block = SHELL_CSS[i:SHELL_CSS.index("}", i)]
        assert f"color: var({token})" in block, (sel, block)
        assert "#" not in block, (sel, "a literal hex")
    # and every one of them records what it measured
    for ratio in ("5.44:1 light / 8.22:1 dark", "7.23:1 light / 5.94:1 dark",
                  "5.76:1 light / 7.14:1 dark"):
        assert ratio in SHELL_CSS, ratio


def test_the_line_is_never_a_tile_or_a_chip():
    """Rule 5: the recipe screen's one accent is "Start cooking" in the
    dock. A LINE, as the card specifies."""
    for sel in (".recipe-batch", ".cook-tonight-batch", ".cook-step-batch"):
        i = SHELL_CSS.index(sel + " {")
        block = SHELL_CSS[i:SHELL_CSS.index("}", i)]
        for banned in ("background", "border", "apricot"):
            assert banned not in block, (sel, banned)
