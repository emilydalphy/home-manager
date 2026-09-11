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
 * numbers. Never page content, never form values, never anything the
 * household typed. Same rule the server-side error table follows — and the
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
    report(location.pathname, detail, {
      type: reason && reason.name ? reason.name : '',
      source: frames.length ? frames[0].split('@').pop() : '',
      stack: frames,
    });
  });
})();
