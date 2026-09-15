# Pomona — Product Flow Map

The inventory of everything a person can do in Pomona, described from their side of
the screen. Owned by the Product Owner mode (`.claude/skills/pomona-product-owner`);
read by every session; edited only after a real walk of the running app.

**States:** `Not walked` (stub — name and moment only) · `Walked` (path recorded from a
real walk, friction listed) · `Refined` (cards from the walk are Done and it has been
re-walked) · `Reference` (Emily has said "this is what the rest should feel like").

**Rule:** never write a step you have not seen. Stubs stay stubs until walked.

Created 2026-09-13 from the app's screens and route groups. Nothing below has been
walked yet. Suggested first walks: The morning check-in or First run, then Plan the
week → Shop → Cook tonight.

**The feeling to design for (Emily, 2026-09-14): "thank goodness for Pomona."** Pomona is
the household's central hub — opened every morning and whenever they need to check on
things — and it keeps customers by being so deep in their routine that they never want
to stop. Each flow below is judged on the load it removes *and* on whether it earns a
place in the daily routine.

---

## The hub

### 0. The morning check-in (and "let me check on things")
- **Who and when:** either adult, 7am, coffee in hand, before the day starts — and again
  whenever something comes up during the day and they want to know where things stand.
- **Job / load phase:** one glance and they know today: what's for dinner, what needs
  taking out, what's due, what the other adult did, what needs a call. (Anticipate +
  monitor delivered as a dashboard — the whole model on one screen.)
- **Why it's flow 0:** Emily's 2026-09-14 vision — Pomona is the household's central hub,
  the place they go in the mornings and when they need to check on things. Retention
  comes from being deep in the routine; this is the routine. It is the flow that decides
  whether the app becomes a habit or a tool.
- **State:** `Not walked`. Today the home screen, the "needs you" strip, notifications,
  the morning text and Cook tonight each carry a piece of this; nobody has walked them as
  *one* moment. Expect the walk to be as much about what's missing as what's there.
- **For builders:** home tab in `static/shell.html`, `/api/needs-you*`, `/api/attention`,
  `/api/notifications`, `app/tools/tonight.py`, `app/tools/attention.py`,
  `app/tools/moves.py`, `app/tools/digest.py` (the morning text is the same content,
  delivered out of the app).

## Habits — the moments Pomona attaches to

Added 2026-09-14 (Emily: "yes to all these"). Habits form around moments the household
already has; these flows attach Pomona to those moments. Together with flow 0 they make
the rhythm: morning check-in → "hold this" any time → evening close → weekly reflection
then plan, with the handoff running across all of it for the second adult.

### H1. "Pomona, hold this" — the offload  ★ founding anchor
- **Who and when:** either adult, any moment a small thing lands in their head — in the
  car, at the sink, at 11pm. *We're nearly out of dish soap. Soccer moved to Thursday.
  Nana's coming on the 28th. Ask the dentist about the retainer.*
- **Job / load phase:** put it down so you can stop carrying it, and trust Pomona to do
  the right thing with it — grocery, calendar, occasion, a question for the week, or just
  kept until it matters. (This *is* anticipation load, handed over. The whole model in
  one gesture.)
- **Why it's starred:** Emily, 2026-09-14 — "this was one of the original reasons I made
  the app, so it should be a huge anchor." It is what makes Pomona a hub rather than a
  meal planner: the place things go. The one flow on the list that asks for words, and
  that is fine — it is offloading, not data entry.

**The path as it is (walked 2026-09-15, Emily driving, laptop copy of the household —
see the caveat under State):**
1. Open Pomona → **Who's this?** (Emily / Vineeth) → tap a name.
2. **Now** screen. First time only, a tips sheet: *"Tap for the usual. Type for the
   rest."* → Got it. The **Ask** button sits bottom-right on every screen.
3. Tap **Ask** → a sheet slides up: *"What's on your mind?"*, greeting *"Tell me what
   you'd like different and I'll rework it,"* three suggestion chips (all about tonight's
   dinner), a text box with the cursor already in it, camera and mic. **Three taps from
   cold to typing; one tap from anywhere once in.**
4. Type a thing → send. Pomona replies in prose, sometimes with a green "updated" card
   (Grocery / Kitchen / Week) that has a **View** link, and one or two suggestion chips.
5. Repeat, or Back.

**What it did with three real things:**
- *"add dish soap to the list"* → "Dish soap is on your list." + Grocery updated card.
  Done, five words, checkable. Did **not** offer to keep an eye on it (soap is the
  textbook staple).
- *"We ended up going to the in-laws for dinner last night so didn't make the dinner we
  had planned"* → "Noted — no cooking needed for last night, then. I don't have a plan
  entry on file… nothing to mark or move. If there was a dish you'd planned that you'd
  like pushed to another night this week, just tell me which one and I'll slot it in."
  **Nothing was saved** (checked read-only). Work handed back to her. Builder voice.
  *Caveat:* this copy had no plan for this week, so part of the answer was true here and
  would not be true on her phone — needs a re-run in the real environment.
- *"I already defrosted the shrimp so it's in my fridge and needs to be cooked"* →
  noted where the shrimp is, asked **tonight or tomorrow?**, then "I've put Garlic
  Butter Shrimp with rice on tonight — quick, and uses up the shrimp before it turns,"
  left the shrimp off the grocery list, offered a veggie side. Kitchen updated + Week
  updated cards. **This is the thank-goodness moment, and the standard for the flow.**
  Blemishes: said "moved" when it created the record; the word "inventory" leaked.

**The exits:** Back (sheet closes, Now underneath); View on a card (jumps to that tab);
a suggestion chip (continues in chat). Nothing is lost on Back; nothing is *kept* either
unless Pomona acted on it.

**Moments that matter:** (a) the greeting — what the door says it's for; (b) the moment
Pomona can't act on a thing — does it hold it or drop it; (c) the reply after a
consequence-laden fact ("dinner didn't happen") — does it do the thinking.

- **State:** `Walked` 2026-09-15. **Caveat:** the walk ran on the laptop copy of the
  household, whose week is not Emily's real week (its only approved plan is dated
  October 2027 — leftover test data, not a bug in her life). The defrost tick she made
  on her phone does not exist on this copy. Findings about *behaviour* (the greeting, the
  "Noted" that saves nothing, the voice) stand; findings that depend on *state* (would it
  have connected the skipped dinner to the thawed shrimp?) need a re-run on her phone or
  on a fresh copy of the real data. See the walk-environment rule in the PO skill.

**Friction found:**
- The door is labelled "meal edits" — greeting + all three chips are about dinner;
  nothing says "I'll hold things for you." She translated her thought into a command
  because of it. → card "Ask: the door says 'hold this', not 'meal edits'" (High, Phase 1)
  https://app.notion.com/p/3dc1f4c0523181bd8fedc123c0b31811
- "Noted" that notes nothing — when Pomona can't act, it sounds like it held the thing
  and drops it. No place a held thing lives; no surfacing later. **The founding-anchor
  gap.** → card "'Noted' must never note nothing" (High, Phase 1, Needs Your Call on
  placement) https://app.notion.com/p/3dc1f4c05231813ea265c842b5e0e587
- Skipped dinner → no consequences worked out (move the dish, the thawed protein, the
  groceries bought for it). Work handed back to her. *Emily to re-type the in-laws
  sentence into the real Pomona on her phone; card only after that (on her to-do).*
- A supply item dropped in chat isn't offered as a staple. → card "Something you run
  out of, mentioned in chat, is offered as a staple" (Medium, Phase 1.5)
  https://app.notion.com/p/3dc1f4c05231817fbba1d7c787966bba
- Chat voice slips: "plan entry on file," "reconcile against," "inventory," three
  paragraphs where one would do. → card "Chat voice: one line for what I did…" (Medium,
  Phase 1) https://app.notion.com/p/3dc1f4c0523181aaba2ad46c881657dc
- Small: "moved" vs created; tips sheet says "type for the rest" but the chat greeting
  narrows it back to meals — the two disagree.
- Seen in passing (belongs to flow 0): **WEEK SET** badge while Now says "Quiet day —
  want me to sort dinner, or the whole week?" — two screens, two stories.

**Decisions (Emily, 2026-09-15):** "yes to all, go with your recommended" on the five
moments — greeting rewrite, held things (assumed: strip on Now + full list in What we
know, flagged Needs Your Call), skipped-dinner re-test on her phone before a card,
staple offer from chat, chat voice rules. Standard for the flow = the shrimp exchange.

- **For builders:** `/api/chat*`, `app/agent.py` (greeting, chips, system voice),
  `app/tools/memory.py`, `app/tools/grocery.py` (adds), `app/tools/staples.py`
  (offer-as-staple), `app/tools/inventory.py` (where the shrimp landed),
  `app/tools/weekly_plan.py` / `swap_in_place.py` (skipped-dinner reconciliation),
  `app/tools/holidays.py` / `big_meal.py` (occasions), `app/tools/week_intake.py`
  (questions for the week), `app/calendar_feed.py` (read-only today — a held thing about
  the calendar has nowhere to land yet). Coaching sheet: `/api/coaching`.

### H2. The evening close
- **Who and when:** 8:30pm, kids down, thirty seconds before the phone goes away.
- **Job / load phase:** tomorrow's dinner, what to take out of the freezer tonight,
  anything due tomorrow, anything the other adult needs to know. (Anticipate, done the
  night before — when the load actually lands.)
- **Why it matters for habit:** the bookend to flow 0. Two fixed daily touchpoints
  instead of one, and the morning check-in becomes "did tonight's plan survive?" If
  "did dinner happen?" ever needs confirming, it is one tap here — never its own ritual.
- **State:** `Not walked`. Pieces exist (defrost ask, tomorrow in Cook tonight,
  notification #4) but there is no evening moment as such.
- **For builders:** `app/tools/defrost.py`, `app/tools/tonight.py`,
  `app/tools/notifications.py`, `app/tools/digest.py` (an evening text is the same
  shape as the morning one).

### H3. The handoff — "what changed since you last looked"
- **Who and when:** the other adult opens Pomona — first thing, or after a day away
  from it.
- **Job / load phase:** *she approved the week, added milk, moved Thursday, and left you
  the defrost.* What your partner did, what was left for you. (Monitor, for two people.)
- **Why it matters for habit:** "checking what changed" is the strongest habit in any
  shared tool. Today the per-adult login knows who you are but never tells you what the
  other adult did.
- **State:** `Not walked`. Per-adult crediting exists for approvals, grocery adds,
  pre-shop drops and week questions; cook/prep ticks and chores record no person.
- **For builders:** `app/tools/coordination.py`, `app/tools/moves.py`,
  `app/tools/_shared.py` (`member_id()`), `app/tools/notifications.py`.

### H4. Coming back after a lapse
- **Who and when:** a week (or three) skipped — sick kid, holiday, life. The first
  reopen.
- **Job / load phase:** re-enter without penalty. No backlog of nagging, no "you missed
  six things" — *"Welcome back. Want me to draft this week?"* (Re-anticipate, quietly.)
- **Why it matters for habit:** habits die at the lapse, not at the start. This is a
  retention flow in disguise.
- **State:** `Not walked`. An "Away-and-back" card exists on the board (2026-09-11 role
  check); this is its habit framing.
- **For builders:** `app/tools/attention.py`, `app/tools/notifications.py` (what
  accumulates while away), `app/tools/weekly_plan.py` (re-draft), `app/tools/staples.py`
  (cadence gaps).

### H5. "Pomona noticed" — the weekly reflection
- **Who and when:** Sunday, just before planning the week.
- **Job / load phase:** *"You didn't have to think about dinner five nights this week.
  Tuesdays are tight, so I'll keep them quick. Two things I got wrong…"* Makes the
  invisible work visible, once a week, and shows the app learning. (Monitor, reported
  back; the visible form of "the app gets quieter over time.")
- **Why it matters for habit:** this is where "thank goodness for Pomona" becomes
  something the person *feels* rather than something we hope for. Not stats, not
  streaks — a sentence or two in the app's voice.
- **State:** `Not walked`. Nothing like it exists; `usage.py`, `plan_quality.py`,
  `meal_variety.py` and `taste_verdict.py` hold the raw material.
- **For builders:** `app/tools/usage.py`, `app/tools/plan_quality.py`,
  `app/tools/meal_variety.py`, `app/tools/taste_verdict.py`, `app/tools/rhythm.py`.

**Deliberately not flows:** streaks, scores or "4 weeks in a row" (the no-game
guardrail — they build anxiety and collapse at the lapse H4 protects); a standalone
daily "did dinner happen?" check-in (a chore the app would be administering — infer it,
confirm with one tap inside H2 if needed).

## Core job — meals on the table

### 1. First run
- **Who and when:** a new household, phone in hand, just got the link. The first five
  minutes decide whether they come back.
- **Job / load phase:** get set up without it feeling like data entry; end with a plan
  they didn't have to make. (Identify + decide, made into a few taps.)
- **State:** `Not walked`. Contains the welcome flow, which Emily has named the voice
  reference for the app — the walk should confirm whether the whole flow earns `Reference`.
- **For builders:** `static/login.html`, `static/onboarding.html`, `static/meal-setup.html`,
  `/api/onboarding/*`, `app/tools/week_intake.py`, `app/tools/rhythm.py`.

### 2. Plan the week
- **Who and when:** the adult who carries dinners, usually Sunday, ten minutes, the week
  ahead is a blur.
- **Job / load phase:** a week of dinners they can say yes to, shaped to the calendar
  and the household. (Anticipate + identify done by the app; decide is a tap.)
- **State:** `Not walked`. Most recent feedback: Emily's 2026-09-13 phone test (19 cards)
  and the "shaping the draft" work (plate chips, add-a-carb, chat on the draft).
- **For builders:** `static/plan-week.html`, `app/tools/weekly_plan.py`,
  `app/tools/proposals.py`, `app/tools/plates.py`, `app/tools/plate_parts.py`,
  `app/tools/swap_in_place.py`, `app/tools/meal_variety.py`, `app/tools/plan_quality.py`.

### 3. Shop
- **Who and when:** whoever is going to the store — sometimes the other adult — in the
  car park or the aisle, one-handed.
- **Job / load phase:** buy what the week needs and nothing twice. (Anticipate: staples
  due; monitor: what's already at home.)
- **State:** `Not walked`. Includes the pre-shop check, by-store view, staples, spices,
  carried-over items.
- **For builders:** Shop tab in `static/shell.html`, `/api/grocery-list/*`,
  `app/tools/grocery.py`, `app/tools/pre_shop.py`, `app/tools/stores.py`,
  `app/tools/staples.py`, `app/tools/spices.py`, `app/tools/quantities.py`.

### 4. Cook tonight
- **Who and when:** 5:30pm, whoever is cooking, kids underfoot.
- **Job / load phase:** know what's for dinner, what to take out, what to start first —
  without opening the plan and thinking. (Anticipate: defrost, prep; monitor: it happened.)
- **State:** `Not walked`. Includes the Cooker, defrost asks, prep sessions, cook-ahead.
- **For builders:** `/api/cooker/*`, `/api/prep/*`, `app/tools/cooker.py`,
  `app/tools/tonight.py`, `app/tools/defrost.py`, `app/tools/prep_sessions.py`,
  `app/tools/cook_ahead.py`, `app/tools/batch_components.py`.

### 5. Leftovers and the plan changing mid-week
- **Who and when:** Wednesday, plans moved, there's half a lasagne.
- **Job / load phase:** adjust without re-planning. (Monitor + re-decide, cheaply.)
- **State:** `Not walked`. Emily's 2026-09-13 decision: leftovers = a screen *before*
  sorting.
- **For builders:** `app/tools/leftovers.py`, `app/tools/swap_in_place.py`,
  `app/tools/attendance.py`, `app/tools/slot_needs.py`.

## Around the core job

### 6. Ask Pomona (chat)
- **Who and when:** any moment the screens don't have an answer for.
- **Job / load phase:** say it in words and have it done. (Identify + decide by voice/text.)
- **State:** `Not walked`. Surfaces on the draft ("chat on the draft"), in the shell, and
  in onboarding — a module review candidate: is it the same thing in each place?
- **For builders:** `/api/chat*`, `app/agent.py`, `app/tools/__init__.py` tool registry.

### 7. What we know (household memory)
- **Who and when:** when the app got something wrong, or the household changed.
- **Job / load phase:** see what the app believes and fix it in one place. (Trust.)
- **State:** `Not walked`.
- **For builders:** `/api/memory/*`, `/api/facts*`, `app/tools/memory.py`,
  `app/tools/household.py`, `app/tools/preferences.py`.

### 8. The household — people, the second adult, sharing
- **Who and when:** setup, and the day the other adult first opens it.
- **Job / load phase:** the app knows there are two of you; load can be addressed to
  either. (The precondition for moving load, not just lightening it.)
- **State:** `Not walked`. Per-adult login slice 1 is built ("Who's this?"); slice 2 (a
  per-adult secret) is not.
- **For builders:** `static/share.html`, `static/member-share.html`, `/api/members`,
  `/api/people`, `/api/member-share`, `app/households.py`, `app/tools/sharing.py`,
  `app/tools/coordination.py`.

### 9. Being reached — notifications, the morning text, "needs you"
- **Who and when:** away from the app, in the morning, or when something needs a call.
- **Job / load phase:** anticipation delivered *out* of the app. (Anticipate + monitor,
  without opening anything.)
- **State:** `Not walked`. Morning text needs Twilio (Emily's to-do); the bell is behind
  a flag; web/native push is an open card.
- **For builders:** `/api/notifications*`, `/api/needs-you*`, `/api/morning-text`,
  `/api/attention`, `app/tools/notifications.py`, `app/tools/digest.py`,
  `app/tools/attention.py`, `app/tools/moves.py`.

### 10. Calendar
- **Who and when:** setup, then invisibly every week.
- **Job / load phase:** the plan already knows Tuesday has a 6pm thing. (Anticipate.)
- **State:** `Not walked`. Meals-only so far; "scheduling beyond meals" is next to scope.
- **For builders:** `/api/calendar*`, `app/calendar_feed.py`.

### 11. Recipes — import, photo, the household's own
- **Who and when:** "we make this all the time, why doesn't it know?"
- **Job / load phase:** the plan draws on what this family actually eats. (Identify.)
- **State:** `Not walked`.
- **For builders:** `app/recipe_import.py`, `app/recipe_photos.py`, `app/tools/recipes.py`,
  `app/tools/taste_verdict.py`.

### 12. Occasions and holidays
- **Who and when:** two weeks before Thanksgiving; the week a birthday lands on a Tuesday.
- **Job / load phase:** awareness, not hosting (Emily, 2026-09-11). (Anticipate.)
- **State:** `Not walked`. Holidays slice 2 merged 2026-09-13; big-meal card exists.
- **For builders:** `/api/holidays*`, `app/tools/holidays.py`, `app/tools/big_meal.py`.

### 13. The feedback loop — from "something's off" to a better app
- **Who and when:** two people. *The user*, the moment something goes wrong or feels
  wrong, in their own words. *Emily*, weekly, deciding what to fix and what to build.
- **Job / load phase:** for the user — be heard, once, without a form. For Emily — see
  the pattern across everyone's reports without reading every one, and turn it into
  (1) bugs to fix and (2) features to build. (Monitor, for the product itself.)
- **Why it's a flow of its own (Emily, 2026-09-15):** "a user fills out a bug, that bug
  goes into a memory, then the AI analyses the trends across all the bugs and shares
  them — for the bugs to be fixed, but also for feature improvements." The loop closes
  only when the user's words reach a card and, ideally, the user finds out.

**The path as it should be** (five stages — the walk records which exist):
1. **Say it** — the user reports from wherever they are, in one gesture, no category
   picker, no form: what happened, what they were trying to do. Same front door as
   "hold this" if possible — a report is just a held thing addressed to us.
2. **Keep it** — stored verbatim, with who, when, which flow/screen, and what the app
   was doing (shape only, no private content). This is the product's memory of its
   own failures.
3. **Notice patterns** — the AI reads every report and groups them: same friction from
   two households, one household hitting the same wall twice, a flow nobody reports on
   because nobody uses it. Outputs a short weekly read in plain language.
4. **Route it** — each pattern becomes either a **bug card** (something broken — to the
   loop) or a **feature/improvement card** (something people are working around — to
   the Product Owner mode for the map first). One observation is a note; two is a
   pattern; a pattern in a moment that matters is a priority.
5. **Close the loop** — the user who reported it learns it was heard and, when it
   ships, that it's fixed. "Thank goodness for Pomona" applies to being listened to as
   much as to dinner.

- **State:** `Not walked`. **What exists today:** stage 1 partly (a "Something not
  working?" form — what happened + what you were trying to do — walk it to see how many
  taps away it is); stage 2 fully (verbatim, shape-only context, deliberately never
  categorised — see `app/tools/feedback.py`). **What doesn't:** stage 3 (nothing reads
  the reports except `observability_report.py --feedback`, at a terminal, by a person);
  stage 4 (no path from a report to the Loop Board); stage 5 (the user never hears
  back). The Product Owner mode's "feedback session" is stages 3–4 done by hand until
  they are built; the Notion Feedback log (pending Emily's yes) is where the by-hand
  version lives.
- **Decision to bring Emily:** where stage 3 runs — inside the app (an admin view she
  opens), in the overnight routine (a weekly report to her), or in a PO session. And
  whether the same door should also take *non-bug* feedback ("I wish it…"), which is
  where the feature signal mostly lives.
- **For builders:** `/api/feedback`, `app/tools/feedback.py`, `observability_report.py`
  (`--feedback`), `app/tools/usage.py` (what people actually use — the "nobody reports
  on it because nobody uses it" half), the Loop Board (Notion) for stage 4.

## Not for testers yet

### 14. Chores
- **Who and when:** the adults, splitting home upkeep. Not kid-centred, no points.
- **State:** `Not walked`. OFF for Emily's household until she asks; scoped 2026-09-11
  (Plan|Chores toggle, 20 cards). Walk once she turns it on.
- **For builders:** `static/chores-setup.html`, `/api/chores*`, `/api/onboarding/chores/*`,
  `app/tools/chores.py`, `app/tools/chore_starter.py`, `set_chores_enabled.py`.

### 15. Inventory
- **Who and when:** deferred as policy (2026-09-01). Lightweight background; no new
  investment; no one should have to do inventory work to complete the core loop.
- **State:** `Not walked`. Walk only to confirm it is *not* in anyone's way.
- **For builders:** `static/inventory.html`, `/api/inventory/*`, `app/tools/inventory.py`.

---

## Modules (cross-flow capabilities) — for module reviews

Stubs; a review fills in *promise · where it surfaces · where it's inconsistent · what
it assumes about the household*.

- **Chat / Ask Pomona** — surfaces in flows 1, 2, 6.
- **Per-adult identity** — flows 2, 3, 8, 9; cook/prep ticks and chore completion still
  record no person.
- **Staples** — flows 3, 7; learned cadence; never touches inventory.
- **Notifications** — flow 9; bell, morning text, notification #4 to the other adult.
- **Copy and voice** — every flow; `DESIGN_SYSTEM.md` and the seven rules.

## Decisions log (product direction, dated)

- 2026-09-15 — Emily: the feedback loop is a flow to map — user reports a bug → stored as
  memory → AI analyses trends across all reports → shares them for (1) bugs to fix and
  (2) feature improvements. Flow 13 expanded from "in-app feedback" to the full loop.
- 2026-09-14 — Emily: yes to all five habit flows (H1–H5). "Pomona, hold this" is a
  founding anchor: "one of the original reasons I made the app, so it should be a huge
  anchor." Streaks/scores and a standalone daily did-it-happen check are ruled out.
- 2026-09-14 — Emily: Pomona is the dashboard and central hub the household goes to in
  the mornings and when they need to check on things. Retention = getting deep into their
  routines so they depend on it and never want to stop; the feeling is "thank goodness
  for Pomona." Added as anchor 7, quality-bar question 10 (routine test), and flow 0.
- 2026-09-13 — Emily: product work needs a dedicated Product Owner mode; flows and
  modules mapped intentionally; UX at the top; more real user feedback. This map created.
