# Prompt for Claude Code

Paste this whole file. It assumes the handover package sits in the repo (or is pasted alongside).

---

Build the redesigned "Plan the Week" flow for Home Manager, following the handover package in
\`design_handoff_plan_the_week/\`. Read \`VOICE.md\` first, then \`SPEC.md\`, then \`DECISIONS.md\`,
\`DATA_MODEL.md\`, \`DATA_AND_API.md\` and \`BUILD_ORDER.md\`. Take strings verbatim from \`COPY.md\` — do not paraphrase them. The clickable
reference is \`Plan the Week.dc.html\` (open it in a browser; the seven steps are in the left rail).

Work in the five stages in \`BUILD_ORDER.md\`, committing after each. Stop and ask before starting a
stage if the previous one revealed something the spec didn't anticipate.

**The four non-negotiables:**

1. Nothing reaches the grocery list until the week is approved, and approval is a **button** on the
   Meals screen — not a sentence the assistant has to remember to offer.
2. Every meal, every day gets planned — 21 slots. The only deliberately empty slot is dinner on a
   night nobody is home, and it says so. A silently missing slot is a bug.
3. Every question carries a "Why I'm asking" line, and every answer that changes planning gets an
   "I'll…" acknowledgement naming the concrete change. Never "So I'll".
4. The app never re-asks what it knows. Q2's cuisines come from What We Know; Q1's day rows show
   what's already on record.

**Specific things that are easy to get wrong:**

- The guest follow-up is **two steppers** (extra adults, extra children), not one count plus a
  category toggle — portions need both numbers. Totals add to household size from What We Know.
- Packed-lunch days don't decide *whether* lunch is planned. Every lunch is planned; those days are
  constrained to food that travels cold.
- "Regular night" is mutually exclusive with the other four tags, and exists so a user can affirm a
  night rather than skip it. An affirmed night is data; a skipped night is a guess.
- Per-category meal counts must accept **0**, not 1 as the floor.
- Delete the conversational onboarding interview in \`app/agent.py\` (~L143-158). Two paths asking the
  same questions can only contradict each other.
- Curly apostrophes (\u2019) in all user-facing copy. No straight quotes.

**When you're done with each stage,** run the tests listed at the end of \`BUILD_ORDER.md\` for that
stage and report on each. For stage 1 specifically, verify that approving twice does not double the
grocery list.

**\`DECISIONS.md\` holds six product calls, each with a recommendation.** If it says the
recommendations are accepted, follow them. If any is still unanswered, ask before building the part
that depends on it — don't guess.

**Build \`DATA_MODEL.md\` faithfully and completely in stage 2, including the parts with no visible
UI:** append-only intake revisions, \`preferences_snapshot\`, \`household_snapshot\`, per-slot
\`derived_from\` provenance, and the soft lock on \`(household_id, week_start)\`. These enable Redo,
"why did it plan that?", and learning from what was actually cooked — none of which can be added
afterwards. Two specific traps it closes:

- A chat instruction that would have changed a Q1/Q2 answer must write a **new intake revision**, or
  regenerating silently reverts what the household just said in chat.
- A slot is never absent: it is planned, \`planned_empty\` (nobody home), or carries an
  \`open_reason\` sentence. Never null-null.
