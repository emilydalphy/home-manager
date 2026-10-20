"""
Recipe library batches written by Claude Code agents, on Emily's plan rather
than the app's API key (Emily, 2026-10-20: "can't we run it through my
current credits ... and not through the API").

An agent researches a dish on the web and writes it up by the app's own
writer rules, then saves it as one JSON file under data/recipe_library_batches/.
This script makes NO model calls:

    python library_batch.py todo [--from-end] [--n 20]   dishes nobody has done yet
    python library_batch.py rules                        the writer's rules, to follow
    python library_batch.py check FILE...                the app's own checks; exit 1 on a problem
    python library_batch.py file FILE... --i-mean-it     file into the library (live DB: Emily's yes)

One file per dish: data/recipe_library_batches/<dish_key>.json, holding the
keyword arguments of recipe_library.file_entry plus "dish_name" (the same
shape as data/recipe_library_samples.json's entries).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BATCH_DIR = ROOT / "data" / "recipe_library_batches"
SEED_FILE = ROOT / "data" / "recipe_library_seed.json"
SAMPLES_FILE = ROOT / "data" / "recipe_library_samples.json"
SERVES = 4
REQUIRED = ("dish_name", "cuisine", "main_protein", "default_servings", "prep_time_minutes",
            "cook_time_minutes", "ingredients", "instructions", "method_notes", "sources")
DASH = re.compile(r"[—–]|\s-\s")
PERSONIFY = re.compile(r"\b(won[’']t wait|wake[s]? (it|them) up|back to life|the pan wants|happy)\b", re.I)
SERVE_WHY = re.compile(r"\b(to scoop|soaks? up|so everyone can|build their own|so the \w+ glisten)", re.I)


def _scratch_db() -> None:
    if not os.environ.get("DB_PATH"):
        os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "batch.db")
        os.environ.setdefault("DISABLE_BACKUPS", "1")
    from app import db
    db.init_db()


def dish_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def done_names() -> set[str]:
    names = {s["dish_name"].lower() for s in json.loads(SAMPLES_FILE.read_text())["samples"]}
    for p in BATCH_DIR.glob("*.json"):
        try:
            names.add(json.loads(p.read_text())["dish_name"].lower())
        except Exception:
            pass
    return names


def cmd_todo(args) -> int:
    dishes = json.loads(SEED_FILE.read_text())["dishes"]
    done = done_names()
    left = [d for d in dishes if d["dish_name"].lower() not in done]
    if args.from_end:
        left = left[::-1]
    print(f"{len(dishes) - len(left)} of {len(dishes)} done; {len(left)} left.")
    for d in left[: args.n]:
        print(json.dumps(d, ensure_ascii=False))
    return 0


def cmd_rules(args) -> int:
    from app import agent
    print(agent.RECIPE_DETAILS_INSTRUCTIONS)
    return 0


def check_one(path: Path) -> list[str]:
    from app import tools
    from app.tools import recipe_research, recipes
    problems = []
    try:
        e = json.loads(path.read_text())
    except Exception as exc:
        return [f"not JSON: {exc}"]
    for k in REQUIRED:
        if k not in e or e[k] in (None, "", []):
            problems.append(f"missing {k}")
    if problems:
        return problems
    if path.stem != dish_key(e["dish_name"]):
        problems.append(f"file should be named {dish_key(e['dish_name'])}.json")
    # A whole batch (a cake, a tray of puffs) keeps what it really makes and
    # says so in a step ("This makes 8 slices."); everything else is for 4.
    batch = any("This makes" in s for s in e.get("instructions") or [])
    if int(e["default_servings"]) != SERVES and not batch:
        problems.append(f"default_servings must be {SERVES} (or a step saying \"This makes ...\" for a whole batch)")
    srcs = e["sources"]
    if not any(s.get("url") for s in srcs):
        problems.append("no source with a url")
    ordered = recipe_research.pick_lead(srcs, recipe_research.trusted_cooks_for(e["cuisine"]) if e.get("fallback_used") else None)
    if not ordered:
        problems.append("no source can lead (needs a read page)")
    elif ordered[0].get("url") != next((s.get("url") for s in srcs if s.get("lead")), None):
        problems.append(f"lead should be {ordered[0].get('name')} by the app's rule (top rating, 20+ ratings)")
    completed = tools.complete_ingredient_list(e["ingredients"], e["instructions"], SERVES)
    added = [i["item"] for i in completed if i["item"] not in {x["item"] for x in e["ingredients"]}]
    # A soup that makes its own broth says "the broth" (Pot-au-Feu, Canh
    # Chua, 2026-10-20): a look, not a fix, so the step isn't reworded to
    # "the cooking liquid" to please the checker.
    own = [a for a in added if a.lower() in ("broth", "stock")]
    for a in own:
        print(f"  look {path.name}: a step says {a.lower()}, not on the list"
              " (fine if the dish makes its own; add it if it's bought)")
    added = [a for a in added if a not in own]
    if added:
        problems.append(f"steps use things not on the list: {', '.join(added)}")
    r = recipes.check_steps_ingredients_consistency(e["ingredients"], e["instructions"], SERVES)
    if r["unused_ingredients"]:
        problems.append(f"on the list but in no step: {', '.join(r['unused_ingredients'])}")
    for m in r["amount_mismatches"]:
        # The amount check misreads "the last clove" and "2 whole garlic
        # cloves" (2026-10-20), so it is a look, not a failure.
        print(f"  look {path.name}: steps use {m['in_steps']} of {m['item']}, list says {m['listed']}"
              " (often a misread; fix only if the sum is really off)")
    for n, step in enumerate(e["instructions"], 1):
        if DASH.search(step):
            problems.append(f"step {n} has a dash")
        if PERSONIFY.search(step):
            problems.append(f"step {n} personifies food")
    if SERVE_WHY.search(e["instructions"][-1]):
        problems.append("serving line explains itself")
    return problems


def cmd_check(args) -> int:
    _scratch_db()
    bad = 0
    for f in args.files:
        probs = check_one(Path(f))
        print(("OK   " if not probs else "FIX  ") + Path(f).name + ("" if not probs else ": " + "; ".join(probs)))
        bad += bool(probs)
    return 1 if bad else 0


def cmd_file(args) -> int:
    if not args.i_mean_it:
        print("Files into the library of whatever DB_PATH points at. Pass --i-mean-it.")
        return 2
    from app.tools import recipe_library
    for f in args.files:
        e = json.loads(Path(f).read_text())
        if recipe_library.base_recipe(e["dish_name"]):
            print("already  ", e["dish_name"]); continue
        kw = {k: v for k, v in e.items() if k != "dish_name"}
        kw.setdefault("origin", "seed")
        print("filed    " if recipe_library.file_entry(e["dish_name"], **kw) else "not filed", e["dish_name"])
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("todo"); t.add_argument("--from-end", action="store_true"); t.add_argument("--n", type=int, default=20)
    sub.add_parser("rules")
    c = sub.add_parser("check"); c.add_argument("files", nargs="+")
    f = sub.add_parser("file"); f.add_argument("files", nargs="+"); f.add_argument("--i-mean-it", action="store_true")
    a = p.parse_args(argv)
    return {"todo": cmd_todo, "rules": cmd_rules, "check": cmd_check, "file": cmd_file}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
