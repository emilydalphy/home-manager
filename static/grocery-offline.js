// The grocery list's copy on the phone, and the ticks made while there was
// no signal (Loop Board: "Grocery: keep working in a store with no signal",
// Emily 2026-09-10).
//
// Two things live in localStorage, keyed per household:
//
//   pomona.grocery.copy.h<id>   the list exactly as the server last sent it
//   pomona.grocery.queue.h<id>  status changes made since, oldest first
//   pomona.grocery.shops.h<id>  the household's usual shops, and whether
//                               the "where do you shop?" card was answered
//
// The third is there because the list alone is not enough to shop from:
// the Shop screen reads the household's shops from /api/memory to know
// which stops a trip has, and whether to show the first-visit shops card
// INSTEAD of the list. With no signal that request fails like any other,
// and without this the screen would answer "no shops named yet" and put
// the card up over a perfectly good list.
//
// The copy is server truth and nothing else; the queue is what this device
// did on top of it. What the screen shows is always copy + queue
// (applyPending), so a tick made in aisle four is on the screen the moment
// it is made, survives a reload, and is sent when the connection comes
// back — in the order it was made, one item's LAST change only (a tick
// then an untick sends "needed" once, which is last-write-wins and the
// honest rule for a shopping list).
//
// Replay's one rule: an op is spent the moment the server ANSWERS, whatever
// it answers. A 404 means the row is gone (a partner removed it) and
// sending it again would not bring it back; a 500 means the same request
// will fail the same way. Only a request the server never saw — fetch
// threw — stays queued, and the queue stops there so order holds. The
// status route is idempotent (see tools.mark_grocery_item), so a request
// whose reply was lost in the store's dead zone is safe to send twice.
//
// This file has no DOM and no fetch of its own: shell.js hands it storage
// and a post function, which is also what lets tests/test_grocery_offline.py
// run it under node exactly as the browser does. Anything that needs Claude
// is out of scope here on purpose — a tick is the only thing a person in a
// store needs to do without signal.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PomonaGroceryOffline = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var SECTION_ORDER = ['produce', 'dairy', 'meat/seafood', 'pantry', 'frozen', 'other'];
  var COPY_PREFIX = 'pomona.grocery.copy.h';
  var QUEUE_PREFIX = 'pomona.grocery.queue.h';
  var SHOPS_PREFIX = 'pomona.grocery.shops.h';
  var HOUSEHOLD_KEY = 'pomona.grocery.household';
  var STATUSES = ['needed', 'in_cart', 'purchased'];

  // Move one item between the buckets of the list shape shell.js renders
  // (groLoadAllData: storeName -> { sections, purchased, inCart }). Mutates
  // and returns `data`. Unknown ids are left alone: the row may have been
  // removed by someone else, and inventing it would put a ghost on the list.
  function applyStatus(data, itemId, status) {
    if (!data || !data.stores || STATUSES.indexOf(status) === -1) return data;
    var id = String(itemId);
    var names = Object.keys(data.stores);
    var found = null, home = null;
    for (var n = 0; n < names.length && !found; n++) {
      var s = data.stores[names[n]];
      var secs = s.sections || [];
      for (var i = 0; i < secs.length && !found; i++) {
        var items = secs[i].items || [];
        for (var j = 0; j < items.length; j++) {
          if (String(items[j].id) === id) { found = items.splice(j, 1)[0]; home = names[n]; break; }
        }
        if (!items.length) secs.splice(i, 1);
      }
      if (found) break;
      var buckets = ['inCart', 'purchased'];
      for (var b = 0; b < buckets.length && !found; b++) {
        var list = s[buckets[b]] || [];
        for (var k = 0; k < list.length; k++) {
          if (String(list[k].id) === id) { found = list.splice(k, 1)[0]; home = names[n]; break; }
        }
      }
    }
    if (!found) return data;
    found.status = status;
    var store = data.stores[home];
    if (status === 'in_cart') store.inCart.push(found);
    else if (status === 'purchased') store.purchased.push(found);
    else insertNeeded(store, found);
    return data;
  }

  // Back onto the list, in its aisle, in section order — so an untick puts
  // the row where the server would have put it, not at the bottom.
  function insertNeeded(store, item) {
    var cat = SECTION_ORDER.indexOf(item.category) === -1 ? 'other' : item.category;
    store.sections = store.sections || [];
    for (var i = 0; i < store.sections.length; i++) {
      if (store.sections[i].section === cat) { store.sections[i].items.push(item); return; }
    }
    var rank = SECTION_ORDER.indexOf(cat);
    var at = store.sections.length;
    for (var j = 0; j < store.sections.length; j++) {
      if (SECTION_ORDER.indexOf(store.sections[j].section) > rank) { at = j; break; }
    }
    store.sections.splice(at, 0, { section: cat, items: [item] });
  }

  function clone(v) { return JSON.parse(JSON.stringify(v)); }

  function create(opts) {
    opts = opts || {};
    var storage = opts.storage || null;
    var now = opts.now || function () { return Date.now(); };

    // Every read and write is wrapped: Safari in private mode throws on
    // localStorage rather than returning null, and a thrown save would
    // take the tick down with it. With no storage at all the queue still
    // works for this page view — it just doesn't survive a reload.
    var memory = {};
    function read(key) {
      try { if (storage) return storage.getItem(key); } catch (err) { /* fall through */ }
      return Object.prototype.hasOwnProperty.call(memory, key) ? memory[key] : null;
    }
    function write(key, value) {
      memory[key] = value;
      try { if (storage) storage.setItem(key, value); } catch (err) { /* memory copy stands */ }
    }
    function remove(key) {
      delete memory[key];
      try { if (storage) storage.removeItem(key); } catch (err) { /* nothing to do */ }
    }
    function readJson(key) {
      var raw = read(key);
      if (!raw) return null;
      try { return JSON.parse(raw); } catch (err) { return null; }
    }

    // Which household this device last heard from. Learned from the server
    // when there is one (shell.js passes /api/coaching's household_id along)
    // and remembered, because offline there is no server to ask — and
    // offline is exactly when the key matters.
    var householdId = opts.householdId != null ? String(opts.householdId) : (read(HOUSEHOLD_KEY) || 'x');
    function copyKey() { return COPY_PREFIX + householdId; }
    function queueKey() { return QUEUE_PREFIX + householdId; }
    function shopsKey() { return SHOPS_PREFIX + householdId; }

    function pending() {
      var q = readJson(queueKey());
      return Array.isArray(q) ? q : [];
    }
    function writePending(q) {
      if (q.length) write(queueKey(), JSON.stringify(q));
      else remove(queueKey());
    }

    var replaying = null;

    var api = {
      household: function () { return householdId; },
      setHousehold: function (id) {
        if (id == null) return;
        householdId = String(id);
        write(HOUSEHOLD_KEY, householdId);
      },

      saveList: function (data) {
        if (!data || !data.stores) return;
        write(copyKey(), JSON.stringify({ savedAt: now(), data: data }));
      },
      readList: function () {
        var saved = readJson(copyKey());
        if (!saved || !saved.data || !saved.data.stores) return null;
        return saved;
      },
      clearList: function () { remove(copyKey()); },

      // { usualStores: [...], dismissed: bool } — what /api/memory answers
      // about shops, for the reload that can't ask it.
      saveShops: function (shops) {
        if (!shops || !Array.isArray(shops.usualStores)) return;
        write(shopsKey(), JSON.stringify({ usualStores: shops.usualStores, dismissed: !!shops.dismissed }));
      },
      readShops: function () {
        var saved = readJson(shopsKey());
        return saved && Array.isArray(saved.usualStores) ? saved : null;
      },

      pending: pending,
      hasPending: function () { return pending().length > 0; },

      // One entry per item: a newer change to the same row replaces the
      // older one and goes to the end, so across items the order is the
      // order the shopper made them.
      queueStatus: function (itemId, status) {
        if (STATUSES.indexOf(status) === -1) return null;
        var q = pending().filter(function (op) { return String(op.id) !== String(itemId); });
        var op = { id: String(itemId), status: status, at: now() };
        q.push(op);
        writePending(q);
        return op;
      },

      // The list as the shopper last saw it: the server's copy with this
      // device's unsent changes on top. Never mutates the stored copy.
      applyPending: function (data) {
        var out = clone(data);
        pending().forEach(function (op) { applyStatus(out, op.id, op.status); });
        return out;
      },
      applyStatus: applyStatus,

      // post(url, body) -> Promise resolving to { ok, status } once the
      // server answered, rejecting only when it never did. Sequential, one
      // replay at a time; a call made while one is running waits for it
      // and then makes a fresh attempt of its own — never a race, and never
      // a stale "no signal" answer handed to the caller who just got one.
      replay: function (post) {
        if (replaying) return replaying.then(function () { return api.replay(post); }, function () { return api.replay(post); });
        var result = { sent: 0, dropped: 0, kept: 0 };
        replaying = (function step() {
          var q = pending();
          if (!q.length) return Promise.resolve(result);
          var op = q[0];
          return Promise.resolve()
            .then(function () { return post('/api/grocery-list/' + op.id + '/status', { status: op.status }); })
            .then(function (res) {
              // Answered: spent, whichever way it went.
              writePending(pending().filter(function (o) { return !(o.id === op.id && o.at === op.at); }));
              if (res && res.ok) result.sent += 1; else result.dropped += 1;
              return step();
            }, function () {
              // Never reached the server. Stop here; order holds.
              result.kept = pending().length;
              return result;
            });
        })().then(function (r) { replaying = null; return r; }, function (err) { replaying = null; throw err; });
        return replaying;
      }
    };
    return api;
  }

  return { create: create, applyStatus: applyStatus, SECTION_ORDER: SECTION_ORDER };
});
