"""
Swapping a dinner that feeds tomorrow's lunch swaps the lunch too.

Gowthami's household, 2026-10-04: "If we change one recipe that should be
for dinner, then lunch the next day, it's not changing the lunch the next
day for the quantity and including it as well."

MEASURED on an approved week before the fix, through the week row's own
Swap (swap_options.choose_swap_option with no whole_dish — a one-night
dinner row sends none):

    before   Mon dinner Beef Chili (make_double_for Tue lunch)
             Tue lunch  Beef Chili (links_to Mon dinner)
             grocery    Beef 2 lbs, Kidney beans 2 cans
    after    Mon dinner Chana Masala
             Tue lunch  Beef Chili          <-- the bug
             grocery    Beef 1 lb, Kidney beans 1 can   <-- the lunch
                        Chickpeas 2 cans, Tomatoes 1 can    buying its own,
                                                            the new dish for
                                                            one table

The fix is server-side and one group: swap_in_place.fed_days — the tapped
meal and every meal still ahead that eats out of ITS cook, downstream
only. apply_pick widens to it, so "Swap · I'll pick", the three-picks
sheet and the chat change card are all covered by the one change;
swap_options asks the three picks against it and gates every day; and the
chat tool (weekly_plan.swap_meal_in_plan_for_chat) widens through
replace_dish_on_days directly, because chat names a dish rather than a
pick.

NO MODEL IS CALLED in this file: every pick carries its steps, so
choose_swap_option applies it as it is (swap_options.needs_write_out), and
swap_meal_in_place takes an injected `picker`.

Each test says CATCH (red against c40f452, this branch's merge base) or
GUARD (green either way, pinned by a mutation named in the docstring and
ACTUALLY RUN). Measured against the merge base's `app/` and `static/` in a
`git archive` of its own, with the five names this branch adds stubbed to
MERGE-BASE BEHAVIOUR so every test reaches the assertion it is named for
(fed_days -> [entry], swapped_said/refilled_said -> "",
instead_of_the_leftovers -> {}, repeat_for_slot -> None,
keeps_as_leftovers -> True):

    30 failed, 20 passed   (39 functions, 50 cases)

No AttributeError anywhere in that list. But read the 30 for less than it
looks, because five of them are not behaviour catches on their own claim
and each says so in its own docstring:

    25  behaviour catches, failing on the assertion they are named for
     1  test_three_meals_read_as_a_list — red on the ABSENCE of `said`,
        not on the joining it is about
     1  test_a_failure_part_way_changes_no_meal — DID NOT RAISE: it
        forces a failure inside replace_dish_on_days, which a one-day
        swap on the merge base never reaches, so it never gets to the
        atomicity it asserts
     3  source/name markers (test_the_toast_prefers_the_servers_sentence,
        test_both_swap_handlers_splice_every_day_the_swap_returned,
        test_the_repeat_rule_is_the_one_generation_uses) — red because
        the line or the name does not exist there, the only kind of red a
        marker on new code can have
    ---
    30

THE PREVIOUS VERSION OF THIS HEADER SAID "24 failed, 11 passed" AND THAT
WAS A STALE NUMBER CARRIED FORWARD — it summed to 35 when the file held
46 cases, which is how it was caught. Re-measured here rather than
re-quoted, on the tree that ships.

MUTATIONS RUN, red counts read off the runs over this file, in a
`git archive` of the branch so nothing else was writing to the tree:

    the whole widening a no-op (fed_days -> [entry]) ................. 22
    keeps_as_leftovers always True ................................... 12
    a second copy of the repeat rule in swap_in_place .................  8
    swapped_said naming days without their meal words ................  3
    the undo restoring only the first row ............................  3
    the fed meal opened BESIDE the old row (plan_slot_open alone) .....  2
    replace_dish_on_days ignoring a per-item `chain` .................  2
    the undo not handing the recorded chain back .....................  2
    swapped_said joining with ", " throughout ........................  2
    apply_pick not widening (the `group=None` default) ...............  1
    fed_days walking both ways (chain_days' rule) ....................  1
    the chat door not widening .......................................  1
    keeps_as_leftovers ignoring the pick's own answer ................  1
    NEVER_OPEN_SLOTS gains dinner (a fed dinner repeated) ............  1
    batch serves from the whole group (BOTH copies of `keeping`) .....  1
    _entry's read not scoped to the household ........................  1
    the sheet never says which meals it is swapping ..................  1
    shell.js: runSwapPick's toast back to savedLine alone ............  1
    shell.js: runSwapInPlace's toast back to savedLine alone .........  1
    shell.js: runSwapInPlace splicing one day, not every day returned   1

Every one bites. FIVE REDDENED NOTHING ON A FIRST RUN AND ALL FIVE ARE
RECORDED RATHER THAN QUIETLY RE-RUN, because three were badly chosen, one
was neutralised by a seed, and one found a real hole:

  * "apply_pick not widening" — 0 red. The other three doors all pass
    `group` explicitly, so the `group is None` default is reached only by
    proposals.apply_proposal (the chat change card's Save changes), and
    NOTHING IN THIS FILE DROVE IT. That default is the fix's own fourth
    door and it was unpinned. test_the_chat_change_cards_save_widens_too
    closes it; the mutation now reddens 1.
  * "NEVER_OPEN_SLOTS gains dinner" — 0 red, neutralised by its own
    seed: with only the cook and the night it feeds on the plan there is
    no other dinner for repeat_for_slot to offer, so the slot opened
    either way. Re-seeded with a third dinner; 1 red.
  * "batch serves from the whole group" — 0 red twice over. First
    because there are TWO copies of the `keeping` line (apply_pick and
    choose_swap_option) and only one was mutated; then, with both
    mutated, because `serves` only reaches
    `default_servings=pick.get("default_servings") or serves or 4` and
    every pick in this file carries its own. It is pinned by
    test_the_new_recipe_is_saved_for_the_batch_that_keeps_it (a pick with
    none) rather than through the grocery list, and the docstring that
    claimed the grocery test pinned it is corrected in place.
  * "_entry's read not scoped to the household" — 0 red as first
    written, which put a no-op `pass` ahead of the docstring. Aimed at
    the WHERE clause: 1 red.
  * "runSwapInPlace splices one day" — 0 red, and the anchor appeared
    twice in shell.js so the first attempt did not apply at all. Aimed
    with surrounding context it still read 0, because nothing here drives
    that handler's DOM splicing; the marker named above is what catches a
    revert, and it says it is a marker.

"""
from __future__ import annotations

import datetime
import importlib
import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

import nodeharness
from conftest import household_today
from app import tools
from app.db import get_conn
from app.tools._shared import use_household

sop = importlib.import_module("app.tools.swap_options")
swap_in_place = importlib.import_module("app.tools.swap_in_place")

TODAY = household_today()
START = TODAY.isoformat()
D1 = (TODAY + datetime.timedelta(days=1)).isoformat()
D2 = (TODAY + datetime.timedelta(days=2)).isoformat()
D3 = (TODAY + datetime.timedelta(days=3)).isoformat()
CHILI = "Beef Chili"
NEW = "Chana Masala"
SHELL = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")
_needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                 reason="node runs the sheet's own renderer")


def _fn(name: str) -> str:
    """One function out of shell.js, by brace matching."""
    i = SHELL.index("function " + name + "(")
    j = SHELL.index("{", i)
    depth, k = 0, SHELL.index("{", i)
    while True:
        if SHELL[k] == "{":
            depth += 1
        elif SHELL[k] == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    return SHELL[i:k + 1]


def _pick(name=NEW, item="Chickpeas", qty="2 cans", minutes=15):
    return {
        "meal_name": name, "reason": "Lighter, and nothing to thaw.",
        "ingredients": [{"item": item, "qty": qty, "category": "pantry"}],
        "instructions": ["Simmer it."], "food_groups": ["protein", "carb", "vegetable"],
        "main_protein": item, "prep_time_minutes": 5, "cook_time_minutes": minutes,
        "default_servings": 2,
    }


def _asker(*picks):
    return lambda context: [dict(p) for p in picks]


@pytest.fixture
def home():
    for name in ("Alex", "Sam"):
        tools.add_member(name)
    tools.add_recipe(CHILI, ingredients=[{"item": "Beef", "qty": "1 lb", "category": "meat/seafood"},
                                         {"item": "Kidney beans", "qty": "1 can", "category": "pantry"}],
                     food_groups=["protein", "carb", "vegetable"], default_servings=2,
                     prep_time_minutes=10, cook_time_minutes=20, instructions=["Brown it."])
    sop._OPTIONS_CACHE.clear()
    return tools.create_weekly_plan(START)["weekly_plan_id"]


def _chain(plan_id, cook_slot="dinner", fed=((None, "lunch"),), approve=True):
    """A confirmed chain: CHILI cooked on D1's `cook_slot`, reheated on
    each (date, slot) in `fed` (None date means D2). Returns the cook's
    entry id."""
    cook = tools.plan_meal(D1, CHILI, slot=cook_slot, weekly_plan_id=plan_id,
                           reasoning="hearty")["entry_id"]
    for date, slot in fed:
        tools.plan_meal(date or D2, CHILI, slot=slot, weekly_plan_id=plan_id,
                        derived_from={"links_to": f"{D1}:{cook_slot}"})
    tools.repair_leftover_chains(plan_id)
    if approve:
        tools.approve_weekly_plan(plan_id, "Alex")
    return cook


def _rows(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.open_reason, "
        "COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.derived_from_json "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? ORDER BY mpe.date, mpe.slot, mpe.id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return [dict(r, derived=json.loads(r["derived_from_json"] or "{}")) for r in rows]


def _meals(plan_id):
    return {(r["date"], r["slot"]): r["meal"] for r in _rows(plan_id)}


def _states(plan_id):
    return {(r["date"], r["slot"]): r["slot_state"] for r in _rows(plan_id)}


def _grocery():
    return {g["item"]: g["quantity"]
            for g in tools.list_grocery_list() + tools.list_grocery_list(status="spice")}


def _id_at(plan_id, date, slot):
    return next(r["id"] for r in _rows(plan_id) if r["date"] == date and r["slot"] == slot)


def _chains(plan_id):
    return {cook: [(t["date"], t["slot"]) for t in s["targets"]]
            for cook, s in tools.plan_leftover_chains(plan_id)["sources"].items()}


def _sheet_swap(plan_id, entry_id, pick=None, whole_dish=False):
    """The three-picks sheet: open, then tap pick 0 — the door the card's
    own test names (the week row's Swap on a one-night dinner)."""
    sop._OPTIONS_CACHE.clear()
    opened = tools.swap_options(plan_id, entry_id, asker=_asker(pick or _pick()),
                                **({"whole_dish": True} if whole_dish else {}))
    out = tools.choose_swap_option(plan_id, entry_id, 0,
                                   **({"whole_dish": True} if whole_dish else {}))
    return opened, out


# ---------- 1. the reported bug, through the door it was reported on ----------


def test_the_lunch_eating_the_leftovers_gets_the_new_dish_too(home):
    """CATCH. The card's own acceptance test: approved week, dinner Mon
    with leftover lunch Tue, swap Mon from the week row -> Tue lunch shows
    the new dish."""
    cook = _chain(home)
    _opened, out = _sheet_swap(home, cook)
    assert out["status"] == "swapped"
    assert _meals(home) == {(D1, "dinner"): NEW, (D2, "lunch"): NEW}


def test_the_grocery_list_holds_the_new_dish_at_batch_size_and_nothing_of_the_old(home):
    """CATCH. "grocery quantities equal the new recipe at batch size, no
    line for the old dish remains" — 2 cans of chickpeas a table, two
    tables, one line of 4. Measured before: Beef 1 lb AND Kidney beans
    1 can left standing beside Chickpeas 2 cans."""
    cook = _chain(home)
    _sheet_swap(home, cook)
    assert _grocery() == {"Chickpeas": "4 cans"}


def test_the_new_dinner_still_feeds_the_lunch(home):
    """CATCH. "The new dinner is written for the whole batch" — the chain
    is carried to the new dish rather than unlinked, so the Cook card still
    reads one cook feeding two meals."""
    cook = _chain(home)
    _opened, out = _sheet_swap(home, cook)
    assert _chains(home) == {out["entry_id"]: [(D2, "lunch")]}
    fed = next(r for r in _rows(home) if r["slot"] == "lunch")
    assert fed["derived"]["links_to"] == f"{D1}:dinner"


def test_no_separate_line_set_is_left_for_the_lunch(home):
    """CATCH. The ledger, not the printed list: before the fix the reheat
    night held grocery links of its own (it had stopped being a reheat), so
    "no separate line set for the lunch" is a question about
    meal_plan_grocery_links rather than about the quantities."""
    cook = _chain(home)
    _sheet_swap(home, cook)
    fed = next(r for r in _rows(home) if r["slot"] == "lunch")
    conn = get_conn()
    links = conn.execute("SELECT item FROM meal_plan_grocery_links WHERE meal_plan_entry_id = ?",
                         (fed["id"],)).fetchall()
    conn.close()
    assert [r["item"] for r in links] == []


def test_a_dish_with_no_leftovers_planned_is_the_one_day_swap_it_always_was(home):
    """GUARD. Mutation: fed_days returning every planned meal — 11 red.
    Nothing widens for an ordinary dinner."""
    tools.plan_meal(D1, CHILI, slot="dinner", weekly_plan_id=home)
    tools.plan_meal(D2, CHILI, slot="dinner", weekly_plan_id=home)
    tools.approve_weekly_plan(home, "Alex")
    cook = next(r["id"] for r in _rows(home) if r["date"] == D1)
    _opened, out = _sheet_swap(home, cook)
    assert _meals(home) == {(D1, "dinner"): NEW, (D2, "dinner"): CHILI}
    assert "days" not in out and "said" not in out


# ---------- 2. the group: downstream only ----------


def test_fed_days_is_the_cook_and_the_meals_it_feeds(home):
    """CATCH (the name does not exist on the merge base). In date order,
    the tapped meal among them."""
    cook = _chain(home, fed=((D2, "lunch"), (D3, "dinner")))
    group = tools.fed_days(home, cook)
    assert [(e["date"], e["slot"]) for e in group] == [(D1, "dinner"), (D2, "lunch"), (D3, "dinner")]


def test_swapping_a_reheat_night_leaves_its_cook_alone(home):
    """GUARD — relabelled from CATCH after measuring it. The whole
    difference from chain_days, and the reason fed_days is downstream
    only: "not Monday's chili again on Tuesday" is an answer about that
    one meal. It is GREEN on the merge base, where nothing widens at all,
    so the cook is left alone there for a different reason than the one
    this test is named for. Mutation — fed_days walking both ways
    (chain_days' rule): 1 red, this test."""
    _chain(home)
    fed = next(r["id"] for r in _rows(home) if r["slot"] == "lunch")
    group = tools.fed_days(home, fed)
    assert [(e["date"], e["slot"]) for e in group] == [(D2, "lunch")]
    _sheet_swap(home, fed)
    assert _meals(home) == {(D1, "dinner"): CHILI, (D2, "lunch"): NEW}


def test_a_fed_meal_already_cooked_is_left_where_it_is(home):
    """GUARD. dish_days' own filter, inherited: a day already cooked is
    not a day to plan into. Mutation: dropping the `cooked` test from
    _along_chains' `ahead` map — 1 red."""
    cook = _chain(home)
    fed = next(r["id"] for r in _rows(home) if r["slot"] == "lunch")
    tools.check_off_meal(fed, "done")
    assert [e["entry_id"] for e in tools.fed_days(home, cook)] == [cook]


def test_another_households_chain_is_never_part_of_the_group(home):
    """GUARD. Mutation: dropping household_id() from fed_days' read — it
    goes through _entry and plan_leftover_chains, both household-scoped;
    this pins the seam rather than the query."""
    cook = _chain(home)
    with use_household(2):
        with pytest.raises(ValueError):
            tools.fed_days(home, cook)


# ---------- 3. every door ----------


def test_swap_i_ll_pick_widens_too(home):
    """CATCH. swap_meal_in_place — the Day and Meal steps' own Swap, one
    model call, no sheet. Mutation: apply_pick not widening — 9 red."""
    cook = _chain(home)
    out = tools.swap_meal_in_place(home, cook, picker=lambda ctx: _pick())
    assert out["status"] == "swapped"
    assert _meals(home) == {(D1, "dinner"): NEW, (D2, "lunch"): NEW}


def test_the_pick_is_asked_for_the_whole_batch_not_one_table(home):
    """CATCH. Or the model chooses and sizes a dinner for two when four
    meals' worth is being cooked. `cook_for` is the same sentence the
    whole-dish Swap already asks under."""
    cook = _chain(home)
    seen = []
    tools.swap_meal_in_place(home, cook, picker=lambda ctx: seen.append(ctx) or _pick())
    assert seen[0]["table"]["serves"] == 2
    assert "4 servings" in (seen[0].get("cook_for") or "")
    assert seen[0]["dates"] == [D1, D2]


def test_the_sheet_asks_its_three_picks_against_both_meals(home):
    """CATCH. build_dish_swap_context, so a pick that lands on a dinner
    and the lunch reheating it has had to fit both."""
    cook = _chain(home)
    seen = []
    sop._OPTIONS_CACHE.clear()
    tools.swap_options(home, cook, asker=lambda ctx: seen.append(ctx) or [_pick()])
    assert seen[0]["dates"] == [D1, D2]
    assert "4 servings" in (seen[0].get("cook_for") or "")


def test_the_sheet_says_which_meals_it_is_swapping(home):
    """CATCH. `dates` and `meals` on the open, so the sheet can name both
    from the same list the write will use."""
    cook = _chain(home)
    opened, _out = _sheet_swap(home, cook)
    assert opened["dates"] == [D1, D2]
    assert opened["meals"] == [{"date": D1, "slot": "dinner"}, {"date": D2, "slot": "lunch"}]


def test_a_veto_at_the_lunch_table_rules_the_pick_out(home):
    """CATCH. Emily's standing rule: a suggestion fits every day of the
    group. swap_meal_in_place used to gate the tapped night only."""
    cook = _chain(home)
    calls = []

    def verdict(name, date, slot):
        return {"verdict": "avoid", "vetoed_by": ["Sam"]} if (date, slot) == (D2, "lunch") else None

    import app.tools.weekly_plan as wp
    original = wp._taste_verdict_for_slot
    wp._taste_verdict_for_slot = verdict
    try:
        out = tools.swap_meal_in_place(home, cook, picker=lambda ctx: calls.append(1) or _pick())
    finally:
        wp._taste_verdict_for_slot = original
    assert out["status"] == "refused"
    assert _meals(home) == {(D1, "dinner"): CHILI, (D2, "lunch"): CHILI}


def test_the_chat_tool_widens_too(home):
    """CATCH. The fourth door. Chat names a dish rather than a pick, so it
    writes through replace_dish_on_days directly. Mutation: the chat door
    not widening — 4 red."""
    cook = _chain(home)
    tools.add_recipe(NEW, ingredients=[{"item": "Chickpeas", "qty": "2 cans", "category": "pantry"}],
                     food_groups=["protein", "carb", "vegetable"], default_servings=2,
                     prep_time_minutes=5, cook_time_minutes=15, instructions=["Simmer it."])
    out = tools.swap_meal_in_plan_for_chat(home, D1, NEW, slot="dinner")
    assert _meals(home) == {(D1, "dinner"): NEW, (D2, "lunch"): NEW}
    assert _grocery() == {"Chickpeas": "4 cans"}
    assert out["said"].startswith("Swapped to Chana Masala for")


def test_the_chat_change_cards_save_widens_too(home):
    """CATCH. The door apply_pick's `group=None` DEFAULT exists for, and
    the only one of the four that does not pass a group of its own:
    proposals.apply_proposal (the chat change card's Save changes).

    FOUND BY A MUTATION THAT DID NOT BITE, and recorded rather than
    quietly fixed. "apply_pick not widening" — `group = [entry]` in place
    of the fed_days read behind `group is None` — reddened NOTHING on the
    first run, because the other three doors all pass `group` explicitly
    and nothing in this file drove the one that doesn't. The default was
    the fix's own fourth door and it was unpinned; this is the test that
    pins it. Mutation now: 2 red.
    """
    cook = _chain(home)
    card = tools.propose_plan_changes(home, [{"date": D1, "slot": "dinner",
                                              "candidates": [_pick()]}])
    out = tools.apply_proposal(card["proposal_id"])
    assert out["status"] == "applied"
    # Both meals, and the cook still feeding the lunch at batch size.
    assert _meals(home) == {(D1, "dinner"): NEW, (D2, "lunch"): NEW}
    assert _chains(home) == {_id_at(home, D1, "dinner"): [(D2, "lunch")]}
    assert _grocery() == {"Chickpeas": "4 cans"}


def test_the_chat_tool_leaves_a_slot_holding_two_snacks_alone(home):
    """GUARD. Deliberately narrow: the chat door widens only when the slot
    holds exactly ONE row, so it can never re-implement
    swap_meal_in_plan's own "which rows am I replacing" rule and disagree
    with it. Mutation: dropping the len(rows) != 1 guard — this test."""
    tools.add_recipe("Apple Slices", ingredients=[{"item": "Apples", "qty": "4", "category": "produce"}],
                     food_groups=["carb"], default_servings=2)
    tools.add_recipe("Oat Bars", ingredients=[{"item": "Oats", "qty": "1 bag", "category": "pantry"}],
                     food_groups=["carb"], default_servings=2)
    tools.plan_meal(D1, "Apple Slices", slot="snack", weekly_plan_id=home)
    tools.plan_meal(D1, "Oat Bars", slot="snack", weekly_plan_id=home)
    out = tools.swap_meal_in_plan_for_chat(home, D1, "Apple Slices", slot="snack",
                                           old_meal="Oat Bars")
    assert "said" not in out
    assert sorted(r["meal"] for r in _rows(home) if r["slot"] == "snack") == \
        ["Apple Slices", "Apple Slices"]


# ---------- 4. the confirmation ----------


def test_the_confirmation_names_both_meals(home):
    """CATCH. The card's own wording: "Swapped to chana masala for Monday
    dinner and Tuesday lunch." Mutation — swapped_said naming days without
    their meal words: 3 red."""
    cook = _chain(home)
    _opened, out = _sheet_swap(home, cook)
    mon, tue = [datetime.date.fromisoformat(d).strftime("%A") for d in (D1, D2)]
    assert out["said"] == f"Swapped to {NEW} for {mon} dinner and {tue} lunch."


def test_three_meals_read_as_a_list(home):
    """GUARD on its own claim, and RED against the merge base for a
    DIFFERENT reason — said rather than counted as a catch: there is no
    `said` at all there, so it dies on KeyError before it can reach the
    joining this test is about. Mutation: swapped_said joining with ", "
    throughout — 2 red, this among them."""
    cook = _chain(home, fed=((D2, "lunch"), (D3, "dinner")))
    _opened, out = _sheet_swap(home, cook)
    days = [datetime.date.fromisoformat(d).strftime("%A") for d in (D1, D2, D3)]
    assert out["said"] == (f"Swapped to {NEW} for {days[0]} dinner, "
                           f"{days[1]} lunch and {days[2]} dinner.")


def test_a_one_meal_swap_says_nothing_extra(home):
    """GUARD. `said` is present only when more than one meal changed, so
    the ordinary swap keeps the toast it always had ("X was swapped in").
    Mutation: `said` written unconditionally — this test."""
    tools.plan_meal(D1, CHILI, slot="dinner", weekly_plan_id=home)
    cook = next(r["id"] for r in _rows(home) if r["date"] == D1)
    _opened, out = _sheet_swap(home, cook)
    assert "said" not in out


def test_the_toast_prefers_the_servers_sentence(home):
    """CATCH (source marker) — relabelled from GUARD after measuring it.
    shell.js's two swap toasts read `said` and fall back to savedLine, so
    the sentence that counts meals is built once, beside the rows. Red
    against the merge base because that line does not exist there, which
    is the only kind of red a source marker on new code can have — not
    evidence of behaviour. Mutations: runSwapPick's toast back to
    savedLine alone — 1 red; runSwapInPlace's — 1 red; this test both
    times."""
    assert "out.said || savedLine(picked.meal, 'swapped in')" in SHELL
    assert "data.said || savedLine(mealDisplayName(daySlotEntry(data.day, slot)), 'swapped in')" in SHELL
    assert "(data.days || [data.day]).forEach(spliceSwappedDay)" in SHELL


def test_both_swap_handlers_splice_every_day_the_swap_returned():
    """CATCH (source marker) on runSwapInPlace, GUARD on runSwapPick.

    A widened swap answers with `days` for every meal it changed, and the
    screen has to put all of them back into the week it is holding or the
    lunch goes on reading the old dish until the next load.
    runSwapPick ALREADY looped (it was written for the whole-dish Swap);
    runSwapInPlace spliced `data.day` alone, and that is the one line of
    static/shell.js this needed besides the two toasts.

    A MARKER, AND SAID TO BE ONE: the mutation that reverts
    runSwapInPlace's loop to `spliceSwappedDay(data.day);` reddens NOTHING
    behavioural in this file — nothing here drives that handler's DOM
    splicing, which would want weekState, renderMealsStep and the panel
    stubbed. So this catches a revert and does not prove the render. The
    swap's own `days` payload is pinned properly, by
    test_swap_i_ll_pick_widens_too."""
    for fn in ("runSwapPick", "runSwapInPlace"):
        i = SHELL.index("function " + fn + "(")
        body = SHELL[i:i + 4000]
        assert ".forEach(spliceSwappedDay)" in body, f"{fn} must splice every day it got back"
        assert "spliceSwappedDay(data.day);" not in body, f"{fn} must not splice one day only"


@_needs_node
def test_the_sheet_names_both_meals_before_the_tap_too():
    """GUARD on shell.js, and the reason no further client change is owed.
    swapDaysLine already handles a group that spans meal types — it was
    written for the whole-dish Swap (Emily, 2026-09-22) — and
    openSwapSheet already copies the RESPONSE's `dates`/`meals` into the
    sheet's state unconditionally. So the moment the server started
    sending them for a widened plain Swap, the sheet began saying both
    meals BEFORE the tap for free; nothing in static/shell.js had to
    learn about it.

    Driven under node against the payload the HTTP drive actually
    returned, rather than read off the source: the risk here is a line
    that renders empty, which a marker test cannot see. Mutation: the
    sheet never says which meals it is swapping (swap_options dropping
    `dates`/`meals` for a widened group) — 1 red, the server-side test
    above; this is its client half."""
    js = (
        "function isSnackSlot(s){ return String(s||'').indexOf('snack') === 0; }\n"
        + "\n".join(_fn(f) for f in ("joinList", "dayName", "slotWord", "swapDaysLine"))
        + "\nvar SWAP_SLOT_PLURALS = { breakfast: 'breakfasts', lunch: 'lunches',"
          " dinner: 'dinners', snack: 'snacks' };\n"
        # Exactly what POST /swap-options answered over real HTTP for a
        # Monday dinner cooked double for Tuesday's lunch.
        "console.log(JSON.stringify([\n"
        "  swapDaysLine({ slot: 'dinner', dates: ['2026-10-06', '2026-10-07'],\n"
        "                 meals: [{date:'2026-10-06',slot:'dinner'},{date:'2026-10-07',slot:'lunch'}] }),\n"
        "  swapDaysLine({ slot: 'dinner', dates: ['2026-10-06'],\n"
        "                 meals: [{date:'2026-10-06',slot:'dinner'}] })\n"
        "]));"
    )
    res = nodeharness.run_node(js, timeout=30)
    assert res.returncode == 0, res.stderr
    widened, one_day = json.loads(res.stdout.strip())
    assert widened == "Swapping Tuesday\u2019s dinner and Wednesday\u2019s lunch."
    assert one_day == "", "an ordinary one-meal swap says nothing extra"


# ---------- 5. a dish that will not keep ----------


@pytest.mark.parametrize("name,keeps", [
    ("Chana Masala", True), ("Beef Chili", True), ("Lentil Soup", True),
    ("Caesar Salad", False), ("Nachos Supreme", False), ("Grilled Cheese", False),
    ("Avocado Toast", False), ("Pasta Salad", True), ("Tuna Salad", True),
    ("Chicken Salad Wraps", True), ("Stir-Fried Noodles", True), ("", True),
])
def test_keeps_as_leftovers_reads_the_name(name, keeps):
    """CATCH (the name does not exist on the merge base). Positive
    evidence only: an unrecognised dish KEEPS, and the compound exceptions
    are the mayo salads a household makes ahead on purpose."""
    assert tools.keeps_as_leftovers({"meal_name": name}) is keeps


def test_a_pick_that_says_it_does_not_keep_is_believed(home):
    """CATCH. The model volunteering a problem with its own dish, for a
    name no word list would catch. Mutation: ignoring the pick's own
    answer — 1 red."""
    assert tools.keeps_as_leftovers({"meal_name": "Chana Masala",
                                     "keeps_as_leftovers": False}) is False
    assert tools.keeps_as_leftovers({"meal_name": "Chana Masala"}) is True


def test_a_dish_that_wont_keep_does_not_go_on_the_lunch(home):
    """CATCH. Criterion 5: the lunch is NOT silently left as the old dish.
    It leaves the chain and takes another of the week's own lunches.
    Mutation: keeps_as_leftovers always True — 6 red."""
    tools.add_recipe("Egg Wraps", ingredients=[{"item": "Tortillas", "qty": "4", "category": "pantry"}],
                     food_groups=["protein", "carb", "vegetable"], default_servings=2,
                     prep_time_minutes=5, cook_time_minutes=10, instructions=["Roll it."])
    cook = _chain(home, approve=False)
    tools.plan_meal(D1, "Egg Wraps", slot="lunch", weekly_plan_id=home)
    tools.approve_weekly_plan(home, "Alex")
    _opened, out = _sheet_swap(home, cook, pick=_pick("Caesar Salad", item="Romaine", qty="1 head"))
    assert _meals(home) == {(D1, "dinner"): "Caesar Salad", (D1, "lunch"): "Egg Wraps",
                            (D2, "lunch"): "Egg Wraps"}
    assert _chains(home) == {}
    assert out["refilled"] == [{"date": D2, "slot": "lunch", "meal": "Egg Wraps"}]


def test_the_toast_says_what_it_put_there_instead(home):
    """CATCH. "and the toast says so" — naming what it PUT there, not only
    what it took away."""
    tools.add_recipe("Egg Wraps", ingredients=[{"item": "Tortillas", "qty": "4", "category": "pantry"}],
                     food_groups=["protein", "carb", "vegetable"], default_servings=2,
                     prep_time_minutes=5, cook_time_minutes=10, instructions=["Roll it."])
    cook = _chain(home, approve=False)
    tools.plan_meal(D1, "Egg Wraps", slot="lunch", weekly_plan_id=home)
    tools.approve_weekly_plan(home, "Alex")
    _opened, out = _sheet_swap(home, cook, pick=_pick("Caesar Salad", item="Romaine", qty="1 head"))
    mon, tue = [datetime.date.fromisoformat(d).strftime("%A") for d in (D1, D2)]
    assert out["said"] == (f"Swapped to Caesar Salad for {mon} dinner. "
                           f"Caesar Salad won’t keep, so I’ve put Egg Wraps on {tue} lunch.")


def test_a_refilled_lunch_buys_for_itself_and_the_cook_for_one_table(home):
    """CATCH. The arithmetic of the refill: the cook is no longer a batch,
    so it buys one table; the repeat is now on two lunches, so it buys
    two.

    WHAT PINS THIS IS THE BROKEN CHAIN, NOT `serves`, and the first
    version of this docstring said otherwise ("Mutation: batch serves
    taken from the whole group — 1 red"). Measured: that mutation, applied
    to BOTH copies of the `keeping` line, reddens NOTHING — because
    `serves` only ever reaches `_save_recipe_if_new`, whose line is
    `default_servings=pick.get("default_servings") or serves or 4`, and
    every pick in this file carries its own default_servings. So the
    grocery figures here are the `chain: {}` on the refilled lunch doing
    the work (the cook stops cooking double, the lunch buys for itself),
    which the replace_dish_on_days mutation pins. `serves` is pinned by
    the test below it instead."""
    tools.add_recipe("Egg Wraps", ingredients=[{"item": "Tortillas", "qty": "4", "category": "pantry"}],
                     food_groups=["protein", "carb", "vegetable"], default_servings=2,
                     prep_time_minutes=5, cook_time_minutes=10, instructions=["Roll it."])
    cook = _chain(home, approve=False)
    tools.plan_meal(D1, "Egg Wraps", slot="lunch", weekly_plan_id=home)
    tools.approve_weekly_plan(home, "Alex")
    _sheet_swap(home, cook, pick=_pick("Caesar Salad", item="Romaine", qty="1 head"))
    assert _grocery() == {"Tortillas": "8", "Romaine": "1 head"}


def test_the_new_recipe_is_saved_for_the_batch_that_keeps_it(home):
    """GUARD. `serves` is what a NEW recipe is saved as — the number the
    Cook card reads back as "Serves N" — and the batch it is written for
    is the meals that KEEP the dish, so a fed meal that left the chain is
    not counted into it.

    Observable only for a pick that carries no default_servings of its
    own, which is why it has a test to itself: `_save_recipe_if_new` is
    `default_servings=pick.get("default_servings") or serves or 4`, so
    every other pick in this file makes `serves` dead. Mutation: `keeping`
    taken as the whole group, in BOTH copies of that line
    (swap_in_place.apply_pick and swap_options.choose_swap_option) — 1
    red, this test; it reddens nothing without this test."""
    tools.add_recipe("Egg Wraps", ingredients=[{"item": "Tortillas", "qty": "4",
                                                "category": "pantry"}],
                     food_groups=["protein", "carb", "vegetable"], default_servings=2,
                     prep_time_minutes=5, cook_time_minutes=10, instructions=["Roll it."])
    cook = _chain(home, approve=False)
    tools.plan_meal(D1, "Egg Wraps", slot="lunch", weekly_plan_id=home)
    tools.approve_weekly_plan(home, "Alex")
    pick = _pick("Caesar Salad", item="Romaine", qty="1 head")
    pick.pop("default_servings")
    _sheet_swap(home, cook, pick=pick)
    conn = get_conn()
    serves = conn.execute("SELECT default_servings FROM recipes WHERE name = ?",
                          ("Caesar Salad",)).fetchone()["default_servings"]
    conn.close()
    # Two eat the dinner; the lunch that was eating its leftovers left the
    # chain (Caesar Salad won't keep), so it is not part of this batch.
    assert serves == 2


def test_with_nothing_to_repeat_the_lunch_becomes_a_question(home):
    """CATCH. The week has no other lunch to copy, so the slot is handed
    back — and Pomona says which. Mutation: the open branch removed —
    2 red."""
    cook = _chain(home)
    _opened, out = _sheet_swap(home, cook, pick=_pick("Caesar Salad", item="Romaine", qty="1 head"))
    assert _states(home) == {(D1, "dinner"): "planned", (D2, "lunch"): "open"}
    tue = datetime.date.fromisoformat(D2).strftime("%A")
    assert out["said"].endswith(f"Caesar Salad won’t keep, so {tue} lunch is yours to fill.")


def test_the_question_replaces_the_meal_rather_than_sitting_beside_it(home):
    """CATCH. Measured while building this: plan_slot_open INSERTS, so
    without the delete the slot held the old dish AND a question — which
    is audit_plan_slots' `duplicated`, and how a night nobody is eating
    gets shopped for. Mutation: the open BESIDE the old row — 2 red."""
    cook = _chain(home)
    _sheet_swap(home, cook, pick=_pick("Caesar Salad", item="Romaine", qty="1 head"))
    lunches = [r for r in _rows(home) if (r["date"], r["slot"]) == (D2, "lunch")]
    assert len(lunches) == 1 and lunches[0]["slot_state"] == "open"
    audit = tools.audit_plan_slots(home)
    assert audit["duplicated"] == []
    assert _grocery() == {"Romaine": "1 head"}


def test_a_fed_dinner_is_left_as_a_question_rather_than_repeated(home):
    """CATCH on the opening (nothing opens on the merge base, so it reads
    'planned' there). Its OWN claim — that a fed DINNER is opened rather
    than quietly refilled — is the GUARD half, pinned by the mutation
    below: meal_variety.NEVER_OPEN_SLOTS is breakfast and lunch, because a
    dinner genuinely is a decision and repeating one nobody asked for is
    the opposite of what the household wants. Mutation: "dinner" added to
    the slots instead_of_the_leftovers will repeat into — 1 red.

    THE WEEK NEEDS A THIRD DINNER TO REPEAT, or that mutation is
    neutralised by the seed and reddens nothing: with only the cook and
    the night it feeds on the plan there is nothing for repeat_for_slot to
    offer, so the slot opens whether "dinner" is in NEVER_OPEN_SLOTS or
    not. Measured — the first version of this test seeded two dinners and
    the mutation read 0 red."""
    tools.add_recipe("Lentil Stew", ingredients=[{"item": "Lentils", "qty": "1 cup",
                                                  "category": "pantry"}],
                     food_groups=["protein", "carb", "vegetable"], default_servings=2,
                     prep_time_minutes=5, cook_time_minutes=20, instructions=["Simmer it."])
    cook = _chain(home, fed=((D2, "dinner"),), approve=False)
    tools.plan_meal(D3, "Lentil Stew", slot="dinner", weekly_plan_id=home)
    tools.approve_weekly_plan(home, "Alex")
    _sheet_swap(home, cook, pick=_pick("Caesar Salad", item="Romaine", qty="1 head"))
    assert _states(home)[(D2, "dinner")] == "open", "a fed dinner is a question, never a repeat"
    assert _meals(home)[(D2, "dinner")] is None, "and no dish was quietly put on it"


# ---------- 6. undo ----------


def test_undo_restores_the_dinner_the_lunch_and_the_groceries(home):
    """GUARD — relabelled from CATCH after measuring it. Criterion 6, all
    three together. GREEN on the merge base, and for a reason worth
    naming: there the lunch was never changed, so "the lunch is back" is
    true without anything putting it back, and restore_leftover_chain
    re-links the chain the one-day swap had broken. It is the only test
    here that asserts the dinner, the lunch and the list come back
    TOGETHER. Mutation: _undo_dish_swap restoring only the first row —
    3 red, this among them."""
    cook = _chain(home)
    before = _grocery()
    _opened, out = _sheet_swap(home, cook)
    back = tools.undo_meal_swap(home, out["entry_id"])
    assert back["status"] == "restored"
    assert _meals(home) == {(D1, "dinner"): CHILI, (D2, "lunch"): CHILI}
    assert _grocery() == before == {"Beef": "2 lbs", "Kidney beans": "2 cans"}
    assert _chains(home) == {back["entry_id"]: [(D2, "lunch")]}


def test_undo_of_a_refilled_lunch_puts_the_chain_back_too(home):
    """GUARD — relabelled from CATCH after measuring it. The one chain
    that cannot be re-derived at undo time, since the swap took it off the
    plan: recorded on the undo note and handed back as
    replace_dish_on_days' per-item `chain`. GREEN on the merge base, where
    no meal ever leaves the chain, so there is no such chain to put back.
    Mutations: the undo not handing it back — 2 red;
    replace_dish_on_days ignoring a per-item `chain` — 2 red; both
    include this test."""
    tools.add_recipe("Egg Wraps", ingredients=[{"item": "Tortillas", "qty": "4", "category": "pantry"}],
                     food_groups=["protein", "carb", "vegetable"], default_servings=2,
                     prep_time_minutes=5, cook_time_minutes=10, instructions=["Roll it."])
    cook = _chain(home, approve=False)
    tools.plan_meal(D1, "Egg Wraps", slot="lunch", weekly_plan_id=home)
    tools.approve_weekly_plan(home, "Alex")
    before = _grocery()
    _opened, out = _sheet_swap(home, cook, pick=_pick("Caesar Salad", item="Romaine", qty="1 head"))
    back = tools.undo_meal_swap(home, out["entry_id"])
    assert _meals(home) == {(D1, "dinner"): CHILI, (D1, "lunch"): "Egg Wraps",
                            (D2, "lunch"): CHILI}
    assert _chains(home) == {back["entry_id"]: [(D2, "lunch")]}
    assert _grocery() == before


def test_undo_of_an_opened_lunch_puts_the_meal_back(home):
    """GUARD — relabelled from CATCH after measuring it. The opened row
    carries the same swap_group token as the rows beside it, which is how
    _undo_dish_swap finds it. GREEN on the merge base, where nothing is
    ever opened in place of a fed meal. Mutations: the fed meal opened
    BESIDE the old row (plan_slot_open alone) — 2 red; the undo not
    handing the recorded chain back — 2 red; both include this test."""
    cook = _chain(home)
    before = _grocery()
    _opened, out = _sheet_swap(home, cook, pick=_pick("Caesar Salad", item="Romaine", qty="1 head"))
    back = tools.undo_meal_swap(home, out["entry_id"])
    assert _meals(home) == {(D1, "dinner"): CHILI, (D2, "lunch"): CHILI}
    assert _states(home) == {(D1, "dinner"): "planned", (D2, "lunch"): "planned"}
    assert _chains(home) == {back["entry_id"]: [(D2, "lunch")]}
    assert _grocery() == before


# ---------- 7. what else must not move ----------


def test_a_draft_still_buys_nothing(home):
    """CATCH on the widening (the lunch keeps the old dish on the merge
    base, so the meals assertion fails there) plus a GUARD on the half it
    is named for: nothing reaches the grocery list before approval, and a
    widened swap is no exception. That half is green either way —
    replace_dish_on_days' own tests cover the ingest; this pins the seam
    for the new door."""
    cook = _chain(home, approve=False)
    _sheet_swap(home, cook)
    assert _grocery() == {}
    assert _meals(home) == {(D1, "dinner"): NEW, (D2, "lunch"): NEW}


def test_a_failure_part_way_changes_no_meal(home):
    """GUARD on its own claim — ONE transaction, as the whole-dish Swap
    already was — and RED against the merge base for a reason that is NOT
    that claim, said rather than counted as a catch: it forces a failure
    inside replace_dish_on_days, and a one-day swap on the merge base
    never goes through that function at all, so it fails on DID NOT RAISE
    without reaching the atomicity it asserts. The claim itself is
    replace_dish_on_days' own rollback, which that function's tests cover;
    this pins it for the new door."""
    import app.tools.meal_plans as meal_plans
    cook = _chain(home)
    before = _meals(home)
    original = meal_plans.plan_meal
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("no")
        return original(*a, **k)

    meal_plans.plan_meal = flaky
    try:
        with pytest.raises(RuntimeError):
            _sheet_swap(home, cook)
    finally:
        meal_plans.plan_meal = original
    assert _meals(home) == before
    assert _grocery() == {"Beef": "2 lbs", "Kidney beans": "2 cans"}


def test_the_repeat_rule_is_the_one_generation_uses(home):
    """GUARD. meal_variety.repeat_for_slot is shared with
    fill_gaps_with_a_repeat, so the swap's answer and generation's own are
    the same answer. RED against the merge base on a NAME it has not got
    (this reads the module's source for it), which is not evidence of
    behaviour. Mutation: a second copy of the "fewest nights, then
    earliest, then name" rule in swap_in_place — 7 red, this among
    them."""
    import app.tools.meal_variety as mv
    assert re.search(r"def repeat_for_slot\(", mv.__doc__ or "") is None
    assert callable(mv.repeat_for_slot)
    source = Path(swap_in_place.__file__).read_text(encoding="utf-8")
    assert "repeat_for_slot" in source
    assert "nights" not in source.split("def instead_of_the_leftovers")[1].split("\ndef ")[0] \
        .split('"""')[2]


def test_an_ordinary_dinner_pays_nothing_for_the_widening(home):
    """GUARD on COST, measured at sqlite3.connect rather than at any
    module's get_conn, because _shared.py imports get_conn inside the
    function and a module-level patch would not see those reads
    (CLAUDE.md's own note from the 2026-09-11 approve-race work).

    fed_days returns early for a cook with no `make_double_for` of its
    own, and the reason is cost: _along_chains reads the chains AND the
    whole week payload (get_week_menu), which reads the chains twice
    more. Without the early return every swap paid that, including the
    ordinary dinner that feeds nothing.

    Measured through choose_swap_option on a one-dinner approved week:
    60 connections on the merge base, 87 with the naive widening, 61
    with the early return — the one extra is fed_days' own _entry read.
    Mutation: the early return removed — this test (87 > the ceiling).
    """
    tools.plan_meal(D1, CHILI, slot="dinner", weekly_plan_id=home)
    tools.approve_weekly_plan(home, "Alex")
    cook = next(r["id"] for r in _rows(home) if r["date"] == D1)
    sop._OPTIONS_CACHE.clear()
    tools.swap_options(home, cook, asker=_asker(_pick()))
    real, seen = sqlite3.connect, []
    def counted(*a, **k):
        seen.append(1)
        return real(*a, **k)
    sqlite3.connect = counted
    try:
        tools.choose_swap_option(home, cook, 0)
    finally:
        sqlite3.connect = real
    # A ceiling with headroom, not the exact figure: this path is shared
    # with the grocery ingest and a neighbouring card may legitimately
    # move it by one or two. 87 is what the naive widening cost, so the
    # ceiling has to sit well under it to catch a revert.
    assert len(seen) < 75, f"the plain swap paid {len(seen)} connections for a widening it does not use"
    # ...and a lower bound too, because "< 75" is also green at zero,
    # which would mean the counter stopped counting.
    assert len(seen) > 20


def test_no_module_level_name_is_defined_twice_in_what_this_touched():
    """GUARD. The repo-wide shadowing rule, for the four modules this
    branch adds module-level names to. Mutation: naming the new word list
    after one already in the file — this test."""
    import ast
    for mod in ("leftovers", "swap_in_place", "swap_options", "weekly_plan", "meal_variety"):
        path = Path(__file__).resolve().parent.parent / "app" / "tools" / f"{mod}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
            elif isinstance(node, ast.Assign):
                names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        assert len(names) == len(set(names)), f"{mod}.py defines a module-level name twice"
