# Decisions to make before coding

Six questions. Each has my recommendation — if you agree with all of them, say so and Claude Code
can treat this file as answered. Anything you leave, it will ask about rather than guess.

---

## 1. Setup says six dinners; the week's tags leave four

**Recommendation: the tags win, and the app says so once.** In the draft headline: *"That's four
dinners this week rather than your usual six — you're out Friday and it's leftovers Tuesday."* No
extra question, but no silent shortfall either.

Alternative: ask during Q1. Rejected because it turns a tagging screen into a negotiation, and the
answer is nearly always "yes, obviously".

---

## 2. Can the second adult un-approve a week after the list is built?

**Recommendation: yes, but it's called "Reopen the week", and it never removes anything from the
shopping list.** Reopening lets the plan be edited again; groceries already added stay, and
re-approving only adds what's new. Removing items someone may already have bought is worse than a
slightly long list.

The alternative — a true reversal — needs "was this item bought?" tracking that doesn't exist yet.

---

## 3. Does Redo re-ask the questions or regenerate from the same answers?

**Recommendation: two separate actions, because they're different needs.**
- **"Try again"** — regenerates from the current intake, no questions. For "right constraints, wrong
  food."
- **"Change my answers"** — back to Q1 with everything prefilled. For "I forgot we're out Thursday."

Both create a new intake revision (\`DATA_MODEL.md\` → Revisions). One button labelled "Redo" can
only be one of these, and it'll be the wrong one half the time.

---

## 4. What happens if the flow is run on a week that's already approved?

**Recommendation: allow it, but say what it means.** *"Sep 1–7 is already approved and on your
shopping list. If we replan it I'll add anything new — I won't take anything off."* Same rule as
reopening.

---

## 5. Both adults get the Sunday nudge?

**Recommendation: yes, both, and the second one to open it joins the first one's intake** rather
than starting a blank one (\`DATA_MODEL.md\` → One intake in flight). Nudging only one adult makes
planning one person's job, which is the thing the two-adult model exists to avoid.

---

## 6. When is the Sunday nudge, exactly?

**Recommendation: Sunday morning, 9am, once, dismissible for the week.** Late enough not to be the
first thing on a weekend morning, early enough that the shop can happen Sunday afternoon.

Worth knowing: this is in-app only for now. Real push is a separate ticket with no infrastructure
behind it yet, so if the app isn't opened on Sunday the nudge is simply seen on Monday.

---

## Not a decision, but flag it to whoever builds

Your current live week (week of 2026-08-24) is a draft with 59 grocery items already attached from
the old add-as-you-plan behaviour. Under the new code, approving it adds nothing and can't double
your list — it was verified against your actual database. No migration needed, but don't be
surprised by a draft that already has a full list.
