"""
The two public doors that take a write, or a secret, hold up against a
script (2026-10-20, overnight defect hunt: public routes and tokens).

1. /api/member-share/{token}/note and /restriction are unauthenticated
   writes for whoever holds a member's link — a link that is texted to a
   grandparent, so it is exactly the kind that gets forwarded. The
   2026-10-10 limits entry says their rate limiting "is app/ratelimit.py's,
   not here", but ratelimit.py had no bucket for them: one leaked link
   could post notes (each read into the chat agent's prompt) and
   restrictions (each one planned around) as fast as a loop runs.

2. /api/health-report compared the X-Report-Token header with
   secrets.compare_digest on two str — which raises TypeError for any
   non-ASCII character. One such header was a 500 instead of the route's
   one deliberate answer to a stranger, 404 ("this route's existence is not
   something a stranger needs to learn").
"""
from app import ratelimit, tools
from app.db import get_conn


def _token():
    tools.add_member("Priya")
    return tools.get_or_create_member_share_link("Priya")["token"]


def _count(table):
    conn = get_conn()
    n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    conn.close()
    return n


def test_a_script_on_a_member_link_is_stopped_and_writes_nothing_more(client):
    """CATCH. Red before: every note was a 200 and a row."""
    token = _token()
    limit = ratelimit.LIMITS["member_share_write"][0][0]
    for i in range(limit):
        assert client.post(f"/api/member-share/{token}/note", json={"note": f"note {i}"}).status_code == 200
    res = client.post(f"/api/member-share/{token}/note", json={"note": "one too many"})
    assert res.status_code == 429
    assert _count("member_notes") == limit
    # One bucket for both writes: switching door doesn't reset the count.
    res = client.post(f"/api/member-share/{token}/restriction", json={"restriction": "no figs"})
    assert res.status_code == 429
    priya = next(m for m in tools.list_members() if m["name"] == "Priya")
    assert "no figs" not in priya["dietary_restrictions"]


def test_a_person_adding_a_few_things_is_never_stopped(client):
    """GUARD. An eater adding three allergies and a note in a row is honest use."""
    token = _token()
    for r in ("peanuts", "shellfish", "sesame"):
        assert client.post(f"/api/member-share/{token}/restriction", json={"restriction": r}).status_code == 200
    assert client.post(f"/api/member-share/{token}/note", json={"note": "No mushrooms please"}).status_code == 200


def test_a_non_ascii_report_token_is_a_404_not_a_500(client, monkeypatch):
    """CATCH. Red before: TypeError from compare_digest, a 500."""
    monkeypatch.setenv("REPORT_TOKEN", "test-report-token")
    res = client.get("/api/health-report", headers={"x-report-token": "café".encode("latin-1")})
    assert res.status_code == 404


def test_the_right_report_token_still_opens_it(client, monkeypatch):
    """GUARD: the byte comparison still lets the real token in."""
    monkeypatch.setenv("REPORT_TOKEN", "test-report-token")
    assert client.get("/api/health-report", headers={"x-report-token": "test-report-token"}).status_code == 200
