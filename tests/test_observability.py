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

import types

import pytest

from app import ratelimit, tools
from app.db import get_conn


# Same shape as tests/test_agent_turn_recording.py: stub the HTTP client,
# never the loop, so the loop itself is the thing under test.
class _Usage:
    input_tokens = cache_read_input_tokens = cache_creation_input_tokens = output_tokens = 0


def _text_block(text):
    return types.SimpleNamespace(type="text", text=text)


def _tool_block(name, tool_input, block_id="tu_1"):
    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        return self._responses.pop(0)


def _stub_client(monkeypatch, responses):
    from app import agent

    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=_FakeMessages(responses)))


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

    This drives the REAL agent loop against a stubbed API client, rather
    than calling record_error itself. An earlier version of this test did
    the latter — it asserted only that inserting a row inserts a row, and
    stayed green with the production line deleted. That is the same
    failure the decision log records for the Plan the Week review: a test
    that stubs the thing into behaving tests the case that was never the
    risk.
    """
    from app import agent

    monkeypatch.setitem(
        agent.TOOL_FUNCTIONS,
        "get_grocery_list",
        lambda **kwargs: (_ for _ in ()).throw(ZeroDivisionError("tool exploded")),
    )
    # Round 1: the model calls the tool. Round 2: it writes a reply, the
    # way it does after being handed a tool error.
    _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[_tool_block("get_grocery_list", {})],
            stop_reason="tool_use",
            usage=_Usage(),
        ),
        types.SimpleNamespace(
            content=[_text_block("Sorry, I couldn't check that just now.")],
            stop_reason="end_turn",
            usage=_Usage(),
        ),
    ])

    reply, _ = agent.run_agent_turn([], "what's on the list?")

    assert "Sorry" in reply, "the turn still finishes normally — that is the point"
    rows = [r for r in _rows() if r["kind"] == "tool"]
    assert rows, (
        "a tool crashed inside a real agent turn and left no record — the "
        "request would look like a success from every angle"
    )
    assert rows[-1]["where_"] == "get_grocery_list"
    assert rows[-1]["detail"] == "ZeroDivisionError"


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


# ---------- what the independent review found, pinned ----------

def test_an_anonymous_caller_cannot_write_rows_by_getting_rate_limited(client, monkeypatch):
    """
    /login is a public path, so a rejected sign-in reaches the limiter with
    no household bound and household_id() falls back to 1. Recording there
    let anyone on the internet write rows into Emily's table as fast as
    they could be refused — with no ceiling and nothing pruning them. The
    limiter would have been causing the resource exhaustion it exists to
    prevent.
    """
    monkeypatch.setattr(ratelimit, "check", lambda bucket, caller: 30)
    before = len(_rows())
    for _ in range(20):
        client.post("/login", data={"password": "wrong", "next": "/"}, follow_redirects=False)
    assert len(_rows()) == before, (
        "an unauthenticated caller wrote error rows just by being rate limited"
    )


def test_a_signed_in_rate_limit_is_still_recorded(signed_in, monkeypatch):
    """The half worth keeping: is the tester bouncing off the chat limit?"""
    monkeypatch.setattr(ratelimit, "check", lambda bucket, caller: 30)
    signed_in.post("/api/chat", json={"message": "hi"})
    assert [r for r in _rows() if r["kind"] == "rate_limit"], "a real household's limit went unrecorded"


def test_the_error_reporters_own_throttling_does_not_write_rows(signed_in):
    """
    The flood protection has to reduce writes. Recording the reporter's own
    rejections replaced each dropped client row with a rate_limit row, so
    60 reports still wrote 60 rows — just less useful ones.
    """
    for _ in range(60):
        signed_in.post("/api/client-error", json={"where": "loop", "detail": "again"})
    assert len(_rows()) < 60, f"60 reports wrote {len(_rows())} rows; the limiter reduced nothing"


def test_the_error_reporter_is_not_reachable_without_signing_in(client):
    """
    The endpoint writes rows, and a write anyone on the internet can reach
    is a way to fill the household's database — measured at 500 rows from
    500 anonymous requests when this was briefly public, with the rate
    limiter bypassed by rotating a header the caller controls. Worse, the
    row cap then evicted the household's *real* errors: 10 genuine
    failures seeded, 1500 junk reports sent, 0 real errors left.

    The cost of closing it is that the two public share pages report
    nothing. That is the accepted trade (2026-09-02) and has its own
    ticket — a share link breaking is something the person you sent it to
    will tell you about; a stranger emptying your error table is not.
    """
    assert client.post(
        "/api/client-error", json={"where": "/share/x", "detail": "boom"}
    ).status_code == 401, "the error endpoint is writable without a password"
    assert client.get("/static/error-reporter.js").status_code == 401


def test_a_client_error_is_filed_against_the_household_that_hit_it(client, beta_household_for_errors):
    """
    Being behind auth is also what binds the household. While the endpoint
    was public, auth_middleware never ran for it, so household_id() fell
    back to 1 and every browser error from every household filed under
    Emily's — the exact misattribution this feature exists to avoid.
    """
    res = client.post(
        "/login", data={"password": "error-isolation-passphrase", "next": "/"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert client.post(
        "/api/client-error", json={"where": "/kitchen", "detail": "TypeError"}
    ).status_code == 204

    conn = get_conn()
    try:
        row = dict(conn.execute(
            "SELECT household_id, where_ FROM error_events ORDER BY id DESC LIMIT 1"
        ).fetchone())
    finally:
        conn.close()
    assert row["where_"] == "/kitchen"
    assert row["household_id"] == beta_household_for_errors, (
        f"a household-{beta_household_for_errors} browser error filed under "
        f"household {row['household_id']}"
    )


def test_a_500_never_records_the_exception_message(signed_in, monkeypatch):
    """
    83 routes build their detail as f"Server error: {e}", and app/tools
    raises exceptions that interpolate household data ("No saved recipe
    named '...'"). Only 20 of those routes have an except ValueError
    between that text and the table, so storing the message would break
    this table's no-user-content rule by construction.
    """
    from app import main

    secret = "Emily's private recipe name"
    monkeypatch.setattr(
        main.tools, "get_usage_summary",
        lambda **k: (_ for _ in ()).throw(ValueError(f"No saved recipe named '{secret}'")),
    )
    assert signed_in.get("/api/observability").status_code == 500

    row = [r for r in _rows() if r["kind"] == "server"][-1]
    assert secret not in row["detail"], f"household text reached the table: {row['detail']}"
    assert row["detail"] == "HTTP 500"


def test_the_table_does_not_grow_without_limit(signed_in):
    """
    Nothing else deletes these rows, and a table that only grows is a slow
    disk-fill on the volume holding the household's real data.
    """
    from app.tools import usage

    for i in range(usage._KEEP_ROWS + usage._PRUNE_EVERY + 60):
        tools.record_error("client", where=f"row-{i}", detail="x")
    assert len(_rows()) <= usage._KEEP_ROWS + usage._PRUNE_EVERY, (
        f"{len(_rows())} rows survived a burst; pruning never ran"
    )


def test_the_report_script_reads_every_household(signed_in):
    """
    The consumer the ticket was actually asking for. It reports every
    household, because running it per household means knowing in advance
    which one had a bad day.
    """
    import observability_report
    from app import households

    beta = households.create_household("Report Test Household", "report-test-passphrase")
    with tools.use_household(beta):
        tools.record_error("tool", where="get_weekly_plan", detail="KeyError")

    report = observability_report.collect(days=1)
    ids = {h["household_id"] for h in report}
    assert 1 in ids and beta in ids, "the report skipped a household"

    beta_section = next(h for h in report if h["household_id"] == beta)
    assert beta_section["errors"]["total"] == 1
    assert next(h for h in report if h["household_id"] == 1)["errors"]["total"] == 0


def test_a_crash_is_filed_against_the_household_that_hit_it(client, beta_household_for_errors):
    """
    Unhandled exceptions are handled in ServerErrorMiddleware, which sits
    OUTSIDE auth_middleware — and the household ContextVar has already been
    reset by the time the exception gets there, so household_id() falls
    back to 1. Without reading the cookie in the handler, every crash from
    every household was filed against Emily's: the beta tester's hardest
    failures landing in the wrong report and missing from their own.
    """
    import asyncio

    from starlette.requests import Request

    from app import main, security

    res = client.post(
        "/login", data={"password": "error-isolation-passphrase", "next": "/"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    cookie = client.cookies[security.COOKIE_NAME]

    scope = {
        "type": "http", "method": "GET", "path": "/__crash", "raw_path": b"/__crash",
        "query_string": b"", "root_path": "", "scheme": "http", "server": ("test", 80),
        "headers": [(b"cookie", f"{security.COOKIE_NAME}={cookie}".encode())],
    }
    # Called outside any household binding, exactly as the middleware does.
    assert tools.household_id() == 1, "precondition: the ContextVar has fallen back"
    asyncio.run(main.record_unhandled_errors(Request(scope), RuntimeError("crash")))

    conn = get_conn()
    try:
        row = dict(conn.execute(
            "SELECT household_id, where_ FROM error_events ORDER BY id DESC LIMIT 1"
        ).fetchone())
    finally:
        conn.close()

    # "(unmatched)" because this synthetic request never went through
    # routing — the point of the test is which household it lands on.
    assert row["where_"] == "(unmatched)"
    assert row["household_id"] == beta_household_for_errors, (
        f"the crash was filed against household {row['household_id']}, not the one that hit it"
    )


def test_a_url_never_puts_household_data_in_the_table(signed_in, monkeypatch):
    """
    where_ used to be the requested URL, and URLs carry household data:
    /api/members/Sophia Rodriguez/share-link put a real person's name in
    the table, and a 500 inside /api/share/{token} put a live, working
    share credential there. Storing the route PATTERN removes every path
    parameter at once, including the ones nobody has thought of yet.
    """
    from app import main

    monkeypatch.setattr(
        main.tools, "get_member_share_link",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        raising=False,
    )
    signed_in.post("/api/members/Sophia Rodriguez/share-link")

    for row in _rows():
        assert "Sophia" not in row["where_"], f"a member's name reached the table: {row['where_']}"


def test_each_household_is_pruned_on_its_own_count(signed_in):
    """
    The counter was global, so the prune fired for whichever household made
    the 50th call. Two households interleaving left one of them never
    pruned — measured at ~3x the cap and still climbing.
    """
    from app import households
    from app.tools import usage

    other = households.create_household("Prune Test", "prune-test-passphrase")
    # Household 2 keeps taking the turn that used to trigger the shared
    # counter, while household 1 does the bulk of the writing.
    for i in range(usage._KEEP_ROWS + 400):
        tools.record_error("client", where=f"h1-{i}", detail="x")
        if i % 3 == 0:
            with tools.use_household(other):
                tools.record_error("client", where=f"h2-{i}", detail="x")

    conn = get_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM error_events WHERE household_id = 1"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n <= usage._KEEP_ROWS + usage._PRUNE_EVERY, (
        f"household 1 holds {n} rows against a cap of {usage._KEEP_ROWS} — "
        f"its prune never fired because another household kept taking the turn"
    )
