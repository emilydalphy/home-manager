// The setup question screens, shared (re-plan Option B, Emily 2026-10-18).
//
// Onboarding's own code for "Who's eating, and when?", "Do you like to cook
// ahead?", "How many different … in a week?", "What does each person need
// for weekday lunches?" and "What are you excited to eat more of lately?",
// MOVED here verbatim out of static/onboarding.html so that Plan a week's
// "Same as last week?" opens the very same screens rather than a copy of
// them. Two pages load it:
//
//   static/onboarding.html — as it always ran: its inline script reads and
//     writes the state below (usualGrid, prepAnswer, varietyChoice,
//     lunchNeeds…) and calls the wire*Step functions once, on load.
//   static/plan-week.html — takes each screen's MARKUP from onboarding.html
//     itself (fetched and lifted, so there is one copy of it) and runs these
//     functions over it, seeded with this week's answers.
//
// A classic script, not a module: the names are globals both pages share,
// exactly as they were inside onboarding's script. What each page must
// provide itself: currentMembers(opts) (the people meals are planned for),
// revealJoinWords(items), renderLunchMaxChips(), stepFlow(), noteCuisines.
// Plan a week's additions are hooks it may define (uwColumnOrder,
// uwPickerLabel, uwExtraRowHtml, uwSummaryLine); onboarding defines none,
// so its screens read exactly as before.

// Loop Board: "Break broad cuisine options into specific ones" (2026-09-03).
// The old list (Italian, Mexican, Asian, Mediterranean, American, Indian) was
// too broad to power real recommendations — "Asian" and "Mediterranean" each
// papered over several distinct cuisines. Expanded to a flat, scannable list
// of specific cuisines instead of a two-level "tap to reveal" chip (that
// would be a new interaction pattern not in DESIGN_SYSTEM.md's component
// vocabulary — a bigger call than this ticket needs). "Asian" -> Japanese,
// Thai, Chinese, Korean, Vietnamese; "Mediterranean" -> Greek, Lebanese,
// Turkish. Italian/Mexican/Indian/American kept (already specific enough).
// Free-text fallback (the input below) still covers anything not listed.
const CUISINES = [
  'Italian', 'Mexican', 'American', 'Indian',
  'Japanese', 'Thai', 'Chinese', 'Korean', 'Vietnamese',
  'Greek', 'Lebanese', 'Turkish',
  'French', 'Spanish', 'Ethiopian',
];
// HOME_TYPES/STANDARD_OPTIONS/FREQUENCIES moved to static/chores-setup.html
// with the chores questions that used them (Emily, 2026-09-05, 20a).

function escapeHtmlLocal(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function chipWithCustom(container, options, multi) {
  container.innerHTML = '';
  options.forEach(opt => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chip';
    btn.textContent = opt;
    btn.dataset.value = opt;
    btn.onclick = () => {
      if (!multi) container.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
      btn.classList.toggle('active');
    };
    container.appendChild(btn);
  });
}
function addCustomChip(container, text, multi) {
  if (!text.trim()) return;
  const value = text.trim();
  // Typing something that is already a chip — one of the presets, or one
  // added a moment ago — selects it rather than adding a second copy.
  // These answers go straight into meal planning, where "Italian,
  // Italian" is noise pretending to be a preference. Committing pending
  // text on Continue made this easy to hit by accident; it was reachable
  // via Enter before that.
  const existing = [...container.querySelectorAll('.chip')].find(
    c => (c.dataset.value || '').toLowerCase() === value.toLowerCase()
  );
  if (existing) {
    if (!multi) container.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
    existing.classList.add('active');
    return;
  }
  if (!multi) container.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'chip active';
  btn.textContent = text.trim();
  btn.dataset.value = text.trim();
  btn.onclick = () => btn.classList.toggle('active');
  container.appendChild(btn);
}
// Every chip on these screens is an on/off button, and "on" was the
// .active class alone — a colour. A screen reader heard "Mexican, button"
// whether it was chosen or not (found driving both pages, 2026-10-25), on
// every chip row of onboarding and of Plan a week's Change screens. The
// class is toggled from a dozen places across three files, so rather than
// teach each of them, aria-pressed follows the class wherever it changes:
// one observer, set up once by whichever page loads this file. A chip that
// already says aria-pressed itself (the allergens, "It changes week to
// week") is told the same thing again.
const CHIP_TOGGLE = 'button.chip, button.rhythm-chip';
function syncChipPressed(el) {
  el.setAttribute('aria-pressed', el.classList.contains('active') ? 'true' : 'false');
}
function sweepChipPressed(node) {
  if (!node || node.nodeType !== 1) return;
  if (node.matches(CHIP_TOGGLE)) syncChipPressed(node);
  node.querySelectorAll(CHIP_TOGGLE).forEach(syncChipPressed);
}
function watchChipPressed(root) {
  new MutationObserver(records => records.forEach(r => {
    if (r.type === 'attributes') { if (r.target.matches(CHIP_TOGGLE)) syncChipPressed(r.target); }
    else r.addedNodes.forEach(sweepChipPressed);
  })).observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
  sweepChipPressed(root);
}
if (typeof MutationObserver === 'function' && typeof document !== 'undefined' && document.documentElement) {
  watchChipPressed(document.documentElement);
}

function getActiveChips(container) {
  return [...container.querySelectorAll('.chip.active')].map(c => c.dataset.value);
}
function getActiveChip(container) {
  const el = container.querySelector('.chip.active');
  return el ? el.dataset.value : '';
}

// ---------- Who's eating, and when? (2026-09-30) ----------
// The usual week: breakfast, lunch and dinner x Monday-Sunday. A cell is
// 'all' (everyone), 'off' (don't plan) or a list of the NAMES eating it —
// names, because nobody is saved until setup finishes (see
// finishSetupAndReveal), and /api/onboarding/answers saves the usual week
// after member_names, so a name in a cell resolves there. Every cell
// starts on 'all' ("Everyone starts on every meal").
const UW_MEALS = ['breakfast', 'lunch', 'dinner'];
const UW_MEAL_LABELS = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };
const UW_MEAL_PLURALS = { breakfast: 'breakfasts', lunch: 'lunches', dinner: 'dinners' };
const UW_UNITS = { breakfast: ['morning', 'mornings'], lunch: ['day', 'days'], dinner: ['night', 'nights'] };
const UW_WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
const UW_DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const UW_LETTERS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
const UW_QUICK = [
  { label: 'Every day', days: [0, 1, 2, 3, 4, 5, 6] },
  { label: 'Weekdays', days: [0, 1, 2, 3, 4] },
  { label: 'Weekends', days: [5, 6] },
  { label: 'None', days: [] },
];
function uwAllRow() { return UW_WEEKDAYS.map(() => 'all'); }
let usualGrid = { breakfast: uwAllRow(), lunch: uwAllRow(), dinner: uwAllRow() };
let uwOpen = null;             // { meal, i } — the day whose picker is showing
// Who takes lunch with them, as read out of setup's last answer ("Anything
// else I should know?") — name -> 'out'. The weekday-lunch line that used
// to set it on this screen left on 2026-10-06 (Onboarding regrouped): the
// Weekday lunches screen asks it properly now, and wins for anybody it
// was answered for (saveRhythmAnswers).
let lunchLocation = {};

function uwIsOn(cell) { return cell !== 'off'; }
function uwDaysOn(row) { return (row || []).filter(uwIsOn).length; }
function uwMealOn(meal) { return uwDaysOn(usualGrid[meal]) > 0; }
// A cell against the household as it stands: a list that names everyone
// is everyone, and one that names nobody still here is everyone too (the
// same reading app/tools/usual_week.py's _clean_cell gives a stored one).
function uwCleanCell(cell, names) {
  if (cell === 'off' || cell === 'all') return cell;
  const kept = names.filter(n => (cell || []).indexOf(n) !== -1);
  if (!kept.length || kept.length >= names.length) return 'all';
  return kept;
}
function uwWho(cell, names) {
  if (cell === 'off') return '–';
  const who = cell === 'all' ? names : cell;
  if (who.length > 3) return cell === 'all' ? 'All' : String(who.length);
  return who.map(n => String(n || '').trim().charAt(0).toUpperCase()).join(' ');
}
function uwCellClass(cell) { return cell === 'all' ? 'is-all' : cell === 'off' ? '' : 'is-some'; }
function uwCellAria(meal, i, cell) {
  const day = UW_DAY_NAMES[i] + ' ' + meal;
  if (cell === 'off') return day + ', not planned';
  if (cell === 'all') return day + ', everyone';
  return day + ', ' + revealJoinWords(cell);
}
function uwCountLabel(meal, n) {
  if (!n) return 'Not planned';
  return n + ' ' + UW_UNITS[meal][n === 1 ? 0 : 1];
}
// A quick pick puts its days on for everyone and the rest off.
function uwSetMany(row, days) { return row.map((_, i) => days.indexOf(i) !== -1 ? 'all' : 'off'); }
function uwQuickOn(row, days) { return row.every((cell, i) => (days.indexOf(i) !== -1) === uwIsOn(cell)); }
// The picker under a tapped day: Everyone, a "Just <name>" each for a
// household of two, a toggle per person for three or more, Don't plan.
function uwPickerOptions(cell, names) {
  const opts = [{ key: 'all', label: 'Everyone', on: cell === 'all' }];
  if (names.length === 2) {
    names.forEach(n => opts.push({ key: 'just:' + n, label: 'Just ' + n, on: Array.isArray(cell) && cell.length === 1 && cell[0] === n }));
  } else if (names.length > 2) {
    names.forEach(n => opts.push({ key: 'toggle:' + n, label: n, toggle: true, on: cell === 'all' || (Array.isArray(cell) && cell.indexOf(n) !== -1) }));
  }
  opts.push({ key: 'off', label: 'Don’t plan', on: cell === 'off' });
  return opts;
}
function uwApplyPick(cell, key, names) {
  if (key === 'all' || key === 'off') return key;
  if (key.indexOf('just:') === 0) return uwCleanCell([key.slice(5)], names);
  const n = key.slice(7);
  let who = cell === 'all' ? names.slice() : cell === 'off' ? [] : cell.slice();
  who = who.indexOf(n) !== -1 ? who.filter(x => x !== n) : who.concat([n]);
  if (!who.length) return 'off';
  return uwCleanCell(who, names);
}
function uwTotalMeals(grid) {
  return UW_MEALS.reduce((t, m) => t + uwDaysOn(grid[m]), 0);
}
// Snacks are their own screen since 2026-10-06 (Onboarding regrouped), so
// the line under the grid counts meals only.
function uwSummary(grid) {
  const total = uwTotalMeals(grid);
  if (!total) return 'Nothing to plan yet. Tap a day to add it.';
  return 'That’s ' + total + (total === 1 ? ' meal' : ' meals') + ' a week.';
}
function uwWeekdayLunchOn(grid) { return grid.lunch.slice(0, 5).some(uwIsOn); }
// The note's "takes lunch with them", for the people the Weekday lunches
// screen has nothing for — anybody it was answered for is written from
// that answer instead (app/tools/member_needs.py), which is saved after
// this and would replace it anyway.
function lunchLocationPayload() {
  const out = {};
  currentMembers({ forMeals: true }).forEach(m => {
    if (lunchLocation[m.name] === 'out' && !lunchNeedsAnswered(m.name)) out[m.name] = 'out';
  });
  return Object.keys(out).length ? out : null;
}

function uwDayTileHtml(meal, i, cell, names, open) {
  return `<button type="button" class="uw-day ${uwCellClass(cell)}${open ? ' is-open' : ''}" data-uw-meal="${meal}" data-uw-day="${i}" aria-label="${escapeHtmlLocal(uwCellAria(meal, i, cell))}" aria-expanded="${open ? 'true' : 'false'}"><b>${UW_LETTERS[i]}</b><span>${escapeHtmlLocal(uwWho(cell, names))}</span></button>`;
}
// The weekdays a card shows, in the order it shows them: Monday to Sunday
// in onboarding. Plan a week defines uwColumnOrder() to show its own
// period's days in date order (and only those); the cells stay keyed by
// weekday either way, so every rule above reads them the same.
function uwColumns() {
  return typeof uwColumnOrder === 'function' ? uwColumnOrder() : UW_WEEKDAYS.map((_, i) => i);
}
function uwMealCardHtml(meal, names) {
  const row = usualGrid[meal];
  const cols = uwColumns();
  const openI = uwOpen && uwOpen.meal === meal ? uwOpen.i : -1;
  let html = `<div class="uw-card" data-uw-card="${meal}">
    <div class="uw-head"><span class="uw-title">${UW_MEAL_LABELS[meal]}</span><span class="uw-count">${uwCountLabel(meal, uwDaysOn(cols.map(i => row[i])))}</span></div>
    <div class="uw-days">${cols.map(i => uwDayTileHtml(meal, i, row[i], names, i === openI)).join('')}</div>`;
  if (openI >= 0) {
    const day = typeof uwPickerLabel === 'function' ? uwPickerLabel(openI) : UW_DAY_NAMES[openI].slice(0, 3);
    html += `<div class="uw-row"><p class="uw-ct">${escapeHtmlLocal(day)} · who’s eating?</p>` +
      uwPickerOptions(row[openI], names).map(o =>
        `<button type="button" class="uw-q${o.on ? ' is-on' : ''}" data-uw-pick="${escapeHtmlLocal(o.key)}" aria-pressed="${o.on ? 'true' : 'false'}">${escapeHtmlLocal(o.label)}</button>`
      ).join('') + '</div>';
    // Plan a week's "this week only" row under the picker (Short on time,
    // Got time, + Guests); onboarding has none.
    if (typeof uwExtraRowHtml === 'function') html += uwExtraRowHtml(meal, openI);
  } else {
    html += '<div class="uw-row uw-quick">' + UW_QUICK.map((q, qi) => {
      const on = uwQuickOn(row, q.days);
      return `<button type="button" class="uw-q${on ? ' is-on' : ''}" data-uw-quick="${qi}" aria-pressed="${on ? 'true' : 'false'}">${q.label}</button>`;
    }).join('') + '</div>';
  }
  return html + '</div>';
}
// The grid is redrawn whole on every tap, and a redraw used to drop the
// keyboard on <body>: Enter on Friday's dinner opened its picker and the
// next Tab started again at Monday's breakfast (found 2026-10-25). The
// control that had focus is found again by what it is — its card and its
// data-uw-* answer — and focused; one that is gone (a pick closes the
// picker; "−" to no guests) hands focus to the day it was under. A tap
// on a phone focuses nothing, so there is nothing to put back.
const UW_FOCUS_ATTRS = ['data-uw-day', 'data-uw-pick', 'data-uw-quick', 'data-uw-tag', 'data-uw-guests'];
function uwFocusSelector(el) {
  if (!el || !el.closest) return null;
  const card = el.closest('[data-uw-card]');
  const attr = UW_FOCUS_ATTRS.find(a => el.hasAttribute(a));
  if (!card || !attr) return null;
  const value = el.getAttribute(attr).replace(/["\\]/g, '\\$&');
  return `[data-uw-card="${card.dataset.uwCard}"] [${attr}="${value}"]`;
}
function uwDaySelector(open) {
  return `[data-uw-card="${open.meal}"] [data-uw-day="${open.i}"]`;
}
let uwShownOpen = null;   // the cell whose picker the last draw showed
function renderMealsDays() {
  const grid = document.getElementById('uw-grid');
  const active = document.activeElement;
  const focusSel = active && grid.contains(active) ? uwFocusSelector(active) : null;
  const wasOpen = uwShownOpen;
  const names = currentMembers({ forMeals: true }).map(m => m.name);
  UW_MEALS.forEach(meal => { usualGrid[meal] = usualGrid[meal].map(cell => uwCleanCell(cell, names)); });
  grid.innerHTML = UW_MEALS.map(meal => uwMealCardHtml(meal, names)).join('');
  uwShownOpen = uwOpen;
  if (focusSel) {
    const back = grid.querySelector(focusSel) || (wasOpen && grid.querySelector(uwDaySelector(wasOpen)));
    if (back) back.focus({ preventScroll: true });
  }
  // A picker that has just opened is the card's last rows — and on the
  // dinner card they open under the fixed foot, out of sight (the CSS's
  // scroll-margin on .uw-row is what clears the foot).
  if (uwOpen && !(wasOpen && wasOpen.meal === uwOpen.meal && wasOpen.i === uwOpen.i)) {
    const card = grid.querySelector(`[data-uw-card="${uwOpen.meal}"]`);
    const last = card && card.lastElementChild;
    if (last && last.scrollIntoView) last.scrollIntoView({ block: 'nearest' });
  }
  document.getElementById('uw-sum').textContent = typeof uwSummaryLine === 'function' ? uwSummaryLine() : uwSummary(usualGrid);
  const next = document.getElementById('meals-days-next');
  if (next) next.disabled = uwTotalMeals(usualGrid) === 0;
}
function buildMealsDaysStep() {
  uwOpen = null;
  renderMealsDays();
}
function wireMealsDaysStep() {
document.getElementById('uw-grid').addEventListener('click', e => {
  const names = currentMembers({ forMeals: true }).map(m => m.name);
  const day = e.target.closest('[data-uw-day]');
  if (day) {
    const meal = day.dataset.uwMeal, i = Number(day.dataset.uwDay);
    uwOpen = uwOpen && uwOpen.meal === meal && uwOpen.i === i ? null : { meal: meal, i: i };
    renderMealsDays();
    return;
  }
  const pick = e.target.closest('[data-uw-pick]');
  if (pick && uwOpen) {
    const key = pick.dataset.uwPick;
    const row = usualGrid[uwOpen.meal];
    row[uwOpen.i] = uwApplyPick(row[uwOpen.i], key, names);
    if (key.indexOf('toggle:') !== 0) uwOpen = null;
    renderMealsDays();
    return;
  }
  const quick = e.target.closest('[data-uw-quick]');
  if (quick) {
    const card = quick.closest('[data-uw-card]');
    const meal = card.dataset.uwCard;
    usualGrid[meal] = uwSetMany(usualGrid[meal], UW_QUICK[Number(quick.dataset.uwQuick)].days);
    renderMealsDays();
    return;
  }
});
}

// ---------- Do you like to cook ahead? (2026-09-30) ----------
// Replaces "Which days do you want to do your meal prepping?". No, or yes
// with the day(s). Saved as usual_week.prep: the rhythm fact prep_days.
// "How long?" left on 2026-10-06 (Onboarding regrouped, Emily
// 2026-10-05): every prep day is planned for up to 2 hours, so the length
// is always 'longer' = 120 minutes (app/tools/usual_week.py PREP_LENGTHS),
// and db._migrate_prep_and_lunch_limits moved the 1-hour households.
const PREP_LENGTH = 'longer';
let prepAnswer = '';           // '' (not answered — read as no) | 'no' | 'yes'
let prepDayKeys = [];          // 'monday'... as tapped
// In week order, whatever order the taps came in; none unless "Yes".
function currentPrepDayKeys() {
  return prepAnswer === 'yes' ? UW_WEEKDAYS.filter(d => prepDayKeys.indexOf(d) !== -1) : [];
}
function hasPrepDay() { return currentPrepDayKeys().length > 0; }
function uwDayName(key) { return UW_DAY_NAMES[UW_WEEKDAYS.indexOf(key)] || ''; }
// The line under the answer (Emily's mockup: "Sunday, a longer stretch.
// I'll put lunch prep and a big batch cook there, and keep weeknights
// quick."). Lunch prep only when lunch is planned at all. Every prep day
// is up to 2 hours since 2026-10-06, so the line says that instead of a
// length somebody picked.
function prepReadback(days, lunchOn) {
  if (!days.length) return '';
  const when = revealJoinWords(days.map(uwDayName)) + ', up to 2 hours';
  const what = lunchOn ? 'lunch prep and a big batch cook' : 'a big batch cook';
  return when + '. I’ll put ' + what + ' there, and keep weeknights quick.';
}
function buildPrepStep() {
  const no = document.getElementById('prep-no');
  const yesCard = document.getElementById('prep-yes-card');
  const yes = document.getElementById('prep-yes');
  no.classList.toggle('is-on', prepAnswer === 'no');
  no.setAttribute('aria-pressed', prepAnswer === 'no' ? 'true' : 'false');
  yesCard.classList.toggle('is-on', prepAnswer === 'yes');
  yes.setAttribute('aria-pressed', prepAnswer === 'yes' ? 'true' : 'false');
  document.getElementById('prep-yes-body').hidden = prepAnswer !== 'yes';
  document.getElementById('prep-day-tiles').innerHTML = UW_WEEKDAYS.map((d, i) => {
    const on = prepDayKeys.indexOf(d) !== -1;
    return `<button type="button" class="uw-day${on ? ' is-all' : ''}" data-prep-day="${d}" aria-label="${UW_DAY_NAMES[i]}" aria-pressed="${on ? 'true' : 'false'}"><b>${UW_LETTERS[i]}</b></button>`;
  }).join('');
  const line = prepReadback(currentPrepDayKeys(), uwMealOn('lunch'));
  const readback = document.getElementById('prep-readback');
  readback.textContent = line;
  readback.hidden = !line;
  // "Yes" with no day picked isn't an answer yet.
  document.getElementById('prep-next').disabled = prepAnswer === 'yes' && !hasPrepDay();
}
function wirePrepStep() {
document.getElementById('prep-no').addEventListener('click', () => { prepAnswer = 'no'; buildPrepStep(); });
document.getElementById('prep-yes').addEventListener('click', () => { prepAnswer = 'yes'; buildPrepStep(); });
document.getElementById('prep-day-tiles').addEventListener('click', e => {
  const btn = e.target.closest('[data-prep-day]');
  if (!btn) return;
  const d = btn.dataset.prepDay;
  prepDayKeys = prepDayKeys.indexOf(d) !== -1 ? prepDayKeys.filter(k => k !== d) : prepDayKeys.concat([d]);
  buildPrepStep();
});
}

// ---------- How many different breakfasts / lunches / dinners? ----------
// One screen per meal (2026-09-30). The keys are
// app/tools/usual_week.py's VARIETY_CHOICES, which is where each one's
// number lives; the words are Emily's mockups'. "Meal prep ahead" needs a
// prep day (the server refuses it without one), so it's only offered with
// one. "A few in rotation" starts picked — one tap on Continue is an
// answer.
// `most` (2026-10-09) is the top of an option's own range — "3–4 lunches"
// is 4 — and an option is only offered when the meal is on at least that
// many days: lunch on weekends only was offered "3–4 lunches" (Loop Board,
// Bug). Breakfast had the same hole. Dinner has no `most` on purpose: its
// answer also sets the leftovers stance (leftoversStanceFromVariety), so
// trimming it changes what the planner is told, which is Emily's call.
const VARIETY_OPTIONS = {
  breakfast: [
    { key: 'go_to_or_two', most: 2, num: '1–2 breakfasts', title: 'A go-to or two', desc: 'Same thing most mornings, something different on the weekend.' },
    { key: 'few_in_rotation', most: 4, num: '3–4 breakfasts', title: 'A few in rotation', desc: 'Enough to mix it up, each one made more than once.' },
    { key: 'new_every_day', num: 'All different', title: 'Something new every morning', desc: 'The most variety, and the most cooking.' },
  ],
  lunch: [
    { key: 'last_nights_dinner', num: 'Mostly leftovers', title: 'Last night’s dinner', desc: 'I’ll make dinner big enough to cover the next day’s lunch.' },
    { key: 'meal_prep_ahead', most: 2, num: '1–2 lunches', title: 'Meal prep ahead', desc: 'Made on your prep day, enough for the week.', needsPrepDay: true },
    { key: 'few_in_rotation', most: 4, num: '3–4 lunches', title: 'A few in rotation', desc: 'Each one made more than once.' },
    { key: 'new_every_day', num: 'All different', title: 'Something new every day', desc: 'The most variety, and the most cooking.' },
  ],
  dinner: [
    { key: 'cook_big_eat_twice', num: '2–3 dinners', title: 'Cook big, eat twice', desc: 'Most dinners come back as leftovers the next night.' },
    { key: 'few_in_rotation', num: '4–5 dinners', title: 'A few in rotation', desc: 'Mostly fresh, with a leftovers night or two.' },
    { key: 'new_every_day', num: 'All different', title: 'Something new every night', desc: 'The most variety, and the most cooking.' },
  ],
};
const VARIETY_DEFAULT = 'few_in_rotation';
const VARIETY_STEPS = ['variety-breakfast', 'variety-lunch', 'variety-dinner'];
let varietyChoice = { breakfast: VARIETY_DEFAULT, lunch: VARIETY_DEFAULT, dinner: VARIETY_DEFAULT };
// `daysOn` left out means "don't trim by days" (the older callers).
function varietyOptionsFor(meal, prepDay, daysOn) {
  return VARIETY_OPTIONS[meal].filter(o => (!o.needsPrepDay || prepDay)
    && !(daysOn !== undefined && o.most && o.most > daysOn));
}
// What one variety screen offers this household: its prep day and the
// days the meal is on in the grid.
function varietyOffered(meal) {
  return varietyOptionsFor(meal, hasPrepDay(), uwDaysOn(usualGrid[meal]));
}
// The pick as it stands: one that's no longer offered (the prep day went,
// or the days shrank) falls back to the default rather than being sent
// for the server to refuse — and when the default itself doesn't fit the
// days, to "something new every day", which plans the same number the
// server would have clamped the rotation to (one a day), and is the
// option the screen shows lit. (typeof: the harnesses in
// tests/test_onboarding_your_week.py lift this without a grid.)
function currentVarietyChoice(meal) {
  const days = typeof usualGrid === 'undefined' ? undefined : uwDaysOn(usualGrid[meal]);
  const offered = varietyOptionsFor(meal, hasPrepDay(), days).map(o => o.key);
  if (offered.indexOf(varietyChoice[meal]) !== -1) return varietyChoice[meal];
  return offered.indexOf(VARIETY_DEFAULT) !== -1 ? VARIETY_DEFAULT : 'new_every_day';
}
// The foot of one variety screen, from the steps this household is asked:
// how many pips and how many are on, the "Breakfast · then lunch · then
// dinner" line (none on the last), and the button ("Next: lunch" while
// another variety screen follows).
function varietyFoot(flow, key) {
  const steps = flow.filter(k => VARIETY_STEPS.indexOf(k) !== -1);
  const i = steps.indexOf(key);
  const meals = steps.map(k => k.slice('variety-'.length));
  const next = flow[flow.indexOf(key) + 1] || '';
  let caption = '';
  if (i < steps.length - 1) {
    caption = meals.map((m, j) => (j <= i ? m : 'then ' + m)).join(' · ');
    caption = caption.charAt(0).toUpperCase() + caption.slice(1);
  }
  return {
    pips: steps.length > 1 ? steps.length : 0,
    on: i + 1,
    caption: caption,
    nextLabel: VARIETY_STEPS.indexOf(next) !== -1 ? 'Next: ' + next.slice('variety-'.length) : 'Continue',
  };
}
function buildVarietyStep(meal) {
  const chosen = currentVarietyChoice(meal);
  document.getElementById('variety-' + meal + '-opts').innerHTML = varietyOffered(meal).map(o => {
    const on = o.key === chosen;
    return `<button type="button" class="q-opt${on ? ' is-on' : ''}" data-variety="${o.key}" aria-pressed="${on ? 'true' : 'false'}"><span class="q-opt-num">${escapeHtmlLocal(o.num)}</span><span class="q-opt-title">${escapeHtmlLocal(o.title)}</span><span class="q-opt-desc">${escapeHtmlLocal(o.desc)}</span></button>`;
  }).join('');
  const foot = varietyFoot(stepFlow(), 'variety-' + meal);
  const stepEl = document.getElementById('step-variety-' + meal);
  const pips = stepEl.querySelector('.q-pips');
  pips.innerHTML = Array.from({ length: foot.pips }, (_, j) => `<span${j < foot.on ? ' class="is-on"' : ''}></span>`).join('');
  const caption = stepEl.querySelector('.q-pips-caption');
  caption.textContent = foot.caption;
  caption.hidden = !foot.caption;
  document.getElementById('variety-' + meal + '-next').textContent = foot.nextLabel;
}
function wireVarietySteps() {
UW_MEALS.forEach(meal => {
  document.getElementById('variety-' + meal + '-opts').addEventListener('click', e => {
    const btn = e.target.closest('[data-variety]');
    if (!btn) return;
    varietyChoice[meal] = btn.dataset.variety;
    buildVarietyStep(meal);
  });
});
}

// ---------- Weekday lunches (Onboarding regrouped, 2026-10-06) ----------
// Emily's locked screen (2026-10-05): "What does each person need for
// weekday lunches?" A row per person who eats lunch on any weekday, each
// with any of five needs. "Different on some days?" turns a name into five
// day rows (Mon-Fri), each with the same five. Someone who buys lunch is
// off Who's eating on weekdays — said here with Out (2026-10-06), which
// writes the grid, and shown as Out whichever screen said it.
//
// Keyed by member NAME like every other answer before the finish, and
// pruned with them (pruneMemberKeyedAnswers). Nothing is posted until
// setup ends: saveOnboardingAnswers sends lunchNeedsPayload().
const LUNCH_NEED_OPTIONS = [
  { key: 'cold_packed', label: 'Cold packed' },
  { key: 'thermos', label: 'Warm in a thermos' },
  { key: 'reheat', label: 'Something to reheat' },
  { key: 'made_fresh', label: 'Made fresh' },
  { key: 'nut_free', label: 'Nut-free environment' },
];
// Monday-Friday — a literal, since UW_WEEKDAYS is declared further down.
const LUNCH_NEED_DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'];
let lunchNeeds = {};          // name -> { needs: [...], days: { monday: [...] } | null }
let lunchDaysPicking = false; // "Different on some days?" opened
// At home / Packed / Out (Emily, 2026-10-06, Loop Board "Setup has no 'eats
// lunch out on weekdays' answer"): one tap per person instead of ten on the
// Who's eating grid. The GRID is the answer — Out takes the person off
// Monday-Friday lunch there, At home and Packed put them back — and Packed
// is the Cold packed need, so neither chip keeps a second copy of anything
// except these two small facts the grid cannot hold:
let lunchHome = {};           // name -> true once "At home" is tapped (nothing else says it)
let lunchOutDays = {};        // name -> { weekday index: the cell as Out left it }, so At home can put them back

// The people with a lunch to plan on some weekday: everyone on a cell
// that's 'all', the names on a subset cell.
function lunchPeople() {
  const names = currentMembers({ forMeals: true }).map(m => m.name);
  const on = new Set();
  (usualGrid.lunch || []).slice(0, 5).forEach(cell => {
    const c = uwCleanCell(cell, names);
    if (c === 'off') return;
    (c === 'all' ? names : c).forEach(n => on.add(n));
  });
  return names.filter(n => on.has(n));
}
// The rows on the screen: everyone meals are planned for, not only
// lunchPeople() — somebody Out is still a row, so the answer stays on
// screen to be changed back instead of the row vanishing under the finger,
// and the ten grid taps a tester used to say it read back as one chip.
function lunchStepPeople() {
  return currentMembers({ forMeals: true }).map(m => m.name);
}
// The days Out took this person off that the grid still holds as Out left
// them. A day changed on the grid since — "Don't plan" for everyone, say —
// is the household's later answer, and is not Out's to give back.
function lunchOutDaysStill(name, names) {
  const took = lunchOutDays[name] || {};
  return Object.keys(took).map(Number).filter(i =>
    JSON.stringify(uwCleanCell(usualGrid.lunch[i], names)) === JSON.stringify(took[i]));
}
// Somebody this screen said Out for, on a day the grid still has as Out
// left it. Keeps the screen in the flow when Out has emptied every weekday
// lunch (stepFlow) — otherwise Continue would look itself up in a flow it
// is no longer in — but not once the grid itself has turned lunch off.
function lunchOutHere() {
  if (typeof lunchOutDays === 'undefined') return false;
  const names = currentMembers({ forMeals: true }).map(m => m.name);
  return Object.keys(lunchOutDays).some(n => names.indexOf(n) !== -1 && lunchOutDaysStill(n, names).length > 0);
}
// Which of the three chips is lit, read from the answers themselves: off
// every weekday lunch on the grid is Out (however it got there), Cold
// packed on any day is Packed, and At home only once tapped.
function lunchWhere(name) {
  if (lunchPeople().indexOf(name) === -1) return 'out';
  const e = lunchNeeds[name];
  const packed = e && (e.needs.indexOf('cold_packed') !== -1 ||
    (!!e.days && LUNCH_NEED_DAYS.some(d => (e.days[d] || []).indexOf('cold_packed') !== -1)));
  if (packed) return 'packed';
  return lunchHome[name] ? 'home' : '';
}
// One tap: write the grid's Monday-Friday lunch cells for this person, and
// the Cold packed need for Packed. Out remembers which days it took them
// off; At home / Packed put them back on those still as Out left them, and
// on every weekday lunch planned for anyone — never on a day the household
// plans no lunch.
function lunchSetWhere(name, where) {
  const names = currentMembers({ forMeals: true }).map(m => m.name);
  const row = usualGrid.lunch;
  if (where === 'out') {
    // Earlier Out days the grid has changed since are dropped, not kept.
    const kept = {};
    lunchOutDaysStill(name, names).forEach(i => { kept[i] = lunchOutDays[name][i]; });
    for (let i = 0; i < 5; i++) {
      const c = uwCleanCell(row[i], names);
      if (c === 'off' || (c !== 'all' && c.indexOf(name) === -1)) continue;
      row[i] = uwApplyPick(c, 'toggle:' + name, names);
      kept[i] = uwCleanCell(row[i], names);
    }
    lunchOutDays[name] = kept;
    delete lunchHome[name];
    return;
  }
  const back = lunchOutDaysStill(name, names);
  for (let i = 0; i < 5; i++) {
    const c = uwCleanCell(row[i], names);
    if (c === 'all' || (c !== 'off' && c.indexOf(name) !== -1)) continue;
    if (c === 'off' && back.indexOf(i) === -1) continue;
    row[i] = c === 'off' ? uwCleanCell([name], names) : uwCleanCell(c.concat([name]), names);
  }
  delete lunchOutDays[name];
  const e = lunchEntry(name);
  const setPacked = list => {
    const has = list.indexOf('cold_packed') !== -1;
    return (where === 'packed') === has ? list : toggleNeed(list, 'cold_packed');
  };
  e.needs = setPacked(e.needs);
  if (e.days) LUNCH_NEED_DAYS.forEach(d => { e.days[d] = setPacked(e.days[d] || []); });
  if (where === 'home') lunchHome[name] = true; else delete lunchHome[name];
}
const LUNCH_WHERE_OPTIONS = [
  { key: 'home', label: 'At home' },
  { key: 'packed', label: 'Packed' },
  { key: 'out', label: 'Out' },
];
function lunchEntry(name) {
  if (!lunchNeeds[name]) lunchNeeds[name] = { needs: [], days: null };
  return lunchNeeds[name];
}
function lunchNeedsAnswered(name) {
  const e = lunchNeeds[name];
  if (!e) return false;
  if (e.needs.length) return true;
  return !!e.days && LUNCH_NEED_DAYS.some(d => (e.days[d] || []).length);
}
function toggleNeed(list, key) {
  const out = list.indexOf(key) !== -1 ? list.filter(k => k !== key) : list.concat([key]);
  return LUNCH_NEED_OPTIONS.map(o => o.key).filter(k => out.indexOf(k) !== -1);
}
function someoneHasMadeFresh() {
  return lunchPeople().some(n => {
    const e = lunchNeeds[n];
    if (!e) return false;
    if (e.days) return LUNCH_NEED_DAYS.some(d => (e.days[d] || []).indexOf('made_fresh') !== -1);
    return e.needs.indexOf('made_fresh') !== -1;
  });
}
function lunchChipsHtml(name, day, picked) {
  return '<div class="chip-row ln-chips">' + LUNCH_NEED_OPTIONS.map(o => {
    const on = picked.indexOf(o.key) !== -1;
    return `<button type="button" class="chip${on ? ' active' : ''}" data-ln-name="${escapeHtmlLocal(name)}" data-ln-day="${day}" data-ln-need="${o.key}" aria-pressed="${on ? 'true' : 'false'}">${o.label}</button>`;
  }).join('') + '</div>';
}
function lunchPersonHtml(name) {
  const e = lunchEntry(name);
  const where = lunchWhere(name);
  let html = `<div class="member-block"><p class="q-sub">${escapeHtmlLocal(name)}</p>` +
    '<div class="uw-row ln-where">' + LUNCH_WHERE_OPTIONS.map(o =>
      `<button type="button" class="uw-q${where === o.key ? ' is-on' : ''}" data-ln-where="${o.key}" data-ln-name="${escapeHtmlLocal(name)}" aria-pressed="${where === o.key ? 'true' : 'false'}">${o.label}</button>`
    ).join('') + '</div>';
  // Out is no lunch to plan, so there is nothing to need.
  if (where === 'out') return html + '</div>';
  if (!e.days) {
    html += lunchChipsHtml(name, '', e.needs);
  } else {
    html += LUNCH_NEED_DAYS.map(d =>
      `<div class="ln-day"><p class="q-opt-label">${uwDayName(d)}</p>${lunchChipsHtml(name, d, e.days[d] || [])}</div>`
    ).join('');
    html += `<button type="button" class="uw-link" data-ln-same="${escapeHtmlLocal(name)}">Same every day</button>`;
  }
  return html + '</div>';
}
function buildLunchNeedsStep() {
  const people = lunchPeople();
  document.getElementById('lunch-needs-people').innerHTML = lunchStepPeople().map(lunchPersonHtml).join('');
  const pick = document.getElementById('lunch-needs-days-pick');
  const standing = people.filter(n => !lunchEntry(n).days);
  if (!standing.length) {
    pick.innerHTML = '';
  } else if (!lunchDaysPicking) {
    pick.innerHTML = '<button type="button" class="uw-link" data-ln-days-open>Different on some days?</button>';
  } else {
    pick.innerHTML = '<p class="q-opt-label">Tap a name to set it day by day</p><div class="uw-row">' +
      standing.map(n => `<button type="button" class="uw-q" data-ln-by-day="${escapeHtmlLocal(n)}">${escapeHtmlLocal(n)}</button>`).join('') +
      '</div>';
  }
  renderLunchMaxChips();
}
function wireLunchNeedsStep() {
document.getElementById('step-lunch-needs').addEventListener('click', e => {
  const where = e.target.closest('[data-ln-where]');
  if (where) {
    lunchSetWhere(where.dataset.lnName, where.dataset.lnWhere);
    buildLunchNeedsStep();
    return;
  }
  const chip = e.target.closest('[data-ln-need]');
  if (chip) {
    const entry = lunchEntry(chip.dataset.lnName);
    const day = chip.dataset.lnDay;
    if (day && entry.days) entry.days[day] = toggleNeed(entry.days[day] || [], chip.dataset.lnNeed);
    else entry.needs = toggleNeed(entry.needs, chip.dataset.lnNeed);
    buildLunchNeedsStep();
    return;
  }
  if (e.target.closest('[data-ln-days-open]')) { lunchDaysPicking = true; buildLunchNeedsStep(); return; }
  const byDay = e.target.closest('[data-ln-by-day]');
  if (byDay) {
    // Every day starts as the standing answer, so only the days that are
    // really different need a tap.
    const entry = lunchEntry(byDay.dataset.lnByDay);
    entry.days = {};
    LUNCH_NEED_DAYS.forEach(d => { entry.days[d] = entry.needs.slice(); });
    lunchDaysPicking = false;
    buildLunchNeedsStep();
    return;
  }
  const same = e.target.closest('[data-ln-same]');
  if (same) {
    // Back to one answer for the week: Monday's, which is where they started.
    const entry = lunchEntry(same.dataset.lnSame);
    entry.needs = entry.days ? (entry.days.monday || []).slice() : entry.needs;
    entry.days = null;
    buildLunchNeedsStep();
  }
});
}
// The payload: only people with something tapped (an empty row says
// nothing, and must not overwrite what the note said — lunchLocationPayload)
// and only people who still have a weekday lunch. A day-by-day person's
// standing answer is Monday's; the server keeps only the days that differ.
function lunchNeedsPayload() {
  const out = {};
  lunchPeople().forEach(n => {
    if (!lunchNeedsAnswered(n)) return;
    const e = lunchNeeds[n];
    if (e.days) {
      const days = {};
      LUNCH_NEED_DAYS.forEach(d => { days[d] = (e.days[d] || []).slice(); });
      out[n] = { needs: days.monday.slice(), days: days };
    } else {
      out[n] = { needs: e.needs.slice(), days: {} };
    }
  });
  return Object.keys(out).length ? out : null;
}

// ---------- Q6: excited about ----------
let excitedBuilt = false;
function buildExcitedStep() {
  if (excitedBuilt) return;
  const chips = document.getElementById('excited-chips');
  chipWithCustom(chips, CUISINES, true);
  excitedBuilt = true;
  document.getElementById('excited-custom').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); commitExcitedCustom(); }
  });
}
// Same reason as commitWontEatInput: a cuisine typed but not entered is
// still an answer, and tapping Continue must not throw it away.
// The chips plus any cuisine read out of setup's last answer and
// confirmed (2026-10-04) — the same shape currentWontEat() uses for its
// presets-plus-typed answer, rather than writing into the chip row, so
// nothing depends on whether that step was ever drawn.
function currentExcitedAbout() {
  const chips = getActiveChips(document.getElementById('excited-chips'));
  const seen = new Set(chips.map(c => c.toLowerCase()));
  return chips.concat(noteCuisines.filter(c => !seen.has(c.toLowerCase())));
}
function commitExcitedCustom() {
  const input = document.getElementById('excited-custom');
  if (!input) return;
  addCustomChip(document.getElementById('excited-chips'), input.value, true);
  input.value = '';
}

// Each screen's taps, wired once by the page that shows it.
function wireSetupScreens() {
  wireMealsDaysStep();
  wirePrepStep();
  wireVarietySteps();
  wireLunchNeedsStep();
}
