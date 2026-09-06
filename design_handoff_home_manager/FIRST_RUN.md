# First run and household setup

**Status: specified here, not prototyped.** No screens exist for this yet — the prototypes start mid-life with a household already known. Build to this spec, or come back to design once the rest is standing. Either is fine; what must not happen is shipping an empty-state app that expects the user to fill in forms.

## Principle

The app earns its data by being useful first. First run collects the four things that make the first week's plan credible — who eats, what's off-limits, which stores, and one anchor commitment — and nothing else. Everything else in "What we know" gets learned over time through the ask bar and through corrections.

Target: **under 90 seconds**, four steps, every one skippable. A skipped step means the assistant asks about it later, in context, in the ask bar.

## Flow

### 0 · Welcome
One screen, plum ground `#66304E`, gold kicker, the house voice: name the promise ("Tell me who eats and I'll take dinner off your plate"). Single gold CTA. No account wall before this — sign-in comes after step 4 so the value is visible first.

### 1 · Who's at the table
- Household name (free text, defaults to "{Surname} House" if the account has one — it becomes the menu framing "The Dalphy House").
- Adults: the signed-in adult, plus **invite one more** by email or link (v1 supports exactly two adults sharing — see the multi-user note below). Each adult gets a colour and an initial: first `#66304E`, second `#4D8A33`.
- Kids: name + age, add-as-many rows. Kids are people the plan accounts for; they are not accounts.
- Copy: heading `Who's at the table`, helper `Ages help with portions and what lands.`

### 2 · What's off the table
- Chips for the common ones (no pork, no beef, vegetarian, gluten-free, dairy-light, nut allergy, shellfish) plus a free-text row per person ("Sam won't eat peppers").
- **Allergies are a hard rule; preferences are soft.** Mark them differently in the data and never let the assistant propose past an allergy.
- Writes straight into `know.People`.

### 3 · Where you shop
- Add 1–3 stores: name, then one habit tag ("every other Saturday", "on the commute home") and a note on what it's for ("bulk", "fresh").
- Optional and high-value: **import what you usually buy** — reuse the import sheet exactly as designed in the Stores tab (paste a list, one per line or comma-separated). Offer it here rather than making the user find it later.
- Writes into `know.Stores` and `storeMap`.

### 4 · One thing that's fixed
- Ask for a single standing commitment: "Anything that's the same every week?" with examples (tee-ball Thursday, pizza Saturday, leftovers Sunday). One row, add more optional.
- This is what makes the first plan feel observed rather than generated. Writes into `know.Rhythm`.

### 5 · First plan
- Generate a full seven-day plan, breakfast/lunch/dinner, and land the user on **This Week** with the day rail on today, exactly the 6a layout — not a special onboarding screen.
- Leave **one dinner deliberately open** near the end of the week, so the first thing they see is the app asking for a decision it can't make alone. That teaches the needs-you band without a tutorial.
- Toast on arrival: `Here's a first pass — change anything and I'll re-plan around it.`

## After first run

- **No coach marks, no tour.** The three things a user must discover — the ask bar, the whole-week sheet, and inline fact editing — are all discoverable by tapping what's on screen.
- The assistant asks for one missing fact at a time, in the ask bar, when it is about to matter ("I don't know what a normal grocery week costs you — roughly $200?"). Never a batch questionnaire.
- Inventory starts empty. Do **not** ask the user to type their fridge in. The first inventory entries come from the first shopping trip: after "Done shopping", the checked items become stock. The "Scan a fridge photo" nudge in Kitchen is the second path (unbuilt).

## Accounts and sharing (two adults, v1)

- One household, two adult accounts, equal permissions. No roles, no owner-only actions in v1.
- Every mutation records its author. That is what the initial avatars on grocery rows show, and it is why "added by Marcus" reads as a reason on unassigned items.
- Both adults see the same lists live. Conflict rule: **last write wins per field**, except item check-off, which is a set operation (once checked by either adult, it stays checked; unchecking is also shared).
- If the second adult never accepts the invite, nothing degrades — attribution just always shows one initial.
- The one notification that exists because of sharing: "Marcus swapped Thursday's dinner" (see NOTIFICATIONS.md).

## Re-entry states worth designing later

Not in scope now, but they will come up: returning after a week away (a plan full of stale days), a household that changes shape (a kid's allergy appears), and pausing (holiday mode). Flag these to design rather than inventing them in code.
