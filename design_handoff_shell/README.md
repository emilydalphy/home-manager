# Home Manager — app shell + weekly menu redesign

Visual source of truth: `Home Manager Prototype.dc.html` (open it in a browser — it is clickable).
Design reference for alternatives considered: `Home Manager Layout Options.dc.html`, sections `#3a` / `#3c`.

This replaces the current `static/index.html` flow (chat box as the home screen, nav as a
run of text links) with a persistent app shell. It is the **3a shell + 3c needs-you band**
direction, which we picked over pure-chat (3b) and pure-queue (3c).

---

## 1. What we are fixing

Audited from the live app. Each fix is non-negotiable; the layout details below are how we do them.

1. **Nav is a sentence.** Six destinations as 0.85rem inline links in a subtitle paragraph, no
   current-page marker → **persistent nav: 4 bottom tabs on mobile, left rail on desktop.**
2. **Home shows no state.** Landing screen is an empty chat box; you must type to learn what's for
   dinner → **Today answers before you ask.**
3. **Setup and daily use compete.** "Set up chores" sits next to "This week's chores" in one flat
   row of seven buttons → **setup folded into Kitchen → "What we know".**
4. **Every page is a dead end.** Grocery / Cooker / Inventory / Memory are separate full-page loads
   with no way back and no assistant → **shell persists on every route; ask bar always reachable.**
5. **No "today".** → **Today is the default route.**
6. **Two doors to the same facts.** "Edit household setup" and "What we know" both edit household
   truth → **one door: Kitchen → What we know.**

---

## 2. Information architecture

Four destinations, in this order. These are the only top-level routes.

| Tab | Route | Contains |
| --- | --- | --- |
| Today | `/` | Needs-you band, tonight's dinner, my chores, grocery summary |
| Week | `/week` | The weekly menu (breakfast / lunch / dinner, 7 days) |
| Grocery | `/grocery` | Existing grocery list (redesign 13a), grouped by store |
| Kitchen | `/kitchen` | Cook mode, running low, **What we know** (absorbs onboarding + memory) |

Retired as top-level destinations: `/onboarding` (→ Kitchen › What we know), `/memory`
(→ same), `/cooker` (→ Kitchen), the chat home (→ ask bar / ask sheet).
Keep the old routes alive as redirects for a release.

The **ask bar is not a destination** — it is docked on every screen and opens a sheet over the
current screen. The user never leaves what they were looking at to ask something.

---

## 3. Tokens

Already in `static/theme.css` unless noted. Do not hardcode; add the missing ones to `:root`.

```
--plum:        #66304e   brand surface, active nav, primary button
--plum-ink:    #3a1f2e   headings, body ink
--gold:        #f0b429   accent on plum, primary CTA on dark
--gold-ink:    #3a2a0a   text on gold
--cream:       #f6efe1   app background
--card:        #ffffff   card surface
--menu-paper:  #fffdf6   menu card surface            (new)
--menu-rule:   #e7dcc4   menu card border / rules     (new)
--menu-ink:    #a8825c   menu meta, course labels     (new)
--sand:        #ecdfc8   secondary button, soft card
--rule:        #f1e8da   list separators
--muted:       #6b5a63   secondary text
--faded:       #a3939b   completed text
--urgent:      #e8562a   needs-a-decision
--warn:        #f0b429 / ink #b98a12   time-boxed
--good:        #6fb84c / ink #4d8a33   done, your turn
```

Type: **Quicksand 700** for headings and dish-level emphasis, **Karla** for everything else.
Karla 800 + `letter-spacing:.1em` + uppercase for kickers.

Radii: cards 18px, buttons/pills 12px, chips 22px, sheet 26px top corners.
Minimum body text 15px. **Minimum tap target 44px** — this is used one-handed while cooking.

---

## 4. Mobile (390pt baseline, build fluid 320–480)

### Shell

```
status bar
┌───────────────────────────────┐
│ scrolling screen content      │  flex:1, overflow-y:auto
│                               │  padding: 6px 20px 8px
└───────────────────────────────┘
│ docked ask bar                │  flex-shrink:0, padding 4px 18px 8px
│ tab bar                       │  flex-shrink:0, white, 1px top rule --rule
```

**Ask bar (docked, every screen):** white, `2px solid --plum`, radius 16, 14px 18px padding.
Placeholder "Ask or add anything…" in `--muted` 16px. 36px sand square with `↑` on the right.
Tapping anywhere on it opens the ask sheet.

**Tab bar:** `display:grid; grid-template-columns:repeat(4,1fr)`, padding `8px 12px 20px`
(bottom padding is the home-indicator safe area). Each tab: 24px rounded chip + 13px Karla 700
label, stacked, centered. Active chip `--plum` / label `--plum`; inactive chip `#d8c8b4` /
label `--muted`. **Badge:** when a tab is *not* active and has open items, a `--urgent` pill
(11px, white, 1px 6px) at top-right of that tab. Today is the only tab with a badge for now
(count = open needs-you cards).

Icons: the prototype uses rounded squares as placeholders. Ship real icons — calendar-day,
calendar-week, cart, pot. Same 24px box, `currentColor`.

### Today

Order top to bottom:

1. **Heading** — "Tuesday, Aug 25" (Karla 700, 13px, uppercase, `.1em`, `--muted`) then a
   Quicksand 700 29px H1 whose text is derived: `0 → "You're clear"`, `1 → "1 thing needs you"`,
   `n → "n things need you"`.
2. **Needs-you band** — 0–3 cards, only while unresolved. White card, radius 18, padding
   `16px 17px`, **`border-top:4px solid`** in the card's urgency color. Structure: kicker (12px
   Karla 800 uppercase, colored) → Quicksand 700 21px title → the resolution controls *inline*.
   - *Dinner decision* (`--urgent`): two option rows on `--cream`, radius 12, `13px 16px`,
     dish + minutes on the left, "Pick" in `--plum` on the right. Tapping one resolves the card.
   - *Shop run* (`--warn`): 4-item summary line + `Shop now` (plum bg / gold text → Grocery) and
     `Later` (sand bg / plum text → dismiss until tomorrow evening).
   - Cards animate in/out (`popIn`, 220ms) and **disappear when resolved** — the band shrinking is
     the reward. On resolve, show a toast (see §6).
3. **Tonight's dinner** — the one plum card on the screen. Kicker gold "Tonight · 6:30 · 35 min",
   Quicksand 700 23px white title, then `Cook mode` (gold) + `Swap` (1.5px white-40% outline).
   `Swap` opens the ask sheet pre-filled with "Swap tonight for something faster".
4. **Your chores** — white card. Header row: "Your chores" + "1 of 3" in `--good`. Rows 52px,
   26px checkbox (2px `#c4b09c` border; checked = `--good` fill + white ✓), name strikes through
   and goes `--faded` when done. Whole row is the tap target. Optimistic toggle.
5. **Grocery summary** — white card, "Grocery run" + derived subtitle
   (`n items · needed before Thursday`, or "All picked up"), `Open` sand button → Grocery tab.

Only *my* chores here, not the household's. Household view lives in a later pass.

### Week — the menu (see §5, this is the piece with the most new design)

### Grocery

Keep the shipped 13a redesign as-is; it now renders **inside the shell** (scroll area + docked ask
bar + tabs) instead of as a standalone page. Header: "2 stores" kicker + "Grocery" H1 + derived
`n of m got` counter in `--good`. One white card per store with the store name, a meta line
("before Thursday" / "on the way home"), and 50px item rows: 24px checkbox, name, and a
`#9c8b93` 14px reason on the right ("Thu dinner", "low", "tikka") that disappears once got.

### Kitchen

1. Plum card — "Cooking tonight" kicker, dish, `Start step 1` (gold) + `Ingredients` (outline).
2. **Running low** — white card, 50px rows, right-side action that flips `Add to list` → `Added ✓`
   after tapping (and actually adds to the grocery list).
3. **What we know about you** — white card. One prose paragraph of household truth
   ("4 people · no pork · Sam won't eat peppers · Thursdays are tee-ball · Costco every other
   Saturday") + a plum `Correct or add something` link that opens the ask sheet. This is the *only*
   door to household setup — onboarding writes here, memory reads from here.
4. Sand card — one nudge ("Scan a fridge photo so I stop suggesting what you already have").

### Ask sheet

Full-screen scrim `rgba(58,31,46,.4)` (tap to dismiss) + bottom sheet: white, radius 26 top,
`max-height:76%`, `animation: sheetUp 240ms cubic-bezier(.2,.8,.2,1)`, 44×5px grab handle.

- Message list scrolls. User bubbles: plum bg, white, radius `16 16 4 16`, right-aligned, max 88%.
  Assistant bubbles: `#f4f0e6`, plum ink, radius `16 16 16 4`, left-aligned.
- **Every assistant reply that changed something emits an action card** below the bubble: white,
  `1.5px solid #e0d3bf`, radius 14 — green 12px uppercase kicker ("Week updated"), the change
  ("Thu · Turkey rice bowls"), and `View` on the right that **navigates to the tab that changed**.
  This is the fix for dead-end answers: an answer always offers the screen it affected.
- Suggestion chips above the input, `--cream` bg, plum 700 15px, radius 22.
- Composer: `--cream` bg, 2px plum border, radius 16, plus a 40px plum/gold send button.
  Enter sends. Sheet stays open after sending.

---

## 5. The weekly menu (Week tab)

Treat this as a printed restaurant menu, not a table. It carries the brand's warmth and it is the
screen people will screenshot and share.

**Data model change — this is the one backend ask.** The week currently stores one dinner per day.
It needs **three slots per day**: `breakfast`, `lunch`, `dinner`, each nullable, each with
`{ title, meta, source }` where `meta` is the short right-hand note ("15 min", "reheat",
"packed", "takeout", "6:30") and `source` says whether it came from the plan, leftovers, or
takeout. Nullable matters: an empty slot is what drives the needs-you card and the "Pick" row.

### Mobile

1. **Menu header card** — `--menu-paper` bg, `1px solid --menu-rule`, radius 18,
   `box-shadow:0 2px 0 #efe4cf` (the "stacked paper" edge), centered contents:
   - rule — `EST. 2019` (Karla 800, 11px, `letter-spacing:.28em`, `--menu-ink`) — rule
   - household name, Quicksand 700 31px ("The Dalphy House")
   - italic Karla 15px `--menu-ink` subtitle: "menu for the week of August 24"
   - rule — three 5px `#c9a24a` dots — rule
   - one derived status line ("Thursday is tee-ball night. Dinner still needs a decision.")
2. **Seven day cards**, one per day, same paper treatment, `padding:15px 18px 13px`,
   `position:relative; overflow:hidden`, **`flex-shrink:0`** (see §8), with a 4px top ribbon:
   plum = tonight, `--urgent` = has an empty slot, `--good` = just changed by the assistant,
   transparent otherwise.
   - Day header row: Quicksand 700 19px day name, `--menu-ink` 14px date, a hairline rule filling
     the remaining space, then an 11px Karla 800 `.14em` uppercase status
     (`Served` / `Tonight` / `Updated` / `Needs you`).
   - Three course rows (Breakfast, Lunch, Dinner) — **dish, dotted leader, meta**:
     dish 16px (Dinner is Karla 700, the other two 400), then a `1.5px dotted --menu-rule` filler
     that eats the slack, then the meta right-aligned in `--menu-ink` 700 14px. Course label sits
     *below* the dish in 10px Karla 800 `.18em` uppercase `--menu-ink` — the dish leads, the label
     annotates.
   - **Empty slot:** dish text becomes italic `--urgent` copy that says why it's open
     ("Choose a dinner — tee-ball night"), meta becomes `Pick` in `--urgent`, and the row is
     tappable → Today (where the decision card resolves it). Nothing else on the menu is tappable.
3. Footer card: "Tell me what's happening this week and I'll rebuild the plan." → opens ask sheet.

Past days grey to `--faded`; do not hide them — the week reads as a whole.

### Desktop

Same paper, laid out as one menu card instead of seven:

- One `--menu-paper` sheet, max-width 1100px, centered, with the same rules/dots header scaled up
  (household name Quicksand 700 ~40px).
- Body is a **7-column grid** (`grid-template-columns:repeat(7,1fr)`) with **three rows**:
  Breakfast, Lunch, Dinner. Day names are the column heads (Quicksand 700 18px + date beneath);
  a left gutter column carries the three course labels, rotated off — plain uppercase Karla 800
  10px `.18em`, right-aligned. Hairline `--menu-rule` between rows and columns; no dotted leaders
  at this width (the grid does the aligning), meta drops under the dish in `--menu-ink` 13px.
- Today's column gets a `--menu-paper`-on-plum tint (`rgba(102,48,78,.06)`) and a 3px plum top
  edge. Empty slots keep the italic `--urgent` copy + `Pick`.
- Below 1100px, fall back to the mobile stack of day cards. Do not try to squeeze 7 columns
  into a tablet.

---

## 6. Interactions and derived state

Nothing on these screens is decoration — every count and heading is derived, never typed.

| Thing | Derived from |
| --- | --- |
| Today H1 | number of unresolved needs-you cards |
| Today badge | same count; hidden when Today is the active tab |
| "1 of 3" chores | `chores.filter(done).length / chores.length` |
| Grocery subtitle + "n of m got" | items not yet got |
| Menu status line | first unresolved conflict this week |
| Day ribbon / status | is-today, has-empty-slot, changed-in-last-session |

- **Resolving a dinner decision** sets the slot, removes the card, shows a toast, and marks that
  menu day `Updated` — and if the pick displaces something (salmon → Saturday), the menu shows
  both moves. Cascades must be visible, not silent.
- **Toast:** `--plum-ink` bg, white, radius 14, `14px 17px`, 16px above the tab bar,
  auto-dismiss 2.2s. Used for resolutions and adds only. Never for errors.
- **Optimistic everything** (checkboxes, picks, adds) with rollback + a red inline note on failure.
- **Assistant replies** must always come back with (a) plain-language confirmation of what changed
  and (b) an action card pointing at the tab that changed.
- Animations: `popIn` 200–220ms for cards/toasts, `sheetUp` 240ms for the sheet. Nothing else moves.

---

## 7. Desktop shell (the rest of it)

Breakpoint at 1024px. Same components, rearranged — no new screens.

- **Left rail**, 230px, `--plum`, full height: "Home Manager" wordmark, then Today / This Week /
  Grocery / Kitchen (18px chip + Karla 16px label, 14px 22px padding). Active row:
  `background:rgba(240,180,41,.18)`, white 700 label, gold chip. Needs-you count as a `--urgent`
  pill on the right of the Today row. A hairline divider, then `What we know`. Bottom:
  outlined `Share meal plan`.
- **Today**, 3 columns in `36px` gutters on `--cream`: the plum dinner card spans the top full
  width (dish left, `Cook mode` / `Swap` right); below it a 1.5fr / 1fr / 1fr grid — needs-you
  cards (here with a `border-left:5px` instead of a top ribbon, controls right-aligned inside the
  card), then Chores, then **Ask** as a *permanent third column*, not a sheet: the message list
  plus the composer pinned to the bottom of that column.
- **Week**: §5 desktop menu, full width.
- **Grocery / Kitchen**: two-column card grids inside the same rail; no new content.
- The ask sheet only exists below 1024px. Above it, the ask column replaces it.

---

## 8. Implementation notes (things that bit us)

- Same stack as the rest of `static/`: plain HTML, vanilla JS, `fetch`, no build step, no framework.
- The scroll area is a `flex-direction:column` container; **every card in it needs
  `flex-shrink:0`** or cards with `overflow:hidden` (the menu day cards) collapse to a few pixels
  and clip their own content instead of scrolling. This broke the menu once already.
- Scroll area, ask bar, and tab bar: `flex:1` / `flex-shrink:0` / `flex-shrink:0`. Hide the
  scrollbar (`::-webkit-scrollbar{width:0}`), keep momentum scrolling.
- Bottom padding on the tab bar must respect `env(safe-area-inset-bottom)`.
- Tab switching must not reload the page — the shell persists, only the scroll area swaps. This is
  the whole point of the redesign.
- Preserve every behavior the current pages have (grocery filters, exclude/include, inline edit,
  store assignment, cooker steps, memory edits). Inventory them before you move them.

---

## 9. Build order

1. **Shell** — routes, persistent tabs/rail, scroll area, docked ask bar. Grocery and Kitchen just
   render their existing markup inside it. Nothing else changes. Ship this first; it alone fixes
   problems 1, 4 and 6.

   **✅ Done.** `static/shell.html` + `shell.css` + `shell.js` implement the persistent frame:
   bottom tabs on mobile, 230px plum rail at ≥1024px, a flex scroll area, and the docked ask bar.
   `/`, `/week`, `/grocery`, `/kitchen` all serve `shell.html`; `shell.js` client-routes between
   the four tabs via `history.pushState` with no page reload. Today/Grocery/Kitchen embed the
   existing `static/index.html` / `grocery.html` / `cooker.html` unmodified via `<iframe>` — zero
   changes to their internals. Week has no prior page, so it's a plain placeholder until Step 4.
   `/cooker` now redirects to `/kitchen` (same content). `/onboarding` and `/memory` are
   deliberately **not** redirected yet — Kitchen has no "What we know" section to receive them
   until that content is actually built, so redirecting now would strand setup/memory editing.
   The old top-level onboarding-redirect check (new household → `/onboarding`) moved from
   `index.html` into `shell.js` so it redirects the whole tab instead of trapping a first-time
   visitor inside the Today iframe. Added `--plum/--gold/--cream/--urgent/--warn/--good/--menu-*`
   etc. as additive tokens in `theme.css` (aliases onto the existing brand vars — nothing renamed
   or removed). Sandbox-verified: fresh DB → onboarding redirect fires at the top level; seeded DB
   → all four routes, the `/cooker` redirect, and `/onboarding`/`/memory`/`/inventory` all return
   the right thing; tab switching confirmed to not reload (a JS marker survives switching through
   all four tabs); desktop breakpoint confirmed (rail shows, tab bar hides at ≥1024px).

   Known interim gap from this step, closed in Step 2 below: at the time Step 1 shipped, Today's
   panel was still `index.html` embedded in an iframe, and the docked ask bar's interim tap
   target was "switch to the Today tab." Step 2 replaced Today's content, so that's no longer
   where the chat lives — see Step 2's note for what the ask bar does now.

2. **Today** — heading, dinner card, chores, grocery summary. No needs-you band yet.

   **✅ Done.** Today's panel in `shell.js` is real now, not the Step-1 `index.html` iframe:
   derived heading (always "You're clear" for now — needsYouCount is hardcoded to 0 until Step 5
   builds the band, but the heading logic itself already branches on 1 vs n same as it will
   then), the plum tonight's-dinner card, a real chores card, and a real grocery summary card.

   **New backend surface (deviation from README §10's "no new endpoint" line):** there was no
   read endpoint for chores at all — `list_chores()` only existed as a chat-agent tool. Added
   `GET /api/chores/today` and `POST /api/chores/{id}/status`, following the exact pattern
   `/api/cooker/check-meal` etc. already use (a thin route wrapping a `tools.py` function, no
   chat round-trip). Flagged to you before building it; you confirmed adding it was fine.

   **Data sources, all existing endpoints except the two above:** dinner card reads
   `/api/cooker-view` and finds today's `slot: "dinner"` entry; "Cook mode" switches to the
   Kitchen tab (real navigation, not a stub); minutes shown = prep + cook time from the recipe.
   Grocery summary reads `/api/grocery-list?status=needed` and counts items across sections.

   **Known gaps/simplifications, called out rather than silently glossed:**
   - No signed-in-member concept exists, so "Your chores" is actually household-wide chores due
     today, not truly *my* chores — `get_chores_due_today()`'s docstring flags this too.
   - The mock's "n items · needed before Thursday" subtitle assumes a due-date on grocery items
     that doesn't exist in this schema — the summary shows "n items to get" / "All picked up"
     instead of inventing a date.
   - If there's no dinner planned for tonight (or the household is on a component-based plan,
     which has no per-day dinner), the dinner card just doesn't render, rather than showing
     something broken. The real "decide now" affordance for an empty slot is the needs-you band
     — Step 5.
   - Ask sheet still doesn't exist (Step 3). The docked ask bar and the dinner card's "Swap"
     button both now do a real (non-shell) navigation to `/static/index.html` — the old chat
     page still works end to end there. Added a one-line "← Back to Home Manager" link at the
     top of `index.html` so that's not a dead end — the only internals touch made to any of the
     four legacy pages so far, and only because Today no longer embeds this one.

   Sandbox-verified against a fresh seeded household: chores card renders real seeded chores,
   optimistic check/uncheck persists (and rolls back on a failed request), "x of y" and the
   grocery count are both genuinely derived (checked against empty/all-done/no-dinner-planned
   states, not just the happy path), Cook mode really switches tabs, and the ask-bar/Swap escape
   hatch round-trips to chat and back via the new link.

3. **Ask sheet + action cards** — chat moves off the home screen and into the sheet on every route.

   **✅ Done.** `#ask-scrim` + `#ask-sheet` in `shell.html`, styled/behaviored in `shell.css`/
   `shell.js`: scrim-to-dismiss, grab-handle, `sheetUp` animation, plum/cream bubbles, suggestion
   chips, and a composer — all matching §4's spec values. The docked ask bar and the dinner
   card's "Swap" button (Step 2's interim escape hatches to `/static/index.html`) now open this
   sheet instead — `Swap` pre-fills "Swap tonight for something faster" as specified. Ported the
   markdown-lite renderer (bold/bullets/tables) and loading-phrase picker from `index.html` so
   replies look and feel the same; all seven original quick-action chips carried over too (§8:
   preserve existing behavior). `index.html` itself is untouched and still works standalone if
   visited directly, but nothing in the shell links to it anymore — the sheet fully replaces it.

   **Action cards, the actual "no dead end" mechanism:** `POST /api/chat` now also returns
   `actions: [{kicker, change, tab|href}]` (`app/main.py`) — built by diffing the conversation
   before/after `run_agent_turn` for which tools the agent actually called (successful, non-`get_
   `/`list_` ones only), categorizing each into one of the four shell tabs or, for household/
   member/preference tools with no tab of their own yet, a real `/memory` href — never a made-up
   destination that wouldn't actually reflect the change. Change text is pulled from the tool
   call's own arguments (`item`/`name`/`chore`/etc. — usually the readable part; results are
   often just ids) with a verb guessed from the tool name's prefix, e.g. `add_grocery_item` →
   "Added milk"; anything not cleanly extractable falls back to the category kicker itself rather
   than showing nothing. Multiple tool calls hitting the same area in one turn collapse to a
   single card (last one wins); different areas each get their own card. This is a genuinely new
   piece of backend logic, not a spec-mandated endpoint, so it's called out rather than assumed.

   **Known gap:** voice dictation (the mic button) was not ported into the sheet's composer —
   `index.html` still has it standalone. Typing works fully in the sheet; carrying dictation over
   is a reasonable follow-up but wasn't part of "move chat into the sheet."

   Sandbox-verified: `summarize_chat_actions` unit-tested directly (single action, multi-tool
   dedup-by-category, error/read-only tool calls correctly excluded, the `/memory` href path) —
   this logic has no UI, so it needed testing on its own rather than only by eyeballing a screen.
   In-browser (with `/api/chat` mocked, since this sandbox has no real Anthropic key): sheet
   opens from the ask bar and from Swap (with the prefill), closes on scrim/handle tap, a reply
   with a markdown table and an action card both render correctly, clicking the action card
   closes the sheet and switches to the tab it named, and quick-action chips send their message.
   Checked at both the mobile and desktop breakpoints.

4. **Menu** — the three-slot data model, then the mobile menu, then the desktop grid.

   **✅ Done.** New backend: `tools.get_week_menu()` + `GET /api/week-menu` — this is the one new
   endpoint README §10 pre-authorizes ("no new endpoint other than the three-slot meal plan").
   Always returns exactly 7 days anchored at the current (most recently created) plan's
   `week_start_date` — same "current plan" convention `get_weekly_plan()` already uses, so this
   doesn't invent an independent Monday/Sunday calendar-week concept. Each day has
   `breakfast`/`lunch`/`dinner`, each `null` or `{title, meta, source}`.

   **Judgment calls (no spec'd mapping exists for these — documented in the function's docstring
   too):**
   - `source`/`meta` are derived from the entry's freeform text: "leftover(s)" → `source:
     "leftovers"`, `meta: "reheat"`; "takeout"/"take-out"/"delivery"/"order in" → `source:
     "takeout"`, `meta: "takeout"`; anything else → `source: "plan"`, `meta` = the recipe's
     `prep_time_minutes + cook_time_minutes` as `"N min"` when both are known on the linked
     recipe, else `null` (no invented number).
   - Component-based plans have no real per-day assignment underneath (same as
     `get_weekly_plan`'s existing `menu_is_suggested` mechanism) — this function fills the 7 days
     from that same suggested spread, `source: "plan"` / `meta: null` throughout, and passes
     `menu_is_suggested` through so the UI can note it's an example arrangement.
   - No plan yet → `week_start_date: null`, `days: []` (household name still included so the empty
     state can still show "The Dalphy House" / "No meal plan yet").

   Frontend: `shell.js`'s Week tab is real now — mobile renders the paper header card + seven day
   cards (dotted-leader course rows, italic urgent "Choose a {slot}" / "Pick" for an empty slot,
   tappable → Today); desktop (`>=1100px`, its own breakpoint per §5, distinct from the shell's
   1024px rail breakpoint) renders one paper sheet as a 7-column × 3-row grid with a course-label
   gutter and a tinted today's column. Both are built from one `/api/week-menu` fetch and switched
   purely by CSS (`.week-days` / `.week-grid`), so there's no JS-side breakpoint branching to keep
   in sync. `flex-shrink:0` added on the day cards per §8's warning (they use `overflow:hidden`).

   **More judgment calls, on the UI side (no prioritisation/change-tracking exists yet — that's
   Step 5/6):**
   - Day status badge: `Tonight` for today, `Served` for a past day, `Needs you` for a future day
     with any empty slot, otherwise no badge shown. The spec's fourth status, `Updated`, needs
     "changed in last session" tracking this step doesn't build.
   - Ribbon: plum for today, `--urgent` for a future/today day with an empty slot, otherwise
     transparent. `--good` ("just changed") isn't derivable yet either.
   - A **past** day's empty slot is shown as plain "Not planned" (`--faded`, not tappable) rather
     than urgent/"Pick" — there's no decision left to make about a day that already happened.
   - Empty-slot copy is the generic "Choose a {slot}" — the mock's "...— tee-ball night" reason
     needs a calendar/event signal this app doesn't have.
   - The header's derived status line counts remaining empty slots from today forward ("2 meals
     still need a decision" / "Your week is set.") rather than surfacing one specific real-world
     conflict, for the same reason.

   Sandbox-verified: `get_week_menu()` exercised directly against a seeded DB for all three real
   source categories (a saved recipe → `"plan"` with real minutes, a freeform "Leftover chili" →
   `"leftovers"`/`"reheat"`, a freeform "Takeout from Nonna Pizza" → `"takeout"`/`"takeout"`), the
   no-plan-yet case, and a component-based plan (7-day spread, `menu_is_suggested: true`) — then
   the same cases again through the real HTTP endpoint. In-browser via Playwright at both
   breakpoints: 7 day cards / 7-column grid render from real data, the empty-week state, a
   fully-planned week ("Your week is set.", no urgent rows), a past day showing "Served" +
   "Not planned" instead of an urgent pick, the 1100px→mobile-stack fallback confirmed still
   collapsed at 1024px, and clicking a "Pick" row (both the day-card and grid variants) correctly
   switches to Today.

5. **Needs-you band** — last, because it needs the prioritisation rules (what counts as needing a
   decision, and in what order). Start with two hardcoded rules: an empty dinner slot within 48
   hours, and a shop run whose items are needed before the next planned meal.

   **✅ Done.** New backend surface (deviation from §10, same category as Step 2's chores
   endpoints — flagged to you before building, approved): `tools.get_needs_you_items()` /
   `GET /api/needs-you`, and `tools.resolve_needs_you_dinner()` / `POST /api/needs-you/dinner`.

   **The two rules, as actually implemented (judgment calls documented in the functions'
   docstrings too):**
   - **Dinner decision:** the soonest of tonight's/tomorrow's dinner slots that's still empty.
     Comes with up to two quick-recipe suggestions (excludes disliked/temporarily-excluded
     recipes, ordered by known prep+cook time ascending) so the card's "Pick" rows have something
     real to resolve to — the whole card is skipped if there isn't even one recipe saved yet,
     since a decision card with nothing to pick from is worse than no card. At most one dinner
     card at a time (the soonest empty slot), not one per empty day.
   - **Shop run:** there are ungathered grocery items (`status='needed'`, not excluded from the
     list) *and* something's actually planned in the next 48 hours that hasn't been cooked yet.
     There's no ingredient-to-grocery-item link in this schema to check "these specific items
     block that specific meal," so this is a proxy — "you have a shop to do, and something's
     coming up soon" — rather than a precise per-ingredient match.

   **Resolving a dinner decision** calls the existing `plan_meal()`, attached to the household's
   current weekly plan (if one exists) so it also shows up correctly in the Week tab. The endpoint
   returns the refreshed needs-you list so the card can just re-render from the response, per §6's
   "cascades must be visible."

   **Frontend:** `shell.js`'s Today panel now has a real needs-you band between the heading and
   the dinner card — 0–2 cards for now (0–3 is the spec's headroom for future rules), dinner-
   decision cards with tappable inline option rows, shop-run cards with `Shop now` (→ Grocery) /
   `Later`. The H1 count and the Today tab's badge (mobile tab bar *and* desktop rail pill — both
   already had the CSS hooks from Step 1, just never driven by real data) are now derived from
   this same list instead of hardcoded to 0. Added the shared toast component §6 describes
   (`#toast` in `shell.html`), shown when a dinner decision resolves.

   **`Later`'s judgment call:** dismisses the shop-run card "until tomorrow evening" via
   `localStorage` (a fixed ~6pm the next day) rather than a backend write — there's no
   signed-in-member/session concept in this app to hang a server-side per-household dismissal on,
   and it's genuinely ephemeral UI state, so a same-device local dismissal is the reasonable
   scope rather than a wasted backend round-trip.

   Sandbox-verified against a fresh seeded household: `get_needs_you_items()`/
   `resolve_needs_you_dinner()` exercised directly (empty state with no recipes/groceries, dinner
   suggestions ordered correctly by time, resolving today's dinner correctly surfaces tomorrow's
   as the next candidate, shop-run only appearing when both conditions hold) — then the same
   through the real HTTP endpoints. In-browser via Playwright: both cards render together with the
   right urgency colors/kickers, picking a dinner option shows the toast, dismisses that card, and
   correctly reveals the next real needs-you state (verified this reflects real changing data, not
   a static mock); "Later" dismisses the shop-run card and the dismissal survives a full page
   reload; the mobile tab badge and desktop rail pill both update to the live count; the "You're
   clear" zero-state renders correctly once everything's resolved.

6. **Desktop** — rail + Today three-column + menu grid.

   **✅ Done — final step.** The rail (Step 1) and menu grid (Step 4) were already built in earlier
   steps; this step is Today's real desktop layout, the one piece of §7 not yet done.

   **Today, 3-column grid:** `shell.css`'s `.today-body` switches from a mobile flex-column stack
   to a CSS `grid-template-areas` layout at `>=1024px` — dinner card full-width on its own row,
   then a `1.5fr/1fr/1fr` row of needs-you / chores / Ask. Same DOM on both breakpoints (no
   duplicated markup, unlike Week's mobile-stack/desktop-grid split in Step 4) — only the CSS
   grid-area assignment changes, so there's nothing to keep in sync between breakpoints. Needs-you
   cards get `border-left:5px` instead of a top ribbon at this width, with `Shop now`/`Later`
   right-aligned rather than stretched full-width, per §7.

   **Judgment call:** the spec's 3-column row only names needs-you / chores / Ask — no column for
   the grocery-summary card. Dropping it outright would lose real functionality with nothing to
   replace it, so it stays stacked under Chores in that same middle column instead of being cut.

   **The Ask column — the substantial new piece:** §7 says "the ask sheet only exists below
   1024px; above it, the ask column replaces it" as a *permanent* third column (message list +
   composer pinned to the bottom), not a sheet that opens/closes. Rather than building a second,
   separate chat implementation, `shell.js` now renders the *same* conversation into whichever of
   the two message-list elements currently exist in the DOM (`#ask-messages` for the sheet,
   `#today-ask-messages` for the column) — `addAskMessage`/`sendAskMessage` write into all mounted
   targets at once, so resizing across the breakpoint never leaves one stale or empty. `openAskSheet`
   (used by the docked ask bar and the dinner card's `Swap` button) is breakpoint-aware: below
   1024px it opens the sheet as before; at/above it, it just focuses (and pre-fills) the column's
   composer, since there's nothing to "open." The docked ask bar itself (`#ask-bar-dock`) is hidden
   at `>=1024px` — there's no sheet left for it to open, and Today's Ask column is the replacement
   per spec. Quick-action chips and the markdown-lite renderer are shared as-is between both
   surfaces (same `.ask-msg`/`.ask-chip`/`.ask-action-card` classes, no desktop-specific styling
   needed beyond sizing the column itself).

   **Known gap, called out rather than silently accepted:** §7 also specs Grocery/Kitchen as
   "two-column card grids inside the same rail" at this breakpoint. Their pages
   (`static/grocery.html`/`cooker.html`) are still embedded unmodified via `<iframe>` per Step 1's
   zero-changes-to-legacy-internals rule, and their own CSS is single-column at any width — so
   today they render as a narrow centered column inside the wider desktop frame rather than a
   two-column grid. Fixing this means editing those pages' own internals, which is outside what
   this redesign pass has touched anywhere else; flagging it here rather than doing it silently or
   pretending it's done. A reasonable follow-up if you want that gap closed.

   Sandbox-verified against a fresh seeded household with a real weekly plan attached: the 3-column
   grid renders correctly with the dinner card spanning full width and needs-you/chores/Ask in the
   row below; needs-you cards show the border-left treatment and right-aligned shop-run controls at
   this breakpoint; sending a message through the desktop Ask column round-trips to a mocked
   `/api/chat` and renders the reply, a markdown table, an action card (with working tab
   navigation), and the quick-action chips exactly as the mobile sheet does; `Swap` on the dinner
   card correctly prefills and focuses the desktop column instead of trying to open the (hidden)
   sheet; the docked ask bar is confirmed hidden at `>=1024px` and still works normally below it;
   switching among all four tabs at both breakpoints confirmed no page reload and no regressions
   (Today's mobile stack, Week's grid/day-cards, and the needs-you band from Step 5 all still work
   exactly as before). Screenshotted Grocery/Kitchen/Week at desktop width to confirm the known gap
   above and that nothing else broke.

This closes the build order in §9 — all six steps are done.

## 9a. Post-launch fixes (found after Step 6, outside the numbered build order)

- **Inventory/photo-scan reachability restored.** `static/index.html`'s old nav bar linked to
  `/onboarding`, `/memory`, `/cooker`, `/inventory`, `/grocery`, and Share. The new shell only ever
  carried four of those forward (Today/Week/Grocery/Kitchen tabs) plus a desktop-only rail link to
  `/memory` — `/inventory` (and the receipt/fridge/pantry photo-scan inputs that live only on that
  page) had no path in from the new shell chrome at all, on either breakpoint. Fixed by adding a
  matching `Inventory` rail row next to `What we know` (desktop), and a small quick-links bar
  (`Inventory` / `What we know`) above the embedded Kitchen iframe for mobile, where there's no
  rail. Both are real page navigation, same as the links `index.html` always had — not new shell
  routes. A real redesigned Kitchen tab (README §4's "Running low" / "What we know about you" /
  scan-nudge cards) would be the eventual proper home for this, but that's unbuilt work outside any
  of the 6 steps; this is a lightweight restore, not that redesign. Sandbox-verified: both links
  render and resolve to `/inventory` (confirmed its 3 photo-scan `<input type=file>` elements are
  still there and functional) and `/memory` on mobile and desktop; full tab-switching regression
  re-run to confirm nothing else broke.
- **Grocery quantities merging into concatenated junk.** Recipe ingredient quantities sometimes
  carry a prep instruction after a comma (e.g. `"3, diced"`, `"4.75 cups, sliced into planks"`) —
  fine in a recipe's own ingredient list, but the grocery-quantity consolidation logic
  (`add_grocery_item`/`_try_consolidate_quantity` in `app/tools.py`) couldn't parse a quantity with
  trailing text like that, so instead of adding same-item amounts together it fell back to literally
  concatenating the raw strings — repeated across a few weeks of the same ingredient, that's how a
  line ends up reading `"3, diced + 1, diced + 1, diced + 1, diced + 1, diced"` instead of a clean
  `"7"`. Fixed at the source with `_strip_prep_descriptor()` (keeps only the amount before the first
  comma) applied before every parse/store, so grocery quantities are always in "the format you'd
  typically buy it" — a plain purchase amount, not a recipe instruction — and same-unit amounts
  merge correctly going forward. Added `repair_grocery_quantities()` (new chat tool, same
  `AskUserQuestion`-free "safe cleanup" pattern as the existing `consolidate_grocery_list`) to fix
  lines that already got mangled before this fix existed — ask "clean up my grocery quantities" (or
  point out a junk-looking line) and it re-parses and re-sums each `"+"`-joined segment. It's
  idempotent and leaves genuinely-incompatible-unit lines (e.g. `"2 cups flour + 1 lb flour"`)
  exactly as add_grocery_item's own fallback would today, so it's safe to run more than once.
  Sandbox-verified: unit-tested the exact two examples from your screenshot (tomatoes → `"7"`,
  strawberries → `"6 cups"`) via `repair_grocery_quantities()` directly, confirmed a second run is a
  no-op, confirmed fresh multi-add chains for both patterns now merge cleanly from the start instead
  of concatenating, and confirmed ordinary quantities/incompatible-unit lines are left untouched —
  then the same through the live `/api/grocery-list` endpoint.
- **Swapping a planned meal now removes its old ingredients, not just adds the new ones.**
  `swap_meal_in_plan`/`swap_component_in_plan` (`app/tools.py`) used to delete the old
  `meal_plan_entries` row and call `plan_meal` for the replacement, but never touched what that old
  meal had already added to the grocery list — so changing Tuesday's dinner from tacos to salmon left
  the taco ingredients sitting on the list forever, on top of salmon's. The tricky part: two different
  meals' ingredients of the same name (e.g. "chicken breast" from both a taco night and a stir-fry
  night) consolidate onto one grocery line, so there was no way to tell how much of that line to take
  back out for just the one meal being replaced. Fixed with a new ledger table,
  `meal_plan_grocery_links` (`app/schema.sql`), recording exactly what each `meal_plan_entries` row
  contributed to which grocery line, at the moment `plan_meal` adds it. Swapping a meal now looks up
  that meal's ledger rows first and reverses them (`_reverse_meal_grocery_contributions`/
  `_subtract_quantity` in `app/tools.py`) — trimming the shared line down if something else still
  needs part of it, deleting it outright if nothing does, or leaving it untouched if the amounts can't
  be safely reconciled (freeform quantities, mismatched units) — before deleting the old plan entry and
  adding the new meal's ingredients. An item already moved to `in_cart`/`purchased` is left alone
  either way, so this never yanks something out of an in-progress shopping trip. Both foreign keys on
  the new table cascade on delete, since grocery items and plan entries both get hard-deleted
  elsewhere in the app independently of this ledger. Sandbox-verified: planned two recipes that both
  needed chicken breast (2 lbs + 1 lb, consolidating to "3 lbs"), swapped the first for a new recipe,
  confirmed the list dropped to "1 lb" chicken breast (exactly the swapped-out meal's share), fully
  removed a same-meal ingredient nothing else needed (tortillas), and added the new meal's ingredients
  — then swapped the second meal too and confirmed chicken breast disappeared entirely once nothing
  needed it. Also confirmed deleting a linked grocery item directly (`remove_grocery_item`) no longer
  hits a foreign-key error now that the cascade is in place.
- **"Already have it" button added to the Grocery List's "Already have this?" review section.**
  That section (powered by `get_grocery_already_have_items`) flags items on the list that inventory
  suggests are already on hand, but only ever offered "Keep it, I need it" or "Remove from list" —
  no way to confirm the match and actually get it into tracked inventory in one step, unlike the
  "Have it" action already available on every normal list row. Added a third button reusing that same
  `/api/grocery-list/{id}/already-have` endpoint (`tools.move_grocery_item_to_inventory` — merges into
  matching inventory, then removes from the grocery list), so triaging this section can resolve an
  item straight to inventory without leaving the review flow. Sandbox-verified via Playwright:
  flagged an item with matching inventory, clicked the new button, confirmed it disappeared from the
  review section and the grocery list, and landed merged into inventory ("1 bag" + "1 bag" →
  "2 bag").

## 10. Out of scope

Household/other-people's chores, notifications, sharing beyond the existing share page,
fridge-photo scanning (the nudge is copy only), and any new endpoint other than the three-slot
meal plan.
