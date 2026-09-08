/*
 * The line under the spinner while a week is being planned.
 *
 * Generating a week is a genuine ~30-second call, and until now every
 * screen that made one showed either a static label ("Drafting your
 * week…") or nothing but a disabled button. This is the one shared
 * component that fills that time with something true — a short line that
 * changes every few seconds, saying what is actually being done.
 *
 * HONESTY IS THE WHOLE POINT. These are not fake progress steps and there
 * is no percentage anywhere: each line names something the generation
 * really does with the household's own data. The stages below are keyed to
 * the only two progress signals the SSE stream actually carries (see
 * _stream_week_generation in app/main.py):
 *
 *   'reading' — the request is out and the "status" frame has landed, but
 *               no day has. The server is assembling household context:
 *               attendance, member dietary restrictions, current and
 *               near-expiring inventory, saved recipes, per-person taste
 *               (see _generate_weekly_plan's context dict in app/agent.py).
 *   'filling' — "day" frames are arriving, one per meal the model decides.
 *               Everything in this stage's lines is in the context block
 *               the model is writing from, so each is true for the whole
 *               stage rather than at one instant in it.
 *   'generic' — the fallback, and what a non-streaming caller gets (the
 *               Meals "Try again" rebuild posts to the plain /generate).
 *
 * A third real stage exists on the server — after the last day the plan is
 * saved, run through the allergy check (_log_plan_conflicts), and given a
 * prep schedule and defrost tasks — but the stream emits nothing to mark
 * it, and guessing at it from a gap between day frames would be inventing
 * progress. If those get their own "status" frames later, add a stage here
 * and key it to them; don't fake it in the meantime.
 *
 * Two things are deliberately NOT said: nothing about a grocery list (the
 * list is only written when the household APPROVES the week, not when it
 * is drafted — see add_ingredients_to_grocery_list in app/tools/weekly_plan.py,
 * so a line about it here would be a promise the draft doesn't keep), and
 * nothing about how far along it is.
 *
 * Styling lives in one place too: `.waiting-line` in static/theme.css, the
 * one stylesheet all three of these pages load.
 */
(function (global) {
  'use strict';

  // ~4s: long enough to read a line without hurrying, short enough that a
  // 30-second wait shows most of a stage rather than one line twice.
  var ROTATE_MS = 4000;

  /*
   * The lines. Voice rules that shaped them (DESIGN_SYSTEM.md §8): warm and
   * a little playful, every word earning its place, no exclamation marks
   * and no emoji (this is a wait, not a celebration), and the allergy line
   * stays plain because safety copy always does.
   */
  var STAGE_LINES = {
    reading: [
      'Reading back what you told me about your week.',
      'Looking at who’s home which nights.',
      'Checking what’s already in your kitchen.',
    ],
    filling: [
      'Picking a few dinners worth looking forward to.',
      'Filling out the plates, not just the mains.',
      'The allergy list is in front of me the whole way.',
      'Leaving the busy nights something quick.',
      'Working in what needs using up first.',
      'Giving the things nobody here eats a wide berth.',
    ],
    generic: [
      'Putting your week together.',
      'This part takes about half a minute.',
    ],
  };

  /*
   * Never returns undefined, whatever it is handed — an unknown stage name,
   * null, a number, a stage whose list someone emptied. A wait screen that
   * throws is strictly worse than one that says something slightly generic.
   */
  function waitingLinesForStage(stage) {
    var lines = STAGE_LINES[stage];
    if (Object.prototype.toString.call(lines) !== '[object Array]' || !lines.length) {
      return STAGE_LINES.generic;
    }
    return lines;
  }

  /*
   * Rotate lines into `el` until stopped.
   *
   * Returns a controller — { setStage, stop } — so the caller can move
   * stages as real events land (setStage restarts at the top of the new
   * stage rather than continuing an index into a different list) and stop
   * when the wait is over. Calling it with no element, or stopping twice,
   * is a no-op rather than an error: this runs on the unhappy paths too.
   */
  function startWaitingLines(el, options) {
    var opts = options || {};
    var intervalMs = opts.intervalMs > 0 ? opts.intervalMs : ROTATE_MS;
    var lines = waitingLinesForStage(opts.stage);
    var idx = 0;
    var timer = null;

    function paint() {
      if (!el) return;
      el.textContent = lines[idx % lines.length];
      // Restart the fade on every swap. Removing the class, forcing a
      // reflow, then re-adding it is the only way to replay a CSS
      // animation on an element that never leaves the DOM.
      el.classList.remove('waiting-line-in');
      void el.offsetWidth;
      el.classList.add('waiting-line-in');
    }

    function tick() {
      idx += 1;
      paint();
    }

    function stop() {
      if (timer !== null) {
        global.clearInterval(timer);
        timer = null;
      }
    }

    function setStage(stage) {
      var next = waitingLinesForStage(stage);
      if (next === lines) return;
      lines = next;
      idx = 0;
      paint();
    }

    if (!el) return { setStage: function () {}, stop: function () {} };

    el.classList.add('waiting-line');
    paint();
    timer = global.setInterval(tick, intervalMs);
    return { setStage: setStage, stop: stop };
  }

  var api = {
    ROTATE_MS: ROTATE_MS,
    STAGE_LINES: STAGE_LINES,
    waitingLinesForStage: waitingLinesForStage,
    startWaitingLines: startWaitingLines,
  };

  global.PomonaWaiting = api;
  // So tests/test_waiting_lines.py can require this file under node and
  // check the stage mapping directly. No page uses it.
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
