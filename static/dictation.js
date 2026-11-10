// Shared voice-dictation helper for any text input or textarea + mic button
// pair: the chat composer (shell.js), Kitchen's "Say it" sheet
// (kitchen-add.js), inventory.html and onboarding.html all use this one.
//
// Usage: var mic = setupDictation(inputEl, micButtonEl, opts)
//   opts.onMessage(msg) — show a user-facing line (errors, the keyboard
//                         hint). Defaults to alert().
//   opts.onStart()      — the person tapped the mic to talk (the chat warms
//                         its briefing here).
//   opts.onText()       — dictation just changed input.value. Setting .value
//                         fires no 'input' event, so a page that grows its
//                         box or enables a button on input listens here.
// It returns { mode(), cancel(), dispose() }:
//   cancel()  — stop listening and throw away anything still to come. The
//               chat calls it on Send (otherwise the next words to arrive
//               would put the sent message back in the box), and a sheet
//               calls it when it closes or redraws.
//   dispose() — cancel, and let go of the iPhone app's listeners. For a box
//               that is being thrown away.
//
// Three ways to turn speech into text, picked per device (voice research,
// 2026-11-09 — Emily: "it's almost unusable it's so bad"):
//
// 1. 'native' — Pomona's iPhone app, on iOS 26 or newer. The app ships its
//    own speech plugin (ios-app/ios/App/App/PomonaSpeechPlugin.swift) on
//    Apple's new on-device engine (SpeechAnalyzer), roughly a quarter of the
//    old engine's mistakes. Free, nothing leaves the phone, and it keeps
//    listening through pauses until the mic is tapped again.
// 2. 'keyboard' — any other iPhone or iPad (Safari, or the app on an older
//    iOS or an older app build without the plugin). Safari's Web Speech API
//    runs Apple's OLD recognizer, stops at the first pause and drops words,
//    so on iOS it is never used. The mic button opens the keyboard instead,
//    and the keyboard's own mic key is Apple's good dictation (keeps
//    listening, adds punctuation). A page can't press that key for the
//    person, so the first tap says where it is.
// 3. 'web' — everywhere else with the Web Speech API. Desktop Chrome/Edge
//    listen until the mic is tapped again (continuous); Android and desktop
//    Safari stay one phrase at a time (continuous mode repeats words on
//    Android and is unreliable in Safari).
(function () {
  var SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
  var LISTENING_PLACEHOLDER = 'Listening… tap the mic again to stop';
  var KEYBOARD_HINT = 'Tap the mic on your keyboard and start talking.';
  var MIC_BLOCKED = "Pomona can't use the microphone. Turn it on in your phone's Settings, under Pomona.";

  function userAgent() {
    return (window.navigator && window.navigator.userAgent) || '';
  }

  // Same test as shell.js's isIosDevice: iPadOS reports itself as a Mac,
  // so a "Macintosh" with touch counts as an iPad.
  function isIosDevice() {
    var ua = userAgent();
    return /iPhone|iPad|iPod/.test(ua) ||
      (/Macintosh/.test(ua) && typeof document !== 'undefined' && 'ontouchend' in document);
  }

  function continuousIsSafe() {
    var ua = userAgent();
    if (/Android/i.test(ua)) return false;
    // Desktop Safari: "Safari" without Chrome's or Edge's own tokens.
    if (/Safari\//.test(ua) && !/Chrome\/|Chromium\/|Edg\//.test(ua)) return false;
    return true;
  }

  // The iPhone app's speech plugin, or null on the website or in an app
  // build from before the plugin existed. Looked up the same way
  // static/push.js finds the push plugin.
  function nativePlugin() {
    try {
      var cap = window.Capacitor;
      if (!cap || typeof cap.isNativePlatform !== 'function' || !cap.isNativePlatform()) return null;
      if (typeof cap.isPluginAvailable === 'function' && !cap.isPluginAvailable('PomonaSpeech')) return null;
      if (cap.Plugins && cap.Plugins.PomonaSpeech) return cap.Plugins.PomonaSpeech;
      if (typeof cap.registerPlugin === 'function') return cap.registerPlugin('PomonaSpeech');
    } catch (err) { /* fall through to the website's ways */ }
    return null;
  }

  // One answer per page load: does this phone's app have the new engine?
  var nativeReady = null;
  function nativeAvailable(plugin) {
    if (!nativeReady) {
      nativeReady = Promise.resolve()
        .then(function () { return plugin.isAvailable(); })
        .then(function (res) { return !!(res && res.available); })
        .catch(function () { return false; });
    }
    return nativeReady;
  }

  window.setupDictation = function setupDictation(input, micBtn, opts) {
    if (!input || !micBtn) return null;
    opts = opts || {};
    var showMessage = opts.onMessage || function (msg) { alert(msg); };
    var onStart = opts.onStart || function () {};
    var onText = opts.onText || function () {};
    var originalPlaceholder = input.placeholder;
    var listening = false;
    var baseValue = '';
    var mode = isIosDevice() ? 'keyboard' : (SpeechRecognitionCtor ? 'web' : 'keyboard');

    function writeSpoken(spoken) {
      spoken = (spoken || '').trim();
      input.value = baseValue && spoken ? (baseValue + ' ' + spoken) : (baseValue || spoken);
      onText(input);
    }

    function showListening(on) {
      listening = on;
      if (on) micBtn.classList.add('active');
      else micBtn.classList.remove('active');
      input.placeholder = on ? LISTENING_PLACEHOLDER : originalPlaceholder;
    }

    // A sheet that redrew without telling us has thrown this box away.
    function boxIsGone() {
      return input.isConnected === false;
    }

    // ---- 2. keyboard: open the keyboard, say where its mic is once ----
    function keyboardTap() {
      input.focus();
      if (!micBtn.dataset.hinted) {
        showMessage(KEYBOARD_HINT);
        micBtn.dataset.hinted = '1';
      }
    }

    // ---- 1. native: the iPhone app's own engine ----
    var plugin = isIosDevice() ? nativePlugin() : null;
    var handles = [];
    // The plugin's session number for THIS box's current listen. Its
    // events carry the number, so words from an earlier listen (another
    // box, or one this box gave up on) are never written here.
    var session = null;
    // The tap whose start() is still on its way back. Each tap gets its own
    // token, so a late answer to an earlier tap is recognised as stale.
    var pendingStart = null;
    var disposed = false;

    function keep(h) {
      Promise.resolve(h).then(function (x) {
        if (!x) return;
        // Thrown away before the listener came back: let go at once.
        if (disposed) { if (typeof x.remove === 'function') x.remove(); return; }
        handles.push(x);
      }).catch(function () {});
    }

    // Stops only the named listen, never one another box has started since.
    function stopSession(s) {
      return Promise.resolve(plugin.stop({ session: s }));
    }

    if (plugin) {
      nativeAvailable(plugin).then(function (ok) {
        if (!ok || disposed) return;
        mode = 'native';
        // The plugin sends the whole text so far each time, never a piece.
        keep(plugin.addListener('partial', function (data) {
          if (!listening || !data || data.session !== session) return;
          if (boxIsGone()) { cancel(); return; }
          writeSpoken(data.text);
        }));
        // It stopped by itself (a call came in, the app went to the
        // background, or the two-minute cap).
        keep(plugin.addListener('ended', function (data) {
          if (!listening || !data || data.session !== session) return;
          if (typeof data.text === 'string' && !boxIsGone()) writeSpoken(data.text);
          session = null;
          showListening(false);
        }));
      });
    }

    function nativeTap() {
      if (listening) {
        var mine = session;
        session = null;
        showListening(false);
        if (mine == null) {
          // Tapped again before it got going: its start() answer is now
          // stale and stops that listen when it arrives.
          pendingStart = null;
          return;
        }
        stopSession(mine)
          .then(function (res) {
            if (res && res.session === mine && typeof res.text === 'string' && !boxIsGone()) writeSpoken(res.text);
          })
          .catch(function () {})
          .then(function () { if (!boxIsGone()) input.focus(); });
        return;
      }
      baseValue = input.value.trim();
      onStart();
      showListening(true);
      session = null;
      var token = {};
      pendingStart = token;
      Promise.resolve(plugin.start()).then(function (res) {
        var started = res && res.session;
        if (pendingStart !== token) {
          // Cancelled (Send, the sheet closed, or tapped again) before it
          // got going: stop exactly this listen, nobody else's.
          if (started != null) stopSession(started).catch(function () {});
          return;
        }
        pendingStart = null;
        session = started;
      }).catch(function (err) {
        if (pendingStart !== token) return; // already cancelled
        pendingStart = null;
        session = null;
        showListening(false);
        var code = err && (err.code || err.message);
        if (code === 'denied') {
          showMessage(MIC_BLOCKED);
          return;
        }
        if (code === 'stopped') return;
        // Anything else: the new engine isn't usable on this phone after
        // all. Use the keyboard's mic from here on rather than fail again.
        mode = 'keyboard';
        keyboardTap();
      });
    }

    // ---- 3. web: the browser's Web Speech API ----
    // A cancelled recognizer is dropped, and every handler checks it is
    // still the current one, so a tap right after Send starts a fresh one
    // instead of tripping over the old one still shutting down.
    var recognition = null;
    function webRecognition() {
      if (recognition) return recognition;
      var rec = new SpeechRecognitionCtor();
      recognition = rec;
      rec.continuous = continuousIsSafe();
      rec.interimResults = true;
      rec.lang = 'en-US';
      rec.onresult = function (e) {
        if (recognition !== rec || !listening) return; // cancelled: the rest is thrown away
        var finalText = '';
        var interimText = '';
        for (var i = 0; i < e.results.length; i++) {
          var result = e.results[i];
          if (result.isFinal) finalText += result[0].transcript;
          else interimText += result[0].transcript;
        }
        writeSpoken(finalText + interimText);
      };
      rec.onerror = function (e) {
        if (recognition !== rec) return;
        var wasListening = listening;
        showListening(false);
        if (e.error === 'aborted' || !wasListening) return; // stopped on purpose, not a real error
        var messages = {
          'not-allowed': "Microphone access is blocked for this site — check your browser's site settings (usually the icon left of the address bar) and allow the microphone, then try again.",
          'service-not-allowed': "Microphone access is blocked for this site — check your browser's site settings (usually the icon left of the address bar) and allow the microphone, then try again.",
          'audio-capture': 'No microphone found — check that one\'s connected and selected as your input device.',
          'no-speech': "Didn't catch anything — try again and speak right after tapping the mic.",
          'network': 'Voice dictation needs an internet connection — check your connection and try again.'
        };
        showMessage(messages[e.error] || ('Voice dictation error: ' + e.error));
      };
      rec.onend = function () {
        if (recognition !== rec) return;
        var wasListening = listening;
        showListening(false);
        if (wasListening && !boxIsGone()) input.focus();
      };
      return rec;
    }

    function webTap() {
      var rec = webRecognition();
      if (listening) {
        // Stays "listening" until onend, so the last words still land.
        rec.stop();
        return;
      }
      baseValue = input.value.trim();
      onStart();
      showListening(true);
      try {
        rec.start();
      } catch (err) {
        showListening(false);
        showMessage('Could not start voice dictation: ' + err.message);
      }
    }

    function cancel() {
      if (!listening) return;
      showListening(false);
      if (mode === 'native') {
        var mine = session;
        session = null;
        // A listen still starting is stopped when its start() comes back.
        pendingStart = null;
        if (mine != null) stopSession(mine).catch(function () {});
      } else if (mode === 'web' && recognition) {
        var rec = recognition;
        recognition = null;
        try {
          if (typeof rec.abort === 'function') rec.abort();
          else rec.stop();
        } catch (err) { /* already stopped */ }
      }
    }

    micBtn.addEventListener('click', function () {
      if (mode === 'native') nativeTap();
      else if (mode === 'web') webTap();
      else keyboardTap();
    });

    return {
      mode: function () { return mode; },
      cancel: cancel,
      dispose: function () {
        cancel();
        disposed = true;
        handles.forEach(function (h) { if (h && typeof h.remove === 'function') h.remove(); });
        handles = [];
      }
    };
  };
})();
