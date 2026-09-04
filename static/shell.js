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
    calendarDay:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M8 3v4M16 3v4M3 10h18"/><circle cx="12" cy="15" r="2.2" fill="currentColor" stroke="none"/></svg>',
    calendarWeek:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01"/></svg>',
    cart:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="20" r="1.4" fill="currentColor" stroke="none"/><circle cx="18" cy="20" r="1.4" fill="currentColor" stroke="none"/><path d="M2.5 3h2.4l2.1 11.4a2 2 0 0 0 2 1.6h8.2a2 2 0 0 0 2-1.6L21 7.5H6.2"/></svg>',
    pot:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 11h16v4a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5v-4Z"/><path d="M2 11h20M8 3.5c0 1-1 1-1 2s1 1.5 1 2M13 3.5c0 1-1 1-1 2s1 1.5 1 2"/></svg>',
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
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h13"/><path d="M12.5 6l6 6-6 6"/></svg>'
  };

  var TABS = [
    { key: 'today', path: '/', label: 'Today', railLabel: 'Today', icon: ICONS.calendarDay, real: true },
    { key: 'week', path: '/week', label: 'Meals', railLabel: 'Meals', icon: ICONS.calendarWeek, week: true },
    // Stage 2 slice 2: Grocery is a real shell screen now, not an embedded
    // page. static/grocery.html still exists and still works standalone, but
    // nothing links to it — it is the fallback, the same way
    // static/grocery-legacy.html already was.
    { key: 'grocery', path: '/grocery', label: 'Grocery', railLabel: 'Grocery', icon: ICONS.cart, grocery: true },
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
  var KITCHEN_SHEETS = {
    memory: { title: 'What we know', src: '/static/memory.html', hash: 'people' },
    inventory: { title: 'Inventory', src: '/static/inventory.html' },
    stores: { title: 'Stores', src: '/static/memory.html', hash: 'stores' }
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

    Object.keys(panels).forEach(function (k) {
      panels[k].classList.toggle('active', k === key);
    });
    document.querySelectorAll('.tab-btn').forEach(function (el) {
      el.classList.toggle('active', el.dataset.tab === key);
    });
    document.querySelectorAll('.rail-row').forEach(function (el) {
      el.classList.toggle('active', el.dataset.tab === key);
    });

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
    // cooker.html and so lit the wrong tab while you cooked.
    if (tab.week && opts && opts.mealsView) setMealsView(opts.mealsView);

    if (pushHistory && window.location.pathname.replace(/\/+$/, '') !== (tab.path === '/' ? '/' : tab.path.replace(/\/+$/, ''))) {
      window.history.pushState({ tab: key }, '', tab.path);
    }
  }

  window.addEventListener('popstate', function () { activateTab(currentTabKey(), false); });

  // ---------- Today ----------
  // README §4/§7: heading, needs-you band, tonight's dinner, chores,
  // grocery summary — same cards on every breakpoint, just rearranged.
  // Mobile stacks them in DOM order (needs-you, dinner, chores+grocery).
  // Desktop (Step 6, §7) lays the same DOM out as a CSS grid: the dinner
  // card spans the full width on its own row, then a 1.5fr/1fr/1fr row of
  // needs-you / chores+grocery / Ask — no JS-side breakpoint branching,
  // `.today-body`'s grid-template-areas (shell.css) does the rearranging.
  // The Ask column only exists (is only ever shown) at >=1024px — see
  // "Ask sheet vs. Ask column" below for how the same conversation renders
  // into both surfaces depending on which one exists at the moment.
  //
  // Judgment call: the spec's 3-column desktop row only names needs-you /
  // chores / Ask — no mention of the grocery-summary card. Dropping it
  // outright would lose real functionality with nothing to replace it, so
  // it's kept stacked below Chores in that same middle column instead.
  async function buildTodayPanel(panel) {
    panel.innerHTML =
      '<div class="today-content">' +
        // Pomona: the greeting, with the date as an eyebrow that runs into a
        // hairline (InnToday). The needs-you count kept its element and its
        // id — it is just no longer the H1, because the screen's H1 is now
        // the greeting and the blueprint allows exactly one hero, which is
        // the dinner panel below.
        '<div class="today-heading">' +
          '<div class="today-datestrip">' +
            '<span class="today-date" id="today-date"></span>' +
            '<span class="today-hairline"></span>' +
          '</div>' +
          '<h1 class="today-greeting" id="today-greeting"></h1>' +
          '<div class="today-status" id="today-h1">You&rsquo;re clear</div>' +
        '</div>' +
        // The offer to plan a week. Outside .today-body, not inside it:
        // .today-body is a named-area grid on desktop, and an area whose
        // only child is display:none still leaves its row's gap behind. As
        // a child of the flex column instead, it disappears completely when
        // there's no offer to make. Above everything because it is an
        // offer, not a demand — it can be read and ignored, rather than
        // mixed in with things that genuinely need a decision today.
        '<div id="plan-week-nudge" class="today-area-nudge"></div>' +
        // The hero, and the only one on this screen. A direct child of
        // .today-content rather than of .today-body, because it bleeds the
        // full width of the panel while everything else sits inside the
        // 20px gutter — a grid child cannot escape its parent's padding.
        '<div id="today-dinner-card" class="dinner-hero today-area-dinner" hidden></div>' +
        '<div class="today-body">' +
          // "Break the uniform card stack": a two-up pair of small tiles,
          // then the wider attention cards, then the quiet chores list.
          // auto-fit means the pair collapses to one full-width tile when
          // there is no prep task, rather than leaving a lonely half-tile.
          '<div class="today-tiles today-area-tiles">' +
            '<div class="today-tile tile-prep" id="today-prep-tile" hidden></div>' +
            '<div class="today-tile tile-defrost" id="today-defrost-tile" hidden></div>' +
            '<button type="button" class="today-tile tile-grocery" id="grocery-summary-open">' +
              '<span class="tile-icon">' + ICONS.bag + '</span>' +
              '<span class="tile-eyebrow">Grocery run</span>' +
              '<span class="tile-body" id="grocery-summary-sub">Loading&hellip;</span>' +
            '</button>' +
          '</div>' +
          '<div id="needs-you-band" class="today-area-needsyou"></div>' +
          '<div class="today-area-chores">' +
            '<div class="shell-card chores-card">' +
              '<div class="chores-header"><h2>Your chores</h2><span class="chores-count" id="chores-count"></span></div>' +
              '<div id="chores-list"></div>' +
            '</div>' +
          '</div>' +
          '<div class="today-area-ask shell-card ask-column" id="today-ask-column">' +
            '<div class="ask-messages" id="today-ask-messages"></div>' +
            '<div class="ask-chips" id="today-ask-chips"></div>' +
            '<form id="today-ask-composer" class="ask-composer-bar">' +
              '<input id="today-ask-input" class="ask-composer-input" type="text" placeholder="The more you tell me, the less you&rsquo;ll swap&hellip;" autocomplete="off" />' +
              '<button type="button" id="today-ask-mic-btn" class="ask-composer-mic" aria-label="Dictate message" title="Dictate message">' +
                '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z"/><path d="M19 11a1 1 0 1 0-2 0 5 5 0 0 1-10 0 1 1 0 1 0-2 0 7 7 0 0 0 6 6.93V21H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-3.07A7 7 0 0 0 19 11z"/></svg>' +
              '</button>' +
              '<button type="submit" id="today-ask-send-btn" class="ask-composer-send" aria-label="Send">&uarr;</button>' +
            '</form>' +
          '</div>' +
        '</div>' +
      '</div>';

    panel.querySelector('#today-date').textContent = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' }).toUpperCase();
    panel.querySelector('#today-greeting').textContent = greetingForNow();

    panel.querySelector('#grocery-summary-open').addEventListener('click', function () { activateTab('grocery', true); });

    setupAskColumn(panel);

    await Promise.all([
      loadPlanWeekNudge(panel),
      loadNeedsYou(panel),
      loadTonightsDinner(panel),
      loadDefrostToday(panel),
      loadChores(panel),
      loadGrocerySummary(panel)
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
      startPlanningWeek(nudge.week_start);
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
        startPlanningWeek(nudge.week_start);
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

  function startPlanningWeek(weekStart) {
    // A full page rather than a tab — see /plan-week in app/main.py for
    // why. Leaving the shell entirely is the point: the flow has a
    // beginning and an end, and comes back to Meals when it's done.
    window.location.href = '/plan-week?week=' + encodeURIComponent(weekStart);
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

  function greetingForNow() {
    // The mockup greets by first name ("Good evening, Emily"). This app has
    // no per-person identity — see CLAUDE.md's open item 4: approving a week
    // has to *ask* which adult is present, because nothing else knows. A
    // name here would therefore be a guess, and wrong half the time in a
    // two-adult household, so the greeting is time-of-day only and the date
    // eyebrow above it carries the specificity instead.
    var h = new Date().getHours();
    if (h < 12) return 'Good morning';
    if (h < 17) return 'Good afternoon';
    return 'Good evening';
  }

  function setTodayHeading(panel, count) {
    // Same sentence, same source, quieter place: this is the line under the
    // greeting now rather than the H1. The count still drives the tab badge.
    var line = panel.querySelector('#today-h1');
    if (line) {
      line.textContent = count === 0 ? "You're clear" : (count === 1 ? '1 thing needs you' : count + ' things need you');
      line.classList.toggle('is-clear', count === 0);
    }
    setTodayBadge(count);
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
  // from inline) and a shop run needed before an upcoming meal. At most
  // one card per rule for now (0-3 is the spec's headroom for later
  // rules). "Later" on the shop-run card is a same-device, until-tomorrow-
  // evening dismissal — there's no per-user account concept in this app to
  // hang a server-side dismissal on, so localStorage is the reasonable
  // judgment call rather than a wasted backend round-trip for something
  // this ephemeral.
  var SHOP_RUN_SNOOZE_KEY = 'hm_shop_run_snoozed_until';

  function isShopRunSnoozed() {
    try {
      var until = Number(localStorage.getItem(SHOP_RUN_SNOOZE_KEY) || 0);
      return Date.now() < until;
    } catch (e) { return false; }
  }

  function snoozeShopRunUntilTomorrowEvening() {
    try {
      var d = new Date();
      d.setDate(d.getDate() + 1);
      d.setHours(18, 0, 0, 0); // "tomorrow evening" ~6pm, a reasonable stand-in for a real per-household evening time
      localStorage.setItem(SHOP_RUN_SNOOZE_KEY, String(d.getTime()));
    } catch (e) { /* localStorage unavailable — the card just won't stay dismissed, not fatal */ }
  }

  async function loadNeedsYou(panel) {
    try {
      var res = await fetch('/api/needs-you');
      if (!res.ok) throw new Error('needs-you lookup failed');
      var data = await res.json();
      renderNeedsYou(panel, data.items || []);
    } catch (err) {
      console.warn('Needs-you lookup failed:', err);
      setTodayHeading(panel, 0);
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
    if (item.type === 'shop_run') {
      var summary = item.sample_items.slice(0, 4).join(', ') + (item.count > item.sample_items.length ? ', and more' : '');
      return (
        '<div class="shell-card needs-you-card urgency-' + item.urgency + '" data-card-type="shop_run">' +
          '<div class="ny-kicker">' + escapeHtml(item.kicker) + '</div>' +
          '<div class="ny-title">' + escapeHtml(item.title) + '</div>' +
          '<div class="ny-summary">' + escapeHtml(item.count + (item.count === 1 ? ' item' : ' items')) + (summary ? ': ' + escapeHtml(summary) : '') + '</div>' +
          '<div class="ny-actions">' +
            '<button type="button" class="btn-gold ny-shop-now">Shop now</button>' +
            '<button type="button" class="btn-sand ny-later">Later</button>' +
          '</div>' +
        '</div>'
      );
    }
    return '';
  }

  function renderNeedsYou(panel, items) {
    var visible = items.filter(function (it) { return !(it.type === 'shop_run' && isShopRunSnoozed()); });
    setTodayHeading(panel, visible.length);

    var band = panel.querySelector('#needs-you-band');
    band.innerHTML = visible.map(needsYouCardHtml).join('');

    band.querySelectorAll('[data-card-type="dinner_decision"] .ny-option').forEach(function (row) {
      row.addEventListener('click', function () {
        resolveDinnerDecision(panel, row.dataset.date, row.dataset.meal, row.closest('.needs-you-card'));
      });
    });
    band.querySelectorAll('[data-card-type="shop_run"] .ny-shop-now').forEach(function (btn) {
      btn.addEventListener('click', function () { activateTab('grocery', true); });
    });
    band.querySelectorAll('[data-card-type="shop_run"] .ny-later').forEach(function (btn) {
      btn.addEventListener('click', function () {
        snoozeShopRunUntilTomorrowEvening();
        var card = btn.closest('.needs-you-card');
        dismissNeedsYouCard(panel, card, items.filter(function (it) { return it.type !== 'shop_run'; }));
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
    if (addIngredients === null) return;  // "Never mind" — nothing planned
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
      // The dinner card and (if it's today) the Week menu both just
      // changed — refresh what's already on screen rather than requiring
      // a manual reload, same "cascades must be visible" spirit as §6.
      loadTonightsDinner(panel);
    } catch (err) {
      console.warn('Dinner resolve failed:', err);
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
  function showToast(message, action) {
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
    toastTimer = setTimeout(function () { toastEl.hidden = true; }, action ? 6000 : 2200);
  }

  // ---------- The hero: tonight's dinner (InnToday) ----------
  // Same endpoint, same fields, same two actions as before — this is the
  // Pomona presentation of them. Every chip is a real value or absent:
  //   35 min      prep_time_minutes + cook_time_minutes
  //   Serves 4    default_servings
  //   accent line the plan's own `reasoning` (the 4-9 word "why")
  // The mockup's celadon "All in the fridge" chip has no binding anywhere in
  // this app — nothing on Today checks a recipe's ingredients against the
  // kitchen — so it is not drawn. Same for its "on the table by a quarter
  // past seven": there is no serve-time field, and the reasoning line is the
  // real sentence that belongs in that Newsreader italic slot.
  async function loadTonightsDinner(panel) {
    var card = panel.querySelector('#today-dinner-card');
    try {
      var res = await fetch('/api/cooker-view');
      if (!res.ok) throw new Error('cooker-view failed');
      var data = await res.json();
      renderPrepNudge(panel, data.prep_tasks || []);
      var today = todayLocalStr();
      var meal = (data.meals || []).filter(function (m) { return m.date === today && m.slot === 'dinner'; })[0];
      if (!meal) {
        // No dinner planned/plannable for tonight (or the household is on a
        // component-based plan, which has no per-day dinner at all). The
        // real "no plan yet, decide now" affordance is the needs-you band —
        // Step 5. Still just doesn't show, deliberately: a spruce panel
        // reading "nothing planned tonight" would be a flat lie to a
        // component-based household, which has a full week and no per-day
        // dinner rows for it to be read out of.
        card.hidden = true;
        return;
      }
      var minutes = (meal.prep_time_minutes || 0) + (meal.cook_time_minutes || 0);
      var chips = '';
      if (minutes) chips += '<span class="hero-chip">' + minutes + ' min</span>';
      if (meal.default_servings) chips += '<span class="hero-chip">Serves ' + escapeHtml(meal.default_servings) + '</span>';
      card.hidden = false;
      card.innerHTML =
        '<div class="hero-top">' +
          '<span class="hero-badge">Tonight&rsquo;s dinner</span>' +
          '<span class="hero-rule"></span>' +
          '<span class="hero-icon">' + ICONS.flame + '</span>' +
        '</div>' +
        '<div class="hero-dish' + dishSizeClass(meal.meal) + '">' + escapeHtml(meal.meal || 'Dinner') + '</div>' +
        (meal.reasoning ? '<div class="hero-accent">' + escapeHtml(meal.reasoning) + '</div>' : '') +
        (chips ? '<div class="hero-chips">' + chips + '</div>' : '') +
        '<button type="button" class="hero-action" id="dinner-cook-mode">' +
          '<span>Start cooking</span>' + ICONS.arrow +
        '</button>' +
        '<button type="button" class="hero-quiet" id="dinner-swap">Swap tonight for something else</button>';
      card.querySelector('#dinner-cook-mode').addEventListener('click', function () { activateTab('week', true, { mealsView: 'cook' }); });
      card.querySelector('#dinner-swap').addEventListener('click', function () {
        openAskSheet('Swap tonight for something faster');
      });
    } catch (err) {
      console.warn('Tonight\'s dinner lookup failed:', err);
      card.hidden = true;
      renderPrepNudge(panel, []);
    }
  }

  // ---------- The prep tile (InnToday's "Before bed" card) ----------
  // The design asks for a prep nudge and the panel had none — but the data
  // is real and already in hand: /api/cooker-view returns the plan's
  // prep_tasks (tools.get_prep_schedule), which Today was fetching and
  // discarding. Read-only, exactly like the mockup's tile: no new endpoint,
  // and no check-off control invented for it (prep is ticked off in Cook
  // mode, which owns that flow). Only a task dated today and still pending
  // is shown — a task for Thursday is not a nudge on Tuesday, and a done one
  // is not a nudge at all.
  function renderPrepNudge(panel, tasks) {
    var tile = panel.querySelector('#today-prep-tile');
    if (!tile) return;
    var today = todayLocalStr();
    // Excludes task_type === 'defrost': that kind gets its own dedicated,
    // interactive tile (renderDefrostToday/#today-defrost-tile) right next
    // to this one — without this filter the same reminder showed up
    // twice, once read-only here and once actionable there.
    var task = (tasks || []).filter(function (t) {
      return t.task_date === today && t.status !== 'done' && t.task_type !== 'defrost';
    })[0];
    if (!task) { tile.hidden = true; tile.innerHTML = ''; return; }
    tile.hidden = false;
    tile.innerHTML =
      '<span class="tile-icon">' + ICONS.clock + '</span>' +
      '<span class="tile-eyebrow">Prep today</span>' +
      '<span class="tile-body">' + escapeHtml(task.description || '') + '</span>' +
      (task.related_meal ? '<span class="tile-foot">for ' + escapeHtml(task.related_meal) + '</span>' : '');
  }

  // ---------- The defrost tile ----------
  // Unlike the read-only prep tile just above, this one is interactive —
  // Loop Board "First-class 'defrost' prep step" specifically asks for a
  // one-tap done/skip right here, because a defrost decision made days
  // before cooking has no natural moment inside Cook mode (which owns
  // check-off for everything else prep-related) to happen in. Backed by
  // /api/prep/defrost-today (app/tools/defrost.get_defrost_today) — pending
  // task_type='defrost' prep_tasks due today. Marking one done/skipped
  // reuses the existing /api/cooker/check-prep endpoint, same table as
  // Cook mode's own prep check-off, just called from here instead.
  async function loadDefrostToday(panel) {
    try {
      var res = await fetch('/api/prep/defrost-today');
      if (!res.ok) throw new Error('defrost lookup failed');
      var data = await res.json();
      panel._defrostTasks = data.tasks || [];
    } catch (err) {
      console.warn('Defrost-today lookup failed:', err);
      panel._defrostTasks = [];
    }
    renderDefrostToday(panel);
  }

  function renderDefrostToday(panel) {
    var tile = panel.querySelector('#today-defrost-tile');
    if (!tile) return;
    var tasks = panel._defrostTasks || [];
    if (!tasks.length) { tile.hidden = true; tile.innerHTML = ''; return; }
    var task = tasks[0];
    var more = tasks.length - 1;
    tile.hidden = false;
    tile.innerHTML =
      '<span class="tile-icon">' + ICONS.clock + '</span>' +
      '<span class="tile-eyebrow">Defrost tonight</span>' +
      '<span class="tile-body">' + escapeHtml(task.description || '') + '</span>' +
      (more > 0 ? '<span class="tile-foot">+' + more + ' more today</span>' : '') +
      '<div class="tile-defrost-actions">' +
        '<button type="button" class="tile-defrost-btn tile-defrost-done" aria-label="Done — moved to the fridge">' +
          '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>' +
          '<span>Done</span>' +
        '</button>' +
        '<button type="button" class="tile-defrost-btn tile-defrost-skip" aria-label="Skip this reminder">' +
          '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6L6 18"/></svg>' +
          '<span>Skip</span>' +
        '</button>' +
      '</div>';
    tile.querySelector('.tile-defrost-done').addEventListener('click', function (e) {
      e.stopPropagation();
      actOnDefrostTask(panel, task, 'done');
    });
    tile.querySelector('.tile-defrost-skip').addEventListener('click', function (e) {
      e.stopPropagation();
      actOnDefrostTask(panel, task, 'skipped');
    });
  }

  async function actOnDefrostTask(panel, task, status) {
    var tasks = panel._defrostTasks || [];
    var idx = tasks.indexOf(task);
    // Optimistic, same shape as toggleChore below: remove from the local
    // list and re-render immediately (a resolved task no longer belongs
    // in "pending, due today"), roll back on failure.
    if (idx > -1) tasks.splice(idx, 1);
    renderDefrostToday(panel);
    try {
      var res = await fetch('/api/cooker/check-prep', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prep_task_id: task.id, status: status })
      });
      if (!res.ok) throw new Error('defrost status update failed');
      showToast(status === 'done' ? 'Moved to the fridge — nice.' : 'Skipped for today.');
    } catch (err) {
      console.warn('Defrost action failed, rolling back:', err);
      if (idx > -1) { tasks.splice(idx, 0, task); } else { tasks.push(task); }
      renderDefrostToday(panel);
      alert('Could not save that right now — try again in a moment.');
    }
  }

  async function loadChores(panel) {
    var listEl = panel.querySelector('#chores-list');
    var countEl = panel.querySelector('#chores-count');
    try {
      var res = await fetch('/api/chores/today');
      if (!res.ok) throw new Error('chores lookup failed');
      var data = await res.json();
      renderChores(panel, data.chores || []);
    } catch (err) {
      console.warn('Chores lookup failed:', err);
      listEl.innerHTML = '<div class="empty-row">Couldn\'t load chores right now.</div>';
      countEl.textContent = '';
    }
  }

  function renderChores(panel, chores) {
    var listEl = panel.querySelector('#chores-list');
    var countEl = panel.querySelector('#chores-count');
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

  async function loadGrocerySummary(panel) {
    var sub = panel.querySelector('#grocery-summary-sub');
    try {
      var res = await fetch('/api/grocery-list?status=needed');
      if (!res.ok) throw new Error('grocery-list failed');
      var data = await res.json();
      var count = (data.sections || []).reduce(function (n, s) { return n + s.items.length; }, 0);
      // README's mock subtitle includes a "needed before Thursday" clause —
      // there's no due-date field on grocery items in this schema, so that
      // part is left out rather than invented; the count itself is real.
      sub.textContent = count === 0 ? 'All picked up' : (count + (count === 1 ? ' item to get' : ' items to get'));
    } catch (err) {
      console.warn('Grocery summary lookup failed:', err);
      sub.textContent = 'Couldn\'t load the grocery list right now.';
    }
  }

  // ==========================================================================
  // Grocery (Stage 2 slice 2) — a native shell screen, was static/grocery.html
  // ==========================================================================
  // The whole errand, start to finish, as ONE screen with four states:
  //
  //   buy    "To buy"      store cards, aisle spines, the Done row
  //   plan   "Plan stops"  triage the unsorted, then the per-store buckets
  //   review "Review"      what looks wrong before you leave the house
  //   shop   a store       the in-cart pass through one shop's aisles
  //
  // The first three are the segmented control; `shop` is entered from the
  // hero or from a bucket and takes the hero over rather than opening a page,
  // per the layout blueprint's rule that a screen never grows its own chrome.
  //
  // This is a pure frontend migration: same /api/grocery-list* endpoints, same
  // request bodies, same semantics as the page it replaces. static/grocery.html
  // is left on disk, untouched and unlinked, as the fallback — same treatment
  // grocery-legacy.html already got.
  //
  // What being native buys, and the reason the ticket exists: a chat turn that
  // changes the list now re-renders THIS panel (see refreshGroceryPanel, wired
  // into refreshStaleTabsFromActions / refreshGrocerySurfaces /
  // refreshAfterReset). The old iframe could only be refreshed by throwing the
  // entire screen away — mid-shop, scrolled deep into a long list.

  var GRO_CATEGORIES = ['produce', 'dairy', 'meat/seafood', 'pantry', 'frozen', 'other'];
  var GRO_CATEGORY_LABELS = {
    produce: 'Produce', dairy: 'Dairy', 'meat/seafood': 'Meat / seafood',
    pantry: 'Pantry', frozen: 'Frozen', other: 'Other'
  };
  // Aisle spine colours. These are `var()` references, not literals: a custom
  // property DOES cascade into an inline style attribute, so emitting
  // `style="background: var(--apricot)"` resolves per theme exactly like a
  // stylesheet rule would. (The earlier note here claimed otherwise; it was
  // wrong, and it was the reason these were frozen at light-mode literals.)
  // Getting them onto tokens is what makes the spines follow dark mode —
  // #4F6B5B and #B23A22 on the dark ground were 1.5:1 and 2.8:1.
  var GRO_AISLE_COLORS = {
    produce: 'var(--celadon)', dairy: 'var(--apricot)',
    'meat/seafood': 'var(--urgent)', pantry: 'var(--celadon-label)',
    frozen: 'var(--urgent)', other: 'var(--ink-inactive)'
  };
  // Store identity colours. Every entry is a LIGHT accent, because the avatar
  // carries spruce ink (--on-accent-ink) and RULE ONE has no exceptions — the
  // set this replaces included spruce and #7E7360, which put dark ink on a
  // dark fill and failed contrast outright.
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
    dots: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5.5" r="0.6"/><circle cx="12" cy="12" r="0.6"/><circle cx="12" cy="18.5" r="0.6"/></svg>',
    basket: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 9.5h14V19a1.8 1.8 0 0 1-1.8 1.8H6.8A1.8 1.8 0 0 1 5 19z"/><path d="M3.5 5.5h17v4h-17z"/><path d="M12 9.5v11"/></svg>'
  };

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
  function groCategoryOptions(current) {
    return GRO_CATEGORIES.map(function (c) {
      return '<option value="' + c + '"' + (c === current ? ' selected' : '') + '>' + GRO_CATEGORY_LABELS[c] + '</option>';
    }).join('');
  }

  var groceryState = {
    screen: 'buy',          // buy | plan | shop | review
    shopStore: null,
    data: null,
    loadError: false,
    usualStores: [],        // household's saved stores, offered as triage pills
    itemStorePrefs: {},     // lowercased item name -> remembered store
    preShopFlags: [],
    preShopOpen: false,
    preShopExpanded: false,
    alreadyHaveSummary: { already_have: [], elsewhere: [] },  // Review's confirmation section
    expandedStores: {},     // store name -> bool (default true)
    doneOpen: false,
    openMenuId: null,
    planOpenId: null,
    planPageSize: 5,
    bucketExpanded: {},     // one at a time
    openFlagKey: null,
    voiceSession: null,
    voiceLog: []
  };

  var GRO_PS_CAP = 5;

  function groPanel() { return panels['grocery']; }
  function groIsBuilt() { var p = groPanel(); return !!(p && p.dataset.built); }

  // ---------- Data ----------
  // One fetch of three views, combined client-side into
  //   storeName -> { sections: [{section, items}], purchased: [], inCart: [] }
  // exactly as the page this replaces did — no new endpoints, and the three
  // statuses are the three things every state needs.
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
  function groUnsorted(data) {
    var u = data.stores['Unassigned'];
    if (!u) return [];
    return u.sections.reduce(function (acc, s) { return acc.concat(s.items); }, []);
  }
  function groStoresWithNeeded(data) {
    return Object.keys(data.stores).filter(function (n) {
      return n !== 'Unassigned' && groNeededCount(data.stores[n]) > 0;
    });
  }
  function groTotals(data) {
    var names = Object.keys(data.stores);
    var totalNeeded = 0, totalDone = 0, stopsTotal = 0, stopsFinished = 0;
    names.forEach(function (name) {
      var s = data.stores[name];
      var needed = groNeededCount(s);
      // in_cart is "found, still in the trolley" — it counts as progress on
      // the trip, the same way the store screen's own wheel counts it.
      var done = s.purchased.length + s.inCart.length;
      totalNeeded += needed;
      totalDone += done;
      if (name !== 'Unassigned' && (needed > 0 || done > 0)) {
        stopsTotal++;
        if (needed === 0) stopsFinished++;
      }
    });
    return {
      needed: totalNeeded,
      done: totalDone,
      all: totalNeeded + totalDone,
      stopsTotal: stopsTotal,
      stopsFinished: stopsFinished,
      stopsLeft: stopsTotal - stopsFinished
    };
  }

  // The hero's one italic line. Same facts the old "N to go · N stops
  // finished" sub carried, said the way the mockup says them.
  function groTripNote(t) {
    if (!t.all) return 'nothing on the list yet';
    if (!t.stopsTotal) return 'no stores picked yet';
    if (t.stopsLeft === 0) return 'every stop finished';
    if (t.stopsFinished === 0) return groPlural(t.stopsTotal, 'stop', 'stops') + ' ahead';
    return groPlural(t.stopsFinished, 'stop', 'stops') + ' down, ' + t.stopsLeft + ' to go';
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
        '<div class="gro-titlerow">' +
          '<h1 class="gro-title">Grocery</h1>' +
          '<span class="gro-hairline"></span>' +
          '<button type="button" class="gro-icon-btn" id="gro-mic-btn" data-gro="voice" ' +
            'title="Hands-free: check off, add, or ask about items by voice" ' +
            'aria-label="Hands-free voice mode">' + GRO_ICONS.mic + '</button>' +
          '<button type="button" class="gro-icon-btn" id="gro-refresh-btn" data-gro="refresh" ' +
            'title="Reload the latest list" aria-label="Reload the latest list">' + GRO_ICONS.refresh + '</button>' +
        '</div>' +
        '<div class="gro-hero" id="gro-hero"></div>' +
        '<div class="gro-seg" id="gro-seg" role="tablist"></div>' +
        '<div class="gro-voice" id="gro-voice" hidden></div>' +
        '<div class="gro-body" id="gro-body"><p class="gro-empty">Loading&hellip;</p></div>' +
        '<div class="gro-body gro-foot">' +
          '<div class="gro-add" id="gro-add" hidden>' +
            '<p class="gro-eyebrow" id="gro-add-label">Add an item</p>' +
            // Two deliberate rows rather than one that wraps: at 375px the
            // four controls cannot sit on a line, and letting them wrap put
            // the mic on its own beside a stranded Qty box.
            '<div class="gro-add-row">' +
              '<input type="text" class="gro-add-item" id="gro-add-item" placeholder="Item, e.g. ground beef" aria-label="Item to add to the list" />' +
              '<button type="button" class="gro-add-mic" id="gro-add-mic" aria-label="Dictate item" title="Dictate item">' + GRO_ICONS.mic + '</button>' +
            '</div>' +
            '<div class="gro-add-row">' +
              '<input type="text" class="gro-add-qty" id="gro-add-qty" placeholder="Qty" aria-label="Quantity" />' +
              '<button type="button" class="gro-add-btn" id="gro-add-btn" data-gro="add">Add to the list</button>' +
            '</div>' +
          '</div>' +
          '<div id="gro-confirm-slot"></div>' +
        '</div>' +
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
    });
    setupDictation(panel.querySelector('#gro-add-item'), panel.querySelector('#gro-add-mic'));

    loadGrocery();
    // Both are niceties for the triage pills — a failure leaves the pills
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
      if (groceryState.screen === 'plan') renderGrocery();
    } catch (err) { /* triage still works from what's tagged on the list */ }
  }
  async function groLoadStorePrefs() {
    try {
      var res = await fetch('/api/grocery-list/store-preferences');
      if (!res.ok) return;
      var data = await res.json();
      groceryState.itemStorePrefs = data.preferences || {};
      if (groceryState.screen === 'plan') renderGrocery();
    } catch (err) { /* "usually here" tagging is a nicety, not load-bearing */ }
  }
  function groIsUsuallyHere(itemName, store) {
    var remembered = groceryState.itemStorePrefs[(itemName || '').trim().toLowerCase()];
    return !!remembered && remembered === store;
  }

  // Learning etiquette: the first time an item gets a store (assign pill,
  // save-row's free-text field), the backend doesn't remember it yet — it
  // comes back with needs_confirmation instead (see stores.
  // set_grocery_item_store). One light tap here is the "confirm" step;
  // declining (letting the toast expire) leaves it a one-off, exactly like
  // before this feature existed. A "yes" writes the preference AND adds
  // the item to that store's typical-items list on the Kitchen sheet in
  // one call (confirm_grocery_item_store_preference).
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

  // Review's confirmation section — this week's "already have" decisions
  // plus current "Elsewhere" exclusions. Loaded alongside everything else
  // rather than only when Review is the active screen, same as
  // preShopFlags, so the count is already right the moment you switch tabs.
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

  // ---------- Render ----------
  function renderGrocery() {
    var panel = groPanel();
    if (!panel) return;
    var hero = panel.querySelector('#gro-hero');
    var seg = panel.querySelector('#gro-seg');
    var body = panel.querySelector('#gro-body');
    if (!hero || !seg || !body) return;

    // Re-rendering replaces the list under the reader's thumb, so hold the
    // scroll position across it. "Nothing else moves, ever."
    var keepScroll = scrollEl ? scrollEl.scrollTop : 0;

    if (groceryState.loadError || !groceryState.data) {
      hero.innerHTML = '';
      seg.innerHTML = '';
      body.innerHTML = groceryState.loadError
        ? '<p class="gro-error">Couldn\'t load the grocery list right now — try the refresh button above.</p>'
        : '<p class="gro-empty">Loading&hellip;</p>';
      panel.querySelector('#gro-add').hidden = true;
      panel.querySelector('#gro-confirm-slot').innerHTML = '';
      return;
    }

    var data = groceryState.data;
    var screen = groceryState.screen;

    hero.innerHTML = screen === 'shop' ? groShopHeroHtml(data) : groTripHeroHtml(data);

    // The segmented control is the three real tabs. Shopping a store is a
    // state of this same screen, not a fourth tab — while it is on, the
    // control goes away rather than lying about where you are.
    if (screen === 'shop') {
      seg.hidden = true;
      seg.innerHTML = '';
    } else {
      seg.hidden = false;
      seg.innerHTML = [
        ['buy', 'To buy'], ['plan', 'Plan stops'], ['review', 'Review']
      ].map(function (pair) {
        var active = screen === pair[0];
        return '<button type="button" class="gro-seg-btn' + (active ? ' active' : '') + '" role="tab" ' +
          'aria-selected="' + (active ? 'true' : 'false') + '" data-gro="seg" data-screen="' + pair[0] + '">' +
          pair[1] + '</button>';
      }).join('');
    }

    if (screen === 'buy') body.innerHTML = groBuyHtml(data);
    else if (screen === 'plan') body.innerHTML = groPlanHtml(data);
    else if (screen === 'shop') body.innerHTML = groShopHtml(data);
    else body.innerHTML = groReviewHtml(data);

    // The add row is persistent markup rather than part of the re-rendered
    // body, so a half-typed item survives a refresh landing underneath it.
    var addCard = panel.querySelector('#gro-add');
    addCard.hidden = !(screen === 'buy' || screen === 'review');
    panel.querySelector('#gro-add-label').textContent =
      screen === 'review' ? 'Add anything missing' : 'Add an item';

    var confirmSlot = panel.querySelector('#gro-confirm-slot');
    if (screen === 'review') {
      var allNeeded = groAllNeeded(data);
      confirmSlot.innerHTML = '<button type="button" class="gro-shop-btn" data-gro="confirm">Confirm list &middot; ' +
        groPlural(allNeeded.length, 'item', 'items') + '</button>';
    } else {
      confirmSlot.innerHTML = '';
    }

    if (scrollEl) scrollEl.scrollTop = keepScroll;
  }

  function groTripHeroHtml(data) {
    var t = groTotals(data);
    var pct = t.all ? Math.round((t.done / t.all) * 100) : 0;
    var target = groPrimaryTarget(data);
    return '' +
      '<div class="gro-hero-top">' +
        '<span class="gro-hero-chip">This week&rsquo;s trip</span>' +
        '<span class="gro-hero-rule"></span>' +
        '<span class="gro-hero-count">' + groPlural(t.all, 'item', 'items') + '</span>' +
      '</div>' +
      '<div class="gro-hero-line">' +
        '<span class="gro-hero-headline">' + (t.needed ? t.needed + ' to go' : 'All set') + '</span>' +
        '<span class="gro-hero-note">' + escapeHtml(groTripNote(t)) + '</span>' +
      '</div>' +
      '<div class="gro-hero-track"><span class="gro-hero-fill" style="width:' + pct + '%"></span></div>' +
      '<button type="button" class="gro-hero-action" data-gro="primary"' + (target.disabled ? ' disabled' : '') + '>' +
        '<span>' + escapeHtml(target.label) + '</span>' + (target.disabled ? '' : ICONS.arrow) +
      '</button>';
  }

  // The screen's one apricot action. Where it goes is read off the list
  // rather than fixed: with one store's worth of shopping left and nothing
  // waiting to be sorted there is only one place it could mean, so it goes
  // straight there; otherwise the honest next step is choosing the stops.
  function groPrimaryTarget(data) {
    var t = groTotals(data);
    if (!t.needed) return { label: 'Nothing left to buy', disabled: true, screen: null };
    var withNeeded = groStoresWithNeeded(data);
    var unsorted = groUnsorted(data);
    if (withNeeded.length === 1 && !unsorted.length) {
      return { label: 'Start shopping', disabled: false, screen: 'shop', store: withNeeded[0] };
    }
    return { label: 'Start shopping', disabled: false, screen: 'plan' };
  }

  function groShopHeroHtml(data) {
    var store = groceryState.shopStore;
    var s = store && data.stores[store];
    if (!s) {
      return '<div class="gro-hero-top"><span class="gro-hero-chip">Shopping</span><span class="gro-hero-rule"></span></div>' +
        '<div class="gro-hero-line"><span class="gro-hero-headline">Pick a store</span></div>' +
        '<button type="button" class="gro-hero-back" data-gro="back-to-plan">&larr; Plan your stops</button>';
    }
    var stops = Object.keys(data.stores).filter(function (n) {
      return n !== 'Unassigned' && (groNeededCount(data.stores[n]) > 0 || data.stores[n].inCart.length > 0 || data.stores[n].purchased.length > 0);
    });
    var idx = stops.indexOf(store);
    var left = groNeededCount(s);
    var inCart = s.inCart.length;
    var done = s.purchased.length + inCart;
    var total = left + done;
    var pct = total ? Math.round((done / total) * 100) : 0;
    return '' +
      '<div class="gro-hero-top">' +
        '<span class="gro-hero-chip">Stop ' + (idx >= 0 ? idx + 1 : 1) + ' of ' + Math.max(stops.length, 1) + '</span>' +
        '<span class="gro-hero-rule"></span>' +
        '<span class="gro-hero-count">' + inCart + ' in the trolley</span>' +
      '</div>' +
      '<div class="gro-hero-line">' +
        '<span class="gro-hero-headline">' + escapeHtml(store) + '</span>' +
        '<span class="gro-hero-note">' + (left ? escapeHtml(groPlural(left, 'thing', 'things') + ' still to find') : 'everything&rsquo;s in the trolley') + '</span>' +
      '</div>' +
      '<div class="gro-hero-track"><span class="gro-hero-fill" style="width:' + pct + '%"></span></div>' +
      '<button type="button" class="gro-hero-action" data-gro="done-here">' +
        '<span>Done here &middot; ' + inCart + '</span>' + ICONS.arrow +
      '</button>' +
      '<button type="button" class="gro-hero-back" data-gro="back-to-plan">&larr; Plan your stops</button>';
  }

  // ---------- State: To buy ----------
  function groBuyHtml(data) {
    var t = groTotals(data);
    var html = groPreShopHtml();

    var names = Object.keys(data.stores);
    var active = groOrderStores(names.filter(function (n) {
      return groNeededCount(data.stores[n]) > 0 || data.stores[n].purchased.length > 0 || data.stores[n].inCart.length > 0;
    }));

    if (!active.length) {
      return html + '<p class="gro-empty">Nothing to buy yet — add something below, or it&rsquo;ll arrive here when you plan a week.</p>';
    }
    if (t.needed === 0) {
      html += '<div class="gro-allclear">Everything&rsquo;s checked off. Nice work.</div>';
    } else {
      // Expanded stores first, so the shop you are working through is not
      // pushed below the ones you have finished with.
      var withNeeded = active.filter(function (n) { return groNeededCount(data.stores[n]) > 0; });
      withNeeded.forEach(function (name) {
        var s = data.stores[name];
        var needed = groNeededCount(s);
        var expanded = groceryState.expandedStores[name] !== false; // default open
        html += '<div class="gro-store' + (expanded ? '' : ' collapsed') + '">' +
          '<button type="button" class="gro-store-head" data-gro="toggle-store" data-store="' + escapeHtml(name) + '" aria-expanded="' + expanded + '">' +
            '<span class="gro-store-avatar" style="background:' + groStoreColor(name) + '">' + escapeHtml(groStoreInitial(name)) + '</span>' +
            '<span class="gro-store-name">' + escapeHtml(groStoreLabel(name)) + '</span>' +
            '<span class="gro-store-left">' + needed + ' left</span>' +
            '<span class="gro-chev">' + (expanded ? GRO_ICONS.chevDown : GRO_ICONS.chevRight) + '</span>' +
          '</button>';
        if (expanded) {
          s.sections.forEach(function (sec) {
            if (!sec.items.length) return;
            html += '<div class="gro-aisle">' +
              '<span class="gro-aisle-spine" style="background:' + groAisleColor(sec.section) + '"></span>' +
              '<span class="gro-eyebrow">' + escapeHtml(sec.section) + '</span>' +
            '</div>';
            sec.items.forEach(function (it) { html += groBuyRowHtml(it); });
          });
        }
        html += '</div>';
      });
    }

    var allDone = [];
    names.forEach(function (n) { data.stores[n].purchased.forEach(function (it) { allDone.push(it); }); });
    var open = groceryState.doneOpen;
    html += '<div class="gro-done' + (open ? ' open' : '') + '">' +
      '<button type="button" class="gro-done-head" data-gro="toggle-done" aria-expanded="' + open + '">' +
        '<span class="gro-done-tick">' + GRO_ICONS.tick + '</span>' +
        '<span class="gro-done-label">Done &middot; ' + allDone.length + '</span>' +
        '<span class="gro-chev">' + (open ? GRO_ICONS.chevDown : GRO_ICONS.chevRight) + '</span>' +
      '</button>' +
      (open
        ? '<div class="gro-done-body">' + (allDone.length
            ? allDone.map(groDoneRowHtml).join('')
            : '<p class="gro-empty">Nothing checked off yet.</p>') + '</div>'
        : '') +
    '</div>';
    return html;
  }

  function groBuyRowHtml(it) {
    var id = String(it.id);
    var menuOpen = groceryState.openMenuId === id;
    return '<div class="gro-row" data-gro="check" data-id="' + id + '">' +
        '<button type="button" class="gro-box" role="checkbox" aria-checked="false" data-gro="check" data-id="' + id + '" ' +
          'aria-label="Got ' + escapeHtml(it.item) + '"></button>' +
        '<p class="gro-name">' + escapeHtml(it.item) + '</p>' +
        (it.quantity ? '<span class="gro-qty">' + escapeHtml(it.quantity) + '</span>' : '') +
        '<button type="button" class="gro-more" data-gro="toggle-menu" data-id="' + id + '" ' +
          'aria-expanded="' + menuOpen + '" aria-label="More options for ' + escapeHtml(it.item) + '">' + GRO_ICONS.dots + '</button>' +
      '</div>' +
      '<div class="gro-menu' + (menuOpen ? ' open' : '') + '" data-menu-for="' + id + '">' +
        '<input type="text" class="gro-m-qty" value="' + escapeHtml(it.quantity || '') + '" placeholder="Quantity" aria-label="Quantity of ' + escapeHtml(it.item) + '" />' +
        '<select class="gro-m-cat" aria-label="Section for ' + escapeHtml(it.item) + '">' + groCategoryOptions(it.category) + '</select>' +
        '<input type="text" class="gro-m-store" value="' + escapeHtml(it.store || '') + '" placeholder="Store" aria-label="Store for ' + escapeHtml(it.item) + '" />' +
        '<button type="button" class="gro-m-save" data-gro="save-row" data-id="' + id + '">Save</button>' +
        '<button type="button" class="gro-m-have" data-gro="have" data-id="' + id + '">Have it</button>' +
        '<button type="button" class="gro-m-else" data-gro="exclude" data-id="' + id + '">Elsewhere</button>' +
        '<button type="button" class="gro-m-remove" data-gro="remove" data-id="' + id + '">Remove</button>' +
      '</div>';
  }

  function groDoneRowHtml(it) {
    var id = String(it.id);
    return '<div class="gro-row done" data-gro="uncheck" data-id="' + id + '">' +
      '<button type="button" class="gro-box checked" role="checkbox" aria-checked="true" data-gro="uncheck" data-id="' + id + '" ' +
        'aria-label="Put ' + escapeHtml(it.item) + ' back on the list">' + GRO_ICONS.tick + '</button>' +
      '<p class="gro-name">' + escapeHtml(it.item) + '</p>' +
      (it.quantity ? '<span class="gro-qty">' + escapeHtml(it.quantity) + '</span>' : '') +
    '</div>';
  }

  // ---------- "Maybe already home" ----------
  // The kitchen may already have some of this. The mockup makes it a compact
  // celadon banner with a "Check" — so it starts folded and opens in place,
  // rather than sitting on top of the list as a queue of decisions. Same
  // flags, same keep/drop per item, same Keep all, same undo.
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

  // ---------- State: Plan your stops ----------
  function groPlanHtml(data) {
    var unsorted = groUnsorted(data);
    var buckets = groStoresWithNeeded(data);
    if (!unsorted.length && !buckets.length) {
      return '<p class="gro-empty">Nothing on the list yet — add items from the To buy tab.</p>';
    }

    var html = '';
    if (unsorted.length) {
      if (groceryState.planOpenId == null) groceryState.planOpenId = String(unsorted[0].id);
      var shown = unsorted.slice(0, groceryState.planPageSize);
      var remaining = unsorted.length - shown.length;
      // Triage pills: every store already on the list, plus the household's
      // usual stores — so a store can be chosen before anything is tagged to
      // it — plus "Any" for something it genuinely does not matter where.
      var pillStores = [];
      Object.keys(data.stores).forEach(function (n) { if (n !== 'Unassigned' && pillStores.indexOf(n) === -1) pillStores.push(n); });
      groceryState.usualStores.forEach(function (n) { if (n && pillStores.indexOf(n) === -1) pillStores.push(n); });

      html += '<p class="gro-eyebrow">To sort &middot; ' + unsorted.length + ' &middot; tap to assign</p>' +
        '<div class="gro-sort">';
      shown.forEach(function (it) {
        var id = String(it.id);
        var isOpen = groceryState.planOpenId === id;
        html += '<div class="gro-sort-row">' +
          '<button type="button" class="gro-sort-head" data-gro="sort-toggle" data-id="' + id + '" aria-expanded="' + isOpen + '">' +
            '<span class="gro-sort-name">' + escapeHtml(it.item) + '</span>' +
            '<span class="gro-sort-qty">' + escapeHtml(it.quantity || '') + '</span>' +
            '<span class="gro-chev">' + (isOpen ? GRO_ICONS.chevDown : GRO_ICONS.chevRight) + '</span>' +
          '</button>' +
          '<div class="gro-pills' + (isOpen ? ' open' : '') + '">' +
            pillStores.map(function (n) {
              return '<button type="button" class="gro-pill" data-gro="assign" data-id="' + id + '" data-store="' + escapeHtml(n) + '" ' +
                'aria-label="Buy ' + escapeHtml(it.item) + ' at ' + escapeHtml(n) + '">' + escapeHtml(n) + '</button>';
            }).join('') +
            '<button type="button" class="gro-pill" data-gro="assign" data-id="' + id + '" data-store="" ' +
              'aria-label="No particular store for ' + escapeHtml(it.item) + '">Any</button>' +
            // Secondary action, same backend path as the To buy ⋯ menu's
            // "Have it" — a store pill sorts the item, this takes it off
            // the list entirely because it turns out no store is needed.
            '<button type="button" class="gro-pill gro-pill-have" data-gro="already-have" data-id="' + id + '" ' +
              'aria-label="Already have ' + escapeHtml(it.item) + '">Have it</button>' +
          '</div>' +
        '</div>';
      });
      html += '</div>';
      if (remaining > 0) {
        html += '<button type="button" class="gro-sort-more" data-gro="sort-more">+' + remaining + ' more to sort</button>';
      }
    }

    buckets.forEach(function (name) {
      var s = data.stores[name];
      var items = s.sections.reduce(function (acc, sec) { return acc.concat(sec.items); }, []);
      var aisles = s.sections.filter(function (sec) { return sec.items.length; }).length;
      var expanded = !!groceryState.bucketExpanded[name];
      html += '<div class="gro-store' + (expanded ? '' : ' collapsed') + '">' +
        '<button type="button" class="gro-store-head" data-gro="toggle-bucket" data-store="' + escapeHtml(name) + '" aria-expanded="' + expanded + '">' +
          '<span class="gro-store-avatar" style="background:' + groStoreColor(name) + '">' + escapeHtml(groStoreInitial(name)) + '</span>' +
          '<span class="gro-store-name">' + escapeHtml(name) + '</span>' +
          (expanded
            ? '<span class="gro-store-left">' + items.length + ' left</span>'
            : '<span class="gro-bucket-count">' + groPlural(items.length, 'item', 'items') + ' &middot; ' + groPlural(aisles, 'aisle', 'aisles') + '</span>') +
          '<span class="gro-chev">' + (expanded ? GRO_ICONS.chevDown : GRO_ICONS.chevRight) + '</span>' +
        '</button>';
      if (expanded) {
        s.sections.forEach(function (sec) {
          if (!sec.items.length) return;
          html += '<div class="gro-aisle">' +
            '<span class="gro-aisle-spine" style="background:' + groAisleColor(sec.section) + '"></span>' +
            '<span class="gro-eyebrow">' + escapeHtml(sec.section) + '</span>' +
            '<span class="gro-aisle-count">' + sec.items.length + ' left</span>' +
          '</div>';
          sec.items.forEach(function (it) {
            var id = String(it.id);
            var usually = groIsUsuallyHere(it.item, name);
            html += '<div class="gro-bucket-row">' +
              '<span class="gro-bucket-name">' + escapeHtml(it.item) + '</span>' +
              (usually ? '<span class="gro-usually" title="Auto-assigned from what you usually get here">usually here</span>' : '') +
              (it.quantity ? '<span class="gro-qty">' + escapeHtml(it.quantity) + '</span>' : '') +
              (usually ? '<button type="button" class="gro-linkbtn" data-gro="not-this-time" data-id="' + id + '">not this time</button>' : '') +
              '<button type="button" class="gro-linkbtn gro-linkbtn-have" data-gro="already-have" data-id="' + id + '">have it</button>' +
              '<button type="button" class="gro-linkbtn" data-gro="move" data-id="' + id + '">move</button>' +
            '</div>';
          });
        });
        html += '<button type="button" class="gro-shop-btn" data-gro="shop-store" data-store="' + escapeHtml(name) + '">Shop this store</button>';
      }
      html += '</div>';
    });

    return html || '<p class="gro-empty">Nothing left to sort — nice work.</p>';
  }

  // ---------- State: Shopping a store ----------
  function groShopHtml(data) {
    var store = groceryState.shopStore;
    var s = store && data.stores[store];
    if (!s) return '<p class="gro-empty">Pick a store from Plan your stops first.</p>';

    var inCartBySection = {};
    s.inCart.forEach(function (it) {
      var cat = it.category || 'other';
      (inCartBySection[cat] = inCartBySection[cat] || []).push(it);
    });

    var html = '';
    var seen = {};
    s.sections.forEach(function (sec) {
      var found = inCartBySection[sec.section] || [];
      if (!sec.items.length && !found.length) return;
      seen[sec.section] = true;
      html += '<div class="gro-store">' +
        '<div class="gro-aisle" style="border-top:none">' +
          '<span class="gro-aisle-spine" style="background:' + groAisleColor(sec.section) + '"></span>' +
          '<span class="gro-eyebrow">' + escapeHtml(sec.section) + '</span>' +
          '<span class="gro-aisle-count">' + sec.items.length + ' left</span>' +
        '</div>' +
        sec.items.map(function (it) { return groShopRowHtml(it, false); }).join('') +
        found.map(function (it) { return groShopRowHtml(it, true); }).join('') +
      '</div>';
    });
    // An aisle whose every item is already in the trolley has no `needed`
    // section left to hang off, so it is rendered from the in-cart side —
    // otherwise finishing an aisle would make it vanish mid-shop.
    Object.keys(inCartBySection).forEach(function (cat) {
      if (seen[cat]) return;
      html += '<div class="gro-store">' +
        '<div class="gro-aisle" style="border-top:none">' +
          '<span class="gro-aisle-spine" style="background:' + groAisleColor(cat) + '"></span>' +
          '<span class="gro-eyebrow">' + escapeHtml(cat) + '</span>' +
          '<span class="gro-aisle-count">all ' + inCartBySection[cat].length + ' &check;</span>' +
        '</div>' +
        inCartBySection[cat].map(function (it) { return groShopRowHtml(it, true); }).join('') +
      '</div>';
    });

    return html || '<p class="gro-empty">Nothing left here — tap &ldquo;Done here&rdquo; to finish this stop.</p>';
  }

  function groShopRowHtml(it, inCart) {
    var id = String(it.id);
    return '<div class="gro-row' + (inCart ? ' done' : '') + '" data-gro="shop-toggle" data-id="' + id + '" data-incart="' + (inCart ? '1' : '0') + '">' +
      '<button type="button" class="gro-box' + (inCart ? ' checked' : '') + '" role="checkbox" aria-checked="' + (inCart ? 'true' : 'false') + '" ' +
        'data-gro="shop-toggle" data-id="' + id + '" data-incart="' + (inCart ? '1' : '0') + '" ' +
        'aria-label="' + (inCart ? 'Put ' + escapeHtml(it.item) + ' back' : 'Found ' + escapeHtml(it.item)) + '">' + (inCart ? GRO_ICONS.tick : '') + '</button>' +
      '<p class="gro-name">' + escapeHtml(it.item) + '</p>' +
      (it.quantity ? '<span class="gro-qty">' + escapeHtml(it.quantity) + '</span>' : '') +
    '</div>';
  }

  // ---------- State: Review ----------
  function groAllNeeded(data) {
    var out = [];
    Object.keys(data.stores).forEach(function (name) {
      data.stores[name].sections.forEach(function (sec) {
        sec.items.forEach(function (it) { out.push(it); });
      });
    });
    return out;
  }

  // This week's "already have" decisions (Have it/Already have + pre-shop
  // drops) and current "Elsewhere" exclusions — a confirmation, not a
  // warning, so it shares the flagCard shell but always celadon ("settled,
  // handled, already true" per the design system), never urgent/apricot.
  // Independent of allNeeded, so it still shows on an otherwise-empty list.
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

  function groReviewHtml(data) {
    var allNeeded = groAllNeeded(data);
    var missingQty = allNeeded.filter(function (it) { return !(it.quantity || '').trim(); });
    var noStore = allNeeded.filter(function (it) { return !(it.store || '').trim(); });
    var groups = {};
    allNeeded.forEach(function (it) {
      var key = it.item.trim().toLowerCase();
      (groups[key] = groups[key] || []).push(it);
    });
    var duplicates = Object.keys(groups).map(function (k) { return groups[k]; }).filter(function (g) { return g.length > 1; });

    // The forward link into Plan stops. Confirm returns to To buy, so without
    // this the only way onward was noticing the segmented control up top.
    var html = '<button type="button" class="gro-cta" data-gro="goto-plan">' +
      '<span>Ready to shop? Plan your stops by store next.</span>' + ICONS.arrow + '</button>';

    // Independent of the needed-list flags below — shows even when
    // everything is sorted and there's nothing left to flag.
    html += groAlreadyHaveHtml();

    if (!allNeeded.length) {
      return html + '<p class="gro-empty">Nothing on the list to review.</p>';
    }
    if (!missingQty.length && !noStore.length && !duplicates.length) {
      html += '<div class="gro-allclear">Nothing flagged — this list is ready to shop.</div>';
    }

    function flagCard(key, badgeColor, badgeInk, title, bodyHtml) {
      var open = groceryState.openFlagKey === key;
      return '<div class="gro-flag">' +
        '<button type="button" class="gro-flag-head" data-gro="flag-toggle" data-key="' + key + '" aria-expanded="' + open + '">' +
          '<span class="gro-flag-badge" style="background:' + badgeColor + ';color:' + badgeInk + '">!</span>' +
          '<span class="gro-flag-title">' + title + '</span>' +
          '<span class="gro-chev">' + (open ? GRO_ICONS.chevDown : GRO_ICONS.chevRight) + '</span>' +
        '</button>' +
        (open ? '<div class="gro-flag-body">' + bodyHtml + '</div>' : '') +
      '</div>';
    }

    if (missingQty.length) {
      html += flagCard('qty', 'var(--urgent)', 'var(--urgent-ink)',
        'Missing quantity &middot; ' + missingQty.length,
        missingQty.map(function (it) {
          return '<div class="gro-fix" data-fix-for="' + String(it.id) + '">' +
            '<span>' + escapeHtml(it.item) + '</span>' +
            '<input type="text" class="gro-fix-qty" placeholder="Add a quantity" aria-label="Quantity for ' + escapeHtml(it.item) + '" />' +
            '<button type="button" data-gro="fix-qty" data-id="' + String(it.id) + '">Save</button>' +
          '</div>';
        }).join(''));
    }
    if (noStore.length) {
      html += flagCard('store', 'var(--apricot)', 'var(--on-accent-ink)',
        'No store assigned &middot; ' + noStore.length,
        '<p class="gro-fix-note">' + noStore.map(function (it) { return escapeHtml(it.item); }).join(', ') + '</p>' +
        '<div class="gro-fix"><button type="button" data-gro="goto-plan">Assign in Plan your stops</button></div>');
    }
    if (duplicates.length) {
      html += flagCard('dupe', 'var(--celadon)', 'var(--on-accent-ink)',
        'Possible duplicate &middot; ' + duplicates.length,
        duplicates.map(function (g) {
          return '<div class="gro-fix">' +
            '<span>' + escapeHtml(g[0].item) + ' appears ' + g.length + ' times</span>' +
            '<button type="button" class="secondary" data-gro="merge" data-ids="' + g.map(function (it) { return it.id; }).join(',') + '">Merge</button>' +
          '</div>';
        }).join(''));
    }

    var bySection = {};
    allNeeded.forEach(function (it) { (bySection[it.category] = bySection[it.category] || []).push(it); });
    html += '<div class="gro-summary"><p class="gro-eyebrow">Summary</p>';
    GRO_CATEGORIES.forEach(function (cat) {
      if (!bySection[cat] || !bySection[cat].length) return;
      html += '<div class="gro-summary-row">' +
        '<span class="gro-s-name">' + escapeHtml(GRO_CATEGORY_LABELS[cat]) + '</span>' +
        '<span class="gro-s-count">' + groPlural(bySection[cat].length, 'item', 'items') + '</span>' +
      '</div>';
    });
    html += '</div>';
    return html;
  }

  // ---------- Actions ----------
  function groSetScreen(screen) {
    groceryState.screen = screen;
    groceryState.openMenuId = null;
    renderGrocery();
    if (scrollEl) scrollEl.scrollTop = 0;
  }

  async function groAddItem() {
    var panel = groPanel();
    if (!panel) return;
    var itemInput = panel.querySelector('#gro-add-item');
    var qtyInput = panel.querySelector('#gro-add-qty');
    var btn = panel.querySelector('#gro-add-btn');
    var name = itemInput.value.trim();
    if (!name) { itemInput.focus(); return; }
    btn.disabled = true;
    var ok = await groDo(function () {
      return groPost('/api/grocery-list/add', { item: name, quantity: qtyInput.value.trim(), category: 'other' });
    }, "Couldn't add that — try again.");
    btn.disabled = false;
    if (ok) {
      itemInput.value = '';
      qtyInput.value = '';
      itemInput.focus();
    }
  }

  // Finishing a stop: everything in the trolley becomes purchased (which is
  // what actually writes it into the kitchen's inventory — see
  // tools.mark_grocery_item), then the trip is recorded. The trip row is
  // bookkeeping and never blocks the flow, which is why it is caught
  // separately. The desktop shopping mode this replaces already did both;
  // the phone screen only did the first, so this is the richer of the two
  // behaviours rather than a new one.
  async function groFinishStore(store) {
    var data = groceryState.data || await groLoadAllData();
    var s = data.stores[store];
    var inCart = s ? s.inCart : [];
    for (var i = 0; i < inCart.length; i++) {
      await groPost('/api/grocery-list/' + inCart[i].id + '/status', { status: 'purchased' });
    }
    try {
      await groPost('/api/shopping-trips/close', { store: store, item_count: inCart.length });
    } catch (err) { /* bookkeeping only */ }
    return inCart.length;
  }

  function onGroceryClick(e) {
    var el = e.target.closest('[data-gro]');
    if (!el) return;
    var action = el.dataset.gro;
    var id = el.dataset.id;

    switch (action) {
      case 'seg':
        groSetScreen(el.dataset.screen);
        return;

      case 'refresh':
        el.disabled = true;
        loadGrocery().then(function () { el.disabled = false; });
        return;

      case 'voice':
        groToggleVoice();
        return;

      case 'primary': {
        var target = groPrimaryTarget(groceryState.data);
        if (target.disabled) return;
        if (target.screen === 'shop') groceryState.shopStore = target.store;
        groSetScreen(target.screen);
        return;
      }

      case 'back-to-plan':
        groSetScreen('plan');
        return;

      case 'toggle-store': {
        var sname = el.dataset.store;
        groceryState.expandedStores[sname] = !(groceryState.expandedStores[sname] !== false);
        renderGrocery();
        return;
      }

      case 'toggle-done':
        groceryState.doneOpen = !groceryState.doneOpen;
        renderGrocery();
        return;

      case 'check':
        groDo(function () {
          return groPost('/api/grocery-list/' + id + '/status', { status: 'purchased' });
        }, "Couldn't check that off — try again.");
        return;

      case 'uncheck':
        groDo(function () {
          return groPost('/api/grocery-list/' + id + '/status', { status: 'needed' });
        }, "Couldn't put that back — try again.");
        return;

      case 'toggle-menu':
        groceryState.openMenuId = groceryState.openMenuId === id ? null : id;
        renderGrocery();
        return;

      case 'save-row': {
        var menu = el.closest('.gro-menu');
        var qty = menu.querySelector('.gro-m-qty').value;
        var cat = menu.querySelector('.gro-m-cat').value;
        var store = menu.querySelector('.gro-m-store').value.trim();
        el.disabled = true;
        var saveRowStoreResult = null;
        groDo(function () {
          return Promise.all([
            groPost('/api/grocery-list/' + id + '/update', { quantity: qty, category: cat }),
            groPost('/api/grocery-list/' + id + '/store', { store: store }).then(function (r) { saveRowStoreResult = r; return r; })
          ]);
        }, "Couldn't save that — try again.").then(function (ok) {
          if (ok) groceryState.openMenuId = null;
          renderGrocery();
          if (ok && saveRowStoreResult && saveRowStoreResult.needs_confirmation) {
            groOfferRememberToast(saveRowStoreResult.item, saveRowStoreResult.store, id);
          }
        });
        return;
      }

      case 'have':
        el.disabled = true;
        groceryState.openMenuId = null;
        groDo(function () {
          return groPostEmpty('/api/grocery-list/' + id + '/already-have');
        }, "Couldn't move that to the kitchen — try again.");
        return;

      case 'exclude':
        el.disabled = true;
        groceryState.openMenuId = null;
        groDo(function () {
          return groPostEmpty('/api/grocery-list/' + id + '/exclude');
        }, "Couldn't update that — try again.");
        return;

      case 'remove':
        el.disabled = true;
        groceryState.openMenuId = null;
        groDo(function () {
          return groPostEmpty('/api/grocery-list/' + id + '/remove');
        }, "Couldn't remove that — try again.");
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

      // ----- plan -----
      case 'sort-toggle':
        groceryState.planOpenId = groceryState.planOpenId === id ? null : id;
        renderGrocery();
        return;

      case 'sort-more':
        groceryState.planPageSize += 5;
        renderGrocery();
        return;

      case 'assign': {
        var toStore = el.dataset.store;
        el.disabled = true;
        var assignResult = null;
        groDo(function () {
          return groPost('/api/grocery-list/' + id + '/store', { store: toStore }).then(function (r) { assignResult = r; return r; });
        }, "Couldn't assign that — try again.").then(function (ok) {
          if (!ok) return;
          // Auto-advance to the next thing still needing a store, and open
          // the store it just landed in so the shopper sees where it went.
          var stillUnsorted = groceryState.data ? groUnsorted(groceryState.data) : [];
          groceryState.planOpenId = stillUnsorted.length ? String(stillUnsorted[0].id) : null;
          groceryState.bucketExpanded = {};
          if (toStore) groceryState.bucketExpanded[toStore] = true;
          renderGrocery();
          if (assignResult && assignResult.needs_confirmation) {
            groOfferRememberToast(assignResult.item, assignResult.store, id);
          }
        });
        return;
      }

      // Same backend path as the To buy ⋯ menu's "Have it" — offered here
      // too (triage row and store-bucket row alike) because "wait, I
      // already have this" is a natural thing to realize mid-sort, not
      // just from the main list. On the triage row this also removes the
      // item from Unassigned, so it advances the same way picking a store
      // does — the shopper never has to hunt for the next thing to sort.
      case 'already-have':
        el.disabled = true;
        groDo(function () {
          return groPostEmpty('/api/grocery-list/' + id + '/already-have');
        }, "Couldn't move that to the kitchen — try again.").then(function (ok) {
          if (!ok) return;
          var stillUnsorted = groceryState.data ? groUnsorted(groceryState.data) : [];
          groceryState.planOpenId = stillUnsorted.length ? String(stillUnsorted[0].id) : null;
          renderGrocery();
        });
        return;

      case 'toggle-bucket': {
        var bname = el.dataset.store;
        var wasOpen = !!groceryState.bucketExpanded[bname];
        groceryState.bucketExpanded = {};
        if (!wasOpen) groceryState.bucketExpanded[bname] = true;
        renderGrocery();
        return;
      }

      // Both clear the row's store for this week. Neither forgets the
      // remembered item->store preference (see set_grocery_item_store), so
      // "not this time" is literally true — it will offer the same store
      // again next week unless it is reassigned somewhere else.
      case 'move':
      case 'not-this-time':
        el.disabled = true;
        groDo(function () {
          return groPost('/api/grocery-list/' + id + '/store', { store: '' });
        }, "Couldn't move that — try again.");
        return;

      case 'shop-store':
        groceryState.shopStore = el.dataset.store;
        groSetScreen('shop');
        return;

      // ----- shop -----
      case 'shop-toggle': {
        var wasInCart = el.dataset.incart === '1';
        groDo(function () {
          return groPost('/api/grocery-list/' + id + '/status', { status: wasInCart ? 'needed' : 'in_cart' });
        }, "Couldn't update that — try again.");
        return;
      }

      case 'done-here': {
        var doneStore = groceryState.shopStore;
        el.disabled = true;
        groDo(function () {
          return groFinishStore(doneStore);
        }, "Couldn't finish this stop — try again.").then(function (ok) {
          if (!ok) { el.disabled = false; return; }
          groSetScreen('plan');
          showToast('Stop saved — I’ll remember what you bought where');
        });
        return;
      }

      // ----- review -----
      case 'flag-toggle':
        groceryState.openFlagKey = groceryState.openFlagKey === el.dataset.key ? null : el.dataset.key;
        renderGrocery();
        return;

      case 'fix-qty': {
        var fixRow = el.closest('.gro-fix');
        var newQty = fixRow.querySelector('.gro-fix-qty').value.trim();
        if (!newQty) return;
        el.disabled = true;
        groDo(function () {
          return groPost('/api/grocery-list/' + id + '/update', { quantity: newQty });
        }, "Couldn't save that — try again.");
        return;
      }

      case 'goto-plan':
        groSetScreen('plan');
        return;

      // Review's confirmation section — undo either kind of "already have"
      // decision via the one existing pre-shop-undo endpoint. Restoring
      // the grocery row to 'needed' is identical either way; the backend
      // also deletes the inventory row a Have it/Already have action
      // created, but only when that write didn't merge into pre-existing
      // stock (see undo_pre_shop_drop/already_have_inventory_id).
      case 'undo-already-have':
        el.disabled = true;
        groDo(function () {
          return groPostEmpty('/api/grocery-list/' + id + '/pre-shop-undo');
        }, "Couldn't undo that — try again.");
        return;

      // Restores an "Elsewhere" exclusion — reuses the existing include
      // endpoint, which already had a backend undo but no UI control
      // anywhere until this Review row.
      case 'undo-elsewhere':
        el.disabled = true;
        groDo(function () {
          return groPostEmpty('/api/grocery-list/' + id + '/include');
        }, "Couldn't undo that — try again.");
        return;

      case 'merge': {
        var ids = el.dataset.ids.split(',');
        if (!window.confirm('Merge these into one line? The extra lines will be removed.')) return;
        el.disabled = true;
        groDo(function () {
          var rest = ids.slice(1);
          return Promise.all(rest.map(function (rid) {
            return groPostEmpty('/api/grocery-list/' + rid + '/remove');
          }));
        }, "Couldn't merge those — try again.");
        return;
      }

      case 'confirm':
        groSetScreen('buy');
        return;

      case 'add':
        groAddItem();
        return;
    }
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
      hero.innerHTML = '<p class="kit-hero-error">Couldn’t load what I know about your household right now.</p>';
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
      '</div>';
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
  var weekState = { selectedIndex: null, days: [], data: null };

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
        '<div class="menu-header shell-card" id="week-header"><div class="menu-loading">Loading your menu&hellip;</div></div>' +
        '<div class="week-mobile" id="week-mobile">' +
          '<div class="week-framing" id="week-framing"></div>' +
          '<div class="day-rail" id="day-rail"></div>' +
          '<div id="day-card-wrap"></div>' +
          '<div class="whole-week-row shell-card" id="whole-week-row">' +
            '<div class="whole-week-text">' +
              '<div class="whole-week-title">The whole week</div>' +
              '<div class="whole-week-sub" id="whole-week-sub">Loading&hellip;</div>' +
            '</div>' +
            '<button type="button" class="btn-sand">Open</button>' +
          '</div>' +
        '</div>' +
        '<div class="week-grid" id="week-grid" hidden></div>' +
        // Approve / approved receipt (design_handoff_plan_the_week).
        // Sibling of #week-mobile and #week-grid for the same reason the
        // reset row below is one — the single entry point has to show at
        // both breakpoints, and #week-header is desktop-only. Sits above
        // the reset row: approving the week is the primary thing to do
        // here, starting over is the last resort.
        // The one place an open slot is answered — see renderOpenSlots.
        // Above Approve because it's the only thing on this screen the
        // household actually has to act on.
        '<div id="week-open-row"></div>' +
        '<div id="week-approve-row"></div>' +
        // InnMeals pairs the apricot "Approve this week" with a quiet
        // italic "or start over" immediately under it — so the reset entry
        // point moves up here from the bottom of the screen. Same button,
        // same handler, same dialog; it is the last resort presented as one
        // rather than as another card competing with the primary action.
        '<div class="week-reset-row" id="week-reset-row">' +
          '<button type="button" class="week-reset-link" id="week-reset-btn">or start over</button>' +
        '</div>' +
        // The permanent way into the two question screens — see
        // renderPlanWeekEntry. Below Approve because approving the week
        // you already have comes before planning the next one.
        '<div id="week-plan-row"></div>' +
        '</div>' +
      '</div>';

    panel.querySelector('#whole-week-row').addEventListener('click', function () { openWeekSheet(); });
    panel.querySelector('#week-reset-btn').addEventListener('click', function () { openResetDialog(); });
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
    if (drafted || window.location.search.indexOf('firstplan=1') !== -1) {
      var cleanUrl = window.location.pathname;
      window.history.replaceState({ tab: 'week' }, '', cleanUrl);
    }
  }

  async function loadWeekMenu(panel) {
    try {
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
      panel.querySelector('#week-header').innerHTML = '<div class="menu-loading">Couldn\'t load your menu right now.</div>';
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

  function computeWeekGapSummary(days) {
    var gaps = [];
    days.forEach(function (day) {
      if (day.isPast) return;
      WEEK_SLOTS.forEach(function (slot) {
        if (!day[slot]) gaps.push({ date: day.date, slot: slot });
      });
    });
    if (!gaps.length) return 'All seven days planned · 21 meals';
    var first = gaps[0];
    var countLabel = gaps.length === 1 ? 'One gap left' : (gaps.length + ' gaps left');
    return countLabel + ' · ' + dayName(first.date, { weekday: 'long' }) + ' ' + SLOT_LABELS[first.slot].toLowerCase();
  }

  function renderWeekFraming(panel, data, statusLine) {
    // InnMeals: "This week" with the week's dates as an apricot badge beside
    // it, and one freshness note under it. Still one line and no recap — the
    // per-slot reasons carry the detail, and `headline` is computed
    // server-side so it says at most one thing.
    //
    // The note is the same sentence the desktop header has always shown
    // (the draft's server headline, else the "N meals still need a decision"
    // count), passed in rather than recomputed so the two can't drift.
    var range = data.week_label || (data.week_start_date ? weekRangeLabel(data.week_start_date) : '');
    panel.querySelector('#week-framing').innerHTML =
      '<div class="week-head">' +
        '<h1>This week</h1>' +
        (range ? '<span class="week-badge">' + escapeHtml(range) + '</span>' : '') +
        // The trip chip (WeekWithTrip mock) — present only on a week that
        // actually has an away stretch, so an ordinary week gains no chrome.
        (data.trip_summary
          ? '<span class="week-trip">' +
              '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" ' +
              'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
              '<rect x="4" y="8" width="16" height="11" rx="2"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/></svg>' +
              escapeHtml(data.trip_summary) +
            '</span>'
          : '') +
      '</div>' +
      (statusLine ? '<div class="week-note">' + escapeHtml(statusLine) + '</div>' : '') +
      // A component-based household has no real day mapping underneath —
      // get_week_menu spreads its component pool across seven days so this
      // screen has something to show, and flags it. Saying so on mobile too:
      // it used to be desktop-header-only, so the phone (which is where this
      // screen actually lives) presented a suggested arrangement as if it
      // were a schedule.
      (data.menu_is_suggested
        ? '<div class="week-suggested-note">One example arrangement — your household assembles freely.</div>'
        : '');
  }

  function renderDayRail(panel, days) {
    // The notched moment (InnMeals): seven day tiles bottom-aligned, and the
    // selected one grows taller, fills spruce and squares off its bottom
    // corners so it pours into the hero panel directly beneath instead of
    // floating above it. The date number is new here and is real —
    // day.date's own day-of-month; the strip used to show weekday letters
    // alone, which made "which week am I looking at" unanswerable.
    panel.querySelector('#day-rail').innerHTML = days.map(function (day, i) {
      var cls = 'day-rail-cell' +
        (i === weekState.selectedIndex ? ' selected' : '') +
        (day.isToday ? ' is-today' : '') +
        (day.isPast ? ' is-past' : '') +
        (day.needsDecision ? ' needs-decision' : '');
      return (
        '<button type="button" class="' + cls + '" data-index="' + i + '" aria-pressed="' + (i === weekState.selectedIndex) + '">' +
          '<span class="day-rail-label">' + dayName(day.date, { weekday: 'short' }).slice(0, 3).toUpperCase() + '</span>' +
          '<span class="day-rail-num">' + dayName(day.date, { day: 'numeric' }) + '</span>' +
          '<span class="day-rail-dot"></span>' +
        '</button>'
      );
    }).join('');
    panel.querySelectorAll('#day-rail .day-rail-cell').forEach(function (btn) {
      btn.addEventListener('click', function () {
        weekState.selectedIndex = Number(btn.dataset.index);
        renderDayRail(panel, weekState.days);
        renderDayCard(panel, weekState.days[weekState.selectedIndex]);
      });
    });
  }

  // ---------- The selected day, split in two (InnMeals) ----------
  // Dinner is the hero: a spruce panel joined to the day strip above it.
  // Breakfast and lunch are subordinate rows in a plain card below — same
  // entries, same three states, deliberately much quieter, because "one
  // hero moment per screen" means the other two slots must not compete
  // with it. Every state courseHtml handled is handled here; the difference
  // is where it renders and how loudly.
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

  function dinnerHeroHtml(day) {
    var entry = day.dinner;
    var tag = day.status
      // Apricot only when the tag itself says a decision is wanted. Keying
      // this off day.needsDecision instead put an apricot "TONIGHT" on any
      // day that merely had an unplanned breakfast — colouring the word
      // "Tonight" as urgent when the urgency belonged to a different slot,
      // and putting a third apricot fill on a screen whose one primary
      // action is Approve. classifyDay only ever writes 'Needs you' on a
      // future day that genuinely wants one (shell.js, classifyDay).
      ? '<span class="hero-tag' + (day.status === 'Needs you' ? ' hero-tag-urgent' : '') + '">' + escapeHtml(day.status) + '</span>'
      : '';
    var top =
      '<div class="hero-top">' +
        '<span class="hero-eyebrow">' + escapeHtml(dayName(day.date, { weekday: 'long' })) + ' dinner</span>' +
        '<span class="hero-rule"></span>' + tag +
      '</div>';

    // The one deliberately empty slot in a week. A statement, not a gap and
    // not a question — it gets the hero's quiet voice and no action at all,
    // because offering to change it is exactly what planned_empty exists to
    // prevent.
    if (entry && entry.state === 'planned_empty') {
      // A need declared before the week was generated has no meal to show
      // yet, but it still has something to SAY — the badge and, for a
      // ready-made slot, the recommendation waiting on a yes.
      var emptyLine = awayLineFor(entry) ||
        (entry.need === 'ready_made' ? 'Something already made' :
         entry.need === 'quick' ? 'Something quick' : 'Nothing yet');
      return top +
        (entry.need && entry.need !== 'away'
          ? '<div class="hero-needs">' + needBadgeHtml(entry) + '</div>' : '') +
        '<div class="hero-dish hero-dish-out">' + escapeHtml(emptyLine) + '</div>' +
        // An away slot's reason says the same thing its headline just said
        // ("nothing planned, nothing bought"), so it is deliberately not
        // repeated here — the household was told once, plainly. Other
        // empty states keep their reason, which adds something.
        (entry.need !== 'away' && (entry.need_reason || entry.reason)
          ? '<div class="hero-accent">' + escapeHtml(entry.need_reason || entry.reason) + '</div>'
          : '') +
        readyMadeHtml(day);
    }

    // A decision handed back. The question and its answers live in one place
    // only — the card in #week-open-row below — so the hero says what the
    // slot IS and nothing more.
    //
    // The open_reason deliberately does NOT repeat here. It read well in
    // isolation, but the reason also renders in .week-open-reason a few
    // hundred pixels down the same unscrolled screen, so the household was
    // asked the same question twice in one glance. Same rule the pre-Pomona
    // day card followed (it showed the bare title too) — restored after the
    // independent verification pass caught the duplicate.
    if (entry && entry.state === 'open') {
      return top +
        '<div class="hero-dish hero-dish-open">' + escapeHtml(entry.title) + '</div>';
    }

    if (entry) {
      return top +
        (entry.need ? '<div class="hero-needs">' + needBadgeHtml(entry) + '</div>' : '') +
        '<div class="hero-dish' + dishSizeClass(entry.title) + '">' + escapeHtml(entry.title) + '</div>' +
        (entry.reason ? '<div class="hero-accent">' + escapeHtml(entry.reason) + '</div>' : '') +
        readyMadeHtml(day) +
        '<div class="hero-chips">' +
          (entry.meta ? '<span class="hero-chip">' + escapeHtml(entry.meta) + '</span>' : '') +
          '<span class="hero-chips-spacer"></span>' +
          dayActionsHtml(day) +
        '</div>';
    }

    return top +
      '<div class="hero-dish hero-dish-' + (day.isPast ? 'blank' : 'empty') + '">' +
        (day.isPast ? 'Not planned' : 'Nothing yet') +
      '</div>';
  }

  // Unchanged rules, restyled: only a genuinely planned dinner gets actions,
  // and "Cook this" only on today, because cook mode can only start tonight's
  // meal — a future day gets "Swap it" alone rather than a button that would
  // open the wrong day's steps.
  function dayActionsHtml(day) {
    if (!(day.dinner && day.dinner.state === 'planned' && !day.isPast)) return '';
    if (day.isToday) {
      return '<button type="button" class="hero-go" id="wk-cook-this">' +
        '<span>Cook this</span>' + ICONS.arrow + '</button>' +
        '<button type="button" class="hero-swap" id="wk-swap-it" aria-label="Swap it">Swap</button>';
    }
    return '<button type="button" class="hero-swap" id="wk-swap-it">Swap it</button>';
  }

  function sideCourseHtml(day, slot) {
    var entry = day[slot];
    var label = '<span class="side-label">' + SLOT_LABELS[slot] + '</span>';
    var body;
    if (entry && entry.state === 'planned_empty') {
      body = '<span class="side-dish side-dish-out">' +
        escapeHtml(awayLineFor(entry) || 'Nothing planned') + '</span>';
    } else if (entry && entry.state === 'open') {
      body = '<span class="side-dish side-dish-open">' + escapeHtml(entry.title) + '</span>';
    } else if (entry) {
      body = '<span class="side-dish">' + escapeHtml(entry.title) + '</span>';
    } else {
      // No fill flow was ever designed for breakfast/lunch — plain and
      // non-interactive, so it never dead-ends like a fake "Pick" would.
      body = '<span class="side-dish side-dish-blank">' +
        (day.isPast ? 'Not planned' : 'Not planned yet') + '</span>';
    }
    // The badge rides alongside the dish rather than replacing it: a quick
    // breakfast still names what it is.
    return '<div class="side-row">' + label + body + needBadgeHtml(entry) + '</div>';
  }

  // Quick-dinner-pick suggestions (day.dinner_suggestions, from
  // _suggest_quick_dinners) remain turned off per household feedback: the
  // "Nothing yet" empty state stays, without the inline pick rows. The
  // backend plumbing (get_week_menu's dinner_suggestions, POST
  // /api/needs-you/dinner) and fillWeekDinner below are untouched, so this
  // is still easy to turn back on.

  function renderDayCard(panel, day) {
    panel.querySelector('#day-card-wrap').innerHTML =
      '<div class="day-hero">' + dinnerHeroHtml(day) + '</div>' +
      '<div class="day-sides shell-card">' +
        WEEK_SLOTS.filter(function (s) { return s !== 'dinner'; })
          .map(function (s) { return sideCourseHtml(day, s); }).join('') +
      '</div>';

    var wrap = panel.querySelector('#day-card-wrap');
    wrap.querySelectorAll('.wk-suggest-row').forEach(function (btn) {
      btn.addEventListener('click', function () { fillWeekDinner(panel, btn.dataset.date, btn.dataset.meal); });
    });
    // Confirm / Choose differently on a ready-made recommendation. Emily's
    // standing rule: the system recommends, the household confirms —
    // nothing acts on the earmark until this is tapped.
    wrap.querySelectorAll('.ready-made-confirm').forEach(function (btn) {
      btn.addEventListener('click', function () { confirmReadyMade(panel, btn.dataset.date, true); });
    });
    wrap.querySelectorAll('.ready-made-other').forEach(function (btn) {
      btn.addEventListener('click', function () {
        // "Choose differently" declines the earmark and hands the question
        // to chat, which is where an actual alternative gets chosen — the
        // engine stores one recommendation, not a menu to pick from.
        confirmReadyMade(panel, btn.dataset.date, false);
        openAskSheet('Something else for ' + dayName(btn.dataset.date, { weekday: 'long' }) +
          '’s dinner — I’ll be back from a trip, so nothing that needs real cooking');
      });
    });
    var cookBtn = wrap.querySelector('#wk-cook-this');
    if (cookBtn) cookBtn.addEventListener('click', function () { activateTab('week', true, { mealsView: 'cook' }); });
    var swapBtn = wrap.querySelector('#wk-swap-it');
    if (swapBtn) swapBtn.addEventListener('click', function () {
      openAskSheet('Swap ' + dayName(day.date, { weekday: 'long' }) + '’s dinner for something else');
    });
  }

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
      // Today's needs-you band / tonight card may cover this same date —
      // if Today has already been built this session, refresh it too so
      // the two tabs never show stale, contradictory states side by side.
      var todayPanel = panels['today'];
      if (todayPanel && todayPanel.dataset.built) {
        loadNeedsYou(todayPanel);
        loadTonightsDinner(todayPanel);
      }
      await loadWeekMenu(panel);
    } catch (err) {
      console.warn('Week dinner fill failed:', err);
      alert('Could not save that pick right now — try again in a moment.');
    }
  }

  function renderWholeWeekRow(panel, days) {
    panel.querySelector('#whole-week-sub').textContent = computeWeekGapSummary(days);
  }

  // ---------- Settling a slot the app handed back ----------

  // The one place an open slot is answered, shown at both breakpoints —
  // the mobile day card is display:none on desktop, and a grid cell is far
  // too small to carry a real question. Amber, and the reason names the
  // CONSTRAINT that caused it, so the ask reads as diligence rather than
  // failure.
  function renderOpenSlots(panel, data) {
    var row = panel.querySelector('#week-open-row');
    if (!row) return;
    var open = [];
    (data.days || []).forEach(function (day) {
      WEEK_SLOTS.forEach(function (slot) {
        if (day[slot] && day[slot].state === 'open') {
          open.push({ date: day.date, slot: slot, entry: day[slot] });
        }
      });
    });
    if (!open.length) { row.innerHTML = ''; return; }

    row.innerHTML = open.map(function (o) {
      return (
        '<div class="shell-card week-open-card" data-open-date="' + o.date + '" data-open-slot="' + o.slot + '">' +
          '<div class="week-open-reason">' + escapeHtml(o.entry.open_reason || '') + '</div>' +
          (o.entry.options && o.entry.options.length
            ? '<div class="week-open-options">' + o.entry.options.map(function (opt) {
                return '<button type="button" class="week-open-option" data-choice="' + escapeHtml(opt.label) + '">' +
                  '<span class="week-open-option-label">' + escapeHtml(opt.label) + '</span>' +
                  '<span class="week-open-option-meta">' + escapeHtml(opt.meta || '—') + '</span>' +
                '</button>';
              }).join('') + '</div>'
            // No options offered — chat is the escape hatch for anything
            // the screens can't express, rather than a dead end.
            : '<button type="button" class="week-open-talk">Tell me what you’d like instead →</button>') +
        '</div>'
      );
    }).join('');
    wireOpenSlotOptions(panel, row);
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
    var todayPanel = panels['today'];
    if (todayPanel && todayPanel.dataset.built) loadGrocerySummary(todayPanel);
  }

  function renderWeekApproval(panel, data) {
    var row = panel.querySelector('#week-approve-row');
    if (!row) return;
    if (!data.weekly_plan_id) { row.innerHTML = ''; return; }

    if (data.status === 'approved') {
      var who = (data.approved_by || '').trim();
      var time = approvedAtLabel(data.approved_at);
      var eyebrow = '✓ APPROVED' + (who ? ' BY ' + who.toUpperCase() : '') + (time ? ' · ' + time : '');
      row.innerHTML =
        '<div class="shell-card week-receipt-card">' +
          '<div class="week-receipt-eyebrow">' + escapeHtml(eyebrow) + '</div>' +
          '<div class="week-receipt-body">' + escapeHtml(receiptBodyText(data)) + '</div>' +
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
        '</div>';
      row.querySelector('#week-reopen-btn').addEventListener('click', function () { reopenWeek(panel, data); });
      row.querySelector('#week-setup-link').addEventListener('click', openMealSetup);
      return;
    }

    var openCount = countOpenSlots(data);
    row.innerHTML =
      '<div class="shell-card week-approve-card">' +
        '<div class="week-approve-promise">' + escapeHtml(groceryPromiseText(data.grocery_preview)) + '</div>' +
        // Approving with a slot still open is allowed, but named — never a
        // silent shortfall.
        '<button type="button" class="btn-gold week-approve-btn" id="week-approve-btn">' +
          (openCount ? escapeHtml(approveWithOpenLabel(data, openCount)) : 'Approve the week') +
        '</button>' +
        // DECISIONS.md #3: two actions, because they're different needs.
        // One button labelled "Redo" can only be one of them, and would be
        // the wrong one half the time.
        '<div class="week-redo-row">' +
          '<button type="button" class="week-redo-btn" id="week-try-again">Try again</button>' +
          '<button type="button" class="week-redo-btn" id="week-change-answers">Change my answers</button>' +
        '</div>' +
      '</div>';
    row.querySelector('#week-approve-btn').addEventListener('click', function () { approveWeek(panel, data); });
    row.querySelector('#week-try-again').addEventListener('click', function () { tryAgain(panel, data); });
    row.querySelector('#week-change-answers').addEventListener('click', function () {
      startPlanningWeek(data.week_start_date);
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
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(data.week_start_date) + '/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      });
      if (!res.ok) throw new Error('regenerate failed');
      await res.json();
      showToast('Same answers, a different week.');
      await loadWeekMenu(panel);
    } catch (err) {
      console.warn('Regenerating the week failed:', err);
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
    var weeks = [
      { start: thisWeekStartLocal(), label: 'This week' },
      { start: nextWeekStartLocal(), label: 'Next week' }
    ];
    row.innerHTML =
      '<div class="shell-card week-plan-row">' +
        '<div class="week-plan-text">' +
          '<div class="week-plan-title">Plan a week</div>' +
          '<div class="week-plan-sub">Two rounds of questions, then I’ll draft it. Nothing gets bought until you approve.</div>' +
        '</div>' +
        '<div class="week-plan-buttons">' +
          weeks.map(function (w) {
            // "Re-plan" rather than "Plan" when that week already has one,
            // so the button never understates what it's about to do.
            var planned = weekIsPlanned(data, w.start);
            return '<button type="button" class="btn-outline-plum week-plan-btn" data-week="' + w.start + '">' +
              '<span class="week-plan-btn-label">' + (planned ? 'Re-plan ' : 'Plan ') + w.label.toLowerCase() + '</span>' +
              '<span class="week-plan-btn-dates">' + escapeHtml(weekRangeLabel(w.start)) + '</span>' +
            '</button>';
          }).join('') +
        '</div>' +
      '</div>' +
      // The standing way in to the setup screen. The receipt offers it too,
      // at the moment a week has just landed and its shortcomings are
      // freshest — but a household shouldn't have to approve something to
      // reach its own settings.
      '<button type="button" class="week-setup-link" id="week-setup-standing">' +
        'Weeks not landing how you’d like? Let’s adjust your setup →</button>';
    row.querySelectorAll('.week-plan-btn').forEach(function (btn) {
      btn.addEventListener('click', function () { startPlanningWeek(btn.dataset.week); });
    });
    row.querySelector('#week-setup-standing').addEventListener('click', openMealSetup);
  }

  function weekIsPlanned(data, weekStart) {
    // Only the week currently on screen is known for certain from this
    // payload. For the other one the button says "Plan", and /plan-week
    // itself states what it found when it opens — better an understated
    // button than a second lookup on every Meals render.
    return !!(data.weekly_plan_id && data.week_start_date === weekStart);
  }

  function weekRangeLabel(weekStart) {
    // "Aug 31–Sep 6". Same shape as the server's _format_week_range, kept
    // in step deliberately — the two are read side by side.
    var start = new Date(weekStart + 'T00:00:00');
    var end = new Date(start.getTime());
    end.setDate(end.getDate() + 6);
    var startMonth = start.toLocaleDateString('en-US', { month: 'short' });
    if (start.getMonth() === end.getMonth()) {
      return startMonth + ' ' + start.getDate() + '–' + end.getDate();
    }
    return startMonth + ' ' + start.getDate() + '–' +
      end.toLocaleDateString('en-US', { month: 'short' }) + ' ' + end.getDate();
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

  function nextWeekStartLocal() {
    // The Monday after this one, built from local date fields for the same
    // reason todayLocalStr is — toISOString() is UTC and gets the day
    // wrong either side of midnight.
    var d = new Date();
    var daysSinceMonday = (d.getDay() + 6) % 7;   // JS weeks start on Sunday
    d.setDate(d.getDate() - daysSinceMonday + 7);
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + m + '-' + day;
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

    var btn = panel.querySelector('#week-approve-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Approving…'; }
    try {
      var res = await fetch('/api/week/' + encodeURIComponent(data.week_start_date) + '/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved_by: approvedBy })
      });
      if (!res.ok) throw new Error('approve failed');
      await res.json();
      showToast('Approved. I’ll get your list together.');
      // Approving is the one action in this app that writes to the grocery
      // list wholesale, so anything already showing that list is stale the
      // moment it succeeds — the same staleness
      // refreshStaleTabsFromActions handles for chat-driven changes, just
      // reached by a button instead of a sentence.
      refreshGrocerySurfaces();
      await loadWeekMenu(panel);
    } catch (err) {
      console.warn('Week approval failed:', err);
      if (btn) { btn.disabled = false; btn.textContent = 'Approve the week'; }
      alert('Could not approve the week right now — try again in a moment.');
    }
  }

  function renderWeekMenu(panel, data) {
    var headerEl = panel.querySelector('#week-header');
    var mobileEl = panel.querySelector('#week-mobile');
    var gridEl = panel.querySelector('#week-grid');
    weekState.data = data;

    if (!data.weekly_plan_id || !data.days.length) {
      headerEl.innerHTML =
        '<div class="menu-rule-line">EST. 2019</div>' +
        '<h1 class="menu-household">' + escapeHtml(data.household_name || 'Pomona') + '</h1>' +
        '<div class="menu-subtitle">No meal plan yet</div>' +
        '<div class="menu-dots">&bull;&bull;&bull;</div>' +
        '<div class="menu-status">Ask Pomona to plan your week to get started.</div>';
      mobileEl.querySelector('#week-framing').innerHTML =
        '<div class="week-head"><h1>This week</h1></div>' +
        '<div class="week-note">No meal plan yet.</div>';
      mobileEl.querySelector('#day-rail').innerHTML = '';
      // The empty state keeps the screen's shape — a spruce hero with
      // nothing in it rather than a missing hero — so "no plan yet" reads
      // as a state of this screen instead of as a broken one.
      mobileEl.querySelector('#day-card-wrap').innerHTML =
        '<div class="day-hero">' +
          '<div class="hero-top"><span class="hero-eyebrow">This week</span><span class="hero-rule"></span></div>' +
          '<div class="hero-dish hero-dish-empty">No meal plan yet</div>' +
          '<div class="hero-accent">Ask Pomona to plan your week to get started.</div>' +
        '</div>';
      mobileEl.querySelector('#whole-week-sub').textContent = '';
      gridEl.innerHTML = '';
      renderOpenSlots(panel, data);
      renderWeekApproval(panel, data);
      renderPlanWeekEntry(panel, data);
      weekState.days = [];
      return;
    }

    var todayStr = todayLocalStr();
    var days = data.days.map(function (d) { return Object.assign({}, d, classifyDay(d, todayStr)); });
    weekState.days = days;
    var emptyAheadCount = days.filter(function (d) { return d.needsDecision; }).length;
    // The desktop header carries the same one line the mobile framing does
    // — #week-framing lives inside #week-mobile, which is display:none at
    // this breakpoint, so without this the headline simply wouldn't exist
    // on desktop and the two layouts would say different things about the
    // same week.
    var statusLine = (data.status !== 'approved' && data.headline)
      ? data.headline
      : (emptyAheadCount === 0
          ? 'Your week is set.'
          : (emptyAheadCount === 1 ? 'One meal still needs a decision.' : emptyAheadCount + ' meals still need a decision.'));

    headerEl.innerHTML =
      '<div class="menu-rule-line">EST. 2019</div>' +
      '<h1 class="menu-household">' + escapeHtml(data.household_name || 'Pomona') + '</h1>' +
      '<div class="menu-subtitle">menu for the week of ' + dayName(data.week_start_date, { month: 'long', day: 'numeric' }) + '</div>' +
      '<div class="menu-dots">&bull;&bull;&bull;</div>' +
      '<div class="menu-status">' + escapeHtml(statusLine) + '</div>' +
      (data.menu_is_suggested ? '<div class="menu-suggested-note">One example arrangement — your household assembles freely.</div>' : '');

    // Default the day rail's selection to today the first time this loads;
    // preserve whatever the household already had selected across a
    // refresh (e.g. right after filling a dinner from this same card).
    if (weekState.selectedIndex === null || weekState.selectedIndex >= days.length) {
      var todayIndexForSelect = days.reduce(function (found, d, i) { return d.isToday ? i : found; }, -1);
      weekState.selectedIndex = todayIndexForSelect >= 0 ? todayIndexForSelect : 0;
    }

    renderWeekFraming(panel, data, statusLine);
    renderDayRail(panel, days);
    renderDayCard(panel, days[weekState.selectedIndex]);
    renderWholeWeekRow(panel, days);
    renderOpenSlots(panel, data);
    renderWeekApproval(panel, data);
    renderPlanWeekEntry(panel, data);
    renderWeekSheetRows(days);

    var todayIndex = days.reduce(function (found, d, i) { return d.isToday ? i : found; }, -1);
    gridEl.innerHTML =
      '<div class="shell-card week-grid-sheet">' +
        '<div class="week-grid-table">' +
          '<div class="wg-cell wg-corner"></div>' +
          days.map(function (day, i) {
            return (
              '<div class="wg-cell wg-daycol-head' + (i === todayIndex ? ' wg-today' : '') + '">' +
                '<div class="wg-day-name">' + dayName(day.date, { weekday: 'long' }) + '</div>' +
                '<div class="wg-day-date">' + dayName(day.date, { month: 'short', day: 'numeric' }) + '</div>' +
              '</div>'
            );
          }).join('') +
          WEEK_SLOTS.map(function (slot) {
            return (
              '<div class="wg-cell wg-gutter">' + SLOT_LABELS[slot] + '</div>' +
              days.map(function (day, i) {
                var entry = day[slot];
                var cellClass = 'wg-cell wg-slot' + (i === todayIndex ? ' wg-today' : '');
                if (entry && entry.state === 'planned_empty') {
                  return (
                    '<div class="' + cellClass + '">' +
                      '<span class="wg-dish wg-dish-blank">' + escapeHtml(entry.title) + '</span>' +
                    '</div>'
                  );
                }
                if (entry && entry.state === 'open') {
                  // Tapping through to the day card is where the options
                  // live — the grid cell is too small to answer in, and a
                  // truncated question is worse than a pointer to it.
                  return (
                    '<div class="' + cellClass + ' wg-slot-open" data-date="' + day.date + '" data-slot="' + slot + '" role="button" tabindex="0">' +
                      '<span class="wg-dish wg-dish-open">Your call</span>' +
                      '<span class="wg-meta">Answer</span>' +
                    '</div>'
                  );
                }
                if (!entry) {
                  if (day.isPast) {
                    return '<div class="' + cellClass + '"><span class="wg-dish wg-dish-blank">Not planned</span></div>';
                  }
                  return (
                    '<div class="' + cellClass + ' wg-slot-empty" data-date="' + day.date + '" data-slot="' + slot + '" role="button" tabindex="0">' +
                      '<span class="course-dish-empty">Choose a ' + slot + '</span>' +
                      '<span class="course-meta-empty">Pick</span>' +
                    '</div>'
                  );
                }
                return (
                  '<div class="' + cellClass + '">' +
                    '<span class="wg-dish">' + escapeHtml(entry.title) + '</span>' +
                    (entry.meta ? '<span class="wg-meta">' + escapeHtml(entry.meta) + '</span>' : '') +
                    (entry.reason ? '<span class="wg-reason">' + escapeHtml(entry.reason) + '</span>' : '') +
                  '</div>'
                );
              }).join('')
            );
          }).join('') +
        '</div>' +
      '</div>';

    // "Pick" tap target -> Today, where the (future, Step 5) decision card
    // resolves it. Nothing else on the menu is tappable, per README §5.
    // "Pick" tap target on the desktop grid -> Today, where the needs-you
    // card resolves it (the desktop grid itself wasn't rebuilt this pass —
    // see the comment above buildWeekPanel). The mobile day card's own
    // "Pick" rows (courseHtml, above) fill inline instead via fillWeekDinner.
    panel.querySelectorAll('.wg-slot-empty').forEach(function (el) {
      var go = function () { activateTab('today', true); };
      el.addEventListener('click', go);
      el.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    });

    // An open slot on the grid selects that day and scrolls the day card
    // into view — the question and its options are too long to answer in a
    // grid cell, and a truncated question is worse than a pointer to it.
    panel.querySelectorAll('.wg-slot-open').forEach(function (el) {
      var go = function () {
        var card = panel.querySelector('.week-open-card[data-open-date="' + el.dataset.date +
          '"][data-open-slot="' + el.dataset.slot + '"]');
        if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      };
      el.addEventListener('click', go);
      el.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    });
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
    loadError: false,
    openDetail: null,   // index of the meal whose recipe is expanded
    tonightIdx: null,
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
  function setMealsView(view) {
    var panel = cookPanel();
    if (!panel || !panel.dataset.built) return;
    var isCook = view === 'cook';
    panel.dataset.mealsView = isCook ? 'cook' : 'plan';

    var planView = panel.querySelector('#week-plan-view');
    var cookView = panel.querySelector('#week-cook-view');
    if (planView) planView.hidden = isCook;
    if (cookView) cookView.hidden = !isCook;

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
      loadCook();
    }
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

    // Hold the scroll across a re-render — the same rule the Grocery panel
    // follows, and it matters more here: a re-render happens every time a
    // box is ticked, and a cook is mid-recipe when they tick one.
    var keepScroll = scrollEl ? scrollEl.scrollTop : 0;

    // Both of these replace the whole view, and both have to put the scroll
    // back like every other path here — a chat turn that fails to reload can
    // otherwise throw a reader who was halfway down the week to the top.
    if (cookState.loadError || !cookState.data) {
      view.innerHTML = '<p class="cook-error">Couldn’t load the cook view right now — switch tabs and back to try again.</p>';
      if (scrollEl) scrollEl.scrollTop = keepScroll;
      return;
    }
    var data = cookState.data;
    if (!data.weekly_plan_id) {
      view.innerHTML = '<p class="cook-empty">No plan yet this week — plan one on the Plan tab first.</p>';
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
    // First time in, the tonight meal's recipe is already open — that is
    // the whole point of a tonight-first screen. After that the cook's own
    // choice wins, so a re-render never reopens what they closed.
    if (cookState.openDetail === null && cookState.tonightIdx !== null &&
        meals[cookState.tonightIdx] && meals[cookState.tonightIdx].has_full_recipe &&
        !view.dataset.autofocused) {
      view.dataset.autofocused = '1';
      cookState.openDetail = String(cookState.tonightIdx);
    }

    view.innerHTML =
      cookTitleRowHtml(data) +
      cookAttentionHtml() +
      '<div class="cook-voice" id="cook-voice" hidden></div>' +
      cookHeroHtml(meals[cookState.tonightIdx], cookState.tonightIdx) +
      '<div class="cook-body">' +
        cookPrepHtml(data) +
        cookRestOfWeekHtml(meals) +
      '</div>';

    wireCookDetails(view);
    updateCookVoiceButtons();
    if (scrollEl) scrollEl.scrollTop = keepScroll;
  }

  function cookTitleRowHtml(data) {
    var done = data.meals_done || 0;
    var total = data.meals_total || 0;
    return '<div class="cook-titlerow">' +
      '<h1 class="cook-title">This week</h1>' +
      '<span class="cook-count">' + done + ' of ' + total + ' cooked</span>' +
    '</div>';
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
    var isDone = meal.cooked_status === 'done';
    var chips = [];
    if (meal.prep_time_minutes || meal.cook_time_minutes) {
      var bits = [];
      if (meal.prep_time_minutes) bits.push(meal.prep_time_minutes + 'm prep');
      if (meal.cook_time_minutes) bits.push(meal.cook_time_minutes + 'm cook');
      chips.push(bits.join(' + '));
    }
    if (meal.default_servings) chips.push('Serves ' + meal.default_servings);
    if (meal.batch_note) chips.push('Bulk ×' + meal.meal_count);

    // Newsreader italic, once per screen. The reasoning is the honest thing
    // to say here; the advance-prep note is the more useful one when there
    // is one, because it is what changes what you do next.
    var note = meal.advance_prep_notes || meal.reasoning || '';
    var detailOpen = String(idx) === String(cookState.openDetail);

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
      '<div class="cook-hero-actions">' +
        // The apricot action, and the only one on this screen. It opens the
        // recipe at the steps; the quiet button beside it opens the same
        // panel at the ingredients. Two ways into one thing the old page
        // already did ("Show recipe") — no new capability, just the split
        // the design asks for between "start cooking" and "what do I need".
        '<button type="button" class="cook-hero-action" data-cook="detail" data-idx="' + idx + '" data-at="steps">' +
          '<span>' + (detailOpen ? 'Hide the recipe' : 'Start step 1') + '</span>' + ICONS.arrow +
        '</button>' +
        '<button type="button" class="cook-hero-icon" data-cook="detail" data-idx="' + idx + '" data-at="ingredients" ' +
          'aria-label="Ingredients" title="Ingredients">' + COOK_ICONS.list + '</button>' +
        '<button type="button" class="cook-hero-check' + (isDone ? ' checked' : '') + '" ' +
          'data-cook="check-meal" data-entry-id="' + meal.entry_id + '" data-next="' + (isDone ? 'pending' : 'done') + '" ' +
          'aria-label="' + (isDone ? 'Mark not cooked' : 'Mark cooked') + '" ' +
          'title="' + (isDone ? 'Mark not cooked' : 'Mark cooked') + '">' + COOK_ICONS.check + '</button>' +
      '</div>' +
      (detailOpen ? '<div class="cook-hero-detail">' + cookDetailHtml(meal, idx, true) + '</div>' : '') +
    '</div>';
  }

  // The supporting rail: the prep that feeds tonight. Two-up, so it reads as
  // a pair of small things rather than another stack of full-width cards.
  function cookPrepHtml(data) {
    var tasks = data.prep_tasks || [];
    if (!tasks.length) return '';
    var done = data.prep_done || 0;
    var total = data.prep_total || tasks.length;
    return '<section class="cook-section">' +
      '<div class="cook-sectionhead">' +
        '<span class="cook-eyebrow cook-eyebrow-warm">Prep schedule</span>' +
        '<span class="cook-rule"></span>' +
        '<span class="cook-sectionnote">' + done + ' of ' + total + ' done</span>' +
        '<button type="button" class="cook-mic" data-cook="voice" data-ctx="prep" ' +
          'aria-label="Hands-free: check off prep steps by voice" ' +
          'title="Hands-free: check off prep steps by voice">' + COOK_ICONS.mic + '</button>' +
      '</div>' +
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

  // Everything that is not tonight, subordinate: one row each, dense on
  // purpose, expandable in place for the cook who is looking ahead.
  function cookRestOfWeekHtml(meals) {
    var rest = meals
      .map(function (m, i) { return { m: m, i: i }; })
      .filter(function (x) { return x.i !== cookState.tonightIdx; });
    if (!rest.length) {
      return meals.length
        ? ''
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
          var open = String(idx) === String(cookState.openDetail);
          var dayLabel = m.component_category
            ? m.component_category
            : (m.date ? dayName(m.date, { weekday: 'short' }).slice(0, 3).toUpperCase() : '');
          return '<div class="cook-week-item' + (isDone ? ' is-done' : '') + '">' +
            '<div class="cook-week-row">' +
              '<button type="button" class="cook-box' + (isDone ? ' checked' : '') + '" ' +
                'data-cook="check-meal" data-entry-id="' + m.entry_id + '" data-next="' + (isDone ? 'pending' : 'done') + '" ' +
                'aria-label="' + (isDone ? 'Mark not cooked' : 'Mark cooked') + '">' + COOK_ICONS.check + '</button>' +
              '<span class="cook-week-day">' + escapeHtml(dayLabel) + '</span>' +
              '<button type="button" class="cook-week-name" data-cook="detail" data-idx="' + idx + '" data-at="steps">' +
                escapeHtml(m.meal || '') +
              '</button>' +
              (m.advance_prep_notes ? '<span class="cook-badge cook-badge-warm">Prep ahead</span>' : '') +
              (m.batch_note ? '<span class="cook-badge">Bulk ×' + m.meal_count + '</span>' : '') +
            '</div>' +
            (open ? '<div class="cook-week-detail">' + cookDetailHtml(m, idx, false) + '</div>' : '') +
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
        '<button type="button" class="cook-mic" data-cook="voice" data-ctx="meal" data-idx="' + idx + '" ' +
          'aria-label="Hands-free for this recipe" ' +
          'title="Hands-free: read steps, ask amounts, log a substitution">' + COOK_ICONS.mic + '</button>' +
      '</div>' +
      (m.advance_prep_notes
        ? '<h4 class="cook-detail-head">Advance prep</h4><p class="cook-detail-p">' + escapeHtml(m.advance_prep_notes) + '</p>'
        : '') +
      '<h4 class="cook-detail-head">Ingredients</h4>' +
      '<ul class="cook-ings" id="cook-ings-' + idx + '">' + ingredients + '</ul>' +
      '<p class="cook-unscaled" id="cook-unscaled-' + idx + '" hidden></p>' +
      '<h4 class="cook-detail-head">Instructions</h4>' +
      cookInstructionsHtml(m) +
      (m.reasoning
        ? '<button type="button" class="cook-why" data-cook="why" data-idx="' + idx + '">Why this?</button>' +
          '<p class="cook-why-text" id="cook-why-' + idx + '" hidden>' + escapeHtml(m.reasoning) + '</p>'
        : '') +
    '</div>';
  }

  // advance_prep_step_indices are 1-based positions within `instructions`
  // that are the make-ahead steps. When a recipe tags them, the steps split
  // into "Do ahead" and "Day of" with their own numbering, so it is clear
  // what to do the night before and what happens later using it. Most
  // recipes tag nothing and get one flat list.
  function cookInstructionsHtml(m) {
    var steps = m.instructions || [];
    if (!steps.length) {
      return '<p class="cook-dim">No steps saved yet.</p>' +
        '<button type="button" class="cook-fill" data-cook="fill" data-recipe="' + escapeHtml(m.meal || '') + '">Fill in this recipe</button>';
    }
    var prepIdx = m.advance_prep_step_indices || [];
    if (!prepIdx.length) {
      return '<ol class="cook-steps">' + steps.map(function (s) { return '<li>' + escapeHtml(s) + '</li>'; }).join('') + '</ol>';
    }
    var doAhead = steps.filter(function (_, i) { return prepIdx.indexOf(i + 1) !== -1; });
    var dayOf = steps.filter(function (_, i) { return prepIdx.indexOf(i + 1) === -1; });
    return '<h5 class="cook-steplabel cook-steplabel-warm">Do ahead</h5>' +
      '<ol class="cook-steps">' + doAhead.map(function (s) { return '<li>' + escapeHtml(s) + '</li>'; }).join('') + '</ol>' +
      '<h5 class="cook-steplabel">Day of</h5>' +
      '<ol class="cook-steps">' + dayOf.map(function (s) { return '<li>' + escapeHtml(s) + '</li>'; }).join('') + '</ol>';
  }

  function wireCookDetails(view) {
    // The recipe panel is rendered by the same pass as everything else, so
    // there is nothing to re-wire per row — one delegated listener on the
    // view (attached at build) handles every control. This hook exists for
    // the one thing delegation cannot do: put the newly-opened panel where
    // it can be read.
    if (cookState.scrollToDetail) {
      cookState.scrollToDetail = false;
      var el = view.querySelector('.cook-detail');
      if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'auto', block: 'nearest' });
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
    return '<div class="cook-attention">' +
      '<p class="cook-attention-title">Needs your attention</p>' +
      items.map(function (it) {
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
      }).join('') +
    '</div>';
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

    if (what === 'detail') {
      var idx = el.getAttribute('data-idx');
      cookState.openDetail = (String(cookState.openDetail) === String(idx)) ? null : idx;
      cookState.scrollToDetail = cookState.openDetail !== null && el.getAttribute('data-at') === 'ingredients';
      renderCook();
      return;
    }
    if (what === 'why') {
      var text = document.getElementById('cook-why-' + el.getAttribute('data-idx'));
      if (text) text.hidden = !text.hidden;
      return;
    }
    if (what === 'check-meal') return cookCheckMeal(el);
    if (what === 'check-prep') return cookCheckPrep(el);
    if (what === 'serves') return cookStepServings(el);
    if (what === 'fill') return cookFillRecipe(el);
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
    if (!res.ok) throw new Error('request failed');
    return res.json().catch(function () { return {}; });
  }

  async function cookCheckMeal(el) {
    el.disabled = true;
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
    } catch (err) {
      el.disabled = false;
      showToast('That didn’t save — try again.');
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

  // Cooking changes what Today shows (its dinner hero and its prep tile read
  // the same rows), so the other screens are told rather than left to go
  // stale — the freshness policy applies to a write made here exactly as it
  // does to one made in chat.
  function refreshPlanSurfacesAfterCook() {
    // One call covers both: loadTonightsDinner reads /api/cooker-view and
    // renders the dinner hero AND the prep tile off the same response.
    if (panels.today && panels.today.dataset.built) loadTonightsDinner(panels.today);
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

  async function cookRateMeal(el) {
    var meal = el.getAttribute('data-meal');
    var notesEl = document.querySelector('[data-attn-notes="' + meal.replace(/"/g, '\\"') + '"]');
    el.disabled = true;
    try {
      await cookPost('/api/recipe-feedback', {
        recipe_name: meal,
        rating: el.getAttribute('data-rating'),
        notes: notesEl ? notesEl.value.trim() : ''
      });
      refreshCookAttention();
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
        var panel = panels['week'];
        weekState.selectedIndex = Number(btn.dataset.index);
        renderDayRail(panel, weekState.days);
        renderDayCard(panel, weekState.days[weekState.selectedIndex]);
        closeWeekSheet();
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
      if (clearedMealPlan) {
        loadNeedsYou(panels.today);
        loadTonightsDinner(panels.today);
      }
      loadGrocerySummary(panels.today);
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
      if (navigator.clipboard && navigator.clipboard.writeText) {
        try { await navigator.clipboard.writeText(url); } catch (e) { /* fall through to prompt */ }
      }
      window.prompt('Read-only link — anyone with it can see this week\'s meal plan (nothing else). Copied to your clipboard if supported:', url);
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
        // check_off_prep_step is also how a defrost task gets marked
        // done/skipped from chat ("mark the chicken thighs done") — same
        // table, same tool, just called from a different surface than the
        // Today tile's own buttons. Without this, Today's defrost tile
        // would go on showing an already-handled reminder until the next
        // full panel rebuild.
        if (panels.today && panels.today.dataset.built) loadDefrostToday(panels.today);
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
        // The list changing also changes Today's "Grocery run" tile, which
        // is a count of the same items.
        if (panels.today && panels.today.dataset.built) loadGrocerySummary(panels.today);
      } else if (action.tab === 'today' && panels.today && panels.today.dataset.built) {
        loadNeedsYou(panels.today);
        loadTonightsDinner(panels.today);
        loadGrocerySummary(panels.today);
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

  function openAskSheet(prefill) {
    ensureAskSheetBuilt();
    closeWeekSheet();
    if (isDesktopAsk()) {
      var col = document.getElementById('today-ask-input');
      if (prefill) col.value = prefill;
      col.focus();
      return;
    }
    askScrim.hidden = false;
    askSheet.hidden = false;
    if (prefill) {
      askInput.value = prefill;
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
  askComposer.addEventListener('submit', function (e) {
    e.preventDefault();
    var message = askInput.value.trim();
    if (!message) return;
    askInput.value = '';
    sendAskMessage(message);
  });

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

  // Wires up Today's permanent desktop Ask column (§7) — same
  // ensureAskSheetBuilt/sendAskMessage the sheet uses, just a second entry
  // point. Called once per Today-panel build (buildTodayPanel), which only
  // happens once (the panel is built lazily, the first time Today is
  // shown, and reused after that) — so this never double-wires the form.
  function setupAskColumn(panel) {
    ensureAskSheetBuilt(); // populates greeting + chips into the column too, even before it's ever "opened"
    var composer = panel.querySelector('#today-ask-composer');
    var input = panel.querySelector('#today-ask-input');
    composer.addEventListener('submit', function (e) {
      e.preventDefault();
      var message = input.value.trim();
      if (!message) return;
      input.value = '';
      sendAskMessage(message);
    });
    setupDictation(input, panel.querySelector('#today-ask-mic-btn'));
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
    renderNotifPanel();
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
