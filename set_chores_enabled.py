"""
Admin script: switch Chores on or off for one household.

Loop Board "Chores v1: Who sees it — a per-household switch" (Emily,
2026-09-12). The beta is meals-only for the tester while Chores is tried
on real weeks in Emily's own house, so whether a house sees Chores at all
— the "Your chores" card on Now, the way into /chores-setup, and the
chores chat tools — is a per-household setting, `households.chores_enabled`,
off for every household until somebody flips it. This script is the
switch. It is deliberately a script rather than a screen, the same way
create_household.py is: two households, one person deciding, and no
settings UI yet that this would belong in.

Usage (local):
    python set_chores_enabled.py --list          # which houses have it on
    python set_chores_enabled.py on              # household 1
    python set_chores_enabled.py on --household 2
    python set_chores_enabled.py off --household 2

Usage (production, via Railway CLI — the one that matters, since the beta
runs on the deployed app, not a laptop):
    railway ssh -- python set_chores_enabled.py on --household 1

NOT `railway run` — that injects the deployed service's variables and
then runs the command on YOUR LAPTOP. Production's DB_PATH points at a
file on the mounted volume, which does not exist locally, so this would
flip a switch in a database nobody will ever read while printing a line
that looks like it worked. `railway ssh` opens a shell on the service
itself; everything after `--` runs there. The script refuses to start if
it detects that mismatch, but the right command is the one above.

Turning a house on changes no chores data — chores rows already there
show on Now the next time it loads. Turning it off hides them again and
leaves every row exactly where it was. Nothing here needs a restart: the
app reads the switch on every request.
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
from app.tools import set_chores_enabled
from app.tools._shared import DEFAULT_HOUSEHOLD_ID


def _status_rows() -> list[dict]:
    """Every household with its switch, for --list and for the confirmation line."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, chores_enabled FROM households ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"], "on": bool(r["chores_enabled"])} for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="Switch Chores on or off for one household.")
    parser.add_argument("state", nargs="?", choices=["on", "off"], help="on or off")
    parser.add_argument(
        "--household",
        type=int,
        default=DEFAULT_HOUSEHOLD_ID,
        help=f"Which household to switch (default: {DEFAULT_HOUSEHOLD_ID}).",
    )
    parser.add_argument("--list", action="store_true", help="Show every household's switch and exit.")
    args = parser.parse_args()

    print(f"Database: {DB_PATH}")
    # Before init_db(), which would otherwise CREATE the database this
    # check exists to stop us pointing at — same guard as create_household.py.
    problem = misplaced_db_path_error()
    if problem:
        sys.exit(f"\n{problem}\n\n{how_to_run_on_production('set_chores_enabled.py')}")
    init_db()

    if args.list:
        print("\nChores switch:")
        for row in _status_rows():
            print(f"  {row['id']}: {row['name']} — {'ON' if row['on'] else 'off'}")
        return

    if not args.state:
        parser.error("say on or off (or use --list)")

    if not households.household_exists(args.household):
        sys.exit(f"There is no household with id {args.household}.")
    try:
        result = set_chores_enabled(args.state == "on", household=args.household)
    except ValueError as e:
        sys.exit(str(e))
    name = next((r["name"] for r in _status_rows() if r["id"] == args.household), str(args.household))
    if result["chores_enabled"]:
        print(f"\nChores is ON for household {args.household} ({name}). Now shows the chores card from its next load.")
    else:
        print(f"\nChores is off for household {args.household} ({name}). Nothing was deleted; the rows are still there.")


if __name__ == "__main__":
    main()
