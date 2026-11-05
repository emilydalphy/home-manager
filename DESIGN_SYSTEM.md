# Pomona Design System: Bright Dusk

For agents. Read this before touching anything visual. Emily approved Bright Dusk on 2026-11-05, and it replaces The Inn.

Sources of truth, in priority order:

1. The boards in `design_handoff_bright_dusk/` (`Z-*.dc.html`, the approved screens; their inline styles give exact values). `README.md` there holds the binding decisions, `N-Rules.dc.html` the rules, `R-Palette.dc.html` the colours, and `playable-prototype.html` the motion and tap behaviour.
2. `static/theme.css`, the code.
3. This document.

If they disagree, trust the boards and flag the drift. Everything below is written as a rule you can check, not a vibe.

**The rebuild runs in stages.** Plan, the tab bar, the toast, the head and the shared components are Bright Dusk now. Shop, Kitchen, Cook, Recipe, Settings, Ask, the welcome and sign-in ride on the new tokens but keep their old layout until their stage. Where old code disagrees with this document on those screens, this document wins and the old code is on its way out.

**The look changes, the flows don't.** If a behaviour would have to change to fit a screen, leave the behaviour alone and note it.

---

## 1. Tokens

All in `static/theme.css`. New work uses the canonical names below and nothing else.

### The palette

| Token | Value | Name | Job |
|---|---|---|---|
| `--cream` | `#FFFAF3` | Warm Cream | The page. |
| `--white` | `#FFFFFF` | White | Tiles and cards. |
| `--dusk` | `#1B4392` | Dusk Blue | The tab bar, Use soon, timers, the welcome. Nothing else. |
| `--night-ink` | `#14264A` | Night Ink | Main text, the toast. |
| `--apricot` | `#FFA45C` | Ripe Apricot | The one main button and Ask. Always dark text. |
| `--soft-apricot` | `#FFC08F` | Soft Apricot | Small labels on dusk, Undo on the toast. |
| `--blush` | `#FBE9D9` | Blush | Quiet tints: the switcher's track, round buttons, quiet buttons. |
| `--slate` | `#55607A` | Slate | Second lines and small details. |
| `--clay` | `#A8461A` | Baked Clay | Today on the day bar, and problems. |
| `--on-dusk` | `#FFF6EA` | | Text on dusk and on the toast. |
| `--on-dusk-soft` | `#C6D2E8` | | The tab bar's resting icons and labels. |
| `--line` | `#F4ECE2` | | The thin line between rows. |
| `--grab` | `#C9CEDA` | | A sheet's grab handle. |
| `--tick-ring` | `#8791A8` | | An unticked box's ring. |

Green stays in the food. No teal, no pale blue. Purple never leads.

### Roles

`--ground` (the page), `--surface` and `--surface-raised` (a tile), `--ink` (text), `--ink-secondary` (second lines), `--hairline` (a thin line), `--field` (the room the phone sits in at 768px and up), `--scrim`, `--toast-bg`, `--toast-ink`, `--plate-shadow`.

### Legacy names

`theme.css` still defines The Inn's names (`--spruce`, `--celadon-tint`, `--sand`, `--apricot-label` and so on) in a marked LEGACY block, forwarding to Bright Dusk, because shell.css still says them about 3,000 times. A screen moves to the canonical names when its stage rebuilds it, and the block goes when the last stage is done. **Never add a new use of a legacy name.**

### Light mode only

Emily, 2026-11-05: light mode only for now, dark mode comes later. Settings → Appearance is gone. Every page's `<html>` carries `data-theme="light"`, which switches off every dark rule, because they are all guarded with `:root:not([data-theme="light"])`. The dark blocks are left in `theme.css` and `shell.css`, dormant, with The Inn's values. To bring dark back: give them Bright Dusk values, then take the attribute off the pages. `tests/test_appearance.py` keeps the guards honest.

---

## 2. Hard rules

Each one is checkable.

1. **Dark text on apricot, always.** Night Ink on `--apricot` is 7.62:1. Nothing light ever sits on it, icon strokes included.
2. **Purple never leads.** It is never a primary surface, focal point or brand colour.
3. **`--clay` means a problem or today, nothing else.** It is not a second apricot for "something to look at".
4. **Dinner is never highlighted.** Every meal is an equal tile. There is no Tonight card and no dark hero on a meal.
5. **One main apricot button per screen.** Ask in the tab bar is always apricot too, so the bar never changes. If a screen wants a second, the other one is a quiet blush button or plain words.
6. **Nothing tappable is under 44×44px**, text links included.
7. **Icons are inline stroke SVG, never emoji.** `stroke="currentColor"`, 2.2px, round caps and joins. Two exceptions, both checked by `tests/test_design_hygiene.py`: the tab bar's four icons are 1.9, as drawn on the boards, and the Pomona mark, a logo, keeps the weight it was drawn at.
8. **Contrast is measured, never eyeballed.** Every pair below is measured; a new pair gets measured the same way and recorded here. `tests/test_contrast.py` fails a palette change that takes a text colour under 4.5:1.
9. **Every colour goes through a token.** A literal hex in a diff, outside `theme.css`, is a review failure. The meta theme-color tags and the manifest are the exception: they can't read a CSS variable.
10. **No outlines.** No beige rims around cards and chips. Cards take the one soft shadow; thin lines are only between rows. A selected state may still draw a rim.

Measured pairs (WCAG 2.1):

| Text | On | Ratio |
|---|---|---|
| Night Ink | cream / white / blush / apricot | 14.38 / 14.94 / 12.63 / 7.62 |
| Slate | cream / white / blush | 6.05 / 6.29 / 5.32 |
| Clay | cream / white / blush | 5.69 / 5.91 / 4.99 |
| `--on-dusk`, white | dusk | 8.67, 9.27 |
| `--on-dusk-soft` | dusk | 6.09 |
| Soft Apricot | dusk / Night Ink | 5.82 / 9.38 |
| `--on-dusk` | Night Ink / clay | 13.96 / 5.52 |
| White | the current tab's pill on dusk | 6.09 |

---

## 2b. Screen rules — from Emily's review of the screen-by-screen mockups (2026-09-11)

*Added by Emily's decision on 2026-09-11 (Tier 2). Each rule came out of a "why" she
gave while reviewing the [Pomona, Screen by Screen](https://claude.ai/code/artifact/d3121bd8-e1f9-4430-82b8-8aaf4031e657)
canvas — several notes pointing at the same reason, written up as one rule. S1, S4 and S9
sharpen things §8 already says; the rest are new. Like §2, each carries a test a reviewer can
run rather than a vibe. They apply to every screen, mockup or built.*

- **S1 · Say the purpose, not the explanation.** A screen states what it is for in its
  title and one line; it never explains itself in a paragraph. *Why (Emily): "what you
  want to say on the screen should be more clear and direct for the purpose of it so that's
  not lost."* **Test:** read only the title and the first line — is the purpose obvious?
- **S2 · Helpers appear when you reach for them.** Prompts, tips and the chat itself show
  up when you tap for them, never as permanent furniture on a working screen. *Why: "the
  prompts at the bottom are taking up too much room"; "the chat being an open bar the whole
  time is taking up too much room and is confusing."* **Test:** on a root screen, is anything
  visible that only helps once you have already decided to act?
- **S3 · Emphasis is a nudge, not a takeover.** The important thing on a screen is the same
  size as its neighbours and is called out by a tint and a label, not by being a poster.
  *Why: "the next-up card pulls too much attention… making the screen more even, but a bit
  called out."* **Test:** does any one block take more than a third of the screen?
- **S4 · Every question changes the plan.** Setup and weekly intake ask only what the
  planner acts on. If nothing in the code reads an answer, the question goes. *Why: "make
  sure whatever information we're gathering upfront actually contributes to the quality of
  the responses."* **Test:** for each question, name the code path (or the prompt rule)
  that uses the answer. (On 2026-09-11 this test found that lunch location, meals-together,
  cooking role and planning anchor are stored and shown but never acted on; only
  `dinner_window`, `leftovers_stance`, `prep_days` and the intake's packed-lunch days are.)
- **S5 · Finishes get their own screen.** The moments where something is done — week
  approved, shopping done, dinner on the table — break the template: big type, one
  number, one next step. *Why: "it's boring to look at the same screen types all the
  time."* **Test:** can you tell you finished without reading?
- **S6 · A highlight says why.** Any tint, badge or colour on a row carries the word for it
  ("Today", "Draft", "Hosting · 5"). *Why: "why is one part highlighting in blue?"*
  **Test:** cover the colour — does the word still tell you?
- **S7 · Real life has a home.** If a household can live it — hosting five for lunch, one
  person out, a night already decided — there is a tap for it where the plan is made, not
  only in the chat. *Why: "I am hosting 5 people for lunch, but there wasn't an option to
  communicate that anywhere."* **Test:** for each intake tag, which real situation is it
  for — and for each situation a tester has named, which tag?
- **S8 · Details are one tap in and one tap back.** Anything you might want to check before
  deciding (a recipe, a day) opens from where you are and returns you there. *Why: "click
  into each recipe… but it needs to also be able to loop back to the main review screen."*
  **Test:** from any detail screen, is the crumb the screen you came from? (This is §6's
  "one way back" rule, applied to details rather than steps.)
- **S9 · Warm and plain, never trying.** No winks, no self-aware asides, no "a quick word".
  If a line sounds like it wants you to like it, cut it. *Why: "this cringy trying-too-hard
  way of writing"; "it's just fluff."* **Test:** would you say it across the kitchen table
  without wincing?
- **S10 · A decision is saved on purpose, and the app says so.** *(Emily, 2026-09-13,
  Tier 2 — her instruction: "if there is a decision that the user makes, it needs to make
  it saved, and then a little pop up should show up saying changes saved.")* Where you
  choose between options, there is a Save (or Done) — the choice is not made by the tap
  that highlights it. When it is saved, the pop-up **names what changed** — the house toast
  pattern, `<thing> was <verbed>`: "Thursday and Friday were swapped" — and an add leads with
  the verb, "Added Carrots", so no was/were is guessed from a food's name (Emily, 2026-10-31:
  "Lemons was added" read wrong; "Swiss chard" and "hummus" defeat any plural guess) —
  with **Undo** on it when the change can be undone. A single tick (a grocery line, a thaw
  move, a chore) is its own decision and still gets the pop-up. A failed save keeps saying so
  in the calm-in-trouble voice (§8). *(**The words changed 2026-09-23**, from Emily's
  correction of 2026-09-22 — "Changes saved" → "Carrots was added" (now "Added Carrots", 2026-10-31), "Put back" → "Undo"
  ("I don't like the AI written style"); see §7 and `.claude/skills/pomona-copywriter/SKILL.md`
  rules 2 and 3. The rule itself is unchanged and still hers from 2026-09-13: a Save, a
  pop-up, an Undo. **The code has not caught up** — `CHANGES_SAVED` / `toastSaved()` in
  `shell.js` still says "Changes saved" at 23 call sites, `plan-week.html` says the
  same words, and "Put back" is still the undo's word in a run of toasts across Shop, Plan
  and Today. That is drift to be fixed — findings 1, 2, 3, 17, 23 and 24 of
  `COPY_SWEEP_2026-09-23.md` — not the pattern to copy, so don't write a new caller of the
  old wording.)* **Test:** after any decision, can you point at the
  button you pressed to make it, and the line that told you it took? First pass landed
  2026-09-13 on branch `worktree-changes-saved` (Swap · I'll pick, chat changes, What we
  know, chore ticks and undo, Cook's prep/attention/usage/un-cook, Plan the week's
  attendance, holiday and away saves). Still open: a Save button under the open-slot
  options (they apply on tap today). Start cooking records the real start since 2026-09-13
  (branch `worktree-real-start-time`) and says so once, only when the clock moved.
- **S11 · Quiet rows: one button stands out, the rest is one tap in.** *(Emily,
  2026-10-30, approving the "Combined" Plan, Shop and Today mockups on the Less clutter
  canvas.)* A screen has one button that stands out (its dock's primary); a row carries
  its name, one short line and, where it opens something, a chevron — never a row of
  buttons. Everything a row can do is in the sheet tapping it opens (`openQuietSheet` in
  `shell.js`; Plan's Swap / Tweak / Move / See the recipe, Shop's store, amount and
  answers, Today's who's-on-it and action). Ticking stays on the row: a tick is the one
  thing a list row is for. And a leftover meal's row says just **"Leftovers"** — never
  "Leftovers from Thursday's dinner" (standing copy rule, everywhere). **Test:** cover
  the dock — is anything left on the screen that looks like a button on every row?
- **S12 · Seamless: never send someone to go find something.** *(Emily, 2026-10-26,
  Tier 2 — "Everything with design should be thinking about making a seamless
  experience.")* When a screen points at something somewhere else — "from Monday", "made
  Sunday", "on the Shop list", "in Settings" — the pointer is the way there: one tap takes
  you to that exact thing (the day, the recipe, the row), and back (S8) returns you where you
  were. Better still, bring what you need here, so the trip isn't needed: a made-ahead
  breakfast shows what to do with it this morning on the day it's eaten, not only on the day
  it was cooked. A pointer with no tap behind it is a dead end, and a dead end is a bug.
  *Why (Emily): clicking into a cook day that "points me to go find the other day where the
  recipe lives is not a good experience… I envision maybe there's a button on that part of the
  screen I can push to even just bring me there."* **Test:** for every line on a screen that
  names another day, screen or list, tap it — does it land on that exact thing? And: could
  the person finish what they came to do without leaving this screen?
- **S13 · The way you answer matches the question.** *(Emily, 2026-10-31, phone test of
  the planning quick check — "it's confusing cause it's asking 'what do you have left'
  then asking to tap what's gone. It should tap what's still left, more intuitive with the
  question. Make a rule to make it so that the response type should be clear to the
  question being asked.")* A question about what you have is answered by tapping what you
  have; a question about what's gone is answered by tapping what's gone. Never ask one
  thing and collect its opposite — the thing the tap marks is the thing the heading names.
  Chips that start on and switch off are fine only when the heading names what "on" means
  ("Use these up", all on, tap one off). First fixed 2026-10-31: Plan the week's "Still
  have these?" now has you tap what's still there (untapped = gone), and the Kitchen
  freezer's tap-what's-gone card asks "Any of these gone?". **Test:** read the heading,
  then tap one thing — does the tap say "yes" to the heading's question, or "no"?

---

## 3. Type

One face: **Figtree**, self-hosted from `static/fonts/` so the iOS app works offline and no page calls Google. Six sizes and no others:

| Size | Line | Weight | Job |
|---|---|---|---|
| 32 | 38 | 700, ‑0.03em | Screen title. |
| 26 | 30 | 700, ‑0.025em | A sheet's title, a feature. |
| 16 | 20–22 | 500 for a row or tile name, 700 for a section heading or button | Rows. |
| 14 | 20 | 500 | Support: the line under a title, the toast. |
| 13 | 18 | 400 or 600 | Small: the line in a tile, labels. |
| 11 | 13 | 500, 700 when current | Tiny: tab labels, the day bar's weekday. |

Weights are 400, 500, 600 and 700. No uppercase eyebrows, no tracking under 26px, no italics. `--font-display` and `--font-accent` are legacy names for the same face. Inputs are 16px, so iOS doesn't zoom the page.

---

## 4. Shape and space

- **Four corner sizes:** 13 controls (`--radius-control`), 16 buttons (`--radius-button`), 22 tiles (`--radius-card`), 26 sheets (`--radius-sheet`). Round buttons and pills are fully round (`--radius-pill`).
- **One card shadow:** `--shadow-card`, `0 1px 2px rgba(20,38,74,.05), 0 10px 28px rgba(20,38,74,.07)`. The switcher's chosen segment has `--shadow-seg`. Dusk cards and the toast have `--shadow-float`.
- **Plates on cream and white** get `--plate-shadow`, from the app, not the drawing. On dusk, none.
- **Space:** a 20px gutter for words, 16px for tiles, 10px between tiles, 14px above the first thing under a head, 18px above a section heading and 6px under it.
- **Motion**: small, quick motion on every press, tab change, sheet and tick, and always off for people who turn motion off (`prefers-reduced-motion`, which also zeroes the tokens). A press scales to .98 over `--motion-press` (120ms). Sheets slide up over `--motion-base` (240ms) on a scrim that fades in, through `openSheet()`/`closeSheet()` in `shell.js`. Tab panels crossfade (`activateTab`). A tick eases to done. Toasts rise in. On Shop, a row on Shop's "Sort them all" screen collapses out when it gets a store (`groSortAllLeave`), and a finished store rolls up (`groRollUp`). Nothing moves to get attention.

---

## 5. Components

One of each, used the same way on every screen. The CSS is in the SHARED COMPONENTS part of `theme.css`, so a page without `shell.js` can use the classes. The HTML builders are the "Bright Dusk parts" near the top of `shell.js`. A later screen reuses these and does not build its own.

| Component | CSS | Builder | Notes |
|---|---|---|---|
| Screen title | `.screen-head`, `.screen-title`, `.screen-sub`, `.screen-tools` | `rootBandHtml(opts)`, `setRootBand` | How every tab root opens. No band: a plain title and one line on cream, round buttons at the right. `opts.noGear` leaves the gear out. The name is history; its ids are what `setRootBand` writes to. Sub-screens have a round Back button above the title (Z-Prep, Z-Settings). |
| Round header button | `.round-btn` | `roundBtnHtml({ label, icon, attrs })` | 44px, blush. Back, More ···, close, +. |
| Segmented switcher | `.seg`, `.seg-btn`, `.seg-days` | `segHtml(items, { days })` | Blush track, the chosen segment white. 46px, or 52px with a weekday and date. Today's segment is clay. |
| Section heading | `.section-head` | `sectionHeadHtml(text)` | 16/700 over a group of tiles. |
| Tile | `.tiles`, `.tile`, `.tile-row`, `.tile-text`, `.tile-plates`, `.tile-name`, `.tile-sub` | `tileHtml({ attrs, art, name, sub, kind, cls, extra, label })` | The base of the whole app: two across, one thing each, with a plate where there is a dish. With `attrs` it is a button, without them a thing to read. `kind: 'row'` is the 68px small tile (snacks), `'text'` words only (a menu). No art means a text tile: no filler pictures. `.is-wide` spans both columns, `.is-quiet` is the slate name. |
| Note card | `.note-card` | | One plain line, and one button when there is a next step. Empty and error states (Z-EmptyPlan, Z-Error). No icons. |
| Main button | `.btn-main`, `.btn-quiet` | | 52px, radius 16. Apricot with dark text, one per screen; blush for the quiet one. `.btn-primary`, `.dock-primary` and `.btn-gold` are older names that look the same. |
| Field | `.field` | | 52px, white, the card shadow. |
| Tick box | `.tick-ring`, `.is-ticked` | | 22px, `--tick-ring`, an apricot circle when done. |
| Sheet | `.quiet-sheet` and the "Sheets, one look" block at the end of `shell.css` | `openQuietSheet(html, wire)`, `quietSheetHeadHtml(eyebrow, title)` | Everything extra slides up from the bottom: a grab handle, a 26px title, a round close, the content, on a dimmed screen. Tiles in a sheet are `.tiles.in-sheet`. |
| Toast | `.toast`, `.toast-action` | `showToast(message, action)` | One dark bar above the tab bar. It names the thing, and Undo sits at its right when it can be undone. It rises above a dock when there is one. |
| Tab bar | `#tab-bar`, `.tab-btn`, `.tab-chip`, `.chat-fab` | `TABS`, `TAB_ICONS` | Solid dusk on every main screen, in the page's flow. Icons and labels; the current tab in a soft pill; `#chat-fab` is Ask, the apricot pill with the word Ask at its end. It replaced the floating chat button and never covers anything. |
| Dock | `.dock` | | The screen's one action, sticky above the tab bar on a cream strip. A screen with no single action has no dock. |
| Plate | `.dish-plate`, `.dish-plate.on-dark` | `Plates.html(key, size)` | See 5b. |

### Plan (Z-Week, Z-Day, Z-PlanMenu)

`planWeekHtml` is one screen for a draft and an approved week: approving changes the chip and the dock, never the layout.

- **The head:** "This week" on All, the weekday and "Today · Nov 4" on a day (`planDayBandParts`). A draft's title is "Here's your week." with its dates, "your turn" and the Draft chip. No counts under titles.
- **The day bar** (`planDayBarHtml`): All, then one segment per day. Past days are hidden. It scrolls past seven days and into the plans either side, with All pinned.
- **All** is a menu: Breakfast, Lunch, Dinner, Snacks, each a section heading over tiles. A repeat shows once with its days ("Wed, Fri"), from `wkMenuGroups`. A night eating a batch made earlier says "Leftovers". Snacks are the small tile. A dish whose days have all gone by leaves the menu.
- **A day** is one grid in order: breakfast, lunch, dinner ("Dinner · 35 min"), then one Snacks tile with its plates and how many. One snack opens its sheet; several open a sheet to choose from.
- **Every tile opens the meal's sheet** (`openPlanRowSheet`): Swap, Tweak, Move, See the recipe.
- **··· on a day** is `openPlanDaySheet`, four tiles: Add a meal, Add from the freezer, Swap with another day, Take the night off, each only where it can do something. Each opens the flow it always opened; the night off is today's only, and goes to the chat with the words ready.
- **··· on All** is the More sheet (`openMealsMoreSheet`): re-planning, the week after, the freezer, the whole week, setup, start over, help. The boards show no buttons on All's head and no way to Settings, so All carries ··· and the gear to keep both reachable.
- **A slot is never absent.** An open slot is a text tile, "Your call", that reveals its options across the screen. A meal nobody's home for is a quiet text tile. A meal the household asked none of isn't drawn.

### Behaviour that stays

These parts of older screens keep their behaviour through the rebuild. The stage that reaches them changes their look, not what they do.

- **Draft flag** (`weekFlagsHtml`): what the draft had to bend, one line at the top with Pomona's own fixes. Never urgent, no apricot. Every word is the server's.
- **Change card** (`mountChangeCard`): what the chat proposes, one row per slot, nothing written until Save, then a toast with Undo and **Changed** on the row for eight seconds.
- **Meal sheet** (`planRowSheetHtml`): Swap, Tweak and Move only where they can do something, then See the recipe.
- **Plate parts and Tweak** (`plateRowHtml`, `openTweakSheet`): the plate as its parts, with a Save and one quiet way out.
- **Cook's day** (`cookRootParts`): today's meals in eating order. No clock, ever.
- **Before bed** (`cookBedCardHtml`): today's fridge moves and tomorrow's prep as one checklist.
- **Empty moment** (`emptyMomentHtml`): becomes a note card at its stage.

---

## 5b. Plates

Every dish shows its plate. Plates carry the colour, so the interface stays calm. The rules for the drawings are in `plates/PLATE_RULES.md`, and they apply to every plate, including new ones. When Emily gives feedback on a plate, it becomes a rule there and is applied across the library. The test: cover the name. Could you still tell what the dish is?

In the app:

- `Plates.html(key, size)` in `static/plates.js` is the one way a screen shows one. `key` is the `plate` the API puts on a meal row or a recipe. `{ onDark: true }` drops the shadow.
- A drawing that fails to load falls back to `neutral`, then to nothing, never a broken image. A planned dish with no key shows `neutral`.
- Sizes: 96 on a tile, 62 for a pair on one tile, 44 on a small tile, 216 at the top of a recipe.

---

## 6. Navigation rules

- **Four screens, period: Plan, Shop, Kitchen, Cook. The app opens on Plan.** (Emily retired Today on 2026-11-01.) The code still spells the tab keys `week`, `grocery`, `home` and `kitchen`, on purpose. **Naming trap:** `kitchen` / `/kitchen` in code is the Cook tab, and `home` / `/home` is the Kitchen tab. Everything else is a *state* (a switcher inside a tab), a *sheet*, or a *step*, never a new page with its own header.
- **Ask is in the tab bar on every main screen, and the sheet it opens is part of the shell, not any one screen.** Its answer arrives over whatever tab you're on and never navigates you somewhere you didn't ask to go. Example chips show only on an empty chat.
- **Cooking is a *step* of Cook**, not a state of Plan and not its own tab.
- **Cook's root has one primary action: "Start cooking", in its dock** (Emily, 2026-09-13). It used to read "Cook's root has no primary action". The dock names the next meal's own action: "Make lunch", "Start cooking" for dinner, "Keep cooking", "Mark eaten", and "Start prep" on a prep day. There's no dock on a night with nothing to cook. One step down, the recipe's "Start cooking" and the cooker's "Next step" / "Done — on the table" (it read "Mark it cooked" until 2026-09-17) are each their screen's one apricot.
- **One way back, and it names its parent**. Every screen deeper than a tab root has exactly one way up, and it goes to the parent, never `history.back()`. The phone's back gesture matches it. The boards draw it as a round Back button; older steps still have the named `‹ This week` crumb until their stage. A tab root has none.
- **One dock**. The single thing a screen is for lives in a fixed strip above the tab bar, labelled for what it does, and it does not scroll away. A screen with no single action has no dock. Rare actions go behind `···`.
- **Each screen has one job, and nothing appears twice.**
- **Refresh policy**:
  - *You change something:* the screen updates on tap, before the server confirms. A failed save reverts the row and says so.
  - *One screen's change touches another:* every screen reads from the same store, so it's already correct.
  - *You switch tabs:* nothing reloads. A quiet background check folds in server changes.
  - *You return after being away a couple of minutes or more:* refetch everything once, quietly.
  - *You pull down:* pull-to-refresh exists on all four tabs, as a courtesy.
  - A new native panel or action tool gets wired into `refreshStaleTabsFromActions()` (`shell.js`). A panel built once and never told to refresh goes stale silently.

---

## 7. Learning etiquette

Pomona learns from what a household does — this section is how it's allowed to do that.

- **The loop is observe → infer → confirm → remember → correct.** The app notices a pattern, forms a guess, and checks it with one light, inline tap — never a form, and never asked twice for the same fact. Once confirmed, it remembers, and that answer stays one tap away from being corrected later. Skipping "confirm" isn't a shortcut version of this loop, it's a different one — see silent learning below.
- **Silent learning needs a visible flag and an undo, right at the point of use.** The app may act on a guess without asking first, but only where the result shows up on-screen as something the person can see and reverse in the moment — the grocery list's "usually here" / "not this time" is the reference pattern. Acting on a guess anywhere that isn't visible and reversible right there isn't silent learning, it's just guessing. *(2026-09-22, Emily's "adding or sorting once remembers" card: a store the household chose themselves — in the add sheet, on a "Sort them all" chip, from a row's ⋯ — is remembered as the item's usual without a confirm step. **Changed 2026-09-23** (Emily, "it should auto remember anyways"): the add sheet used to spell the remembering out itself, a celadon "I'll remember Costco for it" line under the chips, on top of the "Changes saved · Put back" pop-up after — she called the line unneeded, since the row landing in that store's card already shows the pick took. The line is gone; the visible flag is now just the row itself plus the add's own toast ("Added Carrots · Undo"), and Undo still forgets the store the same way Put back did. Only the add sheet's copy changed that day, and of the other two doors only one still says the old words: "Sort them all" already pops "Carrots → Costco · Undo" (`groOfferBulkUndo`), which names the thing; the row's ⋯ move still pops "Changes saved · Put back" (the `row-store` case, finding 1 of `COPY_SWEEP_2026-09-23.md`). That last one is drift left to fix, not a second pattern to copy. — *Corrected 2026-09-23: this note first said both doors kept the old words; checked against `shell.js`, "Sort them all" never did.*)*
- **The flag is the thing on screen; the words never announce the remembering.** *(2026-09-23, from Emily's correction of 2026-09-22 — "Can we make sure that copy throughout is more straight forward like this. I don't like the AI written style." The long version is `.claude/skills/pomona-copywriter/SKILL.md`, rules 2 and 3.)* The visible flag and the undo above are still required — that rule doesn't move. What changed is what they are allowed to say:
  - **The flag is the result, not a sentence about the result.** The row landing in its store card *is* the flag. "I'll remember Costco for it next time" is not one — it is me reporting my own filing, and the person meets the choice again where it's used, which is better proof than a sentence about it. The remembering stays; the announcement goes.
  - **The confirmation names the thing.** "Added Carrots", not "Changes saved" — the house toast pattern (`<thing> was <verbed>`, verb first for an add so no plural is guessed), one clause, past tense. If the app can't name what it just did or just learned, it doesn't know enough to be popping a pop-up.
  - **The undo is called Undo.** Never "Put back", never another phrase for it. One word everywhere, so reversing anything is the same tap.
  These are words, not behaviour: nothing here takes away a confirm step, a flag or an undo. **Test:** read the screen with the copy covered — can you still see that the guess was acted on, and the way back?
- **Repetition earns an offer, not a promotion.** Once something has been observed enough times to look like a rule ("that's two Thursdays — should I just assume it?"), the app asks, once, whether to make it standing. It doesn't quietly upgrade a guess into a fact on its own.

*This section and the voice-character addition just below it were folded in 2026-09-03 (both fully Emily-decided beforehand); per Governance below, the Brand Book canvas guide still needs the same two additions — and §8's "Sounding human" rules of 2026-09-10 — at its next design round. Same for §2b's screen rules of 2026-09-11, and for this section's 2026-09-23 re-cut and the new words in §2b S10.*

---

## 8. Voice

*The warmth-and-play addition below (and the do/don't pair) was Emily-decided on 2026-09-05, per Governance below.*

- Pomona's personality is **kind, dependable, helpful, thoughtful** — and understanding the user is part of being helpful, not separate from it. These four words are the test for any new copy: if a line doesn't read as at least one of them, rewrite it.
- Warm, first-person, concise — and a little playful, especially in titles (Emily, 2026-09-05). State the thing, then soften it — never the reverse, and never at length.
- Encouraging, never sarcastic or deadpan. A joke at the user's expense, or a flat/robotic aside, isn't playful — it's the opposite of kind.
- Exclamation marks and emoji are allowed, used sparingly. Rule of thumb: at most one per screen. Never in error or safety copy — that copy stays calm and plain, no exceptions (see the calm-in-trouble rule below, which still governs).
- **No fluff anywhere in the app. Every line has a job. If taking it away loses nothing, take it away** (Emily, 2026-11-05, Bright Dusk).
- **Every word earns its place — and this one is a hard rule, not a preference** (Emily, 2026-09-09: *"as a new rule don't add useless fluff text"*). Pomona doesn't add copy for the sake of tone or personality. If a line can be cut without losing meaning, cut it. Three failures to watch for, all of which shipped before she caught them:
  - **Restating the screen.** A subtitle that says in a sentence what the content below says in a list. If the reader can already see it, don't narrate it.
  - **Filling a slot.** Copy written because a template has a place for a subtitle, an accent line or a reassurance. An empty slot is a fine outcome; the italic accent line was one, mandatory until 2026-09-09 and gone with Bright Dusk.
  - **Explaining what the label already says.** A button reading "Approve and build my shopping list" needs no line under it explaining that approving builds the shopping list.
  The test: delete the line. If nothing is lost, it was never doing anything. Prefer a shorter true thing to a longer warm one — the warmth is in the accuracy.
- Never promises a feature the app doesn't do, and never pre-announces what's coming. Describe only what's true right now — enthusiasm doesn't get to write checks the product can't cash.
- **Calm and reassuring, never cheery** (Emily, 2026-09-04) — this is about trouble, not tone in general: when something's wrong, reassurance comes from showing the thing is handled — a problem is always stated plainly and paired with its way out in the same breath — never from exclamation marks or enthusiasm. Keep stakes low and reversible where true ("nothing lost," "easy to change back").
- Time as a person would say it ("on the table by a quarter past seven"), not a timestamp ("Est. ready 7:15 PM").
- The answer, not the question ("All in the fridge," not "All required ingredients available").
- Names the person when there is one ("Trash night — Jamie's turn"), because the house has people in it.
- Never a dashboard/task-manager register: no "Action required," no "Task assigned," no clinical precision standing in for a person.
- **"Passphrase," never "password."** (See `static/login.html`, `app/households.py` — already consistent; keep it that way in anything new.)
- The app is **Pomona** — user-facing copy, marketing, onboarding. (Internal identifiers — env vars, DB names, file paths, code symbols — were deliberately left alone during the rebrand; don't rename those without a separate reason.)

**Reading what they meant** (Emily, 2026-09-13, Tier 2 — "we need to incorporate the smart throughout this app"). Five behaviours the chat is held to on every turn, written into `agent.py`'s SYSTEM_PROMPT and enforced by the change card: (1) read the intent and change the smallest thing — "tacos but chicken" keeps the tacos; (2) a reason is a fact — remembered, shown, correctable in the same reply, never asked again; (3) say the consequence once, before it bites, as a fact with what was done about it, not a question; (4) offer three, never an open question — "something else" comes back as things to tap; (5) never ask which one they meant when the screen already says — the subject chip and the card carry it. These apply to every sheet that proposes something, not only the chat.

**Sounding human** (Emily, 2026-09-10, Tier 2 — confirmed after the welcome-flow copy was written to these and approved line by line; the welcome screens in `static/onboarding.html` are the reference for what they produce). Six rules that sit alongside everything above — they don't replace the four-word test, the every-word-earns-its-place rule, or calm-in-trouble:

1. **Say it like you'd say it across the kitchen table.** "My whole job is to take the mental load off you" — not "Pomona plans meals, manages groceries and tracks inventory."
2. **Talk about their life, not the app's features.** "The shopping," not "grocery list management." "The what-are-we-eating-tonight," not "meal decisions."
3. **Contractions, always.** I'm, you'll, nothing's, let's, isn't. A line without one usually reads as a manual.
4. **Short sentences. One breath each.** If you'd pause for air reading it aloud, split it.
5. **Warm words over clever ones.** "Nice to meet you" beats a pun. Playful is in the rhythm, not in jokes.
6. **Never a label that could sit on a dashboard.** No "Get started," no "Set up your profile," no "Features," no "Action required."
7. **Clear beats warm — every time** (Emily, 2026-09-13). If the reader has to work out what's being asked, the line has failed no matter how nicely it reads. Ask the plain question. Her example: the cook-ahead ask read "Roasted Chickpeas is on 5 nights. Cook ahead?" over a row of day chips and a tally — warm, short, and she couldn't tell what tapping a chip would do. What she wanted instead: *"Do you want to batch cook this? Which meals should be included?"* — the question first, then the choice, nothing decorative in between. Her words: *"prioritize being clear vs adding AI confusion and fluff into the copy."* This applies to UX structure as much as words: a question, then the control that answers it, then the buttons that commit — never a yes/no question answered by a picker whose yes/no buttons are a screen further down.

Her own edits while these were being written are the calibration: she chose "Nice to meet you" over "Lovely to meet you too"; asked the purpose line to end on "so it isn't all on you" rather than "Hand it over"; asked a set of explanatory rows to be "a bit more natural in explaining." Warm and plain wins over warm and clever, every time.

**Do / don't:**

- **Do:** "Just two more! Almost there." — warm, earns its one exclamation mark, still just states the thing.
- **Don't:** "Two more and I'll leave you alone." — reads as an apology for existing, not warmth; the old default this replaces.
- **Do (a normal-week card):** "The more I know about how your week runs, the more this plan will feel like yours. It'll get sharper as you correct me." — honest about what actually happens.
- **Don't:** "...and after this it'll need almost no correcting later." — promises an outcome the app can't guarantee; never pre-announce a result like this.

---

## 9. Governance — who decides what

This section is for Emily as much as for any agent working on Pomona. Plain terms.

First, one piece of vocabulary this section leans on: a **token** is just a named value — "the apricot color" or "the standard card corner-roundness" — that every screen points to instead of each screen picking its own. Change the one named value and every screen using it changes together. That's the whole reason a "one line changes the whole app" claim below is literally true rather than a figure of speech.

**Tier 1 — Using the system.** Building a feature with the tokens, components, and rules that already exist in Sections 1–8 above (a new list row, a new card, a new sheet that follows the existing patterns, copy that follows the existing voice). **Any agent can do this as part of normal ticket work** — no separate design approval needed beyond the usual ticket flow. (In plain terms, that flow is: an agent looks into the request, does the work on its own isolated copy — a "branch" — tests it, and only Emily's own review and merge makes it live. See `.claude/skills/home-manager-loop/SKILL.md` for the full version of that flow.)

**Tier 2 — Changing the system.** Anything that isn't just *using* what's already documented: changing a token's *value*, adding a *new* token or component, changing one of the numbered hard rules in Section 2, or changing something structural — a new shape/spacing convention, a new kind of screen element not in Section 5's list, a change to the four-screen navigation structure in Section 6 (e.g. adding a fifth tab), a change to the learning-etiquette guidance in Section 7, or a change to the voice guidance in Section 8. **This is Emily's decision first, every time** — an agent proposes it and explains why, but doesn't just build it. Once she's decided: one "commit" (one saved, labeled change to the project) updates `static/theme.css` and `DESIGN_SYSTEM.md` together — never one without the other, since a token change that isn't written down here is a change nobody else can find later — plus a note to also update the Pomona Brand Book canvas so the human-readable guide doesn't drift from the code. The reason this is worth a real decision rather than "just change the CSS": the whole point of tokens (see above) is that **one line change restyles the entire app** — that's real leverage, which is exactly why it needs Emily's sign-off rather than an agent's guess.

**Tier 3 — Identity.** The app's name, its logo, its overall creative direction. This isn't a ticket at all — it's a conversation with Emily, full stop. Don't scope this kind of change into a ticket even if it seems small.

**When it's ambiguous which tier something is** — including a Section 5/6/7/8 change that doesn't obviously look like a "structural" one, or any other situation this document doesn't clearly cover — flag it as an open question for Emily. Never invent a rule to fill the gap, even if the "obvious" answer seems clear — that's exactly the kind of judgment call this project's workflow reserves for her (see `.claude/skills/home-manager-loop/SKILL.md`, "Emily's role").
