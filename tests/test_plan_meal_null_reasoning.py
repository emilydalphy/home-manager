"""
A generated day whose JSON says "reasoning": null is planned with no "why",
not a NOT NULL failure that stops the week halfway with the household's
write lock held (census audit, 2026-10-21).
"""
import sqlite3

from app import db, tools
from conftest import household_date


def test_a_null_reasoning_plans_the_meal_and_frees_the_lock():
    plan_id = tools.create_weekly_plan(household_date(0))["weekly_plan_id"]
    tools.plan_meal(
        meal_date=household_date(1), meal="Lentil Soup", slot="dinner",
        weekly_plan_id=plan_id, reasoning=None,
    )
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT reasoning FROM meal_plan_entries WHERE weekly_plan_id = ?",
            (plan_id,),
        ).fetchall()
    finally:
        conn.close()
    assert [r["reasoning"] for r in rows] == [""]

    other = sqlite3.connect(db.DB_PATH, timeout=0.1)
    try:
        other.execute("BEGIN IMMEDIATE")  # "database is locked" if anyone still holds it
        other.rollback()
    finally:
        other.close()
