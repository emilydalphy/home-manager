# Pomona Design System

For agents. Read this before touching anything visual. It describes what
`static/theme.css` already does — not a wishlist, a spec of the live app.
Sources of truth, in priority order: `static/theme.css` (the code), then the
Brand Guide canvas ("The Inn", approved by Emily 1 Sep 2026) and the Nav
Blueprint canvas (both in `/private/tmp/.../scratchpad/brand-canvas/` at the
time of writing — ask Emily for current copies if that path is gone), then
this document. If code and this document disagree, trust the code and flag
the drift.

Everything below is written as a rule you can check, not a vibe.

---

## 1. Tokens

**Use canonical names only.** `theme.css` used to also define `--plum`,
`--gold`, `--midnight-violet`, `--oat-cream`, `--violet-light`, `--ink-faint`,
`--card`, `--muted`, etc. as thin `var()` aliases forwarding to the canonical
tokens below, kept only so ~40 existing files kept working. The design
hygiene pass (2026-09-12) migrated every remaining call site to its
canonical name (same value, so no visual change) and deleted the alias
block — there is now exactly one name for each token, and there are no
aliases left to avoid.

All values below are lifted directly from `theme.css`. "Dark" = the
`@media (prefers-color-scheme: dark)` block; there is no manual toggle yet,
the app follows the OS.

### Ground & surface

| Token | Light | Dark | Role |
|---|---|---|---|
| `--ground` | `#FBF6EE` | `#101F19` | App canvas. Every screen starts here. |
| `--surface` | `#FFFDF8` | `#172C22` | Cards, list containers, the ask bar. One step brighter than ground; the lift is a hairline, not a shadow. |
| `--surface-raised` | `#fff` | `#192F24` | The pure-white tier: notification panel, ask-message tables, some hovers. |
| `--spruce` | `#1B3328` | `#22422F` (lighter, deliberately, so it lifts off the darker ground) | Hero panel, dark buttons, active nav ink. One spruce panel per screen. |
| `--spruce-raised` | `#24402F` | `#2E5140` | Chips/icon tiles **on spruce only** — never on ivory. |
| `--spruce-hover` | `#2B4A37` | `#32573F` | Hover on a fill already sitting on spruce. |
| `--spruce-edge` | `transparent` | `#2C4B38` | Dark-only hairline separating the hero from a ground that's now nearly the same value. Safe to reference unconditionally — costs light mode nothing. |
| `--field` | `#12211A` | `#0B1712` | The room the phone sits in at ≥768px (the "phone in the room" desktop stance, 2026-09-11): one step darker than spruce so the root band still reads as the app's own top edge. Nothing is ever set in it. |
| `--ink-strong` | `#1B3328` | `#F3EBDD` | Spruce playing the role of *ink* (active tab, "add" links, outline-button labels). Swings to Ink in dark because spruce itself becomes a panel there. |
| `--celadon-tint` | `#E2EDE5` | `#1C3B2C` | The gentle, non-urgent tile — the nudge, not the task. |

### Ink ramp (warm-shifted; never pure black or pure white)

| Token | Light | Dark | Role |
|---|---|---|---|
| `--ink` | `#23302A` | `#F3EBDD` | Headlines and body on ground/cards. |
| `--ink-on-celadon` | `#24362D` | `#F3EBDD` | Body inside celadon tiles. |
| `--ivory-ink` | `#F6EEE1` | `#F3EBDD` | Text on spruce (hero headlines, dark-button labels). |
| `--ivory-ink-muted` | `rgba(246,238,225,.92)` | `#E4DCCE` (solid) | Chip labels on spruce only. |
| `--on-spruce-ink` | `#fff` | `#fff` | Pure white, on purpose, for a selected chip, the household's own chat bubble and the active mic — things that sit on a saturated fill and read one notch brighter than ivory. Added 2026-09-13 (Emily) so the last literals could go. |
| `--ink-secondary` | `#7C7161` | `#BFB6A5` | Supporting copy, outline-button labels. |
| `--ink-muted` | `#7E7360` | `#BFB6A5` | 10px/800 uppercase eyebrows **only**. |
| `--ink-placeholder` | `#797060` | `#9C9384` | Ask-bar placeholder. Darkened 2026-09-03 (from `#8E8370`, 3.47:1) to clear WCAG AA for normal text — now 4.54:1 on ground. |
| `--ink-inactive` | `#948970` | `#9C9384` | Unselected navigation. |
| `--ink-done` | `#7A705E` | `#A9A091` | Completed/struck list text. Darkened 2026-09-03 (from `#A29886`, 2.65:1) — now 4.53:1 on ground, clearing the gap noted below for `--ink-done-soft`. |
| `--ink-done-soft` | `#C6BCA9` | `#9C9384` | Quantity beside completed text. **Known gap:** light value is 1.85:1 on card ivory — fails 4.5:1 today. Pre-existing, not yet fixed; don't copy this pattern into new work. |

### Apricot — the accent that acts

| Token | Light | Dark | Role |
|---|---|---|---|
| `--apricot` | `#E0915C` | `#E9A16C` | Primary action fill, badges. **One per screen.** |
| `--apricot-light` | `#F2B98E` | `#F2B98E` (unchanged) | Apricot on spruce; the italic accent line's color. |
| `--apricot-deep` | `#C4703C` | `#E9A16C` (folds into Apricot) | Status dots, line-icon strokes at small sizes. |
| `--apricot-label` | `#A2582F` | `#F2B98E` | Uppercase micro-labels, links. |
| `--apricot-label-hover` | `#82441F` | `#F7D2B4` | Hover state of the above. |
| `--apricot-rule` | `rgba(224,145,92,.34)` | `rgba(233,161,108,.30)` | The **only** divider permitted inside the hero panel. |

### Celadon — the accent that reassures

| Token | Light | Dark | Role |
|---|---|---|---|
| `--celadon` | `#A9C4B0` | `#A9C4B0` (unchanged) | Confirmation chips, the "New" badge. Says settled, handled, already true. |
| `--celadon-edge` | `#C7DACD` | `#2E5240` | Hairline — only ever borders a celadon-tint surface. |
| `--celadon-label` | `#4F6B5B` | `#97BDA4` | Label + icon stroke inside celadon tiles. |

### The accent-fill rule, as a token

| Token | Light | Dark | Role |
|---|---|---|---|
| `--on-accent-ink` | `#1B3328` | `#14261D` | Text/glyphs on apricot **or** celadon **or** (dark-only) urgent. See Rule 1. |

### Lines, edges, warm neutrals

| Token | Light | Dark | Role |
|---|---|---|---|
| `--hairline` | `#EEE3D0` | `#26382E` | Card borders, nav divider, header rule. 1.5px, always. |
| `--hairline-strong` | `#E6D9C4` | `#33473B` | Ask bar, outline buttons, italic underline — an edge that's also a tap target. |
| `--hairline-deep` | `#D9C9AF` | `#33473B` | The trip checkbox's rim — one step past strong so an unticked box reads as a box. Folds into strong in dark. |
| `--on-spruce-edge` | `#2E5240` | `#2E5240` | A field's rim on spruce (sign-in). Mode-independent because that page is spruce in both. |
| `--sand` | `#F3EBDC` | `#223328` | Soft secondary-button / soft-card fill. |
| `--sand-deep` | `#E6D9C4` (= `--hairline-strong`) | `#33473B` | Deeper warm-neutral step. |

### Status

| Token | Light | Dark | Role |
|---|---|---|---|
| `--urgent` | `#B23A22` | `#E6705B` | Needs a decision / overdue. **Not from the brand guide** — flagged as the assistant's pick, not an approved swatch; still Emily's call to formalize. On light it's a dark saturated fill carrying ivory text (`--urgent-ink`). On dark it flips to a **light** accent fill carrying `--on-accent-ink` — Rule 1 reaches it there, unlike on light, because on dark no single terracotta satisfies both its fill role and its body-text role. |
| `--urgent-ink` | `#F6EEE1` | `#14261D` | Text on an urgent fill. |
| `--urgent-tint` | `#F7E6DE` | `#3B1D16` | Soft urgent background. |
| `--urgent-label` | `#8F2C17` | `#F0A594` | Urgent text on the tint or the ground. |
| `--good` / `--good-ink` | = `--celadon` / `--celadon-label` | same pattern | Done, handled, your turn. |
| `--warn` / `--warn-ink` | = `--apricot` / `--apricot-label` | same pattern | Time-boxed, wants doing. |

**Legacy aliases are gone** (design hygiene pass, 2026-09-12). `theme.css` used to also carry `--oat-cream`, `--midnight-violet`, `--turmeric-gold`, `--vivid-leaf`, `--electric-coral`, `--oat-cream-dark`, `--violet-soft`, `--violet-hover`, `--gold-hover`, `--text-muted`, `--text-body`, `--card-border`, `--violet-light`, `--ink-faint`, `--qty-done`, `--leaf-dark`, `--plum`, `--plum-ink`, `--gold`, `--gold-ink`, `--cream`, `--card`, `--menu-paper`, `--menu-rule`, `--menu-ink`, `--rule`, `--muted`, `--faded` and `--font-heading` (28 names — a few more than this paragraph used to list) as `var()` forwards to the tokens above, kept for ~40 old call sites. Every call site was moved to its canonical name (identical value, so no visual change) and the alias block was deleted once nothing referenced it. If you're reading an old branch or a stale comment that still names one of these, look up its canonical replacement in this file's git history rather than reintroducing it.

---

## 2. Hard rules

Each one is checkable — a reviewer (human or agent) should be able to grep or measure, not guess.

1. **Dark spruce text on light accent fills, always.** Apricot and celadon (and, on dark mode only, urgent) are light-value accents; the ink on them is `var(--on-accent-ink)`. Ivory-on-saturated-accent fails contrast outright, with no exception, including icon strokes. Grep check: any `color:#fff` (or `--ivory-ink*`) inside a rule whose background is apricot/celadon/dark-urgent is a bug.
2. **Purple never leads.** It is never a primary surface, focal point, or brand color. Small, purposeful doses are fine (a warning about a genuinely enterprise-flavored feature, say) but plum-as-main-color reads as enterprise SaaS — the dashboard, the seat license, the quarterly review — the fastest way to make a home feel like a workplace.
3. **`--urgent` is `#B23A22` (light) / `#E6705B` (dark), and it means genuinely urgent only** — overdue, needs-a-decision. It is not a second apricot for "something to look at."
4. **One hero moment per screen.** The full-bleed spruce panel answers one question first (the household on Kitchen until 2026-09-08; since 2026-09-13 Cook's is the Tonight card, §5 — a spruce card in the gutter rather than a full-bleed panel; until 2026-09-11 tonight's dinner on Today — Today's next-up is the one tinted row in its Shop / Cook group since 2026-09-17, §2b S3, and Today has no hero). The root band (§5, 2026-09-11) is not a hero: it is the roots' header, carries no button and answers no question, so a root can still have its one hero moment below it. A second hero on the same screen demotes the first — don't add one. **At most one, not exactly one** — Emily's approved 2026-09-08 redesigns took the hero off two screens rather than replacing it: Meals' Week/Day/Meal steps and Grocery's List/Sort/Trip/Wrap up steps each opened with a title, a subtitle and (on Meals' root) a badge, and put the screen's one apricot at the foot. This line used to name the selected day on Meals and the trip's state on Grocery as live examples; they are gone. (Emily, 2026-09-17: Shop's root is the checklist — the band, one card per store with a tick per row — sorting has one screen, "Sort them all", and there is no trip; Shop has no hero. 2026-09-22: the add row at the top of the list is gone — "Add something" is an outline button in the root's dock beside the chat FAB and opens the add sheet, so Shop's root has a dock, and still no hero.)
5. **One apricot primary per screen.** Kitchen's root deliberately had zero until 2026-09-13 — nothing there was urgent, and giving it an apricot button would have been a lie about what the screen was for; the shelf design gave it "Start cooking" in the dock, which is the one thing the screen is for (§6). If a screen you're building wants a second apricot fill, that's a sign something else should be spruce/outline/plain text instead.
6. **Nothing tappable is under 44×44px**, including text links — e.g. the italic "or start over" sits inside its own 44px row rather than being sized to its text.
7. **Icons are inline stroke SVG, never emoji.** (`stroke="currentColor"`, ~2.2px stroke (one width for the whole set since 2026-09-13; the mark is 1.8), round caps/joins — see any `.hero-icon svg` or `.notif-bell-icon svg` in `shell.css` for the pattern.) Emily's Identity Round Q4 (2026-09-13): the app used to draw its icons at seven weights (2 to 3) and at 24px that read as "not quite the same set"; every inline stroke SVG in `shell.js`, `shell.html` and the standalone pages is `stroke-width="2.2"` now, and `tests/test_design_hygiene.py` (d) fails any new one that isn't. The one exception is the Pomona mark — a logo, not an icon — which keeps the weight it was drawn at: 1.8 in the root band and on the chat button, 1.7 on the sign-in plaque and the desktop field mark, 1.6 on the welcome screens' large glyphs. Filled shapes inside an icon (Plan's five dots) carry `fill="currentColor" stroke="none"` of their own; the rule is about strokes.
8. **Contrast is measured in-browser (computed-style / real WCAG check), never eyeballed.** The dark-mode pass in `theme.css` carries a measured ratio in a comment next to every derived (non-brand-guide) value — that's the standard: if you add or change a token, compute and record its ratio against every surface it actually sits on, the same way.
9. **Every color goes through a token.** A literal hex value in a diff (outside `theme.css` itself, where new tokens are defined) is a review failure. If you need a color that isn't tokenized yet, that's a Tier 2 change (see Governance) — raise it, don't hardcode it.

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
  approved, shopping done, dinner on the table — break the template: spruce, big type, one
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
  pattern, `<thing> was <verbed>`: "Carrots was added", "Thursday and Friday were swapped" —
  with **Undo** on it when the change can be undone. A single tick (a grocery line, a thaw
  move, a chore) is its own decision and still gets the pop-up. A failed save keeps saying so
  in the calm-in-trouble voice (§8). *(**The words changed 2026-09-23**, from Emily's
  correction of 2026-09-22 — "Changes saved" → "Carrots was added", "Put back" → "Undo"
  ("I don't like the AI written style"); see §7 and `.claude/skills/pomona-copywriter/SKILL.md`
  rules 2 and 3. The rule itself is unchanged and still hers from 2026-09-13: a Save, a
  pop-up, an Undo. **The code has not caught up** — `CHANGES_SAVED` / `toastSaved()` in
  `shell.js` still says "Changes saved" for 27 different actions, `plan-week.html` says the
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

---

## 3. Type

Three faces, each with exactly one job (`--font-display`, `--font-body`, `--font-accent` in `theme.css`):

- **Bricolage Grotesque** (display) — every headline, screen title, and button label. Always 700, always tightened (tracking runs from ‑0.038em at hero size down to ‑0.02em at button size).
- **Figtree** (body/interface) — everything else: body copy, list rows, chips, navigation, all uppercase eyebrows. Full 400–800 weight range in use.
- **Newsreader italic** (accent) — 17px, 400 weight. For a line that carries a real fact, said the way a person would say it aloud ("on the table by a quarter past seven") — never a line that could appear in a project-management tool. **At most one per screen, and most screens should have none.** Two lines and it becomes a serif brand instead of an accent.
  *Changed 2026-09-09 (Emily, Tier 2).* This used to read *"exactly one line per screen — the brand's exhale"*, and a required flourish is a flourish somebody has to invent: screens ended up carrying italic lines that restated what was already above them ("Nine dishes, and the whole week is fed"). Her instruction, reviewing the Review-step mockups: *"get rid of the useless italics text... as a new rule don't add useless fluff text."* The face is still available; the quota is gone. If a screen has nothing worth saying this way, it says nothing.

Eyebrows are the only uppercase text in the system: 10px / 800 / 0.13–0.18em tracking, `--ink-muted` colored.

Scale reference (size / weight / tracking → where):
- 33px/700/‑0.038em — hero dish title
- 31px/700/‑0.038em — screen title (`h1`)
- 30px/700/1.05 line-height/‑0.035em — greeting
- 21px/700/‑0.028em — card headline (`h2`)
- 17px/700/‑0.02em — button label, `h3`/card title
- 17px/400 italic — the accent line, on the screens that have earned one
- 19px/800 — active day number; 16px/700 — day number
- 15px/600 — list rows, placeholder text
- 14px/600 — tile body copy; 14px/700 — in-card button label
- 12px/700 — metadata chips
- 11px/700–800/‑0.01em — navigation labels
- 10px/800/0.13–0.18em caps — eyebrows and badges

---

## 4. Shape & space

- **Radii family** (large, soft, and specific to role — don't pick a radius, look it up): `--radius-hero` 30px (hero panel, bottom corners only) · `--radius-card` 20px (cards/tiles) · `--radius-ask` 18px (ask bar) · `--radius-action` 17px (primary action; active day tile, top corners only) · `--radius-tile` 15px (day tiles, 44px icon button) · `--radius-control` 14px (in-card buttons/inputs) · `--radius-send` 13px (send tile) · `--radius-chip` 11px (metadata chips) · `--radius-badge` 10px (badges, hero icon tile) · `--radius-badge-sm` 9px (small caps badges) · `--radius-pill` 999px.
- **The joined/notched tile motif**: a tile that touches another surface is *square on that shared edge, round everywhere else* — never both round. Example: on Meals, the selected day tile drops its bottom radius exactly where the spruce hero panel drops its top radius, so the two read as one poured shape. This is the system's signature move; don't round every corner of everything "for consistency."
- **Spacing rhythm**: 20px screen gutter (the one true constant — full-bleed panels break it deliberately, which is what makes them read as architecture rather than as cards) · 24/22px top padding above the first line · 12px between cards/grid cells · 14px card padding · 8–10px between chips/buttons · 4px between navigation cells.
- **Elevation**: within a screen's normal content, shadow belongs to the hero panel and the apricot primary action *only* — a card, tile, or row never gets one; it separates via a `--hairline` instead. Shadow is also used, separately, by things that float over the *whole screen* rather than sit within it — `--shadow-sheet` (bottom sheets), `--shadow-dialog` (centered dialogs), `--shadow-panel` (the notification panel), `--shadow-toast` — because those need to read as detached from everything beneath them. Don't add a shadow to ordinary in-page content; do reach for the matching token if you're building a new full-screen overlay. In dark mode the hero's shadow all but disappears against the dark ground, so its lift comes from a **lighter fill plus a hairline edge** (`--spruce-edge`) instead — a shadow reads as nothing on a dark ground, don't try to keep using one there.
- **Motion** (Emily, 2026-09-11 — "I like the idea of adding in motion into the app"): five animations in the whole app (three approved 2026-09-11; a fourth added with the 2026-09-13 "Sort them all: a row leaves the moment I sort it" ticket; a fifth — a done store card rolling up — approved by Emily 2026-09-22, Tier 2), each under 250ms, each off under `prefers-reduced-motion: reduce`. Tokens in `theme.css`: `--motion-fast` (180ms), `--motion-base` (240ms), `--motion-ease` (`cubic-bezier(.2,.7,.2,1)`, ease-out — the standard for anything settling into place), `--motion-ease-in` (`cubic-bezier(.4,0,1,1)`, for anything leaving). A `@media (prefers-reduced-motion: reduce)` block collapses the duration tokens to 0ms, on top of the blanket `transition-duration`/`animation-duration` override this file already carried. The five: (1) every bottom sheet and centered dialog slides up / scales in over a scrim that fades in, and reverses on close (`shell.js`'s `openSheet()`/`closeSheet()` helper, routed through by every sheet and dialog in §5's Sheets/Toast row); (2) tab panels crossfade with a 6px rise when a tab becomes active (`activateTab`, `shell.js`); (3) a ticked grocery row on the shopping trip settles — its checkbox fill, strike-through and ink all ease to the done state, and reverse on untick; (4) a row on Shop's "Sort them all" screen collapses out (`--motion-fast`, `--motion-ease-in`) the moment it is given a store, so the rows below slide up rather than jump (`groSortAllLeave`, `shell.js`); (5) *(added 2026-09-22, Emily's "a store done" mockup)* a store card whose last row was just ticked rolls up, after a beat with "Done at Costco" in its head, to one line below the stores still to do — its rows fold (height to 0 over `--motion-base`, `--motion-ease-in`) and the one-line row settles into place (`--motion-base`, `--motion-ease`); one motion in two halves, instant under reduced motion (`groRollUp`, `shell.js`). **Nothing else animates** — no decorative motion, no attention-getting movement, anywhere else in the app.

---

## 5. Component vocabulary

Reference implementations to copy patterns from, not to import as-is (there's no shared component library — everything is CSS classes plus vanilla JS in `static/shell.js`/`static/shell.css`):

| Component | Where to look | Notes |
|---|---|---|
| Hero panel | `shell.css` `.dinner-hero, .day-hero` (~line 386) | Full-bleed spruce, `--radius-hero` bottom corners, `--shadow-hero`. |
| Root band | `shell.css` `.root-band` (its own section, just above Today's); `rootBandHtml`/`setRootBand` in `shell.js` | How every tab root opens (Emily, 2026-09-11, Option B — Tier 2). One full-bleed spruce band, about 110–130px showing on a phone. **Since 2026-09-13 (Emily, Identity Round Q1 = C) its top-left is the Pomona mark (20px, stroke 1.8, `--apricot-light`) and the wordmark "Pomona" (`--font-display` 15px/700/‑0.02em, `--ivory-ink-muted`), and the date or context that used to be the eyebrow joins the sub-line with " · " in front of whatever it already said** — "Sunday, Sep 13 · 2 of 5 done", "Sep 14–20 · a draft, your turn", "Sunday, Sep 13 · 60 things · 1 stop", "Sunday, Sep 13 · 1 cook tonight". This is `BAND_IDENTITY` in `shell.js`: `'wordmark'` (C, the default), `'mark'` (B, kept in reserve: the mark before the date eyebrow, the eyebrow keeps the date), `'none'` (the band as it was before 2026-09-13 — an eyebrow in `--apricot-light` carrying the date or the context: "Friday, Sep 11", "Aug 24–30", "60 things · 1 stop", "Friday"); one builder renders all three. Under any of them: the title in `--font-display` 31px, at most one status chip ("Draft", "Approved", "Week set" — small caps on `--spruce-raised` with `--ivory-ink-muted`, never an accent fill) and at most one 15px `--ivory-ink-muted` line. The gear and the bell's slot sit top-right in it as 44px tiles on `--spruce-raised`. The ivory content pours over its foot with `--radius-hero` top corners (the band's `::after`; the joined-tile motif at screen scale, same shape as sign-in's card). It never carries a button — the dock is where the one thing to press lives — and it is under a third of the screen at 375×812. In dark it lifts by the lighter `--spruce` plus the `--spruce-edge` hairline along that curve; the measured ratios are in the CSS comment. Roots only: sub-screens keep the step head + crumb below. The band's line and an empty moment under it must never say the same thing (Cook's band says "Friday", its empty state says "nothing to cook"). |
| Empty moment | `shell.css` `.empty-moment`; `emptyMomentHtml` in `shell.js` | A root with nothing on it (Emily, 2026-09-11): a 24px stroke icon in a `--celadon-tint` tile with `--celadon-label` stroke, one sentence in `--ink` 17px `--font-display` 700, an optional second line in `--ink-secondary`, and the next step in the dock — never a button in the block. It fills the space between the band and the dock and centres itself there. Now: "Quiet day. Want me to sort dinner, or the whole week?" (dock: "Let's plan the week" · "Just tonight"); Shop: "Nothing to buy. Approve a week and I'll build the list." (dock: "Go to Plan"); Cook: "Nothing to cook tonight." plus the next cook when there is one (no dock on a night with nothing to cook — since 2026-09-13 Cook's root has a dock only when tonight has a cook; the shelf still shows above the moment). |
| The shelf | `shell.css` `.cook-shelf`/`.shelf-tile` (Kitchen section); `cookShelfHtml`/`dishShortWord` in `shell.js` | Cook's root, Emily 2026-09-13 ("Cook · D · The shelf" from the Beyond-lists canvas). A sideways strip in the 20px gutter: one 74px tile per night of the planning period (`--surface` + `--hairline`, `--radius-tile`, 6px gaps), the day as an eyebrow, the date in the display face (19/800 tonight, 16/700 otherwise — Plan's day-tile step), and one word for the dish (`dishShortWord`: the kind of dish if the name has one — "Stir-fry", "Tikka" — else its last real word — "Chicken"). Tonight is `--celadon-tint` + `--celadon-edge` with a `--celadon-label` eyebrow; a night with nothing planned shows "—" in `--ink-muted` and is not a button. A tile opens that night's meal screen with the "‹ Cook" crumb (§2b S8). Opens scrolled so tonight is in view. |
| The strip (Plan root) | `shell.css` `.wk-strip`/`.wk-tile`, `.wk-root-day`, `.wk-snacks`; `weekStripHtml`/`weekTileHtml`/`weekDayHtml`/`weekSnacksHtml` in `shell.js` | The Plan root's week, Emily 2026-09-14 ("Plan root: the week as a strip", canvas B1 + S4 — the seven-row card is gone). One tile per day of the planning period side by side in the gutter (`--surface` + `--hairline`, `--radius-tile`, 6px gaps, sharing the width; eight or more scroll sideways): the day as a 10px eyebrow, the date in the display face (16/700; 19/800 today), and the three-dot legend for breakfast · lunch · dinner (apricot cooks, celadon already made, grey nothing, an outline still open). Today is `--sand` with a `TODAY` eyebrow in `--apricot-label` (§2b S6); the selected tile carries the `--ink-strong` rim; past days sit at .62 and stay tappable. Tapping a tile selects the day **in place** — no step, no history push — and the day renders under the strip as the Day step's own slot cards (`daySlotCardHtml`, quiet: no Cook this / Swap row on the root, those live on the Meal step's dock; an open slot keeps Pick), then the snacks two-up: a `SNACKS` eyebrow on the ground (`--ink-strong`) and one tile per snack side by side. A root card's Meal step comes back with "‹ This week". Opens on today, else the first day not yet past. |
| Tonight card | `shell.css` `.cook-tonight` (Kitchen section); `cookTonightCardHtml` in `shell.js` | Cook's root's one hero moment (rule 4), same decision. One spruce card in the gutter (`--radius-card`, 18px padding, `--shadow-hero`, and the `--spruce-edge` hairline the band wears so it lifts on the dark ground): an `--apricot-light` eyebrow "MONDAY · TONIGHT · EMILY" (the cook by name only when the household named one — `cook_name` on `/api/cooker-view`), the dish at 26px/700/‑0.034em, a two-up of `--spruce-raised` tiles ("START 6:00" / "ON THE TABLE 6:30", the moves engine's own arithmetic — and once the cook has really begun, "STARTED 6:02" / "ON THE TABLE 6:37" off `cook_started_at`, 2026-09-13), and one 14px/500 `--ivory-ink-muted` line: the thaw or prep fact for this meal, else "Nothing to thaw or prep ahead." — or, once started two minutes or more off the plan, "Started 17 minutes late — the clock's moved with you." (never inside two minutes: no nagging over a minute). (The cook hero's `.cook-meta-chip.is-live` chip that used to echo "Started 6:02" one step down is gone — Emily, 2026-09-17, "The recipe is the recipe": the recipe and cooker screens show no clock; this card and Today are where the real start reads.) Under a third of the screen (§2b S3); no button in it — the dock has the action. Under it, at most two quiet get-ready rows (`.cook-ready-row`: 52px, a 32px icon tile — `--celadon-tint` for a thaw, `--sand` for prep — title in the display face, one line, a chevron). |
| Hero chip / badge | `shell.css` `.hero-chip` (~488), `.hero-badge`/`.hero-tag` (~399) | Chips row inside the hero; `--radius-chip`. |
| Segmented control | `shell.css` `.meals-seg` | Meals' Plan/Cook — the last one left. Grocery's To buy/Plan stops/Review (`.gro-seg`) was deleted on 2026-09-08 with the four-step Grocery redesign; the reference implementation is `.meals-seg` alone now. One control per screen. |
| Step head + crumb | `shell.css` `.wk-head`/`.wk-title`/`.wk-sub`, `.gro-head`/`.gro-sub`, and `.crumb` for the back link | A tab's DEEPER steps (a day, a meal; on Shop, last week's leftovers and "Sort them all" — the sort queue and the trip went on 2026-09-17, Emily: Shop's root is the checklist) open with a title and one line, and a named "‹ Parent" crumb — never `history.back()`, never a new page. Added 2026-09-08; since 2026-09-11 the roots themselves open with the Root band instead (below), so this head is never on a root. The back link was three near-identical classes (`.wk-back`, `.gro-back`, `.cook-focus-back`) until 2026-09-10; it is one `.crumb` now, with `.on-spruce` for the variant inside Cook's hero. |
| Dock | `shell.css` `.dock` (+ `.cook-dock`/`.gro-dock`/`.wk-decide` for per-screen padding) | The screen's one action, sticky above the ask bar. Built as `.cook-dock` for the cook journey and generalised on 2026-09-10 when nav rule 2 put the same strip on Plan and Shop. Quiet secondaries ride along as `.dock-link` text; a second APRICOT would break Rule 5. When the second path is a real one rather than a rare action it is a `.dock-secondary` — a full-width sand button (the same one as Shop's `.gro-secondary` "Skip the rest") ABOVE the apricot, re-inked to `--spruce-raised` on a spruce dock: All set's "See the week" over "Open the list · 53 ingredients" (Emily, 2026-09-13: seeing the week you just made is a first-class path, and the list keeps its pull with the number on it). *(Gone 2026-09-18: All set is one button, "Next · Anything in the freezer?", so `.dock-secondary` has no instance and was removed; the approved Plan root's dock has no apricot at all — "Plan next week" as an outline `.wk-plan-next` at the right.)* A screen with no single action has no dock. Cook's root got one on 2026-09-13 (`.cook-root-dock`, "Start cooking" / "Mark eaten"; nothing on a night with no cook). Since 2026-09-21 Shop's root dock carries "+ Add something" as an outline (`.gro-add-open`, 48px) beside the chat FAB — a side errand kept in reach while the list scrolls, not the screen's action, so still no apricot; the add itself is a body-level sheet (`groAddSheetHtml`) that asks "Where do you get it?" with one chip per store and remembers the pick. |
| Ask-bar chips | `shell.css` `.ask-chips`/`.ask-chip` (~828) | Suggestion chips above the chat composer. |
| Change card | `shell.css` `.ask-change-card` (its own section above `.ask-chips`); `mountChangeCard`/`changeRowHtml` in `shell.js`; `app/tools/proposals.py` | What the chat proposes for the week, drawn under its reply (Emily, 2026-09-13, "Shaping the Draft" Flows C and D). One row per slot: the day as an eyebrow, what was (struck) → what would be, minutes, the one-line reason; two to four options render as tappable pills (`.ask-change-opt`, `--celadon-tint` when chosen); a night they said to leave shows the celadon **Kept** tile. *Another* (an `--apricot-label` link) re-picks that row alone; **Save changes** is spruce (the dock behind the sheet keeps the screen's apricot, Rule 5); "Leave the week as it was" is the quiet way out. Nothing is written until Save; then the pop-up names what changed, with Undo (S10), and the week's rows carry **Changed** (`.wk-changed`, S6) for eight seconds. |
| Plate parts | `shell.css` `.plate`/`.plate-part` (Day card), `.plate-rows`/`.plate-row` (Meal step); `plateRowHtml`/`platePartsRowsHtml` in `shell.js`; `app/tools/plate_parts.py` | The plate as its parts (Emily, 2026-09-13, "Shaping the Draft" Flows A and B). On the Day card: 32px sand chips — the role as a quiet word, the part, a caret ("Protein · Turkey", "Carb · Roasted potatoes"); a part the rule wants and nothing covers is a dashed `--hairline-strong` outline in `--apricot-label` ("+ Add a carb"). A dish that records no food groups shows a plain Protein chip and no dashed ones — unknown is not short. Each is a tap: the protein opens "Change the protein" (options written for this dish, cut-level: thighs vs breasts, ground meats for a burger; Save rewrites the recipe around the pick through the swap's own gates and apply, so Undo is the swap's own); a veg or carb opens "Add something" narrowed to that part, with "Take X off" as the quiet line when a side is already there. On the Meal step the same parts are rows under a "The plate" eyebrow, each with Change / Add. The sheet follows S10: tap to choose (tick + `--celadon-tint`), an apricot Save under the rows (the dock's apricot is behind the scrim), one quiet way out. |
| Remembered chip | `shell.css` `.ask-remembered`; `buildAskMessageEl` in `shell.js` | A fact the turn remembered ("Kids: no shrimp"), shown as what it is under the reply: a `--celadon-tint` chip with the REMEMBERED eyebrow, the words, and "Not quite" opening What we know to correct it (§7: observe → infer → confirm → remember → correct, in one breath). Replaces the generic "Household info updated" card for memory writes. |
| Sheets | `shell.css` `#ask-sheet` (~742), `#week-sheet` (~1631), `#kit-sheet` (~3486) | Slides up over the current tab, dismisses down; each has a `[hidden]` guard pair — see Nav rules below for when a sheet is the right choice vs. a new page. |
| Toast | `shell.css` `.toast` (~359) | Uses its own `--toast-bg`/`--toast-ink` pair (not `--ink`, which inverts) because it's dark-on-light in light mode specifically. |
| Bell / notifications | `shell.css` `.notif-bell` (~1750) | Permanent entry point (Emily's call, 2026-09-02) — stays visible even when the feed is empty; sits beside the ask bar. On the spruce rail it uses a lifted fill (`--spruce-raised`-family) since a spruce bell on spruce would disappear. |
| Cards / surfaces | `theme.css` `.card`, `.surface` (~350) | `--surface` background, 1.5px `--hairline` border, `--radius-card`. Lift is the hairline, not a shadow. |
| Buttons | `theme.css` `.btn-primary`/`.btn-secondary` (~301) | Primary = apricot fill + `--on-accent-ink` text, `--radius-action`; one per screen (Rule 5). Secondary = spruce outline/text-only. |
| Pills | `theme.css` `.pill-success`/`.pill-attention`/`.pill-neutral` (~345) | Status chips; accent fills always carry dark ink per Rule 1. |
| The draft's band extras | `shell.css` `.wk-replan`, `.wk-draft-lead`, `.wk-draft-seg` (the "draft's front door" block after the carousel); `weekBandExtras`/`fillWeekBandExtras` in `shell.js` | Emily, 2026-09-21 (boards A2/C2/C3). Three things filled into the Plan root's band after `rootBandHtml` builds it: the **Re-plan** pill top right (36px `--apricot-light` on spruce, 44px through `::before`; on a draft AND an approved week — the board's call, noted against Rule 5 since the draft's dock already holds the Approve apricot), the draft's two-line **opener** (`draft_opener` from `get_week_menu`, built server-side in `app/tools/draft_opener.py` from the model's own report of the typed requests it honoured and could not — never a guess about the words), and the **What we're eating \| Which days** toggle — a `.wk-seg` variant on spruce: `--spruce-raised` track, `--surface` + `--ink-strong` selected half, 44px halves. A draft's title is "Here's your week." and its line "your turn" (the chip says Draft). |
| The menu (What we're eating) | `shell.css` `.wk-menu`, `.wk-menu-card`, `.wk-menu-head`; `wkMenuGroups`/`wkMenuRowHtml`/`wkMenuHtml` in `shell.js` | The draft's front door (Emily's decision D, 2026-09-15; again 2026-09-21): one card per meal type, one `.wk-row` per dish with the days it covers and one fact ("Mon, Wed · Mexican, as asked" — `asked` from `get_week_menu`), Swap on every row through the same swap sheet as the day cards (`data-wk-day-index` names the dish's first day ahead). No day notes, no steppers. |
| The reason a tap away | `shell.css` `.wk-row-why`, `.wk-why-pop`; `wkRowMetaHtml` in `shell.js` | A row's meta line is the tap when the slot has a stored `reason`; the reason opens as a `--surface-raised` note floating over the next row (absolute, `--shadow-panel`) so it never pushes rows around. One open at a time. No reason, no tap. |

---

## 6. Navigation rules (from the Nav Blueprint)

- **Four native screens, period: Today, Plan, Shop, Cook.** (The first was "Now" from nav v2 part 1, 2026-09-09, until Emily renamed it back to Today on 2026-09-17 — the day's moves grouped as Shop and Cook, tagged Morning / Afternoon / Evening; the other three were Meals, Grocery and Kitchen until nav v2 part 1; the code still spells the tab keys `today`/`week`/`grocery`/`kitchen`, which is deliberate — internal identifiers were left alone, as at the rebrand.) Their tab-bar glyphs: a sunrise, the week as a row (five dots between two rules — the day tiles in miniature; Emily, 2026-09-13, Identity Round Q3 = B, replacing a bullseye nobody read as a plate), a bag, a pot — `ICONS` in `shell.js`, one 24-grid, one stroke (rule 7). Everything else is a *state* (a segmented control inside a tab), a *sheet* (slides over, dismisses down), or a *step* — never a new page with its own header or its own back button. If you're about to add a page with a back arrow to a sibling tab, stop — it should be a sheet or a segmented state instead. Every tab root opens with the root band (§5) — the same spruce band with the date or context, the title, the gear and the bell, and the ivory sheet pouring over its foot — and nothing deeper than a root does (Emily, 2026-09-11).
- **The chat is one icon on every screen, and the sheet it opens is part of the shell, not any one screen.** (Emily, 2026-09-11, reviewing the screen-by-screen mockups; §2b S2.) A round spruce button sits bottom-right on all four screens, same place always — floating above the tab bar, or at the right end of the dock row when a screen has a dock (`.chat-fab` in `shell.css`; the `.dock` clears it with right padding). Its icon is the Pomona mark, 26px in `--apricot-light` on the spruce at the logo's own 1.8 stroke (Emily, 2026-09-13, Identity Round Q2 = B — it was a speech bubble until then; "talk to Pomona" is, literally, the fruit); `aria-label` and `title` still read "Chat with Pomona". Because the fruit doesn't say "chat" on its own, a 9px/800/.14em uppercase "Ask" in `--apricot-light` sits under the mark inside the same 54px button (the mark drops to 22px while it shows) for a device's first three visits — `FAB_LABEL_VISITS` in `shell.js`, a per-device localStorage counter on its own key (`pomona.fabLabelVisits`), 0 = never, `Infinity` = always — and after that the mark alone; nothing outside the button moves either way. Tapping it opens the ask sheet, whose title and composer both read "What's on your mind?" on every tab. The example-prompt chips (a tab's first three visits) and the "?" into Helpful tips live *inside* the sheet; nothing about the chat sits on the screen but the icon. Until 2026-09-11 this was an always-open ask bar above the tab bar with the chips and the "?" beside it — that bar is gone. The sheet's answer still arrives over whatever tab you're on; it never navigates you somewhere you didn't ask to go. The notifications bell moved from the old bar's row into every root's header beside the gear (`prefsGearHtml`).
- **Cooking is a *step* of Cook** (Emily, 2026-09-08), not a state of Plan and not its own tab. Cook's root answers "what's cooking, and what's in the house?"; the focused single-meal screen is one level down it, and `shell.js`'s own `TABS` comment says the same thing. It stays a step rather than becoming a sibling tab for the reason this rule has always given: it is the same week's data with the recipes opened up, and two tabs over one dataset is exactly the kind of drift this rule exists to prevent. (Until 2026-09-08 this was a `Plan | Cook` segmented state at `/week`; that control is gone.)
- **Cook's root has one primary action: "Start cooking", in its dock** (Emily, 2026-09-13, Tier 2 — decided by picking "Cook · D · The shelf"; changed in the same commit as the code). Until then this line read *"Cook's root has no primary action. Nothing on it is urgent by design — it's what today asks of the cook, read rather than acted on"*, and rule 2 below named Cook's root as the live example of a screen with no dock. The shelf design gives the root the one thing it is for: the dock opens tonight's cook on its recipe (`cookEnterFocus` — the screen was "Before you start" until Emily's 2026-09-17 "The recipe is the recipe": the dish, "Cooking for", the Ingredients and Steps cards, "Start cooking"), or reads "Mark eaten" on a reheat night. A night with nothing to cook, or a dinner already cooked, still has no dock — the empty moment (§5) and the shelf stand alone. One step down: the recipe's "Start cooking" and the cooker's "Next step" / "Done — on the table" (it read "Mark it cooked" until 2026-09-17) are each their screen's one apricot. Rule 5 holds one screen at a time.
- **One way back, and it names its parent** (Emily, 2026-09-09 — nav v2 rule 1). Every screen deeper than a tab root carries **exactly one** breadcrumb, and it says where it goes: `‹ This week`, `‹ Monday`, `‹ Shop`. Never a bare arrow, never `history.back()` (which after any wandering points at the previous *view* rather than the parent — a link reading "This week" must not land on a meal), and never a second exit competing with the tab bar. The tab bar never leaves, so the way out of the whole branch is always on screen too; the crumb is the way up *one level*. The phone's back gesture goes up one level as well and matches the crumb. A tab root has no crumb.
- **One dock** (Emily, 2026-09-09 — nav v2 rule 2). The single thing a screen is for lives in a fixed strip above the tab bar, in the same place on every screen, labelled for what it does ("Approve and build my shopping list", "Start cooking"; "Done at Loblaws" was the trip's until 2026-09-17 — Emily: Shop's root is the checklist, a tick per row and no dock, and "Done at Loblaws" is the card's own head once every row is ticked), and it does not scroll away. **A screen with no single action has no dock** — Cook's root was the live example until 2026-09-13, when the shelf design gave it "Start cooking" (see the Cook's-root rule above); a Cook night with nothing to cook, and Today's day with nothing featured, are the live examples now. Inventing a dock for a screen with nothing to press would be the same lie an apricot button on it would be (Rule 5). Today (called Now until 2026-09-17) got a dock on 2026-09-11 (Build 2 of the screen-by-screen redesign): the featured move's action ("Cook this", "Done", "Go shopping" — `moves.py` SHOP_ACTION_LABEL; "Open the list" until 2026-09-17) or, with no week planned, "Let's plan the week" with "Not now" as its quiet link — the action left the next-up card for the dock, so the card could stop being a hero (§2b S3). A day with nothing featured has no dock; ticking a line is the action. Rare actions go behind a `···`, not into a second dock button. This is still one apricot per screen; the dock is where that apricot lives.
- **Refresh policy** (what must be true after each kind of event):
  - *You change something* → the screen updates on tap, before the server confirms. A failed save reverts the row and says so; the common case never waits.
  - *One screen's change touches another* → every screen reads from the same store, so it's already correct (e.g. approving the week on Plan updates Shop before you get there). This is why native screens matter more than they look like they should — an iframe can't share a store.
  - *You switch tabs* → nothing reloads. A quiet background check folds in server changes without the screen jumping under your thumb.
  - *You return after being away a while (a couple of minutes+)* → refetch everything once, quietly — someone else in the house may have shopped or cooked.
  - *You pull down* → pull-to-refresh exists on all four tabs and does the obvious thing, but it's a courtesy, never the only way to see the truth.
  - If you add a new native panel or a new action tool, wire it into `refreshStaleTabsFromActions()` (`shell.js`) — a panel that's built once and never told to refresh goes stale silently. See CLAUDE.md's "Tab panels build once per page load" gotcha for the exact failure mode this has already caused twice.

---

## 7. Learning etiquette

Pomona learns from what a household does — this section is how it's allowed to do that.

- **The loop is observe → infer → confirm → remember → correct.** The app notices a pattern, forms a guess, and checks it with one light, inline tap — never a form, and never asked twice for the same fact. Once confirmed, it remembers, and that answer stays one tap away from being corrected later. Skipping "confirm" isn't a shortcut version of this loop, it's a different one — see silent learning below.
- **Silent learning needs a visible flag and an undo, right at the point of use.** The app may act on a guess without asking first, but only where the result shows up on-screen as something the person can see and reverse in the moment — the grocery list's "usually here" / "not this time" is the reference pattern. Acting on a guess anywhere that isn't visible and reversible right there isn't silent learning, it's just guessing. *(2026-09-22, Emily's "adding or sorting once remembers" card: a store the household chose themselves — in the add sheet, on a "Sort them all" chip, from a row's ⋯ — is remembered as the item's usual without a confirm step. **Changed 2026-09-23** (Emily, "it should auto remember anyways"): the add sheet used to spell the remembering out itself, a celadon "I'll remember Costco for it" line under the chips, on top of the "Changes saved · Put back" pop-up after — she called the line unneeded, since the row landing in that store's card already shows the pick took. The line is gone; the visible flag is now just the row itself plus the add's own toast ("Carrots was added · Undo"), and Undo still forgets the store the same way Put back did. Only the add sheet's copy changed that day: the row menu's move and "Sort them all" still pop "Changes saved · Put back" in the code today — drift left to fix (findings 1 and 2 of `COPY_SWEEP_2026-09-23.md`), not a second pattern to copy.)*
- **The flag is the thing on screen; the words never announce the remembering.** *(2026-09-23, from Emily's correction of 2026-09-22 — "Can we make sure that copy throughout is more straight forward like this. I don't like the AI written style." The long version is `.claude/skills/pomona-copywriter/SKILL.md`, rules 2 and 3.)* The visible flag and the undo above are still required — that rule doesn't move. What changed is what they are allowed to say:
  - **The flag is the result, not a sentence about the result.** The row landing in its store card *is* the flag. "I'll remember Costco for it next time" is not one — it is me reporting my own filing, and the person meets the choice again where it's used, which is better proof than a sentence about it. The remembering stays; the announcement goes.
  - **The confirmation names the thing.** "Carrots was added", not "Changes saved" — the house toast pattern, `<thing> was <verbed>`, one clause, past tense. If the app can't name what it just did or just learned, it doesn't know enough to be popping a pop-up.
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
- **Every word earns its place — and this one is a hard rule, not a preference** (Emily, 2026-09-09: *"as a new rule don't add useless fluff text"*). Pomona doesn't add copy for the sake of tone or personality. If a line can be cut without losing meaning, cut it. Three failures to watch for, all of which shipped before she caught them:
  - **Restating the screen.** A subtitle that says in a sentence what the content below says in a list. If the reader can already see it, don't narrate it.
  - **Filling a slot.** Copy written because a template has a place for a subtitle, an accent line or a reassurance. An empty slot is a fine outcome; see the italic accent line in §3, which used to be mandatory and no longer is.
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
