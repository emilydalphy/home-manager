"""
The app is on a public URL. These tests exist to make sure it stays shut.

Each one corresponds to a way it was open before: any caller could read and
write every /api route, drive the Claude-backed endpoints, and choose which
chat session they landed in.
"""
import datetime

import pytest

from app import ratelimit, security


def _week_start() -> str:
    """Monday of the current week — what create_weekly_plan expects."""
    today = datetime.date.today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


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
    # The form itself, rather than the surrounding copy — the wording here
    # is deliberately being revisited for the beta (it used to greet a
    # first-time tester with "pick up where you left off"), and a smoke
    # test shouldn't fail every time someone improves a sentence.
    assert 'name="password"' in res.text
    assert "passphrase" in res.text.lower()


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


def _forwarded(value):
    class FakeRequest:
        headers = {"x-forwarded-for": value}
        client = None
    return FakeRequest()


def test_caller_is_read_from_the_forwarded_header():
    """Railway terminates TLS at a proxy — without this every caller looks identical."""
    assert ratelimit.caller_id(_forwarded("10.0.0.1")) == "10.0.0.1"


def test_the_caller_is_the_address_our_proxy_wrote_not_the_one_they_sent():
    """
    X-Forwarded-For is APPENDED to, so the leftmost entry is the caller's
    own claim and the rightmost is what our proxy observed. This test used
    to assert the opposite — that the first entry was the caller — which
    made every limit in this module advisory: vary one header, be a new
    caller. Changed deliberately on 2026-09-09. Do not "fix" it back.
    """
    # The caller claimed 1.2.3.4; the proxy appended what it actually saw.
    assert ratelimit.caller_id(_forwarded("1.2.3.4, 198.51.100.77")) == "198.51.100.77"
    # Several hops: still the one our own proxy added, i.e. the last.
    assert ratelimit.caller_id(_forwarded("1.2.3.4, 5.6.7.8, 198.51.100.77")) == "198.51.100.77"


def test_a_junk_forwarded_header_does_not_put_everyone_in_one_bucket():
    """
    A trailing comma or a header of nothing but separators must fall back to
    the socket address rather than returning "", which would be a single
    shared bucket every caller lands in — a rate limit that throttles the
    innocent and nobody else.
    """
    class NoHeaderButAClient:
        headers = {"x-forwarded-for": " , ,"}
        class client:
            host = "198.51.100.5"

    assert ratelimit.caller_id(NoHeaderButAClient()) == "198.51.100.5"
    assert ratelimit.caller_id(_forwarded("198.51.100.77,")) == "198.51.100.77"


# The address Railway's proxy observes and appends. Constant, because one
# attacker is one machine — the whole point is that they cannot change it.
_PROXY_SAW = "198.51.100.77"


def test_rotating_the_forwarded_header_cannot_outrun_the_sign_in_limit(client):
    """
    The bucket exists "so the shared password can't be brute-forced". Before
    2026-09-09 a different X-Forwarded-For on each attempt bought unlimited
    guesses: fifteen wrong passphrases, zero refusals, measured on the real
    route. Driven through that route here, because the bypass lived in the
    gap between the limiter and the request rather than inside either.

    The header is spelled the way production actually receives it: the
    caller's own claim first, then the address the proxy appended. That
    shape is the test — reading the first entry passes a rotating claim
    through as fifteen different callers, reading the appended one sees the
    single machine that it is.
    """
    ratelimit.reset()
    codes = []
    for i in range(15):
        res = client.post(
            "/login",
            data={"password": "definitely-not-it", "next": "/"},
            # A fresh claim every time, exactly what an attacker would send —
            # and the proxy's own observation behind it, which they cannot.
            headers={"x-forwarded-for": f"203.0.113.{i + 1}, {_PROXY_SAW}"},
            follow_redirects=False,
        )
        codes.append(res.status_code)

    assert 429 in codes, "rotating the header still bought unlimited guesses"
    # The limit is 8 per 5 minutes, so the refusals start once those are spent.
    assert codes.count(401) <= 8, f"more than 8 guesses got through: {codes}"


def test_two_households_behind_different_addresses_do_not_share_a_limit(client):
    """
    The fix must not overcorrect into one global bucket: Julia getting her
    passphrase wrong twice must not spend Emily's allowance. Different
    proxy-observed addresses, so different callers.
    """
    ratelimit.reset()
    for i in range(8):
        client.post("/login", data={"password": "wrong", "next": "/"},
                    headers={"x-forwarded-for": f"203.0.113.{i}, 198.51.100.10"},
                    follow_redirects=False)

    spent = client.post("/login", data={"password": "wrong", "next": "/"},
                        headers={"x-forwarded-for": "203.0.113.99, 198.51.100.10"},
                        follow_redirects=False)
    assert spent.status_code == 429, "that address should be out of attempts"

    other = client.post("/login", data={"password": "wrong", "next": "/"},
                        headers={"x-forwarded-for": "203.0.113.99, 198.51.100.11"},
                        follow_redirects=False)
    assert other.status_code == 401, "a different household was locked out by someone else"


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


# ---------- approving a week ----------
# The route that writes the household's shopping list in bulk. Behind the
# gate like everything else, and honest about a week that doesn't exist
# rather than reporting a cheerful approval of nothing.

def test_approving_a_week_refuses_anonymous_callers(client):
    res = client.post("/api/week/2026-09-07/approve", json={"approved_by": "Emily"})
    assert res.status_code == 401


def test_approving_a_week_with_no_plan_is_a_404(signed_in):
    assert signed_in.post("/api/week/1999-01-04/approve", json={}).status_code == 404


def test_approving_a_week_rejects_a_date_that_is_not_a_date(signed_in):
    assert signed_in.post("/api/week/not-a-date/approve", json={}).status_code == 400


def test_approving_a_week_builds_the_list_once(signed_in):
    """
    BUILD_ORDER.md stage 1's own acceptance test, end to end through the
    route the button actually calls: a draft's ingredients are not on the
    list, approving puts them there, and approving again adds nothing.
    """
    from app import tools

    week = _week_start()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(week, "Chili", slot="dinner", weekly_plan_id=plan_id)
    assert tools.list_grocery_list() == []

    first = signed_in.post(f"/api/week/{week}/approve", json={"approved_by": "Emily"})
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "approved"
    assert body["approved_by"] == "Emily"
    assert body["groceries_added"] == 1
    assert body["was_already_approved"] is False
    assert len(tools.list_grocery_list()) == 1

    second = signed_in.post(f"/api/week/{week}/approve", json={"approved_by": "Marcus"})
    assert second.status_code == 200
    assert second.json()["was_already_approved"] is True
    assert second.json()["approved_by"] == "Emily", "the receipt keeps naming who actually settled it"
    # The counts describe the week's receipt, not this request — they stay
    # the original approval's rather than resetting to zero because someone
    # tapped again. was_already_approved is what says nothing happened.
    assert second.json()["groceries_added"] == 1
    assert len(tools.list_grocery_list()) == 1, "approving twice must not double the list"
