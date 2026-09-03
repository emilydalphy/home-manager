"""SQLite connection helper."""
import sqlite3
import os

# Overridable via env var so hosting platforms can point this at a
# persistent volume (their default filesystem is often ephemeral and
# wipes on every redeploy/restart). e.g. DB_PATH=/data/home_manager.db
DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(os.path.dirname(__file__), "home_manager.db")
)
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Lightweight migrations for columns added after the initial schema, so
# existing local databases pick them up without deleting the file.
_MIGRATIONS = [
    ("chores", "category", "TEXT NOT NULL DEFAULT 'cleaning'"),
    ("chores", "rotation_member_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("members", "dietary_restrictions_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("recipes", "tags_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("recipes", "times_cooked", "INTEGER NOT NULL DEFAULT 0"),
    ("recipes", "last_cooked_date", "TEXT"),
    ("households", "goals", "TEXT NOT NULL DEFAULT ''"),
    ("members", "age_group", "TEXT NOT NULL DEFAULT ''"),
    ("recipes", "food_groups_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("meal_plan_entries", "food_groups_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("meal_preferences", "protein_preferences_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("meal_preferences", "cuisine_preferences_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("meal_preferences", "cooking_time_preference", "TEXT NOT NULL DEFAULT ''"),
    ("meal_preferences", "dislikes_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("recipes", "rating", "TEXT NOT NULL DEFAULT ''"),
    ("recipes", "feedback_notes", "TEXT NOT NULL DEFAULT ''"),
    ("recipes", "cuisine", "TEXT NOT NULL DEFAULT ''"),
    ("recipes", "main_protein", "TEXT NOT NULL DEFAULT ''"),
    ("meal_plan_entries", "weekly_plan_id", "INTEGER"),
    ("grocery_items", "source_weekly_plan_id", "INTEGER"),
    ("meal_preferences", "novelty_preference", "TEXT NOT NULL DEFAULT 'balanced'"),
    ("recipes", "temporarily_excluded", "INTEGER NOT NULL DEFAULT 0"),
    ("meal_preferences", "planning_mode", "TEXT NOT NULL DEFAULT 'day_based'"),
    ("weekly_plans", "planning_mode", "TEXT NOT NULL DEFAULT 'day_based'"),
    ("meal_plan_entries", "component_category", "TEXT"),
    ("recipes", "instructions_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("recipes", "default_servings", "INTEGER NOT NULL DEFAULT 4"),
    ("recipes", "prep_time_minutes", "INTEGER"),
    ("recipes", "cook_time_minutes", "INTEGER"),
    ("recipes", "advance_prep_notes", "TEXT NOT NULL DEFAULT ''"),
    ("meal_plan_entries", "cooked_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("meal_plan_entries", "cooked_at", "TEXT"),
    ("grocery_items", "store", "TEXT NOT NULL DEFAULT ''"),
    ("inventory_items", "category", "TEXT NOT NULL DEFAULT 'other'"),
    ("inventory_items", "location", "TEXT NOT NULL DEFAULT ''"),
    ("grocery_items", "excluded_from_list", "INTEGER NOT NULL DEFAULT 0"),
    ("grocery_items", "already_have_reviewed", "INTEGER NOT NULL DEFAULT 0"),
    ("meal_preferences", "usual_stores_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("meal_preferences", "store_typical_items_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("recipes", "advance_prep_step_indices_json", "TEXT NOT NULL DEFAULT '[]'"),
    # Phase 6: set explicitly at creation time (an atomic "does this household
    # have zero prior plans yet?" check at the moment of insert), never
    # inferred later by querying for the earliest plan row — see
    # create_weekly_plan. Powers the onboarding "here's your first week"
    # intro banner.
    ("weekly_plans", "is_first_plan", "INTEGER NOT NULL DEFAULT 0"),
    # Phase 6: short "why this?" rationale per planned meal, generated and
    # persisted at plan-generation time — see schema.sql's comment on
    # meal_plan_entries.reasoning.
    ("meal_plan_entries", "reasoning", "TEXT NOT NULL DEFAULT ''"),
    # Onboarding redesign: free-text eating style/goal, household-level.
    ("meal_preferences", "eating_style", "TEXT NOT NULL DEFAULT ''"),
    # Onboarding redesign: how many dinners a typical week actually plans.
    ("meal_preferences", "dinners_per_week", "INTEGER NOT NULL DEFAULT 7"),
    ("meal_preferences", "breakfasts_per_week", "INTEGER NOT NULL DEFAULT 7"),
    ("meal_preferences", "lunches_per_week", "INTEGER NOT NULL DEFAULT 7"),
    # design_handoff_home_manager Phase 2: adult avatar color for "who
    # added it" on desktop grocery rows. Backfilled once, below, the same
    # run this column is first added.
    ("members", "color", "TEXT NOT NULL DEFAULT ''"),
    # PRE_SHOP_CHECK.md's "Drop it" — soft-remove attribution, see
    # schema.sql's comment on grocery_items.removed_by.
    ("grocery_items", "removed_by", "TEXT NOT NULL DEFAULT ''"),
    # design_handoff_plan_the_week: who approved the week and when — the
    # two fields the approved receipt renders. See schema.sql's comment on
    # weekly_plans.approved_by for why this is a name, not a member id.
    ("weekly_plans", "approved_by", "TEXT NOT NULL DEFAULT ''"),
    ("weekly_plans", "approved_at", "TEXT"),
    # What the approval did to the shopping list, so the receipt survives a
    # reload — see schema.sql's comment on approved_grocery_added.
    ("weekly_plans", "approved_grocery_added", "INTEGER NOT NULL DEFAULT 0"),
    ("weekly_plans", "approved_grocery_skipped", "INTEGER NOT NULL DEFAULT 0"),
    # design_handoff_plan_the_week/DATA_MODEL.md: the intake a plan came
    # from, and the per-slot state + provenance. See schema.sql's comments
    # on week_intake and meal_plan_entries.slot_state for what each is for.
    # No REFERENCES clause on intake_id here: SQLite cannot add a foreign
    # key via ALTER TABLE, so an existing database gets the plain column and
    # a newly-created one gets the constrained version from schema.sql.
    ("weekly_plans", "intake_id", "INTEGER"),
    ("meal_plan_entries", "slot_state", "TEXT NOT NULL DEFAULT 'planned'"),
    ("meal_plan_entries", "open_reason", "TEXT NOT NULL DEFAULT ''"),
    ("meal_plan_entries", "derived_from_json", "TEXT NOT NULL DEFAULT '{}'"),
    # The preference fields the revisitable setup screen owns and the two
    # onboarding steps collect — see schema.sql on meal_preferences.
    ("meal_preferences", "kitchen_kit_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("meal_preferences", "repeats_tolerance", "TEXT NOT NULL DEFAULT ''"),
    ("meal_preferences", "weeknight_max_minutes", "INTEGER NOT NULL DEFAULT 0"),
    ("meal_preferences", "table_style", "TEXT NOT NULL DEFAULT ''"),
    ("meal_preferences", "typical_week", "TEXT NOT NULL DEFAULT ''"),
    ("meal_preferences", "next_week_notes", "TEXT NOT NULL DEFAULT ''"),
    # When this household last made an authenticated request. Nothing else
    # answers "are they still using it": every other timestamp records a
    # household *doing* something (planning, cooking, shopping), so a
    # household that signs in daily and only reads their week looks
    # identical to one that has quietly stopped. That's the single most
    # important beta signal and the one testers never self-report.
    # Touched in security._call_as_household — see there for why it isn't
    # a write on every single request.
    ("households", "last_active_at", "TEXT"),
]

# First two adults (by id, i.e. creation order) get the household's two people
# colors. Repainted for Pomona (2026-09-02): spruce and deep apricot, the two
# most distinguishable anchors in the palette — previously plum #66304E and
# leaf green #4D8A33, both from the retired palette. Any adult beyond a second
# gets no color (the design only names two); a household with no adults marked
# yet (age_group not set to "adult") gets none either — it just runs again
# harmlessly next startup once someone is marked adult, since it only touches
# blank colors.
#
# NOTE: this only colors adults whose color is still blank. Members already
# carrying an old-palette color keep it until someone deliberately clears it —
# there is no migration here on purpose, because overwriting a color a
# household may have chosen is not a repaint's business.
_ADULT_COLORS = ["#1B3328", "#C4703C"]


def _backfill_member_colors(conn):
    # Per household, not globally. The two colors are "the first adult" and
    # "the second adult" *of a household* — a single global ORDER BY id
    # would hand both to whichever household was created first and leave
    # every later household's adults colorless. This ran unscoped while
    # there was only ever one household, which hid the bug completely.
    households = conn.execute("SELECT id FROM households ORDER BY id ASC").fetchall()
    for household in households:
        rows = conn.execute(
            # LOWER(): members.age_group is documented as freeform ("adult",
            # "teen", "child"), and onboarding actually writes it capitalized
            # ("Adult"). An exact-match 'adult' therefore found nobody in the
            # real database — no adult ever got a color, and
            # tools.get_household_people (same comparison, same bug) returned an
            # empty list, so the desktop grocery identity switcher had no one in
            # it. Compare case-insensitively rather than trusting the casing.
            "SELECT id FROM members WHERE household_id = ? "
            "AND LOWER(TRIM(age_group)) = 'adult' AND (color IS NULL OR color = '') ORDER BY id ASC",
            (household["id"],),
        ).fetchall()
        # Colors already taken by this household's other adults, so a second
        # adult added later doesn't get handed the first one's color.
        taken = {
            r["color"]
            for r in conn.execute(
                "SELECT color FROM members WHERE household_id = ? AND color != ''",
                (household["id"],),
            ).fetchall()
        }
        available = [c for c in _ADULT_COLORS if c not in taken]
        for color, row in zip(available, rows):
            conn.execute("UPDATE members SET color = ? WHERE id = ?", (color, row["id"]))


def _merge_duplicate_item_store_preferences(conn):
    """
    One-time-per-duplicate cleanup, found by independent review
    (2026-09-03): item_store_preferences used to be keyed on exact
    lowercased text, so "paper towel" and "paper towels" could each get
    their own row even though the grocery list treats them as one item
    (see tools.grocery._merge_key) — a household could end up with an item
    "typical" at two different stores at once, and a future add would pick
    between the two rows non-deterministically. stores.set_item_store now
    writes by merge-key identity so this can't happen going forward; this
    cleans up any row pairs a database made before that fix. Idempotent
    and cheap: only acts on a household that actually has more than one
    row sharing a merge key, keeping the most recently created row (this
    feature's existing "most recent write wins" rule) and deleting the
    rest.
    """
    # Local import: app.db is imported by every app.tools module (for
    # get_conn), so importing app.tools.grocery back from here at module
    # load time would be circular. Safe as a call-time import instead,
    # same trick tools/stores.py and tools/preferences.py use on each
    # other for the same reason.
    from .tools.grocery import _merge_key

    rows = conn.execute(
        "SELECT id, household_id, item FROM item_store_preferences ORDER BY id"
    ).fetchall()
    groups: dict[tuple[int, str], list] = {}
    for row in rows:
        key = (row["household_id"], _merge_key(row["item"]))
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        if len(group) > 1:
            keep_id = max(r["id"] for r in group)
            for row in group:
                if row["id"] != keep_id:
                    conn.execute("DELETE FROM item_store_preferences WHERE id = ?", (row["id"],))


def _run_migrations(conn):
    for table, column, coltype in _MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    # Run every startup, not just when the column is first added — a
    # second adult marked later (or the age_group changed to "adult" after
    # the fact) should still pick up a color next time the app starts.
    # Idempotent: only ever touches rows whose color is still blank.
    _backfill_member_colors(conn)
    _merge_duplicate_item_store_preferences(conn)


def init_db():
    conn = get_conn()
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    _run_migrations(conn)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DB_PATH}")
