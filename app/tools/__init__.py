"""
Tool functions the AI agent can call. Each function talks directly to
SQLite and returns plain dicts/lists (JSON-serializable) so they can be
handed straight back to Claude as tool results.

Which household a call operates on is request-scoped: `household_id()`
(from `_shared`) reads a ContextVar the web layer sets per request from the
signed session cookie, defaulting to household 1 for scripts and tests.
See `_shared.py` for why it's a ContextVar rather than an argument. Tool
functions therefore take no household parameter — the model never gets to
choose one.

This is a package: the tool functions live in domain modules alongside
this file (recipes, grocery, inventory, chores, ...). Everything they
define is re-exported here, so `from app import tools` and
`tools.add_recipe(...)` keep working exactly as before.
"""
from __future__ import annotations

from ._shared import (  # noqa: F401
    DEFAULT_HOUSEHOLD_ID,
    PUBLIC_BASE_URL,
    _absolute_url,
    household_id,
    set_current_household_id,
    reset_current_household_id,
    use_household,
)
from .attention import (  # noqa: F401
    add_attention_item,
    get_attention_items,
    record_attention_item_usage,
    resolve_attention_item,
)
from .chores import (  # noqa: F401
    _FREQUENCY_DAYS,
    add_chore,
    complete_chore,
    generate_chore_schedule,
    get_chores_due_today,
    get_chores_profile,
    list_chore_definitions,
    list_chores,
    schedule_chore_instance,
    set_chore_instance_status,
    set_chores_profile,
    update_chore,
)
# The household's own "cook these days now" pick (see cook_ahead.py). Not
# an agent tool: the picker is a set of chips on the Cook card, and a
# choice about which mornings to cook for is the household's to make by
# tapping, not the assistant's to make by inferring.
from .cook_ahead import (  # noqa: F401
    cook_ahead_options,
    cook_ahead_repeats,
    mark_cook_ahead_asked,
    set_cook_ahead,
)
from .cooker import (  # noqa: F401
    _find_inventory_match,
    _singularize,
    _use_inventory_row_by_id,
    check_off_meal,
    check_off_prep_step,
    deplete_inventory_for_meal,
    get_cooker_view,
    get_plan_progress,
    get_prep_schedule,
    save_prep_tasks,
)
from .defrost import (  # noqa: F401
    lead_hours_for_item,
    defrost_candidates_for_plan,
    defrost_task_from_ready_made,
    get_defrost_schedule,
    get_defrost_today,
    sync_defrost_tasks,
    meat_items_for_plan,
    confirm_frozen_items,
    mark_defrost_asked,
)
from .coordination import (  # noqa: F401
    check_meal_conflicts,
    check_plan_conflicts,
    explain_meal_choice,
    get_feedback_nudge,
    get_household_people,
)
# Deliberately NOT wired into agent.TOOL_FUNCTIONS — see feedback.py's
# module docstring. Re-exported here only so app/main.py and
# observability_report.py reach it the same way they reach record_error.
from .feedback import (  # noqa: F401
    count_feedback_reports,
    get_feedback_reports,
    record_feedback_report,
)
from .grocery import (  # noqa: F401
    _reverse_meal_grocery_contributions,
    _subtract_quantity,
    _try_consolidate_quantity,
    add_grocery_item,
    add_grocery_items,
    clear_grocery_list,
    clear_stale_grocery_items,
    consolidate_grocery_list,
    exclude_grocery_item,
    get_grocery_list_by_section,
    include_grocery_item,
    list_grocery_list,
    mark_grocery_item,
    move_grocery_item_to_inventory,
    remove_grocery_item,
    repair_grocery_quantities,
    update_grocery_item,
)
from .household import (  # noqa: F401
    _NON_RESTRICTION_VALUES,
    _get_or_create_member,
    _log_preference_event,
    add_member,
    add_pet,
    count_preference_events_this_month,
    get_coaching_state,
    get_household_setup_status,
    list_members,
    list_pets,
    mark_coaching_seen,
    set_household_goals,
    set_member_age_group,
    set_member_dietary_restrictions,
)
from .inventory import (  # noqa: F401
    _LEADING_NUM_RE,
    _add_to_inventory,
    _step_quantity_text,
    _try_subtract_quantity,
    get_cross_location_duplicates,
    get_expiring_soon,
    get_fresh_perishable_inventory,
    get_inventory,
    get_inventory_by_location,
    get_inventory_by_section,
    remove_inventory_item,
    set_inventory_location,
    step_inventory_expiration,
    step_inventory_quantity,
    update_inventory,
    update_inventory_items,
)
# Read-side helpers for the leftover chains repair_leftover_chains records
# (see leftovers.py). Not agent tools — the assistant has no reason to
# call these — but re-exported so agent.py's prep-schedule context and any
# future caller reach them the same `tools.x(...)` way as everything else.
# `covers_note` is aliased on the way out: the bare name says nothing about
# what it covers once it is sitting in this flat namespace.
from .leftovers import (  # noqa: F401
    batch_for_source,
    eaters_at,
    plan_leftover_chains,
)
from .leftovers import covers_note as leftovers_covers_note  # noqa: F401
from .meal_plans import (  # noqa: F401
    create_weekly_plan,
    discard_failed_plan,
    restore_recipe_cook_counters,
    snapshot_recipe_cook_counters,
    get_meal_plan,
    get_recent_meal_history,
    plan_meal,
)
from .memory import (  # noqa: F401
    _CONTEXT_SIGNALS,
    _build_context_completeness,
    add_fact,
    delete_fact,
    delete_preference,
    edit_preference,
    get_facts,
    get_household_memory,
    update_fact,
)
# Today's one timeline of "what's next for us?" (see moves.py). Not agent
# tools: this is a reading of state the assistant's own tools already own,
# built for one screen, and the model has better ways to answer the same
# question (get_cooker_view, get_defrost_today, the grocery list).
from .moves import (  # noqa: F401
    featured_move_id,
    moves_for_day,
    set_move_done,
    today_moves,
)
from .notifications import (  # noqa: F401
    _dismissed_keys,
    dismiss_notification,
    get_active_notifications,
    get_learning_summary,
)
# "Every meal is a full plate" (Emily, 2026-09-05) — see plates.py. Not
# agent tools: the assistant doesn't decide the rule, generation applies it.
# Aliased on the way out where the bare name would say nothing about plates
# once it's sitting in this flat namespace (`missing_groups`, `is_complete`
# and `get_sides` could each be about half a dozen things here).
from .plates import (  # noqa: F401
    complete_plate,
    plate_rule,
    sides_label,
    side_ingredients,
)
from .plates import get_sides as get_plate_sides  # noqa: F401
from .plates import has_food_groups as plate_has_food_groups  # noqa: F401
from .plates import is_complete as plate_is_complete  # noqa: F401
from .plates import missing_groups as plate_missing_groups  # noqa: F401
from .pre_shop import (  # noqa: F401
    _PRE_SHOP_FRACTION_LEAD,
    _PRE_SHOP_FRACTION_TAIL,
    _pre_shop_amount_words,
    _pre_shop_humanize_label,
    _pre_shop_pluralize,
    drop_grocery_item_pre_shop,
    get_already_have_decisions,
    get_grocery_already_have_items,
    get_pre_shop_flags,
    keep_all_pre_shop_flags,
    mark_grocery_item_already_have_reviewed,
    undo_pre_shop_drop,
)
from .prep_sessions import (  # noqa: F401
    PREP_CUT_TASK_TYPE,
    add_prep_cut,
    has_prep_days,
    prep_sessions_for_current_plan,
    prep_sessions_for_plan,
    set_skip_prep_this_week,
)
from .preferences import (  # noqa: F401
    DEFAULT_SNACKS_PER_DAY,
    add_food_dislikes,
    add_store_typical_items,
    add_usual_stores,
    dismiss_stores_prompt,
    get_meal_planning_setup_status,
    remove_item_from_all_stores_typical_list,
    remove_store_typical_item,
    resolve_snacks_per_day,
    save_onboarding_answers,
    set_household_meal_preferences,
)
from .quantities import (  # noqa: F401
    _CONTAINER_UNIT_PLURALS,
    _CONTAINER_UNIT_SINGULARS,
    _DEFAULT_LOCATION_BY_CATEGORY,
    _DEFAULT_SHELF_LIFE_DAYS,
    _GROCERY_CATEGORY_ALIASES,
    _GROCERY_SECTION_ORDER,
    _ITEM_SHELF_LIFE_DAYS,
    _LOCATION_ORDER,
    _MASS_TO_G,
    _METRIC_VOL_TO_ML,
    _NICE_FRACTIONS,
    _QTY_RE,
    _UNIT_ALIASES,
    _UNIT_CONVERSION_GROUPS,
    _UNIT_PLURALS,
    _VOLUME_TO_TSP,
    _WEIGHT_TO_OZ,
    _display_location,
    _estimate_expiration_date,
    _format_quantity,
    _humanize_grocery_quantity,
    _lookup_item_shelf_life_days,
    _normalize_container_word,
    _normalize_grocery_quantity,
    _parse_quantity,
    _resolve_location,
    _resolved_expiration_update,
    _roll_up_unit,
    _round_to_nice_fraction,
    _strip_prep_descriptor,
    package_unit,
)
from .recipes import (  # noqa: F401
    _add_recipe_ingredients_for_entries,
    _add_recipe_ingredients_to_grocery_list,
    _maybe_auto_attribute_solo_night,
    add_recipe,
    attribute_recipe_feedback,
    check_steps_ingredients_consistency,
    cooking_ingredients,
    cooking_quantity,
    flag_recipe_temporary,
    get_member_taste,
    get_recipe,
    list_recipes,
    list_recipes_for_planning,
    log_cooking_deviation,
    log_recipe_note,
    mark_recipe_feedback,
    save_cooking_quantities,
    scale_recipe,
    update_recipe_details,
    validate_measured_quantities,
)
from .reset import (  # noqa: F401
    clear_weekly_plan,
    get_reset_preview,
)
from .rhythm import (  # noqa: F401
    COOKING_ROLES,
    DINNER_WINDOWS,
    LEFTOVERS_STANCES,
    LUNCH_LOCATIONS,
    MAX_PREP_DAYS,
    MEALS_TOGETHER_OPTIONS,
    PLANNING_ANCHORS,
    PLANNING_ANCHOR_WEEKDAYS,
    PREP_DAY_WEEKDAYS,
    PREP_MINUTES_CHOICES,
    WEEKDAYS,
    clear_lunch_location_override,
    effective_lunch_location,
    get_household_rhythm,
    planning_anchor_label,
    prep_days_summary,
    prep_minutes_label,
    rhythm_completeness_signals,
    set_cooking_role,
    set_dinner_window,
    set_leftovers_stance,
    set_lunch_location,
    set_meals_together,
    set_planning_anchor,
    set_prep_days,
)
from .sharing import (  # noqa: F401
    eater_add_dietary_restriction,
    eater_add_note,
    get_member_notes,
    get_or_create_member_share_link,
    get_or_create_share_link,
    get_shared_weekly_plan,
    regenerate_member_share_link,
    resolve_member_share_link,
    revoke_member_share_link,
)
from .attendance import (  # noqa: F401
    clear_slot_attendance,
    context_for_week as attendance_context_for_week,
    get_slot_attendance,
    get_week_attendance,
    grocery_scale_factor,
    headcount_for_slot,
    remove_members_from_slot,
    resolve_member_ids,
    scale_ingredients,
    set_guest_count,
    set_member_attendance,
    set_slot_attendance,
    summary_line as attendance_summary_line,
)
from .slot_needs import (  # noqa: F401
    NEEDS,
    apply_slot_needs_to_plan,
    clear_slot_need,
    confirm_slot_recommendation,
    describe_ready_made,
    generation_context_for_week,
    get_slot_need,
    get_week_slot_needs,
    set_away_stretch,
    set_slot_need,
    set_slot_recommendation,
)
from .stores import (  # noqa: F401
    _DEFAULT_AISLE_ORDER,
    close_shopping_trip,
    confirm_grocery_item_store_preference,
    get_grocery_list_by_store,
    get_item_store_preferences,
    get_stores,
    is_multi_store_household,
    set_grocery_item_store,
    set_grocery_items_stores,
    set_item_store,
)
from .taste_verdict import (  # noqa: F401
    conflict_sentence,
    dish_verdict,
    generation_taste_lines,
    per_person_feedback,
    plan_taste_conflicts,
)
from .usage import (  # noqa: F401
    get_month_to_date_cost,
    get_recent_errors,
    get_recent_plan_quality,
    get_usage_summary,
    record_api_call,
    record_chat_turn,
    record_error,
    record_plan_quality,
    touch_household_active,
)
from .week_intake import (  # noqa: F401
    MOOD_GUIDANCE,
    NIGHT_TAGS,
    ONBOARDING_CUISINES,
    RUSH_MAX_MINUTES,
    _build_preferences_snapshot,
    _current_intake_row,
    _household_composition,
    _intake_row_to_dict,
    _observed_day_patterns,
    _rhythm_packed_lunch_suggestions,
    _week_dates,
    period_dates,
    get_week_intake,
    get_week_intake_history,
    get_week_intake_prefill,
    save_week_intake,
)
from .weekly_plan import (  # noqa: F401
    DAY_SLOTS,
    WEEK_SLOTS,
    _COMPONENT_CATEGORY_ORDER,
    _build_day_based_menu,
    _build_suggested_schedule,
    _compute_freshness,
    _current_weekly_plan_row,
    _format_week_range,
    _format_period_range,
    plan_period,
    period_end_date,
    periods_overlap,
    get_plan_id_for_date,
    find_overlapping_plans,
    retire_overlapping_plans,
    preview_approved_takeover,
    suggest_planning_period,
    MAX_PERIOD_DAYS,
    SlotRefused,
    _plan_grocery_candidate_entries,
    _suggest_quick_dinners,
    _week_headline,
    _weekly_plan_is_approved,
    add_dish_day,
    approve_weekly_plan,
    attach_intake_to_plan,
    audit_plan_slots,
    clear_plan_slot,
    drop_dish_from_day,
    _dedupe_duplicate_slots,
    repair_leftover_chains,
    get_meal_planning_preferences,
    get_needs_you_items,
    get_plan_id_for_week,
    get_week_menu,
    get_week_planning_nudge,
    get_weekly_plan,
    mark_plates_intro_shown,
    plan_slot_empty,
    plan_slot_open,
    preview_plan_grocery_impact,
    reopen_weekly_plan,
    resolve_needs_you_dinner,
    resolve_open_slot,
    set_planning_mode,
    set_week_constraints,
    swap_component_in_plan,
    swap_meal_in_plan,
    week_receipt,
)
# One meal, replaced on the spot for one small model call — the Meals
# screen's "Swap" (Julia, 2026-09-08). Not an agent tool: chat already has
# swap_meal_in_plan and a whole conversation to choose with, and this
# exists precisely to avoid spending that turn.
from .swap_in_place import (  # noqa: F401
    swap_meal_in_place,
    undo_meal_swap,
)
