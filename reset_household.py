"""
One-time admin script: wipes ALL household data (members, preferences,
recipes, weekly plans, grocery list, inventory, chores, share links,
growth-counter log — everything) so the app behaves like a brand-new
household again. This does NOT touch any code/features — it only clears
rows in the database. Safe to run against local dev; for production,
run it with Railway's env vars injected so it targets the real database
(see instructions below), not this repo's local file.

Usage (local):
    python reset_household.py

Usage (production, via Railway CLI):
    railway run python reset_household.py
    (railway link the project first if you haven't: `railway link`)

You'll be asked to type RESET to confirm before anything is deleted —
this is irreversible, there's no undo.
"""
from app.db import get_conn, init_db, DB_PATH

HOUSEHOLD_ID = 1


def main():
    print(f"This will target the database at: {DB_PATH}")
    init_db()  # make sure schema/tables exist before we query them
    conn = get_conn()

    # Discover every table that carries a household_id column — i.e.
    # everything except `households` itself — rather than hardcoding a
    # list that could drift out of sync with schema.sql over time.
    tables = [
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    household_scoped_tables = []
    for t in tables:
        if t == "households":
            continue
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({t})")}
        if "household_id" in cols:
            household_scoped_tables.append(t)

    print("\nCurrent data for this household:")
    for t in household_scoped_tables:
        count = conn.execute(f"SELECT COUNT(*) AS c FROM {t} WHERE household_id = ?", (HOUSEHOLD_ID,)).fetchone()["c"]
        if count:
            print(f"  {t}: {count} row(s)")

    print(
        "\nThis will permanently delete ALL of the above for household_id="
        f"{HOUSEHOLD_ID} and reset household goals — there's no undo."
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
        conn.execute(f"DELETE FROM {t} WHERE household_id = ?", (HOUSEHOLD_ID,))
    # Keep the households row itself (HOUSEHOLD_ID=1 is expected to exist
    # everywhere in the app), just reset its freeform goals field.
    conn.execute("UPDATE households SET goals = '' WHERE id = ?", (HOUSEHOLD_ID,))
    conn.commit()
    conn.close()

    print(
        "\nDone. The app now has zero household members, so the next visit to "
        "the chat page will auto-redirect to /onboarding — the redesigned "
        "first-plan reveal included."
    )


if __name__ == "__main__":
    main()
