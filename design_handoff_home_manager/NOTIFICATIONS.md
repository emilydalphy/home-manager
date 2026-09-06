# Notifications — v1

Four, and only four. Each one exists because a screen in the design implies it, each is individually toggleable in settings, and each deep-links to the exact surface that resolves it. The bar for a fifth: it must be something the user would be annoyed to have missed, not something the app is proud of.

Voice matches the app: name the thing, then the consequence. No exclamation marks, no "Don't forget!", never more than two sentences. Notifications are written in the house voice, same as everything else.

---

## 1 · Dinner decision nudge

**Why:** Today's needs-you band says "Decide by 5pm". A band that only exists inside the app cannot hold a deadline.

- **Trigger:** a dinner within the next 48 hours has no meal, and the local time is 15:00 on the day before. One per gap, never repeated for the same gap.
- **Suppress if:** the gap is filled, or the user opened Today within the last 2 hours.
- **Copy:** title `Thursday still has no dinner` · body `It's tee-ball night — the 15-minute option is turkey rice bowls.`
- **Deep link:** Today, needs-you band scrolled into view.
- **Actions (where the platform supports them):** `Pick that one` (fills it, no app launch) · `Show options` (opens Today).

## 2 · Expiring soon

**Why:** the Kitchen tab badges items expiring within four days, and the whole point of inventory is not wasting food.

- **Trigger:** one or more stock items reach 2 days from their best-before date. Batched into a single notification per day, sent at 09:00 local.
- **Suppress if:** nothing is within 2 days, or the user visited Inventory in the last 24 hours.
- **Copy, one item:** title `Spinach goes off Thursday` · body `Two bags. Want it in a dinner this week?`
- **Copy, several:** title `Three things to use this week` · body `Spinach, chicken and cilantro are all near their date.`
- **Deep link:** Kitchen → Inventory, expiring items at the top.
- **Action:** `Plan a meal with it` → opens the ask bar pre-filled with `Use up the spinach this week`.

## 3 · Weekly plan ready

**Why:** the week is the product's rhythm; this is the one recurring, expected notification.

- **Trigger:** the assistant has generated next week's plan. Default Sunday 17:00 local, user-adjustable in settings (day + time).
- **Copy:** title `Next week's plan is ready` · body `Six dinners, one night left open on purpose. Two new recipes.`
- **Deep link:** This Week, day rail on Monday.
- **Action:** `Looks good` (acknowledges, clears any review badge).
- **Suppress if:** the household has fewer than two planned dinners next week — send nothing rather than an empty plan.

## 4 · The other adult changed something

**Why:** two adults share one list and one plan. Silent changes to a shared plan are how a household stops trusting the app.

- **Trigger:** the *other* adult changes a dinner, deletes a grocery item, or completes a shopping trip. Never fires for your own actions.
- **Coalesce** a burst of changes within 10 minutes into one notification. Cap at three per day; beyond that, hold until the next digest.
- **Copy, plan:** title `Marcus swapped Thursday's dinner` · body `Turkey rice bowls instead of salmon. Salmon moved to Saturday.`
- **Copy, shopping:** title `Marcus finished the Costco run` · body `Six items got, two left for Trader Joe's.`
- **Copy, removal:** title `Marcus took paper towels off the list` · body `He says you already have them.`
- **Deep link:** the changed surface — Week, Grocery, or the shopping summary.

---

## Rules across all four

- **Quiet hours** 21:00–07:00 local: hold and deliver at 07:00, except nothing holds overnight if it would arrive after its own deadline — drop it instead.
- **Never more than two notifications a day** from this app, total, across all four types. Priority when clashing: dinner nudge > expiring > other-adult > weekly plan.
- **No badge-only notifications.** If it isn't worth a sentence, it isn't worth a badge.
- Every notification maps to a resolvable action in the app. If tapping it lands on a screen where nothing can be done, it should not have been sent.
- **Permission ask:** not at first launch. Ask the first time the user leaves a dinner gap unresolved, framed in-context: `Want me to remind you before it's too late to shop?`
- **Settings:** four independent toggles, plus the weekly-plan day/time. Off by default for the other-adult notification if the second adult hasn't joined.

## Deliberately excluded from v1

- **Store geofence** ("you're near Costco") — the most-requested-sounding one and the easiest to get wrong; needs background location, and a mistimed fire while you're driving past is worse than useless. Revisit once shopping mode has real usage.
- Streaks, weekly stats, "you saved $N", recipe marketing, re-engagement pushes. The app has no reason to interrupt anyone to talk about itself.
