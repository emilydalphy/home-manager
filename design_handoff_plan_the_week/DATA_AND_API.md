# Data & API

Written against the current codebase: \`app/agent.py\`, \`app/tools.py\`, \`app/main.py\`,
\`static/onboarding.html\`, \`static/shell.js\`. Line references were accurate at 2026-08-31; confirm
before editing.

## New: week intake

The answers to Q1 and Q2 are a first-class object, not chat history. Without this, the flow's
guarantees can't be enforced and "Redo" can't reuse the answers.

> **Read \`DATA_MODEL.md\` before building any of this.** It specifies the full object, why each
> field exists, append-only revisions, per-slot provenance, and the concurrency lock. The summary
> below is the shape only.

\`\`\`
week_intake
  id
  household_id
  week_start                     date
  created_by                     member_id
  created_at
  night_tags                     json   { "2026-09-02": ["rush"], "2026-09-05": ["out"] }
  guest_counts                   json   { "2026-09-06": { "adults": 2, "children": 0 } }
  packed_lunch_days              json   ["2026-09-01", "2026-09-02"]
  moods                          json   ["Comfort food"]
  cuisines                       json   ["Thai"]
  freeform                       text
\`\`\`

Tag vocabulary — closed set, and each has exactly one planning consequence:

| Tag | Generator behaviour |
| --- | --- |
| \`normal\` | Ordinary cooked dinner. Mutually exclusive with the others |
| \`out\` | Dinner slot planned empty; nothing for it on the grocery list |
| \`rush\` | Hard cap: dinner ≤ 20 min, **or** previous night scaled to cover it |
| \`guests\` | Portions and quantities scale by \`guest_counts\`; recipe choice shifts if children > 0 |
| \`left\` | No new dinner; previous night's batch increased |

\`packed_lunch_days\` does not decide *whether* a lunch is planned — every lunch is planned. It
constrains those days to food that travels cold.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| \`GET\` | \`/api/week/{week_start}/intake\` | Prefill Q1/Q2 — known nights, saved cuisines, household size |
| \`POST\` | \`/api/week/{week_start}/intake\` | Save the intake; returns \`intake_id\` |
| \`POST\` | \`/api/week/{week_start}/generate\` | Generate a draft **from an \`intake_id\`**; adds nothing to groceries |
| \`POST\` | \`/api/week/{week_start}/approve\` | Approve → builds the grocery list. Returns \`{ groceries_added, already_have_skipped }\` |
| \`POST\` | \`/api/week/{week_start}/slot\` | Resolve one slot (open-night choice, or a swap) |
| \`GET\`/\`PATCH\` | \`/api/preferences/meal-planning\` | Setup screen read/write |

### Q1 prefill contract

\`GET .../intake\` must return, per day: any tags already known from stored preferences or calendar
("Tee-ball — you eat at 5"), and any observed pattern worth stating ("Takeout four weeks running").
The grey hint line on each day row is this data. If it's empty the row still works — it just reads
"Nothing on the calendar".

### Cuisines

Q2's cuisine chips are **read from What We Know**, not a constant. Source the household's saved
cuisine list; if empty, fall back to the onboarding list and write taps back to What We Know. This
closes the loop with the "break broad cuisine options into specific ones" ticket automatically.

### Household size

Guest steppers collect **extras**. Total = household members + extras, split adults/children.
Household composition comes from What We Know. If it's missing, ask for the whole table instead of
extras (open question 5 in \`SPEC.md\`).

## Changes to existing code

### \`app/tools.py\`

- \`plan_meal()\` — \`add_ingredients_to_grocery_list\` **defaults False**. (Already done on branch
  \`worktree-grocery-confirm-and-chat-timing\`; this flow depends on it.)
- \`approve_weekly_plan()\` — populates the grocery list from the plan's meals, idempotent on the
  transition into \`approved\`, returns the counts the receipt copy needs.
- \`generate_weekly_plan()\` — takes an \`intake_id\`; must fill **all 21 slots** or return a slot with
  an explicit \`open_reason\` string. A silently missing slot is a bug, not a plan.
- Per-category counts (\`breakfasts_per_week\` etc.) — allow **0–7**, not 1–7. "None, thanks" is a
  valid answer.
- New: \`get_week_intake\` / \`save_week_intake\`.

### \`app/agent.py\`

- Delete the onboarding interview at \`~L143-158\`. The wizard and the two question screens now own
  it; a conversational duplicate can only contradict them.
- Keep ongoing preference capture (\`~L159-181\`) — passing mentions still matter.
- Replace the weekly-plan generation rules with: *the flow owns the sequence; you own recipes, the
  per-slot reasons, and the open-slot explanation.*
- Add \`VOICE.md\` rules verbatim, plus the one-line-response rule.
- The assistant must never claim the grocery list is updated before approval.

### \`static/onboarding.html\`

Two new steps via the existing pattern — add the \`<div id="step-…">\`, register in \`ALL_STEPS\`, add
to \`questionSteps\` for the progress dots, wire Continue. Place both immediately before the
first-week reveal.

### Meals screen

Needs the Approve button, the approved receipt, and the link to the setup screen. Note the shell's
tabs are iframes — follow the existing navigation guard rather than a plain link.

## Two-adult behaviour

- Either adult can approve. The receipt names who and when.
- The other adult is notified on approval (copy exists; push infrastructure is a separate ticket —
  in-app is fine for now).
- Reopening a week after the list is built: see \`DECISIONS.md\` #2.
- Both adults are nudged, and the second to start joins the first's intake: \`DECISIONS.md\` #5 and
  \`DATA_MODEL.md\` → One intake in flight.
