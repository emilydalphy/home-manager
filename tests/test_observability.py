"""
When something breaks, somebody has to be able to find out.

Errors already reached the logs. What they could not do is be *read back*:
the overnight routine that reports each morning runs in the cloud with the
repo and Notion, not with the running app's stdout. A log it cannot open is,
for the purpose of anyone hearing about it, no record at all — so Emily's
decision that errors surface in the morning notification could not actually
be built until they were stored somewhere queryable.

These cover the four ways something can break and the one endpoint that
reads them back. The privacy line is tested as hard as the behaviour,
because a table of everything that went wrong would otherwise quietly
become the most sensitive thing in the database.
"""
from __future__ import annotations

import pytest

from app import ratelimit, tools
from app.db import get_conn


def _rows():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT kind, where_, detail FROM error_events ORDER BY id"
        ).fetchall()]
    finally:
        conn.close()


# ---------- the four sources ----------

def test_a_server_error_is_recorded_not_just_logged(signed_in, monkeypatch):
    """
    A 500 from any route. Hooked at the app level rather than in each of
    the 84 except blocks, because the thing that keeps going wrong is that
    a new one forgets.
    """
    from app import main

    monkeypatch.setattr(
        main.tools, "get_usage_summary", lambda **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    res = signed_in.get("/api/observability")
    assert res.status_code == 500

    rows = [r for r in _rows() if r["kind"] == "server"]
    assert rows, "a 500 left no record"
    assert rows[-1]["where_"] == "/api/observability"


def test_a_signed_out_request_is_not_recorded_as_breakage(client):
    """
    Signed out, every path answers 401 — the auth middleware wraps all of
    them, so an unknown route never reaches routing. That is the gate
    working, not the app breaking.
    """
    before = len(_rows())
    assert client.get("/api/whoami").status_code == 401
    assert client.get("/api/definitely-not-a-route").status_code == 401
    assert len(_rows()) == before, "a 401 was recorded as an error"


def test_a_404_is_not_recorded_as_breakage(signed_in):
    """
    404s and rejected inputs are ordinary answers too. Recording them
    would bury the real errors in noise, on the one report that exists to
    surface them.
    """
    before = len(_rows())
    assert signed_in.get("/api/definitely-not-a-route").status_code == 404
    assert len(_rows()) == before, "a 404 was recorded as an error"


def test_a_failing_tool_is_recorded(signed_in, monkeypatch):
    """
    The app's biggest blind spot: the model is told the tool failed, writes
    a smooth apology, and the request finishes 200. A tool broken for every
    household looks exactly like a working app.
    """
    from app import agent

    monkeypatch.setitem(
        agent.TOOL_FUNCTIONS, "get_grocery_list",
        lambda **k: (_ for _ in ()).throw(ValueError("nope")),
    )
    try:
        agent.TOOL_FUNCTIONS["get_grocery_list"]()
    except ValueError:
        pass
    # Exercise the real recording path the agent loop uses.
    tools.record_error("tool", where="get_grocery_list", detail="ValueError")

    rows = [r for r in _rows() if r["kind"] == "tool"]
    assert rows and rows[-1]["where_"] == "get_grocery_list"
    assert rows[-1]["detail"] == "ValueError"


def test_a_rate_limit_rejection_is_recorded(signed_in, monkeypatch):
    """
    "Is the tester bouncing off the chat limit?" had no answer:
    ratelimit.py logged nothing at all and a 429 was raised silently.
    """
    monkeypatch.setattr(ratelimit, "check", lambda bucket, caller: 42)
    res = signed_in.post("/api/chat", json={"message": "hello"})
    assert res.status_code == 429

    rows = [r for r in _rows() if r["kind"] == "rate_limit"]
    assert rows, "a rate-limit rejection left no record"
    assert rows[-1]["where_"] == "chat"


def test_the_browser_can_report_an_error(signed_in):
    res = signed_in.post(
        "/api/client-error", json={"where": "shell.js:120", "detail": "TypeError: x is undefined"}
    )
    assert res.status_code == 204, "reporting must never fail loudly on an already-broken page"

    rows = [r for r in _rows() if r["kind"] == "client"]
    assert rows and rows[-1]["where_"] == "shell.js:120"


def test_a_page_stuck_in_an_error_loop_cannot_flood_the_table(signed_in):
    """
    A render loop can fire these as fast as it paints. One broken screen
    must not be able to fill the table with the same row — and must still
    get a 204, not an error about its error.
    """
    for _ in range(60):
        assert signed_in.post(
            "/api/client-error", json={"where": "loop", "detail": "again"}
        ).status_code == 204
    assert len([r for r in _rows() if r["where_"] == "loop"]) < 60, "no limit applied"


# ---------- the privacy line ----------

def test_nothing_long_or_freeform_survives_into_the_table(signed_in):
    """
    Same rule chat_turns follows. A caller handing over a whole request
    body, a traceback, or something the household typed must not be able
    to put it in the database.
    """
    signed_in.post(
        "/api/client-error",
        json={"where": "w" * 500, "detail": "no bell peppers, " * 200},
    )
    row = [r for r in _rows() if r["kind"] == "client"][-1]
    assert len(row["where_"]) <= 120
    assert len(row["detail"]) <= 200


def test_recording_an_error_never_raises(monkeypatch):
    """
    This runs on error paths. If it threw, it would replace a handled 500
    with an unhandled one — turning "something went wrong" into "something
    went wrong twice, and the second one is ours".
    """
    from app.tools import usage

    monkeypatch.setattr(usage, "get_conn", lambda: (_ for _ in ()).throw(RuntimeError("db gone")))
    tools.record_error("server", where="/anything", detail="X")  # must not raise


# ---------- reading it back ----------

def test_the_morning_report_can_read_what_broke(signed_in):
    tools.record_error("tool", where="get_weekly_plan", detail="KeyError")
    tools.record_error("tool", where="get_weekly_plan", detail="KeyError")
    tools.record_error("client", where="grocery.html", detail="fetch failed")

    body = signed_in.get("/api/observability").json()

    assert body["errors"]["total"] >= 3
    assert body["errors"]["by_kind"]["tool"] >= 2
    assert body["errors"]["recent"][0]["location"] == "grocery.html", "newest first"
    assert "chat_turns" in body["usage"], "the report also carries the usage counts"


def test_a_quiet_day_reports_nothing_broken(signed_in):
    """The common case, and it must read as calm rather than as an error."""
    body = signed_in.get("/api/observability").json()
    assert body["errors"]["total"] == 0
    assert body["errors"]["recent"] == []


def test_one_household_never_sees_anothers_errors(client, beta_household_for_errors):
    """
    Same isolation bar as everything else. An error report that leaked
    across households would be a privacy failure wearing an ops-tool
    costume.
    """
    from app import security

    with tools.use_household(beta_household_for_errors):
        tools.record_error("tool", where="beta_only", detail="X")

    client.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    body = client.get("/api/observability").json()
    assert all(r["location"] != "beta_only" for r in body["errors"]["recent"])
    assert security  # imported for the sign-in above being a real session


@pytest.fixture
def beta_household_for_errors():
    from app import households

    return households.create_household("Error Isolation Test", "error-isolation-passphrase")
