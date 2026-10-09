"""
Reloading mid-setup keeps every answer and the step you were on.

Found 2026-10-09 (Loop Board, Bug, Medium): sign up by email, answer up to
"Does anyone else help run the house?", reload -- and you are back on
"What's your name?" with the name empty. On a phone a reload is not a
choice: the browser throws a backgrounded tab away and reloads it when you
come back, so a household that went to check a calendar mid-setup lost
everything it had said.

The answers live only in this page's memory until setup's one write at the
end (2026-09-09 -- nothing posts on the way past, because add_member is
get-or-create by name). So the draft is kept in the browser: localStorage,
keyed by the household id /api/whoami gives, written as answers change and
removed the moment setup is saved.

HOW THIS RUNS. Not a source read, and not a few lifted functions: the
page's whole <script> runs under node, in a fresh vm context per "load",
over a DOM parsed from the page's own markup. A reload is exactly that --
a new context, the same localStorage, the same history entry -- so the
test is asking the question the bug report asked. The DOM is small (tags,
ids, classes, data attributes, values, bubbling clicks and inputs) and is
the only stand-in; every function that runs is the page's own.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness


ONBOARDING = (Path(__file__).resolve().parent.parent / "static" / "onboarding.html").read_text()

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page's own script")


def _markup() -> str:
    body = ONBOARDING[ONBOARDING.index("<body"):]
    body = body[body.index(">") + 1:]
    body = re.sub(r"<script[\s\S]*?</script>", "", body)
    body = re.sub(r"<style[\s\S]*?</style>", "", body)
    body = re.sub(r"<template[\s\S]*?</template>", "", body)
    return body.replace("</body>", "").replace("</html>", "")


def _script() -> str:
    start = ONBOARDING.rindex("<script>") + len("<script>")
    return ONBOARDING[start:ONBOARDING.rindex("</script>")]


_HARNESS = r"""
const vm = require('vm');
const MARKUP = __MARKUP__;
const SCRIPT = __SCRIPT__;

// ---------- a DOM parsed from the page's own markup ----------
const VOID = new Set(['input', 'br', 'img', 'meta', 'link', 'hr', 'source', 'path', 'circle', 'rect',
  'line', 'polyline', 'polygon', 'use', 'ellipse', 'area', 'base', 'col', 'embed', 'param', 'track', 'wbr', 'stop']);
function camel(s) { return s.replace(/-(\w)/g, (_, c) => c.toUpperCase()); }
function decode(s) {
  return String(s).replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>').replace(/&rsquo;/g, '’').replace(/&amp;/g, '&');
}
function El(tag) {
  this.tagName = String(tag || 'div').toUpperCase();
  this.children = []; this.parent = null; this.attrs = {}; this.dataset = {}; this.style = {};
  this.hidden = false; this.disabled = false; this.value = ''; this._text = ''; this.id = '';
  this.listeners = {}; this.onclick = null; this._classes = new Set();
  const self = this;
  this.classList = {
    add: (...c) => c.forEach(x => self._classes.add(x)),
    remove: (...c) => c.forEach(x => self._classes.delete(x)),
    toggle: (c, f) => { const on = f === undefined ? !self._classes.has(c) : !!f; if (on) self._classes.add(c); else self._classes.delete(c); return on; },
    contains: c => self._classes.has(c),
  };
}
El.prototype = {
  get className() { return [...this._classes].join(' '); },
  set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); },
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); },
  set textContent(v) { this.children.forEach(c => { c.parent = null; }); this.children = []; this._text = String(v); },
  get innerHTML() { return this._html || ''; },
  set innerHTML(v) { this.children.forEach(c => { c.parent = null; }); this.children = []; this._text = ''; this._html = String(v); parseInto(this, String(v)); },
  get firstChild() { return this.children[0] || null; },
  get lastChild() { return this.children[this.children.length - 1] || null; },
  get offsetWidth() { return 0; },
  setAttribute(k, v) { setAttr(this, k, String(v)); },
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
  removeAttribute(k) { delete this.attrs[k]; },
  appendChild(c) { if (c.parent) c.remove(); c.parent = this; this.children.push(c); return c; },
  insertBefore(c, ref) {
    if (c.parent) c.remove();
    c.parent = this;
    const at = ref ? this.children.indexOf(ref) : -1;
    if (at === -1) this.children.push(c); else this.children.splice(at, 0, c);
    return c;
  },
  remove() { if (!this.parent) return; this.parent.children = this.parent.children.filter(c => c !== this); this.parent = null; },
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
  removeEventListener() {},
  click() { dispatch(this, 'click'); },
  focus() {}, blur() {}, select() {}, scrollIntoView() {}, scrollTo() {},
  getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; },
  closest(sel) { let n = this; while (n) { if (matchesSel(n, sel)) return n; n = n.parent; } return null; },
  matches(sel) { return matchesSel(this, sel); },
  querySelectorAll(sel) { return descendants(this).filter(n => matchesSel(n, sel)); },
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
};
function setAttr(el, k, v) {
  el.attrs[k] = v;
  if (k === 'class') el.className = v;
  else if (k === 'id') el.id = v;
  else if (k.indexOf('data-') === 0) el.dataset[camel(k.slice(5))] = decode(v);
  else if (k === 'value') el.value = decode(v);
  else if (k === 'hidden') el.hidden = true;
  else if (k === 'disabled') el.disabled = true;
  else if (k === 'style') {
    v.split(';').forEach(p => { const i = p.indexOf(':'); if (i > 0) el.style[camel(p.slice(0, i).trim())] = p.slice(i + 1).trim(); });
  }
}
function parseInto(root, html) {
  const re = /<!--[\s\S]*?-->|<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:\s+[^\s=>\/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|([^<]+)/g;
  const stack = [root];
  let m;
  while ((m = re.exec(html)) !== null) {
    const top = stack[stack.length - 1];
    if (m[1]) {
      const tag = m[1].toUpperCase();
      for (let i = stack.length - 1; i > 0; i--) { if (stack[i].tagName === tag) { stack.length = i; break; } }
    } else if (m[2]) {
      const el = new El(m[2]);
      const are = /([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
      let a;
      while ((a = are.exec(m[3] || '')) !== null) setAttr(el, a[1], a[2] !== undefined ? a[2] : a[3] !== undefined ? a[3] : a[4] !== undefined ? a[4] : '');
      if (el.tagName === 'TEXTAREA') {
        const close = html.indexOf('</textarea>', re.lastIndex);
        el.value = decode(html.slice(re.lastIndex, close));
        re.lastIndex = close + '</textarea>'.length;
        el.parent = top; top.children.push(el);
        continue;
      }
      el.parent = top; top.children.push(el);
      if (!m[4] && !VOID.has(m[2].toLowerCase())) stack.push(el);
    } else if (m[5]) {
      top._text += decode(m[5]);
    }
  }
}
function descendants(el) { const out = []; (function walk(n) { n.children.forEach(c => { out.push(c); walk(c); }); })(el); return out; }
function compound(n, part) {
  const toks = part.match(/\[[^\]]+\]|[.#]?[\w-]+/g) || [];
  return toks.every(t => {
    if (t[0] === '.') return n._classes.has(t.slice(1));
    if (t[0] === '#') return n.id === t.slice(1);
    if (t[0] === '[') {
      const a = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(t);
      if (!a) return false;
      const k = a[1];
      const has = k.indexOf('data-') === 0 ? n.dataset[camel(k.slice(5))] : (k === 'id' ? (n.id || undefined) : n.attrs[k]);
      return a[2] === undefined ? has !== undefined : String(has) === a[2];
    }
    return n.tagName === t.toUpperCase();
  });
}
function matchesSel(n, sel) {
  if (!n || !n.tagName) return false;
  return sel.split(',').some(s => {
    const parts = s.trim().split(/\s+(?![^\[]*\])/);
    if (!compound(n, parts[parts.length - 1])) return false;
    let anc = n.parent;
    for (let i = parts.length - 2; i >= 0; i--) {
      while (anc && !compound(anc, parts[i])) anc = anc.parent;
      if (!anc) return false;
      anc = anc.parent;
    }
    return true;
  });
}
function dispatch(el, type, extra) {
  if (type === 'click' && el.disabled) return;
  const ev = Object.assign({ type: type, target: el, defaultPrevented: false, _stop: false }, extra || {});
  ev.preventDefault = () => { ev.defaultPrevented = true; };
  ev.stopPropagation = () => { ev._stop = true; };
  // The path is fixed when the event starts, as in a browser: a handler
  // that redraws its own list detaches the target, and the event still
  // reaches the document.
  const path = [];
  for (let n = el; n; n = n.parent) path.push(n);
  for (const n of path) {
    ev.currentTarget = n;
    if (type === 'click' && typeof n.onclick === 'function') n.onclick(ev);
    (n.listeners[type] || []).slice().forEach(fn => fn(ev));
    if (ev._stop) break;
  }
}

// ---------- what survives a reload: storage and the history entry ----------
const STORE = new Map();
let STORAGE_BROKEN = false;
const storage = {
  getItem(k) { if (STORAGE_BROKEN) throw new Error('SecurityError'); return STORE.has(k) ? STORE.get(k) : null; },
  setItem(k, v) { if (STORAGE_BROKEN) throw new Error('QuotaExceededError'); STORE.set(k, String(v)); },
  removeItem(k) { if (STORAGE_BROKEN) throw new Error('SecurityError'); STORE.delete(k); },
  key(i) { if (STORAGE_BROKEN) throw new Error('SecurityError'); return [...STORE.keys()][i] || null; },
  get length() { if (STORAGE_BROKEN) throw new Error('SecurityError'); return STORE.size; },
};
const HISTORY = [];
let CURSOR = -1;
let POPS = [];

let CALLS = [];
let ERRORS = [];
let SET_UP = false;   // whoami lists adults: setup was saved somewhere
function load(householdId) {
  const docNode = new El('#document');
  const body = new El('body');
  body.parent = docNode; docNode.children.push(body);
  parseInto(body, MARKUP);
  POPS = [];
  const S = {};
  const document = {
    body: body, documentElement: new El('html'), visibilityState: 'visible', activeElement: null,
    getElementById: id => descendants(body).find(n => n.id === id) || null,
    createElement: tag => new El(tag),
    querySelector: sel => body.querySelector(sel),
    querySelectorAll: sel => body.querySelectorAll(sel),
    addEventListener: (t, fn) => docNode.addEventListener(t, fn),
    removeEventListener() {},
  };
  Object.assign(S, {
    document: document, console: { log() {}, info() {}, warn() {}, error: (...a) => ERRORS.push(a.map(String).join(' ')) },
    localStorage: storage, sessionStorage: storage,
    setTimeout, clearTimeout, setInterval, clearInterval, Promise, JSON, Math, Date, URL, URLSearchParams,
    alert: msg => ERRORS.push('alert: ' + msg),
    location: { pathname: '/onboarding', search: '', hash: '', href: 'http://x/onboarding' },
    navigator: { userAgent: 'node' },
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    requestAnimationFrame: fn => setTimeout(fn, 0),
    scrollTo() {},
    addEventListener: (t, fn) => { if (t === 'popstate') POPS.push(fn); },
    removeEventListener() {},
    history: {
      get length() { return HISTORY.length; },
      get state() { return CURSOR >= 0 ? HISTORY[CURSOR] : null; },
      pushState(s) { HISTORY.length = CURSOR + 1; HISTORY.push(s); CURSOR = HISTORY.length - 1; },
      replaceState(s) { if (CURSOR < 0) { HISTORY.push(s); CURSOR = 0; } else HISTORY[CURSOR] = s; },
      back() { if (CURSOR <= 0) return; CURSOR -= 1; const st = HISTORY[CURSOR]; POPS.forEach(fn => fn({ state: st })); },
    },
    Api: {
      fetch: async (url, init) => {
        CALLS.push({ url: url, method: (init && init.method) || 'GET' });
        let out = {};
        if (url === '/api/whoami') out = { household_id: householdId, adults: SET_UP ? [{ id: 1, name: 'Priya' }] : [] };
        else if (url === '/api/ai-consent') out = { status: '' };
        else if (url === '/api/onboarding/answers') out = { usual_week: null };
        return { ok: true, status: 200, json: async () => out, text: async () => JSON.stringify(out) };
      },
    },
  });
  S.window = S; S.self = S;
  const ctx = vm.createContext(S);
  vm.runInContext(SCRIPT, ctx);
  return ctx;
}
function ev(ctx, code) { return vm.runInContext(code, ctx); }
function $(ctx, sel) {
  const el = ev(ctx, 'document').querySelector(sel);
  if (!el) throw new Error('no element for ' + sel);
  return el;
}
function tap(ctx, sel) { $(ctx, sel).click(); }
function type(ctx, sel, text) { const el = $(ctx, sel); el.value = text; dispatch(el, 'input'); }
function settle() { return new Promise(r => setTimeout(r, 30)); }
function draftKeys() { return [...STORE.keys()].filter(k => k.indexOf('onboarding') !== -1); }
function answers(ctx) {
  return JSON.parse(ev(ctx, `JSON.stringify({
    step: currentStep,
    name: document.getElementById('your-name-input').value,
    members: currentMembers(),
    helperPicks: helperPicks,
    helperContacts: helperContacts,
    restrictions: currentRestrictions(),
    usualGrid: usualGrid,
    prepAnswer: prepAnswer, prepDays: prepDayKeys, shopDay: shopDay, shopTopUpDay: shopTopUpDay,
    variety: varietyChoice, dinnerWindow: rhythmDinnerWindow, weeknightMax: weeknightMaxMinutes,
    lunchMax: weekdayLunchMaxMinutes, lunchNeeds: lunchNeeds, lunchHome: lunchHome, lunchOutDays: lunchOutDays,
    snacks: memberSnacks, eatingStyle: currentEatingStyle(), wontEat: currentWontEat(),
    excited: currentExcitedAbout(), kit: kitchenKit, firstPlanStart: firstPlanStart,
    note: anythingElseNote,
  })`));
}

async function main() {
  const out = {};
  const scenario = __SCENARIO__;
  await scenario(out);
  out.errors = ERRORS;
  process.stdout.write(JSON.stringify(out));
}
main().catch(e => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
"""


def _run(scenario: str) -> dict:
    script = (
        _HARNESS.replace("__MARKUP__", json.dumps(_markup()))
        .replace("__SCRIPT__", json.dumps(_script()))
        .replace("__SCENARIO__", scenario)
    )
    res = nodeharness.run_node(script, timeout=60)
    assert res.returncode == 0, f"node failed: {res.stderr[-3000:]}"
    return json.loads(res.stdout)


# The bug report's own walk: email sign-up, the intro, your name, who else,
# and up to "Does anyone else help run the house?" -- then a reload.
_WALK_TO_HELPERS = r"""
  tap(L, '#intro-hello-next'); tap(L, '#intro-help-next'); tap(L, '#intro-talk-next'); tap(L, '#intro-know-next');
  type(L, '#your-name-input', 'Priya');
  tap(L, '#your-name-next');
  type(L, '#members .member-name', 'Sam');
  tap(L, '#add-member');
  const rows = ev(L, "document.querySelectorAll('#members .member-name')");
  rows[rows.length - 1].value = 'Arjun'; dispatch(rows[rows.length - 1], 'input');
  const blocks = ev(L, "document.querySelectorAll('#members .member-block')");
  blocks[blocks.length - 1].querySelector('[data-value="child"]').click();
"""


def test_a_reload_on_the_helpers_step_comes_back_to_it_with_every_answer():
    out = _run(r"""async function (out) {
  let L = load(7);
  await settle();
  """ + _WALK_TO_HELPERS + r"""
  tap(L, '#household-next');
  tap(L, '[data-helper="adult:Sam"]');
  type(L, '[data-helper-field="adult:Sam"]', 'sam@example.com');
  out.before = answers(L);
  L = load(7);            // the reload: same storage, same history entry
  await settle();
  out.after = answers(L);
  out.shown = ev(L, "ALL_STEPS.filter(k => document.getElementById('step-' + k).style.display === 'block')");
  out.youRow = ev(L, "document.querySelector('.member-you-name') ? document.querySelector('.member-you-name').textContent : ''");
}""")
    before, after = out["before"], out["after"]
    assert before["step"] == "helpers"
    assert after["step"] == "helpers", "a reload didn't come back to the step it was on"
    assert out["shown"] == ["helpers"], "the step came back but isn't the one on screen"
    assert after["name"] == "Priya", "a reload emptied the name"
    assert after["members"] == before["members"], "a reload lost who lives here (or their age group)"
    assert [m["name"] for m in after["members"]] == ["Priya", "Sam", "Arjun"]
    assert after["helperPicks"] == ["adult:Sam"] and after["helperContacts"] == {"adult:Sam": "sam@example.com"}
    assert out["errors"] == [], out["errors"]


def test_every_kind_of_answer_comes_back_after_a_reload():
    """
    Not only the first three screens: every answer the finish sends --
    a variable, an object keyed by name, the grid, a chip row the page
    only builds once, a typed box -- is in the draft. Set the lot, reload,
    compare.
    """
    out = _run(r"""async function (out) {
  let L = load(7);
  await settle();
  """ + _WALK_TO_HELPERS + r"""
  tap(L, '#household-next');
  ev(L, `
    restrictionAnswers['Arjun'] = { chips: ['Allergy'], allergens: ['Peanuts'], allergy: 'kiwi', other: '' };
    usualGrid.lunch = ['off', 'off', 'off', 'off', 'off', 'all', ['Priya', 'Arjun']];
    prepAnswer = 'yes'; prepDayKeys = ['sunday'];
    shopDay = 'saturday'; shopTopUpDay = 'wednesday';
    varietyChoice.dinner = 'cook_big_eat_twice';
    rhythmDinnerWindow = '6_8'; weeknightMaxMinutes = 30; weekdayLunchMaxMinutes = 45;
    lunchNeeds['Sam'] = { needs: ['thermos'], days: null };
    memberSnacks['Arjun'] = 3;
    kitchenKit.push('air_fryer'); firstPlanStart = 'next_week';
    anythingElseNote = 'We eat late on Fridays';
    showStep('eating-style');
  `);
  tap(L, '#eating-style-chips [data-value="High-protein"]');
  type(L, '#eating-style', 'no red meat on weeknights');
  tap(L, '#eating-style-next');
  tap(L, '#wont-eat-presets .chip');
  ev(L, "wontEatItems.push('olives'); renderWontEatChips();");
  tap(L, '#wont-eat-next');
  tap(L, '#excited-chips .chip');
  // A cuisine typed and entered: Enter adds the chip (keydown), and the
  // keyup is the last event the page sees before the reload.
  type(L, '#excited-custom', 'Ethiopian');
  dispatch($(L, '#excited-custom'), 'keydown', { key: 'Enter' });
  dispatch($(L, '#excited-custom'), 'keyup', { key: 'Enter' });
  out.before = answers(L);
  L = load(7);
  await settle();
  out.after = answers(L);
}""")
    before, after = out["before"], out["after"]
    assert before["step"] == "excited-about"
    assert before["restrictions"] == {"Arjun": ["allergy: peanuts", "allergy: kiwi"]}
    assert "Ethiopian" in before["excited"] and "olives" in before["wontEat"]
    for key in before:
        assert after[key] == before[key], f"{key} didn't survive the reload: {before[key]!r} -> {after[key]!r}"
    assert out["errors"] == [], out["errors"]


def test_a_draft_is_keyed_by_household_and_never_read_by_another():
    out = _run(r"""async function (out) {
  let L = load(7);
  await settle();
  """ + _WALK_TO_HELPERS + r"""
  tap(L, '#household-next');
  out.keys = draftKeys();
  L = load(8);            // the same phone, signed in to another household
  await settle();
  out.other = answers(L);
  out.keysAfter = draftKeys();
}""")
    assert out["keys"] == ["pomona-onboarding-draft:7"], out["keys"]
    assert out["other"]["name"] == "" and out["other"]["members"] == []
    assert out["other"]["step"] in ("intro-hello", "your-name")
    assert out["keysAfter"] == ["pomona-onboarding-draft:7"], "another household's draft was touched"


def test_finishing_setup_removes_the_draft_and_saves_once():
    out = _run(r"""async function (out) {
  let L = load(7);
  await settle();
  """ + _WALK_TO_HELPERS + r"""
  tap(L, '#household-next');
  out.keysBefore = draftKeys();
  CALLS = [];
  await ev(L, 'finishSetupAndReveal({ plan: false })');
  out.keysAfter = draftKeys();
  out.saves = CALLS.filter(c => c.url === '/api/onboarding/household').length;
  // Anything tapped after the finish must not write it back.
  tap(L, '#add-member');
  ev(L, "currentStep = 'household'");
  dispatch(ev(L, "document.getElementById('your-name-input')"), 'input');
  out.keysLater = draftKeys();
  L = load(7);
  await settle();
  out.after = answers(L);
}""")
    assert out["keysBefore"] == ["pomona-onboarding-draft:7"]
    assert out["saves"] == 1
    assert out["keysAfter"] == [], "the draft outlived the finish"
    assert out["keysLater"] == [], "a tap after the finish wrote the draft back"
    assert out["after"]["name"] == ""


def test_a_private_window_still_works_without_storage():
    out = _run(r"""async function (out) {
  STORAGE_BROKEN = true;
  let L = load(7);
  await settle();
  """ + _WALK_TO_HELPERS + r"""
  tap(L, '#household-next');
  out.step = ev(L, 'currentStep');
  L = load(7);
  await settle();
  out.after = answers(L);
}""")
    assert out["step"] == "helpers"
    # Nothing could be kept, so a reload behaves as it always did.
    assert out["after"]["step"] == "your-name"
    assert out["errors"] == [], out["errors"]


def test_after_a_resume_the_back_gesture_goes_one_step_not_to_the_start():
    """
    Before this, an entry stamped by an earlier load collapsed onto the
    first question, because a reload had emptied every answer it was
    written against. A resumed draft has them back, so the entry is
    honoured.
    """
    out = _run(r"""async function (out) {
  let L = load(7);
  await settle();
  """ + _WALK_TO_HELPERS + r"""
  tap(L, '#household-next');
  L = load(7);
  await settle();
  ev(L, 'window').history.back();
  out.after = answers(L);
}""")
    assert out["after"]["step"] == "household"
    assert out["after"]["name"] == "Priya"


def test_a_household_set_up_elsewhere_never_sees_its_draft():
    """
    Setup finished on another phone (or a save half-landed and the page
    came back out of the service worker's cache): whoami lists the
    household's adults, so the draft is stale -- removed, not resumed.
    """
    out = _run(r"""async function (out) {
  let L = load(7);
  await settle();
  """ + _WALK_TO_HELPERS + r"""
  tap(L, '#household-next');
  out.keysBefore = draftKeys();
  SET_UP = true;
  L = load(7);
  await settle();
  out.after = answers(L);
  out.keysAfter = draftKeys();
}""")
    assert out["keysBefore"] == ["pomona-onboarding-draft:7"]
    assert out["after"]["name"] == "" and out["after"]["helperPicks"] == []
    assert out["keysAfter"] == []
