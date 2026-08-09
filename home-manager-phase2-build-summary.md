# Home Manager — Phase 2 Build Summary

**Phase:** Meal Planning & Groceries, Next Phase (Draft v3 → shipped)
**Related:** `home-manager-user-stories.md`, `home-manager-prd-meal-planning-groceries.md`
**Status:** Shipped and dogfooded. Superseded by Phase 3 (in progress) as the active PRD.

## TL;DR

All six scoped workstreams shipped: plan-as-object foundation, grocery list quality
(sectioning + consolidation), a memory transparency UI, an onboarding rework that ends
in a real tailored first plan, a read-only Eater share link, and voice dictation. Beyond
the PRD's original scope, a round of real dogfooding surfaced and fixed several
production bugs (see "Bugs found in dogfooding" below) that are worth carrying forward
as lessons for how this phase gets tested going in.

## What shipped, by PRD section

**Plan-as-object foundation.** Weekly plans are now a first-class SQLite entity
(`weekly_plans`, linked from `meal_plan_entries` via `weekly_plan_id`) rather than living
implicitly in chat history. `generate_weekly_plan` produces a full week in one pass via a
forced tool call, pulling in household memory, saved recipes, and a 3-week recent-history
window to drive variety/freshness — auto-suggestions avoid repeating a meal, protein, or
cuisine from the last 3 weeks, but an explicit user request for a repeat is always
honored. Recipes are generated/adapted for the household, not filtered from a fixed
catalog — new recipes get saved automatically with `cuisine`/`main_protein` tags that
feed the variety check. `swap_meal_in_plan` handles single-day changes without
regenerating the whole week.

**Grocery list quality.** Items are grouped into store sections (produce, dairy,
meat/seafood, pantry, frozen, other) instead of a flat dump. Adding the same item twice
consolidates quantities when units reconcile (`"2 cups" + "1 cup"` → `"3 cups"`); when
they don't (e.g. cups vs. pounds), both amounts are kept together on one line rather than
guessing a conversion — an explicit product decision (a silently wrong guess erodes trust
faster than a redundant line). `consolidate_grocery_list` cleans up any pre-existing
duplicates.

**Memory transparency UI.** A dedicated "What We Know" page shows everything the app has
saved — dietary restrictions per member, protein preferences (frequency-based: "several
times a week" / "1-2 times a week" / "occasionally" / "rarely" / "avoid" — reworked from
an earlier vague more/less/neutral scale after dogfooding feedback), favorite cuisines,
dislikes, cooking-time preference, notes, household goals — all directly editable with
immediate save, no conversation required. Protein frequencies are editable inline
(dropdown per row) rather than requiring remove-then-re-add.

**Onboarding rework.** Household composition (members, ages, pets) and per-member dietary
restrictions are captured during onboarding, tied to named members from day one — ready
for the eventual multi-person-preferences phase without a data model rework. Onboarding
now ends by generating a real first weekly plan from what was just entered, shown before
landing in chat, rather than a generic confirmation screen.

**Eater share link.** A stable, tokenized, read-only link (`/share/<token>`) always
resolves to whichever plan is most recent — no re-generation needed as new weeks get
planned. No login, no new auth model. Exposes only the meal plan, nothing else about the
household.

**Talk-to-text dictation.** A mic button drives in-page transcription via the Web Speech
API on Android/desktop Chrome, with live interim results streaming into the input as you
speak rather than dumping everything at once at the end. iOS Safari has no Web Speech
API, so dictation there is the native keyboard mic key (already functional with zero
code) — the button just focuses the input and points at it once, since that affordance is
easy to miss.

## Bugs found in dogfooding (fixed)

Real usage surfaced several issues the sandbox testing didn't catch — worth noting as a
pattern for how future phases should budget testing time:

- **Grocery categorization**: eggs/tofu/snap peas were miscategorized as "pantry."
  Root cause was two-layered — a missing system-prompt rule, and a separate schema gap
  where recipe-ingredient-driven grocery adds had no `category` field at all, so they
  silently defaulted to "other" regardless of prompt fixes.
- **Duplicate grocery lines**: pre-existing rows created before consolidation logic
  shipped don't get retroactively merged by write-time consolidation — needed a dedicated
  one-time cleanup tool (`consolidate_grocery_list`), and the system prompt was updated to
  call it proactively instead of asking permission first.
- **Grocery quantities silently stacking across weeks**: nothing distinguished "this
  week's ingredients" from "leftover from 4 weeks ago," so quantities (e.g. "9 lbs
  chicken breast") kept accumulating indefinitely. Fixed by tagging grocery items with
  the weekly plan they came from and auto-clearing stale ones whenever a new plan
  generates.
- **First-time onboarding plan generation failing outright**: `max_tokens` on the plan
  generator (4096) was tuned for a household with saved recipes to reuse; a brand-new
  household with zero saved recipes needs every day to be a fully new recipe (full
  ingredients/tags/cuisine), which routinely exceeded the limit and silently broke the
  whole generation. Raised to 8192.
- **Silently empty chat replies**: the main chat loop's `max_tokens` (1024) was too tight
  for the model to summarize a full generated week back to the user — it could hit the
  cap before writing any text at all, and the code treated that as a normal finish,
  producing a blank bubble with zero explanation. Raised the budget and added a fallback
  message so an empty reply can never reach the user silently again.
- **Agent had no live clock**: relative dates ("tomorrow," "this week") required asking
  the user what today's date was, every time. Fixed by injecting the actual current date
  into the system prompt on every turn.
- **PWA/service-worker caching**: the original service worker cached the app shell
  cache-first with a version tag that never changed, so an installed PWA (or any
  browser tab that had visited once) could get permanently stuck on whatever version was
  live at first install — new deploys were invisible without a manual hard refresh or
  reinstall. Reworked to network-first for pages (icons/manifest stay cache-first), so
  new deploys are picked up on the next load automatically.
- **Voice dictation errors were silent**: mic permission denial, no microphone detected,
  etc. all failed with zero user-facing feedback, making "it's not working" undiagnosable.
  Added specific, actionable error messages per failure type.

## Known limitations carried forward

- Single household, single user, no auth — still true; multi-tenant is a structural wall,
  not yet scoped.
- Chat history is in-memory per server process; a redeploy wipes conversation context
  (data itself persists in SQLite).
- Chore scheduling was explicitly frozen for this phase.
- General chat latency and the two-LLM-call weekly-plan-generation path were both flagged
  as feeling slow — a loading indicator was added as an interim mitigation, but the
  underlying speed question hasn't been investigated yet.
- Chat message truncation in the UI (long assistant replies cutting off mid-word) is
  logged but deferred, not fixed.

## New technical surface (for context on what Phase 3 builds against)

- **Schema**: `weekly_plans`, `share_links` tables; `cuisine`/`main_protein` columns on
  `recipes`; `weekly_plan_id` on `meal_plan_entries`; `source_weekly_plan_id` on
  `grocery_items`.
- **New tools**: `generate_weekly_plan`, `get_weekly_plan`, `swap_meal_in_plan`,
  `approve_weekly_plan`, `get_household_memory`, `edit_preference`, `delete_preference`,
  `get_grocery_list_by_section`, `consolidate_grocery_list`, `clear_stale_grocery_items`,
  `clear_grocery_list`, `get_or_create_share_link`, `get_shared_weekly_plan`.
- **New pages**: `/memory` (What We Know), `/share/<token>` (Eater view).

## Success criteria — met?

All PRD success criteria were met: onboarding ends in a real, plan a first-time user
would describe as tailored (not generic); the grocery list reads like something shoppable
aisle-by-aisle with no silent unit-conversion guessing; the share link works with no
login; and dictation works on both target platforms (via the API on Android/desktop,
via the native keyboard on iOS). The dogfooding signal criterion is also met — Emily has
been actively using and reporting on the app in normal daily use, which is how most of
the bugs above got caught.
