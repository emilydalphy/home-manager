# App Store listing — drafts

Drafted 2026-10-06 overnight (branch `overnight/app-store-listing-drafts`).
**Nothing here has been submitted anywhere.** Emily approves the words and
sets the price; everything below is a draft for that.

Character limits are App Store Connect's; each count is in brackets.

---

## 1. Name and subtitle

**"Pomona" on its own is taken.** App names are unique on the store, and
"Pomona" is a news app (Pomona Media AG, id1585979939). Also live: PomonaGo,
Pomona Club, myPomona, Pomona Life. So the store name needs a second part.
The name under the icon on the phone stays "Pomona" either way
(`CFBundleDisplayName`).

Name (30), best first:

1. **Pomona: Family Meal Planner** [27]
2. Pomona – Dinner, Planned [24]
3. Pomona: Meals, List, Dinner [27]

Subtitle (30), best first:

1. **The week's food, handled** [24]
2. Plan, shop and cook the week [28]
3. Dinner's sorted. So's the list [30]

## 2. Promotional text (170, can change any time without a new build)

> Tell me who's eating and what your week looks like. I'll plan the meals,
> build the shopping list and walk you through dinner. [148]

## 3. Description (4,000)

> Feeding a family is a second job nobody signed up for. What's for dinner,
> what's in the fridge, who's home Thursday, who can't eat what. Pomona
> takes that on.
>
> A FEW QUESTIONS, A WHOLE WEEK
> Tell me who's at the table, what they like and what they can't eat. Each
> week I ask what's coming up — late nights, nights out, guests — and draft
> breakfast, lunch and dinner to fit. Swap, move or drop anything. When it
> looks right, approve it.
>
> THE LIST BUILDS ITSELF
> Approve the week and the shopping list is ready, grouped by section, with
> amounts that add up across every recipe. Things you buy on a rhythm, like
> coffee and dish soap, come back when they're probably due. Tick things off
> in the store, even with no signal.
>
> DINNER, ONE STEP AT A TIME
> Cook shows tonight's meal and when it lands on the table. Start cooking
> and it's one step per screen, with the amounts right there. If something
> needs to come out of the freezer, I'll say so the day before.
>
> ASK ME ANYTHING ABOUT THE WEEK
> "Can we swap Friday for something quicker?" "We're out of eggs." "Theo's
> away till Sunday." Say it the way you'd say it to a person, and I'll
> change the plan and the list to match.
>
> BUILT FOR A HOUSEHOLD
> Everyone at home can sign in to the same household. Allergies and
> dislikes are kept in one place and every plan respects them.
>
> Pomona uses Anthropic's Claude to draft plans and answer you. Your
> household's details go to it only to do that, and are never sold or used
> for ads. No ads, no tracking.

Words to check before approving:

- "Everyone at home can sign in" — true today with the shared passphrase and
  the "Who's this?" pick; reword if Builder C's sign-up changes the model.
- "even with no signal" — true once the list has opened with signal (the
  service worker); kept short on purpose.
- "I'll say so the day before" — the defrost ask; in-app, not a push, unless
  push is on.
- Nothing about texts or push notifications: both need keys set in Railway
  (Twilio, APNs) before they're true for a stranger.

## 4. Keywords (100, comma-separated, no spaces needed)

> meal planner,grocery list,family,dinner,recipes,shopping,weekly plan,allergies,cooking,menu,pantry [99]

Words already in the name and subtitle are indexed anyway, so "meal" and
"planner" could be swapped for "kids,batch" if option 1 is the name.

## 5. URLs

| Field | Draft | Status |
|---|---|---|
| Support URL (required) | `https://home-manager-production-4949.up.railway.app/support` | Route being built tonight by Builder C (`overnight/legal-pages`); not on `origin` when this was written, so the path is assumed. Check it after merge. |
| Privacy policy URL (required) | `https://home-manager-production-4949.up.railway.app/privacy` | Same: assumed path, Builder C's branch. |
| Terms (EULA field, optional) | `https://home-manager-production-4949.up.railway.app/terms` | Same. Apple's standard EULA applies if left blank. |
| Marketing URL (optional) | leave blank | No marketing site yet. |

If a custom domain comes first, all three change with it.

## 6. App privacy ("nutrition label") answers

Matches `ios-app/ios/App/App/PrivacyInfo.xcprivacy` (branch
`overnight/privacy-manifest`) — keep the two in step.

**Do you or your third-party partners collect data from this app?** Yes.

**Tracking:** No data is used to track you. (No ad SDK, no analytics SDK,
nothing shared with data brokers.)

**Data linked to you** — every type below is *linked to the user's
identity* (it's stored against the household) and *not used for tracking*:

| Apple category | Apple type | What it is in Pomona | Purpose |
|---|---|---|---|
| Contact Info | Name | First names of everyone at home, children included | App Functionality |
| Contact Info | Email Address | Sign-in email — **only once sign-up ships** (Builder C). Leave unticked if it hasn't merged by submission. | App Functionality |
| Contact Info | Phone Number | Number for the morning text / dinner nudge, if someone adds one | App Functionality |
| Health & Fitness | Health | Allergies and dietary needs (e.g. celiac, nut allergy) used to plan safe meals | App Functionality |
| User Content | Photos or Videos | A cookbook page photo, kept with the recipe imported from it (receipt and fridge photos are read and not kept) | App Functionality |
| User Content | Customer Support | "Something not working?" reports | App Functionality |
| User Content | Other User Content | Chat messages, the household's food, plans, lists, notes | App Functionality |
| Usage Data | Product Interaction | Counts of chat turns and meals cooked, to see whether the app is used | Analytics |
| Diagnostics | Other Diagnostic Data | The shape of a screen error (type, file, line — never the wording) | App Functionality |

**Not collected:** location, contacts, browsing/search history, financial
info, purchases, advertising data, device ID (the push token is per-install,
used only to deliver notifications), audio (dictation is done on the phone
by iOS; only the resulting text is sent).

**Third parties who receive data (for the privacy policy, not a label
question):** Anthropic (Claude) receives the household's plan context and
chat to draft plans and answer; Railway hosts the app and database; Twilio
receives a phone number and the text when texts are on; Apple receives the
push token and notification text when push is on.

## 7. Age rating questionnaire

Apple's current questionnaire (updated 2025). Draft answers:

| Question | Answer |
|---|---|
| Violence (cartoon, realistic, prolonged, graphic) | None |
| Sexual content or nudity | None |
| Profanity or crude humor | None |
| Horror / fear themes | None |
| Alcohol, tobacco or drug use or references | **Infrequent/Mild** — recipes can name wine or beer as an ingredient. (Answer "None" only if Emily would rather; Apple treats cooking wine lightly.) |
| Medical or treatment information | None — dietary needs shape meals; no medical advice is given |
| Health or wellness topics | None (meal planning, not fitness or diet coaching) |
| Gambling / simulated gambling / contests | None / No |
| Mature or suggestive themes | None |
| Unrestricted web access | No — links to other sites open in Safari, outside the app |
| User-generated content shared with other users / messaging | No — a household sees only its own data |
| Advertising | No |
| Parental controls / age assurance | No |

Expected result: **4+** (9+ if Apple weighs the alcohol answer). The chat
is an AI assistant scoped to the household's food; if the questionnaire
asks about AI-generated content directly, answer yes and describe it that
way.

## 8. Screenshots

In `ios-app/store/screenshots/`, from the demo household
(`create_demo_household.py`) on a throwaway database:

| File | Shows |
|---|---|
| `1-plan.png` | Plan: the week, today's meals, Done / Swap / Move |
| `2-list.png` | Shop: the list, sorted, with amounts |
| `3-cook.png` | Cook mode: one step of Weeknight Chili |
| `4-chat.png` | Ask: the chat sheet with a question typed |
| `5-today.png` | Today: yesterday's check-in, today's meals |

Sizes: `iphone-6.9in/` 1320 × 2868 (the size App Store Connect requires;
it scales down for smaller phones) and `iphone-6.7in/` 1290 × 2796 (a
backup in case the 6.9" slot rejects anything).
`ios-app/store/take-screenshots.mjs` retakes them.

**Gaps, honestly:**

- The chat shot shows a question typed, not an answer: the sandbox has no
  Anthropic key, and a made-up reply would not be a screenshot of the app.
  For a better one, run the script against the live demo household after
  deploy and send the message for real.
- Plain screenshots, no captions or frames. Captions over a frame ("Your
  week, planned in five minutes") sell better; that's a design pass.
- The dates in them are 2026-10-06's week. Retake right before submitting.
- Tacos (Tuesday) has no saved recipe in the demo household, which is why
  Cook mode is shown on Wednesday's chili.

## 9. App Review notes (for the "Sign-in information" box)

> Pomona signs in with a household passphrase rather than a username.
> Username: (leave blank or "demo") · Password: `<DEMO_PASSPHRASE from
> Railway>`. After the passphrase, tap "Maya" on "Who's this?". The demo
> household has a planned week, a shopping list and recipes. The chat uses
> Anthropic's Claude and answers live. To delete a household: the gear
> (top right) → "Delete your household".

(Checked against `main`: the Preferences sheet's link reads "Delete your
household" and calls `/api/household/delete`; it refuses household 1.)

## 10. Pricing — decision page for Emily

**What a household costs to run** (from measured numbers in the Decision
log): one chat turn ≈ 16¢; a light month of chat was $1.78, most of it
cache writes; generating a week ≈ $0.13–0.23. The target written into the
code is $1 per household per month. A household that chats daily and plans
every week is more like **$3–6 a month** today. Hosting (Railway) is small
next to that.

**What's true today:** the app has **no in-app purchase code**. No StoreKit
plugin, no receipt check on the server. Charging inside the iPhone app needs
both, and Apple requires its own in-app purchase for a subscription that
unlocks the app (guideline 3.1.1). That's a build, not a setting.

| Option | What it means | For | Against |
|---|---|---|---|
| **A. Free** | Free download, no payment anywhere | Ships with what exists; most installs; real feedback | Every household costs $3–6/month with no income; no cap |
| **B. Free, then subscribe** | Free at launch; add a subscription in a later release, early households grandfathered or given a long trial | Launch now, charge once value is proven | Changing free→paid later annoys early users unless handled kindly |
| **C. Subscription with free trial** | e.g. **$6.99/month or $59.99/year**, 14-day or 1-month free trial, via Apple | Covers cost from day one; the trial lets people try the whole thing | Needs the StoreKit build first, which delays launch; fewer installs |
| D. Paid upfront | e.g. $9.99 once | Simple | Ongoing AI cost with one-time income; worst fit |

**Apple's cut:** 30%, or **15% through the App Store Small Business
Program** (under $1M a year). Enroll the day the developer account exists —
it's a form in App Store Connect, and it applies to subscriptions from the
first day of the next month (subscriptions also drop to 15% after a
subscriber's first year regardless).

**Recommendation: B.** Launch free so the store review and the first
strangers aren't waiting on a payments build, with a soft guard against
runaway cost (the existing per-household rate limits). Build the
subscription (option C's prices, 1-month free trial, Small Business
Program) as the next App Store card, and decide grandfathering then. If
Emily would rather never give it away, C — and the StoreKit card comes
before submission.

**Decision needed from Emily:** A, B or C; the prices if C; approve the
words above.
