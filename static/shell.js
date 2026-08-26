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
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 11h16v4a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5v-4Z"/><path d="M2 11h20M8 3.5c0 1-1 1-1 2s1 1.5 1 2M13 3.5c0 1-1 1-1 2s1 1.5 1 2"/></svg>'
  };

  var TABS = [
    { key: 'today', path: '/', label: 'Today', railLabel: 'Today', icon: ICONS.calendarDay, real: true },
    { key: 'week', path: '/week', label: 'Week', railLabel: 'This Week', icon: ICONS.calendarWeek, week: true },
    { key: 'grocery', path: '/grocery', label: 'Grocery', railLabel: 'Grocery', icon: ICONS.cart, embed: '/static/grocery.html' },
    { key: 'kitchen', path: '/kitchen', label: 'Kitchen', railLabel: 'Kitchen', icon: ICONS.pot, embed: '/static/cooker.html', quickLinks: [
        { label: 'Inventory', href: '/inventory' },
        { label: 'What we know', href: '/memory' }
      ] }
  ];

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

    if (tab.embed) {
      // Lazy: the iframe's src is set the first time this tab is activated,
      // so switching to Grocery/Kitchen doesn't load both pages up front.
      panel.dataset.embed = tab.embed;
      // Fix: /inventory and /memory (and the fridge/pantry/receipt photo
      // scanning that lives on /inventory) used to be reachable from
      // index.html's old nav bar; nothing in the new shell replaced that
      // for mobile, and only /memory got a desktop-only rail link (Step
      // 1) — /inventory had no path in from the new shell chrome at all.
      // A real redesigned Kitchen tab (README §4's "Running low" / "What
      // we know about you" / scan-nudge cards) would be the eventual home
      // for this, but that's real, unbuilt work, not part of any of the 6
      // build-order steps — this is a lightweight restore, not that
      // redesign. Real page navigation (not a shell route), same as the
      // links index.html always had.
      if (tab.quickLinks) {
        var links = document.createElement('div');
        links.className = 'embed-quicklinks';
        links.innerHTML = tab.quickLinks.map(function (l) {
          return '<a href="' + l.href + '">' + escapeHtml(l.label) + '</a>';
        }).join('');
        panel.appendChild(links);
      }
    } else if (tab.placeholder) {
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

  function activateTab(key, pushHistory) {
    var tab = TABS.filter(function (t) { return t.key === key; })[0];
    if (!tab) return;

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
    // Lazy-load the embedded page the first time its tab is shown.
    if (panel && panel.dataset.embed && !panel.querySelector('iframe')) {
      var iframe = document.createElement('iframe');
      iframe.src = panel.dataset.embed;
      iframe.title = tab.label;
      panel.appendChild(iframe);
    }
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
        '<div class="today-heading">' +
          '<div class="kicker" id="today-date"></div>' +
          '<h1 id="today-h1">You&rsquo;re clear</h1>' +
        '</div>' +
        '<div class="today-body">' +
          '<div id="needs-you-band" class="today-area-needsyou"></div>' +
          '<div id="today-dinner-card" class="dinner-card today-area-dinner" hidden></div>' +
          '<div class="today-area-chores">' +
            '<div class="shell-card chores-card">' +
              '<div class="chores-header"><h2>Your chores</h2><span class="chores-count" id="chores-count"></span></div>' +
              '<div id="chores-list"></div>' +
            '</div>' +
            '<div class="shell-card grocery-summary-card">' +
              '<div class="grocery-summary-text">' +
                '<div class="grocery-summary-title">Grocery run</div>' +
                '<div class="grocery-summary-sub" id="grocery-summary-sub">Loading&hellip;</div>' +
              '</div>' +
              '<button type="button" class="btn-sand" id="grocery-summary-open">Open</button>' +
            '</div>' +
          '</div>' +
          '<div class="today-area-ask shell-card ask-column" id="today-ask-column">' +
            '<div class="ask-messages" id="today-ask-messages"></div>' +
            '<div class="ask-chips" id="today-ask-chips"></div>' +
            '<form id="today-ask-composer" class="ask-composer-bar">' +
              '<input id="today-ask-input" class="ask-composer-input" type="text" placeholder="Ask or add anything&hellip;" autocomplete="off" />' +
              '<button type="submit" id="today-ask-send-btn" class="ask-composer-send" aria-label="Send">&uarr;</button>' +
            '</form>' +
          '</div>' +
        '</div>' +
      '</div>';

    panel.querySelector('#today-date').textContent = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' }).toUpperCase();

    panel.querySelector('#grocery-summary-open').addEventListener('click', function () { activateTab('grocery', true); });

    setupAskColumn(panel);

    await Promise.all([
      loadNeedsYou(panel),
      loadTonightsDinner(panel),
      loadChores(panel),
      loadGrocerySummary(panel)
    ]);
  }

  function setTodayHeading(panel, count) {
    var h1 = panel.querySelector('#today-h1');
    h1.textContent = count === 0 ? "You're clear" : (count === 1 ? '1 thing needs you' : count + ' things need you');
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
    try {
      var res = await fetch('/api/needs-you/dinner', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: mealDate, meal: meal })
      });
      if (!res.ok) throw new Error('dinner resolve failed');
      var data = await res.json();
      showToast(meal + ' is on the plan.');
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

  // ---------- Toast (§6: "used for resolutions and adds only, never errors") ----------
  var toastEl = document.getElementById('toast');
  var toastTimer = null;
  function showToast(message) {
    if (!toastEl) return;
    toastEl.textContent = message;
    toastEl.hidden = false;
    toastEl.classList.remove('pop-in');
    void toastEl.offsetWidth; // restart the animation if a toast is already showing
    toastEl.classList.add('pop-in');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.hidden = true; }, 2200);
  }

  async function loadTonightsDinner(panel) {
    var card = panel.querySelector('#today-dinner-card');
    try {
      var res = await fetch('/api/cooker-view');
      if (!res.ok) throw new Error('cooker-view failed');
      var data = await res.json();
      var today = new Date().toISOString().slice(0, 10);
      var meal = (data.meals || []).filter(function (m) { return m.date === today && m.slot === 'dinner'; })[0];
      if (!meal) {
        // No dinner planned/plannable for tonight (or the household is on a
        // component-based plan, which has no per-day dinner at all). The
        // real "no plan yet, decide now" affordance is the needs-you band —
        // Step 5. For now this card just doesn't show, rather than showing
        // something broken or inventing a decision flow ahead of schedule.
        card.hidden = true;
        return;
      }
      var minutes = (meal.prep_time_minutes || 0) + (meal.cook_time_minutes || 0);
      card.hidden = false;
      card.innerHTML =
        '<div class="dinner-kicker">Tonight' + (minutes ? ' &middot; ' + minutes + ' min' : '') + '</div>' +
        '<div class="dinner-title">' + escapeHtml(meal.meal || 'Dinner') + '</div>' +
        '<div class="dinner-actions">' +
          '<button type="button" class="btn-gold" id="dinner-cook-mode">Cook mode</button>' +
          '<button type="button" class="btn-outline-light" id="dinner-swap">Swap</button>' +
        '</div>';
      card.querySelector('#dinner-cook-mode').addEventListener('click', function () { activateTab('kitchen', true); });
      card.querySelector('#dinner-swap').addEventListener('click', function () {
        openAskSheet('Swap tonight for something faster');
      });
    } catch (err) {
      console.warn('Tonight\'s dinner lookup failed:', err);
      card.hidden = true;
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
            (isDone ? '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>' : '') +
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

  // ---------- Week (Step 4) ----------
  // README §5: the weekly menu, "a printed restaurant menu, not a table."
  // Backed by GET /api/week-menu (app/tools.get_week_menu) — always 7 days,
  // three slots a day, each slot null or {title, meta, source}. Mobile
  // renders a paper header + seven day cards; desktop (>=1100px, a
  // breakpoint of its own, distinct from the shell's 1024px rail
  // breakpoint) renders one paper sheet as a 7-column x 3-row grid. Both
  // are built from the same fetch and switched purely by CSS so there's no
  // JS-side breakpoint logic to keep in sync.
  //
  // Judgment calls (no real prioritisation/"changed last session" tracking
  // exists yet — that's Step 5/6):
  //   - Day status: "Tonight" for today, "Served" for past days, "Needs
  //     you" for a future day with any empty slot, otherwise no status
  //     badge. The spec's fourth status ("Updated") needs change-tracking
  //     this step doesn't have yet.
  //   - Ribbon: plum for today, --urgent for any empty slot, otherwise
  //     transparent. "--good = just changed" isn't derivable yet either.
  //   - Empty-slot copy is the generic "Choose a {slot}" — the mock's
  //     "...— tee-ball night" reason needs a calendar/event signal this
  //     app doesn't have.
  //   - Header status line counts remaining empty slots from today forward
  //     rather than surfacing a specific real-world conflict (same reason).
  var SLOT_LABELS = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };
  var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];

  async function buildWeekPanel(panel) {
    panel.innerHTML =
      '<div class="week-content">' +
        '<div class="menu-header shell-card" id="week-header"><div class="menu-loading">Loading your menu&hellip;</div></div>' +
        '<div class="week-days" id="week-days"></div>' +
        '<div class="week-grid" id="week-grid" hidden></div>' +
        '<div class="menu-footer shell-card" id="week-footer">' +
          '<p>Tell me what&rsquo;s happening this week and I&rsquo;ll rebuild the plan.</p>' +
          '<button type="button" class="btn-gold" id="week-footer-ask">Ask Home Manager</button>' +
        '</div>' +
      '</div>';

    panel.querySelector('#week-footer-ask').addEventListener('click', function () {
      openAskSheet();
    });

    await loadWeekMenu(panel);
  }

  async function loadWeekMenu(panel) {
    try {
      var res = await fetch('/api/week-menu');
      if (!res.ok) throw new Error('week-menu lookup failed');
      var data = await res.json();
      renderWeekMenu(panel, data);
    } catch (err) {
      console.warn('Week menu lookup failed:', err);
      panel.querySelector('#week-header').innerHTML = '<div class="menu-loading">Couldn\'t load your menu right now.</div>';
    }
  }

  function dayName(dateStr, opts) {
    // Parse as local, not UTC, so "today" compares correctly regardless of timezone offset.
    var d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString('en-US', opts);
  }

  function classifyDay(day, todayStr) {
    var hasEmpty = WEEK_SLOTS.some(function (s) { return !day[s]; });
    var isToday = day.date === todayStr;
    var isPast = day.date < todayStr;
    // A past day's empty slot isn't an open decision any more — don't flag
    // it urgent or offer "Pick" for something that already happened.
    var needsDecision = !isPast && hasEmpty;
    var status = isToday ? 'Tonight' : (isPast ? 'Served' : (needsDecision ? 'Needs you' : ''));
    var ribbon = isToday ? 'today' : (needsDecision ? 'urgent' : '');
    return { hasEmpty: hasEmpty, needsDecision: needsDecision, isToday: isToday, isPast: isPast, status: status, ribbon: ribbon };
  }

  function renderWeekMenu(panel, data) {
    var headerEl = panel.querySelector('#week-header');
    var daysEl = panel.querySelector('#week-days');
    var gridEl = panel.querySelector('#week-grid');

    if (!data.weekly_plan_id || !data.days.length) {
      headerEl.innerHTML =
        '<div class="menu-rule-line">EST. 2019</div>' +
        '<h1 class="menu-household">' + escapeHtml(data.household_name || 'Home Manager') + '</h1>' +
        '<div class="menu-subtitle">No meal plan yet</div>' +
        '<div class="menu-dots">&bull;&bull;&bull;</div>' +
        '<div class="menu-status">Ask Home Manager to plan your week to get started.</div>';
      daysEl.innerHTML = '';
      gridEl.innerHTML = '';
      return;
    }

    var todayStr = new Date().toISOString().slice(0, 10);
    var days = data.days.map(function (d) { return Object.assign({}, d, classifyDay(d, todayStr)); });
    var emptyAheadCount = days.filter(function (d) { return d.needsDecision; }).length;
    var statusLine = emptyAheadCount === 0
      ? 'Your week is set.'
      : (emptyAheadCount === 1 ? 'One meal still needs a decision.' : emptyAheadCount + ' meals still need a decision.');

    headerEl.innerHTML =
      '<div class="menu-rule-line">EST. 2019</div>' +
      '<h1 class="menu-household">' + escapeHtml(data.household_name || 'Home Manager') + '</h1>' +
      '<div class="menu-subtitle">menu for the week of ' + dayName(data.week_start_date, { month: 'long', day: 'numeric' }) + '</div>' +
      '<div class="menu-dots">&bull;&bull;&bull;</div>' +
      '<div class="menu-status">' + escapeHtml(statusLine) + '</div>' +
      (data.menu_is_suggested ? '<div class="menu-suggested-note">One example arrangement — your household assembles freely.</div>' : '');

    function slotRowHtml(day, slot) {
      var entry = day[slot];
      var label = '<div class="course-label">' + SLOT_LABELS[slot] + '</div>';
      if (!entry) {
        if (day.isPast) {
          // Already happened — nothing to pick, so no urgent styling or tap target.
          return (
            '<div class="course-row">' +
              '<div class="course-main">' +
                '<span class="course-dish course-dish-blank">Not planned</span>' +
                '<span class="course-leader"></span>' +
              '</div>' +
              label +
            '</div>'
          );
        }
        return (
          '<div class="course-row course-row-empty" data-date="' + day.date + '" data-slot="' + slot + '" role="button" tabindex="0">' +
            '<div class="course-main">' +
              '<span class="course-dish course-dish-empty">Choose a ' + slot + '</span>' +
              '<span class="course-leader"></span>' +
              '<span class="course-meta course-meta-empty">Pick</span>' +
            '</div>' +
            label +
          '</div>'
        );
      }
      return (
        '<div class="course-row">' +
          '<div class="course-main">' +
            '<span class="course-dish' + (slot === 'dinner' ? ' course-dish-dinner' : '') + '">' + escapeHtml(entry.title) + '</span>' +
            '<span class="course-leader"></span>' +
            (entry.meta ? '<span class="course-meta">' + escapeHtml(entry.meta) + '</span>' : '') +
          '</div>' +
          label +
        '</div>'
      );
    }

    daysEl.innerHTML = days.map(function (day) {
      var ribbonClass = day.ribbon ? ' ribbon-' + day.ribbon : '';
      return (
        '<div class="shell-card day-card' + ribbonClass + (day.isPast ? ' day-past' : '') + '">' +
          '<div class="day-header">' +
            '<span class="day-name">' + dayName(day.date, { weekday: 'long' }) + '</span>' +
            '<span class="day-date">' + dayName(day.date, { month: 'short', day: 'numeric' }) + '</span>' +
            '<span class="day-rule"></span>' +
            (day.status ? '<span class="day-status">' + escapeHtml(day.status) + '</span>' : '') +
          '</div>' +
          WEEK_SLOTS.map(function (s) { return slotRowHtml(day, s); }).join('') +
        '</div>'
      );
    }).join('');

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
                  '</div>'
                );
              }).join('')
            );
          }).join('') +
        '</div>' +
      '</div>';

    // "Pick" tap target -> Today, where the (future, Step 5) decision card
    // resolves it. Nothing else on the menu is tappable, per README §5.
    panel.querySelectorAll('.course-row-empty, .wg-slot-empty').forEach(function (el) {
      var go = function () { activateTab('today', true); };
      el.addEventListener('click', go);
      el.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    });
  }

  // ---------- Docked ask bar ----------
  var askBar = document.getElementById('ask-bar');
  if (askBar) {
    askBar.addEventListener('click', function () { openAskSheet(); });
  }

  // ---------- Rail: share meal plan ----------
  // Same flow as the existing "Share meal plan" link in static/index.html —
  // reused as-is against the same /api/share-link endpoint.
  var shareBtn = document.getElementById('rail-share');
  if (shareBtn) {
    shareBtn.addEventListener('click', async function () {
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
    });
  }

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

  // Same seven quick actions static/index.html always offered — preserved
  // here rather than trimmed, per README §8 ("preserve every behavior the
  // current pages have").
  var QUICK_ACTIONS = [
    { label: 'Set up chores', msg: 'Let’s set up chores for our household.' },
    { label: 'This week’s chores', msg: 'What chores are coming up this week?' },
    { label: 'Add a chore', msg: 'Add a chore.' },
    { label: 'Set up meal planning', msg: 'Let’s set up meal planning — ask me about dietary restrictions and what we like to eat.' },
    { label: 'This week’s meals', msg: 'What’s the meal plan for this week?' },
    { label: 'Add a recipe', msg: 'I want to save a recipe.' },
    { label: 'Grocery list', msg: 'What’s on the grocery list?' }
  ];

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
    askChipTargets().forEach(function (chipsEl) {
      chipsEl.innerHTML = QUICK_ACTIONS.map(function (q, i) {
        return '<button type="button" class="ask-chip" data-i="' + i + '">' + escapeHtml(q.label) + '</button>';
      }).join('');
      chipsEl.querySelectorAll('.ask-chip').forEach(function (chip) {
        chip.addEventListener('click', function () { sendAskMessage(QUICK_ACTIONS[Number(chip.dataset.i)].msg); });
      });
    });
    addAskMessage('assistant', 'Hi! I’m your home manager. Tap a suggestion below, or just tell me what you need.');
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
        else if (action.href) window.location.href = action.href;
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

  function setAskInputsDisabled(disabled) {
    askInputTargets().forEach(function (pair) {
      pair.input.disabled = disabled;
      pair.btn.disabled = disabled;
    });
  }

  async function sendAskMessage(message) {
    if (!message || askSending) return;
    ensureAskSheetBuilt();
    addAskMessage('user', message);
    askSending = true;
    setAskInputsDisabled(true);
    var loadingWraps = addAskMessage('assistant', pickLoadingPhrase(message));
    loadingWraps.forEach(function (w) { w.querySelector('.ask-bubble').classList.add('loading'); });

    try {
      var res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: askSessionId, message: message })
      });
      loadingWraps.forEach(function (w) { w.remove(); });
      if (!res.ok) {
        var detail = '';
        try { detail = (await res.json()).detail || ''; } catch (e) { /* not JSON */ }
        throw new Error(detail || ('Request failed (' + res.status + ' ' + res.statusText + ')'));
      }
      var data = await res.json();
      addAskMessage('assistant', data.reply, data.actions);
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
  })();
})();
