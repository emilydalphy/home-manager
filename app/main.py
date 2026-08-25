"""
FastAPI backend for the Home Manager chat app.

Run with:  uvicorn app.main:app --reload
Then open: http://localhost:8000
"""
from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
import base64
import datetime
import logging
import os

from .db import init_db
from .agent import run_agent_turn, trim_conversation, generate_chore_recommendations, generate_weekly_plan, fill_in_recipe, scan_receipt_image, scan_fridge_photo, scan_pantry_photo, AssistantUnavailableError
from . import tools

logger = logging.getLogger("home_manager")

app = FastAPI(title="Home Manager")

# In-memory session store: session_id -> conversation history.
# Fine for V1 (single household, one browser tab). Swap for a real
# session/DB-backed store before this becomes multi-user.
SESSIONS: dict[str, list[dict]] = {}


class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str


class ChatResponse(BaseModel):
    reply: str


class MemberInput(BaseModel):
    name: str
    age_group: str = ""


class PetInput(BaseModel):
    name: str
    pet_type: str = ""


class HouseholdOnboardingRequest(BaseModel):
    members: list[MemberInput] = []
    pets: list[PetInput] = []
    goals: str = ""


class OnboardingAnswersRequest(BaseModel):
    """The onboarding-redesign minimum-viable question set (PRD §4.1) — the
    only 7 questions asked before the first plan is generated."""
    member_names: list[str] = []
    household_restrictions: dict[str, list[str]] = {}  # member name -> restrictions, only for members who have any
    eating_style: str = ""
    wont_eat: list[str] = []
    excited_about: list[str] = []
    dinners_per_week: int = 7
    breakfasts_per_week: int = 7
    lunches_per_week: int = 7


class ChoreProfileRequest(BaseModel):
    home_type: str = ""
    bedrooms: int = 0
    bathrooms: int = 0
    has_yard: bool = False
    standard: str = "standard"  # relaxed | standard | meticulous
    rotation_members: list[str] = []
    existing_help: str = ""
    existing_help_frequency: str = ""
    include_notes: str = ""
    exclude_notes: str = ""


class ChoreItemInput(BaseModel):
    name: str
    category: str = "cleaning"
    frequency: str = "weekly"
    assignee_names: list[str] = []


class ChoreSaveRequest(BaseModel):
    chores: list[ChoreItemInput] = []


class ChoreStatusRequest(BaseModel):
    status: str = "done"  # 'done' | 'pending'


class MemoryEditRequest(BaseModel):
    field: str  # notes | cooking_time_preference | cuisine_preferences | protein_preferences | dislikes | usual_stores | goals
    value: object  # str, list[str], or dict depending on field


class MemoryDeleteRequest(BaseModel):
    field: str  # dislikes | cuisine_preferences | protein_preferences | notes | cooking_time_preference | usual_stores
    item: str | None = None


class StoreTypicalItemAddRequest(BaseModel):
    store: str
    item: str


class StoreTypicalItemRemoveRequest(BaseModel):
    store: str
    item: str


class CheckOffMealRequest(BaseModel):
    entry_id: int
    status: str = "done"  # pending | done


class CheckOffPrepRequest(BaseModel):
    prep_task_id: int
    status: str = "done"  # pending | done


class FillRecipeRequest(BaseModel):
    recipe_name: str


class CookingDeviationRequest(BaseModel):
    recipe_name: str
    note: str


class ResolveAttentionRequest(BaseModel):
    status: str = "resolved"  # resolved | dismissed


class AttentionUsageRequest(BaseModel):
    amount_used: str = ""


class MemberRestrictionRequest(BaseModel):
    restriction: str


class MemberNoteRequest(BaseModel):
    note: str


class RecipeFeedbackRequest(BaseModel):
    recipe_name: str
    rating: str | None = None  # liked | disliked | None (notes only)
    notes: str = ""


class InventoryUpdateRequest(BaseModel):
    item: str
    action: str = "set"  # add | use | remove | set
    quantity: str = ""
    category: str = "other"
    expiration_date: str | None = None
    location: str | None = None  # fridge | freezer | pantry — falls back to a category-based guess if unset


class ScannedItem(BaseModel):
    item: str
    quantity: str = ""
    category: str = "other"
    expiration_date: str | None = None
    location: str | None = None


class ConfirmScanRequest(BaseModel):
    items: list[ScannedItem]


class GroceryAddRequest(BaseModel):
    item: str
    quantity: str = ""
    category: str = "other"


class GroceryUpdateRequest(BaseModel):
    quantity: str | None = None
    category: str | None = None


class GroceryStatusRequest(BaseModel):
    status: str = "purchased"  # needed | in_cart | purchased


class GroceryStoreRequest(BaseModel):
    store: str = ""


@app.on_event("startup")
def startup():
    init_db()


@app.get("/api/onboarding/status")
def onboarding_status():
    """Combined status for the onboarding wizard to decide what to show/skip."""
    try:
        household = tools.get_household_setup_status()
        meal = tools.get_meal_planning_setup_status()
    except Exception as e:
        logger.exception("Onboarding status check failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"household": household, "meal_planning": meal}


@app.post("/api/onboarding/household")
def onboarding_household(req: HouseholdOnboardingRequest):
    """Save household basics: members (+ age group), pets, and goals. Called directly by the onboarding wizard — no LLM round-trip needed for structured form data."""
    try:
        for m in req.members:
            if not m.name.strip():
                continue
            tools.add_member(m.name.strip())
            if m.age_group:
                tools.set_member_age_group(m.name.strip(), m.age_group)
        for p in req.pets:
            if not p.name.strip():
                continue
            tools.add_pet(p.name.strip(), p.pet_type)
        if req.goals:
            tools.set_household_goals(req.goals)
    except Exception as e:
        logger.exception("Household onboarding save failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"saved": True}


@app.post("/api/onboarding/answers")
def onboarding_answers(req: OnboardingAnswersRequest):
    """
    Save the onboarding-redesign minimum-viable question set in one call —
    household member names, per-person hard restrictions, eating style,
    won't-eat list, excited-about cuisines, and dinners per week. Replaces
    the old /api/onboarding/meal-preferences endpoint's broader collection
    (favorite proteins, casual dislikes beyond won't-eat, cooking-time
    preference) — those are no longer asked upfront; they accumulate
    through ordinary chat/UI use afterward, per the onboarding redesign PRD.
    """
    try:
        memory = tools.save_onboarding_answers(
            member_names=req.member_names,
            household_restrictions=req.household_restrictions,
            eating_style=req.eating_style,
            wont_eat=req.wont_eat,
            excited_about=req.excited_about,
            dinners_per_week=req.dinners_per_week,
            breakfasts_per_week=req.breakfasts_per_week,
            lunches_per_week=req.lunches_per_week,
        )
    except Exception as e:
        logger.exception("Onboarding answers save failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return memory


@app.get("/api/members/{name}/share-link")
def get_member_share_link(name: str):
    """
    Get (or create on first use) a household member's self-service link —
    powers both the onboarding first-plan reveal's invite-at-reveal offer
    and the persistent "invite" affordance on the Memory view, since
    skipping the reveal shouldn't mean losing the option entirely.
    """
    try:
        link = tools.get_or_create_member_share_link(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Member share link lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return link


@app.post("/api/onboarding/generate-first-plan")
def onboarding_generate_first_plan():
    """
    Generate and save a real first weekly plan from what was just entered in
    onboarding (household composition, dietary restrictions, protein/cuisine/
    cooking-time preferences). Called once, right after meal-preferences
    onboarding is saved, so the wizard can show a plan that visibly reflects
    what the person just told it instead of ending on a generic confirmation
    screen. Starts the week today.
    """
    try:
        week_start = datetime.date.today().isoformat()
        plan = generate_weekly_plan(week_start)
    except AssistantUnavailableError as e:
        logger.warning("First-plan generation hit a transient Claude API failure: %s", e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("First-plan generation during onboarding failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return plan


@app.post("/api/onboarding/chores/recommend")
def onboarding_chores_recommend(req: ChoreProfileRequest):
    """
    Turn a household chores profile into a recommended chore list via a
    single forced tool-call to Claude. Pets and household goals are pulled
    in automatically from what's already saved. Returns suggestions only —
    nothing is created yet; the wizard shows these as an editable checklist
    and the user's final choices go to /api/onboarding/chores/save.
    """
    try:
        pets = tools.list_pets()
        household = tools.get_household_setup_status()
        profile = req.dict()
        profile["pets"] = pets
        profile["goals"] = household.get("goals", "")
        chores = generate_chore_recommendations(profile)
    except AssistantUnavailableError as e:
        logger.warning("Chore recommendation hit a transient Claude API failure: %s", e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Chore recommendation failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"chores": chores}


@app.post("/api/onboarding/chores-profile")
def onboarding_chores_profile(req: ChoreProfileRequest):
    """
    Save the chores questionnaire answers directly as context — no LLM call,
    no chores created yet. Used when skipping the recommendation/review
    step; the chat assistant can read this later via get_chores_profile
    instead of re-asking these questions.
    """
    try:
        tools.set_chores_profile(
            home_type=req.home_type,
            bedrooms=req.bedrooms,
            bathrooms=req.bathrooms,
            has_yard=req.has_yard,
            standard=req.standard,
            rotation_members=req.rotation_members,
            existing_help=req.existing_help,
            existing_help_frequency=req.existing_help_frequency,
            include_notes=req.include_notes,
            exclude_notes=req.exclude_notes,
        )
    except Exception as e:
        logger.exception("Chores profile save failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"saved": True}


@app.post("/api/onboarding/chores/save")
def onboarding_chores_save(req: ChoreSaveRequest):
    """Create the chores the user kept/edited from the recommendations, then generate the upcoming schedule."""
    try:
        for c in req.chores:
            if not c.name.strip():
                continue
            tools.add_chore(
                name=c.name.strip(),
                frequency=c.frequency,
                category=c.category,
                assignee_names=c.assignee_names or None,
            )
        tools.generate_chore_schedule(days_ahead=14)
    except Exception as e:
        logger.exception("Chore save failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"saved": True, "created": len(req.chores)}


@app.get("/api/memory")
def get_memory():
    """Everything the app has saved about this household's meal preferences — powers the 'what we know' view."""
    try:
        memory = tools.get_household_memory()
    except Exception as e:
        logger.exception("Fetching household memory failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return memory


@app.post("/api/memory/edit")
def edit_memory(req: MemoryEditRequest):
    """Directly set a preference field (see MemoryEditRequest for valid fields), used by the 'what we know' view's edit controls."""
    try:
        if req.field == "goals":
            tools.set_household_goals(str(req.value))
        else:
            tools.edit_preference(req.field, req.value)
        memory = tools.get_household_memory()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Editing preference failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return memory


@app.post("/api/memory/delete")
def delete_memory(req: MemoryDeleteRequest):
    """Remove/clear a preference field or a single list item, used by the 'what we know' view's remove controls."""
    try:
        tools.delete_preference(req.field, req.item)
        memory = tools.get_household_memory()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Deleting preference failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return memory


@app.post("/api/memory/store-items/add")
def add_memory_store_item(req: StoreTypicalItemAddRequest):
    """Add one typical item for a usual store, used by the 'what we know' view's per-store item lists."""
    try:
        tools.add_store_typical_items(req.store, [req.item])
        memory = tools.get_household_memory()
    except Exception as e:
        logger.exception("Adding store typical item failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return memory


@app.post("/api/memory/store-items/remove")
def remove_memory_store_item(req: StoreTypicalItemRemoveRequest):
    """Remove one typical item from a usual store's list, used by the 'what we know' view's per-store item lists."""
    try:
        tools.remove_store_typical_item(req.store, req.item)
        memory = tools.get_household_memory()
    except Exception as e:
        logger.exception("Removing store typical item failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return memory


@app.get("/api/cooker-view")
def get_cooker_view(weekly_plan_id: int | None = None):
    """Everything needed to cook the current (or given) weekly plan — powers the dedicated Cooker view page."""
    try:
        view = tools.get_cooker_view(weekly_plan_id)
    except Exception as e:
        logger.exception("Cooker view lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return view


@app.get("/api/recipes/scale")
def scale_recipe_endpoint(name: str, servings: int):
    """
    Live-scale a saved recipe's ingredient quantities to a target serving
    count — powers the Cooker view's serving-size stepper (no chat
    round-trip needed for a +/- tap). Quantities that don't parse cleanly
    (e.g. "a pinch," "to taste") come back unchanged in unscaled_items so
    the Cooker knows to eyeball them.
    """
    if servings < 1:
        raise HTTPException(status_code=400, detail="Servings must be at least 1.")
    try:
        result = tools.scale_recipe(name, servings)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Recipe scaling failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/cooker/check-meal")
def cooker_check_meal(req: CheckOffMealRequest):
    """Mark a planned meal cooked/pending directly from the Cooker view (no chat round-trip needed)."""
    try:
        tools.check_off_meal(req.entry_id, req.status)
        view = tools.get_cooker_view()
    except Exception as e:
        logger.exception("Cooker check-meal failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return view


@app.post("/api/cooker/check-prep")
def cooker_check_prep(req: CheckOffPrepRequest):
    """Mark a prep task done/pending directly from the Cooker view (no chat round-trip needed)."""
    try:
        tools.check_off_prep_step(req.prep_task_id, req.status)
        view = tools.get_cooker_view()
    except Exception as e:
        logger.exception("Cooker check-prep failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return view


@app.post("/api/cooker/log-deviation")
def cooker_log_deviation(req: CookingDeviationRequest):
    """Log a one-off cooking deviation/substitution against a recipe directly from the Cooker view — used by the hands-free voice 'log a substitution' command (Phase 5, §4.1), also usable from a future UI control."""
    try:
        result = tools.log_cooking_deviation(req.recipe_name, req.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Cooker log-deviation failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/cooker/fill-recipe")
def cooker_fill_recipe(req: FillRecipeRequest):
    """Generate and save a full step-by-step for a recipe that's missing one, directly from the Cooker view."""
    try:
        fill_in_recipe(req.recipe_name)
        view = tools.get_cooker_view()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AssistantUnavailableError as e:
        logger.warning("Cooker fill-recipe hit a transient Claude API failure: %s", e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Cooker fill-recipe failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return view


@app.get("/api/attention")
def get_attention():
    """Phase 4, §4.4: the unified 'needs your attention' list (feedback nudge + queued inventory-depletion matches) — powers the Cooker view's attention banner."""
    try:
        items = tools.get_attention_items()
    except Exception as e:
        logger.exception("Attention-items lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"items": items}


@app.post("/api/attention/{item_id}/resolve")
def resolve_attention(item_id: int, req: ResolveAttentionRequest):
    """Mark a queued attention item resolved/dismissed directly from the Cooker view."""
    try:
        tools.resolve_attention_item(item_id, req.status)
        items = tools.get_attention_items()
    except Exception as e:
        logger.exception("Attention-item resolve failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"items": items}


@app.get("/api/chores/today")
def chores_today():
    """
    Chore instances due today — powers the app-shell Today screen's chores
    card (design_handoff_shell/README.md §4). Added for Step 2 of that
    redesign: no chores read endpoint existed before this (list_chores was
    chat-agent-only), so this — like /api/cooker-view, /api/grocery-list,
    etc. — is a small direct-read endpoint for a UI page rather than a
    chat round-trip. See the Step 2 note in the README's build-order log
    for why this exists despite that doc's "no new endpoints" line.
    """
    try:
        chores = tools.get_chores_due_today()
    except Exception as e:
        logger.exception("Today's-chores lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"chores": chores}


@app.post("/api/chores/{instance_id}/status")
def set_chore_status(instance_id: int, req: ChoreStatusRequest):
    """Toggle a chore instance done/pending directly from the Today screen's chores card (no chat round-trip needed)."""
    try:
        result = tools.set_chore_instance_status(instance_id, req.status)
    except Exception as e:
        logger.exception("Chore status update failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/recipe-feedback")
def record_recipe_feedback(req: RecipeFeedbackRequest):
    """Rate a recipe (liked/disliked, with optional notes) directly from the Cooker view's feedback-nudge banner item — the inline alternative to answering 'how'd it go?' back in chat."""
    try:
        result = tools.mark_recipe_feedback(req.recipe_name, rating=req.rating, notes=req.notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Recipe feedback failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/attention/{item_id}/use")
def record_attention_usage(item_id: int, req: AttentionUsageRequest):
    """Log how much was actually used for a 'needs_amount_used' inventory-depletion attention item, applying it directly to the tracked inventory row and resolving the item — the inline alternative to answering by rewriting the amount left."""
    try:
        result = tools.record_attention_item_usage(item_id, req.amount_used)
        items = tools.get_attention_items()
    except Exception as e:
        logger.exception("Attention-item usage log failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"result": result, "items": items}


@app.get("/api/inventory")
def get_inventory_view():
    """Pantry/fridge inventory grouped by store section — powers the dedicated Inventory view page."""
    try:
        result = tools.get_inventory_by_section()
    except Exception as e:
        logger.exception("Inventory lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.get("/api/inventory/expiring")
def get_inventory_expiring(days: int = 4):
    """Items already expired or expiring within `days` days — powers the Inventory view's 'going bad soon' banner."""
    try:
        result = tools.get_expiring_soon(days=days)
    except Exception as e:
        logger.exception("Expiring-inventory lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"items": result}


@app.get("/api/inventory/by-location")
def get_inventory_view_by_location():
    """Pantry/fridge inventory grouped by storage location (fridge/freezer/pantry) — powers the Inventory view's location-grouping toggle."""
    try:
        result = tools.get_inventory_by_location()
    except Exception as e:
        logger.exception("Inventory-by-location lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.get("/api/inventory/duplicates")
def get_inventory_duplicates():
    """Items tracked in more than one storage location at once (e.g. an opened sauce in the fridge and an unopened one in the pantry) — powers the Inventory view's duplicates banner."""
    try:
        result = tools.get_cross_location_duplicates()
    except Exception as e:
        logger.exception("Cross-location duplicate lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"duplicates": result}


@app.post("/api/inventory/update")
def update_inventory_view(req: InventoryUpdateRequest):
    """Add/update an inventory item directly from the Inventory view (not via chat)."""
    try:
        tools.update_inventory(req.item, req.action, quantity=req.quantity, expiration_date=req.expiration_date, category=req.category, location=req.location)
        result = tools.get_inventory_by_section()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Inventory update failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/inventory/{item_id}/remove")
def remove_inventory_view_item(item_id: int):
    """Remove a single inventory item directly from the Inventory view."""
    try:
        tools.remove_inventory_item(item_id)
        result = tools.get_inventory_by_section()
    except Exception as e:
        logger.exception("Inventory remove failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.get("/api/grocery-list")
def get_grocery_list_view(status: str = "needed"):
    """
    Grocery list grouped by store section — powers the dedicated Grocery
    List view page. status: needed | in_cart | purchased | excluded | all.
    For 'needed', items flagged by get_grocery_already_have_items (not yet
    reviewed) are left out here too — they're shown separately in the
    view's "Already have this?" review section instead, so nothing appears
    twice.
    """
    try:
        result = tools.get_grocery_list_by_section(status=status)
        if status == "needed":
            already_have_ids = {it["item_id"] for it in tools.get_grocery_already_have_items()}
            if already_have_ids:
                result = {
                    "sections": [
                        {"section": s["section"], "items": [it for it in s["items"] if it["id"] not in already_have_ids]}
                        for s in result["sections"]
                    ]
                }
                result["sections"] = [s for s in result["sections"] if s["items"]]
        result["multi_store"] = tools.is_multi_store_household()
    except Exception as e:
        logger.exception("Grocery list lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.get("/api/grocery-list/by-store")
def get_grocery_list_by_store_view(status: str = "needed"):
    """
    Grocery list split into store groups (see set_item_store) — powers the
    Grocery List view's 'By store' toggle. Same already-have filtering as
    the main /api/grocery-list endpoint for status='needed', so a flagged
    item doesn't show here while also sitting in the review section.
    """
    try:
        result = tools.get_grocery_list_by_store(status=status)
        if status == "needed":
            already_have_ids = {it["item_id"] for it in tools.get_grocery_already_have_items()}
            if already_have_ids:
                stores = []
                for store in result["stores"]:
                    sections = [
                        {"section": s["section"], "items": [it for it in s["items"] if it["id"] not in already_have_ids]}
                        for s in store["sections"]
                    ]
                    sections = [s for s in sections if s["items"]]
                    if sections:
                        stores.append({"store": store["store"], "sections": sections})
                result = {"stores": stores}
    except Exception as e:
        logger.exception("Grocery list by-store lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.get("/api/grocery-list/already-have")
def get_grocery_already_have_view():
    """Items on the 'needed' list that may already be covered by tracked inventory — powers the Grocery List view's 'Already have this?' review section."""
    try:
        result = tools.get_grocery_already_have_items()
    except Exception as e:
        logger.exception("Grocery already-have lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"items": result}


@app.get("/api/grocery-list/store-preferences")
def get_grocery_item_store_preferences():
    """Every remembered item->store association (see set_item_store) as a flat map — powers the Grocery List view's 'usually here' indicator on auto-tagged items."""
    try:
        prefs = tools.get_item_store_preferences()
    except Exception as e:
        logger.exception("Grocery store-preferences lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"preferences": prefs}


@app.post("/api/grocery-list/{item_id}/keep")
def keep_grocery_list_item(item_id: int):
    """Confirm an already-have-flagged item is still needed — moves it back into the normal To-buy list."""
    try:
        result = tools.mark_grocery_item_already_have_reviewed(item_id)
    except Exception as e:
        logger.exception("Grocery already-have review failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/{item_id}/store")
def set_grocery_list_item_store(item_id: int, req: GroceryStoreRequest):
    """Assign which store a specific listed item should be bought at, directly from the Grocery List view."""
    try:
        result = tools.set_grocery_item_store(item_id, req.store)
    except Exception as e:
        logger.exception("Grocery list store assignment failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/add")
def add_grocery_list_item(req: GroceryAddRequest):
    """Add an item to the grocery list directly from the Grocery List view (not via chat)."""
    try:
        result = tools.add_grocery_item(req.item, quantity=req.quantity, category=req.category, added_by="user")
    except Exception as e:
        logger.exception("Grocery list add failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/{item_id}/update")
def update_grocery_list_item(item_id: int, req: GroceryUpdateRequest):
    """Correct an already-listed item's quantity/category directly from the Grocery List view."""
    try:
        result = tools.update_grocery_item(item_id, quantity=req.quantity, category=req.category)
    except Exception as e:
        logger.exception("Grocery list item update failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/{item_id}/status")
def set_grocery_list_item_status(item_id: int, req: GroceryStatusRequest):
    """Move an item between needed/in_cart/purchased — checking something off as purchased also adds it to tracked inventory automatically."""
    try:
        result = tools.mark_grocery_item(item_id, status=req.status)
    except Exception as e:
        logger.exception("Grocery list status update failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/{item_id}/remove")
def remove_grocery_list_item(item_id: int):
    """Delete an item from the grocery list entirely, directly from the Grocery List view."""
    try:
        result = tools.remove_grocery_item(item_id)
    except Exception as e:
        logger.exception("Grocery list remove failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/{item_id}/already-have")
def move_grocery_list_item_to_inventory(item_id: int):
    """
    'Already have' button on any Grocery List row — turns out the
    household already has this on hand, so it gets added straight to
    pantry/fridge inventory (no separate manual entry) and taken off the
    list, in one tap.
    """
    try:
        result = tools.move_grocery_item_to_inventory(item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Grocery list already-have-to-inventory failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/{item_id}/exclude")
def exclude_grocery_list_item(item_id: int):
    """Hide an item from the normal shown list (getting it elsewhere) without deleting it, directly from the Grocery List view."""
    try:
        result = tools.exclude_grocery_item(item_id)
    except Exception as e:
        logger.exception("Grocery list exclude failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/{item_id}/include")
def include_grocery_list_item(item_id: int):
    """Undo exclude — put an item back on the normal shown list, directly from the Grocery List view."""
    try:
        result = tools.include_grocery_item(item_id)
    except Exception as e:
        logger.exception("Grocery list include failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


_MAX_SCAN_IMAGE_BYTES = 8 * 1024 * 1024  # generous for a phone photo; Claude's own image limits are higher still


async def _read_scan_image(photo: UploadFile) -> tuple[str, str]:
    if photo.content_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        raise HTTPException(status_code=400, detail="Please upload a JPEG, PNG, WEBP, or GIF photo.")
    data = await photo.read()
    if not data:
        raise HTTPException(status_code=400, detail="That photo came through empty — try again.")
    if len(data) > _MAX_SCAN_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="That photo's too large — try a smaller/lower-res photo.")
    return base64.b64encode(data).decode("ascii"), photo.content_type


@app.post("/api/inventory/scan-receipt")
async def scan_receipt(photo: UploadFile = File(...)):
    """
    Phase 4, §4.3: photograph a grocery receipt and get back a draft list of
    detected items — nothing is saved yet, the Inventory view shows this as
    an editable review step before /api/inventory/confirm-scan actually
    writes anything.
    """
    image_b64, media_type = await _read_scan_image(photo)
    try:
        items = scan_receipt_image(image_b64, media_type)
    except AssistantUnavailableError as e:
        logger.warning("Receipt scan hit a transient Claude API failure: %s", e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Receipt scan failed")
        raise HTTPException(status_code=500, detail=f"Couldn't read that receipt: {e}")
    return {"items": items}


@app.post("/api/inventory/scan-fridge")
async def scan_fridge(photo: UploadFile = File(...)):
    """
    Phase 4, §4.3: photograph fridge shelves and get back a draft list of
    detected items for an initial stock-take or re-sync — same
    review-before-save flow as the receipt scan, but expect lower
    confidence given mixed/stacked/partially obscured items. Every returned
    item is pre-tagged location='fridge' (or 'freezer' for anything
    visibly in a freezer compartment) so it lands in the right place
    without extra manual work.
    """
    image_b64, media_type = await _read_scan_image(photo)
    try:
        items = scan_fridge_photo(image_b64, media_type)
    except AssistantUnavailableError as e:
        logger.warning("Fridge scan hit a transient Claude API failure: %s", e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Fridge scan failed")
        raise HTTPException(status_code=500, detail=f"Couldn't read that photo: {e}")
    return {"items": items}


@app.post("/api/inventory/scan-pantry")
async def scan_pantry(photo: UploadFile = File(...)):
    """
    Phase 4, §4.3 follow-up: photograph pantry/cupboard shelves — same flow
    as scan-fridge, but every returned item is pre-tagged location='pantry'.
    """
    image_b64, media_type = await _read_scan_image(photo)
    try:
        items = scan_pantry_photo(image_b64, media_type)
    except AssistantUnavailableError as e:
        logger.warning("Pantry scan hit a transient Claude API failure: %s", e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Pantry scan failed")
        raise HTTPException(status_code=500, detail=f"Couldn't read that photo: {e}")
    return {"items": items}


@app.post("/api/inventory/confirm-scan")
def confirm_scan(req: ConfirmScanRequest):
    """Save a reviewed/edited scan result (receipt, fridge, or pantry photo) into inventory."""
    try:
        entries = [
            {"item": i.item, "action": "add", "quantity": i.quantity, "category": i.category, "expiration_date": i.expiration_date, "location": i.location}
            for i in req.items
        ]
        tools.update_inventory_items(entries, action="add")
        result = tools.get_inventory_by_section()
    except Exception as e:
        logger.exception("Confirm-scan save failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.get("/api/share-link")
def get_share_link():
    """Get (or create on first call) this household's read-only Eater share-link token."""
    try:
        result = tools.get_or_create_share_link()
    except Exception as e:
        logger.exception("Share link lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.get("/api/share/{token}")
def get_shared_plan(token: str):
    """Public, read-only: resolve a share token to the household's current weekly plan. No auth."""
    try:
        plan = tools.get_shared_weekly_plan(token)
    except Exception as e:
        logger.exception("Shared plan lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    if plan is None:
        raise HTTPException(status_code=404, detail="This link isn't valid.")
    return plan


@app.get("/api/member-share/{token}")
def get_member_share(token: str):
    """Public: resolve a member self-service token to that person's own name, restrictions, and notes. No auth beyond the token itself."""
    try:
        view = tools.resolve_member_share_link(token)
    except Exception as e:
        logger.exception("Member share lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    if view is None:
        raise HTTPException(status_code=404, detail="This link isn't valid.")
    return view


@app.post("/api/member-share/{token}/restriction")
def add_member_share_restriction(token: str, req: MemberRestrictionRequest):
    """Public: add a dietary restriction as the member behind this token — merges with their existing list."""
    try:
        tools.eater_add_dietary_restriction(token, [req.restriction])
        view = tools.resolve_member_share_link(token)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Member share restriction add failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return view


@app.post("/api/member-share/{token}/note")
def add_member_share_note(token: str, req: MemberNoteRequest):
    """Public: leave a freeform preference/feedback note as the member behind this token."""
    try:
        tools.eater_add_note(token, req.note)
        view = tools.resolve_member_share_link(token)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Member share note add failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return view


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    history = SESSIONS.get(req.session_id, [])
    try:
        reply, updated_history = run_agent_turn(history, req.message)
    except AssistantUnavailableError as e:
        # Claude's API itself was down/overloaded even after retrying inside
        # run_agent_turn — str(e) is already a warm, customer-facing
        # message (never a raw status code or JSON blob), so it's safe to
        # show as-is. Session history is untouched here since the request
        # never got far enough to append anything malformed.
        logger.warning("Chat turn hit a transient Claude API failure: %s", e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        # Log the full error server-side, but return a short, readable
        # message to the browser instead of a raw crash page — the most
        # common cause is a missing/invalid ANTHROPIC_API_KEY.
        logger.exception("Chat turn failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    # Cap stored history so a long-lived browser tab (no logout, "default"
    # session id) can't grow this — and the full payload re-sent to Claude
    # every turn — without bound. See trim_conversation for why this is
    # safe to cut mid-list without breaking tool_use/tool_result pairing.
    SESSIONS[req.session_id] = trim_conversation(updated_history)
    return ChatResponse(reply=reply)


static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# App-shell redesign (design_handoff_shell/README.md), Step 1: the four
# top-level shell routes all serve the same shell.html — it's a persistent
# app frame whose client-side router (static/shell.js) shows/hides tab
# content without a page reload. static/index.html, grocery.html and
# cooker.html are unmodified and still reachable directly under /static/
# (via the mount below); the shell embeds them via <iframe> for now, so
# their own behavior (chat, grocery filters, cook steps, etc.) needed zero
# changes. /week and /kitchen are new destinations with no prior route.
@app.get("/")
def index():
    return FileResponse(os.path.join(static_dir, "shell.html"))


@app.get("/week")
def week_page():
    return FileResponse(os.path.join(static_dir, "shell.html"))


@app.get("/grocery")
def grocery_page():
    return FileResponse(os.path.join(static_dir, "shell.html"))


@app.get("/kitchen")
def kitchen_page():
    return FileResponse(os.path.join(static_dir, "shell.html"))


# /cooker's content now lives at the Kitchen tab (same cooker.html, embedded
# in the shell) — redirect rather than serve it standalone a second way.
@app.get("/cooker")
def cooker_page():
    return RedirectResponse(url="/kitchen")


# /onboarding and /memory are NOT redirected yet, on purpose: the README
# retires them as top-level destinations once Kitchen's "What we know"
# absorbs their content, but that content merge hasn't been built (it's not
# part of Step 1's scope). Redirecting them now, before Kitchen actually
# has a "What we know" section, would strand first-time setup and memory
# edits with no way to reach them. They keep serving their real pages
# unchanged until that merge happens.
@app.get("/onboarding")
def onboarding_page():
    return FileResponse(os.path.join(static_dir, "onboarding.html"))


@app.get("/memory")
def memory_page():
    return FileResponse(os.path.join(static_dir, "memory.html"))


@app.get("/inventory")
def inventory_page():
    return FileResponse(os.path.join(static_dir, "inventory.html"))


@app.get("/share/{token}")
def share_page(token: str):
    """Public read-only page — no auth check here (share.html itself calls /api/share/{token}
    and shows a friendly not-found state if the token is invalid); this route just serves the shell."""
    return FileResponse(os.path.join(static_dir, "share.html"))


@app.get("/member-share/{token}")
def member_share_page(token: str):
    """Public member self-service page — no auth check here (member-share.html calls
    /api/member-share/{token} and shows a not-found state if the token is invalid/revoked)."""
    return FileResponse(os.path.join(static_dir, "member-share.html"))
