"""
The morning text — "Reach me before the moment" (Loop Board, 2026-09-11),
step 0 aside (that one is tests/test_bell_refresh.py).

app/tools/digest.py: one digest builder off today_moves and the live
notification feed, one channel seam (text via Twilio, stdlib only), and one
daily pass the in-process loop in app/main.py calls every few minutes.

What matters and is pinned here:

- the text for a seeded day (a cook, a fridge move, a shop) is one short
  message under the cap, in plain words, with the link last;
- an empty day sends nothing at all;
- once a day, per person, per LOCAL day — a second pass, a restart, a
  Vancouver household at Toronto's seven: none of them text twice or early;
- no Twilio keys -> nothing is sent and the reason is recorded;
- Twilio's HTTP call is stubbed for both the happy path and the failure,
  and neither the auth token nor the text ever reaches the sends table;
- numbers are normalised to E.164, and an off number is never texted;
- two households never see each other's numbers or send state;
- the Preferences row and its sheet exist in shell.js (source markers —
  see tests/test_grocery_steps.py's docstring for what a marker is worth).

No test here ever reaches the network: send_sms is exercised with
urllib.request.urlopen monkeypatched, and everything else takes a stub.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import agent, households, tools
from app.db import get_conn
from app.tools import digest

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

TODAY = dt.date.today()
ISO_TODAY = TODAY.isoformat()
WEEK_START = (TODAY - dt.timedelta(days=2)).isoformat()
TORONTO = ZoneInfo("America/Toronto")


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


def _seed_day(*, cook=True, fridge=True, shop=True):
    """A day with something on it: skewers tonight, thighs to move, a list."""
    tools.add_recipe(
        "Chicken Skewers",
        ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
        prep_time_minutes=10, cook_time_minutes=25, default_servings=3,
    )
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    if cook:
        tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    if fridge:
        conn = get_conn()
        conn.execute(
            "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
            "related_meal, status, task_type) VALUES (?, ?, ?, ?, ?, 'pending', 'defrost')",
            (tools.household_id(), plan_id, ISO_TODAY,
             "Move the chicken thighs to the fridge — for Thursday’s skewers.", "Chicken Skewers"),
        )
        conn.commit()
        conn.close()
    if shop:
        tools.add_grocery_item("Chicken Thighs", quantity="1 lb")
    return plan_id


def _local(hour: int, minute: int = 0, zone=TORONTO, day: dt.date = TODAY) -> datetime:
    """A wall-clock moment in `zone`, handed to the loop as UTC."""
    return datetime.combine(day, dt.time(hour, minute), tzinfo=zone).astimezone(timezone.utc)


def _rows(hid: int | None = None) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT household_id, member_id, sent_on, status, detail FROM morning_text_sends "
        "WHERE household_id = ? ORDER BY id",
        (hid or tools.household_id(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


class _Sender:
    """A stub channel: remembers every call, answers what it's told to."""

    def __init__(self, status="ok", detail="twilio SMabc"):
        self.calls: list[tuple[str, str]] = []
        self.status, self.detail = status, detail

    def __call__(self, to, body):
        self.calls.append((to, body))
        return {"status": self.status, "detail": self.detail}


@pytest.fixture
def twilio_env(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret-token-never-logged")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+16475550100")


@pytest.fixture
def no_twilio(monkeypatch):
    for key in digest.TWILIO_ENV:
        monkeypatch.delenv(key, raising=False)


# ---------- the digest ----------

def test_a_seeded_day_is_one_short_message_in_plain_words(monkeypatch):
    monkeypatch.setenv("HOME_MANAGER_URL", "https://pomona.example")
    _adults()
    _seed_day()

    text = tools.build_morning_text(datetime.combine(TODAY, dt.time(7, 0)))

    assert text is not None
    assert text.startswith("Tonight: Chicken Skewers.")
    assert "Move the chicken thighs to the fridge — for Thursday’s skewers." in text
    assert "Shop for tonight — 1 item, by" in text
    assert text.endswith(" https://pomona.example/")
    assert len(text) <= tools.MORNING_TEXT_MAX_CHARS
    assert "\n" not in text, "a text is a line, not a page"
    assert "!" not in text
    assert "Action required" not in text and "Task" not in text


def test_no_link_when_no_url_is_configured(monkeypatch):
    monkeypatch.delenv("HOME_MANAGER_URL", raising=False)
    monkeypatch.setattr(digest, "PUBLIC_BASE_URL", "")
    _adults()
    _seed_day(fridge=False, shop=False)

    text = tools.build_morning_text(datetime.combine(TODAY, dt.time(7, 0)))
    assert text == "Tonight: Chicken Skewers."


def test_an_empty_day_is_no_text_at_all():
    _adults()
    tools.create_weekly_plan(WEEK_START)
    assert tools.build_morning_text(datetime.combine(TODAY, dt.time(7, 0))) is None


def test_a_done_move_is_not_in_the_text():
    """A late send after someone already cooked: nothing the Today screen
    would still be asking for."""
    _adults()
    _seed_day(fridge=False, shop=False)
    conn = get_conn()
    entry = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = 'dinner'",
        (tools.household_id(), ISO_TODAY),
    ).fetchone()["id"]
    conn.close()
    tools.check_off_meal(entry, "done")
    text = tools.build_morning_text(datetime.combine(TODAY, dt.time(7, 0)))
    # What's left is the Today screen's own "how did it go?" nudge — real
    # content — and not a cook that already happened.
    assert "Tonight" not in (text or "")
    assert text == "How did Chicken Skewers go?" or text is None


def test_tomorrows_dinner_gap_is_not_tonights():
    """The feed's dinner nudge covers tonight or tomorrow; only tonight's
    belongs in today's text. A planned tonight and an empty tomorrow say
    nothing about tomorrow."""
    _adults()
    _seed_day(fridge=False, shop=False)
    text = tools.build_morning_text(datetime.combine(TODAY, dt.time(7, 0)))
    assert "still open" not in text


def test_tonights_gap_is_said_plainly_with_no_exclamation():
    _adults()
    tools.add_recipe("Quick Eggs", ingredients=[{"item": "Eggs", "qty": "6"}], prep_time_minutes=5, cook_time_minutes=5)
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal((TODAY + dt.timedelta(days=1)).isoformat(), "Quick Eggs", slot="dinner", weekly_plan_id=plan_id)
    text = tools.build_morning_text(datetime.combine(TODAY, dt.time(7, 0)))
    assert text is not None and text.startswith("Tonight's still open — nothing's planned yet.")
    assert "!" not in text


def test_the_text_never_outgrows_two_segments(monkeypatch):
    monkeypatch.setenv("HOME_MANAGER_URL", "https://pomona.example")
    _adults()
    tools.add_recipe("A" * 120, ingredients=[{"item": "Rice", "qty": "1 cup"}], prep_time_minutes=5, cook_time_minutes=5)
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(ISO_TODAY, "A" * 120, slot="dinner", weekly_plan_id=plan_id)
    conn = get_conn()
    for i in range(6):
        conn.execute(
            "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
            "related_meal, status, task_type) VALUES (?, ?, ?, ?, ?, 'pending', 'defrost')",
            (tools.household_id(), plan_id, ISO_TODAY, f"Move the thing number {i} to the fridge — for Sunday’s dinner {'x' * 40}.", "x"),
        )
    conn.commit()
    conn.close()

    text = tools.build_morning_text(datetime.combine(TODAY, dt.time(7, 0)))
    assert len(text) <= tools.MORNING_TEXT_MAX_CHARS
    assert text.startswith("Tonight: " + "A" * 120)
    assert text.endswith("https://pomona.example/")


# ---------- the pass: once a day, on the household's clock ----------

def test_the_pass_texts_each_opted_in_adult_once_and_records_it(twilio_env):
    _adults()
    _seed_day()
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    tools.set_morning_text(phone="(647) 555-0199", on=True, name="Vineeth")
    sender = _Sender()

    results = tools.run_morning_texts_once(now_utc=_local(7, 3), send=sender)

    assert [r["status"] for r in results] == ["ok", "ok"]
    assert sorted(to for to, _ in sender.calls) == ["+14165550100", "+16475550199"]
    assert sender.calls[0][1] == sender.calls[1][1], "one digest, every number"
    rows = _rows()
    assert [(r["member_id"], r["sent_on"], r["status"]) for r in rows] == [
        (_member_id("Emily"), ISO_TODAY, "ok"), (_member_id("Vineeth"), ISO_TODAY, "ok"),
    ]

    # The same morning again — a restart, the next poll — sends nothing.
    again = tools.run_morning_texts_once(now_utc=_local(7, 9), send=sender)
    assert again == []
    assert len(sender.calls) == 2


def test_before_the_household_hour_nothing_happens(twilio_env):
    _adults()
    _seed_day()
    tools.set_morning_text(phone="4165550100", on=True, time="7:30am", name="Emily")
    sender = _Sender()

    assert tools.run_morning_texts_once(now_utc=_local(7, 20), send=sender) == []
    assert sender.calls == [] and _rows() == []

    assert [r["status"] for r in tools.run_morning_texts_once(now_utc=_local(7, 31), send=sender)] == ["ok"]


def test_the_household_clock_decides_the_morning(twilio_env):
    """Vancouver at seven is Toronto at ten. A Vancouver household set to
    seven is not texted at Toronto's seven (their four in the morning), and
    is texted at theirs. `sent_on` is their date, not the server's."""
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="6045550100", on=True, timezone="America/Vancouver", name="Emily")
    sender = _Sender()
    vancouver = ZoneInfo("America/Vancouver")

    assert tools.run_morning_texts_once(now_utc=_local(7, 0, zone=TORONTO), send=sender) == []
    results = tools.run_morning_texts_once(now_utc=_local(7, 0, zone=vancouver), send=sender)
    assert [r["status"] for r in results] == ["ok"]
    assert _rows()[0]["sent_on"] == TODAY.isoformat()


def test_a_missed_morning_is_sent_once_when_the_app_is_back_the_same_day(twilio_env):
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="4165550100", on=True, name="Emily")
    sender = _Sender()

    # Down at seven, back at ten: one text.
    assert [r["status"] for r in tools.run_morning_texts_once(now_utc=_local(10, 0), send=sender)] == ["ok"]
    assert tools.run_morning_texts_once(now_utc=_local(10, 5), send=sender) == []
    assert len(sender.calls) == 1


def test_a_morning_missed_by_too_much_is_recorded_not_sent(twilio_env):
    """Back at nine at night is not a morning text. The day gets a row so
    the report can say so and the next pass doesn't keep trying."""
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="4165550100", on=True, name="Emily")
    sender = _Sender()

    results = tools.run_morning_texts_once(now_utc=_local(21, 0), send=sender)
    assert [r["status"] for r in results] == ["skipped-late"]
    assert sender.calls == []
    assert _rows()[0]["status"] == "skipped-late"
    assert tools.run_morning_texts_once(now_utc=_local(21, 5), send=sender) == []


def test_a_day_with_nothing_to_say_records_the_skip_and_sends_nothing(twilio_env):
    _adults("Emily")
    tools.create_weekly_plan(WEEK_START)
    tools.set_morning_text(phone="4165550100", on=True, name="Emily")
    sender = _Sender()

    results = tools.run_morning_texts_once(now_utc=_local(7, 0), send=sender)
    assert [r["status"] for r in results] == ["skipped-empty"]
    assert sender.calls == []
    assert _rows()[0]["status"] == "skipped-empty"


def test_a_number_turned_off_is_never_texted(twilio_env):
    _adults()
    _seed_day()
    tools.set_morning_text(phone="4165550100", on=True, name="Emily")
    tools.set_morning_text(phone="6475550199", on=False, name="Vineeth")
    sender = _Sender()

    tools.run_morning_texts_once(now_utc=_local(7, 0), send=sender)
    assert [to for to, _ in sender.calls] == ["+14165550100"]

    tools.set_morning_text(on=False, name="Emily")
    conn = get_conn()
    conn.execute("DELETE FROM morning_text_sends")
    conn.commit()
    conn.close()
    assert tools.run_morning_texts_once(now_utc=_local(7, 0), send=sender) == []
    assert len(sender.calls) == 1


def test_a_failed_send_is_recorded_and_the_next_number_still_goes(twilio_env):
    _adults()
    _seed_day()
    tools.set_morning_text(phone="4165550100", on=True, name="Emily")
    tools.set_morning_text(phone="6475550199", on=True, name="Vineeth")
    calls = []

    def flaky(to, body):
        calls.append(to)
        if to == "+14165550100":
            return {"status": "failed", "detail": "HTTP 400 21608 The number is unverified"}
        return {"status": "ok", "detail": "twilio SM123"}

    results = tools.run_morning_texts_once(now_utc=_local(7, 0), send=flaky)
    assert sorted(r["status"] for r in results) == ["failed", "ok"]
    assert len(calls) == 2
    by_member = {r["member_id"]: r for r in _rows()}
    assert by_member[_member_id("Emily")]["status"] == "failed"
    assert "unverified" in by_member[_member_id("Emily")]["detail"]
    assert by_member[_member_id("Vineeth")]["status"] == "ok"


def test_a_channel_that_raises_is_recorded_as_failed_not_crashed(twilio_env, monkeypatch):
    """send_digest is the seam every real channel goes through; an
    exception inside a channel becomes a failed row, and the pass goes on."""
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="4165550100", on=True)

    def boom(to, body):
        raise RuntimeError("twilio exploded")

    monkeypatch.setitem(digest.CHANNELS, "text", boom)
    results = tools.run_morning_texts_once(now_utc=_local(7, 0))
    assert [r["status"] for r in results] == ["failed"]
    assert _rows()[0]["status"] == "failed" and "RuntimeError" in _rows()[0]["detail"]
    assert "exploded" not in _rows()[0]["detail"]


def test_an_unknown_channel_is_a_failed_row_the_email_seam_included():
    assert tools.send_digest("email", "someone@example.com", "Tonight: tacos.")["status"] == "failed"
    assert "no email channel" in tools.send_digest("email", "x", "y")["detail"]


# ---------- no keys, and the real Twilio call stubbed ----------

def test_without_twilio_keys_nothing_is_sent_and_the_reason_is_recorded(no_twilio):
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="4165550100", on=True, name="Emily")
    assert tools.twilio_configured() is False

    results = tools.run_morning_texts_once(now_utc=_local(7, 0))
    assert [r["status"] for r in results] == ["skipped-no-keys"]
    row = _rows()[0]
    assert row["status"] == "skipped-no-keys"
    assert "TWILIO" in row["detail"]
    report = tools.get_morning_text_report(days=1)
    assert report["configured"] is False and report["opted_in"] == 1
    assert report["by_status"] == {"skipped-no-keys": 1}


def test_the_startup_loop_stays_off_without_keys(no_twilio, monkeypatch, caplog):
    """DISABLE_MORNING_TEXT is set for the whole suite; lift it here and
    confirm the loop still declines to start when the keys are missing —
    and says so once, plainly."""
    import asyncio
    from app import main as app_main

    monkeypatch.delenv("DISABLE_MORNING_TEXT", raising=False)
    created = []
    monkeypatch.setattr(app_main.asyncio, "create_task", lambda coro: created.append(coro) or coro.close())
    with caplog.at_level("INFO", logger="home_manager"):
        asyncio.run(app_main.start_morning_text_loop())
    assert created == []
    assert any("Morning texts are off" in r.getMessage() for r in caplog.records)


def test_the_startup_loop_starts_with_keys(twilio_env, monkeypatch):
    import asyncio
    from app import main as app_main

    monkeypatch.delenv("DISABLE_MORNING_TEXT", raising=False)
    created = []

    def fake_create_task(coro):
        created.append(coro)
        coro.close()

    monkeypatch.setattr(app_main.asyncio, "create_task", fake_create_task)
    asyncio.run(app_main.start_morning_text_loop())
    assert len(created) == 1


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_send_sms_posts_the_twilio_form_with_basic_auth_and_nothing_leaks(twilio_env, monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = req.data.decode("utf-8")
        return _FakeResponse(json.dumps({"sid": "SM0123456789abcdef", "status": "queued"}).encode("utf-8"))

    monkeypatch.setattr(digest.urllib.request, "urlopen", fake_urlopen)

    result = tools.send_sms("+14165550100", "Tonight: tacos.")

    assert result["status"] == "ok"
    assert seen["url"] == "https://api.twilio.com/2010-04-01/Accounts/ACtest/Messages.json"
    assert seen["method"] == "POST"
    assert seen["auth"].startswith("Basic ")
    assert "To=%2B14165550100" in seen["body"] and "From=%2B16475550100" in seen["body"]
    assert "Body=Tonight%3A+tacos." in seen["body"]
    # What is recorded carries neither the token nor the text.
    assert "secret-token" not in result["detail"] and "tacos" not in result["detail"]


def test_send_sms_records_a_twilio_refusal_without_raising(twilio_env, monkeypatch):
    def fake_urlopen(req, timeout=0):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            io.BytesIO(json.dumps({"code": 21608, "message": "The number +1416... is unverified."}).encode("utf-8")),
        )

    monkeypatch.setattr(digest.urllib.request, "urlopen", fake_urlopen)
    result = tools.send_sms("+14165550100", "Tonight: tacos.")
    assert result["status"] == "failed"
    assert result["detail"].startswith("HTTP 400 21608")
    assert "secret-token" not in result["detail"]


def test_twilios_own_reason_never_carries_the_number_into_the_row_or_the_log(twilio_env, monkeypatch, caplog):
    """Verifier finding, 2026-09-11: Twilio names the number it refused in
    its error text, and that text used to be stored and logged verbatim."""
    def fake_urlopen(req, timeout=0):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            io.BytesIO(json.dumps({"code": 21211, "message": "The 'To' number +14165550100 is not a valid phone number."}).encode("utf-8")),
        )

    monkeypatch.setattr(digest.urllib.request, "urlopen", fake_urlopen)
    result = tools.send_sms("+14165550100", "Tonight: tacos.")
    assert result["status"] == "failed" and "21211" in result["detail"]
    assert "4165550100" not in result["detail"] and "[number]" in result["detail"]

    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="4165550100", on=True)
    with caplog.at_level("WARNING", logger="home_manager"):
        tools.run_morning_texts_once(now_utc=_local(7, 0), send=lambda to, body: {
            "status": "failed", "detail": f"HTTP 400 21211 The 'To' number {to} is not a valid phone number.",
        })
    assert "4165550100" not in _rows()[0]["detail"]
    assert not any("4165550100" in r.getMessage() for r in caplog.records)


def test_send_sms_records_a_network_failure_without_raising(twilio_env, monkeypatch):
    def fake_urlopen(req, timeout=0):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(digest.urllib.request, "urlopen", fake_urlopen)
    result = tools.send_sms("+14165550100", "Tonight: tacos.")
    assert result["status"] == "failed" and "URLError" in result["detail"]


def test_the_sends_table_never_holds_the_token_or_the_text(twilio_env):
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="4165550100", on=True, name="Emily")
    sender = _Sender(detail="twilio SM1")
    tools.run_morning_texts_once(now_utc=_local(7, 0), send=sender)
    conn = get_conn()
    everything = " ".join(
        " ".join(str(v) for v in dict(r).values())
        for r in conn.execute("SELECT * FROM morning_text_sends").fetchall()
    )
    conn.close()
    assert "secret-token" not in everything
    assert "Skewers" not in everything
    assert "4165550100" not in everything


# ---------- numbers, hours, and who ----------

@pytest.mark.parametrize("raw, e164", [
    ("416-555-0100", "+14165550100"),
    ("(647) 555 0199", "+16475550199"),
    ("1 416 555 0100", "+14165550100"),
    ("+44 20 7946 0958", "+442079460958"),
    ("  ", ""),
    (None, ""),
])
def test_numbers_are_normalised_to_e164(raw, e164):
    assert tools.normalise_phone(raw) == e164


@pytest.mark.parametrize("raw", ["555-0100", "12345", "call me", "+1", "0416555010", "4161550100", "1-041-655-5010"])
def test_a_number_that_is_not_one_is_refused_plainly(raw):
    """Verifier finding, 2026-09-11: a ten-digit string with a leading 0
    used to normalise to +10416555010 — a number no morning could reach."""
    with pytest.raises(ValueError) as e:
        tools.normalise_phone(raw)
    assert "416-555-0100" in str(e.value)


@pytest.mark.parametrize("raw, hhmm", [
    ("7", "07:00"), ("7am", "07:00"), ("7:30 am", "07:30"), ("07:00", "07:00"),
    ("19:15", "19:15"), ("6:45pm", "18:45"), ("12am", "00:00"), ("12 pm", "12:00"),
])
def test_times_are_normalised(raw, hhmm):
    assert tools.normalise_time(raw) == hhmm


@pytest.mark.parametrize("raw", ["", "25:00", "7:60", "noonish", "13pm"])
def test_a_time_that_is_not_one_is_refused(raw):
    with pytest.raises(ValueError):
        tools.normalise_time(raw)


def test_set_morning_text_is_the_chat_tool_and_only_changes_what_it_is_given():
    _adults("Emily")
    assert "set_morning_text" in {d["name"] for d in agent.TOOL_DEFINITIONS}
    assert agent.TOOL_FUNCTIONS["set_morning_text"] is tools.set_morning_text

    first = tools.set_morning_text(phone="416-555-0100", time="7am", on=True)
    assert first["phone"] == "+14165550100" and first["on"] is True and first["time"] == "07:00"
    assert first["timezone"] == "America/Toronto"
    assert first["configured"] is False or first["configured"] is True

    later = tools.set_morning_text(time="6:30")
    assert later["time"] == "06:30" and later["phone"] == "+14165550100" and later["on"] is True

    off = tools.set_morning_text(on=False)
    assert off["on"] is False and off["phone"] == "+14165550100"


def test_turning_it_on_with_no_number_is_refused():
    _adults("Emily")
    with pytest.raises(ValueError) as e:
        tools.set_morning_text(on=True)
    assert "number" in str(e.value)


def test_two_adults_and_no_name_asks_rather_than_guessing():
    _adults()
    with pytest.raises(ValueError) as e:
        tools.set_morning_text(phone="4165550100", on=True)
    assert "Emily" in str(e.value) and "Vineeth" in str(e.value)

    # ...unless the number already belongs to one of them.
    tools.set_morning_text(phone="4165550100", name="Vineeth")
    result = tools.set_morning_text(phone="4165550100", on=True)
    assert result["name"] == "Vineeth" and result["on"] is True


def test_a_child_is_not_offered_the_morning_text():
    _adults("Emily")
    tools.add_member("Mia")
    tools.set_member_age_group("Mia", "child")
    names = [a["name"] for a in tools.get_morning_text_settings()["adults"]]
    assert names == ["Emily"]
    with pytest.raises(ValueError):
        tools.set_morning_text(phone="4165550100", on=True, name="Mia")


def test_clearing_the_number_turns_it_off():
    _adults("Emily")
    tools.set_morning_text(phone="4165550100", on=True)
    result = tools.set_morning_text(phone="")
    assert result["phone"] == "" and result["on"] is False


def test_an_unknown_time_zone_is_refused_and_a_bad_stored_one_falls_back(twilio_env):
    _adults("Emily")
    with pytest.raises(ValueError):
        tools.set_morning_text(timezone="Mars/Olympus")
    conn = get_conn()
    conn.execute("UPDATE households SET timezone = 'Not/AZone' WHERE id = ?", (tools.household_id(),))
    conn.commit()
    conn.close()
    _seed_day()
    tools.set_morning_text(phone="4165550100", on=True)
    sender = _Sender()
    # Falls back to Toronto rather than crashing the whole pass.
    assert [r["status"] for r in tools.run_morning_texts_once(now_utc=_local(7, 0), send=sender)] == ["ok"]


# ---------- household isolation ----------

def test_numbers_and_send_state_stay_inside_their_household(twilio_env):
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="4165550100", on=True)

    other = households.create_household("The Beta Testers", "a-safe-distinct-passphrase")
    with tools.use_household(other):
        _adults("Julia")
        _seed_day()
        tools.set_morning_text(phone="9055550100", on=True)
        assert [a["phone"] for a in tools.get_morning_text_settings()["adults"]] == ["+19055550100"]

    assert [a["phone"] for a in tools.get_morning_text_settings()["adults"]] == ["+14165550100"]

    sender = _Sender()
    results = tools.run_morning_texts_once(now_utc=_local(7, 0), send=sender)
    assert sorted((r["household_id"], r["status"]) for r in results) == [(1, "ok"), (other, "ok")]
    assert {r["household_id"] for r in _rows(1)} == {1}
    assert {r["household_id"] for r in _rows(other)} == {other}
    with tools.use_household(other):
        assert tools.get_morning_text_report()["total"] == 1
    assert tools.get_morning_text_report()["total"] == 1


def test_one_households_failure_does_not_cost_the_other_its_morning(twilio_env, monkeypatch):
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="4165550100", on=True)
    other = households.create_household("The Beta Testers", "a-safe-distinct-passphrase")
    with tools.use_household(other):
        _adults("Julia")
        _seed_day()
        tools.set_morning_text(phone="9055550100", on=True)

    real_build = digest.build_morning_text

    def build_or_boom(now_local=None):
        if tools.household_id() == 1:
            raise RuntimeError("household 1's plan is broken today")
        return real_build(now_local)

    monkeypatch.setattr(digest, "build_morning_text", build_or_boom)
    sender = _Sender()
    results = tools.run_morning_texts_once(now_utc=_local(7, 0), send=sender)
    assert [(r["household_id"], r["status"]) for r in results] == [(other, "ok")]
    assert _rows(1) == [], "nothing recorded, so the next pass tries household 1 again"


# ---------- the routes ----------

def test_the_preferences_routes_read_and_save(signed_in):
    _adults()
    res = signed_in.get("/api/morning-text")
    assert res.status_code == 200
    data = res.json()
    assert data["time"] == "07:00" and data["timezone"] == "America/Toronto"
    assert [a["name"] for a in data["adults"]] == ["Emily", "Vineeth"]
    emily = data["adults"][0]["member_id"]

    res = signed_in.post("/api/morning-text", json={"member_id": emily, "phone": "416 555 0100", "on": True, "time": "06:45"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["phone"] == "+14165550100" and body["on"] is True and body["time"] == "06:45"
    assert body["settings"]["adults"][0]["on"] is True

    bad = signed_in.post("/api/morning-text", json={"member_id": emily, "phone": "12345"})
    assert bad.status_code == 400 and "416-555-0100" in bad.json()["detail"]

    off = signed_in.post("/api/morning-text", json={"member_id": data["adults"][1]["member_id"], "on": True})
    assert off.status_code == 400 and "number" in off.json()["detail"]


def test_the_save_route_acts_on_the_member_id_it_was_given(signed_in):
    """Verifier finding, 2026-09-11: the route used to bounce id -> name
    -> id and, with two adults of one name, wrote to the wrong row."""
    _adults("Alex")
    conn = get_conn()
    conn.execute(
        "INSERT INTO members (household_id, name, age_group) VALUES (?, 'Alex', 'adult')", (tools.household_id(),)
    )
    conn.commit()
    conn.close()
    adults = tools.get_morning_text_settings()["adults"]
    assert [a["name"] for a in adults] == ["Alex", "Alex"]
    second = adults[1]["member_id"]

    res = signed_in.post("/api/morning-text", json={"member_id": second, "phone": "4165550101", "on": True})
    assert res.status_code == 200, res.text
    after = {a["member_id"]: a for a in tools.get_morning_text_settings()["adults"]}
    assert after[second]["phone"] == "+14165550101" and after[second]["on"] is True
    assert after[adults[0]["member_id"]]["phone"] == ""


def test_the_save_route_refuses_a_child(signed_in):
    _adults("Emily")
    tools.add_member("Mia")
    tools.set_member_age_group("Mia", "child")
    res = signed_in.post("/api/morning-text", json={"member_id": _member_id("Mia"), "phone": "4165550100", "on": True})
    assert res.status_code == 400


def test_the_save_route_refuses_another_households_member(signed_in):
    _adults("Emily")
    other = households.create_household("The Beta Testers", "a-safe-distinct-passphrase")
    with tools.use_household(other):
        _adults("Julia")
        julia = _member_id("Julia")
    res = signed_in.post("/api/morning-text", json={"member_id": julia, "phone": "4165550100", "on": True})
    assert res.status_code == 404
    with tools.use_household(other):
        assert tools.get_morning_text_settings()["adults"][0]["phone"] == ""


def test_observability_carries_the_morning_text_counts(signed_in, no_twilio):
    _adults("Emily")
    tools.set_morning_text(phone="4165550100", on=True)
    res = signed_in.get("/api/observability?days=1")
    assert res.status_code == 200
    texts = res.json()["morning_texts"]
    assert texts["configured"] is False and texts["opted_in"] == 1


def test_the_report_says_texting_is_off_when_someone_signed_up(capsys):
    import observability_report as rep

    rep._print_human(
        [{
            "household_id": 1, "household": "Emily", "errors": {"total": 0, "by_kind": {}, "recent": []},
            "usage": {"looks_inactive": True, "days": 7, "last_active_at": None},
            "morning_texts": {"configured": False, "opted_in": 1, "total": 0, "by_status": {}, "days": 1},
        }],
        1, "a test",
    )
    out = capsys.readouterr().out
    assert "Morning text — 1 signed up, but texting is OFF" in out

    rep._print_human(
        [{
            "household_id": 1, "household": "Emily", "errors": {"total": 0, "by_kind": {}, "recent": []},
            "usage": {"looks_inactive": True, "days": 7, "last_active_at": None},
            "morning_texts": {"configured": True, "opted_in": 2, "total": 2, "by_status": {"ok": 1, "failed": 1},
                              "days": 1, "last_failure": "HTTP 400 21608 unverified"},
        }],
        1, "a test",
    )
    out = capsys.readouterr().out
    assert "Morning text — FAILED for 1" in out and "unverified" in out


# ---------- the Preferences row and sheet (source markers) ----------

def test_the_preferences_sheet_has_a_morning_text_row():
    assert "<span class=\"prefs-row-title\">Morning text</span>" in SHELL_JS
    assert "data-morning=\"open\"" in SHELL_JS
    assert "function prefsMorningLine()" in SHELL_JS
    assert "fetch('/api/morning-text')" in SHELL_JS


def test_the_morning_sheet_saves_by_member_and_never_guesses():
    assert "morningSheetEl.id = 'morning-sheet';" in SHELL_JS
    assert "method: 'POST'" in SHELL_JS
    body = SHELL_JS[SHELL_JS.index("async function saveMorningSheet()"):SHELL_JS.index("function openMorningSheet()")]
    assert "fetch('/api/morning-text', { method: 'POST'" in body
    # Every adult's row is tried even when an earlier one is refused.
    assert "break;" not in body
    assert "member_id: parseInt(row.getAttribute('data-member-id'), 10)" in body
    # A refusal from the server is shown in place, calmly, no exclamation.
    assert "note.textContent = problem;" in body
    assert "!" not in re.sub(r"!==?", "", body).replace("if (!", "").replace("(!", "")


def test_the_morning_toggle_is_a_44px_button_in_tokens_only():
    assert ".morning-toggle {" in SHELL_CSS
    block = SHELL_CSS[SHELL_CSS.index(".morning-toggle {"):SHELL_CSS.index(".morning-toggle[aria-pressed=\"true\"]")]
    assert "min-height: 44px;" in block
    section = SHELL_CSS[SHELL_CSS.index('"Morning text" sheet'):SHELL_CSS.index("Meals — Week / Day / Meal")]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", section), "Rule 9: every colour through a token"
    assert "color: var(--on-accent-ink);" in section, "Rule 1: dark ink on the celadon fill"


def test_the_sends_table_is_wiped_between_tests():
    from tests.conftest import _TABLES
    assert "morning_text_sends" in _TABLES
