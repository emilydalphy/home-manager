# Build order

Six phases. Each ends with something demonstrable, and each phase's acceptance checks are things you can verify by hand against the prototypes.

## 0 · Foundations
Tokens from README's Design Tokens section as the codebase's own primitives (colour, type scale, spacing, radius, shadow, the three animations). Quicksand + Karla loaded. Phone shell: status bar, scrollable content, docked ask bar, four-tab bar with badges.

*Check:* an empty Today renders inside the shell at 390×844 with the ask bar and tabs pinned, scrollbars hidden.

## 1 · Today and the week
`Meal` model with the `open` status, the needs-you band, tonight card, chores, grocery row. Then This Week: day rail, day card, and the 6a week sheet.

*Check:* filling Thursday from Today removes the band card, decrements the Today badge, updates the day rail's dashed pill, and changes the week-row sub to "All seven days planned · 21 meals". Tapping a week-sheet row selects that day and closes the sheet. The footer button reads "Back to Tuesday" with the space.

## 2 · Grocery, phone then desktop
Phone "to buy" list. Then the desktop three-column layout: rail filters, unassigned block, aisle-grouped store cards, "Got it" pill, "Already have it" removal, trip panel.

*Check:* assigning an unassigned item moves it into the right store card under the right aisle and the block disappears at zero. "Already have it" removes the row entirely; "Got it" keeps it, struck through, hidden unless "Already got" is on. Empty filter shows the empty-state card.

## 3 · Shopping mode
Plum full-screen mode, oversized rows, aisle cards, progress footer, next-store handoff, and the trip-close that promotes checked items into stock.

*Check:* heading follows the first unfinished aisle; progress matches got/total; "Next store" switches stores and becomes "Back to list" when nothing is left elsewhere; "Done shopping" returns to the list with the trip toast.

## 4 · Kitchen: inventory and memory
Inventory grouped by location with the detail sheet (qty stepper, location picker, best-before stepper with relative wording, add-to-list, used-it-up). Then What we know: four tabs, inline textarea editing, add/delete, the Stores tab's "what you get where" chips, and the import sheet with its parser.

*Check:* the expiry stepper moves one day per tap and the note re-words across the today/tomorrow/days/weeks/months thresholds; changing location re-groups immediately; "Add to grocery list" becomes inert as "On the list ✓". Adding a fact then cancelling leaves **no** empty row — same for switching tabs or leaving the screen mid-edit. Pasting a comma- or newline-separated list adds only the non-duplicates and toasts the count.

## 5 · Assistant, then first run and notifications
Wire the ask sheet to the real endpoint with the reply + card + patch contract. Then FIRST_RUN.md, then the four notifications in NOTIFICATIONS.md.

*Check:* every patch that changes data produces a card whose "View" lands on the changed tab; cascades (Thursday → salmon Saturday) come from the server, not the client; a fresh household completes setup in under 90 seconds and lands on This Week with one dinner deliberately open.

---

## Getting the details right

The three things most likely to be lost in translation, in order:

1. **The rotated badges and paper card in the week view.** `rotate(-2deg)` / `rotate(2deg)`, `#FFFDF6` paper with `0 2px 0 #EFE4CF`, and the 36px hairline course separators are the whole reason the week reads like a menu instead of a table. Don't normalise them.
2. **"Got it" vs "Already have it".** Two different meanings — in the cart, versus stop asking. The labelled pill and the text action are deliberate; a bare checkbox tested badly.
3. **The 21-meal grid fits unscrolled at 390×844.** 13px cells, `gap: 8px`, `1fr 1fr 1.25fr`. If it starts scrolling, the option's reason for existing is gone.

Ask before inventing: anything not in these files — offline conflict UI, holiday/pause mode, receipt OCR, the fridge-photo scan, tablet-specific layouts, a fifth notification.
