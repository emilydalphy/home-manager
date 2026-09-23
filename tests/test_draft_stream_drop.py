"""
A draft that finished is never reported as one that failed.

2026-09-22, ~21:41 ET, Emily on her phone: Plan the week -> Draft my week ->
"That draft didn't come together — nothing's changed." The server had not
failed. The app log shows plan 58 saved normally at 01:42:00 UTC; Railway's
edge log shows `POST /api/week/2026-09-23/generate/stream 200 17632ms`,
closing at 01:41:50 — the stream connection dropped ~17.6 s in, ten seconds
before the draft finished, and plan-week.html's streamGenerate read the
rejected read() as a failed draft.

What is pinned here:

  1. The stream sends a keep-alive comment through its silent stretches
     (the model thinks for 15-20 s before the first "day" frame), on the
     draft stream and the chat stream alike.
  2. GET /api/week/{week_start}/generate/status answers how a generation
     ended — running, done with its plan id, or failed with its error — by
     the run_token the page sent, scoped to the household, and never
     mistakes an older run for the one the page asked about.
  3. plan-week.html tells "the server sent an error" (alert, as before)
     apart from "the connection went" (keep the drafting screen, say it is
     still drafting, ask the status endpoint until the draft lands, the
     server says it failed, or two minutes pass).
  4. The draft stream's 400 and 503 branches, and a client closing the
     stream, all leave a line in the log.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import agent, households, main as main_module
from app.main import app
from app.tools._shared import DEFAULT_HOUSEHOLD_ID
from tests import nodeharness

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")

WEEK = "2026-09-21"   # a Monday; the key every status read normalises to


# --------------------------------------------------------------------------
# 1. Keep-alive through the silence
# --------------------------------------------------------------------------

@pytest.fixture
def quick_heartbeat(monkeypatch):
    monkeypatch.setattr(main_module, "_STREAM_HEARTBEAT_SECONDS", 0.05)


def test_the_draft_stream_sends_a_keep_alive_while_the_model_is_quiet(monkeypatch, quick_heartbeat):
    """FAILS ON MAIN: the relay blocked on events.get() with no timeout, so
    a 15-20 s think before the first "day" frame sent nothing at all."""
    def fake(week_start, **kwargs):
        time.sleep(0.4)
        return {"weekly_plan_id": 7, "week_start_date": week_start}

    monkeypatch.setattr(main_module, "generate_weekly_plan", fake)
    frames = list(main_module._stream_week_generation(
        week_start=WEEK, constraints_notes="", intake_id=None,
    ))
    assert frames[0].startswith("event: status")
    assert frames[-1].startswith("event: done")
    beats = [f for f in frames if f == ": keep-alive\n\n"]
    assert len(beats) >= 2, frames
    # A comment frame: no event, no data — every SSE reader skips it.
    assert "data:" not in main_module._SSE_HEARTBEAT
    assert main_module._SSE_HEARTBEAT.startswith(":")


def test_the_chat_stream_sends_the_same_keep_alive(monkeypatch, quick_heartbeat):
    def slow_turn(history, message, **kwargs):
        time.sleep(0.3)
        return "ok", history + [{"role": "assistant", "content": "ok"}]

    monkeypatch.setattr(main_module, "run_agent_turn", slow_turn)
    monkeypatch.setattr(main_module, "_finish_chat_turn", lambda *a, **k: {"reply": "ok", "actions": []})
    frames = list(main_module._stream_chat_turn(
        session_id="s", message="hi", history=[], proactive_check=False,
    ))
    assert frames[0].startswith("event: status")
    assert frames[-1].startswith("event: done")
    assert ": keep-alive\n\n" in frames


def test_no_keep_alive_when_the_answer_comes_quickly(monkeypatch):
    monkeypatch.setattr(main_module, "generate_weekly_plan",
                        lambda week_start, **kw: {"weekly_plan_id": 1, "week_start_date": week_start})
    frames = list(main_module._stream_week_generation(
        week_start=WEEK, constraints_notes="", intake_id=None,
    ))
    assert [f.split("\n", 1)[0] for f in frames] == ["event: status", "event: done"]


# --------------------------------------------------------------------------
# 4. The log says what happened
# --------------------------------------------------------------------------

@pytest.mark.parametrize("exc, status", [
    (ValueError("no meals came back"), 400),
    (agent.AssistantUnavailableError("Claude is busy — try again in a minute."), 503),
])
def test_a_refused_or_unavailable_draft_is_logged(monkeypatch, caplog, exc, status):
    """FAILS ON MAIN: both branches put an error frame on the stream and
    wrote nothing to the log."""
    def fake(week_start, **kwargs):
        raise exc

    monkeypatch.setattr(main_module, "generate_weekly_plan", fake)
    with caplog.at_level(logging.WARNING, logger=main_module.logger.name):
        frames = list(main_module._stream_week_generation(
            week_start=WEEK, constraints_notes="", intake_id=None,
        ))
    assert frames[-1].startswith("event: error")
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(f"({status})" in line and str(exc) in line and WEEK in line for line in lines), lines


def test_a_client_closing_the_draft_stream_is_logged_and_the_draft_carries_on(monkeypatch, caplog):
    finished = threading.Event()
    release = threading.Event()

    def fake(week_start, **kwargs):
        release.wait(5)
        finished.set()
        return {"weekly_plan_id": 9, "week_start_date": week_start}

    monkeypatch.setattr(main_module, "generate_weekly_plan", fake)
    sent = []

    async def drop_after_first_frame():
        # The ASGI conversation of a phone that hangs up after the first
        # frame: Starlette (ASGI 2.3, as uvicorn speaks) listens on receive()
        # and cancels the stream when the disconnect arrives.
        first = asyncio.Event()

        async def send(message):
            sent.append(message)
            if message.get("body"):
                first.set()

        async def receive():
            await first.wait()
            return {"type": "http.disconnect"}

        response = main_module._SSEResponse(
            main_module._stream_week_generation(week_start=WEEK, constraints_notes="", intake_id=None),
            what=f"Draft stream for the week of {WEEK}", carries_on="generation continues",
        )
        scope = {"type": "http", "asgi": {"spec_version": "2.3"}}
        await asyncio.wait_for(response(scope, receive, send), 10)

    with caplog.at_level(logging.WARNING, logger=main_module.logger.name):
        asyncio.run(drop_after_first_frame())
    release.set()
    assert finished.wait(5), "the generation thread is not stopped by the stream closing"
    bodies = [m["body"] for m in sent if m.get("body")]
    assert len(bodies) == 1 and bodies[0].startswith(b"event: status"), "hung up before anything else"
    lines = [r.getMessage() for r in caplog.records]
    assert any(
        f"Draft stream for the week of {WEEK} closed by the client after" in line
        and "generation continues" in line
        for line in lines
    ), lines


def test_a_stream_that_runs_to_the_end_logs_no_close(signed_in, monkeypatch, caplog):
    monkeypatch.setattr(main_module, "generate_weekly_plan",
                        lambda week_start, **kw: {"weekly_plan_id": 1, "week_start_date": week_start})
    with caplog.at_level(logging.WARNING, logger=main_module.logger.name):
        with signed_in.stream("POST", f"/api/week/{WEEK}/generate/stream", json={}) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache"
            lines = list(response.iter_lines())
    assert any(line.startswith("event: done") for line in lines)
    assert not [r for r in caplog.records if "closed by the client" in r.getMessage()]


# --------------------------------------------------------------------------
# 2. The status endpoint
# --------------------------------------------------------------------------

@pytest.fixture
def held_generation(monkeypatch):
    """
    agent.generate_weekly_plan for real — lock, bookkeeping and all — with
    the model call replaced by one that waits for the test to let it go.
    """
    gate = {"release": threading.Event(), "entered": threading.Event(), "outcome": None}

    def fake_generate(week_start_date, **kwargs):
        gate["entered"].set()
        assert gate["release"].wait(5)
        if isinstance(gate["outcome"], BaseException):
            raise gate["outcome"]
        return {"weekly_plan_id": gate["outcome"] or 58, "week_start_date": week_start_date}

    monkeypatch.setattr(agent, "_generate_weekly_plan", fake_generate)
    monkeypatch.setattr(agent, "_generate_prep_schedule_if_needed", lambda plan: None)
    monkeypatch.setattr(agent, "_sync_defrost_tasks_if_needed", lambda plan: None)
    return gate


def _start(token, week=WEEK, household=DEFAULT_HOUSEHOLD_ID):
    errors = []

    def run():
        from app.tools._shared import use_household
        try:
            with use_household(household):
                agent.generate_weekly_plan(week, confirm_takeover=True, run_token=token)
        except BaseException as e:   # noqa: BLE001 — the test reads it
            errors.append(e)

    t = threading.Thread(target=run)
    t.start()
    return t, errors


def _status(client, token, week=WEEK):
    res = client.get(f"/api/week/{week}/generate/status", params={"run": token})
    assert res.status_code == 200, res.text
    return res.json()


def test_status_reports_a_running_draft_then_its_plan(signed_in, held_generation):
    thread, _ = _start("tok-running")
    assert held_generation["entered"].wait(5)

    body = _status(signed_in, "tok-running")
    assert body["running"] is True
    assert body["run"]["state"] == "running"
    assert body["run"]["token"] == "tok-running"
    assert body["run"]["started_at"] and body["run"]["finished_at"] is None
    assert body["run"]["plan_id"] is None

    held_generation["release"].set()
    thread.join(5)

    body = _status(signed_in, "tok-running")
    assert body["running"] is False
    assert body["run"]["state"] == "done"
    assert body["run"]["plan_id"] == 58
    assert body["run"]["finished_at"]
    assert body["latest"]["token"] == "tok-running"


def test_status_reports_a_failed_draft_with_its_error(signed_in, held_generation):
    held_generation["outcome"] = ValueError("no meals came back")
    held_generation["release"].set()
    thread, errors = _start("tok-failed")
    thread.join(5)
    assert errors and isinstance(errors[0], ValueError), "the caller still sees the failure"

    run = _status(signed_in, "tok-failed")["run"]
    assert run["state"] == "failed"
    assert run["plan_id"] is None
    assert run["error"] == {"status": 400, "detail": "no meals came back"}


def test_a_server_error_is_scrubbed_like_the_streams_own_error_frame(signed_in, held_generation):
    held_generation["outcome"] = RuntimeError("sqlite3.OperationalError: no such table: secrets")
    held_generation["release"].set()
    thread, _ = _start("tok-500")
    thread.join(5)

    run = _status(signed_in, "tok-500")["run"]
    assert run["state"] == "failed"
    assert run["error"]["status"] == 500
    assert run["error"]["detail"] == main_module.THINK_TROUBLE_LINE
    assert "sqlite" not in json.dumps(run)


def test_an_older_draft_is_never_taken_for_the_one_asked_about(signed_in, held_generation):
    """The case the token exists for: a finished draft from earlier this
    week must not read as "your draft landed" to a page whose own request
    never got as far as the server."""
    held_generation["release"].set()
    thread, _ = _start("tok-earlier")
    thread.join(5)

    body = _status(signed_in, "tok-this-tap")
    assert body["run"] is None
    assert body["running"] is False
    assert body["latest"]["token"] == "tok-earlier", "the newest run is still visible, labelled as its own"


def test_the_answer_for_one_tap_survives_a_later_draft(signed_in, held_generation):
    held_generation["release"].set()
    for token in ("tok-first", "tok-second"):
        thread, _ = _start(token)
        thread.join(5)
    assert _status(signed_in, "tok-first")["run"]["state"] == "done"
    assert _status(signed_in, "tok-first")["latest"]["token"] == "tok-second"


def test_status_reads_any_date_in_the_week_as_that_week(signed_in, held_generation):
    """Keyed by _week_lock_key, like the lock: the page asks with the same
    week_start it posted to, which may be a custom period's first day."""
    held_generation["release"].set()
    thread, _ = _start("tok-wed", week="2026-09-23")
    thread.join(5)
    assert _status(signed_in, "tok-wed", week="2026-09-23")["run"]["state"] == "done"
    assert _status(signed_in, "tok-wed", week=WEEK)["run"]["state"] == "done"


def test_another_household_sees_nothing_of_this_draft(signed_in, held_generation):
    beta_id = households.create_household("The Beta Testers", "beta-tester-passphrase")
    with TestClient(app) as beta:
        res = beta.post("/login", data={"password": "beta-tester-passphrase", "next": "/"},
                        follow_redirects=False)
        assert res.status_code == 303
        assert beta.get("/api/whoami").json()["household_id"] == beta_id

        thread, _ = _start("tok-emily")
        assert held_generation["entered"].wait(5)
        try:
            theirs = _status(beta, "tok-emily")
            assert theirs == {"week_start": WEEK, "running": False, "run": None, "latest": None}
            assert _status(signed_in, "tok-emily")["running"] is True
        finally:
            held_generation["release"].set()
            thread.join(5)
        assert _status(beta, "tok-emily")["run"] is None


def test_status_refuses_a_bad_date_and_needs_a_session(client, signed_in):
    assert signed_in.get("/api/week/not-a-date/generate/status").status_code == 400
    assert signed_in.get(f"/api/week/{WEEK}/generate/status", params={"run": "x" * 65}).status_code == 400
    with TestClient(app) as stranger:
        res = stranger.get(f"/api/week/{WEEK}/generate/status", follow_redirects=False)
        assert res.status_code in (401, 303)


def test_the_stream_endpoint_carries_the_token_through_to_the_status(signed_in, monkeypatch):
    """End to end over HTTP: the token the page posts is the one the status
    endpoint answers for."""
    monkeypatch.setattr(agent, "_generate_weekly_plan",
                        lambda week_start_date, **kw: {"weekly_plan_id": 61, "week_start_date": week_start_date})
    monkeypatch.setattr(agent, "_generate_prep_schedule_if_needed", lambda plan: None)
    monkeypatch.setattr(agent, "_sync_defrost_tasks_if_needed", lambda plan: None)
    with signed_in.stream("POST", f"/api/week/{WEEK}/generate/stream",
                          json={"run_token": "tok-http"}) as response:
        assert response.status_code == 200
        list(response.iter_lines())
    run = _status(signed_in, "tok-http")["run"]
    assert run["state"] == "done" and run["plan_id"] == 61


def test_a_waiter_handed_the_other_callers_plan_is_recorded_as_done(signed_in, held_generation, monkeypatch):
    """The lock still turns a double tap into one generation; the second
    tap's own record says done, with the one plan both got."""
    # The fake generation saves no row, so the hand-over's re-read is faked
    # to return the plan it names.
    monkeypatch.setattr(agent.tools, "get_weekly_plan", lambda pid: {"weekly_plan_id": pid})
    first, _ = _start("tok-tap-1")
    assert held_generation["entered"].wait(5)
    second, _ = _start("tok-tap-2")
    deadline = time.time() + 5
    while _status(signed_in, "tok-tap-2")["run"] is None and time.time() < deadline:
        time.sleep(0.01)
    assert _status(signed_in, "tok-tap-2")["run"]["state"] == "running", "waiting counts as drafting"
    held_generation["release"].set()
    first.join(5)
    second.join(5)
    one, two = _status(signed_in, "tok-tap-1")["run"], _status(signed_in, "tok-tap-2")["run"]
    assert one["state"] == two["state"] == "done"
    assert one["plan_id"] == 58
    assert two["plan_id"] == 58


def test_a_needs_confirmation_answer_is_not_a_run(signed_in, monkeypatch):
    monkeypatch.setattr(agent.tools, "preview_approved_takeover", lambda *a, **k: {"days": [WEEK]})
    out = agent.generate_weekly_plan(WEEK, run_token="tok-ask")
    assert out["status"] == "needs_confirmation"
    assert _status(signed_in, "tok-ask")["run"] is None


# --------------------------------------------------------------------------
# 3. The page
# --------------------------------------------------------------------------

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the page's own functions"
)


def _extract(name: str, source: str = PAGE) -> str:
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    head = "async " if source[max(0, start - 6):start] == "async " else ""
    return head + source[start:j + 1]


_PRELUDE = """
var painted = [];
var fetched = [];
var classes = [];
var weekStart = '2026-09-23';
var dayLine = { stopped: 0, stop: function () { this.stopped++; }, sawDate: function () {} };
function paintDraftStatus(t) { painted.push(t); }
function $(id) { return { classList: { add: function (c) { classes.push(id + '.' + c); } } }; }
var DRAFT_POLL_MS = 1;
var DRAFT_WAIT_CAP_MS = 120000;
function streamOf(parts, ending) {
  // A fetch Response whose body yields `parts` and then either ends
  // ('end') or rejects the next read the way a dropped connection does.
  var enc = new TextEncoder();
  var i = 0;
  return { ok: true, body: { getReader: function () { return { read: function () {
    if (i < parts.length) return Promise.resolve({ done: false, value: enc.encode(parts[i++]) });
    if (ending === 'drop') return Promise.reject(new TypeError('network error'));
    return Promise.resolve({ done: true });
  } }; } } };
}
"""


def _page_functions() -> str:
    return "\n".join(_extract(n) for n in (
        "lostContact", "streamGenerate", "showDraftReady", "wait", "newRunToken", "waitForDraft",
    ))


def _run(body: str) -> dict:
    script = _PRELUDE + _page_functions() + "\n(async function () {\n" + body + "\n})().catch(function (e) {" \
        " console.log(JSON.stringify({ harnessError: String(e && e.stack || e) })); });\n"
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert "harnessError" not in out, out["harnessError"]
    return out


_STATUS = 'event: status\ndata: {"message": "Drafting your week\\u2026"}\n\n'
_KEEP = ": keep-alive\n\n"


@_needs_node
def test_a_dropped_stream_is_lost_contact_not_a_failed_draft():
    """FAILS ON MAIN: the rejected read() threw a plain Error, and the caller
    alerted "That draft didn't come together" over a draft that landed."""
    out = _run(f"""
      fetch = function () {{ return Promise.resolve(streamOf({json.dumps([_STATUS, _KEEP])}, 'drop')); }};
      try {{ await streamGenerate({{}}); console.log(JSON.stringify({{ threw: false }})); }}
      catch (e) {{ console.log(JSON.stringify({{ threw: true, lost: !!e.lostContact }})); }}
    """)
    assert out == {"threw": True, "lost": True}


@_needs_node
def test_a_stream_that_ends_without_done_is_lost_contact():
    out = _run(f"""
      fetch = function () {{ return Promise.resolve(streamOf({json.dumps([_STATUS, _KEEP])}, 'end')); }};
      try {{ await streamGenerate({{}}); }}
      catch (e) {{ console.log(JSON.stringify({{ lost: !!e.lostContact }})); }}
    """)
    assert out == {"lost": True}


@_needs_node
def test_a_fetch_that_never_connects_is_lost_contact():
    out = _run("""
      fetch = function () { return Promise.reject(new TypeError('Load failed')); };
      try { await streamGenerate({}); }
      catch (e) { console.log(JSON.stringify({ lost: !!e.lostContact })); }
    """)
    assert out == {"lost": True}


@_needs_node
def test_an_error_frame_is_still_a_real_failure():
    err = 'event: error\ndata: {"status": 400, "detail": "no meals came back"}\n\n'
    out = _run(f"""
      fetch = function () {{ return Promise.resolve(streamOf({json.dumps([_STATUS, _KEEP, err])}, 'drop')); }};
      try {{ await streamGenerate({{}}); }}
      catch (e) {{ console.log(JSON.stringify({{ lost: !!e.lostContact, message: e.message, status: e.status }})); }}
    """)
    assert out == {"lost": False, "message": "no meals came back", "status": 400}


@_needs_node
def test_keep_alive_frames_are_skipped_and_done_still_lands():
    done = 'event: done\ndata: {"weekly_plan_id": 58}\n\n'
    out = _run(f"""
      fetch = function () {{ return Promise.resolve(streamOf({json.dumps([_STATUS, _KEEP, _KEEP, done])}, 'end')); }};
      var plan = await streamGenerate({{}});
      console.log(JSON.stringify({{ plan: plan, painted: painted }}));
    """)
    assert out["plan"] == {"weekly_plan_id": 58}
    assert out["painted"][-1] == "Your week is ready."


def _poll_harness(answers: list, cap_ms: int = 120000) -> dict:
    # `answers` are what successive status reads return; 'throw' is a read
    # that never reaches the server. Date.now ticks 10 s per read so the cap
    # is reachable without waiting for it.
    return _run(f"""
      var answers = {json.dumps(answers)};
      var clock = 1000000;
      Date.now = function () {{ return clock; }};
      DRAFT_WAIT_CAP_MS = {cap_ms};
      fetch = function (url) {{
        fetched.push(url);
        clock += 10000;
        var a = answers.length ? answers.shift() : 'throw';
        if (a === 'throw') return Promise.reject(new TypeError('offline'));
        return Promise.resolve({{ ok: true, json: function () {{ return Promise.resolve(a); }} }});
      }};
      var landed = await waitForDraft('tok-1', clock);
      console.log(JSON.stringify({{ landed: landed, painted: painted, fetched: fetched, stopped: dayLine.stopped }}));
    """)


@_needs_node
def test_after_a_drop_the_page_asks_until_the_draft_lands():
    out = _poll_harness([
        {"running": True, "run": None},
        "throw",
        {"running": True, "run": {"state": "running"}},
        {"running": False, "run": {"state": "done", "plan_id": 58}},
    ])
    assert out["landed"] is True
    assert out["painted"] == ["Still drafting…"]
    assert out["stopped"] == 1, "the day line's own clock stops claiming days"
    assert len(out["fetched"]) == 4
    assert all(u == "/api/week/2026-09-23/generate/status?run=tok-1" for u in out["fetched"])


@_needs_node
def test_after_a_drop_a_failed_draft_is_reported_as_failed():
    out = _poll_harness([
        {"running": True, "run": {"state": "running"}},
        {"running": False, "run": {"state": "failed", "error": {"status": 503, "detail": "busy"}}},
    ])
    assert out["landed"] is False
    assert len(out["fetched"]) == 2


@_needs_node
def test_after_a_drop_the_page_gives_up_at_the_cap_even_if_nothing_answers():
    out = _poll_harness([], cap_ms=120000)
    assert out["landed"] is False
    # 10 s per read against a 120 s cap: twelve tries, then the alert.
    assert len(out["fetched"]) == 12


def test_the_caller_waits_out_a_drop_and_alerts_only_on_a_real_failure():
    advance = _extract("advance")
    assert "var runToken = newRunToken();" in advance
    assert "run_token: runToken" in advance
    assert "if (!err || !err.lostContact) throw err;" in advance
    assert "if (!(await waitForDraft(runToken, startedAt))) throw new Error('draft did not land');" in advance
    # The redirect is the success path's own, reached by both.
    assert advance.count("location.href = '/week?drafted=' + encodeURIComponent(weekStart);") == 1
    assert advance.index("waitForDraft(") < advance.index("location.href = '/week?drafted='")
    # The failure alert is unchanged.
    assert "'That draft didn’t come together — nothing’s changed. Tap Draft my week to try again.'" in advance
    assert "var DRAFT_WAIT_CAP_MS = 120000;" in PAGE
    assert "var DRAFT_POLL_MS = 2000;" in PAGE


def test_the_crumb_still_leaves_while_the_page_waits():
    """`drafting` stays true through waitForDraft, so "‹ Plan" is leaveFlow."""
    tap = _extract("crumbTap")
    assert "if (step === 1 || drafting) leaveFlow();" in tap
    wait_fn = _extract("waitForDraft")
    assert "drafting = false" not in re.sub(r"//[^\n]*", "", wait_fn)
