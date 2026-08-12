# Home Manager (V1)

A Claude-powered household assistant for cleaning schedule, meal planning, and
grocery list. Chat-based, backed by SQLite, built on FastAPI + the Anthropic
Messages API with tool use.

## How it's built

- **`app/schema.sql`** — SQLite schema. Every table has a `household_id`
  column (hardcoded to `1` for now) so this can go multi-tenant later
  without a data model rewrite.
- **`app/tools.py`** — plain Python functions (add/list chores, plan meals,
  manage grocery list) that read/write the database. These are the real
  "product" — the AI is just a natural-language interface on top.
- **`app/agent.py`** — tool schemas + the Claude tool-use loop. Claude
  decides which tool(s) to call based on what you ask; the loop executes
  them and feeds results back until Claude has a final answer.
- **`app/main.py`** — FastAPI server exposing `/api/chat` and serving the
  chat UI.
- **`static/index.html`** — single-page chat UI, no build step.

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Set your Anthropic API key:
   ```
   export ANTHROPIC_API_KEY=sk-ant-...
   ```

3. (Optional) seed some starter chores/recipes/groceries:
   ```
   python seed.py
   ```

4. Run the server:
   ```
   uvicorn app.main:app --reload
   ```

5. Open http://localhost:8000. First-time visitors are sent to a dedicated
   onboarding wizard (`/onboarding`) instead of straight to chat — click-to-select
   screens for who's in the household (with general age group, not exact age),
   any pets, your goals for the app, then whichever modules you want to set up
   now (meal planning, chores, or both — anything skipped can be set up later
   in chat). Meal planning covers dietary restrictions per person, protein
   more/less, favorite cuisines, and cooking time. Chores asks a handful of
   profile questions (home type, bed/bath count, yard, cleanliness standard,
   who's in the rotation, existing help like a cleaning service) and then
   calls Claude once to generate a recommended starting chore list, shown as
   an editable checklist — uncheck anything, edit frequency or who's
   responsible, or add your own, before saving. Every chip group has a
   "type your own" option for anything not listed. After onboarding, you
   land in chat and can just talk normally:
   - "what chores are coming up this week?"
   - "add a chore: clean out the gutters, every 6 months, maintenance"
   - "mark the trash chore as done"
   - "add milk and eggs to the grocery list"
   - "plan chicken stir fry for dinner Thursday"

   You can rerun the wizard any time via "Edit household setup" at the top
   of the chat page.

### How chore scheduling works

Chores you add are templates (name, category, frequency, who's responsible).
`generate_chore_schedule` turns those into actual due-dated instances — if a
chore has more than one person assigned, it rotates between them each time,
picking up from whoever went last. The agent calls this automatically during
onboarding and whenever the upcoming list looks thin; you can also just ask
"generate this month's chores" any time.

### Meal planning

The first time you bring up meals, recipes, or groceries, it'll separately
ask about dietary restrictions (per person) and general food preferences
(favorite cuisines, quick-meal needs, things to avoid) before diving in —
tap "Set up meal planning" or just ask about dinner. After that, saved
recipes can carry tags (vegetarian, quick, kid-friendly, etc.) and the app
tracks how often each one gets planned, so it can favor familiar favorites
over suggesting something new every time. Planning a saved recipe still
auto-adds its ingredients to the grocery list.

The assistant also picks up on feedback about specific recipes as soon as you
mention it — "we loved that chicken dish," "that pasta was too bland," "make
that again sometime" — and saves a liked/disliked rating plus any freeform
notes against the recipe, without needing to be asked. Liked recipes get
surfaced first in future suggestions; disliked ones are avoided. Same goes for
general dislikes ("no peppers, please") — said once, remembered in every
conversation after, not just the current one.

### Weekly meal plans

Ask for a full week at once ("plan my week," "what should we eat this week") and
the assistant generates a whole week's dinners in a single pass — not a
back-and-forth chat negotiation — as a real, reviewable plan rather than
scattered one-off entries. It leans on saved favorites but always works in at
least one new recipe, and avoids repeating meals, proteins, or cuisines it's
already served in the last 3 weeks (an explicit request for a repeat, like
"let's do the tacos from last week," is always honored). To change just one
day without touching the rest of the week, ask for a swap ("swap Tuesday for
something with chicken") instead of regenerating everything.

### What the app knows

Ask "what do you know about our preferences?" (or similar) and the assistant
will summarize everything it's saved — dietary restrictions, favorite
proteins/cuisines, dislikes, cooking-time preference, and notes. You can
correct anything directly in conversation ("actually I like Thai food," "forget
that I said no peppers") and it's saved immediately, the same way new
preferences are.

There's also a dedicated **What We Know** page (linked from the top of the
chat page) if you'd rather review and edit things directly instead of asking
in chat — add/remove dislikes and favorite cuisines as chips, add/remove
protein preferences, and edit cooking-time preference, notes, and household
goals, all with immediate save, no conversation required.

### Grocery list

Just say what you need — "add milk and eggs to the list" — and it's saved,
whether you mention one item or several at once. Items stay on the list
across the week (there's no expiration or reset) until you mark them
purchased, so the list itself is how it "remembers." Planning a saved recipe
still auto-adds its ingredients on top of anything you've listed manually.

The list is organized into store sections (produce, dairy, meat/seafood,
pantry, frozen, other) so it reads like something you can actually shop from,
aisle by aisle, rather than a flat dump. Adding the same item twice doesn't
create duplicate lines — quantities are consolidated automatically when the
units match (e.g. "2 cups flour" + "1 cup flour" becomes "3 cups flour"); if
the units genuinely don't reconcile (like cups vs. pounds), both amounts are
kept together on one line rather than the app silently guessing a conversion.

If you're getting something elsewhere instead of on the regular trip — a butcher, a
farmers market — say so and it's excluded from the shown list without being deleted: it
stays tracked (so a future ingredient add for the same item still consolidates into that
line instead of duplicating it), it's just hidden from what you're about to shop from.
Say the word and it's back on the list.

### Plan flexibility & feedback nuance

One-off constraints for a specific week ("3 nights this week," "under 30 minutes on
weeknights," "one vegetarian night") apply only to that week's plan — they don't become
a standing preference. Recipes can be temporarily flagged out of rotation ("let's not do
the stir fry for a while") without permanently disliking them, distinct from an actual
"we don't like this" rating. A single bad (or good) experience with a recipe can be
logged as a one-off note without flipping its permanent rating — only a real pattern of
feedback should exclude a recipe from suggestions going forward. A "Recipe variety"
setting (mostly favorites / balanced / surprise me often, editable from the What We Know
page) controls how much new-recipe exposure shows up in generated plans, with a floor —
even "mostly favorites" still includes at least one new recipe a week.

Households can also switch how planning works entirely: **day-based** (the default — one
meal per day) or **component-based** (a pool of items by category — a breakfast for the
week, a few proteins, a few vegetables, carbs, a treat, a dip — for the household to mix
and match freely instead of a fixed day-to-day schedule). This is a standing household
setting, not a per-week choice, and switching doesn't affect any plan already generated.

### Cooker (turning an approved plan into dinner actually happening)

Recipes now carry full detail beyond just a shopping list — ordered instructions, default
servings, prep/cook time, and any advance-prep notes ("marinate overnight," "soak beans the
day before"). Ask for a recipe's full detail ("how do I make the chicken skewers?") or to
scale it for a different number of people ("make that for 6") and quantities adjust
automatically wherever they're parseable (anything freeform, like "a pinch," is left as-is
and flagged rather than guessed).

After a plan is generated, the assistant can build a prep schedule — working backward from
each recipe's advance-prep notes to flag things like "marinate the chicken tonight" on the
right day ahead of when it's needed. Ask "what's left to cook" or "what's my prep schedule"
any time for a done-vs-outstanding view. Mention that a meal or prep step is done ("we ate
the tacos," "chicken's marinating") and it's checked off immediately, not just acknowledged
in the chat.

The prep schedule also looks across the whole week for shared prep, not just meal by meal —
if the same component turns up more than once (a rice side used twice, a marinade base
shared by two dishes), it's consolidated into one batch-prep task covering every meal it
serves ("cook a big batch of rice — enough for Tuesday's stir fry and Thursday's fried
rice") instead of a near-duplicate task per meal.

Checking a meal off as cooked also tries to deplete its ingredients from tracked inventory.
A confident match (the ingredient name lines up with something tracked) depletes silently —
no interruption. Anything less certain — an ambiguous match ("garlic" against a tracked
"garlic bulb" — probably the same thing, but not safe to assume) or a quantity that
genuinely can't be reconciled (the recipe says "1 cup," inventory just has "a bag") — is
never silently guessed at or dropped; it lands in the **needs your attention** list instead
(a banner on the Cooker view, and the assistant will work it into chat naturally rather
than an interrogation). This same list also covers the existing "how'd that turn out?"
nudge for a recently-cooked, unrated meal — one place for anything worth a second look.

If you changed something while cooking — swapped an ingredient, adjusted a step — mention it
("used thighs instead of breast this time") and it's logged as a one-off deviation, feeding
into the same soft-signal memory as recipe feedback, without touching the recipe's permanent
rating.

### Cooker view

A dedicated page (linked at the top of chat, next to What We Know) built for whoever's
actually standing in the kitchen: this week's meals with full ingredients and step-by-step
instructions expandable per meal, the generated prep schedule, and tap-to-check-off progress
for both — no need to go back and forth with chat mid-cook. Asking for a recipe in chat
always returns the full breakdown too: if a recipe predates having instructions saved (or
was added quickly without them), the assistant works out a reasonable step-by-step from its
own knowledge on the spot and saves it, rather than telling you nothing's there.

A banner at the top surfaces anything in the **needs your attention** list — an
inventory-depletion match worth double-checking, or a recently-cooked meal that hasn't been
rated — with a quick mark-handled/not-relevant action right there, no need to go through
chat for it.

### Household coordination & trust

Before a plan is approved, the assistant checks it against any saved dietary restrictions/
allergies and flags likely clashes ("heads up, the noodle dish has peanut butter and Jamie
has a peanut allergy") — a warning to weigh, not a hard block, since it's a simple keyword
match and can be a false positive. Ask "why did you suggest this?" or "why haven't we had
X in a while?" for the actual reasoning (rating, notes, history) behind a meal, rather than
a guess.

If you've cooked something and haven't said whether you liked it, the assistant may bring
it up once, naturally, the next time you're chatting — not a push notification, just a
low-key "how'd the salmon turn out?" the next time you open it. This is one of the checks
folded into the needs-your-attention list described under Cooker above.

Ad hoc items ("also grab batteries") work exactly like any other grocery add — no need for
them to trace back to a recipe. If the household shops at more than one store, say so once
("we get bulk stuff at Costco") and future adds of that item are pre-sorted there
automatically; ask for the list split by store instead of by section once more than one
store is in play.

Ask "what have you learned about us?" for an aggregate picture — how many recipes are
tracked, how many are liked/disliked, how many deviations have been logged — distinct from
the raw preference dump on the What We Know page.

### Expiration & use-it-up

Inventory items get an estimated expiration automatically whenever an exact date isn't given.
The estimate first checks a table of common item-level shelf lives (eggs, milk, feta, ground
beef, etc. — around 60 items, adapted from general USDA/FDA freshness guidance, not a live
lookup) and falls back to a rough per-category default (produce, dairy, meat/seafood, pantry,
frozen) only for items it doesn't recognize. Mention or scan an actual date and that always
takes precedence over any estimate. The
Inventory view surfaces a banner for anything already gone bad or coming up soon. When
generating a weekly plan, the assistant actively looks for a chance to work in at least one
recipe that uses up something near-expiring — a real goal, not just a tiebreaker, though it
won't force a bad fit just to use up an odd ingredient.

Beyond that stronger near-expiring nudge, the assistant also gives a softer general lean toward
meats/seafood, produce, and dairy already on hand even when nothing's urgent yet — favoring what's
already in the fridge over defaulting to a fresh purchase, both when generating a full weekly plan
and when suggesting a one-off meal in chat. This is a light preference, not a rule: it won't force
an odd combination or expect every item on hand to get used, and it never overrides genuine
variety, dietary restrictions, or preference.

### Inventory view

A dedicated page (linked at the top of chat) for browsing what's on hand without going
through chat — grouped into the same store sections as the grocery list (produce, dairy,
meat/seafood, pantry, frozen, other), with inline quantity editing and removal, plus a quick
add-item form for anything you'd rather type directly than mention conversationally. Adding
still happens either way — through chat ("used the last of the spinach") or here — and both
stay in sync automatically.

### Photo-based inventory capture

Two "Scan instead of typing" buttons at the top of the Inventory view, each uploading a photo
to Claude's own multimodal vision (no separate OCR/vision service) to extract a draft item list:

- **Scan a receipt** — extracts food/grocery line items with their quantity and category,
  skipping tax/subtotal/payment/loyalty-point lines automatically.
- **Scan fridge/pantry** — identifies visible food items on a shelf for an initial stock-take or
  re-sync. This is the harder recognition problem (mixed, stacked, partially obscured items), so
  quantity is often left blank rather than guessed.

Neither scan ever saves directly — both return a draft list shown as an editable review (uncheck
anything wrong, fix the name/quantity/category) before anything is written to inventory, and
anything the model was genuinely unsure about is flagged "double-check" rather than presented
with false confidence. Accuracy — especially for fridge/pantry photos — hasn't yet been
validated against real photos; treat early results with appropriate skepticism until that's been
tested live.

The fridge/pantry scan is actually two separate buttons — **Scan fridge** and **Scan pantry** —
so each detected item is automatically tagged with the right storage location (see below) without
extra manual work; anything visibly in a freezer compartment is tagged 'freezer' regardless of
which button was used.

### Storage location (fridge/freezer/pantry) & duplicate detection

Every inventory item has a storage location — fridge, freezer, or pantry — separate from its
grocery category. These often diverge: a bottle of BBQ sauce is category='pantry' by food type,
but once opened it usually lives in the fridge. Location defaults to a sensible guess from
category (produce/dairy/meat go to the fridge, frozen to the freezer, everything else to the
pantry) but can be set explicitly — in chat ("it's in the fridge now that it's open"), from the
Inventory view's per-item location dropdown, or automatically from which photo-scan button was
used.

The Inventory view has a toggle at the top — **by store section** (the original grouping,
matching the grocery list) or **by fridge/pantry** — so you can see everything actually in the
fridge or the pantry specifically, not just grouped by food type.

Because the same item name can now be tracked in more than one location at once (an opened BBQ
sauce in the fridge, a separate unopened one in the pantry), a banner surfaces whenever that
happens — flagging it so an opened, nearly-empty item doesn't get overlooked while an unopened
one sits untouched, and so a re-buy doesn't happen when one's already on hand somewhere else. The
assistant checks for this proactively in chat too, the same way it checks for near-expiring items.

### Pantry & fridge inventory

Separate from the grocery list — this tracks what you actually *have*, not what you
still need. It's chat-only, on purpose: mention it the same way you'd mention a
preference ("picked up a rotisserie chicken," "used the last of the spinach," "I've
got about 2 lbs of ground beef left") and it's saved immediately, no manual-entry
screen. Checking an item off the grocery list also adds it to inventory
automatically. When generating a weekly plan, ingredients already tracked in
inventory are skipped when auto-adding to the grocery list instead of piling on
top of what you already have.

Recipes and planned meals can also carry `food_groups` (protein/carb/vegetable
coverage) — when something's missing, the assistant may mention it once as an
optional idea ("want a veggie side with that?"), but never blocks or requires
it. Spaghetti and meatballs with no salad is a perfectly fine dinner; the
point is to make balance easy when you want it, not to enforce it.

The database file (`app/home_manager.db`) is created automatically on first
run — delete it any time to start fresh.

## Installing it as an app (PWA)

The frontend is a Progressive Web App — once it's running somewhere reachable
(locally or hosted), you can install it like a real app:

- **iPhone/iPad (Safari):** open the URL, tap Share → "Add to Home Screen."
- **Android (Chrome):** open the URL, tap the ⋮ menu → "Install app" (or you'll
  see an automatic install banner).
- **Desktop (Chrome/Edge):** open the URL, click the install icon (⊕) in the
  address bar, or menu → "Install Home Manager."

It'll open in its own window/full-screen with its own icon, no browser
chrome. Note: installing from `localhost` only works on the same machine —
to install it on your phone you need it hosted somewhere reachable over the
internet (see below), or reachable on your home wifi.

## Hosting it so you (and later, others) can reach it from anywhere

Right now `localhost:8000` only works while your laptop is running the
server. To install this on your phone or let anyone else use it, it needs to
run somewhere always-on. Railway is the easiest option to start with (Render
and Fly.io work similarly):

1. Push this folder to a GitHub repo.
2. Go to railway.app, sign in, "New Project" → "Deploy from GitHub repo" →
   select the repo. Railway will detect the `Dockerfile` automatically.
3. In the project's Variables tab, add `ANTHROPIC_API_KEY` with your key.
4. **Important — persistent storage:** by default, container filesystems on
   these platforms are wiped on every redeploy, which would erase your
   SQLite database. In Railway, add a Volume (Settings → Volumes), mount it
   at `/data`, and add an env var `DB_PATH=/data/home_manager.db` so the
   database lives on the persistent disk instead of the ephemeral container
   filesystem.
5. Once deployed, Railway gives you a public URL
   (`something.up.railway.app`). Open that on your phone and install it as
   described above.
6. Add one more env var: `PUBLIC_BASE_URL=https://something.up.railway.app`
   (the exact URL from step 5, no trailing slash). Without this, any link the
   assistant hands back in chat — like an Eater's self-service link — has no
   way to know its own domain and can't construct a working URL.

This still has no login/accounts — treat the URL as private (don't share it
publicly) until the auth work described below is done.

## What's deliberately simple in V1

- Single household, single user, no auth.
- Meal plan → grocery list is one-directional (planning a saved recipe adds
  its ingredients). No dedupe/merging of quantities yet.
- Chat history is stored in memory per server process — restarting the
  server clears conversation context (the data itself persists in SQLite).
- Not built yet: meal planning onboarding (dietary preferences, favorite
  meals), shared scheduling for appointments/errands/date nights, and pantry
  /fridge inventory tracking for food waste. These are the next phases.

## Path to a sellable product

The things that will actually need to change to go multi-tenant:
- Replace `HOUSEHOLD_ID = 1` in `app/tools.py` with a real household id
  derived from auth/session.
- Add a users/auth table and login flow.
- Move chat session storage from in-memory dict to a real store (Redis/DB).
- Swap SQLite for Postgres once concurrent households need to write at once.

Because the schema and tools were built household-scoped from day one, none
of this requires touching the data model — it's a routing/auth layer on top
of what already exists.

## Path to the App Store / Play Store

The PWA install (above) is the fast path to "downloaded on my phone" and
works today. For real app store distribution later, wrap this same frontend
with [Capacitor](https://capacitorjs.com/) — it packages an existing web app
into a native iOS/Android app without a UI rewrite. That's a later step, once
there's hosting + auth in place and it's worth the App Store review process.
