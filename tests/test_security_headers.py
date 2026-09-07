"""
The app sent no security response headers at all -- found by the
security-audit card's 2026-09-04 pass, which searched app/ for
Strict-Transport-Security, Content-Security-Policy, X-Frame-Options,
frame-ancestors and X-Content-Type-Options and got zero matches.

These tests pin the four that now always ship and the one that is
conditional, but more importantly they pin the two *narrow* choices,
because both would look like oversights to someone tidying up later:

  - the CSP carries frame-ancestors and nothing else, because every page
    in this app has inline <style> and <script>, so a default-src policy
    would break the whole front end on the next deploy;
  - frame-ancestors is 'self', not 'none', because the Kitchen tab really
    does frame What we know and Inventory.

Both of those are the kind of thing a well-meaning "harden the CSP"
change would undo, so each has a test that fails loudly rather than a
comment that can be skimmed past.
"""


def _headers_for(client, path="/login"):
    return client.get(path, follow_redirects=False).headers


def test_the_always_on_headers_are_present(client):
    headers = _headers_for(client)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert headers["Content-Security-Policy"] == "frame-ancestors 'self'"
    # The header that was already there before this change.
    assert headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"


def test_a_public_page_gets_them_too(client):
    """
    The share pages are the ones opened by people outside the household,
    from links pasted into messages -- so they are the pages that most
    need the referrer and framing rules, not the least. A middleware that
    only covered authenticated routes would miss exactly them.
    """
    headers = client.get("/share/not-a-real-token", follow_redirects=False).headers
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert headers["Content-Security-Policy"] == "frame-ancestors 'self'"


def test_a_signed_in_app_route_gets_them_too(signed_in):
    headers = signed_in.get("/", follow_redirects=False).headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Security-Policy"] == "frame-ancestors 'self'"


def test_framing_is_same_origin_because_the_kitchen_tab_frames_its_own_pages(client):
    """
    'none' would be the stricter-looking answer and it would break the
    Kitchen tab, whose entry tiles open static/memory.html and
    static/inventory.html inside an iframe (shell.js's #kit-sheet).
    """
    headers = _headers_for(client)
    assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" not in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] != "DENY"


def test_the_csp_restricts_framing_and_nothing_else(client):
    """
    Every page in this app carries inline CSS and JS. Adding script-src or
    default-src without threading nonces through ~20 hand-written HTML
    files would blank the app in production, where nothing would catch it
    -- the pages still return 200; the browser just refuses to run them.
    """
    csp = _headers_for(client)["Content-Security-Policy"]
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
    assert forwarded.headers["Strict-Transport-Security"] == "max-age=31536000"


def test_hsts_does_not_claim_subdomains(client):
    """
    A browser honours HSTS for a year with no way to withdraw it early.
    Pinning only the host already known to be https-only keeps a future
    subdomain from being locked out by a decision made tonight.
    """
    headers = client.get(
        "/login", follow_redirects=False, headers={"X-Forwarded-Proto": "https"}
    ).headers
    assert "includeSubDomains" not in headers["Strict-Transport-Security"]
    assert "preload" not in headers["Strict-Transport-Security"]
