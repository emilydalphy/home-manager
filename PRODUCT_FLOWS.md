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

**The path as it is (walked 2026-09-15, Emily driving, laptop copy of the household —
see the caveat under State):**
1. Open Pomona → **Who's this?** (Emily / Vineeth) → tap a name. *(Asked again on
   re-open in the panel despite "I'll remember on this device" — likely the panel
   forgetting, not the app; check on her phone.)*
2. **Now.** Header: *Tuesday, Sep 15 · 0 of 2 done*. Then, top to bottom: a **THIS
   WEEK** card *"Shall I put Sep 14–20 together?"*; a **DINNER** card *"Tomorrow needs a
   dinner" · Pick*; today's timeline — **NOW · Shop for tonight · 3 items · by 6:05**,
   then **6:30 Garlic Butter Shrimp · dinner · 25 min**; a big orange **Open the list**
   button with **Ask** beside it. Tabs: Now · Plan · Shop · Cook.
   Emily: *"the actions were really unclear on the Now screen, so I just clicked Shop
   for tonight."*
3. Tap **Shop for tonight** → **Shop** tab. *Tuesday, Sep 15 · 3 things · 2 stops* —
   Costco: whole chicken (2), Dish soap; T&T: rice (2 cups). **Start the trip** / See
   the week. A **"1" badge** appears on the Now tab the moment you leave it.
4. Emily, asked what she'd do next at 7am: *"I would go into the plan for the day to
   know what to expect for meals."* Tap **Plan** → on this copy it opens on **Oct 11–17,
   2027 · APPROVED** (the stale test plan), "Your week is set — 2 meals, 1 recipe, one
   list of 3 ingredients," a "One quick one before you go — Anything in the freezer?
   whole chicken" card, seven day tiles, and Monday's three slots all *Nothing yet · Pick*.
   Today (Sep 15) is not on the screen at all.

**The exits:** close the app (nothing to close — nothing was asked that has to be
answered); Open the list → Shop; Pick → tomorrow's dinner options; the THIS WEEK card →
week planning; Ask. Nothing on Now records that the morning check happened.

**Moments that matter:** (a) the first two seconds of Now — does it tell today's story
or list jobs; (b) the day's meals in one glance without leaving Now (Emily's own next
tap); (c) whether anything on the screen is *wrong* — one invented deadline and the
morning is un-trusted.

- **State:** `Walked` 2026-09-15 (laptop copy, Emily driving, viewed in the Browser
  pane). **Caveat:** on this copy the only approved plan is Oct 2027, there is no plan
  for this week, and tonight's shrimp came from the H1 chat walk. The findings about
  the *shape* of Now (competing asks, no greeting, "0 of 2 done", the shop-for-tonight
  logic, the badge) are behaviour and stand on her phone. Whether Plan opens on today
  vs. a future approved week, and what Now shows when a real week *is* set, need her
  phone.

**Friction found:**
- **"The actions were really unclear"** (Emily's words, step 2). Four asks compete —
  put the week together? pick tomorrow? shop for tonight? Open the list (orange) — and
  two overlap: saying yes to the week solves tomorrow, so "Pick" asks twice. Now is a
  job list, not today's story. → card "Now tells today's story for the household (not a job list)" (High, Phase 1)
  https://app.notion.com/p/3dc1f4c052318116823bde70ac45ef91
- **"Shop for tonight · by 6:05" is not true.** Tonight is the shrimp (already in the
  fridge); the three things on the list are chicken, rice, dish soap — none of them for
  tonight. Pomona says "shop for tonight" whenever the list has anything on it and a
  cook is within reach; it never checks whether the items are *for* that cook. At 7am it
  hands over a deadline that doesn't exist. → card "'Shop for tonight' only when something on the list is for tonight" (Bug,
  High, Phase 1) https://app.notion.com/p/3dc1f4c0523181838fe2f2ca8c708ebf
- **The day's meals aren't on Now.** Emily's real next tap was Plan, "to know what to
  expect for meals." Now shows dinner only, as a timeline row; breakfast and lunch (and
  who's home) live a tab away. → card "Every planned meal today shows on Now, not just dinner" (Medium, Phase 1)
  https://app.notion.com/p/3dc1f4c0523181c085d0f88443ecebce
- **"0 of 2 done" at 7am** reads as a scoreboard before the day has started; nothing
  says good morning, who it is talking to, or what the other adult did. → folded into the
  "Now tells today's story" card (cut "0 of 2 done"); "what Vineeth did" is H3's job.
- **The "1" on Now never goes away** while "Tomorrow needs a dinner" is open — a badge
  that is always lit is one you stop reading. → folded into the "Now tells
  today's story" card (cut until it can mean *new since you last looked*, H3).
- Two screens, two stories (seen in H1 too): Now asks "Shall I put Sep 14–20 together?"
  while Plan says "Your week is set." On this copy the set week is Oct 2027, so partly
  a state artefact — but Plan opening on a future approved week instead of today is a
  real question. *Check on her phone.*
- Small: Who's this? re-asked in the panel (probably the panel); "Anything in the
  freezer? whole chicken" sits on Plan while the same chicken is on the shopping list
  — the two don't know about each other.
- **Seen from here, belongs to flow 2 (Plan the week → "Any days that are
  different?"):** the **"Who's eating?"** circles (E / V). Everyone starts as *in*; a
  tap marks them *out*, but a plain lettered circle reads as "tap to choose me," and
  the orange focus glow after a tap reads as "selected" — the opposite of what
  happened. Emily: *"the clicking doesn't make sense — the click of the initial
  removes them."* → **Decision 2026-09-15: label becomes "Is anyone out?"** (tap = mark
  out; the question now matches the gesture). → card "'Who's eating?' becomes
  'Is anyone out?'" (Medium, Phase 1) https://app.notion.com/p/3dc1f4c05231815da491c1c6760bf85a

**Decisions (Emily, 2026-09-15):**
- "Who's eating?" → **"Is anyone out?"** — the label says what a tap does.
- Direction, in her words: *"There is a lot that's been built, but I want to be really
  clear on if we even need everything. I want everything to have a clear purpose."*
  Applies to every element on Now before any of moments 1–4 become cards: each card,
  badge and button on the hub must name its purpose in one line or go.
- **Now is for the household**, not the person who opened it — "yes for the household."
- Purpose audit of Now (2026-09-15): keep the date line, the timeline, the one orange
  button, the holiday pill, the afternoon "Still good?", held things; merge the week ask
  with "Tomorrow needs a dinner"; cut "0 of 2 done", the always-lit tab badge, the bell
  (already hidden). Emily: "yes write those cards." Five moments → three cards + one
  under flow 2. The hole the household framing exposes: nothing says what the other
  adult did (H3).

- **For builders:** home tab in `static/shell.html` / `static/shell.js` (Now =
  `panel-today`, the timeline = `day-strip`, tile taps = `runTodayMoveAction`),
  `app/tools/moves.py` (`_shop_moves` — the "Shop for tonight" move, `SHOP_HORIZON_HOURS`,
  deadline from `cooker.planned_start_for`), `/api/needs-you` (`app/tools/attention.py` —
  the dinner_decision + shop_run items and the tab badge), `/api/notifications`,
  `app/tools/tonight.py`, `app/tools/digest.py` (the morning text is the same content,
  delivered out of the app), `static/plan-week.html` (which week Plan opens on).

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

- **State:** `Walked` 2026-09-15; four cards from the walk Done the same day — **due a
  re-walk** (on her phone) before it can be `Refined`. **Caveat:** the walk ran on the laptop copy of the
  household, whose week is not Emily's real week (its only approved plan is dated
  October 2027 — leftover test data, not a bug in her life). The defrost tick she made
  on her phone does not exist on this copy. Findings about *behaviour* (the greeting, the
  "Noted" that saves nothing, the voice) stand; findings that depend on *state* (would it
  have connected the skipped dinner to the thawed shrimp?) need a re-run on her phone or
  on a fresh copy of the real data. See the walk-environment rule in the PO skill.

**Friction found:**
- The door is labelled "meal edits" — greeting + all three chips are about dinner;
  nothing says "I'll hold things for you." She translated her thought into a command
  because of it. → card "Ask: the door says 'hold this', not 'meal edits'" (High, Phase 1) → Done, merged c779345 2026-09-15
  https://app.notion.com/p/3dc1f4c0523181bd8fedc123c0b31811
- "Noted" that notes nothing — when Pomona can't act, it sounds like it held the thing
  and drops it. No place a held thing lives; no surfacing later. **The founding-anchor
  gap.** → card "'Noted' must never note nothing" (High, Phase 1, Needs Your Call on
  placement) → Done, merged 3dc9897 2026-09-15 — placement (Now card + What we know) still
  Emily's to confirm https://app.notion.com/p/3dc1f4c05231813ea265c842b5e0e587
- Skipped dinner → no consequences worked out (move the dish, the thawed protein, the
  groceries bought for it). Work handed back to her. *Emily to re-type the in-laws
  sentence into the real Pomona on her phone; card only after that (on her to-do).*
- A supply item dropped in chat isn't offered as a staple. → card "Something you run
  out of, mentioned in chat, is offered as a staple" (Medium, Phase 1.5) → Done, merged 6435ed5 2026-09-15
  https://app.notion.com/p/3dc1f4c05231817fbba1d7c787966bba
- Chat voice slips: "plan entry on file," "reconcile against," "inventory," three
  paragraphs where one would do. → card "Chat voice: one line for what I did…" (Medium,
  Phase 1) → Done, merged 40cd499 2026-09-15 https://app.notion.com/p/3dc1f4c0523181aaba2ad46c881657dc
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

**The path as it is (walked 2026-09-15, Emily driving in the Browser pane, laptop copy
of the household — the copy's "next week" was Oct 18–24, 2027; dates ignored, behaviour
judged):**
1. **Plan** tab → **Plan next week ›**.
2. **1 of 4 · "Any days that are different this week?"** — *Tap a day. Out, home late,
   guests — for any meal.* Seven day tiles; tapping one opens a day sheet (Dinner tags:
   Nothing special / Nobody home / Short on time / Got time tonight / Hosting guests /
   Leftovers; **Who's eating?** E / V initials per meal; guest steppers for lunch and
   breakfast). Card: *"Anyone away this week? I'll skip planning and shopping while
   you're gone."* Button **Next — 1 day noted**.
3. **2 of 4 · "Any lunches on the go this week?"** — *I'll keep those to food that packs
   cold.* Seven day chips. **‹ Back · Nothing on the go · Next.**
4. **3 of 4 · "What are you in the mood for?"** — *As many as you like. I won't make
   every night the same.* Nine mood chips; **Cuisines you fancy** (*A starting point —
   I'll remember whatever you tap*), ten more. **‹ Back · Surprise me · Next.**
5. **4 of 4 · "Anything else I should plan around?"** — one text box, hint *"Friday is
   pizza night. I want to use the lamb in the freezer."* **‹ Back · Nothing else · Draft
   my week.**
6. **Building your week** — *○ Looking at who's home which nights.* *"No need to wait
   here — I'll keep going, and the draft will be on Plan once it's done. Nothing is
   approved until you say so."* Button greyed **Drafting your week…** (~20 s; the status
   line never changed; "Nothing else" still showing).
7. **The draft, on Plan.** Band: **DRAFT · Oct 18–24 · 7 days · a draft, your turn.**
   Toggle **What we're eating | Which days** (menu view is default). Menu view:
   BREAKFASTS (3 dishes, e.g. *Greek Yogurt … 4 mornings*), LUNCHES (5, two of them
   leftovers marked *nothing to cook*), DINNERS (7, each *1 night*), SNACKS (5), every
   row with a − / + stepper and **Change** / **Change one**. Sticky **Approve and build
   my shopping list**, Ask beside it.
8. **Which days**: *DINNERS · drag to move a night · Bar = how long* — seven rows, day,
   dish, a time bar (25–110 min), drag handles. Dinners only.
9. **Approve** → **All set.** *Oct 18–24 is planned, and the list is built.* Toast:
   *"Approved. I'll get your list together. Open the list."* Then **"Two quick ones
   before you go"**: (a) *Anything in the freezer? Tap what's frozen and I'll tell you
   when to move it to the fridge* — eight chips (Bacon … Shrimp … whole chicken) →
   **Add to the schedule / None — all fresh**; (b) *Do you want to batch cook any of
   these?* — **nine** questions, one per repeated dish, including Trail mix, Apple slices
   with peanut butter, Cheese and crackers, Sliced cucumbers with tzatziki, Greek
   yogurt (*"It'd cook on Tuesday. Which other days should it cover?"*) → **Batch cook
   these / Cook each on its own**. Then **21 MEALS · 13 RECIPES · 66 INGREDIENTS**,
   *Nothing to thaw before Monday.* **See the week / Open the list · 66 ingredients.**

**The exits:** ‹ Plan at any intake step (answers so far are saved); leaving during
"Building your week" (the draft lands on Plan anyway); the draft left unapproved (Now
keeps asking); Approve → All set → Open the list (flow 3) or See the week.

**Moments that matter:** (a) the first line of the draft — does it show it *listened*;
(b) the intake's length — four screens of telling before one of getting; (c) the
after-approve screen — the last thing they see, and today it is ten questions.

- **State:** `Walked` 2026-09-15 (laptop copy, Emily driving; Emily's taps on "Which
  days" and "Approve" did not register in the pane and were re-tapped by the session —
  a pane quirk, not app behaviour, unless it recurs on her phone). Behaviour findings
  stand; the draft's *content* (dishes, variety vs. previous weeks) is the copy's, not
  hers.

**Friction found:**
- **Four screens of telling before one of getting.** Steps 1–3 each ask something
  Pomona could know or remember: which days are different (the calendar it already
  reads); which lunches leave the house (it pre-answers this from onboarding rhythm but
  still shows the screen); moods and cuisines (it says "I'll remember" but week 30 looks
  like week 1). → card "Week intake: one screen of what Pomona already knows, not four of asking"
  (High, Phase 1, Design) https://app.notion.com/p/3dc1f4c052318195974dc1c92c1959f8
- **The draft doesn't say what it did with what you told it.** No opening line, no
  reason next to a dish (Pomona stores one per dish and shows none). Monday's +2 guests
  are invisible on both views. Trust has to be earned by reading twenty rows. → card "The draft says what it did with what you told it" (High, Phase 1)
  https://app.notion.com/p/3dc1f4c0523181d38224d1773b1359aa
- **"Two quick ones before you go" is ten questions**, and half of them are nonsense
  in a person's ears — *"Trail mix … it'd cook on Tuesday. Which other days should it
  cover?"* Snacks and yogurt are not cooked. The freezer chips include the shrimp that
  was eaten tonight and the whole chicken that is on the shopping list (so not in the
  freezer). Asked *after* approval, at the moment the person wants to leave. → card "After approve: only real questions, asked once — not ten" (Bug, High,
  Phase 1) https://app.notion.com/p/3dc1f4c05231813ba149e22bfa0aa64d
- **Menu view is the default; the week view is behind a toggle.** For "Sunday, ten
  minutes, say yes," the seven-line Which days is the glance and the menu is the
  detail. Steppers on every row invite fiddling. → **Emily, 2026-09-15: keep the menu
  first, as today.** No card.
- Small: "Nothing on the go" / "Surprise me" / "Nothing else" each duplicate "Next
  with nothing chosen"; "Leftover Turkey Club Wraps" vs *nothing to cook* for the same
  idea; the building screen's status line never advances; "Nothing else" still tappable
  while drafting; guest counts tapped by accident save silently and count as "1 day
  noted".
- **Who's eating? → "Is anyone out?"** → card (Medium, Phase 1)
  https://app.notion.com/p/3dc1f4c05231815da491c1c6760bf85a (decided during the flow 0
  walk, same day).

**Decisions (Emily, 2026-09-15):** "go with your recommendations — ask me questions
directly" on A–C (one-screen intake, the draft explains itself, only real questions
after approve). D (which view is the draft's front door): **"What we're eating" stays first, as today** —
Emily's answer, 2026-09-15, asked directly. The by-type menu is the front door; Which
days stays behind the toggle. No card.

**Feedback round 2026-09-20 (Emily, her phone, Sunday morning, re-planning a week
that already had a draft — "Pomona Flow Feedback.pdf", 10 annotated screens).** Mockups:
https://claude.ai/artifact/9dE1dRcbWKCQKchDEYJJBx (trimmed to the chosen boards on
2026-09-21). Her framing: *the onboarding screens have a motion the week planning should
share* — one question a screen, the "Cooking up your week" building screen, the day-card
reveal. Observations, in her words, tagged to the step:

- (entry) "I want to re-plan my week, but the option to do so is really hidden." Re-plan
  lived under **More ···** in the draft's footer. → board A2 (pill in the band). Chosen.
- (entry) "It's Sunday, but the days are showing from yesterday … I would need to select
  my own day." Re-plan offered Sep 19–25 on Sep 20. → board A3 **Starting when?** Kept.
- (step 1, day sheet) "This screen needs to be more focused. It's hard to understand what
  exactly they need to answer." → board B1b (a row per person; her wording for the
  pills: *No breakfast / No lunch / No dinner*). Chosen.
- (step 1, away card) "Confusing — it should be more about if they are going to be away
  for a longer period." → *Away for a few nights? — Tell me when you're travelling or gone
  for a stretch — I'll skip those meals altogether.* (hers, via the copywriter).
- (step 3) "The location of Surprise me makes it seem like it's not a viable option."
  → board B2 (a card at the top). Chosen.
- (step 3) "I want Mexican this week, but because it's not under my preferred it's not
  listed." → **+ Add one** pill → its own screen → *Add Mexican* → back with it chosen
  (boards B2 → B4a → B4b).
- (step 6) "This building screen should use the same building screen as in the
  onboarding." → board C1. "Great."
- (draft) "I gave it a detailed description — Mexican for lunch, chicken breast, potatoes
  and veggies for dinner — and it didn't listen." Only Monday got both. → widened the
  existing High card *The draft says what it did with what you told it*.
- (draft) "Bring back the screen where I'm able to see just the meals that are selected,
  then be able to see the days." → board C2 (*What we're eating* front door, meals only,
  no day notes) + C3 (*Which days* behind the toggle).
- (draft) "You're continuously giving me the same food recommendations as previous
  weeks." → two-week variety window, said in the opener when true.
- (chat) "If I said I want to fix all the breakfasts then you should just do that." and
  "It's confusing where the user needs to go from here." → board C4.
- (draft) "If there is a conflict for an allergy, just don't suggest anything that fits
  that. Also make sure the formatting is fixed." → no board: never draft or offer an
  allergen dish; the "Keep it anyway" card and its overlap go together.

**Decisions (Emily, 2026-09-21):** as above, plus: C1 great; C2 meals only; "once you've
got them filed run the loop." Assumptions built in for her to flip: Re-plan pre-fills last
answers; two-week variety window; initials clash = shortest distinct prefix for the
colliding people only; a child gets a row on the day sheet.

**Cards filed 2026-09-21 and BUILT the same day** (all Phase 1 — Beta; every branch
independently verified, two of them twice after a first FAIL; nothing pushed or merged):
- `intake-motion-2026-09-21` (5728 tests): *Starting when?* (today,
  never yesterday) https://app.notion.com/p/3e21f4c052318167a4a9e523ecbaf869 · the intake
  in the onboarding motion https://app.notion.com/p/3e21f4c05231812ca168ec89c669df60 (folds
  the 2026-09-15 "one screen of what Pomona already knows" card) · day sheet row per person
  https://app.notion.com/p/3e21f4c052318103a29dd4b7ac7ed7af (supersedes "Who's eating? →
  Is anyone out?") · Surprise me card https://app.notion.com/p/3e21f4c0523181a38f8ff0bfbccc53b7
  · add a cuisine https://app.notion.com/p/3e21f4c0523181f28ed7de8cd5e0b393 · building
  screen https://app.notion.com/p/3e21f4c0523181a2a92bf7f9349dbbf7 · initials clash
  https://app.notion.com/p/3e21f4c052318166978dd15cd71a53ce (Needs Your Call on the rule).
- `draft-front-door-2026-09-21` (5699): Re-plan pill
  https://app.notion.com/p/3e21f4c0523181f6ae10c31f036dc783 · What we're eating front door
  https://app.notion.com/p/3e21f4c05231814dba3dc3308664ebce · the draft says what it did
  (widened) https://app.notion.com/p/3dc1f4c0523181d38224d1773b1359aa.
- `chat-whole-week-2026-09-21` (5673): https://app.notion.com/p/3e21f4c0523181db8c92fd2f97be0209
- `allergen-hard-block-2026-09-21` (5738): https://app.notion.com/p/3e21f4c05231814e8e07e6795769fb46

**Found while building, for a later card:** a Friday with the current week approved
returns a Fri–Thu window (`suggest_planning_period` clamps after the plan-ahead shift);
the chat About chip keeps the old dish name until the sheet closes; the server still
computes the unused `settle` field.

**Merged to main 2026-09-21 evening** on Emily's "push and merge in the suggested order"
(allergen → chat → draft → intake; one integration fix — the allergen gate dropped the
draft's request report; 5873 tests green on main). Her two remaining calls the same
message: a child gets a row on the day sheet — yes; the initials rule (shortest distinct
prefix) — yes. A second chat had filed ten duplicate cards from this same feedback on
2026-09-20 evening; archived as duplicates, each pointing at the built card.

**Due next:** a re-walk of flow 2 on Emily's phone against the live app once the four
branches are merged, to move it to `Refined`.

**2026-09-25 — "Same as last week?" (Emily chose option A; branch `same-as-last-week`,
not merged).** From the second planned week on, Plan a week opens on one page of last
week's answers — Days · Weekday lunches · Taking lunch with you · Different days · Mood ·
Anything else — each with Change (opens only that question, Done comes back; a changed
row reads "· Changed"), and **Plan this week** drafts from them. Carried: day count,
lunches + prep day, lunches taken out, mood and cuisines. Empty every week: different
days, guests, holidays, anything else. Dates move forward. A first week asks all five
questions. A slot above the rows is kept for the next card, "Bring over from last week".
Card: https://app.notion.com/p/3e41f4c05231817f823ac9c7185903cd

- **For builders:** `static/plan-week.html` (the intake: steps 1–4, day sheet,
  `toggleAvatar`, `SKIP_LABELS`, the building screen), `app/tools/week_intake.py`
  (`_rhythm_packed_lunch_suggestions` — the lunch prefill that exists but doesn't skip
  the step), `static/shell.js` (the draft on Plan: `wk-seg-btn` What we're eating /
  Which days, steppers, `wk-review-go` approve, the All set sheet with the freezer ask
  and batch-cook asks), `app/tools/weekly_plan.py` (approval, `reasoning` per entry —
  stored, unshown), `app/tools/proposals.py`, `app/tools/plates.py`,
  `app/tools/plate_parts.py`, `app/tools/swap_in_place.py`, `app/tools/meal_variety.py`,
  `app/tools/plan_quality.py`, `app/calendar_feed.py` (read-only; not used by intake
  step 1).

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

- 2026-09-17/18 — Emily, core-loop feedback session ("make the one core loop stronger by
  removing the fluff"): 18 cards on the board, mockups at
  https://claude.ai/artifact/3kNwJnrCupMcFNgLcgQ2gU. Flow 1: welcome is four screens (purpose
  line under the hello; "Here's how it works" replaces talk-to-me; the coaching moves to a
  help icon); prep days unlimited, "I don't prep ahead" first; the loading screen reads the
  answers back; the reveal is "Here's week 1" as swipeable day cards (breakfast → lunch →
  dinner, Swap per row, one Approve). Flow 2: Check the week uses the same cards; "Swap the
  meal"; "Approve · Open grocery list"; All set is one thing, then the freezer question as
  its own screen, then the list; batch cooking is assumed from prep days and never asked.
  Flow 0: Now → Today, grouped Shop / Cook, Morning · Afternoon · Evening tags, no "Open the
  list". Flow 4: the recipe is name + servings + ingredients + steps + Start cooking; no
  clocks in Cook; defrost leads are 1/2/3 nights by cut (USDA). Flow 3: sorting has one way;
  the list is the checklist (no trip). Flow 5: Done + Swap inline on the Plan tab; Swap =
  three picks, move a day, or tell me.
- 2026-09-15 — Emily, during the flow 0 walk: "There is a lot that's been built, but I
  want to be really clear on if we even need everything. I want everything to have a
  clear purpose." Purpose-first: every surface, card, badge and button must be able to
  say in one line which load it removes for whom, or it is cut. Grooming and walks apply
  this as the first test.
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
