# Copy cleanse — 2026-09-11

Every user-facing line changed or removed on branch `worktree-copy-cleanse`, so any of them can be put back.
Reasons: **restates** (the screen already says it) · **fills a slot** · **explains the label** · **wink** (S9) ·
**dashboard label** · **not a person's phrasing** · **italic quota** (italics now live on the sign-in screen only;
the welcome intro never used them).

## Italics

| Screen | Before | After | Reason |
|---|---|---|---|
| Meal screen (Plan › day › meal) hero | Italic line under the dish: the planner's reasoning, e.g. "Beef is a favorite protein per household preferences and stir-fry is a quick weeknight option loaded with vegetables." | cut | not a person's phrasing / italic quota |
| Now › next-up card | Italic "reason" line under the dish (planner reasoning for a cook; a fact for a fridge move / reheat / batch) | Plain Figtree 15px; the planner's reasoning no longer reaches it (`app/tools/moves.py`) — only facts do: "for Thursday's skewers", "Cooking for 6 — covers tonight and leftovers on Thursday.", a recipe's reheat note | italic quota / not a person's phrasing |
| Cook › Before you start hero | Italic note: batch note, else advance-prep note, else planner reasoning | Plain; batch note or advance-prep note only — reasoning cut | italic quota / not a person's phrasing |
| Cook › prep session hero (all done) | "That's the prep done — the week is easier from here." (italic) | "That's the prep done." (plain) | wink / italic quota |
| Cook › reheat night hero | Italic reheat note | Same words, plain | italic quota |
| Cook › whole method › "Why this?" text | Italic reasoning behind a tap | Same words, plain | italic quota |
| Plan root (component households) | "One example arrangement — your household assembles freely." (italic) | "One example — assemble it however you like." (plain) | italic quota / not a person's phrasing |
| Plan › ready-made alternative line | Italic 12.5px alternative sentence | Same words, plain 13px | italic quota |
| Plan / Cook quiet text links ("or start over", "Something in the freezer?", "Cooking ahead?") | Newsreader italic 17px | Figtree 600 15px, same underline | italic quota |
| Helpful tips › examples ("What's next tonight?" …) | Italic | Plain 14px/600 | italic quota |
| Setup › "Who are we planning for?" | Italic: "The names of everyone at home. I'll ask what they eat next." | Plain: "Everyone who eats at home." | italic quota / restates (second sentence pre-announced the next screen) |
| Setup › reveal (while drafting) | Italic: "Putting together a real week from what you just told me…" | cut | restates the title / italic quota |
| Setup › reveal (drafted) | Italic: "Built around exactly what you just told me — not a generic starter, planned around your usual 6–8 dinners." (and variants) | cut | restates the receipt below it / wink / italic quota |
| Setup › reveal (failed) | Italic: "That didn't come together — shall I have another go?" | cut (the receipt's own line "The first week didn't come together — tap Try again, or I'll draft it when you open the app." stays) | restates / italic quota |
| Setup › reveal | Italic: "You can change any of your responses later on under the gear." | cut | fills a slot (the intro already says "you can change any answer later") |
| Setup › reveal links "or tweak it with me" / "Take me in anyway" | Italic | Same words, plain | italic quota |
| Sign-in › "This one's just for your household." | Italic | **unchanged** — the one italic line left | — |

## Noise

| Screen | Before | After | Reason |
|---|---|---|---|
| Sign-in | "Enter your passphrase to come in." | cut | explains the label (field says "Passphrase", button says "Come in") |
| Sign-in helper | "Ask your household settings owner for the passphrase." | "Don't have the passphrase? Ask whoever set up your household." | dashboard label ("settings owner") |
| Meal screen › The plate | "Protein, veg, carb. Nothing to thaw." | "Nothing to thaw." (chips already list the groups; a real thaw note still shows in full) | restates |
| Plan root | "Swap anything. Nothing's bought until you approve." | cut | explains the label (dock says "Approve and build my shopping list") |
| Plan › no plan yet card | "Two rounds of questions, then I'll draft it. Nothing gets bought until you approve." | cut | fills a slot (also stale — it's four screens now) |
| Plan › no plan yet card | "Weeks not landing how you'd like? Let's adjust your setup →" | "Adjust your setup →" | wink |
| Plan › More sheet › Reopen the week | "Edit it again — your list stays as it is" | "Your list stays as it is" | restates |
| Plan › More sheet › Check the week | "What you're eating, and which days" | cut | explains the label |
| Plan › More sheet › See the whole week | "All the meals, and the link to share them" | "With the link to share it" | restates |
| Plan › away night with nothing made ahead | "I haven't got anything to earmark for this yet — nothing batch-cooked earlier and nothing in the freezer." | "Nothing already made to cover it yet — nothing batch-cooked, nothing in the freezer." | not a person's phrasing |
| Now › nudge dismissed | "Of course. It'll be waiting for you under Plan — I won't ask again this week." | "It'll be waiting under Plan — I won't ask again this week." | fills a slot ("Of course.") |
| Chat opener | "Tell me what you'd like different and I'll rework it — no need to be polite about it." | "Tell me what you'd like different and I'll rework it." | wink |
| Chat › example chips label | "Or start with one of these" | cut | restates (narrates the chips) |
| Chat › loading bubble | "Cooking up your list...", "Sorting the aisles...", "Filling the cart...", "Cooking up a plan...", "Simmering on your week...", "Plating up some ideas...", "Preheating the ideas oven...", "Sweeping up the details...", "Tidying up your schedule...", "Dusting things off...", "Whipping this up...", "Stirring up an answer...", "Cooking something up...", "Simmering on it..." | "Sorting out the list...", "Working on the list...", "Working on the week...", "Looking at the week...", "Working on the chores...", "One moment...", "Working on it..." | wink |
| Coach card (first open) | "The buttons do the everyday things." / "For anything else, tap the chat and type it." / "If I get something wrong, tell me there." | "Buttons do the everyday things." / "Everything else, type in the chat." / "If I get it wrong, say so there." (title and sub-lines unchanged) | fewest words; matches the welcome flow's "If I get it wrong, say so" |
| Helpful tips › opening | "Say it however it comes out. There's no right way to phrase it." | "Say it however it comes out." | restates |
| Helpful tips › closers | "Buttons do the common things. Words do the rest." | cut (the other closer stays) | restates (the coach card's title) |
| Cook root › Recipes row | "Ask me what we've saved" | cut (no cheap count on the payload) | describes a feature, not a fact |
| Cook root › Add from a link row | "Paste a recipe page and I'll read it" | cut | explains the label |
| Cook root › Prep days row (none set) | "Tell me which days you prep and I'll batch the week around them" | cut | describes a feature |
| Cook › attention card | "Needs your attention" / "N quick things from last night" | "How did it go?" / "N quick things" | dashboard label |
| Cook › no saved recipe | "Freeform meal — no saved recipe detail. Ask in the ask bar for the full recipe." | "No saved recipe for this one — ask me for it in the chat." | not a person's phrasing |
| Cook › Before you start, empty saved recipe | "Nothing to get out for this one yet. The whole method has a way to fill the recipe in." | "Nothing to get out yet — fill the recipe in under The whole method." | one breath |
| Cook › Before you start, freeform meal | "Nothing written down for this one. Ask me for the recipe and I'll put one together." | "Nothing written down for this one — ask me for the recipe." | one breath |
| Cook › log toast | "Logged. I'll remember how it went." | "Logged — that'll steer next week." | restates ("Logged") |
| Add from a link › draft read by the model | "I read this off the page myself, so give it a look before saving." | "Read off the page — check it over before saving." | wink ("myself") |
| Add from a link › saved | "Saved. “X” is one of your recipes now — ask me to put it on the week whenever you like." | "Saved. “X” is one of your recipes now." | describes a feature |
| Shop › sort done | "Nothing left to sort — nice work." | "Nothing left to sort." | wink |
| Shop › sort-how row | "One row each. Tap only the exceptions." | "Tap only the exceptions." | restates |
| Shop › Maybe already home | "N things the kitchen may already have" | "N things" | restates the title |
| Shop › Maybe already home helper | "Inventory thinks these are in the kitchen. Dropping one takes it off today's list." | "Dropping one takes it off today's list." | restates |
| Shop › Where next (none left) | "That is every stop — wrap it up." | "That's every stop — wrap it up." | contractions |
| Shop › stores prompt | "Tap every shop you use. I'll sort the list by store and plan your stops." | "I'll sort the list by store and plan your stops." | explains the chips |
| Preferences › Your rhythm (empty) | "Not set yet" | "Sets when to start cooking" | says what the setting does |
| Preferences › Prep days (empty) | "Not set yet" | "Batches the week around them" | says what the setting does |
| Setup › Which meals | "Tap the ones you want on the plan. Everything else I'll leave to you." | "Whatever you don't tap, I leave to you." | restates (first sentence) |
| Setup › Never put on the plate | "Allergies, must-avoids, the way someone eats — per person, so I get each plate right." | "Allergies, must-avoids, the way someone eats." | fills a slot (per-person rows are visible) |
| Setup › Way meals lean | "Tap what fits. Skip it if nothing does." | cut | explains the chips (textarea already says "optional") |
| Setup › Never recommend | "I'll steer clear of it completely." | cut | explains the label |
| Setup › Excited to eat more of | "Cuisines, styles, anything sounding good right now." | cut | restates the chips |
| Setup › Meal prepping | "Which days, and roughly how long? Up to two." | "Up to two days." | restates the chips ("Roughly how long?" still appears once a day is picked) |
| Setup › Your first week (kit) | "I'll only suggest recipes your kitchen can actually make." | "I'll only suggest recipes your kitchen can make." | wink ("actually") |
| Setup › invite the house | "Everyone gets their own link to add their own preferences and feedback — totally optional, and you can always do this later from What We Know." | "Each person gets a link to add what they eat. Optional, and it's under Preferences later too." | one breath / stale name |
| Plan the week › Lunches on the go | "Tap the days someone takes lunch with them. I'll keep those to food that packs cold." | "I'll keep those to food that packs cold." | explains the chips |
| Plan the week › lunches ack | "I'll keep those N to things that travel cold, and plan the rest as hot lunches at home." | cut | restates the sub-line |
| Plan the week › mood ack | "I'll lean the week this way without making every night the same." | cut | restates the sub-line |
| Plan the week › cuisines body (nothing saved) | "You haven't told me your cuisines yet, so here's a starting point — whatever you tap I'll remember for next time too." | "A starting point — I'll remember whatever you tap." | one breath |
| Plan the week › cuisines body (saved) | "The ones you've told me you like, from what we know about your household. Whatever you tap wins over your usual rotation this week." | "Your usual ones. Whatever you tap comes first this week." | one breath |
| Plan the week › cuisines ack | "I'll put these ahead of your usual rotation this week." | cut | restates the body line |
| Plan the week › Anything else | "A night that's already decided, something in the freezer to use up, a craving. I'll keep it and shop for it." | cut | restates the placeholder |
| Plan the week › away sheet lede | "Tell me the range once — I'll cover every meal in between, no need to tag each day." | "One range covers every meal in between — no need to tag each day." | one breath |
| Plan the week › guests (household known) | "Adults and children eat differently, so I need both numbers — it changes the portions and what I'd suggest cooking." | cut | explains the steppers |
| Plan the week › guests (household unknown) | "I don't know your household's make-up yet, so tell me the whole table — adults and children eat differently, and it changes the portions and what I'd suggest cooking." | "I don't know your household yet, so count the whole table." | one breath |
| Meal setup › intro | "Change anything here and I'll follow it from next week onwards. Nothing is locked in from when you signed up." | "Change anything here and I'll follow it from next week." | restates |
| Meal setup › recipes a week | "Pick how many breakfasts, lunches and dinners you'll actually cook. I'll fill the rest of the week with those — leftovers included — unless you tell me you're away." | "The rest of the week is repeats and leftovers." | explains the steppers |
| Meal setup › Your kitchen | "I'll only suggest recipes your kitchen can actually make." | "I'll only suggest recipes your kitchen can make." | wink |
| Meal setup › Won't eat | "Everything you've told me to keep off the table. Correct me any time — I'd rather know." | cut | explains the label / wink |
| Meal setup › At the table | "Whether everyone eats the same thing." | cut | explains the label (the options say it) |
| Meal setup › A normal week | "The single most useful thing you can tell me. The more I know about how your week runs, the more this plan will feel like yours — it gets sharper as you correct me." | "The more I know about how your week runs, the more this plan will feel like yours. It'll get sharper as you correct me." (DESIGN_SYSTEM §8's own "do" line) | fills a slot |
| Meal setup › chat card | "Some things don't fit in a stepper. Describe the shape of it and I'll set all of this for you." | "Say it in your own words and I'll set all of this for you." | wink |
| Meal setup › saved notes | "Noted — I'll start from that next week too." / "Noted — I've taken that off." / "Noted — I'll remember that." / "Noted — that shapes every week I build." | "Noted — from next week on." / "Noted — that's off the table." / "Noted." / "Noted." | restates |

## CSS removed or changed with the lines above

- `static/shell.css`: `.hero-accent`, `.week-suggested-note`, `.ready-made-alt`, `.week-reset-link`, `.cook-hero-note`,
  `.cook-why-text`, `.tips-group-example` → plain Figtree; `.rv-invite`, `.ask-examples-label`, `.hero-quiet` (unused) deleted.
- `static/onboarding.html`: `.q-accent` → plain; `.reveal-accent`, `.reveal-note`, `.rhythm-italic` deleted; `.reveal-quiet-link` → plain;
  `buildRevealCopy()` / `revealRhythmClause()` removed.
- `static/login.html`: `.signin-sub` deleted.
- `static/theme.css`: `.accent-line` (unused anywhere) left alone — token file, Tier 2.

## Left alone, flagged for Emily

- Chat sheet: title "What's on your mind?" and the composer placeholder "What's on your mind?" say the same thing, one above the other (three tests pin both). Suggest a different placeholder, e.g. "Say it however it comes out".
- Cook › whole method › "Why this?" still opens the planner's reasoning (plain now, behind a tap). Cut it too if the reasoning shouldn't appear anywhere.
- Preferences › "How you eat" and "Stores" still read "Not set yet" — no ≤5-word line said what the setting does.
- Plan the week › day-tag acks ("I'll keep it under 20 minutes…", "I'll plan nothing and buy nothing for this night.") left — they carry a consequence, not just the label.
- Plan the week › "As many as you like. I won't make every night the same." and Setup › "So I can tell you when to start cooking." left — each says what the answer does.
- Plan › receipt "One quick one, if you like" / "Two quick ones, if you like" left.
- Helpful tips › "AFTER YOU SEND" eyebrow left.
