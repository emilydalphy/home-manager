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

SYSTEM_PROMPT = """You are a helpful, no-nonsense home manager assistant for a household. \
You manage the cleaning/maintenance chore schedule, meal planning, and the grocery list.

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
for each household member (an empty list is a fine answer).
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
- Be concise and practical. This is a household utility, not a chat companion.
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
- Every new generate_weekly_plan call automatically clears 'needed' grocery items left over from \
the previous week's plan before adding this week's, so quantities shouldn't silently stack up \
across many weeks. If the user still notices something oddly large or stale (e.g. "why do we \
need 9 lbs of chicken?"), call clear_stale_grocery_items directly — it's safe, it only removes \
items tied to an old, already-superseded plan, never anything the user added themselves. If \
they explicitly want the whole list wiped and starting over, use clear_grocery_list instead.
- Pantry/fridge inventory (what's actually on hand right now, separate from the grocery list) \
is tracked purely from chat mentions — there's no manual-entry screen, so this only works if \
you call update_inventory proactively, the same way preferences get captured proactively. Any \
time the user mentions buying something ("picked up a rotisserie chicken" -> action="add"), \
using some or all of something ("used the last of the spinach" -> action="use", blank \
quantity), something going bad/getting tossed (action="remove"), or stating what they currently \
have (action="set"), call update_inventory right away, don't wait to be asked. If more than one \
item is mentioned at once — very common the first time someone populates inventory by listing \
out their whole pantry/fridge — use update_inventory_items instead of several individual calls. \
Before adding a \
staple to the grocery list from a direct request (not from a generated weekly plan), check \
get_inventory first — if it looks like they already have enough, ask rather than silently \
adding it ("you've still got flour on hand — still want more, or skip it?").
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
        "description": "Set a household member's dietary restrictions/allergies. Pass an empty list if they have none.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "restrictions": {"type": "array", "items": {"type": "string"}},
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
        "name": "approve_weekly_plan",
        "description": "Mark a weekly plan as approved/reviewed by the Planner.",
        "input_schema": {
            "type": "object",
            "properties": {"weekly_plan_id": {"type": "integer"}},
            "required": ["weekly_plan_id"],
        },
    },
    {
        "name": "get_household_memory",
        "description": "Get a plain summary of everything saved about this household's meal preferences: member dietary restrictions, favorite proteins/cuisines, dislikes, cooking-time preference, notes, goals. Use this when the user asks what the app knows/remembers, or before generating a plan.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "edit_preference",
        "description": "Directly set a household meal-preference field to a new value, for corrections. Valid fields: 'notes', 'cooking_time_preference' (both plain strings), 'cuisine_preferences'/'dislikes' (list of strings, replaces the whole list — prefer add_food_dislikes for adding a single new dislike conversationally), 'protein_preferences' (dict of protein -> how-often, e.g. {\"chicken\": \"several times a week\"}, merged in). Use delete_preference instead to remove a single item without replacing the whole list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": ["notes", "cooking_time_preference", "cuisine_preferences", "protein_preferences", "dislikes"]},
                "value": {"description": "String for notes/cooking_time_preference, array for cuisine_preferences/dislikes, object for protein_preferences."},
            },
            "required": ["field", "value"],
        },
    },
    {
        "name": "delete_preference",
        "description": "Remove a remembered preference. For 'dislikes' or 'cuisine_preferences', pass item = the value to remove. For 'protein_preferences', item = the protein name. For 'notes' or 'cooking_time_preference', omit item to clear the field.",
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": ["dislikes", "cuisine_preferences", "protein_preferences", "notes", "cooking_time_preference"]},
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
            "properties": {"status": {"type": "string", "enum": ["needed", "in_cart", "purchased", "all"]}},
        },
    },
    {
        "name": "get_grocery_list_by_section",
        "description": "Get the grocery list grouped into standard store sections (produce, dairy, meat/seafood, pantry, frozen, other) in shopping order. Prefer this over list_grocery_list whenever showing or reviewing the list with the user, so it reads like something they can actually shop from.",
        "input_schema": {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["needed", "in_cart", "purchased", "all"]}},
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
        "name": "remove_grocery_item",
        "description": "Delete an item from the grocery list, given its item_id.",
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
                    "description": "Each entry: a plain string (uses the shared action), or an object with item/action/quantity/expiration_date to mix actions within one call.",
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


def generate_weekly_plan(week_start_date: str, constraints_notes: str = "", day_count: int = 7) -> dict:
    """
    Generate and save a full week's meal plan in one pass: gathers current
    household memory, saved recipes, and recent meal history, asks Claude
    for a complete week via a forced tool call, then persists it as a new
    weekly_plan with each day's meal attached — creating new recipes as
    needed. Returns the saved plan via get_weekly_plan. This is the
    preferred way to handle "plan my week" / "what should we eat this
    week" style requests instead of planning meal-by-meal across several
    plan_meal calls.
    """
    context = {
        "week_start_date": week_start_date,
        "day_count": day_count,
        "constraints_notes": constraints_notes,
        "household_memory": tools.get_household_memory(),
        # Temporarily-excluded recipes (flag_recipe_temporary) are filtered out
        # here at the source rather than relying on a prompt instruction, so
        # they're never even a candidate for suggestion.
        "saved_recipes": tools.list_recipes(include_temporarily_excluded=False),
        "recent_history": tools.get_recent_meal_history(weeks=3),
        "current_inventory": tools.get_inventory(),
    }
    days = generate_weekly_plan_llm(context)

    plan = tools.create_weekly_plan(week_start_date, constraints_notes=constraints_notes)
    plan_id = plan["weekly_plan_id"]

    # Clears out any 'needed' grocery items still sourced from the PREVIOUS
    # plan before this week's ingredients get added below — otherwise
    # quantities from old, already-superseded weeks silently keep stacking
    # onto the same line forever. Passing plan_id explicitly rather than
    # letting it re-derive "current" avoids any ambiguity if two plans get
    # created within the same second.
    tools.clear_stale_grocery_items(current_weekly_plan_id=plan_id)

    for day in days:
        meal_name = day.get("meal_name")
        if not meal_name:
            continue
        if day.get("is_new_recipe") and day.get("ingredients"):
            existing = next((r for r in tools.list_recipes() if r["name"] == meal_name), None)
            if not existing:
                tools.add_recipe(
                    name=meal_name,
                    ingredients=day.get("ingredients", []),
                    tags=day.get("tags", []),
                    food_groups=day.get("food_groups", []),
                    cuisine=day.get("cuisine", ""),
                    main_protein=day.get("main_protein", ""),
                )
        tools.plan_meal(
            meal_date=day.get("date"),
            meal=meal_name,
            slot=day.get("slot", "dinner"),
            food_groups=day.get("food_groups"),
            weekly_plan_id=plan_id,
        )

    return tools.get_weekly_plan(plan_id)


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
    "mark_recipe_feedback": tools.mark_recipe_feedback,
    "log_recipe_note": tools.log_recipe_note,
    "flag_recipe_temporary": tools.flag_recipe_temporary,
    "plan_meal": tools.plan_meal,
    "get_meal_plan": tools.get_meal_plan,
    "generate_weekly_plan": generate_weekly_plan,
    "set_week_constraints": tools.set_week_constraints,
    "get_weekly_plan": tools.get_weekly_plan,
    "swap_meal_in_plan": tools.swap_meal_in_plan,
    "approve_weekly_plan": tools.approve_weekly_plan,
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
    "remove_grocery_item": tools.remove_grocery_item,
    "update_inventory": tools.update_inventory,
    "update_inventory_items": tools.update_inventory_items,
    "get_inventory": tools.get_inventory,
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

    while True:
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
