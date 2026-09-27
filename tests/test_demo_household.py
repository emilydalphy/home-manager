"""
create_demo_household.py: the App Store review household.

Three things this must never get wrong, so each gets its own section:
  - it must never create or touch household 1 (Emily's real household),
  - running it twice must not duplicate anything, and
  - the demo household must be exactly as isolated from other households
    as any two real households are (test_multi_household.py's own bar).
"""
import os

import pytest

import create_demo_household as demo
from app import households, tools
from app.db import get_conn
from app.tools._shared import DEFAULT_HOUSEHOLD_ID, use_household


PASSPHRASE = "demo-review-passphrase-123"


@pytest.fixture(autouse=True)
def _demo_passphrase_env(monkeypatch):
    monkeypatch.setenv(demo.DEMO_PASSPHRASE_ENV, PASSPHRASE)


def _counts_for(table: str, household_id: int) -> int:
    conn = get_conn()
    n = conn.execute(
        f"SELECT COUNT(*) AS c FROM {table} WHERE household_id = ?", (household_id,)
    ).fetchone()["c"]
    conn.close()
    return n


# ---------- building the household ----------

def test_first_run_creates_the_demo_household():
    demo.main()
    found = demo._find_demo_household()
    assert found is not None
    assert found["name"] == demo.DEMO_HOUSEHOLD_NAME
    assert found["id"] != DEFAULT_HOUSEHOLD_ID


def test_demo_household_has_believable_data():
    demo.main()
    found = demo._find_demo_household()
    hid = found["id"]
    with use_household(hid):
        members = tools.list_members()
        assert len(members) == 2
        assert set(demo._ADULT_NAMES) == {m["name"] for m in members}

        recipes = tools.list_recipes()
        assert len(recipes) >= 2

        grocery = tools.list_grocery_list()
        assert len(grocery) > 0

        plan = tools.get_weekly_plan()
        assert plan is not None
        assert plan.get("status") == "approved"


def test_passphrase_signs_into_the_demo_household_not_household_1():
    demo.main()
    found = demo._find_demo_household()
    assert households.resolve_passphrase(PASSPHRASE) == found["id"]
    assert found["id"] != DEFAULT_HOUSEHOLD_ID


# ---------- idempotency ----------

def test_running_twice_does_not_duplicate_the_household():
    demo.main()
    first = demo._find_demo_household()
    demo.main()
    second = demo._find_demo_household()
    assert first["id"] == second["id"], "a second run must reuse the same household, not create another"

    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM households WHERE name = ?", (demo.DEMO_HOUSEHOLD_NAME,)
    ).fetchone()["c"]
    conn.close()
    assert n == 1


def test_running_twice_does_not_duplicate_its_data():
    demo.main()
    found = demo._find_demo_household()
    hid = found["id"]

    demo.main()

    assert _counts_for("members", hid) == 2
    assert _counts_for("recipes", hid) == 3
    assert _counts_for("weekly_plans", hid) == 1
    # A fresh grocery list is rebuilt from the same recipes each time, not
    # appended to what was already there.
    with use_household(hid):
        grocery_after_two_runs = len(tools.list_grocery_list())
    demo.main()
    with use_household(hid):
        grocery_after_three_runs = len(tools.list_grocery_list())
    assert grocery_after_two_runs == grocery_after_three_runs


def test_running_twice_rotates_the_passphrase_if_it_changed(monkeypatch):
    demo.main()
    found = demo._find_demo_household()

    monkeypatch.setenv(demo.DEMO_PASSPHRASE_ENV, "a-brand-new-passphrase-456")
    demo.main()

    assert households.resolve_passphrase("a-brand-new-passphrase-456") == found["id"]
    assert households.resolve_passphrase(PASSPHRASE) is None


# ---------- never household 1 ----------

def test_household_1_is_never_created_or_touched():
    conn = get_conn()
    before_members = conn.execute(
        "SELECT COUNT(*) AS c FROM members WHERE household_id = ?", (DEFAULT_HOUSEHOLD_ID,)
    ).fetchone()["c"]
    conn.close()
    assert before_members == 0, "test isolation assumption: household 1 starts empty"

    demo.main()
    demo.main()  # and again, for good measure

    conn = get_conn()
    after_members = conn.execute(
        "SELECT COUNT(*) AS c FROM members WHERE household_id = ?", (DEFAULT_HOUSEHOLD_ID,)
    ).fetchone()["c"]
    after_recipes = conn.execute(
        "SELECT COUNT(*) AS c FROM recipes WHERE household_id = ?", (DEFAULT_HOUSEHOLD_ID,)
    ).fetchone()["c"]
    conn.close()
    assert after_members == 0
    assert after_recipes == 0
    assert households.resolve_passphrase(PASSPHRASE) != DEFAULT_HOUSEHOLD_ID


def test_refuses_household_1_even_if_asked_directly():
    with pytest.raises(SystemExit):
        demo._refuse_unless_demo_household(DEFAULT_HOUSEHOLD_ID, "My Household")


def test_refuses_a_household_with_the_wrong_name():
    """
    Guards the id-1 check's sibling: even a non-default household is
    refused unless its name is exactly DEMO_HOUSEHOLD_NAME, so nothing
    downstream can be pointed at the wrong household by a stale id.
    """
    other_id = households.create_household("Some Beta Tester", "unrelated-passphrase-xyz")
    with pytest.raises(SystemExit):
        demo._refuse_unless_demo_household(other_id, "Some Beta Tester")


def test_a_second_household_with_a_different_name_is_left_alone():
    other_id = households.create_household("The Beta Testers", "beta-passphrase-abc")
    with use_household(other_id):
        tools.add_member("Riley")

    demo.main()

    with use_household(other_id):
        members = tools.list_members()
    assert [m["name"] for m in members] == ["Riley"]


# ---------- isolation from other households (the multi-household bar) ----------

def test_demo_household_cannot_see_another_households_data():
    other_id = households.create_household("The Beta Testers", "beta-passphrase-def")
    with use_household(other_id):
        tools.add_member("Riley")
        tools.add_recipe("Beta-only Bake", ingredients=[{"item": "flour", "qty": "1 cup"}])

    demo.main()
    demo_id = demo._find_demo_household()["id"]

    with use_household(demo_id):
        demo_member_names = {m["name"] for m in tools.list_members()}
        demo_recipe_names = {r["name"] for r in tools.list_recipes()}

    assert "Riley" not in demo_member_names
    assert "Beta-only Bake" not in demo_recipe_names


def test_another_household_cannot_see_the_demo_households_data():
    demo.main()
    demo_id = demo._find_demo_household()["id"]

    other_id = households.create_household("The Beta Testers", "beta-passphrase-ghi")
    with use_household(other_id):
        other_member_names = {m["name"] for m in tools.list_members()}
        other_recipe_names = {r["name"] for r in tools.list_recipes()}

    assert not (set(demo._ADULT_NAMES) & other_member_names)
    assert "Chicken Stir Fry" not in other_recipe_names
    assert demo_id != other_id


# ---------- the passphrase source ----------

def test_refuses_to_run_with_no_passphrase_set(monkeypatch):
    monkeypatch.delenv(demo.DEMO_PASSPHRASE_ENV, raising=False)
    with pytest.raises(SystemExit):
        demo.main()
    assert demo._find_demo_household() is None


def test_passphrase_is_not_hardcoded_in_the_script():
    source = open(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "create_demo_household.py")
    ).read()
    # The only literal passphrase-shaped strings in the file should be in
    # docstrings/usage examples, never assigned as the real value — the
    # real value only ever comes from os.environ.
    assert "os.environ.get(DEMO_PASSPHRASE_ENV" in source
    assert 'DEMO_PASSPHRASE = "' not in source
