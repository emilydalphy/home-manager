"""
The other adult's first open (Loop Board "First open for the adult who
didn't set Pomona up", 2026-09-25).

As the adult who didn't set Pomona up, the first time I open it I see one
short welcome: my name, who set things up, what's already planned for us,
and one button into Today. Once per adult, never again — so the flag is on
the member row (server-side), not in the browser: they may switch phones.

WHO NEVER SEES IT — the rule, stated once because three places lean on it:

  1. Every member who exists when this ships. `members.first_open_seen_at`
     arrives by ALTER TABLE as '' on every row, and a run-once data
     migration (db._mark_existing_members_first_open_seen) stamps every
     existing member 'before-first-open'. Being picked at "Who's this?"
     lives only in a cookie, so "has this adult ever opened Pomona?" cannot
     be read back from the database; stamping everyone who exists is the
     only way to be sure Emily, her partner and the beta testers never get
     a welcome out of nowhere. The cost: an existing household's partner
     who has genuinely never opened Pomona won't see it either.
  2. The adult who set things up, recorded as
     `households.set_up_by_member_id` and stamped 'set-up'. Recorded WHEN
     SETUP FINISHES (record_setup_adult, called from POST
     /api/onboarding/household, which onboarding sends only at the end):
     the session's adult if one is picked, else the first adult typed in
     onboarding. That is before any invite link can exist, so a partner
     opening the app first can't take their place. Only for a household
     with no such record (made before this, or by a script) does the
     fallback apply: the FIRST adult to open the shell (the first
     /api/whoami that resolves to an adult) is recorded then. A one-adult
     household is always the setter-up either way.
     For households that existed before this, the migration records the
     adult who approved the most recent week (by member id), else the
     first adult by creation order — the name the welcome says for an
     adult added later ("Emily's set up your household").
  3. Anyone who has already been through it: the stamp is set when they
     leave the welcome (POST /api/first-open/seen), not when it is drawn,
     so a reload mid-welcome shows it again rather than losing it.

Everyone else — an adult added after setup, or arriving by an invite link
once someone has opened the shell — sees it the first time the session
resolves to them.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..db import get_conn
from ._shared import current_member, household_id

# The values first_open_seen_at can hold. '' is the only one that means
# "hasn't seen it"; the others say why not, for anyone reading the table.
SEEN_BEFORE_FIRST_OPEN = "before-first-open"  # existed when this shipped
SEEN_SET_UP = "set-up"  # the adult who set the household up
# A real pass through the welcome stores the timestamp itself.

# How many dinners the preview names: tonight (or the next one) and the
# couple after it. Emily's option A, "tonight + the next couple of dinners".
PREVIEW_DINNERS = 3


def _setup_adult_name(conn, set_up_by) -> str:
    if set_up_by is None:
        return ""
    row = conn.execute(
        "SELECT name FROM members WHERE id = ? AND household_id = ?",
        (set_up_by, household_id()),
    ).fetchone()
    return (row["name"] or "").strip() if row else ""


def first_open_state(member: dict | None = None) -> dict:
    """
    Whether this session's adult should see the first-open welcome, and the
    name of whoever set the household up. `member` is current_member()'s
    answer when the caller already has it.

    Has one side effect, deliberately: the first adult of a household to get
    here is recorded as the one who set it up (rule 2 in the module
    docstring) and never shown the welcome. The claim is a conditional
    UPDATE, so two adults opening at the same instant cannot both be it.
    """
    if member is None:
        member = current_member()
    if not member:
        return {"show": False, "set_up_by": ""}
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT first_open_seen_at FROM members WHERE id = ? AND household_id = ?",
            (member["id"], household_id()),
        ).fetchone()
        hh = conn.execute(
            "SELECT set_up_by_member_id FROM households WHERE id = ?", (household_id(),)
        ).fetchone()
        set_up_by = hh["set_up_by_member_id"] if hh else None
        if row is None or (row["first_open_seen_at"] or ""):
            return {"show": False, "set_up_by": _setup_adult_name(conn, set_up_by)}
        if set_up_by is None:
            claimed = conn.execute(
                "UPDATE households SET set_up_by_member_id = ? "
                "WHERE id = ? AND set_up_by_member_id IS NULL",
                (member["id"], household_id()),
            ).rowcount
            if claimed:
                conn.execute(
                    "UPDATE members SET first_open_seen_at = ? WHERE id = ?",
                    (SEEN_SET_UP, member["id"]),
                )
                conn.commit()
                return {"show": False, "set_up_by": member["name"]}
            # Someone else claimed it between the two reads — they set it
            # up, so this adult is the other one. Fall through with theirs.
            set_up_by = conn.execute(
                "SELECT set_up_by_member_id FROM households WHERE id = ?", (household_id(),)
            ).fetchone()["set_up_by_member_id"]
        if set_up_by == member["id"]:
            conn.execute(
                "UPDATE members SET first_open_seen_at = ? WHERE id = ?",
                (SEEN_SET_UP, member["id"]),
            )
            conn.commit()
            return {"show": False, "set_up_by": member["name"]}
        return {"show": True, "set_up_by": _setup_adult_name(conn, set_up_by)}
    finally:
        conn.close()


def record_setup_adult(candidate_ids: list[int] | None = None) -> int | None:
    """
    Record who set the household up at the moment setup finishes — the
    sturdy half of rule 2 (coordinator follow-up, 2026-09-25). Onboarding
    writes its people only when setup is finished (finishSetupAndReveal →
    POST /api/onboarding/household), so this runs then, before any invite
    link can exist, and a partner opening the app first can never be
    recorded in the setter-up's place.

    Who: the adult in the session, if one is picked and is an adult of this
    household; otherwise the first ADULT among `candidate_ids` (the members
    that request just saved, in the order they were typed), else the
    household's first adult by creation order. Only when nobody is recorded
    yet — a second pass through onboarding never moves it. That adult is
    stamped as never needing the welcome. Returns the recorded id, or None
    when there's no adult to record (first_open_state's "first to open the
    main app" rule then stays the fallback).
    """
    conn = get_conn()
    try:
        hh = conn.execute(
            "SELECT set_up_by_member_id FROM households WHERE id = ?", (household_id(),)
        ).fetchone()
        if hh is None or hh["set_up_by_member_id"] is not None:
            return None
        adult_sql = "LOWER(TRIM(age_group)) = 'adult'"
        chosen = None
        member = current_member()
        if member:
            chosen = member["id"]
        if chosen is None:
            for mid in candidate_ids or []:
                row = conn.execute(
                    f"SELECT id FROM members WHERE id = ? AND household_id = ? AND {adult_sql}",
                    (mid, household_id()),
                ).fetchone()
                if row is not None:
                    chosen = row["id"]
                    break
        if chosen is None:
            row = conn.execute(
                f"SELECT id FROM members WHERE household_id = ? AND {adult_sql} ORDER BY id ASC LIMIT 1",
                (household_id(),),
            ).fetchone()
            chosen = row["id"] if row else None
        if chosen is None:
            return None
        claimed = conn.execute(
            "UPDATE households SET set_up_by_member_id = ? WHERE id = ? AND set_up_by_member_id IS NULL",
            (chosen, household_id()),
        ).rowcount
        if not claimed:
            return None
        conn.execute(
            "UPDATE members SET first_open_seen_at = ? WHERE id = ? AND first_open_seen_at = ''",
            (SEEN_SET_UP, chosen),
        )
        conn.commit()
        return chosen
    finally:
        conn.close()


def mark_first_open_seen() -> dict:
    """
    The session's adult has been through the welcome; never show it again.
    Idempotent — a second call keeps the first timestamp. Raises
    ValueError when the session resolves to nobody (there is no one to
    mark), which the route turns into a 400.
    """
    member = current_member()
    if not member:
        raise ValueError("Nobody's picked on this device yet.")
    conn = get_conn()
    conn.execute(
        "UPDATE members SET first_open_seen_at = datetime('now') "
        "WHERE id = ? AND household_id = ? AND first_open_seen_at = ''",
        (member["id"], household_id()),
    )
    conn.commit()
    conn.close()
    return {"seen": True, "member_id": member["id"]}


def _dinner_title(row) -> str:
    return ((row["recipe_name"] or row["freeform_meal"] or "")).strip()


def first_open_preview() -> dict:
    """
    What the welcome shows under the greeting (option A): the live week's
    next few dinners, the shopping list's count, and what's waiting for a
    call. Built from what the Today screen already reads — the plan that
    covers today (weekly_plan._live_plan_covering), the needed-rows count
    the needs-you band uses, and get_needs_you_items itself — rather than a
    query of its own.

    week_state is 'approved', 'draft' (a week is being planned: drafted,
    waiting for a yes) or 'none'.
    """
    from . import weekly_plan as _wp

    today = _wp._household_today()
    conn = get_conn()
    try:
        plan = _wp._live_plan_covering(conn, today.isoformat())
        dinners: list[dict] = []
        week_state = "none"
        if plan is not None:
            week_state = "approved" if plan["status"] == "approved" else "draft"
            start, day_count = _wp.plan_period(plan)
            last = (
                date.fromisoformat(start) + timedelta(days=max(day_count, 1) - 1)
            ).isoformat()
            rows = conn.execute(
                "SELECT mpe.date, mpe.freeform_meal, r.name AS recipe_name "
                "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
                "WHERE mpe.household_id = ? AND mpe.weekly_plan_id = ? AND mpe.slot = 'dinner' "
                "AND mpe.component_category IS NULL AND mpe.slot_state = 'planned' "
                "AND mpe.date >= ? AND mpe.date <= ? "
                "ORDER BY mpe.date ASC, mpe.id ASC",
                (household_id(), plan["id"], today.isoformat(), last),
            ).fetchall()
            seen_dates: set[str] = set()
            for r in rows:
                title = _dinner_title(r)
                if not title or r["date"] in seen_dates:
                    continue
                seen_dates.add(r["date"])
                day = date.fromisoformat(r["date"])
                offset = (day - today).days
                dinners.append({
                    "date": r["date"],
                    "title": title,
                    "is_tonight": offset == 0,
                    "is_tomorrow": offset == 1,
                    "weekday": day.strftime("%A"),
                })
                if len(dinners) >= PREVIEW_DINNERS:
                    break
        list_count = conn.execute(
            "SELECT COUNT(*) AS n FROM grocery_items WHERE household_id = ? "
            "AND status = 'needed' AND excluded_from_list = 0",
            (household_id(),),
        ).fetchone()["n"]
    finally:
        conn.close()

    # What needs a call: the needs-you band's own decisions (an open or
    # empty dinner tonight/tomorrow). The shop-run card is left out — the
    # list's count already says there's shopping.
    decisions: list[str] = []
    try:
        for item in _wp.get_needs_you_items():
            if item.get("type") in ("dinner_open", "dinner_decision") and item.get("title"):
                decisions.append(item["title"])
    except Exception:
        # A preview that can't read the band still greets them; Today
        # shows the band itself a tap later.
        decisions = []

    return {
        "week_state": week_state,
        "dinners": dinners,
        "list_count": int(list_count or 0),
        "decisions": decisions,
    }
