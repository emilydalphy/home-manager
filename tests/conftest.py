"""
Shared test setup.

Every test runs against a throwaway SQLite file, never the real database.
app/db.py reads DB_PATH at import time, so the env var has to be set before
anything under app/ is imported — hence the os.environ writes at module
scope here, above the imports that depend on them.
"""
import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="home-manager-tests-"), "test.db")
os.environ["DB_PATH"] = _TMP_DB
os.environ["HOME_MANAGER_PASSWORD"] = "test-password"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ratelimit  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.main import app  # noqa: E402

# Tables are wiped between tests rather than the file being recreated, so
# the schema and migrations run once and each test still starts clean.
_TABLES = [
    # week_intake comes after weekly_plans: a plan references the intake it
    # was generated from, so the referencing rows go first.
    "meal_plan_grocery_links", "prep_tasks", "meal_plan_entries", "weekly_plans", "week_intake",
    "grocery_items", "inventory_items", "recipe_notes", "recipes",
    "chore_instances", "chores", "chores_profile", "attention_items",
    "member_notes", "member_share_links", "share_links", "facts",
    "preference_events", "notification_dismissals", "item_store_preferences",
    "shopping_trips", "stores", "meal_preferences", "pets", "members",
]


@pytest.fixture(scope="session", autouse=True)
def _database():
    init_db()


@pytest.fixture(autouse=True)
def clean_state():
    ratelimit.reset()
    conn = get_conn()
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in _TABLES:
        try:
            conn.execute(f"DELETE FROM {table}")
        except Exception:
            pass  # a table this build doesn't have yet is not a test failure
    # Households themselves are wiped too, except the seeded id=1 that
    # schema.sql creates and every existing test implicitly runs against.
    # Without this, a test that creates a second household leaks it into
    # every test that runs after it in the session — and an isolation test
    # that silently shares state with its neighbours is worse than none,
    # because it still passes.
    try:
        conn.execute("DELETE FROM household_credentials WHERE household_id != 1")
        conn.execute("DELETE FROM households WHERE id != 1")
    except Exception:
        pass
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    conn.close()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def signed_in(client):
    res = client.post(
        "/login",
        data={"password": "test-password", "next": "/"},
        follow_redirects=False,
    )
    assert res.status_code == 303, "sign-in should redirect on success"
    return client
