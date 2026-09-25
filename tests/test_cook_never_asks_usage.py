"""
Cook never asks how much of something was used.

Emily, 2026-09-25, on Cook's "How did it go? · 2 quick things" card asking
"How much Garlic did you use for Lebanese-Style Garlic Shrimp with Herbed
Rice? (tracking "Garlic" — 2 heads)" with a box, Log it and Skip: "Assume I
made what the recipe called for here, don't ask me. It's a lot on the user."

cooker.deplete_inventory_for_meal used to queue that question whenever a
tracked item matched an ingredient the recipe gave no amount for (and a
"was this the tracked X?" question for a loose match). Now:

  - a recipe amount still comes off silently, as it always did;
  - no amount, or a loose match, leaves the row exactly as it is — what
    "Skip" on the old question did — and nothing is queued;
  - questions queued before the change never come back out of
    get_attention_items (Cook's card, chat, the morning text);
  - the rating half of "How did it go?" stays.
"""
from __future__ import annotations

from pathlib import Path

from app import tools

DAY = "2026-01-05"
SHELL_JS = Path(__file__).resolve().parent.parent / "static" / "shell.js"


def _inv(name):
    rows = [r for r in tools.get_inventory() if r["item"].lower() == name.lower()]
    return rows[0] if rows else None


def _cook(recipe, ingredients):
    tools.add_recipe(recipe, ingredients=ingredients, default_servings=2)
    entry = tools.plan_meal(DAY, recipe, slot="dinner")["entry_id"]
    return tools.check_off_meal(entry, "done")


def _usage_items():
    return [i for i in tools.get_attention_items() if i["kind"] == "inventory_depletion"]


def test_no_amount_in_the_recipe_asks_nothing_and_leaves_the_garlic_alone():
    tools.update_inventory("Garlic", "add", quantity="2 heads", category="produce")
    result = _cook("Lebanese-Style Garlic Shrimp", [{"item": "Garlic", "qty": ""}])

    assert _usage_items() == []
    assert result["inventory_queued_for_review"] == []
    assert _inv("Garlic")["quantity"] == "2 heads"


def test_a_recipe_amount_still_comes_off_without_asking():
    tools.update_inventory("Rice", "add", quantity="4 cups", category="pantry")
    _cook("Herbed Rice", [{"item": "Rice", "qty": "1 cup"}])

    assert _usage_items() == []
    assert _inv("Rice")["quantity"] == "3 cups"


def test_untick_and_retick_never_asks_either():
    tools.update_inventory("Garlic", "add", quantity="2 heads", category="produce")
    tools.add_recipe("Garlic Bread", ingredients=[{"item": "Garlic", "qty": ""}], default_servings=2)
    entry = tools.plan_meal(DAY, "Garlic Bread", slot="dinner")["entry_id"]
    for _ in range(3):
        tools.check_off_meal(entry, "done")
        tools.check_off_meal(entry, "pending")
    tools.check_off_meal(entry, "done")

    assert _usage_items() == []
    assert _inv("Garlic")["quantity"] == "2 heads"


def test_a_question_queued_before_the_change_never_shows():
    """A household that cooked yesterday still has the old row pending."""
    old = tools.add_attention_item(
        "inventory_depletion",
        'How much Garlic did you use for Garlic Shrimp? (tracking "Garlic" — 2 heads)',
        {"entry_id": 1, "ingredient": "Garlic", "candidate_item_id": 1, "needs_amount_used": True},
    )["id"]
    loose = tools.add_attention_item(
        "inventory_depletion",
        'Used garlic for Garlic Shrimp — closest thing tracked is "garlic bulb". Deplete that?',
        {"entry_id": 1, "ingredient": "garlic", "candidate_item_id": 2},
    )["id"]
    other = tools.add_attention_item("use_soon", "The salmon won't keep past Friday.")["id"]

    ids = [i["id"] for i in tools.get_attention_items()]
    assert old not in ids and loose not in ids
    assert other in ids


def test_the_attention_route_hides_them_too(signed_in):
    tools.add_attention_item(
        "inventory_depletion", "How much Garlic did you use?",
        {"entry_id": 1, "ingredient": "Garlic", "needs_amount_used": True},
    )
    items = signed_in.get("/api/attention").json()["items"]
    assert [i for i in items if i["kind"] == "inventory_depletion"] == []


def test_the_rating_half_of_how_did_it_go_stays():
    _cook("Herbed Rice Two", [{"item": "Rice", "qty": "1 cup"}])
    kinds = [i["kind"] for i in tools.get_attention_items()]
    assert "feedback_nudge" in kinds


def test_the_cook_card_has_no_amount_box():
    src = SHELL_JS.read_text()
    assert "needs_amount_used" not in src
    assert 'placeholder="e.g. 1 cup, or leave blank for all of it"' not in src
    assert "it.kind !== 'inventory_depletion'" in src
