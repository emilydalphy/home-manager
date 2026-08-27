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
]

# First two adults (by id, i.e. creation order) get the exact two colors
# design_handoff_home_manager's README specifies for the household's people
# token — Emily #66304E (the household's own plum), Marcus #4D8A33 (the
# household's own leaf green). Any adult beyond a second gets no color (the
# design only names two); a household with no adults marked yet (age_group
# not set to "adult") gets none either — it just runs again harmlessly next
# startup once someone is marked adult, since it only touches blank colors.
_ADULT_COLORS = ["#66304E", "#4D8A33"]


def _backfill_member_colors(conn):
    rows = conn.execute(
        "SELECT id FROM members WHERE age_group = 'adult' AND (color IS NULL OR color = '') ORDER BY id ASC"
    ).fetchall()
    for i, row in enumerate(rows):
        if i >= len(_ADULT_COLORS):
            break
        conn.execute("UPDATE members SET color = ? WHERE id = ?", (_ADULT_COLORS[i], row["id"]))


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
