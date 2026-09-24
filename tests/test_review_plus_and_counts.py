"""
Three small things Emily settled on 2026-09-10, and the tests for them.
(The review stepper and its "+" strip went on 2026-09-18 — Check the week
is a carousel of day cards now — so the screen-side tests here went with
them; the server's add-dish-day / drop-dish-day tests stay.)

  1. THE REVIEW STEPPER'S "+" ASKS WHICH DAY. Every candidate day already
     holds a dish, so "+" always means REPLACING something — and a rule
     that picks the victim silently is exactly the failure this app keeps
     getting caught by. The strip shows what each day is holding and the
     household taps the one they are willing to spend.
  2. THE APPROVE BUTTON COUNTS ALL FOUR MEAL TYPES. It is a promise that
     nothing is left to decide, and an open snack is something left to
     decide.
  3. THE COOK'S SERVING COUNT IS SAVED WITH THE TICKS. They describe one
     cooking session, so a reload that brought back half-ticked amounts
     nobody chose was not an inconsistency — it was a screen that was
     wrong.

The same split every recent file here draws. BEHAVIOUR for the writes,
against the real functions and the real route; and THE SCREEN'S OWN
FUNCTIONS, RUN, under node against plain objects — not source markers,
because every bug in this batch is a value (a count, a target id, a stored
number) and a test that greps shell.js for the right words cannot see one.
"""
from __future__ import annotations

import datetime
import json
import shutil

import nodeharness
from pathlib import Path

import pytest

from app import tools
from app.db import get_conn


TODAY = datetime.date.today()
WEEK_START = TODAY.isoformat()
D0 = WEEK_START
D1 = (TODAY + datetime.timedelta(days=1)).isoformat()
D2 = (TODAY + datetime.timedelta(days=2)).isoformat()
D3 = (TODAY + datetime.timedelta(days=3)).isoformat()

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------- helpers

def _plan() -> int:
    return tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]


def _ids(day: str, slot: str) -> list[int]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ? "
        "ORDER BY id ASC", (tools.household_id(), day, slot),
    ).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _state(day: str, slot: str) -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = ? ORDER BY mpe.id ASC",
        (tools.household_id(), day, slot),
    ).fetchall()
    conn.close()
    return [(r["slot_state"], r["meal"]) for r in rows]


# ------------------------------------- CHANGE 1: the write behind the "+"

def test_the_dish_lands_on_the_day_the_household_picked():
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)

    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])

    assert out["status"] == "added"
    assert out["dish"] == "Chicken Tacos"
    assert _state(D1, "dinner") == [("planned", "Chicken Tacos")]
    # And the night it was copied FROM is untouched — this adds a day, it
    # does not move one.
    assert _state(D0, "dinner") == [("planned", "Chicken Tacos")]


def test_it_names_what_the_day_was_holding_so_the_screen_can_say_what_it_cost():
    """The household is agreeing to lose something. The toast names it, and
    the name has to come from the row that was actually displaced rather
    than from whatever the screen last drew."""
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)

    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])
    assert out["replaced"] == "Bean Chili"


def test_filling_an_open_slot_displaces_nothing_and_says_so():
    """An open slot is a question, not a dish — nothing is lost by
    answering it, and the sentence must not invent a loss."""
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_slot_open(plan, D1, "dinner", "I'd rather ask than guess on this one.")

    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])

    assert out["replaced"] is None
    assert _state(D1, "dinner") == [("planned", "Chicken Tacos")]


def test_a_night_nobody_is_home_is_never_a_day_to_plan_into():
    """
    planned_empty means nobody is home, or the household asked for none of
    that meal. Three separate bugs in this app have come from code reading
    that state as a free plate. Nothing on the screen offers one — and the
    rule holds at the WRITE too, because a control is not where a rule like
    this belongs.
    """
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_slot_empty(plan, D1, "dinner", "You’re out — I’ve planned nothing.")

    with pytest.raises(ValueError):
        tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])
    assert _state(D1, "dinner") == [("planned_empty", None)]


def test_adding_a_snack_leaves_the_days_other_snack_alone():
    """
    The same hazard the stepper going DOWN shipped with and had fixed in
    review: a day holds TWO rows at slot='snack' by default, so anything
    acting on one of them has to say WHICH. This goes through
    swap_meal_in_plan by entry id for exactly that reason — old_meal cannot
    say it for an OPEN target, which has no meal name at all.
    """
    plan = _plan()
    tools.plan_meal(D0, "Apple and peanut butter", slot="snack", weekly_plan_id=plan)
    tools.plan_meal(D1, "Apple and peanut butter", slot="snack", weekly_plan_id=plan)
    tools.plan_meal(D2, "Trail mix", slot="snack", weekly_plan_id=plan)
    tools.plan_meal(D2, "Greek yogurt", slot="snack", weekly_plan_id=plan)
    trail_mix = _ids(D2, "snack")[0]

    tools.add_dish_day(plan, _ids(D0, "snack")[0], trail_mix)

    # The Greek yogurt beside it survives, unmoved. (The new row is
    # appended, so it sorts after — the point is that there are still two
    # snacks and only the tapped one changed.)
    assert _state(D2, "snack") == [
        ("planned", "Greek yogurt"),
        ("planned", "Apple and peanut butter"),
    ]


def test_an_open_snack_is_filled_without_taking_the_real_one_with_it():
    """The case old_meal genuinely cannot express: an open slot has no
    name, so a by-name replacement would have to pass None and take every
    row in the slot — the two-snacks bug reached from the other side."""
    plan = _plan()
    tools.plan_meal(D0, "Apple and peanut butter", slot="snack", weekly_plan_id=plan)
    tools.plan_meal(D2, "Greek yogurt", slot="snack", weekly_plan_id=plan)
    tools.plan_slot_open(plan, D2, "snack", "You cut Trail mix back, so this one is yours.")
    open_row = _ids(D2, "snack")[1]

    tools.add_dish_day(plan, _ids(D0, "snack")[0], open_row)

    assert _state(D2, "snack") == [
        ("planned", "Greek yogurt"),
        ("planned", "Apple and peanut butter"),
    ]


def test_it_hands_back_the_changed_day_in_the_shape_the_screen_already_reads():
    """get_week_menu's own day dict, exactly as the stepper going down and
    the in-place swap both answer — which is what lets the Review screen
    splice one day into the week it holds instead of refetching."""
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Oatmeal", slot="breakfast", weekly_plan_id=plan)

    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])

    assert out["day"]["date"] == D1
    assert out["day"]["dinner"]["title"] == "Chicken Tacos"
    # The rest of the day comes back untouched, not just the slot that moved.
    assert out["day"]["breakfast"]["title"] == "Oatmeal"


def test_a_day_of_a_different_meal_is_refused():
    """The stepper lives inside a meal-type group and every day it offers
    is a day of that same meal. A breakfast dish onto a dinner is a
    different decision, and one nobody made on this screen."""
    plan = _plan()
    tools.plan_meal(D0, "Overnight Oats", slot="breakfast", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)

    with pytest.raises(ValueError):
        tools.add_dish_day(plan, _ids(D0, "breakfast")[0], _ids(D1, "dinner")[0])
    assert _state(D1, "dinner") == [("planned", "Bean Chili")]


def test_an_entry_from_another_week_is_refused_rather_than_quietly_moved():
    """Household- and plan-scoped both, the same rule the in-place swap and
    the stepper going down follow — an id from somewhere else is a refusal,
    not an edit of somebody else's dinner."""
    plan_a = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan_a)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan_a)
    plan_b = tools.create_weekly_plan(
        (TODAY + datetime.timedelta(days=21)).isoformat()
    )["weekly_plan_id"]

    with pytest.raises(ValueError):
        tools.add_dish_day(plan_b, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])
    assert _state(D1, "dinner") == [("planned", "Bean Chili")]


def test_the_shopping_list_follows_the_swap_rather_than_carrying_both_dishes():
    """
    The whole reason this composes swap_meal_in_plan instead of writing its
    own removal: reversing the displaced dish's contribution, and buying
    for the one going in, are things that function already does correctly
    on an approved week. A second implementation here is how two paths end
    up disagreeing about one household's shopping list.
    """
    plan = _plan()
    tools.add_recipe("Chicken Tacos", ingredients=[{"item": "Tortillas", "qty": "8"}])
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Kidney beans", "qty": "2 tin"}])
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    def names():
        return {i["item"] for i in tools.list_grocery_list()}
    assert "Kidney beans" in names()

    tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])

    assert "Kidney beans" not in names(), names()
    assert "Tortillas" in names(), names()


def test_the_route_answers_with_the_changed_day(signed_in):
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)

    res = signed_in.post(
        f"/api/week/{WEEK_START}/add-dish-day",
        json={"entry_id": _ids(D0, "dinner")[0],
              "target_entry_id": _ids(D1, "dinner")[0]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "added"
    assert body["replaced"] == "Bean Chili"
    assert body["day"]["dinner"]["title"] == "Chicken Tacos"


def test_the_route_refuses_a_deliberately_empty_target(signed_in):
    """
    A 200 that says no, the shape drop_dish_from_day already answers a
    refusal in — so the screen has one branch for both halves of the
    stepper. The sentence has to be the RULE refusing and not FastAPI
    answering about something else, which is why this reads the words and
    not only the status.
    """
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_slot_empty(plan, D1, "dinner", "You’re out — I’ve planned nothing.")

    res = signed_in.post(
        f"/api/week/{WEEK_START}/add-dish-day",
        json={"entry_id": _ids(D0, "dinner")[0],
              "target_entry_id": _ids(D1, "dinner")[0]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "refused"
    assert "a day to plan into" in res.json()["message"], res.text
    assert _state(D1, "dinner") == [("planned_empty", None)]


# --------------------------- the review pass, 2026-09-10 ---------------------

def _chain(plan: int, cook_day: str, cook: str, reheat_day: str, reheat: str,
           cook_slot: str = "dinner", reheat_slot: str = "lunch") -> None:
    """
    A confirmed leftovers chain, written by repair_leftover_chains rather
    than by hand — the pairing has to be one the app itself produces, or
    the test is about a shape that cannot occur.
    """
    tools.plan_meal(cook_day, cook, slot=cook_slot, weekly_plan_id=plan)
    tools.plan_meal(reheat_day, reheat, slot=reheat_slot, weekly_plan_id=plan)
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
        (json.dumps({"links_to": f"{cook_day}:{cook_slot}"}),
         _ids(reheat_day, reheat_slot)[-1]),
    )
    conn.commit()
    conn.close()
    tools.repair_leftover_chains(plan)


def _slot(plan: int, day: str, slot: str) -> dict:
    days = {d["date"]: d for d in tools.get_week_menu(plan)["days"]}
    return days[day][slot]


def test_a_reheat_row_adds_the_dish_it_reads_as_not_its_own_text():
    """
    BLOCKER, found in review and reproduced through the real route. A row on
    this screen is labelled by mealDisplayName, which for a confirmed reheat
    answers with the dish being reheated — so a row reading "Beef Bulgogi ·
    nothing to cook · 1 lunch" is a night whose stored text is "Leftover
    bulgogi bowls". Deriving the name from that row wrote a DIFFERENT dish
    from the one tapped: a night rendering as a reheat with no batch behind
    it, nothing bought for it, and the dish the household agreed to lose
    gone.

    Reachable for any chain whose reheat lands in a different meal-type
    group from its cook — a dinner cooked double for the next day's lunch,
    which is exactly what this sets up. Same class as the two blockers
    already fixed on this screen: a control labelled with one dish acting on
    another.
    """
    plan = _plan()
    tools.add_recipe("Beef Bulgogi", ingredients=[{"item": "Beef", "qty": "2 lb"}])
    _chain(plan, D0, "Beef Bulgogi", D1, "Leftover bulgogi bowls")
    tools.plan_meal(D2, "Tuna Sandwich", slot="lunch", weekly_plan_id=plan)
    reheat = _slot(plan, D1, "lunch")
    # The row really is the shape this is about: it reads as the cook.
    assert reheat["leftover_from"]["meal"] == "Beef Bulgogi"

    out = tools.add_dish_day(plan, reheat["entry_id"], _ids(D2, "lunch")[0])

    assert out["dish"] == "Beef Bulgogi", out["dish"]
    landed = _slot(plan, D2, "lunch")
    assert landed["title"] == "Beef Bulgogi"
    assert landed["source"] == "plan", "and it is a real cook, not a phantom reheat"


def test_the_dish_a_reheat_row_adds_is_actually_bought_for():
    """The other half of the same defect, and the one a household would
    notice: on an approved week the displaced dish came off the list and
    nothing went on for what replaced it."""
    plan = _plan()
    tools.add_recipe("Beef Bulgogi", ingredients=[{"item": "Beef", "qty": "2 lb"}])
    tools.add_recipe("Tuna Sandwich", ingredients=[{"item": "Tuna", "qty": "2 tin"}])
    _chain(plan, D0, "Beef Bulgogi", D1, "Leftover bulgogi bowls")
    tools.plan_meal(D2, "Tuna Sandwich", slot="lunch", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")

    def line(item):
        return next((i for i in tools.list_grocery_list() if i["item"] == item), None)

    assert line("Tuna")
    before = line("Beef")["quantity"]

    tools.add_dish_day(plan, _slot(plan, D1, "lunch")["entry_id"], _ids(D2, "lunch")[0])

    assert line("Tuna") is None, "the dish that was displaced comes off"
    assert line("Beef"), "and the dish that replaced it is bought for"
    assert line("Beef")["quantity"] != before, (
        "a second cook of it wants more beef than one did", line("Beef")["quantity"])


def test_breaking_a_chain_names_the_night_that_was_eating_off_it():
    """
    Going UP onto a night other nights are eating off was allowed and said
    nothing — the chain gone, the fed night quietly an ordinary cook. The
    DATA was right (swap_meal_in_plan re-buys for it), so the fix was to
    say so rather than to refuse: one screen must not refuse the mirror of
    what it silently allows.

    UPDATED 2026-09-24. The mirror this used to record in an aside — the
    stepper going DOWN refusing that night and naming the one eating off
    it — is gone: "−" re-plans the fed night itself now (Emily's standing
    rule, no "go change X first"). The aside went with it rather than
    being rewritten, because a refusal wrote nothing and the answer that
    replaced it moves the cook, which is not something this test can do
    before its own "+" and still be about the "+". That half lives in
    tests/test_drop_dish_self_solving.py now. The claim this test is named
    for is untouched.
    """
    plan = _plan()
    tools.add_recipe("Beef Bulgogi", ingredients=[{"item": "Beef", "qty": "2 lb"}])
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    _chain(plan, D1, "Beef Bulgogi", D2, "Leftover bulgogi",
           cook_slot="dinner", reheat_slot="dinner")
    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])

    assert out["unchained"] == [{"date": D2, "slot": "dinner"}], out["unchained"]


def test_an_ordinary_replacement_names_no_freed_nights():
    """The clause is only ever said when there is one — every other tap
    must not grow a sentence about nothing."""
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])
    assert out["unchained"] == []


def test_a_meal_already_cooked_is_not_a_day_to_plan_into():
    """
    Today's dinner, ticked off at seven, is not a past day — so the
    control's isPast test never covered it and the write had no status
    check at all. Replacing it destroyed the cooked record, left the
    inventory depleted for a meal now off the plan, and took the
    ingredients for a meal somebody had eaten off the shopping list.
    """
    plan = _plan()
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Kidney beans", "qty": "2 tin"}])
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    tools.check_off_meal(_ids(D1, "dinner")[0])

    with pytest.raises(ValueError):
        tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])

    assert _state(D1, "dinner") == [("planned", "Bean Chili")]
    conn = get_conn()
    status = conn.execute(
        "SELECT cooked_status FROM meal_plan_entries WHERE id = ?", (_ids(D1, "dinner")[0],)
    ).fetchone()["cooked_status"]
    conn.close()
    assert status == "done", "the record of somebody having cooked it survives"
    assert any(i["item"] == "Kidney beans" for i in tools.list_grocery_list(status="all")), \
        "and a meal that has been eaten keeps its line"


def test_the_week_payload_says_which_meals_have_been_cooked():
    """The control needs the same fact the write checks, and nothing on
    this payload carried it — so the picker could only ever have guessed
    from the date."""
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.check_off_meal(_ids(D1, "dinner")[0])
    assert _slot(plan, D1, "dinner")["cooked"] is True
    assert _slot(plan, D0, "dinner")["cooked"] is False


def test_the_stepper_going_down_will_not_delete_a_cooked_record_either():
    """
    Found on the SECOND review pass. The "+" grew a cooked check and the
    sibling half of the same stepper — built by this branch's predecessor,
    so not pre-existing the way "Change one" is — had none at all. "−"
    always takes the LAST day a dish covers, so any dish on two nights
    whose later one has been ticked had a live control that deleted the
    cooked record, left the inventory depleted for a meal now off the plan,
    and took an eaten meal's ingredients off the shopping list.
    """
    plan = _plan()
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Kidney beans", "qty": "2 tin"}])
    tools.plan_meal(D0, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    tools.check_off_meal(_ids(D1, "dinner")[0])

    out = tools.drop_dish_from_day(plan, _ids(D1, "dinner")[0])

    assert out["status"] == "refused", out
    assert "already" in out["message"], out["message"]
    assert _state(D1, "dinner") == [("planned", "Bean Chili")]
    conn = get_conn()
    status = conn.execute(
        "SELECT cooked_status FROM meal_plan_entries WHERE id = ?", (_ids(D1, "dinner")[0],)
    ).fetchone()["cooked_status"]
    conn.close()
    assert status == "done"
    assert any(i["item"] == "Kidney beans" for i in tools.list_grocery_list(status="all"))


def test_the_refusal_to_drop_a_cooked_night_names_it_by_the_dish_it_reads_as():
    """Same resolution `dish` in add_dish_day got, in the sibling: a
    confirmed reheat is labelled on screen with the dish it reheats, so a
    sentence built from its stored text names something nobody was shown."""
    plan = _plan()
    tools.add_recipe("Beef Bulgogi", ingredients=[{"item": "Beef", "qty": "2 lb"}])
    _chain(plan, D0, "Beef Bulgogi", D1, "Leftover bulgogi bowls",
           cook_slot="dinner", reheat_slot="lunch")
    tools.plan_meal(D2, "Leftover bulgogi bowls", slot="lunch", weekly_plan_id=plan)
    tools.check_off_meal(_ids(D1, "lunch")[0])

    out = tools.drop_dish_from_day(plan, _ids(D1, "lunch")[0])

    assert out["status"] == "refused"
    assert "Beef Bulgogi" in out["message"], out["message"]
    assert "Leftover bulgogi bowls" not in out["message"], out["message"]


def test_the_night_that_was_displaced_is_named_by_what_the_picker_showed():
    """
    Found on the second review pass, and the same defect as the blocker
    left half-fixed. `dish` was resolved through the chain and `replaced`
    was not, so the picker offered "Saturday · Bean Chili" and the toast
    reported "in place of Leftover chili" — the right night displaced, the
    wrong name reported, which is exactly the class this whole pass exists
    to close.
    """
    plan = _plan()
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Kidney beans", "qty": "2 tin"}])
    tools.add_recipe("Chicken Tacos", ingredients=[{"item": "Tortillas", "qty": "8"}])
    tools.plan_meal(D0, "Chicken Tacos", slot="lunch", weekly_plan_id=plan)
    _chain(plan, D1, "Bean Chili", D2, "Leftover chili",
           cook_slot="dinner", reheat_slot="lunch")
    # The picker shows that lunch as the dish it reheats.
    assert _slot(plan, D2, "lunch")["leftover_from"]["meal"] == "Bean Chili"

    out = tools.add_dish_day(plan, _ids(D0, "lunch")[0], _ids(D2, "lunch")[0])

    assert out["replaced"] == "Bean Chili", out["replaced"]


def test_the_two_stale_screen_sentences_are_shown_rather_than_swallowed():
    """
    The narrowing that introduced SlotRefused went one step too far: these
    two are written for a reader and were taking the 404 path, which the
    screen prints as the generic "that didn't work". By SlotRefused's own
    stated rule — it marks the sentences written for a person — they
    belong in it. Stale-screen-only, so the impact is small; the rule is
    the point.
    """
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    same = _ids(D0, "dinner")[0]

    with pytest.raises(tools.SlotRefused) as picked_its_own_day:
        tools.add_dish_day(plan, same, same)
    assert "already on that day" in str(picked_its_own_day.value)

    with pytest.raises(tools.SlotRefused) as day_already_has_it:
        tools.add_dish_day(plan, same, _ids(D1, "dinner")[0])
    assert "already has it" in str(day_already_has_it.value)


def test_a_sentence_for_a_person_comes_back_as_an_answer_and_an_id_does_not(signed_in):
    """The split, at the route: a refusal written for a reader is a 200
    carrying its words; anything else is a 404 and the screen shows its own
    plain line rather than a row id or a Python exception."""
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    same = _ids(D0, "dinner")[0]

    readable = signed_in.post(
        f"/api/week/{WEEK_START}/add-dish-day",
        json={"entry_id": same, "target_entry_id": _ids(D1, "dinner")[0]},
    )
    assert readable.status_code == 200, readable.text
    assert readable.json() == {"status": "refused", "message": "That day already has it."}

    nonsense = signed_in.post(
        f"/api/week/{WEEK_START}/add-dish-day",
        json={"entry_id": same, "target_entry_id": 999999},
    )
    assert nonsense.status_code == 404, nonsense.text
    assert "999999" in nonsense.json()["detail"], "the id is in the log, not on screen"


# ------------------- CHANGE 2: does generation make an open snack at all?

def test_week_generations_own_finishing_passes_never_hand_back_an_open_snack():
    """
    HALF OF THE QUESTION EMILY ASKED, pinned as a test rather than left as
    a claim in a report. Widening the Approve button's count to all four
    meal types would start labelling weeks that previously looked settled
    IF week GENERATION produced open snacks on its own.

    It does not. The two passes that could are both scoped to the three
    real meals: audit_plan_slots (the generation-gap pass) filters to
    WEEK_SLOTS, and repair_leftover_chains skips any row whose slot is not
    in it. This drives _finish_week_slots over a week with no snacks at all
    and asserts nothing hands one back as a question.

    It CHARACTERISES behaviour this branch does not change, so it is green
    on both sides — deliberately, because the answer is what Emily needs
    and a claim nobody can re-run is not one.

    Two things this does NOT say, and an earlier version of it wrongly
    implied both. The model can hand back an open snack itself:
    submit_weekly_plan's schema takes slot='snack' with slot_state='open'
    and the persist loop writes it through unchanged. And attendance can
    produce one with no assistant involved at all — see the test below,
    which is the leg the first answer to this question got wrong.
    """
    from app.agent import _finish_week_slots

    plan = _plan()
    dates = [(TODAY + datetime.timedelta(days=i)).isoformat() for i in range(7)]
    for d in dates:
        for slot in ("breakfast", "lunch", "dinner"):
            tools.plan_meal(d, f"Something for {slot}", slot=slot, weekly_plan_id=plan)

    _finish_week_slots(plan, WEEK_START, None, {}, day_count=7)

    conn = get_conn()
    rows = conn.execute(
        "SELECT slot, slot_state FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan,)
    ).fetchall()
    conn.close()
    open_snacks = [r for r in rows if r["slot"] == "snack" and r["slot_state"] == "open"]
    assert open_snacks == [], open_snacks


def test_an_ordinary_attendance_edit_does_hand_back_an_open_snack():
    """
    THE OTHER HALF, and the leg the first answer to Emily's question got
    WRONG. It said slot_needs' away-reopen "only validates the three real
    meals". It does not: _validate_slot defaults to allow_snack=True and
    both _ALL_SLOTS tuples include 'snack'. So marking somebody away for a
    snack and then back — an ordinary edit, no assistant anywhere near it —
    empties the slot and then hands it back as a question.

    Which makes the widening MORE right rather than less: an open snack
    from an away-reopen is a real decision handed back to the household,
    and a button promising nothing is left to decide has to count it.
    """
    plan = _plan()
    tools.add_member("Emily")
    tools.plan_meal(D0, "Trail mix", slot="snack", weekly_plan_id=plan)
    assert [s for s, _ in _state(D0, "snack")] == ["planned"]

    tools.set_member_attendance(D0, "snack", "Emily", present=False)
    assert [s for s, _ in _state(D0, "snack")] == ["planned_empty"]

    tools.set_member_attendance(D0, "snack", "Emily", present=True)
    assert [s for s, _ in _state(D0, "snack")] == ["open"]

    # ...and it is a real question with a reason on it, which is what makes
    # it something the Approve button owes the household a count of.
    conn = get_conn()
    reason = conn.execute(
        "SELECT open_reason FROM meal_plan_entries WHERE date = ? AND slot = 'snack' "
        "ORDER BY id DESC LIMIT 1", (D0,)).fetchone()["open_reason"]
    conn.close()
    assert reason and "Emily" in reason


# ------------------------------------------- the screen's own functions
# (the stepper's and the strip's own tests went with them, 2026-09-18)

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)


def _extract(name: str, source: str = SHELL_JS) -> str:
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start : j + 1]


def _extract_var(name: str, source: str = SHELL_JS) -> str:
    start = source.index(f"var {name} = ")
    depth, quote, j = 0, "", source.index("=", start) + 1
    while True:
        c = source[j]
        if quote:
            if c == "\\":
                j += 2
                continue
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth == 0:
            break
        j += 1
    return source[start : j + 1]


def _run_node(harness: str):
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_ESCAPE = _extract("escapeHtml") + "\n"
_SLOT_FURNITURE = (
    "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
    "var SLOT_LABELS = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };\n"
    "function dayName(d, opts){ return { 'MON': 'Monday', 'TUE': 'Tuesday',"
    " 'WED': 'Wednesday', 'THU': 'Thursday' }[d] || d; }\n"
    + _extract("isSnackSlot") + "\n"
    + _extract("snackSlotKey") + "\n"
    + _extract("daySlotEntry") + "\n"
    + _extract("daySlotKeys") + "\n"
    + _extract("slotEyebrowLabel") + "\n"
)


def _planned(title, source="plan", entry_id=1):
    return {"title": title, "state": "planned", "source": source, "entry_id": entry_id}

def _open(entry_id=9):
    return {"title": "I’d like your call on this one", "state": "open",
            "source": "open", "entry_id": entry_id}

def _empty(entry_id=8):
    return {"title": "Out — nothing to cook", "state": "planned_empty",
            "source": "empty", "entry_id": entry_id}

def _day(date, **slots):
    day = {"date": date, "before_plan_start": False, "isPast": False,
           "isToday": False, "snacks": slots.pop("snacks", [])}
    for s in ("breakfast", "lunch", "dinner"):
        day[s] = slots.get(s)
    day["snack"] = (day["snacks"] or [None])[0]
    return day


# ------------------------------ CHANGE 2: the Approve button's own count

def _count(days):
    harness = (
        _SLOT_FURNITURE
        + _extract("countOpenSlots") + "\n"
        + _extract("approveWithOpenLabel") + "\n"
        + "var data = { days: " + json.dumps(days) + " };\n"
        + "var n = countOpenSlots(data);\n"
        + "console.log(JSON.stringify({ n: n, label: n ? approveWithOpenLabel(data, n) : '' }));\n"
    )
    return _run_node(harness)


@_needs_node
def test_an_open_snack_is_something_left_to_decide():
    """
    Emily, 2026-09-10. The button is a promise that nothing is left to
    decide, and before this it counted three meal types out of four — so
    "Which days" said `SNACK 2 · Your call` while the button read "Approve
    and build my shopping list". The screen knew and the button did not.
    """
    days = [_day("MON", dinner=_planned("Chicken Tacos"),
                 snacks=[_planned("Apple", entry_id=5), _open(entry_id=6)])]
    assert _count(days)["n"] == 1


@_needs_node
def test_one_open_snack_is_named_by_its_day():
    days = [_day("MON", dinner=_planned("Chicken Tacos")),
            _day("TUE", snacks=[_open(entry_id=6)])]
    assert _count(days)["label"] == "Approve — leave Tuesday open"


@_needs_node
def test_several_open_slots_across_all_four_meal_types_are_counted_together():
    days = [_day("MON", breakfast=_open(entry_id=1),
                 snacks=[_planned("Apple", entry_id=2), _open(entry_id=3)]),
            _day("TUE", dinner=_open(entry_id=4)),
            _day("WED", lunch=_open(entry_id=5))]
    out = _count(days)
    assert out["n"] == 4
    assert out["label"] == "Approve — leave 4 slots open"


@_needs_node
def test_a_deliberately_empty_snack_is_still_not_a_decision():
    """NO-REGRESSION GUARD — it passes on the commit before this one too,
    and that is the point. Widening the count to four meal types must not
    widen it to four STATES. planned_empty is a night nobody is home, or a meal the
    household asked for none of — nothing to settle either way."""
    days = [_day("MON", snacks=[_empty(entry_id=1)]), _day("TUE", dinner=_empty(entry_id=2))]
    assert _count(days)["n"] == 0


@_needs_node
def test_a_settled_week_still_counts_nothing():
    """The other no-regression guard, and it also passes before the change:
    a week with nothing open must not start naming a slot now that snacks
    are counted."""
    days = [_day("MON", breakfast=_planned("Oats"), lunch=_planned("Soup"),
                 dinner=_planned("Chicken Tacos"),
                 snacks=[_planned("Apple", entry_id=5), _planned("Yogurt", entry_id=6)])]
    assert _count(days)["n"] == 0


def test_week_slots_is_still_exactly_the_three_real_meals():
    """
    NO-REGRESSION GUARD, green on both sides. The scope that did NOT
    change, pinned because the temptation is to fix
    the button by adding 'snack' to WEEK_SLOTS. That constant is the
    21-slot guarantee and the definition of a cook; weekCountsLabel,
    classifyDay, reviewDayIsClosed and reviewClosedLine all read it, and a
    snack counted as a cook is a different bug in four places.
    """
    assert "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];" in SHELL_JS
    assert "'snack'" not in _extract_var("WEEK_SLOTS")


# ------- the second review pass: where a refusal lands, and the minus -------


