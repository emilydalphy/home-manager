"""
Read-only share links and the eater self-service pages they open.
"""
from __future__ import annotations

import json
import secrets
from ..db import get_conn
from ._shared import HOUSEHOLD_ID, _absolute_url
from . import household as _household
from . import weekly_plan as _weekly_plan


def get_or_create_share_link() -> dict:
    """
    Get this household's read-only share-link token for the weekly meal
    plan, creating one on first use. The token is stable — it's not tied to
    a specific plan, so the same link keeps working and always shows
    whichever plan is most recent as new weeks get generated. No login is
    involved; anyone with the link can view the current plan, nothing else.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT token FROM share_links WHERE household_id = ? ORDER BY created_at ASC LIMIT 1",
        (HOUSEHOLD_ID,),
    ).fetchone()
    if row:
        token = row["token"]
    else:
        token = secrets.token_urlsafe(16)
        conn.execute(
            "INSERT INTO share_links (household_id, token) VALUES (?, ?)",
            (HOUSEHOLD_ID, token),
        )
        conn.commit()
    conn.close()
    return {"token": token, "link": _absolute_url(f"/share/{token}")}


def get_shared_weekly_plan(token: str) -> dict | None:
    """
    Resolve a share-link token to the household's current (most recent)
    weekly plan. Returns None if the token doesn't match anything, so the
    caller can 404 rather than leak whether a token almost matched. Only
    meal-plan data is returned — no other household info (dietary details,
    chores, etc.) is exposed through this path.
    """
    conn = get_conn()
    row = conn.execute("SELECT household_id FROM share_links WHERE token = ?", (token,)).fetchone()
    household = None
    if row:
        household = conn.execute(
            "SELECT name FROM households WHERE id = ?", (row["household_id"],)
        ).fetchone()
    conn.close()
    if not row:
        return None
    plan = _weekly_plan.get_weekly_plan()
    plan["household_name"] = household["name"] if household else ""
    return plan


# A personal, tokenized, limited-write link tied to one members row, so a
# household member other than the Planner can register their own dietary
# restrictions and feedback directly, without full account creation or
# relaying it secondhand through the Planner. Modeled on the read-only plan
# share link above, but scoped to a single person and able to write.
def get_or_create_member_share_link(member_name: str) -> dict:
    """
    Get (or create on first use) a standing, personal share link for one
    household member — lets them view/add their own dietary restrictions
    and leave feedback notes directly, without going through the Planner.
    Raises ValueError if no member with this name exists yet (add them with
    add_member or set_member_dietary_restrictions first). If a non-revoked
    link already exists for this person, returns the same one rather than
    creating a duplicate — use regenerate_member_share_link to force a new
    token instead. Returns a `link` field with the actual, absolute URL —
    share that value verbatim, never construct or guess a URL yourself.
    """
    conn = get_conn()
    member = conn.execute(
        "SELECT id, name FROM members WHERE household_id = ? AND LOWER(name) = LOWER(?)",
        (HOUSEHOLD_ID, member_name),
    ).fetchone()
    if not member:
        conn.close()
        raise ValueError(f"No household member named '{member_name}' yet.")
    row = conn.execute(
        "SELECT token FROM member_share_links WHERE household_id = ? AND member_id = ? AND revoked = 0 "
        "ORDER BY created_at DESC LIMIT 1",
        (HOUSEHOLD_ID, member["id"]),
    ).fetchone()
    if row:
        token = row["token"]
    else:
        token = secrets.token_urlsafe(16)
        conn.execute(
            "INSERT INTO member_share_links (household_id, member_id, token) VALUES (?, ?, ?)",
            (HOUSEHOLD_ID, member["id"], token),
        )
        conn.commit()
    conn.close()
    return {"member_name": member["name"], "token": token, "link": _absolute_url(f"/member-share/{token}")}


def revoke_member_share_link(member_name: str) -> dict:
    """Revoke a household member's self-service link — any existing link stops working immediately. Use before regenerate_member_share_link, or on its own if the link should just be shut off (e.g. it was shared by mistake)."""
    conn = get_conn()
    member = conn.execute(
        "SELECT id FROM members WHERE household_id = ? AND LOWER(name) = LOWER(?)",
        (HOUSEHOLD_ID, member_name),
    ).fetchone()
    if not member:
        conn.close()
        raise ValueError(f"No household member named '{member_name}'.")
    conn.execute(
        "UPDATE member_share_links SET revoked = 1 WHERE household_id = ? AND member_id = ? AND revoked = 0",
        (HOUSEHOLD_ID, member["id"]),
    )
    conn.commit()
    conn.close()
    return {"member_name": member_name, "revoked": True}


def regenerate_member_share_link(member_name: str) -> dict:
    """Revoke a household member's current self-service link (if any) and issue a fresh one in the same call — use if a link may have leaked, or the Planner just wants a clean new one."""
    revoke_member_share_link(member_name)
    return get_or_create_member_share_link(member_name)


def resolve_member_share_link(token: str) -> dict | None:
    """
    Resolve a member self-service token to that person's own view: their
    name, current dietary restrictions, and any notes they've left before.
    Returns None for an invalid or revoked token, so the caller can 404
    rather than leak whether a token almost matched. Only this one member's
    own data is exposed through this path — nothing else about the
    household.
    """
    conn = get_conn()
    link = conn.execute(
        "SELECT member_id FROM member_share_links WHERE token = ? AND revoked = 0", (token,)
    ).fetchone()
    if not link:
        conn.close()
        return None
    member = conn.execute(
        "SELECT name, dietary_restrictions_json FROM members WHERE id = ?", (link["member_id"],)
    ).fetchone()
    if not member:
        conn.close()
        return None
    notes = conn.execute(
        "SELECT note, created_at FROM member_notes WHERE member_id = ? ORDER BY created_at DESC LIMIT 10",
        (link["member_id"],),
    ).fetchall()
    conn.close()
    return {
        "member_name": member["name"],
        "dietary_restrictions": json.loads(member["dietary_restrictions_json"]),
        "notes": [{"note": n["note"], "created_at": n["created_at"]} for n in notes],
    }


def eater_add_dietary_restriction(token: str, restrictions: list[str]) -> dict:
    """
    Add dietary restriction(s) for the member behind this self-service
    token — merges with whatever they already have (never a destructive
    replace, since this is a one-off self-edit, not a full-list
    resubmission). Raises ValueError for an invalid/revoked token.
    """
    conn = get_conn()
    link = conn.execute(
        "SELECT member_id FROM member_share_links WHERE token = ? AND revoked = 0", (token,)
    ).fetchone()
    if not link:
        conn.close()
        raise ValueError("This link isn't valid.")
    member = conn.execute("SELECT name FROM members WHERE id = ?", (link["member_id"],)).fetchone()
    conn.close()
    if not member:
        raise ValueError("This link isn't valid.")
    return _household.set_member_dietary_restrictions(member["name"], restrictions)


def eater_add_note(token: str, note: str) -> dict:
    """Leave a freeform preference/feedback note as the member behind this self-service token. Raises ValueError for an invalid/revoked token."""
    conn = get_conn()
    link = conn.execute(
        "SELECT member_id FROM member_share_links WHERE token = ? AND revoked = 0", (token,)
    ).fetchone()
    if not link:
        conn.close()
        raise ValueError("This link isn't valid.")
    conn.execute(
        "INSERT INTO member_notes (household_id, member_id, note) VALUES (?, ?, ?)",
        (HOUSEHOLD_ID, link["member_id"], note),
    )
    conn.commit()
    conn.close()
    return {"saved": True}


def get_member_notes(member_name: str | None = None) -> list[dict]:
    """
    List freeform notes members have left via their self-service links —
    use this when the Planner asks what an Eater has said, or as part of
    reviewing feedback generally. Omit member_name for every member's
    notes, most recent first.
    """
    conn = get_conn()
    if member_name:
        rows = conn.execute(
            "SELECT m.name, n.note, n.created_at FROM member_notes n "
            "JOIN members m ON m.id = n.member_id "
            "WHERE n.household_id = ? AND LOWER(m.name) = LOWER(?) ORDER BY n.created_at DESC",
            (HOUSEHOLD_ID, member_name),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT m.name, n.note, n.created_at FROM member_notes n "
            "JOIN members m ON m.id = n.member_id "
            "WHERE n.household_id = ? ORDER BY n.created_at DESC",
            (HOUSEHOLD_ID,),
        ).fetchall()
    conn.close()
    return [{"member_name": r["name"], "note": r["note"], "created_at": r["created_at"]} for r in rows]
