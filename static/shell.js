/*
  Persistent app shell (design_handoff_shell/README.md §4, §7, §8).

  Renders the tab bar (mobile) / left rail (desktop >=1024px), the docked
  ask bar, and a single scroll area that swaps between four tab panels
  without reloading the page (history.pushState + show/hide, not a real
  navigation).

  Step 1: Grocery and Kitchen embed the existing static pages unmodified
  via <iframe> — this is deliberate: it means their internals (filters,
  inline edit, cook steps, etc.) needed zero changes to move inside the
  shell. Week has no existing page to preserve, so it gets a plain
  placeholder until Step 4.

  Step 2: Today is real now — heading, tonight's dinner, chores, grocery
  summary, all built from existing read endpoints (plus two small new
  ones for chores — see design_handoff_shell/README.md's Step 2 note).
  No needs-you band yet (that's Step 5), so the heading always reads
  "You're clear" for now — it's still deriving from a real count, that
  count is just always 0 until the band exists.

  Step 3: chat lives in the ask sheet now, reachable from every screen via
  the docked ask bar. /api/chat returns `actions` (see app/main.py) — one
  per shell area a reply actually changed — and every action renders as a
  card under the assistant's bubble with a "View" that jumps straight to
  that tab (or, for household info that isn't on a tab yet, to /memory).
  static/index.html's chat is untouched and still works standalone, but
  nothing in the shell links to it as of this step — the sheet fully
  replaces it as the way to reach the assistant from the app.

  Step 4: Week is real now — the seven-day paper menu on mobile, a
  7-column x 3-row grid on desktop (>=1100px, its own breakpoint), backed
  by the new GET /api/week-menu endpoint (see app/tools.get_week_menu for
  the three-slot {title, meta, source} data model and its derivation
  judgment calls). See the "Week (Step 4)" section below for the UI-side
  judgment calls (day status, ribbon, empty-slot copy).

  Step 5: the needs-you band is real now, backed by two new endpoints
  (GET /api/needs-you, POST /api/needs-you/dinner — see
  app/tools.get_needs_you_items/resolve_needs_you_dinner for the two
  hardcoded rules this starts with). Today's H1/badge count and the Today
  tab's mobile-badge/rail-pill are all derived from that same list now,
  not hardcoded to 0. Also adds the shared toast (§6) used when a dinner
  decision resolves.

  Step 6 (final build-order step, §7): the desktop (>=1024px) Today layout
  is real now — same cards as mobile, rearranged into a CSS grid (dinner
  full-width on top, then needs-you / chores+grocery / Ask across a row)
  purely via shell.css's grid-template-areas, no duplicated markup. The
  Ask sheet and the desktop Ask column render the *same* conversation —
  see "Ask sheet vs. Ask column" below for how sendAskMessage/addAskMessage
  write into whichever of the two message-list surfaces currently exist,
  so resizing across the breakpoint never desyncs them. The docked ask bar
  is hidden at this breakpoint (nothing left for it to open).
*/
(function () {
  'use strict';

  var ICONS = {
    // Nav bar set, exact match to the InnToday.dc.html mockup (design-system
    // canvas, brand-canvas/InnToday.dc.html) per the "nav bar icons don't
    // match the design mockup" ticket — same drawing style, stroke weight,
    // and 24x24 grid as the mock, so don't restyle these independently of it.
    sunrise:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M4 15a8 8 0 0 1 16 0"/><path d="M2.5 19h19"/><path d="M12 3.5v2"/><path d="M5 7l1.5 1.5"/><path d="M19 7l-1.5 1.5"/></svg>',
    plate:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="3"/></svg>',
    pot:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 9.5h13V16a4.5 4.5 0 0 1-4.5 4.5h-4A4.5 4.5 0 0 1 5.5 16z"/><path d="M3.5 9.5h17"/><path d="M12 3.5v3"/></svg>',
    // Pomona (InnToday/InnMeals): the hero's flame tile, the prep tile's
    // clock, the grocery tile's bag, and the arrow that ends a primary
    // action. Line icons, 2px, round caps — the brand guide's one icon
    // style; they inherit currentColor so the same markup works on spruce
    // and on ivory.
    flame:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3c3.2 2.8 5 5.4 5 8.4a5 5 0 0 1-10 0c0-1.6.8-3 2-4.2 0 1.6.8 2.4 1.6 2.4 1 0 1.6-.9 1.6-2.4 0-1.4-.4-2.8-.2-4.2z"/><path d="M6 21h12"/></svg>',
    clock:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l3 1.6"/><path d="M9 2.5h6"/></svg>',
    bag:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 8.5h15l-1.3 10.7a2 2 0 0 1-2 1.8H7.8a2 2 0 0 1-2-1.8z"/><path d="M9.2 8.5V6.6a2.8 2.8 0 0 1 5.6 0v1.9"/></svg>',
    arrow:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h13"/><path d="M12.5 6l6 6-6 6"/></svg>',
    // The desktop week grid's "Why this?" toggle (InnMeals redesign) — same
    // small info-circle the mock draws next to it.
    info:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.5v.01"/></svg>'
  };

  // A leftovers night is a reheat, not a cook (Emily, 2026-09-04) — so its
  // one action is not "Start cooking" and not "Mark cooked". The wording
  // itself is still Emily's call; it lives here, in ONE place, used by both
  // the Cook screen's reheat card and Today's hero, so changing it is a
  // one-line change rather than a hunt.
  var REHEAT_ACTION_LABEL = 'Mark eaten';
  var REHEAT_UNDO_LABEL = 'Mark not eaten';

  // The beta is meals-only (Emily, 2026-09-08, option 1b on the Chores
  // ticket) — Chores hasn't been validated yet, so Today's "Your chores"
  // card is hidden rather than shown to every beta household. This is the
  // one flag that decides it: false means buildTodayPanel never renders
  // the chores card markup and never calls loadChores (so no
  // /api/chores/today request either). The chores backend, the
  // /chores-setup page, and loadChores/renderChores themselves are all
  // untouched — flipping this back to true is the whole reversal.
  var SHOW_CHORES_ON_TODAY = false;

  // The notifications bell and its feed left the app with the Today
  // redesign (Emily, 2026-09-08). Today is a timeline of moves now, and
  // everything time-bound the feed used to carry — a dinner to cook, a
  // thing to take out of the freezer, a shop to do — is a move on it. A
  // second inbox sitting beside that list is the exact thing the screen was
  // rebuilt to remove; the feed's remaining, non-time-bound items are
  // dropped for now rather than rehomed.
  //
  // Hidden behind one constant rather than deleted, the same way
  // SHOW_CHORES_ON_TODAY above is: /api/notifications and
  // /api/notifications/dismiss are untouched (the plan-week nudge still
  // uses the dismiss route), loadNotifications and the panel's own code are
  // intact, and flipping this back to true puts the button back in its old
  // slot beside the ask bar. While false, the bell is taken out of the
  // document and /api/notifications is never requested.
  var SHOW_NOTIF_BELL = false;

  // Cook-mode hands-free voice, hidden not deleted (Emily, 2026-09-08):
  // "Let's just drop the cook mode voice for now. Just hide it, and we can
  // rebuild it later." While false: no mic button renders in Cook, no
  // SpeechRecognition/speechSynthesis session is ever created, and no mic
  // permission prompt fires. Flip to true to bring it back.
  var COOK_VOICE_ENABLED = false;

  // The ask bar is shell chrome — one composer above the tab bar on all four
  // screens (NavBlueprint §7) — so its hint is the one thing about it that
  // can be per-screen. Today's names what Today is for now that the screen
  // is a timeline of moves; the others keep the line the bar has always
  // carried.
  var ASK_HINTS = {
    today: 'Ask me anything about today\u2026',
    week: 'Tweak this week with me\u2026',
    // Grocery's own line, because "Add something" on the list opens this
    // bar: the hint has to say what it takes.
    grocery: 'Add oat milk and lemons\u2026',
    _default: 'The more you tell me, the less you\u2019ll swap\u2026'
  };

  function setAskHintForTab(key) {
    var hint = ASK_HINTS[key] || ASK_HINTS._default;
    var docked = document.querySelector('#ask-bar .ask-placeholder');
    if (docked) docked.textContent = hint;
  }

  var TABS = [
    { key: 'today', path: '/', label: 'Today', railLabel: 'Today', icon: ICONS.sunrise, real: true },
    { key: 'week', path: '/week', label: 'Meals', railLabel: 'Meals', icon: ICONS.plate, week: true },
    // Stage 2 slice 2: Grocery is a real shell screen now, not an embedded
    // page. static/grocery.html still exists and still works standalone, but
    // nothing links to it — it is the fallback, the same way
    // static/grocery-legacy.html already was.
    { key: 'grocery', path: '/grocery', label: 'Grocery', railLabel: 'Grocery', icon: ICONS.bag, grocery: true },
    // Stage 2 slice 3: Kitchen is a real shell screen too, and with it the
    // last iframe tab comes out. static/kitchen.html still exists and still
    // works standalone but nothing links to it — the fallback, exactly the
    // treatment static/grocery.html and static/grocery-legacy.html already
    // have. Cooking is NOT here any more: it moved to the Meals tab's Cook
    // state (see COOK below and NavBlueprint's "Where cooking lives").
    { key: 'kitchen', path: '/kitchen', label: 'Kitchen', railLabel: 'Kitchen', icon: ICONS.pot, kitchen: true }
  ];

  // The Kitchen hub's entry tiles. The blueprint asks for these to open as
  // sheets over the hub rather than as full page navigations that leave the
  // app shell (tab bar and all) with only the browser's back button to
  // return — which is what /inventory and /memory were until this slice.
  //
  // Each sheet hosts the EXISTING page in an iframe. Rebuilding
  // inventory.html and memory.html natively is real work and is explicitly
  // not this slice; hosting them in a sheet is the least invasive thing that
  // still satisfies the rule that a screen never becomes a page with its own
  // chrome — the sheet's header is the way back, and the pages' own back
  // links hide themselves inside a frame (static/embedded-page.js).
  //
  // `src` is written as a plain /static/*.html literal on purpose:
  // tests/test_embedded_pages.py derives "which pages can end up in a frame"
  // by reading this file, and a computed or concatenated path would make
  // that derivation silently blind.
  // `hash` names the tab within the page, for the entries that are a tab of
  // a shared page rather than a page of their own. memory.html reads it on
  // load (openingTab) and exposes showKitchenTab() for the case where the
  // page is already open on a different tab.
  // memory/stores share one page and one sheet title on purpose (Loop Board
  // "I should be able to see all the onboarding information here"): the
  // title used to stay 'Stores' even after switching to another tab inside
  // the sheet, because it is set once at open time from whichever entry
  // point was tapped (see openKitchenSheet) and switching tabs inside the
  // iframe never told the sheet chrome to update it. Giving both entries
  // the same fixed title sidesteps that without needing the sheet to poll
  // or the iframe to call back out on every tab change.
  var KITCHEN_SHEETS = {
    memory: { title: 'What we know', src: '/static/memory.html', hash: 'people' },
    inventory: { title: 'Inventory', src: '/static/inventory.html' },
    stores: { title: 'What we know', src: '/static/memory.html', hash: 'stores' }
  };

  function currentTabKey() {
    var path = window.location.pathname.replace(/\/+$/, '') || '/';
    for (var i = 0; i < TABS.length; i++) {
      var tp = TABS[i].path === '/' ? '/' : TABS[i].path.replace(/\/+$/, '');
      if (path === tp) return TABS[i].key;
    }
    return 'today';
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  var scrollEl = document.getElementById('shell-scroll');
  var tabBarEl = document.getElementById('tab-bar');
  var railRowsEl = document.getElementById('rail-rows');
  var panels = {};

  TABS.forEach(function (tab) {
    var panel = document.createElement('div');
    panel.className = 'tab-panel';
    panel.id = 'panel-' + tab.key;

    // No tab is an embedded page any more. Grocery lost its iframe in
    // Stage 2 slice 2 and Kitchen in slice 3, so the lazy-src plumbing that
    // used to live here is gone with them — the only iframe left in the app
    // is the one inside a Kitchen entry sheet (see KITCHEN_SHEETS).
    if (tab.placeholder) {
      var box = document.createElement('div');
      box.className = 'tab-placeholder';
      box.innerHTML =
        '<div class="kicker">' + tab.placeholder.kicker + '</div>' +
        '<h1>' + tab.placeholder.title + '</h1>' +
        '<p>' + tab.placeholder.body + '</p>';
      panel.appendChild(box);
    }
    // tab.real (Today) is built lazily by buildTodayPanel() below.

    scrollEl.appendChild(panel);
    panels[tab.key] = panel;

    // Mobile tab bar button
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tab-btn';
    btn.dataset.tab = tab.key;
    btn.innerHTML =
      '<span class="tab-chip">' + tab.icon + '</span>' +
      '<span class="tab-label">' + tab.label + '</span>' +
      '<span class="tab-badge">1</span>';
    btn.addEventListener('click', function () { activateTab(tab.key, true); });
    tabBarEl.appendChild(btn);

    // Desktop rail row
    var row = document.createElement('button');
    row.type = 'button';
    row.className = 'rail-row';
    row.dataset.tab = tab.key;
    row.innerHTML =
      '<span class="rail-chip">' + tab.icon + '</span>' +
      '<span>' + tab.railLabel + '</span>' +
      '<span class="rail-badge">1</span>';
    row.addEventListener('click', function () { activateTab(tab.key, true); });
    railRowsEl.appendChild(row);
  });

  function activateTab(key, pushHistory, opts) {
    var tab = TABS.filter(function (t) { return t.key === key; })[0];
    if (!tab) return;

    // A Kitchen entry sheet belongs to the Kitchen tab, so it cannot outlive
    // a move off it. This is reached by the browser's Back button too
    // (popstate calls here), which is where it showed: Kitchen → open
    // Inventory → Back left the Inventory sheet sitting over the Grocery
    // panel, and dismissing it then refreshed a Kitchen hub nobody was
    // looking at. Callers that want a sheet open (the rail shortcuts,
    // followActionHref) activate the tab first and open it after, so this
    // does not fight them.
    closeKitchenSheet();

    // The shop-done handoff is page-view-only (see
    // groceryState.justFinishedTrip's declaration, further down this file
    // but already assigned by the time any tab click can reach here, same
    // as weekState below) — leaving Grocery for any other tab and coming
    // back later must not still show "that's the shopping done" from a
    // stop finished earlier in this same visit.
    if (key !== 'grocery') groceryState.justFinishedTrip = false;

    Object.keys(panels).forEach(function (k) {
      panels[k].classList.toggle('active', k === key);
    });
    document.querySelectorAll('.tab-btn').forEach(function (el) {
      el.classList.toggle('active', el.dataset.tab === key);
    });
    document.querySelectorAll('.rail-row').forEach(function (el) {
      el.classList.toggle('active', el.dataset.tab === key);
    });
    setAskHintForTab(key);

    var panel = panels[key];
    // Lazy-build Today's real content the first time it's shown.
    if (tab.real && !panel.dataset.built) {
      panel.dataset.built = '1';
      buildTodayPanel(panel);
    }
    // Lazy-build the Week menu the first time it's shown (Step 4).
    if (tab.week && !panel.dataset.built) {
      panel.dataset.built = '1';
      buildWeekPanel(panel);
    }
    // Lazy-build Grocery the first time it's shown (Stage 2 slice 2).
    if (tab.grocery && !panel.dataset.built) {
      panel.dataset.built = '1';
      buildGroceryPanel(panel);
    }
    // Lazy-build Kitchen the first time it's shown (Stage 2 slice 3).
    if (tab.kitchen && !panel.dataset.built) {
      panel.dataset.built = '1';
      buildKitchenPanel(panel);
    }

    // Meals has two states. `opts.mealsView` is how the two cook entry
    // points (Today's "Start cooking", Meals' own "Cook this") land on the
    // Cook state instead of Plan — it replaces the old forceEmbedSrc hack,
    // which reached cooking by re-pointing the KITCHEN tab's iframe at
    // cooker.html and so lit the wrong tab while you cooked. Both of them
    // also pass `mealsFocus`, so they land IN tonight's focused screen, not
    // just on the Cook overview — see setMealsView.
    if (tab.week && opts && opts.mealsView) setMealsView(opts.mealsView, opts.mealsFocus);

    // Grocery is four STEPS now (list/sort/trip/wrap — see goGroceryStep).
    // `opts.groScreen` is the old segment name the approve-week receipt's
    // "Take me to the list" (and its toast twin, and the ask sheet's "Plan
    // my stops" chip) still pass; groSetScreen maps it onto the step that
    // means the same thing. Cheap either way — it just flips groceryState
    // and re-renders, whether or not Grocery was already built above.
    if (tab.grocery && opts && opts.groScreen) groSetScreen(opts.groScreen);

    // A draft awaiting a decision shouldn't hide just because you left and
    // came back (Loop Board: "land on the review moment"). #shell-scroll is
    // one scroll region shared by every tab (see its declaration below), so
    // switching INTO Meals doesn't reset scroll position on its own — a
    // household that had scrolled down reading a day card before switching
    // away would return to that same scroll depth, with the review band
    // (now the first thing in the panel) sitting off-screen above them.
    // Only fires on an actual tab switch into an already-built Meals PLAN
    // view with a live draft — never on the quiet background refreshes
    // loadWeekMenu does elsewhere (settling an open slot, a chat edit),
    // which per the nav rules must never jump the screen under someone's
    // thumb, and never on a Cook-view entry (opts.mealsView === 'cook',
    // from Today's "Start cooking" or Meals' own "Cook this") — the review
    // band doesn't exist in #week-cook-view, so jumping scroll there
    // wouldn't reveal anything and isn't what this is for. weekState is
    // declared further down this file but already assigned by the time
    // any tab click can reach here.
    if (tab.week && (!opts || opts.mealsView !== 'cook') && panel.dataset.built && weekState.data &&
        weekState.data.weekly_plan_id && weekState.data.status !== 'approved' && scrollEl) {
      scrollEl.scrollTop = 0;
    }

    if (pushHistory && window.location.pathname.replace(/\/+$/, '') !== (tab.path === '/' ? '/' : tab.path.replace(/\/+$/, ''))) {
      window.history.pushState({ tab: key }, '', tab.path);
    }
  }

  // One listener decides what Back means. Meals' three steps and Grocery's
  // four push their own history entries at the same /week and /grocery paths
  // (pushMealsStepHistory / pushGroceryStepHistory), so the Android/browser
  // back gesture steps out one level there before it leaves the tab at all;
  // every other tab is unaffected.
  window.addEventListener('popstate', function (e) {
    activateTab(currentTabKey(), false);
    if (currentTabKey() === 'week') applyMealsStepFromHistory(e && e.state);
    if (currentTabKey() === 'grocery') applyGroceryStepFromHistory(e && e.state);
  });

  // ---------- Today ----------
  // README §4/§7: heading, needs-you band, tonight's dinner, chores,
  // grocery summary — same cards on every breakpoint, just rearranged.
  // Mobile stacks them in DOM order (next-up, needs-you, the rest).
  // Desktop lays the same DOM out as a CSS grid: the next-up card spans the
  // full width on its own row, then a 1.5fr/1fr/1fr row of needs-you + the
  // rest / chores / Ask — no JS-side breakpoint branching, `.today-body`'s
  // grid-template-areas (shell.css) does the rearranging. The Ask column
  // only exists (is only ever shown) at >=1024px — see "Ask sheet vs. Ask
  // column" below for how the same conversation renders into both surfaces
  // depending on which one exists at the moment.
  // ---------- Today: one timeline of moves ----------
  // Emily's approved Today design, 2026-09-08. The screen answers "what's
  // next for us?" with two blocks and nothing else: ONE compact spruce
  // "Next up" card carrying a single action, and "The rest of today" — a
  // plain list of every other move, each with a round tick.
  //
  // Both come from one fetch, /api/today/moves (app/tools/moves.py), which
  // ranks the day's cooks, reheats, fridge moves, prep and shopping against
  // each other. The ranking lives on the server precisely so this screen
  // never has to decide what matters, only how to say it — and so the rule
  // is testable, which a shell.js rule would not be.
  //
  // What this replaced, and where each piece went: the tall dinner hero and
  // its "Start cooking" (now the cook move, and the card only when it is
  // genuinely next), the read-only "Before bed" prep tile (now prep moves),
  // the defrost tile (now fridge moves, ticked instead of done/skipped),
  // the grocery-count tile (now the shop move, which only appears when
  // there is a cook close enough for it to matter) and the notifications
  // bell (see SHOW_NOTIF_BELL). The one thing deliberately kept beside them
  // is the open-dinner card in the needs-you band: when tonight's dinner is
  // an unanswered question, that decision IS what's next, so it takes the
  // card's place rather than sitting above a second one.
  async function buildTodayPanel(panel) {
    panel.innerHTML =
      '<div class="today-content">' +
        // The date as an eyebrow running into a hairline (InnToday), now
        // with the week's state at the far end of the same rule — one
        // glance says what day it is and whether there's a plan behind it.
        '<div class="today-heading">' +
          '<div class="today-datestrip">' +
            '<span class="today-date" id="today-date"></span>' +
            '<span class="today-hairline"></span>' +
            '<span class="today-weekstate" id="today-week-state" hidden></span>' +
          '</div>' +
          '<h1 class="today-greeting">Today</h1>' +
          '<div class="today-progress" id="today-progress"></div>' +
        '</div>' +
        // The offer to plan a week. Outside .today-body, not inside it:
        // .today-body is a named-area grid on desktop, and an area whose
        // only child is display:none still leaves its row's gap behind.
        '<div id="plan-week-nudge" class="today-area-nudge"></div>' +
        // The hero, and the only one on this screen — a direct child of
        // .today-content rather than of .today-body, because it bleeds the
        // full width of the panel while everything else sits inside the
        // 20px gutter, and a grid child cannot escape its parent's padding.
        '<div id="today-next-up" class="dinner-hero nextup-hero" hidden></div>' +
        '<div class="today-body">' +
          '<div id="needs-you-band" class="today-area-needsyou"></div>' +
          '<div id="today-rest" class="today-area-rest"></div>' +
          // SHOW_CHORES_ON_TODAY (2026-09-08): the beta is meals-only, so
          // this card is left out of the markup entirely while the flag
          // is false — not just hidden, so there's nothing for a stray
          // selector to find.
          (SHOW_CHORES_ON_TODAY ?
            '<div class="today-area-chores">' +
              '<div class="shell-card chores-card">' +
                '<div class="chores-header"><h2>Your chores</h2><span class="chores-count" id="chores-count"></span></div>' +
                '<div id="chores-list"></div>' +
                // Chores setup moved out of first-run onboarding onto its own
                // page (Emily, 2026-09-05, 20a) so a brand-new household's
                // first stop is the meal loop, not a chores questionnaire.
                // This is how it stays reachable — shown only for a household
                // that has never gone through it (see renderChores below).
                // Reuses .week-setup-link (the Meals tab's own "way into the
                // revisitable setup screen" link) rather than inventing a new
                // component for the same job — DESIGN_SYSTEM.md §9 Tier 1.
                '<a href="/chores-setup" class="week-setup-link" id="chores-setup-link" style="display:none">Want help with chores too? Set them up</a>' +
              '</div>' +
            '</div>'
          : '') +
          '<div class="today-area-ask shell-card ask-column" id="today-ask-column">' +
            '<div class="ask-messages" id="today-ask-messages"></div>' +
            '<div class="ask-chips" id="today-ask-chips"></div>' +
            '<form id="today-ask-composer" class="ask-composer-bar">' +
              '<textarea id="today-ask-input" class="ask-composer-input" rows="1" placeholder="Ask me anything about today&hellip;" autocomplete="off"></textarea>' +
              '<button type="button" id="today-ask-mic-btn" class="ask-composer-mic" aria-label="Dictate message" title="Dictate message">' +
                '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z"/><path d="M19 11a1 1 0 1 0-2 0 5 5 0 0 1-10 0 1 1 0 1 0-2 0 7 7 0 0 0 6 6.93V21H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-3.07A7 7 0 0 0 19 11z"/></svg>' +
              '</button>' +
              '<button type="submit" id="today-ask-send-btn" class="ask-composer-send" aria-label="Send">&uarr;</button>' +
            '</form>' +
          '</div>' +
        '</div>' +
      '</div>';

    panel.querySelector('#today-date').textContent = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' }).toUpperCase();

    setupAskColumn(panel);

    await Promise.all([
      loadPlanWeekNudge(panel),
      loadNeedsYou(panel),
      loadTodayMoves(panel),
      // SHOW_CHORES_ON_TODAY (2026-09-08): skip the call, not just the
      // render — no chores card means no reason to hit /api/chores/today.
      (SHOW_CHORES_ON_TODAY ? loadChores(panel) : Promise.resolve())
    ]);
  }

  // ---------- The offer to plan a week ----------
  // design_handoff_plan_the_week §2. Dismissible, and the dismissal is
  // scoped to the week itself, so "I won't ask again this week" is
  // literally true rather than approximately true.

  async function loadPlanWeekNudge(panel) {
    var wrap = panel.querySelector('#plan-week-nudge');
    if (!wrap) return;
    try {
      var res = await fetch('/api/week/plan-nudge');
      if (!res.ok) throw new Error('nudge lookup failed');
      var nudge = await res.json();
      renderPlanWeekNudge(wrap, nudge);
    } catch (err) {
      // An offer is the most skippable thing on this screen — if it can't
      // be fetched, show nothing rather than an error about a suggestion.
      console.warn('Plan-week nudge lookup failed:', err);
      wrap.innerHTML = '';
    }
  }

  function renderPlanWeekNudge(wrap, nudge) {
    if (!nudge || !nudge.show) { wrap.innerHTML = ''; return; }
    wrap.innerHTML =
      '<div class="shell-card plan-nudge-card">' +
        '<div class="plan-nudge-top">' +
          '<span class="plan-nudge-eyebrow">' +
            (nudge.is_current_week ? 'THIS WEEK' : 'NEXT WEEK') + ' &middot; WHENEVER SUITS YOU</span>' +
          '<button type="button" class="plan-nudge-dismiss" id="plan-nudge-dismiss">Not now</button>' +
        '</div>' +
        '<div class="plan-nudge-title">Shall I put ' + escapeHtml(nudge.week_label) + ' together for you?</div>' +
        '<div class="plan-nudge-body">Two rounds of questions from me — about five minutes — then I’ll ' +
          'draft the week and you tell me what to change. Nothing gets bought until you approve it.</div>' +
        '<button type="button" class="btn-gold plan-nudge-cta" id="plan-nudge-go">Let’s plan the week</button>' +
      '</div>';

    wrap.querySelector('#plan-nudge-go').addEventListener('click', function () {
      // nudge.day_count, not seven. The nudge's own headline already names
      // the real span ("Shall I put Sep 5–7 together for you?"), so
      // dropping it here asked the next screen about four days nobody had
      // been offered. Same defect as the Meals entry's, same payload.
      startPlanningWeek(nudge.week_start, nudge.day_count || 7);
    });
    wrap.querySelector('#plan-nudge-dismiss').addEventListener('click', async function () {
      // Say what dismissing means, and where the offer went — the entry
      // point on Meals is permanent, so nothing is actually lost.
      wrap.innerHTML =
        '<div class="shell-card plan-nudge-card plan-nudge-dismissed">' +
          '<div class="plan-nudge-body">Of course. It’ll be waiting for you under Meals — I won’t ask again this week.</div>' +
          '<button type="button" class="plan-nudge-link" id="plan-nudge-later">Plan the week →</button>' +
        '</div>';
      wrap.querySelector('#plan-nudge-later').addEventListener('click', function () {
        startPlanningWeek(nudge.week_start, nudge.day_count || 7);
      });
      try {
        await fetch('/api/notifications/dismiss', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: nudge.dismiss_key })
        });
      } catch (err) {
        console.warn('Dismissing the plan-week nudge failed:', err);
      }
    });
  }

  function startPlanningWeek(weekStart, dayCount) {
    // A full page rather than a tab — see /plan-week in app/main.py for
    // why. Leaving the shell entirely is the point: the flow has a
    // beginning and an end, and comes back to Meals when it's done.
    //
    // `days` rides in the URL alongside the start so the question screens
    // ask about the days actually being planned. It is omitted for a plain
    // seven, which keeps every link this app has ever produced (and any
    // bookmarked one) byte-identical to what it was.
    var url = '/plan-week?week=' + encodeURIComponent(weekStart);
    if (dayCount && dayCount !== 7) url += '&days=' + encodeURIComponent(dayCount);
    window.location.href = url;
  }

  // Display type has to survive real titles. A component-based household's
  // "dinner" is a concatenation of that day's components ("Herb-Roasted
  // Chicken with Roasted Root Vegetables with Hummus and Veggie Sticks"), and
  // day-based weeks carry the odd long recipe name too — either will run to
  // six or seven lines at the hero's 31px. Stepping the size down keeps the
  // whole name readable, which truncating it would not: what is for dinner is
  // the one thing this panel exists to answer.
  function dishSizeClass(title) {
    var n = String(title || '').length;
    if (n > 78) return ' hero-dish-xs';
    if (n > 42) return ' hero-dish-sm';
    return '';
  }

  function setTodayHeading(panel, count, isError) {
    // The needs-you count no longer has a line of its own on Today — the
    // line under the title is "N of M done", written by renderTodayMoves —
    // but it still drives the tab badge, which is the one place a count of
    // unanswered questions is worth carrying. A failed lookup badges zero
    // rather than badging a guess.
    setTodayBadge(isError ? 0 : count);
  }

  function setTodayBadge(count) {
    var tabBtn = document.querySelector('.tab-btn[data-tab="today"]');
    var railRow = document.querySelector('.rail-row[data-tab="today"]');
    [[tabBtn, '.tab-badge'], [railRow, '.rail-badge']].forEach(function (pair) {
      var el = pair[0];
      if (!el) return;
      el.classList.toggle('has-badge', count > 0);
      var badge = el.querySelector(pair[1]);
      if (badge) badge.textContent = String(count);
    });
  }

  // ---------- Needs-you band (Step 5, README §4/§6) ----------
  // "Start with two hardcoded rules" per §9's build order: an undecided
  // dinner within 48h (with up to two quick-recipe suggestions to pick
  // from inline) and a shop run needed before an upcoming meal.
  //
  // The shop-run card is no longer drawn here (2026-09-08). It said the
  // same thing as Today's own shop move — "the list still has things on it
  // and there's a cook coming" — and two cards making one point, one of
  // them ranked against everything else and one of them not, is exactly
  // the pile the redesign took apart. The rule is still in
  // /api/needs-you (other surfaces read it); Today just renders the move
  // instead, so its localStorage "Later" snooze went with the card.
  async function loadNeedsYou(panel) {
    try {
      var res = await fetch('/api/needs-you');
      if (!res.ok) throw new Error('needs-you lookup failed');
      var data = await res.json();
      renderNeedsYou(panel, data.items || []);
    } catch (err) {
      console.warn('Needs-you lookup failed:', err);
      setTodayHeading(panel, 0, true);
      panel.querySelector('#needs-you-band').innerHTML = '';
    }
  }

  function needsYouCardHtml(item) {
    if (item.type === 'dinner_decision') {
      return (
        '<div class="shell-card needs-you-card urgency-' + item.urgency + '" data-card-type="dinner_decision">' +
          '<div class="ny-kicker">' + escapeHtml(item.kicker) + '</div>' +
          '<div class="ny-title">' + escapeHtml(item.title) + '</div>' +
          '<div class="ny-options">' +
            item.options.map(function (opt, i) {
              return (
                '<div class="ny-option" data-date="' + escapeHtml(item.date) + '" data-meal="' + escapeHtml(opt.meal) + '" data-index="' + i + '">' +
                  '<span class="ny-option-dish">' + escapeHtml(opt.meal) + (opt.minutes ? ' &middot; ' + opt.minutes + ' min' : '') + '</span>' +
                  '<span class="ny-option-pick">Pick</span>' +
                '</div>'
              );
            }).join('') +
          '</div>' +
        '</div>'
      );
    }
    if (item.type === 'dinner_open') {
      // An open slot the app already handed back on the Plan screen (see
      // openSlotCardHtml) — same shape of card, surfaced here too because
      // that's exactly what needs-you is for. Resolves through the same
      // path the Plan screen's own open-slot cards use (resolveOpenSlot /
      // POST /api/week/{week_start}/slot), not the dinner_decision path
      // above — that one only plans a brand-new slot; this one is
      // replacing an existing open one.
      var hasOptions = item.options && item.options.length;
      return (
        '<div class="shell-card needs-you-card urgency-' + item.urgency + '" data-card-type="dinner_open">' +
          '<div class="ny-kicker">' + escapeHtml(item.kicker) + '</div>' +
          '<div class="ny-title">' + escapeHtml(item.title) + '</div>' +
          (item.body ? '<div class="ny-summary">' + escapeHtml(item.body) + '</div>' : '') +
          (hasOptions
            ? '<div class="ny-options">' +
                item.options.map(function (opt, i) {
                  return (
                    '<div class="ny-option" data-date="' + escapeHtml(item.date) + '" ' +
                      'data-week-start="' + escapeHtml(item.week_start || '') + '" ' +
                      'data-choice="' + escapeHtml(opt.label) + '" data-index="' + i + '">' +
                      '<span class="ny-option-dish">' + escapeHtml(opt.label) + (opt.meta ? ' &middot; ' + escapeHtml(opt.meta) : '') + '</span>' +
                      '<span class="ny-option-pick">Pick</span>' +
                    '</div>'
                  );
                }).join('') +
              '</div>'
            : '<button type="button" class="btn-sand ny-open-talk" data-date="' + escapeHtml(item.date) + '">Tell me what you’d like instead</button>') +
        '</div>'
      );
    }
    return '';
  }

  function renderNeedsYou(panel, items) {
    // Only the dinner decisions reach this band on Today now — see the
    // note above needsYouCardHtml. Everything else /api/needs-you returns
    // is either a move on the timeline or nothing this screen shows.
    var visible = items.filter(function (it) {
      return it.type === 'dinner_open' || it.type === 'dinner_decision';
    });
    setTodayHeading(panel, visible.length);

    var band = panel.querySelector('#needs-you-band');
    band.innerHTML = visible.map(needsYouCardHtml).join('');

    // The one card allowed to stand in for the "Next up" card (Emily,
    // 2026-09-08): when TONIGHT's dinner is still an open question, that
    // decision IS what's next, so the timeline steps aside rather than
    // stacking a second card on top of it. Recorded here, acted on in
    // renderTodayMoves — the two loads race on first build, so whichever
    // lands second re-renders with the answer.
    //
    // Scoped to today's date on purpose: this band also carries "Tomorrow
    // needs a dinner", which is a question about a different day and has no
    // business hiding what to do in the next four hours.
    var todayStr = todayLocalStr();
    panel._openDinnerCard = visible.some(function (it) {
      return (it.type === 'dinner_open' || it.type === 'dinner_decision') && it.date === todayStr;
    });
    if (panel._moves) renderTodayMoves(panel, panel._moves);

    band.querySelectorAll('[data-card-type="dinner_decision"] .ny-option').forEach(function (row) {
      row.addEventListener('click', function () {
        resolveDinnerDecision(panel, row.dataset.date, row.dataset.meal, row.closest('.needs-you-card'));
      });
    });
    band.querySelectorAll('[data-card-type="dinner_open"] .ny-option').forEach(function (row) {
      row.addEventListener('click', function () {
        resolveOpenDinner(panel, row.dataset.weekStart, row.dataset.date, row.dataset.choice, row.closest('.needs-you-card'));
      });
    });
    band.querySelectorAll('[data-card-type="dinner_open"] .ny-open-talk').forEach(function (btn) {
      btn.addEventListener('click', function () {
        openAskSheet('For ' + dayName(btn.dataset.date, { weekday: 'long' }) + '’s dinner, I’d like ');
      });
    });
  }

  function dismissNeedsYouCard(panel, cardEl, remainingItems) {
    if (cardEl) {
      cardEl.classList.add('pop-out');
      setTimeout(function () { renderNeedsYou(panel, remainingItems); }, 180);
    } else {
      renderNeedsYou(panel, remainingItems);
    }
  }

  async function resolveDinnerDecision(panel, mealDate, meal, cardEl) {
    // Ask before touching the grocery list. Planning a meal used to add its
    // ingredients silently; the household rule now is that nothing reaches
    // the list without an explicit yes, and a card tap has no conversation
    // in which to ask — so the card asks for itself.
    var addIngredients = await askAboutIngredients(meal);
    // "core loop handoffs, slice 2" item F (Emily, 2026-09-05): "Never
    // mind" used to close the dialog with no feedback at all — say
    // plainly that nothing changed, per the calm-in-trouble/reassurance
    // rule (DESIGN_SYSTEM.md §8): the answer is "nothing lost," said once.
    if (addIngredients === null) { showToast('Left as it was.'); return; }
    try {
      var res = await fetch('/api/needs-you/dinner', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: mealDate, meal: meal, add_ingredients: addIngredients })
      });
      if (!res.ok) throw new Error('dinner resolve failed');
      var data = await res.json();
      showToast(dinnerPlannedToast(meal, data));
      dismissNeedsYouCard(panel, cardEl, data.items || []);
      // Today's timeline and (if it's today) the Week menu both just
      // changed — refresh what's already on screen rather than requiring
      // a manual reload, same "cascades must be visible" spirit as §6.
      loadTodayMoves(panel);
    } catch (err) {
      console.warn('Dinner resolve failed:', err);
      alert('Could not save that pick right now — try again in a moment.');
    }
  }

  // Settles an OPEN dinner slot picked from the needs-you band — the same
  // endpoint the Plan screen's own open-slot cards use (resolveOpenSlot),
  // since that one replaces the existing open row instead of inserting a
  // second entry alongside it the way /api/needs-you/dinner would.
  async function resolveOpenDinner(panel, weekStart, mealDate, choice, cardEl) {
    if (!weekStart) {
      alert('Could not save that pick right now — try again in a moment.');
      return;
    }
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(weekStart) + '/slot', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: mealDate, slot: 'dinner', choice: choice })
      });
      if (!res.ok) throw new Error('open dinner resolve failed');
      await res.json();
      showToast(choice + ' is on the plan.');
      await loadNeedsYou(panel);
      loadTodayMoves(panel);
    } catch (err) {
      console.warn('Open dinner resolve failed:', err);
      alert('Could not save that pick right now — try again in a moment.');
    }
  }

  // ---------- "Add the ingredients?" confirm ----------
  // Resolves to true (add them), false (just plan it), or null (cancelled,
  // plan nothing). Same scrim/dialog treatment as the reset dialog, and the
  // same "only one open at a time" rule as the sheets.
  var dinnerConfirmScrim = document.getElementById('dinner-confirm-scrim');
  var dinnerConfirmDialog = document.getElementById('dinner-confirm-dialog');
  var dinnerConfirmResolve = null;

  function closeDinnerConfirm(answer) {
    if (!dinnerConfirmScrim) return;
    dinnerConfirmScrim.hidden = true;
    dinnerConfirmDialog.hidden = true;
    var resolve = dinnerConfirmResolve;
    dinnerConfirmResolve = null;
    if (resolve) resolve(answer);
  }

  function askAboutIngredients(meal) {
    // No dialog in the document (an older cached shell.html) — fail closed
    // and add nothing rather than silently writing to the grocery list.
    if (!dinnerConfirmDialog) return Promise.resolve(false);
    closeAskSheet();
    closeWeekSheet();
    document.getElementById('dinner-confirm-meal').textContent =
      meal + ' — want its ingredients on your grocery list?';
    dinnerConfirmScrim.hidden = false;
    dinnerConfirmDialog.hidden = false;
    document.getElementById('dinner-confirm-add').focus();
    return new Promise(function (resolve) { dinnerConfirmResolve = resolve; });
  }

  function dinnerPlannedToast(meal, data) {
    var added = (data && data.groceries_added) || [];
    if (!added.length) return meal + ' is on the plan.';
    return meal + ' is on the plan. ' + added.length +
      (added.length === 1 ? ' ingredient' : ' ingredients') + ' added to the list.';
  }

  if (dinnerConfirmScrim) {
    dinnerConfirmScrim.addEventListener('click', function () { closeDinnerConfirm(null); });
    document.getElementById('dinner-confirm-cancel').addEventListener('click', function () { closeDinnerConfirm(null); });
    document.getElementById('dinner-confirm-skip').addEventListener('click', function () { closeDinnerConfirm(false); });
    document.getElementById('dinner-confirm-add').addEventListener('click', function () { closeDinnerConfirm(true); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !dinnerConfirmDialog.hidden) closeDinnerConfirm(null);
    });
  }

  // ---------- "Who's approving?" picker ----------
  // Resolves to an adult's name, or null if they backed out. Same
  // scrim/dialog treatment as the reset and ingredients confirms.
  var approveWhoScrim = document.getElementById('approve-who-scrim');
  var approveWhoDialog = document.getElementById('approve-who-dialog');
  var approveWhoResolve = null;

  function closeApproveWho(answer) {
    if (!approveWhoScrim) return;
    approveWhoScrim.hidden = true;
    approveWhoDialog.hidden = true;
    var resolve = approveWhoResolve;
    approveWhoResolve = null;
    if (resolve) resolve(answer);
  }

  function askWhoIsApproving(people) {
    // No dialog in the document (an older cached shell.html) — approve
    // without a name rather than blocking the action on a missing picker.
    // The receipt drops the name; it never invents one.
    if (!approveWhoDialog) return Promise.resolve('');
    closeAskSheet();
    closeWeekSheet();
    var optionsEl = document.getElementById('approve-who-options');
    optionsEl.innerHTML = people.map(function (name) {
      return '<button type="button" class="btn-outline-plum approve-who-option" data-name="' +
        escapeHtml(name) + '">' + escapeHtml(name) + '</button>';
    }).join('');
    optionsEl.querySelectorAll('.approve-who-option').forEach(function (btn) {
      btn.addEventListener('click', function () { closeApproveWho(btn.dataset.name); });
    });
    approveWhoScrim.hidden = false;
    approveWhoDialog.hidden = false;
    var first = optionsEl.querySelector('.approve-who-option');
    if (first) first.focus();
    return new Promise(function (resolve) { approveWhoResolve = resolve; });
  }

  if (approveWhoScrim) {
    approveWhoScrim.addEventListener('click', function () { closeApproveWho(null); });
    document.getElementById('approve-who-cancel').addEventListener('click', function () { closeApproveWho(null); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !approveWhoDialog.hidden) closeApproveWho(null);
    });
  }

  // ---------- Toast (§6: "used for resolutions and adds only, never errors") ----------
  var toastEl = document.getElementById('toast');
  var toastTimer = null;
  // `action` (optional) is {label, onClick} and turns the toast into an
  // undoable one — added for the pre-shop check's "dropped it" message, which
  // takes an item off the list and has to offer a way back. A toast carrying
  // an action stays up longer, because it is now something to read AND decide
  // rather than something to notice.
  function showToast(message, action, holdMs) {
    // holdMs: for the rare toast that is a sentence rather than a
    // confirmation — an allergy warning after approval — 2.2 seconds is not
    // long enough to read one.
    if (!toastEl) return;
    toastEl.textContent = message;
    if (action && action.label) {
      var actionBtn = document.createElement('button');
      actionBtn.type = 'button';
      actionBtn.className = 'toast-action';
      actionBtn.textContent = action.label;
      actionBtn.addEventListener('click', function () {
        toastEl.hidden = true;
        if (toastTimer) clearTimeout(toastTimer);
        if (action.onClick) action.onClick();
      });
      toastEl.appendChild(actionBtn);
    }
    toastEl.hidden = false;
    toastEl.classList.remove('pop-in');
    void toastEl.offsetWidth; // restart the animation if a toast is already showing
    toastEl.classList.add('pop-in');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.hidden = true; }, holdMs || (action ? 6000 : 2200));
  }

  // ---------- The moves themselves ----------
  // Shapes, ticks and actions for /api/today/moves. Everything below reads
  // the payload the server already ranked; nothing here re-derives it.

  // 20px of visual inside a 44px tap target (DESIGN_SYSTEM.md rule 6).
  var TICK_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 13l4 4L19 7"/></svg>';

  function moveTickHtml(move) {
    // Not every move has a tick behind it — a shop move's "done" dispatch is
    // a no-op (moves.set_move_done's own `kind == "shop"` branch), so
    // ticking it used to fill the circle in and have it silently snap back,
    // with a toast that lied about it. `tickable` (from moves.py) says
    // whether the done dispatch actually flips anything; when it doesn't,
    // render an empty same-size spacer so the row's height (and the tick
    // column other rows share) doesn't jump around.
    if (!move.tickable) return '<span class="tick tick-empty" aria-hidden="true"></span>';
    return '<button type="button" class="tick' + (move.done ? ' is-done' : '') + '" ' +
      'data-move-tick="' + escapeHtml(move.id) + '" ' +
      'aria-pressed="' + (move.done ? 'true' : 'false') + '" ' +
      'aria-label="' + (move.done ? 'Put it back on the list' : 'Tick it off') + '">' +
      '<span class="tick-box">' + TICK_ICON + '</span>' +
    '</button>';
  }

  function nextUpCardHtml(move) {
    var chips = move.chips || [];
    return '<div class="hero-top">' +
        '<span class="hero-eyebrow">NEXT UP</span>' +
        '<span class="hero-rule"></span>' +
        (move.time_label ? '<span class="nextup-when">' + escapeHtml(move.time_label) + '</span>' : '') +
      '</div>' +
      '<div class="hero-dish nextup-dish' + dishSizeClass(move.title) + '">' + escapeHtml(move.title) + '</div>' +
      // The one Newsreader italic line on the screen (theme.css: "two lines
      // and it becomes a serif brand") — the move's own reason, never copy
      // written for the slot.
      (move.reason ? '<div class="hero-accent">' + escapeHtml(move.reason) + '</div>' : '') +
      (chips.length
        ? '<div class="hero-chips">' + chips.map(function (c) {
            return '<span class="hero-chip">' + escapeHtml(c) + '</span>';
          }).join('') + '</div>'
        : '') +
      '<div class="nextup-foot">' +
        '<button type="button" class="hero-action" data-move-action="' + escapeHtml(move.id) + '">' +
          '<span>' + escapeHtml((move.action && move.action.label) || 'Do it') + '</span>' + ICONS.arrow +
        '</button>' +
        moveTickHtml(move) +
      '</div>';
  }

  function moveRowHtml(move) {
    // A done row has nothing left to open, so its text stops being a
    // button — the tick is the only control on it, and it undoes.
    var text =
      '<span class="rest-row-title">' + escapeHtml(move.title) + '</span>' +
      (move.detail ? '<span class="rest-row-detail">' + escapeHtml(move.detail) + '</span>' : '');
    return '<div class="rest-row' + (move.done ? ' is-done' : '') + '">' +
      (move.done
        ? '<span class="rest-row-text">' + text + '</span>'
        : '<button type="button" class="rest-row-text rest-row-open" data-move-action="' + escapeHtml(move.id) + '">' + text + '</button>') +
      moveTickHtml(move) +
    '</div>';
  }

  function tomorrowCardHtml(move) {
    return '<div class="shell-card tomorrow-card">' +
      '<div class="tomorrow-eyebrow">TOMORROW</div>' +
      '<div class="tomorrow-lead">That&rsquo;s today handled. Tomorrow starts with</div>' +
      '<div class="tomorrow-title">' + escapeHtml(move.title) + '</div>' +
      (move.detail ? '<div class="tomorrow-detail">' + escapeHtml(move.detail) + '</div>' : '') +
    '</div>';
  }

  var WEEK_STATE_LABELS = { set: 'WEEK SET', draft: 'DRAFT', none: 'NOTHING PLANNED' };

  function renderTodayMoves(panel, data) {
    if (!data) return;
    panel._moves = data;
    var moves = data.moves || [];

    var badge = panel.querySelector('#today-week-state');
    if (badge) {
      var label = WEEK_STATE_LABELS[data.week_state || 'none'];
      badge.textContent = label || '';
      badge.hidden = !label;
      badge.className = 'today-weekstate is-' + (data.week_state || 'none');
    }

    var progress = panel.querySelector('#today-progress');
    if (progress) {
      progress.textContent = moves.length
        ? (data.done || 0) + ' of ' + moves.length + ' done'
        : 'Nothing planned for today yet';
    }

    // The one exception to "the card is whatever the server ranked first"
    // (Emily, 2026-09-08): an unanswered dinner is itself the decision, and
    // its needs-you card is already on screen — a second card above it
    // would be two answers to the same question.
    var featured = null;
    if (!panel._openDinnerCard) {
      featured = moves.filter(function (m) { return m.id === data.featured; })[0] || null;
    }

    var nextUp = panel.querySelector('#today-next-up');
    if (nextUp) {
      nextUp.hidden = !featured;
      nextUp.innerHTML = featured ? nextUpCardHtml(featured) : '';
    }

    var rest = moves.filter(function (m) { return !featured || m.id !== featured.id; });
    var pending = rest.filter(function (m) { return !m.done; });
    var settled = rest.filter(function (m) { return m.done; });

    var restEl = panel.querySelector('#today-rest');
    if (!restEl) return;
    var html = '';
    if (pending.length || settled.length) {
      html += '<div class="shell-card rest-card">' +
        (pending.length
          ? '<h2 class="rest-title">The rest of today</h2>' +
            '<div class="rest-list">' + pending.map(moveRowHtml).join('') + '</div>'
          : '') +
        (settled.length
          ? '<div class="rest-done">' +
              '<div class="rest-done-head">Done today</div>' +
              '<div class="rest-list">' + settled.map(moveRowHtml).join('') + '</div>' +
            '</div>'
          : '') +
      '</div>';
    }
    // Nothing left to do today. Say so, and — when there is one — name
    // tomorrow's first move rather than leaving a blank screen.
    if (!featured && !pending.length && !panel._openDinnerCard) {
      html += data.tomorrow
        ? tomorrowCardHtml(data.tomorrow)
        : '<div class="shell-card today-empty">' +
            (settled.length ? 'That&rsquo;s everything for today.' : 'Nothing on your list today.') +
          '</div>';
    }
    restEl.innerHTML = html;

    panel.querySelectorAll('[data-move-tick]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var id = btn.getAttribute('data-move-tick');
        var m = (panel._moves.moves || []).filter(function (x) { return x.id === id; })[0];
        if (m) toggleTodayMove(panel, id, !m.done);
      });
    });
    panel.querySelectorAll('[data-move-action]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        runTodayMoveAction(panel, btn.getAttribute('data-move-action'));
      });
    });
  }

  function renderTodayMovesError(panel) {
    var nextUp = panel.querySelector('#today-next-up');
    if (nextUp) { nextUp.hidden = true; nextUp.innerHTML = ''; }
    var progress = panel.querySelector('#today-progress');
    // "Nothing to do" is a real answer from a real count; a failed lookup
    // is not that, and saying it anyway would tell someone the day is clear
    // when the truth is only that the app couldn't check.
    if (progress) progress.textContent = 'Couldn’t check just now — pull to refresh.';
    var restEl = panel.querySelector('#today-rest');
    if (restEl) restEl.innerHTML = '';
  }

  async function loadTodayMoves(panel) {
    try {
      var res = await fetch('/api/today/moves');
      if (!res.ok) throw new Error('today moves lookup failed');
      renderTodayMoves(panel, await res.json());
    } catch (err) {
      console.warn('Today lookup failed:', err);
      renderTodayMovesError(panel);
    }
  }

  // Every other surface that changes something Today shows calls this —
  // see refreshStaleTabsFromActions and DESIGN_SYSTEM.md §6's refresh
  // policy ("a panel that's built once and never told to refresh goes
  // stale silently").
  function refreshTodayMoves() {
    if (panels.today && panels.today.dataset.built) loadTodayMoves(panels.today);
  }

  async function toggleTodayMove(panel, moveId, done) {
    var data = panel._moves;
    var move = ((data && data.moves) || []).filter(function (m) { return m.id === moveId; })[0];
    if (!move) return;
    var was = move.done;
    // Optimistic: the row drops into (or climbs out of) "Done today" on the
    // tap, before the server confirms — §6, "the common case never waits".
    move.done = done;
    data.done = (data.moves || []).filter(function (m) { return m.done; }).length;
    renderTodayMoves(panel, data);
    try {
      var res = await fetch('/api/today/moves/' + encodeURIComponent(moveId) + '/done', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ done: done })
      });
      if (!res.ok) throw new Error('move check-off failed');
      var fresh = await res.json();
      renderTodayMoves(panel, fresh);
      // Only claim it worked if the server's own re-derived move agrees —
      // a shop move (or anything else non-tickable) dispatches to nothing,
      // so its `done` never actually moves, and the toast should not say it
      // did. See moves.py's `tickable` note and moveTickHtml above.
      var updated = ((fresh && fresh.moves) || []).filter(function (m) { return m.id === moveId; })[0];
      if (updated && updated.done === done) {
        showToast(done ? 'Ticked off.' : 'Back on the list.');
      }
      // The same rows are the Cook screen's check-offs — keep the two from
      // showing different answers to the same question.
      refreshCookView();
    } catch (err) {
      console.warn('Could not save that tick:', err);
      move.done = was;
      data.done = (data.moves || []).filter(function (m) { return m.done; }).length;
      renderTodayMoves(panel, data);
      showToast('That didn’t save — try again.');
    }
  }

  function runTodayMoveAction(panel, moveId) {
    var move = ((panel._moves && panel._moves.moves) || []).filter(function (m) { return m.id === moveId; })[0];
    if (!move) return;
    var target = (move.action && move.action.target) || {};
    // A move whose action IS the tick (a reheat's "Mark eaten", a fridge
    // move's "Done") does the thing here rather than navigating somewhere
    // that would have nothing on it.
    if (target.kind === 'check_meal' || target.kind === 'check_prep') {
      return toggleTodayMove(panel, move.id, !move.done);
    }
    if (target.tab === 'week') {
      return activateTab('week', true, {
        mealsView: target.mealsView || 'cook',
        mealsFocus: target.mealsFocus || true
      });
    }
    if (target.tab) return activateTab(target.tab, true);
  }

  async function loadChores(panel) {
    var listEl = panel.querySelector('#chores-list');
    var countEl = panel.querySelector('#chores-count');
    try {
      var res = await fetch('/api/chores/today');
      if (!res.ok) throw new Error('chores lookup failed');
      var data = await res.json();
      renderChores(panel, data.chores || [], !!data.chores_set_up);
    } catch (err) {
      console.warn('Chores lookup failed:', err);
      listEl.innerHTML = '<div class="empty-row">Couldn\'t load chores right now.</div>';
      countEl.textContent = '';
    }
  }

  function renderChores(panel, chores, choresSetUp) {
    var listEl = panel.querySelector('#chores-list');
    var countEl = panel.querySelector('#chores-count');
    var setupLink = panel.querySelector('#chores-setup-link');
    // Only offered to a household that's never been through chores setup —
    // once they have (a profile saved, or any chore exists), there's
    // nothing left to "set up", whether or not one happens to be due today.
    // choresSetUp is omitted by toggleChore's re-renders (a checkbox tap
    // doesn't change setup status), so the link is left exactly as
    // loadChores last set it rather than guessed at here.
    if (setupLink && choresSetUp !== undefined) {
      setupLink.style.display = choresSetUp ? 'none' : 'block';
    }
    var done = chores.filter(function (c) { return c.status === 'done'; }).length;
    countEl.textContent = chores.length ? (done + ' of ' + chores.length) : '';
    countEl.className = 'chores-count' + (chores.length && done === chores.length ? ' all-done' : '');

    if (!chores.length) {
      listEl.innerHTML = '<div class="empty-row">Nothing due today.</div>';
      return;
    }

    listEl.innerHTML = chores.map(function (c) {
      var isDone = c.status === 'done';
      return (
        '<div class="chore-row' + (isDone ? ' done' : '') + '" data-id="' + c.id + '">' +
          '<span class="chore-checkbox" role="checkbox" aria-checked="' + isDone + '" tabindex="0">' +
            // stroke follows the checkbox's own colour rather than being
            // hardcoded white: ivory on a light accent is the one thing
            // the palette forbids outright, and this tick was the
            // instance the brand sweep missed (1.87:1).
            (isDone ? '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>' : '') +
          '</span>' +
          '<span class="chore-name">' + escapeHtml(c.chore) + '</span>' +
        '</div>'
      );
    }).join('');

    listEl.querySelectorAll('.chore-row').forEach(function (row) {
      var toggle = function () { toggleChore(panel, row, chores); };
      row.querySelector('.chore-checkbox').addEventListener('click', toggle);
      row.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
    });
  }

  async function toggleChore(panel, row, chores) {
    var id = Number(row.dataset.id);
    var chore = chores.filter(function (c) { return c.id === id; })[0];
    if (!chore) return;
    var prevStatus = chore.status;
    var nextStatus = prevStatus === 'done' ? 'pending' : 'done';

    // Optimistic: flip immediately, roll back on failure.
    chore.status = nextStatus;
    renderChores(panel, chores);

    try {
      var res = await fetch('/api/chores/' + id + '/status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: nextStatus })
      });
      if (!res.ok) throw new Error('status update failed');
    } catch (err) {
      console.warn('Chore toggle failed, rolling back:', err);
      chore.status = prevStatus;
      renderChores(panel, chores);
    }
  }

  // ==========================================================================
  // Grocery — four STEPS of one tab (Emily's approved design, 2026-09-08)
  // ==========================================================================
  // The screen answers "what do we need, and where?" as steps, not segments:
  //
  //   LIST     the root: one card per store, the needed things under it, a
  //            "N TO SORT" badge when anything has no store yet, and the
  //            screen's one apricot action — "Start the trip".
  //   SORT     one unsorted item at a time: its name, its quantity, and the
  //            store pills (plus Any / Have it / Somewhere else). Exists only
  //            while something is unsorted.
  //   TRIP     one stop at a time: this store's things with a tick each, the
  //            trolley collapsed underneath, and "Done at Costco → Metro".
  //   WRAP UP  what didn't make it into the cart, the trip's own count, and
  //            "Finish the trip".
  //
  // They are STATES of this tab, never routes — /grocery throughout, exactly
  // as Meals is /week in all three of its steps (NavBlueprint: "never a new
  // page with its own header"). goGroceryStep pushes its own history entry so
  // the browser's back gesture steps out one level; a refresh lands on LIST,
  // because groceryState.step starts there and nothing restores it.
  //
  // What the three-segment version had and this doesn't, all deliberate and
  // all still reachable through the ask bar, which is what "Add something"
  // now opens: the per-row ⋯ menu (edit quantity/category/store, Remove),
  // the To buy / Plan stops / Review segmented control, the spruce trip hero,
  // and Review's own flag cards (missing quantity, no store, possible
  // duplicate + Merge). Review's CONFIRMATION half — "Already sorted this
  // week", with its two undos — is kept and is now part of WRAP UP.
  //
  // Same /api/grocery-list* endpoints as before, same request bodies, same
  // statuses (needed / in_cart / purchased / excluded). No new routes.

  var GRO_CATEGORY_LABELS = {
    produce: 'Produce', dairy: 'Dairy', 'meat/seafood': 'Meat / seafood',
    pantry: 'Pantry', frozen: 'Frozen', other: 'Other'
  };
  // Aisle spine colours. These are `var()` references, not literals: a custom
  // property DOES cascade into an inline style attribute, so emitting
  // `style="background: var(--apricot)"` resolves per theme exactly like a
  // stylesheet rule would. Getting them onto tokens is what makes the spines
  // follow dark mode — #4F6B5B and #B23A22 on the dark ground were 1.5:1 and
  // 2.8:1.
  var GRO_AISLE_COLORS = {
    produce: 'var(--celadon)', dairy: 'var(--apricot)',
    'meat/seafood': 'var(--urgent)', pantry: 'var(--celadon-label)',
    frozen: 'var(--urgent)', other: 'var(--ink-inactive)'
  };
  // Store identity colours. Every entry is a LIGHT accent, because the avatar
  // carries spruce ink (--on-accent-ink) and RULE ONE has no exceptions.
  // These stay LITERALS on purpose, unlike the aisle spines above: they are
  // arbitrary identity colours rather than semantic roles, and a light accent
  // fill carrying dark ink is correct on either ground. Measured against
  // --on-accent-ink in both modes, the worst pair is 5.40:1 (light) / 6.32:1
  // (dark). Pointing them at tokens would be wrong — --celadon-edge and
  // --sand-deep both go DARK in dark mode, which would put dark ink on a
  // dark fill, the exact failure the comment above is about.
  var GRO_STORE_PALETTE = ['#E0915C', '#A9C4B0', '#F2B98E', '#C7DACD', '#E6D9C4', '#EFD3A9'];

  var GRO_ICONS = {
    refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11.5A8 8 0 1 0 18.4 17"/><path d="M20 5.5V11h-5.5"/></svg>',
    mic: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z"/><path d="M19 11a1 1 0 1 0-2 0 5 5 0 0 1-10 0 1 1 0 1 0-2 0 7 7 0 0 0 6 6.93V21H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-3.07A7 7 0 0 0 19 11z"/></svg>',
    chevDown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9.5l6 6 6-6"/></svg>',
    chevRight: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 6l6 6-6 6"/></svg>',
    tick: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7"/></svg>',
    basket: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 9.5h14V19a1.8 1.8 0 0 1-1.8 1.8H6.8A1.8 1.8 0 0 1 5 19z"/><path d="M3.5 5.5h17v4h-17z"/><path d="M12 9.5v11"/></svg>'
  };

  // How many things a store card shows before "+ N more".
  var GRO_CARD_PEEK = 4;

  function groAisleColor(section) { return GRO_AISLE_COLORS[section] || 'var(--ink-inactive)'; }
  function groStoreColor(name) {
    // "Any store" is the leftovers bucket, not a stop — it gets the quiet
    // sand fill rather than a store identity colour.
    if (!name || name === 'Unassigned') return '#E6D9C4';
    var h = 0;
    for (var i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
    return GRO_STORE_PALETTE[h % GRO_STORE_PALETTE.length];
  }
  function groStoreLabel(name) { return (!name || name === 'Unassigned') ? 'Any store' : name; }
  function groStoreInitial(name) {
    if (!name || name === 'Unassigned') return '?';
    return (name.trim()[0] || '?').toUpperCase();
  }
  function groPlural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }
  // Real stores first, "Any store" last. It is where things land before
  // anyone has decided, so it reads as the remainder at the bottom of the
  // list rather than as the first stop of the trip.
  function groOrderStores(names) {
    return names.slice().sort(function (a, b) {
      if (a === 'Unassigned') return 1;
      if (b === 'Unassigned') return -1;
      return 0;
    });
  }

  var groceryState = {
    // list | sort | trip | wrap — see goGroceryStep. Starts at the root, so
    // a refresh lands on LIST.
    step: 'list',
    data: null,
    loadError: false,
    usualStores: [],        // household's saved stores, offered as sort pills
    // Loop Board 19a: whether the "Where do you usually shop?" first-visit
    // card has been quietly declined ("One list is fine") — persisted
    // server-side (meal_preferences.stores_prompt_dismissed_at) so it stays
    // gone across visits, not just this page view.
    storesPromptDismissed: false,
    itemStorePrefs: {},     // lowercased item name -> remembered store
    preShopFlags: [],
    preShopOpen: false,
    preShopExpanded: false,
    alreadyHaveSummary: { already_have: [], elsewhere: [] },  // WRAP UP's confirmation
    listExpanded: {},       // store name -> bool: "+ N more" tapped on LIST
    inCartOpen: false,      // "In your cart · N" group on TRIP
    // Ids resolved via SORT's "Any" pill this page view. "Any" saves
    // store: '' (see stores.set_grocery_item_store's docstring — an empty
    // store is a deliberate, remembered-nothing "no particular store" skip,
    // not a placeholder), which is indistinguishable on the wire from an
    // item that has simply never been triaged: both land in the
    // 'Unassigned' bucket. groUnsorted() below excludes ids in this set so
    // an "Any" choice leaves the to-sort queue exactly the way a real store
    // choice already does (see the 'assign' handler). KNOWN LIMIT, unchanged
    // from the segmented version: this is client-side and page-view only,
    // so a reload re-sorts an "Any" item — which matches "skip" being
    // one-off rather than permanent, but does mean the TO SORT badge can
    // come back after a refresh.
    anyStoreIds: {},
    // How many things SORT set out to sort, so the progress line can say
    // "2 of 3" rather than counting down from a number nobody saw.
    sortTotal: 0,
    // The trip, snapshotted at "Start the trip" so finishing a stop can't
    // renumber the ones behind it: an ordered list of store names, plus
    // where we are in it. Null between trips.
    tripStops: null,
    tripIndex: 0,
    tripTotal: 0,           // things needed when the trip began
    tripBought: 0,          // things actually committed, this trip only
    // WRAP UP: ids the shopper said "Couldn't find it" about. There is no
    // note column on grocery_items (see app/schema.sql), so this keeps the
    // row exactly as it is — needed — and only records that it has been
    // answered, so the wrap-up list doesn't keep asking.
    wrapKept: {},
    openFlagKey: null,
    voiceSession: null,
    voiceLog: [],
    // Page-view only, like weekReceiptHandoffDismissed — "I'll come back to
    // it" on the shop-done handoff just collapses the offer for this visit,
    // no persistence.
    shopDoneHandoffDismissed: false,
    // Whether a trip was finished in THIS page view (see 'finish-trip').
    // groTotals().done can't answer "did anything get bought this cycle" —
    // it sums purchased + in_cart rows over the household's entire
    // lifetime, and nothing ever resets a purchased row (clear_stale_
    // grocery_items only touches 'needed' rows), so it stays > 0 forever
    // after the first-ever trip. This flag is the honest replacement: true
    // only from a successful finish in this visit, and cleared (a) the
    // moment the list gains needed items again (groListHtml), so a stale
    // trip from before a restock can't resurface once the restock is bought
    // out too, and (b) on leaving the Grocery tab for any other tab
    // (activateTab), so "away and back" doesn't resurrect it either.
    justFinishedTrip: false
  };

  var GRO_PS_CAP = 5;

  function groPanel() { return panels['grocery']; }
  function groIsBuilt() { var p = groPanel(); return !!(p && p.dataset.built); }

  // ---------- Data ----------
  // One fetch of three views, combined client-side into
  //   storeName -> { sections: [{section, items}], purchased: [], inCart: [] }
  // exactly as the page this replaces did — no new endpoints, and the three
  // statuses are the three things every step needs.
  async function groLoadAllData() {
    var results = await Promise.all([
      fetch('/api/grocery-list/by-store?status=needed'),
      fetch('/api/grocery-list?status=purchased'),
      fetch('/api/grocery-list?status=in_cart')
    ]);
    if (results.some(function (r) { return !r.ok; })) throw new Error('grocery load failed');
    var byStore = await results[0].json();
    var purchasedView = await results[1].json();
    var inCartView = await results[2].json();

    var stores = {};
    (byStore.stores || []).forEach(function (s) {
      stores[s.store] = { sections: s.sections || [], purchased: [], inCart: [] };
    });
    function fold(view, key) {
      (view.sections || []).forEach(function (sec) {
        (sec.items || []).forEach(function (it) {
          var name = it.store || 'Unassigned';
          if (!stores[name]) stores[name] = { sections: [], purchased: [], inCart: [] };
          stores[name][key].push(it);
        });
      });
    }
    fold(purchasedView, 'purchased');
    fold(inCartView, 'inCart');
    return { stores: stores };
  }

  function groNeededCount(storeData) {
    return (storeData.sections || []).reduce(function (n, s) { return n + s.items.length; }, 0);
  }
  function groStoreItems(storeData) {
    return (storeData.sections || []).reduce(function (acc, s) { return acc.concat(s.items); }, []);
  }
  function groUnsorted(data) {
    var u = data.stores['Unassigned'];
    if (!u) return [];
    return groStoreItems(u).filter(function (it) { return !groceryState.anyStoreIds[String(it.id)]; });
  }
  // Things deliberately marked "Any" this page view — sorted, but with no
  // store of their own. They are not a card on LIST (they have no stop to
  // sit under); they ride along with the first stop of the trip.
  function groAnyItems(data) {
    var u = data.stores['Unassigned'];
    if (!u) return [];
    return groStoreItems(u).filter(function (it) { return !!groceryState.anyStoreIds[String(it.id)]; });
  }
  function groStoresWithNeeded(data) {
    return groOrderStores(Object.keys(data.stores).filter(function (n) {
      return n !== 'Unassigned' && groNeededCount(data.stores[n]) > 0;
    }));
  }
  function groTotals(data) {
    var names = Object.keys(data.stores);
    var totalNeeded = 0, totalDone = 0;
    names.forEach(function (name) {
      var s = data.stores[name];
      // in_cart is "found, still in the trolley" — it counts as progress on
      // the trip, the same way the stop's own count does.
      totalNeeded += groNeededCount(s);
      totalDone += s.purchased.length + s.inCart.length;
    });
    return { needed: totalNeeded, done: totalDone, all: totalNeeded + totalDone };
  }

  // ---------- Requests ----------
  async function groPost(url, body) {
    var res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    if (!res.ok) throw new Error('request failed');
    return res.json().catch(function () { return {}; });
  }
  async function groPostEmpty(url) {
    var res = await fetch(url, { method: 'POST' });
    if (!res.ok) throw new Error('request failed');
    return res.json().catch(function () { return {}; });
  }

  // Every write goes through here: run it, then re-read and re-render. The
  // failure message is the shell's toast rather than an alert() — the page
  // this replaces used alert(), which is a modal interruption for something
  // that is usually just "try that again".
  async function groDo(fn, failureMessage) {
    try {
      await fn();
      await loadGrocery();
      return true;
    } catch (err) {
      console.warn('Grocery action failed:', err);
      showToast(failureMessage || "That didn't save — try again.");
      await loadGrocery();
      return false;
    }
  }

  // ---------- Build ----------
  function buildGroceryPanel(panel) {
    panel.innerHTML =
      '<div class="grocery-content">' +
        '<button type="button" class="gro-back" id="gro-back" data-gro="step-back" hidden></button>' +
        '<div class="gro-head">' +
          '<div class="gro-head-row">' +
            '<h1 class="gro-title" id="gro-title">Grocery</h1>' +
            // The TO SORT badge is a control, not decoration: it is the only
            // way into the SORT step, and it only exists while something has
            // no store.
            '<button type="button" class="gro-sortbadge" id="gro-sortbadge" data-gro="goto-sort" hidden></button>' +
            '<span class="gro-hairline"></span>' +
            '<button type="button" class="gro-icon-btn" id="gro-mic-btn" data-gro="voice" ' +
              'title="Hands-free: check off, add, or ask about items by voice" ' +
              'aria-label="Hands-free voice mode">' + GRO_ICONS.mic + '</button>' +
            '<button type="button" class="gro-icon-btn" id="gro-refresh-btn" data-gro="refresh" ' +
              'title="Reload the latest list" aria-label="Reload the latest list">' + GRO_ICONS.refresh + '</button>' +
          '</div>' +
          '<div class="gro-sub" id="gro-sub" hidden></div>' +
        '</div>' +
        '<div class="gro-voice" id="gro-voice" hidden></div>' +
        '<div class="gro-body" id="gro-body"><p class="gro-empty">Loading&hellip;</p></div>' +
        '<div class="gro-body gro-foot" id="gro-foot"></div>' +
      '</div>';

    // One delegated listener for the whole screen. The alternative — re-wiring
    // every row after every render, which is what the page this replaces did —
    // is where a missed handler hides.
    panel.addEventListener('click', onGroceryClick);
    panel.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      if (e.target.id === 'gro-stores-prompt-input') {
        e.preventDefault();
        panel.querySelector('[data-gro="stores-prompt-add"]').click();
      }
    });

    loadGrocery();
    // Both are niceties for the SORT pills — a failure leaves the pills
    // populated from what is already tagged on the list, so neither blocks.
    groLoadUsualStores();
    groLoadStorePrefs();
  }

  async function groLoadUsualStores() {
    try {
      var res = await fetch('/api/memory');
      if (!res.ok) return;
      var memory = await res.json();
      groceryState.usualStores = memory.usual_stores || [];
      groceryState.storesPromptDismissed = !!memory.stores_prompt_dismissed;
      renderGrocery();
    } catch (err) { /* sorting still works from what's tagged on the list */ }
  }
  async function groLoadStorePrefs() {
    try {
      var res = await fetch('/api/grocery-list/store-preferences');
      if (!res.ok) return;
      var data = await res.json();
      groceryState.itemStorePrefs = data.preferences || {};
    } catch (err) { /* "usually here" tagging is a nicety, not load-bearing */ }
  }
  function groIsUsuallyHere(itemName, store) {
    var remembered = groceryState.itemStorePrefs[(itemName || '').trim().toLowerCase()];
    return !!remembered && remembered === store;
  }

  // Learning etiquette: the first time an item gets a store (a SORT pill),
  // the backend doesn't remember it yet — it comes back with
  // needs_confirmation instead (see stores.set_grocery_item_store). One
  // light tap here is the "confirm" step; declining (letting the toast
  // expire) leaves it a one-off, exactly like before this feature existed.
  // A "yes" writes the preference AND adds the item to that store's
  // typical-items list on the Kitchen sheet in one call
  // (confirm_grocery_item_store_preference).
  function groOfferRememberToast(item, store, itemId) {
    showToast('Remember ' + item + ' at ' + store + '?', {
      label: 'Yes, remember',
      onClick: function () {
        groDo(function () {
          return groPostEmpty('/api/grocery-list/' + itemId + '/store/confirm');
        }, "Couldn't save that — try again.").then(function (ok) {
          if (ok) {
            groceryState.itemStorePrefs[(item || '').trim().toLowerCase()] = store;
            showToast('Got it — remembered for next time');
            renderGrocery();
          }
        });
      }
    });
  }

  async function groLoadPreShopFlags() {
    try {
      var res = await fetch('/api/grocery-list/pre-shop-flags');
      if (!res.ok) { groceryState.preShopFlags = []; return; }
      groceryState.preShopFlags = (await res.json()).flags || [];
    } catch (err) { groceryState.preShopFlags = []; }
  }

  // WRAP UP's confirmation section — this week's "already have" decisions
  // plus current "Elsewhere" exclusions. Loaded alongside everything else
  // rather than only when WRAP UP is the active step, same as preShopFlags,
  // so the count is already right the moment you get there.
  async function groLoadAlreadyHaveSummary() {
    try {
      var res = await fetch('/api/grocery-list/already-have-summary');
      if (!res.ok) { groceryState.alreadyHaveSummary = { already_have: [], elsewhere: [] }; return; }
      groceryState.alreadyHaveSummary = await res.json();
    } catch (err) { groceryState.alreadyHaveSummary = { already_have: [], elsewhere: [] }; }
  }

  async function loadGrocery() {
    var panel = groPanel();
    if (!panel || !panel.dataset.built) return;
    try {
      var pair = await Promise.all([groLoadAllData(), groLoadPreShopFlags(), groLoadAlreadyHaveSummary()]);
      groceryState.data = pair[0];
      groceryState.loadError = false;
    } catch (err) {
      console.warn('Grocery list lookup failed:', err);
      groceryState.loadError = true;
    }
    renderGrocery();
  }

  // Called from the three refresh paths (chat action, week approval, reset).
  // A screen that was built early has to stay correct, not stay frozen.
  function refreshGroceryPanel() {
    if (groIsBuilt()) loadGrocery();
  }

  // ---------- The step machine ----------
  // Copied from Meals' goMealsStep/pushMealsStepHistory pair, deliberately:
  // one pattern for "a tab with steps" beats two that drift.

  function pushGroceryStepHistory() {
    // Same path in every step — Grocery is /grocery throughout. The state
    // object is what the back gesture reads; the URL never claims a page
    // that doesn't exist.
    window.history.pushState({
      tab: 'grocery',
      groStep: groceryState.step,
      groTripIndex: groceryState.tripIndex
    }, '', '/grocery');
  }

  function goGroceryStep(step, opts) {
    opts = opts || {};
    var prev = groceryState.step;
    groceryState.step = step;
    // Arriving at SORT fixes how many things it set out to sort, so the
    // progress line can say "2 of 3" instead of counting down from a number
    // nobody was shown.
    if (step === 'sort' && prev !== 'sort' && groceryState.data) {
      groceryState.sortTotal = groUnsorted(groceryState.data).length;
    }
    if (opts.tripIndex !== undefined && opts.tripIndex !== null) groceryState.tripIndex = opts.tripIndex;
    if (opts.push !== false) pushGroceryStepHistory();
    renderGrocery();
    // A step change is a screen change, so it starts at the top.
    if (scrollEl) scrollEl.scrollTop = 0;
  }

  // The browser's own back gesture, one level out. Called from the shell's
  // single popstate listener so there is one place that decides what Back
  // means, rather than a second listener racing the first.
  function applyGroceryStepFromHistory(state) {
    if (!groIsBuilt()) return;
    if (state && state.groTripIndex !== undefined && state.groTripIndex !== null) {
      groceryState.tripIndex = state.groTripIndex;
    }
    groceryState.step = (state && state.groStep) || 'list';
    renderGrocery();
  }

  // Compatibility shim for the three callers outside this region that still
  // ask for a screen by its old segment name — the approved-week receipt's
  // "Take me to the list", its toast twin, and the ask sheet's "Plan my
  // stops" chip (all `activateTab('grocery', true, { groScreen: 'plan' })`).
  // They all mean "the list, and sort it if it needs sorting", which is
  // exactly what these two steps are.
  function groSetScreen(screen) {
    var data = groceryState.data;
    var wantsSort = screen === 'plan' && data && groUnsorted(data).length > 0;
    goGroceryStep(wantsSort ? 'sort' : 'list');
  }

  // ---------- Render ----------
  function renderGrocery() {
    var panel = groPanel();
    if (!panel) return;
    var back = panel.querySelector('#gro-back');
    var title = panel.querySelector('#gro-title');
    var badge = panel.querySelector('#gro-sortbadge');
    var sub = panel.querySelector('#gro-sub');
    var body = panel.querySelector('#gro-body');
    var foot = panel.querySelector('#gro-foot');
    if (!back || !title || !badge || !sub || !body || !foot) return;

    // Re-rendering replaces the list under the reader's thumb, so hold the
    // scroll position across it. "Nothing else moves, ever."
    var keepScroll = scrollEl ? scrollEl.scrollTop : 0;

    if (groceryState.loadError || !groceryState.data) {
      back.hidden = true;
      badge.hidden = true;
      sub.hidden = true;
      title.textContent = 'Grocery';
      body.innerHTML = groceryState.loadError
        ? '<p class="gro-error">Couldn\'t load the grocery list right now — try the refresh button above.' + snwLink() + '</p>'
        : '<p class="gro-empty">Loading&hellip;</p>';
      foot.innerHTML = '';
      return;
    }

    var data = groceryState.data;
    // A step that stopped making sense under its own feet falls back to the
    // root rather than rendering a screen about nothing: SORT with nothing
    // left to sort, a trip whose stops were never snapshotted.
    if (groceryState.step === 'sort' && !groUnsorted(data).length) groceryState.step = 'list';
    if ((groceryState.step === 'trip' || groceryState.step === 'wrap') && !groceryState.tripStops) {
      groceryState.step = 'list';
    }
    var step = groceryState.step;

    var head = groHeadFor(data, step);
    back.hidden = !head.back;
    if (head.back) back.textContent = head.back;
    title.textContent = head.title;
    sub.hidden = !head.sub;
    if (head.sub) sub.textContent = head.sub;

    // The badge belongs to LIST — on the deeper steps it would be a second
    // way out of a screen that already has one.
    var unsorted = groUnsorted(data).length;
    var showBadge = step === 'list' && unsorted > 0 && !groStoresPromptShouldShow();
    badge.hidden = !showBadge;
    if (showBadge) {
      badge.textContent = unsorted + ' TO SORT';
      badge.setAttribute('aria-label', groPlural(unsorted, 'thing', 'things') + ' to sort');
    }

    if (step === 'sort') body.innerHTML = groSortHtml(data);
    else if (step === 'trip') body.innerHTML = groTripHtml(data);
    else if (step === 'wrap') body.innerHTML = groWrapHtml(data);
    else body.innerHTML = groListHtml(data);

    foot.innerHTML = groFootHtml(data, step);

    if (scrollEl) scrollEl.scrollTop = keepScroll;
  }

  // Title, subtitle and back link for each step, in one place so the copy is
  // readable as a set rather than scattered through four builders.
  function groHeadFor(data, step) {
    if (step === 'sort') {
      return { back: '‹ Grocery', title: 'Where does this go?', sub: groUnsorted(data).length + ' to sort' };
    }
    if (step === 'trip') {
      var store = groTripStore();
      var stops = groceryState.tripStops || [];
      return {
        back: '‹ Pause the trip',
        title: store || 'The trip',
        sub: 'Stop ' + (groceryState.tripIndex + 1) + ' of ' + Math.max(stops.length, 1) +
          ' · ' + groTripItems(data).length + ' left'
      };
    }
    if (step === 'wrap') return { back: '', title: 'How did it go?', sub: '' };
    var t = groTotals(data);
    var stopCount = groStoresWithNeeded(data).length;
    var sub = '';
    if (t.needed) {
      sub = groPlural(t.needed, 'thing', 'things');
      if (stopCount) sub += ' · ' + groPlural(stopCount, 'stop', 'stops');
    }
    return { back: '', title: 'Grocery', sub: sub };
  }

  // ---------- LIST ----------
  function groListHtml(data) {
    var stops = groStoresWithNeeded(data);
    var unsorted = groUnsorted(data);
    if (stops.length || unsorted.length) {
      // The list has needed items again — any justFinishedTrip signal left
      // over from an earlier trip in this same visit no longer describes
      // this list. See the flag's declaration in groceryState above.
      groceryState.justFinishedTrip = false;
    }

    var html = groPreShopHtml();

    // Nothing to shop, nowhere to shop it: the just-in-time stores card
    // stands in for the store cards, unchanged in behaviour.
    if (groStoresPromptShouldShow() && (stops.length || unsorted.length)) {
      return html + groStoresPromptHtml();
    }

    if (!stops.length && !unsorted.length) {
      if (groceryState.justFinishedTrip) {
        if (groceryState.shopDoneHandoffDismissed) {
          return html +
            '<div class="shell-card gro-shop-done-card gro-shop-done-dismissed">' +
              '<div class="gro-shop-done-line">That’s the shopping done.</div>' +
              '<button type="button" class="gro-shop-done-link" data-gro="shop-done-tonight">See tonight’s dinner &rarr;</button>' +
            '</div>';
        }
        return html + groShopDoneHtml();
      }
      return html + '<p class="gro-empty">Nothing on the list yet — it’ll arrive here when you plan a week.</p>';
    }

    stops.forEach(function (name) { html += groStoreCardHtml(data, name); });

    // Unsorted things are NOT a section here — they live in SORT, which the
    // badge above opens. Saying so once beats a card that repeats them.
    if (!stops.length && unsorted.length) {
      html += '<p class="gro-empty">Everything on the list still needs a store — tap ' +
        unsorted + ' TO SORT above and I’ll take you through them.</p>';
    }
    return html;
  }

  function groStoreCardHtml(data, name) {
    var s = data.stores[name];
    var items = groStoreItems(s);
    var expanded = !!groceryState.listExpanded[name];
    var shown = expanded ? items : items.slice(0, GRO_CARD_PEEK);
    var hidden = items.length - shown.length;
    return '<div class="gro-store">' +
      '<div class="gro-card-head">' +
        '<span class="gro-store-avatar" style="background:' + groStoreColor(name) + '">' +
          escapeHtml(groStoreInitial(name)) + '</span>' +
        '<span class="gro-store-name">' + escapeHtml(name) + ' · ' + items.length + '</span>' +
      '</div>' +
      shown.map(groListRowHtml).join('') +
      (hidden > 0
        ? '<button type="button" class="gro-more-link" data-gro="expand-store" data-store="' + escapeHtml(name) + '">' +
            '+ ' + hidden + ' more</button>'
        : '') +
    '</div>';
  }

  function groListRowHtml(it) {
    return '<div class="gro-listrow">' +
      '<span class="gro-listrow-name">' + escapeHtml(it.item) + '</span>' +
      (it.quantity ? '<span class="gro-qty">' + escapeHtml(it.quantity) + '</span>' : '') +
    '</div>';
  }

  // ---------- SORT ----------
  // One thing at a time. The pills and their semantics are the ones the
  // triage row already had: a store assigns and advances, "Any" saves an
  // empty store and advances (see anyStoreIds), "Have it" takes it off the
  // list into the kitchen, "Somewhere else" excludes it. The last choice
  // drops through to LIST on its own — see the 'assign' handler.
  function groSortHtml(data) {
    var unsorted = groUnsorted(data);
    if (!unsorted.length) return '<p class="gro-empty">Nothing left to sort — nice work.</p>';
    var it = unsorted[0];
    var id = String(it.id);

    // Every store already on the list, plus the household's usual stores —
    // so a store can be chosen before anything is tagged to it.
    var pillStores = [];
    Object.keys(data.stores).forEach(function (n) { if (n !== 'Unassigned' && pillStores.indexOf(n) === -1) pillStores.push(n); });
    groceryState.usualStores.forEach(function (n) { if (n && pillStores.indexOf(n) === -1) pillStores.push(n); });

    var total = Math.max(groceryState.sortTotal, unsorted.length);
    var position = total - unsorted.length + 1;

    return '<div class="shell-card gro-sortcard">' +
        '<p class="gro-sort-item">' + escapeHtml(it.item) + '</p>' +
        (it.quantity ? '<p class="gro-sort-qty">' + escapeHtml(it.quantity) + '</p>' : '') +
        '<div class="gro-pills open">' +
          pillStores.map(function (n) {
            return '<button type="button" class="gro-pill" data-gro="assign" data-id="' + id + '" data-store="' + escapeHtml(n) + '" ' +
              'aria-label="Buy ' + escapeHtml(it.item) + ' at ' + escapeHtml(n) + '">' + escapeHtml(n) + '</button>';
          }).join('') +
          '<button type="button" class="gro-pill" data-gro="assign" data-id="' + id + '" data-store="" ' +
            'aria-label="No particular store for ' + escapeHtml(it.item) + '">Any</button>' +
          // Secondary action, same backend path as "Have it" always had — a
          // store pill sorts the item, this takes it off the list entirely
          // because it turns out no store is needed.
          '<button type="button" class="gro-pill gro-pill-have" data-gro="already-have" data-id="' + id + '" ' +
            'aria-label="Already have ' + escapeHtml(it.item) + '">Have it</button>' +
          // Covers the other reason a thing leaves the sort queue without a
          // store: it's being picked up on a trip that isn't one of this
          // household's stores. Same /exclude route as ever.
          '<button type="button" class="gro-pill gro-pill-else" data-gro="triage-exclude" data-id="' + id + '" ' +
            'aria-label="Getting ' + escapeHtml(it.item) + ' somewhere else">Somewhere else</button>' +
        '</div>' +
      '</div>' +
      '<p class="gro-sort-progress">' + position + ' of ' + total + '</p>';
  }

  // ---------- TRIP ----------
  function groTripStore() {
    var stops = groceryState.tripStops || [];
    return stops[groceryState.tripIndex] || null;
  }

  // What this stop is for. The first stop also carries anything marked
  // "Any" — it has to be bought somewhere, and the first shop you walk into
  // is the honest answer.
  function groTripItems(data) {
    var store = groTripStore();
    if (!store) return [];
    var s = data.stores[store];
    var items = s ? groStoreItems(s) : [];
    if (groceryState.tripIndex === 0) items = items.concat(groAnyItems(data));
    return items;
  }
  function groTripInCart(data) {
    var store = groTripStore();
    if (!store) return [];
    var s = data.stores[store];
    var items = s ? s.inCart.slice() : [];
    if (groceryState.tripIndex === 0) {
      var any = data.stores['Unassigned'];
      if (any) items = items.concat(any.inCart);
    }
    return items;
  }
  // The stop's needed things, aisle by aisle, in the order the payload
  // already put them (get_grocery_list_by_store's own section order — the
  // order a shop is walked in). The first stop's "Any" things fold into the
  // matching aisle rather than trailing after it.
  function groTripSections(data) {
    var store = groTripStore();
    if (!store) return [];
    var out = [];
    var byName = {};
    function fold(sections) {
      (sections || []).forEach(function (sec) {
        if (!sec.items.length) return;
        if (!byName[sec.section]) {
          byName[sec.section] = { section: sec.section, items: [] };
          out.push(byName[sec.section]);
        }
        byName[sec.section].items = byName[sec.section].items.concat(sec.items);
      });
    }
    fold(data.stores[store] && data.stores[store].sections);
    if (groceryState.tripIndex === 0) {
      var any = data.stores['Unassigned'];
      if (any) {
        fold((any.sections || []).map(function (sec) {
          return {
            section: sec.section,
            items: sec.items.filter(function (it) { return !!groceryState.anyStoreIds[String(it.id)]; })
          };
        }));
      }
    }
    return out;
  }

  function groTripHtml(data) {
    var store = groTripStore();
    if (!store) return '<p class="gro-empty">This trip has no stops left — head back and start a new one.</p>';

    var sections = groTripSections(data);
    var html = '';
    if (sections.length) {
      html += '<div class="gro-store">';
      sections.forEach(function (sec) {
        html += '<div class="gro-aisle">' +
          '<span class="gro-aisle-spine" style="background:' + groAisleColor(sec.section) + '"></span>' +
          '<span class="gro-eyebrow">' + escapeHtml(GRO_CATEGORY_LABELS[sec.section] || sec.section) + '</span>' +
          '<span class="gro-aisle-count">' + sec.items.length + ' left</span>' +
        '</div>';
        sec.items.forEach(function (it) { html += groTripRowHtml(it); });
      });
      html += '</div>';
    } else {
      html += '<p class="gro-empty">Everything here is in the cart.</p>';
    }

    // The trolley, collapsed, with the put-back this screen has always had
    // (groDoneRowHtml's row IS the put-back: status -> needed).
    var inCart = groTripInCart(data);
    if (inCart.length) {
      var open = groceryState.inCartOpen;
      html += '<div class="gro-done' + (open ? ' open' : '') + '">' +
        '<button type="button" class="gro-done-head" data-gro="toggle-incart" aria-expanded="' + open + '">' +
          '<span class="gro-done-tick">' + GRO_ICONS.basket + '</span>' +
          '<span class="gro-done-label">In your cart · ' + inCart.length + '</span>' +
          '<span class="gro-chev">' + (open ? GRO_ICONS.chevDown : GRO_ICONS.chevRight) + '</span>' +
        '</button>' +
        (open ? '<div class="gro-done-body">' + inCart.map(groDoneRowHtml).join('') + '</div>' : '') +
      '</div>';
    }
    return html;
  }

  function groTripRowHtml(it) {
    var id = String(it.id);
    return '<div class="gro-row" data-gro="trip-toggle" data-id="' + id + '" data-incart="0">' +
      '<button type="button" class="gro-box" role="checkbox" aria-checked="false" ' +
        'data-gro="trip-toggle" data-id="' + id + '" data-incart="0" ' +
        'aria-label="Found ' + escapeHtml(it.item) + '"></button>' +
      '<p class="gro-name">' + escapeHtml(it.item) + '</p>' +
      (it.quantity ? '<span class="gro-qty">' + escapeHtml(it.quantity) + '</span>' : '') +
    '</div>';
  }

  // A row already in the trolley — tapping it puts it back on the list.
  function groDoneRowHtml(it) {
    var id = String(it.id);
    return '<div class="gro-row done" data-gro="uncheck" data-id="' + id + '">' +
      '<button type="button" class="gro-box checked" role="checkbox" aria-checked="true" data-gro="uncheck" data-id="' + id + '" ' +
        'aria-label="Put ' + escapeHtml(it.item) + ' back on the list">' + GRO_ICONS.tick + '</button>' +
      '<p class="gro-name">' + escapeHtml(it.item) + '</p>' +
      (it.quantity ? '<span class="gro-qty">' + escapeHtml(it.quantity) + '</span>' : '') +
    '</div>';
  }

  // ---------- WRAP UP ----------
  function groAllNeeded(data) {
    var out = [];
    Object.keys(data.stores).forEach(function (name) {
      groStoreItems(data.stores[name]).forEach(function (it) { out.push(it); });
    });
    return out;
  }

  function groWrapHtml(data) {
    var stillNeeded = groAllNeeded(data);
    var html = '';
    if (stillNeeded.length) {
      html += '<p class="gro-eyebrow">Still on the list</p><div class="gro-store gro-wrap-list">';
      stillNeeded.forEach(function (it) {
        var id = String(it.id);
        var kept = !!groceryState.wrapKept[id];
        html += '<div class="gro-wrap-row' + (kept ? ' kept' : '') + '">' +
          '<span class="gro-wrap-name">' + escapeHtml(it.item) +
            (it.quantity ? ' <span class="gro-qty">' + escapeHtml(it.quantity) + '</span>' : '') + '</span>' +
          (kept
            ? '<span class="gro-wrap-kept">Kept on the list</span>'
            : '<span class="gro-wrap-seg">' +
                '<button type="button" class="gro-wrap-btn" data-gro="wrap-keep" data-id="' + id + '">Couldn’t find it</button>' +
                '<button type="button" class="gro-wrap-btn" data-gro="wrap-else" data-id="' + id + '">Somewhere else</button>' +
              '</span>') +
        '</div>';
      });
      html += '</div>';
    } else {
      html += '<div class="gro-allclear">Everything on the list made it into the cart.</div>';
    }

    // Review's confirmation half, kept: this week's "already have" decisions
    // and current "Elsewhere" exclusions, each with its undo.
    html += groAlreadyHaveHtml();

    var bought = groceryState.tripBought;
    var total = Math.max(groceryState.tripTotal, bought);
    html += '<p class="gro-wrap-summary">Bought ' + bought + ' of ' + total + '</p>';
    return html;
  }

  // This week's "already have" decisions (Have it + pre-shop drops) and
  // current "Elsewhere" exclusions — a confirmation, not a warning, so it is
  // always celadon ("settled, handled, already true" per the design system),
  // never urgent/apricot.
  function groAlreadyHaveHtml() {
    var summary = groceryState.alreadyHaveSummary || {};
    var already = summary.already_have || [];
    var elsewhere = summary.elsewhere || [];
    if (!already.length && !elsewhere.length) return '';

    function decisionRow(it, action, label) {
      return '<div class="gro-fix">' +
        '<span>' + escapeHtml(it.item) + (it.quantity ? ' &middot; ' + escapeHtml(it.quantity) : '') + '</span>' +
        '<button type="button" class="secondary" data-gro="' + action + '" data-id="' + String(it.id) + '">' + label + '</button>' +
      '</div>';
    }

    var body = '';
    if (already.length) {
      body += '<p class="gro-fix-note">You said you already have: ' +
        already.map(function (it) { return escapeHtml(it.item); }).join(', ') + '</p>' +
        already.map(function (it) { return decisionRow(it, 'undo-already-have', 'Actually, I need it'); }).join('');
    }
    if (elsewhere.length) {
      body += '<p class="gro-fix-note">Getting elsewhere: ' +
        elsewhere.map(function (it) { return escapeHtml(it.item); }).join(', ') + '</p>' +
        elsewhere.map(function (it) { return decisionRow(it, 'undo-elsewhere', 'Actually, get it here'); }).join('');
    }

    var open = groceryState.openFlagKey === 'already-have';
    return '<div class="gro-flag">' +
      '<button type="button" class="gro-flag-head" data-gro="flag-toggle" data-key="already-have" aria-expanded="' + open + '">' +
        '<span class="gro-flag-badge" style="background:var(--celadon);color:var(--on-accent-ink)">&check;</span>' +
        '<span class="gro-flag-title">Already sorted this week &middot; ' + (already.length + elsewhere.length) + '</span>' +
        '<span class="gro-chev">' + (open ? GRO_ICONS.chevDown : GRO_ICONS.chevRight) + '</span>' +
      '</button>' +
      (open ? '<div class="gro-flag-body">' + body + '</div>' : '') +
    '</div>';
  }

  // ---------- The foot: one apricot action per step (Rule 5) ----------
  function groFootHtml(data, step) {
    if (step === 'list') {
      var stops = groStoresWithNeeded(data);
      var canGo = stops.length > 0;
      return (canGo
          ? '<button type="button" class="gro-primary" data-gro="start-trip">Start the trip</button>'
          : '') +
        '<button type="button" class="gro-quiet" data-gro="add-something">Add something</button>';
    }
    if (step === 'trip') {
      var stops2 = groceryState.tripStops || [];
      var here = groTripStore();
      var next = stops2[groceryState.tripIndex + 1];
      var label = next
        ? 'Done at ' + here + ' → ' + next
        : 'Done shopping';
      return '<button type="button" class="gro-primary" data-gro="stop-done">' + escapeHtml(label) + '</button>';
    }
    if (step === 'wrap') {
      return '<button type="button" class="gro-primary" data-gro="finish-trip">Finish the trip</button>';
    }
    return '';
  }

  // ---------- "Maybe already home" ----------
  // The kitchen may already have some of this: a compact celadon banner with
  // a "Check", folded until asked. Same flags, same keep/drop per item, same
  // Keep all, same undo.
  function groPreShopHtml() {
    var flags = groceryState.preShopFlags;
    if (!flags.length) return '';
    var open = groceryState.preShopOpen;
    var shown = groceryState.preShopExpanded ? flags : flags.slice(0, GRO_PS_CAP);
    var remaining = flags.length - shown.length;

    var html = '<div class="gro-ps">' +
      '<button type="button" class="gro-ps-head" data-gro="ps-toggle" aria-expanded="' + open + '">' +
        GRO_ICONS.basket +
        '<span class="gro-ps-text">' +
          '<span class="gro-ps-title">Maybe already home</span>' +
          '<span class="gro-ps-sub">' + groPlural(flags.length, 'thing', 'things') + ' the kitchen may already have</span>' +
        '</span>' +
        '<span class="gro-ps-check">' + (open ? 'Hide' : 'Check') + '</span>' +
      '</button>';
    if (open) {
      html += '<div class="gro-ps-body">' +
        '<p class="gro-ps-helper">Inventory thinks these are in the kitchen. Dropping one takes it off today&rsquo;s list.</p>' +
        shown.map(function (f) {
          var title = f.onHandLocation ? ' title="In the ' + escapeHtml(f.onHandLocation) + '"' : '';
          return '<div class="gro-ps-row">' +
            '<p class="gro-ps-name">' + escapeHtml(f.name) + '</p>' +
            '<p class="gro-ps-sentence"' + title + '>' + escapeHtml(f.sentence) + '</p>' +
            '<div class="gro-ps-actions">' +
              '<button type="button" class="gro-ps-btn gro-ps-btn-keep" data-gro="ps-decide" data-decision="keep" ' +
                'data-id="' + f.itemId + '" data-name="' + escapeHtml(f.name) + '">Buy it anyway</button>' +
              '<button type="button" class="gro-ps-btn gro-ps-btn-drop" data-gro="ps-decide" data-decision="drop" ' +
                'data-id="' + f.itemId + '" data-name="' + escapeHtml(f.name) + '">Drop it</button>' +
            '</div>' +
          '</div>';
        }).join('') +
        '<div class="gro-ps-foot">' +
          (remaining > 0
            ? '<button type="button" class="gro-ps-more" data-gro="ps-more">+' + remaining + ' more like this</button>'
            : '<span></span>') +
          '<button type="button" class="gro-ps-keepall" data-gro="ps-keepall">Keep all ' + flags.length + '</button>' +
        '</div>' +
      '</div>';
    }
    return html + '</div>';
  }

  // Tonight's dinner name, read from whatever the Meals tab has already
  // cached in weekState — never a new fetch just for this line. Null whenever
  // Meals hasn't been visited yet this session, or tonight has no settled
  // dish, which the shop-done handoff below treats as "say it without the
  // dish".
  function tonightDinnerName() {
    var data = weekState.data;
    if (!data || !data.days) return null;
    var todayStr = todayLocalStr();
    var day = data.days.filter(function (d) { return d.date === todayStr; })[0];
    var entry = day && day.dinner;
    if (entry && entry.title && entry.state !== 'open' && entry.state !== 'planned_empty') return entry.title;
    return null;
  }

  // The handoff for "that's the shopping done" (Emily, 2026-09-04's ask to
  // walk the loop forward): shown on LIST once the list has nothing left to
  // buy AND a trip was actually finished in this page view —
  // groceryState.justFinishedTrip, not a lifetime purchased/in_cart count
  // (see that flag's declaration for why). Spruce fill, not apricot: LIST's
  // own "Start the trip" owns the screen's one apricot (Rule 5).
  function groShopDoneHtml() {
    var dish = tonightDinnerName();
    var line = dish
      ? 'That’s the shopping done. Tonight it’s ' + escapeHtml(dish) + '.'
      : 'That’s the shopping done.';
    return (
      '<div class="shell-card gro-shop-done-card">' +
        '<div class="gro-shop-done-line">' + line + '</div>' +
        '<div class="gro-shop-done-actions">' +
          '<button type="button" class="btn-gold" data-gro="shop-done-tonight">Show me tonight</button>' +
          '<button type="button" class="btn-sand" data-gro="shop-done-later">I’ll come back to it</button>' +
        '</div>' +
      '</div>'
    );
  }

  // ---------- First-visit "where do you usually shop?" (Loop Board 19a) ----------
  // Stores used to be an onboarding question; Emily decided (2026-09-05) to
  // ask just-in-time instead, right where it first matters — the first real
  // list, rather than a question asked before there's even a list to sort.
  // Short, editable presets for an Ontario household, plus free text for
  // anything else. Picking one saves immediately through the same write path
  // the Kitchen "What we know" Stores tab uses (edit_preference/usual_stores),
  // so the SORT pills pick it up the moment this card disappears
  // (usualStores.length becomes > 0).
  var GRO_STORE_PROMPT_CHIPS = ['Costco', 'Loblaws', 'No Frills', 'Metro', 'Sobeys', 'Walmart', 'Farm Boy', 'T&T', 'Whole Foods'];

  function groStoresPromptShouldShow() {
    return !groceryState.usualStores.length && !groceryState.storesPromptDismissed;
  }

  function groStoresPromptHtml() {
    var chips = GRO_STORE_PROMPT_CHIPS.map(function (name) {
      return '<button type="button" class="gro-pill" data-gro="stores-prompt-pick" data-store="' + escapeHtml(name) + '">' +
        escapeHtml(name) + '</button>';
    }).join('');
    return (
      '<div class="shell-card gro-stores-prompt">' +
        '<p class="gro-stores-prompt-title">Where do you usually shop?</p>' +
        '<p class="gro-stores-prompt-sub">I&rsquo;ll sort the list by store and plan your stops.</p>' +
        '<div class="gro-pills open">' + chips + '</div>' +
        '<div class="gro-stores-prompt-add">' +
          '<input type="text" class="gro-stores-prompt-input" id="gro-stores-prompt-input" ' +
            'placeholder="Somewhere else?" aria-label="Add a store you usually shop at" />' +
          '<button type="button" class="gro-linkbtn" data-gro="stores-prompt-add">Add</button>' +
        '</div>' +
        '<button type="button" class="gro-stores-prompt-dismiss" data-gro="stores-prompt-dismiss">One list is fine</button>' +
      '</div>'
    );
  }

  // Saves through the same field edit_preference/the Stores tab already
  // uses — merges into whatever's already saved rather than replacing it,
  // so two quick taps ("Costco", then "No Frills") don't clobber each
  // other. Local state updates immediately so the pills reflect the new
  // store without waiting on a full grocery reload.
  function groAddUsualStore(name) {
    name = (name || '').trim();
    if (!name || groceryState.usualStores.indexOf(name) !== -1) return Promise.resolve();
    var merged = groceryState.usualStores.concat([name]);
    return groPost('/api/memory/edit', { field: 'usual_stores', value: merged }).then(function () {
      groceryState.usualStores = merged;
      renderGrocery();
    });
  }

  // ---------- Actions ----------

  // Finishing a stop: everything in the trolley becomes purchased (which is
  // what actually writes it into the kitchen's inventory — see
  // tools.mark_grocery_item), then the trip is recorded. The trip row is
  // bookkeeping and never blocks the flow, which is why it is caught
  // separately.
  async function groFinishStore(store) {
    var data = groceryState.data || await groLoadAllData();
    var s = data.stores[store];
    var inCart = s ? s.inCart.slice() : [];
    // The first stop carries anything marked "Any" (see groTripItems), so
    // its trolley has to be committed with it rather than stranded.
    if (groceryState.tripIndex === 0 && data.stores['Unassigned']) {
      inCart = inCart.concat(data.stores['Unassigned'].inCart);
    }
    for (var i = 0; i < inCart.length; i++) {
      await groPost('/api/grocery-list/' + inCart[i].id + '/status', { status: 'purchased' });
    }
    try {
      await groPost('/api/shopping-trips/close', { store: store, item_count: inCart.length });
    } catch (err) { /* bookkeeping only */ }
    groceryState.tripBought += inCart.length;
    return inCart.length;
  }

  // Anything still in a trolley when the trip is wrapped up — normally
  // nothing, because each stop commits as you leave it, but a stop that was
  // paused and never re-entered would otherwise strand its cart.
  async function groFinishAnyRemainingCarts() {
    var data = groceryState.data;
    if (!data) return;
    var names = Object.keys(data.stores);
    for (var i = 0; i < names.length; i++) {
      var s = data.stores[names[i]];
      for (var j = 0; j < s.inCart.length; j++) {
        await groPost('/api/grocery-list/' + s.inCart[j].id + '/status', { status: 'purchased' });
        groceryState.tripBought += 1;
      }
    }
  }

  function groStartTrip() {
    var data = groceryState.data;
    if (!data) return;
    var stops = groStoresWithNeeded(data);
    if (!stops.length) return;
    // Resuming a paused trip keeps its stops and its place; a new one is
    // read off the list as it stands right now.
    if (!groceryState.tripStops || !groceryState.tripStops.length) {
      groceryState.tripStops = stops;
      groceryState.tripIndex = 0;
      groceryState.tripBought = 0;
      groceryState.tripTotal = groTotals(data).needed;
      groceryState.wrapKept = {};
    }
    groceryState.inCartOpen = false;
    goGroceryStep('trip');
  }

  function onGroceryClick(e) {
    var el = e.target.closest('[data-gro]');
    if (!el) return;
    var action = el.dataset.gro;
    var id = el.dataset.id;

    switch (action) {
      case 'refresh':
        el.disabled = true;
        loadGrocery().then(function () { el.disabled = false; });
        return;

      case 'voice':
        groToggleVoice();
        return;

      // ----- moving between steps -----
      case 'step-back':
        // Up one level, named — never history.back(), which after any
        // wandering points at the previous VIEW rather than the parent. The
        // back gesture keeps its own, correct meaning through the shell's
        // popstate listener. Pausing a trip keeps tripStops, so "Start the
        // trip" resumes where it left off.
        goGroceryStep('list');
        return;

      case 'goto-sort':
        goGroceryStep('sort');
        return;

      case 'start-trip':
        groStartTrip();
        return;

      case 'add-something':
        // Item management is the ask bar's job now — one composer, every
        // screen (NavBlueprint §7). The prefill puts the cursor where the
        // thing goes.
        openAskSheet('Add ');
        return;

      case 'expand-store':
        groceryState.listExpanded[el.dataset.store] = true;
        renderGrocery();
        return;

      // ----- pre-shop -----
      case 'ps-toggle':
        groceryState.preShopOpen = !groceryState.preShopOpen;
        if (!groceryState.preShopOpen) groceryState.preShopExpanded = false;
        renderGrocery();
        return;

      case 'ps-more':
        groceryState.preShopExpanded = true;
        renderGrocery();
        return;

      case 'ps-decide': {
        var decision = el.dataset.decision;
        var itemName = el.dataset.name || 'That';
        var psId = id;
        el.closest('.gro-ps-row').querySelectorAll('button').forEach(function (b) { b.disabled = true; });
        groDo(function () {
          return groPost('/api/grocery-list/' + psId + '/pre-shop', { decision: decision, author: 'user' });
        }, "Couldn't update that — try again.").then(function (ok) {
          if (!ok) return;
          if (decision === 'keep') {
            showToast(itemName + ' stays on the list');
          } else {
            showToast(itemName + ' off the list — you have enough', {
              label: 'Undo',
              onClick: function () {
                groDo(function () {
                  return groPostEmpty('/api/grocery-list/' + psId + '/pre-shop-undo');
                }, "Couldn't undo that — try again.");
              }
            });
          }
        });
        return;
      }

      case 'ps-keepall':
        el.disabled = true;
        groDo(function () {
          return groPostEmpty('/api/grocery-list/pre-shop/keep-all');
        }, "Couldn't update those — try again.").then(function (ok) {
          if (ok) showToast('Kept all — nothing dropped');
        });
        return;

      // ----- "where do you usually shop?" first-visit card (Loop Board 19a) -----
      case 'stores-prompt-pick':
        el.disabled = true;
        groAddUsualStore(el.dataset.store).catch(function () {
          showToast("Couldn't save that — try again.");
        }).then(function () { el.disabled = false; });
        return;

      case 'stores-prompt-add': {
        var storesPromptPanel = groPanel();
        var storesPromptInput = storesPromptPanel && storesPromptPanel.querySelector('#gro-stores-prompt-input');
        if (!storesPromptInput) return;
        var typedStore = storesPromptInput.value.trim();
        if (!typedStore) { storesPromptInput.focus(); return; }
        el.disabled = true;
        groAddUsualStore(typedStore).catch(function () {
          showToast("Couldn't save that — try again.");
        }).then(function () { el.disabled = false; });
        return;
      }

      case 'stores-prompt-dismiss':
        el.disabled = true;
        groPost('/api/memory/stores-prompt-dismiss', {}).then(function () {
          groceryState.storesPromptDismissed = true;
          renderGrocery();
        }).catch(function () {
          el.disabled = false;
          showToast("Couldn't save that — try again.");
        });
        return;

      // ----- SORT -----
      case 'assign': {
        var toStore = el.dataset.store;
        el.disabled = true;
        var assignResult = null;
        groDo(function () {
          return groPost('/api/grocery-list/' + id + '/store', { store: toStore }).then(function (r) { assignResult = r; return r; });
        }, "Couldn't assign that — try again.").then(function (ok) {
          if (!ok) return;
          // The "Any" pill sends an empty store, same as never-sorted — see
          // anyStoreIds' declaration above. Mark it resolved (only on
          // success) so groUnsorted drops it from the queue exactly like a
          // real store pick already does.
          if (!toStore) groceryState.anyStoreIds[id] = true;
          groAdvanceSort();
          if (assignResult && assignResult.needs_confirmation) {
            groOfferRememberToast(assignResult.item, assignResult.store, id);
          }
        });
        return;
      }

      // "Wait, I already have this" is a natural thing to realize mid-sort.
      // Same backend path "Have it" always used; it takes the item off the
      // list into the kitchen and advances like a store pick does.
      case 'already-have':
        el.disabled = true;
        groDo(function () {
          return groPostEmpty('/api/grocery-list/' + id + '/already-have');
        }, "Couldn't move that to the kitchen — try again.").then(function (ok) {
          if (ok) groAdvanceSort();
        });
        return;

      // The item is getting picked up somewhere that isn't one of this
      // household's stores, so it comes off the sort queue the same way
      // "Have it" does, and shows up under "Already sorted this week" on
      // WRAP UP instead. Same /exclude route as ever.
      case 'triage-exclude':
        el.disabled = true;
        groDo(function () {
          return groPostEmpty('/api/grocery-list/' + id + '/exclude');
        }, "Couldn't update that — try again.").then(function (ok) {
          if (ok) groAdvanceSort();
        });
        return;

      // ----- TRIP -----
      case 'trip-toggle':
        groDo(function () {
          return groPost('/api/grocery-list/' + id + '/status', { status: 'in_cart' });
        }, "Couldn't update that — try again.");
        return;

      case 'uncheck':
        groDo(function () {
          return groPost('/api/grocery-list/' + id + '/status', { status: 'needed' });
        }, "Couldn't put that back — try again.");
        return;

      case 'toggle-incart':
        groceryState.inCartOpen = !groceryState.inCartOpen;
        renderGrocery();
        return;

      case 'stop-done': {
        var doneStore = groTripStore();
        var stops = groceryState.tripStops || [];
        var isLast = groceryState.tripIndex >= stops.length - 1;
        el.disabled = true;
        groDo(function () {
          return groFinishStore(doneStore);
        }, "Couldn't finish this stop — try again.").then(function (ok) {
          if (!ok) { el.disabled = false; return; }
          groceryState.inCartOpen = false;
          if (isLast) {
            goGroceryStep('wrap');
          } else {
            goGroceryStep('trip', { tripIndex: groceryState.tripIndex + 1 });
          }
        });
        return;
      }

      // ----- WRAP UP -----
      // "Couldn't find it" keeps the row exactly as it is (needed). There is
      // no note column on grocery_items, so nothing is written — this only
      // records that the question has been answered, so the list stops
      // asking about it in this wrap-up.
      case 'wrap-keep':
        groceryState.wrapKept[id] = true;
        renderGrocery();
        return;

      case 'wrap-else':
        el.disabled = true;
        groDo(function () {
          return groPostEmpty('/api/grocery-list/' + id + '/exclude');
        }, "Couldn't update that — try again.");
        return;

      case 'finish-trip':
        el.disabled = true;
        groDo(function () {
          return groFinishAnyRemainingCarts();
        }, "Couldn't finish the trip — try again.").then(function (ok) {
          if (!ok) { el.disabled = false; return; }
          groceryState.justFinishedTrip = true;
          groceryState.tripStops = null;
          groceryState.tripIndex = 0;
          groceryState.wrapKept = {};
          goGroceryStep('list');
          showToast('Stop saved — I’ll remember what you bought where');
        });
        return;

      case 'flag-toggle':
        groceryState.openFlagKey = groceryState.openFlagKey === el.dataset.key ? null : el.dataset.key;
        renderGrocery();
        return;

      // The shop-done handoff on LIST — see groShopDoneHtml.
      case 'shop-done-tonight':
        activateTab('week', true, { mealsView: 'cook', mealsFocus: true });
        return;

      case 'shop-done-later':
        groceryState.shopDoneHandoffDismissed = true;
        renderGrocery();
        return;

      // Undo either kind of "already have" decision via the one existing
      // pre-shop-undo endpoint. Restoring the grocery row to 'needed' is
      // identical either way; the backend also deletes the inventory row a
      // Have it/Already have action created, but only when that write didn't
      // merge into pre-existing stock (see undo_pre_shop_drop/
      // already_have_inventory_id).
      case 'undo-already-have':
        el.disabled = true;
        groDo(function () {
          return groPostEmpty('/api/grocery-list/' + id + '/pre-shop-undo');
        }, "Couldn't undo that — try again.");
        return;

      // Restores an "Elsewhere" exclusion — reuses the existing include
      // endpoint, which already had a backend undo but no UI control
      // anywhere until this row.
      case 'undo-elsewhere':
        el.disabled = true;
        groDo(function () {
          return groPostEmpty('/api/grocery-list/' + id + '/include');
        }, "Couldn't undo that — try again.");
        return;
    }
  }

  // One choice made: either there's another thing to sort, or the step is
  // over and the shopper lands back on the list with the good news. The
  // auto-return is what makes SORT a queue rather than a screen you have to
  // remember to leave.
  function groAdvanceSort() {
    var left = groceryState.data ? groUnsorted(groceryState.data).length : 0;
    if (left) {
      renderGrocery();
      return;
    }
    goGroceryStep('list');
    showToast('All sorted.');
  }

  // ---------- Hands-free voice ----------
  // The Shopper session, carried over from the page this replaces: same
  // trigger phrase, same three commands, same engine (voice-session.js, now
  // loaded by the shell). The blueprint lists hands-free as keeping its
  // current behaviour, so nothing here is redesigned — it just had to come
  // with the screen rather than be left behind on a page nothing links to.
  function groSetVoiceStatus(text) {
    var panel = groPanel();
    if (!panel) return;
    var el = panel.querySelector('#gro-voice');
    if (!el) return;
    if (!text) {
      groceryState.voiceLog = [];
      el.hidden = true;
      el.innerHTML = '';
      return;
    }
    groceryState.voiceLog.unshift(text);
    groceryState.voiceLog = groceryState.voiceLog.slice(0, 5);
    el.hidden = false;
    el.innerHTML =
      '<div class="gro-voice-head"><span class="gro-voice-dot"></span>Listening&hellip;</div>' +
      '<ul class="gro-voice-log">' +
        groceryState.voiceLog.map(function (t) { return '<li>' + escapeHtml(t) + '</li>'; }).join('') +
      '</ul>' +
      '<span class="gro-voice-note">Say &ldquo;hey Pomona&rdquo; plus a command, or tap the mic again to stop.</span>';
  }

  function groUpdateVoiceButton() {
    var panel = groPanel();
    if (!panel) return;
    var btn = panel.querySelector('#gro-mic-btn');
    if (!btn) return;
    btn.classList.toggle('listening', !!(groceryState.voiceSession && groceryState.voiceSession.isActive()));
  }

  function groVoiceFuzzyFind(text, candidates, getLabel) {
    var t = (text || '').trim().toLowerCase();
    if (!t || !candidates || !candidates.length) return null;
    for (var i = 0; i < candidates.length; i++) {
      if (getLabel(candidates[i]).trim().toLowerCase() === t) return candidates[i];
    }
    for (var j = 0; j < candidates.length; j++) {
      var label = getLabel(candidates[j]).trim().toLowerCase();
      if (label && (label.indexOf(t) !== -1 || t.indexOf(label) !== -1)) return candidates[j];
    }
    var words = t.split(/\s+/);
    var best = null, bestScore = 0;
    candidates.forEach(function (c) {
      var labelWords = getLabel(c).trim().toLowerCase().split(/\s+/);
      var score = words.filter(function (w) { return labelWords.indexOf(w) !== -1; }).length;
      if (score > bestScore) { bestScore = score; best = c; }
    });
    return bestScore > 0 ? best : null;
  }

  function groIsVoiceEndCommand(command) {
    return /\b(stop|cancel|exit|goodbye|end session|that'?s all|all done)\b/i.test(command) || /^done$/i.test(command.trim());
  }

  async function groFetchNeededFlat() {
    var res = await fetch('/api/grocery-list?status=needed');
    if (!res.ok) return [];
    var view = await res.json();
    var items = [];
    (view.sections || []).forEach(function (s) { items.push.apply(items, s.items); });
    return items;
  }

  async function groHandleVoiceCommand(command) {
    if (groIsVoiceEndCommand(command)) return { spoken: 'Ending hands-free.', endSession: true };

    if (/\b(what|which)\b/i.test(command) && /\b(store|section)\b/i.test(command)) {
      var afterKeyword = command.replace(/^.*?\b(store|section)\b\s*(?:is|does)?\s*/i, '').replace(/\bin\??$/i, '').trim();
      var items = await groFetchNeededFlat();
      var found = groVoiceFuzzyFind(afterKeyword, items, function (i) { return i.item; });
      if (!found) return { spoken: "I don't see that on the list." };
      var storeText = found.store && found.store.trim() ? found.store : 'no store assigned';
      return { spoken: found.item + ' is ' + found.category + ', ' + storeText + '.' };
    }

    if (/\b(check off|got|grabbed|found|bought|picked up)\b/i.test(command)) {
      var afterGot = command.replace(/^.*?\b(check off|got|grabbed|found|bought|picked up)\b\s*/i, '').trim();
      var gotItems = await groFetchNeededFlat();
      var gotItem = groVoiceFuzzyFind(afterGot, gotItems, function (i) { return i.item; });
      if (!gotItem) return { spoken: "I don't see that on the list." };
      try {
        await groPost('/api/grocery-list/' + gotItem.id + '/status', { status: 'purchased' });
      } catch (err) { return null; }
      loadGrocery();
      return { spoken: 'Got it, ' + gotItem.item + ' checked off.' };
    }

    if (/\b(add|put|need)\b/i.test(command)) {
      var name = command
        .replace(/^.*?\b(add)\b\s*/i, '')
        .replace(/\b(to the list|to my list|on the list|on my list)\b/gi, '')
        .replace(/^(put|we need|need)\s+/i, '')
        .trim();
      if (!name) return null;
      try {
        await groPost('/api/grocery-list/add', { item: name, quantity: '', category: 'other' });
      } catch (err) { return null; }
      loadGrocery();
      return { spoken: 'Added ' + name + ' to the list.' };
    }

    return null;
  }

  function groToggleVoice() {
    if (typeof window.createVoiceSession !== 'function') {
      showToast("Hands-free voice isn't available in this browser.");
      return;
    }
    if (groceryState.voiceSession && groceryState.voiceSession.isActive()) {
      groceryState.voiceSession.stop();
      return;
    }
    groceryState.voiceSession = window.createVoiceSession({
      onListeningChange: function (isListening) {
        groUpdateVoiceButton();
        if (!isListening) groSetVoiceStatus('');
      },
      onStatus: function (text) { groSetVoiceStatus(text); },
      onCommand: function (command) { return groHandleVoiceCommand(command); },
      onEnd: function () { groUpdateVoiceButton(); }
    });
    if (groceryState.voiceSession.isStandaloneIOS()) {
      groSetVoiceStatus('Heads up: hands-free voice can be unreliable in the installed home-screen app on iOS — if it doesn’t seem to hear you, try this from a regular Safari tab instead.');
    }
    groceryState.voiceSession.start();
    groUpdateVoiceButton();
  }

  // ---------- Kitchen (Stage 2 slice 3, built to InnKitchen) ----------
  //
  // The household's standing knowledge and its settings, and nothing that
  // is urgent. Three things follow from that and are deliberate:
  //
  //   - There is NO apricot action anywhere on this screen. The blueprint
  //     is explicit: "Kitchen has no apricot button at all — nothing there
  //     is urgent, and giving it one would be a lie about what the screen
  //     is for." The hero's "Read it back" is spruce-raised, not apricot.
  //   - Inventory is the quiet tile: muted icon, muted sub-line, no count
  //     badge. Inventory is deferred as policy — background only, never
  //     something the core loop asks the household to maintain — so it must
  //     not look like work waiting to be done.
  //   - Cooking is NOT here. It moved to the Meals tab's Cook state; this
  //     hub used to carry a "Cooking tonight" card whose buttons re-pointed
  //     this tab's iframe at cooker.html.
  //
  // The one hero is the household itself: what the app has learned, how
  // much of it there is, and a way to read it back.
  var kitchenState = { memory: null, facts: null, inventory: null, loadError: false };

  var KITCHEN_ICONS = {
    person:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 20.5V17a7 7 0 0 1 14 0v3.5"/><circle cx="12" cy="7" r="3.2"/></svg>',
    fridge:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5h12v17H6z"/><path d="M6 10h12"/><path d="M9 6.5v1.5"/><path d="M9 13v1.5"/></svg>',
    storefront:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 9.5h14V19a1.8 1.8 0 0 1-1.8 1.8H6.8A1.8 1.8 0 0 1 5 19z"/><path d="M3.5 5.5h17v4h-17z"/></svg>',
    camera:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5h4l1.5-2.5h6L16.5 8.5h4V19a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19z"/><circle cx="12" cy="13.5" r="3.4"/></svg>'
  };

  function kitchenPanel() { return panels['kitchen']; }
  function kitchenIsBuilt() { var p = kitchenPanel(); return !!(p && p.dataset.built); }

  function buildKitchenPanel(panel) {
    panel.innerHTML =
      '<div class="kitchen-content">' +
        '<div class="kit-titlerow">' +
          '<span class="kit-eyebrow">Your household</span>' +
          '<span class="kit-hairline"></span>' +
        '</div>' +
        '<h1 class="kit-title">Kitchen</h1>' +
        '<div class="kit-hero" id="kit-hero"></div>' +
        '<div class="kit-body" id="kit-body"></div>' +
      '</div>';
    panel.addEventListener('click', onKitchenClick);
    loadKitchen();
  }

  // Three reads, none of which blocks the others: the memory summary (usual
  // stores, members, and growth_count_this_month — a counter this app
  // already keeps for exactly this "you've taught me N things" line), the
  // freeform facts (which fill the Taste and Rhythm chips), and the
  // inventory summary for the quiet tile.
  async function loadKitchen() {
    if (!kitchenIsBuilt()) return;
    try {
      var results = await Promise.all([
        fetch('/api/memory').then(function (r) { return r.ok ? r.json() : null; }),
        fetch('/api/facts').then(function (r) { return r.ok ? r.json() : null; })
      ]);
      if (!results[0]) throw new Error('memory lookup failed');
      kitchenState.memory = results[0];
      kitchenState.facts = (results[1] && results[1].facts) || [];
      kitchenState.loadError = false;
    } catch (err) {
      console.warn('Kitchen lookup failed:', err);
      kitchenState.loadError = true;
    }
    renderKitchen();
    // The inventory line is a nicety on a tile that is quiet by policy — it
    // renders "what's on hand" and fills in a moment later if the counts
    // arrive. It must never hold up the hero.
    loadKitchenInventory();
  }

  function refreshKitchenPanel() {
    if (kitchenIsBuilt()) loadKitchen();
  }

  // "N to use soon · N running low". Use-soon is the real
  // expiring-within-4-days endpoint; "running low" has no stored threshold
  // anywhere in this app, so it stays what the old hub made it — a light
  // client-side read of a leading quantity <= 1 — rather than a fabricated
  // stat. Carried over unchanged from static/kitchen.html.
  async function loadKitchenInventory() {
    try {
      var pair = await Promise.all([
        fetch('/api/inventory/expiring?days=4'),
        fetch('/api/inventory')
      ]);
      var expiring = pair[0].ok ? ((await pair[0].json()).items || []) : [];
      var lowCount = 0;
      if (pair[1].ok) {
        ((await pair[1].json()).sections || []).forEach(function (s) {
          (s.items || []).forEach(function (it) {
            var m = /^\s*([\d.]+)/.exec(it.quantity || '');
            if (m && parseFloat(m[1]) <= 1) lowCount++;
          });
        });
      }
      kitchenState.inventory = { expiring: expiring.length, low: lowCount };
    } catch (err) {
      kitchenState.inventory = null;
    }
    var sub = kitchenPanel() && kitchenPanel().querySelector('#kit-inv-sub');
    if (sub) sub.textContent = kitchenInventoryLine();
  }

  function kitchenInventoryLine() {
    var inv = kitchenState.inventory;
    if (!inv) return 'What’s on hand';
    return inv.expiring + ' to use soon · ' + inv.low + ' running low';
  }

  // Loop Board "Onboarding: household rhythm without traditional
  // assumptions" flagged this as a known gap: the structured rhythm answers
  // (lunch location per person, meals eaten together, who cooks, when
  // dinner lands, when the week should be ready, leftovers stance — see
  // app/tools/rhythm.py get_household_rhythm) live separately from the
  // freeform facts table's category='rhythm' notes, and only the freeform
  // notes were being counted here. A household that answered every real
  // rhythm question but never left a freeform note was under-reporting as
  // zero. Counts one "thing known" per answered structured fact: one per
  // household member with a standing lunch-location answer, plus one each
  // for meals_together/cooking_role/dinner_window/planning_anchor/
  // leftovers_stance when set — on top of the freeform notes, not instead
  // of them.
  function structuredRhythmCount(mem) {
    var rhythm = (mem && mem.rhythm) || {};
    var count = 0;
    var lunchByPerson = rhythm.lunch_location || {};
    Object.keys(lunchByPerson).forEach(function (name) {
      if (lunchByPerson[name] && lunchByPerson[name].standing) count++;
    });
    if (rhythm.meals_together) count++;
    if (rhythm.cooking_role) count++;
    if (rhythm.dinner_window) count++;
    if (rhythm.planning_anchor) count++;
    if (rhythm.leftovers_stance) count++;
    return count;
  }

  function kitchenCounts() {
    var mem = kitchenState.memory || {};
    var facts = kitchenState.facts || [];
    function factsIn(cat) {
      return facts.filter(function (f) { return f.category === cat; }).length;
    }
    // People counts what the People tab actually shows: the household's
    // members plus anything freeform recorded about them. Every chip is
    // "how much this tab holds", so tapping one lands somewhere that
    // matches the number.
    return {
      people: (mem.members || []).length + factsIn('people'),
      taste: factsIn('taste'),
      rhythm: factsIn('rhythm') + structuredRhythmCount(mem),
      stores: (mem.usual_stores || []).length
    };
  }

  function renderKitchen() {
    var panel = kitchenPanel();
    if (!panel) return;
    var hero = panel.querySelector('#kit-hero');
    var body = panel.querySelector('#kit-body');
    if (!hero || !body) return;

    if (kitchenState.loadError || !kitchenState.memory) {
      hero.innerHTML = '<p class="kit-hero-error">Couldn’t load what I know about your household right now.' + snwLink(true) + '</p>';
      body.innerHTML = '';
      return;
    }

    var counts = kitchenCounts();
    var total = counts.people + counts.taste + counts.rhythm + counts.stores;
    var taught = kitchenState.memory.growth_count_this_month || 0;

    // The headline says where the app is with this household, and the
    // Newsreader line under it says what changed lately. Both are read off
    // real numbers. The mockup's "Six weeks in" wants a household start
    // date that nothing in this app exposes, so it is not written here —
    // inventing a tenure would be inventing history.
    var headline = total
      ? 'Getting the hang of you'
      : 'Tell me about your household';
    var note = total
      ? (taught
          ? (taught === 1 ? 'one new thing learned this month' : taught + ' new things learned this month')
          : 'nothing new this month — tell me anything and it lands here')
      : 'nothing on record yet — the more I know, the fewer swaps you’ll make';

    hero.innerHTML =
      '<div class="kit-hero-top">' +
        '<span class="kit-hero-chip">What we know</span>' +
        '<span class="kit-hero-rule"></span>' +
        '<span class="kit-hero-icon">' + KITCHEN_ICONS.person + '</span>' +
      '</div>' +
      '<div class="kit-hero-line">' +
        '<h2 class="kit-hero-headline">' + escapeHtml(headline) + '</h2>' +
        '<p class="kit-hero-note">' + escapeHtml(note) + '</p>' +
      '</div>' +
      '<div class="kit-chips">' +
        kitChip('People', counts.people, 'memory') +
        kitChip('Taste', counts.taste, 'taste') +
        kitChip('Rhythm', counts.rhythm, 'rhythm') +
        kitChip('Stores', counts.stores, 'stores') +
      '</div>' +
      // Spruce-raised, not apricot. See the note at the top of this section.
      '<button type="button" class="kit-hero-action" data-kit="sheet" data-sheet="memory">' +
        '<span>Read it back</span>' + ICONS.arrow +
      '</button>';

    var stores = kitchenState.memory.usual_stores || [];
    body.innerHTML =
      '<div class="kit-tiles">' +
        // Quiet by policy: muted stroke, muted sub, no badge.
        '<button type="button" class="kit-tile kit-tile-quiet" data-kit="sheet" data-sheet="inventory">' +
          '<span class="kit-tile-icon">' + KITCHEN_ICONS.fridge + '</span>' +
          '<span class="kit-tile-title">Inventory</span>' +
          '<span class="kit-tile-sub" id="kit-inv-sub">' + escapeHtml(kitchenInventoryLine()) + '</span>' +
        '</button>' +
        '<button type="button" class="kit-tile" data-kit="sheet" data-sheet="stores">' +
          '<span class="kit-tile-icon kit-tile-icon-warm">' + KITCHEN_ICONS.storefront + '</span>' +
          '<span class="kit-tile-title">Stores</span>' +
          '<span class="kit-tile-sub">' +
            (stores.length ? escapeHtml(stores.join(', ')) : 'Where things come from') +
          '</span>' +
        '</button>' +
      '</div>' +
      // Carried over from the old hub unchanged, including the fact that
      // nothing is built behind it yet — it is an entry point that says so.
      '<div class="kit-worth" data-kit="worth">' +
        '<div class="kit-worth-top">' +
          '<span class="kit-worth-icon">' + KITCHEN_ICONS.camera + '</span>' +
          '<span class="kit-worth-eyebrow">Worth doing sometime</span>' +
        '</div>' +
        '<p class="kit-worth-text">Scan a fridge photo, so I stop suggesting what you already have.</p>' +
      '</div>' +
      // Quiet, and last: a way out of a bad moment, not a chore.
      snwTile();
  }

  function kitChip(label, count, sheet) {
    return '<button type="button" class="kit-chip" data-kit="sheet" data-sheet="' + sheet + '">' +
      escapeHtml(label) + '<span class="kit-chip-num">' + count + '</span></button>';
  }

  function onKitchenClick(e) {
    var target = e.target.closest('[data-kit]');
    if (!target) return;
    var what = target.getAttribute('data-kit');
    if (what === 'sheet') {
      var key = target.getAttribute('data-sheet');
      // The Taste and Rhythm chips are tabs of What we know, not sheets of
      // their own — same page, opened on the tab whose number was tapped.
      if (key === 'taste' || key === 'rhythm') openKitchenSheet('memory', key);
      else openKitchenSheet(key);
      return;
    }
    if (what === 'worth') {
      showToast('Not built yet — tell me in the ask bar what’s in the fridge and I’ll take it from there.');
    }
  }

  // ---------- Kitchen entry sheets ----------
  // Same scrim/sheet pattern as the ask and week sheets, and the same
  // "one open at a time" rule. The sheet supplies the header and the way
  // back; the page inside it supplies no chrome of its own (its own back
  // link hides itself in a frame — static/embedded-page.js).
  var kitSheetScrim = document.getElementById('kit-sheet-scrim');
  var kitSheetEl = document.getElementById('kit-sheet');
  var kitSheetOpen = null;

  function openKitchenSheet(key, tab) {
    var meta = KITCHEN_SHEETS[key];
    if (!meta || !kitSheetEl) return;
    closeAskSheet();
    closeWeekSheet();
    var hash = tab || meta.hash;
    var frame = document.getElementById('kit-sheet-frame');

    // Keep the loaded document, and ask it to change view, rather than
    // reloading — so reopening a sheet does not throw away a scroll position
    // or a half-typed edit for no reason.
    //
    // What this must NOT do is decide "already showing the right thing" from
    // the URL. The page's own tab strip moves between tabs without touching
    // its hash, so after tapping People inside the sheet the src still read
    // `#stores` while People was on screen — and the Stores tile, seeing a
    // matching src, reopened on People under a header saying "Stores". The
    // page therefore exposes showKitchenTab(), which is authoritative about
    // what it is actually displaying.
    if (frame.dataset.page !== meta.src) {
      frame.dataset.page = meta.src;
      frame.setAttribute('src', meta.src + (hash ? '#' + hash : ''));
    } else if (hash) {
      var told = false;
      try {
        var win = frame.contentWindow;
        if (win && typeof win.showKitchenTab === 'function') {
          win.showKitchenTab(hash);
          told = true;
        }
      } catch (err) {
        // Same-origin, so this should not throw; if it ever does, fall back
        // to a reload rather than showing the wrong view under a confident
        // header.
        console.warn('Kitchen sheet tab handoff failed:', err);
      }
      if (!told) frame.setAttribute('src', meta.src + '#' + hash);
    }
    document.getElementById('kit-sheet-title').textContent = meta.title;
    frame.title = meta.title;
    kitSheetOpen = key;
    kitSheetScrim.hidden = false;
    kitSheetEl.hidden = false;
  }

  function closeKitchenSheet() {
    if (!kitSheetScrim) return;
    kitSheetScrim.hidden = true;
    kitSheetEl.hidden = true;
    // Anything edited in there changes what the hub counts, so re-read on
    // the way out. This is the sheet's half of the freshness policy.
    if (kitSheetOpen) refreshKitchenPanel();
    kitSheetOpen = null;
  }

  if (kitSheetScrim) {
    kitSheetScrim.addEventListener('click', closeKitchenSheet);
    document.getElementById('kit-sheet-handle').addEventListener('click', closeKitchenSheet);
    document.getElementById('kit-sheet-close').addEventListener('click', closeKitchenSheet);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !kitSheetEl.hidden) closeKitchenSheet();
    });
  }

  // Where an action card or a notification says "View" and names an href
  // rather than a tab.
  //
  // Two of those hrefs now have a home inside the app. `/memory` is what
  // every household/preferences write points at (app/main.py's
  // _MEMORY_HREF_TOOLS) — its comment says "no shell tab shows this yet
  // (Kitchen's 'What we know' absorbs it in a later step)", and this is
  // that step. Sending someone out of the shell to see a store they just
  // added by voice was the last full page navigation left in the app.
  //
  // Anything else still navigates: /onboarding and /plan-week are focused
  // sequences that deliberately live outside the tab bar.
  var HREF_AS_SHEET = { '/memory': 'memory', '/inventory': 'inventory' };
  // One normalisation, used by both callers. They used to differ — this one
  // trimmed a trailing slash and refreshStaleTabsFromActions matched the raw
  // string — so a '/memory/' href would have opened the sheet but not
  // refreshed the hub behind it.
  function hrefSheetKey(href) {
    return HREF_AS_SHEET[(href || '').replace(/\/+$/, '') || '/'] || null;
  }
  function followActionHref(href) {
    var sheet = hrefSheetKey(href);
    if (sheet) {
      activateTab('kitchen', true);
      openKitchenSheet(sheet);
      return;
    }
    window.location.href = href;
  }

  // The desktop rail's two shortcuts open the same sheets. They used to be
  // <a href> full page navigations out of the shell — see shell.html.
  document.querySelectorAll('[data-rail-sheet]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      activateTab('kitchen', true);
      openKitchenSheet(btn.getAttribute('data-rail-sheet'));
    });
  });

  // ---------- Week (Step 4, rebuilt for design_handoff_home_manager
  // option 6a) ----------
  // Backed by GET /api/week-menu (app/tools.get_week_menu) — always 7 days,
  // three slots a day, each slot null or {title, meta, source}, plus
  // (new) `dinner_suggestions` on any today-or-future day whose dinner is
  // still empty. Desktop (>=1100px, its own breakpoint, distinct from the
  // shell's 1024px rail breakpoint) keeps the existing 7-column x 3-row
  // grid unchanged — the home-manager package's file table lists This Week
  // as phone-only, and the grid already serves "see the whole week" well on
  // a big screen, so it wasn't touched. Mobile (<1100px) is the option-6a
  // rebuild: a day rail, one day's card in full, and a "whole week" row
  // that opens a bottom sheet with all 21 meals — replacing the old
  // seven-stacked-cards scroll per the package's explicit call-out that 6a
  // replaces it.
  //
  // Judgment calls:
  //   - Day rail "needs a decision" / day card empty-dinner suggestions
  //     reuse the same _suggest_quick_dinners() list and the same
  //     POST /api/needs-you/dinner fill endpoint the Today needs-you band
  //     uses — it's generic over any date, not just the nearest 48h gap.
  //   - Breakfast/lunch have no fill flow designed anywhere in this
  //     package (only dinner gets the "Nothing yet" + suggestion-row
  //     treatment) — an empty breakfast/lunch renders as plain muted
  //     "Not planned yet" text, not a fake tappable "Pick" that would
  //     dead-end (the old build's version of this dead-ended into Today,
  //     which has no breakfast/lunch decision flow either).
  //   - "Cook this" needs a real cook-mode destination; Kitchen's cook mode
  //     only knows how to start "tonight's" meal, not an arbitrary future
  //     date's. So "Cook this" only appears on *today's* card (paired with
  //     "Swap it"); a filled future day gets "Swap it" alone rather than a
  //     "Cook this" that would open the wrong day's steps.
  //   - The mock's per-card reasons ("tee-ball night") need a calendar/
  //     event signal this app doesn't have — omitted rather than invented.
  var SLOT_LABELS = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };
  var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];
  // pendingDayFocus: {date, slot} set by the chat's "See your week" chip,
  // drained by applyPendingDayFocus once the days for that week are
  // actually loaded. Null the rest of the time.
  // step / mealSlot: which of the three Meals steps is showing and, on the
  // Meal step, which slot of the selected day. Both are page state, not
  // routes — a refresh starts at 'week' by design (see the step machine
  // below), because the week is the answer and a deep step is where you
  // happened to be, not where you asked to land.
  var weekState = {
    selectedIndex: null, days: [], data: null, pendingDayFocus: null,
    step: 'week', mealSlot: 'dinner'
  };

  async function buildWeekPanel(panel) {
    panel.innerHTML =
      '<div class="week-content">' +
        // Plan | Cook. Cooking is a second state of this tab, not a tab of
        // its own and not a page: it is the same dataset as the week panel
        // with the recipes opened up, and two tabs over one dataset is how
        // panels drift apart (NavBlueprint, "Where cooking lives").
        //
        // Judgment call: InnCooker draws this control pouring into the
        // spruce hero directly beneath it, the way the Meals day rail
        // does. That only works if the control is the last thing before
        // the hero, which it cannot be in both states — Plan has its "This
        // week" framing and its day rail in between. So it is the plain
        // segmented control the Grocery screen already shipped, in one
        // place, identical in both states, rather than a decoration that
        // would have to be built twice and would sit differently in each.
        '<div class="meals-seg" id="meals-seg" role="tablist">' +
          '<button type="button" class="meals-seg-btn active" data-meals-view="plan" role="tab" aria-selected="true">Plan</button>' +
          '<button type="button" class="meals-seg-btn" data-meals-view="cook" role="tab" aria-selected="false">' +
            ICONS.flame + '<span>Cook</span>' +
          '</button>' +
        '</div>' +
        '<div id="week-cook-view" hidden></div>' +
        '<div id="week-plan-view">' +
        // The two bands that belong to the WEEK rather than to any day of
        // it, above the card and hidden on the Day and Meal steps (see
        // renderMealsStep). The review band is a draft's decision — first
        // child so it needs no scroll, which is the whole of the "land on
        // the review moment" change. The approve row is its other half:
        // the receipt, the freezer check and the cook-ahead offer once the
        // week is settled.
        '<div id="week-review-band"></div>' +
        '<div id="week-approve-row"></div>' +
        // Week -> Day -> Meal. One container, three renderings; see
        // renderMealsStep.
        '<div id="week-steps"><div class="menu-loading">Loading your week&hellip;</div></div>' +
        '</div>' +
      '</div>';

    panel.querySelectorAll('.meals-seg-btn').forEach(function (btn) {
      btn.addEventListener('click', function () { setMealsView(btn.getAttribute('data-meals-view')); });
    });

    // /plan-week hands back with ?drafted=<Monday>. Without honouring it,
    // Meals asks for "the current plan" and gets whichever week contains
    // TODAY — so drafting next week ended with the week just built
    // invisible behind this one, along with its headline and its Approve
    // card. Same failure class as the "chat plans a week the tab doesn't
    // show" bug in CLAUDE.md's decision log.
    var drafted = new URLSearchParams(window.location.search).get('drafted');
    if (drafted) weekState.showWeekStart = drafted;

    await loadWeekMenu(panel);

    if (drafted) {
      showToast('Here’s your week — change anything before you approve it.');
    }
    // FIRST_RUN.md step 5: onboarding redirects here with ?firstplan=1
    // right after generating the household's first real week — land on
    // This Week (already the case) and show the arrival toast once, then
    // scrub the param so a refresh doesn't re-show it.
    if (window.location.search.indexOf('firstplan=1') !== -1) {
      showToast("Here's a first pass — change anything and I'll re-plan around it.");
    }
    // The reveal's "or tweak it with me" quiet link (Loop Board "Redesign
    // the post-onboarding first sample week screen") redirects here with
    // ?tweak=1 instead of ?firstplan=1 — same landing, but straight into
    // the ask sheet with a prefill rather than a toast, since the person
    // already said they want to change something rather than just look.
    if (window.location.search.indexOf('tweak=1') !== -1) {
      openAskSheet("Let's tweak my first sample week — ");
    }
    if (drafted || window.location.search.indexOf('firstplan=1') !== -1 || window.location.search.indexOf('tweak=1') !== -1) {
      var cleanUrl = window.location.pathname;
      window.history.replaceState({ tab: 'week' }, '', cleanUrl);
    }
  }

  // Where this household's "this week" starts, from the planning_anchor
  // they gave at onboarding (see tools.suggest_planning_period). Fetched
  // once per page load and cached: it is a standing preference, not
  // per-week state, and the Meals card re-renders often enough that a
  // request per render would be noise. Null until it arrives — every
  // reader falls back to the Monday, which is what this did before.
  var planningPeriodDefault = null;

  async function loadPlanningPeriodDefault() {
    if (planningPeriodDefault) return planningPeriodDefault;
    try {
      var res = await fetch('/api/week/planning-period');
      if (!res.ok) throw new Error('planning period lookup failed');
      planningPeriodDefault = await res.json();
    } catch (err) {
      // Deliberately silent and non-blocking. This only chooses which day
      // a default button offers; failing to get it must not stop the
      // Meals tab rendering, and a toast about it would be noise about
      // something the household never asked for.
      console.warn('Planning period default unavailable, falling back to Monday:', err);
    }
    return planningPeriodDefault;
  }

  async function loadWeekMenu(panel) {
    try {
      // Awaited before the render below so the "Plan this week" button is
      // right the first time it is painted, rather than saying Monday and
      // then silently changing to today under the household's thumb.
      await loadPlanningPeriodDefault();
      // weekState.showWeekStart pins Meals to one specific week rather than
      // "whichever contains today" — set when /plan-week hands back a week
      // it just drafted. It survives reloads of the panel (a swap, an
      // approval) so the household stays on the week they're working on.
      var url = '/api/week-menu';
      if (weekState.showWeekStart) {
        var planId = await planIdForWeek(weekState.showWeekStart);
        if (planId) url += '?weekly_plan_id=' + encodeURIComponent(planId);
      }
      var res = await fetch(url);
      if (!res.ok) throw new Error('week-menu lookup failed');
      var data = await res.json();
      renderWeekMenu(panel, data);
      // Plan and Cook are two renderings of one week, so anything that
      // reloads the plan reloads the cook view with it — a swap, an
      // approval, a chat turn, a reset. Doing it here rather than at each
      // of those six call sites is the point: a seventh call site added
      // later gets the behaviour for free instead of being the next thing
      // that goes stale. It is a no-op until someone has actually opened
      // Cook, so this costs nothing for a household that never does.
      refreshCookView();
    } catch (err) {
      console.warn('Week menu lookup failed:', err);
      panel.querySelector('#week-steps').innerHTML = '<div class="menu-loading">Couldn\'t load your week right now.</div>';
    }
  }

  async function planIdForWeek(weekStart) {
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(weekStart) + '/intake');
      if (!res.ok) return null;
      var prefill = await res.json();
      return prefill.plan_id || null;
    } catch (err) {
      // Falling back to "the current week" is a worse view, not a broken
      // one — better than failing the whole panel over which week to show.
      console.warn('Week lookup failed:', err);
      return null;
    }
  }

  function dayName(dateStr, opts) {
    // Parse as local, not UTC, so "today" compares correctly regardless of timezone offset.
    var d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString('en-US', opts);
  }

  function todayLocalStr() {
    // Build today's date from local fields, not toISOString() (which is UTC) —
    // otherwise "today" is wrong for anyone whose local date has already rolled
    // over past midnight while UTC's date hasn't yet.
    var d = new Date();
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + m + '-' + day;
  }

  function classifyDay(day, todayStr) {
    // A slot with no entry at all, which after a real generation shouldn't
    // happen — every slot is planned, planned_empty or open. It still has
    // to be handled: plenty of weeks predate the guarantee.
    var hasEmpty = WEEK_SLOTS.some(function (s) { return !day[s]; });
    // A slot the app deliberately handed back. This is a decision even
    // though the slot isn't empty — and a planned_empty night is NOT one,
    // which is the whole point of it being its own state.
    var hasOpen = WEEK_SLOTS.some(function (s) { return day[s] && day[s].state === 'open'; });
    var isToday = day.date === todayStr;
    var isPast = day.date < todayStr;
    // A past day's empty slot isn't an open decision any more — don't flag
    // it urgent or offer "Pick" for something that already happened.
    var needsDecision = !isPast && (hasEmpty || hasOpen);
    var status = isToday ? 'Tonight' : (isPast ? 'Served' : (needsDecision ? 'Needs you' : ''));
    var ribbon = isToday ? 'today' : (needsDecision ? 'urgent' : '');
    return { hasEmpty: hasEmpty, needsDecision: needsDecision, isToday: isToday, isPast: isPast, status: status, ribbon: ribbon };
  }

  // ---------- derived trip states (WeekWithTrip mock) ----------
  // Per Emily's progressive-disclosure decision these appear ONLY when a
  // trip or an override has created the need — an ordinary week's slots
  // carry no `need` at all and render exactly as they always have.
  var NEED_LABELS = { quick: 'Quick', ready_made: 'Ready-made' };
  var READY_CHECK =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7"/></svg>';

  function needBadgeHtml(entry) {
    if (!entry || !entry.need || !NEED_LABELS[entry.need]) return '';
    var who = (entry.need_for_names || []).length
      ? ' for ' + joinList(entry.need_for_names)
      : '';
    return '<span class="need-badge">' + escapeHtml(NEED_LABELS[entry.need] + who) + '</span>';
  }

  function joinList(names) {
    if (names.length <= 1) return names.join('');
    if (names.length === 2) return names[0] + ' and ' + names[1];
    return names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1];
  }

  // An away slot is quiet on purpose: it states what it is and offers
  // nothing, because offering to change it is exactly what the away state
  // exists to prevent. Distinct from the generic "Out — nothing to cook"
  // an out-night gets, since this one can also name whose trip caused it.
  function awayLineFor(entry) {
    return entry && entry.need === 'away'
      ? 'Away — nothing planned, nothing bought.'
      : (entry && entry.title) || '';
  }

  // The recommendation surface. Everything shown here comes from what the
  // engine actually stored: the sentence, the alternative, and whether it
  // has been confirmed. Nothing is invented client-side — when the engine
  // has nothing to recommend, this renders nothing rather than a plausible
  // guess.
  function readyMadeHtml(day) {
    var entry = day.dinner;
    if (!entry || entry.need !== 'ready_made') return '';
    var rec = entry.recommendation;
    if (!rec) {
      return '<div class="ready-made">' +
        '<div class="ready-made-ask">' + escapeHtml(entry.need_reason ||
          'First one back — I’ll cover this with something already made.') + '</div>' +
        '<div class="ready-made-none">I haven’t got anything to earmark for this yet — ' +
          'nothing batch-cooked earlier and nothing in the freezer.</div>' +
      '</div>';
    }
    if (rec.confirmed) {
      return '<div class="ready-made">' +
        '<div class="ready-made-done">' + READY_CHECK +
          '<span>' + escapeHtml(capitalizeFirst(rec.label)) + ' — settled.</span></div>' +
      '</div>';
    }
    return '<div class="ready-made">' +
      '<div class="ready-made-ask">' + escapeHtml(rec.sentence) + '</div>' +
      '<div class="ready-made-actions">' +
        '<button type="button" class="ready-made-confirm" data-date="' + day.date + '">' +
          READY_CHECK + '<span>Confirm</span></button>' +
        '<button type="button" class="ready-made-other" data-date="' + day.date + '">Choose differently</button>' +
      '</div>' +
      (rec.alternative
        ? '<div class="ready-made-alt">' + escapeHtml(rec.alternative.sentence) + '</div>'
        : '') +
    '</div>';
  }

  function capitalizeFirst(s) { return String(s).charAt(0).toUpperCase() + String(s).slice(1); }

  async function confirmReadyMade(panel, date, confirmed) {
    var week = weekState.data && weekState.data.week_start_date;
    if (!week) return;
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(week) + '/slot-recommendation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: date, slot: 'dinner', confirmed: confirmed })
      });
      if (!res.ok) throw new Error('confirm failed');
      await loadWeekMenu(panel);
    } catch (err) {
      console.warn('Ready-made confirmation failed:', err);
      showToast('I couldn’t save that just now — try again in a moment.');
    }
  }

  // Quick-dinner-pick suggestions (day.dinner_suggestions, from
  // _suggest_quick_dinners) remain turned off per household feedback: the
  // "Nothing yet" empty state stays, without the inline pick rows. The
  // backend plumbing (get_week_menu's dinner_suggestions, POST
  // /api/needs-you/dinner) and fillWeekDinner below are untouched, so this
  // is still easy to turn back on.

  async function fillWeekDinner(panel, mealDate, meal) {
    // Same confirm as the Today card — see resolveDinnerDecision.
    var addIngredients = await askAboutIngredients(meal);
    if (addIngredients === null) return;
    try {
      var res = await fetch('/api/needs-you/dinner', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: mealDate, meal: meal, add_ingredients: addIngredients })
      });
      if (!res.ok) throw new Error('dinner resolve failed');
      var fillData = await res.json();
      showToast(dinnerPlannedToast(meal, fillData));
      // Today's needs-you band and its timeline may cover this same date —
      // if Today has already been built this session, refresh it too so
      // the two tabs never show stale, contradictory states side by side.
      var todayPanel = panels['today'];
      if (todayPanel && todayPanel.dataset.built) loadNeedsYou(todayPanel);
      refreshTodayMoves();
      await loadWeekMenu(panel);
    } catch (err) {
      console.warn('Week dinner fill failed:', err);
      alert('Could not save that pick right now — try again in a moment.');
    }
  }

  // ---------- Meals: Week -> Day -> Meal (Emily's approved design, 2026-09-08) ----
  //
  // Meals answers one question — "what are we eating this week, and is it
  // settled?" — in three steps, and the whole redesign is that they are
  // STEPS rather than a stack. The root used to carry a review band, a
  // framing line, a day rail, a day card, a whole-week row, an approve row,
  // a reset link, a Plan-a-week card with its own picker and a setup link,
  // all at once; every one of them was a different question asked in the
  // same breath. Now:
  //
  //   WEEK  the answer, in one card: seven rows, three lines each, and a
  //         badge saying whether the week is settled. Rare actions live
  //         behind "More".
  //   DAY   one day, three equal cards — no hero, because a day is three
  //         meals and pretending one of them is the day was the old
  //         screen's other problem.
  //   MEAL  one meal in full: the plate, cooking ahead, why this night.
  //
  // They are states of the Meals tab, exactly as Plan/Cook are (NavBlueprint:
  // "never a new page with its own header"). Back links and the browser's
  // own back gesture step out one level (see pushMealsStepHistory and the
  // popstate handler at the top of this file); a refresh lands on WEEK,
  // because weekState.step starts there and nothing restores it.

  var WEEK_BADGES = { set: 'SET', draft: 'DRAFT', none: 'NOTHING YET' };

  function weekPlanState(data) {
    if (!data || !data.weekly_plan_id || !(data.days || []).length) return 'none';
    return data.status === 'approved' ? 'set' : 'draft';
  }

  // The dish as it should READ on a card, which for a made-ahead night is
  // the dish itself ("Egg White Bites") and not the whole sentence the
  // headline makes ("Made ahead — Sunday's Egg White Bites"). The sentence
  // is still true and still shown — it just belongs in the eyebrow, where
  // "made ahead Sunday" is one short fact rather than half the name.
  function mealDisplayName(entry) {
    if (!entry) return '';
    if (entry.leftover_from && entry.leftover_from.meal) return entry.leftover_from.meal;
    return entry.title || '';
  }

  // "4 cooks, 3 made ahead" — the shape of the week in one clause. Takeout
  // is deliberately neither: nobody cooks it and nobody made it ahead, and
  // counting it as a cook would overstate the week's work.
  function weekCountsLabel(days) {
    var cooks = 0, ahead = 0;
    (days || []).forEach(function (day) {
      WEEK_SLOTS.forEach(function (slot) {
        var entry = day[slot];
        if (!entry || entry.state !== 'planned') return;
        if (entry.source === 'leftovers') ahead++;
        else if (entry.source !== 'takeout') cooks++;
      });
    });
    var parts = [];
    if (cooks) parts.push(cooks + (cooks === 1 ? ' cook' : ' cooks'));
    if (ahead) parts.push(ahead + ' made ahead');
    return parts.join(', ');
  }

  function weekStepHeadHtml(data, days) {
    var state = weekPlanState(data);
    var dayCount = data.day_count || days.length || 7;
    // periodRangeLabel is the fallback only; the server's own week_label
    // already knows the real span of a custom period.
    var range = data.week_label ||
      (data.week_start_date ? periodRangeLabel(data.week_start_date, dayCount) : '');
    // Seven days is "this week" because that is what a week is called. A
    // household on a different rhythm is not living a week, so its own
    // dates are the title and the subtitle doesn't repeat them.
    var isWeek = dayCount === 7;
    var title = isWeek || !range ? 'This week' : range;
    var sub = [];
    if (isWeek && range) sub.push(range);
    var counts = weekCountsLabel(days);
    if (counts) sub.push(counts);
    if (data.trip_summary) sub.push(data.trip_summary);
    return '<div class="wk-head">' +
      '<div class="wk-head-row">' +
        '<h1 class="wk-title">' + escapeHtml(title) + '</h1>' +
        '<span class="wk-state is-' + state + '">' + WEEK_BADGES[state] + '</span>' +
      '</div>' +
      (sub.length ? '<div class="wk-sub">' + escapeHtml(sub.join(' · ')) + '</div>' : '') +
      // A component-based household has no real day mapping underneath —
      // get_week_menu spreads its pool across the days so this screen has
      // something to show, and says so rather than presenting a suggested
      // arrangement as a schedule.
      (data.menu_is_suggested
        ? '<div class="week-suggested-note">One example arrangement — your household assembles freely.</div>'
        : '') +
    '</div>';
  }

  // One line of one row. The dot is the whole legend: apricot = somebody
  // cooks, celadon = it is already made, grey = nothing to do. Names
  // truncate to one line in CSS rather than wrapping — Emily's call, seven
  // days truncated beats five days in full, because the question this card
  // answers is "is the week settled", not "what exactly is Thursday".
  function weekRowLineHtml(day, slot) {
    var entry = day[slot];
    var dot = 'is-none';
    var quiet = ' is-quiet';
    var text;
    if (entry && entry.state === 'planned') {
      quiet = '';
      if (entry.source === 'leftovers') { dot = 'is-ahead'; text = mealDisplayName(entry); }
      else { dot = 'is-cook'; text = entry.title; }
    } else if (entry && entry.state === 'open') {
      dot = 'is-open';
      text = 'Pick a ' + slot;
    } else if (entry && entry.state === 'planned_empty') {
      text = awayLineFor(entry) || entry.title || 'Nothing planned';
    } else {
      text = day.isPast ? 'Not planned' : 'Nothing yet';
    }
    return '<span class="wk-line' + quiet + '">' +
      '<span class="wk-dot ' + dot + '"></span>' +
      '<span class="wk-line-name">' + escapeHtml(text) + '</span>' +
    '</span>';
  }

  function weekRowHtml(day, i) {
    return '<button type="button" class="wk-day-row' +
        (day.isToday ? ' is-today' : '') + (day.isPast ? ' is-past' : '') +
        '" data-wk-day="' + i + '">' +
      '<span class="wk-day-col">' +
        '<span class="wk-day-dow">' + dayName(day.date, { weekday: 'short' }).slice(0, 3).toUpperCase() + '</span>' +
        '<span class="wk-day-num">' + dayName(day.date, { day: 'numeric' }) + '</span>' +
      '</span>' +
      '<span class="wk-day-meals">' +
        WEEK_SLOTS.map(function (slot) { return weekRowLineHtml(day, slot); }).join('') +
      '</span>' +
    '</button>';
  }

  function weekStepHtml(data, days) {
    var state = weekPlanState(data);
    // NOTHING YET: the card has nothing to be, so the existing plan-a-week
    // entry takes its place — same buttons, same handlers, rendered into
    // #week-plan-row by renderPlanWeekEntry after this lands.
    if (state === 'none') {
      return weekStepHeadHtml(data, days) +
        '<div id="week-plan-row"></div>' +
        // Setup and the other rare actions stay one tap away here too.
        '<div class="wk-foot wk-foot-solo">' +
          '<button type="button" class="wk-foot-more" id="wk-more" aria-haspopup="dialog">More ···</button>' +
        '</div>';
    }
    var dayCount = data.day_count || days.length || 7;
    return weekStepHeadHtml(data, days) +
      '<div class="shell-card wk-week-card">' +
        days.map(weekRowHtml).join('') +
      '</div>' +
      // Everything rare is one tap away and nothing rare is on the page.
      '<div class="wk-foot">' +
        '<button type="button" class="wk-foot-link" id="wk-plan-next">' +
          escapeHtml(planEntryLabel(dayCount, 'next', false)) + ' ›</button>' +
        '<button type="button" class="wk-foot-more" id="wk-more" aria-haspopup="dialog">More ···</button>' +
      '</div>';
  }

  // ---------- DAY ----------

  // "both home tonight". Read off dinner's own attendance (get_week_menu's
  // _decorate_with_needs), never guessed from the household roster: the
  // point of the line is who is actually eating, and a roster count would
  // say two on a night one of them is away.
  function dayAttendanceLine(day) {
    var entry = day.dinner;
    if (!entry) return '';
    var present = entry.present_names || [];
    var away = entry.away_names || [];
    var guests = entry.guest_count || 0;
    var line = '';
    if (away.length && !present.length) line = 'everyone out tonight';
    else if (away.length) line = joinList(away) + ' away tonight';
    else if (present.length === 1) line = 'just ' + present[0] + ' tonight';
    else if (present.length === 2) line = 'both home tonight';
    else if (present.length > 2) line = 'all ' + present.length + ' home tonight';
    if (guests) line += (line ? ', ' : '') + guests + (guests === 1 ? ' guest' : ' guests');
    return line;
  }

  function daySubtitle(day) {
    var parts = [dayName(day.date, { month: 'short', day: 'numeric' })];
    var att = dayAttendanceLine(day);
    if (att) parts.push(att);
    return parts.join(' · ');
  }

  // "Dinner · 6:30" — the hour this household actually eats, from the
  // dinner_window rhythm fact by way of get_week_menu's slot_times, which
  // reads moves.py's own mapping so Today's timeline and this card can't
  // put different times on the same meal.
  function slotEyebrow(day, slot) {
    var label = SLOT_LABELS[slot];
    var entry = day[slot];
    if (entry && entry.leftover_from) {
      var when = dayName(entry.leftover_from.date, { weekday: 'long' });
      return label + ' · ' + (entry.leftover_from.cook_ahead
        ? 'made ahead ' + when
        : 'leftovers from ' + when);
    }
    // A reheat the planner wrote in words ("Leftovers from last night")
    // has no chain to point at, but it is still not a cook: say so rather
    // than showing a cook time beside a Mark eaten button.
    if (entry && entry.source === 'leftovers') return label + ' · leftovers';
    var times = (weekState.data && weekState.data.slot_times) || {};
    return times[slot] ? label + ' · ' + times[slot] : label;
  }

  // The plate in words: the sides the app attached, else the one-line note
  // it already wrote about this plate. Nothing is invented here — an entry
  // with neither gets no chips rather than a plausible guess.
  function plateChips(entry) {
    var chips = ((entry && entry.sides) || []).map(function (s) { return s.name; })
      .filter(Boolean);
    if (!chips.length && entry && entry.plate_note) chips = [entry.plate_note];
    return chips;
  }

  function chipsRowHtml(chips, cls) {
    chips = (chips || []).filter(Boolean);
    if (!chips.length) return '';
    return '<span class="' + (cls || 'wk-chips') + '">' +
      chips.map(function (c) { return '<span class="wk-chip">' + escapeHtml(c) + '</span>'; }).join('') +
    '</span>';
  }

  // "Cook this · 55 min". meta is "35 min" for a real cook and the words
  // "reheat"/"takeout" for the two things that aren't one — those carry no
  // cooking time, so they carry no time chip either.
  function cookTimeChip(entry) {
    var meta = entry && entry.meta;
    return (meta && meta !== 'reheat' && meta !== 'takeout') ? meta : '';
  }

  // The two-segment control. Which primary a slot gets is entirely a
  // function of its state: a cook is cooked, a made-ahead night is eaten,
  // an open slot is answered, and an away night is offered nothing at all —
  // offering to change it is exactly what the away state exists to prevent.
  function slotActionsHtml(day, slot, apricot) {
    var entry = day[slot];
    var primaryCls = 'wk-act wk-act-primary' + (apricot ? ' is-apricot' : '');
    var swap = '<button type="button" class="wk-act wk-act-swap" data-wk-swap="' + slot + '">Swap</button>';
    if (entry && entry.state === 'planned') {
      if (day.isPast) return '';
      var time = cookTimeChip(entry);
      var label = entry.source === 'leftovers'
        ? REHEAT_ACTION_LABEL
        : 'Cook this' + (time ? ' · ' + time : '');
      return '<div class="wk-acts">' +
        '<button type="button" class="' + primaryCls + '" data-wk-cook="' + slot + '">' +
          escapeHtml(label) + '</button>' + swap +
      '</div>';
    }
    if (entry && entry.state === 'open') {
      return '<div class="wk-acts">' +
        '<button type="button" class="' + primaryCls + '" data-wk-pick="' + slot + '">Pick</button>' +
        swap +
      '</div>';
    }
    if (entry && entry.state === 'planned_empty') {
      if (entry.need === 'away' || day.isPast) return '';
      return '<div class="wk-acts">' + swap + '</div>';
    }
    if (!entry && !day.isPast) {
      // Nothing here at all. Chat is the way out rather than a dead end —
      // there is no inline fill flow for breakfast or lunch, and a fake
      // "Pick" that opened nothing would be worse than no button.
      return '<div class="wk-acts">' +
        '<button type="button" class="' + primaryCls + '" data-wk-ask="' + slot + '">Pick</button>' +
      '</div>';
    }
    return '';
  }

  function daySlotCardHtml(day, slot) {
    var entry = day[slot];
    var openable = !!(entry && entry.state === 'planned');
    var name, quiet = '';
    if (entry && entry.state === 'planned') name = mealDisplayName(entry);
    else if (entry && entry.state === 'open') { name = 'Your call'; quiet = ' is-quiet'; }
    else if (entry && entry.state === 'planned_empty') {
      name = awayLineFor(entry) || entry.title || 'Nothing planned';
      quiet = ' is-quiet';
    } else { name = day.isPast ? 'Not planned' : 'Nothing yet'; quiet = ' is-quiet'; }

    var body =
      '<span class="wk-slot-eyebrow">' + escapeHtml(slotEyebrow(day, slot)) + '</span>' +
      '<span class="wk-slot-name' + quiet + '">' + escapeHtml(name) + '</span>' +
      (entry && entry.need ? '<span class="wk-slot-need">' + needBadgeHtml(entry) + '</span>' : '') +
      chipsRowHtml(plateChips(entry));

    return '<div class="shell-card wk-slot-card" data-wk-slot="' + slot + '">' +
      (openable
        ? '<button type="button" class="wk-slot-body" data-wk-meal="' + slot + '">' + body + '</button>'
        : '<div class="wk-slot-body is-flat">' + body + '</div>') +
      // The ready-made recommendation still belongs to dinner and to
      // nothing else — it is an answer to "the first one back tonight",
      // not a property of a slot.
      (slot === 'dinner' ? readyMadeHtml(day) : '') +
      slotActionsHtml(day, slot, false) +
      '<div class="wk-slot-open" id="wk-open-' + slot + '" hidden>' +
        (entry && entry.state === 'open' ? openSlotCardHtml(day.date, slot, entry) : '') +
      '</div>' +
    '</div>';
  }

  function dayStepHtml(day) {
    return '<button type="button" class="wk-back" data-wk-back="week">‹ This week</button>' +
      '<div class="wk-head">' +
        '<div class="wk-head-row"><h1 class="wk-title">' +
          escapeHtml(dayName(day.date, { weekday: 'long' })) + '</h1></div>' +
        '<div class="wk-sub">' + escapeHtml(daySubtitle(day)) + '</div>' +
      '</div>' +
      '<div class="wk-slots">' +
        WEEK_SLOTS.map(function (slot) { return daySlotCardHtml(day, slot); }).join('') +
      '</div>';
  }

  // ---------- MEAL ----------

  var PLATE_GROUP_LABELS = { protein: 'protein', carb: 'carb', vegetable: 'veg' };

  // "Protein, carb, veg. Nothing to thaw." Both halves are read, never
  // guessed: the groups are the ones the entry recorded (plates.py never
  // invents them either), and the thaw line is the plan's own defrost task
  // — the same prep_tasks row Today's fridge move ticks.
  function plateCardHtml(entry) {
    var groups = (entry.food_groups || [])
      .map(function (g) { return PLATE_GROUP_LABELS[g] || g; });
    var chips = groups.map(capitalizeFirst)
      .concat(((entry.sides) || []).map(function (s) { return s.name; }));
    var lines = [];
    if (groups.length) lines.push(capitalizeFirst(groups.join(', ')) + '.');
    lines.push(entry.defrost && entry.defrost.note
      ? entry.defrost.note.replace(/\.?$/, '.')
      : 'Nothing to thaw.');
    return '<div class="shell-card wk-card">' +
      '<div class="wk-card-title">The plate</div>' +
      chipsRowHtml(chips) +
      '<div class="wk-card-line">' + escapeHtml(lines.join(' ')) + '</div>' +
    '</div>';
  }

  // The cook card this entry is on, in the Cook view's own data. The
  // cook-ahead picker is that view's control, reused here verbatim
  // (cookAheadHtml) rather than rebuilt — one picker, one POST, one set of
  // rules about which days a batch may cover.
  function cookMealForEntry(entryId) {
    if (entryId === null || entryId === undefined) return null;
    var meals = (cookState.data && cookState.data.meals) || [];
    for (var i = 0; i < meals.length; i++) {
      if (meals[i].entry_id === entryId) return meals[i];
      if (meals[i].entry_ids && meals[i].entry_ids.indexOf(entryId) !== -1) return meals[i];
    }
    return null;
  }

  // Fetched once and cached on cookState, so opening a meal never costs a
  // round trip twice and the Cook view finds it already loaded. Silent on
  // failure: the picker is an offer, and a meal that renders without one is
  // still a correct meal.
  async function ensureCookDataForMeals(panel) {
    if (cookState.data || cookState.mealsCookFetch) return;
    cookState.mealsCookFetch = true;
    try {
      var res = await fetch('/api/cooker-view');
      if (!res.ok) throw new Error('cooker-view failed');
      cookState.data = await res.json();
      if (weekState.step === 'meal') renderMealsStep(panel);
    } catch (err) {
      console.warn('Cook-ahead lookup failed:', err);
    } finally {
      cookState.mealsCookFetch = false;
    }
  }

  function mealStepHtml(day, slot) {
    var entry = day[slot];
    var chips = [
      cookTimeChip(entry),
      entry.serves ? 'Serves ' + entry.serves : '',
      entry.plate_note
    ];
    var cookMeal = cookMealForEntry(entry.entry_id);
    var aheadHtml = cookMeal ? cookAheadHtml(cookMeal) : '';
    return '<button type="button" class="wk-back" data-wk-back="day">‹ ' +
        escapeHtml(dayName(day.date, { weekday: 'long' })) + '</button>' +
      '<div class="wk-head">' +
        '<div class="wk-head-row"><h1 class="wk-title">' +
          escapeHtml(mealDisplayName(entry)) + '</h1></div>' +
        chipsRowHtml(chips, 'wk-chips wk-chips-head') +
      '</div>' +
      plateCardHtml(entry) +
      (aheadHtml ? '<div class="shell-card wk-card">' + aheadHtml + '</div>' : '') +
      // Why this night, when the plan actually recorded a reason. Written
      // at generation (meal_plan_entries.reasoning), so it can't contradict
      // the real one.
      (entry.reason
        ? '<div class="shell-card wk-card">' +
            '<div class="wk-card-title">Why this night</div>' +
            '<div class="wk-card-line">' + escapeHtml(entry.reason) + '</div>' +
          '</div>'
        : '') +
      // The screen's one apricot primary (Rule 5) — the Day step's own
      // segments are quiet for exactly this reason.
      slotActionsHtml(day, slot, true);
  }

  // ---------- the step machine ----------

  function mealsCurrentDay() {
    var i = weekState.selectedIndex;
    return (i !== null && weekState.days[i]) ? weekState.days[i] : null;
  }

  function pushMealsStepHistory() {
    // Same path either way — Meals is /week in all three steps, exactly as
    // Grocery is /grocery in all three of its. The state object is what the
    // back gesture reads; the URL never claims a page that doesn't exist.
    window.history.pushState({
      tab: 'week',
      mealsStep: weekState.step,
      mealsDay: weekState.selectedIndex,
      mealsSlot: weekState.mealSlot
    }, '', '/week');
  }

  function goMealsStep(step, opts) {
    opts = opts || {};
    weekState.step = step;
    if (opts.dayIndex !== undefined && opts.dayIndex !== null) weekState.selectedIndex = opts.dayIndex;
    if (opts.slot) weekState.mealSlot = opts.slot;
    if (opts.push !== false) pushMealsStepHistory();
    var panel = panels['week'];
    if (!panel || !panel.dataset.built) return;
    renderMealsStep(panel);
    // A step change is a screen change, so it starts at the top — the same
    // rule Cook's focus screen and Grocery's shopping mode already follow.
    if (scrollEl) scrollEl.scrollTop = 0;
  }

  // The browser's own back gesture, one level out. Called from the shell's
  // single popstate listener so there is one place that decides what Back
  // means, rather than a second listener racing the first.
  function applyMealsStepFromHistory(state) {
    var panel = panels['week'];
    if (!panel || !panel.dataset.built) return;
    var step = (state && state.mealsStep) || 'week';
    if (state && state.mealsDay !== undefined && state.mealsDay !== null) {
      weekState.selectedIndex = state.mealsDay;
    }
    if (state && state.mealsSlot) weekState.mealSlot = state.mealsSlot;
    weekState.step = step;
    renderMealsStep(panel);
  }

  function renderMealsStep(panel) {
    var steps = panel.querySelector('#week-steps');
    if (!steps) return;
    var data = weekState.data || {};
    var day = mealsCurrentDay();
    // A day or a meal that stopped existing under the step (the week was
    // re-planned, the slot swapped out) falls back to the root rather than
    // rendering a step about nothing.
    if (weekState.step === 'meal' &&
        !(day && day[weekState.mealSlot] && day[weekState.mealSlot].state === 'planned')) {
      weekState.step = day ? 'day' : 'week';
    }
    if (weekState.step === 'day' && !day) weekState.step = 'week';

    var onRoot = weekState.step === 'week';
    var band = panel.querySelector('#week-review-band');
    var approve = panel.querySelector('#week-approve-row');
    // The draft's decision and the approved receipt belong to the week, not
    // to one day of it — they sit above the card on the root and nowhere
    // else, the same way Cook's focus screen hides the Plan/Cook control.
    if (band) band.hidden = !onRoot;
    if (approve) approve.hidden = !onRoot;
    var seg = panel.querySelector('#meals-seg');
    if (seg && panel.dataset.mealsView !== 'cook') seg.hidden = !onRoot;

    if (weekState.step === 'meal') {
      steps.innerHTML = mealStepHtml(day, weekState.mealSlot);
      ensureCookDataForMeals(panel);
    } else if (weekState.step === 'day') {
      steps.innerHTML = dayStepHtml(day);
    } else {
      steps.innerHTML = weekStepHtml(data, weekState.days);
      if (weekPlanState(data) === 'none') renderPlanWeekEntry(steps, data);
    }
    wireMealsStep(panel, steps);
  }

  function wireMealsStep(panel, steps) {
    steps.querySelectorAll('[data-wk-day]').forEach(function (row) {
      row.addEventListener('click', function () {
        goMealsStep('day', { dayIndex: Number(row.getAttribute('data-wk-day')) });
      });
    });
    steps.querySelectorAll('[data-wk-back]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        // Up one level, named — "‹ This week" goes to the week, always.
        // Deliberately NOT history.back(): back goes to the PREVIOUS view,
        // which after any wandering (a day, a meal, back, another day) is
        // not the same thing as the parent, and a link that says "This
        // week" must not land on a meal. The back gesture keeps its own,
        // correct meaning through the popstate handler.
        goMealsStep(btn.getAttribute('data-wk-back'));
      });
    });
    steps.querySelectorAll('[data-wk-meal]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        goMealsStep('meal', { slot: btn.getAttribute('data-wk-meal') });
      });
    });
    steps.querySelectorAll('[data-wk-cook]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var day = mealsCurrentDay();
        var slot = btn.getAttribute('data-wk-cook');
        var entry = day && day[slot];
        // Everything that identifies THIS meal, so Cook lands on it
        // whatever shape the plan is — the same target dayActionsHtml used
        // to pass, now available from any day rather than only today.
        activateTab('week', true, {
          mealsView: 'cook',
          mealsFocus: {
            entryId: entry ? entry.entry_id : null,
            date: day ? day.date : null,
            slot: slot,
            title: entry ? entry.title : ''
          }
        });
      });
    });
    steps.querySelectorAll('[data-wk-swap]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var day = mealsCurrentDay();
        if (!day) return;
        openAskSheet('Swap ' + dayName(day.date, { weekday: 'long' }) + '’s ' +
          btn.getAttribute('data-wk-swap') + ' for something else');
      });
    });
    steps.querySelectorAll('[data-wk-ask]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var day = mealsCurrentDay();
        if (!day) return;
        openAskSheet('For ' + dayName(day.date, { weekday: 'long' }) + '’s ' +
          btn.getAttribute('data-wk-ask') + ', I’d like ');
      });
    });
    // "Pick" on an open slot reveals the resolver the app already had — the
    // question and its options, unchanged, in the day they belong to
    // instead of stacked at the bottom of the root.
    steps.querySelectorAll('[data-wk-pick]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var box = steps.querySelector('#wk-open-' + btn.getAttribute('data-wk-pick'));
        if (!box) return;
        box.hidden = false;
        btn.hidden = true;
      });
    });
    wireOpenSlotOptions(panel, steps);
    // The ready-made earmark's two answers. Emily's standing rule: the
    // system recommends, the household confirms — nothing acts on the
    // earmark until one of these is tapped. They used to be wired by
    // renderDayCard; they render on the Day step's dinner card now
    // (readyMadeHtml, unchanged) and need wiring in the same breath.
    steps.querySelectorAll('.ready-made-confirm').forEach(function (btn) {
      btn.addEventListener('click', function () { confirmReadyMade(panel, btn.dataset.date, true); });
    });
    steps.querySelectorAll('.ready-made-other').forEach(function (btn) {
      btn.addEventListener('click', function () {
        // "Choose differently" declines the earmark and hands the question
        // to chat, which is where an actual alternative gets chosen — the
        // engine stores one recommendation, not a menu to pick from.
        confirmReadyMade(panel, btn.dataset.date, false);
        openAskSheet('Something else for ' + dayName(btn.dataset.date, { weekday: 'long' }) +
          '’s dinner — I’ll be back from a trip, so nothing that needs real cooking');
      });
    });
    // The cook-ahead picker, borrowed whole from the Cook view: the chips
    // are local state until confirmed, and confirming goes through
    // cookSetCookAhead so there is exactly one place that writes it.
    steps.querySelectorAll('[data-cook="ahead-day"]').forEach(function (chip) {
      chip.addEventListener('click', function () {
        var sourceId = chip.getAttribute('data-source-id');
        var picks = cookState.cookAheadPicks[sourceId] ||
          (cookState.cookAheadPicks[sourceId] = {});
        var dayId = chip.getAttribute('data-day-id');
        if (picks[dayId]) delete picks[dayId]; else picks[dayId] = true;
        renderMealsStep(panel);
      });
    });
    steps.querySelectorAll('[data-cook="ahead-go"]').forEach(function (go) {
      go.addEventListener('click', async function () {
        await cookSetCookAhead(go);
        // Consolidating cooks changes what this week says about itself —
        // the covered days become made-ahead lines on the root card.
        await loadWeekMenu(panel);
      });
    });
    var next = steps.querySelector('#wk-plan-next');
    if (next) next.addEventListener('click', function () {
      var dayCount = (weekState.data && weekState.data.day_count) ||
        (planningPeriodDefault && planningPeriodDefault.day_count) || 7;
      var start = (weekState.data && (weekState.data.period_start_date || weekState.data.week_start_date)) ||
        (planningPeriodDefault && planningPeriodDefault.start_date) || thisWeekStartLocal();
      startPlanningWeek(addDaysLocal(start, dayCount), dayCount);
    });
    var more = steps.querySelector('#wk-more');
    if (more) more.addEventListener('click', function () { openMealsMoreSheet(); });
  }

  // ---------- "More": every rare action, one tap off the root ----------
  // The root used to carry all of these at once. None of them is wrong —
  // they are just not what the screen is for, and a screen that shows its
  // rare actions permanently is a screen that never finishes answering its
  // own question. A sheet, not a page (NavBlueprint): it slides over Meals
  // and dismisses down.

  var mealsMoreScrim = document.getElementById('meals-more-scrim');
  var mealsMoreSheet = document.getElementById('meals-more-sheet');

  function mealsMoreRowHtml(id, label, sub) {
    return '<button type="button" class="wk-more-row" id="' + id + '">' +
      '<span class="wk-more-label">' + escapeHtml(label) + '</span>' +
      (sub ? '<span class="wk-more-sub">' + escapeHtml(sub) + '</span>' : '') +
    '</button>';
  }

  function renderMealsMoreSheet() {
    var rows = document.getElementById('meals-more-rows');
    if (!rows) return;
    var data = weekState.data || {};
    var hasPlan = !!data.weekly_plan_id;
    var dayCount = data.day_count ||
      (planningPeriodDefault && planningPeriodDefault.day_count) || 7;
    var start = data.period_start_date || data.week_start_date ||
      (planningPeriodDefault && planningPeriodDefault.start_date) || thisWeekStartLocal();
    rows.innerHTML =
      mealsMoreRowHtml('wk-more-replan',
        planEntryLabel(dayCount, 'current', hasPlan), periodRangeLabel(start, dayCount)) +
      // The custom-range picker, unchanged — same opener id, same picker
      // id, same wirePeriodPicker. It only moved.
      '<button type="button" class="wk-more-row" id="week-period-open" aria-expanded="false">' +
        '<span class="wk-more-label">' + escapeHtml(PERIOD_PICKER_COPY.open) + '</span></button>' +
      '<div class="week-period-picker" id="week-period-picker" hidden></div>' +
      (hasPlan && data.status !== 'approved'
        ? mealsMoreRowHtml('wk-more-try-again', 'Try again', 'Same answers, a different week') +
          mealsMoreRowHtml('wk-more-change', 'Change my answers')
        : '') +
      mealsMoreRowHtml('wk-more-whole-week', 'See the whole week', 'All the meals, and the link to share them') +
      mealsMoreRowHtml('wk-more-setup', 'Adjust your setup') +
      mealsMoreRowHtml('wk-more-reset', 'Start over');

    var panel = panels['week'];
    function on(id, fn) {
      var el = rows.querySelector('#' + id);
      if (el) el.addEventListener('click', fn);
    }
    on('wk-more-replan', function () { closeMealsMoreSheet(); startPlanningWeek(start, dayCount); });
    on('wk-more-try-again', function () { closeMealsMoreSheet(); tryAgain(panel, data); });
    on('wk-more-change', function () {
      closeMealsMoreSheet();
      startPlanningWeek(data.week_start_date, data.day_count || 7);
    });
    on('wk-more-whole-week', function () { closeMealsMoreSheet(); openWeekSheet(); });
    on('wk-more-setup', function () { closeMealsMoreSheet(); openMealSetup(); });
    on('wk-more-reset', function () { closeMealsMoreSheet(); openResetDialog(); });
    // The picker's own confirm navigates away to /plan-week, so it needs no
    // dismissal of its own.
    wirePeriodPicker(rows, start, dayCount);
  }

  function openMealsMoreSheet() {
    if (!mealsMoreSheet) return;
    closeAskSheet();
    closeWeekSheet();
    renderMealsMoreSheet();
    mealsMoreScrim.hidden = false;
    mealsMoreSheet.hidden = false;
  }
  function closeMealsMoreSheet() {
    if (!mealsMoreScrim) return;
    mealsMoreScrim.hidden = true;
    mealsMoreSheet.hidden = true;
  }
  if (mealsMoreScrim) {
    mealsMoreScrim.addEventListener('click', closeMealsMoreSheet);
    document.getElementById('meals-more-handle').addEventListener('click', closeMealsMoreSheet);
    document.getElementById('meals-more-close').addEventListener('click', closeMealsMoreSheet);
  }

  // ---------- Settling a slot the app handed back ----------

  // The one place an open slot is answered. It used to be a stack of cards
  // at the bottom of the root, one per open slot and none of them beside
  // the day it was about; now it is revealed by "Pick" inside that day's
  // own card (see daySlotCardHtml). Same question, same options, same
  // write — amber, and the reason names the CONSTRAINT that caused it, so
  // the ask reads as diligence rather than failure.
  function openSlotCardHtml(date, slot, entry) {
    return (
      '<div class="shell-card week-open-card" data-open-date="' + date + '" data-open-slot="' + slot + '">' +
        '<div class="week-open-reason">' + escapeHtml(entry.open_reason || '') + '</div>' +
        (entry.options && entry.options.length
          ? '<div class="week-open-options">' + entry.options.map(function (opt) {
              return '<button type="button" class="week-open-option" data-choice="' + escapeHtml(opt.label) + '">' +
                '<span class="week-open-option-label">' + escapeHtml(opt.label) + '</span>' +
                '<span class="week-open-option-meta">' + escapeHtml(opt.meta || '—') + '</span>' +
              '</button>';
            }).join('') + '</div>'
          // No options offered — chat is the escape hatch for anything the
          // screens can't express, rather than a dead end.
          : '<button type="button" class="week-open-talk">Tell me what you’d like instead →</button>') +
      '</div>'
    );
  }

  function wireOpenSlotOptions(panel, scope) {
    scope.querySelectorAll('.week-open-card').forEach(function (cardEl) {
      cardEl.querySelectorAll('.week-open-option').forEach(function (btn) {
        btn.addEventListener('click', function () {
          resolveOpenSlot(panel, cardEl.dataset.openDate, cardEl.dataset.openSlot, btn.dataset.choice);
        });
      });
      var talk = cardEl.querySelector('.week-open-talk');
      if (talk) talk.addEventListener('click', function () {
        openAskSheet('For ' + dayName(cardEl.dataset.openDate, { weekday: 'long' }) + '’s ' +
          cardEl.dataset.openSlot + ', I’d like ');
      });
    });
  }

  async function resolveOpenSlot(panel, date, slot, choice) {
    var data = weekState.data;
    if (!data || !data.week_start_date) return;
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(data.week_start_date) + '/slot', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: date, slot: slot, choice: choice })
      });
      if (!res.ok) throw new Error('slot resolve failed');
      await res.json();
      showToast(dayName(date, { weekday: 'long' }) + '’s settled — thank you.');
      // Settling a slot in an already-approved week writes to the shopping
      // list, so anything showing that list is now stale.
      if (data.status === 'approved') refreshGrocerySurfaces();
      await loadWeekMenu(panel);
    } catch (err) {
      console.warn('Open slot resolution failed:', err);
      alert('Could not save that choice right now — try again in a moment.');
    }
  }

  // ---------- Approve the week (design_handoff_plan_the_week) ----------
  // Approval is the one thing that puts a week's ingredients on the
  // shopping list, and it is a BUTTON — not a sentence the assistant has to
  // remember to offer. Everything below is the Meals screen's half of that:
  // the promise while the week is a draft, and the receipt once it isn't.

  function approvedAtLabel(approvedAt) {
    // approved_at is SQLite's datetime('now') — UTC, no zone marker. Told
    // explicitly that it's UTC ('Z'), so it renders in the household's own
    // local time rather than an hours-off "9:41AM" that never matches when
    // they actually tapped it.
    if (!approvedAt) return '';
    var d = new Date(approvedAt.replace(' ', 'T') + 'Z');
    if (isNaN(d.getTime())) return '';
    return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }).replace(/\s/g, '').toUpperCase();
  }

  function groceryPromiseText(preview) {
    var n = (preview && preview.would_add_count) || 0;
    // The spec's promise names a real number. When that number is zero
    // there is nothing to promise, and the verbatim line would read "0
    // items" — so this case gets its own honest sentence instead. It is
    // not hypothetical: a week planned before approval gated the list
    // already has its ingredients on there (see DECISIONS.md's closing
    // note), and approving it genuinely adds nothing.
    if (!n) {
      return 'Everything this week needs is already on your shopping list. Approving won’t add anything to it.';
    }
    // The spec's line ended ", less whatever's already in your kitchen",
    // which promised a subtraction that has in fact already happened: n is
    // the count AFTER the kitchen was checked (see
    // preview_plan_grocery_impact, which puts each ingredient in exactly
    // one of the two buckets). So it read as "n items, and then fewer than
    // that" when the truth is "n items, which is what's left".
    //
    // Now it says what actually happened, in the same shape as the receipt
    // below — and says nothing at all when nothing was subtracted, which is
    // every household that hasn't done the inventory intake.
    // (Emily's call, 2026-09-02. COPY.md and SPEC.md updated to match.)
    var have = (preview && preview.already_have_count) || 0;
    var line = 'I haven’t put anything on your shopping list yet. Approve the week and I’ll build it — ' +
      n + (n === 1 ? ' item' : ' items') + '.';
    if (have) {
      line += ' ' + have + (have === 1 ? ' more was' : ' more were') +
        ' already in your kitchen, so I’ve left ' + (have === 1 ? 'that' : 'those') + ' off.';
    }
    return line;
  }

  function receiptBodyText(data) {
    var added = data.approved_grocery_added || 0;
    var skipped = data.approved_grocery_skipped || 0;
    var others = data.other_adults || [];
    var text;
    if (!added) {
      // Same reason as groceryPromiseText's zero case — "I've put 0 items
      // on your shopping list" is worse than saying what actually happened.
      text = 'All set. Your shopping list already had everything this week needs, so I left it as it was.';
    } else {
      text = 'All set. I’ve put ' + added + (added === 1 ? ' item' : ' items') + ' on your shopping list';
      // The "already in your kitchen" clause only earns its place when
      // something was actually left off. Writing "0 were already in your
      // kitchen" is noise.
      text += skipped
        ? ' — ' + skipped + (skipped === 1 ? ' was' : ' were') + ' already in your kitchen, so I left ' +
          (skipped === 1 ? 'that' : 'those') + ' off.'
        : '.';
    }
    // "Marcus has been told the week is settled" is only written when there
    // IS another adult, and it is true when written: approving raises a
    // household-wide "N approved the week" notification (see
    // tools.get_active_notifications #4) the other adult sees on their next
    // visit. A one-adult household gets no sentence about nobody.
    if (others.length) {
      text += ' ' + others.join(' and ') + (others.length === 1 ? ' has' : ' have') + ' been told the week is settled.';
    }
    return text;
  }

  function refreshGrocerySurfaces() {
    // Both surfaces that show groceries are this script's own now, so both
    // are re-rendered in place. This used to reload the Grocery iframe's src
    // — throwing the whole screen away, scroll position and all — because a
    // second document was the only handle the shell had on it.
    refreshGroceryPanel();
    // Today's shop move is a reading of the same list — it appears and
    // disappears with it.
    refreshTodayMoves();
  }

  // Keyed by weekly_plan_id, page-view only (no fetch, no server write) —
  // Emily's ask was to walk the loop forward, not to remember a "no" past
  // this visit, and reopening this exact question every time the approved
  // receipt re-renders (a tab switch away and back, say) would be worse
  // than just leaving the offer up. See renderWeekApproval's handoff row.
  var weekReceiptHandoffDismissed = {};

  // The freezer-check ask card's own state (Loop Board "Defrost check: ask
  // at approval") — page-view only, same reasoning as
  // weekReceiptHandoffDismissed just above: nothing here is the server's
  // business except the eventual answer. `items` is null until fetched (or
  // reset to null to force a fresh fetch — see openDefrostAskFromCook),
  // then the plan's own meat/seafood ingredients once loaded; `selected`
  // is which chips are currently tapped, keyed by item name.
  // `forceShow` is a one-shot override so the Cook view's "Something in
  // the freezer?" link can reopen this even after the household already
  // answered for this plan — it does not touch defrost_asked_at itself,
  // which stays what gates the AUTOMATIC card.
  var defrostAskState = { planId: null, items: null, selected: {}, forceShow: false };

  function defrostAskChipHtml(it) {
    var selected = !!defrostAskState.selected[it.item];
    return '<button type="button" class="defrost-chip' + (selected ? ' is-selected' : '') + '" ' +
      'data-defrost-chip="' + escapeHtml(it.item) + '" aria-pressed="' + selected + '">' +
      escapeHtml(it.item) +
    '</button>';
  }

  function defrostAskCardHtml() {
    var items = defrostAskState.items || [];
    return (
      '<div class="shell-card plan-nudge-card defrost-ask-card" id="defrost-ask-card">' +
        '<div class="plan-nudge-top">' +
          '<span class="plan-nudge-eyebrow">FREEZER CHECK</span>' +
          '<button type="button" class="plan-nudge-dismiss" id="defrost-ask-dismiss">Not now</button>' +
        '</div>' +
        '<div class="plan-nudge-title">Any of this week’s meat in the freezer?</div>' +
        '<div class="plan-nudge-body">Tap what’s frozen and I’ll tell you when to move it to the fridge.</div>' +
        '<div class="defrost-ask-chips">' + items.map(defrostAskChipHtml).join('') + '</div>' +
        '<div class="ny-actions">' +
          '<button type="button" class="btn-gold" id="defrost-ask-confirm">Add to the schedule</button>' +
          '<button type="button" class="btn-sand" id="defrost-ask-none">None — all fresh</button>' +
        '</div>' +
      '</div>'
    );
  }

  function wireDefrostAskCard(row, panel, data) {
    var card = row.querySelector('#defrost-ask-card');
    if (!card) return;
    card.querySelectorAll('[data-defrost-chip]').forEach(function (chip) {
      chip.addEventListener('click', function () {
        var name = chip.getAttribute('data-defrost-chip');
        defrostAskState.selected[name] = !defrostAskState.selected[name];
        chip.classList.toggle('is-selected');
        chip.setAttribute('aria-pressed', defrostAskState.selected[name] ? 'true' : 'false');
      });
    });
    var dismissBtn = card.querySelector('#defrost-ask-dismiss');
    if (dismissBtn) dismissBtn.addEventListener('click', function () { submitDefrostAsk(panel, data, []); });
    var noneBtn = card.querySelector('#defrost-ask-none');
    if (noneBtn) noneBtn.addEventListener('click', function () { submitDefrostAsk(panel, data, []); });
    var confirmBtn = card.querySelector('#defrost-ask-confirm');
    if (confirmBtn) {
      confirmBtn.addEventListener('click', function () {
        var chosen = Object.keys(defrostAskState.selected).filter(function (k) { return defrostAskState.selected[k]; });
        submitDefrostAsk(panel, data, chosen);
      });
    }
  }

  // Fetched once per plan (see defrostAskState.planId), then cached —
  // switching tabs and back must not refetch (nav rules: "nothing
  // reloads"). Re-renders the approval row once the answer arrives, since
  // the row already painted without the card while this was in flight.
  async function ensureDefrostAskItems(panel, data) {
    if (!data.weekly_plan_id) return;
    if (defrostAskState.planId === data.weekly_plan_id && defrostAskState.items !== null) return;
    defrostAskState.planId = data.weekly_plan_id;
    defrostAskState.items = null;
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(data.week_start_date) + '/defrost-items');
      if (!res.ok) throw new Error('defrost items lookup failed');
      var body = await res.json();
      if (defrostAskState.planId !== data.weekly_plan_id) return; // a newer plan loaded while this was in flight
      defrostAskState.items = body.items || [];
      renderWeekApproval(panel, data);
    } catch (err) {
      console.warn('Defrost item lookup failed:', err);
      if (defrostAskState.planId === data.weekly_plan_id) defrostAskState.items = [];
    }
  }

  async function submitDefrostAsk(panel, data, items) {
    var card = panel.querySelector('#defrost-ask-card');
    var buttons = card ? card.querySelectorAll('button') : [];
    buttons.forEach(function (b) { b.disabled = true; });
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(data.week_start_date) + '/defrost-confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: items }),
      });
      if (!res.ok) throw new Error('defrost confirm failed');
      var body = await res.json();
      // Force a fresh defrost-items fetch next time this plan's card could
      // show again (the Cook view's re-ask) — this plan just got an
      // answer, but a re-ask can offer the same items again.
      defrostAskState.items = null;
      defrostAskState.selected = {};
      var notes = (body.notes || []).map(function (n) { return n.note; });
      if (notes.length) {
        // Calm and plain (DESIGN_SYSTEM §8) — the note already IS the fact
        // plus its way out, so it's shown as-is, held long enough to read.
        showToast(notes[0], null, 9000);
      } else if (items.length) {
        showToast('Got it — I’ll remind you when to move ' + (items.length === 1 ? 'it' : 'them') + ' to the fridge.');
      }
      await loadWeekMenu(panel); // refetches defrost_asked_at so the card hides itself
    } catch (err) {
      console.warn('Defrost confirmation failed:', err);
      buttons.forEach(function (b) { b.disabled = false; });
      alert('Could not save that right now — try again in a moment.');
    }
  }

  // The Cook view's "Something in the freezer?" re-ask (see cookPrepHtml).
  // Lands on Meals' Plan state, where the ask card actually lives, forcing
  // it open even if this plan already has an answer on file — re-asking is
  // explicitly allowed any number of times, it just never resets
  // defrost_asked_at (only a real answer/dismiss does that).
  function openDefrostAskFromCook() {
    defrostAskState.items = null;
    defrostAskState.selected = {};
    defrostAskState.forceShow = true;
    // The ask card lives above the week card and shows on the ROOT only
    // (renderMealsStep hides both bands on the Day and Meal steps), so a
    // re-ask arriving while a day is open has to come back out to the
    // week — otherwise it forces open a card nobody can see.
    weekState.step = 'week';
    activateTab('week', true, { mealsView: 'plan' });
    var panel = panels['week'];
    if (!panel) return;
    if (weekState.data) renderWeekApproval(panel, weekState.data);
    else loadWeekMenu(panel);
  }

  // ---------- Cook ahead, asked once for the whole week ----------
  // Emily, 2026-09-08 (item 8): ask at approval too, not only on the Cook
  // card, and no cap on days. The Cook card's picker asks one card at a
  // time, so a week of the same breakfast only meets the question five
  // cards in; the receipt is where the shape of the week is actually
  // visible. Same state shape and same reasoning as defrostAskState just
  // above (page-view only, `items` null until fetched, `forceShow` the
  // one-shot override the Cook view's link sets) — `picks` is which later
  // days are ticked, keyed source entry_id -> covered entry_id. Nothing
  // starts ticked: cooking ahead is a choice, not a default.
  var cookAheadAskState = { planId: null, items: null, picks: {}, forceShow: false };

  function cookAheadAskPicks(item) {
    var picks = cookAheadAskState.picks[item.first.entry_id];
    if (!picks) picks = cookAheadAskState.picks[item.first.entry_id] = {};
    return picks;
  }

  function cookAheadAskBlockHtml(item) {
    var later = item.later || [];
    var picks = cookAheadAskPicks(item);
    var ticked = later.filter(function (d) { return !!picks[d.entry_id]; });
    // The live arithmetic, same as the Cook card's: this day plus every
    // ticked one, and the people sitting down to all of them. Eaters can be
    // 0 for a household with nobody on record, and then the line just says
    // how many days — still true.
    var count = ticked.length + 1;
    var eaters = item.first.eaters || 0;
    if (eaters) ticked.forEach(function (d) { eaters += d.eaters || 0; });
    var total = later.length + 1;
    return '<div class="ca-ask-block">' +
      '<div class="ca-ask-line">' +
        escapeHtml(item.dish + ' is on ' + total + ' ' + cookSlotWord(item.slot, total) + '. Cook ahead?') +
      '</div>' +
      '<div class="ca-ask-days">' +
        later.map(function (d) {
          var on = !!picks[d.entry_id];
          return '<button type="button" class="ca-ask-day' + (on ? ' is-on' : '') + '" ' +
            'data-ca-source="' + item.first.entry_id + '" data-ca-day="' + d.entry_id + '" ' +
            'aria-pressed="' + on + '">' + escapeHtml(dayNameShort(d.date)) + '</button>';
        }).join('') +
      '</div>' +
      '<div class="ca-ask-count">' +
        escapeHtml('Makes ' + count + ' ' + cookSlotWord(item.slot, count) + (eaters ? ' · for ' + eaters : '')) +
      '</div>' +
    '</div>';
  }

  function cookAheadAskCardHtml() {
    var items = cookAheadAskState.items || [];
    return (
      '<div class="shell-card plan-nudge-card cook-ahead-ask-card" id="cook-ahead-ask-card">' +
        '<div class="plan-nudge-top">' +
          '<span class="plan-nudge-eyebrow">COOK AHEAD</span>' +
        '</div>' +
        '<div class="plan-nudge-title">One batch, several days?</div>' +
        '<div class="plan-nudge-body">Tick the days a batch should cover and they become one cook.</div>' +
        items.map(cookAheadAskBlockHtml).join('') +
        // One answer for the whole card, and no second apricot: the
        // receipt above already spent this screen's one apricot primary on
        // "Take me to the list" (Rule 5), and .ny-actions .btn-gold is
        // spruce here for exactly that reason.
        '<div class="ny-actions">' +
          '<button type="button" class="btn-gold" id="cook-ahead-ask-confirm">Cook ahead for these</button>' +
          '<button type="button" class="btn-sand" id="cook-ahead-ask-none">Cook each on its own</button>' +
        '</div>' +
      '</div>'
    );
  }

  function wireCookAheadAskCard(row, panel, data) {
    var card = row.querySelector('#cook-ahead-ask-card');
    if (!card) return;
    card.querySelectorAll('[data-ca-day]').forEach(function (chip) {
      chip.addEventListener('click', function () {
        var picks = cookAheadAskState.picks[chip.getAttribute('data-ca-source')] ||
          (cookAheadAskState.picks[chip.getAttribute('data-ca-source')] = {});
        var dayId = chip.getAttribute('data-ca-day');
        if (picks[dayId]) delete picks[dayId]; else picks[dayId] = true;
        // Re-render rather than toggling in place: the count line under
        // this block has to change with the chip, and it is the whole
        // point of ticking one.
        renderWeekApproval(panel, data);
      });
    });
    var confirmBtn = card.querySelector('#cook-ahead-ask-confirm');
    if (confirmBtn) {
      confirmBtn.addEventListener('click', function () {
        var choices = [];
        (cookAheadAskState.items || []).forEach(function (item) {
          var picks = cookAheadAskState.picks[item.first.entry_id] || {};
          var covered = (item.later || [])
            .filter(function (d) { return !!picks[d.entry_id]; })
            .map(function (d) { return d.entry_id; });
          // A dish nobody ticked is left alone entirely — sending it with
          // no days would be a write that says nothing.
          if (covered.length) {
            choices.push({ source_entry_id: item.first.entry_id, covered_entry_ids: covered });
          }
        });
        submitCookAheadAsk(panel, data, choices);
      });
    }
    var noneBtn = card.querySelector('#cook-ahead-ask-none');
    if (noneBtn) noneBtn.addEventListener('click', function () { submitCookAheadAsk(panel, data, []); });
  }

  // Fetched once per plan and then cached, exactly as the defrost items
  // are — switching tabs and back must not refetch (nav rules: "nothing
  // reloads"). Re-renders the approval row once the answer arrives, since
  // the row already painted without the card while this was in flight.
  async function ensureCookAheadAskItems(panel, data) {
    if (!data.weekly_plan_id) return;
    if (cookAheadAskState.planId === data.weekly_plan_id && cookAheadAskState.items !== null) return;
    cookAheadAskState.planId = data.weekly_plan_id;
    cookAheadAskState.items = null;
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(data.week_start_date) + '/cook-ahead-items');
      if (!res.ok) throw new Error('cook-ahead items lookup failed');
      var body = await res.json();
      if (cookAheadAskState.planId !== data.weekly_plan_id) return; // a newer plan loaded while this was in flight
      cookAheadAskState.items = body.items || [];
      renderWeekApproval(panel, data);
    } catch (err) {
      console.warn('Cook-ahead item lookup failed:', err);
      if (cookAheadAskState.planId === data.weekly_plan_id) cookAheadAskState.items = [];
    }
  }

  async function submitCookAheadAsk(panel, data, choices) {
    var card = panel.querySelector('#cook-ahead-ask-card');
    var buttons = card ? card.querySelectorAll('button') : [];
    buttons.forEach(function (b) { b.disabled = true; });
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(data.week_start_date) + '/cook-ahead-confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ choices: choices }),
      });
      if (!res.ok) throw new Error('cook-ahead confirm failed');
      var body = await res.json();
      cookAheadAskState.items = null;
      cookAheadAskState.picks = {};
      var refused = (body.refused || []);
      var applied = (body.applied || []);
      if (refused.length) {
        // The refusal already IS the fact plus its way out (see
        // set_cook_ahead), so it's shown as-is and held long enough to read.
        showToast(refused[0].note, null, 9000);
      } else if (applied.length) {
        showToast('Got it — one batch covers those days now.');
      }
      await loadWeekMenu(panel); // refetches cook_ahead_asked_at so the card hides itself
      loadCook(); // the Cook view, if it's built, now has fewer cooks and some made-ahead days
    } catch (err) {
      console.warn('Cook-ahead confirmation failed:', err);
      buttons.forEach(function (b) { b.disabled = false; });
      alert('Could not save that right now — try again in a moment.');
    }
  }

  // The Cook view's "Cooking ahead?" re-ask (see cookAheadAskLinkHtml),
  // mirroring openDefrostAskFromCook: lands on Meals' Plan state where the
  // card lives and forces it open even once this plan has an answer on
  // file. Re-asking never resets cook_ahead_asked_at — that column only
  // gates the automatic card.
  function openCookAheadAskFromCook() {
    cookAheadAskState.items = null;
    cookAheadAskState.picks = {};
    cookAheadAskState.forceShow = true;
    // The ask card lives above the week card and shows on the ROOT only
    // (renderMealsStep hides both bands on the Day and Meal steps), so a
    // re-ask arriving while a day is open has to come back out to the
    // week — otherwise it forces open a card nobody can see.
    weekState.step = 'week';
    activateTab('week', true, { mealsView: 'plan' });
    var panel = panels['week'];
    if (!panel) return;
    if (weekState.data) renderWeekApproval(panel, weekState.data);
    else loadWeekMenu(panel);
  }

  function renderWeekApproval(panel, data) {
    var row = panel.querySelector('#week-approve-row');
    if (!row) return;
    if (!data.weekly_plan_id) { row.innerHTML = ''; return; }

    if (data.status === 'approved') {
      var who = (data.approved_by || '').trim();
      var time = approvedAtLabel(data.approved_at);
      var eyebrow = '✓ APPROVED' + (who ? ' BY ' + who.toUpperCase() : '') + (time ? ' · ' + time : '');
      // The loop nudge (Emily, 2026-09-04): the receipt already says the
      // list is built, so it's the natural place to ask whether she wants
      // to go plan the trip from here. Only offered when approving
      // actually put something on the list — a week that needed nothing
      // has nowhere useful to send her. This is also the screen's one
      // apricot primary (Rule 5): renderWeekReviewBand renders nothing
      // once a week is approved, so nothing else on Meals is competing
      // for it.
      var added = data.approved_grocery_added || 0;
      var handoffHtml = '';
      if (added && !weekReceiptHandoffDismissed[data.weekly_plan_id]) {
        handoffHtml =
          '<div class="week-receipt-handoff">' +
            '<div class="week-receipt-handoff-line">' +
              escapeHtml('Your list is ready — ' + added + (added === 1 ? ' item.' : ' items.')) +
            '</div>' +
            '<div class="week-receipt-handoff-actions">' +
              '<button type="button" class="btn-gold" id="week-receipt-go">Take me to the list</button>' +
              '<button type="button" class="week-receipt-not-now" id="week-receipt-not-now">Not now</button>' +
            '</div>' +
          '</div>';
      }

      // The freezer-check ask card (Loop Board "Defrost check: ask at
      // approval") — the same moment the "your list is ready" handoff
      // above appears is the natural place to also ask what's frozen,
      // since inventory is deferred policy and most households never
      // track a freezer item any other way. Asked once per plan
      // (data.defrost_asked_at gates it); forceShow is the one-shot
      // override the Cook view's re-ask link sets, consumed here whether
      // or not there turns out to be anything to ask about.
      var forceDefrostShow = defrostAskState.forceShow;
      defrostAskState.forceShow = false;
      var defrostHtml = '';
      if (!data.defrost_asked_at || forceDefrostShow) {
        if (defrostAskState.planId === data.weekly_plan_id && defrostAskState.items !== null) {
          if (defrostAskState.items.length) defrostHtml = defrostAskCardHtml();
        } else {
          ensureDefrostAskItems(panel, data); // re-renders this row once it resolves
        }
      }

      // The cook-ahead ask, directly under the freezer check — the same
      // once-per-plan shape (cook_ahead_asked_at gates it, forceShow is
      // the Cook view's one-shot override) and, like it, rendered only
      // when there is actually a repeated dish to ask about.
      var forceCookAheadShow = cookAheadAskState.forceShow;
      cookAheadAskState.forceShow = false;
      var cookAheadAskHtml = '';
      if (!data.cook_ahead_asked_at || forceCookAheadShow) {
        if (cookAheadAskState.planId === data.weekly_plan_id && cookAheadAskState.items !== null) {
          if (cookAheadAskState.items.length) cookAheadAskHtml = cookAheadAskCardHtml();
        } else {
          ensureCookAheadAskItems(panel, data); // re-renders this row once it resolves
        }
      }

      row.innerHTML =
        '<div class="shell-card week-receipt-card">' +
          '<div class="week-receipt-eyebrow">' + escapeHtml(eyebrow) + '</div>' +
          '<div class="week-receipt-body">' + escapeHtml(receiptBodyText(data)) + '</div>' +
          handoffHtml +
          // The receipt is the moment a household is most likely to notice
          // the week wasn't quite right, so the way to fix that permanently
          // is offered right here rather than only on the settings screen
          // they'd have to go looking for.
          '<button type="button" class="week-setup-link" id="week-setup-link">' +
            'Weeks not landing how you’d like? Let’s adjust your setup →</button>' +
          // Not "un-approve". Reopening lets the week be edited again and
          // never takes anything off the shopping list — re-approving only
          // adds what's new. Removing items somebody may already have
          // bought is worse than a slightly long list.
          '<button type="button" class="week-reopen-btn" id="week-reopen-btn">Reopen the week</button>' +
        '</div>' +
        defrostHtml +
        cookAheadAskHtml;
      if (defrostHtml) wireDefrostAskCard(row, panel, data);
      if (cookAheadAskHtml) wireCookAheadAskCard(row, panel, data);
      row.querySelector('#week-reopen-btn').addEventListener('click', function () { reopenWeek(panel, data); });
      row.querySelector('#week-setup-link').addEventListener('click', openMealSetup);
      var goBtn = row.querySelector('#week-receipt-go');
      if (goBtn) {
        goBtn.addEventListener('click', function () {
          // Plan stops is the screen that's actually about the trip Emily
          // just asked to be walked toward, not just the list itself.
          activateTab('grocery', true, { groScreen: 'plan' });
        });
      }
      var notNowBtn = row.querySelector('#week-receipt-not-now');
      if (notNowBtn) {
        notNowBtn.addEventListener('click', function () {
          weekReceiptHandoffDismissed[data.weekly_plan_id] = true;
          var handoff = row.querySelector('.week-receipt-handoff');
          if (handoff) handoff.remove();
        });
      }
      return;
    }

    // A draft's Approve/Try again/Change my answers now render up in
    // #week-review-band (renderWeekReviewBand, called from renderWeekMenu)
    // instead of here — that's the whole point of the review-landing
    // change: the decision is above the fold, not after the day rail and
    // day card. Leaving this row empty (rather than duplicating the same
    // apricot button in two places) also keeps Rule 5 — one apricot
    // primary per screen.
    row.innerHTML = '';
  }

  // ---------- The review band (Loop Board: "land on the review moment") ----
  // First child of #week-plan-view (see buildWeekPanel), so a draft's
  // decision is the first thing on the Meals screen at both breakpoints —
  // no scroll, no separate arrival screen. Renders nothing once the week is
  // approved (renderWeekApproval's receipt, further down, takes over) or
  // when there's no plan at all yet.
  function renderWeekReviewBand(panel, data, statusLine) {
    var band = panel.querySelector('#week-review-band');
    if (!band) return;
    if (!data.weekly_plan_id || data.status === 'approved') { band.innerHTML = ''; return; }

    var openCount = countOpenSlots(data);
    band.innerHTML =
      '<div class="shell-card week-approve-card">' +
        '<div class="week-review-eyebrow">DRAFT · YOUR TURN</div>' +
        (statusLine ? '<div class="week-note">' + escapeHtml(statusLine) + '</div>' : '') +
        // Said once, ever: the first week where the app rounded a meal out
        // for them (app/tools/plates.py, weekly_plan.PLATES_INTRO). The
        // server decides whether it appears and marks it as said, so this
        // is simply "show it if it's there" — sitting under the status line
        // and above the promise, because it explains something about the
        // week they're being asked to approve.
        (data.plates_note ? '<div class="week-note">' + escapeHtml(data.plates_note) + '</div>' : '') +
        // A possible allergy/must-avoid clash, named above the Approve
        // button rather than left for the household to catch. Server-worded
        // (see check_plan_conflicts) so the sentence lives with the data it
        // describes, and absent entirely when there's nothing to say.
        (data.conflicts_note
          ? '<div class="week-note week-conflict-note">' + escapeHtml(data.conflicts_note) + '</div>'
          : '') +
        '<div class="week-approve-promise">' + escapeHtml(groceryPromiseText(data.grocery_preview)) + '</div>' +
        // Approving with a slot still open is allowed, but named — never a
        // silent shortfall.
        '<button type="button" class="btn-gold week-approve-btn" id="week-approve-btn">' +
          (openCount ? escapeHtml(approveWithOpenLabel(data, openCount)) : 'Approve the week') +
        '</button>' +
        // Same idiom as "or start over" (.week-reset-link) — one quiet
        // Newsreader-italic text link under the primary button, not a
        // second button competing with it. Prefills the composer rather
        // than opening a blank chat, echoing the first-week-reveal
        // branch's "or tweak it with me" pattern for the everyday flow.
        '<button type="button" class="week-reset-link week-tweak-link" id="week-tweak-btn">or tweak it with me</button>' +
        // DECISIONS.md #3: two actions, because they're different needs.
        // One button labelled "Redo" can only be one of them, and would be
        // the wrong one half the time.
        '<div class="week-redo-row">' +
          '<button type="button" class="week-redo-btn" id="week-try-again">Try again</button>' +
          '<button type="button" class="week-redo-btn" id="week-change-answers">Change my answers</button>' +
        '</div>' +
        // Empty and hidden until "Try again" is tapped — the rebuild is a
        // ~30-second call that until now showed nothing but a disabled
        // button, so this is where the rotating waiting line goes
        // (static/waiting-lines.js, the same component the first-week
        // reveal and /plan-week's drafting step use).
        '<div class="week-redo-waiting waiting-line" id="week-redo-waiting" hidden></div>' +
      '</div>';
    band.querySelector('#week-approve-btn').addEventListener('click', function () { approveWeek(panel, data); });
    band.querySelector('#week-tweak-btn').addEventListener('click', function () {
      openAskSheet('Let’s tweak this week — ');
    });
    band.querySelector('#week-try-again').addEventListener('click', function () { tryAgain(panel, data); });
    band.querySelector('#week-change-answers').addEventListener('click', function () {
      // The same day count tryAgain() already passes when it rebuilds this
      // plan — changing your answers must not also silently change how
      // many days you are answering about.
      startPlanningWeek(data.week_start_date, data.day_count || 7);
    });
  }

  function countOpenSlots(data) {
    var n = 0;
    (data.days || []).forEach(function (day) {
      WEEK_SLOTS.forEach(function (s) { if (day[s] && day[s].state === 'open') n++; });
    });
    return n;
  }

  function approveWithOpenLabel(data, openCount) {
    if (openCount > 1) return 'Approve — leave ' + openCount + ' slots open';
    var openDay = null;
    (data.days || []).forEach(function (day) {
      WEEK_SLOTS.forEach(function (s) { if (day[s] && day[s].state === 'open' && !openDay) openDay = day.date; });
    });
    return 'Approve — leave ' + dayName(openDay, { weekday: 'long' }) + ' open';
  }

  async function tryAgain(panel, data) {
    // "Same inputs, a different week" — regenerate from the answers already
    // on record, without asking them again. The other half of Redo,
    // "Change my answers", goes back to Q1 with everything prefilled.
    var btn = panel.querySelector('#week-try-again');
    if (btn) { btn.disabled = true; btn.textContent = 'Rebuilding…'; }
    // This one posts to the plain /generate, which streams nothing back —
    // there are no real stages to key to, so it gets the generic lines
    // rather than a stage it can't honestly claim to be in.
    var waitEl = panel.querySelector('#week-redo-waiting');
    var waiter = null;
    if (waitEl && window.PomonaWaiting) {
      waitEl.hidden = false;
      waiter = window.PomonaWaiting.startWaitingLines(waitEl, { stage: 'generic' });
    }
    function stopWaiting() {
      if (waiter) { waiter.stop(); waiter = null; }
      if (waitEl) { waitEl.hidden = true; waitEl.textContent = ''; }
    }
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(data.week_start_date) + '/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // "Same answers, a different week" has to mean the same DAYS too.
        // An empty body means seven days from the filing key, which for a
        // custom period (Thursday to next Thursday, filed under the
        // Thursday) would quietly rebuild it as a plain seven — a rebuild
        // that silently changes the question is not a rebuild.
        body: JSON.stringify({
          day_count: data.day_count || 7,
          period_start: data.period_start_date || data.week_start_date
        })
      });
      if (!res.ok) throw new Error('regenerate failed');
      await res.json();
      // Before loadWeekMenu, which rebuilds this band from scratch and
      // would otherwise leave the interval ticking against a detached node.
      stopWaiting();
      showToast('Same answers, a different week.');
      await loadWeekMenu(panel);
    } catch (err) {
      console.warn('Regenerating the week failed:', err);
      stopWaiting();
      if (btn) { btn.disabled = false; btn.textContent = 'Try again'; }
      alert('Could not rebuild the week right now — try again in a moment.');
    }
  }

  async function reopenWeek(panel, data) {
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(data.week_start_date) + '/reopen', { method: 'POST' });
      if (!res.ok) throw new Error('reopen failed');
      await res.json();
      showToast('Open again. Nothing has come off your shopping list.');
      await loadWeekMenu(panel);
    } catch (err) {
      console.warn('Reopening the week failed:', err);
      alert('Could not reopen the week right now — try again in a moment.');
    }
  }

  // The permanent entry into the two question screens. The Sunday nudge on
  // Today is dismissible and week-scoped; this one never goes away, which
  // is what makes "it'll be waiting for you under Meals" true.
  function renderPlanWeekEntry(panel, data) {
    var row = panel.querySelector('#week-plan-row');
    if (!row) return;
    // Both weeks, named by their real dates. This used to offer NEXT week
    // and only next week, which left no discoverable way to plan or
    // re-plan the week you're actually living in — the nudge could reach
    // it, but the nudge is dismissible and disappears once the week has any
    // plan at all. An entry point that can only reach one week isn't a
    // permanent entry point, it's the nudge with extra steps.
    // Where "this week" starts is the household's answer, not the
    // calendar's — planningPeriodDefault is the rhythm-derived suggestion
    // from /api/week/planning-period (the morning after their chosen
    // ready day, e.g. Saturday for a household ready by Friday; today,
    // three days, for a household planning as it goes).
    // Falls back to the Monday while that request is in flight or has
    // failed, which is exactly what this offered before it existed.
    var defaultStart = (planningPeriodDefault && planningPeriodDefault.start_date) || thisWeekStartLocal();
    // How LONG this household's period runs, from the same suggestion.
    // Seven used to be hardcoded here in three places at once — the second
    // button's start day, both buttons' date labels, and the URL they
    // opened — so an "as we go" household (day_count 3) was offered two
    // seven-day weeks and then handed a seven-day intake. Seven stays the
    // fallback for the moment before the suggestion lands and for a failed
    // lookup, exactly as the Monday start does.
    var dayCount = (planningPeriodDefault && planningPeriodDefault.day_count) || 7;
    var periods = [
      { start: defaultStart, which: 'current' },
      { start: addDaysLocal(defaultStart, dayCount), which: 'next' }
    ];
    row.innerHTML =
      '<div class="shell-card week-plan-row">' +
        '<div class="week-plan-text">' +
          '<div class="week-plan-title">Plan a week</div>' +
          '<div class="week-plan-sub">Two rounds of questions, then I’ll draft it. Nothing gets bought until you approve.</div>' +
        '</div>' +
        '<div class="week-plan-buttons">' +
          periods.map(function (p) {
            // "Re-plan" rather than "Plan" when that stretch already has a
            // plan, so the button never understates what it's about to do.
            var planned = weekIsPlanned(data, p.start);
            return '<button type="button" class="btn-outline-plum week-plan-btn" data-week="' + p.start + '">' +
              '<span class="week-plan-btn-label">' + escapeHtml(planEntryLabel(dayCount, p.which, planned)) + '</span>' +
              '<span class="week-plan-btn-dates">' + escapeHtml(periodRangeLabel(p.start, dayCount)) + '</span>' +
            '</button>';
          }).join('') +
        '</div>' +
        // One tap deeper, on its own full-width line rather than as a third
        // item beside the two week buttons. Not styling: three flex items
        // in that row crushed the card's text column to one character per
        // line on the desktop rail (measured, not guessed). It is also
        // deliberately not shaped like those buttons — the two weeks stay
        // the answer for almost everybody, and this is the door for the
        // household whose week simply isn't one of them. Its own 44px row
        // (hard rule 6) rather than a text link sized to its words.
        '<button type="button" class="week-period-open" id="week-period-open" aria-expanded="false">Pick my own days</button>' +
        '<div class="week-period-picker" id="week-period-picker" hidden></div>' +
      '</div>' +
      // The standing way in to the setup screen. The receipt offers it too,
      // at the moment a week has just landed and its shortcomings are
      // freshest — but a household shouldn't have to approve something to
      // reach its own settings.
      '<button type="button" class="week-setup-link" id="week-setup-standing">' +
        'Weeks not landing how you’d like? Let’s adjust your setup →</button>';
    row.querySelectorAll('.week-plan-btn').forEach(function (btn) {
      btn.addEventListener('click', function () { startPlanningWeek(btn.dataset.week, dayCount); });
    });
    row.querySelector('#week-setup-standing').addEventListener('click', openMealSetup);
    wirePeriodPicker(row, defaultStart, dayCount);
  }

  // What the two buttons are called. Seven days is still "this week" /
  // "next week" — that is simply what a week is called, and calling it
  // "the next 7 days" would make the common case read as machinery. A
  // household on a shorter horizon is NOT planning a week, though, and
  // being told it is was half of the bug this wording fixes.
  // ASSUMPTION (Emily's call): "Plan the next 3 days" / "Plan the 3 after".
  function planEntryLabel(dayCount, which, planned) {
    var verb = planned ? 'Re-plan ' : 'Plan ';
    if (dayCount === 7) return verb + (which === 'current' ? 'this week' : 'next week');
    var unit = dayCount + (dayCount === 1 ? ' day' : ' days');
    // "the 3 after", not "the 3 days after" — the dates line right under it
    // says which three, so the second "days" is a word that isn't earning
    // its place.
    return verb + (which === 'current' ? 'the next ' + unit : 'the ' + dayCount + ' after');
  }

  // The custom-range picker: ONE strip of days, inline in the card that
  // opened it. Tap the first day, tap the last, and the days between fill
  // in. Not a sheet and not a page — it is a refinement of the choice
  // already on screen, and taking over the screen for it would make
  // picking Thursday feel heavier than picking Monday.
  //
  // This REPLACES a start-day strip plus a row of length chips (3 days /
  // 5 days / 1 week / to the same day next week / 2 weeks). That shape was
  // built to a note reading "keep it two taps, not a calendar widget";
  // Emily looked at it on 2026-09-05 and asked to let people choose the
  // date range directly, so that note no longer holds and nobody should
  // re-argue it from the comment that used to sit here. Two taps survive
  // anyway — the first day and the last one.
  //
  // Every user-facing string lives in one place so the wording is a
  // one-line change rather than a hunt through the render.
  var PERIOD_PICKER_COPY = {
    open: 'Pick my own days',
    close: 'Never mind',
    // Live. The first reads while a whole range is showing; the second
    // takes over once a first day has been tapped and the last one is the
    // only thing missing.
    hintBoth: 'Tap the first day, then the last',
    hintEnd: 'Tap the last day',
    confirm: 'Plan these days'
  };

  // How far past today the strip runs — about three weeks, which is as far
  // ahead as anyone has been observed to plan and still short enough to
  // scroll by thumb.
  var PERIOD_STRIP_DAYS = 21;
  // The ceiling on a range, and it is not a design choice: /plan-week
  // clamps its `days` parameter to 1..28 (see static/plan-week.html), so a
  // longer range would arrive there silently shortened. Enforced here so
  // the button never names dates the next screen won't honour.
  var PERIOD_MAX_DAYS = 28;

  function wirePeriodPicker(row, defaultStart, defaultDays) {
    var opener = row.querySelector('#week-period-open');
    var picker = row.querySelector('#week-period-picker');
    if (!opener || !picker) return;
    // `start` is always set. `end` is '' exactly while a range is
    // half-chosen — which is also the whole of the confirm button's
    // disabled condition.
    var state = { start: defaultStart, end: '' };

    opener.addEventListener('click', function () {
      var opening = picker.hidden;
      picker.hidden = !opening;
      opener.setAttribute('aria-expanded', opening ? 'true' : 'false');
      opener.textContent = opening ? PERIOD_PICKER_COPY.close : PERIOD_PICKER_COPY.open;
      if (opening) {
        // Opens on the household's OWN period — the same days the first
        // button offers — rather than on nothing at all. An "as we go"
        // household opens on its three days, a weekly one on its week,
        // and either can retap from there.
        state = {
          start: defaultStart,
          end: addDaysLocal(defaultStart, Math.max(1, defaultDays || 7) - 1)
        };
        renderPicker();
      }
    });

    function stripDays() {
      // Today forward, because "from today" is the request this control
      // exists to answer. The one exception: a household's own period can
      // have started BEFORE today (a Monday-anchored week, opened on a
      // Thursday), and a strip that couldn't show the preselected range
      // would open with nothing selected on it. So it begins at the
      // earlier of the two and always reaches PERIOD_STRIP_DAYS past
      // today.
      var today = todayLocalStr();
      var first = defaultStart < today ? defaultStart : today;
      var stop = addDaysLocal(today, PERIOD_STRIP_DAYS - 1);
      var days = [];
      for (var d = first; d <= stop; d = addDaysLocal(d, 1)) days.push(d);
      return days;
    }

    function pickDay(day) {
      if (!state.end) {
        // A range is half-chosen. A day on or after the start closes it —
        // including the start itself, which is a legitimate one-day range
        // ("just tonight"), not a mis-tap to swallow. A day BEFORE the
        // start isn't an end at all; it's somebody restarting, so it
        // becomes the new first day.
        if (day < state.start) {
          state.start = day;
        } else {
          var cap = addDaysLocal(state.start, PERIOD_MAX_DAYS - 1);
          state.end = day <= cap ? day : cap;
        }
      } else {
        // A whole range is showing; any tap starts a new one.
        state = { start: day, end: '' };
      }
      renderPicker();
    }

    function chosenDayCount() {
      // Inclusive of both ends: Sep 5 to Sep 7 is three days, not two.
      return daysBetweenLocal(state.start, state.end) + 1;
    }

    function renderPicker() {
      var days = stripDays();
      var last = state.end || state.start;
      // Every tap rebuilds the strip, and a rebuilt element starts at
      // scrollLeft 0 — so scrolling right to tap the 20th snapped the
      // strip back to today and put the day you had just chosen off
      // screen. Carried across by hand; there is nothing else holding it.
      var prevScroll = picker.querySelector('.week-period-scroll');
      prevScroll = prevScroll ? prevScroll.scrollLeft : 0;
      picker.innerHTML =
        '<div class="week-period-group">' +
          '<div class="week-period-hint">' +
            escapeHtml(state.end ? PERIOD_PICKER_COPY.hintBoth : PERIOD_PICKER_COPY.hintEnd) +
          '</div>' +
          '<div class="week-period-scroll" role="group" aria-label="Days to plan">' +
            days.map(function (d) {
              var isEdge = d === state.start || d === state.end;
              var inRange = d >= state.start && d <= last;
              return '<button type="button" class="week-period-day' +
                (isEdge ? ' is-on' : (inRange ? ' is-in' : '')) +
                '" data-day="' + d + '" aria-pressed="' + (inRange ? 'true' : 'false') + '">' +
                '<span class="week-period-dow">' + escapeHtml(dayNameShort(d)) + '</span>' +
                '<span class="week-period-num">' + new Date(d + 'T00:00:00').getDate() + '</span>' +
              '</button>';
            }).join('') +
          '</div>' +
        '</div>' +
        // The whole point of the confirm row: it names the actual dates, so
        // nobody taps it wondering what they just chose. With only a first
        // day chosen there are no dates to name yet, so it has nothing to
        // say and cannot be tapped.
        '<button type="button" class="btn-outline-plum week-period-go" id="week-period-go"' +
          (state.end ? '' : ' disabled') + '>' +
          '<span class="week-plan-btn-label">' + escapeHtml(PERIOD_PICKER_COPY.confirm) + '</span>' +
          (state.end
            ? '<span class="week-plan-btn-dates">' +
                escapeHtml(periodRangeLabel(state.start, chosenDayCount())) + '</span>'
            : '') +
        '</button>';

      var scroll = picker.querySelector('.week-period-scroll');
      scroll.scrollLeft = prevScroll;
      picker.querySelectorAll('[data-day]').forEach(function (b) {
        b.addEventListener('click', function () { pickDay(b.dataset.day); });
      });
      // Then make sure the range you just finished is actually on screen.
      // Only this container scrolls — scrollIntoView would take the page
      // with it, moving the whole card under the thumb that just tapped.
      // The LAST of the range's two ends, which is the one a tap most
      // likely just put out of view. (:last-of-type would have meant "the
      // last button, if it happens to be chosen" — a different thing.)
      var edges = picker.querySelectorAll('.week-period-day.is-on');
      var edge = edges[edges.length - 1];
      if (edge) {
        var left = edge.offsetLeft, right = left + edge.offsetWidth;
        if (right > scroll.scrollLeft + scroll.clientWidth) {
          scroll.scrollLeft = right - scroll.clientWidth;
        } else if (left < scroll.scrollLeft) {
          scroll.scrollLeft = left;
        }
      }
      picker.querySelector('#week-period-go').addEventListener('click', function () {
        if (!state.end) return;
        startPlanningWeek(state.start, chosenDayCount());
      });
    }
  }

  function daysBetweenLocal(fromIso, toIso) {
    // Rounded rather than floored: both ends are local midnight, so a DST
    // boundary between them makes the difference 23 or 25 hours.
    var a = new Date(fromIso + 'T00:00:00');
    var b = new Date(toIso + 'T00:00:00');
    return Math.round((b.getTime() - a.getTime()) / 86400000);
  }

  function addDaysLocal(isoDate, n) {
    // Built from local date fields for the same reason todayLocalStr is —
    // toISOString() is UTC and gets the day wrong either side of midnight.
    var d = new Date(isoDate + 'T00:00:00');
    d.setDate(d.getDate() + n);
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + m + '-' + day;
  }

  function dayNameShort(isoDate) {
    return new Date(isoDate + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short' });
  }

  function periodRangeLabel(startDate, dayCount) {
    // "Sep 11–18". The client twin of the server's _format_period_range,
    // kept in step deliberately — the two are read side by side, one on
    // this button and one on the draft's eyebrow after it is tapped.
    var start = new Date(startDate + 'T00:00:00');
    var end = new Date(start.getTime());
    end.setDate(end.getDate() + Math.max(1, dayCount) - 1);
    var startMonth = start.toLocaleDateString('en-US', { month: 'short' });
    if (start.getTime() === end.getTime()) return startMonth + ' ' + start.getDate();
    if (start.getMonth() === end.getMonth()) {
      return startMonth + ' ' + start.getDate() + '–' + end.getDate();
    }
    return startMonth + ' ' + start.getDate() + '–' +
      end.toLocaleDateString('en-US', { month: 'short' }) + ' ' + end.getDate();
  }

  function weekIsPlanned(data, weekStart) {
    // Only the week currently on screen is known for certain from this
    // payload. For the other one the button says "Plan", and /plan-week
    // itself states what it found when it opens — better an understated
    // button than a second lookup on every Meals render.
    return !!(data.weekly_plan_id && data.week_start_date === weekStart);
  }

  function thisWeekStartLocal() {
    var d = new Date();
    var daysSinceMonday = (d.getDay() + 6) % 7;   // JS weeks start on Sunday
    d.setDate(d.getDate() - daysSinceMonday);
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + m + '-' + day;
  }

  function openMealSetup() {
    // A full page, like /plan-week and /onboarding — see app/main.py.
    window.location.href = '/meal-setup';
  }

  async function approveWeek(panel, data) {
    // Who is approving. There is no per-person login in this app (see
    // tools.get_household_people), so with two adults on record the only
    // honest way to name one on the receipt is to ask which one is here —
    // a single tap, and it is also the household's confirm step. One adult
    // (or none) needs no question: approve straight away.
    var people = (data.other_adults || []);
    var approvedBy = '';
    if (people.length > 1) {
      approvedBy = await askWhoIsApproving(people);
      if (approvedBy === null) return;
    } else if (people.length === 1) {
      approvedBy = people[0];
    }
    await submitWeekApproval(panel, data, approvedBy, false);
  }

  // Posts the approval. `confirmHardConflicts` is only ever true right
  // after the household has tapped the "Approve anyway" button that
  // showApproveConfirm renders below — never inferred, never set on the
  // first tap.
  async function submitWeekApproval(panel, data, approvedBy, confirmHardConflicts) {
    var btn = panel.querySelector('#week-approve-btn');
    var restoreLabel = btn ? btn.textContent : 'Approve the week';
    if (btn) { btn.disabled = true; btn.textContent = 'Approving…'; }
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(data.week_start_date) + '/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved_by: approvedBy, confirm_hard_conflicts: !!confirmHardConflicts })
      });
      if (!res.ok) throw new Error('approve failed');
      // The approve endpoint answers with the dietary check it ran on the
      // way through (app/main.py's approve route, tools.approve_weekly_plan).
      // This used to throw that answer away and show a fixed line, so a
      // household that approved past the draft's warning — or never saw it —
      // was told only that their list was coming. Approving is allowed to
      // clash; being quiet about it is not.
      var approval = {};
      try { approval = (await res.json()) || {}; } catch (e) { approval = {}; }

      if (approval.status === 'needs_confirmation') {
        // A HARD clash and no confirm tap yet — nothing was approved,
        // nothing was written. Same idiom as the open-slots label above
        // (approveWithOpenLabel: say what tapping again will do), except
        // this one needs an actual second tap rather than just a relabeled
        // button, because what's at stake here is a real allergy clash,
        // not an empty slot.
        showApproveConfirm(panel, data, approval, approvedBy);
        return;
      }

      var openListAction = {
        label: 'Open the list',
        onClick: function () { activateTab('grocery', true, { groScreen: 'plan' }); }
      };
      if (approval.conflicts_note) {
        showToast('Approved. ' + approval.conflicts_note, openListAction, 9000);
      } else {
        showToast('Approved. I’ll get your list together.', openListAction);
      }
      // Approving is the one action in this app that writes to the grocery
      // list wholesale, so anything already showing that list is stale the
      // moment it succeeds — the same staleness
      // refreshStaleTabsFromActions handles for chat-driven changes, just
      // reached by a button instead of a sentence.
      refreshGrocerySurfaces();
      await loadWeekMenu(panel);
    } catch (err) {
      console.warn('Week approval failed:', err);
      if (btn) { btn.disabled = false; btn.textContent = restoreLabel; }
      alert('Could not approve the week right now — try again in a moment.');
    }
  }

  // The one-clash-away state: the review band above already names the
  // clash (data.conflicts_note, rendered before anyone even tapped
  // Approve), so this only has to offer the two ways through — approve
  // past it, or back out and fix the plan first. Only
  // submitWeekApproval's needs_confirmation branch ever calls this.
  function showApproveConfirm(panel, data, approval, approvedBy) {
    var band = panel.querySelector('#week-review-band');
    var card = band && band.querySelector('.week-approve-card');
    var btn = card && card.querySelector('#week-approve-btn');
    if (!card || !btn) return;

    var meals = [];
    (approval.conflicts || []).forEach(function (c) {
      if (c.meal && meals.indexOf(c.meal) === -1) meals.push(c.meal);
    });
    // Named when it's cheap (one dish sitting right there); a generic
    // "the clash" once there's more than one, same call the server's own
    // conflicts_note sentence makes.
    var label = (meals.length === 1)
      ? 'Approve anyway — I’ve seen the ' + meals[0] + ' clash'
      : 'Approve anyway — I’ve seen the clash';
    btn.textContent = label;
    btn.disabled = false;
    // Swap the handler rather than stack a second listener on top of the
    // original "approve the week" one — this tap now means something
    // different.
    var freshBtn = btn.cloneNode(true);
    btn.parentNode.replaceChild(freshBtn, btn);
    freshBtn.addEventListener('click', function () {
      submitWeekApproval(panel, data, approvedBy, true);
    });

    // A quiet way out — same idiom as "or tweak it with me" just below,
    // not a second button competing with the confirm for attention.
    // Reloading the week menu is what takes both of them back to the
    // ordinary draft state, same as tryAgain/reopenWeek already do after
    // their own actions.
    var tweakLink = card.querySelector('#week-tweak-btn');
    var fixLink = card.querySelector('#week-fix-first-link');
    if (!fixLink) {
      fixLink = document.createElement('button');
      fixLink.type = 'button';
      fixLink.id = 'week-fix-first-link';
      fixLink.className = 'week-reset-link week-tweak-link';
      fixLink.textContent = 'Let me fix it first';
      if (tweakLink && tweakLink.parentNode) {
        tweakLink.parentNode.insertBefore(fixLink, tweakLink);
      } else {
        card.appendChild(fixLink);
      }
    }
    fixLink.onclick = function () { loadWeekMenu(panel); };
  }

  function renderWeekMenu(panel, data) {
    weekState.data = data;
    var todayStr = todayLocalStr();
    var days = (data.days || []).map(function (d) { return Object.assign({}, d, classifyDay(d, todayStr)); });
    weekState.days = days;

    // The one sentence about the state of the week, kept exactly as it
    // was: a draft says whatever the server's headline says, an approved
    // week says it's set, and anything with a gap counts the gaps. It
    // feeds the review band; the root card's own subtitle says the shape
    // of the week instead ("4 cooks, 3 made ahead"), which is a different
    // fact and deliberately not the same sentence twice.
    var emptyAheadCount = days.filter(function (d) { return d.needsDecision; }).length;
    var statusLine = '';
    if (data.weekly_plan_id && days.length) {
      statusLine = (data.status !== 'approved' && data.headline)
        ? data.headline
        : (emptyAheadCount === 0
            ? 'Your week is set.'
            : (emptyAheadCount === 1 ? 'One meal still needs a decision.' : emptyAheadCount + ' meals still need a decision.'));
    }

    // Default the day pointer to today the first time this loads; preserve
    // whatever day the household was already on across a refresh (settling
    // a slot, a chat edit) so the screen never moves under their thumb.
    if (!days.length) {
      weekState.selectedIndex = null;
      weekState.step = 'week';
    } else if (weekState.selectedIndex === null || weekState.selectedIndex >= days.length) {
      var todayIndexForSelect = days.reduce(function (found, d, i) { return d.isToday ? i : found; }, -1);
      weekState.selectedIndex = todayIndexForSelect >= 0 ? todayIndexForSelect : 0;
    }

    renderWeekReviewBand(panel, data, statusLine);
    renderWeekApproval(panel, data);
    renderMealsStep(panel);
    renderWeekSheetRows(days);
    // Chat's "See your week" chip may have asked for a specific day before
    // this week's days existed — now they do. No-op unless one is pending.
    applyPendingDayFocus(panel);
  }

  // ---------- Cook: the Meals tab's second state (InnCooker) ----------
  //
  // The same /api/cooker-view data static/cooker.html always used, re-ranked
  // rather than re-listed. That page rendered the whole week as one flat
  // stack and then scrolled you to today; this one answers "what am I
  // cooking now" in the hero and demotes everything else beneath it —
  // tonight, then the prep that feeds it, then the rest of the week as a
  // quiet list.
  //
  // Everything the old page could do, this does: check a meal or a prep
  // task off, expand a recipe, scale the servings live, see why a meal was
  // chosen, fill in a missing recipe, work the attention banner, and drive
  // any of it hands-free. No endpoint changed.
  //
  // Which meal is "tonight" is decided by the clock, not by what has been
  // ticked — households reliably forget to check breakfast off, and "the
  // next unticked meal" parks the screen on breakfast all day. Carried over
  // from cooker.html deliberately, comment and all.
  var COOK_SLOT_ORDER = ['breakfast', 'lunch', 'dinner', 'snack'];
  var cookState = {
    data: null,
    attention: [],
    attentionOpen: false, // folded by default — see cookAttentionHtml
    loadError: false,
    tonightIdx: null,
    // Focused single-meal cook mode: a state of this same screen, exactly
    // like Grocery's shopping mode is a state of Grocery — never a route,
    // never a page with its own header. 'overview' is the tonight-hero +
    // prep-rail + week-list screen this always starts on; 'focus' takes
    // the whole screen over for one meal until "back to the week" or a
    // check-off returns you. See cookEnterFocus/cookExitFocus.
    // 'session' is the third state of this same screen (Loop Board "Prep
    // days"): one prep day's whole list, ticked off item by item. Same
    // shape as 'focus' and reached the same way — never a route, never a
    // page with a header of its own.
    screen: 'overview',
    sessionDate: null,   // which prep session is focused, by its ISO date —
                         // an id would not survive a re-render, since a
                         // session is computed rather than stored
    prepCutPicks: {},    // entry_id -> { ingredient item: true } — which raw
                         // components are ticked in the "Prep-cut on Sunday?"
                         // offer, until the write lands (see cookPrepCutHtml)
    focusIdx: null,      // index into cookState.data.meals, while focused
    focusScrollTo: null, // 'ingredients' | null — landed-on section, once
    // Set by a "Start cooking"/"Cook this" deep link that arrives before the
    // view has ever loaded. Either `true` (the old "focus whatever tonight
    // turns out to be" behaviour, still used by callers that have no
    // specific meal in hand) or `{ entryId }` naming the exact meal_plan
    // entry to land on — see cookResolveFocusIndex.
    pendingFocusTarget: false,
    pendingScrollTop: false,    // this render is a screen change, not a re-paint — reset scroll instead of preserving it
    focusStepsChecked: {},      // 'idx:stepPos' -> true — tap-to-check on Do-ahead/Day-of steps, client-side only (see cookStepLi)
    cookAheadPicks: {},         // source entry_id -> { covered entry_id: true } — the cook-ahead chips as they stand between taps; seeded from the server's own `selected` and dropped again on every load or write (see cookAheadPicks)
    voiceSession: null,
    voiceContext: null, // { type: 'prep' } | { type: 'meal', idx }
    voiceStepCursor: {},
    voiceLog: []
  };

  var COOK_ICONS = {
    list:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 5.5h14"/><path d="M5 12h14"/><path d="M5 18.5h9"/></svg>',
    check:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7"/></svg>',
    mic:
      '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z"/><path d="M19 11a1 1 0 1 0-2 0 5 5 0 0 1-10 0 1 1 0 1 0-2 0 7 7 0 0 0 6 6.93V21H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-3.07A7 7 0 0 0 19 11z"/></svg>'
  };

  function cookPanel() { return panels['week']; }
  function cookIsShowing() {
    var p = cookPanel();
    return !!(p && p.dataset.built && p.dataset.mealsView === 'cook');
  }

  // Plan <-> Cook. Both are states of one tab and neither is a route: the
  // Meals path stays /week either way, exactly as Grocery's three segments
  // are all /grocery. Deep links into cooking come through the entry points
  // (Today's "Start cooking", Meals' "Cook this"), not through a URL.
  // focusTarget is how Today's "Start cooking" and Meals' own "Cook this"
  // land directly in the focused screen for ONE meal, rather than on the
  // Cook overview — the literal "entering 'Start cooking' on a meal opens
  // that ONE meal full-screen" the ticket asks for. It's `{ entryId }` when
  // the caller knows exactly which meal_plan entry it means (see
  // cookResolveFocusIndex), or the legacy `true` for a caller that only
  // means "tonight, whatever that turns out to be" — a generic flag with no
  // meal identity, which is how this used to land on the morning's
  // breakfast when tapped before dinner (Loop Board, Emily 2026-09-07).
  // Clicking the Plan/Cook segmented control itself never passes this, so a
  // deliberate switch to Cook still lands on the overview, same as always.
  function setMealsView(view, focusTarget) {
    var panel = cookPanel();
    if (!panel || !panel.dataset.built) return;
    var isCook = view === 'cook';
    panel.dataset.mealsView = isCook ? 'cook' : 'plan';

    var planView = panel.querySelector('#week-plan-view');
    var cookView = panel.querySelector('#week-cook-view');
    if (planView) planView.hidden = isCook;
    if (cookView) cookView.hidden = !isCook;
    // Coming back from Cook: the Plan side decides for itself whether the
    // segmented control shows (a Day or Meal step hides it, same rule
    // Cook's focus screen follows), and Cook may have left it hidden.
    if (!isCook && weekState.data) renderMealsStep(panel);

    panel.querySelectorAll('.meals-seg-btn').forEach(function (btn) {
      var on = btn.getAttribute('data-meals-view') === (isCook ? 'cook' : 'plan');
      btn.classList.toggle('active', on);
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
    });

    // Leaving Cook stops any hands-free session with it — a mic still
    // listening on a screen you can no longer see is the worst version of
    // this feature.
    if (!isCook) stopCookVoice();
    if (isCook && !cookView.dataset.built) {
      cookView.dataset.built = '1';
      cookView.innerHTML = '<p class="cook-empty">Loading&hellip;</p>';
      cookView.addEventListener('click', onCookClick);
      cookState.pendingFocusTarget = focusTarget || false;
      loadCook();
      return;
    }
    // Already built and loaded: a repeat "Start cooking"/"Cook this" tap
    // should land back in the meal it named, not wherever the screen was
    // left — and never in a DIFFERENT meal than the one asked for.
    if (isCook && focusTarget && cookState.data) {
      var idx = cookResolveFocusIndex(cookState.data.meals || [], focusTarget);
      if (idx !== null && cookState.data.meals[idx]) cookEnterFocus(idx);
    }
  }

  // Turns a mealsFocus target into an index into cookState.data.meals — or
  // null when there's nothing to focus. `true` (no meal identity given)
  // falls back to the old "tonight" guess; `{ entryId }` matches the exact
  // meal_plan entry, checking a merged card's `entry_ids` too (component
  // batching collapses several plan entries into one card — see
  // get_cooker_view). An entryId that names a real target but isn't in
  // THIS cooker view (a reheat night with nothing to cook, or the plan
  // changed under it) deliberately returns null rather than falling back
  // to tonightIdx — landing on the overview beats landing on a different
  // meal than the one that was tapped.
  function cookResolveFocusIndex(meals, target) {
    if (target && typeof target === 'object') {
      var list = meals || [];
      var i, m;
      // Most exact first: the entry itself (or a merged card carrying it).
      if (target.entryId != null) {
        for (i = 0; i < list.length; i++) {
          m = list[i];
          if (m.entry_id === target.entryId) return i;
          if (m.entry_ids && m.entry_ids.indexOf(target.entryId) !== -1) return i;
        }
      }
      // Then the same date + slot — a day-based card that lost its id.
      if (target.date && target.slot) {
        for (i = 0; i < list.length; i++) {
          m = list[i];
          if (m.date === target.date && m.slot === target.slot) return i;
        }
      }
      // Then the dish by name — a component-based week's menu carries no
      // entry ids or real dates, only titles, and its cook cards are
      // merged by name (get_cooker_view), so the name IS the identity.
      if (target.title) {
        var want = String(target.title).trim().toLowerCase();
        for (i = 0; i < list.length; i++) {
          if (String(list[i].meal || '').trim().toLowerCase() === want) return i;
        }
      }
      // Nothing matched: the overview, never a different meal.
      return null;
    }
    return target ? cookState.tonightIdx : null;
  }

  async function loadCook() {
    var panel = cookPanel();
    if (!panel) return;
    var view = panel.querySelector('#week-cook-view');
    if (!view || !view.dataset.built) return;
    try {
      var pair = await Promise.all([
        fetch('/api/cooker-view'),
        fetch('/api/attention')
      ]);
      if (!pair[0].ok) throw new Error('cooker-view failed');
      cookState.data = await pair[0].json();
      cookState.attention = pair[1].ok ? ((await pair[1].json()).items || []) : [];
      cookState.loadError = false;
      // Which meal is "tonight" is decided HERE, on a real load, and then
      // pinned — not recomputed on every render. cookTonightIndex prefers an
      // uncooked meal, so recomputing after a write meant that ticking
      // tonight's dinner as cooked threw it out of the hero and replaced it
      // with the evening snack: the screen moved out from under the person
      // who had just finished cooking. The old page had the same guard for
      // the same reason (its autoFocusedToday flag). A genuine reload — a
      // chat turn, a swap, coming back to the tab — repicks it.
      cookState.tonightIdx = cookTonightIndex(cookState.data.meals || []);
    } catch (err) {
      console.warn('Cooker lookup failed:', err);
      cookState.loadError = true;
    }
    // A "Start cooking"/"Cook this" deep link that arrived before this
    // view had ever loaded (the common case — Cook is lazy-built) asked
    // for one meal in focus, not the overview; honor it now that the data
    // (and, for the legacy `true` case, tonight's index) is actually known.
    if (cookState.pendingFocusTarget) {
      var target = cookState.pendingFocusTarget;
      cookState.pendingFocusTarget = false;
      if (!cookState.loadError) {
        var idx = cookResolveFocusIndex(cookState.data.meals || [], target);
        if (idx !== null && cookState.data.meals[idx]) {
          cookEnterFocus(idx);
          return;
        }
      }
    }
    renderCook();
  }

  // Called from the refresh paths. A Cook view that was opened early has to
  // stay correct, not stay frozen — the same rule the Grocery panel follows.
  function refreshCookView() {
    var panel = cookPanel();
    var view = panel && panel.querySelector('#week-cook-view');
    if (view && view.dataset.built) loadCook();
  }

  // Re-render from a response the server already handed back, instead of
  // re-fetching. Every /api/cooker/* write returns the whole refreshed view,
  // which is why checking a box has never needed a round trip of its own.
  function renderCookFrom(view) {
    cookState.data = view;
    cookState.loadError = false;
    renderCook();
  }

  function cookSlotRank(m) {
    var i = COOK_SLOT_ORDER.indexOf(m.slot || '');
    return i === -1 ? 99 : i;
  }

  function cookCurrentSlotIndex(hour) {
    if (hour < 11) return 0;   // breakfast
    if (hour < 16) return 1;   // lunch
    return 2;                  // dinner
  }

  function cookTonightIndex(meals, nowHour) {
    var iso = todayLocalStr();
    var hour = typeof nowHour === 'number' ? nowHour : new Date().getHours();
    var from = cookCurrentSlotIndex(hour);
    var todays = (meals || [])
      .map(function (m, i) { return { m: m, i: i }; })
      .filter(function (x) { return x.m.date === iso; })
      .sort(function (a, b) { return cookSlotRank(a.m) - cookSlotRank(b.m); });
    if (!todays.length) return null;
    var fromNow = todays.filter(function (x) { return cookSlotRank(x.m) >= from; });
    var uncooked = fromNow.filter(function (x) { return x.m.cooked_status !== 'done'; });
    if (uncooked.length) return uncooked[0].i;
    if (fromNow.length) return fromNow[0].i;
    return todays[todays.length - 1].i;
  }

  function cookDateLabel(dateStr) {
    if (!dateStr) return '';
    var d = new Date(dateStr + 'T00:00:00');
    if (isNaN(d)) return dateStr;
    return d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
  }

  function renderCook() {
    var panel = cookPanel();
    var view = panel && panel.querySelector('#week-cook-view');
    if (!view) return;

    // The Plan/Cook segmented control is Meals' own top-level state, not
    // part of what's "on screen" once a meal takes it over — the same rule
    // Grocery's shopping mode follows for its own To buy/Plan stops/Review
    // control (renderGrocery: "while it is on, the control goes away rather
    // than lying about where you are").
    var mealsSeg = panel && panel.querySelector('#meals-seg');
    if (mealsSeg) mealsSeg.hidden = cookState.screen !== 'overview';

    // Hold the scroll across a re-render — the same rule the Grocery panel
    // follows, and it matters more here: a re-render happens every time a
    // box is ticked, and a cook is mid-recipe when they tick one.
    var keepScroll = scrollEl ? scrollEl.scrollTop : 0;

    // Both of these replace the whole view, and both have to put the scroll
    // back like every other path here — a chat turn that fails to reload can
    // otherwise throw a reader who was halfway down the week to the top.
    if (cookState.loadError || !cookState.data) {
      view.innerHTML = '<p class="cook-error">Couldn’t load the cook view right now — switch tabs and back to try again.' + snwLink() + '</p>';
      if (scrollEl) scrollEl.scrollTop = keepScroll;
      return;
    }
    var data = cookState.data;
    // Every fresh view — a load, or the one a write hands back — is the
    // truth about which days are ticked, so the cook-ahead chips go back
    // to reading it rather than to whatever was tapped before it arrived.
    if (cookState.cookAheadFrom !== data) {
      cookState.cookAheadFrom = data;
      cookState.cookAheadPicks = {};
    }
    if (!data.weekly_plan_id) {
      view.innerHTML = '<p class="cook-empty">No plan yet this week &mdash; ' +
        '<button type="button" class="cook-empty-link" data-cook="goto-plan">plan one on the Plan tab first</button>.</p>';
      if (scrollEl) scrollEl.scrollTop = keepScroll;
      return;
    }

    var meals = data.meals || [];
    // Pinned by loadCook; only worked out here if a write response arrived
    // before any load ever did, or if the pinned index no longer exists.
    if (cookState.tonightIdx === null || cookState.tonightIdx === undefined ||
        !meals[cookState.tonightIdx]) {
      cookState.tonightIdx = cookTonightIndex(meals);
    }

    // A focused meal that vanished from underneath it (the plan changed,
    // it was swapped out) has nothing left to show — fall back to the
    // overview rather than rendering a focus screen for a meal that no
    // longer exists.
    if (cookState.screen === 'focus' && !meals[cookState.focusIdx]) {
      cookState.screen = 'overview';
    }
    // ...and the same guard for a prep session that stopped existing
    // (prep days corrected, the week skipped, the last item cleared).
    if (cookState.screen === 'session' && !cookSessionOn(data, cookState.sessionDate)) {
      cookState.screen = 'overview';
    }

    if (cookState.screen === 'session') {
      view.innerHTML = cookSessionHtml(data, cookSessionOn(data, cookState.sessionDate), meals);
    } else if (cookState.screen === 'focus') {
      view.innerHTML = cookFocusHtml(data, meals, cookState.focusIdx);
      wireCookFocusScroll(view);
    } else {
      // The hero leads — one hero per screen, same rule Today follows (its
      // needs-you band sits below #today-next-up, never above).
      // Attention items come after it, folded by default per the
      // inventory-is-quiet policy: a count you can open, not a wall of
      // questions on the way in.
      view.innerHTML =
        cookTitleRowHtml(data) +
        (COOK_VOICE_ENABLED ? '<div class="cook-voice" id="cook-voice" hidden></div>' : '') +
        cookHeroHtml(meals[cookState.tonightIdx], cookState.tonightIdx) +
        cookAttentionHtml() +
        cookDefrostLinkHtml() +
        cookAheadAskLinkHtml() +
        '<div class="cook-body">' +
          // Between "Cooking today" (the hero) and the rest of the week —
          // a prep day is about the days ahead, so it sits above the list
          // of them and below the one thing happening now.
          cookPrepSessionsHtml(data) +
          cookPrepHtml(data, meals[cookState.tonightIdx], cookState.tonightIdx) +
          cookRestOfWeekHtml(meals, data) +
        '</div>';
    }

    updateCookVoiceButtons();
    // Entering/leaving focus is a screen change (Grocery's shopping-mode
    // precedent resets to the top the same way, groSetScreen); every other
    // render — a step checked, a box ticked — keeps the reader's place.
    if (scrollEl) scrollEl.scrollTop = cookState.pendingScrollTop ? 0 : keepScroll;
    cookState.pendingScrollTop = false;
  }

  function cookTitleRowHtml(data) {
    var done = data.meals_done || 0;
    var total = data.meals_total || 0;
    return '<div class="cook-titlerow">' +
      '<h1 class="cook-title">This week</h1>' +
      '<span class="cook-count">' + done + ' of ' + total + ' cooked</span>' +
    '</div>';
  }

  // "for 6" once a night is cooking one batch across more than one dinner
  // (get_cooker_view sets `servings` and `covers_note` together on exactly
  // those nights — see cooker._apply_leftover_chains). Everything else
  // keeps the "Serves 4" it has always shown, read off the recipe.
  function cookServesChip(meal) {
    if (meal.covers_note && meal.servings) return 'for ' + meal.servings;
    return meal.default_servings ? 'Serves ' + meal.default_servings : '';
  }

  // ---------- Cook ahead: one batch, several days of the same dish ----------
  // Emily, 2026-09-07, on a plan with the same breakfast every morning:
  // "We don't want to make egg bites every morning... the user can mark
  // off the days of the week it's on the plan that we should cook the
  // portions for now." get_cooker_view hands each cook card the later
  // days it could cover (cook_ahead.days, each with its own eaters and
  // whether it is already ticked); this is the picker over them.

  // "morning" / "mornings" — the meal of the day this repeat is, said the
  // way a person would. Dinner is a night, because that is what the rest
  // of this screen calls it.
  function cookSlotWord(slot, count) {
    var one = slot === 'breakfast' ? 'morning' : (slot === 'lunch' ? 'lunch' : 'night');
    if (count === 1) return one;
    return one === 'lunch' ? 'lunches' : one + 's';
  }

  // Which days are ticked right now. Seeded from the server's own answer
  // (a card that already cooks ahead comes back with those days selected),
  // then owned by the screen until the next write or load — ticking a chip
  // must not wait for a round trip to show.
  function cookAheadPicks(meal) {
    var picks = cookState.cookAheadPicks[meal.entry_id];
    if (!picks) {
      picks = {};
      ((meal.cook_ahead && meal.cook_ahead.days) || []).forEach(function (d) {
        if (d.selected) picks[d.entry_id] = true;
      });
      cookState.cookAheadPicks[meal.entry_id] = picks;
    }
    return picks;
  }

  function cookAheadHtml(meal) {
    var days = (meal.cook_ahead && meal.cook_ahead.days) || [];
    if (!days.length) return '';
    var picks = cookAheadPicks(meal);
    var ticked = days.filter(function (d) { return !!picks[d.entry_id]; });
    // Only a change asks to be confirmed. Chips that still match what the
    // plan already says are the state, not a decision, so there is nothing
    // to press — the button comes back the moment one is tapped.
    var changed = days.some(function (d) { return !!picks[d.entry_id] !== !!d.selected; });

    // The live arithmetic: this day plus every ticked one, and the people
    // sitting down to all of them. attendance is null only where there is
    // no real day to count (see get_cooker_view), and then the count line
    // simply says how many days, which is still true.
    var count = ticked.length + 1;
    var eaters = meal.attendance ? meal.attendance.headcount : 0;
    if (eaters) {
      ticked.forEach(function (d) { eaters += d.eaters || 0; });
    }
    var summary = 'Makes ' + count + ' ' + cookSlotWord(meal.slot, count) +
      (eaters ? ' · for ' + eaters : '');

    // Unticking everything is a real answer, and it deserves its own
    // words: this is not "cook for these," it is putting each day back to
    // cooking for itself.
    var action = !changed ? '' : (ticked.length ? 'Cook for these' : 'Cook each on its own');

    return '<div class="cook-ahead">' +
      '<p class="cook-ahead-ask">Cooking ahead? Tick the ' +
        cookSlotWord(meal.slot, 2) + ' this batch should cover.</p>' +
      '<div class="cook-ahead-days">' +
        days.map(function (d) {
          var on = !!picks[d.entry_id];
          return '<button type="button" class="cook-ahead-day' + (on ? ' is-on' : '') + '" ' +
            'data-cook="ahead-day" data-source-id="' + meal.entry_id + '" data-day-id="' + d.entry_id + '" ' +
            'aria-pressed="' + on + '">' + escapeHtml(dayNameShort(d.date)) + '</button>';
        }).join('') +
      '</div>' +
      '<div class="cook-ahead-foot">' +
        '<span class="cook-ahead-count">' + escapeHtml(summary) + '</span>' +
        (action
          ? '<button type="button" class="cook-ahead-go" data-cook="ahead-go" ' +
              'data-source-id="' + meal.entry_id + '">' + escapeHtml(action) + '</button>'
          : '') +
      '</div>' +
    '</div>';
  }

  // A reheat night, in place of the cook hero. Emily, 2026-09-04: the dish
  // is cooked on ONE night, and the night that eats the leftovers should
  // not be a second cook. So: no recipe, no steps, no prep, no Bulk badge,
  // no "Start cooking" — the source it came from, an optional line of the
  // recipe's own reheating advice, and one action.
  function cookReheatCardHtml(meal, chipLabel) {
    var isDone = meal.cooked_status === 'done';
    var chips = [];
    if (meal.servings) chips.push('for ' + meal.servings);
    var note = meal.reheat_note || '';
    return '<div class="cook-hero-top">' +
        '<span class="cook-hero-chip">' + escapeHtml(chipLabel) + '</span>' +
        '<span class="cook-hero-rule"></span>' +
      '</div>' +
      '<div class="cook-hero-line">' +
        '<h2 class="cook-hero-headline' + (isDone ? ' is-done' : '') + '">' +
          escapeHtml(meal.leftovers_headline || 'Leftovers') +
        '</h2>' +
        (note ? '<p class="cook-hero-note">' + escapeHtml(note) + '</p>' : '') +
      '</div>' +
      (chips.length
        ? '<div class="cook-hero-chips">' + chips.map(function (c) {
            return '<span class="cook-meta-chip">' + escapeHtml(c) + '</span>';
          }).join('') + '</div>'
        : '') +
      '<div class="cook-hero-actions">' +
        '<button type="button" class="cook-hero-action" ' +
          'data-cook="check-meal" data-entry-id="' + meal.entry_id + '" data-next="' + (isDone ? 'pending' : 'done') + '">' +
          '<span>' + escapeHtml(isDone ? REHEAT_UNDO_LABEL : REHEAT_ACTION_LABEL) + '</span>' + (isDone ? '' : ICONS.arrow) +
        '</button>' +
      '</div>';
  }

  function cookReheatHeroHtml(meal) {
    return '<div class="cook-hero cook-hero-quiet">' + cookReheatCardHtml(meal, 'Tonight') + '</div>';
  }

  // The one hero on this screen, and the one apricot action in it.
  function cookHeroHtml(meal, idx) {
    if (!meal) {
      return '<div class="cook-hero cook-hero-quiet">' +
        '<div class="cook-hero-top">' +
          '<span class="cook-hero-chip cook-chip-quiet">Tonight</span>' +
          '<span class="cook-hero-rule"></span>' +
        '</div>' +
        '<h2 class="cook-hero-headline">Nothing to cook tonight</h2>' +
        '<p class="cook-hero-note">the rest of the week is below</p>' +
      '</div>';
    }
    if (meal.is_leftovers) return cookReheatHeroHtml(meal);
    var isDone = meal.cooked_status === 'done';
    var chips = [];
    if (meal.prep_time_minutes || meal.cook_time_minutes) {
      var bits = [];
      if (meal.prep_time_minutes) bits.push(meal.prep_time_minutes + 'm prep');
      if (meal.cook_time_minutes) bits.push(meal.cook_time_minutes + 'm cook');
      chips.push(bits.join(' + '));
    }
    // "for 6" for a night cooking one batch across more than one dinner
    // (see cookServesChip); "Serves 4" for every ordinary night, unchanged.
    chips.push(cookServesChip(meal));
    if (meal.batch_note) chips.push('Bulk ×' + meal.meal_count);
    // "with a green salad" — the side the app attached to fill out this
    // plate (app/tools/plates.py). Its ingredients and steps are already
    // folded into the recipe below; this is what says so on the hero.
    if (meal.sides_label) chips.push(meal.sides_label);
    chips = chips.filter(Boolean);

    // Newsreader italic, once per screen. The reasoning is the honest thing
    // to say here; the advance-prep note is the more useful one when there
    // is one, because it is what changes what you do next — and the note
    // about a batch covering a later night's leftovers outranks both,
    // because it is the reason this card says 6 and not 3.
    var note = meal.covers_note || meal.advance_prep_notes || meal.reasoning || '';

    return '<div class="cook-hero">' +
      '<div class="cook-hero-top">' +
        '<span class="cook-hero-chip">Tonight</span>' +
        '<span class="cook-hero-rule"></span>' +
        (meal.advance_prep_notes ? '<span class="cook-hero-tag">Advance prep</span>' : '') +
      '</div>' +
      '<div class="cook-hero-line">' +
        '<h2 class="cook-hero-headline' + (isDone ? ' is-done' : '') + '">' + escapeHtml(meal.meal || 'Dinner') + '</h2>' +
        (note ? '<p class="cook-hero-note">' + escapeHtml(note) + '</p>' : '') +
      '</div>' +
      (chips.length
        ? '<div class="cook-hero-chips">' + chips.map(function (c) {
            return '<span class="cook-meta-chip">' + escapeHtml(c) + '</span>';
          }).join('') + '</div>'
        : '') +
      // Beside the "for 6" chip and the note that explains it: the offer to
      // make this one batch cover the other days it is planned for.
      cookAheadHtml(meal) +
      '<div class="cook-hero-actions">' +
        // The apricot action, and the only one on this screen. It no longer
        // expands the recipe in place below the fold — it takes the whole
        // screen over for just this meal (cookEnterFocus), which is the
        // point of the ticket: everything else stops competing for the
        // screen while your hands are busy. The quiet icon beside it opens
        // the same focused screen, scrolled straight to ingredients.
        '<button type="button" class="cook-hero-action" data-cook="focus" data-idx="' + idx + '" data-at="steps">' +
          '<span>Start cooking</span>' + ICONS.arrow +
        '</button>' +
        '<button type="button" class="cook-hero-icon" data-cook="focus" data-idx="' + idx + '" data-at="ingredients" ' +
          'aria-label="Ingredients" title="Ingredients">' + COOK_ICONS.list + '</button>' +
        '<button type="button" class="cook-hero-check' + (isDone ? ' checked' : '') + '" ' +
          'data-cook="check-meal" data-entry-id="' + meal.entry_id + '" data-next="' + (isDone ? 'pending' : 'done') + '" ' +
          'aria-label="' + (isDone ? 'Mark not cooked' : 'Mark cooked') + '" ' +
          'title="' + (isDone ? 'Mark not cooked' : 'Mark cooked') + '">' + COOK_ICONS.check + '</button>' +
      '</div>' +
    '</div>';
  }

  // Re-ask entry point (Loop Board "Defrost check: ask at approval") — a
  // household can always find something extra was frozen after already
  // answering, so this stays available regardless of whether there's a
  // current prep schedule, not folded inside cookPrepHtml's tasks.length
  // guard. Shares .week-reset-link/.week-tweak-link's exact quiet-text-link
  // idiom (Newsreader italic, own 44px tap target) rather than inventing a
  // second visual language for the same kind of action.
  function cookDefrostLinkHtml() {
    return '<button type="button" class="week-reset-link week-tweak-link cook-defrost-link" ' +
      'data-cook="defrost-ask">Something in the freezer?</button>';
  }

  // The same re-ask entry point for the whole-week cook-ahead card (Loop
  // Board "Cook ahead: ask at approval") — a household that answered "cook
  // each on its own" at approval can change its mind mid-week, so this
  // stays available whatever cook_ahead_asked_at says. Same quiet-text-link
  // idiom as the freezer link it sits beside.
  function cookAheadAskLinkHtml() {
    return '<button type="button" class="week-reset-link week-tweak-link cook-ahead-ask-link" ' +
      'data-cook="cook-ahead-ask">Cooking ahead?</button>';
  }

  // ---------- Prep sessions: the work one prep day holds ----------
  // Emily, 2026-09-04 and again 2026-09-08: "I like to do some prep on
  // Sunday to make the week easier, make some things fresh during the
  // week, and then do another prep Wednesday/Thursday depending on the
  // week." get_cooker_view hands the whole thing over on `prep_sessions`
  // (tools/prep_sessions.py): the batch cooks, fridge moves and prep-cuts
  // already on this plan, gathered onto the day they happen on. Nothing
  // here invents work — every item is something the week already asked
  // for, shown on the day the household chose to do it.

  // "about 50 min" / "about an hour" — time the way a person says it
  // (DESIGN_SYSTEM.md §8), never "Est. 50m".
  function cookMinutesLabel(minutes) {
    if (!minutes) return '';
    if (minutes < 45) return 'about ' + minutes + ' min';
    if (minutes < 75) return 'about an hour';
    var hours = Math.round(minutes / 30) / 2;
    return 'about ' + hours + ' hour' + (hours === 1 ? '' : 's');
  }

  // "covers Mon–Wed" — the days this session's work actually feeds, read
  // off what the items cover rather than off a fixed window after the prep
  // day, so it is a claim about this plan and not a guess about a week.
  function cookCoversLabel(dates) {
    var days = (dates || []).slice().sort();
    if (!days.length) return '';
    if (days.length === 1) return 'covers ' + dayNameShort(days[0]);
    return 'covers ' + dayNameShort(days[0]) + '–' + dayNameShort(days[days.length - 1]);
  }

  function cookSessionOn(data, dateStr) {
    var sessions = (data && data.prep_sessions) || [];
    for (var i = 0; i < sessions.length; i++) {
      if (sessions[i].date === dateStr) return sessions[i];
    }
    return null;
  }

  function cookPrepSessionsHtml(data) {
    var sessions = data.prep_sessions || [];
    if (!sessions.length) {
      // A household that told us its prep days and simply has a quiet one
      // gets nothing here — asking again for an answer they already gave
      // is the app not listening. Only a household that has never said
      // gets the offer, and it is one quiet line, not a card.
      if (data.prep_days_set) return '';
      return '<p class="cook-empty">Prep ahead? ' +
        '<button type="button" class="cook-empty-link" data-cook="prep-days">Tell Pomona which days you prep</button>.</p>';
    }
    return '<section class="cook-section">' +
      '<div class="cook-sectionhead">' +
        '<span class="cook-eyebrow">Prep sessions</span>' +
        '<span class="cook-rule"></span>' +
      '</div>' +
      '<div class="cook-week">' +
        sessions.map(function (s) {
          var allDone = s.items_total > 0 && s.items_done === s.items_total;
          var line = [s.weekday + ' prep', cookMinutesLabel(s.total_minutes_estimate), cookCoversLabel(s.covers)]
            .filter(Boolean).join(' · ');
          return '<div class="cook-week-item' + (allDone ? ' is-done' : '') + '">' +
            '<div class="cook-week-row">' +
              '<span class="cook-week-day">' + escapeHtml(dayNameShort(s.date).toUpperCase()) + '</span>' +
              '<button type="button" class="cook-week-name" data-cook="session" data-date="' + escapeHtml(s.date) + '">' +
                escapeHtml(line) +
              '</button>' +
              '<span class="cook-badge' + (allDone ? '' : ' cook-badge-warm') + '">' +
                s.items_done + ' of ' + s.items_total + ' done' +
              '</span>' +
            '</div>' +
          '</div>';
        }).join('') +
      '</div>' +
    '</section>';
  }

  // The session's own screen: the same shape the focused cook screen takes
  // (one hero, a back link, then the list) because it is the same kind of
  // thing — one job on screen while your hands are busy. No apricot
  // primary of its own: the work here IS the ticking, so a second call to
  // action would be competing with the list it sits above (Rule 5).
  function cookSessionHtml(data, session, meals) {
    if (!session) return '';
    var chips = [cookMinutesLabel(session.total_minutes_estimate), cookCoversLabel(session.covers)].filter(Boolean);
    var allDone = session.items_total > 0 && session.items_done === session.items_total;
    var note = allDone
      ? 'That’s the prep done — the week is easier from here.'
      : (session.note || '');
    return '<div class="cook-focus">' +
      '<div class="cook-hero">' +
        '<button type="button" class="cook-focus-back" data-cook="exit-session">&larr; Back to the week</button>' +
        '<div class="cook-hero-top">' +
          '<span class="cook-hero-chip">' + escapeHtml(cookDateLabel(session.date)) + '</span>' +
          '<span class="cook-hero-rule"></span>' +
          '<span class="cook-hero-tag">' + session.items_done + ' of ' + session.items_total + '</span>' +
        '</div>' +
        '<div class="cook-hero-line">' +
          '<h2 class="cook-hero-headline' + (allDone ? ' is-done' : '') + '">' +
            escapeHtml(session.weekday + ' prep') +
          '</h2>' +
          (note ? '<p class="cook-hero-note">' + escapeHtml(note) + '</p>' : '') +
        '</div>' +
        (chips.length
          ? '<div class="cook-hero-chips">' + chips.map(function (c) {
              return '<span class="cook-meta-chip">' + escapeHtml(c) + '</span>';
            }).join('') + '</div>'
          : '') +
      '</div>' +
      '<div class="cook-body">' +
        '<section class="cook-section">' +
          '<div class="cook-sectionhead">' +
            '<span class="cook-eyebrow cook-eyebrow-warm">On the list</span>' +
            '<span class="cook-rule"></span>' +
          '</div>' +
          '<div class="cook-week">' +
            session.items.map(function (item) {
              return cookSessionItemHtml(item, meals);
            }).join('') +
          '</div>' +
        '</section>' +
      '</div>' +
    '</div>';
  }

  function cookSessionItemHtml(item, meals) {
    var isDone = !!item.done;
    // A batch cook is checked off by cooking it, so its box opens the
    // recipe rather than being a second place cooked_status can be set —
    // two boxes for one fact is how they drift apart. A fridge move or a
    // prep-cut is a real prep_tasks row and ticks in place.
    var isCook = item.kind === 'cook_ahead';
    var idx = isCook ? cookResolveFocusIndex(meals || [], { entryId: item.entry_id }) : null;
    var canOpen = isCook && idx !== null;
    var label = isCook
      ? (isDone ? 'Cooked' : 'Cook it')
      : (isDone ? 'Mark not done' : 'Mark done');
    var box = canOpen
      ? '<button type="button" class="cook-box' + (isDone ? ' checked' : '') + '" ' +
          'data-cook="focus" data-idx="' + idx + '" data-at="steps" ' +
          'aria-label="' + escapeHtml(label) + '">' + COOK_ICONS.check + '</button>'
      : (item.prep_task_id != null
        ? '<button type="button" class="cook-box' + (isDone ? ' checked' : '') + '" ' +
            'data-cook="check-prep" data-prep-id="' + item.prep_task_id + '" ' +
            'data-next="' + (isDone ? 'pending' : 'done') + '" ' +
            'aria-label="' + escapeHtml(label) + '">' + COOK_ICONS.check + '</button>'
        : '<span class="cook-box' + (isDone ? ' checked' : '') + '">' + COOK_ICONS.check + '</span>');
    var name = canOpen
      ? '<button type="button" class="cook-week-name" data-cook="focus" data-idx="' + idx + '" data-at="steps">' +
          escapeHtml(item.title) + '</button>'
      : '<span class="cook-week-name">' + escapeHtml(item.title) + '</span>';
    return '<div class="cook-week-item' + (isDone ? ' is-done' : '') + '">' +
      '<div class="cook-week-row">' +
        box +
        name +
        (item.feeds && item.feeds !== item.title
          ? '<span class="cook-badge">' + escapeHtml(item.feeds) + '</span>'
          : '') +
      '</div>' +
    '</div>';
  }

  // ---------- "Prep-cut on Sunday?" ----------
  // The one thing a prep day holds that nothing else produced: the raw
  // components — the salad ingredients for the bowls. Deliberately
  // household-driven and deterministic (no model guessing what "raw" is):
  // it offers this meal's produce ingredients, and the household ticks
  // what they actually want to cut. Lives on the focused cook screen
  // because that is where the ingredients are read off, and because the
  // overview stays quiet.
  function cookPrepCutOptions(data, meal) {
    if (!meal || meal.is_leftovers) return [];
    var sessions = (data && data.prep_sessions) || [];
    // Only a prep day that comes before this meal can prep for it.
    var before = sessions.filter(function (s) { return !meal.date || s.date <= meal.date; });
    if (!before.length) return [];
    var entryIds = meal.entry_ids || [meal.entry_id];
    var already = {};
    ((data && data.prep_tasks) || []).forEach(function (t) {
      if (t.task_type === 'prep_cut' && entryIds.indexOf(t.meal_plan_entry_id) !== -1) {
        already[(t.description || '').trim().toLowerCase()] = true;
      }
    });
    // Produce only, read off the ingredient's own category (the same
    // field plan_quality's fresh-ingredient rule reads). A recipe whose
    // ingredients carry no category offers nothing rather than having the
    // screen guess which of them are raw — an honest empty, not a
    // degraded one.
    return (meal.ingredients || [])
      .filter(function (i) { return (i.category || '').trim().toLowerCase() === 'produce' && (i.item || '').trim(); })
      .map(function (i) { return { item: (i.item || '').trim(), description: cookPrepCutDescription(i.item) }; })
      .filter(function (o) { return !already[o.description.toLowerCase()]; });
  }

  // The words the task is stored and read back in — "Cut up romaine", the
  // way a person would say it, not "prep_cut: romaine".
  function cookPrepCutDescription(item) {
    return 'Cut up ' + String(item || '').trim();
  }

  function cookPrepCutPicks(meal) {
    var picks = cookState.prepCutPicks[meal.entry_id];
    if (!picks) picks = cookState.prepCutPicks[meal.entry_id] = {};
    return picks;
  }

  function cookPrepCutHtml(data, meal) {
    var options = cookPrepCutOptions(data, meal);
    if (!options.length) return '';
    var sessions = (data.prep_sessions || []).filter(function (s) { return !meal.date || s.date <= meal.date; });
    // The nearest prep day before the meal — the one a household would
    // actually mean by "prep it ahead".
    var session = sessions[sessions.length - 1];
    var picks = cookPrepCutPicks(meal);
    var ticked = options.filter(function (o) { return !!picks[o.item]; });
    return '<div class="cook-ahead">' +
      '<p class="cook-ahead-ask">Prep-cut on ' + escapeHtml(session.weekday) + '? Tick what you’d rather cut then.</p>' +
      '<div class="cook-ahead-days">' +
        options.map(function (o) {
          var on = !!picks[o.item];
          return '<button type="button" class="cook-ahead-day' + (on ? ' is-on' : '') + '" ' +
            'data-cook="prep-cut-pick" data-entry-id="' + meal.entry_id + '" ' +
            'data-item="' + escapeHtml(o.item) + '" aria-pressed="' + on + '">' +
            escapeHtml(o.item) + '</button>';
        }).join('') +
      '</div>' +
      (ticked.length
        ? '<div class="cook-ahead-foot">' +
            '<span class="cook-ahead-count">' + ticked.length + ' to cut on ' + escapeHtml(session.weekday) + '</span>' +
            '<button type="button" class="cook-ahead-go" data-cook="prep-cut-go" ' +
              'data-entry-id="' + meal.entry_id + '" data-date="' + escapeHtml(session.date) + '">Add to that day</button>' +
          '</div>'
        : '') +
    '</div>';
  }

  // The supporting rail: the prep that feeds tonight. Two-up, so it reads as
  // a pair of small things rather than another stack of full-width cards.
  function cookPrepHtml(data, tonightMeal, tonightIdx) {
    // prep_cut rows belong to their prep session (cookPrepSessionsHtml)
    // and are left out here — the same reminder in two cards on one screen
    // is what the Today prep tile's own defrost exclusion exists to stop.
    // The counts are recomputed from what is actually shown rather than
    // read off prep_done/prep_total, which count every row.
    var tasks = (data.prep_tasks || []).filter(function (t) { return t.task_type !== 'prep_cut'; });
    if (!tasks.length) return '';
    var done = tasks.filter(function (t) { return t.status === 'done'; }).length;
    var total = tasks.length;
    var allDone = total > 0 && done === total;
    // Once every prep task is off the list there's nothing left to check
    // here — the note stops counting and points at what's next instead.
    // The hero above already carries a "Start cooking"/"Mark eaten" apricot
    // primary whenever tonight has a real meal (cookHeroHtml/
    // cookReheatHeroHtml), and Rule 5 (one apricot primary per screen)
    // means this section must never add a second one on top of it — the
    // fallback link below only appears on the (currently unreachable, but
    // still correct to guard) case where tonight has no meal at all and so
    // the hero offers no way in.
    var heroHasPrimaryAction = !!tonightMeal;
    return '<section class="cook-section">' +
      '<div class="cook-sectionhead">' +
        '<span class="cook-eyebrow cook-eyebrow-warm">Prep schedule</span>' +
        '<span class="cook-rule"></span>' +
        '<span class="cook-sectionnote">' + (allDone ? 'Prep’s done — the rest is tonight.' : (done + ' of ' + total + ' done')) + '</span>' +
        (COOK_VOICE_ENABLED
          ? '<button type="button" class="cook-mic" data-cook="voice" data-ctx="prep" ' +
              'aria-label="Hands-free: check off prep steps by voice" ' +
              'title="Hands-free: check off prep steps by voice">' + COOK_ICONS.mic + '</button>'
          : '') +
      '</div>' +
      (allDone && !heroHasPrimaryAction && tonightIdx !== null && tonightIdx !== undefined
        ? '<button type="button" class="cook-hero-action cook-prep-startcooking" data-cook="focus" data-idx="' + tonightIdx + '" data-at="steps">' +
            '<span>Start cooking</span>' + ICONS.arrow +
          '</button>'
        : '') +
      '<div class="cook-prep-grid">' +
        tasks.map(function (t) {
          var isDone = t.status === 'done';
          return '<div class="cook-prep-card' + (isDone ? ' is-done' : '') + '">' +
            '<button type="button" class="cook-box' + (isDone ? ' checked' : '') + '" ' +
              'data-cook="check-prep" data-prep-id="' + t.id + '" data-next="' + (isDone ? 'pending' : 'done') + '" ' +
              'aria-label="' + (isDone ? 'Mark not done' : 'Mark done') + '">' + COOK_ICONS.check + '</button>' +
            '<span class="cook-prep-date">' + escapeHtml(cookDateLabel(t.task_date)) + '</span>' +
            '<span class="cook-prep-text">' + escapeHtml(t.description) +
              (t.related_meal ? ' <span class="cook-prep-meal">(' + escapeHtml(t.related_meal) + ')</span>' : '') +
            '</span>' +
          '</div>';
        }).join('') +
      '</div>' +
    '</section>';
  }

  // Everything that is not tonight, subordinate: one dense row each. Tapping
  // a name enters the same focused screen tonight's hero does — a "quiet
  // scannable week list" per the ticket, not another accordion of recipes.
  function cookRestOfWeekHtml(meals, data) {
    var rest = meals
      .map(function (m, i) { return { m: m, i: i }; })
      .filter(function (x) { return x.i !== cookState.tonightIdx; });
    if (!rest.length) {
      if (meals.length) return '';
      // Every dinner this period was deliberately marked away (cooker.py's
      // all_away flag) — say that, rather than the generic "nothing
      // planned" line, which would read as though the week was simply
      // forgotten.
      return data && data.all_away
        ? '<p class="cook-empty">Nothing to cook this week — you’re away.</p>'
        : '<p class="cook-empty">No meals on this plan yet.</p>';
    }
    return '<section class="cook-section">' +
      '<div class="cook-sectionhead">' +
        '<span class="cook-eyebrow">The rest of the week</span>' +
        '<span class="cook-rule"></span>' +
      '</div>' +
      '<div class="cook-week">' +
        rest.map(function (x) {
          var m = x.m, idx = x.i;
          var isDone = m.cooked_status === 'done';
          var dayLabel = m.component_category
            ? m.component_category
            : (m.date ? dayName(m.date, { weekday: 'short' }).slice(0, 3).toUpperCase() : '');
          // A reheat night is a row, not a way into a recipe: its name is
          // plain text rather than a button into the focused cook screen,
          // and its box says eaten rather than cooked. It keeps the
          // "Leftovers — Tuesday's Bulgogi" wording the reheat card uses,
          // so the same night reads the same way wherever you meet it.
          var isReheat = !!m.is_leftovers;
          var rowLabel = isReheat
            ? (m.leftovers_headline || 'Leftovers')
            : (m.meal || '');
          var checkLabel = isReheat
            ? (isDone ? REHEAT_UNDO_LABEL : REHEAT_ACTION_LABEL)
            : (isDone ? 'Mark not cooked' : 'Mark cooked');
          return '<div class="cook-week-item' + (isDone ? ' is-done' : '') + '">' +
            '<div class="cook-week-row">' +
              '<button type="button" class="cook-box' + (isDone ? ' checked' : '') + '" ' +
                'data-cook="check-meal" data-entry-id="' + m.entry_id + '" data-next="' + (isDone ? 'pending' : 'done') + '" ' +
                'aria-label="' + escapeHtml(checkLabel) + '">' + COOK_ICONS.check + '</button>' +
              '<span class="cook-week-day">' + escapeHtml(dayLabel) + '</span>' +
              (isReheat
                ? '<span class="cook-week-name">' + escapeHtml(rowLabel) + '</span>'
                : '<button type="button" class="cook-week-name" data-cook="focus" data-idx="' + idx + '" data-at="steps">' +
                    escapeHtml(rowLabel) +
                  '</button>') +
              (isReheat ? '<span class="cook-badge">Reheat</span>' : '') +
              (!isReheat && m.advance_prep_notes ? '<span class="cook-badge cook-badge-warm">Prep ahead</span>' : '') +
              (!isReheat && m.batch_note ? '<span class="cook-badge">Bulk ×' + m.meal_count + '</span>' : '') +
            '</div>' +
          '</div>';
        }).join('') +
      '</div>' +
    '</section>';
  }

  // One recipe panel, used by both the hero and a week row.
  function cookDetailHtml(m, idx, onSpruce) {
    var cls = onSpruce ? ' on-spruce' : '';
    if (!m.has_full_recipe) {
      return '<p class="cook-norecipe' + cls + '">Freeform meal — no saved recipe detail. Ask in the ask bar for the full recipe.</p>';
    }
    var ingredients = (m.ingredients || []).map(function (i) {
      return '<li>' + escapeHtml((i.qty ? i.qty + ' ' : '') + (i.item || '')) + '</li>';
    }).join('') || '<li class="cook-dim">None listed</li>';

    return '<div class="cook-detail' + cls + '">' +
      '<div class="cook-detail-tools">' +
        (m.default_servings
          ? '<div class="cook-serves" data-idx="' + idx + '" data-recipe="' + escapeHtml(m.meal || '') + '" data-base="' + m.default_servings + '">' +
              '<span class="cook-serves-label">Serves</span>' +
              '<button type="button" class="cook-serves-btn" data-cook="serves" data-idx="' + idx + '" data-delta="-1" aria-label="Fewer servings">&minus;</button>' +
              '<span class="cook-serves-count" id="cook-serves-' + idx + '">' + m.default_servings + '</span>' +
              '<button type="button" class="cook-serves-btn" data-cook="serves" data-idx="' + idx + '" data-delta="1" aria-label="More servings">+</button>' +
            '</div>'
          : '') +
        (COOK_VOICE_ENABLED
          ? '<button type="button" class="cook-mic" data-cook="voice" data-ctx="meal" data-idx="' + idx + '" ' +
              'aria-label="Hands-free for this recipe" ' +
              'title="Hands-free: read steps, ask amounts, log a substitution">' + COOK_ICONS.mic + '</button>'
          : '') +
      '</div>' +
      (m.advance_prep_notes
        ? '<h4 class="cook-detail-head">Advance prep</h4><p class="cook-detail-p">' + escapeHtml(m.advance_prep_notes) + '</p>'
        : '') +
      '<h4 class="cook-detail-head">Ingredients</h4>' +
      '<ul class="cook-ings" id="cook-ings-' + idx + '">' + ingredients + '</ul>' +
      '<p class="cook-unscaled" id="cook-unscaled-' + idx + '" hidden></p>' +
      '<h4 class="cook-detail-head">Instructions</h4>' +
      cookInstructionsHtml(m, idx) +
      // The end of the last step used to just stop — the only way back to
      // "Mark cooked" was scrolling all the way back up to the hero. A
      // small, quiet row right where the steps run out closes the loop:
      // the same handler as the hero's own "Mark cooked" button (so this
      // is never a second source of truth for that write), plus a plain
      // way back. Only while there's really a recipe with steps to finish,
      // and only until it's actually marked cooked — once it's done, this
      // is just clutter under a screen that already says so.
      ((m.instructions || []).length && m.cooked_status !== 'done'
        ? cookFocusEndHtml(m)
        : '') +
      (m.reasoning
        ? '<button type="button" class="cook-why" data-cook="why" data-idx="' + idx + '">Why this?</button>' +
          '<p class="cook-why-text" id="cook-why-' + idx + '" hidden>' + escapeHtml(m.reasoning) + '</p>'
        : '') +
    '</div>';
  }

  function cookFocusEndHtml(m) {
    return '<div class="cook-focus-end">' +
      '<p class="cook-focus-end-note">That’s everything — how did it go?</p>' +
      '<div class="cook-focus-end-actions">' +
        '<button type="button" class="cook-focus-end-done" data-cook="focus-check" data-entry-id="' + m.entry_id + '" data-next="done">Mark it cooked</button>' +
        '<button type="button" class="cook-focus-end-back" data-cook="exit-focus">Back to the week</button>' +
      '</div>' +
    '</div>';
  }

  // advance_prep_step_indices are 1-based positions within `instructions`
  // that are the make-ahead steps. When a recipe tags them, the steps split
  // into "Do ahead" and "Day of" with their own numbering, so it is clear
  // what to do the night before and what happens later using it. Most
  // recipes tag nothing and get one flat list.
  //
  // Each step is tap-to-check (cookState.focusStepsChecked, client-side
  // only — there is no server column for "which recipe steps has this cook
  // done," and there doesn't need to be one: it's a during-the-cook memory
  // aid, not a record anyone needs later, so it resets with the page like
  // the rest of this in-memory screen state). Keyed by meal idx + the
  // step's position in the FULL instructions array, not the Do
  // ahead/Day of sub-list's own numbering, so a check survives whichever
  // list it's currently rendered into.
  function cookStepLi(step, idx, stepPos) {
    var key = idx + ':' + stepPos;
    var done = !!cookState.focusStepsChecked[key];
    // The number stays a real <ol> marker — a step someone might reference
    // ("step 3") should look like one — so the checkbox+text flex row lives
    // INSIDE the <li> rather than on it; display:flex directly on an <li>
    // silently drops its own marker in every browser that matters here.
    return '<li class="cook-step-item' + (done ? ' is-done' : '') + '">' +
      '<span class="cook-step-row">' +
        '<button type="button" class="cook-box cook-step-check' + (done ? ' checked' : '') + '" ' +
          'data-cook="check-step" data-idx="' + idx + '" data-step="' + stepPos + '" ' +
          'aria-label="' + (done ? 'Mark step not done' : 'Mark step done') + '">' + COOK_ICONS.check + '</button>' +
        '<span class="cook-step-text">' + escapeHtml(step) + '</span>' +
      '</span>' +
    '</li>';
  }

  function cookInstructionsHtml(m, idx) {
    var steps = m.instructions || [];
    if (!steps.length) {
      return '<p class="cook-dim">No steps saved yet.</p>' +
        '<button type="button" class="cook-fill" data-cook="fill" data-recipe="' + escapeHtml(m.meal || '') + '">Fill in this recipe</button>';
    }
    var prepIdx = m.advance_prep_step_indices || [];
    if (!prepIdx.length) {
      return '<ol class="cook-steps cook-steps-check">' +
        steps.map(function (s, i) { return cookStepLi(s, idx, i); }).join('') +
      '</ol>';
    }
    var doAhead = [], dayOf = [];
    steps.forEach(function (s, i) { (prepIdx.indexOf(i + 1) !== -1 ? doAhead : dayOf).push({ s: s, i: i }); });
    return '<h5 class="cook-steplabel cook-steplabel-warm">Do ahead</h5>' +
      '<ol class="cook-steps cook-steps-check">' + doAhead.map(function (x) { return cookStepLi(x.s, idx, x.i); }).join('') + '</ol>' +
      '<h5 class="cook-steplabel">Day of</h5>' +
      '<ol class="cook-steps cook-steps-check">' + dayOf.map(function (x) { return cookStepLi(x.s, idx, x.i); }).join('') + '</ol>';
  }

  // ---------- Cook: focused single-meal mode ----------
  // Entering "Start cooking" (the hero, a week row, or a Today/Meals deep
  // link) takes the whole Cook screen over for one meal — the ticket's
  // whole point: "no stack of twenty other cards underneath, no scroll
  // position to lose". Leaving is the quiet "← Back to the week" text
  // control on the focused hero, the same shape Grocery's shopping mode
  // uses to step back out of a store into Plan your stops (groShopHeroHtml/
  // .gro-hero-back) — a state of this screen, never a page with its own
  // header or back button.
  function cookEnterFocus(idx) {
    if (!cookState.data || !(cookState.data.meals || [])[idx]) return;
    cookState.screen = 'focus';
    cookState.focusIdx = idx;
    cookState.focusScrollTo = null;
    cookState.pendingScrollTop = true;
    renderCook();
  }

  function cookExitFocus() {
    cookState.screen = 'overview';
    cookState.pendingScrollTop = true;
    renderCook();
  }

  // A prep session, by its date — see cookState.sessionDate for why the
  // date is the handle and not an index.
  function cookEnterSession(dateStr) {
    if (!cookSessionOn(cookState.data, dateStr)) return;
    cookState.screen = 'session';
    cookState.sessionDate = dateStr;
    cookState.pendingScrollTop = true;
    renderCook();
  }

  // "Tell Pomona which days you prep" — the standing answer lives on What
  // we know's Rhythm tab, and that is a sheet over whatever tab you're on
  // (§6: everything that isn't one of the four screens is a state, a sheet
  // or a step), so Cook opens it in place rather than navigating away from
  // a half-cooked week.
  function openRhythmFromCook() {
    // 'tab/anchor': What we know opens Rhythm and scrolls to the prep-days block.
    openKitchenSheet('memory', 'rhythm/prep-days');
  }

  // Hand this meal's ticked raw components to the prep day. One call per
  // component (the route takes one description and the meals it feeds);
  // the last response is the refreshed view every /api/cooker/* write
  // returns, so the session's count updates without a reload.
  async function cookAddPrepCuts(el) {
    var entryId = parseInt(el.getAttribute('data-entry-id'), 10);
    var prepDate = el.getAttribute('data-date');
    var picks = cookState.prepCutPicks[entryId] || {};
    var items = Object.keys(picks).filter(function (k) { return picks[k]; });
    if (!items.length) return;
    el.disabled = true;
    try {
      var view = null;
      for (var i = 0; i < items.length; i++) {
        view = await cookPost('/api/prep-cut', {
          prep_date: prepDate,
          description: cookPrepCutDescription(items[i]),
          entry_ids: [entryId],
          weekly_plan_id: cookState.data ? cookState.data.weekly_plan_id : null
        });
      }
      cookState.prepCutPicks[entryId] = {};
      if (view) renderCookFrom(view);
      showToast('Added to your ' + dayName(prepDate, { weekday: 'long' }) + ' prep.');
    } catch (err) {
      el.disabled = false;
      showToast('That didn’t save — try again.');
    }
  }

  // Prep for just this meal: a defrost task carries the entry it feeds
  // (meal_plan_entry_id, set by defrost.sync_defrost_tasks — see
  // get_prep_schedule), which also covers a merged bulk-cook card via its
  // entry_ids. A general (LLM-written) prep task has no entry link, only
  // the plain-text related_meal name save_prep_tasks stores, so that's the
  // fallback match.
  function cookFocusPrepTasks(data, meal) {
    // Nothing is cooked on a reheat night, so nothing is prepped for one.
    // The backend already declines to write those tasks (defrost.py,
    // agent.generate_prep_schedule); this stops an older 'general' task
    // still carrying the source meal's NAME from matching the reheat
    // night by the related_meal fallback below.
    if (meal.is_leftovers) return [];
    var all = (data && data.prep_tasks) || [];
    var entryIds = meal.entry_ids || [meal.entry_id];
    var mealName = (meal.meal || '').trim().toLowerCase();
    return all.filter(function (t) {
      if (t.meal_plan_entry_id != null) return entryIds.indexOf(t.meal_plan_entry_id) !== -1;
      return !!mealName && (t.related_meal || '').trim().toLowerCase() === mealName;
    });
  }

  // Same card shell as the week's shared prep rail (cookPrepHtml) — one
  // section, scoped down to this meal's own rows, defrost tiles included.
  function cookFocusPrepHtml(tasks) {
    if (!tasks.length) return '';
    return '<section class="cook-section">' +
      '<div class="cook-sectionhead">' +
        '<span class="cook-eyebrow cook-eyebrow-warm">For this meal</span>' +
        '<span class="cook-rule"></span>' +
      '</div>' +
      '<div class="cook-prep-grid">' +
        tasks.map(function (t) {
          var isDone = t.status === 'done';
          return '<div class="cook-prep-card' + (isDone ? ' is-done' : '') + '">' +
            '<button type="button" class="cook-box' + (isDone ? ' checked' : '') + '" ' +
              'data-cook="check-prep" data-prep-id="' + t.id + '" data-next="' + (isDone ? 'pending' : 'done') + '" ' +
              'aria-label="' + (isDone ? 'Mark not done' : 'Mark done') + '">' + COOK_ICONS.check + '</button>' +
            '<span class="cook-prep-date">' + escapeHtml(cookDateLabel(t.task_date)) + '</span>' +
            '<span class="cook-prep-text">' + escapeHtml(t.description) + '</span>' +
          '</div>';
        }).join('') +
      '</div>' +
    '</section>';
  }

  // "for 2 + 1 guest" — headcount plus who's extra, since portions matter
  // mid-cook. attendance is null for a component-based meal's placeholder
  // date (see get_cooker_view) — no real day to answer "who's home" about.
  function cookAttendanceChip(meal) {
    // A batch night already answers "for how many" with the number it is
    // actually cooking (its own table plus the leftover nights') — showing
    // tonight's three beside a six-serving recipe would just be two
    // different answers to the same question.
    if (meal.covers_note && meal.servings) return null;
    var att = meal.attendance;
    if (!att) return null;
    var label = 'for ' + att.present_count;
    if (att.guest_count) {
      label += ' + ' + att.guest_count + ' guest' + (att.guest_count !== 1 ? 's' : '');
    }
    return label;
  }

  // The focused screen for a reheat night — reachable from Today's hero,
  // which deep-links straight into focus for tonight. Deliberately the
  // same compact card the overview hero shows rather than a full-screen
  // recipe with the recipe taken out of it: there is no cook here, so
  // there is nothing for a cook screen to hold.
  function cookReheatFocusHtml(meal) {
    var src = meal.leftovers_from || {};
    var dayLabel = meal.date ? cookDateLabel(meal.date) : 'Leftovers';
    var srcLine = src.date ? 'Cooked on ' + cookDateLabel(src.date) + '.' : '';
    return '<div class="cook-focus">' +
      '<div class="cook-hero cook-hero-quiet">' +
        '<button type="button" class="cook-focus-back" data-cook="exit-focus">&larr; Back to the week</button>' +
        cookReheatCardHtml(meal, dayLabel) +
      '</div>' +
      (srcLine
        ? '<div class="cook-body"><div class="card cook-focus-recipe">' +
            '<p class="cook-detail-p">' + escapeHtml(srcLine) + '</p>' +
          '</div></div>'
        : '') +
    '</div>';
  }

  function cookFocusHtml(data, meals, idx) {
    var meal = meals[idx];
    if (meal.is_leftovers) return cookReheatFocusHtml(meal);
    var isDone = meal.cooked_status === 'done';
    // A component-based plan's date is a placeholder (week_start, per
    // get_weekly_plan) — showing it as a real day would be a lie about
    // when this is for, same reason cookRestOfWeekHtml prefers the
    // category label over the date for these entries.
    var dayLabel = meal.component_category || (meal.date ? cookDateLabel(meal.date) : 'Cooking');

    var chips = [];
    if (meal.prep_time_minutes || meal.cook_time_minutes) {
      var bits = [];
      if (meal.prep_time_minutes) bits.push(meal.prep_time_minutes + 'm prep');
      if (meal.cook_time_minutes) bits.push(meal.cook_time_minutes + 'm cook');
      chips.push(bits.join(' + '));
    }
    if (meal.batch_note) chips.push('Bulk ×' + meal.meal_count);
    // Same "for 6" the overview hero shows on a batch night — the focused
    // screen is where the ingredients are actually read off, so it is the
    // one place the number really has to be right in front of them.
    if (meal.covers_note && meal.servings) chips.push('for ' + meal.servings);
    // The side that fills out this plate, named here too — this is the
    // screen someone actually cooks from, and the "Alongside" steps at the
    // bottom of the list want explaining before they're reached.
    if (meal.sides_label) chips.push(meal.sides_label);
    var attChip = cookAttendanceChip(meal);
    if (attChip) chips.push(attChip);

    // Newsreader italic, once per screen — same rule as the overview hero,
    // and this replaces it (they never show at once). Same precedence too:
    // the batch note first, because it explains the quantities below it.
    var note = meal.covers_note || meal.advance_prep_notes || meal.reasoning || '';
    var prepTasks = cookFocusPrepTasks(data, meal);

    return '<div class="cook-focus">' +
      '<div class="cook-hero">' +
        '<button type="button" class="cook-focus-back" data-cook="exit-focus">&larr; Back to the week</button>' +
        '<div class="cook-hero-top">' +
          '<span class="cook-hero-chip">' + escapeHtml(dayLabel) + '</span>' +
          '<span class="cook-hero-rule"></span>' +
          (meal.advance_prep_notes ? '<span class="cook-hero-tag">Advance prep</span>' : '') +
        '</div>' +
        '<div class="cook-hero-line">' +
          '<h2 class="cook-hero-headline' + (isDone ? ' is-done' : '') + '">' + escapeHtml(meal.meal || 'Dinner') + '</h2>' +
          (note ? '<p class="cook-hero-note">' + escapeHtml(note) + '</p>' : '') +
        '</div>' +
        (chips.length
          ? '<div class="cook-hero-chips">' + chips.map(function (c) {
              return '<span class="cook-meta-chip">' + escapeHtml(c) + '</span>';
            }).join('') + '</div>'
          : '') +
        // Same picker as the overview hero — the focused screen is where
        // the ingredients are actually read off, so it is where changing
        // the batch has to be possible too.
        cookAheadHtml(meal) +
        // ...and, for the same reason, where the raw components get
        // handed to a prep day. Only here, not on the overview hero: the
        // overview is the "what am I making now" screen and this is a
        // question about a different day.
        cookPrepCutHtml(data, meal) +
        '<button type="button" class="cook-hero-action cook-focus-check' + (isDone ? ' is-done' : '') + '" ' +
          'data-cook="focus-check" data-entry-id="' + meal.entry_id + '" data-next="' + (isDone ? 'pending' : 'done') + '">' +
          '<span>' + (isDone ? 'Mark not cooked' : 'Mark cooked') + '</span>' + (isDone ? '' : ICONS.arrow) +
        '</button>' +
      '</div>' +
      '<div class="cook-body">' +
        cookFocusPrepHtml(prepTasks) +
        '<div class="card cook-focus-recipe">' + cookDetailHtml(meal, idx, false) + '</div>' +
      '</div>' +
    '</div>';
  }

  function wireCookFocusScroll(view) {
    // The recipe panel is rendered by the same pass as everything else, so
    // there is nothing to re-wire per row — one delegated listener on the
    // view (attached at build) handles every control. This hook exists for
    // the one thing delegation cannot do: land the ingredients icon's entry
    // on the ingredients list rather than the top of the screen.
    if (cookState.focusScrollTo === 'ingredients') {
      cookState.focusScrollTo = null;
      var el = view.querySelector('.cook-ings');
      if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'auto', block: 'start' });
    }
  }

  // ---------- Cook: the attention banner ----------
  // Carried over from cooker.html whole: the three shapes an attention item
  // can take (a plain one that can be resolved or dismissed, an
  // inventory-usage one that wants an amount, and the feedback nudge that
  // wants a rating) all still exist and still hit the same endpoints.
  function cookAttentionHtml() {
    var items = cookState.attention || [];
    if (!items.length) return '';
    // Folded behind a count, same move as Grocery's "Maybe already home"
    // (groPreShopHtml) — quiet by default, one tap opens it, per the
    // inventory-is-background policy: nobody should have to read a wall of
    // questions to get to tonight.
    var open = cookState.attentionOpen;
    var sub = items.length === 1 ? '1 quick thing from last night' : items.length + ' quick things from last night';
    var html = '<div class="cook-attention">' +
      '<button type="button" class="cook-attention-head" data-cook="attn-toggle" aria-expanded="' + open + '">' +
        '<span class="cook-attention-text">' +
          '<span class="cook-attention-title">Needs your attention</span>' +
          '<span class="cook-attention-sub">' + escapeHtml(sub) + '</span>' +
        '</span>' +
        '<span class="cook-attention-toggle">' + (open ? 'Hide' : 'Review') + '</span>' +
      '</button>';
    if (!open) return html + '</div>';
    html += '<div class="cook-attention-body">';
    html += items.map(function (it) {
        var needsAmount = it.id != null && it.detail && it.detail.needs_amount_used;
        if (needsAmount) {
          return '<div class="cook-attn-item is-stacked" data-attn-id="' + it.id + '">' +
            '<span class="cook-attn-summary">' + escapeHtml(it.summary) + '</span>' +
            '<span class="cook-attn-row">' +
              '<input type="text" class="cook-attn-input" data-attn-input="' + it.id + '" ' +
                'placeholder="e.g. 1 cup, or leave blank for all of it" aria-label="Amount used" />' +
              '<button type="button" class="cook-attn-go" data-cook="attn-use" data-attn-id="' + it.id + '">Log it</button>' +
              '<button type="button" class="cook-attn-skip" data-cook="attn-resolve" data-attn-id="' + it.id + '" data-status="dismissed">Skip</button>' +
            '</span>' +
          '</div>';
        }
        if (it.kind === 'feedback_nudge') {
          var mealName = (it.detail && it.detail.meal) || '';
          return '<div class="cook-attn-item is-stacked" data-attn-meal="' + escapeHtml(mealName) + '">' +
            '<span class="cook-attn-summary">' + escapeHtml(it.summary) + '</span>' +
            '<span class="cook-attn-row">' +
              '<button type="button" class="cook-attn-fb" data-cook="feedback" data-rating="liked" data-meal="' + escapeHtml(mealName) + '">Liked it</button>' +
              '<button type="button" class="cook-attn-fb" data-cook="feedback" data-rating="disliked" data-meal="' + escapeHtml(mealName) + '">Not a hit</button>' +
              '<input type="text" class="cook-attn-input" data-attn-notes="' + escapeHtml(mealName) + '" placeholder="Notes (optional)" aria-label="Notes" />' +
            '</span>' +
          '</div>';
        }
        return '<div class="cook-attn-item">' +
          '<span class="cook-attn-summary">' + escapeHtml(it.summary) + '</span>' +
          (it.id != null
            ? '<span class="cook-attn-actions">' +
                '<button type="button" class="cook-attn-btn" data-cook="attn-resolve" data-attn-id="' + it.id + '" data-status="resolved" aria-label="Mark handled" title="Mark handled">' + COOK_ICONS.check + '</button>' +
                '<button type="button" class="cook-attn-btn is-dismiss" data-cook="attn-resolve" data-attn-id="' + it.id + '" data-status="dismissed" aria-label="Not relevant" title="Not relevant">&times;</button>' +
              '</span>'
            : '') +
        '</div>';
      }).join('');
    html += '</div></div>';
    return html;
  }

  async function refreshCookAttention() {
    try {
      var res = await fetch('/api/attention');
      if (res.ok) {
        cookState.attention = (await res.json()).items || [];
        renderCook();
      }
    } catch (err) { /* non-critical */ }
  }

  // ---------- Cook: actions ----------
  // One delegated listener for the whole view. The page this replaces
  // re-wired every control after every render, which is where a missed
  // handler hides.
  function onCookClick(e) {
    var el = e.target.closest('[data-cook]');
    if (!el) return;
    var what = el.getAttribute('data-cook');

    if (what === 'focus') {
      var idx = parseInt(el.getAttribute('data-idx'), 10);
      cookState.focusScrollTo = el.getAttribute('data-at') === 'ingredients' ? 'ingredients' : null;
      cookEnterFocus(idx);
      return;
    }
    if (what === 'exit-focus') return cookExitFocus();
    if (what === 'session') return cookEnterSession(el.getAttribute('data-date'));
    if (what === 'exit-session') return cookExitFocus();
    if (what === 'prep-days') return openRhythmFromCook();
    if (what === 'prep-cut-pick') {
      var cutPicks = cookPrepCutPicks({ entry_id: el.getAttribute('data-entry-id') });
      var cutItem = el.getAttribute('data-item');
      if (cutPicks[cutItem]) delete cutPicks[cutItem]; else cutPicks[cutItem] = true;
      renderCook();
      return;
    }
    if (what === 'prep-cut-go') return cookAddPrepCuts(el);
    if (what === 'goto-plan') return setMealsView('plan');
    if (what === 'focus-check') return cookFocusCheckMeal(el);
    if (what === 'check-step') {
      var stepKey = el.getAttribute('data-idx') + ':' + el.getAttribute('data-step');
      cookState.focusStepsChecked[stepKey] = !cookState.focusStepsChecked[stepKey];
      renderCook();
      return;
    }
    if (what === 'why') {
      var text = document.getElementById('cook-why-' + el.getAttribute('data-idx'));
      if (text) text.hidden = !text.hidden;
      return;
    }
    if (what === 'ahead-day') {
      var sourceId = el.getAttribute('data-source-id');
      var picks = cookState.cookAheadPicks[sourceId] ||
        (cookState.cookAheadPicks[sourceId] = {});
      var dayId = el.getAttribute('data-day-id');
      if (picks[dayId]) delete picks[dayId]; else picks[dayId] = true;
      renderCook();
      return;
    }
    if (what === 'ahead-go') return cookSetCookAhead(el);
    if (what === 'check-meal') return cookCheckMeal(el);
    if (what === 'check-prep') return cookCheckPrep(el);
    if (what === 'defrost-ask') return openDefrostAskFromCook();
    if (what === 'cook-ahead-ask') return openCookAheadAskFromCook();
    if (what === 'serves') return cookStepServings(el);
    if (what === 'fill') return cookFillRecipe(el);
    if (what === 'attn-toggle') { cookState.attentionOpen = !cookState.attentionOpen; renderCook(); return; }
    if (what === 'attn-resolve') return cookResolveAttention(el);
    if (what === 'attn-use') return cookLogUsage(el);
    if (what === 'feedback') return cookRateMeal(el);
    if (what === 'voice') return cookToggleVoice(el);
  }

  async function cookPost(url, body) {
    var res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    if (!res.ok) {
      // Some of these routes answer a refusal with the sentence to show
      // (see /api/cooker/cook-ahead) rather than with a failure to
      // explain — carry it on the error so the caller can say it out loud.
      var err = new Error('request failed');
      var payload = await res.json().catch(function () { return {}; });
      if (typeof (payload || {}).detail === 'string') err.detail = payload.detail;
      throw err;
    }
    return res.json().catch(function () { return {}; });
  }

  // "core loop handoffs, slice 2" item E (Emily, 2026-09-05): marking a
  // real cook done gets a toast confirming it was logged, with a "Rate it"
  // action that opens the existing attention band rather than a new
  // rating flow — the feedback nudge already lives there once the app has
  // something to ask about. Reheat nights ("Mark eaten") are excluded:
  // there's no separate cook to rate, the dish was already rated the
  // night it was actually made.
  function toastMealLogged() {
    showToast('Logged. I’ll remember how it went.', {
      label: 'Rate it',
      onClick: function () { cookState.attentionOpen = true; renderCook(); },
    });
  }

  async function cookCheckMeal(el) {
    el.disabled = true;
    var justCooked = el.getAttribute('data-next') === 'done' && el.getAttribute('aria-label') === 'Mark cooked';
    try {
      var view = await cookPost('/api/cooker/check-meal', {
        entry_id: parseInt(el.getAttribute('data-entry-id'), 10),
        status: el.getAttribute('data-next')
      });
      renderCookFrom(view);
      // Checking a meal off can queue new inventory-depletion items, and it
      // moves the week's "N of M cooked" everywhere else that counts it.
      refreshCookAttention();
      refreshPlanSurfacesAfterCook();
      if (justCooked) toastMealLogged();
    } catch (err) {
      el.disabled = false;
      showToast('That didn’t save — try again.');
    }
  }

  // The focused screen's own check-off: marking a meal cooked completes
  // and returns to the overview (the ticket's answer to "what happens when
  // you check the meal off") — there's nothing left to do on this screen
  // once it's done. Marking it back to not-cooked is an undo, not a
  // completion, so that one stays put in focus rather than bouncing out.
  // Always a real cook, never a reheat (see cookHeroHtml/cookReheatHeroHtml
  // — a reheat night never opens this focused screen), so no aria-label
  // check is needed here the way cookCheckMeal above needs one.
  async function cookFocusCheckMeal(el) {
    el.disabled = true;
    var next = el.getAttribute('data-next');
    try {
      var view = await cookPost('/api/cooker/check-meal', {
        entry_id: parseInt(el.getAttribute('data-entry-id'), 10),
        status: next
      });
      if (next === 'done') {
        cookState.screen = 'overview';
        cookState.pendingScrollTop = true;
      }
      renderCookFrom(view);
      refreshCookAttention();
      refreshPlanSurfacesAfterCook();
      if (next === 'done') toastMealLogged();
    } catch (err) {
      el.disabled = false;
      showToast('That didn’t save — try again.');
    }
  }

  // Confirming the picker: one write, then the whole screen re-renders
  // from the view it hands back — the covered days become "made ahead"
  // cards and this one starts cooking for all of them.
  async function cookSetCookAhead(el) {
    var sourceId = parseInt(el.getAttribute('data-source-id'), 10);
    var picks = cookState.cookAheadPicks[sourceId] || {};
    var covered = Object.keys(picks)
      .filter(function (k) { return picks[k]; })
      .map(function (k) { return parseInt(k, 10); });
    el.disabled = true;
    try {
      var view = await cookPost('/api/cooker/cook-ahead', {
        source_entry_id: sourceId,
        covered_entry_ids: covered
      });
      renderCookFrom(view);
      // Consolidating cooks changes what Today's dinner hero and the
      // week's prep rail read, exactly as checking a meal off does.
      refreshPlanSurfacesAfterCook();
    } catch (err) {
      el.disabled = false;
      showToast(err && err.detail ? err.detail : 'That didn’t save — try again.');
    }
  }

  async function cookCheckPrep(el) {
    el.disabled = true;
    try {
      renderCookFrom(await cookPost('/api/cooker/check-prep', {
        prep_task_id: parseInt(el.getAttribute('data-prep-id'), 10),
        status: el.getAttribute('data-next')
      }));
      refreshPlanSurfacesAfterCook();
    } catch (err) {
      el.disabled = false;
      showToast('That didn’t save — try again.');
    }
  }

  // Cooking changes what Today shows — its moves are read off the same
  // rows — so the other screens are told rather than left to go stale; the
  // freshness policy applies to a write made here exactly as it does to one
  // made in chat.
  function refreshPlanSurfacesAfterCook() {
    refreshTodayMoves();
  }

  // Live re-scale without a plan reload. Non-numeric quantities ("a pinch",
  // "to taste") cannot scale mathematically, so the backend leaves those
  // alone and names them in unscaled_items rather than guessing.
  async function cookStepServings(el) {
    var idx = el.getAttribute('data-idx');
    var wrap = el.closest('.cook-serves');
    var countEl = document.getElementById('cook-serves-' + idx);
    if (!wrap || !countEl) return;
    var delta = parseInt(el.getAttribute('data-delta'), 10);
    var base = parseInt(wrap.getAttribute('data-base'), 10) || 1;
    var current = parseInt(countEl.textContent, 10) || base;
    var next = Math.max(1, current + delta);
    if (next === current) return;
    countEl.textContent = next;

    var list = document.getElementById('cook-ings-' + idx);
    var note = document.getElementById('cook-unscaled-' + idx);
    try {
      var res = await fetch('/api/recipes/scale?name=' + encodeURIComponent(wrap.getAttribute('data-recipe')) + '&servings=' + next);
      if (!res.ok) throw new Error('scale failed');
      var data = await res.json();
      if (list) {
        list.innerHTML = (data.scaled_ingredients || []).map(function (i) {
          return '<li>' + escapeHtml((i.qty ? i.qty + ' ' : '') + (i.item || '')) + '</li>';
        }).join('') || '<li class="cook-dim">None listed</li>';
      }
      if (note) {
        if (data.unscaled_items && data.unscaled_items.length) {
          note.textContent = 'Eyeball these — they don’t scale automatically: ' + data.unscaled_items.join(', ') + '.';
          note.hidden = false;
        } else {
          note.hidden = true;
        }
      }
    } catch (err) {
      // Leave the list as it was rather than breaking the recipe over a
      // failed scale; the number in the stepper is the only thing that moved.
    }
  }

  async function cookFillRecipe(el) {
    el.disabled = true;
    var original = el.textContent;
    el.textContent = 'Writing recipe…';
    try {
      renderCookFrom(await cookPost('/api/cooker/fill-recipe', { recipe_name: el.getAttribute('data-recipe') }));
    } catch (err) {
      el.disabled = false;
      el.textContent = original;
      showToast('Couldn’t write that recipe right now — try again.');
    }
  }

  async function cookResolveAttention(el) {
    el.disabled = true;
    try {
      var data = await cookPost('/api/attention/' + el.getAttribute('data-attn-id') + '/resolve', {
        status: el.getAttribute('data-status')
      });
      cookState.attention = data.items || [];
      renderCook();
    } catch (err) {
      el.disabled = false;
      showToast('That didn’t save — try again.');
    }
  }

  async function cookLogUsage(el) {
    var id = el.getAttribute('data-attn-id');
    var input = document.querySelector('[data-attn-input="' + id + '"]');
    el.disabled = true;
    try {
      var data = await cookPost('/api/attention/' + id + '/use', {
        amount_used: input ? input.value.trim() : ''
      });
      cookState.attention = data.items || [];
      renderCook();
    } catch (err) {
      el.disabled = false;
      showToast('Couldn’t log that — try again.');
    }
  }

  // Tomorrow's date, local — same construction as todayLocalStr, one day
  // on. Used only to decide whether the post-rating toast below has
  // somewhere useful to send "Show me tomorrow".
  function tomorrowLocalStr() {
    var d = new Date();
    d.setDate(d.getDate() + 1);
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + m + '-' + day;
  }

  // Read off data already on the client (this week's prep_tasks, general
  // and defrost alike) rather than a new lookup — the rating toast just
  // wants to know whether pointing at tomorrow is worth offering at all.
  function cookTomorrowHasPrepOrDefrost() {
    var tasks = (cookState.data && cookState.data.prep_tasks) || [];
    var tomorrow = tomorrowLocalStr();
    return tasks.some(function (t) { return t.task_date === tomorrow; });
  }

  async function cookRateMeal(el) {
    var meal = el.getAttribute('data-meal');
    var notesEl = document.querySelector('[data-attn-notes="' + meal.replace(/"/g, '\\"') + '"]');
    var hadAttention = (cookState.attention || []).length > 0;
    el.disabled = true;
    try {
      await cookPost('/api/recipe-feedback', {
        recipe_name: meal,
        rating: el.getAttribute('data-rating'),
        notes: notesEl ? notesEl.value.trim() : ''
      });
      await refreshCookAttention();
      // Only the rating that actually empties the list earns the toast —
      // rating one of several still leaves "attention" open, which isn't
      // "noted, done" yet.
      if (hadAttention && !(cookState.attention || []).length) {
        showToast('Noted — that’ll steer next week.', cookTomorrowHasPrepOrDefrost() ? {
          label: 'Show me tomorrow',
          onClick: function () { activateTab('week', true, { mealsView: 'cook' }); }
        } : undefined);
      }
    } catch (err) {
      el.disabled = false;
      showToast('Couldn’t save that rating — try again.');
    }
  }

  // ---------- Cook: hands-free ----------
  // The same two sessions the old page had — one scoped to the prep
  // schedule, one to a single recipe — on the same shared engine
  // (voice-session.js) the Grocery screen uses. Behaviour is carried over
  // unchanged, per the blueprint's "hands-free voice keeps its current
  // behaviour".
  function cookVoiceEl() { return document.getElementById('cook-voice'); }

  function setCookVoiceStatus(text) {
    var el = cookVoiceEl();
    if (!el) return;
    if (!text) {
      cookState.voiceLog = [];
      el.hidden = true;
      el.innerHTML = '';
      return;
    }
    // A short scrollback rather than one overwritten line: a single status
    // flashed by before it could be read, which made "it never hears me"
    // impossible to tell apart from "it heard something else".
    cookState.voiceLog.unshift(text);
    cookState.voiceLog = cookState.voiceLog.slice(0, 5);
    el.hidden = false;
    el.innerHTML =
      '<span class="cook-voice-dot"></span>Listening&hellip;' +
      '<ul class="cook-voice-log">' + cookState.voiceLog.map(function (t) {
        return '<li>' + escapeHtml(t) + '</li>';
      }).join('') + '</ul>' +
      '<span class="cook-voice-note">Say “hey Pomona” plus a command, or tap the mic again to stop.</span>';
  }

  function updateCookVoiceButtons() {
    var active = cookState.voiceSession && cookState.voiceSession.isActive() && cookState.voiceContext;
    document.querySelectorAll('#week-cook-view .cook-mic').forEach(function (btn) {
      var ctxType = btn.getAttribute('data-ctx');
      var btnIdx = btn.getAttribute('data-idx');
      var isThisOne = !!(active && cookState.voiceContext.type === ctxType &&
        (ctxType !== 'meal' || String(cookState.voiceContext.idx) === btnIdx));
      btn.classList.toggle('listening', isThisOne);
    });
  }

  function stopCookVoice() {
    if (cookState.voiceSession && cookState.voiceSession.isActive()) cookState.voiceSession.stop();
  }

  function cookToggleVoice(el) {
    // Belt-and-suspenders: the mic buttons that dispatch here don't render
    // while COOK_VOICE_ENABLED is false, but this guard means no
    // SpeechRecognition/speechSynthesis session (and no permission prompt)
    // can be created even if something still reaches this function.
    if (!COOK_VOICE_ENABLED) return;
    var ctxType = el.getAttribute('data-ctx');
    var btnIdx = el.getAttribute('data-idx');
    var isThisActive = cookState.voiceSession && cookState.voiceSession.isActive() && cookState.voiceContext &&
      cookState.voiceContext.type === ctxType &&
      (ctxType !== 'meal' || String(cookState.voiceContext.idx) === btnIdx);
    if (isThisActive) { stopCookVoice(); return; }
    if (typeof window.createVoiceSession !== 'function') {
      showToast('Hands-free isn’t available in this browser.');
      return;
    }
    stopCookVoice();
    var ctx = ctxType === 'meal' ? { type: 'meal', idx: parseInt(btnIdx, 10) } : { type: 'prep' };
    cookState.voiceContext = ctx;
    cookState.voiceStepCursor = {};
    cookState.voiceSession = window.createVoiceSession({
      onListeningChange: function (isListening) {
        updateCookVoiceButtons();
        if (!isListening) setCookVoiceStatus('');
      },
      onStatus: function (text) { setCookVoiceStatus(text); },
      onCommand: async function (command) {
        if (ctx.type === 'prep') return handleCookPrepVoice(command);
        return handleCookMealVoice(command, ctx.idx);
      },
      onEnd: function () { cookState.voiceContext = null; updateCookVoiceButtons(); }
    });
    if (cookState.voiceSession.isStandaloneIOS()) {
      setCookVoiceStatus('Heads up: hands-free can be unreliable in the installed home-screen app on iOS — if it doesn’t seem to hear you, try a regular Safari tab.');
    }
    cookState.voiceSession.start();
    updateCookVoiceButtons();
  }

  // Matching is keyword-anywhere rather than a fixed sentence shape: real
  // speech (and imperfect transcription) rarely comes back phrased one way.
  function cookIsEndCommand(command) {
    return /\b(stop|cancel|exit|goodbye|end session|that'?s all|that’s all|all done)\b/i.test(command) ||
      /^done$/i.test(command.trim());
  }

  // Exact match, then substring, then word overlap. Deliberately permissive
  // — and it never silently changes data on a weak match by itself, because
  // the caller always says back what it acted on.
  function cookFuzzyFind(text, candidates, getLabel) {
    var t = (text || '').trim().toLowerCase();
    if (!t || !candidates || !candidates.length) return null;
    for (var i = 0; i < candidates.length; i++) {
      if (getLabel(candidates[i]).trim().toLowerCase() === t) return candidates[i];
    }
    for (var j = 0; j < candidates.length; j++) {
      var label = getLabel(candidates[j]).trim().toLowerCase();
      if (label && (label.indexOf(t) !== -1 || t.indexOf(label) !== -1)) return candidates[j];
    }
    var words = t.split(/\s+/);
    var best = null, bestScore = 0;
    candidates.forEach(function (c) {
      var labelWords = getLabel(c).trim().toLowerCase().split(/\s+/);
      var score = words.filter(function (w) { return labelWords.indexOf(w) !== -1; }).length;
      if (score > bestScore) { bestScore = score; best = c; }
    });
    return bestScore > 0 ? best : null;
  }

  async function handleCookPrepVoice(command) {
    if (cookIsEndCommand(command)) return { spoken: 'Ending hands-free.', endSession: true };
    var tasks = (cookState.data && cookState.data.prep_tasks) || [];
    var task = null;
    // Prep steps have no voice action but checking off, so "step"/"task"
    // plus a number is enough — no verb needs matching too.
    if (/\b(step|task)\b/i.test(command)) {
      var n = window.voiceParseNumber(command);
      if (n !== null) {
        task = tasks[n - 1] || null;
        if (!task) return { spoken: 'There’s no step ' + n + ' — I only count ' + tasks.length + '.' };
      }
    }
    if (!task) {
      var afterVerb = command.replace(/\b(check off|mark|complete|finish|done with)\b/gi, '');
      task = cookFuzzyFind(afterVerb, tasks, function (t) { return t.description; });
    }
    if (!task) return null;
    try {
      renderCookFrom(await cookPost('/api/cooker/check-prep', { prep_task_id: task.id, status: 'done' }));
    } catch (err) { return null; }
    refreshCookAttention();
    return { spoken: 'Got it, "' + task.description + '" marked done.' };
  }

  async function handleCookMealVoice(command, idx) {
    var meal = cookState.data && cookState.data.meals ? cookState.data.meals[idx] : null;
    if (!meal) return null;
    if (cookIsEndCommand(command)) return { spoken: 'Ending hands-free.', endSession: true };

    // Recipe steps aren't individually checkable, so "step" plus a number is
    // always a read-it-back question and never an action — no ambiguity to
    // resolve against the other commands.
    if (/\bstep\b/i.test(command)) {
      var n = window.voiceParseNumber(command);
      if (n !== null) {
        var step = (meal.instructions || [])[n - 1];
        return step
          ? { spoken: 'Step ' + n + ': ' + step }
          : { spoken: 'There’s no step ' + n + ' — this recipe has ' + (meal.instructions || []).length + ' steps.' };
      }
    }
    if (/\bnext\b/i.test(command)) {
      var cursor = cookState.voiceStepCursor[idx] || 0;
      var nextStep = (meal.instructions || [])[cursor];
      if (!nextStep) return { spoken: 'That’s the last step — nothing more after this.' };
      cookState.voiceStepCursor[idx] = cursor + 1;
      return { spoken: 'Step ' + (cursor + 1) + ': ' + nextStep };
    }
    // Marking the whole meal done needs an explicit qualifier, specifically
    // so a bare "I'm done" doesn't collide with the session-ending phrase
    // checked above.
    if (/\b(done|finish|finished|complete|completed|mark|check off)\b/i.test(command) &&
        /\b(cooking|meal|recipe|dish|this|it)\b/i.test(command)) {
      try {
        renderCookFrom(await cookPost('/api/cooker/check-meal', { entry_id: meal.entry_id, status: 'done' }));
      } catch (err) { return null; }
      refreshCookAttention();
      refreshPlanSurfacesAfterCook();
      return { spoken: 'Got it, ' + meal.meal + ' marked done.' };
    }
    if (/\b(how much|amount|how many)\b/i.test(command)) {
      var afterKeyword = command.replace(/^.*?\b(how much|how many|amount of|amount)\b/i, '').trim();
      var ing = cookFuzzyFind(afterKeyword, meal.ingredients || [], function (i) { return i.item || ''; });
      return ing
        ? { spoken: (ing.qty || 'No amount tracked') + ' of ' + ing.item + '.' }
        : { spoken: 'I don’t see that ingredient in this recipe.' };
    }
    if (/\bsubstitut|\b(swap|swapped|instead|log|note)\b/i.test(command)) {
      var note = command
        .replace(/^(?:log a substitution|log a deviation|log that|note that|note|i substituted|i swapped)[:\s]*/i, '')
        .trim() || command;
      try {
        await cookPost('/api/cooker/log-deviation', { recipe_name: meal.meal, note: note });
      } catch (err) { return null; }
      return { spoken: 'Got it, logged.' };
    }
    return null;
  }

  // ---------- Week sheet (the 6a grid) ----------
  // Same scrim/sheet/grab-handle pattern as the ask sheet, and the same
  // "only one open at a time" rule — opening this closes the ask sheet and
  // vice versa (see openAskSheet below).
  var weekSheetScrim = document.getElementById('week-sheet-scrim');
  var weekSheetEl = document.getElementById('week-sheet');

  function renderWeekSheetRows(days) {
    if (!weekSheetEl) return;
    var range = days.length
      ? dayName(days[0].date, { month: 'short', day: 'numeric' }) + '–' + dayName(days[days.length - 1].date, { day: 'numeric' })
      : '';
    document.getElementById('week-sheet-range').textContent = range;

    document.getElementById('week-sheet-rows').innerHTML = days.map(function (day, i) {
      // An open slot is a gap in the sense that matters here — something
      // waiting on the household. A planned_empty night is not: it needs
      // no decision, which is exactly why it's its own state.
      var hasGap = !day.isPast && WEEK_SLOTS.some(function (s) {
        return !day[s] || day[s].state === 'open';
      });
      var rowClass = 'week-sheet-row' +
        (i === weekState.selectedIndex ? ' selected' : '') +
        (day.isPast ? ' past' : '') +
        (hasGap ? ' gap' : '');
      return (
        '<button type="button" class="' + rowClass + '" data-index="' + i + '">' +
          '<span class="week-sheet-day-label">' + dayName(day.date, { weekday: 'short' }).slice(0, 3).toUpperCase() + '</span>' +
          WEEK_SLOTS.map(function (slot) {
            var entry = day[slot];
            var cellClass = 'week-sheet-cell' + (slot === 'dinner' ? ' dinner' : '');
            if (entry && entry.state === 'planned_empty') {
              return '<span class="' + cellClass + ' blank">' + escapeHtml(entry.title) + '</span>';
            }
            if (entry && entry.state === 'open') {
              return '<span class="' + cellClass + ' empty">Your call</span>';
            }
            if (entry) return '<span class="' + cellClass + '">' + escapeHtml(entry.title) + '</span>';
            if (day.isPast) return '<span class="' + cellClass + ' blank">Not planned</span>';
            return '<span class="' + cellClass + ' empty">Open' + (slot === 'dinner' ? '' : '') + '</span>';
          }).join('') +
        '</button>'
      );
    }).join('');

    document.getElementById('week-sheet-rows').querySelectorAll('.week-sheet-row').forEach(function (btn) {
      btn.addEventListener('click', function () {
        closeWeekSheet();
        goMealsStep('day', { dayIndex: Number(btn.dataset.index) });
      });
    });

    var backBtn = document.getElementById('week-sheet-back');
    if (backBtn && weekState.days[weekState.selectedIndex]) {
      backBtn.textContent = 'Back to ' + dayName(weekState.days[weekState.selectedIndex].date, { weekday: 'long' });
    }
  }

  function openWeekSheet() {
    if (!weekSheetEl || !weekState.days.length) return;
    closeAskSheet();
    renderWeekSheetRows(weekState.days);
    weekSheetScrim.hidden = false;
    weekSheetEl.hidden = false;
  }
  function closeWeekSheet() {
    if (!weekSheetScrim) return;
    weekSheetScrim.hidden = true;
    weekSheetEl.hidden = true;
  }
  if (weekSheetScrim) {
    weekSheetScrim.addEventListener('click', closeWeekSheet);
    document.getElementById('week-sheet-handle').addEventListener('click', closeWeekSheet);
    document.getElementById('week-sheet-back').addEventListener('click', closeWeekSheet);
    document.getElementById('week-sheet-share').addEventListener('click', shareWeekPlan);
  }

  // ---------- Start over (self-service reset) ----------
  // Backed by GET /api/reset/preview (counts, so the dialog names real
  // numbers before anything is deleted) and POST /api/reset (the two
  // independent clears). Scoped to the meal plan and the grocery list and
  // nothing else — recipes/chores/members/inventory are untouched, which
  // the dialog says out loud, because the only other "reset" this app has
  // is reset_household.py, which wipes all of it.
  //
  // Judgment calls:
  //   - Both boxes start checked (the common case is "this week needs a
  //     do-over"), but a reset with nothing to remove starts unchecked and
  //     disabled rather than silently doing nothing.
  //   - Deleting is not undoable here, so the confirm button reads
  //     "Start over" only while something is actually selected, and the
  //     dialog stays open (button re-enabled) if the request fails —
  //     closing it would leave you unsure whether anything happened.
  var resetScrim = document.getElementById('reset-scrim');
  var resetDialog = document.getElementById('reset-dialog');
  var resetMealCb = document.getElementById('reset-meal-plan');
  var resetGroceryCb = document.getElementById('reset-grocery-list');
  var resetConfirmBtn = document.getElementById('reset-confirm');
  var resetSubmitting = false;

  function plural(n, one, many) {
    return n + ' ' + (n === 1 ? one : many);
  }

  function setResetOptionState(cb, subEl, count, emptyText, filledText) {
    var row = cb.closest('.reset-option');
    var isEmpty = !count;
    row.classList.toggle('is-empty', isEmpty);
    cb.disabled = isEmpty;
    cb.checked = !isEmpty;
    subEl.textContent = isEmpty ? emptyText : filledText;
  }

  function syncResetConfirmBtn() {
    if (!resetConfirmBtn) return;
    resetConfirmBtn.disabled = resetSubmitting || (!resetMealCb.checked && !resetGroceryCb.checked);
  }

  async function openResetDialog() {
    if (!resetDialog) return;
    closeAskSheet();
    closeWeekSheet();
    resetSubmitting = false;
    resetConfirmBtn.textContent = 'Start over';
    var mealSub = document.getElementById('reset-meal-plan-sub');
    var grocerySub = document.getElementById('reset-grocery-list-sub');
    mealSub.textContent = 'Checking…';
    grocerySub.textContent = 'Checking…';
    resetMealCb.disabled = true;
    resetGroceryCb.disabled = true;
    resetConfirmBtn.disabled = true;
    resetScrim.hidden = false;
    resetDialog.hidden = false;

    try {
      var res = await fetch('/api/reset/preview');
      if (!res.ok) throw new Error('reset preview failed');
      var data = await res.json();
      setResetOptionState(
        resetMealCb, mealSub, data.meal_count,
        'Nothing planned this week.',
        'Removes ' + plural(data.meal_count, 'planned meal', 'planned meals') + ' and the groceries they added.'
      );
      setResetOptionState(
        resetGroceryCb, grocerySub, data.grocery_count,
        'The list is already empty.',
        'Removes ' + plural(data.grocery_count, 'item', 'items') + ' still to buy.'
      );
    } catch (err) {
      console.warn('Reset preview failed:', err);
      // Don't offer a delete we couldn't size up — the counts are the whole
      // point of confirming, so fail closed rather than guessing.
      mealSub.textContent = "Couldn't check right now.";
      grocerySub.textContent = "Couldn't check right now.";
      resetMealCb.checked = false;
      resetGroceryCb.checked = false;
    }
    syncResetConfirmBtn();
  }

  function closeResetDialog() {
    if (!resetScrim) return;
    resetScrim.hidden = true;
    resetDialog.hidden = true;
  }

  async function runReset() {
    if (resetSubmitting) return;
    var doMealPlan = resetMealCb.checked;
    var doGroceryList = resetGroceryCb.checked;
    if (!doMealPlan && !doGroceryList) return;
    resetSubmitting = true;
    resetConfirmBtn.textContent = 'Starting over…';
    syncResetConfirmBtn();
    try {
      var res = await fetch('/api/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ meal_plan: doMealPlan, grocery_list: doGroceryList })
      });
      if (!res.ok) throw new Error('reset failed');
      var data = await res.json();
      closeResetDialog();
      refreshAfterReset(doMealPlan, doGroceryList);
      // Counting both clears separately would under-report the list: with
      // both selected the plan goes first, so its ingredients are already
      // gone by the time the list clear runs and only whatever a person had
      // added themselves is left for it to remove ("4 meals and 1 grocery
      // item" for a list that just went from 9 to 0). Name the list rather
      // than a number when both ran.
      var summary;
      if (data.meal_plan && data.grocery_list) {
        summary = 'Cleared ' + plural(data.meal_plan.meals_cleared, 'meal', 'meals') + ' and the grocery list';
      } else if (data.meal_plan) {
        summary = 'Cleared ' + plural(data.meal_plan.meals_cleared, 'meal', 'meals');
      } else {
        summary = 'Cleared ' + plural(data.grocery_list.removed_count, 'grocery item', 'grocery items');
      }
      showToast(summary + '. Fresh start.');
    } catch (err) {
      console.warn('Reset failed:', err);
      resetConfirmBtn.textContent = "Couldn't do that — try again";
    } finally {
      resetSubmitting = false;
      syncResetConfirmBtn();
    }
  }

  // Every surface that could now be showing meals or groceries that no
  // longer exist. Same staleness problem refreshStaleTabsFromActions()
  // solves for chat-driven changes (tab panels build once per page load),
  // reached from a button instead of a chat turn. Grocery used to need its
  // own special case here — a contentWindow.location.reload() through the
  // iframe boundary; now it is a panel like the others.
  function refreshAfterReset(clearedMealPlan, clearedGroceryList) {
    // loadWeekMenu takes the Cook state with it — clearing the week cannot
    // leave Cook holding a plan that no longer exists.
    if (panels.week && panels.week.dataset.built) loadWeekMenu(panels.week);
    if (panels.today && panels.today.dataset.built) {
      if (clearedMealPlan) loadNeedsYou(panels.today);
      loadTodayMoves(panels.today);
    }
    if (clearedGroceryList || clearedMealPlan) refreshGroceryPanel();
  }

  if (resetScrim) {
    resetScrim.addEventListener('click', closeResetDialog);
    document.getElementById('reset-cancel').addEventListener('click', closeResetDialog);
    resetConfirmBtn.addEventListener('click', runReset);
    resetMealCb.addEventListener('change', syncResetConfirmBtn);
    resetGroceryCb.addEventListener('change', syncResetConfirmBtn);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !resetDialog.hidden) closeResetDialog();
    });
  }

  // ---------- Docked ask bar ----------
  var askBar = document.getElementById('ask-bar');
  if (askBar) {
    askBar.addEventListener('click', function () { openAskSheet(); });
  }

  // ---------- Share meal plan (rail button + week sheet's "Share") ----------
  // Same flow as the original "Share meal plan" link in static/index.html —
  // reused as-is against the same /api/share-link endpoint.
  async function shareWeekPlan() {
    try {
      var res = await fetch('/api/share-link');
      if (!res.ok) throw new Error('Could not get link');
      var data = await res.json();
      var url = window.location.origin + '/share/' + data.token;
      var copied = false;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        try { await navigator.clipboard.writeText(url); copied = true; } catch (e) { /* fall through to the prompt */ }
      }
      if (copied) {
        showToast('Link copied. Anyone with it sees this week’s meals, nothing else.');
      } else {
        // Only reached when the clipboard API itself isn't there (or
        // refused) — the prompt's own text box is the fallback way to
        // actually get the link off the screen.
        window.prompt('Read-only link — anyone with it can see this week\'s meal plan (nothing else):', url);
      }
    } catch (err) {
      alert('Could not create a share link right now: ' + err.message);
    }
  }
  var shareBtn = document.getElementById('rail-share');
  if (shareBtn) shareBtn.addEventListener('click', shareWeekPlan);

  // ---------- Ask sheet (Step 3) ----------
  // README §4 "Ask sheet": chat moves off the home screen into a sheet
  // reachable from every route. Ported from static/index.html: the
  // markdown-lite renderer (bold/bullets/tables in a reply) and the
  // loading-phrase picker, so replies still look the same as they always
  // did. NOT ported: voice dictation (the mic button) — index.html still
  // has it standalone; wiring it into this composer too is a reasonable
  // follow-up but out of scope for "move chat into the sheet."
  var askScrim = document.getElementById('ask-scrim');
  var askSheet = document.getElementById('ask-sheet');
  var askMessagesEl = document.getElementById('ask-messages');
  var askChipsEl = document.getElementById('ask-chips');
  var askComposer = document.getElementById('ask-composer');
  var askInput = document.getElementById('ask-input');
  var askSendBtn = document.getElementById('ask-send-btn');
  var askSessionId = 'default'; // same shared backend session static/index.html always used
  var askBuilt = false;
  var askSending = false;
  var askConversationStarted = false;

  // Once the household has actually said something, the "tap a suggestion"
  // chips no longer make sense sitting above an ongoing conversation —
  // hide them for the rest of this session rather than leaving them
  // dangling under real messages.
  function hideAskChips() {
    askChipTargets().forEach(function (chipsEl) { chipsEl.innerHTML = ''; chipsEl.hidden = true; });
  }

  // Context-aware quick actions (Loop Board: "Pomona: rethink the chat's
  // pre-given quick actions", decided by Emily 2026-09-03) — replaces the
  // old static seven-item list above with 2 suggestions grounded in the
  // core weekly loop (plan → approve → prep/cook → grocery), chosen from
  // the household's actual state rather than shown every time regardless
  // of context. Deliberately dumb and local: no LLM call to pick these,
  // just a state → suggestion table, computed once per page load the same
  // moment the ask experience is first built (askBuilt below).
  //
  // State → suggestion table (also documented on the ticket):
  //   no plan yet (weekly_plan_id is null)         -> "Plan my week"
  //   plan exists, status !== 'approved' (drafted) -> "Approve this week"
  //   plan approved, local hour < 17 (daytime)      -> "What should I prep today?"
  //   plan approved, local hour >= 17 (evening)     -> "What's for dinner tonight?"
  //   always alongside the above                    -> "Add … to the grocery list"
  //     (this one doesn't send — it focuses the composer with "Add "
  //     pre-filled, per the brief's "open-ended one" behavior, since what
  //     to add is the household's to finish typing, not ours to guess.)
  var GROCERY_QUICK_ACTION = { label: 'Add … to the grocery list', prefill: 'Add ' };

  // Local hour, not UTC — same reasoning as todayLocalStr()/dayName() above:
  // "daytime" vs "evening" has to match the person's own clock. 17:00 is the
  // cutoff: before it, the useful question is what to prep ahead of dinner;
  // from then on, dinner itself is the near-term thing.
  function isEveningLocal() {
    return new Date().getHours() >= 17;
  }

  function computeContextQuickActions(weekMenu) {
    var hasPlan = !!(weekMenu && weekMenu.weekly_plan_id);
    var primary;
    if (!hasPlan) {
      primary = { label: 'Plan my week', msg: 'Let’s plan my week.' };
    } else if (weekMenu.status !== 'approved') {
      primary = { label: 'Approve this week', msg: 'I’d like to approve this week’s plan.' };
    } else if (isEveningLocal()) {
      primary = { label: 'What’s for dinner tonight?', msg: 'What’s for dinner tonight?' };
    } else {
      primary = { label: 'What should I prep today?', msg: 'What should I prep today?' };
    }
    return [primary, GROCERY_QUICK_ACTION];
  }

  // Fetches the household's current plan fresh rather than trusting
  // weekState.data — that cache can be empty (Week tab never opened this
  // load) or pinned to a past week (weekState.showWeekStart), neither of
  // which is "the current state" this chip logic needs. GET /api/week-menu
  // with no weekly_plan_id is cheap (a local SQLite lookup) and always
  // means "the household's current plan" (tools.get_week_menu's own
  // documented convention). A failed fetch degrades to the no-plan
  // suggestion rather than throwing — a wrong guess here is a missed
  // suggestion, not a broken chat.
  function loadQuickActionChips() {
    fetch('/api/week-menu')
      .then(function (res) { return res.ok ? res.json() : null; })
      .catch(function () { return null; })
      .then(function (weekMenu) { renderAskChips(computeContextQuickActions(weekMenu)); });
  }

  function renderAskChips(actions) {
    askChipTargets().forEach(function (chipsEl) {
      chipsEl.hidden = false;
      chipsEl.innerHTML = actions.map(function (q, i) {
        return '<button type="button" class="ask-chip" data-i="' + i + '">' + escapeHtml(q.label) + '</button>';
      }).join('');
      chipsEl.querySelectorAll('.ask-chip').forEach(function (chip) {
        chip.addEventListener('click', function () {
          var action = actions[Number(chip.dataset.i)];
          // The post-change next-step chips (offerNextStepChips) navigate
          // directly rather than sending a message — "Open the list",
          // "Plan my stops" and "See your week" are places to go, not
          // things to ask about.
          if (action.onClick) return action.onClick();
          // The grocery chip pre-fills and focuses instead of sending —
          // what to add is the household's call, not something to guess at
          // and send as a message. openAskSheet(prefill) already knows how
          // to do this on both the mobile sheet and the desktop column.
          if (action.prefill) openAskSheet(action.prefill);
          else sendAskMessage(action.msg);
        });
      });
    });
  }

  // "core loop handoffs, slice 2" item B (Emily, 2026-09-05): once
  // hideAskChips has fired (after the household's first message), the
  // pre-conversation quick-action chips are gone for good — but a turn
  // that actually changed something still has an obvious next step, and
  // making the household type it out again is exactly the friction the
  // quick-action chips exist to remove. So: after any turn whose actions
  // (the same {tab, change} cards refreshStaleTabsFromActions reads) show
  // a real change, recompute and show the relevant chip(s). A turn that
  // changed nothing — a question answered — gets none, which is the point
  // of gating on `actions` rather than on "a turn happened."
  //
  // NOTE (2026-09-08): this pair was added by e2024a4 and then silently
  // lost from main in merge 2d69951 ("Merge custom-date-range"), which
  // took the other side of the conflicted region wholesale. Restored here
  // alongside the "See your week" chip below, because that chip has
  // nowhere to live without it.
  //
  // Priority for the PRIMARY chip when a turn touched more than one area:
  // an approval (which often ALSO carries a grocery action for the items
  // it just added) beats a plain grocery edit, which beats an unapproved
  // draft edit — the biggest life-cycle event wins.
  //
  // "See your week" (Emily, 2026-09-08, Loop Board "Tweak-the-week chat:
  // after a swap the flow dies") rides ahead of that primary whenever the
  // turn edited a draft week: after a swap the receipt card says WEEK
  // UPDATED but every other affordance here only sends another message,
  // so there was no way to go LOOK at what just changed without hunting
  // for the tab yourself. It goes FIRST because looking is free and
  // reversible and approving is neither — see, then approve.
  function computeNextStepChips(actions) {
    var weekAction = null, groceryAction = null;
    (actions || []).forEach(function (a) {
      if (a.tab === 'week') weekAction = a;
      if (a.tab === 'grocery') groceryAction = a;
    });
    // approve_weekly_plan is the one 'week' tool whose action card's
    // `change` text says "approved" (app/main.py's _categorize_tool
    // special-cases it to "Week approved — your list is ready") — the
    // only signal available here that this turn was an approval rather
    // than an ordinary draft edit.
    var weekApproved = !!(weekAction && /approved/i.test(weekAction.change || ''));
    var chips = [];
    if (weekAction && !weekApproved) {
      chips.push({
        label: 'See your week',
        // The receipt card's own View does activateTab(action.tab) after
        // closeAskSheet(); this does the same, plus the two things the
        // card can't: it pins the Plan state (not Cook) and lands on the
        // day that changed. closeAskSheet() is a no-op at desktop widths,
        // where the Ask column is always visible and the week is already
        // on screen beside it — there, this just selects the day.
        onClick: function () {
          closeAskSheet();
          focusChangedWeekDay(weekAction.date, weekAction.slot);
        }
      });
    }
    if (weekApproved) {
      chips.push({ label: 'Open the list', onClick: function () { activateTab('grocery', true); } });
    } else if (groceryAction) {
      chips.push({ label: 'Plan my stops', onClick: function () { activateTab('grocery', true, { groScreen: 'plan' }); } });
    } else if (weekAction) {
      // Same label + message computeContextQuickActions already uses for
      // "there's a draft, go approve it" — one wording for one meaning.
      chips.push({ label: 'Approve this week', msg: 'I’d like to approve this week’s plan.' });
    }
    return chips;
  }

  function offerNextStepChips(actions) {
    var chips = computeNextStepChips(actions);
    if (chips.length) renderAskChips(chips);
  }

  // Land on Meals → Plan, on the day that just changed, with the changed
  // meal briefly ringed so the eye finds it without a caption telling it
  // to. `date`/`slot` come off the action card (app/main.py's ChatAction),
  // and are both optional: a component-based plan's swap has no date at
  // all, and an older cached reply won't carry the fields — in either case
  // this still does the useful half and just shows the week as it stands.
  //
  // weekState.pendingDayFocus is the handoff, because activateTab may only
  // just have *started* building the panel (buildWeekPanel → loadWeekMenu
  // is async): renderWeekMenu drains it once the days actually exist, and
  // the direct call below covers the already-built case, whichever wins.
  function focusChangedWeekDay(date, slot) {
    weekState.pendingDayFocus = date ? { date: date, slot: slot || 'dinner' } : null;
    activateTab('week', true, { mealsView: 'plan' });
    var panel = panels['week'];
    if (panel && panel.dataset.built) applyPendingDayFocus(panel);
  }

  function applyPendingDayFocus(panel) {
    var pending = weekState.pendingDayFocus;
    if (!pending || !weekState.days.length) return;
    var index = -1;
    weekState.days.forEach(function (d, i) { if (d.date === pending.date) index = i; });
    if (index < 0) return; // the change landed outside the week on screen
    weekState.pendingDayFocus = null;
    // The Day step is where a changed meal is legible — the root card
    // truncates every name to one line, which is right for "is the week
    // settled" and wrong for "look what I changed".
    goMealsStep('day', { dayIndex: index });
    var target = panel.querySelector('.wk-slot-card[data-wk-slot="' + pending.slot + '"]');
    if (!target) return;
    target.classList.add('just-changed');
    // Long enough to notice, short enough that it's gone before it can be
    // mistaken for a state the day is now in.
    setTimeout(function () { target.classList.remove('just-changed'); }, 2000);
  }

  function splitTableRow(line) {
    var cells = line.split('|');
    if (cells.length && cells[0].trim() === '') cells.shift();
    if (cells.length && cells[cells.length - 1].trim() === '') cells.pop();
    return cells.map(function (c) { return c.trim(); });
  }
  var TABLE_ROW_RE = /^\s*\|.*\|\s*$/;
  var TABLE_SEPARATOR_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

  function renderMarkdownLite(text) {
    var escaped = escapeHtml(text);
    var bolded = escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    var lines = bolded.split('\n');
    var out = [];
    var i = 0;
    while (i < lines.length) {
      if (TABLE_ROW_RE.test(lines[i]) && i + 1 < lines.length && TABLE_SEPARATOR_RE.test(lines[i + 1])) {
        var headerCells = splitTableRow(lines[i]);
        var j = i + 2;
        var rows = [];
        while (j < lines.length && TABLE_ROW_RE.test(lines[j])) { rows.push(splitTableRow(lines[j])); j++; }
        out.push(
          '<table class="ask-msg-table"><thead><tr>' +
          headerCells.map(function (c) { return '<th>' + c + '</th>'; }).join('') +
          '</tr></thead><tbody>' +
          rows.map(function (r) { return '<tr>' + r.map(function (c) { return '<td>' + c + '</td>'; }).join('') + '</tr>'; }).join('') +
          '</tbody></table>'
        );
        i = j;
      } else {
        out.push(lines[i].replace(/^(\s*)[-*]\s+/, '$1&bull;&nbsp;'));
        i++;
      }
    }
    return out.join('\n');
  }

  var LOADING_PHRASES = {
    grocery: ['Cooking up your list...', 'Sorting the aisles...', 'Filling the cart...'],
    meal: ['Cooking up a plan...', 'Simmering on your week...', 'Plating up some ideas...', 'Preheating the ideas oven...'],
    chore: ['Sweeping up the details...', 'Tidying up your schedule...', 'Dusting things off...'],
    default: ['Whipping this up...', 'Stirring up an answer...', 'Cooking something up...', 'Simmering on it...']
  };
  function pickLoadingPhrase(message) {
    var m = (message || '').toLowerCase();
    var bucket = 'default';
    if (/grocer|shopping list|\bcart\b/.test(m)) bucket = 'grocery';
    else if (/meal|dinner|breakfast|lunch|recipe|\bplan\b|cook|\beat\b/.test(m)) bucket = 'meal';
    else if (/chore|clean|tidy|vacuum|dust|laundry/.test(m)) bucket = 'chore';
    var options = LOADING_PHRASES[bucket];
    return options[Math.floor(Math.random() * options.length)];
  }

  // Step 6, §7: "The ask sheet only exists below 1024px. Above it, the ask
  // column replaces it." Both surfaces show the *same* conversation — one
  // shared history, rendered into whichever of the two message-list
  // elements currently exist in the DOM (the sheet's #ask-messages always
  // exists once the page loads; the column's #today-ask-messages only
  // exists once Today's panel has been built). Sending/receiving writes
  // into all of them at once rather than picking one "active" surface, so
  // resizing across the 1024px breakpoint never leaves the other one
  // stale or empty.
  function askMessageTargets() {
    var t = [askMessagesEl, document.getElementById('today-ask-messages')];
    return t.filter(function (el) { return !!el; });
  }
  function askChipTargets() {
    var t = [askChipsEl, document.getElementById('today-ask-chips')];
    return t.filter(function (el) { return !!el; });
  }
  function askInputTargets() {
    var t = [
      { input: askInput, btn: askSendBtn },
      { input: document.getElementById('today-ask-input'), btn: document.getElementById('today-ask-send-btn') }
    ];
    return t.filter(function (pair) { return !!pair.input; });
  }

  function ensureAskSheetBuilt() {
    if (askBuilt) return;
    askBuilt = true;
    loadQuickActionChips();
    // No exclamation mark, and an offer rather than an instruction — this
    // is the first thing the assistant ever says, and it has to sit beside
    // the same voice as the rest of the app.
    addAskMessage('assistant', 'Tell me what you’d like different and I’ll rework it — no need to be polite about it.');
  }

  function buildAskMessageEl(role, text, actions) {
    var wrap = document.createElement('div');
    wrap.className = 'ask-msg ' + role;
    var bubble = document.createElement('div');
    bubble.className = 'ask-bubble';
    bubble.innerHTML = renderMarkdownLite(text);
    wrap.appendChild(bubble);
    (actions || []).forEach(function (action) {
      var card = document.createElement('button');
      card.type = 'button';
      card.className = 'ask-action-card';
      card.innerHTML =
        '<span class="ask-action-text">' +
          '<span class="ask-action-kicker">' + escapeHtml(action.kicker) + '</span>' +
          '<span class="ask-action-change">' + escapeHtml(action.change) + '</span>' +
        '</span>' +
        '<span class="ask-action-view">View</span>';
      card.addEventListener('click', function () {
        closeAskSheet();
        if (action.tab) activateTab(action.tab, true);
        else if (action.href) followActionHref(action.href);
      });
      wrap.appendChild(card);
    });
    return wrap;
  }

  function addAskMessage(role, text, actions) {
    // Returns one element per surface that received it (0-2), so the
    // caller (sendAskMessage's loading bubble) can remove/update all of
    // them together — see the multi-target comment above.
    return askMessageTargets().map(function (target) {
      var el = buildAskMessageEl(role, text, actions);
      target.appendChild(el);
      target.scrollTop = target.scrollHeight;
      return el;
    });
  }

  // A chat turn that changes the plan (generate/swap/approve a week, etc.)
  // is tagged by the backend as a "week" action (see app/main.py's
  // _categorize_tool/summarize_chat_actions). Meals/Week only ever fetches
  // its data once per page load (buildWeekPanel's dataset.built guard,
  // same pattern Grocery/Kitchen's iframes use) — so if that tab was
  // already open in this browser tab *before* the chat made the change,
  // it keeps showing whatever it loaded at that point, even after
  // switching away and back, until a full page reload. This is the
  // "made a plan with the assistant but This Week/Meals doesn't show it"
  // report — confirmed by testing, not assumed. Rather than requiring a
  // reload, proactively refresh any already-built tab a chat action just
  // touched, the same way fillWeekDinner already refreshes Today when a
  // week-sheet dinner-pick affects it.
  //
  // Grocery was the hole in this. The backend has always tagged grocery
  // writes with tab: 'grocery' (app/main.py's _GROCERY_TOOLS), but this
  // function had no branch for it and could not have had a useful one — the
  // tab was a second document, and reaching into an iframe to re-render part
  // of it is not something a parent page can do. So "add milk" in chat
  // changed the list and the Grocery tab went on showing the old one until a
  // reload. Now that the panel is this script's own, the branch is the same
  // one line every other tab gets.
  function refreshStaleTabsFromActions(actions) {
    (actions || []).forEach(function (action) {
      if (action.tab === 'week' && panels.week && panels.week.dataset.built) {
        // loadWeekMenu refreshes the Cook state too — see its tail.
        loadWeekMenu(panels.week);
      } else if (action.tab === 'kitchen') {
        // Kitchen was the second hole in this. The backend has always
        // tagged these writes with tab: 'kitchen' (app/main.py's
        // _KITCHEN_TOOLS) and this function has never had a branch for
        // it — for the same reason Grocery didn't, and with the same
        // consequence: "we finished the chicken" in chat changed the
        // inventory and the Kitchen tab went on showing the old counts.
        //
        // The branch is two calls rather than one because that tool set
        // spans both screens now: check_off_meal / check_off_prep_step /
        // resolve_attention_item land in the Meals tab's Cook state, while
        // update_inventory and friends land on the Kitchen hub's quiet
        // tile.
        refreshKitchenPanel();
        refreshCookView();
        // check_off_prep_step is also how a fridge move gets ticked from
        // chat ("mark the chicken thighs done") — same table, same tool,
        // just called from a different surface than Today's own ticks.
        // Without this, Today would go on listing an already-handled move
        // until the next full panel rebuild.
        refreshTodayMoves();
      } else if (!action.tab && hrefSheetKey(action.href)) {
        // Household/preferences writes carry no tab at all — they carry
        // href: '/memory' (app/main.py's _MEMORY_HREF_TOOLS), because when
        // that was written no shell screen showed the household's standing
        // knowledge. The Kitchen hub does now: its People / Taste / Rhythm
        // / Stores counts are exactly what these tools change. Reading the
        // href rather than adding a tab to the backend keeps the change on
        // this side of the wire, where the screen that went stale lives.
        refreshKitchenPanel();
      } else if (action.tab === 'grocery') {
        refreshGroceryPanel();
        // The list changing also changes Today's shop move, which is a
        // reading of the same items.
        refreshTodayMoves();
      } else if (action.tab === 'today' && panels.today && panels.today.dataset.built) {
        loadNeedsYou(panels.today);
        loadTodayMoves(panels.today);
      }
    });
  }

  function setAskInputsDisabled(disabled) {
    askInputTargets().forEach(function (pair) {
      pair.input.disabled = disabled;
      pair.btn.disabled = disabled;
    });
  }

  // Server-Sent Events reader for /api/chat/stream -- hand-rolled because
  // EventSource can't carry a POST body. Resolves to the same {reply,
  // actions} shape /api/chat's plain JSON response always gave; onProgress
  // fires for every event before "done" so a caller can update a loading
  // indicator while a slow turn (one that ends up generating a whole
  // week, ~37s) is still in flight, instead of the ~2-5s a plain question
  // already took either way. See the "Make chat responses faster" ticket.
  async function streamChatMessage(payload, onProgress) {
    var res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok || !res.body) {
      var detail = '';
      try { detail = (await res.json()).detail || ''; } catch (e) { /* not JSON */ }
      throw new Error(detail || ('Request failed (' + res.status + ' ' + res.statusText + ')'));
    }
    var reader = res.body.getReader();
    var decoder = new TextDecoder();
    var buffer = '';
    var result = null;

    function handleFrame(frame) {
      var eventName = 'message';
      var dataLines = [];
      frame.split('\n').forEach(function (line) {
        if (line.indexOf('event:') === 0) eventName = line.slice(6).trim();
        else if (line.indexOf('data:') === 0) dataLines.push(line.slice(5).trim());
      });
      if (!dataLines.length) return;
      var body = JSON.parse(dataLines.join('\n'));
      if (eventName === 'done') {
        result = body;
      } else if (eventName === 'error') {
        var err = new Error(body.detail || 'Request failed');
        err.status = body.status;
        throw err;
      } else if (onProgress) {
        onProgress(eventName, body);
      }
    }

    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      var frames = buffer.split('\n\n');
      buffer = frames.pop();
      for (var i = 0; i < frames.length; i++) handleFrame(frames[i]);
    }
    if (!result) throw new Error('Request failed');
    return result;
  }

  async function sendAskMessage(message) {
    if (!message || askSending) return;
    ensureAskSheetBuilt();
    addAskMessage('user', message);
    if (!askConversationStarted) {
      askConversationStarted = true;
      hideAskChips();
    }
    askSending = true;
    setAskInputsDisabled(true);
    var loadingWraps = addAskMessage('assistant', pickLoadingPhrase(message));
    loadingWraps.forEach(function (w) { w.querySelector('.ask-bubble').classList.add('loading'); });

    try {
      // Most turns finish in a few seconds either way, so this progress
      // callback usually never fires before "done" arrives -- it only
      // earns its keep on the turn that ends up generating a whole week
      // (~37s previously silent), where the loading bubble now updates
      // instead of sitting frozen on its opening phrase the whole time.
      var plannedCount = 0;
      var data = await streamChatMessage(
        { session_id: askSessionId, message: message },
        function (eventName, body) {
          var bubbleText = null;
          if (eventName === 'status') {
            bubbleText = body.message || null;
          } else if (eventName === 'day') {
            plannedCount += 1;
            bubbleText = 'Building your week — ' + plannedCount +
              (plannedCount === 1 ? ' thing' : ' things') + ' planned so far…';
          }
          if (bubbleText) {
            loadingWraps.forEach(function (w) {
              var bubble = w.querySelector('.ask-bubble');
              if (bubble) bubble.textContent = bubbleText;
            });
          }
        }
      );
      loadingWraps.forEach(function (w) { w.remove(); });
      addAskMessage('assistant', data.reply, data.actions);
      refreshStaleTabsFromActions(data.actions);
      offerNextStepChips(data.actions);
    } catch (err) {
      loadingWraps.forEach(function (w) { w.remove(); });
      addAskMessage('assistant', 'Error: ' + err.message);
    } finally {
      askSending = false;
      setAskInputsDisabled(false);
      // Only focus the surface that's actually visible right now — focusing
      // a hidden input scrolls nothing into view but is still a stray
      // side-effect (and on mobile, would fight the (still-hidden) sheet's
      // own focus below).
      var activePair = window.matchMedia('(min-width: 1024px)').matches
        ? { input: document.getElementById('today-ask-input') }
        : { input: askInput };
      if (activePair.input) activePair.input.focus();
    }
  }

  // On desktop the Ask column is always visible — "opening" it just means
  // focusing (and optionally pre-filling) its composer, no sheet to show.
  function isDesktopAsk() {
    return window.matchMedia('(min-width: 1024px)').matches && !!document.getElementById('today-ask-input');
  }

  // ---------- Composer auto-grow (Loop Board: "Chat composer should grow
  // with your message") ----------
  // #ask-input/#today-ask-input were fixed one-line <input>s — anything
  // longer than a sentence scrolled out of view while typing. Both are now
  // <textarea>s that grow with what's typed, up to ~4-5 lines
  // (ASK_COMPOSER_MAX_HEIGHT, mirrored in shell.css's .ask-composer-input
  // max-height), then scroll internally instead of growing further. One
  // function serves both composer instances since they share markup/CSS.
  //
  // oneLineHeight() is computed from line-height + padding rather than by
  // reading the empty textarea's own scrollHeight — measured live, an EMPTY
  // textarea's scrollHeight tracks its wrapped *placeholder* text, not one
  // line of real content. The desktop Today column is narrow enough that
  // this composer's long placeholder wraps to 2-3 lines there, so an empty
  // box was measuring (and rendering) as multi-line tall — confirmed live
  // rather than assumed. Computing the one-line height from font metrics
  // instead sidesteps the placeholder entirely, and works even before the
  // element has ever been laid out (e.g. the moment its panel is built,
  // still offscreen), since it doesn't depend on scrollHeight at all.
  var ASK_COMPOSER_MAX_HEIGHT = 128; // px — keep in sync with shell.css's .ask-composer-input max-height
  function oneLineHeight(textarea) {
    var cs = getComputedStyle(textarea);
    return parseFloat(cs.lineHeight) + parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
  }
  function autoGrowAskInput(textarea) {
    if (!textarea) return;
    var bar = textarea.closest('.ask-composer-bar');
    if (!textarea.value) {
      // Nothing typed — always exactly one line, regardless of what the
      // (possibly multi-line-wrapped) placeholder would otherwise measure.
      textarea.style.height = oneLineHeight(textarea) + 'px';
      textarea.style.overflowY = 'hidden';
      if (bar) bar.classList.remove('is-grown');
      return;
    }
    textarea.style.height = 'auto'; // shrink first so scrollHeight reflects the current value, not the old height
    var next = Math.min(textarea.scrollHeight, ASK_COMPOSER_MAX_HEIGHT);
    textarea.style.height = next + 'px';
    textarea.style.overflowY = textarea.scrollHeight > ASK_COMPOSER_MAX_HEIGHT ? 'auto' : 'hidden';
    if (bar) bar.classList.toggle('is-grown', next > oneLineHeight(textarea) + 2);
  }

  function openAskSheet(prefill) {
    ensureAskSheetBuilt();
    closeWeekSheet();
    closeMealsMoreSheet();
    if (isDesktopAsk()) {
      var col = document.getElementById('today-ask-input');
      if (prefill) col.value = prefill;
      autoGrowAskInput(col);
      col.focus();
      return;
    }
    askScrim.hidden = false;
    askSheet.hidden = false;
    if (prefill) {
      askInput.value = prefill;
      autoGrowAskInput(askInput);
      askInput.focus();
    } else {
      askInput.focus();
    }
  }
  function closeAskSheet() {
    askScrim.hidden = true;
    askSheet.hidden = true;
  }

  askScrim.addEventListener('click', closeAskSheet);
  document.getElementById('ask-sheet-handle').addEventListener('click', closeAskSheet);
  // Enter-to-send is deliberately NOT wired here. #ask-input is the sheet
  // used on phone widths and on any narrower/tablet window below the
  // permanent desktop column's 1024px breakpoint (the same split this
  // shell already draws everywhere else — e.g. isDesktopAsk() above,
  // #ask-bar-dock's own breakpoint). On a touch keyboard, Enter/return
  // inserting a newline (the textarea's native, un-intercepted behavior)
  // is the least surprising choice — it's how every native mobile chat
  // text field already behaves, and the always-visible send button is the
  // one way to actually send. See setupAskColumn() below for the opposite,
  // keyboard-first choice made for the desktop column.
  askComposer.addEventListener('submit', function (e) {
    e.preventDefault();
    var message = askInput.value.trim();
    if (!message) return;
    askInput.value = '';
    autoGrowAskInput(askInput); // shrink back to one line
    sendAskMessage(message);
  });
  askInput.addEventListener('input', function () { autoGrowAskInput(askInput); });

  // ---------- Voice dictation (restored per testing feedback) ----------
  // Ported from static/index.html's mic button, which the ask-sheet's
  // Step 3 rewrite explicitly left out at the time ("a reasonable follow-
  // up but out of scope"). Two very different situations:
  // - Android Chrome / desktop Chrome expose the Web Speech API, so a mic
  //   button can drive in-page transcription directly.
  // - iOS Safari does NOT expose it at all — dictation there only exists
  //   as the mic key built into the native keyboard on any text field,
  //   which already works with zero code. The button can't trigger that
  //   programmatically, so on iOS it just focuses the input and points at
  //   the keyboard mic once.
  // Set up once per input/mic-button pair so the sheet's composer and
  // Today's permanent desktop composer (§7) each dictate independently —
  // index.html only ever had one composer to worry about, this shell has
  // two.
  var SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
  function setupDictation(input, micBtn) {
    if (!input || !micBtn) return;
    var originalPlaceholder = input.placeholder;
    var recognition = null;
    var recognizing = false;
    var dictationBaseValue = '';

    if (SpeechRecognitionCtor) {
      recognition = new SpeechRecognitionCtor();
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.lang = 'en-US';

      recognition.onresult = function (e) {
        var finalText = '';
        var interimText = '';
        for (var i = 0; i < e.results.length; i++) {
          var result = e.results[i];
          if (result.isFinal) finalText += result[0].transcript;
          else interimText += result[0].transcript;
        }
        var spoken = (finalText + interimText).trim();
        input.value = dictationBaseValue ? (dictationBaseValue + ' ' + spoken) : spoken;
        autoGrowAskInput(input); // dictation sets .value directly, which fires no 'input' event
      };
      recognition.onerror = function (e) {
        recognizing = false;
        micBtn.classList.remove('active');
        input.placeholder = originalPlaceholder;
        if (e.error === 'aborted') return; // user-initiated stop, not a real error
        var messages = {
          'not-allowed': "Microphone access is blocked for this site — check your browser's site settings (usually the icon left of the address bar) and allow the microphone, then try again.",
          'service-not-allowed': "Microphone access is blocked for this site — check your browser's site settings (usually the icon left of the address bar) and allow the microphone, then try again.",
          'audio-capture': 'No microphone found — check that one\'s connected and selected as your input device.',
          'no-speech': "Didn't catch anything — try again and speak right after tapping the mic.",
          'network': 'Voice dictation needs an internet connection — check your connection and try again.'
        };
        showToast(messages[e.error] || ('Voice dictation error: ' + e.error));
      };
      recognition.onend = function () {
        recognizing = false;
        micBtn.classList.remove('active');
        input.placeholder = originalPlaceholder;
        input.focus();
      };

      micBtn.addEventListener('click', function () {
        if (recognizing) {
          recognition.stop();
          return;
        }
        dictationBaseValue = input.value.trim();
        recognizing = true;
        micBtn.classList.add('active');
        input.placeholder = 'Listening... tap the mic again to stop';
        try {
          recognition.start();
        } catch (err) {
          recognizing = false;
          micBtn.classList.remove('active');
          input.placeholder = originalPlaceholder;
          showToast('Could not start voice dictation: ' + err.message);
        }
      });
    } else {
      micBtn.addEventListener('click', function () {
        input.focus();
        if (!micBtn.dataset.hinted) {
          showToast('Tap the microphone icon on your keyboard to dictate your message.');
          micBtn.dataset.hinted = '1';
        }
      });
    }
  }
  setupDictation(askInput, document.getElementById('ask-mic-btn'));
  autoGrowAskInput(askInput); // sets its correct one-line height immediately, in case the browser rendered rows="1" differently before this ran

  // Wires up Today's permanent desktop Ask column (§7) — same
  // ensureAskSheetBuilt/sendAskMessage the sheet uses, just a second entry
  // point. Called once per Today-panel build (buildTodayPanel), which only
  // happens once (the panel is built lazily, the first time Today is
  // shown, and reused after that) — so this never double-wires the form.
  function setupAskColumn(panel) {
    ensureAskSheetBuilt(); // populates greeting + chips into the column too, even before it's ever "opened"
    var composer = panel.querySelector('#today-ask-composer');
    var input = panel.querySelector('#today-ask-input');
    function submitTodayAsk(e) {
      if (e) e.preventDefault();
      var message = input.value.trim();
      if (!message) return;
      input.value = '';
      autoGrowAskInput(input); // shrink back to one line
      sendAskMessage(message);
    }
    composer.addEventListener('submit', submitTodayAsk);
    // Unlike the sheet's #ask-input (see the comment above its submit
    // listener), this composer is desktop-only (isDesktopAsk() gates it to
    // >=1024px, a permanent column rather than a sheet) — a keyboard-first
    // surface where Enter-to-send, Shift+Enter-for-newline is the
    // unsurprising choice (matches Slack/Discord/Linear, which this
    // permanent panel visually resembles). e.isComposing guards an IME's
    // Enter-to-confirm-a-candidate from also sending the message.
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        submitTodayAsk();
      }
    });
    input.addEventListener('input', function () { autoGrowAskInput(input); });
    setupDictation(input, panel.querySelector('#today-ask-mic-btn'));
    autoGrowAskInput(input); // sets its correct one-line height immediately, in case the browser rendered rows="1" differently before this ran
  }

  // ---------- Notifications (Phase 5 / NOTIFICATIONS.md) ----------
  // Live in-app feed, not real scheduled push — see README's Phase 5
  // notes and schema.sql's notification_dismissals comment for why.
  var notifBell = document.getElementById('notif-bell');
  var notifBadge = document.getElementById('notif-badge');
  var notifScrim = document.getElementById('notif-scrim');
  var notifPanel = document.getElementById('notif-panel');
  var notifList = document.getElementById('notif-panel-list');
  var latestNotifications = [];

  function renderNotifPanel() {
    if (!latestNotifications.length) {
      notifList.innerHTML = '<div class="notif-empty">Nothing needs your attention right now.</div>';
      return;
    }
    notifList.innerHTML = latestNotifications.map(function (n) {
      return '<div class="notif-row" data-notif-key="' + escapeHtml(n.key) + '">' +
        '<p class="notif-row-title">' + escapeHtml(n.title) + '</p>' +
        '<p class="notif-row-body">' + escapeHtml(n.body) + '</p>' +
        '<div class="notif-row-actions">' +
          '<button type="button" class="notif-row-action" data-notif-action="' + escapeHtml(n.key) + '">' + escapeHtml(n.action_label || 'View') + '</button>' +
          '<button type="button" class="notif-row-dismiss" data-notif-dismiss="' + escapeHtml(n.key) + '">Dismiss</button>' +
        '</div>' +
      '</div>';
    }).join('');
    notifList.querySelectorAll('[data-notif-action]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-notif-action');
        var n = latestNotifications.filter(function (x) { return x.key === key; })[0];
        closeNotifPanel();
        // Acting on a notification is as much a resolution as the explicit
        // Dismiss button below — it shouldn't still be sitting in the feed
        // next time the bell opens. Fire-and-forget, same as Dismiss: the
        // navigation this is about to do shouldn't wait on it.
        fetch('/api/notifications/dismiss', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: key }) }).catch(function () { /* best-effort */ });
        latestNotifications = latestNotifications.filter(function (x) { return x.key !== key; });
        notifBadge.hidden = latestNotifications.length === 0;
        if (!n) return;
        if (n.tab) activateTab(n.tab, true);
        else if (n.href) followActionHref(n.href);
      });
    });
    notifList.querySelectorAll('[data-notif-dismiss]').forEach(function (btn) {
      btn.addEventListener('click', async function () {
        var key = btn.getAttribute('data-notif-dismiss');
        try { await fetch('/api/notifications/dismiss', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: key }) }); } catch (err) { /* best-effort */ }
        await loadNotifications();
      });
    });
  }

  function openNotifPanel() {
    notifScrim.hidden = false;
    notifPanel.hidden = false;
    renderNotifPanel(); // whatever's already in hand, instantly
    loadNotifications(); // then a quiet refetch — someone else in the house may have acted on one since this loaded (loadNotifications re-renders once the panel is visible)
  }
  function closeNotifPanel() {
    notifScrim.hidden = true;
    notifPanel.hidden = true;
  }
  // The bell lives inside the app's chrome, not on top of the page. Mobile
  // chrome is the ask-bar dock (a flex sibling of #shell-scroll, so it never
  // scrolls); desktop chrome is the rail. Moving the one element between the
  // two slots keeps a single button, a single badge and a single click
  // handler — and makes overlapping scrolled content structurally impossible
  // rather than something a magic offset has to keep dodging.
  var bellIsDesktop = window.matchMedia('(min-width: 1024px)');

  function placeNotifBell() {
    if (!notifBell) return;
    if (!SHOW_NOTIF_BELL) {
      // Removed, not `hidden`: `.notif-bell`'s own `display: flex` beats the
      // attribute — a lesson this element already taught once, see
      // loadNotifications below.
      if (notifBell.parentNode) notifBell.parentNode.removeChild(notifBell);
      return;
    }
    var slot = document.getElementById(bellIsDesktop.matches ? 'bell-home-rail' : 'bell-home-dock');
    if (slot && notifBell.parentNode !== slot) slot.appendChild(notifBell);
  }

  placeNotifBell();
  // Crossing the breakpoint by resizing (or rotating a phone) re-homes it.
  if (bellIsDesktop.addEventListener) bellIsDesktop.addEventListener('change', placeNotifBell);
  else if (bellIsDesktop.addListener) bellIsDesktop.addListener(placeNotifBell);  // older WebKit

  if (notifBell) notifBell.addEventListener('click', openNotifPanel);
  if (notifScrim) notifScrim.addEventListener('click', closeNotifPanel);
  var notifPanelClose = document.getElementById('notif-panel-close');
  if (notifPanelClose) notifPanelClose.addEventListener('click', closeNotifPanel);

  async function loadNotifications() {
    // SHOW_NOTIF_BELL (2026-09-08): no bell means no way into the panel, so
    // there is nothing for this to feed — skip the request rather than
    // fetching a feed nobody can open.
    if (!SHOW_NOTIF_BELL) return;
    try {
      var res = await fetch('/api/notifications');
      if (!res.ok) throw new Error('failed');
      var data = await res.json();
      latestNotifications = data.notifications || [];
      // The bell is NOT hidden when the feed is empty. Emily's call,
      // 2026-09-02: it is the only way into the notifications panel, so
      // hiding it when there is nothing to show would make an empty panel
      // unreachable — and the panel already has an empty state that reads
      // "Nothing needs your attention right now."
      // (This line used to say `notifBell.hidden = ...`, which never worked
      // anyway: `.notif-bell`'s own `display: flex` overrode the attribute,
      // so the bell had always been permanently visible in practice. The
      // decision makes the intent and the behaviour agree.)
      // Only the unread DOT is conditional.
      notifBadge.hidden = latestNotifications.length === 0;
      if (latestNotifications.length === 0) closeNotifPanel();
      if (!notifPanel.hidden) renderNotifPanel();
    } catch (err) {
      console.warn('Notifications lookup failed:', err);
    }
  }

  // ---------- First-run onboarding check ----------
  // Runs at the top level (not inside a per-tab panel) so a first-time
  // visitor with zero household members is redirected to /onboarding
  // before any tab content renders at all.
  (async function checkOnboarding() {
    try {
      var res = await fetch('/api/onboarding/status');
      var data = await res.json();
      if (data.household && data.household.has_members === false) {
        window.location.href = '/onboarding';
        return;
      }
    } catch (err) {
      console.warn('Onboarding status check failed:', err);
    }
    activateTab(currentTabKey(), false);
    loadNotifications();
  })();

  // ---------- "Something not working?" (Emily's option a, 2026-09-08) ----------
  //
  // One box, one send, in the person's own words. No categories: a picker
  // is the app deciding in advance what can go wrong, which is the thing
  // it is worst at — and "the list looked finished but the eggs weren't on
  // it" fits no category anyone would have written down.
  //
  // Two ways in, and no third. A quiet tile on Kitchen (Kitchen has no
  // primary action by design — Nav rule / Rule 5 — so this is a tile, and
  // never an apricot button), and a small link on the error states the app
  // already shows, where the question is being asked anyway. Everything
  // below is new: the only edits to existing renderers are the tile string
  // in renderKitchen and one snwLink() append per error paragraph.
  //
  // What travels with the note is shape and nothing else — the current
  // path, and a few JS error class names. Names only, never a message: the
  // same rule static/error-reporter.js follows, for the same reason (an
  // error message in this app can carry a recipe or a member's name).
  // The server re-checks both anyway; the browser is the untrusted end.

  var SNW_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.5 12.2a7.7 7.7 0 0 1-8.3 7.7L5 20.8l1-6.4a7.7 7.7 0 1 1 14.5-2.2z"/><path d="M12 8.6v3.6"/><path d="M12 15.4h.01"/></svg>';

  var SNW_SHAPE_RE = /^[A-Za-z][A-Za-z0-9_]{0,38}(Error|Exception)$/;
  var snwShapes = [];

  function snwRecordShape(name) {
    // Class names only. Anything else — a sentence, a URL, an empty
    // reason — is dropped rather than trimmed, because there is no way to
    // tell a browser's own wording from an interpolated recipe name.
    var text = String(name || '');
    if (!SNW_SHAPE_RE.test(text)) return;
    if (snwShapes[snwShapes.length - 1] === text) return;
    snwShapes.push(text);
    if (snwShapes.length > 5) snwShapes.shift();
  }

  window.addEventListener('error', function (e) {
    snwRecordShape(e && e.error && e.error.name);
  }, true);
  window.addEventListener('unhandledrejection', function (e) {
    snwRecordShape(e && e.reason && e.reason.name);
  });

  // The Kitchen tile. Quiet on purpose, and last on the screen: it is a
  // way out of a bad moment, not a chore the household is being handed.
  function snwTile() {
    return '<button type="button" class="snw-tile" data-snw="open">' +
      '<span class="snw-tile-icon">' + SNW_ICON + '</span>' +
      '<span class="snw-tile-text">' +
        '<span class="snw-tile-title">Something not working?</span>' +
        '<span class="snw-tile-sub">Tell Emily what happened</span>' +
      '</span>' +
    '</button>';
  }

  // The in-prose link for an error state. `onSpruce` is for the one error
  // paragraph that sits inside a spruce hero, where the apricot label
  // colour has to lift off a dark ground instead of a light one.
  function snwLink(onSpruce) {
    return ' <button type="button" class="snw-link' + (onSpruce ? ' snw-link-hero' : '') +
      '" data-snw="open">Something not working? Tell Emily</button>';
  }

  var snwSheetEl = null;
  var snwScrimEl = null;

  function buildSnwSheet() {
    if (snwSheetEl) return;
    snwScrimEl = document.createElement('div');
    snwScrimEl.id = 'snw-scrim';
    snwScrimEl.hidden = true;
    snwSheetEl = document.createElement('div');
    snwSheetEl.id = 'snw-sheet';
    snwSheetEl.hidden = true;
    snwSheetEl.setAttribute('role', 'dialog');
    snwSheetEl.setAttribute('aria-modal', 'true');
    snwSheetEl.setAttribute('aria-labelledby', 'snw-title');
    snwSheetEl.innerHTML =
      '<div class="ask-sheet-handle" id="snw-handle"></div>' +
      '<div class="snw-titlerow">' +
        '<span class="snw-title" id="snw-title">Something not working?</span>' +
        '<span class="snw-hairline"></span>' +
        '<button type="button" class="kit-sheet-close" id="snw-close" aria-label="Close">&times;</button>' +
      '</div>' +
      '<div class="snw-body" id="snw-body"></div>';
    // Body level, like every other sheet here: position:fixed has to sit
    // outside the tab panel's stacking and scroll context.
    document.body.appendChild(snwScrimEl);
    document.body.appendChild(snwSheetEl);
    snwScrimEl.addEventListener('click', closeSnwSheet);
    snwSheetEl.querySelector('#snw-handle').addEventListener('click', closeSnwSheet);
    snwSheetEl.querySelector('#snw-close').addEventListener('click', closeSnwSheet);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && snwSheetEl && !snwSheetEl.hidden) closeSnwSheet();
    });
  }

  function snwFormHtml() {
    return '' +
      '<label class="snw-label" for="snw-what">What happened?</label>' +
      '<textarea id="snw-what" class="snw-input" rows="4" ' +
        'placeholder="Even half a sentence helps"></textarea>' +
      '<label class="snw-label" for="snw-trying">What were you trying to do?' +
        '<span class="snw-optional">Optional</span></label>' +
      '<textarea id="snw-trying" class="snw-input" rows="2"></textarea>' +
      '<button type="button" class="snw-send" id="snw-send" disabled>Send</button>';
  }

  function openSnwSheet() {
    buildSnwSheet();
    // One sheet at a time, the same rule the Kitchen sheets follow.
    closeAskSheet();
    closeWeekSheet();
    closeKitchenSheet();
    var body = snwSheetEl.querySelector('#snw-body');
    body.innerHTML = snwFormHtml();
    var what = body.querySelector('#snw-what');
    var send = body.querySelector('#snw-send');
    what.addEventListener('input', function () {
      send.disabled = !what.value.trim();
    });
    send.addEventListener('click', function () {
      sendSnwReport(what.value, (body.querySelector('#snw-trying') || {}).value);
    });
    snwScrimEl.hidden = false;
    snwSheetEl.hidden = false;
    what.focus();
  }

  function closeSnwSheet() {
    if (!snwSheetEl) return;
    snwScrimEl.hidden = true;
    snwSheetEl.hidden = true;
  }

  function sendSnwReport(whatHappened, tryingToDo) {
    var text = String(whatHappened || '').trim();
    if (!text) return;

    // Confirmed before the request resolves, and confirmed either way. A
    // send that failed is not worth telling someone about here: they are
    // already reporting one thing that went wrong, and "that didn't send
    // either" turns one bad moment into two — the same reason
    // /api/feedback answers 204 whatever happens to the row.
    var body = snwSheetEl.querySelector('#snw-body');
    body.innerHTML =
      '<p class="snw-done">Got it — Emily reads every one of these. ' +
      'If it&rsquo;s blocking you, text her too.</p>' +
      '<button type="button" class="snw-send" id="snw-done-close">Close</button>';
    body.querySelector('#snw-done-close').addEventListener('click', closeSnwSheet);

    try {
      fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        keepalive: true,
        body: JSON.stringify({
          what_happened: text,
          trying_to_do: String(tryingToDo || '').trim(),
          where: location.pathname,
          error_shapes: snwShapes.slice(-5)
        })
      }).catch(function () { /* see above */ });
    } catch (err) { /* see above */ }
  }

  // Delegated, so the tile and every error-state link work without any
  // renderer having to wire a listener — and so nothing here has to be
  // re-bound when a panel re-renders under it.
  document.addEventListener('click', function (e) {
    var target = e.target && e.target.closest && e.target.closest('[data-snw]');
    if (target) openSnwSheet();
  });

  // ---------- Service worker registration ----------
  // This used to live only in static/index.html, which registered it the
  // first time anyone loaded the app. The app-shell redesign moved the
  // real entry point to this file's shell.html ("/", "/week", etc. all
  // serve shell.html now — see app/main.py's index()); index.html is no
  // longer loaded by normal navigation, so a device that installs the app
  // fresh after this redesign never registers a service worker at all,
  // silently losing the offline-install behavior service-worker.js is
  // built for. Registering it here restores that for new installs. (An
  // already-installed old service worker from before this redesign stays
  // active regardless of what registers it going forward — that one gets
  // fixed by the service-worker.js content change itself, which the
  // browser detects and updates to automatically.)
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/static/service-worker.js').then(function (reg) {
        reg.update();
      }).catch(function (err) {
        console.warn('Service worker registration failed:', err);
      });
      var reloadedForNewWorker = false;
      navigator.serviceWorker.addEventListener('controllerchange', function () {
        if (reloadedForNewWorker) return;
        reloadedForNewWorker = true;
        window.location.reload();
      });
    });
  }
})();
