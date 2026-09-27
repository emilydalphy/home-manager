/*
 * One place every screen talks to the server through.
 *
 * Why this exists (Loop Board, "one shared api.js", un-parked 2026-09-27):
 * the app had about 150 hand-written raw calls to /api, each assuming the
 * server is whatever site the page came from. That is true today and stops
 * being true the day the App Store app carries its own screens — they will
 * be loaded from the phone, and every call has to be pointed at the real
 * site instead. With every call going through here, that is one line
 * (Api.setBase) rather than 150.
 *
 * Three ways in, smallest first:
 *
 *   Api.url('/api/x')           the full address for a path — for the rare
 *                               place that needs the string itself.
 *
 *   Api.fetch('/api/x', init)   EXACTLY fetch: same arguments, same Response,
 *                               same rejection. Only the address is prefixed
 *                               and cookies are sent. Use it for streaming
 *                               (/api/chat/stream reads res.body itself), and
 *                               for any call site that already handles its
 *                               own failure — which is every call shell.js
 *                               moved onto this file, so moving them changed
 *                               nothing a person can see.
 *
 *   Api.json('/api/x', opts)    the one NEW code should reach for. Sends a
 *                               plain object as JSON (or a FormData upload
 *                               as-is), reads the answer as JSON, and on
 *                               failure shows the app's own toast and
 *                               rejects with an ApiError. See below.
 *
 * Loaded after error-reporter.js and before the page's own script, on
 * signed-in pages only (it is not in app/security.py's public list, same as
 * shell.js). It calls `fetch` by name at the moment of each request, never a
 * copy taken at load, so error-reporter.js's wrapper still sees every call
 * and still writes it into the trail.
 *
 * Deliberately no `window.` inside: the node harnesses in tests/ run slices
 * of shell.js with their own `fetch` stand-in, and this file is prepended
 * to them as-is (tests/nodeharness.py), so the stand-in is the fetch it
 * finds.
 */
var Api = (function () {
  'use strict';

  // Where the server is. Empty means "the site this page came from", which
  // is every page today — a relative path is left exactly as written, so
  // nothing about a request changes until a base is set. The App Store build
  // sets it once, before the page's own script runs, either by calling
  // Api.setBase or with <meta name="pomona-api-base" content="https://…">.
  var base = '';
  try {
    var meta = typeof document !== 'undefined' && document.querySelector &&
      document.querySelector('meta[name="pomona-api-base"]');
    if (meta && meta.content) base = String(meta.content).replace(/\/+$/, '');
  } catch (err) { /* no document (a test harness): stay relative */ }

  function setBase(url) {
    base = String(url || '').replace(/\/+$/, '');
  }

  function getBase() {
    return base;
  }

  // Only a path that starts at the root is ours to point somewhere else.
  // A full address ("https://…") or a relative one is left alone.
  function url(path) {
    var p = String(path);
    if (base && p.charAt(0) === '/' && p.charAt(1) !== '/') return base + p;
    return p;
  }

  // fetch, with the address prefixed and cookies sent. `credentials:
  // 'include'` is what carries the sign-in cookie to another origin once a
  // base is set; on the same site it sends exactly what fetch's default
  // already sent. A caller's own `credentials` wins. The init object is
  // copied, never changed, so a caller reusing it sees no difference.
  function apiFetch(path, init) {
    var opts = {};
    if (init) {
      for (var k in init) {
        if (Object.prototype.hasOwnProperty.call(init, k)) opts[k] = init[k];
      }
    }
    if (!opts.credentials) opts.credentials = 'include';
    return fetch(url(path), opts);
  }

  // ---------- Api.json: the default for new code ----------
  //
  // What a failure looks like, everywhere, so screens stop drifting (some
  // toasted, some went quiet, some showed raw errors):
  //
  //  - The person sees the app's own toast, in the app's own words:
  //    "Couldn't save that — try again." for a change, "Couldn't load that
  //    — try again." for a read. `errorMessage` says it better for one
  //    call; `quiet: true` turns the toast off for a caller that shows the
  //    failure some other way (an inline line, a retry button).
  //  - The promise rejects with an ApiError carrying `status` (0 when the
  //    request never got there), `detail` (the server's own "detail", when
  //    it sent one) and `body`. A caller that catches it has handled it.
  //  - A caller that does NOT catch it has left an unhandled rejection,
  //    which error-reporter.js already reports — with the route attached
  //    (`pomonaRoute`, the same tag its fetch wrapper puts on a dropped
  //    request), so the morning report can say which call it was. Nothing
  //    new is sent from here: a handled failure is not news, and the
  //    server already logs its own errors.
  //
  // The toast is whatever the page registered with Api.onError — shell.js
  // hands over its showToast. A page that registers nothing shows nothing
  // and just gets the rejection.
  var errorHandler = null;

  function onError(fn) {
    errorHandler = typeof fn === 'function' ? fn : null;
  }

  var SAVE_FAILED = 'Couldn’t save that — try again.';
  var LOAD_FAILED = 'Couldn’t load that — try again.';

  function ApiError(message, status, detail, body, path) {
    var err = new Error(message);
    err.name = 'ApiError';
    err.status = status;
    err.detail = detail || '';
    err.body = body === undefined ? null : body;
    try { err.pomonaRoute = routePattern(path); } catch (e) { /* never a second failure */ }
    return err;
  }

  // Which ROUTE, never which row — the same rule, and the same code, as
  // routePattern in error-reporter.js: "/api/week/2026-09-21/approve"
  // becomes "/api/week/{}/approve", because a path is somewhere a date, a
  // member id or a share token can be sitting. The server re-derives it
  // anyway; this end just never sends the value in the first place.
  function routePattern(path) {
    var p = String(path || '').split('?')[0].split('#')[0];
    if (p.indexOf('/') !== 0) return '';
    var out = [];
    var parts = p.split('/');
    for (var i = 1; i < parts.length && out.length < 8; i++) {
      var seg = parts[i];
      if (!seg) continue;
      out.push(/^[A-Za-z][A-Za-z0-9_-]{0,29}$/.test(seg) && !/^\d/.test(seg) ? seg : '{}');
    }
    return '/' + out.join('/');
  }

  // Bodies fetch already knows how to send, passed through as they are.
  // FormData especially: it writes its own multipart Content-Type (with the
  // boundary), and setting one here would break the upload. Only a plain
  // object or array is turned into JSON.
  function isSendableAsIs(body) {
    if (typeof body === 'string') return true;
    var kinds = [
      typeof FormData !== 'undefined' ? FormData : null,
      typeof URLSearchParams !== 'undefined' ? URLSearchParams : null,
      typeof Blob !== 'undefined' ? Blob : null,
      typeof ArrayBuffer !== 'undefined' ? ArrayBuffer : null,
    ];
    for (var i = 0; i < kinds.length; i++) {
      if (kinds[i] && body instanceof kinds[i]) return true;
    }
    return typeof ArrayBuffer !== 'undefined' && !!ArrayBuffer.isView && ArrayBuffer.isView(body);
  }

  // A plain object or a Headers instance, copied into a plain object.
  function copyHeaders(given) {
    var out = {};
    if (!given) return out;
    if (typeof given.forEach === 'function' && typeof given.get === 'function') {
      given.forEach(function (value, name) { out[name] = value; });
      return out;
    }
    for (var h in given) {
      if (Object.prototype.hasOwnProperty.call(given, h)) out[h] = given[h];
    }
    return out;
  }

  function hasHeader(headers, name) {
    var want = name.toLowerCase();
    for (var h in headers) {
      if (Object.prototype.hasOwnProperty.call(headers, h) && h.toLowerCase() === want) return true;
    }
    return false;
  }

  // opts: { method, body, headers, signal, keepalive, quiet, errorMessage }
  // Resolves to the parsed JSON ({} for an empty or non-JSON 2xx answer).
  function json(path, opts) {
    opts = opts || {};
    var hasBody = opts.body !== undefined && opts.body !== null;
    var method = String(opts.method || (hasBody ? 'POST' : 'GET')).toUpperCase();
    var headers = copyHeaders(opts.headers);
    var init = { method: method, headers: headers };
    if (hasBody) {
      if (isSendableAsIs(opts.body)) {
        init.body = opts.body;
      } else {
        init.body = JSON.stringify(opts.body);
        if (!hasHeader(headers, 'Content-Type')) headers['Content-Type'] = 'application/json';
      }
    }
    if (opts.signal) init.signal = opts.signal;
    if (opts.keepalive) init.keepalive = true;

    var said = opts.errorMessage || (method === 'GET' ? LOAD_FAILED : SAVE_FAILED);

    function fail(err) {
      // A caller that cancelled its own request (its AbortController) asked
      // for that; it is not a failure to tell anyone about.
      var aborted = err && err.name === 'AbortError';
      if (!opts.quiet && !aborted && errorHandler) {
        try { errorHandler(said, err); } catch (e) { /* a toast must never break the caller */ }
      }
      throw err;
    }

    // Never got there, or dropped mid-answer. Keep the browser's own error
    // (its message is what error-reporter.js matches to say "network"),
    // tagged with status 0 and the route.
    function dropped(netErr) {
      if (netErr && typeof netErr === 'object') {
        try {
          if (netErr.status === undefined) netErr.status = 0;
          if (!netErr.pomonaRoute) netErr.pomonaRoute = routePattern(path);
        } catch (e) { /* frozen error object: leave it */ }
      }
      return fail(netErr);
    }

    return apiFetch(path, init).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        if (text) {
          try { data = JSON.parse(text); } catch (e) { data = null; }
        }
        if (!res.ok) {
          var detail = data && typeof data.detail === 'string' ? data.detail : '';
          return fail(ApiError('Request failed (' + res.status + ')', res.status, detail, data, path));
        }
        return data === null ? {} : data;
      }, dropped);
    }, dropped);
  }

  return {
    url: url,
    fetch: apiFetch,
    json: json,
    setBase: setBase,
    getBase: getBase,
    onError: onError,
  };
})();
