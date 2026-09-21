"""
The evening cook nudge (Loop Board, 2026-09-21): one line at the start of
the household's dinner window, to the morning text's numbers.

app/tools/digest.py's evening section: `build_evening_nudge` reads tonight
off today_moves (as the morning text does), `run_evening_nudges_once` is the
pass the same in-process loop makes each tick, `send_evening_nudge` is the
one seam a push sender slots into later, and `members.evening_nudge_on` /
`members.evening_nudge_sent_on` are the switch and the once-a-day mark.

What is pinned here:

- the text for a plain night, a fridge-move-first night, a prep-first
  night, and a recipe with no minutes; nothing for no dinner, a night out,
  nobody home, a leftovers-only night, a dinner already done or started;
- the clock for each dinner_window answer and the default;
- once per person per local day across two passes and a "restart";
  the window's start and end; a dinner planned after the hour still gets
  its nudge on a later pass;
- the switches: the nudge's own, and the morning text's it rides on;
- no Twilio keys -> nothing is sent and the day is still marked;
- the endpoints, the migration, and the sheet's rows (source markers).

No test here reaches the network: every pass takes a stub sender, and
the one that takes the default has send_sms's channel monkeypatched.
"""
from __future__ import annotations

import datetime as dt
import re
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import db as app_db
from app import tools
from app.db import get_conn
from app.tools import digest
from app.tools import rhythm as _rhythm
from conftest import household_today

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
MAIN_PY = (REPO / "app" / "main.py").read_text(encoding="utf-8")

TODAY = household_today()
ISO_TODAY = TODAY.isoformat()
ISO_YESTERDAY = (TODAY - dt.timedelta(days=1)).isoformat()
WEEK_START = (TODAY - dt.timedelta(days=2)).isoformat()
TORONTO = ZoneInfo("America/Toronto")
LINK = "https://pomona.example"


# ---------- helpers ----------

def _adults(*names):
    for n in names or ("Emily", "Vineeth"):
        tools.add_member(n)
        tools.set_member_age_group(n, "adult")


def _member_id(name: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM members WHERE household_id = ? AND name = ?", (tools.household_id(), name)
    ).fetchone()
    conn.close()
    return row["id"]


def _entry_id(day: str = ISO_TODAY, slot: str = "dinner") -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


def _seed_dinner(*, minutes: bool = True, name: str = "Chicken Skewers") -> int:
    """Tonight's dinner on a plan: skewers, 10 + 25 minutes (or none)."""
    tools.add_recipe(
        name,
        ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
        prep_time_minutes=10 if minutes else None, cook_time_minutes=25 if minutes else None,
        default_servings=3,
    )
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(ISO_TODAY, name, slot="dinner", weekly_plan_id=plan_id, add_ingredients_to_grocery_list=False)
    return plan_id


def _prep_task(plan_id: int, description: str, task_type: str = "defrost") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
        "related_meal, status, task_type) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (tools.household_id(), plan_id, ISO_TODAY, description, "Chicken Skewers", task_type),
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def _opt_in(*names):
    numbers = {"Emily": "416-555-0100", "Vineeth": "(647) 555-0199"}
    for n in names or ("Emily", "Vineeth"):
        tools.set_morning_text(phone=numbers[n], on=True, name=n)


def _local(hour: int, minute: int = 0, zone=TORONTO, day: dt.date = TODAY) -> datetime:
    """A wall-clock moment in `zone`, handed to the loop as UTC."""
    return datetime.combine(day, dt.time(hour, minute), tzinfo=zone).astimezone(timezone.utc)


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(TODAY, dt.time(hour, minute))


def _sent_on(name: str) -> str:
    conn = get_conn()
    row = conn.execute("SELECT evening_nudge_sent_on FROM members WHERE id = ?", (_member_id(name),)).fetchone()
    conn.close()
    return row["evening_nudge_sent_on"]


class _Sender:
    """A stub seam: remembers every (member, text), answers what it's told."""

    def __init__(self, status="ok", detail="twilio SMabc"):
        self.calls: list[tuple[dict, str]] = []
        self.status, self.detail = status, detail

    def __call__(self, member, text):
        self.calls.append((member, text))
        return {"status": self.status, "detail": self.detail}


@pytest.fixture
def link(monkeypatch):
    monkeypatch.setenv("HOME_MANAGER_URL", LINK)


@pytest.fixture
def no_twilio(monkeypatch):
    for key in digest.TWILIO_ENV:
        monkeypatch.delenv(key, raising=False)


# ---------- the text ----------

def test_a_plain_night_is_the_dish_the_minutes_and_a_tap_into_cook(link):
    _adults()
    _seed_dinner()
    text = tools.build_evening_nudge(_at(17, 0))
    assert text == f"Tonight: Chicken Skewers — 35 min. Tap to start. {LINK}/kitchen"
    assert "\n" not in text and "!" not in text


def test_a_recipe_with_no_minutes_leaves_the_minutes_out(link):
    _adults()
    _seed_dinner(minutes=False)
    assert tools.build_evening_nudge(_at(17, 0)) == f"Tonight: Chicken Skewers. Tap to start. {LINK}/kitchen"


def test_no_link_when_no_url_is_configured(monkeypatch):
    monkeypatch.delenv("HOME_MANAGER_URL", raising=False)
    monkeypatch.setattr(digest, "PUBLIC_BASE_URL", "")
    _adults()
    _seed_dinner()
    assert tools.build_evening_nudge(_at(17, 0)) == "Tonight: Chicken Skewers — 35 min. Tap to start."


def test_a_fridge_move_still_to_do_comes_first(link):
    _adults()
    plan_id = _seed_dinner()
    _prep_task(plan_id, "Move the chicken thighs to the fridge — for Thursday’s skewers.")
    text = tools.build_evening_nudge(_at(17, 0))
    assert text == f"Move the chicken thighs to the fridge first — then Chicken Skewers. {LINK}/kitchen"


def test_a_prep_step_still_to_do_comes_first_too(link):
    _adults()
    plan_id = _seed_dinner()
    _prep_task(plan_id, "Marinate the chicken", task_type="prep")
    assert tools.build_evening_nudge(_at(17, 0)) == f"Marinate the chicken first — then Chicken Skewers. {LINK}/kitchen"


def test_a_fridge_move_already_done_does_not_come_first(link):
    _adults()
    plan_id = _seed_dinner()
    task_id = _prep_task(plan_id, "Move the chicken thighs to the fridge — for Thursday’s skewers.")
    tools.check_off_prep_step(task_id, "done")
    assert tools.build_evening_nudge(_at(17, 0)).startswith("Tonight: Chicken Skewers")


def test_no_dinner_planned_is_no_nudge():
    _adults()
    tools.create_weekly_plan(WEEK_START)
    assert tools.build_evening_nudge(_at(17, 0)) is None
    assert tools.tonight_for_nudge(_at(17, 0))["reason"] == "no_dinner"


def test_no_plan_at_all_is_no_nudge():
    _adults()
    assert tools.build_evening_nudge(_at(17, 0)) is None


def test_a_night_out_is_no_nudge():
    """"Not tonight — we're going out" leaves tonight planned_empty; the
    nudge has nothing to start."""
    _adults()
    _seed_dinner()
    out = tools.tonight_night_off(now=_at(15, 0))
    assert out["status"] != "refused", out
    assert tools.build_evening_nudge(_at(17, 0)) is None
    assert tools.tonight_for_nudge(_at(17, 0))["reason"] == "no_dinner"


def test_nobody_home_for_dinner_is_no_nudge():
    _adults()
    _seed_dinner()
    tools.set_slot_attendance(ISO_TODAY, "dinner", present_member_ids=[])
    assert tools.build_evening_nudge(_at(17, 0)) is None
    assert tools.tonight_for_nudge(_at(17, 0))["reason"] == "away"


def test_a_leftovers_only_night_is_no_nudge():
    """A reheat is a line, never the card — and never a "Tap to start"."""
    _adults()
    tools.add_recipe("Bulgogi", ingredients=[{"item": "Beef", "qty": "1 lb"}], prep_time_minutes=10, cook_time_minutes=20)
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(ISO_YESTERDAY, "Bulgogi", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TODAY, "Bulgogi", slot="dinner", weekly_plan_id=plan_id)
    tools.set_cook_ahead(_entry_id(ISO_YESTERDAY), [_entry_id(ISO_TODAY)])
    moves = tools.today_moves(now=_at(17, 0))["moves"]
    assert [m["kind"] for m in moves if m["slot"] == "dinner"] == ["reheat"]
    assert tools.build_evening_nudge(_at(17, 0)) is None


def test_a_dinner_already_marked_done_is_no_nudge():
    _adults()
    _seed_dinner()
    tools.check_off_meal(_entry_id(), "done")
    assert tools.build_evening_nudge(_at(17, 0)) is None
    assert tools.tonight_for_nudge(_at(17, 0))["reason"] == "done"


def test_a_cook_already_started_is_no_nudge():
    _adults()
    _seed_dinner()
    tools.start_cooking(_entry_id(), now_utc=_local(16, 50))
    assert tools.build_evening_nudge(_at(17, 0)) is None
    assert tools.tonight_for_nudge(_at(17, 0))["reason"] == "started"


# ---------- the clock ----------

@pytest.mark.parametrize("window, clock", [
    ("5_6ish", time(17, 0)),
    ("6_8", time(18, 0)),
    ("later", time(19, 0)),
    ("all_over", time(17, 30)),
    ("", time(17, 30)),
    (None, time(17, 30)),
])
def test_the_nudge_goes_at_the_start_of_the_dinner_window(window, clock):
    assert tools.evening_nudge_clock(window) == clock


def test_every_clock_names_a_real_dinner_window_answer():
    assert set(tools.EVENING_NUDGE_CLOCK_BY_WINDOW) <= set(_rhythm.DINNER_WINDOWS)
    assert tools.EVENING_NUDGE_DEFAULT_CLOCK == time(17, 30)


def test_the_household_answer_moves_the_hour():
    _adults("Emily")
    _seed_dinner()
    _opt_in("Emily")
    tools.set_dinner_window("6_8")
    sender = _Sender()
    assert tools.run_evening_nudges_once(now_utc=_local(17, 30), send=sender) == []
    assert [r["status"] for r in tools.run_evening_nudges_once(now_utc=_local(18, 0), send=sender)] == ["ok"]
    assert tools.get_evening_nudge_settings()["clock"] == "18:00"


# ---------- the pass: once a night, on the household's clock ----------

def test_the_pass_nudges_each_opted_in_adult_once_and_a_restart_never_twice(link):
    _adults()
    _seed_dinner()
    _opt_in()
    sender = _Sender()

    results = tools.run_evening_nudges_once(now_utc=_local(17, 31), send=sender)

    assert [r["status"] for r in results] == ["ok", "ok"]
    assert sorted(m["phone"] for m, _ in sender.calls) == ["+14165550100", "+16475550199"]
    assert sender.calls[0][1] == sender.calls[1][1] == f"Tonight: Chicken Skewers — 35 min. Tap to start. {LINK}/kitchen"
    assert _sent_on("Emily") == ISO_TODAY and _sent_on("Vineeth") == ISO_TODAY

    # The next poll, and a container that restarts inside the window.
    assert tools.run_evening_nudges_once(now_utc=_local(17, 36), send=sender) == []
    assert tools.run_evening_nudges_once(now_utc=_local(18, 5), send=sender) == []
    assert len(sender.calls) == 2


def test_before_the_window_and_well_after_it_nothing_goes():
    _adults("Emily")
    _seed_dinner()
    _opt_in("Emily")
    sender = _Sender()
    assert tools.run_evening_nudges_once(now_utc=_local(17, 29), send=sender) == []
    late = _local(17, 30) + dt.timedelta(minutes=tools.EVENING_NUDGE_LATE_WINDOW_MINUTES + 1)
    assert tools.run_evening_nudges_once(now_utc=late, send=sender) == []
    assert sender.calls == [] and _sent_on("Emily") == ""


def test_a_dinner_planned_after_the_hour_still_gets_its_nudge():
    """A pass with nothing to say marks nothing, so the household that
    decides on dinner at twenty to six is still nudged."""
    _adults("Emily")
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    _opt_in("Emily")
    sender = _Sender()
    assert tools.run_evening_nudges_once(now_utc=_local(17, 30), send=sender) == []
    assert _sent_on("Emily") == ""
    tools.add_recipe("Quick Eggs", ingredients=[{"item": "Eggs", "qty": "6"}], prep_time_minutes=5, cook_time_minutes=5)
    tools.plan_meal(ISO_TODAY, "Quick Eggs", slot="dinner", weekly_plan_id=plan_id)
    assert [r["status"] for r in tools.run_evening_nudges_once(now_utc=_local(17, 40), send=sender)] == ["ok"]
    assert sender.calls[0][1].startswith("Tonight: Quick Eggs — 10 min.")


def test_the_household_clock_decides_the_evening():
    _adults("Emily")
    _seed_dinner()
    tools.set_morning_text(phone="6045550100", on=True, timezone="America/Vancouver", name="Emily")
    sender = _Sender()
    assert tools.run_evening_nudges_once(now_utc=_local(17, 30, zone=TORONTO), send=sender) == []
    results = tools.run_evening_nudges_once(now_utc=_local(17, 30, zone=ZoneInfo("America/Vancouver")), send=sender)
    assert [r["status"] for r in results] == ["ok"]
    assert _sent_on("Emily") == ISO_TODAY


def test_the_nudge_switch_and_the_morning_switch_both_gate_it():
    _adults()
    _seed_dinner()
    _opt_in()
    tools.set_evening_nudge_for_member(_member_id("Vineeth"), on=False)
    sender = _Sender()
    tools.run_evening_nudges_once(now_utc=_local(17, 30), send=sender)
    assert [m["name"] for m, _ in sender.calls] == ["Emily"]
    assert _sent_on("Vineeth") == ""

    # Morning text off: the nudge is off with it, whatever its own switch says.
    tools.set_morning_text(on=False, name="Emily")
    conn = get_conn()
    conn.execute("UPDATE members SET evening_nudge_sent_on = ''")
    conn.commit()
    conn.close()
    assert tools.run_evening_nudges_once(now_utc=_local(17, 30), send=sender) == []
    assert len(sender.calls) == 1


def test_a_failed_send_is_marked_and_not_retried_every_five_minutes(caplog):
    _adults("Emily")
    _seed_dinner()
    _opt_in("Emily")
    sender = _Sender(status="failed", detail="HTTP 400 21211 The 'To' number +14165550100 is not valid")
    with caplog.at_level("WARNING", logger="home_manager"):
        results = tools.run_evening_nudges_once(now_utc=_local(17, 30), send=sender)
    assert [r["status"] for r in results] == ["failed"]
    assert _sent_on("Emily") == ISO_TODAY
    assert tools.run_evening_nudges_once(now_utc=_local(17, 35), send=sender) == []
    assert not any("4165550100" in r.getMessage() for r in caplog.records)


def test_without_twilio_keys_nothing_is_sent_and_the_night_is_still_marked(no_twilio, monkeypatch):
    """The default seam is the morning text's channel; with no keys that
    channel declines, and the day is marked so the loop moves on."""
    _adults("Emily")
    _seed_dinner()
    _opt_in("Emily")
    seen = []
    monkeypatch.setitem(digest.CHANNELS, "text", lambda to, body: seen.append((to, body)) or {"status": "skipped-no-keys", "detail": "no keys"})
    results = tools.run_evening_nudges_once(now_utc=_local(17, 30))
    assert [r["status"] for r in results] == ["skipped-no-keys"]
    assert seen[0][0] == "+14165550100" and seen[0][1].startswith("Tonight: Chicken Skewers")
    assert _sent_on("Emily") == ISO_TODAY


def test_the_seam_is_one_function_a_push_sender_can_replace(monkeypatch):
    calls = []
    monkeypatch.setitem(digest.CHANNELS, "text", lambda to, body: calls.append(to) or {"status": "ok", "detail": ""})
    assert tools.send_evening_nudge({"member_id": 1, "name": "Emily", "phone": "+14165550100"}, "Tonight: tacos.")["status"] == "ok"
    assert calls == ["+14165550100"]


def test_two_households_never_see_each_others_evening():
    """The Beta Testers have no dinner tonight; only Emily's house is nudged,
    and only her row is marked."""
    from app import households
    _adults("Emily")
    _seed_dinner()
    _opt_in("Emily")
    other = households.create_household("The Beta Testers", "a-safe-distinct-passphrase")
    with tools.use_household(other):
        _adults("Julia")
        tools.create_weekly_plan(WEEK_START)
        tools.set_morning_text(phone="9055550100", on=True)
    sender = _Sender()
    results = tools.run_evening_nudges_once(now_utc=_local(17, 30), send=sender)
    assert [(r["household_id"], r["status"]) for r in results] == [(1, "ok")]
    with tools.use_household(other):
        assert _sent_on("Julia") == ""


# ---------- the loop ----------

def test_the_startup_loop_makes_both_passes_on_one_tick():
    body = MAIN_PY[MAIN_PY.index("async def start_morning_text_loop()"):MAIN_PY.index("@app.get(\"/api/onboarding/status\")")]
    assert "tools.run_morning_texts_once" in body
    assert "tools.run_evening_nudges_once" in body
    assert body.count("asyncio.create_task(_loop())") == 1


# ---------- the endpoints ----------

def test_the_settings_read_defaults_on_for_whoever_has_the_morning_text(signed_in):
    _adults()
    _opt_in("Emily")
    res = signed_in.get("/api/evening-nudge")
    assert res.status_code == 200
    data = res.json()
    assert data["clock"] == "17:30"
    by_name = {a["name"]: a for a in data["adults"]}
    assert by_name["Emily"] == {"member_id": _member_id("Emily"), "name": "Emily", "on": True, "morning_on": True, "active": True}
    assert by_name["Vineeth"]["on"] is True and by_name["Vineeth"]["active"] is False


def test_the_switch_saves_by_member(signed_in):
    _adults()
    _opt_in("Emily")
    res = signed_in.post("/api/evening-nudge", json={"member_id": _member_id("Emily"), "on": False})
    assert res.status_code == 200
    assert res.json()["on"] is False and res.json()["active"] is False
    assert {a["name"]: a["on"] for a in res.json()["settings"]["adults"]} == {"Emily": False, "Vineeth": True}
    res = signed_in.post("/api/evening-nudge", json={"member_id": _member_id("Emily"), "on": True})
    assert res.json()["active"] is True


def test_a_foreign_or_child_member_is_refused(signed_in):
    _adults("Emily")
    tools.add_member("Kid")
    tools.set_member_age_group("Kid", "child")
    assert signed_in.post("/api/evening-nudge", json={"member_id": 999999, "on": True}).status_code == 404
    res = signed_in.post("/api/evening-nudge", json={"member_id": _member_id("Kid"), "on": True})
    assert res.status_code == 400
    assert "adult" in res.json()["detail"]


# ---------- the migration and the sheet ----------

def test_the_migration_is_present_and_members_are_wiped_between_tests():
    cols = {(t, c): typ for t, c, typ in app_db._MIGRATIONS}
    assert cols[("members", "evening_nudge_on")] == "INTEGER NOT NULL DEFAULT 1"
    assert cols[("members", "evening_nudge_sent_on")] == "TEXT NOT NULL DEFAULT ''"
    from tests.conftest import _TABLES
    assert "members" in _TABLES
    _adults("Emily")
    assert tools.get_evening_nudge_settings()["adults"][0]["on"] is True


def test_the_morning_sheet_carries_the_evening_switch():
    assert "fetch('/api/evening-nudge')" in SHELL_JS
    render = SHELL_JS[SHELL_JS.index("function renderMorningSheet()"):SHELL_JS.index("async function saveMorningSheet()")]
    assert "data-evening-toggle" in render and "Evening nudge" in render
    save = SHELL_JS[SHELL_JS.index("async function saveMorningSheet()"):SHELL_JS.index("function openMorningSheet()")]
    assert "fetch('/api/evening-nudge', { method: 'POST'" in save
    assert "break;" not in save
    # The toggle is the same 44px button as the morning one, and the row
    # is drawn in tokens only.
    section = SHELL_CSS[SHELL_CSS.index('"Morning text" sheet'):SHELL_CSS.index("Meals — Week / Day / Meal")]
    assert ".morning-evening-row { min-height: 44px; }" in section
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", section)
