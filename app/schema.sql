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
    -- An adult's avatar color for the "who added it" avatars on desktop
    -- grocery rows (the People token: first adult spruce #1B3328, second
    -- deep apricot #C4703C, repainted for Pomona 2026-09-02 — these were
    -- plum #66304E and leaf green #4D8A33 under the retired palette).
    -- Blank until backfilled — see db._backfill_member_colors, run once
    -- when this column is first added.
    color TEXT NOT NULL DEFAULT '',
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
    -- Same idea as dinners_per_week, for the other two main meals.
    breakfasts_per_week INTEGER NOT NULL DEFAULT 7,
    lunches_per_week INTEGER NOT NULL DEFAULT 7,
    onboarding_complete INTEGER NOT NULL DEFAULT 0,
    -- design_handoff_plan_the_week. The settings the revisitable setup
    -- screen owns and the two onboarding steps collect. They are separate
    -- columns rather than notes text because generation reads them as
    -- constraints, and because week_intake.preferences_snapshot_json has to
    -- capture them as structured values.
    --
    -- What the household has to cook with. The highest-value question the
    -- app wasn't asking: it prevents impossible suggestions outright.
    kitchen_kit_json TEXT NOT NULL DEFAULT '[]', -- ["slow_cooker", "air_fryer"]
    -- How they feel about eating the same thing twice. This single answer
    -- changes the structure of every week the app builds — whether it cooks
    -- once and stretches it, or gives seven different dinners.
    repeats_tolerance TEXT NOT NULL DEFAULT '', -- cook_once_eat_twice | one_a_week | all_different
    -- A real number of minutes, distinct from cooking_time_preference's
    -- freeform "quick"/"moderate". 0 means unset — no cap.
    weeknight_max_minutes INTEGER NOT NULL DEFAULT 0,
    -- Whether everyone eats the same thing, or plates differ.
    table_style TEXT NOT NULL DEFAULT '', -- everyone_same | kids_differ | plate_your_own
    -- Onboarding step A, both free text and both skippable. Kept verbatim:
    -- "a week I understand needs almost no correcting later."
    typical_week TEXT NOT NULL DEFAULT '',
    next_week_notes TEXT NOT NULL DEFAULT '',
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

-- design_handoff_plan_the_week/DATA_MODEL.md: the ANSWERS a week was built
-- from, as a first-class object rather than as chat history. Three things
-- become impossible to add later if this isn't stored with provenance:
-- "Try again" (regenerate from the same answers), "why did it plan that?",
-- and any learning from what the household actually cooked.
--
-- APPEND-ONLY. A row is never updated in place. Redo, or any chat
-- instruction that changes an *answer* ("cut it to four dinners", "actually
-- Wednesday should be leftovers"), copies the current revision, applies the
-- change, and inserts it as revision+1, stamping superseded_at on the old
-- one. The current intake for a week is the highest revision with
-- superseded_at IS NULL. The trap this closes: if chat edited only the plan
-- and not the intake, regenerating would silently revert everything the
-- household just said in chat.
--
-- Rule of thumb for whether something is a new revision: would this answer
-- have changed if they'd said it during the questions? Instructions that
-- only affect one slot ("swap Thursday") change the plan, not the intake.
CREATE TABLE IF NOT EXISTS week_intake (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    week_start TEXT NOT NULL,       -- ISO date, the Monday of the target week
    revision INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL DEFAULT '', -- adult's name, same convention as weekly_plans.approved_by
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    superseded_at TEXT,             -- null while this is the current revision

    -- Q1. Keyed by ISO DATE, never by weekday ("2026-09-02", not "Tue") —
    -- weekday keys break the moment you plan two weeks, look at history, or
    -- cross a month boundary.
    night_tags_json TEXT NOT NULL DEFAULT '{}',        -- {"2026-09-02": ["rush"]}
    -- Stores the EXTRAS the guest steppers collect; the base household is in
    -- household_snapshot. Portions need the total, and household composition
    -- changes (a child ages, someone moves in), so the total has to be
    -- reconstructible from what was true that week rather than from today's
    -- What We Know.
    guest_counts_json TEXT NOT NULL DEFAULT '{}',      -- {"2026-09-06": {"adults": 2, "children": 0}}
    packed_lunch_days_json TEXT NOT NULL DEFAULT '[]', -- ["2026-09-01"]

    -- Q2.
    moods_json TEXT NOT NULL DEFAULT '[]',
    cuisines_json TEXT NOT NULL DEFAULT '[]',
    -- Stored verbatim and never parsed destructively. Whatever the model
    -- extracts from it goes in the slots' derived_from; the household's own
    -- words survive.
    freeform TEXT NOT NULL DEFAULT '',

    household_snapshot_json TEXT NOT NULL DEFAULT '{}', -- {"adults": 2, "children": 2}
    -- A COPY, not a reference. This is the one people skip and regret: if a
    -- plan only points at live preferences, then the day someone edits
    -- "won't eat", every past plan's reasoning becomes unreadable and
    -- unreproducible — you can no longer tell whether a strange choice was a
    -- bug or a preference that has since changed.
    preferences_snapshot_json TEXT NOT NULL DEFAULT '{}'
);

-- UNIQUE, not just an index. save_week_intake reads the current revision,
-- then supersedes it and inserts revision+1 — three statements with no lock
-- between them. Two adults saving at the same moment (the exact case
-- DATA_MODEL.md says will actually happen, on a Sunday evening) both read
-- revision 1 and both write revision 2, leaving two live rows and silently
-- losing one adult's answers. This constraint turns that into a failed
-- insert, which save_week_intake retries — a lost answer becomes a slower
-- save instead of a wrong week.
CREATE UNIQUE INDEX IF NOT EXISTS idx_week_intake_revision
    ON week_intake (household_id, week_start, revision);

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
    -- design_handoff_plan_the_week: who said yes to this week, and when.
    -- Approval is what builds the grocery list (see approve_weekly_plan),
    -- so these two are the receipt the Meals screen renders — "APPROVED BY
    -- EMILY · 9:41AM" — and the record of which adult carried it. Stored as
    -- a NAME, not a members.id FK, to match how the rest of this app
    -- attributes adult actions (grocery_items.added_by, removed_by): there
    -- is no per-person login, just the lightweight adult picker from
    -- get_household_people, so a name is the only identity that actually
    -- exists at this layer. Blank/null until approved.
    approved_by TEXT NOT NULL DEFAULT '',
    approved_at TEXT,
    -- What that approval actually did to the shopping list, captured at the
    -- moment it happened. The receipt ("I've put 22 items on your shopping
    -- list — 6 were already in your kitchen, so I left those off") has to
    -- survive a page reload, and neither number is recoverable afterwards:
    -- the added count blurs as the household edits the list, and nothing
    -- anywhere records which ingredients were skipped for already being in
    -- the kitchen. Two integers is cheaper than a receipt that quietly
    -- starts lying a day later.
    approved_grocery_added INTEGER NOT NULL DEFAULT 0,
    approved_grocery_skipped INTEGER NOT NULL DEFAULT 0,
    -- Which week_intake revision this plan was generated from, so "the week
    -- you had before you redid it" stays recoverable and "why did it plan
    -- that?" stays answerable. Null for plans made before the intake flow
    -- existed, and for anything planned meal-by-meal outside it.
    intake_id INTEGER REFERENCES week_intake(id),
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
    -- design_handoff_plan_the_week/DATA_MODEL.md → Open slots. A slot is
    -- NEVER absent. It is exactly one of three states, and slot_state is
    -- what says which, so "planned nothing" and "forgot to plan" can't be
    -- confused for each other — a null-null slot is the silent-empty-slot
    -- bug in stored form.
    --   'planned'       recipe_id or freeform_meal is set. The normal case.
    --   'planned_empty' nobody is home. Needs no decision and must NEVER be
    --                   offered to the household as one — this is the only
    --                   deliberately empty slot in a week.
    --   'open'          open_reason holds a full sentence naming the
    --                   CONSTRAINT that caused it, so the ask reads as
    --                   diligence rather than failure.
    -- Enforced in code (see tools._validate_slot_state) rather than as a
    -- table CHECK: this column arrives by ALTER TABLE on existing
    -- databases, and SQLite cannot add a table-level constraint that way.
    slot_state TEXT NOT NULL DEFAULT 'planned',
    open_reason TEXT NOT NULL DEFAULT '',
    -- Which inputs produced this slot. Nearly free to record at generation
    -- time and impossible to backfill. Three payoffs, in the order you'll
    -- want them: the per-slot "why" line is generated from this rather than
    -- improvised (so it can't contradict the actual reason); a wrong plan
    -- can be traced to the input that caused it — a bad tag, a stale
    -- preference, or the model; and later, cross-referencing against what
    -- was actually cooked ("every rush night you didn't cook" is a finding
    -- you can only get if the tags are attached to the slots).
    --   {"tags": ["rush"], "constraint": "max_minutes:20",
    --    "inputs": ["cuisines:thai", "mood:comfort_food"],
    --    "freeform": "I want to use the lamb in the freezer",
    --    "inventory": ["lamb_shoulder"], "links_to": "entry_id:8842"}
    derived_from_json TEXT NOT NULL DEFAULT '{}',
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
    status TEXT NOT NULL DEFAULT 'needed', -- needed | in_cart | purchased | removed (soft, see removed_by)
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
    -- Which household member made a pre-shop "Drop it" decision
    -- (PRE_SHOP_CHECK.md) — blank until dropped. Not yet used to drive a
    -- live cross-device notification (see get_pre_shop_flags/
    -- drop_grocery_item_pre_shop): NOTIFICATIONS.md #4 documents that this
    -- codebase has no concept of "the other adult" distinct from "you" at
    -- the data layer, and that gap applies here too.
    removed_by TEXT NOT NULL DEFAULT '',
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

-- Per-meal ledger of exactly which grocery_items line (and how much of it)
-- a given meal_plan_entries row contributed, recorded at plan_meal() time
-- whenever a recipe's ingredients get auto-added to the grocery list. This
-- is what makes swap_meal_in_plan/swap_component_in_plan able to *remove*
-- the old meal's ingredients precisely instead of only ever adding the new
-- meal's on top — without it there's no way to tell "half of this 3 lbs
-- chicken breast line came from the meal being replaced" apart from "all of
-- it did," since same-name ingredients from different meals consolidate
-- onto one grocery_items row. See _reverse_meal_grocery_contributions.
-- ON DELETE CASCADE on both foreign keys: meal_plan_entries and
-- grocery_items rows both get hard-deleted elsewhere in the app (a swap,
-- consolidate_grocery_list, clear_stale_grocery_items, remove_grocery_item,
-- ...) independently of this ledger, and PRAGMA foreign_keys=ON (see
-- db.get_conn) would otherwise block those deletes once a link row points
-- at them. A cascaded link row is just a stale ledger entry with nothing
-- left to reverse — harmless.
CREATE TABLE IF NOT EXISTS meal_plan_grocery_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    meal_plan_entry_id INTEGER NOT NULL REFERENCES meal_plan_entries(id) ON DELETE CASCADE,
    grocery_item_id INTEGER NOT NULL REFERENCES grocery_items(id) ON DELETE CASCADE,
    item TEXT NOT NULL, -- item name at the time of add, for logging/debugging
    quantity TEXT NOT NULL DEFAULT '', -- exactly what THIS meal contributed, pre-merge
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

-- design_handoff_home_manager Phase 2: real metadata for a store the
-- household shops at — habit ("every other Saturday"), role ("bulk" /
-- "fresh"), and a per-store aisle order (Phase 4's "what you get where"
-- Stores tab will let this be edited; for now it just carries the default
-- fixed order so the row exists to extend later). Keyed by NAME, not id —
-- grocery_items.store and item_store_preferences.store are both already
-- free-text store names throughout the app; adding a real stores.id FK
-- everywhere would be a much larger, riskier migration for no Phase-2
-- payoff, so this table hangs off the same name convention instead. A
-- store only gets a row here once something explicitly sets its habit/
-- role/aisle_order (see tools.get_stores, which fills in defaults for any
-- store name it finds on the grocery list with no row here yet).
CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    name TEXT NOT NULL,
    habit TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    aisle_order_json TEXT NOT NULL DEFAULT '["Produce","Bakery","Dairy","Meat","Frozen","Pantry","Household"]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(household_id, name)
);

-- design_handoff_home_manager Phase 3: closed when "Done shopping"/"Next
-- store" wraps up a stop in desktop Shopping mode (option 5g). Deliberately
-- minimal — no itemIds column, since nothing in this phase's UI reads trip
-- history back yet (no past-trips list is spec'd). item_count is captured
-- at close time for a future "N items" summary without re-deriving it from
-- grocery_items (whose statuses keep changing after the trip closes).
-- Per-item inventory promotion already happens at checkoff time (marking a
-- grocery item purchased calls _add_to_inventory), so closing a trip here
-- is bookkeeping, not another promotion pass.
CREATE TABLE IF NOT EXISTS shopping_trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    store TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- design_handoff_home_manager Phase 4: freeform household facts for the
-- "What we know" screen's People/Taste/Rhythm tabs (option 5d) — add any
-- note, edit it inline, delete it. Deliberately a SEPARATE layer from the
-- existing structured preference fields (members.dietary_restrictions_json,
-- meal_preferences' cuisine/protein/dislikes/cooking_time/etc.), which
-- already drive meal-plan generation and stay exactly as they were,
-- editable via chat as before. Forcing those disparate fixed-shape fields
-- (a dict, a few single-value settings, per-member lists) into one uniform
-- add/edit/delete freeform list would have meant either a risky rewrite of
-- generation logic or an awkward half-mapping — see README's "Kitchen —
-- What we know" Phase 4 notes. The Stores tab does NOT use this table; it
-- already had a natural fit in usual_stores_json/store_typical_items_json
-- (meal_preferences) and keeps using those.
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    category TEXT NOT NULL, -- 'people' | 'taste' | 'rhythm'
    text TEXT NOT NULL,
    hard INTEGER NOT NULL DEFAULT 0, -- true for allergy-type facts, per DATA_AND_API.md's Fact.hard
    author TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Loop Board: "Week planning: away-stretches and per-meal needs". One
-- gesture in the intake ("away Sat lunch through Sun lunch") — see
-- away_stretches below — sets a whole range of these; the per-meal
-- override layer (progressive disclosure, per Emily's decision
-- 2026-09-03) sets one at a time. Deliberately a SIBLING table to
-- meal_plan_entries, not a column on it: a need can (and normally does)
-- exist before any meal_plan_entries row does — it's declared at intake
-- time, same as week_intake.night_tags today, and generation reads it
-- when building the week. One row per (date, slot); a slot with no row
-- here is implicitly 'normal'.
CREATE TABLE IF NOT EXISTS slot_needs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    date TEXT NOT NULL, -- ISO date
    slot TEXT NOT NULL, -- breakfast | lunch | dinner | snack
    -- 'normal'      no special need — the ordinary case, and never actually
    --               stored (see slot_needs.clear_slot_need); listed here
    --               only as the implicit default a missing row means.
    -- 'away'        extends planned_empty to any slot, not just dinner:
    --               nobody is home to eat it. No planning, no groceries —
    --               same invariant as meal_plan_entries.slot_state =
    --               'planned_empty', enforced the same non-negotiable way.
    -- 'quick'       the last real meal before an away stretch begins —
    --               grab-and-go, not a sit-down plan.
    -- 'ready_made'  the first real meal after an away stretch ends — covered
    --               by a batch-cooked earmark or a freezer defrost rather
    --               than fresh cooking; see the recommendation columns below.
    need TEXT NOT NULL DEFAULT 'normal',
    reason TEXT NOT NULL DEFAULT '',
    -- Which away_stretches row (if any) produced this need via range
    -- derivation, so undoing/editing a trip can find every slot it touched
    -- — including the two derived edges, which don't fall inside the
    -- stretch's own from/to range. NULL for a need set directly through
    -- the per-meal override layer rather than derived from a range.
    away_stretch_id INTEGER REFERENCES away_stretches(id),
    -- Ready-made recommendation: what to earmark to cover this slot without
    -- fresh cooking. Emily's rule (Notion, 2026-09-03): the system always
    -- RECOMMENDS, the household always CONFIRMS — nothing here is acted on
    -- (no defrost reminder, no batch-cook instruction) until confirmed=1.
    -- At most one of the two should be set at a time; both blank means no
    -- recommendation has been computed yet.
    recommended_batch_from_entry_id INTEGER REFERENCES meal_plan_entries(id),
    recommended_defrost_item TEXT NOT NULL DEFAULT '',
    recommendation_confirmed INTEGER NOT NULL DEFAULT 0,
    -- WHOSE need this is, as a JSON list of members.id. '[]' means the
    -- whole household, which is what every need meant before attendance
    -- existed — so old rows keep their meaning. Set for the per-traveler
    -- edges: if only Vineeth is away Saturday, Saturday breakfast is
    -- 'quick' FOR VINEETH (the rest of the house eats normally), and the
    -- first meal he's back for is 'ready_made' for him. Without this, a
    -- partial trip would tag the whole household's meal as a grab-and-go,
    -- which is exactly the "one layer too shallow" problem this deepening
    -- fixes.
    for_member_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(household_id, date, slot)
);

-- Loop Board: "Week planning: away-stretches and per-meal needs". One
-- "any trips this week?" range ask — day+meal to day+meal — recorded as
-- its own row so the derivation (which slots got marked away, which two
-- got the quick/ready_made edges) stays traceable back to the single
-- gesture that produced it, rather than looking like four unrelated
-- per-meal edits. See tools/slot_needs.py:set_away_stretch.
CREATE TABLE IF NOT EXISTS away_stretches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    from_date TEXT NOT NULL, -- ISO date of the first away slot
    from_slot TEXT NOT NULL,
    to_date TEXT NOT NULL, -- ISO date of the last away slot (inclusive)
    to_slot TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    -- WHO is away for this stretch, as a JSON list of members.id. '[]'
    -- means the whole household ("all of us"), which is both the common
    -- case and the pre-attendance behavior, so an old row reads correctly.
    -- Emily's deepened model (Notion, 2026-09-03): "Two people in the home
    -- might also not have the exact same schedule" — a trip belongs to
    -- specific travelers, and each traveler gets their own derived
    -- quick-departure / ready-made-return edge.
    member_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Loop Board: "Week planning: away-stretches and per-meal needs", the
-- DEEPENED model (Emily, 2026-09-03) that supersedes the household-level
-- away flag above. Her words: "this flow feels like it's one layer too
-- shallow... just Emily is home for dinner on Thursday, but Vineeth is
-- out."
--
-- The atomic fact is per-person, per-meal ATTENDANCE — who is actually at
-- which meal — and everything else derives from it:
--   * nobody present (and no guests) => that slot is 'away' in slot_needs:
--     nothing planned, nothing bought. "Away" is no longer a flag someone
--     sets; it is what an empty attendance MEANS. Both directions are kept
--     in sync by tools/attendance.py:_sync_away_need.
--   * some present => plan for the real headcount; portions and grocery
--     quantities scale to it (see attendance.grocery_scale_factor).
--   * guests => the same model with the headcount up. The "Hosting guests"
--     night-tag chip writes guest_count here rather than being a second,
--     parallel notion of table size.
--
-- One row per (date, slot), and — same "absence has one meaning"
-- discipline slot_needs itself follows — a slot with NO row here means
-- the ordinary case: every current member present, no guests. That
-- matters beyond tidiness: it means a member added later is present by
-- default everywhere, instead of being retroactively absent from every
-- meal the household has ever planned.
CREATE TABLE IF NOT EXISTS slot_attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    date TEXT NOT NULL, -- ISO date
    slot TEXT NOT NULL, -- breakfast | lunch | dinner | snack
    -- The members PRESENT, as a JSON list of members.id. '[]' means nobody
    -- is home for this meal, which (with guest_count 0) is exactly the
    -- 'away' case. Stored as who's present rather than who's absent so
    -- that "away" has a single, checkable definition: an empty set.
    present_member_ids_json TEXT NOT NULL DEFAULT '[]',
    -- Extra mouths beyond the members present — the "Hosting guests"
    -- gesture, unified into attendance rather than living only in
    -- week_intake.guest_counts_json.
    guest_count INTEGER NOT NULL DEFAULT 0,
    -- Which gesture produced this row: 'toggle' (a presence avatar tapped
    -- on a day card), 'away_stretch' (derived from a trip range), 'guests'
    -- (the night-tag chip), or 'chat'. Kept so the UI can explain itself
    -- and so a trip's slots can be found and undone together.
    source TEXT NOT NULL DEFAULT '',
    away_stretch_id INTEGER REFERENCES away_stretches(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(household_id, date, slot)
);

-- Loop Board: "Onboarding: household rhythm without traditional
-- assumptions". Emily's decided architecture (Notion, 2026-09-03):
-- Rhythm (onboarding, once) -> Exceptions (weekly intake, the away
-- stretches above) -> Corrections (chat, anytime, permanent). This table
-- is the Rhythm layer's structured half — the three behavior-based
-- questions (lunch location, meals eaten together, who cooks) that feed
-- generation defaults programmatically, distinct from the freeform
-- `facts` category='rhythm' notes (which stay freeform display text, not
-- something generation can branch logic on).
--
-- member_name = '' for a household-level fact (meals_together,
-- cooking_role); set for a per-person fact (lunch_location). weekday = ''
-- for the STANDING answer; a specific weekday (e.g. 'Tuesday') for a
-- correction that overrides just that day — this is the hybrid-schedule
-- support ("Marcus is in the office Tuesdays now") that Emily explicitly
-- chose to learn via chat correction rather than ask upfront. Resolving
-- "where is Marcus at lunch on a given Tuesday" means checking the
-- weekday-specific row first and falling back to the standing one — see
-- tools/rhythm.py:get_household_rhythm.
CREATE TABLE IF NOT EXISTS household_rhythm (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    member_name TEXT NOT NULL DEFAULT '',
    weekday TEXT NOT NULL DEFAULT '', -- '' = standing answer; 'Monday'..'Sunday' = a per-weekday override
    -- 'lunch_location'  value: 'home' | 'out' | 'varies'. Per-person.
    -- 'meals_together'  value: 'dinner_only' | 'dinner_and_breakfast' |
    --                   'most_meals' | 'varies'. Household-level.
    -- 'cooking_role'    value: 'one_person' | 'turns' | 'whoever_free'.
    --                   Household-level; `who` names the person when
    --                   value='one_person'.
    fact_type TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    who TEXT NOT NULL DEFAULT '',
    -- 'onboarding' | 'chat_correction' | 'expanded_view' — not behavior,
    -- just provenance for anyone auditing why a default is what it is.
    source TEXT NOT NULL DEFAULT 'onboarding',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(household_id, member_name, weekday, fact_type)
);

-- design_handoff_home_manager Phase 5 (NOTIFICATIONS.md): this app has no
-- push infrastructure (no service-worker push handler, no VAPID keys, no
-- background scheduler process on the Railway deployment) — see README's
-- Phase 5 notes for why real OS-level push for the 4 notification types
-- was descoped to a live, in-app "what needs your attention" feed instead.
-- This table is just what that live feed needs to not re-nag: a
-- dismissed key stays dismissed until its underlying condition changes
-- (a new dinner gap gets a new date-keyed row, so dismissing today's
-- doesn't hide tomorrow's).
CREATE TABLE IF NOT EXISTS notification_dismissals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    key TEXT NOT NULL,
    dismissed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(household_id, key)
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

-- One access credential per household: the beta's "how does a second
-- household get in" answer, and deliberately not an account system. There
-- are no usernames and no per-person logins -- a household has one shared
-- passphrase, exactly like the single HOME_MANAGER_PASSWORD did, except it
-- now identifies *which* household is signing in rather than just proving
-- someone may. See app/households.py for the hashing and the lookup.
--
-- Household 1 needs no row here: HOME_MANAGER_PASSWORD keeps signing Emily
-- in, unchanged, so her existing deployment and her existing cookie carry
-- on working without anything being set up.
CREATE TABLE IF NOT EXISTS household_credentials (
    household_id INTEGER PRIMARY KEY REFERENCES households(id),
    -- pbkdf2_sha256$<iterations>$<salt-hex>$<hash-hex>. Never the passphrase.
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per chat turn. Deliberately NO message content -- this exists to
-- answer "is the household actually using the app, and what does a turn
-- cost", not to keep a transcript. Chat history itself lives only in
-- memory (app/main.py SESSIONS) and is wiped on every restart, so before
-- this table there was no way to answer either question, and no way to
-- answer them retroactively either: an unrecorded turn is gone.
--
-- rounds is the number the cost work never had -- a cheaper call that
-- needs more rounds to finish the job is not actually cheaper, so cost per
-- completed job needs this alongside the tokens. All of it was already
-- computed for agent.py's log lines and thrown away.
CREATE TABLE IF NOT EXISTS chat_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    rounds INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per thing that went wrong, so it can be READ BACK rather than
-- only written to a log nobody opens. That distinction is the whole point:
-- errors already reach Railway's logs, but the overnight routine that
-- reports them each morning cannot read those logs -- it runs in the cloud
-- with the repo and Notion, not with the running app's stdout. An error
-- that only exists in a log is, for the purpose of anyone finding out
-- about it, an error nobody recorded.
--
-- Deliberately NO request bodies, no arguments, no tracebacks and no
-- user text -- same rule as chat_turns. `detail` is an exception class
-- name or a short reason, `where` is a route or tool name. Enough to tell
-- you something broke and where to go looking; not a second copy of the
-- household's private data sitting in a table.
CREATE TABLE IF NOT EXISTS error_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    kind TEXT NOT NULL,          -- server | tool | client | rate_limit
    where_ TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_error_events_household_created
    ON error_events (household_id, created_at);

-- One row per Anthropic API call, of ANY kind. chat_turns above rolls a
-- whole chat turn (its several rounds) into one row for turn-level
-- reporting; this table is one row per ACTUAL call to
-- client.messages.create, at every call site in the app -- chat included,
-- but also the six other places chat_turns never saw: weekly-plan
-- generation, component-plan fill-in, prep-schedule generation, recipe
-- fill-in, photo scans (receipt/fridge/pantry), and chore
-- recommendations. That distinction matters because chat_turns alone only
-- ever priced the chat loop, and the app's single most expensive call
-- (weekly-plan generation, ~18,000 uncached input tokens per run) was
-- invisible to it -- see the "Get API token usage down" ticket.
--
-- Deliberately duplicates chat's own numbers rather than replacing
-- chat_turns: the two answer different questions (turns/rounds needed to
-- finish a job, vs. cost broken down by call site) and neither can be
-- derived from the other.
--
-- call_site is the `label` passed to agent._create_with_retry -- the one
-- function every Anthropic call in the app actually goes through. That is
-- also why recording lives there instead of at each of the seven call
-- sites separately: one instrumentation point covers all of them, and a
-- call site added later is covered automatically instead of needing this
-- table kept in sync by hand.
--
-- model is stored per row, not assumed, so a call site that ever runs on
-- a different model from the rest is still priced correctly rather than
-- silently billed at the wrong rate.
CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    call_site TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_api_calls_household_created
    ON api_calls (household_id, created_at);
CREATE INDEX IF NOT EXISTS idx_api_calls_household_site
    ON api_calls (household_id, call_site);

-- Seed a single default household so V1 works out of the box
INSERT INTO households (id, name)
SELECT 1, 'My Household'
WHERE NOT EXISTS (SELECT 1 FROM households WHERE id = 1);
