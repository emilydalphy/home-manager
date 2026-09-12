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
#   owned      one named person, every time
#   shared     named people take turns (the round-robin the schedule always did)
#   whoever    nobody in particular — first to tick it
#   outsourced somebody outside the house does it — a cleaner, a lawn
#              service, a laundry pickup
#
# Owned is the default: the point of that card is that the noticing and the
# doing sit with one person, so a chore only ends up shared or whoever
# because somebody said so.
#
# Outsourced is the fourth (Loop Board "Chores v1: tag a chore as
# outsourced"). It looks like 'whoever' underneath — nobody named, no
# assignee on its instances — and it means the opposite: a 'whoever' chore
# is still ours to do and this one is not. So it is a mode rather than a
# flag on that one, and everywhere the app asks "whose is this?" it gets a
# fourth answer instead of a blank. It keeps its frequency and still shows
# on its day (we know Thursday is cleaner day); it carries no tick, and it
# counts for nobody.
MODES = ("owned", "shared", "whoever", "outsourced")
DEFAULT_MODE = "owned"

# What an outsourced row says at the end when the household didn't name
# who does it. "Someone else" rather than the word "outsourced": the tag
# is for us, the row is read by a person standing in their kitchen.
NOBODY_IN_THE_HOUSE = "someone else"

# What an owned chore with nobody on it says — the shape left behind by
# handing an outsourced chore back (see update_chore). "Your call" is
# already this app's words for a decision handed back to the household:
# it is what an open meal slot prints on Meals. Not "either of you",
# which is the label for a chore the house has DECIDED is nobody's in
# particular — the opposite of a question nobody has answered yet.
UNCLAIMED = "Your call"


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


def is_outsourced(row) -> bool:
    """
    True when somebody outside the house does this chore. The one question
    to ask before counting a chore toward anybody — the fairness view and
    any effort total must skip these, because they are nobody's load.
    Takes a chores row or a chore_instances row joined to one (both carry
    `mode`), so there is one answer rather than two that can disagree.
    """
    return chore_mode(row) == "outsourced"


def _outsourced_label(row) -> str:
    """Who does it, when it isn't us. '' unless the row is outsourced."""
    if not is_outsourced(row):
        return ""
    return (row["outsourced_to"] if "outsourced_to" in row.keys() else "") or ""


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
    - outsourced: nobody in the house, whoever was named or not. A name
      passed alongside it is somebody outside the household (a cleaner
      isn't a member), so it is NOT resolved against members — it rides in
      `outsourced_to` instead, which is the caller's job to pass on.
    """
    names = [n.strip() for n in (names or []) if n and n.strip()]
    existing = existing or []
    if mode is not None and mode not in MODES:
        raise ValueError(
            f"'{mode}' isn't a way to own a chore. Use owned, shared, whoever or outsourced."
        )
    # Before any name is looked up: an outsourced chore is nobody in the
    # house's, and the person who does it is not a member — resolving
    # "Maria" here would ask the household who Maria is when they have
    # just finished telling us she doesn't live here.
    if mode == "outsourced":
        return "outsourced", []
    ids = _dedupe([i for i in (_member_named(conn, n) for n in names) if i is not None])

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


def _write_people(conn, chore_id: int, mode: str, ids: list[int], outsourced_to: str = "") -> None:
    # outsourced_to is written on EVERY call, not only the outsourced ones:
    # the label is only ever true while the tag is, so handing the chore
    # back to the house clears it in the same statement that changes the
    # mode. A label left behind would print a cleaner's name beside a chore
    # somebody in the house had just taken back.
    label = (outsourced_to or "").strip() if mode == "outsourced" else ""
    conn.execute(
        "UPDATE chores SET mode = ?, rotation_member_ids_json = ?, default_assignee_id = ?, outsourced_to = ? "
        "WHERE id = ? AND household_id = ?",
        (mode, json.dumps(ids), ids[0] if ids else None, label, chore_id, household_id()),
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
    Whose turn it is on a shared chore right now — and it has to be the
    turn on the SAME occurrence the screens show, or the Now card says
    Jamie while list_chore_definitions says Emily about one chore at one
    moment. That was a real disagreement introduced by the no-guilt-pile
    collapse, which picks the LATEST slipped occurrence while this picked
    the earliest.

    So the answer is _collapse_outstanding's representative: the latest
    occurrence due on or before today. A backlog collapses into the one
    job that is actually owed, and the turns before it are written off
    rather than queued up behind it — the whole point of the card is that
    a bad month does not leave four turns outstanding. Failing that it is
    the next occurrence ahead, and failing that whoever is next in turn.
    """
    today = date.today().isoformat()
    owed = conn.execute(
        "SELECT assignee_id FROM chore_instances WHERE chore_id = ? AND household_id = ? "
        "AND status = 'pending' AND due_date <= ? ORDER BY due_date DESC, id DESC LIMIT 1",
        (chore_id, household_id(), today),
    ).fetchone()
    if owed and owed["assignee_id"] is not None:
        return owed["assignee_id"]
    ahead = conn.execute(
        "SELECT assignee_id FROM chore_instances WHERE chore_id = ? AND household_id = ? "
        "AND status = 'pending' AND due_date > ? ORDER BY due_date ASC, id ASC LIMIT 1",
        (chore_id, household_id(), today),
    ).fetchone()
    if ahead and ahead["assignee_id"] is not None:
        return ahead["assignee_id"]
    return _next_in_turn(conn, chore_id, rotation)


def _blank_who_label(conn, mode: str, people: list[int]) -> str:
    """
    What a row says when there is no name to print. "Either of you" is the
    right blank for a chore the household has decided is nobody's in
    particular, and the wrong one for the other two blanks — an
    outsourced chore is precisely nobody HERE's, and an owned chore with
    nobody on it is a question nobody has answered yet.
    """
    if mode == "outsourced":
        return NOBODY_IN_THE_HOUSE
    if mode == "owned" and not people:
        return UNCLAIMED
    return _nobody_label(conn)


def _describe(conn, row, names_by_id: dict[int, str] | None = None) -> dict:
    """
    The owner fields every chore row carries on the way out — what the
    Plan | Chores rows and the Now card will show. `who_label` is the one
    to print: the owner's first name, whose turn it is on a shared chore,
    "either of you" when it's nobody's in particular, or who does it when
    it isn't us at all.

    An outsourced row also says `outsourced` outright and carries
    `completable: False`, so a screen draws the tag and leaves the tick
    off rather than having to know what the modes mean.
    """
    names_by_id = names_by_id if names_by_id is not None else _names_by_id(conn)
    mode = chore_mode(row)
    people = _rotation_ids(row)
    owner_id = people[0] if mode == "owned" and people else None
    up_next_id = _up_this_time(conn, row["id"], people) if mode == "shared" else None
    outsourced_to = _outsourced_label(row)
    if mode == "owned":
        who = _first_name(names_by_id.get(owner_id))
    elif mode == "shared":
        who = _first_name(names_by_id.get(up_next_id))
    elif mode == "outsourced":
        who = outsourced_to or NOBODY_IN_THE_HOUSE
    else:
        who = ""
    return {
        "mode": mode,
        "owner": names_by_id.get(owner_id) if owner_id is not None else None,
        "assignees": [names_by_id.get(i, "?") for i in people],
        "up_next": names_by_id.get(up_next_id) if up_next_id is not None else None,
        # "either of you" is the right blank for a whoever chore and the
        # wrong one for an outsourced chore, which is precisely nobody
        # here — so the fallback only applies to the modes it's true of.
        "who_label": who or _blank_who_label(conn, mode, people),
        "outsourced": mode == "outsourced",
        "outsourced_to": outsourced_to,
        "completable": mode != "outsourced",
        # An owned chore with nobody on it is the one shape the house can
        # be left in by handing an outsourced chore back (see
        # update_chore) — a real question for the household, not a bug, so
        # it is said out loud rather than filled in with a guess.
        "needs_owner": mode == "owned" and not people,
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
    outsourced_to: str | None = None,
) -> dict:
    """
    Create a new recurring chore definition. Every chore has a chosen
    owner: `mode` is owned (one person, always — the default), shared
    (the named people take turns), whoever (nobody in particular) or
    outsourced (somebody outside the house does it). `owner_name` names
    the owner; `assignee_names` names the people taking turns;
    `outsourced_to` names whoever comes in to do it ("Maria", "the lawn
    people") and is optional even then. With none of them, the only adult
    (or the only person in the setup rotation) owns it; if there's no one
    obvious, it's shared across the people named in setup.
    """
    conn = get_conn()
    names = list(assignee_names or [])
    # Naming who comes in IS the tag: "the cleaner does the bathrooms"
    # doesn't also say the word outsourced, and asking for it twice would
    # be asking the household to speak the app's language.
    if outsourced_to and outsourced_to.strip() and mode is None:
        mode = "outsourced"
    if owner_name and owner_name.strip() and mode != "outsourced":
        names = [owner_name] + [n for n in names if n.strip().lower() != owner_name.strip().lower()]
        if mode is None:
            mode = "owned"
    try:
        mode, ids = _resolve_people(conn, mode, names)
    except ValueError:
        conn.close()
        raise
    label = (outsourced_to or "").strip() if mode == "outsourced" else ""
    cur = conn.execute(
        "INSERT INTO chores (household_id, name, category, frequency, default_assignee_id, rotation_member_ids_json, mode, outsourced_to) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (household_id(), name, category, frequency, ids[0] if ids else None, json.dumps(ids), mode, label),
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
    owner, the people taking turns, whose turn it is right now, and
    whether somebody outside the house does it (outsourced, outsourced_to).
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
    outsourced_to: str | None = None,
) -> dict:
    """
    Update an existing chore's frequency, category, who it belongs to, or
    active status. Changing the owner or mode moves every instance not yet
    done onto the new answer; done ones keep whoever did them.

    Tagging a chore outsourced ("the cleaner does the bathrooms now")
    takes it off whoever had it and off every instance not yet done.
    Handing it back — any other mode, with nobody named — returns it to
    owned with no owner and says `needs_owner`, so the household is asked
    whose it is rather than having one guessed for them.
    """
    conn = get_conn()
    require_household_row(conn, "chores", chore_id, label="chore")
    try:
        # Work out who first, before anything is written: a refused owner
        # change ("whose should it be?") must leave the row exactly as it
        # was, frequency included.
        people = None
        label = ""
        if mode is not None or owner_name is not None or assignee_names is not None or outsourced_to is not None:
            row = conn.execute(
                "SELECT * FROM chores WHERE id = ? AND household_id = ?", (chore_id, household_id())
            ).fetchone()
            was_outsourced = is_outsourced(row)
            names = list(assignee_names or [])
            if outsourced_to is not None and outsourced_to.strip() and mode is None:
                # Naming who comes in is the tag — same as add_chore.
                mode = "outsourced"
            if owner_name and owner_name.strip() and mode != "outsourced":
                names = [owner_name] + [n for n in names if n.strip().lower() != owner_name.strip().lower()]
                if mode is None:
                    mode = "owned"
            elif mode is None and assignee_names is not None and not names:
                # An explicit empty list is what "nobody's" looked like before
                # mode existed; keep meaning that rather than re-deriving an owner.
                mode = "whoever"
            if was_outsourced and mode == "outsourced":
                # Re-labelling ("it's Maria now, not the agency") keeps the
                # tag; an omitted label leaves the one already on the row.
                label = (outsourced_to if outsourced_to is not None else row["outsourced_to"]) or ""
                people = ("outsourced", [])
            elif was_outsourced and mode in ("owned", None) and not names:
                # Un-tagging with nobody named. Deliberately NOT
                # _resolve_people's owned path, which would infer the only
                # adult or fall back to sharing it round: the household
                # has just taken a chore back off somebody, and who picks
                # it up is exactly the thing they haven't said. So the
                # untag lands, the owner is blank, and `needs_owner` asks
                # — one light tap (DESIGN_SYSTEM §7), not a guess.
                people = ("owned", [])
            else:
                label = (outsourced_to or "") if mode == "outsourced" else ""
                people = _resolve_people(conn, mode, names, existing=_rotation_ids(row), strict=mode == "owned")

        if frequency is not None:
            conn.execute("UPDATE chores SET frequency = ? WHERE id = ? AND household_id = ?", (frequency, chore_id, household_id()))
        if category is not None:
            conn.execute("UPDATE chores SET category = ? WHERE id = ? AND household_id = ?", (category, chore_id, household_id()))
        reassigned = None
        if people is not None:
            _write_people(conn, chore_id, *people, outsourced_to=label)
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


# --- when the next one falls due ---------------------------------------------
#
# Loop Board "Chores v1: No guilt pile — a slipped chore is due, not overdue"
# (Emily, 2026-09-11). Two rules live in this section, and they are the
# whole of the card's arithmetic.

# The day a chore was actually done. done_on is the answer; the two
# fallbacks are for rows written before that column existed, so nothing in
# this module depends on db._backfill_chore_done_on having run first — the
# same stance chore_mode() takes towards _migrate_chore_modes.
_DONE_DAY_SQL = "COALESCE(NULLIF(done_on, ''), date(completed_at), due_date)"


def _last_done_day(conn, chore_id: int) -> date | None:
    """The day this chore was last actually done, or None if it never was."""
    row = conn.execute(
        f"SELECT {_DONE_DAY_SQL} AS done_day FROM chore_instances "
        "WHERE chore_id = ? AND household_id = ? AND status = 'done' "
        f"ORDER BY {_DONE_DAY_SQL} DESC, id DESC LIMIT 1",
        (chore_id, household_id()),
    ).fetchone()
    if not row or not row["done_day"]:
        return None
    try:
        return date.fromisoformat(row["done_day"])
    except ValueError:
        return None


def _next_due_date(conn, chore_id: int, interval: int, today: date, outsourced: bool = False) -> date | None:
    """
    When this chore's next occurrence falls — or None when the honest
    answer is "nothing new".

    **The clock runs from the day it was last DONE, not the day it was last
    DUE.** Reckoning from the due date is what turned one slipped mop into a
    pile: the next occurrence landed in the past, so did the one after it,
    and filling the schedule dealt out four rows nobody could ever have
    been on time for. From the done day, doing it late simply moves the
    rhythm along — which is what a person means when they say they mopped.

    **And a chore that is already due gets nothing written for it at all —
    unless it's OUTSOURCED.** A pending occurrence whose day has gone by is
    due now; when the NEXT one falls is not knowable until this one is
    actually done, so for an owned or shared chore stacking a second row on
    top of it would rebuild the pile one date at a time. That reasoning
    depends on somebody being able to catch the pending row up by doing it
    — which is exactly what's not true of an outsourced chore
    (_refuse_if_outsourced means nobody in the house ever ticks it). Holding
    the schedule for a missed cleaner visit doesn't protect a person from a
    guilt pile; it just quietly cancels every cleaner day after the one
    that slipped, which is worse than the pile it was built to avoid
    (Loop Board "A slipped outsourced chore stops being scheduled, and
    nothing on any screen can clear it", Emily). So an outsourced chore is
    exempt from the hold: its rhythm keeps running as if the slipped
    occurrence had been settled. The slipped row itself is untouched —
    still pending, still due, still un-tickable — this only decides
    whether a future one also gets written.

    For that exempt case the anchor is the slipped due date itself (the
    most recent row on file), stepped forward by the interval until the
    result is after today — never the day the fix happened to run. A
    weekly cleaner who missed this Thursday gets next Thursday, not a
    back-dated Thursday and not a drift onto whatever weekday the schedule
    next ran. Stepping (rather than jumping straight to "today + interval")
    is what keeps a cleaner who's missed several weeks in a row landing on
    their usual weekday instead of sliding onto a different one.
    """
    slipped = conn.execute(
        "SELECT id FROM chore_instances WHERE chore_id = ? AND household_id = ? "
        "AND status = 'pending' AND due_date < ? LIMIT 1",
        (chore_id, household_id(), today.isoformat()),
    ).fetchone()
    if slipped and not outsourced:
        return None

    last_due = conn.execute(
        "SELECT due_date FROM chore_instances WHERE chore_id = ? AND household_id = ? "
        "ORDER BY due_date DESC, id DESC LIMIT 1",
        (chore_id, household_id()),
    ).fetchone()
    if not last_due:
        # Never scheduled at all — it starts today, exactly as before.
        return today

    anchor = date.fromisoformat(last_due["due_date"])
    done_day = _last_done_day(conn, chore_id)
    if done_day and done_day > anchor:
        # Done after the last date the schedule named: the rhythm restarts
        # from when it actually happened. While the schedule still runs
        # ahead of the done day, that schedule is the answer and this is a
        # no-op — the done-day anchor only takes over once it has run out,
        # which is exactly the case that used to produce a backlog.
        anchor = done_day

    if slipped:
        # Outsourced and already due: step forward from the slipped date by
        # whole intervals until landing after today, rather than a single
        # anchor + interval hop, so a cleaner who's missed more than one
        # visit still lands on their usual weekday and not on whatever date
        # one hop happens to produce.
        next_due = anchor + timedelta(days=interval)
        while next_due <= today:
            next_due += timedelta(days=interval)
        return next_due

    # Never write an occurrence into the past. A day that has already gone
    # by is not something anyone can do on time, and a run of them is the
    # pile itself.
    return max(anchor + timedelta(days=interval), today)


def _fill_schedule(conn, chore, today: date, horizon: date) -> list[dict]:
    """
    Write this chore's occurrences from its next due date out to `horizon`,
    skipping any date that already has one. Shared by the bulk generate and
    by a tick, so "when does the next one fall" has exactly one
    implementation rather than two that can disagree.
    """
    interval = _FREQUENCY_DAYS.get(chore["frequency"], 7)
    if not interval:
        # frequency 'once' has no interval, so there is no "next" to write.
        # Such a chore never gets an occurrence from here at all and has to
        # be dated by hand with schedule_chore_instance — pre-existing
        # (generate_chore_schedule's own query has always excluded 'once'),
        # and silent, which is worth knowing if one ever looks empty.
        return []
    next_due = _next_due_date(conn, chore["id"], interval, today, outsourced=is_outsourced(chore))
    if next_due is None:
        return []

    mode = chore_mode(chore)
    people = _rotation_ids(chore)
    last = conn.execute(
        "SELECT assignee_id FROM chore_instances WHERE chore_id = ? AND household_id = ? "
        "ORDER BY due_date DESC, id DESC LIMIT 1",
        (chore["id"], household_id()),
    ).fetchone()
    last_assignee_id = last["assignee_id"] if last else None
    rotation_cursor = people.index(last_assignee_id) + 1 if last_assignee_id in people else 0

    created = []
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
    return created


def _rebase_future(conn, chore_id: int, interval: int, done_day: date, today: date) -> int:
    """
    Move this chore's still-to-come occurrences onto the rhythm that starts
    from the day it was just DONE.

    Without this the card's rule holds only when the schedule has run out.
    A household who generated a fortnight ahead, missed the first two and
    mopped on the tenth day still got the stale row three days later —
    "the next occurrence is generated from that date" being quietly
    overruled by a date written before the mopping happened. Anchoring on
    the done day and leaving the old row in place would have been worse
    still: two answers to when the next mop is.

    The whole run is shifted by ONE delta rather than only the first row
    being moved, so the gaps stay even. Re-dating just the next one leaves
    the one after it a few days behind it — the same "too soon" defect one
    row down. Shifting preserves each row's id and its assignee, which is
    what keeps a shared rotation's turn order intact through a tick.

    **The cost, written down:** a one-off occurrence somebody dated by hand
    on a recurring chore (schedule_chore_instance) moves with the rhythm
    too. Nothing in the table distinguishes it from a generated row, and
    inventing a flag to tell them apart is a bigger claim than this makes.
    """
    rows = conn.execute(
        "SELECT id, due_date FROM chore_instances WHERE chore_id = ? AND household_id = ? "
        "AND status = 'pending' AND due_date > ? ORDER BY due_date ASC, id ASC",
        (chore_id, household_id(), today.isoformat()),
    ).fetchall()
    if not rows:
        return 0
    # Never earlier than today: a back-dated tick on a daily chore would
    # otherwise shift tomorrow's row into last week.
    target = max(done_day + timedelta(days=interval), today)
    delta = (target - date.fromisoformat(rows[0]["due_date"])).days
    if delta == 0:
        return 0
    for row in rows:
        moved = date.fromisoformat(row["due_date"]) + timedelta(days=delta)
        conn.execute(
            "UPDATE chore_instances SET due_date = ? WHERE id = ? AND household_id = ?",
            (moved.isoformat(), row["id"], household_id()),
        )
    return len(rows)


def generate_chore_schedule(days_ahead: int = 14) -> list[dict]:
    """
    Auto-generate upcoming chore instances for every active chore, out to
    `days_ahead`. Skips dates that already have a pending/done instance for
    that chore. Who each one goes to follows the chore's mode: owned goes
    to the owner every time, shared takes turns through the named people
    (continuing from whoever had it last), whoever and outsourced go to
    nobody (an outsourced chore still gets its instances, so the week
    shows that Thursday is cleaner day — it just isn't ours). Call
    this after onboarding, and periodically (e.g. "generate this week's
    chores") to keep the schedule filled in.

    A chore that has already slipped is left alone rather than topped up —
    see _next_due_date. It is due now; nothing is gained by writing more
    dates it has already gone past. The one exception is an outsourced
    chore: since nobody in the house ever ticks it, holding the schedule
    for a slipped cleaner day wouldn't spare a person a guilt pile — it
    would just cancel every cleaner day after the one that was missed. So
    an outsourced chore keeps generating on schedule even while its
    slipped occurrence sits there unsettled; see _next_due_date for the
    exact anchor.
    """
    conn = get_conn()
    chores = conn.execute(
        "SELECT * FROM chores WHERE household_id = ? AND active = 1 AND frequency != 'once'", (household_id(),)
    ).fetchall()

    created = []
    today = date.today()
    horizon = today + timedelta(days=days_ahead)
    for chore in chores:
        created.extend(_fill_schedule(conn, chore, today, horizon))

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
           CASE WHEN ci.status = 'done'
                THEN COALESCE(NULLIF(ci.done_on, ''), date(ci.completed_at), ci.due_date)
           END AS done_on,
           c.id AS chore_id, c.mode, c.rotation_member_ids_json, c.default_assignee_id,
           c.outsourced_to
    FROM chore_instances ci
    JOIN chores c ON c.id = ci.chore_id
    LEFT JOIN members m ON m.id = ci.assignee_id
    LEFT JOIN members d ON d.id = ci.completed_by_member_id
"""


def _instance_dicts(conn, rows) -> list[dict]:
    """
    One instance per row, with who it's for spelled out: `assignee` is
    whose it was when scheduled, `completed_by` who ticked it, and
    `who_label` the name to print (first name, "either of you", or who
    comes in to do it when it isn't us).

    An outsourced instance carries `outsourced` and `completable: False`
    so a screen draws the tag and no tick without having to know what the
    modes mean — and, just as important, so nothing counts it: it is
    nobody's load, and a "1 of 3 done" that includes cleaner day is a
    household being told it is behind on something it never had to do.
    """
    nobody = None
    out = []
    for r in rows:
        # The chore's own columns ride along on the join, so chore_mode()
        # can read the row directly.
        row_mode = chore_mode(r)
        outsourced = row_mode == "outsourced"
        who = _first_name(r["assignee"])
        if not who and outsourced:
            who = _outsourced_label(r) or NOBODY_IN_THE_HOUSE
        elif not who:
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
            # The day the work happened (not the instant it was ticked).
            "done_on": r["done_on"],
            # How many occurrences this row stands for. Always 1 until
            # _collapse_outstanding merges a backlog into one due row.
            "stands_for": 1,
            "outsourced": outsourced,
            "outsourced_to": _outsourced_label(r),
            "completable": not outsourced,
        })
    return out


def _collapse_outstanding(items: list[dict], today: date) -> list[dict]:
    """
    Everything a chore has let slip shows as ONE due row.

    Loop Board "Chores v1: No guilt pile" (Emily, 2026-09-11). A weekly mop
    nobody got to for a month is four pending rows in the table and one job
    in the house. Four rows IS the guilt pile — and nobody should be asked
    to tick the same mop four times to make it go away.

    **Collapsed on the read, not by merging the rows away**, for two
    reasons. The rows are honest history: the schedule really did name
    those days, and the fairness view reads them. And time keeps making
    more of them, so a write-side collapse would have to run on every read
    anyway — a read quietly rewriting the household's data. The write
    belongs on the tick instead, which is where _mark_done clears the group.

    The row that stands for the group is the LATEST one due on or before
    today: the occurrence that is genuinely due now, so nothing downstream
    is handed a date weeks back to be tempted into counting days from.
    `stands_for` says how many it covers, for anything that asks — nothing
    prints it unprompted, and nothing anywhere turns it into a tally.
    """
    today_iso = today.isoformat()
    out: list[dict] = []
    seen: dict[int, int] = {}
    for raw in items:
        item = dict(raw)
        item.setdefault("stands_for", 1)
        if item["status"] != "pending" or item["due_date"] > today_iso:
            out.append(item)
            continue
        at = seen.get(item["chore_id"])
        if at is None:
            seen[item["chore_id"]] = len(out)
            out.append(item)
        elif item["due_date"] >= out[at]["due_date"]:
            item["stands_for"] = out[at]["stands_for"] + 1
            out[at] = item
        else:
            out[at]["stands_for"] += 1
    return out


def list_chores(status: str = "pending", days_ahead: int = 14) -> list[dict]:
    """
    List chore instances, optionally filtered by status (pending/done/
    skipped/all). Each carries who it's for (assignee, who_label) and, once
    done, who actually did it (completed_by) and the day they did it
    (done_on).

    Anything still pending whose day has gone by is simply DUE — one row
    per chore however many occurrences slipped, carrying `stands_for`.
    There is no overdue status here to report and no count of days late to
    read off: see _collapse_outstanding.
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
    result = _collapse_outstanding(_instance_dicts(conn, rows), date.today())
    conn.close()
    return result


def _refuse_if_outsourced(conn, instance_id: int) -> None:
    """
    An outsourced chore cannot be ticked off, by anybody, ever.

    The question this answers, written down because it is a decision and
    not an oversight: should an outsourced instance be completable at all?
    No. A tick in this app means a person in the house did a thing, and it
    is what the fairness view counts — so allowing one would either credit
    somebody who didn't do it (the signed-in adult, by _doer_id's default,
    which is exactly the "Vinneth did the bins" failure recorded on the
    owner card) or record a completion belonging to nobody. Neither is
    worth having, and a household that wants the credit can hand the chore
    back with one tap.

    A calm sentence rather than a silent accept: a tick that appears to
    land and doesn't is the worse failure. 'skipped' is still allowed —
    the cleaner not coming is a real thing that happens to a real Thursday.
    """
    row = conn.execute(
        "SELECT c.mode, c.name, c.outsourced_to FROM chore_instances ci "
        "JOIN chores c ON c.id = ci.chore_id "
        "WHERE ci.id = ? AND ci.household_id = ?",
        (instance_id, household_id()),
    ).fetchone()
    if row is None or not is_outsourced(row):
        return
    who = (row["outsourced_to"] or "").strip()
    raise ValueError(
        f"{row['name']} is {who}'s, not ours — there's nothing to tick off."
        if who
        else f"{row['name']} is somebody else's — there's nothing to tick off."
    )


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


def _done_day(done_on: str | None) -> date:
    """
    The day a tick says the work happened. Nothing given means today.

    A date nobody can read, or one that hasn't happened yet, is a plain
    question back rather than a quiet guess — the same stance _doer_id
    takes towards a name it doesn't recognise.
    """
    if not done_on or not str(done_on).strip():
        return date.today()
    try:
        day = date.fromisoformat(str(done_on).strip())
    except ValueError:
        raise ValueError(f"I couldn't read {done_on!r} as a date — it wants YYYY-MM-DD.")
    if day > date.today():
        raise ValueError(f"{day.isoformat()} hasn't happened yet — when was it actually done?")
    return day


def _mark_done(conn, instance_id: int, doer: int | None, done_day: date) -> dict:
    """
    One tick, and everything it settles.

    The row goes done, carrying the DAY the work happened beside the
    instant the tick arrived. Then two things follow from the no-guilt-pile
    card, and both are the point of doing this in one place:

    - **Every other pending occurrence of the same chore due on or before
      it is cleared.** A backlog shows as one due row (see
      _collapse_outstanding), so one tick has to settle the whole group or
      the household is asked to tick the same mop four times. They are
      marked 'skipped' rather than 'done': those occurrences did not
      happen, and writing four mops for one would be false history the
      fairness view would go on to count. Nothing shows a skipped row, so
      this reads as the pile simply being gone.
    - **The next occurrence is written from the done day.** That is what
      stops a chore done late from coming straight back due — see
      _next_due_date.

    Un-ticking (set_chore_instance_status back to pending) brings back the
    one row, not the pile. Deliberate: which occurrences a tick swept up is
    not recorded, and resurrecting three weeks of dates on a mis-tap is the
    exact thing this card exists to stop. **It does leave the occurrence
    this tick put on the calendar**, so an un-tick makes list_chores
    ("pending") answer with two rows for that chore — the one handed back
    and the one ahead. Harmless on the Today card, which only shows what is
    due; worth knowing before writing anything that counts pending rows.
    """
    row = conn.execute(
        "SELECT chore_id, due_date FROM chore_instances WHERE id = ? AND household_id = ?",
        (instance_id, household_id()),
    ).fetchone()
    conn.execute(
        "UPDATE chore_instances SET status = 'done', completed_at = datetime('now'), done_on = ?, "
        "completed_by_member_id = ? WHERE id = ? AND household_id = ?",
        (done_day.isoformat(), doer, instance_id, household_id()),
    )
    # Bounded by the LATER of the ticked row's day and today, so the sweep
    # does not depend on the caller having passed _collapse_outstanding's
    # representative. Handed an earlier row of the same pile — a card
    # rendered before a date rollover, a stale instance_id the assistant
    # still holds — the old `<= row.due_date` left the rest of the pile
    # standing, and the card then showed one chore twice: once ticked,
    # once still due. Ticking a FUTURE occurrence early still clears
    # everything up to it, which is why it is the later of the two.
    sweep_through = max(row["due_date"], date.today().isoformat())
    cleared = conn.execute(
        "UPDATE chore_instances SET status = 'skipped' WHERE household_id = ? AND chore_id = ? "
        "AND status = 'pending' AND due_date <= ? AND id != ?",
        (household_id(), row["chore_id"], sweep_through, instance_id),
    ).rowcount or 0

    chore = conn.execute(
        "SELECT * FROM chores WHERE id = ? AND household_id = ?", (row["chore_id"], household_id())
    ).fetchone()
    moved = 0
    if chore and chore["active"] and chore["frequency"] != "once":
        interval = _FREQUENCY_DAYS.get(chore["frequency"], 7) or 0
        if interval:
            today = date.today()
            moved = _rebase_future(conn, chore["id"], interval, done_day, today)
            _fill_schedule(conn, chore, today, max(done_day, today) + timedelta(days=interval))
    # Read the next date back off the table rather than reporting whatever
    # this call happened to INSERT: after a rebase the next occurrence is
    # usually a row that already existed and was moved, and a caller told
    # "next_due: null" about a chore that plainly has one would be wrong.
    nxt = conn.execute(
        "SELECT MIN(due_date) AS d FROM chore_instances WHERE chore_id = ? AND household_id = ? AND status = 'pending'",
        (row["chore_id"], household_id()),
    ).fetchone()
    return {
        "instance_id": instance_id,
        "status": "done",
        "done_on": done_day.isoformat(),
        "also_cleared": cleared,
        "upcoming_moved": moved,
        "next_due": nxt["d"] if nxt else None,
    }


def complete_chore(instance_id: int, done_by: str | None = None, done_on: str | None = None) -> dict:
    """
    Mark a chore instance as done. `done_by` names who did it when it
    wasn't the signed-in adult ("Vineeth did the bins"); otherwise the
    session's adult gets the credit. `done_on` (YYYY-MM-DD) back-dates the
    WORK for "I did it yesterday" — the next occurrence counts from that
    day, so saying so actually moves the rhythm rather than being humoured.
    An outsourced chore is refused — nobody in the house did it, so there
    is nobody to credit, and nothing of its rhythm moves.
    """
    conn = get_conn()
    # try/finally rather than closing on each path by hand. _mark_done runs
    # a sweep, a rebase and a refill, any of which can raise after a write
    # has already opened a transaction on this connection — and a leaked
    # SQLite connection holding an uncommitted write surfaces later as
    # "database is locked" somewhere with nothing to do with the cause.
    try:
        # Whose row it is, then whether it is ours to tick at all: a row
        # belonging to another household must be refused before anything
        # here reads a word of it, outsourced label included.
        require_household_row(conn, "chore_instances", instance_id, label="chore instance")
        _refuse_if_outsourced(conn, instance_id)
        doer = _doer_id(conn, done_by)
        done_day = _done_day(done_on)
        result = _mark_done(conn, instance_id, doer, done_day)
        conn.commit()
        return result
    finally:
        conn.close()


def get_chores_due_today() -> list[dict]:
    """
    Chore instances due today — powers the app-shell Today screen's chores
    card (design_handoff_shell/README.md §4). Includes both pending and
    done instances (not just pending) so the UI can show an accurate
    "x of y done" count rather than only the still-open ones.

    **A pending chore whose day has gone by is due, and shows here exactly
    like anything else due today** (Loop Board "Chores v1: no guilt pile",
    Emily, 2026-09-11). It used to be due_date = today on the nose, so a
    chore that slipped fell off the screen entirely and came back only as
    a fresh pile of past dates the next time the schedule was generated.
    Nothing in the payload says how long ago it was asked for, and one
    chore is one row however many occurrences went by — see
    _collapse_outstanding.

    Done instances are the ones done TODAY, whatever day they were asked
    for. Otherwise ticking a chore that slipped would make the row vanish
    under the finger and take the "1 of 1" count with it.

    Household-wide, not filtered to a signed-in member: there's no
    per-user login concept yet (see household_id() above), so this can't
    actually distinguish "my chores" from anyone else's the way the
    redesign's Today spec describes. Noted as a known gap in the README
    rather than silently faked.
    """
    conn = get_conn()
    today = date.today()
    today_iso = today.isoformat()
    rows = conn.execute(
        _INSTANCE_SELECT + """
        WHERE ci.household_id = ?
          AND ci.due_date <= ?
          AND (ci.status = 'pending'
               OR (ci.status = 'done'
                   AND COALESCE(NULLIF(ci.done_on, ''), date(ci.completed_at), ci.due_date) = ?))
        ORDER BY ci.due_date ASC, ci.id ASC
        """,
        (household_id(), today_iso, today_iso),
    ).fetchall()
    result = _collapse_outstanding(_instance_dicts(conn, rows), today)
    conn.close()
    return result


def set_chore_instance_status(instance_id: int, status: str = "done") -> dict:
    """
    Mark a chore instance done or back to pending directly from the Today
    screen's chores card (no chat round-trip needed) — same shape as
    check_off_meal/check_off_prep_step below. A tick is credited to the
    session's adult (completed_by_member_id) and dated today; un-ticking
    clears both. A tick here goes through the same _mark_done as chat, so
    a slipped chore's whole backlog clears on one tap and the next
    occurrence is reckoned from today. An outsourced chore has no tick to
    give — see _refuse_if_outsourced.
    """
    conn = get_conn()
    # Same try/finally as complete_chore, and for the same reason: this is
    # the other door into _mark_done. It is also what closes the connection
    # when the refusal below raises, so that path needs no close of its own.
    try:
        require_household_row(conn, "chore_instances", instance_id, label="chore instance")
        if status == "done":
            # Only the tick is refused. 'skipped' stays open to an
            # outsourced chore — the cleaner not coming is a real thing
            # that happens to a real Thursday, and it is also the only way
            # a slipped outsourced row is ever cleared, since a tick never
            # comes to sweep it.
            _refuse_if_outsourced(conn, instance_id)
            result = _mark_done(conn, instance_id, _doer_id(conn, None), date.today())
        else:
            conn.execute(
                "UPDATE chore_instances SET status = ?, completed_at = NULL, done_on = NULL, "
                "completed_by_member_id = NULL WHERE id = ? AND household_id = ?",
                (status, instance_id, household_id()),
            )
            result = {"instance_id": instance_id, "status": status}
        conn.commit()
        return result
    finally:
        conn.close()
