"""
FastAPI backend for the Home Manager chat app.

Run with:  uvicorn app.main:app --reload
Then open: http://localhost:8000
"""
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import datetime
import logging
import os

from .db import init_db
from .agent import run_agent_turn, generate_chore_recommendations, generate_weekly_plan, fill_in_recipe
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


class MealPreferencesOnboardingRequest(BaseModel):
    dietary_restrictions: dict[str, list[str]] = {}  # member name -> restrictions
    protein_preferences: dict[str, str] = {}
    cuisine_preferences: list[str] = []
    cooking_time_preference: str = ""
    notes: str = ""


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


class MemoryEditRequest(BaseModel):
    field: str  # notes | cooking_time_preference | cuisine_preferences | protein_preferences | goals
    value: object  # str, list[str], or dict depending on field


class MemoryDeleteRequest(BaseModel):
    field: str  # dislikes | cuisine_preferences | protein_preferences | notes | cooking_time_preference
    item: str | None = None


class CheckOffMealRequest(BaseModel):
    entry_id: int
    status: str = "done"  # pending | done


class CheckOffPrepRequest(BaseModel):
    prep_task_id: int
    status: str = "done"  # pending | done


class FillRecipeRequest(BaseModel):
    recipe_name: str


class MemberRestrictionRequest(BaseModel):
    restriction: str


class MemberNoteRequest(BaseModel):
    note: str


class InventoryUpdateRequest(BaseModel):
    item: str
    action: str = "set"  # add | use | remove | set
    quantity: str = ""
    category: str = "other"
    expiration_date: str | None = None


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


@app.post("/api/onboarding/meal-preferences")
def onboarding_meal_preferences(req: MealPreferencesOnboardingRequest):
    """Save meal-planning onboarding answers directly (dietary restrictions per member, protein/cuisine/cooking-time preferences). Marks meal-planning onboarding complete."""
    try:
        for name, restrictions in req.dietary_restrictions.items():
            tools.set_member_dietary_restrictions(name, restrictions, replace=True)
        tools.set_household_meal_preferences(
            notes=req.notes,
            protein_preferences=req.protein_preferences,
            cuisine_preferences=req.cuisine_preferences,
            cooking_time_preference=req.cooking_time_preference,
            mark_complete=True,
        )
    except Exception as e:
        logger.exception("Meal preferences onboarding save failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"saved": True}


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


@app.get("/api/cooker-view")
def get_cooker_view(weekly_plan_id: int | None = None):
    """Everything needed to cook the current (or given) weekly plan — powers the dedicated Cooker view page."""
    try:
        view = tools.get_cooker_view(weekly_plan_id)
    except Exception as e:
        logger.exception("Cooker view lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return view


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


@app.post("/api/cooker/fill-recipe")
def cooker_fill_recipe(req: FillRecipeRequest):
    """Generate and save a full step-by-step for a recipe that's missing one, directly from the Cooker view."""
    try:
        fill_in_recipe(req.recipe_name)
        view = tools.get_cooker_view()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Cooker fill-recipe failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return view


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


@app.post("/api/inventory/update")
def update_inventory_view(req: InventoryUpdateRequest):
    """Add/update an inventory item directly from the Inventory view (not via chat)."""
    try:
        tools.update_inventory(req.item, req.action, quantity=req.quantity, expiration_date=req.expiration_date, category=req.category)
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
    except Exception as e:
        # Log the full error server-side, but return a short, readable
        # message to the browser instead of a raw crash page — the most
        # common cause is a missing/invalid ANTHROPIC_API_KEY.
        logger.exception("Chat turn failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    SESSIONS[req.session_id] = updated_history
    return ChatResponse(reply=reply)


static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/onboarding")
def onboarding_page():
    return FileResponse(os.path.join(static_dir, "onboarding.html"))


@app.get("/memory")
def memory_page():
    return FileResponse(os.path.join(static_dir, "memory.html"))


@app.get("/cooker")
def cooker_page():
    return FileResponse(os.path.join(static_dir, "cooker.html"))


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
