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
 * What gets sent is deliberately thin: a script URL and line, or an
 * exception name. Never page content, never form values, never anything
 * the household typed. Same rule the server-side error table follows.
 *
 * Every failure path here is swallowed. This runs when the page is already
 * having a bad time; a reporter that throws would turn one problem into
 * two, and the second one would be ours.
 */
(function () {
  var MAX_REPORTS = 5;      // per page load — a render loop must not flood
  var sent = 0;
  var lastKey = '';

  function report(where, detail) {
    try {
      if (sent >= MAX_REPORTS) return;
      // Identical consecutive errors are one story, not many. A component
      // failing on every animation frame would otherwise spend the whole
      // budget saying the same thing.
      var key = where + '|' + detail;
      if (key === lastKey) return;
      lastKey = key;
      sent++;

      var body = JSON.stringify({
        where: String(where || '').slice(0, 120),
        detail: String(detail || '').slice(0, 200),
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
    report(at, message);
  }, true);

  window.addEventListener('unhandledrejection', function (e) {
    // A rejected promise nobody caught — how a failed fetch usually shows
    // up in this app, since most screens load their data that way.
    var reason = e && e.reason;
    var detail = reason && reason.message ? reason.message : String(reason || 'unhandled rejection');
    report(location.pathname, detail);
  });
})();
