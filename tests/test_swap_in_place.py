"""
Swap one meal, in place — Julia (first beta tester, 2026-09-08): "be able
to click on the one recipe and meal that the user wants to switch and then
have it regenerate just the one on the spot."

Two kinds of test, the same split tests/test_meals_week_day_meal.py uses:

  * BEHAVIOUR, for the backend — what the one model call is actually told,
    that the pick lands through the same swap_meal_in_plan a chat swap uses
    (so the grocery list, the leftover chain and the taste verdict behave
    identically), that an allergen clash costs one retry and then a plain
    refusal with nothing written, that Undo puts back the original, and that
    an entry belonging to somebody else is a 404 and not a swap.
  * SOURCE MARKERS, for the front end, because shell.js has no JS test
    harness in this repo (see tests/test_frontend_restored_2026_09_08.py's
    docstring for what a marker is worth).

The model is mocked everywhere. Two ways, on purpose: most tests inject a
`picker`, which is the seam swap_meal_in_place exposes for exactly this; the
ledger test stubs agent._client instead and goes through the real call
plumbing, because "a row lands in api_calls under swap_in_place" is a claim
about that plumbing and nothing else can prove it.
"""
from __future__ import annotations

import datetime
import types
from pathlib import Path

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import swap_in_place as sip
from app.tools._shared import use_household


TODAY = datetime.date.today()
WEEK_START = (TODAY - datetime.timedelta(days=TODAY.weekday())).isoformat()
DAYS = [(datetime.date.fromisoformat(WEEK_START) + datetime.timedelta(days=i)).isoformat()
        for i in range(7)]
MONDAY, TUESDAY, WEDNESDAY = DAYS[0], DAYS[1], DAYS[2]

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


# ---------- fixtures ----------


def _pick(name="Lemon Chicken Traybake", reason="Lighter than the chops, and nothing to thaw.",
          ingredients=None, **extra) -> dict:
    """One well-formed answer from the model."""
    return {
        "meal_name": name,
        "reason": reason,
        "is_new_recipe": True,
        "ingredients": ingredients if ingredients is not None else [
            {"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"},
            {"item": "Lemons", "qty": "3", "category": "produce"},
            {"item": "Baby potatoes", "qty": "1 bag", "category": "produce"},
        ],
        "instructions": ["Heat the oven.", "Roast everything together."],
        "food_groups": ["protein", "vegetable", "carb"],
        "cuisine": "Mediterranean",
        "main_protein": "chicken",
        "prep_time_minutes": 10,
        "cook_time_minutes": 35,
        "default_servings": 2,
        **extra,
    }


def _recorder(*picks):
    """
    A picker that hands back each pick in turn and keeps every context it
    was given — the contexts ARE the prompt, so this is how the prompt gets
    asserted without an API call.
    """
    seen: list[dict] = []

    def picker(context):
        seen.append(context)
        return picks[min(len(seen), len(picks)) - 1]

    picker.contexts = seen
    return picker


@pytest.fixture
def week():
    """
    A draft week with three real dinners, two members, and one saved dish
    that is safe. Returns the plan id.
    """
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    for name, groups in (("Pork Chops", ["protein"]), ("Chili", ["protein", "vegetable"]),
                         ("Fish Tacos", ["protein", "carb"])):
        tools.add_recipe(
            name,
            ingredients=[{"item": name.split()[0], "qty": "1 lb", "category": "meat/seafood"}],
            food_groups=groups, prep_time_minutes=10, cook_time_minutes=20,
        )
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    for day, dish in ((MONDAY, "Pork Chops"), (TUESDAY, "Chili"), (WEDNESDAY, "Fish Tacos")):
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan_id,
                        reasoning="fits the week")
    return plan_id


def _entry_id(plan_id: int, day: str, slot: str = "dinner") -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND weekly_plan_id = ? "
        "AND date = ? AND slot = ?",
        (tools.household_id(), plan_id, day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


def _entry_rows(plan_id: int, day: str, slot: str = "dinner") -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.*, COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.weekly_plan_id = ? AND mpe.date = ? AND mpe.slot = ?",
        (tools.household_id(), plan_id, day, slot),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- what the one call is told ----------


class TestThePrompt:
    def test_the_prompt_carries_the_households_hard_exclusions(self, week):
        """
        An allergy on a member and a hard What-we-know fact both reach the
        call, as must_not_contain. This is the whole safety half of the
        feature: a model that is never told cannot honour it.
        """
        tools.set_member_dietary_restrictions("Vineeth", ["peanut allergy"])
        tools.add_fact("people", "No shellfish in this house", hard=True)
        picker = _recorder(_pick())
        tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=picker)

        must_not = picker.contexts[0]["must_not_contain"]
        assert any("peanut allergy" in x and "Vineeth" in x for x in must_not)
        assert "No shellfish in this house" in must_not

    def test_the_prompt_carries_the_taste_lines_for_that_slots_eaters(self, week):
        """
        The shared verdicts for the table that actually eats this meal —
        and a night one of them is away gets its own line, because a dish
        only the absent person dislikes is fair game then.
        """
        tools.add_recipe("Thai Green Curry", ingredients=[{"item": "Green curry paste", "qty": "1 jar"}])
        tools.add_recipe("Lemon Orzo", ingredients=[{"item": "Orzo", "qty": "1 box"}])
        tools.attribute_recipe_feedback("Thai Green Curry", "Vineeth", rating="disliked")
        tools.attribute_recipe_feedback("Lemon Orzo", "Emily", rating="liked")
        tools.set_member_attendance(MONDAY, "dinner", "Vineeth", present=False)

        picker = _recorder(_pick())
        tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=picker)

        lines = picker.contexts[0]["taste_verdicts"]
        # One hater at the table vetoes the dish for that table.
        assert any(line.startswith("whole table") and "Thai Green Curry" in line for line in lines)
        # The night Vineeth is out is a different table, and gets its own
        # line — which is the whole point of computing this per slot.
        assert any(MONDAY in line and "Emily" in line for line in lines)

    def test_the_prompt_carries_the_rest_of_the_week(self, week):
        """What stops the replacement being a second helping of Tuesday."""
        picker = _recorder(_pick())
        tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=picker)

        others = picker.contexts[0]["week_other_dishes"]
        assert any("Chili" in line for line in others)
        assert any("Fish Tacos" in line for line in others)
        # Never the slot being replaced — that one is on `avoid`, not here.
        assert not any("Pork Chops" in line for line in others)

    def test_the_dish_being_replaced_is_always_on_avoid(self, week):
        picker = _recorder(_pick())
        tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=picker)
        assert picker.contexts[0]["avoid"] == ["Pork Chops"]

    def test_the_prompt_carries_the_slots_plate_rule_and_table(self, week):
        tools.set_guest_count(MONDAY, "dinner", 2)
        picker = _recorder(_pick())
        tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=picker)
        context = picker.contexts[0]
        assert context["plate_rule"] == ["protein", "vegetable", "carb"]
        assert context["table"]["serves"] == 4  # two members plus two guests
        assert context["table"]["guests"] == 2
        assert context["slot"] == "dinner"

    def test_a_rush_night_caps_the_minutes(self, week):
        tools.save_week_intake(WEEK_START, night_tags={MONDAY: ["rush"]})
        picker = _recorder(_pick())
        tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=picker)
        assert picker.contexts[0]["night_tags"] == ["rush"]
        assert picker.contexts[0]["max_minutes"] == tools.RUSH_MAX_MINUTES

    def test_an_unrushed_night_has_no_cap_even_with_a_weeknight_limit(self, week):
        tools.edit_preference("weeknight_max_minutes", 30)
        tools.save_week_intake(WEEK_START, night_tags={MONDAY: ["unrushed"]})
        picker = _recorder(_pick())
        tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=picker)
        assert picker.contexts[0]["night_tags"] == ["unrushed"]
        assert picker.contexts[0]["max_minutes"] is None

    def test_a_weeknight_limit_still_applies_to_an_untagged_night(self, week):
        """The control for the test above: the cap is lifted BY the tag,
        not by the household merely having set one."""
        tools.edit_preference("weeknight_max_minutes", 30)
        picker = _recorder(_pick())
        tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=picker)
        assert picker.contexts[0]["max_minutes"] == 30

    def test_the_instructions_are_a_separate_cacheable_block(self):
        """
        The household's JSON changes on every call and the instructions
        never do, so they go first with the cache breakpoint on them — the
        same shape (and the same reason) as generate_weekly_plan_llm.
        """
        assert "must_not_contain" in sip.INSTRUCTIONS
        assert "Call submit_swap" in sip.INSTRUCTIONS
        assert sip.SWAP_TOOL["name"] == "submit_swap"


# ---------- the pick, applied ----------


class TestApplyingThePick:
    def test_the_new_dish_replaces_the_old_one_on_that_slot_only(self, week):
        result = tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=_recorder(_pick()))
        assert result["status"] == "swapped"
        assert result["meal"] == "Lemon Chicken Traybake"
        assert result["replaced"] == "Pork Chops"

        rows = _entry_rows(week, MONDAY)
        assert [r["meal"] for r in rows] == ["Lemon Chicken Traybake"]
        # One slot, and nothing else on the week moved.
        assert [r["meal"] for r in _entry_rows(week, TUESDAY)] == ["Chili"]

    def test_the_chain_fields_are_sane(self, week):
        """
        The new entry is a real planned meal on the same day and slot,
        attached to the same plan, carrying the model's one-line reason as
        its reasoning — the field the Meal step's "Why this night" reads.
        """
        result = tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=_recorder(_pick()))
        row = _entry_rows(week, MONDAY)[0]
        assert row["id"] == result["entry_id"]
        assert row["weekly_plan_id"] == week
        assert row["slot"] == "dinner"
        assert row["slot_state"] == "planned"
        assert row["recipe_id"] is not None, "the pick is saved as a real recipe, not freeform"
        assert row["reasoning"] == "Lighter than the chops, and nothing to thaw."

    def test_the_pick_is_saved_as_a_cookable_recipe(self, week):
        tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=_recorder(_pick()))
        recipe = tools.get_recipe("Lemon Chicken Traybake")
        assert [i["item"] for i in recipe["ingredients"]] == [
            "Chicken thighs", "Lemons", "Baby potatoes"]
        assert recipe["instructions"], "a swap has to be cookable afterwards, same as a chat swap"

    def test_a_draft_leaves_the_grocery_list_alone(self, week):
        """
        Approval is what puts a week on the list. A swap inside a draft
        changes the plan and nothing else — the same rule swap_meal_in_plan
        already follows, which is exactly why this goes through it.
        """
        tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=_recorder(_pick()))
        assert tools.list_grocery_list("all") == []

    def test_the_refreshed_day_comes_back_in_the_shape_the_screen_reads(self, week):
        result = tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=_recorder(_pick()))
        day = result["day"]
        assert day["date"] == MONDAY
        assert day["dinner"]["title"] == "Lemon Chicken Traybake"
        assert day["dinner"]["state"] == "planned"

    def test_a_ledger_row_is_written_under_swap_in_place(self, week, monkeypatch):
        """
        Through the real call plumbing, not the picker seam: the label is
        what the api_calls ledger groups by, and "what does a swap cost this
        household" has to be a query rather than a guess.
        """
        response = types.SimpleNamespace(
            content=[types.SimpleNamespace(type="tool_use", name="submit_swap", input=_pick())],
            stop_reason="tool_use",
            usage=types.SimpleNamespace(
                input_tokens=1400, cache_read_input_tokens=0,
                cache_creation_input_tokens=900, output_tokens=520,
            ),
        )
        calls = {"n": 0, "kwargs": None}

        class _Messages:
            def create(self, **kwargs):
                calls["n"] += 1
                calls["kwargs"] = kwargs
                return response

        monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=_Messages()))
        result = tools.swap_meal_in_place(week, _entry_id(week, MONDAY))
        assert result["status"] == "swapped"
        assert calls["n"] == 1, "one meal, one model call"
        # The cheapest effort route the app has, not the week generator's.
        assert calls["kwargs"]["output_config"] == agent._effort_config("utility")

        conn = get_conn()
        rows = [dict(r) for r in conn.execute(
            "SELECT call_site, input_tokens, output_tokens FROM api_calls WHERE household_id = 1"
        ).fetchall()]
        conn.close()
        assert [r["call_site"] for r in rows] == ["swap_in_place"]
        assert rows[0]["output_tokens"] == 520


# ---------- the allergen gate ----------


class TestAnAllergenClash:
    def test_a_clashing_pick_costs_one_retry_and_the_second_one_lands(self, week):
        tools.set_member_dietary_restrictions("Vineeth", ["peanut allergy"])
        picker = _recorder(_pick(name="Peanut Noodles"), _pick(name="Lemon Chicken Traybake"))
        result = tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=picker)

        assert result["status"] == "swapped"
        assert result["meal"] == "Lemon Chicken Traybake"
        assert len(picker.contexts) == 2, "one retry, not a loop"
        # The retry is told not to offer it again.
        assert "Peanut Noodles" in picker.contexts[1]["avoid"]

    def test_two_clashes_are_refused_in_plain_words_and_nothing_changes(self, week):
        tools.set_member_dietary_restrictions("Vineeth", ["peanut allergy"])
        picker = _recorder(_pick(name="Peanut Noodles"), _pick(name="Peanut Satay"))
        result = tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=picker)

        assert result["status"] == "refused"
        assert result["message"] == sip.REFUSAL
        assert "!" not in result["message"], "error copy stays calm and plain"
        assert len(picker.contexts) == 2, "it stops after the retry"
        # Nothing written: the dinner is untouched and the clashing dish was
        # never saved as a recipe.
        assert [r["meal"] for r in _entry_rows(week, MONDAY)] == ["Pork Chops"]
        assert not any(r["name"] == "Peanut Noodles" for r in tools.list_recipes())

    def test_an_allergen_only_in_the_ingredients_is_caught_too(self, week):
        """The dish name is clean; the ingredient list isn't."""
        tools.set_member_dietary_restrictions("Emily", ["shellfish"])
        clashing = _pick(name="Summer Noodle Bowl", ingredients=[
            {"item": "Shrimp", "qty": "1 lb", "category": "meat/seafood"},
        ])
        picker = _recorder(clashing, _pick(name="Lemon Chicken Traybake"))
        result = tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=picker)
        assert result["meal"] == "Lemon Chicken Traybake"

    def test_a_standing_dislike_never_refuses_a_swap(self, week):
        """A dislike is worth a word, never worth blocking what was asked for."""
        tools.add_food_dislikes(["mushrooms"])
        picker = _recorder(_pick(name="Mushroom Risotto"))
        result = tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=picker)
        assert result["status"] == "swapped"
        assert result["meal"] == "Mushroom Risotto"


# ---------- tapping twice ----------


def test_a_second_swap_avoids_the_first_replacement(week):
    first = tools.swap_meal_in_place(week, _entry_id(week, MONDAY),
                                picker=_recorder(_pick(name="Lemon Chicken Traybake")))
    assert first["avoid"] == ["Pork Chops", "Lemon Chicken Traybake"]

    picker = _recorder(_pick(name="Black Bean Bowls"))
    second = tools.swap_meal_in_place(week, first["entry_id"], avoid=first["avoid"], picker=picker)

    assert "Lemon Chicken Traybake" in picker.contexts[0]["avoid"]
    assert "Pork Chops" in picker.contexts[0]["avoid"]
    assert second["meal"] == "Black Bean Bowls"


def test_undo_after_two_swaps_restores_the_original_dish(week):
    """
    swapped_from is written once. "Undo" means "put back what was there
    before I started tapping", not "step back one dish" — a household that
    taps Swap three times and then Undo wants their Pork Chops.
    """
    first = tools.swap_meal_in_place(week, _entry_id(week, MONDAY),
                                picker=_recorder(_pick(name="Lemon Chicken Traybake")))
    second = tools.swap_meal_in_place(week, first["entry_id"], avoid=first["avoid"],
                                 picker=_recorder(_pick(name="Black Bean Bowls")))

    restored = tools.undo_meal_swap(week, second["entry_id"])
    assert restored["status"] == "restored"
    assert restored["meal"] == "Pork Chops"
    assert [r["meal"] for r in _entry_rows(week, MONDAY)] == ["Pork Chops"]
    assert restored["day"]["dinner"]["title"] == "Pork Chops"


def test_undo_forgets_the_swap_so_it_cannot_be_undone_twice(week):
    first = tools.swap_meal_in_place(week, _entry_id(week, MONDAY),
                                picker=_recorder(_pick()))
    restored = tools.undo_meal_swap(week, first["entry_id"])
    with pytest.raises(ValueError, match="nothing to put back"):
        tools.undo_meal_swap(week, restored["entry_id"])


def test_undo_restores_the_reason_the_slot_had_before(week):
    first = tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=_recorder(_pick()))
    tools.undo_meal_swap(week, first["entry_id"])
    assert _entry_rows(week, MONDAY)[0]["reasoning"] == "fits the week"


# ---------- scoping and refusal to guess ----------


def test_another_households_meal_is_not_swappable(week):
    """
    Household-scoped like every other week route. The entry is real and the
    plan is real — they just belong to somebody else, which has to read as
    "no such meal", not as a swap.
    """
    entry_id = _entry_id(week, MONDAY)
    with use_household(2):
        with pytest.raises(ValueError, match="No meal"):
            tools.swap_meal_in_place(week, entry_id, picker=_recorder(_pick()))
    assert [r["meal"] for r in _entry_rows(week, MONDAY)] == ["Pork Chops"]


def test_an_empty_slot_has_nothing_to_swap(week):
    plan_id = week
    tools.plan_slot_open(weekly_plan_id=plan_id, meal_date=DAYS[3], slot="dinner",
                         open_reason="you didn't say", options=[])
    with pytest.raises(ValueError, match="no meal on that slot"):
        tools.swap_meal_in_place(plan_id, _entry_id(plan_id, DAYS[3]), picker=_recorder(_pick()))


def test_a_model_that_comes_back_with_nothing_is_refused_not_guessed(week):
    result = tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=_recorder({}))
    assert result["status"] == "refused"
    assert [r["meal"] for r in _entry_rows(week, MONDAY)] == ["Pork Chops"]


def test_the_route_is_household_scoped_and_answers_with_the_day(signed_in, week, monkeypatch):
    monkeypatch.setattr(tools, "swap_meal_in_place",
                        lambda plan_id, entry_id, avoid=None: sip.swap_meal_in_place(
                            plan_id, entry_id, avoid=avoid, picker=_recorder(_pick())))
    res = signed_in.post(
        f"/api/week/{WEEK_START}/swap-in-place",
        json={"entry_id": _entry_id(week, MONDAY), "avoid": []},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "swapped"
    assert body["day"]["dinner"]["title"] == "Lemon Chicken Traybake"


def test_the_route_404s_on_a_meal_that_is_not_on_that_week(signed_in, week):
    res = signed_in.post(f"/api/week/{WEEK_START}/swap-in-place", json={"entry_id": 99999})
    assert res.status_code == 404


# ---------- source markers: the Day and Meal steps ----------


class TestTheDayAndMealMarkup:
    def test_swap_is_the_in_place_action_now(self):
        assert "runSwapInPlace(panel, day, btn.getAttribute('data-wk-swap'))" in SHELL_JS
        assert "'/api/week/' + encodeURIComponent(weekStart) + '/swap-in-place'" in SHELL_JS

    def test_the_card_says_it_is_working(self):
        assert "Finding something else…" in SHELL_JS

    def test_the_undo_chip_and_its_window(self):
        assert "data-wk-undo=" in SHELL_JS
        assert "var SWAP_UNDO_MS = 8000;" in SHELL_JS
        assert "'/swap-undo'" in SHELL_JS

    def test_the_wordier_path_is_kept_as_a_quiet_link(self):
        assert "Tell me what instead" in SHELL_JS
        # Prefilled exactly as it was, and openAskSheet itself untouched.
        assert "openAskSheet('Swap ' + dayName(day.date, { weekday: 'long' }) + '’s ' +\n" \
               "          btn.getAttribute('data-wk-tell') + ' for something else');" in SHELL_JS

    def test_the_swap_line_carries_no_second_apricot_fill(self):
        """
        Rule 5: the screen's one apricot is the primary above this line. The
        swap line's two controls are a text link and an outlined chip.
        """
        block = SHELL_CSS.split(".wk-swap-line")[1].split("/* ---------- MEAL")[0]
        assert "background: var(--apricot)" not in block
        assert "min-height: 44px;" in block  # Rule 6

    def test_the_copy_has_no_exclamation_marks(self):
        for line in ("Finding something else…", "Tell me what instead",
                     "That didn’t work just now — nothing changed."):
            assert line in SHELL_JS
            assert "!" not in line



# ---------- verifier findings, 2026-09-08 ----------

class TestVerifierFindings:
    def test_a_component_plan_entry_is_refused_and_nothing_is_deleted(self):
        """A component-based plan keys rows by category on one placeholder
        date+slot; the day-based delete would wipe every component. Refuse."""
        tools.add_member("Emily")
        for name in ("Chili", "Roasted Broccoli", "Rice"):
            tools.add_recipe(name, ingredients=[{"item": name, "qty": "1", "category": "produce"}])
        tools.set_planning_mode("component_based")
        plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
        for name, cat in (("Chili", "protein"), ("Roasted Broccoli", "vegetable"), ("Rice", "carb")):
            tools.plan_meal(WEEK_START, name, weekly_plan_id=plan_id, component_category=cat)
        conn = get_conn()
        entry_id = conn.execute("SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND component_category = 'protein'", (plan_id,)).fetchone()["id"]
        before = conn.execute("SELECT COUNT(*) FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan_id,)).fetchone()[0]
        conn.close()
        with pytest.raises(ValueError):
            tools.swap_meal_in_place(plan_id, entry_id, picker=_recorder(_pick()))
        conn = get_conn()
        after = conn.execute("SELECT COUNT(*) FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan_id,)).fetchone()[0]
        conn.close()
        assert before == after == 3

    def test_a_dish_an_eater_dislikes_is_retried_like_a_clash(self, week):
        """The taste rule is a gate, not a suggestion: Vineeth dislikes the
        first pick and is home, so the second pick lands instead."""
        tools.add_recipe("Mushroom Risotto", ingredients=[{"item": "rice", "qty": "1 cup"}])
        tools.attribute_recipe_feedback("Mushroom Risotto", "Vineeth", rating="disliked")
        picker = _recorder(_pick("Mushroom Risotto"), _pick("Lemon Chicken Traybake"))
        result = tools.swap_meal_in_place(week, _entry_id(week, MONDAY), picker=picker)
        assert result["status"] == "swapped"
        assert result["meal"] == "Lemon Chicken Traybake"
        assert "Mushroom Risotto" in result["avoid"]
