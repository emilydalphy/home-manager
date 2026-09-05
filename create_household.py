"""
Admin script: create a new household and the passphrase that opens it.

This is how the beta tester gets her own household. It is deliberately a
script rather than a screen — there is no sign-up flow, no invite email and
no account management UI in the app, because for one trusted friend the
cheapest correct thing is Emily running this once and sending her the
passphrase however she likes.

Usage (local):
    python create_household.py "The Smiths"
    python create_household.py "The Smiths" --passphrase "correct horse battery"

Usage (production, via Railway CLI — this is the one that matters, since
the beta tester signs into the deployed app, not a laptop):
    railway ssh -- python create_household.py "The Smiths"

NOT `railway run` — that injects the deployed service's variables and
then runs the command on YOUR LAPTOP. Production's DB_PATH points at a
file on the mounted volume, which does not exist locally, so this would
mint a household into a database nobody will ever read, while printing a
passphrase that looks real. `railway ssh` opens a shell on the service
itself; everything after `--` runs there. The script refuses to start if
it detects that mismatch, but the right command is this one.

--list is the safe check before anything destructive: it touches no
household data and prints the database path it found plus the households
in it, so it is the quickest way to confirm you are pointed at production.
(It does run the app's ordinary startup migrations first, the same ones
the app itself runs every boot — so "touches no household data" rather
than "changes nothing at all".)
    railway ssh -- python create_household.py --list

With no --passphrase, one is generated and printed. Nothing is stored in
plain text: only a PBKDF2 hash goes into the database, so this printout is
the only time the passphrase is visible. If it is lost, use
--reset-passphrase to set a new one rather than creating a second
household.

    python create_household.py --list
    python create_household.py --reset-passphrase 2

The new household starts genuinely empty — no members, no recipes, nothing
copied from anyone else's. Signing in with its passphrase lands on the
app's normal onboarding, exactly as Emily's did.
"""
import argparse
import secrets
import sys

from app.db import (
    DB_PATH,
    how_to_run_on_production,
    init_db,
    misplaced_db_path_error,
)
from app import households


# Four words from a small, deliberately unambiguous list. Long enough to be
# unguessable at this scale, and typable on a phone keyboard by someone who
# was read it over the phone -- which is how it will actually be delivered.
_WORDS = [
    "amber", "anchor", "apple", "basket", "beacon", "birch", "bramble", "bridge",
    "candle", "cedar", "cinder", "clover", "compass", "copper", "cotton", "crescent",
    "daisy", "domino", "ember", "fable", "falcon", "feather", "fennel", "garnet",
    "ginger", "granite", "harbor", "hazel", "heron", "hollow", "indigo", "ivory",
    "juniper", "kettle", "lantern", "laurel", "lemon", "lilac", "linen", "marble",
    "meadow", "mellow", "mitten", "nectar", "nimbus", "olive", "orchard", "otter",
    "pebble", "pepper", "pewter", "pillow", "poppy", "quartz", "quiet", "ribbon",
    "rosemary", "saffron", "sage", "sandal", "sparrow", "spruce", "sugar", "summit",
    "tangle", "teapot", "thistle", "timber", "tulip", "velvet", "walnut", "willow",
    "window", "winter", "yarrow",
]


def _generate_passphrase() -> str:
    return "-".join(secrets.choice(_WORDS) for _ in range(4))


def _print_credential(name: str, household_id: int, passphrase: str) -> None:
    print()
    print("=" * 66)
    print(f"  Household {household_id}: {name}")
    print(f"  Passphrase: {passphrase}")
    print("=" * 66)
    print()
    print("  This is the only time the passphrase is shown — only a hash is")
    print("  stored. Give it to her directly (text, signal, in person); she")
    print("  enters it on the app's normal sign-in screen and gets her own")
    print("  empty household.")
    print()
    print("  If it's lost, set a new one rather than creating a second")
    print(f"  household:  python create_household.py --reset-passphrase {household_id}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a household, or manage its passphrase.")
    parser.add_argument("name", nargs="?", help="Name for the new household, e.g. \"The Smiths\".")
    parser.add_argument(
        "--passphrase",
        default=None,
        help="Set an explicit passphrase instead of generating one.",
    )
    parser.add_argument("--list", action="store_true", help="List households and exit.")
    parser.add_argument(
        "--reset-passphrase",
        type=int,
        metavar="HOUSEHOLD_ID",
        help="Replace an existing household's passphrase instead of creating one.",
    )
    args = parser.parse_args()

    print(f"Database: {DB_PATH}")
    # Before init_db(), which would otherwise CREATE the database this
    # check exists to stop us pointing at. --list is guarded too: its
    # whole job is answering "which database am I looking at", and a
    # confident answer about the wrong one is the failure being fixed.
    problem = misplaced_db_path_error()
    if problem:
        sys.exit(f"\n{problem}\n\n{how_to_run_on_production('create_household.py')}")
    init_db()

    if args.list:
        print("\nHouseholds:")
        for row in households.list_households():
            if row["id"] == households.DEFAULT_HOUSEHOLD_ID and not row["has_credential"]:
                how = "signs in with HOME_MANAGER_PASSWORD"
            elif row["has_credential"]:
                how = "has its own passphrase"
            else:
                how = "NO WAY TO SIGN IN — set one with --reset-passphrase"
            print(f"  {row['id']}: {row['name']} — {how}")
        return

    if args.reset_passphrase is not None:
        household_id = args.reset_passphrase
        if not households.household_exists(household_id):
            sys.exit(f"There is no household with id {household_id}.")
        passphrase = args.passphrase or _generate_passphrase()
        try:
            households.set_passphrase(household_id, passphrase)
        except ValueError as e:
            sys.exit(str(e))
        name = next(
            (h["name"] for h in households.list_households() if h["id"] == household_id),
            str(household_id),
        )
        print(f"\nReplaced the passphrase for household {household_id}. The old one no longer works.")
        _print_credential(name, household_id, passphrase)
        return

    if not args.name:
        parser.error("a household name is required (or use --list / --reset-passphrase)")

    passphrase = args.passphrase or _generate_passphrase()
    try:
        household_id = households.create_household(args.name, passphrase)
    except ValueError as e:
        sys.exit(str(e))
    _print_credential(args.name, household_id, passphrase)


if __name__ == "__main__":
    main()
