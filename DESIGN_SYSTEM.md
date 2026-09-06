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

**Use canonical names only in new code.** `theme.css` also defines
`--plum`, `--gold`, `--midnight-violet`, `--oat-cream`, `--violet-light`,
`--ink-faint`, `--card`, `--muted`, etc. as thin `var()` aliases forwarding
to the canonical tokens below, kept only so ~40 existing files keep working.
**Never write a new call site against an alias.** If you touch a line that
uses one, move it to the canonical name while you're there.

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
| `--ink-strong` | `#1B3328` | `#F3EBDD` | Spruce playing the role of *ink* (active tab, "add" links, outline-button labels). Swings to Ink in dark because spruce itself becomes a panel there. |
| `--celadon-tint` | `#E2EDE5` | `#1C3B2C` | The gentle, non-urgent tile — the nudge, not the task. |

### Ink ramp (warm-shifted; never pure black or pure white)

| Token | Light | Dark | Role |
|---|---|---|---|
| `--ink` | `#23302A` | `#F3EBDD` | Headlines and body on ground/cards. |
| `--ink-on-celadon` | `#24362D` | `#F3EBDD` | Body inside celadon tiles. |
| `--ivory-ink` | `#F6EEE1` | `#F3EBDD` | Text on spruce (hero headlines, dark-button labels). |
| `--ivory-ink-muted` | `rgba(246,238,225,.92)` | `#E4DCCE` (solid) | Chip labels on spruce only. |
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

**Legacy aliases exist** (`--oat-cream`, `--midnight-violet`, `--turmeric-gold`, `--vivid-leaf`, `--electric-coral`, `--violet-light`, `--ink-faint`, `--qty-done`, `--leaf-dark`, `--plum`, `--plum-ink`, `--gold`, `--gold-ink`, `--cream`, `--card`, `--menu-paper`, `--menu-rule`, `--menu-ink`, `--rule`, `--muted`, `--faded`, `--font-heading`). They are `var()` forwards to the tokens above, kept for ~40 old call sites. **Do not use them in new code.**

---

## 2. Hard rules

Each one is checkable — a reviewer (human or agent) should be able to grep or measure, not guess.

1. **Dark spruce text on light accent fills, always.** Apricot and celadon (and, on dark mode only, urgent) are light-value accents; the ink on them is `var(--on-accent-ink)`. Ivory-on-saturated-accent fails contrast outright, with no exception, including icon strokes. Grep check: any `color:#fff` (or `--ivory-ink*`) inside a rule whose background is apricot/celadon/dark-urgent is a bug.
2. **Purple never leads.** It is never a primary surface, focal point, or brand color. Small, purposeful doses are fine (a warning about a genuinely enterprise-flavored feature, say) but plum-as-main-color reads as enterprise SaaS — the dashboard, the seat license, the quarterly review — the fastest way to make a home feel like a workplace.
3. **`--urgent` is `#B23A22` (light) / `#E6705B` (dark), and it means genuinely urgent only** — overdue, needs-a-decision. It is not a second apricot for "something to look at."
4. **One hero moment per screen.** The full-bleed spruce panel answers one question first (tonight's dinner on Today, the selected day on Meals, the trip's state on Grocery, the household on Kitchen). A second hero on the same screen demotes the first — don't add one.
5. **One apricot primary per screen.** Kitchen deliberately has zero — nothing there is urgent, and giving it an apricot button would be a lie about what the screen is for. If a screen you're building wants a second apricot fill, that's a sign something else should be spruce/outline/plain text instead.
6. **Nothing tappable is under 44×44px**, including text links — e.g. the italic "or start over" sits inside its own 44px row rather than being sized to its text.
7. **Icons are inline stroke SVG, never emoji.** (`stroke="currentColor"`, ~2–2.6px stroke-width, round caps/joins — see any `.hero-icon svg` or `.notif-bell-icon svg` in `shell.css` for the pattern.)
8. **Contrast is measured in-browser (computed-style / real WCAG check), never eyeballed.** The dark-mode pass in `theme.css` carries a measured ratio in a comment next to every derived (non-brand-guide) value — that's the standard: if you add or change a token, compute and record its ratio against every surface it actually sits on, the same way.
9. **Every color goes through a token.** A literal hex value in a diff (outside `theme.css` itself, where new tokens are defined) is a review failure. If you need a color that isn't tokenized yet, that's a Tier 2 change (see Governance) — raise it, don't hardcode it.

---

## 3. Type

Three faces, each with exactly one job (`--font-display`, `--font-body`, `--font-accent` in `theme.css`):

- **Bricolage Grotesque** (display) — every headline, screen title, and button label. Always 700, always tightened (tracking runs from ‑0.038em at hero size down to ‑0.02em at button size).
- **Figtree** (body/interface) — everything else: body copy, list rows, chips, navigation, all uppercase eyebrows. Full 400–800 weight range in use.
- **Newsreader italic** (accent) — 17px, 400 weight, *exactly one line per screen, in italic*. It's the brand's exhale; two lines and it becomes a serif brand instead of an accent. This is the "house line" — a fact stated the way a person would say it aloud ("on the table by a quarter past seven"), never a line that could appear in a project-management tool.

Eyebrows are the only uppercase text in the system: 10px / 800 / 0.13–0.18em tracking, `--ink-muted` colored.

Scale reference (size / weight / tracking → where):
- 33px/700/‑0.038em — hero dish title
- 31px/700/‑0.038em — screen title (`h1`)
- 30px/700/1.05 line-height/‑0.035em — greeting
- 21px/700/‑0.028em — card headline (`h2`)
- 17px/700/‑0.02em — button label, `h3`/card title
- 17px/400 italic — the one accent line
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

---

## 5. Component vocabulary

Reference implementations to copy patterns from, not to import as-is (there's no shared component library — everything is CSS classes plus vanilla JS in `static/shell.js`/`static/shell.css`):

| Component | Where to look | Notes |
|---|---|---|
| Hero panel | `shell.css` `.dinner-hero, .day-hero` (~line 386) | Full-bleed spruce, `--radius-hero` bottom corners, `--shadow-hero`. |
| Hero chip / badge | `shell.css` `.hero-chip` (~488), `.hero-badge`/`.hero-tag` (~399) | Chips row inside the hero; `--radius-chip`. |
| Segmented control | `shell.css` `.gro-seg`/`.gro-seg-btn` (~2547), `.meals-seg` (~3542) | Grocery's To buy/Plan stops/Review, Meals' Plan/Cook. One control per screen, identical shape both places. |
| Ask-bar chips | `shell.css` `.ask-chips`/`.ask-chip` (~828) | Suggestion chips above the chat composer. |
| Sheets | `shell.css` `#ask-sheet` (~742), `#week-sheet` (~1631), `#kit-sheet` (~3486) | Slides up over the current tab, dismisses down; each has a `[hidden]` guard pair — see Nav rules below for when a sheet is the right choice vs. a new page. |
| Toast | `shell.css` `.toast` (~359) | Uses its own `--toast-bg`/`--toast-ink` pair (not `--ink`, which inverts) because it's dark-on-light in light mode specifically. |
| Bell / notifications | `shell.css` `.notif-bell` (~1750) | Permanent entry point (Emily's call, 2026-09-02) — stays visible even when the feed is empty; sits beside the ask bar. On the spruce rail it uses a lifted fill (`--spruce-raised`-family) since a spruce bell on spruce would disappear. |
| Cards / surfaces | `theme.css` `.card`, `.surface` (~350) | `--surface` background, 1.5px `--hairline` border, `--radius-card`. Lift is the hairline, not a shadow. |
| Buttons | `theme.css` `.btn-primary`/`.btn-secondary` (~301) | Primary = apricot fill + `--on-accent-ink` text, `--radius-action`; one per screen (Rule 5). Secondary = spruce outline/text-only. |
| Pills | `theme.css` `.pill-success`/`.pill-attention`/`.pill-neutral` (~345) | Status chips; accent fills always carry dark ink per Rule 1. |

---

## 6. Navigation rules (from the Nav Blueprint)

- **Four native screens, period: Today, Meals, Grocery, Kitchen.** Everything else is a *state* (a segmented control inside a tab), a *sheet* (slides over, dismisses down), or a *step* — never a new page with its own header or its own back button. If you're about to add a page with a back arrow to a sibling tab, stop — it should be a sheet or a segmented state instead.
- **The chat/ask input is part of the shell, not any one screen.** It sits above the tab bar on all four screens, same place, same size, always — the escape hatch from every hierarchy on the page. Its answer arrives as a sheet over whatever tab you're on; it never navigates you somewhere you didn't ask to go.
- **Cooking lives under Meals** (a `Plan | Cook` segmented state at `/week`), not as its own tab and not under Today — it's the same week's data with the recipes opened up, and two tabs over one dataset is exactly the kind of drift this rule exists to prevent.
- **Kitchen has no primary action.** Nothing on it is urgent by design (see Rule 5); it's the household's standing knowledge and settings, quiet on purpose.
- **Refresh policy** (what must be true after each kind of event):
  - *You change something* → the screen updates on tap, before the server confirms. A failed save reverts the row and says so; the common case never waits.
  - *One screen's change touches another* → every screen reads from the same store, so it's already correct (e.g. approving the week on Meals updates Grocery before you get there). This is why native screens matter more than they look like they should — an iframe can't share a store.
  - *You switch tabs* → nothing reloads. A quiet background check folds in server changes without the screen jumping under your thumb.
  - *You return after being away a while (a couple of minutes+)* → refetch everything once, quietly — someone else in the house may have shopped or cooked.
  - *You pull down* → pull-to-refresh exists on all four tabs and does the obvious thing, but it's a courtesy, never the only way to see the truth.
  - If you add a new native panel or a new action tool, wire it into `refreshStaleTabsFromActions()` (`shell.js`) — a panel that's built once and never told to refresh goes stale silently. See CLAUDE.md's "Tab panels build once per page load" gotcha for the exact failure mode this has already caused twice.

---

## 7. Learning etiquette

Pomona learns from what a household does — this section is how it's allowed to do that.

- **The loop is observe → infer → confirm → remember → correct.** The app notices a pattern, forms a guess, and checks it with one light, inline tap — never a form, and never asked twice for the same fact. Once confirmed, it remembers, and that answer stays one tap away from being corrected later. Skipping "confirm" isn't a shortcut version of this loop, it's a different one — see silent learning below.
- **Silent learning needs a visible flag and an undo, right at the point of use.** The app may act on a guess without asking first, but only where the result shows up on-screen as something the person can see and reverse in the moment — the grocery list's "usually here" / "not this time" is the reference pattern. Acting on a guess anywhere that isn't visible and reversible right there isn't silent learning, it's just guessing.
- **Repetition earns an offer, not a promotion.** Once something has been observed enough times to look like a rule ("that's two Thursdays — should I just assume it?"), the app asks, once, whether to make it standing. It doesn't quietly upgrade a guess into a fact on its own.

*This section and the voice-character addition just below it were folded in 2026-09-03 (both fully Emily-decided beforehand); per Governance below, the Brand Book canvas guide still needs the same two additions at its next design round.*

---

## 8. Voice

*The warmth-and-play addition below (and the do/don't pair) was Emily-decided on 2026-09-05, per Governance below.*

- Pomona's personality is **kind, dependable, helpful, thoughtful** — and understanding the user is part of being helpful, not separate from it. These four words are the test for any new copy: if a line doesn't read as at least one of them, rewrite it.
- Warm, first-person, concise — and a little playful, especially in titles (Emily, 2026-09-05). State the thing, then soften it — never the reverse, and never at length.
- Encouraging, never sarcastic or deadpan. A joke at the user's expense, or a flat/robotic aside, isn't playful — it's the opposite of kind.
- Exclamation marks and emoji are allowed, used sparingly. Rule of thumb: at most one per screen. Never in error or safety copy — that copy stays calm and plain, no exceptions (see the calm-in-trouble rule below, which still governs).
- Every word earns its place — Pomona doesn't add copy for the sake of tone or personality. If a line can be cut without losing meaning, cut it.
- Never promises a feature the app doesn't do, and never pre-announces what's coming. Describe only what's true right now — enthusiasm doesn't get to write checks the product can't cash.
- **Calm and reassuring, never cheery** (Emily, 2026-09-04) — this is about trouble, not tone in general: when something's wrong, reassurance comes from showing the thing is handled — a problem is always stated plainly and paired with its way out in the same breath — never from exclamation marks or enthusiasm. Keep stakes low and reversible where true ("nothing lost," "easy to change back").
- Time as a person would say it ("on the table by a quarter past seven"), not a timestamp ("Est. ready 7:15 PM").
- The answer, not the question ("All in the fridge," not "All required ingredients available").
- Names the person when there is one ("Trash night — Jamie's turn"), because the house has people in it.
- Never a dashboard/task-manager register: no "Action required," no "Task assigned," no clinical precision standing in for a person.
- **"Passphrase," never "password."** (See `static/login.html`, `app/households.py` — already consistent; keep it that way in anything new.)
- The app is **Pomona** — user-facing copy, marketing, onboarding. (Internal identifiers — env vars, DB names, file paths, code symbols — were deliberately left alone during the rebrand; don't rename those without a separate reason.)

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
