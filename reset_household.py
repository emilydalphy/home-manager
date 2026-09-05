"""
One-time admin script: wipes ALL household data (members, preferences,
recipes, weekly plans, grocery list, inventory, chores, share links,
growth-counter log — everything) so the app behaves like a brand-new
household again. This does NOT touch any code/features — it only clears
rows in the database. Safe to run against local dev; for production, it
has to run INSIDE the deployed container (see below), because that is
the only place the real database file exists.

Usage (local):
    python reset_household.py             # household 1
    python reset_household.py --household 2

Usage (production, via Railway CLI):
    railway ssh -- python reset_household.py
    (railway link the project first if you haven't: `railway link`)

NOT `railway run` — that injects the deployed service's variables and
then runs the command on YOUR LAPTOP. Production's DB_PATH points at a
file on the mounted volume, which does not exist locally, so the script
would either fail or quietly create an empty database there and report
success against it. `railway ssh` opens a shell on the service itself;
everything after `--` runs there. The script refuses to start if it
detects that mismatch, but the right command is this one.

To confirm which database you are about to wipe, run the safe check first
— it touches no household data:
    railway ssh -- python create_household.py --list

You'll be asked to type RESET to confirm before anything is deleted —
this is irreversible, there's no undo. The confirmation names the
household by name as well as id, because since the beta there is more
than one and "which household am I about to wipe" is now a real question
with a wrong answer.
"""
import argparse
import sys

from app.db import (
    get_conn,
    init_db,
    DB_PATH,
    how_to_run_on_production,
    misplaced_db_path_error,
)
from app.tools._shared import DEFAULT_HOUSEHOLD_ID


def main():
    parser = argparse.ArgumentParser(description="Wipe one household's data.")
    parser.add_argument(
        "--household",
        type=int,
        default=DEFAULT_HOUSEHOLD_ID,
        help=f"Which household to wipe (default: {DEFAULT_HOUSEHOLD_ID}).",
    )
    household_id = parser.parse_args().household

    print(f"This will target the database at: {DB_PATH}")
    # Before init_db(), which would otherwise CREATE the database this
    # check exists to stop us pointing at.
    problem = misplaced_db_path_error()
    if problem:
        sys.exit(f"\n{problem}\n\n{how_to_run_on_production('reset_household.py')}")
    init_db()  # make sure schema/tables exist before we query them
    conn = get_conn()

    household_row = conn.execute(
        "SELECT name FROM households WHERE id = ?", (household_id,)
    ).fetchone()
    if not household_row:
        print(f"There is no household with id {household_id}. Nothing was deleted.")
        conn.close()
        sys.exit(1)
    household_name = household_row["name"]

    # Discover every table that carries a household_id column — i.e.
    # everything except `households` itself — rather than hardcoding a
    # list that could drift out of sync with schema.sql over time.
    tables = [
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    # `households` is the household itself. `household_credentials` carries a
    # household_id and so would otherwise be swept up by the discovery above
    # — but it is the passphrase that signs into the household, not data
    # belonging to it. Deleting it would lock that household out of the app
    # permanently, with no way back in and nothing on screen to explain why.
    # A reset is meant to empty a household, not destroy it.
    never_wipe = {"households", "household_credentials"}
    household_scoped_tables = []
    for t in tables:
        if t in never_wipe:
            continue
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({t})")}
        if "household_id" in cols:
            household_scoped_tables.append(t)

    print(f"\nCurrent data for household {household_id} ({household_name!r}):")
    for t in household_scoped_tables:
        count = conn.execute(f"SELECT COUNT(*) AS c FROM {t} WHERE household_id = ?", (household_id,)).fetchone()["c"]
        if count:
            print(f"  {t}: {count} row(s)")

    print(
        f"\nThis will permanently delete ALL of the above for household {household_id} "
        f"({household_name!r}) and reset its goals — there's no undo."
    )
    confirm = input("Type RESET to continue: ").strip()
    if confirm != "RESET":
        print("Cancelled — nothing was deleted.")
        conn.close()
        return

    # FK constraints are on for normal app operation, but a full wipe of a
    # single household doesn't need to respect delete order — turn them
    # off for this connection only, for the duration of the wipe.
    conn.execute("PRAGMA foreign_keys = OFF")
    for t in household_scoped_tables:
        conn.execute(f"DELETE FROM {t} WHERE household_id = ?", (household_id,))
    # Keep the households row itself (the app expects the household to
    # exist; wiping it would orphan the passphrase that signs into it),
    # just reset its freeform goals field.
    conn.execute("UPDATE households SET goals = '' WHERE id = ?", (household_id,))
    conn.commit()
    conn.close()

    print(
        "\nDone. The app now has zero household members, so the next visit to "
        "the chat page will auto-redirect to /onboarding — the redesigned "
        "first-plan reveal included."
    )


if __name__ == "__main__":
    main()
