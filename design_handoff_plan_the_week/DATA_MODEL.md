# Data model — get this right before writing screens

Everything else in this package is cheap to change later. This isn't: if the week's answers aren't
stored as a first-class object with provenance, three things become impossible to add afterwards —
Redo, "why did it plan that?", and any learning from what the household actually cooked.

## The shape

Three objects, in a chain: **intake → plan → slots**. Each knows the one before it.

\`\`\`
week_intake                        the answers
  id
  household_id
  week_start                date          Monday of the target week
  revision                  int           1, 2, 3… (see "Revisions")
  created_by                member_id
  created_at                timestamp
  superseded_at             timestamp     null while current

  night_tags                json          { "2026-09-02": ["rush"], "2026-09-05": ["out"] }
  guest_counts              json          { "2026-09-06": { "adults": 2, "children": 0 } }
  packed_lunch_days         json          ["2026-09-01", "2026-09-02"]
  moods                     json          ["Comfort food"]
  cuisines                  json          ["Thai"]
  freeform                  text          the "anything else" field, verbatim

  household_snapshot        json          { "adults": 2, "children": 2 }
  preferences_snapshot      json          the full preference set used (see below)
\`\`\`

\`\`\`
weekly_plans                       existing table, two new columns
  intake_id                 fk → week_intake.id
  approved_by               member_id     null until approved
  approved_at               timestamp     null until approved
\`\`\`

\`\`\`
meal_plan_entries                  existing table, three new columns
  open_reason               text          null if planned; the sentence if the slot is open
  reason                    text          the 4–9 word "why" shown under the meal name
  derived_from              json          which inputs produced this slot (see "Provenance")
\`\`\`

## Why each of the awkward bits

**Keyed by ISO date, not weekday.** \`night_tags\` uses \`"2026-09-02"\`, never \`"Tue"\`. Weekday keys
break the moment you plan two weeks, look at history, or cross a month boundary.

**\`guest_counts\` stores extras, and \`household_snapshot\` stores the base.** The UI collects "2 extra
adults"; portions need the total. Household composition changes (a child ages, someone moves in), so
the total has to be reconstructible from what was true *that week*, not from today's What We Know.

**\`preferences_snapshot\` is a copy, not a reference.** This is the one people skip and regret. If the
plan only points at live preferences, then the day someone edits "won't eat", every past plan's
reasoning becomes unreadable and unreproducible — you can no longer tell whether a strange choice
was a bug or a preference that has since changed. Snapshot at generation:

\`\`\`
{ "meal_counts": { "breakfasts": 1, "lunches": 3, "dinners": 6 },
  "wont_eat": ["olives", "blue cheese", "fennel"],
  "protein": { "chicken": 2, "fish": 1, "red_meat": "rarely" },
  "weeknight_max_minutes": 40,
  "repeats": "one_a_week",
  "kit": ["slow_cooker", "air_fryer"],
  "cuisines": ["thai", "indian", "italian", "mexican", "greek", "japanese"],
  "table_style": "everyone_same" }
\`\`\`

**\`freeform\` is stored verbatim and never parsed destructively.** Whatever the model extracts from it
goes in \`derived_from\` on the slots it affected. The household's own words survive.

## Revisions — how Redo works without losing anything

\`week_intake\` rows are **append-only**. Never update one in place.

- Redo, or any chat instruction that changes an *answer* ("cut it to four dinners", "actually
  Wednesday should be leftovers"), writes a **new revision** for the same \`(household, week_start)\`,
  copying the previous revision and applying the change. The old row gets \`superseded_at\`.
- The current intake for a week is the row with the highest \`revision\` and \`superseded_at IS NULL\`.
- The plan generated from each revision keeps its \`intake_id\`, so "the week you had before you
  redid it" is always recoverable.

**The trap this closes:** if chat edits only the plan and not the intake, then Redo regenerates from
stale answers and silently reverts everything the household just said in chat. Any chat instruction
that would have changed a Q1/Q2 answer must write a new revision. Instructions that only affect one
slot ("swap Thursday") change the plan, not the intake.

Rule of thumb: **would this answer have changed if they'd said it during the questions?** If yes,
new revision.

## Provenance — what makes "why did it plan that?" answerable

Nearly free if you record it at generation time, and impossible to backfill. Per slot:

\`\`\`
derived_from: {
  "tags":        ["rush"],                    which night tags applied
  "constraint":  "max_minutes:20",            the binding constraint, if any
  "inputs":      ["cuisines:thai", "mood:comfort_food"],
  "freeform":    "I want to use the lamb in the freezer",   quoted span, if it drove this
  "inventory":   ["lamb_shoulder"],           stock items this slot was chosen to use
  "links_to":    "entry_id:8842"              the earlier slot this one eats the leftovers of
}
\`\`\`

Three payoffs, in order of how soon you'll want them:

1. The per-slot "why" line in the UI is generated from this rather than improvised, so it can't
   contradict the actual reason.
2. When a plan is wrong you can see which input caused it — a bad tag, a stale preference, or the
   model.
3. Later: cross-reference against what was actually cooked. "Every \`rush\` night you didn't cook"
   is a finding you can only get if the tags are attached to the slots.

## Open slots

A slot is never absent. Either \`recipe_id\` is set, or \`open_reason\` is a full sentence naming the
constraint that caused it. Add a check constraint if the ORM allows it — a null-null slot is the
silent-empty-slot bug in stored form.

\`\`\`
open_reason: "Wednesday I'd rather ask than guess: after Monday's chili, everything I have under
              20 minutes repeats something you've just eaten."
\`\`\`

Exception: a \`nobody home\` dinner is \`planned_empty\` — a third state, not an open slot. It needs no
decision and must never be offered to the household as one.

## One intake in flight per week

Both adults get the Sunday nudge, so both can start the flow. Take a soft lock on
\`(household_id, week_start)\` when Q1 opens; if the other adult already has one, show them the
in-progress intake instead of a blank one — *"Marcus started this an hour ago — shall I carry on
from where he got to?"* Two intakes racing to generate the same week is the one concurrency case
that will actually happen, most likely on a Sunday evening.

## Approval

\`approved_by\` + \`approved_at\` on \`weekly_plans\` are what the receipt copy renders, and what makes
the grocery write idempotent: the list is built **only** on the transition into \`approved\`, and an
entry that has already contributed is skipped. Both guards are needed — one alone lets a
re-approval double a quantity.

## Retention

Keep superseded intake revisions and approved plans indefinitely at this scale; they're small and
they're the only record of what was asked and why. \`preferences_snapshot\` is the largest field and
it's a few hundred bytes.
