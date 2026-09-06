# Pre-shop check — "Maybe already home"

Replaces the current "Already have this?" block on the Grocery screen (phone + desktop). Hand this
file to Claude Code as the build spec; the working reference is
`Home Manager Prototype v2.dc.html` (phone, Grocery tab) and `Home Manager Desktop.dc.html`
(Grocery list view). Option id in the design canvas: **1a** in `Grocery Triage Options.dc.html`.

## Why it changed

The shipped block had four defects. The new design fixes each one — do not reintroduce them:

1. **Three actions, two of which removed the item.** "Already have it" and "Remove from list" meant
   the same thing to users. Now there are exactly **two** answers.
2. **The action row scrolled horizontally off screen.** Buttons are now 50/50 full width, stacked
   under the sentence on phone. Nothing in this block may overflow the viewport.
3. **Raw pack maths in the label** ("1 stick (from larger pack) + 1 stick (from larger pack) wanted
   — tracking 0.5 stick in the fridge"). Now one resolved sentence: *"You want 2 sticks. Fridge
   shows half a stick."*
4. **A tall card covering the list you opened.** The block is now a normal card in the list flow,
   pinned to the top, and it disappears at zero.

## Content model

One entry per grocery item where the household has stock on hand for the same product.

```ts
type PreShopFlag = {
  itemId: string;        // GroceryItem.id
  name: string;          // "Eggs"
  wantedLabel: string;   // "2 dozen"        — already humanised, no pack arithmetic
  onHandLabel: string;   // "24"             — already humanised
  onHandLocation?: string; // "the fridge"   — optional, used only in the desktop tooltip
};
```

Sentence is composed server-side or in one formatter, never in the view:
`You want {wantedLabel}. Fridge shows {onHandLabel}.`

**Humanising rules** (this is where the old screen failed):
- Collapse multiple wanted lines for the same product into one total ("2 sticks", not "1 + 1").
- Fractions become words: `0.5` → "half a stick", `1.5` → "a stick and a half".
- Use the item's own unit, never the pack unit. "2 dozen" not "24 eggs" if the list says dozen.
- Round counts; never show decimals to the user.
- Max one sentence, max ~60 characters. If it can't be said in one sentence, don't flag the item.

## Layout & style

Shared tokens: plum `#66304E`, plum hover `#7C3D5E`, block bg `#F3E6EE`, block border
`1px solid #E4CEDE`, row divider `1px solid #E7D4E1`, ink `#3A1F2E`, muted plum ink `#7D6A74`,
outline border `1.5px solid #C9B3C1`, outline hover fill `#ECDBE6`. Fonts: Quicksand 700 for
titles, Karla for everything else.

### Phone (390 wide)

- Card: `border-radius: 18px`, `padding: 16px 18px 8px`, first card in the grocery scroll list,
  above the store cards, `gap: 13px` to the next card.
- Header row: title "Maybe already home" `Quicksand 700 19px #66304E`; right-aligned text action
  "Keep all {n}" `Karla 700 14px #8A4A6E` (hover `#66304E`).
- Helper: "Inventory thinks these are in the kitchen. Dropping one takes it off today's list."
  `Karla 14px/1.5 #7D6A74`, `margin-bottom: 6px`.
- Item row: `border-top: 1px solid #E7D4E1`, `padding: 13px 0 12px`.
  - Name `Karla 700 17px #3A1F2E`.
  - Sentence `Karla 15px/1.45 #7D6A74`, `margin-bottom: 10px`.
  - Actions: `display:flex; gap:8px`, each `flex:1`, `min-height:44px`, `border-radius:12px`,
    `Karla 700 15px`, centred.
    - **Buy it anyway** — filled `#66304E`, white text.
    - **Drop it** — transparent, `1.5px solid #C9B3C1`, plum text.

### Desktop

- Same card in the centre column, `border-radius: 20px`, `padding: 18px 22px 10px`,
  `animation: popIn .2s ease`, sits below the Unassigned block and above the store cards.
- Header is one line: title, then helper "Inventory thinks these are in the kitchen. Dropping one
  takes it off this trip." `Karla 15px #9C8B93` filling the middle, then "Keep all {n}"
  `Karla 700 15px #8A4A6E` right.
- Item row: `min-height: 58px`, `border-top: 1px solid #E7D4E1`, `gap: 16px` —
  name (`min-width: 130px`, `Karla 700 17px`) · sentence (`flex:1`, `Karla 16px #7D6A74`) ·
  two pill buttons (`border-radius: 20px`, `padding: 10px 17px`, `Karla 700 15px`).

## Behaviour

| Action | Result |
| --- | --- |
| **Buy it anyway** | Row leaves the block, item stays on the list unchanged. Toast `{Name} stays on the list`. Sets `preShopDecision: "keep"` so it is **not** flagged again this trip. |
| **Drop it** | Item goes `status: "removed"` (soft). Row leaves the block and the item leaves the store card. Toast `{Name} off the list — you have enough` with **Undo**. |
| **Keep all {n}** | Every remaining flag resolves as keep in one write. Toast `Kept all — nothing dropped`. |
| Block reaches zero | Block unmounts. No success card, no confetti — the clean list is the confirmation. |
| Undo | Restores `status: "needed"` and does **not** re-add the flag this trip. |

Rules:
- Flags are computed per trip and cached; resolving one never re-orders the others.
- A decision by either adult resolves the flag for both, live. The "adult changed something"
  notification fires for **Drop it** only, never for keep (copy already in `NOTIFICATIONS.md`).
- Never auto-remove. The system only ever asks.
- Cap the block at **5 rows**; if more qualify show the 5 highest-confidence and a
  `+{n} more like this` text link that filters the list to the rest.
- The block never appears in shopping mode.

## Data & API

Read: `GET /trips/{id}/pre-shop-flags` → `PreShopFlag[]` (server does the stock comparison and the
humanising).

Write, both idempotent per `itemId`:
- `POST /grocery-items/{id}/pre-shop { decision: "keep" }`
- `POST /grocery-items/{id}/pre-shop { decision: "drop" }` → sets `status: "removed"`, records
  `authorId`, returns the undo token.

Removals stay soft so the assistant can learn from repeat drops (see `DATA_AND_API.md`).

## Acceptance criteria

1. No horizontal overflow at 320, 390, and 430 CSS px. Both buttons fully visible, ≥44px tall.
2. Every sentence is one line of plain language with no parentheses and no decimals.
3. Exactly two per-item actions exist anywhere in this block.
4. Dropping an item removes it from its store card immediately and Undo puts it back in place.
5. The block unmounts at zero and the list scroll position does not jump when it does.
6. Two devices open on the same trip: a decision on one clears the row on the other within a
   refresh cycle, without clobbering an in-flight tap.
7. Nothing floats over the block — the mic FAB and ask bar must not cover the last row's buttons.

## Prompt for Claude Code

> Build the "Maybe already home" pre-shop check on the Grocery screen for phone and desktop,
> following `design_handoff_home_manager/PRE_SHOP_CHECK.md` exactly: two actions per item
> ("Buy it anyway", "Drop it"), one plain-language sentence per item, "Keep all {n}" bulk keep,
> soft removal with undo toast, block unmounts at zero. Use the tokens and type sizes in the spec.
> Replace the existing "Already have this?" block entirely. Then check the seven acceptance
> criteria in the spec and report on each.
