"""
Chat can undo a batch (Emily, 2026-09-21, Loop Board): "don't batch the
rice", "cook the chili fresh on Thursday", "no batch cooking this week".

Batching is assumed from prep days since 2026-09-18/21 — nobody is asked,
the week is approved, and All set says what happened. Verification found
the chat had no batch or cook-ahead tool at all, so saying so did nothing
and the only way out was Swap on the Plan tab, which changes the DISH
rather than how it is cooked. Emily's guardrail: silent learning needs a
visible undo right where it shows.

Every test here is about tools.unbatch / tools.rebatch
(app/tools/batch_undo.py), the memory that keeps a re-approval from
putting the batch back, and the one card it draws.
"""
from __future__ import annotations

import datetime
import json

import pytest

import nodeharness
from conftest import household_today
from app import agent, db, main, tools
from app.tools import batch_components, batch_undo, cook_ahead, cooker, weekly_plan


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _day(n: int) -> str:
    return (_monday() + datetime.timedelta(days=n)).isoformat()


WEEK = _monday().isoformat()
MON, TUE, WED, THU, FRI = _day(0), _day(1), _day(2), _day(3), _day(4)


# ---------- the household, and a week that batches ----------

def _household():
    for name in ("Emily", "Vineeth", "Kid"):
        tools.add_member(name)
    tools.set_prep_days([{"weekday": "sunday", "minutes": 90}])


def _chili():
    tools.add_recipe(
        "Turkey Chili",
        ingredients=[{"item": "Ground turkey", "qty": "1 lb", "category": "meat"},
                     {"item": "Black beans", "qty": "2 cans", "category": "pantry"}],
        default_servings=4, prep_time_minutes=15, cook_time_minutes=40,
        instructions=["Brown the turkey.", "Simmer for 40 minutes."],
    )


def _egg_dishes():
    tools.add_recipe(
        "Egg Salad",
        ingredients=[{"item": "Eggs", "qty": "6", "category": "dairy"},
                     {"item": "Mayo", "qty": "1 jar", "category": "pantry"}],
        default_servings=4, prep_time_minutes=10, cook_time_minutes=10,
        instructions=["Hard-boil the eggs.", "Mix with the mayo."],
    )
    tools.add_recipe(
        "Breakfast Bowl",
        ingredients=[{"item": "Eggs", "qty": "4", "category": "dairy"},
                     {"item": "Rice", "qty": "1 cup", "category": "pantry"}],
        default_servings=4, prep_time_minutes=5, cook_time_minutes=15,
        instructions=["Hard-boil the eggs.", "Cook the rice."],
    )


def _plan(*meals, approve=True):
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    ids = {}
    for date_str, dish, slot in meals:
        ids[(date_str, slot)] = tools.plan_meal(
            date_str, dish, slot=slot, weekly_plan_id=plan_id
        )["entry_id"]
    if approve:
        tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id, ids


def _batched_week():
    """Turkey Chili on two nights, and eggs boiled by two different dishes."""
    _household()
    _chili()
    _egg_dishes()
    return _plan(
        (MON, "Turkey Chili", "dinner"), (THU, "Turkey Chili", "dinner"),
        (TUE, "Egg Salad", "lunch"), (WED, "Breakfast Bowl", "breakfast"),
    )


def _grocery():
    conn = db.get_conn()
    rows = sorted(
        (r["item"], r["quantity"])
        for r in conn.execute("SELECT item, quantity FROM grocery_items WHERE status = 'needed'").fetchall()
    )
    conn.close()
    return rows


def _prep_rows():
    conn = db.get_conn()
    rows = [(r["task_type"], r["description"]) for r in
            conn.execute("SELECT task_type, description FROM prep_tasks ORDER BY id").fetchall()]
    conn.close()
    return rows


def _ledger_entries():
    conn = db.get_conn()
    rows = {r["meal_plan_entry_id"] for r in conn.execute(
        "SELECT DISTINCT meal_plan_entry_id FROM meal_plan_grocery_links").fetchall()}
    conn.close()
    return rows


def _derived(entry_id):
    conn = db.get_conn()
    row = conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return json.loads(row["derived_from_json"] or "{}")


def _cards(plan_id):
    return {(m["date"], m["meal"]): (m.get("servings"), bool(m.get("is_leftovers")))
            for m in cooker.get_cooker_view(plan_id)["meals"]}


# ---------- the state the ticket starts from ----------

def test_approving_a_week_with_prep_days_really_does_batch_both_kinds():
    """The precondition, so nothing below can pass over a week that was
    never batched in the first place."""
    plan_id, ids = _batched_week()

    assert [(b["dish"], [c["date"] for c in b["covered"]])
            for b in cook_ahead.batched_dishes(plan_id)] == [("Turkey Chili", [THU])]
    assert [(b["label"], [c["dish"] for c in b["covered"]])
            for b in batch_components.batched_components(plan_id)] == [("Boiled eggs", ["Breakfast Bowl"])]
    assert _cards(plan_id)[(MON, "Turkey Chili")] == (6, False)
    assert _cards(plan_id)[(THU, "Turkey Chili")] == (3, True)


# ---------- each phrasing routes to the tool ----------

@pytest.mark.parametrize("what, day", [
    ("the rice", ""),           # "don't batch the rice"
    ("rice", ""),
    ("chili", THU),             # "cook the chili fresh on Thursday"
    ("the chili", ""),
    ("the eggs", ""),
    ("", ""),                   # "no batch cooking this week"
])
def test_the_agent_has_one_tool_and_every_phrasing_fits_it(what, day):
    """The card asks for ONE tool. Every phrasing in the user story is a
    call to it — a dish name, a component's ingredient, a named day, or
    nothing at all for the whole week."""
    assert "unbatch" in agent.TOOL_FUNCTIONS
    assert agent.TOOL_FUNCTIONS["unbatch"] is tools.unbatch
    schema = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "unbatch")
    assert set(schema["input_schema"]["properties"]) == {"what", "day"}
    assert schema["input_schema"]["required"] == []
    # Both arguments optional, so "no batch cooking this week" is a bare call.
    _batched_week()
    out = tools.unbatch(what, day) if (what or day) else tools.unbatch()
    assert out["status"] in {"unbatched", "nothing"}


def test_the_tool_is_named_in_the_system_prompt_with_its_three_phrasings():
    """A tool the model is never told about is a tool it never calls."""
    prompt = agent.SYSTEM_PROMPT
    assert "`unbatch`" in prompt
    assert "batch the rice" in prompt
    assert "fresh on Thursday" in prompt
    assert "no batch cooking this week" in prompt


# ---------- a repeated dish ----------

def test_cook_the_chili_fresh_on_thursday_puts_thursday_back_to_its_own_cook():
    plan_id, _ = _batched_week()

    out = tools.unbatch("the chili", THU)

    assert out["status"] == "unbatched"
    assert cook_ahead.batched_dishes(plan_id) == []
    cards = _cards(plan_id)
    # The first day's servings drop back, and Thursday is a cook again
    # rather than a reheat.
    assert cards[(MON, "Turkey Chili")] == (None, False)
    assert cards[(THU, "Turkey Chili")] == (None, False)


def test_both_halves_of_the_chain_are_gone_not_just_the_source():
    """plan_leftover_chains honours a pairing only when both halves agree,
    so a half-pruned chain reads as no chain and hides the bug."""
    plan_id, ids = _batched_week()
    source, target = ids[(MON, "dinner")], ids[(THU, "dinner")]

    tools.unbatch("chili")

    assert "make_double_for" not in _derived(source)
    assert "make_double_note" not in _derived(source)
    assert "links_to" not in _derived(target)
    assert cook_ahead.COOK_AHEAD_FLAG not in _derived(target)


def test_the_shopping_list_says_the_same_thing_before_and_after():
    """A batch changes what is COOKED, never what is eaten, so the week's
    own totals are invariant — the source's share is exactly the sum of
    the shares of the days it feeds. The ledger is re-split; the LINES do
    not move, and the reply is not allowed to claim they did."""
    plan_id, _ = _batched_week()
    before = _grocery()

    out = tools.unbatch("chili")

    assert _grocery() == before
    assert out["list_changed"] is False
    assert "list" not in out["said"].lower()


def test_the_fed_night_buys_its_own_portion_again():
    """The ledger, not the lines. A later rescale (any swap on that
    recipe) moves the whole amount onto the cook night and leaves the fed
    night holding none of it; un-batching has to give that night its own
    share back, or the next removal there subtracts nothing."""
    plan_id, ids = _batched_week()
    source, target = ids[(MON, "dinner")], ids[(THU, "dinner")]
    from app.tools import grocery as _grocery
    _grocery._reverse_meal_grocery_contributions(target)
    weekly_plan._rescale_leftover_source_grocery(source, target)
    assert target not in _ledger_entries(), "precondition: the fed night buys nothing while batched"

    out = tools.unbatch("chili")

    assert target in _ledger_entries()
    # And the LINES still say what they said: a batch changes what is
    # cooked, never what is eaten, so there is nothing for the reply to
    # claim about the list.
    assert out["list_changed"] is False
    assert "list" not in out["said"].lower()


def test_a_side_the_batch_had_swallowed_comes_back_and_the_reply_says_so():
    """The one shape where un-batching really does move a LINE: a fed
    night contributes nothing while it is a reheat — including its side —
    so a Thursday reheat with a fresh green salad had no lettuce on the
    list. (That a chained night's side is never bought is pre-existing and
    its own card; this pins that un-batching puts it right and says so.)"""
    _household()
    _chili()
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    mon_id = tools.plan_meal(MON, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    thu_id = tools.plan_meal(THU, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    conn = db.get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET sides_json = ? WHERE id = ?",
        (json.dumps([{"role": "side", "name": "Green Salad", "servings": 4,
                      "ingredients": [{"item": "Lettuce", "qty": "1 head", "category": "produce"}]}]),
         thu_id),
    )
    conn.commit()
    conn.close()
    tools.set_cook_ahead(mon_id, [thu_id])
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert not any(item == "Lettuce" for item, _ in _grocery())

    out = tools.unbatch("chili")

    assert any(item == "Lettuce" for item, _ in _grocery())
    assert out["list_changed"] is True
    assert out["said"].endswith("Your list has changed to match.")


def test_one_day_of_a_longer_batch_is_freed_and_the_rest_stays():
    _household()
    _chili()
    # Wednesday and Thursday, not Thursday and Friday: a batch covers at
    # most three days after its cook (the 2026-09-23 food-safety rule), so
    # a Monday cook no longer reaches Friday.
    plan_id, ids = _plan((MON, "Turkey Chili", "dinner"), (WED, "Turkey Chili", "dinner"),
                         (THU, "Turkey Chili", "dinner"))
    assert [c["date"] for c in cook_ahead.batched_dishes(plan_id)[0]["covered"]] == [WED, THU]

    out = tools.unbatch("chili", WED)

    assert out["status"] == "unbatched"
    assert [c["date"] for c in cook_ahead.batched_dishes(plan_id)[0]["covered"]] == [THU]
    assert "links_to" not in _derived(ids[(WED, "dinner")])
    assert "links_to" in _derived(ids[(THU, "dinner")])


def test_naming_the_day_that_cooks_takes_the_whole_batch_apart():
    """"Don't cook ahead on Monday" is about the cook, not about one of
    the nights it feeds."""
    _household()
    _chili()
    plan_id, _ = _plan((MON, "Turkey Chili", "dinner"), (THU, "Turkey Chili", "dinner"),
                       (FRI, "Turkey Chili", "dinner"))

    tools.unbatch("chili", MON)

    assert cook_ahead.batched_dishes(plan_id) == []


# ---------- a shared component ----------

def test_dont_batch_the_eggs_takes_the_prep_row_away():
    plan_id, _ = _batched_week()
    assert [t for t in _prep_rows() if t[0] == batch_components.BATCH_TASK_TYPE]

    out = tools.unbatch("the eggs")

    assert out["status"] == "unbatched"
    assert batch_components.batched_components(plan_id) == []
    assert [t for t in _prep_rows() if t[0] == batch_components.BATCH_TASK_TYPE] == []
    assert out["said"] == "Each dish boils its own eggs now."


def test_a_component_batch_is_never_left_standing_as_a_batch_of_one():
    """Two dishes or nothing — a row covering one dish is a prep task
    nobody can explain."""
    _household()
    _egg_dishes()
    tools.add_recipe(
        "Egg Toast", ingredients=[{"item": "Eggs", "qty": "2", "category": "dairy"},
                                  {"item": "Bread", "qty": "1 loaf", "category": "pantry"}],
        default_servings=4, prep_time_minutes=5, cook_time_minutes=8,
        instructions=["Hard-boil the eggs.", "Toast the bread."],
    )
    plan_id, ids = _plan((TUE, "Egg Salad", "lunch"), (WED, "Breakfast Bowl", "breakfast"),
                         (THU, "Egg Toast", "breakfast"))
    assert len(batch_components.batched_components(plan_id)[0]["covered"]) == 2

    tools.unbatch("eggs", WED)
    assert [c["dish"] for c in batch_components.batched_components(plan_id)[0]["covered"]] == ["Egg Toast"]

    tools.unbatch("eggs", THU)
    assert batch_components.batched_components(plan_id) == []


def test_clear_batch_component_is_its_own_write_not_an_empty_set_batch_component():
    """set_batch_component with nothing refuses, correctly, with a
    sentence about MAKING a batch — said to somebody undoing one."""
    plan_id, _ = _batched_week()
    key = batch_components.batched_components(plan_id)[0]["key"]

    assert isinstance(batch_components.set_batch_component(plan_id, key, []), str)
    assert batch_components.clear_batch_component(plan_id, key) == 1
    assert batch_components.batched_components(plan_id) == []
    # Idempotent: a second call has nothing to remove and says so.
    assert batch_components.clear_batch_component(plan_id, key) == 0


# ---------- everything this week ----------

def test_no_batch_cooking_this_week_takes_both_kinds_apart_in_one_call():
    plan_id, _ = _batched_week()

    out = tools.unbatch()

    assert out["status"] == "unbatched"
    assert cook_ahead.batched_dishes(plan_id) == []
    assert batch_components.batched_components(plan_id) == []
    assert out["said"] == "Nothing’s batched this week now — every dish cooks its own."


# ---------- undo ----------

def test_undo_puts_a_dish_batch_back_exactly_as_it_was():
    plan_id, _ = _batched_week()
    before_cards, before_grocery = _cards(plan_id), _grocery()

    out = tools.unbatch("chili")
    assert _cards(plan_id) != before_cards

    back = tools.rebatch(out["undo"])

    assert back["status"] == "rebatched"
    assert _cards(plan_id) == before_cards
    assert _grocery() == before_grocery


def test_undo_puts_a_component_batch_back_exactly_as_it_was():
    plan_id, _ = _batched_week()
    before = _prep_rows()

    out = tools.unbatch("the eggs")
    back = tools.rebatch(out["undo"])

    assert back["status"] == "rebatched"
    assert _prep_rows() == before


def test_undo_also_forgets_the_choice_so_the_week_batches_again():
    """An undo that left "never batch this" standing would put the batch
    back and then have the next approval take it away again."""
    plan_id, ids = _batched_week()
    out = tools.unbatch("chili")
    assert _derived(ids[(THU, "dinner")]).get(batch_undo.NO_BATCH_FLAG) is True

    tools.rebatch(out["undo"])

    assert batch_undo.NO_BATCH_FLAG not in _derived(ids[(THU, "dinner")])
    assert batch_undo.declined_dish_entry_ids(plan_id) == set()


def test_undo_never_moves_the_shopping_list():
    """Making a batch has never changed what has to be bought
    (cook_ahead.py), and a rescale here would re-ingest the fed night as a
    reheat and take its SIDE off the list with it."""
    _household()
    _chili()
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    mon_id = tools.plan_meal(MON, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    thu_id = tools.plan_meal(THU, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    conn = db.get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET sides_json = ? WHERE id = ?",
        (json.dumps([{"role": "side", "name": "Green Salad", "servings": 4,
                      "ingredients": [{"item": "Lettuce", "qty": "1 head", "category": "produce"}]}]),
         thu_id),
    )
    conn.commit()
    conn.close()
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert any(item == "Lettuce" for item, _ in _grocery())

    out = tools.unbatch("chili")
    tools.rebatch(out["undo"])

    assert any(item == "Lettuce" for item, _ in _grocery())


def test_an_undo_payload_for_another_households_plan_writes_nothing():
    plan_id, _ = _batched_week()
    out = tools.unbatch("chili")
    with tools.use_household(2):
        back = tools.rebatch(out["undo"])
    assert back["status"] == "refused"
    assert cook_ahead.batched_dishes(plan_id) == []


# ---------- remembering the choice ----------

def test_re_approving_the_week_does_not_put_a_dish_batch_back():
    plan_id, _ = _batched_week()
    tools.unbatch("chili")

    conn = db.get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'draft' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()
    out = tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert cook_ahead.batched_dishes(plan_id) == []
    assert [d["dish"] for d in out["cook_ahead"]["declined"] if d["kind"] == "dish"] == ["Turkey Chili"]


def test_re_approving_the_week_does_not_put_a_component_batch_back():
    plan_id, _ = _batched_week()
    tools.unbatch("the eggs")

    conn = db.get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'draft' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()
    out = tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert batch_components.batched_components(plan_id) == []
    assert [d["key"] for d in out["cook_ahead"]["declined"] if d["kind"] == "component"] == ["boiled:egg"]


def test_a_batch_nobody_objected_to_is_still_written_beside_one_they_did():
    """The memory is per batch, not a switch on the whole week."""
    plan_id, _ = _batched_week()
    tools.unbatch("chili")
    tools.unbatch("the eggs")
    # Put the component batch back by hand; only the dish stays declined.
    key = "boiled:egg"
    uses = [u["entry_id"] for c in batch_components.shared_components(plan_id) if c["key"] == key
            for u in c["uses"]]
    batch_components.set_batch_component(plan_id, key, uses)
    batch_undo.forget_no_batch_component(uses, key)

    conn = db.get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'draft' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert cook_ahead.batched_dishes(plan_id) == []
    assert batch_components.batched_components(plan_id) != []


def test_a_day_swapped_away_takes_its_own_objection_with_it():
    """The memory lives on the entry, so a new row is a new decision."""
    plan_id, ids = _batched_week()
    tools.unbatch("chili", THU)
    assert batch_undo.declined_dish_entry_ids(plan_id) == {ids[(THU, "dinner")]}

    tools.swap_meal_in_plan(plan_id, THU, "Egg Salad", slot="dinner", old_meal="Turkey Chili")

    assert batch_undo.declined_dish_entry_ids(plan_id) == set()


# ---------- saying only what is true ----------

def test_a_week_with_nothing_batched_says_so_and_writes_nothing():
    _household()
    _chili()
    plan_id, _ = _plan((MON, "Turkey Chili", "dinner"))

    out = tools.unbatch("chili")

    assert out["status"] == "nothing"
    assert out["said"] == "Nothing’s batched on this week."
    assert out["undo"] is None


def test_words_that_match_nothing_name_what_was_asked_for():
    _batched_week()
    out = tools.unbatch("the lasagne")
    assert out["status"] == "nothing"
    assert "lasagne" in out["said"]


def test_two_batches_answering_to_one_word_ask_rather_than_guess():
    """"Don't batch the eggs" with two egg batches on the plan is a
    question, not a coin toss."""
    _household()
    _egg_dishes()
    tools.add_recipe(
        "Egg Drop Soup", ingredients=[{"item": "Eggs", "qty": "3", "category": "dairy"},
                                      {"item": "Stock", "qty": "1 carton", "category": "pantry"}],
        default_servings=4, prep_time_minutes=5, cook_time_minutes=10,
        instructions=["Poach the eggs.", "Add the stock."],
    )
    tools.add_recipe(
        "Egg Drop Bowl", ingredients=[{"item": "Eggs", "qty": "2", "category": "dairy"},
                                      {"item": "Noodles", "qty": "1 bag", "category": "pantry"}],
        default_servings=4, prep_time_minutes=5, cook_time_minutes=10,
        instructions=["Poach the eggs.", "Boil the noodles."],
    )
    plan_id, _ = _plan((TUE, "Egg Salad", "lunch"), (WED, "Breakfast Bowl", "breakfast"),
                       (THU, "Egg Drop Soup", "dinner"), (FRI, "Egg Drop Bowl", "dinner"))
    assert len(batch_components.batched_components(plan_id)) >= 2

    out = tools.unbatch("the eggs")

    assert out["status"] == "ambiguous"
    assert out["said"].startswith("Which one")
    assert out["undo"] is None
    # And nothing was written while it asked.
    assert batch_components.batched_components(plan_id) != []


def test_a_planners_own_leftovers_night_is_not_a_batch_and_is_left_alone():
    """Nobody batched that — the week was planned around cooking once and
    eating twice. Changing it is swap_meal_in_plan's job."""
    _household()
    _chili()
    plan_id, ids = _plan((MON, "Turkey Chili", "dinner"), (THU, "Turkey Chili", "dinner"),
                         approve=False)
    # The planner's shape: a chain with no cook_ahead flag on it.
    source, target = ids[(MON, "dinner")], ids[(THU, "dinner")]
    for entry_id, derived in (
        (source, {"make_double_for": [f"{THU}:dinner"], "make_double_note": "One batch on Monday covers Thursday too."}),
        (target, {"links_to": f"{MON}:dinner"}),
    ):
        conn = db.get_conn()
        conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                     (json.dumps(derived), entry_id))
        conn.commit()
        conn.close()

    out = tools.unbatch("chili")

    assert out["status"] == "nothing"
    assert "make_double_for" in _derived(source)


def test_no_plan_at_all_says_so():
    _household()
    out = tools.unbatch("chili")
    assert out["status"] == "nothing"
    assert out["said"] == "There’s no plan on this week yet."


# ---------- the change card ----------

def _actions(tool_name, args, result):
    before = []
    after = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": tool_name, "input": args}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": json.dumps(result)}]},
    ]
    return main.summarize_chat_actions(before, after)


def test_the_card_is_the_tools_own_sentence_with_an_undo():
    said = "Thursday’s Turkey Chili cooks fresh now, not from Monday’s batch."
    undo = {"weekly_plan_id": 1, "dishes": [{"source_entry_id": 1, "covered_entry_ids": [2]}], "components": []}

    actions = _actions("unbatch", {"what": "chili", "day": THU},
                       {"status": "unbatched", "said": said, "undo": undo, "list_changed": False})

    assert len(actions) == 1
    card = actions[0]
    assert card.tab == "week"
    assert card.change == said
    assert card.undo == {"label": "Undo", "kind": "unbatch", "payload": undo}


def test_a_turn_that_wrote_nothing_draws_no_card():
    """'ambiguous' is a question and 'nothing' is a fact — neither changed
    a screen, and a card would say one did."""
    for status in ("ambiguous", "nothing", "refused"):
        actions = _actions("unbatch", {"what": "eggs"},
                           {"status": status, "said": "Which one — the eggs or the rice?", "undo": None})
        assert actions == [], status


def test_the_card_is_tagged_week_so_both_screens_refresh():
    """The panels-build-once gotcha: the card's tab is what re-reads Plan,
    and shell.js's `week` branch re-reads Shop with it."""
    assert "unbatch" in main._WEEK_TOOLS
    assert main._categorize_tool("unbatch") == ("week", "week", None)


def test_the_undo_route_puts_it_back(signed_in):
    plan_id, _ = _batched_week()
    out = tools.unbatch("chili")
    assert cook_ahead.batched_dishes(plan_id) == []

    res = signed_in.post("/api/week/unbatch-undo", json=out["undo"])

    assert res.status_code == 200
    assert res.json()["status"] == "rebatched"
    assert cook_ahead.batched_dishes(plan_id) != []


def test_a_refused_undo_is_a_sentence_at_200_not_an_error(signed_in):
    """A batch the week has moved under is a thing to read on the card."""
    _batched_week()
    res = signed_in.post("/api/week/unbatch-undo",
                         json={"weekly_plan_id": 9999, "dishes": [], "components": []})
    assert res.status_code == 200
    assert res.json()["status"] == "refused"


# ---------- the Undo button on the card ----------

_SHELL = None


def _shell():
    global _SHELL
    if _SHELL is None:
        with open("static/shell.js") as fh:
            _SHELL = fh.read()
    return _SHELL


def test_the_receipt_card_mounts_an_undo_when_the_action_carries_one():
    """Run the real renderer: a source marker cannot see a button that is
    never appended."""
    src = _shell()
    start = src.index("function mountActionUndo(")
    end = src.index("\n  }\n", start) + 4
    body = src[start:end]
    out = nodeharness.run_node("""
      %s
      var appended = [];
      var card = { appendChild: function (el) { appended.push(el); } };
      function fakeButton() {
        return { type: '', className: '', textContent: '', disabled: false,
                 addEventListener: function () {}, remove: function () {} };
      }
      var document = { createElement: function () { return fakeButton(); } };
      function showToast() {}
      function refreshStaleTabsFromActions() {}
      mountActionUndo(card, { undo: { label: 'Undo', payload: { weekly_plan_id: 1 } } });
      console.log(JSON.stringify({ n: appended.length, label: appended[0].textContent,
                                   cls: appended[0].className }));
    """ % body)
    assert out.returncode == 0, out.stderr
    parsed = json.loads(out.stdout.strip().splitlines()[-1])
    assert parsed["n"] == 1
    assert parsed["label"] == "Undo"
    # The Remembered chip's own button class, so the two cannot drift.
    assert "ask-remembered-fix" in parsed["cls"]


def test_the_card_only_mounts_an_undo_when_there_is_a_payload():
    src = _shell()
    assert "if (action.undo && action.undo.payload) mountActionUndo(receipt, action);" in src


# ==========================================================================
# Two weeks on file (found by review, 2026-09-24)
# ==========================================================================
#
# Nothing above this line builds more than one plan. `_plan` creates one
# and every test uses it, so no fixture in this file ever crossed a plan
# boundary — and a component batch was being resolved across every plan
# the household has, taking the most recently inserted row for the key.
# All 40 tests passed over it. That is the same shape as an isolation test
# that passes because the fixture never crossed the boundary it names, in
# its cross-PLAN form rather than the cross-household one the file already
# guards.
#
# Two approved weeks sharing a component key is the app's own
# "Plan next week ›" plus eggs or rice — this card's own examples.

def _second_week():
    """Next week, same two egg dishes, approved. Its prep row is newer."""
    nxt = (_monday() + datetime.timedelta(days=7)).isoformat()
    plan_id = tools.create_weekly_plan(nxt)["weekly_plan_id"]
    ids = {}
    for offset, dish, slot in ((1, "Egg Salad", "lunch"), (2, "Breakfast Bowl", "breakfast")):
        day = (_monday() + datetime.timedelta(days=7 + offset)).isoformat()
        ids[(day, slot)] = tools.plan_meal(day, dish, slot=slot, weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id, ids


def test_unbatching_this_week_does_not_reach_into_next_weeks_plan():
    """
    The objection belongs on the week the household said it about.

    Before the fix, `batch_source_id` found the component's source with a
    household-only query ordered by id — so next week's row, being newer,
    won, and un-batching THIS week wrote `no_batch_components` onto NEXT
    week's entry. Three harms, all reproduced: this week's own source
    never got the objection; next week silently declined a batch nobody
    objected to; and the card's Undo answered "Pick at least two dishes to
    make them at once.", which is the one sentence
    clear_batch_component's docstring says must never be shown to somebody
    undoing a batch.
    """
    plan_a, ids_a = _batched_week()
    plan_b, ids_b = _second_week()

    out = tools.unbatch("the eggs")
    assert out["status"] == "unbatched", out

    # The source it named is THIS week's, not next week's.
    a_ids = set(ids_a.values())
    b_ids = set(ids_b.values())
    named = {e for c in out["undo"]["components"] for e in c["entry_ids"]}
    assert named & a_ids, "it did not touch the week it was asked about"
    assert not (named & b_ids), f"it reached into next week's plan: {named & b_ids}"

    # Next week still batches when it is approved again.
    assert batch_undo.declined_component_keys(plan_b) == set(), (
        "next week was told not to batch something nobody objected to"
    )
    # And this week's own source carries the objection.
    assert batch_undo.declined_component_keys(plan_a), (
        "the week that was asked about was never marked"
    )


def test_the_undo_button_still_works_with_two_weeks_on_file():
    """
    The harm a household would actually meet: tap the Undo on the card and
    get a refusal written for somebody making a batch, with the batch not
    put back.
    """
    plan_a, _ = _batched_week()
    _second_week()

    out = tools.unbatch("the eggs")
    back = tools.rebatch(out["undo"])
    assert back["status"] != "refused", back
    assert batch_components.batched_components(plan_a), "the batch was not put back"


# ---------- one dish, two cooks (the three-day leftover rule, merged 2026-09-24) ----------

SAT = _day(5)


def test_dont_batch_a_dish_cooked_twice_takes_both_cooks_apart_without_asking():
    """Mon, Wed, Fri, Sat is two batches of the same chili (Monday's can't
    reach Friday). "Don't batch the chili" means both; asking "Which one —
    Turkey Chili and Turkey Chili?" left the household with no answer."""
    _household()
    _chili()
    plan_id, _ = _plan((MON, "Turkey Chili", "dinner"), (WED, "Turkey Chili", "dinner"),
                       (FRI, "Turkey Chili", "dinner"), (SAT, "Turkey Chili", "dinner"))
    assert len(cook_ahead.batched_dishes(plan_id)) == 2

    out = tools.unbatch("chili")

    assert out["status"] == "unbatched", out
    assert cook_ahead.batched_dishes(plan_id) == []


def test_the_all_set_line_names_a_dish_cooked_twice_once():
    from app.tools import weekly_plan
    _household()
    _chili()
    plan_id, _ = _plan((MON, "Turkey Chili", "dinner"), (WED, "Turkey Chili", "dinner"),
                       (FRI, "Turkey Chili", "dinner"), (SAT, "Turkey Chili", "dinner"))
    line = weekly_plan.batched_line(plan_id)
    assert line.count("Turkey Chili") == 1, line
    assert "2 cooks" in line and "one cook each" not in line, line
