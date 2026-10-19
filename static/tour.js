/*
 * The first-open tour (Loop Board "Build the in-app tour (Option A)",
 * Emily, 2026-10-18). Six stops over the real screens: how to change the
 * plan (Swap and Tweak, Move and Pick, the Pomona button), then Shop, Cook
 * and Today, ending back on the draft week. Drawn in
 * mockups/first-open-tour.html; the copy here is that copy.
 *
 * shell.js decides WHEN (a first draft on Plan, GET /api/tour says show;
 * or "Show me around again" in Preferences) and hands in the few shell
 * powers a stop needs — switching tab — as `host`. This file owns the
 * overlay and nothing else: it never fetches, so it can't add a raw
 * /api call (tests/test_api_js.py), and shell.js records the ending.
 *
 * A stop names its target as a function, not a selector, because "the
 * first row that has both Swap and Tweak" isn't a selector. When the
 * target isn't on screen this week — no open meal to Pick, no list yet to
 * shop from — the stop still reads: the card sits centred with no ring.
 * Nothing animates (the tour isn't one of DESIGN_SYSTEM §4's five), and
 * every colour is a token in shell.css (.tour-*), so dark mode follows.
 */
(function (global) {
  'use strict';

  function visible(el) {
    if (!el || !el.getClientRects().length) return false;
    // A built panel that isn't the active tab still holds its buttons.
    var panel = el.closest('.tab-panel');
    if (panel && !panel.classList.contains('active')) return false;
    var r = el.getBoundingClientRect();
    // Sideways too: Schedule's carousel keeps every day's card laid out
    // beside the one in view, so Saturday's Pick has a real box at x=2070.
    // Ringing that would ring nothing; the next candidate (or the centred
    // card) reads better than scrolling the carousel to another day.
    return r.width > 0 && r.height > 0 && r.right > 0 && r.left < global.innerWidth;
  }
  function first(selector, pick) {
    var all = document.querySelectorAll(selector);
    for (var i = 0; i < all.length; i++) {
      if (visible(all[i])) return pick ? pick(all[i]) : all[i];
    }
    return null;
  }
  function tabButton(key) { return first('.tab-btn[data-tab="' + key + '"]'); }

  var STOPS = [
    {
      tab: 'week',
      title: 'Swap it or tweak it',
      body: 'Swap gives you a few other dishes to choose from. Tweak changes one part, like the protein or a side. Not in the list? Type something else.',
      // The buttons of the first row that has Tweak (so Swap is beside
      // it), else the first Swap's row.
      target: function () {
        return first('[data-wk-tweak]', function (b) { return b.closest('.wk-row-acts') || b; }) ||
          first('[data-wk-swap-sheet]', function (b) { return b.closest('.wk-row-acts') || b; });
      }
    },
    {
      tab: 'week',
      title: 'Move it, or pick it',
      body: 'Move puts a meal on another day. A meal that says “Your call” is yours to decide: tap Pick and choose from a few.',
      target: function () { return first('[data-wk-pick]') || first('[data-wk-move]'); }
    },
    {
      tab: 'week',
      title: 'Or just tell me',
      // Not "Tap here": the overlay takes the tap, so the line points at
      // the button rather than promising it works right now.
      body: 'This is where you ask: “No fish this week.” “Make Tuesday quicker.” I’ll show you what I’d change.',
      target: function () { return first('#chat-fab'); }
    },
    {
      tab: 'grocery',
      title: 'Shop',
      body: 'Approve the week and your shopping list lands here. Before you shop, tick any spices, sauces or pantry staples you’re out of. I leave those off unless you say.',
      target: function (late) { return first('[data-gro="goto-beforeshop"]') || (late ? tabButton('grocery') : null); }
    },
    {
      tab: 'kitchen',
      title: 'Cook',
      // Two ways to read, by what's ringed: Start cooking (tonight has a
      // cook) or just the Cook tab (a draft nobody's approved yet).
      body: function (el) {
        return el && el.closest && el.closest('#kit-dock')
          ? 'Tonight’s dinner, when to start, and the recipe. Start cooking takes you through it a step at a time.'
          : 'Once the week’s approved, tonight’s dinner lives here, with when to start and the recipe. Start cooking takes you through it a step at a time.';
      },
      target: function (late) { return first('#kit-dock button') || (late ? tabButton('kitchen') : null); }
    },
    {
      tab: 'today',
      title: 'Today',
      body: 'What today needs: what to buy, what to cook, and when to start. Tick things off as you go.',
      target: function () { return tabButton('today'); }
    }
  ];
  var LAST_LABEL = 'Back to my week';

  function bodyOf(stop, el) {
    return typeof stop.body === 'function' ? stop.body(el) : stop.body;
  }

  var root = null;
  var state = null; // { index, host, opts, returnFocus, target }

  function build() {
    if (root) return;
    root = document.createElement('div');
    root.id = 'tour';
    root.className = 'tour';
    root.hidden = true;
    root.innerHTML =
      '<div class="tour-hole" id="tour-hole"></div>' +
      '<div class="tour-card" id="tour-card" role="dialog" aria-modal="true" ' +
        'aria-labelledby="tour-title" aria-describedby="tour-body">' +
        '<p class="tour-count" id="tour-count"></p>' +
        '<h2 class="tour-title" id="tour-title"></h2>' +
        '<p class="tour-body" id="tour-body"></p>' +
        '<div class="tour-acts">' +
          '<button type="button" class="tour-skip" id="tour-skip">Skip the tour</button>' +
          '<button type="button" class="tour-next" id="tour-next">Next</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(root);
    root.querySelector('#tour-skip').addEventListener('click', function () { end(true); });
    root.querySelector('#tour-next').addEventListener('click', next);
    // Capture, so the Esc that skips the tour isn't also closing a sheet
    // underneath it, and Tab stays between the card's two buttons.
    document.addEventListener('keydown', onKey, true);
    global.addEventListener('resize', function () { if (state) place(); });
  }

  function onKey(e) {
    if (!state) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      end(true);
    } else if (e.key === 'Tab') {
      var skip = root.querySelector('#tour-skip');
      var nxt = root.querySelector('#tour-next');
      var on = document.activeElement;
      e.preventDefault();
      (on === nxt ? skip : nxt).focus();
    }
  }

  // Wait for the stop's target: a tab just switched to may still be
  // drawing. Gives up after ~1.5s and the card sits centred instead.
  function findTarget(stop, cb) {
    var tries = 0;
    (function look() {
      if (!state) return;
      var el = null;
      // `late`: the tab's own button is only the fallback once the screen
      // has had ~0.8s to draw its real control — it is always there, so
      // asking for it first would win before Shop or Cook had drawn.
      try { el = stop.target(tries >= 8); } catch (err) { el = null; }
      if (el || tries++ >= 15) return cb(el);
      setTimeout(look, 100);
    })();
  }

  function show(index) {
    var stop = STOPS[index];
    state.index = index;
    state.target = null;
    try { state.host.goTab(stop.tab); } catch (err) { /* the card still reads */ }
    root.querySelector('#tour-count').textContent = (index + 1) + ' of ' + STOPS.length;
    root.querySelector('#tour-title').textContent = stop.title;
    root.querySelector('#tour-body').textContent = bodyOf(stop, null);
    root.querySelector('#tour-next').textContent = index === STOPS.length - 1 ? LAST_LABEL : 'Next';
    root.hidden = false;
    place();
    root.querySelector('#tour-next').focus();
    findTarget(stop, function (el) {
      if (!state || state.index !== index) return;
      state.target = el;
      root.querySelector('#tour-body').textContent = bodyOf(stop, el);
      if (el) {
        var r = el.getBoundingClientRect();
        // Off screen (a row further down the week): bring it into view
        // before ringing it. Fixed things — the tab bar, the chat button —
        // are always in view and never scroll.
        if (r.top < 0 || r.bottom > global.innerHeight - 150) {
          try { el.scrollIntoView({ block: 'center' }); } catch (err) { el.scrollIntoView(); }
        }
      }
      place();
    });
  }

  // The ring is the target's box plus 4px; the dim is its own box-shadow.
  // The card goes below the ring when it fits, else above, else centred.
  function place() {
    var hole = root.querySelector('#tour-hole');
    var card = root.querySelector('#tour-card');
    var vh = global.innerHeight;
    var el = state && state.target;
    var r = el && el.isConnected ? el.getBoundingClientRect() : null;
    card.style.top = '';
    card.classList.remove('is-centred');
    if (!r || !r.width) {
      hole.classList.add('is-empty');
      root.classList.add('is-centred');
      card.classList.add('is-centred');
      return;
    }
    root.classList.remove('is-centred');
    hole.classList.remove('is-empty');
    var pad = 4;
    hole.style.left = (r.left - pad) + 'px';
    hole.style.top = (r.top - pad) + 'px';
    hole.style.width = (r.width + pad * 2) + 'px';
    hole.style.height = (r.height + pad * 2) + 'px';
    var gap = 12;
    var h = card.offsetHeight;
    var below = r.bottom + pad + gap;
    var above = r.top - pad - gap - h;
    if (below + h <= vh - 8) card.style.top = below + 'px';
    else if (above >= 8) card.style.top = above + 'px';
    else card.classList.add('is-centred');
  }

  function next() {
    if (!state) return;
    if (state.index >= STOPS.length - 1) return end(false);
    show(state.index + 1);
  }

  function end(skipped) {
    if (!state) return;
    var s = state;
    state = null;
    root.hidden = true;
    // Ends on the draft week, whichever way it ended (the card: "ending
    // on the draft week").
    try { s.host.goTab('week'); } catch (err) { /* nothing to undo */ }
    try { if (s.returnFocus && s.returnFocus.focus) s.returnFocus.focus(); } catch (err) { /* gone */ }
    if (s.host.onEnd) s.host.onEnd(skipped, s.opts);
  }

  // host: { goTab(key), onEnd(skipped, opts) }. opts.replay: started from
  // Preferences rather than by the first draft.
  function start(host, opts) {
    if (state) return false;
    build();
    state = { index: 0, host: host, opts: opts || {}, returnFocus: document.activeElement, target: null };
    show(0);
    return true;
  }

  global.PomonaTour = {
    start: start,
    isOpen: function () { return !!state; },
    STOPS: STOPS
  };
})(window);
