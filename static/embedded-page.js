/*
 * The shell's shared guard for pages it embeds in an iframe.
 *
 * Why this exists: every top-level route (/, /meals, /grocery, /kitchen)
 * serves shell.html. So an ordinary link to one of those, followed from
 * inside an embedded page, loads a *second complete app shell* inside the
 * frame — two ask bars and two tab bars stacked. That bug has been
 * reported three separate times.
 *
 * The guard itself is four lines. The problem was never the logic, it was
 * that the logic lived hand-copied in four different pages, so a new
 * embedded page (or a new link on an existing one) shipped the bug
 * silently — it looks correct opened on its own and only breaks inside the
 * shell. This file plus the smoke test in tests/test_embedded_pages.py
 * turns "someone has to remember" into a test failure.
 *
 * Usage: include this script, then mark the offending link with
 * data-shell-back:
 *
 *   data-shell-back="hide"
 *       Hide the element when embedded. For pages where the shell's own
 *       tab bar is already the way back, so the link is redundant rather
 *       than wrong (grocery, cooker).
 *
 *   data-shell-back="/static/kitchen.html"
 *       Rewrite the link to point at that URL when embedded — a sibling
 *       page that loads *inside* the frame instead of a top-level route
 *       that would load another shell (inventory, memory, which are
 *       pushed views within the Kitchen tab and do need a way back).
 *
 * The two behaviours differ on purpose and are not interchangeable; the
 * attribute is what lets one file serve both.
 *
 * Opened standalone (not in a frame) every page is left exactly as
 * authored — the links are correct there, and that is a real path: the
 * desktop rail links to several of these pages directly.
 */
(function () {
  if (window.self === window.top) return;

  var marked = document.querySelectorAll('[data-shell-back]');
  for (var i = 0; i < marked.length; i++) {
    var el = marked[i];
    var mode = el.getAttribute('data-shell-back');

    if (mode === 'hide') {
      el.style.display = 'none';
      continue;
    }

    // Anything else is a replacement href. Accept the attribute either on
    // the anchor itself or on a wrapper around it, since the existing
    // markup does both.
    var anchor = el.tagName === 'A' ? el : el.querySelector('a[href]');
    if (anchor) anchor.setAttribute('href', mode);
  }
})();
