# Home Manager — project context for Claude Code

This file is read automatically at the start of every Claude Code session in
this repo. It's a status briefing, not the full history — for engineering
decisions and root-caused bugs, see **Decision log** below; append to it as
you go, in the same terse style (fact, then why). The `design_handoff_home_manager/`
and `design_handoff_shell/` directories are UI/design-system handoff docs from
two separate redesign efforts (screens, tokens, copy, interaction specs) —
useful for "why does this screen look like this," but they are **not** an
engineering decision log, despite an earlier version of this file claiming
otherwise.

## What this is

A household assistant app: FastAPI + SQLite backend (`app/`), vanilla
HTML/CSS/JS frontend (`static/`), no build step, no framework. An
Anthropic-Claude chat agent (`app/agent.py`, tool-calling against
`app/tools/`) plans meals, manages a grocery list, tracks kitchen
inventory, and answers "what we know" about the household. Deployed to
Railway, auto-deploying from `main` on push. Live at
`home-manager-production-4949.up.railway.app`.

**All UI work follows `DESIGN_SYSTEM.md` — read it before touching anything
visual.** Tokens, hard rules, components, nav rules, voice, and who's allowed
to change what are all there.

## Current state (as of 2026-09-08)

**Landed 2026-09-11 (pushed as `4976b36`) — three features built against
the mental load model, plus the model itself.** Read the loop skill's new
section "The problem we're solving — the mental load model" before picking
work; Emily anchored the roadmap on it that day. On `main` now:

- **Staples** (`app/tools/staples.py`, tables `staples` + `staple_events`,
  `grocery_items.staple_id`): things a household buys on a rhythm, put on
  the grocery list as an ordinary line when probably due, with "We have
  plenty" / "Not this trip" and a Staples card on Shop. Cadence is learned
  from real purchases (median interval, two needed). **Never touches
  `inventory_items`** — inventory is an in-development beta feature by
  Emily's call, staples come first. Chat: `add_staple`, `list_staples`,
  `mark_staple_plenty`, `remove_staple`.
- **Per-adult login, slice 1** ("Who's this?" after the passphrase for a
  household with more than one adult; the pick lives in the signed session
  cookie; `member_id()` / `current_member()` in `app/tools/_shared.py`).
  Approving the week, grocery adds, pre-shop drops and the week's questions
  are credited to the session's adult; notification #4 goes to the OTHER
  adult. Slice 2 (a per-adult secret) is not built. Cook/prep ticks and
  chore completion still record no person — those tables have no column.
- **Morning text** (`app/tools/digest.py`, `morning_text_sends`,
  `households.timezone` default `America/Toronto`,
  `households.morning_text_time` default 07:00, `members.phone` /
  `morning_text_on`): one daily text of today's moves via Twilio's REST
  API, from an in-process loop like the backup loop; sends nothing until
  `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` are set
  (logs "Morning texts are off" at startup otherwise). The bell also
  refetches on focus/visibility and every five minutes (still behind
  `SHOW_NOTIF_BELL = false`). Preferences → "Morning text"; chat:
  `set_morning_text`.

Suite: 2677 tests on the merged `main`. Everything above is `In progress`
→ `Done` on the Loop Board except "Reach me before the moment", which
stays open for web/native push.


Landed on `main` on or after 2026-09-06 — the **complete** first-parent list
for that window, verified in `git log origin/main --first-parent`, newest
first. (Mostly merges; `ca97720` is a direct commit.)
(Merges between 2026-09-04 and 09-06 are deliberately not enumerated: there
are 44 in the full range since this section was last dated, so run
`git log` rather than trusting any list here to be exhaustive.)

`4b8fbd2` cook-ahead · `42d422a` Cook-this focus fix · `2027f3c` security
response headers (on every app route — an unhandled 500 is produced inside
the framework's error layer and gets none) · `fa5ba6e` a previous correction
to this file · `a0b487d` defrost ask at approval · `1a943f1` adult-or-child
onboarding · `fe1d3f6` What we know showing every onboarding answer ·
`1e6117a` five tests stop failing every Sunday · `5687ef4` main merged into
the custom-date-range + atomic-period-takeover merge · `0cdaf62`
atomic period takeover · `2d69951` custom date range · `ca97720` allergy
check confirm-tap.

**Two of those matter more than the rest, because the Decision log below
still labels them "NOT merged at the time of writing":** `0cdaf62`
(atomic-period-takeover) and `2d69951` (custom-date-range). They are on
`main`. The log's own preamble says those headlines are point-in-time, but
these are the two where believing the headline would cost you real work.
(`ca97720`, the allergy confirm-tap, is also on `main`; its Decision log
entry carries no merge-status label either way, so it misleads nobody.)

For completeness, since a partial list is its own trap: **every** entry in
the Decision log still carrying "NOT merged at the time of writing" is in
fact merged. All five checked on 2026-09-08 — `custom-date-range` and
`atomic-period-takeover` (above), plus `fix-grocery-quantity-inflation`
(`6a01a9d`), `fix-full-plate` (`0d26635`) and `planning-periods` (its
`plan_period`/`content_start_date` work is live in
`app/tools/weekly_plan.py`). `allergy-backfill-and-gluten` (`30a7b0b`) is
merged too. Treat that phrase anywhere below as "was true on the branch,"
never as current state.

`main` is live on Railway. Everything the Decision log below describes as
built has since **merged** — the entries were written on their branches and
several still say "not merged" in their own headline; that is the state at
the time of writing, not now. Specifically, all of these are on `main`:
the Pomona rebrand and dark mode, the native Grocery and Kitchen screens
(no tab is an iframe any more), multi-household, the `app/tools/` package
split, the Plan the Week flow, household rhythm onboarding, per-person
taste, the defrost flow, and streaming chat.

The **Plan the Week** flow is still the shape of weekly planning: a nudge,
two question screens (`/plan-week`), a 21-slot draft on Meals, and an
Approve button, plus a revisitable setup screen at `/meal-setup`. Read
`design_handoff_plan_the_week/` before touching weekly planning, approval,
or the assistant's voice.

The four native tabs are **Today / Meals / Grocery / Kitchen** (`TABS` in
`static/shell.js`). **Cooking lives on KITCHEN as of 2026-09-08** — it used
to be a *state* of the Meals tab (a `Plan | Cook` segmented control at
`/week`) and this file said so for two days after it stopped being true.
Kitchen is the cook's tab now: its root is today's cooks, the prep sessions
that feed them and the rest of the week, and cook mode (the focused
single-meal screen) is a *step* of it. Meals is Plan only; the segmented
control is gone. Everything Kitchen used to say about the household — "What
we know" and "Something not working?" — is in a **Preferences** sheet behind
a gear in the header of every root screen. See the 2026-09-08 decision-log
entry for the whole shape. Chores still have no tab of their own. **Updated 2026-09-08 (Emily, option
1b on the Chores ticket):** the beta is meals-only, so Today's "Your
chores" card is hidden behind one front-end constant,
`SHOW_CHORES_ON_TODAY` in `static/shell.js` (next to `REHEAT_ACTION_LABEL`),
currently `false` — when false, `buildTodayPanel` neither renders the card
nor calls `loadChores`, so `/api/chores/today` (`app/main.py:1238`) isn't
requested either. Nothing else changed: `loadChores`/`renderChores`
(`static/shell.js`), the chores backend, and the standalone,
still-orphaned `/chores-setup` page (`app/main.py`, `chores_setup_page`)
are all untouched, and flipping that one constant back to `true` is the
whole reversal. Whether `/chores-setup` should be linked from anywhere
remains open on the Chores ticket — Emily's call.

**Cook-mode hands-free voice is hidden — Emily, 2026-09-08.** "Let's just
drop the cook mode voice for now. Just hide it, and we can rebuild it
later." `COOK_VOICE_ENABLED` in `static/shell.js` (beside `TABS`) gates it:
false means the mic button, spoken steps, and voice commands on the Cook
screen render nothing and create no `SpeechRecognition`/`speechSynthesis`
session. Code is intact, not deleted, for a later rebuild.

If you're picking this repo up fresh, run `git log --oneline -15` to confirm
this is still accurate.

**The "known live bug" this section used to carry is FIXED — corrected
2026-09-06.** For two days this file told every fresh session that
`/api/chat/stream` crashes on any turn returning an action card, and that
"no fix exists in this repository". Both sentences were true when written
and are now false: commit `6f81027` ("Fix TypeError crash in
/api/chat/stream when a reply carries an action card") is on `main`,
`app/main.py` imports `jsonable_encoder` (line 8), and `_sse_event` ends
with `json.dumps(jsonable_encoder(data))`. Verified against `main`, not
taken from a ticket.

Left as a warning rather than quietly deleted, because the failure was the
briefing and not the code. This file is the first thing any session reads,
so a stale "known live bug" is believed — it sends whoever reads it hunting
something that isn't there, and it survived two days precisely because it
looked authoritative. **If you record a live bug here, delete the entry in
the same change that fixes it.**

**That test gap is now CLOSED — corrected 2026-09-08.** This paragraph used
to say the gap "is still real": that
`tests/test_streaming_endpoints.py` stubs `summarize_chat_actions` to return
`[]`, so no test puts a real `ChatAction` through the encoder. Two of that
file's nine tests do still use the `[]` stub (five never touch
`summarize_chat_actions` at all), but two now push a real `ChatAction`
through on purpose —
`test_chat_stream_generator_delivers_a_reply_that_carries_an_action_card`
returns a real `ChatAction`, drives `_stream_chat_turn` directly, and
asserts the serialized `done` payload. It fails on the pre-fix `json.dumps`.

Same lesson as the entry above, one level down: the *fix* was recorded here
and the *test* that closed the gap was not, so this file kept advertising a
hole in the suite that had been filled. When you close a gap this file
names, correct the claim in the same change.

## Working style established so far

- **Sandbox-verify everything before calling it done.** Fresh/reset SQLite
  DB, real `uvicorn` process, Playwright with `/opt/pw-browsers/chromium`
  for anything UI-facing. Don't trust a fix until it's actually been run.
  For chat-agent behavior specifically, a real API call reproduction (stub
  `TOOL_FUNCTIONS` to bypass DB state if needed) beats reasoning from code
  alone — see the max_tokens entry in the Decision log for an example.
- **Keep the Decision log below updated** — every scope deviation, bug
  root-caused, and judgment call, honestly including things that were tried
  and didn't work. This is what makes the project legible to the next
  session (human or Claude), replacing the broken pointer this file used to
  have.
- A recurring local sandbox quirk (may or may not reproduce in your
  environment): `pkill -f uvicorn` and combined multi-step heredoc bash
  commands intermittently return exit 144 while silently not completing.
  Split DB-reset steps into separate, individually-verified commands rather
  than chaining them.

## Known architectural gotchas (don't re-discover these the hard way)

- **`app/tools/` is a package, and `app/tools/__init__.py` is its public
  face.** Every tool function is defined in a domain module
  (`recipes.py`, `grocery.py`, `weekly_plan.py`, …) and re-exported from
  `__init__.py`, so `from app import tools` / `tools.add_recipe(...)` works
  exactly as it did when this was one file. **If you add a new tool
  function, add it to `__init__.py`'s re-export list too** — otherwise
  `agent.py` and `main.py` won't see it.
  Two conventions hold the package together: `household_id()` (and
  `PUBLIC_BASE_URL`/`_absolute_url`) come from `_shared.py` and are
  defined nowhere else, and a call into *another* domain module is written
  `_grocery.add_grocery_item(...)` after `from . import grocery as
  _grocery`. The alias is not decoration: the domains are genuinely
  circular (grocery ↔ inventory ↔ pre-shop ↔ weekly plan), and importing
  the *module* rather than the *name* is what lets those cycles resolve at
  call time instead of exploding at import time. Underscore-prefixed
  aliases also keep the module name from colliding with the many local
  variables called `inventory`, `grocery`, `stores`, etc.

- **The household is request-scoped, and there is no `HOUSEHOLD_ID`
  constant any more.** `app/tools/_shared.py` defines `household_id()`,
  which reads a `ContextVar` that `security.auth_middleware` sets per
  request from the signed session cookie. The default is 1, so scripts,
  seeds and tests keep working unchanged. Three things to know before
  touching this:
  - **Never reintroduce a module-level household constant, and never
    capture `household_id()` at import time** (default argument, class
    attribute, module-level `X = household_id()`). It must be called
    *inside* the function, at request time. The constant was deliberately
    deleted rather than aliased so a missed call site raises NameError
    instead of silently reading household 1 — a loud failure instead of a
    cross-household data leak.
  - **A new public (unauthenticated) route has no cookie and therefore no
    bound household.** Whatever identifies the caller there — today, a
    share token — must bind it explicitly with `tools.use_household(...)`.
    This is exactly where the old code was wrong: `get_shared_weekly_plan`
    read the token's household to fetch its *name*, then served the
    hardcoded household's plan underneath.
  - The ContextVar reaching a `def` route depends on Starlette/anyio
    copying the context into the worker thread. That is verified, not
    assumed: `tests/test_multi_household.py` asserts it through a real
    request, so a dependency upgrade that broke it would go red rather
    than silently serving every household as household 1.
- **A single chat turn's `max_tokens` cap can be outrun by open-ended
  multi-tool work.** A request that touches many meal slots/recipes in one
  turn (e.g. rebuild a week's dinners with new recipes) can need more output
  than any fixed cap — reproduced needing 8+ unresolved tool_use blocks at
  16000 output tokens. `run_agent_turn` (agent.py) now retries the round
  instead of returning a dead-end apology when `stop_reason == "max_tokens"`,
  bounded by `MAX_TOOL_ROUNDS`. If you add another single-shot LLM call
  elsewhere in this file (there are several, each with their own
  `max_tokens`), consider whether it needs the same retry-on-truncation
  treatment rather than just a bigger fixed number.
- **Tab panels build once per page load.** `static/shell.js`'s
  `buildWeekPanel`/`buildTodayPanel`/`buildGroceryPanel`/etc. are guarded by
  `panel.dataset.built`, so switching tabs away and back does NOT refetch
  data. Any change made via chat (or otherwise) to an already-built panel's
  data goes stale until reload, unless something explicitly refreshes it —
  see `refreshStaleTabsFromActions()` in shell.js, which does this for
  chat-driven changes by reading each turn's `actions[].tab` field.
  **Grocery was the hole in that and is now covered** (branch
  `pomona-grocery-native`): the backend has always emitted `tab: 'grocery'`,
  but while that tab was an iframe there was no useful branch to write —
  a parent page cannot re-render part of a child document. If you add a new
  native panel, add its branch here too, or it will go stale in exactly the
  same silent way.
  **Kitchen was the other hole and is covered on `pomona-kitchen-cooker`.**
  Two things about that branch matter here: `tab: 'kitchen'` now refreshes
  *two* screens, because `check_off_meal`/`check_off_prep_step` are tagged
  kitchen but cooking lives under Meals; and an action with **no tab at
  all** can still make a screen stale — the household/preferences tools
  carry only `href: '/memory'`, which is exactly what the Kitchen hub's
  counts show, so `refreshStaleTabsFromActions` reads the href too.
- **"Current week" plan resolution** is centralized in
  `tools._current_weekly_plan_row()` (`app/tools/weekly_plan.py`) — it
  prefers the plan whose week
  actually contains today, falling back to most-recently-created only if
  none does. This used to be "just pick whichever plan has the latest
  `created_at`" everywhere, which silently drifted from "this week" the
  moment any other plan existed (a leftover old plan, a pre-planned future
  week). If you add a new "give me the current plan" call site, use this
  helper — don't re-derive the old raw query.
- **`generate_weekly_plan` (agent.py) generates before it persists.** It
  used to create the `weekly_plans` row first and fill it in after, which
  meant a failed/truncated LLM call left a permanently empty "current week"
  plan with no error shown anywhere. Now it only writes anything once it
  has real content, and raises a clear error otherwise (which the chat
  loop's existing per-tool `try/except` surfaces back to the model as a
  normal tool error).
- **Grocery quantity merging/display** goes through
  `_humanize_grocery_quantity()` (`app/tools/quantities.py`) — discrete
  items round up to a
  whole number (can't buy 1.5 onions), measurable units (tsp/tbsp/cup,
  oz/lb, g/kg, ml/l) roll up to the largest sensible unit. `scale_recipe`'s
  own scaling is intentionally NOT run through this — that's for cooking,
  not shopping, and wants precise amounts in the recipe's original unit.
  Multi-word container units ("1 lb bag") and prep-descriptor-bearing
  ingredient names ("Baby spinach, chopped") have both bitten this before —
  see Decision log.
- **A meal slot is never absent — it's one of three states.** Since Plan
  the Week, `meal_plan_entries.slot_state` is `planned`, `planned_empty`
  (nobody home, or the household asked for none of that meal) or `open`
  (a decision genuinely handed back, carrying an `open_reason` sentence).
  A `planned_empty` slot **must never be offered as a decision** — not as
  "Swap it", not as a gap in a chat summary, not through the slot API.
  Three separate bugs have already come from code treating it as a missing
  meal. `tools.audit_plan_slots()` asserts the whole week, and reports
  DUPLICATES as well as gaps: two rows for one slot is how a night nobody
  is home ends up with groceries bought for it.
- **Telling the generator something is not the same as preventing it.**
  The prompt says not to plan a dinner for a night the household is out;
  it sometimes does anyway. `agent._finish_week_slots` therefore *clears*
  the slot (`tools.clear_plan_slot`) before writing the deliberate empty
  row, instead of writing beside whatever is there. Any new rule of the
  form "the tag wins over the model" needs the same treatment — a prompt
  instruction is a request, not a guarantee.
- **`week_intake` is append-only, and that's load-bearing.** Never update a
  row in place; `tools.save_week_intake` copies the current revision,
  applies the change, and inserts revision+1. Anything that would have
  changed a Q1/Q2 answer — including a chat instruction like "cut it to
  four dinners" — must go through it, or regenerating silently reverts what
  the household just said. The revision is UNIQUE per
  `(household, week_start)` and the save retries on conflict; without that,
  two adults saving at the same moment lost one of their answer sets.
- **Per-category meal counts mean DISTINCT MEALS, not days planned.**
  `dinners_per_week: 4` is four different dinners spread across seven
  nights, not four nights fed and three blank. This changed with Plan the
  Week (it used to mean days) and it is what lets "every slot is filled"
  and the setup screen's "I'd rather plan four things you cook than seven
  you don't" both be true. 0 still means none at all, all week.
- Service worker (`static/service-worker.js`) is network-first for
  navigations/`.html`/`.js`/`.css`, cache-first only for icons/manifest —
  this was a real stale-cache bug once (`CACHE_NAME` bumped to `v3` to
  force-clear it). If a fix "isn't showing up" on a real device, suspect
  this before suspecting the code — especially on an installed iOS
  Home-Screen PWA, which is slower to pick up service worker updates than a
  normal browser tab.
- **`home_manager` logger's INFO output was silently dropped** until
  `main.py` added `logging.basicConfig(level=logging.INFO, ...)`. Nothing
  else in the app configures logging, and Python's default "handler of last
  resort" only surfaces WARNING and above — so any new `logger.info(...)`
  call anywhere in this app will actually show up now, but double-check
  this basicConfig call is still there if logging output ever goes quiet
  again.

## Decision log (root-caused bugs, judgment calls — append here as you go)

Newest first. Keep entries terse: one line of fact, one line of why. Full
detail lives in the commit that made the change (`git log --oneline` /
`git show <hash>`) — this log is for surfacing *that something happened and
why*, not duplicating the diff.

- **2026-09-11 — A draft whose week has ended is no longer the Plan tab's
  front page, and Plan and Now name ONE week. Branch
  `worktree-stale-draft`.** Seen on Friday 2026-09-11: Plan opened on "This
  week · Aug 24–30 · a draft, your turn" (twelve days dead) while Now asked
  "Shall I put Sep 7–13 together?". Root cause: `_current_weekly_plan_row`
  (`app/tools/weekly_plan.py`) falls back to the newest non-retired plan
  when nothing covers today — any dates, any status — while the nudge
  derived its own week from `suggest_planning_period`. Three changes, Emily's
  defaults: (1) **an expired draft retires** — `retire_expired_drafts()` is a
  lazy sweep (no scheduler) run by `get_week_menu` and
  `get_week_planning_nudge`; a draft whose last day is before today gets
  `status='retired'`, new column `retired_reason='expired_draft'` (a
  takeover writes `'superseded'`), period and meals kept. The fallback
  query refuses such a draft independently, so chat resolves the same way
  between sweeps. Threshold is the morning after the last day, no grace —
  a draft never touched the shopping list, and the day it can't be cooked
  from is the day the tab needs to open on the real week. Approved plans
  whose week has passed are NOT swept (they were the household's real week;
  the fallback still shows them — Emily may want that changed too). (2)
  **`suggest_planning_period` is the one source of "which week"**: the
  nudge's case 1, the Plan tab's empty state (`get_week_menu` returns
  `suggested_period`, shell.js adopts it as `planningPeriodDefault`) and
  `/api/week/planning-period` all read it; a test holds all three equal on a
  pinned Thursday and Friday. (3) **`PLAN_AHEAD_FROM_WEEKDAY = 4`**: from the
  fifth day of a period (Friday of a Monday week — the same distance in for
  a Saturday-start week, i.e. Wednesday) an unapproved current period is
  skipped and the suggestion is next week (`is_current_period` False → the
  eyebrow and the Plan head say "Next week"). *Approved* is the test, not
  merely live: a draft covering today stays on Plan but Now offers the
  week after. The same constant now opens the nudge for a planned week's
  successor (was Saturday; Friday now — one threshold, not two), via
  `_attention_moves_on`, which keeps the old "day before it ends" clause so
  a 3-day as-we-go horizon still gets its offer. **Onboarding is exempt**
  (`plan_ahead=False` in `_first_plan_window`): the person chose this week
  or next on the screen, and shifting under that would invert Julia's bug
  on a Friday sign-up — the four onboarding tests caught it on the first
  full run. 22 tests in `tests/test_stale_draft_front_page.py`; one
  assertion in `test_planning_periods.py`'s dismissal test moved with the
  rule (Saturday now offers next week under a new key). Suite 2794.
- **2026-09-11 — Pomona knows the holiday is coming and ASKS, never
  assumes. Branch `worktree-holiday-aware`, slice 1.** Loop Board
  "Holidays: Pomona knows 12 October is coming and asks how you're
  spending it". Emily's frame (2026-09-11): "it's a lot to assume that we
  would be making the big meal, we might be going over to someone's
  house. It's more that it should be aware of and accommodate holidays."
  So: `app/tools/holidays.py` computes Canadian holidays BY RULE (Easter
  formula, nth-weekday, province table; nothing pinned to 2026), and a
  calendar-feed all-day event whose title IS a holiday name counts the
  same way — exact-name-after-normalisation, because a substring match
  turned "Reid's birthday" (eid) and a 4-day "Reid at camp" into asking
  holidays. Multi-day spans never count (slice-1 limit; a two-day Rosh
  Hashanah is dropped). `holiday_answers` keyed (household, date) — a
  date, not a week revision, because the answer can come from Now, chat
  or the Days screen before any intake exists — with `households.country`
  / `province` defaulting CA/ON. The question, in the intake's Days screen
  and on Now from 3 days out (once a day, on the household's clock):
  "How are you spending Thanksgiving?" → Hosting / Going to someone's /
  Just us / Not sure yet. **Out** = the existing attendance write with
  nobody home for DINNER only (`source='holiday'`), so slot_needs goes
  `away`, the dinner is `planned_empty` and its groceries reverse —
  superseding, not overwriting, any quick/ready-made need so undo brings
  it back. **Bring a dish** = a `plan_meal` entry in that dinner slot with
  attendance left home (so it shops and cooks on the day); the out write
  is skipped, so the two never fight. **Hosting** = Build 4's `guests`
  night tag + `guest_counts` through `save_week_intake` (new revision,
  every other answer byte-identical) — the big-meal menu / split shop /
  timeline is slice 2 and reads `answer` + `headcount`. **A trip already
  covering the day wins**: hosting records the headcount only, just-us
  leaves the stretch's attendance and need alone (verifier caught the
  first version relabelling the trip's row and losing its link). Changing
  an answer hands the dinner back as an open question, never a blank.
  Planner: context + prompt line, and `apply_holiday_answers_to_plan`
  after the slot-needs pass so enforcement doesn't depend on the model
  reading. Asking holidays: New Year's, Easter Sunday, Mother's/Father's
  Day, Canada Day, Thanksgiving, Christmas, Boxing Day; the rest are
  label-only (one flag per row). QC gets National Patriots' Day + Fête
  nationale, no Civic; NL no Civic; blank province = national days only.
  US: `set_holiday_region` refuses until a US table exists. 57 tests in
  `tests/test_holidays.py`; verified live on a throwaway DB. Suite 2761
  before the merge with main's chores work.
- **2026-09-11 — Pomona knows the holiday is coming and ASKS, never
  assumes. Branch `worktree-holiday-aware`, slice 1.** Loop Board
  "Holidays: Pomona knows 12 October is coming and asks how you're
  spending it". Emily's frame (2026-09-11): "it's a lot to assume that we
  would be making the big meal, we might be going over to someone's
  house. It's more that it should be aware of and accommodate holidays."
  So: `app/tools/holidays.py` computes Canadian holidays BY RULE (Easter
  formula, nth-weekday, province table; nothing pinned to 2026), and a
  calendar-feed all-day event whose title IS a holiday name counts the
  same way — exact-name-after-normalisation, because a substring match
  turned "Reid's birthday" (eid) and a 4-day "Reid at camp" into asking
  holidays. Multi-day spans never count (slice-1 limit; a two-day Rosh
  Hashanah is dropped). `holiday_answers` keyed (household, date) — a
  date, not a week revision, because the answer can come from Now, chat
  or the Days screen before any intake exists — with `households.country`
  / `province` defaulting CA/ON. The question, in the intake's Days screen
  and on Now from 3 days out (once a day, on the household's clock):
  "How are you spending Thanksgiving?" → Hosting / Going to someone's /
  Just us / Not sure yet. **Out** = the existing attendance write with
  nobody home for DINNER only (`source='holiday'`), so slot_needs goes
  `away`, the dinner is `planned_empty` and its groceries reverse —
  superseding, not overwriting, any quick/ready-made need so undo brings
  it back. **Bring a dish** = a `plan_meal` entry in that dinner slot with
  attendance left home (so it shops and cooks on the day); the out write
  is skipped, so the two never fight. **Hosting** = Build 4's `guests`
  night tag + `guest_counts` through `save_week_intake` (new revision,
  every other answer byte-identical) — the big-meal menu / split shop /
  timeline is slice 2 and reads `answer` + `headcount`. **A trip already
  covering the day wins**: hosting records the headcount only, just-us
  leaves the stretch's attendance and need alone (verifier caught the
  first version relabelling the trip's row and losing its link). Changing
  an answer hands the dinner back as an open question, never a blank.
  Planner: context + prompt line, and `apply_holiday_answers_to_plan`
  after the slot-needs pass so enforcement doesn't depend on the model
  reading. Asking holidays: New Year's, Easter Sunday, Mother's/Father's
  Day, Canada Day, Thanksgiving, Christmas, Boxing Day; the rest are
  label-only (one flag per row). QC gets National Patriots' Day + Fête
  nationale, no Civic; NL no Civic; blank province = national days only.
  US: `set_holiday_region` refuses until a US table exists. 57 tests in
  `tests/test_holidays.py`; verified live on a throwaway DB.
- **2026-09-11 — Every chore has a chosen owner: owned / shared / whoever.
  Branch `worktree-chore-owner-mode`.** Loop Board "Chores v1: Every chore
  has a chosen owner" — Emily, 2026-09-11: "Owners should be chosen."
  Before: rotation-only (`rotation_member_ids_json` round-robin in
  `generate_chore_schedule`), no owner default, no record of who ticked.
  Now `chores.mode` ∈ {owned, shared, whoever}, **owned is the default**;
  owner = `default_assignee_id` when owned (no second "who" column — the
  migration `_migrate_chore_modes` in `app/db.py` is a pure derivation:
  one person → owned, several → shared, none → whoever; idempotent, runs
  every startup, tolerates malformed rotation JSON). Engine assigns by
  mode; `whoever` → NULL assignee. `chore_instances.completed_by_member_id`
  records the doer separately from the assignee (both kept for the v2
  fairness view); an owner change reassigns pending instances only, done
  ones keep their person, and a shared turn continues after whoever
  actually did the last one. Chat: `add_chore`/`update_chore` take `mode`
  + `owner_name` ("give the bathrooms to Vineeth" → owned; "let's take
  turns" → shared; "either of us" → whoever); `complete_chore` takes
  `done_by`. **Naming someone is never how a member gets created here**
  (verifier round 1 caught "Vinneth" inventing a person and a typo'd
  `done_by` crediting the signed-in adult): names resolve exact, then
  unique first name, else a calm question back; the save route skips and
  reports such a row instead of half-saving. Starter list proposes an
  owner per row, dealt round the setup rotation when the model leaves one
  blank. API rows carry `mode`, `owner`, `up_next`, `who_label` ("either
  of you" only with exactly two adults, else "anyone") for the screens the
  Plan | Chores and Now-card cards will build; the hidden Now card prints
  `who_label` behind the unchanged `SHOW_CHORES_ON_TODAY = false`. Left
  for those cards: the toggle screen, per-row editing in the (orphaned)
  wizard, how "who's up" reads. 61 tests in `tests/test_chore_owner_mode.py`
  incl. migration against a DB built from main's schema and cross-household
  isolation of every new path. Suite 2772.
- **2026-09-11 — Inventory wears an "In development" pill now. Branch
  `worktree-inventory-in-development`.** Loop Board "Inventory: mark as
  still in development" — Emily's 2026-09-11 call: inventory stays a
  not-ready beta feature while staples ships first, and a tester shouldn't
  spend effort (or feedback) keeping it up to date. Labelled, not hidden:
  receipt/fridge/pantry scans still land items there, and a hidden screen
  would make scanned items vanish somewhere nobody can see. Two entry
  points get the neutral pill (`pill pill-neutral`, celadon, never
  apricot): the Kitchen tile in `kitchenTilesHtml()` and the desktop rail
  row — the rail one is appended by JS, not baked into `shell.html`,
  because a `.pill`'s own `inline-flex` beats `[hidden]` (same lesson as
  `SHOW_NOTIF_BELL`). One calm line at the top of the sheet: "Inventory is
  still being built. Nothing else in Pomona depends on it, so there's no
  need to keep it up to date." Gate is `INVENTORY_IN_DEVELOPMENT` beside
  `SHOW_CHORES_ON_TODAY` in `shell.js` — **plus a second copy inside
  `inventory.html`'s own script**, because that sheet is a separate
  document (standalone at `/inventory` and iframed into Kitchen) and can't
  see `shell.js`'s scope. Turning it off later is two one-line flips, not
  one. 7 source-pinning tests (`tests/test_inventory_in_development_marker.py`,
  pattern of `test_cook_voice_hidden.py`); one older assertion narrowed
  from `">Inventory<"` to `kit-row-title">Inventory`. Verified live at
  desktop and 375px (no wrap, no rail at phone width). Suite 2709.
- **2026-09-11 — Tapping tonight's dinner on Now 500'd when the only plan
  on file was an old week. Branch `worktree-needs-you-old-plan`.** Loop
  Board "Now: 'needs you' dinner card 500s when the only plan on file is
  an old week" (Phase 0 bug, seen on a throwaway DB while verifying the
  redesign). Reading the card was fine — `get_needs_you_items` reads
  `meal_plan_entries` by date, not by plan. The tap was the failure:
  `resolve_needs_you_dinner` (`app/tools/weekly_plan.py`) asked
  `get_weekly_plan()` for "the" plan, which falls back to the newest one
  when no period contains today, and handed that stale id to `plan_meal`,
  whose period guard (`app/tools/meal_plans.py` ~120) rightly refuses to
  file a meal where no screen would show it. Fix: pass the plan id only
  when its period actually covers the picked date, otherwise `None` — the
  same unlinked shape a one-off chat-planned meal already has, which Now,
  `get_meal_plan` and the grocery buffer all treat as first-class. Two
  tests (tool + HTTP route) fail on main with the exact error. Verifier
  checked the four neighbours: no plan / old + current (attaches to the
  current) / future-only / downstream grocery — all fine, and the
  unlinked meal is visible tonight, so this isn't a 500 traded for a
  silent loss. Suite 2704.
- **2026-09-11 — The morning text: anticipation OUT of the app. Branch
  `worktree-reach-me`, NOT merged at the time of writing.** Loop Board
  "Reach me before the moment" (Emily: "prioritize the push
  notifications"; text first, not email; push once it's in the App
  Store). Step 0, its own commit: the bell refetches on
  `visibilitychange`/`focus` and every 5 minutes while visible
  (`refreshNotificationsIfDue`, shell.js) — it was fetched once per page
  load, so an installed PWA showed a stale bell for days; still a no-op
  while `SHOW_NOTIF_BELL` is false. Channel 1: `app/tools/digest.py` —
  `build_morning_text()` off `today_moves()` and the live feed (tonight,
  the freezer, the shop, the prep, one attention item; ≤300 chars, link
  last, nothing → no text); `send_digest(channel, …)` seam with text via
  Twilio's REST API over `urllib` (no new dependency; email is a name in
  `CHANNELS`, not code); `run_morning_texts_once()` called by an
  in-process loop in main.py mirroring `start_backup_loop` (poll every 5
  min, `DISABLE_MORNING_TEXT=1` opts out, missing `TWILIO_*` keys = logs
  once and never starts). Once a day per person per LOCAL day via
  `morning_text_sends` (UNIQUE household/member/sent_on; statuses ok /
  failed / skipped-empty / skipped-no-keys / skipped-late) — a restart
  never double-sends; a missed morning sends once if under 8h late.
  **The first per-household time zone**: `households.timezone` (IANA,
  default `America/Toronto` — an assumption for every existing row) and
  `households.morning_text_time` (default 07:00); nothing else reads
  the zone yet, on purpose. Numbers are `members.phone` (E.164, 10
  digits assumed +1) + `members.morning_text_on`, adults only, keyed by
  member row so the per-adult login composes. UI: "Morning text" row in
  the Preferences sheet opening its own sheet (time, per-adult number,
  On/Off, spruce Save — inputs are the honest control here); chat tool
  `set_morning_text(phone, time, on, name, timezone)`. Report:
  `morning_texts` in `/api/observability` and one line in
  `observability_report.py`. Emily's part: a Twilio account, a Canadian
  number, `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`/`TWILIO_FROM_NUMBER`
  on Railway (trial accounts text verified numbers only).
- **2026-09-11 — Swapping a meal is ONE transaction now. Branch
  `worktree-swap-atomic`.** Loop Board "Swapping a meal isn't atomic either
  — the same seam, one level down, shared by every swap in the app": the
  debt drop-dish-atomic recorded the same morning rather than smuggled in.
  `swap_meal_in_plan` unlinked the chain, reversed the groceries, DELETEd
  the displaced row, committed, then called `plan_meal` to INSERT the
  replacement and buy for it — four commits, and a failure after the
  delete left a day with **no row at all**, its old groceries gone, under a
  route answering 500. Reproduced first with the characterisation test the
  drop-dish work left behind (`..._is_NOT_fixed_here` passed on `4469563`),
  and it is that function that matters: it is the write behind
  `add_dish_day` (the Check-the-week "+"), every chat swap, `swap_in_place`
  and its undo, and `plan_quality.repair_snack_clashes` at generation —
  and `resolve_open_slot` carried a second copy of the same four commits.
  - **The shape is the two prior fixes', not a third.** A new
    `_replace_slot_entries` owns one connection, one commit, `rollback()`
    on the way out; `swap_meal_in_plan` and `resolve_open_slot` both call
    it. `plan_meal`, `add_grocery_item`, `_record_grocery_link`,
    `WeekGroceryBuffer`, `_add_recipe_ingredients_for_entries`,
    `_reingest_unlinked_entries`, `_rescale_leftover_source_grocery`, and
    the read helpers under the ingest (`plan_leftover_chains`,
    `batch_for_source`, `eaters_at`, `get_slot_attendance`,
    `grocery_scale_factor`, `servings_scale_factor`) all take an optional
    `conn=None`: given one they read and write on it and neither commit
    nor close; left unset every other caller — chat `plan_meal`,
    `approve_weekly_plan`, `clear_plan_slot`, `drop_dish_from_day` — is
    byte-for-byte as before.
  - **Nothing runs after the commit this time, and that is the difference
    from drop-dish.** The step drop-dish had to lift out — the leftover
    source's grocery rescale — could not join because it re-enters the
    recipe ingest tree, and every function in that tree opened its own
    connection. This ticket gave that whole tree a `conn` (it had to: the
    new meal's OWN ingest is the deepest seam, and it reads the row the
    transaction has only just inserted, which no second connection could
    even see). Once the tree joins, the rescale and the re-buy for stranded
    reheat nights join for free, so the swap has no logged-not-raised tail.
    `drop_dish_from_day` still runs its rescale after its commit, untouched
    — its own card's tests pin that arrangement, and un-deferring it is a
    two-line change for whoever next opens that function.
  - **What is guaranteed, precisely.** `_replace_slot_entries` opens with
    an explicit `BEGIN IMMEDIATE`, so the write lock is held from its
    FIRST read (the plan's approved state, the chain map, the unlink's
    reads) to the commit — not, as sqlite3's legacy `isolation_level=""`
    would otherwise give, only from the first DELETE. The independent
    review found the gap: a second writer swapping the same slot and
    flipping the plan to draft between those reads and the DELETE left
    two rows on one day and bought groceries for a draft. The one read
    that cannot be inside the lock is the caller's own — `swap_meal_in_plan`
    and `resolve_open_slot` resolve the old row ids on a connection of
    their own first — so the DELETE's rowcount is checked against the ids
    it was given and a mismatch raises and rolls back ("changed under this
    swap … try again") rather than planning a second meal on top of
    whatever replaced it. Both pinned: `conn.in_transaction` is asserted
    from inside the first read, and a test drives a second connection into
    the gap and gets one row on the day (the other writer's) and nothing
    else moved. `tests/test_swap_atomic.py` also counts `get_conn` across
    weekly_plan, grocery, meal_plans, recipes, leftovers and attendance for
    the whole of `_replace_slot_entries` and requires **exactly one** — for
    a plain approved swap, for swapping a reheat night (unlink and rescale
    inside), and for swapping the cook night (re-buy inside).
  - **Reads that used to be stale are correct now, as a side effect.** In
    rollback-journal mode a read on a second connection does not deadlock,
    it silently sees the pre-transaction world; the chain check inside the
    ingest was one of those. Not a bug anyone hit (ids AUTOINCREMENT, so a
    deleted row's id is never reused), but the rule "one connection inside
    the transaction" is stricter than "no second writer" for that reason.
  - **Left out, deliberately.** `swap_component_in_plan` (component-based
    plans) has the same delete-then-`plan_meal` shape and is the one
    remaining caller not routed through `_replace_slot_entries`; component
    plans have no slot-per-day rule to break, so it is a smaller wrong and
    a separate card. `plan_meal` on its own connection still commits the
    row before the ingest, exactly as before — changing that would change
    chat and generation behaviour this ticket is not about.
  - `tests/test_swap_atomic.py`: 26 tests, **20 red on `4469563`** — a
    forced failure after the delete, inside the INSERT itself (a
    connection subclass that refuses that one statement), inside the
    ingest, after the reversal, after the unlink, inside the rescale,
    inside the re-buy; one per caller (`add_dish_day` and its route, a
    chat two-snack swap, `swap_meal_in_place` with the picker stubbed,
    `resolve_open_slot` and its route, the snack repair inside a stubbed
    generation); the connection count; the transaction-is-open check; the
    lock-held-from-the-first-read pin; and the second-writer race. The
    other six are happy paths measured against the parent commit. The
    characterisation test in `tests/test_drop_dish_atomic.py` is inverted
    in place (`..._is_now_atomic_too`, red on `4469563`), so the history
    reads. 2507 -> 2533.
  - **Still open, on the card as notes.** `swap_in_place` runs
    `_write_entry_note` and `get_week_menu` AFTER the committed swap; a
    failure there answers 500 over a swap that landed — a valid row, never
    a missing day, so a different and smaller wrong. `swap_component_in_plan`
    has the same seam (above).
- **2026-09-11 — The plan can see the household's calendar. Branch
  `worktree-calendar-read`.** Loop Board "Meals: plan the week around
  what's actually on the household's calendar" (Phase 1.5, the top finding
  of the 2026-09-10 feature-gap research): Pomona planned a 50-minute
  braise on the night of the 6pm practice sitting on the very phone it was
  running on. The card said Google Calendar; the build reads a calendar's
  private **subscribe link** (iCal/ICS — Google's "Secret address in iCal
  format", Apple's public-calendar link, Outlook's "Publish calendar" ICS)
  instead of Google OAuth, because OAuth needs a Google Cloud app plus
  Google's multi-week sensitive-scope review, which no session can do
  (assumption on the card, Emily to confirm; a Google "Connect" button
  layers on top later, everything downstream is identical). READ-ONLY by
  construction — `app/calendar_feed.py` only ever GETs. Fetching reuses
  `recipe_import.fetch_page`'s SSRF guard, refactored into a shared
  `fetch_text` (one fetcher, not a second one drifting): public addresses
  only, pinned connect, ≤3 redirects, 3 MB, 15 s wall clock. ICS is parsed
  with the stdlib: folded lines, DTSTART/DTEND with TZID / `Z` / all-day,
  DURATION, RRULE DAILY/WEEKLY (INTERVAL, COUNT, UNTIL, BYDAY) plus plain
  MONTHLY/YEARLY for birthdays, expanded only inside the requested window,
  EXDATE, RECURRENCE-ID overrides, STATUS:CANCELLED skipped; anything more
  exotic (BYSETPOS, ordinal BYDAY) is skipped and counted, never guessed.
  Times land on the household's clock: the feed's `X-WR-TIMEZONE` (Google
  always sets it), else the zone most events are written in, else the
  optional `HOUSEHOLD_TIMEZONE` env var, else the server's local zone —
  there is still no per-household timezone setting (`meal_plans.py` notes
  the same gap). Planning input: per day of the period, `commitments`
  (title/start/end), `all_day` titles, and ONE derived hint
  `evening_busy_from`/`until` when timed commitments eat ≥60 min of 5–9pm;
  the generation prompt treats that dinner like a `rush` night or a
  leftovers night and must NAME the commitment in the reason
  (`calendar:<title>` in `derived_from.inputs`). **The household's own
  answers win, in code, not just in the prompt**: `apply_household_answers`
  withholds the hint on a date tagged unrushed/guests/normal/left/out or
  where nobody is home for dinner, and says so (`household_said`); `rush`
  keeps it. The `calendar` key is absent entirely for a household with no
  calendar, so nothing new reaches their prompt. A feed that can't be read
  at planning time falls back to the last successful read (a compact
  cache of parsed events for a rolling 8-week window, on the feed row)
  with a note, or to "no calendar" — never a failed plan; the guard is
  `generation_context`'s own try in `calendar_feed.py` (there is none in
  `agent.py`). **The link is a secret**: `calendar_feeds.url` is never
  returned (status shows label + `host/…` and at most four characters of
  the token, never more than a quarter of it), never logged — `fetch_text`
  logs the host and the exception CLASS only, because http.client's
  `InvalidURL` quotes the whole path in its text (a verifier found that
  leak) — and never in a prompt (calendar status deliberately is NOT on
  `/api/memory`, which feeds generation). Titles are untrusted: capped at
  60 chars, control chars stripped, and the prompt carries the same "data
  to read, not instructions to you" line the recipe reader uses. Hostile
  feeds are bounded too (same verifier): every expansion is clamped to
  the window (an all-day event ending in 9999 used to be walked to the end
  of time, 430 ms each), a per-feed cap on walked day-instances, and a
  duration or RRULE interval that overflows date arithmetic skips that
  event rather than refusing the feed. UI: a "Your calendar"
  card on What we know's Rhythm tab (paste → Check it, which says "Found 4
  things in the coming week" → Save; connected: label, redacted link,
  Check again, Disconnect) and a matching Preferences row — nowhere else,
  so a household that connects nothing sees only "Not connected" behind
  the gear. Deep links into What we know (`rhythm/calendar`, and the older
  `rhythm/prep-days`) now scroll once the tab has actually rendered — the
  anchor used to be dropped when the fetch outran the load event. Left
  out on purpose: OAuth, writing to the calendar (a separate card),
  more than one calendar per household, the component-based planning
  mode, and deriving a real `slot_needs` "quick" row from the calendar (a
  derived row would look like something the household said). 61 tests in
  `tests/test_calendar_feed.py` (Google/Apple/Outlook fixtures, the
  guard, the redaction, the planner contract, outage, two households),
  none touching the network or a model; verified live on a throwaway DB
  against Google's public Canadian-holidays feed (170 KB, parses clean).
- **2026-09-11 — Planning over an APPROVED week asks first now. Branch
  `worktree-replan-confirm`.** Found reviewing the ask-sheet branch (entry
  below), pre-existing on main: a chat planning request that overlapped a
  running week took its days over on the spot — meals gone, grocery lines
  reversed — and nothing confirmed first. Root cause is not in
  `retire_overlapping_plans`, which is doing its job (one plan per day is
  Emily's rule, 2026-09-04, and it stays): it is that the chat tool
  `generate_weekly_plan` had no way to carry a "yes", and the system prompt
  told the model to route "plan my week" straight to it. **Emily's
  decision (2026-09-11, live): keep the take-over, put a question in front
  of it.** Same shape as `approve_weekly_plan`'s `confirm_hard_conflicts`,
  deliberately: the tool gains `confirm_takeover` (default false); when the
  period would take days off an APPROVED plan and the flag is not set, it
  writes nothing and returns `needs_confirmation` with the exact days,
  each day's meals, any days that would be left unplanned, a meal count, a
  shopping-list count READ off the grocery ledger (bought and in-cart
  lines excluded, same rule as the takeover; the card's own criterion is
  that the number matches what then happens, and a test holds it to that)
  and a `note` the assistant can say as-is ("I'd replace Thursday to
  Sunday's dinners — Bean Chili, Salmon — and 11 things on your shopping
  list would change. Go ahead?"). "Change", not "come off": a line a
  surviving meal still needs is trimmed, not removed. **Zero has three
  meanings and the first version conflated them** (review caught it with
  an approved week whose overlapping days were all "out" being told "it's
  all bought already"): none needed but some bought -> "it's all bought
  already"; meals but no lines at all -> "Nothing on the shopping list
  changes"; no meals on those days -> "Nothing's planned for Thursday to
  Sunday, so nothing would be lost." Each shape has its own test, and the
  dinner-name dashes only close when a clause follows them ("— Bean Chili
  —." was the other review finding). The prompt rule matches the hard-conflict one word for word
  where it matters: never on its own initiative, never on the first call,
  only after a yes in this conversation. `tools.preview_approved_takeover`
  is the read-only half and runs the REAL decision (`_plan_takeover`, with
  no new plan yet), so the days it names are the days that would go —
  including the orphaned side of a period strictly inside a week.
  - **Drafts are still taken over silently.** Nothing of a draft's has
    reached the shopping list; replacing one is what re-planning means.
  - **The plan-week screen is not asked twice.** Its routes
    (`/api/week/{week}/generate` and the stream) pass the flag themselves,
    because plan-week.html already says what re-planning an approved week
    means before the five minutes of questions. Onboarding's first-plan
    routes pass it too: the reveal has no way to ask, and a first plan is
    by definition one from a household with nothing approved.
  - **Left alone, and worth Emily's eyes:** plan-week's own warning line
    says "I'll add anything new — I won't take anything off", and that is
    not what a takeover does to a still-`needed` line (bought and in-cart
    lines are the ones left alone). Pre-existing copy, not this card. Also
    left: that screen only warns about the SAME week's plan, so a long
    period from it running into a different approved week is not warned
    about there either. And the flag is honour-system from the model's
    side, exactly as `confirm_hard_conflicts` is — the tool cannot see the
    conversation to check a yes was actually given.
  - `tests/test_replan_confirm.py`: 18 tests, 15 red on main (the
    reproduction, the days/meals naming, the orphan case, the count tests
    and the three zero shapes, the dash artifact, confirmed proceeds,
    approved-beside-draft, the schema/prompt markers and the signature
    default); the 3 green on main are the promises that nothing else
    changed (drafts, no-overlap, the plan-week route). Six tests in
    `test_planning_periods.py` that generate over an approved plan to test
    the takeover mechanics now pass `confirm_takeover=True` — they test
    what happens after the yes. 2235 -> 2253, all green. Not verified
    against a live model: the tool's refusal and the prompt rule are
    certain, the model's choice to ask is inferred from the instruction.
- **2026-09-11 — Grocery list photo scan: a fourth sibling to the
  receipt/fridge/pantry scans, not new infrastructure. Branch
  `worktree-grocery-photo-list`.** Loop Board card, from Emily's Clementine
  research: photograph a handwritten list or screenshot a digital one and
  have Pomona add it. Reused everything the card's own notes pointed at —
  `_read_scan_image`/`_MAX_SCAN_IMAGE_BYTES` (`app/main.py`), the
  forced-tool-call `_scan_image_for_items` pattern and its shared
  `submit_scanned_items` schema (`app/agent.py`), and
  `tools.add_grocery_items` for the actual save, so a confirmed scanned
  item gets the same duplicate-quantity consolidation a typed item does.
  New: `agent.scan_grocery_list_image` (a grocery-specific prompt — item
  name as bought, quantity only if actually written, handwritten AND
  screenshot explicitly in scope), `POST /api/grocery-list/scan` (draft) and
  `POST /api/grocery-list/confirm-scan` (save). Frontend: a camera-icon
  button next to the LIST step's manual "Add" row in Shop (`static/shell.js`
  `groFootHtml`/`groScanUploadPhoto`), opening a body-level review sheet
  (`#gro-scan-sheet` in `static/shell.html`, wired near `weekSheetScrim`
  rather than inside the Grocery function cluster — `onGroceryClick`'s own
  region runs under Node with no `document` in
  `tests/test_grocery_fast_sort.py`, and touching `document` at load time
  inside that region broke all 38 of its tests until moved). Voice per
  DESIGN_SYSTEM §8: "Here's what I read — untick anything I got wrong."
  **Where the button lives was an explicit open question on the card**
  (Emily's call to make, not this session's) — the inventory scans it
  borrows the pattern from actually live on a different tab entirely
  (Kitchen's Inventory sheet, `static/inventory.html`), so "next to the
  existing scans" and "on the Shop tab" couldn't both be followed literally;
  built next to the nearest existing "put something on the list" control
  instead (the manual add row) as the stated default, flagged for her to
  override. `tests/test_grocery_photo_scan.py`, 15 tests, all red on `main`
  (the routes don't exist there) and green on the branch; full suite 2250
  passed (2235 before this ticket — the difference is this card's 15 plus
  what landed on `main` in between). Verified live against a throwaway DB:
  the button renders 44x44, the review sheet opens, edits/unticks work, and
  a confirmed item round-trips onto the real Shop list through the normal
  add path — the model call itself is stub-tested only (no real Anthropic
  key in this environment).
  - **Independent-verifier catch, fixed same day: the sheet's save button
    was a SECOND apricot** (DESIGN_SYSTEM §2 rule 5). Shop's LIST step
    already spends its one apricot on `.gro-primary` ("Start the trip"), so
    `.gro-scan-save`'s own `var(--apricot)` fill was a real violation, not a
    nitpick. Every other body-level confirm sheet in the app
    (`week-sheet-back`, `reset-confirm`, `dinner-confirm-add`) already uses
    `.btn-gold` for exactly this reason — switched to the same convention
    rather than inventing a new class, and resized `.gro-scan-cancel` to
    match it (44px/12px) so the pair reads as one row. Added
    `test_the_scan_review_sheets_save_button_is_not_a_second_apricot`
    (`tests/test_grocery_photo_scan.py`) as the source-marker guard, the
    same pattern `tests/test_grocery_steps.py`'s sibling apricot/spruce
    checks already use for LIST's other controls.
- **2026-09-11 — The grocery list survives no signal. Branch
  `worktree-grocery-offline`.** Reproduced first: kill the server mid-trip,
  tick one row → `groDo` failed, its follow-up `loadGrocery()` failed too and
  the whole trip screen was replaced by *"Couldn't load the grocery list"*;
  reload → same, list gone. The shell itself already loaded offline (the
  worker is network-first with cache fallback), so the gap was the data,
  not the page. Built: `static/grocery-offline.js`, a DOM-free module that
  keeps three things in localStorage per household — the list as the server
  last sent it (`pomona.grocery.copy.h<id>`), a queue of ticks made since
  (`…queue.h<id>`), and the `/api/memory` shops answer (`…shops.h<id>`,
  without which an offline reload put the "where do you shop?" card up over
  the list). `loadGrocery` saves the copy on success and renders copy+queue
  when fetch throws; `trip-toggle`/`uncheck` go through `groTick` (applied
  locally at once, POSTed straight away when online, queued when not);
  `groReplayQueue` sends oldest-first on load, on the `online` event, on
  every tick/refresh, and every 30s while anything waits. One entry per
  row (last-write-wins). An op is spent the moment the server ANSWERS
  (404/500 dropped, with a toast); only fetch-threw stays queued.
  - **Server side**: `mark_grocery_item` is a no-op when the status is
    unchanged — a replayed or double-tapped "purchased" used to add to
    inventory twice.
  - **Worker `v5 → v6`**: offline navigations fall back to any cached shell
    route ("/", "/grocery", "/week", "/kitchen" all serve `shell.html`, but
    the cache is keyed by URL, so a phone that reached Shop by tab-tap had
    nothing under `/grocery`); redirected navigations (signed out → /login)
    are never cached. **To bump again**: change `CACHE_NAME` in
    `static/service-worker.js` — the activate handler deletes every other
    cache — and add a dated note above it saying why. Only needed when the
    worker's own logic or a cache-first asset (icons/manifest) changes;
    JS/CSS/HTML are network-first and pick themselves up.
  - **Out of scope, on purpose**: add/remove/store offline (an offline add
    would need a temporary id and a mapping on replay), "Done at <store>"
    offline (it writes `purchased` + closes the trip; its toast now says
    *"No signal — try that once you're back"* rather than "try again"),
    every other tab, and anything Claude-driven. Conflict rule is
    last-write-wins; a partner's removal wins over this phone's tick (404
    → dropped, toast).
  - **Household isolation is the session's, not the phone's (verifier,
    same day).** The copy/queue/shops keys are read only for a household
    the server has named THIS session or that a still-signed-in session
    remembered; sign-out and any 401 call `groOffline.forget()` (every
    `pomona.grocery.*` key and the pointer go); a different `household_id`
    from `/api/coaching` purges the previous household's keys before
    anything is written, and drops an offline copy already on screen. An
    unknown household + no signal shows a calm wait ("No signal — I'll
    show your list as soon as you're back."), never a guessed list. Whatever
    the page fetched before coaching answered is held in memory and moved
    under the right key when it does. Queue entries are validated on read
    (a `null` used to jam replay forever). Ask and the inventory scan say
    "I need a signal for this one" with no signal instead of "Error:
    Failed to fetch". Dropped-ticks toast is count-aware and plain.
  - `tests/test_grocery_offline.py` (40): the module and the shell's own
    `loadGrocery`/`groTick`/`groReplayQueue` run under node with a switchable
    fetch, the worker run under node with stub `caches`, and the route's
    idempotency. The harness caught two real bugs the browser session had
    not reached — `replay()` being handed a `(id, status)` poster instead
    of `(url, body)`, and a replay called while the previous one was still
    settling inheriting its stale "no signal" answer.
- **2026-09-11 — Stepping a dish down is ONE transaction now. Branch
  `overnight/drop-dish-atomic`.** The debt the review-stepper work filed
  rather than smuggled in (see its entry below, and `99db198` where it has
  been sitting on `main`): `drop_dish_from_day` unlinked the chain,
  reversed the groceries and deleted the row, then called `plan_slot_open`
  to write the `open` row — four separate commits. Reproduced against the
  real code before touching it, and again afterwards: force a `RuntimeError`
  inside `plan_slot_open` and on `main` the day ends up with **no rows at
  all**, its grocery line already reversed, `audit_plan_slots` reporting the
  slot MISSING, and the route answering 500 so the screen says *"nothing
  changed"* — false, and the one state `schema.sql` says can never exist.
  On the branch the same forced failure leaves the row `planned`, the
  grocery line intact and the audit clean.
  - **The fix is the `atomic-period-takeover` shape, not a second
    implementation.** `plan_slot_open` and `_unlink_leftover_target` join
    `_reverse_meal_grocery_contributions` in taking an optional `conn`:
    given one they read and write on it and neither commit nor close, and
    left unset every other call site behaves exactly as before. One
    connection, one commit, `rollback()` on the way out.
  - **The step that CANNOT join the transaction, and why that is the whole
    difficulty.** `_rescale_leftover_source_grocery` re-ingests through
    `add_grocery_item` and the recipe ingest tree, every one of which opens
    its own connection — and SQLite gives one writer at a time, so called
    from inside the open write transaction it would sit behind that
    transaction's own lock and die of "database is locked". So
    `_unlink_leftover_target` SKIPS it when handed a connection and returns
    the source entry id instead, and the caller runs it AFTER the commit.
    A failure there is logged, not raised: the day has already been handed
    back, and raising would report "nothing changed" over a change that did
    happen. The cost of the log is one night's share of over-buying.
  - **No nested `get_conn` is pinned by counting them**, the way
    `tests/test_planning_periods.py` does, because the failure mode is an
    intermittent "database is locked" rather than a deterministic wrong
    answer.
  - **`add_dish_day` — the "+" on the same screen — HAS the same seam and is
    deliberately NOT fixed here.** It is one level down and not in
    `add_dish_day` at all: the write is `swap_meal_in_plan`, which deletes
    the displaced row, commits, then calls `plan_meal` to write the
    replacement. Force `plan_meal` to fail and the target day is genuinely
    absent, exactly as a drop used to leave one. That seam is shared by
    EVERY swap in the app — chat, swap-in-place, `resolve_open_slot`, the
    generation repairs — so closing it means threading a connection through
    the app's central write and the grocery ingest behind it. Its own card.
    `test_the_stepper_going_UP_has_the_same_seam_and_is_NOT_fixed_here`
    characterises it so the next session finds it written down instead of
    rediscovering it; **invert that test when `swap_meal_in_plan` is made
    atomic.**
  - `tests/test_drop_dish_atomic.py` is the guard, 13 tests, **9 of them red
    on `5f638fc`**; the other four are the happy path, the no-regression
    check that every other `_unlink_leftover_target` caller still rescales,
    and the characterisation above, each saying so in its own docstring.
    2169 -> 2182. Driven over the real route on a throwaway DB as well:
    dropping an approved week's only dinner leaves exactly one row for that
    slot, `open`, carrying "You cut Bean Chili back, so this one is yours to
    fill.", with the grocery line gone.
- **2026-09-11 — The ask sheet offers four named jobs instead of two
  guesses. Branch `overnight/ask-sheet-named-intents`.** Emily's decision,
  2026-09-10, from the Clementine research: a blank box makes a household
  guess the magic words, so the sheet leads with four real jobs said the
  way a person says them — *Plan the rest of my week*, *What should I cook
  tonight?*, *Swap tonight for something quicker*, *What do I need to
  defrost?*. **The machinery was MOSTLY right**: `renderAskChips` already
  drew the row twice (inside the ask sheet on a phone, and in the desktop
  Ask column) and already ran a chip on tap — but it had a second branch,
  `prefill`, that focused the composer instead, and one of the two old chips
  took it. Nothing produces a `prefill` now, so that branch is gone rather
  than left standing under a comment describing a chip that no longer
  exists. (An earlier draft of this entry said the machinery "has always RUN
  a chip on tap rather than pre-filling it" and then, three bullets later,
  that the grocery chip "PRE-FILLED the composer instead of running". Both
  could not be true; review caught it.) **Not "the dock"** — that is
  `#ask-bar-dock`, which carries the coaching examples; the intents live in
  `#ask-chips` and are on screen only once the sheet is open.
  - **FIXED, not context-aware, and that is a measurement decision rather
    than a taste one** (Emily). Context-aware chips are the better product
    and are where this goes next; a fixed set ships now and answers the
    question nobody can currently answer — which intents people actually
    tap. This **supersedes the card's own acceptance criterion** that an
    intent which would do nothing useful is not shown; her decision is the
    later word, and the criterion is noted as superseded on the card rather
    than quietly dropped.
  - **TWO COSTS OF THAT, and the first draft of this entry only counted the
    harmless one.** On a household with NO plan, three of the four ask about
    a week that does not exist; the assistant answers honestly, so the price
    is a wasted tap. **The opposite direction has teeth**, was found by
    review, and is the reason this branch went back to Emily before merge:
    the pair this replaces offered a planning chip ONLY under `!hasPlan`, so
    a household already mid-week was never shown one. "Plan the rest of my
    week" is now one tap for them, `agent.py` routes an explicit planning
    request straight to `generate_weekly_plan` with no confirmation step,
    and `retire_overlapping_plans` has **no exemption for an APPROVED
    plan** — so the days it takes over lose their meal rows and have their
    grocery lines reversed. Two things cut the other way and are why this is
    a question rather than a defect: the chip's wording points at the
    remaining days, which is what taking those days over *means*, and that
    behaviour is Emily's own documented rule ("that is the household's rule,
    not a glitch"). What is genuinely new is that it is one tap from a
    household that was previously never offered it. **Unverified end to
    end**: no API key in this sandbox, so the code path is certain and the
    model's tool choice is inferred from the instruction that tells it to
    make exactly that choice. Raised on the card; the underlying "replan
    over a running week without asking first" hazard is its own card.
  - **EACH CHIP SENDS ITS OWN LABEL, WORD FOR WORD.** A chip carrying a
    hidden sentence is one nobody can learn from, and teaching what you are
    allowed to say is the whole job here — so what it sends has to be what
    it says. It also leaves no second wording to drift out of step, which
    is what the old pair had (its "Approve this week" message lived in two
    functions).
  - **The old pair's grocery chip is gone, deliberately.** "Add … to the
    grocery list" PRE-FILLED the composer instead of running, which this
    card's own rule forbids, and it is not one of Emily's four. Grocery's
    placeholder (`ASK_HINTS.grocery`, "Add oat milk and lemons…") still
    teaches that sentence in the place it belongs. One line to put back.
  - **`loadQuickActionChips` still fetches `/api/week-menu`, and that is
    load-bearing for something else entirely.** The chips no longer need
    the plan, but `setDishIndex` does — it is what lets a reply naming a
    dish link to its recipe. Deleting the fetch along with the chip logic
    would have broken dish links in chat silently. Two things fall out: the
    chips now go up instantly instead of after a round trip, and a dropped
    request costs those links rather than degrading to a wrong suggestion
    the way the old code did.
  - **FOUR CHIPS COST 200px, AND THE COACHING EXAMPLES YIELD TO THEM ON
    DESKTOP — which is a fix, not a tidy-up.** The desktop Ask column is
    **347px** of usable width and the chips are 205/232/286/219 wide, so no
    two ever pair: four chips is always four rows. Stacked under the
    coaching examples' own 148px row that is **348px of chips in one
    column**, and it pushed the composer off the screen — measured on a
    seeded household at 1280x900, composer bottom **857 on main, 961 on
    this branch**, i.e. a regression this branch introduced and one that
    would have hit every NEW household, since the examples show for a
    tab's first three visits. So `renderAskExamples` stands down while the
    intents are up: they were a three-visit stand-in for exactly what the
    intents now say permanently, and `COACH_EXAMPLES` even carries "I'm
    short on time tonight", which is "Swap tonight for something quicker"
    in other words. Column back to 468 and composer back to 857, matching
    main exactly.
    **THE SCOPING IS THE WHOLE CORRECTNESS OF THAT RULE, and the first
    version got it wrong.** The phone's chips container is filled when the
    sheet is BUILT, not when it is opened — so a guard that read it at any
    width hid the DOCK's examples permanently, which is the coaching
    feature deleted rather than deferred. Caught in the browser, not in
    review: examples hidden with the sheet still closed. It is
    `today-ask-examples` only now, because the desktop column is the one
    place the two rows ever share a screen. Both directions are pinned by
    tests.
  - **Still true after that fix, and left alone:** at 1280x800 and 1280x720
    the composer needs a scroll. The column starts 406px down the Today
    grid, so its bottom is 874 whatever the window height is; main fit at
    720 by about two pixels. The alternatives, so nobody re-derives them:
    `max-height: calc(100vh - 160px)` on the column does not help (it
    cannot know about that 406px top offset), pairing the chips needs a
    font or padding below the 44px/§6 floor, and anything else is a re-cut
    of the Today desktop grid — a bigger claim than a chip row gets to
    make. The page scrolls and the input is reachable. Emily's lever if she
    minds is three intents instead of four.
  - **Also measured, also left:** at **360px** wide the 200px chip row
    squeezes `#ask-messages` to 79px against a 100px greeting, so the
    assistant's first sentence is clipped at first paint (it scrolls; 375px
    and 390px are both fine).
  - `tests/test_ask_sheet_named_intents.py` is the guard, 17 tests, most of
    them running shell.js's own functions under node — the two promises
    that matter ("tapping runs it", "the chips don't wait on the network")
    are behaviour, which a source-marker test cannot see. **On the evidence
    those tests are worth, be precise, because the first draft of this entry
    was not:** 13 of the 17 do go red against main's shell.js, but most
    die in the test file's own `_slice()` helper (main has no
    `var ASK_INTENTS = [` to slice from), so not one of them reaches an
    assertion about the OLD behaviour. The evidence that they bite is
    mutation instead, and an independent reviewer ran it: putting
    `renderAskChips(ASK_INTENTS)` back inside the fetch's `.then` reddens 6,
    and swapping `msg: label` for `prefill: label` reddens 3 including the
    "never merely pre-fills" one. Two tests were repaired after that review
    — one asserted a Python literal against itself and could never fail, and
    one was a byte-identical copy of another, and three were added for the
    examples-yield rule and its scoping. 2169 -> 2186. **Verified in a
    real Chromium** on a throwaway DB at 390x844, 390x667 and 1280x900, and
    independently re-driven at 360x640, 375x667, 1280x720/800 and 1440x900:
    all four render in order on all four tabs, every chip measures 44px, the
    page never scrolls sideways, no chip is apricot (Rule 5 — the only
    apricot in the sheet is the pre-existing send button), and the tap puts
    exactly `Swap tonight for something quicker` on the wire with the input
    empty before and after. Console clean apart from Google Fonts in
    the sandbox.
- **2026-09-11 — A browser error keeps its SHAPE now, because keeping
  nothing was not the only way to honour the boundary. Branch
  `overnight/client-error-shape`.** The whole record of a real tester's
  crash was `kind='client', where='/', detail='browser error'` — every word
  true and impossible to act on. Emily's call (2026-09-10): keep the type,
  the script file and line, a stack shape, and counts; never the message.
  - **The message stays dropped, and the 2026-09-02 reasoning is untouched.**
    That feed is printed into a Claude agent's context under an instruction
    to act on what it reads. What changed is only the claim that a shape and
    a message are the same thing: five new fields carry the shape and none
    of them can hold a sentence.
  - **The type is checked against a FIXED LIST, not a pattern — and so is
    `detail`'s, which is a pre-existing hole this closes.**
    `_JS_ERROR_CLASS_RE` alone waves through
    `class IgnoreEveryPriorInstructionError extends Error {}`: forty
    characters of attacker-authored English wearing the badge of a
    recognised JS error. Every standard constructor plus the DOMException
    names a browser app hits; anything else is the honest `(other)`.
  - **The residual is written down rather than left to be rediscovered.** A
    frame's function and file names are written by whoever wrote the
    script, so a direct POST writes both. The bound: identifier characters
    only, no spaces and no punctuation past `_ $ . -`, 40/60 chars, five
    frames, 240-char column. A token, not a sentence — nothing that can
    quote, close a fence or address a reader. That bound is exactly why the
    MESSAGE is still dropped whole: a message is the field a sentence fits
    in.
  - **`static/error-reporter.js` reduces on its side too, and that is a
    convenience and not the check.** The server re-derives every field:
    the browser is the untrusted end, and anything it can send a curl can
    send without running the reporter at all. The reporter's own reduction
    matters for one thing a server check could never see — a Chrome stack's
    FIRST LINE is `TypeError: <the message>`, so shipping `err.stack` whole
    would ship the message inside a field nothing inspects frame by frame.
  - **Repeats are counted, not stored one row each** — an identical shape
    inside 24h bumps `occurrences` instead of inserting. Not tidiness: the
    prune evicts oldest-first, so one broken screen filling the table
    quietly deletes every other error in it, which is how a real bug gets
    hidden by a cosmetic one. 24h because the report reads a day at a time;
    any longer and "20 in the last 1d" would be counting last week.
    `get_recent_errors` counts `SUM(occurrences)`, or the number a reader
    sees would have dropped on the day this landed and read as the app
    getting better.
  - **"Across how many households" is computed in
    `observability_report.py`, never in the app.** The app has no
    all-households view on purpose; the script is the one place that
    legitimately holds every household at once, and it is holding shapes,
    not data. It is the difference between one tester's device doing
    something odd and a bug shipped to everybody.
  - **Deliberately NOT done:** the tester's existing row is written off as
    unrecoverable (nothing backfills a stack trace — that would be
    inventing history, the same rule the 21-slot `reasoning` backfill was
    refused under); the two public share pages still report nothing, since
    on a public path there is no true answer to "whose error is this?"; and
    `static/shell.js` is untouched.
  - 18 tests in `tests/test_client_error_shape.py`; 2169 -> 2187. 17 of the
    18 fail on `5f638fc`; the eighteenth says in its own docstring that it
    is a guard rather than a catch (five rows and one row of five
    occurrences both count to five — the point is that the number did not
    move). Two of them run the reporter's own reduction under node against
    a browser stub, since "the message rode along inside the stack" is
    exactly what a source-marker test cannot see. **Verified against a real
    uvicorn on a throwaway DB** (port 8973): real and hostile payloads
    POSTed signed-in, the unauthenticated case refused 401, 40 repeats
    folding into one row, a pre-migration database opened by the new code
    and its legacy row still reading back, and the morning report printed
    from both the file and the live app.

- **2026-09-11 — The trouble line was keyed by POSITION, and two readable
  sentences had been swallowed. Branch `overnight/review-plus-and-counts`,
  third and final review round; everything else came back safe to merge.**
  - **`reviewState.troubleFor` was an index into a list every render
    rebuilds.** A chat turn tagged `tab: 'week'` reloads the week under
    this screen, and the sentence is only ever cleared by another tap — so
    a week that changed underneath moved the sentence onto whatever dish
    now sat at that position. The drop refusals NAME their dish out loud
    ("Bean Chili on Friday also feeds Saturday's lunch"), which makes this
    one more instance of the class this branch has now closed FOUR times:
    a thing labelled with one dish reporting about another. Keyed on
    **meal type plus the name the row reads as** now
    (`reviewTroubleIsFor`), which is unique within a group by construction
    since `reviewEatingGroups` keys dishes that way; and a sentence whose
    dish has left the week is DROPPED rather than moved to the foot of the
    body — it is about something no longer on screen, so its own row or
    nowhere are the only honest places for it. The `.rv-trouble-foot`
    fallback added one round earlier went with it.
  - **`SlotRefused` had been drawn one notch too tight.** "That day
    already has it." and "That dish is already on that day." are sentences
    written for a reader and were taking the 404 path, which the screen
    prints as the generic "that didn't work". Stale-screen-only, so the
    impact is small — but by that type's own stated rule they belong in
    it, and a rule that holds only for the cases somebody remembered is
    not a rule.
  - **Two things found and deliberately NOT fixed, written down so the
    next session doesn't rediscover them as new.** The sentence outlives
    its own tap: it survives a switch to "Which days", renders nowhere
    there, and comes back on the way in. And a refusal does not re-read
    the week, so a count that went stale underneath stays stale until the
    next write. Both are identical to the behaviour before any of this,
    neither risks data, and both are really one question — how long an
    answer should live on this screen — which is a decision rather than a
    bug fix.
  - **`drop_dish_from_day` IS NOT ATOMIC. Pre-existing on `main`
    (`99db198`), deliberately left, filed as its own card.** Force a
    `RuntimeError` in `plan_slot_open` after the DELETE and the dinner row
    is gone with **no `open` row replacing it** and the grocery line
    already reversed, while the route answers 500 so the screen says
    "nothing changed" — false, and a genuinely ABSENT slot, the exact
    state that function's own docstring says must never exist. It wants
    the connection-threading treatment `atomic-period-takeover` used (see
    its entry below), which is more than a review pass should smuggle in.
    Recorded here because this is where the code is.
  - 5 more tests (57 -> 60 there, and two rewritten); 2091 -> 2094. Every
    one of them red on `90bb207`. One existing file updated honestly with
    a note saying what moved (`test_review_two_views`'s preludes, for the
    helper `reviewDishRowHtml` now calls). **Verified in a real Chromium on
    port 8986**, driving a real chat-turn refresh with a refusal on screen.

- **2026-09-11 — Three more on the same stepper, and one of them destroyed
  data. Branch `overnight/review-plus-and-counts`, second review round.**
  The round before it confirmed the "+" blocker genuinely fixed — the chain
  built six ways through `repair_leftover_chains` on approved weeks, the
  planted dish matching the row's label every time — and then found these.
  - **THE "−" IN THE SAME ROW STILL DELETED A COOKED RECORD.** The "+" grew
    a `cooked_status` check and its sibling had none, which is the sharper
    version of the lesson this branch keeps re-learning: a rule fixed on
    one half of a control is not fixed. Reachable whenever a dish covers
    two nights and the LATER one has been ticked, because "−" always takes
    the last day the dish covers — the tick gone, the inventory left
    depleted for a meal now off the plan, an eaten meal's ingredients back
    off the shopping list. `drop_dish_from_day` refuses it now in the same
    `refused` shape it already used for a chain source, and the control
    goes inert. **Deliberately NOT "take the last UNCOOKED day instead"**:
    that quietly takes a different day from the one the count implies,
    which is this screen's own recurring bug wearing a different hat.
    **And CLAUDE.md named "Change one" as the remaining hole of this kind
    and did not name this one** — corrected below; "Change one" really is
    pre-existing, and this was built by this branch's predecessor.
  - **The toast named the displaced night by its stored text.** `dish` was
    resolved through the chain by the round before and `replaced` was not,
    so the picker offered "Saturday · Bean Chili" and the toast reported
    "in place of Leftover chili". Nothing was written wrongly and the right
    night was displaced — but it is the same "labelled with one dish,
    reported as another" defect, left half-closed by the fix for it. One
    lookup, the one `dish` already makes. `drop_dish_from_day`'s
    `open_reason` had it too ("You cut Leftover chili back") and is fixed
    with it, since that sentence is what the Day step shows in place of a
    meal.
  - **The newly-surfaced refusal landed below the fold.** `.rv-trouble`
    rendered once at the foot of the whole body — measured at 390px on a
    seven-day week, **2114px down an 844px viewport**, with no toast, no
    scroll and the strip still open. So a refused tap moved nothing, said
    nothing where the finger was, and explained itself a page and a half
    away, which reads as a control that does nothing at all. It renders
    inside the dish row that was tapped now (`troubleFor`), with the foot
    kept as the fallback for a sentence whose row is gone — dropping it
    there would trade one invisible message for none.
  - **Only sentences written for a PERSON are shown.** Surfacing `detail`
    was right and was too broad: it printed `"No slot 999 on that week's
    plan."`, and a 500's `"Server error: <python exception>"`, straight
    into the household's week. New `weekly_plan.SlotRefused(ValueError)`
    marks the two that are written for a reader; the route answers those
    as `{status: 'refused', message}` at 200 — **the shape
    `drop_dish_from_day` already used**, so the screen now has ONE branch
    for both halves of the stepper — and everything else stays a 404 and
    takes the plain line. An app that did exactly the right thing must not
    report itself broken.
  - **The docstring's re-buying claim was unconditional and the behaviour
    is not.** `_reingest_unlinked_entries` is a no-op for a freeform entry
    by design and buys nothing on a draft, so a freed night with no
    `recipe_id` gets nothing — the exact state the "−" refuses in the same
    breath. Written down properly at the function, since that is what a
    future session will act on; the toast already said only the narrower
    thing it could stand behind.
  - Two smaller ones taken: the new refusals use curly apostrophes like
    every other sentence beside them, and
    `test_a_refusal_with_no_sentence_still_says_something_plain` says in
    its own docstring that it is a guard rather than a catch — its redness
    against an earlier commit was a harness artefact, and the round before
    counted it as coverage it is not.
  - 6 more tests (51 -> 57 there); 2085 -> 2091. All six fail on
    `33f4b5d`. **Re-verified in a real Chromium on port 8984** at 390px in
    both schemes, on a throwaway DB, with the served build confirmed
    in-page first.

- **2026-09-10 — A blocker and three concerns in the "+", found on review
  of the entry below and fixed on the same branch
  (`overnight/review-plus-and-counts`).** All reproduced through the app's
  own route in a real Chromium. Changes 2 and 3 held under everything the
  reviewer threw at them and are untouched here.
  - **THE BLOCKER: the "+" wrote a DIFFERENT dish from the one the row
    names, and bought nothing for it.** A Review row is labelled by
    `mealDisplayName`, which for a confirmed reheat answers with
    `leftover_from.meal` — the dish being reheated — while
    `add_dish_day` re-derived the name from the row itself as
    `COALESCE(recipes.name, freeform_meal)`. Those two disagree for any
    chain whose reheat lands in a different meal-type GROUP from its cook,
    which is an ordinary dinner-cooked-double-for-tomorrow's-lunch:
    `repair_leftover_chains` accepts it and the generation prompt asks for
    that night to be written as freeform text naming what it eats. So a row
    reading **Beef Bulgogi · nothing to cook · 1 lunch** planted *Leftover
    bulgogi bowls* — a night rendering as a reheat with no batch behind it,
    nothing bought for it, and the dish the household agreed to lose gone.
    On an approved week the displaced dish came off the list and nothing
    went on. **The same class as the two blockers already fixed on this
    screen this week: a control labelled with one dish acting on another.**
    The name is resolved through `plan_leftover_chains` now — the one
    reader of that pairing, and the source of the very field
    `mealDisplayName` reads — so the write plans the dish the row names,
    with the COOK night's food groups rather than the reheat's (a reheat
    row records none). Refusing a reheat as the source was the other option
    and was not taken: a group whose only entry is the reheat would then
    have a permanently dead "+".
  - **The "−" refused to break a chain and the "+" broke one in silence.**
    `drop_dish_from_day` answers "Beef Bulgogi on Thursday also feeds
    Friday's dinner — change that first"; adding onto that identical day
    went straight through, Friday quietly became an ordinary cook, and the
    toast said nothing. **The data was right** — down DELETES and strands
    the fed night, up REPLACES and `swap_meal_in_plan` re-buys for it — so
    the fix is to SAY it, not to refuse it: one screen must not refuse the
    mirror of what it silently allows. `add_dish_day` returns `unchained`
    (read before the swap, since afterwards there is nothing left to ask)
    and the toast adds a clause: "Saturday was eating off it, so that night
    is on its own now." **Not "a cook of its own"** — that was the first
    wording, and the browser caught it contradicting the row printed right
    underneath: a freed night keeps whatever it was called, and one the
    planner had written as "Leftover bulgogi" still reads "nothing to
    cook". The chain being broken is what is certainly true, so that is
    what it says.
  - **An already-cooked meal was offered, and taking it over destroyed the
    record.** The control excluded `isPast` only and the write had no
    status check, so today's dinner ticked off at seven was a live
    candidate — the `cooked_status` gone, the inventory depleted for a meal
    now off the plan, and the ingredients for a meal somebody ate taken off
    the shopping list. `get_week_menu` carries `cooked` per slot now
    (additive), the picker skips it, and the write refuses it. **"Change
    one" has the same hole and is pre-existing — deliberately NOT widened
    into here; this only declines to add a second door to it.** ~~And that
    is the only one left.~~ **It was not: the "−" in the same stepper had
    the identical hole, and unlike "Change one" it is NOT pre-existing —
    this branch's own predecessor built it. Closed in the entry above.**
  - **The write's refusals are shown in the server's own words.** Every
    `ValueError` here becomes a 404 and the screen was printing the generic
    "That didn't work just now" over it — reporting a breakage where the
    app had done the right thing. Two of these are sentences a household
    needs to read (a day nobody is home, a meal already cooked), so
    `runAddDishDay` surfaces `detail` and falls back to the plain sentence
    only when there is none. **Too broad, and corrected in the entry above:
    that printed EVERY `ValueError` verbatim, row ids and "Server error:
    <python exception>" included. Only sentences written for a person are
    surfaced now.**
  - **And the answer to Emily's generation question was wrong in one of its
    three legs**, which matters because she has already been given it. Two
    legs check out. The third — "`slot_needs`' away-reopen only validates
    the three real meals" — is FALSE: `_validate_slot` defaults to
    `allow_snack=True` and both `_ALL_SLOTS` tuples include `snack`, so
    marking somebody away for a snack and then back hands the slot back as
    an open question with no assistant involved at all. The HEADLINE
    survives (generation's own finishing passes never make one) and **the
    widening is more right rather than less** — an away-reopen's open snack
    is a real decision handed back. Both legs are separate tests now, each
    saying which half it covers. **A fourth path, added in the entry above
    for completeness: `drop_dish_from_day` — the "−" on this very screen —
    opens whatever slot it empties, snacks included.**
  - 13 more tests (38 -> 51 there; 2072 -> 2085 total). **12 of the 13 are
    red on `b7f5f09`**, four of them because the harness needs
    `addDishToastText`, which is itself the change; the thirteenth is the
    attendance characterisation above and is green on both sides by design.
    **Re-verified in a real Chromium on port 8982** at 390px light and
    dark: the reheat row now adds the dish it names, a cooked day is not
    offered, the freed-night clause reads as one sentence, and the refusal
    shows the server's words.

- **2026-09-10 — "+" asks which day, the Approve button counts all four
  meal types, and a cook's serving count is saved with the ticks. Branch
  `overnight/review-plus-and-counts`.** Three of Emily's decisions in one
  pass, each closing something the two branches before it left open in
  their own words. **Four things it got wrong are corrected in the entry
  above — read that first.**
  - **THE STEPPER'S "+" SHOWS WHAT IT WOULD DISPLACE.** The entry below
    says up "was not built" because it needs a day to land on and every
    candidate is either holding another dish or deliberately empty, so a
    placement rule would be one nobody has decided. Emily's answer is that
    nobody should decide it: **every candidate day already holds
    something, so "+" always means REPLACING, and a rule that picks the
    victim silently is exactly the failure this app keeps getting caught
    by.** So the tap opens a strip under the row — one row per candidate,
    each saying the day and the dish currently on it, an open slot reading
    "Your call" — and the household taps the one they are willing to
    spend. A toast names the day and what it cost; answering an open slot
    says only what it now is, because nothing was lost.
    **`cookAheadHtml` was the thing to reuse and genuinely could not be**,
    which is written into `.rv-pick`'s own CSS rather than left as a
    judgement in a commit message: its chips are a three-letter weekday
    and nothing else (there is no room in one for a dish name at 390px), it
    is a multi-select waiting on a confirm rather than a single choice, and
    the days it offers are days already holding the SAME dish. Same family,
    opposite question. What IS reused is the write: `tools.add_dish_day`
    composes `swap_meal_in_plan`, so the grocery reversal, the chain
    unlink, the re-buy for nights that were eating off the displaced dish
    and the taste verdict are all that function's, not a second copy that
    can disagree with it about one household's shopping list.
    **The one backend addition is `old_entry_id` on `swap_meal_in_plan`**,
    beside the `old_meal` it already had and for the case a name cannot
    express: an OPEN slot has no meal name, so a by-name replacement would
    pass None and take every row in the slot with it — the two-snacks bug
    the entry below had to fix, reached from the other side.
    `planned_empty` is refused at the write and not only left off the
    strip, because a control is not where that rule belongs. Past days and
    days outside the plan's period are not offered either. ~~And days
    already eaten.~~ **Only PAST days, which is not the same thing —
    today's dinner ticked off at seven is neither past nor free. Corrected
    in the review-pass entry above.**
  - **THE APPROVE BUTTON COUNTS ALL FOUR MEAL TYPES.** The entry below
    flagged this and left it as Emily's call; the call is to widen.
    `countOpenSlots`/`approveWithOpenLabel` now ask `daySlotKeys` what a
    day is actually made of instead of asking `WEEK_SLOTS`, which is a
    different question — reproduced in Chromium before the change: "Which
    days" said `Snack 2 · Your call` on Saturday while the button read
    "Approve and build my shopping list", and the Week root said "Approve
    this week". Both now say "Approve — leave Saturday open".
    **`WEEK_SLOTS` is untouched and there is a test saying so**: it stays
    the three real meals, so a snack is never a cook and never counts
    against the 21-slot guarantee. `reviewDaySlotKeys` moved up beside
    `daySlotEntry` and lost its prefix; same function, one caller more.
    **The question Emily asked, answered: week GENERATION's own finishing
    passes never hand back an open snack.** The two that could are scoped
    to the three meals — `audit_plan_slots` (the generation-gap pass)
    filters to `WEEK_SLOTS`, and `repair_leftover_chains` skips any row
    outside it. ~~And `slot_needs`' away-reopen only validates those
    three.~~ **THAT THIRD LEG IS FALSE and Emily was given the wrong
    version of this answer: `slot_needs._validate_slot` defaults to
    `allow_snack=True`, so an open snack falls out of an ordinary
    attendance edit (away, then back) with no assistant anywhere near it.
    Corrected and pinned in the review-pass entry above; the headline holds
    and the widening is more right rather than less, since an away-reopen's
    open snack is a real question handed back.** The other path is the
    model itself: `submit_weekly_plan`'s schema takes `slot: 'snack'` with
    `slot_state: 'open'` and `_generate_weekly_plan` writes it through
    unchanged. Pinned as tests rather than left as a claim; no API key in
    this sandbox, so it is the code paths driven for real, not a generated
    week.
  - **THE COOK'S SERVING COUNT LIVES IN THE TICK RECORD.** It was held for
    the page's life only, so a reload left **a half-ticked ingredient list
    at amounts nobody chose** — reproduced in Chromium on the pre-change
    build: Serves 4, "8 Tortillas" ticked, reload, and the row reads
    "4 Tortillas" and is still ticked. Not an inconsistency; a screen that
    is wrong. The two describe one cooking session, so they end at the same
    moment: same key (`pomona.cookTicks.p<plan>`), same record, same expiry
    — a new week is a new plan id and both go together. Still per device
    and still not sent to the server: a cook overriding tonight at the
    counter is not a change to who lives in the house, and surviving a
    reload does not make it one. Two things that fall out and are written
    down where they matter: the stored amounts are ABSOLUTE (the list
    `/api/recipes/scale` handed back), so re-applying REPLACES the server's
    numbers rather than multiplying them and cannot compose twice with the
    batch/attendance scaling `get_cooker_view` already did; and
    `cookReadTicks` hydrates once per plan and is called BEFORE the write
    in `cookStepServings`, because a read on the far side of that
    assignment would put the stored number back over the tap. A record
    whose entry has no ingredient list behind it is dropped rather than
    rendered.
  - `tests/test_review_plus_and_counts.py` is the new guard (38) and
    `tests/test_cook_journey.py` grew 8 (55 -> 63); 2026 -> 2072. **34 of
    the 38 and 5 of the 8 fail on `fc4bc76`** (34, not the 33 first written
    here — the file cannot be collected against that commit's shell.js at
    all, so the count was taken from a run with the pure helper move
    applied and one test was missed); the rest are no-regression
    guards or the generation characterisation above, and each says so in
    its own docstring. Two existing files were updated honestly rather than
    deleted, each with a note saying what moved (`test_review_two_views`'s
    prelude for the renamed helper, `test_cook_journey`'s function list for
    `cookReadServes`). **Verified in a real Chromium** at 390px light and
    dark and at 1280px, on a throwaway DB: the strip is 44px rows that wrap
    rather than overflow on a long dish name, the page never scrolls
    sideways, the screen's only apricot is still Approve, and the pick,
    the toast, the count and the reload all do what is written above.
    Contrast measured off computed styles — the ask 11.60:1 light /
    11.28:1 dark, the day 12.59 / 14.40, the dish 12.78 / 14.40, "Your
    call" `--ink-secondary` at 4.44 / 8.48 (the app-wide value, 0.06 under
    AA in light and unchanged here). Console clean apart from Google Fonts
    being unreachable in the sandbox.
  - **Two of the same snack on one day is allowed — Emily, asked and
    answered, 2026-09-10: leave it.** The strip offers a day that already
    holds this dish in ANOTHER of its snack slots, so asking twice
    deliberately gives you two. Days the dish already covers are excluded,
    which is the ordinary case; this is only reachable on purpose, it is
    one tap to undo, and an app refusing a deliberate choice is the harder
    thing to explain.

- **2026-09-10 — Two blockers and two concerns in the Review stepper, found
  by an independent reviewer and fixed on the same branch
  (`overnight/review-week-two-views`).** Both blockers were reproduced
  through the real route and the real UI, and both had a fix pattern
  already written down in this codebase.
  - **The stepper on a snack deleted the day's OTHER snack.**
    `drop_dish_from_day` composed `clear_plan_slot`, which deletes EVERY row
    at `(plan, date, slot)` — and a day holds TWO rows at `slot='snack'` by
    default (`preferences.resolve_snacks_per_day`). One tap on the Apple row
    destroyed the Greek yogurt beside it, grocery reversal and all, and left
    a day the household had asked two snacks of holding one open slot.
    `swap_meal_in_plan`'s own docstring had already written the rule down
    ("a slot holding two snacks would lose both to a swap that was only ever
    about one of them") and that is exactly why it takes `old_meal` and works
    by id; this re-opened the hole and the UI exposed it. Removal is BY ID
    now, with the same two lines of care in the same order
    (`_unlink_leftover_target`, then `_reverse_meal_grocery_contributions`).
    **The lesson is the narrow one: `clear_plan_slot` is a SLOT operation,
    and a slot is not a meal.** Anything acting on one entry must say which.
  - **The Approve button stopped telling the truth the moment the stepper
    was used.** `runDropDishDay` spliced the changed day into
    `weekState.days` and stopped; `countOpenSlots`, which the button's label
    is built from, reads `weekState.data.days`, which a splice never
    touches. So a week that had just been handed an open slot back went on
    saying "Approve and build my shopping list". `runSwapInPlace` has had
    `await loadWeekMenu(panel)` all along and its comment says why. One
    line, now with a test that RUNS the function against stubs and asserts
    the call happened — the defect was a missing call, which is precisely
    what a rendering test cannot see. A `showToast` naming the day rides
    with it, because the tap leaves work behind and Review renders an open
    slot as the bare words "Your call".
    **It counts breakfast, lunch and dinner, and NOT snacks — so "the button
    keeps counting" is true of three slots out of four.** `countOpenSlots`
    and `approveWithOpenLabel` iterate `WEEK_SLOTS`, which is deliberately
    the three real meals (see the `meals-renders-snacks` entry: a snack must
    never be counted as a cook or an open slot). That scope predates this
    branch and is not changed here — but this branch adds the SNACKS
    stepper, which makes an open snack one tap away, so the gap is newly
    reachable: drop a day off a snack and "Which days" says `SNACK 2 · Your
    call` while the button still says "Approve and build my shopping list".
    The screen knows and the button does not. Widening the count is Emily's
    call, not a change to smuggle in under a bug fix, because `WEEK_SLOTS`
    is load-bearing in four other readers. **She made it the next day —
    widen — and the count is all four now; see the entry above. This
    paragraph describes the branch, not `main`.** `WEEK_SLOTS` itself was
    left exactly as this entry insists it should be.
  - **A chain SOURCE is refused rather than dropped.**
    `_unlink_leftover_target` covers the target side only, so taking away a
    night that was cooked double left the night it fed holding a real recipe
    nobody planned to cook, with the doubled batch's groceries just reversed
    out from under it. The behaviour is pre-existing and shared with every
    chat swap; what was new was a control that would hit it by arithmetic
    rather than by a decision. `drop_dish_from_day` now answers `refused`
    with a sentence naming the night that depends on it, and writes nothing.
  - Two smaller things: `.rv-step-count.is-busy` faded to `--ink-secondary`
    on the `--sand` track (**4.04:1**) — a transient state is not a disabled
    one, so WCAG 1.4.3's exemption does not cover it; the fade is gone and
    both stepper buttons going inert is the whole busy signal. And `Change`
    reads **"Change one"** on a dish covering more than one day, because
    that is what it does.

- **2026-09-10 — Reviewing a week is two views of one week, not more of it
  on one screen. Branch `overnight/review-week-two-views`, stacked on
  `overnight/tap-a-meal-opens-recipe`.** Emily's approved design,
  2026-09-09, Option A. Julia's report was that a week is too much to take
  in — three meals and two snacks across seven days is 35 things — so
  "Check the week" is a FOURTH step of the Meals tab (week -> review ->
  day -> meal, all at `/week`, back link up a level by name) carrying one
  segmented control: **What we're eating**, grouped by meal type with each
  dish once and the days it covers, and **Which days**, one card per day
  showing DINNER and expanding to all five. Badge "NOT APPROVED YET", one
  apricot, "Approve and build my shopping list". Reachable from a draft on
  the page (`#week-check-btn`, a quiet secondary above Approve) and from
  the More sheet once a week is approved — it is the Review step for every
  week, not a first-run screen.
  **Two rules do the counting, and neither is a name match.** A dish is
  identified by the name it READS as (`mealDisplayName`), which for a
  made-ahead night is the source dish rather than the whole "Made ahead —
  Sunday's Egg White Bites" sentence, so a leftover chain collapses into
  one row over two days without `reviewEatingGroups` knowing anything about
  chains; and a COOK is `weekly_plan._is_cook`'s own test (planned, not
  leftovers or takeout), so this screen and the approved-week receipt can
  never put different numbers on the same week. Only `planned` slots are
  dishes — an `open` slot is a question and a `planned_empty` one is a
  night nobody is home.
  **The stepper goes DOWN and not up, deliberately.** Down is arithmetic:
  `tools.drop_dish_from_day` composes `clear_plan_slot` (which reverses the
  grocery contribution and unlinks any chain pointing at the row — its job,
  not this one's) with `plan_slot_open`, so the day comes back as a
  QUESTION and never as an absent slot. `open`, not `planned_empty`:
  planned_empty means nobody is home or the household asked for none of
  that meal and must never be offered as a decision, and cutting one dish
  back is neither. It takes the LAST day the dish covers. (An earlier
  version of this entry went on to claim that day "is always the reheat
  rather than the cook that feeds it". **That is false** and is corrected
  in the entry above — it holds only within one meal type; a dinner cooked
  double for the next day's LUNCH is a source sitting in the Dinners group
  and can be last in it.) **Up was not
  built**: it needs a day to land on, and every candidate is either holding
  another dish or deliberately empty, so a placement rule would be one
  nobody has decided. `+` opens the ask sheet ("Another night of X — ")
  instead of guessing. **Built the next day, and this reasoning decided its
  shape rather than being overturned by it: there is still no placement
  rule — the strip shows what each day is holding and the household picks
  which one to spend. `+` no longer opens the ask sheet; see the
  2026-09-10 entry at the top.** **`Change` opens that dish's Meal step**, where
  Swap-in-place and "Tell me what instead" already live; changing every
  covered day in one tap is part 2's job and is not half-built here.
  **Three things the browser found that reading the code did not.** (1) The
  away rule read EVERY slot including snacks — but both places that mark a
  day away (`agent._finish_week_slots`'s `out`-night pass and its
  slot_needs pass) write breakfast/lunch/dinner only, so on a real week it
  would essentially never have fired; it reads the three meals now, and a
  closed day that still carries snacks stays expandable rather than hiding
  real rows behind "nobody's home". (2) A chain's second night showed the
  same dish name as its first with nothing saying why, which on a review
  screen reads as a planning mistake — the face now carries "made ahead" /
  "leftovers", read off the entry's chain. (3) The segmented control's
  buttons were 40px inside a 48px row; Rule 6 is about the thing you tap.
  **Contrast, measured in Chromium at 390px both schemes:** nothing muted
  clears 4.5:1 on `--sand` (`--ink-secondary` 4.04, `--ink-muted` 3.93) or
  on `--ground` (4.44 / 4.33), so the segmented labels and the group eyebrow
  take `--ink-strong` and the selection is carried by the raised pill and a
  weight step rather than a colour step; today is an inset celadon edge
  rather than a `--celadon-tint` fill, which would have put four labels onto
  a ground where three of them fail. Every new value is recorded in
  `shell.css`. `tests/test_review_two_views.py` is the guard, 35 tests, the
  front-end half running shell.js's own functions under node rather than
  reading the source for markers — the risk in a screen whose job is
  counting is the count, which no marker test can see. One existing
  assertion in `tests/test_flows_3_review_and_receipt.py` moved from a
  file-wide apricot count to a per-function one, honestly and with a note:
  the Review step renders its own `#week-approve-btn` into the same
  `.wk-decide` shell so `approveWeek`/`showApproveConfirm` need no second
  implementation, and only one step is ever in the DOM. Suite 1792 -> 1827.
- **2026-09-10 — Six more on the cook journey, and two of them are
  corrections to what the last two entries claimed. Branch
  `overnight/cook-journey-step-by-step`, second review round.**
  **The tests for the servings blocker did not guard it.** The two headline
  ones hand-mutated `meal.ingredients` and then rendered — and the OLD
  renderers read from the meal too, so both passed on the broken commit;
  the only real guard was `assert "innerHTML" not in fn`, which a rewrite
  poking `el.textContent` would have sailed through. They are renamed and
  say what they actually cover now, and
  `test_a_tap_on_the_stepper_is_carried_by_every_stage` drives
  `cookStepServings` itself against a stubbed `/scale` and then walks the
  stages — tap, stored state, re-render, which is the path that broke. It
  fails on `da80b01` and passes on `1aedac0`, which is exactly the shape a
  guard on that fix should have.
  **Three fast taps gave one increment.** `current` was read off
  `meal.default_servings`, which only moves when a reply lands, so every
  tap in the same second counted from the same number and fired the same
  request; a fast +/- left the winner to whichever reply arrived last. The
  count is advanced on the meal AT TAP TIME (`serves_target`) and each
  request carries a sequence token, so a superseded reply is dropped rather
  than racing. Measured in Chromium: 2 -> 5 on three taps in one frame,
  scale calls 3/4/5, and a +/- pair calls 6 then 5 and lands on 5.
  **An ordinary tab switch threw the rescale away.** Meals then Kitchen
  runs `loadKitchen` through `refreshKitchenPanel`, and the screen came back
  at the household's own number while the cook was holding the pan — one
  tap, no notice. The last entry called that "a later load", which
  undersells it; it is now carried for the page's life in
  `cookState.serves`, keyed by the DISH, and re-applied by
  `cookApplyServesOverride` on every render, so a load, a write response
  and a tab switch all leave it standing. Not sent to the server and not
  outliving the page: this is a cook overriding tonight at the counter, not
  a change to who is eating.
  **A rescaled batch contradicted itself on one screen.** `+1` on a
  cook-ahead source gave `Serves 7` under a chip still reading `for 6` and
  a note still reading "Cooking for 6 — enough for Thursday, Friday, and
  Saturday". The chip IS the number being cooked, so it follows the cook
  (`meal.servings` moves with the override). The note is the server's
  sentence and it names a count, so it goes — and the NIGHTS are said again
  from `meal.covers`, the dates themselves rather than a re-worded
  sentence: "This batch is also meant for Friday and Saturday", plus
  "— check it still stretches" only when the cook has gone BELOW what the
  batch was sized for, which is the only direction that can leave a night
  short.
  **The dock's foot was switched off on desktop.** `@media (min-width:
  1100px)` set `.cook-body { padding: 16px 28px 0 }` — the padding
  shorthand this repo's own gutter rule warns about — so `--cook-dock-h`
  was still 123px, the dock was still sticky, and the bottom padding was 0
  at exactly the width nobody had measured. `padding-inline`/`-block` now,
  and the test asserts the invariant across EVERY `.cook-body` rule in the
  file rather than reading the base one, since reading the base one is what
  let this through.
  **And the dock claim itself was wrong** — see the correction in the entry
  above. Nothing was unreachable on either build; the foot is comfort and a
  guaranteed gap, and the desktop shorthand is the bug that was real.
  Two nits taken: the oven line no longer prints a unit the step did not
  write ("Preheat oven to 200" is "Oven at 200°", not an inferred °C — this
  is the section whose whole rule is that it never says a thing nobody
  wrote), and `cookKitMentions` also reads "do not use a grill" / "no need
  for a blender" / "instead of" as refusals. `tests/test_cook_journey.py`
  grew 7 (48 -> 55); suite 1840 -> 1847. All seven fail on `1aedac0` bar
  the tap-across-stages one, which fails on `da80b01`, where its bug lived.
  Verified in Chromium on a throwaway DB at 390x844, 390x780 with the
  coaching row up, and 1024/1280/1440 wide: the foot is 131px at every
  desktop width, a batch scaled up and down says one number everywhere, and
  a tab switch leaves the cook's count alone.

- **2026-09-10 — Five things the cook-journey slice got wrong, on the same
  branch (`overnight/cook-journey-step-by-step`). Found on independent
  review, all reproduced in a real Chromium, fixed in one pass.**
  (1) **The serving stepper's rescale was thrown away one tap later.**
  `cookStepServings` wrote only to the DOM — `#cook-ings-N` and
  `#cook-getout-N` — and left `cookState.data` alone. Survivable while the
  stepper lived on one screen nothing re-rendered; fatal the moment cooking
  became three stages, because every stage change calls `renderCook()`,
  which rebuilds from that state. A cook who set Serves 4 on Before you
  start was told "1 Carrots" mid-recipe and handed Serves 2 back on the way
  home, silently. **The slice moved that stepper and gave the move a
  reason** ("get the amounts right before the cupboard is open") and then
  halved the amounts two screens later, which makes this the branch's own
  bug and not an inherited one. It writes `meal.ingredients`,
  `meal.default_servings` and `meal.unscaled_items` and re-renders now; the
  count in the stepper still moves on the tap (the refresh policy's "the
  common case never waits") and goes back if the scale call fails. Two
  things fall out for free: the "N of M out" note follows a rescale,
  because the renderer counts it, and the "eyeball these" line is rendered
  off the meal (`cookUnscaledHtml`) rather than poked into a hidden `<p>`,
  so both stages say it. Ticks survive because they are filed under the
  ingredient's NAME. Deliberate: a later load refetches and the household's
  own servings win again — the server is the truth about how many people
  are eating.
  (2) **The dock's foot. NOT the blocker this bullet first called it —
  corrected 2026-09-10 in the same round that measured it properly.** What
  was written here was "two of three ingredients and the whole Pans and kit
  section were behind it at first paint", and that is ordinary sticky-footer
  behaviour: a sticky `bottom:0` last child comes to rest at the end of the
  scroll, so nothing was unreachable, and the re-check measured
  occluded-at-max-scroll as 0 on the build with the fix and on the build
  without it. `wireCookDock` + `--cook-dock-h` are a real improvement —
  ~131px of runway, so the last rows clear the bar earlier on the way down,
  and a guaranteed gap instead of a row ending flush against it — but they
  are comfort, not a rescue. **The bug in this area that IS real was found
  in the next round and is in the entry above: a `padding` shorthand in the
  1100px block zeroed the bottom, so the foot was switched off on desktop
  entirely.** Kept rather than deleted because a "blocker we fixed" carried
  forward is how a slice's history stops being true. **Also correcting this
  file:** the entry below says "verified in a real Chromium at 390px", and
  the journey was — every stage, both schemes, a real reload. What was not
  checked was whether the dock covered anything, at any height. Verifying a
  flow is not verifying a layout.
  (3) **The oven line invented temperatures.** It asked for "oven" plus a
  heating word plus any three-digit number anywhere in the step, and "set
  aside" satisfies the heating word — so a meat-probe target ("roast until
  a probe reads 145°F"), a braise time ("braise for 180 minutes") and a
  resting time all came back as oven temperatures, printed FIRST in the
  list with no hedge, failing silently. It also could not see a real 90C.
  The number must now follow "oven to" (or "oven at"/"oven up to")
  directly: every one of those fails it, because in each the number belongs
  to something else. "Gas mark 6" produces nothing, and a quiet miss is the
  right failure for a section whose stated rule is that it never guesses.
  Same pass: "no skillet needed — use the baking sheet you already have"
  no longer asks for a skillet (`cookKitMentions` reads clause by clause
  and discounts a negated mention, the shape `_COMPOUND_EXCEPTIONS`
  already uses). The dead second regex alternative went with the rewrite.
  (4) **The empty-recipe fallback promised a control that isn't there.** It
  sent every recipeless meal to the whole method "to write one" — true for
  a SAVED recipe with no steps, false for a freeform meal, where
  `cookDetailHtml` returns early and there is no fill button at all. Two
  sentences now, one per absence, each naming the way out that screen
  really has.
  (5) **A load under a focused cook could hand you a stranger's recipe.**
  `focusIdx` is an index and every load rebuilds `meals`; `loadKitchen`
  re-pinned `tonightIdx` and reset neither the index nor the stage, so a
  chat turn tagged `tab:'kitchen'` arriving mid-cook could render "Step 3
  of 4" of whatever dish now sat there — and, with fewer steps, the "Mark
  it cooked" finish of a dish nobody started. `cookState.focusMealKey`
  records WHICH dish the focus is on and `cookFollowFocusedMeal` follows it
  by identity, falling back to the root when it is genuinely gone; the step
  cursor is clamped where the stage is decided, so the dock and the
  instruction can never answer about different steps.
  Two review nits taken rather than noted. `.cook-sectionnote` was going to
  ship at 4.44:1 for 11px/700 with a comment explaining why — but a
  knowingly sub-AA value on a NEW screen is a decision, not a note, so the
  count takes body ink (12.78:1 light / 14.4:1 dark); the eyebrow beside it
  is a label and stays muted. And the whole method's finish had become the
  quietest control on its screen, so `.cook-focus-end-done` is that stage's
  one apricot primary, full width — there is still exactly one finish
  control on it and still nothing in its dock but the way back to your
  place. `tests/test_cook_journey.py` grew 10 tests (38 -> 48), including
  the servings one asserting ACROSS a stage change, which is precisely what
  a single-screen render test cannot see; suite 1830 -> 1840. Re-verified
  in Chromium at the reviewer's own 390x780 with the coaching row up: every
  ticklist row and the kit section clear of the dock at first paint and at
  full scroll, and Serves 4 carried intact through step, method and back.

- **2026-09-10 — Cooking is three stages: before you start, one step at a
  time, and the whole method one tap away. Branch
  `overnight/cook-journey-step-by-step`, FIRST SLICE ONLY.** Emily's
  approved design, 2026-09-09; she chose one-step-at-a-time as the default,
  with the whole method one tap away. Cook mode was one long screen — hero,
  prep, the whole recipe, scroll — and is now `cookState.focusStage`, three
  stages of the SAME step of Kitchen (never routes; the "‹ Kitchen" link
  still goes up one level by name, and moving between stages never touches
  history). Every entry point already came through `cookEnterFocus`, so the
  "Cook this opens Before you start" rule is one line there rather than five
  at the call sites, and `cookResolveFocusIndex` is untouched.
  **Deliberately left for the second slice, and NOT built here: the running
  timer a step can offer, and the Done/arrival moment (the rating writing to
  the taste record).** Finishing already worked and still does — every stage
  that has a finish uses the existing `focus-check` write and the existing
  words, "Mark it cooked".
  Four things worth knowing before changing it.
  (1) **The whole method is `cookDetailHtml` whole and unchanged** — the
  panel this screen always rendered, every step tickable, "why this",
  fill-in-a-recipe — so the two cooking stages cannot drift about what the
  recipe says. Same renderer Meals' Meal step reads `plain`, and `plain`
  now computes no meal key and reads no ticks at all, so the read-only frame
  stayed read-only (verified in a browser: zero `data-cook` controls in it).
  (2) **Ticked steps and ingredients are localStorage keyed by
  `weekly_plan_id`, and that key is the design.** Prep ticks were already
  the server's (`prep_tasks.status`); these two have no column anywhere, and
  the acceptance criterion is that they survive leaving the screen — which
  has to include a reload, since an installed PWA discards its web view the
  moment the phone goes down mid-cook. Plan ids are globally unique, so two
  households on one device can never read each other's ticks without this
  code knowing anything about households, and a new week is a new id, so
  last week's ticks expire by construction (writing prunes every other
  plan's key). They are keyed by the MEAL's identity — entry_id, or the
  dish's name for a component week — never by its index into
  `cookState.data.meals`, which is the array every load and every write
  response rebuilds. **Open for Emily:** per-device is the deliberate call
  (two people cooking two dishes on two phones must not tick each other's
  steps); a cook that follows you from phone to tablet mid-recipe is a real
  column and a write per tap, worth asking for rather than assuming.
  (3) **Two derived things read the recipe's own words and invent nothing.**
  `cookKitFor` scans the STEPS for cookware (`COOK_KIT_WORDS`, whole-word,
  so "grilled halloumi" is not a grill) plus the oven preheat, because
  nothing in this app records equipment — no column, nothing the generator
  is asked for — and an empty answer is a missing section, never an empty
  one. `cookStepNeeds` names the ingredients a step mentions. Both are
  keyword lists on purpose, the call `plates.is_low_carb` already made: they
  run per render, a wrong answer costs one extra chip, and a list anyone can
  correct beats a judgment nobody can see.
  (4) **The dock is sticky, inside the page's own scroller.** Rule 5 is
  untouched: Kitchen's ROOT still has no primary action, and this is one
  step down where "Mark it cooked" already lived. The whole method's dock
  carries NO apricot — it already ends on `cookFocusEndHtml`'s "Mark it
  cooked" under the last step, and a sticky copy would be the same action
  twice on one screen.
  **Three bugs found on the way, all pre-existing, all fixed here.**
  `cookFocusPrepHtml`'s `.cook-sectionhead` div was never closed, so the
  prep grid rendered as a flex item inside the header row at a third of the
  width — invisible while that section was the only thing above the recipe
  card, obvious with a ticklist under it. `renderCook` restored the scroll
  AFTER `wireCookFocusScroll`, so landing on a section was undone every
  time; nothing reached it before (every caller passes `data-at="steps"`),
  and opening the whole method from step seven does. And `.cook-sectionnote`
  was `--ink-inactive`, measured 3.21:1 on ground in light at 11px/700 —
  now `--ink-secondary`, 4.44:1 light / 8.48:1 dark, which is the ramp's own
  supporting-copy value and still 0.06 short of AA; that is written into the
  rule rather than fixed by reaching for a token that passes but means
  "struck off". **Also for Emily:** the serving stepper and the ticklist
  agree with each other, but a 4-serving recipe shows a 2-serving list for a
  household of two (`get_cooker_view`'s attendance scaling) — correct and
  pre-existing, just far more visible now that the amounts are the screen.
  `tests/test_cook_journey.py` is the guard, 38 tests, all of them RUNNING
  the screen's own functions under node rather than reading the source for a
  marker; 1830 total. Two existing tests were updated honestly rather than
  deleted, each saying what moved. Verified in a real Chromium at 390px and
  1280px, light and dark, against a seeded throwaway DB: Cook this → Before
  you start → start → step through → whole method (landing on the step you
  left) → back (place kept) → full page reload → ticks and resume point
  intact. Console clean apart from Google Fonts being unreachable in the
  sandbox.

- **2026-09-10 — A day printed dinner before lunch, because `slot` is a TEXT
  column. Branch `overnight/day-slot-order`.** `get_weekly_plan` ended
  `ORDER BY mpe.date ASC, mpe.slot ASC`, which is ALPHABETICAL — breakfast,
  dinner, lunch, snack. **Where it was actually visible** (measured against
  `main`, not assumed): the flat `meals` list — the assistant's own read of
  the week — and the two Kitchen renderers that walk it unsorted,
  `kitchenTodayRows` and `cookRestOfWeekHtml`. NOT the `menu` day dict,
  whose key order comes from `_build_day_based_menu`'s own dict literal and
  already read correctly; and not `cookTonightIndex` /
  `cookTomorrowFocusTarget`, which had each grown a private `COOK_SLOT_ORDER`
  to work around this. The order was already written down in `DAY_SLOTS`;
  the query never asked for it. New `weekly_plan.slot_order_sql(column)` builds
  the ORDER BY `CASE` from that tuple, so a slot added there is added
  everywhere at once, and an unknown slot sorts LAST rather than
  disappearing. `mpe.id` is the last word in both queries because a day can
  hold more than one snack: they tie on date AND slot, so without it SQLite
  may hand them back either way round and "first planned wins" (the `menu`'s
  single `snack` key) means a different snack run to run. `plan_quality.
  _load_plan_entries` had the identical sort and is fixed the same way — its
  rules mostly re-sort what they need, but `snack_clashes` reads the order
  straight through to name the first thing a snack repeats.
  `attendance.get_week_attendance` is deliberately NOT changed and carries a
  comment saying why: it builds a `{date: {slot: ...}}` lookup and every
  caller asks it for a slot by name, so that sort is read by nobody. One
  behaviour call rode along: `_build_day_based_menu` folds an unknown slot
  into `dinner`, and now that such a slot always arrives last it would have
  overwritten a real dinner (before, whether it won depended on its own
  spelling — 'brunch' lost, 'elevenses' won), so the real dinner is
  protected explicitly. Two sorts of the same class are knowingly left
  alone, out of this card's scope but worth a line: `leftovers.py`'s
  `targets.sort(key=(date, slot))`, which feeds the `covers_note` sentence,
  and `cook_ahead.py:202`. `tests/test_day_slot_order.py` is the guard,
  7 tests built on a day inserted in a deliberately scrambled order; 5 of
  them fail on the pre-fix code (the other two are no-regression guards and
  say so in their own docstrings). 1736 -> 1743.
- **2026-09-09 — The shops question closed itself on the first tap. Branch
  `overnight/onboarding-shops-multiselect`.** Emily, testing onboarding as a
  new household: "once I click one it brings me to a different screen that
  doesn't have anything. I should be able to click all the shops that I want
  to include in it." **Root cause is one predicate, not a missing screen:**
  `groStoresPromptShouldShow` (`static/shell.js`) was
  `!usualStores.length && !storesPromptDismissed`, and saving the first shop
  makes the first half false — so the card's own gate went false under the
  answer being typed into it, one shop was the most anyone could ever name,
  and what "advanced" was nothing at all. There is no second screen: what
  replaced the card was LIST with no stop cards yet, which for a brand-new
  household is one grey line. Reproduced against a real uvicorn on a
  throwaway DB before anything was changed.
  The card is multi-select now: chips toggle (`groToggleUsualStore`, which
  writes the WHOLE shorter list because `usual_stores` is a set on the
  server, not an append log), a `storesPromptOpen` flag keeps it up while it
  is being answered, a count line says how many are picked, a typed shop
  joins the chips instead of living apart from them, and **one button at the
  foot is the only way out** — labelled "One list is fine" with nothing
  picked and "That's where we shop" otherwise, both writing the same
  `/api/memory/stores-prompt-dismiss` that has always meant "this question is
  answered". Picked chips are celadon, not apricot: a picked shop is a
  settled fact, and nine apricot chips beside the card's one apricot button
  would be nine more primaries (Rule 5). For the same rule, LIST's foot drops
  "Start the trip" while the card is up — LIST returns the card INSTEAD of
  its stops, so that button pointed at stores not on the screen.
  **Two things left alone, deliberately.** The picker only names shops; it
  never tags an item, so what LIST shows straight afterwards is the
  store-less-list question that `f9b77e1` ("Show the grocery list to a
  household that never named a store", on `origin/main`) already answers —
  this branch is based one merge earlier and does not touch a single line
  that commit touches, so the two merge clean and re-implementing it here
  would only have made a conflict. And each toggle writes its own
  `preference_events` row, so `growth_count_this_month` now counts an
  un-pick as well as a pick; pre-existing shape, not worth a second write
  path. `tests/test_stores_multiselect.py` is the guard, 22 tests, 16 of them
  red on the previous commit — the front-end half RUNS the Grocery region's
  own functions under node against a small stub rather than reading the
  source for a marker, because the bug was a predicate going false, which is
  exactly what a source-marker test cannot see. **Verified in a real
  Chromium** at 390px in both colour schemes, on the reviewer's pass below:
  the nine chips wrap onto three rows, every one of them 44px tall, the card
  is 427px, the page never scrolls sideways and the only apricot on it is
  the button at the foot.
  **The reviewer's pass, same branch, three findings.** (1) *A reload
  part-way through ended the question for good.* `storesPromptOpen` was
  page-view only, so tapping one shop and reloading left the gate reading
  "shops named, never dismissed" — permanently false, while the database
  still said the question was unanswered, and the only remaining route to
  the other shops was Kitchen → What we know → Stores, which a brand-new
  household has never been shown. **Gating on `!storesPromptDismissed`
  alone is the trade-off NOT taken:** nothing backfills
  `stores_prompt_dismissed_at` (`app/db.py`), so every existing household —
  shops named long ago, no dismissal row — would have been asked all over
  again. The flag is persisted on the client instead, the shape the
  approved-week receipt's dismissal already uses, keyed per household
  (`pomona.storesPromptOpen.h<id>`). **localStorage, not sessionStorage**,
  and that is the whole decision: an installed PWA is killed and relaunched
  constantly and a relaunch ends the session, so a household that taps a
  shop, takes a phone call and comes back would lose the question exactly as
  it did before. The receipt can afford sessionStorage because coming back
  next session is its correct behaviour; an unfinished question coming back
  is the point of it. Every read and write is wrapped — this storage throws
  outright in Safari's private mode. (2) *Rapid taps silently lost shops.*
  Every tap recomputes the whole list from client state and posts it, so at
  a 400ms round trip with taps 150ms apart the second and third taps each
  read a list the first tap's answer had not reached, and wrote the earlier
  shops back out — two of three shops gone, and never on a laptop. Local
  state moves first now, and the writes are serialised: one in flight, and
  the one behind it sends whatever the list is when it actually goes out, so
  taps during a write collapse into a single trailing write and the last tap
  wins. A failed write re-reads `/api/memory`, because the count line must
  not keep claiming something the server does not hold. (3) *The picked chip
  was a weaker mark than the unpicked one* — celadon-tint on ground is
  1.12:1 and celadon-edge on hairline-strong 1.05:1, an inverted affordance
  and far under WCAG 1.4.11's 3:1 for a state indicator. The BORDER carries
  the state now, `--ink-strong`: measured in Chromium off computed styles,
  **9.73:1 light / 8.43:1 dark** against the unpicked chip beside it, with
  the label moved to `--ink-on-celadon` (10.65:1 / 10.37:1 on the fill) so
  the picked chip's text is no fainter than the unpicked one's. Not apricot,
  which would have been a second primary on a card whose foot button owns
  the screen's one. Rode along: un-picking a shop now prunes that shop's
  `store_typical_items` — the toggle writes a shorter whole list through
  `edit_preference`, which had no pruning, while `delete_preference` has
  always had it for exactly the reason written down there. Scoped to shops
  actually coming OFF the list, never to everything absent from it, because
  `add_store_typical_items` does not require a store to be a usual store
  first. `tests/test_stores_multiselect.py` is 34 tests now; 1792 total.
- **2026-09-09 — Three things the fast-sort slice got wrong, on the same
  branch (`overnight/grocery-fast-sort`).** Found by an independent
  reviewer, all three reproduced in a real Chromium, fixed in one pass on
  top. Worth reading as a set: every one of them is the same shape — a
  change that was right in itself and whose consequence one step downstream
  was not checked.
  - **Persisting "Any" made the row disappear.** The BLOCKER. Once an
    answered row stopped coming back to the queue, nothing else was showing
    it: `groListHtml` drew the loose pile only when there were NO stops, and
    `groStoreCardItems` folded it into a shop's card only for a *sole*-store
    household. So for everyone else a row with `store='' store_decided=1`
    was countable in the subtitle, present on the trip, and on no screen —
    with no ⋯ to change it back. A **regression on the parent**, where the
    page-view map meant a reload at least put the row back in the queue.
    New `groAnywhereCardHtml`: one card, "Anywhere · N", the sand avatar
    carrying a basket (there is no name to take a letter from), ordinary
    rows with the ordinary ⋯. Two things it must not do, both pinned by
    tests: print a one-shop household's list twice (its pile is already
    inside that shop's card) and pull rows that are still in the queue onto
    LIST. **Sub-case, same fix:** answer "Anywhere" for everything and there
    were no stops, so "Start the trip" never rendered. `groStoresWithNeeded`
    now falls back to the most-used shop when nothing is tagged and
    something has nowhere to go — the same defensible default "Put all 40 at
    Loblaws" already offers, not a second invention, and for a one-shop
    household it IS their one shop (that special case is gone, folded into
    this one). It reads RIDE-ALONGS, never the whole loose pile: a row still
    waiting in the queue is unasked, not homeless, and inventing a stop for
    it would answer the household's question for them. A stop holding
    nothing of its own draws no card — "Loblaws · 0" over "Anywhere · 2" is
    a card about nothing.
  - **The bulk write was not atomic, and its docstring said it was.**
    `set_grocery_items_stores` looped `set_grocery_item_store`, which opens,
    commits and closes per row; a failure on row 3 of 4 left rows 1 and 2
    written and returned a 500. That is worse here than in most places
    because the UNDO is itself a bulk assign, so a half-applied one leaves
    the list in a state nobody has a name for with the toast's chip already
    spent. Split into `_stage_grocery_item_store` (rows, on a caller-owned
    connection) and `_settle_grocery_item_store` (the preference write,
    after the commit) — the `atomic-period-takeover` shape, and for its
    reason: a nested `get_conn` inside an open write transaction waits on
    SQLite's single writer and dies of "database is locked", so the
    preference write CANNOT be inside the loop. Also bounded at
    `MAX_BULK_STORE_ASSIGNMENTS = 500`, refused with a 400 before anything
    is written; unbounded it was 5000 connections and 3.3s in one request.
  - **The undo spent its own payload on the way out.** `groBulkAssign`
    nulled `bulkUndo` before posting, so a failed undo took the only record
    of the previous state with it: forty rows at a shop nobody chose, a
    toast reading "try again", and no again. The payload is now held until
    the undo SUCCEEDS, and a failure re-offers the chip
    ("Couldn't undo that — tap Undo to try again"). Safe precisely because
    the server side is all-or-nothing now — a failure means nothing moved,
    so the payload still describes the list exactly. The two fixes are one
    fix.
  - **Two writers of `store`, one of them maintaining `store_decided`.**
    `_apply_store_to_matching_rows` (behind `set_item_store`, reachable from
    chat and the Kitchen Stores sheet) wrote the store and never the flag,
    so clearing a preference left the row permanently "answered" with no
    shop on it — never re-asked, and per the blocker invisible. Exactly the
    `snacks_per_week_set` drift class this file warns about. It writes both
    now: a real store answers the question, clearing the preference
    re-opens it, which is what `delete_preference` does for its own flag.
  - **Four nits with it.** The trip's trolley and its commit took every
    Unassigned in-cart row while the screen above them showed only the ones
    that ride along — one filter now (`groRideAlongInCart`), so a stop
    cannot commit something that was never on it. WHERE NEXT gained a back
    link that REOPENS the stop just finished (`tripLastDone`): "Done at
    Costco" is a full-width apricot under a list of things still to tick,
    and the mis-tap used to end that shop for the trip. The `.gro-next-note`
    comment said 7.44:1 dark where every other record said 7.36:1. And
    `test_the_new_screens_use_tokens_only` ended `... or True`, so it could
    never fail.
  - **The test harness was widened rather than argued with.** Both concerns
    lived in `onGroceryClick`, which the first pass covered with source
    markers only, so `_grocery_block()` now runs the handlers too — eleven
    lines of fake event and element, no DOM. `tests/test_grocery_fast_sort.py`
    is 47 tests (30 -> 47); ten of the seventeen new ones fail on the commit
    they were written against, the rest are controls for the double-print
    and snapshot risks this pass introduced. Suite 1822 -> 1839. Re-verified
    in a real Chromium at 390px, light and dark, on the reviewer's own
    reproduction: the answered row visible and editable across a reload,
    moved to a shop and back, everything-Anywhere still able to start a
    trip, a 500 injected into the undo leaving the rows untouched and the
    chip re-offered, an oversize batch refused with 400, and a mis-tapped
    stop reopened and finished again without being re-offered. Console
    clean.
- **2026-09-09 — Sorting forty things stopped costing forty screens, and an
  "Any" answer finally survives a reload. Branch
  `overnight/grocery-fast-sort`** (stacked on
  `overnight/onboarding-shops-multiselect`, both in the Grocery region).
  Emily: *"if there's 40 ingredients ... it can take too long to go through
  the screens all like this."* The one-at-a-time queue is untouched and is
  still what a handful gets (`GRO_FAST_SORT_MIN = 6`); above that the badge
  opens a chooser (step `sorthow`) whose foot apricot is **"Put all 40 at
  Loblaws"** and whose body is two quiet rows — "Sort them all on one
  screen" and "Or one at a time · 1 of 40". Three new steps of the same tab
  (`sorthow`, `sortall`, `next`), same `/grocery`, same step machine.
  - **The most-used shop is read off the list the tab already has open** —
    rows tagged to a shop, needed + in the trolley + bought this cycle,
    biggest wins, ties broken by the order the household named its shops
    (`groMostUsedStore`). No new counter, no new fetch.
  - **SORT ALL stages, it does not save per tap.** Forty rows × a request
    each is the same complaint one level down, and a re-render would move
    the list under the thumb working down it. A chip tap edits
    `sortAllPicks` and repaints that one row's chips in the DOM — measured
    in Chromium: zero `/api` requests, scroll position unchanged, neighbours
    untouched. The foot button sends all forty at once.
  - **One backend change, and it is a column, not an endpoint.**
    `grocery_items.store_decided` (schema.sql + `_MIGRATIONS`, default 0,
    surfaced by `list_grocery_list`). It exists because `store = ''` means
    both "never asked" and "asked, no particular shop", which is exactly the
    KNOWN LIMIT this file recorded: `anyStoreIds` was a page-view map, so a
    reload put every skipped item back in the queue. `anyStoreIds` is gone;
    `groItemDecided` reads the column. **What "Any" now means, and where
    those rows live:** it is an ANSWER — "no particular shop" — and it
    sticks. LIST's row ⋯ "Any" used to push a row back into the to-sort
    queue and now settles it exactly as SORT's pill does, deliberately:
    being asked again about something you just answered is the annoyance
    the whole slice exists to remove. Answered rows live in an **"Anywhere"
    card** on LIST, beside the store cards, every row carrying the same ⋯
    so a household can change its mind. That card is not decoration — the
    first cut of this branch shipped without it, and a row answered "Any"
    was then on NO screen for a multi-shop household: out of the badge
    (answered), out of every store card (no store), and so out of reach of
    the only control that could move it. See the correction entry below.
  - **One new route, `POST /api/grocery-list/store-bulk`**
    (`tools.set_grocery_items_stores`), because "one tap" that is forty
    round trips is not one tap, and because an undo has to restore every
    row's store AND its `decided` flag together rather than half of them.
    Undo sends the rows as they were BEFORE the write (`groPreviousStores`),
    never "everything to unsorted" — a row already answered "Any", or
    already tagged to a shop, comes back the way it was. `remember` defaults
    to **false** on the bulk route and true on the single-row one: one tap
    must not become forty remembered opinions, and the one-at-a-time queue
    still offers its "Remember for {store}?" per item.
  - **A household with one shop, or none, never sees a sorting step.**
    `groCanSort` (more than one shop to choose between) gates `groUnsorted`,
    so the badge, the step and its fast paths go quiet in one place rather
    than six. It also fixed a dead end nobody had reported: a one-shop
    household's list sat entirely unassigned, so `groStoresWithNeeded`
    returned no stops and **"Start the trip" never appeared**. That shop is
    now a stop, and its card covers the loose pile (`groSoleStore`,
    `groStoreCardItems`). Verified at 390px: one card "Loblaws · 40", no
    badge, trip opens with all 40 on it.
  - **Finishing a stop asks where next instead of assuming.** New `next`
    step: the stops still ahead with what is left on each, plus "I'm done
    shopping for today". The snapshot rule holds — `tripStops` is never
    reordered; `tripDone` (by NAME, since the visiting order is now the
    household's) is what keeps a finished stop off the screen. Two things
    changed with it: the trip button is "Done at Farm Boy" rather than
    naming a next stop it no longer picks, and the subtitle counts stops
    BEHIND you (`Stop ' + (done + 1)`) — the snapshot index would have said
    "Stop 3 of 3" with two shops still waiting. **Also simplified:**
    shopless things used to ride with `tripIndex === 0` and were stranded if
    left unbought there; they now follow the shopper to whatever stop is
    open, which is both simpler and the only thing that survives the
    household choosing its own order.
  - **No second apricot anywhere.** `sorthow`'s is the bulk button (its two
    body rows are plain), `sortall`'s is the foot, and `next` has **none** —
    the stops above it are the choice, and an apricot on "I'm done" would
    put the tab's accent on ending the trip early. New `.gro-secondary` for
    that one button. Contrast measured in Chromium in both schemes and
    recorded in `shell.css`: row titles 13.52:1 light / 12.49:1 dark, their
    sub-lines 4.70:1 / 7.36:1, the secondary's label 11.60:1 / 11.28:1. The
    "will come with you" note sits INSIDE the stops card rather than under
    it, because `--ink-secondary` is 4.70:1 on `--surface` and only 4.44:1
    on `--ground`.
  - **Verified in a real Chromium at 390px, light and dark:** 40 unsorted
    items, the chooser, the bulk assign and its undo (badge back at 40 TO
    SORT), the sort-all screen and a single-row exception, an "Any" answer
    surviving a reload (39 TO SORT after), a three-stop trip with the stops
    taken out of order and the finished one never re-offered, and the
    one-shop and no-shop households. Console clean apart from Google Fonts
    being unreachable in the sandbox, which also means the screenshots show
    fallback typefaces. `tests/test_grocery_fast_sort.py` is the guard, 30
    tests (mostly running shell.js's own functions under node, since every
    bug here is behaviour a source marker cannot see); 1822 total at the
    time, 1839 after the correction pass above. Four
    assertions in `tests/test_grocery_steps.py` were updated honestly rather
    than deleted, each saying what moved.
  - **Deliberately not done:** a household with NO shop still cannot start a
    trip (pre-existing — it has no stops, and the ticket's answer for them
    was to stop asking, not to invent one); SORT ALL's staged picks are lost
    if you leave the screen without saving, which is why nothing is claimed
    until the button; the foot button on a forty-row SORT ALL is forty rows
    down, matching the tab's grammar rather than pinning a second copy at
    the top; and no chat tool was added for any of this — it is all screen
    work on routes the assistant already has.
- **2026-09-10 — The other half of "setup finishes once": two taps of the
  SKIP link started two concurrent runs. Same branch
  `overnight/onboarding-go-back`, second review pass.** The guard in the
  entry below set its flag *after* four awaited POSTs and leaned on
  `btn.disabled` for the window in between. That covered Continue — a
  disabled button dispatches no click — and covered the skip link not at
  all: it is a `<span>`, and it called `finishSetupAndReveal(null)`, so
  there was no button to disable and nothing on screen changed. Two genuine
  taps therefore started two runs at once. Measured in a real Chromium at
  300ms of save latency: **12 POSTs, interleaved, three generations** where
  there should be four and one. Both runs reach `generate_weekly_plan` ->
  `tools.retire_overlapping_plans` and both claim the same days through the
  same `_first_plan_window`, so the overlap is total and the second retires
  the first — the same damage the history trap was written to prevent,
  reached one step earlier and worse, because two generations are in flight
  at once. **Why it survived:** on localhost the window is ~50ms and the
  bug is invisible; on a phone talking to Railway it is half a second to two
  seconds of a link that showed no sign of having been tapped. An impatient
  second tap is expected behaviour, not exotic.
  **The fix is three states rather than a boolean, and no reliance on a
  control.** `setupRun` is `'idle' | 'running' | 'done'`; `'running'` is set
  SYNCHRONOUSLY, before the first `await` and without reference to any
  button, and it is what a second tap hits. The `catch` puts it back to
  `'idle'`, because a save that failed never asked for a week. `'done'` is
  the original permanent guard. `finishSetupAndReveal` takes no argument at
  all now — the `btn.disabled` idiom is gone, since it only ever covered the
  button. **And the reason the second tap happened gets fixed too:**
  `setKitRepeatsBusy` puts BOTH controls into the same in-progress state,
  so the skip link says "Saving your answers…" and stops being tappable
  (`.skip-link.is-busy`) for as long as the run takes. `.skip-link` also
  moved off `--text-muted` onto the canonical `--ink-secondary` while it
  was being touched (§1) — same value, measured 4.44:1 light / 8.48:1 dark.
  Verified in a real Chromium on a throwaway DB across the reviewer's whole
  matrix (0/120/300/600ms save latency, 50-1500ms between taps), with the
  latency applied INSIDE the page rather than in the route handler so the
  page's own timeline is the one being slowed: **4 POSTs and one generation
  every time**, including a tap dispatched straight at the handler to
  bypass `pointer-events` — so what holds is the flag, not the CSS. Both
  things that must not break were re-checked: a failed SAVE frees the
  controls and `setupRun` goes back to `'idle'`, and a failed GENERATION
  still lets "Try again" run a second one with the reveal trap re-arming
  after it. 5 more tests (30 -> 35); 1788 -> 1793. The new ones fail on the
  previous commit (10 POSTs, two generations, under node with a 120ms
  save).

- **2026-09-10 — The finished wizard was one back swipe away, and
  re-finishing destroyed the week it had just built. Same branch
  `overnight/onboarding-go-back`, review pass.** An independent reviewer
  reproduced it in a real Chromium: at the reveal, one back gesture and two
  taps of Continue, and the page posted the household, the rhythm, the
  answers and `generate-first-plan` a **second** time — eight POSTs where
  there should be four. The second generation runs
  `tools.retire_overlapping_plans` (`app/agent.py`) over the first, so it
  destroys the meals and reverses the grocery lines of the week the
  household was looking at. **A regression, not a pre-existing hole:** on
  `main` there are no pushed entries to swipe back into, and the entry below
  says in its own words that the reveal "replaces its entry" so this cannot
  happen. `replaceState` rewrites ONE entry, the one you are standing on;
  the other nine were untouched. **Two guards now, deliberately, because the
  damage is real data.** *Reachability:* `revealReached` is set the first
  time `showStep('reveal')` runs and the popstate handler answers every
  gesture from then on by pushing the reveal entry back and re-showing it —
  the gesture is spent, the screen doesn't move, and pushing truncates the
  forward stack so a forward swipe has nowhere to go either. *Safety:*
  `finishSetupAndReveal` is a no-op after the first successful pass, however
  it is reached; a save that FAILS lets the household try again, and the
  reveal's own "Try again" is untouched, since it is only on screen when
  nothing came back and there is therefore no week for a second attempt to
  take over. (**The first version of that safety guard was incomplete and
  leaked on the skip link — corrected the same day, see the entry above
  this one.**) Three more things
  went with it. **(a) A reload mid-flow left history lying.** The entries
  ahead still named later steps while every in-memory answer was gone, so a
  forward swipe reached, say, `restrictions` with no diet blocks on it, and
  finishing from there posted. Entries now carry a `PAGE_LOAD` stamp;
  `startOnboarding` PUSHES rather than replaces when it finds a step in
  `history.state` (pushState truncates the stale forward entries outright),
  and a gesture back onto an entry from an older load collapses onto the
  household step and takes the entry over. **(b) Nothing re-checked that
  the household had anybody in it.** "Add at least one person" lived only on
  the household step's own Continue, so a gesture past it let a household of
  ZERO people post and be handed a generated week. `finishSetupAndReveal`
  re-validates and sends them back to the step that fixes it, and the alert
  is now a line ON that step (`#household-empty`, `--urgent`, measured
  5.54:1) — an alert covers the list of names it is talking about.
  **(c) A restriction could transfer to a different person when a name was
  reused.** `restrictionAnswers` is keyed by name and was pruned only inside
  `buildRestrictionsStep`; remove Sam, rename Alex to Sam, and a history
  jump that skips that rebuild ships Sam's *peanut allergy* as Alex's — by
  then there IS a Sam, so nothing downstream can tell. Pruning now happens
  on the household EDIT (`pruneMemberKeyedAnswers`, wired to the remove
  button and the name input), which catches the removal while the name is
  still gone, and again inside `currentRestrictions()` so the payload
  guarantee stops depending on which screens were drawn. `rhythmLunchLocation`
  and `rhythmCookingWho` get the same treatment — same shape, same
  end-of-setup POST. The backend's only member identity is the NAME, so two
  people called Sam are still indistinguishable to it; that is a bigger
  ticket, and this closes the half that is reachable from here.
  **Nit fixed with them:** the back control moved off `--text-muted` (a
  legacy alias) onto the canonical `--ink-secondary` at the shell step
  link's own 14px/700 — measured in Chromium at 390px, 4.44:1 light and
  8.48:1 dark, hit area 91x44. Light is 0.06 under AA for normal text and
  is under it everywhere in this app (the shell's `.crumb` carries the same
  value); clearing it means changing `--ink-secondary` itself, which is
  **Emily's Tier 2 call**, not a one-screen hex. **And the test harness is
  why this survived:** `tests/test_onboarding_go_back.py`'s history stub
  modelled `back()` as a POP with no `history.state` at all, so a forward
  entry could not exist in it and a reloaded page could not be described;
  the one reveal test asserted the stack didn't GROW, which was true and had
  nothing to do with reachability. It is a real back/forward stack with a
  cursor now, and the blocker test fails against the pre-fix page (it walks
  reveal -> typical-week -> dinners -> excited-about). 18 -> 30 tests there;
  1776 -> 1788. `test_chores_setup_split`'s dot assertion, which had been
  updated into `assert x == x`, asserts something falsifiable again.
  **Verified in a real Chromium at 390px** on a throwaway DB with the
  generation stubbed at the network layer: the reviewer's reproduction goes
  from 8 POSTs and two generations to 4 and one.

- **2026-09-09 — Onboarding had a way back and nobody could find it, and
  two steps had already written their answer down by the time you did.
  Branch `overnight/onboarding-go-back`.** Emily: "add a go back option in
  case I want to go back to change responses." There WAS one — a bare
  "← Back" under Continue on every step after the first — which is the more
  useful bug report: a control placed after the primary action, named after
  the gesture rather than after where it goes, is one you find only once you
  have already given up on the screen. It is now a `‹ <the previous step>`
  button at the TOP of each step (`STEP_TITLES`, `renderBackLink`), and the
  three things around it that were actually broken are the change:
  (1) **Arriving at a step redraws it** — `STEP_BUILDERS` keyed by step, run
  by `showStep` on every arrival, so a step shows the answers as they stand
  rather than whatever the last render left behind. This is what
  `buildRestrictionsStep` got wrong: it rebuilt itself from the current
  household every time it was reached and wiped the chips it was meant to
  redraw, so coming forward after any change handed back a blank question.
  Its answers live in `restrictionAnswers` now (name -> chips/allergy/other)
  and the chips are drawn from them.
  (2) **The dependency rule is derivation, never a table of what to
  invalidate.** Back destinations come from `stepFlow()`/`stepBefore()`, not
  from a `data-back="restrictions"` stamped on each step's markup — that
  form is a table somebody has to keep in step with the flow, and the first
  conditional step would turn every one of those attributes into a lie
  nothing here would catch. Same shape one level down: an answer keyed by a
  member name is dropped at RENDER time when nobody by that name is in the
  household (`renderLunchPeople` and `renderCookingWhoChips` already did
  this; `buildRestrictionsStep` does now), and the solo-adult branch stays
  `applySoloAdultDefaults`'s derivation — go back, add a second person, and
  "who cooks" is a real question again with the filled-in answer cleared.
  (3) **Nothing reaches the household until setup finishes.** Two steps used
  to POST on the way past — members when the household step was left, the
  rhythm facts when the second rhythm step was — and both are keyed by NAME.
  `add_member` is get-or-create by name and **nothing in this app deletes a
  member**, so "Jamie" typed, corrected to "James", and continued through
  left a household of three, two of them the same person, with no way to
  take one back out. All four writes moved into `finishSetupAndReveal`, in
  order (members, rhythm, answers, plan-the-week), so there is never a first
  copy for a second one to duplicate.
  `test_a_corrected_name_would_leave_two_of_the_same_person` characterises
  the old behaviour against the real route, so the reason stays written down.
  The back GESTURE: one history entry per step; the named control walks the
  stack back with `history.back()` rather than pushing, which is what stops
  the gesture right after a back tap from bouncing forward onto the step you
  just left, and the popstate handler corrects the landing if it is ever not
  the step the label promised. This is the one place in the repo where a
  back link uses `history.back()`, deliberately: onboarding is linear, so
  the entry behind you IS the step behind you — the rule elsewhere exists
  because a tab you can wander around in has no such guarantee. The reveal
  **replaces** its entry rather than pushing, so arriving at it doesn't add
  one. ~~That is what makes a gesture back into the finished wizard
  impossible.~~ **WRONG, and it shipped: `replaceState` rewrites the entry
  you are STANDING on and leaves the nine behind it exactly where they
  were.** One back swipe landed on typical-week with every answer still in
  memory, and two taps of Continue ran the whole finish again — a second
  LLM week, plus `retire_overlapping_plans` destroying the meals and
  reversing the grocery lines of the week just shown. Fixed the next day;
  see the 2026-09-10 entry above for what actually makes the reveal
  terminal. An unrecognised popstate
  state is left to the browser rather than trapping somebody on question one.
  **Two copy/behaviour changes, both forced by (3) and both Emily's to
  veto:** rhythm-2's CTA says "Continue" instead of "Save my rhythm" (it no
  longer saves), and a rhythm-save failure is now reported at the end of
  setup rather than at the rhythm step. `tests/test_onboarding_go_back.py`
  is the guard, 18 tests — the page's own navigation and both rebuild paths
  RUN under node against a DOM stub, because the original bug is exactly
  what a source-marker test cannot see; 1758 -> 1776. Two existing files
  were corrected honestly rather than deleted:
  `test_chores_setup_split`'s dot-count test compared two hand-written lists
  and one of them is derived now, and `test_onboarding_age_group`'s
  docstrings said the household POST fires "right after the household step".
  **Verified end to end in a real chromium at 390px** (Playwright, throwaway
  DB, port 8934): forward, back by control, back by gesture, household
  changed, forward again — one Robin in `members`, no orphan Jamie anywhere,
  and the only non-GET before the finish was the sign-in. **Not done, on
  purpose:** the flow is still every step for every household
  (`stepFlow()` derives it but has nothing to drop yet), and correcting an
  answer after setup is still Preferences' job — the reveal has no way back
  (**true as a design intention, and only true of the CODE since the
  2026-09-10 entry above**).
- **2026-09-10 — The chat link's target is the dish's NAME AND NOTHING
  ELSE, because a real swap recreates the entry. Same branch,
  `tap-a-meal-opens-recipe`, second review.** The fix in the entry below
  put the dish's name in the markup and re-resolved it at tap time, which
  was right and was not enough. `dishTargetForName` still handed back the
  whole `{entryId, date, slot, title}` target the index had recorded, and
  `cookResolveFocusIndex` tries `entryId` first and then **date + slot with
  no name check at all**. `weekly_plan.swap_meal_in_plan` DELETES the plan
  entry and creates a new one, so after any real swap the recorded id is
  *always* a miss and that unchecked fallback *always* fires — landing on
  whatever dish now occupies that night. Reproduced end to end in Chromium
  through the app's own write path (entry 15 -> 29, no hand-edited rows):
  a reply's link labelled **Chicken Tacos** opened **Bean Chili, Thursday
  Sep 10**, and the only entry-bearing control on that screen was
  `Mark it cooked` carrying **id 29** — so the household believes it is
  ticking the dish it tapped and ticks the one that replaced it.
  - **The fix is scoped to the chat link, deliberately.**
    `dishTargetForName` returns `{title}` only, so both of the resolver's
    earlier branches are unreachable from a chat link and the name match —
    which compares against the cook card's own `meal` — does the work.
    `cookResolveFocusIndex` is **not** changed: Today's rows and Meals'
    "Cook this" read their target and their label out of one payload in
    one breath, and their id/date fallbacks are correct *for them*. Adding
    a name check inside the shared date+slot branch would have been a
    change to every caller's contract to fix one caller's misuse of it.
  - **A tap now re-reads the plan before it opens anything**
    (`openDishFromChat` -> `readDishIndex`). `refreshDishIndex` is silent
    on failure by design, so one dropped `/api/week-menu` leaves a reply
    naming last week's dinners indefinitely; a deliberate tap can afford
    one local SQLite lookup. A dish that has left the plan now says so
    instead of opening a screen for a meal nobody is cooking — which is the
    lower-severity half of the same report. A failed re-read falls through
    to the index in hand, which still cannot open a *different* dish; it is
    only the "this is gone" message that needs the network.
  - **What is deliberately NOT done:** the cook view is not force-reloaded
    on the way in. If the index and the cooker view are somehow stale
    *together*, the screen can still show a dish under its own name that
    has since left the plan. That fails loudly rather than wrongly —
    `cooker.check_off_meal` raises `No meal plan entry with id N` for a
    deleted entry, so nothing else gets ticked — and a reload while a meal
    is focused would move `cookState.focusIdx`, which is a fresh instance
    of the exact bug being fixed here. Worth a ticket, not a smuggled
    change.
  - **The guard is an integration test, not a source marker**
    (`test_a_real_swap_cannot_make_a_chat_link_open_the_new_dish` and its
    stale-index twin): it plans a week, builds the index from the real
    `get_week_menu` payload, runs the real `swap_meal_in_plan`, and
    resolves the old reply's link against the real `get_cooker_view` with
    the screen's own two functions under node. It asserts the entry id
    really did change first, so it can never go toothless if the swap path
    stops recreating. Both fail on the previous commit with
    `AssertionError: 'Bean Chili' != 'Bean Chili'`. Suite 1790 -> 1792.

- **2026-09-10 — Six things review found on `tap-a-meal-opens-recipe`, and
  the blocker among them. Same branch.** An independent reviewer drove the
  branch in a real browser and reproduced all six. Fixed in one pass, on
  top of the existing commits.
  - **THE BLOCKER: a chat reply's dish link kept its label and lost its
    target.** The markup carried the dish's POSITION in `dishIndex.entries`
    and read it back at CLICK time — but that array is rebuilt and
    re-sorted (longest name first) on every `/api/week-menu` read,
    including the one `sendAskMessage` fires on the line straight after it
    renders the bubble. So exactly the turns that most want a link — the
    ones that changed the week — invalidated the indices inside the reply
    describing that change. Reproduced in Chromium: a bubble reading
    `data-dish="2"` and labelled **Chicken Tacos** opened **Sheet Pan
    Salmon**, on a screen whose apricot primary is "Mark it cooked", i.e.
    one tap from a wrong write. **The link carries the dish's NAME now**,
    and `dishTargetForName` re-resolves it against the plan as it stands
    when it is tapped, so a link either opens the dish it names or opens
    nothing and says so ("That's not on the plan any more."). This is the
    failure `cookResolveFocusIndex` exists to prevent, reached from the one
    direction that wasn't going through it. **That fix was necessary and
    NOT sufficient** — a second review broke the same invariant through the
    app's own swap path, one level below it; see the entry above this one.
    Putting the name in the markup was right; handing the resolver the
    whole recorded target was still wrong.
  - **A dish on TWO nights is not linked at all.** The index kept the first
    occurrence and dropped the rest, so a reply saying "Chicken Tacos is on
    Saturday" opened Thursday — and cook mode's check-off writes against
    that `entry_id`, so "Mark it cooked" would have ticked the wrong night.
    Which night a sentence means cannot be read out of generated prose
    without trusting exactly what this feature refuses to trust everywhere
    else, so the honest answer is the smaller one: a name that names two
    meals links to neither and stays prose. Every other dish still links.
  - **The reply is walked as TEXT NODES now, not regexed as HTML.** With
    dishes named "table" and "strong", running the regex over
    `renderMarkdownLite`'s output reached inside `<table
    class="ask-msg-table">` and `<strong>` and flattened the reply's
    markdown table into literal escaped tags. Nothing could be injected —
    the escaping was correct on both sides — but it is the blocker's own
    root cause one level over: rewriting a rendered artifact instead of the
    thing it was rendered from. `dishSegments` (pure, over one run of text)
    plus `linkifyDishNamesIn` (walks `childNodes`, skips BUTTON/A/CODE/PRE)
    replace `linkifyDishNames`.
  - **Two entry points now say where they came from.**
    `runTodayMoveAction` and Grocery's shop-done handoff called
    `activateTab` directly, so they inherited whatever `focusOrigin` an
    earlier deep link had left: Meals -> Thursday -> Cook this -> leave by
    the tab bar -> tap a cook row on Today, and back said "‹ Thursday" and
    dropped you on Meals. With no stale state at all it still made one
    screen disagree with itself — Today's Next up DISH NAME said "‹ Today"
    while the row's own button 200px below said "‹ Kitchen". Both go
    through `openRecipeFor` now, with `{label: 'Today'}` and
    `{label: 'Grocery'}`.
  - **The back link adds no history entries.** `cookExitFocus` pushed in
    `activateTab` and pushed again in `goMealsStep`, so one press grew
    `history.length` by two (measured 7 -> 9 by the reviewer, 17 -> 19
    here) and the following back gesture skipped the Day step onto a state
    nobody had visited. Going back UP a level MOVES the entry you are
    standing on: `activateTab` takes `opts.replaceHistory` and
    `goMealsStep` takes `{replace: true}`, both `replaceState`. Measured
    17 -> 17 after.
  - **Coming back to Kitchen by the tab bar redraws the cook screen.**
    Clearing `focusOrigin` there without re-rendering left a still-mounted
    cook screen reading "‹ Today" while it now landed on Kitchen. It only
    redraws when there is a deeper screen mounted to redraw.
  - **Two judgement calls, said plainly.** (1) The Meal step no longer
    prints a "no saved recipe detail" card for a grab-and-go SNACK — that
    card's whole content would be "there isn't one", which is the same
    empty card the plate card is already hidden for on that slot. A
    breakfast/lunch/dinner with no recipe keeps the line, because there the
    absence is worth saying and it names the way to fill it in. (2) A
    linked dish name is visually identical to an unlinked one
    (`.dish-link` is `color: inherit`), so in a dense row there is no
    affordance at all beyond tapping it. **Left that way on purpose**: hard
    rule 5 gives a screen one accent and it belongs to that screen's
    primary action, and an underline or an apricot on every dish name in a
    seven-row week card would turn the card into a page of links. The
    discoverability is the row itself, which has always been tappable. If
    Emily wants it visible, the change is one rule in `.dish-link` — not a
    per-screen decision.
  - `.dish-link.is-inline` is new: the block/full-width default is right
    for a row title and wrong inside a sentence — measured at 390px, the
    Grocery line took the whole width and dropped its full stop onto the
    next line.
  - **Verified in a real Chromium this time** (390px and 1280px, against a
    throwaway seeded DB and a throwaway uvicorn), by running the same
    script against the PRE-FIX `shell.js` and the fixed one: every one of
    the six reproduced before and none after. Only the model call was
    stubbed — `/api/chat/stream` was fulfilled with a canned reply, and the
    week really changed underneath it first, exactly as a real turn does.
    Eight new tests in `tests/test_tap_a_meal_opens_recipe.py`, each of
    which fails on the pre-review commit; suite 1782 -> 1790.
    **One thing that verification could not have caught**, and should be
    read as a limit on it: the week was changed by writing to the database
    directly, so the plan entry kept its id. A real swap DELETES and
    RECREATES it, which is what let the same invariant break again — see
    the entry above. **A reproduction that fakes the write is a
    reproduction of a different bug.**

- **2026-09-09 — A dish name is a link to its recipe, everywhere it
  appears. Branch `overnight/tap-a-meal-opens-recipe`.** Emily, after
  testing the app: "if you click the meal anywhere throughout the app, it
  should bring you to the screen with the recipe on it." **Which screen
  that is was the whole decision.** There is exactly ONE screen in this
  app with a recipe on it — cook mode, the focused single-meal step of
  Kitchen (`cookFocusHtml` -> `cookDetailHtml`) — so that is the recipe
  screen, and nothing new was built. Meals' Meal step (`mealStepHtml`) was
  the other candidate and it had no recipe on it at all: it now renders
  the SAME panel `plain` (`cookDetailHtml(m, idx, false, true)`) —
  ingredients, advance prep, Do ahead / Day of steps, and the
  "Freeform meal — no saved recipe detail" sentence for a dish nobody has
  written one for. Plain is the same renderer with every writing control
  taken off (serves stepper, mic, step checkboxes, "Fill in this recipe",
  the end-of-cook row, "Why this?") **and its element ids**: those are all
  handles for `onCookClick`/`renderCook`, which only ever redraw the
  Kitchen panel, and a second `#cook-ings-3` on another panel would have
  `getElementById` reaching the wrong screen. One renderer in two frames,
  exactly as the Meal step already borrows `cookAheadHtml`; two renderers
  is how two screens end up saying different things about one dish.
  **Every tap goes through one door,** `openRecipeFor(target, origin)`,
  which sets `cookState.focusOrigin` and then does the
  `activateTab('kitchen', …, { cookFocus })` each call site used to do for
  itself. The origin is what makes the breadcrumb rule hold: cook mode's
  back link was the literal "‹ Kitchen", which is a lie the moment a dish
  name on Today opens it, so it is `cookBackLabel()` now — "‹ Today",
  "‹ Monday", "‹ the chat", and still "‹ Kitchen" for every entry point
  that existed before. `cookExitFocus` resets Kitchen's own screen FIRST
  (a tab left sitting on a cook screen reopens there) and then returns to
  the origin — for Meals, to the exact step, and for chat, to the tab the
  reply was on with the conversation reopened. The origin is cleared by
  Kitchen's own rows, by `cookEnterSession`, and by any plain
  `activateTab('kitchen')`, so it only ever means "this deep link".
  (**That list was written as if it were exhaustive and it was not** — two
  entry points set no origin at all and inherited a stale one. Corrected
  2026-09-10; see the review-pass entry above this one.)
  **What now links, and what deliberately does not.** Newly linked: Today's
  Next up headline, a DONE row's dish name (the row stops being the move's
  button — the tick is the only control left — but a cooked dinner is
  exactly the name someone taps wanting to see what went into it), and the
  Tomorrow card. Already linked and unchanged: Kitchen's "Cooking today",
  "The rest of the week" and prep-session rows, Meals' Day-step slot card
  (which opens the Meal step, and the Meal step now has the recipe on it).
  Deliberately NOT links, each for a reason: **a reheat night** (there is
  no cook, so nothing for a cook screen to hold — the rule Kitchen's rows
  already followed, Emily 2026-09-04), an away or open slot, the Week
  card's own dish lines and the "See the whole week" sheet's cells (a day
  row is one `<button>` and a dish line inside it is ~18px — you cannot
  nest a button, and you cannot make a third of a row clear 44px; the row
  IS the link, one level up), the needs-you band's suggestion rows (those
  dishes are not on the plan yet and the row's job is Pick), the
  cook-ahead ask's sentence, and `static/share.html` (a public printed
  menu with no app behind it). ~~**Grocery names no dishes at all** — a
  grocery row is an ingredient — so the ticket's "beside grocery items"
  had nothing to link.~~ **Wrong, corrected 2026-09-10:** Grocery names
  exactly ONE dish, in the shop-done handoff ("That's the shopping done.
  Tonight it's <dish>."), and it is a link now like every other. The rest
  of the sentence stands: a grocery ROW is an ingredient and links nothing.
  **Chat replies: linked, and the mechanism is the honest part.** Nothing
  in the `ChatAction` contract says which words of a reply are dishes, and
  asking the model to mark them up would be trusting generated text about
  the plan. So `linkifyDishNames` does not look for dish names in the
  reply — it looks for the dishes it already KNOWS are on the plan
  (`setDishIndex`, off the `/api/week-menu` payload `renderWeekMenu` and
  the ask sheet's own quick-action fetch already have, so no request of its
  own) and links exactly those. That set is also exactly the set that HAS
  a recipe screen, since cook mode is per plan entry: a dish the app can't
  open stays prose. Names are matched longest-first ("Chicken Tacos" beats
  "Chicken"), whole-word only, and the whole reply is rewritten in ONE
  `String.replace` pass so nothing inserted is ever rescanned. A chat
  change to a week whose panel was never built refreshes the index on its
  own (`refreshDishIndex` off `refreshStaleTabsFromActions`) — the "panels
  build once per page load" gotcha, one level down.
  **The 44px floor** is met by the invisible-hitbox trick `.wg2-why` and
  `.gro-icon-btn` already use: `.dish-link::after` is a 44px box centred on
  the name (`min-height: 100%` so a two-line hero title keeps its whole
  area), and `.ask-dish::after` is `inset: -12px -4px` around an inline
  word. The inline one overlaps the lines above and below — that is a real
  trade and it is written down in the CSS: a mis-tap opens a recipe, and
  the way back is one named link. `.dish-link` is declared BEFORE
  `.rest-row-title`, `.tomorrow-title` and `.hero-dish` on purpose, since
  those set their own family/size/weight/colour and win an
  equal-specificity tie by being later — the class only removes the
  browser's button furniture, it never recolours a name.
  **Two existing tests were updated honestly rather than deleted:**
  `test_the_back_link_says_kitchen` (renamed
  `..._says_where_it_came_from`; the rule it is about — up one level BY
  NAME, never `history.back()` — is unchanged, only the source of the name
  is) and `test_cook_this_still_passes_the_exact_meal` (the same four-field
  target, handed to `openRecipeFor` instead of `activateTab` directly).
  `tests/test_tap_a_meal_opens_recipe.py` is the new guard, 32 tests as of
  the review pass below, most of them running the screen's own functions
  under node — the bug was "the name looks tappable and nothing happens",
  which a source-marker test cannot see. (**Its counts were written
  pre-merge and were wrong on the tree:** 24 tests and "1736 -> 1760" were
  this branch measured on its own; the merged tree was 1782 before the
  review pass and is 1790 after it.)
  **Not verified in a browser at the time** — no browser tooling in that
  session, so the LAYOUT at 390px and on desktop went unchecked. **It has
  been now** (2026-09-10, real Chromium at 390px and 1280px); see the
  review-pass entry above.

- **2026-09-09 — Nobody had told the household how to talk to the app.
  Branch `coaching-how-to-talk-to-me`.** Julia is the first tester to reach
  Pomona never having talked to one: she finished setup, landed on Today,
  and had no idea what she was allowed to say. Three parts, all teaching the
  same thing — the ask bar IS the app and the buttons are its shortcuts.
  (1) **Two tappable example prompts under the ask bar, per tab**, on that
  tab's first three visits and then gone (`COACH_EXAMPLES`,
  `coachOnTabShown`, `renderAskExamples` in `static/shell.js`; a new
  `#ask-examples` row in the mobile dock and `#today-ask-examples` above the
  desktop Ask column's input). They are `.ask-chip`, the component the
  quick-action chips already use, and tapping one goes through
  `openAskSheet()` + `sendAskMessage()` — the existing path, not a second
  one. (2) **One how-and-why card on Today**, eyebrow "A quick word", shown
  when the household has a plan and has never dismissed it; a
  `.plan-nudge-card` instance, no apricot (the hero owns it), two quiet
  `.plan-nudge-link` outs. (3) **A "Helpful tips" sheet** — eight lines,
  four tab groups with one example each plus what happens after you send —
  behind a Preferences row and a small "?" beside the ask bar at both
  widths; it reuses `#prefs-sheet`'s own CSS rules rather than restating
  them. **Where the state lives, and why the two differ:** the per-tab visit
  counters are localStorage keyed per household (a per-device teaching aid;
  a lost count costs one chip), but the card's dismissal is a new
  `households.coaching_seen_at` column behind `GET /api/coaching` /
  `POST /api/coaching/seen` — being handed "here's how this works" again on
  the phone after reading it on the laptop is the opposite of being coached.
  The write is write-once, so "when did they first see it" stays answerable.
  Two traps worth remembering: `.ask-chips` is `display:flex`, which beats
  the bare `[hidden]` attribute (the same trap `#reveal-days` hit on the
  onboarding branch — fixed for the existing quick-action containers too),
  and `coachOnTabShown` is hoisted and called from `activateTab` hundreds of
  lines above its own `var coachState`, so it guards against being called
  before that runs. `tests/test_coaching.py` is the guard, 22 tests (the
  front-end half RUNS shell.js's own functions under node against a small
  DOM stub rather than reading the source for markers); 1713 total. One
  existing assertion in `tests/test_feedback_reports.py` widened its source
  window from 1400 to 2200 chars — the new Preferences row pushed
  `snwTile()` down inside the same renderer; the assertion itself is
  unchanged. **Not verified in a browser** — no browser tooling in this
  session, so the examples row's wrap in the dock and the tips sheet's
  layout at 390px are unchecked.
- **2026-09-08 — Setup ends on a receipt, not on a shrug. Branch
  `onboarding-done-receipt`.** The reveal used to end on "Looks good — take
  me in" over a list of days: nothing said setup was finished, nothing said
  what had been saved, and nothing named a next step (Emily + Julia). It
  now ends on the same block Meals shows when a week is approved — eyebrow
  "YOU'RE SET UP", title "That's everything I need.", then three lines in
  one fixed order: what was saved ("2 of you, 1 allergy, dinner between 6
  and 8, prep on Sunday and Wednesday."), what Pomona did ("Your first week
  is drafted: 16 meals, 5 cooks.") and the one next step ("Next: look it
  over and approve it, then I'll write your list."). Every clause is built
  from an answer actually given — a fact nobody gave ("all over the place"
  as a dinner window, no prep days) is left out of the sentence rather than
  padded into it — and the counts follow `weekly_plan._is_cook`'s rule so
  the reveal and the approved-week receipt can't put different numbers on
  the same week: a reheat or takeout night is a meal but not a cook, an
  away or open slot is neither. **Numbers are digits here** (Emily), not
  the words-to-twelve rule `_receipt_number` follows — flagged because it
  is a deliberate split between the two receipts, and it is one function
  (`revealSetupLine`/`revealPlanLine`) to reverse. The one apricot is
  "Review my week" and it lands on the DRAFT, not on the plain week:
  `/week?drafted=<the plan's own Monday>`, the existing hand-back
  `/plan-week` already uses, because a household that chose "Next week" (or
  a Sunday one folding forward) has its first plan filed under a Monday
  that isn't this one, and Meals otherwise opens on whichever week contains
  today — the same failure class as the `?drafted` bug this param was added
  for. `?firstplan=1` and its toast are gone from onboarding; the receipt
  says more, and `?drafted`'s own arrival line names the same next step.
  A **failed or empty** generation gets the same block with the celadon
  taken off it and no counts on it — "Your answers are saved." / "The first
  week didn't come together — tap Try again, or I'll draft it when you open
  the app." — with the existing Try again pair below; a generation that
  finished with zero meals now takes that path too, which retired
  `renderRevealDays`'s own "No meals generated yet… ask in chat" box (two
  different sentences for one piece of news, one of them pointing away from
  the retry button sitting right there). Not repeated anywhere later:
  Preferences already holds the answers. `tests/
  test_onboarding_done_receipt.py` is the guard, 15 tests (the three line
  builders run for real under node, the rest source-level); 1706 total.
- **2026-09-08 — A recipe ingredient has two amounts now: one for the
  shop, one for the pan. Branch `recipe-quantities-measured`.** Julia
  (first beta tester): "The recipe quantities are not specific enough.
  It's saying stuff like 'one bottle olive oil' which is incorrect. It
  should give actual measurements in the cooking view." **Root cause: the
  app asked for it.** `generate_weekly_plan_llm`'s ingredient bullet
  (agent.py, the "write each ingredient's qty as how it's actually bought
  at the store" bullet) tells the model to write shopping-shaped
  quantities because that string IS the grocery line —
  `recipes._add_recipe_ingredients_for_entries` reads it and
  `quantities._PACKAGE_UNITS` buys one bottle for the whole week however
  many dinners name it. Correct for the list. `get_cooker_view` then
  showed the same string to the cook, and `scale_recipe` halved it to
  "0.5 bottles". Neither the packaging step nor a rounding helper wrote
  anything back into the recipe; nothing was ever converted, which is the
  bug.
  **The fix is a split, not a rewrite.** An ingredient dict may now carry
  `cook_qty` beside `qty` (inside `ingredients_json` — no migration).
  `recipes.validate_measured_quantities` is the rule (package words
  rejected; a can kept only for a canned good with a size; a bare count
  only for something countable; "to taste" allowed), and
  `recipes.COOKING_QUANTITIES_PER_4` — 176 everyday items, per four
  servings, plus seven class defaults and a per-package-word last resort —
  is the deterministic fallback, so "1 bottle olive oil" becomes "2 tbsp"
  offline, in a test, every time. `cooking_ingredients` is a presentation
  pass at the END of `get_cooker_view` (after batch/chain/attendance
  scaling) and the first step of `scale_recipe`; **the grocery path was
  not touched and the list still says "1 bottle"**, which is right.
  `fill_in_recipe` asks for measured amounts, validates them, makes ONE
  repair call for only the offending lines, then falls to the table.
  **Cost delta (chars/4 estimate — no working API key here, see the effort
  note in agent.py):** the fill call's prompt names only the ingredients
  that actually fail the validator, and the `cooking_quantities` schema
  property is attached only for those recipes, so a recipe already written
  in measurements pays **+10.3% input / +13.4% call (+$0.001)** and the
  reported case pays **+31.9% input / +35.8% call (+$0.0025)**. That is
  over the ~10%-per-call budget this work was given, and deliberately: the
  overage is the measured lines themselves plus the richer steps
  (temperature/time/doneness cue) that answer the second half of Julia's
  report. Absolute impact against the $1/household/month target is a
  fraction of a cent per fill, and a fill is a button press on a recipe
  with no instructions, not a per-week cost. The repair call is its own
  ledger row (`generate_recipe_detail_llm.repair`) so its real frequency
  is measurable rather than guessed.
  Also added: `recipes.check_steps_ingredients_consistency` (an ingredient
  no step uses; a step naming something the list never bought) as a
  log-only `steps_match_ingredients` **info** note in `plan_quality` and
  at fill time. It uses the measurement table's own keys as its food
  vocabulary and passes over words it doesn't know — a false "you forgot
  to buy shallots" is worse than a missed one.

- **2026-09-08 — Onboarding, second pass: Julia's beta feedback, and the
  "asked for next week, planned this week" bug. Branch
  `onboarding-copy-v2`.** Julia is the first beta tester to go through
  onboarding cold. **Copy** (Emily's exact strings): the plan-ready day is
  "When do you want the meal plan for the week ready?", prep days opens
  "Do you like meal prepping?", restrictions is "Dietary preferences or
  restrictions", won't-eat is "Anything I should never recommend?", and
  the counts step asks per meal type ("How many different breakfasts do
  you want?", ×3) instead of one generic "How many different recipes a
  week?". The typical-week step lost its why-panel, its five-bullet
  "worth mentioning" list and its second explanatory paragraph — one line,
  one short example. **Chips before typing**: eating-style and won't-eat
  were bare text boxes and now lead with presets, keeping the box as an
  optional "Anything else?". **A household of exactly one adult** is no
  longer asked which meals it eats together or who cooks — two questions
  with one possible answer each. Both are filled in (`most_meals`,
  `one_person` + that person) and saved through the ordinary path, so What
  we know reads them back as answers, which is what they are; a second
  person of any age group makes both questions real again and clears them.
  **Snacks are per DAY** now (chips 0/1/2/3, default 2): new
  `meal_preferences.snacks_per_day` + `snacks_per_day_set`, its own
  answered-flag for the same reason `snacks_per_week_set` exists (both
  columns are NOT NULL DEFAULT). `snacks_per_week` is still written
  alongside it and still means DISTINCT snack recipes, so the conversion is
  **capped at 7** (`preferences.snacks_per_week_from_per_day`) rather than
  the literal ×7 — that column is documented and validated 0-7 in five
  places and writing 14 into it would make every one of those readers
  wrong, not keep them in step. The precise answer is `snacks_per_day`,
  which is what the planner reads. **The reveal** shows nothing
  menu-shaped until the first real day arrives: `#reveal-days` ships empty
  AND `hidden` (with its own `[hidden]` rule, since `display:flex` beats
  the attribute), and the title only says "Here's your sample week" once
  there is one — until then it is the hero plus the shared waiting lines.
  **The next-week bug, root-caused:** onboarding never asked which week
  the first plan was for. Its own copy had been talking about the week
  ahead for two steps running ("your week starts the next morning";
  "anything already on the calendar?") while both first-plan endpoints
  computed the current calendar week from `today.weekday()` and ignored
  the household's `planning_anchor` entirely — and the STREAMING one (the
  route the reveal actually calls) also had none of the part-week or
  Sunday fold-forward logic its plain twin's docstring spends four
  paragraphs describing, so a Wednesday household's reveal showed Monday
  and Tuesday. Both now go through one `main._first_plan_window`, which
  takes the period from `tools.suggest_planning_period` (i.e. from the
  plan-ready day the household just gave) and shifts it forward one whole
  period when they picked "Next week" on the new start chips. Expressed as
  `period_start` rather than `skip_days` — the two are the same statement
  and only `period_start` can travel through `_stream_week_generation`. A
  household that never answered the anchor gets 'sunday' from that
  function, i.e. the Monday week, so nothing changes for them and every
  existing week-key/part-week test is untouched. `tests/
  test_onboarding_copy_v2.py` is the guard, 34 tests; 1551 total.
- **2026-09-08 — "The chat said it changed a snack and it didn't change it
  in the meal plan" was the SCREEN, not the swap. Branch
  `snack-swap-applies`.** Julia, first beta tester. Root cause:
  `weekly_plan.get_week_menu` — the one backend ask behind the Meals
  screen — built its days from `slots = ("breakfast", "lunch", "dinner")`
  and dropped every `slot='snack'` row on the floor. The swap tool always
  accepted `slot='snack'` (nothing in `swap_meal_in_plan` ever checked the
  slot), the chat schema always offered it, the action card always routed
  to `week` with the right date/slot — the write landed and had nowhere to
  appear, so the app told the household about a change they could not see.
  `WEEK_SLOTS` is the 21-slot GUARANTEE, not the list of slots a day HAS;
  reading it as the latter is what caused this, so `DAY_SLOTS` now exists
  beside it and `get_week_menu`/`_build_day_based_menu` return a `snacks`
  list (plus `snack`, the first of them) per day. **shell.js still
  hard-codes its own `WEEK_SLOTS` of three and will not draw them until it
  reads `day.snacks`** — payload is additive, so nothing breaks meanwhile.
  Three more things in the same branch: `swap_meal_in_plan` takes
  `old_meal` (a day has two snacks; a swap about one of them must not
  delete both) and refuses a slot that isn't one; a reply claiming a
  change on a turn where no write tool succeeded is replaced with
  `agent.CHANGE_CLAIM_RETRACTION` (`verify_change_claim` — nothing in the
  loop had ever checked that "I've swapped that" was true); and snacks
  default to TWO DIFFERENT ones a day
  (`preferences.resolve_snacks_per_day` — `snacks_per_day` if the
  household was asked, else an EXPLICIT `snacks_per_week` spread over
  seven and floored at one, else 2, because `snacks_per_week`'s stored 3
  is a column default nobody answered). Julia's other report — the same
  food for breakfast and for the snack that day — is a `plan_quality` rule
  (`snack_echoes_a_meal` / `snacks_distinct_per_day`, name-stem match) and
  the one thing in that module that REPAIRS rather than logs:
  `repair_snack_clashes` trades the offending snack onto a day it fits, or
  failing that gives its slot to another day's snack. Only ever the week's
  own snacks — a replacement invented from a hard-coded list would have
  been through none of the restriction/dislike/allergy handling that
  generation applies, and "we fixed your repetitive snack by giving you
  one you're allergic to" is the worse bug.

- **2026-09-08 — Swap is one small call now, not a chat turn. Branch
  `swap-one-meal-in-place`.** Julia (first beta tester): "be able to click
  on the one recipe and meal that the user wants to switch and then have it
  regenerate just the one on the spot." Meals' "Swap" used to open the ask
  sheet with a prefilled sentence and spend a whole chat turn. It is now
  `POST /api/week/{week_start}/swap-in-place {entry_id, avoid?}` →
  `app/tools/swap_in_place.py`, one forced `submit_swap` tool call at the
  `utility` effort route, priced in the api_calls ledger under the new call
  site **`swap_in_place`**. Four things worth knowing before changing it:
  (1) **the pick is applied through the existing `swap_meal_in_plan`**, so
  leftover chains, groceries-only-on-approval, plate sides and the shared
  `taste_verdict` behave exactly as a chat swap — this module deliberately
  owns no second swap implementation, and the two fields plan_meal can't be
  told about from in there (`reasoning`, `derived_from`) are written
  straight after; (2) **the allergen check runs on the pick BEFORE anything
  is written**, through a new `coordination.check_meal_conflicts` — the
  per-dish half of `check_plan_conflicts`, pulled out so both use one
  matcher rather than two that can disagree. A hard clash costs exactly one
  retry with that dish added to `avoid`, then a plain refusal and nothing
  saved. Checking after applying would have meant reversing a swap the
  household never asked for, dragging the grocery list and any chain
  through it; (3) **`derived_from.swapped_from` is written once** — a
  second swap carries the ORIGINAL forward, so Undo means "put back what
  was there before I started tapping", not "step back one dish"
  (`POST .../swap-undo`, also through `swap_meal_in_plan`); (4) the
  outgoing dish is on `avoid` from the first call and stays on the list
  handed back, which is what the screen sends as the next tap's `avoid`.
  Front end, `static/shell.js` Day and Meal steps only: `swapLineHtml` is
  one quiet line under a slot's actions saying whichever of three things is
  true — "Tell me what instead" (the old ask-sheet path, same prefill,
  `openAskSheet` itself untouched), "Finding something else…" while the
  call is out, then the model's one-line reason plus an Undo chip for 8s.
  No second apricot (Rule 5). **Cost measured off the call's own shape:**
  ~1,450 input tokens (≈700 instructions, cached after the first swap of a
  session; ≈390 tool schema; ≈350 household context) and ~550 output, so
  **≈$0.009 for the first swap and ≈$0.006 warm**, roughly double if the
  allergen retry fires — against a chat turn's ~16k-token context for the
  same edit. Left out honestly: `plan_quality.check_and_log` doesn't run on
  a swapped slot (it is a whole-week rule engine and log-only), and an
  undo restores the dish but not a leftover chain the swap broke — that is
  `swap_meal_in_plan`'s pre-existing behaviour, shared with every chat
  swap, not something this path adds.
- **2026-09-08 — Kitchen is the cook's tab, and everything the app knows
  about the household is a sheet. Branch
  `flows-4-kitchen-and-preferences`.** Emily's approved design. KITCHEN
  answers "what's cooking, and what's in the house?": title, a subtitle
  saying the day and the count ("Monday · 1 cook tonight" — "tonight" only
  while every cook left today is a dinner), then **Cooking today** (one
  line per cook or reheat, "start by 5:35 · 55 min" read off
  `/api/today/moves` so Today and Kitchen cannot disagree about when to
  start, a Cook / cooked / Reheat / eaten badge, a tick, and the dish name
  opening cook mode), **Prep sessions** (`cookPrepSessionsHtml`, moved
  unchanged), **The rest of the week** (one line per remaining cook day,
  collapsed after three behind "+ N more cooks"), and two quiet tiles,
  Inventory and Recipes. **Cook mode is a STEP of this tab**, not a state
  of Meals: `cookPanel()` returns the Kitchen panel, the focused screen
  renders into `#kit-cook-view`, and the back link is "‹ Kitchen" (up a
  level by name, never `history.back()` — the same rule Meals' steps
  follow). Every entry point now passes `activateTab('kitchen', true,
  {cookFocus})` — Today's moves (`runTodayMoveAction`, and `moves.py`'s own
  action target, which used to name `{tab: "week", mealsView: "cook"}`),
  Meals' Day/Meal "Cook this", and Grocery's shop-done handoff — resolved
  by the unchanged `cookResolveFocusIndex` (id → date+slot → title → the
  root, never a wrong meal). `setMealsView`, `loadCook` and
  `refreshCookView` are gone: `kitchenEnterCook`, `loadKitchen` and
  `refreshKitchenPanel` do those jobs, and `loadKitchen` is one
  `Promise.all` over cooker-view + attention + today's moves — **no new
  route**, because the moves payload already carries the start-by
  arithmetic. **The rule that changed:** DESIGN_SYSTEM §6/Rule 5 said
  Kitchen has no primary action at all. It still has none on its ROOT; cook
  mode's "Mark it cooked" is the tab's apricot, one step deeper.
  **PREFERENCES** is a bottom sheet behind `prefsGearHtml()`, rendered in
  the header of all four roots and hidden on every deeper step (Meals' Day
  and Meal, Grocery's shopping mode, and Kitchen's cook mode, which
  replaces the root outright). Five rows read the household back plainly
  from ONE cached `/api/memory` per open (Who's here · Your rhythm · Prep
  days · How you eat · Stores), each opening the What we know tab that owns
  that answer through the existing Kitchen entry sheet; then "Something not
  working?" (`snwTile()`, the same component, moved off Kitchen) and "Sign
  out" (a one-line `confirm`, then `GET /logout`). The chat refresher's
  href branch feeds `prefsInvalidate()` now instead of the Kitchen hub.
  **Removed, and where each piece went:** the "what we know" hero and its
  four count chips (Preferences), the "Worth doing sometime" card (nothing
  behind it was ever built), the Cook overview's spruce "Tonight" hero
  (`cookHeroHtml` — today's cooks are lines, and the "for 6" batch chip and
  covers-note live on the focused screen, which is where the ingredients
  are actually read off), its "This week / N of M cooked" title row, and
  its "Prep schedule" two-up rail (`cookPrepHtml` — its rows are Today's
  fridge/prep moves, its prep-cut exclusion moved into
  `cookFocusPrepTasks`, and its done-count note and hands-free mic moved
  into `cookFocusPrepHtml`, so the voice feature keeps both entry points).
  Four source-marker tests were updated honestly rather than deleted, each
  with a note saying what moved: `test_prep_days` (two), `test_leftovers_batch`
  (its node-run hero tests now render the Kitchen line;
  the "for 6" chip is the one thing they no longer cover — `cookFocusHtml`
  is not a pure function), `test_feedback_reports` (the tile renders in
  Preferences; `kit-hero-error` went with the hero) and
  `test_meals_week_day_meal` (`cookFocus` for `mealsFocus`).
  `tests/test_kitchen_and_preferences.py` is the new guard, 31 tests.
  **Left out honestly:** there is no Recipes page in this repo, so the
  Recipes tile opens the ask bar on "What recipes do we have saved?", which
  the assistant answers off `list_recipes` — it does not pretend to be a
  browser. "The rest of the week" shows the days AHEAD only; a cook that
  was missed on a past day is no longer tickable from Kitchen. And the
  browser check was run in **jsdom** driving the real `shell.js` against a
  real uvicorn on a seeded throwaway DB (every flow above verified, console
  clean) — not in a real browser: the Chrome extension was not connected
  and no Playwright browsers are installed in this environment, so the
  390px and desktop LAYOUTS are unverified.
- **2026-09-08 — Five things the Kitchen/Preferences slice got wrong, on
  the same branch (`flows-4-kitchen-and-preferences`).** Found on review of
  the entry above, fixed in one pass. (1) **Preferences invented a fact.**
  `meal_preferences.snacks_per_week` is `NOT NULL DEFAULT 3`, so nothing in
  the row could tell "they said three" from "we assumed three" — "How you
  eat" duly told a brand-new household it eats *3 snacks a week*. New
  column `snacks_per_week_set` (schema.sql + db.py `_MIGRATIONS`), written
  only when an EXPLICIT `snacks_per_week` reaches
  `preferences.set_household_meal_preferences`, cleared by
  `delete_preference`, surfaced as `snacks_per_week_set` on
  `get_household_memory`, read by `prefsEatingLine`. Existing households
  are backfilled from `preference_events`
  (`snacks_per_week` / `onboarding_meals_per_week`), never from the number
  itself — `db._backfill_snacks_per_week_set`. The other four rows were
  audited and were already honest: members, `usual_stores` and the rhythm
  facts are empty/NULL until answered. Their five empty-state lines now all
  read **"Not set yet"** (the people row used to say "Nobody on record
  yet"). (2) **`cookFocusPrepTasks` orphaned general prep.** A `prep_tasks`
  row with no `meal_plan_entry_id`, no name-matching `related_meal` and a
  date that is not a prep day ("Soak the beans", +2d) rendered NOWHERE —
  not in a session, not on a cook screen, and Today only shows today. The
  Kitchen root now carries **"Prep to do"** directly under Prep sessions
  (`kitchenLoosePrepTasks` / `kitchenPrepTodoHtml`): every *pending* task no
  session and no cook screen already shows, dated, with a tick. It is a
  net, not a third list — nothing appears in two places. (3) **"Show me
  tomorrow" didn't show tomorrow.** The rating toast's action went to the
  Kitchen root with no focus, which shows tomorrow only when tomorrow
  happens to be a prep-session day. `cookShowTomorrow` opens tomorrow's
  first cook in slot order (`cookTomorrowFocusTarget`, reheats skipped —
  they have no cook screen) and otherwise lands on the root scrolled onto
  the prep; the action is offered when tomorrow has a cook OR prep
  (`cookTomorrowHasSomethingToShow`). (4) Four nits: a check-off now
  re-reads `/api/today/moves` into `kitchenState.moves`
  (`refreshKitchenMoves`, off `refreshPlanSurfacesAfterCook`) and a cooked
  row drops its start-by chip; the subtitle says "1 cook **left** tonight"
  / "nothing left to cook today" once anything today is ticked, with done
  read from the plan row and the move together; cook mode's apricot says
  **"Mark it cooked"**, the same words as the end-of-recipe button (the
  root's checkbox `aria-label` stays "Mark cooked" — `cookCheckMeal` reads
  that exact string); and `runTodayMoveAction` translates a stale cached
  `{tab:'week', mealsView:'cook', mealsFocus}` target into a `cookFocus`
  instead of dropping the tap on the plan. `tests/test_kitchen_and_
  preferences.py` grew 21 tests (31 -> 52), most of them running the
  screen's own functions under node rather than reading the source for a
  marker; `test_prep_days`'s section-order marker was updated honestly for
  the new "Prep to do" line. Suite 1474 -> 1495.
- **2026-09-08 — "The ask bar can do it" is not the same as "a person should
  spend a model turn on it" — three Grocery row actions came back. Same
  branch `flows-5-grocery-sort-step`, verifier pass.** The entry below says
  item management is the ask bar's job now; that was too broad and this
  corrects it rather than quietly changing the code under it. Back on LIST,
  all `static/shell.js`: (1) a quiet per-row ⋯ (`groRowMenuHtml`, 44px hit
  area, inline under the row, no apricot) with the old menu's verbs on the
  old routes — quantity via `/update` (the old `save-row`/`fix-qty`), store
  via `/store` with the pills plus "Any" (the old `move`/`not-this-time`: an
  empty store, which does NOT forget the remembered item→store preference)
  and "Somewhere else" (`/exclude`), and `/remove` with an Undo that re-adds
  the line, since `remove_grocery_item` is a hard delete and the undo can
  only give back the line, not the row id; (2) an inline add row in LIST's
  foot (`groAddItem`, spruce not apricot, Enter or the button) posting
  straight to `/api/grocery-list/add` exactly as `groHandleVoiceCommand`
  does — an item added with no store lands in the TO SORT count; the ask bar
  stays for anything wordier and `ASK_HINTS.grocery` still says so; (3)
  Review's duplicate detection, MOVED not rewritten out of `groReviewHtml`
  (`groDuplicateGroups`, same trimmed-lowercased key) and said as one quiet
  line above the store cards — "Two rows of spinach · Merge" — running the
  old `merge` handler, confirm included. Four fixes rode along: `groListHtml`
  concatenated the `unsorted` ARRAY into copy ("[object Object],…") where it
  meant `.length`; `groSetScreen` sent the receipt's "open the list" to SORT
  whenever anything was unsorted and now always lands on LIST (the badge is
  the way into SORT); the whole-trip toast said "Stop saved", the per-stop
  line, and now says "Trip finished — 21 things home." off `tripBought`; and
  two comments quoting a string `tests/test_flows_3_review_and_receipt.py`
  asserts is gone were reworded, which is what made the suite red after the
  main merge. Guarded by six more tests in `tests/test_grocery_steps.py`
  (22 there now), and driven headlessly against the real API on a throwaway
  DB (36 assertions: every verb actually wrote what it claims). **Still not
  verified in a browser** — no browser tooling in this session either, so the
  ⋯ menu's and the add row's LAYOUT at 390px is unchecked.
- **2026-09-08 — Grocery is four steps, not three segments. Branch
  `flows-5-grocery-sort-step`.** Emily's approved design: the tab answers
  "what do we need, and where?" as **LIST -> SORT -> TRIP -> WRAP UP**, four
  states of the same tab at `/grocery` throughout, copied off Meals'
  `goMealsStep`/`pushMealsStepHistory` pair (now `goGroceryStep` /
  `pushGroceryStepHistory`, wired into the shell's one popstate listener; a
  refresh lands on LIST). LIST is one card per store ("Costco · 14", four
  things then "+ 10 more"), a subtitle saying the shape of the shop
  ("23 things · 2 stops"), an apricot "3 TO SORT" badge in the head that is
  the only way into SORT, and one apricot "Start the trip" plus a quiet "Add
  something" that opens the ask sheet prefilled `"Add "`. SORT is ONE
  unsorted thing at a time with the existing chips and their existing
  semantics (store pills / Any / Have it / Somewhere else = `/exclude`),
  a "2 of 3" progress line, and an auto-return to LIST with "All sorted." on
  the last choice. TRIP is one stop at a time — stops **snapshotted** at
  "Start the trip" so finishing one can't renumber the rest, "Any" things
  riding with the first stop, nothing from another store on screen, the
  existing collapsed "In your cart · N" group with its put-back, and "Done at
  Costco → Metro" / "Done shopping"; "‹ Pause the trip" keeps the trip.
  WRAP UP is what didn't make it in ("Couldn't find it" / "Somewhere else"
  per row), the Review segment's confirmation half kept whole ("Already
  sorted this week" + both undos), a per-trip "Bought 21 of 23", and
  "Finish the trip". **Left the root, all `static/shell.js`:** the three-way
  segmented control, the spruce trip hero and its progress wheel, the per-row
  ⋯ menu (edit quantity/category/store, Remove), the inline "Add an item"
  card, the store-bucket rows with move/not-this-time, the Done group on To
  buy, and Review's three flag cards (missing quantity, no store, possible
  duplicate + Merge). Where they went: item management is the ask bar's job
  now, which is what "Add something" opens and why `ASK_HINTS.grocery` is
  "Add oat milk and lemons…"; everything else was a question the four steps
  already answer. **Three things worth knowing before changing it:** (1) no
  backend change at all — every step reuses the existing
  `/api/grocery-list*` routes and the needed/in_cart/purchased/excluded
  statuses, and `groSetScreen` survives only as a shim mapping the old
  `opts.groScreen: 'plan'` (the approved-week receipt, its toast twin, the
  ask sheet's chip) onto LIST-or-SORT, so nothing outside the Grocery region
  had to be touched; (2) "Bought N of M" counts THIS trip
  (`tripBought`/`tripTotal`), never `groTotals().done`, which sums purchased
  rows over the household's lifetime — the same trap `justFinishedTrip`
  already existed to avoid; (3) `anyStoreIds` keeps its **known limit** — an
  "Any" choice is client-side and page-view only, so a reload puts that thing
  back in the to-sort queue. Two DESIGN_SYSTEM.md pointers were corrected in
  the same change, honestly rather than quietly: Rule 4 now reads "at most
  one hero, not exactly one" (Grocery's hero and Meals' day hero are both
  gone), and §5's segmented-control row points at `.meals-seg` alone.
  `tests/test_grocery_steps.py` is the new guard (16 source markers);
  `test_frontend_restored_2026_09_08.py`'s "Somewhere else" and
  `groStoresPromptHtml` markers still pass untouched. **Not verified in a
  browser** — no browser tooling was reachable in that session; the whole
  flow was driven headlessly instead (the region evaluated against the real
  API on a seeded throwaway DB, 44 assertions), so the LAYOUT at 390px and on
  desktop is the one thing still unchecked.
- **2026-09-08 — Review IS the week card; approval ends in one receipt.
  Branch `flows-3-review-and-receipt`.** Emily's approved design, the third
  of the three flows: on the new Week root the draft review band was ~410px
  and, after approval, the receipt plus two nudge cards filled a phone
  viewport, so the seven-row card — the answer the screen exists to give —
  started below the fold in both states. **DRAFT** is now the week itself:
  the DRAFT badge, a subtitle that says "a draft, your turn" instead of the
  shape of the week, the rows as the review (tap a row, Day, Swap, all as
  built), and under the card one apricot "Approve this week" plus a quiet
  "Tweak it with me" (`weekDecideHtml`). Above the card, only for a HARD
  allergen clash, one urgent-tint "One thing to settle" card
  (`renderWeekSettle`) with "Swap the <dish>" (straight to that Meal step)
  and "Keep it anyway" — which is deliberately the ORDINARY approve path,
  so `approve_weekly_plan` still answers `needs_confirmation` and the
  second explicit tap is still what the backend waits for
  (`showApproveConfirm` now works on `.wk-decide` and scrolls the button
  into view). A soft conflict gets no card ever: one line under the card
  (`weekNotesHtml`), where the once-ever plates note went too. **SET** shows
  a celadon receipt — an eyebrow, one counted sentence and one thaw line —
  plus "Two quick ones before you go", the freezer check and the cook-ahead
  offer folded from two full cards into two LINES with an "Ask" that expands
  each ask's existing chip UI in place. Same state, same `asked_at` gates,
  same `/defrost-confirm` and `/cook-ahead-confirm`; only the frame changed.
  Two segments close it: apricot "Open the list" (Grocery → Plan stops) and
  "See the week", which **dismisses the receipt via `sessionStorage`, keyed
  by `weekly_plan_id`** (`pomona.weekReceiptDismissed.<id>`) — it has to
  survive a tab switch, since the panel re-renders every time Meals comes
  back, but not a new session, since a week approved yesterday should open
  on the card. Nothing about it is the server's business, which is why it
  isn't a column; the Cook view's two re-ask links clear it. **Removed:**
  `#week-review-band` and `renderWeekReviewBand` outright, with
  `groceryPromiseText` (the promise line), `receiptBodyText` (the long "All
  set. I've put N items…" paragraph), `approvedAtLabel` (the APPROVED BY
  eyebrow), the "your list is ready" handoff and its dismissal map, the
  "Not now" on the freezer ask, and the review band's Try again / Change my
  answers pair (already in the More sheet since flows 2 — only the
  `#week-redo-waiting` line moved, onto the page under the card, since the
  sheet closes the moment you tap). "Reopen the week" and "Adjust your
  setup" left the receipt for the More sheet with every other rare action.
  **New on the server, deliberately, because copy that counts things must
  not drift from the things it counts** (and shell.js has no JS test
  harness): `weekly_plan.week_receipt(days, plan_id)` returns
  meals/cooks/list_count/thaw_count plus the two sentences — a reheat night
  is a meal but not a cook and an away night is neither (same rule as the
  card's own subtitle), the list is the `needed` view the Grocery tab
  opens on and an empty one says "nothing left to buy" rather than
  promising a list of nothing, numbers run one-to-twelve as words and
  digits above (Emily), and the thaw line is either "<N> things to move to
  the fridge this week." or "Nothing to thaw before <the plan's next cook
  day>." — the free-until fact, dropping to "this week" when the week has
  no cook left to name. `coordination._settle` / `_soft_note` write the two
  clash sentences beside the data; "is allergic to" is only ever said when
  the restriction actually says allergy, otherwise "can't have". Both ride
  in `get_week_menu` (`receipt` for an approved week, `settle`/`soft_note`
  for a draft only). `tests/test_flows_3_review_and_receipt.py` is the new
  guard (40 tests); `test_the_draft_review_band_still_renders_above_the_card`
  in `tests/test_meals_week_day_meal.py` was INVERTED rather than deleted
  and renamed to say so — flows 2's own entry said "flows 3 replaces the
  band", and this is that. One bug found only in the browser: an author
  `display` beats the UA sheet's `[hidden]`, so both asks rendered open
  until `.wk-quick-body[hidden] { display: none; }` went in. One thing left
  alone: `approveWeek`'s who's-approving step is unreachable today because
  `get_week_menu` only fills `other_adults` once a plan HAS an approver, so
  a draft always sees an empty list — pre-existing, untouched here, and
  worth a ticket of its own rather than a change smuggled into a design
  slice.
- **2026-09-08 — Meals is three steps, not one stack. Branch
  `flows-2-meals-week-day-meal`.** Emily's approved design: the Meals PLAN
  state answers "what are we eating this week, and is it settled?" as
  **WEEK -> DAY -> MEAL**, all three states of the same tab (like Plan/Cook,
  never routes — `/week` throughout). WEEK is one card of seven rows, three
  dot-prefixed lines each (apricot = a cook, celadon = made ahead, grey =
  out/none, apricot outline = "Pick a lunch"), names truncated to ONE line
  (Emily: seven days truncated beats five in full), today's row tinted
  celadon, a SET/DRAFT/NOTHING YET badge in the header and a subtitle saying
  the shape of the week ("Sep 7–13 · 4 cooks, 3 made ahead"). DAY is three
  EQUAL cards — no hero, because a day is three meals and the old dinner
  hero said otherwise — each with an eyebrow carrying the household's real
  slot hour ("Dinner · 6:30", from `get_week_menu`'s new `slot_times`, which
  reads moves.py's mapping so Today and Meals cannot disagree) or, for a
  made-ahead night, "made ahead Monday". MEAL is one meal: "The plate"
  (components as chips plus "Protein, carb, veg. Nothing to thaw."), the
  Cook view's own cook-ahead picker reused verbatim (`cookAheadHtml` +
  `cookSetCookAhead`, re-inked for an ivory card — its own rules are written
  for the spruce cook hero), "Why this night", and the screen's one apricot
  "Cook this". **Left the root, all in `static/shell.js`:** the framing
  line, the day rail, the day card (dinner hero + sides tiles), the whole-
  week row, the "or start over" reset link, the Plan-a-week card, the
  standing setup link, the open-slot cards, the desktop `#week-header` paper
  menu and the desktop 7x3 grid (`wg2*`). Where each went: the seven-row
  card IS the whole-week overview at every width, which is why the grid came
  out rather than sitting under a card saying the same thing; the open-slot
  resolver moved inside the day it belongs to, revealed by "Pick"; and every
  rare action — Re-plan this week, Pick my own days (the existing picker,
  moved unchanged), Try again, Change my answers, Adjust your setup, Start
  over, plus See the whole week so the share link keeps an entry point —
  moved into a new "More" bottom sheet. **Kept exactly as they were:** the
  draft review band and the approved receipt (with its freezer check and
  cook-ahead ask), now rendered above the card and hidden on the deeper
  steps; flows 3 replaces the band. Three judgment calls worth knowing: (1)
  "Cook this" is now offered on ANY non-past day, not only today — the
  old restriction ("cook mode can only start tonight's meal") predates
  `42d422a`'s entry-id focus target, which lands on the exact meal; (2)
  back LINKS go up a level by name and never call `history.back()`, because
  after any wandering the previous entry is not the parent and a link
  saying "This week" must not land on a meal — the back GESTURE keeps
  history's own meaning through the one popstate listener, and a refresh
  lands on WEEK; (3) `#week-review-band` was missing from the gutter list in
  `shell.css`, so a draft's Approve button had always sat flush to the
  screen edge — invisible above a day rail that started at the edge too,
  obvious above a card that doesn't. Payload additions, all read and never
  guessed: `slot_times`, per-entry `food_groups`, `defrost` (the plan's own
  `prep_tasks` row, the one Today's fridge move ticks) and `leftover_from`
  (source dish + date + `cook_ahead`, kept apart from the headline built out
  of them). `tests/test_meals_week_day_meal.py` is the new guard; two
  markers in `tests/test_frontend_restored_2026_09_08.py` were updated
  honestly rather than deleted (the desktop grid's chip class, and the
  reheat test which widened from dinner-only to all three slots), and its
  line-count floor was raised to 8600 per its own instruction.
- **2026-09-08 — Today is a ranked timeline of moves, not a stack of
  cards. Branch `flows-1-today-next-up`.** Emily's approved design: the
  screen answers "what's next for us?" with exactly two blocks — one
  compact spruce **Next up** card carrying a single action, and **The rest
  of today**, a list of every other move with a round tick, done items
  fading into a "Done today" group at the bottom. New `app/tools/moves.py`
  is where that lives: `moves_for_day(date, now=None)` turns the day's
  cooks, reheats (a cooker-view card with `is_leftovers`), fridge moves
  (`task_type='defrost'`), other prep and a shop run into one shape with a
  window and a weight, and `today_moves()` ranks them — **open now or
  within 4h, highest weight first, then earliest `window_start`**, with
  reheats never featured (Emily: made-ahead food "is a line, never the
  card"). Two endpoints, `GET /api/today/moves` and
  `POST /api/today/moves/{id}/done`. Three things about it worth knowing
  before changing it: (1) there is **no moves table** — `done` is derived
  from `cooked_status`/`prep_tasks.status` and a tick dispatches to
  `check_off_meal`/`check_off_prep_step`, so Today and Cook cannot
  disagree; (2) the shop move's window deliberately opens at **00:00**,
  which is what makes it beat the dinner it is for on the equal-weight
  tie-break, and it is never "done" — it stops existing when the list
  empties; (3) dinner's hour comes from the `dinner_window` rhythm fact
  through `defrost._DINNER_CLOCK_BY_WINDOW` rather than a second copy of
  that mapping. Removed from Today, all in `static/shell.js`: the tall
  dinner hero, the "Before bed" prep tile, the defrost tile, the
  grocery-count tile, and the needs-you band's `shop_run` card (it said
  the same thing as the shop move — the last of the duplication the
  redesign existed to remove). The open-dinner needs-you card stays and is
  the one card allowed to stand in for Next up, scoped to **tonight's**
  date so "Tomorrow needs a dinner" cannot hide what to do in the next
  four hours. The notifications bell is hidden behind `SHOW_NOTIF_BELL`
  in shell.js — same pattern as `SHOW_CHORES_ON_TODAY`, routes and panel
  code untouched, one line to reverse; its time-bound contents are moves
  now and the rest is dropped for now. A prep-session source is a `TODO`
  in moves.py: nothing on `main` reports one yet. Left out honestly:
  `moves_for_day` reads the **current** plan, so a tomorrow that belongs
  to a different plan comes back empty rather than wrong.
- **2026-09-08 — A merge resolved shell.js by taking one side whole, and
  a day of front-end work vanished.** `2d69951` ("Merge custom-date-range",
  2026-09-06, a different session) hit a conflict in `static/shell.js` and
  resolved it with the branch's whole file: `2d69951:static/shell.js` is
  byte-identical to `2d69951^2:static/shell.js` (890 lines changed, net
  -681). The branch had forked at `eb54fd3`, so every shell.js change from
  the nine merges that landed between `eb54fd3` and `ca97720` on 2026-09-05
  was erased in one commit — the "Somewhere else" grocery triage chip
  (`0725d09`), the next-step chip (`6e9899c`), the Cook empty-state link to
  Plan and the end-of-cook handoffs (`a2c4973`), the Plan tab's
  reheat-as-leftovers label (`dbe458a`), the chores-setup link and the
  just-in-time stores prompt on Grocery (`606f655`), the full-plate side
  chips (`0d26635`) and the hard-allergy confirm sheet (`ea41834`).
  Nothing failed: there is no JS test harness in this repo, and every
  backend half was untouched, so the suite stayed green at 1161. Found only
  by reading the file. `static/shell.css`, `static/onboarding.html`,
  `static/chores-setup.html` and all of `app/` and `tests/` were verified
  hunk by hunk against the same nine merges and are intact — the loss was
  shell.js alone. Restored on `restore-dropped-frontend-work` by redoing
  `2d69951` as a real three-way merge (base `eb54fd3`, ours `ca97720`,
  theirs `acff198`) and re-applying that result onto today's main, keeping
  main's newer code at every overlap. Two hunks are deliberately NOT
  restored, because newer work supersedes them rather than main having lost
  them: `0725d09`'s `weekRangeLabel`/`periodNoun` planning-period wording
  (custom-date-range replaced it with `periodRangeLabel`/`planEntryLabel`,
  which does the same job better) and `6e9899c`'s singular
  `computeNextStepChip` (`tweak-sheet-see-your-week`, merged as `462d506`,
  had already re-added the pair in its plural form — the one piece of the
  loss that came back on its own). **The rule: when a merge conflicts in
  `static/shell.js`, resolve it hunk by hunk — keep both sides — and run
  the source-marker tests; never take one side wholesale.**
  `tests/test_frontend_restored_2026_09_08.py` is the tripwire: one
  assertion per restored feature plus a blunt line-count floor, because a
  wholesale resolution always shows up as a large sudden shrink.
- **2026-09-07 — The same breakfast on five mornings meant five cooks.**
  Emily, on a plan with Egg White Bites every morning: "We don't want to
  make egg bites every morning." A day-based plan writes each morning as
  its own entry, so the Cook screen showed each as its own cook. New
  `app/tools/cook_ahead.py` lets the household tick the days one batch
  should cover, and writes exactly the leftover-chain shape
  `repair_leftover_chains` writes (both halves, or
  `plan_leftover_chains` ignores it) plus a `cook_ahead: true` flag on the
  covered day — which changes nothing about the chain and only changes the
  words: "Made ahead — Monday's Egg White Bites", not "Leftovers".
  Groceries are deliberately untouched (the week still eats the same
  portions; only the cooking is consolidated), which is why this does NOT
  reuse `_unlink_leftover_target` — that one rescales, correctly, for a
  night being removed outright. One thing left standing: a BREAKFAST
  source is still invalid to `repair_leftover_chains` (it only accepts
  lunch/dinner sources), so a cook-ahead chain on breakfasts would be
  reopened if that ever ran over an existing plan. It only runs during
  generation today, so it can't reach one — but widening that rule is the
  fix if it ever does.
- **2026-09-06 — A crashed allergy check fails CLOSED at approval.** Found
  by an independent review of the confirm-tap work: `check_plan_conflicts`
  raising inside `approve_weekly_plan` was logged and the week approved
  anyway — tolerable when the check only produced a warning, not once a
  hard clash needs a confirm tap, because the crash silently bypassed the
  gate. A failed check now returns `needs_confirmation` with
  `check_failed: true` and a plain note ("I couldn't check this week
  against your household's allergies just now"); the flag still approves,
  so a broken check cannot lock a household out of its week. Also worth
  knowing: the armed "Approve anyway" state is client-only and resets if
  the band re-renders between taps — deliberate, the server re-gates every
  tap, so the worst case is one extra tap. Don't "fix" it by remembering
  the confirm on the client.
- **2026-09-05 — Reversing a grocery contribution could strand a line when
  its display unit rolled between lb/oz (or cup/tbsp) mid-week. Branch
  `grocery-reversal-from-ledger`.** `_subtract_quantity` reversed a meal's
  contribution by subtracting it out of whatever the line CURRENTLY
  displayed. `_humanize_grocery_quantity` rolls a line's unit to whatever
  reads best at its total, so a line at "1.25 lbs" becomes "8 oz" once the
  first of two ledger rows is subtracted back out — but the second ledger
  row was still written in lb (from ingest, before either reversal), so
  reversing it against a line now in oz found two units that didn't
  reconcile and left the line exactly as it was: stranded, and
  `clear_weekly_plan` reverses a week's entries in no particular order, so
  this could hit on either meal depending on iteration order.
  `_reverse_meal_grocery_contributions` now recomputes a line from what
  every OTHER meal still on the ledger for it adds up to
  (`quantities._sum_ledger_quantities`, converting between units in the
  same measurable family as it sums) instead of subtracting one
  contribution out of the display — which makes reversal order-independent
  and needs no ordering safeguard in `clear_weekly_plan` at all. That
  recompute only ever replaces a line the ledger can fully account for
  (`source_weekly_plan_id` set); a hand-added standing want falls back to
  the old subtract-from-display path, now itself made unit-normalising,
  and is never deleted by a reversal even when nothing else wants it —
  only blanked back to an unspecified quantity. Packages are untouched
  (already correct: link-count, not quantity). No schema change — the
  ledger's own display strings carry enough (amount + unit) once
  normalised through `quantities.py`'s existing conversion tables. New
  test file `tests/test_grocery_reversal_ledger.py`.
- **2026-09-05 — 17 peppers was arithmetic, and the arithmetic was wrong in
  two places. Branch `fix-produce-quantities` (on top of
  `fix-grocery-quantity-inflation`, NOT merged at the time of writing).**
  Emily, looking at the week the package fix had already cleaned up: "a
  regular week for a family of 3 shouldn't have 17 peppers, it's not
  normal — look into the root of this." The previous entry closed with
  exactly this as **Open for Emily**, and answered it wrongly: the
  seventeen were not honest arithmetic that might merely want a cap. Two
  independent causes, plus the reason there were five pepper dinners at
  all.
  - **Nothing in the grocery path had ever looked at `default_servings`.**
    Every recipe carries 4 (the `add_recipe` default, and what generation
    writes), and the only scaling factor —
    `attendance.grocery_scale_factor` — was deliberately anchored to the
    HOUSEHOLD, returning 1.0 whenever no attendance row said otherwise. Its
    docstring said so and said why: a recipe-servings anchor "would
    silently re-quantify every meal in the app the moment this shipped,
    which is a much bigger claim than this ticket gets to make on its own."
    Correct scoping then; the claim has now been made, by Emily, on
    evidence. `attendance.servings_scale_factor` composes the two —
    `grocery_scale_factor` (headcount ÷ household_size) × (household_size ÷
    default_servings) = **eaters ÷ default_servings**, the household size
    cancelling so neither anchor is applied twice. It falls back to
    attendance alone with no members on record or no default_servings,
    so a household mid-onboarding still shops as it did.
  - **Rounding happened once per recipe and then summed.** With the
    servings scaling the five dinners want 2.25, 3, 1.5, 3 and 3 peppers;
    rounded up individually that is 3+3+2+3+3 = **14**, when the week wants
    12.75 → **13**. A shopper buys peppers once. `WeekGroceryBuffer` holds
    per-portion amounts unrounded for the whole approval and rounds once
    per grocery line (`_week_bought_amount`: ceil for countables, nearest
    quarter for measurables, rolled up to the display unit FIRST so the
    line and the ledger share a unit). **The recipe-week group was the
    wrong unit for this** — grouping is right for a sealed package, but
    Emily's peppers came from five DIFFERENT recipes, so only something
    spanning the whole `approve_weekly_plan` can see them as one shopping
    decision. `plan_meal` and the swap paths get a buffer of their own that
    flushes on the way out.
  - **Rounding once forces apportionment, and that is load-bearing, not
    tidiness.** The line says 13; the meals behind it wanted 12.75. Ledger
    rows carrying their own unrounded shares would leave a phantom quarter
    pepper after `clear_weekly_plan` — which then displays as one whole
    pepper for a dinner nobody is cooking. `_apportion` splits the rounded
    total by largest remainder into whole quanta that sum to the line
    exactly. A meal can land on a real `"0"`, never a blank: a blank tells
    `_subtract_quantity` "this contribution IS the whole line".
  - **Why five pepper dinners existed at all is a prompt gap, not a
    quantity bug.** The generation prompt had variety rules for
    `main_protein` and for cuisine and none for an ingredient. Both
    instruction blocks now cap one fresh ingredient at 3 dinners a week
    (staples and things the household asked for exempt) and tell the model
    to set `default_servings` from `attendance.default_serves` rather than
    a generic 4 — which, once it takes, means the ingest has nothing left
    to rescale. `plan_quality.ingredient_repeat` measures it, warn-only.
    Note `"pepper"` is deliberately NOT in `_STAPLE_FRESH_WORDS`: black
    pepper is a staple, it is pantry so it never reaches the rule, and the
    word sitting there would exempt Bell pepper from the rule written for
    it.
  - **This changes amounts for every household with members on record, and
    that is Emily's to veto.** Four existing tests asserted the old
    behaviour and were turned over on purpose, one of them
    (`test_a_week_where_everyone_is_home_shops_exactly_as_before`) being
    the explicit safety property of the earlier scoping decision; it is now
    `..._buys_for_everyone_who_is_home` and says in its docstring why it
    flipped. Two households of 2 with 4-serving recipes now buy half of
    what they bought last week. The failure mode if this is wrong is
    under-buying, which costs a trip.
  - **Left open:** existing SAVED recipes still say 4, so the ingest is
    doing the rescaling for every one of them and will keep doing it until
    they are rewritten; nothing back-fills `default_servings`. And
    re-quantifying an already-approved line when attendance changes
    afterwards is still not done (the KNOWN LIMITATION in
    `_add_recipe_ingredients_for_entries`, unchanged).

- **2026-09-05 — A stated request is the week's ANCHOR, not an order. Branch
  `plan-quality-anchor-not-order`.** Emily's decision 11a on the plan-quality
  ticket: "I want burgers" means burgers exactly where she said AND a week
  composed around them. The day-based instructions used to say "honour it
  exactly … plan that meal where they said, don't plan over it" and stop
  there — the literal-request-and-nothing-else behaviour she saw. Prompt-only
  change: a stance sentence ("A week should read as composed — a shape across
  the days …") near the top of the guidelines, the freeform bullet rewritten
  (anchor it, then build the days around it, name the connection in
  reasoning; the tag-collision rule is the ONE exception to placement), and
  the collision paragraph reconciled with it. +1,328 chars in the cached
  block (a one-time cache-write cost). No code path changed; no model or
  effort change (her 12/13 wait on baseline data). Pinned by
  `tests/test_prompt_anchor.py`.

- **2026-09-05 — Every meal is a full plate, and a short one gets a side
  rather than a regeneration. Branch `fix-full-plate` (NOT merged at the
  time of writing).** Emily settled the question `plan_quality`'s
  `full_plate` rule had been deliberately only WARNING about since it was
  written. A plate is protein + vegetable, plus a carb unless the
  household's `eating_style` reads low-carb; it applies to all four slots,
  with a lighter floor on breakfast/snack. New `app/tools/plates.py` holds
  the rule, the classifier and the attach mechanics; `agent.generate_sides_llm`
  is the one new model call; `_complete_plates_pass` runs it over a
  just-finished week.
  - **The eating_style classifier is new and is a keyword list, not a model
    call.** Nothing classified `eating_style` before — it was handed to the
    model as free text. `plates.is_low_carb` is a documented phrase list
    because it runs per entry per week, the cost of a wrong answer is one
    unwanted side, and a list anyone can read and correct beats a judgment
    nobody can see. It is deliberately literal: "clean eating" reads as
    NOT low-carb and gets a carb, which is the app's default, not a harm.
  - **"Never just a fruit / just a granola bar" reduces to a two-group
    floor**, and that is the whole of the light rule (`LIGHT_SLOT_MIN_GROUPS`).
    Both of those carry at most ONE food group, so a two-group floor
    excludes them without the app keeping a list of foods it disapproves of.
  - **A side attaches to the ENTRY (`meal_plan_entries.sides_json`), never
    to the recipe.** A recipe is shared across weeks; rewriting its
    `ingredients_json` to bolt a salad on would change every future plan
    that reuses it and be indistinguishable later from the recipe's own
    ingredients. `derived_from_json` was the other candidate (no migration
    needed) and was rejected: it records what CAUSED a slot and is read as
    provenance by four modules, while a side is content the grocery list
    and Cooker have to consume. Recording it against the same entry_id is
    also what makes removal symmetric for free —
    `_reverse_meal_grocery_contributions` is keyed by entry.
  - **An entry with NO recorded food groups is skipped and logged, never
    guessed at** — the same stance `plan_quality`'s rule already took.
  - **Six side calls per generated week, dinners first.** A week needing
    more than six is a generation problem to read in the log, not one to
    paper over with twenty-eight model calls; the overflow is logged by
    name. ~$0.003–0.005 per call (cached instructions; see
    `tools/usage.py`), so a capped worst-case week is about two cents.
  - **The household is told once, and the telling is marked by the ROUTE,
    not the tool.** `get_week_menu` is also a read the assistant makes
    mid-conversation; stamping `plates_intro_shown_at` there would spend
    the one telling on something nobody saw, so `/api/week-menu` does it.
  - **`complete_plates` off means log-only, not silent.** The pass still
    runs and still records what it would have added.
  - **Known gap:** `cooker.deplete_inventory_for_meal` reads the RECIPE's
    ingredients, so a side's ingredients are bought but never depleted from
    tracked inventory when the meal is checked off. Deliberately out of
    scope; own ticket. Also deliberately not built: Emily's "optional
    add-ons" idea for keto (decision 7a) is a separate later ticket.
- **2026-09-05 — Two decided fixes to the allergy check: gluten/wheat's own
  false positive, and pre-enforcement allergy facts backfilled onto member
  records. Branch `allergy-backfill-and-gluten` (NOT merged at the time of
  writing).**
  - **"Gluten-Free Pasta" made with rice flour stopped flagging itself.**
    `_ALLERGEN_ALIASES["gluten"/"wheat"]` expands into flour/pasta/noodles so
    the check reaches "Wheat Pasta" — but that same expansion flagged a dish
    that is, by definition, safe for the restriction it tripped. Two fixes,
    both via the existing mechanisms rather than a new one: alternative-flour
    compounds (rice/almond/chickpea/buckwheat/corn/oat/coconut/tapioca
    flour; rice/glass/soba/buckwheat noodles; chickpea/lentil/rice pasta)
    added to `_COMPOUND_EXCEPTIONS`, and a new, narrower rule in `_matches`:
    a segment (dish name or one ingredient line) that says "gluten-free" /
    "gluten free" / "GF" outright is negated for the GLUTEN/WHEAT alias
    words *in that segment only* — a nut or dairy restriction still sees it.
    "Almond Flour Cake" is now correctly not-gluten but still a nut-allergy
    clash, since the discount is per word, never per compound (same rule
    `_COMPOUND_EXCEPTIONS` already followed for peanut butter/coconut milk).
  - **Facts written down before the enforcement fix existed only in
    `facts`, never on the member record (Emily's decision 3a).** The
    planner and `check_plan_conflicts` read `facts` directly since
    2026-09-04, but a member's own profile only ever showed
    `dietary_restrictions_json` — so a household whose allergy was saved as
    a What-we-know note before that fix looked, on their own profile, like
    they had no allergy on file at all. `db._backfill_allergy_notes_from_facts`
    runs every startup (same idempotent-migration shape as
    `_backfill_member_colors`): for each fact naming an existing member and
    yielding an avoidance phrase — via `coordination._fact_keywords` and a
    newly-extracted `coordination._named_member`/`_name_words` (factored out
    of `_avoidances()`, which now calls them too, so the backfill can never
    silently drift from what the live check treats as an avoidance) — it
    appends `"allergy: <phrase>"` to that member's restrictions when not
    already present, case-insensitively. Deliberately **not** gated on
    `fact.hard`: the What-we-know screen has never set that flag itself (see
    the 2026-09-04 entry below), so gating on it would have backfilled
    almost nothing. Household-wide facts (no member named, e.g. "no pork in
    this house") are left alone on purpose — nothing is missing there to
    fill in. Facts are never edited or deleted. Runs automatically on the
    next deploy (wired into `_run_migrations`, called from `init_db()` at
    app startup); to run it immediately without waiting for one, from
    inside the deployed container: `railway ssh -- python -c "from app.db
    import init_db; init_db()"`.
- **2026-09-05 — "Pick my own days" became a date RANGE, and the same card
  stopped assuming every household plans in weeks. Branch
  `custom-date-range` (NOT merged at the time of writing).** Two tickets in
  one pass because they are one control.
  - **Emily, looking at the start-day-plus-length picker: "for the custom
    dates just let them choose the date range they want."** One strip of
    days now — first tap is the first day, second tap the last, the days
    between fill in. The five length chips (`PERIOD_LENGTHS`) are gone and
    so is the "keep it two taps, not a calendar widget" note that produced
    them; the replacement comment says that explicitly so nobody re-argues
    it from the old one. Two taps survive anyway.
  - **Tapping the start again completes a ONE-DAY range** rather than being
    a no-op. The alternative reading of the ticket ("no-op or clears")
    blocks "just tonight", and a control that swallows a deliberate tap is
    worse than one that takes a person at their word. A day before the
    start restarts from there; any tap on a finished range starts a new one.
  - **The strip begins at the earlier of today and the household's own
    start**, not flatly at today. A Monday-anchored week opened on a
    Thursday preselects days that began before today, and a strip that
    cannot show them would open with nothing selected on it.
  - **28 is `/plan-week`'s clamp, not a design choice.** `PERIOD_MAX_DAYS`
    exists so the confirm never names dates the next screen would quietly
    shorten.
  - **The bug in the same card: it planned seven days for everybody.**
    `renderPlanWeekEntry` hardcoded a seven three times over — the second
    button's start day, both buttons' date labels, and (by omitting the
    argument to `startPlanningWeek`) the URL — so an "as we go" household,
    whose `/api/week/planning-period` has said `day_count: 3` all along,
    was offered two week-long stretches and then handed a seven-day intake.
    The server was right the whole time; only the client wasn't reading it.
  - **Three more call sites had the identical defect and are fixed with
    it** — the Today nudge's CTA and its dismissed-state link
    (`nudge.day_count` was already on that payload beside the label that
    names the real span) and the review band's "Change my answers"
    (`data.day_count`, exactly what `tryAgain` already passes). Leaving
    them would have made the Meals card and the nudge disagree about the
    same household's week.
  - `weekRangeLabel` is deleted rather than taught a day count:
    `periodRangeLabel(x, 7)` was already character-for-character what it did.
  - **Button wording is an ASSUMPTION, flagged for Emily:** "Plan the next
    3 days" / "Plan the 3 after" when the period isn't seven days, and the
    unchanged "Plan this week" / "Plan next week" when it is. It lives in
    one function (`planEntryLabel`); all picker copy lives in one object
    (`PERIOD_PICKER_COPY`), for the same reason.
  - Contrast measured in-browser and recorded in `shell.css`: range
    endpoints 7.23:1 light / 8.46:1 dark, the days between 10.65:1 /
    10.37:1, the hint line 4.70:1 / 7.36:1. Tiles measured 44x44 at 390px.
- **2026-09-06 — A multi-plan period takeover is now ONE transaction.
  Branch `atomic-period-takeover` (NOT merged at the time of writing).**
  The known debt from `planning-periods`: `retire_overlapping_plans`
  settled every decision up front and then destroyed per plan in a loop
  that committed many times, so a failure between two plans left the first
  plan's meals, prep tasks and grocery reversal genuinely gone while the
  caller raised and every screen said nothing had been saved.
  - **The fix is a passed connection, not a second implementation.**
    `_reverse_meal_grocery_contributions` and `_release_plan_days` take an
    optional `conn=None`; given one they read and write on it and neither
    commit nor close, and left unset they behave exactly as before — which
    matters, because the reversal has 7 call sites and none of the others
    changed. The in_cart/purchased rule is inherited unchanged, and there is
    a test driving it through a two-plan takeover to say so.
  - **Atomicity and deadlock-avoidance turned out to be the same
    requirement.** SQLite gives one writer at a time, so any helper still
    opening its own connection inside the open write transaction would wait
    on the lock and then fail with "database is locked". That is the real
    reason the connection is threaded rather than each helper being trusted
    to commit politely — and `tests/test_planning_periods.py` now counts
    `get_conn` calls during a takeover (exactly two: one read pass in
    `_plan_takeover` before the transaction opens, one for the transaction)
    so a well-meaning nested `get_conn` fails loudly instead of
    intermittently.
  - **No explicit `BEGIN` is needed and none was added.** `db.get_conn()`
    leaves sqlite3's legacy `isolation_level=""`, so the first write opens a
    transaction implicitly; reads on that connection see its own uncommitted
    writes, which is what the loop has always relied on (plan two must not
    find plan one's already-deleted entries). If that default is ever
    changed to `isolation_level=None`, this function needs a real `BEGIN`.
  - **Nothing long-running is inside the transaction.** The only LLM call in
    this path happens far earlier in `agent.generate_weekly_plan`; the
    takeover is the last step, after the plan is real, and the loop is
    bounded by the number of overlapping plans. `_plan_takeover`'s read of
    every live plan deliberately stays OUTSIDE, before the write connection
    is opened.

- **2026-09-04 — A written-down allergy now reaches the food, and the check
  that finds it stopped crying wolf. Branch `fix-allergy-enforcement` (NOT
  merged at the time of writing).** Root cause of the original bug was three
  gaps in a row, not one: generation was never handed the `facts` table, the
  safety net (`check_plan_conflicts`) read only member restrictions and only
  a saved recipe's ingredient text, and nothing ever called it outside chat.
  All three closed; a second pass then fixed what the first pass's keyword
  matching did to the innocent meals.
  - **Warn, never block — and that is a default, not a conclusion.** A clash
    is said out loud at generation, on the review band, and again on
    approval, but approval still goes through. Auto-promoting a hard note
    into a member dietary restriction, or hard-blocking on one, is
    **Emily's call and still pending**; until then the household decides and
    the app is merely never silent. Live evidence for that decision: an
    approved week of Emily's put "Pineapple chunks · 3 bag frozen" on the
    shopping list for a pineapple-allergic member. The allergen was in an
    innocently-named dish's INGREDIENTS, so the list was the first place it
    became visible. Left unblocked as agreed, with a test pinning that the
    approval sentence names the meal that put it there.
  - **Keyword extraction is the whole difficulty, and it is judgment, not
    an algorithm.** A hard fact is a sentence a person wrote, so treating
    every non-stopword in it as a food to hunt for flagged House Salad on
    "no pork in this house", Porridge on "no cow milk…oat milk is fine",
    Satay on "allergic to tree nuts but peanuts are fine", and a Protein
    Bowl on "needs high-protein dinners". Three layers now stand between a
    fact and a match term: only the span after an **avoidance trigger**
    counts (a fact with no trigger — a requirement — produces no terms at
    all), a **stated exception** is subtracted rather than merely truncated
    (so it also cancels an alias expansion), and the stopword list carries
    the furniture of household prose. Against that, an explicit
    **allergen alias table** (`_ALLERGEN_ALIASES`) makes "nut allergy" reach
    peanut butter and walnuts while whole-word matching keeps it off
    coconut, nutmeg and butternut. Both are deliberately *lists a person can
    argue with*, not stemming — and the alias table is a starting list;
    extend it when a real miss shows up.
  - **A multi-word avoidance is matched as a PHRASE, not as its loose
    words** (third pass, after a second independent verification). Reading
    the right span out of the sentence was only half the job: every word of
    that span then became an *independent* term, so "no red meat during the
    week" hunted for "red" on its own and flagged Red Lentil Dahl, and
    "avoid sugar" flagged Sugar Snap Peas. A phrase now has to be found as
    a phrase — all its words, in order, inside ONE stretch of text (the
    dish's name, or a single ingredient line, never the two joined) — and
    only a genuinely one-word avoidance matches on one word. Splitting on
    commas and "and"/"or" first (`_conflict_phrases`) is what stops that
    becoming a miss: "allergic to pineapple, shellfish and eggs" is three
    one-word phrases. Applied to saved restrictions as well as facts, since
    "red meat" typed into the restrictions box has the same problem. **Known
    limit, deliberately left open:** the check matches words, so "red meat"
    does not reach beef, lamb or pork — closing it needs a food taxonomy,
    not a regex. There is a test asserting the miss so it stays written
    down.
  - **`_COMPOUND_EXCEPTIONS` — the two-word foods where the allergen word is
    not the allergen.** Whole-word matching already keeps "nut" off coconut,
    nutmeg, butternut and eggplant; these are the ones written as two words,
    where the word really is standing there and still isn't the allergen —
    "peanut butter" is not dairy, "coconut milk" is not dairy, "sugar snap
    peas" are a pea (all three off Emily's own week). Implemented as an
    exclusion *on the alias*, never by dropping it: real butter is dairy, so
    "butter" stays in `_ALLERGEN_ALIASES["dairy"]` and only its occurrence
    inside a nut/seed-butter compound is discounted. Two limits keep a false
    positive from becoming a false negative — only the listed word is
    discounted (a **nut** allergy still catches peanut butter, on "peanut"),
    and a discount only applies to a one-word avoidance (write "allergic to
    coconut milk" and you are taken at your word). Same spirit as the alias
    table: a short list a person can argue with, extended when a real case
    shows up. `buttermilk` went the other way — into the dairy aliases,
    because whole-word matching reaches neither half of it and it *is*
    dairy.
  - **A false positive is a safety bug here, not a cosmetic one.** A check
    that flags the safe meals too is one the household learns to click past,
    and the real warning goes past with it. That is why the false-positive
    work sits in the same file as the false-negative work.
  - **Correction to `ccbc532`'s commit message:** it says "32 new tests, 24
    of which fail on the previous commit". A first re-measurement said 23; a third-round verification re-ran it three times and got **24** — the commit message was right. The
    claim is wrong only in the message, not in the code or the tests.
  - **The `hard` flag still has no UI.** `add_fact(hard=True)` is reachable
    from chat and nowhere else — the What-we-know screen cannot set or show
    it — so the whole hard-fact path depends on the assistant having chosen
    the flag when it wrote the note. Open, unchanged by this branch.

- **2026-09-04 — The grocery list multiplied packages by how often a meal
  repeated. Branch `fix-grocery-quantity-inflation` (NOT merged at the time
  of writing; branched off `fix-leftovers-ordering`).** Emily's first
  approved week: 6 bags of baby spinach, 4 bottles of honey, 4 tubs of
  hummus, 3 bottles of olive oil, cottage cheese as "48 oz tubs". The three
  earlier fixes she remembers — summing instead of concatenating, the
  singular/plural merge, container pluralising — were all correct and none
  of them was the bug. The inputs to the sum were wrong.
  - **Root cause: `approve_weekly_plan` ingested one MEAL at a time**
    (`weekly_plan.py`, the `_plan_grocery_candidate_entries` loop → one
    `_add_recipe_ingredients_to_grocery_list` call per entry). The
    generation prompt deliberately repeats a breakfast 2-3+ times a week
    and writes qty as a bought unit ("1 bag"), so six mornings added one
    bag six times. Ingestion is now per RECIPE-week
    (`recipes._add_recipe_ingredients_for_entries`).
  - **A sealed package is added once per recipe-week and consolidated
    across recipes by keeping the LARGER, not the sum** (`add_grocery_item`
    gained `quantity_mode="max"`). Per-portion amounts are untouched and
    still add up per meal, still scaled by each meal's own attendance
    factor — Emily's 18 peppers were the arithmetic truth, not a bug.
  - **`_PACKAGE_UNITS` is deliberately five words** — bag, bottle,
    container, jar, tub. "can"/"tin"/"box"/"carton"/"pack" are packages of
    things eaten a package at a time (a tin of beans, a box of pasta), and
    collapsing those leaves a cook short mid-week. A test asserting a
    takeover trims the beans caught exactly that when "tin" was briefly in
    the set. When in doubt a word stays OUT: an extra line beats a missing
    dinner.
  - **"48 oz tub" parsed as forty-eight tubs.** `_parse_quantity` now reads
    `<size> <measure> <container>` as ONE package whose size rides with the
    unit — `(1.0, "tub (48 oz)")`, formatted back as "3 tubs (48 oz)" and
    round-tripping exactly. This changes "1 lb bag" to display as "1 bag
    (1 lb)" too, which is the same fix, not a side effect.
  - **Reversal had to stop being one-meal-one-share for packages.** A
    package line now survives until the LAST meal linked to it goes
    (`_reverse_meal_grocery_contributions`), so a swap leaves the bottle
    the other two dinners still need and clearing the week still empties
    the list exactly. A package line with no `source_weekly_plan_id` is a
    hand-added standing want and is never removed.
  - **A descriptor is not a unit, and it used to eat the whole quantity.**
    Emily added two more rows after the above was written: "Mixed berries:
    2 lb bag (frozen) + 2 lb bag (frozen) + 2 lb bag (frozen) + 2 lb bag
    (frozen)" and "Pineapple chunks · 3 bag frozen". Two different
    failures with one cause — "(frozen)" made the string unparseable so it
    concatenated, and in "1 bag frozen" the unit read as "bag frozen" so
    `package_unit` never saw a bag and three snacks summed. Descriptors
    now come OUT before parsing and come BACK as a note
    (`_split_quantity_note`), in one canonical shape: **amount first, note
    last, separated by a comma** — "1 bag (2 lb), frozen", "3 bags,
    frozen", "1 jar, large". Amount-first because that is what a shopper
    scans for; the note is what they read once they have found the line.
    The vocabulary is a CLOSED set (`_DESCRIPTOR_WORDS`) of words that can
    only ever be descriptions — an unrecognized trailing word is left
    alone, because guessing it is decoration is how a real unit gets
    thrown away. A note is only re-attached to something that parsed, so
    freeform text ("a frozen handful") keeps its own wording rather than
    saying "frozen" twice.
  - **The concatenation fallback may never write the same words twice.**
    "2 cups + 1 lb" is an honest report that two amounts could not be
    reconciled; four copies of "2 lb bag (frozen)" is not. Identical text
    now collapses to one copy with a repeat marker — "a handful ×2" —
    which counts up on the way in (`_repeat_or_concatenate`, summing under
    `_try_consolidate_quantity` and taking the max under
    `_greater_of_quantity`) and back down on the way out
    (`_subtract_quantity`). Genuinely different text still concatenates.
    Same package word with the size stated on only one side merges too
    (`_shared_package_unit`): "1 bag (2 lb)" beside "1 bag" is two bags of
    the same thing, and the stated size wins because it says more. Two
    DIFFERENT stated sizes still show both — that is a real disagreement,
    not a formatting accident.
  - **How `leftovers-servings-scaling` actually landed**, correcting an
    earlier draft of this entry that said a total-servings factor
    "replaces that one call and nothing else". It does not. That branch
    (since merged to `main`; see the entry below) does three things per
    entry and only one of them is a factor, and merging it into the
    grouped ingest needed all three handled separately:
    1. `scale *= batch["servings"] / batch["cook_eaters"]` for a chain
       SOURCE is the easy one, and the old claim holds there: it
       multiplies the per-entry `grocery_scale_factor` inside the loop
       that builds `scaled_for_entry`, one entry at a time, composing
       exactly as it did before.
    2. `return [], []` for a LEFTOVERS entry did NOT survive translation
       and would have been a real bug. A chain reuses the same
       `recipe_id`, so the cook night and the reheat night land in the
       SAME recipe-week group — an early return would have dropped the
       cook night's shop along with the reheat's. It is now a FILTER:
       `contributing_ids`, with the group returning `[], []` only when
       every entry in it was a reheat (which is what the single-meal door
       still does for a lone leftovers entry).
    3. The package path needed that filtered list too. A package is added
       once per group and then `_record_link`s each contributing entry; a
       reheat night must not hold a link, or a swap would leave the
       bottle alive past the cook night that actually earned it.
    Chains are now looked up once per PLAN and cached, not once per entry
    — grouping already brings every meal for a recipe through in one
    call, so the per-entry query would just repeat itself.
  - **Open for Emily:** whether a true per-portion sum should ever be
    capped. 17 peppers is honest arithmetic — five dinners wanting 3, 4,
    2, 4 and 4 — and it is NOT capped or hidden; the list shows 17. It may
    still be more than anyone wants to read on one line.
    **CLOSED, and this paragraph was wrong** — see the 2026-09-05 entry at
    the top. It was not honest arithmetic: nothing scaled by
    `default_servings`, so three people were buying four people's dinner,
    and the per-recipe rounding added one more on top. No cap was needed;
    the inputs were wrong again, one level up.

- **2026-09-04 — A leftovers night is a reheat, not a second cook. Branch
  `leftovers-servings-scaling` (on top of `fix-leftovers-ordering`, NOT
  merged at the time of writing).** Emily, seeing the same dish on two
  nights of the Cook view: "show the one night it's being cooked as 6
  servings, make a little note that this covers tonight + leftovers, and
  not have it on another night for cooking." `fix-leftovers-ordering`
  made the chains trustworthy (`derived_from.make_double_for` on the
  source) but nothing read them back, so the second night still behaved
  as a full cook everywhere: its own recipe card, its own groceries, its
  own defrost reminder, its own prep, and a second inventory depletion
  when checked off. New `app/tools/leftovers.py` is the one read-side
  reader (it honours a chain only when BOTH entries agree, so an
  unvalidated plan behaves exactly as before), and the Cook view, the
  grocery contribution, defrost and the prep context all go through it.
  Component mode's bulk-cook scaling was lifted into
  `cooker._scale_card_to_batch` and is now shared by both modes rather
  than existing only on the component side. **Known gap:** the Meals
  *Plan* tab (`weekly_plan.get_week_menu`'s `build_slot`) still draws a
  leftovers night as an ordinary planned cook with a "Cook this" button
  — it only detects leftovers from the freeform text, and a chain entry
  carries a real recipe_id. Deliberately out of scope; own ticket.

- **2026-09-04 — A plan is a PERIOD, not a week, and no day has two of
  them. Branch `planning-periods` (NOT merged at the time of writing).**
  Loop Board "Planning periods, not weeks — plan any window (Thursday to
  Thursday)". `weekly_plans` gains `content_start_date` + `day_count`;
  `week_start_date` keeps its old job and only that job — the filing key
  every `/api/week/{...}` route is addressed by. Read the period through
  `weekly_plan.plan_period()`, never the columns directly.
  - **The sentinels ARE the old meaning, and nothing backfills them.** `''`
    / `0` resolve to "seven days from week_start_date", so every row
    written before this reads identically without being rewritten — and a
    rewrite is the only way this migration could turn a correct row wrong.
    There is a SQL twin of that resolution (`_SQL_PERIOD_START`,
    `_SQL_PERIOD_LAST_OFFSET`) because two queries have to resolve it
    inside SQL; they have to agree with the Python exactly, and a drift
    would be invisible — the query would just return a different plan than
    every other reader thinks is current.
  - **The riskiest site was the one a grep would miss**, exactly as the
    ticket's own investigation predicted: `_current_weekly_plan_row`'s
    seven-day window was the SQL literal `date(week_start_date, '+6 days')`,
    not a `timedelta` or a `range(7)`.
  - **`_week_dates(start)[:day_count]` was the other bad idiom** — five
    sites. A slice CAPS at seven, so an 8-day Thursday-to-Thursday period
    silently lost its eighth day: generated for, never audited, never
    rendered. `week_intake.period_dates(start, n)` replaces it.
  - **One plan per day is Emily's rule (2026-09-04) and it is NEW.** There
    was never a uniqueness constraint; overlapping plans are ordinary
    existing data, resolved until now by a newest-wins tiebreak.
    `retire_overlapping_plans` enforces it going forward, and **existing
    overlaps are deliberately NOT migrated** — `find_overlapping_plans`
    reports them without touching anything. Deciding at startup which of a
    household's real, already-cooked-from weeks to dismantle, in the one
    database that matters and with no undo, is not a migration's business.
  - **Takeover runs LAST in generation, after the plan is real.** It is the
    only step that destroys another plan's content, and the week it
    replaces is one the household may still be cooking from — dismantling
    it for a generation that then fails and rolls back would be the worst
    possible order. Grocery reconciliation goes through the existing
    per-meal `_reverse_meal_grocery_contributions`, so the in_cart/
    purchased rule is inherited rather than re-decided.
  - **A new period strictly INSIDE an existing plan orphans that plan's
    tail**, because a period is contiguous and cannot survive with a hole
    punched through it. Reported as `orphaned_dates` and logged rather than
    hidden. **This one is worth Emily's eyes** — it is the only case where
    the household loses planned days they didn't ask to replace.
  - **`clear_stale_grocery_items` had to change, and this was found by
    writing the test, not by reading the code.** It treated every plan but
    the current one as stale, which was the same sentence as "replaced"
    right up until a partial takeover became possible. A period starting
    Thursday leaves the previous plan alive with Mon–Wed still on it, and
    its ingredients were being deleted out from under three days the
    household was about to cook. Stale now means "holds no day from today
    onward". The overlap itself is left to the per-meal reversal — which is
    also what gives an already-bought line its protection, since this
    function is a blunt DELETE with no ledger behind it.
  - **`planning_anchor` finally does something.** Collected at rhythm
    onboarding and, by its own setter's admission, acted on nowhere until
    now: `suggest_planning_period` maps `sunday_before` (and never-answered)
    to the Monday week, and `midweek`/`as_we_go` to seven days from today.
    That mapping is a judgment call — the anchor is a cadence, not a
    weekday — and it is written down in that function rather than inferred.
  - **The prompt was rewritten in the same pass.** This repo's own rule is
    that telling the generator something isn't the same as preventing it;
    the converse also holds. A period-shaped database under a system prompt
    that says "Monday through Sunday" three times gets Monday-week
    reasoning anyway. The assistant is also told not to plan wider than it
    was asked, because a wider period now silently retires days nobody
    mentioned.
  - **An adversarial review found nine issues before this was pushed; eight
    are fixed and one is deliberately open.** The serious ones were all the
    same mechanism failing in different places: `day_count = 0` meant BOTH
    "surrendered every day" AND, read as the legacy sentinel, seven days —
    so a retired plan claimed a whole week again the moment anything let it
    past a `status != 'retired'` filter, and **approving a week did exactly
    that** (it resolved to the retired row by filing key and set its status
    back to `approved`: two live plans on one week, from one HTTP call).
    The sentinel is now BOTH columns unset together and a retired plan keeps
    its start. Also fixed: `get_plan_id_for_date`'s filing-key fallback,
    which answered "yes, that plan" for days a plan no longer covers —
    `slot_needs` is the caller, so an `away` attached to a dead plan is
    never enforced and the household gets shopped for a night they said they
    were out. And the takeover now deconflicts **globally**: shortening two
    pre-existing overlapping plans independently pushed both onto the same
    resume date and invented five clashing days that did not exist before it
    ran. **The lesson is the same one the Plan the Week review taught: the
    author had verified all of it and still shipped those.**
  - **~~STILL OPEN~~ CLOSED on branch `atomic-period-takeover`
    (2026-09-06): `retire_overlapping_plans` is now one transaction.** It
    was true as written — every decision was computed before anything was
    destroyed, but each plan's meals, prep tasks and grocery reversal
    committed before the next plan was touched, so a failure mid-loop (a
    locked database, a killed process) left the first plan's days genuinely
    gone while the household saw an error saying nothing was saved. Only
    ever reachable when one generation took over two or more plans at once.
    The fix is the one this bullet asked for: `_release_plan_days` and
    `_reverse_meal_grocery_contributions` take an optional `conn`, so the
    whole loop runs on one connection and commits once. See the 2026-09-06
    Decision-log entry at the top.
  - UI is one light control on the plan card ("Pick my own days" → start
    day + length + a confirm naming the dates), inline rather than a sheet.
    Contrast measured in both schemes; lowest new value 4.58:1 light
    (a 10px/800 eyebrow on `--ink-muted`), 7.36:1 dark. No second apricot —
    the screen's one primary stays with Approve.

- **2026-09-02 — Dark mode. Branch `pomona-dark-mode` (MERGED; was stacked
  on `pomona-kitchen-cooker`, which merged with it).**
  Pomona Stage 3, the last of the rebrand. `theme.css` gets one
  `@media (prefers-color-scheme: dark)` block redefining only the token
  custom properties; the app follows the OS and there is deliberately **no
  manual toggle** this stage. The block is scoped
  `:root:not([data-theme="light"])` so a page can opt out — `share.html`
  (the public "printed menu" card) and the three dead legacy pages
  (`grocery.html`, `cooker.html`, `kitchen.html`) do.
  - **Sixteen dark values come from the brand guide; the rest are derived**
    and carry their measured WCAG ratio in a comment. The derived one to
    know about is `--urgent`: on dark it becomes a LIGHT accent (`#E6705B`)
    carrying `--on-accent-ink`, i.e. Rule One reaches it on dark where it
    does not on light. It has to, because `--urgent` is used both as a fill
    AND as body text, and no dark terracotta satisfies both on a dark
    ground. Still Emily's call, same as the light `#B23A22`.
  - **The trap in this codebase is a token that plays two roles.** `--spruce`
    (and its aliases `--plum` and `--midnight-violet`) is the hero fill AND
    the strongest ink in light; in dark it becomes a raised panel, so every
    `color: var(--spruce|--plum|--midnight-violet)` was dark-on-dark. That is
    ~100 call sites, now on the new `--ink-strong` (identical to `--spruce`
    on `:root`). Same shape for `--oat-cream`/`--ground` used as ivory text,
    and `--ink` used as the toast's background. **Grep for an alias, not
    just the canonical name** — the first sweep missed `--midnight-violet`
    entirely and that is where most of the call sites were.
  - **Light mode is byte-identical, and that was verified rather than
    asserted:** two servers (this branch and its parent), same throwaway DB,
    computed-colour dump of every element across all four tabs and both
    sheets, compared by SHA-256. It caught two regressions this pass had
    introduced that no screenshot would have — a toast shadow moved
    `.25`→`.18` and a scrim `.42`→`.45` when they were pointed at tokens
    whose values were close but not equal. **A literal only becomes a token
    if the token's light value is byte-identical**; otherwise it keeps the
    literal and gets a dark override. Both cases are commented in place.
  - `CACHE_NAME` `pomona-shell-v4` → `v5`. Note this bump matters LESS than
    the v3→v4 one did, and it is worth being precise about why: CSS/JS/HTML
    are **network-first** in this worker, so an online phone would pick the
    new stylesheet up regardless. What the bump actually clears is the
    stale *offline fallback* copies of the old light-only CSS. (Only the
    icons and manifest are cache-first, and they did not change here.)
    `manifest.json` is deliberately unchanged — the manifest
    has no media-query form, so only the in-page `<meta name="theme-color">`
    tags vary by scheme, and they now do.
  - **Known light-mode contrast failures found and deliberately not fixed at
    the time**, because this stage could not change light. **ALL FOUR HAVE
    SINCE BEEN FIXED** on `main` (2026-09-03, `3fc55d6`, which also added
    `tests/test_contrast.py`); `theme.css` now carries the measured
    "Darkened 2026-09-03" ratios in place. The list below is history, not an
    open to-do: the completed-chore tick is
    `#fff` on celadon (1.87:1 — the same Rule One class stage 1 fixed nine
    of and missed here), `--ink-done`/`--ink-done-soft` (2.80:1/1.85:1), and
    login's placeholder/helper (3.93:1/3.54:1). All are correct in dark.
    (Superseded: they were fixed on 2026-09-03 — see the correction at the
    top of this bullet. Left in place because the list is still the record of
    what stage 3 found and chose not to touch.)

- **2026-09-02 — Kitchen is native too, cooking moved under Meals, and the
  last iframe tab is gone. Branch `pomona-kitchen-cooker` (MERGED).**
  Pomona Stage 2 slice 3, built straight to InnKitchen/InnCooker. Two
  screens changed and one whole mechanism went with them.
  **The Kitchen tab is now a hub the shell draws** — a spruce hero carrying
  what the app has learned (People / Taste / Rhythm / Stores counts, and a
  "Read it back"), then Inventory and Stores as entry tiles. No apricot
  anywhere on it by design: the blueprint's rule is that Kitchen has no
  primary action, because nothing on it is urgent. Inventory is the quietest
  thing on it — muted stroke, no badge — because inventory is background by
  policy and must not look like work waiting to be done.
  **Cooking is a second state of the Meals tab**, behind a Plan | Cook
  segmented control, and the route stays `/week` in both — Cook is a state,
  exactly as Grocery's To buy / Plan stops / Review are states of
  `/grocery`. It is the same week's plan with the recipes opened up, so it
  belongs with the week; it only ever lived in Kitchen because a bypass
  pointed that tab's iframe at `cooker.html`, which lit the wrong tab while
  you cooked. `activateTab`'s `forceEmbedSrc` hack is deleted with it, and
  `/cooker` now redirects to `/week` rather than `/kitchen`. Tonight is the
  hero; prep is a two-up supporting rail; the rest of the week is a quiet
  list. Everything the old page did survives: check-offs, expandable
  recipes, the live serving stepper, bulk-cook collapse, the attention
  banner's three shapes, "why this", fill-in-a-recipe, and both hands-free
  sessions.
  Seven things worth knowing:
  - **`.week-content` is a flex column with `gap: 12px`, and wrapping the
    plan's rows in `#week-plan-view` collapsed them into one flex item** —
    silently taking every gap between the plan's rows to 0 (measured, not
    guessed: the approve row ended up flush against the reset row). The
    wrapper carries the same `display:flex; gap:12px` now. Any future
    "wrap these existing rows in a div" needs the same check.
  - **`cookTonightIndex` must be pinned on load, not recomputed per
    render.** It prefers an *uncooked* meal, so recomputing it after a write
    meant ticking tonight's dinner as cooked threw it out of the hero and
    replaced it with the evening snack — the screen moving out from under
    the person who had just finished cooking. `static/cooker.html` had the
    same guard (`autoFocusedToday`) for the same reason; it had to be
    re-derived rather than inherited, because that page recomputed nothing.
  - **`check_off_meal` and `check_off_prep_step` are tagged `tab: 'kitchen'`
    by the backend** (`_KITCHEN_TOOLS`), and cooking is no longer in
    Kitchen. So `refreshStaleTabsFromActions`'s new `kitchen` branch
    refreshes *both* the hub and the Cook state. Don't "tidy" it to one.
  - **Cook is refreshed from inside `loadWeekMenu`, not at its six call
    sites.** Plan and Cook are two renderings of one week, so every path
    that reloads the plan reloads Cook — and a seventh call site added later
    gets it for free instead of being the next thing to go stale.
  - **Kitchen's entry tiles open sheets that iframe the existing pages.**
    `memory.html` and `inventory.html` were explicitly out of scope to
    rebuild, and a sheet already satisfies "never a page with its own
    chrome". So the one remaining iframe in the app is `#kit-sheet`'s, and
    both pages now hide their *whole header block* in a frame (not just the
    back link) — leaving the h1 gave "What we know" twice, once in the
    sheet's chrome and once under it. Their `data-shell-back` therefore
    changed from `"/static/kitchen.html"` to `"hide"`: the old value would
    now load the superseded hub inside the screen that replaced it.
    `tests/test_embedded_pages.py` was updated to pin the new truth,
    including its frontier regex, which read `embed:`/`forceEmbedSrc:` —
    both now gone, so it would have derived an empty set and passed
    vacuously, the exact failure that file warns about.
  - **The last two navigations out of the shell are gone.** The desktop
    rail's What-we-know/Inventory links were `<a href>` full page loads, and
    every household/preferences chat action still carried `href: '/memory'`
    (`_MEMORY_HREF_TOOLS`, whose comment said "no shell tab shows this yet
    (Kitchen's 'What we know' absorbs it in a later step)" — this is that
    step). Both now open the sheet, via `followActionHref`. Deliberately
    handled on the client rather than by changing the backend's action
    contract, since it is the screen that was stale, not the payload.
  - **`static/kitchen.html` and `static/cooker.html` are untouched and
    unlinked**, the same fallback treatment `grocery.html` got, so this
    slice reverts by restoring one `TABS` line and two entry points.
  Two judgment calls flagged rather than assumed. **InnCooker draws the
  segmented control pouring into the spruce hero**; that only works if it is
  the last thing before the hero, which it cannot be in both states (Plan
  has its framing and day rail in between). It is the plain segmented
  control the Grocery screen already shipped instead — one control,
  identical in both states. And **the mockup's "Household settings" tile was
  not built**: its "Emily and Jamie · passphrase · sharing" has no screen
  behind it anywhere in this app (households are made by a script,
  `memory.html` has no sharing UI), and the blueprint's own rule is to treat
  a mockup element you cannot find in the real page as a drawing error and
  ask. Also not written: the hero's "Six weeks in", which wants a household
  tenure nothing exposes — inventing one would be inventing history.

- **2026-09-02 — Grocery is a native shell panel, not an iframe, on the
  branch `pomona-grocery-native` (MERGED).** Pomona Stage 2 slice 2;
  built directly to the InnGrocery design rather than migrate-then-restyle.
  `static/grocery.html`'s four screens are four states of one panel — To
  buy / Plan stops / Review behind a segmented control, and shopping a
  store takes the hero over instead of being a page with its own back
  button. Same `/api/grocery-list*` endpoints; zero API changes.
  **The point of it was the refresh policy, not the layout.** Three
  mechanisms existed only because this tab was a second document, and one
  of them silently did nothing for the tab it most needed to cover:
  `refreshStaleTabsFromActions` had no `grocery` branch (it could not have
  had a useful one), `refreshGrocerySurfaces` re-pointed the iframe's
  `src`, and `refreshAfterReset` reached through
  `contentWindow.location.reload()`. All three now call one
  `refreshGroceryPanel()`. Verified by measurement, not inspection: a chat
  turn that adds an item updates the panel with the DOM node preserved and
  the scroll position unchanged at 400px.
  Five things worth knowing:
  - **`static/grocery.html` is deliberately untouched and unlinked**, the
    same treatment `grocery-legacy.html` got, so the slice reverts by
    restoring one line in `TABS`. That is also why `tests/test_embedded_pages.py`
    still passes unchanged — its `EMBEDDABLE` list tolerates extra entries,
    and the file it checks still exists with its guard. The two cleanups
    the ticket listed (grocery's back-link guard, its `href="/"`) are
    therefore NOT done; they belong with the decision to delete the file.
  - **The bespoke wide-desktop grocery layouts were not ported** — the
    blueprint scopes them out of this pass. Two things existed only there
    and are gone: the person/identity switcher that stamped `added_by`, and
    the rail's "Already got" toggle (the Done section covers the latter).
    Flagged to Emily rather than assumed.
  - **Finishing a stop now records a `shopping_trips` row on every
    breakpoint.** Only the desktop mode called
    `/api/shopping-trips/close`; the phone's "Done here" did not. The
    native screen does the richer of the two, wrapped so it can never block
    the flow.
  - **A class rule that sets `display` beats the UA's `[hidden]` rule.**
    Hiding the add card and the segmented control by setting `.hidden` did
    nothing until `.gro-add[hidden]`/`.gro-seg[hidden]` existed. This repo
    already carried `.today-tile[hidden]` and `.dinner-hero[hidden]` for
    the same reason — if you add an element you hide via `.hidden` and you
    also give it a `display`, you need the guard.
  - **Store avatar colours are all light accents now.** The hash palette
    included spruce and `#7E7360`, which put `--on-accent-ink` (near-black)
    on a near-black tile — the same failure RULE ONE exists to prevent.
    Keeping every entry light makes the rule hold by construction.
  Two quirks were carried over unchanged and are pre-existing, not
  introduced: "Elsewhere" (exclude) has no un-exclude anywhere in the UI
  (only chat can `include_grocery_item`), and the hands-free end-command
  regex matches `that's all` but not `that’s all` (curly apostrophe) — the
  regex is byte-identical to the old page's.

- **2026-09-02 — The app records what breaks (`error_events`), and
  `observability_report.py` reads it back for the morning notification.**
  Before the friend beta there was no way to learn that the app had failed
  for someone without her saying so — a crashed tool is especially
  invisible, since the assistant apologises smoothly and the turn records
  as a success. Four sources: 5xx, unhandled crashes, browser errors
  (`static/error-reporter.js`), rate-limit rejections. **Three review
  rounds found something every time, and the pattern each time was a fix
  that opened a worse hole than it closed.** Round 2: letting the public
  share pages report meant an unauthenticated write endpoint — 500 junk
  rows evicted all 10 seeded real errors, reproduced. Reverted; the two
  share pages now report nothing, tracked as its own ticket. Round 3: the
  replacement guard asked "does this request have a cookie?" when it
  needed to ask "is a household bound?" — `/login` is a *public* path, so
  any signed-in household could write unbounded rows into household 1's
  table (25 from 25 requests, measured). **The rule that came out of it:
  on a public path there is no true answer to "whose error is this?", so
  it is logged and not recorded** — a confident wrong answer sends Emily
  hunting her own share links for someone else's bug. Also: `where_`
  stores the route *pattern*, never the URL, or member names and live
  share tokens land in a table read aloud each morning; and the browser's
  `detail` is reduced to a shape (`TypeError`) rather than stored
  verbatim, because it is the one untrusted end and its text is printed
  into an agent's context.
  Two judgment calls worth knowing. **The report reads the live app over
  HTTP, not a database file** — the first version read `DB_PATH` with a
  docstring arguing a fresh clone "can read a database file", which is
  true of a local file and false of Railway's, the only one with anything
  in it. It would have printed "Nothing broke" every morning forever. It
  now needs `HOME_MANAGER_URL` + `HOME_MANAGER_PASSPHRASES` set in the
  overnight environment, and **exit 2 ("couldn't look") is deliberately
  separate from exit 0 ("looked, all fine")**. And the unit test for that
  script guessed `/api/whoami`'s key the same wrong way the script did
  (`name`, not `household_name`), so both agreed and neither noticed —
  caught only by running it against a real server, which is why there is
  now a test pinning the script against the actual routes.
- **2026-09-02 — The app is now Pomona, on the branch
  `pomona-rebrand-foundation` (MERGED). Stage 1 = foundation only,
  no screen layouts touched.** The retired palette (Oat Cream / Midnight
  Violet / Turmeric Gold / Vivid Leaf / Electric Coral) is gone, replaced by
  the brand guide's spruce `#1B3328` / ivory `#FBF6EE` / apricot `#E0915C` /
  celadon `#A9C4B0`; Quicksand→Bricolage Grotesque and Karla→Figtree, plus
  Newsreader italic as the one accent face. Review finding **#11 (three
  overlapping colour vocabularies) is closed by the same pass** rather than
  separately — every value was being re-pointed anyway, so `theme.css` now
  has ONE canonical set named after the brand guide, and the old names
  (`--plum`, `--gold`, `--midnight-violet`, …) survive only as thin `var()`
  aliases so the ~40 existing call sites keep working. Don't add new uses of
  an alias. Four things worth knowing:
  - **Dark spruce text on light accent fills is a hard rule**, and it is a
    real behaviour change, not a recolour: `--gold-ink` used to be a near-black
    brown on gold and is now `--on-accent-ink` (`#1B3328`). Nine places were
    putting `#fff` on what is now celadon — light-on-light, failing contrast
    outright — and were fixed to dark ink. A grep for `color:#fff` inside any
    rule whose background is apricot/celadon is the check if more appear.
  - **Light mode only, deliberately.** The brand guide already carries a
    tuned dark value for every token; that is why they are all defined once
    on `:root`, so dark mode is one `prefers-color-scheme` block rather than
    another sweep. It is a follow-up, not an oversight.
  - **`--urgent: #B23A22` is the one value not from the brand guide** — the
    guide has no "urgent" swatch, and urgent semantics had to survive the
    repaint without becoming a second apricot. Flagged for Emily; it is the
    assistant's pick, not an approved swatch.
  - **`HOME_MANAGER_PASSWORD`, the repo, DB names, file paths and code
    identifiers were deliberately NOT renamed** — only user-facing strings.
    Renaming the env var would have broken the deployed Railway config.
  The PWA icon, `manifest.json` and the sign-in screen were rebuilt too, and
  `CACHE_NAME` went `home-manager-shell-v3` → `pomona-shell-v4` — that bump
  is load-bearing, since the icons and manifest are the assets this worker
  still serves *cache-first*, and they changed content while keeping their
  URLs. Not yet merged; `static/grocery-legacy.html` was left on the old
  palette on purpose (dead code, linked from nowhere).

- **2026-09-01 — Multiple households, on the branch
  `multi-household-beta` — MERGED, now on `main`; the date above is when it
  was built, not merged).** The friend beta needs her own
  household with her own data, so signing in now establishes *which*
  household a session is, rather than only that the caller is allowed in.
  Each household has one shared passphrase (`app/households.py`, PBKDF2
  hash in the new `household_credentials` table); `HOME_MANAGER_PASSWORD`
  still signs into household 1 so Emily's deployment needed no migration
  and nobody was logged out. The household id rides inside the signed
  cookie (tampering breaks the HMAC and is rejected, rather than falling
  back to household 1), `auth_middleware` binds it for the request, and
  every query reads it via `household_id()` — so a route cannot forget to
  scope itself, because scoping is not something a route does.
  Deliberately **not** an account system: no users, no sign-up, no
  account UI. Household #2 is created by `create_household.py`, a script,
  because one trusted friend does not need a flow. Three latent bugs were
  fixed on the way, all invisible with one household and all real with
  two: the share link served the hardcoded household's plan under the
  token household's *name*; the eater self-service write re-resolved the
  member *by name* in the wrong household (an allergy could land on a
  same-named person in another family) and stamped the wrong household
  onto the note row; and `db._backfill_member_colors` picked "the first
  two adults" globally rather than per household. The isolation tests
  were checked by mutation rather than by being green — breaking
  `household_id()` fails 11 of them, and reverting only the share-link fix
  fails exactly the 2 share tests. An independent review agent then found
  two more holes, both now fixed and pinned by tests: a household could be
  created with `HOME_MANAGER_PASSWORD` itself (the collision guard checked
  stored credentials, and household 1's credential is the env var — so
  that household's users would have landed in Emily's, seeing her data,
  while their own became unreachable), and `plan_meal` took a
  caller-supplied `weekly_plan_id` without checking it belonged to the
  household. **Any new "which household does this passphrase open?" logic
  must go through `households.resolve_passphrase`, which mirrors the login
  route's env-var-first order — that mismatch was the whole of the first
  bug.** Same lesson as the Plan the Week review: the author had verified
  all of it and still shipped those.
- **2026-09-01 — `app/tools.py` (6,895 lines) became the `app/tools/`
  package: 20 domain modules plus `_shared.py`.** Code-review finding #8,
  done now because the multi-household work is next and would otherwise
  have had to edit one enormous file. Deliberately a *pure* refactor — no
  behaviour was changed, and that was verified rather than asserted: all
  216 definitions are AST-identical to the originals once the cross-module
  qualification is normalised away, the 120-test suite passes unchanged,
  and an 83-step end-to-end flow (plan a week → grocery → inventory →
  pre-shop → cook mode → notifications) produces byte-identical output run
  against the old file and the new package. Two judgment calls worth
  knowing: the module boundaries follow the file's own
  `# ---------- section ----------` comments *except* where those markers
  had drifted from reality (the grocery CRUD functions sat under the
  pre-shop marker), and the split does **not** attempt multi-tenancy —
  `HOUSEHOLD_ID` is still the constant 1, just imported from `_shared.py`
  instead of being defined next to the code that uses it, so threading a
  real household id through later is a change to one file's worth of
  imports rather than a 249-site sweep. The only namespace change is that
  `tools.json` / `tools.os` / `tools.get_conn` (incidental imports that
  were never API and that nothing referenced) no longer exist.

- **2026-08-31 — Plan the Week shipped (PR #3), built in the five stages in
  `design_handoff_plan_the_week/BUILD_ORDER.md`.** Approval is now a button
  on Meals rather than a sentence the assistant had to remember to offer;
  the week's answers are a first-class append-only object; the draft shows
  all 21 slots with per-slot reasons. Three product calls worth knowing,
  all made by Emily rather than assumed: the six `DECISIONS.md`
  recommendations were accepted as written; per-category meal counts now
  mean distinct meals rather than days planned (see the gotcha above — the
  spec required both "all 21 slots filled" and "four things you cook, not
  seven you don't", which only reconcile this way); and a household with no
  composition on record gets asked for the whole table in the guest panel
  rather than for extras added to a base of zero.
- **2026-08-31 — `VOICE.md` replaced the assistant's tone instruction
  app-wide, not just in this flow.** The old prompt asked for "warm,
  cheery... real enthusiasm"; the new copy is written to "never apologetic,
  never eager, never cute. No exclamation marks." Two voices on one screen
  would have shown exactly the seam the design exists to remove, so the old
  one went rather than being blended. Emily reviewed real chat replies
  before this shipped. One follow-up was needed: told it had got something
  wrong, the assistant opened with "You're right, my apologies" — an
  apology invites the household to reassure the app, which hands the work
  back to them, so there's now an explicit rule to take the correction and
  say what changed instead.
- **2026-08-31 — The conversational meal-planning interview was deleted from
  `agent.py`.** The wizard and the two question screens own those questions
  now. Two paths asking the same things could only contradict each other,
  and the flow can no longer be skipped into.
- **2026-08-31 — An independent review agent found nine issues on the branch
  before it was pushed; all were fixed.** Worth reading the commit
  (`5a70496`) — the serious one was that a night nobody is home could still
  put food on the shopping list, because the deliberate empty row was
  written *beside* a dinner the model planned against instructions rather
  than replacing it. The existing test missed it by stubbing the model into
  behaving, i.e. it tested the case that was never the risk. Two of the
  nine were regressions introduced by this same work (component-based
  households lost their day-card controls; drafting next week landed you on
  this week). This is the strongest evidence so far for the sub-agent
  verification step in `.claude/skills/home-manager-loop` — the author had
  browser-verified all of it and still shipped those.
- **2026-08-31 — `get_household_people()` and `db._backfill_member_colors`
  matched `age_group = 'adult'` exactly, but onboarding writes "Adult".**
  Neither found anybody in the real database: no adult ever got an avatar
  colour, and the desktop grocery identity switcher was permanently empty.
  Found incidentally while building the approval receipt, which needed to
  name an adult. Both compare case-insensitively now.
- **2026-08-30 — Proactive checks (`get_attention_items`, `get_expiring_soon`)
  moved from a system-prompt instruction to code.** They previously relied on
  the model remembering to call them "near the start of a conversation," a
  soft instruction easy to let slide. `run_agent_turn` now takes a
  `proactive_check` flag; `main.py` sets it when a session's last message was
  4+ hours ago, and `agent._build_proactive_check_block()` runs both checks
  and injects anything genuinely pending as a system block before the model
  ever sees the turn. Verified live: a seeded expiring item got worked into
  the reply unprompted. Not yet pushed to origin/main as of this entry —
  check `git log origin/main..HEAD` before assuming it's deployed.
- **2026-08-30 — "Start over" (self-service reset) added to the Meals tab.**
  Wiping a week's plan or the grocery list previously meant chat, or
  `reset_household.py` — an admin script that wipes *everything*
  (recipes, chores, members) and is not meant for regular use. New
  `tools.clear_weekly_plan()` loops the existing per-meal
  `_reverse_meal_grocery_contributions()` over the plan's entries rather
  than reinventing the grocery-side logic, and empties the `weekly_plans`
  row instead of deleting it so the week's dates/constraints survive and
  `_current_weekly_plan_row` isn't left choosing between an orphan and a
  new plan. Two judgment calls worth knowing: the entry point is a row in
  `.week-content` (a sibling of `#week-mobile`/`#week-grid`), because
  `#week-header` is desktop-only and would have hidden the button on the
  phone PWA; and with both resets selected the plan runs first, so the
  toast names "the grocery list" rather than a count — the count would
  read "1 grocery item" for a list that just went from 9 to 0, since the
  plan's own reversal already took the other 8.

- **2026-08-30 — Grocery list over-counted staples and fragmented
  near-duplicate ingredient names.** Every recipe using a small-use staple
  (garlic powder, olive oil) independently added a full store-bought unit
  (10 recipes → 10 jars); prep-descriptor differences ("chopped" vs not)
  blocked exact-name merging. Fixed by only writing a real quantity on a
  staple's first use per week, and stripping prep descriptors before
  merging names.
- **2026-08-30 — Chat turn could dead-end on `stop_reason == "max_tokens"`.**
  See the Known architectural gotchas entry above — root cause and fix are
  the same thing.
- **2026-08-30 — Prompt caching was not enabled on the main chat loop.**
  System prompt (~7.6K tokens) + tool defs (~15K tokens) were resent
  uncached every single turn. Split the system prompt into a frozen,
  cache-marked block plus a small uncached "today's date" block (kept
  separate so the date changing daily doesn't bust the cache), and added
  top-level automatic caching so the growing conversation history is also
  read from cache turn-over-turn. Verified with live `cache_read`/
  `cache_creation` numbers, not just by reading the code — see the
  `home_manager` logger fix above for why that verification wasn't visible
  before.
- **2026-08-30 — Voice dictation was chat-only; Stores tab had no way to add
  a store directly.** Extracted dictation into a shared
  `static/dictation.js` (Grocery/Inventory/Memory are separate iframed
  documents that can't reach `shell.js`'s copy). Added a "+ Add a store"
  control — the Stores tab is the documented onboarding replacement for
  "where do you shop" but previously had no add path of its own.
- **2026-08-29 — "This week" planning could target the wrong week late in
  the week.** Asked on a Thursday/Friday/Saturday, the chat agent would
  generate a plan starting *next* Monday instead of filling the current
  week — a real, saved plan the Meals tab then never shows, since it only
  displays the plan whose week actually contains today. Same failure class
  as an earlier "chat describes a plan the tab doesn't show" bug, just
  reached via the agent choosing the wrong week to generate for in the
  first place.

- **"Something not working?" stores prose, and that is why it is fenced**
  (Emily's option a, 2026-09-08). `feedback_reports` is the only table in
  the app holding free text from the browser end — deliberately, since the
  sentence is the whole point and a category picker would decide in
  advance what can go wrong. Everything around it stays shape-only on the
  existing rules (`_safe_client_where` for the route, `_safe_client_detail`
  for error shapes), and the prose itself is kept out of the morning
  report: `/api/observability` carries a COUNT only, and the reports are
  printed by `observability_report.py --feedback`, fenced as untrusted
  quoted text. The reason is the one in the client-error comment — that
  output is printed into a Claude agent's context under an instruction to
  act on what it reads, and free text from an untrusted end arriving there
  is an injection channel, not just a privacy question. Nothing about
  feedback is registered in `agent.TOOL_FUNCTIONS`, and
  `tests/test_feedback_reports.py` pins that, the no-prose default output
  and the fence. There is deliberately no `/api/admin/feedback`: the read
  is household-scoped like every other read here, so no view crosses the
  isolation boundary.

- **Prep days ship as a question and a view; the planner is deliberately
  untouched** (2026-09-08, from Emily 2026-09-04/09-08: "I like to do some
  prep on Sunday to make the week easier... and then do another prep
  Wednesday/Thursday depending on the week"). Two slices landed. **A**, the
  question: a seventh household rhythm fact, `prep_days`
  (`tools/rhythm.py` — `set_prep_days`/`prep_days_summary`, stored as ONE
  list-valued row rather than one row per weekday, because
  `household_rhythm`'s `weekday` column already means "a per-weekday
  override" and the household's own ORDER is part of the answer). Asked
  once, skippably, in onboarding's rhythm step; editable on What we know;
  correctable in chat via the `set_prep_days` tool. It is deliberately
  **absent from `rhythm_completeness_signals`** — a household that doesn't
  prep ahead has answered nothing wrong and must not be scored incomplete
  for it. **B**, the view: `tools/prep_sessions.py` gathers what a prep day
  already holds — a cook-ahead chain whose source night falls on it, a
  fridge move `defrost.py` already dated there, and the one new row type
  (`prep_tasks.task_type='prep_cut'`) — onto a "Prep sessions" card and a
  session screen in the Cook view. **C**, the planner, was explicitly left
  out: generation still knows nothing about prep days.
  Three judgment calls worth not re-litigating: (1) this module **gathers,
  never generates** — in particular it never re-dates a defrost row, since
  a thaw date is a food-safety answer and moving it to suit a prep day
  would be this module overruling the one that knows why the date is what
  it is; (2) the one-off ("I can't prep this Sunday") is
  `weekly_plans.skip_prep_this_week`, a flag on the PLAN, because it is
  true of one week and not of the household — `set_prep_days(...,
  this_week_only=True)` never edits the standing answer; (3) `prep_cut`
  rows are one per (description, entry) rather than one row covering
  several meals, because `meal_plan_entry_id` is singular (defrost uses it
  the same way) and a per-meal row is what lets a session say honestly
  what each cut feeds. The Cook view's own prep rail filters `prep_cut`
  out and recounts from what it shows, so the same reminder never appears
  in two cards — the same rule Today's prep tile follows for defrost.

## Deploying

Push to `main` on GitHub; Railway auto-deploys from there. CI runs the smoke
test suite on every push (added in the `hardening` work).

**Required Railway variables — confirmed set as of the `hardening` merge, but
worth re-checking if the live site ever misbehaves:**

- `HOME_MANAGER_PASSWORD` and `SESSION_SECRET` — required, or the app fails
  closed to all remote traffic by design. Since the multi-household branch,
  `HOME_MANAGER_PASSWORD` is specifically *household 1's* passphrase (i.e.
  Emily's); other households get their own, stored hashed in the database
  by `create_household.py`. `SESSION_SECRET` matters more than it did:
  the household id is inside the signed cookie, so losing the secret logs
  every household out, not just Emily.
- `DB_PATH=/data/home_manager.db` **with an actual volume mounted at `/data`.**
  If `DB_PATH` isn't set (or the volume isn't mounted), every redeploy
  silently wipes the household's database. This is a real data-loss risk,
  not a theoretical one.
- `PUBLIC_BASE_URL` — used for share links.
- A **spend cap on the Anthropic API key** in the console — worth
  double-checking this is actually in place, since it can't be verified from
  the repo. Rate limiting (`app/ratelimit.py`) caps the request rate, not
  total spend — they're not the same protection.

## A full code review already exists — read it before doing more hardening work

A 17-finding review of the whole codebase was done alongside the `hardening`
work (findings ranked, with which are fixed and which aren't):
https://claude.ai/code/artifact/0d42e9f3-4e71-401d-a4c0-1a1f1983dbf2

Findings 1/2/3/5/6/7/12/13/17 are fixed and now live on `main`.
**Still open, in case you're deciding what to work on next:**

- ~~**#8** — split `app/tools.py` into a package by domain.~~ **Done
  2026-09-01** (see Decision log) — `app/tools/` is now 20 domain modules
  and the auth/multi-tenancy work it was blocking can start.
- ~~**#9** — the shell's tabs are iframes; navigation workarounds are
  accumulating as a result.~~ **Done 2026-09-02** across two branches
  (`pomona-grocery-native`, `pomona-kitchen-cooker`, both since merged) — no
  tab is an embedded page any more, and the workarounds it named
  (`forceEmbedSrc`, the `contentWindow.location.reload()` refresh path, the
  per-page back-link rewrites) are gone with them. One iframe remains by
  choice: the Kitchen entry sheet, which hosts `memory.html` /
  `inventory.html` rather than rebuild them this pass. **#16 (two navigation
  systems) largely goes with it** — the shell no longer navigates the
  browser out of itself anywhere.
- **#10** — no shared `api.js`; **130** hand-written `fetch('/api/...')`
  call sites, and pages carry 30–60 KB of inline CSS/JS each that can't be
  cached separately from the page. **The count said "~26" until 2026-09-08;
  it is 130** — counted twice, two ways, over `static/*.html` and
  `static/*.js`, and every root-relative `fetch(` in the frontend is an
  `/api` call. Worst: `shell.js` 45, `memory.html` 19, `cooker.html` 14,
  `inventory.html` 13. The five-fold undercount matters because it is the
  number this finding is sized by, and because every one of those sites is
  origin-bound — so it is also the real cost of the Capacitor/App Store
  ticket, which cannot work until they stop assuming the app is served by
  its own backend.
- ~~**#11** — three overlapping color vocabularies in `theme.css`.~~
  **Done 2026-09-02** — closed by the Pomona rebrand pass (see Decision
  log): `theme.css` now has one canonical set named after the brand guide,
  with the old names surviving only as thin `var()` aliases. Don't add new
  uses of an alias.
- **#14** — "What we know" effectively asks the household to do data entry;
  autosave and collapse would help.
- **#15** — loading states are bare "Loading…" against a design system that
  otherwise commits to warm, first-person copy.
- **#16** — two navigation systems (client-side tabs vs. full page loads)
  that look identical but behave differently.

That review's own notes also flag: it verified the backend against a real
uvicorn server, but never actually saw the frontend rendered — broad
visual/screen-reader verification of the whole app is still an open gap.

## Immediate open items

1. **Live the Plan the Week flow for a real week before extending it.** It
   shipped verified but not yet *used* — nobody has actually answered the
   five minutes of questions on a Sunday and cooked the result. The things
   most likely to be wrong are pacing and question wording, and neither
   shows up in a test.
2. **Old plans predate the 21-slot guarantee.** Weeks generated before
   2026-08-31 can have genuinely missing slots and long full-sentence
   `reasoning` values (the draft screen expects 4–9 word phrases). Both
   render fine; they just look different from a freshly generated week.
   No migration was written for this on purpose — backfilling a "why" the
   app never actually reasoned would be inventing history.
3. **Multi-household is merged and live** (branch `multi-household-beta`,
   since merged into `main`, see Decision log). What it still does *not* do, on
   purpose, and what Emily has yet to decide: what the second household's
   first run looks like, and how the passphrase actually reaches the beta
   tester. The mechanism is there; the product answers are open questions
   on the Loop Board ticket.
4. **Two-adult identity is still a lightweight picker, not a login.**
   Approving asks which adult is present because nothing else knows. The
   "other adult was told" notification is household-wide rather than
   addressed to a person, for the same reason. Real per-person accounts
   would clean up the receipt, the notification and the intake lock at once.
5. **Recommendation enforcement** — `get_attention_items`/`get_expiring_soon`
   code-enforced at session start (see Decision log). Still prompt-only, and
   candidates for the same treatment if it proves valuable:
   `get_cross_location_duplicates`, the feedback nudge's re-surfacing cadence.
6. The still-open review findings (#9–#11, #14–#16 above) are real but not
   urgent — good candidates for "what should we work on next" rather than
   anything blocking. Note #15 (bare "Loading…" states) is now more visible
   next to copy written to `VOICE.md`.

### 2026-09-11 — Recipes: bring one in from a link. Branch `worktree-recipe-import-link`.

Loop Board (Phase 1.5, from the 2026-09-10 feature-gap research): Pomona could
generate recipes but not absorb the twenty a household already makes. There was
no recipe browser and no hand-add form — "Recipes" on the Cook root opens the ask
bar, and `tools.add_recipe` was chat-only. Now a quiet third tile on Cook's root,
"Add from a link", opens a native sheet (built on demand like the SNW sheet, not
an iframe): paste a link → `POST /api/recipes/import-url` returns a DRAFT →
review/edit every field → `POST /api/recipes/add` saves through `tools.add_recipe`,
so the ingredients are the same `{item, qty, category}` lines grocery and the
cook view already read. Nothing is stored until the household says so — the
receipt/fridge scan shape with a URL in front.

`app/recipe_import.py` is the whole thing and is written as hostile-input
handling first: the server is fetching a URL a user typed. http/https only, no
credentials, ports 80/443 only, `localhost`/`.local`/`.internal` names refused,
every resolved address must be public (private/loopback/link-local/metadata/
multicast/reserved refused, IPv4-mapped IPv6 unwrapped), the socket goes to the
address that passed rather than back through DNS (`_PinnedHTTPSConnection` keeps
cert+SNI on the hostname), ≤3 redirects each re-checked, 3 MB cap, an 8 s socket
timeout AND a 15 s wall-clock budget for the whole fetch checked between reads (a
server trickling one byte at a time never trips a socket timeout; a second
verifier caught that), plain User-Agent, and the page is only ever read as text.
A URL that makes the standard library itself raise (`http://[::1/`, a 64-char
DNS label) is a plain refusal, not a 500; a thousand-deep JSON-LD block is "no
recipe"; the page's text and title go to the fallback model inside a fence it is
told holds a web page, not instructions. Extraction order:
schema.org `Recipe` JSON-LD (plain, `@graph`, arrays, HowToSection steps, ISO
durations) first because it is exact; otherwise `agent.read_recipe_from_page_llm`
reads the visible text and the draft is marked `read_by: "model"`, which the sheet
says out loud. Ingredient strings split deterministically
(`split_ingredient_line`: "1 (14 oz) can diced tomatoes" → qty `1 can (14 oz)`,
which `_parse_quantity` reads back) and a rough `guess_category` picks a store
section. New `recipes.source_url` column (migration in `db.py`). Same "scan"
rate-limit bucket. Out of scope on purpose: images, bulk import, a browser
extension, paywalled pages, and "paste the text"/"photograph a card" (the card's
open question). 130 tests in `tests/test_recipe_import.py`, none touching the
network.

### 2026-09-08 — Taste: one hater vetoes a shared night; a solo night can overrule

Emily's rule: a dish is a shared verdict by default — if anyone eating that night
has marked it disliked, it is not served that night. If one person loved it and
another disliked it, it may be suggested on a night only the lover eats.
`app/tools/taste_verdict.py` computes the verdict per slot against that slot's
eaters (attendance-aware); `_generate_weekly_plan` hands the planner compact
verdict lines only for dishes with per-person feedback; `check_plan_conflicts`
adds a SOFT `member_taste` conflict when a plan slips (never a hard block, so the
approval gate is unchanged). UI half (whose-verdict tap, solo-night flag) is a
follow-up on the Taste UI card.

### 2026-09-08 — Snacks finally render on Meals. Branch `meals-renders-snacks`.

The other half of "snack-swap-applies": the backend fix made `get_week_menu`
return `day.snacks` (a list, same shape as `day.breakfast`/`lunch`/`dinner`)
plus `day.snack` (the first), but shell.js's own `WEEK_SLOTS` stayed the
hard-coded three and never drew them — the decision log said so plainly.
`static/shell.js` now does, in all three Meals steps, and `WEEK_SLOTS` is
left exactly as it was on purpose: it stays the three real meals so
`weekCountsLabel`/`countOpenSlots` (which read it) keep never treating a
snack as a cook or an open slot. Snacks get their own slot KEYS instead —
`daySlotEntry`'s new lookup, `'snack'` for `day.snacks[0]` (matching both
`day.snack` and the bare `'snack'` app/main.py's ChatAction already sends)
and `'snack2'`/`'snack3'`/... for the rest — so `applyPendingDayFocus`'s
existing ring selector lands on the right card with no change of its own.
Week: `weekRowHtml`/`weekSnackLineHtml` add one line per snack after the
three meal lines, grey/none dot unless `isRealCook` (source outside
leftovers/takeout AND a real prep/cook time) says otherwise — most snacks
are grab-and-go. Day: `daySnackCardsHtml` reuses `daySlotCardHtml` verbatim
per snack, eyebrow "Snack" solo or "Snack 1"/"Snack 2" when there's more
than one (`slotEyebrowLabel`), primary action "Mark eaten" unless
`isRealCook` (`slotActionsHtml`). Meal: `mealStepHtml` hides "The plate"
card for a snack with nothing to say about it (`plateCardIsEmpty` — no
food groups, no sides, no thaw task); a real-recipe snack keeps it exactly
as any other slot would. Swap: `runSwapInPlace`/`runSwapUndo` and the
Day/Meal step's meal-validity guard (`renderMealsStep`) all switched from
`day[slot]` to `daySlotEntry(day, slot)` — without that a swap or a tap
into the second snack's Meal step would have silently done nothing, since
`day['snack2']` was never a real property. Left out honestly: a hard
allergen clash's "One thing to settle" card (`weekSettleTargetSlot`) still
only searches WEEK_SLOTS, so a clash on a snack won't deep-link there yet;
and the pending-focus ring can't disambiguate WHICH snack changed (the
action card carries no index), so it always lands on the first, same as
`day.snack` already does. `tests/test_meals_week_day_meal.py` covers the
new render functions by running them under node (8 more tests, 1644 total)
rather than reading the source for the right words — the original bug was
exactly a case a source-marker test cannot catch.
