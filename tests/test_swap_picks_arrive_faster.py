"""
Swap picks arrive faster (Loop Board, Emily, 2026-09-21: "make it faster
if possible"; target under five seconds).

Railway's log had the Swap sheet's `swap_options` call at 10.5–13 s for
1300–1600 output tokens at the utility effort: the call asked for three
COMPLETE recipes — every ingredient with its store quantity and category,
every step — and the sheet drew a name, a line and the minutes from each.

What changed (app/tools/swap_options.py):
  * the options schema is what the sheet shows plus what the gate needs:
    meal_name, reason, the main ingredients as plain names (the allergen
    gate matches items, so it needs them; no quantities), minutes. No
    instructions, no per-ingredient objects. Three picks are a few hundred
    tokens rather than fifteen hundred.
  * the call runs on the new `picks` effort route (low, PICKS_EFFORT to
    override) with max_tokens capped to what three trimmed picks need.
  * the recipe is written out for ONE dish — the one the household taps —
    at choose time, through the swap's own tool and instructions, and
    gated again on its full list before it is planned. A pick that already
    carries steps, or names a dish the household has saved, is applied as
    it was (so every earlier test of choose_swap_option still holds).

The real timing can't be measured here (no key); what this file pins is
the shape, the route and the budget the timing follows from, plus the
choose-time write-out. Token counts are estimated in the report.
"""
from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest

from app import agent, tools
from app.tools import swap_in_place as _swap

sop = importlib.import_module("app.tools.swap_options")
from test_week1_carousel import DAY1, _asker, _entry_id, week  # noqa: F401


# ---------- the shape of a pick ----------


def _option(name, protein="Chicken thighs", reason="Lighter than the chops, and nothing to thaw.", minutes=30):
    """A pick the way the trimmed schema has the model give it."""
    return {"meal_name": name, "reason": reason, "minutes": minutes,
            "ingredients": [protein, "Lemons", "Baby potatoes", "Green beans"]}


def test_the_schema_asks_for_picks_not_recipes():
    item = sop.OPTIONS_TOOL["input_schema"]["properties"]["options"]["items"]
    assert set(item["required"]) == {"meal_name", "reason", "ingredients", "minutes"}
    assert "instructions" not in item["properties"], "no steps — the recipe is written for the one they take"
    assert item["properties"]["ingredients"]["items"] == {"type": "string"}, (
        "plain names, not {item, qty, category} objects — no per-ingredient quantities"
    )
    assert "qty" not in json.dumps(item["properties"]["ingredients"])
    assert item["properties"]["minutes"]["type"] == "integer"
    assert "Not recipes" in sop.OPTIONS_TOOL["description"]
    # The trimmed schema is its own, not the swap's full one (which is what
    # the write-out uses at choose time).
    assert item is not _swap.SWAP_TOOL["input_schema"]
    assert "Do NOT write recipes" in sop.INSTRUCTIONS
    assert "quantit" not in sop.INSTRUCTIONS.split("- `ingredients`")[0].split("Rules")[1], (
        "the rules no longer ask for quantities anywhere before the ingredients line"
    )


def test_the_budget_is_three_trimmed_picks_not_three_recipes():
    assert sop.OPTIONS_MAX_TOKENS <= 1500
    assert sop.WRITE_OUT_MAX_TOKENS == 2000, "one dish, the same budget as Swap · I'll pick"
    # A realistic answer, as the tool JSON the model produces: three picks
    # come to well under a third of the old call's 1300–1600 output.
    three = json.dumps({"options": [_option("Lemon chicken traybake"),
                                    _option("Fish tacos with slaw", protein="Cod fillets"),
                                    _option("Chickpea and spinach curry", protein="Chickpeas")]})
    assert len(three) < 700, len(three)  # ~4 chars a token → under 200 tokens


# ---------- the call itself ----------


def _fake_response(payload: dict):
    return SimpleNamespace(stop_reason="tool_use",
                           content=[SimpleNamespace(type="tool_use", input=payload)],
                           usage=SimpleNamespace(output_tokens=1, input_tokens=1, cache_read_input_tokens=0))


def _capture(monkeypatch, payload: dict) -> dict:
    seen = {}

    def create(client, **kwargs):
        seen.update(kwargs)
        return _fake_response(payload)

    monkeypatch.setattr(agent, "_client", lambda: object())
    monkeypatch.setattr(agent, "_create_with_retry", create)
    return seen


def test_the_options_call_runs_on_the_picks_route_with_a_small_cap(monkeypatch):
    seen = _capture(monkeypatch, {"options": [_option("Lemon chicken traybake")]})
    out = sop._ask_options({"slot": "dinner"})
    assert out == [_option("Lemon chicken traybake")]
    assert seen["label"] == "swap_options"
    assert seen["model"] == agent.MODEL
    assert seen["max_tokens"] == sop.OPTIONS_MAX_TOKENS
    assert seen["output_config"] == {"effort": "low"}
    assert seen["tools"] == [sop.OPTIONS_TOOL]
    assert seen["tool_choice"] == {"type": "tool", "name": "submit_swap_options"}
    first = seen["messages"][0]["content"][0]
    assert first["text"] == sop.INSTRUCTIONS and first["cache_control"] == {"type": "ephemeral"}


def test_the_picks_route_is_low_and_env_tunable(monkeypatch):
    monkeypatch.delenv("PICKS_EFFORT", raising=False)
    assert agent._effort_config("picks") == {"effort": "low"}
    monkeypatch.setenv("PICKS_EFFORT", "medium")
    assert agent._effort_config("picks") == {"effort": "medium"}
    # The other routes are untouched.
    assert agent._effort_config("utility") == {"effort": "medium"}


def test_the_write_out_is_the_swaps_own_call_for_one_named_dish(monkeypatch):
    full = {"meal_name": "Lemon chicken traybake", "reason": "written", "ingredients": [{"item": "Chicken thighs", "qty": "1 lb"}]}
    seen = _capture(monkeypatch, full)
    out = sop._write_out({"slot": "dinner"}, _option("Lemon chicken traybake"))
    assert out == full
    assert seen["label"] == "swap_option_writeout"
    assert seen["model"] == agent.MODEL and seen["max_tokens"] == sop.WRITE_OUT_MAX_TOKENS
    assert seen["output_config"] == {"effort": "medium"}, "the same route as Swap · I'll pick"
    assert seen["tools"] == [_swap.SWAP_TOOL]
    blocks = seen["messages"][0]["content"]
    assert blocks[0]["text"] == _swap.INSTRUCTIONS and blocks[0]["cache_control"] == {"type": "ephemeral"}, (
        "the swap's own cached prefix, so the two calls share it"
    )
    assert blocks[-1]["text"] == f"{sop.WRITE_OUT_ASK} Lemon chicken traybake"


# ---------- the sheet, on trimmed picks ----------


def test_trimmed_picks_reach_the_sheet_with_their_minutes(week):
    ask = _asker(_option("Lemon chicken traybake", minutes=35), _option("Fish tacos", protein="Cod fillets", minutes=20))
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=ask)
    assert [(o["meal"], o["minutes"], o["reason"]) for o in out["options"]] == [
        ("Lemon chicken traybake", 35, "Lighter than the chops, and nothing to thaw."),
        ("Fish tacos", 20, "Lighter than the chops, and nothing to thaw."),
    ]
    assert "ingredients" not in out["options"][0], "the sheet never sees the list"


def test_the_gate_reads_the_short_list(week):
    """The main ingredients are why the list is kept at all: an allergen
    among them drops the pick before it is offered."""
    tools.set_member_dietary_restrictions("Emily", ["shellfish allergy"])
    ask = _asker(_option("Garlic prawns with rice", protein="Prawns"), _option("Fish tacos", protein="Cod fillets"))
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=ask)
    assert [o["meal"] for o in out["options"]] == ["Fish tacos"]


# ---------- choosing: the recipe is written for the one they take ----------


def _writer(full: dict | None = None, fail: bool = False):
    calls = []

    def write(context, pick):
        calls.append((context, pick))
        if fail:
            raise RuntimeError("no model tonight")
        return dict(full or {
            "meal_name": pick["meal_name"] + " with greens",  # the model's own title is not the one tapped
            "reason": "the model's line",
            "is_new_recipe": True,
            "ingredients": [{"item": "Chicken thighs", "qty": "1 lb", "category": "meat/seafood"},
                            {"item": "Lemons", "qty": "2", "category": "produce"},
                            {"item": "Baby potatoes", "qty": "1 lb", "category": "produce"}],
            "instructions": ["Roast everything.", "Squeeze the lemon over."],
            "food_groups": ["protein", "carb", "vegetable"], "main_protein": "chicken",
            "prep_time_minutes": 10, "cook_time_minutes": 25, "default_servings": 2,
        })

    write.calls = calls
    return write


def test_choosing_a_trimmed_pick_writes_it_out_then_plans_it(week):
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=_asker(_option("Lemon chicken traybake"), _option("Fish tacos", protein="Cod fillets")))
    write = _writer()
    out = sop.choose_swap_option(week, entry_id, 0, writer=write)
    assert out["status"] == "swapped"
    # Written out once, for the tapped dish, against the slot the picks
    # were asked for.
    assert len(write.calls) == 1
    context, pick = write.calls[0]
    assert pick["meal_name"] == "Lemon chicken traybake" and context["replacing"] == "Pork Chops"
    # The name and the line are the ones the household read; the recipe
    # underneath is the model's — quantities, steps, the lot.
    assert out["meal"] == "Lemon chicken traybake"
    assert out["reason"] == "Lighter than the chops, and nothing to thaw."
    saved = tools.get_recipe("Lemon chicken traybake")
    assert saved["instructions"] == ["Roast everything.", "Squeeze the lemon over."]
    assert {i["item"]: i["qty"] for i in saved["ingredients"]} == {"Chicken thighs": "1 lb", "Lemons": "2", "Baby potatoes": "1 lb"}
    assert out["day"]["dinner"]["title"] == "Lemon chicken traybake"


def test_a_pick_that_already_carries_its_steps_is_not_written_out_again(week):
    from test_week1_carousel import _pick
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=_asker(_pick("Lemon Chicken Traybake")))
    write = _writer()
    assert sop.choose_swap_option(week, entry_id, 0, writer=write)["status"] == "swapped"
    assert write.calls == []


def test_a_pick_naming_a_saved_dish_is_not_written_out(week):
    """`Chili` is already the household's; there is nothing to write."""
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=_asker(_option("Chili", protein="Ground beef")))
    write = _writer()
    out = sop.choose_swap_option(week, entry_id, 0, writer=write)
    assert out["status"] == "swapped" and out["meal"] == "Chili"
    assert write.calls == []


def test_a_failed_write_out_changes_nothing_and_keeps_the_picks(week):
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=_asker(_option("Lemon chicken traybake")))
    out = sop.choose_swap_option(week, entry_id, 0, writer=_writer(fail=True))
    assert out == {"status": "refused", "message": sop.WRITE_OUT_TROUBLE}
    assert "!" not in out["message"]
    assert tools.existing_recipe_named("Lemon chicken traybake") is None
    # The slot is as it was, the picks are still on offer, and the next
    # tap goes through.
    assert sop.choose_swap_option(week, entry_id, 0, writer=_writer())["status"] == "swapped"


def test_a_write_out_missing_its_ingredients_is_refused_not_planned_thin(week):
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=_asker(_option("Lemon chicken traybake")))
    out = sop.choose_swap_option(week, entry_id, 0, writer=_writer(full={"meal_name": "Lemon chicken traybake"}))
    assert out["status"] == "refused" and out["message"] == sop.WRITE_OUT_TROUBLE
    assert tools.existing_recipe_named("Lemon chicken traybake") is None


def test_the_full_list_is_gated_again_before_it_is_planned(week):
    """Six plain names passed the gate; the written-out recipe can name
    what they did not. It is checked again, and refused in the swap's own
    words."""
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=_asker(_option("Lemon chicken traybake")))
    tools.set_member_dietary_restrictions("Emily", ["dairy allergy"])
    full = _writer()(None, _option("Lemon chicken traybake"))
    full["ingredients"].append({"item": "Butter", "qty": "2 tbsp", "category": "dairy"})
    out = sop.choose_swap_option(week, entry_id, 0, writer=_writer(full=full))
    assert out["status"] == "refused"
    assert out["message"].startswith("I left it as it was — Lemon chicken traybake clashes with")
    assert tools.existing_recipe_named("Lemon chicken traybake") is None


def test_the_route_still_reaches_choose_without_a_writer(signed_in, week, monkeypatch):
    """POST /swap-choose passes no writer, so a trimmed pick goes through
    the real write-out — stubbed here at the model call."""
    from test_week1_carousel import WEEK_START
    monkeypatch.setattr(tools, "swap_options",
                        lambda plan_id, entry_id, avoid=None: sop.swap_options(
                            plan_id, entry_id, avoid=avoid, asker=_asker(_option("Lemon chicken traybake"))))
    seen = _capture(monkeypatch, _writer()(None, _option("Lemon chicken traybake")))
    entry_id = _entry_id(week, DAY1)
    assert signed_in.post(f"/api/week/{WEEK_START}/swap-options", json={"entry_id": entry_id}).status_code == 200
    res = signed_in.post(f"/api/week/{WEEK_START}/swap-choose", json={"entry_id": entry_id, "option": 0})
    assert res.status_code == 200 and res.json()["status"] == "swapped"
    assert seen["label"] == "swap_option_writeout"


# ---------- verifier follow-ups (2026-09-21) ----------


def _save_peanut_bake():
    tools.add_recipe("Grandma's Skillet Bake",
                     ingredients=[{"item": "Chicken thighs", "qty": "1 lb", "category": "meat/seafood"},
                                  {"item": "Peanuts", "qty": "1 cup", "category": "pantry"},
                                  {"item": "Rice", "qty": "2 cups", "category": "pantry"}],
                     food_groups=["protein", "carb"], prep_time_minutes=10, cook_time_minutes=30)


def test_a_saved_dish_is_gated_on_its_own_recipe_not_the_short_list(week):
    """The allergen hole the verifier reproduced: a saved bake with
    peanuts, a peanut allergy, and a trimmed pick for that name whose
    four short names leave the peanuts out. The gate reads the recipe
    that would actually be planned — so the pick is never offered."""
    _save_peanut_bake()
    tools.set_member_dietary_restrictions("Emily", ["peanut allergy"])
    short = _option("Grandma's Skillet Bake", protein="Chicken thighs")
    assert "Peanuts" not in short["ingredients"]
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=_asker(short, _option("Fish tacos", protein="Cod fillets")))
    assert [o["meal"] for o in out["options"]] == ["Fish tacos"]


def test_a_saved_dish_is_gated_again_on_its_own_recipe_at_the_tap(week):
    """Offered while the house was fine with peanuts; the allergy is added
    before the tap. The tap re-reads the saved recipe, not the short list."""
    _save_peanut_bake()
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=_asker(_option("Grandma's Skillet Bake", protein="Chicken thighs")))
    tools.set_member_dietary_restrictions("Emily", ["peanut allergy"])
    out = sop.choose_swap_option(week, entry_id, 0, writer=_writer())
    assert out["status"] == "refused"
    assert out["message"].startswith("I left it as it was — Grandma's Skillet Bake clashes with")


def test_a_saved_dish_with_a_clean_recipe_still_goes_through(week):
    """The rule cuts one way only: a saved dish whose real list is fine is
    offered and planned, without a write-out (it needs none)."""
    _save_peanut_bake()
    tools.set_member_dietary_restrictions("Emily", ["shellfish allergy"])
    entry_id = _entry_id(week, DAY1)
    # The short list even names a prawn the recipe doesn't have — the
    # recipe is the source of truth for a dish that exists.
    out = tools.swap_options(week, entry_id, asker=_asker(_option("Grandma's Skillet Bake", protein="Prawns")))
    assert [o["meal"] for o in out["options"]] == ["Grandma's Skillet Bake"]
    write = _writer()
    assert sop.choose_swap_option(week, entry_id, 0, writer=write)["status"] == "swapped"
    assert write.calls == []


@pytest.mark.parametrize("missing", ["instructions", "qty"])
def test_a_write_out_without_steps_or_quantities_is_refused_not_planned_thin(week, missing):
    """A write-out that answers with ingredients but no steps (or with
    lines missing their quantities) would land as a recipe the Cooker
    can't cook from. Refused — the same line, nothing written."""
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=_asker(_option("Lemon chicken traybake")))
    full = _writer()(None, _option("Lemon chicken traybake"))
    if missing == "instructions":
        full["instructions"] = ["", "  "]
    else:
        full["ingredients"][1]["qty"] = ""
    out = sop.choose_swap_option(week, entry_id, 0, writer=_writer(full=full))
    assert out == {"status": "refused", "message": sop.WRITE_OUT_TROUBLE}
    assert tools.existing_recipe_named("Lemon chicken traybake") is None
    # The picks are still on offer; a whole write-out then goes through.
    assert sop.choose_swap_option(week, entry_id, 0, writer=_writer())["status"] == "swapped"


def test_write_out_is_complete_is_the_one_rule():
    good = _writer()(None, _option("x"))
    assert sop.write_out_is_complete(good)
    assert not sop.write_out_is_complete(None)
    assert not sop.write_out_is_complete({"meal_name": "x"})
    assert not sop.write_out_is_complete(dict(good, instructions=[]))
    assert not sop.write_out_is_complete(dict(good, ingredients=[{"item": "Rice"}]))
