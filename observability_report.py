"""
What broke, and is the app being used — printed for the morning report.

Run this, read the output, lead with anything under BROKEN. That is the
whole contract.

    python observability_report.py            # last 1 day of errors, 7 of usage
    python observability_report.py --days 7
    python observability_report.py --json     # for a machine to read

Why a script and not the API endpoint: the overnight routine runs in the
cloud with a fresh clone of this repo, not inside the running app. It has
no more access to an authenticated HTTP endpoint on Railway than it does
to Railway's logs — it would need the deployed URL and a household
passphrase, neither of which lives in the repo. It *can* read a database
file. That is the same reason create_household.py and reset_household.py
are scripts, and this follows them.

Point DB_PATH at the database you want (on Railway that is
/data/home_manager.db); locally it defaults to app/home_manager.db.

Reports every household, one section each, because the point is to notice
a beta tester's bad day — and running it per household would mean knowing
in advance which household had one.
"""
from __future__ import annotations

import argparse
import json
import sys

from app import tools
from app.db import get_conn


def _households() -> list[tuple[int, str]]:
    conn = get_conn()
    try:
        return [(r["id"], r["name"]) for r in conn.execute(
            "SELECT id, name FROM households ORDER BY id"
        ).fetchall()]
    finally:
        conn.close()


def collect(days: int) -> list[dict]:
    out = []
    for hid, name in _households():
        # use_household is the same binding a request gets, so these read
        # exactly what that household would see and nothing else.
        with tools.use_household(hid):
            out.append({
                "household_id": hid,
                "household": name,
                "errors": tools.get_recent_errors(days=days),
                "usage": tools.get_usage_summary(days=max(days, 7)),
            })
    return out


def _print_human(report: list[dict], days: int) -> None:
    for h in report:
        errors, usage = h["errors"], h["usage"]
        print(f"\n=== {h['household']} (household {h['household_id']}) ===")

        if errors["total"]:
            kinds = ", ".join(f"{n} {k}" for k, n in errors["by_kind"].items())
            print(f"  BROKEN — {errors['total']} in the last {days}d: {kinds}")
            # Grouped, so eleven copies of one failure read as one problem.
            seen: dict[tuple[str, str], int] = {}
            for row in errors["recent"]:
                key = (row["kind"], row["location"])
                seen[key] = seen.get(key, 0) + 1
            for (kind, where), n in sorted(seen.items(), key=lambda kv: -kv[1])[:8]:
                print(f"      {kind:10} {where}" + (f"  (x{n})" if n > 1 else ""))
        else:
            print("  Nothing broke.")

        if usage["looks_inactive"]:
            print(f"  QUIET — no chat, no meals cooked, no plans in {usage['days']}d.")
        else:
            print(
                f"  Used — {usage['chat_turns']} chat turns, "
                f"{usage['meals_cooked']} meals cooked, "
                f"{usage['plans_generated']} plans ({usage['plans_approved']} approved)"
            )
        print(f"  Last active: {usage['last_active_at'] or 'never'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=1, help="how far back to look for errors")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    report = collect(args.days)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report, args.days)

    # Exit 1 when something is worth leading with, so a caller can branch
    # on it without parsing anything.
    return 1 if any(h["errors"]["total"] for h in report) else 0


if __name__ == "__main__":
    sys.exit(main())
