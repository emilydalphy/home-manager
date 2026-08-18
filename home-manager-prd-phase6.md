# PRD: Home Manager — Phase 6 (Closing the Design-to-Build Gap)

**Status:** Draft v3 (audits complete — see §4.1 Findings; build in progress per §8)
**Owner:** Emily
**Related:** home-manager-user-stories.md (Theme 9), home-manager-design-context-brief.md,
home-manager-screen-specs.md, Home Manager - Branding package.pdf

## 1. Background

This phase didn't come from the original roadmap — it came from applying the brand/screen
specs to the four priority screens and finding nine places where the design called for
functionality that isn't styling, it's feature work: new data to track, new logic to run, or in
three cases, functionality that may or may not already exist and just isn't confirmed. Several
of these are stories that earlier phase PRDs marked shipped (US-1.2/1.7, US-2.2, US-2.4,
US-4.4, US-6.3, US-3.7, US-7.7) but which turn out to have shipped the underlying
generation or backend logic without the surfacing layer that makes it visible to the user — the
same gap the design principles explicitly warned against: personalization should be visible,
not just felt.

This is a completion pass, not new product direction. Nothing here changes scope or
priorities elsewhere in the roadmap — it closes gaps between what's already been decided
and what the app actually shows.

## 2. Goals

- Make already-shipped backend logic (freshness tracking, per-meal reasoning, first-run
  distinction, the growth-signal thesis, per-person preferences, multi-store splitting)
  actually visible in the UI, per the screen specs.
- Resolve the three genuine unknowns — is conflict-detection logic real, does any
  delete-confirmation step exist, is multi-store tab visibility already conditional — with a
  quick audit before assuming either "just wire it up" or "build from scratch."
- Ship the two pieces of confirmed new functionality: a real growth-signal log to count
  from, and the serving-size stepper.

## 3. Non-goals (this phase)

- **Any new feature work beyond these nine items.** This phase is explicitly a completion
  pass on already-decided scope, not an opportunity to add new capability.
- **Further design work.** The screen specs are done; this phase is implementation
  against them.
- **Anything from the Phase 5 (voice) or Phase 4 (multi-person/inventory) backlogs** —
  those proceed on their own tracks independent of this pass.

## 4. Scope for this phase

### 4.1 Audits (do these first, cheaply, before scoping the rest in detail)

- **Conflict-detection logic (US-9.3):** confirm whether US-2.2's flag-and-override
  conflict warning was actually implemented on the backend during Phase 3, or only
  scoped. If it exists, this is a UI-wiring task. If it doesn't, it's real backend work that got
  missed.
- **Delete-confirmation step (US-9.7):** confirm whether memory.html (or wherever
  preference deletion lives today) has any confirmation step at all. If none exists, this is
  genuinely a small addition — a confirm dialog with the warm copy already drafted in the
  screen specs. If one exists but is generic/clinical, this is a copy-only change.
- **Multi-store tab conditional logic (US-9.8):** confirm whether the backend already
  returns single-store vs. multi-store state to the frontend, and whether the frontend
  already conditionally renders the tab bar based on it, or whether the tabs were just
  visually styled without that logic behind them.

#### 4.1 Findings (audit complete)

- **Conflict-detection logic (US-9.3): exists, but only reaches chat text, not a visible
  banner.** `tools.check_plan_conflicts()` (Phase 3, Workstream D) is real, working backend
  logic — it checks planned meals against household members' saved dietary
  restrictions/allergies via keyword matching against recipe ingredients, and is wired as an
  agent tool the system prompt instructs the model to call right before
  `approve_weekly_plan` and mention any conflicts found. What's missing is the surfacing
  layer the screen specs call for: there's no dedicated interactive weekly-plan screen
  today (plan approval happens conversationally in chat), so there's nowhere for a compact
  Electric Coral banner to live, and the read-only share.html view never calls
  `check_plan_conflicts` at all — an outside viewer never sees a flagged clash even if one
  exists. **Verdict: UI-wiring task, not backend work** — but "wiring it up" means building
  an actual visual surface for it, most likely on share.html since that's the closest thing to
  a "weekly plan view" that exists.
- **Delete-confirmation step (US-9.7): does not exist.** `removeItem()` in memory.html
  calls `/api/memory/delete` immediately on click with zero confirmation of any kind — no
  native `confirm()`, no dialog, nothing. **Verdict: genuinely missing, build from scratch**
  (the warm copy is already drafted in the screen specs: "Remove this? You can always
  tell me again.").
- **Multi-store tab conditional logic (US-9.8): does not exist.** grocery.html's "By store"
  view is a persistent third toggle (alongside "To buy" and "Purchased") that's always
  shown regardless of how many stores the household uses — it's not a segmented
  per-store tab bar (e.g. a "Costco" tab next to a "Farm Boy" tab) and there's no backend
  signal distinguishing single- vs. multi-store households at all (`usual_stores` is just an
  unordered list; nothing computes or returns a count). **Verdict: no conditional logic
  exists, needs to be added** if single-store households should stop seeing "By store" as
  a meaningful option — worth noting the current always-available grouping view is a
  reasonable design on its own merits, so this is more a polish decision than a bug fix.

### 4.2 Weekly plan view completions (US-9.1, US-9.2, US-9.4)

- **Freshness tag:** surface a count of new-vs-repeat recipes for the current week's plan.
  Likely a computed field returned alongside the plan object rather than a schema
  change, since the served-meals history needed to determine "new" already exists from
  the Phase 2/3 variety-checking logic.
- **Per-meal reasoning:** generate and persist a short rationale for each planned meal,
  retrievable instantly when the Planner taps "why this?" — see §5 for the
  store-vs-compute decision this depends on.
- **First-run marker:** distinguish a household's very first generated plan so the
  onboarding intro banner (US-6.3's payoff) can actually render. See §5 for how this
  should be flagged.

### 4.3 Memory view completions (US-9.5, US-9.6, US-9.7)

- **Growth counter:** count and surface every preference write event (create, update, or
  delete) within the current calendar month, backed by a real log — see §5, this needs
  new tracking, not just a UI number.
- **Per-person grouping:** restructure the memory view to group by household member,
  reading from the members table confirmed to exist in Phase 4, with a general/household
  bucket for preferences not tied to a specific person.
- **Delete confirmation:** per the §4.1 audit outcome, either add the confirmation step
  with the warm copy already drafted in the screen specs, or replace clinical copy with it.

### 4.4 Grocery list view completion (US-9.8)

- Per the §4.1 audit outcome, either confirm the existing conditional logic is correct, or
  add it so single-store households never see a tab bar.

### 4.5 Cooker prep view completion (US-9.9)

- Build the serving-size stepper: a simple –/+ control on the recipe detail view that
  recalculates ingredient quantities live. See §5 for handling non-numeric quantities.

## 5. Technical considerations

- **Per-meal reasoning: store at generation time, not compute on demand — decided.**
  The model already has the relevant context (preferences, history, constraints) at the
  moment it generates the plan — persisting a short rationale then costs a small amount
  of extra generation output, versus a full extra round-trip (latency and cost) every time a
  user taps "why this?" later.
- **First-run marker: an explicit flag, not a derived query.** Set an `is_first_plan` flag
  (or equivalent) at the moment onboarding generates the first plan, rather than inferring
  "first plan" by querying for the household's earliest plan row. The explicit flag is robust
  to future data changes (backfills, edits, re-onboarding); a derived "earliest row" query is
  fragile and could silently break.
- **Growth counter needs a real event log, not just current-state preference rows.** If
  preferences are currently stored as current-state only (which the Phase 4
  destructive-write bug findings suggest is likely — recall
  `set_member_dietary_restrictions` did a full replace with no history), there's nothing to
  count "8 things" from yet. This needs an append-only log of preference edits
  (create/update/delete, timestamped) — worth sequencing this alongside or right after the
  Phase 4 fetch-then-merge fix, since both touch the same write path and it'd be wasteful
  to modify it twice.
- **Per-person memory grouping** needs a clear answer for where household-level
  (non-member-specific) preferences live in a per-person-grouped UI — a "General" or
  "Household" bucket alongside named members, rather than forcing every preference to
  belong to a specific person.
- **Serving-size stepper** needs to handle non-numeric or imprecise quantities ("a pinch,"
  "to taste") — these can't scale mathematically. Recommend leaving non-numeric
  quantities as-is rather than attempting to scale or hide them, so the stepper doesn't
  produce nonsensical output for the recipes where it matters least anyway.

## 6. Decisions (formerly open questions)

- **Per-meal reasoning:** store at generation time, per §5. Means rationale gets
  generated and persisted for every meal even if the Planner never taps "why this?" —
  accepted tradeoff given the alternative is a live round-trip on every tap.
- **Growth counter definition:** count every preference write event — create, update, or
  delete — not just net-new facts. A correction ("actually I don't like broccoli anymore") is
  still a sign of active engagement and should count the same as a brand-new fact. No
  deduping for repeated edits to the same item for now; revisit only if real usage shows
  that's actually noisy or gameable, consistent with the pattern of not over-building before
  there's data to justify it.
- **Growth counter window:** calendar month, not a rolling 30 days — matches the "this
  month" copy literally and is simpler to compute and reason about than a rolling window.
- **Audits (§4.1): still pending, and correctly so.** Whether conflict-detection logic exists,
  whether any delete-confirmation step exists, whether multi-store tab logic is already
  conditional — these are facts about the code, not product judgment calls, so they can't
  be resolved on paper. They stay the explicit first action item in §8's build order rather
  than being decided here, same treatment as Phase 5's speech-to-text engine question.

## 7. Success criteria for this phase

- All three audits (§4.1) completed with a documented finding, even if the finding is
  "already existed, just needed wiring."
- The weekly plan view shows a freshness tag, working "why this?" reasoning per meal,
  and a first-run intro banner on a genuinely first-generated plan.
- The memory view shows a real growth counter backed by an actual log, is grouped by
  household member, and has a warm delete-confirmation step.
- The grocery list's store tabs appear only when multi-store splitting is actually active.
- The Cooker's recipe detail view has a working serving-size stepper that recalculates
  quantities live, without breaking on non-numeric quantities.

## 8. Suggested build order within this phase

1. **The three audits (§4.1) first** — cheap, fast, and they determine whether the rest of
   this phase is smaller or larger than it currently looks.
2. **Serving-size stepper (§4.5)** — fully new and self-contained, no dependencies on
   anything else in this phase.
3. **Freshness tag and first-run marker (§4.2)** — both relatively lightweight additions to
   the existing plan-generation response.
4. **Per-meal reasoning (§4.2)** — do after the above, once the store-vs-compute decision
   in §6 is confirmed.
5. **Growth counter and per-person grouping (§4.3)** — pair these together since both
   touch the preference/member data model; sequence the growth counter's event-log
   work alongside the Phase 4 destructive-write fix if that hasn't already shipped, since
   they touch the same write path.
