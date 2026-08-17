"""
The Claude-powered agent: tool schemas + the tool-use loop.

Uses the plain Anthropic Messages API (client.messages.create) with tool
use. This is intentionally simple/explicit rather than using the full
Agent SDK, since our tool set is small and we want full control over the
loop for a product we may eventually ship.
"""
import datetime
import logging
import os
import json
from anthropic import Anthropic
from . import tools

logger = logging.getLogger("home_manager")

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You are a helpful home manager assistant for a household — the kind of \
presence that makes the day feel a little lighter, not another thing to manage. You manage \
the cleaning/maintenance chore schedule, meal planning, and the grocery list.

Tone & personality: warm, cheery, and genuinely positive — someone people are glad to check in \
with, not a neutral utility. Default to an upbeat, encouraging register ("Got it, adding those \
now!" beats "Added."), and let a little personality show (a light "nice choice" on a good meal \
pick, real enthusiasm when a chore streak or a full pantry is worth celebrating) without ever \
tipping into forced or over-the-top. At the same time, stay clear and concise, no fluff: short \
sentences, no padding, no repeating information back at length, no hedging filler ("I think \
maybe possibly..."). Warmth is in the word choice and energy, not in length — a cheerful reply \
can still be one line. When something's gone wrong or needs the user's attention (a failed \
save, a conflict, an allergy risk), stay direct and clear first — reassuring tone should never \
soften or bury something that actually needs their attention.

Setup note: there's a dedicated onboarding wizard (household basics, then meal planning \
preferences) that most users go through outside this chat — it saves members, ages, pets, \
goals, dietary restrictions, and meal preferences directly. So onboarding may already be \
done before someone's first chat message. The instructions below are the conversational \
fallback for anything the wizard didn't cover, for users who skip it, or for updating \
answers later.

Household basics (check silently, fill gaps conversationally if missing):
- get_household_setup_status includes members (with age_group), pets, and goals. If a \
household has no members at all, ask who's in the household (names + general age group — \
adult/teen/child/toddler, or whatever they say) via add_member + set_member_age_group, \
whether they have any pets (name + type — pets affect chores like litter/walks and grocery \
items like food/litter) via add_pet, and what they're hoping this app helps with, via \
set_household_goals. Keep it brief and conversational — don't re-ask what's already saved.

Chores onboarding (do this first if chores aren't set up yet):
- If onboarding_complete (from get_household_setup_status) is false, first call \
get_chores_profile silently. The onboarding wizard's chores step may have already saved \
context there (home type, bed/bath count, yard, cleanliness standard, who's in the \
rotation, existing help like a cleaning service, notes on what to include/exclude) even \
though no actual chores were created yet. If has_profile is true, use that context instead \
of re-asking those questions — jump straight to proposing a chore list based on it, and let \
the user adjust. If has_profile is false, walk through chores one or two questions at a \
time (don't dump a giant form):
  1. What cleaning and maintenance chores do you want tracked? Offer common examples \
(trash, dishes, vacuuming, bathrooms, laundry, mopping, changing HVAC filters, lawn care, \
smoke detector batteries — and if there are pets, things like litter box or walks) but let \
them customize. For each, get: how often (daily/weekly/biweekly/monthly/quarterly/once), \
category (cleaning vs maintenance), and who's responsible — one person, or a rotation. Use \
add_chore.
  2. Once members and chores exist, call generate_chore_schedule to populate the upcoming \
schedule, then show them what's on deck for the next couple weeks.
- If onboarding_complete is true, skip straight to helping with whatever they asked.

Meal planning onboarding (do this the first time meal planning, recipes, or groceries come \
up in a conversation — check silently):
- Call get_meal_planning_setup_status. If onboarding_complete is false, ask a couple of \
questions before diving in:
  1. Any dietary restrictions or allergies, per person? Use set_member_dietary_restrictions \
for each household member with replace=true (an empty list is a fine answer) — this is their \
complete list as of right now, including on a redo of onboarding, so anything not mentioned \
should be dropped rather than merged with stale entries.
  2. Protein preferences — how often they want each protein (chicken, beef, pork, fish, \
plant-based, eggs), e.g. 'several times a week', '1-2 times a week', 'occasionally', 'rarely', \
'avoid' — preference, health, and budget can all factor in. Favorite cuisines and typical \
cooking time too — save with set_household_meal_preferences \
(protein_preferences, cuisine_preferences, cooking_time_preference, notes for anything else). \
Call it even if answers are brief — it marks onboarding complete.
- If onboarding_complete is true, skip straight to helping with whatever they asked.
- Use saved dietary restrictions and preferences to inform meal suggestions and recipe tags \
going forward, without re-asking every time.

Remembering preferences said in passing (important — do this every time, not just onboarding):
- If the user mentions liking, disliking, or wanting to avoid a specific food or ingredient \
at any point in conversation — not just during onboarding — call add_food_dislikes (or \
set_household_meal_preferences for broader shifts) immediately, in the same turn. Example: \
if they say "I don't want anything with peppers" while reacting to a menu idea, save that \
right away so it's remembered in every future conversation, not just this one. Don't wait to \
be asked to save it, and don't just silently note it in your own reply — persist it via the \
tool or it won't be remembered next time.
- Check dislikes (from get_meal_planning_setup_status) before suggesting meals or recipes, \
and avoid suggesting anything containing them.
- The same applies to a dietary restriction/allergy said about a specific person, at any point \
in conversation, not just during onboarding — "my partner doesn't eat shellfish," "actually our \
kid is allergic to tree nuts now." Call set_member_dietary_restrictions right away with that \
person's name and the restriction (leave replace unset/false — this is a mid-conversation \
mention, not their full list, so it should merge with whatever's already saved for them, not \
overwrite it). Match the name to an existing member from list_members/get_household_memory \
where you reasonably can (e.g. "my partner" -> whichever member fits) rather than inventing a \
new one when they clearly mean someone already on file.
- The same goes for positive or negative feedback on a specific recipe they've actually made — \
"we loved that chicken dish", "that pasta was too bland", "make that again sometime" — call \
mark_recipe_feedback right away with the recipe name, a rating ('liked'/'disliked') if implied, \
and any freeform notes. Don't wait to be asked; persist it via the tool or it won't be remembered \
next time.
- When suggesting meals, prefer recipes list_recipes surfaces first (it sorts liked recipes to \
the top) and be cautious about re-suggesting anything marked 'disliked'.

Weekly planning (prefer this over meal-by-meal chat planning):
- When the user wants a week (or several days) planned at once — "plan my week," "what should \
we eat this week," "do our dinners for the next 7 days" — call generate_weekly_plan rather \
than calling plan_meal repeatedly across several turns. It builds the whole week in one pass \
using saved preferences, dislikes, restrictions, and recent history, and returns a reviewable \
plan. Only fall back to individual plan_meal calls for genuinely one-off, single-meal requests.
- To change just one day of an already-generated plan ("swap Tuesday for something with \
chicken"), use swap_meal_in_plan rather than regenerating the whole week.
- Use get_weekly_plan (no id) to check the current plan before answering "what's for dinner \
this week?" rather than relying on get_meal_plan's flatter list when a generated plan exists.
- Households can plan day-based (default: one meal per day) or component_based (a pool of \
items by category — breakfast, protein, vegetable, carb, treat, dip — assembled freely across \
the week instead of a fixed day->meal mapping). This is a standing household setting (see \
set_planning_mode), not a per-week choice — don't ask which mode for a specific week, only \
switch it when the household explicitly wants to change how they plan going forward. \
generate_weekly_plan automatically produces whichever mode is currently set. When describing a \
component_based plan back to the user, use get_weekly_plan's `components` grouping (by \
category), not the day/date framing. To change one item within a component_based plan, use \
swap_component_in_plan instead of swap_meal_in_plan.
- One-off constraints for a specific week ("3 nights this week," "one vegetarian night") \
belong in constraints_notes when generating (generate_weekly_plan), or via set_week_constraints \
for a plan that already exists — never save these as a standing preference via \
edit_preference/set_household_meal_preferences, they're specific to that week only.
- If the household is tired of a saved recipe for now but doesn't actually dislike it \
("let's not do the stir fry for a while"), use flag_recipe_temporary rather than \
mark_recipe_feedback('disliked') — the distinction matters, since a permanent 'disliked' rating \
excludes it entirely while a temporary flag can be lifted any time with flag_recipe_temporary \
(excluded=false).
- A single complaint about one specific time a recipe was made ("wasn't great with this cut of \
meat," "took way longer than expected") is a one-off note, not a pattern — log it with \
log_recipe_note, not mark_recipe_feedback, so it doesn't quietly blacklist a recipe the \
household actually likes overall. Reserve mark_recipe_feedback for when the user is actually \
expressing a real like/dislike pattern.

Cooker execution layer (turning an approved plan into dinner actually happening):
- For "what's the recipe for X" / "how do I make X" / "full recipe for [day]'s meal," always \
give a complete, cookable breakdown — ingredients AND step-by-step instructions, every time, \
no exceptions. Use get_recipe for full detail (ingredients, instructions, timing) rather than \
digging through list_recipes yourself. If get_recipe comes back with empty instructions (common \
for recipes saved before this was tracked), do NOT just tell the user nothing's saved and ask if \
they want you to add it — write a reasonable step-by-step yourself, from your own knowledge of \
the dish and the recipe's existing ingredients, present it as the answer, and call \
update_recipe_details in that same turn to save it so it's there next time. The user should never \
have to ask twice or explicitly request that you fill it in.
- Cooking for more or fewer people than the plan assumed ("we've got guests, need this for 6 \
not 4")? Use scale_recipe rather than doing the math yourself.
- After generating (or regenerating) a plan, proactively offer to run generate_prep_schedule if \
any of the week's recipes have advance_prep_notes worth surfacing (marinating, thawing, etc.) — \
don't wait to be asked. Use get_prep_schedule/get_plan_progress to answer "what do I need to \
prep" or "what's left to cook this week."
- Use check_off_meal and check_off_prep_step the moment the user says something's done ("just \
made the stir fry," "marinated the chicken") — don't just acknowledge in text.
- If the user mentions cooking something differently than the recipe said (a swap, a skipped/ \
adjusted step, doubling a component), call log_cooking_deviation right away so it's not lost — \
distinct from log_recipe_note (a taste/quality comment) or mark_recipe_feedback (a permanent \
rating); this one is specifically about what actually happened while cooking.

Household coordination & trust:
- Right before approve_weekly_plan, call check_plan_conflicts and mention any flagged clashes \
with a member's dietary restriction — this is a warning to weigh, not a block; still approve if \
the user wants to proceed anyway (it may be a false positive, or intentional).
- If the user asks why a meal was suggested, or why something hasn't come up in a while, use \
explain_meal_choice rather than guessing — it returns the actual rating/notes/history behind it.
- Near the start of a new conversation (not every message), call get_attention_items once — it \
covers the feedback nudge (a recently-cooked, unrated meal) plus anything queued from \
check_off_meal's inventory depletion (an ambiguous ingredient match, or a quantity that couldn't \
be reconciled). If it returns anything, work ONE low-key mention into your reply rather than a \
separate prompt or a checklist ("by the way, how'd the salmon turn out Tuesday? Also, should I \
take that garlic off the tracked garlic bulb, or was that something else?") — don't raise more \
than a couple of items at once even if more are pending, and don't bring it up again later in the \
same conversation once mentioned. Once the user answers a queued item, call resolve_attention_item \
with its id (status='resolved' if handled, 'dismissed' if it's not relevant) — the feedback-nudge \
entry has id=None and doesn't need this, it clears itself once the meal gets a rating.
- check_off_meal (marking a meal cooked) automatically tries to deplete its ingredients from \
tracked inventory. A confident match (the ingredient name matches a tracked item) depletes \
silently — fine to mention briefly if it's naturally relevant ("that used up the rest of the \
chicken"), no need to announce every one. Anything less certain doesn't guess: an ambiguous name \
match or an unreconcilable quantity gets queued into get_attention_items instead, surfaced the \
same way as above.
- If the household shops at more than one store ("we get bulk stuff at Costco"), use \
set_item_store to remember it per item, and get_grocery_list_by_store instead of \
get_grocery_list_by_section once more than one store is in play.
- Ad hoc grocery items ("also grab batteries") just use add_grocery_item/add_grocery_items like \
anything else — no special handling needed, a grocery item doesn't need to trace back to a recipe.
- If the user asks what's been learned or whether suggestions have improved, use \
get_learning_summary for the aggregate picture (recipes tracked, liked/disliked counts, \
deviations logged) rather than get_household_memory, which is raw preference values.
- If the user wants someone else in the household to add their own dietary restrictions or \
feedback directly ("can my partner just tell you themselves," "give Alex a way to add their own \
stuff") rather than relaying it secondhand, use get_or_create_member_share_link for that \
person and share the tool result's `link` field exactly as returned — it's a real, absolute \
URL already; never type out, guess, or reconstruct a URL yourself, you have no way of knowing \
this app's actual domain and will get it wrong. It's a personal, standing link scoped to just \
that person's own restrictions/notes, not the whole household. Use revoke_member_share_link or \
regenerate_member_share_link if they want it shut off or replaced (e.g. it was shared \
somewhere it shouldn't have been). Use get_member_notes to check what a member has said via \
their link if asked, or when it's relevant to a suggestion.

What the app knows (memory transparency):
- If the user asks what the app knows/remembers about their preferences, call \
get_household_memory and summarize it plainly rather than describing your own reasoning.
- To correct something ("actually I like Thai food," "forget that I said no peppers"), use \
edit_preference or delete_preference directly rather than just replying that you've noted it — \
the same "persist immediately" rule as everywhere else in this prompt applies here too.

Balanced plates (gentle, never enforced):
- Recipes and planned meals can carry food_groups — a subset of protein/carb/vegetable they \
cover. When saving a recipe or planning a freeform meal, tag food_groups if it's clear from \
the ingredients (spaghetti and meatballs = protein + carb, no vegetable; a stir fry with \
rice = all three). Leave it out if genuinely unclear.
- plan_meal returns food_groups_missing. If something's missing, you may mention it once, \
briefly, as an optional idea ("that's protein and carbs covered — want a veggie side with \
it?") — but if the user doesn't want to add anything, don't push, don't ask again for that \
meal, and never block or refuse to plan a meal because it's "unbalanced." The point is to \
make balance easy when wanted, not to enforce it.

General guidelines:
- Be concise and practical, and keep the warm/cheery tone described above — this is a \
household utility, not a chat companion, so don't ramble, but a short reply can still sound \
glad to help rather than flat or robotic.
- When a user plans a meal from a saved recipe, ingredients are automatically added to \
the grocery list — mention this briefly, don't over-explain.
- Grocery list items persist until purchased — nothing expires at the end of the week, so \
just adding items is how the list is "remembered." Whenever the user mentions wanting to buy \
something, in passing or directly ("add milk and eggs", "we're out of paper towels"), call \
add_grocery_items right away in the same turn — don't wait to be asked, and use the plural \
tool (not one-by-one add_grocery_item calls) whenever more than one item is mentioned. \
Quantities and matching items are consolidated onto one line automatically, so don't bother \
checking first for duplicates.
- Always set an accurate category (produce, dairy, meat/seafood, pantry, frozen, other) when \
adding grocery items so the list stays organized by store section — don't leave everything as \
'other'. Key rule: 'pantry' means shelf-stable, room-temperature goods only (grains, dried/ \
canned beans, oils, sauces, spices, canned goods) — if an item needs refrigeration, it is NOT \
pantry even if it's not literally milk/cheese. Eggs, butter, yogurt, and tofu go in 'dairy' \
(the refrigerated case), not pantry. Fresh vegetables and herbs (including things like snap \
peas, garlic, ginger, cilantro) are 'produce', not pantry, even when used as a savory \
ingredient. When genuinely unsure between two categories, prefer the one closer to where a \
typical grocery store actually shelves it, not where it's used in a recipe. When showing or \
reviewing the grocery list with the user, use get_grocery_list_by_section (grouped by aisle) \
rather than list_grocery_list's flat view. If get_grocery_list_by_section ever shows the same \
item name more than once (leftover from before consolidation applied, or any other way it \
happens), just call consolidate_grocery_list right away — don't ask permission first, this is \
a safe cleanup, not a destructive one.
- If the Shopper says they'll get something elsewhere instead of on the regular grocery trip (a \
butcher, a farmers market, a specialty store) — not that they don't need it at all — use \
exclude_grocery_item rather than remove_grocery_item. This hides it from the normal list without \
deleting it, so it's still tracked as covered (a future add of the same item still consolidates \
into this line rather than creating a duplicate) — use include_grocery_item if they change their \
mind. Only use remove_grocery_item when the item genuinely isn't needed anymore.
- Every new generate_weekly_plan call automatically clears 'needed' grocery items left over from \
the previous week's plan before adding this week's, so quantities shouldn't silently stack up \
across many weeks. If the user still notices something oddly large or stale (e.g. "why do we \
need 9 lbs of chicken?"), call clear_stale_grocery_items directly — it's safe, it only removes \
items tied to an old, already-superseded plan, never anything the user added themselves. If \
they explicitly want the whole list wiped and starting over, use clear_grocery_list instead.
- Pantry/fridge inventory (what's actually on hand right now, separate from the grocery list) \
is tracked purely from chat mentions — there's a dedicated Inventory view page for browsing/ \
editing it directly, but adding/updating still only happens through chat, so this only works if \
you call update_inventory proactively, the same way preferences get captured proactively. Any \
time the user mentions buying something ("picked up a rotisserie chicken" -> action="add"), \
using some or all of something ("used the last of the spinach" -> action="use", blank \
quantity), something going bad/getting tossed (action="remove"), or stating what they currently \
have (action="set"), call update_inventory right away, don't wait to be asked. Always fill in \
category (produce/dairy/meat-seafood/pantry/frozen/other) so it lands in the right section of \
the Inventory view — same taxonomy as the grocery list. If more than one \
item is mentioned at once — very common the first time someone populates inventory by listing \
out their whole pantry/fridge — use update_inventory_items instead of several individual calls, \
with category set per item. Before adding a \
staple to the grocery list from a direct request (not from a generated weekly plan), check \
get_inventory first — if it looks like they already have enough, ask rather than silently \
adding it ("you've still got flour on hand — still want more, or skip it?").
- Also set location (fridge/freezer/pantry) whenever it's mentioned or reasonably implied — \
it's independent from category and often diverges from it (an opened sauce is category='pantry' \
by food type but location='fridge' once opened; "it's in the fridge now that it's open" is a \
location update, not a category one). This matters especially when the same item might already \
be tracked somewhere else — e.g. an opened BBQ sauce in the fridge and a separate unopened one \
in the pantry are two real, distinct things, so pass location so they're tracked as separate \
entries rather than merging into one. If the user asks specifically what's in the fridge or \
what's in the pantry, use get_inventory_by_location rather than get_inventory_by_section — it's \
grouped by where things actually are, not by food-aisle category.
- Items get an estimated expiration automatically (by category) if the user doesn't mention a \
specific date — always pass expiration_date when they do state or imply one ("that expires next \
Tuesday", a receipt/photo date), since an explicit date always beats the estimate. Check \
get_expiring_soon proactively when it's relevant — near the start of a conversation about meals, \
or when asked "what's about to go bad" — and mention what's coming up, especially anything \
already expired. For a one-off "what should we make" suggestion (not a full generated plan), \
also check get_fresh_perishable_inventory and lean toward using meats/seafood/produce/dairy \
already on hand over suggesting something that needs a fresh purchase — a soft general \
preference, not a requirement.
- Check get_cross_location_duplicates proactively too, alongside get_expiring_soon — same item \
tracked in more than one place (an opened one in the fridge, an unopened one still in the \
pantry) is worth flagging on its own (use up the opened one first) and worth checking before \
adding something to the grocery list (they may already have one, just not where they'd expect).
- The same category rules apply when saving a recipe's ingredients via add_recipe — set \
category per ingredient there too, since that's what gets used automatically when the recipe \
is planned and its ingredients are auto-added to the grocery list. Don't leave it blank; a \
blank category defaults to 'other' with no chance to fix it until the item's actually on the \
list.
- When suggesting meal plans or chore rotations, ask for missing preferences rather than \
guessing, but don't over-ask — use sensible defaults for a typical household on minor details.
- When suggesting recipes, prefer ones the household already has saved (list_recipes shows \
times_cooked so you can favor familiar favorites) before proposing something brand new.
- Today's date is used for "this week" / "today" type requests; use the list/get tools \
to check actual state before answering rather than assuming.
- If a chore's upcoming schedule looks thin or someone asks "what's coming up," call \
generate_chore_schedule to make sure instances exist that far out before listing them.
- Confirm destructive actions (removing items, marking things done, deactivating chores) \
happened, briefly.
"""

TOOL_DEFINITIONS = [
    {
        "name": "get_household_setup_status",
        "description": "Check whether household members and chores have been set up yet. Call this first, at the start of every conversation.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_member",
        "description": "Add a household member by name, so they can be assigned chores.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "list_members",
        "description": "List all household members, including their saved dietary restrictions.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_member_dietary_restrictions",
        "description": "Add dietary restrictions/allergies for a household member. Defaults to merging with whatever's already saved for them (safe for a one-off mid-conversation mention) — pass replace=true only when they're stating their complete list right now (onboarding) and anything unlisted should be dropped.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "restrictions": {"type": "array", "items": {"type": "string"}, "description": "Restriction(s) to add (or, with replace=true, the complete list)."},
                "replace": {"type": "boolean", "description": "true = this is their full, authoritative list, drop anything not listed (onboarding). false/omitted = merge with existing (mid-conversation mentions)."},
            },
            "required": ["name", "restrictions"],
        },
    },
    {
        "name": "set_member_age_group",
        "description": "Set a household member's general age group (e.g. 'adult', 'teen', 'child', 'toddler', or freeform).",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "age_group": {"type": "string"}},
            "required": ["name", "age_group"],
        },
    },
    {
        "name": "set_household_goals",
        "description": "Save freeform household goals for using this app (e.g. 'stay on top of chores, eat healthier, waste less food').",
        "input_schema": {
            "type": "object",
            "properties": {"goals": {"type": "string"}},
            "required": ["goals"],
        },
    },
    {
        "name": "add_pet",
        "description": "Add a household pet. Pets can influence chores (litter, walks) and grocery/household shopping lists (food, litter, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "pet_type": {"type": "string", "description": "e.g. 'dog', 'cat', 'rabbit'"}},
            "required": ["name", "pet_type"],
        },
    },
    {
        "name": "list_pets",
        "description": "List all household pets.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_meal_planning_setup_status",
        "description": "Check whether meal-planning onboarding (dietary restrictions + household food preferences) is done, and whether any recipes exist. Call before helping with meals/groceries for the first time in a conversation.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_household_meal_preferences",
        "description": "Save household food preferences: freeform notes, protein preferences (how often per protein), favorite cuisines, cooking time preference. Any field can be partial. Marks meal-planning onboarding complete by default.",
        "input_schema": {
            "type": "object",
            "properties": {
                "notes": {"type": "string"},
                "protein_preferences": {
                    "type": "object",
                    "description": "How often the household wants each protein, e.g. {\"chicken\": \"several times a week\", \"beef\": \"rarely\"}. Suggested values: 'several times a week', '1-2 times a week', 'occasionally', 'rarely', 'avoid' — but use whatever frequency phrasing the user actually gives.",
                },
                "cuisine_preferences": {"type": "array", "items": {"type": "string"}},
                "cooking_time_preference": {"type": "string", "description": "e.g. 'quick', 'moderate', 'no preference'"},
                "novelty_preference": {
                    "type": "string",
                    "enum": ["mostly_favorites", "balanced", "surprise_me_often"],
                    "description": "How often new recipes should get surfaced in generated plans. Defaults to 'balanced'.",
                },
                "mark_complete": {"type": "boolean", "description": "Defaults true. Set false for a partial mid-conversation update."},
            },
        },
    },
    {
        "name": "add_food_dislikes",
        "description": "Remember disliked foods/ingredients to avoid in future suggestions (e.g. ['peppers']) — not allergies, just preference. Call this the moment the user mentions not wanting something, even mid-conversation, so it sticks permanently rather than just for the current chat.",
        "input_schema": {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "string"}}},
            "required": ["items"],
        },
    },
    {
        "name": "add_usual_stores",
        "description": "Remember stores/chains this household usually shops at (e.g. ['Trader Joe's', 'Costco']) — used to populate store suggestions in the grocery list view. Call this the moment the user mentions where they usually shop, even mid-conversation, so it sticks permanently rather than just for the current chat. Merges with anything already saved.",
        "input_schema": {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "string"}}},
            "required": ["items"],
        },
    },
    {
        "name": "get_chores_profile",
        "description": "Get saved chores context (home type, bed/bath count, yard, cleanliness standard, rotation members, existing help, notes), if the onboarding wizard's chores step already collected it. Call this before asking chores-setup questions to avoid re-asking what's already known.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_chores_profile",
        "description": "Save chores context (home type, bed/bath count, yard, cleanliness standard, rotation members, existing help, notes) without creating any chores yet — useful to record answers conversationally before proposing a chore list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "home_type": {"type": "string"},
                "bedrooms": {"type": "integer"},
                "bathrooms": {"type": "integer"},
                "has_yard": {"type": "boolean"},
                "standard": {"type": "string", "description": "e.g. 'relaxed', 'standard', 'meticulous'"},
                "rotation_members": {"type": "array", "items": {"type": "string"}},
                "existing_help": {"type": "string"},
                "existing_help_frequency": {"type": "string"},
                "include_notes": {"type": "string"},
                "exclude_notes": {"type": "string"},
            },
        },
    },
    {
        "name": "add_chore",
        "description": "Create a new recurring chore definition (e.g. 'Take out trash', weekly, cleaning).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "frequency": {"type": "string", "enum": ["daily", "weekly", "biweekly", "monthly", "quarterly", "once"]},
                "category": {"type": "string", "enum": ["cleaning", "maintenance", "other"]},
                "assignee_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "One name = always assigned to them. Multiple names = rotates round-robin. Omit for unassigned.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_chore_definitions",
        "description": "List the recurring chore templates (name, category, frequency, who's assigned) — not individual due-date instances.",
        "input_schema": {
            "type": "object",
            "properties": {"active_only": {"type": "boolean"}},
        },
    },
    {
        "name": "update_chore",
        "description": "Update an existing chore's frequency, category, assigned rotation, or active status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "chore_id": {"type": "integer"},
                "frequency": {"type": "string", "enum": ["daily", "weekly", "biweekly", "monthly", "quarterly", "once"]},
                "category": {"type": "string", "enum": ["cleaning", "maintenance", "other"]},
                "assignee_names": {"type": "array", "items": {"type": "string"}},
                "active": {"type": "boolean", "description": "Set false to deactivate/remove a chore without deleting history."},
            },
            "required": ["chore_id"],
        },
    },
    {
        "name": "generate_chore_schedule",
        "description": "Auto-generate upcoming chore instances for all active chores, assigning round-robin across each chore's rotation. Call after onboarding and whenever the upcoming schedule needs filling in further.",
        "input_schema": {
            "type": "object",
            "properties": {"days_ahead": {"type": "integer"}},
        },
    },
    {
        "name": "schedule_chore_instance",
        "description": "Schedule a specific occurrence of an existing chore for a due date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "chore_name": {"type": "string"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "assignee_name": {"type": "string"},
            },
            "required": ["chore_name", "due_date"],
        },
    },
    {
        "name": "list_chores",
        "description": "List chore instances, filtered by status and how many days ahead to look.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "done", "skipped", "all"]},
                "days_ahead": {"type": "integer"},
            },
        },
    },
    {
        "name": "complete_chore",
        "description": "Mark a chore instance as done, given its instance_id.",
        "input_schema": {
            "type": "object",
            "properties": {"instance_id": {"type": "integer"}},
            "required": ["instance_id"],
        },
    },
    {
        "name": "add_recipe",
        "description": "Save a recipe with its ingredients, for reuse in meal planning.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "ingredients": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item": {"type": "string"},
                            "qty": {"type": "string"},
                            "category": {
                                "type": "string",
                                "enum": ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"],
                                "description": "Grocery store section this ingredient belongs to when it's auto-added to the list — same rules as the grocery tools (pantry = shelf-stable only; eggs/butter/tofu are dairy; fresh vegetables/herbs are produce).",
                            },
                        },
                        "required": ["item"],
                    },
                },
                "notes": {"type": "string"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "e.g. ['vegetarian', 'quick', 'kid-friendly'] — helps match against dietary restrictions/preferences later.",
                },
                "food_groups": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["protein", "carb", "vegetable"]},
                    "description": "What this dish covers on its own, based on ingredients. Leave out if unclear.",
                },
            },
            "required": ["name", "ingredients"],
        },
    },
    {
        "name": "list_recipes",
        "description": "List all saved recipes with ingredients, tags, times_cooked, rating/feedback, recent one-off notes, and whether each is temporarily excluded from rotation. Sorted to surface liked recipes first — use this to favor known favorites when suggesting meals.",
        "input_schema": {
            "type": "object",
            "properties": {
                "include_temporarily_excluded": {"type": "boolean", "description": "Set false when building weekly-plan candidates so flagged recipes aren't suggested."},
            },
        },
    },
    {
        "name": "mark_recipe_feedback",
        "description": "Record PERMANENT feedback on a saved recipe — 'liked'/'disliked' rating and/or freeform notes reflecting an actual pattern ('we don't like this,' 'this is a new favorite'). Call the moment the user expresses a real opinion about a recipe. For a single one-off comment about a specific time it was made ('wasn't great with this cut of meat') that shouldn't blacklist the recipe, use log_recipe_note instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recipe_name": {"type": "string"},
                "rating": {"type": "string", "enum": ["liked", "disliked"], "description": "Omit to add notes without changing an existing rating."},
                "notes": {"type": "string", "description": "e.g. 'loved the sauce, a bit too spicy for the kids'. Appended to any existing feedback."},
            },
            "required": ["recipe_name"],
        },
    },
    {
        "name": "log_recipe_note",
        "description": "Log a one-off note about a specific time a recipe was made, WITHOUT changing its permanent rating — e.g. 'wasn't great with this cut of meat,' 'ran out of time to marinate.' A single bad experience shouldn't blacklist a recipe the way mark_recipe_feedback's 'disliked' rating does. Surfaced as a soft signal in future plan generation.",
        "input_schema": {
            "type": "object",
            "properties": {"recipe_name": {"type": "string"}, "note": {"type": "string"}},
            "required": ["recipe_name", "note"],
        },
    },
    {
        "name": "flag_recipe_temporary",
        "description": "Temporarily exclude a recipe from auto-suggestion rotation (excluded=true), or bring it back (excluded=false) — distinct from a permanent 'disliked' rating. Use when the household is just tired of a favorite for now, not actually disliking it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recipe_name": {"type": "string"},
                "excluded": {"type": "boolean", "description": "Defaults to true (exclude). Pass false to bring it back into rotation."},
            },
            "required": ["recipe_name"],
        },
    },
    {
        "name": "get_recipe",
        "description": "Get full detail for a single saved recipe by exact name — ingredients, instructions, timing. Use when the user wants to see a specific recipe in full.",
        "input_schema": {
            "type": "object",
            "properties": {"recipe_name": {"type": "string"}},
            "required": ["recipe_name"],
        },
    },
    {
        "name": "update_recipe_details",
        "description": "Backfill or correct a saved recipe's instructions/servings/timing/advance-prep notes. Use this right after get_recipe comes back with empty instructions: work out a reasonable step-by-step yourself and save it here in the same turn, rather than telling the user nothing's saved. Only pass the fields you're setting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recipe_name": {"type": "string"},
                "instructions": {"type": "array", "items": {"type": "string"}},
                "default_servings": {"type": "integer"},
                "prep_time_minutes": {"type": "integer"},
                "cook_time_minutes": {"type": "integer"},
                "advance_prep_notes": {"type": "string"},
            },
            "required": ["recipe_name"],
        },
    },
    {
        "name": "scale_recipe",
        "description": "Scale a saved recipe's ingredient quantities to a different number of servings than it's written for (e.g. cooking for 6 when the recipe serves 4).",
        "input_schema": {
            "type": "object",
            "properties": {
                "recipe_name": {"type": "string"},
                "target_servings": {"type": "integer"},
            },
            "required": ["recipe_name", "target_servings"],
        },
    },
    {
        "name": "log_cooking_deviation",
        "description": "Capture something that actually changed while cooking a recipe — a swap, an adjusted step, a doubled component — so it's not lost. Call the moment the user mentions cooking something differently than the recipe says.",
        "input_schema": {
            "type": "object",
            "properties": {"recipe_name": {"type": "string"}, "note": {"type": "string"}},
            "required": ["recipe_name", "note"],
        },
    },
    {
        "name": "plan_meal",
        "description": "Schedule a meal (recipe name or freeform description) for a date/slot. Auto-adds ingredients to grocery list and food_groups if it's a saved recipe. Returns food_groups_missing so you can optionally, gently, suggest rounding it out.",
        "input_schema": {
            "type": "object",
            "properties": {
                "meal_date": {"type": "string", "description": "YYYY-MM-DD"},
                "meal": {"type": "string"},
                "slot": {"type": "string", "enum": ["breakfast", "lunch", "dinner"]},
                "add_ingredients_to_grocery_list": {"type": "boolean"},
                "food_groups": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["protein", "carb", "vegetable"]},
                    "description": "Only used for freeform meals not tied to a saved recipe. Leave out if unclear.",
                },
            },
            "required": ["meal_date", "meal"],
        },
    },
    {
        "name": "get_meal_plan",
        "description": "Get the upcoming meal plan for the next N days, including food groups covered per meal where known.",
        "input_schema": {
            "type": "object",
            "properties": {"days_ahead": {"type": "integer"}},
        },
    },
    {
        "name": "generate_weekly_plan",
        "description": "Generate and save a full week's meal plan in one pass, tailored to this household's preferences, dislikes, restrictions, and recent meal history (avoids repeats, surfaces new recipes). Preferred over multiple plan_meal calls whenever the user wants a whole week planned at once (e.g. 'plan my week', 'what should we eat this week').",
        "input_schema": {
            "type": "object",
            "properties": {
                "week_start_date": {"type": "string", "description": "YYYY-MM-DD, first day to plan."},
                "constraints_notes": {"type": "string", "description": "Freeform per-week asks, e.g. 'out Thu/Fri, keep it under 30 min on weeknights, one vegetarian night'."},
                "day_count": {"type": "integer", "description": "Defaults to 7."},
            },
            "required": ["week_start_date"],
        },
    },
    {
        "name": "set_week_constraints",
        "description": "Set/update one-off constraints on a specific week's plan (e.g. '3 nights this week', 'under 30 min on weeknights') without making them a permanent preference. Omit weekly_plan_id for the current/most recent plan. If generating a brand-new plan, just pass constraints_notes to generate_weekly_plan instead — use this for constraints that come up after a plan already exists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "constraints_notes": {"type": "string"},
                "weekly_plan_id": {"type": "integer"},
            },
            "required": ["constraints_notes"],
        },
    },
    {
        "name": "get_weekly_plan",
        "description": "Get a generated weekly plan with all its meals. Omit weekly_plan_id to get the household's most recent plan.",
        "input_schema": {
            "type": "object",
            "properties": {"weekly_plan_id": {"type": "integer"}},
        },
    },
    {
        "name": "swap_meal_in_plan",
        "description": "Replace one day's meal in an already-generated weekly plan without regenerating the rest of the week.",
        "input_schema": {
            "type": "object",
            "properties": {
                "weekly_plan_id": {"type": "integer"},
                "meal_date": {"type": "string", "description": "YYYY-MM-DD"},
                "new_meal": {"type": "string"},
                "slot": {"type": "string", "enum": ["breakfast", "lunch", "dinner"]},
                "food_groups": {"type": "array", "items": {"type": "string", "enum": ["protein", "carb", "vegetable"]}},
            },
            "required": ["weekly_plan_id", "meal_date", "new_meal"],
        },
    },
    {
        "name": "swap_component_in_plan",
        "description": "Replace one item within a component_based plan's category (e.g. swap out one of the proteins) without touching the rest of the plan — the component-plan equivalent of swap_meal_in_plan.",
        "input_schema": {
            "type": "object",
            "properties": {
                "weekly_plan_id": {"type": "integer"},
                "component_category": {"type": "string", "enum": ["breakfast", "protein", "vegetable", "carb", "treat", "dip"]},
                "old_meal": {"type": "string", "description": "Exact current item name being replaced."},
                "new_meal": {"type": "string"},
                "food_groups": {"type": "array", "items": {"type": "string", "enum": ["protein", "carb", "vegetable"]}},
            },
            "required": ["weekly_plan_id", "component_category", "old_meal", "new_meal"],
        },
    },
    {
        "name": "approve_weekly_plan",
        "description": "Mark a weekly plan as approved/reviewed by the Planner.",
        "input_schema": {
            "type": "object",
            "properties": {"weekly_plan_id": {"type": "integer"}},
            "required": ["weekly_plan_id"],
        },
    },
    {
        "name": "generate_prep_schedule",
        "description": "Generate (or regenerate) the prep/cooking schedule for a weekly plan — what needs prepping or starting ahead of time (marinating, thawing, batch-cooking) and when, derived from each meal's recipe timing. Omit weekly_plan_id for the household's current plan. Regenerating replaces the previous schedule.",
        "input_schema": {
            "type": "object",
            "properties": {"weekly_plan_id": {"type": "integer"}},
        },
    },
    {
        "name": "get_prep_schedule",
        "description": "Get the generated prep-task schedule for a plan. Omit weekly_plan_id for the household's current plan.",
        "input_schema": {
            "type": "object",
            "properties": {"weekly_plan_id": {"type": "integer"}},
        },
    },
    {
        "name": "check_off_prep_step",
        "description": "Mark a specific prep task as done or back to pending.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prep_task_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["pending", "done"]},
            },
            "required": ["prep_task_id"],
        },
    },
    {
        "name": "check_off_meal",
        "description": "Mark a specific planned meal as cooked (status='done') or back to pending. Find the entry_id via get_weekly_plan or get_plan_progress. Marking done also tries to deplete its ingredients from tracked inventory — confident matches happen silently (mention briefly if relevant, e.g. 'used up the last of the chicken'), anything uncertain is queued into get_attention_items rather than guessed at (returned in the result as inventory_queued_for_review).",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["pending", "done"]},
            },
            "required": ["entry_id"],
        },
    },
    {
        "name": "get_plan_progress",
        "description": "Get a done-vs-outstanding view of a weekly plan: which meals have been cooked and which prep tasks are done, plus counts. Omit weekly_plan_id for the household's current plan. Use this when the user asks 'what's left to cook this week' or similar.",
        "input_schema": {
            "type": "object",
            "properties": {"weekly_plan_id": {"type": "integer"}},
        },
    },
    {
        "name": "get_cooker_view",
        "description": "Get everything needed to cook this week's plan in one shot: each meal with full recipe detail (ingredients, instructions, timing, advance-prep notes, cooked status), the prep schedule, and progress. Omit weekly_plan_id for the household's current plan.",
        "input_schema": {
            "type": "object",
            "properties": {"weekly_plan_id": {"type": "integer"}},
        },
    },
    {
        "name": "check_plan_conflicts",
        "description": "Check a weekly plan's meals against household members' saved dietary restrictions/allergies for likely clashes (e.g. a peanut allergy against a recipe listing peanut butter). Non-blocking — surfaces warnings, doesn't prevent approval. Call before approve_weekly_plan and mention any conflicts found. Omit weekly_plan_id for the household's current plan.",
        "input_schema": {
            "type": "object",
            "properties": {"weekly_plan_id": {"type": "integer"}},
        },
    },
    {
        "name": "explain_meal_choice",
        "description": "Explain why a meal is/isn't a natural suggestion right now — rating, feedback notes, times cooked, last cooked date, tags, cuisine, whether it's temporarily excluded, etc. Use when the user asks 'why did you suggest this?' or 'why haven't we had X in a while?'",
        "input_schema": {
            "type": "object",
            "properties": {"meal_name": {"type": "string"}},
            "required": ["meal_name"],
        },
    },
    {
        "name": "get_feedback_nudge",
        "description": "Check whether there's a good moment to gently ask about something recently cooked that's never been rated. Prefer get_attention_items instead in most cases — it includes this same check plus anything else pending (like low-confidence inventory-depletion matches) in one call.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_attention_items",
        "description": "The unified 'needs your attention' list — a recently-cooked, unrated meal (the feedback nudge) plus any low-confidence ingredient-to-inventory matches from checking a meal off as cooked. Call this once near the start of a conversation (not every message) and, if it returns anything, work it into your response in one low-key way rather than an interrogation checklist — e.g. 'by the way, the garlic in last night's dinner — should I take that off the tracked garlic bulb, or was that something else?' Use resolve_attention_item once the user's answered.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "resolve_attention_item",
        "description": "Mark a queued attention item handled ('resolved') or not relevant ('dismissed') so it stops showing up in get_attention_items. Only pass a real item id (from get_attention_items) — the feedback-nudge entry has id=None and resolves itself once the meal gets a rating, not through this.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["resolved", "dismissed"]},
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "set_item_store",
        "description": "Remember which store a grocery item should be bought at (e.g. 'we get paper towels at Costco'). Applies to matching items already on the list and to future adds of that item name. Pass an empty store to clear.",
        "input_schema": {
            "type": "object",
            "properties": {"item": {"type": "string"}, "store": {"type": "string"}},
            "required": ["item", "store"],
        },
    },
    {
        "name": "get_grocery_list_by_store",
        "description": "Get the grocery list split into store groups (see set_item_store), each grouped by section. Use instead of get_grocery_list_by_section once the household has assigned items to more than one store.",
        "input_schema": {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["needed", "in_cart", "purchased", "all", "excluded"], "description": "'excluded' shows only items hidden via exclude_grocery_item; they're left out automatically from 'needed'/'in_cart'/'purchased' but included in 'all'."}},
        },
    },
    {
        "name": "get_learning_summary",
        "description": "Get an aggregate, human-readable snapshot of what's been learned so far (recipes tracked, liked/disliked counts, temporarily excluded, deviations logged). Use when the user asks 'what have you picked up about us?' or similar — distinct from get_household_memory's raw preference values.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_planning_mode",
        "description": "Set the household's standing weekly-planning mode: 'day_based' (default, one meal per day/slot) or 'component_based' (plan by category instead — a breakfast for the week, several proteins, several vegetables, carbs, a treat, a dip, for the household to assemble freely). Household-level, applies to the next generated plan; can be changed again any time.",
        "input_schema": {
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": ["day_based", "component_based"]}},
            "required": ["mode"],
        },
    },
    {
        "name": "get_or_create_member_share_link",
        "description": "Get (or create) a standing personal link for one household member, so they can add their own dietary restrictions and leave feedback notes directly without going through the Planner. Returns the same link on repeat calls unless it's been revoked/regenerated. The result's `link` field is the real, absolute URL — share it exactly as returned, never type out or guess a URL yourself.",
        "input_schema": {
            "type": "object",
            "properties": {"member_name": {"type": "string"}},
            "required": ["member_name"],
        },
    },
    {
        "name": "revoke_member_share_link",
        "description": "Revoke a household member's self-service link — it stops working immediately (e.g. it was shared by mistake, or the household wants to shut it off).",
        "input_schema": {
            "type": "object",
            "properties": {"member_name": {"type": "string"}},
            "required": ["member_name"],
        },
    },
    {
        "name": "regenerate_member_share_link",
        "description": "Revoke a household member's current self-service link and issue a fresh one in one step (e.g. it may have leaked).",
        "input_schema": {
            "type": "object",
            "properties": {"member_name": {"type": "string"}},
            "required": ["member_name"],
        },
    },
    {
        "name": "get_member_notes",
        "description": "List freeform preference/feedback notes household members have left via their self-service links. Omit member_name for everyone's notes.",
        "input_schema": {
            "type": "object",
            "properties": {"member_name": {"type": "string"}},
        },
    },
    {
        "name": "get_household_memory",
        "description": "Get a plain summary of everything saved about this household's meal preferences: member dietary restrictions, favorite proteins/cuisines, dislikes, cooking-time preference, notes, goals, usual stores. Use this when the user asks what the app knows/remembers, or before generating a plan.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "edit_preference",
        "description": "Directly set a household meal-preference field to a new value, for corrections. Valid fields: 'notes', 'cooking_time_preference' (both plain strings), 'cuisine_preferences'/'dislikes'/'usual_stores' (list of strings, replaces the whole list — prefer add_food_dislikes/add_usual_stores for adding a single new item conversationally), 'protein_preferences' (dict of protein -> how-often, e.g. {\"chicken\": \"several times a week\"}, merged in). Use delete_preference instead to remove a single item without replacing the whole list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": ["notes", "cooking_time_preference", "cuisine_preferences", "protein_preferences", "dislikes", "usual_stores"]},
                "value": {"description": "String for notes/cooking_time_preference, array for cuisine_preferences/dislikes/usual_stores, object for protein_preferences."},
            },
            "required": ["field", "value"],
        },
    },
    {
        "name": "delete_preference",
        "description": "Remove a remembered preference. For 'dislikes', 'cuisine_preferences', or 'usual_stores', pass item = the value to remove. For 'protein_preferences', item = the protein name. For 'notes' or 'cooking_time_preference', omit item to clear the field.",
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": ["dislikes", "cuisine_preferences", "protein_preferences", "notes", "cooking_time_preference", "usual_stores"]},
                "item": {"type": "string"},
            },
            "required": ["field"],
        },
    },
    {
        "name": "add_grocery_item",
        "description": "Add an item to the grocery list. If a matching item is already on the list, quantities are consolidated into one line automatically (units permitting) rather than creating a duplicate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string"},
                "quantity": {"type": "string"},
                "category": {"type": "string", "enum": ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"], "description": "Pick the one that actually matches the item so the list stays organized by store section."},
            },
            "required": ["item"],
        },
    },
    {
        "name": "add_grocery_items",
        "description": "Add several items to the grocery list at once, e.g. from 'add milk and eggs to the list'. Preferred over add_grocery_item when the user names more than one thing. Each entry can be a plain string, or an object {item, quantity, category} when you know more detail (e.g. from a recipe). Quantities consolidate with anything already on the list automatically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "item": {"type": "string"},
                                    "quantity": {"type": "string"},
                                    "category": {"type": "string", "enum": ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"]},
                                },
                                "required": ["item"],
                            },
                        ],
                    },
                },
                "category": {"type": "string", "enum": ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"], "description": "Fallback category for entries given as plain strings."},
            },
            "required": ["items"],
        },
    },
    {
        "name": "list_grocery_list",
        "description": "List grocery items as a flat list, filtered by status.",
        "input_schema": {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["needed", "in_cart", "purchased", "all", "excluded"], "description": "'excluded' shows only items hidden via exclude_grocery_item; they're left out automatically from 'needed'/'in_cart'/'purchased' but included in 'all'."}},
        },
    },
    {
        "name": "get_grocery_list_by_section",
        "description": "Get the grocery list grouped into standard store sections (produce, dairy, meat/seafood, pantry, frozen, other) in shopping order. Prefer this over list_grocery_list whenever showing or reviewing the list with the user, so it reads like something they can actually shop from.",
        "input_schema": {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["needed", "in_cart", "purchased", "all", "excluded"], "description": "'excluded' shows only items hidden via exclude_grocery_item; they're left out automatically from 'needed'/'in_cart'/'purchased' but included in 'all'."}},
        },
    },
    {
        "name": "consolidate_grocery_list",
        "description": "Merge any duplicate lines for the same item into one, combining quantities. Call this immediately (don't just ask permission) if you notice the same item listed more than once when reviewing the grocery list, or if the user asks to clean up/consolidate it.",
        "input_schema": {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["needed", "in_cart", "purchased", "all"]}},
        },
    },
    {
        "name": "clear_stale_grocery_items",
        "description": "Remove 'needed' items sourced from an older, already-superseded generated weekly plan (not the current one) — fixes quantities silently stacking up across several weeks onto the same line. Never touches items a person added directly. This runs automatically each time a new week is generated, but call it directly if the user points out old/inflated quantities and wants them cleared.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "clear_grocery_list",
        "description": "Remove ALL items with a given status in one shot — a full reset. Only use this when the user explicitly asks to wipe/empty/start the grocery list over, not for routine cleanup (use consolidate_grocery_list or clear_stale_grocery_items instead).",
        "input_schema": {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["needed", "in_cart", "purchased", "all"]}},
        },
    },
    {
        "name": "mark_grocery_item",
        "description": "Update a grocery item's status, given its item_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["needed", "in_cart", "purchased"]},
            },
            "required": ["item_id", "status"],
        },
    },
    {
        "name": "update_grocery_item",
        "description": "Directly correct an already-listed grocery item's quantity and/or category by item_id (e.g. \"actually make that 3 lbs, not 2\" or \"that's produce, not pantry\") — for fixing a line already on the list, not adding a new one. Leave a field unset to leave it unchanged.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer"},
                "quantity": {"type": "string"},
                "category": {"type": "string", "enum": ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"]},
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "remove_grocery_item",
        "description": "Delete an item from the grocery list, given its item_id. For something the Shopper will get elsewhere rather than buying it at all, prefer exclude_grocery_item instead — this permanently removes it from meal-plan ingredient tracking too, not just the shown list.",
        "input_schema": {
            "type": "object",
            "properties": {"item_id": {"type": "integer"}},
            "required": ["item_id"],
        },
    },
    {
        "name": "exclude_grocery_item",
        "description": "Hide an item from the normal shown/shopped grocery list without deleting it — for something the Shopper will get elsewhere (a butcher, a farmers market) instead of on the regular trip. Stays tracked (a future add of the same item still consolidates into this line, not a duplicate); just won't appear in the list shown by default. Use include_grocery_item to undo.",
        "input_schema": {
            "type": "object",
            "properties": {"item_id": {"type": "integer"}},
            "required": ["item_id"],
        },
    },
    {
        "name": "include_grocery_item",
        "description": "Undo exclude_grocery_item — put an item back on the normal shown/shopped grocery list.",
        "input_schema": {
            "type": "object",
            "properties": {"item_id": {"type": "integer"}},
            "required": ["item_id"],
        },
    },
    {
        "name": "get_grocery_already_have_items",
        "description": "Cross-reference the 'needed' grocery list against tracked inventory to flag items that may not actually need buying (already tracked with a quantity on hand). Check this if the user asks whether anything on the list is redundant, or proactively mention it if something obviously overlaps. Each item is only flagged once — see mark_grocery_item_already_have_reviewed to confirm one is still needed.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "mark_grocery_item_already_have_reviewed",
        "description": "Confirm a grocery item flagged by get_grocery_already_have_items is still needed despite the inventory match (e.g. running low) — stops it from being flagged again.",
        "input_schema": {
            "type": "object",
            "properties": {"item_id": {"type": "integer"}},
            "required": ["item_id"],
        },
    },
    {
        "name": "update_inventory",
        "description": "Update pantry/fridge inventory from a chat mention (buying, using, running out of something). Call this proactively any time the user mentions inventory-related info, the same way preferences get captured proactively — there's no manual-entry screen, chat is the only way this gets tracked.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["add", "use", "remove", "set"],
                    "description": "add=bought/received it; use=some or all was used (blank quantity means all); remove=gone for another reason (spoiled, thrown out); set=state an absolute amount currently on hand.",
                },
                "quantity": {"type": "string", "description": "Freeform, e.g. '2 lbs'. Leave blank if not mentioned (for use/remove, blank means all of it)."},
                "expiration_date": {"type": "string", "description": "ISO date, only if the person actually mentioned one. Leave unset otherwise."},
                "category": {"type": "string", "enum": ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"], "description": "Grocery-style store section, so the Inventory view stays organized. Defaults to 'other' if omitted."},
                "location": {"type": "string", "enum": ["fridge", "freezer", "pantry"], "description": "Where it's physically stored, independent from category (e.g. an opened sauce is category='pantry' by food type but location='fridge' once opened). Set explicitly when mentioned/implied, especially if the same item might also exist elsewhere (an unopened one still in the pantry) so they're tracked as distinct entries. Leave unset to fall back to a category-based guess for a new item, or leave an existing item's location unchanged."},
            },
            "required": ["item", "action"],
        },
    },
    {
        "name": "update_inventory_items",
        "description": "Update several inventory items in one call — use this instead of repeated update_inventory calls whenever more than one item is mentioned at once, especially the first time someone lists out everything in their pantry/fridge.",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "Each entry: a plain string (uses the shared action), or an object with item/action/quantity/expiration_date/category/location to mix actions within one call. Fill in category per item (produce/dairy/meat-seafood/pantry/frozen/other) so everything lands in the right Inventory view section, and location (fridge/freezer/pantry) whenever it's known or implied — see update_inventory.",
                    "items": {"type": "string"},
                },
                "action": {
                    "type": "string",
                    "enum": ["add", "use", "remove", "set"],
                    "description": "Default action applied to any plain-string entries.",
                },
            },
            "required": ["items"],
        },
    },
    {
        "name": "get_inventory",
        "description": "List everything currently tracked in pantry/fridge inventory. Check before suggesting a grocery addition for a staple that might already be on hand — ask rather than silently adding if it looks like they already have it.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_inventory_by_section",
        "description": "Get pantry/fridge inventory grouped into store sections (produce, dairy, meat/seafood, pantry, frozen, other). Use this instead of get_inventory whenever showing the full inventory to the user, so it reads organized rather than a flat list.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_inventory_by_location",
        "description": "Get pantry/fridge inventory grouped by storage location (fridge, freezer, pantry) instead of food category. Use this specifically when the user asks what's in the fridge, what's in the pantry, etc. — location can diverge from category (an opened sauce is category='pantry' by food type but location='fridge' once opened), so this is the more accurate answer to that particular question than get_inventory_by_section.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_cross_location_duplicates",
        "description": "Find items tracked in more than one storage location at once (e.g. an opened BBQ sauce in the fridge and an unopened one still in the pantry). Check this proactively alongside get_expiring_soon when it's relevant — inventory questions, before adding something to the grocery list, when generating a plan — and flag it (e.g. 'you've got mustard in both the fridge and pantry — might want to use up the opened one before buying more').",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_expiring_soon",
        "description": "List inventory items already expired or expiring within N days (soonest first), each flagged 'expired' or 'expiring_soon'. Use for 'what's about to go bad' questions, and check proactively before suggesting meals so near-expiring items get used before they're wasted.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "description": "Lookahead window in days. Defaults to 4."}},
        },
    },
    {
        "name": "get_fresh_perishable_inventory",
        "description": "List meats/seafood, produce, and dairy on hand that aren't urgent yet (that's get_expiring_soon) but are still perishable. Use when suggesting a one-off meal or answering 'what should we make' to lean toward using what's already on hand over defaulting to a fresh purchase — a soft general preference, not a hard rule.",
        "input_schema": {
            "type": "object",
            "properties": {"near_expiring_days": {"type": "integer", "description": "Items expiring within this many days are excluded (they're already covered by get_expiring_soon). Defaults to 4."}},
        },
    },
    {
        "name": "remove_inventory_item",
        "description": "Remove a single inventory item outright by id (e.g. it spoiled, or was tracked by mistake).",
        "input_schema": {
            "type": "object",
            "properties": {"item_id": {"type": "integer"}},
            "required": ["item_id"],
        },
    },
]

_GENERATE_WEEKLY_PLAN_TOOL = {
    "name": "submit_weekly_plan",
    "description": "Submit the generated weekly meal plan.",
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {
                "type": "array",
                "description": "One entry per planned day/slot.",
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "ISO date, e.g. 2026-08-10."},
                        "slot": {"type": "string", "enum": ["breakfast", "lunch", "dinner"]},
                        "meal_name": {"type": "string", "description": "Recipe name, existing or new."},
                        "is_new_recipe": {"type": "boolean"},
                        "ingredients": {
                            "type": "array",
                            "description": "Required if is_new_recipe is true; omit/empty for an existing saved recipe.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "item": {"type": "string"},
                                    "qty": {"type": "string"},
                                    "category": {
                                        "type": "string",
                                        "enum": ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"],
                                        "description": "Grocery store section (pantry = shelf-stable only; eggs/butter/tofu are dairy; fresh vegetables/herbs are produce).",
                                    },
                                },
                                "required": ["item"],
                            },
                        },
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "food_groups": {"type": "array", "items": {"type": "string", "enum": ["protein", "carb", "vegetable"]}},
                        "cuisine": {"type": "string"},
                        "main_protein": {"type": "string"},
                        "instructions": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Ordered cooking steps — fill in for a new recipe so it's actually cookable, not just a shopping list.",
                        },
                        "default_servings": {"type": "integer", "description": "What the ingredient quantities above are written for. Defaults to 4 if omitted."},
                        "prep_time_minutes": {"type": "integer"},
                        "cook_time_minutes": {"type": "integer"},
                        "advance_prep_notes": {"type": "string", "description": "e.g. 'marinate at least 4 hours ahead, can be done the night before'. Leave blank if nothing needs advance prep."},
                    },
                    "required": ["date", "slot", "meal_name", "is_new_recipe"],
                },
            },
        },
        "required": ["days"],
    },
}


def generate_weekly_plan_llm(context: dict) -> list[dict]:
    """
    Given household context (preferences, dislikes, member restrictions,
    saved recipes, recent meal history), ask Claude for a full week's meal
    plan in a single pass. Uses a forced tool call so the result is always
    structured, reviewable data — not a chat narrative. Recipes are
    generated/adapted for this household's specific preferences, not
    filtered from a fixed catalog, per the product's moat thesis.
    """
    client = _client()
    prompt = f"""Household context (JSON):
{json.dumps(context, indent=2)}

Generate a full week's dinner plan (7 days unless day_count says otherwise) for this \
household. Guidelines:
- Respect every listed dietary restriction and allergy without exception. Avoid every \
listed dislike.
- Lean toward liked/favorite recipes from saved_recipes (rating='liked' or high \
times_cooked), but don't just repeat them. household_memory's novelty_preference sets how \
much new-recipe exposure to aim for this week: "mostly_favorites" -> still surface at least \
ONE new recipe (never zero — this is a floor, not optional), the rest can lean on favorites; \
"balanced" (the default) -> aim for 2-3 new recipes across the week alongside favorites; \
"surprise_me_often" -> most of the week can be new/untested recipes, favorites become the \
minority. Regardless of setting, never let the rotation shrink to only "safe" meals — that's \
the floor this whole rule exists to enforce.
- A recipe's recent_one_off_notes (see saved_recipes) are a single occurrence's comment, not a \
verdict — weigh them softly (e.g. avoid the exact same misstep if a note calls one out), but \
don't treat them like rating='disliked'. Only an actual 'disliked' rating should exclude a \
recipe from suggestion.
- Avoid repeating any meal (or a near-identical variant) that appears in recent_history \
within the last 3 weeks, and avoid repeating the same main_protein or cuisine too many \
days in a row — check recent_history's cuisine/main_protein fields, not just meal names.
- household_memory's protein_preferences say how often the household wants each protein \
(e.g. "several times a week", "1-2 times a week", "occasionally", "rarely", "avoid") — treat \
this as a real constraint on the week's mix, not just a tiebreaker: a protein marked "avoid" \
shouldn't appear at all, "rarely" should appear at most once across the week, and one marked \
"several times a week" should show up more than once. Weigh this alongside — not instead of — \
the variety rules above.
- Honor any per-week constraints in constraints_notes exactly (e.g. "out Thursday/Friday" \
means don't plan those days; "under 30 minutes on weeknights" means quick weeknight meals).
- For each day, set is_new_recipe=true and fill in ingredients/tags/food_groups/cuisine/ \
main_protein only if this is a recipe not already in saved_recipes. If you're reusing a \
saved recipe, set is_new_recipe=false and just give its exact meal_name — don't re-invent \
its ingredients.
- cuisine and main_protein should be filled in for every day where reasonably inferable \
(existing or new recipe) — this is what powers future variety checks, so don't leave it \
blank just because the recipe already existed.
- For every new-recipe ingredient, set category to the grocery store section it actually \
belongs to (produce, dairy, meat/seafood, pantry, frozen, other) — pantry means shelf-stable \
only; eggs, butter, and tofu are dairy; fresh vegetables/herbs are produce. This determines \
which aisle it's grouped under when auto-added to the grocery list, so don't leave it blank \
or default to pantry/other out of habit.
- current_inventory lists what's already on hand. For an ingredient already covered there in a \
comparable quantity, still include it in the recipe's ingredients list (the recipe should stay \
accurate/reusable), but leave its category as normal — the household already has it, so it \
doesn't need to be over-represented as a fresh grocery need; don't let already-stocked pantry \
staples influence which recipes you pick either way.
- near_expiring_inventory lists items already expired or expiring soon, most urgent first — \
unlike current_inventory generally (which shouldn't sway recipe choice), actively favor at \
least one recipe this week that uses up something on this list, especially anything already \
'expired'. This is a real goal, not a tiebreaker — reducing food waste is the point — but don't \
force a bad fit: skip an item if nothing reasonable uses it, rather than contorting a recipe \
around it.
- fresh_perishable_inventory lists meats/seafood, produce, and dairy already on hand that aren't \
urgent yet (that's near_expiring_inventory above) but are still perishable. Give these a gentle, \
general preference over buying more of the same when a recipe would work well with them — e.g. \
if there's chicken thighs on hand and you're picking a protein for one of the nights anyway, lean \
toward a chicken recipe rather than defaulting to something requiring a fresh purchase. This is a \
soft lean, not a rule: don't force an odd combination, don't feel obligated to use every item on \
the list, and don't let it override genuine variety/preference/novelty considerations — it only \
matters as a tiebreaker-ish nudge among otherwise-reasonable options.
- For any new recipe, fill in instructions (ordered cooking steps) so it's actually cookable \
later, not just a shopping list — this powers the Cooker view. Also fill in default_servings, \
prep_time_minutes/cook_time_minutes, and advance_prep_notes (e.g. "marinate at least 4 hours \
ahead") whenever reasonably inferable — advance_prep_notes in particular feeds \
generate_prep_schedule, so leave it blank rather than guessing if nothing genuinely needs \
advance prep, but don't skip it out of habit when something clearly does (marinating, thawing, \
soaking, dough that needs to rise, etc.).

Call submit_weekly_plan with the result."""

    # A full week where every day is a brand-new recipe (the common case on
    # first-ever use, before any saved_recipes exist) needs a full
    # ingredients/tags/food_groups list per day — 4096 tokens was cutting
    # this off mid-JSON, which the SDK can't parse, causing the whole
    # generation to fail. Bumped well above the realistic worst case.
    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        tools=[_GENERATE_WEEKLY_PLAN_TOOL],
        tool_choice={"type": "tool", "name": "submit_weekly_plan"},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "max_tokens":
        logger.warning("generate_weekly_plan_llm hit max_tokens; plan may be incomplete")
    for block in response.content:
        if block.type == "tool_use":
            return block.input.get("days", [])
    return []


_GENERATE_COMPONENT_PLAN_TOOL = {
    "name": "submit_component_plan",
    "description": "Submit the generated component-based weekly plan.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": "One entry per planned item — e.g. three separate entries with category='protein' for three proteins, not one entry covering all three.",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": ["breakfast", "protein", "vegetable", "carb", "treat", "dip"]},
                        "meal_name": {"type": "string", "description": "A single standalone item for this category only — don't bundle in another category (e.g. a protein item should not include 'with rice' or 'with beans' in the name; submit those as their own separate carb/vegetable items)."},
                        "is_new_recipe": {"type": "boolean"},
                        "ingredients": {
                            "type": "array",
                            "description": "Required if is_new_recipe is true; omit/empty for an existing saved recipe.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "item": {"type": "string"},
                                    "qty": {"type": "string"},
                                    "category": {
                                        "type": "string",
                                        "enum": ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"],
                                        "description": "Grocery store section.",
                                    },
                                },
                                "required": ["item"],
                            },
                        },
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "food_groups": {"type": "array", "items": {"type": "string", "enum": ["protein", "carb", "vegetable"]}},
                        "cuisine": {"type": "string"},
                        "main_protein": {"type": "string"},
                        "instructions": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Ordered cooking steps — fill in for a new recipe so it's actually cookable, not just a shopping list.",
                        },
                        "default_servings": {"type": "integer", "description": "What the ingredient quantities above are written for. Defaults to 4 if omitted."},
                        "prep_time_minutes": {"type": "integer"},
                        "cook_time_minutes": {"type": "integer"},
                        "advance_prep_notes": {"type": "string", "description": "e.g. 'marinate at least 4 hours ahead, can be done the night before'. Leave blank if nothing needs advance prep."},
                    },
                    "required": ["category", "meal_name", "is_new_recipe"],
                },
            },
        },
        "required": ["items"],
    },
}


def generate_component_plan_llm(context: dict) -> list[dict]:
    """
    Component_based equivalent of generate_weekly_plan_llm: instead of one
    meal per day, produces a pool of items by category (breakfast, protein,
    vegetable, carb, treat, dip) for the household to assemble freely
    across the week. Same forced-tool-call approach for structured output.
    """
    client = _client()
    prompt = f"""Household context (JSON):
{json.dumps(context, indent=2)}

Generate a component-based weekly plan for this household — NOT a day-by-day plan. \
Produce a pool of items by category that the household will mix and match across the week \
themselves, roughly: 1 breakfast idea, 2-3 proteins, 3-4 vegetables, 1-2 carbs, 1 treat, and \
1 dip/sauce (adjust counts modestly for household size/goals in constraints_notes, but stay \
close to this shape — it's a reasonable default, not a rigid rule). Guidelines:
- Respect every listed dietary restriction and allergy without exception. Avoid every listed \
dislike.
- Lean toward liked/favorite recipes from saved_recipes, but honor novelty_preference the same \
way as day-based planning — even "mostly_favorites" should include at least one new item \
somewhere in the pool.
- A recipe's recent_one_off_notes are a soft signal, not a verdict — only an actual \
rating='disliked' should exclude something entirely.
- household_memory's protein_preferences should shape which/how many proteins you pick (a \
protein marked "avoid" shouldn't appear; "several times a week" can appear more than once \
across the protein items).
- Honor any per-week constraints in constraints_notes exactly.
- For each item, set is_new_recipe=true and fill in ingredients/tags/food_groups/cuisine/ \
main_protein only if it's not already in saved_recipes; otherwise is_new_recipe=false with just \
the exact meal_name.
- For every new-item ingredient, set category to the correct grocery store section (produce, \
dairy, meat/seafood, pantry, frozen, other) — pantry means shelf-stable only; eggs/butter/tofu \
are dairy; fresh vegetables/herbs are produce.
- current_inventory lists what's already on hand — still include those ingredients in a new \
recipe's list for accuracy, but don't let already-stocked items influence which items you pick.
- near_expiring_inventory lists items already expired or expiring soon, most urgent first — \
unlike current_inventory generally, actively favor at least one item this week that uses up \
something on this list, especially anything already 'expired'. Skip it if nothing reasonable \
uses the item rather than forcing a bad fit.
- fresh_perishable_inventory lists meats/seafood, produce, and dairy on hand that aren't urgent \
yet but are still perishable. Give these a soft general lean when picking proteins/vegetables for \
the pool — favor something that uses what's already on hand over defaulting to a fresh purchase, \
without forcing a bad fit or feeling obligated to use every item on the list.
- Each item must be a standalone single component of its own category, not a bundled dish that \
mixes categories — the whole point is the household mixes and matches these freely. A protein \
item is just the protein preparation (e.g. "Garlic Lime Shrimp", "Chicken Fajita"), NOT "Garlic \
Lime Shrimp with Black Beans and Cilantro Rice" — that bundles in a carb and hides it from the \
carb slot entirely, so it can't be paired with anything else and the pool undercounts carbs. \
This applies just as much to vegetables as to carbs: NOT "Greek Chicken Skewers with Tomato and \
Cucumber" — the tomato and cucumber are a vegetable side that belongs in its own category='vegetable' \
item (e.g. "Greek Tomato Cucumber Salad"), not folded into the protein's name or ingredient list. \
If a dish idea naturally has a rice/beans/veggie/sauce side, split it: submit the protein alone under \
category='protein' and submit each side separately under its own correct category (carb, \
vegetable, dip) as its own item (even if they were conceived as one recipe). Same rule for every \
category — a vegetable item shouldn't secretly include a protein, a carb item shouldn't secretly \
include a sauce that's really a dip item, etc.
- Once a protein (or any item) is split down to just its own category, its name still needs to \
be specific enough to be useful on its own, since the side dishes that used to make the full \
dish name descriptive are now separate items — "Chicken Skewers" or "Grilled Chicken" alone is \
too generic. Keep the flavor profile, marinade, or prep method in the name even after splitting: \
"Greek Lemon-Oregano Chicken Skewers", "Garlic Lime Shrimp", "Blackened Cajun Salmon" — specific \
enough that picking it out of a pool of proteins tells you what it actually tastes like.
- For any new recipe, fill in instructions (ordered cooking steps), default_servings, \
prep_time_minutes/cook_time_minutes, and advance_prep_notes the same way as day-based planning \
— see the equivalent guidance there. This powers the Cooker view and prep schedule.

Call submit_component_plan with the result."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        tools=[_GENERATE_COMPONENT_PLAN_TOOL],
        tool_choice={"type": "tool", "name": "submit_component_plan"},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "max_tokens":
        logger.warning("generate_component_plan_llm hit max_tokens; plan may be incomplete")
    for block in response.content:
        if block.type == "tool_use":
            return block.input.get("items", [])
    return []


def generate_weekly_plan(week_start_date: str, constraints_notes: str = "", day_count: int = 7) -> dict:
    """
    Generate and save a full week's meal plan in one pass: gathers current
    household memory, saved recipes, and recent meal history, asks Claude
    for a complete week via a forced tool call, then persists it as a new
    weekly_plan with each day's meal attached — creating new recipes as
    needed. Returns the saved plan via get_weekly_plan. This is the
    preferred way to handle "plan my week" / "what should we eat this
    week" style requests instead of planning meal-by-meal across several
    plan_meal calls. Automatically generates a component_based plan
    instead of day-based if that's the household's current planning_mode
    (see set_planning_mode) — the caller doesn't need to know which mode
    is active, this handles both.
    """
    household_memory = tools.get_household_memory()
    context = {
        "week_start_date": week_start_date,
        "day_count": day_count,
        "constraints_notes": constraints_notes,
        "household_memory": household_memory,
        # Temporarily-excluded recipes (flag_recipe_temporary) are filtered out
        # here at the source rather than relying on a prompt instruction, so
        # they're never even a candidate for suggestion.
        "saved_recipes": tools.list_recipes(include_temporarily_excluded=False),
        "recent_history": tools.get_recent_meal_history(weeks=3),
        "current_inventory": tools.get_inventory(),
        # Phase 4, §4.2: items already expired or expiring soon — the LLM
        # prompts are instructed to weight candidate recipes toward using
        # these up, especially the most urgent ones.
        "near_expiring_inventory": tools.get_expiring_soon(),
        # Phase 4, §4.2 follow-up: perishables on hand that aren't urgent
        # yet — a softer "favor what's fresh" nudge distinct from the
        # near-expiring one above.
        "fresh_perishable_inventory": tools.get_fresh_perishable_inventory(),
    }

    plan = tools.create_weekly_plan(week_start_date, constraints_notes=constraints_notes)
    plan_id = plan["weekly_plan_id"]

    # Clears out any 'needed' grocery items still sourced from the PREVIOUS
    # plan before this week's ingredients get added below — otherwise
    # quantities from old, already-superseded weeks silently keep stacking
    # onto the same line forever. Passing plan_id explicitly rather than
    # letting it re-derive "current" avoids any ambiguity if two plans get
    # created within the same second.
    tools.clear_stale_grocery_items(current_weekly_plan_id=plan_id)

    def _ensure_recipe_saved(meal_name, item):
        if item.get("is_new_recipe") and item.get("ingredients"):
            existing = next((r for r in tools.list_recipes() if r["name"] == meal_name), None)
            if not existing:
                tools.add_recipe(
                    name=meal_name,
                    ingredients=item.get("ingredients", []),
                    tags=item.get("tags", []),
                    food_groups=item.get("food_groups", []),
                    cuisine=item.get("cuisine", ""),
                    main_protein=item.get("main_protein", ""),
                    instructions=item.get("instructions", []),
                    default_servings=item.get("default_servings") or 4,
                    prep_time_minutes=item.get("prep_time_minutes"),
                    cook_time_minutes=item.get("cook_time_minutes"),
                    advance_prep_notes=item.get("advance_prep_notes", ""),
                )

    if household_memory.get("planning_mode") == "component_based":
        items = generate_component_plan_llm(context)
        for item in items:
            meal_name = item.get("meal_name")
            category = item.get("category")
            if not meal_name or not category:
                continue
            _ensure_recipe_saved(meal_name, item)
            tools.plan_meal(
                meal_date=week_start_date,  # placeholder — component items aren't tied to a specific day
                meal=meal_name,
                food_groups=item.get("food_groups"),
                weekly_plan_id=plan_id,
                component_category=category,
            )
    else:
        days = generate_weekly_plan_llm(context)
        for day in days:
            meal_name = day.get("meal_name")
            if not meal_name:
                continue
            _ensure_recipe_saved(meal_name, day)
            tools.plan_meal(
                meal_date=day.get("date"),
                meal=meal_name,
                slot=day.get("slot", "dinner"),
                food_groups=day.get("food_groups"),
                weekly_plan_id=plan_id,
            )

    return tools.get_weekly_plan(plan_id)


_GENERATE_PREP_SCHEDULE_TOOL = {
    "name": "submit_prep_schedule",
    "description": "Submit the generated prep/cooking schedule for a weekly plan.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "Prep tasks derived from each meal's timing/advance_prep_notes — only include a task when something genuinely needs prepping ahead (marinating, thawing, batch-cooking, dough rising, etc.), not every meal needs one.",
                "items": {
                    "type": "object",
                    "properties": {
                        "task_date": {"type": "string", "description": "ISO date this should happen on (the day before the meal, morning of, etc. — whatever the timing calls for)."},
                        "description": {"type": "string", "description": "e.g. 'Marinate the chicken for tonight's stir fry (at least 4 hours)'. For a consolidated batch-prep task covering more than one meal, describe it as one task, e.g. 'Cook a big batch of rice — enough for Tuesday's stir fry and Thursday's fried rice'."},
                        "related_meal": {"type": "string", "description": "Meal name(s) this task supports. If it's a batch-prep task consolidated across more than one meal (see guidance below), list all of them joined with ' + ', e.g. 'Tuesday's stir fry + Thursday's fried rice' — one task, not a duplicate per meal."},
                    },
                    "required": ["task_date", "description", "related_meal"],
                },
            },
        },
        "required": ["tasks"],
    },
}


def generate_prep_schedule_llm(context: dict) -> list[dict]:
    """
    Given a week's plan (dates/meals) and each meal's full recipe detail
    (instructions, prep/cook time, advance_prep_notes), work out what needs
    prepping or starting ahead of time and when — derived purely from
    recipe timing, not any real calendar or the Cooker's availability. This
    is closer to a small backward-scheduling pass than simple CRUD: for
    each meal with advance_prep_notes (e.g. "marinate 4+ hours ahead"),
    figure out a sensible date (same day if a few hours is enough, the day
    before if it says "overnight" or the plan is component_based with no
    specific day per item — use the plan's week_start_date as the
    reference point in that case). Also does batch-prep consolidation
    (Phase 4, §4.4): recognizes a component shared across more than one
    meal this week (a rice side, a marinade base) and produces one task
    covering all of them instead of a duplicate task per meal.
    """
    client = _client()
    prompt = f"""Weekly plan + recipe detail (JSON):
{json.dumps(context, indent=2)}

Generate a prep/cooking schedule for this week. Guidelines:
- Only create a task where a recipe's advance_prep_notes, prep_time_minutes, or instructions \
genuinely call for doing something ahead of time (marinating, thawing, soaking, dough that \
needs to rise, batch-cooking a component in advance). Most meals need no task at all — don't \
invent busywork.
- Work backward from each meal's planned date: if advance_prep_notes says "at least 4 hours \
ahead," a same-day morning task is fine; if it says "overnight" or "the night before," schedule \
it the day before instead.
- For a component_based plan (planning_mode='component_based'), items aren't tied to a specific \
day — use week_start_date as the reference point for any tasks needed (e.g. "before you start \
using this component this week").
- Keep descriptions specific and actionable, e.g. "Marinate the chicken for the stir fry (at \
least 4 hours, can do the night before)" rather than just "prep chicken."
- Batch-prep consolidation: before finalizing, look across the WHOLE week's ingredients for \
components that recur in more than one meal — the same side (rice, a grain, a slaw base) used \
in two dishes, a marinade/sauce base shared across meals, an ingredient that needs the same \
chopping/prep for multiple recipes. When you spot a genuine match, consolidate it into ONE task \
that covers all of them (e.g. "Cook a big batch of rice — enough for Tuesday's stir fry and \
Thursday's fried rice") instead of writing a separate near-identical task per meal, and list \
every meal it covers in related_meal. Only consolidate when it's genuinely the same prep serving \
multiple meals — don't force a merge across meals with different quantities, timing, or doneness \
needs just because the ingredient name matches (e.g. rice cooked plain for one dish vs. seasoned \
a specific way for another may still warrant separate tasks; use judgment).

Call submit_prep_schedule with the result."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        tools=[_GENERATE_PREP_SCHEDULE_TOOL],
        tool_choice={"type": "tool", "name": "submit_prep_schedule"},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "max_tokens":
        logger.warning("generate_prep_schedule_llm hit max_tokens; schedule may be incomplete")
    for block in response.content:
        if block.type == "tool_use":
            return block.input.get("tasks", [])
    return []


def generate_prep_schedule(weekly_plan_id: int | None = None) -> dict:
    """
    Generate (or regenerate) the prep/cooking schedule for a weekly plan —
    gathers the plan's meals plus each one's full recipe detail (timing,
    advance-prep notes), asks Claude to work out what needs prepping ahead
    and when, and persists it. Regenerating replaces any previous schedule
    for this plan rather than duplicating it. Returns the plan's progress
    view (see get_plan_progress) so the caller sees both the schedule and
    current done/outstanding state in one call.
    """
    plan = tools.get_weekly_plan(weekly_plan_id)
    plan_id = plan.get("weekly_plan_id")
    if plan_id is None:
        raise ValueError("No weekly plan exists yet — generate one first with generate_weekly_plan.")

    recipes_by_name = {r["name"]: r for r in tools.list_recipes()}
    meals_detail = []
    for m in plan["meals"]:
        recipe = recipes_by_name.get(m["meal"])
        meals_detail.append({
            "date": m["date"],
            "meal": m["meal"],
            "component_category": m.get("component_category"),
            "instructions": recipe["instructions"] if recipe else [],
            # ingredients included (not just timing) so the prep schedule can
            # spot the same component recurring across meals this week — a
            # rice side used twice, a marinade base shared by two dishes —
            # and consolidate that into one batch-prep task instead of one
            # per meal (see the "batch-prep consolidation" prompt guidance
            # in generate_prep_schedule_llm).
            "ingredients": recipe["ingredients"] if recipe else [],
            "prep_time_minutes": recipe["prep_time_minutes"] if recipe else None,
            "cook_time_minutes": recipe["cook_time_minutes"] if recipe else None,
            "advance_prep_notes": recipe["advance_prep_notes"] if recipe else "",
        })

    context = {
        "week_start_date": plan["week_start_date"],
        "planning_mode": plan["planning_mode"],
        "meals": meals_detail,
    }
    tasks = generate_prep_schedule_llm(context)
    tools.save_prep_tasks(plan_id, tasks)
    return tools.get_plan_progress(plan_id)


_FILL_RECIPE_DETAIL_TOOL = {
    "name": "submit_recipe_detail",
    "description": "Submit a full cookable step-by-step for a recipe that's missing one.",
    "input_schema": {
        "type": "object",
        "properties": {
            "instructions": {
                "type": "array", "items": {"type": "string"},
                "description": "Ordered, specific cooking steps — enough to actually cook the dish from, not a vague summary.",
            },
            "default_servings": {"type": "integer", "description": "What the existing ingredient quantities are written for. Keep the recipe's current value unless it's clearly wrong."},
            "prep_time_minutes": {"type": "integer"},
            "cook_time_minutes": {"type": "integer"},
            "advance_prep_notes": {"type": "string", "description": "e.g. 'marinate at least 4 hours ahead, can be done the night before'. Empty string if nothing needs advance prep."},
        },
        "required": ["instructions", "default_servings", "prep_time_minutes", "cook_time_minutes", "advance_prep_notes"],
    },
}


def generate_recipe_detail_llm(recipe: dict) -> dict:
    """
    Given a saved recipe's name and ingredients (and its current
    default_servings, since the ingredient quantities are already written
    for that count), work out a full step-by-step from general cooking
    knowledge of the dish. Powers the Cooker view's "Fill in this recipe"
    button, for recipes that predate instructions being tracked (or were
    saved quickly, e.g. from a reused/component plan) — the chat agent
    already does this inline per the system prompt, but the Cooker view is
    a plain page with no LLM loop of its own, so it needs this as a direct
    call instead.
    """
    client = _client()
    prompt = f"""Recipe (JSON):
{json.dumps(recipe, indent=2)}

This recipe has ingredients but no saved instructions. Write a complete, specific, ordered \
step-by-step for actually cooking it, using your general knowledge of the dish and the \
ingredients/quantities given. Also fill in prep_time_minutes, cook_time_minutes, and \
advance_prep_notes (leave advance_prep_notes as an empty string if nothing needs to be done \
ahead of time — don't invent advance prep that isn't really needed). Keep default_servings the \
same as the recipe's current value unless it's obviously wrong for the ingredient quantities.

Call submit_recipe_detail with the result."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        tools=[_FILL_RECIPE_DETAIL_TOOL],
        tool_choice={"type": "tool", "name": "submit_recipe_detail"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    return {}


def fill_in_recipe(recipe_name: str) -> dict:
    """
    Generate and save a full step-by-step for a recipe that's missing one —
    used by the Cooker view's "Fill in this recipe" button. Idempotent: if
    the recipe already has instructions by the time this runs, just returns
    it as-is rather than overwriting.
    """
    recipe = tools.get_recipe(recipe_name)
    if recipe["instructions"]:
        return recipe
    detail = generate_recipe_detail_llm(recipe)
    if not detail.get("instructions"):
        raise ValueError("Couldn't generate instructions for this recipe — try again.")
    return tools.update_recipe_details(
        recipe_name,
        instructions=detail.get("instructions"),
        default_servings=detail.get("default_servings"),
        prep_time_minutes=detail.get("prep_time_minutes"),
        cook_time_minutes=detail.get("cook_time_minutes"),
        advance_prep_notes=detail.get("advance_prep_notes"),
    )


# ---------- Photo-based inventory capture (Phase 4, §4.3) ----------
# Three entry points (receipt, fridge shelf, pantry shelf) sharing one
# output shape and one forced-tool-call pattern, using Claude's native
# multimodal support directly rather than a separate OCR/vision service.
# None of these are registered as chat tools — like generate_recipe_detail_llm,
# they're direct calls from a dedicated endpoint/button, not something the
# chat agent invokes itself. Per the PRD, results are never saved directly —
# all three return a draft list for the Inventory view to show as an
# editable review step before anything is written to inventory_items, since
# especially fridge/pantry recognition is expected to be error-prone.

_SCAN_ITEMS_TOOL = {
    "name": "submit_scanned_items",
    "description": "Submit the food items detected in the photo, for the user to review/edit before anything is saved.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item": {"type": "string", "description": "Plain item name, e.g. 'ground beef', not the receipt's abbreviated/all-caps SKU text."},
                        "quantity": {"type": "string", "description": "Freeform, e.g. '2 lbs' or '1 dozen'. Leave blank if not reasonably determinable."},
                        "category": {
                            "type": "string",
                            "enum": ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"],
                            "description": "Grocery store section.",
                        },
                        "confidence": {"type": "string", "enum": ["high", "low"], "description": "'low' for anything you're genuinely unsure about (partially obscured, ambiguous, guessed from packaging alone) — surfaced to the user so they know what to double-check."},
                    },
                    "required": ["item", "category", "confidence"],
                },
            },
        },
        "required": ["items"],
    },
}


def _scan_image_for_items(image_b64: str, media_type: str, instructions: str) -> list[dict]:
    client = _client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        tools=[_SCAN_ITEMS_TOOL],
        tool_choice={"type": "tool", "name": "submit_scanned_items"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": instructions},
            ],
        }],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input.get("items", [])
    return []


def scan_receipt_image(image_b64: str, media_type: str) -> list[dict]:
    """
    Extract grocery items from a photographed receipt — food/grocery line
    items only. Returns a draft list; nothing is saved to inventory here.
    """
    instructions = """This is a photo of a grocery store receipt. Extract every actual food/grocery \
item purchased, with your best-guess plain item name (expand abbreviated/all-caps SKU text into a \
normal name, e.g. "ORG BANANA" -> "organic bananas"), a quantity if the receipt states or implies \
one (weight, count, "2 @ $3"), and the correct grocery category. Skip anything that isn't a \
purchased food item: tax, subtotal/total lines, payment info, loyalty point lines, bag fees, \
coupons/discounts, store header/address text. If a line is illegible or you can't tell what it is, \
leave it out rather than guessing wildly — better to under-extract than invent items. Mark \
confidence 'low' for anything you're genuinely unsure about (unclear abbreviation, ambiguous \
category).

Call submit_scanned_items with the result."""
    return _scan_image_for_items(image_b64, media_type, instructions)


_FRIDGE_PANTRY_SCAN_INSTRUCTIONS_TEMPLATE = """This is a photo of the inside of a {place}. Identify \
each distinct food item you can make out, with your best-guess plain name and the correct grocery \
category (use 'frozen' for anything visibly in a freezer compartment, not by food type alone).

For quantity, actually look for and use whatever visual evidence is there before leaving it blank: \
a package's printed size/volume/weight (e.g. "4 L" on a milk jug, "1 lb" on a butter box, "12" on \
an egg carton), or a clearly countable number of discrete units (3 apples sitting together, 2 \
yogurt tubs). Use that real information rather than defaulting to blank just because it takes more \
looking — someone reading this list shouldn't have to guess what a quick label-glance would've \
told you. That said, never invent a quantity you can't actually read or count: don't assume a \
"typical" size for a product, don't guess how full a container is, and don't estimate a count for \
anything stacked/obscured/too far to make out clearly — leave quantity blank in those cases rather \
than fabricating a plausible-sounding number. Mark confidence 'low' on the whole item (not just the \
quantity) for anything you're meaningfully inferring rather than clearly seeing (a guess from a \
container shape/label edge, something mostly blocked by another item), and don't invent items you \
can't actually make out just to fill out the list. It's fine to return fewer, more confident items \
than to over-guess.

Call submit_scanned_items with the result."""


def _tag_scan_location(items: list[dict], default_location: str) -> list[dict]:
    """
    Attach a storage location to each scanned item deterministically from
    which button/photo the scan came from, rather than asking the model to
    also infer location (unreliable and unnecessary — we already know
    whether this was the fridge or pantry button). The one exception:
    anything visibly in a freezer compartment gets 'freezer' regardless,
    since category='frozen' already signals that from the image itself.
    """
    for it in items:
        it["location"] = "freezer" if it.get("category") == "frozen" else default_location
    return items


def scan_fridge_photo(image_b64: str, media_type: str) -> list[dict]:
    """
    Identify food items visible in a fridge (or fridge freezer compartment)
    photo for an initial stock-take or re-sync. This is the harder
    recognition problem of the two capture methods (mixed/stacked/partially
    obscured items) — per the PRD this always ships with a confirm/edit
    step, never trusted silently, so this always returns a draft list
    rather than saving anything. Every returned item is tagged
    location='fridge' (or 'freezer' for anything visibly in a freezer
    compartment) so it lands in the right place in the Inventory view's
    location grouping without the user having to set it manually.
    """
    instructions = _FRIDGE_PANTRY_SCAN_INSTRUCTIONS_TEMPLATE.format(place="fridge, including its freezer compartment if visible")
    items = _scan_image_for_items(image_b64, media_type, instructions)
    return _tag_scan_location(items, "fridge")


def scan_pantry_photo(image_b64: str, media_type: str) -> list[dict]:
    """
    Identify food items visible in a pantry/cupboard shelf photo. Same
    review-before-save flow as scan_fridge_photo. Every returned item is
    tagged location='pantry' (or 'freezer' for the unusual case of a
    visible chest freezer/freezer drawer in the photo).
    """
    instructions = _FRIDGE_PANTRY_SCAN_INSTRUCTIONS_TEMPLATE.format(place="pantry or cupboard shelf")
    items = _scan_image_for_items(image_b64, media_type, instructions)
    return _tag_scan_location(items, "pantry")


TOOL_FUNCTIONS = {
    "get_household_setup_status": tools.get_household_setup_status,
    "add_member": tools.add_member,
    "list_members": tools.list_members,
    "set_member_dietary_restrictions": tools.set_member_dietary_restrictions,
    "set_member_age_group": tools.set_member_age_group,
    "set_household_goals": tools.set_household_goals,
    "add_pet": tools.add_pet,
    "list_pets": tools.list_pets,
    "get_meal_planning_setup_status": tools.get_meal_planning_setup_status,
    "set_household_meal_preferences": tools.set_household_meal_preferences,
    "add_food_dislikes": tools.add_food_dislikes,
    "add_usual_stores": tools.add_usual_stores,
    "get_chores_profile": tools.get_chores_profile,
    "set_chores_profile": tools.set_chores_profile,
    "add_chore": tools.add_chore,
    "list_chore_definitions": tools.list_chore_definitions,
    "update_chore": tools.update_chore,
    "generate_chore_schedule": tools.generate_chore_schedule,
    "schedule_chore_instance": tools.schedule_chore_instance,
    "list_chores": tools.list_chores,
    "complete_chore": tools.complete_chore,
    "add_recipe": tools.add_recipe,
    "list_recipes": tools.list_recipes,
    "get_recipe": tools.get_recipe,
    "update_recipe_details": tools.update_recipe_details,
    "scale_recipe": tools.scale_recipe,
    "mark_recipe_feedback": tools.mark_recipe_feedback,
    "log_recipe_note": tools.log_recipe_note,
    "log_cooking_deviation": tools.log_cooking_deviation,
    "flag_recipe_temporary": tools.flag_recipe_temporary,
    "plan_meal": tools.plan_meal,
    "get_meal_plan": tools.get_meal_plan,
    "generate_weekly_plan": generate_weekly_plan,
    "set_week_constraints": tools.set_week_constraints,
    "get_weekly_plan": tools.get_weekly_plan,
    "swap_meal_in_plan": tools.swap_meal_in_plan,
    "swap_component_in_plan": tools.swap_component_in_plan,
    "approve_weekly_plan": tools.approve_weekly_plan,
    "generate_prep_schedule": generate_prep_schedule,
    "get_prep_schedule": tools.get_prep_schedule,
    "check_off_prep_step": tools.check_off_prep_step,
    "check_off_meal": tools.check_off_meal,
    "get_plan_progress": tools.get_plan_progress,
    "get_cooker_view": tools.get_cooker_view,
    "check_plan_conflicts": tools.check_plan_conflicts,
    "explain_meal_choice": tools.explain_meal_choice,
    "get_feedback_nudge": tools.get_feedback_nudge,
    "get_attention_items": tools.get_attention_items,
    "resolve_attention_item": tools.resolve_attention_item,
    "set_item_store": tools.set_item_store,
    "get_grocery_list_by_store": tools.get_grocery_list_by_store,
    "get_learning_summary": tools.get_learning_summary,
    "set_planning_mode": tools.set_planning_mode,
    "get_or_create_member_share_link": tools.get_or_create_member_share_link,
    "revoke_member_share_link": tools.revoke_member_share_link,
    "regenerate_member_share_link": tools.regenerate_member_share_link,
    "get_member_notes": tools.get_member_notes,
    "get_household_memory": tools.get_household_memory,
    "edit_preference": tools.edit_preference,
    "delete_preference": tools.delete_preference,
    "add_grocery_item": tools.add_grocery_item,
    "add_grocery_items": tools.add_grocery_items,
    "list_grocery_list": tools.list_grocery_list,
    "get_grocery_list_by_section": tools.get_grocery_list_by_section,
    "consolidate_grocery_list": tools.consolidate_grocery_list,
    "clear_stale_grocery_items": tools.clear_stale_grocery_items,
    "clear_grocery_list": tools.clear_grocery_list,
    "mark_grocery_item": tools.mark_grocery_item,
    "update_grocery_item": tools.update_grocery_item,
    "remove_grocery_item": tools.remove_grocery_item,
    "exclude_grocery_item": tools.exclude_grocery_item,
    "include_grocery_item": tools.include_grocery_item,
    "get_grocery_already_have_items": tools.get_grocery_already_have_items,
    "mark_grocery_item_already_have_reviewed": tools.mark_grocery_item_already_have_reviewed,
    "update_inventory": tools.update_inventory,
    "update_inventory_items": tools.update_inventory_items,
    "get_inventory": tools.get_inventory,
    "get_inventory_by_section": tools.get_inventory_by_section,
    "get_inventory_by_location": tools.get_inventory_by_location,
    "get_cross_location_duplicates": tools.get_cross_location_duplicates,
    "get_expiring_soon": tools.get_expiring_soon,
    "get_fresh_perishable_inventory": tools.get_fresh_perishable_inventory,
    "remove_inventory_item": tools.remove_inventory_item,
}


def _client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it before starting the server, e.g.\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...\n"
        )
    return Anthropic(api_key=api_key)


_RECOMMEND_CHORES_TOOL = {
    "name": "submit_chore_recommendations",
    "description": "Submit the recommended chore list.",
    "input_schema": {
        "type": "object",
        "properties": {
            "chores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "category": {"type": "string", "enum": ["cleaning", "maintenance", "other"]},
                        "frequency": {"type": "string", "enum": ["daily", "weekly", "biweekly", "monthly", "quarterly", "once"]},
                        "assignee_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Subset of the household's rotation_members. Omit/empty for unassigned.",
                        },
                    },
                    "required": ["name", "category", "frequency"],
                },
            },
        },
        "required": ["chores"],
    },
}


def generate_chore_recommendations(profile: dict) -> list[dict]:
    """
    Given a household chores profile (home type, bedroom/bathroom count,
    yard, cleanliness standard, rotation members, pets, existing help,
    include/exclude notes), ask Claude for a recommended starting chore
    list. Uses a forced tool call so the result is always structured JSON,
    no free-text parsing needed. Used by the onboarding wizard's chores
    step — not part of the regular chat tool loop.
    """
    client = _client()
    prompt = f"""Household profile (JSON):
{json.dumps(profile, indent=2)}

Recommend a starting cleaning/maintenance chore list for this household. Guidelines:
- Only use names from rotation_members for assignee_names. Assign chores to a single \
person where that makes sense, or list multiple names for chores that should rotate \
between people. Leave assignee_names empty for anything nobody's clearly responsible for.
- Scale frequency to the stated standard: 'relaxed' = less frequent, 'standard' = \
typical/moderate, 'meticulous' = more frequent.
- Scale bathroom-related chores to the bathroom count if it's more than 1-2.
- Only include yard-related chores if has_yard is true.
- Only include pet-related chores (litter box, pet area cleanup, etc.) if pets is non-empty \
— match the chore to the actual pet type(s) listed.
- If existing_help describes outside help (e.g. a cleaning service) with a frequency, \
don't duplicate what that service already covers — adjust or reduce overlapping deep-clean \
chores instead of doubling up.
- Fold in anything from include_notes as its own chore or two. Do not include anything \
described in exclude_notes.
- Aim for a practical, non-exhaustive list — roughly 8 to 14 chores covering both routine \
cleaning and periodic maintenance (e.g. HVAC filters, smoke detector batteries), not every \
conceivable task.

Call submit_chore_recommendations with the result."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        tools=[_RECOMMEND_CHORES_TOOL],
        tool_choice={"type": "tool", "name": "submit_chore_recommendations"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input.get("chores", [])
    return []


def run_agent_turn(conversation: list[dict], user_message: str) -> tuple[str, list[dict]]:
    """
    Run one user turn through Claude, executing any tool calls it makes,
    looping until it produces a final text response.

    `conversation` is the running message history (list of role/content
    dicts, Anthropic Messages API format). Returns (assistant_text, updated_conversation).
    """
    client = _client()
    conversation = conversation + [{"role": "user", "content": user_message}]

    # The model has no live clock, so it can't answer "today"/"tomorrow"/
    # "this week" style requests (or fill in a week_start_date for
    # generate_weekly_plan) without being told the actual date each turn.
    today = datetime.date.today()
    system_with_date = (
        f"{SYSTEM_PROMPT}\n\nToday's date is {today.isoformat()} ({today.strftime('%A')}). "
        "Use this to resolve relative dates like \"today\", \"tomorrow\", \"this week\", or "
        "\"next Monday\" yourself — never ask the user what today's date is."
    )

    # Safety cap on tool-calling rounds within a single turn. Without this,
    # a model that keeps calling tools (e.g. retrying a tool that keeps
    # erroring, or looping over a large draft plan) grows `conversation`
    # unboundedly in memory with no way out — that's a real production
    # crash risk (worker OOM/kill), not just a slow response, and it's
    # indistinguishable from a normal error to the browser since the
    # process dies mid-request instead of returning any HTTP response at
    # all. Hard-stopping after a generous but finite number of rounds turns
    # that failure mode into an ordinary, recoverable chat reply instead.
    MAX_TOOL_ROUNDS = 25
    rounds = 0

    while True:
        rounds += 1
        if rounds > MAX_TOOL_ROUNDS:
            logger.warning("run_agent_turn hit MAX_TOOL_ROUNDS (%d) — aborting loop", MAX_TOOL_ROUNDS)
            text = (
                "Sorry, that got stuck in a loop on my end — could you try again, maybe broken "
                "into a couple smaller requests?"
            )
            return text, conversation

        response = client.messages.create(
            model=MODEL,
            # Was 1024 — too tight once tool calls like generate_weekly_plan
            # come back with a full week's worth of meals to summarize; the
            # model would hit max_tokens mid-response (sometimes before
            # writing any text at all), which our stop_reason check below
            # was treating as a normal finish, producing a silently empty
            # reply. Bumped to give real summaries room to breathe.
            max_tokens=4096,
            system=system_with_date,
            tools=TOOL_DEFINITIONS,
            messages=conversation,
        )

        conversation.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            text = "".join(block.text for block in response.content if block.type == "text")
            if not text.strip():
                # Defensive fallback: never silently return a blank bubble to
                # the user, even if a future edge case produces one.
                logger.warning(
                    "run_agent_turn produced an empty reply (stop_reason=%s)", response.stop_reason
                )
                text = (
                    "Sorry, I hit a snag putting that response together — could you try asking "
                    "again, maybe a bit more specifically?"
                )
            return text, conversation

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            fn = TOOL_FUNCTIONS.get(block.name)
            try:
                result = fn(**block.input) if fn else {"error": f"Unknown tool {block.name}"}
                content = json.dumps(result, default=str)
                is_error = False
            except Exception as e:
                content = json.dumps({"error": str(e)})
                is_error = True
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                }
            )

        conversation.append({"role": "user", "content": tool_results})


# A "real" user turn boundary is a {"role": "user"} entry whose content is a
# plain string — that's an actual typed message, appended once per
# run_agent_turn call. Tool-result continuations within a turn also use
# {"role": "user"}, but their content is a list of tool_result blocks, not a
# string, so they're never mistaken for a boundary here.
MAX_CONVERSATION_TURNS = 40


def trim_conversation(history: list[dict], max_turns: int = MAX_CONVERSATION_TURNS) -> list[dict]:
    """
    Cap stored session history to the most recent `max_turns` real user
    turns. A single browser tab's session can run indefinitely (there's no
    logout), so without a cap the conversation — and the full payload sent
    to Claude on every single turn — grows without bound the longer someone
    keeps a session open, which is both a real memory-growth risk and a
    steadily worsening cost/latency one. Only ever cuts at a genuine turn
    boundary (see above), never mid-turn, so a tool_use block is never
    separated from its paired tool_result — the Anthropic API rejects a
    request where those don't line up.
    """
    boundaries = [i for i, m in enumerate(history) if m.get("role") == "user" and isinstance(m.get("content"), str)]
    if len(boundaries) <= max_turns:
        return history
    cut_at = boundaries[-max_turns]
    return history[cut_at:]
