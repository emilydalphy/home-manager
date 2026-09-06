# Data and API

Shapes below are the minimum each screen needs. Field names are suggestions; match the codebase's conventions. Everything the UI derives (counts, badges, groupings, progress, relative dates) is listed as derived — don't store it.

## Entities

```
Household        id, name ("The Dalphy House"), establishedYear, timezone
Person           id, householdId, name, kind: adult|child, age?, initial, color, isAccount
Fact             id, householdId, category: people|taste|rhythm|stores, text,
                 hard: bool (allergies = true, preferences = false), authorId, updatedAt
Store            id, householdId, name, habit ("every other Saturday"),
                 role ("bulk" | "fresh"), aisleOrder: string[]
UsualItem        id, storeId, name            // "what you get where" chips
Meal             id, householdId, date, slot: breakfast|lunch|dinner,
                 title, minutes?, servedAt?, source: assistant|user, status: planned|open|served
GroceryItem      id, householdId, name, storeId | null, aisle, reason,
                 status: needed|got|removed, authorId, mealIds: string[], createdAt
StockItem        id, householdId, name, location: fridge|freezer|pantry,
                 qty: number, unit: string, bestBefore: date | null, updatedAt
Trip             id, householdId, storeId, startedAt, finishedAt, itemIds: string[]
Message          id, householdId, role: user|assistant, text,
                 card?: { kicker, body, destination }, createdAt
```

Notes
- `GroceryItem.storeId = null` **is** the triage state. There is no separate "unassigned" collection.
- `Meal.status = open` is a first-class state, not a null title — the needs-you band, the day rail's dashed pill, and the week grid's "Open · tee-ball" all read it.
- `StockItem.bestBefore` is a real date. The prototype's `days` integer is a demo shortcut; the UI's "in 3 days" wording is computed at render.
- `Fact.hard` gates the assistant: never propose a meal that violates a hard fact.

## Per-screen reads and writes

| Screen | Reads | Writes |
| --- | --- | --- |
| Today | open `Meal`s (next 48h), `GroceryItem` counts per store, chores, tonight's `Meal` | fill a `Meal` (pick suggestion), snooze a store run, toggle chore |
| This Week | `Meal`s for the week (21 rows), household name | select day (client only), fill/swap a `Meal` |
| Week sheet | same week `Meal`s | none — selection only |
| Grocery (phone) | `GroceryItem` where status ≠ removed, grouped by store | toggle `status` needed↔got |
| Grocery (desktop) | same + `aisle`, `authorId`, `Store.aisleOrder` | assign `storeId`, toggle got, `status: removed` ("Already have it"), create item |
| Shopping mode | `GroceryItem` for one store | toggle got; on "Done shopping" close a `Trip` and promote checked items into `StockItem`s |
| Inventory | `StockItem`s grouped by location | qty ±1, change location, shift `bestBefore` ±1 day, delete ("Used it up"), create `GroceryItem` ("Add to grocery list") |
| What we know | `Fact`s by category | update/create/delete `Fact` |
| Stores tab | `UsualItem`s per store | move between stores, delete, create, bulk create (import) |
| Ask sheet | `Message` history | append user message; assistant returns message + card + a patch to whatever it changed |

## Derived, never stored

Today badge (count of open decisions) · Kitchen badge (stock within 4 days of `bestBefore`) · "N of N got" · per-store and per-aisle counts · aisle groupings · shopping progress % · relative expiry wording · "N to use soon · N running low" · week-gap summary · trip aisle order.

## Assistant contract

One endpoint, one shape. The client sends the message plus enough context to plan; the server returns prose **and** a structured patch, so the UI can apply the change and show a card that links to it.

```
POST /assistant/message
  { householdId, text, context: { date, tab } }
→ { reply: string,
    card?: { kicker, body, destination: "today"|"week"|"grocery"|"kitchen" },
    patch: { meals?: Meal[], groceryItems?: GroceryItem[], facts?: Fact[], stock?: StockItem[] } }
```

Rules
- The patch is authoritative; the client applies it optimistically and reconciles.
- Every patch that changes anything must come with a card naming what changed. A reply with no card and no patch is a conversation, which is fine — a patch with no card is not.
- Cascades belong on the server: filling Thursday moved salmon to Saturday and pizza to Friday. The client must never compute a re-plan.
- Hard facts (allergies) are enforced server-side, not by prompt alone.

## Sync between the two adults

- Live updates on the shared household (websocket/subscription). Both adults have equal permissions in v1.
- **Last write wins per field**, with one exception: `GroceryItem.status` is a set operation — got stays got until someone explicitly un-gets it.
- Every mutation records `authorId`; the desktop row avatars and "added by Marcus" reasons read it.
- A remote change while the user is mid-edit (fact textarea open, stepper being tapped) must not clobber the local buffer — apply on blur/save.
- Removals are soft (`status: removed`) so "Already have it" can be undone and so the assistant can learn from what gets removed repeatedly.

## Offline

Not designed, but the interaction model assumes it: every action is optimistic and there are no spinners anywhere in the design. Queue mutations locally, reconcile on reconnect, and if a conflict can't be resolved silently, surface it as a toast in the app's voice rather than a dialog.
