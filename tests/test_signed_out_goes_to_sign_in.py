"""
Signed out: Today sends you to sign in, not "Couldn't check just now"
(Loop Board, 2026-10-26).

A phone whose session had ended (expired, or a security reset) opened Today
and read "Couldn't check just now — pull to refresh." — the screen's line
for a failed lookup, because loadTodayMoves treated the gate's 401 like any
other non-OK answer. Pulling never helped. Now static/api.js takes any 401
to /login?next=<where you were>, and the screens that draw a "couldn't
check" line (Today, Plan's week and chores, Settings' saved recipes) ask
Api.signingIn() first, so the line never shows on the way out.

What these pin, under node with the real api.js and the real shell.js
functions:
  - a 401 through Api.fetch goes to sign in with path + query + hash as next,
    once per page, and still hands the caller its Response;
  - a 500 or a dropped connection does not;
  - not on the sign-in pages themselves (no loop), and not with a base set
    (the App Store build serves its screens from the phone);
  - Api.json toasts nothing on the way out;
  - Today: a 401 draws no "couldn't check", a 500 still does;
  - the server's side of it: a signed-out read of /api/today/moves is a 401.
"""
from __future__ import annotations

import json
import pathlib
import shutil

import nodeharness
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
API_JS = (ROOT / "static" / "api.js").read_text(encoding="utf-8")
SHELL_JS = (ROOT / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs api.js")

_STUB = """
const went = [];
const toasts = [];
var location = {
  pathname: '/', search: '', hash: '',
  replace: function (u) { went.push(u); },
};
let answer = () => Promise.resolve(makeRes(200, '{"ok":true}'));
function makeRes(status, text) {
  return { ok: status >= 200 && status < 300, status: status,
           text: () => Promise.resolve(text), json: () => Promise.resolve(JSON.parse(text || '{}')) };
}
function fetch(url, init) { return answer(url, init); }
"""


def _run(body: str, prelude: str = ""):
    script = _STUB + prelude + API_JS + "\n(async () => {\n" + body + "\n})().catch(e => { console.error(e); process.exit(1); });"
    res = nodeharness.run_node(script)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def _function(name: str) -> str:
    start = SHELL_JS.index("  async function %s(" % name)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end] + "\n  }\n"


# ---------------------------------------------------------------------------
# api.js
# ---------------------------------------------------------------------------

@_needs_node
def test_a_401_goes_to_sign_in_with_where_you_were_as_next():
    out = _run("""
location.pathname = '/week'; location.search = '?week=2026-10-26'; location.hash = '#chores';
answer = () => Promise.resolve(makeRes(401, '{"detail":"Please sign in again."}'));
const res = await Api.fetch('/api/today/moves');
await Api.fetch('/api/week-menu');   // a second 401 on the same page: one trip
console.log(JSON.stringify({ went, status: res.status, signingIn: Api.signingIn() }));
""")
    assert out["went"] == ["/login?next=%2Fweek%3Fweek%3D2026-10-26%23chores"]
    # The caller still gets its Response (the grocery copy's own 401
    # handling, groForgetOffline, runs off it).
    assert out["status"] == 401
    assert out["signingIn"] is True


@_needs_node
def test_a_page_leaving_for_goodbye_is_not_pulled_to_sign_in():
    # Delete household / remove me: the server clears the cookie, the page
    # calls Api.leaving() and goes to /goodbye; a read still in flight then
    # comes back 401 and must not replace /goodbye with /login.
    out = _run("""
answer = () => Promise.resolve(makeRes(401, '{}'));
Api.leaving();
await Api.fetch('/api/today/moves');
console.log(JSON.stringify({ went }));
""")
    assert out["went"] == []
    leave = SHELL_JS[SHELL_JS.index("groForgetOffline();   // the grocery copy on this phone was the household's"):]
    leave = leave[:leave.index("/goodbye')")]
    assert "Api.leaving()" in leave


@_needs_node
def test_a_server_error_or_no_connection_stays_put():
    out = _run("""
answer = () => Promise.resolve(makeRes(500, 'Internal Server Error'));
await Api.fetch('/api/today/moves');
answer = () => Promise.resolve(makeRes(403, '{}'));
await Api.fetch('/api/x');
answer = () => Promise.reject(new TypeError('Failed to fetch'));
try { await Api.fetch('/api/today/moves'); } catch (e) {}
console.log(JSON.stringify({ went, signingIn: Api.signingIn() }));
""")
    assert out == {"went": [], "signingIn": False}


@_needs_node
@pytest.mark.parametrize("page", ["/login", "/join", "/goodbye"])
def test_never_from_a_sign_in_page_itself(page):
    out = _run("""
location.pathname = %s;
answer = () => Promise.resolve(makeRes(401, '{}'));
await Api.fetch('/api/whoami');
console.log(JSON.stringify({ went, signingIn: Api.signingIn() }));
""" % json.dumps(page))
    assert out == {"went": [], "signingIn": False}


@_needs_node
def test_not_with_a_base_set_the_app_store_build_decides_for_itself():
    out = _run("""
Api.setBase('https://pomona.example');
answer = () => Promise.resolve(makeRes(401, '{}'));
await Api.fetch('/api/today/moves');
console.log(JSON.stringify({ went }));
""")
    assert out == {"went": []}


@_needs_node
def test_api_json_toasts_nothing_on_the_way_to_sign_in():
    out = _run("""
Api.onError((said) => toasts.push(said));
answer = () => Promise.resolve(makeRes(401, '{"detail":"Please sign in again."}'));
let e; try { await Api.json('/api/recipes'); } catch (err) { e = err; }
console.log(JSON.stringify({ toasts, status: e.status, went: went.length }));
""")
    assert out == {"toasts": [], "status": 401, "went": 1}


# ---------------------------------------------------------------------------
# Today (shell.js loadTodayMoves)
# ---------------------------------------------------------------------------

_TODAY = """
const drawn = [];
function renderTodayMovesError() { drawn.push('couldnt-check'); }
function renderTodayMoves() { drawn.push('moves'); }
function renderSinceLooked() {}
console.warn = () => {};
"""


@_needs_node
@pytest.mark.parametrize("status,drawn,went", [
    (401, [], 1),
    (500, ["couldnt-check"], 0),
])
def test_today_signed_out_goes_to_sign_in_and_a_500_still_says_couldnt_check(status, drawn, went):
    out = _run(_function("loadTodayMoves") + """
answer = () => Promise.resolve(makeRes(%d, '{}'));
await loadTodayMoves({});
console.log(JSON.stringify({ drawn, went: went.length }));
""" % status, prelude=_TODAY)
    assert out == {"drawn": drawn, "went": went}


@_needs_node
def test_today_with_no_connection_still_says_couldnt_check():
    out = _run(_function("loadTodayMoves") + """
answer = () => Promise.reject(new TypeError('Failed to fetch'));
await loadTodayMoves({});
console.log(JSON.stringify({ drawn, went: went.length }));
""", prelude=_TODAY)
    assert out == {"drawn": ["couldnt-check"], "went": 0}


def test_the_other_couldnt_check_lines_ask_first():
    """Plan's week and chores, and Settings' saved recipes, draw their own
    "couldn't check" from a catch — each one asks Api.signingIn() first."""
    for name, marker in [
        ("loadWeekMenu", "Couldn\\'t load your week right now."),
        ("loadPlanChores", "weekState.choresTrouble = true"),
        ("loadRecipes", "recipesState.failed = true"),
    ]:
        body = _function(name)
        catch = body[body.index("} catch (err) {"):]
        assert "Api.signingIn()" in catch[:catch.index(marker)], name


# ---------------------------------------------------------------------------
# The server's half: the signal this keys on
# ---------------------------------------------------------------------------

def test_a_signed_out_read_of_today_is_a_401(client):
    res = client.get("/api/today/moves")
    assert res.status_code == 401
    assert res.json() == {"detail": "Please sign in again."}


def test_sign_in_brings_you_back_to_where_next_says(client):
    res = client.post(
        "/login", data={"password": "test-password", "next": "/?tab=today#now"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/?tab=today#now"
