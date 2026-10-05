"""
static/api.js — the one place screens talk to the server through
(Loop Board: "App Store (long term): one shared api.js", 2026-09-27).

First slice: api.js exists, shell.js's 103 `fetch('/api…')` calls (and its
11 helper-built ones) go through `Api.fetch`, and a ratchet keeps the raw
count from growing while the other screens migrate one branch at a time.

What these pin:
  - the ratchet itself (raw calls in static/ never go UP);
  - shell.js makes no bare `fetch(` at all any more;
  - api.js is served to signed-in pages only, loaded in shell.html after
    error-reporter.js and before every page script;
  - Api.fetch is fetch: same address (until a base is set), same init plus
    cookies, same Response, same rejection — the property that makes the
    shell.js move "no behaviour change";
  - Api.json's JSON / FormData / error behaviour, for the code that comes
    next;
  - error-reporter.js keeps naming the screen, not api.js, as the place a
    dropped request came from.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil

import nodeharness
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
API_JS = (STATIC / "api.js").read_text(encoding="utf-8")
SHELL_JS = (STATIC / "shell.js").read_text(encoding="utf-8")
SHELL_HTML = (STATIC / "shell.html").read_text(encoding="utf-8")
REPORTER_JS = (STATIC / "error-reporter.js").read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------
# A raw call is `fetch(` + a quote/backtick + `/api`, not preceded by a dot
# or a word character — so `Api.fetch('/api…')` and `window.fetch(` in a
# test stub are not counted, and `fetch( '/api` with a space is.
#
# 150 on main when the card was un-parked (2026-09-27 — the card's "151"
# was a plain text search, which also caught one `window.fetch('/api`);
# 47 after shell.js moved onto api.js; 34 after inventory.html (13);
# 24 after plan-week.html (10 — its eleventh call builds its url in a
# variable first, which this pattern cannot see, and moved with them);
# 13 after onboarding.html (11). help-sheet.js's one call moved with it
# and changes nothing here: it was written `global.fetch('/api…')`, which
# this pattern cannot see. It was the last such call in static/ — see
# test_no_raw_call_hides_behind_an_object_any_more.
# LOWER this when a screen migrates. Never raise it: a
# new call is written with Api.json / Api.fetch instead (CLAUDE.md).
RAW_API_FETCH_CEILING = 13

_RAW = re.compile(r"(?<![\w.$])fetch\(\s*['\"`]/api")


def _raw_counts() -> dict[str, int]:
    out = {}
    for path in sorted(list(STATIC.glob("*.html")) + list(STATIC.glob("*.js"))):
        n = len(_RAW.findall(path.read_text(encoding="utf-8")))
        if n:
            out[path.name] = n
    return out


def test_raw_api_fetch_count_never_goes_up():
    counts = _raw_counts()
    total = sum(counts.values())
    assert total <= RAW_API_FETCH_CEILING, (
        f"{total} raw fetch('/api…') calls in static/ (ceiling {RAW_API_FETCH_CEILING}): "
        f"{counts}. New code goes through static/api.js — Api.json(path, opts), or "
        "Api.fetch(path, init) for a raw Response. See CLAUDE.md."
    )


def test_the_ceiling_is_kept_tight():
    """A migrated screen lowers the ceiling in the same branch — otherwise
    the slack it leaves is room for new raw calls to creep back in."""
    total = sum(_raw_counts().values())
    assert total == RAW_API_FETCH_CEILING, (
        f"raw count is {total} but the ceiling says {RAW_API_FETCH_CEILING}: "
        f"lower RAW_API_FETCH_CEILING to {total}"
    )


def test_no_raw_call_hides_behind_an_object_any_more():
    """The ratchet above deliberately skips `<something>.fetch('/api…')`, so
    `Api.fetch` does not count itself. help-sheet.js's one call was written
    `global.fetch('/api/feedback', …)` and was invisible to it for exactly
    that reason — a raw call wearing the shape of a migrated one. It went
    through Api.fetch on 2026-09-30 and it was the last of its kind, so this
    says so: anything but `Api.` in front of a `/api` fetch is a raw call
    the count above will not see."""
    dotted = re.compile(r"(\w+)\.fetch\(\s*['\"`]/api")
    found = {}
    for path in sorted(list(STATIC.glob("*.html")) + list(STATIC.glob("*.js"))):
        others = [m.group(1) for m in dotted.finditer(path.read_text(encoding="utf-8")) if m.group(1) != "Api"]
        if others:
            found[path.name] = others
    assert found == {}, (
        f"a /api call behind an object the ratchet cannot count: {found}. "
        "Use Api.fetch (static/api.js)."
    )


def test_the_ratchet_pattern_sees_every_quote_style_and_skips_api_fetch():
    sample = (
        "fetch('/api/a'); fetch(\"/api/b\"); fetch(`/api/c/${x}`); fetch( '/api/d');"
        " Api.fetch('/api/e'); window.fetch('/api/f'); myfetch('/api/g'); fetch('/static/x');"
    )
    assert len(_RAW.findall(sample)) == 4


def test_shell_js_makes_no_bare_fetch_at_all():
    """Not only the literal '/api…' ones: the helpers that take a url
    (groPost, cookPost, wwkPost, postJson, …) go through Api.fetch too."""
    bare = [
        (i + 1, line.strip())
        for i, line in enumerate(SHELL_JS.splitlines())
        if re.search(r"(?<![\w.$])fetch\(", line)
    ]
    assert bare == []
    assert SHELL_JS.count("Api.fetch(") == 131  # +2 2026-10-05 Morning text "What should it include?" (GET /api/morning-text/preview, POST /api/morning-text/parts), +1 2026-10-05 the silent re-pick (pickWhoSilently, POST /api/whoami/pick), +3 2026-10-05 Change recipe (POST /api/meal-recipe, /api/meal-recipe/rewrite, /api/meal-recipe/undo — one call site each, all three through Api.fetch because each reads a refusal sentence off a 200 or a 400 body rather than only a status), +1 2026-10-05 POST /api/memory/primary-member (wwkPostSaying — the tripwire fired on this line; wwkPost's own copy stays, so a 400's sentence is shown for the main person and nothing else changes), +2 2026-10-04 Settings → Recipes (GET /api/recipes, GET /api/recipes/{id}), +1 2026-09-30 POST /api/usual-week (uwPost, so a 400 is said in its words), +1 2026-09-30 GET /api/usual-week (Your rhythm), +1 2026-09-30 swap-picks prefetch, +1 chat warm-up (2026-09-30, /api/chat/warm); 114 at the first slice + 3 from the 2026-09-27 consent and delete-household branches + 1 net from Move (2026-09-28: move-options/move-meal/move-meal-undo in, swap-nights/-undo out of the Plan sheet)


# ---------------------------------------------------------------------------
# Served like shell.js
# ---------------------------------------------------------------------------

def test_api_js_is_signed_in_only(client):
    """Same rule as shell.js: not in app/security.py's public list."""
    anon = client.get("/static/api.js", follow_redirects=False)
    assert anon.status_code != 200
    assert client.get("/static/shell.js", follow_redirects=False).status_code == anon.status_code
    client.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    assert client.get("/static/api.js", follow_redirects=False).status_code == 200


def test_shell_html_loads_api_js_after_the_reporter_and_before_every_page_script():
    order = re.findall(r'<script src="/static/([^"]+)"', SHELL_HTML)
    assert "api.js" in order
    assert order.index("error-reporter.js") < order.index("api.js")
    for page_script in order[order.index("api.js") + 1:]:
        assert page_script != "error-reporter.js"
    assert order.index("api.js") < order.index("shell.js")


def test_the_service_worker_treats_api_js_like_shell_js():
    """Network-first for every .js (so a deploy is picked up), and a cached
    copy for offline Shop — api.js needs no list of its own to get that."""
    sw = (STATIC / "service-worker.js").read_text(encoding="utf-8")
    assert 'url.pathname.endsWith(".js")' in sw
    assert "shell.js" not in re.sub(r"//.*", "", sw), "the worker names no script by name"


# ---------------------------------------------------------------------------
# Behaviour, under node
# ---------------------------------------------------------------------------

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs api.js")

_STUB = """
const calls = [];
const toasts = [];
let answer = (url, init) => Promise.resolve(makeRes(200, '{"ok":true}'));
function makeRes(status, text) {
  return { ok: status >= 200 && status < 300, status: status, text: () => Promise.resolve(text) };
}
function fetch(url, init) { calls.push({ url: url, init: init }); return answer(url, init); }
class FormData { append() {} }
"""


def _run(body: str):
    res = nodeharness.run_node(_STUB + API_JS + "\n(async () => {\n" + body + "\n})().catch(e => { console.error(e); process.exit(1); });")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@_needs_node
def test_api_fetch_is_fetch_with_cookies_and_nothing_else():
    out = _run("""
const init = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"a":1}', keepalive: true };
const sentinel = { i: 'the response' };
answer = () => Promise.resolve(sentinel);
const res = await Api.fetch('/api/x?y=1', init);
const err = new TypeError('Failed to fetch');
answer = () => Promise.reject(err);
let caught = null;
try { await Api.fetch('/api/z'); } catch (e) { caught = e; }
answer = () => Promise.resolve(sentinel);
await Api.fetch('/api/own', { credentials: 'same-origin' });
console.log(JSON.stringify({
  sameResponse: res === sentinel,
  sameRejection: caught === err,
  url: calls[0].url,
  init: calls[0].init,
  initUntouched: !('credentials' in init),
  noInit: calls[1].init,
  callerWins: calls[2].init.credentials,
}));
""")
    assert out["sameResponse"] and out["sameRejection"]
    assert out["url"] == "/api/x?y=1"
    assert out["init"] == {
        "method": "POST", "headers": {"Content-Type": "application/json"},
        "body": '{"a":1}', "keepalive": True, "credentials": "include",
    }
    assert out["initUntouched"]
    assert out["noInit"] == {"credentials": "include"}
    assert out["callerWins"] == "same-origin"


@_needs_node
def test_a_base_url_prefixes_root_paths_only():
    out = _run("""
const before = Api.url('/api/x');
Api.setBase('https://pomona.example/');
await Api.fetch('/api/week');
console.log(JSON.stringify({
  before: before,
  base: Api.getBase(),
  sent: calls[0].url,
  full: Api.url('https://elsewhere.example/api/x'),
  protocolRelative: Api.url('//cdn.example/x'),
  relative: Api.url('api/x'),
}));
""")
    assert out == {
        "before": "/api/x",
        "base": "https://pomona.example",
        "sent": "https://pomona.example/api/week",
        "full": "https://elsewhere.example/api/x",
        "protocolRelative": "//cdn.example/x",
        "relative": "api/x",
    }


@_needs_node
def test_api_json_sends_json_and_reads_json():
    out = _run("""
Api.onError((said) => toasts.push(said));
answer = () => Promise.resolve(makeRes(200, '{"items":[1,2]}'));
const got = await Api.json('/api/grocery-list/add', { body: { item: 'milk' } });
answer = () => Promise.resolve(makeRes(204, ''));
const empty = await Api.json('/api/thing', { method: 'delete' });
const read = await Api.json('/api/read');
console.log(JSON.stringify({ got, empty, read, first: calls[0].init, second: calls[1].init, third: calls[2].init, toasts }));
""")
    assert out["got"] == {"items": [1, 2]}
    assert out["empty"] == {}
    assert out["first"] == {
        "method": "POST", "headers": {"Content-Type": "application/json"},
        "body": '{"item":"milk"}', "credentials": "include",
    }
    assert out["second"]["method"] == "DELETE"
    assert out["third"]["method"] == "GET" and "body" not in out["third"]
    assert out["toasts"] == []


@_needs_node
def test_api_json_passes_a_formdata_upload_through_untouched():
    out = _run("""
const form = new FormData();
await Api.json('/api/recipes/photo', { body: form });
console.log(JSON.stringify({ sameBody: calls[0].init.body === form, headers: calls[0].init.headers, method: calls[0].init.method }));
""")
    # No Content-Type: the browser writes the multipart boundary itself.
    assert out == {"sameBody": True, "headers": {}, "method": "POST"}


@_needs_node
def test_api_json_failure_toasts_and_rejects_with_the_servers_detail():
    out = _run("""
Api.onError((said, err) => toasts.push(said));
answer = () => Promise.resolve(makeRes(409, '{"detail":"That week is already approved."}'));
let e1; try { await Api.json('/api/week/2026-09-21/approve', { body: {} }); } catch (e) { e1 = e; }
answer = () => Promise.resolve(makeRes(500, 'Internal Server Error'));
let e2; try { await Api.json('/api/week-menu'); } catch (e) { e2 = e; }
let e3; try { await Api.json('/api/x', { body: {}, quiet: true }); } catch (e) { e3 = e; }
let e4; try { await Api.json('/api/x', { body: {}, errorMessage: 'Couldn’t move that — try again.' }); } catch (e) { e4 = e; }
const net = new TypeError('Failed to fetch');
answer = () => Promise.reject(net);
let e5; try { await Api.json('/api/members/7/share-link'); } catch (e) { e5 = e; }
console.log(JSON.stringify({
  toasts,
  e1: { name: e1.name, status: e1.status, detail: e1.detail, route: e1.pomonaRoute },
  e2: { status: e2.status, detail: e2.detail, body: e2.body },
  e3status: e3.status,
  e5: { same: e5 === net, status: e5.status, route: e5.pomonaRoute, message: e5.message },
}));
""")
    assert out["toasts"] == [
        "Couldn’t save that — try again.",
        "Couldn’t load that — try again.",
        "Couldn’t move that — try again.",
        "Couldn’t load that — try again.",
    ]
    # The route, never the value in it — same rule as error-reporter.js.
    assert out["e1"] == {
        "name": "ApiError", "status": 409,
        "detail": "That week is already approved.", "route": "/api/week/{}/approve",
    }
    assert out["e2"] == {"status": 500, "detail": "", "body": None}
    assert out["e3status"] == 500  # quiet: rejected, no toast
    # A dropped request keeps the browser's own error (its message is what
    # the reporter matches to say "network"), tagged, not replaced.
    assert out["e5"] == {"same": True, "status": 0, "route": "/api/members/{}/share-link", "message": "Failed to fetch"}


@_needs_node
def test_a_page_that_registers_no_toast_just_gets_the_rejection():
    out = _run("""
answer = () => Promise.resolve(makeRes(500, ''));
let e; try { await Api.json('/api/x'); } catch (err) { e = err; }
console.log(JSON.stringify({ status: e.status }));
""")
    assert out == {"status": 500}


def test_shell_js_hands_api_json_its_own_toast():
    assert "Api.onError(function (said) { showToast(said); });" in SHELL_JS


@_needs_node
def test_api_json_edge_cases_found_on_review():
    """Found by the independent review of this branch (2026-09-27): a
    Headers instance was dropped, a lower-case content-type got a second
    one beside it, `body: null` sent "null", URLSearchParams was turned
    into "{}", a body that dropped mid-read skipped the toast and the tag,
    and a caller's own abort toasted."""
    out = _run("""
Api.onError((said) => toasts.push(said));
await Api.json('/api/a', { method: 'POST', headers: new Headers({ 'X-One': '1' }), body: { a: 1 } });
await Api.json('/api/b', { headers: { 'content-type': 'application/json' }, body: { b: 1 } });
await Api.json('/api/c', { body: null });
const qs = new URLSearchParams('x=1');
await Api.json('/api/d', { body: qs });
answer = () => Promise.resolve({ ok: true, status: 200, text: () => Promise.reject(new TypeError('network error')) });
let e1; try { await Api.json('/api/week/2026-09-21/e'); } catch (e) { e1 = e; }
const abort = new Error('aborted'); abort.name = 'AbortError';
answer = () => Promise.reject(abort);
let e2; try { await Api.json('/api/f'); } catch (e) { e2 = e; }
console.log(JSON.stringify({
  a: calls[0].init.headers,
  b: calls[1].init.headers,
  c: { method: calls[2].init.method, hasBody: 'body' in calls[2].init },
  d: calls[3].init.body === qs && !('Content-Type' in calls[3].init.headers),
  e1: { status: e1.status, route: e1.pomonaRoute },
  e2same: e2 === abort,
  toasts,
}));
""")
    assert out["a"] == {"x-one": "1", "Content-Type": "application/json"}
    assert out["b"] == {"content-type": "application/json"}
    assert out["c"] == {"method": "GET", "hasBody": False}
    assert out["d"] is True
    assert out["e1"] == {"status": 0, "route": "/api/week/{}/e"}
    assert out["e2same"] is True
    assert out["toasts"] == ["Couldn’t load that — try again."], "the dropped read toasts; the abort does not"


# ---------------------------------------------------------------------------
# error-reporter.js still names the screen
# ---------------------------------------------------------------------------

_REPORTER_STUB = """
const listeners = {};
const sent = [];
global.window = {
  addEventListener: (n, fn) => { (listeners[n] = listeners[n] || []).push(fn); },
  fetch: function () { return Promise.resolve('ok'); },
};
global.location = { pathname: '/week', origin: 'https://pomona.example', href: 'https://pomona.example/week' };
global.URL = URL;
Object.defineProperty(global, 'navigator', {
  value: { sendBeacon: (url, blob) => { sent.push(JSON.parse(blob.body)); return true; } },
  configurable: true,
});
global.Blob = class { constructor(parts) { this.body = parts.join(''); } };
"""


@_needs_node
def test_a_dropped_request_through_api_js_is_placed_in_the_screen_not_api_js():
    """Every shell.js call now passes through Api.fetch on its way to the
    reporter's fetch wrapper, so Chrome's call-site stack for a dropped
    request has api.js's frame between the two. Before the move, `source`
    was the shell.js line; it still has to be."""
    fire = """
const err = new TypeError('Failed to fetch');
err.stack = [
  'TypeError: Failed to fetch',
  '    at window.fetch (https://pomona.example/static/error-reporter.js:151:30)',
  '    at Object.apiFetch [as fetch] (https://pomona.example/static/api.js:88:12)',
  '    at loadTheWeek (https://pomona.example/static/shell.js:6207:15)',
].join('\\n');
listeners['unhandledrejection'][0]({ reason: err });
console.log(JSON.stringify(sent));
"""
    res = nodeharness.run_node(_REPORTER_STUB + REPORTER_JS + fire)
    assert res.returncode == 0, res.stderr
    shape = json.loads(res.stdout.strip())[0]
    assert "api.js" not in json.dumps(shape)
    assert shape["source"] == "shell.js:6207:15"
    assert shape["stack"] == ["loadTheWeek@shell.js:6207:15"]
