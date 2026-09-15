"""
Loop Board (LOW · Improvement · Phase 1): "Corners: stale tab names, a
chores page reachable with chores off, raw JSON 404s, no favicon".

A tester poking around the edges of the app — a bookmark, a typo'd
address, a dev-tools tab title — should never land somewhere that reads as
abandoned. Five small corners, covered here:

1. static/inventory.html's back link ("Cook", not "Kitchen" — Cook is the
   tab's current name) and its "Dictate item" mic button, which rendered
   as a blank dark square (a CSS specificity bug, not a broken SVG — see
   the comment on ".inv-add-row .dictate-btn" in that file).
2. GET /chores-setup redirects a switched-off household home instead of
   showing a full questionnaire for a module the house can't see anywhere
   else; static/chores-setup.html's back link ("Now", not "Today").
3. static/manifest.json's description no longer leads with Chores.
4. An unknown address a browser navigates to gets a small branded page
   instead of a bare JSON 404 — but only that: a route's own 404 (a real
   ID that doesn't exist) and an API caller's 404 (no text/html Accept)
   are untouched, and a signed-out visitor still hits the login gate
   first, same as any other page.
5. GET /favicon.ico serves the app's icon instead of 404ing on every load.

Static-file assertions are tripwires (read the file, check the marker) per
this repo's usual pattern for text/markup changes; the redirect, the 404
routing split, and the favicon are behaviour, so those go through the real
app over TestClient.
"""
from __future__ import annotations

from pathlib import Path

from app import tools

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"

INVENTORY_HTML = (STATIC / "inventory.html").read_text(encoding="utf-8")
CHORES_SETUP_HTML = (STATIC / "chores-setup.html").read_text(encoding="utf-8")
MANIFEST_JSON = (STATIC / "manifest.json").read_text(encoding="utf-8")


# ==========================================================================
# 1. Inventory: the back link and the mic button
# ==========================================================================

def test_inventory_back_link_names_the_cook_tab():
    """Kitchen was renamed to Cook (nav v2); the crumb back into it had not caught up."""
    assert '<a class="inv-back" href="/kitchen">&larr; Cook</a>' in INVENTORY_HTML
    assert "&larr; Kitchen</a>" not in INVENTORY_HTML


def test_inventory_dictate_button_is_not_repainted_by_the_generic_button_rule():
    """
    ".inv-add-row button" (a class + a type selector, specificity 0,1,1)
    outranks theme.css's ".dictate-btn" (a class alone, 0,1,0), so without
    a more specific override the mic button silently inherited the Add
    button's spruce fill and ground-coloured icon — dark-on-dark-ish in
    light mode, genuinely dark-on-dark in dark mode (--spruce and --ground
    are both dark there), which is what "renders as a blank dark square"
    was describing. ".inv-add-row .dictate-btn" (two classes, 0,2,0) is
    the fix; this pins that it exists and restores the transparent,
    ink-strong look theme.css's own .dictate-btn defines.
    """
    assert ".inv-add-row .dictate-btn" in INVENTORY_HTML
    # It has to actually win back the properties the generic rule stomped,
    # not just exist as a selector with nothing in it.
    idx = INVENTORY_HTML.index(".inv-add-row .dictate-btn")
    rule = INVENTORY_HTML[idx: INVENTORY_HTML.index("}", idx) + 1]
    assert "background: transparent" in rule
    assert "var(--ink-strong)" in rule


def test_inventory_still_wires_up_the_mic_button():
    """The smaller fix was CSS, not removal — the button, its id and its
    dictation wiring are all still in the page."""
    assert 'id="inv-add-item-mic-btn"' in INVENTORY_HTML
    assert "setupDictation(document.getElementById('inv-add-item')" in INVENTORY_HTML
    assert '/static/dictation.js' in INVENTORY_HTML


# ==========================================================================
# 2. Chores setup: gated by the switch, and its back link
# ==========================================================================

def test_chores_setup_html_back_link_names_now():
    assert '<a class="back-link" href="/">&larr; Now</a>' in CHORES_SETUP_HTML
    assert "Back to Today" not in CHORES_SETUP_HTML


def test_chores_setup_redirects_home_when_the_switch_is_off(signed_in):
    """
    Chores is off by default (tests/conftest.py resets it before every
    test). A tester who types /chores-setup anyway should land back on Now,
    not on a full questionnaire for a module they cannot otherwise reach —
    the same reasoning the two /api/chores* write routes already apply
    (403, tools.CHORES_OFF_MESSAGE) for a request nothing on screen sent.
    """
    assert tools.chores_enabled() is False
    res = signed_in.get("/chores-setup", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/"


def test_chores_setup_still_serves_the_page_when_the_switch_is_on(signed_in):
    tools.set_chores_enabled(True)
    res = signed_in.get("/chores-setup", follow_redirects=False)
    assert res.status_code == 200
    assert "Let" in res.text and "chores" in res.text.lower()


# ==========================================================================
# 3. manifest.json: meals lead, not Chores
# ==========================================================================

def test_manifest_description_does_not_lead_with_chores():
    import json

    manifest = json.loads(MANIFEST_JSON)
    description = manifest["description"]
    assert not description.strip().lower().startswith("chores")
    assert description == (
        "Meals on the table — the plan, the list, the cooking — and a "
        "hand with the rest of the house."
    )


# ==========================================================================
# 4. Unknown addresses: a branded page for a browser, JSON for everyone else
# ==========================================================================

UNKNOWN_PATH = "/this-page-does-not-exist-anywhere"


def test_unknown_path_gets_the_branded_page_for_a_browser_get(signed_in):
    res = signed_in.get(UNKNOWN_PATH, headers={"accept": "text/html,application/xhtml+xml"})
    assert res.status_code == 404
    assert "text/html" in res.headers["content-type"]
    assert "isn" in res.text and "t here" in res.text  # "isn't here" (curly apostrophe)
    assert 'href="/"' in res.text


def test_unknown_path_stays_json_for_a_non_html_caller(signed_in):
    """A fetch() (the app's own service worker, a stray API client) never sees HTML."""
    res = signed_in.get(UNKNOWN_PATH, headers={"accept": "application/json"})
    assert res.status_code == 404
    assert "application/json" in res.headers["content-type"]
    assert res.json() == {"detail": "Not Found"}


def test_unknown_path_stays_json_with_no_accept_header_at_all(signed_in):
    """The default shape of every existing test's client.get() — must not change."""
    res = signed_in.get(UNKNOWN_PATH)
    assert res.status_code == 404
    assert "application/json" in res.headers["content-type"]


def test_unknown_path_via_post_stays_json_even_for_a_browser(signed_in):
    """The branded page is GET-only — a POST to a dead address is not a wrong turn to fix up."""
    res = signed_in.post(UNKNOWN_PATH, headers={"accept": "text/html"})
    assert res.status_code == 404
    assert "application/json" in res.headers["content-type"]


def test_a_routes_own_404_is_not_repainted(signed_in):
    """
    /api/members/{name}/share-link raises HTTPException(404, "No household
    member named '...'") for a name that isn't real — a route WAS matched,
    it just has nothing for this id. That must keep answering JSON with its
    own sentence even when a browser asks, because it is not the "nothing
    here at all" case the branded page is for.
    """
    res = signed_in.get(
        "/api/members/Nobody-By-This-Name/share-link",
        headers={"accept": "text/html"},
    )
    assert res.status_code == 404
    assert "application/json" in res.headers["content-type"]
    assert "Nobody-By-This-Name" in res.json()["detail"]


def test_signed_out_visitor_still_hits_the_login_gate_on_an_unknown_path(client):
    """
    auth_middleware wraps the whole app, ahead of routing — a signed-out
    browser must be sent to /login exactly as it would from any other
    page, never straight to the 404 page for an address it was never going
    to see the routing answer for.
    """
    res = client.get(UNKNOWN_PATH, headers={"accept": "text/html"}, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"].startswith("/login")


# ==========================================================================
# 5. favicon.ico
# ==========================================================================

def test_favicon_is_served_with_a_long_cache_header(client):
    """Public (security._PUBLIC_EXACT already lists it) — no sign-in needed."""
    res = client.get("/favicon.ico")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    cache_control = res.headers.get("cache-control", "")
    assert "max-age=" in cache_control
    # A day-old cache header would still 404 on "every page load" the way
    # the card describes; require something that actually sticks.
    max_age = int(cache_control.split("max-age=")[1].split(",")[0])
    assert max_age >= 60 * 60 * 24 * 30
