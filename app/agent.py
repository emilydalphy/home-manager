"""
The Claude-powered agent: tool schemas + the tool-use loop.

Uses the plain Anthropic Messages API (client.messages.create) with tool
use. This is intentionally simple/explicit rather than using the full
Agent SDK, since our tool set is small and we want full control over the
loop for a product we may eventually ship.
"""
import os
import json
from anthropic import Anthropic
from . import tools

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
  2. Protein preferences (more/less/neutral on chicken, beef, pork, fish, plant-based, \
eggs), favorite cuisines, and typical cooking time — save with set_household_meal_preferences \
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
rather than list_grocery_list's flat view.
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
        "description": "Save household food preferences: freeform notes, protein preferences (more/less/neutral per protein), favorite cuisines, cooking time preference. Any field can be partial. Marks meal-planning onboarding complete by default.",
        "input_schema": {
            "type": "object",
            "properties": {
                "notes": {"type": "string"},
                "protein_preferences": {
                    "type": "object",
                    "description": "e.g. {\"chicken\": \"more\", \"beef\": \"less\"} — values are 'more'/'less'/'neutral'.",
                },
                "cuisine_preferences": {"type": "array", "items": {"type": "string"}},
                "cooking_time_preference": {"type": "string", "description": "e.g. 'quick', 'moderate', 'no preference'"},
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
                        "properties": {"item": {"type": "string"}, "qty": {"type": "string"}},
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
        "description": "List all saved recipes with ingredients, tags, times_cooked, and any rating/feedback from previous times they were made. Sorted to surface liked recipes first — use this to favor known favorites when suggesting meals.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "mark_recipe_feedback",
        "description": "Record feedback on a saved recipe — 'liked'/'disliked' rating and/or freeform notes. Call the moment the user expresses an opinion about a specific recipe they've made, so future suggestions can favor what they actually liked.",
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
        "description": "Directly set a household meal-preference field to a new value, for corrections. Valid fields: 'notes', 'cooking_time_preference' (both plain strings), 'cuisine_preferences' (list of strings, replaces the whole list), 'protein_preferences' (dict like {\"chicken\": \"more\"}, merged in). Use delete_preference instead to remove a single item without replacing the whole list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": ["notes", "cooking_time_preference", "cuisine_preferences", "protein_preferences"]},
                "value": {"description": "String for notes/cooking_time_preference, array for cuisine_preferences, object for protein_preferences."},
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
                                "properties": {"item": {"type": "string"}, "qty": {"type": "string"}},
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
times_cooked), but don't just repeat them — surface at least one recipe not already in \
saved_recipes this week (a genuinely new suggestion, tailored to this household's \
preferences), so the rotation doesn't shrink to only "safe" meals over time.
- Avoid repeating any meal (or a near-identical variant) that appears in recent_history \
within the last 3 weeks, and avoid repeating the same main_protein or cuisine too many \
days in a row — check recent_history's cuisine/main_protein fields, not just meal names.
- Honor any per-week constraints in constraints_notes exactly (e.g. "out Thursday/Friday" \
means don't plan those days; "under 30 minutes on weeknights" means quick weeknight meals).
- For each day, set is_new_recipe=true and fill in ingredients/tags/food_groups/cuisine/ \
main_protein only if this is a recipe not already in saved_recipes. If you're reusing a \
saved recipe, set is_new_recipe=false and just give its exact meal_name — don't re-invent \
its ingredients.
- cuisine and main_protein should be filled in for every day where reasonably inferable \
(existing or new recipe) — this is what powers future variety checks, so don't leave it \
blank just because the recipe already existed.

Call submit_weekly_plan with the result."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        tools=[_GENERATE_WEEKLY_PLAN_TOOL],
        tool_choice={"type": "tool", "name": "submit_weekly_plan"},
        messages=[{"role": "user", "content": prompt}],
    )
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
        "saved_recipes": tools.list_recipes(),
        "recent_history": tools.get_recent_meal_history(weeks=3),
    }
    days = generate_weekly_plan_llm(context)

    plan = tools.create_weekly_plan(week_start_date, constraints_notes=constraints_notes)
    plan_id = plan["weekly_plan_id"]

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
    "plan_meal": tools.plan_meal,
    "get_meal_plan": tools.get_meal_plan,
    "generate_weekly_plan": generate_weekly_plan,
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
    "mark_grocery_item": tools.mark_grocery_item,
    "remove_grocery_item": tools.remove_grocery_item,
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

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=conversation,
        )

        conversation.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            text = "".join(block.text for block in response.content if block.type == "text")
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
