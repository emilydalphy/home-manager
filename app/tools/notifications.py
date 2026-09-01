"""
The notification feed and the learning summary behind it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from ..db import get_conn
from ._shared import HOUSEHOLD_ID
from . import inventory as _inventory
from . import recipes as _recipes
from . import weekly_plan as _weekly_plan


# Live, in-app "what needs your attention" feed — see schema.sql's comment
# on notification_dismissals for why this isn't real scheduled push.
# Covers 3 of the 4 spec'd types (dinner nudge, expiring soon, weekly plan
# ready); the 4th ("the other adult changed something") is not computed
# here — see README's Phase 5 notes for why.
def _dismissed_keys(conn) -> set:
    rows = conn.execute("SELECT key FROM notification_dismissals WHERE household_id = ?", (HOUSEHOLD_ID,)).fetchall()
    return {r["key"] for r in rows}


def get_active_notifications() -> list[dict]:
    """
    Compute the household's current notifications live (not scheduled —
    see schema.sql's notification_dismissals comment). Each has a stable
    `key` so dismissing one doesn't hide a future, different occurrence of
    the same type (e.g. dismissing today's dinner-gap nudge doesn't
    suppress tomorrow's). Powers the shell's notification bell.
    """
    conn = get_conn()
    dismissed = _dismissed_keys(conn)
    conn.close()
    out = []

    # 1. Dinner decision nudge (NOTIFICATIONS.md #1) — reuses the same
    # dinner-gap detection the Today needs-you band already uses, so the
    # notification and the band never disagree about what's open.
    for item in _weekly_plan.get_needs_you_items():
        if item.get("type") != "dinner_decision":
            continue
        key = f"dinner_gap:{item['date']}"
        if key in dismissed:
            continue
        day_label = "Tonight" if item["date"] == date.today().isoformat() else "tomorrow"
        first_option = (item.get("options") or [{}])[0].get("name") if item.get("options") else None
        out.append({
            "key": key, "type": "dinner_decision",
            "title": item["title"],
            "body": f"The quickest option is {first_option}." if first_option else "Nothing planned yet — take a look at tonight's options.",
            "tab": "today", "action_label": "Show options",
        })
        break

    # 2. Expiring soon (NOTIFICATIONS.md #2) — 2-day window per spec (the
    # Kitchen/Inventory badge itself uses 4 days; this notification is
    # deliberately tighter, matching the spec's own trigger).
    expiring = _inventory.get_expiring_soon(days=2)
    if expiring:
        today_iso = date.today().isoformat()
        key = f"expiring:{today_iso}"
        if key not in dismissed:
            if len(expiring) == 1:
                it = expiring[0]
                # COPY.md's use-it-up rewrite: name what's happening and
                # what I'll do about it, rather than reporting a date and
                # leaving the household to work out the implication.
                title = f"Your {it['item'].lower()} turns soon"
                body = (
                    f"{it['quantity']} — I’ll work it into this week if you’d like."
                    if it["quantity"] else "I’ll work it into this week if you’d like."
                )
            else:
                names = ", ".join(e["item"] for e in expiring[:3])
                title = f"{len(expiring)} things to use this week"
                body = f"{names} all turn soon — I’ll work them into this week if you’d like."
            out.append({
                "key": key, "type": "expiring_soon", "title": title, "body": body,
                "href": "/inventory", "action_label": "Plan a meal with it",
            })

    # 3. Weekly plan ready (NOTIFICATIONS.md #3) — a plan for a week that
    # hasn't started yet, generated recently, with at least 2 dinners (the
    # spec's own "don't notify for an empty plan" suppression rule).
    conn = get_conn()
    plan_row = conn.execute(
        "SELECT id, week_start_date, created_at FROM weekly_plans WHERE household_id = ? AND week_start_date > ? ORDER BY created_at DESC LIMIT 1",
        (HOUSEHOLD_ID, date.today().isoformat()),
    ).fetchone()
    if plan_row:
        created = plan_row["created_at"]
        recent = False
        try:
            created_dt = datetime.fromisoformat(created.replace(" ", "T"))
            recent = (datetime.utcnow() - created_dt) <= timedelta(hours=24)
        except ValueError:
            recent = False
        if recent:
            dinner_count = conn.execute(
                "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE weekly_plan_id = ? AND slot = 'dinner'",
                (plan_row["id"],),
            ).fetchone()["n"]
            key = f"weekly_plan:{plan_row['id']}"
            if dinner_count >= 2 and key not in dismissed:
                out.append({
                    "key": key, "type": "weekly_plan_ready",
                    "title": "Next week's plan is ready",
                    "body": f"{dinner_count} dinners planned.",
                    "tab": "week", "action_label": "Looks good",
                })

    # 4. The other adult settled the week (NOTIFICATIONS.md #4 — the type
    # that was previously left uncomputed for want of any per-adult
    # identity). Approval now records WHO said yes (weekly_plans.approved_by
    # — see design_handoff_plan_the_week), which is enough to make this
    # real. It is still household-wide rather than addressed to one person:
    # there is no per-adult session to deliver it to, so the honest version
    # is a shared "Emily approved it" the other adult sees when they next
    # open the app. That is exactly what the approved receipt's "{Other
    # adult} has been told the week is settled" is promising — so the
    # sentence describes something that actually happens.
    approved_row = conn.execute(
        "SELECT id, week_start_date, approved_by, approved_at FROM weekly_plans "
        "WHERE household_id = ? AND status = 'approved' AND TRIM(approved_by) != '' AND approved_at IS NOT NULL "
        "ORDER BY approved_at DESC LIMIT 1",
        (HOUSEHOLD_ID,),
    ).fetchone()
    if approved_row:
        # Keyed by plan id AND approval time, so reopening and re-approving
        # a week raises a fresh notification rather than being silenced by
        # the earlier approval's dismissal.
        key = f"week_approved:{approved_row['id']}:{approved_row['approved_at']}"
        recent = False
        try:
            approved_dt = datetime.fromisoformat(approved_row["approved_at"].replace(" ", "T"))
            recent = (datetime.utcnow() - approved_dt) <= timedelta(hours=48)
        except (ValueError, AttributeError):
            recent = False
        if recent and key not in dismissed:
            week_label = _weekly_plan._format_week_range(approved_row["week_start_date"])
            out.append({
                "key": key, "type": "week_approved",
                "title": f"{approved_row['approved_by']} approved the week",
                "body": f"{week_label} is settled, and the shopping list is built.",
                "tab": "week", "action_label": "Take a look",
            })
    conn.close()
    return out


def dismiss_notification(key: str) -> dict:
    """Mark one notification key dismissed so it stops showing until its underlying condition changes (a new date/plan id)."""
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO notification_dismissals (household_id, key) VALUES (?, ?)",
        (HOUSEHOLD_ID, key),
    )
    conn.commit()
    conn.close()
    return {"key": key, "dismissed": True}


def get_learning_summary() -> dict:
    """
    A visible, human-readable snapshot of what the app has actually learned
    so far — use when the user asks something like "what have you picked up
    about us?" or "has this gotten smarter?" Distinct from
    get_household_memory (raw preference values): this is aggregate stats
    that show adaptation over time.
    """
    recipes = _recipes.list_recipes()
    liked = [r for r in recipes if r["rating"] == "liked"]
    disliked = [r for r in recipes if r["rating"] == "disliked"]
    excluded = [r for r in recipes if r["temporarily_excluded"]]
    deviation_notes = 0
    conn = get_conn()
    deviation_notes = conn.execute(
        "SELECT COUNT(*) AS c FROM recipe_notes WHERE household_id = ? AND note_type = 'deviation'",
        (HOUSEHOLD_ID,),
    ).fetchone()["c"]
    conn.close()
    return {
        "recipes_tracked": len(recipes),
        "recipes_liked": len(liked),
        "recipes_disliked": len(disliked),
        "recipes_temporarily_excluded": len(excluded),
        "cooking_deviations_logged": deviation_notes,
        "liked_recipe_names": [r["name"] for r in liked],
        "disliked_recipe_names": [r["name"] for r in disliked],
    }
