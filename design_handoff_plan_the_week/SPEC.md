# Plan the Week — design pass

Targets **"Work through the chat prompts/experience for building the weekly plan"** plus the
revisitable preferences screen, the typical-week onboarding step, the "add more context" copy
rewrite, and approve state on Meals.

Prototype: `Plan the Week.dc.html` — clickable, seven steps in the left rail.

---

## 1. Voice — read this before writing any copy

**The app is in the household's service.** Not a peer, not a bot with personality, not a coach.
The closest human model is a very good private chef or house manager: it knows the household well,
it does the work, it asks when a decision is genuinely the client's to make, and it never makes
them feel managed.

**Five rules:**

1. **Offer, don't instruct.** "Shall I put Sep 1–7 together for you?" — not "Plan your week."
   "I'd like your call on this one" — not "Action needed."
2. **First person singular, and it does the work.** "I'll keep it under 20 minutes." "I've put 22
   items on your list." The app is the one carrying the load; the user is the one deciding.
3. **Every question states what it buys them, before they answer.** Never a bare field.
4. **Every answer is acknowledged with its consequence.** Tag a night → "So I'll keep it under 20
   minutes." That's how personalisation becomes visible instead of claimed.
5. **Deference without servility.** "Of course. It'll be waiting under Meals." Never apologetic,
   never eager, never cute. No exclamation marks. No "Oops."

**Words to avoid:** *should, need to, don't forget, let's, oops, great!, you haven't yet.*
**Words that work:** *shall I, I'd suggest, if you'd like, I'll leave that to you, noted, of course.*

| Instead of | Write |
| --- | --- |
| "Plan your week" | "Shall I put next week together for you?" |
| "No dinner set for Wednesday" | "Wednesday I'd rather ask than guess" |
| "Grocery list updated" | "I've put 22 items on your list — six were already in your kitchen, so I left those off" |
| "Dismissed" | "Of course. It'll be waiting under Meals — I won't ask again this week" |
| "Tell me anything" | "The more you tell me, the less you'll swap" |

### The "Why I'm asking" block

Every question screen carries one plum panel (`#F3E6EE`, radius 12, 12×14 padding) directly under
the title:

> **Why I'm asking:** this is what stops me putting a 50-minute braise on the night you're at
> tee-ball. Every night you tag, I plan differently — and I'll tell you how.

It is not decoration. It states the specific failure the answer prevents. If you can't name that
failure, the question shouldn't be asked.

### The "I'll…" acknowledgement

Green (`#4D8A33`), one line, appears the moment an answer is given. Every control that changes
planning behaviour has one. The phrasing is always a bare **"I'll…"** — never "So I'll", which
reads as the app justifying itself rather than getting on with it.

| Answer | Acknowledgement |
| --- | --- |
| Regular night | "I'll plan a normal dinner you cook that evening." |
| Nobody home | "I'll plan nothing and buy nothing for this night." |
| Short on time | "I'll keep it under 20 minutes, or make the night before stretch to cover it." |
| Hosting guests | "I'll scale the recipe and the shopping to the bigger table." |
| Leftovers | "I'll cook enough the night before instead of planning something new." |
| Lunches packed | "I'll keep those 3 to things that travel cold, and plan the rest as hot lunches at home." |
| Guest count | "I'll cook for 7 — 4 adults and 3 children — and shop for that." |

---

## 2. Sunday nudge (Today screen)

Dismissible, plum top rule.

> **NEXT WEEK · WHENEVER SUITS YOU** · Not now
> Shall I put Sep 1–7 together for you?
> Two rounds of questions from me — about five minutes — then I'll draft the week and you tell me
> what to change. Nothing gets bought until you approve it.
> [ Let's plan the week ]

Dismiss → "Of course. It'll be waiting for you under Meals — I won't ask again this week." Nudge
suppressed for the week; the entry point lives permanently on Meals.

---

## 3. Question 1 of 2 — which nights should I work around?

Day-by-day, Mon–Sun. Each day shows what the app already knows in grey ("Tee-ball — you eat at 5",
"Takeout four weeks running") so the household isn't re-answering things it's been told.

**Five tags per day**, each with its own planning consequence:

| Tag | What it does to the plan |
| --- | --- |
| **Regular night** | Normal cooked dinner. Exclusive — selecting it clears the others. Exists so a user working down the list can *affirm* rather than skip; an affirmed night is data, a skipped night is a guess. |
| **Nobody home** | Slot planned empty, and nothing for it reaches the shopping list. |
| **Short on time** | Hard cap of 20 minutes, **or** the previous night is scaled up to cover it. That's the insight: it's not a vague "busy" flag, it's a constraint on cook time and a trigger for cook-once-eat-twice. |
| **Hosting guests** | Opens a follow-up (below). Scales recipe *and* shopping quantities. |
| **Leftovers** | No new meal; the previous night's batch is increased instead. |

### Hosting guests follow-up

Appears inline in that day's card. **Two steppers, not one number and a category** — a single count
plus an "Adults / Adults + kids / Kids" toggle can't say *how many of each*, which is exactly what
portions and quantities need:

> **Who's joining you?**
> Adults and children eat differently, so I need both numbers — it changes the portions and what I'd
> suggest cooking.
> Extra adults    [− 2 +]
> Extra children  [− 0 +]
> **I'll** cook for 6 — 4 adults and 2 children — and shop for that.

Both steppers 0–10. The acknowledgement states the resulting table composition, not just a total,
so a wrong tap is visible immediately. Household size (2 adults, 2 children) comes from What We
Know and is added to the extras.

### Lunches — one compact control

Every lunch gets planned. The question is which ones **leave the house**, because that's the only
thing that changes the food:

> **Which lunches leave the house?**
> I'll plan every lunch either way. Tap the days someone takes it with them and I'll keep those to
> things that travel cold and hold up till noon.

Seven day pills, one card at the bottom — not a per-day tag, which would double the tapping and
clutter the grid.

CTA reflects the state: **"Next — 3 nights noted"** / **"Next — every night is a regular one"**.

---

## 4. Question 2 of 2 — what would you like this week?

> **Why I'm asking:** your setup tells me what you'll eat. This tells me what you actually want
> right now — it's the difference between a correct week and a week you look forward to.

- **In the mood for** — six chips (warm, lighter, on the grill, comfort food, something new, keep it
  cheap). *"I'll lean the week this way without making every night the same."*
- **Cuisines you fancy** — **read from the household's saved cuisine preferences in What We Know**,
  not a fixed global list. *"The ones you've told me you like, from what we know about your
  household. Whatever you tap wins over your usual rotation this week."* Two reasons: the household
  never re-answers a question it has already answered, and the screen visibly reflects its own
  stored profile. Separate from mood because mood is about form and cuisine is about flavour.
  *(Empty state: if no cuisines are saved, fall back to the onboarding cuisine list and write the
  taps back to What We Know.)*
- **Anything else you want to share for this week?** — **multi-line textarea**, min-height 96px, no
  horizontal scroll. *"Anything at all — and if there's a meal you've already decided on, tell me
  here. I won't plan over it, and I'll still shop for it so you're not short on the night."* Framed
  as an open invitation rather than a specific slot, with the already-decided case named in the
  description so it's clearly welcome without narrowing the field.

**Removed: the last-week feedback question.** Per-plan retrospectives belong where the miss happens
(the day it didn't get cooked), not as a toll gate on the next week's planning.

---

## 5. The draft — the whole week, approvable in one pass

**Every meal, every day.** Twenty-one slots, all filled, all driven by the answers — because
approval covers the week and a week with holes in it isn't approvable:

```
Wed 3                          Short on time
  BREAKFAST   Scrambled eggs on toast — ten minutes, and the eggs are in
  LUNCH       Halloumi and couscous — packs cold, no reheating needed — packed
  DINNER      I'd like your call on this one
```

The answers do real work in every slot: a **short-on-time** morning gets grab-and-go rather than a
cooked breakfast; a **packed** lunch gets something that travels cold; a **leftovers** night is fed
by the previous evening being scaled up; a **nobody-home** dinner is the one slot deliberately left
empty, and it says so — *"Out — nothing to cook. You're out — I've planned nothing and bought
nothing."*

Muted styling (`#B0A3A9`, weight 400) is now reserved for that single case. Nothing else in the week
is ever blank, and no slot ever says "you handle these" — planning all three meals is the job.

**Headline: one line, no recap.** *"Your week's here — there's one night I'd like your call on."*
Per-slot reasons (4–9 words) carry the detail without narration.

**Open slots are stated, never silent:**

> Wednesday I'd rather ask than guess: after Monday's chili, everything I have under 20 minutes
> repeats something you've just eaten. Which of these would you prefer?
> · Breakfast for dinner — 12 min
> · Chili one more night — 0 min
> · Takeout, don't plan it — —

Amber card (`#FDF0DC`, 4px `#E8A33A` left rule). The reason names the *constraint* that caused it,
so the ask reads as diligence rather than failure.

**The grocery promise sits in the draft:**
> I haven't put anything on your shopping list yet. Approve the week and I'll build it — **22
> items**.

When something genuinely was left off, and only then, it says so — matching the receipt's shape:
> ...I'll build it — **22 items**. 3 more were already in your kitchen, so I've left those off.

The original line ended "less whatever's already in your kitchen", which promised a subtraction
that had already been applied: the count is what remains *after* the kitchen is checked, never
before. Corrected 2026-09-02.

---

## 6. Approve is a button

Bottom bar: **Approve the week** + **Redo**. With Wednesday still open it reads **"Approve — leave
Wednesday open"**: allowed, but named.

Approved Meals screen shows the same full week plus a green receipt:

> **✓ APPROVED BY YOU · 9:41AM**
> All set. I've put 22 items on your shopping list — six were already in your kitchen, so I left
> those off. Marcus has been told the week is settled.

Closes the gap in the grocery ticket: approval now *is* the thing that builds the list, and it's
reachable without the assistant remembering to offer it. Attribution matters — either adult can
approve, and the other one hears about it.

---

## 7. Meal planning setup (revisitable)

From Meals: *"Weeks not landing how you'd like? Let's adjust your setup →"*

- **How many meals shall I plan?** — steppers, 0–7 per category. *"One breakfast a week is a
  perfectly good answer — I'd rather plan four things you cook than seven you don't."*
- **What I'm working with** — won't-eat, protein rhythm, weeknight time budget, repeats tolerance,
  kitchen kit, table style. All inline-editable. *"Correct me any time — I'd rather know."*
- **Easier to just tell me?** — embedded chat, scoped honestly: *"Some things don't fit in a
  stepper. Describe the shape of it and I'll set all of this for you."* Placeholder is a real
  sentence: *"We're travelling half of September."*

Steppers own numbers, chat owns sentences. That division is what stops the screen feeling like two
apps bolted together.

---

## 8. Onboarding — two added steps

**Step A — "What does a normal week look like at yours?"**
> **Why I'm asking:** this is the single most useful thing you can tell me. A week I understand
> needs almost no correcting later — and it means your very first plan isn't a guess.

Free text plus four starter chips that append example sentences (never a blank page), then
*"Next week specifically — anything already in the diary? I'll build your very first week around it
rather than guessing."*

**Step B — "Two more and I'll leave you alone"**
> **Why I'm asking:** your kit decides which recipes are even possible, and your feeling about
> repeats decides the shape of every week I build.

- **What you've got to cook with** — slow cooker, air fryer, grill, Instant Pot, stand mixer,
  blender, cast iron, no dishwasher. *"I'll only suggest recipes your kitchen can actually make."*
  Highest-value question not currently asked: it prevents impossible suggestions outright.
- **How do you feel about repeats?** — cook-once-eat-twice / one repeat a week / something different
  every night. *"This one changes a lot — it decides whether I cook once and stretch it, or give you
  seven different dinners."* This single answer changes the structure of every week the app builds.

Skip on both: *"Skip — I'll tell you as we go"* — true, and it keeps the flow honest.

**Other questions considered and deliberately left out** (flagged for your call, item 4 below): who
cooks which nights, budget per week, shopping day and store, ambition level, portion size.

---

## 9. Copy rewrites for "add more context" moments

Pattern: **what to give → what it buys you.** Never "tell me anything."

| Moment | Before | After |
| --- | --- | --- |
| Chat bar | "Ask or add anything…" | "The more you tell me, the less you'll swap…" |
| Chat sheet title | "Talk it through" | "Tell me what to change" |
| Onboarding week | (no framing) | "This is the single most useful thing you can tell me." |
| Anything else to share | (bare field) | "Anything at all — and if there's a meal you've already decided on, tell me here." |
| Setup chat | (bare field) | "Some things don't fit in a stepper. Describe the shape of it." |
| Use-it-up card | "Spinach expires in 2 days" | "Your baby spinach turns in two days… I'll work both into next week if you'd like." |

---

## 10. What the model still owns

Screens own sequence, pacing, and the guarantee a question gets asked at all. The assistant owns
recipe choice, per-slot reasons, the open-slot explanation, and anything typed in chat. The
onboarding-interview block at `agent.py:143-158` can shrink substantially — the flow can no longer
be skipped into.

The voice rules in §1 belong in `SYSTEM_PROMPT` verbatim, since the assistant's free-text replies
have to sit beside this copy without a seam.

---

## Open questions for you

1. **Setup counts vs. the week's tags.** Setup says six dinners; tagging three nights leaves four.
   Does the week's tagging win silently (my assumption), or should I say "that's four dinners this
   week rather than your usual six — shall I?"
2. **Un-approving.** Can the second adult un-approve a week after the list is built, and what
   happens to items already ticked off in the shop?
3. **Redo.** Currently returns to question 1 with answers intact. Should it instead regenerate from
   the same answers — "same inputs, a different week"?
4. **More onboarding questions.** Which of these earn a step: who cooks which nights, weekly budget,
   shopping day and store, cooking ambition, portion size? Each adds ~20 seconds.
5. **Household size for guest maths.** The guest steppers add to "2 adults, 2 children" from What We
   Know. If a household hasn't filled that in, should the guest panel ask for the whole table
   instead of the extras?
