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
    // Kitchen is the cook's tab now (Emily, 2026-09-08), so its hint asks
    // the question a cook standing in one actually has.
    kitchen: 'What\u2019s in the fridge that needs using?',
    // Grocery's own line. One thing goes in the list's own inline add row;
    // the bar is for the wordier ask, so the hint shows it taking more than
    // one item at a time.
    grocery: 'Add oat milk and lemons\u2026',
    _default: 'The more you tell me, the less you\u2019ll swap\u2026'
  };

  function setAskHintForTab(key) {
    var hint = ASK_HINTS[key] || ASK_HINTS._default;
    var docked = document.querySelector('#ask-bar .ask-placeholder');
    if (docked) docked.textContent = hint;
  }

  var TABS = [
    { key: 'today', path: '/', label: 'Now', railLabel: 'Now', icon: ICONS.sunrise, real: true },
    { key: 'week', path: '/week', label: 'Plan', railLabel: 'Plan', icon: ICONS.plate, week: true },
    // Stage 2 slice 2: Grocery is a real shell screen now, not an embedded
    // page. static/grocery.html still exists and still works standalone, but
    // nothing links to it — it is the fallback, the same way
    // static/grocery-legacy.html already was.
    { key: 'grocery', path: '/grocery', label: 'Shop', railLabel: 'Shop', icon: ICONS.bag, grocery: true },
    // Kitchen is the COOK'S tab (Emily, 2026-09-08). It answers "what's
    // cooking, and what's in the house?": today's cooks, the prep sessions
    // that feed them, the rest of the week, and the two quiet ways into
    // Inventory and Recipes. Cook mode is a STEP of this tab — it used to
    // be a state of Meals, which meant the tab you cooked from was the tab
    // you planned from, and the two competed for the same screen.
    // Everything the tab used to hold about the household itself ("What we
    // know", "Something not working?") moved into the Preferences sheet
    // behind the header gear, which every root screen carries.
    // static/kitchen.html still exists and still works standalone but
    // nothing links to it — the fallback, exactly the treatment
    // static/grocery.html and static/grocery-legacy.html already have.
    { key: 'kitchen', path: '/kitchen', label: 'Cook', railLabel: 'Cook', icon: ICONS.pot, kitchen: true }
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

    // Cook mode's hands-free session belongs to the screen it was started
    // on — a mic still listening on a tab you have left is the worst
    // version of this feature. (A no-op while COOK_VOICE_ENABLED is off.)
    if (key !== 'kitchen') stopCookVoice();

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
    // Onboarding coaching part 1: this tab's two example prompts, for its
    // first three visits. Counted here rather than in each build*Panel,
    // because a re-visit to an already-built panel is still a visit.
    coachOnTabShown(key);

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

    // Cook mode is a step of Kitchen, and `opts.cookFocus` is how every
    // entry point into it (Today's Next up card and its move lines, Meals'
    // "Cook this", Grocery's shop-done handoff, Kitchen's own "Cooking
    // today" lines) names the ONE meal it means. It is `{entryId, date,
    // slot, title}` when the caller knows the meal, or the legacy `true`
    // for a caller that only means "tonight, whatever that turns out to
    // be" — see cookResolveFocusIndex, which never lands on a different
    // meal than the one that was tapped.
    if (tab.kitchen && opts && opts.cookFocus) kitchenEnterCook(opts.cookFocus);
    // Reaching Kitchen any OTHER way — the tab bar, the desktop rail —
    // means the recipe deep link that set an origin is over: back belongs
    // to Kitchen again (cookState.focusOrigin / openRecipeFor). The cook
    // screen is often still mounted underneath (leaving cook mode by the
    // tab bar doesn't exit it), and clearing the state without redrawing
    // left its button still reading "‹ Today" while it now lands on
    // Kitchen — fixed 2026-09-10, found by review.
    else if (tab.kitchen && cookState && cookState.focusOrigin) {
      cookState.focusOrigin = null;
      if (cookState.screen !== 'overview' && panel.dataset.built) renderCook();
    }

    // Grocery is four STEPS now (list/sort/trip/wrap — see goGroceryStep).
    // `opts.groScreen` is the old segment name the approve-week receipt's
    // grocery segment (and its toast twin, and the ask sheet's "Plan
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
    // thumb. (This used to carry a second exception for a Cook-view entry;
    // cooking is Kitchen's own step now, so entering it never activates
    // Meals at all.) weekState is declared further down this file but
    // already assigned by the time any tab click can reach here.
    if (tab.week && panel.dataset.built && weekState.data &&
        weekState.data.weekly_plan_id && weekState.data.status !== 'approved' && scrollEl) {
      scrollEl.scrollTop = 0;
    }

    var pathIsAlreadyOurs = window.location.pathname.replace(/\/+$/, '') ===
      (tab.path === '/' ? '/' : tab.path.replace(/\/+$/, ''));
    // `opts.replaceHistory` moves the CURRENT entry to this tab instead of
    // adding one. Cook mode's back link is the caller (fixed 2026-09-10,
    // found by review): it was pushing here and pushing again in
    // goMealsStep, so one press of "‹ Thursday" grew history by two, and
    // the following back gesture skipped the Day step and surfaced a state
    // nobody had visited. Leaving cook mode is stepping back UP a level,
    // so the entry that said "kitchen" should now say where you actually
    // are — not sit behind a new one.
    if (opts && opts.replaceHistory) {
      if (!pathIsAlreadyOurs) window.history.replaceState({ tab: key }, '', tab.path);
    } else if (pushHistory && !pathIsAlreadyOurs) {
      window.history.pushState({ tab: key }, '', tab.path);
    }
  }

  // One listener decides what Back means. Meals' three steps and Grocery's
  // four push their own history entries at the same /week and /grocery paths
  // (pushMealsStepHistory / pushGroceryStepHistory), so the Android/browser
  // back gesture steps out one level there before it leaves the tab at all;
  // every other tab is unaffected.
  //
  // The ask sheet (openAskSheet/closeAskSheet below) cooperates the same
  // way: opening it on mobile/tablet pushes one entry with askSheet:true at
  // the same path. If that entry is the one we're leaving (askSheetHistoryPushed
  // is still set, and the state we're arriving at isn't itself one of
  // those), this is the back gesture asking to close the sheet, not to
  // change tabs or steps — closeAskSheet() handles it and nothing else
  // below runs, since the URL never actually changed.
  window.addEventListener('popstate', function (e) {
    if (askSheetHistoryPushed && !(e && e.state && e.state.askSheet)) {
      closeAskSheet();
      return;
    }
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
            // Every root screen carries the Preferences gear in its header
            // (see prefsGearHtml). Today has no deeper step, so it is never
            // hidden here.
            prefsGearHtml() +
          '</div>' +
          '<h1 class="today-greeting">Now</h1>' +
          '<div class="today-progress" id="today-progress"></div>' +
        '</div>' +
        // The offer to plan a week. Outside .today-body, not inside it:
        // .today-body is a named-area grid on desktop, and an area whose
        // only child is display:none still leaves its row's gap behind.
        '<div id="plan-week-nudge" class="today-area-nudge"></div>' +
        // Onboarding coaching, part 2 (2026-09-08): the one-time "This is
        // how to talk to me" card. Outside .today-body for the same reason
        // the nudge above it is — see that comment. Empty (and so
        // display:none) on every load but the one it is shown on.
        // The hero, and the only one on this screen — a direct child of
        // .today-content rather than of .today-body, because it bleeds the
        // full width of the panel while everything else sits inside the
        // 20px gutter, and a grid child cannot escape its parent's padding.
        '<div id="today-next-up" class="dinner-hero nextup-hero" hidden></div>' +
        // Onboarding coaching, part 2 (2026-09-08): the one-time "This is
        // how to talk to me" card. BELOW the next-up card, never above it —
        // the day's one action stays on the first screen even on a short
        // phone (verifier, 2026-09-09). Empty (and so display:none) on
        // every load but the one it is shown on.
        '<div id="coach-card-slot" class="today-area-nudge"></div>' +
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
            // Coaching part 1 + part 3 on desktop: the per-tab example
            // prompts sit above the Ask column's input, with the same
            // "?" into Helpful tips the mobile dock carries.
            '<div class="ask-examples-row">' +
              '<div class="ask-chips ask-examples" id="today-ask-examples" hidden></div>' +
              '<button type="button" class="ask-tips-btn" data-tips="open" aria-label="Helpful tips" title="Helpful tips">?</button>' +
            '</div>' +
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

    // The how-and-why card's slot only exists once this panel has been
    // built, and /api/coaching may well have answered before that — so the
    // card is rendered from both ends, and renderCoachCard is idempotent.
    renderCoachCard();
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
          '<div class="plan-nudge-body">Of course. It’ll be waiting for you under Plan — I won’t ask again this week.</div>' +
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

  // The cookFocus payload that opens this move's recipe, or null when
  // there is no recipe behind it. Only a COOK has one: a reheat is a line
  // and never a way into a recipe (Kitchen's own rows follow the same
  // rule), and a fridge move, a prep task or a shop run names no dish at
  // all. moves.py already writes the payload for a cook's action target,
  // so this reads it rather than building a second one that could drift;
  // the fallback covers a payload cached before that field existed.
  function moveRecipeTarget(move) {
    if (!move || move.kind !== 'cook') return null;
    var target = (move.action && move.action.target) || {};
    if (target.cookFocus) return target.cookFocus;
    if (move.entry_id == null) return null;
    return {
      entryId: move.entry_id,
      date: move.date || null,
      slot: move.slot || null,
      title: move.title || ''
    };
  }

  // Emily's rule (2026-09-09): a dish name is a link to its recipe. The
  // name keeps its own class and every pixel of its own type — .dish-link
  // only takes the browser's button furniture off and puts a 44px tap
  // target under it.
  function moveDishHtml(move, cls) {
    var name = escapeHtml(move.title);
    if (!moveRecipeTarget(move)) return '<span class="' + cls + '">' + name + '</span>';
    return '<button type="button" class="' + cls + ' dish-link" data-move-dish="' +
      escapeHtml(move.id) + '">' + name + '</button>';
  }

  function nextUpCardHtml(move) {
    var chips = move.chips || [];
    return '<div class="hero-top">' +
        '<span class="hero-eyebrow">NEXT UP</span>' +
        '<span class="hero-rule"></span>' +
        (move.time_label ? '<span class="nextup-when">' + escapeHtml(move.time_label) + '</span>' : '') +
      '</div>' +
      moveDishHtml(move, 'hero-dish nextup-dish' + dishSizeClass(move.title)) +
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
    var detail = move.detail
      ? '<span class="rest-row-detail">' + escapeHtml(move.detail) + '</span>'
      : '';
    var text = '<span class="rest-row-title">' + escapeHtml(move.title) + '</span>' + detail;
    // A done row has nothing left to DO, so the whole row stops being the
    // move's own button — the tick is the only control on it, and it
    // undoes. The dish still has a recipe, though, and a cooked dinner is
    // exactly the name someone taps wanting to see what went into it
    // (Emily, 2026-09-09), so the NAME goes on being a link even here.
    var doneText = moveRecipeTarget(move)
      ? '<span class="rest-row-text">' +
          '<button type="button" class="rest-row-title dish-link" data-move-dish="' +
            escapeHtml(move.id) + '">' + escapeHtml(move.title) + '</button>' + detail +
        '</span>'
      : '<span class="rest-row-text">' + text + '</span>';
    return '<div class="rest-row' + (move.done ? ' is-done' : '') + '">' +
      (move.done
        ? doneText
        : '<button type="button" class="rest-row-text rest-row-open" data-move-action="' + escapeHtml(move.id) + '">' + text + '</button>') +
      moveTickHtml(move) +
    '</div>';
  }

  function tomorrowCardHtml(move) {
    return '<div class="shell-card tomorrow-card">' +
      '<div class="tomorrow-eyebrow">TOMORROW</div>' +
      '<div class="tomorrow-lead">That&rsquo;s today handled. Tomorrow starts with</div>' +
      moveDishHtml(move, 'tomorrow-title') +
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
    // The dish name itself, wherever Today prints one — the Next up card's
    // headline, a done row, tomorrow's first move. Straight to the recipe,
    // never to the row's own action: tapping a name is "show me this", not
    // "do this to it".
    panel.querySelectorAll('[data-move-dish]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var move = todayMoveById(panel, btn.getAttribute('data-move-dish'));
        if (move) openRecipeFor(moveRecipeTarget(move), { label: 'Now', tab: 'today' });
      });
    });
  }

  // Every move Today has on screen, tomorrow's included — the tomorrow
  // card is drawn from data.tomorrow, which is not in the day's own list.
  function todayMoveById(panel, id) {
    var data = panel._moves || {};
    var hit = (data.moves || []).filter(function (m) { return m.id === id; })[0];
    if (hit) return hit;
    return (data.tomorrow && data.tomorrow.id === id) ? data.tomorrow : null;
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
      refreshKitchenPanel();
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
    // A cook or a reheat opens cook mode on Kitchen, on the exact meal the
    // move names (moves.py writes that payload).
    //
    // Through openRecipeFor, like every other way into cook mode (fixed
    // 2026-09-10, found by review). Calling activateTab straight left
    // whatever origin an earlier deep link had set standing, so a cook
    // opened from Today could come back saying "‹ Thursday" and drop the
    // household on Meals — and even with no stale state it made the screen
    // disagree with itself: this row's own dish name already said
    // "‹ Today" while its action button said "‹ Kitchen", two answers to
    // one meal 200px apart.
    if (target.tab === 'kitchen' && target.cookFocus) {
      return openRecipeFor(target.cookFocus, { label: 'Now', tab: 'today' });
    }
    // The same thing, in the shape moves.py wrote it before 2026-09-08,
    // when cook mode was a state of the Meals tab. A payload cached by the
    // service worker (or held on a tab left open across the deploy) still
    // carries it, and the tab it names has no cook state any more — so
    // translate it rather than dropping the tap on the plan, where it
    // would silently do nothing.
    if (target.tab === 'week' && target['mealsView'] === 'cook') {
      return openRecipeFor(target['mealsFocus'] || true, { label: 'Now', tab: 'today' });
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
  // What the three-segment version had and this doesn't: the To buy / Plan
  // stops / Review segmented control, the spruce trip hero, the Done group
  // on To buy, and Review's missing-quantity and no-store flag cards — all
  // questions the four steps already answer. Review's CONFIRMATION half
  // ("Already sorted this week", with its two undos) is kept and is now part
  // of WRAP UP, and its duplicate flag is kept as one quiet line at the top
  // of LIST (groDuplicatesHtml).
  //
  // Three things went out with the segments and came BACK, because "the ask
  // bar can do it" is not the same as "a person should have to spend a model
  // turn on it": the per-row ⋯ (groRowMenuHtml — quantity, store, remove, on
  // the routes the old menu used), the inline add row in LIST's foot
  // (groAddItem, one POST to /api/grocery-list/add), and the duplicate line
  // above. The ask bar is still the escape hatch for anything wordier — that
  // is what it is good at — but it is no longer the ONLY way to fix a row.
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
    basket: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 9.5h14V19a1.8 1.8 0 0 1-1.8 1.8H6.8A1.8 1.8 0 0 1 5 19z"/><path d="M3.5 5.5h17v4h-17z"/><path d="M12 9.5v11"/></svg>',
    dots: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5.5" r="0.6"/><circle cx="12" cy="12" r="0.6"/><circle cx="12" cy="18.5" r="0.6"/></svg>',
    camera: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5h4l1.5-2.5h6L16.5 8.5h4V19a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19z"/><circle cx="12" cy="13.5" r="3.4"/></svg>'
  };

  // How many things a store card shows before "+ N more".
  var GRO_CARD_PEEK = 4;

  // The listExpanded key for the no-store section. Not a store name, and it
  // must never collide with one: a household really could shop somewhere
  // called "Everything", and the two cards would then share an expanded
  // state. The angle brackets are illegal in a store name the UI accepts.
  var GRO_LOOSE_KEY = '<no-store>';

  // Above this many things to sort, the tab offers the fast paths first
  // instead of dropping the household straight into the one-at-a-time queue.
  // Emily, 2026-09-09: "if there's 40 ingredients it can take too long to go
  // through the screens all like this." Five is the handful she likes the
  // queue for — three stray items are finished before a menu of ways to
  // finish them could be read. Six is where the menu starts earning its own
  // screen.
  var GRO_FAST_SORT_MIN = 6;

  // The three screens that only exist while something is unsorted. Named as
  // a set because they share one fallback: the moment the queue empties —
  // by any route, including a bulk assign — all three stop making sense and
  // drop back to LIST.
  var GRO_SORT_STEPS = ['sort', 'sorthow', 'sortall'];

  // How long an Undo chip stays beside its toast. The tab's own remove-undo
  // and Meals' swap-undo both sit around eight seconds; a bulk assign is the
  // biggest single write in this tab, so it gets the same window rather than
  // showToast's shorter default.
  var GRO_UNDO_MS = 8000;

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
    // card has been answered — persisted server-side
    // (meal_preferences.stores_prompt_dismissed_at) so it stays gone across
    // visits, not just this page view. Any answer closes it, including a
    // list of shops: the card is the question, not the shops.
    storesPromptDismissed: false,
    // Whether the household is part-way through ANSWERING that card. The
    // flag above is enough to know the question is settled; it is not
    // enough to keep the card on screen while it is being answered, because
    // saving the first shop makes usualStores non-empty and the card's own
    // condition would go false under the hand still tapping it. Set by the
    // first tap, cleared only by the button at the foot of the card, and
    // mirrored into localStorage so a reload part-way through resumes the
    // question instead of ending it — see storesPromptOpenKey.
    storesPromptOpen: false,
    itemStorePrefs: {},     // lowercased item name -> remembered store
    preShopFlags: [],
    preShopOpen: false,
    preShopExpanded: false,
    // Staples (app/tools/staples.py): what the household buys on a rhythm.
    // Loaded with the list, shown as one quiet card at the foot of LIST when
    // there are any, closed by default. A due one is not in here twice — it
    // is an ordinary line in its section carrying staple_id, rendered as a
    // suggestion by groListRowHtml.
    staples: [],
    staplesOpen: false,
    alreadyHaveSummary: { already_have: [], elsewhere: [] },  // WRAP UP's confirmation
    listExpanded: {},       // store name -> bool: "+ N more" tapped on LIST
    // The one LIST row whose ⋯ menu is open, as a string id, or null. One at
    // a time on purpose — the old per-row menu worked the same way, and two
    // open editors on a phone list is two places a half-typed quantity can
    // be lost.
    openRowId: null,
    inCartOpen: false,      // "In your cart · N" group on TRIP
    // How many things SORT set out to sort, so the progress line can say
    // "2 of 3" rather than counting down from a number nobody saw.
    sortTotal: 0,
    // SORT ALL's staged answers: item id -> store name, '' meaning "no
    // particular shop". Staged rather than written per tap, so changing a
    // row on a forty-row screen costs no request and cannot move anything
    // under the thumb — see groSortAllHtml. Nothing is saved until the
    // button at the foot, which sends all forty in one call.
    sortAllPicks: {},
    // What the last bulk assign overwrote: [{item_id, store, decided}] as
    // the rows were BEFORE it ran, so Undo restores each one exactly rather
    // than dumping the lot back into the to-sort queue. Cleared by the undo
    // itself and replaced by the next bulk assign; the toast timing out
    // leaves it sitting here, which is harmless because the only thing that
    // reads it is the chip inside that toast.
    bulkUndo: null,
    // The trip, snapshotted at "Start the trip" so finishing a stop can't
    // renumber the ones behind it: an ordered list of store names, plus
    // where we are in it. Null between trips.
    tripStops: null,
    tripIndex: 0,
    tripTotal: 0,           // things needed when the trip began
    tripBought: 0,          // things actually committed, this trip only
    // Stops finished on this trip, by name. Finishing a stop no longer
    // starts the next one on its own — the household says where it is
    // actually driving next (the WHERE NEXT step) — so something has to
    // remember which shops are behind us. Keyed by name rather than by
    // index because the order stops are VISITED is now the household's
    // choice, while tripStops stays the snapshot it always was.
    tripDone: {},
    // The stop most recently finished, so WHERE NEXT's back link can reopen
    // it. Only ever the last one: "back" means one step, not a history of
    // the trip.
    tripLastDone: null,
    // WRAP UP: ids the shopper said "Couldn't find it" about. There is no
    // note column on grocery_items (see app/schema.sql), so this keeps the
    // row exactly as it is — needed — and only records that it has been
    // answered, so the wrap-up list doesn't keep asking.
    wrapKept: {},
    openFlagKey: null,
    voiceSession: null,
    voiceLog: [],
    // Page-view only — "I'll come back to
    // it" on the shop-done handoff (Plan stops, everything bought) just
    // collapses the offer for this visit, no persistence.
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
    justFinishedTrip: false,
    // Whether the last thing this screen tried to reach the server with
    // didn't get there. True means the list on screen is the phone's own
    // copy (see static/grocery-offline.js) and any ticks since are queued.
    // Cleared the moment a request gets through, so a shopper who was
    // never offline never sees the line this drives.
    offline: false
  };

  var GRO_PS_CAP = 5;

  // ---------- No signal ----------
  // The list's copy on the phone and the ticks made without signal, keyed
  // per household. The module is DOM-free and fetch-free on purpose (it is
  // what tests/test_grocery_offline.py runs under node); everything below
  // is the wiring. Emily, 2026-09-10: a list that goes blank in aisle four
  // fails at the exact moment it is needed, so the copy is what the screen
  // shows whenever the network can't answer, and a tick never waits for
  // the network at all.
  var groOffline = (function () {
    var storage = null;
    try { storage = window.localStorage; } catch (err) { /* private mode: page-view memory only */ }
    return window.PomonaGroceryOffline
      ? window.PomonaGroceryOffline.create({ storage: storage })
      : null;
  })();
  // What the status line says. Two lines, both calm, both with the way
  // out in the same breath (DESIGN_SYSTEM §8) — Emily may reword either.
  var GRO_OFFLINE_LINE = "No signal — I’ll save your ticks when you’re back.";
  var GRO_CATCHING_UP_LINE = "Saving your ticks…";
  var GRO_CAUGHT_UP_TOAST = "Back online — your ticks are saved.";
  // Ticks the server refused on replay: the row is gone (a partner removed
  // it, or bought it and finished the stop) and the fresh list is the truth.
  function groTicksDroppedToast(n) {
    return (n === 1 ? "One tick" : n + " ticks") + " couldn’t be saved — the list is up to date now.";
  }
  // No signal and no copy to show (this device hasn't heard which household
  // is signed in, or was just signed out): a calm empty state, not an error.
  var GRO_NO_COPY_LINE = "No signal — I’ll show your list as soon as you’re back.";
  // Something other than a tick, asked for with no signal. The old line
  // ("try again") is wrong advice in a dead zone.
  var GRO_NO_SIGNAL_TOAST = "No signal — try that once you’re back.";

  function groHasPending() { return !!(groOffline && groOffline.hasPending()); }
  // "fetch threw" is the network; a status code is the server answering.
  function groIsNetworkError(err) {
    return !!err && err.name === 'TypeError';
  }
  function groSetOffline(on) {
    if (groceryState.offline === on) return;
    groceryState.offline = on;
    renderGroceryOfflineLine();
  }

  // A POST answering { ok, status } when the server replied and rejecting
  // only when it never did — the distinction the queue is built on. This
  // (url, body) shape is what groOffline.replay is handed.
  function groPostJson(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (res) {
      if (res.status === 401) groForgetOffline();
      return { ok: res.ok, status: res.status };
    });
  }
  // Sign-out, or the server saying the session is over (401): nothing of
  // the grocery copy or queue outlives it on this device.
  function groForgetOffline() {
    if (groOffline) groOffline.forget();
    groceryState.offline = false;
  }
  function groPostStatus(id, status) {
    return groPostJson('/api/grocery-list/' + id + '/status', { status: status });
  }

  // A tick: on the screen now, on the server when it can be. The local copy
  // is changed first, then the request goes out — with no signal it is
  // queued instead, and it is queued too whenever something older is still
  // waiting, so the order the shopper made them in is the order they land.
  function groTick(id, status) {
    if (groceryState.data && groOffline) {
      groOffline.applyStatus(groceryState.data, id, status);
      renderGrocery();
    }
    if (!groOffline) {
      // No module at all (a script that failed to load): the old path.
      groDo(function () { return groPost('/api/grocery-list/' + id + '/status', { status: status }); },
        status === 'needed' ? "Couldn't put that back — try again." : "Couldn't update that — try again.");
      return;
    }
    if (navigator.onLine === false || groHasPending()) {
      groOffline.queueStatus(id, status);
      if (navigator.onLine === false) groSetOffline(true);
      else renderGroceryOfflineLine();
      groReplayQueue();
      return;
    }
    groPostStatus(id, status).then(function (res) {
      if (!res.ok) {
        // The server said no (the row is gone, most likely) — the fresh
        // list is the answer, same as any refused write.
        showToast(status === 'needed' ? "Couldn't put that back — try again." : "Couldn't update that — try again.");
      }
      groSetOffline(false);
      return loadGrocery();
    }, function (err) {
      if (!groIsNetworkError(err)) console.warn('Grocery tick failed:', err);
      groOffline.queueStatus(id, status);
      groSetOffline(true);
    });
  }

  // Send what is waiting, oldest first. Called when the browser says it is
  // back online, when the tab loads, on every tick and refresh, and on a
  // slow timer while anything is queued — a store's wifi that "connects"
  // without reaching anything never fires the online event.
  var groReplayTimer = null;
  function groReplayQueue() {
    if (!groOffline || !groHasPending()) {
      if (groReplayTimer) { clearInterval(groReplayTimer); groReplayTimer = null; }
      return Promise.resolve(null);
    }
    if (!groReplayTimer) groReplayTimer = setInterval(groReplayQueue, 30000);
    renderGroceryOfflineLine();
    return groOffline.replay(groPostJson).then(function (result) {
      if (!result) return result;
      if (result.kept > 0) {
        groSetOffline(true);
        return result;
      }
      groSetOffline(false);
      if (groReplayTimer) { clearInterval(groReplayTimer); groReplayTimer = null; }
      if (result.dropped > 0) showToast(groTicksDroppedToast(result.dropped));
      else if (result.sent > 0) showToast(GRO_CAUGHT_UP_TOAST);
      renderGroceryOfflineLine();
      if (result.sent > 0 || result.dropped > 0) return loadGrocery().then(function () { return result; });
      return result;
    });
  }

  // The one line this adds to the screen, under the head. Nothing at all
  // when online with nothing waiting — a shopper who never loses signal
  // never sees it.
  function renderGroceryOfflineLine() {
    var panel = groPanel();
    var line = panel && panel.querySelector('#gro-offline');
    if (!line) return;
    var text = '';
    if (groceryState.offline) text = GRO_OFFLINE_LINE;
    else if (groHasPending()) text = GRO_CATCHING_UP_LINE;
    line.textContent = text;
    line.hidden = !text;
  }

  // Wired once, when the Shop screen is built (buildGroceryPanel): the
  // browser's own word on the connection, for the moment a bar comes back.
  var groConnectivityWired = false;
  function groWireConnectivity() {
    if (groConnectivityWired || !groOffline) return;
    groConnectivityWired = true;
    window.addEventListener('online', function () {
      if (!groIsBuilt()) return;
      groReplayQueue().then(function () {
        // Nothing was queued, but the list on screen may still be the copy
        // — or the no-signal wait, with no copy at all.
        if (groceryState.offline || groceryState.loadError === 'no-signal') loadGrocery();
      });
    });
    window.addEventListener('offline', function () {
      if (groIsBuilt()) groSetOffline(true);
    });
  }

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
    if (results.some(function (r) { return r.status === 401; })) groForgetOffline();
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
  // Whether "where does this go?" is a question at all. It needs more than
  // one possible answer: a household that named no shop, or exactly one, is
  // being asked to choose between one option and itself. Emily,
  // 2026-09-09 — a one-shop household must never see a sorting step. Below,
  // groUnsorted returns nothing for them, which takes the TO SORT badge, the
  // whole step and its fast paths off the tab in one place rather than in
  // six.
  function groCanSort(data) { return groPillStores(data).length > 1; }
  // The one shop, when there is exactly one. Their list never gets tagged to
  // it (there was no question to answer), so several things below have to
  // read "no store" as "that shop" — otherwise the household's whole list
  // sits in a bucket with no stop attached and the trip can never start.
  function groSoleStore(data) {
    var shops = groPillStores(data);
    return shops.length === 1 ? shops[0] : null;
  }
  // Whether a person has answered where this row goes. Persisted now
  // (grocery_items.store_decided) rather than held in a page-view map: an
  // "Any" answer writes store '', which is byte-identical on the wire to
  // never having been asked, so before this column a reload put every
  // skipped item straight back into the queue.
  function groItemDecided(it) { return !!(it && it.store_decided); }
  function groUnsorted(data) {
    if (!groCanSort(data)) return [];
    var u = data.stores['Unassigned'];
    if (!u) return [];
    return groStoreItems(u).filter(function (it) { return !groItemDecided(it); });
  }
  // Everything in the Unassigned bucket, whichever half of the split above
  // it fell into. LIST needs the union rather than either half: a household
  // with no stops has to be able to SEE its list, and to that household the
  // difference between "not sorted yet" and "sorted as Any" is invisible and
  // uninteresting — both mean "a thing I need to buy, no store attached".
  function groLooseItems(data) {
    var u = data.stores['Unassigned'];
    return u ? groStoreItems(u) : [];
  }
  // Things with no shop of their own, which therefore ride along with
  // whichever stop is being shopped. Normally that is the ones answered
  // "Any"; a one-shop household answered nothing, so for them it is the
  // whole loose pile.
  function groRideAlongItems(data) {
    if (groSoleStore(data)) return groLooseItems(data);
    var u = data.stores['Unassigned'];
    if (!u) return [];
    return groStoreItems(u).filter(groItemDecided);
  }
  // What a store's card and its count actually cover. For the one-shop
  // household that is its own rows plus the loose pile, which has nowhere
  // else it could be bought.
  function groStoreCardItems(data, name) {
    var s = data.stores[name];
    var items = s ? groStoreItems(s) : [];
    if (groSoleStore(data) === name) items = items.concat(groLooseItems(data));
    return items;
  }
  function groStoresWithNeeded(data) {
    var names = Object.keys(data.stores).filter(function (n) {
      return n !== 'Unassigned' && groNeededCount(data.stores[n]) > 0;
    });
    // Nothing tagged to any shop, but things on the list that still have to
    // be bought somewhere: without a stop there is no "Start the trip" and
    // the tab is a dead end. Two households land here — one that named a
    // single shop (its list was never tagged because there was no question
    // to answer) and one that answered "Anywhere" to everything. Both get
    // the shop they actually buy from most, which is the same defensible
    // default "Put all 40 at Loblaws" already offers rather than a second
    // invention; for the one-shop household it IS their one shop.
    // Read off the RIDE-ALONGS, not the whole loose pile: something still
    // waiting in the to-sort queue is not homeless, it is unasked, and
    // inventing a stop for it would answer the household's question for
    // them and take the TO SORT badge off the screen.
    if (!names.length && groRideAlongItems(data).length) {
      var fallback = groMostUsedStore(data);
      if (fallback) names.push(fallback);
    }
    return groOrderStores(names);
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
      // "Try again" is the wrong advice with no signal: only ticks queue,
      // everything else waits for a bar (see groTick).
      var noSignal = groIsNetworkError(err) || navigator.onLine === false;
      showToast(noSignal ? GRO_NO_SIGNAL_TOAST : (failureMessage || "That didn't save — try again."));
      await loadGrocery();
      return false;
    }
  }

  // ---------- Build ----------
  function buildGroceryPanel(panel) {
    panel.innerHTML =
      '<div class="grocery-content">' +
        '<button type="button" class="crumb" id="gro-back" data-gro="step-back" hidden></button>' +
        '<div class="gro-head">' +
          '<div class="gro-head-row">' +
            '<h1 class="gro-title" id="gro-title">Shop</h1>' +
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
            // The Preferences gear, in the header like every other root
            // screen's. Hidden on the deeper steps (renderGrocery).
            prefsGearHtml() +
          '</div>' +
          '<div class="gro-sub" id="gro-sub" hidden></div>' +
        '</div>' +
        // The no-signal line (renderGroceryOfflineLine). Hidden unless the
        // list on screen is the phone's copy or a tick is still waiting.
        '<p class="gro-offline" id="gro-offline" hidden></p>' +
        '<div class="gro-voice" id="gro-voice" hidden></div>' +
        '<div class="gro-body" id="gro-body"><p class="gro-empty">Loading&hellip;</p></div>' +
        '<div class="gro-body gro-foot" id="gro-foot"></div>' +
        // The step's one action, in the dock (rule 2) — last in the markup
        // because that is where a sticky footer's flow position has to be.
        '<div class="dock gro-dock" id="gro-dock"></div>' +
      '</div>';

    // One delegated listener for the whole screen. The alternative — re-wiring
    // every row after every render, which is what the page this replaces did —
    // is where a missed handler hides.
    panel.addEventListener('click', onGroceryClick);
    // Enter in either add field adds the item, so the list can be filled
    // without moving a hand to the button.
    panel.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      if (e.target.id === 'gro-add-item' || e.target.id === 'gro-add-qty') {
        e.preventDefault();
        groAddItem();
      }
      if (e.target.id === 'gro-stores-prompt-input') {
        e.preventDefault();
        panel.querySelector('[data-gro="stores-prompt-add"]').click();
      }
    });

    groWireConnectivity();
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
      // The other half of the same answer: whether this household was
      // part-way through picking its shops when the page last went away.
      groceryState.storesPromptOpen = readStoresPromptOpen();
      // Kept with the list's copy: with no signal this request fails, and
      // an empty answer here would put the "where do you shop?" card up
      // over the list and take "Start the trip" away — see grocery-offline.js.
      if (groOffline) groOffline.saveShops({ usualStores: groceryState.usualStores, dismissed: groceryState.storesPromptDismissed });
      renderGrocery();
    } catch (err) {
      // No signal: the last answer stands. Anything else, sorting still
      // works from what's tagged on the list.
      var shops = groOffline && groIsNetworkError(err) ? groOffline.readShops() : null;
      if (shops) {
        groceryState.usualStores = shops.usualStores;
        groceryState.storesPromptDismissed = shops.dismissed;
        groceryState.storesPromptOpen = readStoresPromptOpen();
        renderGrocery();
      }
    }
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

  async function groLoadStaples() {
    try {
      var res = await fetch('/api/staples');
      if (!res.ok) { groceryState.staples = []; return; }
      groceryState.staples = (await res.json()).staples || [];
    } catch (err) { groceryState.staples = []; }
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
      var pair = await Promise.all([groLoadAllData(), groLoadPreShopFlags(), groLoadAlreadyHaveSummary(), groLoadStaples()]);
      // The server's answer is the copy; what the screen shows is that plus
      // any ticks still waiting to be sent, so a tick made a moment ago in
      // a dead zone doesn't vanish the instant one bar comes back.
      if (groOffline) {
        groOffline.saveList(pair[0]);
        groceryState.data = groHasPending() ? groOffline.applyPending(pair[0]) : pair[0];
      } else {
        groceryState.data = pair[0];
      }
      groceryState.loadError = false;
      groSetOffline(false);
    } catch (err) {
      var copy = groOffline && groIsNetworkError(err) ? groOffline.readList() : null;
      if (copy) {
        // No signal, but the list is still here — the phone's own copy with
        // this device's unsent ticks on top. The step the shopper was on
        // stays too: nothing about the screen changes except the line
        // under the head.
        groceryState.data = groOffline.applyPending(copy.data);
        groceryState.loadError = false;
        groSetOffline(true);
      } else if (groOffline && groIsNetworkError(err)) {
        // No signal and nothing this device may show: it doesn't know which
        // household is signed in (nothing since sign-out, or the server
        // hasn't answered yet this session). Not an error — a wait.
        groceryState.data = null;
        groceryState.loadError = 'no-signal';
      } else {
        console.warn('Grocery list lookup failed:', err);
        groceryState.loadError = true;
      }
    }
    renderGrocery();
    if (!groceryState.loadError) groReplayQueue();
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
    // A row's ⋯ belongs to the LIST you opened it on, not to the next step.
    groceryState.openRowId = null;
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
  // grocery segment, its toast twin, and the ask sheet's "Plan my stops"
  // chip (all `activateTab('grocery', true, { groScreen: 'plan' })`).
  // They all mean "open the list", and the list is where they land, always:
  // an unsorted item is not a reason to drop somebody into a one-at-a-time
  // queue they didn't ask for. The TO SORT badge on LIST is how you get to
  // SORT, and it is right there in the head when there is anything to sort.
  function groSetScreen(screen) {
    goGroceryStep('list');
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
    var dock = panel.querySelector('#gro-dock');
    if (!back || !title || !badge || !sub || !body || !foot || !dock) return;

    // Re-rendering replaces the list under the reader's thumb, so hold the
    // scroll position across it. "Nothing else moves, ever."
    var keepScroll = scrollEl ? scrollEl.scrollTop : 0;

    renderGroceryOfflineLine();
    if (groceryState.loadError || !groceryState.data) {
      back.hidden = true;
      badge.hidden = true;
      sub.hidden = true;
      title.textContent = 'Shop';
      body.innerHTML = groceryState.loadError === 'no-signal'
        ? '<p class="gro-empty">' + escapeHtml(GRO_NO_COPY_LINE) + '</p>'
        : groceryState.loadError
        ? '<p class="gro-error">Couldn\'t load the grocery list right now — try the refresh button above.' + snwLink() + '</p>'
        : '<p class="gro-empty">Loading&hellip;</p>';
      foot.innerHTML = '';
      dock.innerHTML = '';
      return;
    }

    var data = groceryState.data;
    // A step that stopped making sense under its own feet falls back to the
    // root rather than rendering a screen about nothing: SORT with nothing
    // left to sort, a trip whose stops were never snapshotted.
    if (GRO_SORT_STEPS.indexOf(groceryState.step) !== -1 && !groUnsorted(data).length) {
      groceryState.step = 'list';
    }
    if ((groceryState.step === 'trip' || groceryState.step === 'wrap' || groceryState.step === 'next') &&
        !groceryState.tripStops) {
      groceryState.step = 'list';
    }
    // Every stop is behind us: there is nothing to choose between, so the
    // question is the wrap-up rather than "where next?".
    if (groceryState.step === 'next' && !groRemainingStops(data).length) groceryState.step = 'wrap';
    var step = groceryState.step;

    var head = groHeadFor(data, step);
    back.hidden = !head.back;
    if (head.back) back.textContent = head.back;
    title.textContent = head.title;
    sub.hidden = !head.sub;
    if (head.sub) sub.textContent = head.sub;

    // The gear belongs to the root only, like the badge below.
    var groGear = panel.querySelector('.prefs-gear');
    if (groGear) groGear.hidden = step !== 'list';
    // The badge belongs to LIST — on the deeper steps it would be a second
    // way out of a screen that already has one.
    var unsorted = groUnsorted(data).length;
    // groUnsorted already answers "is there anything to sort, and is sorting
    // even a question here" (groCanSort): a household with one shop or none
    // gets nothing back from it, so the badge, the step and its fast paths
    // all go quiet together. The one thing left to check here is that the
    // shops question itself isn't still on screen underneath.
    var showBadge = step === 'list' && unsorted > 0 && !groStoresPromptShouldShow();
    badge.hidden = !showBadge;
    if (showBadge) {
      badge.textContent = unsorted + ' TO SORT';
      badge.setAttribute('aria-label', groPlural(unsorted, 'thing', 'things') + ' to sort');
    }

    // The body holds a live input too while the shops card is up — its
    // "Somewhere else?" field — and since a tapped chip re-renders the card
    // straight away, an unrelated re-render is no longer a rare event. Same
    // rule as the foot's add row below.
    var storesTyped = groCaptureStoresPromptInput(body);
    if (step === 'sort') body.innerHTML = groSortHtml(data);
    else if (step === 'sorthow') body.innerHTML = groSortHowHtml(data);
    else if (step === 'sortall') body.innerHTML = groSortAllHtml(data);
    else if (step === 'next') body.innerHTML = groNextHtml(data);
    else if (step === 'trip') body.innerHTML = groTripHtml(data);
    else if (step === 'wrap') body.innerHTML = groWrapHtml(data);
    else body.innerHTML = groListHtml(data);
    groRestoreStoresPromptInput(body, storesTyped);

    // LIST's foot holds a live input. A re-render it didn't ask for — the
    // usual-stores fetch landing, another tab pushing a refresh — must not
    // eat a half-typed "oat milk", or the focus that was sitting in it.
    var addRow = groCaptureAddRow(foot);
    foot.innerHTML = groFootHtml(data, step);
    groRestoreAddRow(foot, addRow);

    // The step's one action last, and in its own strip: it has to stay on
    // screen while the list scrolls under it (rule 2).
    dock.innerHTML = groDockHtml(data, step);

    if (scrollEl) scrollEl.scrollTop = keepScroll;
  }

  function groCaptureStoresPromptInput(body) {
    var input = body.querySelector('#gro-stores-prompt-input');
    if (!input) return null;
    return { value: input.value, focused: document.activeElement === input };
  }
  function groRestoreStoresPromptInput(body, saved) {
    if (!saved || !saved.value) return;
    var input = body.querySelector('#gro-stores-prompt-input');
    if (!input) return;
    input.value = saved.value;
    if (!saved.focused) return;
    input.focus();
    try { input.setSelectionRange(input.value.length, input.value.length); } catch (err) { /* not all inputs allow it */ }
  }

  function groCaptureAddRow(foot) {
    var item = foot.querySelector('#gro-add-item');
    if (!item) return null;
    var qty = foot.querySelector('#gro-add-qty');
    return {
      item: item.value,
      qty: qty ? qty.value : '',
      focused: document.activeElement === item ? 'item'
        : (qty && document.activeElement === qty ? 'qty' : null)
    };
  }
  function groRestoreAddRow(foot, saved) {
    if (!saved) return;
    var item = foot.querySelector('#gro-add-item');
    var qty = foot.querySelector('#gro-add-qty');
    if (item) item.value = saved.item;
    if (qty) qty.value = saved.qty;
    var back = saved.focused === 'item' ? item : (saved.focused === 'qty' ? qty : null);
    if (!back) return;
    back.focus();
    try { back.setSelectionRange(back.value.length, back.value.length); } catch (err) { /* not all inputs allow it */ }
  }

  // Title, subtitle and back link for each step, in one place so the copy is
  // readable as a set rather than scattered through four builders.
  function groHeadFor(data, step) {
    if (step === 'sort') {
      return { back: '‹ Shop', title: 'Where does this go?', sub: groUnsorted(data).length + ' to sort' };
    }
    if (step === 'sorthow') {
      // Same question as the queue's, because it is the same question — the
      // household is only choosing how many screens it wants to answer it in.
      return { back: '‹ Shop', title: 'Where does this go?', sub: groUnsorted(data).length + ' to sort' };
    }
    if (step === 'sortall') {
      return { back: '‹ Shop', title: 'Sort them all', sub: groUnsorted(data).length + ' to sort' };
    }
    if (step === 'next') {
      var left = groRemainingStops(data).length;
      return {
        // Back reopens the stop just finished, which is the one thing this
        // screen can be reached by mistake from: "Done at Costco" is a
        // full-width apricot under a list of things still to tick, and
        // without this the mis-tap ended that shop for the trip.
        back: groceryState.tripLastDone ? '‹ Back to ' + groceryState.tripLastDone : '‹ Shop',
        title: 'Where next?',
        sub: left ? groPlural(left, 'stop', 'stops') + ' left' : ''
      };
    }
    if (step === 'trip') {
      var store = groTripStore();
      var stops = groceryState.tripStops || [];
      // Counted from the stops already BEHIND you, not from this stop's
      // place in the snapshot. The household picks its own order now, so the
      // snapshot index would say "Stop 3 of 3" while two shops were still
      // waiting — the number has to describe the trip, not the list.
      var done = 0;
      stops.forEach(function (n) { if (groceryState.tripDone[n]) done += 1; });
      return {
        back: '‹ Pause the trip',
        title: store || 'The trip',
        sub: 'Stop ' + (done + 1) + ' of ' + Math.max(stops.length, 1) +
          ' · ' + groTripItems(data).length + ' left'
      };
    }
    if (step === 'wrap') return { back: '‹ Shop', title: 'How did it go?', sub: '' };
    var t = groTotals(data);
    var stopCount = groStoresWithNeeded(data).length;
    var sub = '';
    if (t.needed) {
      sub = groPlural(t.needed, 'thing', 'things');
      if (stopCount) sub += ' · ' + groPlural(stopCount, 'stop', 'stops');
    }
    return { back: '', title: 'Shop', sub: sub };
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
    // The union, not `unsorted`: an item answered "Any" is still a thing on
    // the list, and before 2026-09-09 it counted for neither branch below —
    // so a household that finished sorting saw "Nothing on the list yet"
    // printed over three real items.
    var loose = groLooseItems(data);

    if (groStoresPromptShouldShow() && (stops.length || loose.length)) {
      return html + groStoresPromptHtml();
    }

    if (!stops.length && !loose.length) {
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
      return html + '<p class="gro-empty">Nothing on the list yet — it’ll arrive here when you plan a week.</p>' + groStaplesHtml();
    }

    html += groDuplicatesHtml(data);

    // A stop with nothing of its own gets no card. That happens exactly
    // once — when the only stop is the stand-in above, put there so a list
    // of shopless things can still be shopped. "Loblaws · 0" sitting over
    // "Anywhere · 2" would be a card about nothing, and the honest reading
    // is the one the Anywhere card already gives.
    stops.forEach(function (name) {
      if (groStoreCardItems(data, name).length) html += groStoreCardHtml(data, name);
    });

    // Things with no store fall into two piles, and only one of them is
    // repeated elsewhere.
    //
    // NOT YET ANSWERED ones live in SORT, which the badge above opens, so
    // they are deliberately absent here — reading the same list in two
    // places is what that rule exists to prevent.
    //
    // ANSWERED "no particular shop" ones live nowhere else at all. They
    // leave the badge the moment they are answered, and they belong to no
    // store card, so with stops on screen they used to be on no screen:
    // countable in the subtitle, present on the trip, and unreachable by
    // the one control that could change them. That was a real regression
    // the day "Any" started persisting — before it, a reload put the row
    // back in the queue, where it was at least visible. They get a card.
    //
    // With NO stops at all there is nothing else to read, so the whole
    // loose pile IS the list, headingless. This used to be a line of copy
    // pointing at the badge, which left a household reading "3 things"
    // above an empty screen — and for a household that never named a store,
    // that was the permanent state of its Grocery tab. Emily's call,
    // 2026-09-09: show them as one plain section with no store heading, so
    // "One list is fine" means what it says.
    if (!stops.length) {
      if (loose.length) html += groLooseCardHtml(data, loose);
    } else {
      // A one-shop household's loose pile is already inside that shop's own
      // card (groStoreCardItems) — it has only one place it could be
      // bought — so a second card here would print it twice.
      var anywhere = groSoleStore(data) ? [] : groRideAlongItems(data);
      if (anywhere.length) html += groAnywhereCardHtml(data, anywhere);
    }
    return html + groStaplesHtml();
  }

  // ---------- Two rows of the same thing ----------
  // Moved here whole from the Review segment's "Possible duplicate" flag
  // card (groReviewHtml on the old root), grouping on exactly the key it
  // grouped on: the trimmed, lowercased name. add_grocery_item consolidates
  // by its own merge key when a person adds something, so a surviving pair
  // is two spellings of one thing that arrived by two routes — worth
  // showing, never worth the app merging on its own.
  function groDuplicateGroups(data) {
    var groups = {};
    groAllNeeded(data).forEach(function (it) {
      var key = (it.item || '').trim().toLowerCase();
      (groups[key] = groups[key] || []).push(it);
    });
    return Object.keys(groups).map(function (k) { return groups[k]; })
      .filter(function (g) { return g.length > 1; });
  }

  var GRO_NUMBER_WORDS = ['no', 'one', 'Two', 'Three', 'Four', 'Five', 'Six',
    'Seven', 'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve'];
  function groCountWord(n) { return GRO_NUMBER_WORDS[n] || String(n); }

  // "about 3 weeks" for a toast's "I'll ask again in ..." — the same
  // rounding the server uses for cadence_words, without the "every".
  function groCadenceSpan(days) {
    days = Number(days) || 0;
    if (days < 6) return 'about ' + days + ' days';
    if (days < 10) return 'about a week';
    var weeks = Math.round(days / 7);
    if (weeks <= 1) return 'about a week';
    if (weeks <= 3) return 'about ' + weeks + ' weeks';
    var months = Math.max(1, Math.round(days / 30));
    return months <= 1 ? 'about a month' : 'about ' + months + ' months';
  }

  // One quiet line at the top of the list per duplicated thing, with the
  // Merge the Review card had. A line, not a card: it is a small thing to
  // notice on the way past, not a question the screen is about.
  function groDuplicatesHtml(data) {
    var groups = groDuplicateGroups(data);
    if (!groups.length) return '';
    return groups.map(function (g) {
      return '<p class="gro-dupe">' +
        '<span class="gro-dupe-text">' + groCountWord(g.length) + ' rows of ' +
          escapeHtml(g[0].item) + '</span>' +
        '<span class="gro-dupe-dot">·</span>' +
        '<button type="button" class="gro-dupe-merge" data-gro="merge" data-ids="' +
          g.map(function (it) { return it.id; }).join(',') + '">Merge</button>' +
      '</p>';
    }).join('');
  }

  // A store's card minus the head — no avatar, no name, because there is no
  // store to name. Rows still go through groListRowHtml, so the menu, the
  // quantity and the tick behave exactly as they do under a stop; the only
  // thing missing is a heading this household never chose.
  function groLooseCardHtml(data, items) {
    var expanded = !!groceryState.listExpanded[GRO_LOOSE_KEY];
    var shown = expanded ? items : items.slice(0, GRO_CARD_PEEK);
    var hidden = items.length - shown.length;
    return '<div class="gro-store">' +
      shown.map(function (it) { return groListRowHtml(it, data); }).join('') +
      (hidden > 0
        ? '<button type="button" class="gro-more-link" data-gro="expand-store" data-store="' +
            escapeHtml(GRO_LOOSE_KEY) + '">+ ' + hidden + ' more</button>'
        : '') +
    '</div>';
  }

  // The things answered "no particular shop", once there ARE store cards for
  // them to sit beside. They are not a stop — they have no shop — but they
  // are very much on the list, and without this card they were on no screen
  // at all: out of the TO SORT badge because they are answered, out of every
  // store card because they belong to no store, and so out of reach of the
  // row ⋯ that is the only way to change your mind about one. "Anywhere" has
  // to be a place you can point at, not a disappearance.
  function groAnywhereCardHtml(data, items) {
    var expanded = !!groceryState.listExpanded[GRO_LOOSE_KEY];
    var shown = expanded ? items : items.slice(0, GRO_CARD_PEEK);
    var hidden = items.length - shown.length;
    return '<div class="gro-store">' +
      '<div class="gro-card-head">' +
        // A basket rather than an initial: there is no name here to take a
        // letter from. No inline colour either — a store avatar's fill comes
        // from the hash palette, which is a set of LIGHT accents carrying
        // dark ink in both schemes, and this one has no name to hash. Its
        // pair of tokens lives in .gro-anywhere-avatar, where it can differ
        // by scheme like every other pair.
        '<span class="gro-store-avatar gro-anywhere-avatar">' +
          GRO_ICONS.basket + '</span>' +
        '<span class="gro-store-name">Anywhere &middot; ' + items.length + '</span>' +
      '</div>' +
      shown.map(function (it) { return groListRowHtml(it, data); }).join('') +
      (hidden > 0
        ? '<button type="button" class="gro-more-link" data-gro="expand-store" data-store="' +
            escapeHtml(GRO_LOOSE_KEY) + '">+ ' + hidden + ' more</button>'
        : '') +
    '</div>';
  }

  function groStoreCardHtml(data, name) {
    var items = groStoreCardItems(data, name);
    var expanded = !!groceryState.listExpanded[name];
    var shown = expanded ? items : items.slice(0, GRO_CARD_PEEK);
    var hidden = items.length - shown.length;
    return '<div class="gro-store">' +
      '<div class="gro-card-head">' +
        '<span class="gro-store-avatar" style="background:' + groStoreColor(name) + '">' +
          escapeHtml(groStoreInitial(name)) + '</span>' +
        '<span class="gro-store-name">' + escapeHtml(name) + ' · ' + items.length + '</span>' +
      '</div>' +
      shown.map(function (it) { return groListRowHtml(it, data); }).join('') +
      (hidden > 0
        ? '<button type="button" class="gro-more-link" data-gro="expand-store" data-store="' + escapeHtml(name) + '">' +
            '+ ' + hidden + ' more</button>'
        : '') +
    '</div>';
  }

  function groListRowHtml(it, data) {
    var id = String(it.id);
    var open = groceryState.openRowId === id;
    var staple = !!it.staple_id;
    return '<div class="gro-listrow' + (open ? ' open' : '') + (staple ? ' gro-listrow-staple' : '') + '">' +
      '<span class="gro-listrow-name">' + escapeHtml(it.item) + '</span>' +
      (it.quantity ? '<span class="gro-qty">' + escapeHtml(it.quantity) + '</span>' : '') +
      '<button type="button" class="gro-rowmore" data-gro="row-menu" data-id="' + id + '" ' +
        'aria-expanded="' + open + '" aria-label="More for ' + escapeHtml(it.item) + '">' +
        GRO_ICONS.dots + '</button>' +
    '</div>' +
    (staple ? groStapleLineHtml(it) : '') +
    (open ? groRowMenuHtml(it, data) : '');
  }

  // The line under a staple Pomona put on the list itself (staple_id set):
  // the visible flag that makes this silent learning rather than guessing
  // (DESIGN_SYSTEM.md §7), and the two answers that change anything. There
  // is no "keep" button: leaving it on the list is keeping it, and buying
  // it is what teaches the rhythm. Quiet throughout — no apricot.
  function groStapleLineHtml(it) {
    var id = String(it.id);
    return '<div class="gro-staple-line">' +
      '<span class="gro-staple-text">Probably running low</span>' +
      '<button type="button" class="gro-staple-btn" data-gro="staple-decide" data-decision="plenty" ' +
        'data-id="' + id + '" data-name="' + escapeHtml(it.item) + '">We have plenty</button>' +
      '<button type="button" class="gro-staple-btn" data-gro="staple-decide" data-decision="skip" ' +
        'data-id="' + id + '" data-name="' + escapeHtml(it.item) + '">Not this trip</button>' +
    '</div>';
  }

  // The row's ⋯, restored from the root's per-row menu and carrying the same
  // verbs on the same routes — three of them, in the order a person reaches
  // for them:
  //   edit the quantity  -> POST /api/grocery-list/{id}/update   (old fix-qty
  //                         and the quantity half of the old save-row)
  //   change the store   -> POST /api/grocery-list/{id}/store    (the pills;
  //                         "Any" is the old move / not-this-time, which
  //                         clears the row's store for this week without
  //                         forgetting the remembered item->store preference,
  //                         and "Somewhere else" is the same /exclude the
  //                         SORT chip uses)
  //   remove the line    -> POST /api/grocery-list/{id}/remove   (with an undo)
  // Quiet throughout — no apricot anywhere in it. LIST's one apricot is
  // "Start the trip" (Rule 5), and a row action is never the thing the
  // screen is for.
  function groRowMenuHtml(it, data) {
    var id = String(it.id);
    return '<div class="gro-rowmenu" data-menu-for="' + id + '">' +
      '<div class="gro-rowmenu-qty">' +
        '<input type="text" class="gro-rowmenu-input" id="gro-rowqty-' + id + '" ' +
          'value="' + escapeHtml(it.quantity || '') + '" placeholder="How much?" ' +
          'aria-label="Quantity for ' + escapeHtml(it.item) + '" />' +
        '<button type="button" class="gro-rowmenu-save" data-gro="row-qty" data-id="' + id + '">Save</button>' +
      '</div>' +
      '<div class="gro-pills open">' +
        groPillStores(data).map(function (n) {
          return '<button type="button" class="gro-pill" data-gro="row-store" data-id="' + id + '" ' +
            'data-store="' + escapeHtml(n) + '" ' +
            'aria-label="Buy ' + escapeHtml(it.item) + ' at ' + escapeHtml(n) + '">' + escapeHtml(n) + '</button>';
        }).join('') +
        '<button type="button" class="gro-pill" data-gro="row-store" data-id="' + id + '" data-store="" ' +
          'aria-label="No particular store for ' + escapeHtml(it.item) + '">Any</button>' +
        '<button type="button" class="gro-pill gro-pill-else" data-gro="row-exclude" data-id="' + id + '" ' +
          'aria-label="Getting ' + escapeHtml(it.item) + ' somewhere else">Somewhere else</button>' +
      '</div>' +
      '<div class="gro-rowmenu-foot">' +
        // A tap on a thing you buy is how a staple gets made without a
        // form (the other way is telling the assistant). Hidden once it
        // is one — the Staples card below the list is where it lives then.
        (groIsStapleName(it.item)
          ? '<span class="gro-rowmenu-note">One of your staples</span>'
          : '<button type="button" class="gro-rowmenu-staple" data-gro="row-staple" data-id="' + id + '" ' +
              'data-name="' + escapeHtml(it.item) + '" data-qty="' + escapeHtml(it.quantity || '') + '" ' +
              'data-cat="' + escapeHtml(it.category || 'other') + '">Make it a staple</button>') +
        '<button type="button" class="gro-rowmenu-remove" data-gro="row-remove" data-id="' + id + '" ' +
          'data-name="' + escapeHtml(it.item) + '" data-qty="' + escapeHtml(it.quantity || '') + '" ' +
          'data-cat="' + escapeHtml(it.category || 'other') + '" data-store="' + escapeHtml(it.store || '') + '">' +
          'Remove</button>' +
      '</div>' +
    '</div>';
  }

  // Same idea as the server's merge key, at the strength this needs: case,
  // spacing and a trailing "s" don't make two names two things.
  function groStapleKey(name) {
    var key = (name || '').trim().toLowerCase().replace(/\s+/g, ' ');
    return key.length > 3 && key.slice(-1) === 's' ? key.slice(0, -1) : key;
  }
  function groIsStapleName(name) {
    var key = groStapleKey(name);
    return groceryState.staples.some(function (st) { return groStapleKey(st.item) === key; });
  }

  // ---------- Staples ----------
  // One quiet card at the foot of LIST, closed by default: what the
  // household buys on a rhythm, each with when Pomona thinks it is next due.
  // Nothing here is a question — a due staple is already on the list above
  // as a line. Pause and Remove are the only verbs; Resume undoes a pause.
  function groStaplesHtml() {
    var staples = groceryState.staples;
    if (!staples.length) return '';
    var open = groceryState.staplesOpen;
    var html = '<div class="gro-staples">' +
      '<button type="button" class="gro-ps-head" data-gro="staples-toggle" aria-expanded="' + open + '">' +
        GRO_ICONS.basket +
        '<span class="gro-ps-text">' +
          '<span class="gro-ps-title">Staples</span>' +
          '<span class="gro-ps-sub">' + groPlural(staples.length, 'thing', 'things') + ' you buy on a rhythm</span>' +
        '</span>' +
        '<span class="gro-ps-check">' + (open ? 'Hide' : 'See') + '</span>' +
      '</button>';
    if (open) {
      html += '<div class="gro-staples-body">' +
        staples.map(function (st) {
          var meta = st.paused
            ? 'Paused'
            : st.cadence_words + (st.due_words ? ' · ' + st.due_words : '');
          return '<div class="gro-staple-row' + (st.paused ? ' paused' : '') + '">' +
            '<span class="gro-staple-name">' + escapeHtml(st.item) + '</span>' +
            '<span class="gro-staple-meta">' + escapeHtml(meta) + '</span>' +
            '<button type="button" class="gro-staple-act" data-gro="' + (st.paused ? 'staple-resume' : 'staple-pause') + '" ' +
              'data-id="' + st.id + '" data-name="' + escapeHtml(st.item) + '">' + (st.paused ? 'Resume' : 'Pause') + '</button>' +
            '<button type="button" class="gro-staple-act" data-gro="staple-remove" data-id="' + st.id + '" ' +
              'data-name="' + escapeHtml(st.item) + '">Remove</button>' +
          '</div>';
        }).join('') +
      '</div>';
    }
    return html + '</div>';
  }

  // Every store already on the list, plus the household's usual stores — so
  // a store can be chosen before anything is tagged to it. Shared by SORT's
  // chips and the LIST row's ⋯ so the two can't offer different stores.
  function groPillStores(data) {
    var pillStores = [];
    Object.keys(data.stores).forEach(function (n) {
      if (n !== 'Unassigned' && pillStores.indexOf(n) === -1) pillStores.push(n);
    });
    groceryState.usualStores.forEach(function (n) {
      if (n && pillStores.indexOf(n) === -1) pillStores.push(n);
    });
    return pillStores;
  }

  // ---------- SORT HOW: the two fast paths, and the queue as the third ----
  // Forty things answered one screen at a time is forty screens, and that
  // was Emily's complaint. The queue is still right for a handful and is
  // untouched below; this step is what stands in front of it once there are
  // more than GRO_FAST_SORT_MIN, and it offers the two answers that finish
  // the job in one go before offering the one that doesn't.

  // Which shop to put everything at. Read off the list the tab already has
  // open: every row tagged to a shop, whatever its status — still needed, in
  // the trolley, or bought this cycle — counted per shop, biggest wins. That
  // is the app's own record of where this household's groceries actually go,
  // so there is no new counter and no new fetch behind this number. A
  // household with nothing tagged yet has no record to read, and falls back
  // to the first shop it named, which is the order it typed them in.
  function groMostUsedStore(data) {
    var counts = {};
    Object.keys(data.stores).forEach(function (name) {
      if (name === 'Unassigned') return;
      var s = data.stores[name];
      counts[name] = groNeededCount(s) + s.purchased.length + s.inCart.length;
    });
    var best = null;
    groPillStores(data).forEach(function (name) {
      if (best === null || (counts[name] || 0) > (counts[best] || 0)) best = name;
    });
    return best;
  }

  function groSortHowHtml(data) {
    var unsorted = groUnsorted(data);
    if (!unsorted.length) return '<p class="gro-empty">Nothing left to sort — nice work.</p>';
    // Two rows, and the quiet one is last. "1 of 40" is the queue said
    // honestly: it is where you would be after the first answer, out of how
    // many answers it wants.
    return '<div class="shell-card gro-howcard">' +
        '<button type="button" class="gro-howrow" data-gro="goto-sortall">' +
          '<span class="gro-howrow-text">' +
            '<span class="gro-howrow-title">Sort them all on one screen</span>' +
            '<span class="gro-howrow-sub">One row each. Tap only the exceptions.</span>' +
          '</span>' +
          '<span class="gro-chev">' + GRO_ICONS.chevRight + '</span>' +
        '</button>' +
        '<button type="button" class="gro-howrow" data-gro="goto-sort-one">' +
          '<span class="gro-howrow-text">' +
            '<span class="gro-howrow-title">Or one at a time</span>' +
            '<span class="gro-howrow-sub">1 of ' + unsorted.length + '</span>' +
          '</span>' +
          '<span class="gro-chev">' + GRO_ICONS.chevRight + '</span>' +
        '</button>' +
      '</div>';
  }

  // ---------- SORT ALL: the whole list, one row each ----------
  // Every unsorted thing with a shop chip on it, everything starting at the
  // most-used shop, and nothing written until the button at the foot. Staged
  // rather than saved per tap for two reasons that are really one: a tap
  // must not cost a request on a screen where forty of them are expected,
  // and it must not re-render the list under the thumb that is working down
  // it. The tap handler edits the one row's chips in the DOM and nothing
  // else moves — see 'sortall-pick'.
  function groSortAllPick(it, fallback) {
    var staged = groceryState.sortAllPicks[String(it.id)];
    return staged === undefined ? (fallback || '') : staged;
  }

  function groSortAllHtml(data) {
    var unsorted = groUnsorted(data);
    if (!unsorted.length) return '<p class="gro-empty">Nothing left to sort — nice work.</p>';
    var pillStores = groPillStores(data);
    var fallback = groMostUsedStore(data);
    return '<div class="shell-card gro-sortall">' +
      unsorted.map(function (it) {
        var id = String(it.id);
        var picked = groSortAllPick(it, fallback);
        var chips = pillStores.map(function (n) {
          return groSortAllChip(id, it.item, n, n, picked === n);
        }).join('') + groSortAllChip(id, it.item, '', 'Any', picked === '');
        return '<div class="gro-sortall-row" data-row-for="' + id + '">' +
            '<div class="gro-sortall-head">' +
              '<span class="gro-sortall-name">' + escapeHtml(it.item) + '</span>' +
              (it.quantity ? '<span class="gro-qty">' + escapeHtml(it.quantity) + '</span>' : '') +
            '</div>' +
            '<div class="gro-pills open gro-sortall-pills">' + chips + '</div>' +
          '</div>';
      }).join('') +
    '</div>';
  }

  function groSortAllChip(id, itemName, store, label, on) {
    return '<button type="button" class="gro-pill' + (on ? ' gro-pill-on' : '') + '" ' +
      'data-gro="sortall-pick" data-id="' + id + '" data-store="' + escapeHtml(store) + '" ' +
      'aria-pressed="' + on + '" ' +
      'aria-label="' + escapeHtml(itemName) + ': ' +
        (store ? escapeHtml(store) : 'no particular shop') + '">' +
      escapeHtml(label) + '</button>';
  }

  // ---------- SORT ----------
  // One thing at a time. The pills and their semantics are the ones the
  // triage row already had: a store assigns and advances, "Any" saves an
  // empty store and advances, "Have it" takes it off the list into the
  // kitchen, "Somewhere else" excludes it. The last choice drops through to
  // LIST on its own — see the 'assign' handler.
  function groSortHtml(data) {
    var unsorted = groUnsorted(data);
    if (!unsorted.length) return '<p class="gro-empty">Nothing left to sort — nice work.</p>';
    var it = unsorted[0];
    var id = String(it.id);

    // Every store already on the list, plus the household's usual stores —
    // so a store can be chosen before anything is tagged to it.
    var pillStores = groPillStores(data);

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

  // What this stop is for — its own things, plus everything with no shop of
  // its own. Those follow the shopper from stop to stop rather than being
  // pinned to the first one: they can be bought anywhere, the shop you are
  // standing in is as good an answer as any, and one left unbought at the
  // first stop used to be stranded for the rest of the trip. That mattered
  // more once the household started choosing its own order of stops.
  function groTripItems(data) {
    var store = groTripStore();
    if (!store) return [];
    var s = data.stores[store];
    var items = s ? groStoreItems(s) : [];
    return items.concat(groRideAlongItems(data));
  }
  // The trolley at this stop. The shopless half is filtered the same way
  // groTripItems filters the needed half — only what actually rides along —
  // so the two halves of one screen cannot disagree about which rows belong
  // to this stop.
  function groRideAlongInCart(data) {
    var any = data.stores['Unassigned'];
    if (!any) return [];
    if (groSoleStore(data)) return any.inCart.slice();
    return any.inCart.filter(groItemDecided);
  }
  function groTripInCart(data) {
    var store = groTripStore();
    if (!store) return [];
    var s = data.stores[store];
    var items = s ? s.inCart.slice() : [];
    return items.concat(groRideAlongInCart(data));
  }
  // The stop's needed things, aisle by aisle, in the order the payload
  // already put them (get_grocery_list_by_store's own section order — the
  // order a shop is walked in). The ride-along things fold into the matching
  // aisle rather than trailing after it.
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
    var rideIds = {};
    groRideAlongItems(data).forEach(function (it) { rideIds[String(it.id)] = true; });
    var any = data.stores['Unassigned'];
    if (any) {
      fold((any.sections || []).map(function (sec) {
        return {
          section: sec.section,
          items: sec.items.filter(function (it) { return !!rideIds[String(it.id)]; })
        };
      }));
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

  // ---------- WHERE NEXT ----------
  // Finishing a stop used to start the next one, in the order the snapshot
  // happened to be in. Nobody drives in the order a list was built, so this
  // asks: the stops that are left, with what is still on each, and the
  // answer that ends the day. The snapshot rule holds — tripStops is
  // untouched, so nothing is renumbered; this only changes which of them the
  // shopper walks into next.
  function groStopRemaining(data, name) {
    var s = data.stores[name];
    return s ? groNeededCount(s) + s.inCart.length : 0;
  }
  // Stops still worth walking into: not already finished, and with something
  // left on them. A shop whose things all came home some other way is not a
  // choice, it is a detour.
  function groRemainingStops(data) {
    return (groceryState.tripStops || []).filter(function (n) {
      return !groceryState.tripDone[n] && groStopRemaining(data, n) > 0;
    });
  }

  function groNextHtml(data) {
    var remaining = groRemainingStops(data);
    if (!remaining.length) return '<p class="gro-empty">That is every stop — wrap it up.</p>';
    var html = '<div class="gro-store gro-nextstops">' +
      remaining.map(function (name) {
        return '<button type="button" class="gro-nextrow" data-gro="next-stop" ' +
            'data-store="' + escapeHtml(name) + '">' +
            '<span class="gro-store-avatar" style="background:' + groStoreColor(name) + '">' +
              escapeHtml(groStoreInitial(name)) + '</span>' +
            '<span class="gro-nextrow-text">' +
              '<span class="gro-nextrow-name">' + escapeHtml(name) + '</span>' +
              '<span class="gro-nextrow-count">' + groPlural(groStopRemaining(data, name), 'thing', 'things') + ' left</span>' +
            '</span>' +
            '<span class="gro-chev">' + GRO_ICONS.chevRight + '</span>' +
          '</button>';
      }).join('');
    // Said once, because it is the one thing about this screen a shopper
    // could get wrong: the shopless things are not at any of these stops,
    // they come to whichever one you pick. Inside the card rather than under
    // it, because it is about these rows — and because --ink-secondary on
    // the card's surface clears AA where the same ink on the ground does not
    // (4.70:1 against 4.44:1, both measured; see the rule in shell.css).
    var ride = groRideAlongItems(data).length;
    if (ride) {
      html += '<p class="gro-next-note">' + groPlural(ride, 'thing', 'things') +
        ' with no shop will come with you.</p>';
    }
    return html + '</div>';
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

  // ---------- The foot: what is NOT the screen's one action ----------
  // Only LIST has anything here now, and only the add row. The step's one
  // action moved to the dock below (rule 2) — at the foot of a real week's
  // groceries, "Start the trip" sat a hundred rows under the fold.
  function groFootHtml(data, step) {
    if (step !== 'list') return '';
    // Adding one thing must not cost a model turn. This posts straight to
    // /api/grocery-list/add — the same route groHandleVoiceCommand's "add
    // oat milk" uses, and the same one the root's "Add an item" card used
    // — so the cheap, common case stays cheap. The ask bar above the tab
    // bar is still there for anything wordier ("add oat milk and lemons,
    // and drop the spinach"), which is what it is good at.
    //
    // It stays out of the dock deliberately: adding a thing is a side
    // errand next to starting the trip, and a dock holding two jobs is not
    // a dock (rule 2 — "a second apricot" is what Rule 5 already forbids,
    // and a second STRIP is the same mistake one level up).
    // The photo button (Loop Board, 2026-09-11 — "snap a photo of a
    // written or on-screen list and have Pomona add it") rides along in
    // the same row as the manual add, the nearest existing "put something
    // on the list" affordance — the inventory scans this borrows the
    // pattern from live on a different tab (Kitchen's Inventory sheet),
    // and where exactly this control sits was left an open design
    // question on the ticket. See groScanOpenPicker/groScanUploadPhoto.
    return '<div class="gro-add">' +
        '<input type="text" class="gro-add-item" id="gro-add-item" ' +
          'placeholder="Add something" aria-label="Something to add to the list" />' +
        '<input type="text" class="gro-add-qty" id="gro-add-qty" placeholder="Qty" aria-label="How much" />' +
        '<button type="button" class="gro-add-btn" id="gro-add-btn" data-gro="add">Add</button>' +
        '<button type="button" class="gro-scan-btn" id="gro-scan-btn" data-gro="scan-open" ' +
          'title="Add from a photo of your list" aria-label="Add from a photo of your list">' +
          GRO_ICONS.camera +
        '</button>' +
      '</div>';
  }

  // ---------- The dock: one apricot action per step (Rule 5) ----------
  // Same labels, same handlers, same one-per-screen discipline these had at
  // the foot; what changed is that they stay on screen (rule 2).
  function groDockHtml(data, step) {
    if (step === 'list') {
      var stops = groStoresWithNeeded(data);
      // Nothing to start while the shops question is up: LIST is showing
      // that card INSTEAD of the stops (groListHtml returns early), so the
      // button would walk the household through shops that aren't on the
      // screen — and its apricot would be a second one beside the card's,
      // which Rule 5 doesn't allow. No action, so no dock — the rule's own
      // "a screen with no single action has no dock" case.
      var canGo = stops.length > 0 && !groStoresPromptShouldShow();
      return canGo
        ? '<button type="button" class="gro-primary" data-gro="start-trip">Start the trip</button>'
        : '';
    }
    // SORT HOW's one apricot is the bulk answer, because it is the one that
    // finishes the job in a single tap. The other two paths are rows in the
    // body — quieter, and neither of them is a second primary (Rule 5).
    if (step === 'sorthow') {
      var most = groMostUsedStore(data);
      if (!most) return '';
      return '<button type="button" class="gro-primary" data-gro="sort-all-at" ' +
        'data-store="' + escapeHtml(most) + '">Put all ' + groUnsorted(data).length +
        ' at ' + escapeHtml(most) + '</button>';
    }
    if (step === 'sortall') {
      return '<button type="button" class="gro-primary" data-gro="sortall-save">That&rsquo;s them sorted</button>';
    }
    if (step === 'trip') {
      // The button no longer names the next stop, because the next stop is
      // no longer this screen's to decide — see the WHERE NEXT step.
      return '<button type="button" class="gro-primary" data-gro="stop-done">' +
        escapeHtml('Done at ' + (groTripStore() || 'this stop')) + '</button>';
    }
    if (step === 'next') {
      // Quiet, and deliberately the only button here: the stops above are
      // the choice this screen is for, and an apricot on "I'm done" would
      // put the tab's accent on ending the trip early. Rule 5 allows a
      // screen with no primary; Kitchen's root is the precedent.
      return '<button type="button" class="gro-secondary" data-gro="trip-end">' +
        'I&rsquo;m done shopping for today</button>';
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
    var entry = tonightDinnerEntry();
    return entry ? entry.title : null;
  }

  function tonightDinnerEntry() {
    var data = weekState.data;
    if (!data || !data.days) return null;
    var todayStr = todayLocalStr();
    var day = data.days.filter(function (d) { return d.date === todayStr; })[0];
    var entry = day && day.dinner;
    if (entry && entry.title && entry.state !== 'open' && entry.state !== 'planned_empty') return entry;
    return null;
  }

  // The exact meal "Show me tonight" (and the dish name beside it) opens.
  // `true` is the old "tonight, whatever the clock says that is" fallback,
  // used when Meals has never been opened this session and there is no
  // entry to name — the same shape every other cookFocus caller passes.
  function tonightDinnerRecipeTarget() {
    var entry = tonightDinnerEntry();
    return (entry && recipeTargetForEntry(entry, todayLocalStr(), 'dinner')) || true;
  }

  // The handoff for "that's the shopping done" (Emily, 2026-09-04's ask to
  // walk the loop forward): shown on LIST once the list has nothing left to
  // buy AND a trip was actually finished in this page view —
  // groceryState.justFinishedTrip, not a lifetime purchased/in_cart count
  // (see that flag's declaration for why). Spruce fill, not apricot: LIST's
  // own "Start the trip" owns the screen's one apricot (Rule 5).
  //
  // The dish name in that line is a link too (2026-09-10). The branch's own
  // decision-log entry claimed "Grocery names no dishes at all"; it names
  // exactly one, right here, and Emily's rule has no exception for it. It
  // is the same door as the button beside it — same data-gro, same handler,
  // same meal — so this adds a way in, never a second answer.
  function groShopDoneHtml() {
    var dish = tonightDinnerName();
    var line = dish
      ? 'That’s the shopping done. Tonight it’s ' +
        '<button type="button" class="dish-link is-inline" data-gro="shop-done-tonight">' +
        escapeHtml(dish) + '</button>.'
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
  // anything else. Every tap saves immediately through the same write path
  // the Kitchen "What we know" Stores tab uses (edit_preference/usual_stores),
  // so the SORT pills offer exactly the shops named here.
  //
  // MULTI-SELECT, since 2026-09-09 (Emily, testing as a new household): most
  // households shop at more than one place, and this card used to close on
  // the first tap — its own gate was "no shops named yet", which the first
  // save made false. One shop was the most anybody could name, and the
  // question vanished into a LIST with nothing tagged to a store yet, which
  // is the empty screen she landed on. Tapping now toggles and nothing but
  // the button at the foot ends the question.
  var GRO_STORE_PROMPT_CHIPS = ['Costco', 'Loblaws', 'No Frills', 'Metro', 'Sobeys', 'Walmart', 'Farm Boy', 'T&T', 'Whole Foods'];

  // storesPromptOpen has to survive a reload, and that is not a nicety.
  // Page-view only, it ended the question for good: tap one shop, reload,
  // and the gate below reads "shops named, never dismissed" — permanently
  // false, while the database still records the question as unanswered. The
  // only remaining route to the other shops was Kitchen → What we know →
  // Stores, which a brand-new household has never been shown.
  //
  // Gating on !storesPromptDismissed alone would have fixed that and broken
  // something worse: nothing backfills stores_prompt_dismissed_at (app/db.py),
  // so every EXISTING household — shops named months ago, no dismissal row —
  // would be asked the question all over again.
  //
  // So the flag is persisted on the client, the same shape the approved-week
  // receipt's dismissal uses (WEEK_RECEIPT_DISMISS_KEY). localStorage rather
  // than sessionStorage, and that is the whole of the decision: an installed
  // PWA is killed and relaunched constantly, and a relaunch ends the session
  // — so a household that taps a shop, takes a phone call and comes back
  // would lose the question exactly as it does today. The receipt can afford
  // sessionStorage because coming back next session is its correct
  // behaviour; an unfinished question coming back is the whole point of it.
  // Keyed per household so two households signing into one browser can't
  // inherit each other's half-answered question.
  var STORES_PROMPT_OPEN_KEY = 'pomona.storesPromptOpen.h';

  function storesPromptOpenKey() {
    // coachState is the shell's one client-side answer to "which household
    // is this", and it is declared hundreds of lines below this one — so
    // this guards against running before that var does, the same way
    // coachOnTabShown has to.
    var id = (typeof coachState !== 'undefined' && coachState) ? coachState.householdId : null;
    return STORES_PROMPT_OPEN_KEY + (id == null ? 'x' : id);
  }

  // Every read and write is wrapped: Safari in private mode throws on
  // localStorage rather than returning null, and a remembered question must
  // never take the Grocery tab down with it.
  function readStoresPromptOpen() {
    try {
      var key = storesPromptOpenKey();
      if (window.localStorage.getItem(key) === '1') return true;
      // A tap in the moment before /api/coaching answered lands under the
      // household-less key. Adopt it once, under this household's own key,
      // rather than leave it lying there for the next household to find.
      if (window.localStorage.getItem(STORES_PROMPT_OPEN_KEY + 'x') !== '1') return false;
      window.localStorage.removeItem(STORES_PROMPT_OPEN_KEY + 'x');
      window.localStorage.setItem(key, '1');
      return true;
    } catch (err) { return false; }
  }

  function groSetStoresPromptOpen(open) {
    groceryState.storesPromptOpen = open;
    try {
      if (open) window.localStorage.setItem(storesPromptOpenKey(), '1');
      else {
        window.localStorage.removeItem(storesPromptOpenKey());
        window.localStorage.removeItem(STORES_PROMPT_OPEN_KEY + 'x');
      }
    } catch (err) { /* see above: the question just stays page-view only */ }
  }

  function groStoresPromptShouldShow() {
    // Answered once is answered for good, whatever the answer was.
    if (groceryState.storesPromptDismissed) return false;
    // Being answered right now — see storesPromptOpen. Checked before the
    // "never named a shop" test below, which goes false on the first tap.
    if (groceryState.storesPromptOpen) return true;
    return !groceryState.usualStores.length;
  }

  function groStoresPromptHtml() {
    var picked = groceryState.usualStores;
    // A shop typed into "Somewhere else?" joins the presets rather than
    // living apart from them, so it can be un-picked the same way as any
    // other — a typo shouldn't need the Kitchen sheet to undo.
    var names = GRO_STORE_PROMPT_CHIPS.slice();
    picked.forEach(function (name) { if (names.indexOf(name) === -1) names.push(name); });
    var chips = names.map(function (name) {
      var on = picked.indexOf(name) !== -1;
      return '<button type="button" class="gro-pill' + (on ? ' gro-pill-on' : '') + '" ' +
        'data-gro="stores-prompt-pick" data-store="' + escapeHtml(name) + '" ' +
        'aria-pressed="' + on + '">' + escapeHtml(name) + '</button>';
    }).join('');
    // The chips carry the answer, but a tap on a phone is often under a
    // thumb — one line says the count out loud so it can be read without
    // hunting for which chips changed colour.
    var count = picked.length;
    var countLine = count ? groPlural(count, 'shop', 'shops') + ' picked' : 'No shops picked yet';
    // One button, and it is the only way out of the question. Its wording is
    // the household's own answer: no shops chosen is a real answer, not a
    // skip, and it keeps the exact words the quiet dismissal used to carry.
    var done = count ? 'That&rsquo;s where we shop' : 'One list is fine';
    return (
      '<div class="shell-card gro-stores-prompt">' +
        '<p class="gro-stores-prompt-title">Where do you usually shop?</p>' +
        '<p class="gro-stores-prompt-sub">Tap every shop you use. I&rsquo;ll sort the list by store and plan your stops.</p>' +
        '<div class="gro-pills open">' + chips + '</div>' +
        '<div class="gro-stores-prompt-add">' +
          '<input type="text" class="gro-stores-prompt-input" id="gro-stores-prompt-input" ' +
            'placeholder="Somewhere else?" aria-label="Add a store you usually shop at" />' +
          '<button type="button" class="gro-linkbtn" data-gro="stores-prompt-add">Add</button>' +
        '</div>' +
        '<p class="gro-stores-prompt-count">' + countLine + '</p>' +
        '<button type="button" class="gro-primary" data-gro="stores-prompt-done">' + done + '</button>' +
      '</div>'
    );
  }

  // Saves through the same field edit_preference/the Stores tab already
  // uses. The WHOLE list goes over the wire, not the one shop that changed,
  // because usual_stores is a set rather than an append log — un-picking has
  // to be able to take one back out again.
  //
  // Which makes every tap a read-modify-write, and three taps under a thumb
  // are three of them at once. Measured: with a 400ms round trip and taps
  // 150ms apart, the second and third taps each read a list the first tap's
  // answer had not reached yet, so each one wrote the earlier shops back
  // out — one shop saved, three showing. It never happens on a laptop,
  // which is exactly why it needed fixing rather than watching.
  //
  // Two things fix it together. Local state moves FIRST, so the next tap
  // reads a list that already has the last one in it (which is also what
  // the chip's own colour has always claimed). And the writes are
  // serialised: one is in flight at a time, and the one behind it sends
  // whatever the list is at the moment it actually goes out. Taps arriving
  // during a write collapse into that single trailing write, so the last
  // tap wins and it wins by sending everything.
  var storesWriteChain = Promise.resolve();
  var storesWriteTrailing = null;

  function groSetUsualStores(next) {
    groceryState.usualStores = next;
    renderGrocery();
    if (storesWriteTrailing) return storesWriteTrailing;
    var send = function () {
      storesWriteTrailing = null;
      return groPost('/api/memory/edit', {
        field: 'usual_stores', value: groceryState.usualStores.slice()
      });
    };
    // .then(send, send): the write behind a FAILED one still has to go out,
    // or one dropped connection would silently stop every later tap saving.
    storesWriteTrailing = storesWriteChain.then(send, send);
    storesWriteChain = storesWriteTrailing.catch(function () {
      // The optimistic list is now ahead of what the server holds, and the
      // count line under the chips would be saying something untrue. Take
      // the server's answer back; the caller has already said out loud that
      // the save failed.
      return groLoadUsualStores();
    });
    return storesWriteTrailing;
  }

  function groToggleUsualStore(name) {
    name = (name || '').trim();
    if (!name) return Promise.resolve();
    return groceryState.usualStores.indexOf(name) === -1
      ? groSetUsualStores(groceryState.usualStores.concat([name]))
      : groSetUsualStores(groceryState.usualStores.filter(function (n) { return n !== name; }));
  }

  // The free-text half: adding is all it can do, so a name already on the
  // list is a no-op rather than a toggle — typing "Costco" a second time
  // must not quietly un-pick the chip that is already lit.
  function groAddUsualStore(name) {
    name = (name || '').trim();
    if (!name || groceryState.usualStores.indexOf(name) !== -1) return Promise.resolve();
    return groSetUsualStores(groceryState.usualStores.concat([name]));
  }

  // ---------- Actions ----------

  // The LIST foot's inline add — one POST to /api/grocery-list/add, no model
  // turn, exactly as the root's "Add an item" card and the voice session's
  // "add oat milk" both do it. An item added with no store lands in the
  // Unassigned bucket, which is what the TO SORT badge counts, so the new
  // thing shows up there rather than silently having no stop.
  async function groAddItem() {
    var panel = groPanel();
    if (!panel) return;
    var itemInput = panel.querySelector('#gro-add-item');
    var qtyInput = panel.querySelector('#gro-add-qty');
    var btn = panel.querySelector('#gro-add-btn');
    if (!itemInput || !qtyInput || !btn) return;
    var name = itemInput.value.trim();
    if (!name) { itemInput.focus(); return; }
    var qty = qtyInput.value.trim();
    btn.disabled = true;
    // Cleared BEFORE the write: groDo re-renders on the way out, and the
    // foot's value-preserving re-render would otherwise put the typed text
    // straight back into an emptied field.
    itemInput.value = '';
    qtyInput.value = '';
    var ok = await groDo(function () {
      return groPost('/api/grocery-list/add', { item: name, quantity: qty, category: 'other' });
    }, "Couldn't add that — try again.");
    var freshItem = panel.querySelector('#gro-add-item');
    var freshBtn = panel.querySelector('#gro-add-btn');
    if (freshBtn) freshBtn.disabled = false;
    if (!ok) {
      // Nothing was saved, so the typing has to come back rather than vanish.
      if (freshItem) freshItem.value = name;
      var freshQty = panel.querySelector('#gro-add-qty');
      if (freshQty) freshQty.value = qty;
      return;
    }
    if (freshItem) freshItem.focus();
  }

  // ---------- Photo scan review (Loop Board, 2026-09-11) ----------
  // "Grocery: snap a photo of a written or on-screen list and have Pomona
  // add it." A fourth scan target sibling to inventory's receipt/fridge/
  // pantry scans (app/agent.py's _scan_image_for_items,
  // /api/inventory/scan-*) — same forced-tool-call model, same
  // review-before-save shape (POST /api/grocery-list/scan for the draft,
  // POST /api/grocery-list/confirm-scan to actually add anything). The
  // sheet lives at body level (shell.html), same as ask-sheet/week-sheet,
  // since position:fixed has to sit outside this tab panel's own
  // stacking/scroll context — so it is wired once below, not rebuilt
  // inside buildGroceryPanel.
  var groScanState = { items: [] };

  function groScanOpenPicker() {
    var input = document.getElementById('gro-scan-input');
    if (input) input.click();
  }

  function groScanOpenSheet() {
    var scrim = document.getElementById('gro-scan-scrim');
    var sheet = document.getElementById('gro-scan-sheet');
    if (!scrim || !sheet) return;
    scrim.hidden = false;
    sheet.hidden = false;
  }
  function groScanCloseSheet() {
    var scrim = document.getElementById('gro-scan-scrim');
    var sheet = document.getElementById('gro-scan-sheet');
    if (scrim) scrim.hidden = true;
    if (sheet) sheet.hidden = true;
    groScanState.items = [];
  }

  function groScanRenderLoading() {
    var body = document.getElementById('gro-scan-body');
    if (!body) return;
    body.innerHTML =
      '<p class="gro-scan-sub">Reading your photo&hellip;</p>' +
      '<div class="gro-scan-loading">This can take a few seconds.</div>';
  }

  // Calm and plain per DESIGN_SYSTEM §8 — error copy never gets an
  // exclamation mark or forced cheer, and it's always paired with a way
  // out (here, just Close — the file input is untouched so trying again
  // is one more tap on the camera button).
  function groScanRenderError(message) {
    var body = document.getElementById('gro-scan-body');
    if (!body) return;
    body.innerHTML =
      '<div class="gro-scan-error">' + escapeHtml(message) + '</div>' +
      '<div class="gro-scan-actions">' +
        '<button type="button" class="gro-scan-cancel" data-gro="scan-close">Close</button>' +
      '</div>';
  }

  // Voice per DESIGN_SYSTEM §8: state what happened, then the one thing to
  // check — "untick anything I got wrong" is the review step in one line,
  // not a restated header.
  function groScanRenderReview() {
    var body = document.getElementById('gro-scan-body');
    if (!body) return;
    if (!groScanState.items.length) {
      body.innerHTML =
        '<p class="gro-scan-empty">Nothing to add from that photo.</p>' +
        '<div class="gro-scan-actions"><button type="button" class="gro-scan-cancel" data-gro="scan-close">Close</button></div>';
      return;
    }
    body.innerHTML =
      '<p class="gro-scan-sub">Here&rsquo;s what I read &mdash; untick anything I got wrong.</p>' +
      '<div class="gro-scan-list">' +
        groScanState.items.map(function (it, i) {
          return '<div class="gro-scan-row' + (it.keep === false ? ' unchecked' : '') + '" data-idx="' + i + '">' +
            '<label class="gro-scan-check-wrap">' +
              '<input type="checkbox" class="gro-scan-check" data-idx="' + i + '" ' + (it.keep === false ? '' : 'checked') +
                ' aria-label="Keep ' + escapeHtml(it.item) + '" />' +
            '</label>' +
            '<input type="text" class="gro-scan-name" data-idx="' + i + '" value="' + escapeHtml(it.item) + '" aria-label="Item name" />' +
            '<input type="text" class="gro-scan-qty" data-idx="' + i + '" value="' + escapeHtml(it.quantity || '') + '" placeholder="Qty" aria-label="Quantity" />' +
            (it.confidence === 'low' ? '<span class="gro-scan-low">Check</span>' : '') +
          '</div>';
        }).join('') +
      '</div>' +
      '<div class="gro-scan-actions">' +
        '<button type="button" class="gro-scan-cancel" data-gro="scan-close">Cancel</button>' +
        // .btn-gold, not apricot: Shop's LIST step already spends its one
        // apricot on .gro-primary ("Start the trip") — DESIGN_SYSTEM §2
        // rule 5. Every other body-level confirm sheet in the app
        // (week-sheet-back, reset-confirm, dinner-confirm-add) uses this
        // same class for exactly this reason.
        '<button type="button" class="gro-scan-save btn-gold" data-gro="scan-save">Add to the list</button>' +
      '</div>';
  }

  async function groScanUploadPhoto(file) {
    groScanOpenSheet();
    groScanRenderLoading();
    try {
      var form = new FormData();
      form.append('photo', file);
      var res = await fetch('/api/grocery-list/scan', { method: 'POST', body: form });
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) {
        groScanRenderError(data.detail || "I couldn't read that photo — try again.");
        return;
      }
      groScanState.items = (data.items || []).map(function (it) {
        return { item: it.item, quantity: it.quantity || '', category: it.category || 'other', confidence: it.confidence, keep: true };
      });
      groScanRenderReview();
    } catch (err) {
      console.warn('Grocery list scan failed:', err);
      groScanRenderError("I couldn't reach Pomona's servers — try again in a moment.");
    }
  }

  // Confirms through /api/grocery-list/confirm-scan, which goes straight
  // through tools.add_grocery_items — the same add path (and duplicate
  // quantity consolidation) a typed item uses, so a confirmed scanned item
  // behaves exactly like one added any other way.
  async function groScanSave() {
    var toSave = groScanState.items.filter(function (it) { return it.keep !== false && it.item.trim(); });
    if (!toSave.length) { groScanCloseSheet(); return; }
    var saveBtn = document.querySelector('.gro-scan-save');
    if (saveBtn) saveBtn.disabled = true;
    try {
      var res = await fetch('/api/grocery-list/confirm-scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items: toSave.map(function (it) { return { item: it.item.trim(), quantity: it.quantity, category: it.category }; })
        })
      });
      if (!res.ok) throw new Error('save failed');
      var result = await res.json().catch(function () { return {}; });
      groScanCloseSheet();
      var addedCount = (result.added || []).length;
      var mergedCount = (result.merged_with_existing || []).length;
      var parts = [];
      if (addedCount) parts.push(groPlural(addedCount, 'item', 'items') + ' added');
      if (mergedCount) parts.push(groPlural(mergedCount, 'item', 'items') + ' combined with what was already on the list');
      showToast(parts.length ? parts.join(', ') + '.' : 'Added to the list.');
      await loadGrocery();
    } catch (err) {
      console.warn('Grocery list confirm-scan failed:', err);
      showToast("Couldn't save those — try again.");
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  // DOM wiring for the sheet above (scrim/handle/close/file-input
  // listeners) lives with the other body-level sheets' wiring, near
  // weekSheetScrim — see groScanSheetEl there. Kept out of this function
  // cluster on purpose: tests/test_grocery_fast_sort.py runs this whole
  // region under Node against a stub with no `document`, and every other
  // handler here is a plain function it can call directly rather than
  // code that touches the DOM at load time.

  // Finishing a stop: everything in the trolley becomes purchased (which is
  // what actually writes it into the kitchen's inventory — see
  // tools.mark_grocery_item), then the trip is recorded. The trip row is
  // bookkeeping and never blocks the flow, which is why it is caught
  // separately.
  async function groFinishStore(store) {
    var data = groceryState.data || await groLoadAllData();
    var s = data.stores[store];
    var inCart = s ? s.inCart.slice() : [];
    // Every stop carries the shopless things (see groTripItems), so
    // whatever of them is in the trolley here is committed here — and here
    // that means ALL of them, not the decided-only set the screen draws.
    // The filter is right for the display (a stop should not show a row
    // that was never on it) and wrong for the commit: a row is in the
    // trolley only because somebody put it there, and an undecided one can
    // arrive there when a store is added or a preference cleared mid-trip.
    // Filter the commit too and that row is committed by nothing and drawn
    // by nothing — in_cart forever, on no screen, with the receipt quietly
    // under-reporting. Something physically in the cart has been bought.
    var anyBucket = data.stores['Unassigned'];
    inCart = inCart.concat(anyBucket ? anyBucket.inCart.slice() : []);
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
      groceryState.tripDone = {};
      groceryState.tripLastDone = null;
    }
    groceryState.inCartOpen = false;
    goGroceryStep('trip');
  }

  // ---------- Sorting many things at once ----------
  // Both fast paths land here, and so does their undo: one request carrying
  // every row's answer, rather than one request per row. See
  // tools.set_grocery_items_stores for why that is a route of its own.
  //
  // `previous` is captured from the rows as they stand BEFORE the write —
  // each one's store AND whether anybody had answered for it — so Undo puts
  // the list back exactly as it was. Restoring everything to "unsorted"
  // instead would be a different list from the one the household had a
  // moment ago: anything already answered "Any", or already tagged to a
  // shop it was about to be moved off, would come back as an open question.
  function groBulkAssign(assignments, previous, doneMessage) {
    if (!assignments.length) return;
    groDo(function () {
      return groPost('/api/grocery-list/store-bulk', { assignments: assignments, remember: false });
    }, "Couldn't sort those — try again.").then(function (ok) {
      if (!ok) return;
      groceryState.sortAllPicks = {};
      groceryState.bulkUndo = previous;
      groOfferBulkUndo(doneMessage);
    });
  }

  function groOfferBulkUndo(message) {
    showToast(message, { label: 'Undo', onClick: groRunBulkUndo }, GRO_UNDO_MS);
  }

  // The payload is held until the undo actually SUCCEEDS. Clearing it on the
  // tap made "try again" a sentence with nothing behind it: a failed undo
  // took the chip away with the only record of what the rows used to be, and
  // forty rows sat at a shop nobody chose with no way back. The server side
  // is all-or-nothing (set_grocery_items_stores commits once), so a failure
  // means nothing moved and this payload still describes the list exactly.
  function groRunBulkUndo() {
    var undo = groceryState.bulkUndo;
    if (!undo) return;
    groDo(function () {
      return groPost('/api/grocery-list/store-bulk', { assignments: undo, remember: false });
    }, "Couldn't undo that — try again.").then(function (ok) {
      if (ok) {
        groceryState.bulkUndo = null;
        // A retry that worked has to replace the failure toast, not sit
        // underneath it: the previous "tap Undo to try again" line stays up
        // for the rest of its window otherwise, contradicting the list it is
        // sitting on. The chip is already inert by then, so it is the words
        // that mislead.
        showToast('Put back where they were.', null, GRO_UNDO_MS);
        return;
      }
      groOfferBulkUndo('Couldn’t undo that — tap Undo to try again.');
    });
  }

  function groPreviousStores(items) {
    return items.map(function (it) {
      return { item_id: it.id, store: it.store || '', decided: groItemDecided(it) };
    });
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
        //
        // WHERE NEXT is the exception, and it is still "up one level,
        // named": its parent is the stop you were just at, not the list.
        // Reopening it puts the shop back among the choices — what was
        // already bought there stays bought, because it was.
        if (groceryState.step === 'next' && groceryState.tripLastDone) {
          var reopen = groceryState.tripLastDone;
          delete groceryState.tripDone[reopen];
          groceryState.tripLastDone = null;
          var reopenAt = (groceryState.tripStops || []).indexOf(reopen);
          groceryState.inCartOpen = false;
          goGroceryStep('trip', reopenAt === -1 ? undefined : { tripIndex: reopenAt });
          return;
        }
        goGroceryStep('list');
        return;

      // The badge is still the only way in, and for a handful it still opens
      // the queue directly: a menu of ways to answer three questions costs
      // more than answering them. Past GRO_FAST_SORT_MIN the fast paths come
      // first, and the queue is one of the three things they offer.
      case 'goto-sort': {
        var toSort = groceryState.data ? groUnsorted(groceryState.data).length : 0;
        goGroceryStep(toSort >= GRO_FAST_SORT_MIN ? 'sorthow' : 'sort');
        return;
      }

      case 'goto-sortall':
        groceryState.sortAllPicks = {};
        goGroceryStep('sortall');
        return;

      case 'goto-sort-one':
        goGroceryStep('sort');
        return;

      // "Put all 40 at Loblaws" — every unsorted thing, one request, one
      // undo. Nothing already answered is touched: groUnsorted is the queue,
      // so a row the household deliberately put somewhere else keeps its
      // answer.
      case 'sort-all-at': {
        if (!groceryState.data) return;
        var bulkStore = el.dataset.store || '';
        var bulkItems = groUnsorted(groceryState.data);
        el.disabled = true;
        groBulkAssign(
          bulkItems.map(function (it) { return { item_id: it.id, store: bulkStore, decided: true }; }),
          groPreviousStores(bulkItems),
          groPlural(bulkItems.length, 'thing', 'things') + ' at ' + bulkStore + '.'
        );
        return;
      }

      // One row of SORT ALL. Staged in memory and repainted in place — no
      // request, no re-render, so the list cannot move under the thumb of
      // someone working down forty rows.
      case 'sortall-pick': {
        groceryState.sortAllPicks[id] = el.dataset.store || '';
        var row = el.closest('.gro-sortall-row');
        if (row) {
          var chips = row.querySelectorAll('.gro-pill');
          for (var ci = 0; ci < chips.length; ci++) {
            var on = chips[ci] === el;
            chips[ci].classList.toggle('gro-pill-on', on);
            chips[ci].setAttribute('aria-pressed', String(on));
          }
        }
        return;
      }

      case 'sortall-save': {
        if (!groceryState.data) return;
        var allItems = groUnsorted(groceryState.data);
        var allFallback = groMostUsedStore(groceryState.data);
        el.disabled = true;
        groBulkAssign(
          allItems.map(function (it) {
            return { item_id: it.id, store: groSortAllPick(it, allFallback), decided: true };
          }),
          groPreviousStores(allItems),
          groPlural(allItems.length, 'thing', 'things') + ' sorted.'
        );
        return;
      }

      case 'start-trip':
        groStartTrip();
        return;

      case 'add':
        // Straight to /api/grocery-list/add — see groAddItem. The ask bar
        // is still there, above the tab bar, for anything wordier.
        groAddItem();
        return;

      case 'scan-open':
        // Opens the hidden file input; the review sheet itself lives at
        // body level (see groScanUploadPhoto) since position:fixed has to
        // sit outside this panel's stacking/scroll context.
        groScanOpenPicker();
        return;

      case 'expand-store':
        groceryState.listExpanded[el.dataset.store] = true;
        renderGrocery();
        return;

      // ----- the LIST row's quiet ⋯ (see groRowMenuHtml) -----
      case 'row-menu':
        groceryState.openRowId = groceryState.openRowId === id ? null : id;
        renderGrocery();
        return;

      case 'row-qty': {
        var rowQtyPanel = groPanel();
        var rowQtyInput = rowQtyPanel && rowQtyPanel.querySelector('#gro-rowqty-' + id);
        if (!rowQtyInput) return;
        var rowQty = rowQtyInput.value.trim();
        el.disabled = true;
        groDo(function () {
          return groPost('/api/grocery-list/' + id + '/update', { quantity: rowQty });
        }, "Couldn't save that — try again.").then(function (ok) {
          if (!ok) return;
          groceryState.openRowId = null;
          renderGrocery();
        });
        return;
      }

      // The old move / not-this-time is the "Any" pill here: an empty store
      // clears the row's store for this week without forgetting the
      // remembered item->store preference (see set_grocery_item_store), so
      // "not this time" is literally true. It now counts as an ANSWER, the
      // same as SORT's "Any" — this used to drop the row back into the
      // to-sort queue, and a person who has just said "no particular shop"
      // being asked where it goes is the annoyance this whole slice exists
      // to remove.
      case 'row-store': {
        var rowStore = el.dataset.store;
        el.disabled = true;
        var rowStoreResult = null;
        groDo(function () {
          return groPost('/api/grocery-list/' + id + '/store', { store: rowStore })
            .then(function (r) { rowStoreResult = r; return r; });
        }, "Couldn't move that — try again.").then(function (ok) {
          if (!ok) return;
          groceryState.openRowId = null;
          renderGrocery();
          if (rowStoreResult && rowStoreResult.needs_confirmation) {
            groOfferRememberToast(rowStoreResult.item, rowStoreResult.store, id);
          }
        });
        return;
      }

      case 'row-exclude':
        el.disabled = true;
        groceryState.openRowId = null;
        groDo(function () {
          return groPostEmpty('/api/grocery-list/' + id + '/exclude');
        }, "Couldn't update that — try again.");
        return;

      case 'row-remove': {
        var goneName = el.dataset.name || 'That';
        var goneQty = el.dataset.qty || '';
        var goneCat = el.dataset.cat || 'other';
        var goneStore = el.dataset.store || '';
        el.disabled = true;
        groceryState.openRowId = null;
        var goneStapleId = null;
        groDo(function () {
          return groPostEmpty('/api/grocery-list/' + id + '/remove')
            .then(function (r) { goneStapleId = r && r.staple_id ? r.staple_id : null; });
        }, "Couldn't remove that — try again.").then(function (ok) {
          if (!ok) return;
          showToast(goneName + ' off the list', {
            label: 'Undo',
            onClick: function () {
              // A staple's line was soft-removed and counted as "not this
              // trip", so its undo is the staple's own: the same row comes
              // back and the skip is taken back with it.
              if (goneStapleId) {
                groDo(function () {
                  return groPostEmpty('/api/staples/' + goneStapleId + '/undo');
                }, "Couldn't put that back — try again.");
                return;
              }
              // /remove is a hard delete (remove_grocery_item), so the undo
              // puts the LINE back rather than the row: same name, same
              // quantity, same section, then its store again if it had one.
              // A new id, the same list — which is what the person meant.
              groDo(function () {
                return groPost('/api/grocery-list/add', { item: goneName, quantity: goneQty, category: goneCat })
                  .then(function (r) {
                    var backId = r && r.item_id;
                    if (!goneStore || !backId) return r;
                    return groPost('/api/grocery-list/' + backId + '/store', { store: goneStore });
                  });
              }, "Couldn't put that back — try again.");
            }
          });
        });
        return;
      }

      // Two rows of the same thing, from the quiet line at the top of LIST.
      // The old Review handler, unchanged: keep the first line, remove the
      // rest. The confirm stays because this one is not undoable.
      case 'merge': {
        var mergeIds = el.dataset.ids.split(',');
        if (!window.confirm('Merge these into one line? The extra lines will be removed.')) return;
        el.disabled = true;
        groDo(function () {
          var rest = mergeIds.slice(1);
          return Promise.all(rest.map(function (rid) {
            return groPostEmpty('/api/grocery-list/' + rid + '/remove');
          }));
        }, "Couldn't merge those — try again.");
        return;
      }

      // ----- pre-shop -----
      case 'ps-toggle':
        groceryState.preShopOpen = !groceryState.preShopOpen;
        if (!groceryState.preShopOpen) groceryState.preShopExpanded = false;
        renderGrocery();
        return;

      // ----- staples -----
      case 'staple-decide': {
        var stDecision = el.dataset.decision;
        var stName = el.dataset.name || 'That';
        var stLineId = id;
        el.closest('.gro-staple-line').querySelectorAll('button').forEach(function (b) { b.disabled = true; });
        var stResult = null;
        groDo(function () {
          return groPost('/api/grocery-list/' + stLineId + '/staple', { decision: stDecision })
            .then(function (r) { stResult = r; });
        }, "Couldn't update that — try again.").then(function (ok) {
          if (!ok || !stResult) return;
          var line;
          if (stDecision === 'plenty') {
            line = stName + ' off the list — I\u2019ll ask again in ' + groCadenceSpan(stResult.cadence_days);
          } else if (stResult.just_paused) {
            line = stName + ' paused — three trips skipped. It\u2019s under Staples if you want it back.';
          } else {
            line = stName + ' off the list — I\u2019ll ask again next week';
          }
          showToast(line, {
            label: 'Undo',
            onClick: function () {
              groDo(function () {
                return groPostEmpty('/api/staples/' + stResult.id + '/undo');
              }, "Couldn't undo that — try again.");
            }
          });
        });
        return;
      }

      case 'row-staple': {
        var mkName = el.dataset.name || '';
        var mkQty = el.dataset.qty || '';
        var mkCat = el.dataset.cat || 'other';
        el.disabled = true;
        groceryState.openRowId = null;
        groDo(function () {
          return groPost('/api/staples/add', { item: mkName, quantity: mkQty, category: mkCat });
        }, "Couldn't save that — try again.").then(function (ok) {
          if (ok) showToast(mkName + ' is a staple now — I\u2019ll put it on the list before you run out');
        });
        return;
      }

      case 'staples-toggle':
        groceryState.staplesOpen = !groceryState.staplesOpen;
        renderGrocery();
        return;

      case 'staple-pause':
      case 'staple-resume': {
        var pausing = action === 'staple-pause';
        var pName = el.dataset.name || 'That';
        el.disabled = true;
        groDo(function () {
          return groPostEmpty('/api/staples/' + id + '/' + (pausing ? 'pause' : 'resume'));
        }, "Couldn't update that — try again.").then(function (ok) {
          if (ok) showToast(pausing ? pName + ' paused' : pName + ' back on the rhythm');
        });
        return;
      }

      case 'staple-remove': {
        var rmName = el.dataset.name || 'That';
        el.disabled = true;
        groDo(function () {
          return groPostEmpty('/api/staples/' + id + '/remove');
        }, "Couldn't remove that — try again.").then(function (ok) {
          if (ok) showToast(rmName + ' isn\u2019t a staple any more');
        });
        return;
      }

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
      // Every one of these three keeps the card open. Only stores-prompt-done
      // below ends the question.
      case 'stores-prompt-pick':
        el.disabled = true;
        groSetStoresPromptOpen(true);
        groToggleUsualStore(el.dataset.store).catch(function () {
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
        groSetStoresPromptOpen(true);
        // Cleared BEFORE the write, the same shape groAddItem uses. The card
        // re-renders the moment the chip appears now, and that re-render
        // carries a half-typed name across (groCaptureStoresPromptInput), so
        // a name left in the box would show as a chip AND still be sitting
        // there to be added twice. On failure the catch puts it straight
        // back, where the same carry-across keeps it.
        storesPromptInput.value = '';
        groAddUsualStore(typedStore).catch(function () {
          showToast("Couldn't save that — try again.");
          var freshStoresInput = groPanel() && groPanel().querySelector('#gro-stores-prompt-input');
          if (freshStoresInput) freshStoresInput.value = typedStore;
        }).then(function () { el.disabled = false; });
        return;
      }

      case 'stores-prompt-done':
        el.disabled = true;
        // The answer is already saved, shop by shop — this records that the
        // question was ANSWERED, which is what stops it being asked again.
        // It runs whether or not any shop was picked: "one list is fine" is
        // an answer, and a household that later clears its shops on the
        // Kitchen sheet shouldn't be asked all over again.
        groPost('/api/memory/stores-prompt-dismiss', {}).then(function () {
          groceryState.storesPromptDismissed = true;
          groSetStoresPromptOpen(false);
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
          // The "Any" pill sends an empty store, which on its own reads as
          // never-sorted. The row is marked answered server-side instead
          // (grocery_items.store_decided, set by the same call), so the
          // choice survives a reload — it used to live in a page-view map
          // and the queue asked again on the next refresh.
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
      // Both ticks go through groTick rather than groDo: on the screen at
      // once, queued if there is no signal. These two are the whole of what
      // works offline, on purpose — see the "No signal" section above.
      case 'trip-toggle':
        groTick(id, 'in_cart');
        return;

      case 'uncheck':
        groTick(id, 'needed');
        return;

      case 'toggle-incart':
        groceryState.inCartOpen = !groceryState.inCartOpen;
        renderGrocery();
        return;

      // Finishing a stop ends THAT stop and nothing else. It used to march
      // straight into the next one in snapshot order, which assumed a route
      // nobody had said they were driving; now the household is asked (the
      // WHERE NEXT step), and the trip only ends when they say so or when
      // there is genuinely nothing left to walk into.
      case 'stop-done': {
        var doneStore = groTripStore();
        el.disabled = true;
        groDo(function () {
          return groFinishStore(doneStore);
        }, "Couldn't finish this stop — try again.").then(function (ok) {
          if (!ok) { el.disabled = false; return; }
          groceryState.inCartOpen = false;
          if (doneStore) {
            groceryState.tripDone[doneStore] = true;
            groceryState.tripLastDone = doneStore;
          }
          var stillToGo = groceryState.data ? groRemainingStops(groceryState.data) : [];
          goGroceryStep(stillToGo.length ? 'next' : 'wrap');
        });
        return;
      }

      // WHERE NEXT's answers. The stop is found in the SNAPSHOT rather than
      // in today's list, so picking one cannot renumber the others, and a
      // finished stop is never on this screen to be picked (groRemainingStops
      // filters on tripDone).
      case 'next-stop': {
        var goTo = (groceryState.tripStops || []).indexOf(el.dataset.store);
        if (goTo === -1) return;
        groceryState.inCartOpen = false;
        goGroceryStep('trip', { tripIndex: goTo });
        return;
      }

      case 'trip-end':
        goGroceryStep('wrap');
        return;

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
          // The whole trip just ended, not one stop of it — "Stop saved" was
          // the per-stop line, and saying it here undersold what happened.
          // Count this trip's own purchases (tripBought), never
          // groTotals().done, which sums every purchase the household has
          // ever made.
          var home = groceryState.tripBought;
          groceryState.tripStops = null;
          groceryState.tripIndex = 0;
          groceryState.wrapKept = {};
          groceryState.tripDone = {};
          groceryState.tripLastDone = null;
          goGroceryStep('list');
          showToast(home
            ? 'Trip finished — ' + groPlural(home, 'thing', 'things') + ' home.'
            : 'Trip finished.');
        });
        return;

      case 'flag-toggle':
        groceryState.openFlagKey = groceryState.openFlagKey === el.dataset.key ? null : el.dataset.key;
        renderGrocery();
        return;

      // The shop-done handoff on LIST — see groShopDoneHtml. Through
      // openRecipeFor, like every other way into cook mode, so the back
      // link says the tab this actually came from instead of inheriting
      // whatever an earlier deep link left on cookState (fixed 2026-09-10,
      // found by review).
      case 'shop-done-tonight':
        openRecipeFor(tonightDinnerRecipeTarget(), { label: 'Shop', tab: 'grocery' });
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

  // ---------- Kitchen: the cook's tab ----------
  //
  // Emily's approved design, 2026-09-08. Kitchen answers "what's cooking,
  // and what's in the house?" and nothing else. Its root, top to bottom:
  // the day and how many cooks are in it, "Cooking today" (one line per
  // cook or reheat, with the start-by time the moves engine already
  // works out), "Prep sessions" (the same rows the Cook overview carried,
  // moved here unchanged), "The rest of the week", and two quiet tiles.
  //
  // Three things follow from that and are deliberate:
  //
  //   - There is still NO apricot on this ROOT. The nav blueprint's rule
  //     was "Kitchen has no primary action at all"; that rule changes with
  //     this slice, but only one step deeper: cook mode's "Mark it cooked"
  //     is the tab's apricot, and the root stays quiet. A screen that
  //     lists what is coming is not a screen with something urgent on it.
  //   - Cooking IS here now. Cook mode is a STEP of this tab (see
  //     renderCook below) rather than a state of Meals — the tab you cook
  //     from should not be the tab you plan from, and the cook OVERVIEW
  //     the Meals tab used to carry is this root.
  //   - Everything Kitchen used to say about the household itself — the
  //     "what we know" hero and its four counts, and the "Something not
  //     working?" tile — moved into the Preferences sheet behind the
  //     header gear (see openPrefsSheet). Kitchen is not the settings
  //     drawer any more, so it does not open with a paragraph about the
  //     household.
  //
  // `cookState` (declared with the rest of cook mode, further down) holds
  // the cooker view itself; kitchenState holds only what this root adds.
  var kitchenState = {
    inventory: null,
    // Today's moves (/api/today/moves), read for ONE thing: the "start by
    // 5:35" arithmetic, which is dinner_window minus the recipe's own
    // prep+cook time and lives on the server (app/tools/moves.py) so Today
    // and Kitchen cannot disagree about when to start. No new route: the
    // payload Today already asks for answers this too.
    moves: [],
    loading: false,
    // "+ 3 more cooks" — the rest of the week is three lines until asked.
    restExpanded: false,
    // Set by a caller that wants the root's prep to be the thing you land
    // on rather than the top of the tab — the rating toast's "Show me
    // tomorrow" when tomorrow has prep but no cook. Cleared by the render
    // that honours it, so it never fires twice.
    scrollToPrep: false
  };

  var KITCHEN_ICONS = {
    person:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="3.6"/><path d="M4.8 20.5a7.2 7.2 0 0 1 14.4 0"/></svg>',
    fridge:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5.5" y="2.8" width="13" height="18.4" rx="2.6"/><path d="M5.5 10h13"/><path d="M9 6.4v1.8"/><path d="M9 12.6v2.2"/></svg>',
    book:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H18v15.5H6.5A1.5 1.5 0 0 0 5 20z"/><path d="M5 20a1.5 1.5 0 0 1 1.5-1.5H18V21H6.5A1.5 1.5 0 0 1 5 20z"/><path d="M9 7.5h5.5"/></svg>',
    storefront:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 9.5h14V19a1.8 1.8 0 0 1-1.8 1.8H6.8A1.8 1.8 0 0 1 5 19z"/><path d="M3.5 5.5h17v4h-17z"/></svg>',
    camera:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5h4l1.5-2.5h6L16.5 8.5h4V19a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19z"/><circle cx="12" cy="13.5" r="3.4"/></svg>',
    link:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13.5a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1.2 1.2"/><path d="M14 10.5a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.2-1.2"/></svg>'
  };

  function kitchenPanel() { return panels['kitchen']; }
  function kitchenIsBuilt() { var p = kitchenPanel(); return !!(p && p.dataset.built); }

  function buildKitchenPanel(panel) {
    panel.innerHTML =
      '<div class="kitchen-content">' +
        // The root and the cook step are two children of one panel, one
        // hidden at a time — the same shape Grocery's shopping mode uses,
        // and never a route: /kitchen is the path in both.
        '<div id="kit-root-view">' +
          '<div class="kit-titlerow">' +
            '<span class="kit-eyebrow">What’s cooking</span>' +
            '<span class="kit-hairline"></span>' +
            prefsGearHtml() +
          '</div>' +
          '<h1 class="kit-title">Cook</h1>' +
          '<p class="kit-sub" id="kit-sub"></p>' +
          '<div class="kit-body" id="kit-body"></div>' +
        '</div>' +
        '<div id="kit-cook-view" hidden></div>' +
      '</div>';
    // Two delegated listeners on the one panel: the tiles' own, and cook
    // mode's (onCookClick), which serves both the root's lines and the
    // focused screen. Each reads its own attribute, so neither sees the
    // other's clicks.
    panel.addEventListener('click', onKitchenClick);
    panel.addEventListener('click', onCookClick);
    loadKitchen();
  }

  // Three reads, in parallel, none of which blocks the others: the cooker
  // view (the week's cooks, its prep sessions, its prep tasks), the
  // attention queue, and today's moves for the start-by arithmetic. The
  // inventory line follows separately — it is a nicety on a tile that is
  // quiet by policy and must never hold up the day's cooks.
  async function loadKitchen() {
    if (!kitchenIsBuilt()) return;
    kitchenState.loading = true;
    try {
      var trio = await Promise.all([
        fetch('/api/cooker-view'),
        fetch('/api/attention'),
        fetch('/api/today/moves')
      ]);
      if (!trio[0].ok) throw new Error('cooker-view failed');
      cookState.data = await trio[0].json();
      cookState.attention = trio[1].ok ? ((await trio[1].json()).items || []) : [];
      kitchenState.moves = trio[2].ok ? ((await trio[2].json()).moves || []) : [];
      cookState.loadError = false;
      // Which meal is "tonight" is decided HERE, on a real load, and then
      // pinned — not recomputed on every render. cookTonightIndex prefers
      // an uncooked meal, so recomputing after a write meant that ticking
      // tonight's dinner as cooked threw it out from under the person who
      // had just cooked it. A genuine reload repicks it.
      cookState.tonightIdx = cookTonightIndex(cookState.data.meals || []);
    } catch (err) {
      console.warn('Kitchen lookup failed:', err);
      cookState.loadError = true;
    }
    kitchenState.loading = false;
    // A "Cook this" deep link that arrived before this tab had ever
    // loaded (the common case — Kitchen is lazy-built) asked for one meal
    // in focus, not the root; honour it now that the data is known.
    if (cookState.pendingFocusTarget) {
      var target = cookState.pendingFocusTarget;
      cookState.pendingFocusTarget = false;
      if (!cookState.loadError) {
        var idx = cookResolveFocusIndex(cookState.data.meals || [], target);
        if (idx !== null && cookState.data.meals[idx]) {
          cookEnterFocus(idx);
          loadKitchenInventory();
          return;
        }
      }
    }
    renderCook();
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

  // ---------- Kitchen root: today's cooks ----------

  // One row per meal that happens TODAY, cooks and reheats alike, in slot
  // order as the plan gives them. `move` is the matching entry from
  // /api/today/moves, which is where the start-by arithmetic lives — the
  // row falls back to the plan's own facts when there is no move for it
  // (a day that is not today's, a plan the moves engine has not caught up
  // with) rather than inventing a clock.
  function kitchenTodayRows(meals, moves, todayIso) {
    var byEntry = {};
    (moves || []).forEach(function (m) {
      if ((m.kind === 'cook' || m.kind === 'reheat') && m.entry_id != null) byEntry[m.entry_id] = m;
    });
    var rows = [];
    (meals || []).forEach(function (meal, idx) {
      if (meal.date !== todayIso) return;
      var move = byEntry[meal.entry_id] || null;
      var isReheat = !!meal.is_leftovers;
      // The plan row and the move say the same thing about "cooked" — the
      // move's `done` is read off cooked_status (app/tools/moves.py) — but
      // either one can be the fresher of the two after a tick, so a row is
      // done if either says so rather than whichever happened to reload.
      var done = meal.cooked_status === 'done' || !!(move && move.done);
      rows.push({
        idx: idx,
        entryId: meal.entry_id,
        isReheat: isReheat,
        done: done,
        title: isReheat ? (meal.leftovers_headline || 'Leftovers') : (meal.meal || 'Dinner'),
        line: kitchenTodayLine(meal, move, isReheat, done),
        // "Cook" / "Reheat" while it is still ahead of you, and the past
        // tense of whichever it was once it is done — a reheat night was
        // never cooked, it was eaten (REHEAT_ACTION_LABEL says so too).
        badge: done ? (isReheat ? 'eaten' : 'cooked') : (isReheat ? 'Reheat' : 'Cook')
      });
    });
    return rows;
  }

  // "start by 5:35 · 55 min" for a cook; "leftovers from Sunday · reheat ·
  // 6:30" for a reheat — both read off the move rather than restated here,
  // so the words match the ones Today uses for the same meal.
  function kitchenTodayLine(meal, move, isReheat, done) {
    if (isReheat) return move ? move.detail : 'reheat';
    var bits = [];
    ((move && move.chips) || []).forEach(function (chip) {
      // A cook that is already done has no start-by left to make: the
      // moves payload keeps the chip (it is arithmetic about the slot, not
      // about the tick), so the row drops it rather than telling someone
      // who has just cooked when they should have started.
      if (/^Start by /.test(chip)) { if (!done) bits.unshift('start by ' + chip.slice('Start by '.length)); }
      else bits.push(chip);
    });
    if (!bits.length) {
      var mins = (meal.prep_time_minutes || 0) + (meal.cook_time_minutes || 0);
      if (move && move.time_label) bits.push(move.time_label);
      if (mins) bits.push(mins + ' min');
    }
    return bits.join(' · ');
  }

  // "Monday · 1 cook tonight". "tonight" only while every cook left today
  // really is a dinner — a lunch to make at eleven in the morning is not
  // tonight, and saying so would be the kind of small lie that stops
  // anyone trusting the line.
  //
  // Once anything today has been ticked the count says "left": at eight in
  // the evening with two of three cooked, "3 cooks today" is a number
  // nobody recognises and it reads as though the evening has not started.
  // The done state is the rows' own, which is the plan's and the moves'
  // taken together (kitchenTodayRows).
  function kitchenSubtitle(rows, meals, todayIso) {
    var day = dayName(todayIso, { weekday: 'long' });
    var cooks = rows.filter(function (r) { return !r.isReheat && !r.done; });
    var anyDone = rows.some(function (r) { return r.done; });
    if (!cooks.length) {
      return rows.length ? day + ' · nothing left to cook today' : day + ' · nothing to cook today';
    }
    var allDinner = cooks.every(function (r) {
      var meal = (meals || [])[r.idx];
      return meal && meal.slot === 'dinner';
    });
    var noun = cooks.length === 1 ? 'cook' : 'cooks';
    return day + ' · ' + cooks.length + ' ' + noun + (anyDone ? ' left' : '') +
      (allDinner ? ' tonight' : ' today');
  }

  function kitchenCookingTodayHtml(rows) {
    return '<section class="cook-section">' +
      '<div class="cook-sectionhead">' +
        '<span class="cook-eyebrow cook-eyebrow-warm">Cooking today</span>' +
        '<span class="cook-rule"></span>' +
      '</div>' +
      (rows.length
        ? '<div class="cook-week">' + rows.map(kitchenTodayRowHtml).join('') + '</div>'
        : '<p class="cook-empty">Nothing on the stove today.</p>') +
    '</section>';
  }

  function kitchenTodayRowHtml(row) {
    var checkLabel = row.isReheat
      ? (row.done ? REHEAT_UNDO_LABEL : REHEAT_ACTION_LABEL)
      : (row.done ? 'Mark not cooked' : 'Mark cooked');
    // A reheat is a line, never a way into a recipe — there is no cook
    // here, so there is nothing for a cook screen to hold (Emily,
    // 2026-09-04). Its box still ticks: the dish gets eaten either way.
    var name = row.isReheat
      ? '<span class="cook-week-name">' + escapeHtml(row.title) + '</span>'
      : '<button type="button" class="cook-week-name" data-cook="focus" data-idx="' + row.idx + '" data-at="steps">' +
          escapeHtml(row.title) + '</button>';
    return '<div class="cook-week-item' + (row.done ? ' is-done' : '') + '">' +
      '<div class="cook-week-row">' +
        '<button type="button" class="cook-box' + (row.done ? ' checked' : '') + '" ' +
          'data-cook="check-meal" data-entry-id="' + row.entryId + '" data-next="' + (row.done ? 'pending' : 'done') + '" ' +
          'aria-label="' + escapeHtml(checkLabel) + '">' + COOK_ICONS.check + '</button>' +
        name +
        '<span class="cook-badge' + (row.done || row.isReheat ? '' : ' cook-badge-warm') + '">' +
          escapeHtml(row.badge) + '</span>' +
      '</div>' +
      (row.line ? '<p class="cook-week-sub">' + escapeHtml(row.line) + '</p>' : '') +
    '</div>';
  }

  // Prep that nothing else on this tab shows.
  //
  // Three places a prep_tasks row can surface: its prep day's session
  // (cookPrepSessionsHtml, which only picks up rows dated ON a prep day),
  // the focused cook screen of the meal it feeds (cookFocusPrepTasks,
  // which needs either a meal_plan_entry_id or a related_meal that matches
  // a dish by name), and Today's timeline, which only ever shows today.
  // A general task with neither link, dated on an ordinary day — "Soak the
  // beans", two days out — fell through all three and rendered NOWHERE.
  // A task the app wrote and then hid is worse than one it never wrote, so
  // the root collects the leftovers: every pending row no session and no
  // cook screen already carries, dated, with a tick.
  //
  // Done rows are left out on purpose: this is the "nothing is invisible"
  // net, not a second progress list, and a finished task is not lost.
  function kitchenLoosePrepTasks(data) {
    var tasks = (data && data.prep_tasks) || [];
    if (!tasks.length) return [];
    var shown = {};
    ((data && data.prep_sessions) || []).forEach(function (session) {
      (session.items || []).forEach(function (item) {
        if (item.prep_task_id != null) shown[item.prep_task_id] = true;
      });
    });
    ((data && data.meals) || []).forEach(function (meal) {
      cookFocusPrepTasks(data, meal).forEach(function (t) { shown[t.id] = true; });
    });
    return tasks.filter(function (t) {
      return t.status !== 'done' && !shown[t.id];
    });
  }

  function kitchenPrepTodoHtml(tasks) {
    if (!tasks.length) return '';
    return '<section class="cook-section" id="kit-prep-todo">' +
      '<div class="cook-sectionhead">' +
        '<span class="cook-eyebrow">Prep to do</span>' +
        '<span class="cook-rule"></span>' +
      '</div>' +
      '<div class="cook-week">' +
        tasks.map(function (t) {
          var day = t.task_date ? dayNameShort(t.task_date).toUpperCase() : '';
          return '<div class="cook-week-item">' +
            '<div class="cook-week-row">' +
              '<button type="button" class="cook-box" data-cook="check-prep" ' +
                'data-prep-id="' + t.id + '" data-next="done" aria-label="Mark done">' +
                COOK_ICONS.check +
              '</button>' +
              (day ? '<span class="cook-week-day">' + escapeHtml(day) + '</span>' : '') +
              '<span class="cook-week-name">' + escapeHtml(t.description) + '</span>' +
            '</div>' +
          '</div>';
        }).join('') +
      '</div>' +
    '</section>';
  }

  // The two quiet ways out of the cook's tab and into the house's
  // cupboards. Both are quiet by policy — inventory is background work the
  // core loop never asks anyone to keep up, and there is no apricot on
  // this root.
  function kitchenTilesHtml() {
    return '<div class="kit-tiles">' +
      '<button type="button" class="kit-tile kit-tile-quiet" data-kit="sheet" data-sheet="inventory">' +
        '<span class="kit-tile-icon">' + KITCHEN_ICONS.fridge + '</span>' +
        '<span class="kit-tile-title">Inventory</span>' +
        '<span class="kit-tile-sub" id="kit-inv-sub">' + escapeHtml(kitchenInventoryLine()) + '</span>' +
      '</button>' +
      // There is no recipe browser in this app, and this tile does not
      // pretend there is one: it opens the ask bar on the question, which
      // the assistant answers off list_recipes (app/tools/recipes.py).
      '<button type="button" class="kit-tile kit-tile-quiet" data-kit="recipes">' +
        '<span class="kit-tile-icon">' + KITCHEN_ICONS.book + '</span>' +
        '<span class="kit-tile-title">Recipes</span>' +
        '<span class="kit-tile-sub">Ask me what we’ve saved</span>' +
      '</button>' +
      // Bring in a recipe the household already makes, from a web page —
      // the review-before-save sheet below (recipe import, 2026-09-11).
      // Quiet like its neighbours: an entry point, not a task.
      '<button type="button" class="kit-tile kit-tile-quiet" data-kit="recipe-link">' +
        '<span class="kit-tile-icon">' + KITCHEN_ICONS.link + '</span>' +
        '<span class="kit-tile-title">Add from a link</span>' +
        '<span class="kit-tile-sub">Paste a recipe page and I’ll read it</span>' +
      '</button>' +
    '</div>';
  }

  // The root itself. Rendered by renderCook (below) whenever cook mode is
  // not on screen, so there is one place that decides which of the tab's
  // two screens is showing.
  function renderKitchen() {
    var panel = kitchenPanel();
    if (!panel) return;
    var sub = panel.querySelector('#kit-sub');
    var body = panel.querySelector('#kit-body');
    if (!body) return;
    var todayIso = todayLocalStr();

    if (cookState.loadError || !cookState.data) {
      if (sub) sub.textContent = dayName(todayIso, { weekday: 'long' });
      body.innerHTML =
        '<p class="cook-error">Couldn’t load the kitchen right now — switch tabs and back to try again.' + snwLink() + '</p>' +
        kitchenTilesHtml();
      return;
    }

    var data = cookState.data;
    var meals = data.meals || [];
    var rows = kitchenTodayRows(meals, kitchenState.moves, todayIso);
    if (sub) sub.textContent = kitchenSubtitle(rows, meals, todayIso);

    if (!data.weekly_plan_id) {
      body.innerHTML =
        '<p class="cook-empty">No plan yet this week &mdash; ' +
          '<button type="button" class="cook-empty-link" data-cook="goto-plan">plan one on the Plan tab first</button>.</p>' +
        kitchenTilesHtml();
      return;
    }

    body.innerHTML =
      cookAttentionHtml() +
      kitchenCookingTodayHtml(rows) +
      cookPrepSessionsHtml(data) +
      kitchenPrepTodoHtml(kitchenLoosePrepTasks(data)) +
      cookRestOfWeekHtml(meals, data, todayIso, kitchenState.restExpanded) +
      cookDefrostLinkHtml() +
      cookAheadAskLinkHtml() +
      kitchenTilesHtml();
  }

  function onKitchenClick(e) {
    var target = e.target.closest('[data-kit]');
    if (!target) return;
    var what = target.getAttribute('data-kit');
    if (what === 'sheet') {
      openKitchenSheet(target.getAttribute('data-sheet'));
      return;
    }
    if (what === 'recipes') {
      openAskSheet('What recipes do we have saved?');
      return;
    }
    if (what === 'recipe-link') {
      openRecipeLinkSheet();
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
    // Inventory can be edited in there, and the Kitchen tile counts it, so
    // re-read on the way out. This is the sheet's half of the freshness
    // policy.
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

  // ---------- "Add from a link" (recipe import, 2026-09-11) ----------
  // Paste a link -> Pomona reads the page -> the draft is shown to review
  // and edit -> Save. Nothing is stored until the household says so: the
  // same review-before-save shape as the receipt and fridge scans, with a
  // URL in front instead of a photo. A native sheet built on demand, the
  // way the Something-not-working sheet is, so it is not another iframe.
  //
  // Copy rules (DESIGN_SYSTEM §8): trouble is stated plainly and paired
  // with its way out in the same breath. Every failure below offers the
  // ask bar, because telling Pomona the recipe in chat already works
  // (tools.add_recipe) — the way out is a real one.
  var rliSheetEl = null;
  var rliScrimEl = null;
  var rliDraft = null;

  function buildRecipeLinkSheet() {
    if (rliSheetEl) return;
    rliScrimEl = document.createElement('div');
    rliScrimEl.id = 'rli-scrim';
    rliScrimEl.hidden = true;
    rliSheetEl = document.createElement('div');
    rliSheetEl.id = 'rli-sheet';
    rliSheetEl.hidden = true;
    rliSheetEl.setAttribute('role', 'dialog');
    rliSheetEl.setAttribute('aria-modal', 'true');
    rliSheetEl.setAttribute('aria-labelledby', 'rli-title');
    rliSheetEl.innerHTML =
      '<div class="ask-sheet-handle" id="rli-handle"></div>' +
      '<div class="kit-sheet-titlerow">' +
        '<span class="kit-sheet-title" id="rli-title">Add from a link</span>' +
        '<span class="kit-sheet-hairline"></span>' +
        '<button type="button" class="kit-sheet-close" id="rli-close" aria-label="Close">&times;</button>' +
      '</div>' +
      '<div class="rli-body" id="rli-body"></div>';
    document.body.appendChild(rliScrimEl);
    document.body.appendChild(rliSheetEl);
    rliScrimEl.addEventListener('click', closeRecipeLinkSheet);
    rliSheetEl.querySelector('#rli-handle').addEventListener('click', closeRecipeLinkSheet);
    rliSheetEl.querySelector('#rli-close').addEventListener('click', closeRecipeLinkSheet);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && rliSheetEl && !rliSheetEl.hidden) closeRecipeLinkSheet();
    });
    rliSheetEl.addEventListener('click', onRecipeLinkClick);
  }

  function openRecipeLinkSheet() {
    buildRecipeLinkSheet();
    closeAskSheet();
    closeWeekSheet();
    closeKitchenSheet();
    rliDraft = null;
    renderRecipeLinkAsk('');
    rliScrimEl.hidden = false;
    rliSheetEl.hidden = false;
    var input = rliSheetEl.querySelector('#rli-url');
    if (input) input.focus();
  }

  function closeRecipeLinkSheet() {
    if (!rliSheetEl) return;
    rliScrimEl.hidden = true;
    rliSheetEl.hidden = true;
  }

  // The way out of every failure: say it in the ask bar instead. Prefilled
  // so the household is one paste away rather than starting from nothing.
  function rliAskInsteadHtml() {
    return '<button type="button" class="rli-link" data-rli="ask-instead">' +
      'Tell me the recipe instead</button>';
  }

  function renderRecipeLinkAsk(url, problem) {
    var body = rliSheetEl.querySelector('#rli-body');
    body.innerHTML =
      '<label class="rli-label" for="rli-url">Paste the link</label>' +
      '<input id="rli-url" class="rli-input" type="url" inputmode="url" autocomplete="off" ' +
        'autocapitalize="off" spellcheck="false" placeholder="https://" value="' + escapeHtml(url || '') + '">' +
      (problem
        ? '<p class="rli-problem" role="alert">' + escapeHtml(problem) + '</p>' + rliAskInsteadHtml()
        : '') +
      '<button type="button" class="rli-read" id="rli-read" data-rli="read"' + (url ? '' : ' disabled') + '>Read the recipe</button>';
    var input = body.querySelector('#rli-url');
    var read = body.querySelector('#rli-read');
    input.addEventListener('input', function () { read.disabled = !input.value.trim(); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && input.value.trim()) readRecipeLink();
    });
  }

  function readRecipeLink() {
    var body = rliSheetEl.querySelector('#rli-body');
    var input = body.querySelector('#rli-url');
    var read = body.querySelector('#rli-read');
    var url = (input.value || '').trim();
    if (!url) return;
    read.disabled = true;
    read.textContent = 'Reading\u2026';
    fetch('/api/recipes/import-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url })
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var detail = data && typeof data.detail === 'string' ? data.detail : '';
          if (res.status === 429) detail = 'That\u2019s a few links in a row \u2014 give it a minute and try again.';
          if (!detail) detail = 'I couldn\u2019t read a recipe on that page.';
          throw new Error(detail);
        }
        return data.draft;
      });
    }).then(function (draft) {
      rliDraft = draft;
      renderRecipeLinkReview(draft);
    }).catch(function (err) {
      renderRecipeLinkAsk(url, (err && err.message) || 'I couldn\u2019t read a recipe on that page.');
    });
  }

  function rliIngredientRowHtml(ing) {
    // The draft's store section rides along on the row so the save keeps
    // it; a row the household adds by hand has none and the server
    // guesses one.
    return '<div class="rli-ing" data-category="' + escapeHtml(ing.category || '') + '">' +
      '<input class="rli-input rli-ing-qty" type="text" placeholder="how much" value="' + escapeHtml(ing.qty || '') + '" aria-label="Amount">' +
      '<input class="rli-input rli-ing-item" type="text" placeholder="ingredient" value="' + escapeHtml(ing.item || '') + '" aria-label="Ingredient">' +
      '<button type="button" class="rli-ing-remove" data-rli="remove-ing" aria-label="Remove">&times;</button>' +
    '</div>';
  }

  function rliHost(url) {
    try { return new URL(url).hostname.replace(/^www\./, ''); } catch (e) { return ''; }
  }

  function renderRecipeLinkReview(draft) {
    var body = rliSheetEl.querySelector('#rli-body');
    var host = rliHost(draft.source_url);
    var ings = (draft.ingredients || []).map(rliIngredientRowHtml).join('');
    body.innerHTML =
      // Where it came from and how it was read. A model-read draft says so,
      // because it is a reading of the page rather than a copy of it.
      '<p class="rli-source">' +
        (host ? 'From ' + escapeHtml(host) + '. ' : '') +
        (draft.read_by === 'model'
          ? 'I read this off the page myself, so give it a look before saving.'
          : 'Check it over, then save.') +
      '</p>' +
      '<label class="rli-label" for="rli-name">Name</label>' +
      '<input id="rli-name" class="rli-input" type="text" value="' + escapeHtml(draft.name || '') + '">' +
      '<div class="rli-numbers">' +
        '<label class="rli-label rli-num"><span>Serves</span>' +
          '<input id="rli-servings" class="rli-input" type="number" min="1" inputmode="numeric" value="' + escapeHtml(draft.default_servings || 4) + '"></label>' +
        '<label class="rli-label rli-num"><span>Prep mins</span>' +
          '<input id="rli-prep" class="rli-input" type="number" min="0" inputmode="numeric" value="' + escapeHtml(draft.prep_time_minutes || '') + '"></label>' +
        '<label class="rli-label rli-num"><span>Cook mins</span>' +
          '<input id="rli-cook" class="rli-input" type="number" min="0" inputmode="numeric" value="' + escapeHtml(draft.cook_time_minutes || '') + '"></label>' +
      '</div>' +
      '<div class="rli-label">Ingredients</div>' +
      '<div class="rli-ings" id="rli-ings">' + ings + '</div>' +
      '<button type="button" class="rli-link" data-rli="add-ing">Add an ingredient</button>' +
      '<label class="rli-label" for="rli-steps">Steps <span class="rli-hint">one per line</span></label>' +
      '<textarea id="rli-steps" class="rli-input rli-steps" rows="8">' + escapeHtml((draft.instructions || []).join('\n')) + '</textarea>' +
      '<p class="rli-problem" id="rli-save-problem" role="alert" hidden></p>' +
      '<button type="button" class="btn-primary rli-save" id="rli-save" data-rli="save">Save to my recipes</button>';
  }

  function collectRecipeLinkDraft() {
    var body = rliSheetEl.querySelector('#rli-body');
    var ingredients = [];
    body.querySelectorAll('.rli-ing').forEach(function (row) {
      var item = (row.querySelector('.rli-ing-item').value || '').trim();
      if (!item) return;
      ingredients.push({
        item: item,
        qty: (row.querySelector('.rli-ing-qty').value || '').trim(),
        category: row.getAttribute('data-category') || null
      });
    });
    var steps = (body.querySelector('#rli-steps').value || '').split('\n')
      .map(function (s) { return s.replace(/^\s*\d+[.)]\s*/, '').trim(); })
      .filter(Boolean);
    function num(id) {
      var v = parseInt((body.querySelector(id) || {}).value, 10);
      return isNaN(v) || v <= 0 ? null : v;
    }
    return {
      name: (body.querySelector('#rli-name').value || '').trim(),
      ingredients: ingredients,
      instructions: steps,
      default_servings: num('#rli-servings') || 4,
      prep_time_minutes: num('#rli-prep'),
      cook_time_minutes: num('#rli-cook'),
      cuisine: (rliDraft && rliDraft.cuisine) || '',
      main_protein: (rliDraft && rliDraft.main_protein) || '',
      source_url: (rliDraft && rliDraft.source_url) || ''
    };
  }

  function saveRecipeLink() {
    var body = rliSheetEl.querySelector('#rli-body');
    var save = body.querySelector('#rli-save');
    var problem = body.querySelector('#rli-save-problem');
    var payload = collectRecipeLinkDraft();
    problem.hidden = true;
    if (!payload.name) {
      problem.textContent = 'Give it a name first.';
      problem.hidden = false;
      body.querySelector('#rli-name').focus();
      return;
    }
    save.disabled = true;
    save.textContent = 'Saving\u2026';
    fetch('/api/recipes/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) throw new Error((data && typeof data.detail === 'string' && data.detail) || 'That didn\u2019t save \u2014 try again.');
        return data;
      });
    }).then(function (saved) {
      body.innerHTML =
        '<p class="rli-done">Saved. \u201c' + escapeHtml(saved.name || payload.name) + '\u201d is one of your recipes now \u2014 ' +
          'ask me to put it on the week whenever you like.</p>' +
        '<button type="button" class="rli-read" data-rli="another">Add another</button>' +
        '<button type="button" class="rli-link" data-rli="close">Done</button>';
    }).catch(function (err) {
      problem.textContent = (err && err.message) || 'That didn\u2019t save \u2014 try again.';
      problem.hidden = false;
      save.disabled = false;
      save.textContent = 'Save to my recipes';
    });
  }

  function onRecipeLinkClick(e) {
    var target = e.target && e.target.closest && e.target.closest('[data-rli]');
    if (!target) return;
    var what = target.getAttribute('data-rli');
    if (what === 'read') readRecipeLink();
    else if (what === 'save') saveRecipeLink();
    else if (what === 'add-ing') {
      var list = rliSheetEl.querySelector('#rli-ings');
      list.insertAdjacentHTML('beforeend', rliIngredientRowHtml({ item: '', qty: '' }));
      var rows = list.querySelectorAll('.rli-ing-item');
      rows[rows.length - 1].focus();
    }
    else if (what === 'remove-ing') target.closest('.rli-ing').remove();
    else if (what === 'another') openRecipeLinkSheet();
    else if (what === 'close') closeRecipeLinkSheet();
    else if (what === 'ask-instead') {
      closeRecipeLinkSheet();
      openAskSheet('Save this recipe for me: ');
    }
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

  // ---------- Snacks render too (2026-09-08, "snack-swap-applies") ----------
  // day.snacks is a LIST (a day normally has two), not a fourth WEEK_SLOTS
  // entry — WEEK_SLOTS stays exactly the three real meals on purpose, so
  // weekCountsLabel keeps never counting a snack as a cook, and the 21-slot
  // guarantee stays a guarantee about the three meals it was written for.
  // (It used to say "and every open-slot count below" too. That is no
  // longer true and the change was deliberate: the Approve button counts
  // all four now — see countOpenSlots — because an open snack is something
  // left to decide and the button is a promise that nothing is. What did
  // NOT change is WEEK_SLOTS itself, which four other readers depend on.)
  // Snack entries get their own slot KEYS instead: 'snack' for day.snacks[0] —
  // matching day.snack, the shorthand the backend already hands back, and
  // the bare 'snack' the chat's action card sends (app/main.py ChatAction,
  // swap_meal_in_plan) — then 'snack2', 'snack3', ... for the rest. That
  // means a pendingDayFocus naming slot 'snack' (see applyPendingDayFocus)
  // rings the first snack card without any extra lookup.
  function isSnackSlot(slot) {
    return slot === 'snack' || /^snack\d+$/.test(slot);
  }
  function snackSlotKey(i) {
    return i === 0 ? 'snack' : 'snack' + (i + 1);
  }
  // Resolves either a real WEEK_SLOTS key or a snack key back to its entry,
  // so daySlotCardHtml/slotEyebrow/slotActionsHtml/mealStepHtml can share one
  // lookup instead of every caller knowing snacks live in a list.
  function daySlotEntry(day, slot) {
    if (!day) return null;
    if (slot === 'snack') return day.snack || (day.snacks && day.snacks[0]) || null;
    var m = /^snack(\d+)$/.exec(slot);
    if (m) return (day.snacks && day.snacks[Number(m[1]) - 1]) || null;
    return day[slot];
  }
  // Every slot KEY a day actually holds, in the order it is eaten: the
  // three real meals, then one key per snack the day has. WEEK_SLOTS
  // answers "which slots does the app guarantee"; this answers "which
  // slots is this day made of", and the two are different questions —
  // asking the first one where the second was meant is how an open snack
  // stayed invisible to the Approve button.
  function daySlotKeys(day) {
    return WEEK_SLOTS.concat(((day && day.snacks) || []).map(function (_, i) {
      return snackSlotKey(i);
    }));
  }
  // The word a sentence should use for this slot — every real slot by its
  // own name, any snack key collapsed to the plain word "snack" (nobody
  // says "swap Thursday's snack2").
  function slotWord(slot) {
    return isSnackSlot(slot) ? 'snack' : slot;
  }
  // "Snack" when the day has one, "Snack 1"/"Snack 2" when it has more —
  // SLOT_LABELS has no fixed word for this because the count decides it.
  function slotEyebrowLabel(day, slot) {
    if (!isSnackSlot(slot)) return SLOT_LABELS[slot];
    var snacks = (day && day.snacks) || [];
    if (snacks.length <= 1) return 'Snack';
    var idx = slot === 'snack' ? 1 : Number(slot.slice(5));
    return 'Snack ' + idx;
  }
  // "Has a real recipe to make" vs. grab-and-go — the same test
  // weekly_plan.py's _is_cook applies server-side (source outside
  // leftovers/takeout), narrowed by the one more thing this payload can
  // show that isn't already implied by source: an actual prep/cook time
  // (cookTimeChip's own `meta`). A freeform "apple slices" snack has
  // neither a recipe row nor a time, so it reads as what it is — nothing to
  // cook — rather than earning the same apricot dot a real cook gets.
  function isRealCook(entry) {
    return !!(entry && entry.source !== 'leftovers' && entry.source !== 'takeout' && entry.meta);
  }
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
        // Meals is Plan, full stop (Emily, 2026-09-08). The Plan | Cook
        // segmented control that used to sit here is gone with the state it
        // switched to: cooking is a step of the Kitchen tab now, so this
        // tab has one job and no control saying otherwise.
        //
        // What sits in its place is the Preferences gear, which every root
        // screen carries in its header (see prefsGearRowHtml). It is hidden
        // on the Day and Meal steps, the same rule the segmented control
        // followed — a gear belongs to the root of a tab, not to a step
        // inside it.
        prefsGearRowHtml('meals-gear-row') +
        '<div id="week-plan-view">' +
        // The one band that belongs to the WEEK rather than to any day of
        // it, above the card and hidden on the Day and Meal steps (see
        // renderMealsStep). It carries at most one thing at a time
        // (renderWeekApproval): a draft's hard-allergen "One thing to
        // settle" card, or — until it is dismissed — the approved week's
        // receipt and its two remaining asks. The old #week-review-band
        // that used to sit above it is gone: review IS the week card now,
        // and its Approve button sits under the card (weekStepHtml).
        '<div id="week-approve-row"></div>' +
        // Week -> Day -> Meal. One container, three renderings; see
        // renderMealsStep.
        '<div id="week-steps"><div class="menu-loading">Loading your week&hellip;</div></div>' +
        '</div>' +
      '</div>';

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
      // Meals and Kitchen are two readings of one week, so anything that
      // reloads the plan reloads the cook's tab with it — a swap, an
      // approval, a chat turn, a reset. Doing it here rather than at each
      // of those six call sites is the point: a seventh call site added
      // later gets the behaviour for free instead of being the next thing
      // that goes stale. It is a no-op until someone has actually opened
      // Kitchen, so this costs nothing for a household that never does.
      refreshKitchenPanel();
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
    // A DRAFT's subtitle says whose turn it is, not the shape of the week:
    // the shape is what the seven rows underneath are for, and the one
    // thing the badge can't say on its own is that nothing happens until
    // somebody here decides (Emily's approved design, 2026-09-08).
    if (state === 'draft') {
      sub.push('a draft, your turn');
    } else {
      var counts = weekCountsLabel(days);
      if (counts) sub.push(counts);
    }
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

  // One line per snack, same legend as weekRowLineHtml, narrowed by one
  // more rule (Emily's approved 2026-09-08 design): a snack only earns the
  // apricot cook dot when it's a real recipe (isRealCook) — most are
  // grab-and-go, and an apricot dot on a whole row of those would claim
  // somebody cooks food nobody actually cooks. A day with zero snacks
  // contributes nothing here, so an ordinary row is unchanged.
  function weekSnackLineHtml(entry) {
    var dot = 'is-none';
    var quiet = ' is-quiet';
    var text;
    if (entry.state === 'planned') {
      quiet = '';
      if (entry.source === 'leftovers') { dot = 'is-ahead'; text = mealDisplayName(entry); }
      else if (isRealCook(entry)) { dot = 'is-cook'; text = entry.title; }
      else { text = entry.title; }
    } else if (entry.state === 'open') {
      dot = 'is-open';
      text = 'Pick a snack';
    } else if (entry.state === 'planned_empty') {
      text = awayLineFor(entry) || entry.title || 'Nothing planned';
    } else {
      text = entry.title || 'Nothing planned';
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
        (day.snacks || []).map(weekSnackLineHtml).join('') +
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
      weekNotesHtml(data) +
      // Everything rare is one tap away and nothing rare is on the page.
      // ABOVE the decision, not below it, since the decision became a dock
      // (rule 2): a sticky strip's flow position has to be the end of the
      // screen, or at the bottom of the scroll it lifts off the ask bar and
      // leaves this row stranded underneath it. Rule 2 puts rare actions
      // behind the "···" rather than beside the dock's button anyway, so
      // they were never candidates to ride along inside it.
      '<div class="wk-foot">' +
        '<button type="button" class="wk-foot-link" id="wk-plan-next">' +
          escapeHtml(planEntryLabel(dayCount, 'next', false)) + ' ›</button>' +
        '<button type="button" class="wk-foot-more" id="wk-more" aria-haspopup="dialog">More ···</button>' +
      '</div>' +
      weekDecideHtml(data);
  }

  // The quiet lines under the card. A SOFT conflict — somebody at the table
  // isn't keen — gets no card and no button: it is a preference, it never
  // gates approval, and it is said once in one line (server-worded, see
  // coordination._soft_note). The plates note rides here too, for the one
  // week it is ever shown: it explains something about the week being
  // approved, and it used to live in the review band that this design
  // removes.
  function weekNotesHtml(data) {
    var notes = [];
    if (data.plates_note) notes.push(data.plates_note);
    if (weekPlanState(data) === 'draft' && data.soft_note) notes.push(data.soft_note);
    if (!notes.length) return '';
    return '<div class="wk-notes">' + notes.map(function (n) {
      return '<div class="wk-note">' + escapeHtml(n) + '</div>';
    }).join('') + '</div>';
  }

  // A draft's decision, directly under the week it is about — the screen's
  // one apricot (Rule 5) and one quiet text action beside it. This is what
  // replaced #week-review-band: the review is the card above, so the band's
  // eyebrow, its status line and its grocery promise all went, and what is
  // left is the decision itself.
  function weekDecideHtml(data) {
    if (weekPlanState(data) !== 'draft') return '';
    var openCount = countOpenSlots(data);
    return '<div class="wk-decide dock">' +
      // The way into the Review step, above the decision it is for: read
      // the week properly, then approve it. Secondary, not a second apricot
      // (Rule 5) — the decision is still the primary here.
      '<button type="button" class="wk-check-btn" id="week-check-btn">Check the week</button>' +
      // Approving with a slot still open is allowed, but named — never a
      // silent shortfall. That is the only thing allowed to reword this
      // button (Emily's copy is "Approve this week").
      '<button type="button" class="btn-gold week-approve-btn" id="week-approve-btn">' +
        (openCount ? escapeHtml(approveWithOpenLabel(data, openCount)) : 'Approve this week') +
      '</button>' +
      '<button type="button" class="week-reset-link week-tweak-link" id="week-tweak-btn">Tweak it with me</button>' +
      // Empty and hidden until "Try again" is tapped in the More sheet —
      // the rebuild is a ~30-second call, so this is where the rotating
      // waiting line goes (static/waiting-lines.js). It followed the redo
      // actions out of the band and into the sheet's handler, but the line
      // itself has to be on the page you are looking at, not inside a
      // sheet that closes the moment you tap.
      '<div class="week-redo-waiting waiting-line" id="week-redo-waiting" hidden></div>' +
    '</div>';
  }

  // ---------- REVIEW: the two views ----------
  // Emily's approved design, 2026-09-09, Option A. The week card above
  // answers "is this settled?" at a glance; this step is for actually
  // checking it before approving, and it does that by reading the SAME week
  // two ways rather than by putting more of it on one screen at once.
  // Julia's report was that a week is too much to take in — three meals and
  // two snacks across seven days is 35 things, and a list of all 35 is the
  // problem rather than the answer.
  //
  //   WHAT WE'RE EATING  grouped by meal type, each dish ONCE with the
  //                      number of days it covers. This is the view that
  //                      makes "we're having chilli three times" visible,
  //                      which no day-by-day list ever does.
  //   WHICH DAYS         one card per day carrying DINNER — the thing
  //                      people actually check — with the other four one
  //                      tap away, and a day nobody is home saying so.
  //
  // A fourth step of the Meals tab (week -> review -> day -> meal), not a
  // page and not a first-run screen: any draft is checkable at any time,
  // and an approved week still reads back here.
  var REVIEW_GROUP_LABELS = {
    breakfast: 'Breakfasts', lunch: 'Lunches', dinner: 'Dinners', snack: 'Snacks'
  };
  // How a household counts days of a given meal. "4 mornings", not "4
  // breakfasts" — Emily's own phrasing, and it is what a person says.
  var REVIEW_SLOT_NOUNS = {
    breakfast: ['morning', 'mornings'],
    lunch: ['lunch', 'lunches'],
    dinner: ['night', 'nights'],
    snack: ['day', 'days']
  };
  // view: which of the two is showing. openDays: which day cards in the
  // second view are expanded, keyed by date so a re-render keeps them open.
  // busy/trouble: the stepper's one in-flight call, exactly one at a time
  // for the same reason swapState is (see it) — a person is tapping one
  // stepper, not three. picking: which dish row (by its index into
  // reviewState.dishes) has its day picker open, or null.
  // troubleFor: WHICH dish row the trouble line belongs under, as
  // { slot, name } — see reviewTroubleIsFor for why it is not an index.
  // It used to render once at the foot of the whole body, which on a real
  // week puts it below every group — measured at 390px, 2114px down an
  // 844px screen. So a refused tap moved nothing, said nothing where the
  // finger was, and left its explanation 1270px away, which reads as a
  // control that does nothing at all. A sentence has to arrive where the
  // tap was.
  //
  // KNOWN AND DELIBERATELY LEFT, both of them identical before any of this
  // and neither risking data: the sentence outlives its own tap — it
  // survives a switch to "Which days", renders nowhere there, and comes
  // back on the way in — and a refusal does not re-read the week, so a
  // count that has gone stale underneath stays stale until the next write.
  // Both are questions about how long an answer should live on this
  // screen, which is a decision rather than a bug fix.
  var reviewState = {
    view: 'eating', openDays: {}, busy: null, trouble: '', troubleFor: null, picking: null,
  };

  var RV_MINUS_SVG = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" ' +
    'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true">' +
    '<path d="M6 12h12"/></svg>';
  var RV_PLUS_SVG = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" ' +
    'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true">' +
    '<path d="M6 12h12"/><path d="M12 6v12"/></svg>';
  var RV_CHEVRON_SVG = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" ' +
    'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" ' +
    'aria-hidden="true"><path d="M8 10l4 4 4-4"/></svg>';

  function reviewSlotNoun(slot, n) {
    var pair = REVIEW_SLOT_NOUNS[slot] || REVIEW_SLOT_NOUNS.snack;
    return n === 1 ? pair[0] : pair[1];
  }

  // Every dish of the week, once, grouped by meal type and carrying the
  // days it covers.
  //
  // Two rules do all the work here. A dish is identified by the name it
  // READS as (mealDisplayName), which for a made-ahead or leftover night is
  // the source dish rather than the "Made ahead — Sunday's Egg White Bites"
  // sentence — so a chain collapses into one row covering both days without
  // this function knowing anything about chains. And a COOK is the same
  // test weekly_plan._is_cook applies server-side (planned, and not
  // leftovers or takeout), deliberately, so this screen and the approved
  // week's receipt can never put different numbers on the same week.
  //
  // Only `planned` slots are dishes. An `open` slot is a question and a
  // `planned_empty` one is a night nobody is home — neither is something
  // the household is eating, and counting either would be the same mistake
  // three earlier bugs made.
  function reviewEatingGroups(days) {
    var byType = {};
    var order = ['breakfast', 'lunch', 'dinner', 'snack'];
    (days || []).forEach(function (day) {
      var seen = WEEK_SLOTS.map(function (s) { return { type: s, entry: day[s] }; });
      (day.snacks || []).forEach(function (e) { seen.push({ type: 'snack', entry: e }); });
      seen.forEach(function (s) {
        var entry = s.entry;
        if (!entry || entry.state !== 'planned') return;
        var name = mealDisplayName(entry);
        if (!name) return;
        var group = byType[s.type] ||
          (byType[s.type] = { slot: s.type, label: REVIEW_GROUP_LABELS[s.type], dishes: [], index: {} });
        var key = name.trim().toLowerCase();
        var dish = group.index[key];
        if (!dish) {
          dish = group.index[key] = { slot: s.type, name: name, days: [], cooks: 0 };
          group.dishes.push(dish);
        }
        // days arrive in the week's own order, so the LAST of these is the
        // furthest-out day, and that is the one the stepper takes away.
        // Within one meal type that also happens to be a reheat rather
        // than the cook that feeds it — a chain's source sits earlier than
        // the night it feeds. It is NOT a general invariant, and an earlier
        // version of this comment claimed it was: a dinner cooked double
        // for the next day's LUNCH is a source sitting in the Dinners
        // group, and can be last in it. Nothing here relies on the
        // distinction — the server refuses a chain source outright (see
        // tools.drop_dish_from_day), which is where the check belongs,
        // since only it can see the chain.
        // `cooked` rides along because the stepper going DOWN always takes
        // the LAST of these, and a day somebody has already cooked is not
        // one to take away — see reviewDishRowHtml.
        dish.days.push({ date: day.date, entryId: entry.entry_id, cooked: !!entry.cooked });
        if (entry.source !== 'leftovers' && entry.source !== 'takeout') dish.cooks++;
      });
    });
    return order.map(function (t) { return byType[t]; }).filter(Boolean);
  }

  // Said only when it is not already on screen. The stepper beside it
  // already says how many days the dish covers, so repeating that as a
  // subtitle would be exactly the restatement §8 forbids — this line exists
  // for the one fact the count can't carry, which is that some of those
  // days cost no cooking. A dish cooked fresh every time says nothing.
  function reviewCookLine(dish) {
    var n = dish.days.length;
    if (dish.cooks === n) return '';
    if (!dish.cooks) return 'nothing to cook';
    return dish.cooks === 1 ? 'cooked once' : 'cooked ' + dish.cooks + ' times';
  }

  // Which days one more of this dish could go on, and what each of them is
  // currently holding.
  //
  // Going up used to open the ask sheet, because nothing had decided which
  // day an extra one should land on. Emily's answer (2026-09-10) is that
  // nothing should: every candidate day already holds something, so "+"
  // always means REPLACING something, and a rule that picks the victim
  // silently is exactly the failure this app keeps getting caught by. So
  // the strip shows what each day is holding and the household says which
  // one they are willing to spend.
  //
  // Four exclusions, and the middle two are not niceties. A day the dish
  // ALREADY covers is not offered (the stepper's number is the count of
  // those days — offering one back would be a tap that changes nothing).
  // A `planned_empty` slot is NEVER offered: nobody is home, or the
  // household asked for none of that meal, and three separate bugs in this
  // app have come from code reading that state as a free plate. A meal
  // already COOKED is not offered either: the tick is a record, the
  // inventory was depleted against it, and its ingredients are on a list
  // that has been shopped — and today's dinner, ticked off at seven, is
  // not a past day, so isPast does not cover this and never did. And a day
  // genuinely behind us, or outside the plan's own period, is not a day to
  // plan into.
  //
  // Snacks are offered one ENTRY at a time rather than one day at a time,
  // because a day holds two of them by default and they are two different
  // decisions — displacing the apple is not displacing the yogurt beside
  // it. That is also why the write is by entry id all the way down.
  function reviewAddDayOptions(days, dish) {
    var covered = {};
    (dish.days || []).forEach(function (d) { covered[d.date] = true; });
    var out = [];
    (days || []).forEach(function (day) {
      if (!day || day.before_plan_start || day.isPast || covered[day.date]) return;
      var keys = dish.slot === 'snack'
        ? (day.snacks || []).map(function (_, i) { return snackSlotKey(i); })
        : [dish.slot];
      keys.forEach(function (key) {
        var e = daySlotEntry(day, key);
        if (!e || (e.state !== 'planned' && e.state !== 'open')) return;
        if (e.cooked) return;
        out.push({
          date: day.date,
          entryId: e.entry_id,
          open: e.state === 'open',
          // What is on it, in the words the rest of this screen uses for
          // it — a made-ahead night reads as the dish, not as the whole
          // "Made ahead — Sunday's Egg White Bites" sentence.
          holding: e.state === 'open' ? 'Your call' : mealDisplayName(e),
          // Only when the day has more than one of them to tell apart.
          eyebrow: (dish.slot === 'snack' && (day.snacks || []).length > 1)
            ? slotEyebrowLabel(day, key) : ''
        });
      });
    });
    return out;
  }

  function reviewAddPickerHtml(dish, idx, days) {
    var options = reviewAddDayOptions(days, dish);
    if (!options.length) {
      // Every other day of this meal is already this dish, out, or gone.
      // Said plainly rather than opened as an empty strip.
      return '<div class="rv-pick"><p class="rv-pick-ask">' +
        escapeHtml('There’s no other ' + reviewSlotNoun(dish.slot, 1) +
          ' this week to put it on.') + '</p>' +
        '<button type="button" class="rv-pick-cancel" data-rv-pick-cancel="1">' +
          'Never mind</button></div>';
    }
    return '<div class="rv-pick">' +
      '<p class="rv-pick-ask">' +
        escapeHtml('Which ' + reviewSlotNoun(dish.slot, 1) + '? Each one already has ' +
          'something, and ' + dish.name + ' takes its place.') + '</p>' +
      '<div class="rv-pick-days">' +
        options.map(function (o, i) {
          return '<button type="button" class="rv-pick-day" data-rv-pick="' + idx + '" ' +
              'data-rv-pick-at="' + i + '">' +
            '<span class="rv-pick-when">' +
              escapeHtml(dayName(o.date, { weekday: 'long' })) +
              (o.eyebrow ? ' · ' + escapeHtml(o.eyebrow) : '') +
            '</span>' +
            '<span class="rv-pick-holding' + (o.open ? ' is-quiet' : '') + '">' +
              escapeHtml(o.holding) + '</span>' +
          '</button>';
        }).join('') +
      '</div>' +
      '<button type="button" class="rv-pick-cancel" data-rv-pick-cancel="1">' +
        'Never mind</button>' +
    '</div>';
  }

  function reviewDishRowHtml(dish, idx, days) {
    var n = dish.days.length;
    var cookLine = reviewCookLine(dish);
    var busy = reviewState.busy === idx;
    var picking = reviewState.picking === idx;
    var one = reviewSlotNoun(dish.slot, 1);
    // At one day there is nothing left to take away — a dish you don't want
    // at all is a change, not a smaller number, and Change is the button
    // right beside it.
    //
    // ...and not when the day it would take is one somebody has already
    // COOKED. "−" always takes the LAST day the dish covers (see
    // runDropDishDay), so a dish on two nights whose later one has been
    // ticked had a live control that deleted a cooked record, left the
    // inventory depleted for a meal off the plan, and took an eaten meal's
    // ingredients off the list. The same harm the "+" was fixed for, in
    // the sibling half of the same stepper. Deliberately NOT "drop the
    // last UNCOOKED day instead": that would quietly take a different day
    // from the one the count implies, which is this screen's own recurring
    // bug wearing a different hat. The write refuses it in words too.
    var lastDay = dish.days[n - 1];
    var canDrop = n > 1 && !busy && !(lastDay && lastDay.cooked);
    return '<div class="rv-dish' + (picking ? ' is-picking' : '') + '">' +
      '<div class="rv-dish-said">' +
        '<span class="rv-dish-name">' + escapeHtml(dish.name) + '</span>' +
        (cookLine ? '<span class="rv-dish-cooks">' + escapeHtml(cookLine) + '</span>' : '') +
      '</div>' +
      '<div class="rv-dish-acts">' +
        '<span class="rv-step">' +
          '<button type="button" class="rv-step-btn" data-rv-less="' + idx + '"' +
            (canDrop ? '' : ' disabled') +
            ' aria-label="One fewer ' + escapeHtml(one + ' of ' + dish.name) + '">' +
            RV_MINUS_SVG + '</button>' +
          '<span class="rv-step-count">' +
            escapeHtml(n + ' ' + reviewSlotNoun(dish.slot, n)) + '</span>' +
          // Going UP is not arithmetic: it needs a day to land on, and
          // every candidate is already holding something. Nothing here
          // picks one — it opens the strip below, which shows what each
          // day is holding, and the household says which one to spend.
          '<button type="button" class="rv-step-btn" data-rv-more="' + idx + '"' +
            (busy ? ' disabled' : '') +
            ' aria-expanded="' + (picking ? 'true' : 'false') + '"' +
            ' aria-label="Another ' + escapeHtml(one + ' of ' + dish.name) +
            ' — I’ll ask which day">' + RV_PLUS_SVG + '</button>' +
        '</span>' +
        '<button type="button" class="rv-change" data-rv-change="' + idx + '">' +
          (n > 1 ? 'Change one' : 'Change') + '</button>' +
      '</div>' +
      // Under the stepper that was tapped, not at the foot of the page.
      (reviewTroubleIsFor(dish)
        ? '<div class="rv-trouble">' + escapeHtml(reviewState.trouble) + '</div>'
        : '') +
      (picking ? reviewAddPickerHtml(dish, idx, days) : '') +
    '</div>';
  }

  // Whether the trouble line belongs to THIS dish — by the meal type and
  // the name it reads as, never by its position in the list.
  //
  // It was an index, and an index into an array every render rebuilds. A
  // chat turn tagged tab:'week' reloads the week under the screen, and the
  // sentence is only ever cleared by another tap — so a week that changed
  // underneath moved the sentence onto whatever dish now sat at that
  // position. The drop refusals NAME their dish out loud ("Bean Chili on
  // Friday also feeds Saturday's lunch"), which makes that one more
  // instance of the class this branch has now closed three times: a thing
  // labelled with one dish reporting about another. A name is unique
  // within its group (reviewEatingGroups keys them that way), so meal type
  // plus name is a real key and cannot collide.
  function reviewTroubleIsFor(dish) {
    var at = reviewState.troubleFor;
    return !!(reviewState.trouble && at && dish &&
      at.slot === dish.slot && at.name === dish.name);
  }

  // Flattened as it renders, so every stepper carries a plain index into
  // reviewState.dishes rather than a dish name in an HTML attribute.
  function reviewEatingHtml(days) {
    var groups = reviewEatingGroups(days);
    var flat = [];
    if (!groups.length) {
      return '<div class="rv-body"><div class="rv-empty">Nothing planned yet.</div></div>';
    }
    // A sentence whose dish is no longer on the week is DROPPED, not moved
    // to the foot: it is about something that has left the screen, and the
    // only honest places for it are its own row or nowhere. Cleared before
    // the rows are drawn so nothing renders it on the way past.
    if (reviewState.trouble && !groups.some(function (g) {
      return g.dishes.some(reviewTroubleIsFor);
    })) {
      reviewState.trouble = '';
      reviewState.troubleFor = null;
    }
    var html = groups.map(function (group) {
      return '<div class="rv-group">' +
        '<div class="rv-group-label">' + escapeHtml(group.label) + '</div>' +
        '<div class="shell-card rv-group-card">' +
          group.dishes.map(function (dish) {
            flat.push(dish);
            return reviewDishRowHtml(dish, flat.length - 1, days);
          }).join('') +
        '</div>' +
      '</div>';
    }).join('');
    reviewState.dishes = flat;
    return '<div class="rv-body">' + html + '</div>';
  }

  // A day with nothing to cook on it — every one of its three real meals is
  // deliberately empty, which is either "nobody is home" or "you asked for
  // none of these". READ, never inferred from a missing row: a day with no
  // rows at all is an unplanned day, a different thing, and it gets the
  // ordinary card.
  //
  // The three MEALS and not the snacks, deliberately. Both places that mark
  // a day away — the `out` night pass and the slot_needs pass in
  // agent._finish_week_slots — only ever write breakfast, lunch and dinner,
  // so a rule that also demanded empty snacks would essentially never fire
  // on a real generated week, which is the whole case this state exists
  // for. (Measured in a browser against a seeded away day, not reasoned
  // from the code: the first version of this rule read every slot and the
  // Friday nobody was home rendered as an ordinary day.)
  function reviewDayIsClosed(day) {
    var meals = WEEK_SLOTS.map(function (s) { return day[s]; });
    if (meals.some(function (e) { return !e; })) return false;
    return meals.every(function (e) { return e.state === 'planned_empty'; });
  }

  // Whether anything under the face is worth opening. A closed day usually
  // has nothing — but since nothing marks a SNACK away, a day nobody is
  // home can still carry two of them, and hiding real rows behind "nobody's
  // home" would be this screen deciding they don't count. A deliberately
  // empty slot is still never offered as a decision: it is a line, and the
  // line says what it is.
  function reviewDayHasMore(day) {
    return daySlotKeys(day).some(function (slot) {
      var e = daySlotEntry(day, slot);
      return !!e && (e.state === 'planned' || e.state === 'open');
    });
  }

  // What a closed day says for itself. awayLineFor gives the away sentence
  // the rest of Meals already uses; anything else falls back to the slot's
  // own recorded words rather than a line this screen made up. Dinner
  // first, because that is the slot the away need is declared on.
  function reviewClosedLine(day) {
    var first = [day.dinner].concat(WEEK_SLOTS.map(function (s) { return day[s]; }))
      .filter(Boolean)[0];
    return awayLineFor(first) || (first && first.title) || 'Nothing planned.';
  }

  function reviewSlotLineHtml(day, slot) {
    var entry = daySlotEntry(day, slot);
    var name, quiet = ' is-quiet';
    if (entry && entry.state === 'planned') { name = mealDisplayName(entry); quiet = ''; }
    else if (entry && entry.state === 'open') name = 'Your call';
    else if (entry && entry.state === 'planned_empty') {
      name = awayLineFor(entry) || entry.title || 'Nothing planned';
    } else name = day.isPast ? 'Not planned' : 'Nothing yet';
    return '<span class="rv-slot' + quiet + '">' +
      '<span class="rv-slot-label">' + escapeHtml(slotEyebrowLabel(day, slot)) + '</span>' +
      '<span class="rv-slot-name">' + escapeHtml(name) + '</span>' +
    '</span>';
  }

  function reviewDayTitle(day) {
    return dayName(day.date, { weekday: 'long' }) + ' ' + dayName(day.date, { day: 'numeric' });
  }

  // The face of a day card: the day, and the one line that answers "what
  // are we eating". Dinner, because that is the meal people actually check
  // — unless nobody is home, in which case the day's own away sentence is
  // the whole answer and dinner is not a thing to name.
  //
  // `note` is the one thing the dish name cannot say for itself: that
  // tonight costs no cooking. Without it, a chain reads as the same dinner
  // planned twice — which on a screen whose whole job is checking the week
  // looks like a mistake the household should fix, and is the opposite of
  // what it is. Read off the entry's own chain, never guessed from two days
  // sharing a name.
  function reviewDayFaceLine(day) {
    if (reviewDayIsClosed(day)) return { line: reviewClosedLine(day), quiet: ' is-quiet', note: '' };
    var dinner = daySlotEntry(day, 'dinner');
    if (dinner && dinner.state === 'planned') {
      var note = '';
      if (dinner.leftover_from) note = dinner.leftover_from.cook_ahead ? 'made ahead' : 'leftovers';
      else if (dinner.source === 'leftovers') note = 'leftovers';
      return { line: mealDisplayName(dinner), quiet: '', note: note };
    }
    if (dinner && dinner.state === 'open') return { line: 'Your call', quiet: ' is-quiet', note: '' };
    if (dinner && dinner.state === 'planned_empty') {
      return {
        line: awayLineFor(dinner) || dinner.title || 'Nothing planned',
        quiet: ' is-quiet', note: '',
      };
    }
    return { line: day.isPast ? 'Not planned' : 'Nothing yet', quiet: ' is-quiet', note: '' };
  }

  function reviewDayNoteHtml(note) {
    return note ? '<span class="rv-day-note">' + escapeHtml(note) + '</span>' : '';
  }

  function reviewDayCardHtml(day, i) {
    var title = reviewDayTitle(day);
    var closed = reviewDayIsClosed(day);
    var face = reviewDayFaceLine(day);
    // Nothing under it worth opening — a day nobody is home and nothing
    // else on it. Flat rather than an expander that opens onto three
    // repetitions of the line already on its face.
    if (!reviewDayHasMore(day)) {
      return '<div class="shell-card rv-day' + (closed ? ' is-closed' : '') + '">' +
        '<div class="rv-day-head is-flat">' +
          '<span class="rv-day-col">' +
            '<span class="rv-day-title">' + escapeHtml(title) + '</span>' +
            '<span class="rv-day-dinner' + face.quiet + '">' + escapeHtml(face.line) + '</span>' +
            reviewDayNoteHtml(face.note) +
          '</span>' +
        '</div>' +
      '</div>';
    }
    var open = !!reviewState.openDays[day.date];
    var line = face.line, quiet = face.quiet;
    return '<div class="shell-card rv-day' + (open ? ' is-open' : '') +
        (closed ? ' is-closed' : '') + (day.isToday ? ' is-today' : '') + '">' +
      '<button type="button" class="rv-day-head" data-rv-day="' + escapeHtml(day.date) + '"' +
          ' aria-expanded="' + (open ? 'true' : 'false') + '">' +
        '<span class="rv-day-col">' +
          '<span class="rv-day-title">' + escapeHtml(title) + '</span>' +
          '<span class="rv-day-dinner' + quiet + '">' + escapeHtml(line) + '</span>' +
          reviewDayNoteHtml(face.note) +
        '</span>' +
        '<span class="rv-day-chev">' + RV_CHEVRON_SVG + '</span>' +
      '</button>' +
      (open
        ? '<div class="rv-day-slots">' +
            daySlotKeys(day).map(function (slot) {
              return reviewSlotLineHtml(day, slot);
            }).join('') +
            // Through to the Day step, which is where a slot is actually
            // acted on. Review reads and counts; it does not grow a second
            // copy of every per-slot control.
            '<button type="button" class="rv-day-open" data-rv-open="' + i + '">' +
              'Open ' + escapeHtml(dayName(day.date, { weekday: 'long' })) + ' ›</button>' +
          '</div>'
        : '') +
    '</div>';
  }

  function reviewDaysHtml(days) {
    if (!(days || []).length) {
      return '<div class="rv-body"><div class="rv-empty">Nothing planned yet.</div></div>';
    }
    return '<div class="rv-body rv-days">' +
      days.map(reviewDayCardHtml).join('') +
    '</div>';
  }

  // The screen's one apricot (Rule 5), and deliberately the same .wk-decide
  // shell and #week-approve-btn id the Week root uses — approveWeek and
  // showApproveConfirm both look for exactly those, so the who's-approving
  // step and the hard-clash "Approve anyway" arming work here with no
  // second implementation. Only one of the two steps is ever in the DOM at
  // a time (renderMealsStep replaces #week-steps outright), so the shared
  // id is never duplicated.
  function reviewDecideHtml(data) {
    if (weekPlanState(data) !== 'draft') return '';
    var openCount = countOpenSlots(data);
    return '<div class="wk-decide dock">' +
      '<button type="button" class="btn-gold week-approve-btn" id="week-approve-btn">' +
        (openCount
          ? escapeHtml(approveWithOpenLabel(data, openCount))
          : 'Approve and build my shopping list') +
      '</button>' +
    '</div>';
  }

  function reviewStepHtml(data, days) {
    var eating = reviewState.view !== 'days';
    // "Not approved yet" while it is a draft, and the plain truth once it
    // isn't — this step is for every week, so the badge has to be able to
    // say the other thing.
    var draft = weekPlanState(data) === 'draft';
    return '<button type="button" class="crumb" data-wk-back="week">‹ This week</button>' +
      '<div class="wk-head">' +
        '<div class="wk-head-row">' +
          '<h1 class="wk-title">Check the week</h1>' +
          '<span class="wk-state is-' + (draft ? 'draft' : 'set') + '">' +
            (draft ? 'NOT APPROVED YET' : 'APPROVED') + '</span>' +
        '</div>' +
      '</div>' +
      '<div class="wk-seg" role="tablist">' +
        '<button type="button" class="wk-seg-btn' + (eating ? ' is-on' : '') + '"' +
          ' role="tab" aria-selected="' + (eating ? 'true' : 'false') + '"' +
          ' data-rv-view="eating">What we’re eating</button>' +
        '<button type="button" class="wk-seg-btn' + (eating ? '' : ' is-on') + '"' +
          ' role="tab" aria-selected="' + (eating ? 'false' : 'true') + '"' +
          ' data-rv-view="days">Which days</button>' +
      '</div>' +
      (eating ? reviewEatingHtml(days) : reviewDaysHtml(days)) +
      reviewDecideHtml(data);
  }

  // Which of a day's slot KEYS holds this entry. For the three real meals
  // the group's own type is already the key; a snack is the exception,
  // because a day's second snack lives under 'snack2' and the group only
  // knows it is a snack. Matched on entry id rather than on position, so a
  // day whose snacks were re-ordered still opens the right one.
  function reviewMealSlotKey(day, entryId, slotType) {
    if (slotType !== 'snack') return slotType;
    var snacks = (day && day.snacks) || [];
    for (var i = 0; i < snacks.length; i++) {
      if (snacks[i] && snacks[i].entry_id === entryId) return snackSlotKey(i);
    }
    return 'snack';
  }

  // One fewer day of a dish. No model call and no chat turn — this is
  // arithmetic on the plan, so it is one small POST, and the backend hands
  // back the changed day in get_week_menu's own shape for the same reason
  // the in-place swap does: the week this screen is holding can be updated
  // by splicing one day into it, so BOTH views are right the moment it
  // lands, with no refetch and no second renderer.
  async function runDropDishDay(panel, idx) {
    var dish = (reviewState.dishes || [])[idx];
    var data = weekState.data;
    if (!dish || dish.days.length < 2 || !data || !data.week_start_date) return;
    if (reviewState.busy !== null) return;
    // The furthest-out day the dish covers — see reviewEatingGroups, and
    // note that this is NOT guaranteed to be a reheat: the server is what
    // refuses a night other nights are eating off, and it answers
    // 'refused' with the sentence to show.
    var target = dish.days[dish.days.length - 1];
    reviewState.busy = idx;
    // Every row's day picker is about the week as it stands, and this
    // changes it — so a strip left open would be offering days off a count
    // that has moved. Closed here rather than left to be re-derived.
    reviewState.picking = null;
    reviewState.trouble = '';
    reviewState.troubleFor = null;
    renderMealsStep(panel);
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(data.week_start_date) + '/drop-dish-day', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entry_id: target.entryId })
      });
      if (!res.ok) throw new Error('drop failed');
      var out = await res.json();
      reviewState.busy = null;
      // A 200 that says no: this dish is cooked double for another night,
      // and taking it away would leave that night holding a recipe nobody
      // planned to cook. The sentence is the server's — it is the one that
      // knows which night depends on it — and nothing was written.
      if (out && out.status === 'refused') {
        reviewState.trouble = out.message || SWAP_TROUBLE;
        reviewState.troubleFor = { slot: dish.slot, name: dish.name };
        renderMealsStep(panel);
        return;
      }
      if (out && out.day) spliceSwappedDay(out.day);
      renderMealsStep(panel);
      // Then the rest of the week, for the same reason runSwapInPlace does
      // it: the splice updates weekState.DAYS, and the badge, the subtitle
      // and — the one that matters most here — the Approve button's own
      // count all read weekState.DATA, which a splice never touches. Left
      // out, the button went on saying "Approve and build my shopping
      // list" on a week that had just been handed an open slot back: copy
      // that counts things, drifting from the things it counts, on the
      // screen whose whole premise is counting.
      await loadWeekMenu(panel);
      // The slot came back as a question, so say so — the household tapped
      // "one fewer" and the work that leaves behind is a night with nothing
      // on it. Named the way resolveOpenSlot names its own day.
      showToast(dayName(out.date, { weekday: 'long' }) + '’s ' +
        slotWord(out.slot) + ' is yours to fill now.');
      // An approved week's shopping list just changed underneath, so
      // anything showing it is stale — the same courtesy resolveOpenSlot
      // already pays.
      if (data.status === 'approved') refreshGrocerySurfaces();
    } catch (err) {
      console.warn('Dropping a day failed:', err);
      reviewState.busy = null;
      // Calm and plain, and it says what is true of the plan (§8).
      reviewState.trouble = SWAP_TROUBLE;
      reviewState.troubleFor = { slot: dish.slot, name: dish.name };
      renderMealsStep(panel);
    }
  }

  // What the tap changed, said in the order it matters: the day, the dish,
  // what it replaced, and — the clause this originally missed — any night
  // that was eating off what just went.
  //
  // The stepper going DOWN refuses to break a chain outright, and says so
  // ("...also feeds Friday's dinner — change that first"). This path
  // allows it, because it REPLACES rather than deletes and
  // swap_meal_in_plan re-buys for every night that was eating off the
  // displaced dish, so nothing is left stranded. But one screen must not
  // refuse the mirror of what it silently allows, and silence was the
  // whole of the difference: Friday stopped being a reheat and became a
  // cook of its own with nobody told. It is told now.
  function addDishToastText(out) {
    var day = dayName(out.date, { weekday: 'long' });
    var line = day + '’s ' + slotWord(out.slot) + ' is ' + out.dish + ' now' +
      (out.replaced ? ', in place of ' + out.replaced + '.' : '.');
    var freed = (out.unchained || []).map(function (t) {
      return dayName(t.date, { weekday: 'long' });
    });
    if (!freed.length) return line;
    var list = freed.length === 1
      ? freed[0]
      : freed.slice(0, -1).join(', ') + ' and ' + freed[freed.length - 1];
    var one = freed.length === 1;
    // "on its own now", not "a cook of its own now". A freed night keeps
    // whatever it was called, and a night the planner had written as
    // "Leftover bulgogi" still READS as a reheat on the row underneath
    // this toast — measured in a browser, which is where the first wording
    // was caught contradicting the screen it was printed over. What is
    // certainly true is the chain: nothing is feeding that night any more.
    return line + ' ' + list + ' ' + (one ? 'was' : 'were') +
      ' eating off it, so ' + (one ? 'that night is on its own' : 'those nights are on their own') +
      ' now.';
  }

  // One more day of a dish, on the day the household picked. Same shape as
  // runDropDishDay above and for the same reasons — one small POST, no
  // model call and no chat turn, and the backend hands back the changed
  // day in get_week_menu's own shape so the week this screen is holding
  // updates by splicing one day into it.
  //
  // `at` is a position in the options this render drew, not a day and not
  // an entry id in the markup: the options are recomputed from the same
  // days the row was drawn from, so a stale index can only ever miss (and
  // returns), never land on a different day than the one that was tapped.
  async function runAddDishDay(panel, idx, at) {
    var dish = (reviewState.dishes || [])[idx];
    var data = weekState.data;
    if (!dish || !data || !data.week_start_date) return;
    if (reviewState.busy !== null) return;
    var option = reviewAddDayOptions(weekState.days, dish)[at];
    var from = dish.days[0];
    if (!option || !from) return;
    reviewState.busy = idx;
    reviewState.trouble = '';
    reviewState.troubleFor = null;
    renderMealsStep(panel);
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(data.week_start_date) + '/add-dish-day', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entry_id: from.entryId, target_entry_id: option.entryId })
      });
      if (!res.ok) throw new Error('add failed');
      var out = await res.json();
      reviewState.busy = null;
      // A 200 that says no, in the same shape the stepper going down
      // already answers one: a day nobody is home, a meal somebody has
      // already cooked. The sentence is the server's, because it is the
      // one that knows which, and nothing was written. ONLY sentences
      // written for a person arrive this way — an id or a raw exception is
      // a 404 and takes the plain line below, since an app that did
      // exactly the right thing must not report itself broken.
      if (out && out.status === 'refused') {
        reviewState.trouble = out.message || SWAP_TROUBLE;
        reviewState.troubleFor = { slot: dish.slot, name: dish.name };
        renderMealsStep(panel);
        return;
      }
      reviewState.picking = null;
      if (out && out.day) spliceSwappedDay(out.day);
      renderMealsStep(panel);
      // The splice updates weekState.DAYS; the badge, the subtitle and the
      // Approve button's own count all read weekState.DATA, which a splice
      // never touches — and this tap can settle an open slot, which is
      // exactly the count that button is a promise about. Same one line
      // runSwapInPlace and the stepper going down both carry.
      await loadWeekMenu(panel);
      // Name the day and what it cost. An open slot cost nothing — a
      // question was answered — so the sentence doesn't invent a loss.
      showToast(addDishToastText(out));
      // An approved week's shopping list just changed underneath, so
      // anything showing it is stale — the same courtesy the stepper going
      // down already pays.
      if (data.status === 'approved') refreshGrocerySurfaces();
    } catch (err) {
      console.warn('Adding a day failed:', err);
      reviewState.busy = null;
      reviewState.trouble = SWAP_TROUBLE;
      reviewState.troubleFor = { slot: dish.slot, name: dish.name };
      renderMealsStep(panel);
    }
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
    var label = slotEyebrowLabel(day, slot);
    var entry = daySlotEntry(day, slot);
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

  // ---------- Swap, in place ----------
  // Julia (first beta tester, 2026-09-08): "click on the one recipe and
  // meal that the user wants to switch and then have it regenerate just
  // the one on the spot." Swap used to open the ask sheet and cost a whole
  // chat turn; it is now one small call to /api/week/{week}/swap-in-place
  // and the card answers in place.
  //
  // One at a time on purpose: the household is tapping Swap on one meal,
  // not on three at once, and a map of per-slot states would be state to
  // keep true across every re-render for a case that doesn't happen.
  // `avoid` rides on it so a second tap says "not that one either" rather
  // than re-offering what was just turned down.
  var swapState = null;
  var swapUndoTimer = null;
  // Long enough to notice and reach, short enough that the line doesn't
  // become permanent furniture on the card.
  var SWAP_UNDO_MS = 8000;
  // Calm and plain, and it says what's true of the plan — see the
  // calm-in-trouble rule in DESIGN_SYSTEM.md §8.
  var SWAP_TROUBLE = 'That didn’t work just now — nothing changed.';

  function swapStateFor(date, slot) {
    return (swapState && swapState.date === date && swapState.slot === slot) ? swapState : null;
  }

  function clearSwapUndoTimer() {
    if (swapUndoTimer) { clearTimeout(swapUndoTimer); swapUndoTimer = null; }
  }

  // The one quiet line under a slot's actions. It is always the same line;
  // only what it says changes — the wordier way out when nothing is
  // happening, the working line while the call is out, then the reason and
  // an Undo chip. One line that changes is why the card doesn't jump.
  function swapLineHtml(day, slot) {
    var state = swapStateFor(day.date, slot);
    var tell = '<button type="button" class="wk-swap-tell" data-wk-tell="' + slotWord(slot) + '">' +
      'Tell me what instead</button>';
    if (state && state.busy) {
      return '<div class="wk-swap-line"><span class="wk-swap-working">Finding something else…</span></div>';
    }
    if (state && state.message) {
      return '<div class="wk-swap-line">' +
        '<span class="wk-swap-said">' + escapeHtml(state.message) + '</span>' + tell +
      '</div>';
    }
    if (state && state.reason) {
      return '<div class="wk-swap-line">' +
        '<span class="wk-swap-said">' + escapeHtml(state.reason) + '</span>' +
        '<button type="button" class="wk-swap-undo" data-wk-undo="' + slot + '">Undo</button>' +
        tell +
      '</div>';
    }
    return '<div class="wk-swap-line">' + tell + '</div>';
  }

  // The two-segment control. Which primary a slot gets is entirely a
  // function of its state: a cook is cooked, a made-ahead night is eaten,
  // an open slot is answered, and an away night is offered nothing at all —
  // offering to change it is exactly what the away state exists to prevent.
  function slotActionsHtml(day, slot, apricot) {
    var entry = daySlotEntry(day, slot);
    var primaryCls = 'wk-act wk-act-primary' + (apricot ? ' is-apricot' : '');
    var swap = '<button type="button" class="wk-act wk-act-swap" data-wk-swap="' + slot + '">Swap</button>';
    // Rides with the Swap button wherever it is offered, and nowhere else:
    // a slot with no way to change it has nothing to say about changing it.
    var swapLine = swapLineHtml(day, slot);
    if (entry && entry.state === 'planned') {
      if (day.isPast) return '';
      var time = cookTimeChip(entry);
      // A snack without a real recipe is grab-and-go — "Cook this" would be
      // asking the household to cook nothing, so it gets the same primary
      // a reheat night gets: tap it once it's eaten.
      var label = entry.source === 'leftovers'
        ? REHEAT_ACTION_LABEL
        : (isSnackSlot(slot) && !isRealCook(entry))
          ? REHEAT_ACTION_LABEL
          : 'Cook this' + (time ? ' · ' + time : '');
      return '<div class="wk-acts">' +
        '<button type="button" class="' + primaryCls + '" data-wk-cook="' + slot + '">' +
          escapeHtml(label) + '</button>' + swap +
      '</div>' + swapLine;
    }
    if (entry && entry.state === 'open') {
      return '<div class="wk-acts">' +
        '<button type="button" class="' + primaryCls + '" data-wk-pick="' + slot + '">Pick</button>' +
        swap +
      '</div>' + swapLine;
    }
    if (entry && entry.state === 'planned_empty') {
      if (entry.need === 'away' || day.isPast) return '';
      return '<div class="wk-acts">' + swap + '</div>' + swapLine;
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
    var entry = daySlotEntry(day, slot);
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

  // One card per snack, after the three meal cards — reusing daySlotCardHtml
  // whole rather than a second card shape, so a snack gets the exact same
  // eyebrow/name/actions/tap-to-open-Meal-step wiring any other slot does.
  // A day with zero snacks contributes nothing here.
  function daySnackCardsHtml(day) {
    return (day.snacks || []).map(function (_, i) {
      return daySlotCardHtml(day, snackSlotKey(i));
    }).join('');
  }

  function dayStepHtml(day) {
    return '<button type="button" class="crumb" data-wk-back="week">‹ This week</button>' +
      '<div class="wk-head">' +
        '<div class="wk-head-row"><h1 class="wk-title">' +
          escapeHtml(dayName(day.date, { weekday: 'long' })) + '</h1></div>' +
        '<div class="wk-sub">' + escapeHtml(daySubtitle(day)) + '</div>' +
      '</div>' +
      '<div class="wk-slots">' +
        WEEK_SLOTS.map(function (slot) { return daySlotCardHtml(day, slot); }).join('') +
        daySnackCardsHtml(day) +
      '</div>';
  }

  // ---------- MEAL ----------

  var PLATE_GROUP_LABELS = { protein: 'protein', carb: 'carb', vegetable: 'veg' };

  // "Protein, carb, veg. Nothing to thaw." Both halves are read, never
  // guessed: the groups are the ones the entry recorded (plates.py never
  // invents them either), and the thaw line is the plan's own defrost task
  // — the same prep_tasks row Today's fridge move ticks.
  // A grab-and-go snack has nothing to say here — no food groups recorded,
  // no added sides, no thaw task — and "Nothing to thaw." on its own isn't
  // information, it's an empty card wearing a caption. Hide rather than
  // show it; a real meal (which always carries at least a food-group read)
  // never trips this.
  function plateCardIsEmpty(entry) {
    return !((entry.food_groups && entry.food_groups.length) ||
      (entry.sides && entry.sides.length) ||
      (entry.defrost && entry.defrost.note));
  }

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
    var entry = daySlotEntry(day, slot);
    var chips = [
      cookTimeChip(entry),
      entry.serves ? 'Serves ' + entry.serves : '',
      entry.plate_note
    ];
    var cookMeal = cookMealForEntry(entry.entry_id);
    var aheadHtml = cookMeal ? cookAheadHtml(cookMeal) : '';
    return '<button type="button" class="crumb" data-wk-back="day">‹ ' +
        escapeHtml(dayName(day.date, { weekday: 'long' })) + '</button>' +
      '<div class="wk-head">' +
        '<div class="wk-head-row"><h1 class="wk-title">' +
          escapeHtml(mealDisplayName(entry)) + '</h1></div>' +
        chipsRowHtml(chips, 'wk-chips wk-chips-head') +
      '</div>' +
      // A real meal always carries at least a food-group read, so this only
      // ever actually hides the card for a grab-and-go snack — the plate
      // card stays exactly as it was for breakfast/lunch/dinner.
      ((isSnackSlot(slot) && plateCardIsEmpty(entry)) ? '' : plateCardHtml(entry)) +
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
      // The recipe itself. Emily, 2026-09-09: tapping a dish should bring
      // you to the screen with the recipe on it — and inside Meals the
      // dish's screen is this one, so the recipe belongs here rather than
      // two taps further on. The panel is the cook screen's own
      // (cookDetailHtml), rendered `plain`: same words, same order, no
      // working controls. cookAheadHtml above is reused from that screen
      // in exactly the same way, and for the same reason.
      mealRecipeCardHtml(cookMeal, slot) +
      // The screen's one apricot primary (Rule 5) — the Day step's own
      // segments are quiet for exactly this reason.
      slotActionsHtml(day, slot, true);
  }

  // Nothing at all when the Cook view has no card for this entry: either
  // it hasn't loaded yet (ensureCookDataForMeals re-renders when it does)
  // or this slot is a reheat night, which is a line and never a way into a
  // recipe — the rule Kitchen's own rows already follow. A card that IS
  // here with no saved recipe still renders, because cookDetailHtml says
  // so plainly ("Freeform meal — no saved recipe detail"), and a name that
  // says nothing is what this ticket exists to fix.
  function mealRecipeCardHtml(cookMeal, slot) {
    if (!cookMeal || cookMeal.is_leftovers) return '';
    // ...and nothing for a grab-and-go snack with no recipe behind it
    // either (2026-09-10). "Apple slices" is not a freeform meal somebody
    // forgot to write up, so a card whose whole content is "no saved
    // recipe detail" is an empty card — exactly what this screen already
    // takes the plate card away for on the same slot. A breakfast, lunch
    // or dinner with no recipe keeps the line: there, the absence is worth
    // saying, and it names the way to fill it in.
    if (isSnackSlot(slot || '') && !cookMeal.has_full_recipe) return '';
    return '<div class="shell-card wk-card wk-recipe-card">' +
      '<div class="wk-card-title">The recipe</div>' +
      cookDetailHtml(cookMeal, 'meal', false, true) +
    '</div>';
  }

  // ---------- the step machine ----------

  function mealsCurrentDay() {
    var i = weekState.selectedIndex;
    return (i !== null && weekState.days[i]) ? weekState.days[i] : null;
  }

  // What a cook screen opened from Meals should SAY it came from, and
  // where it should land: the weekday, and the exact step — a tap from the
  // Meal step returns to that meal, a tap from the Day step to that day.
  function mealsOriginFor(day, slot) {
    if (!day) return null;
    return {
      label: dayName(day.date, { weekday: 'long' }),
      tab: 'week',
      mealsStep: weekState.step === 'meal' ? 'meal' : 'day',
      mealsDay: weekState.selectedIndex,
      mealsSlot: slot || weekState.mealSlot
    };
  }

  function mealsStepHistoryState() {
    // Same path either way — Meals is /week in all three steps, exactly as
    // Grocery is /grocery in all three of its. The state object is what the
    // back gesture reads; the URL never claims a page that doesn't exist.
    return {
      tab: 'week',
      mealsStep: weekState.step,
      mealsDay: weekState.selectedIndex,
      mealsSlot: weekState.mealSlot
    };
  }

  function pushMealsStepHistory() {
    window.history.pushState(mealsStepHistoryState(), '', '/week');
  }

  // Used by cook mode's back link, which is stepping back up rather than
  // going somewhere new — see activateTab's replaceHistory.
  function replaceMealsStepHistory() {
    window.history.replaceState(mealsStepHistoryState(), '', '/week');
  }

  function goMealsStep(step, opts) {
    opts = opts || {};
    weekState.step = step;
    if (opts.dayIndex !== undefined && opts.dayIndex !== null) weekState.selectedIndex = opts.dayIndex;
    if (opts.slot) weekState.mealSlot = opts.slot;
    if (opts.replace) replaceMealsStepHistory();
    else if (opts.push !== false) pushMealsStepHistory();
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
        !(day && daySlotEntry(day, weekState.mealSlot) && daySlotEntry(day, weekState.mealSlot).state === 'planned')) {
      weekState.step = day ? 'day' : 'week';
    }
    if (weekState.step === 'day' && !day) weekState.step = 'week';
    // Nothing to check yet. The Review step is about a week that exists;
    // with no plan at all the root's own plan-a-week entry is the answer.
    if (weekState.step === 'review' && weekPlanState(data) === 'none') weekState.step = 'week';

    var onRoot = weekState.step === 'week';
    var approve = panel.querySelector('#week-approve-row');
    // The clash to settle and the approved receipt belong to the week, not
    // to one day of it — they sit above the card on the root and nowhere
    // else, the same way Cook's focus screen hides the Plan/Cook control.
    // (The draft's Approve button is inside the root's own markup now, so
    // it needs no hiding of its own — weekStepHtml simply doesn't build it
    // on the Day or Meal step.)
    if (approve) approve.hidden = !onRoot;
    // The gear is the root's, not a step's — same rule the Plan/Cook
    // control it replaced followed.
    var gearRow = panel.querySelector('#meals-gear-row');
    if (gearRow) gearRow.hidden = !onRoot;

    if (weekState.step === 'meal') {
      steps.innerHTML = mealStepHtml(day, weekState.mealSlot);
      ensureCookDataForMeals(panel);
    } else if (weekState.step === 'day') {
      steps.innerHTML = dayStepHtml(day);
    } else if (weekState.step === 'review') {
      steps.innerHTML = reviewStepHtml(data, weekState.days);
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
        var entry = day && daySlotEntry(day, slot);
        // Everything that identifies THIS meal, so cook mode lands on it
        // whatever shape the plan is — the same target dayActionsHtml used
        // to pass, now aimed at Kitchen, where cooking lives. cookFocus.slot
        // is the fallback match (date+slot) behind entryId — Kitchen's own
        // rows carry the backend's plain 'snack', never our 'snack2' index
        // key, so that's what goes here too.
        //
        // Through openRecipeFor so cook mode's back link names the step
        // this came from ("‹ Monday") and lands back on it, rather than
        // saying Kitchen — somewhere this person has not been.
        openRecipeFor({
          entryId: entry ? entry.entry_id : null,
          date: day ? day.date : null,
          slot: isSnackSlot(slot) ? 'snack' : slot,
          title: entry ? entry.title : ''
        }, mealsOriginFor(day, slot));
      });
    });
    // Swap is the in-place action now: one call, one new dish, answered on
    // the card. A second tap swaps again and carries the dish just turned
    // down along as something not to offer.
    steps.querySelectorAll('[data-wk-swap]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var day = mealsCurrentDay();
        if (!day) return;
        runSwapInPlace(panel, day, btn.getAttribute('data-wk-swap'));
      });
    });
    steps.querySelectorAll('[data-wk-undo]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var day = mealsCurrentDay();
        if (!day) return;
        runSwapUndo(panel, day, btn.getAttribute('data-wk-undo'));
      });
    });
    // The wordier way, kept: the same sentence Swap used to send, opening
    // the same sheet the same way. openAskSheet itself is untouched.
    steps.querySelectorAll('[data-wk-tell]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var day = mealsCurrentDay();
        if (!day) return;
        openAskSheet('Swap ' + dayName(day.date, { weekday: 'long' }) + '’s ' +
          btn.getAttribute('data-wk-tell') + ' for something else');
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
    // The Review step's own controls. The two views are one control over
    // one week, so switching is local state and a re-render — there is
    // nothing to fetch, and a round trip to show the same days a different
    // way would be the screen forgetting what it already knows.
    steps.querySelectorAll('[data-rv-view]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        reviewState.view = btn.getAttribute('data-rv-view');
        // A half-made decision doesn't travel between views: the strip is
        // about rows that aren't on the other one.
        reviewState.picking = null;
        renderMealsStep(panel);
      });
    });
    steps.querySelectorAll('[data-rv-day]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var date = btn.getAttribute('data-rv-day');
        if (reviewState.openDays[date]) delete reviewState.openDays[date];
        else reviewState.openDays[date] = true;
        renderMealsStep(panel);
      });
    });
    steps.querySelectorAll('[data-rv-open]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        goMealsStep('day', { dayIndex: Number(btn.getAttribute('data-rv-open')) });
      });
    });
    steps.querySelectorAll('[data-rv-less]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        runDropDishDay(panel, Number(btn.getAttribute('data-rv-less')));
      });
    });
    steps.querySelectorAll('[data-rv-more]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var idx = Number(btn.getAttribute('data-rv-more'));
        if (!(reviewState.dishes || [])[idx]) return;
        // The one thing this screen cannot answer on its own is WHICH day
        // the extra one goes on, so it asks rather than guesses — and the
        // asking is now the strip under the row, which shows what each day
        // is holding. One open at a time: two pickers open at once is two
        // half-made decisions on one screen.
        reviewState.picking = reviewState.picking === idx ? null : idx;
        renderMealsStep(panel);
      });
    });
    steps.querySelectorAll('[data-rv-pick]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        runAddDishDay(panel, Number(btn.getAttribute('data-rv-pick')),
          Number(btn.getAttribute('data-rv-pick-at')));
      });
    });
    steps.querySelectorAll('[data-rv-pick-cancel]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        reviewState.picking = null;
        renderMealsStep(panel);
      });
    });
    steps.querySelectorAll('[data-rv-change]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var dish = (reviewState.dishes || [])[Number(btn.getAttribute('data-rv-change'))];
        if (!dish || !dish.days.length) return;
        // Straight to that meal, which is where every way of changing a
        // dish already lives (Swap in place, "Tell me what instead"). The
        // first day it covers, because for a chain that is the night it is
        // actually cooked. Changing every covered day in one tap is the
        // next card's job, not something to half-build here.
        var first = dish.days[0];
        var idx = weekState.days.findIndex(function (d) { return d.date === first.date; });
        if (idx === -1) return;
        goMealsStep('meal', { dayIndex: idx, slot: reviewMealSlotKey(weekState.days[idx], first.entryId, dish.slot) });
      });
    });
    // The draft's decision, now that it lives under the card rather than in
    // a band of its own. Same two handlers as before, same approveWeek /
    // openAskSheet — only the surface moved.
    var approveBtn = steps.querySelector('#week-approve-btn');
    if (approveBtn) approveBtn.addEventListener('click', function () {
      approveWeek(panel, weekState.data || {});
    });
    var tweakBtn = steps.querySelector('#week-tweak-btn');
    if (tweakBtn) tweakBtn.addEventListener('click', function () {
      openAskSheet('Let’s tweak this week — ');
    });
    var checkBtn = steps.querySelector('#week-check-btn');
    if (checkBtn) checkBtn.addEventListener('click', function () {
      goMealsStep('review');
    });
  }

  // ---------- Swap, in place: the two calls ----------

  // The changed day, folded into the week the screen is already holding,
  // so the new dish is on the card before the full refresh comes back —
  // the refresh policy's "you change something → the screen updates on
  // tap" (DESIGN_SYSTEM.md §6). The backend hands back get_week_menu's own
  // day dict for exactly this, so there is no second shape to render.
  function spliceSwappedDay(freshDay) {
    if (!freshDay || !freshDay.date) return;
    var todayStr = todayLocalStr();
    for (var i = 0; i < weekState.days.length; i++) {
      if (weekState.days[i].date === freshDay.date) {
        weekState.days[i] = Object.assign({}, freshDay, classifyDay(freshDay, todayStr));
        return;
      }
    }
  }

  function weekStartForSwap() {
    return (weekState.data && weekState.data.week_start_date) || null;
  }

  async function runSwapInPlace(panel, day, slot) {
    var entry = daySlotEntry(day, slot);
    var weekStart = weekStartForSwap();
    if (!entry || entry.entry_id === null || entry.entry_id === undefined || !weekStart) return;
    // Whatever this sitting has already turned down for this slot. Carried
    // rather than recomputed: the server is the one that knows what it
    // offered, and it hands the list back each time.
    var carried = (swapStateFor(day.date, slot) || {}).avoid || [];
    clearSwapUndoTimer();
    swapState = { date: day.date, slot: slot, busy: true, avoid: carried };
    renderMealsStep(panel);
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(weekStart) + '/swap-in-place', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entry_id: entry.entry_id, avoid: carried })
      });
      if (!res.ok) throw new Error('swap failed');
      var data = await res.json();
      // A 200 that says no. The sentence is the server's — it is the one
      // that knows what it couldn't work around — and nothing changed.
      if (data.status !== 'swapped') {
        swapState = {
          date: day.date, slot: slot,
          avoid: data.avoid || carried, message: data.message || SWAP_TROUBLE
        };
        renderMealsStep(panel);
        return;
      }
      swapState = {
        date: day.date, slot: slot,
        avoid: data.avoid || carried, reason: data.reason || '', canUndo: true
      };
      spliceSwappedDay(data.day);
      renderMealsStep(panel);
      // Then the rest of the week, quietly: a swap can change the badge,
      // the subtitle, the draft's clash line and Kitchen's reading of the
      // same week. loadWeekMenu is the one place that keeps all of those
      // in step, and it re-renders the step with the swap line intact.
      await loadWeekMenu(panel);
      clearSwapUndoTimer();
      swapUndoTimer = setTimeout(function () {
        swapUndoTimer = null;
        // The reason goes with the chip: it is saved on the meal as its
        // "Why this night", so the card doesn't have to keep holding it.
        if (swapStateFor(day.date, slot)) { swapState = null; renderMealsStep(panel); }
      }, SWAP_UNDO_MS);
    } catch (err) {
      console.warn('Swap failed:', err);
      swapState = { date: day.date, slot: slot, avoid: carried, message: SWAP_TROUBLE };
      renderMealsStep(panel);
    }
  }

  async function runSwapUndo(panel, day, slot) {
    var entry = daySlotEntry(day, slot);
    var weekStart = weekStartForSwap();
    if (!entry || entry.entry_id === null || entry.entry_id === undefined || !weekStart) return;
    clearSwapUndoTimer();
    swapState = { date: day.date, slot: slot, busy: true, avoid: [] };
    renderMealsStep(panel);
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(weekStart) + '/swap-undo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entry_id: entry.entry_id })
      });
      if (!res.ok) throw new Error('undo failed');
      var data = await res.json();
      swapState = null;
      spliceSwappedDay(data.day);
      renderMealsStep(panel);
      await loadWeekMenu(panel);
    } catch (err) {
      console.warn('Undo failed:', err);
      swapState = { date: day.date, slot: slot, avoid: [], message: SWAP_TROUBLE };
      renderMealsStep(panel);
    }
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
      // Reopening followed the receipt's own buttons in here (Emily's
      // approved design, 2026-09-08: the receipt is a receipt, and once
      // it's dismissed a settled week is header + card + foot). Not
      // "un-approve": it lets the week be edited again and never takes
      // anything off the shopping list — re-approving only adds what's new,
      // and removing something somebody may already have bought is worse
      // than a slightly long list.
      (hasPlan && data.status === 'approved'
        ? mealsMoreRowHtml('wk-more-reopen', 'Reopen the week', 'Edit it again — your list stays as it is')
        : '') +
      // A draft carries "Check the week" on the page itself; an APPROVED
      // week has no decision row for it to sit in, and this step is for
      // every week rather than only for one about to be approved.
      (hasPlan && data.status === 'approved'
        ? mealsMoreRowHtml('wk-more-check', 'Check the week', 'What you’re eating, and which days')
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
    on('wk-more-reopen', function () { closeMealsMoreSheet(); reopenWeek(panel, data); });
    on('wk-more-check', function () { closeMealsMoreSheet(); goMealsStep('review'); });
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
  // the decision under a draft's week card, and the receipt above a settled
  // one.
  //
  // Three copy helpers used to live here and are gone with the surfaces
  // that carried them (Emily's approved design, 2026-09-08):
  // approvedAtLabel (the receipt's "APPROVED BY EMILY · 9:41AM" eyebrow —
  // the eyebrow is "Your week is set" now), groceryPromiseText (the review
  // band's promise line) and receiptBodyText (the receipt's long "All set.
  // I've put N items…" paragraph). The receipt says the same week in one
  // counted sentence instead — see weekly_plan.week_receipt, which builds
  // it on the server where the numbers are.

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

  // The freezer-check ask card's own state (Loop Board "Defrost check: ask
  // at approval") — page-view only, same reasoning as the receipt's own
  // dismissal flag (weekReceiptDismissed): nothing here is the server's
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

  // The ask itself, unchanged in everything but its frame: it used to be a
  // full .plan-nudge-card of its own under the receipt (eyebrow FREEZER
  // CHECK, a title, a body line, then the chips), and it is now the body
  // that "Anything in the freezer?" expands in place — so the chips and the
  // two answers are what is left, and the question is the line above it
  // (Emily's approved design, 2026-09-08). Same id, so wireDefrostAskCard
  // and submitDefrostAsk find it exactly as before.
  function defrostAskCardHtml() {
    var items = defrostAskState.items || [];
    return (
      '<div class="wk-quick-body defrost-ask-card" id="defrost-ask-card"' +
          (weekQuickOpen.defrost ? '' : ' hidden') + '>' +
        '<div class="wk-quick-body-line">Tap what’s frozen and I’ll tell you when to move it to the fridge.</div>' +
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
    // "Not now" went with the card's own header: a line that is collapsed
    // until you tap it is already "not now", and a dismiss button inside it
    // would answer a question you had to open in order to decline.
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
    // The ask is a line inside the receipt now, so forcing it open means
    // three things rather than one: un-dismiss the receipt (it may have
    // been sent away with "See the week" this session), expand this line,
    // and come back out to the ROOT, since the band above the week card is
    // hidden on the Day and Meal steps (renderMealsStep).
    if (weekState.data) setWeekReceiptDismissed(weekState.data.weekly_plan_id, false);
    weekQuickOpen.defrost = true;
    weekState.step = 'week';
    activateTab('week', true);
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

  // Same fold as the freezer check just above: the blocks and the two
  // answers, expanded in place by the "… Cook ahead?" line rather than
  // stacked as a second full card under the receipt. Same id, so
  // wireCookAheadAskCard and submitCookAheadAsk are untouched.
  function cookAheadAskCardHtml() {
    var items = cookAheadAskState.items || [];
    return (
      '<div class="wk-quick-body cook-ahead-ask-card" id="cook-ahead-ask-card"' +
          (weekQuickOpen.cookAhead ? '' : ' hidden') + '>' +
        '<div class="wk-quick-body-line">Tick the days a batch should cover and they become one cook.</div>' +
        items.map(cookAheadAskBlockHtml).join('') +
        // One answer for the whole ask, and no second apricot: the receipt
        // above already spent this screen's one apricot primary on "Open
        // the list" (Rule 5), and .ny-actions .btn-gold is spruce here for
        // exactly that reason.
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
      refreshKitchenPanel(); // Kitchen, if it's built, now has fewer cooks and some made-ahead days
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
    // Same three things as openDefrostAskFromCook just above: un-dismiss
    // the receipt this line lives in, expand the line, and come back out to
    // the ROOT, where the band above the week card is shown.
    if (weekState.data) setWeekReceiptDismissed(weekState.data.weekly_plan_id, false);
    weekQuickOpen.cookAhead = true;
    weekState.step = 'week';
    activateTab('week', true);
    var panel = panels['week'];
    if (!panel) return;
    if (weekState.data) renderWeekApproval(panel, weekState.data);
    else loadWeekMenu(panel);
  }

  // ---------- Above the week card: one thing at a time ----------
  // Emily's approved 2026-09-08 design gives #week-approve-row exactly one
  // job per state, and never two cards stacked:
  //
  //   DRAFT  a hard allergen clash, and nothing else. Review IS the week
  //          card below; its Approve button sits under it (weekDecideHtml).
  //   SET    the receipt and the two asks that are still open — until it is
  //          dismissed, after which the week card is the top of the screen.
  //
  // What went: the old #week-review-band with its "DRAFT · YOUR TURN"
  // eyebrow, status line and grocery promise; and the receipt's own long
  // body, its "your list is ready" handoff and the two full-height nudge
  // cards under it. Between them they filled a phone viewport and pushed
  // the week itself below the fold, which is the whole problem this
  // redesign exists to fix.
  function renderWeekApproval(panel, data) {
    var row = panel.querySelector('#week-approve-row');
    if (!row) return;
    if (!data.weekly_plan_id) { row.innerHTML = ''; return; }
    if (data.status === 'approved') renderWeekReceipt(row, panel, data);
    else renderWeekSettle(row, panel, data);
  }

  // ---------- DRAFT: "One thing to settle" ----------
  // Only ever a HARD clash — an allergy or a must-avoid. A soft one (a
  // dislike, somebody at the table not keen) gets no card at all: it is a
  // preference, it never gates approval, and it is one quiet line under the
  // card instead (weekNotesHtml). The sentence and the count are the
  // server's (coordination._settle), so the wording lives with the data.
  function weekSettleTargetSlot(day, meal) {
    var found = null;
    WEEK_SLOTS.forEach(function (slot) {
      var entry = day && day[slot];
      if (!found && entry && entry.state === 'planned' && entry.title === meal) found = slot;
    });
    return found;
  }

  // "Swap the salsa" — the dish's last word, which is what a person calls
  // it once the sentence above has already named it in full. Kept to the
  // last word so the two segments stay side by side at 390px; a one-word
  // dish is its own short name.
  function dishShortName(meal) {
    var words = String(meal || '').trim().split(/\s+/);
    var last = words[words.length - 1] || '';
    if (words.length < 2 || last.length < 4) return meal;
    return last.toLowerCase();
  }

  function renderWeekSettle(row, panel, data) {
    var settle = data.settle;
    if (!settle || !settle.note) { row.innerHTML = ''; return; }
    var count = settle.count || 1;
    var title = count === 1 ? 'One thing to settle' : spellSmallNumber(count) + ' things to settle';
    row.innerHTML =
      '<div class="shell-card wk-settle-card">' +
        '<div class="wk-settle-title">' + escapeHtml(title) + '</div>' +
        '<div class="wk-settle-note">' + escapeHtml(settle.note) + '</div>' +
        '<div class="wk-settle-acts">' +
          '<button type="button" class="wk-settle-swap" id="wk-settle-swap">' +
            escapeHtml('Swap the ' + dishShortName(settle.meal)) + '</button>' +
          '<button type="button" class="wk-settle-keep" id="wk-settle-keep">Keep it anyway</button>' +
        '</div>' +
      '</div>';
    row.querySelector('#wk-settle-swap').addEventListener('click', function () {
      // Straight to the meal the clash is about, where Swap already lives —
      // the Day step if the exact slot can't be identified (a component
      // plan has no dates), never a dead end.
      var index = -1;
      weekState.days.forEach(function (d, i) { if (d.date === settle.date) index = i; });
      if (index < 0) {
        // No day to land on (a component plan has no dates): hand it to
        // the ask sheet with the swap already worded, never a dead tap.
        openAskSheet('Swap ' + settle.meal + ' for something else');
        return;
      }
      var slot = weekSettleTargetSlot(weekState.days[index], settle.meal);
      if (slot) goMealsStep('meal', { dayIndex: index, slot: slot });
      else goMealsStep('day', { dayIndex: index });
    });
    row.querySelector('#wk-settle-keep').addEventListener('click', function () {
      // Deliberately the ordinary approve path, not a shortcut past it:
      // approve_weekly_plan answers a hard clash with needs_confirmation and
      // writes nothing, so this posts, comes back refused, and
      // showApproveConfirm turns the Approve button under the card into
      // "Approve anyway — I've seen the … clash". Keeping it anyway still
      // costs the second, explicit tap that a real allergy clash is owed.
      approveWeek(panel, data);
    });
  }

  // ---------- SET: the receipt, and the two asks still open ----------
  // Dismissal is per weekly_plan_id in sessionStorage, deliberately: it has
  // to survive a tab switch (the panel re-renders every time Meals comes
  // back, and a receipt that reappeared after "See the week" would be
  // ignoring the tap) but NOT a new session — a week approved yesterday
  // opens on the week card, not on a receipt for a decision already made.
  // Nothing here is the server's business, which is why it isn't a column.
  var WEEK_RECEIPT_DISMISS_KEY = 'pomona.weekReceiptDismissed.';

  function weekReceiptDismissed(planId) {
    try { return sessionStorage.getItem(WEEK_RECEIPT_DISMISS_KEY + planId) === '1'; }
    catch (err) { return false; }
  }
  function setWeekReceiptDismissed(planId, dismissed) {
    try {
      if (dismissed) sessionStorage.setItem(WEEK_RECEIPT_DISMISS_KEY + planId, '1');
      else sessionStorage.removeItem(WEEK_RECEIPT_DISMISS_KEY + planId);
    } catch (err) { /* private mode: the receipt just stays up for this view */ }
  }

  // Which of the two asks is expanded, page-view only. Held outside the
  // render because the cook-ahead chips re-render this whole row on every
  // tap (the count line under them has to change with the chip), and a
  // question that collapsed under your thumb would be unusable.
  var weekQuickOpen = { defrost: false, cookAhead: false };

  function spellSmallNumber(n) {
    var words = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven',
                 'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve'];
    return words[n] || String(n);
  }

  // One ask, folded to a line: the question, what it is about, and "Ask".
  // The ask's own UI is still exactly the one it always had — it is just
  // hidden until this line is tapped, rather than being a card of its own.
  function weekQuickLineHtml(line) {
    var open = !!line.open;
    return '<div class="wk-quick-line">' +
      '<button type="button" class="wk-quick-head" data-quick="' + line.key + '" ' +
          'aria-expanded="' + open + '">' +
        '<span class="wk-quick-text">' +
          '<span class="wk-quick-q">' + escapeHtml(line.q) + '</span>' +
          (line.sub ? '<span class="wk-quick-sub">' + escapeHtml(line.sub) + '</span>' : '') +
        '</span>' +
        '<span class="wk-quick-ask">' + (open ? 'Close' : 'Ask') + '</span>' +
      '</button>' +
      line.body +
    '</div>';
  }

  function renderWeekReceipt(row, panel, data) {
    if (weekReceiptDismissed(data.weekly_plan_id)) { row.innerHTML = ''; return; }

    // The freezer check (Loop Board "Defrost check: ask at approval") and
    // the cook-ahead offer, both gated exactly as they were: once per plan
    // by their own asked_at column, with forceShow the one-shot override
    // the Cook view's re-ask links set. Only the presentation changed.
    var forceDefrostShow = defrostAskState.forceShow;
    var defrostHtml = '';
    if (!data.defrost_asked_at || forceDefrostShow) {
      if (defrostAskState.planId === data.weekly_plan_id && defrostAskState.items !== null) {
        // The one-shot override is spent only once the line can actually
        // render — the first pass often just starts the fetch, and the
        // re-render it triggers must still see the override.
        defrostAskState.forceShow = false;
        if (defrostAskState.items.length) defrostHtml = defrostAskCardHtml();
      } else {
        ensureDefrostAskItems(panel, data); // re-renders this row once it resolves
      }
    }
    var forceCookAheadShow = cookAheadAskState.forceShow;
    var cookAheadAskHtml = '';
    if (!data.cook_ahead_asked_at || forceCookAheadShow) {
      if (cookAheadAskState.planId === data.weekly_plan_id && cookAheadAskState.items !== null) {
        cookAheadAskState.forceShow = false;
        if (cookAheadAskState.items.length) cookAheadAskHtml = cookAheadAskCardHtml();
      } else {
        ensureCookAheadAskItems(panel, data); // re-renders this row once it resolves
      }
    }

    var lines = [];
    if (defrostHtml) {
      lines.push({
        key: 'defrost', open: weekQuickOpen.defrost, body: defrostHtml,
        q: 'Anything in the freezer?',
        sub: defrostAskSummary()
      });
    }
    if (cookAheadAskHtml) {
      lines.push({
        key: 'cookAhead', open: weekQuickOpen.cookAhead, body: cookAheadAskHtml,
        q: cookAheadAskQuestion(),
        sub: cookAheadAskSummary()
      });
    }

    var receipt = data.receipt || {};
    row.innerHTML =
      '<div class="shell-card week-receipt-card">' +
        '<div class="week-receipt-eyebrow">YOUR WEEK IS SET</div>' +
        '<div class="week-receipt-title">' + escapeHtml(receipt.title || 'Your week is set.') + '</div>' +
        (receipt.thaw_line
          ? '<div class="week-receipt-line">' + escapeHtml(receipt.thaw_line) + '</div>' : '') +
        '<div class="week-receipt-acts">' +
          // The screen's one apricot in the SET state (Rule 5) — the draft's
          // Approve button is gone by now, and the asks below are quiet.
          '<button type="button" class="btn-gold week-receipt-go" id="week-receipt-go">Open the list</button>' +
          '<button type="button" class="week-receipt-see" id="week-receipt-see">See the week</button>' +
        '</div>' +
      '</div>' +
      (lines.length
        ? '<div class="shell-card wk-quick-card">' +
            '<div class="wk-quick-title">' +
              (lines.length === 1 ? 'One quick one before you go' : 'Two quick ones before you go') +
            '</div>' +
            lines.map(weekQuickLineHtml).join('') +
          '</div>'
        : '');

    if (defrostHtml) wireDefrostAskCard(row, panel, data);
    if (cookAheadAskHtml) wireCookAheadAskCard(row, panel, data);
    row.querySelectorAll('[data-quick]').forEach(function (head) {
      head.addEventListener('click', function () {
        var key = head.getAttribute('data-quick');
        weekQuickOpen[key] = !weekQuickOpen[key];
        renderWeekApproval(panel, data);
      });
    });
    row.querySelector('#week-receipt-go').addEventListener('click', function () {
      // Plan stops is the screen that's actually about the trip the receipt
      // just promised, not just the list itself.
      activateTab('grocery', true, { groScreen: 'plan' });
    });
    row.querySelector('#week-receipt-see').addEventListener('click', function () {
      setWeekReceiptDismissed(data.weekly_plan_id, true);
      renderWeekApproval(panel, data);
      if (scrollEl) scrollEl.scrollTop = 0;
    });
  }

  // "Chicken thighs · Salmon · Ground beef" — the chips, collapsed. Three
  // and a count, because a line is a line: the rest are all still there the
  // moment the ask is opened.
  function defrostAskSummary() {
    var items = (defrostAskState.items || []).map(function (it) { return it.item; });
    if (!items.length) return '';
    if (items.length <= 3) return items.join(' · ');
    return items.slice(0, 3).join(' · ') + ' · +' + (items.length - 3);
  }

  function cookAheadAskQuestion() {
    var items = cookAheadAskState.items || [];
    if (items.length !== 1) return 'Cook anything ahead?';
    var item = items[0];
    var total = (item.later || []).length + 1;
    return item.dish + ' on ' + total + ' ' + cookSlotWord(item.slot, total) + '. Cook ahead?';
  }

  function cookAheadAskSummary() {
    var items = cookAheadAskState.items || [];
    if (items.length < 2) return '';
    return items.map(function (item) {
      var total = (item.later || []).length + 1;
      return item.dish + ' on ' + total + ' ' + cookSlotWord(item.slot, total);
    }).join(' · ');
  }

  // All FOUR meal types, not the three of WEEK_SLOTS (Emily, 2026-09-10).
  // This button is a promise that nothing is left to decide, and an open
  // snack is something left to decide — so counting three slots out of four
  // made it a promise the screen beside it already contradicted: "Which
  // days" said `SNACK 2 · Your call` while the button read "Approve and
  // build my shopping list". One tap away since the Review stepper started
  // working on snacks.
  //
  // WEEK_SLOTS itself is untouched, deliberately: it stays the three real
  // meals so a snack is never counted as a cook or against the 21-slot
  // guarantee, and four other readers depend on that. The widening belongs
  // to this count, so it asks daySlotKeys what the day is actually made of
  // instead of asking WEEK_SLOTS a question WEEK_SLOTS does not answer.
  function countOpenSlots(data) {
    var n = 0;
    (data.days || []).forEach(function (day) {
      daySlotKeys(day).forEach(function (s) {
        var e = daySlotEntry(day, s);
        if (e && e.state === 'open') n++;
      });
    });
    return n;
  }

  function approveWithOpenLabel(data, openCount) {
    if (openCount > 1) return 'Approve — leave ' + openCount + ' slots open';
    var openDay = null;
    (data.days || []).forEach(function (day) {
      daySlotKeys(day).forEach(function (s) {
        var e = daySlotEntry(day, s);
        if (e && e.state === 'open' && !openDay) openDay = day.date;
      });
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
    //
    // `approving_adults`, not `other_adults`: the latter is every adult but
    // the one who ALREADY approved, so on a draft — the only state this
    // button exists in — it was always empty and this step never once ran.
    // See get_week_menu, where both fields are set side by side.
    //
    // Since 2026-09-11 the session usually already knows: the adult picked
    // at "Who's this?" (shellWho.member). Then there is no question to ask
    // — the name goes on the week straight away, and the server would fill
    // it in from the session even if this sent nothing. The picker below
    // is the fallback for a device with no pick (an older cookie, or the
    // lookup failed at boot).
    var people = (data.approving_adults || []);
    var approvedBy = shellWho.member ? shellWho.member.name : '';
    if (!approvedBy && people.length > 1) {
      approvedBy = await askWhoIsApproving(people);
      if (approvedBy === null) return;
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

  // The one-clash-away state: the "One thing to settle" card above the week
  // already names the clash (data.settle, rendered before anyone even
  // tapped Approve), so this only has to offer the two ways through —
  // approve past it, or back out and fix the plan first. Only
  // submitWeekApproval's needs_confirmation branch ever calls this.
  //
  // It works on the decision row under the week card now (weekDecideHtml)
  // rather than inside the removed review band; everything else about it —
  // the relabelled button, the swapped handler, the quiet way out — is
  // unchanged, and the second, explicit tap is still what the backend is
  // waiting for.
  function showApproveConfirm(panel, data, approval, approvedBy) {
    var card = panel.querySelector('.wk-decide');
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
    // "Keep it anyway" on the settle card comes through here too, and it is
    // tapped ABOVE the week card while this button sits below it — so the
    // confirm has to be brought to the eye rather than left offscreen.
    if (freshBtn.scrollIntoView) freshBtn.scrollIntoView({ block: 'center' });

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
    setDishIndex(data);
    var todayStr = todayLocalStr();
    var days = (data.days || []).map(function (d) { return Object.assign({}, d, classifyDay(d, todayStr)); });
    weekState.days = days;

    // The review band's status line went with the band (Emily's approved
    // design, 2026-09-08). Everything it said, the screen now says in the
    // place that owns the fact: the badge and subtitle say whether the week
    // is a draft and whose turn it is, the seven rows say where the gaps
    // are, and the receipt says what a settled week came to. One sentence
    // repeating all three above the card was the top of the fold spent on
    // a summary of what was directly underneath it.

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

    renderWeekApproval(panel, data);
    renderMealsStep(panel);
    renderWeekSheetRows(days);
    // Chat's "See your week" chip may have asked for a specific day before
    // this week's days existed — now they do. No-op unless one is pending.
    applyPendingDayFocus(panel);
  }

  // ---------- Cook mode: a step of the Kitchen tab ----------
  //
  // The same /api/cooker-view data static/cooker.html always used. That
  // page rendered the whole week as one flat stack and then scrolled you
  // to today; the Kitchen root above answers "what am I cooking now" and
  // this is what opens when you tap one of its lines — one meal, the whole
  // screen, until "‹ Kitchen" or "Mark it cooked" brings you back.
  //
  // It was a state of the MEALS tab until 2026-09-08 (a Plan | Cook
  // segmented control at /week). Emily's approved design moved it here:
  // the tab you cook from should not be the tab you plan from, and the
  // cook overview this used to open on is the Kitchen root itself.
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
    focusMealKey: null,  // ...and WHICH dish that index is supposed to be, by
                         // cookMealKey. The index is only as good as the array
                         // it points into, and every load rebuilds that array.
    focusScrollTo: null, // 'ingredients' | null — landed-on section, once
    // Which of cook mode's three stages is showing. Cook mode used to be
    // one long screen — hero, prep, the whole recipe — and the ticket
    // ("Cooking: before you start, one step at a time, and a proper
    // finish", Emily 2026-09-09) splits it into the three things a person
    // actually does in order:
    //   'prep'   — Before you start. Everything out of the cupboard as a
    //              ticklist with quantities, plus the pans you'll want.
    //   'step'   — One step at a time, big type. The DEFAULT once you
    //              start: Emily's choice, "best when your hands are busy
    //              and the phone is across the counter".
    //   'method' — The whole method on one screen, tick as you go. One tap
    //              away from either of the others, for people who cook by
    //              skimming ahead or juggling two pans.
    // These are stages of the SAME step of the Kitchen tab, not steps of
    // their own: the back link still goes up one level by name to wherever
    // cook mode was opened from, and moving between stages never touches
    // history. Same rule Grocery's shopping mode follows inside its trip.
    focusStage: 'prep',
    stepIdx: 0,          // which instruction the 'step' stage is showing, 0-based into the FULL instructions array
    // The stage "The whole method" was opened from, so leaving it puts you
    // back exactly where you were rather than at the top of the recipe —
    // "switching back keeps your place" is the acceptance criterion, and
    // the place is a stage plus, for 'step', stepIdx (which method never
    // changes).
    methodFrom: 'prep',
    // Where the cook screen was opened FROM, when that wasn't Kitchen:
    // { label, tab, mealsDay, mealsSlot } — see openRecipeFor. Cook mode is
    // a step of Kitchen, so its back link has always said "‹ Kitchen"; once
    // a dish name anywhere in the app opens it (Emily, 2026-09-09: "click
    // the meal anywhere throughout the app, it should bring you to the
    // screen with the recipe on it") a link saying Kitchen would be naming
    // somewhere the person has never been. Same rule as every other deeper
    // screen here — the link goes up a level BY NAME and lands back there,
    // and it is never history.back(). Null is the ordinary case: you came
    // from Kitchen, so Kitchen is what it says.
    focusOrigin: null,
    // Set by a "Start cooking"/"Cook this" deep link that arrives before the
    // view has ever loaded. Either `true` (the old "focus whatever tonight
    // turns out to be" behaviour, still used by callers that have no
    // specific meal in hand) or `{ entryId }` naming the exact meal_plan
    // entry to land on — see cookResolveFocusIndex.
    pendingFocusTarget: false,
    pendingScrollTop: false,    // this render is a screen change, not a re-paint — reset scroll instead of preserving it
    ticks: null,                // the ticked ingredients and steps for the plan named by ticksFor — see cookReadTicks
    serves: {},                 // mealKey -> { servings, ingredients, unscaled_items, ... } — the cook's own serving count. Stored beside the ticks and read back with them (see cookReadTicks), so a load, a tab switch and a reload all leave it standing
    servesSeq: 0,               // sequence token, so a superseded /scale reply loses instead of racing
    ticksFor: null,             // which weekly_plan_id `ticks` was read for
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

  function cookPanel() { return panels['kitchen']; }

  // ---------- What's ticked, and where it lives ----------
  // Three ticklists meet on this screen and only one of them already had a
  // home. PREP ticks are the server's and always were (prep_tasks.status
  // through check_off_prep_step) — nothing here touches them. The two the
  // cook journey adds have no column behind them anywhere: the ingredients
  // you've got out on "Before you start", and the steps you've done.
  //
  // They used to be a bare object on cookState, thrown away with the page,
  // and the ticket's acceptance criterion is that they survive leaving the
  // screen and coming back. That has to include a real reload: an
  // installed PWA has its web view discarded the moment the phone is put
  // down mid-cook, which is precisely when somebody comes back to it.
  //
  // So: localStorage, keyed by the WEEKLY PLAN's id. Two reasons for that
  // key rather than a household one. Plan ids are unique across
  // households, so two households sharing a device can never read each
  // other's ticks without this code knowing anything about households; and
  // a new week is a new plan id, so last week's ticks expire by
  // construction rather than by a sweep somebody has to remember to write.
  //
  // Per-device rather than a server column, deliberately — the same call
  // the coaching visit counters made, for the same kind of state. A
  // half-cooked recipe is a live, one-phone, one-hour thing, and two
  // people cooking two different dishes on two phones must not tick each
  // other's steps. **Open for Emily:** if a cook should follow you from
  // the phone to the tablet mid-recipe, that is a real column and a write
  // per tap — worth asking for, not worth assuming.
  //
  // THE SERVING COUNT LIVES HERE TOO (Emily, 2026-09-10). It used to be
  // held for the page's life only, which meant a reload left a half-ticked
  // ingredient list at amounts nobody chose: you ticked "4 chicken thighs",
  // came back, and the row said 2 and was still ticked. That is not an
  // inconsistency, it is a screen that is wrong — and the reason it is
  // wrong is that the ticks and the count describe the same cooking
  // session, so they have to end at the same moment. One record, one key,
  // one expiry: a new week is a new plan id and both go together.
  //
  // Still per device and still not sent to the server. The serving count is
  // one cook overriding tonight at the counter, not a change to who lives
  // in the house, and nothing about surviving a reload makes it the
  // household's answer instead of this cook's.
  var COOK_TICKS_PREFIX = 'pomona.cookTicks.p';

  // A meal's identity in the tick store. Never the index into
  // cookState.data.meals, which is what the old in-memory keys used: the
  // array is rebuilt by every load and every write response, so an index
  // that survived a reload would put last night's ticks on tonight's dish.
  // A component-based week's cards carry no entry ids at all (their date is
  // a placeholder — see get_cooker_view), so the dish's own name is the
  // fallback, exactly as cookResolveFocusIndex already falls back to it.
  function cookMealKey(meal) {
    if (!meal) return '';
    if (meal.entry_id !== null && meal.entry_id !== undefined) return 'e' + meal.entry_id;
    return 'n' + String(meal.meal || '').trim().toLowerCase();
  }

  function cookTickPlanId() {
    var d = cookState.data;
    return (d && d.weekly_plan_id !== null && d.weekly_plan_id !== undefined) ? d.weekly_plan_id : null;
  }

  // Every read and write is wrapped: Safari in private mode throws on
  // localStorage rather than returning null, and a thrown memory aid must
  // not take the cook screen down with it (the same guard coachReadVisits
  // carries, and the same reason). A plan-less view gets a scratch object
  // so the screen still renders and ticks still work for the page's life.
  function cookReadTicks() {
    var planId = cookTickPlanId();
    if (cookState.ticks && cookState.ticksFor === planId) return cookState.ticks;
    var parsed = null;
    if (planId !== null) {
      try {
        var raw = window.localStorage.getItem(COOK_TICKS_PREFIX + planId);
        parsed = raw ? JSON.parse(raw) : null;
      } catch (err) { parsed = null; }
    }
    if (!parsed || typeof parsed !== 'object') parsed = {};
    cookState.ticks = {
      steps: (parsed.steps && typeof parsed.steps === 'object') ? parsed.steps : {},
      ings: (parsed.ings && typeof parsed.ings === 'object') ? parsed.ings : {}
    };
    // The serving overrides ride in the same record and are hydrated with
    // it — exactly once per plan, since this function returns early once
    // ticksFor matches. Hydrating on every call would put the stored
    // number back over a tap that had not been written yet.
    cookState.serves = cookReadServes(parsed.serves);
    cookState.ticksFor = planId;
    return cookState.ticks;
  }

  // Only entries that still describe a rescale get through. A stored blob
  // is a week old at most, but it is read back into the amounts a person
  // cooks from, so a half-written or hand-edited one is dropped rather
  // than rendered: a servings count with no ingredient list behind it
  // would show a number over amounts that never moved.
  function cookReadServes(raw) {
    var out = {};
    if (!raw || typeof raw !== 'object') return out;
    Object.keys(raw).forEach(function (key) {
      var o = raw[key];
      if (!o || typeof o !== 'object') return;
      if (!(parseInt(o.servings, 10) > 0) || !Array.isArray(o.ingredients)) return;
      out[key] = {
        servings: parseInt(o.servings, 10),
        ingredients: o.ingredients,
        unscaled_items: Array.isArray(o.unscaled_items) ? o.unscaled_items : [],
        was_batch: !!o.was_batch,
        planned_for: parseInt(o.planned_for, 10) || null
      };
    });
    return out;
  }

  function cookWriteTicks() {
    var planId = cookTickPlanId();
    if (planId === null || !cookState.ticks) return;
    try {
      window.localStorage.setItem(COOK_TICKS_PREFIX + planId, JSON.stringify({
        steps: cookState.ticks.steps,
        ings: cookState.ticks.ings,
        serves: cookState.serves
      }));
      // Every other plan's ticks belong to a week that is over. Nobody is
      // coming back for them, and a store that only ever grows is a store
      // that eventually throws on a quota nobody was watching.
      for (var i = window.localStorage.length - 1; i >= 0; i--) {
        var k = window.localStorage.key(i);
        if (k && k.indexOf(COOK_TICKS_PREFIX) === 0 && k !== COOK_TICKS_PREFIX + planId) {
          window.localStorage.removeItem(k);
        }
      }
    } catch (err) { /* see cookReadTicks */ }
  }

  function cookTicked(kind, key) { return !!cookReadTicks()[kind][key]; }

  function cookToggleTick(kind, key) {
    var store = cookReadTicks();
    if (store[kind][key]) delete store[kind][key]; else store[kind][key] = 1;
    cookWriteTicks();
  }

  // Set rather than toggle — "Next step" says a step is done, and a Next
  // tapped on a step already ticked (you stepped back to re-read it) must
  // not quietly untick it on the way past.
  function cookSetTick(kind, key, on) {
    var store = cookReadTicks();
    if (on) store[kind][key] = 1; else delete store[kind][key];
    cookWriteTicks();
  }

  // Entering cook mode on ONE meal. Every entry point comes through here
  // (activateTab's opts.cookFocus): Today's Next up card and its move
  // lines, Meals' "Cook this", Grocery's shop-done handoff, and Kitchen's
  // own "Cooking today" lines. `focusTarget` is `{entryId, date, slot,
  // title}` when the caller knows the meal, or the legacy `true` for a
  // caller that only means "tonight, whatever that turns out to be" — a
  // generic flag with no meal identity, which is how this used to land on
  // the morning's breakfast when tapped before dinner (Loop Board, Emily
  // 2026-09-07).
  //
  // A target that arrives before the tab has ever loaded — the common
  // case, since Kitchen is lazy-built — is parked on cookState and
  // honoured by loadKitchen the moment the data lands.
  function kitchenEnterCook(focusTarget) {
    var panel = kitchenPanel();
    if (!panel || !panel.dataset.built) return;
    if (kitchenState.loading || !cookState.data) {
      cookState.pendingFocusTarget = focusTarget || false;
      return;
    }
    var idx = cookResolveFocusIndex(cookState.data.meals || [], focusTarget);
    if (idx !== null && cookState.data.meals[idx]) cookEnterFocus(idx);
  }

  // ---------- A dish name is a link to its recipe ----------
  // Emily, 2026-09-09, after testing the app: "if you click the meal
  // anywhere throughout the app, it should bring you to the screen with
  // the recipe on it."
  //
  // There is exactly ONE screen in this app with a recipe on it — cook
  // mode, the focused single-meal step of Kitchen — so that is where a
  // dish name goes, rather than a new screen nobody asked for. Every
  // caller comes through here so the back link, the tab switch and the
  // "there is nothing to open" case are decided in one place instead of
  // five.
  //
  // `origin` is what the cook screen's back link will SAY and where it
  // will land: { label, tab } for a plain tab, plus mealsDay/mealsSlot to
  // come back to the exact Meals step you left. Omit it from inside
  // Kitchen — that is the ordinary "‹ Kitchen" case.
  function openRecipeFor(target, origin) {
    if (!target) return;
    cookState.focusOrigin = origin || null;
    activateTab('kitchen', true, { cookFocus: target });
  }

  // The words on the cook screen's back link. Never a guess: either the
  // origin said its own name or you came from Kitchen.
  function cookBackLabel() {
    // The tab's own name is the fallback, and it changed on 2026-09-09:
    // Kitchen became Cook. Two branches met here — one renamed the tabs, the
    // other made this label say where you actually came from — and the
    // dynamic version is the one that survived, so the rename lives in its
    // default rather than in three hardcoded buttons.
    var origin = cookState.focusOrigin;
    return (origin && origin.label) ? origin.label : 'Cook';
  }

  // A reheat night is not a way into a recipe — there is no cook here, so
  // there is nothing for a cook screen to hold (the rule Kitchen's own
  // rows already follow, Emily 2026-09-04). Everything else that names a
  // real plan entry is: a dish with no saved recipe still opens, and says
  // plainly that there isn't one (cookDetailHtml), which beats a name that
  // looks tappable and does nothing.
  function recipeTargetForEntry(entry, date, slot) {
    if (!entry || entry.state === 'planned_empty' || entry.state === 'open') return null;
    if (entry.source === 'leftovers') return null;
    if (entry.entry_id == null && !entry.title) return null;
    return {
      entryId: entry.entry_id != null ? entry.entry_id : null,
      date: date || null,
      slot: isSnackSlot(slot || '') ? 'snack' : (slot || null),
      title: entry.title || ''
    };
  }

  // Turns a cookFocus target into an index into cookState.data.meals — or
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

  // The hands-free status/log panel. It used to sit on the Cook overview;
  // both mics are on a cook screen now (the recipe's own, and the prep
  // section's), so it renders with them. Gated like everything else voice:
  // while COOK_VOICE_ENABLED is false there is no panel and no session.
  function cookVoicePanelHtml() {
    return COOK_VOICE_ENABLED ? '<div class="cook-voice" id="cook-voice" hidden></div>' : '';
  }

  // Keep the focus pointing at the dish it was opened on. Lifted out of
  // renderCook so it can be run on its own in a test — the failure it
  // exists to stop only happens across a data swap, which is exactly what
  // a single-render test cannot reach.
  function cookFollowFocusedMeal(meals) {
    if (!cookState.focusMealKey) return;
    var here = meals[cookState.focusIdx];
    if (here && cookMealKey(here) === cookState.focusMealKey) return;
    for (var i = 0; i < meals.length; i++) {
      if (cookMealKey(meals[i]) === cookState.focusMealKey) {
        cookState.focusIdx = i;
        return;
      }
    }
    cookState.screen = 'overview';
  }

  // Which of the tab's two screens is showing. The root (renderKitchen) is
  // the "overview" state this used to render itself; focus and session are
  // the two screens that take the whole tab over for one job.
  function renderCook() {
    var panel = kitchenPanel();
    if (!panel) return;
    var rootView = panel.querySelector('#kit-root-view');
    var view = panel.querySelector('#kit-cook-view');
    if (!rootView || !view) return;

    // Hold the scroll across a re-render — the same rule the Grocery panel
    // follows, and it matters more here: a re-render happens every time a
    // box is ticked, and a cook is mid-recipe when they tick one.
    var keepScroll = scrollEl ? scrollEl.scrollTop : 0;

    var data = cookState.data;
    // Every fresh view — a load, or the one a write hands back — is the
    // truth about which days are ticked, so the cook-ahead chips go back
    // to reading it rather than to whatever was tapped before it arrived.
    if (data && cookState.cookAheadFrom !== data) {
      cookState.cookAheadFrom = data;
      cookState.cookAheadPicks = {};
    }
    var meals = (data && data.meals) || [];
    // Pinned by loadKitchen; only worked out here if a write response
    // arrived before any load ever did, or if the pinned index is gone.
    if (cookState.tonightIdx === null || cookState.tonightIdx === undefined ||
        !meals[cookState.tonightIdx]) {
      cookState.tonightIdx = cookTonightIndex(meals);
    }
    // The focus is an INDEX, and every load and every write response
    // rebuilds `meals` — so before trusting it, check it still names the
    // dish that was opened. It used not to: loadKitchen re-pinned
    // tonightIdx and left focusIdx, focusStage and stepIdx exactly where
    // they were, so a chat turn tagged tab:'kitchen' arriving mid-cook
    // could render "Step 3 of 4" of whatever dish now sat at that index —
    // and, if the new one had fewer steps, hand the cook the "Mark it
    // cooked" finish of a dish they never started. The dish is followed by
    // identity if it merely moved; the screen falls back to the root if it
    // is gone, rather than showing a cook screen for a meal that no longer
    // exists. Same guard for a prep session that stopped existing.
    // Before anything is drawn: the cook's own serving count goes back on
    // over whatever this data came with, whether that was a load, a write
    // response or a tab switch.
    cookApplyServesOverride(meals);
    if (cookState.screen === 'focus') cookFollowFocusedMeal(meals);
    if (cookState.screen === 'focus' && !meals[cookState.focusIdx]) cookState.screen = 'overview';
    if (cookState.screen === 'session' && !cookSessionOn(data, cookState.sessionDate)) cookState.screen = 'overview';
    if (cookState.loadError || !data) cookState.screen = 'overview';

    var onRoot = cookState.screen === 'overview';
    var onFocus = false;
    rootView.hidden = !onRoot;
    view.hidden = onRoot;
    if (onRoot) {
      view.innerHTML = '';
      renderKitchen();
    } else if (cookState.screen === 'session') {
      view.innerHTML = cookVoicePanelHtml() +
        cookSessionHtml(data, cookSessionOn(data, cookState.sessionDate), meals);
    } else {
      view.innerHTML = cookVoicePanelHtml() + cookFocusHtml(data, meals, cookState.focusIdx);
      // BEFORE the scroll is restored: this changes the height of the
      // content the scroll position is measured against.
      wireCookDock(view);
      onFocus = true;
    }

    updateCookVoiceButtons();
    // Entering/leaving a screen resets to the top (Grocery's shopping-mode
    // precedent does the same); every other render — a step checked, a box
    // ticked — keeps the reader's place.
    if (scrollEl) scrollEl.scrollTop = cookState.pendingScrollTop ? 0 : keepScroll;
    cookState.pendingScrollTop = false;
    // AFTER the scroll is settled, never before: this hook's whole job is
    // to land on a section, and a restore running behind it would put the
    // reader straight back where they weren't. (The 'ingredients' case has
    // always been in the wrong order here; nothing reached it, because
    // every caller passes data-at="steps". Opening the whole method from
    // step seven does reach it, and did nothing until this moved.)
    if (onFocus) wireCookFocusScroll(view);
    // ...and the one thing that overrides both, after the restore rather
    // than before it: someone was promised prep and sent here to see it
    // (the rating toast's "Show me tomorrow", with no cook to focus).
    // Whichever section actually holds it — the loose "Prep to do" list if
    // there is one, the prep sessions otherwise.
    if (onRoot && kitchenState.scrollToPrep) {
      kitchenState.scrollToPrep = false;
      var prepEl = rootView.querySelector('#kit-prep-todo') || rootView.querySelector('#kit-prep-sessions');
      if (prepEl && prepEl.scrollIntoView) prepEl.scrollIntoView({ behavior: 'auto', block: 'start' });
    }
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

  // The overview's spruce "Tonight" hero (cookHeroHtml, and its reheat
  // twin) came out with the overview itself on 2026-09-08: the Kitchen
  // root lists today's cooks as lines, not as one hero standing in for the
  // day, and the meal you actually open takes the whole screen (see
  // cookFocusHtml, which keeps the "for 6" batch chip, the covers note and
  // the cook-ahead picker the hero used to carry). cookReheatCardHtml,
  // which both of them used, is still here — the focused screen for a
  // reheat night is exactly that card and nothing more.

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
    return '<section class="cook-section" id="kit-prep-sessions">' +
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
        '<button type="button" class="crumb on-spruce" data-cook="exit-session">&lsaquo; Cook</button>' +
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

  // The overview's "Prep schedule" two-up rail (cookPrepHtml) came out
  // with the overview on 2026-09-08. Nothing it showed is lost: a fridge
  // move or a prep task due today is a line on Today's timeline
  // (app/tools/moves.py), the rows that belong to a prep day are in that
  // day's session (cookPrepSessionsHtml), and the ones that belong to the
  // meal you are cooking are on the focused screen (cookFocusPrepHtml,
  // which inherited this rail's done-count note and its hands-free mic).

  // Everything that is not today, subordinate: one dense row each — "Tue ·
  // Sesame Salmon Bowls · 25 min". Tapping a name opens the same focused
  // screen today's lines do. Three rows, then "+ N more cooks", because
  // this is the shape of the week ahead and not a second week screen: the
  // Meals tab is where a week is read in full.
  var KITCHEN_REST_VISIBLE = 3;

  function cookRestOfWeekHtml(meals, data, todayIso, expanded) {
    var rest = (meals || [])
      .map(function (m, i) { return { m: m, i: i }; })
      // The days AHEAD — "the rest of the week" is not a place a Monday
      // that already happened belongs. A component-based plan carries a
      // placeholder date (week_start, see get_weekly_plan) rather than a
      // real day, so those are kept on their own terms rather than being
      // filtered out as "past".
      .filter(function (x) {
        return !x.m.date || x.m.component_category || x.m.date > todayIso;
      });
    if (!rest.length) {
      if ((meals || []).length) return '';
      // Every dinner this period was deliberately marked away (cooker.py's
      // all_away flag) — say that, rather than the generic "nothing
      // planned" line, which would read as though the week was simply
      // forgotten.
      return data && data.all_away
        ? '<p class="cook-empty">Nothing to cook this week — you’re away.</p>'
        : '<p class="cook-empty">No meals on this plan yet.</p>';
    }
    var shown = expanded ? rest : rest.slice(0, KITCHEN_REST_VISIBLE);
    var hidden = rest.length - shown.length;
    var hiddenCooks = rest.slice(shown.length).filter(function (x) { return !x.m.is_leftovers; }).length;
    return '<section class="cook-section">' +
      '<div class="cook-sectionhead">' +
        '<span class="cook-eyebrow">The rest of the week</span>' +
        '<span class="cook-rule"></span>' +
      '</div>' +
      '<div class="cook-week">' +
        shown.map(function (x) { return cookRestRowHtml(x.m, x.i); }).join('') +
      '</div>' +
      (hidden
        ? '<button type="button" class="cook-empty-link cook-more-link" data-cook="rest-more">+ ' + hidden +
            ' more ' + (hiddenCooks === hidden ? (hidden === 1 ? 'cook' : 'cooks') : 'to come') +
          '</button>'
        : '') +
    '</section>';
  }

  function cookRestRowHtml(m, idx) {
    var isDone = m.cooked_status === 'done';
    var dayLabel = m.component_category
      ? m.component_category
      : (m.date ? dayName(m.date, { weekday: 'short' }).slice(0, 3).toUpperCase() : '');
    // A reheat night is a row, not a way into a recipe: its name is plain
    // text rather than a button into the focused cook screen, and its box
    // says eaten rather than cooked. It keeps the "Leftovers — Tuesday's
    // Bulgogi" wording the reheat card uses, so the same night reads the
    // same way wherever you meet it.
    var isReheat = !!m.is_leftovers;
    var rowLabel = isReheat ? (m.leftovers_headline || 'Leftovers') : (m.meal || '');
    var checkLabel = isReheat
      ? (isDone ? REHEAT_UNDO_LABEL : REHEAT_ACTION_LABEL)
      : (isDone ? 'Mark not cooked' : 'Mark cooked');
    var minutes = (m.prep_time_minutes || 0) + (m.cook_time_minutes || 0);
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
        (!isReheat && minutes ? '<span class="cook-badge">' + minutes + ' min</span>' : '') +
        (!isReheat && m.advance_prep_notes ? '<span class="cook-badge cook-badge-warm">Prep ahead</span>' : '') +
        (!isReheat && m.batch_note ? '<span class="cook-badge">Bulk ×' + m.meal_count + '</span>' : '') +
      '</div>' +
    '</div>';
  }

  // ---------- What the recipe's own words say you'll need ----------
  // Nothing in this app records a recipe's EQUIPMENT: no column, no field
  // on get_cooker_view, nothing the generator is ever asked for. "Before
  // you start" asks for "the pans you'll want", so the only honest source
  // is the recipe's own steps — if a step says skillet, a skillet is
  // wanted, and if no step names anything the section does not appear
  // rather than inventing one.
  //
  // A keyword list on purpose, the same call plates.is_low_carb made: it
  // runs on every render, the cost of a wrong answer is one extra pan on a
  // list, and a list anyone can read and correct beats a judgment nobody
  // can see. Each entry is [what the step says, what to call it]; several
  // phrases share one name, and a name is only ever listed once.
  var COOK_KIT_WORDS = [
    ['rimmed baking sheet', 'Baking sheet'],
    ['baking sheet', 'Baking sheet'],
    ['sheet pan', 'Baking sheet'],
    ['baking dish', 'Baking dish'],
    ['casserole dish', 'Baking dish'],
    ['roasting pan', 'Roasting pan'],
    ['dutch oven', 'Dutch oven'],
    ['stockpot', 'Large pot'],
    ['large pot', 'Large pot'],
    ['saucepan', 'Saucepan'],
    ['skillet', 'Skillet'],
    ['frying pan', 'Skillet'],
    ['sauté pan', 'Skillet'],
    ['saute pan', 'Skillet'],
    ['griddle', 'Griddle'],
    ['grill', 'Grill'],
    ['wok', 'Wok'],
    ['slow cooker', 'Slow cooker'],
    ['pressure cooker', 'Pressure cooker'],
    ['instant pot', 'Pressure cooker'],
    ['air fryer', 'Air fryer'],
    ['food processor', 'Food processor'],
    ['blender', 'Blender'],
    ['muffin tin', 'Muffin tin'],
    ['loaf pan', 'Loaf pan'],
    ['cake pan', 'Cake pan'],
    ['mixing bowl', 'Mixing bowl'],
    ['large bowl', 'Mixing bowl'],
    ['colander', 'Colander'],
    ['strainer', 'Strainer'],
    ['parchment', 'Parchment paper'],
    ['tin foil', 'Foil'],
    ['aluminum foil', 'Foil'],
    ['aluminium foil', 'Foil']
  ];

  // Whole words only. "Grilled halloumi" is not a reason to get the grill
  // out, and a bare indexOf would say it was.
  //
  // ...and neither is "no skillet needed — use the baking sheet you
  // already have", which is a step going out of its way to tell you NOT to
  // get one out. A mention preceded by no/without/don't-need is discounted
  // rather than the word being dropped from the list: the same shape
  // coordination.py's _COMPOUND_EXCEPTIONS uses, and for the same reason —
  // the word really is standing there, it just isn't saying what a plain
  // match thinks it is.
  function cookKitMentions(hay, phrase) {
    var esc = phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    var word = new RegExp('\\b' + esc + '\\b');
    // Two shapes of refusal: "no skillet needed" and "don't use a
    // skillet" / "no need for a skillet" / "instead of a skillet".
    var negated = new RegExp(
      '\\b(?:no|without|skip|skipping)\\s+(?:the\\s+|a\\s+|an\\s+)?' + esc + '\\b' +
      '|\\b(?:don\'?t|do\\s+not|no\\s+need\\s+(?:to|for)|instead\\s+of|rather\\s+than)\\s+' +
      '(?:use\\s+|using\\s+|reach\\s+for\\s+|get\\s+out\\s+)?(?:the\\s+|a\\s+|an\\s+)?' + esc + '\\b'
    );
    // Clause by clause, so one sentence — "no skillet needed, use the
    // baking sheet you already have" — can turn down the skillet without
    // turning down the baking sheet standing beside it.
    var clauses = hay.split(/[.;,\n]/);
    for (var i = 0; i < clauses.length; i++) {
      if (word.test(clauses[i]) && !negated.test(clauses[i])) return true;
    }
    return false;
  }

  // "Preheat the oven to 425°F" is the one before-you-start fact that
  // costs twenty minutes when it is missed, so it rides at the head of the
  // same list — read out of the step that says it, never invented.
  //
  // REWRITTEN 2026-09-10, because the first version guessed. It asked for
  // "oven" plus a heating word plus any three-digit number anywhere in the
  // step, and "set aside" satisfies the heating word, so it read a
  // meat-probe target ("roast until a probe reads 145°F"), a braise time
  // ("Heat the oven, cover, and braise for 180 minutes") and a resting
  // time ("out of the oven and set aside for 100 minutes") as oven
  // temperatures, said them first in the list with no hedge, and failed
  // silently. It also missed a real one below 100 ("Preheat the oven to
  // 90C"), which the three-digit rule made impossible.
  //
  // The rule now is that the step has to SAY the temperature to the oven:
  // the number must follow "oven to" (or "oven at"/"oven up to") directly,
  // with nothing between but a word like "about". Everything the old
  // version invented fails that, because in every one of those sentences
  // the number belongs to something else. A step that preheats without
  // naming a number, or says "gas mark 6", produces nothing — a quiet miss
  // is this section's stated failure mode, and it is the right one:
  // "everything out of the cupboard" must never list a thing nobody wrote.
  var COOK_OVEN_RE = /\boven\s+(?:up\s+)?(?:to|at)\s+(?:about\s+|around\s+)?(\d{2,3})\s*(?:°|º)?\s*(?:degrees?\s*)?([FfCc])?\b/i;

  function cookOvenLine(steps) {
    for (var i = 0; i < (steps || []).length; i++) {
      var m = COOK_OVEN_RE.exec(String(steps[i] || ''));
      if (!m) continue;
      var temp = parseInt(m[1], 10);
      // A number this small after "oven to" is not a temperature in either
      // scale — it is a rack position or a typo, and either way not
      // something to print as an instruction.
      if (temp < 40) continue;
      // The unit is printed only when the step WROTE one. It used to be
      // inferred from the number (>=250 reads as Fahrenheit), which is a
      // good guess and still a guess — and this is the section whose whole
      // rule is that it never says a thing nobody wrote. "Oven at 200°" is
      // exactly what the recipe said, and a cook who wrote 200 knows which
      // scale they meant.
      return 'Oven at ' + temp + '°' + (m[2] ? m[2].toUpperCase() : '');
    }
    return '';
  }

  function cookKitFor(meal) {
    var steps = (meal && meal.instructions) || [];
    var hay = steps.join(' \n ').toLowerCase();
    var out = [];
    var oven = cookOvenLine(steps);
    if (oven) out.push(oven);
    COOK_KIT_WORDS.forEach(function (pair) {
      if (!cookKitMentions(hay, pair[0])) return;
      if (out.indexOf(pair[1]) === -1) out.push(pair[1]);
    });
    return out;
  }

  // One ingredient, said the way both ticklists say it: the amount that
  // actually goes in the pan (get_cooker_view already swapped the
  // shopping-shaped qty for the cooking one — see recipes.cooking_
  // ingredients) and then the thing.
  function cookIngredientLabel(ing) {
    return ((ing && ing.qty ? ing.qty + ' ' : '') + ((ing && ing.item) || '')).trim();
  }

  // The words that would count as this ingredient being named in a step.
  // The head noun is the last word before any prep descriptor ("Baby
  // spinach, chopped" -> "spinach"), the same simplification quantities.py
  // already makes when it merges two grocery lines differing only by one.
  // One trailing 's' is the whole of the plural handling on purpose: a
  // stemmer would start matching things nobody wrote, and the cost of a
  // miss here is a step that lists one ingredient fewer.
  function cookIngredientNouns(item) {
    var base = String(item || '').split(',')[0].toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').trim();
    if (!base) return [];
    var words = base.split(/\s+/);
    var forms = [base];
    var head = words[words.length - 1];
    if (head && forms.indexOf(head) === -1) forms.push(head);
    forms.slice().forEach(function (f) {
      forms.push(/s$/.test(f) ? f.slice(0, -1) : f + 's');
    });
    return forms.filter(function (f) { return f.length >= 3; });
  }

  // What one step needs, read off the step's own words. A step that names
  // nothing on the ingredient list shows nothing — a quiet step beats a
  // confident wrong list of things to fetch.
  function cookStepNeeds(meal, stepPos) {
    var steps = (meal && meal.instructions) || [];
    var text = ' ' + String(steps[stepPos] || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ') + ' ';
    var out = [];
    ((meal && meal.ingredients) || []).forEach(function (ing) {
      var forms = cookIngredientNouns(ing.item);
      for (var i = 0; i < forms.length; i++) {
        if (text.indexOf(' ' + forms[i] + ' ') !== -1) {
          out.push(cookIngredientLabel(ing));
          return;
        }
      }
    });
    return out;
  }

  // One recipe panel. The focused cook screen renders it whole; Meals'
  // Meal step renders it `plain` (Emily, 2026-09-09 — "click the meal
  // anywhere... it should bring you to the screen with the recipe on it",
  // and inside Meals that screen is the Meal step). Plain is the SAME
  // renderer with the working controls taken off, not a second recipe
  // panel: the serving stepper, the hands-free mic, the step checkboxes,
  // "Fill in this recipe" and the end-of-cook row all write through
  // onCookClick/renderCook, which only ever redraw the Kitchen panel — and
  // two renderers for one recipe is how the two screens end up saying
  // different things about the same dish. Reading is what the Meal step is
  // for; cooking is one apricot tap away on the same screen.
  //
  // Plain also drops the element ids (cook-ings-N and friends): they are
  // handles for those same controls, and a second copy of one on another
  // panel would have getElementById reaching the wrong screen.
  function cookDetailHtml(m, idx, onSpruce, plain) {
    var cls = onSpruce ? ' on-spruce' : '';
    if (!m.has_full_recipe) {
      return '<p class="cook-norecipe' + cls + '">Freeform meal — no saved recipe detail. Ask in the ask bar for the full recipe.</p>';
    }
    var ingredients = (m.ingredients || []).map(function (i) {
      return '<li>' + escapeHtml(cookIngredientLabel(i)) + '</li>';
    }).join('') || '<li class="cook-dim">None listed</li>';

    return '<div class="cook-detail' + cls + '">' +
      (plain ? '' :
      '<div class="cook-detail-tools">' +
        (m.default_servings
          ? '<div class="cook-serves" data-idx="' + idx + '" data-recipe="' + escapeHtml(m.meal || '') + '" data-base="' + m.default_servings + '">' +
              '<span class="cook-serves-label">Serves</span>' +
              '<button type="button" class="cook-serves-btn" data-cook="serves" data-idx="' + idx + '" data-delta="-1" aria-label="Fewer servings">&minus;</button>' +
              '<span class="cook-serves-count" id="cook-serves-' + idx + '">' + cookServesShown(m) + '</span>' +
              '<button type="button" class="cook-serves-btn" data-cook="serves" data-idx="' + idx + '" data-delta="1" aria-label="More servings">+</button>' +
            '</div>'
          : '') +
        (COOK_VOICE_ENABLED
          ? '<button type="button" class="cook-mic" data-cook="voice" data-ctx="meal" data-idx="' + idx + '" ' +
              'aria-label="Hands-free for this recipe" ' +
              'title="Hands-free: read steps, ask amounts, log a substitution">' + COOK_ICONS.mic + '</button>'
          : '') +
      '</div>') +
      (m.advance_prep_notes
        ? '<h4 class="cook-detail-head">Advance prep</h4><p class="cook-detail-p">' + escapeHtml(m.advance_prep_notes) + '</p>'
        : '') +
      '<h4 class="cook-detail-head">Ingredients</h4>' +
      '<ul class="cook-ings"' + (plain ? '' : ' id="cook-ings-' + idx + '"') + '>' + ingredients + '</ul>' +
      (plain ? '' : cookUnscaledHtml(m, idx)) +
      '<h4 class="cook-detail-head">Instructions</h4>' +
      cookInstructionsHtml(m, idx, plain) +
      // The end of the last step used to just stop — the only way back to
      // "Mark cooked" was scrolling all the way back up to the hero. A
      // small, quiet row right where the steps run out closes the loop:
      // the same handler as the hero's own "Mark cooked" button (so this
      // is never a second source of truth for that write), plus a plain
      // way back. Only while there's really a recipe with steps to finish,
      // and only until it's actually marked cooked — once it's done, this
      // is just clutter under a screen that already says so.
      (!plain && (m.instructions || []).length && m.cooked_status !== 'done'
        ? cookFocusEndHtml(m)
        : '') +
      // The Meal step has its own "Why this night" card reading the same
      // sentence off the plan, so plain leaves this out rather than
      // printing the reason twice on one screen.
      (!plain && m.reasoning
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
        '<button type="button" class="cook-focus-end-back" data-cook="exit-focus">Back to ' +
          escapeHtml(cookBackLabel()) + '</button>' +
      '</div>' +
    '</div>';
  }

  // advance_prep_step_indices are 1-based positions within `instructions`
  // that are the make-ahead steps. When a recipe tags them, the steps split
  // into "Do ahead" and "Day of" with their own numbering, so it is clear
  // what to do the night before and what happens later using it. Most
  // recipes tag nothing and get one flat list.
  //
  // Each step is tap-to-check, through the tick store (cookReadTicks) —
  // keyed by the MEAL's own identity plus the step's position in the FULL
  // instructions array, not the Do ahead/Day of sub-list's own numbering,
  // so a check survives whichever list it's currently rendered into. It
  // used to be keyed by the meal's INDEX and thrown away with the page;
  // both were wrong for the same reason, which is that a cook puts the
  // phone down. See the comment on COOK_TICKS_PREFIX for where it lives
  // now and why there.
  //
  // `plain` is the reading copy (Meals' Meal step): the step keeps its
  // number and its words and loses the checkbox, because a tick there
  // would write into cookState and be redrawn by a render that only ever
  // touches the Kitchen panel.
  function cookStepLi(step, idx, stepPos, plain, mealKey) {
    if (plain) {
      return '<li class="cook-step-item">' +
        '<span class="cook-step-row"><span class="cook-step-text">' + escapeHtml(step) + '</span></span>' +
      '</li>';
    }
    var done = cookTicked('steps', mealKey + ':' + stepPos);
    // The number stays a real <ol> marker — a step someone might reference
    // ("step 3") should look like one — so the checkbox+text flex row lives
    // INSIDE the <li> rather than on it; display:flex directly on an <li>
    // silently drops its own marker in every browser that matters here.
    return '<li class="cook-step-item' + (done ? ' is-done' : '') + '">' +
      '<span class="cook-step-row">' +
        '<button type="button" class="cook-box cook-step-check' + (done ? ' checked' : '') + '" ' +
          'data-cook="check-step" data-meal-key="' + escapeHtml(mealKey) + '" data-step="' + stepPos + '" ' +
          'aria-label="' + (done ? 'Mark step not done' : 'Mark step done') + '">' + COOK_ICONS.check + '</button>' +
        '<span class="cook-step-text">' + escapeHtml(step) + '</span>' +
      '</span>' +
    '</li>';
  }

  function cookInstructionsHtml(m, idx, plain) {
    var steps = m.instructions || [];
    var listCls = plain ? 'cook-steps' : 'cook-steps cook-steps-check';
    // Only the checkable copy needs one; `plain` renders no checkbox, so
    // it stays free of the tick store entirely — the read-only frame on
    // Meals must not so much as look at what has been ticked here.
    var mealKey = plain ? '' : cookMealKey(m);
    if (!steps.length) {
      // "Fill in this recipe" is a write, and it lands through
      // onCookClick — so the reading copy says the fact and leaves the
      // button to the screen that can actually run it.
      return '<p class="cook-dim">No steps saved yet.</p>' +
        (plain ? ''
          : '<button type="button" class="cook-fill" data-cook="fill" data-recipe="' + escapeHtml(m.meal || '') + '">Fill in this recipe</button>');
    }
    var prepIdx = m.advance_prep_step_indices || [];
    if (!prepIdx.length) {
      return '<ol class="' + listCls + '">' +
        steps.map(function (s, i) { return cookStepLi(s, idx, i, plain, mealKey); }).join('') +
      '</ol>';
    }
    var doAhead = [], dayOf = [];
    steps.forEach(function (s, i) { (prepIdx.indexOf(i + 1) !== -1 ? doAhead : dayOf).push({ s: s, i: i }); });
    return '<h5 class="cook-steplabel cook-steplabel-warm">Do ahead</h5>' +
      '<ol class="' + listCls + '">' + doAhead.map(function (x) { return cookStepLi(x.s, idx, x.i, plain, mealKey); }).join('') + '</ol>' +
      '<h5 class="cook-steplabel">Day of</h5>' +
      '<ol class="' + listCls + '">' + dayOf.map(function (x) { return cookStepLi(x.s, idx, x.i, plain, mealKey); }).join('') + '</ol>';
  }

  // ---------- Cook mode: one meal, the whole screen ----------
  // Tapping a cook — on the Kitchen root, on Today, or on a Meals day —
  // takes the whole tab over for that one meal: the ticket's whole point,
  // "no stack of twenty other cards underneath, no scroll position to
  // lose". Leaving is the quiet "‹ Kitchen" text control on the focused
  // hero, the same shape Grocery's shopping mode uses to step back out of
  // a store into Plan your stops (groShopHeroHtml/.gro-hero-back) — a step
  // of this tab, never a page with its own header or back button, and a
  // link that goes UP a level by name rather than calling history.back().
  function cookEnterFocus(idx) {
    if (!cookState.data || !(cookState.data.meals || [])[idx]) return;
    cookState.screen = 'focus';
    cookState.focusIdx = idx;
    cookState.focusScrollTo = null;
    cookState.pendingScrollTop = true;
    // Every way in opens on Before you start — "Cook this opens Before you
    // start" is the acceptance criterion, and it holds for Today's move,
    // Meals' Cook this, the Kitchen row and a dish name in chat alike,
    // because they all land here. Ticks are NOT reset with it: they are the
    // one thing on this screen that outlives the visit (cookReadTicks), so
    // reopening a half-cooked dish resumes it.
    cookState.focusStage = 'prep';
    cookState.stepIdx = 0;
    cookState.methodFrom = 'prep';
    // WHICH dish this focus is on, by identity rather than by its place in
    // the list — see the guard in renderCook.
    cookState.focusMealKey = cookMealKey(cookState.data.meals[idx]);
    renderCook();
  }

  function cookExitFocus() {
    // A mic still listening on a screen you can no longer see is the worst
    // version of this feature — leaving cook mode stops the session with
    // it, exactly as switching off the old Cook state used to.
    stopCookVoice();
    var origin = cookState.focusOrigin;
    cookState.focusOrigin = null;
    // Kitchen goes back to its own root FIRST, whether or not we then
    // leave the tab: a tab left sitting on a cook screen would reopen
    // there the next time it is tapped, which is not where the person
    // left the app.
    cookState.screen = 'overview';
    cookState.pendingScrollTop = true;
    renderCook();
    if (!origin || !origin.tab) return;
    // The ask sheet is part of the shell, not of any one tab, so a dish
    // tapped in a reply comes back to the tab it was tapped ON with the
    // conversation open again — the reply that named the dish is the thing
    // "‹ the chat" promises to return to.
    // Neither of these adds a history entry — going back UP a level moves
    // the entry we are standing on, it does not stack a new one on top of
    // it. (See activateTab's replaceHistory: this used to push twice, so
    // one press of the back link grew history by two and the next back
    // gesture skipped a step.)
    if (origin.tab !== 'kitchen') activateTab(origin.tab, false, { replaceHistory: true });
    if (origin.reopenAsk) { openAskSheet(); return; }
    if (origin.tab === 'kitchen') return;
    // ...and back to the exact step, not just the tab: Meals is Week ->
    // Day -> Meal, and "‹ Monday" has to mean Monday.
    if (origin.tab === 'week' && origin.mealsDay !== undefined && origin.mealsDay !== null) {
      goMealsStep(origin.mealsStep || 'meal', {
        dayIndex: origin.mealsDay, slot: origin.mealsSlot, replace: true
      });
    }
  }

  // A prep session, by its date — see cookState.sessionDate for why the
  // date is the handle and not an index.
  function cookEnterSession(dateStr) {
    if (!cookSessionOn(cookState.data, dateStr)) return;
    // A session is only ever reached from the Kitchen root, so its own
    // "‹ Kitchen" is right — and any recipe origin left over from an
    // earlier deep link would send exit-session to the wrong tab.
    cookState.focusOrigin = null;
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
      // prep_cut rows belong to their prep session (cookPrepSessionsHtml),
      // and a prep_cut carries the entry it feeds — so without this it
      // would be listed here as well as there. The overview's prep rail
      // carried the identical exclusion for the identical reason; it moved
      // here when that rail came out.
      return t.task_type !== 'prep_cut';
    }).filter(function (t) {
      if (t.meal_plan_entry_id != null) return entryIds.indexOf(t.meal_plan_entry_id) !== -1;
      return !!mealName && (t.related_meal || '').trim().toLowerCase() === mealName;
    });
  }

  // One section, scoped to this meal's own rows, defrost tiles included.
  // It inherited two things from the overview's shared prep rail when that
  // rail came out: the done-count note (which stops counting and says
  // what's next once every row is off the list) and the hands-free mic,
  // whose 'prep' context checks those rows off by voice
  // (handleCookPrepVoice). Both belong here now — this is the screen
  // someone is standing at the counter with.
  function cookFocusPrepHtml(tasks) {
    if (!tasks.length) return '';
    var done = tasks.filter(function (t) { return t.status === 'done'; }).length;
    var allDone = done === tasks.length;
    return '<section class="cook-section">' +
      '<div class="cook-sectionhead">' +
        '<span class="cook-eyebrow cook-eyebrow-warm">For this meal</span>' +
        '<span class="cook-rule"></span>' +
        '<span class="cook-sectionnote">' +
          (allDone ? 'Prep’s done — the rest is tonight.' : (done + ' of ' + tasks.length + ' done')) +
        '</span>' +
        (COOK_VOICE_ENABLED
          ? '<button type="button" class="cook-mic" data-cook="voice" data-ctx="prep" ' +
              'aria-label="Hands-free: check off prep steps by voice" ' +
              'title="Hands-free: check off prep steps by voice">' + COOK_ICONS.mic + '</button>'
          : '') +
      // This '</div>' was missing (2026-09-10). .cook-sectionhead is a flex
      // ROW, so an unclosed one swallowed the prep grid as one of its own
      // items and squeezed every card into a third of the width. It was
      // invisible while the prep section was the only thing above the
      // recipe card; with a ticklist under it, it is the first thing you
      // see. Every other section head in this file closes its own div.
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
        '<button type="button" class="crumb on-spruce" data-cook="exit-focus">&lsaquo; ' +
          escapeHtml(cookBackLabel()) + '</button>' +
        cookReheatCardHtml(meal, dayLabel) +
      '</div>' +
      (srcLine
        ? '<div class="cook-body"><div class="card cook-focus-recipe">' +
            '<p class="cook-detail-p">' + escapeHtml(srcLine) + '</p>' +
          '</div></div>'
        : '') +
    '</div>';
  }

  // ---------- Cook mode's three stages ----------
  // The ticket ("Cooking: before you start, one step at a time, and a
  // proper finish", Emily 2026-09-09) replaces one long cook screen with
  // the three things a person actually does, in order. What follows is the
  // presentation of data this screen already had — get_cooker_view already
  // scales the ingredients, already merges a batch, already tags the
  // make-ahead steps; nothing here re-derives any of it.

  function cookFocusMeal() {
    var meals = (cookState.data && cookState.data.meals) || [];
    return meals[cookState.focusIdx] || null;
  }

  // Where "Start cooking" lands. The first step nobody has ticked, so
  // coming back to a half-cooked dish resumes rather than restarts — and
  // the very first cook, with nothing ticked, gets step one for free.
  function cookFirstUndoneStep(meal) {
    var steps = (meal && meal.instructions) || [];
    var key = cookMealKey(meal);
    for (var i = 0; i < steps.length; i++) {
      if (!cookTicked('steps', key + ':' + i)) return i;
    }
    return steps.length ? steps.length - 1 : 0;
  }

  function cookGoStage(stage) {
    if (stage === 'method' && cookState.focusStage !== 'method') {
      cookState.methodFrom = cookState.focusStage;
      // Opening the whole method from step 7 and landing at step 1 is not
      // "one tap away", it is losing your place in the other direction.
      if (cookState.focusStage === 'step') cookState.focusScrollTo = 'step:' + cookState.stepIdx;
    }
    cookState.focusStage = stage;
    cookState.pendingScrollTop = true;
    renderCook();
  }

  function cookStartCooking() {
    var meal = cookFocusMeal();
    cookState.stepIdx = meal ? cookFirstUndoneStep(meal) : 0;
    cookGoStage('step');
  }

  // Moving on is what marks a step done. The whole point of one step at a
  // time is that the hands are busy, and a tick plus a Next is one tap
  // more than that person has to spare; the whole method's own checkboxes
  // are where a mis-tap gets undone.
  function cookStepForward() {
    var meal = cookFocusMeal();
    if (!meal) return;
    var steps = meal.instructions || [];
    cookSetTick('steps', cookMealKey(meal) + ':' + cookState.stepIdx, true);
    if (cookState.stepIdx < steps.length - 1) cookState.stepIdx += 1;
    cookState.pendingScrollTop = true;
    renderCook();
  }

  // Back from step one is back to the counter you laid out, not a dead
  // control. Stepping back deliberately leaves the ticks alone: you are
  // re-reading a step, not undoing it.
  function cookStepBack() {
    if (cookState.stepIdx <= 0) return cookGoStage('prep');
    cookState.stepIdx -= 1;
    cookState.pendingScrollTop = true;
    renderCook();
  }

  // The hero, shared by all three stages so the dish, the way back and the
  // stage you are on never move between them. On the two cooking stages it
  // goes slim: once you have started, "what am I making" is settled and the
  // screen's biggest type belongs to the instruction instead.
  function cookFocusHeroHtml(data, meal, idx, stage) {
    var isDone = meal.cooked_status === 'done';
    var onPrep = stage === 'prep';
    var steps = meal.instructions || [];
    var dayLabel = meal.component_category || (meal.date ? cookDateLabel(meal.date) : 'Cooking');

    var chipLabel = onPrep
      ? 'Before you start'
      : (stage === 'method'
          ? 'The whole method'
          : 'Step ' + (cookState.stepIdx + 1) + ' of ' + steps.length);

    var chips = [];
    if (onPrep) {
      chips.push(dayLabel);
      if (meal.prep_time_minutes || meal.cook_time_minutes) {
        var bits = [];
        if (meal.prep_time_minutes) bits.push(meal.prep_time_minutes + 'm prep');
        if (meal.cook_time_minutes) bits.push(meal.cook_time_minutes + 'm cook');
        chips.push(bits.join(' + '));
      }
      if (meal.batch_note) chips.push('Bulk ×' + meal.meal_count);
      // The "for 6" a batch night is really cooking for — this is the
      // screen where the ingredients are read off, so it is the one place
      // the number has to be right in front of them.
      if (meal.covers_note && meal.servings) chips.push('for ' + meal.servings);
      // (meal.servings is kept in step with the stepper by
      // cookApplyServesOverride — a chip saying "for 6" over a stepper
      // saying 7 was the same dish contradicting itself on one screen.)
      // The side that fills out this plate, named before its "Alongside"
      // steps are reached at the bottom of the list.
      if (meal.sides_label) chips.push(meal.sides_label);
      var attChip = cookAttendanceChip(meal);
      if (attChip) chips.push(attChip);
    }

    // Newsreader italic, at most once per screen (DESIGN_SYSTEM §3) and
    // only on Before you start: it carries a real fact about the cook —
    // the batch first, because it explains the quantities under it.
    var note = onPrep ? (cookBatchNote(meal) || meal.advance_prep_notes || meal.reasoning || '') : '';

    return '<div class="cook-hero' + (onPrep ? '' : ' cook-hero-slim') + '">' +
      '<button type="button" class="crumb on-spruce" data-cook="exit-focus">&lsaquo; ' +
        escapeHtml(cookBackLabel()) + '</button>' +
      '<div class="cook-hero-top">' +
        '<span class="cook-hero-chip">' + escapeHtml(chipLabel) + '</span>' +
        '<span class="cook-hero-rule"></span>' +
        (onPrep && meal.advance_prep_notes ? '<span class="cook-hero-tag">Advance prep</span>' : '') +
      '</div>' +
      '<div class="cook-hero-line">' +
        '<h2 class="cook-hero-headline' + (isDone ? ' is-done' : '') + '">' +
          escapeHtml(meal.meal || 'Dinner') + '</h2>' +
        (note ? '<p class="cook-hero-note">' + escapeHtml(note) + '</p>' : '') +
      '</div>' +
      (chips.length
        ? '<div class="cook-hero-chips">' + chips.map(function (c) {
            return '<span class="cook-meta-chip">' + escapeHtml(c) + '</span>';
          }).join('') + '</div>'
        : '') +
      // Both pickers are setup decisions about days other than this one,
      // so they belong on Before you start and nowhere else — mid-step
      // they are two questions in the way of an instruction.
      (onPrep ? cookAheadHtml(meal) : '') +
      (onPrep ? cookPrepCutHtml(data, meal) : '') +
    '</div>';
  }

  // ---------- Stage 1: Before you start ----------
  // "Everything out of the cupboard as a ticklist with quantities" — the
  // step nobody designs and everybody needs.

  // What a tick is filed under. The item's own name, not its position:
  // the serving stepper rewrites every quantity in place, and a plates
  // pass can add a side's ingredients to the list, so a position would
  // move the ticks onto the wrong rows.
  function cookIngTickId(ing, i) {
    var name = String((ing && ing.item) || '').trim().toLowerCase();
    return name || ('#' + i);
  }

  // The WHOLE row is the control, not just the box beside it — this is the
  // one screen in the app read at arm's length with wet hands, and the
  // 44px the .cook-box::after inset already guarantees is the floor, not
  // the target. The box keeps its class (so a ticked ingredient reads
  // exactly like a ticked prep task or a ticked step) and becomes a span,
  // since a button inside a button is not a thing.
  function cookGetOutRowHtml(ing, i, mealKey) {
    var id = cookIngTickId(ing, i);
    var done = cookTicked('ings', mealKey + ':' + id);
    return '<li class="cook-getout-item' + (done ? ' is-done' : '') + '">' +
      '<button type="button" class="cook-getout-row" ' +
        'data-cook="check-ing" data-meal-key="' + escapeHtml(mealKey) + '" ' +
        'data-ing="' + escapeHtml(id) + '" aria-pressed="' + (done ? 'true' : 'false') + '">' +
        '<span class="cook-box' + (done ? ' checked' : '') + '">' + COOK_ICONS.check + '</span>' +
        '<span class="cook-getout-text">' + escapeHtml(cookIngredientLabel(ing)) + '</span>' +
      '</button>' +
    '</li>';
  }

  function cookGetOutHtml(meal, idx) {
    var ings = meal.ingredients || [];
    if (!ings.length) {
      // A freeform meal with no saved recipe has nothing to fetch, and
      // saying "0 of 0 out" about it would be pretending otherwise.
      return '';
    }
    var mealKey = cookMealKey(meal);
    var done = ings.filter(function (ing, i) {
      return cookTicked('ings', mealKey + ':' + cookIngTickId(ing, i));
    }).length;
    return '<section class="cook-section">' +
      '<div class="cook-sectionhead">' +
        '<span class="cook-eyebrow">Everything out</span>' +
        '<span class="cook-rule"></span>' +
        '<span class="cook-sectionnote">' +
          (done === ings.length ? 'All out.' : (done + ' of ' + ings.length + ' out')) +
        '</span>' +
      '</div>' +
      // The serving stepper lives here rather than on the recipe now: the
      // amounts are worth getting right BEFORE the cupboard is open, and
      // this is the one screen that is about the amounts. It rewrites this
      // list in place, exactly as it always rewrote the recipe's.
      (meal.default_servings
        ? '<div class="cook-serves" data-idx="' + idx + '" data-meal-key="' + escapeHtml(mealKey) + '" ' +
            'data-recipe="' + escapeHtml(meal.meal || '') + '" data-base="' + meal.default_servings + '">' +
            '<span class="cook-serves-label">Serves</span>' +
            '<button type="button" class="cook-serves-btn" data-cook="serves" data-idx="' + idx + '" data-delta="-1" aria-label="Fewer servings">&minus;</button>' +
            '<span class="cook-serves-count" id="cook-serves-' + idx + '">' + cookServesShown(meal) + '</span>' +
            '<button type="button" class="cook-serves-btn" data-cook="serves" data-idx="' + idx + '" data-delta="1" aria-label="More servings">+</button>' +
          '</div>'
        : '') +
      '<ul class="cook-getout" id="cook-getout-' + idx + '">' +
        ings.map(function (ing, i) { return cookGetOutRowHtml(ing, i, mealKey); }).join('') +
      '</ul>' +
      cookUnscaledHtml(meal, idx) +
    '</section>';
  }

  // "Plus the pans you'll want." Read out of the steps (cookKitFor); an
  // empty answer is a missing section, never an empty one.
  function cookKitHtml(meal) {
    var kit = cookKitFor(meal);
    if (!kit.length) return '';
    return '<section class="cook-section">' +
      '<div class="cook-sectionhead">' +
        '<span class="cook-eyebrow">Pans and kit</span>' +
        '<span class="cook-rule"></span>' +
      '</div>' +
      '<div class="cook-kit">' +
        kit.map(function (k) { return '<span class="cook-kit-chip">' + escapeHtml(k) + '</span>'; }).join('') +
      '</div>' +
    '</section>';
  }

  function cookPrepStageHtml(data, meal, idx) {
    var prepTasks = cookFocusPrepTasks(data, meal);
    var body = cookFocusPrepHtml(prepTasks) + cookGetOutHtml(meal, idx) + cookKitHtml(meal);
    if (!body) {
      // Two different absences, and they have different ways out. A SAVED
      // recipe with nothing in it gets "Fill in this recipe" on the whole
      // method (cookInstructionsHtml). A freeform meal gets no such
      // button — cookDetailHtml returns early on !has_full_recipe — so
      // pointing one at the whole method was promising a control that
      // isn't there. Say the true thing in each case, and name the way out
      // the screen really has.
      body = meal.has_full_recipe
        ? '<p class="cook-dim">Nothing to get out for this one yet. The whole method has a way to fill the recipe in.</p>'
        : '<p class="cook-dim">Nothing written down for this one. Ask me for the recipe and I’ll put one together.</p>';
    }
    return '<div class="cook-body">' + body + '</div>';
  }

  // ---------- Stage 2: one step at a time ----------
  // Big type, one instruction, and what that step needs. The default once
  // you start (Emily's choice): "best when your hands are busy and the
  // phone is across the counter."
  function cookStepStageHtml(meal, idx) {
    var steps = meal.instructions || [];
    // Already clamped by cookFocusHtml, the one place that decides which
    // stage is showing.
    var pos = cookState.stepIdx;
    var needs = cookStepNeeds(meal, pos);
    // The make-ahead steps carry their own label in the whole method
    // (advance_prep_step_indices, 1-based); a step that is one of them says
    // so here too, or the same step reads as two different jobs on the two
    // screens.
    var isAhead = (meal.advance_prep_step_indices || []).indexOf(pos + 1) !== -1;
    return '<div class="cook-body cook-step-stage">' +
      (isAhead ? '<h5 class="cook-steplabel cook-steplabel-warm">Do ahead</h5>' : '') +
      '<p class="cook-bigstep">' + escapeHtml(steps[pos] || '') + '</p>' +
      (needs.length
        ? '<div class="cook-needs">' +
            '<span class="cook-eyebrow">For this step</span>' +
            '<div class="cook-kit">' +
              needs.map(function (n) { return '<span class="cook-kit-chip">' + escapeHtml(n) + '</span>'; }).join('') +
            '</div>' +
          '</div>'
        : '') +
    '</div>';
  }

  // ---------- Stage 3: the whole method ----------
  // Deliberately the recipe panel this screen has always rendered, whole
  // and unchanged (cookDetailHtml) — every step tickable, the "why this",
  // the fill-in — so the two cooking stages can never drift about what the
  // recipe says. Same renderer Meals' Meal step reads `plain`.
  function cookMethodStageHtml(meal, idx) {
    return '<div class="cook-body">' +
      '<div class="card cook-focus-recipe">' + cookDetailHtml(meal, idx, false) + '</div>' +
    '</div>';
  }

  // ---------- The dock ----------
  // Each cooking stage's one apricot, plus the quiet ways sideways. Rule 5
  // is untouched by it: Kitchen's ROOT still has no primary action at all,
  // and this is a step of the tab one level down, exactly as cook mode's
  // "Mark it cooked" already was. Sticky rather than in flow, because the
  // phone is across the counter and the next thing to do must not be a
  // scroll away.
  function cookDockHtml(primaryHtml, links) {
    var quiet = (links || []).filter(Boolean);
    return '<div class="dock cook-dock">' +
      (quiet.length ? '<div class="cook-dock-links">' + quiet.join('') + '</div>' : '') +
      primaryHtml +
    '</div>';
  }

  function cookDockLink(label, attrs) {
    return '<button type="button" class="cook-dock-link" ' + attrs + '>' + escapeHtml(label) + '</button>';
  }

  // "Mark it cooked" — the same words, the same write and the same handler
  // as the end-of-recipe button, wherever a cook meets it. Finishing has
  // worked this way since cook mode shipped and this slice deliberately
  // does not touch it.
  function cookDockCookedHtml(meal) {
    var isDone = meal.cooked_status === 'done';
    return '<button type="button" class="cook-hero-action cook-focus-check' + (isDone ? ' is-done' : '') + '" ' +
      'data-cook="focus-check" data-entry-id="' + meal.entry_id + '" data-next="' + (isDone ? 'pending' : 'done') + '">' +
      '<span>' + (isDone ? 'Mark not cooked' : 'Mark it cooked') + '</span>' + (isDone ? '' : ICONS.arrow) +
    '</button>';
  }

  function cookFocusDockHtml(meal) {
    var steps = meal.instructions || [];
    var stage = cookState.focusStage;
    var methodLink = cookDockLink('The whole method', 'data-cook="stage" data-stage="method"');

    if (stage === 'prep') {
      // Nothing to step through is not a reason to offer a step-through.
      // The whole method is where "Fill in this recipe" lives, so that is
      // where a recipe with no steps is sent.
      var primary = steps.length
        ? '<button type="button" class="cook-hero-action" data-cook="start-cooking">' +
            '<span>Start cooking</span>' + ICONS.arrow + '</button>'
        : cookDockCookedHtml(meal);
      return cookDockHtml(primary, [methodLink]);
    }

    // The whole method deliberately carries NO apricot of its own. It is
    // the skim-ahead screen, and it already ends where it always has, on
    // cookFocusEndHtml's "Mark it cooked" under the last step — a sticky
    // copy of that same button would be the same action twice on one
    // screen. So its dock is the one thing the screen cannot say for
    // itself: the way back to the place you left.
    if (stage === 'method') {
      var fromStep = cookState.methodFrom === 'step';
      return cookDockHtml('', [cookDockLink(
        fromStep ? '‹ Back to step ' + (cookState.stepIdx + 1) : '‹ Before you start',
        'data-cook="stage" data-stage="' + (fromStep ? 'step' : 'prep') + '"'
      )]);
    }

    var last = cookState.stepIdx >= steps.length - 1;
    // The last step's primary is the finish, not a Next into nothing.
    var stepPrimary = last
      ? cookDockCookedHtml(meal)
      : '<button type="button" class="cook-hero-action" data-cook="step-next">' +
          '<span>Next step</span>' + ICONS.arrow + '</button>';
    return cookDockHtml(stepPrimary, [
      cookDockLink(cookState.stepIdx <= 0 ? '‹ Before you start' : '‹ Back', 'data-cook="step-prev"'),
      methodLink
    ]);
  }

  function cookFocusHtml(data, meals, idx) {
    var meal = meals[idx];
    if (meal.is_leftovers) return cookReheatFocusHtml(meal);
    var steps = meal.instructions || [];
    // A stage that has nothing behind it any more — the recipe was filled
    // in and then emptied, or a deep link landed mid-journey — falls back
    // to Before you start rather than rendering a step that isn't there.
    var stage = cookState.focusStage;
    if (stage === 'step' && !steps.length) stage = cookState.focusStage = 'prep';
    if (stage !== 'prep' && stage !== 'step' && stage !== 'method') stage = cookState.focusStage = 'prep';
    // Clamped HERE rather than inside the step renderer, so the dock's
    // "is this the last one" test and the instruction on screen can never
    // be answering about two different steps — which is what happened when
    // a shorter recipe arrived underneath a cursor pointing past its end.
    cookState.stepIdx = steps.length
      ? Math.min(Math.max(cookState.stepIdx, 0), steps.length - 1)
      : 0;

    var body = stage === 'prep'
      ? cookPrepStageHtml(data, meal, idx)
      : (stage === 'method' ? cookMethodStageHtml(meal, idx) : cookStepStageHtml(meal, idx));

    return '<div class="cook-focus">' +
      cookFocusHeroHtml(data, meal, idx, stage) +
      body +
      cookFocusDockHtml(meal) +
    '</div>';
  }

  // Gives the body a foot the size of the dock. Measured rather than
  // guessed, because the dock's height changes with the stage (one quiet
  // link, two, or none) and with wrapping at small widths.
  //
  // Honest about what this is: a sticky bottom:0 last child already comes
  // to rest at the end of the scroll, so the ticklist was never
  // unreachable without it — occluded-at-max-scroll measured 0 both with
  // and without. What it buys is comfort at 390px, where the scrollport is
  // 459 with the coaching row up: the last rows clear the bar earlier on
  // the way down, and a row ends with a gap instead of flush against its
  // edge. The bug that was real is the desktop one — see .cook-body's
  // padding in the 1100px block, where a shorthand had been zeroing this.
  function wireCookDock(view) {
    var focus = view.querySelector('.cook-focus');
    var dock = focus && focus.querySelector('.cook-dock');
    if (!focus || !dock) return;
    focus.style.setProperty('--cook-dock-h', dock.offsetHeight + 'px');
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
      return;
    }
    // ...and the same job for the other direction: opening the whole
    // method from step seven lands on step seven, not back at the top of
    // the recipe. The step's own <li> carries its position, since the Do
    // ahead / Day of split means the seventh <li> is not always step seven.
    if (typeof cookState.focusScrollTo === 'string' && cookState.focusScrollTo.indexOf('step:') === 0) {
      var pos = cookState.focusScrollTo.slice(5);
      cookState.focusScrollTo = null;
      var stepEl = view.querySelector('.cook-step-check[data-step="' + pos + '"]');
      var li = stepEl && stepEl.closest ? stepEl.closest('.cook-step-item') : null;
      if (li && li.scrollIntoView) li.scrollIntoView({ behavior: 'auto', block: 'center' });
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
      // Opened from inside Kitchen, so Kitchen is genuinely where back
      // goes — drop whatever origin an earlier deep link left behind.
      cookState.focusOrigin = null;
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
    if (what === 'goto-plan') return activateTab('week', true);
    if (what === 'rest-more') {
      kitchenState.restExpanded = true;
      renderKitchen();
      return;
    }
    if (what === 'focus-check') return cookFocusCheckMeal(el);
    if (what === 'check-step') {
      cookToggleTick('steps', el.getAttribute('data-meal-key') + ':' + el.getAttribute('data-step'));
      renderCook();
      return;
    }
    if (what === 'check-ing') {
      cookToggleTick('ings', el.getAttribute('data-meal-key') + ':' + el.getAttribute('data-ing'));
      renderCook();
      return;
    }
    // Moving between the three stages of one cook. Never history, never a
    // tab switch: they are stages of one step (see cookState.focusStage).
    if (what === 'stage') return cookGoStage(el.getAttribute('data-stage'));
    if (what === 'start-cooking') return cookStartCooking();
    if (what === 'step-next') return cookStepForward();
    if (what === 'step-prev') return cookStepBack();
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
  // Always a real cook, never a reheat (a reheat night is a line on the
  // Kitchen root, not a way into this screen — see kitchenTodayRowHtml),
  // so no aria-label check is needed here the way cookCheckMeal needs one.
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
    refreshKitchenMoves();
  }

  // The Kitchen root's start-by lines and its done state are read off
  // /api/today/moves, and no /api/cooker/* write hands that payload back —
  // so renderCookFrom refreshed cookState.data and left kitchenState.moves
  // exactly as it was. One small re-read closes the gap; a failure is
  // silent, because the row's own facts are already correct and a toast
  // about a background read is noise.
  async function refreshKitchenMoves() {
    if (!kitchenIsBuilt()) return;
    try {
      var res = await fetch('/api/today/moves');
      if (!res.ok) return;
      kitchenState.moves = (await res.json()).moves || [];
    } catch (err) {
      return;
    }
    renderCook();
  }

  // Live re-scale without a plan reload. Non-numeric quantities ("a pinch",
  // "to taste") cannot scale mathematically, so the backend leaves those
  // alone and names them in unscaled_items rather than guessing.
  // ---------- The cook's own serving count ----------
  // Three separate things went wrong here and they are one mechanism.
  //
  // It used to write only to the DOM (#cook-ings-N, #cook-getout-N) and
  // leave cookState alone — survivable while the stepper sat on one screen
  // nothing re-rendered, fatal once every stage change calls renderCook()
  // and rebuilds from that state.
  //
  // Then it read the current count off `meal.default_servings`, which only
  // moves when a response lands, so three fast taps all counted from the
  // same number and produced one increment out of three requests, and a
  // fast +/- left the winner to whichever reply arrived last. The count is
  // now advanced on the meal AT TAP TIME (`serves_target`), and each
  // request carries a sequence token so a reply that has been superseded
  // is dropped instead of racing.
  //
  // And an ordinary tab switch threw the whole thing away: loadKitchen
  // refetches, the household's own servings come back, and a cook holding
  // the pan is silently returned to 3. The choice is kept for the page's
  // life in cookState.serves, keyed by the DISH rather than by its place
  // in the list, and re-applied by cookApplyServesOverride on every render
  // — so a load, a write response and a tab switch all leave it standing.
  // It is not sent to the server and does not outlive the page: this is a
  // cook overriding tonight at the counter, not a change to who is eating.
  function cookServesShown(m) {
    if (!m) return null;
    return m.serves_target != null ? m.serves_target : m.default_servings;
  }

  // Re-apply the cook's choice over whatever the server just handed back.
  // Runs on every render, which is what makes it survive a refetch — and,
  // since the choice is stored beside the ticks, a reload too. cookReadTicks
  // is what hydrates it, so it is called here rather than assumed: this
  // runs before anything is drawn, which on a fresh page is before any tick
  // has been read.
  //
  // The stored amounts are ABSOLUTE — the scaled list /api/recipes/scale
  // handed back for the count the cook chose — so re-applying them replaces
  // whatever the server sent rather than multiplying it. That is what keeps
  // it from composing twice with the batch and attendance scaling
  // get_cooker_view does: those shaped the list this one replaces, and the
  // one thing that does follow the cook is the batch card's "for N" chip
  // (was_batch), which IS the number being cooked.
  function cookApplyServesOverride(meals) {
    cookReadTicks();
    (meals || []).forEach(function (m) {
      var o = cookState.serves[cookMealKey(m)];
      if (!o) return;
      m.ingredients = o.ingredients;
      m.default_servings = o.servings;
      m.unscaled_items = o.unscaled_items;
      m.serves_overridden = true;
      // A batch card's "for 6" chip is the number being cooked, so it
      // follows the cook. Its covers_note does NOT — see cookBatchNote.
      if (o.was_batch) m.servings = o.servings;
    });
  }

  async function cookStepServings(el) {
    var idx = parseInt(el.getAttribute('data-idx'), 10);
    var meal = (cookState.data && (cookState.data.meals || [])[idx]) || null;
    var wrap = el.closest('.cook-serves');
    if (!meal || !wrap) return;
    var delta = parseInt(el.getAttribute('data-delta'), 10);
    var current = parseInt(cookServesShown(meal), 10) ||
      parseInt(wrap.getAttribute('data-base'), 10) || 1;
    var next = Math.max(1, current + delta);
    if (next === current) return;

    // On the meal immediately, so the next tap in the same second counts
    // from here and not from the number the last reply happened to leave.
    meal.serves_target = next;
    var token = (cookState.servesSeq = (cookState.servesSeq || 0) + 1);
    meal.serves_token = token;
    // The count moves on the tap; the amounts follow when the scale comes
    // back (the refresh policy's "the common case never waits"). Not a
    // full render, so the list doesn't redraw with amounts that are one
    // request behind the number above them.
    var countEl = document.getElementById('cook-serves-' + idx);
    if (countEl) countEl.textContent = next;
    try {
      var res = await fetch('/api/recipes/scale?name=' +
        encodeURIComponent(wrap.getAttribute('data-recipe')) + '&servings=' + next);
      if (!res.ok) throw new Error('scale failed');
      var data = await res.json();
      // A reply for a count the cook has already tapped past: drop it. Two
      // requests are in flight after a fast +/- and the wrong survivor is
      // how the screen ends up on a number nobody chose.
      if (meal.serves_token !== token) return;
      // Hydrate BEFORE writing, never after: cookReadTicks reads the whole
      // record back when it is asked for a plan it hasn't seen, so a read
      // on the far side of this assignment would put the stored number
      // back over the tap that just happened.
      cookReadTicks();
      cookState.serves[cookMealKey(meal)] = {
        servings: next,
        ingredients: data.scaled_ingredients || [],
        unscaled_items: data.unscaled_items || [],
        // Remembered so the override knows whether this card's "for N"
        // chip is a batch size that should follow it.
        was_batch: !!meal.covers_note,
        planned_for: cookState.serves[cookMealKey(meal)]
          ? cookState.serves[cookMealKey(meal)].planned_for
          : (parseInt(meal.servings, 10) || parseInt(meal.default_servings, 10) || null)
      };
      meal.serves_target = null;
      // Stored with the ticks, in the same record and with the same
      // lifetime: the two describe one cooking session, and a reload that
      // brought back half-ticked amounts nobody chose is what this fixes.
      cookWriteTicks();
      cookApplyServesOverride([meal]);
      renderCook();
    } catch (err) {
      if (meal.serves_token !== token) return;
      // Put the number back rather than leaving the screen claiming a
      // count the amounts underneath it don't match.
      meal.serves_target = null;
      if (countEl) countEl.textContent = meal.default_servings;
      showToast('Couldn’t rescale that just now — try again.');
    }
  }

  // What a batch card says under its headline once the cook has set their
  // own amount. covers_note is the server's sentence and it NAMES A
  // SERVINGS COUNT ("Cooking for 6 — enough for Thursday and Friday"), so
  // the moment the cook rescales it is stating a number that is no longer
  // true, 100px under a stepper saying otherwise. The NIGHTS are still the
  // plan, so they are said again from meal.covers — the dates themselves,
  // not a re-worded sentence — and the caution is added only when the cook
  // has gone BELOW what the batch was sized for, which is the only
  // direction that can leave one of those nights short.
  function cookBatchNote(meal) {
    if (!meal.serves_overridden) return meal.covers_note || '';
    var days = ((meal.covers || []).map(function (c) {
      return c.date ? dayName(c.date, { weekday: 'long' }) : '';
    })).filter(Boolean);
    if (!days.length) return '';
    var list = days.length === 1
      ? days[0]
      : days.slice(0, -1).join(', ') + ' and ' + days[days.length - 1];
    var o = cookState.serves[cookMealKey(meal)] || {};
    var short = o.planned_for && cookServesShown(meal) < o.planned_for;
    return short
      ? 'This batch is also meant for ' + list + ' — check it still stretches.'
      : 'This batch is also meant for ' + list + '.';
  }

  // "Eyeball these — they don't scale automatically." Rendered from the
  // meal rather than poked into a hidden <p> after the fact, so it says
  // the same thing on Before you start and on the whole method.
  function cookUnscaledHtml(m, idx) {
    var items = (m && m.unscaled_items) || [];
    if (!items.length) return '';
    return '<p class="cook-unscaled" id="cook-unscaled-' + idx + '">' +
      escapeHtml('Eyeball these — they don’t scale automatically: ' + items.join(', ') + '.') +
    '</p>';
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

  // Tomorrow's first real cook, as a cookFocus target — the screen that
  // actually shows tomorrow. Slot order, so it is breakfast before dinner
  // and not whatever the plan happened to list first. A reheat night is
  // skipped: it has no cook screen at all (see kitchenTodayRowHtml), so
  // focusing it would land on a card rather than on tomorrow.
  function cookTomorrowFocusTarget() {
    var tomorrow = tomorrowLocalStr();
    var cooks = ((cookState.data && cookState.data.meals) || [])
      .map(function (m, i) { return { m: m, i: i }; })
      .filter(function (x) { return x.m.date === tomorrow && !x.m.is_leftovers; })
      .sort(function (a, b) { return cookSlotRank(a.m) - cookSlotRank(b.m); });
    if (!cooks.length) return null;
    var m = cooks[0].m;
    return { entryId: m.entry_id, date: m.date, slot: m.slot, title: m.meal || '' };
  }

  // Offer the action only when there is something tomorrow to be shown —
  // a cook counts as well as prep, since the cook is what the tap opens
  // when there is one.
  function cookTomorrowHasSomethingToShow() {
    return cookTomorrowHasPrepOrDefrost() || !!cookTomorrowFocusTarget();
  }

  // "Show me tomorrow" used to land on the Kitchen ROOT with no focus,
  // which shows tomorrow only when tomorrow happens to be a prep-session
  // day — the rest of the time it promised tomorrow and delivered today.
  // Now it opens tomorrow's first cook when there is one, and otherwise
  // the root aimed at the prep it was offered for.
  function cookShowTomorrow() {
    var focus = cookTomorrowFocusTarget();
    if (focus) {
      activateTab('kitchen', true, { cookFocus: focus });
      return;
    }
    kitchenState.scrollToPrep = true;
    cookState.screen = 'overview';
    activateTab('kitchen', true);
    // Already-built tabs are not re-rendered by activateTab, so the flag
    // above would sit unread; a fresh build renders on its own load.
    if (kitchenIsBuilt()) renderCook();
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
        showToast('Noted — that’ll steer next week.', cookTomorrowHasSomethingToShow() ? {
          label: 'Show me tomorrow',
          onClick: cookShowTomorrow
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
    document.querySelectorAll('#kit-cook-view .cook-mic').forEach(function (btn) {
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

  // ---------- Grocery photo-scan sheet wiring ----------
  // Same body-level scrim/sheet pattern as the week sheet above. The
  // render/upload/save logic (groScanOpenSheet, groScanRenderReview,
  // groScanUploadPhoto, groScanSave, ...) lives with the rest of Grocery,
  // near groAddItem — only the DOM listeners live here, because
  // tests/test_grocery_fast_sort.py evaluates that whole Grocery region
  // under Node against a stub with no `document`.
  var groScanSheetEl = document.getElementById('gro-scan-sheet');
  var groScanScrimEl = document.getElementById('gro-scan-scrim');
  var groScanInputEl = document.getElementById('gro-scan-input');
  if (groScanSheetEl) {
    groScanSheetEl.addEventListener('click', function (e) {
      var el = e.target.closest('[data-gro]');
      if (!el) return;
      if (el.dataset.gro === 'scan-close') { groScanCloseSheet(); return; }
      if (el.dataset.gro === 'scan-save') { groScanSave(); return; }
    });
    // Delegated so it works for every row without re-wiring on each render
    // (the same reason onGroceryClick is one listener for the whole tab).
    groScanSheetEl.addEventListener('change', function (e) {
      if (!e.target.classList.contains('gro-scan-check')) return;
      var idx = Number(e.target.dataset.idx);
      if (!groScanState.items[idx]) return;
      groScanState.items[idx].keep = e.target.checked;
      var row = e.target.closest('.gro-scan-row');
      if (row) row.classList.toggle('unchecked', !e.target.checked);
    });
    groScanSheetEl.addEventListener('input', function (e) {
      var idx = Number(e.target.dataset.idx);
      if (!groScanState.items[idx]) return;
      if (e.target.classList.contains('gro-scan-name')) groScanState.items[idx].item = e.target.value;
      if (e.target.classList.contains('gro-scan-qty')) groScanState.items[idx].quantity = e.target.value;
    });
  }
  if (groScanScrimEl) groScanScrimEl.addEventListener('click', groScanCloseSheet);
  var groScanHandleEl = document.getElementById('gro-scan-handle');
  if (groScanHandleEl) groScanHandleEl.addEventListener('click', groScanCloseSheet);
  var groScanCloseBtnEl = document.getElementById('gro-scan-close');
  if (groScanCloseBtnEl) groScanCloseBtnEl.addEventListener('click', groScanCloseSheet);
  if (groScanInputEl) {
    groScanInputEl.addEventListener('change', function () {
      var file = groScanInputEl.files && groScanInputEl.files[0];
      // Cleared immediately so picking the exact same file twice in a row
      // still fires a change event the second time.
      groScanInputEl.value = '';
      if (file) groScanUploadPhoto(file);
    });
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
    // The coaching example prompts go with them, and for the same reason:
    // a household that has just typed its own sentence has no more use for
    // a suggested one. (renderAskExamples is defined further down; this
    // whole file is one IIFE, so the declaration is hoisted.)
    renderAskExamples(null);
  }

  // Named one-tap intents (Loop Board: "Ask sheet: offer named one-tap
  // intents instead of only a blank box", decided by Emily 2026-09-10).
  // A blank box makes a household guess the magic words; four real jobs,
  // said the way a person says them, teach the app's range in one glance.
  // Replaces the two context-aware quick actions this surface used to
  // carry ("Pomona: rethink the chat's pre-given quick actions", Emily
  // 2026-09-03) — the same machinery, four better lines through it.
  //
  // FIXED — the same four on every visit, and that is Emily's call rather
  // than a shortcut. Context-aware chips are where this goes next and are
  // the better product; a fixed set ships now and tells us which intents
  // people actually tap, which is the thing nobody can currently answer.
  // TWO COSTS OF A FIXED SET, both found by review and neither one a
  // reason to gate a chip here — the gate would be the context-awareness
  // Emily deferred, and deferring it was the decision:
  //   * On a household with no plan, three of the four ask about a week
  //     that doesn't exist. The assistant answers honestly, so the price
  //     is a wasted tap.
  //   * THE OTHER DIRECTION HAS TEETH AND IS THE ONE TO KNOW ABOUT. The
  //     pair this replaces offered a planning chip ONLY under `!hasPlan`.
  //     "Plan the rest of my week" is now offered to a household already
  //     mid-week, and generating a period TAKES OVER the days it overlaps
  //     (agent.py's own instruction; `retire_overlapping_plans` has no
  //     exemption for an APPROVED plan). The chip's wording points at the
  //     remaining days, which is what was asked for — but nothing confirms
  //     first, and the underlying "replan over a running week without
  //     asking" hazard is its own Loop Board card. Raised with Emily
  //     rather than answered here: the four are hers.
  //
  // EACH CHIP SENDS ITS OWN LABEL, WORD FOR WORD. A chip carrying a hidden
  // sentence is one nobody can learn from — and teaching what you're
  // allowed to say is the whole job here, so what it sends has to be what
  // it says. It also leaves no second wording to drift out of step.
  //
  // GONE WITH THE OLD PAIR: "Add … to the grocery list", which pre-filled
  // the composer instead of running. It isn't one of the four, and this
  // card's own rule is that tapping an intent does the thing. Grocery's
  // own placeholder ("Add oat milk and lemons…", ASK_HINTS.grocery) still
  // teaches the same sentence in the place it belongs.
  var ASK_INTENTS = [
    'Plan the rest of my week',
    'What should I cook tonight?',
    'Swap tonight for something quicker',
    'What do I need to defrost?'
  ].map(function (label) { return { label: label, msg: label }; });

  // The chips no longer depend on the plan, so they go up the moment the
  // ask experience is built rather than after a round trip. The fetch is
  // still made and is no longer optional-feeling: /api/week-menu is what
  // setDishIndex reads, so a reply that names a dish can link it without a
  // request of its own. A failed fetch costs those links and nothing else.
  function loadQuickActionChips() {
    renderAskChips(ASK_INTENTS);
    fetch('/api/week-menu')
      .then(function (res) { return res.ok ? res.json() : null; })
      .catch(function () { return null; })
      .then(function (weekMenu) {
        if (weekMenu) setDishIndex(weekMenu);
      });
  }

  // ---------- A dish name in a chat reply is a link too ----------
  // The hard half of Emily's rule (2026-09-09): in a reply the dish name is
  // generated prose, not a row with an id on it. Nothing in the ChatAction
  // contract says which words in a sentence are dishes, and asking the
  // model to mark them up would be trusting generated text to be accurate
  // about the plan.
  //
  // So this does not try to find dish names in the reply. It looks for the
  // dishes it ALREADY KNOWS are on the plan — read off /api/week-menu, the
  // same payload Meals renders — and links those, exactly those. That set
  // is also precisely the set that HAS a recipe screen to open, since cook
  // mode is per plan entry: a dish the app can't open is a dish this
  // leaves as prose, which is the honest half of the same rule.
  //
  // Names are matched longest-first so "Chicken Tacos" wins over a plan
  // that also has a "Chicken" on it.
  //
  // THE LINK CARRIES THE DISH'S NAME, NEVER A POSITION IN THIS INDEX
  // (fixed 2026-09-10, found by review). The first version wrote
  // data-dish="<array index>" and read dishIndex.entries[i] back at CLICK
  // time — but this index is rebuilt and re-sorted on every /api/week-menu
  // read, including the one sendAskMessage fires on the line straight after
  // it renders the bubble. So the very turns that most want a link — the
  // ones that changed the week — invalidated the indices inside the reply
  // describing that change, and a bubble labelled "Chicken Tacos" opened
  // "Apple slices". On a screen whose apricot primary is "Mark it cooked"
  // that is one tap from a wrong write. The name is the stable handle: it
  // is what the reply actually says, and it is re-resolved against the
  // CURRENT plan when the link is tapped (dishTargetForName), so a link
  // either opens the dish it names or opens nothing at all.
  //
  // `byName` is that lookup, and `entries` is only the set that is safe to
  // link at all — see the ambiguity rule in setDishIndex.
  var dishIndex = { entries: [], byName: {}, re: null };

  // A dish on TWO different nights is not linked at all (2026-09-10, found
  // by review). The index used to keep the first occurrence and drop the
  // rest, so a reply saying "Chicken Tacos is on Saturday" opened Thursday
  // — and cook mode's check-off writes against that entry_id, so "Mark it
  // cooked" would have ticked the wrong night. The reply's own prose is
  // the only thing that knows which night is meant, and reading a weekday
  // out of generated text to pick between two entries is exactly the
  // "trusting generated text to be accurate about the plan" this feature
  // refuses to do everywhere else. So the honest answer is the smaller
  // one: a name that names two meals names none of them, and stays prose.
  // Every other dish still links.
  function setDishIndex(weekMenu) {
    var groups = {};
    var order = [];
    ((weekMenu && weekMenu.days) || []).forEach(function (day) {
      var slots = WEEK_SLOTS.slice();
      (day.snacks || []).forEach(function (_, i) { slots.push(snackSlotKey(i)); });
      slots.forEach(function (slot) {
        var entry = daySlotEntry(day, slot);
        var target = recipeTargetForEntry(entry, day.date, slot);
        // A name of two characters or fewer is not a dish anybody wrote —
        // and a one-letter alternation would light up half the reply.
        if (!target || !target.title || target.title.trim().length < 3) return;
        var key = target.title.trim().toLowerCase();
        // What makes two rows the SAME meal rather than two of them: the
        // plan entry when there is one, otherwise the night it sits on. A
        // component-based week carries neither, and one name across two of
        // its days is still two meals.
        var slotKey = target.entryId != null
          ? 'e:' + target.entryId
          : 'd:' + (target.date || '?') + '/' + (target.slot || '?');
        if (!groups[key]) { groups[key] = { target: target, slots: {}, count: 0 }; order.push(key); }
        if (!groups[key].slots[slotKey]) {
          groups[key].slots[slotKey] = true;
          groups[key].count += 1;
        }
      });
    });
    var entries = [];
    var byName = {};
    order.forEach(function (key) {
      if (groups[key].count !== 1) return; // two nights, one name: prose.
      entries.push(groups[key].target);
      byName[key] = groups[key].target;
    });
    entries.sort(function (a, b) { return b.title.length - a.title.length; });
    dishIndex.entries = entries;
    dishIndex.byName = byName;
    dishIndex.re = entries.length
      ? new RegExp('(^|[^A-Za-z0-9])(' +
          entries.map(function (e) { return escapeRegExp(e.title.trim()); }).join('|') +
          ')(?![A-Za-z0-9])', 'g')
      : null;
  }

  // What a tapped dish name resolves to RIGHT NOW, or null. Null is a real
  // answer, not a failure: the plan may have moved on since the reply was
  // written (the dish was swapped out, or it is now on two nights), and a
  // link that opens the wrong meal is worse than one that opens none.
  //
  // **It hands back the NAME ONLY, deliberately** (2026-09-10, second
  // review). Returning the whole `{entryId, date, slot, title}` target the
  // index holds was still not safe, one level below the fix that put the
  // name in the markup: a real swap DELETES and RECREATES the plan entry
  // (`weekly_plan.swap_meal_in_plan`), so the recorded entryId is always a
  // miss, and `cookResolveFocusIndex`'s NEXT fallback is date+slot with no
  // name check — which lands on whatever dish now occupies that night.
  // Reproduced end to end: a link labelled Chicken Tacos opened Bean Chili
  // after a real swap of that slot, on a screen whose only entry-bearing
  // control is "Mark it cooked".
  //
  // A name-only target skips both earlier branches and goes straight to
  // the resolver's name match, which compares against the cook card's own
  // `meal` — so a chat link opens the dish it names or lands on the
  // Kitchen root, and can never open a different one. The resolver itself
  // is deliberately NOT changed: Today's rows and Meals' "Cook this" read
  // the target and the label out of the same payload in one breath, and
  // their id/date fallbacks are right for them.
  function dishTargetForName(name) {
    var key = String(name == null ? '' : name).trim().toLowerCase();
    var hit = key && dishIndex.byName[key];
    return hit ? { title: hit.title } : null;
  }

  function escapeRegExp(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

  // Re-read the plan when something changed it and no screen is going to.
  // Silent on failure: a reply whose dish names stay prose is a smaller
  // problem than a toast about a request nobody asked for.
  function refreshDishIndex() { return readDishIndex(); }

  function readDishIndex() {
    return fetch('/api/week-menu')
      .then(function (res) { return res.ok ? res.json() : null; })
      .catch(function () { return null; })
      .then(function (weekMenu) { if (weekMenu) setDishIndex(weekMenu); return !!weekMenu; });
  }

  // Opening a dish named in a reply. The index behind that link can be
  // stale — refreshDishIndex is silent on failure by design, so one
  // dropped /api/week-menu leaves the reply naming last week's dinners —
  // and this is a deliberate tap, so it can afford to re-read the plan
  // first (one local SQLite lookup) rather than act on what it last heard.
  // A dish that has since left the plan says so instead of opening
  // something. A failed re-read falls through to the index we have, which
  // still cannot open a different dish than the one named — it is only the
  // "this is gone" message that needs the network.
  function openDishFromChat(name) {
    var back = currentTabKey();
    readDishIndex().then(function () {
      var target = dishTargetForName(name);
      if (!target) { showToast('That’s not on the plan any more.'); return; }
      // The sheet closes on the way, exactly as an action card's View
      // does — at desktop widths closeAskSheet is a no-op and the
      // conversation stays beside the recipe.
      closeAskSheet();
      openRecipeFor(target, { label: 'the chat', tab: back, reopenAsk: true });
    });
  }

  // One run of PLAIN TEXT, split into prose and dish names:
  // [{text}, {dish}, {text}, ...]. Case-insensitive matching is
  // deliberately NOT used: the reply says the dish the way the plan spells
  // it, and a loose match is how "Bowl" starts linking sentences.
  //
  // This works on text, never on markup — see linkifyDishNamesIn.
  function dishSegments(text) {
    var src = String(text == null ? '' : text);
    if (!dishIndex.re || !src) return [{ text: src }];
    var out = [];
    var last = 0;
    var m;
    dishIndex.re.lastIndex = 0;
    while ((m = dishIndex.re.exec(src)) !== null) {
      var at = m.index + m[1].length; // m[1] is the boundary character the match ate
      if (at > last) out.push({ text: src.slice(last, at) });
      out.push({ dish: m[2] });
      last = at + m[2].length;
    }
    if (!out.length) return [{ text: src }];
    if (last < src.length) out.push({ text: src.slice(last) });
    return out;
  }

  // Elements whose text is never prose to be linkified: a dish name inside
  // a link or a button is already one, and code is quoted verbatim.
  var DISH_SKIP_TAGS = { BUTTON: 1, A: 1, CODE: 1, PRE: 1 };

  // Walks the TEXT NODES of an already-rendered reply and turns each known
  // dish name into a button.
  //
  // It used to run a regex over renderMarkdownLite's HTML *string* instead
  // (fixed 2026-09-10, found by review). That is rewriting a rendered
  // artifact rather than the thing it was rendered from, and it showed:
  // a household with a dish called "table" or "strong" had the reply's
  // markdown table flattened into literal escaped tags, because the regex
  // reached inside `<table class="ask-msg-table">` and `<strong>`. Nothing
  // could be injected — the escaping was correct on both sides — but the
  // fix is the same one the dish-link bug above needed: work on the real
  // thing (text, in the DOM), not on a string that looks like it.
  function linkifyDishNamesIn(root) {
    if (!root || !dishIndex.re) return root;
    var kids = Array.prototype.slice.call(root.childNodes || []);
    kids.forEach(function (node) {
      if (node.nodeType === 3) { // text
        var segs = dishSegments(node.nodeValue);
        // One segment that is plain text means nothing matched. One segment
        // that is a DISH means the whole node is the dish name — which is
        // exactly what a bolded name or a table cell looks like, so this
        // cannot be a "fewer than two segments, leave it" test.
        if (segs.length === 1 && segs[0].dish === undefined) return;
        var frag = document.createDocumentFragment();
        segs.forEach(function (seg) {
          if (seg.dish === undefined) { frag.appendChild(document.createTextNode(seg.text)); return; }
          var btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'ask-dish';
          btn.setAttribute('data-dish', seg.dish);
          btn.textContent = seg.dish;
          frag.appendChild(btn);
        });
        root.replaceChild(frag, node);
      } else if (node.nodeType === 1 && !DISH_SKIP_TAGS[node.tagName]) {
        linkifyDishNamesIn(node);
      }
    });
    return root;
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
          // Everything else SENDS. There used to be a third branch here —
          // `if (action.prefill) openAskSheet(action.prefill)`, for the old
          // "Add … to the grocery list" chip, which focused the composer
          // instead of doing anything. Nothing produces a `prefill` now
          // (ASK_INTENTS run, offerNextStepChips navigate or send), so the
          // branch is gone rather than left standing with a comment
          // describing a chip that no longer exists. `openAskSheet(text)`
          // still takes a prefill and is still used by the entry points
          // that genuinely want one — they just don't come through here.
          sendAskMessage(action.msg);
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
      // The one wording for "there's a draft, go approve it". It used to
      // be shared with the pre-conversation quick actions; those are the
      // fixed four now (ASK_INTENTS) and no longer say it, so this is the
      // only place it lives.
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
    activateTab('week', true);
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
    // Only the assistant's side: the household's own message is their
    // words, and rewriting what someone just typed is not this app's
    // business.
    bubble.innerHTML = renderMarkdownLite(text);
    if (role === 'assistant') linkifyDishNamesIn(bubble);
    bubble.querySelectorAll('[data-dish]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        // Resolved from the NAME, against the plan as it stands right now
        // — never from a position recorded when the bubble was drawn, and
        // never carrying that moment's entry id or night either. See
        // openDishFromChat / dishTargetForName.
        openDishFromChat(btn.getAttribute('data-dish'));
      });
    });
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
        // loadWeekMenu refreshes the Cook state too — see its tail, and
        // the dish index the chat's own dish links read (setDishIndex).
        loadWeekMenu(panels.week);
      } else if (action.tab === 'week') {
        // The same week changed, but Meals has never been opened in this
        // page load, so there is no panel to reload — and the dish index
        // would go on naming last week's dinners in every reply. One
        // request, and only for a household that has actually changed the
        // plan from chat without ever opening the tab.
        refreshDishIndex();
      } else if (action.tab === 'kitchen') {
        // Kitchen was the second hole in this. The backend has always
        // tagged these writes with tab: 'kitchen' (app/main.py's
        // _KITCHEN_TOOLS) and this function has never had a branch for
        // it — for the same reason Grocery didn't, and with the same
        // consequence: "we finished the chicken" in chat changed the
        // inventory and the Kitchen tab went on showing the old counts.
        //
        // One call covers the whole tool set now: check_off_meal /
        // check_off_prep_step / resolve_attention_item and
        // update_inventory and friends all land on the Kitchen tab, which
        // re-reads the cooker view and the inventory tile together.
        refreshKitchenPanel();
        refreshKitchenPanel();
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
        // knowledge. The Preferences sheet does now, and its row subtitles
        // are exactly what these tools change — so the cached read behind
        // them is dropped, and the sheet re-reads if it is open. Reading
        // the href rather than adding a tab to the backend keeps the
        // change on this side of the wire, where the surface that went
        // stale lives.
        prefsInvalidate();
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

  // One calm line for a turn that never reached the server (DESIGN_SYSTEM
  // §8: the thing, and its way out, in the same breath). Emily may reword.
  var ASK_NO_SIGNAL_LINE = "I need a signal for this one — try again when you’re back.";

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
      // Asking needs Claude, and Claude needs a connection. With no signal
      // that is the whole answer — not "Error: Failed to fetch".
      var askNoSignal = navigator.onLine === false || (err && err.name === 'TypeError');
      addAskMessage('assistant', askNoSignal ? ASK_NO_SIGNAL_LINE : 'Error: ' + err.message);
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

  // The sheet pushes one history entry while it's open (mobile/tablet only
  // — the desktop Ask column never touches history) so the Android/browser
  // back gesture closes it before it leaves the tab underneath, same as
  // Meals' and Grocery's own step history. This flag is how the shell's
  // shared popstate listener (below) tells "the back gesture just left our
  // pushed entry" apart from an ordinary tab/step change, and how
  // openAskSheet avoids double-pushing on a prefill while the sheet is
  // already open.
  var askSheetHistoryPushed = false;

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
    if (!askSheetHistoryPushed) {
      window.history.pushState({ tab: currentTabKey(), askSheet: true }, '', window.location.pathname);
      askSheetHistoryPushed = true;
    }
    if (prefill) {
      askInput.value = prefill;
      autoGrowAskInput(askInput);
      askInput.focus();
    } else {
      askInput.focus();
    }
  }
  // Every caller — scrim tap, the Back button, Escape, a sent message, and
  // the shell's popstate listener on the back gesture — just forgets the
  // pushed entry rather than calling history.back() on it: deliberately
  // NOT history.back(), same reasoning as goMealsStep's crumb above
  // (see its comment) — an immediate, unrelated pushState elsewhere in the
  // same tap (e.g. an action card's "View" jumping to another tab right
  // after closing the sheet) would race a queued back-traversal in
  // unpredictable ways. Leaving the stale entry in place when the sheet
  // closes without the browser having moved costs nothing more than one
  // invisible extra back-press later landing back on the same tab/path —
  // the same trade every forward-only push in this file already makes.
  function closeAskSheet() {
    askScrim.hidden = true;
    askSheet.hidden = true;
    askSheetHistoryPushed = false;
  }

  askScrim.addEventListener('click', closeAskSheet);
  document.getElementById('ask-sheet-handle').addEventListener('click', closeAskSheet);
  document.getElementById('ask-sheet-back').addEventListener('click', closeAskSheet);
  // Escape closes the sheet on a desktop keyboard (narrower windows below
  // the 1024px Ask-column breakpoint still use the sheet, and any keyboard
  // can be attached at that width). isDesktopAsk() width means the column
  // is showing instead and #ask-sheet is already hidden, so this is a no-op
  // there — the desktop column itself is unchanged.
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !askSheet.hidden) closeAskSheet();
  });
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
    // "Who's this?" — before any tab renders, so nothing is fetched, read
    // or credited as nobody. Only when the household has more than one
    // adult and this device has no pick yet (needs_pick, decided
    // server-side); a one-adult household never sees it. If the lookup
    // fails (offline, an older server) the shell opens anyway — the
    // question is asked next time, and every write still records what it
    // recorded before this existed.
    await ensureWhoPicked();
    activateTab(currentTabKey(), false);
    loadNotifications();
  })();

  // ---------- "Who's this?" (slice 1 of per-adult login, 2026-09-11) ----------
  //
  // The household passphrase opens the door; this says which adult is
  // holding the phone. One screen, once per device, remembered in the
  // signed session cookie (POST /api/whoami/pick — see app/main.py) so it
  // survives reloads and redeploys and is forgotten only by signing out.
  // Reachable again from Preferences' "You're {name}" row to switch.
  //
  // What it changes today: the week's approver, who added a grocery item,
  // who dropped one at the pre-shop check and who started the week's
  // questions all record this adult's name without asking (the server
  // fills them in from the session — tools/_shared.py acting_name), and
  // the "{name} approved the week" notification is no longer shown to the
  // adult who approved. Each adult having their own secret is a later
  // slice; this trusts the device.
  var shellWho = { member: null, adults: [], loaded: false };
  var whoScreenEl = null;
  var whoResolve = null;

  async function loadWhoami() {
    try {
      var res = await fetch('/api/whoami');
      if (!res.ok) throw new Error('whoami failed');
      var data = await res.json();
      shellWho.member = data.member || null;
      shellWho.adults = data.adults || [];
      shellWho.loaded = true;
      return data;
    } catch (err) {
      console.warn('Who am I lookup failed:', err);
      return null;
    }
  }

  async function ensureWhoPicked() {
    var data = await loadWhoami();
    if (!data || !data.needs_pick) return;
    await openWhoScreen(false);
  }

  function buildWhoScreen() {
    if (whoScreenEl) return;
    whoScreenEl = document.createElement('div');
    whoScreenEl.id = 'who-screen';
    whoScreenEl.hidden = true;
    whoScreenEl.setAttribute('role', 'dialog');
    whoScreenEl.setAttribute('aria-modal', 'true');
    whoScreenEl.setAttribute('aria-labelledby', 'who-title');
    whoScreenEl.innerHTML =
      '<div class="who-inner">' +
        '<h1 class="who-title" id="who-title">Who’s this?</h1>' +
        '<p class="who-sub">I’ll remember on this device.</p>' +
        '<div class="who-rows" id="who-rows"></div>' +
        '<p class="who-error" id="who-error" hidden></p>' +
        '<button type="button" class="who-cancel" id="who-cancel" hidden>Never mind</button>' +
      '</div>';
    document.body.appendChild(whoScreenEl);
    whoScreenEl.querySelector('#who-cancel').addEventListener('click', function () { closeWhoScreen(null); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && whoScreenEl && !whoScreenEl.hidden) {
        var cancel = whoScreenEl.querySelector('#who-cancel');
        if (cancel && !cancel.hidden) closeWhoScreen(null);
      }
    });
  }

  function renderWhoRows() {
    var rows = whoScreenEl.querySelector('#who-rows');
    rows.innerHTML = shellWho.adults.map(function (a) {
      return '<button type="button" class="who-row" data-who-pick="' + a.id + '">' +
        '<span class="who-row-initial" aria-hidden="true">' + escapeHtml(a.initial || (a.name || '?').charAt(0).toUpperCase()) + '</span>' +
        '<span class="who-row-name">' + escapeHtml(a.name) + '</span>' +
        ICONS.arrow +
      '</button>';
    }).join('');
    rows.querySelectorAll('[data-who-pick]').forEach(function (btn) {
      btn.addEventListener('click', function () { pickWho(parseInt(btn.getAttribute('data-who-pick'), 10)); });
    });
  }

  // Resolves to the picked member, or null if they backed out (only
  // possible when `switching` — the first ask has no way past it but a
  // name, because there is nothing to show until there is a someone).
  function openWhoScreen(switching) {
    buildWhoScreen();
    closeAskSheet();
    closeWeekSheet();
    renderWhoRows();
    whoScreenEl.querySelector('#who-cancel').hidden = !switching;
    whoScreenEl.querySelector('#who-error').hidden = true;
    whoScreenEl.hidden = false;
    var first = whoScreenEl.querySelector('.who-row');
    if (first) first.focus();
    return new Promise(function (resolve) { whoResolve = resolve; });
  }

  function closeWhoScreen(answer) {
    if (!whoScreenEl) return;
    whoScreenEl.hidden = true;
    var resolve = whoResolve;
    whoResolve = null;
    if (resolve) resolve(answer);
  }

  async function pickWho(memberId) {
    var buttons = whoScreenEl.querySelectorAll('.who-row');
    buttons.forEach(function (b) { b.disabled = true; });
    var errorEl = whoScreenEl.querySelector('#who-error');
    errorEl.hidden = true;
    try {
      var res = await fetch('/api/whoami/pick', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ member_id: memberId })
      });
      if (!res.ok) throw new Error('pick failed');
      var data = await res.json();
      shellWho.member = data.member || null;
      closeWhoScreen(shellWho.member);
      // The feed is addressed now (the approver is not told they
      // approved), so it is re-read for whoever this is. The Preferences
      // row names them, if the sheet is open.
      loadNotifications();
      if (prefsState.open) renderPrefsRows();
    } catch (err) {
      console.warn('Picking who this is failed:', err);
      errorEl.textContent = 'That didn’t save. Try tapping your name again.';
      errorEl.hidden = false;
      buttons.forEach(function (b) { b.disabled = false; });
    }
  }

  // The Preferences row: who this device is opened as, and the way to
  // change it. Only when there is a choice to make — a one-adult household
  // gets no row, because "You're Emily / Not you?" would be a question
  // with one answer.
  function whoPrefsRowHtml() {
    if (!shellWho.member || shellWho.adults.length < 2) return '';
    return '<button type="button" class="prefs-row" data-who="switch">' +
      '<span class="prefs-row-text">' +
        '<span class="prefs-row-title">You’re ' + escapeHtml(shellWho.member.name) + '</span>' +
        '<span class="prefs-row-sub">Not you? Switch</span>' +
      '</span>' +
      ICONS.arrow +
    '</button>';
  }

  document.addEventListener('click', function (e) {
    var target = e.target && e.target.closest && e.target.closest('[data-who="switch"]');
    if (!target) return;
    closePrefsSheet();
    openWhoScreen(true);
  });

  // ---------- Preferences: what Pomona knows about your household ----------
  //
  // Emily's approved design, 2026-09-08. Everything the app has been told
  // about the household used to live on the Kitchen tab, which meant the
  // cook's tab opened with a paragraph about the household and a row of
  // counts. It is a sheet now, behind one gear in the header of every root
  // screen — settings belong one tap from anywhere, not in the middle of
  // one screen's job.
  //
  // Each row is a summary of an answer plus the way to change it: tapping
  // one opens What we know on the tab that owns that answer (the same
  // Kitchen entry sheet the Inventory tile uses, so there is one mechanism
  // and not two). Nothing here is apricot — no line in this sheet is
  // urgent, and none of it is a task.
  //
  // The subtitles come from ONE read of /api/memory per open, cached until
  // something writes to it (prefsInvalidate, called from the chat action
  // refresher for exactly the tools that change these answers).
  var PREFS_GEAR_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<circle cx="12" cy="12" r="3.1"/>' +
    '<path d="M19.2 14.2a1.5 1.5 0 0 0 .3 1.65l.05.05a1.8 1.8 0 1 1-2.55 2.55l-.05-.05a1.5 1.5 0 0 0-1.65-.3 1.5 1.5 0 0 0-.9 1.37v.13a1.8 1.8 0 1 1-3.6 0v-.07a1.5 1.5 0 0 0-.98-1.37 1.5 1.5 0 0 0-1.65.3l-.05.05A1.8 1.8 0 1 1 5.57 15.9l.05-.05a1.5 1.5 0 0 0 .3-1.65 1.5 1.5 0 0 0-1.37-.9h-.13a1.8 1.8 0 1 1 0-3.6h.07a1.5 1.5 0 0 0 1.37-.98 1.5 1.5 0 0 0-.3-1.65l-.05-.05A1.8 1.8 0 1 1 8.06 4.47l.05.05a1.5 1.5 0 0 0 1.65.3h.07a1.5 1.5 0 0 0 .9-1.37v-.13a1.8 1.8 0 1 1 3.6 0v.07a1.5 1.5 0 0 0 .9 1.37 1.5 1.5 0 0 0 1.65-.3l.05-.05a1.8 1.8 0 1 1 2.55 2.55l-.05.05a1.5 1.5 0 0 0-.3 1.65v.07a1.5 1.5 0 0 0 1.37.9h.13a1.8 1.8 0 1 1 0 3.6h-.07a1.5 1.5 0 0 0-1.37.9z"/>' +
    '</svg>';

  // The gear itself. Rendered into the header of each of the four root
  // screens — and nowhere deeper: Meals' Day and Meal steps hide the row
  // it sits in (renderMealsStep), Grocery hides it while shopping a store
  // (renderGrocery), and Kitchen's lives inside the root view, which cook
  // mode replaces outright.
  function prefsGearHtml() {
    return '<button type="button" class="prefs-gear" data-prefs="open" ' +
      'aria-label="Preferences" title="Preferences">' + PREFS_GEAR_ICON + '</button>';
  }

  // For a screen whose header is not a title row the gear can sit inside
  // (Meals, whose root header belongs to the week card): its own right-
  // aligned row, hidden as a unit on the deeper steps.
  function prefsGearRowHtml(id) {
    return '<div class="prefs-gear-row" id="' + id + '">' + prefsGearHtml() + '</div>';
  }

  var prefsState = { memory: null, calendar: null, open: false };

  function prefsInvalidate() {
    prefsState.memory = null;
    if (prefsState.open) loadPrefs();
  }

  // ---------- reading the answers back ----------

  function prefsPeopleLine(mem) {
    var members = (mem && mem.members) || [];
    // The same words as the other four rows. Five different ways of
    // saying "you haven't told me" read as five different states; one
    // reads as one sheet with nothing in it yet.
    if (!members.length) return 'Not set yet';
    var names = members.map(function (m) { return m.name; }).join(', ');
    var avoid = [];
    members.forEach(function (m) {
      (m.dietary_restrictions || []).forEach(function (r) { if (r) avoid.push(r); });
    });
    if (!avoid.length) return names;
    var shown = avoid.slice(0, 2).join(', ');
    return names + ' · ' + shown + (avoid.length > 2 ? ' +' + (avoid.length - 2) : '');
  }

  // "dinner around 6:30" — the household's own answer said as a clock, from
  // the same mapping the fridge-move scheduler and Today's timeline read
  // (app/tools/defrost.py's _DINNER_CLOCK_BY_WINDOW). 'all_over' is a real
  // answer and gets real words rather than a made-up time.
  var PREFS_DINNER_CLOCK = {
    '5_6ish': 'dinner around 5:30',
    '6_8': 'dinner around 7',
    'later': 'dinner around 8',
    'all_over': 'dinner whenever it lands'
  };

  function prefsRhythmLine(mem) {
    var rhythm = (mem && mem.rhythm) || {};
    var bits = [];
    if (PREFS_DINNER_CLOCK[rhythm.dinner_window]) bits.push(PREFS_DINNER_CLOCK[rhythm.dinner_window]);
    var anchor = rhythm.planning_anchor || '';
    if (anchor === 'as_we_go') bits.push('planned as you go');
    else if (anchor) bits.push('plan ready ' + anchor.charAt(0).toUpperCase() + anchor.slice(1) + 's');
    return bits.length ? bits.join(' · ') : 'Not set yet';
  }

  function prefsPrepLine(mem) {
    var summary = ((mem && mem.rhythm) || {}).prep_days_summary || '';
    // prep_days_summary is a sentence ("Preps on Sunday (about an hour).");
    // this row already says "Prep days", so the lead-in and the full stop
    // are the app repeating itself.
    return summary ? summary.replace(/^Preps on /, '').replace(/\.$/, '') : 'Not set yet';
  }

  var PREFS_LEFTOVERS = {
    'love_them': 'leftovers welcome',
    'fine_sometimes': 'leftovers now and then',
    'fresh_each_night': 'fresh every night'
  };

  // Every line in this sheet says "Not set yet" until the household has
  // actually said something — a sheet titled "What Pomona knows about your
  // household" may not print a schema default back as a fact. The other
  // four rows get that for free (members and usual_stores are empty until
  // filled; the rhythm answers are NULL until answered), but
  // snacks_per_week is NOT NULL DEFAULT 3, so it needs the API to say
  // whether the 3 was answered or assumed — snacks_per_week_set
  // (app/tools/memory.py). Without it a brand-new household was told it
  // eats three snacks a week, which nobody had ever said.
  function prefsEatingLine(mem) {
    var bits = [];
    var stance = ((mem && mem.rhythm) || {}).leftovers_stance || '';
    if (PREFS_LEFTOVERS[stance]) bits.push(PREFS_LEFTOVERS[stance]);
    // Two snack numbers, two answered-flags, and this line reads back
    // whichever question the household was actually asked. Onboarding asks
    // per DAY since 2026-09-08 (Julia), so that one wins when both are on;
    // a household whose only snacks answer predates that column still sees
    // their own per-week number rather than a per-day default nobody said.
    // "no snacks" is a real answer either way and gets real words rather
    // than being dropped as a falsy number.
    if (mem && mem.snacks_per_day_set) {
      var perDay = mem.snacks_per_day || 0;
      bits.push(perDay ? perDay + ' snack' + (perDay === 1 ? '' : 's') + ' a day' : 'no snacks');
    } else if (mem && mem.snacks_per_week_set) {
      var perWeek = mem.snacks_per_week || 0;
      bits.push(perWeek ? perWeek + ' snack' + (perWeek === 1 ? '' : 's') + ' a week' : 'no snacks');
    }
    return bits.length ? bits.join(' · ') : 'Not set yet';
  }

  function prefsStoresLine(mem) {
    var stores = (mem && mem.usual_stores) || [];
    return stores.length ? stores.join(', ') : 'Not set yet';
  }

  // Loop Board "Meals: plan the week around what's actually on the
  // household's calendar" (2026-09-11). Its own small read (/api/calendar)
  // rather than a field on /api/memory, because household memory also
  // feeds the generation prompt and the calendar's link must never go
  // near one — status() only ever returns the label and a redacted tail.
  // "Not connected" is the row's whole empty state: no nudge, no badge.
  // (The typeof guard is for tests/test_kitchen_and_preferences.py, which
  // runs the row functions under node from a slice that has no prefsState.)
  function prefsCalendarLine() {
    var cal = typeof prefsState !== 'undefined' ? prefsState.calendar : null;
    if (!cal || !cal.connected) return 'Not connected';
    return cal.last_error ? (cal.label || 'Connected') + ' · couldn’t reach it' : (cal.label || 'Connected');
  }

  // Every row: what it says, and which tab of What we know owns the answer
  // behind it. 'rhythm/prep-days' is a tab plus a spot inside it — see
  // static/memory.html's openingTab/showKitchenTab.
  var PREFS_ROWS = [
    { title: 'Who’s here', tab: 'people', line: prefsPeopleLine },
    { title: 'Your rhythm', tab: 'rhythm', line: prefsRhythmLine },
    { title: 'Prep days', tab: 'rhythm/prep-days', line: prefsPrepLine },
    { title: 'How you eat', tab: 'taste', line: prefsEatingLine },
    { title: 'Your calendar', tab: 'rhythm/calendar', line: prefsCalendarLine },
    { title: 'Stores', tab: 'stores', line: prefsStoresLine }
  ];

  // ---------- the sheet ----------

  var prefsSheetEl = null;
  var prefsScrimEl = null;

  function buildPrefsSheet() {
    if (prefsSheetEl) return;
    prefsScrimEl = document.createElement('div');
    prefsScrimEl.id = 'prefs-scrim';
    prefsScrimEl.hidden = true;
    prefsSheetEl = document.createElement('div');
    prefsSheetEl.id = 'prefs-sheet';
    prefsSheetEl.hidden = true;
    prefsSheetEl.setAttribute('role', 'dialog');
    prefsSheetEl.setAttribute('aria-modal', 'true');
    prefsSheetEl.setAttribute('aria-labelledby', 'prefs-title');
    prefsSheetEl.innerHTML =
      '<div class="ask-sheet-handle" id="prefs-handle"></div>' +
      '<div class="kit-sheet-titlerow">' +
        '<span class="kit-sheet-title" id="prefs-title">Preferences</span>' +
        '<span class="kit-sheet-hairline"></span>' +
        '<button type="button" class="kit-sheet-close" id="prefs-close" aria-label="Close">&times;</button>' +
      '</div>' +
      '<p class="prefs-sub">What Pomona knows about your household</p>' +
      '<div class="prefs-rows" id="prefs-rows"></div>';
    // Body level, like every other sheet here: position:fixed has to sit
    // outside the tab panel's stacking and scroll context.
    document.body.appendChild(prefsScrimEl);
    document.body.appendChild(prefsSheetEl);
    prefsScrimEl.addEventListener('click', closePrefsSheet);
    prefsSheetEl.querySelector('#prefs-handle').addEventListener('click', closePrefsSheet);
    prefsSheetEl.querySelector('#prefs-close').addEventListener('click', closePrefsSheet);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && prefsSheetEl && !prefsSheetEl.hidden) closePrefsSheet();
    });
  }

  function renderPrefsRows() {
    if (!prefsSheetEl) return;
    var rows = prefsSheetEl.querySelector('#prefs-rows');
    if (!rows) return;
    var mem = prefsState.memory;
    rows.innerHTML =
      // Who this device is opened as, first — see whoPrefsRowHtml (empty
      // for a one-adult household).
      whoPrefsRowHtml() +
      PREFS_ROWS.map(function (row) {
        var line = mem ? row.line(mem) : 'Reading it back…';
        return '<button type="button" class="prefs-row" data-prefs="tab" data-tab="' + row.tab + '">' +
          '<span class="prefs-row-text">' +
            '<span class="prefs-row-title">' + escapeHtml(row.title) + '</span>' +
            '<span class="prefs-row-sub">' + escapeHtml(line) + '</span>' +
          '</span>' +
          ICONS.arrow +
        '</button>';
      }).join('') +
      // Onboarding coaching part 3 (2026-09-08): the permanent way back to
      // "Helpful tips". A .prefs-row like the five above it, but it opens a
      // sheet instead of a "What we know" tab, so it is written out here
      // rather than added to PREFS_ROWS (whose rows all read a memory field
      // back).
      '<button type="button" class="prefs-row" data-tips="open">' +
        '<span class="prefs-row-text">' +
          '<span class="prefs-row-title">Helpful tips</span>' +
          '<span class="prefs-row-sub">How to ask me for things</span>' +
        '</span>' +
        ICONS.arrow +
      '</button>' +
      // The second group: a way out of a bad moment, and the way out of the
      // app. Same quiet tile the Kitchen tab used to carry — one component,
      // one place it is defined.
      '<div class="prefs-group2">' +
        snwTile() +
        '<button type="button" class="prefs-tile" data-prefs="signout">' +
          '<span class="snw-tile-icon">' + PREFS_SIGNOUT_ICON + '</span>' +
          '<span class="snw-tile-text">' +
            '<span class="snw-tile-title">Sign out</span>' +
            '<span class="snw-tile-sub">You’ll need your passphrase to get back in</span>' +
          '</span>' +
        '</button>' +
      '</div>';
  }

  var PREFS_SIGNOUT_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4.5H18a1.5 1.5 0 0 1 1.5 1.5v12a1.5 1.5 0 0 1-1.5 1.5h-3.5"/><path d="M10 8.5 6.5 12l3.5 3.5"/><path d="M6.5 12H15"/></svg>';

  // One read per open, cached. The rows render immediately off the cache
  // (or with a placeholder line) rather than waiting on the network — a
  // sheet that opens empty and fills in beats a sheet that opens late.
  async function loadPrefs() {
    // The calendar row is re-read on every open (it is one tiny request,
    // and connecting or disconnecting happens inside the What-we-know
    // sheet, which this cache would otherwise never hear about).
    loadPrefsCalendar();
    if (prefsState.memory) { renderPrefsRows(); return; }
    try {
      var res = await fetch('/api/memory');
      if (!res.ok) throw new Error('memory lookup failed');
      prefsState.memory = await res.json();
    } catch (err) {
      console.warn('Preferences lookup failed:', err);
      prefsState.memory = null;
    }
    if (prefsState.open) renderPrefsRows();
  }

  async function loadPrefsCalendar() {
    try {
      var res = await fetch('/api/calendar');
      prefsState.calendar = res.ok ? await res.json() : null;
    } catch (err) {
      prefsState.calendar = null;
    }
    if (prefsState.open) renderPrefsRows();
  }

  function openPrefsSheet() {
    buildPrefsSheet();
    // One sheet at a time, the same rule every other sheet here follows.
    closeAskSheet();
    closeWeekSheet();
    closeKitchenSheet();
    closeSnwSheet();
    prefsState.open = true;
    renderPrefsRows();
    prefsScrimEl.hidden = false;
    prefsSheetEl.hidden = false;
    loadPrefs();
  }

  function closePrefsSheet() {
    if (!prefsSheetEl) return;
    prefsState.open = false;
    prefsScrimEl.hidden = true;
    prefsSheetEl.hidden = true;
  }

  // Delegated, so the gear works from every root screen's header without
  // any renderer wiring a listener, and so nothing has to be re-bound when
  // a panel re-renders under it.
  document.addEventListener('click', function (e) {
    var target = e.target && e.target.closest && e.target.closest('[data-prefs]');
    if (!target) return;
    var what = target.getAttribute('data-prefs');
    if (what === 'open') return openPrefsSheet();
    if (what === 'tab') {
      closePrefsSheet();
      // What we know, opened on the tab that owns this answer — the same
      // Kitchen entry sheet the Inventory tile uses.
      openKitchenSheet('memory', target.getAttribute('data-tab'));
      return;
    }
    if (what === 'signout') {
      // One line, plainly, before the app hands the session back. Calm and
      // reversible-sounding because it is: the way back is the passphrase
      // they already have.
      if (window.confirm('Sign out of Pomona on this device?')) {
        groForgetOffline();   // the grocery copy is this household's, not the phone's
        window.location.href = '/logout';
      }
    }
  });

  // ---------- Onboarding coaching (Emily + Julia, 2026-09-08) ----------
  //
  // Julia is the first person to reach this app never having talked to one.
  // She finished setup, landed on Today, and had no idea what she was meant
  // to say. Three parts, all teaching the same one thing — the ask bar is
  // the app, and the buttons are the shortcuts:
  //
  //   1. Two tappable example prompts under the ask bar, per tab, on the
  //      first three visits to that tab and then gone for good. They send
  //      through sendAskMessage, the same path the quick-action chips use,
  //      and they are .ask-chip like every other chip here.
  //   2. One card on Today the first time the shell opens after setup — the
  //      household has a plan and has never dismissed it.
  //   3. A "Helpful tips" sheet, behind a Preferences row and a "?" beside
  //      the ask bar, for anyone who wants the whole thing back later.
  //
  // Where each piece of state lives, and why the two differ: the per-tab
  // visit counters are localStorage, per household, because they are a
  // per-device teaching aid and a lost count costs one chip. The card's
  // dismissal is on the SERVER (households.coaching_seen_at, GET/POST
  // /api/coaching) — being handed "here's how this works" again on the
  // phone after reading it on the laptop is the opposite of being coached.

  var COACH_VISITS_TO_SHOW = 3;

  // Two per tab, in the household's own words rather than in command form —
  // the point is that a sentence works, not that there is a syntax. Grocery
  // deliberately echoes ASK_HINTS.grocery: that line is grey placeholder
  // text inside the bar, and this is the tappable proof that it does what it
  // says.
  //
  // Today's second example carries a name, and it has to be one of THIS
  // household's. It shipped hardcoded as "Vineeth is out Thursday" — the
  // developer's own partner — which every beta household then read as an
  // example about their own week. `example_name` comes from /api/coaching;
  // until it answers, and for a household with nobody on record yet, the
  // name-free sentence teaches exactly the same thing.
  var COACH_EXAMPLES = {
    today: ['What’s next tonight?', null],
    week: ['Swap Thursday for something lighter', 'Less chicken, more fish this week'],
    grocery: ['Add oat milk and lemons', 'We already have rice'],
    kitchen: ['What can I make with the chicken thighs?', 'I’m short on time tonight']
  };

  // The one example built from household data rather than written down.
  function coachAwayExample() {
    var name = coachState && coachState.exampleName;
    return name ? name + ' is out Thursday' : 'One of us is out Thursday';
  }

  // COACH_EXAMPLES holds a null where that sentence goes, so the tab's two
  // chips stay one list in one place; this fills it at render time, when the
  // name is known.
  function coachExamplesFor(key) {
    var prompts = COACH_EXAMPLES[key];
    if (!prompts) return null;
    return prompts.map(function (p) { return p === null ? coachAwayExample() : p; });
  }

  var coachState = {
    ready: false,
    householdId: null,
    hasPlan: false,
    // One of this household's own adults, for Today's "someone is out"
    // example. Null until /api/coaching answers, and for a household with
    // no members yet — coachAwayExample() has a name-free sentence for both.
    exampleName: null,
    // Starts true so nothing can flash before /api/coaching answers: a card
    // that appears and vanishes is worse than one that appears a beat late.
    seen: true
  };

  function coachVisitsKey() {
    return 'pomona.coaching.visits.h' + (coachState.householdId == null ? 'x' : coachState.householdId);
  }

  // Every read and write is wrapped: Safari in private mode throws on
  // localStorage rather than returning null, and a thrown teaching aid
  // would take the tab switch down with it.
  function coachReadVisits() {
    try {
      var raw = window.localStorage.getItem(coachVisitsKey());
      var parsed = raw ? JSON.parse(raw) : null;
      return (parsed && typeof parsed === 'object') ? parsed : {};
    } catch (err) { return {}; }
  }

  function coachWriteVisits(visits) {
    try { window.localStorage.setItem(coachVisitsKey(), JSON.stringify(visits)); } catch (err) { /* see above */ }
  }

  // Which visit this is, 1-based. Stops WRITING past the limit — the
  // counter's only question is "have the three been spent", and a number
  // that keeps climbing answers nothing extra — but keeps RETURNING the
  // incremented value, so the fourth visit reads 4 and shows nothing.
  function coachCountVisit(key) {
    var visits = coachReadVisits();
    var n = (Number(visits[key]) || 0) + 1;
    if (n <= COACH_VISITS_TO_SHOW) {
      visits[key] = n;
      coachWriteVisits(visits);
    }
    return n;
  }

  function coachExampleTargets() {
    var t = [document.getElementById('ask-examples'), document.getElementById('today-ask-examples')];
    return t.filter(function (el) { return !!el; });
  }

  // Both surfaces at once, the same reason askChipTargets does it: the
  // mobile dock's row and Today's Ask column can both be in the document,
  // and resizing across 1024px must not leave the other one stale.
  function renderAskExamples(prompts) {
    coachExampleTargets().forEach(function (el) {
      // Not beside the named intents. On a phone these two never share a
      // screen — the examples are in the dock, the intents are inside the
      // sheet that covers it — but in the desktop Ask column they stack,
      // and two teaching rows in one 347px column is 348px of chips that
      // pushed the composer off a 1280x900 screen (measured: composer
      // bottom 857 before the intents landed, 961 after). They also say
      // some of the same things: COACH_EXAMPLES' "I'm short on time
      // tonight" is "Swap tonight for something quicker" in other words.
      // The intents are the permanent version of what the examples were a
      // three-visit stand-in for, so the examples yield to them — §8's
      // "every word earns its place", applied to a whole row.
      // DESKTOP COLUMN ONLY, and that scoping is the whole correctness of
      // it. The phone's chips container is filled the moment the sheet is
      // BUILT, not when it is opened, so a guard that read it at any width
      // hid the dock's examples permanently — which is this same row's
      // feature, deleted. Measured on a phone before the scoping went in:
      // examples hidden with the sheet still closed.
      var intents = el.id === 'today-ask-examples'
        ? document.getElementById('today-ask-chips')
        : null;
      if (intents && !intents.hidden && intents.innerHTML) {
        el.innerHTML = '';
        el.hidden = true;
        return;
      }
      if (!prompts || !prompts.length) {
        el.innerHTML = '';
        el.hidden = true;
        return;
      }
      el.hidden = false;
      el.innerHTML = prompts.map(function (text, i) {
        return '<button type="button" class="ask-chip ask-chip-example" data-i="' + i + '">' +
          escapeHtml(text) + '</button>';
      }).join('');
      el.querySelectorAll('.ask-chip-example').forEach(function (chip) {
        chip.addEventListener('click', function () {
          var text = prompts[Number(chip.dataset.i)];
          // The existing send path, unchanged: open whichever surface the
          // conversation lives on at this width, then send. Without the
          // open, a phone would fire a message into a hidden sheet and
          // appear to have done nothing.
          openAskSheet();
          sendAskMessage(text);
        });
      });
    });
  }

  // Called on every tab activation (and once when /api/coaching answers,
  // for whichever tab the app opened on).
  function coachOnTabShown(key) {
    // Hoisted, and called from activateTab far above this section — so on a
    // synchronous first activation `coachState` is still an uninitialised
    // `var`. Nothing to count against, and nowhere to put it.
    if (!coachState) return;
    coachState.tab = key;
    if (!coachState.ready) return;
    // Once the household has said something of their own, examples are a
    // lesson they have already passed.
    if (askConversationStarted) return renderAskExamples(null);
    var prompts = coachExamplesFor(key);
    if (!prompts) return renderAskExamples(null);
    renderAskExamples(coachCountVisit(key) <= COACH_VISITS_TO_SHOW ? prompts : null);
  }

  // ---------- the how-and-why card ----------

  var COACH_CARD_LINES = [
    'Ask for anything in plain words — a swap, a change of plan, a question about tonight.',
    'The more you tell me about your week, the better the plan fits. Away nights, guests, a craving.',
    'Buttons do the common things. Words do the rest.'
  ];

  // A .plan-nudge-card instance, not a new card type — same eyebrow / title
  // / body shape as Today's other quiet card (DESIGN_SYSTEM §9 Tier 1).
  // No apricot anywhere in it: Today's own hero owns that colour, and this
  // is a word, not an action.
  function coachCardHtml() {
    return '<div class="shell-card plan-nudge-card coach-card">' +
      '<div class="plan-nudge-eyebrow">A QUICK WORD</div>' +
      '<div class="plan-nudge-title">This is how to talk to me</div>' +
      '<ul class="coach-lines">' +
        COACH_CARD_LINES.map(function (line) {
          return '<li>' + escapeHtml(line) + '</li>';
        }).join('') +
      '</ul>' +
      '<div class="coach-actions">' +
        '<button type="button" class="plan-nudge-link coach-got-it" data-coach="got-it">Got it</button>' +
        '<button type="button" class="plan-nudge-link coach-tips" data-coach="tips">Show me tips</button>' +
      '</div>' +
    '</div>';
  }

  function renderCoachCard() {
    if (!coachState) return; // see coachOnTabShown
    var slot = document.getElementById('coach-card-slot');
    if (!slot) return;
    var show = coachState.ready && coachState.hasPlan && !coachState.seen;
    if (!show) { slot.innerHTML = ''; return; }
    if (slot.dataset.built === '1') return;
    slot.dataset.built = '1';
    slot.innerHTML = coachCardHtml();
  }

  // Both buttons dismiss it, because both mean "I've read this" — the card
  // is shown exactly once and "Show me tips" is the longer answer to the
  // same question, not a way of putting the card off.
  function coachDismissCard() {
    coachState.seen = true;
    var slot = document.getElementById('coach-card-slot');
    if (slot) slot.innerHTML = '';
    // Fire-and-forget: a failed write costs one repeated card on the next
    // load, which is not worth an error message on a screen whose whole job
    // is a warm first impression.
    try {
      fetch('/api/coaching/seen', { method: 'POST', keepalive: true })
        .catch(function () { /* see above */ });
    } catch (err) { /* see above */ }
  }

  document.addEventListener('click', function (e) {
    var target = e.target && e.target.closest && e.target.closest('[data-coach]');
    if (!target) return;
    coachDismissCard();
    if (target.getAttribute('data-coach') === 'tips') openTipsSheet();
  });

  // ---------- "Helpful tips" ----------
  //
  // One screen, no scroll on a phone if it can be helped: four groups, one
  // real example each, and the line that answers the question nobody asks
  // out loud — what actually happens when you press send.

  var TIPS_OPENING = 'Say it however it comes out. There’s no right way to phrase it.';

  var TIPS_GROUPS = [
    { tab: 'Now', example: 'What’s next tonight?', line: 'The day in front of you — what’s cooking, who’s out, what still needs doing.' },
    { tab: 'Plan', example: 'Swap Thursday for something lighter', line: 'The week’s plan — swaps, away nights, what you’re in the mood for.' },
    { tab: 'Shop', example: 'Add oat milk and lemons', line: 'The list — adding, dropping, what you already have at home.' },
    { tab: 'Cook', example: 'What can I make with the chicken thighs?', line: 'Tonight’s cooking — what’s in the house, and how long you’ve got.' }
  ];

  var TIPS_CLOSERS = [
    'The more you tell me about your week, the better the plan fits.',
    'Buttons do the common things. Words do the rest.'
  ];

  var TIPS_AFTER_SEND = 'I’ll say what changed, and the screen updates. If I couldn’t, I’ll say that too.';

  var tipsSheetEl = null;
  var tipsScrimEl = null;

  function buildTipsSheet() {
    if (tipsSheetEl) return;
    tipsScrimEl = document.createElement('div');
    tipsScrimEl.id = 'tips-scrim';
    tipsScrimEl.hidden = true;
    tipsSheetEl = document.createElement('div');
    tipsSheetEl.id = 'tips-sheet';
    tipsSheetEl.hidden = true;
    tipsSheetEl.setAttribute('role', 'dialog');
    tipsSheetEl.setAttribute('aria-modal', 'true');
    tipsSheetEl.setAttribute('aria-labelledby', 'tips-title');
    tipsSheetEl.innerHTML =
      '<div class="ask-sheet-handle" id="tips-handle"></div>' +
      '<div class="kit-sheet-titlerow">' +
        '<span class="kit-sheet-title" id="tips-title">Helpful tips</span>' +
        '<span class="kit-sheet-hairline"></span>' +
        '<button type="button" class="kit-sheet-close" id="tips-close" aria-label="Close">&times;</button>' +
      '</div>' +
      '<p class="prefs-sub">' + escapeHtml(TIPS_OPENING) + '</p>' +
      '<div class="tips-body">' +
        TIPS_GROUPS.map(function (g) {
          return '<div class="tips-group">' +
            '<div class="tips-group-tab">' + escapeHtml(g.tab) + '</div>' +
            '<div class="tips-group-line">' + escapeHtml(g.line) + '</div>' +
            '<div class="tips-group-example">&ldquo;' + escapeHtml(g.example) + '&rdquo;</div>' +
          '</div>';
        }).join('') +
        '<ul class="tips-closers">' +
          TIPS_CLOSERS.map(function (line) { return '<li>' + escapeHtml(line) + '</li>'; }).join('') +
        '</ul>' +
        '<div class="tips-after">' +
          '<div class="tips-after-label">AFTER YOU SEND</div>' +
          '<div class="tips-after-line">' + escapeHtml(TIPS_AFTER_SEND) + '</div>' +
        '</div>' +
      '</div>';
    // Body level, like every other sheet here: position:fixed has to sit
    // outside the tab panel's stacking and scroll context.
    document.body.appendChild(tipsScrimEl);
    document.body.appendChild(tipsSheetEl);
    tipsScrimEl.addEventListener('click', closeTipsSheet);
    tipsSheetEl.querySelector('#tips-handle').addEventListener('click', closeTipsSheet);
    tipsSheetEl.querySelector('#tips-close').addEventListener('click', closeTipsSheet);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && tipsSheetEl && !tipsSheetEl.hidden) closeTipsSheet();
    });
  }

  function openTipsSheet() {
    buildTipsSheet();
    // One sheet at a time, the rule every other sheet here follows.
    closeAskSheet();
    closeWeekSheet();
    closeKitchenSheet();
    closePrefsSheet();
    closeSnwSheet();
    tipsScrimEl.hidden = false;
    tipsSheetEl.hidden = false;
  }

  function closeTipsSheet() {
    if (!tipsSheetEl) return;
    tipsScrimEl.hidden = true;
    tipsSheetEl.hidden = true;
  }

  // Delegated, so the Preferences row and both "?" buttons (the mobile dock
  // and Today's Ask column, which is re-rendered whenever Today rebuilds)
  // work without anything wiring a listener.
  document.addEventListener('click', function (e) {
    var target = e.target && e.target.closest && e.target.closest('[data-tips]');
    if (target) openTipsSheet();
  });

  // ---------- boot ----------

  function loadCoachingState() {
    fetch('/api/coaching')
      .then(function (res) {
        if (res.status === 401) groForgetOffline();
        return res.ok ? res.json() : null;
      })
      .catch(function () { return null; })
      .then(function (state) {
        coachState.ready = true;
        if (state) {
          coachState.householdId = state.household_id;
          // The grocery copy and tick queue are keyed by household too, and
          // this is the one place the shell learns which one it is.
          if (groOffline && groOffline.setHousehold(state.household_id) && groceryState.offline) {
            // The list on screen was a copy under a different (or no)
            // household than the one just confirmed. It is not this
            // household's to see: drop it and ask the server.
            groceryState.data = null;
            groceryState.offline = false;
            renderGrocery();
            loadGrocery();
          }
          coachState.hasPlan = !!state.has_plan;
          coachState.seen = !!state.coaching_seen_at;
          coachState.exampleName = state.example_name || null;
        }
        // Whatever tab the app opened on never got counted, because the
        // household wasn't known yet.
        coachOnTabShown(coachState.tab || currentTabKey());
        renderCoachCard();
      });
  }

  loadCoachingState();

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
