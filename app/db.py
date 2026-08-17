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
]


def _run_migrations(conn):
    for table, column, coltype in _MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


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
