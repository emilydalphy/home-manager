"""
The app is on a public URL. These tests exist to make sure it stays shut.

Each one corresponds to a way it was open before: any caller could read and
write every /api route, drive the Claude-backed endpoints, and choose which
chat session they landed in.
"""
import pytest

from app import ratelimit, security


# ---------- the gate itself ----------

def test_api_refuses_anonymous_callers(client):
    res = client.get("/api/memory")
    assert res.status_code == 401
    body = res.json()
    assert "members" not in body, "household data must not leak in the refusal"


def test_anonymous_browser_is_sent_to_login(client):
    res = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"].startswith("/login")


def test_write_routes_refuse_anonymous_callers(client):
    res = client.post("/api/memory/edit", json={"field": "goals", "value": "hacked"})
    assert res.status_code == 401


def test_chat_refuses_anonymous_callers(client):
    """The route that spends money is the one that most needs the gate."""
    res = client.post("/api/chat", json={"session_id": "default", "message": "hi"})
    assert res.status_code == 401


def test_signed_in_caller_reaches_the_api(signed_in):
    res = signed_in.get("/api/memory")
    assert res.status_code == 200
    assert "members" in res.json()


# ---------- sign-in ----------

def test_login_page_renders(client):
    res = client.get("/login")
    assert res.status_code == 200
    assert "Household password" in res.text


def test_wrong_password_does_not_sign_you_in(client):
    res = client.post("/login", data={"password": "nope", "next": "/"}, follow_redirects=False)
    assert res.status_code == 401
    assert security.COOKIE_NAME not in res.cookies
    assert client.get("/api/memory").status_code == 401


def test_logout_clears_the_session(signed_in):
    signed_in.get("/logout", follow_redirects=False)
    assert signed_in.get("/api/memory").status_code == 401


def test_login_rejects_an_offsite_next(client):
    """?next=https://evil.example must not become a redirect off the app."""
    res = client.post(
        "/login",
        data={"password": "test-password", "next": "https://evil.example/steal"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/"


def test_tampered_cookie_is_rejected(client):
    client.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    assert client.get("/api/memory").status_code == 200
    client.cookies.clear()
    client.cookies.set(security.COOKIE_NAME, "forged.9999999999.abc")
    assert client.get("/api/memory").status_code == 401


# ---------- what stays public ----------

@pytest.mark.parametrize("path", ["/healthz", "/robots.txt", "/static/theme.css"])
def test_public_paths_stay_reachable(client, path):
    assert client.get(path).status_code == 200


def test_robots_disallows_everything(client):
    assert "Disallow: /" in client.get("/robots.txt").text


def test_responses_carry_a_noindex_header(client):
    assert "noindex" in client.get("/healthz").headers.get("x-robots-tag", "")


def test_share_links_do_not_require_the_password(client, signed_in):
    """An Eater with a share link should never meet the household password."""
    from app import tools

    tools.add_member("Tester")
    token = tools.get_or_create_share_link()["token"]
    anon = client
    anon.cookies.clear()
    res = anon.get(f"/api/share/{token}")
    assert res.status_code != 401


def test_an_invalid_share_token_is_not_a_way_in(client):
    res = client.get("/api/share/not-a-real-token")
    assert res.status_code in (404, 200)
    assert res.status_code != 401  # public route
    if res.status_code == 200:
        assert not res.json(), "an unknown token must not resolve to a real plan"


# ---------- chat sessions ----------

def test_session_id_comes_from_the_cookie_not_the_body(signed_in):
    """
    The old bug: session_id was read straight off the request body with a
    default of "default", so anyone could read the household's history.
    """
    cookie = signed_in.cookies.get(security.COOKIE_NAME)
    assert security.read_session(cookie), "signing in must mint a real session id"
    assert security.read_session("default") is None
    assert security.read_session(None) is None


def test_session_ids_are_unique_per_sign_in():
    assert security.issue_session() != security.issue_session()


# ---------- rate limiting ----------

def test_repeated_failed_logins_get_throttled(client):
    codes = [
        client.post("/login", data={"password": "wrong", "next": "/"},
                    follow_redirects=False).status_code
        for _ in range(12)
    ]
    assert 429 in codes, "brute-forcing the shared password must be throttled"


def test_rate_limiter_allows_then_blocks():
    ratelimit.reset()
    ratelimit.LIMITS["unit-test"] = [(2, 60)]
    try:
        assert ratelimit.check("unit-test", "1.2.3.4") is None
        assert ratelimit.check("unit-test", "1.2.3.4") is None
        assert ratelimit.check("unit-test", "1.2.3.4") is not None
        # A different caller is unaffected.
        assert ratelimit.check("unit-test", "5.6.7.8") is None
    finally:
        ratelimit.LIMITS.pop("unit-test", None)


def test_caller_is_read_from_the_forwarded_header():
    """Railway terminates TLS at a proxy — without this every caller looks identical."""
    class FakeRequest:
        headers = {"x-forwarded-for": "203.0.113.9, 10.0.0.1"}
        client = None

    assert ratelimit.caller_id(FakeRequest()) == "203.0.113.9"


# ---------- self-service reset ----------
# The one in-app route that deletes household data in bulk. It must be
# behind the gate like everything else, and must not delete anything on an
# empty/malformed body.

def test_reset_refuses_anonymous_callers(client):
    res = client.post("/api/reset", json={"meal_plan": True, "grocery_list": True})
    assert res.status_code == 401


def test_reset_preview_refuses_anonymous_callers(client):
    assert client.get("/api/reset/preview").status_code == 401


def test_reset_with_nothing_selected_is_rejected(signed_in):
    assert signed_in.post("/api/reset", json={}).status_code == 400
