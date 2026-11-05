# Bright Dusk redesign handoff

Emily approved this look on 2026-11-05. The boards here are the source of truth for the rebuild. `playable-prototype.html` shows motion and tap behaviour. Sample meals in the boards are samples.

`N-Rules.dc.html` holds the design rules and `R-Palette.dc.html` the colours. Both replace The Inn.

## Decisions
- Colours: Dusk Blue #1B4392, Night Ink #14264A, Ripe Apricot #FFA45C, Soft Apricot #FFC08F, Warm Cream #FFFAF3, Blush #FBE9D9, Slate #55607A, Baked Clay #A8461A, White. Green stays in the food. Purple never leads.
- Light mode only for now. Remove Settings > Appearance. Do not delete dark tokens that a later dark mode would need unless they get in the way.
- One font, Figtree, at six sizes: 32, 26, 16, 14, 13, 11. Four corner sizes: 13 controls, 16 buttons, 22 tiles, 26 sheets. Round buttons and pills are fully round.
- No band at the top of screens. Title and one line on cream.
- Tiles are the base of the whole app: two across, one thing each, with a plate where there is a dish.
- Tab bar: solid Dusk Blue on every main screen, icons and labels, Ask is always the apricot pill with the word Ask at its end. One main apricot button per screen otherwise, always dark text.
- Dinner is never highlighted. Every meal is an equal tile. Dusk Blue is for the tab bar, Use soon, timers and the welcome.
- Plan: All is a menu grouped by Breakfast, Lunch, Dinner, Snacks, and a repeat meal shows once with its days. A day shows breakfast, lunch, dinner, snacks in that order. Add meal and freezer live under the ··· menu. Past days are hidden. No counts under titles.
- Cook: today's prep, then meal tiles, then next up. A single big card only when one meal is left.
- Prep: the established section names and order, as tiles with tick boxes, all open, each folds on tap. Made on the night stays, with its sentence and Add it. All computation stays as it is.
- Recipe: plate, name, two tabs. Ingredients are tiles with amounts. Steps are numbered tiles with "Takes about N min" at the top. No source line on this screen for now; the existing original-recipe links in the app stay where they are unless that conflicts with this layout, in which case keep them reachable and flag it.
- Shop: store switcher, aisle tiles with tick boxes. Kitchen: Use soon card, Fridge/Freezer/Pantry switcher, tiles.
- Settings: six tiles (Household, Food, Your week, Stores, Reminders, Account) plus Help, one tap in. Inside screens are tiles. Everything in the live list stays reachable, except Appearance and Add to home screen.
- Ask: the sheet from the prototype, dusk bubbles for the person, white for Pomona.
- Empty and error screens: one plain line, one button when there is a next step, no filler pictures.
- Sheets slide up. Toasts are one dark bar above the tab bar, naming the thing, with Undo.
- Plates on cream and white get a soft shadow: drop-shadow(0 2px 5px rgba(20, 38, 74, .18)), from the app, not the drawing.
- Copy: no fluff. Every line has a job. Copy rules in DESIGN_SYSTEM.md still apply.
- Welcome: Z-WelcomeD, revolving tile, her wording exactly.

## Behaviour
The look changes, the flows do not. Keep every feature and flow. If a behaviour must change to fit a screen, leave the behaviour alone and note it.
