"""
The notification feed and the learning summary behind it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from ..db import get_conn
from ._shared import current_member, household_id
from . import inventory as _inventory
from . import recipes as _recipes
from . import weekly_plan as _weekly_plan


def _today() -> date:
    """
    Today where the household lives. Lazily imported because cooker reaches
    this module, so `from . import cooker` at module scope would be a cycle
    — the same shape held._today and inventory._today use.

    Both readers below decide a CALENDAR DAY. The dismissal key is the one
    with teeth: on the server's clock it rolls over at about 8pm local, so a
    household that tapped a notification away at 7:55 was shown it again at
    8:01 under a key naming tomorrow.

    Deliberately NOT applied to the `datetime.utcnow()` a few lines further
    down, which measures how long ago a plan was created against SQLite's
    own UTC `created_at`. That is a UTC instant compared against a UTC
    instant, as a DURATION; there is no calendar day in it to be wrong
    about, and converting it would make the two sides disagree.
    """
    from .cooker import household_today

    return household_today()


# Live, in-app "what needs your attention" feed — see schema.sql's comment
# on notification_dismissals for why this isn't real scheduled push.
# Covers 3 of the 4 spec'd types (dinner nudge, expiring soon, weekly plan
# ready); the 4th ("the other adult changed something") is not computed
# here — see README's Phase 5 notes for why.
def _dismissed_keys(conn) -> set:
    rows = conn.execute("SELECT key FROM notification_dismissals WHERE household_id = ?", (household_id(),)).fetchall()
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
    # dinner-gap DETECTION the Today needs-you band already uses, so the
    # two never disagree about which nights are open.
    #
    # What is SHOWN can still differ, and the exception is written down
    # here rather than left for somebody to find, because a comment of
    # exactly this shape is what was believed for months below: dismissing
    # a notification silences the bell and leaves the band's card standing
    # where it was. That is what a dismissal means in this feed (see
    # schema.sql's notification_dismissals comment), not the two disagreeing
    # about a night.
    #
    # BOTH of the band's dinner shapes, which is what makes that sentence
    # true. It said it from the day it was written and the very next line
    # made it false until 2026-09-17: `dinner_decision` is a night with no
    # dinner row at all, `dinner_open` is a night the app deliberately
    # handed back with a reason (the Review stepper's "−", a generation
    # gap it could not settle, an away night undone). Both are a decision
    # waiting on the household, and filtering to the first left an open
    # dinner with a card on Now, no bell, and no morning text. A
    # `planned_empty` night — nobody home — is neither, and the band never
    # offers one, so it stays silent here by construction.
    #
    # No day word is computed here, deliberately. The band item's own
    # title already carries it ("Tonight needs a dinner", "Tomorrow's
    # dinner needs your call"), worked out on the HOUSEHOLD's clock. A
    # `day_label` off date.today() sat here unused from the day this was
    # written; it read like the server-clock bug class this repo has spent
    # a week sweeping and it was simply dead, so it went rather than being
    # moved onto household_today(). Don't reinstate it: a second day word
    # is a second answer to a question the title already answers, and on
    # the wrong clock.
    for item in _weekly_plan.get_needs_you_items():
        kind = item.get("type")
        if kind not in ("dinner_decision", "dinner_open"):
            continue
        # Separate keys on purpose. The two are different news about the
        # same night — "you haven't decided" against "I couldn't, and
        # here's why" — and a night really can turn from the first into
        # the second, since generating a week over an undecided night
        # fills it as an open question. One key would let this morning's
        # dismissal silence this afternoon's different ask. `dinner_gap:`
        # is left exactly as it was for the decision shape, so every
        # dismissal already on record keeps working.
        key = f"dinner_gap:{item['date']}" if kind == "dinner_decision" else f"dinner_open:{item['date']}"
        if key in dismissed:
            continue
        if kind == "dinner_open":
            # The app wrote a sentence when it opened this slot
            # (plan_slot_open's open_reason names the constraint), and the
            # card on Now already shows it. Saying anything else here is
            # the bell and the band disagreeing about one night.
            #
            # The fallback names no control and no day, and both halves of
            # that are deliberate. It must not promise "options": the label
            # two lines down goes out of its way NOT to say that word when
            # there are none, and a body promising them underneath it would
            # be the same small lie by another route. And it must not say
            # "tonight's" — this card is the SOONEST unsettled dinner,
            # which is tomorrow's about as often as it is tonight's, and
            # its own title already says which. ("Tell me what you'd like"
            # is the card's own button for an open slot with nothing to
            # tap.) The decision branch below still has both of those and
            # they are pre-existing; what this branch must not do is add a
            # second copy of them.
            #
            # Unreachable today rather than merely unlikely, and that was
            # checked rather than assumed: plan_slot_open raises on a blank
            # reason, all three INSERTs into meal_plan_entries hardcode
            # slot_state, and nothing in app/ UPDATEs it — so no row can
            # reach here `open` with nothing to say.
            body = item.get("body") or "Take a look and tell me what you'd like."
            # "Show options" only when there are any: the commonest open
            # slot has none (drop_dish_from_day plans one with no options
            # at all) and its card offers "Tell me what you'd like
            # instead" in their place. A label naming a control that isn't
            # on the screen is exactly the small promise §8 rules out.
            action_label = "Show options" if item.get("options") else "Take a look"
        else:
            # KNOWN AND LEFT, its own card, older than this change and
            # untouched by it: _suggest_quick_dinners returns {meal,
            # minutes}, so .get("name") is always None and this body has
            # always been the fallback. Fixing it changes what the
            # decision bell says, which this ticket is not about.
            first_option = (item.get("options") or [{}])[0].get("name") if item.get("options") else None
            body = f"The quickest option is {first_option}." if first_option else "Nothing planned yet — take a look at tonight's options."
            action_label = "Show options"
        out.append({
            "key": key, "type": kind,
            "title": item["title"],
            "body": body,
            "tab": "today", "action_label": action_label,
        })
        break

    # 2. Expiring soon (NOTIFICATIONS.md #2) — 2-day window per spec (the
    # Kitchen/Inventory badge itself uses 4 days; this notification is
    # deliberately tighter, matching the spec's own trigger).
    expiring = _inventory.get_expiring_soon(days=2)
    if expiring:
        today_iso = _today().isoformat()
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
        (household_id(), _today().isoformat()),
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

    # 4. The other adult settled the week (NOTIFICATIONS.md #4). Approval
    # records WHO said yes (weekly_plans.approved_by, and since 2026-09-11
    # approved_by_member_id — see approve_weekly_plan), and the session now
    # knows which adult is reading (tools.current_member). So this is
    # addressed, not broadcast: the adult who approved does not get told
    # that they approved. Everyone else in the household sees it, which
    # for a two-adult house is exactly "the other adult". The member id is
    # the comparison when there is one; the name is the fallback for a
    # plan approved before ids were stored. A device with nobody picked
    # still sees it — better a settled week told twice than not at all.
    #
    # Dismissals stay household-wide (notification_dismissals has no
    # member column): once anyone taps it away it is gone for the house.
    approved_row = conn.execute(
        "SELECT id, week_start_date, approved_by, approved_by_member_id, approved_at FROM weekly_plans "
        "WHERE household_id = ? AND status = 'approved' AND TRIM(approved_by) != '' AND approved_at IS NOT NULL "
        "ORDER BY approved_at DESC LIMIT 1",
        (household_id(),),
    ).fetchone()
    if approved_row and _is_the_approver(approved_row, current_member()):
        approved_row = None
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
                # Points at Grocery, not Meals — the shopping list is the
                # thing this notification just said got built, so "See the
                # list" is the step that's actually waiting, not another
                # look at the plan that's already settled (loop-handoffs
                # slice 1).
                "tab": "grocery", "action_label": "See the list",
            })
    conn.close()
    return out


def _is_the_approver(approved_row, member: dict | None) -> bool:
    """Is the adult reading this feed the one who approved the week?"""
    if not member:
        return False
    stored_id = approved_row["approved_by_member_id"]
    if stored_id is not None:
        return int(stored_id) == int(member["id"])
    return (approved_row["approved_by"] or "").strip().lower() == member["name"].strip().lower()


def dismiss_notification(key: str) -> dict:
    """Mark one notification key dismissed so it stops showing until its underlying condition changes (a new date/plan id)."""
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO notification_dismissals (household_id, key) VALUES (?, ?)",
        (household_id(), key),
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
        (household_id(),),
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
