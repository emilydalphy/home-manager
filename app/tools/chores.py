"""
Chore definitions, their schedules, and the household's chores profile.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from ..db import get_conn
from ._shared import household_id, require_household_row, current_member


_FREQUENCY_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14, "monthly": 30, "quarterly": 91, "once": None}

# Who a chore belongs to — Loop Board "Chores v1: every chore has a chosen
# owner" (Emily, 2026-09-11). See schema.sql's comment on chores.mode.
#
#   owned    one named person, every time
#   shared   named people take turns (the round-robin the schedule always did)
#   whoever  nobody in particular — first to tick it
#
# Owned is the default: the point of the card is that the noticing and the
# doing sit with one person, so a chore only ends up shared or whoever
# because somebody said so.
MODES = ("owned", "shared", "whoever")
DEFAULT_MODE = "owned"


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


# --- who a chore belongs to ---------------------------------------------------

def _rotation_ids(row) -> list[int]:
    """The people named on a chore row, in turn order (owner first)."""
    try:
        ids = [i for i in json.loads(row["rotation_member_ids_json"] or "[]") if i is not None]
    except (TypeError, ValueError):
        ids = []
    if not ids and row["default_assignee_id"] is not None:
        ids = [row["default_assignee_id"]]
    return ids


def chore_mode(row) -> str:
    """
    A chore row's mode. A row the startup migration hasn't reached yet (or
    one written by an older build) still reads as '' — derive the answer
    from its rotation by the same rule the migration uses, so nothing
    depends on the backfill having run first.
    """
    mode = row["mode"] if "mode" in row.keys() else ""
    if mode in MODES:
        return mode
    people = _rotation_ids(row)
    if len(people) == 1:
        return "owned"
    return "shared" if people else "whoever"


def _first_name(name: str | None) -> str:
    return (name or "").strip().split(" ")[0] if name else ""


def _nobody_label(conn) -> str:
    """What a 'whoever' chore shows in place of a name."""
    adults = conn.execute(
        "SELECT COUNT(*) AS n FROM members WHERE household_id = ? AND LOWER(TRIM(age_group)) = 'adult'",
        (household_id(),),
    ).fetchone()["n"]
    return "either of you" if adults == 2 else "anyone"


def _names_by_id(conn) -> dict[int, str]:
    rows = conn.execute("SELECT id, name FROM members WHERE household_id = ?", (household_id(),)).fetchall()
    return {r["id"]: r["name"] for r in rows}


def _adult_ids(conn) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM members WHERE household_id = ? AND LOWER(TRIM(age_group)) = 'adult' ORDER BY id ASC",
        (household_id(),),
    ).fetchall()
    return [r["id"] for r in rows]


def _dedupe(ids: list[int]) -> list[int]:
    seen: set[int] = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _member_named(conn, name: str, *, what: str = "who did you mean") -> int | None:
    """
    An existing member by name: an exact match (case-insensitive), else the
    one person whose first name it is. Never creates anybody — a chore's
    owner is somebody already in the house, and a typo must not become a
    person (it did: "Vinneth" got a members row). Unknown or ambiguous
    raises a plain question the assistant can put to the household; blank
    is None.
    """
    key = (name or "").strip().lower()
    if not key:
        return None
    rows = conn.execute(
        "SELECT id, name FROM members WHERE household_id = ? ORDER BY id ASC", (household_id(),)
    ).fetchall()
    exact = [r["id"] for r in rows if (r["name"] or "").strip().lower() == key]
    if len(exact) == 1:
        return exact[0]
    first = [r["id"] for r in rows if _first_name(r["name"]).lower() == key]
    if len(first) == 1:
        return first[0]
    if len(exact) > 1 or len(first) > 1:
        raise ValueError(f"There's more than one {name.strip()} here — {what}?")
    raise ValueError(f"I don't know anyone called {name.strip()} here — {what}?")


def _people_pool(conn) -> list[int]:
    """
    Who to draw on when nobody was named: the rotation named in setup
    first, then the household's adults, then anyone at all (a household
    set up through chat may never have marked ages). A setup name that no
    longer matches a member is skipped, not invented.
    """
    profile = conn.execute(
        "SELECT rotation_members_json FROM chores_profile WHERE household_id = ?", (household_id(),)
    ).fetchone()
    try:
        names = json.loads(profile["rotation_members_json"]) if profile else []
    except (TypeError, ValueError):
        names = []
    ids = []
    for n in names:
        if not isinstance(n, str) or not n.strip():
            continue
        try:
            found = _member_named(conn, n)
        except ValueError:
            found = None
        if found is not None:
            ids.append(found)
    if ids:
        return _dedupe(ids)
    adults = _adult_ids(conn)
    if adults:
        return adults
    return [r["id"] for r in conn.execute(
        "SELECT id FROM members WHERE household_id = ? ORDER BY id ASC", (household_id(),)
    ).fetchall()]


def _inferred_owner(conn) -> int | None:
    """
    The one person a chore can safely be handed to without asking: the
    only adult in the household, or the only name in the setup rotation.
    With two or more candidates there is no honest guess, and the caller
    falls back to taking turns instead of picking one.
    """
    adults = _adult_ids(conn)
    if len(adults) == 1:
        return adults[0]
    pool = _people_pool(conn)
    if len(pool) == 1:
        return pool[0]
    return None


def _resolve_people(
    conn,
    mode: str | None,
    names: list[str] | None,
    *,
    existing: list[int] | None = None,
    strict: bool = False,
) -> tuple[str, list[int]]:
    """
    Turn what the caller said into (mode, member ids), filling in what they
    left out. `names` are the people named; `existing` is who is on the
    chore already (an update keeps them when the names weren't restated).

    - no mode: one name means owned, several mean shared, none means the
      default — owned, by the person that can be inferred (see
      _inferred_owner). When nobody can be, the chore is shared across the
      setup rotation rather than handed to a guess.
    - owned with no owner anywhere: `strict` (an explicit "make it owned")
      raises so the assistant asks whose it should be; otherwise the same
      shared fallback.
    - shared with fewer than two people named: filled from the setup
      rotation / the adults.
    """
    names = [n.strip() for n in (names or []) if n and n.strip()]
    ids = _dedupe([i for i in (_member_named(conn, n) for n in names) if i is not None])
    existing = existing or []
    if mode is not None and mode not in MODES:
        raise ValueError(f"'{mode}' isn't a way to own a chore. Use owned, shared or whoever.")

    if mode is None:
        if len(ids) == 1:
            mode = "owned"
        elif ids:
            mode = "shared"
        else:
            mode = DEFAULT_MODE

    if mode == "whoever":
        return "whoever", []

    if mode == "owned":
        if ids:
            return "owned", ids[:1]
        if len(existing) == 1:
            return "owned", existing
        owner = _inferred_owner(conn)
        if owner is not None:
            return "owned", [owner]
        if strict:
            raise ValueError("Whose should it be? Name the person who'll own it.")
        pool = _dedupe(existing + _people_pool(conn))
        if len(pool) >= 2:
            return "shared", pool
        return ("owned", pool[:1]) if pool else ("whoever", [])

    # shared
    if len(ids) >= 2:
        return "shared", ids
    if not ids and len(existing) >= 2:
        return "shared", existing
    pool = _dedupe(ids + existing + _people_pool(conn))
    if len(pool) >= 2:
        return "shared", pool
    return ("owned", pool[:1]) if pool else ("whoever", [])


def _write_people(conn, chore_id: int, mode: str, ids: list[int]) -> None:
    conn.execute(
        "UPDATE chores SET mode = ?, rotation_member_ids_json = ?, default_assignee_id = ? "
        "WHERE id = ? AND household_id = ?",
        (mode, json.dumps(ids), ids[0] if ids else None, chore_id, household_id()),
    )


def _next_in_turn(conn, chore_id: int, rotation: list[int]) -> int | None:
    """
    Who's up next on a shared chore: the person after whoever the latest
    scheduled instance went to, wrapping round. Nobody scheduled yet means
    the first name.
    """
    if not rotation:
        return None
    last = conn.execute(
        "SELECT assignee_id FROM chore_instances WHERE chore_id = ? AND household_id = ? "
        "ORDER BY due_date DESC, id DESC LIMIT 1",
        (chore_id, household_id()),
    ).fetchone()
    if last and last["assignee_id"] in rotation:
        return rotation[(rotation.index(last["assignee_id"]) + 1) % len(rotation)]
    return rotation[0]


def _up_this_time(conn, chore_id: int, rotation: list[int]) -> int | None:
    """
    Whose turn it is on a shared chore right now: the earliest instance
    still pending, or failing that the next person in turn.
    """
    pending = conn.execute(
        "SELECT assignee_id FROM chore_instances WHERE chore_id = ? AND household_id = ? AND status = 'pending' "
        "ORDER BY due_date ASC, id ASC LIMIT 1",
        (chore_id, household_id()),
    ).fetchone()
    if pending and pending["assignee_id"] is not None:
        return pending["assignee_id"]
    return _next_in_turn(conn, chore_id, rotation)


def _describe(conn, row, names_by_id: dict[int, str] | None = None) -> dict:
    """
    The owner fields every chore row carries on the way out — what the
    Plan | Chores rows and the Now card will show. `who_label` is the one
    to print: the owner's first name, whose turn it is on a shared chore,
    or "either of you" when it's nobody's in particular.
    """
    names_by_id = names_by_id if names_by_id is not None else _names_by_id(conn)
    mode = chore_mode(row)
    people = _rotation_ids(row)
    owner_id = people[0] if mode == "owned" and people else None
    up_next_id = _up_this_time(conn, row["id"], people) if mode == "shared" else None
    if mode == "owned":
        who = _first_name(names_by_id.get(owner_id))
    elif mode == "shared":
        who = _first_name(names_by_id.get(up_next_id))
    else:
        who = ""
    return {
        "mode": mode,
        "owner": names_by_id.get(owner_id) if owner_id is not None else None,
        "assignees": [names_by_id.get(i, "?") for i in people],
        "up_next": names_by_id.get(up_next_id) if up_next_id is not None else None,
        "who_label": who or _nobody_label(conn),
    }


def _reassign_pending(conn, chore_id: int) -> int:
    """
    After an owner change, the instances not yet done follow the new
    answer; the done ones are history and keep whoever did them. Shared
    picks up the turn order from the most recent done instance so the
    change doesn't hand the same person two in a row.
    """
    chore = conn.execute("SELECT * FROM chores WHERE id = ? AND household_id = ?", (chore_id, household_id())).fetchone()
    mode = chore_mode(chore)
    people = _rotation_ids(chore)
    pending = conn.execute(
        "SELECT id FROM chore_instances WHERE chore_id = ? AND household_id = ? AND status = 'pending' "
        "ORDER BY due_date ASC, id ASC",
        (chore_id, household_id()),
    ).fetchall()
    if mode == "shared" and people:
        # The turn continues after whoever actually DID the last one, not
        # whoever it was scheduled for — the doer is the fairness fact.
        last_done = conn.execute(
            "SELECT assignee_id, completed_by_member_id FROM chore_instances "
            "WHERE chore_id = ? AND household_id = ? AND status = 'done' "
            "ORDER BY due_date DESC, id DESC LIMIT 1",
            (chore_id, household_id()),
        ).fetchone()
        last_person = None
        if last_done:
            last_person = last_done["completed_by_member_id"]
            if last_person is None:
                last_person = last_done["assignee_id"]
        cursor = people.index(last_person) + 1 if last_person in people else 0
    for n, inst in enumerate(pending):
        if mode == "owned":
            assignee = people[0] if people else None
        elif mode == "shared" and people:
            assignee = people[(cursor + n) % len(people)]
        else:
            assignee = None
        conn.execute(
            "UPDATE chore_instances SET assignee_id = ? WHERE id = ? AND household_id = ?",
            (assignee, inst["id"], household_id()),
        )
    return len(pending)


def add_chore(
    name: str,
    frequency: str = "weekly",
    category: str = "cleaning",
    assignee_names: list[str] | None = None,
    mode: str | None = None,
    owner_name: str | None = None,
) -> dict:
    """
    Create a new recurring chore definition. Every chore has a chosen
    owner: `mode` is owned (one person, always — the default), shared
    (the named people take turns) or whoever (nobody in particular).
    `owner_name` names the owner; `assignee_names` names the people taking
    turns. With neither, the only adult (or the only person in the setup
    rotation) owns it; if there's no one obvious, it's shared across the
    people named in setup.
    """
    conn = get_conn()
    names = list(assignee_names or [])
    if owner_name and owner_name.strip():
        names = [owner_name] + [n for n in names if n.strip().lower() != owner_name.strip().lower()]
        if mode is None:
            mode = "owned"
    try:
        mode, ids = _resolve_people(conn, mode, names)
    except ValueError:
        conn.close()
        raise
    cur = conn.execute(
        "INSERT INTO chores (household_id, name, category, frequency, default_assignee_id, rotation_member_ids_json, mode) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (household_id(), name, category, frequency, ids[0] if ids else None, json.dumps(ids), mode),
    )
    conn.commit()
    chore_id = cur.lastrowid
    row = conn.execute("SELECT * FROM chores WHERE id = ? AND household_id = ?", (chore_id, household_id())).fetchone()
    described = _describe(conn, row)
    conn.close()
    return {
        "chore_id": chore_id,
        "name": name,
        "category": category,
        "frequency": frequency,
        **described,
    }


def list_chore_definitions(active_only: bool = True) -> list[dict]:
    """
    List the recurring chore templates themselves (not individual due-date
    instances): name, category, frequency, and who it belongs to — mode,
    owner, the people taking turns, and whose turn it is right now.
    """
    conn = get_conn()
    query = "SELECT * FROM chores c WHERE c.household_id = ?"
    if active_only:
        query += " AND c.active = 1"
    rows = conn.execute(query, (household_id(),)).fetchall()
    names_by_id = _names_by_id(conn)
    result = []
    for r in rows:
        result.append(
            {
                "id": r["id"],
                "name": r["name"],
                "category": r["category"],
                "frequency": r["frequency"],
                "active": bool(r["active"]),
                **_describe(conn, r, names_by_id),
            }
        )
    conn.close()
    return result


def update_chore(
    chore_id: int,
    frequency: str | None = None,
    category: str | None = None,
    assignee_names: list[str] | None = None,
    active: bool | None = None,
    mode: str | None = None,
    owner_name: str | None = None,
) -> dict:
    """
    Update an existing chore's frequency, category, who it belongs to, or
    active status. Changing the owner or mode moves every instance not yet
    done onto the new answer; done ones keep whoever did them.
    """
    conn = get_conn()
    require_household_row(conn, "chores", chore_id, label="chore")
    try:
        # Work out who first, before anything is written: a refused owner
        # change ("whose should it be?") must leave the row exactly as it
        # was, frequency included.
        people = None
        if mode is not None or owner_name is not None or assignee_names is not None:
            row = conn.execute(
                "SELECT * FROM chores WHERE id = ? AND household_id = ?", (chore_id, household_id())
            ).fetchone()
            names = list(assignee_names or [])
            if owner_name and owner_name.strip():
                names = [owner_name] + [n for n in names if n.strip().lower() != owner_name.strip().lower()]
                if mode is None:
                    mode = "owned"
            elif mode is None and assignee_names is not None and not names:
                # An explicit empty list is what "nobody's" looked like before
                # mode existed; keep meaning that rather than re-deriving an owner.
                mode = "whoever"
            people = _resolve_people(conn, mode, names, existing=_rotation_ids(row), strict=mode == "owned")

        if frequency is not None:
            conn.execute("UPDATE chores SET frequency = ? WHERE id = ? AND household_id = ?", (frequency, chore_id, household_id()))
        if category is not None:
            conn.execute("UPDATE chores SET category = ? WHERE id = ? AND household_id = ?", (category, chore_id, household_id()))
        reassigned = None
        if people is not None:
            _write_people(conn, chore_id, *people)
            reassigned = _reassign_pending(conn, chore_id)
        if active is not None:
            conn.execute("UPDATE chores SET active = ? WHERE id = ? AND household_id = ?", (1 if active else 0, chore_id, household_id()))
        conn.commit()
        row = conn.execute("SELECT * FROM chores WHERE id = ? AND household_id = ?", (chore_id, household_id())).fetchone()
        described = _describe(conn, row)
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    result = {"chore_id": chore_id, "updated": True, "name": row["name"], **described}
    if reassigned is not None:
        result["upcoming_moved"] = reassigned
    return result


def generate_chore_schedule(days_ahead: int = 14) -> list[dict]:
    """
    Auto-generate upcoming chore instances for every active chore, out to
    `days_ahead`. Skips dates that already have a pending/done instance for
    that chore. Who each one goes to follows the chore's mode: owned goes
    to the owner every time, shared takes turns through the named people
    (continuing from whoever had it last), whoever goes to nobody. Call
    this after onboarding, and periodically (e.g. "generate this week's
    chores") to keep the schedule filled in.
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
        mode = chore_mode(chore)
        people = _rotation_ids(chore)

        last = conn.execute(
            "SELECT due_date, assignee_id FROM chore_instances WHERE chore_id = ? AND household_id = ? "
            "ORDER BY due_date DESC, id DESC LIMIT 1",
            (chore["id"], household_id()),
        ).fetchone()

        if last:
            next_due = date.fromisoformat(last["due_date"]) + timedelta(days=interval)
            last_assignee_id = last["assignee_id"]
        else:
            next_due = today
            last_assignee_id = None

        rotation_cursor = people.index(last_assignee_id) + 1 if last_assignee_id in people else 0

        while next_due <= horizon:
            exists = conn.execute(
                "SELECT id FROM chore_instances WHERE chore_id = ? AND household_id = ? AND due_date = ?",
                (chore["id"], household_id(), next_due.isoformat()),
            ).fetchone()
            if not exists:
                if mode == "owned":
                    assignee_id = people[0] if people else None
                elif mode == "shared" and people:
                    assignee_id = people[rotation_cursor % len(people)]
                    rotation_cursor += 1
                else:
                    assignee_id = None
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

    mode = chore_mode(chore)
    people = _rotation_ids(chore)
    if assignee_name:
        try:
            assignee_id = _member_named(conn, assignee_name)
        except ValueError:
            conn.close()
            raise
    elif mode == "owned":
        assignee_id = people[0] if people else None
    elif mode == "shared":
        assignee_id = _next_in_turn(conn, chore["id"], people)
    else:
        assignee_id = None

    cur = conn.execute(
        "INSERT INTO chore_instances (household_id, chore_id, assignee_id, due_date) VALUES (?, ?, ?, ?)",
        (household_id(), chore["id"], assignee_id, due_date),
    )
    conn.commit()
    instance_id = cur.lastrowid
    conn.close()
    return {"instance_id": instance_id, "chore": chore_name, "due_date": due_date}


_INSTANCE_SELECT = """
    SELECT ci.id, c.name AS chore, ci.due_date, ci.status,
           ci.assignee_id, m.name AS assignee,
           ci.completed_by_member_id, d.name AS completed_by,
           c.id AS chore_id, c.mode, c.rotation_member_ids_json, c.default_assignee_id
    FROM chore_instances ci
    JOIN chores c ON c.id = ci.chore_id
    LEFT JOIN members m ON m.id = ci.assignee_id
    LEFT JOIN members d ON d.id = ci.completed_by_member_id
"""


def _instance_dicts(conn, rows) -> list[dict]:
    """
    One instance per row, with who it's for spelled out: `assignee` is
    whose it was when scheduled, `completed_by` who ticked it, and
    `who_label` the name to print (first name, or "either of you").
    """
    nobody = None
    out = []
    for r in rows:
        # The chore's own columns ride along on the join, so chore_mode()
        # can read the row directly.
        row_mode = chore_mode(r)
        who = _first_name(r["assignee"])
        if not who:
            if nobody is None:
                nobody = _nobody_label(conn)
            who = nobody
        out.append({
            "id": r["id"],
            "chore": r["chore"],
            "chore_id": r["chore_id"],
            "due_date": r["due_date"],
            "status": r["status"],
            "assignee": r["assignee"],
            "mode": row_mode,
            "who_label": who,
            "completed_by": r["completed_by"],
        })
    return out


def list_chores(status: str = "pending", days_ahead: int = 14) -> list[dict]:
    """
    List chore instances, optionally filtered by status (pending/done/
    skipped/all). Each carries who it's for (assignee, who_label) and, once
    done, who actually did it (completed_by).
    """
    conn = get_conn()
    end_date = (date.today() + timedelta(days=days_ahead)).isoformat()
    rows = conn.execute(
        _INSTANCE_SELECT + """
        WHERE ci.household_id = ?
          AND (? = 'all' OR ci.status = ?)
          AND ci.due_date <= ?
        ORDER BY ci.due_date ASC
        """,
        (household_id(), status, status, end_date),
    ).fetchall()
    result = _instance_dicts(conn, rows)
    conn.close()
    return result


def _doer_id(conn, done_by: str | None) -> int | None:
    """
    Who gets credit for a tick: the person named, else the session's adult.
    A name that matches nobody is a question back to the household, never
    quietly the person who was talking — "Vinneth did the bins" with Emily
    signed in must not go down as Emily.
    """
    if done_by and done_by.strip():
        return _member_named(conn, done_by, what="who did it")
    member = current_member()
    return member["id"] if member else None


def complete_chore(instance_id: int, done_by: str | None = None) -> dict:
    """
    Mark a chore instance as done. `done_by` names who did it when it
    wasn't the signed-in adult ("Vineeth did the bins"); otherwise the
    session's adult gets the credit.
    """
    conn = get_conn()
    require_household_row(conn, "chore_instances", instance_id, label="chore instance")
    try:
        doer = _doer_id(conn, done_by)
    except ValueError:
        conn.close()
        raise
    conn.execute(
        "UPDATE chore_instances SET status = 'done', completed_at = datetime('now'), completed_by_member_id = ? "
        "WHERE id = ? AND household_id = ?",
        (doer, instance_id, household_id()),
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
        _INSTANCE_SELECT + """
        WHERE ci.household_id = ? AND ci.due_date = ? AND ci.status != 'skipped'
        ORDER BY ci.id ASC
        """,
        (household_id(), today),
    ).fetchall()
    result = _instance_dicts(conn, rows)
    conn.close()
    return result


def set_chore_instance_status(instance_id: int, status: str = "done") -> dict:
    """
    Mark a chore instance done or back to pending directly from the Today
    screen's chores card (no chat round-trip needed) — same shape as
    check_off_meal/check_off_prep_step below. A tick is credited to the
    session's adult (completed_by_member_id); un-ticking clears it.
    """
    conn = get_conn()
    require_household_row(conn, "chore_instances", instance_id, label="chore instance")
    if status == "done":
        conn.execute(
            "UPDATE chore_instances SET status = 'done', completed_at = datetime('now'), completed_by_member_id = ? "
            "WHERE id = ? AND household_id = ?",
            (_doer_id(conn, None), instance_id, household_id()),
        )
    else:
        conn.execute(
            "UPDATE chore_instances SET status = ?, completed_at = NULL, completed_by_member_id = NULL "
            "WHERE id = ? AND household_id = ?",
            (status, instance_id, household_id()),
        )
    conn.commit()
    conn.close()
    return {"instance_id": instance_id, "status": status}
