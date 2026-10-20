"""
The public share link carries the menu and nothing else (defect hunt,
2026-10-11).

/api/share/<token> needs no sign-in: anyone holding the link — a
grandparent, a babysitter, whoever it was forwarded to — gets its JSON. It
used to be the whole of get_weekly_plan with `packed_as` popped, so it also
carried who approved the week, the week's own freeform asks ("out Thu/Fri"
— when the house is empty), the planner's reason for each meal ("nut-free
for Maya"), an open slot's sentence and every ingredient. share.html drew
none of it; the JSON still served it.
"""
from __future__ import annotations

import datetime
import re

from conftest import household_today

from app import tools
from app.db import get_conn


def _week_start() -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


def _shared_week():
    ws = _week_start()
    plan_id = tools.create_weekly_plan(ws, constraints_notes="out Thu/Fri at the cottage")["weekly_plan_id"]
    tools.add_recipe("Peanut Noodles", [{"item": "secret-sauce-ingredient", "qty": "1 cup"}])
    tools.plan_meal(ws, "Peanut Noodles", slot="dinner", weekly_plan_id=plan_id)
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET reasoning = 'nut-free for Maya at school' WHERE weekly_plan_id = ?",
        (plan_id,),
    )
    conn.execute(
        "UPDATE weekly_plans SET status = 'approved', approved_by = 'Vineeth', approved_at = datetime('now') WHERE id = ?",
        (plan_id,),
    )
    conn.commit()
    conn.close()
    return ws, tools.get_or_create_share_link()["token"]


def test_the_public_link_shows_the_menu(client):
    ws, token = _shared_week()
    client.cookies.clear()
    res = client.get(f"/api/share/{token}")
    assert res.status_code == 200
    plan = res.json()
    assert plan["household_name"] == "My Household"
    assert plan["week_start_date"] == ws
    assert plan["weekly_plan_id"]
    assert [m["meal"] for m in plan["meals"]] == ["Peanut Noodles"]
    day = next(d for d in plan["menu"] if d["date"] == ws)
    assert day["dinner"] == "Peanut Noodles"
    assert plan["menu_is_suggested"] is False
    # No line written to the household goes out (2026-10-20).
    assert "new_recipe_count" not in plan and "is_first_plan" not in plan


def test_the_public_link_carries_nothing_about_the_household(client):
    _, token = _shared_week()
    client.cookies.clear()
    text = client.get(f"/api/share/{token}").text
    for private in ("Vineeth", "cottage", "Maya", "secret-sauce-ingredient"):
        assert private not in text, f"{private!r} reached the public share link"
    plan = client.get(f"/api/share/{token}").json()
    for key in ("approved_by", "constraints_notes", "intake_id", "status"):
        assert key not in plan
    assert set(plan["meals"][0]) == {"date", "slot", "meal"}


def test_every_field_the_share_page_reads_is_still_sent(client):
    """The allowlist must not starve share.html: each `plan.<key>` the page
    reads is in the payload (components only on a component-based week)."""
    _, token = _shared_week()
    client.cookies.clear()
    plan = client.get(f"/api/share/{token}").json()
    html = client.get(f"/share/{token}").text
    read = set(re.findall(r"\bplan\.([a-z_]+)", html))
    assert read, "share.html no longer reads plan.<key> — update this test"
    assert read - {"components"} <= set(plan), read - set(plan)
