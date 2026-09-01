"""
Chore definitions, their schedules, and the household's chores profile.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from ..db import get_conn
from ._shared import household_id
from . import household as _household


_FREQUENCY_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14, "monthly": 30, "quarterly": 91, "once": None}


def set_chores_profile(
    home_type: str = "",
    bedrooms: int = 0,
    bathrooms: int = 0,
    has_yard: bool = False,
    standard: str = "",
    rotation_members: list[str] | None = None,
    existing_help: str = "",
    existing_help_frequency: str = "",
    include_notes: str = "",
    exclude_notes: str = "",
) -> dict:
    """
    Save household context for chores (home type/size, yard, cleanliness
    standard, who's in the rotation, existing help, notes) without creating
    any chores yet. Useful as a quick save of onboarding answers, or to
    record context conversationally before building the actual chore list
    with add_chore.
    """
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO chores_profile
            (household_id, home_type, bedrooms, bathrooms, has_yard, standard,
             rotation_members_json, existing_help, existing_help_frequency,
             include_notes, exclude_notes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(household_id) DO UPDATE SET
            home_type = excluded.home_type,
            bedrooms = excluded.bedrooms,
            bathrooms = excluded.bathrooms,
            has_yard = excluded.has_yard,
            standard = excluded.standard,
            rotation_members_json = excluded.rotation_members_json,
            existing_help = excluded.existing_help,
            existing_help_frequency = excluded.existing_help_frequency,
            include_notes = excluded.include_notes,
            exclude_notes = excluded.exclude_notes,
            updated_at = datetime('now')
        """,
        (
            household_id(), home_type, bedrooms, bathrooms, 1 if has_yard else 0, standard,
            json.dumps(rotation_members or []), existing_help, existing_help_frequency,
            include_notes, exclude_notes,
        ),
    )
    conn.commit()
    conn.close()
    return {"saved": True}


def get_chores_profile() -> dict:
    """Get saved chores context (home type, yard, standard, etc.), if any was recorded. Empty fields mean it wasn't collected yet."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM chores_profile WHERE household_id = ?", (household_id(),)
    ).fetchone()
    conn.close()
    if not row:
        return {"has_profile": False}
    return {
        "has_profile": True,
        "home_type": row["home_type"],
        "bedrooms": row["bedrooms"],
        "bathrooms": row["bathrooms"],
        "has_yard": bool(row["has_yard"]),
        "standard": row["standard"],
        "rotation_members": json.loads(row["rotation_members_json"]),
        "existing_help": row["existing_help"],
        "existing_help_frequency": row["existing_help_frequency"],
        "include_notes": row["include_notes"],
        "exclude_notes": row["exclude_notes"],
    }


def add_chore(
    name: str,
    frequency: str = "weekly",
    category: str = "cleaning",
    assignee_names: list[str] | None = None,
) -> dict:
    """
    Create a new recurring chore definition. `assignee_names` can be one
    name (always assigned to that person) or several (rotates round-robin
    each time the chore comes up). Leave empty for unassigned.
    """
    conn = get_conn()
    rotation_ids = []
    default_id = None
    if assignee_names:
        rotation_ids = [_household._get_or_create_member(conn, n) for n in assignee_names]
        default_id = rotation_ids[0]
    cur = conn.execute(
        "INSERT INTO chores (household_id, name, category, frequency, default_assignee_id, rotation_member_ids_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (household_id(), name, category, frequency, default_id, json.dumps(rotation_ids)),
    )
    conn.commit()
    chore_id = cur.lastrowid
    conn.close()
    return {
        "chore_id": chore_id,
        "name": name,
        "category": category,
        "frequency": frequency,
        "assignees": assignee_names or [],
    }


def list_chore_definitions(active_only: bool = True) -> list[dict]:
    """List the recurring chore templates themselves (not individual due-date instances)."""
    conn = get_conn()
    query = "SELECT c.id, c.name, c.category, c.frequency, c.active, c.rotation_member_ids_json FROM chores c WHERE c.household_id = ?"
    if active_only:
        query += " AND c.active = 1"
    rows = conn.execute(query, (household_id(),)).fetchall()
    member_rows = conn.execute("SELECT id, name FROM members WHERE household_id = ?", (household_id(),)).fetchall()
    names_by_id = {m["id"]: m["name"] for m in member_rows}
    conn.close()
    result = []
    for r in rows:
        rotation_ids = json.loads(r["rotation_member_ids_json"])
        result.append(
            {
                "id": r["id"],
                "name": r["name"],
                "category": r["category"],
                "frequency": r["frequency"],
                "active": bool(r["active"]),
                "assignees": [names_by_id.get(i, "?") for i in rotation_ids],
            }
        )
    return result


def update_chore(
    chore_id: int,
    frequency: str | None = None,
    category: str | None = None,
    assignee_names: list[str] | None = None,
    active: bool | None = None,
) -> dict:
    """Update an existing chore's frequency, category, assigned rotation, or active status."""
    conn = get_conn()
    if frequency is not None:
        conn.execute("UPDATE chores SET frequency = ? WHERE id = ? AND household_id = ?", (frequency, chore_id, household_id()))
    if category is not None:
        conn.execute("UPDATE chores SET category = ? WHERE id = ? AND household_id = ?", (category, chore_id, household_id()))
    if assignee_names is not None:
        rotation_ids = [_household._get_or_create_member(conn, n) for n in assignee_names]
        default_id = rotation_ids[0] if rotation_ids else None
        conn.execute(
            "UPDATE chores SET rotation_member_ids_json = ?, default_assignee_id = ? WHERE id = ? AND household_id = ?",
            (json.dumps(rotation_ids), default_id, chore_id, household_id()),
        )
    if active is not None:
        conn.execute("UPDATE chores SET active = ? WHERE id = ? AND household_id = ?", (1 if active else 0, chore_id, household_id()))
    conn.commit()
    conn.close()
    return {"chore_id": chore_id, "updated": True}


def generate_chore_schedule(days_ahead: int = 14) -> list[dict]:
    """
    Auto-generate upcoming chore instances for every active chore, out to
    `days_ahead`. Skips dates that already have a pending/done instance for
    that chore. Assigns round-robin across a chore's rotation members (or
    the single assignee if only one), continuing the rotation from whoever
    was assigned last time. Call this after onboarding, and periodically
    (e.g. "generate this week's chores") to keep the schedule filled in.
    """
    conn = get_conn()
    chores = conn.execute(
        "SELECT * FROM chores WHERE household_id = ? AND active = 1 AND frequency != 'once'", (household_id(),)
    ).fetchall()

    created = []
    today = date.today()
    horizon = today + timedelta(days=days_ahead)

    for chore in chores:
        interval = _FREQUENCY_DAYS.get(chore["frequency"], 7)
        rotation_ids = json.loads(chore["rotation_member_ids_json"])

        last = conn.execute(
            "SELECT due_date, assignee_id FROM chore_instances WHERE chore_id = ? ORDER BY due_date DESC LIMIT 1",
            (chore["id"],),
        ).fetchone()

        if last:
            next_due = date.fromisoformat(last["due_date"]) + timedelta(days=interval)
            last_assignee_id = last["assignee_id"]
        else:
            next_due = today
            last_assignee_id = None

        rotation_cursor = rotation_ids.index(last_assignee_id) + 1 if last_assignee_id in rotation_ids else 0

        while next_due <= horizon:
            exists = conn.execute(
                "SELECT id FROM chore_instances WHERE chore_id = ? AND due_date = ?",
                (chore["id"], next_due.isoformat()),
            ).fetchone()
            if not exists:
                if rotation_ids:
                    assignee_id = rotation_ids[rotation_cursor % len(rotation_ids)]
                    rotation_cursor += 1
                else:
                    assignee_id = chore["default_assignee_id"]
                cur = conn.execute(
                    "INSERT INTO chore_instances (household_id, chore_id, assignee_id, due_date) VALUES (?, ?, ?, ?)",
                    (household_id(), chore["id"], assignee_id, next_due.isoformat()),
                )
                created.append({"chore": chore["name"], "due_date": next_due.isoformat(), "instance_id": cur.lastrowid})
            next_due += timedelta(days=interval)

    conn.commit()
    conn.close()
    return created


def schedule_chore_instance(chore_name: str, due_date: str, assignee_name: str | None = None) -> dict:
    """Schedule a one-off occurrence of a chore for a specific date (YYYY-MM-DD)."""
    conn = get_conn()
    chore = conn.execute(
        "SELECT * FROM chores WHERE household_id = ? AND name = ?", (household_id(), chore_name)
    ).fetchone()
    if not chore:
        conn.close()
        raise ValueError(f"No chore named '{chore_name}'. Create it first with add_chore.")

    assignee_id = chore["default_assignee_id"]
    if assignee_name:
        assignee_id = _household._get_or_create_member(conn, assignee_name)

    cur = conn.execute(
        "INSERT INTO chore_instances (household_id, chore_id, assignee_id, due_date) VALUES (?, ?, ?, ?)",
        (household_id(), chore["id"], assignee_id, due_date),
    )
    conn.commit()
    instance_id = cur.lastrowid
    conn.close()
    return {"instance_id": instance_id, "chore": chore_name, "due_date": due_date}


def list_chores(status: str = "pending", days_ahead: int = 14) -> list[dict]:
    """List chore instances, optionally filtered by status (pending/done/skipped)."""
    conn = get_conn()
    end_date = (date.today() + timedelta(days=days_ahead)).isoformat()
    rows = conn.execute(
        """
        SELECT ci.id, c.name AS chore, ci.due_date, ci.status, m.name AS assignee
        FROM chore_instances ci
        JOIN chores c ON c.id = ci.chore_id
        LEFT JOIN members m ON m.id = ci.assignee_id
        WHERE ci.household_id = ?
          AND (? = 'all' OR ci.status = ?)
          AND ci.due_date <= ?
        ORDER BY ci.due_date ASC
        """,
        (household_id(), status, status, end_date),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def complete_chore(instance_id: int) -> dict:
    """Mark a chore instance as done."""
    conn = get_conn()
    conn.execute(
        "UPDATE chore_instances SET status = 'done', completed_at = datetime('now') WHERE id = ? AND household_id = ?",
        (instance_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"instance_id": instance_id, "status": "done"}


def get_chores_due_today() -> list[dict]:
    """
    Chore instances due today — powers the app-shell Today screen's chores
    card (design_handoff_shell/README.md §4). Includes both pending and
    done instances (not just pending) so the UI can show an accurate
    "x of y done" count rather than only the still-open ones.

    Household-wide, not filtered to a signed-in member: there's no
    per-user login concept yet (see household_id() above), so this can't
    actually distinguish "my chores" from anyone else's the way the
    redesign's Today spec describes. Noted as a known gap in the README
    rather than silently faked.
    """
    conn = get_conn()
    today = date.today().isoformat()
    rows = conn.execute(
        """
        SELECT ci.id, c.name AS chore, ci.due_date, ci.status, m.name AS assignee
        FROM chore_instances ci
        JOIN chores c ON c.id = ci.chore_id
        LEFT JOIN members m ON m.id = ci.assignee_id
        WHERE ci.household_id = ? AND ci.due_date = ? AND ci.status != 'skipped'
        ORDER BY ci.id ASC
        """,
        (household_id(), today),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_chore_instance_status(instance_id: int, status: str = "done") -> dict:
    """
    Mark a chore instance done or back to pending directly from the Today
    screen's chores card (no chat round-trip needed) — same shape as
    check_off_meal/check_off_prep_step below.
    """
    conn = get_conn()
    completed_at_sql = "datetime('now')" if status == "done" else "NULL"
    conn.execute(
        f"UPDATE chore_instances SET status = ?, completed_at = {completed_at_sql} WHERE id = ? AND household_id = ?",
        (status, instance_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"instance_id": instance_id, "status": status}
