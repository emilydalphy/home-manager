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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seed a single default household so V1 works out of the box
INSERT INTO households (id, name)
SELECT 1, 'My Household'
WHERE NOT EXISTS (SELECT 1 FROM households WHERE id = 1);
