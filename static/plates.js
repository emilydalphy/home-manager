/*
 * Drawn plates: the one way a screen shows one.
 *
 *   Plates.html(key, size)                   an <img>, or '' with no key
 *   Plates.html(key, size, { onDark: true }) no shadow, for dusk blue
 *   Plates.url(key)                          the drawing's address
 *
 * `key` is the `plate` the API puts on a meal row or a recipe
 * (app/tools/plate_drawings.py). The drawings are static/plates/<key>.svg,
 * so the browser caches each one and a page lists only keys. The soft shadow
 * lives on .dish-plate in shell.css. A drawing that fails to load falls back
 * to the neutral plate, and then to nothing: never a broken image.
 */
var Plates = (function () {
  var NEUTRAL = 'neutral';

  function url(key) {
    var path = '/static/plates/' + encodeURIComponent(key) + '.svg';
    return typeof Api !== 'undefined' ? Api.url(path) : path;
  }

  function html(key, size, opts) {
    if (!key) return '';
    var px = Math.round(Number(size)) || 44;
    return '<img class="dish-plate' + (opts && opts.onDark ? ' on-dark' : '') + '" src="' + url(key) + '" ' +
      'width="' + px + '" height="' + px + '" alt="" loading="lazy" decoding="async">';
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('error', function (event) {
      var img = event.target;
      if (!img || !img.classList || !img.classList.contains('dish-plate')) return;
      if (img.dataset.fellBack) { img.style.visibility = 'hidden'; return; }
      img.dataset.fellBack = '1';
      img.src = url(NEUTRAL);
    }, true);
  }

  return { url: url, html: html };
})();
