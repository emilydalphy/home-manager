# Scope

## Tickets this package covers

| Ticket | How it's covered |
| --- | --- |
| **Work through the chat prompts/experience for building the weekly plan** (High) | The whole package. Sequence moves from \`SYSTEM_PROMPT\` into two screens; pacing, motivation and acknowledgement designed explicitly |
| **Meal planning: revisitable preferences screen + fix chat onboarding gap** (Medium) | Setup screen with per-category steppers (0–7), all saved preferences inline-editable, embedded chat scoped to what steppers can't express |
| **Add onboarding question about typical week & upcoming plans** (Medium) | Onboarding step A — combined typical-week + next-week, both free text, both skippable |
| **Rewrite "add more context" prompts to explain why it helps** (Medium) | The copy rule in \`VOICE.md\` §3 plus the rewrite table in \`COPY.md\` |
| **Grocery list is built before the meal plan is confirmed** (High, in progress) | Provides the missing UI half: an Approve button, and the draft-screen promise that states what approval will do |

## Related tickets this touches but does not close

- **Week generation silently leaves random meal slots empty** — the draft screen's open-slot
  treatment is the UI answer, but the generator still needs to not silently drop slots.
- **Make chat responses faster** — the one-line headline rule (\`VOICE.md\` §4) cuts response length
  at the point it's most visible; latency itself is untouched.
- **Break broad cuisine options into specific ones** — Q2 reads cuisines from What We Know, so
  whatever that list becomes flows through automatically.
- **Redesign the post-onboarding "first sample week" screen** — the draft screen is the pattern to
  reuse there; not rebuilt in this package.

## Explicitly out of scope

- Shell/iframe navigation work (its own tickets).
- Push notifications for "plan ready" / "adult approved" — copy exists in the old
  \`NOTIFICATIONS.md\`, infrastructure doesn't exist yet.
- Prep-task generation, defrost steps, leftovers inventory category.
- Grocery-side work beyond the approval trigger.
