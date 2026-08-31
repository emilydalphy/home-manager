# Plan the Week — handover package

Everything needed to build the redesigned weekly-plan flow for Home Manager.

**Ticket:** "Home Manager: Work through the chat prompts/experience for building the weekly plan"
(High, Meal planning design). Also closes or advances four neighbours — see \`SCOPE.md\`.

## Read in this order

| File | What it is |
| --- | --- |
| \`SCOPE.md\` | Which tickets this covers, and what it deliberately doesn't |
| \`VOICE.md\` | The tone rules. Read before writing a single string |
| \`SPEC.md\` | The full design spec, screen by screen |
| \`COPY.md\` | Every user-facing string, ready to lift |
| \`DECISIONS.md\` | Six calls to make before coding, each with a recommendation |
| \`DATA_MODEL.md\` | The intake object in full — **build this right first** |
| \`DATA_AND_API.md\` | Endpoints, and what changes in \`agent.py\` / \`tools.py\` |
| \`BUILD_ORDER.md\` | Five stages, each independently shippable |
| \`AGENT_PROMPT.md\` | Paste-into-Claude-Code prompt |
| \`Plan the Week.dc.html\` | The clickable prototype (open in a browser; \`support.js\` sits beside it) |

## The one-paragraph version

Building the week stops being an open-ended chat and becomes a flow with real screens: a dismissible
Sunday nudge, **two** question screens, a full 21-slot draft, and an **Approve** button that is what
builds the shopping list. Chat stays as the escape hatch for anything the screens can't express.
Every question states what it buys the household before they answer; every answer is acknowledged
with the concrete change it caused. The assistant keeps recipe choice and free-text handling; it no
longer owns the sequence.

## Non-negotiables

1. **Nothing reaches the grocery list until the week is approved.** Approval is a button, not a
   sentence the assistant has to remember to offer.
2. **Every meal, every day gets planned** — 21 slots. The only deliberately empty slot is a dinner
   on a night nobody is home, and it says so.
3. **No question without a stated reason**, and no question where two answers produce the same
   behaviour.
4. **Every plan knows the answers it came from.** Intake revisions are append-only and each slot
   records what produced it — see \`DATA_MODEL.md\`.
5. **The app never re-asks what it already knows.** Cuisines come from What We Know; day rows show
   what's already on record.
