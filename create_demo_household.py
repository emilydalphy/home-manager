"""
Admin script: create (or reset) the demo household Apple's App Store
reviewer signs into.

Why this exists
----------------
App Review needs working sign-in details that land on a household that
already looks lived-in — a planned week, a shopping list, a couple of
recipes — so a reviewer can judge the app in minutes instead of stumbling
through empty onboarding. That household must never be a real one: not
Emily's (household 1), not a beta tester's. This script creates a
household set aside for exactly that purpose, named so it is unmistakable
in any household listing, and fills it with believable-but-fake data —
never anything copied from a real household, and never any call to the
chat model (no ANTHROPIC_API_KEY is touched; every row here is written
directly).

Idempotent, safely
-------------------
Running this script twice does not create two demo households or double
up its data. It finds the demo household by its fixed name
(DEMO_HOUSEHOLD_NAME) — creating it on the first run — and on every run
after that, wipes just that household's data and rebuilds it from
scratch, the same table-discovery wipe reset_household.py uses (every
table with a household_id column, minus `households` and
`household_credentials` themselves). That is safe here specifically
because of the refusal below: this script will not run that wipe, or
anything else, against any household other than the one this file
created and named.

Refuses to touch anything but the demo household
--------------------------------------------------
Two separate guards, both checked before a single row is written or
deleted:
  1. It never acts on household 1 (households.DEFAULT_HOUSEHOLD_ID) —
     Emily's real household — full stop.
  2. It never acts on a household whose name isn't exactly
     DEMO_HOUSEHOLD_NAME, even if somehow asked to by id. There is no
     --household flag on this script for that reason: the demo household
     is looked up by name, not accepted as an argument, so there is
     nothing for a typo or a stale id to point at the wrong household.

The passphrase
---------------
Read from the DEMO_PASSPHRASE environment variable — set as a Railway
variable for the deployed app — and never written into this repo. The
script refuses to run with no passphrase available (no silent fallback
to a generated one that would only be printed once and then lost in a
CI log). See MIN_PASSPHRASE_LENGTH in app/households.py for the length
floor.

Usage (local, throwaway database only — see the project's loop skill on
never running admin scripts against app/home_manager.db):
    DB_PATH=/tmp/demo-preview.db DEMO_PASSPHRASE="correct horse battery" \\
        python create_demo_household.py

Usage (production, via Railway CLI — this is the one that matters):
    railway ssh -- python create_demo_household.py

NOT `railway run` — see create_household.py's module docstring for why;
the same mismatch applies here and the same guard (misplaced_db_path_error)
refuses to run into it.

--reset does the same rebuild as a plain run (rebuilding IS the reset —
the point of idempotency is that there is no separate "first create" vs.
"reset" data path to keep in sync) but skips printing the passphrase
banner, since after the first run nobody needs to see it again unless it
changed.

AI consent — open TODO
------------------------
A sibling branch (seen locally as `appstore-ai-consent`, though the
version reachable from this branch's `git log` looks stale/out of date
with unrelated changes — it may not be the current version of that work)
is adding per-household AI-consent records. This script does not know
that schema yet and is not guessing at it. If that lands first:
    TODO(ai-consent): grant consent for the demo household here, right
    after it's created/found, using whatever function that branch adds
    (something like `consent.grant(household_id, ...)`) — the demo
    household should look like a household that has already said yes to
    the chat feature, same as a real one would before using it.
This is also called out on the Loop Board card for this ticket so it
isn't lost.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

from app import households, tools
from app.db import (
    DB_PATH,
    get_conn,
    how_to_run_on_production,
    init_db,
    misplaced_db_path_error,
)
from app.tools._shared import DEFAULT_HOUSEHOLD_ID, use_household

# Fixed and unmistakable — this is how the script (and any human reading
# a household list) recognizes "this is the demo one", and it is the only
# thing that makes the refusal-guard below meaningful.
DEMO_HOUSEHOLD_NAME = "The Demo Household (App Store review)"

DEMO_PASSPHRASE_ENV = "DEMO_PASSPHRASE"

# Names for the two adults: an Emily & Vineeth-style pair (first names,
# a couple running a household) but deliberately not them.
_ADULT_NAMES = ["Maya", "Theo"]


def _find_demo_household() -> dict | None:
    for h in households.list_households():
        if h["name"] == DEMO_HOUSEHOLD_NAME:
            return h
    return None


def _refuse_unless_demo_household(household_id: int, name: str) -> None:
    """
    The safety rail the whole script leans on: everything below this call
    site in main() operates on `household_id`, so nothing runs unless
    both checks pass.
    """
    if household_id == DEFAULT_HOUSEHOLD_ID:
        sys.exit(
            "Refusing: that is household 1, Emily's real household. This "
            "script only ever creates or resets the demo household — it "
            "will not touch household 1, no matter what."
        )
    if name != DEMO_HOUSEHOLD_NAME:
        sys.exit(
            f"Refusing: household {household_id} is named {name!r}, not "
            f"{DEMO_HOUSEHOLD_NAME!r}. This does not look like the demo "
            "household, so nothing was touched."
        )


def _wipe_household_data(household_id: int) -> None:
    """
    Delete every row belonging to this household across every
    household-scoped table, keeping the household and its credential row
    intact — the same discovery-based wipe reset_household.py uses,
    minus its interactive confirmation (safe here only because
    _refuse_unless_demo_household has already run and this can only ever
    be the demo household).
    """
    conn = get_conn()
    conn.execute("PRAGMA foreign_keys = OFF")
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    never_wipe = {"households", "household_credentials"}
    for t in tables:
        if t in never_wipe:
            continue
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({t})")}
        if "household_id" in cols:
            conn.execute(f"DELETE FROM {t} WHERE household_id = ?", (household_id,))
    conn.execute("UPDATE households SET goals = '' WHERE id = ?", (household_id,))
    conn.commit()
    conn.close()
    # Recipe photos live on disk beside the database, indexed by the rows
    # just deleted — drop the files with them so a rebuild doesn't leave
    # orphaned images from a previous run's recipes.
    from app import recipe_photos

    recipe_photos.remove_household_photos(household_id)


def _build_demo_data(household_id: int) -> dict:
    """
    Fill the (freshly emptied) demo household with a believable week —
    written directly through the same `tools` functions the chat agent
    calls, but invoked here as plain Python, never through a model. No
    ANTHROPIC_API_KEY is read or needed.
    """
    with use_household(household_id):
        for name in _ADULT_NAMES:
            tools.add_member(name)
            tools.set_member_age_group(name, "adult")

        tools.set_household_meal_preferences(
            notes="Demo household for App Store review — likes quick weeknight dinners.",
            protein_preferences={"chicken": 5, "tofu": 4, "beef": 3, "fish": 4},
            cuisine_preferences=["Italian", "Mexican", "Thai"],
            cooking_time_preference="30 minutes or less on weeknights",
            novelty_preference="balanced",
            eating_style="balanced, nothing restrictive",
            dinners_per_week=7,
            breakfasts_per_week=0,
            lunches_per_week=0,
            snacks_per_week=3,
            snacks_per_day=1,
        )

        tools.add_recipe(
            "Chicken Stir Fry",
            ingredients=[
                {"item": "chicken breast", "qty": "1 lb"},
                {"item": "broccoli", "qty": "1 head"},
                {"item": "bell pepper", "qty": "1"},
                {"item": "soy sauce", "qty": "1/4 cup"},
                {"item": "garlic", "qty": "2 cloves"},
                {"item": "rice", "qty": "2 cups"},
            ],
            notes="Quick weeknight dinner, about 25 minutes.",
            cuisine="Asian",
            main_protein="chicken",
            food_groups=["protein", "carb", "vegetable"],
            instructions=[
                "Cook rice according to package directions.",
                "Slice chicken and vegetables.",
                "Stir-fry chicken until cooked through, then add vegetables.",
                "Add soy sauce and garlic, toss, and serve over rice.",
            ],
            default_servings=4,
            prep_time_minutes=10,
            cook_time_minutes=15,
        )
        tools.add_recipe(
            "Weeknight Chili",
            ingredients=[
                {"item": "ground beef", "qty": "1 lb"},
                {"item": "kidney beans", "qty": "1 can"},
                {"item": "diced tomatoes", "qty": "1 can"},
                {"item": "onion", "qty": "1"},
                {"item": "chili powder", "qty": "2 tbsp"},
            ],
            notes="Makes enough for leftovers the next day.",
            cuisine="Mexican",
            main_protein="beef",
            food_groups=["protein", "vegetable"],
            instructions=[
                "Brown the beef with the diced onion.",
                "Add beans, tomatoes, and chili powder.",
                "Simmer 20 minutes.",
            ],
            default_servings=4,
            prep_time_minutes=10,
            cook_time_minutes=25,
        )
        tools.add_recipe(
            "Pesto Pasta",
            ingredients=[
                {"item": "pasta", "qty": "1 box"},
                {"item": "pesto", "qty": "1 jar"},
                {"item": "cherry tomatoes", "qty": "1 pint"},
                {"item": "parmesan", "qty": "1 block"},
            ],
            notes="A fast, no-fuss night.",
            cuisine="Italian",
            main_protein="vegetarian",
            food_groups=["carb", "vegetable"],
            default_servings=4,
            prep_time_minutes=5,
            cook_time_minutes=15,
        )

        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        plan = tools.create_weekly_plan(week_start.isoformat())
        plan_id = plan["weekly_plan_id"]

        # A believable week: the three saved recipes plus a few realistic
        # freeform nights, so the plan doesn't read as three dishes on
        # repeat.
        dinners = [
            "Chicken Stir Fry",
            "Tacos",
            "Weeknight Chili",
            "Leftover chili",
            "Pesto Pasta",
            "Pizza night",
            "Grilled salmon with rice",
        ]
        for offset, dish in enumerate(dinners):
            meal_date = (week_start + timedelta(days=offset)).isoformat()
            tools.plan_meal(meal_date, dish, slot="dinner", weekly_plan_id=plan_id)

        # Approving is what puts the week's ingredients on the grocery
        # list (see approve_weekly_plan's docstring) — this is also what
        # makes the plan show up as a real, agreed-to week rather than an
        # unreviewed draft.
        tools.approve_weekly_plan(plan_id, approved_by=_ADULT_NAMES[0])

        # A couple of standing items beyond what the recipes need, so the
        # list doesn't read as purely recipe-generated.
        tools.add_grocery_item("paper towels", category="other")
        tools.add_grocery_item("coffee", category="pantry")

        # TODO(ai-consent): once the sibling branch's consent schema
        # lands on main, grant AI consent for this household right here —
        # see the module docstring above for why this isn't guessed at
        # now.

        return {"weekly_plan_id": plan_id, "week_start": week_start.isoformat()}


def _print_banner(household_id: int, passphrase: str, created: bool) -> None:
    verb = "Created" if created else "Reset"
    print()
    print("=" * 70)
    print(f"  {verb} demo household {household_id}: {DEMO_HOUSEHOLD_NAME}")
    print(f"  Passphrase: {passphrase}")
    print("=" * 70)
    print()
    print("  This is the App Store review household — a planned week, a")
    print("  grocery list, and a few recipes are already in place. Give this")
    print("  passphrase to App Store Connect's review-notes field (see")
    print("  APP_REVIEW_NOTES.md), not the passphrase for any real household.")
    print()


def main() -> None:
    print(f"Database: {DB_PATH}")
    # Before init_db(), which would otherwise CREATE the database this
    # check exists to stop us pointing at.
    problem = misplaced_db_path_error()
    if problem:
        sys.exit(f"\n{problem}\n\n{how_to_run_on_production('create_demo_household.py')}")
    init_db()

    passphrase = os.environ.get(DEMO_PASSPHRASE_ENV, "").strip()
    if not passphrase:
        sys.exit(
            f"Set {DEMO_PASSPHRASE_ENV} before running this script — the demo "
            "household's passphrase comes from that environment variable and is "
            "never stored in the repo. On Railway, set it as a service variable; "
            "locally, export it in your shell for this one run."
        )

    existing = _find_demo_household()
    created = existing is None
    if created:
        try:
            household_id = households.create_household(DEMO_HOUSEHOLD_NAME, passphrase)
        except ValueError as e:
            sys.exit(str(e))
    else:
        household_id = existing["id"]
        name = existing["name"]
        _refuse_unless_demo_household(household_id, name)
        # Re-set the passphrase every run: DEMO_PASSPHRASE may have
        # changed since the household was first created (e.g. rotated
        # after review), and a stale stored hash would silently lock the
        # new value out.
        try:
            households.set_passphrase(household_id, passphrase)
        except ValueError as e:
            sys.exit(str(e))

    # Re-check by id even on the freshly-created path: create_household()
    # already enforces uniqueness, but this keeps the guard unconditional
    # rather than "trusted because we just created it".
    name = next(h["name"] for h in households.list_households() if h["id"] == household_id)
    _refuse_unless_demo_household(household_id, name)

    if not created:
        _wipe_household_data(household_id)

    _build_demo_data(household_id)
    _print_banner(household_id, passphrase, created)


if __name__ == "__main__":
    main()
