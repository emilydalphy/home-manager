"""
The one plain line the chat shows while a turn is between tool rounds
(Loop Board: "Speed: chat shows what it's doing and answers sooner").

A chat turn can make several model calls back to back, running a tool
between each, and nothing reaches the browser until the whole turn ends.
This turns the tool the model just asked for, and its main input, into a
short line for the sheet to show instead of a frozen "Thinking…". It is a
lookup table, never a model call: the turn costs exactly what it did.

Voice (DESIGN_SYSTEM §8, pomona-copywriter): the action, the named thing,
nothing announced. A tool with no entry gets "One moment…" so a line from
an earlier tool never sits on screen describing work that has finished.
"""
from __future__ import annotations

import datetime

FALLBACK_LINE = "One moment…"


def _text(value, limit: int = 40) -> str:
    """A name the household typed or the model wrote, trimmed to one line's worth."""
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _day(value) -> str:
    """'2026-10-04' -> 'Sunday'; anything else (a weekday already, blank) as written."""
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        return datetime.date.fromisoformat(value.strip()[:10]).strftime("%A")
    except ValueError:
        return _text(value, 20)


def _with(template: str, fallback: str, *values: str):
    """template filled with the values when every one is present, else fallback."""
    if all(values):
        return template.format(*values)
    return fallback


# tool name -> builder(input dict) -> line. Keep each line the action and
# the thing; the trailing ellipsis is the same one "Thinking…" wears.
_LINES = {
    "add_recipe": lambda i: _with("Writing {}…", "Writing the recipe…", _text(i.get("name"))),
    "update_recipe_details": lambda i: _with("Updating {}…", "Updating the recipe…", _text(i.get("recipe_name"))),
    "scale_recipe": lambda i: _with("Scaling {}…", "Scaling the recipe…", _text(i.get("recipe_name"))),
    "get_recipe": lambda i: _with("Opening {}…", "Opening the recipe…", _text(i.get("recipe_name"))),
    "list_recipes": lambda i: "Looking through your recipes…",
    "mark_recipe_feedback": lambda i: _with("Saving your note on {}…", "Saving your note…", _text(i.get("recipe_name"))),
    "log_recipe_note": lambda i: _with("Saving your note on {}…", "Saving your note…", _text(i.get("recipe_name"))),
    "plan_meal": lambda i: (
        _with("Adding {} to {}…", "Adding it to the plan…", _text(i.get("meal")), _day(i.get("meal_date")))
    ),
    "swap_meal_in_plan": lambda i: (
        _with("Swapping in {} on {}…", "Swapping the meal…", _text(i.get("new_meal")), _day(i.get("meal_date")))
    ),
    "swap_component_in_plan": lambda i: _with("Swapping in {}…", "Swapping the meal…", _text(i.get("new_meal"))),
    "swap_dinner_nights": lambda i: (
        _with("Swapping {} and {}…", "Swapping the two nights…", _day(i.get("date_a")), _day(i.get("date_b")))
    ),
    "take_the_night_off": lambda i: _with("Taking {} off…", "Taking the night off…", _day(i.get("day"))),
    "unbatch": lambda i: "Splitting up the batch…",
    "propose_plan_changes": lambda i: "Lining up the changes…",
    "generate_weekly_plan": lambda i: "Building the week…",
    "approve_weekly_plan": lambda i: "Approving the plan…",
    "discard_draft_plan": lambda i: "Removing the draft…",
    "get_weekly_plan": lambda i: "Checking the plan…",
    "get_meal_plan": lambda i: "Checking the plan…",
    "get_week_ingredients": lambda i: "Checking the week's ingredients…",
    "generate_prep_schedule": lambda i: "Working out the prep…",
    "add_grocery_item": lambda i: _with("Adding {} to the shopping list…", "Adding to the shopping list…", _text(i.get("item"))),
    "add_grocery_items": lambda i: "Adding to the shopping list…",
    "add_staple": lambda i: _with("Adding {} to your staples…", "Adding to your staples…", _text(i.get("item"))),
    "update_inventory": lambda i: "Updating what's on hand…",
    "update_inventory_items": lambda i: "Updating what's on hand…",
    "set_away_stretch": lambda i: "Marking the days away…",
    "add_fact": lambda i: "Saving that…",
}

# Read-only lookups share a line by family rather than one entry each.
_PREFIX_LINES = (
    ("get_grocery_", "Checking the shopping list…"),
    ("list_grocery_", "Checking the shopping list…"),
    ("get_inventory", "Checking what's on hand…"),
    ("get_prep_", "Checking the prep…"),
)


def progress_line(tool_name: str, tool_input) -> str:
    """The line for a tool call, or FALLBACK_LINE when there is nothing better to say."""
    data = tool_input if isinstance(tool_input, dict) else {}
    builder = _LINES.get(tool_name)
    if builder is not None:
        try:
            return builder(data) or FALLBACK_LINE
        except Exception:
            return FALLBACK_LINE
    for prefix, line in _PREFIX_LINES:
        if tool_name.startswith(prefix):
            return line
    return FALLBACK_LINE
