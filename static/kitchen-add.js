/*
 * "Add to the kitchen" (Loop Board C2, 2026-10-27; mockups-c Kitchen.dc.html
 * screen 2, OptionC.dc.html screens 2 and 3), and the three-photo
 * "Show me your kitchen" it runs during first-run setup (C5).
 *
 * One sheet, loaded by the app shell (static/shell.html — the Kitchen tab's
 * "Snap a shelf" and "+ Add") and by first-run setup (static/onboarding.html),
 * so the photo step and the review are the very same thing in both. The
 * help-sheet.js pattern: nothing here depends on shell.js; the sheet builds
 * its own markup, injects its own stylesheet once (every colour a theme.css
 * token, so it follows dark mode), and talks to the server through
 * static/api.js (Api.json), which every page loading this loads first.
 *
 *   KitchenAdd.open({ mode, place, onSaved, onClose, onDone, doneLabel })
 *     mode 'add'   — the four ways in: Snap a shelf, Scan a receipt, Say it,
 *                    Type it (Kitchen.dc.html screen 2, in that order).
 *     mode 'snap'  — straight to the photo step (the dock's "Snap a shelf").
 *     mode 'setup' — Fridge, Freezer, Pantry, one photo each, each skippable
 *                    (OptionC screen 2). onDone(total) when all three are
 *                    answered or a receipt was added instead.
 *     place        — the shelf to start on (the one showing on the tab).
 *     onSaved(res) — after a save: {added, place, items, undo, line}.
 *   KitchenAdd.close()
 *
 * THE GUARDRAIL: NOTHING IS SAVED BEFORE THE REVIEW. A photo or a receipt
 * goes to the scan route, which only reads; a sentence goes to read-said,
 * which only reads; the review ("Here's what I saw") is the one place a
 * save starts, and it sends only the rows left ticked to /api/whats-home/add.
 * Items Pomona isn't sure of (the server's `sure: false`) start unticked.
 * Type it is the one exception — one thing the person typed is what they
 * meant — and it saves through the same route.
 */
(function (global) {
  'use strict';

  var PLACES = ['fridge', 'freezer', 'pantry'];
  var PLACE_LABELS = { fridge: 'Fridge', freezer: 'Freezer', pantry: 'Pantry' };

  // Verbatim from Kitchen.dc.html screen 2 and OptionC.dc.html 2 and 3.
  var COPY = {
    title: 'Add to the kitchen',
    sub: 'Shopped with Pomona? It’s already in. This is for everything else.',
    reviewTitle: 'Here’s what <em>I saw</em>',
    reviewSub: 'Untick anything that’s wrong.',
    notSure: 'not sure',
    setupEyebrow: 'Kitchen · getting started',
    setupTitle: 'Show me <em>your kitchen</em>',
    setupSub: 'Three photos. About two minutes.',
    setupNow: 'Open the door and take one photo',
    setupNext: 'Next',
    setupReceipt: 'Or scan your last receipt instead.',
    // Invented for C2/C5 (pomona-copywriter; listed in the hand-over note).
    snapTitle: 'Snap a shelf',
    reading: 'Reading the photo…',
    readingSaid: 'Reading that…',
    heardTitle: 'Here’s what <em>I heard</em>',
    emptyTitle: 'I couldn’t make anything out',
    emptySub: 'Try again a bit closer, with the light on.',
    emptySaid: 'Try saying it a different way.',
    tryAgain: 'Try again',
    errorTitle: 'Couldn’t read that photo',
    noSignalTitle: 'I need a signal for this one',
    noSignalSub: 'Try again when you’re back.',
    saveFailed: 'That didn’t save. Nothing was added — try again.',
    sayTitle: 'Say it',
    sayPlaceholder: 'Two litres of milk and a dozen eggs.',
    sayNext: 'Next',
    typeTitle: 'Type it',
    typePlaceholder: 'What is it?',
    skipped: 'Skipped',
    back: '‹ Add to the kitchen',
    retakePhoto: 'Try another photo',
    sayAgain: 'Say it again',
    leftOut: 'Left out — spices and regulars live with Staples: ',
    eyebrows: { fridge: 'Kitchen · fridge photo', freezer: 'Kitchen · freezer photo', pantry: 'Kitchen · pantry photo',
      receipt: 'Kitchen · receipt', say: 'Kitchen · what you said' },
  };

  var OPTIONS = [
    { key: 'snap', title: 'Snap a shelf', line: 'Fridge, freezer or pantry. One photo each.' },
    { key: 'receipt', title: 'Scan a receipt', line: 'For a shop you did without the list.' },
    { key: 'say', title: 'Say it', line: '“Two litres of milk and a dozen eggs.”' },
    { key: 'type', title: 'Type it', line: 'One thing at a time.' }
  ];

  var SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
  var ICONS = {
    snap: SVG + '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg>',
    receipt: SVG + '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6"/></svg>',
    say: SVG + '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>',
    type: SVG + '<path d="M5 12h14M12 5v14"/></svg>',
    tick: SVG + '<path d="M5 12.5l4.5 4.5L19 7"/></svg>',
    skip: SVG + '<path d="M7 12h10"/></svg>'
  };

  // ---- Pure helpers (tests/test_kitchen_add.py runs these under node) ----

  // The review's rows: ticked unless the server said it isn't sure. A row
  // with no `sure` at all is ticked (an older server, or Say it).
  function reviewRows(items) {
    return (items || []).map(function (it) {
      return {
        item: String((it && it.item) || ''),
        quantity: String((it && it.quantity) || ''),
        category: String((it && it.category) || ''),
        location: String((it && it.location) || ''),
        sure: !(it && it.sure === false),
        ticked: !(it && it.sure === false)
      };
    }).filter(function (r) { return r.item; });
  }

  function tickedCount(rows) {
    var n = 0;
    (rows || []).forEach(function (r) { if (r.ticked) n++; });
    return n;
  }

  // Where a review's things go, said the way the button says it: the shelf
  // the photo was of; a receipt or a sentence goes to "the kitchen" unless
  // every row shares one shelf.
  function reviewPlace(kind, rows) {
    if (PLACES.indexOf(kind) !== -1) return kind;
    var seen = {};
    var list = [];
    (rows || []).forEach(function (r) {
      if (r.ticked && !seen[r.location]) { seen[r.location] = true; list.push(r.location); }
    });
    return list.length === 1 && PLACES.indexOf(list[0]) !== -1 ? list[0] : 'kitchen';
  }

  // "Add 13 to the fridge" (OptionC screen 3). With nothing ticked the
  // number goes and the button waits.
  function addButtonLabel(n, place) {
    return n > 0 ? 'Add ' + n + ' to the ' + place : 'Add to the ' + place;
  }

  // The toast, in the house pattern: the thing, then what happened. A shelf
  // photo that confirms rows already tracked UPDATES them; those are
  // counted apart from the ones it added, so the number is what changed.
  function savedLine(added, place, items, updated) {
    var where = place || 'kitchen';
    added = added || 0;
    updated = updated || 0;
    function named() {
      var name = String(items[0]);
      return name.charAt(0).toUpperCase() + name.slice(1);
    }
    if (updated && !added) {
      if (updated === 1 && items && items.length === 1) return named() + ' in the ' + where + ' was updated.';
      return updated + ' things in the ' + where + ' were updated.';
    }
    if (updated && added) {
      return added + (added === 1 ? ' thing was' : ' things were') + ' added to the ' + where + ', and ' + updated + ' updated.';
    }
    if (added === 1 && items && items[0]) return named() + ' was added to the ' + where + '.';
    return added + ' things were added to the ' + where + '.';
  }

  // OptionC screen 2's status line under a finished shelf.
  function setupLine(result) {
    if (result === 'skipped') return COPY.skipped;
    if (typeof result === 'number') return result === 1 ? '1 thing added' : result + ' things added';
    return '';
  }

  // ---- The sheet ----

  var STYLE =
    '#ka-scrim{position:fixed;inset:0;z-index:70;background:var(--scrim);animation:ka-fade var(--motion-fast) var(--motion-ease)}' +
    '#ka-sheet{position:fixed;left:0;right:0;bottom:0;z-index:71;max-height:92%;overflow-y:auto;box-sizing:border-box;' +
      'background:var(--surface);color:var(--ink);border-radius:var(--radius-hero) var(--radius-hero) 0 0;box-shadow:var(--shadow-sheet);' +
      'padding:14px 18px calc(24px + env(safe-area-inset-bottom,0px));display:flex;flex-direction:column;gap:10px;' +
      'font-family:var(--font-body);animation:ka-up var(--motion-base) var(--motion-ease)}' +
    '#ka-sheet *{box-sizing:border-box}' +
    '#ka-scrim[hidden],#ka-sheet[hidden]{display:none}' +
    '@keyframes ka-fade{from{opacity:0}to{opacity:1}}' +
    '@keyframes ka-up{from{transform:translateY(24px);opacity:0}to{transform:none;opacity:1}}' +
    '@media (min-width:768px){#ka-sheet{left:50%;right:auto;transform:translateX(-50%);width:520px;max-width:100%}}' +
    '.ka-handle{width:40px;height:5px;border-radius:var(--radius-pill);background:var(--hairline-deep);margin:0 auto 6px;flex:none}' +
    '.ka-crumb{align-self:flex-start;min-height:44px;margin:-8px 0 -6px -4px;padding:0 4px;background:none;border:0;' +
      'font:600 15px var(--font-body);color:var(--ink-secondary);cursor:pointer}' +
    '.ka-eyebrow{margin:0;font:600 12px/1.2 var(--font-body);letter-spacing:.08em;text-transform:uppercase;color:var(--celadon-label)}' +
    '.ka-title{font-family:var(--font-display);font-weight:700;font-size:24px;letter-spacing:-0.03em;line-height:1.1;color:var(--ink);margin:0}' +
    '.ka-title em{font-family:var(--font-accent);font-style:italic;font-weight:400;color:var(--apricot-label)}' +
    '.ka-sub{font-size:14px;line-height:1.4;color:var(--ink-secondary);margin:0 0 4px}' +
    '.ka-opt{display:flex;align-items:center;gap:14px;width:100%;min-height:64px;padding:12px 14px;text-align:left;' +
      'background:transparent;border:1px solid var(--hairline-strong);border-radius:var(--radius-card);color:var(--ink);cursor:pointer;font:inherit}' +
    '.ka-opt:hover{border-color:var(--ink-strong)}' +
    '.ka-ic{width:44px;height:44px;flex:none;border-radius:var(--radius-badge);background:var(--celadon-tint);color:var(--celadon-label);' +
      'display:flex;align-items:center;justify-content:center}' +
    '.ka-ic svg{width:22px;height:22px}' +
    '.ka-opt-title{margin:0;font-size:15px;font-weight:700;color:var(--ink)}' +
    '.ka-opt-line{margin:2px 0 0;font-size:13px;line-height:1.35;color:var(--ink-secondary)}' +
    '.ka-chips{display:flex;gap:8px}' +
    '.ka-chip{flex:1;min-height:44px;border-radius:var(--radius-chip);border:1.5px solid var(--hairline-strong);background:transparent;' +
      'font:600 14px var(--font-body);color:var(--ink);cursor:pointer}' +
    '.ka-chip[aria-pressed="true"]{background:var(--celadon);border-color:var(--celadon);color:var(--on-accent-ink)}' +
    '.ka-list{display:flex;flex-direction:column;border:1px solid var(--hairline);border-radius:var(--radius-card);padding:2px 12px}' +
    '.ka-row{display:flex;align-items:center;gap:12px;width:100%;min-height:48px;padding:4px 0;background:none;border:0;' +
      'border-top:1px solid var(--hairline);text-align:left;cursor:pointer;font:500 15px var(--font-body);color:var(--ink)}' +
    '.ka-row:first-child{border-top:0}' +
    '.ka-box{width:22px;height:22px;flex:none;border-radius:var(--radius-pill);border:2px solid var(--hairline-strong);color:transparent;' +
      'display:flex;align-items:center;justify-content:center}' +
    '.ka-box svg{width:13px;height:13px}' +
    '.ka-row[aria-pressed="true"] .ka-box{background:var(--celadon);border-color:var(--celadon);color:var(--on-accent-ink)}' +
    '.ka-row-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
    '.ka-row[aria-pressed="false"] .ka-row-name{color:var(--ink-secondary)}' +
    '.ka-row-qty{flex:none;max-width:40%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;color:var(--ink-secondary)}' +
    '.ka-primary{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;min-height:52px;border:0;border-radius:var(--radius-action);' +
      'background:var(--apricot);color:var(--on-accent-ink);font-family:var(--font-display);font-weight:700;font-size:16px;letter-spacing:-0.02em;' +
      'cursor:pointer;box-shadow:var(--shadow-action);margin-top:4px}' +
    '.ka-primary svg{width:20px;height:20px}' +
    '.ka-primary:disabled{opacity:.45;cursor:default;box-shadow:none}' +
    '.ka-outline{display:flex;align-items:center;justify-content:center;width:100%;min-height:48px;border-radius:var(--radius-action);' +
      'background:transparent;border:1.5px solid var(--hairline-strong);font-family:var(--font-display);font-weight:700;font-size:15px;' +
      'color:var(--ink-strong);cursor:pointer}' +
    '.ka-quiet{display:flex;align-items:center;justify-content:center;width:100%;min-height:44px;background:none;border:0;padding:0;' +
      'font:italic 400 16px var(--font-accent);color:var(--ink-secondary);cursor:pointer}' +
    '.ka-input{width:100%;border:1.5px solid var(--hairline-strong);border-radius:var(--radius-control);padding:12px 14px;' +
      'font:500 16px var(--font-body);color:var(--ink);background:var(--surface);resize:none;line-height:1.4}' +
    '.ka-input:focus{outline:none;border-color:var(--ink-strong)}' +
    '.ka-input::placeholder{color:var(--ink-placeholder)}' +
    '.ka-say{display:flex;gap:8px;align-items:flex-start}' +
    '.ka-mic{width:48px;height:48px;flex:none;border-radius:var(--radius-pill);border:1.5px solid var(--hairline-strong);background:transparent;' +
      'color:var(--ink);display:flex;align-items:center;justify-content:center;cursor:pointer}' +
    '.ka-mic svg{width:22px;height:22px}' +
    '.ka-mic.active{background:var(--celadon);border-color:var(--celadon);color:var(--on-accent-ink)}' +
    '.ka-mic[hidden]{display:none}' +
    '.ka-wait{display:flex;align-items:center;gap:12px;min-height:88px;color:var(--ink-secondary);font-size:15px}' +
    '.ka-spin{width:22px;height:22px;flex:none;border-radius:50%;border:2.5px solid var(--hairline-strong);border-top-color:var(--celadon-label);' +
      'animation:ka-spin 0.9s linear infinite}' +
    '@keyframes ka-spin{to{transform:rotate(360deg)}}' +
    '@media (prefers-reduced-motion:reduce){.ka-spin{animation:none}}' +
    '.ka-note{margin:0;font-size:14px;line-height:1.4;color:var(--urgent-label)}' +
    '.ka-step{display:flex;align-items:center;gap:14px;padding:10px 0;border-top:1px solid var(--hairline)}' +
    '.ka-step:first-child{border-top:0}' +
    '.ka-step .ka-ic.is-done{background:var(--celadon);color:var(--on-accent-ink)}' +
    '.ka-step .ka-ic.is-skipped{background:var(--sand);color:var(--ink-muted)}' +
    '.ka-more{min-height:44px;background:none;border:0;border-top:1px solid var(--hairline);text-align:left;cursor:pointer;' +
      'font:600 14px var(--font-body);color:var(--ink-secondary)}' +
    '.ka-step .ka-ic.is-later{background:var(--sand);color:var(--ink-muted)}' +
    '.ka-step-title{margin:0;font-size:15px;font-weight:700;color:var(--ink)}' +
    '.ka-step.is-later .ka-step-title{color:var(--ink-secondary)}' +
    '.ka-step-line{margin:2px 0 0;font-size:13px;color:var(--ink-secondary)}' +
    '.ka-link{align-self:flex-start;min-height:44px;padding:0 4px;background:none;border:0;font:500 14px var(--font-body);' +
      'color:var(--ink-secondary);text-decoration:underline;text-underline-offset:3px;cursor:pointer}';

  var sheetEl = null;
  var setupMemory = null;
  var scrimEl = null;
  var fileEl = null;
  var st = null;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function ensureBuilt() {
    if (sheetEl) return;
    var doc = global.document;
    if (!doc.getElementById('ka-style')) {
      var style = doc.createElement('style');
      style.id = 'ka-style';
      style.textContent = STYLE;
      doc.head.appendChild(style);
    }
    scrimEl = doc.createElement('div');
    scrimEl.id = 'ka-scrim';
    scrimEl.hidden = true;
    sheetEl = doc.createElement('div');
    sheetEl.id = 'ka-sheet';
    sheetEl.hidden = true;
    sheetEl.setAttribute('role', 'dialog');
    sheetEl.setAttribute('aria-modal', 'true');
    sheetEl.setAttribute('aria-labelledby', 'ka-title');
    // The camera. capture="environment" opens the back camera on a phone;
    // a desktop browser ignores it and shows a file picker, which is fine.
    fileEl = doc.createElement('input');
    fileEl.type = 'file';
    fileEl.accept = 'image/*';
    fileEl.setAttribute('capture', 'environment');
    fileEl.hidden = true;
    fileEl.id = 'ka-photo';
    doc.body.appendChild(scrimEl);
    doc.body.appendChild(sheetEl);
    doc.body.appendChild(fileEl);
    scrimEl.addEventListener('click', close);
    doc.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape' || !sheetEl || sheetEl.hidden) return;
      // Words typed in Say it / Type it keep the sheet open, the courtesy
      // shell.js's escapeClosesTabSheet gives every tab sheet.
      var active = doc.activeElement;
      if (active && sheetEl.contains(active) && /^(INPUT|TEXTAREA)$/.test(active.tagName) &&
          String(active.value || '').trim()) return;
      close();
    });
    fileEl.addEventListener('change', function () {
      var file = fileEl.files && fileEl.files[0];
      fileEl.value = ''; // the same photo can be picked twice
      if (file && st) scan(st.kind, file);
    });
    sheetEl.addEventListener('click', onClick);
  }

  function open(opts) {
    opts = opts || {};
    ensureBuilt();
    var place = PLACES.indexOf(opts.place) !== -1 ? opts.place : 'fridge';
    st = {
      opts: opts,
      mode: opts.mode === 'snap' || opts.mode === 'setup' ? opts.mode : 'add',
      step: 'menu',
      place: place,
      kind: place,
      rows: [],
      fromMenu: false,
      error: '',
      busy: false,
      setup: { fridge: null, freezer: null, pantry: null },
      setupTotal: 0,
      leftOut: null,
      showAll: false
    };
    // Closing the three-photo sheet part-way and opening it again picks up
    // where it was: a finished shelf stays finished.
    if (st.mode === 'setup' && setupMemory) {
      st.setup = setupMemory.setup;
      st.setupTotal = setupMemory.total;
    }
    if (st.mode === 'snap') st.step = 'snap';
    if (st.mode === 'setup') st.step = 'setup';
    scrimEl.hidden = false;
    sheetEl.hidden = false;
    render();
  }

  function close() {
    if (!sheetEl || sheetEl.hidden) return;
    sheetEl.hidden = true;
    scrimEl.hidden = true;
    var done = st;
    st = null;
    if (done && done.opts.onClose) {
      try { done.opts.onClose(); } catch (err) { /* the host's own business */ }
    }
  }

  function crumbHtml() {
    return st.fromMenu ? '<button type="button" class="ka-crumb" data-ka="menu">' + esc(COPY.back) + '</button>' : '';
  }

  function placeChipsHtml(selected) {
    return '<div class="ka-chips" role="group">' + PLACES.map(function (p) {
      return '<button type="button" class="ka-chip" data-ka="place" data-place="' + p + '" aria-pressed="' +
        (p === selected ? 'true' : 'false') + '">' + PLACE_LABELS[p] + '</button>';
    }).join('') + '</div>';
  }

  function menuHtml() {
    return '<div class="ka-handle" aria-hidden="true"></div>' +
      '<h2 class="ka-title" id="ka-title">' + esc(COPY.title) + '</h2>' +
      '<p class="ka-sub">' + esc(COPY.sub) + '</p>' +
      OPTIONS.map(function (o) {
        return '<button type="button" class="ka-opt" data-ka="opt" data-opt="' + o.key + '">' +
          '<span class="ka-ic">' + ICONS[o.key] + '</span>' +
          '<span><p class="ka-opt-title">' + esc(o.title) + '</p><p class="ka-opt-line">' + esc(o.line) + '</p></span>' +
        '</button>';
      }).join('');
  }

  // The photo step: which shelf, then the camera. The dock's "Snap a shelf"
  // lands here with the shelf on screen already picked.
  function snapHtml() {
    return '<div class="ka-handle" aria-hidden="true"></div>' + crumbHtml() +
      '<h2 class="ka-title" id="ka-title">' + esc(COPY.snapTitle) + '</h2>' +
      placeChipsHtml(st.place) +
      '<button type="button" class="ka-primary" data-ka="camera">' + ICONS.snap +
        '<span>Take a photo of the ' + esc(st.place) + '</span></button>';
  }

  function waitHtml(text) {
    return '<div class="ka-handle" aria-hidden="true"></div>' +
      '<h2 class="ka-title" id="ka-title">' + (st.kind === 'say' ? COPY.heardTitle : COPY.reviewTitle) + '</h2>' +
      '<div class="ka-wait" role="status"><span class="ka-spin" aria-hidden="true"></span><span>' + esc(text) + '</span></div>';
  }

  // How many rows show before "+ N more", so the one button stays in view
  // at phone height (the mockup folds at six; a seventh row costs nothing).
  var REVIEW_FOLD = 7;

  function reviewWayOutHtml() {
    if (st.mode === 'setup' && PLACES.indexOf(st.kind) !== -1) {
      return '<button type="button" class="ka-quiet" data-ka="skip">Skip the ' + esc(st.kind) + '</button>';
    }
    if (st.kind === 'say') return '<button type="button" class="ka-quiet" data-ka="again">' + esc(COPY.sayAgain) + '</button>';
    return '<button type="button" class="ka-quiet" data-ka="retake">' + esc(COPY.retakePhoto) + '</button>';
  }

  function leftOutHtml() {
    var lo = st.leftOut || {};
    var names = (lo.spices || []).concat(lo.regulars || []);
    return names.length ? '<p class="ka-sub">' + esc(COPY.leftOut + names.join(', ')) + '</p>' : '';
  }

  function reviewHtml() {
    var n = tickedCount(st.rows);
    var place = reviewPlace(st.kind, st.rows);
    var eyebrow = COPY.eyebrows[st.kind] ? '<p class="ka-eyebrow">' + esc(COPY.eyebrows[st.kind]) + '</p>' : '';
    if (!st.rows.length) {
      return '<div class="ka-handle" aria-hidden="true"></div>' + eyebrow +
        '<h2 class="ka-title" id="ka-title">' + esc(COPY.emptyTitle) + '</h2>' +
        '<p class="ka-sub">' + esc(st.kind === 'say' ? COPY.emptySaid : COPY.emptySub) + '</p>' + leftOutHtml() +
        '<button type="button" class="ka-primary" data-ka="again">' + esc(COPY.tryAgain) + '</button>' +
        (st.mode === 'setup' && PLACES.indexOf(st.kind) !== -1 ? reviewWayOutHtml() : '');
    }
    var shown = st.showAll ? st.rows.length : Math.min(st.rows.length, REVIEW_FOLD);
    var more = st.rows.length - shown;
    return '<div class="ka-handle" aria-hidden="true"></div>' + eyebrow +
      '<h2 class="ka-title" id="ka-title">' + (st.kind === 'say' ? COPY.heardTitle : COPY.reviewTitle) + '</h2>' +
      '<p class="ka-sub">' + esc(COPY.reviewSub) + '</p>' +
      '<div class="ka-list">' + st.rows.slice(0, shown).map(function (r, i) {
        var right = r.sure ? r.quantity : COPY.notSure;
        return '<button type="button" class="ka-row" data-ka="tick" data-i="' + i + '" aria-pressed="' + (r.ticked ? 'true' : 'false') + '">' +
          '<span class="ka-box">' + ICONS.tick + '</span>' +
          '<span class="ka-row-name">' + esc(r.item) + (r.sure ? '' : '?') + '</span>' +
          (right ? '<span class="ka-row-qty">' + esc(right) + '</span>' : '') +
        '</button>';
      }).join('') +
      (more ? '<button type="button" class="ka-more" data-ka="more">+ ' + more + ' more</button>' : '') +
      '</div>' + leftOutHtml() +
      (st.error ? '<p class="ka-note" role="alert">' + esc(st.error) + '</p>' : '') +
      '<button type="button" class="ka-primary" data-ka="save"' + (n && !st.busy ? '' : ' disabled') + '>' +
        esc(addButtonLabel(n, place)) + '</button>' + reviewWayOutHtml();
  }

  function errorHtml() {
    return '<div class="ka-handle" aria-hidden="true"></div>' +
      '<h2 class="ka-title" id="ka-title">' + esc(st.errorTitle || COPY.errorTitle) + '</h2>' +
      '<p class="ka-sub">' + esc(st.error) + '</p>' +
      '<button type="button" class="ka-primary" data-ka="again">' + esc(COPY.tryAgain) + '</button>';
  }

  function sayHtml() {
    return '<div class="ka-handle" aria-hidden="true"></div>' + crumbHtml() +
      '<h2 class="ka-title" id="ka-title">' + esc(COPY.sayTitle) + '</h2>' +
      '<div class="ka-say"><textarea id="ka-say-text" class="ka-input" rows="3" aria-label="' + esc(COPY.sayTitle) + '" placeholder="' +
        esc(COPY.sayPlaceholder) + '">' + esc(st.said || '') + '</textarea>' +
        '<button type="button" class="ka-mic" id="ka-say-mic" aria-label="Talk">' + ICONS.say + '</button></div>' +
      '<p class="ka-sub" id="ka-say-note" role="status" hidden></p>' +
      '<button type="button" class="ka-primary" data-ka="said">' + esc(COPY.sayNext) + '</button>';
  }

  function typeHtml() {
    return '<div class="ka-handle" aria-hidden="true"></div>' + crumbHtml() +
      '<h2 class="ka-title" id="ka-title">' + esc(COPY.typeTitle) + '</h2>' +
      '<input id="ka-type-text" class="ka-input" type="text" autocomplete="off" aria-label="' + esc(COPY.typeTitle) + '" placeholder="' +
        esc(COPY.typePlaceholder) + '" value="' + esc(st.typed || '') + '">' +
      placeChipsHtml(st.place) +
      (st.error ? '<p class="ka-note" role="alert">' + esc(st.error) + '</p>' : '') +
      '<button type="button" class="ka-primary" data-ka="typed"' + ((st.typed || '').trim() && !st.busy ? '' : ' disabled') + '>' +
        esc('Add to the ' + st.place) + '</button>';
  }

  // OptionC screen 2: the three shelves, the one up next with the camera,
  // done ones ticked with what they added, the rest "Next".
  function setupNext() {
    for (var i = 0; i < PLACES.length; i++) if (st.setup[PLACES[i]] === null) return PLACES[i];
    return '';
  }

  function setupHtml() {
    var next = setupNext();
    var anyDone = PLACES.some(function (p) { return st.setup[p] !== null; });
    return '<div class="ka-handle" aria-hidden="true"></div>' +
      '<p class="ka-eyebrow">' + esc(COPY.setupEyebrow) + '</p>' +
      '<h2 class="ka-title" id="ka-title">' + COPY.setupTitle + '</h2>' +
      '<p class="ka-sub">' + esc(COPY.setupSub) + '</p>' +
      '<div class="ka-list">' + PLACES.map(function (p) {
        var result = st.setup[p];
        var done = result !== null;
        var now = p === next;
        var line = done ? setupLine(result) : (now ? COPY.setupNow : COPY.setupNext);
        return '<div class="ka-step' + (!done && !now ? ' is-later' : '') + '">' +
          '<span class="ka-ic' + (result === 'skipped' ? ' is-skipped' : (done ? ' is-done' : (now ? '' : ' is-later'))) + '">' +
            (result === 'skipped' ? ICONS.skip : (done ? ICONS.tick : ICONS.snap)) + '</span>' +
          '<div><p class="ka-step-title">' + PLACE_LABELS[p] + '</p><p class="ka-step-line">' + esc(line) + '</p></div>' +
        '</div>';
      }).join('') + '</div>' +
      (!anyDone ? '<button type="button" class="ka-link" data-ka="setup-receipt">' + esc(COPY.setupReceipt) + '</button>' : '') +
      (next
        ? '<button type="button" class="ka-primary" data-ka="camera">' + ICONS.snap + '<span>Take a photo of the ' + next + '</span></button>' +
          '<button type="button" class="ka-outline" data-ka="skip">Skip the ' + next + '</button>'
        : '<button type="button" class="ka-primary" data-ka="setup-done">' + esc(st.opts.doneLabel || 'Done') + '</button>');
  }

  function render() {
    if (!st) return;
    var html = '';
    if (st.step === 'menu') html = menuHtml();
    else if (st.step === 'snap') html = snapHtml();
    else if (st.step === 'reading') html = waitHtml(st.kind === 'say' ? COPY.readingSaid : COPY.reading);
    else if (st.step === 'review') html = reviewHtml();
    else if (st.step === 'error') html = errorHtml();
    else if (st.step === 'say') html = sayHtml();
    else if (st.step === 'type') html = typeHtml();
    else if (st.step === 'setup') html = setupHtml();
    sheetEl.innerHTML = html;
    if (st.step === 'say') wireSay();
    if (st.step === 'type') wireType();
  }

  function wireSay() {
    var box = sheetEl.querySelector('#ka-say-text');
    var mic = sheetEl.querySelector('#ka-say-mic');
    var note = sheetEl.querySelector('#ka-say-note');
    // Today's voice input (Emily, 2026-10-27: keep Say it, on what's
    // there): static/dictation.js — the Web Speech API where the browser
    // has it, and on an iPhone the keyboard's own mic, which the helper
    // points at by focusing the box. The review catches a mishearing.
    // Next reads the box when it is tapped, so dictation (which writes
    // .value with no 'input' event) needs no listener here.
    if (typeof global.setupDictation === 'function') {
      global.setupDictation(box, mic, { onMessage: function (msg) {
        if (note) { note.textContent = msg; note.hidden = false; }
      } });
    } else {
      mic.hidden = true;
    }
    box.focus();
  }

  function wireType() {
    var box = sheetEl.querySelector('#ka-type-text');
    var add = sheetEl.querySelector('[data-ka="typed"]');
    box.addEventListener('input', function () {
      st.typed = box.value;
      add.disabled = !box.value.trim() || st.busy;
    });
    box.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && box.value.trim()) { e.preventDefault(); saveTyped(); }
    });
    box.focus();
  }

  function onClick(e) {
    var el = e.target && e.target.closest && e.target.closest('[data-ka]');
    if (!el || !st) return;
    var what = el.getAttribute('data-ka');
    if (what === 'opt') {
      var opt = el.getAttribute('data-opt');
      st.fromMenu = true;
      st.error = '';
      if (opt === 'receipt') { st.kind = 'receipt'; pickPhoto(); return; }
      st.step = opt === 'snap' ? 'snap' : opt;
      if (opt === 'snap') st.kind = st.place;
      render();
    } else if (what === 'menu') {
      st.step = 'menu';
      st.error = '';
      render();
    } else if (what === 'place') {
      st.place = el.getAttribute('data-place');
      if (st.step === 'snap') st.kind = st.place;
      if (st.step === 'type') {
        var box = sheetEl.querySelector('#ka-type-text');
        if (box) st.typed = box.value;
      }
      render();
    } else if (what === 'camera') {
      if (st.step === 'setup') st.kind = setupNext();
      pickPhoto();
    } else if (what === 'setup-receipt') {
      st.kind = 'receipt';
      pickPhoto();
    } else if (what === 'skip') {
      var p = st.step === 'review' ? st.kind : setupNext();
      if (p) st.setup[p] = 'skipped';
      st.step = 'setup';
      st.rows = [];
      remember();
      render();
    } else if (what === 'more') {
      st.showAll = true;
      render();
    } else if (what === 'retake') {
      pickPhoto();
    } else if (what === 'setup-done') {
      finishSetup();
    } else if (what === 'tick') {
      var r = st.rows[parseInt(el.getAttribute('data-i'), 10)];
      if (r) { r.ticked = !r.ticked; st.error = ''; render(); }
    } else if (what === 'save') {
      saveReview();
    } else if (what === 'said') {
      readSaid();
    } else if (what === 'typed') {
      saveTyped();
    } else if (what === 'again') {
      st.error = '';
      st.errorTitle = '';
      if (st.mode === 'setup') st.step = 'setup';
      else if (st.kind === 'say') st.step = 'say';
      else if (st.kind === 'receipt') { st.step = st.fromMenu ? 'menu' : 'snap'; }
      else st.step = 'snap';
      render();
    }
  }

  // Opened from inside the tap, so the browser lets it open the camera.
  function pickPhoto() {
    if (fileEl) fileEl.click();
  }

  function failWith(err, photo) {
    var offline = (typeof navigator !== 'undefined' && navigator.onLine === false) || (err && err.status === 0);
    st.step = 'error';
    st.errorTitle = offline ? COPY.noSignalTitle : (photo ? COPY.errorTitle : '');
    // The server's own sentence only where it is written for a person (a
    // photo it can't take, Claude busy, too many at once); a 500's detail
    // is a stack's last line, never shown.
    var said = err && err.detail && [400, 429, 503].indexOf(err.status) !== -1 ? err.detail : '';
    st.error = offline ? COPY.noSignalSub : (said || COPY.emptySub);
  }

  function scan(kind, file) {
    var route = { fridge: 'scan-fridge', freezer: 'scan-freezer', pantry: 'scan-pantry', receipt: 'scan-receipt' }[kind];
    if (!route) return;
    st.kind = kind;
    st.step = 'reading';
    render();
    var mine = st;
    var form = new FormData();
    form.append('photo', file);
    Api.json('/api/inventory/' + route, { body: form, quiet: true }).then(function (data) {
      if (st !== mine) return;
      st.rows = reviewRows(data && data.items);
      st.leftOut = (data && data.left_out) || null;
      st.showAll = false;
      st.step = 'review';
      render();
    }, function (err) {
      if (st !== mine) return;
      failWith(err, true);
      render();
    });
  }

  function readSaid() {
    var box = sheetEl.querySelector('#ka-say-text');
    if (box) st.said = box.value;
    var text = String(st.said || '').trim();
    if (!text) { if (box) box.focus(); return; }
    st.kind = 'say';
    st.step = 'reading';
    render();
    var mine = st;
    Api.json('/api/whats-home/read-said', { body: { text: text }, quiet: true }).then(function (data) {
      if (st !== mine) return;
      st.rows = reviewRows(data && data.items);
      st.step = 'review';
      render();
    }, function (err) {
      if (st !== mine) return;
      failWith(err, false);
      render();
    });
  }

  function post(kind, items) {
    return Api.json('/api/whats-home/add', { body: { kind: kind, items: items }, quiet: true });
  }

  function remember() {
    if (st && st.mode === 'setup') setupMemory = { setup: st.setup, total: st.setupTotal };
  }

  function saved(res) {
    var line = savedLine(res.added, res.place, res.items, res.updated);
    if (st.opts.onSaved) {
      try { st.opts.onSaved({ added: res.added, updated: res.updated, place: res.place, items: res.items, undo: res.undo, line: line }); }
      catch (err) { /* the host's own business */ }
    }
  }

  // The review's one button: only the ticked rows are sent.
  function saveReview() {
    if (st.busy) return;
    var ticked = st.rows.filter(function (r) { return r.ticked; }).map(function (r) {
      return { item: r.item, quantity: r.quantity, category: r.category, location: r.location };
    });
    if (!ticked.length) return;
    st.busy = true;
    render();
    var mine = st;
    post(st.kind, ticked).then(function (res) {
      if (st !== mine) return;
      st.busy = false;
      if (st.mode === 'setup') {
        st.setupTotal += (res.added || 0) + (res.updated || 0);
        if (st.kind === 'receipt') { finishSetup(); return; }
        st.setup[st.kind] = (res.added || 0) + (res.updated || 0);
        remember();
        st.step = 'setup';
        render();
        return;
      }
      saved(res);
      close();
    }, function () {
      if (st !== mine) return;
      st.busy = false;
      st.error = COPY.saveFailed;
      render();
    });
  }

  function saveTyped() {
    var name = String(st.typed || '').trim();
    if (!name || st.busy) return;
    st.busy = true;
    st.error = '';
    var mine = st;
    post('type', [{ item: name, location: st.place }]).then(function (res) {
      if (st !== mine) return;
      st.busy = false;
      saved(res);
      close();
    }, function () {
      if (st !== mine) return;
      st.busy = false;
      st.error = COPY.saveFailed;
      render();
    });
  }

  function finishSetup() {
    setupMemory = null;
    var total = st.setupTotal;
    var onDone = st.opts.onDone;
    st.opts.onClose = null;
    close();
    if (onDone) {
      try { onDone(total); } catch (err) { /* the host's own business */ }
    }
  }

  // A screen's markup for a given state, without a document — what
  // tests/test_kitchen_add.py reads under node. Leaves the live state as
  // it was.
  function htmlFor(over) {
    var keep = st;
    st = {
      opts: {}, mode: 'add', step: 'menu', place: 'fridge', kind: 'fridge', rows: [],
      fromMenu: false, error: '', busy: false,
      setup: { fridge: null, freezer: null, pantry: null }, setupTotal: 0, leftOut: null, showAll: false
    };
    for (var k in (over || {})) if (Object.prototype.hasOwnProperty.call(over, k)) st[k] = over[k];
    var builders = { menu: menuHtml, snap: snapHtml, review: reviewHtml, say: sayHtml, type: typeHtml, setup: setupHtml };
    var out = builders[st.step] ? builders[st.step]() : '';
    st = keep;
    return out;
  }

  var api = {
    open: open,
    close: close,
    isOpen: function () { return !!(sheetEl && !sheetEl.hidden); },
    // For tests and the host's own toast.
    OPTIONS: OPTIONS,
    COPY: COPY,
    reviewRows: reviewRows,
    tickedCount: tickedCount,
    reviewPlace: reviewPlace,
    addButtonLabel: addButtonLabel,
    savedLine: savedLine,
    setupLine: setupLine,
    htmlFor: htmlFor
  };
  global.KitchenAdd = api;
  // So tests can require this file under node and run the builders.
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
