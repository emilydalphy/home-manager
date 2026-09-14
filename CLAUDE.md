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

- **2026-09-14 — ...and then the pre-shop card pinned the restored lines
  off the list anyway. Branch `overnight/pre-shop-covers-the-amount`
  (based on `overnight/inventory-covers-the-amount`), NOT merged at the
  time of writing.** The second half of the entry below, and the identical
  defect one step later: `pre_shop.get_pre_shop_flags` decided what "may
  already be home" with a confident NAME match plus a non-blank test, and
  then RENDERED both amounts in the sentence it showed — so the card read
  "You want 3 lbs. Fridge shows 2 lbs." while holding that line off what
  the household shops from. Measured over HTTP on a throwaway DB, the
  shop-week-1-then-approve-week-2 shape: `/api/grocery-list?status=needed`
  came back with **zero** sections and all six of the lines the approval
  fix had just restored sat behind the card, each under a sentence
  disproving it. Not data loss — one "Keep all" tap restores them — but
  until this, the approval fix delivered almost nothing visible.
  - **A flag is not a remark, which is the whole reason the amount has to
    be compared.** `app/main.py`'s "needed" views (`/api/grocery-list` and
    `/api/grocery-list/by-store`) filter flagged ids out, so an unreviewed
    flag removes the line from the Shop tab. Every ticked line writes an
    inventory row, so a week shopped normally put the next week's whole
    list behind the card.
  - **Pointed at `recipes._KitchenStock`, the class the parent branch
    introduced — deliberately the same class and NOT a second copy of the
    arithmetic.** Two implementations of "is there enough of this at home"
    is exactly the bug: the ingest would restore a line and the card would
    divert it again. Imported by the package's module-alias convention
    (`from . import recipes as _recipes`); `recipes`' module-level import
    closure does not reach `pre_shop`, so there is no cycle to resolve.
    **Say what that does and does not buy, precisely: it is the same RULE,
    read twice over one shelf — not one reading.** An earlier draft of
    this entry said the two "cannot disagree about one kitchen", which is
    false and was caught on review. Each call builds its own ledger, so
    the ingest's claims are forgotten by the time the card asks, and a
    week really can be told about the same stock twice: two recipes
    wanting 2 lbs of chicken each against 3 lbs on the shelf leaves recipe
    A skipped and a 2 lb line for recipe B — and the pre-shop check, with
    a fresh ledger, sees all 3 lbs and flags that line, so the household
    shops for nothing and goes out with 3 lbs for a 4 lb week.
    Reproduced; identical on the parent, so not introduced here. Closing
    it means PERSISTING the ingest's claims and reconciling them against
    what has since been eaten — a much bigger ticket, deliberately not
    attempted. Its own card.
  - **CLAIMING IS ON** — one reading of the kitchen per pass over the
    list, spent as it is granted. The pre-shop check is a read-only view,
    which argued for judging each line independently; the flag's exclusion
    filter is what settles it the other way. Two lines of one food can sit
    on the list at once, and two pounds on the shelf is an answer to one
    of them; flagging both takes both off the list and sends the household
    home with half of what the week wants — the same "told about the same
    two pounds twice" failure the parent branch closed inside one
    approval. Reachable through the public API, and the test uses that
    route rather than a hand-written row: `grocery._NUMBER_CHANGES_MEANING`
    keeps "Pea" and "Peas" as two lines on purpose, while
    `cooker._singularize` reads both as one thing, so both match one
    inventory row confidently. The cost is that WHICH of the two gets the
    flag follows `list_grocery_list`'s order (category, then item) — which
    need not even put the pair together, since two lines of one food can
    be filed under different sections. Stable either way, and either
    answer is defensible since the pair is one food; what matters is that
    only one comes off the list.
  - **The kitchen is asked LAST, after the card's wording guards**, because
    `covers()` spends what it grants: a line the card declines to phrase
    (an amount it can't reduce to one clause, a sentence past 60
    characters) must not quietly claim stock the next line of the same
    food is measured against. Pinned by the one test that goes red when
    the check is moved above those guards.
  - **Asked under the MATCHED ROW's name, not the grocery line's.**
    `_KitchenStock` keys on the plain stripped name while
    `_find_inventory_match`'s confident test forgives a trailing "s", so
    keying on the line ("Eggs") would silently stop asking about a row
    called "Egg" — a narrowing nobody asked for. The matched row's own
    name asks about exactly the row the sentence is about to name, and
    picks up duplicate rows of it, which `_KitchenStock` sums.
  - **`get_grocery_already_have_items` got the same gate**, one door over
    in the same file: the identical name-only check, and it is a chat tool
    (`agent.TOOL_FUNCTIONS`), so a wrong "you already have that" is said
    out loud. One line, the same helper.
  - **What cannot be compared stays on the list** — the parent's bias, an
    extra line beats a missing dinner: a freeform wanted amount ("a
    handful"), an unreadable row on the shelf, one unreadable row among
    several of a name, two unit families that don't convert. The deliberate
    cost: a line the card used to flag on its wording alone ("You want a
    bunch. Fridge shows 1 bunch.") is now bought.
  - **`_pre_shop_parse_total` is pulled out of `_pre_shop_humanize_label`**
    so the number compared and the phrase printed come from one read of
    one string; the card must never say "You want 2 lbs" about a figure it
    compared as something else.
  - **THE SAME INVARIANT, ENFORCED ON THE OTHER SIDE TOO — the fix's own
    bug, found on review of the first commit.** `covers()` sums every
    inventory row of a name; the sentence printed the ONE row
    `_find_inventory_match` returned. So a household with a pound of
    broccoli in the fridge and a pound and a half in the pantry read "You
    want 2 lbs. Fridge shows 1 lb." over a line the card had just taken
    off the list — the exact visual signature this branch exists to
    remove, arriving from the side it was not fixed on, and arbitrary
    besides: swap the two rows and it reads "a lb and a half". The
    DECISION was right (they do have 2.5 lbs); the card is the decision
    surface. Identical on the parent, but this branch is what made summing
    load-bearing, and the test asserted only that the line was flagged,
    never what it said — so the suite blessed it unseen. Fixed by
    PRINTING THE COMPARED FIGURE rather than declining the flag:
    `_KitchenStock.on_hand_total` (new, read-only, claims nothing) hands
    back the row count and the sum, rendered in the unit the matched row
    was written in so a household that tracks in pounds is not told about
    ounces. Declining would have been the tidier change and the worse one
    — it throws away a correct answer to avoid saying it. `onHandLocation`
    is dropped when the total spans more than one row, because a total
    across two shelves belongs to neither. Same on
    `get_grocery_already_have_items`, where it matters more: that one is
    read back in words, and it was reporting "1 lb" for a 2 lb line it had
    just cleared. A single row is byte-identical to before — its own
    words, notes and shelf — and there is a test saying so.
  - **A quantity written `1 (14 oz) can` is now never flagged.** It
    renders as a label but `_pre_shop_parse_total` cannot reduce it to one
    (amount, unit), so nothing can be compared and it stays on the list.
    Consistent with the bias above, and narrow: the link/photo import's own
    splitter writes that shape as `1 can (14 oz)`, which does parse.
  - **Deliberately left, both flagged rather than fixed:**
    `weekly_plan.preview_plan_grocery_impact`'s `already_have_count` still
    asks the name-only question — it works over DISTINCT ingredient NAMES
    with no scaling, so pointing it at `_KitchenStock` means computing each
    recipe-week's `need` the way the ingest does, which is a rewrite and
    not a line; and `cooker.get_cooker_view`'s `at_home` mark, whose
    amounts are per-meal COOK amounts rather than a shopping line, so it
    would want its own claim ledger across the week's meals. Both only
    mislead — neither buys nor skips. Their own cards. What DID get fixed
    is the sentence above the first one: its docstring claimed it "mirrors
    _add_recipe_ingredients_to_grocery_list's own two rules exactly", which
    the PARENT branch made false, so it now says which rule it lost and
    why it is left. And `agent.TOOL_DEFINITIONS`' description for
    `get_grocery_already_have_items` said "already tracked with a quantity
    on hand" — the behaviour is a strict subset of that now, so no
    contract broke, but the sentence was stale and is rewritten.
  - `tests/test_pre_shop_covers_the_amount.py` (24; **11 red on `0633cdd`**,
    and the two sentence tests red on this branch's own first commit too).
    The guards say so in their own docstrings, and two of them are
    mutation-checked instead: removing `_KitchenStock`'s claim ledger, and
    asking it before the wording guards. Suite **4675 passed, 1 failed** —
    the known pre-existing
    `test_a_real_swap_cannot_make_a_chat_link_open_the_new_dish` stale
    date, red on `main` too. Before/after driven over a real uvicorn on a
    throwaway DB: 0 lines to shop from and 6 flags, against 6 lines and 0
    flags.

- **2026-09-14 — Two ounces on the shelf took two POUNDS off the shopping
  list. Branch `overnight/inventory-covers-the-amount`, NOT merged at the
  time of writing.** Loop Board bug, reproduced over HTTP on a throwaway DB
  before anything was touched: add "Chicken thighs · 2 oz" to inventory,
  approve a week whose dinners want 2 lbs of them, and the approval answers
  `already_have_skipped: 1` with chicken thighs on no list at all. The
  natural shape is worse and is the one Julia hit — approve week one, shop
  it normally (ticking a line purchased writes an inventory row, which is
  what "Done at Costco" does), then approve week two: measured
  `groceries_added: 0, already_have_skipped: 2` with week two's list EMPTY,
  against `groceries_added: 2` on the fix.
  - **Root cause, one line.** `recipes._add_recipe_ingredients_for_entries`
    built `have_names` from `SELECT item FROM inventory_items WHERE
    TRIM(quantity) != ''` and skipped any ingredient whose name was in it.
    The quantity was selected on as a non-blank TEST and then thrown away;
    nothing ever compared it with what the week needed.
  - **The rule now (`recipes._KitchenStock`): skip only what the kitchen
    can be SHOWN to cover.** The tracked quantities, read in the unit the
    shopping line would be written in, must add up to at least what that
    line would say. Everything else is bought — this module's standing bias
    (`_PACKAGE_UNITS`: "an extra line beats a missing dinner"), and the one
    direction a wrong answer here is survivable in.
  - **THE STRONGEST THING ABOUT THIS CHANGE, and the reason it needed no
    regression hunt (an independent reviewer's argument, better than the
    author's): the new skip condition is a strict SUBSET of the old one.**
    The old rule skipped on the name alone; the new one skips on the name
    AND an amount that covers. So no ingredient can be suppressed that was
    not already being suppressed — every behaviour change is in the
    direction of buying more, by construction, and nothing downstream of
    the ingest can see a shape it could not see before.
  - **The figure compared is this RECIPE-WEEK's whole scaled claim, put
    through `_week_bought_amount` — i.e. what that group would actually
    buy, rounded exactly once by the same function the line is written
    with.** Not one meal's share (three 1-lb dinners want 3 lbs, and 2 lbs
    on hand is not enough). The week's TOTAL across all recipe groups —
    the strictly correct figure — is only known at `WeekGroceryBuffer.flush`,
    and moving the decision there means restructuring the two paths that
    bypass the buffer (sealed packages, freeform) plus the `already_have`
    return. So instead the STOCK IS CLAIMED AS IT IS SPENT: one buffer is
    one approval, the stock is read once and hangs off it
    (`buffer.kitchen_stock()`), and a grant deducts itself, so the second
    and third recipes of a week to want chicken thighs cannot each be told
    about the same two pounds. That gets the same answer as a whole-week
    total without a second arithmetic that could disagree with the ingest's.
  - **"It rounds UP, so it errs safe" IS FALSE, and an earlier draft of
    this entry and of the code comment both said it.** `_week_bought_amount`
    → `_shopping_round` ceils a COUNTABLE (and a counted pack), and takes a
    MEASURABLE unit to the NEAREST quarter of its display unit, which can
    round DOWN: 1.5 lbs on hand covers a 1.6 lb need, 1 kg covers 1.12 kg
    (1.13 kg is refused). Bounded at an eighth of the display unit, and it
    is the same gap the shopping line itself carries — the app would have
    written "1.5 lbs" for that need too — so the BEHAVIOUR is right and
    only the sentence was wrong. Fuzzing found one skip short by 1.1% in
    2,264 skips.
  - **A counted pack is SPENT at the pieces, not at the whole pack.** The
    first cut claimed the ROUNDED figure, which put a false line on the
    list every week garlic or eggs appeared: three recipe-weeks wanting 3,
    2 and 2 cloves each round up to a whole head, so two heads on the shelf
    were spent by the first two groups and the third bought garlic nobody
    needed (reproduced; the list read `Garlic · 1 head`). The claim is now
    the RAW need converted into the line's unit. It loosens the ledger
    slightly — a household can be told about the fraction of a pack an
    earlier group rounded away — and that is acceptable because the
    COMPARISON is unchanged: every individual skip is still decided against
    the rounded figure, so the safe direction is preserved and no skip is
    any less well-founded than it was.
  - **Several rows of one name are SUMMED, not picked between** (the "kept
    in two places" case, 2026-09-13): two rows are two rows of one food and
    "have we enough" is a question about the food. But every row must be
    readable and convertible into the needed unit — "3 lbs" beside "a bit
    left" is a total nobody can state, so it is bought.
  - **Unreconcilable means buy**: a freeform quantity on either side, a
    package against a measured amount, two unit families that do not
    convert. Conversion is `quantities._convert_to_unit`, so oz/lb,
    tsp/tbsp/cup, g/kg, ml/l and the counted packs all reconcile and
    nothing else is guessed at.
  - **TWO TRADE-OFFS EMILY SHOULD SEE NAMED, both deliberate.**
    (1) **Coverage is all-or-nothing per group.** 1.5 lbs of salmon against
    a 2 lb need buys the whole 2 lbs, not the missing half. Safe, and never
    claimed otherwise — but it means quantities run systematically high
    wherever there is partial stock, which is adjacent to Emily's own
    standing complaint that quantities are too high. Buying the difference
    would mean the ingest writing a line the ledger cannot reverse, so it
    is a bigger change than this.
    (2) **The freeform cost, measured rather than guessed.** A realistic
    five-dinner week against a fully stocked kitchen goes from **0 extra
    lines on `2120af5` to 4** on this branch: Salt, Black pepper and Olive
    oil, all three folded into "Spices this week" (spices.py gives them
    `status='spice'`), plus ONE intrusive line, `Fresh parsley`. Salt and
    pepper are freeform ("to taste"); olive oil is the package-against-a-
    measured-amount case. One real line a week is the price of not letting
    "there is some in the house" stand in for "there is enough". The
    `to taste ×3` / `a handful ×2` wording those lines carry is
    pre-existing `_repeat_or_concatenate` behaviour and is not this
    branch's doing. Reversing the freeform half is one line (the
    `else: need = None`).
  - **Name matching is unchanged** (`strip().lower()`, not
    `grocery._merge_key`): merge-key matching would make MORE ingredients
    skippable, and widening what counts as "we have it" is the direction
    this fix exists to narrow.
  - **THREE SIBLING READS STILL ASK THE OLD NAME-ONLY QUESTION, and one of
    them eats most of the visible win.** Scoped out to keep this change to
    the one function that buys, and each is a small change now that the
    rule is a class:
    `pre_shop.get_pre_shop_flags` is the one that matters. It only RENDERS
    both amounts ("You want 3 lbs. Fridge shows 2 lbs."); its own test is
    still `_find_inventory_match` plus a non-blank quantity check, i.e.
    exactly what was just replaced. And `/api/grocery-list?status=needed`
    hides a flagged line from the shop-from list, so on this branch the six
    lines restored in shape B are all diverted straight into the pinned
    "Maybe already home" card and the list reads empty. One "Keep all" tap
    brings them back, so it is not data loss — but a household that does
    not tap it still shops from nothing. Its own card.
    `weekly_plan.preview_plan_grocery_impact`'s `already_have_count` (the
    draft screen's "I'll build it — N items") now UNDER-promises what
    approval adds, and `cooker.get_cooker_view`'s `at_home` mark can say a
    meal's ingredient is at home when there is a tenth of it. Neither buys
    or skips anything.
  - `tests/test_inventory_covers_the_amount.py`, 20 tests, **11 red on
    `2120af5`**; the green ones say in their own docstrings that they are
    no-regression guards. Five mutations checked to bite: exactly-enough
    against a strict `>`, the sum against taking one row, the claim ledger
    against not claiming, the counted-pack claim against claiming the
    rounded figure, and the comparison against using the raw need. Suite
    4651 passed, 1 failed — the known pre-existing
    `test_a_real_swap_cannot_make_a_chat_link_open_the_new_dish` stale
    date, red on `main` too.
- **2026-09-14 — Now was about the wrong day for four hours every evening:
  moves.py read the SERVER's clock, and so did the card Now answers with.
  Branch `overnight/moves-household-clock`, NOT merged at the time of
  writing.** The deployed container runs in UTC
  (the Dockerfile is `python:3.11-slim` and sets no `TZ`) and
  `households.timezone` defaults to `America/Toronto`, so from 8pm Toronto
  the server's date is already tomorrow. `moves._as_date(None)` was
  `date.today()` and the three `now = now or datetime.now()` defaults were
  the server's clock, and neither `/api/today/moves` nor `static/shell.js`
  passes a date — so Now's whole timeline answered about TOMORROW while
  `/api/today/tonight`, which has read the household's clock since it
  shipped, still said today. Two cards on one screen disagreeing about what
  day it is, at dinner time. Worse, from the same payload: Now offered a
  cook and `POST /api/cooker/start` — which already goes through
  `cooker.household_now` — refused it, "That's Monday's — I'll note the
  start when you cook it Monday." Reproduced over real HTTP on a throwaway
  DB in BOTH directions (household behind the server, and a server whose
  own TZ is behind the household).
  - **Resolved at the three entry points, not in `_as_date`, and the clock
    is read ONCE.** `today_moves`, `moves_for_day` and `featured_move_id`
    take `now = now or _household_now()` and then `target = _as_date(day)
    if day is not None else now.date()`. `_as_date` stays the pure parser
    with its server-date last resort: it is a shared helper, and making it
    household-aware would have put a DB read behind every date string this
    module parses. `today_moves` hands both `day` and `now` down to
    `moves_for_day` and `featured_move_id` (including the recursive
    "tomorrow" call), so one screen costs exactly ONE read of the zone
    however many moves the day holds — pinned by a test that counts the
    calls. `app/main.py` needed no change at all.
  - **The day follows the `now` a caller passed**, rather than being
    resolved separately: a caller holding one clock must never be answered
    about another day. `tests/test_moves.py` builds its `now` off the
    server's own today, so nothing there moves.
  - **`_household_now` is `cooker.household_now`**, the one reader of
    `households.timezone` for this (module alias `_cooker`, already
    imported — no new edge in the domain graph, per the package's
    circular-import convention). It is called only outside any write
    transaction. Anything raising falls back to `datetime.now()` and logs:
    a zone nobody can read must never leave Now blank.
  - **`digest.build_morning_text` is untouched and unchanged** — it has
    always passed `day=now_local.date(), now=now_local`, i.e. the morning
    text was already right and the screen was the half that was wrong.
    Explicit arguments still win over everything here.
  - **THE OTHER HALF OF THE CLOCK, and moving only the timeline REOPENED a
    closed bug — found by review, fixed on the same branch.** Now's
    "Tonight needs a dinner" card carries a DATE, `static/shell.js` posts
    that date back verbatim, and the meal is written on it — so a card on
    the server's clock over a timeline on the household's is
    "a dinner answered on Now is saved and invisible"
    (`overnight/needs-you-dinner-invisible`, 2026-09-13) coming back from
    the other side, and strictly worse than `main`, where both halves were
    wrong TOGETHER and so at least agreed. Measured live at the production
    shape: card `2026-09-14`, timeline `2026-09-13`, the answered dinner on
    neither screen. Three reads in `weekly_plan.py` moved onto the same
    clock through a new `_household_today()`:
    `get_needs_you_items` (the card's own day, and the holiday ask's);
    `unplanned_meals_ahead` (its window never looks back, so the server's
    date DROPPED a loose meal saved on the household's own evening — the
    same bug wearing the no-plan hat); and `resolve_needs_you_dinner`,
    which now resolves the plan by `get_plan_id_for_date(meal_date)` — the
    app's own "which plan does this day belong to", which reads no clock at
    all — instead of asking which plan is *current* and then checking
    coverage. The 2026-09-11 rule it was written for (never attach a meal
    to a plan whose period misses the date, or `plan_meal` 500s the tap) is
    unchanged and now holds by construction.
    `_household_today` imports `cooker` INSIDE the function: `cooker`
    imports `weekly_plan` at module scope, so a top-level import here would
    be a cycle — the same lazy shape `moves._today_holiday` already uses.
  - **Deliberately left out:** `tonight.py` still carries its own copy of
    the same UTC→household conversion (a third, after `cooker` and
    `digest._zone`) — folding those into one is a real tidy-up and not a
    bug fix, so it is its own card.
  - **A pre-existing one, stated plainly rather than softened, because an
    earlier draft of this entry did soften it:** `cooker.get_cooker_view`'s
    stale-plan check compares `period_end_date` to the SERVER's
    `date.today()`, so for the hours the two dates differ a household with
    an approved plan and a dinner tonight gets `moves: []` and
    `week_state: "none"` from `/api/today/moves` — an empty Now, not merely
    a plan that "reads as stale". Identical on `main`, unchanged by this
    branch, `cooker.py`'s to fix; filed as its own card.
  - `tests/test_moves_household_clock.py` (19; **11 of the first 13 red on
    `2120af5`**, and **5 of the 6 needs-you ones red on this branch's own
    first commit**, which is where that regression lived). The clock is
    frozen by swapping `cooker.datetime` for a subclass whose `now()`
    answers one fixed UTC instant — the zone lookup, the
    `households.timezone` read and the ZoneInfo fallback all run for real,
    and `date.today()` is left real so the two genuinely differ inside one
    test exactly as they do in production. One test walks all 24 UTC hours
    in both zone directions asserting `today_moves` and `tonight_check`
    name the same day; another answers the card with the date it carries
    and looks for the dinner on the timeline. Suite **4651, 4650 passing**,
    the only failure the known pre-existing
    `test_tap_a_meal_opens_recipe::test_a_real_swap_cannot_make_a_chat_link_open_the_new_dish`.
  - **THE TEST SUITE NOW HAS A CLOCK IT DID NOT HAVE BEFORE, and this is
    the thing to read before the next change here.** `tests/conftest.py`
    creates its household with `households.timezone` at its column default,
    `America/Toronto`, while the test process runs in whatever `TZ` it is
    given — UTC on CI. Every test that builds a date with
    `datetime.date.today()` and then asks a screen about "today" was
    therefore asserting that the SERVER's day and the HOUSEHOLD's day are
    the same day. That was true of the code until now and is not true of it
    any more. Measured, whole suite, under a genuinely straddling
    `TZ=Pacific/Niue`: `main` 2 failed / 4630 passed, this branch 23 failed
    / 4628 — **21 post-only failures, all but ONE of them a test seeding by
    the process's date** (`test_needs_you_dinner_visible` 13,
    `test_tools` 4, `test_draft_waits_for_approval` 2, `test_cook_shelf`
    1, `test_morning_text` 1, plus the two `test_moves.py` route ones
    fixed here). At the default UTC they are green — **except between
    00:00 and 03:59 UTC**, which is the same four Toronto evening hours
    this branch is about. Two of them, the `test_moves.py` route pair, are
    fixed here by naming the day (`?date=`), which is what their claims
    were always about. **The remaining nineteen are fixed by ONE LINE, and
    it is in the CI workflow rather than in any test**: the runner now sets
    `TZ: America/Toronto`, the app's own default household zone, so the
    process and the household it is testing agree about what day it is.
    Measured the same tree three ways on 2026-09-14 — `TZ=Pacific/Niue`
    23 failed; unset (UTC) 0 failed, but only because it ran outside the
    00:00–03:59 window; `TZ=America/Toronto` 0 failed, and that one holds
    at any hour rather than by luck. It is the RUNNER's clock only and is
    emphatically not a claim that the app may assume Toronto — the app
    reads each household's own zone, which is the entire point of this
    branch. **The pin fixes CI, not local dev** — a developer whose machine
    is not on Toronto time still sees up to 23 failures at the wrong hour
    with nothing explaining why, so run `TZ=America/Toronto pytest` if the
    suite ever goes red in a way that makes no sense against the diff.
  - **ONE of those 21 was NOT a harness artifact, and calling them all
    artifacts was wrong** (found on re-review, corrected here).
    `test_needs_you_dinner_visible.py::TestWhichLooseMealsCountAsThisWeeks
    Cooking::test_the_horizon_matches_what_the_assistant_can_talk_about`
    compares two APP functions to each other — the seeding only decides
    which meals exist — so it cannot be a seeding artifact by
    construction. It was red because this branch moved
    `unplanned_meals_ahead` (what the SCREEN can show) onto the household's
    clock and left `get_meal_plan` (what the ASSISTANT can name) on the
    server's, which opened a one-day sliver where chat could name a loose
    meal seven days out that no screen drew — for the same four hours a
    day. That is precisely the gap the 2026-09-13 entry below says that
    test exists to close, reopened a day wide. Fixed rather than
    documented: `get_meal_plan` reads `_household_today()` too, which its
    own comment had been asking for ("the honest fix for that is storing a
    household's timezone"). Measured directly rather than through the
    suite, household a day behind the server, loose dinners seeded across
    the horizon: **named-but-invisible was `['Day8']` before the fix and
    `[]` after.** **The lesson worth keeping: when a clock moves, every
    window that has to COINCIDE with it moves in the same commit — a
    half-converted app is a new bug, not a smaller one.**
  - **That fix RAISES the straddling-TZ artifact count, from 23 to 29, and
    that is the expected direction rather than a regression.** `get_meal_plan`
    is read by many more tests than `unplanned_meals_ahead` is, and every
    one of them seeds its dates from the process's `date.today()` — so
    moving the function onto the household's clock turns each into the same
    seeding artifact as the other nineteen. None of the six is an app
    failure: the app-level gap the change exists to close is closed, by the
    direct measurement above. At `TZ=America/Toronto` — what CI now runs —
    the whole suite is **4650 passed, 1 failed**, that one being the known
    pre-existing stale-date test. The artifact count is a property of the
    harness's seeding, not of the code, and the card for re-seeding those
    tests off the household's clock is the thing that takes it to zero.
  - **The obvious fix was tried first and is wrong; recorded so nobody
    re-tries it.** Putting the test household on the process's own clock
    in `tests/conftest.py` (deriving the zone from `TZ`, else
    `/etc/localtime`) does take `Pacific/Niue` from 23 failures to 14 —
    but it breaks **four `test_morning_text` tests at the DEFAULT TZ**,
    because `America/Toronto` is the product's real default and those
    tests correctly assert it. Aligning the runner instead leaves every
    test's meaning untouched. The shift form above is what keeps this to the tests
    that hard-code a date rather than every one that pins a clock: with
    it, the dozen files patching `weekly_plan.date` still control the
    answer whenever the two clocks agree, which is every hour but four.
  - **An earlier version of this entry claimed "identical failure sets
    under a straddling TZ" and that was wrong** — it was measured under
    `TZ=America/Anchorage`, which is UTC−8 and only straddles when the UTC
    hour is under 8; at the hour it ran, it was not straddling at all.
    `Pacific/Niue` (UTC−11) is the one to use. Left written down because
    the mistake — checking a timezone property in a timezone that happened
    not to exercise it — is easy to repeat.

- **2026-09-13 — Hosting a holiday is THE BIG MEAL now: a menu, the shop
  in two trips, the prep on the days before, a day-of timeline. Branch
  `worktree-holiday-hosting`, slice 2 of Loop Board "Holidays: Pomona
  knows 12 October is coming and asks how you're spending it".** Slice 1
  (below, 2026-09-11) made "Hosting" the intake's guests tag + headcount
  and asked the planner for a generous dinner; this makes it a menu.
  **Where it lives — nothing new invented.** A dinner is ONE
  `meal_plan_entries` row per (date, slot) — `audit_plan_slots`, the
  leftover chain and both screens depend on it — so the big meal is one
  dinner entry: the MAIN is its recipe (written for the whole table,
  `default_servings` = eaters), the SIDES and the SWEET ride in
  `sides_json` — plates.py's "this dish, on this night" column — each
  with a `role`, the day-of timing (`minutes`, `cook_minutes`, `oven`,
  `ahead_days`) and `servings` = the table it was written for, which the
  ingest anchors on the way it anchors on a recipe's default_servings
  (`weekly_plan._entry_side_groups`) so a dish for seven is
  bought once for seven and scales by attendance from there. Every
  reader of `sides_json` already treats a side as part of the entry:
  approval buys it under the same entry id, the Cook view lists its
  steps "Alongside", `check_meal_conflicts` reads its ingredients,
  `plate_note` says "with X, Y and Z" on the Plan card. The alternative
  — one entry per dish under non-canonical slot names — was rejected: it
  hides from the duplicate audit rather than passing it, and every
  per-slot reader would meet a slot it doesn't know. **The menu's own
  record** is `holiday_answers.menu_json` (the entry it built, whether
  it ADOPTED the household's own dinner or PROPOSED the main, the prior
  reasoning, the main's timing, status) beside two hosting-only columns,
  `on_table_at` and `guest_notes` — the smallest honest capture of guest
  restrictions, which existed nowhere (guest_counts is two integers):
  free text in the host's words (≤160 chars), read verbatim by the
  proposal and parsed by the SAME allergy matcher a hard What-we-know
  fact goes through (`big_meal.guest_avoidances` →
  `coordination._fact_keywords`), with people's names — members,
  possessives, mid-sentence capitals — dropped from the match terms so
  "no nuts for Grandma" never matches "Grandma's rolls". "Sam's
  vegetarian" is honoured by the model, which the matcher can't do.
  **The proposal** is one model call, `agent.generate_big_meal_llm`
  (`utility` effort, forced `submit_big_meal`), injected at call time
  like `complete_plate`'s side generator so tests stub it. It builds
  AROUND the dinner already in the slot (the planner's pick at
  generation via `apply_to_plan`, or whatever the household planned —
  ADOPTED, never replaced) and proposes a main only for an empty/open
  slot. EVERY dish is checked against the household's restrictions and
  the guests' notes, the main included: a proposed main that clashes is
  proposed again once with the clash named (`avoid` in the context),
  then the slot is handed back open with the reason; an adopted main
  that clashes is SAID, never touched (`conflicts`). It degrades, never
  crashes: main-only with a note when the sides can't be had; an open
  slot naming hosting and the count when nothing can. **The menu follows
  the answer:** a hosting answer given again (new count, time, note)
  KEEPS the menu and `refresh_menu`s it in place — rescale, re-check
  every dish, drop what clashes, one proposal for replacements of the
  roles lost — rather than rebuilding, so dishes changed by hand
  survive; `propose_big_meal` is the deliberate do-over. The count
  reaches it from anywhere: `attendance.set_slot_attendance` calls
  `big_meal.on_attendance_changed` (a no-op for every ordinary meal),
  which is what the intake's steppers and `set_guest_count` go through;
  `answer_holiday` suppresses that hook while it writes and refreshes
  once at the end. **The Days screen** records the answer on the tap
  (`build_menu: false`) and builds on its Save, with the count, the time
  and the notes together; what was left off and why comes back as one
  sentence (`big_meal_said`: "Left off the walnut salad — no nuts. Added
  apple crumble instead.") and is shown under the answer. **A trip
  already covering the day wins** (slice 1's rule): `build_menu` checks
  attendance itself and builds nothing for a nobody-home dinner, so every
  caller gets the same answer. **The shop split** is read-time, not
  stored: `big_meal.shop_split` joins the entry's `meal_plan_grocery_links`
  and labels a `needed` line early if it keeps — by store section, and
  for a line in "other" (a recipe that named no section) by
  `PERISHABLE_WORDS`, so raw chicken and fresh thyme are fresh and foil
  keeps — or if another meal needs it before the FRESH trip (Saturday's
  onions can't wait for Sunday); the two grocery routes stamp
  `shop_timing` per line and a `shop_split` with the trips' labels, and
  the Shop screen's store cards read their aisles in groups under one
  quiet heading each ("For Thanksgiving — buy by Friday"). Read-time
  means nothing to sync at approval/swap/reversal and nothing to unwind.
  **The prep spread** is `prep_tasks` rows with `task_type='holiday'`
  (its own producer, deleting only its own rows): one per make-ahead
  dish on holiday − `ahead_days`, pulled onto the household's standing
  prep day when one falls in the three days before, plus the two shops
  (related_meal `Shop`, which moves.py renders as a shop and lets stand
  in for the week's own "Shop before tomorrow" that day, so Now never
  asks for one trip twice). **Written only once the week is approved** —
  `approve_weekly_plan` calls `spread_prep_for_plan` — because approval
  is when a week becomes real and its shopping exists; a draft's menu is
  a proposal. Each row is dated into the plan whose period holds THAT
  day (a Monday holiday's Sunday belongs to the week before) and
  `cooker.get_prep_schedule` returns rows dated into the plan's period
  by another LIVE plan (draft/approved — a retired plan's "chop onions"
  for a Chili no longer planned must not surface in its replacement) and
  its own entries' rows wherever dated, and DROPS any row whose entry is
  gone: `prep_tasks.meal_plan_entry_id` carries no foreign key (added by
  ALTER), so a read-time guard is the one place that covers every delete
  path, past and future; `clear_plan_slot` also deletes as it goes, as
  `_replace_slot_entries` already did. **The timeline**
  (`big_meal.timeline`, chat `get_big_meal`) works back from
  `on_table_at` (the household's dinner clock when unsaid, and it says
  so; a bare hour 1–11 reads as the evening — "6" is dinner, not dawn;
  "12" is noon; a colon or am/pm is taken as written): the main rests,
  goes in and starts before that; one oven, two racks — a day-of oven
  dish fits the rest window, else shares the oven with the main once
  ("with the main" / "the main joins it"), else goes in before and is
  kept warm; made-ahead dishes warm through in the last half hour or
  come out of the fridge; steps are ordered by the real datetime and one
  that falls before the day says "the evening before, 11:00 pm". Nothing
  is more precise than the recipe times it was given. **Drawing it on
  the Cook tab is deferred** until Cook-D ("the shelf",
  `worktree-cook-shelf`) merges — that session owns the Cook tab's
  rendering and the meal screen, and a timeline card built against
  today's Cook would be rebuilt the week after. **Unwind:** leaving
  hosting takes the count off first, deletes the holiday prep rows, then
  — for a PROPOSED main — hands the dinner back as an open question
  (slice 1's `_reopen`, groceries reversed once); for an ADOPTED dinner
  gives it back as it was (sides off, marks off, its own reasoning back,
  its shopping re-bought for the household alone); a dinner the
  household re-planned by hand carries no mark and is left alone; the
  split, being read-time, is simply gone. **A dinner moved to another
  night** (main's `swap_dinner_nights`) is the household's move:
  `menu_entry` looks the entry up by the id in `menu_json` and, finding
  it off the holiday date, treats the menu as gone — prep rows deleted,
  the mark taken off the moved dinner, the record cleared — never
  followed; "out" then empties the holiday's dinner and re-hosting
  builds one fresh menu. **Also fixed while here:** a leftover-chain
  source's SIDES are no longer scaled by the batch
  (`_add_recipe_ingredients_for_entries(chain_scale=False)` on the two
  side ingests — a reheat night buys nothing new for a salad);
  `_run_migrations` skips a table that schema.sql creates but this
  database doesn't have yet (an ALTER on a missing table aborted every
  migration after it — caught by the chores snapshot test) with a loud
  warning, and still raises for a table schema.sql never creates (a
  misspelling); `answer_holiday` and the big-meal tools are `_WEEK_TOOLS`
  (kept on one line — `test_week_seven_tiles` reads the source). **The
  verifier's catches (2026-09-13), each now a test:** the trip-covered
  day got a turkey (#1); an adopted dinner was reopened on leaving
  hosting (#2); the Days screen built on the tap with no count or notes
  and nothing read `dropped`/`conflicts` (#3); a headcount change changed
  nothing (#4); the proposed main was never checked — a shrimp boil
  landed on a shellfish-allergic table (#5); a hand swap left orphan prep
  rows (#6); a retired plan's prep surfaced in its replacement (#7); the
  timeline sorted by clock string and wrapped past midnight (#8); "6"
  read as 06:00 (#9); Saturday's onions went on the fresh trip and
  "other" defaulted to early (#10); Sunday asked for the shop twice
  (#11); a reheat scaled the sides (#12); names in guest notes were
  match terms (#13); a moved dinner was reopened (#14); a draft wrote
  prep rows (#15); raw tokens in copy (#16); a misspelled migration table
  went silent (#17). **Round 2 (same day):** `chain_scale=False` had
  reached every plate side, so an ordinary Tuesday-cook/Thursday-reheat
  bought its rice once — it is now only a big-meal dish (one carrying
  `servings`) that skips the batch; `_people_words` had read any
  mid-sentence capital as a name, so "No Nuts" / "NO NUTS" / "No
  Peanuts" produced no match terms and peanut noodles landed — a word
  the matcher knows as food (allergen families, aliases, the perishable
  table, plural twins) is never dropped, and all-caps is emphasis, not a
  person; common produce joined `PERISHABLE_WORDS` (cranberries and
  sprouts left in "other" go on the fresh trip); a gone menu says "The
  big meal isn't on the plan any more — say hosting again" rather than
  claiming the week is missing; `said` says one clash once;
  `reset.clear_weekly_plan` deletes prep rows keyed to its entries
  wherever dated, not just its own plan's. **ASSUMPTIONS (constants at the top of
  big_meal.py):** one main + 3 sides + 1 sweet; early trip 3 days out,
  fresh trip the day before; pantry/frozen/other keep, "other" by the
  perishable word table; a dish can be made up to 2 days ahead; the
  make-ahead fallback is a word table (sauces, stuffings, casseroles,
  pies, rolls keep; salads/roasts don't; a sweet keeps); default
  on-the-table time = the household's dinner clock; a standing prep day
  in the window takes the make-ahead work; 30 min to warm a made-ahead
  dish through. **Not done, own cards:** a leftover chain whose SOURCE
  night is emptied (out, holiday change, a cleared slot) leaves the
  reheat night as an orphan "cook" that buys nothing — pre-existing
  (slice 1's out did the same); per-line override of early/fresh; a Now
  card for the hosting details (the Now tap still points at chat for
  count/time/notes); the Cook-tab timeline. 53 tests in
  `tests/test_big_meal.py` (65 after round 2); suite 3345 → 3377 on the
  branch, 3912 on the merge with main (`b4f69c8`); verified live on a
  throwaway DB with the model stubbed.
- **2026-09-13 — Cook: the real start time moves the clock (and says so
  once). Branch `worktree-real-start-time`, merged 2026-09-13 evening.** Emily: "if the user ends up starting at a different time it
  should auto connect to whatever time it is for them and update the done
  time accordingly too. And it can make a little pop up note that it
  adjusted for actual timing." Every clock was PLANNED only — moves.py
  worked the start back from the dinner hour, the Meal step's stops did the
  same arithmetic, and cook mode's "Start cooking" wrote nothing down. Now:
  `meal_plan_entries.cook_started_at` (household-local naive ISO, the same
  clock the slot times are in; migration in `db.py`), written once by
  `cooker.start_cooking` through `POST /api/cooker/start {entry_id}` —
  `COALESCE` so the first tap wins under two threads, answers the refreshed
  cooker view plus `started_at` / `on_the_table` / `planned_start` /
  `already_started`, 404 for another household's entry. `get_cooker_view`
  carries `cook_started_at` per card (a component batch: the earliest
  sibling's); Now's cook move opens at the real start, its chip reads
  "Started 6:02" for "Start by 5:45", `time_label`/`detail` carry the new
  table time, and it grew `started_at` + `planned_start`. Shell:
  `cookStartCooking` enters the steps at once and posts in the background
  (`cookRecordStart`); ONE toast, once, only when the start is ≥2 minutes
  off the plan ("You started at 6:02, so the clock moved. On the table by
  6:47.", 6 s); a failure is one calm line. Readers: the cook hero's chips
  ("Started 6:02" in the new celadon `.cook-meta-chip.is-live`, "On the
  table 6:47", on every stage), the Tonight card's tiles (STARTED / ON THE
  TABLE) and line ("Started 17 minutes late — the clock's moved with
  you."; nothing inside 2 minutes), the Meal step's hero/stops/dock
  (`mealClockStops` takes `household.startMinutes` and rebases every stop;
  "Everything out" at the exact minute, no step rounded before it; the
  dock says "Keep cooking"). "Mark not cooked" clears the column and every
  reader falls back to the plan. **Judgment calls:** (1) ONE total for
  every clock — `cooker.cook_total_minutes` (prep + cook, or the longest
  side's minutes) is now what moves.py adds up too; until now Now said
  "Start by" from prep + cook alone while the Meal step counted the side,
  so a dinner with a 25-minute side could show two different starts. A
  household with such a side sees Now's start move earlier by the
  difference. (2) The comparison is by clock, not by date: a meal started
  on a different day than it was planned for is "late"/"early" by
  minutes-of-day only. (3) `mealCookUnderway` is still keyed to ticks (the
  ticklist resumes from them); the Meal step's dock reads the real start
  as well, so a cook begun is never offered "Start at". 35 tests in
  `tests/test_real_start_time.py` (backend + node harnesses); full suite
  4430, 4429 passing. Smoke-tested against a copy of the live DB: the migration adds
  the column, start/idempotent start/404/clear-on-untick all answer as
  designed. Pre-existing, not this branch's: `test_needs_you_dinner_visible
  ::TestABrandNewHouseholdWithNoPlanAtAll::test_the_shop_move_can_see_it_too`
  fails on main too after 6:30 pm local (it calls `today_moves()` with the
  real clock and the shop move for tonight's dinner has closed).
- **2026-09-13 — The Identity Round, built: the mark and "Pomona" open
  every root band, the chat button is the mark, Plan's glyph is the week
  as a row, one 2.2px stroke for the icon set. Branch
  `worktree-identity-build`, NOT merged at the time of writing.** Emily's
  four picks from the Identity Round canvas (Tier 3 on the "where does the
  mark live" question, Tier 2 on rule 7 and the §5/§6 rows — DESIGN_SYSTEM
  updated in the same commit). **Q1 = C, B in reserve:** `BAND_IDENTITY`
  in `shell.js` (`'wordmark'` default | `'mark'` | `'none'`), one
  `rootBandHtml` for all three. Under `'wordmark'` the band's top-left is
  `markSvg()` (20px, stroke 1.8, `--apricot-light`) + "Pomona"
  (`.root-band-wordmark`, display 15/700/‑0.02em, `--ivory-ink-muted`);
  the eyebrow span stays in the DOM hidden, holding the date, and
  `bandSubText` folds it into the sub-line with " · " — the sub-line's own
  words live on `data-sub` so `setRootBand` can change either half
  without losing the other. Shop's eyebrow (`groBandEyebrow`) and Cook's
  (`cookBandEyebrow`, new) carry the whole date under `'wordmark'` only
  ("Sunday, Sep 13 · 60 things · 1 stop", "Sunday, Sep 13 · 1 cook
  tonight"); under `'mark'`/`'none'` they are what they were ("60 things ·
  1 stop", "Sunday"). Now's date format moved into one `bandDateLabel`.
  **Q2 = B + a label:** `shell.html`'s `#chat-fab` holds the mark (26px,
  1.8, `--apricot-light` on spruce) and a hidden `#chat-fab-label` "Ask";
  `placeFabLabel` shows it (mark drops to 22px, `.has-label`) for the
  first `FAB_LABEL_VISITS = 3` page loads on the device, counted on
  localStorage key `pomona.fabLabelVisits` — the coaching counter's
  mechanism, its own key (per device, not per household or tab; 0 =
  never, `Infinity` = always). `aria-label`/`title` unchanged. **Q3 =
  B:** `ICONS.week` (five `fill="currentColor" stroke="none"` dots on
  `cy=12` between two rules); `ICONS.plate` deleted — `TABS` was its only
  reader. **Q4 = B:** every inline stroke SVG in `shell.js`, `shell.html`,
  `login.html`, `onboarding.html`, `plan-week.html`, `inventory.html` is
  `stroke-width="2.2"` (the other four listed pages draw no SVG). Before:
  76 stroke SVGs — 4 marks and 72 icons, of which 27 were already 2.2;
  43 were re-weighted (from 1.9, 2, 2.1, 2.3, 2.4, 2.6, 2.8 and 3), the
  chat bubble (2.1) was replaced by the mark and the bullseye (2) retired,
  and 3 gained the missing `stroke-linejoin` (the review tiles' +/−/grip).
  After: 77 stroke SVGs — 71 icons all at 2.2, and 6 drawings of the
  mark left at 1.6/1.7/1.8 as drawn. No glyph's geometry broke at 2.2 — the two
  near-zero paths that draw a dot with their round caps (`info`'s
  `M12 7.5v.01`, `SNW_ICON`'s `M12 15.4h.01`) and the ⋯ circles
  (`r="0.6"`) just get a hair smaller. Guards: `tests/test_design_hygiene.py`
  (d) fails any stroke SVG in those files that is not 2.2 unless it draws
  the mark (`M12 20.4c-3.1`, or `MARK_PATHS` for the shell.js builder),
  and checks round caps/joins; `tests/test_identity_build.py` (17) runs
  the band under all three constants, the date-in-the-line for each root,
  `setRootBand` keeping the date across partial updates, the FAB counter
  (visits 1–3 show, 4 doesn't, 0/`Infinity`, a throwing localStorage),
  Plan's paths, and the sweep's numbers. Full suite 4255. Sandbox-verified
  on a copy of the pre-reset backup DB (a loose dinner added for today so
  Cook had a cook): all four roots at 375 light and dark and at 1280, the
  FAB with "Ask" on visit 1 and bare on visit 4 (same 54px box, nothing
  around it moved), Cook › Before you start with a ticked row.
  - **Judgment calls, for Emily.** (1) Under `'wordmark'` a root's line
    is never empty — Now on a quiet day reads just "Sunday, Sep 13" where
    it used to have no line; the band grows by that one line on those
    days. (2) Now's error line becomes "Sunday, Sep 13 · Couldn't check
    just now — pull to refresh." (3) Plan | Chores keeps the chores'
    week label as its line ("Sep 14–20"); Plan's custom-period band reads
    "5 days · a draft, your turn" under its date-range title. (4) The
    FAB visit is one per page load of the shell (the coaching counter
    counts per tab activation); switching tabs never flips the label
    mid-session. (5) The "Ask" label is `aria-hidden` — the button's
    accessible name stays "Chat with Pomona". (6) The welcome screens'
    two large marks (1.6) were treated as the logo too, not swept.
    (7) The dots' fill="currentColor" shapes were left with
    `stroke="none"`, so the sweep never fattened a filled dot.

- **2026-09-13 — One wrong-typed stored answer stops a What we know
  section from opening. Branch `worktree-kitchen-fallback-and-memory-types`,
  NOT merged at the time of writing.** Loop Board bug: `POST
  /api/memory/edit` and the `edit_preference` chat tool (same Python
  function, `app/tools/memory.py`'s `edit_preference`) accepted a bare
  string for a list-valued field (`cuisine_preferences`, `dislikes`,
  `usual_stores`, `kitchen_kit`) with no validation and stored it raw;
  `GET /api/memory` then handed the string straight back instead of a
  list, and any frontend reader doing `.filter`/`.join`/`.map` on it threw
  (one spot, `prefsCuisineForms`, had already worked around this on its
  own — the rest hadn't). Fixed on both sides of the same file: the write
  side (`_coerce_str_list`) comma-splits a bare string into a list
  (`"Thai"` -> `["Thai"]`, `"Thai, Mexican"` -> two items — decision:
  comma-split, not always-one-item, since a model is more likely to send a
  plain list that way than as real JSON) and refuses anything that isn't a
  string or list-of-strings; `protein_preferences` (dict-valued) gets its
  own "must be a dict" guard. The read side (`_as_str_list`, wired into
  `get_household_memory`) normalises the same four fields so a row that
  was ALREADY bad on disk before this fix doesn't crash `/api/memory`
  either — `delete_preference`'s own reads of the same three list fields
  got the identical guard, since a bare string there doesn't throw, it
  silently iterates as characters and rewrites the row into single-letter
  garbage the next time anything was removed. Found during verification,
  same day, three more of the same shape: (1) `_coerce_str_list`'s list
  branch returned an already-a-list value untouched while its string
  branch already trimmed/dropped blanks — so `["Thai", "", "  "]` stored
  blanks as-is and What We Know rendered one empty chip per blank entry;
  both branches (and `_as_str_list`'s) now trim the same way. (2)
  `edit_preference`'s `usual_stores` branch diffs the OLD list against
  the new one to prune `store_typical_items_json` for a dropped store,
  and read that old value with a raw `json.loads` instead of
  `_as_str_list` — a legacy bare-string row iterated as characters, so
  the diff never matched a real store name and a stale entry silently
  survived. (3) `delete_preference`'s `protein_preferences` branch had
  the matching gap for a legacy bare-string row — `dict(json.loads(...))`
  with no isinstance guard, so a bad row made `dict()` itself throw
  (surfaced as a confusing 400 on an ordinary "forget this protein"
  click) instead of the character-explosion the list fields had; given
  the same dict-or-empty-dict guard `get_household_memory` already uses.
  Tests: `tests/test_memory_field_types.py` (new — both write paths, both read
  paths, `delete_preference`); `tests/test_prefs_eating_line.py`'s
  bare-string test rewritten to construct the bad shape by hand now that
  the live route no longer produces one.

- **2026-09-13 — With only a past week on file, Kitchen shows last month's
  meals as "the rest of the week". Branch
  `worktree-kitchen-fallback-and-memory-types`, NOT merged at the time of
  writing.** Loop Board bug: `_current_weekly_plan_row`
  (`app/tools/weekly_plan.py`) deliberately falls back to the household's
  newest non-retired plan when nothing covers today — right for a chat
  answer to "what's the plan", wrong for `get_cooker_view`'s "what am I
  cooking RIGHT NOW" (which Now's `today_moves`/`moves_for_day` also read,
  via `app/tools/moves.py`, with no id of their own). A household whose
  last approved week ended weeks ago saw that week's dinners rendered
  under Cook's "this week" framing. Fixed in `get_cooker_view` only, right
  after resolving the plan: when the caller omitted `weekly_plan_id` AND
  the resolved plan's `period_end_date` is before today, the view is
  reduced to the same empty shape as "no plan at all", with a new
  `last_planned_label` field (e.g. "Aug 18–24") carrying the stale plan's
  own date range for an honest line. Deliberately narrower than "doesn't
  cover today": a plan that HASN'T STARTED YET (a draft generated ahead of
  time for next week) is left alone — `test_is_current_plan_is_the_same_
  query_not_a_date_rule` and `TestAPlanThatDoesNotCoverToday` (existing
  tests) pin that cook mode legitimately opens next week's draft when
  today's own week is empty, and only a plan whose LAST day has already
  gone by counts as stale. `static/shell.js`'s `renderKitchen` already had
  an empty-state branch for "no plan"; extended to say "Nothing planned
  this week yet." + "Last planned: <label>" for the stale case, vs. "No
  plan yet this week" for a household that has genuinely never planned.
  Side effect: this also closes a previously-characterised, deliberately-
  unfixed residual bug for a component-based household whose current plan
  has already ended (`test_a_component_household_no_longer_has_this_bug`,
  inverted) — but NOT for a component household whose current plan is a
  future, not-yet-started one, which still has the original bug on
  purpose (`test_a_component_household_with_a_future_plan_still_has_the_
  narrower_bug`, a new characterisation test for the next session).

- **2026-09-13 — A hand-added item in another unit than the recipe's no
  longer grows a longer line every week. Branch
  `worktree-grocery-unit-concat`, NOT merged at the time of writing.**
  Loop Board bug (found 2026-09-13 fuzzing the line-to-zero work: 151 of
  200 randomised runs, identical on `main`). A standing want ("Eggs · 1",
  hand-added) and a recipe's "2 cups" can't be added up, so
  `add_grocery_item` concatenated them onto the person's line ("1 + 2
  cups", `keep_standing`), and the reversal — which reads a line as one
  number in one unit — could never take the plan's share back off; the
  next week added another "+ 2 cups", for as long as the eggs stayed
  unbought (reproduced: "1 + 4 cups + 4 cups + 4 cups" after three
  approvals, with or without the meals leaving in between).
  - **Rule chosen: two lines, honest — never one line nobody can read.**
    `grocery._merge_target`: an amount joins the first same-name line it
    adds up with CLEANLY; failing that it may concatenate onto a line of
    its OWN KIND (a person's add onto a person's line, a plan's onto a
    plan's) and never across. So the person's want stays exactly as typed
    and the plan's amount goes on a plan-owned line that recomputes from
    its ledger, leaves with its week, is deleted by
    `clear_stale_grocery_items`, and is set aside as a leftover by the
    CARRY step like any other plan line — the reversal is correct by
    construction, with no new arithmetic on the standing-want restate
    path (see the line-to-zero entry below for why that path is not to
    be touched lightly). The other option — one line, two clauses, a
    clause-aware reversal — was rejected for exactly that reason. A plan's
    two recipes disagreeing on a unit still share ONE plan line ("2 cups
    + 3"), byte-identical to before, because its ledger fully describes
    it; a person's two unrelated amounts still share the person's line
    (no ledger to keep straight). Same-family standing wants merge and
    restate exactly as before.
  - **The one cross-kind join kept: a person's add onto a pending spice
    line** (status 'spice', spices.py): that line is the plan's reminder,
    not an amount, and the person's add is the answer to it — it ticks
    the line onto the list, as the spices merge decided the same day.
    The week after, the plan's cumin starts its own reminder rather than
    a third clause.
  - **CARRY step: Keep joins only a line it adds up with cleanly**
    (`_this_weeks_line` now takes the amount); otherwise the carried line
    comes back on its own as a standing want, and undo works. It used to
    write "1 bag + 2 lbs" — the same bug wearing the leftovers hat.
    `consolidate_grocery_list` likewise no longer glues the two kinds
    together. One carry-over test was retargeted honestly: "a keep that
    cannot come back off" is now the bag-onto-sized-bag pair ("2 bags (2
    lb)"), since the unreconciled pair no longer merges at all.
  - Scratch fuzz (different-family pairs only, 3 approve-and-clear weeks,
    200 runs): **200/200 grew on `main`, 0/200 here**, two seeds. Tests:
    `tests/test_grocery_unit_families.py` (13; 9 red on `main`, 3 guards
    for what did not change, one 40-run randomised loop), plus one new
    test in `tests/test_grocery_carry_over.py`. Not done: lines already
    concatenated on Emily's real list stay as they are until bought —
    `repair_grocery_quantities` is the assistant's tool for those and was
    not run.

- **2026-09-13 — Two adults approving the week at the same instant no
  longer buy everything twice. Branch `worktree-approve-race`, NOT merged
  at the time of writing.** `approve_weekly_plan`'s two "safe to call
  twice" guards were each a read on one connection, acted on by a write on
  a later, separate connection/implicit transaction — the status flip
  into 'approved' committed on its own, before the recipe-week grocery
  ingest ran. Two threaded calls released at a 0ms gap doubled every
  grocery line 6/6 trials; at a 20ms gap, 0/6, because the first call had
  already committed — which is why one impatient tap never showed this.
  Same class this repo has closed three times already
  (atomic-period-takeover, swap-atomic, drop-dish-atomic), same shape: the
  flip is now one conditional UPDATE (`WHERE status != 'approved'`), and
  everything the transition does — the carry-over set-aside, the
  ingest, the `approved_grocery_added/skipped` + `carried_over_count`
  receipt — runs inside the SAME transaction as that flip, on one
  connection opened with an explicit `BEGIN IMMEDIATE`. Pulled into its
  own function, `_settle_weekly_plan_approval`, matching
  `_replace_slot_entries`' shape. A caller that loses the race (0 rows
  flipped) rolls back having written nothing and returns the same
  `was_already_approved` shape an honest, unhurried re-approval always
  has. `tests/test_approve_race.py` reproduces the race with real threads
  (a `threading.Barrier`, no monkeypatched ordering) — confirmed to fail
  on the pre-fix code (6/6 doubled) and pass on the branch.
  - **The confirmation gate for a hard allergy conflict stays OUTSIDE the
    lock, deliberately.** `check_plan_conflicts` does real read work and
    nothing that slow belongs inside a write lock (same call the
    takeover/swap/drop fixes made). The one edge this leaves: two
    approvals racing a hard-conflict plan in the exact gap between that
    read and the transaction can make the loser see one extra
    "needs_confirmation" round-trip for a week that, by the time it asked,
    was already approved. Confirming past it is harmless — the guard
    below simply finds nothing left to do — and this is far narrower than
    the bug being fixed (it needs a hard conflict AND a race, not just a
    race), so it's left open rather than pulling the conflict check inside
    the lock too.
  - **An independent verifier's pass found one real stray connection,
    fixed the same day.** `acting_member_id_for(approved_by)` was
    evaluated as a bare argument to the UPDATE — i.e. AFTER `BEGIN
    IMMEDIATE` — and it calls `current_member()`, which opens its own
    connection via a LOCAL `from ..db import get_conn` inside
    `_shared.py`, invisible to patching any module's own pre-bound
    `get_conn` name (only patching `app.db.get_conn` itself catches a
    local import, since it re-resolves fresh on every call). Harmless in
    practice — a plain SELECT coexists with the write transaction's
    RESERVED lock under SQLite's default rollback-journal mode, and the
    same shape already existed on `main` elsewhere before this ticket —
    but this fix's whole claim is a precise one connection, so
    `approved_by_member_id` is now resolved in `approve_weekly_plan`
    before the transaction opens, same as `approved_by` itself already
    was. The connection-count test now patches `app.db.get_conn` directly
    too, and was confirmed to fail (1 stray connection) against the
    inline version before this and pass (0) after.

- **2026-09-13 — Four small defects from driving the app on a throwaway
  DB, fixed one commit each. Branch `worktree-four-small-defects`, NOT
  merged at the time of writing.** All four were real, independent, and
  each got its own test that fails on `main`.
  - **Chore status accepted any string.** `set_chore_instance_status`
    (`app/tools/chores.py`) wrote whatever it was handed straight to
    `chore_instances.status` — a typo landed a row in a status no
    screen's WHERE clause looks for: not pending, not done, just gone.
    Now validated against `CHORE_INSTANCE_STATUSES` before opening a
    connection, raising the new `InvalidChoreStatus` (a `ValueError`
    sibling of `ChoreRefused`, not a subclass, so it doesn't fall into
    the existing 404 handler) — the `/status` route maps it to 422.
  - **A chore could be moved into 2020.** `move_chore_instance` parsed
    `to_date` and wrote it straight to `due_date` with no floor.
    Judgment call: refuses a target **before today**, not before the
    chore's creation — the latter would block pulling a genuinely
    slipped old instance forward, which is the opposite of what a
    household asking for that wants. Today itself stays valid.
  - **Rhythm half-saved.** `/api/onboarding/rhythm` called the six-plus-one
    individual setters one at a time, each on its own connection/commit —
    an invalid field partway through left the fields ahead of it already
    written. New `tools.save_rhythm_answers` (`app/tools/rhythm.py`)
    validates every given field first, then writes all of them on one
    connection with one commit. The individual setters are unchanged and
    still right for a single chat correction.
  - **New members had no avatar colour.** `members.color` was only ever
    filled in by `db._backfill_member_colors`, a startup migration — an
    adult added while the server kept running stayed colorless until the
    next restart. `set_member_age_group` (`app/tools/household.py`) now
    assigns the next unused `db._ADULT_COLORS` slot the moment someone
    becomes an adult, same "only two, only if still blank" rule the
    backfill uses.

- **2026-09-13 — Answering "how much did you use?" twice takes the food out
  once, however many times the box is toggled. Branch
  `worktree-attention-twice`, NOT merged at the time of writing.** Loop
  Board bug, found while fixing check_off_meal's own double depletion (see
  that entry below, "Two things left alone," item 2) and left out of that
  branch on purpose. `attention.add_attention_item` deduped a queued
  "how much X did you use?" question against `status = 'pending'` rows
  only — so once the household answered it (`record_attention_item_usage`)
  and then untucked/re-ticked the same meal (an ordinary toggle,
  `check_off_meal`'s own claim is released whenever nothing was actually
  reconciled — exactly what a no-stated-quantity ingredient does every
  time), `deplete_inventory_for_meal` found the same confident match again
  and queued a BRAND NEW item with no memory of the first answer.
  Answering that one subtracted the person's amount from whatever the
  shelf currently read — already reduced by the first answer — so one real
  use took the ingredient out twice. Reproduced: Lettuce 2 heads -> answer
  "1" -> 1 head -> untick -> re-tick -> answer "1" again -> row deleted.
  - **Same shape as check_off_meal's and grocery.mark_grocery_item's
    re-tick fixes, without a schema change** — `attention_items.detail_json`
    is already a free-form blob, so there was somewhere to put the memory
    without a migration. `add_attention_item` now dedupes/reopens on
    `(kind, entry_id, ingredient)` pulled out of `detail` whenever both are
    present, not `kind + summary + pending`: a match that is `pending` is
    the old no-op; a match that is `resolved` or `dismissed` is REOPENED in
    place (status back to `pending`, `detail_json` merged so a prior
    answer's receipt survives) instead of a second row being inserted.
    Callers without both keys (none today) fall back to the old
    pending-only dedupe.
  - **A repeat answer REPLACES the first rather than compounding onto it.**
    `record_attention_item_usage` writes its own receipt into
    `detail_json.applied` (`{before, after, rev_after}` — `before` is the
    quantity BEFORE THE VERY FIRST answer, carried forward unchanged across
    every reopen) after every successful answer. The next answer recomputes
    from that `before`, not from the shelf's current reading, but ONLY when
    `inventory_items.rev` still matches `rev_after` — proof nothing has
    touched the row since (a manual edit, another depletion). When it
    doesn't match, the original can't be trusted any more and this answers
    fresh against the current quantity, same as the very first time.
  - **Found by review, and fixed in the same branch: a component batch
    re-ticked through a DIFFERENT sibling checkbox dodged the reopen.**
    `check_off_meal` was calling `deplete_inventory_for_meal(entry_id)`
    with whichever entry THIS tap named — fine for a single-entry meal,
    where every tap names the same id, but a merged component card's
    re-tick can land on any sibling, and a different sibling's `entry_id`
    is a different dedupe key, so the same bug reappeared one door over
    ("how much Jello did you use?" asked and answered twice for one real
    box). Fixed by calling `deplete_inventory_for_meal(min(linked_ids))` —
    the batch's own siblings query keys off weekly_plan_id + meal name, not
    entry_id, so the lowest linked id is a stable stand-in for "the batch"
    regardless of which checkbox is tapped, and only grows as new siblings
    are planned, never shrinks.
  - `tests/test_attention_answered_twice.py` (12): the reproduction
    end-to-end through `check_off_meal`, several toggle rounds, a second
    answer that differs from the first (replaces, does not add), the
    sibling-batch case above, direct `add_attention_item` reopen/dedupe
    unit tests, and the rev-mismatch fallback (household hand-edits the
    shelf between two answers). 9 of the first 11 red against `b322c99`
    (checked by `git stash`, not by overlaying an older commit). Suite
    4064 -> 4076, no new failures.
  - **Left alone on purpose:** a true concurrent (same-instant, two-thread)
    double-submit of `record_attention_item_usage` on one still-pending
    item has no claim/lock and could in principle double-apply — the same
    class of race `check_off_meal` and `mark_grocery_item` both guard
    against for THEIR writes, but this path has no UI double-tap route
    driving it the way a checkbox toggle does, and the reported bug is the
    untick/re-tick shape, not concurrency. Its own card if it turns out to
    matter.
- **2026-09-13 — "How did it go?": "Will grab elsewhere" picks the store,
  "Don't need anymore", and "Add a new store" that comes back. Branch
  `worktree-shop-store-screens`, NOT merged at the time of writing.** Loop
  Board improvement (Emily: "'somewhere else' make it 'will grab
  elsewhere' and then if they select that show the drop down of the other
  stores ... an option ... to add a new store (also in the sorting screen
  in case) ... make sure the flow brings them back to this screen. Also
  add a 'don't need anymore' option"). The wrap-up's "Somewhere else"
  used to EXCLUDE the row (`/exclude`, status 'excluded' — off every
  list); a thing you will grab at Metro is on Metro's list, not off the
  list, so the answer is now a store picker under the row
  (`groWrapStorePickerHtml`, `wrap-move`) and the line moves there. The
  SORT screens' own "Somewhere else" chip still excludes — unchanged, so
  the open leftovers question (an excluded line still absorbing next
  week's amount) is untouched here; the wrap-up simply no longer feeds
  that path.
  - **A wrap-up move is this week's answer, never a new preference:**
    `GroceryStoreRequest` gained `remember` (default true, so the LIST
    row's pills and SORT behave exactly as before) and the wrap-up sends
    `remember: false` — "Costco was out of eggs, Metro this week" must
    not quietly rewrite "eggs come from Costco" (`set_grocery_item_store`
    updates a known preference immediately, by design). Undo is the same
    call back to the store it came from. A moved line reads "Grabbing at
    Metro" (`wrapMoved`, in the trip snapshot) rather than being asked
    about again.
  - **"Don't need anymore" is the pre-shop drop** — the same
    `drop_grocery_item_pre_shop` "Have it" uses (soft-remove, toast undo
    through `/pre-shop-undo`, never bought, never inventory). Both land
    on the wrap-up's confirmation card, whose line now reads "Not needed
    this week:" rather than "You said you already have:" so it is true of
    both.
  - **The way back is a sheet, not a route.** Stores live in the What we
    know sheet (`openKitchenSheet('stores')`), which slides over the
    current screen; the chat's `/memory` href reaches it through the
    Kitchen tab (`followActionHref`) and would have lost the wrap-up.
    "Add a new store" (WRAP UP's picker, SORT, SORT ALL —
    `groAddStorePillHtml`) arms `groceryState.storeReturn` ({step, kind,
    id, name, from}) and opens the sheet on the "+ Add a store" field
    (`wwkFocusAdd`); `wwkAddStore` hands a saved store to
    `groUsualStoreAdded`, which closes the sheet and makes it the answer
    where it was asked (queue: assigned; SORT ALL: staged; wrap-up:
    moved); `closeKitchenSheet` calls `groStoresSheetClosed`, which
    forgets the hand-off and takes any shop the sheet knows that the
    list's copy doesn't (additive — the sheet's cached read can be
    older than the list's optimistic state). One URL throughout; nav-v2
    rules kept.
  - Also: the wrap-up row's quantity chip stretched under the name as a
    full-width bar (`.gro-qty` is `display:flex` inside a block span,
    pre-existing) — `.gro-wrap-name` is a flex row now.
  - Tests: `tests/test_shop_wrap_up_answers.py` (21: node harness for
    the answers, the picker and both directions of the hand-off; the
    route's `remember`; source markers for the sheet wiring).
- **2026-09-13 — "Start the trip" asks "Where are we headed?", and both
  store-pick screens are store cards. Branch `worktree-shop-store-screens`,
  NOT merged at the time of writing.** Loop Board feature (Emily: "it
  should ask me (through a select screen) which store we're headed to ...
  similar to the 'where's next' screen ... And make the design of that
  one + the where next screen a bit nicer - add some colour"). Before,
  "Start the trip" opened whichever stop the list put first; the
  household's own choice of route only began at the second stop. New
  HEADED step in `static/shell.js` (`groHeadedHtml`, `head-for`): the
  stops as cards, and the card you tap is the stop the snapshot opens on
  (`groBeginTrip(stops, startAt)` — `groStartTrip` is now the question,
  `groBeginTrip` the snapshot). One stop asks nothing; a paused trip is
  continued, never asked again ("Finish later" / "Continue the trip" are
  untouched). WHERE NEXT is the same cards (`groStopCardsHtml`), so the
  two match by construction.
  - **The design pass, and what it is not.** One card per stop
    (`.gro-stop`): a spine in the store's palette colour down the card's
    left edge, square where it meets the card and round outside (the
    joined-tile motif), the initial in it, the name in the display face,
    the count, and the things ONLY HERE. The shopless note became the
    celadon tile under the cards. Neither screen gets an apricot fill —
    the cards are the choice, and HEADED has no dock at all (rule 2: no
    single action, no dock; the crumb is the way back). Ratios measured
    in Chromium and in the CSS comment: ONLY HERE `--apricot-label` on
    `--surface` 5.20:1 light / 8.53:1 dark; the rest are pairs already
    cited elsewhere. The store palette itself is unchanged (Tier 2).
  - **"Only here" = a remembered item -> store preference
    (`groIsUsuallyHere`, `item_store_preferences`)**, the one thing the
    data has that says "you can't get this at the other stop"; every row
    on a stop's list carries that store, so the row's store would say
    nothing. Three by name, "+N" for the rest. Emily's phrase kept over
    the app's "usually here"; one string to change (`groStopCardsHtml`).
  - **Found by review: the back gesture can land on HEADED with a trip
    already on** (HEADED, TRIP and NEXT each push a history entry), and a
    card tapped there re-snapshotted — Costco no longer done, the count
    of what came home back to zero. `head-for` now carries the guard
    `groStartTrip` has: with a live trip, the card is read as WHERE NEXT
    reads it (into that stop if still open, else `groResumeTrip`).
  - Tests: `tests/test_shop_where_headed.py` (17). Six harness trips in
    `tests/test_shop_trip_exit.py` now name Costco (`startTripAt`), and
    three `.gro-nextrow` markers moved to `.gro-stop`.

- **2026-09-13 — Swap says who picks: "Swap · I'll pick". Branch
  `worktree-meal-open-and-swap`, NOT merged at the time of writing.**
  Loop Board improvement (Emily: "when you click the 'swap' button, it
  goes with something totally different ... it should make that clear").
  One constant, `SWAP_LABEL` in `static/shell.js`, at both sites the
  in-place swap is offered (the Day card, the Meal dock), against "Tell me
  what instead" beside it. Considered and not built: "Surprise me" (loses
  the word Swap the clash card and the undo flow use, and over-promises
  whimsy for a pick that works around the table's exclusions and the
  week's other dishes) and a hint line under the pair (restates the
  labels — §8's every-word rule). "Random" is deliberately not in the
  label for the same reason. The post-swap state is unchanged: reason +
  Undo + the chat link for `SWAP_UNDO_MS` (8 s), the swap itself still
  there to go again. A persistent Undo would need `swapped_from` on the
  week menu's entries; not done, since the reason is saved on the meal
  and the line is not meant to be furniture.
- **2026-09-13 — Tapping a meal in the draft always opens that meal.
  Branch `worktree-meal-open-and-swap`, NOT merged at the time of
  writing.** Loop Board bug (Emily, on her phone: "sometimes it brings me
  to the recipe, and sometimes it brings me to this other page with the
  full cook list"; after a chat swap, "I can't go to the screen where I
  can see the instructions for it"). Two causes, both in `static/shell.js`:
  a dish name on the draft's "What we're eating" list went through
  `openRecipeFor` into COOK MODE while the tiles' way in (tile → Day →
  card) went to Plan's own Meal step — two destinations for one tap; and
  both screens read the recipe off Cook's OWN cooker view (`/api/cooker-
  view` with no id = the plan whose period contains today,
  `_current_weekly_plan_row`), fetched once and never again. On a Sunday
  with Plan pinned to the week just drafted, nothing on the draft is in
  that view, so `cookResolveFocusIndex` found nothing and cook mode fell
  back to its root ("Cook · 4 cooks today"); the Meal step drew a hero
  with no steps. A dish swapped in had the same problem by a different
  route: a new entry id, and a cached view fetched before it existed.
  - **Fix: one door, and the plan's own view.** Every tap on a meal in
    Plan opens the Meal step (the crumb remembers whether it came from
    the list or the Day step — `weekState.mealBack`). The Meal step reads
    `/api/cooker-view?weekly_plan_id=<the plan on screen>` into
    `weekState.cookView` — never into `cookState.data`, which is Cook's
    reading of its own week — and re-reads it after anything that reloads
    the week (`loadWeekMenu` marks it stale). While it is on its way the
    clock says "Getting the recipe…"; a failed read is held and says so
    rather than retrying on every render.
  - **`is_current_plan` on the cooker view** (`cooker.get_cooker_view`):
    whether the named plan is the one the no-id view resolves to — the
    only plan cook mode can open a meal from. "Cook this" / "Start at …"
    are offered only when it is true; on a week Cook doesn't hold yet the
    Day card's row is the swap alone and the Meal dock is swap + "Tell me
    what instead", since deciding is that week's job. Answered by the
    same query rather than by dates: on a day no plan covers, the fallback
    current plan IS next week's draft, so "its period has started" would
    be the wrong test. Emily can override the dock's shape for that case.

- **2026-09-13 — After the plan, seeing the week is a first-class path;
  the list keeps its pull. Branch `worktree-allset-week-path`, NOT merged
  at the time of writing.** Two Loop Board cards on All set (Emily, on
  her phone: "'6 cooks' is confusing language ... say it's recipes";
  "53 to buy feels intimidating, can we say 53 ingredients"; "make the
  loop easier to go and see the week and not go straight to the list.
  but make the CTA to go to the list enticing").
  - **"Recipes" is a different number, not a relabel.** A dish cooked
    on two nights is one recipe and two cooks, so `week_receipt` counts
    distinct cooked dishes as `recipes` (by title, casefolded) and keeps
    `cooks` for the Plan band's "4 cooks, 3 made ahead" — the two words
    now describe two things. The tiles and the root receipt's sentence
    ("6 meals, 5 recipes, one list of 53 ingredients.") read `recipes`;
    `list_count` is unchanged: the 'needed' lines "Open the list" opens
    on, which leaves an unticked "Spices this week" line and a
    keep-or-drop leftover off the number until they are ticked or kept.
  - **All set outlived its own screen.** `submitWeekApproval` sets
    `weekState.step = 'allset'` and nothing but "See the week" ever
    moved it: "Open the list" is `activateTab('grocery')`, and the tab
    bar is `activateTab` too, so coming back to Plan by either showed
    the finished-planning screen again for the whole page view. Now
    `activateTab` folds 'allset' to the root on the way OUT of Plan
    (same shape as `groceryState.justFinishedTrip`), re-rendering the
    panel in the same tick so nothing moves under a thumb; the asks' late fetches
    land on the root's receipt row, which the approval already
    dismissed. Deeper steps (a day, a meal) are left alone — the
    refresh policy says nothing reloads on a tab switch.
  - **A `.dock-secondary` above the apricot, not a `.dock-link`.** The
    ticket asked for a real button; Rule 5 forbids a second APRICOT,
    not a second button — Shop's "Skip the rest" (`.gro-secondary`) and
    the old `.wk-check-btn` above Approve were the same sand button in
    a dock. On All set's spruce dock it takes the counters' own
    `--spruce-raised` fill, the `--apricot-rule` edge and `--ivory-ink`
    (9.86/7.70 on the fill, 8.53/6.90 on hover, light/dark). Written
    into DESIGN_SYSTEM §5 Dock in the same commit. Judgment calls, all
    one line: the primary carries the count ("Open the list · 53
    ingredients", plain over an empty list); the approved week's review
    step docks "Open the list" where Approve was (`reviewDecideHtml`,
    still the one `wk-decide dock`); Shop's LIST puts "See the week" as
    the quiet link beside "Start the trip", landing on the tiles when an
    approved week is loaded and on the Plan root otherwise (pushing a
    step `renderMealsStep` would fold leaves Back pointing at a screen
    nobody saw). A household with no named shop has no "Start the trip"
    and so no dock — pre-existing, and the tab bar is still one tap.
  - Not touched, for Emily: Cook's "4 cooks today", the Plan band's
    "5 cooks, 1 made ahead", onboarding's "16 meals, 5 cooks", Shop's
    band "10 things · 1 stop". Tests: `tests/test_allset_receipt_words.py`
    (7) and `tests/test_allset_week_path.py` (15, node harness).

- **2026-09-13 — Staples get sections, and the spice rack is one of them.
  Branch `worktree-staples-sections`, NOT merged at the time of writing.**
  Loop Board feature (Emily: "make one of the grouping 'spices' ... a good
  starting point to introduce the inventory management without doing the
  full thing" → offered the staples framing: "Yes ... I want to make sure
  there are sections under it, and then the spices is one section so it's
  easy to organize"). Staples-style, NOT inventory: no counts, no
  locations, no data entry; nothing here reads or writes `inventory_items`.
  - **A section is DERIVED, never stored or typed.** `staples.section_for
    (item, category)` → Spices / Pantry basics / Fridge basics / Household
    supplies / Other (`SECTION_ORDER` / `SECTION_LABELS`, the assumed set):
    a spice by `spices.is_spice` first; then a household word or phrase in
    the name (beats a category — "dish soap" under pantry is a category
    mistake); then the grocery category (household; produce / dairy /
    meat-seafood / FROZEN → fridge, the same appliance; pantry → pantry);
    then a food word in the name (phrase first, then the last word — the
    thing itself, so "chicken stock" is pantry and "peanut butter" is a
    listed phrase — then any word, every word singularised); else Other.
    No schema change: `_shape` works it out on every read, so a better
    classifier fixes every staple at once. `/api/staples` now returns
    `sections` beside the flat `staples`; the Staples card on Shop renders
    each under a `.gro-eyebrow` heading (`groStapleRowHtml` /
    `.gro-staple-sec`).
  - **The Spices section IS the spice rack, kept from purchases.** A bought
    line whose name is a spice becomes a staple on its own
    (`record_staple_purchase` → `_create_spice_staple`; `seed_spice_staples`
    does the same for every spice already in the purchased history, run on
    every read of the staples or the card, idempotent). Its cadence starts
    at `spices.RECENTLY_BOUGHT_DAYS` (56) — one number for "a jar lasts
    about this long" — and is learned like any staple. `_seed_history`
    takes `except_line_id` so the line being ticked today is one purchase,
    not a bought-when-listed plus a bought-when-ticked a few days apart
    (which would teach a phantom interval).
  - **The card reads the staple; the old 56-day scan is gone.**
    `list_spices_this_week`: bought within its cadence → left out under
    "Bought lately, so not listed" (the wording stays true — a "plenty"
    answer moves the due date, not last-bought); cadence run out (staple
    due) → the pending row is ticked there and then, as a needed line
    carrying `staple_id` and `due: true` ("Probably running low" under the
    name), so the list's own We-have-plenty / Not-this-trip work on it
    too; an untick on that row is "we have plenty" (`note_line_removed`),
    a re-tick takes it back (`reverse_last_answer`). Test
    `test_bought_lately_is_not_offered_again` now moves the staple's clock
    instead of the purchased line's created_at.
  - **Judgment call: a spice staple is never pushed onto the list by
    `sync_due_staples`.** A jar is used when a recipe calls for it, not on
    a rhythm; "probably running low: cumin" in a week nobody cooks with
    cumin is the third jar Emily wants to stop buying. It waits for the
    card. The one exception keeps the chat promise: `add_staple(...,
    running_low=True)` on a spice puts the line on today (`_put_on_list`,
    shared with sync). Also: a household thing filed as "other" by a chat
    add ("toilet paper") now starts on the household default cadence (30)
    rather than the catch-all 21, via the section.

- **2026-09-13 — "6 cucumbers": the kind goes in the name when the count
  depends on it. Branch `worktree-ingredient-variety`, NOT merged at the
  time of writing.** Loop Board improvement (Emily, on the list: "does it
  mean the persian cucumbers? Because that makes sense, but 6 english
  cucumbers would be a crazy amount"). Not a bug in the list: the line was
  exactly what the recipe wrote, and the ingest never strips a word. What
  was missing was the WORD, and only the model knows it — so the fix is a
  prompt sentence (both plan prompts, the sides prompt, and the
  `add_recipe` tool's `item` description): name the kind whenever the
  count only makes sense for that kind, a bare name reads as the
  full-size kind.
  - **The catch is a flag, never a rewrite.** `recipes._PRODUCE_COUNT_PER_
    SERVING` — cucumbers, tomatoes, potatoes, peppers, onions, apples, each
    with a per-serving ceiling for the ordinary kind (1, 2, 2, 1.5, 1.5, 2)
    — is deliberately NOT a class in `_PLAUSIBLE_PER_SERVING`: that table
    rewrites the cook amount, and "6 cucumbers" → "2" would buy the wrong
    amount of the right thing when six Persian ones were meant. A bare
    count over the ceiling on a name that does not say a different kind is
    reported by `plan_quality._produce_variety_named` ("info", morning
    report, like `_quantities_plausible`); the list and the cook view show
    the line as written. A named kind ("Persian", "cherry", "baby", "green"
    on an onion) is never second-guessed, wherever in the name it is said
    ("Cucumbers (Persian)", "Cucumbers, Persian" — the verifier's catch);
    "English cucumbers '6'" is still caught but only as "a lot", not asked
    which kind. "1 dozen" / "6 ct" count as counts.
  - **Not annotating the list line ("6 cucumbers — small ones?")**, on
    purpose: no field to carry it without a schema change, the row's name
    is the merge key and a store-preference key, and at the list the
    servings are unknown — a wrong "small ones?" on a real list is worse
    than a quiet line in the report. Its own card if the prompt sentence
    is not enough.
  - **"Persian cucumbers" and "Cucumbers" from two recipes stay two
    lines** (`grocery._merge_key` already does this): they are two
    different things at the store, and the merge is built to fail toward
    two lines. Pinned in `tests/test_ingredient_variety.py`.

- **2026-09-13 — "One-pot" was a claim about the plate, not the method.
  Branch `worktree-plate-tags-method`, NOT merged at the time of writing.**
  Emily: a grilled turkey-burger-and-charred-vegetables plate got tagged
  "one-pot, nothing extra." Root cause: `weekly_plan.get_week_menu`'s
  `plate_note()` awarded the tag whenever `plates.is_complete` was true
  (food_groups covered protein/veg/carb) — it never looked at how the dish
  was actually cooked. Fix: `plates.contradicts_one_pot(name, tags,
  instructions)` text-scans the recipe's own name/tags/instructions for
  grill/broil/bbq words or a step naming a second cooking vessel
  ("separate"/"another"/"second"/"meanwhile" + pan/pot/skillet/oven/etc.),
  and `plate_note` withholds the tag when it fires. Two false-positive
  traps found by testing against ordinary recipe phrasing, not just the
  burger case, and excluded: "meanwhile, preheat the oven" (the first line
  of nearly every oven/sheet-pan one-pot recipe — excluded via a negative
  lookahead on "preheat") and "in a separate bowl" for a marinade/dressing
  (bowl dropped from the vessel word list entirely). "Grilled cheese" as an
  ingredient/topping name is stripped before the grill-word scan for the
  same reason — it's pan-fried, not grilled. Known remaining gaps, left as
  is rather than adding more regex complexity: a hyphenated "Bar-B-Q" won't
  match, and reversed two-pot phrasing ("simmer the sauce in one pot while
  the pasta boils in another") isn't caught either — both real but
  low-frequency compared to the reported bug.
- **2026-09-13 — Add a recipe by photographing the page of a cookbook — and
  the book is cited. Branch `worktree-recipe-photo`, NOT merged at the time
  of writing.** Loop Board (Feature, High, Phase 1.5). Emily: "uploading
  your own recipe if you can take a photo of a recipe in a cookbook. Then
  make sure the book/photos is cited." The link import with a photo in
  front of it, and the first time the app keeps an image.
  - **Same shape as the link import, deliberately.** `POST
    /api/recipes/import-photo` (multipart `photo` + optional `photo2` +
    `hint`, like the scans) → `agent.read_recipe_from_photos_llm` reads the
    page(s) in ONE forced-tool vision call (`_READ_RECIPE_PHOTO_TOOL`: the
    recipes on the page AND the credit — running head, folio, "never
    invent a title") → `recipe_import.draft_from_photo_read` normalises
    through the same `draft_from_model` / `split_ingredient_line` /
    `guess_category` path, marked `read_by: "photo"`, with `citation`
    {book, author, page} and `candidates` when the page holds two recipes
    → the same review sheet → `/api/recipes/add`. Two photos are one call,
    not one per photo: a method that starts on the left page and finishes
    on the right can only be read whole. A bad read is the reader's own
    plain sentence (400) and keeps nothing.
  - **Storage: files beside the database, rows as the index, nothing by
    path.** `app/recipe_photos.py`: `<dirname(DB_PATH)>/recipe_photos/
    <household_id>/<recipe_id>-<n>.jpg` (same Railway volume as the DB, so
    it survives a redeploy for the same reason), `recipe_photos` table
    (household_id, recipe_id, position, filename, media_type). The read
    stashes the photo as PENDING under a random token in the household's
    own `pending/` folder; the save moves it onto the recipe; a token that
    is stale, foreign or malformed is skipped, never an error; pending
    files are swept after a day. `GET /api/recipes/{id}/photos/{n}` looks
    the row up under `household_id()` and builds the path from the row
    inside that household's folder — another household's ids are a 404,
    and no request string ever touches a path. `reset_household.py` drops
    the folder with the rows. **No server-side resample:** there is no
    imaging library in this app and adding Pillow is a dependency decision
    for Emily, not a code change; the phone shrinks the page instead
    (`shrinkPhotoForUpload`, long edge 1600px, JPEG 0.86 — a 12MP page
    lands at ~400 KB) and the server checks the bytes are a JPEG/PNG/WEBP
    (magic bytes, not the header) under a 5 MB cap.
  - **One field says where a recipe came from.** `recipes.recipe_citation`
    builds `{kind: book|link, ..., text}` from `source_url` +
    the new `source_book/source_author/source_page` columns (nullable
    defaults, `_MIGRATIONS`); wording is "From Salt Fat Acid Heat, Samin
    Nosrat, p. 212" / "pp. 212–213" for a spread / "From a cookbook" for a
    photo with no visible credit / "From seriouseats.com" for a link —
    never "Source:" (§8). Carried on `list_recipes`, the cooker view meals
    and `get_week_menu` slots as `citation` (+ `photo_urls`), and rendered
    by ONE shell function, `recipeCitationHtml` (book in italics; a button
    "See the page" over the kept photo), inserted in three places:
    `mealStepHtml` under the clock, `daySlotCardHtml` under the dish name
    (text only — the card is already a button), and cook mode's Before
    you start. Before this, `source_url` was shown nowhere but the import
    sheet — a link recipe on the plan said nothing about where it was from.
    The chat's `add_recipe` tool gained the three credit fields too ("the
    lasagne from the Ottolenghi book" saves cited).
  - **The review asks for the credit as a sentence, not a form** (§7):
    "From [which book] / by [who wrote it], p. [page]" — inline
    underlined inputs prefilled with what the page showed, all optional;
    each blank travels with the word before it so a wrap never strands a
    comma. A two-recipe page shows "This page has two recipes — which
    one?" with one row per recipe; a tap swaps the whole draft.
  - **The chat path is the composer's camera, not multimodal chat.** The
    chat does not accept images today (the fridge/pantry scans are direct
    routes, not chat), so `#ask-photo-btn` beside the mic opens the same
    sheet with the typed words as the `hint` (fenced as data in the
    prompt; may name the book). Building image attachments into
    `/api/chat` is a separate card. Cook's More sheet gets the row "Add
    from a cookbook" under "Add from a link" (`GRO_ICONS.camera`).
  - **Cost:** one `read_recipe_from_photos_llm` row in `api_calls` per
    read via `_create_with_retry`; `tests/test_usage.py`'s call-site pin
    and the `api_calls` schema comment now count eleven. Same "scan" rate
    bucket. Verified live against a throwaway DB with the vision call
    stubbed (no API key on this machine): row → picker → shrink → read →
    candidates → credit → save → the credit on the day card, the Meal
    step and Before you start → "See the page" opens the served photo;
    the composer camera passes the typed note through. 71 tests in
    `tests/test_recipe_photo_import.py`; conftest wipes `recipe_photos`.
  - **Verifier round (same day):** two saves racing on one pending token
    used to 500 the loser with the filesystem path in `detail` and leave
    its recipe row behind — `attach_pending` now skips a token whose file
    vanished between the check and the move (never raises; a path never
    reaches a response), and the save route wraps the attach the same
    way. The link host is parsed with `urlsplit` (lowercase, no userinfo,
    no port — `https://evil.com@seriouseats.com/x` is seriouseats.com),
    not regexed. The review row reads the stored comma form, its page
    blank is text with "p."/"pp." following the value, and the
    unreadable-photo sentence is the server's in both places.
    `recipe_import.normalise_amount` fixes the three spellings
    `_parse_quantity` was blind to on model-copied amounts — "1 ½ cups",
    "400 g / 14 oz" (first printed wins), "2–3 cloves" (top end, as
    `split_ingredient_line` already did) — for the photo draft AND the
    model-read half of the link import; markup-read links were already
    going through the line splitter and are unchanged.
- **2026-09-13 — Sorting the list: "Have it" and "Use something else" on
  every item. Branch `worktree-grocery-sorting-round`, NOT merged at the
  time of writing.** Loop Board feature (Emily: "there should also be the
  'have this already' option ... instead of fresh oregano I'll use dry
  oregano"). Both verbs sit wherever an item is being sorted — the
  one-at-a-time queue, the one-screen sort, and a LIST row's `⋯` (the
  only place for a one-shop household, which never sees a sorting step).
  - **"Have it" is the pre-shop drop, not the inventory route — and the
    queue's existing "Have it" pill was moved off `/already-have` to say
    so.** That route (`move_grocery_item_to_inventory`) writes a row to
    `inventory_items`, and the ticket's own rule is policy 2026-09-01:
    "have it already" is a per-week answer, never inventory work.
    `drop_grocery_item_pre_shop` already was exactly that — soft-remove,
    undo, listed on the wrap-up under "Already have", and a staple's line
    dropped this way tells the staple "we have plenty". The
    `/already-have` route and its handler stay for anything else.
  - **"Use something else" is a per-WEEK record** (`grocery_substitutions`,
    `grocery.substitute_grocery_item`): the line is renamed to the
    alternative — quantity kept as written, since nobody can convert fresh
    oregano into dry, so the number stays for the person to adjust — or
    soft-removed when the alternative is at home; `get_cooker_view`
    annotates every matching ingredient in that week's meals with
    `substitute`, and `cookIngredientLabel` (the one place a cook-screen
    ingredient is worded) appends "— using dry oregano instead". Not a
    recipe edit: next week the recipe asks for fresh again. The existing
    `log_cooking_deviation` / `recipe_notes` is a permanent note on the
    recipe and was left alone for that reason.
- **2026-09-13 — Spices this week: one opt-in section, the list assumes
  a spice rack. Branch `worktree-grocery-sorting-round`, NOT merged at
  the time of writing.** Loop Board improvement (Emily: "put all the
  spices together under one section ... select the ones you want to add
  to the list to buy ... it also takes up a lot of space and scrolling").
  New `app/tools/spices.py`: `is_spice(name)` is a MAINTAINED LIST of
  names (spices, dried herbs, salt/pepper, cooking oils) tried from the
  most specific reading down — whole name, descriptors stripped, form
  word dropped ("cumin seeds"), last word alone — and never a heuristic
  on the word "spice". Fresh herbs stay in produce: basil, cilantro,
  parsley, mint, dill, chives, rosemary, thyme, sage, tarragon count only
  when written "dried", and "fresh" anything never counts. `_NOT_ALONE`
  is what keeps a bell pepper, a garlic clove and a fresh chili out.
  - **The model is a STATUS, not a flag.** A plan's spice is inserted by
    `add_grocery_item` with `status = 'spice'`; a tick makes it
    'needed'; an untick puts it back. So every reader of "to buy" —
    counts, the sort queue, the trip, pre-shop flags, the wrap-up — is
    right without knowing spices exist, and the merge (`add_grocery_item`
    now matches 'spice' rows too) keeps two recipes' cumin on one line.
    A PERSON adding a spice by name ticks the pending line: their add is
    a want. `_reverse_meal_grocery_contributions` and
    `clear_stale_grocery_items` treat 'spice' like 'needed'; a new
    week's approval deletes the previous week's still-unticked spices
    (never wanted) while a ticked one goes through keep-or-drop like any
    line, and keeping it ticks this week's twin.
  - **Judgment calls, all one line to change:** salt, pepper and oils
    are spice-rack things (Emily's ticket left it open); a spice with a
    purchased line made within `RECENTLY_BOUGHT_DAYS` (56) is not offered
    again — the same "created_at stands in for bought" reasoning as
    `staples._seed_history` — and the section names what it left out so
    "all the spices the recipes need" stays true; the section is closed
    by default on LIST (less scrolling was half the ask), with its line
    "All the spices the recipes need. Tick the ones you need to buy."
    Six existing tests that read a recipe's olive oil / salt off the
    needed list now read the section as well — the amounts they pin are
    unchanged, only where the line waits moved.
- **2026-09-13 — Last week's leftovers: asked before they add onto this
  week. Branch `worktree-grocery-sorting-round`, NOT merged at the time of
  writing.** Loop Board bug (Emily: "some of the quantities are so high
  but I think it might be because it was adding on from last week's").
  Root cause, reproduced in `tests/test_grocery_carry_over.py`:
  `approve_weekly_plan` ingests through `add_grocery_item`, whose merge
  finds ANY still-'needed' line with the same name — including last
  week's unbought "2 lbs chicken thighs" — sums this week's 2 lbs into it
  and stamps the NEW plan's id on the row (`keep_standing` is only for
  hand adds). So the list said 4 lbs, nothing said why, and
  `clear_stale_grocery_items` could never take the old share off because
  the row now belonged to the new week. It fires every time a week is
  approved while the previous one still has a day left — any Sunday —
  because `_live_plan_ids` keeps a plan alive through its last day, so
  generation's cleanup pass leaves those lines in place. Same code path
  as the open "hand-added unit vs plan unit concatenates" card
  (`_try_consolidate_quantity` → `_repeat_or_concatenate`): that card is
  the same merge failing to reconcile units; this one is the merge
  succeeding on the wrong two weeks. Not fixed here.
  - **Fix: set aside, then ask.** On the transition into 'approved',
    `grocery.set_aside_carried_over_items` moves every 'needed' line from
    an earlier plan whose period has STARTED to `status = 'carried'`
    (`carried_from_plan_id` remembers whose), before a single ingredient
    lands — so this week's amounts go on clean lines. The Shop tab asks
    one screen before sorting: "Still on the list from last week — keep
    or drop?" (Emily's option (a), 2026-09-13), showing this week's own
    amount on its own line. Keep adds the old amount onto this week's
    line out loud (or restores the line as a standing want, source NULL,
    so no later cleanup deletes something asked for); Don't need
    soft-removes; both undo, and the wrap-up's already-have list skips
    both (`removed_by` carried_kept / carried_dropped).
  - **What is NOT a leftover, on purpose:** a hand-added line (a standing
    want — merges exactly as before), a staple's suggestion, an excluded
    line, anything already in a cart, and — the judgment call — a plan
    that has not started yet: approving two weeks ahead is building next
    week's list, and nobody has had a chance to buy it, so those still
    merge the old way. Unanswered 'carried' rows are cleared with the
    rest by `clear_stale_grocery_items` once their plan has gone by.

- **2026-09-13 — A way out of the Shop loop before every store is done.
  Branch `worktree-shop-exit`, NOT merged at the time of writing.** Loop
  Board "Shop: a way out of the Shop loop" (Bug, High, Beta). Emily, on
  her phone: "unless you complete all the shops, you get stuck in the
  Shop loop." Four things trapped her, all client-side in `static/shell.js`
  (there is no server-side trip; `shopping_trips` is a per-stop log):
  WHERE NEXT had no way to the root — its crumb reopens the stop just
  finished (on purpose) and its one button, "I'm done shopping for
  today", is what a shopper going home with a store still to do would
  say, and it led into "How did it go?" asking about a store she had not
  been to; TRIP and WRAP UP's "‹ Shop" crumb landed on a list identical
  to one with no trip on; "Start the trip" over a paused trip resumed at
  `tripIndex`, which still names the stop just FINISHED after "Done at
  Costco" (WHERE NEXT never moves it), so coming back reopened Costco and
  the only way on was to finish it again — the loop; and the trip was
  page-view state, gone with every relaunch of the installed app, leaving
  what had been ticked sitting `in_cart` on the server off every screen.
  Now: "Finish later" as a `.dock-link` beside every trip screen's own
  action (`groTripPauseLinkHtml`, `trip-pause`); WHERE NEXT's end button
  reads "Skip the rest"; LIST over a paused trip says "Trip in progress ·
  1 stop left" in the band and offers "Continue the trip" + "Finish the
  trip" (`groTripPausedDockHtml`, `groResumeTrip`, `groFinishTrip` — one
  finish function shared with WRAP UP); continuing lands on the current
  stop if still open, else on WHERE NEXT; and the trip is mirrored into
  localStorage per household (`groSaveTrip`/`groRestoreTrip`, read once
  on the first list load, kept `GRO_TRIP_KEEP_MS` = 3 days).
  - **Finishing from LIST is one tap and skips "How did it go?"** — the
    ask was "the remaining stores aren't happening", and the wrap-up's
    questions ("Couldn't find it" / "Somewhere else") are about stores
    you visited. It commits any trolley (`groFinishAnyRemainingCarts`),
    leaves everything needed as needed, and clears the snapshot.
  - **The mirror is never cleared before it has been read.** `goGroceryStep`
    saves on every step change, and an approval's "Open the list" or the
    first sort landing runs before the list loads — with no trip in memory
    yet, that save used to be a wipe. `groSaveTrip` refuses to remove the
    key until `tripRestored` is true. Caught by the harness, not by review.
  - The paused-trip helpers (`groTripPaused`, `groTripPausedLine`,
    `groInCartCount`) live with the renderers, above the "Actions" marker:
    `tests/test_stores_multiselect.py` slices the region there and calls
    `groDockHtml`, which reads them.
  - Tests: `tests/test_shop_trip_exit.py` (25, node harness), two copy
    markers updated for the renamed button, one root-band marker for the
    band's sub-line. 3370 on the branch.

- **2026-09-13 — Planning has a door that isn't Approve. Branch
  `worktree-planning-exit`, NOT merged at the time of writing.** Loop
  Board "Planning: a way out of the draft that isn't approving it" (Bug,
  High; Emily on her phone: "you can only exit the process by approving
  the week"). Two traps, both real, neither a modal or a route loop:
  - **`/plan-week` had no way out.** It is a standalone page with no tab
    bar by design (see its own header comment and `plan_week_page` in
    main.py), reached by `window.location.href` — and on an installed PWA
    there is no browser chrome either. Its first screen hid the stepper's
    "‹ Back" (`showStep`), the drafting screen hid it too, and nothing on
    any screen led anywhere but "Draft my week". It now carries ONE
    "‹ Plan" crumb above the eyebrow, outside the step sections so it is
    on every screen including the drafting one (`leaveFlow`). A plain
    navigation to `/week`, never `history.back()` (nav v2 rule 1).
    Leaving writes the current screen's answers as a revision — keepalive,
    never awaited, and only if something changed since the screen opened
    (`answersAtLoad`) — so coming back carries on, and a screen left
    untouched does not make the next visit narrate "carried on from your
    answers" about nothing. Leaving mid-draft saves nothing: the answers
    went with the tap, and `_stream_week_generation` runs on its own
    daemon thread, so the draft still lands, as a draft. The drafting
    screen says so in one line.
  - **The draft on Plan had no "More ···".** Since Build 3 (2026-09-11,
    `da12386`) a draft's root is the Review, and `reviewStepHtml`'s root
    form rendered the two views and Approve and nothing else — the More
    sheet's own "Try again" and "Change my answers" rows for a draft
    (`renderMealsMoreSheet`) existed but no button on a draft opened the
    sheet. The tab bar never hid, so this was not a trap in the strict
    sense, but "continue or start again" needs the sheet. The week root's
    `.wk-foot` with `#wk-more` is on the draft root now, above the dock;
    `wireMealsStep` already wired it. The deeper approved-week form keeps
    its crumb and gets no foot (one way back per screen).
  - What leaving does NOT do is pinned by `tests/test_planning_exit.py`:
    no approved plan, no grocery lines, no prep tasks, from either door,
    and "Try again"/"Change my answers" leave exactly one live draft.
  - **Known, left alone:** a draft for a week other than the one
    containing today is only shown when `?drafted=` hands it over; after
    any reload Plan shows today's week, and a next-week draft is behind
    "Plan next week ›" ("already has a draft"). Pre-existing, same after
    approving from chat; its own card if it bites.

- **2026-09-13 — Prep questions are a step of All set, not a footnote.
  Branch `worktree-prep-questions-step`, NOT merged at the time of
  writing.** Loop Board "Prep questions are a clear step, not a footnote"
  (Phase 1) plus the Bug card "All set screen: dark text on the dark
  background is hard to read". Emily, on her phone: the freezer and
  cook-ahead questions were "a subtle piece to skip". On the All set
  screen they now sit ABOVE the counters, under a 21px display headline
  ("Two quick ones before you go" — "if you like" was an apology), and
  both are OPEN from the start with no "Ask" fold (`fixed` lines in
  `weekQuickLineHtml`; `defrostAskCardHtml`/`cookAheadAskCardHtml` take
  `open` from the caller). One tap answers "None — all fresh", two answer
  with a chip. Skipping is the dock: leaving by "Open the list" / "See
  the week" is the deliberate tap, and the questions still come back on
  the root's receipt next session and from Cook's re-ask. The ROOT's
  receipt keeps its fold — there the week card is the point.
  - **An answered question collapses to its confirmation, in place**
    ("Chicken breast: move to the fridge Monday night · Salmon fillets:
    Thursday night"; "Roasted Chickpeas: one batch Tuesday covers
    Thursday"). Page-view state (`weekQuickDone`, keyed to the plan),
    same reasoning as `weekQuickOpen`: the server already holds the
    answer as a prep task / a chain, this is the screen keeping its word
    that the tap landed. Built from the confirm responses' own rows
    (`created`, `applied`), never from a second fetch. A Cook re-ask
    clears the line first, or the old answer would stand in for the
    question while items refetch (verifier).
  - **The contrast bug was the ivory card's inks on spruce.** The asks'
    bodies were written for the ivory receipt card; on `--spruce-raised`
    their `--ink-secondary` helper lines measured 2.37:1 and the
    cook-ahead sentence in `--ink` 1.21:1 (light). Re-inked on spruce
    only: `--ivory-ink` 9.86/7.70, `--ivory-ink-muted` 8.62/6.52
    (light/dark, in the CSS comment and in
    `tests/test_prep_questions_step.py`, which measures theme.css). A
    ticked chip is solid `--celadon` + `--on-accent-ink` there (the cook
    hero's own recipe) — the tint it used to take is 1.38:1 against the
    card in dark. The quiet answer button takes the cook hero's outline
    (`--apricot-rule` edge, `--ivory-ink` label). Nothing on the ivory
    card changed.
  - **The doubled title was the single-dish case.** With one repeated
    dish the fold's heading already IS "X on 2 nights. Cook ahead?", and
    the block under it said "X is on 2 nights. Cook ahead?" again —
    `cookAheadAskBlockHtml(item, named)` names the block only when two or
    more dishes share the heading "Cook anything ahead?". Same fix on the
    root's receipt, which had the same duplicate behind "Ask".

- **2026-09-13 — "Tell me what instead" knows which meal it was tapped on,
  and the chat request has a `context` field now. Branch
  `worktree-tell-me-instead-context`, NOT merged at the time of writing.**
  Loop Board "'Tell me what instead' knows which meal you tapped it on,
  and acts on one yes" (Emily, on the Tuesday burgers: she tapped the link
  beside the recipe, chat had no idea which meal she meant, then asked her
  to confirm too many times).
  - **Root cause, both halves.** The link put a sentence in the composer
    ("Swap Tuesday's dinner for something else", shell.js's `[data-wk-tell]`
    handler) and nothing else: `ChatRequest` was `{session_id, message}`,
    and the only "context" the agent ever had was `_is_tweak_context`
    sniffing a prefill prefix (agent.py, `TWEAK_CONTEXT_PREFIXES` — its own
    comment says "the chat endpoint carries no context field of its
    own"). Delete the sentence, type what you want, and the model is
    starting from zero. The repeated confirmations then follow from three
    standing prompt rules stacking on one change: "Clarifying questions"
    (which meal?), the propose-then-confirm rule in the same paragraph,
    and the grocery-list rule ("nothing reaches the list without the
    household saying so"), which the model applies again after a swap on
    an approved week even though `swap_meal_in_plan` already moved the
    lines. That's from the prompt, not a transcript — the conversation
    itself isn't stored.
  - **The mechanism is structured, server-resolved, per-turn.**
    `ChatRequest.context` is a pointer (`{kind, entry_id, date, slot}`),
    never a description: `tools.describe_planned_meal` re-reads the meal
    from the household's own live plan each turn, by id first and then by
    date+slot, because a swap deletes and re-inserts the row and "actually,
    chicken" one message later must still land. `_build_chat_context_block`
    appends a system block (like `_TWEAK_REPLY_BLOCK`, never an edit to the
    frozen cached prompt) naming the meal, its ingredients, whether the
    week is approved, and the rule: confirm ONCE in one line, act on any
    yes, no second question, no grocery-list question. The shell sends the
    context with every message while the "About Tuesday's dinner · …" line
    is above the composer, and drops it when the sheet closes or the × is
    tapped — sent every turn, not once, so the yes carries the subject and
    the rule with it. `kind` is the seam for other "open chat about X"
    entry points; only `planned_meal` is wired.
  - **Anything unresolvable is an ordinary turn, never an error**: a
    planned_empty or open slot, a component plan, another household's id,
    a bad date — all None, all logged, block omitted. A same-dish change
    (turkey → beef) is add_recipe of a named variant then
    swap_meal_in_plan with old_meal, which is what keeps the slot and moves
    the grocery lines by itself; the block says so in those words so the
    model doesn't invent a third path. `context` is only passed to
    run_agent_turn when present, so every existing test fake with the old
    signature still fits.

- **2026-09-13 — "Plan next week ›" under a two-day plan offered two more
  days. Branch `worktree-sunday-week-span`, NOT merged at the time of
  writing.** Loop Board "Planning on a Sunday offered only the next 2
  days instead of the week" (Bug, High). Emily, Sunday 2026-09-13 on her
  phone: the default offered Mon–Tue, and her All set screen read "Sep
  14–15 is planned … 6 meals · 6 cooks".
  - **Root cause: the link was sized by the plan on screen, on the
    client.** `#wk-plan-next` (shell.js) computed "period_start +
    day_count, for day_count days" from the week-menu payload. Her plan on
    screen was a TWO-day one — a Saturday sign-up's "this week" is Sat–Sun
    (`main._first_plan_window`, pinned in the new test; a custom range or
    a takeover remnant does the same) — so the week after it was two days.
    Now's nudge, which reads the rhythm, asked about Sep 14–20 at the same
    moment: the two-screens-two-weeks class the 2026-09-11 "one source of
    which week" rule was written for, and this was the one link still
    deriving its own. Reproduced with the date pinned to that Sunday.
  - **Fixed on the server.** New `weekly_plan.next_period_after(plan)`,
    carried on `get_week_menu` as `next_period`: the day after the plan's
    last day, for the RHYTHM's length (`suggest_planning_period`'s
    day_count — seven, or three as-we-go), which is exactly what the nudge
    offers from Friday. A plan whose days have already passed (the
    approved-week fallback) gets the standing suggestion instead, with
    `is_current_period` so the link can say "this week". The client reads
    it through one `nextPeriodFor` for both the label and the tap, with
    the old arithmetic kept only as the fallback for a payload without it
    — deliberately small, because the seven-tiles picker is being rebuilt
    on `worktree-week-tiles`.
  - **A shorter span says why, in one line — and that is the only time it
    is shorter.** If another live plan already holds a day inside the
    stretch, the offer stops the day before it and `shortened_reason`
    ("Sep 17–20 is already planned.") rides into `weekNotesHtml`'s quiet
    lines above the link. Before, "Plan next week" over such a week would
    have generated the whole seven and taken those days over — and the
    question screen's warning would not have fired, since
    `get_week_intake_prefill` looks a plan up by filing key only. A
    stretch held from its FIRST day is offered whole as a re-plan
    (`is_planned`, so the link says "Re-plan next week"). **Not shortened
    for a trip** (judgment call): a night away is a `planned_empty` slot
    inside the week, not a reason to plan a shorter one.
  - Nudge untouched: from Friday it still says nothing when the following
    period is held at all, so Plan may offer a shortened stretch Now is
    quiet about — quiet is not a contradiction. `tests/
    test_sunday_next_week_span.py`, 18 tests (17 red on `eaf334f`; the
    green one characterises the Saturday two-day part-week as intended).

- **2026-09-13 — Recipe quantities pass a sanity check (no stick of
  butter in a 2-serving soup). Branch `worktree-recipe-quantity-sanity`,
  NOT merged at the time of writing.** Emily, on her phone, Turkish-Style
  Lentil Soup, Serves 2: "½ cups Red lentils · 1 lb Carrots · 1 stick
  Butter · 1 bunch Mint". "One stick of butter is a crazy amount for this
  whole recipe."
  - **Root cause, reproduced with the recipe saved exactly as the prompt
    asks for it.** It was NOT the serves scaler and NOT a recipe for four
    scaled down: the card matches a recipe generated for the household's
    own table of two, where every `qty` is the SHOPPING line
    (`generate_weekly_plan_llm`'s "how it's actually bought" bullet —
    butter is bought by the stick, carrots by the pound). The cook view
    keeps a shopping qty whenever it measures something
    (`recipes.cooking_ingredients`), and "stick" is in
    `_EXTRA_MEASURED_UNITS` as a real kitchen unit, so `_quantity_problem`
    passed it through; `scale_recipe` then kept it whole because a stick is
    in `_DISCRETE_UNITS` (`max(1, round(0.5))`). Nothing anywhere asked
    whether the AMOUNT made sense for the number of people. "½ cups" is
    `quantities._format_quantity` pluralising everything but exactly 1;
    shell.js's `humanQtyText` only rewrites the number.
  - **The guard is deterministic and there is no new model call.**
    `recipes._PLAUSIBLE_PER_SERVING`: seven ingredient classes (fat, salt,
    sugar, aromatic, spice, protein, grain) with a per-serving ceiling
    each; `implausible_quantity` judges a cooking amount against it and
    `plausible_cooking_quantity` replaces one that is over with the app's
    own figure for the item (`COOKING_QUANTITIES_PER_4`, scaled — the same
    table that already turns "1 bottle" into "2 tbsp", so the two
    corrections cannot disagree) or, for an item the table has never met,
    the ceiling in the line's own unit. It runs in `cooking_ingredients`
    (so every recipe already saved is covered at read time) and before a
    save (`add_recipe` and `save_cooking_quantities` write the corrected
    `cook_qty`; the shopping qty is never touched — one stick is still
    what you buy). Rescales SILENTLY for the cook and FLAGS separately:
    `plan_quality._quantities_plausible` is an "info" rule that reports
    the line as the model wrote it and what the cook view shows, into the
    morning report like `_steps_match_ingredients`. A repair call was
    rejected on cost ($1/household/month) and because the table answers
    the same question for free; the fill path's `validate_measured_
    quantities` is deliberately still called WITHOUT `servings` so an
    out-of-range amount never turns into a paid repair round.
  - **The ranges are ceilings, generous, and per serving** (fat 2 tbsp,
    salt 1.5 tsp, sugar ¼ cup, dried spice 2 tsp, aromatics 4 cloves /
    1 tbsp ginger — no bare-count ceiling, because "12 garlic knots" and
    "24 onion rings" are counted dishes wearing an aromatic's name, found
    on review; protein 1 lb, grain 2 cups or 8 oz dry). Every floor is
    zero: nobody has complained of too little, and 2 oz of bacon
    flavouring a soup is not a mistake. A stick of butter for FOUR is
    exactly 2 tbsp a head and passes; the same stick for two does not. A
    baking recipe that says "serves 4" with a cup of butter would be
    shown 2 tbsp — accepted, this is a dinner planner, and the import path
    carries the recipe's own yield ("24 cookies" → 24). Names that borrow
    a class word ("sugar snap peas", "green beans", "low-fat yogurt",
    "garlic bread") are listed in `_NOT_THIS_CLASS` and not judged; the
    pre-save pass keys its fixes by line position, so two lines that
    share a name are each judged on their own (also found on review).
  - **A stick cut to a fraction is written in tablespoons** (`scale_recipe`,
    `_STICK_TBSP = 8`): four-person stick halved is "4 tbsp", not rounded
    back up to a stick; a whole number of sticks stays sticks.
  - **One or less is singular** in `_format_quantity` ("0.5 cup", "0.75
    lb"), which is what reads "½ cup" once the front end has done the
    fraction. Two ledger tests that pinned "0.6 lbs" were updated;
    parsing reads either form so nothing stored needs rewriting.
  - **Left out on purpose:** "1 lb Carrots" for two is at the top of what
    a produce ceiling would allow and there is no vegetable class — a
    carrot-heavy soup for two really can use a pound; a vegetable class is
    a one-line addition if Emily wants one. Variety naming (Persian vs
    English cucumber) and grocery merging are separate cards.

- **2026-09-13 — Un-ticking a bought staple un-teaches it, and an item
  kept in two places lands on one stated kitchen row. Branch
  `worktree-grocery-followups`, NOT merged at the time of writing.** Two
  Loop Board follow-ups to the grocery re-tick fix below: "Un-ticking a
  bought staple doesn't un-teach it" (Bug, Medium, Phase 0 — the one that
  entry reported and did not fix) and "An item kept in two places merges
  into whichever row the database returns first" (Bug, Low, Phase 1).
  - **The bought event remembers which grocery line created it, and what
    the rhythm read on either side.** `staples.record_staple_purchase` ran
    on every line turning 'purchased' and wrote one `bought` event per
    staple per day; the untick reversed the kitchen and never touched the
    staple, so an un-bought staple still believed it was bought today (last
    bought today, next due a whole cadence out, and a cadence could turn
    "learned" from a date nobody bought on). Now two nullable columns on
    `staple_events` (schema.sql + `_MIGRATIONS`, NOT backfilled):
    `grocery_item_id` — the line whose tick created the event — and
    `receipt_json` — `cadence_days / cadence_source / last_bought_at /
    next_due_at / skip_streak / paused` before and after the tick, the
    same recorded-not-derived shape as `inventory_receipt_json`. New
    `staples.unrecord_staple_purchase`, called from `mark_grocery_item` on
    every line LEAVING 'purchased' (after the commit, own connection, the
    mirror of the record call): removes TODAY's event only when it stands
    on this very line, then puts the recorded "before" back if the staple
    still reads exactly the recorded "after"; if something changed the
    staple in between (a told cadence, a pause) the event still goes and
    the rhythm is re-learned from the dates that remain, with
    `last_bought_at` cleared first so the un-bought date cannot anchor
    next_due. Re-ticking records it again — still one per day. Earlier
    days are never touched.
  - **Attribution — the one-per-day rule means one event can have two
    parents, and the event goes to nobody when it does.** A second same-day
    source (another list line, or any caller with no line — a receipt-scan
    hook, chat) that finds the day already bought sets the event's
    `grocery_item_id` to NULL: it now stands on more than one thing, and no
    untick removes it. Chosen over a parents table because nothing but the
    tick calls `record_staple_purchase` today (checked: receipt scans go to
    inventory, not staples — the card's receipt case is a contract for the
    hook that does not yet exist, tested by calling
    `record_staple_purchase(source="receipt")` directly). The known cost:
    two separate lines for one staple ticked the same day (possible only
    when the first was already purchased when the second was added, since
    the add path merges needed lines) and then BOTH un-ticked leaves one
    phantom bought date. Recorded in the test file as the accepted corner.
    Why `receipt_json` and not recompute-only: `_relearn` keeps whatever
    cadence it finds when intervals drop below two, so a cadence that
    became "learned" on the un-bought date would have stayed learned; a
    "not this trip" push of next_due would have been replaced by
    last_bought + cadence. Both reproduced in the tests.
  - **Events from before the columns are never removed** — NULL
    `grocery_item_id` reads as "not this line's", which is the truth since
    nothing recorded which line wrote them. A purchased line REMOVED (⋯ →
    Remove) rather than un-ticked still leaves the event, same as it leaves
    the kitchen — the untick is the only reversal, as before.
  - **`tests/test_chores_switch.py::test_the_migration_adds_the_column_to_
    an_existing_database` now runs schema.sql over the snapshot before
    `_MIGRATIONS`, the way `init_db` does.** It ran `_MIGRATIONS` alone
    over the pre-chores snapshot, which predates `staple_events`; this is
    the first migration on a table newer than that snapshot, so the ALTER
    hit "no such table". No real database takes that path — `init_db`
    always creates tables first. The drift guard
    (`test_schema_migration_drift.py`) already did it the right way.
  - **Two places, one row: the most recently written, ties to the newer
    (`updated_at DESC, id DESC`), on add, set AND use.** `_add_to_inventory`
    matched by `LOWER(item)` with `fetchone()` and no `ORDER BY` when no
    location was given; "set" and "use" had the same shape ("use" already
    called it a known limitation). With BBQ sauce open in the fridge and
    unopened in the pantry, SQLite returned whichever it liked — an add
    could merge into one row and the next "used some" subtract from the
    other. Now one helper, `inventory._find_row_by_name`, used by all
    three. The argument: the row the household last touched is the one in
    play; every name-only path agrees, so add-then-use land on the same
    row; and the rule is stated, so a wrong pick is explainable. The
    card's first preference — the item's usual location — does not exist:
    checked staples (no location column, by design), grocery lines (none)
    and inventory (location is an explicit hint or the category default
    at insert, `_resolve_location`); nothing per-item remembers a place.
    Category default was considered for the ADD side (a new bottle goes
    where an unopened one lives) and rejected as the shared rule because
    it is actively wrong on the USE side ("used the last of the BBQ
    sauce" would delete the unopened pantry bottle) and the card asked for
    one rule. Not queue-don't-guess: no case where the recent-row pick is
    worse than arbitrary, and inventory is deferred (Emily 2026-09-01) —
    no new screen. A location hint still wins outright, as before. The
    grocery untick restores by the receipt's `inventory_id`, never by
    name — confirmed and tested with the other row edited (and made the
    newest) between tick and untick.
  - `tests/test_staple_untick_unteaches.py` (14, all red on the merge
    base) and `tests/test_inventory_two_places_pick.py` (10, 5 red on the
    merge base — the 5 where the arbitrary pick happened to coincide with
    id order were green by luck). Suite 3514 -> **3538 passed**. Also
    driven over a real uvicorn on a throwaway DB: Coffee tick -> event 2
    (grocery_item_id 1, receipt) -> untick -> no bought event, kitchen
    empty -> re-tick -> event 3, one bag.
  - **Separate card, maybe:** a per-item "usual place" (or the category
    default on the add side only) is the thing that would make the add
    side smarter than "most recent"; and the two-lines-both-unticked
    phantom date above if it ever shows up in real use.
- **2026-09-13 — A starter chore list from what Pomona already knows.
  Branch `worktree-chores-starter-list`, NOT merged at the time of
  writing.** Loop Board "Chores v1: A starter list from what Pomona
  already knows" (Phase 2). Setup proposes the list; the household keeps,
  tweaks and drops instead of typing housework out, and nobody becomes
  the administrator.
  - **The list is rules, not the model — and the model can only adjust
    it.** Before: `generate_chore_recommendations` was one forced tool
    call with nothing underneath, so a house could be proposed a list
    with no laundry, no bins and nothing seasonal, and the route only knew
    what the page posted. Now `app/tools/chore_starter.py` builds the
    baseline from facts (bathrooms → one row each while sayable; laundry;
    garbage / recycling / green bin; yard rows iff `has_yard`; pet CARE
    per kind — daily walks or litter, monthly flea/tick, yearly vet,
    grooming — iff pets; furnace filter / gutters / winter tires iff a
    house; patio furniture iff a yard; closet swap, smoke detectors,
    windows, oven, monthly tidy-and-donate for everyone) and deals an
    owner onto every row. Claude is then asked ONLY for `chores` (adds),
    `drop` and `change` over that list (`_merge_chore_adjustments`);
    `NEVER_DROPPED` = Laundry + Garbage out, which the model cannot
    remove — the household does, by hand. **Any API failure returns the
    baseline** (found in live verification: a 401 is not an
    `AssistantUnavailableError`, and a 500 from the starter list would
    have been the wrong answer to "show me a list").
  - **Owner rule, stated:** dealt round `rotation_members` in the order
    setup named them, top to bottom, one row each; a row the described
    help covers is tagged outsourced and skips the deal; nobody named →
    every row `whoever`. No `shared` proposals — owned is the default the
    owner card chose, and a proposal is one tap to change.
  - **Two new rhythms: `semiannual` (182) and `yearly` (365)** in
    `_FREQUENCY_DAYS`, the three tool enums, `CHORE_RHYTHM_LABELS`/`ORDER`
    in shell.js ("Twice a year", "Once a year") and the schema comment.
    Twice-a-year things squeezed into "quarterly" would have been asked
    for at the wrong time. `FREQUENCY_WORDS` (chores.py) is the Python
    twin of the JS labels and a test pins the two dicts equal. Words are
    the shell's existing ones ("Every two weeks" — the card's "every
    couple of weeks" was NOT adopted, flagged for Emily).
  - **Setup never asks again.** `GET /api/onboarding/chores/known` (people,
    adults, pets, `home.known` + facts, saved profile, rhythm picker).
    `ChoreProfileRequest` fields are now `Optional`, None = "not asked this
    call": `/recommend` merges answers over the saved profile
    (`profile_for_starter`), pets from the table, adults when nobody was
    named; `/chores-profile` treats None as the old defaults so the skip
    path saves exactly what it always did. `/recommend` writes nothing —
    not even the profile; the keep and the skip both save it.
    `/chores/save` now refuses a rhythm it can't keep (reported in
    `skipped`, like a stray name) and returns `scheduled`.
  - **The chat path is the same list.** `get_starter_chore_list` (gated
    chores tool) = saved profile + pets + adults → the rules; the system
    prompt's chores walk-through now asks only what isn't on file, saves
    with `set_chores_profile`, reads this list back in words, then
    `add_chore` per kept row + `generate_chore_schedule`. "Never type out
    a list of your own instead."
  - **chores-setup.html got the smallest review that exposes the
    contract** (prefill from `/known`, pets shown not asked, per-row
    owner/rhythm/"Someone else does it"/drop, "Keep these" vs "Not now").
    The sibling card "Setup becomes a step under Chores" builds the real
    screen; this page and `GET /chores-setup` were deliberately kept.
  - **Left alone, and a card:** every new chore's first occurrence is
    TODAY (`_next_due_date`'s "never scheduled → today"), so keeping 28
    rows puts 28 on Now on day one, the yearly vet visit included.
    Pre-existing; staggering the first fortnight is a product call.
  - **Verifier's two catches, fixed:** the help's "How often?" answer now
    re-rhythms EVERY row the help is tagged onto (the lawn people's
    fortnight is when the lawn is mown), not just the cleaning rows — one
    question was asked, so one answer applies, and review corrects the
    odd row. And the pure function no longer crashes on what a saved
    profile or the chat can hand it: a non-numeric room count reads as
    "not known" (`_count` → 0, the same one row an unknown home gets)
    and a non-string in the rotation is nobody.
  - Tests: `tests/test_chore_starter_list.py` (37, one section per
    acceptance criterion) plus one parametrize case in
    `test_chores_switch.py`; three older tests re-pinned to the new
    contract (recommend returns baseline + adds; save's body gained
    `scheduled`; the gated-tool set). 3383 passed.
- **2026-09-13 — Un-tick and re-tick a bought grocery line and the kitchen
  holds ONE of it. Branch `worktree-grocery-retick`, NOT merged at the time
  of writing.** Loop Board "Un-tick and re-tick a bought grocery item and
  it goes into the kitchen twice" (Bug, Phase 0). `grocery.mark_grocery_item`
  added a purchased line to inventory on every transition INTO 'purchased';
  its 2026-09-11 no-op guard (`app/tools/grocery.py`, the
  `row["status"] == status` early return) compares statuses, so it catches
  the offline replay it was written for and nothing else — purchased ->
  needed -> purchased changes the status each time. Reproduced in-process
  and over the real route: Eggs 12 -> 12 -> 24. The sibling of the cook
  tick entry below, found and reported by it.
  - **Memory is its own column, `grocery_items.inventory_added_at`**
    (schema.sql + `_MIGRATIONS`, nullable, NOT backfilled), same reasoning
    as `inventory_depleted_at`: status forgets. The add runs only while the
    stamp is NULL. **Claimed inside the same `BEGIN IMMEDIATE` that flips
    the status** — the status route is a sync def in a threadpool, and two
    'purchased' posts for one line at the same instant doubled on **20/20**
    trials before (the old guard had the same race; it read the status on
    one connection and wrote on another). `_add_to_inventory` grew a `conn`
    parameter so the inventory write, the stamp and the receipt land in
    one transaction or not at all. 0/20 after.
  - **UN-TICKING PUTS IT BACK — when it can prove exactly what it added,
    and only then. The opposite call from the cook tick, deliberately, and
    from the code rather than by analogy.** What a purchase writes is
    KNOWN at the moment it writes it: `_add_to_inventory` either inserts a
    fresh row (the line's own quantity, category, an estimated expiry) or
    merges into a same-name row via `_try_consolidate_quantity` and
    overwrites its `source` and `category`. A depletion has no ledger and
    deletes rows at zero; a purchase can write down what it did. So the
    tick records a receipt, `grocery_items.inventory_receipt_json`: which
    inventory row, `fresh` or merge, and the row's quantity / source /
    category / expiration_date / updated_at / rev on BOTH sides of the
    write. The untick reverses ONLY when the row still reads exactly the
    "after", **proven by a new `inventory_items.rev`** — a per-row write
    counter bumped by a trigger (`inventory_items_bump_rev`, schema.sql)
    on EVERY update of any column — and then does the exact inverse:
    deletes a fresh row (the call
    `undo_pre_shop_drop` already makes on `already_have_inventory_id`, the
    precedent this extends), or puts a merged row back to its recorded
    "before" — never "now minus what we added", nothing computed. Row
    gone, row touched, no receipt: the kitchen is left alone, the result
    says `inventory_restored: False`, and **the stamp STAYS**, so the
    re-tick adds nothing on top of what is already there (`inventory_added:
    False`). The answers to the card's questions: after the add the
    quantity is recoverable because it is recorded, not derived; if the
    household used some in between, the 8 they have is their number now
    and stays, and the re-tick does not put 12 more on it; if the row was
    merged, the before-state is restored exactly, category included (the
    tick's 'other' overwriting a 'pantry' row comes back 'pantry'). A row
    is never reduced or deleted on anything less than "this is the row and
    it is exactly as we left it". **Emily's to overrule** in either
    direction: never restoring (the cook-tick stance) would leave phantom
    eggs the next list is shopped against; restoring on quantity alone
    would occasionally undo a hand-set number.
  - **The first cut used `updated_at` as the proof and an independent
    verifier broke it the same day — recorded here because the mistake is
    easy to make again.** `datetime('now')` is whole-second, so "set to 8,
    set back to 12" inside the tick's own second read as the tick's own
    write, and the untick reverted (or deleted) a row that had been
    touched twice; a location-only edit in that second slipped through the
    same way. Worse, the first test file sidestepped it by forcing
    `updated_at` into the past. Now: `rev`, a trigger rather than a
    `rev = rev + 1` in each of the eight writers, so a ninth writer that
    forgets still counts — the proof is the database's, not every future
    caller's; two writes in one millisecond are two bumps. The trigger is
    declared in schema.sql and the column it bumps is added by
    `_MIGRATIONS` in the same `init_db` call (SQLite does not resolve a
    trigger body's columns until it runs — checked). The tests now run the
    real sequences with no sleep and no timestamp poking, plus one that
    walks every writer and asserts the bump.
  - **Row GONE between tick and untick clears the stamp; row TOUCHED keeps
    it — two different certainties, decided separately.** The verifier
    flagged that a deleted row left the stamp in place, so that grocery
    line could never re-enter the kitchen by re-tick, permanently and
    silently — the moment somebody tidies the kitchen by hand before
    fixing the list (a coherent thing to do), the line is locked out for
    good. A gone row also has nothing left to double onto, which is the
    whole reason the stamp exists. So `_restore_inventory_from_receipt`
    returns `RESTORED` / `ROW_GONE` / `LEFT_ALONE`; the first two clear
    the stamp, only the first says `inventory_restored: True`. The cost:
    one contrived sequence (eat all twelve, THEN untick, then re-tick)
    puts twelve back. A merged row used down to zero (which deletes) is
    gone in the same sense and treated the same.
  - **Not backfilled**, so a line already 'purchased' before this deploys
    has a NULL stamp and no receipt: an untick there leaves the kitchen
    alone, and the re-tick adds ONCE more, then never again. Same bounded
    one-off the sibling column accepted, for the same reason — a startup
    backfill would stamp rows whose inventory may long since be eaten.
  - The agent's `mark_grocery_item` tool description now says to read
    `inventory_added` / `inventory_restored` before telling the household
    what happened to the kitchen. `update_inventory(action="add")` still
    returns `{"item_id", "item"}` — the receipt fields are stripped there.
  - `tests/test_grocery_retick_double_adds.py`, 28 tests, 21 of the
    original 22 red against the merge base (the 22nd is the replay guard,
    kept green on purpose) and the same-second trio red against the first
    cut; tick/untick/retick, purchased -> in_cart -> purchased, fresh and
    merge reversals with source/category/expiry, set-down-and-back /
    location-only / expiry-nudge / stepped / cook-depleted rows left
    alone with no sleep anywhere, every writer bumps `rev`, the gone row
    (deleted, and used-to-zero) clearing the stamp, the pre-column line,
    20 concurrent pairs, the route, and a migration test on a DB derived
    from today's schema minus the three columns (the
    `test_chore_owner_mode` pattern; the trigger exists on that old file
    before its column does and bumps once the column lands). Suite
    3345 -> **3373 passed**. Also driven over a real uvicorn on a
    throwaway DB: Eggs 12 -> gone -> 12 -> unchanged; Butter 6 -> 12 -> 6
    -> 12 (merge); Yogurt 12 -> 8 (stepper) -> untick leaves 8 -> retick
    leaves 8.
  - **Reported, not fixed:** `staples.record_staple_purchase` writes a
    'bought' event on the tick and nothing takes it back on an untick, so
    a staple un-bought today still believes it was bought today and learns
    its rhythm from that date (one event per day, so a re-tick teaches
    nothing twice — the untick is the gap). Its own card.
- **2026-09-13 — Now is one strip down the day. Branch
  `worktree-now-strip`, NOT merged at the time of writing.** Emily picked
  it on 2026-09-12 from the "Beyond lists" canvas (artboard "Now · A · The
  day as a strip"). Now's content — the sand next-up card plus "The rest
  of today" / "Done today" — is ONE vertical strip now: every move is a
  node on a `44px | 1fr` grid (`dayStripNodeHtml` / `.day-strip` in
  `static/shell.js` / `shell.css`, "NOW — the day as a strip" section).
  Left: the time as a 10px/800 eyebrow ("8:00", "NOON", "6:30"; "TODAY"
  for a shop, "TONIGHT" for a fridge or prep move), a 28px dot, a 1.5px
  `--hairline` down to the next node. The dot is the state AND the tick
  (44px of tap around it, Rule 6): done = `--celadon` + a tick, now =
  `--apricot` + the move's icon, later = `--surface` + `--hairline-strong`
  + the icon, all glyphs in `--on-accent-ink` on a fill (Rule 1). Right:
  title + one meta line (`move.detail`); the next-up move is the ONE
  tinted node — a `--celadon-tint` tile with a NOW eyebrow (§2b S3, S6) —
  and its action stays in the dock, unchanged. The whole body is the tap
  target for "open this move" (52px+); a done node's dish name stays a
  link, as before. Ticking settles with the grocery row's transitions
  (`todayAnimateNodeSettle`, the same opposite-state-then-reflow trick as
  `groAnimateRowSettle`; the strip's selectors ride animation 3's rules in
  the Motion section — not a fourth animation). The tomorrow card and the
  empty moment are untouched and sit after the strip. `/api/today/moves`
  already carried everything; nothing added to `app/`. Old card/row CSS
  retired (`.nextup-card`, `.rest-*`); `.nextup-when` stays because Plan's
  Meal step hero still uses it, and the shared `.tick` stays for the
  chores rows. Verified in the browser at 375×812 light and dark and at
  1280 against a throwaway copy of the pre-reset backup with a seeded day
  (a made-ahead breakfast, lunch, dinner, a fridge move, 60 needed
  groceries). `tests/test_now_day_strip.py` (15). Judgment calls for
  Emily: (1) **order** — moves.py sorts by `window_start`, which put every
  all-day move first and would have read "TONIGHT" above breakfast; the
  strip sorts by where a move sits on the day (`dayStripOrder`: a shop
  at the top as "TODAY", a fridge/prep move at the foot as "TONIGHT" —
  the old "Before bed" slot), server order kept between ties; (2) **the
  tile's meta ink** — the mockup said `--ink-secondary`, which measures
  3.98:1 on `--celadon-tint` (under AA at 14px); it is `--celadon-label`
  (4.87:1 light / 5.92:1 dark, the token for text inside a celadon tile,
  what the tomorrow card already uses); (3) **two apricot fills** — the
  now dot and the dock's button are both apricot, as the picked mockup
  draws them (Rule 5 says one per screen; the dot is 28px, but it is a
  second one); (4) the NOW tile carries `move.reason` as a second small
  line when there is one ("for Thursday's skewers") — the old card's
  accent line, kept so a fridge move still says what it is for; (5) the
  eyebrow tracks at .06em rather than the .13em eyebrow norm so "TONIGHT"
  fits the 44px rail (it still overflows ~3px each side); (6) the
  needs-you band (tonight's open dinner, "Tomorrow needs a dinner") stays
  ABOVE the strip, since an undecided dinner is what's next and suppresses
  the tinted node, as before. Not done: `move.detail` still repeats the
  clock the rail shows ("dinner · 25 min · 6:30"); dropping it is a
  moves.py change left for a follow-up.
- **2026-09-13 — The meal screen is a clock. Branch `worktree-meal-clock`,
  NOT merged at the time of writing.** Emily picked "Meal · B · The clock"
  from the Beyond-lists canvas on 2026-09-12. Plan › a day › a meal is now
  a spruce hero in the gutter ("DINNER · MONDAY", "On the table by half
  six", the dish, "Start at 6:00" and — only when `cooking_role` is one
  named person — "Emily's cooking"; the thaw note as its one line), one
  eyebrow ("About thirty minutes, seven stops"), and the cook as stops on a
  spine: "Everything out" first (names on the line, amounts one tap in via
  `humanQtyText`), then one stop per instruction with the time it lands at.
  The dock is "Start at 6:00" / "Start cooking" / "Keep cooking" (a step
  already ticked in cook mode's store) with "Swap this meal" as the quiet
  link. "The plate" chips card and the bullet-list "The recipe" card are
  gone (`plateCardHtml`, `mealRecipeCardHtml`, `.wk-recipe-card` deleted);
  the cook-ahead picker keeps its card under the stops. **The timing rule**
  (`mealClockStops`, pure, unit-tested under node in
  `tests/test_meal_clock.py`): start = the slot's table time
  (`get_week_menu`'s `slot_times`, read back into minutes by
  `slotTableMinutes` — the label has no am/pm, the slot supplies it) minus
  the recipe's prep + cook minutes off the cooker-view card (moves.py's own
  "Start by" arithmetic). No recipe carries per-step minutes
  (`instructions_json` is a list of strings), so the stops are SPREAD
  evenly from the start to the table time, the last one landing on the
  table, each rounded to the nearest five minutes and marked `estimated` —
  which is what makes the eyebrow say "About". A `step_minutes` array, if a
  recipe ever carries one, gets exact unrounded times and no "About". No
  total minutes: stops with no times and the eyebrow "Six stops". Never
  seconds. **The stops and cook mode's steps are one list** — both read
  `cookMeal.instructions` off `/api/cooker-view`, which is why the screen
  builds off the Cook view's card (`cookMealForEntry`) and not the week
  entry; verified in the browser (7 stops ↔ "Step 1 of 6" + Everything
  out). Judgment calls: (1) a stop's title is the step's opening clause
  when it is ≤4 words, else its first three words (fewer if that ends on a
  joining word) with the WHOLE step as the line — a line starting
  mid-phrase read worse than a few repeated words; (2) the swap line's idle
  "Tell me what instead" is not rendered in this dock (it stays on the Day
  step's cards) — the dock is one action and one quiet link; (3) the
  "Serves N" and plate-note chips are gone with the design's two chips;
  (4) a recipe with no ingredients gets no "Everything out" stop rather
  than an empty one; (5) the cook's name comes from `/api/memory`'s
  rhythm, fetched once per page for the Plan tab (`ensureRhythmForMeals`),
  and the chip is omitted for "turns"/"whoever's free"/unanswered rather
  than guessed. The chevron on "Everything out" turns without a transition
  (§4: three animations, all spoken for).
- **2026-09-13 — Plan › Which days is seven tiles, and a night can be
  moved. Branch `worktree-week-tiles`, NOT merged at the time of writing.**
  Emily picked "Week · A · Seven tiles" from the Beyond lists canvas
  (2026-09-12). One tile a night — date | dinner + a 5px bar for how long
  (minutes/95, floor 18%, cap 150px; apricot from 50 min, celadon under) |
  a 44px drag handle — replacing the expanding day card (`reviewDayCardHtml`
  and the `.rv-day-*` rules are gone; `reviewDayTileHtml`/`reviewDaysHtml`
  and `.rv-tile*` in their place). The five slots a card used to expand
  into are the Day step's now: the tile's body opens it (§2b S8).
  - **Moving a night trades the two DINNERS and nothing else.** New
    `tools.swap_dinner_nights(plan_id, date_a, date_b)` (weekly_plan.py)
    re-dates the dinner rows IN PLACE — ids kept, so grocery links, the
    cooked tick and the plate sides ride along and **the grocery list is
    never touched** (same dishes, same lines). What is keyed by DATE moves
    by hand: leftover-chain `links_to`/`make_double_for` references are
    rewritten to the new nights, and defrost `prep_tasks` naming a moved
    entry shift by the same number of days (status kept, weekday re-said).
    Prep-cut rows stay on the prep DAY (a rhythm fact); 'general' LLM prep
    tasks carry no entry id and are left for `generate_prep_schedule`.
    slot_needs/attendance stay with the day — "Emily is out Thursday" is
    about Thursday, not the dish.
  - **Refusals are answers (`status: 'refused'`), never writes:** a night
    nobody is home (`planned_empty`), a dinner already cooked, or a chain
    that would run backwards (the reheat before its cook) — checked in
    memory before the transaction writes. A night off the period, the same
    night twice, or a malformed date is a ValueError → 400.
  - **Undo is one token** (`derived_from.moved_from = {date, at}` on each
    moved row, the shape swap_in_place's `swapped_from` takes).
    `undo_dinner_nights_swap` requires both nights to still point at each
    other, then clears it — so Undo is the LAST move, which is what an
    eight-second toast can honestly offer. Routes:
    `POST /api/week/{week}/swap-nights` and `/swap-nights-undo`. Chat:
    `swap_dinner_nights` is a tool beside `swap_meal_in_plan`, tagged
    `week` in `_WEEK_TOOLS` so the panel refreshes.
  - **Front end is optimistic (§6):** the tiles trade on the drop, then
    POST; a refusal or failure puts them back and says the server's
    sentence in a calm toast (a toast rather than the in-card trouble line
    the stepper uses, per the design brief); success offers Undo. Drag is
    pointer events: a mouse lifts on press, a finger after a 250ms hold
    (a quick swipe on the handle never lifts); the lifted tile carries
    `--shadow-hero` and follows the finger, the night under it slides into
    the lifted night's home — a swap previewed as a swap, nothing in
    between moves — on the tab crossfade's own `--motion-fast`/`--motion-ease`.
    Keyboard: the handle is a button; ArrowUp/Down trades with the
    neighbour, focus follows the dish (and returns on a refusal), and the
    result is announced through `#rv-tiles-live` — which lives in
    shell.html, NOT in the re-rendered panel: a region rebuilt with the
    tiles was replaced before it was read (found in the browser walk).
  - **Judgment calls for Emily:** (1) "Hosting · 5" is the headcount at
    the table (`serves`), guests included — her own phrasing. (2) The head
    row's eyebrow and "Bar = how long" use `--ink-strong`, not
    `--ink-muted`/`--ink-secondary` as drawn: on the ground those measure
    4.33 and 4.44, short of AA (rule 8). (3) A plain tile is 60px, not 56:
    the body is a 44px tap target (rule 6) inside 7px padding and a 1.5px
    edge. (4) "All seven fit above the dock at 375×812 with no tags" holds
    only once the root band scrolls away (tiles + head ≈ 460px against
    643px above the dock); with the band, control and dock on screen
    nothing seven-tall could. (5) Away nights and cooked nights render no
    handle and refuse to be a drop target; an unplanned night can take a
    dinner (one row moves, the other night stays empty).
  - The browser walk ran headless Chrome through Playwright in a scratch
    venv — neither the Browser pane nor the Chrome extension was reachable
    from this session.
- **2026-09-13 — Cook's root is the shelf: one strip of nights, one spruce
  card for tonight, "Start cooking" in the dock. Branch
  `worktree-cook-shelf`, NOT merged at the time of writing.** Emily picked
  "Cook · D · The shelf" from the Beyond-lists canvas. `renderKitchen`
  renders, in order: `cookShelfHtml` (one 74px tile per night of the
  planning period, tonight celadon, one word per dish via
  `dishShortWord`; a tile opens that night's meal screen with the
  "‹ Cook" crumb), the attention fold, `kitchenCookingTodayHtml` (now
  ONE spruce `.cook-tonight` card: eyebrow with the day / the meal of the
  day / the cook's name, the dish at 26px, START + ON THE TABLE off the
  move the server already computed, the thaw/prep fact for this meal),
  `cookGetReadyRowsHtml` (at most two quiet rows — the next thaw for a
  later night, the next prep session, the next loose prep task; the two
  soonest), and one "More ···" link to `#cook-more-sheet` with Recipes /
  Add from a link / Inventory in their old `.kit-row` shape. The dock
  (`#kit-dock`, `.cook-root-dock`) reads "Start cooking" →
  `cookEnterFocus(tonightIdx)`, "Mark eaten" on a reheat night, and is
  hidden when tonight has no cook or it's done. **Tier 2, decided by the
  pick:** DESIGN_SYSTEM §6's "Cook's root has no primary action" and rule
  2's "Cook's root is the live example" of a dockless screen were changed
  in the same commit (plus §2 rules 4/5 and the §5 rows for the empty
  moment and the dock; two new §5 rows for the shelf and the Tonight
  card). Retired: `cookRestOfWeekHtml`/`cookRestDayRowHtml`/
  `KITCHEN_REST_VISIBLE`, `cookPrepSessionsHtml`, `kitchenPrepTodoHtml`,
  `kitchenTilesHtml`, `kitchenState.restExpanded`, the `rest-more`
  handler, the `.cook-day-*` CSS. Kept: `kitchenTodayRows`/`Line`/
  `Subtitle` (the band's line is unchanged), `kitchenTodayRowHtml` (a
  multi-slot day's other meals still render as tick rows under the
  card), `kitchenLoosePrepTasks` (feeds the get-ready rows so no prep
  task is invisible). Backend: `/api/cooker-view` grew three additive
  keys — `period_start_date`, `day_count` (from `get_weekly_plan`'s
  period, so the shelf can show an unplanned night as one) and
  `cook_name` (the `cooking_role` rhythm fact when it is `one_person`;
  the first code that reads that answer, which §2b S4 had flagged as
  never acted on).
  - **Judgment calls, for Emily.** (1) The card's no-thaw line is
    "Nothing to thaw or prep ahead." rather than the artboard's
    "Everything's in. Nothing to thaw." — "everything's in" claims the
    groceries are home, which nothing in the app can verify (§8: never
    promise what isn't true). (2) A reheat night's tile IS tappable: it
    opens the reheat's own card (`cookReheatFocusHtml`, the screen Now's
    hero already opens), which carries "Mark eaten"/"Mark not eaten" —
    a tile that does nothing among tiles that do would read as broken.
    The 2026-09-04 rule ("a reheat is never a way into a recipe") still
    holds: there is no recipe on that screen. (3) The no-plan state
    shows no shelf (seven dashes say nothing); the shelf appears once a
    plan or a loose meal exists, including on a night with no cook. (4)
    Recipes / Add from a link / Inventory stayed reachable from Cook
    behind "More ···" rather than moving into Preferences: they are
    things the cook does, not settings. (5) On a day with more than one
    meal to make, the card is the meal `cookTonightIndex` already
    picked and the others keep their old tick rows under it — the
    design shows the dinner-only case, and a 21-slot plan's breakfast
    must not vanish. (6) The get-ready rows cap at two by design; when a
    thaw, a session AND a loose task all exist, the loose task waits
    until one of the others is done. (7) "Start" and "on the table" are
    the move's own clocks ("6:00", no am/pm — the same words Now uses);
    with no move for the meal the card shows "TAKES · 30 min" instead
    of inventing a start time. Tests: `tests/test_cook_shelf.py` (22)
    plus the eight Cook-root files updated to the new shape.
- **2026-09-13 — Skip, swap, or "not this week": a ··· on every chore row.
  Branch `overnight/chores-skip-hand-move`, NOT merged at the time of
  writing.** Loop Board "Chores v1: Skip, swap, or 'not this week'"
  (Phase 2). Real life bends the plan for a day without anybody re-doing
  the setup: **Skip this time**, **Hand to [the other person]**, **Move
  to another day**, behind a `···` on a chore row and never a second dock
  button (§6), inline under the row rather than in a sheet, following
  Grocery's per-row `···` (`groRowMenuHtml`/`.gro-rowmore`) rather than
  inventing a second kind. It lands on BOTH surfaces at once because it
  is in `choreRowHtml`, the one row builder Now's card and Plan | Chores
  already share.
  - **Which half is shared, and why it is this half — the decision the
    ticket asked for in writing.** Each verb is two jobs: work out WHICH
    occurrence, then change it. Only the first is hard, only the first is
    chat's problem (a household says a chore name, not a row id), and it
    was already solved once (`_due_or_next_pending_id`). So the WRITE is
    what got lifted out, id-keyed — `skip_chore_instance`,
    `move_chore_instance`, `hand_chore_instance` — and the by-name tools
    resolve and then call in. `move_chore` and `skip_chore` are now
    resolution plus one call; their behaviour is unchanged and two tests
    that are GREEN on main say so. The alternative, an optional
    `instance_id` on the chat tools, would have put two resolution paths
    inside one function and handed the model a parameter it can never
    fill.
  - **A skip from the UI sweeps the backlog, and that is not optional.**
    The row on screen IS `_collapse_outstanding`'s representative, so
    skipping only it would put the chore straight back as due on the next
    read — the guilt pile in a different hat. Skipping still does NOT move
    the rhythm: nobody did the work, so there is no day for the next
    occurrence to count from, and the next one stays exactly where it was.
    Nothing is credited to anybody (`completed_by_member_id` untouched),
    so the later fairness view has honest data.
    **The bound is TODAY, not `_mark_done`'s "later of the row's day and
    today" — an earlier draft of this branch copied that and it was wrong,
    caught on review.** A tick means the work happened, so everything owed
    up to it is settled; a skip settles nothing. With the copied bound,
    skipping the occurrence three weeks out skipped the two before it as
    well — three weeks of bins from one call, reproduced. Now: a target
    that is itself due settles every other pending occurrence due on or
    before today (which keeps a stale id off the card twice, the care
    `_mark_done` takes), and a target ahead of today sweeps nothing at
    all. `skip_chore`'s own behaviour is unchanged **in every state the
    app can reach through ordinary use**, because `_due_or_next_pending_id`
    resolves to the latest row due on or before today whenever anything
    has slipped, so a future row never arrives with a pile behind it.
    **That is not an absolute, and the exception is reachable** (found on
    re-review): `schedule_chore_instance` does not dedupe, so saying
    "put the bins on the 19th" twice in chat leaves TWO pending rows on
    one future day, and `skip_chore` then resolves to one of them and
    (now) sweeps nothing — where before it swept the other. So "skip the
    bins" can leave a bins still due that day. Degenerate, and the new
    behaviour is the more defensible of the two — nobody did any work, so
    nothing is settled — but do not read the parity claim as covering
    every state. Worth knowing while you are here: `move_chore_instance`
    refuses to make such a duplicate and `schedule_chore_instance` never
    has, which is pre-existing and its own card if it ever bites.
  - **"Hand to" is one occurrence and never the chore.** New
    `chore_instances.assignee_id` write only; nothing touches
    `chores.default_assignee_id`, `rotation_member_ids_json` or
    `_reassign_pending`, so next week is still whoever's it always was.
    Changing the OWNER later still reassigns pending instances over it,
    which is right — that is the household saying the standing answer
    changed. On a shared chore the rotation reads the instances as they
    stand, so a handed turn simply continues after whoever ends up with
    it, the same stance `_reassign_pending` already takes towards whoever
    actually DID the last one. **Naming somebody never creates them** —
    `_member_named`, the same exact-then-unique-first-name-else-a-question
    resolution `add_chore`/`update_chore` use, which is what the "Vinneth"
    failure on the owner card bought.
  - **Three routes, not verbs folded into `/status`.** `POST
    /api/chores/{id}/skip` · `/hand` · `/move`, each 403 while the
    household's switch is off exactly as `/status` does. A skip is not a
    status the tick can be flipped to (it sweeps, and it leaves the
    rhythm alone) and the other two write different columns; `/status`
    stays the tick's route, untouched.
  - **`people` rides on the two chore reads** (`chore_people()` =
    `_people_pool`, the module's existing answer to "who does chores
    here"), and rows gained `assignee_id`. The `···` must not cost a
    request to open, and it cannot leave whoever already has it off its
    own menu from a first name alone. Only on the ENABLED payload — the
    switched-off answer still says nothing about the household, and a
    test pins that.
  - **Optimistic on both surfaces, one implementation.** `runChoreAction`
    applies the change, redraws, posts, and on failure puts the row back
    in its own place (not appended) with "That didn't save. Try it again
    in a moment." On success BOTH surfaces re-read, because grouping and
    order are the server's answer. Now's card drops a row moved off today;
    Plan keeps it and re-heads it via `choreGroupFor`, a deliberate client
    twin of `_chore_group` used only for the beat between the tap and the
    read. **A skip re-dates rather than removes on Plan** (`choreAfterSkip`,
    by the chore's own rhythm, a placeholder the read overwrites): Plan is
    one row per chore by construction — `_top_up_unscheduled` writes the
    next occurrence on the very next read precisely so a chore never falls
    off that list — so dropping the row would break that invariant for a
    beat and then have the chore reappear under a different heading.
    **It marks the row skipped; it does NOT guess a date, and an earlier
    version of this branch did** (found on re-review). That version
    re-dated to today plus the chore's own rhythm, a client twin of
    `_FREQUENCY_DAYS` — but after a skip the server's answer is usually an
    occurrence ALREADY on the calendar, whose date has nothing to do with
    today: measured 25 days wrong for a monthly chore that also had a row
    five days out. Worse, the guess could stand as FACT, because
    `planChoresStepHtml` shows its trouble line only when there is no list
    at all and after this runs there is one — so a failed refresh left an
    invented date on screen with nothing saying so, until some later read
    happened to succeed. "Skipped" is a thing this screen knows; the date
    is the server's to say, and if the read never lands the row goes on
    reading Skipped, which is what actually happened. The rhythm table is
    gone with it. A `once` chore needs no special case now — the server
    simply has no next occurrence for it, so the read drops it.
  - **A refresh behind an in-flight read starts its own** (re-review).
    `loadPlanChores` coalesces on `planChoresFetching`, so a
    `ctx.refresh()` fired by the ··· while a read that PREDATES the tap
    was still out got handed that read and started no new one — Plan
    settling back onto the pre-action list. It queues one instead.
    `loadChores` has no such guard and needs none for this; its own
    unlikely hazard is two overlapping reads landing out of order, which
    is a different thing and is not fixed here.
  - **Every action says what it did, with an Undo where the undo is
    exact.** On Now a skip or a move-off-today makes the row vanish, and a
    row that disappears in silence is indistinguishable from a mis-tap.
    Undo re-dates a move back to the day it came from, hands a chore back
    to whoever had it (offered only when there WAS somebody — a `whoever`
    chore has no name to send), and for a skip restores the ROW rather
    than the pile it swept: the same choice `_mark_done`'s docstring
    already makes about un-ticking, for the same reason.
  - **A skipped occurrence is not a done one, in all three refusals.**
    `hand_chore_instance` told you a skipped row was "already done" —
    nobody did it — which `move_chore_instance` eighty lines above already
    got right, with a comment saying why. Reachable in exactly the case
    `ChoreRefused` exists for, and now that these sentences are shown word
    for word a wrong one costs more than it did when everything read
    "That didn't save." `skip_chore_instance` was checked and is
    deliberately left with ONE sentence for both states: "isn't waiting to
    be done" is exactly true of a done occurrence and of a skipped one,
    and naming which would tell the household something they didn't ask.
  - **A refusal prints the server's own sentence** (`tools.ChoreRefused`,
    `weekly_plan.SlotRefused`'s shape and its reason — the 2026-09-11
    entry, "an app that did exactly the right thing must not report itself
    broken"): 200 `{status: 'refused', message}`, the row goes back and the
    screen re-reads. Reachable, and this is the case it is for: the `···`
    is open on one phone while the other adult ticks the row. Everything
    else — including `require_household_row`'s deliberately opaque "No
    chore instance with id 7." — stays a 404 and takes the plain line.
  - **"Move to" offers a week around THE ROW'S OWN DAY, not around today
    — corrected on review, and it was the branch's blocker.** The first
    version built `today … today+6`, and the `···` is on every pending
    row including Plan's "Coming up" group, which is exactly where Emily
    put the monthly and quarterly chores. So a "Gutters · every few months
    · Dec 1" row got seven chips reading Today…Sat, every one of which
    dragged it eleven weeks forward — and once moved, the same control
    offered only that week again, so **there was no way back to December
    from any screen**. The window now starts three days before the row's
    own day (never earlier than today — a chore cannot be due in the
    past), so both directions are reachable and the original day is one
    tap back; the Undo chip above is the second way. A chip beyond the
    coming week carries its date ("Sat Nov 28") rather than a bare
    weekday, because "Fri" on a December row is a question. Anything
    outside that window is the ask sheet's job — `move_chore` takes any
    date. **My own Chromium pass missed this**: I opened a `week`-group
    row and never a `later` one.
  - **Two small residuals, looked at and left, so nobody re-finds them as
    new.** The move window is clamped at today, so a row due TODAY moved
    to +4/+5/+6 loses `Today` from its own chips — six cases, at most six
    days out of reach, and the Undo chip covers it while the toast is up.
    And `_a_date`'s "I couldn't read … as a date" is a plain `ValueError`,
    so it takes the 404 door and the screen's plain line rather than being
    printed: the ··· only ever sends dates it generated itself, so the
    only way to see that sentence is a hand-made request.
  - **`also_cleared` is deliberately not in the toast.** A skip that
    settles three slipped weeks still says "Skipped Bins this time." and
    nothing more. Not an oversight: the no-guilt-pile rule is that a
    slipped chore is ONE job and the household never learns there were
    three — the tool description for `skip_chore` says the number is there
    so the assistant doesn't double-report, "never to be read back as a
    count of what was missed". A toast saying "and 3 more" would print
    exactly the pile the card exists to hide, and there is no shorter
    non-numeric wording that adds anything true.
  - **Two judgement calls, flagged rather than decided:** (1) an
    OUTSOURCED row gets no `···` at all, per this card's own acceptance
    criteria — but `skip_chore_instance` still permits it and chat still
    offers it, because "the cleaner isn't coming this week" is a real
    Thursday. If Emily wants the menu on those rows it is one condition.
    (2) A DONE row gets no `···` either: all three verbs act on an
    occurrence still waiting, and the server refuses each of them on a
    done row, so a control that can only fail is worse than none.
  - **Pre-existing and untouched:** a tick still credits the SESSION's
    adult, not the assignee, so Emily ticking a chore she handed to
    Vineeth is recorded as Emily's. That is arguably right (she ticked
    it) and is `_doer_id`'s existing rule; chat's `done_by` is the way to
    say otherwise. Worth Emily's eyes before the fairness view lands.
  - **Said precisely, because an earlier draft of this entry over-claimed
    twice.** There is not "one write per verb": `skip_chore`'s named-date
    branch deliberately still goes through `set_chore_instance_status`,
    because a date the household picked out must settle that occurrence
    and nothing else. And a handed turn does not simply "continue after
    whoever ends up with it" — `_next_in_turn` reads the LATEST instance
    by due date, so handing over the latest-dated occurrence moves who
    comes next and handing over an earlier one does not. Neither is
    wrong; no rule forces either.
  - **The owner-safety test could not see the leak it is named after
    (review).** `test_hand_changes_this_occurrence_only_...` asserted the
    reported `owner`, which `_rotation_ids` derives from
    `rotation_member_ids_json` first and only falls back to
    `default_assignee_id` for — and `add_chore(owner_name=...)` never
    leaves that JSON empty. A mutation writing `default_assignee_id` from
    `hand_chore_instance` passed the whole suite. It reads the raw columns
    now; the mutation fails it.
  - `tests/test_chore_row_actions.py`, 60 tests, **39 of the original 41
    red on `0d359e5`**;
    the 2 green say so in their docstrings (the by-name tools surviving
    the write being lifted out; the switched-off read not growing a
    field). `test_chores_switch.py`'s gate-completeness list and its
    declines-while-off parametrize grew to twelve tools. The 14 added on
    the review pass each pin one of its findings, and three mutations were
    run to check they bite: the old sweep bound, the today-anchored move
    window, and the owner write. Three existing
    node harnesses were widened for the new region and one assertion in
    `test_plan_chores.py` moved from counting a name in the whole string
    to counting it among the rendered NAMES — the `···`'s aria-label
    carries the chore's name and is not a second row. Suite 3224 (was
    3182). Verified live in Chromium at 390×844, light and dark, on a
    throwaway DB: all three verbs on both surfaces, the cross-surface
    refresh, the optimistic state observable mid-flight under a delayed
    failure, the revert and its toast with the server confirmed
    unchanged, `···`/verb/chip all 44px, no apricot anywhere in the menu,
    no sideways scroll. Contrast measured off computed styles and
    recorded in `shell.css`. Re-driven after the review pass with a
    quarterly chore months out actually opened — the case the first pass
    never reached.
- **2026-09-13 — A grocery line could read "0" with two dinners still
  planned, and which night you dropped decided the number. Branch
  `overnight/grocery-line-to-zero`, NOT merged at the time of writing.**
  Reproduced first, twice. (A) Three nights of a recipe written for 12 in a
  household of 3: the line rounds once to "1 Lemon" and the ledger
  apportions it 1 / 0 / 0, so dropping the FIRST night summed the two
  survivors to nothing — `get_grocery_list_by_section` served `"quantity":
  "0"` to the Shop tab. (B) Two nights of a recipe for 4 wanting 2 cans:
  line "3 cans", shares 2 / 1, so dropping the second night left 2 cans
  (right) and dropping the first left 1 (wrong — the surviving dinner needs
  1.5). Both over HTTP through `POST
  /api/week/{week}/drop-dish-day` on a throwaway DB, and both reachable
  through `swap_meal_in_plan`, which is the write behind every swap in the
  app.
  - **Root cause is `_apportion` outliving the reversal it was written
    for.** It was added on 2026-09-05 to serve a reversal that SUBTRACTED a
    contribution out of the displayed line, where a fractional ledger row
    really would have left a phantom quarter-pepper behind. Later THE SAME
    DAY, reversal stopped subtracting and started recomputing the line from
    what the other ledger rows add up to. Summing rounded shares is lossy —
    the two fixes are individually right and compose into this. Nobody
    checked what a `0` share's non-zero sibling leaving does, and
    `_apportion`'s own docstring anticipates a legitimate `"0"`.
  - **The fix records what the meal actually asked for.**
    `recipes._ledger_share` writes each meal's UNROUNDED contribution
    (0.25 lemon, 1.5 cans, 0.6 lb) in the unit the line ended up in;
    `_apportion` is deleted, and `_week_bought_amount` no longer returns a
    quantum for it. Reversal is unchanged — it already sums the survivors
    through `quantities._sum_ledger_quantities`, which rounds exactly the
    way `_week_bought_amount` does, so the survivors are rounded ONCE, the
    answer no longer depends on which night went, and a line that loses no
    meal at all recomputes to exactly what was put on it. **No schema
    change and no second copy of the arithmetic**: re-deriving from the
    surviving ENTRIES was the other option and was rejected — a line can
    hold five different recipes (Emily's peppers), so it would have meant
    re-walking each entry's recipe, attendance and chain inside a function
    that runs inside `swap_meal_in_plan`'s one transaction, i.e. a second
    implementation of the ingest that can disagree with the first.
  - **The phantom `_apportion` guarded against cannot come back**, and this
    is worth being precise about because the brief for this work assumed it
    could: nothing subtracts any more. The last meal off a line takes the
    row with it (`fully_removed = not other_qtys`), and every meal before
    that re-derives the line from scratch. `clear_weekly_plan` still leaves
    an empty list, pinned by the test that has always said so.
  - **A line with meals still behind it never reads "0".**
    `_sum_ledger_quantities` floors at the smallest buyable amount (1 of a
    count, a quarter of a measurable unit) when nothing is left to print; a
    bucket contributing nothing beside one that does is simply dropped,
    rather than printing a phantom whole pepper next to "2 cups". The floor
    is not belt-and-braces: **ledger rows written before this change still
    carry apportioned shares**, nothing can recover what those meals
    wanted, and Emily's live database has them. It is what keeps those old
    rows off "Lemon · 0". Case B's order-dependence is NOT fixed for them
    and cannot be, and a line MIXING old and new rows under-contributes
    while any old row survives (measured: a mixed line recomputed 2 → 1) —
    the ROWS heal as each week is re-approved, the line only once its last
    old row is gone.
  - **THE ONE PATH THAT STILL SUBTRACTS IS THE HOUSEHOLD'S OWN STANDING
    WANT, AND THE FIRST VERSION OF THIS BRANCH BROKE IT.** Independent
    review caught it; it is the reason to read this bullet before touching
    any of it again. A line with `source_weekly_plan_id IS NULL` can never
    be recomputed — the person's own amount is in it and no ledger row
    describes it — so reversal subtracts. The ingest adds ONE *rounded*
    week total; handing that path *unrounded* shares means it subtracts
    less than was added and the ceil rounds the remainder straight back up.
    Measured through the public API: a hand-added "3 Onions" under three
    dinners of a recipe wanting 1.5 apiece went **3 → 5 → 7 → 9 → 11** over
    four approve-and-clear cycles, +2 a week, for ever. `_apportion` had
    made that path exactly symmetric by construction, and deleting it took
    the symmetry with it. **The scope was wide, not a corner**: every
    discrete countable whose per-meal share is fractional, i.e. whenever
    `eaters ÷ default_servings ≠ 1`, and `add_grocery_item` keeps
    `source_weekly_plan_id` NULL on merge by design (`keep_standing`), so
    every hand add, chat add, offline add, photo-scan add **and every
    staples line** is a standing want, and `clear_stale_grocery_items`
    exempts them so nothing ever corrects it.
    Fixed by `quantities._ledger_totals` + `grocery._restate_standing_want`:
    the line is RESTATED in one step — what is on it now, less what the
    plan's meals round to, plus what the ones still planned round to —
    rather than subtracted from repeatedly. **Three things make that work,
    and each was found by measuring drift that survived the version before
    it.** Read all three before touching it; two of them look like
    over-engineering and are not.
    (a) **One step**, because re-humanizing the whole line at every removal
    snaps it to a quantum each time and the residue accumulates.
    (b) **The plan's share is rounded the way the INGEST rounded it** —
    `_shopping_round` on the ledger's own unit, rolled up exactly as
    `_week_bought_amount` does, and only then converted into the line's
    unit. Rounding it in the LINE's unit instead is tidier-looking and
    wrong whenever the line's display unit rolls mid-sequence: the first
    removal rounds at a quarter-POUND and over-credits the plan, the line
    then rolls to ounces, and the later removals — now on a quarter-ounce
    quantum — never give the over-credit back. **This shipped in the first
    correction of this branch and review caught it**: a hand-added "2 oz"
    came back BLANK, and a "500 ml" staple went 500 → 416.75 → 166.75 and
    stayed there. Four traces, all now matching or beating `main`.
    (c) **The household's own amount is snapped back onto the line's
    quantum** (`_snap_to_unit_quantum`), because it is re-derived out of a
    line that has been re-rounded for display in between, so it drifts a
    little further every removal. **Half rounds UP there, and that is the
    fix, not a detail**: the display rounding that puts the error there
    rounds half DOWN, so exactly-half keeps coming up — 8 oz on a line
    shown in pounds derives as 0.375 lb, dead between two quarters, and
    rounding it down hands the household 4 oz. The snap stands down
    entirely when it would annihilate a genuinely positive amount (2 oz is
    under half a quarter-pound), and the derived own floors at zero so a
    hand-edited line can never come back negative.
  - **WHAT THIS PATH DOES NOT PROMISE, because an exact restate is not
    reachable and pretending otherwise is how it went wrong twice.** The
    line is a rounded string and what the ADD rounded away is not
    recoverable from it: 15 oz of plan on top of a household's own 8 oz is
    stored as `"1.5 lbs"`, and the 1.75 oz difference is simply gone before
    any reversal runs. `main` loses it too. **"Never ends below the
    household's own amount" is NOT the rule, and an earlier draft of this
    bullet claimed something close to it** — `main` itself ends below by
    that measure 13-18 times in 900 randomised runs, so it is not a bar
    anything here clears. The rules actually held to, and the ones to hold
    a future change to:
      - **a line the household typed is never blanked**;
      - **nothing compounds** — every below-own case is one-shot and flat
        from the first cycle, checked over eight approve-and-clear cycles
        on both sides;
      - **strictly fewer below-own outcomes than `main`, at comparable
        worst case.** Over 900 runs weighted onto roll-up boundaries: 0
        blanks either side, below-own **2 here against 13 on `main`** in
        one set of draws and **7 against 18** in an independent reviewer's;
        of the end-states that differ from `main`, roughly two thirds land
        closer to the household's own amount. Worst single case is
        comparable in both directions (`main` `500 g → 400 g`, −20%; here
        `6 oz → 4 oz`, −33%).
    **Where the remaining tail comes from, so nobody hunts it twice:** the
    snap in (c). Putting a display-drifted number back on a quantum can be
    up to half a quantum from the truth, and at a coarse quantum that is a
    real amount — an own of 15 oz derives as 0.828125 lb and snaps to 0.75
    lb, i.e. 12 oz. It is inherent to re-deriving a number out of a rounded
    string, it does not accumulate, and the aggregate is better than
    `main`'s. **The bias is upward** where the arithmetic has any freedom,
    which is this module's whole stance; the tail above is where it has
    none.
  - **The ledger is written at twelve significant figures, not six**
    (`recipes._LEDGER_SIG`). These rows are machinery, never read, and they
    have to ADD BACK UP: two thirds written three times at six figures is
    2.000001, which the ceil turns into three, so reversal believed the
    plan had put three onions on a line it had only put two on and took the
    household's own one away with them. `quantities._plain_number` also
    stops `%g` ever writing an exponent — "1e-05 cups" parses as nothing,
    which made `_sum_ledger_quantities` give up on the WHOLE line and drop
    into `_subtract_quantity`, where an identical current-and-remove string
    means "this is the whole line" and DELETES a row other meals still link
    to.
  - **`_week_bought_amount` and `_humanize_grocery_quantity` are now one
    function** (`quantities._shopping_round`) rather than two that happen
    to agree. The whole fix rests on that equivalence, and it was
    unpinned — turning the "you cannot buy 12.75 peppers, round UP" ceil
    into a `round` passed the entire suite. One function cannot drift, and
    there is now a test on the direction as well.
  - **`clear_weekly_plan` calls the same reversal and does NOT exhibit the
    bug on its own** — checked, not assumed: it removes an entire rounding
    group at once, so the last removal deletes the row whatever order it
    goes in. It passes through wrong intermediate states (it commits per
    meal), which nothing reads. The caller that DOES remove part of a
    rounding group is `_release_plan_days` — the atomic period takeover —
    and it had the bug with no single-meal control involved at all; it has
    its own test.
  - **Untouched, deliberately, each with a test saying so:** a sealed
    package (`quantity_mode="max"`) is never apportioned and still survives
    until its last link; a standing want is still only blanked, never
    deleted, and still takes the subtract path (what it subtracts is what
    changed — see above); bought and in-cart lines are still left alone;
    and household isolation. Approval-time lines are byte-identical to
    `main` across 120 randomised weeks, and standing wants behave
    identically to `main` across 200 randomised approve-and-clear runs
    (and better than it across the 900 boundary-weighted ones above).
  - `tests/test_grocery_line_to_zero.py`, 35 tests, **19 red on `0d359e5`**
    (checked both by stashing and by `git checkout 0d359e5 -- app/`) —
    though **4** of those 19 are red only because they name functions that
    do not exist on `main` (`..._rounds_UP_and_a_measurable_one...`,
    `..._vanishingly_small_share_cannot_delete_a_line...`,
    `..._snap_breaks_a_tie_upward_not_downward`,
    `..._snap_never_annihilates_a_small_amount_on_a_coarse_line`), so they
    are guards rather than catches, say so in their own docstrings, and are
    pinned by mutation instead. Fuzzed as well as tested: 868 drop-one
    reversals across 300 randomised weeks give **0** zero-valued lines and
    **0** order-dependent weeks here, against 47 and 174 on `main`. Seven
    mutations checked to bite: ceil→round (7 red), `_LEDGER_SIG` 12→6 (1),
    the standing-want rounding put back in the line's unit (6), the snap's
    tie-break turned back down (1), the snap's annihilation fallback
    removed (3), the `max(0, …)` floor removed (1), and
    `_round_in_unit`→`_shopping_round` inside the line recompute (1).
    Seven test functions in three existing files
    were updated honestly rather than deleted, each with a note saying what
    moved — they pinned the apportioned denomination. Two of them
    (`test_grocery_reversal_ledger`'s reversal-order pair) compared the
    line against the one ledger row the recompute had just written, which
    could never disagree; they compare against a fixed string in both
    orders now, which is the claim that was being made and was not being
    tested — on the apportioned ledger those two orders left "8 oz" and
    "12 oz" for the same surviving dinner.
    `test_produce_quantities`'s swap test keeps its number (11 either way
    for that particular week) and says in its docstring that it is a guard
    rather than a catch. Suite 3182 → 3217, the same two
    `test_onboarding_reveal_stream` Sunday failures before and after.
- **2026-09-13 — A meal's ingredients come out of the kitchen once, however
  many times the box is tapped. Branch `overnight/cook-tick-double-depletes`,
  NOT merged at the time of writing.** `check_off_meal` ran
  `deplete_inventory_for_meal` on EVERY call with `status='done'`, with
  nothing recording that it had already run for that entry — the previous
  `cooked_status` wasn't even selected. Reproduced in-process and over a
  real `POST /api/cooker/check-meal`: 20 tortillas -> 12 -> 4 -> the
  inventory row DELETED outright.
  - **The unchanged-status guard `mark_grocery_item` has is necessary and
    NOT sufficient, and that is the whole shape of this fix.** The Kitchen
    cook checkbox is a TOGGLE — shell.js renders `data-next="pending"` once
    a meal is done and posts whatever `data-next` says — so the ordinary
    way to send `done` twice is **tick -> untick -> tick**, three taps on
    one control, in which the status genuinely changed in between and a
    status comparison sees nothing wrong. (Independently re-checked: a
    status guard alone, applied to main, still gives 20 -> 12 -> 12 -> 4.)
    It fixes only the other route in: Today and Kitchen are separate
    build-once panels that can disagree about whether tonight is cooked,
    and Today's tick dispatches to this same function through
    `moves.set_move_done`, so two unticked boxes can each post `done` (a
    retried POST is the same shape). Both are in the fix; only the second
    is in the precedent.
  - So the memory is its own column, `meal_plan_entries.inventory_depleted_at`
    (schema.sql + `_MIGRATIONS`). Not `cooked_at`, which an untick sets back
    to NULL, i.e. the one field that forgets exactly when it matters.
  - **"At most once" has to hold against two THREADS and across a whole
    component BATCH, and the first cut of this held against neither.** Both
    found on review, both reproduced, both now fixed and pinned:
    `/api/cooker/check-meal` is a sync `def` route, so Starlette runs it in
    a threadpool and two panels really do post at once — reading the column
    on one connection and stamping it on another let both threads see NULL,
    measured at **17/20** concurrent pairs double-depleting; and stamping
    only the entry that was tapped let `tick(A) -> untick(B) -> tick(B)`
    take a second batch off a component card every time. The depletion is
    now **claimed before it runs**, in one `BEGIN IMMEDIATE` transaction
    over every linked entry (`_claim_inventory_depletion`) — the
    lock-from-the-first-read shape `weekly_plan._replace_slot_entries`
    already uses, and for its reason. Whoever loses the claim depletes
    nothing. 0/20 after.
  - **The claim is RELEASED when the pass turns out to have moved nothing**,
    which is what keeps the column honest. `depleted` is not the same
    question as "inventory changed": an ingredient is reported depleted
    whenever the recipe named an amount and the match was confident,
    *including* `units_reconciled: False`, where `_use_inventory_row_by_id`
    deliberately writes nothing because the tracked quantity ("a big
    carton") could not be subtracted from. The first cut stamped those, so
    it recorded a depletion that never happened and then blocked the real
    one for good once the household tidied the quantity up — reproduced.
    A leftovers night, a freeform meal and a recipe nothing is tracked for
    release for the same reason, which is also what lets a night that stops
    being a reheat deplete the first time it really is cooked.
  - **Nothing is backfilled**, so a meal already ticked `done` before this
    deploy has a NULL stamp and an untick -> re-tick on one of those will
    take its ingredients once more. Deliberate: a startup backfill that
    stamped every done row would also re-stamp the reheats and unparseable
    quantities the release rule exists to clear, so it would trade a
    bounded one-off for a permanent wrong.
  - **UN-TICKING PUTS NOTHING BACK, deliberately — Emily's to overrule.**
    There is no ledger of what a depletion took (a grocery line has
    `grocery_item_links`; this has nothing), so once the call returns the
    amounts are gone and every reversal is a guess wrong in a different
    direction: `deplete_inventory_for_meal` DELETES a row whose quantity
    reaches zero or can't be reconciled, so a re-created row has lost its
    location, category and expiry; a depletion that reported success but
    wrote nothing back (`units_reconciled` False, the tracked quantity too
    imprecise to subtract from) took nothing, so "restoring" it INVENTS
    inventory; and the household may have edited those rows in between. A
    partial restore is worse than none, because it silently inflates a
    pantry that then gets shopped against. So an untick means only "this
    is not cooked yet", the result says `inventory_restored: False` rather
    than staying quiet, and putting something back is the inventory
    screen's job. What the column buys is that a mis-tap costs one meal's
    worth however many times the box is tapped, instead of compounding.
    **The door is open, though, and the entry should not be read as saying
    otherwise:** a restore is a guess given the data as it stands, not in
    principle — this column could have carried the per-item delta and made
    one constructible. It doesn't, because a reversal still could not
    rebuild a DELETED row's location and expiry, and half-exact is the
    worst of the three. Emily's to reopen.
  - **The guard is "every LINKED entry already reads this way", not "this
    row does".** A component-based card is done only when all its siblings
    are (`get_cooker_view`'s merge), so a wholesale early return would
    leave a sibling planned after the batch was cooked pending forever.
    Returning early also leaves `cooked_at` where it was, so a second tap
    doesn't move the time the meal was actually cooked.
  - `tests/test_cook_tick_double_depletes.py`, 27 tests, **21 red against
    `0d359e5`** (checked by overlaying `git checkout 0d359e5 -- app/`, not
    by stashing against a HEAD that already carries the fix). Of the other
    six, five are no-regression guards — including the one guarding against
    the unchanged-status widening being too broad — and the sixth
    (`..._tidying_that_quantity_up_...`) is green on main because main has
    no stamp to get wrong, but red against the first cut of this fix, which
    stamped a pass that had written nothing. Each says which it is in its
    own docstring. `tests/test_leftovers_batch.py:293` already covered
    the twice-ticked REHEAT; this covers the cook night, toggled. Three of
    the concurrency tests are timing-dependent by nature and say so; the
    8-trial one is the reliable guard (red 5/5 against the pre-claim
    version). Branch collects **3209**, 3207 passed, 2 failed — the two
    known Sunday failures in `test_onboarding_reveal_stream.py`, which fail
    on main too. (Main collects 3182.)
  - **Two things left alone and reported rather than fixed, both the same
    class one door over.** (1) `grocery.mark_grocery_item`: its guard is
    also status-only, so `purchased -> needed -> purchased` adds to
    inventory twice (Eggs 12 -> 12 -> 24, measured). (2) The
    attention-queue path: an ingredient the recipe gives no quantity for is
    queued rather than depleted, so nothing is stamped, and
    `add_attention_item` dedupes only against *pending* rows — so once the
    household has answered, a toggle re-asks and answering again takes it
    again (Lettuce 2 heads -> 1 head -> row gone, for one meal, via
    `record_attention_item_usage`, which is a live UI path). Not fixed here
    because the second depletion happens outside `check_off_meal`, in a
    path that neither reads nor writes this column; closing it means the
    attention resolution stamping the entry, which is its own card.
- **2026-09-13 — A dinner answered on Now, on a day no plan covers, was
  saved and then invisible on every screen. Branch
  `overnight/needs-you-dinner-invisible`, NOT merged at the time of
  writing.** The beta tester's first evening. A brand-new household has no
  weekly plan, so Now offers "Tonight needs a dinner"; answering it really
  did write the meal, and then nothing showed it — no move on Now, no
  start-by time, no tick, no cook mode, nothing in the morning text. The
  card vanished (so it read as accepted) and Now fell back to *"Quiet day.
  Want me to sort dinner, or the whole week?"*, offering to sort the dinner
  it had just been given.
  - **Root cause is one step behind the obvious one, and the 2026-09-11 fix
    it follows is CORRECT and unchanged.** `resolve_needs_you_dinner` writes
    `weekly_plan_id = None` when the current plan's period doesn't cover the
    date — that is the deliberate fix for a 500 on the same tap, and undoing
    it would bring the 500 back. What was wrong is that
    `cooker.get_cooker_view` is strictly plan-scoped, and Now's moves
    (`moves.py`), cook mode and the Kitchen list are ALL built on it — and
    the morning text is built on Now's moves, so it went dark with them.
    One plan-scoped read, three blind screens and the text behind them.
  - **Fixed in `get_cooker_view`, not in `moves.py`.** A second source in
    moves would have put the move on Now and left cook mode and Kitchen
    still empty, and made "the day's meals" a question with two answers that
    can drift. **It WOULD have fixed the morning text** —
    `digest.build_morning_text` reads `today_moves` and nothing else, and an
    earlier draft of this entry and of the code comment both named it as a
    third surface a moves fix would miss. Wrong, corrected in the same pass
    the reviewer caught it; the decision stands on the two surfaces that are
    real. Also decided against: widening `get_weekly_plan` — its name IS its
    scope, and **15 call sites across nine modules** read it as "that plan's
    rows". New `weekly_plan.unplanned_meals_ahead(plan)` returns loose
    entries (`weekly_plan_id IS NULL`) in exactly the shape `get_weekly_plan`
    puts its own `meals` in, and `get_cooker_view` concatenates the two
    through its one existing card-building pass.
  - **The no-duplicates rule is one line: a day the plan covers is left
    alone.** On a day a plan speaks for, the plan is the answer and nothing
    changes — a covered day cannot show an unlinked row beside a planned one,
    because loose rows on covered dates are never read. Only a day no plan
    covers falls back to its own rows, which is exactly the past-week,
    future-week and no-plan-at-all cases.
  - **Bounded today .. +7 days inclusive** (`UNPLANNED_HORIZON_DAYS`) and
    never backwards: this answers "what is there to cook from here on".
    **It is 7 and not 6, and the first cut got this wrong in a way worth
    recording:** it was 6, under a comment claiming it already matched
    `get_meal_plan`'s default — which it did not. That function computes
    `today + days_ahead` and filters `<=`, so its default window is eight
    days, not seven. Measured with loose dinners at +0/+6/+7/+8:
    `get_meal_plan()` gave [+0, +6, +7] and the Cook view [+0, +6]. The
    residue was a one-day sliver where a meal is something the assistant can
    name and no screen shows — a thin band of this very bug — so the number
    that closes it beats the tidier-sounding "a week is seven days". The two
    agree by maintenance, not construction, and
    `test_the_horizon_matches_what_the_assistant_can_talk_about` pins the
    property rather than the number. And **only** for the no-argument
    call — `get_cooker_view(plan_id)` is a question about ONE plan (the
    share view, `/api/cooker-view?weekly_plan_id=`) and gets exactly it.
  - **Component-based plans are carved out on MECHANICS, and the bug is
    fully intact for them — say so out loud.** The reason is NOT "their
    dates are placeholders" (an earlier draft said that; it is a fact about
    the plan's rows, not about the loose rows, which carry real dates). It
    is that `get_cooker_view`'s component branch groups by dish name and
    batch-collapses repeats into one card, so a dated one-off dropped into
    it would be folded into an undated component or scaled to a batch
    nobody planned — a rebuild of that branch, not a carve-out.
    **Reproduced 2026-09-13 with the identical symptom:** a component
    household whose current plan doesn't cover today saves the meal and
    shows nothing. A narrowing of an existing bug rather than a regression
    (component mode is not the default, and it needs an approved plan on
    file to reach at all), characterised by
    `test_a_component_household_still_has_this_bug` so the next session
    finds it written down instead of rediscovering it. **Invert that test
    when the component branch learns to carry a dated row.** Its own card.
  - **The two lists are MERGED in eating order, not appended.**
    `kitchenTodayRows` and `cookRestOfWeekHtml` (shell.js) walk this list
    unsorted — see the 2026-09-10 "a day printed dinner before lunch" entry
    — so appending would have printed tonight's answered dinner after next
    Friday. A no-op when there is nothing loose.
  - With no plan at all the payload keeps `weekly_plan_id: None`, so Today's
    week-state badge still reads "none" and every plan-scoped pass (leftover
    chains, cook-ahead, prep tasks, prep sessions) is skipped rather than
    handed a None id.
  - **Two things for Emily, both about where a loose meal now is and isn't.**
    (1) A one-off chat `plan_meal` on a day the plan DOES cover is still
    invisible here. The chat tool never passes a `weekly_plan_id`, so every
    chat-planned meal is a loose row, and on a covered day this fix leaves
    it exactly as it was — showing it beside the plan's own row for that
    slot is a product decision nobody has made, and "a covered day behaves
    exactly as before" was this ticket's own acceptance criterion.
    (2) **A loose meal is now visible on four surfaces and editable on
    none.** It reads on Now, in cook mode, on the Kitchen list and in the
    morning text — and the Meals tab still doesn't draw it
    (`get_week_menu` is plan-scoped), while every control that could change
    or remove it (`clear_plan_slot`, `drop_dish_from_day`, the swap paths,
    the Review stepper) takes a `weekly_plan_id`. So a mis-tapped dinner is
    now visible-and-unremovable except by asking in chat. Net better than
    saved-and-invisible, and within this ticket's intent — but it is a new
    state, and Emily should see it before the tester does. The honest fix
    is a way to take a loose meal off a day, which is its own card.
  - `tests/test_needs_you_dinner_visible.py` (20 tests, **13 red on
    `0d359e5`**). The seven green are no-regression guards and each says so
    in its own docstring: the empty state, the meal-really-saved check the
    old tests already made, the two covered-day no-duplicate promises, the
    named-plan scope, household isolation, and the component
    characterisation. **Two of those seven name a screen and are green on
    purpose** — an earlier draft of that file's docstring claimed "every
    test here that names a screen fails on `0d359e5`", which overstated it
    in exactly the way the ask-sheet entry warns about. The two 2026-09-11
    tests in `tests/test_tools.py` were **widened, not replaced** — they
    asserted only `get_meal_plan`, which is not a screen, and that is
    precisely how this survived; both are red on `0d359e5` now. Suite
    3182 -> 3202 (2 pre-existing Sunday failures in
    `test_onboarding_reveal_stream.py`, red on `0d359e5` too, untouched).
    Verified over HTTP against a real uvicorn on a throwaway DB. **Not
    verified in a browser** — the Playwright browsers are on this machine
    but the Python package is not installed, so the payloads and
    `todayIsEmpty`'s own predicate are what was checked, not the pixels.
- **2026-09-12 — Design hygiene: hand-written colours, two emoji, three
  legacy pages. Branch `worktree-design-hygiene`, NOT merged at the time of
  writing.** Emily: "go ahead and run the design hygiene." A rename-only
  pass (no pixel was meant to move) plus one real deletion.
  **Deleted**: `static/{grocery,cooker,kitchen,memory,index}.html` — every
  tab went native before this pass (What we know absorbed memory.html's
  content 2026-09-12; Grocery/Kitchen/Cook earlier), so nothing live
  reached any of them (confirmed by grepping `static/`, `app/`, `tests/`,
  `*.md` for each filename and route). `inventory.html` stays — deferred
  beta feature, still the one page `#kit-sheet-frame` embeds. Routes
  `/memory` (served memory.html directly) and `/cooker` (redirected into
  the shell purely as an old-bookmark shim) are gone — both now 404.
  `/`, `/week`, `/grocery`, `/kitchen` are NOT touched despite the name
  collision with the deleted files: those four are live SPA deep-link
  routes the shell itself pushState()s to (`TABS` in shell.js,
  `SHELL_ROUTES` in service-worker.js) and always served shell.html, never
  the legacy pages — deleting them would have broken reloading the
  Shop/Cook/Plan tabs. Deleted `tests/test_cooker_today.py` outright (it
  tested only `todaysMealIndex`, a function that lived solely in the
  deleted cooker.html and was never called by the live Cook tab); trimmed
  `tests/test_embedded_pages.py` (EMBEDDABLE is `["inventory"]` now, not
  five pages), `test_cook_voice_hidden.py`, `test_calendar_feed.py` and
  `test_contrast.py` (each had one assertion or list entry tied to a
  deleted page, the rest of each file covers live behaviour and stayed).
  **Tokenised**: every literal hex color rule 9 governs, across
  `shell.css`/`shell.js`/`shell.html`/`login.html`/`onboarding.html`/
  `plan-week.html`/`meal-setup.html`/`share.html`/`member-share.html`/
  `chores-setup.html`/`inventory.html`, mapped to the existing token with
  the identical value — a rename, not a recolor. `shell.js`'s
  `GRO_STORE_PALETTE` (six hardcoded store-avatar hexes, deliberately the
  same in light and dark) moved into `theme.css` as new `--store-1..6` and
  `--store-none` tokens (fixed values, not redefined in the dark block) and
  the array now holds `var(--store-N)` strings resolved inline via the
  markup string, not a runtime lookup. A handful of literals had no
  exact-value token twin (theme.css's own tokens either differ by a few hex
  digits or flip in dark mode when the literal must not) and were left
  literal with a comment at the call site and flagged for Emily rather than
  silently mapped to a close-but-different token: `shell.css`'s two
  `color: #fff` (ask-bubble user text on spruce; the ask-composer-mic's
  active-state icon, base/light rule only — dark already correctly
  overrides to `var(--urgent-ink)`), `shell.css`'s `.gro-box`'s
  `#D9C9AF` border, `login.html`'s `.signin-field` border `#2E5240` (equals
  `--celadon-edge`'s *dark* value used in a rule with no dark override —
  pointing it at the token would change light mode), and the repeated
  "spruce fill + white text" chip/button pattern's `color: #fff` in
  `plan-week.html` (×2), `meal-setup.html` (×2), `member-share.html`,
  `chores-setup.html` and `share.html`. `<meta name="theme-color">`'s two
  values in every page (exact `--spruce` light/dark) are commented as an
  accepted exception — a meta tag's content attribute cannot reference a
  CSS `var()`. **Aliases**: every remaining `var(--alias)` call site (28
  names, ~218 occurrences across `shell.css` and six HTML pages) moved to
  its canonical token, then the entire COMPATIBILITY ALIASES block was
  deleted from `theme.css` (zero call sites left) and DESIGN_SYSTEM.md §1
  updated to match, in this same set of commits. **`shell.css` TOC**: a
  table of contents added at the top of the file, five previously
  `----------`-only major sections (Today, the hero panel, the ask sheet,
  the weekly-menu block, What we know) promoted to the heavier `====`
  banner to match Grocery/Kitchen/Cook/Preferences/Meals' existing
  treatment. The TOC's section titles are deliberately paraphrased rather
  than quoted verbatim from each banner — several existing tests locate a
  section by searching `shell.css` for its exact banner text via
  `.index()`/`.find()`/`.rindex()`, and an identical copy of that text
  higher up the file would make the search land on the table of contents
  instead of the real section, silently emptying whatever slice the test
  meant to check (two tests broke exactly this way on the first draft,
  vacuously passing on an empty string, and a third — `.rindex()`
  searching backward for a `/* ====` banner — matched the TOC's own
  example text; all three were only caught by rerunning the full suite,
  not by the two tests this pass added). **New tests**:
  `tests/test_design_hygiene.py` guards all three: no unexplained literal
  hex in the files above (with an explicit, comment-carrying allowlist for
  the exceptions just listed), no emoji/pictographic code point anywhere in
  `static/` (a `✓` and a `★` already in the app are allowlisted by exact
  character — they're plain typographic marks reviewed as part of this
  pass, not emoji in rule 7's sense; arrows and an ellipsis elsewhere in
  the app are outside the scanned Unicode ranges entirely), and the five
  deleted pages/two routes never reappear. Confirmed via the named
  entities (`&#127823;`/`&#9998;`) that the two emoji the Loop Board card
  named were already gone before this pass (the desktop rail that carried
  them was removed 2026-09-12, per the card). Full suite: 3174 pass
  (was 3135 on `main`; net change is fewer tests tied to deleted pages plus
  25 new hygiene tests). Sandbox-verified signed in against the
  pre-onboarding-reset backup DB, light and dark: all four tab roots, the
  Cook › Inventory sheet, Preferences, the native What we know sheet, and
  sign-in — plus every touched CSS custom property's `getComputedStyle`
  value checked against its pre-change literal in both colour schemes.
  Pixel screenshots could not be captured this session (the Browser pane
  never displayed for this background agent — `computer` screenshot timed
  out every retry, `preview_start` was denied by the auto-mode classifier);
  the computed-style and route-status checks above are the substitute
  record. Not pushed; branch only.

- **2026-09-12 — Chores v1: add or change anything by saying so. Branch
  `chores-chat-tools`, NOT merged at the time of writing.** Re-verified the
  chat tools against the Chores screen for the whole user story (add,
  change frequency/owner/mode, mark outsourced, mark done, ask what's
  due — all already true on `main`) and closed the two real gaps: no chat
  tool skipped a single occurrence, and none re-dated one. Added
  `skip_chore(chore_name, when?)` and `move_chore(chore_name, to_date,
  from_date?)` to `app/tools/chores.py`, both additive (no existing
  function's body changed) and both resolving "which occurrence" via a
  new shared helper, `_due_or_next_pending_id` — the latest pending
  instance due on or before today, else the earliest one ahead, the same
  representative `_collapse_outstanding` already uses for the Now card.
  `skip_chore` is a thin door onto the existing `set_chore_instance_status`
  (so chat and the Today card's own skip agree), and — resolving to "the
  due one" — also sweeps any backlog behind it exactly like a tick does
  (`also_cleared`), so "skip the vacuuming" doesn't leave two more slipped
  weeks still reading as due; naming a specific date skips only that one,
  no sweep. `move_chore` re-dates the row in place (keeps its id and
  whoever already had it) rather than creating a new one the way
  `schedule_chore_instance` always does, refuses rather than doubling a
  chore up on a date it's already got, and only touches a `pending` row —
  a `done` one is history. Both joined `CHORES_TOOLS` (the switch gate),
  `TOOL_FUNCTIONS`, `TOOL_DEFINITIONS`, and `_CHORE_TOOLS` in
  `app/main.py` (tagged `today`, so the shell refreshes and the card reads
  "Skipped .../Moved ..." — two new `_VERB_PREFIXES` entries). System
  prompt gained one bullet distinguishing "not this week" (skip_chore, not
  a tick, not missed) from "push it to Saturday" (move_chore, same chore
  and person, just re-dated) — everything else in the existing chores
  guidance (no-guilt phrasing, owner/mode/outsourced wording) was already
  correct and needed no change. `tests/test_chores_chat_tools.py` adds one
  agent-dispatch-path test per chores tool (the original nine plus these
  two — nine already passed on `main` through that path, just untested
  that way before) plus unit tests for skip/move's rules including
  household isolation; `tests/test_chores_switch.py`'s gate-completeness
  test and its `_CHORES_TOOL_CALLS`/"declines while off" coverage grew
  from nine tools to eleven. Suite 3042 (23 new; **15 fail on `main`**: the
  12 skip/move behaviour tests, the renamed gate-completeness test, and
  the 2 new `test_each_chores_tool_declines_while_off` parametrize cases
  — all three follow from the same nine-to-eleven change to
  `test_chores_switch.py`, not just the new test file on its own).
  **Follow-up same day:** the refusal strings in both tools said "pending
  occurrence" — reads clinical next to the rest of this file's voice
  (e.g. `_refuse_if_outsourced`'s "there's nothing to tick off"). Reworded
  to match: "There's no Mop coming up to skip/move", "Mop is already done
  that day — nothing to move" (a done row on the named date now gets its
  own message instead of the same blank "nothing there" an empty date
  gets), "Mop is already on 2026-09-20" for the duplicate refusal. Same
  `ValueError`, same control flow — wording only; no test pinned the old
  text, so nothing else changed.

- **2026-09-12 — Plan gets a Meals | Chores toggle showing what's due.
  Branch `plan-chores-toggle`, NOT merged at the time of writing.** Loop
  Board "Chores v1: Plan gets a Meals | Chores toggle showing what's due"
  (Emily, 2026-09-11: a grouped LIST, not a week grid — "today, this
  week, and then the other ones would likely become monthly, bi-monthly,
  or semi-annually"). Chores is a **state of the Plan root**: a new
  value of `weekState.step` (`'chores'`), a sibling of `'week'` in
  `renderMealsStep`, so it keeps the band and the gear, has no head or
  crumb of its own, and inherits history and the back gesture from
  `goMealsStep` (Meals → Chores pushes one entry; Chores → Meals
  `replace`s, so flipping never stacks). The control is `.wk-seg` copied
  from the review step (`planModeSegHtml`, hook class `.plan-mode-seg`),
  in a new `#week-mode-slot` under the band, rendered only on the root
  and only with `choresEnabled()` — off, Plan is byte-identical. The
  band on Chores keeps the week's dates and "This week" and drops the
  meal plan's chip and line (`planChoresBandParts`). **One read per
  chore, not per instance:** new `GET /api/chores/pending` →
  `tools.get_chores_pending` (additive, beside `get_chores_due_today`;
  `_INSTANCE_SELECT` untouched) returns one row per chore — the row done
  TODAY if there is one (so a tick stays visible, ticked, all day and an
  untick has a row to land on), else the earliest pending, which for a
  slipped chore is `_collapse_outstanding`'s single due row — with
  `group` (`today` / `week` / `later`), `frequency`, and the week it
  grouped by (`week_start`/`week_end`/`week_label`, from
  `suggest_planning_period(plan_ahead=False)`; a non-seven-day answer
  falls back to a Monday week — `_chores_week`). `week` is the REST OF
  THE HOUSEHOLD'S WEEK, not a rolling seven days, so the band and the
  heading name one week; anything after it is `later` whatever its
  rhythm, and the shell heads `later` by frequency (`CHORE_RHYTHM_LABELS`:
  Every day / Every week / Every two weeks / Every month / Every few
  months / Just once). Order inside a group is ONE function,
  `_due_order_key` (to-do before done, longest waiting first). Inactive
  chores are left out (their surviving pending rows still show on Now —
  pre-existing, flagged, not fixed here). **A write on the read**,
  `_top_up_unscheduled`: an active recurring chore with NO pending row
  (added in chat with no generate after, reactivated, its one row
  'skipped') gets its next occurrence written by the same
  `_next_due_date` everything uses — one row, only where there was none,
  same precedent as `retire_expired_drafts` on `get_week_menu`; the
  alternative was a list that silently drops a chore. Rows are Now's
  rows: `choreRowHtml` is extracted from `renderChores` and is now the
  ONE builder behind both screens (a `.chore-main` wrapper around the
  words is the only markup change; Now's CSS keeps it invisible,
  `.pc-list` wraps it so the owner drops to a quiet second line with the
  day word — "Vineeth · Sunday", "Emily · Oct 2" — off today). Tick:
  optimistic through the existing `POST /api/chores/{id}/status`, in
  place (no client-side re-sort; the server's order lands on the next
  read), revert + toast "That didn't save. Try it again in a moment." on
  failure. **Cross-surface refresh** (the build-once gotcha): Now's
  `toggleChore` calls `loadPlanChores` and Plan's `togglePlanChore` calls
  `loadChores`; `refreshStaleTabsFromActions`' `today` branch re-reads
  Plan's list whether or not Now is built (the tab is the contract — any
  chore tool the concurrent `chores-chat-tools` branch adds is tagged
  `today`). `loadPlanChores` only redraws when the household is looking
  at Chores; `renderWeekMenu`'s empty-days reset no longer pulls a
  household off Chores. Empty moments (`EMPTY_ICONS.home`, new): never
  set up → "Chores aren't set up yet." / "Want me to suggest a list from
  what I already know about your place?" with the state's ONE dock,
  "Set up chores" → `/chores-setup` (Rule 5: the only case Chores has a
  dock); set up and empty → "Nothing's due." / "The house is fine.", no
  dock. `.is-chores` on the panel grows `#week-plan-view` for this state
  only (measured: `0 1 auto`, 316px in a 729px panel, left the dock
  mid-screen) — scoped like `.is-allset` so Meals' layout does not move.
  Three source-marker tests were kept honest rather than loosened: the
  `approve.hidden` line is intact with a second line for Chores;
  `test_chore_outsourced`'s Now-card test now slices `choreRowHtml`, the
  row's new home, same claims. 24 tests in `tests/test_plan_chores.py`
  (all 24 red on main); `tests/test_chores_switch.py`'s harness gained
  the shared builder. Suite 3043. Verified live on a throwaway DB at
  375×812, light and dark.

- **2026-09-12 — Chores is switched on per household, and the "Your
  chores" card is back on Now where the switch is on. Branch
  `chores-switch-and-now-card`, NOT merged at the time of writing.** Two
  Loop Board cards, "Chores v1: Who sees it — a per-household switch"
  (Emily: on in her house, off for the tester, so it is validated on real
  weeks before it competes with the meal loop) and "Chores v1: Turn the
  'Your chores' card on Now back on — and make it serve the story".
  `households.chores_enabled INTEGER NOT NULL DEFAULT 0` in schema.sql
  AND `_MIGRATIONS`; default off for every household including Emily's,
  because that is the state the retired global constant already had
  everyone in — turning a house on is a decision, not a migration's
  guess. `python set_chores_enabled.py on --household 1` (Railway:
  `railway ssh -- python set_chores_enabled.py on --household 1`), same
  guard pattern as `create_household.py`; no admin UI. Read at request
  time (`tools.chores_enabled()`), never cached. `/api/whoami` carries
  it; `shellWho.chores_enabled` → `choresEnabled()` replaces
  `SHOW_CHORES_ON_TODAY` in `buildTodayPanel` (card markup and the
  `loadChores` call both gated, so an off house makes no
  `/api/chores/today` request). **One gate for the nine chat tools**, at
  the dispatch in `run_agent_turn` (`CHORES_TOOLS`), not nine wrapped
  entries: the declined result is `is_error: True` with
  `tools.CHORES_OFF_MESSAGE` ("Chores isn't switched on for your house
  yet.") so `_turn_wrote_anything` never counts a declined `add_chore`
  and no "Chores updated" card is drawn — and it is neither logged as a
  failure nor written to `error_events`, so the morning report doesn't
  read the tester asking about chores as a broken tool. Routes: `GET
  /api/chores/today` answers **200 `{"chores": [], "chores_set_up":
  false, "enabled": false}`** while off (a 4xx would print "Couldn't
  load chores" into a card); `POST /api/chores/{id}/status` refuses with
  403. `get_household_setup_status` now reports `chores_enabled` and
  counts a house with people and the switch off as
  `onboarding_complete` — before this the system prompt read a
  members-only house as incomplete forever and opened every conversation
  with the chores questions; one prompt line tells the model not to
  offer chores in an off house. `/chores-setup` stays reachable by URL
  (its save routes are ungated); the only link in renders behind the
  switch. The card itself (ticket 2): re-cut against the tokens and its
  neighbours rather than redesigned — `--rule`/`--plum-ink`/`--muted`
  aliases → `--hairline`/`--ink`/`--ink-secondary`, 1px → 1.5px
  hairline, the 26px `.chore-checkbox` (under Rule 6, handler on the
  square) → the shared `.tick`/`.tick-box` at the row's end, `.tick-empty`
  spacer on an outsourced row, 15px/700 name like `.rest-row-title`.
  Empty line "Nothing due today." → **"No chores today."**; a house that
  has never set chores up gets the invitation ("Want help with chores
  too? Set them up", unchanged) alone rather than under an empty line
  that says the same thing. `refreshStaleTabsFromActions`' `today` branch
  now calls `loadChores` (the chore tools are tagged `today`; the card
  went stale after "the bins are done" in chat). Left alone, flagged: the
  empty moment's "Nothing on your list today." can sit under a chores
  card with rows on a day with no meal moves — Emily's copy, her call.
  37 tests in `tests/test_chores_switch.py` (35 red on main); 8 older
  tests rewritten from the constant to the switch. Suite 3012.

- **2026-09-12 — The setup question screens are the welcome screens'
  rhythm on ivory, and the reveal is the mirror of "Hi, I'm Pomona."
  Branch `worktree-setup-luxury`.** Loop Board "Setup question screens in
  the welcome flow's look: one idea per screen, big type, lots of air"
  (Emily, 2026-09-10: "luxurious, like the Apple onboarding for a new
  iPhone"). The 2026-09-11 audit found the questions breaking from the
  five approved welcome screens: three progress cues at once (a
  ten-segment strip, a "1 of 4 · Who's eating" eyebrow, the crumb), a
  darker apricot on Continue (`.next-btn`'s own fill vs the intro's
  `--apricot`), an × on every name row including the empty ones, controls
  hugging the title. All in `static/onboarding.html` (CSS + markup + the
  navigation block); copy untouched except two form labels. Each question
  is now crumb → 64px → `.q-title` (Bricolage 700 33px, `text-wrap:
  balance`) → one `.q-line` (17px `--ink-secondary`) → 32px → controls →
  a fixed `.q-foot` (§6 one dock) holding the four-dot `.q-pager`, the
  quiet skip line where a step has one, and the ONE `.btn-primary`. The
  strip and the eyebrow are gone: **`QUESTION_SECTIONS`** maps each
  question to one of four stops and `renderProgress` draws the intro's
  own dot row (`--hairline-strong`, current dot 18px `--ink-strong` — spruce
  on ivory, ivory on the dark ground). The grouping follows the ORDER
  asked (people+meals · what you eat · leftovers/prep/dinner · first week)
  so the long dot only moves forward; note the last welcome screen lists
  "How your week runs" before "What you eat" and the flow asks them the
  other way round — reordering questions is a bigger call than a look,
  Emily's to make. Chips are one look for both families (`.chip` and
  `.rhythm-chip`, 46px, 10px gaps, spruce when chosen, `--ivory-ink` not
  `#fff`); the × on a name row exists only once the row has a name
  (`.row.is-empty`, kept by `addMemberRow`) and is a stroke SVG; "+ Add
  person" is a full-width spruce outline; "Anything else?" moved from a
  label over each typed-answer box into its placeholder with an `.sr-only`
  label (§8.6); dinner-time's "So I can tell you when to start cooking."
  moved from under the chips to under the question, same words. **Every
  arrival is a crossfade** (`.step-enter`: opacity + 8px rise over
  `--motion-base`, the fixed foot fades only, `showStep` stamps it on a
  real arrival and not on the reveal's history trap); the body eases
  `background-color` and the apricot glow moved to a `body::before` layer
  so it can fade instead of cutting. This is DESIGN_SYSTEM §4's animation
  (2) applied to setup, not a fourth kind — but §4's "exactly three" list
  names tab panels only, so the doc needs a line (Tier 2, flagged, not
  edited here). **The reveal** (rule S5) is on spruce again
  (`body.reveal-active`, sharing the intro's rules): the mark, the eyebrow,
  a 38px title, then **the one number** — `renderRevealNumber`, the meal
  count from `revealPlanCounts` at 96px with the receipt's own word
  (meal/meals); chosen over dinners because it is the count the receipt
  already states and adds no new word — then the celadon receipt, the day
  cards as `--spruce-raised` tiles with `--apricot-rule` dividers, the
  invite as another tile, and the one apricot in a fixed spruce foot
  (`#reveal-actions` / `#reveal-error-actions` are each a foot; only one
  shows). Left alone on purpose: the crumb, every question and its order,
  the reveal's pre-existing italic on "Nothing planned", the LLM-failure
  path (now a raised spruce tile, calm). Verified in Chromium at 375×812
  light and dark and at 1280 against a throwaway copy of the
  before-onboarding-reset backup; the fake API key makes generation fail,
  so the success state was checked by calling the page's own renderers
  with a five-day plan. Tests: `tests/test_onboarding_setup_luxury.py`
  (51, new) — one pager and no eyebrow per question, `.btn-primary` and no
  own fill on the question button, no × on an empty row (run under node),
  chips ≥44px, tokens only, no italics, the cleansed copy still there,
  the crossfade, the reveal on spruce with the number. The two tests that
  pinned the strip (`test_chores_setup_split.py`,
  `test_onboarding_welcome_flow.py`) now pin `QUESTION_SECTIONS`; the
  go-back DOM stub's `classList.toggle` honours its force argument (it
  didn't, and the old intro-active test passed only because its steps
  alternated). Suite 3026 (was 2975).
- **2026-09-12 — What we know is native: one sheet, collapsible sections,
  every change saves as it is made. Branch `worktree-what-we-know-native`.**
  Two Loop Board cards from the 2026-09-11 design audit ("rebuild it native,
  in the Preferences sheet's vocabulary" + "needs autosave and collapse").
  It was `static/memory.html` in `#kit-sheet`'s iframe — the last surface
  running the old app: dotted chips, "Age group"/"Dietary restrictions"
  labels, a bordered-pill segmented control, Save/Cancel on every edit.
  Now `shell.js`'s "What we know (native, 2026-09-12)" section renders
  into `#wwk-body` (new sibling of the frame in `shell.html`;
  `openKitchenSheet` shows one or the other — Inventory keeps its iframe,
  deferred beta by Emily's call). Seven sections in the Preferences rows'
  order plus "Won't eat" (the household dislikes had no row of their own):
  each head IS a `.prefs-row` reading its line with the Preferences row's
  OWN function (`PREFS_ROWS` entries now carry `section:` not `tab:`), and
  opens in place; from a Preferences row only that section is expanded and
  scrolled to. Chips are `.wwk-chip` = the `.defrost-chip` recipe (celadon
  selected). Autosave is `wwkCommit`: apply to the cached `/api/memory`
  (shared with Preferences, so its rows are right on close with no
  re-read) → redraw → request → revert + toast on failure; a `seq` guard
  so a late reply never overwrites a later tap; text saves on blur/Enter.
  Three things learned the hard way, all in `wwkPreservingFocus`/
  `wwkMorph`: (1) a blur-triggered save that replaced the panel pulled the
  next tap's chip out from under the click before it landed — section
  redraws now MORPH block-by-block (only a changed chip row is swapped), and
  text-field saves redraw only the head line (`quiet`); (2) swapping out a
  focused input fires its blur synchronously mid-swap, before
  `isConnected` flips, and that blur's own save re-entered the morph
  (`NotFoundError` on `replaceWith`) — a `wwkRedrawing` flag tells the
  focusout handler to ignore it; (3) `scrollIntoView` on the sliding
  sheet landed short, and a node captured before the load's redraw was
  detached — the scroller's `scrollTop` is set directly, on a fresh lookup,
  after the slide. Real-data finds in the pre-reset backup: `age_group`
  "Adult" (capitalised) so no chip ever read selected — compared
  lowercased now; `protein_preferences` carrying "more"/"less"/"neutral"
  under "Fish / seafood"-style keys, which the old page read as unrated —
  `wwkProteinState` reads both shapes, exact lowercase key wins, clearing
  clears every key it lived under. `prefsPeopleLine` says "allergic to
  peanuts, kiwi" for "allergy: …" entries (helper defined AFTER it, because
  `test_kitchen_and_preferences.py` runs that slice under node). Escape on
  an add-input closes the input, not the sheet (stopPropagation). Left in
  place, deliberately: `static/memory.html` and the `/memory` route —
  `static/index.html` and `static/kitchen.html` (legacy, unrouted) still
  link there and `tests/test_embedded_pages.py` derives its closure through
  it; nothing in the shell loads it any more. Left out: dictation mic
  buttons (the keyboard mic covers it), member rename/remove (no API, old
  page had none). Verified live on a copy of the pre-reset backup at
  375×812 light + dark and 1280: every section edited, reloaded, stuck.
  `tests/test_what_we_know_native.py` (65: markup, wiring, a per-fact
  checklist of the old page's 36 editable facts, and the two line readers
  under node); 4 older tests re-pointed from tab to section. Suite 3040.
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
  `SHOW_CHORES_ON_TODAY` in `shell.js` (that constant became the
  per-household `choresEnabled()` on 2026-09-12; the inventory flag still
  sits beside it) — **plus a second copy inside
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
  current) / future-only / downstream grocery — all fine. Suite 2704.
  **CORRECTED 2026-09-13 — this entry used to end "…and the unlinked meal
  is visible tonight, so this isn't a 500 traded for a silent loss", and
  that sentence was FALSE.** It was true of `get_meal_plan` and of no
  screen: `get_cooker_view` is strictly plan-scoped, and Now's moves, cook
  mode and the Kitchen list are all built on it, so the answered dinner was
  saved and then invisible everywhere a person looks — a 500 traded for
  exactly the silent loss the sentence denied. The claim survived because
  the two tests above only ever asked `get_meal_plan`, which is not a
  screen. Fixed and widened on `overnight/needs-you-dinner-invisible` — see
  the 2026-09-13 entry at the top. The rest of this entry stands: the
  `weekly_plan_id = None` write is correct and unchanged. **The clause
  above saying Now treats the unlinked shape as first-class is left exactly
  as written, and it is the thing that was wrong** — it was an intention,
  not a measurement; deleting it would hide how the belief got here.
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
