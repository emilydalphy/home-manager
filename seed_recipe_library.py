"""
Seed the Shared Recipe Library from the 500-dish starter list
(data/recipe_library_seed.json) — Loop Board, Emily 2026-10-18.

THIS SPENDS ANTHROPIC API MONEY: every dish not already in the library is
researched on the web (search + page reads) and then written up by the
recipe writer — the same two calls a household's recipe pass makes. Run it
only after Emily has approved the six samples
(mockups/recipe-library-samples.html), and with --i-mean-it; without that
flag it prints this and stops before touching anything.

    DB_PATH=/data/home_manager.db python seed_recipe_library.py --i-mean-it

What it does, a batch at a time (--batch-size dishes written at once):
  * skips a dish the library already has a written base for — so a run
    that stops (Ctrl-C, a deploy, an API error) picks up where it left off
    when run again, and a dish a household already paid for is never paid
    for twice;
  * reuses the library's research for a dish that has research but no
    recipe yet (only the writer is billed);
  * researches the rest exactly as the app does (open search, the trusted
    cooks for the cuisine when nothing read is well rated, the lead chosen
    by recipe_research.pick_lead), writes the recipe for a table of 4 with
    no household in it, and files it through the library's ONE write path,
    recipe_library.file_entry(origin="seed").
A dish whose research or writing fails is logged and left out; the next run
tries it again. --samples files the six reviewed samples
(data/recipe_library_samples.json) as they stand, with no API calls.

No household's recipes or research are touched: research is not saved into
anyone's dish_research, and no recipes row is created. Two things DO run as
household 1, because a script has no request and household_id() defaults to
1: the AI-consent check every model call makes (household 1 must have said
yes), and the api_calls usage rows, so this run's spend shows up under
household 1 in observability_report.py. The run prints that, so the morning
report's numbers for that day can be read for what they are.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEED_FILE = ROOT / "data" / "recipe_library_seed.json"
SAMPLES_FILE = ROOT / "data" / "recipe_library_samples.json"

# The library's base is written for a generic table: the household that
# copies it scales it to its own (recipes.default_servings) like any recipe.
SERVES = 4

REFUSAL = """\
seed_recipe_library.py spends Anthropic API money: it researches each of up to
500 dishes on the web and has the recipe writer write each one up.

It is for AFTER Emily approves the six samples in
mockups/recipe-library-samples.html. Nothing has been read or written.

To run it, add --i-mean-it (and check DB_PATH points at the database you mean).
"""

logger = logging.getLogger("home_manager.seed_library")


def load_dishes(path: Path = SEED_FILE) -> list[dict]:
    return json.loads(Path(path).read_text())["dishes"]


def load_samples(path: Path = SAMPLES_FILE) -> list[dict]:
    return json.loads(Path(path).read_text())["samples"]


def pending(dishes: list[dict]) -> list[dict]:
    """The dishes the library has no written base for yet, in list order."""
    from app.tools import recipe_library
    return [d for d in dishes if not recipe_library.base_recipe(d["dish_name"])]


def research(dish: dict) -> dict | None:
    """{"sources": [...lead first...], "method_notes", "fallback_used"} —
    the library's own research when it has some, otherwise a new search the
    way agent._research_dish_new runs one, minus the household's save."""
    from app import agent
    from app.tools import recipe_library, recipe_research

    filed = recipe_library.research_for(dish["dish_name"])
    if filed:
        return {"sources": filed["sources"], "method_notes": filed.get("method_notes") or "",
                "fallback_used": bool(filed.get("fallback_used")), "new": False}
    name, cuisine, note = dish["dish_name"], dish.get("cuisine") or "", dish.get("why") or ""
    budget = agent._dish_research_budget()
    first = {k: min(budget[k], agent._OPEN_SEARCH_SHARE[k]) for k in budget}
    found = agent.research_dish_llm(name, cuisine, note, budget=first) or {}
    sources, fallback = found.get("sources") or [], False
    trusted = recipe_research.trusted_cooks_for(cuisine)
    if recipe_research.needs_trusted_cooks(sources):
        used = found.get("used") or {}
        left = {k: budget[k] - used.get(k, 0) for k in budget}
        tokens_left = recipe_research.dish_input_tokens() - int(found.get("input_tokens") or 0)
        if left["web_fetch"] > 0 and left["web_search"] > 0 and tokens_left > 0:
            from_cooks = agent.research_dish_llm(name, cuisine, note, trusted=trusted, budget=left,
                                                 input_tokens=tokens_left) or {}
            if any(s.get("read") for s in from_cooks.get("sources") or []):
                found, sources, fallback = from_cooks, from_cooks["sources"], True
    ordered = recipe_research.pick_lead(sources, trusted if fallback else None)
    if not ordered:
        return None
    return {"sources": ordered, "method_notes": found.get("method_notes") or "",
            "fallback_used": fallback, "new": True}


def write(dish: dict, found: dict | None) -> dict | None:
    """The recipe writer's version of the dish for a table of SERVES with
    no household in it (no must-avoids, no kitchen, no names), held to its
    own arithmetic the way a household's recipe is."""
    from app import agent, tools
    from app.tools import recipe_research

    spec = {
        "name": dish["dish_name"],
        "slot": dish.get("meal") or "dinner",
        "cuisine": dish.get("cuisine") or "",
        "main_protein": dish.get("main_protein") or "",
        "tags": [],
        "food_groups": [],
        "dish_note": dish.get("why") or "",
        "prep_time_minutes": None,
        "cook_time_minutes": None,
        "serves": SERVES,
        "must_not_contain": [],
    }
    writer_research = recipe_research.research_for_writer(found)
    if writer_research:
        spec["research"] = writer_research
    detail = agent.generate_recipe_details_llm(spec) or {}
    ingredients = [i for i in (detail.get("ingredients") or [])
                   if isinstance(i, dict) and str(i.get("item") or "").strip()]
    if not ingredients or not detail.get("instructions"):
        return None
    servings = detail.get("default_servings") or SERVES
    ingredients = tools.complete_ingredient_list(ingredients, detail.get("instructions") or [], servings)
    instructions, ingredients, _ = agent._settle_recipe_amounts(
        dish["dish_name"], ingredients, detail.get("instructions") or [], servings,
    )
    return {
        "ingredients": ingredients,
        "instructions": instructions,
        "default_servings": servings,
        "prep_time_minutes": detail.get("prep_time_minutes"),
        "cook_time_minutes": detail.get("cook_time_minutes"),
        "advance_prep_notes": detail.get("advance_prep_notes") or "",
        "advance_prep_step_indices": detail.get("advance_prep_step_indices") or [],
    }


def seed_one(dish: dict) -> dict:
    """Research, write and file one dish. Never raises: a failure is the
    dish's outcome, and the next run tries it again."""
    from app.tools import recipe_library

    name = dish["dish_name"]
    try:
        found = research(dish)
        if found is None:
            # No research is not no recipe — the app writes without it too —
            # but the library is meant to be researched dishes, so the dish
            # waits for a later run rather than going in unsourced.
            return {"dish": name, "outcome": "no_research"}
        if found.get("new"):
            # Filed the moment it lands, as live research is: a run that
            # dies in the writer doesn't pay for this search again.
            recipe_library.file_entry(name, sources=found["sources"], method_notes=found["method_notes"],
                                      fallback_used=found["fallback_used"], cuisine=dish.get("cuisine") or "",
                                      main_protein=dish.get("main_protein") or "", origin="seed")
        recipe = write(dish, found)
        if recipe is None:
            return {"dish": name, "outcome": "not_written"}
        entry_id = recipe_library.file_entry(
            name, **recipe, cuisine=dish.get("cuisine") or "", main_protein=dish.get("main_protein") or "",
            sources=found["sources"], method_notes=found["method_notes"],
            fallback_used=found["fallback_used"], origin="seed",
        )
        return {"dish": name, "outcome": "filed" if entry_id else "not_filed", "id": entry_id,
                "researched": bool(found.get("new"))}
    except Exception as exc:  # one dish never stops the run
        logger.exception("Seeding %r failed", name)
        return {"dish": name, "outcome": "error", "error": str(exc)[:200]}


def file_samples(samples: list[dict]) -> list[dict]:
    """The six reviewed samples, filed exactly as Emily saw them."""
    from app.tools import recipe_library
    out = []
    for s in samples:
        if recipe_library.base_recipe(s["dish_name"]):
            out.append({"dish": s["dish_name"], "outcome": "already_there"})
            continue
        args = {k: v for k, v in s.items() if k != "dish_name"}
        entry_id = recipe_library.file_entry(s["dish_name"], **args)
        out.append({"dish": s["dish_name"], "outcome": "filed" if entry_id else "not_filed", "id": entry_id})
    return out


def run(dishes: list[dict], *, batch_size: int = 5, limit: int | None = None,
        pause_seconds: float = 0.0, log=print) -> list[dict]:
    todo = pending(dishes)
    if limit is not None:
        todo = todo[:limit]
    log(f"{len(dishes) - len(pending(dishes))} of {len(dishes)} dishes already in the library; "
        f"{len(todo)} to seed this run, {batch_size} at a time.")
    results: list[dict] = []
    for start in range(0, len(todo), batch_size):
        batch = todo[start:start + batch_size]
        with ThreadPoolExecutor(max_workers=batch_size) as pool:
            done = list(pool.map(seed_one, batch))
        results.extend(done)
        for r in done:
            log(f"  {r['outcome']:<12} {r['dish']}")
        log(f"Batch {start // batch_size + 1}: {sum(r['outcome'] == 'filed' for r in results)} filed so far.")
        if pause_seconds and start + batch_size < len(todo):
            time.sleep(pause_seconds)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the Shared Recipe Library from the starter list.")
    parser.add_argument("--i-mean-it", action="store_true",
                        help="Required. This spends API money on research and recipe writing.")
    parser.add_argument("--samples", action="store_true",
                        help="File only the six reviewed samples, as they stand (no API calls).")
    parser.add_argument("--batch-size", type=int, default=5, help="Dishes written at once (default 5).")
    parser.add_argument("--limit", type=int, default=None, help="Stop after this many dishes this run.")
    parser.add_argument("--cuisine", default=None, help="Only dishes of this cuisine.")
    parser.add_argument("--pause", type=float, default=0.0, help="Seconds between batches.")
    args = parser.parse_args(argv)

    if not args.i_mean_it:
        print(REFUSAL, file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from app.db import DB_PATH, how_to_run_on_production, init_db, misplaced_db_path_error
    from app.tools import recipe_library

    if not recipe_library.library_enabled():
        print("RECIPE_LIBRARY is off; nothing to seed into.", file=sys.stderr)
        return 1
    print(f"Database: {DB_PATH}")
    # Before init_db(), which would otherwise create the database this check
    # exists to stop us pointing at — the admin scripts' own guard.
    problem = misplaced_db_path_error()
    if problem:
        print(f"\n{problem}\n\n{how_to_run_on_production('seed_recipe_library.py --i-mean-it')}", file=sys.stderr)
        return 1
    init_db()
    if args.samples:
        for r in file_samples(load_samples()):
            print(f"  {r['outcome']:<13} {r['dish']}")
        return 0
    print("Model calls run as household 1: its consent is checked and the spend is logged under it.")
    dishes = load_dishes()
    if args.cuisine:
        dishes = [d for d in dishes if d["cuisine"].lower() == args.cuisine.lower()]
    results = run(dishes, batch_size=max(1, args.batch_size), limit=args.limit, pause_seconds=args.pause)
    failed = [r for r in results if r["outcome"] != "filed"]
    print(f"Done: {len(results) - len(failed)} filed, {len(failed)} left for the next run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
