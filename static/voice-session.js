// Phase 5 (Voice) — shared hands-free voice engine, reused by both the
// Cooker (cooker.html) and Shopper (grocery.html) cases per the PRD's
// explicit instruction not to build two parallel implementations.
//
// Design, matching the PRD's resolved decisions:
// - Session-based activation (deliberate tap to start/stop), NOT
//   always-on/wake-word — see PRD §6.
// - An in-session trigger phrase ("hey home manager") distinguishes a real
//   command from ambient conversation, rather than treating everything
//   heard as an instruction — see PRD §4.1.
// - recognition.continuous is deliberately left FALSE, with the engine
//   manually restarting recognition after each utterance, rather than
//   relying on continuous=true. This is a direct response to the Phase 5
//   technical spike (see README's Phase 5 section): continuous mode is
//   specifically flaky on iOS Safari (mic not stopping, no results),
//   while short restart-per-utterance recognition is documented as more
//   stable there.
// - Spoken confirmation via the browser-native SpeechSynthesis API
//   (PRD §5, decided — no new backend dependency).
// - A confirm/repeat fallback for low-confidence or failed recognition,
//   and a clear status callback for the visual listening indicator, per
//   PRD §4.3 (reliability & fallback is cross-cutting, not a polish step).
//
// Known limitation (flagged, not silently hidden): the Phase 5 technical
// spike found that BOTH the Web Speech API and MediaRecorder-based audio
// capture have documented reliability problems specifically when this PWA
// is installed to an iOS home screen (standalone display mode) rather than
// used as a regular Safari tab. isStandaloneIOS() below lets the calling
// page surface a heads-up about this rather than have the feature just
// silently fail with no explanation.

const VOICE_TRIGGER_PHRASE = 'hey home manager';
const VOICE_SESSION_TIMEOUT_MS = 5 * 60 * 1000; // end session after 5 min of no recognized command

function createVoiceSession(opts) {
  // opts:
  //   onListeningChange(isListening: bool) — update the visual mic indicator
  //   onStatus(text: string) — status/transcript feed for the visual log
  //   onCommand(commandText: string) -> Promise<{spoken: string, endSession?: bool} | null>
  //     Return null (or throw) for "didn't understand" — the engine handles
  //     the confirm/repeat fallback itself; the caller only needs to parse
  //     recognized commands and report back what to say.
  //   onEnd() — session fully stopped (tap, spoken end phrase, timeout, or error)
  const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
  const supported = !!SpeechRecognitionCtor && !!window.speechSynthesis;

  let recognition = null;
  let active = false;
  let restarting = false;
  let inactivityTimer = null;

  function speak(text) {
    if (!window.speechSynthesis) return;
    try {
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      window.speechSynthesis.speak(utter);
    } catch (err) {
      // TTS is a nicety for hands-free confirmation, not load-bearing —
      // the visual status callback already carries the same information,
      // so a synthesis failure here should never block the feature.
    }
  }

  function resetInactivityTimer() {
    if (inactivityTimer) clearTimeout(inactivityTimer);
    inactivityTimer = setTimeout(() => {
      if (active) {
        opts.onStatus && opts.onStatus('Session ended — no activity for a while.');
        stop();
      }
    }, VOICE_SESSION_TIMEOUT_MS);
  }

  function stripTrigger(transcript) {
    const t = transcript.trim().toLowerCase();
    const idx = t.indexOf(VOICE_TRIGGER_PHRASE);
    if (idx === -1) return null; // no trigger phrase heard — ambient talk, ignore entirely
    let rest = t.slice(idx + VOICE_TRIGGER_PHRASE.length).trim();
    rest = rest.replace(/^[,.\s]+/, '');
    return rest;
  }

  async function handleResult(transcript) {
    resetInactivityTimer();
    const command = stripTrigger(transcript);
    if (command === null) return; // no trigger phrase — not directed at the app, don't react
    if (!command) {
      opts.onStatus && opts.onStatus('Heard the trigger phrase but no command after it — go ahead.');
      speak("I'm listening, go ahead.");
      return;
    }
    opts.onStatus && opts.onStatus('Heard: "' + command + '"');
    let result = null;
    try {
      result = await opts.onCommand(command);
    } catch (err) {
      result = null;
    }
    if (result && result.spoken) {
      speak(result.spoken);
      opts.onStatus && opts.onStatus(result.spoken);
      if (result.endSession) stop();
    } else {
      speak("Didn't catch that, try again.");
      opts.onStatus && opts.onStatus('Didn’t catch that — try "hey home manager" plus a command again.');
    }
  }

  function startRecognitionInstance() {
    recognition = new SpeechRecognitionCtor();
    recognition.lang = 'en-US';
    // Deliberately NOT continuous — see the module header for why.
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.onresult = (event) => {
      const transcript = event.results[event.results.length - 1][0].transcript;
      handleResult(transcript);
    };

    recognition.onerror = (event) => {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        opts.onStatus && opts.onStatus('Microphone permission was denied — allow mic access to use hands-free voice.');
        stop();
        return;
      }
      // 'no-speech' and 'aborted' are routine (silence between utterances,
      // or a deliberate restart) — not worth surfacing as errors.
      if (event.error !== 'no-speech' && event.error !== 'aborted') {
        opts.onStatus && opts.onStatus('Voice error (' + event.error + ') — still listening.');
      }
    };

    recognition.onend = () => {
      if (!active || restarting) return;
      restarting = true;
      // A short delay avoids a tight restart loop if recognition keeps
      // failing instantly (e.g. no mic available) — see PRD §5/§8 spike
      // notes on iOS Safari's continuous-mode instability, which is the
      // whole reason this engine restarts per-utterance instead of
      // relying on continuous=true in the first place.
      setTimeout(() => {
        restarting = false;
        if (active) {
          try { recognition.start(); } catch (err) { /* already running — ignore */ }
        }
      }, 250);
    };

    try {
      recognition.start();
    } catch (err) {
      opts.onStatus && opts.onStatus("Couldn't start listening — try again.");
    }
  }

  function isStandaloneIOS() {
    return window.navigator.standalone === true;
  }

  function start() {
    if (!supported) {
      opts.onStatus && opts.onStatus('Hands-free voice isn’t supported in this browser.');
      return false;
    }
    active = true;
    resetInactivityTimer();
    startRecognitionInstance();
    opts.onListeningChange && opts.onListeningChange(true);
    speak('Hands-free on. Say hey home manager, then a command.');
    return true;
  }

  function stop() {
    if (!active && !recognition) return;
    active = false;
    if (inactivityTimer) clearTimeout(inactivityTimer);
    if (recognition) {
      try { recognition.stop(); } catch (err) { /* ignore */ }
    }
    opts.onListeningChange && opts.onListeningChange(false);
    opts.onEnd && opts.onEnd();
  }

  return { start, stop, isActive: () => active, supported, isStandaloneIOS, speak };
}

window.createVoiceSession = createVoiceSession;
