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

## Current state (as of 2026-09-04)

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
`static/shell.js`). Cooking is a *state* of the Meals tab, not its own tab.
Chores have a working backend but no tab, and are deliberately hidden from
the beta (`OFFER_CHORES_AFTER_REVEAL = false` in `static/onboarding.html`).

If you're picking this repo up fresh, run `git log --oneline -15` to confirm
this is still accurate.

**Known live bug at the time of writing:** `/api/chat/stream` crashes on any
chat turn that returns an action card — `_sse_event` serialises a pydantic
`ChatAction` with plain `json.dumps` (`jsonable_encoder` appears nowhere in
`app/`). Reproduced on `main` 2026-09-04.

Note the symptom carefully, because it is worse than "chat writes don't
work": the tool call and the database write **do** commit, and the crash
happens afterwards while encoding the frame. So the change really happens
while the screen says `Error: Request failed` — and `streamChatMessage` has
no fallback to `/api/chat`, so that is all the user sees. It invites doing
the thing twice.

**No fix exists in this repository.** A fix was reportedly built in a
worktree outside this repo (branch name `fix-chat-stream-actions`) and never
pushed — it is not on `origin` and not in any local branch or worktree here,
so don't go looking for it. The test gap that let this ship is real too:
`tests/test_streaming_endpoints.py` stubs `summarize_chat_actions` to return
`[]`, so no test ever puts a real `ChatAction` through the encoder.

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
  - **STILL OPEN, and Emily's call: `retire_overlapping_plans` is not
    atomic across plans.** Every decision is computed before anything is
    destroyed, but each plan's meals, prep tasks and grocery reversal commit
    before the next plan is touched. A failure mid-loop (a locked database,
    a killed process) leaves the first plan's days genuinely gone while the
    household sees an error saying nothing was saved. Only reachable when a
    single generation takes over two or more plans at once. A real fix needs
    one transaction spanning the loop, which fights the
    connection-per-operation style and `_reverse_meal_grocery_contributions`
    committing internally — worth doing deliberately, not as a footnote to
    this branch.
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
- **#10** — no shared `api.js`; ~26 hand-written `fetch('/api/...')` call
  sites, and pages carry 30–60 KB of inline CSS/JS each that can't be
  cached separately from the page.
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
