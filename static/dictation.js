// Shared voice-dictation helper (Web Speech API) for any text input or
// textarea + mic button pair. Extracted from shell.js's ask composer so
// grocery.html/inventory.html/memory.html can reuse the same dictation
// behavior instead of each re-implementing it — those pages are standalone
// (loaded directly or in an iframe, not part of shell.js's single bundle),
// so this needs to be its own file they can each <script src> in.
//
// Usage: setupDictation(inputEl, micButtonEl, { onMessage }). `onMessage`
// is called with a user-facing string for errors/hints — pass the host
// page's own toast function if it has one; defaults to alert().
(function () {
  var SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;

  window.setupDictation = function setupDictation(input, micBtn, opts) {
    if (!input || !micBtn) return;
    opts = opts || {};
    var showMessage = opts.onMessage || function (msg) { alert(msg); };
    var originalPlaceholder = input.placeholder;
    var recognition = null;
    var recognizing = false;
    var dictationBaseValue = '';

    if (SpeechRecognitionCtor) {
      recognition = new SpeechRecognitionCtor();
      recognition.continuous = false;
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
        var spoken = (finalText + interimText).trim();
        input.value = dictationBaseValue ? (dictationBaseValue + ' ' + spoken) : spoken;
      };
      recognition.onerror = function (e) {
        recognizing = false;
        micBtn.classList.remove('active');
        input.placeholder = originalPlaceholder;
        if (e.error === 'aborted') return; // user-initiated stop, not a real error
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
        recognizing = false;
        micBtn.classList.remove('active');
        input.placeholder = originalPlaceholder;
        input.focus();
      };

      micBtn.addEventListener('click', function () {
        if (recognizing) {
          recognition.stop();
          return;
        }
        dictationBaseValue = input.value.trim();
        recognizing = true;
        micBtn.classList.add('active');
        input.placeholder = 'Listening... tap the mic again to stop';
        try {
          recognition.start();
        } catch (err) {
          recognizing = false;
          micBtn.classList.remove('active');
          input.placeholder = originalPlaceholder;
          showMessage('Could not start voice dictation: ' + err.message);
        }
      });
    } else {
      // No Web Speech API (e.g. iOS Safari) — the device's own keyboard
      // mic still works once the field is focused, same fallback shell.js
      // uses for its composer.
      micBtn.addEventListener('click', function () {
        input.focus();
        if (!micBtn.dataset.hinted) {
          showMessage('Tap the microphone icon on your keyboard to dictate.');
          micBtn.dataset.hinted = '1';
        }
      });
    }
  };
})();
