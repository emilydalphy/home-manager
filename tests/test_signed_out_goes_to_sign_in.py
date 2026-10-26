"""
Signed out: a screen goes to sign-in, not "Couldn't check just now".

Loop Board, 2026-10-26. A security reset rotated sign-ins, every API call
from Emily's phone came back 401, and Today said "Couldn't check just now —
pull to refresh." — which reads as the app or the connection being broken.
The server now marks its "not signed in" 401 (app/security.py
SIGNED_OUT_HEADER) and static/api.js sends the page to /login?next=<this
screen> on it, once, for every screen at once. Real failures (a 500, no
connection) still reach the screen's own "couldn't check" line.
"""
from __future__ import annotations

import json
import pathlib
import shutil

import nodeharness
import pytest

from app import security

STATIC = pathlib.Path(__file__).resolve().parent.parent / "static"
API_JS = (STATIC / "api.js").read_text(encoding="utf-8")
SHELL_JS = (STATIC / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs api.js")


# ---------- the server's marker ----------

def test_a_signed_out_read_carries_the_marker(client):
    res = client.get("/api/today/moves")
    assert res.status_code == 401
    assert res.headers.get(security.SIGNED_OUT_HEADER) == "1"
    assert security.SIGNED_OUT_HEADER in res.headers.get("access-control-expose-headers", "")


def test_a_signed_in_read_and_a_wrong_passphrase_carry_no_marker(client, signed_in):
    assert signed_in.get("/api/today/moves").headers.get(security.SIGNED_OUT_HEADER) is None
    # The sign-in screen's own refusals are its to show — never "go sign in".
    signed_in.cookies.clear()
    wrong = signed_in.post("/login", data={"password": "nope", "next": "/"}, follow_redirects=False)
    assert wrong.headers.get(security.SIGNED_OUT_HEADER) is None
    code = signed_in.post("/api/auth/email/verify", json={"email": "a@example.com", "code": "000000"})
    assert code.status_code != 200
    assert code.headers.get(security.SIGNED_OUT_HEADER) is None


# ---------- api.js under node ----------

_STUB = """
const calls = [];
const went = [];
let forgot = 0;
const location = { pathname: '/', search: '', replace: (u) => went.push(u) };
function headersOf(h) { return { get: (n) => (h || {})[n] === undefined ? null : h[n] }; }
function makeRes(status, h) {
  return { ok: status >= 200 && status < 300, status: status, headers: headersOf(h),
           text: () => Promise.resolve('{}'), json: () => Promise.resolve({}) };
}
const SIGNED_OUT = { 'X-Pomona-Signed-Out': '1' };
let answer = () => Promise.resolve(makeRes(200));
function fetch(url, init) { calls.push(url); return answer(url, init); }
const tick = () => new Promise((r) => setTimeout(r, 20));
// Settled within a beat, or still waiting — the signed-out answer never settles.
async function settles(p) {
  let done = false;
  p.then(() => { done = true; }, () => { done = true; });
  await tick();
  return done;
}
"""


def _run(body: str, prelude: str = ""):
    script = _STUB + prelude + API_JS + "\n(async () => {\n" + body + "\n})().catch(e => { console.error(e); process.exit(1); });"
    res = nodeharness.run_node(script)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@_needs_node
def test_signed_out_goes_to_sign_in_once_with_next_and_never_answers():
    out = _run("""
Api.onSignedOut(() => { forgot++; });
location.pathname = '/week'; location.search = '?week=2026-10-26';
answer = () => Promise.resolve(makeRes(401, SIGNED_OUT));
const first = await settles(Api.fetch('/api/week-menu'));
const json = await settles(Api.json('/api/recipes'));
const again = await settles(Api.fetch('/api/today/moves'));
console.log(JSON.stringify({ first, json, again, went, forgot }));
""")
    assert out["first"] is False and out["json"] is False and out["again"] is False
    assert out["went"] == ["/login?next=%2Fweek%3Fweek%3D2026-10-26"], "one trip, back to this screen"
    assert out["forgot"] == 1, "the offline grocery forget still runs, once"


@_needs_node
def test_real_failures_and_unmarked_401s_reach_the_caller():
    out = _run("""
const seen = [];
for (const [status, h] of [[500, null], [401, null], [503, null]]) {
  answer = () => Promise.resolve(makeRes(status, h));
  seen.push((await Api.fetch('/api/today/moves')).status);
}
answer = () => Promise.reject(new TypeError('Failed to fetch'));
let dropped = null;
try { await Api.fetch('/api/today/moves'); } catch (e) { dropped = e.message; }
console.log(JSON.stringify({ seen, dropped, went }));
""")
    assert out == {"seen": [500, 401, 503], "dropped": "Failed to fetch", "went": []}


@_needs_node
@pytest.mark.parametrize("page", ["/login", "/join", "/share/abc123", "/member-share/x", "/goodbye"])
def test_the_way_in_and_public_pages_never_leave(page):
    out = _run("""
location.pathname = %s;
answer = () => Promise.resolve(makeRes(401, SIGNED_OUT));
const res = await Api.fetch('/api/whatever');
console.log(JSON.stringify({ status: res.status, went }));
""" % json.dumps(page))
    assert out == {"status": 401, "went": []}


@_needs_node
def test_with_a_base_sign_in_is_on_that_site():
    out = _run("""
Api.setBase('https://pomona.example');
answer = () => Promise.resolve(makeRes(401, SIGNED_OUT));
await settles(Api.fetch('/api/today/moves'));
console.log(JSON.stringify({ went }));
""")
    assert out["went"] == ["https://pomona.example/login?next=%2F"]


# ---------- Today itself (the card's test) ----------

def _function(name: str) -> str:
    start = SHELL_JS.index("  async function %s(" % name)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end] + "\n  }\n"


_TODAY = """
const drawn = [];
function renderTodayMovesError() { drawn.push('couldnt-check'); }
function renderTodayMoves() { drawn.push('moves'); }
function renderSinceLooked() {}
console.warn = () => {};
"""


@_needs_node
@pytest.mark.parametrize("status, signed_out, expect_went, expect_drawn", [
    (401, True, ["/login?next=%2F"], []),
    (500, False, [], ["couldnt-check"]),
])
def test_today_signed_out_goes_to_sign_in_and_a_500_still_says_couldnt_check(
        status, signed_out, expect_went, expect_drawn):
    out = _run("""
answer = () => Promise.resolve(makeRes(%d, %s));
%s
loadTodayMoves({});
await tick();
console.log(JSON.stringify({ went, drawn }));
""" % (status, "SIGNED_OUT" if signed_out else "null", _function("loadTodayMoves")), prelude=_TODAY)
    assert out == {"went": expect_went, "drawn": expect_drawn}
