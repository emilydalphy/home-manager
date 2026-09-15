"""
Admin script: take off any recipe title that promises an ingredient the
recipe hasn't got.

Emily, 2026-09-14, standing at the stove: "Seared Turkey and Zucchini
Skillet with White Beans" — seven ingredients, seven steps, and not a bean
in either. Generation is honest from now on (agent._honest_meal_names
corrects a title before the recipe is written), but the recipes already
saved are not, and the Friday leftover of that dinner still reads the same
way. This is the one-off pass over what is already on record.

It corrects the NAME and never the ingredients. A recipe whose groceries
have already been bought must not grow a tin of beans nobody shopped for;
"Seared Turkey and Zucchini Skillet" is the honest name of what is
actually in the pan. See plan_quality.honest_recipe_title for exactly how
little it dares change, and why — it passes over far more titles than it
touches, on purpose.

Read-only by default. A rename has no undo, so look at the list first.

Usage (local):
    python repair_recipe_titles.py                      # household 1, shows what it would change
    python repair_recipe_titles.py --household 2
    python repair_recipe_titles.py --all                # every household on the box
    python repair_recipe_titles.py --all --apply        # write it

Usage (production, via Railway CLI — the one that matters, since the beta
runs on the deployed app):
    railway ssh -- python repair_recipe_titles.py --all
    railway ssh -- python repair_recipe_titles.py --all --apply

NOT `railway run` — that injects the deployed service's variables and then
runs the command on YOUR LAPTOP, against a database file that does not
exist there. The script refuses to start if it detects that mismatch, but
the right command is the one above.

Safe to run twice: a corrected title has nothing left to take off, so a
second run finds nothing.
"""
import argparse
import sys

from app.db import (
    DB_PATH,
    get_conn,
    how_to_run_on_production,
    init_db,
    misplaced_db_path_error,
)
from app import households
from app.tools import use_household
from app.tools.plan_quality import repair_recipe_titles
from app.tools._shared import DEFAULT_HOUSEHOLD_ID


def _household_ids() -> list[int]:
    conn = get_conn()
    rows = conn.execute("SELECT id FROM households ORDER BY id ASC").fetchall()
    conn.close()
    return [r["id"] for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Correct recipe titles that name an ingredient the recipe hasn't got."
    )
    parser.add_argument(
        "--household",
        type=int,
        default=DEFAULT_HOUSEHOLD_ID,
        help=f"Which household to look at (default: {DEFAULT_HOUSEHOLD_ID}).",
    )
    parser.add_argument(
        "--all", action="store_true", dest="every",
        help="Every household, not just one. The beta has two; without this the second is missed.",
    )
    parser.add_argument("--apply", action="store_true", help="Write the corrected titles.")
    args = parser.parse_args()

    print(f"Database: {DB_PATH}")
    # Before init_db(), which would otherwise CREATE the database this
    # check exists to stop us pointing at — same guard as set_chores_enabled.py.
    problem = misplaced_db_path_error()
    if problem:
        sys.exit(f"\n{problem}\n\n{how_to_run_on_production('repair_recipe_titles.py')}")
    init_db()

    if args.every:
        wanted = _household_ids()
    else:
        if not households.household_exists(args.household):
            sys.exit(f"There is no household with id {args.household}.")
        wanted = [args.household]

    written = 0
    for house in wanted:
        with use_household(house):
            found = repair_recipe_titles(apply=args.apply)
        corrections = [f for f in found if f["after"]]
        refusals = [f for f in found if not f["after"]]
        written += len(corrections)
        head = f"\nHousehold {house}" if len(wanted) > 1 else ""
        if not found:
            print(f"{head}\nEvery recipe title matches what's in the recipe. Nothing to change.")
            continue
        if head:
            print(head)
        if corrections:
            print(f"\n{len(corrections)} title{'' if len(corrections) == 1 else 's'}:")
            for change in corrections:
                print(f"  {change['before']}")
                print(f"    -> {change['after']}")
        # Printed, not logged. Somebody is reading this to decide whether to
        # write; "I found one and left it alone" is part of the answer.
        if refusals:
            print(f"\n{len(refusals)} left alone:")
            for change in refusals:
                print(f"  {change['before']}")
                print(f"    {change['why']}")

    if args.apply and written:
        print("\nWritten. Every screen reads the recipe's name, so the week card, the Cook")
        print("view and the leftovers all say the new one from their next load.")
    elif written:
        print("\nNothing was changed. Run it again with --apply to write these.")


if __name__ == "__main__":
    main()
