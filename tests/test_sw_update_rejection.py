"""
"TypeError on /" at a household's very first open (live error report,
2026-10-02: Julia Home 2026-09-24, demo household 2026-10-01, Tester Home
2026-09-16 — the only error seen in more than one household).

What the rows said: an unhandled promise rejection (reason=unknown), class
TypeError, NO stack frames at all, a message that is not one of the
browser's network-failure strings, on "/", with a trail of only
"/ → GET /api/coaching 200 → GET /api/onboarding/status 200" — nothing
failed, and the next thing that happens is the redirect to /onboarding.

No stack frames means the rejection was made by the browser itself, not by
our code: a TypeError thrown anywhere in shell.js carries a shell.js frame
(see the cookPrepCutPicks rows in the same report). Of the promises the
shell starts at boot, the only browser-made one nobody catches is
`reg.update()` in the service-worker registration at the bottom of
shell.js — the registration's own .catch() sat on the outer chain, and
update()'s promise was dropped on the floor. ServiceWorkerRegistration
.update() rejects with a TypeError when its script fetch/job fails, and
the first open is exactly when it is most exposed: there is no worker yet,
so register() has only just started installing one, update() is queued
behind it, and the page then navigates away (to /onboarding, or the
controllerchange reload that skipWaiting + clients.claim triggers).

Nothing on screen changed for the person — a failed update is retried on
the next load — but the rejection went to the error report as a bug.

This runs the real registration block from shell.js under node with a
stand-in navigator whose update() rejects with a stackless TypeError, and
checks that nothing is left unhandled.
"""
from __future__ import annotations

import json
import os
import re

from tests.nodeharness import run_node

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELL = os.path.join(ROOT, "static", "shell.js")


def _registration_block() -> str:
    with open(SHELL, encoding="utf-8") as f:
        src = f.read()
    start = src.index("  if ('serviceWorker' in navigator) {")
    # The block is the last thing in the shell's IIFE.
    end = src.index("\n})();", start)
    block = src[start:end]
    assert "serviceWorker.register" in block
    return block


def _run(update_behaviour: str) -> dict:
    script = r"""
var unhandled = [];
var warned = [];
var updateCalls = 0;
process.on('unhandledRejection', function (reason) {
  unhandled.push(reason && reason.name);
});
var console = { warn: function () { warned.push(Array.prototype.slice.call(arguments).length); } };
var loadHandlers = [];
var window = {
  addEventListener: function (name, fn) { if (name === 'load') loadHandlers.push(fn); },
  location: { reload: function () {} },
};
function stacklessTypeError() {
  // What the browser hands back: a TypeError with no JS frames on it.
  var e = new TypeError('a browser-written message');
  e.stack = '';
  return e;
}
var reg = {
  update: function () {
    updateCalls++;
    return %(UPDATE)s;
  },
};
var navigator = {
  serviceWorker: {
    register: function () { return Promise.resolve(reg); },
    addEventListener: function () {},
  },
};
(function () {
%(BLOCK)s
})();
loadHandlers.forEach(function (fn) { fn(); });
setTimeout(function () {
  console.log = undefined;
  process.stdout.write(JSON.stringify({ unhandled: unhandled, warned: warned.length, updateCalls: updateCalls }));
}, 50);
""" % {"UPDATE": update_behaviour, "BLOCK": _registration_block()}
    # `console` is shadowed above for the block; write the result directly.
    proc = run_node(script)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_a_rejected_service_worker_update_is_handled_not_left_unhandled():
    out = _run("Promise.reject(stacklessTypeError())")
    assert out["updateCalls"] == 1, "the update is still asked for"
    assert out["unhandled"] == [], (
        "reg.update()'s rejection escaped as an unhandled rejection — the "
        "'TypeError on /' first-open row in the live error report"
    )
    assert out["warned"] == 1, "the failure is still noted in the console"


def test_a_successful_update_still_runs_quietly():
    out = _run("Promise.resolve(reg)")
    assert out == {"unhandled": [], "warned": 0, "updateCalls": 1}


def test_no_bare_update_call_is_left_in_the_shell():
    # A bare `reg.update();` statement is the shape that dropped the promise.
    with open(SHELL, encoding="utf-8") as f:
        src = f.read()
    assert not re.search(r"^\s*reg\.update\(\);", src, re.M)
