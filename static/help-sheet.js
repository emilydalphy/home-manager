/*
 * "Need a hand?" — the sheet behind the round `?` (Loop Board "Help icon:
 * 'Need a hand?' sheet and 'Something not working?'", Emily 2026-09-18;
 * mockups 09b-help and 09c-not-working).
 *
 * One sheet, two pages, loaded by both the standalone Week 1 screen
 * (static/onboarding.html) and the app shell (static/shell.html), so the
 * `?` on Week 1 and the one on Plan › Check the week open the very same
 * thing. Nothing here depends on shell.js: the sheet builds its own
 * markup, injects its own stylesheet once (every colour a theme.css
 * token, so it follows dark mode), and talks to the one route it needs.
 *
 *   openHelpSheet({ screenName }) — four rows that say how to change the
 *     week (swap, tell me, nothing's locked in, correct me), the quiet
 *     "Something not working? Tell me" line and Got it.
 *   closeHelpSheet()             — Escape, the scrim and Got it all call it.
 *
 * "Something not working?" is the same form the shell's own sheet shows
 * (shell.js snwFormHtml: two fields, Send disabled until there is text,
 * POST /api/feedback) with one thing added on both: the screen's own name,
 * said on the form ("I'll include which screen you were on: Week 1.") and
 * sent as `screen`. What travels with the note is shape and nothing else —
 * the path, the screen name; no error text, no URL with a name in it.
 *
 * It replaces the one-time "Tap for the usual. Type for the rest." sheet
 * that used to open the first time the shell loaded after setup: a lesson
 * at the door was one more thing between setup and the week (DESIGN_SYSTEM
 * §2b S2). This says the same things where they are needed, on request.
 */
(function (global) {
  'use strict';

  var HELP_TITLE = 'Need a hand?';
  var SNW_TITLE = 'Something not working?';
  var SNW_LINE = 'Tell me what happened and I’ll pass it straight to Emily.';
  var SNW_PLACEHOLDER = 'Even half a sentence helps';
  var SNW_SENT = 'Sent — thanks, Emily reads every one.';
  var SNW_CLOSE_AFTER_MS = 1600;

  // The Swap row's icon is the Swap button's own (onboarding.html's
  // reveal cards); the others are drawn on the same 24-grid at the one
  // stroke (DESIGN_SYSTEM §2 rule 7).
  var ICON_SWAP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 7h11l-3-3M17 17H6l3 3"/></svg>';
  var ICON_TALK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 6a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H9l-5 4z"/></svg>';
  var ICON_CALENDAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>';
  var ICON_PENCIL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4l11-11-4-4L4 16z"/></svg>';
  var ICON_TICK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7"/></svg>';

  // The four rows, verbatim from the approved sheet (09b-help).
  var HELP_ROWS = [
    { icon: ICON_SWAP, title: 'Swap a meal',
      line: 'Tap Swap under any meal and I’ll show you three other picks.' },
    { icon: ICON_TALK, title: 'Or just tell me',
      line: '“Jamie’s out Thursday.” “Less chicken.” “We already have rice.” I’ll change that one thing, not the whole week.' },
    { icon: ICON_CALENDAR, title: 'Nothing’s locked in',
      line: 'Approve now, change any day later from the Plan tab.' },
    { icon: ICON_PENCIL, title: 'If I get it wrong, say so',
      line: 'Correct me right where it happens and I’ll remember for next week.' }
  ];

  // Every colour is a theme.css token; every size is the mockup's. The
  // sheet slides up over a scrim that fades in (DESIGN_SYSTEM §4, motion 1)
  // on the motion tokens, which prefers-reduced-motion collapses to 0ms.
  var STYLE =
    '#help-scrim{position:fixed;inset:0;z-index:60;background:var(--scrim);' +
      'animation:help-fade var(--motion-fast) var(--motion-ease)}' +
    '#help-sheet{position:fixed;left:0;right:0;bottom:0;z-index:61;max-height:88%;overflow-y:auto;' +
      'box-sizing:border-box;background:var(--surface);color:var(--ink);' +
      'border-radius:var(--radius-hero) var(--radius-hero) 0 0;box-shadow:var(--shadow-sheet);' +
      'padding:14px 20px calc(28px + env(safe-area-inset-bottom,0px));display:flex;flex-direction:column;gap:10px;' +
      'font-family:var(--font-body);animation:help-up var(--motion-base) var(--motion-ease)}' +
    '#help-sheet *{box-sizing:border-box}' +
    '#help-scrim[hidden],#help-sheet[hidden]{display:none}' +
    '@keyframes help-fade{from{opacity:0}to{opacity:1}}' +
    '@keyframes help-up{from{transform:translateY(24px);opacity:0}to{transform:none;opacity:1}}' +
    '@media (min-width:768px){#help-sheet{left:50%;right:auto;transform:translateX(-50%);width:520px;max-width:100%}}' +
    '.help-handle{width:36px;height:4px;border-radius:var(--radius-pill);background:var(--hairline-deep);margin:0 auto 6px}' +
    '.help-title{font-family:var(--font-display);font-weight:700;font-size:24px;letter-spacing:-0.03em;line-height:1.1;color:var(--ink);margin:0}' +
    '.help-row{display:flex;gap:14px;align-items:flex-start;padding:10px 0}' +
    '.help-row-icon{width:40px;height:40px;flex:0 0 40px;border-radius:var(--radius-badge);background:var(--celadon-tint);' +
      'color:var(--celadon-label);display:flex;align-items:center;justify-content:center}' +
    '.help-row-icon svg{width:20px;height:20px}' +
    '.help-row-title{font-size:16px;font-weight:600;color:var(--ink);line-height:1.3;margin:0}' +
    '.help-row-line{font-size:14px;color:var(--ink-secondary);line-height:1.4;margin:2px 0 0}' +
    '.help-quiet{display:flex;align-items:center;justify-content:center;width:100%;min-height:44px;background:none;border:0;' +
      'padding:0;font:italic 400 17px var(--font-accent);color:var(--ink-secondary);cursor:pointer}' +
    '.help-quiet:hover{color:var(--ink)}' +
    '.help-outline{display:flex;align-items:center;justify-content:center;width:100%;height:54px;border-radius:var(--radius-action);' +
      'background:transparent;border:1.5px solid var(--hairline-strong);font-family:var(--font-display);font-weight:700;' +
      'font-size:17px;letter-spacing:-0.02em;color:var(--ink-strong);cursor:pointer}' +
    '.help-primary{display:flex;align-items:center;justify-content:center;width:100%;height:54px;border:0;border-radius:var(--radius-action);' +
      'background:var(--apricot);color:var(--on-accent-ink);font-family:var(--font-display);font-weight:700;font-size:17px;' +
      'letter-spacing:-0.02em;cursor:pointer;box-shadow:var(--shadow-action)}' +
    '.help-primary:disabled{opacity:.45;cursor:default;box-shadow:none}' +
    '.help-sub{font-size:15px;line-height:1.4;color:var(--ink-secondary);margin:0}' +
    '.help-label{font-size:15px;font-weight:600;color:var(--ink);line-height:1.3;margin:4px 0 0;display:block}' +
    '.help-optional{font-size:13px;font-weight:500;color:var(--ink-secondary)}' +
    '.help-input{width:100%;border:1.5px solid var(--hairline-strong);border-radius:var(--radius-control);padding:12px 14px;' +
      'font:500 16px var(--font-body);color:var(--ink);background:var(--surface);resize:none;line-height:1.4}' +
    '.help-input:focus{outline:none;border-color:var(--ink-strong)}' +
    '.help-input::placeholder{color:var(--ink-placeholder)}' +
    '.help-note{display:flex;gap:10px;align-items:center;padding:8px 12px;background:var(--celadon-tint);' +
      'border:1.5px solid var(--celadon-edge);border-radius:var(--radius-control);color:var(--celadon-label)}' +
    '.help-note svg{width:18px;height:18px;flex:0 0 auto}' +
    '.help-note p{margin:0;font-size:13px;font-weight:600;line-height:1.4;color:var(--celadon-label)}' +
    '.help-sent{font-size:15px;line-height:1.45;color:var(--ink);margin:10px 0 0;text-align:center}';

  var sheetEl = null;
  var scrimEl = null;
  var closeTimer = null;
  var current = { screenName: '' };

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function ensureBuilt() {
    if (sheetEl) return;
    var doc = global.document;
    if (!doc.getElementById('help-sheet-style')) {
      var style = doc.createElement('style');
      style.id = 'help-sheet-style';
      style.textContent = STYLE;
      doc.head.appendChild(style);
    }
    scrimEl = doc.createElement('div');
    scrimEl.id = 'help-scrim';
    scrimEl.hidden = true;
    sheetEl = doc.createElement('div');
    sheetEl.id = 'help-sheet';
    sheetEl.hidden = true;
    sheetEl.setAttribute('role', 'dialog');
    sheetEl.setAttribute('aria-modal', 'true');
    sheetEl.setAttribute('aria-labelledby', 'help-title');
    doc.body.appendChild(scrimEl);
    doc.body.appendChild(sheetEl);
    scrimEl.addEventListener('click', closeHelpSheet);
    doc.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && sheetEl && !sheetEl.hidden) closeHelpSheet();
    });
    sheetEl.addEventListener('click', function (e) {
      var target = e.target && e.target.closest && e.target.closest('[data-help]');
      if (!target) return;
      var what = target.getAttribute('data-help');
      if (what === 'close') closeHelpSheet();
      else if (what === 'snw') showSnwForm();
      else if (what === 'send') sendSnw();
    });
  }

  function helpHtml() {
    return '<div class="help-handle" aria-hidden="true"></div>' +
      '<h2 class="help-title" id="help-title">' + esc(HELP_TITLE) + '</h2>' +
      HELP_ROWS.map(function (row) {
        return '<div class="help-row">' +
          '<div class="help-row-icon">' + row.icon + '</div>' +
          '<div><p class="help-row-title">' + esc(row.title) + '</p>' +
          '<p class="help-row-line">' + esc(row.line) + '</p></div>' +
        '</div>';
      }).join('') +
      '<button type="button" class="help-quiet" data-help="snw">Something not working? Tell me</button>' +
      '<button type="button" class="help-outline" data-help="close">Got it</button>';
  }

  // The same two fields as shell.js's snwFormHtml, plus the screen note.
  function snwFormHtml(screenName) {
    return '<div class="help-handle" aria-hidden="true"></div>' +
      '<h2 class="help-title" id="help-title">' + esc(SNW_TITLE) + '</h2>' +
      '<p class="help-sub">' + esc(SNW_LINE) + '</p>' +
      '<label class="help-label" for="help-snw-what">What happened?</label>' +
      '<textarea id="help-snw-what" class="help-input" rows="4" placeholder="' + esc(SNW_PLACEHOLDER) + '"></textarea>' +
      '<label class="help-label" for="help-snw-trying">What were you trying to do? <span class="help-optional">Optional</span></label>' +
      '<textarea id="help-snw-trying" class="help-input" rows="2"></textarea>' +
      (screenName
        ? '<div class="help-note">' + ICON_TICK + '<p>I’ll include which screen you were on: ' + esc(screenName) + '.</p></div>'
        : '') +
      '<button type="button" class="help-primary" id="help-snw-send" data-help="send" disabled>Send</button>' +
      '<button type="button" class="help-quiet" data-help="close">Never mind</button>';
  }

  function showSnwForm() {
    sheetEl.innerHTML = snwFormHtml(current.screenName);
    var what = sheetEl.querySelector('#help-snw-what');
    var send = sheetEl.querySelector('#help-snw-send');
    what.addEventListener('input', function () { send.disabled = !what.value.trim(); });
    what.focus();
  }

  function sendSnw() {
    var what = sheetEl.querySelector('#help-snw-what');
    var trying = sheetEl.querySelector('#help-snw-trying');
    var text = what ? String(what.value || '').trim() : '';
    if (!text) return;
    // Confirmed before the request resolves, and either way — the same
    // reason shell.js gives: "that didn't send either" would turn one bad
    // moment into two, and /api/feedback answers 204 whatever happens.
    sheetEl.innerHTML = '<div class="help-handle" aria-hidden="true"></div>' +
      '<h2 class="help-title" id="help-title">' + esc(SNW_TITLE) + '</h2>' +
      '<p class="help-sent">' + esc(SNW_SENT) + '</p>';
    try {
      global.fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        keepalive: true,
        body: JSON.stringify({
          what_happened: text,
          trying_to_do: trying ? String(trying.value || '').trim() : '',
          where: global.location.pathname,
          screen: current.screenName || '',
          error_shapes: []
        })
      }).catch(function () { /* see above */ });
    } catch (err) { /* see above */ }
    if (closeTimer) clearTimeout(closeTimer);
    closeTimer = setTimeout(closeHelpSheet, SNW_CLOSE_AFTER_MS);
  }

  function openHelpSheet(opts) {
    ensureBuilt();
    current.screenName = (opts && opts.screenName) ? String(opts.screenName) : '';
    if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
    sheetEl.innerHTML = helpHtml();
    scrimEl.hidden = false;
    sheetEl.hidden = false;
    var first = sheetEl.querySelector('[data-help="close"]');
    if (first && first.focus) first.focus();
  }

  function closeHelpSheet() {
    if (!sheetEl) return;
    if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
    scrimEl.hidden = true;
    sheetEl.hidden = true;
  }

  var api = {
    HELP_TITLE: HELP_TITLE,
    HELP_ROWS: HELP_ROWS,
    SNW_TITLE: SNW_TITLE,
    SNW_PLACEHOLDER: SNW_PLACEHOLDER,
    SNW_SENT: SNW_SENT,
    helpHtml: helpHtml,
    snwFormHtml: snwFormHtml,
    openHelpSheet: openHelpSheet,
    closeHelpSheet: closeHelpSheet
  };
  global.openHelpSheet = openHelpSheet;
  global.closeHelpSheet = closeHelpSheet;
  global.PomonaHelp = api;
  // So tests can require this file under node and run the builders.
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
