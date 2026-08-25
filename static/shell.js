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
    { key: 'week', path: '/week', label: 'Week', railLabel: 'This Week', icon: ICONS.calendarWeek, placeholder: {
        kicker: 'Coming in step 4',
        title: 'Your weekly menu',
        body: 'The paper-style weekly menu (breakfast / lunch / dinner, seven days) lands here once the meal-plan data model supports three slots a day.'
      } },
    { key: 'grocery', path: '/grocery', label: 'Grocery', railLabel: 'Grocery', icon: ICONS.cart, embed: '/static/grocery.html' },
    { key: 'kitchen', path: '/kitchen', label: 'Kitchen', railLabel: 'Kitchen', icon: ICONS.pot, embed: '/static/cooker.html' }
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

  // The real ask sheet is Step 3. Interim: anything that should "open the
  // assistant" instead does a real navigation to the existing chat page —
  // it's the one place the assistant still fully works end to end until
  // the sheet exists (README problem #4: the assistant should stay
  // reachable from anywhere, not just from Today).
  function openAssistant() {
    window.location.href = '/static/index.html';
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

    if (pushHistory && window.location.pathname.replace(/\/+$/, '') !== (tab.path === '/' ? '/' : tab.path.replace(/\/+$/, ''))) {
      window.history.pushState({ tab: key }, '', tab.path);
    }
  }

  window.addEventListener('popstate', function () { activateTab(currentTabKey(), false); });

  // ---------- Today ----------
  // README §4: heading, tonight's dinner, chores, grocery summary. No
  // needs-you band yet (Step 5) — needsYouCount is hardcoded to 0 for now,
  // but the heading is still *derived* from that count, same as it will be
  // once the band exists and starts setting it for real.
  async function buildTodayPanel(panel) {
    panel.innerHTML =
      '<div class="today-content">' +
        '<div class="today-heading">' +
          '<div class="kicker" id="today-date"></div>' +
          '<h1 id="today-h1">You&rsquo;re clear</h1>' +
        '</div>' +
        '<div id="today-dinner-card" class="dinner-card" hidden></div>' +
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
      '</div>';

    var needsYouCount = 0; // Step 5 will replace this with the real needs-you band count.
    var h1 = panel.querySelector('#today-h1');
    h1.textContent = needsYouCount === 0 ? "You're clear" : (needsYouCount === 1 ? '1 thing needs you' : needsYouCount + ' things need you');

    panel.querySelector('#today-date').textContent = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' }).toUpperCase();

    panel.querySelector('#grocery-summary-open').addEventListener('click', function () { activateTab('grocery', true); });

    await Promise.all([
      loadTonightsDinner(panel),
      loadChores(panel),
      loadGrocerySummary(panel)
    ]);
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
      // Swap should open the ask sheet pre-filled with "Swap tonight for
      // something faster" — the sheet doesn't exist yet (Step 3), so this
      // is the same interim escape hatch as the docked ask bar for now.
      card.querySelector('#dinner-swap').addEventListener('click', openAssistant);
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

  // ---------- Docked ask bar ----------
  var askBar = document.getElementById('ask-bar');
  if (askBar) {
    askBar.addEventListener('click', openAssistant);
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
