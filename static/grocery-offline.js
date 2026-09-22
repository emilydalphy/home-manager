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
// One more kind of op since 2026-09-21 (Loop Board 3e21f4c0): the "Yes,
// freezing it" answer under a just-ticked meat line, and its Put back —
//   { id, kind: 'freezing', answer: 'freezer' | 'fridge', at }
// posted to /api/grocery-list/<id>/freezing. It queues and replays exactly
// like a status change (one per line, latest wins, spent when the server
// answers), sits in the same queue so it goes out AFTER the tick it
// followed, and is idempotent on the server for the same reason the
// status route is. A status op and a freezing op for one line are two
// different ops — a put-back of the row never cancels the freezer answer
// (the server's "fridge" is what removes a move). applyPending ignores
// it: nothing on the list's shape changes with the answer.
//
// And one more since 2026-09-21 (Loop Board 3e31f4c0-5231-817d): a thing
// added from the "Add something" sheet with no signal —
//   { id, kind: 'add', item, quantity, category, store?, at }
// posted to /api/grocery-list/add (with remember: true when a store rides
// along). Its id is a local one ('add-<time>-<n>') until the server gives
// the line a real one on replay; applyPending draws it on the phone's
// copy under the store it was given (applyAdd), status needed, so the
// list shows it at once. One entry per add (no two adds are the same
// op), in the queue's order with the ticks. A tick on the local row is
// refused by the screen (isPendingAddId) — there is nothing on the
// server to tick yet — and Put back on the toast takes the add out of
// the queue again (unqueue). The route is not idempotent the way the
// status route is: a request whose reply was lost lands the line, and a
// resend merges into it (add_grocery_item consolidates same-name lines),
// which for a shopping list is the right side to err on.
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
  var FREEZING_ANSWERS = ['freezer', 'fridge'];
  var ADD_ID_PREFIX = 'add-';
  var UNASSIGNED = 'Unassigned';

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

  // A queued add drawn onto the list shape: a needed row under the store
  // it was given (the loose pile when it was given none), in its aisle,
  // carrying the op's local id. Mutates and returns `data`. A store the
  // list has no bucket for yet gets one — the card appears, which is what
  // the add asked for.
  function applyAdd(data, op) {
    if (!data || !data.stores || !op) return data;
    var name = op.store || UNASSIGNED;
    if (!data.stores[name]) data.stores[name] = { sections: [], purchased: [], inCart: [] };
    var already = findLine(data, op.id);
    if (already) return data;
    insertNeeded(data.stores[name], {
      id: String(op.id), item: op.item, quantity: op.quantity || '', category: op.category || 'other',
      store: op.store || '', store_decided: op.store === undefined ? 0 : 1, status: 'needed', pending: true
    });
    return data;
  }
  // Take one row off the list shape, wherever it sits. Mutates and returns
  // `data`; a row not on it is left alone.
  function removeLine(data, itemId) {
    if (!data || !data.stores) return data;
    var id = String(itemId);
    Object.keys(data.stores).forEach(function (n) {
      var s = data.stores[n];
      (s.sections || []).forEach(function (sec) {
        sec.items = (sec.items || []).filter(function (it) { return String(it.id) !== id; });
      });
      s.sections = (s.sections || []).filter(function (sec) { return sec.items.length; });
      ['inCart', 'purchased'].forEach(function (b) {
        s[b] = (s[b] || []).filter(function (it) { return String(it.id) !== id; });
      });
    });
    return data;
  }
  function findLine(data, itemId) {
    var id = String(itemId);
    var names = Object.keys(data.stores);
    for (var n = 0; n < names.length; n++) {
      var s = data.stores[names[n]];
      var secs = s.sections || [];
      for (var i = 0; i < secs.length; i++) {
        var items = secs[i].items || [];
        for (var j = 0; j < items.length; j++) if (String(items[j].id) === id) return items[j];
      }
    }
    return null;
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

    // Which household is signed in on this device. Learned from the server
    // (shell.js passes /api/coaching's household_id along) and remembered
    // for the reload that can't ask — but it is the SESSION's household,
    // not the last one seen: sign-out and any 401 call forget(), which
    // clears the pointer and every key, and a different answer from the
    // server purges the previous household's keys before anything is
    // written under the new one. While the household is unknown nothing is
    // read from or written to storage — the list a page fetched before
    // /api/coaching answered is held in memory and moved under the right
    // key the moment it does (setHousehold). Unknown + offline shows no
    // list at all: a guessed list is the wrong list on a shared phone.
    var householdId = opts.householdId != null ? String(opts.householdId) : (read(HOUSEHOLD_KEY) || null);
    function known() { return householdId != null; }
    function keyFor(prefix) { return prefix + (known() ? householdId : 'unknown'); }
    function copyKey() { return keyFor(COPY_PREFIX); }
    function queueKey() { return keyFor(QUEUE_PREFIX); }
    function shopsKey() { return keyFor(SHOPS_PREFIX); }

    // Storage only ever sees a known household's keys; unknown stays in
    // memory (the page-view fallback above).
    function readScoped(key) { return known() ? read(key) : (Object.prototype.hasOwnProperty.call(memory, key) ? memory[key] : null); }
    function writeScoped(key, value) { if (known()) write(key, value); else memory[key] = value; }
    function removeScoped(key) { if (known()) remove(key); else delete memory[key]; }
    function readJsonScoped(key) {
      var raw = readScoped(key);
      if (!raw) return null;
      try { return JSON.parse(raw); } catch (err) { return null; }
    }
    function purge(id) {
      remove(COPY_PREFIX + id); remove(QUEUE_PREFIX + id); remove(SHOPS_PREFIX + id);
    }

    // A queue entry is an object with an id and one of the three statuses
    // (a tick, kind absent or 'status'), one of the two freezing answers
    // (kind 'freezing'), or a named thing to add (kind 'add'); anything
    // else (a null from a bad write, a shape from an older build) is
    // dropped on read rather than left to jam replay forever.
    function isFreezing(op) { return !!op && op.kind === 'freezing'; }
    function isAdd(op) { return !!op && op.kind === 'add'; }
    function validOp(op) {
      if (!op || typeof op !== 'object') return false;
      if (typeof op.id !== 'string' && typeof op.id !== 'number') return false;
      if (isFreezing(op)) return FREEZING_ANSWERS.indexOf(op.answer) !== -1;
      if (isAdd(op)) return typeof op.item === 'string' && !!op.item.trim();
      return op.kind === undefined || op.kind === 'status' ? STATUSES.indexOf(op.status) !== -1 : false;
    }
    // Where an op goes and what it carries.
    function opRequest(op) {
      if (isFreezing(op)) return { url: '/api/grocery-list/' + op.id + '/freezing', body: { answer: op.answer } };
      if (isAdd(op)) {
        var body = { item: op.item, quantity: op.quantity || '', category: op.category || 'other' };
        if (op.store !== undefined) { body.store = op.store; body.remember = true; }
        return { url: '/api/grocery-list/add', body: body };
      }
      return { url: '/api/grocery-list/' + op.id + '/status', body: { status: op.status } };
    }
    // One entry per (kind, line): the newer one replaces the older and
    // goes to the end, so across lines the order is the order the shopper
    // made them. (An add's id is its own, so no add ever replaces another.)
    function enqueue(op) {
      var q = pending().filter(function (o) { return !(String(o.id) === String(op.id) && (o.kind || 'status') === (op.kind || 'status')); });
      q.push(op);
      writePending(q);
      return op;
    }
    var addSeq = 0;
    function pending() {
      var q = readJsonScoped(queueKey());
      return Array.isArray(q) ? q.filter(validOp) : [];
    }
    function writePending(q) {
      if (q.length) writeScoped(queueKey(), JSON.stringify(q));
      else removeScoped(queueKey());
    }

    var replaying = null;

    var api = {
      household: function () { return householdId; },
      known: known,

      // The server has said which household this session is. Returns true
      // when that changed something: a different household than the one
      // remembered (whose keys are purged first), or the first answer of
      // the page view (whatever was fetched meanwhile moves under its key).
      setHousehold: function (id) {
        if (id == null) return false;
        var next = String(id);
        if (householdId === next) return false;
        var carried = null;
        if (known()) {
          purge(householdId);
        } else {
          carried = { copy: memory[copyKey()], queue: memory[queueKey()], shops: memory[shopsKey()] };
          delete memory[copyKey()]; delete memory[queueKey()]; delete memory[shopsKey()];
        }
        householdId = next;
        write(HOUSEHOLD_KEY, next);
        if (carried) {
          if (carried.copy) write(copyKey(), carried.copy);
          if (carried.shops) write(shopsKey(), carried.shops);
          if (carried.queue) {
            var ops = [];
            try { ops = JSON.parse(carried.queue); } catch (err) { ops = []; }
            (Array.isArray(ops) ? ops : []).filter(validOp).forEach(function (op) {
              if (isFreezing(op)) api.queueFreezing(op.id, op.answer);
              else if (isAdd(op)) enqueue(op);
              else api.queueStatus(op.id, op.status);
            });
          }
        }
        return true;
      },

      // Sign-out, or a 401: nothing of this device's grocery memory
      // outlives the session — every key, and the pointer that says whose
      // they were. Scans storage so a key from an older build goes too.
      forget: function () {
        if (known()) purge(householdId);
        remove(HOUSEHOLD_KEY);
        try {
          if (storage && typeof storage.length === 'number' && typeof storage.key === 'function') {
            var stale = [];
            for (var i = 0; i < storage.length; i++) {
              var k = storage.key(i);
              if (k && k.indexOf('pomona.grocery.') === 0) stale.push(k);
            }
            stale.forEach(function (k) { storage.removeItem(k); });
          }
        } catch (err) { /* private mode: nothing was ever written */ }
        memory = {};
        householdId = null;
      },

      saveList: function (data) {
        if (!data || !data.stores) return;
        writeScoped(copyKey(), JSON.stringify({ savedAt: now(), data: data }));
      },
      // Only a known household's copy: unknown (nobody signed in that this
      // device has heard of) answers nothing, whatever storage holds.
      readList: function () {
        if (!known()) return null;
        var saved = readJsonScoped(copyKey());
        if (!saved || !saved.data || !saved.data.stores) return null;
        return saved;
      },
      clearList: function () { removeScoped(copyKey()); },

      // { usualStores: [...], dismissed: bool } — what /api/memory answers
      // about shops, for the reload that can't ask it.
      saveShops: function (shops) {
        if (!shops || !Array.isArray(shops.usualStores)) return;
        writeScoped(shopsKey(), JSON.stringify({ usualStores: shops.usualStores, dismissed: !!shops.dismissed }));
      },
      readShops: function () {
        if (!known()) return null;
        var saved = readJsonScoped(shopsKey());
        return saved && Array.isArray(saved.usualStores) ? saved : null;
      },

      pending: pending,
      hasPending: function () { return pending().length > 0; },

      // A tick: one entry per row (see enqueue).
      queueStatus: function (itemId, status) {
        if (STATUSES.indexOf(status) === -1) return null;
        return enqueue({ id: String(itemId), status: status, at: now() });
      },
      // "Yes, freezing it" / its Put back: one entry per row, latest wins,
      // behind whatever ticks are already waiting.
      queueFreezing: function (itemId, answer) {
        if (FREEZING_ANSWERS.indexOf(answer) === -1) return null;
        return enqueue({ id: String(itemId), kind: 'freezing', answer: answer, at: now() });
      },

      // A thing to add: its own entry, a local id until the server answers.
      // `line` is the /add body — item, quantity, category, and store
      // (present = the sheet asked; '' = Anywhere) when it was asked.
      queueAdd: function (line) {
        if (!line || typeof line.item !== 'string' || !line.item.trim()) return null;
        var op = { id: ADD_ID_PREFIX + now() + '-' + (++addSeq), kind: 'add', item: line.item.trim(),
          quantity: line.quantity || '', category: line.category || 'other', at: now() };
        if (line.store !== undefined && line.store !== null) op.store = line.store;
        return enqueue(op);
      },
      // Put back on a queued add: it never went, so it simply leaves the
      // queue. True when something was removed.
      unqueue: function (opId) {
        var q = pending();
        var kept = q.filter(function (o) { return String(o.id) !== String(opId); });
        if (kept.length === q.length) return false;
        writePending(kept);
        return true;
      },
      isPendingAddId: function (id) { return String(id).indexOf(ADD_ID_PREFIX) === 0; },

      // The list as the shopper last saw it: the server's copy with this
      // device's unsent changes on top. Never mutates the stored copy.
      applyPending: function (data) {
        var out = clone(data);
        pending().forEach(function (op) {
          if (isAdd(op)) applyAdd(out, op);
          else if (!isFreezing(op)) applyStatus(out, op.id, op.status);
        });
        return out;
      },
      applyStatus: applyStatus,
      applyAdd: applyAdd,
      removeLine: removeLine,

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
          var req = opRequest(op);
          return Promise.resolve()
            .then(function () { return post(req.url, req.body); })
            .then(function (res) {
              // Answered: spent, whichever way it went.
              writePending(pending().filter(function (o) {
                return !(o.id === op.id && o.at === op.at && isFreezing(o) === isFreezing(op));
              }));
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
