# Build order

Five stages. Each is shippable on its own and leaves the app in a coherent state.

## 1 — Approve (half a day)

The smallest change with the largest payoff, and everything else assumes it.

- Approve button + approved receipt on the Meals screen.
- Wire to \`approve_weekly_plan()\` (the branch that populates groceries on approval).
- Draft state shows the grocery promise: *"I haven't put anything on your shopping list yet."*
- Assistant stops claiming the list is updated before approval.

**Done when:** a draft week can be approved from the Meals screen, the list builds exactly once, and
approving twice adds nothing.

## 2 — The two question screens (2–3 days)

- \`week_intake\` table exactly as specified in \`DATA_MODEL.md\` — append-only revisions,
  \`preferences_snapshot\`, \`household_snapshot\`, per-slot \`derived_from\`. Do this part first and
  don't trim it; the three things it enables can't be retrofitted.
- The four intake/generate endpoints.
- Q1: day rows with known-context hints, five tags, exclusive \`normal\`, guest steppers, packed-lunch
  pills, "I'll…" acknowledgements.
- Q2: moods, cuisines **from What We Know**, freeform share field.
- Generation reads the intake. All 21 slots filled or explicitly open.
- Entry points: Sunday nudge on Today (dismissible, once per week) and a permanent one on Meals.

**Done when:** a household can go nudge → Q1 → Q2 → draft → approve without typing in chat, and the
tags demonstrably change the plan.

## 3 — Draft screen (1–2 days)

- Day cards, three slots each, week-summary dot strip.
- Open-slot treatment: amber card, reason naming the constraint, three answers.
- Swap per slot; Redo; "Talk it through" opens chat scoped to this plan.

**Done when:** every slot is either planned or explicitly open with a reason, and the week is
readable end-to-end before approving.

## 4 — Setup screen (1–2 days)

- Steppers 0–7 per category (loosen the 1–7 floor in \`tools.py\`).
- All saved preferences inline-editable.
- Embedded chat routed to \`edit_preference\`.
- Entry from Meals and from the approved receipt.

**Done when:** every preference set during onboarding can be changed afterwards without chat, and
chat can still do it for anything the steppers can't express.

## 5 — Onboarding steps + copy sweep (1 day)

- Steps A and B before the first-week reveal.
- Kitchen kit and repeats tolerance feed generation.
- \`VOICE.md\` into \`SYSTEM_PROMPT\`; the \`COPY.md\` rewrite table across the app.
- Remove the conversational onboarding interview from \`agent.py\`.

**Done when:** a new household's first plan is built from real answers, and no screen invites input
without saying what it buys.

## Testing worth writing

- A draft adds nothing to groceries; approval adds once; second approval adds nothing.
- \`out\` night: dinner empty, no groceries for it.
- \`rush\` night: dinner ≤ 20 min, or previous night scaled.
- \`guests\` with children: quantities scale by the right total.
- Every generated week has 21 slots, each planned, \`planned_empty\`, or carrying an \`open_reason\` —
  never null-null.
- A chat instruction that changes an answer creates a new intake revision, and "Try again" after it
  respects the change rather than reverting it.
- Two adults starting the flow on the same week share one intake.
- Meal counts accept 0.
- Two devices: approval on one is reflected on the other.
