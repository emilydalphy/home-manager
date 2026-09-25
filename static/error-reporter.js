/*
 * Tell the server when something breaks in the browser.
 *
 * Before this, the front end reported nothing at all. A screen that failed
 * to load wrote a console.warn nobody would ever see, and in a couple of
 * places rendered a silently blank section. So "it was just empty" — the
 * way a real person describes it — could mean the server errored, the
 * network dropped, or there was genuinely nothing to show. Three different
 * problems wearing the same face, and no way to tell them apart.
 *
 * What gets sent is deliberately thin: an exception TYPE, the script file
 * and line it came from, and a few stack frames as function names and line
 * numbers, plus a short trail of the screens and requests just before it
 * (2026-09-25 — see "the trail" below). Never page content, never form
 * values, never anything the household typed. Same rule the server-side error table follows — and the
 * server re-derives every one of these before storing it, because this end
 * is the untrusted one and a check that only runs here is a check a curl
 * skips.
 *
 * The shape is sent because without it the entire record of a real tester's
 * crash was "browser error on /". A rejected promise carries no filename
 * and no line on the event at all, so its stack is the only thing that can
 * say where in the code it happened.
 *
 * Every failure path here is swallowed. This runs when the page is already
 * having a bad time; a reporter that throws would turn one problem into
 * two, and the second one would be ours.
 */
(function () {
  var MAX_REPORTS = 5;      // per page load — a render loop must not flood
  var sent = 0;
  var lastKey = '';

  // One frame of a stack, as the browser writes it, reduced to the two
  // things that locate it in the code: the function and the line. Every
  // engine formats a stack differently and none of it is specified, so this
  // reads the two shapes that cover the browsers this app runs in and gives
  // up quietly on anything else — a missing frame costs a line of context,
  // a wrong one costs an hour.
  //
  //   Chrome:  "    at renderWeek (https://host/static/shell.js:6207:15)"
  //   Safari/Firefox: "renderWeek@https://host/static/shell.js:6207:15"
  function frameShape(line) {
    var text = String(line || '').trim();
    var fn = '';
    var loc = '';
    var m = text.match(/^at\s+([^\s(]+)\s+\((.+)\)$/) || text.match(/^at\s+()(.+)$/);
    if (m) {
      fn = m[1];
      loc = m[2];
    } else if (text.indexOf('@') > -1) {
      fn = text.slice(0, text.indexOf('@'));
      loc = text.slice(text.indexOf('@') + 1);
    } else {
      return '';
    }
    // File and line only. The path goes for the same reason the server
    // stores a route pattern rather than a URL: a path is somewhere a
    // member's name or a live share token can be sitting.
    loc = loc.split('?')[0].split('/').pop();
    if (!/^[A-Za-z0-9_.\-]{1,60}:\d{1,7}(:\d{1,7})?$/.test(loc)) return '';
    // This file is never the answer to "where did it happen". Chrome
    // builds a fetch TypeError's stack at the CALL SITE, and since the
    // wrapper below became the call site, the top frame of every dropped
    // request was `window.fetch@error-reporter.js` — so `source`, which
    // the morning report prints in its head line, named the error
    // reporter for exactly the errors this reporter exists to explain.
    // Measured against main: `shell.js:6:33` became
    // `error-reporter.js:151:30`. Dropping our own frames restores it,
    // and is right beyond the wrapper too: a frame inside the reporter
    // locates the reporter, never the app.
    if (loc.indexOf('error-reporter.js:') === 0) return '';
    return (/^[A-Za-z0-9_$.]{1,40}$/.test(fn) ? fn + '@' : '') + loc;
  }

  function stackShape(err) {
    var raw = err && typeof err.stack === 'string' ? err.stack : '';
    if (!raw) return [];
    var out = [];
    var lines = raw.split('\n');
    for (var i = 0; i < lines.length && out.length < 5; i++) {
      // The first line of a Chrome stack is "TypeError: <message>" — the
      // message, which is the one thing that must never go out.
      var shape = frameShape(lines[i]);
      if (shape) out.push(shape);
    }
    return out;
  }

  // The browser's OWN words for "the request never got there". A native
  // fetch failure is a TypeError the engine made, and it carries no stack
  // at all — so without this the whole record is a bare "TypeError", which
  // is what a real bug that rejects with a TypeError looks like too. Julia
  // hit exactly that on 2026-09-18 and the morning report could not say
  // which it was.
  //
  // A CLOSED LIST of exact strings, never a pattern: the message is the one
  // field that must never be stored (Emily, 2026-09-10 — keep the shape,
  // never the words), so it is matched here, turned into a token, and
  // dropped. The server checks the same list against the same message for
  // the same reason every other field is re-derived there — this end is the
  // untrusted one. Adding a string here without adding it there buys
  // nothing.
  var NETWORK_MESSAGES = [
    'Load failed',                                   // Safari
    'Failed to fetch',                               // Chrome, Edge
    'NetworkError when attempting to fetch resource.', // Firefox
    'The network connection was lost.',              // Safari, iOS
    'cancelled',                                     // Safari, navigated away
    'The Internet connection appears to be offline.' // Safari, iOS
  ];

  function isNetworkMessage(text) {
    var msg = String(text || '');
    for (var i = 0; i < NETWORK_MESSAGES.length; i++) {
      if (msg === NETWORK_MESSAGES[i]) return true;
    }
    return false;
  }

  // Which ROUTE failed, never which row. "/api/week/2026-09-21/approve"
  // becomes "/api/week/{}/approve" — the same rule the server already
  // follows for `where_`, and for the same reason: a path is somewhere a
  // date, a member id or a live share token can be sitting. A segment is
  // kept only if it reads as a fixed part of a route; anything else is a
  // value and becomes {}.
  function routePattern(url) {
    var path;
    try {
      path = new URL(String(url || ''), location.href).pathname;
    } catch (err) {
      path = String(url || '').split('?')[0];
    }
    if (path.indexOf('/') !== 0) return '';
    var out = [];
    var parts = path.split('/');
    for (var i = 1; i < parts.length && out.length < 8; i++) {
      var seg = parts[i];
      if (!seg) continue;
      out.push(/^[A-Za-z][A-Za-z0-9_-]{0,29}$/.test(seg) && !/^\d/.test(seg) ? seg : '{}');
    }
    return '/' + out.join('/');
  }

  // ---------- the trail: what the person was doing just before ----------
  //
  // Julia's 2026-09-24 error was a bare "TypeError on /", no source, no
  // stack. Railway's logs showed it fired in the second the page was being
  // redirected — /login → / → /onboarding, with GET /api/coaching in
  // between — and that took an evening of reading logs to find. A trail of
  // the last few steps says it on the row itself.
  //
  // A step is only ever one of four machine-made things, never anything a
  // person typed or anything the page says:
  //
  //   "from /login"            the page this one was reached from
  //   "/week"                  this page, as a route pattern
  //   "view week.day"          a screen change, by the app's own screen key
  //   "GET /api/coaching 200"  a request: method, route pattern, status
  //                            (or "failed" when it never got an answer)
  //   "leaving page"           the page starting to go away
  //
  // Button labels are deliberately NOT steps. "Tapped Sophia's Chicken
  // Skewers" is the obvious thing to want and exactly the thing a recipe
  // or member name hides in. The server re-checks every step against the
  // app's own route table and its closed list of screen keys, and drops
  // anything that fails — this end is the untrusted one, so this list is a
  // courtesy and the server's is the guarantee.
  var TRAIL_MAX = 8;
  var trail = [];

  function step(text) {
    try {
      text = String(text || '');
      if (!text) return;
      // A screen polling the same route, or a render loop replacing the
      // same history entry, is one step, not the whole trail.
      if (trail.length && trail[trail.length - 1] === text) return;
      trail.push(text);
      if (trail.length > TRAIL_MAX) trail.shift();
    } catch (err) { /* see the header: never a second failure */ }
  }

  // Which screen a history entry is, read off the state object the app's
  // own code pushed — never the url, and never anything a person chose.
  // shell.js's tabs push {tab}, Plan's steps {tab:'week', mealsStep},
  // Shop's {tab:'grocery', groStep}, the ask sheet {askSheet:true}, and
  // onboarding {onboardingStep}. The root step of a tab ('week', 'list')
  // is the tab itself.
  function viewName(state) {
    if (!state || typeof state !== 'object') return '';
    var name = '';
    if (state.askSheet) name = 'ask';
    else if (state.onboardingStep) name = 'onboarding.' + state.onboardingStep;
    else if (state.tab) {
      var sub = state.mealsStep || state.groStep || '';
      name = (sub && sub !== 'week' && sub !== 'list') ? state.tab + '.' + sub : String(state.tab);
    }
    return /^[a-z][a-z0-9.\-]{0,40}$/.test(name) ? name : '';
  }

  // Hooked at the one place every screen change in this app already goes
  // through: the history API. shell.js's activateTab, goMealsStep and
  // goGroceryStep, and onboarding's pushStepHistory, all push or replace a
  // state object and nothing else changes the screen — so wrapping the two
  // methods here, the same way fetch is wrapped below, sees every one of
  // them without a call sprinkled into any of those files. Back is the
  // popstate listener. The wrapper returns exactly what the native method
  // returned and never throws its own error.
  try {
    if (window.history && typeof window.history.pushState === 'function') {
      ['pushState', 'replaceState'].forEach(function (method) {
        var nativeMethod = window.history[method];
        window.history[method] = function (state) {
          var out = nativeMethod.apply(this, arguments);
          try { step(viewName(state) ? 'view ' + viewName(state) : ''); } catch (err) { /* never */ }
          return out;
        };
      });
    }
    window.addEventListener('popstate', function (e) {
      var name = viewName(e && e.state);
      if (name) step('view ' + name);
    });
    // Leaving. beforeunload fires the moment a navigation STARTS —
    // location.href = '/onboarding' included — which is the window Julia's
    // error fell in: the old page is still running, its requests are being
    // torn down, and anything that rejects now happens "on /" while the
    // person is already somewhere else. pagehide is the one iOS Safari
    // fires reliably. Neither listener prevents anything, so the page
    // leaves exactly as it would have.
    window.addEventListener('beforeunload', function () { step('leaving page'); });
    window.addEventListener('pagehide', function () { step('leaving page'); });
  } catch (err) { /* see the header */ }

  // Tag a failed request's own error with the route it was for, so the
  // rejection handler below can say which one it was. Wrapping fetch is
  // the only way to learn that: the rejection carries no url, and this app
  // has well over a hundred fetch call sites, none of which should have to
  // know the reporter exists.
  //
  // The wrapper NEVER changes what a caller sees. It re-throws the original
  // rejection, untouched, so a screen's own error handling behaves exactly
  // as before; a caller that catches its own failure reports nothing, which
  // is right — a handled failure is not news. Tagging the error rather than
  // remembering "the last one that failed" in a variable is deliberate:
  // a variable would still be sitting there, stale, when some unrelated
  // rejection arrived later and took the blame for it.
  //
  // It also writes each finished request into the trail, as a route
  // pattern and a status — never the url, never a body.
  if (window.fetch) {
    var nativeFetch = window.fetch;
    window.fetch = function (input, init) {
      var result;
      try {
        result = nativeFetch.apply(this, arguments);
      } catch (err) {
        throw err;
      }
      // .then AND .catch: a thenable with only .then is a promise to the
      // language and not to this line, and calling .catch on one would
      // throw synchronously out of fetch — turning a reporting nicety
      // into a broken request. Unreachable today (nothing else in this
      // app wraps fetch, and native fetch returns a real Promise), which
      // is exactly why it is worth costing one clause rather than an
      // outage nobody can explain.
      if (!result || typeof result.then !== 'function' ||
          typeof result.catch !== 'function') return result;
      var target = (input && input.url) ? input.url : input;
      var said = '';
      try {
        var method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
        var route = routePattern(target);
        // The reporter's own post is not something the person did.
        if (route !== '/api/client-error') said = method + ' ' + route + ' ';
      } catch (err) { said = ''; }
      return result.then(function (res) {
        try { if (said) step(said + (res && res.status ? res.status : 'failed')); } catch (err) { /* never */ }
        return res;
      }, function (err) {
        try {
          if (said) step(said + 'failed');
          if (err && typeof err === 'object' && !err.pomonaRoute) {
            err.pomonaRoute = routePattern(target);
          }
        } catch (tagErr) { /* reporting must never break a request */ }
        throw err;
      });
    };
  }

  // Where this page load starts: the page it came from, when that was
  // this app, and the page itself. Both as route patterns; the server maps
  // them onto its own route table, so a share token or a name in a path
  // never survives even if the pattern kept it.
  try {
    var ref = document.referrer ? new URL(document.referrer) : null;
    if (ref && ref.origin === location.origin) step('from ' + routePattern(ref.pathname));
  } catch (err) { /* no referrer is the common case */ }
  try { step(routePattern(location.pathname)); } catch (err) { /* see the header */ }

  function report(where, detail, shape) {
    try {
      if (sent >= MAX_REPORTS) return;
      // Identical consecutive errors are one story, not many. A component
      // failing on every animation frame would otherwise spend the whole
      // budget saying the same thing.
      var key = where + '|' + detail;
      if (key === lastKey) return;
      lastKey = key;
      sent++;

      shape = shape || {};
      var body = JSON.stringify({
        where: String(where || '').slice(0, 120),
        detail: String(detail || '').slice(0, 200),
        type: String(shape.type || '').slice(0, 40),
        source: String(shape.source || '').slice(0, 80),
        stack: shape.stack || [],
        // Why there is no location, when there is none. Empty whenever the
        // stack answered the question on its own.
        reason: String(shape.reason || '').slice(0, 20),
        request: String(shape.request || '').slice(0, 80),
        // What the person was doing just before — see the trail above.
        trail: trail.slice(),
      });

      // sendBeacon survives the page being closed or navigated away, which
      // is exactly when a failing page tends to get abandoned. fetch is
      // the fallback where it isn't available.
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/client-error', new Blob([body], { type: 'application/json' }));
      } else if (window.fetch) {
        fetch('/api/client-error', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: body,
          keepalive: true,
        }).catch(function () { /* reporting must never surface its own failure */ });
      }
    } catch (err) { /* see above */ }
  }

  window.addEventListener('error', function (e) {
    // Two different events share this name. A failed <img>/<script>/<link>
    // has a target and a url; a real script error has a message. Note the
    // url lives on `src` for scripts and images but `href` for
    // stylesheets — missing that reported every blocked font stylesheet
    // as a contentless "script error".
    var target = e && e.target;
    if (target && target !== window && (target.src || target.href)) {
      var raw = String(target.src || target.href);

      // Only our own files. A blocked font or third-party script fires on
      // every single page load — on a flaky connection that would be the
      // entire contents of the report, spending the page's whole budget
      // before a real error got a turn. It also isn't a bug in this app.
      if (raw.indexOf('/') === 0 || raw.indexOf(location.origin) === 0) {
        // Host and path only, never the query string: partly because
        // "?family=Karla:wght@400;500;700" is noise rather than
        // information, and partly because a query string is somewhere
        // data can hide, and nothing here should be able to carry any.
        var name = raw;
        try {
          var u = new URL(raw, location.href);
          name = u.host + u.pathname;
        } catch (err) { name = raw.split('?')[0]; }
        report(location.pathname, 'failed to load ' + name.slice(0, 80));
      }
      return;
    }

    // A cross-origin script error is reported by every browser as the
    // literal string "Script error." with no file and no line — the
    // browser withholding the detail, by design. Recording it says only
    // "something, somewhere, went wrong", which is worse than silence on
    // a report whose whole job is to surface the errors that matter.
    var message = (e && e.message) || '';
    if (!message || (message.indexOf('Script error') === 0 && !e.filename)) return;

    var at = e.filename
      ? String(e.filename).split('/').pop() + ':' + (e.lineno || 0)
      : location.pathname;
    var err = e && e.error;
    report(at, message, {
      // e.error is the thrown value and carries the class name; e.message
      // is the sentence. The name is the half worth keeping.
      type: err && err.name ? err.name : '',
      source: e.filename
        ? String(e.filename).split('?')[0].split('/').pop() + ':' + (e.lineno || 0) +
          (e.colno ? ':' + e.colno : '')
        : '',
      stack: stackShape(err),
    });
  }, true);

  window.addEventListener('unhandledrejection', function (e) {
    // A rejected promise nobody caught — how a failed fetch usually shows
    // up in this app, since most screens load their data that way.
    var reason = e && e.reason;
    var detail = reason && reason.message ? reason.message : String(reason || 'unhandled rejection');
    // This is the case the shape was built for. The event has no filename
    // and no line of its own, so before the stack the whole record of a
    // rejected fetch was "browser error" on whatever page you were on.
    var frames = stackShape(reason);
    // No frames is the case this cannot otherwise explain. Say WHY it has
    // no location — a request that never arrived, or genuinely unknown —
    // so the morning report can tell a tester's phone losing signal from a
    // bug. With frames there is a location already and nothing to explain.
    var reasonToken = '';
    var route = '';
    if (!frames.length) {
      reasonToken = isNetworkMessage(detail) ? 'network' : 'unknown';
      route = (reason && reason.pomonaRoute) || '';
    }
    report(location.pathname, detail, {
      type: reason && reason.name ? reason.name : '',
      source: frames.length ? frames[0].split('@').pop() : '',
      stack: frames,
      reason: reasonToken,
      request: route,
    });
  });
})();
