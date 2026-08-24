-- Home Manager schema
-- Every table carries household_id so this can go multi-tenant later
-- without a data model rewrite. For V1 there's just one household (id=1).

CREATE TABLE IF NOT EXISTS households (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    goals TEXT NOT NULL DEFAULT '', -- freeform, e.g. "stay on top of chores, eat healthier"
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    name TEXT NOT NULL,
    age_group TEXT NOT NULL DEFAULT '', -- freeform, e.g. "adult", "teen", "child", "toddler"
    dietary_restrictions_json TEXT NOT NULL DEFAULT '[]', -- e.g. ["vegetarian", "peanut allergy"]
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    name TEXT NOT NULL,
    pet_type TEXT NOT NULL DEFAULT '', -- freeform, e.g. "dog", "cat", "rabbit"
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Household-level meal preferences. One row per household.
CREATE TABLE IF NOT EXISTS meal_preferences (
    household_id INTEGER PRIMARY KEY REFERENCES households(id),
    notes TEXT NOT NULL DEFAULT '', -- freeform "let me type it" catch-all
    protein_preferences_json TEXT NOT NULL DEFAULT '{}', -- {"chicken": "more", "beef": "less", ...}
    cuisine_preferences_json TEXT NOT NULL DEFAULT '[]', -- ["Italian", "Mexican", ...]
    dislikes_json TEXT NOT NULL DEFAULT '[]', -- ingredients/foods to avoid, e.g. ["peppers", "mushrooms"] — not allergies, just preference
    cooking_time_preference TEXT NOT NULL DEFAULT '', -- e.g. "quick", "moderate", "no preference"
    -- How often new (not-yet-saved) recipes should get surfaced when generating a
    -- weekly plan: 'mostly_favorites' | 'balanced' | 'surprise_me_often'. Has a
    -- floor even at the lowest setting — see generate_weekly_plan_llm's prompt —
    -- so freshness never drops to zero just because the household leans safe.
    novelty_preference TEXT NOT NULL DEFAULT 'balanced',
    -- Household-level, not per-week: 'day_based' (default) assigns one meal per
    -- day/slot, same as Phase 2. 'component_based' plans by category instead
    -- (a breakfast for the week, several proteins, several vegetables, carbs,
    -- a treat, a dip) for the household to assemble freely rather than a fixed
    -- day->meal mapping. A household is one or the other, never mixed within a
    -- single week, but can switch any time via set_planning_mode.
    planning_mode TEXT NOT NULL DEFAULT 'day_based',
    -- Onboarding redesign: free text, not a preset list on purpose (e.g. "keto",
    -- "high-protein, low-carb") — a specific eating style/goal for meals to
    -- follow, distinct from hard dietary_restrictions on the members table.
    -- Household-level per the PRD's default lean, since there's no per-person
    -- UI surface for this and no clear case yet for splitting it by member.
    eating_style TEXT NOT NULL DEFAULT '',
    -- Onboarding redesign: how many dinners a typical week should actually
    -- plan (1-7). Lets a household say "we're only home for dinner 4 nights"
    -- instead of always getting all 7 filled in. Defaults to 7 (every night)
    -- for pre-redesign households that never answered this.
    dinners_per_week INTEGER NOT NULL DEFAULT 7,
    onboarding_complete INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Household-level chores context collected during onboarding — home type,
-- size, standard, etc. Saved as reference even before any chores exist, so
-- chat-based chore setup (or a future recommendation step) doesn't have to
-- re-ask. One row per household.
CREATE TABLE IF NOT EXISTS chores_profile (
    household_id INTEGER PRIMARY KEY REFERENCES households(id),
    home_type TEXT NOT NULL DEFAULT '',
    bedrooms INTEGER NOT NULL DEFAULT 0,
    bathrooms INTEGER NOT NULL DEFAULT 0,
    has_yard INTEGER NOT NULL DEFAULT 0,
    standard TEXT NOT NULL DEFAULT '', -- relaxed | standard | meticulous
    rotation_members_json TEXT NOT NULL DEFAULT '[]',
    existing_help TEXT NOT NULL DEFAULT '',
    existing_help_frequency TEXT NOT NULL DEFAULT '',
    include_notes TEXT NOT NULL DEFAULT '',
    exclude_notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A chore definition (e.g. "Take out trash", recurs weekly)
CREATE TABLE IF NOT EXISTS chores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'cleaning', -- cleaning | maintenance | other
    frequency TEXT NOT NULL DEFAULT 'weekly', -- daily | weekly | biweekly | monthly | quarterly | once
    default_assignee_id INTEGER REFERENCES members(id),
    rotation_member_ids_json TEXT NOT NULL DEFAULT '[]', -- member ids to round-robin through; overrides default_assignee_id if non-empty
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A specific occurrence of a chore that needs doing on/around a date
CREATE TABLE IF NOT EXISTS chore_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    chore_id INTEGER NOT NULL REFERENCES chores(id),
    assignee_id INTEGER REFERENCES members(id),
    due_date TEXT NOT NULL, -- ISO date
    status TEXT NOT NULL DEFAULT 'pending', -- pending | done | skipped
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    name TEXT NOT NULL,
    notes TEXT,
    ingredients_json TEXT NOT NULL DEFAULT '[]', -- [{"item": "chicken breast", "qty": "1 lb"}]
    tags_json TEXT NOT NULL DEFAULT '[]', -- e.g. ["vegetarian", "quick", "kid-friendly"]
    food_groups_json TEXT NOT NULL DEFAULT '[]', -- subset of ["protein", "carb", "vegetable"]
    times_cooked INTEGER NOT NULL DEFAULT 0,
    last_cooked_date TEXT,
    rating TEXT NOT NULL DEFAULT '', -- '' | 'liked' | 'disliked' — feedback after actually making it
    feedback_notes TEXT NOT NULL DEFAULT '', -- freeform, e.g. "loved the sauce, a bit too spicy for the kids"
    cuisine TEXT NOT NULL DEFAULT '', -- freeform, e.g. "Italian", "Mexican" — used for plan variety checks
    main_protein TEXT NOT NULL DEFAULT '', -- freeform, e.g. "chicken", "beef", "vegetarian" — used for plan variety checks
    -- Temporarily excluded from auto-suggestion rotation (e.g. "we're sick of this
    -- for now") — distinct from rating='disliked', which is a permanent pattern.
    -- Manually toggled on/off via flag_recipe_temporary; no auto-expiry.
    temporarily_excluded INTEGER NOT NULL DEFAULT 0,
    -- Cooker execution layer (Phase 3): full recipe detail beyond just
    -- ingredients, so a recipe is actually cookable from within the app.
    instructions_json TEXT NOT NULL DEFAULT '[]', -- ordered list of step strings
    default_servings INTEGER NOT NULL DEFAULT 4, -- baseline for scale_recipe
    prep_time_minutes INTEGER, -- active prep time, if known
    cook_time_minutes INTEGER, -- active cook time, if known
    advance_prep_notes TEXT NOT NULL DEFAULT '', -- e.g. "marinate at least 4 hours ahead, can be done the night before" — feeds generate_prep_schedule
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One-off notes tied to a specific recipe, distinct from the recipe's
-- permanent rating (recipes.rating/feedback_notes, set via
-- mark_recipe_feedback). note_type='feedback' is a single occurrence's
-- comment ("wasn't great with this cut of meat") that shouldn't by itself
-- blacklist the recipe the way a 'disliked' rating does — it's a soft
-- signal, weighed alongside the recipe's actual rating, not a replacement
-- for it. note_type='deviation' is reserved for the Cooker execution layer
-- (what actually changed while cooking) so both share the same store.
CREATE TABLE IF NOT EXISTS recipe_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    recipe_id INTEGER NOT NULL REFERENCES recipes(id),
    note_type TEXT NOT NULL DEFAULT 'feedback', -- 'feedback' | 'deviation'
    note TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A single-pass generated week of meals, reviewable/editable as one artifact
-- rather than living implicitly in chat history. The Eater share link (later
-- phase) always points at whichever plan is most recent for the household.
CREATE TABLE IF NOT EXISTS weekly_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    week_start_date TEXT NOT NULL, -- ISO date, the Monday (or first planned day) of the week
    status TEXT NOT NULL DEFAULT 'draft', -- draft | approved
    constraints_notes TEXT NOT NULL DEFAULT '', -- freeform per-week asks, e.g. "out Thu/Fri, keep it under 30 min"
    -- Snapshotted from meal_preferences.planning_mode at creation time, so a
    -- past plan stays interpretable even if the household later switches
    -- modes — see meal_plan_entries.component_category.
    planning_mode TEXT NOT NULL DEFAULT 'day_based',
    -- Set once, explicitly, at creation (see create_weekly_plan) — true only
    -- for the very first plan a household ever generates. Never re-derived
    -- later (e.g. "earliest plan row"), so it stays correct through
    -- backfills, edits, or re-onboarding. Powers the first-run intro banner.
    is_first_plan INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A meal scheduled for a specific date/slot (breakfast/lunch/dinner)
CREATE TABLE IF NOT EXISTS meal_plan_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    date TEXT NOT NULL, -- ISO date
    slot TEXT NOT NULL DEFAULT 'dinner', -- breakfast | lunch | dinner
    recipe_id INTEGER REFERENCES recipes(id),
    freeform_meal TEXT, -- used if not tied to a saved recipe
    food_groups_json TEXT NOT NULL DEFAULT '[]', -- subset of ["protein", "carb", "vegetable"] covered by this plate
    weekly_plan_id INTEGER REFERENCES weekly_plans(id), -- null for ad hoc/one-off meals not part of a generated week
    -- Set only for entries belonging to a component_based plan — e.g.
    -- "breakfast", "protein", "vegetable", "carb", "treat", "dip". NULL means
    -- a normal day-based entry (date/slot are what matters). When set, `date`
    -- is just the plan's week_start_date as a placeholder (this item isn't
    -- tied to a specific day) and `slot` is unused.
    component_category TEXT,
    -- Cooker execution layer: has this specific planned meal actually been
    -- cooked yet? Separate from recipes.times_cooked (a lifetime counter) —
    -- this is per-entry, so the week's progress view can show done vs.
    -- outstanding. See check_off_meal/get_plan_progress.
    cooked_status TEXT NOT NULL DEFAULT 'pending', -- pending | done
    -- When cooked_status was last set to 'done' — powers the feedback nudge
    -- (get_feedback_nudge): a meal that's been cooked but whose recipe still
    -- has no rating is worth gently asking about, but only once, and only
    -- for something recently made.
    cooked_at TEXT,
    -- Phase 6: a short "why this?" rationale, generated and persisted at
    -- plan-generation time (not computed on demand — the model already has
    -- the relevant preferences/history/constraints in context right then,
    -- so this costs a little extra generation output instead of a live
    -- round-trip every time someone taps "why this?"). Blank for meals
    -- planned before this was tracked, or added one-off via plan_meal
    -- without a reasoning argument.
    reasoning TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A generated prep/cooking task for a weekly plan — "what needs prepping or
-- starting ahead of time and when," derived purely from recipe timing (see
-- generate_prep_schedule), not the Cooker's calendar/availability.
CREATE TABLE IF NOT EXISTS prep_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    weekly_plan_id INTEGER NOT NULL REFERENCES weekly_plans(id),
    task_date TEXT NOT NULL, -- ISO date this task should happen on
    description TEXT NOT NULL, -- e.g. "Marinate chicken for Wednesday's stir fry"
    related_meal TEXT NOT NULL DEFAULT '', -- freeform meal name this task supports
    status TEXT NOT NULL DEFAULT 'pending', -- pending | done
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Phase 4, §4.4: a "needs your attention" queue item — currently used for
-- low-confidence inventory-depletion matches from check_off_meal (an
-- ambiguous ingredient-to-inventory name match, or a name match whose
-- quantity couldn't be reconciled), so nothing gets silently dropped or
-- guessed at. Designed to be reusable for other kinds of soft nudges later
-- (kind is a free string, detail_json holds whatever's specific to that
-- kind) rather than a table scoped to just this one feature.
CREATE TABLE IF NOT EXISTS attention_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    kind TEXT NOT NULL, -- e.g. 'inventory_depletion'
    summary TEXT NOT NULL, -- human-readable one-liner
    detail_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending', -- pending | resolved | dismissed
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);

-- Read-only tokenized link for sharing the current weekly plan with someone
-- outside the household (the "Eater" persona from the PRD) — no login, no
-- new auth model. The token is stable and always resolves to whichever
-- weekly_plan is most recent, so it never needs to be regenerated as new
-- plans get created week to week; the same link just stays live.
CREATE TABLE IF NOT EXISTS share_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Phase 4, §4.1: Eater self-service — a personal, tokenized link tied to a
-- single members row (unlike share_links above, which is household-wide and
-- read-only). Write access through this token is scoped narrowly to that
-- member's own dietary_restrictions and freeform notes (member_notes below)
-- — nothing else. Standing by default (doesn't expire), but the Planner can
-- revoke it (revoked=1) and generate a fresh one; resolve_member_share_link
-- only honors a non-revoked row, so an old leaked link stops working the
-- moment it's revoked without needing to delete history.
CREATE TABLE IF NOT EXISTS member_share_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    member_id INTEGER NOT NULL REFERENCES members(id),
    token TEXT NOT NULL UNIQUE,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Freeform preference/feedback notes an Eater leaves via their own
-- self-service link — distinct from recipe_notes (which is tied to a
-- specific recipe); this is a general note attributed to a person, e.g.
-- "I'd love more vegetarian nights" or "loved the tacos, more of that."
CREATE TABLE IF NOT EXISTS member_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    member_id INTEGER NOT NULL REFERENCES members(id),
    note TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS grocery_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    item TEXT NOT NULL,
    quantity TEXT,
    category TEXT DEFAULT 'other', -- produce | dairy | meat | pantry | household | other
    added_by TEXT DEFAULT 'ai', -- 'ai' if auto-added from meal plan, else member name
    status TEXT NOT NULL DEFAULT 'needed', -- needed | in_cart | purchased
    -- Which generated weekly_plan this item's ingredients came from, if any.
    -- NULL means it's a standing item (added directly by a person, or from
    -- an ad hoc one-off meal) and should never be auto-cleared. Lets
    -- clear_stale_grocery_items() remove leftover quantities from an old
    -- week's plan once a new week has been generated, instead of them
    -- silently stacking onto the same line forever.
    source_weekly_plan_id INTEGER REFERENCES weekly_plans(id),
    -- Which store this item should be bought at, if the household shops
    -- across more than one (e.g. Costco for bulk pantry, a regular grocery
    -- store for everything else). '' means unassigned/default. Populated
    -- automatically from item_store_preferences when set — see set_item_store.
    store TEXT NOT NULL DEFAULT '',
    -- Phase 4, §4.5: hide this item from the normal shown/shopped list
    -- without deleting it — for something the Shopper will get elsewhere
    -- (a butcher, a farmers market) rather than on the regular trip. Stays
    -- in this table with status unchanged, so it still counts as the same
    -- tracked line for meal-plan ingredient consolidation (a future
    -- add_grocery_item call for the same item merges into this line rather
    -- than creating a duplicate) — only its visibility in the default list
    -- changes. See exclude_grocery_item/include_grocery_item.
    excluded_from_list INTEGER NOT NULL DEFAULT 0,
    -- Cross-referenced against tracked inventory and flagged as "you may
    -- already have this" on the Grocery List view's review section (see
    -- get_grocery_already_have_items). 0 means still flagged/unreviewed
    -- (pulled out of the normal To-buy list into the review section); 1
    -- means the shopper confirmed they still need it, so it shows normally
    -- in To-buy again despite the inventory match. Never affects removal —
    -- confirming "doesn't need to be on the list" just deletes the row.
    already_have_reviewed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Remembered default store per item name (Phase 3, household coordination) —
-- so once someone says "we get paper towels at Costco," every future add of
-- that item is pre-assigned there instead of asking again. One row per
-- item name per household.
CREATE TABLE IF NOT EXISTS item_store_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    item TEXT NOT NULL,
    store TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(household_id, item)
);

-- Real tracked pantry/fridge inventory (Phase 3), distinct from the grocery
-- list — this is "what we currently have", captured primarily via chat
-- mention ("picked up a rotisserie chicken", "used the last of the
-- spinach"), not a manual-entry form. Also populated automatically when a
-- grocery item is checked off as purchased (source='grocery_checkoff').
CREATE TABLE IF NOT EXISTS inventory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    item TEXT NOT NULL,
    quantity TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'chat', -- 'chat' | 'grocery_checkoff'
    expiration_date TEXT, -- ISO date, left unset unless explicitly known — nothing reads this yet (Phase 4)
    -- Store section, same taxonomy as grocery_items.category (produce, dairy,
    -- meat/seafood, pantry, frozen, other) so the Inventory view can group
    -- items the same way the grocery list does. Inherited automatically when
    -- an item is checked off the grocery list into inventory; set directly
    -- by the assistant otherwise.
    category TEXT NOT NULL DEFAULT 'other',
    -- Storage location: 'fridge' | 'freezer' | 'pantry'. Independent from
    -- category — a sauce is category='pantry' by food type but often moves
    -- to the fridge once opened, so this is tracked as its own field
    -- rather than derived from category alone. Defaults to a category-based
    -- guess when not stated explicitly (see _DEFAULT_LOCATION_BY_CATEGORY).
    location TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Phase 6: append-only log of preference writes (create/update/delete) —
-- not a value store, meal_preferences/members already hold current state.
-- Just a timestamped record that a write happened, so the Memory view's
-- growth counter ("You've taught me N things this month") can count real
-- activity within a window instead of being a made-up number. Every write
-- counts, including corrections to the same field — no deduping, per Phase
-- 6 PRD §6 decision. Only logged from the specific correction/addition
-- entry points a person or the assistant actually uses after initial setup
-- (edit_preference, delete_preference, add_food_dislikes, etc.) — not from
-- onboarding's bulk initial save, so getting through onboarding doesn't
-- inflate "this month" on day one.
CREATE TABLE IF NOT EXISTS preference_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    field TEXT NOT NULL, -- e.g. 'dislikes', 'protein_preferences', 'member:Jamie:dietary_restrictions'
    action TEXT NOT NULL, -- 'write' (create or update) | 'delete'
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seed a single default household so V1 works out of the box
INSERT INTO households (id, name)
SELECT 1, 'My Household'
WHERE NOT EXISTS (SELECT 1 FROM households WHERE id = 1);
