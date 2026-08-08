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

### Grocery list

Just say what you need — "add milk and eggs to the list" — and it's saved,
whether you mention one item or several at once. Items stay on the list
across the week (there's no expiration or reset) until you mark them
purchased, so the list itself is how it "remembers" — no need to re-add
anything already on it, and re-adding a duplicate is a no-op. Planning a
saved recipe still auto-adds its ingredients on top of anything you've
listed manually.

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
