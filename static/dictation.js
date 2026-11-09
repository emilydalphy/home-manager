// Shared voice-dictation helper for any text input or textarea + mic button
// pair: the chat composer (shell.js), Kitchen's "Say it" sheet
// (kitchen-add.js), inventory.html and onboarding.html all use this one.
//
// Usage: setupDictation(inputEl, micButtonEl, opts)
//   opts.onMessage(msg) — show a user-facing line (errors, the keyboard
//                         hint). Defaults to alert().
//   opts.onStart()      — the person tapped the mic to talk (the chat warms
//                         its briefing here).
//   opts.onText()       — dictation just changed input.value. Setting .value
//                         fires no 'input' event, so a page that grows its
//                         box or enables a button on input listens here.
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
//    iOS). Safari's Web Speech API runs Apple's OLD recognizer, stops at the
//    first pause and drops words, so on iOS it is never used. The mic
//    button opens the keyboard instead, and the keyboard's own mic key is
//    Apple's good dictation (keeps listening, adds punctuation). A page
//    can't press that key for the person, so the first tap says where it is.
// 3. 'web' — everywhere else with the Web Speech API (desktop and Android
//    Chrome). Desktop listens until the mic is tapped again
//    (continuous); Android stays one phrase at a time, because continuous
//    mode there repeats words.
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

  function isAndroid() {
    return /Android/i.test(userAgent());
  }

  // The iPhone app's speech plugin, or null on the website. Looked up the
  // same way static/push.js finds the push plugin.
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
    if (!input || !micBtn) return;
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
    var partialHandle = null;
    var endHandle = null;
    if (plugin) {
      nativeAvailable(plugin).then(function (ok) {
        if (!ok) return;
        mode = 'native';
        // The plugin sends the whole text so far each time, never a piece.
        Promise.resolve(plugin.addListener('partial', function (data) {
          if (listening) writeSpoken(data && data.text);
        })).then(function (h) { partialHandle = h; }).catch(function () {});
        // It stopped by itself (a call came in, the app went to the
        // background, or the two-minute cap).
        Promise.resolve(plugin.addListener('ended', function (data) {
          if (!listening) return;
          if (data && typeof data.text === 'string') writeSpoken(data.text);
          showListening(false);
        })).then(function (h) { endHandle = h; }).catch(function () {});
      });
    }

    function nativeTap() {
      if (listening) {
        showListening(false);
        Promise.resolve(plugin.stop())
          .then(function (res) { if (res && typeof res.text === 'string') writeSpoken(res.text); })
          .catch(function () {})
          .then(function () { input.focus(); });
        return;
      }
      baseValue = input.value.trim();
      onStart();
      showListening(true);
      Promise.resolve(plugin.start()).catch(function (err) {
        showListening(false);
        var code = err && (err.code || err.message);
        if (code === 'denied') {
          showMessage(MIC_BLOCKED);
          return;
        }
        // Anything else: the new engine isn't usable on this phone after
        // all. Use the keyboard's mic from here on rather than fail again.
        mode = 'keyboard';
        keyboardTap();
      });
    }

    // ---- 3. web: the browser's Web Speech API ----
    var recognition = null;
    function webRecognition() {
      if (recognition) return recognition;
      recognition = new SpeechRecognitionCtor();
      recognition.continuous = !isAndroid();
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
        writeSpoken(finalText + interimText);
      };
      recognition.onerror = function (e) {
        showListening(false);
        if (e.error === 'aborted') return; // the person stopped it, not a real error
        var messages = {
          'not-allowed': "Microphone access is blocked for this site — check your browser's site settings (usually the icon left of the address bar) and allow the microphone, then try again.",
          'service-not-allowed': "Microphone access is blocked for this site — check your browser's site settings (usually the icon left of the address bar) and allow the microphone, then try again.",
          'audio-capture': 'No microphone found — check that one\'s connected and selected as your input device.',
          'no-speech': "Didn't catch anything — try again and speak right after tapping the mic.",
          'network': 'Voice dictation needs an internet connection — check your connection and try again.'
        };
        showMessage(messages[e.error] || ('Voice dictation error: ' + e.error));
      };
      recognition.onend = function () {
        showListening(false);
        input.focus();
      };
      return recognition;
    }

    function webTap() {
      var rec = webRecognition();
      if (listening) {
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

    micBtn.addEventListener('click', function () {
      if (mode === 'native') nativeTap();
      else if (mode === 'web') webTap();
      else keyboardTap();
    });

    // For a page that throws its box away (a sheet closing): stop listening
    // and let go of the plugin's listeners.
    return {
      mode: function () { return mode; },
      stop: function () {
        if (!listening) return;
        if (mode === 'native') nativeTap();
        else if (mode === 'web' && recognition) recognition.stop();
      },
      dispose: function () {
        if (partialHandle && typeof partialHandle.remove === 'function') partialHandle.remove();
        if (endHandle && typeof endHandle.remove === 'function') endHandle.remove();
      }
    };
  };
})();
