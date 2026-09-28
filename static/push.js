/*
 * Push notifications, inside Pomona's iPhone app only (Loop Board "App
 * Store: push notifications on the iPhone app", 2026-09-27).
 *
 * shell.js loads this file only when the Capacitor bridge says it is running
 * as the native app (loadPushModule), so a browser or the home-screen web
 * app never fetches it. It defines window.PomonaPush and does three things:
 *
 *   1. Listens, from the moment it loads, for a tap on a notification and
 *      opens the screen it is about (the notification's `path`, one of the
 *      shell's own routes — app/push.py ALLOWED_PATHS). Capacitor holds a
 *      tap that launched the app until this listener is added.
 *   2. When notifications are already allowed, registers with Apple on
 *      every launch and saves the phone's token on the server
 *      (POST /api/push/devices, through api.js) — tokens can change, and
 *      this keeps the server's copy current.
 *   3. turnOn(): asks iOS for permission (the one system prompt) and, on a
 *      yes, registers and switches this adult's notifications on. shell.js
 *      decides WHEN to call it — never on first launch; after the first
 *      week is approved (see offerPush in shell.js).
 *
 * Nothing here sends a notification; the server does (app/push.py).
 */
(function () {
  'use strict';

  var ROUTES = { '/': 1, '/week': 1, '/grocery': 1, '/kitchen': 1 };

  function plugin() {
    var cap = window.Capacitor;
    if (!cap) return null;
    if (cap.Plugins && cap.Plugins.PushNotifications) return cap.Plugins.PushNotifications;
    if (typeof cap.registerPlugin === 'function') {
      try { return cap.registerPlugin('PushNotifications'); } catch (e) { return null; }
    }
    return null;
  }

  var P = plugin();
  var registered = null;  // the promise of the one registration per page load

  function openPath(path) {
    var p = typeof path === 'string' ? path : '';
    if (!ROUTES[p]) p = '/';
    if (window.location.pathname !== p) window.location.assign(p);
  }

  function saveToken(token) {
    return Api.json('/api/push/devices', { body: { token: String(token || ''), platform: 'ios' }, quiet: true })
      .catch(function (err) { console.warn('Saving the notification token failed:', err); });
  }

  function listen() {
    if (!P || typeof P.addListener !== 'function') return;
    P.addListener('registration', function (t) { saveToken(t && t.value); });
    P.addListener('registrationError', function (err) {
      console.warn('Notification registration failed:', err && err.error);
    });
    P.addListener('pushNotificationActionPerformed', function (action) {
      var data = (action && action.notification && action.notification.data) || {};
      openPath(data.path);
    });
  }

  // 'granted' | 'denied' | 'prompt' — iOS's own answer, never a guess.
  function permission() {
    if (!P) return Promise.resolve('denied');
    return P.checkPermissions().then(function (r) {
      var s = r && r.receive;
      return s === 'granted' || s === 'denied' ? s : 'prompt';
    }).catch(function () { return 'denied'; });
  }

  function register() {
    if (!P) return Promise.resolve();
    if (!registered) {
      registered = P.register().catch(function (err) {
        registered = null;
        console.warn('Notification registration failed:', err);
      });
    }
    return registered;
  }

  function setOn(on) {
    return Api.json('/api/push/preferences', { body: { on: !!on } });
  }

  // The one system prompt. Resolves to the permission it ended on.
  function turnOn() {
    if (!P) return Promise.resolve('denied');
    return P.requestPermissions().then(function (r) {
      var s = r && r.receive;
      if (s !== 'granted') return s === 'denied' ? 'denied' : 'prompt';
      return register().then(function () { return setOn(true); }).then(function () { return 'granted'; });
    });
  }

  listen();
  var ready = permission().then(function (state) {
    if (state === 'granted') register();
    return state;
  });

  window.PomonaPush = {
    available: !!P,
    ready: ready,
    permission: permission,
    turnOn: turnOn,
    setOn: setOn,
    openPath: openPath
  };
})();
