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

## Current state (as of 2026-08-31)

`main` is live on Railway. The big recent change is the **Plan the Week**
flow (PR #3), built from the handover package now in
`design_handoff_plan_the_week/` — read that directory before touching
weekly planning, approval, or the assistant's voice. Building a week is no
longer an open-ended chat: it's a nudge, two question screens
(`/plan-week`), a 21-slot draft on Meals, and an Approve button. There is
also a revisitable setup screen at `/meal-setup`. If you're picking this
repo up fresh, run `git log --oneline -15` to confirm this is still
accurate.

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
  `buildWeekPanel`/`buildTodayPanel`/etc. are guarded by
  `panel.dataset.built`, so switching tabs away and back does NOT refetch
  data. Any change made via chat (or otherwise) to an already-built panel's
  data goes stale until reload, unless something explicitly refreshes it —
  see `refreshStaleTabsFromActions()` in shell.js, which now does this for
  chat-driven changes by reading each turn's `actions[].tab` field.
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
- **2026-09-01 — Multiple households, on the branch
  `multi-household-beta` (not yet merged).** The friend beta needs her own
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
- **#9** — the shell's tabs are iframes; navigation workarounds (see the
  Kitchen back-link bug fixed earlier) are accumulating as a result.
- **#10** — no shared `api.js`; ~26 hand-written `fetch('/api/...')` call
  sites, and pages carry 30–60 KB of inline CSS/JS each that can't be
  cached separately from the page.
- **#11** — three overlapping color vocabularies in `theme.css`.
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
3. **Multi-household is built but not merged** (branch
   `multi-household-beta`, 2026-09-01, see Decision log). What it does
   *not* do, on purpose, and what Emily still has to decide: what the
   second household's first run looks like, and how the passphrase
   actually reaches the beta tester. The mechanism is there; the product
   answers are open questions on the Loop Board ticket.
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
