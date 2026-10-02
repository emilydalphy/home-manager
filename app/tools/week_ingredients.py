"""
What's actually IN the week's food, for the chat to check before it answers.

QA walk as a new household, 2026-10-02: "Juno doesn't like spicy food. Is
anything this week too spicy for her?" The reply looked at Friday's fajita
bowls and then said "Nothing else on the week leans spicy either" — but
the Beef and Vegetable Ragu on the same week calls for crushed red pepper
flakes, and it said so the moment it was asked about the ragu by name.

Root cause: a chat sent from the Plan tab carries the week as DISH NAMES
only (agent._build_week_context_block, via proposals.describe_plan_for_chat)
and tells the model not to call get_weekly_plan "just to find it". Nothing
in that turn had the ingredient lists, and nothing told it to look before
claiming an absence — so it judged the week by its titles. "Ragu" doesn't
sound spicy.

This is the lookup that closes it: every planned dish on the week, each
with its ingredient names (and its attached side's), one entry per dish
however many nights it's on, plus the slots it could NOT check because
there's no saved ingredient list behind them. Names only — no quantities,
categories, steps or reasoning — so it costs a few hundred tokens on the
turn that asks rather than the ~15K a whole get_weekly_plan result does,
and nothing at all on the turns that don't. Deliberately a tool and not a
briefing line: the Sept 21–23 cost work slimmed the briefing on purpose,
and every-ingredient-every-turn would undo it for a question asked rarely.
"""
from __future__ import annotations

import datetime

from . import weekly_plan as _weekly_plan


def _weekday(iso: str) -> str:
    try:
        return datetime.date.fromisoformat(iso).strftime("%a")
    except (TypeError, ValueError):
        return ""


def _item_names(ingredients) -> list[str]:
    names = []
    for ing in ingredients or []:
        if isinstance(ing, dict):
            item = (ing.get("item") or "").strip()
        elif isinstance(ing, str):
            item = ing.strip()
        else:
            item = ""
        if item and item not in names:
            names.append(item)
    return names


def get_week_ingredients(weekly_plan_id: int | None = None) -> dict:
    """
    Every planned dish on a week with its ingredient names, for answering
    "is anything spicy / has nuts / has dairy this week?" from the real
    recipes. Omit weekly_plan_id for the household's current plan.

    Returns weekly_plan_id, `dishes` (meal, when — e.g. "Mon 2026-10-05
    dinner" — ingredients, and sides each with their own ingredients) and
    `no_ingredient_list` (the slots whose dish has nothing saved to check,
    e.g. a freeform "Leftovers" or "Takeout").
    """
    plan = _weekly_plan.get_weekly_plan(weekly_plan_id)
    if not plan.get("weekly_plan_id"):
        return {"weekly_plan_id": None, "dishes": [], "no_ingredient_list": []}

    by_meal: dict[str, dict] = {}
    unchecked: list[str] = []
    for m in plan.get("meals") or []:
        name = (m.get("meal") or "").strip()
        # A nobody-home or open slot has no dish at all: nothing to check,
        # and nothing to report as unchecked either.
        if not name or m.get("slot_state") in ("planned_empty", "open"):
            continue
        when = " ".join(p for p in (_weekday(m.get("date") or ""), m.get("date") or "", m.get("slot") or "") if p)
        items = _item_names(m.get("ingredients"))
        sides = [
            {"name": (s.get("name") or "").strip(), "ingredients": _item_names(s.get("ingredients"))}
            for s in (m.get("sides") or []) if isinstance(s, dict) and (s.get("name") or "").strip()
        ]
        if not items:
            unchecked.append(f"{when}: {name}")
        # Keyed on the dish AND whether it has a list: a batch cooked on
        # Monday and eaten again Tuesday is one dish to check, not two.
        dish = by_meal.get(name)
        if dish is None:
            if not items and not sides:
                continue
            dish = by_meal[name] = {"meal": name, "when": [], "ingredients": items, "sides": []}
        dish["when"].append(when)
        if items and not dish["ingredients"]:
            dish["ingredients"] = items
        for side in sides:
            if side["name"] not in {s["name"] for s in dish["sides"]}:
                dish["sides"].append(side)

    return {
        "weekly_plan_id": plan["weekly_plan_id"],
        "status": plan.get("status"),
        "dishes": list(by_meal.values()),
        "no_ingredient_list": unchecked,
    }
