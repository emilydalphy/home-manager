"""
"Change recipe" — pick one of your own, paste a link, or say what to change.

Notion card 'Recipe page: a "Change recipe" button'. Four doors on one
button, and three different kinds of risk behind them, so this file is in
three parts.

THE WRITE (parts 1-3). Pointing a meal at a different recipe moves a
household's shopping list, so what is pinned is not "the column changed"
but what the SHOPPER sees: a draft's list is untouched (nothing is on it
yet), an approved week's is re-bought through the same reversal/ingest
pair the leftover rescale and the swap use, a leftover chain follows the
meal it reheats, and a refusal writes nothing at all. The allergy gate
runs BEFORE the transaction opens, so a blocked pick has not touched a
row — and it names the person and the ingredient, which is this card's
own criterion.

THE PROSE (part 4). `request_text` is kept exactly as typed — that is the
feature, and the reason Emily asked for it: the common asks are supposed
to become buttons later, and a summary throws away the only evidence of
which ones they are. Everything around it is the rule feedback_reports
already follows: the default morning report carries a COUNT, the words
need `--recipe-changes`, and what that flag prints is fenced as untrusted.

THE SCREEN (part 5). Run for real under node, not read for markers: the
risks here are a button offered where there is nothing to change, a second
apricot, and a hard-coded colour.

Red against 8050ff2^ (the commit before the backend) is not a meaningful
number for this file — `app.tools.recipe_change` does not exist there, so
every test is a collection error. The evidence is the mutation table in
the report instead; every test below says what it is for.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout

import pytest

from app import tools
from app.db import get_conn
from app.tools import recipe_change

from conftest import household_date, household_today

TODAY = household_today()
MON = household_date(0)
TUE = household_date(1)
WED = household_date(2)


# ---------- fixtures ----------

def _recipe(name: str, items: list[tuple[str, str]], **kw) -> int:
    return tools.add_recipe(
        name,
        [{"item": i, "qty": q, "category": "other"} for i, q in items],
        instructions=kw.pop("instructions", ["Cook it.", "Serve it."]),
        default_servings=kw.pop("default_servings", 4),
        prep_time_minutes=kw.pop("prep_time_minutes", 10),
        cook_time_minutes=kw.pop("cook_time_minutes", 20),
        **kw,
    )["recipe_id"]


def _plan(status: str = "draft", day_count: int = 3) -> int:
    plan = tools.create_weekly_plan(MON, content_start_date=MON, day_count=day_count)["weekly_plan_id"]
    if status == "approved":
        conn = get_conn()
        try:
            conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan,))
            conn.commit()
        finally:
            conn.close()
    return plan


def _entry(plan: int, date: str, meal: str, slot: str = "dinner", buy: bool = False) -> int:
    return tools.plan_meal(date, meal, slot=slot, weekly_plan_id=plan,
                           add_ingredients_to_grocery_list=buy)["entry_id"]


def _needed() -> dict:
    conn = get_conn()
    try:
        return {r["item"]: r["quantity"] for r in conn.execute(
            "SELECT item, quantity FROM grocery_items WHERE status IN ('needed', 'in_cart', 'spice')"
        )}
    finally:
        conn.close()


def _entry_recipe(entry_id: int):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT recipe_id, freeform_meal, slot, date, slot_state FROM meal_plan_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _rows() -> list[dict]:
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT household_id, member_id, dish_name, request_text, outcome, "
            "       meal_plan_entry_id, recipe_id, previous_json "
            "FROM recipe_change_requests ORDER BY id"
        )]
    finally:
        conn.close()


# ---------- 1. picking one of the household's own recipes ----------

def test_a_draft_meal_takes_the_new_recipe_and_keeps_its_night_and_slot(signed_in):
    """
    The card's whole acceptance criterion for door one. The slot, the day
    and the state are what make it the same meal — only the recipe behind
    it moves.
    """
    plan = _plan("draft")
    old = _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    new = _recipe("Chana Masala (mum's)", [("Chickpeas", "2 cans"), ("Amchur", "1 tsp")])
    entry = _entry(plan, TUE, "Chana Masala", slot="dinner")

    res = tools.change_meal_recipe(entry, new)

    assert res["status"] == "changed", res
    row = _entry_recipe(entry)
    assert row["recipe_id"] == new
    assert row["freeform_meal"] is None
    assert (row["date"], row["slot"], row["slot_state"]) == (TUE, "dinner", "planned")
    assert old != new


def test_a_draft_change_leaves_the_shopping_list_exactly_where_it_was(signed_in):
    """
    A draft has nothing on the list yet, so there is nothing to reverse and
    nothing to buy — approval does it, as it does for every other draft.
    The grocery list is never written to without the household saying so.
    """
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    new = _recipe("Chana Masala (mum's)", [("Chickpeas", "2 cans"), ("Amchur", "1 tsp")])
    entry = _entry(plan, TUE, "Chana Masala")
    before = _needed()

    res = tools.change_meal_recipe(entry, new)

    assert res["approved"] is False
    assert res["lines_changed"] == 0
    assert _needed() == before == {}
    assert "changed on the list" not in res["said"]


def test_an_approved_weeks_list_loses_the_old_ingredients_and_gains_the_new(signed_in):
    """
    The half with teeth. An approved week has been shopped from, so the
    change has to move the list — through the same reversal and ingest the
    leftover rescale and the swap use, never a second arithmetic.
    """
    plan = _plan("approved")
    _recipe("Chana Masala", [("Chickpeas", "2 cans"), ("Coconut milk", "1 can")])
    new = _recipe("Chana Masala (mum's)", [("Chickpeas", "2 cans"), ("Amchur", "1 tsp")])
    entry = _entry(plan, TUE, "Chana Masala", buy=True)
    before = _needed()
    assert "Coconut milk" in before and "Chickpeas" in before

    res = tools.change_meal_recipe(entry, new)

    after = _needed()
    assert res["status"] == "changed", res
    assert res["approved"] is True
    assert "Coconut milk" not in after, after
    assert "Amchur" in after, after
    assert "Chickpeas" in after, after
    assert res["lines_changed"] >= 2
    assert "changed on the list" in res["said"]


def test_the_toast_says_the_dish_now_uses_your_recipe_when_the_name_is_the_same(signed_in):
    """
    The card's own sentence. Said when the dish is still called the same
    thing, which is the ordinary case for a household picking their own
    version of it.
    """
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    new = _recipe("Chana Masala (mum’s)", [("Chickpeas", "2 cans"), ("Amchur", "1 tsp")])
    entry = _entry(plan, TUE, "Chana Masala")
    # One recipe per name is this app's rule, so "the same name" on disk is
    # reached by renaming rather than by saving a second "Chana Masala" —
    # which is exactly the state a household picking their own version of a
    # dish ends up in once they have corrected the name.
    conn = get_conn()
    try:
        conn.execute("UPDATE recipes SET name = ? WHERE id = ?", ("Chana Masala", new))
        conn.commit()
    finally:
        conn.close()

    said = tools.change_meal_recipe(entry, new)["said"]

    assert said == "Chana Masala now uses your recipe."


def test_a_differently_named_recipe_renames_the_meal_and_the_toast_says_so(signed_in):
    """
    §8: never say a thing that isn't true. Re-pointing the recipe IS what
    renames the meal — the plan reads a meal's name off its recipe — so
    "now uses your recipe" would be the quieter half of what happened.
    """
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    new = _recipe("Rajma", [("Kidney beans", "2 cans")])
    entry = _entry(plan, TUE, "Chana Masala")

    said = tools.change_meal_recipe(entry, new)["said"]

    assert said == "Chana Masala is now Rajma."


def test_a_leftover_chain_follows_the_meal_it_reheats(signed_in):
    """
    A reheat night eats the cook night's food, so it is the same recipe by
    construction — both ends move together or the week says one dish is
    two different things.
    """
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    new = _recipe("Rajma", [("Kidney beans", "2 cans")])
    cook = _entry(plan, MON, "Chana Masala")
    reheat = _entry(plan, WED, "Chana Masala", slot="lunch")
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
            (json.dumps({"links_to": f"{MON}:dinner"}), reheat),
        )
        conn.execute(
            "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
            (json.dumps({"make_double_for": [f"{WED}:lunch"]}), cook),
        )
        conn.commit()
    finally:
        conn.close()

    res = tools.change_meal_recipe(cook, new)

    assert sorted(res["entry_ids"]) == sorted([cook, reheat])
    assert _entry_recipe(cook)["recipe_id"] == new
    assert _entry_recipe(reheat)["recipe_id"] == new


def test_an_unrelated_meal_on_the_same_recipe_is_left_alone(signed_in):
    """
    The chain is the only thing that follows. Two nights that happen to
    share a recipe but are not a cook-and-reheat pair are two decisions,
    and changing one must not change the other.
    """
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    new = _recipe("Rajma", [("Kidney beans", "2 cans")])
    one = _entry(plan, MON, "Chana Masala")
    other = _entry(plan, WED, "Chana Masala")

    res = tools.change_meal_recipe(one, new)

    assert res["entry_ids"] == [one]
    assert _entry_recipe(other)["recipe_id"] != new


# ---------- 2. the refusals ----------

def test_a_recipe_this_household_does_not_have_is_refused_and_writes_nothing(signed_in):
    """A refusal is a sentence for a reader at 200, and the plan is as it was."""
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    entry = _entry(plan, TUE, "Chana Masala")
    was = _entry_recipe(entry)

    res = tools.change_meal_recipe(entry, 99999)

    assert res == {"status": "refused", "said": recipe_change.NO_SUCH_RECIPE}
    assert _entry_recipe(entry) == was


def test_another_households_recipe_is_not_this_ones_to_pick(signed_in):
    """Household scope, at the one read that could cross it."""
    from app import households

    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    entry = _entry(plan, TUE, "Chana Masala")
    other = households.create_household("Recipe Change Isolation", "recipe-change-isolation-pass")
    with tools.use_household(other):
        theirs = _recipe("Their Rajma", [("Kidney beans", "2 cans")])

    res = tools.change_meal_recipe(entry, theirs)

    assert res["said"] == recipe_change.NO_SUCH_RECIPE
    assert _entry_recipe(entry)["recipe_id"] != theirs


def test_the_recipe_it_already_uses_is_not_a_change(signed_in):
    """Offering it would be offering a refusal, which is why the sheet
    leaves it out of the list — and the server says so either way."""
    plan = _plan("draft")
    same = _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    entry = _entry(plan, TUE, "Chana Masala")

    res = tools.change_meal_recipe(entry, same)

    assert res == {"status": "refused", "said": recipe_change.SAME_RECIPE}


def test_a_reheat_night_is_refused_and_pointed_at_the_dinner_it_comes_from(signed_in):
    """
    There is no recipe behind a reheat. The honest answer names the night
    that does have one, rather than a dead end.
    """
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    new = _recipe("Rajma", [("Kidney beans", "2 cans")])
    cook = _entry(plan, MON, "Chana Masala")
    reheat = _entry(plan, WED, "Chana Masala", slot="lunch")
    conn = get_conn()
    try:
        conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                     (json.dumps({"links_to": f"{MON}:dinner"}), reheat))
        conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                     (json.dumps({"make_double_for": [f"{WED}:lunch"]}), cook))
        conn.commit()
    finally:
        conn.close()

    res = tools.change_meal_recipe(reheat, new)

    assert res["status"] == "refused"
    assert res["said"] == recipe_change.REHEAT_NOT_CHANGEABLE
    assert "comes from" in res["said"]
    assert _entry_recipe(reheat)["recipe_id"] != new


def test_a_night_written_in_words_as_leftovers_is_a_reheat_too(signed_in):
    """
    The planner writes some reheat nights as freeform text rather than as a
    chain, so the test has to be the one build_slot makes — the word, not
    only the link.
    """
    plan = _plan("draft")
    new = _recipe("Rajma", [("Kidney beans", "2 cans")])
    entry = _entry(plan, WED, "Leftovers from Monday’s chana masala", slot="lunch")

    res = tools.change_meal_recipe(entry, new)

    assert res["said"] == recipe_change.REHEAT_NOT_CHANGEABLE


def test_a_night_nobody_is_home_for_has_nothing_to_change(signed_in):
    """
    A planned_empty slot must never be offered as a decision (three bugs
    have come from treating it as a missing meal), and an open one is a
    question rather than a dish.
    """
    plan = _plan("draft")
    new = _recipe("Rajma", [("Kidney beans", "2 cans")])
    tools.plan_slot_empty(plan, TUE, "dinner", reason="Nobody home")
    conn = get_conn()
    try:
        entry = conn.execute(
            "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
            (plan, TUE),
        ).fetchone()["id"]
    finally:
        conn.close()

    res = tools.change_meal_recipe(entry, new)

    assert res == {"status": "refused", "said": recipe_change.NOT_CHANGEABLE}


# ---------- 3. the allergy gate ----------

def test_a_pick_somebody_here_cannot_eat_is_blocked_by_name_and_by_ingredient(signed_in):
    """
    The card's own criterion. A bare "that doesn't work" leaves nowhere to
    go, so the sentence says WHO and WHAT — and it is one reading of
    coordination.check_meal_conflicts, the matcher the draft's banner and
    the swap gate already share.
    """
    tools.add_member("Reid")
    tools.set_member_age_group("Reid", "child")
    tools.set_member_dietary_restrictions("Reid", ["allergy: peanut"])
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    new = _recipe("Satay Noodles", [("Peanut butter", "3 tbsp"), ("Noodles", "400 g")])
    entry = _entry(plan, TUE, "Chana Masala")

    res = tools.change_meal_recipe(entry, new)

    assert res["status"] == "blocked", res
    assert res["member"] == "Reid"
    assert "peanut" in res["ingredient"].lower()
    assert "Reid" in res["said"] and "peanut" in res["said"].lower()
    assert res["said"].rstrip().endswith("Pick another.")


def test_a_blocked_pick_has_written_nothing_at_all(signed_in):
    """
    The gate runs before the transaction opens. An approved week's list is
    the thing that proves it: a block that reversed the old ingredients and
    then refused would leave the week holding a dish nobody bought for.
    """
    tools.add_member("Reid")
    tools.set_member_age_group("Reid", "child")
    tools.set_member_dietary_restrictions("Reid", ["allergy: peanut"])
    plan = _plan("approved")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    new = _recipe("Satay Noodles", [("Peanut butter", "3 tbsp"), ("Noodles", "400 g")])
    entry = _entry(plan, TUE, "Chana Masala", buy=True)
    before = _needed()
    was = _entry_recipe(entry)

    tools.change_meal_recipe(entry, new)

    assert _needed() == before
    assert _entry_recipe(entry) == was
    assert "Peanut butter" not in _needed()


def test_a_wont_eat_is_not_a_refusal_because_the_household_asked_for_it(signed_in):
    """
    GUARD on the one judgment call here, pinned by the mutation that widens
    the gate to every severity. A soft clash is a standing DISLIKE; the
    household picked this recipe out of their own book by name, and
    refusing it over one would be the app overruling a choice — which is
    the reading swap_in_place's own gate already makes.

    The fixture is a `dislikes` entry on purpose, and it took a mutation
    measuring 0 red to find out why: a household-level What-we-know note
    with hard=False produces NO clash at all (coordination only reads the
    hard ones), and a member dietary restriction produces a HARD one even
    without the word allergy. `dislikes` is the one source of severity
    'soft' there is, so it is the only fixture that can tell this guard
    apart from a test that passes because nothing was matched.
    """
    tools.add_member("Vic")
    tools.set_member_age_group("Vic", "adult")
    tools.edit_preference("dislikes", ["mushrooms"])
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    new = _recipe("Mushroom Risotto", [("Mushrooms", "400 g"), ("Arborio rice", "300 g")])
    entry = _entry(plan, TUE, "Chana Masala")

    # The soft clash really is there, or this guard proves nothing.
    from app.tools import coordination

    assert [c["severity"] for c in coordination.check_meal_conflicts(
        "Mushroom Risotto",
        ingredients=[{"item": "Mushrooms", "qty": "400 g"}])] == ["soft"]

    res = tools.change_meal_recipe(entry, new)

    assert res["status"] == "changed", res
    assert _entry_recipe(entry)["recipe_id"] == new


def test_a_side_on_the_night_is_checked_as_well_as_the_dish(signed_in):
    """
    A plate's side is part of what lands on the table, and the gate reads
    the entry's own sides — not just the recipe it is being pointed at.
    """
    tools.add_member("Reid")
    tools.set_member_age_group("Reid", "child")
    tools.set_member_dietary_restrictions("Reid", ["allergy: peanut"])
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    new = _recipe("Rajma", [("Kidney beans", "2 cans")])
    entry = _entry(plan, TUE, "Chana Masala")
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE meal_plan_entries SET sides_json = ? WHERE id = ?",
            (json.dumps([{"name": "Peanut slaw",
                          "ingredients": [{"item": "Peanut butter", "qty": "2 tbsp"}]}]), entry),
        )
        conn.commit()
    finally:
        conn.close()

    res = tools.change_meal_recipe(entry, new)

    assert res["status"] == "blocked", res
    assert res["member"] == "Reid"


# ---------- 4. the prose, and what the report may say about it ----------

def test_what_someone_typed_is_stored_word_for_word(signed_in):
    """
    The feature. Emily asked for every request to be recorded so the common
    ones can become buttons later; a summary, a tag or a truncation throws
    away the only evidence of which ones those are.
    """
    typed = (
        "Less spicy — Reid won't touch it — and we don't have a pressure cooker, "
        "so could it be the \"one pan\" version?"
    )
    rid = tools.record_recipe_change_request("Chana Masala", typed)

    rows = _rows()
    assert rid and len(rows) == 1
    assert rows[0]["request_text"] == typed
    assert rows[0]["dish_name"] == "Chana Masala"
    assert rows[0]["outcome"] == recipe_change.OUTCOME_REWRITTEN


def test_a_request_is_credited_to_whoever_the_device_is_signed_in_as(signed_in, monkeypatch):
    """
    The card asks for "member (if known)". The one honest answer is the
    session's own adult — not a name a caller passes, which could be
    anybody — so this drives a real REQUEST rather than the tool, since
    member_id() reads the session.

    It drives the FAILED path on purpose: the request is stored whether or
    not the rewrite lands, because an ask the app could not answer is the
    most useful kind for Emily to see.
    """
    from app import agent

    emily = tools.add_member("Emily")["member_id"]
    tools.set_member_age_group("Emily", "adult")
    signed_in.post("/api/whoami/pick", json={"member_id": emily})
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    entry = _entry(plan, TUE, "Chana Masala")
    monkeypatch.setattr(agent, "generate_recipe_details_llm",
                        lambda spec: {"ingredients": [], "instructions": []})

    res = signed_in.post("/api/meal-recipe/rewrite", json={"entry_id": entry, "text": "Less spicy"})

    # The ask is on file either way; the household is told it did not work.
    assert res.status_code == 400, res.text
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["member_id"] == emily
    assert rows[0]["outcome"] == recipe_change.OUTCOME_FAILED
    assert rows[0]["request_text"] == "Less spicy"


def test_a_request_from_a_device_that_has_not_said_who_it_is_is_filed_anyway(signed_in):
    """NULL where a device has not been asked — the request still matters."""
    rid = tools.record_recipe_change_request("Chana Masala", "Less spicy")
    assert rid
    assert _rows()[0]["member_id"] is None


def test_an_empty_request_is_not_a_request(signed_in):
    assert tools.record_recipe_change_request("Chana Masala", "   ") is None
    assert tools.record_recipe_change_request("Chana Masala", "") is None
    assert _rows() == []


def test_a_very_long_request_is_capped_rather_than_refused(signed_in):
    """
    The one thing done to the text. Refusing it outright would lose the
    whole request over its length, which is the opposite of the point.
    """
    tools.record_recipe_change_request("Chana Masala", "x" * 5000)
    assert len(_rows()[0]["request_text"]) == recipe_change.MAX_REQUEST_TEXT


def test_one_households_requests_are_never_another_households(signed_in):
    from app import households

    tools.record_recipe_change_request("Chana Masala", "Less spicy")
    other = households.create_household("Recipe Ask Isolation", "recipe-ask-isolation-pass")
    with tools.use_household(other):
        tools.record_recipe_change_request("Their Rajma", "More garlic")
        assert [r["request_text"] for r in tools.recent_recipe_change_requests()] == ["More garlic"]
        assert tools.count_recipe_change_requests() == 1

    assert [r["request_text"] for r in tools.recent_recipe_change_requests()] == ["Less spicy"]
    assert tools.count_recipe_change_requests() == 1


@pytest.mark.parametrize("text,theme", [
    ("Could this be quicker on a weeknight?", "time"),
    ("We don't have a pressure cooker", "equipment"),
    ("Too spicy for the kids", "spice"),
    ("Make it more authentic, how my mum did it", "authenticity"),
    ("Use dried chickpeas instead of canned", "ingredient swap"),
    ("I just don't love it", "other"),
    ("", "other"),
])
def test_a_request_is_filed_under_one_of_the_cards_five_themes(text, theme):
    """
    The theme is derived at READ time, so correcting the word list corrects
    every row ever stored — nothing is baked into the table. It is allowed
    to be wrong: the only reader is a heading in a report, and the words
    themselves are printed verbatim underneath it.
    """
    assert recipe_change.request_theme(text) == theme


def test_a_request_tripping_two_themes_takes_the_more_specific_one(signed_in):
    """
    First match wins, and the order is the point: "ingredient swap" is the
    widest of the five (its own word list holds "no ", "add " and "use "),
    so it goes last or it would swallow most of the others.
    """
    assert recipe_change.request_theme("Add less chilli so it's milder") == "spice"
    assert recipe_change.request_theme("Use the air fryer instead") == "equipment"
    assert recipe_change.THEMES[-1] == "other"
    assert recipe_change.THEMES[-2] == "ingredient swap"


def test_the_default_morning_report_carries_a_count_and_not_one_word(signed_in):
    """
    Emily's 2026-09-08 rule for prose, unchanged. That output is printed
    into a Claude agent's context under an instruction to act on what it
    reads; free text from an untrusted end arriving there is an injection
    channel, not just a privacy question.
    """
    import observability_report as rep

    typed = "Less spicy and no pressure cooker, please"
    tools.record_recipe_change_request("Chana Masala", typed)
    report, source = rep.collect(days=7)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rep._print_human(report, 7, source)
    out = buf.getvalue()

    assert typed not in out
    assert "Less spicy" not in out
    assert "1 recipe change request on file" in out
    assert "--recipe-changes" in out


def test_the_opt_in_reader_prints_the_words_and_fences_them(signed_in):
    """
    The other half: a flag nobody knows to run is a read path that does not
    exist, so the count above points at this. What it prints is labelled
    untrusted, grouped by theme, with the request verbatim underneath.
    """
    import observability_report as rep

    typed = "Less spicy, and we don't have a pressure cooker"
    tools.record_recipe_change_request("Chana Masala", typed)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rep._print_recipe_changes(rep.collect_recipe_changes(days=30), days=30)
    out = buf.getvalue()

    assert typed in out
    assert rep._UNTRUSTED_HEADER in out
    assert "untrusted, what they asked for" in out
    # Grouped under "equipment" rather than "spice": the themes are tried
    # in order and equipment comes first, which is the ordering this file
    # pins directly a few tests up.
    assert "--- equipment (" in out
    assert "Chana Masala" in out


def test_the_report_says_whether_a_rewrite_was_kept(signed_in):
    """
    The half of Emily's ask that is not the text itself. 'rewritten' means
    nobody put it back, which is what kept means here.
    """
    import observability_report as rep

    rid = tools.record_recipe_change_request("Chana Masala", "Less spicy")
    tools.mark_recipe_change_request(rid, recipe_change.OUTCOME_UNDONE)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rep._print_recipe_changes(rep.collect_recipe_changes(days=30), days=30)
    out = buf.getvalue()

    assert "undone" in out
    assert "kept" not in out


def test_the_table_holds_no_second_copy_of_anything_a_person_typed_elsewhere(signed_in):
    """
    A sweep rather than a claim: the only free text in this table is the
    request itself. Anything else a person typed arriving here would be a
    second copy of it with no rule attached.
    """
    conn = get_conn()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(recipe_change_requests)")}
    finally:
        conn.close()
    assert cols == {
        "id", "household_id", "member_id", "dish_name", "request_text",
        "meal_plan_entry_id", "recipe_id", "outcome", "previous_json", "created_at",
    }


def test_none_of_this_is_reachable_from_the_chat_agent(signed_in):
    """
    The same rule feedback_reports follows. A tool that read these back
    would put free text from the untrusted end into the model's own
    context, and nothing the household can ask for needs it.
    """
    from app import agent

    for name in ("record_recipe_change_request", "recent_recipe_change_requests",
                 "count_recipe_change_requests", "mark_recipe_change_request",
                 "undo_recipe_change", "change_meal_recipe", "apply_rewrite"):
        assert name not in agent.TOOL_FUNCTIONS, name
    names = {t["name"] for t in agent.TOOL_DEFINITIONS}
    assert not {n for n in names if "recipe_change" in n or "rewrite" in n}, names


# ---------- 5. rewriting one in place, and putting it back ----------

def test_a_rewrite_replaces_the_recipe_in_place_and_keeps_the_dishs_name(signed_in):
    """
    In place, not as a new recipe. The dish's name is read off its recipe,
    so a new row under a new name would rename the meal — and the card's
    own sentence is that the dish stays.
    """
    plan = _plan("draft")
    rid = _recipe("Chana Masala", [("Chickpeas", "2 cans")],
                  instructions=["Fry the onions.", "Add the chickpeas."])
    entry = _entry(plan, TUE, "Chana Masala")
    spec = tools.rewrite_spec(entry)

    res = tools.apply_rewrite(spec, {
        "ingredients": [{"item": "Dried chickpeas", "qty": "250 g"}, {"item": "Amchur", "qty": "1 tsp"}],
        "instructions": ["Soak the chickpeas overnight.", "Simmer with amchur."],
        "default_servings": 4,
    }, "Use dried chickpeas and more amchur")

    assert res["status"] == "rewritten", res
    assert res["said"].startswith("Chana Masala is rewritten")
    assert _entry_recipe(entry)["recipe_id"] == rid
    conn = get_conn()
    try:
        row = conn.execute("SELECT name, ingredients_json, instructions_json FROM recipes WHERE id = ?",
                           (rid,)).fetchone()
        assert row["name"] == "Chana Masala"
        assert "Dried chickpeas" in row["ingredients_json"]
        assert "Soak the chickpeas overnight." in row["instructions_json"]
        assert conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 1
    finally:
        conn.close()


def test_a_rewrite_records_the_request_and_what_it_replaced(signed_in):
    """
    The snapshot on the request row is what makes the undo exact rather
    than a guess — plan_undo's shape, and its reason: the record of a
    change is where what-it-replaced belongs.
    """
    plan = _plan("draft")
    rid = _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    entry = _entry(plan, TUE, "Chana Masala")
    spec = tools.rewrite_spec(entry)

    res = tools.apply_rewrite(spec, {
        "ingredients": [{"item": "Dried chickpeas", "qty": "250 g"}],
        "instructions": ["Soak them."],
    }, "Use dried chickpeas")

    row = _rows()[-1]
    assert row["request_text"] == "Use dried chickpeas"
    assert row["meal_plan_entry_id"] == entry
    assert row["recipe_id"] == rid
    assert row["outcome"] == recipe_change.OUTCOME_REWRITTEN
    snap = json.loads(row["previous_json"])
    assert snap["name"] == "Chana Masala"
    assert "Chickpeas" in snap["ingredients_json"]
    assert res["request_id"] == _rows()[-1]["household_id"] or res["request_id"]


def test_putting_it_back_restores_the_old_recipe_exactly(signed_in):
    """
    Restored from the snapshot, never regenerated: nothing else can know
    what the recipe said before, and a second model call would answer a
    different question.
    """
    plan = _plan("draft")
    rid = _recipe("Chana Masala", [("Chickpeas", "2 cans")],
                  instructions=["Fry the onions.", "Add the chickpeas."], default_servings=4)
    entry = _entry(plan, TUE, "Chana Masala")
    conn = get_conn()
    try:
        was = dict(conn.execute(
            "SELECT " + ", ".join(recipe_change._SNAPSHOT_COLUMNS) + " FROM recipes WHERE id = ?",
            (rid,)).fetchone())
    finally:
        conn.close()

    res = tools.apply_rewrite(tools.rewrite_spec(entry), {
        "ingredients": [{"item": "Dried chickpeas", "qty": "250 g"}],
        "instructions": ["Soak them."],
        "default_servings": 6,
    }, "Use dried chickpeas")
    undone = tools.undo_recipe_change(res["request_id"])

    assert undone["status"] == "undone", undone
    assert "back to the recipe it had" in undone["said"]
    conn = get_conn()
    try:
        now = dict(conn.execute(
            "SELECT " + ", ".join(recipe_change._SNAPSHOT_COLUMNS) + " FROM recipes WHERE id = ?",
            (rid,)).fetchone())
    finally:
        conn.close()
    assert now == was
    assert _rows()[-1]["outcome"] == recipe_change.OUTCOME_UNDONE


def test_putting_it_back_twice_says_so_rather_than_doing_it_again(signed_in):
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    entry = _entry(plan, TUE, "Chana Masala")
    res = tools.apply_rewrite(tools.rewrite_spec(entry), {
        "ingredients": [{"item": "Dried chickpeas", "qty": "250 g"}],
        "instructions": ["Soak them."],
    }, "Use dried chickpeas")
    tools.undo_recipe_change(res["request_id"])

    again = tools.undo_recipe_change(res["request_id"])

    assert again == {"status": "refused", "said": recipe_change.UNDO_GONE}


def test_an_approved_weeks_list_moves_with_a_rewrite_and_moves_back(signed_in):
    """
    The shopping follows the recipe in both directions, through the same
    pair — a rewrite that left the old ingredients on the list would have
    the household shopping for a dish they are no longer cooking.
    """
    plan = _plan("approved")
    _recipe("Chana Masala", [("Chickpeas", "2 cans"), ("Coconut milk", "1 can")])
    entry = _entry(plan, TUE, "Chana Masala", buy=True)
    before = _needed()
    assert "Coconut milk" in before

    res = tools.apply_rewrite(tools.rewrite_spec(entry), {
        "ingredients": [{"item": "Dried chickpeas", "qty": "250 g"}, {"item": "Amchur", "qty": "1 tsp"}],
        "instructions": ["Soak them.", "Simmer."],
    }, "Use dried chickpeas")

    after = _needed()
    assert "Coconut milk" not in after, after
    assert "Dried chickpeas" in after, after
    assert res["lines_changed"] >= 2
    assert "changed on the list" in res["said"]

    tools.undo_recipe_change(res["request_id"])

    back = _needed()
    assert "Coconut milk" in back, back
    assert "Dried chickpeas" not in back, back


def test_a_rewrite_that_comes_back_in_pieces_is_refused_before_anything_is_saved(signed_in):
    plan = _plan("draft")
    rid = _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    entry = _entry(plan, TUE, "Chana Masala")
    spec = tools.rewrite_spec(entry)

    for detail in ({"ingredients": [], "instructions": ["Soak them."]},
                   {"ingredients": [{"item": "Dried chickpeas", "qty": "250 g"}], "instructions": []}):
        with pytest.raises(ValueError):
            tools.apply_rewrite(spec, detail, "Use dried chickpeas")

    conn = get_conn()
    try:
        assert "Chickpeas" in conn.execute(
            "SELECT ingredients_json FROM recipes WHERE id = ?", (rid,)).fetchone()[0]
    finally:
        conn.close()
    assert _rows() == []


def test_a_meal_that_moved_while_the_rewrite_was_being_written_is_not_written_over(signed_in):
    """
    The other adult, or chat. Writing this rewrite onto whatever is there
    now would be answering a question about a dish nobody asked about.
    """
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    other = _recipe("Rajma", [("Kidney beans", "2 cans")])
    entry = _entry(plan, TUE, "Chana Masala")
    spec = tools.rewrite_spec(entry)
    tools.change_meal_recipe(entry, other)

    res = tools.apply_rewrite(spec, {
        "ingredients": [{"item": "Dried chickpeas", "qty": "250 g"}],
        "instructions": ["Soak them."],
    }, "Use dried chickpeas")

    assert res["status"] == "refused"
    assert "changed while I was writing" in res["said"]


def test_a_meal_whose_recipe_has_not_been_written_yet_says_when_it_will_be(signed_in):
    """
    A draft's new dishes carry details_pending — the recipe is written at
    approval (menu-first generation). There is nothing to rewrite yet, and
    the sentence says when there will be rather than refusing blankly.
    """
    plan = _plan("draft")
    tools.add_recipe("Chana Masala", [{"item": "Chickpeas", "qty": "2 cans"}], details_pending=True)
    entry = _entry(plan, TUE, "Chana Masala")

    with pytest.raises(ValueError) as e:
        tools.rewrite_spec(entry)
    assert str(e.value) == recipe_change.REWRITE_NO_RECIPE


def test_the_rewrite_spec_carries_the_dishs_own_name_so_the_writer_cannot_rename_it(signed_in):
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")], cuisine="Indian", main_protein="vegetarian")
    entry = _entry(plan, TUE, "Chana Masala")

    spec = tools.rewrite_spec(entry)

    assert spec["name"] == "Chana Masala"
    assert spec["entry_id"] == entry
    assert spec["cuisine"] == "Indian"
    assert spec["ingredients"] and spec["instructions"]


# ---------- 6. the routes ----------

def test_the_three_routes_need_a_signed_in_household(client):
    for path, body in (("/api/meal-recipe", {"entry_id": 1, "recipe_id": 1}),
                       ("/api/meal-recipe/rewrite", {"entry_id": 1, "text": "Less spicy"}),
                       ("/api/meal-recipe/undo", {"request_id": 1})):
        assert client.post(path, json=body).status_code == 401, path
    assert client.get("/api/recipe-changes").status_code == 401


def test_picking_a_recipe_over_http_answers_with_the_sentence_to_show(signed_in):
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    new = _recipe("Chana Masala (mum's)", [("Chickpeas", "2 cans"), ("Amchur", "1 tsp")])
    entry = _entry(plan, TUE, "Chana Masala")

    res = signed_in.post("/api/meal-recipe", json={"entry_id": entry, "recipe_id": new})

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "changed"
    assert "now uses your recipe" in res.json()["said"] or "is now" in res.json()["said"]


def test_a_refusal_is_a_sentence_at_200_not_an_error(signed_in):
    """
    An app that did exactly the right thing must not report itself broken —
    the shape add_dish_day and the chore rows already answer in.
    """
    plan = _plan("draft")
    same = _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    entry = _entry(plan, TUE, "Chana Masala")

    res = signed_in.post("/api/meal-recipe", json={"entry_id": entry, "recipe_id": same})

    assert res.status_code == 200, res.text
    assert res.json() == {"status": "refused", "said": recipe_change.SAME_RECIPE}


def test_a_meal_that_is_not_this_households_is_a_400_and_says_nothing_about_it(signed_in):
    res = signed_in.post("/api/meal-recipe", json={"entry_id": 987654, "recipe_id": 1})
    assert res.status_code == 400
    assert "987654" in res.json()["detail"]


def test_reading_the_requests_back_over_http_is_household_scoped(signed_in):
    from app import households

    tools.record_recipe_change_request("Chana Masala", "Less spicy")
    other = households.create_household("Recipe Ask HTTP Isolation", "recipe-ask-http-pass")
    with tools.use_household(other):
        tools.record_recipe_change_request("Their Rajma", "More garlic")

    body = signed_in.get("/api/recipe-changes").json()

    assert [r["request_text"] for r in body["requests"]] == ["Less spicy"]
    assert body["requests"][0]["theme"] == "spice"
    assert "More garlic" not in res_text(body)


# ---------- 7. the screen ----------

def res_text(body: dict) -> str:
    return json.dumps(body)


def _shell() -> str:
    from pathlib import Path

    return Path("static/shell.js").read_text()


def _css() -> str:
    from pathlib import Path

    return Path("static/shell.css").read_text()


def _changeable(meal: dict) -> bool:
    import json as _json

    from tests import nodeharness

    src = _shell()

    def extract(name: str) -> str:
        start = src.index(f"  function {name}(")
        end = src.index("\n  }\n", start) + len("\n  }\n")
        return src[start:end]

    harness = (
        "function escapeHtml(s) { return String(s == null ? '' : s); }\n"
        + "var CHANGE_RECIPE_LABEL = 'Change recipe';\n"
        + extract("recipeIsChangeable") + extract("recipeChangeBtnHtml")
        + f"console.log(JSON.stringify(recipeChangeBtnHtml({_json.dumps(meal)})));\n"
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return _json.loads(res.stdout.strip())


_COOK = {"entry_id": 7, "meal": "Chana Masala", "has_full_recipe": True, "is_leftovers": False}


def test_the_button_is_offered_on_a_meal_with_a_recipe_behind_it():
    out = _changeable(_COOK)
    assert 'data-cr="open"' in out
    assert 'data-entry-id="7"' in out
    assert "Change recipe" in out


@pytest.mark.parametrize("meal,why", [
    ({"entry_id": 7, "meal": "Leftovers", "has_full_recipe": True, "is_leftovers": True},
     "a reheat night has no cook in it"),
    ({"entry_id": 7, "meal": "Apple slices", "has_full_recipe": False, "is_leftovers": False},
     "a grab-and-go snack has no recipe to change"),
    ({"meal": "Chana Masala", "has_full_recipe": True, "is_leftovers": False},
     "no entry id, so there is nothing to address the change to"),
    (None, "no meal at all"),
])
def test_the_button_is_not_offered_where_there_is_nothing_to_change(meal, why):
    """The screen only has to not offer what it can see is wrong — the
    server refuses each of these in its own sentence anyway."""
    assert _changeable(meal) == "", why


def test_the_button_is_the_plain_outline_control_and_not_a_second_apricot():
    """
    Rule 5: a screen gets one apricot and it belongs to that screen's
    primary action, which on the recipe is the dock. The button reuses
    .wk-ing-add's own shape rather than a new one.
    """
    out = _changeable(_COOK)
    assert 'class="wk-ing-add recipe-change-btn"' in out
    # The RULE, not the first mention — the comment above the block names
    # the class too, and slicing from there would read the comment's own
    # "--apricot" as the button's.
    rule = _css()[_css().index(".recipe-change-btn {"):]
    rule = rule[:rule.index("}")]
    assert "--apricot" not in rule, rule


def test_the_sheet_offers_the_cards_four_doors_and_says_which_is_the_swap():
    """
    Pick from my recipes, paste a link, tell Pomona what to change — and
    "Different meal instead", in a card of its own, because it is a
    different kind of answer (it changes the night, not the recipe).
    """
    src = _shell()
    rows = src[src.index("function changeRecipeRowsHtml"):]
    rows = rows[:rows.index("\n  function crPickSubLine")]
    for label, key in (("Pick from my recipes", "pick"), ("Paste a link", "link"),
                       ("Tell Pomona what to change", "ask"), ("Different meal instead", "swap")):
        assert label in rows, label
        assert f'data-cr="{key}"' in rows, key
    assert "cr-rows-other" in rows


def test_the_ask_screen_is_one_box_and_one_button_and_no_chips():
    """
    Emily's override of 2026-10-04, verbatim: "Build no chips... Don't add
    recipe-suggestion generation to the recipe writer." Every request is
    recorded instead, so the common ones can become buttons later.
    """
    src = _shell()
    ask = src[src.index("function changeRecipeAskHtml"):]
    ask = ask[:ask.index("\n  function renderChangeRecipeSheet")]
    assert "<textarea" in ask
    assert ask.count('data-cr="rewrite"') == 1
    assert "defrost-chip" not in ask and "wwk-chip" not in ask and "class=\"chip" not in ask
    assert "Same dish" in ask


def test_the_sheets_one_apricot_is_the_rewrite_button():
    css = _css()
    go = css[css.index(".cr-go {"):]
    go = go[:go.index("}")]
    assert "var(--apricot)" in go
    assert "var(--on-accent-ink)" in go, "Rule 1: dark ink on a light accent fill"


def test_the_refusal_line_takes_the_text_on_tint_token_not_the_fill_one():
    """
    --urgent-ink is for an urgent FILL; on --urgent-tint it is
    light-on-light. --urgent-label is the pairing .wk-state.is-draft
    already uses.
    """
    css = _css()
    said = css[css.index(".cr-said {"):]
    said = said[:said.index("}")]
    assert "var(--urgent-tint)" in said
    assert "var(--urgent-label)" in said
    assert "--urgent-ink" not in said


def test_the_new_block_uses_tokens_only():
    """Rule 9. A literal here is a colour that cannot follow the theme."""
    import re

    css = _css()
    block = css[css.index(".wk-ing-acts {"):css.index(".recipes-cite-link")]
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", block), block


def test_the_sheet_is_a_real_sheet_and_not_just_the_insides():
    """
    The rules that make a sheet a sheet are ID-scoped in this file, not
    class-scoped — reusing the classes gets the insides and none of the
    container, which once shipped a dialog in document flow under the tab
    bar.
    """
    css = _css()
    for anchor in ("#recipes-scrim", "#recipes-sheet"):
        assert anchor in css
    # Every selector LIST that names the recipes sheet names this one too.
    # Comments stripped first, and the unit is the list rather than the
    # line, because a list of six IDs is written over several lines.
    import re as _re

    bare = _re.sub(r"/\*.*?\*/", "", css, flags=_re.S)
    lists = [m.group(1) for m in _re.finditer(r"([^{}]*)\{", bare)
             if "#recipes-sheet" in m.group(1) or "#recipes-scrim" in m.group(1)]
    assert len(lists) >= 4, lists
    for chunk in lists:
        assert "#cr-sheet" in chunk or "#cr-scrim" in chunk, chunk


def test_a_change_re_reads_every_screen_that_was_a_reading_of_what_moved():
    """
    The panels-build-once gotcha. The week, the kitchen and the list are
    all readings of the meal that just changed, so a change that refreshed
    only the screen it was tapped on would leave the other two stale.
    """
    src = _shell()
    after = src[src.index("async function crAfterChange"):]
    after = after[:after.index("\n  async function crUndo")]
    assert "closeChangeRecipeSheet()" in after
    assert "loadWeekMenu" in after
    assert "refreshKitchenPanel" in after or "loadKitchen" in after
    assert "refreshGroceryPanel" in after or "refreshGrocerySurfaces" in after
    assert "'Put it back'" in after


def test_the_link_import_is_no_longer_behind_the_in_development_flag():
    """
    "Paste a link" is one of the four doors now, so the row that flag gated
    has a home. The flag stays as the one line that turns it off again.
    """
    src = _shell()
    line = [l for l in src.splitlines() if "var RECIPE_LINK_IN_DEVELOPMENT" in l]
    assert len(line) == 1, line
    assert "false" in line[0]
