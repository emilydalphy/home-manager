/*
  Persistent app shell — Step 1 (design_handoff_shell/README.md §4, §7, §8).

  Renders the tab bar (mobile) / left rail (desktop >=1024px), the docked
  ask bar, and a single scroll area that swaps between four tab panels
  without reloading the page (history.pushState + show/hide, not a real
  navigation). Grocery and Kitchen embed the existing static pages
  unmodified via <iframe> — this is deliberate: it means their internals
  (filters, inline edit, cook steps, etc.) needed zero changes to move
  inside the shell. Today does the same with the current chat-based
  index.html until Step 2 replaces it with the real Today screen. Week has
  no existing page to preserve, so it gets a plain placeholder until Step 4.
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
    { key: 'today', path: '/', label: 'Today', railLabel: 'Today', icon: ICONS.calendarDay, embed: '/static/index.html' },
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
      // so switching to Grocery/Kitchen/Today doesn't load all four
      // existing pages up front.
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

    // Lazy-load the embedded page the first time its tab is shown.
    var panel = panels[key];
    if (panel && panel.dataset.embed && !panel.querySelector('iframe')) {
      var iframe = document.createElement('iframe');
      iframe.src = panel.dataset.embed;
      iframe.title = tab.label;
      panel.appendChild(iframe);
    }

    if (pushHistory && window.location.pathname.replace(/\/+$/, '') !== (tab.path === '/' ? '/' : tab.path.replace(/\/+$/, ''))) {
      window.history.pushState({ tab: key }, '', tab.path);
    }
  }

  window.addEventListener('popstate', function () { activateTab(currentTabKey(), false); });

  // ---------- Docked ask bar ----------
  // The real ask sheet is Step 3. Interim: tapping the dock takes you to
  // Today, where the current chat interface still lives, so the assistant
  // stays reachable from every screen (README problem #4) even before the
  // sheet exists.
  var askBar = document.getElementById('ask-bar');
  if (askBar) {
    askBar.addEventListener('click', function () { activateTab('today', true); });
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
  // Ported from static/index.html: previously this ran at the top level of
  // the page. Now that Today's chat is embedded in an iframe, the same
  // check inside index.html would only redirect the iframe, stranding a
  // first-time visitor inside a small embedded frame with no shell chrome.
  // Running it here redirects the whole tab instead.
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
