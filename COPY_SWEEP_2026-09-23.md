# Copy sweep — the plain-copy rules, 2026-09-23

A findings list, not a rewrite. Nothing in this file has been changed in the app.
Emily approves each line before any of it ships.

Emily, 2026-09-22: *"Can we make sure that copy throughout is more straight forward like
this. I don't like the AI written style."*

The four things being looked for (now rule 2 of `.claude/skills/pomona-copywriter/SKILL.md`):

1. **Announcing memory** — "I'll remember X for next time."
2. **A euphemism where a verb belongs** — "Drop it", "Put back".
3. **A generic confirmation where the thing has a name** — "Changes saved" when it could
   say "Carrots was added".
4. **A helper line restating the controls under it.**

House toast pattern: `<thing> was <verbed>`, and the action word is the plain verb
(`Undo`), not a phrase.

**Already in flight — not findings, other agents are rewriting these now:** the
"Maybe already home" buttons and helper line (`groPreShopHtml`, `static/shell.js:5874`),
and the Add sheet's note plus its add toast (`groAddSheetNote`, `static/shell.js:5682`;
`groAddedToast`, `static/shell.js:6347`).

**Scale, honestly:** 30 findings covering 47 places in the code — and finding 1 on its own
reaches another 27. By screen: 14 on Shop, 7 on Plan, 5 on Today, 3 on Cook, 1 that is
everywhere at once. Shop has the most because Shop has the most copy.

---

## One thing that needs Emily's yes before it can be fixed

`DESIGN_SYSTEM.md` §7 (Learning etiquette) currently holds up three of the exact lines
Emily corrected as *the reference pattern* other screens should copy: the celadon
"I'll remember Costco for it" line, the "Changes saved · Put back" pop-up, and Put back as
the name of the undo. It was written on 2026-09-22, the day before her correction.

Left as it is, that paragraph will re-grow this copy on the next screen anyone builds.
It has **not** been changed here: §7 and §8 are Tier 2 in the design system's own
governance, which makes them Emily's decision, not an agent's. The copywriter skill now
says the opposite, so the two documents disagree until she says which wins. Recommended:
she says the word and one commit updates §7's parenthetical to match the skill.

---

## Everywhere at once — the one to decide first

**1. "Changes saved"** — `static/shell.js:1622` (`var CHANGES_SAVED = 'Changes saved';`),
with copies at `static/plan-week.html:937` and `static/onboarding.html:2420`.

This single string is the app's answer to **27** different actions: ticking something off
the list, freezing a meat, swapping a dinner, moving a night, finishing a held thing,
saving Preferences, accepting a chat change. Every one of them knows the name of the thing
it just changed, and every one of them says "Changes saved" instead.

This is the line Emily corrected ("Changes saved" → "Carrots was added"), and it is by far
the biggest single win in the sweep.

*Suggested:* `toastSaved()` takes the thing's name and says `<thing> was <verbed>` —
"Carrots was ticked off", "Chicken Skewers was swapped", "Thursday and Friday were
swapped", "Your answers were saved". Where a caller genuinely has no name to give,
"Saved" on its own is still better than "Changes saved", which says nothing twice.

*Worth knowing:* the constant exists so a test can ask "did this save say so" without
knowing the wording — so this is a change to one helper plus its 27 call sites, not 27
separate edits.

---

## Shop

### Worst first

**2. "Put back" on a toast, where every other toast says "Undo"** — four places:
- `static/shell.js:6601` — `label: bought ? 'Undo' : 'Put back'` (ticking a line off)
- `static/shell.js:7014` — `label: 'Put back'` (undoing a sort of the whole list)
- `static/shell.js:5123` — `label: 'Put back'` ("Yes, freezing it")
- `static/shell.js:3326` — `var GRO_ELSEWHERE_BACK = 'Put it back';`

Anti-pattern 2, and the app already disagrees with itself: eighteen other toasts in the
same file say `Undo`. Emily's correction was exactly this one.
*Suggested:* `Undo` on all three toasts. `GRO_ELSEWHERE_BACK` is not an undo — it's a
standing button on a row you set aside days ago — so **"Back on the list"** there, not
"Undo".

**3. "Put back." as the whole toast** — `static/shell.js:6799`
("Put back." / "Put back where they were."), `static/shell.js:7400`,
`static/shell.js:7450`, and server-side at `app/tools/tonight.py:1284`.

Anti-pattern 3. A sentence fragment with no subject: the app knew which item and didn't say.
*Suggested:* "Carrots was put back." / "Two things were put back." The tonight.py line
already has the good version one branch above it (`"{dish} is back on tonight."`) — the
fallback should say "It's back on tonight." rather than "Put back."

**4. “I’ll sort the list by store and plan your stops.”** —
`static/shell.js:6114`, under "Where do you usually shop?"

Anti-pattern 1 and 4 at once: it announces what the app will do with the answer, above the
store chips that show it.
*Suggested:* cut the line. The question stands on its own.

**5. “It’s under Staples if you want it back.”** — `static/shell.js:7108`
(full toast: “Rice paused — three trips skipped. It’s under Staples if you want it back.”)

The second sentence is the same shape as the line Emily cut ("Change it any time from the
row's ⋯ menu"): a sentence about where a control lives.
*Suggested:* "Rice was paused — three trips skipped." Keep the Undo chip; that is the way back.

**6. “Here’s what I read — untick anything I got wrong.”** — `static/shell.js:6477`,
over the scanned-photo list

Narrating *I*, twice, over a list of tickboxes that already show what was read.
*Suggested:* "Untick anything that's wrong."

**7. "All the spices the recipes need. Tick the ones you need to buy."** —
`static/shell.js:4737`

Anti-pattern 4: the second sentence describes the tickboxes directly below it.
*Suggested:* "All the spices this week's recipes need." (or cut the line — the section is
already titled "Spices this week").

**8. "· tap to see them"** — `static/shell.js:4968`, the subtitle of a rolled-up done card

Anti-pattern 4: the whole card is a button with a chevron on it.
*Suggested:* the count alone ("13 things").

**9. “I’ll come back to it”** — `static/shell.js:5994`, beside "Show me tonight"

Anti-pattern 2: a phrase in the person's voice where a verb belongs.
*Suggested:* "Not now" (the same pair already exists at `static/shell.js:2082`).

**10. "Kept all — nothing dropped"** — `static/shell.js:7220`

The second half restates the first, and "dropped" is the euphemism Emily corrected.
*Suggested:* "Everything stays on the list."

**11. "Actually, I need it"** — `static/shell.js:5598`, in "Already had on hand"

Anti-pattern 2 — a phrase, not the action.
*Suggested:* "Put back on the list."

**12. “Approved. Here’s your list.”** — `static/shell.js:4099` (and the same string at
`static/shell.js:10890`)

A toast that restates the screen it just landed you on.
*Suggested:* "Your week was approved." — or cut it; arriving on the list is the confirmation.

### Lower down

**13. "· keep or drop?"** — `static/shell.js:4505`, the carry screen's subtitle, over
buttons that say "Keep" and "Don't need". Euphemism, and it doesn't match its own buttons.
*Suggested:* "· still need them?"

**14. “That’s where we shop” / “One list is fine”** — `static/shell.js:6110`. Buttons
written as sentences rather than actions. **Flagging, not recommending:** the code comment
says the wording is deliberate — no shops picked is a real answer, not a skip — and that
reasoning is sound. Emily's call.

**15. "Choose differently"** — `static/shell.js:11096` (Plan's ready-made card, listed here
because it reads like Shop's pills). Stiff.
*Suggested:* "Pick another."

---

## Plan

**16. “Mexican’s in. I’ll remember it.”** — `static/plan-week.html:2400`
(`inLine: '’s in. I’ll remember it.'`)

Anti-pattern 1, almost word for word the line Emily cut. The first clause already confirms
it; the cuisine is visibly on the list.
*Suggested:* "Mexican's in."

**17. "Put back." after a swap or a night move** — `static/shell.js:12255`,
`static/shell.js:12734`, `static/shell.js:14846`, `static/shell.js:19750`

Same fragment as Shop finding 3, on four more actions. Each one knows the dish or the day.
*Suggested:* "Chicken Skewers is back on Thursday." / "Thursday and Friday are back as they were."

**18. “Here's a first pass — change anything and I'll re-plan around it.”** —
`static/shell.js:10859` (straight apostrophes in the source, unlike its neighbour), and
**“Here’s your week — change anything before you approve it.”** — `static/shell.js:10852`

Both restate the screen and then explain the controls on it.
*Suggested:* "Here's your first week." / "Here's your week." The Swap buttons and the
Approve button say the rest.

**19. "One example arrangement — your household assembles freely."** —
`static/shell.js:11410`

Not one of the four shapes, but it is the clearest case in the app of the register Emily
named: nobody says "assembles freely" at a kitchen table.
*Suggested:* "One way to put it together — take what you like."

**20. “I’ve ticked your usual days — I’ll keep those to food that travels well.”** —
`static/plan-week.html:2330` (and the shorter `:2331`)

First clause: good, names what was done. Second clause announces.
*Suggested:* "I've ticked your usual days."

**21. "Something else — tell me" / "Tell me what instead"** — `static/shell.js:12486`,
`static/shell.js:12957`, `static/shell.js:13593`

Anti-pattern 2 — phrases where a verb belongs. Three spellings of one button.
*Suggested:* one wording everywhere, e.g. "Ask for something else".

**22. "Put back " in a screen-reader label** — `static/shell.js:11808`
(`(done ? 'Put back ' : 'Done — ') + name`)

Only heard, never seen, but it's the same euphemism.
*Suggested:* "Not cooked yet — Chicken Skewers."

---

## Today

**23. "Put back"** — `static/shell.js:1069`, after restoring a held thing

Anti-pattern 2 and 3 together: a euphemism, with no subject.
*Suggested:* "It's back on your list." (or name the held thing).

**24. "Put back."** — `static/shell.js:2524`, `static/shell.js:2544`
(undoing a night off, undoing a tonight swap)

Same fragment. Both know the dish.
*Suggested:* "Chicken Skewers is back on tonight."

**25. “It’ll be waiting under Plan — I won’t ask again this week.”** —
`static/shell.js:1131`, after dismissing the plan-week nudge

First half is orientation and earns its place. "I won't ask again this week" is announcing
what the app will remember.
*Suggested:* "It'll be waiting under Plan."

**26. "Ticked off." / "Back on the list."** — `static/shell.js:2648`

Both know the move's name.
*Suggested:* "Shopping was ticked off." / "Shopping is back on the list."

**27. "Done with this"** — `static/shell.js:1002`, the Holding-for-you row's button

Mild — a phrase, not a verb. Low priority.
*Suggested:* "Done".

---

## Cook

Cook is the cleanest of the four. Three lines.

**28. “Ask me anything about the recipes we’ve saved.”** — `static/shell.js:8588`

It is the placeholder under a row already labelled "Ask about our recipes" — anti-pattern 4.
*Suggested:* cut it, or make it a real prompt: "What can I make with chickpeas?"

**29. “That’s Friday’s — I’ll note the start when you cook it Friday.”** —
`app/tools/cooker.py:972`

Anti-pattern 1: announces the bookkeeping.
*Suggested:* "That one's for Friday."

**30. “Started — the clock’s moved with you.”** — `static/shell.js:8307`

Borderline; the second clause is the only place the time shift is said. **Flagging, not
recommending** — cutting it may lose real information.

---

## Considered and kept

Lines that look like violations and aren't, with why — so nobody re-flags them next sweep:

- **“Freezing it? I’ll remind you Friday to move it to the fridge for Sunday.”**
  (`static/shell.js:5025`) — this is the consequence of tapping Yes, said before it bites
  (DESIGN_SYSTEM §8). Not a filing announcement: without it the question is unanswerable.
- **“Rice off the list — I’ll ask again in about 3 weeks”** (`static/shell.js:7106`; the
  span comes from `groCadenceSpan`, `static/shell.js:4789`, which always says “about …”)
  — same reason. The item vanishes; when it comes back is information the person can't
  see anywhere else. (Its sibling at `:7108` is a finding — see 5 — because that clause is
  about where a control lives, not what happens.)
- **“Mint is a staple now — I’ll put it on the list before you run out”** (no full stop in
  the source) (`static/shell.js:7133`) — that promise *is* the feature; without it "staple" is a word
  with no meaning attached.
- **“No signal — I’ll save your ticks when you’re back.”** (`static/shell.js:3580`) and the
  other three offline lines — trouble copy, stating the problem with its way out, exactly
  as the calm-in-trouble rule asks.
- **“Correct me right where it happens and I’ll remember for next week.”**
  (`static/help-sheet.js:58`) — a help sheet is the one place explaining behaviour is the
  whole job.
- **“I’ve carried on from the answers already saved for this week.”**
  (`static/plan-week.html:1341`) — explains why the form is already filled in. Orientation.
- **“The two dinners trade places. Nothing else moves.”** (`static/shell.js:12448`) —
  "Nothing else moves" is a fact about scope, and a reassuring one. Not decoration.
- **“Swapped.”** (`static/shell.js:2411`) — a fallback only; the line above it names both
  dishes and only falls through when the server returns no meal name.
- **“Put <item> back on the list”** (`static/shell.js:4992`) — a screen-reader label that
  already names the thing and reads as a full sentence. Its two siblings,
  `static/shell.js:1745` and `static/shell.js:2865`, say the fixed “Put it back on the
  list” with no name — listed here rather than as a finding of its own because a screen
  reader reads the row’s own name right beside it, but worth a look if Emily wants it.
- **Every "Couldn't … — try again." error line** — the "that" is generic on purpose; these
  are trouble copy and the way out is in the same breath.

---

## Not swept

- **Chores** — paused by Emily, 2026-09-18. `renderChores` and its handlers
  (`static/shell.js` ~2698–3222) have the same "Put back." and unnamed-toast habits, and
  they are left alone until Chores comes back.
- **Onboarding and the welcome flow** — outside the four screens this card names, and
  approved line by line on 2026-09-10. Its `CHANGES_SAVED` copy
  (`static/onboarding.html:2420`) is in finding 1 only because it is the same string.
- **Chat replies** — written by the model at run time from `agent.py`'s SYSTEM_PROMPT, not
  fixed strings. Worth its own card if the same habits show up in real transcripts.
