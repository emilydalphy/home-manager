"""
The app sent no security response headers at all -- found by the
security-audit card's 2026-09-04 pass, which searched app/ for
Strict-Transport-Security, Content-Security-Policy, X-Frame-Options,
frame-ancestors and X-Content-Type-Options and got zero matches.

These tests pin the headers, but the ones that matter are the three that
pin a *decision* rather than a value, because each is the kind of thing a
later tidy-up would undo without noticing:

  - the middleware has to sit OUTSIDE auth_middleware, or every
    unauthenticated response loses its headers while every test that
    reaches a route handler still passes (see the registration-order
    test -- this is exactly what happened when the order was reversed
    during review);
  - the CSP restricts framing, forms, <base> and <object> and nothing
    else, because default-src/script-src would break every page in this
    app on the next deploy;
  - frame-ancestors is 'self', not 'none', because the Kitchen tab really
    does frame What we know and Inventory.

Each of those has a test that can go red on its own. Tests that merely
restate a value another test already asserts exactly are not tests, so
the value assertions below are deliberately loose where a narrower test
owns the specific claim.
"""

ALWAYS_ON = ("X-Content-Type-Options", "X-Frame-Options", "Content-Security-Policy", "Referrer-Policy")


def _headers_for(client, path="/login"):
    return client.get(path, follow_redirects=False).headers


def test_the_always_on_headers_are_present(client):
    headers = _headers_for(client)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    # Loose on purpose: test_the_csp_restricts_only_what_it_should owns
    # the exact contents, and an equality check here would make that test
    # unable to fail on its own.
    assert "Content-Security-Policy" in headers
    # The header that was already there before this change.
    assert headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"


def test_an_unauthenticated_request_to_a_private_route_still_gets_them(client):
    """
    The load-bearing test on this file.

    auth_middleware answers an unauthenticated request itself, without the
    request ever reaching a route handler. This middleware only sees that
    response because it is registered AFTER auth_middleware and Starlette
    inserts each one at the front, putting this one outermost.

    Reverse those two registrations and this is the only test here that
    goes red -- every other test hits a path that reaches a handler, so
    all of them keep passing while a hostile caller's 401 comes back bare.
    """
    res = client.get("/api/whoami", follow_redirects=False)
    assert res.status_code in (401, 403, 303), "expected auth_middleware to answer this itself"
    for header in ALWAYS_ON:
        assert header in res.headers, (
            f"{header} missing on an unauthenticated response -- security_headers must be "
            "registered AFTER auth_middleware in app/main.py so it wraps it"
        )


def test_a_public_page_gets_them_too(client):
    """
    The share pages are the ones opened by people outside the household,
    from links pasted into messages -- so they are the pages that most
    need the referrer and framing rules, not the least.
    """
    headers = client.get("/share/not-a-real-token", follow_redirects=False).headers
    for header in ALWAYS_ON:
        assert header in headers


def test_a_signed_in_app_route_gets_them_too(signed_in):
    headers = signed_in.get("/", follow_redirects=False).headers
    for header in ALWAYS_ON:
        assert header in headers


def test_static_files_and_redirects_get_them_too(client, signed_in):
    """
    Two response paths that don't go through an ordinary route function:
    the StaticFiles mount, and the 303 the login route hands back.
    """
    static = signed_in.get("/static/shell.js", follow_redirects=False)
    assert static.status_code == 200
    assert static.headers["X-Content-Type-Options"] == "nosniff"

    redirect = client.post(
        "/login", data={"password": "test-password", "next": "/"}, follow_redirects=False
    )
    assert redirect.status_code == 303
    assert "Content-Security-Policy" in redirect.headers


def test_framing_is_same_origin_because_the_kitchen_tab_frames_its_own_pages(client):
    """
    'none' would be the stricter-looking answer and it would break the
    Kitchen tab, whose entry tiles open static/memory.html and
    static/inventory.html inside an iframe (shell.js's #kit-sheet).
    """
    headers = _headers_for(client)
    assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" not in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "SAMEORIGIN"


def test_the_csp_restricts_only_what_it_should(client):
    """
    Every page in this app carries inline CSS and JS. Adding script-src or
    default-src without threading nonces through ~20 hand-written HTML
    files would blank the app in production, where nothing would catch it
    -- the pages still return 200; the browser just refuses to run them.

    The three that DO ship alongside frame-ancestors cost nothing here:
    no <base> tag, no <object>/<embed>, and one form with an action, which
    posts to /login on this origin.
    """
    csp = _headers_for(client)["Content-Security-Policy"]
    for directive in ("frame-ancestors 'self'", "base-uri 'self'", "form-action 'self'", "object-src 'none'"):
        assert directive in csp, f"{directive} should be part of the policy"
    for directive in ("default-src", "script-src", "style-src", "img-src", "connect-src"):
        assert directive not in csp, (
            f"{directive} would break this app's inline CSS/JS -- see the docstring; "
            "a real CSP needs nonces first and is its own ticket"
        )


def test_hsts_is_sent_only_when_the_browser_arrived_over_https(client):
    """
    Railway terminates TLS at its proxy, so the app itself always sees
    http and X-Forwarded-Proto is what tells the truth -- the same signal
    the session cookie's Secure flag already reads.
    """
    plain = client.get("/login", follow_redirects=False)
    assert "Strict-Transport-Security" not in plain.headers

    forwarded = client.get(
        "/login", follow_redirects=False, headers={"X-Forwarded-Proto": "https"}
    )
    hsts = forwarded.headers["Strict-Transport-Security"]
    # Loose on the exact number so the directives test below stays able to
    # fail on its own; a year is the floor worth asserting.
    assert hsts.startswith("max-age=")
    assert int(hsts.split("max-age=")[1].split(";")[0].strip()) >= 31536000


def test_hsts_does_not_claim_subdomains_or_preload(client):
    """
    A browser honours HSTS for a year with no way to withdraw it early, so
    the pin stays on the one host known to be https-only. preload is not
    merely undesirable but unavailable: it requires includeSubDomains, and
    this host sits on the shared up.railway.app public suffix.
    """
    hsts = client.get(
        "/login", follow_redirects=False, headers={"X-Forwarded-Proto": "https"}
    ).headers["Strict-Transport-Security"].lower()
    assert "includesubdomains" not in hsts
    assert "preload" not in hsts
