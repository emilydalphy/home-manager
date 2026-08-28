# Handoff: Home Manager — phone app + desktop grocery

## Overview

Home Manager is an assistant-led household app for a family of four. It answers "what's for dinner, what do we need, and what's already in the house" without the user having to maintain a plan by hand. The assistant proposes; the household confirms.

This package covers five surfaces:

| Surface | Where | File |
| --- | --- | --- |
| Today (triage) | phone | `Home Manager Prototype v2.dc.html` |
| This Week (day card + whole-week sheet) | phone | same |
| Grocery (to buy) | phone | same |
| Kitchen → Inventory, What we know | phone | same |
| Grocery list + Shopping mode | desktop | `Home Manager Desktop.dc.html` |
| Cook mode (step-by-step, hands-free) | phone | `Kitchen Cooker Redesign.dc.html` |

Supported sizes: **phone (390×844 reference) and desktop (1280×800 reference) only.** Tablet gets the desktop layout; no TV/kitchen-display target in v1.

Household model for v1: **two adults share one household** (Emily `E`, plum `#66304E`; Marcus `M`, green `#4D8A33`). Kids (Sam 7, Maya 4) are people the plan accounts for, not accounts. Every list row records who added it and renders their initial as a 26px avatar.

## About the design files

The `.dc.html` files in this bundle are **design references created in HTML** — prototypes that show intended look and behavior. They are not production code to copy.

The task is to **recreate these designs in the target codebase's existing environment** (React, Vue, SwiftUI, native, etc.) using its established patterns, component library, navigation, and data layer. If no environment exists yet, pick the most appropriate framework for the product (a phone-first app with a desktop web surface) and implement the designs there.

To view a prototype: open the `.dc.html` file in a browser. `support.js` must sit next to it. Everything in them is clickable — check items off, pick a Thursday dinner, open the week sheet, edit a fact, adjust an expiry date, run an import, drop into shopping mode.

## Fidelity

**High-fidelity.** Final colors, typography, spacing, radii, shadows, motion, and copy. Recreate pixel-accurately using the codebase's own primitives. Every hex value and type spec in this document is the intended value, not an approximation.

Two deliberate exceptions, called out where they appear: the receipt-photo capture and the "Scan a fridge photo" affordance are entry points only — no camera/OCR flow is designed yet.

---

## Screens / Views

### 1. Phone shell

Persistent frame around every phone screen, top to bottom:

- **Status bar** — 46px tall, `padding: 0 26px 4px`, time `Karla 700 14px #3A1F2E` left, battery/signal glyphs right.
- **Screen content** — `flex: 1`, vertical scroll, scrollbar hidden (`::-webkit-scrollbar { width: 0; height: 0 }`).
- **Docked ask bar** — always visible, `padding: 4px 18px 8px`. White card, `border: 2px solid #66304E`, `radius: 16px`, `padding: 14px 18px`. Placeholder "Ask or add anything…" `Karla 400 16px #6B5A63`; trailing 36×36 `radius: 10px` `#ECDFC8` button with `↑` in `Karla 800 17px #66304E`. Tapping anywhere on it opens the ask sheet.
- **Tab bar** — white, `border-top: 1px solid #ECE1D0`, `padding: 8px 12px 20px`, 4-column grid, `gap: 4px`. Each tab: 24px `radius: 8px` chip (active `#66304E`, inactive `#D8C8B4`), label `Karla 700 13px` (active `#66304E`, inactive `#6B5A63`), 7px gap. Badge: absolute `top: 2px; right: 14px`, `#E8562A` pill, `Karla 800 11px #FFF`, `radius: 10px`, `padding: 1px 6px`. Badge hides on the active tab.
- Tabs: **Today · Week · Grocery · Kitchen**. Today badges the count of open decisions; Kitchen badges the count of items expiring within 4 days.

Screen background `#F6EFE1`; phone corner radius 34px; outer page background `#E9E4D8`.

### 2. Today

**Purpose:** the only screen that says "here is what needs you." Everything else is reference.

Layout: single scrolling column, `padding: 6px 20px 8px`, `gap: 13px`.

- **Header** — kicker "Tuesday, Aug 25" `Karla 700 13px, letter-spacing .1em, uppercase #6B5A63`; heading `Quicksand 700 29px #3A1F2E`. Heading text is computed: `"You're clear"` / `"1 thing needs you"` / `"N things need you"`.
- **Needs-you band** — one card per open decision, `animation: popIn .22s ease`. White, `radius: 18px`, `padding: 16px 17px`, colored 4px top border.
  - *Dinner gap* — top border `#E8562A`. Kicker "Decide by 5pm · tee-ball night" `Karla 800 12px .09em uppercase #E8562A`. Title "Thursday has no dinner" `Quicksand 700 21px`. Two suggestion rows, `#F6EFE1`, `radius: 12px`, `padding: 13px 16px`, name `Karla 600 16px`, trailing "Pick" `Karla 700 15px #66304E`; hover `#EFE4CF`.
  - *Store run* — top border `#F0B429`, kicker `#B98A12`, body `Karla 400 15px #6B5A63`, then "Shop now" (plum `#66304E` bg, `#F0B429` ink) and "Later" (`#ECDFC8` bg, `#66304E` ink), both `radius: 12px`, `padding: 14px 20px`, `Karla 700 16px`.
  - Cards leave the band when resolved; the tab badge decrements.
- **Tonight card** — plum `#66304E`, `radius: 18px`, `padding: 20px`. Kicker "Tonight · 6:30 · 35 min" `Karla 800 13px .1em uppercase #F0B429`; dish `Quicksand 700 23px #FFF, line-height 1.28`; actions "Cook mode" (`#F0B429` bg / `#3A2A0A` ink, hover `#FFC63D`) and "Swap" (`1.5px solid rgba(255,255,255,.4)`, white ink, hover `rgba(255,255,255,.12)`).
- **Chores card** — white, `radius: 18px`, `padding: 18px 19px`. Title `Quicksand 700 19px`, counter "1 of 3" `Karla 700 15px #4D8A33`. Rows 52px, `border-bottom: 1px solid #F1E8DA`, 26px checkbox `radius: 8px` (`2px solid #C4B09C` → filled `#6FB84C` with white `✓` `Karla 800 15px`), label `Karla 400 16px` (done: `#A3939B` + line-through).
- **Grocery run row** — white card, `radius: 18px`, tappable to the Grocery tab. Title `Quicksand 700 19px`, sub `Karla 400 15px #6B5A63` = "N items · needed before Thursday" or "All picked up", trailing "Open" chip `#ECDFC8`/`#66304E`.

**✅ Built (Phase 1).** This screen was already substantially built from the earlier `design_handoff_shell` package, which shares the same brand/token system — needs-you band (dinner-gap + store-run cards), tonight card, chores card, and grocery-run row all match this spec already. No changes needed this pass beyond what's noted for This Week below (Today itself wasn't touched). Known, already-documented gaps: no fixed "6:30" time (no time-of-day field in the data model) and the grocery-row sub omits "needed before Thursday" (no due-date field on grocery items) — both real, deliberate judgment calls from the earlier build, not oversights.

### 3. This Week (option 6a, approved)

**Purpose:** one day in full detail, with the whole week one tap away. Replaces the earlier seven-stacked-cards scroll.

Layout, top to bottom:

1. **Header** — "The Dalphy House · week of August 24" `Karla italic 15px #8A6A52`; "This week" `Quicksand 700 29px`.
2. **Day rail** — `padding: 0 14px 10px`, 7 equal cells, `gap: 5px`. Each: `padding: 9px 0`, `radius: 9px`, centered `Karla 11px .06em` label + a 5px dot below (`margin: 5px auto 0`).
   - selected: bg `#F0B429`, label `Karla 800 #3A2A0A`, dot `#3A2A0A`, `transform: rotate(-2deg)`
   - day with an unfilled dinner: `1px dashed #E8562A`, label `#C0431E`, dot `#E8562A`
   - default: transparent, label `Karla 700 #8A7A82`, dot `#C4B09C`
3. **Day card** — `#FFFDF6`, `1px solid #E7DCC4`, `radius: 20px`, `box-shadow: 0 2px 0 #EFE4CF`, `padding: 22px 22px 24px`, centered column.
   - Stamp "TUESDAY, AUG 25" `Karla 700 10px .18em uppercase #8A7A83`.
   - Title `Quicksand 700 21px` — "Today's Table" for today, "Already served" for past days, "The table" otherwise.
   - Three courses, each a rotated badge + dish:
     - Breakfast — badge `#ECDFC8` bg / `#66304E` ink, `rotate(-2deg)`
     - Lunch — badge `#6FB84C` bg / `#FFF` ink, `rotate(2deg)`
     - Dinner — badge `#E8562A` bg / `#FFF` ink, `rotate(-2deg)`, label "Dinner ★" when filled, "Dinner" when empty
     - Badge spec: `Karla 800 10px .06em uppercase`, `padding: 6px 12px`, `radius: 20px`
     - Dish `Quicksand 600 16px` (dinner 19px), centered, `line-height 1.35–1.4`
     - Course separator: `height: 1px; background: #E8562A; opacity: .2; width: 36px; margin: 16px auto`
   - Dinner meta line `Karla italic 14px #8A6A52` ("on the table at 6:30 · 35 minutes").
   - Empty dinner: dish text "Nothing yet — tee-ball night" in `#C0431E` italic, meta "pick one and I'll sort the shopping", then the same two suggestion rows as Today's band.
   - Filled dinner (not a past day): "Cook this" (plum/gold) + "Swap it" (`1.5px solid #66304E`, plum ink, hover `#F4E7EF`), 50px tall, `radius: 13px`.
4. **"The whole week" row** — white card, `radius: 18px`, `padding: 17px 19px`. Title `Quicksand 700 18px`; sub = "One gap left · Thursday dinner" or "All seven days planned · 21 meals"; trailing "Open" chip. **This is the only entry point to the week sheet** (the approved decision: a button on the day card, not a swipe or a separate tab).
5. **Ask row** — white card, prompt "Tell me what's happening this week and I'll rebuild the plan." + "Ask".

#### Week sheet (the 6a grid)

Modal sheet over the phone frame. Scrim `rgba(58,31,46,.45)`, `animation: fadeIn .18s`; tapping the scrim closes. Sheet: `#FFFDF6`, `radius: 26px 26px 0 0`, `box-shadow: 0 -8px 30px rgba(58,31,46,.2)`, `padding: 16px 16px 20px`, `gap: 9px`, `animation: sheetUp .24s cubic-bezier(.2,.8,.2,1)`.

- Grab handle 44×4 `#DDCFB6`, centered, closes on tap.
- Title row: "The whole week" `Quicksand 700 20px`, hairline `#EADFC7`, "Aug 24–30" `Karla italic 14px #8A6A52`.
- Column headers: 40px spacer, then `Breakfast` / `Lunch` (flex 1) and `Dinner` (flex 1.25), `Karla 800 9px .14em uppercase` — first two `#A8825C`, dinner `#C0431E`.
- **Seven rows, no scroll.** Each row: `display: flex; gap: 8px; padding: 9px 4px; border-top: 1px solid #F1E8DA; radius: 10px`. 40px day label `Karla 12px` + three cells `Karla 13px, line-height 1.35`.
  - selected day row: bg `#F8F0DD`, day label `Karla 800 #66304E`
  - past day row: `opacity: .55`
  - day with no dinner: day label `#C0431E`; dinner cell "Open · tee-ball" italic `#C0431E`
  - dinner cell otherwise `Karla 600 #3A1F2E`
- **Tapping any row selects that day and closes the sheet** (approved: a meal tap jumps to the day's card — it does not open a swap sheet inline).
- Footer: "Share" (outlined plum) + "Back to {Day}" (plum bg / gold ink), 50px, `radius: 13px`. The second label interpolates the selected day's full name — mind the space.

Small type (13px) in the grid is intentional: it is the only way all 21 meals fit unscrolled at 390px. It is scan-only; every cell taps through to the full-size day card.

**✅ Built (Phase 1), mobile only.** This is a real rebuild, not a touch-up — the app's previous Week tab (from `design_handoff_shell`) was the "earlier seven-stacked-cards scroll" this option explicitly replaces. Built for `<1100px`: day rail (rotate(-2deg) gold selection, dashed-orange gap cells), single paper day card with rotated course badges and `36px` hairline separators, empty-dinner suggestion rows that fill inline (reusing the same `_suggest_quick_dinners`/`POST /api/needs-you/dinner` the Today needs-you band uses, now generalized in `get_week_menu` to return `dinner_suggestions` on any today-or-future empty-dinner day, not just the nearest 48h gap), the "whole week" row (gap-count + first-gap summary), and the bottom sheet (7 rows, tap-to-select-and-close, "Back to {Day}" with the space, "Share" wired to the existing share-link flow).

The **desktop (`>=1100px`) grid was deliberately left untouched** — this package's file table lists This Week as phone-only, and the existing 7×3 grid already serves "see the whole week at a glance" well on a wide screen, so rebuilding it wasn't in scope for this pass.

Judgment calls, none of them silent — see the comment block above `buildWeekPanel` in `static/shell.js`:
- Breakfast/lunch have no fill flow anywhere in this package (only dinner gets suggestion rows) — an empty breakfast/lunch renders as plain "Not planned yet," not a fake tappable "Pick" that would dead-end.
- "Cook this" needs a real cook-mode destination, and Kitchen's cook mode only knows how to start *tonight's* meal — so "Cook this" only appears on today's card (paired with "Swap it"); a filled future day gets "Swap it" alone.
- Per-card reasons ("tee-ball night") need a calendar/event signal this app doesn't have yet — omitted rather than invented, same as the earlier build's judgment calls on Today.

Sandbox-verified with Playwright at 390×844: day-rail selection and gap styling, suggestion-row fill (confirmed the grocery-relevant meal landed on the plan, toast fired, Today's own needs-you band refreshes if already built), the whole-week sheet's row-tap-selects-and-closes behavior and its live gap-count sub-line, and the "Back to {Day}" label. Also re-verified at 1280×900 that the desktop grid, header, and rail are pixel-identical to before — no regression from the mobile rebuild.

### 4. Grocery — phone (state 1 of 4: "to buy")

Only "to buy" is in scope for the phone. Triage, review, and in-store mode live on desktop (see below); the phone does not get separate screens for them in v1.

Layout: `padding: 6px 20px 8px`, `gap: 13px`.
- Header: "2 stores" kicker, "Grocery" `Quicksand 700 29px`, counter "1 of 7 got" `Karla 700 15px #4D8A33`.
- One white card per store, `radius: 18px`, `padding: 16px 19px 12px`. Store name `Quicksand 700 19px`, meta `Karla 700 14px #6B5A63` ("before Thursday" / "on the way home").
- Item rows 50px, `border-bottom: 1px solid #F1E8DA`: 24px checkbox `radius: 7px`, name `Karla 400 16px`, trailing reason `Karla 400 14px #9C8B93`. Checked: `#6FB84C` fill, white `✓`, name `#A3939B` + line-through, reason cleared.

**✅ Built (Phase 2).** The existing four-screen "To buy / By store / Shop / Review" phone build (design 13a) already covers this state and was left as-is — it already does everything section 4 describes (store cards, checkbox rows, "Already have it") and predates this package. No phone changes were needed this phase; phone-side Phase 2 work was the backend only (per-item `added_by` now flows through from the add form, and the new `stores`/`people` metadata tables exist for the desktop build below).

### 5. Kitchen (hub)

- Header kicker "Recipes · inventory · what we know", heading "Kitchen" `Quicksand 700 29px`.
- **Cooking tonight** — plum card, dish `Quicksand 700 22px #FFF`, "Start step 1" (gold) + "Ingredients" (outlined). "Start step 1" opens Cook mode (`Kitchen Cooker Redesign.dc.html`).
- **Inventory row** — white card; sub "N to use soon · N running low"; "Open".
- **What we know row** — white card; sub "People · Taste · Rhythm · Stores"; "Open".
- **Worth doing sometime** — `#ECDFC8` card, `radius: 18px`: "Scan a fridge photo so I stop suggesting what you already have." *Entry point only — no capture flow designed.*

Both sub-screens are pushed views inside the Kitchen tab with a `‹ Kitchen` back affordance (`Karla 700 14px #66304E`); the tab bar and ask bar stay put.

**✅ Built (Phase 4).** New `static/kitchen.html` replaces the old "prep this week" list as the Kitchen tab's default embed (see shell.js). Sandbox-verified: the Cooking tonight card reads the real tonight's-dinner data (same source as Today's dinner card), Inventory/What we know rows link out and show live summary stats. "Worth doing sometime" toasts its entry-point message rather than pretending a capture flow exists.

Deliberate scope call: Inventory and What We Know are real page navigations (`/inventory`, `/memory`), **not** true pushed views inside the Kitchen tab's own scroll area — the existing Grocery/Kitchen tabs already use the same "separate page, not a shell route" pattern (see shell.js's own comment predating this package), and building genuine SPA-style push/pop navigation into the shell was a bigger structural change than this phase's screens needed. The back arrow reads "‹ Kitchen" and returns there either way; the difference is invisible unless you're watching the URL bar. "Cook mode" entry points (Today's dinner card, Week's "Cook this") now pass `forceEmbedSrc` to `activateTab` so they jump straight past the new hub into the already-built Cook mode (cooker.html) instead of landing on the hub first.

### 6. Inventory (option 5a, approved)

**Purpose:** what's in the house, organised the way you look for food.

- Header: `‹ Kitchen`, "Inventory" `Quicksand 700 29px`, right-aligned summary `Karla 14px #6B5A63` "N to use soon · N running low".
- Three white cards in fixed order: **Fridge, Freezer, Pantry**. Card `radius: 18px`, `padding: 16px 18px 10px`. Header: name `Quicksand 700 19px`, hairline `#F1E8DA`, "N items" `Karla 14px #9C8B93`.
- Item row: `padding: 12px 0`, `border-bottom: 1px solid #F1E8DA`, tappable. 6px status dot, name `Karla 400 16px`, quantity `Karla 15px #8A7A82` ("3 gal", "1 half bag"), flag `Karla 700 13px .06em uppercase`.
  - expiring within 4 days → dot `#E8562A`, flag "use soon" `#C0431E`
  - low quantity → dot `#F0B429`, flag "low" `#B98A12`
  - otherwise dot `#C4B09C`, no flag

#### Item detail sheet (approved: tap a row for a detail sheet)

White sheet, `radius: 26px 26px 0 0`, `padding: 16px 20px 22px`, `gap: 14px`, same scrim/animation as the week sheet.

- Kicker `Karla 700 12px .1em uppercase #A8825C` = "{Location} · added this month".
- Name `Quicksand 700 25px`.
- **Quantity stepper** — label "How much is left" `Karla 16px #6B5A63`, then `−` / value / `+`. Buttons 44×44, `radius: 12px`, `#F6EFE1` bg, `Karla 800 20px #66304E`, hover `#EFE4CF`. Value 96px wide, centered, `Quicksand 700 18px`. Floor at 0.
- **Location picker** — three equal buttons, `padding: 13px 0`, `radius: 12px`, `1.5px solid`. Selected: bg `#F4E7EF`, border `#66304E`, `Karla 700 #66304E`. Unselected: bg `#FFF`, border `#E0D3BF`, `Karla 400 #3A1F2E`. Changing location re-groups the item immediately.
- **Best before** — `#F6EFE1` card, `radius: 14px`, `padding: 13px 13px 13px 17px`. Left: "Best before" `Karla 16px` + relative note `Karla 14px` ("in 2 days", "tomorrow", "in 3 weeks", "2 days past"). Right: `−` / date / `+` (44px white buttons, 86px date `Karla 700 16px`). One tap = one day. Date ink `#C0431E` when within 4 days, else `#3A1F2E`.
- **Actions** — "Add to grocery list" (plum/gold, becomes "On the list ✓" and inert once present) + "Used it up" (outlined plum; removes the item, closes the sheet, toasts "{Name} marked used up"). Both 52px, `radius: 13px`.

Relative-date wording rules: `<0` → "N days past"; `0` → "today"; `1` → "tomorrow"; `≤7` → "in N days"; `≤60` → "in N weeks"; else "in N months".

**✅ Built (Phase 4).** `static/inventory.html` rewritten from scratch — three fixed-order location cards, status dots/flags, and the full item detail sheet (quantity stepper, location picker with immediate re-grouping, best-before stepper with the exact relative-wording thresholds above, "Add to grocery list" → "On the list ✓", "Used it up"). Sandbox-verified via Playwright: stepping quantity, changing location, stepping best-before, the add-to-grocery inert state, and used-it-up's toast + removal all work against the real endpoints.

Deliberate scope calls:
- **Quantity stepper** operates on the existing freeform quantity string (e.g. "3 gal"), not a clean number+unit model — this app's inventory has always stored quantity as text (so "1 half bag" or "a splash" stay representable). A tap nudges the leading number by 1 and keeps whatever text follows it; a `-` tap on a non-numeric quantity ("some") is a no-op rather than guessing. Documented in `app/tools._step_quantity_text`.
- **"Running low"** has no stored threshold anywhere in this app (no min-quantity field exists) — it's a light client-side heuristic (leading quantity number ≤ 1), not a fabricated stat. A future phase could add a real per-item threshold if this turns out to matter.
- **Receipt/fridge/pantry photo scanning**, present on the old pre-redesign inventory page, is not part of this design package's spec for option 5a and was dropped from this page — the backend endpoints (`/api/inventory/scan-*`) are untouched and still work for the assistant/chat path, just not linked from this view anymore.

### 7. What we know (option 5d, approved)

**Purpose:** the household memory, split so nothing is a long scroll.

- Header: `‹ Kitchen`, "What we know" `Quicksand 700 29px`.
- **Four tabs** — `padding: 0 16px 12px`, `gap: 6px`, equal width, `padding: 11px 0`, `radius: 10px`. Active: `#66304E` bg, `Karla 800 13px #F6EFE1`. Inactive: `#FFF` bg, `Karla 700 13px #6B5A63`. Order: **People · Taste · Rhythm · Stores**.
- **Facts card** — white, `radius: 18px`, `padding: 17px 19px 11px`. Intro line per tab `Karla 15px #6B5A63`. Then one row per fact, `padding: 11px 0`, `border-top: 1px solid #F1E8DA`: text `Karla 16px, line-height 1.45` + trailing "Edit" `Karla 700 14px #A8825C`. The whole row is tappable.
- **Inline editor** (approved: edit any fact inline on its tab) — replaces the row with a **wrapping textarea**, not a single-line input: full width, `box-sizing: border-box`, `2px solid #66304E`, `radius: 12px`, `#FFFDF6` bg, `Karla 16px, line-height 1.45`, `padding: 12px 14px`, `height: 78px`, `resize: none`. Below it: "Save" (plum/gold, `radius: 11px`, `padding: 12px 18px`), "Cancel" (`Karla 700 15px #8A7A82`), and right-aligned "Delete" (`#C0431E`). Enter saves; Shift+Enter is a newline.
- **"+ Add something to this list"** — `Karla 700 15px #66304E` on a `border-top` row. Opens an empty editor. **An abandoned empty fact must never persist:** cancelling, switching tabs, or leaving the screen drops any fact whose text is empty.
- **Footer note** — `#ECDFC8` card, per-tab guidance (see COPY.md).

#### Stores tab extra: "What you get where"

Only on the Stores tab, below the facts card. White, `radius: 18px`, `padding: 17px 19px 15px`.

- Title "What you get where" `Quicksand 700 19px`; helper "Tap an item to move it to the other store, or × to forget it."
- One block per store, separated by `border-top: 1px solid #F1E8DA`, `padding: 13px 0`. Header: store name `Karla 700 16px`, habit meta `Karla 14px #A8825C` ("every other Saturday · bulk" / "on the commute · fresh"), count `Karla 14px #9C8B93`.
- **Item chips** — `#F6EFE1`, `radius: 20px`, `padding: 9px 8px 9px 13px`, `gap: 8px`, wrapping row with `gap: 7px`. Label `Karla 15px` (tap = move to the other store, with a toast). Trailing 22px `×` button, `radius: 12px`, `#E6DBC6` bg, `Karla 800 13px #66304E` (tap = forget).
- **"+ Add"** chip — `1.5px dashed #C4B09C`, `radius: 20px`, `Karla 700 15px #66304E`. Opens an inline input (`2px solid #66304E`, `radius: 20px`, placeholder "e.g. Rotisserie chicken") + "Add" button; Enter commits and keeps the field open for a run of entries.
- **"Import a whole list"** row — `#F6EFE1`, `radius: 14px`: title `Karla 700 16px`, sub "Paste your notes app, or photograph a receipt", trailing "Open".

#### Import sheet

White bottom sheet, `padding: 16px 20px 22px`, `gap: 14px`.
- Kicker "WHAT YOU USUALLY BUY", title "Import a list" `Quicksand 700 24px`.
- "These items belong to" + two store buttons (same selected styling as the location picker).
- **Textarea** — `2px solid #66304E`, `radius: 14px`, `height: 120px`, `resize: none`, `Karla 16px`, placeholder "Paste or type one per line — rotisserie chicken, frozen berries, coffee beans…".
- Parser: split on newlines **and** commas, strip leading bullets/numbering (`^[-•\s\d.]+`), trim, drop entries ≤1 character, drop case-insensitive duplicates already on that store's list. Toast "Added N items to {Store}".
- "Photograph a receipt instead" row → *entry point only*; currently toasts "Open your camera roll and I'll read the receipt". Implement as camera/library capture + OCR, or hide until that exists.
- Footer: "Cancel" (outlined) + "Save to {Store}" (plum/gold), 52px.

**✅ Built (Phase 4).** `static/memory.html` rewritten from scratch — four tabs, per-tab intro/footer copy from COPY.md, inline textarea edit/add/delete, and the Stores tab's chips (tap to move between stores, × to forget, "+ Add" inline input) plus the import sheet with the exact parser rules above (newline/comma split, bullet-strip, dedup, toast). Sandbox-verified via Playwright: adding a fact, cancelling an empty draft (confirmed it never persists), and adding a store chip all round-trip against real endpoints.

Deliberate scope calls, the largest of this phase:
- **People/Taste/Rhythm facts are a new, separate `facts` table** (see schema.sql's comment), not a UI on top of the existing structured preference fields (`members.dietary_restrictions_json`, `meal_preferences`' cuisine/protein/dislikes/cooking-time/etc.). Those structured fields already drive meal-plan generation and are untouched — still editable via chat exactly as before. DATA_AND_API.md's uniform freeform-list `Fact` model doesn't map cleanly onto that mix of a dict, several single-value settings, and per-member lists; forcing it would have meant either a risky rewrite of generation logic or an awkward partial mapping. So What We Know's People/Taste/Rhythm tabs are an additive **notes layer** — genuinely useful (anything typed there is saved and shown back), but not yet read by meal generation. A future phase could point generation at facts instead, but that's a planning-logic change, not a UI build.
- The **Stores tab** is the exception — it already had a natural, existing home (`usual_stores_json`/`store_typical_items_json`) and uses that real data directly, so "move to the other store" and the import sheet actually affect what the assistant knows.
- **"Photograph a receipt instead"** in the import sheet is left out — same reasoning as Inventory's dropped scan buttons; no OCR flow exists to hand it off to.
- `Fact.hard` (the allergy flag) exists as a column but isn't yet surfaced in the UI or read by anything — no editor control for it was in scope this phase, and nothing enforces it server-side yet either.

### 8. Grocery — desktop (option 5f, approved)

1280×800 reference, `#F6EFE1`, `radius: 20px`, three columns.

**Left rail — 214px**, white, `border-right: 1px solid #ECE1D0`, `padding: 24px 16px`.
- "STORES" label `Karla 800 11px .14em uppercase #A8825C`.
- Filter rows: `padding: 12px`, `radius: 11px`, label `Karla 16px` + count. Selected: bg `#F4E7EF`, `Karla 700 #66304E`. Hover `#F8F2E6`. Rows: **Everything**, each store, **Unassigned** (label and count in `#B98A12`).
- "SHOW" label, then **Already got** toggle: 18px checkbox `radius: 5px` (`2px solid #C4B09C` → `#6FB84C` + `✓`), label `Karla 16px`. Off by default; when off, purchased rows are hidden.
- Bottom note `#F6EFE1`, `radius: 13px`, `Karla 15px #4A3A43` — "Hiding N items you've already got." / "Showing everything, including what's already in the cart."

**Centre column** — header `padding: 24px 28px 14px`: meta "N of N got · N unassigned" `Karla 700 13px .1em uppercase #6B5A63`, "Grocery" `Quicksand 700 29px`; right side a 236px "Add an item…" input (`1.5px solid #D8C8B4`, `radius: 13px`, 48px) + "Add" button (`#ECDFC8`/`#66304E`). Enter or Add appends the item as **unassigned**, toasting "{name} added — pick a store for it".

- **Unassigned block (triage, folded into the list)** — pinned at the top, white, `1.5px dashed #C4B09C`, `radius: 20px`, `animation: popIn .2s`. Title "Unassigned" `Quicksand 700 20px` + helper "Pick a store and it moves into that list". Each row: 26px initial avatar, name `Karla 17px`, then one pill per store (`1.5px solid #D8C8B4`, `radius: 20px`, `padding: 9px 15px`, `Karla 700 15px #66304E`, hover `#F4E7EF`). Assigning moves the row into that store's card and toasts "{name} → {Store}". The block disappears at zero.
- **Store cards** — white, `radius: 20px`, `padding: 18px 22px 12px`. Header: store `Quicksand 700 20px`, meta "before Thursday · N to get", right-aligned "Shop this store" `Karla 700 15px #66304E` → shopping mode for that store. Under the header, a one-line legend: "Tick **Got it** as it goes in the cart. **Already have it** takes it off the list entirely." (`Karla 14px #9C8B93`, bold spans `#6B5A63`).
- **Aisle subheads** — `Karla 800 11px .14em uppercase #A8825C`, `padding: 13px 0 5px`, `border-top: 1px solid #F1E8DA`. Fixed aisle order: Produce, Bakery, Dairy, Meat, Frozen, Pantry, Household.
- **Item row** — `min-height: 58px`, `border-bottom: 1px solid #F8F2E6`, `gap: 14px`:
  1. **"Got it" pill** — the checkbox is labelled, not bare: `padding: 7px 13px 7px 9px`, `radius: 20px`, `1.5px solid` + 22px box `radius: 6px` + label "Got it" `Karla 700 14px`. Unchecked: bg `#FFF`, border `#E0D3BF`, ink `#8A7A82`. Checked: bg `#F0F7EA`, border `#B8DBA4`, ink `#4D8A33`, box `#6FB84C` with white `✓`. Hover border `#66304E`.
  2. Name `Karla 17px` (checked: `#A3939B` + line-through) — also toggles.
  3. Reason `Karla 15px #9C8B93`.
  4. 26px initial avatar (`#66304E` for E, `#4D8A33` for M), `opacity .55` when got.
  5. **"Already have it"** `Karla 700 14px #8A7A82`, hover `#C0431E` — **removes the row from the list** and toasts "{Name} off the list — I'll assume you have it". This is the "we already have one, stop asking" action; it is distinct from "Got it", which means "it's in the cart".
- **Empty state** — white card, "Nothing left on this filter" `Quicksand 700 21px` + "Switch to Everything on the left, or turn on “Already got”."

**Right panel — 304px**, white, `border-left: 1px solid #ECE1D0`, `padding: 24px 22px`, `gap: 17px`.
- "This trip" `Quicksand 700 21px` + note `Karla 15px #6B5A63`.
- "AISLE ORDER · {Store}" list: 6px aisle dot, name `Karla 16px`, count. Aisle dots: Produce `#6FB84C`, Bakery `#C9A24A`, Dairy `#F0B429`, Meat `#E8562A`, Frozen `#4F8FD6`, Pantry `#B0619A`, Household `#E8562A`.
- "COVERS THESE MEALS" — `#F6EFE1`, `radius: 16px`: "Tue tikka · Thu turkey bowls · Fri salmon".
- "SHARED LIST" — `#ECDFC8`, `radius: 16px`: "Emily and Marcus both add here. Initials show who asked for what."
- Bottom CTA "Start shopping {Store}" — `#F0B429` bg, `#3A2A0A` ink, 52px, `radius: 13px`, hover `#FFC63D`.

**✅ Built (Phase 2).** Sandbox-verified end to end (fresh DB, real endpoints, Playwright at 1280×900): left rail with live counts, an Unassigned triage block that folds into the list and disappears at zero, aisle-grouped store cards in the fixed aisle order, the "Got it" pill distinct from the "Already have it" text action, per-row avatars, the "Already got" toggle, the empty-state card, and the "This trip" panel with aisle counts and "Start shopping {Store}". Matches BUILD_ORDER.md's Phase 2 check exactly: assigning an unassigned item moves it into the right store/aisle and the block disappears at zero; "Already have it" removes the row entirely; "Got it" keeps it struck through, hidden unless "Already got" is on; an empty filter shows the empty-state card. Toggled purely by JS (window width + which phone-tab is active), not a CSS breakpoint, so Plan/Shop/Review stay on the existing phone-style overlays even at a wide viewport — matches the file table's Phase 2/3 split (Shopping mode is Phase 3).

Deliberate scope calls, to flag rather than silently absorb:
- **Store** is a name-keyed metadata table (`app/schema.sql` `stores`), not a real foreign key on `grocery_items` — avoids migrating every existing call site that already uses free-text store names. `habit`/`role`/custom aisle order exist in the schema but aren't editable from the UI yet.
- **"Already have it"** reuses the existing inventory-promoting endpoint (moves the item into Kitchen inventory), not the simpler soft-remove this section's raw text describes — a superset of the spec, not a swap.
- **Full soft-delete/undo** and the **Trip** entity are deferred — nothing in this phase's UI needs them; Trip belongs to Phase 3 (Shopping mode) where it's actually load-bearing.
- **"Covers these meals"** is left out — nothing currently links a grocery item back to the meal it came from, so it would be fabricated rather than derived.
- The identity switcher (header avatars) is desktop-only, per the spec's own scoping — the phone "Add an item" form still adds unattributed.

### 9. Shopping mode — desktop/tablet (option 5g, approved)

Same 1280×800 box, **plum `#66304E` background** — the whole-screen colour change is the "you're in the store" signal.

- Header `padding: 26px 34px 20px`: kicker "Shopping · {Store}" `Karla 800 12px .14em uppercase #F0B429`; heading "{First unfinished aisle} first" `Quicksand 700 30px #FFF` (or "Everything's in the cart"); counter `Quicksand 700 28px #FFF` with `/ total` in `rgba(255,255,255,.5) 20px`; "Done shopping" outlined button (`1.5px solid rgba(255,255,255,.4)`, 50px).
- Body: two-column grid, `gap: 20px`, one `#F6EFE1` card per aisle, `radius: 22px`, `padding: 20px 26px 12px`. Card header: "PRODUCE · 2 items" `Karla 800 12px .14em uppercase #A8825C`, hairline `#E2D7C4`, "N left" `Karla 700 14px #8A7A82`.
- **Oversized rows** for arm's-length use: `padding: 18px 0`, 34px checkbox `radius: 10px` (`2.5px solid #C4B09C` → `#6FB84C` + white `✓ 18px`), name `Karla 600 22px`, reason `Karla 16px #8A7A82`. Checked: `opacity .5`, `#8A7A82`, line-through. Whole row is the hit target.
- Footer: 8px progress track `rgba(255,255,255,.18)` with `#6FB84C` fill at `got/total`; note "{Other store} next · N items" or "Last stop of the trip"; button "Next store" → switches store, or "Back to list" when nothing is left elsewhere.
- "Done shopping" returns to the list and toasts "Trip saved — I'll remember what you bought where".

**✅ Built (Phase 3).** Sandbox-verified end to end (fresh DB, real endpoints, Playwright at 1280×900): heading tracks the first unfinished aisle and switches to "Everything's in the cart" once every row is checked; the counter and progress-bar fill match got/total; "Next store" hands off to the next store with remaining needed items and becomes "Back to list" once none are left; "Done shopping" returns to the desktop Grocery list with the trip toast. Entry point is the desktop Grocery trip panel's "Start shopping {Store}" only — the phone's existing in-store screen (design 13a, predates this package) is completely untouched, matching section 4's note that triage/review/in-store mode "live on desktop" in v1.

Deliberate scope calls:
- Rows reuse the existing needed↔`in_cart` toggle the phone shop screen already had — checking a row here is the same state the phone's shop screen uses, so nothing forks.
- **Inventory promotion happens per item at checkoff**, not batched at trip close: marking a grocery item purchased already calls the app's existing inventory-add logic. "Done shopping"/"Next store" batch-flip `in_cart` → `purchased` (which triggers that same per-item promotion) and then record a closed `shopping_trips` row — bookkeeping, not a second promotion pass.
- **Trip** is now a real table (`shopping_trips`: store, item_count, started_at, finished_at) rather than the fuller entity DATA_AND_API.md sketches (no `itemIds` list) — nothing in this phase's UI shows trip history back, so there was nothing to read it into yet. A future phase could extend it if a "past trips" view gets designed.
- "COVERS THESE MEALS" region from the trip panel (section 8) still isn't shown here either, for the same reason it was skipped in Phase 2 — no link from a grocery item back to the meal it came from exists yet.

### 10. Cook mode

See `Kitchen Cooker Redesign.dc.html` — step-by-step, hands-free, and per-step feedback flows are already built there in both phone and desktop form. Entry: Today's "Cook mode", Kitchen's "Start step 1", the week card's "Cook this".

---

## Phase 5 — Assistant patch contract, First run, Notifications

**✅ Assistant patch contract (DATA_AND_API.md).** No new code needed here. The spec's `{ reply, card?, patch }` shape — "every patch that changes data produces a card whose View lands on the changed tab" — is already satisfied by the existing `ChatAction`/`summarize_chat_actions()` mechanism (built earlier in this app's history, predating this design package). Rather than a hand-authored patch object, each assistant turn's card is derived from the *actual tool calls the agent executed* that turn, deduped by category, each carrying a kicker/change line and a tab-or-href destination — arguably a more reliable version of the same guarantee, since the card can never claim a change the backend didn't really make. No changes made; documenting this as already-satisfied rather than rebuilding it.

**✅ First run (FIRST_RUN.md), partial.** `static/onboarding.html` (from an earlier design package) already covers most of the spec's intent: household/members/dietary-restrictions/meal-preferences steps, then a real call to `/api/onboarding/generate-first-plan` that generates and reveals an actual first week inline — not a placeholder. Given FIRST_RUN.md's own stated philosophy ("everything else in What We Know gets learned over time through the ask bar and through corrections"), this pass made only the highest-value, lowest-risk change: onboarding's three completion redirects now go to `/week?firstplan=1` instead of `/`, and This Week shows a one-time toast ("Here's a first pass — change anything and I'll re-plan around it.") on arrival, then cleans the query string. **Deliberately deferred:** a "where you shop" onboarding step and a "name one thing that's fixed" step (both real gaps — the new What We Know Stores/Rhythm tabs from Phase 4 now provide a legitimate post-onboarding path to the same data instead); a two-adult email-invite flow (no auth/multi-user infrastructure exists to build this on); and "leave one dinner deliberately open" as an explicit first-plan generation behavior (not verified either way in the existing generator).

**✅ Notifications (NOTIFICATIONS.md), 3 of 4 types, live feed instead of push.** No push infrastructure exists anywhere in this codebase — no service worker push handler, no VAPID keys, no background scheduler process — and standing that up (browser Push API + service worker + a persistent scheduler running on Railway) is its own multi-day subsystem, out of scope for this pass. Built instead: a bell icon (shell-level, floats above both phone and desktop layouts) that opens a small popover panel of **live, on-demand-computed** notifications — the same underlying detections the app already uses elsewhere, just surfaced as a feed:

- **#1 Dinner decision nudge** — reuses the existing needs-you dinner-gap detection.
- **#2 Expiring soon** — reuses `get_expiring_soon(days=2)`, batched into one notification with singular/plural copy matching COPY.md's pattern.
- **#3 Weekly plan ready** — a plan for a not-yet-started week, created within the last 24h, with ≥2 dinners (the spec's own "don't notify for an empty plan" rule).
- **#4 "The other adult changed something" — not built.** This needs an activity-log/attribution system (who made which change) that doesn't exist for meal-plan or shopping-trip mutations; there's currently no concept of "the other adult" distinct from "you" at the data layer.

Each notification has a stable `key` so dismissing one doesn't suppress a *different* future occurrence of the same type (e.g. dismissing today's expiring-soon nudge doesn't hide tomorrow's), backed by a new `notification_dismissals` table. Because this is a live-computed feed rather than scheduled push, most of NOTIFICATIONS.md's "Rules across all four" section doesn't apply and wasn't implemented: quiet hours, the 2-per-day cap, and the permission-ask flow are all about *when a push arrives on a lock screen* — there's no delivery moment to gate when the bell just reflects current state on demand. The one rule that *does* carry over — no badge-only notifications, everything is a sentence — is preserved. The bell icon itself now uses the shell's real line-icon system (matching the tab bar/rail's `ICONS.*` SVGs and the active-tab plum-bg/gold-ink chip treatment) instead of the plain oat-cream circle + 🔔 emoji from the first pass — feedback from testing that the original read as generic, not brand-matched.

**Bugs found in testing and fixed post-launch:**

- The bell stayed visible forever once it had appeared once, even after every notification was dismissed/resolved — `loadNotifications()` unconditionally unhid it on every successful fetch instead of hiding it again at zero. Fixed to key visibility off the current count each time, and to auto-close the panel if it's open when the count drops to zero.
- **The chat assistant had no way to read or write People/Taste/Rhythm facts** — `add_fact`/`get_facts`/`update_fact`/`delete_fact` were built as backend functions and wired into the manual add/edit UI on the What We Know page itself, but never added to `agent.py`'s tool list. Since the app is chat-first ("Ask or add anything…"), this meant telling the assistant "remember that Sam is allergic to peanuts" silently did nothing — only typing directly into the People/Taste/Rhythm "+Add" boxes on the page persisted anything, which is why Stores (backed by older, already-wired-up data) worked while the three new tabs looked empty. Fixed by adding all four as real tool definitions + dispatch entries, verified end-to-end (add → list → update → delete) against a live DB.
- **The service worker was serving stale `shell.js`/`shell.css` forever on any device that had it installed before this redesign.** `service-worker.js`'s fetch handler only treated page navigations and `.html` requests as network-first; `.js`/`.css` fell into the same cache-first bucket as icons/manifest. Those files change on every deploy just like the pages that load them, so a device with an already-cached copy kept running whatever JS/CSS was live at install time indefinitely — a fully up-to-date `shell.html` silently running old `shell.js` logic underneath it. This explains "This Week doesn't update on desktop but does on mobile": whichever device had the service worker installed longest was the stale one, not a per-platform difference. Separately, its registration call also only lived in the old `static/index.html`, which stopped being the app's real entry point when the app-shell redesign moved "/" to `shell.html` — so a device that installed fresh *after* that redesign never registered a service worker at all, losing offline/PWA-install behavior silently. Fixed both: registration moved into `shell.js` (restores it for new installs), `.js`/`.css` added to the network-first bucket, and `CACHE_NAME` bumped to `v3` so the browser detects the changed service worker file and clears out anyone's old stale cache on next visit.
- **Kitchen's "‹ Kitchen" back link nested a whole second app shell inside itself.** `inventory.html`/`memory.html` are reached two ways: standalone via the desktop rail's direct link (a real top-level page, `href="/kitchen"` on the back link is correct there), and as a Kitchen-hub "pushed view" inside `kitchen.html`'s own iframe (`window.location.href = '/inventory'`, staying inside that iframe). The back link's hardcoded `href="/kitchen"` didn't distinguish the two — followed from inside the iframe, it loaded the *full* `shell.html` (rail, tab bar, ask bar, the works) a second time nested inside the already-small Kitchen tab panel, stacked underneath the real outer shell. Confirmed via a sandbox screenshot (two stacked "Ask or add anything…" bars), not a hypothetical. Fixed by detecting `window.self !== window.top` and pointing the back link at `/static/kitchen.html` (the hub's own embed src, staying inside the same iframe) only in that case; the standalone rail-link path is untouched.

**Confirmed functionality loss, not yet restored:**

- **Voice dictation on the main chat input.** `static/index.html` (the pre-shell full chat page) had a mic button using the Web Speech API to dictate directly into the chat box. When chat moved into the ask sheet (an earlier app-shell step, predating this package), the markdown renderer and loading-phrase picker were ported over but the mic button explicitly wasn't — flagged in that step's own code comment as "a reasonable follow-up but out of scope." Since `index.html` is no longer reachable through normal navigation (`/` now serves `shell.html`), that mic button isn't reachable by any real user path any more, in the ask sheet or anywhere else. Distinct from the hands-free voice *session* in Grocery/Cooker (`voice-session.js`, Phase 5 of a different, earlier build) — that one's untouched and still works, since `grocery.html`/`cooker.html` are still embedded unmodified.
- **No way back into the household-setup wizard.** `index.html`'s nav always carried an "Edit household setup" link straight to `/onboarding`. The new shell only ever routes there automatically, once, when a household has zero members — nothing in the rail, tab bar, Kitchen hub, or What We Know page links to it afterward. The underlying data is still editable (via chat — `add_member`, `edit_preference`, etc. all still work), but the dedicated step-by-step wizard UI has no discoverable entry point once setup is done; `/onboarding` only works if someone types it into the address bar directly.
- **`static/grocery-legacy.html` is dead code**, not a loss — the pre-Phase-2/3 grocery page, fully superseded by the current `grocery.html`, kept on disk but reachable from no route or link anywhere. Noted here only so it isn't mistaken for something that broke.

**Round 3 of testing feedback, all implemented:**

- **Ask-sheet chips now disappear once the household actually engages.** They were staying visible above an ongoing conversation, which read as if nothing had been said yet. `sendAskMessage` now hides them (`hideAskChips()`) the first time a message goes out, whether from typing or from tapping a chip itself.
- **Grocery's "Plan your stops" screen was hard to discover from Review** on mobile. Its only entry point was a tab labeled "By store" — a different name than the screen's own "Plan your stops" heading — and the Review screen's "Confirm list" button returns to To buy, not forward to Plan stops, so there was no path from Review to it either. Fixed: the tab is now labeled "Plan stops" to match, and Review carries an always-visible "Ready to shop? Plan your stops by store next." card at the top that jumps straight there.
- **This Week's "Tell me what's happening this week" row was redundant** with the shell's own docked "Ask or add anything…" bar, which is present on every tab including this one — two chat entry points stacked on one screen. Removed the week-specific row; the docked bar covers the same job.
- **The Week tab is now labeled "Meals"** — tab bar chip, desktop rail row, and the in-page heading all updated. The internal route (`/week`) and code (`key: 'week'`, `week_start_date`, etc.) are untouched — this was a display-label change only, not a URL/data rename.
- **Photo capture (receipt, fridge, and pantry scanning) is restored on Inventory.** This was a deliberate Phase 4 scope cut (design_handoff_home_manager's own spec called the receipt/fridge-photo affordances "entry points only — no capture flow designed"), documented as dropped at the time — but the backend (`/api/inventory/scan-receipt`, `scan-fridge`, `scan-pantry`, `confirm-scan`, all in `app/main.py`/`app/agent.py`) was never touched or removed. Feedback was clear this needs to come back regardless of the original spec's scope, so it's UI-only work: three buttons under "Add to inventory" open the device camera/photo library, upload to the matching scan endpoint, and show a review sheet (editable name/quantity/category/location per item, a "double-check" badge on anything the model flagged low-confidence, remove-before-saving) before anything writes to `inventory_items` — same review-before-save contract the backend was already built around. Verified with a mocked scan response end-to-end (review → edit/remove → save → shows up in the right location card) since this sandbox has no `ANTHROPIC_API_KEY` to exercise the real vision call; the graceful-failure path (no key / API error) was verified directly and shows a plain "Couldn't read that photo" message rather than crashing.
- **"Add to inventory" moved to the top of the page**, directly under the heading — it and the new scan buttons were previously below the entire stacked location list, easy to miss on a long inventory.
- **Fridge/Freezer/Pantry are no longer just one long scroll.** A pill row (only showing locations that actually have items) now sits above the cards; tapping one scrolls straight to that section and briefly highlights it. The stacked-card layout itself is unchanged — this is a navigation aid on top of it, not a restructure, since collapsing them into single-section-at-a-time tabs would have hidden the overview a quick glance currently gives.

**Round 4 of testing feedback:**

- **Voice dictation restored on both chat-input surfaces.** This was the "confirmed functionality loss" flagged above. A mic button (Web Speech API, `SpeechRecognition`/`webkitSpeechRecognition`) is now on the mobile ask sheet's composer (`#ask-mic-btn`) and the desktop Today panel's composer (`#today-ask-mic-btn`) — tap to start, tap again to stop, live interim transcript fills the input as you speak. On a browser/OS with no Web Speech support (iOS Safari), the button instead just focuses the input and hints that the device's own keyboard mic works there, since iOS has no in-page dictation API to hook into. Verified in-sandbox: button renders and is clickable on both surfaces; since the sandbox browser has no real microphone permission, the exercised path is the "permission denied" branch, which surfaces the intended human-readable toast rather than a raw browser error — the happy-path transcript-filling logic is the same well-tested pattern ported from the old `index.html` implementation, unchanged.
- **"I made a plan with the AI but it's not pulling into the weekly view" — root-caused and fixed.** This was the same one-fetch-per-page-load architecture explained earlier (see the This-Week/desktop bug above) but from a new trigger: any tab already built before a chat turn changes its data stays stale even if the household never left that tab — e.g. sitting on Today, asking the assistant to plan the week, and switching to Meals for the first time afterward, where it had never been built at all, would actually be fine; the stale case is when Meals or Today had already been visited once this page load before the chat-driven change happens. Fixed with `refreshStaleTabsFromActions()`: after every chat turn, it looks at the same `actions[].tab` field the action-card UI already uses (from `summarize_chat_actions()`/`_categorize_tool`) and, for any already-built panel whose tab a just-executed tool touched, re-runs that panel's normal load functions (`loadWeekMenu` for Meals; `loadNeedsYou`/`loadTonightsDinner`/`loadGrocerySummary` for Today) — the same refresh path tab-switching itself would trigger on a panel that hadn't been built yet, just invoked proactively instead of waiting for a switch that might not happen. Verified end-to-end in a sandbox Playwright run: visited Meals while empty ("No meal plan yet"), seeded a plan and sent a mocked chat action tagged `tab: "week"`, then returned to the already-built Meals tab without reloading the page — the new plan appeared immediately.
- **"The chat knows about a meal plan the app doesn't show" — root-caused and fixed properly this time.** First flagged early in testing and explained away then as "both the chat and the Meals tab pick the household's most-recently-*created* plan, not the plan matching this actual calendar week" — documented at the time as a known ambiguity rather than fixed. It resurfaced (asking "what's my meal plan for this week" got an answer describing meals the Meals tab didn't show) because that's exactly what the ambiguity predicts: `get_weekly_plan`/`get_week_menu`/`set_week_constraints`/`get_prep_schedule` all independently picked "whichever plan has the latest `created_at`" whenever no specific plan id was given — which drifts from "this week" the moment *any* other plan exists with a later `created_at`, e.g. a plan generated ahead of time for a future week, or an old leftover plan that was never cleared. Reproduced directly in the sandbox: seeded the real current week's (mostly empty) plan first, then a second plan for a future week created afterward — the assistant's "what's my plan" answer described the *later-created* future plan's meal, while the Meals tab correctly showed the real current week empty, exactly matching the report. Fixed at the source with one shared resolver, `_current_weekly_plan_row()`: prefer the plan whose week actually contains today; only fall back to most-recently-created when no plan covers today at all (so a household with no current-week plan still correctly gets "no plan" everywhere, instead of some other week's plan being presented as if it were this one). Wired into all four call sites that used to duplicate the old raw "most recent" query. Verified the fix changes behavior with the exact reproduction above (old logic resolves to the wrong future plan; new logic resolves to the real current-week one) and confirmed `/api/week-menu` returns the correct plan end-to-end.
- **Grocery quantities weren't reconciling into anything a shopper could act on.** Flagged directly from live testing: an onion accumulating "1 large + 1/2" and salsa accumulating to "52 tbsp". Two separate causes, both in `app/tools.py`'s quantity-merge logic (`_try_consolidate_quantity`/`_subtract_quantity`, used by `add_grocery_item` and every path that adds a recipe's ingredients to the list): (1) size descriptors ("1 large" onion) were parsed as a literal unit string `"large"`, which never matched a following unit-less amount like `"1/2"`, so the two fell into the no-safe-merge fallback that just concatenates both strings with " + " — fixed by mapping size words (large/medium/small/whole/jumbo/xl) to no-unit in `_UNIT_ALIASES`, so they're recognized as the same kind of quantity as a bare count and merge normally (1 + 0.5 → 1.5). (2) Even once merged, an amount like "1.5" or "52 tbsp" isn't how anyone actually shops: nobody buys half an onion or measures out tablespoons at a store. Added `_humanize_grocery_quantity()`, now the final step of every grocery-line merge/subtract/add: a discrete count (no unit, or any unit not in a measurable group — "large", "clove", "can", etc.) rounds *up* to a whole number, since fractional real-world items can't be bought; a measurable unit (tsp/tbsp/cup, oz/lb, g/kg, ml/l) is rolled up to the largest unit it comfortably fits and rounded to the nearest quarter — so 52 tbsp displays as "3.25 cups" instead of a number nobody would ever measure with a tablespoon. This only touches grocery-list quantities; `scale_recipe`'s own ingredient scaling (for following a recipe while cooking, not shopping) is untouched and still shows precise amounts in whatever unit the recipe was written in. Existing bad-looking lines already on a live list (like the "1 large + 1/2" onion) don't need a manual fix — the existing `repair_grocery_quantities()` tool re-parses and re-merges any "+"-joined line through this same updated logic, so asking the assistant to "clean up the grocery list" (or it running that automatically) resolves them. Verified directly against the merge/format functions and end-to-end through `add_grocery_item` for both the onion and salsa cases.
- **A generated week could come back completely empty with no error surfaced anywhere.** Found by checking the live app directly (`/week` showing every slot as "Not planned"/"Choose a ___" for the real current week) after the previous fix above made plan selection correct — this was a second, real bug the mismatch fix had been masking: `agent.py`'s `generate_weekly_plan` used to create the `weekly_plans` row *first*, then call the LLM to actually generate the days, then loop over the result inserting each meal. If that LLM call came back empty — hit `max_tokens` mid-response (a genuinely truncated week can happen; `generate_weekly_plan_llm` only logs a warning, it doesn't raise), or the API call itself failed — the loop over an empty list simply did nothing, leaving the already-committed plan row as a permanent, real, empty "current week" plan with no error shown to the household anywhere. Confirmed live: the production DB had exactly this — a real weekly plan row for the correct current week, zero meals in it. Fixed by reordering: the LLM call now runs *before* anything is written, and an empty result raises a clear error ("nothing was saved; try generating the week again") instead of ever creating the shell row — the existing tool-call error handling in the chat loop (`agent.py`'s per-tool `try/except`) already surfaces a tool error back to the model as a normal `is_error` tool result, so this becomes an honest "that didn't work, want me to try again?" in the chat instead of a silent empty week. Verified in the sandbox both ways: a mocked empty LLM response now raises and leaves zero `weekly_plans` rows (previously left one), and a mocked real response still saves normally. The stray empty plan already sitting in production isn't cleaned up automatically (there's no delete-plan tool, and it's harmless clutter) — asking the assistant to plan the week again now creates a fresh, populated plan that supersedes it under the existing "prefer the most-recently-created plan for the matching week" rule from the fix above.

---

## Interactions & behavior

**Navigation**
- Tab bar switches the four tabs; switching tabs closes any open sheet and pops Kitchen back to its hub.
- Kitchen sub-screens are pushed views with `‹ Kitchen`; no separate tab.
- Assistant reply cards carry a destination — tapping one closes the sheet and lands on the tab that changed.

**Sheets** (ask, week, inventory item, import) — all four share: `fadeIn .18s ease` scrim `rgba(58,31,46,.4–.45)`, `sheetUp .24s cubic-bezier(.2,.8,.2,1)` panel, tap-scrim-to-close, 44px grab handle that also closes, `radius: 26px 26px 0 0`. Only one open at a time.

**Motion inventory** — that's the whole set:
```
sheetUp  .24s cubic-bezier(.2,.8,.2,1)   translateY(102%) → 0
fadeIn   .18s ease                       opacity 0 → 1
popIn    .20–.22s ease                   opacity 0 + translateY(8px) → 0
```
Hover states are instant colour swaps, no transitions. Cards entering the needs-you band use `popIn`.

**Toasts** — phone: absolute, `left/right: 20px`, `bottom: 112px` (clear of the ask bar and tabs), `#3A1F2E` bg, white `Karla 600 15px`, `radius: 14px`, `padding: 14px 17px`, `popIn .2s`, auto-dismiss at **2200ms**, one at a time (a new toast replaces the old and resets the timer). Desktop: fixed, bottom-centred, `bottom: 34px`, plus `box-shadow: 0 10px 30px rgba(58,31,46,.3)`.

**Assistant (ask sheet)** — chips "Sam has tee-ball Thursday", "Add tortillas", "Swap tonight"; free text input; each reply is a bubble plus an optional action card (kicker `Karla 800 12px .09em uppercase #4D8A33`, body, "View"). User bubbles: plum bg, white ink, `radius: 16px 16px 4px 16px`, right-aligned. Assistant bubbles: `#F4F0E6`, `radius: 16px 16px 16px 4px`, left-aligned. Prototype intent-matching is a stand-in for a real model call; the *shape* to preserve is **reply + one card that names what changed and links to it**.

**Optimism** — every check, pick, assign, edit, and stepper tap applies locally and immediately, then reconciles. No spinners anywhere in the design; if the network is slow, keep the optimistic state and reconcile silently.

**Responsive** — phone layouts hold from 320 to ~700px (the week grid's 13px cells are the tightest constraint; below 360px shorten dish names before shrinking type). Desktop layouts hold from ~1100px up; below that, drop the right panel first, then the left rail becomes a filter row above the list. Tablet uses the desktop layout.

**Error/empty states designed:** all-clear Today ("You're clear"), empty grocery filter, "All picked up", inventory group with nothing in it (hide the card), zero unassigned (hide the block), nothing left at the other store ("Last stop of the trip"). Not designed: offline, auth failure, sync conflict between the two adults — spec those with engineering.

## State management

Phone (`Home Manager Prototype v2.dc.html`):

| State | Type | Notes |
| --- | --- | --- |
| `tab` | `today \| week \| grocery \| kitchen` | persistent tab |
| `sub` | `null \| inventory \| know` | Kitchen pushed view |
| `weekDay` | 0–6 | selected day; defaults to today |
| `weekSheet` | bool | 6a grid open |
| `invOpen` | index \| null | inventory detail sheet |
| `knowTab` | `People \| Taste \| Rhythm \| Stores` | |
| `knowEdit` | `{tab, idx}` \| null | which fact is being edited |
| `knowDraft` | string | textarea buffer |
| `storeMap` | `{store: string[]}` | usual items per store |
| `addStore` / `addDraft` | string \| null / string | inline chip add |
| `importOpen` / `importStore` / `importText` | bool / string / string | import sheet |
| `askOpen` / `draft` / `messages` | bool / string / array | assistant |
| `dinnerOpen` / `costcoOpen` / `thu` | bool / bool / string \| null | open decisions; `thu` cascades: setting it moves salmon to Saturday and pizza to Friday |
| `chores` | `{name, done}[]` | |
| `items` | `{name, store, why, got}[]` | grocery |
| `stock` | `{name, where, qty, unit, days}[]` | `days` = days from today; expiry is stored as a date in production |
| `know` | `{tab: string[]}` | household facts |
| `toast` | string | 2200ms timer |

Desktop adds `view` (`list \| shopping`), `shopStore`, `filter`, `showGot`, `draft`, and gives each item `aisle` and `who`.

Derived, never stored: open-decision count (Today badge), expiring count (Kitchen badge), per-store counts, aisle groupings, progress percentage, "N of N got", relative expiry wording, week-gap count.

## Design tokens

**Colour**
```
Page ground      #E9E4D8
Screen ground    #F6EFE1
Card / surface   #FFFFFF
Paper (menu)     #FFFDF6
Warm surface     #ECDFC8   hover #E2D2B6
Selected tint    #F4E7EF   (plum wash)
Row highlight    #F8F0DD   hover ground #F8F2E6 / #EFE4CF
Plum             #66304E   hover #7A3A5D
Plum deep (ink)  #3A1F2E
Gold             #F0B429   hover #FFC63D   ink-on-gold #3A2A0A
Gold ink (text)  #B98A12
Orange           #E8562A   ink #C0431E
Green            #6FB84C   ink #4D8A33   tint bg #F0F7EA  tint border #B8DBA4
Body ink         #3A1F2E
Muted ink        #6B5A63 · #8A7A82 · #9C8B93 · #A3939B (disabled/struck)
Label brown      #A8825C   italic brown #8A6A52   stamp grey #8A7A83
Borders          #E7DCC4 · #ECE1D0 · #F1E8DA · #F8F2E6 · #E0D3BF · #D8C8B4 · #C4B09C · #DDCFB6 · #EADFC7 · #E2D7C4
Scrims           rgba(58,31,46,.40) · rgba(58,31,46,.45)
Aisle dots       Produce #6FB84C · Bakery #C9A24A · Dairy #F0B429 · Meat #E8562A · Frozen #4F8FD6 · Pantry #B0619A · Household #E8562A
People           Emily #66304E · Marcus #4D8A33
```

**Type** — two families only.
```
Quicksand 600/700 — headings, dish names, card titles, numerals in counters
Karla 400/600/700/800 — body, labels, meta, buttons, kickers

Screen title      Quicksand 700 29–31px
Card title        Quicksand 700 19–21px
Dish (hero)       Quicksand 700 22–23px   Dish (card) Quicksand 600 16–19px
Desktop title     Quicksand 700 29–30px   Shopping row Karla 600 22px
Body              Karla 400 16–17px / 1.45–1.55
Meta / secondary  Karla 400/700 14–15px
Kicker            Karla 700/800 12–13px, letter-spacing .09–.14em, uppercase
Micro label       Karla 800 9–11px, letter-spacing .14–.28em, uppercase
Button            Karla 700 15–16px
Grid cell (6a)    Karla 400/600 13px / 1.35
Italic accent     Karla italic 14–16px #8A6A52 (house voice)
```
Minimum sizes: 13px only inside the week grid; 14px elsewhere on phone; hit targets ≥44px (the 34px shopping checkbox sits inside a 70px row).

**Spacing** — 4px base: `4 · 5 · 6 · 7 · 8 · 9 · 10 · 11 · 12 · 13 · 14 · 16 · 18 · 20 · 22 · 24 · 26 · 28 · 30 · 34 · 44`. Phone screen padding `6px 18–20px 8px`, card padding `16–22px`, card gaps `12–14px`, desktop panel padding `24px 22–28px`, desktop column gap `18–22px`.

**Radius** — `34` phone frame · `26` sheet top · `22` shopping aisle card · `20` cards, chips, badges · `18` phone cards · `16` inner cards, ask bar · `14–13` buttons, notes · `12–11` small buttons, rows · `10–8` checkboxes, tab chips · `7–5` small boxes · `4–3` grab handles.

**Shadow**
```
Device / panel   0 18px 50px rgba(58,31,46,.20)
Phone (page)     0 18px 50px rgba(58,31,46,.22)
Paper lift       0 2px 0 #EFE4CF
Sheet            0 -8px 30px rgba(58,31,46,.18–.20)
Soft card        0 4px 24px rgba(58,31,46,.07)
Selected card    0 3px 14px rgba(58,31,46,.08)
Desktop toast    0 10px 30px rgba(58,31,46,.30)
```

**Reference sizes** — phone 390×844; desktop 1280×800; desktop rails 214px (left) / 304px (right); week-sheet columns 40px + 1fr + 1fr + 1.25fr.

## Assets

No image assets. Everything is type, colour, and CSS shapes. Two glyphs are used as text: `✓` (checks) and `★` (dinner emphasis); `−`, `+`, `×`, `↑`, `‹` are typed characters. Fonts are Google Fonts (Quicksand, Karla) — self-host or use the codebase's existing loader. No icon set is required; if the codebase has one, `✓ − + × ↑ ‹` may be swapped for equivalents.

Placeholder-only affordances (no flow designed): receipt photo capture, fridge-photo scan.

## Files

| File | What it is |
| --- | --- |
| `Home Manager Prototype v2.dc.html` | **Primary phone prototype** — Today, Week (6a), Grocery, Kitchen, Inventory (5a), What we know (5d), all sheets |
| `Home Manager Desktop.dc.html` | **Desktop prototype** — grocery list (5f) with triage folded in, shopping mode (5g) |
| `Kitchen Cooker Redesign.dc.html` | Cook mode — steps, hands-free, feedback |
| `Grocery Desktop + Week Menu Options.dc.html` | Option set the desktop and week designs came from (5e–5k, 6a–6c) — useful for "why this and not that" |
| `Inventory + What We Know Options.dc.html` | Option set for 5a / 5d, including the rejected alternatives |
| `Home Manager Brand Doc.dc.html` | Voice, colour and type rationale |
| `support.js` | Runtime the prototypes need to open in a browser — **not** production code |
| `COPY.md` | Every user-facing string, by screen |
| `FIRST_RUN.md` | First-run / household setup spec (not prototyped) |
| `NOTIFICATIONS.md` | The four v1 push notifications |
| `DATA_AND_API.md` | Entities, per-screen reads/writes, sync rules |
| `BUILD_ORDER.md` | Suggested implementation sequence with acceptance checks |

Read `README.md` → `DATA_AND_API.md` → `BUILD_ORDER.md` in that order; keep `COPY.md` open while building.
