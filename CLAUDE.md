# Home Manager — project context for Claude Code

This file is read automatically at the start of every Claude Code session in
this repo, so it is kept SHORT on purpose: what the app is, how we work, the
traps, how to deploy. The full engineering history — every root-caused bug and
judgment call, newest first — lives in **`docs/DECISION_LOG.md`** (moved out of
this file 2026-10-05; at 24,000 lines it was being re-read by every session and
every sub-agent). Anything in code comments or tests that says "CLAUDE.md's
entry", "CLAUDE.md's Decision log" or "see Decision log" means that file. The
`design_handoff_home_manager/` and `design_handoff_shell/` directories are
UI/design-system handoff docs, not an engineering decision log.

## What this is

A household assistant app: FastAPI + SQLite backend (`app/`), vanilla
HTML/CSS/JS frontend (`static/`), no build step, no framework. An
Anthropic-Claude chat agent (`app/agent.py`, tool-calling against
`app/tools/`) plans meals, manages a grocery list, tracks kitchen
inventory, and answers "what we know" about the household. Deployed to
Railway, auto-deploying from `main` on push. Live at
`home-manager-production-4949.up.railway.app`.

**`PRODUCT_FLOWS.md` is the map of every user flow, from the person's side of the
screen** — read the entry for whatever flow you're touching. Product-level work
(walking a flow, reviewing a module, grooming the board, user feedback) is the
`pomona-product-owner` skill's job; building is the loop's.

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
  - **2026-09-21 — Evening cook nudge** (branch `evening-cook-nudge`,
    same module): one text at the START of the household's dinner window,
    to the morning text's numbers — "Tonight: Chicken Skewers — 35 min.
    Tap to start." + `/kitchen`, or "Move the chicken thighs to the fridge
    first — then Chicken Skewers." when a fridge move / prep step is still
    undone. Clock table `digest.EVENING_NUDGE_CLOCK_BY_WINDOW`: `5_6ish`
    → 17:00, `6_8` → 18:00, `later` → 19:00, `all_over` / unset → 17:30
    (`EVENING_NUDGE_DEFAULT_CLOCK`) — NOT `defrost._DINNER_CLOCK_BY_WINDOW`,
    which is when dinner lands. Once a day per person via
    `members.evening_nudge_sent_on` (household-local date, stamped on any
    send attempt; a pass with nothing to say stamps nothing and the
    90-minute `EVENING_NUDGE_LATE_WINDOW_MINUTES` is what stops the
    checking). Switch `members.evening_nudge_on` (default 1; live only
    while that adult's morning text is on), `GET/POST /api/evening-nudge`,
    rows inside the Morning text sheet. The pass
    (`run_evening_nudges_once`) runs on the SAME tick as the morning loop
    in `start_morning_text_loop` — no second loop, same Twilio gate.
    **Push is not built**: `send_evening_nudge(member, text)` is the one
    seam a push sender slots into; today it is SMS via `send_digest`.
    Silent by design on: no dinner / night off / open slot, nobody home
    (`attendance.nobody_home` or need `away`), a **reheat (leftovers or
    made-ahead) night — a reheat is a line, never a "Tap to start"** —
    and once tonight's dinner is done or `cook_started_at` is set.
    Tests: `tests/test_evening_nudge.py`.

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
Approve button; the old setup screen at `/meal-setup` was folded into
Preferences on 2026-09-25 and now redirects to `/week?prefs=open`. Read
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
entry for the whole shape. Chores still have no tab of their own. **Updated 2026-09-12 (branch
`chores-switch-and-now-card`, two Loop Board cards):** whether a house
sees Chores is a per-household switch, `households.chores_enabled` (off
by default; `set_chores_enabled.py` flips it; `/api/whoami` carries it;
`choresEnabled()` in `static/shell.js` reads it). The global
`SHOW_CHORES_ON_TODAY` constant that hid Today's "Your chores" card for
everyone from 2026-09-08 is gone. Off: no card, no `/api/chores/today`
request, no link into `/chores-setup`, the two chores routes answer
empty / 403, and the nine chores chat tools decline through one gate in
`agent.run_agent_turn` (`CHORES_TOOLS`). On: the card is back at the foot
of Now, re-cut against the tokens and the shared `.tick`. See the
decision-log entry. **Also 2026-09-12 (branch `plan-chores-toggle`):
Plan has a Meals | Chores control where the switch is on — Chores is a
state of the Plan root (`weekState.step === 'chores'`), a grouped list
(Today / This week / Coming up by rhythm) off `GET /api/chores/pending`;
see that entry.**

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

- **New code talks to the server through `static/api.js`, never a raw
  `fetch('/api…')`** (2026-09-27). `Api.json(path, opts)` for ordinary
  calls (toast + `ApiError` on failure); `Api.fetch(path, init)` when you
  need the raw Response (streaming, or your own failure handling). One
  `Api.setBase` is what lets bundled App Store screens reach the live site.
  `tests/test_api_js.py` fails if the raw-call count in `static/` goes up;
  after migrating a screen, lower `RAW_API_FETCH_CEILING` to the new count.
  api.js is signed-in only — a public page needs `app/security.py` first.

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
  **The rounding happens ONCE, on the whole line, and the per-meal ledger
  (`meal_plan_grocery_links`) holds each meal's UNROUNDED share** — see
  `recipes._ledger_share`. That is what lets
  `grocery._reverse_meal_grocery_contributions` re-derive the line from
  whoever is left when a night is dropped or swapped. Recording a rounded
  share there (which is what `_apportion` used to do) makes the recompute
  lossy: it took a line to "0" with two dinners still planned, and made the
  answer depend on which night went. Don't put a rounded number in that
  column.
  **And the mirror of that, which is easy to break: the ONE line that
  cannot be recomputed — a household's own standing want, any row with
  `source_weekly_plan_id IS NULL`, which is every hand/chat/offline/scan
  add and every staples line — must have the ROUNDED plan contribution
  taken back off it, never the raw shares** (`quantities._ledger_totals` +
  `grocery._restate_standing_want`). The ingest adds one rounded total, so
  subtracting anything smaller leaves a remainder the ceil pushes back up
  and the line climbs a little every week for ever.
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
- **A dated test seeds off the HOUSEHOLD's clock, never the process's.**
  `from conftest import household_today` (and `household_date(n)` for the ISO
  string) — the date the app's screens will actually use. Since 2026-09-14
  the screens run on `households.timezone` (default `America/Toronto`) while
  the test process runs on whatever `TZ` it was given, so
  `datetime.date.today()` is the SERVER's day and is a different day from the
  app's for four hours of every UTC day. A test that seeds with it and then
  asks a screen about "today" is asserting that the two clocks agree, which
  they do not. It composes with `--today` / `@pytest.mark.today` /
  `frozen_today` rather than replacing them — under a pin it reports the
  household's date at the pinned instant. A test that is ABOUT the two clocks
  disagreeing sets `households.timezone` itself and freezes `cooker.datetime`
  (`tests/test_moves_household_clock.py` is the model). CI's `straddle`
  matrix is what catches the next one of these.

- **`home_manager` logger's INFO output was silently dropped** until
  `main.py` added `logging.basicConfig(level=logging.INFO, ...)`. Nothing
  else in the app configures logging, and Python's default "handler of last
  resort" only surfaces WARNING and above — so any new `logger.info(...)`
  call anywhere in this app will actually show up now, but double-check
  this basicConfig call is still there if logging output ever goes quiet
  again.

## Decision log — lives in `docs/DECISION_LOG.md`

Moved out of this file on 2026-10-05 (Emily's call: the log had grown to
~24,000 lines and every session and sub-agent paid to read it). Nothing was
dropped; it was moved verbatim.

- **Before you change an area, search the log for it** — e.g.
  `grep -n -i "swap" docs/DECISION_LOG.md` — so a bug that was already
  root-caused isn't re-discovered the hard way. The traps that bite most
  often are summarised above under *Known architectural gotchas*.
- **Append new entries to `docs/DECISION_LOG.md`**, at the top of its list
  (newest first), in the same terse style: one line of fact, one line of
  why, the branch. Keep entries short — the commit holds the detail.
- **Merging.** When a merge conflicts in `static/shell.js` OR in
  `docs/DECISION_LOG.md`, resolve it hunk by hunk — keep both sides, never
  `git checkout --ours/--theirs` the whole file (the 2026-09-26 incident lost
  493 lines that way). Then, from the merged tree and before pushing, run
  `python check_merge_kept_the_log.py origin/main <branch>...` — it is the
  only check that notices a merge dropping a BRANCH's own entry.
  `tests/test_claude_md_tripwire.py` guards the log's size and newest
  entries, and that this file stays short.

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
