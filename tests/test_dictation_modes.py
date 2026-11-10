"""
Which way the mic listens on each device (voice research, 2026-11-09).

Emily: "It's almost unusable it's so bad." The cause: on an iPhone, Safari
(and the iPhone app's web view) DO expose the Web Speech API, so the old
helper used it — and on iOS that is Apple's oldest recognizer, run one
phrase at a time, so it stopped at the first pause and dropped words. The
helper's own comment still said iOS had no such API at all.

static/dictation.js now picks per device:
  - iPhone app with the new engine (PomonaSpeech plugin says available) → native
  - any other iPhone / iPad → the keyboard's own mic (never Web Speech)
  - desktop → Web Speech, listening until tapped again (continuous)
  - Android → Web Speech, one phrase at a time (continuous repeats words there)

These run the real file under node with a fake browser around it.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import nodeharness
import pytest

ROOT = Path(__file__).resolve().parent.parent
DICTATION_JS = (ROOT / "static" / "dictation.js").read_text()
SHELL_JS = (ROOT / "static" / "shell.js").read_text()

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 26_1 like Mac OS X) AppleWebKit/605.1.15 Version/26.1 Mobile/15E148 Safari/604.1"
IPHONE_APP_UA = IPHONE_UA + " PomonaApp/1.0.0"
DESKTOP_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
ANDROID_UA = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36"

PRELUDE = """
var log = { recognitions: [], messages: [], focused: 0, started: 0, stopped: 0, texts: [] };
var window = { navigator: { userAgent: %(ua)s } };
var document = {};
if (%(touch)s) document.ontouchend = null;
window.webkitSpeechRecognition = function () {
  var self = this;
  log.recognitions.push(self);
  this.start = function () { log.started += 1; };
  this.stop = function () { log.stopped += 1; if (self.onend) self.onend(); };
};
%(capacitor)s
function alert(m) { log.messages.push(m); }
var input = { value: '', placeholder: 'Ask Pomona', focus: function () { log.focused += 1; } };
var clicks = [];
var micBtn = { dataset: {}, classes: {},
  classList: { add: function (c) { micBtn.classes[c] = true; }, remove: function (c) { delete micBtn.classes[c]; } },
  addEventListener: function (ev, fn) { clicks.push(fn); } };
"""

NATIVE_PLUGIN = """
var listeners = {};
var nativeCalls = [];
window.Capacitor = {
  isNativePlatform: function () { return true; },
  isPluginAvailable: function (n) { return n === 'PomonaSpeech'; },
  Plugins: { PomonaSpeech: {
    isAvailable: function () { nativeCalls.push('isAvailable'); return Promise.resolve({ available: %(available)s }); },
    start: function () { nativeCalls.push('start'); return %(start)s; },
    stop: function () { nativeCalls.push('stop'); return Promise.resolve({ session: 1, text: 'two onions and a lemon.' }); },
    addListener: function (name, fn) { listeners[name] = fn; return Promise.resolve({ remove: function () {} }); }
  } }
};
"""


def _run(ua, body, touch=False, capacitor=""):
    script = PRELUDE % {"ua": json.dumps(ua), "touch": "true" if touch else "false", "capacitor": capacitor}
    script += DICTATION_JS + "\n" + body
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@_needs_node
def test_an_iphone_never_uses_safaris_speech_api():
    out = _run(IPHONE_UA, """
var h = window.setupDictation(input, micBtn, { onMessage: function (m) { log.messages.push(m); } });
clicks[0](); clicks[0]();
console.log(JSON.stringify({ mode: h.mode(), recs: log.recognitions.length, started: log.started,
  focused: log.focused, messages: log.messages }));
""")
    assert out["mode"] == "keyboard"
    assert out["recs"] == 0 and out["started"] == 0
    assert out["focused"] == 2
    # The hint says where the keyboard's mic is, once, not on every tap.
    assert out["messages"] == ["Tap the mic on your keyboard and start talking."]


@_needs_node
def test_an_ipad_that_calls_itself_a_mac_counts_as_an_iphone():
    out = _run("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/26.1 Safari/605.1.15", """
var h = window.setupDictation(input, micBtn, {});
clicks[0]();
console.log(JSON.stringify({ mode: h.mode(), started: log.started }));
""", touch=True)
    assert out == {"mode": "keyboard", "started": 0}


@_needs_node
def test_desktop_listens_until_the_mic_is_tapped_again():
    out = _run(DESKTOP_UA, """
var h = window.setupDictation(input, micBtn, {});
clicks[0]();
var rec = log.recognitions[0];
rec.onresult({ results: [ { isFinal: true, 0: { transcript: 'chicken thighs' } },
                          { isFinal: false, 0: { transcript: ' and rice' } } ] });
var mid = { value: input.value, active: !!micBtn.classes.active, placeholder: input.placeholder };
clicks[0]();
console.log(JSON.stringify({ mode: h.mode(), continuous: rec.continuous, mid: mid,
  stopped: log.stopped, active: !!micBtn.classes.active, placeholder: input.placeholder }));
""")
    assert out["mode"] == "web"
    assert out["continuous"] is True
    assert out["mid"]["value"] == "chicken thighs and rice"
    assert out["mid"]["active"] is True
    assert out["mid"]["placeholder"].startswith("Listening")
    assert out["stopped"] == 1 and out["active"] is False
    assert out["placeholder"] == "Ask Pomona"


@_needs_node
def test_android_stays_one_phrase_at_a_time():
    out = _run(ANDROID_UA, """
window.setupDictation(input, micBtn, {});
clicks[0]();
console.log(JSON.stringify({ continuous: log.recognitions[0].continuous }));
""")
    assert out == {"continuous": False}


@_needs_node
def test_dictation_keeps_what_was_already_typed():
    out = _run(DESKTOP_UA, """
var grew = 0;
window.setupDictation(input, micBtn, { onText: function () { grew += 1; } });
input.value = 'Add ';
clicks[0]();
log.recognitions[0].onresult({ results: [ { isFinal: true, 0: { transcript: 'oat milk' } } ] });
console.log(JSON.stringify({ value: input.value, grew: grew }));
""")
    assert out == {"value": "Add oat milk", "grew": 1}


@_needs_node
def test_the_iphone_app_uses_its_own_engine_when_it_has_one():
    cap = NATIVE_PLUGIN % {"available": "true", "start": "Promise.resolve({ session: 1 })"}
    out = _run(IPHONE_APP_UA, """
var started = 0;
var h = window.setupDictation(input, micBtn, { onStart: function () { started += 1; } });
setTimeout(function () {
  clicks[0]();
  setTimeout(function () {
  listeners.partial({ session: 1, text: 'two onions' });
  var mid = input.value;
  clicks[0]();
  setTimeout(function () {
    console.log(JSON.stringify({ mode: h.mode(), calls: nativeCalls, mid: mid, value: input.value,
      started: started, recs: log.recognitions.length, active: !!micBtn.classes.active }));
  }, 10);
  }, 10);
}, 10);
""", capacitor=cap)
    assert out["mode"] == "native"
    assert out["calls"] == ["isAvailable", "start", "stop"]
    assert out["mid"] == "two onions"
    assert out["value"] == "two onions and a lemon."
    assert out["started"] == 1
    assert out["recs"] == 0
    assert out["active"] is False


@_needs_node
def test_the_iphone_app_on_an_older_ios_uses_the_keyboard():
    cap = NATIVE_PLUGIN % {"available": "false", "start": "Promise.resolve({ session: 1 })"}
    out = _run(IPHONE_APP_UA, """
var h = window.setupDictation(input, micBtn, {});
setTimeout(function () {
  clicks[0]();
  console.log(JSON.stringify({ mode: h.mode(), calls: nativeCalls, recs: log.recognitions.length, focused: log.focused }));
}, 10);
""", capacitor=cap)
    assert out == {"mode": "keyboard", "calls": ["isAvailable"], "recs": 0, "focused": 1}


@_needs_node
def test_a_failed_start_falls_back_to_the_keyboard():
    cap = NATIVE_PLUGIN % {"available": "true", "start": "Promise.reject({ code: 'failed' })"}
    out = _run(IPHONE_APP_UA, """
var h = window.setupDictation(input, micBtn, { onMessage: function (m) { log.messages.push(m); } });
setTimeout(function () {
  clicks[0]();
  setTimeout(function () {
    console.log(JSON.stringify({ mode: h.mode(), active: !!micBtn.classes.active, focused: log.focused,
      messages: log.messages }));
  }, 10);
}, 10);
""", capacitor=cap)
    assert out["mode"] == "keyboard"
    assert out["active"] is False
    assert out["focused"] == 1
    assert out["messages"] == ["Tap the mic on your keyboard and start talking."]


@_needs_node
def test_a_blocked_microphone_says_where_to_turn_it_on():
    cap = NATIVE_PLUGIN % {"available": "true", "start": "Promise.reject({ code: 'denied' })"}
    out = _run(IPHONE_APP_UA, """
var h = window.setupDictation(input, micBtn, { onMessage: function (m) { log.messages.push(m); } });
setTimeout(function () {
  clicks[0]();
  setTimeout(function () {
    console.log(JSON.stringify({ mode: h.mode(), messages: log.messages }));
  }, 10);
}, 10);
""", capacitor=cap)
    assert out["mode"] == "native"
    assert out["messages"] == ["Pomona can't use the microphone. Turn it on in your phone's Settings, under Pomona."]


def test_the_chat_composer_uses_the_shared_helper():
    # One engine for every mic: the chat's own copy of the old Web Speech
    # code is gone, and shell.html loads dictation.js before shell.js.
    assert "function setupDictation(" not in SHELL_JS
    assert "SpeechRecognitionCtor" not in SHELL_JS
    assert "setupAskDictation(askInput, document.getElementById('ask-mic-btn'))" in SHELL_JS
    html = (ROOT / "static" / "shell.html").read_text()
    assert html.index('src="/static/dictation.js"') < html.index('src="/static/shell.js"')


# --- the reviewer's two defects (2026-11-09) -------------------------------

@_needs_node
def test_cancel_throws_away_words_still_arriving_on_desktop():
    # The chat cancels on Send. Continuous listening means more results can
    # arrive after that; none may put the sent words back in the box.
    out = _run(DESKTOP_UA, """
var h = window.setupDictation(input, micBtn, {});
clicks[0]();
var rec = log.recognitions[0];
var aborted = 0;
rec.abort = function () { aborted += 1; if (rec.onerror) rec.onerror({ error: 'aborted' }); };
rec.onresult({ results: [ { isFinal: true, 0: { transcript: 'add oat milk' } } ] });
h.cancel();
input.value = '';   // the chat clears the box once the message is sent
rec.onresult({ results: [ { isFinal: true, 0: { transcript: 'add oat milk please' } } ] });
console.log(JSON.stringify({ value: input.value, aborted: aborted, active: !!micBtn.classes.active,
  messages: log.messages }));
""")
    assert out == {"value": "", "aborted": 1, "active": False, "messages": []}


@_needs_node
def test_words_from_another_listen_never_land_in_this_box():
    cap = NATIVE_PLUGIN % {"available": "true", "start": "Promise.resolve({ session: 2 })"}
    out = _run(IPHONE_APP_UA, """
var h = window.setupDictation(input, micBtn, {});
setTimeout(function () {
  clicks[0]();
  setTimeout(function () {
    listeners.partial({ session: 1, text: 'from the old sheet' });
    var before = input.value;
    listeners.partial({ session: 2, text: 'lemons' });
    console.log(JSON.stringify({ before: before, after: input.value }));
  }, 10);
}, 10);
""", capacitor=cap)
    assert out == {"before": "", "after": "lemons"}


@_needs_node
def test_cancel_while_starting_stops_the_phone_mic_once_it_starts():
    cap = NATIVE_PLUGIN % {"available": "true", "start": "Promise.resolve({ session: 3 })"}
    out = _run(IPHONE_APP_UA, """
var h = window.setupDictation(input, micBtn, {});
setTimeout(function () {
  clicks[0]();
  h.dispose();          // the sheet closed before start() came back
  setTimeout(function () {
    listeners.partial({ session: 3, text: 'nope' });
    console.log(JSON.stringify({ calls: nativeCalls, value: input.value, active: !!micBtn.classes.active }));
  }, 10);
}, 10);
""", capacitor=cap)
    assert out["calls"] == ["isAvailable", "start", "stop"]
    assert out["value"] == ""
    assert out["active"] is False


def test_send_cancels_the_chat_mic_and_the_say_it_sheet_lets_go():
    a = SHELL_JS.index("  async function sendAskMessage(")
    head = SHELL_JS[a:a + 400]
    assert "if (askDictation) askDictation.cancel();" in head
    assert "var askDictation = setupAskDictation(askInput" in SHELL_JS
    ka = (ROOT / "static" / "kitchen-add.js").read_text()
    render = ka[ka.index("  function render() {"):ka.index("  var sayDictation = null;")]
    assert render.index("dropDictation();") < render.index("sheetEl.innerHTML = html;")
    close = ka[ka.index("  function close() {"):ka.index("  function close() {") + 200]
    assert "dropDictation();" in close
    assert "sayDictation = global.setupDictation(box, mic" in ka


@_needs_node
def test_desktop_safari_stays_one_phrase_at_a_time():
    out = _run("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Safari/605.1.15", """
window.setupDictation(input, micBtn, {});
clicks[0]();
console.log(JSON.stringify({ continuous: log.recognitions[0].continuous }));
""")
    assert out == {"continuous": False}


# --- second review (2026-11-09): a late start() answer is per tap ----------

STAGED_PLUGIN = """
var listeners = {};
var nativeCalls = [];
var starts = [];
window.Capacitor = {
  isNativePlatform: function () { return true; },
  isPluginAvailable: function (n) { return n === 'PomonaSpeech'; },
  Plugins: { PomonaSpeech: {
    isAvailable: function () { return Promise.resolve({ available: true }); },
    start: function () { return new Promise(function (res) { starts.push(res); }); },
    stop: function (o) { nativeCalls.push('stop:' + (o && o.session)); return Promise.resolve({ session: o && o.session, text: '' }); },
    addListener: function (name, fn) { listeners[name] = fn; return Promise.resolve({ remove: function () {} }); }
  } }
};
"""


@_needs_node
def test_send_then_tap_again_keeps_the_new_listen():
    # Tap, Send while the model downloads, tap again. The first start's late
    # answer must stop only its own listen, and the box keeps the second.
    out = _run(IPHONE_APP_UA, """
var h = window.setupDictation(input, micBtn, {});
setTimeout(function () {
  clicks[0]();           // start #1, still on its way
  h.cancel();            // Send
  clicks[0]();           // start #2
  starts[0]({ session: 1 });
  starts[1]({ session: 2 });
  setTimeout(function () {
    listeners.partial({ session: 2, text: 'second' });
    console.log(JSON.stringify({ calls: nativeCalls, value: input.value, active: !!micBtn.classes.active }));
  }, 10);
}, 10);
""", capacitor=STAGED_PLUGIN)
    assert out == {"calls": ["stop:1"], "value": "second", "active": True}


@_needs_node
def test_a_thrown_away_box_never_stops_the_new_boxs_listen():
    out = _run(IPHONE_APP_UA, """
var oldBox = window.setupDictation(input, micBtn, {});
var input2 = { value: '', placeholder: '', focus: function () {} };
var btn2 = { dataset: {}, classes: {}, classList: { add: function (c) { btn2.classes[c] = true; }, remove: function (c) { delete btn2.classes[c]; } },
  addEventListener: function (ev, fn) { clicks.push(fn); } };
var newBox = window.setupDictation(input2, btn2, {});
setTimeout(function () {
  clicks[0]();           // old box starts
  oldBox.dispose();      // the sheet redraws
  clicks[1]();           // the new box starts
  starts[0]({ session: 1 });
  starts[1]({ session: 2 });
  setTimeout(function () {
    console.log(JSON.stringify({ calls: nativeCalls, newActive: !!btn2.classes.active }));
  }, 10);
}, 10);
""", capacitor=STAGED_PLUGIN)
    assert out == {"calls": ["stop:1"], "newActive": True}
