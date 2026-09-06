# Copy deck — every user-facing string

Voice: warm, plain, slightly houseproud. The app speaks like a competent person who lives there — never like software. Sentence case everywhere except kickers and micro-labels (uppercase with letter-spacing). No exclamation marks. No emoji. Contractions are fine and preferred.

Rules for anything new: name the thing that changed, then the consequence ("Locked Thursday to 15-min turkey rice bowls and moved the salmon to Saturday"). Never "Success!", never "Oops". Numbers are digits. Times are "6:30", durations "35 minutes" in prose and "35 min" in tight rows.

---

## Global

| Element | String |
| --- | --- |
| Ask bar placeholder | `Ask or add anything…` |
| Ask sheet input placeholder | `Tell me what changed…` |
| Tabs | `Today` · `Week` · `Grocery` · `Kitchen` |
| Back affordance (Kitchen sub-screens) | `‹ Kitchen` |
| Household name (menu framing) | `The Dalphy House` |
| Household established line | `Est. 2019` |

## Today

| Element | String |
| --- | --- |
| Date kicker | `Tuesday, Aug 25` |
| Heading — nothing open | `You're clear` |
| Heading — one open | `1 thing needs you` |
| Heading — several | `{n} things need you` |
| Dinner-gap kicker | `Decide by 5pm · tee-ball night` |
| Dinner-gap title | `Thursday has no dinner` |
| Suggestion A | `Turkey rice bowls · 15 min` |
| Suggestion B | `Leftover tikka bowls · 5 min` |
| Suggestion action | `Pick` |
| Store-run kicker | `Before Thursday` |
| Store-run title | `Costco run · 4 items` |
| Store-run body | `Chicken, sweet potatoes, towels, oil` |
| Store-run actions | `Shop now` · `Later` |
| Tonight kicker | `Tonight · 6:30 · 35 min` |
| Tonight dish | `Weeknight Chicken Tikka with Basmati Rice` |
| Tonight actions | `Cook mode` · `Swap` |
| Chores title | `Your chores` |
| Chores counter | `{done} of {total}` |
| Chores | `Take out trash` · `Laundry` · `Vacuum living room` |
| Grocery row title | `Grocery run` |
| Grocery row sub | `{n} items · needed before Thursday` / `All picked up` |
| Grocery row action | `Open` |

**Toasts:** `Thursday set — turkey rice bowls, 15 min` · `Thursday set — leftovers, nothing to buy` · `I'll remind you Wednesday evening`

## This Week

| Element | String |
| --- | --- |
| Framing line | `The Dalphy House · week of August 24` |
| Heading | `This week` |
| Day rail | `MON` `TUE` `WED` `THU` `FRI` `SAT` `SUN` |
| Day stamp | `TUESDAY, AUG 25` (uppercase, full day + date) |
| Card title — today | `Today's Table` |
| Card title — past | `Already served` |
| Card title — other | `The table` |
| Course badges | `Breakfast` · `Lunch` · `Dinner ★` (filled) / `Dinner` (empty) |
| Dinner meta | `on the table at 6:30 · 35 minutes` |
| Empty dinner | `Nothing yet — tee-ball night` |
| Empty dinner meta | `pick one and I'll sort the shopping` |
| Card actions | `Cook this` · `Swap it` |
| Week row title | `The whole week` |
| Week row sub | `One gap left · Thursday dinner` / `All seven days planned · 21 meals` |
| Ask row prompt | `Tell me what's happening this week and I'll rebuild the plan.` |
| Ask row action | `Ask` |

**Week sheet:** title `The whole week`, range `Aug 24–30`, column heads `Breakfast` `Lunch` `Dinner`, empty dinner cell `Open · tee-ball`, footer `Share` and `Back to {Day}` (note the space before the day name).

**Meals used throughout** (keep these consistent — they appear on Today, the week card, the week grid, and the grocery reasons):

| Day | Breakfast | Lunch | Dinner |
| --- | --- | --- | --- |
| Mon | Oatmeal & berries | Turkey wraps · packed | Sheet-pan sausage & peppers |
| Tue | Yogurt bowls with berries | Leftover sausage & peppers | Chicken tikka with basmati rice |
| Wed | Scrambled eggs & toast | Tikka bowls · packed | Sheet-pan fajitas |
| Thu | Overnight oats | Ham & cheese rolls | *open* → Turkey rice bowls |
| Fri | Pancakes · Sam's turn | Pasta salad | Salmon & sweet potato |
| Sat | Big breakfast · bacon & eggs | Grilled cheese & tomato soup | Pizza night |
| Sun | Cinnamon rolls | Costco run · hot dogs | Leftovers night |

When Thursday is filled from a suggestion, salmon moves to Saturday and pizza to Friday — the copy that explains it: `Thursday's handled — salmon moved to Saturday.`

## Grocery — phone

Header kicker `2 stores`, heading `Grocery`, counter `{got} of {total} got`. Store metas: `before Thursday` (Costco), `on the way home` (Trader Joe's). Item reasons: `Thu dinner` · `Fri dinner` · `ran out` · `low` · `tikka` · `added`.

## Grocery — desktop

| Element | String |
| --- | --- |
| Header meta | `{got} of {total} got · {n} unassigned` |
| Heading | `Grocery` |
| Add field | `Add an item…` / button `Add` |
| Rail sections | `STORES` · `SHOW` |
| Rail filters | `Everything` · `Costco` · `Trader Joe's` · `Unassigned` · `Already got` |
| Rail note | `Hiding {n} items you've already got.` / `Showing everything, including what's already in the cart.` |
| Unassigned title | `Unassigned` |
| Unassigned helper | `Pick a store and it moves into that list` |
| Row legend | `Tick Got it as it goes in the cart. Already have it takes it off the list entirely.` |
| Row controls | `Got it` · `Already have it` |
| Store action | `Shop this store` |
| Aisles | `Produce` `Bakery` `Dairy` `Meat` `Frozen` `Pantry` `Household` |
| Empty state | `Nothing left on this filter` / `Switch to Everything on the left, or turn on “Already got”.` |
| Trip panel | `This trip` · `AISLE ORDER · {Store}` · `COVERS THESE MEALS` · `SHARED LIST` |
| Trip note (Costco) | `Costco has to happen before Thursday's tee-ball. Trader Joe's is on Emily's commute.` |
| Trip note (TJ's) | `Trader Joe's is on the commute home — five things, one stop.` |
| Meals covered | `Tue tikka · Thu turkey bowls · Fri salmon` |
| Shared note | `Emily and Marcus both add here. Initials show who asked for what.` |
| CTA | `Start shopping {Store}` |

**Toasts:** `{Item} → {Store}` · `{Item} added — pick a store for it` · `{Item} off the list — I'll assume you have it` · `Trip saved — I'll remember what you bought where`

## Shopping mode

Kicker `Shopping · {Store}`; heading `{Aisle} first` or `Everything's in the cart`; counter `{got} / {total}`; `Done shopping`; aisle head `{AISLE} · {n} items`; per-aisle `{n} left`; footer `{Store} next · {n} items` / `Last stop of the trip`; button `Next store` / `Back to list`.

## Kitchen

| Element | String |
| --- | --- |
| Kicker | `Recipes · inventory · what we know` |
| Heading | `Kitchen` |
| Tonight kicker | `Cooking tonight` |
| Tonight dish | `Chicken Tikka with Basmati Rice` |
| Tonight actions | `Start step 1` · `Ingredients` |
| Inventory row | `Inventory` / `{n} to use soon · {n} running low` / `Open` |
| Memory row | `What we know about you` / `People · Taste · Rhythm · Stores` / `Open` |
| Nudge card | `Worth doing sometime` / `Scan a fridge photo so I stop suggesting what you already have.` |

## Inventory

Heading `Inventory`; summary `{n} to use soon · {n} running low`; groups `Fridge` `Freezer` `Pantry`; group count `{n} items`; flags `use soon` · `low`.

**Detail sheet:** kicker `{Location} · added this month`; `How much is left`; `Where it lives`; `Fridge` `Freezer` `Pantry`; `Best before`; relative note `today` / `tomorrow` / `in {n} days` / `in {n} weeks` / `in {n} months` / `{n} days past`; actions `Add to grocery list` → `On the list ✓`, and `Used it up`.

**Toasts:** `Added {item} to the list` · `{Item} marked used up`

**Seed stock:** Fridge — Whole milk 3 gal, Plain yogurt 4 cups, Baby spinach 1 bag, Chicken breast 2 lb, Cilantro 1 bunch. Freezer — Salmon fillets 4, Peas 2 bags, Leftover chili 2 cups. Pantry — Basmati rice 1 half bag, Olive oil 1 inch left, Canned tomatoes 4 cans, Tortillas 6 left.

## What we know

Heading `What we know`; tabs `People` `Taste` `Rhythm` `Stores`; row action `Edit`; editor `Save` · `Cancel` · `Delete`; add row `+ Add something to this list`; editor placeholder `What should I know?`

**Intros**
- People — `Who I'm planning for, and what nobody will eat.`
- Taste — `What tends to work, so suggestions land the first time.`
- Rhythm — `The shape of your week — I plan around these.`
- Stores — `Where things come from and what a week usually costs.`

**Footers**
- People — `Tap any line to correct it. I'll re-check this week's plan against changes.`
- Taste — `The more of these you fix, the fewer swaps you'll have to make.`
- Rhythm — `Tell me about a one-off week in the ask bar instead — these are the standing patterns.`
- Stores — `I use these to decide which list an item lands on.`

**Seed facts**

*People:* `Four at the table — two adults, Sam (7), Maya (4)` · `No pork, ever` · `Sam won't eat peppers, but eats them puréed in sauce` · `Maya is dairy-light — cheese is fine, milk isn't`

*Taste:* `Weeknight dinners under 40 minutes` · `Anything with lime lands well` · `Fish only if it's salmon` · `Grill on weekends when the weather allows`

*Rhythm:* `Thursdays are tee-ball — dinner on the table by 5:30` · `Friday breakfast is pancakes, Sam's turn to help` · `Sunday night is leftovers, no cooking` · `School-day breakfast is self-serve`

*Stores:* `Costco every other Saturday morning` · `Trader Joe's is on the commute home` · `Basmati rice and paper towels are Costco items` · `About $220 a week for groceries`

**Toasts:** `Saved — I'll plan around that` · `Removed`

### Stores tab — what you get where

Title `What you get where`; helper `Tap an item to move it to the other store, or × to forget it.`; store metas `every other Saturday · bulk` and `on the commute · fresh`; count `{n} usual items`; add chip `+ Add`; add placeholder `e.g. Rotisserie chicken`; add button `Add`.

Import row: `Import a whole list` / `Paste your notes app, or photograph a receipt` / `Open`.

**Import sheet:** kicker `WHAT YOU USUALLY BUY`; title `Import a list`; `These items belong to`; textarea placeholder `Paste or type one per line — rotisserie chicken, frozen berries, coffee beans…`; `Photograph a receipt instead` / `Camera`; `Cancel` · `Save to {Store}`.

**Toasts:** `I'll put {item} on the {Store} list` · `Added {n} items to {Store}` · `Open your camera roll and I'll read the receipt`

**Seed usual items** — Costco: Basmati rice, Paper towels, Chicken breast, Olive oil, Frozen salmon, Sweet potatoes. Trader Joe's: Milk, Eggs, Yogurt, Cilantro, Limes, Tortillas.

## Assistant replies (shape, not script)

| Trigger | Reply | Card kicker / body / destination |
| --- | --- | --- |
| Busy Thursday | `Locked Thursday to 15-min turkey rice bowls and moved the salmon to Saturday.` | `Week updated` / `Thu · Turkey rice bowls` / Week |
| Add an item | `Added tortillas to the Trader Joe's list — you're already going before Thursday.` | `Grocery updated` / `Trader Joe's · 4 items` / Grocery |
| Swap tonight | `Tonight's tikka is 35 minutes and everything's in the fridge. Sheet-pan fajitas would be the easy swap — want it?` | `Ready when you are` / `Swap tonight → fajitas` / Week |
| Anything else | `Got it — I've saved that to what I know about your household and I'll plan around it.` | `Saved` / `Kitchen · What we know` / Kitchen |

Suggestion chips: `Sam has tee-ball Thursday` · `Add tortillas` · `Swap tonight`
