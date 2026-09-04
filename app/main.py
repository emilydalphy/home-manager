"""
FastAPI backend for the Home Manager chat app.

Run with:  uvicorn app.main:app --reload
Then open: http://localhost:8000
"""
from fastapi import FastAPI, HTTPException, File, Form, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel
import asyncio
import base64
import contextvars
import datetime
import json
import logging
import os
import queue
import re
import threading
import time

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exception_handlers import http_exception_handler

from . import agent, backup, households, ratelimit, security
from .db import get_conn, init_db
from .agent import run_agent_turn, trim_conversation, generate_chore_recommendations, generate_weekly_plan, fill_in_recipe, scan_receipt_image, scan_fridge_photo, scan_pantry_photo, AssistantUnavailableError
from . import tools


# Nothing else in the app configures logging, and an unconfigured logger
# only surfaces WARNING and above (Python's "handler of last resort") — so
# without this, every logger.info() call in this app (including the
# per-round token/cache usage in agent.run_agent_turn) is silently dropped
# instead of reaching the terminal.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger("home_manager")

app = FastAPI(title="Home Manager")

# Shared-password gate over every non-public route. Registered before the
# routes below so it wraps all of them, including the /static mount.
app.middleware("http")(security.auth_middleware)


def _route_pattern(request: Request) -> str:
    """
    The matched route's pattern, not the URL that was requested.

    "/api/members/{name}/share-link", never
    "/api/members/Sophia Rodriguez/share-link"; "/api/share/{token}",
    never the live token. Every piece of household data a URL can carry is
    a path parameter, so taking the pattern removes all of them at once —
    including the ones nobody has thought of yet. Redacting known shapes
    individually would mean catching each new route by hand, and missing
    one is how a member's name or a working share link ends up in a table
    whose schema comment promises neither.
    """
    route = request.scope.get("route")
    pattern = getattr(route, "path", None)
    return pattern or "(unmatched)"


@app.exception_handler(StarletteHTTPException)
async def record_server_errors(request: Request, exc: StarletteHTTPException):
    """
    Record any 5xx before it leaves the building.

    Hooked here rather than in each route's except block on purpose: there
    are 84 of those, they already log a traceback, and the thing that keeps
    going wrong is that a *new* one forgets. A handler at the app level
    covers routes nobody has written yet.

    429s are recorded by _enforce_rate_limit itself, which knows which
    bucket was hit; everything below 500 is an ordinary client answer
    (401 not signed in, 404, a rejected input) and is not breakage.
    """
    if exc.status_code >= 500 and _has_bound_household(request):
        # The status code, never the message. 83 routes build their detail
        # as f"Server error: {e}", and {e} is unbounded application text —
        # 27 places in app/tools raise exceptions that interpolate
        # household data ("No saved recipe named '...'", "No household
        # member named '...'"). Only 20 of those routes have an
        # `except ValueError` standing between that text and here, so
        # storing the message would violate this table's stated
        # no-user-content rule by construction rather than by accident.
        # The path is already in where_; the message is in the log.
        await run_in_threadpool(
            tools.record_error, "server", _route_pattern(request), f"HTTP {exc.status_code}"
        )
    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def record_unhandled_errors(request: Request, exc: Exception):
    """
    The genuinely unhandled case — an exception no route caught.

    These are the worst kind to lose, because there is no friendly message
    and no ticket: the browser just gets a bare 500. Recorded by exception
    class name only; the traceback goes to the log as it always did.
    """
    # Bind the household from the cookie before recording.
    #
    # This handler runs in ServerErrorMiddleware, which sits OUTSIDE
    # auth_middleware — and _call_as_household resets the ContextVar in its
    # finally block before the exception gets this far. So household_id()
    # has already fallen back to 1 by now, and without this every crash
    # from every household was filed against Emily's. The beta tester's
    # hard failures are exactly the class this feature exists to surface,
    # and they were landing in the wrong report and missing from theirs.
    #
    # On a public path there is no cookie to recover it from and no binding
    # to recover — see _has_bound_household. A crash there is logged and
    # not recorded, rather than recorded against whoever household 1 is.
    where = _route_pattern(request)
    household = None
    if _has_bound_household(request):
        try:
            parts = security.read_session_parts(request.cookies.get(security.COOKIE_NAME))
            household = parts[1] if parts else None
        except Exception:
            household = None

        def _record():
            # SQLite writes block. These handlers are async, so a direct
            # call would run on the event loop thread and stall every
            # concurrent request under write contention — the same reason
            # security._call_as_household routes its bookkeeping write
            # through the threadpool.
            if household is None:
                tools.record_error("server", where=where, detail=type(exc).__name__)
            else:
                with tools.use_household(household):
                    tools.record_error("server", where=where, detail=type(exc).__name__)

        await run_in_threadpool(_record)

    logger.exception("Unhandled error on %s", where)
    # Return the response rather than re-raising. Starlette's
    # ServerErrorMiddleware expects one back and only sends it if the
    # handler returns; raising from here skipped that, so uvicorn's
    # protocol-level fallback answered instead — same status and body, but
    # the keep-alive connection was dropped on every 500. (The no-index
    # header still isn't applied either way: no_index_headers is a user
    # middleware and sits inside this one, so it never sees this response.
    # A 500 body carries nothing worth not indexing.)
    return PlainTextResponse("Internal Server Error", status_code=500)


@app.middleware("http")
async def no_index_headers(request: Request, call_next):
    """
    Keep the app out of search results. It holds the household's dietary
    notes and members, and even behind a password there is no reason for
    any of it to be crawled or cached by an index.
    """
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


# In-memory chat history: session_id -> conversation.
#
# The session id is minted server-side and carried in the signed login
# cookie (see security.issue_session). It used to come straight off the
# request body with a default of "default", which meant any caller could
# both read and append to the household's conversation, and could grow this
# dict without bound by inventing new ids. Neither is possible now, but the
# TTL and cap below stay as a second line of defence — and they also stop a
# long-lived deploy accumulating history for browsers that never come back.
SESSIONS: dict[str, list[dict]] = {}
SESSION_TOUCHED: dict[str, float] = {}
_SESSION_TTL = 7 * 24 * 60 * 60  # a week without a message and it's dropped
# Per household, deliberately — not across all of them. A single shared cap
# meant one household's busy evening evicted another household's live
# conversation, so the beta tester's assistant could forget mid-conversation
# because Emily happened to be chatting at the same time. Nothing about one
# household's usage should be able to reach into another's. Total sessions
# are now bounded by households x this, which is the intended shape: the cap
# is a per-household guard rail, not a global memory budget. That trade is
# deliberate and fine while households are counted on one hand — but it does
# remove the only global ceiling, and a conversation holds up to 40 turns
# including full tool_result payloads, so it is not small. Revisit and add a
# global backstop (well above this number) if the app ever carries more than
# roughly ten households.
_MAX_SESSIONS_PER_HOUSEHOLD = 50
# Gap since the last message after which the next one counts as "the start
# of a new sitting" for run_agent_turn's proactive_check (see agent.py) —
# long enough that it doesn't re-fire mid-conversation, short enough to
# catch "opened the app again this morning" within the same still-live
# 7-day session, not just a session's literal first-ever message.
_NEW_SITTING_GAP = 4 * 60 * 60


def _session_household(session_key: str) -> str:
    """
    The household part of a chat session key.

    Keys are built by _chat_session_id below as "h<household>:<session id>",
    so the household is everything before the first colon. A key in any
    other shape falls back to itself, which puts it in a bucket of its own
    rather than silently sharing one household's allowance with another —
    the failure mode this split exists to prevent.
    """
    prefix, sep, _ = session_key.partition(":")
    return prefix if sep else session_key


def _prune_sessions() -> None:
    now = time.time()
    # Both sweeps below walk a snapshot (list(...)) rather than the live
    # dict. /api/chat is a `def` route, so Starlette runs it in a
    # threadpool and two households chatting at once really do mutate
    # these dicts underneath a walk — which raises "dictionary changed
    # size during iteration" and 500s somebody's message.
    stale = [sid for sid, seen in list(SESSION_TOUCHED.items()) if now - seen > _SESSION_TTL]
    for sid in stale:
        SESSIONS.pop(sid, None)
        SESSION_TOUCHED.pop(sid, None)
    # Still over the cap (many devices, all active): drop least-recent
    # first, within each household separately. Bucketing by household is
    # the whole point — see _MAX_SESSIONS_PER_HOUSEHOLD above.
    by_household: dict[str, list[str]] = {}
    for sid in list(SESSIONS):
        by_household.setdefault(_session_household(sid), []).append(sid)
    for keys in by_household.values():
        over = len(keys) - _MAX_SESSIONS_PER_HOUSEHOLD
        if over <= 0:
            continue
        keys.sort(key=lambda sid: SESSION_TOUCHED.get(sid, 0.0))
        for sid in keys[:over]:
            SESSIONS.pop(sid, None)
            SESSION_TOUCHED.pop(sid, None)


def _chat_session_id(request: Request) -> str:
    """
    The conversation key for this caller, taken from the signed cookie.

    Prefixed with the household so chat context is isolated too, not just
    stored data. Session ids are random per sign-in and so could not
    collide across households anyway — but the fallback key below is a
    fixed string, and one shared chat history between two households would
    leak one household's dinners into the other's conversation just as
    surely as a bad SQL query would. The prefix makes that structurally
    impossible rather than merely improbable.

    Falls back to a single shared local session when no password is
    configured, which is the `uvicorn --reload` case on a laptop — there is
    exactly one user there and nothing to separate.
    """
    cookie = request.cookies.get(security.COOKIE_NAME)
    parts = security.read_session_parts(cookie)
    if parts:
        sid, household_id = parts
        return f"h{household_id}:{sid}"
    return f"h{tools.household_id()}:local-dev"


def _enforce_rate_limit(request: Request, bucket: str, record: bool = True) -> None:
    wait = ratelimit.check(bucket, ratelimit.caller_id(request))
    if not wait:
        return

    # Always logged: a rejection is worth seeing whoever caused it.
    logger.warning("Rate limit hit on %s (retry in %ss)", bucket, wait)

    # Recorded only where a household is actually bound, and never for the
    # error reporter's own bucket. Both halves matter:
    #
    # - `/login` is a public path, so it reaches here unbound and
    #   household_id() falls back to 1. Recording there let a caller write
    #   rows into Emily's table as fast as they could be refused — the
    #   limiter causing the exact resource consumption it exists to
    #   prevent. See _has_bound_household for why the test is the path and
    #   not the cookie. Failed sign-ins are still logged, which is where
    #   that signal belongs.
    # - Recording the reporter's own rejections replaced each dropped
    #   client-error row with a rate_limit row, so the flood protection
    #   reduced nothing; it just made the flood less informative.
    if record and _has_bound_household(request):
        tools.record_error("rate_limit", where=bucket, detail=f"retry_after={wait}s")

    raise HTTPException(
        status_code=429,
        detail=f"That's a lot of requests at once — give it about {wait} seconds and try again.",
    )


def _has_bound_household(request: Request) -> bool:
    """
    Is there a household these errors honestly belong to?

    Only on a non-public path. `auth_middleware` binds the household from
    the signed cookie there and *nowhere else* — a public path (`/login`, a
    share link, a static file) is deliberately left unbound, so it reaches
    the error handlers with `household_id()` sitting on its default of 1,
    i.e. Emily's.

    This asks about the path and not about the cookie, and the difference
    was a real hole rather than a tidiness point. A cookie check answers
    "is this caller signed in?", which is true for the beta tester on every
    path — including the public ones where her household is not bound. So
    she (or anyone holding her passphrase) could POST wrong passphrases at
    `/login` and every rejection wrote a row into *household 1's* table,
    with no ceiling but request rate: 25 rows from 25 requests, measured.
    At `_KEEP_ROWS` the prune starts evicting Emily's genuine errors — the
    same eviction attack the public reporting endpoint opened, now needing
    one valid cookie instead of none. It also fired by accident: a tester
    mistyping her passphrase ten times filed ten rows into Emily's morning
    report.

    Public-path failures are still logged, and a 5xx there still returns a
    500 as it always did. They are just not written down, because there is
    no true answer to "whose?" and a confident wrong answer sends Emily
    hunting through her own share links for somebody else's bug.
    """
    # scope["path"] for the same reason auth_middleware uses it: a "#" or
    # "?" inside the path truncates request.url.path, so the two would
    # otherwise disagree about which requests are public.
    return not security.is_public_path(request.scope["path"])


class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str


class ChatAction(BaseModel):
    """
    One "this changed, go look" action card under an assistant reply — the
    app-shell redesign's fix for chat answers that dead-end (README problem
    #4 / Step 3). Exactly one of `tab` (a shell tab key: today/week/grocery/
    kitchen — client-side route, no reload) or `href` (a real page, for
    changes that don't land on one of the four shell tabs yet, e.g.
    household info still living at /memory) is set.
    """
    kicker: str
    change: str
    tab: str | None = None
    href: str | None = None


class ChatResponse(BaseModel):
    reply: str
    actions: list[ChatAction] = []


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


class OnboardingRhythmRequest(BaseModel):
    """
    The six locked household-rhythm questions (Loop Board "Onboarding:
    household rhythm without traditional assumptions", Emily's "LOCKED: the
    six rhythm questions" note, 2026-09-03). lunch_location maps each
    member's name to 'home'/'out'/'varies' — the wizard sends one entry per
    household member. cooking_role_who is only read when cooking_role is
    'one_person'.
    """
    lunch_location: dict[str, str] = {}
    meals_together: str = ""
    cooking_role: str = ""
    cooking_role_who: str = ""
    dinner_window: str = ""
    planning_anchor: str = ""
    leftovers_stance: str = ""


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


class ResolveDinnerRequest(BaseModel):
    date: str
    meal: str
    # The answer to the card's "want the ingredients on your grocery list?"
    # step. False when the person said no — never a silent default yes.
    add_ingredients: bool = False


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
    status: str = "done"  # pending | done | skipped


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
    # design_handoff_home_manager Phase 2: which household adult added this,
    # from the client-side identity switcher (see static/grocery.html) —
    # optional and defaults to the old unattributed "user" so every existing
    # caller of this endpoint keeps working unchanged.
    added_by: str = "user"


class GroceryUpdateRequest(BaseModel):
    quantity: str | None = None
    category: str | None = None


class GroceryStatusRequest(BaseModel):
    status: str = "purchased"  # needed | in_cart | purchased


class GroceryStoreRequest(BaseModel):
    store: str = ""


class GroceryPreShopRequest(BaseModel):
    decision: str  # "keep" | "drop"
    # Which household adult made the call, from the same client-side
    # identity switcher GroceryAddRequest.added_by uses — see
    # tools.drop_grocery_item_pre_shop.
    author: str = "user"


class ResetRequest(BaseModel):
    """
    Which of the two self-service resets to run — see POST /api/reset.
    Both default to False so a malformed or empty body deletes nothing;
    the route rejects "neither" rather than treating it as "both".
    """
    meal_plan: bool = False
    grocery_list: bool = False


@app.on_event("startup")
def startup():
    init_db()
    # Says in the logs, on day one, whether this database will survive a
    # redeploy — rather than leaving that discoverable only by losing it.
    backup.warn_if_database_is_ephemeral()


@app.on_event("startup")
async def start_backup_loop():
    """
    Take a snapshot of the database daily.

    An in-process loop because there is nothing else: the container runs
    one command (uvicorn) with no cron, no scheduler and no sidecar, so
    "a nightly copy" has to live somewhere in here or not exist. The
    trade-off is honest -- if the app is down, no backup is taken that
    day; if it restarts, the day's snapshot is simply retaken, since they
    are keyed by date.

    The work runs in a threadpool because SQLite writes block, and a
    minute of copying on the event loop would stall every request.
    """
    if os.environ.get("DISABLE_BACKUPS") == "1":
        logger.info("Database backups are disabled for this process (DISABLE_BACKUPS=1)")
        return

    async def _loop():
        while True:
            try:
                result = await run_in_threadpool(backup.run_daily_maintenance)
                logger.info(
                    "Backup maintenance: created=%s pruned=%d kept=%d",
                    result["created"], len(result["pruned"]), result["kept"],
                )
            except Exception:
                # The loop must outlive any single bad day. A backup task
                # that dies quietly would leave the household uncovered
                # with nothing to show for it.
                logger.exception("Backup maintenance failed; will try again tomorrow")
            await asyncio.sleep(24 * 60 * 60)

    asyncio.create_task(_loop())


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


@app.post("/api/onboarding/rhythm")
def onboarding_rhythm(req: OnboardingRhythmRequest):
    """
    Save the six locked household-rhythm questions in one call — lunch
    location per person, meals eaten together, who cooks, when dinner
    lands, when the week should be ready, and the household's leftovers
    stance (Loop Board "Onboarding: household rhythm without traditional
    assumptions"). Called directly by the onboarding wizard's two rhythm
    steps, placed after household members and before the food questions
    per Emily's stated learning hierarchy (rhythm before habits before
    preferences). The same six facts are also settable/correctable via
    chat at any time through the underlying rhythm tools (set_lunch_location,
    etc.) — this endpoint is just the structured-form path onto the same
    storage, so an onboarding answer and a later chat correction are the
    same write.
    """
    try:
        for member_name, location in req.lunch_location.items():
            member_name = (member_name or "").strip()
            if member_name and location in tools.LUNCH_LOCATIONS:
                tools.set_lunch_location(member_name, location, source="onboarding")
        if req.meals_together:
            tools.set_meals_together(req.meals_together, source="onboarding")
        if req.cooking_role:
            tools.set_cooking_role(req.cooking_role, who=req.cooking_role_who, source="onboarding")
        if req.dinner_window:
            tools.set_dinner_window(req.dinner_window, source="onboarding")
        if req.planning_anchor:
            tools.set_planning_anchor(req.planning_anchor, source="onboarding")
        if req.leftovers_stance:
            tools.set_leftovers_stance(req.leftovers_stance, source="onboarding")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Onboarding rhythm save failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return tools.get_household_rhythm()


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
    screen. The week is keyed to its Monday, like every other week in the
    app.

    This used to start the week on whatever day onboarding happened, which
    was the only place in the codebase writing a non-Monday key. A week is
    not a rolling seven days here — it is a specific Monday, and the front
    end computes that key by subtracting the weekday from today. So a
    household onboarding on a Wednesday got a plan filed under Wednesday
    while every screen went looking for Monday: the Meals tab offered to
    "Plan this week" for a week it was already showing, taking that offer
    created a second overlapping plan, and chat judged the plan stale and
    wanted to rebuild it. Fixed 2026-09-02 by snapping the filing key to
    Monday (see git history) — that part stays.

    What changed here (Loop Board "Build a real part-week for households
    who onboard mid-week"): the week is still FILED under Monday, but its
    CONTENT now actually starts today rather than at the week's Monday.
    Onboarding on a Wednesday used to mean a plan whose first two days had
    already gone by; now it means a 5-day plan running Wednesday-Sunday,
    correctly filed under that week's Monday so every other screen still
    finds it. See generate_weekly_plan's skip_days parameter for the
    mechanics, and _prorate_meal_count for how "4 dinners a week" scales
    down to fit fewer days.

    Floor rule for the degenerate case (flagged as a judgment call, not a
    technical necessity — Emily's to revisit): onboarding on a Sunday would
    otherwise produce a 1-day part-week, which is a lot of new machinery
    (its own grocery scoping, its own reveal) for a single dinner. Instead,
    a 1-day part-week skips straight to a normal, full 7-day plan starting
    the very next day (Monday) — the household's first plan is a whole
    real week rather than a token one, at the cost of tonight's dinner not
    being covered by "the plan" at all. A 2-day part-week (Saturday
    onboarding) is left as a genuine part-week rather than folded in the
    same way — two real days felt worth planning for rather than skipping.
    """
    try:
        today = datetime.date.today()
        monday = today - datetime.timedelta(days=today.weekday())
        skip_days = today.weekday()  # 0=Monday .. 6=Sunday
        day_count = 7 - skip_days
        if day_count <= 1:
            # Sunday: fold forward into next week's full plan rather than
            # generating a 1-day part-week. See the floor-rule note above.
            monday = monday + datetime.timedelta(days=7)
            skip_days = 0
            day_count = 7
        week_start = monday.isoformat()
        plan = generate_weekly_plan(week_start, day_count=day_count, skip_days=skip_days)
    except AssistantUnavailableError as e:
        logger.warning("First-plan generation hit a transient Claude API failure: %s", e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("First-plan generation during onboarding failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return plan


@app.post("/api/onboarding/generate-first-plan/stream")
def onboarding_generate_first_plan_stream():
    """
    Streaming twin of /api/onboarding/generate-first-plan (see it for the
    week-key rationale) -- reuses the exact same _stream_week_generation
    machinery the Meals draft screen's /api/week/{week_start}/generate/stream
    already uses (Loop Board "Redesign the post-onboarding first sample
    week screen": the reveal was still a single ~30-second blocking call
    while the rest of the app had already moved to progressive per-day
    streaming). Same generation, same saved plan, same side effects
    (generate_weekly_plan is the one thing both endpoints call) -- the
    only difference is the reveal can now show each day landing instead
    of one long silence. The plain endpoint stays as the tested,
    unstreamed path other callers (and the existing onboarding tests)
    still use, same rationale as generate_week_stream's own docstring.
    """
    today = datetime.date.today()
    week_start = (today - datetime.timedelta(days=today.weekday())).isoformat()
    return StreamingResponse(
        _stream_week_generation(week_start=week_start, constraints_notes="", intake_id=None),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
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
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
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
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
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
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Chore status update failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.get("/api/prep/defrost-today")
def defrost_today():
    """
    Pending defrost tasks due today — powers the app-shell Today screen's
    defrost tile (Loop Board "First-class 'defrost' prep step"), the same
    small direct-read-endpoint pattern /api/chores/today established just
    above. Marking one done/skipped reuses the existing
    /api/cooker/check-prep endpoint (tools.check_off_prep_step accepts
    'skipped' now too) rather than adding a second write endpoint for the
    same table.
    """
    try:
        tasks = tools.get_defrost_today()
    except Exception as e:
        logger.exception("Today's-defrost lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"tasks": tasks}


@app.get("/api/week-menu")
def week_menu(weekly_plan_id: int | None = None):
    """
    The always-7-day, three-slot (breakfast/lunch/dinner) weekly menu —
    powers the app-shell Week tab (design_handoff_shell/README.md §5). This
    is the one new endpoint that README §10 pre-authorizes ("no new
    endpoint other than the three-slot meal plan"). See
    tools.get_week_menu for the title/meta/source derivation.
    """
    try:
        menu = tools.get_week_menu(weekly_plan_id)
    except Exception as e:
        logger.exception("Week-menu lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return menu


# ---------- Plan the Week (design_handoff_plan_the_week) ----------
# Week-scoped routes, keyed by the week's Monday rather than by plan id,
# per DATA_AND_API.md's endpoint table. A week is the thing the household
# talks about ("Sep 1–7"); the plan id is an implementation detail the
# screens shouldn't have to carry around.

class WeekApproveRequest(BaseModel):
    approved_by: str = ""


class WeekIntakeRequest(BaseModel):
    """
    Whichever answers this screen collected. Every field is optional and
    None means "not this screen's business" — Q1 sends nights, guests and
    packed lunches; Q2 sends moods, cuisines and the freeform share. Each
    saves a new intake revision without clobbering the other's half. See
    tools.save_week_intake.
    """
    night_tags: dict | None = None
    guest_counts: dict | None = None
    packed_lunch_days: list | None = None
    moods: list | None = None
    cuisines: list | None = None
    # The length of the period these answers are for. Defaults to the seven
    # every existing client sends nothing about; it widens the in-range check
    # on night_tags, which would otherwise refuse a tag on the eighth day of
    # an eight-day period and stop the flow dead.
    day_count: int = 7
    freeform: str | None = None
    created_by: str = ""


class WeekGenerateRequest(BaseModel):
    intake_id: int | None = None
    constraints_notes: str = ""
    # The planning period (Loop Board "Planning periods, not weeks"). Both
    # default to the traditional shape: seven days beginning at the
    # {week_start} in the path, which is exactly what every existing caller
    # sends and gets. period_start is only needed when the content window
    # starts somewhere other than the filing key, which the UI does not
    # currently produce (it files a custom period under its own first day)
    # but the API allows, since a plan's filing key and its first day are
    # genuinely two different things.
    day_count: int = 7
    period_start: str | None = None


def _validated_period(week_start: str, req: WeekGenerateRequest) -> tuple[str, int]:
    """
    The period a generate request is asking for, refused at the door if it
    isn't one. Shared by the plain and streaming endpoints so a request the
    JSON endpoint rejects can't be smuggled past the SSE one -- where the
    same failure would arrive as an `error` frame after a 200 and a set of
    headers, i.e. as a stream that starts fine and then says no.
    """
    try:
        datetime.date.fromisoformat(week_start)
        if req.period_start:
            datetime.date.fromisoformat(req.period_start)
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must be ISO dates (YYYY-MM-DD).")
    if req.day_count < 1 or req.day_count > tools.MAX_PERIOD_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"A planning period has to be between 1 and {tools.MAX_PERIOD_DAYS} days.",
        )
    return (req.period_start or week_start), req.day_count


def _plan_id_for_week(week_start: str) -> int:
    """
    Resolve a week's Monday to its weekly_plans row id, 404ing if that week
    has no plan. Kept here rather than in each route so every week-scoped
    endpoint fails the same way for the same reason.
    """
    try:
        datetime.date.fromisoformat(week_start)
    except ValueError:
        raise HTTPException(status_code=400, detail="week_start must be an ISO date (YYYY-MM-DD).")
    plan_id = tools.get_plan_id_for_week(week_start)
    if plan_id is None:
        raise HTTPException(status_code=404, detail=f"No plan for the week of {week_start}.")
    return plan_id


class MealPlanningPreferenceRequest(BaseModel):
    """One field at a time — the setup screen saves each control as it's
    touched, so nothing is lost by leaving the page."""
    field: str
    value: object


@app.get("/api/preferences/meal-planning")
def meal_planning_preferences():
    """Everything the revisitable setup screen shows and can edit."""
    try:
        return tools.get_meal_planning_preferences()
    except Exception as e:
        logger.exception("Meal planning preference lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")


@app.patch("/api/preferences/meal-planning")
def update_meal_planning_preferences(req: MealPlanningPreferenceRequest):
    """
    Change one preference. Routed through tools.edit_preference, the same
    entry point chat uses, so a change made on this screen and a change made
    by talking to the assistant are the same write with the same validation
    and the same preference-event log line.
    """
    try:
        tools.edit_preference(req.field, req.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Meal planning preference update failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return tools.get_meal_planning_preferences()


@app.get("/api/week/plan-nudge")
def week_plan_nudge():
    """
    Whether to offer to plan a week on Today, and which one. Dismissing it
    goes through the existing /api/notifications/dismiss with the returned
    dismiss_key — the key is the week itself, so "I won't ask again this
    week" is literally true and next week's offer isn't silenced with it.
    """
    try:
        return tools.get_week_planning_nudge()
    except Exception as e:
        logger.exception("Week planning nudge lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")


@app.get("/api/week/planning-period")
def week_planning_period():
    """
    Where this household's "this week" starts, and how long it runs — the
    default the plan screen opens on.

    A separate call rather than a field on the nudge because the permanent
    "Plan a week" entry needs it too, and that entry is shown whether or not
    the nudge is. Declared above the /api/week/{week_start}/... routes on
    purpose: FastAPI matches in declaration order, and "planning-period"
    would otherwise be swallowed as a {week_start} that isn't a date.
    """
    try:
        return tools.suggest_planning_period()
    except Exception as e:
        logger.exception("Planning period suggestion failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")


@app.get("/api/week/{week_start}/intake")
def week_intake_prefill(week_start: str, day_count: int = 7):
    """
    Everything the two question screens need to open already knowing what
    the app knows: each day's hint from What We Know and observed history,
    the household's own saved cuisines, its composition for the guest
    maths, and any intake already in flight for this week (so the second
    adult to start joins the first one's answers instead of a blank set).
    """
    try:
        datetime.date.fromisoformat(week_start)
    except ValueError:
        raise HTTPException(status_code=400, detail="week_start must be an ISO date (YYYY-MM-DD).")
    if day_count < 1 or day_count > tools.MAX_PERIOD_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"A planning period has to be between 1 and {tools.MAX_PERIOD_DAYS} days.",
        )
    try:
        return tools.get_week_intake_prefill(week_start, day_count=day_count)
    except Exception as e:
        logger.exception("Week intake prefill failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")


@app.post("/api/week/{week_start}/intake")
def save_week_intake_route(week_start: str, req: WeekIntakeRequest):
    """
    Save the household's answers as a NEW intake revision. Append-only —
    see tools.save_week_intake for why nothing is ever updated in place.
    """
    try:
        return tools.save_week_intake(
            week_start,
            night_tags=req.night_tags,
            guest_counts=req.guest_counts,
            packed_lunch_days=req.packed_lunch_days,
            moods=req.moods,
            cuisines=req.cuisines,
            freeform=req.freeform,
            created_by=req.created_by,
            day_count=req.day_count,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Week intake save failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")


@app.post("/api/week/{week_start}/generate")
def generate_week(week_start: str, req: WeekGenerateRequest):
    """
    Draft a period from the household's answers — seven days from
    {week_start} unless the body says otherwise. Adds NOTHING to the grocery
    list — a draft is not a yes; approving is (see approve_week).

    A period that overlaps an existing plan takes those days over (Emily's
    one-plan-per-day rule); the response's `took_over` says what that cost
    the shopping list, including which lines were left alone because
    somebody had already bought them.
    """
    period_start, day_count = _validated_period(week_start, req)
    try:
        plan = generate_weekly_plan(
            week_start,
            constraints_notes=req.constraints_notes,
            intake_id=req.intake_id,
            day_count=day_count,
            period_start=period_start,
        )
    except AssistantUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Week generation failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return plan


def _sse_event(event: str, data) -> str:
    """
    One Server-Sent Events frame. `event` names what kind of thing this
    is (the browser dispatches on it); `data` is JSON-encoded the same way
    every other endpoint's response body already is.

    Routed through jsonable_encoder first -- the same conversion FastAPI
    runs automatically on a normal `response_model` return value -- rather
    than handing `data` straight to json.dumps. A plain json.dumps call
    only understands JSON's own primitive types, so any pydantic model
    tucked inside `data` (e.g. the ChatAction objects _finish_chat_turn
    puts in the "done" event's `actions` list) raised "Object of type X is
    not JSON serializable" and aborted the stream after headers were
    already sent -- the browser never saw the reply OR the action card,
    even though the underlying tool call (e.g. adding a grocery item) had
    already happened. jsonable_encoder also handles datetimes, Decimal,
    dataclasses, etc. generically, so any other non-primitive value that
    ends up in a streamed payload is covered the same way, not just
    ChatAction specifically.
    """
    return f"event: {event}\ndata: {json.dumps(jsonable_encoder(data))}\n\n"


def _stream_week_generation(
    *, week_start: str, constraints_notes: str, intake_id,
    day_count: int = 7, period_start: str | None = None,
):
    """
    Run generate_weekly_plan on a background thread and yield its progress
    as Server-Sent Events: an immediate "status" event (so the browser has
    something to show within ~2 seconds of the request landing, not ~37
    seconds of silence), one "day" event per day/item as the model finishes
    deciding it, and a final "done" (or "error") event carrying exactly
    what the plain /generate endpoint would have returned.

    Why a background thread rather than just iterating inside this
    generator: generate_weekly_plan is a normal blocking function --
    dozens of lines of DB writes threaded through the LLM call, not
    something written to yield control. The only way to interleave "make
    progress" and "tell the browser about it" without rewriting all of
    that as an async/generator pipeline is to run it on its own thread and
    relay events through a queue, which is what this does. The generation
    thread runs inside a copied context (contextvars.copy_context) so it
    sees the same household_id the request itself was scoped to --
    without that, the calls it makes on this new thread would silently
    fall back to the default household.
    """
    events: queue.Queue = queue.Queue()
    _DONE = object()

    def on_item(item):
        events.put(("day", item))

    def run():
        token = agent._WEEK_GEN_PROGRESS.set(on_item)
        try:
            plan = generate_weekly_plan(
                week_start, constraints_notes=constraints_notes, intake_id=intake_id,
                day_count=day_count, period_start=period_start,
            )
            events.put(("done", plan))
        except AssistantUnavailableError as e:
            events.put(("error", {"status": 503, "detail": str(e)}))
        except ValueError as e:
            events.put(("error", {"status": 400, "detail": str(e)}))
        except Exception as e:
            logger.exception("Streaming week generation failed")
            events.put(("error", {"status": 500, "detail": f"Server error: {e}"}))
        finally:
            agent._WEEK_GEN_PROGRESS.reset(token)
            events.put(_DONE)

    ctx = contextvars.copy_context()
    threading.Thread(target=lambda: ctx.run(run), daemon=True).start()

    yield _sse_event("status", {"message": "Drafting your week…"})
    while True:
        item = events.get()
        if item is _DONE:
            return
        event_name, payload = item
        yield _sse_event(event_name, payload)


@app.post("/api/week/{week_start}/generate/stream")
def generate_week_stream(week_start: str, req: WeekGenerateRequest):
    """
    Streaming twin of /api/week/{week_start}/generate (see it for what
    this actually does) -- same generation, same result, but the plan-week
    screen can show progress instead of a single ~37-second spinner. Kept
    as a separate endpoint rather than replacing /generate outright:
    onboarding's first-plan route calls generate_weekly_plan directly
    (never through either HTTP endpoint), so nothing else depends on
    /generate changing shape, and a caller with no interest in progress
    (a future integration, a script) can keep using the plain JSON one.

    Streams a period of any length -- the per-day `day` events are emitted
    by the generation itself, so nothing here counts to seven. The period is
    validated BEFORE the StreamingResponse is constructed, so a bad request
    is a plain 400 rather than a 200 whose body immediately says no.
    """
    period_start, day_count = _validated_period(week_start, req)
    return StreamingResponse(
        _stream_week_generation(
            week_start=week_start, constraints_notes=req.constraints_notes, intake_id=req.intake_id,
            day_count=day_count, period_start=period_start,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class WeekSlotRequest(BaseModel):
    date: str
    slot: str = "dinner"
    choice: str


@app.post("/api/week/{week_start}/slot")
def resolve_week_slot(week_start: str, req: WeekSlotRequest):
    """
    Settle one slot — an open night the app handed back, or a swap. In a
    draft this leaves the shopping list alone; in an approved week it keeps
    the list in step, same rule as a swap.
    """
    plan_id = _plan_id_for_week(week_start)
    if req.slot not in tools.WEEK_SLOTS:
        raise HTTPException(status_code=400, detail=f"slot must be one of {', '.join(tools.WEEK_SLOTS)}.")
    try:
        return tools.resolve_open_slot(plan_id, req.date, req.slot, req.choice)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Slot resolution failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")


class SlotAttendanceRequest(BaseModel):
    """
    One presence gesture. Either form is valid, and the screen sends
    whichever it made:
      - `member` + `present` — one avatar tapped (the common case).
      - `guest_count` — the guests stepper.
    Both may be sent together; anything omitted is left as it was.
    """
    date: str
    slot: str = "dinner"
    member: str | None = None
    present: bool | None = None
    guest_count: int | None = None


class AwayStretchRequest(BaseModel):
    """
    The trip range gesture. `member_names` empty/None means the whole
    household — the common case and the pre-attendance meaning.
    """
    from_date: str
    from_slot: str = "dinner"
    to_date: str
    to_slot: str = "dinner"
    reason: str = ""
    member_names: list | None = None


class SlotRecommendationRequest(BaseModel):
    """Confirm (or decline) a ready-made recommendation for one slot."""
    date: str
    slot: str = "dinner"
    confirmed: bool = True


@app.get("/api/week/{week_start}/attendance")
def week_attendance(week_start: str):
    """
    Everything the presence UI needs for one week in a single round trip:
    the household's people (so avatars can be drawn before anything is
    tapped), the meals whose attendance differs from everyone-being-home,
    and the derived slot needs those produced.

    Deliberately NOT plan-scoped — attendance is declared at intake time,
    usually before a plan for that week exists at all, exactly like
    week_intake.night_tags. So this must not 404 on "no plan yet".
    """
    try:
        datetime.date.fromisoformat(week_start)
    except ValueError:
        raise HTTPException(status_code=400, detail="week_start must be an ISO date (YYYY-MM-DD).")
    try:
        return {
            "week_start": week_start,
            "members": tools.list_members(),
            "attendance": tools.get_week_attendance(week_start),
            "slot_needs": tools.get_week_slot_needs(week_start),
        }
    except Exception as e:
        logger.exception("Week attendance lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")


@app.post("/api/week/{week_start}/attendance")
def set_week_attendance(week_start: str, req: SlotAttendanceRequest):
    """
    Record one presence gesture. Returns the resulting attendance for that
    slot — including its `summary` line and whether the change made the
    meal away — so the screen can render the truth it just wrote rather
    than re-deriving it and risking a different answer.
    """
    try:
        result = None
        if req.member is not None:
            result = tools.set_member_attendance(
                req.date, req.slot, req.member, present=bool(req.present),
            )
        if req.guest_count is not None:
            result = tools.set_guest_count(req.date, req.slot, req.guest_count)
        if result is None:
            raise HTTPException(status_code=400, detail="Send either a member to toggle or a guest_count.")
        result["summary"] = tools.attendance_summary_line(result)
        result["slot_need"] = tools.get_slot_need(req.date, req.slot)
        return result
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Attendance save failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")


@app.post("/api/week/{week_start}/away-stretch")
def set_week_away_stretch(week_start: str, req: AwayStretchRequest):
    """
    The trip range — "away from Saturday lunch, back for Sunday dinner" —
    optionally scoped to specific travelers. Returns which slots emptied,
    which merely shrank, and the two derived edges, which is exactly what
    the picker's confirmation copy reports back.
    """
    try:
        return tools.set_away_stretch(
            req.from_date, req.from_slot, req.to_date, req.to_slot,
            reason=req.reason, member_names=req.member_names,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Away stretch save failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")


@app.post("/api/week/{week_start}/slot-recommendation")
def confirm_week_slot_recommendation(week_start: str, req: SlotRecommendationRequest):
    """
    The household's yes/no on a ready-made recommendation. Emily's standing
    rule: the system recommends, the household confirms — nothing acts on a
    recommendation until this flips.
    """
    try:
        return tools.confirm_slot_recommendation(req.date, req.slot, confirmed=req.confirmed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Recommendation confirmation failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")


@app.post("/api/week/{week_start}/reopen")
def reopen_week(week_start: str):
    """
    Reopen an approved week for editing (DECISIONS.md #2). Never removes
    anything from the shopping list — re-approving only adds what's new.
    """
    plan_id = _plan_id_for_week(week_start)
    try:
        return tools.reopen_weekly_plan(plan_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Week reopen failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")


@app.post("/api/week/{week_start}/approve")
def approve_week(week_start: str, req: WeekApproveRequest):
    """
    Approve a week — the button that IS the yes. Approval is what builds
    the grocery list (tools.approve_weekly_plan); nothing reaches that list
    before it.

    The two counts describe THIS WEEK'S RECEIPT, not what this particular
    request did: how many items the approval put on the shopping list, and
    how many it left off as already in the household's kitchen. On a
    re-approval (which adds nothing — see approve_weekly_plan's guards)
    they are still the original approval's numbers, because that is what
    the receipt on screen says and it should not reset to zero just
    because someone tapped again. `was_already_approved` is the field that
    says whether this call actually did anything.
    """
    plan_id = _plan_id_for_week(week_start)
    try:
        result = tools.approve_weekly_plan(plan_id, approved_by=req.approved_by)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Week approval failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {
        "week_start": week_start,
        "weekly_plan_id": result["weekly_plan_id"],
        "status": result["status"],
        "approved_by": result["approved_by"],
        "approved_at": result["approved_at"],
        "was_already_approved": result["was_already_approved"],
        "groceries_added": result["groceries_added_count"],
        "already_have_skipped": result["already_have_skipped_count"],
    }


@app.get("/api/reset/preview")
def reset_preview():
    """
    Counts for the Meals tab's "Start over" confirm dialog — how many
    planned meals and how many still-needed grocery items a reset would
    remove — so the dialog can name real numbers and grey out a choice
    that would do nothing. Read-only; see tools.get_reset_preview.
    """
    try:
        return tools.get_reset_preview()
    except Exception as e:
        logger.exception("Reset preview failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")


@app.post("/api/reset")
def reset(req: ResetRequest):
    """
    The self-service reset behind the Meals tab's "Start over" dialog:
    clear this week's meal plan, clear the grocery list, or both. Narrow on
    purpose — it touches nothing else the household owns (recipes, chores,
    members, inventory, memory). Wiping all of that is reset_household.py,
    an admin script with no in-app entry point.

    Order matters when both are asked for: the plan goes first, so its
    per-meal grocery reversals (tools.clear_weekly_plan) are already
    reflected in what the list clear then removes — the other way round
    would reverse contributions against rows that no longer exist.
    """
    if not req.meal_plan and not req.grocery_list:
        raise HTTPException(status_code=400, detail="Nothing selected to reset.")
    result = {"meal_plan": None, "grocery_list": None}
    try:
        if req.meal_plan:
            result["meal_plan"] = tools.clear_weekly_plan()
        if req.grocery_list:
            result["grocery_list"] = tools.clear_grocery_list(status="needed")
    except Exception as e:
        logger.exception("Reset failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    logger.info(
        "Self-service reset: meal_plan=%s grocery_list=%s",
        result["meal_plan"] and result["meal_plan"]["meals_cleared"],
        result["grocery_list"] and result["grocery_list"]["removed_count"],
    )
    return result


@app.get("/api/needs-you")
def needs_you():
    """
    The Today screen's needs-you band (design_handoff_shell/README.md §4,
    Step 5 of the app-shell build order) — up to a couple of cards for
    things that need a decision right now: an undecided dinner within 48
    hours, and a shop run needed before an upcoming meal. New endpoint,
    same deviation-from-§10 category as /api/chores/today (Step 2) —
    flagged to you before building, approved. See tools.get_needs_you_items
    for the two hardcoded rules this starts with.
    """
    try:
        items = tools.get_needs_you_items()
    except Exception as e:
        logger.exception("Needs-you lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"items": items}


@app.post("/api/needs-you/dinner")
def resolve_needs_you_dinner(req: ResolveDinnerRequest):
    """
    Resolve a needs-you dinner-decision card by planning the picked meal
    (via tools.plan_meal), then return the refreshed needs-you list.

    req.add_ingredients carries the answer to the card's confirm step, so
    the grocery list is only written to when the person actually said yes
    — same rule the assistant follows in chat.
    """
    try:
        result = tools.resolve_needs_you_dinner(
            req.date, req.meal, add_ingredients_to_grocery_list=req.add_ingredients
        )
    except Exception as e:
        logger.exception("Needs-you dinner resolve failed")
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
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Inventory remove failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


class InventoryQuantityStepRequest(BaseModel):
    delta: float = 1


class InventoryLocationRequest(BaseModel):
    location: str


class InventoryExpirationStepRequest(BaseModel):
    delta_days: int = 1


@app.post("/api/inventory/{item_id}/quantity")
def step_inventory_quantity_view(item_id: int, req: InventoryQuantityStepRequest):
    """Nudge an inventory item's quantity — the item detail sheet's +/- stepper (design_handoff_home_manager §6)."""
    try:
        result = tools.step_inventory_quantity(item_id, req.delta)
    except Exception as e:
        logger.exception("Inventory quantity step failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/inventory/{item_id}/location")
def set_inventory_location_view(item_id: int, req: InventoryLocationRequest):
    """Move an inventory item to a different storage location — the item detail sheet's location picker (design_handoff_home_manager §6)."""
    try:
        result = tools.set_inventory_location(item_id, req.location)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Inventory location change failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/inventory/{item_id}/expiration")
def step_inventory_expiration_view(item_id: int, req: InventoryExpirationStepRequest):
    """Shift an inventory item's best-before date by one day per tap (design_handoff_home_manager §6)."""
    try:
        result = tools.step_inventory_expiration(item_id, req.delta_days)
    except Exception as e:
        logger.exception("Inventory expiration step failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


class FactAddRequest(BaseModel):
    category: str
    text: str
    hard: bool = False
    author: str = ""


class FactUpdateRequest(BaseModel):
    text: str | None = None
    hard: bool | None = None


@app.get("/api/facts")
def get_facts_view(category: str | None = None):
    """
    Household facts for the What We Know screen (design_handoff_home_manager
    §7), optionally filtered to one tab's category. For the People tab
    specifically, also includes `onboarding` -- household members and
    dietary dislikes captured during onboarding, which live in the
    members/meal_preferences tables, not the freeform `facts` table this
    route otherwise reads. Without this, People only ever showed things a
    user had separately told the chat assistant to "remember" -- never what
    onboarding already collected.
    """
    try:
        result = tools.get_facts(category=category)
        onboarding = None
        preferences = None
        if category == "people":
            setup = tools.get_meal_planning_setup_status()
            onboarding = {"members": setup["members"], "household_dislikes": setup["dislikes"]}
        if category == "taste":
            # Eating style and the cuisines someone said they were excited
            # about are collected during onboarding and then drive every
            # week the app plans — but until now they were editable
            # nowhere at all, and not even shown. Chat could change them;
            # nothing else could. They live in meal_preferences rather than
            # the freeform `facts` table this route otherwise reads, which
            # is the same reason the People tab needs its `onboarding`
            # block above.
            memory = tools.get_household_memory()
            preferences = {
                "eating_style": memory.get("eating_style") or "",
                "cuisines": memory.get("cuisine_preferences") or [],
            }
    except Exception as e:
        logger.exception("Facts lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"facts": result, "onboarding": onboarding, "preferences": preferences}


@app.post("/api/facts/add")
def add_fact_view(req: FactAddRequest):
    """Add one freeform fact — What We Know's '+ Add something to this list'."""
    try:
        result = tools.add_fact(req.category, req.text, hard=req.hard, author=req.author)
    except Exception as e:
        logger.exception("Fact add failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/facts/{fact_id}/update")
def update_fact_view(fact_id: int, req: FactUpdateRequest):
    """Edit a fact's text/hard flag in place — What We Know's inline editor."""
    try:
        result = tools.update_fact(fact_id, text=req.text, hard=req.hard)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Fact update failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/facts/{fact_id}/delete")
def delete_fact_view(fact_id: int):
    """Delete a fact outright."""
    try:
        result = tools.delete_fact(fact_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Fact delete failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


class NotificationDismissRequest(BaseModel):
    key: str


@app.get("/api/notifications")
def get_notifications_view():
    """Live 'what needs your attention' feed — powers the shell's notification bell (design_handoff_home_manager Phase 5 / NOTIFICATIONS.md)."""
    try:
        result = tools.get_active_notifications()
    except Exception as e:
        logger.exception("Notifications lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"notifications": result}


@app.post("/api/notifications/dismiss")
def dismiss_notification_view(req: NotificationDismissRequest):
    """Dismiss one notification by key so it stops showing until its underlying condition changes."""
    try:
        result = tools.dismiss_notification(req.key)
    except Exception as e:
        logger.exception("Notification dismiss failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.get("/api/grocery-list")
def get_grocery_list_view(status: str = "needed"):
    """
    Grocery list grouped by store section — powers the dedicated Grocery
    List view page. status: needed | in_cart | purchased | excluded | all.
    For 'needed', items flagged by get_pre_shop_flags (not yet reviewed)
    are left out here too — they're shown separately in the Grocery
    screen's pinned "Maybe already home" pre-shop check instead, so
    nothing appears twice.
    """
    try:
        result = tools.get_grocery_list_by_section(status=status)
        if status == "needed":
            already_have_ids = {it["itemId"] for it in tools.get_pre_shop_flags()}
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
    Grocery List view's 'By store' toggle. Same pre-shop-flag filtering as
    the main /api/grocery-list endpoint for status='needed', so a flagged
    item doesn't show here while also sitting in the pre-shop check block.
    """
    try:
        result = tools.get_grocery_list_by_store(status=status)
        if status == "needed":
            already_have_ids = {it["itemId"] for it in tools.get_pre_shop_flags()}
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


@app.get("/api/grocery-list/pre-shop-flags")
def get_pre_shop_flags_view():
    """Items on the 'needed' list that may already be covered by tracked inventory, humanised into a one-sentence comparison each — powers the Grocery screen's pinned 'Maybe already home' pre-shop check (PRE_SHOP_CHECK.md)."""
    try:
        result = tools.get_pre_shop_flags()
    except Exception as e:
        logger.exception("Pre-shop flags lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"flags": result}


@app.get("/api/grocery-list/store-preferences")
def get_grocery_item_store_preferences():
    """Every remembered item->store association (see set_item_store) as a flat map — powers the Grocery List view's 'usually here' indicator on auto-tagged items."""
    try:
        prefs = tools.get_item_store_preferences()
    except Exception as e:
        logger.exception("Grocery store-preferences lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"preferences": prefs}


@app.post("/api/grocery-list/{item_id}/pre-shop")
def resolve_pre_shop_flag(item_id: int, req: GroceryPreShopRequest):
    """
    Resolve one pre-shop flag (PRE_SHOP_CHECK.md): decision 'keep' ("Buy it
    anyway") confirms the item is still needed and stops it being flagged
    again; decision 'drop' ("Drop it") soft-removes it (status: removed,
    reversible via /pre-shop-undo). Idempotent per item.
    """
    if req.decision not in ("keep", "drop"):
        raise HTTPException(status_code=400, detail="decision must be 'keep' or 'drop'")
    try:
        if req.decision == "keep":
            result = tools.mark_grocery_item_already_have_reviewed(item_id)
        else:
            result = tools.drop_grocery_item_pre_shop(item_id, author=req.author)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Pre-shop decision failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/{item_id}/pre-shop-undo")
def undo_pre_shop_flag(item_id: int):
    """Undo a pre-shop 'Drop it' — restores the item to the list without re-flagging it this trip."""
    try:
        result = tools.undo_pre_shop_drop(item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Pre-shop undo failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/pre-shop/keep-all")
def keep_all_pre_shop_flags_view():
    """'Keep all {n}' — resolves every currently flagged pre-shop item as keep, in one write."""
    try:
        result = tools.keep_all_pre_shop_flags()
    except Exception as e:
        logger.exception("Pre-shop keep-all failed")
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


@app.post("/api/grocery-list/{item_id}/store/confirm")
def confirm_grocery_list_item_store(item_id: int):
    """
    Finalize the one-tap 'Remember for {store}?' offer set_grocery_item_store
    makes the first time an item gets a store (its needs_confirmation flag) —
    saves the item->store preference and adds it to that store's typical-items
    list on the Kitchen sheet. Only called when the shopper taps 'yes';
    declining needs no call at all.
    """
    try:
        result = tools.confirm_grocery_item_store_preference(item_id)
    except Exception as e:
        logger.exception("Grocery list store preference confirm failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/add")
def add_grocery_list_item(req: GroceryAddRequest):
    """Add an item to the grocery list directly from the Grocery List view (not via chat)."""
    try:
        result = tools.add_grocery_item(req.item, quantity=req.quantity, category=req.category, added_by=req.added_by)
    except Exception as e:
        logger.exception("Grocery list add failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.get("/api/stores")
def get_stores_view():
    """Every store the household's grocery list currently references, with real habit/role/aisle-order metadata where it exists — powers the desktop Grocery view's left-rail STORES filter (design_handoff_home_manager §8)."""
    try:
        result = tools.get_stores()
    except Exception as e:
        logger.exception("Store list lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"stores": result}


@app.get("/api/people")
def get_people_view():
    """The household's adults with their avatar initial/color — powers the desktop Grocery view's identity switcher and per-row 'added by' avatars (design_handoff_home_manager §8)."""
    try:
        result = tools.get_household_people()
    except Exception as e:
        logger.exception("People lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"people": result}


class ShoppingTripCloseRequest(BaseModel):
    store: str
    item_count: int = 0


@app.post("/api/shopping-trips/close")
def close_shopping_trip_view(req: ShoppingTripCloseRequest):
    """Record a shopping stop as finished — desktop Shopping mode's 'Done shopping'/'Next store' actions (design_handoff_home_manager §9)."""
    try:
        result = tools.close_shopping_trip(req.store, item_count=req.item_count)
    except Exception as e:
        logger.exception("Shopping trip close failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/{item_id}/update")
def update_grocery_list_item(item_id: int, req: GroceryUpdateRequest):
    """Correct an already-listed item's quantity/category directly from the Grocery List view."""
    try:
        result = tools.update_grocery_item(item_id, quantity=req.quantity, category=req.category)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Grocery list item update failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/{item_id}/status")
def set_grocery_list_item_status(item_id: int, req: GroceryStatusRequest):
    """Move an item between needed/in_cart/purchased — checking something off as purchased also adds it to tracked inventory automatically."""
    try:
        result = tools.mark_grocery_item(item_id, status=req.status)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Grocery list status update failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/{item_id}/remove")
def remove_grocery_list_item(item_id: int):
    """Delete an item from the grocery list entirely, directly from the Grocery List view."""
    try:
        result = tools.remove_grocery_item(item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Grocery list remove failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.get("/api/grocery-list/already-have-summary")
def get_grocery_already_have_summary_view():
    """
    Review screen's confirmation section: this week's "already have"
    decisions (pre-shop "Maybe already home" drops, and Have it/Already
    have actions taken anywhere on the Grocery screen) plus items
    currently marked "Elsewhere" — each restorable in one tap
    (already_have via /pre-shop-undo, elsewhere via /include).
    """
    try:
        already_have = tools.get_already_have_decisions()
        elsewhere = tools.list_grocery_list(status="excluded")
    except Exception as e:
        logger.exception("Grocery already-have summary lookup failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return {"already_have": already_have, "elsewhere": elsewhere}


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
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Grocery list exclude failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    return result


@app.post("/api/grocery-list/{item_id}/include")
def include_grocery_list_item(item_id: int):
    """Undo exclude — put an item back on the normal shown list, directly from the Grocery List view."""
    try:
        result = tools.include_grocery_item(item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
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
async def scan_receipt(request: Request, photo: UploadFile = File(...)):
    """
    Phase 4, §4.3: photograph a grocery receipt and get back a draft list of
    detected items — nothing is saved yet, the Inventory view shows this as
    an editable review step before /api/inventory/confirm-scan actually
    writes anything.
    """
    _enforce_rate_limit(request, "scan")
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
async def scan_fridge(request: Request, photo: UploadFile = File(...)):
    """
    Phase 4, §4.3: photograph fridge shelves and get back a draft list of
    detected items for an initial stock-take or re-sync — same
    review-before-save flow as the receipt scan, but expect lower
    confidence given mixed/stacked/partially obscured items. Every returned
    item is pre-tagged location='fridge' (or 'freezer' for anything
    visibly in a freezer compartment) so it lands in the right place
    without extra manual work.
    """
    _enforce_rate_limit(request, "scan")
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
async def scan_pantry(request: Request, photo: UploadFile = File(...)):
    """
    Phase 4, §4.3 follow-up: photograph pantry/cupboard shelves — same flow
    as scan-fridge, but every returned item is pre-tagged location='pantry'.
    """
    _enforce_rate_limit(request, "scan")
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


# ---------- Chat action cards (app-shell redesign, Step 3) ----------
# Maps a tool the agent actually called during a turn to the shell tab (or,
# for things the shell doesn't have a tab for yet, a real page href) that
# now reflects the change — this is what lets an assistant reply in the
# ask sheet offer "View" instead of dead-ending (README problem #4).
# Read-only get_*/list_* tools never appear here since they don't change
# anything. Anything NOT listed falls back to the household-info href
# (see _categorize_tool) rather than being silently dropped, since a write
# tool we haven't explicitly categorized is still something that changed.
_CHORE_TOOLS = {"add_chore", "update_chore", "generate_chore_schedule", "schedule_chore_instance", "complete_chore"}
_WEEK_TOOLS = {"plan_meal", "generate_weekly_plan", "set_week_constraints", "swap_meal_in_plan", "swap_component_in_plan", "approve_weekly_plan"}
_KITCHEN_TOOLS = {
    "add_recipe", "update_recipe_details", "mark_recipe_feedback", "log_recipe_note", "log_cooking_deviation",
    "flag_recipe_temporary", "generate_prep_schedule", "check_off_prep_step", "check_off_meal",
    "resolve_attention_item", "update_inventory", "update_inventory_items", "remove_inventory_item",
}
_GROCERY_TOOLS = {
    "add_grocery_item", "add_grocery_items", "consolidate_grocery_list", "repair_grocery_quantities", "clear_stale_grocery_items",
    "clear_grocery_list", "mark_grocery_item", "update_grocery_item", "remove_grocery_item",
    "exclude_grocery_item", "include_grocery_item", "mark_grocery_item_already_have_reviewed", "set_item_store",
}
# Household/member/preferences/setup — no shell tab shows this yet (Kitchen's
# "What we know" absorbs it in a later step), so these point at the real
# /memory page instead of a tab that wouldn't actually reflect the change.
_MEMORY_HREF_TOOLS = {
    "add_member", "set_member_dietary_restrictions", "set_member_age_group", "set_household_goals", "add_pet",
    "set_household_meal_preferences", "add_food_dislikes", "add_usual_stores", "add_store_typical_items",
    "remove_store_typical_item", "set_chores_profile", "edit_preference", "delete_preference",
    "get_or_create_member_share_link", "revoke_member_share_link", "regenerate_member_share_link",
}
_CATEGORY_KICKERS = {
    "today": "Chores updated",
    "week": "Week updated",
    "kitchen": "Kitchen updated",
    "grocery": "Grocery updated",
    "memory": "Household info updated",
}
_VERB_PREFIXES = [
    ("add_", "Added"), ("remove_", "Removed"), ("complete_", "Completed"), ("check_off_", "Marked done:"),
    ("swap_", "Swapped in"), ("update_", "Updated"), ("set_", "Updated"), ("mark_", "Updated"),
    ("generate_", "Generated"), ("approve_", "Approved"), ("exclude_", "Excluded"), ("include_", "Added back"),
    ("clear_", "Cleared"), ("consolidate_", "Consolidated"), ("resolve_", "Resolved"), ("flag_", "Flagged"),
    ("log_", "Logged"), ("plan_", "Planned"), ("schedule_", "Scheduled"),
]
_CHANGE_TEXT_FIELDS = ["name", "item", "chore", "meal", "recipe_name", "dish", "message", "store", "field", "text"]


def _categorize_tool(tool_name: str) -> tuple[str, str | None, str | None]:
    """Returns (category_key, tab, href) for a successfully-called write tool. Read-only tools should never reach this."""
    if tool_name in _CHORE_TOOLS:
        return "today", "today", None
    if tool_name in _WEEK_TOOLS:
        return "week", "week", None
    if tool_name in _KITCHEN_TOOLS:
        return "kitchen", "kitchen", None
    if tool_name in _GROCERY_TOOLS:
        return "grocery", "grocery", None
    # Explicit household/memory tools, and the catch-all for any write tool
    # not yet categorized above — better to point at the closest real page
    # than to silently produce no action card at all.
    return "memory", None, "/memory"


def _humanize_change(tool_name: str, args: dict, result) -> str:
    """Best-effort human-readable line for an action card, e.g. "Added milk" —
    built from the tool call's own arguments (usually the readable part;
    results are often just ids/status) with a small verb guessed from the
    tool name's prefix. Falls back to the category kicker if nothing usable
    is found, which still reads fine on its own."""
    noun = None
    for field in _CHANGE_TEXT_FIELDS:
        v = args.get(field) if isinstance(args, dict) else None
        if isinstance(v, str) and v.strip():
            noun = v.strip()
            break
    if noun is None and isinstance(result, dict):
        for field in _CHANGE_TEXT_FIELDS:
            v = result.get(field)
            if isinstance(v, str) and v.strip():
                noun = v.strip()
                break
    if noun is None:
        return None
    if len(noun) > 60:
        noun = noun[:57] + "..."
    verb = "Updated"
    for prefix, v in _VERB_PREFIXES:
        if tool_name.startswith(prefix):
            verb = v
            break
    return f"{verb} {noun}"


_READ_ONLY_PREFIXES = ("get_", "list_")


def summarize_chat_actions(before_history: list, after_history: list) -> list[ChatAction]:
    """
    Look at everything the agent actually did this turn (before_history vs.
    after_history from run_agent_turn) and produce the action card(s) for
    it — one per distinct shell area that changed, using the last matching
    tool call's own change text if more than one tool hit the same area.
    """
    new_entries = after_history[len(before_history):]

    tool_names_by_id: dict[str, tuple] = {}  # tool_use_id -> (name, args)
    for entry in new_entries:
        if entry.get("role") != "assistant":
            continue
        for block in entry.get("content", []):
            block_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
            if block_type != "tool_use":
                continue
            name = getattr(block, "name", None) or block.get("name")
            block_id = getattr(block, "id", None) or block.get("id")
            args = getattr(block, "input", None)
            if args is None and isinstance(block, dict):
                args = block.get("input")
            tool_names_by_id[block_id] = (name, args or {})

    by_category: dict[str, ChatAction] = {}
    for entry in new_entries:
        if entry.get("role") != "user":
            continue
        content = entry.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result" or block.get("is_error"):
                continue
            name, args = tool_names_by_id.get(block.get("tool_use_id"), (None, None))
            if not name or name.startswith(_READ_ONLY_PREFIXES):
                continue
            try:
                result = json.loads(block.get("content") or "{}")
            except Exception:
                result = None
            category, tab, href = _categorize_tool(name)
            change = _humanize_change(name, args, result) or _CATEGORY_KICKERS[category]
            by_category[category] = ChatAction(kicker=_CATEGORY_KICKERS[category], change=change, tab=tab, href=href)

    return list(by_category.values())


def _finish_chat_turn(session_id: str, history: list, reply: str, updated_history: list) -> dict:
    """
    Everything both /api/chat and its streaming twin do once
    run_agent_turn has produced a reply: summarize action cards, trim and
    store the session, and record the turn for cost/usage reporting.
    Pulled out into one place so the two endpoints can't quietly drift —
    an action-card bug fixed in one and not the other, say.
    """
    try:
        actions = summarize_chat_actions(history, updated_history)
    except Exception:
        # Action cards are a nice-to-have on top of the reply, not load-
        # bearing — never let a bug in summarizing them turn into a 500 for
        # what was otherwise a perfectly good chat turn.
        logger.exception("Summarizing chat actions failed; returning reply with no action cards")
        actions = []
    # Cap stored history so a long-lived browser tab (no logout, "default"
    # session id) can't grow this — and the full payload re-sent to Claude
    # every turn — without bound. See trim_conversation for why this is
    # safe to cut mid-list without breaking tool_use/tool_result pairing.
    SESSIONS[session_id] = trim_conversation(updated_history)
    SESSION_TOUCHED[session_id] = time.time()
    _prune_sessions()
    # The only durable record that this turn ever happened. SESSIONS above
    # is memory-only and dies with the process, so without this line a
    # restart erases every trace of how much the app was used — and unlike
    # most gaps, it can't be backfilled later. No message content is
    # stored; see schema.sql on chat_turns.
    #
    # Passed as one dict rather than **unpacked: unpacking happens at the
    # call site, *before* record_chat_turn's own error handling can catch
    # anything, so a single unexpected key in the tally — someone adding
    # usage["thinking_tokens"] in agent.py and forgetting this end — would
    # raise TypeError here and turn every chat turn into a 500, after
    # Claude had already been paid for and the reply was in hand. The
    # whole point of this line is that it cannot break the turn it
    # records.
    tools.record_chat_turn(agent.LAST_TURN_USAGE.get({}))
    return {"reply": reply, "actions": actions}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request):
    request_started = time.perf_counter()
    # req.session_id is accepted and ignored — the clients still send it and
    # there is no reason to break them, but the real key comes from the
    # signed cookie so a caller can't choose whose history they land in.
    _enforce_rate_limit(request, "chat")
    session_id = _chat_session_id(request)
    history = SESSIONS.get(session_id, [])
    is_new_sitting = time.time() - SESSION_TOUCHED.get(session_id, 0) > _NEW_SITTING_GAP
    try:
        reply, updated_history = run_agent_turn(history, req.message, proactive_check=is_new_sitting)
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
    result = _finish_chat_turn(session_id, history, reply, updated_history)
    logger.info(
        "/api/chat request took %.2fs end to end", time.perf_counter() - request_started
    )
    return ChatResponse(**result)


def _stream_chat_turn(*, session_id: str, message: str, history: list, proactive_check: bool):
    """
    Run run_agent_turn on a background thread and yield its progress as
    Server-Sent Events, the chat-loop twin of _stream_week_generation
    (see its docstring for why a background thread + queue, and why the
    thread runs inside a copied context). An immediate "status" event
    means the browser has something within milliseconds of the request
    landing; for the vast majority of turns (measured 2026-08-31 at 3-5
    seconds) "done" follows a few seconds later with nothing in between.
    It earns its place on the turn that calls generate_weekly_plan (a
    stale/missing plan the household just said yes to rebuilding, or
    "plan my week" itself) — that's the ~37-second call this app actually
    has, and "day" events surface each one as the model decides it via
    the same _WEEK_GEN_PROGRESS mechanism the plan-week screen's own
    streaming endpoint uses.
    """
    events: queue.Queue = queue.Queue()
    _DONE = object()

    def on_item(item):
        events.put(("day", item))

    def run():
        token = agent._WEEK_GEN_PROGRESS.set(on_item)
        try:
            reply, updated_history = run_agent_turn(history, message, proactive_check=proactive_check)
            events.put(("done", _finish_chat_turn(session_id, history, reply, updated_history)))
        except AssistantUnavailableError as e:
            logger.warning("Chat turn hit a transient Claude API failure: %s", e)
            events.put(("error", {"status": 503, "detail": str(e)}))
        except Exception as e:
            logger.exception("Chat turn failed")
            events.put(("error", {"status": 500, "detail": f"Server error: {e}"}))
        finally:
            agent._WEEK_GEN_PROGRESS.reset(token)
            events.put(_DONE)

    ctx = contextvars.copy_context()
    threading.Thread(target=lambda: ctx.run(run), daemon=True).start()

    yield _sse_event("status", {"message": "Thinking…"})
    while True:
        item = events.get()
        if item is _DONE:
            return
        event_name, payload = item
        yield _sse_event(event_name, payload)


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest, request: Request):
    """
    Streaming twin of /api/chat: same run_agent_turn, same reply, same
    action cards, delivered as Server-Sent Events instead of one JSON
    blob. See _stream_chat_turn for what this actually buys and why.
    """
    _enforce_rate_limit(request, "chat")
    session_id = _chat_session_id(request)
    history = SESSIONS.get(session_id, [])
    is_new_sitting = time.time() - SESSION_TOUCHED.get(session_id, 0) > _NEW_SITTING_GAP
    return StreamingResponse(
        _stream_chat_turn(
            session_id=session_id, message=req.message, history=history, proactive_check=is_new_sitting,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


# Cooking is a state of the Meals tab now (Stage 2 slice 3) — the same
# week's plan with the recipes opened up, which is why it belongs with the
# week rather than in Kitchen, where it only ever lived by pointing that
# tab's iframe at cooker.html.
#
# Redirecting to /week lands on Meals' default Plan state rather than
# straight into Cook. That is deliberate: Cook is a state, not a route, in
# exactly the way Grocery's To buy / Plan stops / Review are states of
# /grocery. The two real ways in are Today's "Start cooking" and Meals'
# own "Cook" — this route exists only so an old bookmark still arrives
# somewhere sensible.
@app.get("/cooker")
def cooker_page():
    return RedirectResponse(url="/week")


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


@app.get("/plan-week")
def plan_week_page():
    """
    The two question screens (design_handoff_plan_the_week). A standalone
    page rather than a tab in the shell, for the same reason /onboarding is
    one: a focused sequence with a beginning and an end, which tab chrome
    would only invite leaving half-answered. Takes ?week=<Monday>.
    """
    return FileResponse(os.path.join(static_dir, "plan-week.html"))


@app.get("/meal-setup")
def meal_setup_page():
    """
    The revisitable meal-planning setup (design_handoff_plan_the_week §7).
    Everything onboarding asked, editable afterwards without going through
    chat — plus an embedded chat for the things a stepper can't express.
    """
    return FileResponse(os.path.join(static_dir, "meal-setup.html"))


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


# ---------- Sign-in (see app/security.py for why this exists) ----------


def _render_login(next_path: str, error: str = "") -> HTMLResponse:
    html = open(os.path.join(static_dir, "login.html"), encoding="utf-8").read()
    # The template carries a placeholder rather than building the markup in
    # Python, so the page stays editable as a normal static file.
    banner = f'<p class="error">{error}</p>' if error else ""
    html = html.replace("<!--ERROR-->", banner)
    html = html.replace("__NEXT__", security.sanitize_next(next_path))
    return HTMLResponse(html, status_code=401 if error else 200)


def _is_https(request: Request) -> bool:
    # Railway terminates TLS at its proxy, so the app itself sees http —
    # X-Forwarded-Proto is what says whether the browser is on https.
    forwarded = request.headers.get("x-forwarded-proto", "")
    if forwarded:
        return forwarded.split(",")[0].strip() == "https"
    return request.url.scheme == "https"


@app.get("/login")
def login_page(request: Request, next: str = "/"):
    if security.read_session(request.cookies.get(security.COOKIE_NAME)):
        return RedirectResponse(url=security.sanitize_next(next), status_code=303)
    return _render_login(next)


def _household_for_password(password: str) -> int | None:
    """
    Which household does this passphrase open? None if it opens none.

    Two accepted credentials, checked in this order:

    1. `HOME_MANAGER_PASSWORD` — household 1, i.e. Emily's. Unchanged from
       before multi-household existed, so her deployment keeps working with
       nothing to set up and nobody logged out.
    2. A passphrase stored in `household_credentials` — how the beta
       tester's household gets in. See `app/households.py`.

    The env var is checked first so household 1 costs one constant-time
    compare rather than a pbkdf2 pass per household.
    """
    if security.check_password(password):
        return households.DEFAULT_HOUSEHOLD_ID
    return households.authenticate(password)


@app.post("/login")
def login_submit(request: Request, password: str = Form(""), next: str = Form("/")):
    _enforce_rate_limit(request, "login")
    household_id = _household_for_password(password)
    if household_id is None:
        # Deliberately does not say whether the passphrase was wrong or
        # merely belonged to no household — those are the same failure to
        # anyone who should not be here.
        logger.warning("Failed sign-in attempt from %s", ratelimit.caller_id(request))
        return _render_login(next, "That passphrase didn't match. Try again.")
    logger.info("Sign-in for household %s", household_id)
    response = RedirectResponse(url=security.sanitize_next(next), status_code=303)
    response.set_cookie(
        security.COOKIE_NAME,
        security.issue_session(household_id),
        max_age=security.COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_is_https(request),
        path="/",
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(security.COOKIE_NAME, path="/")
    return response


# A share URL carries a live credential in its path. The reporter sends
# location.pathname, so on /share/<token> the token would be the thing
# recorded — a working key sitting in a table Emily reads each morning, and
# one that outlives the error it was reporting. Redacted server-side rather
# than in the browser, because the browser is the untrusted end.
_SHARE_PATH_RE = re.compile(r"^(/(?:api/)?(?:member-)?share)/[^/]+")

# A member's name is also a path segment, and the shape check alone does not
# catch it: "/api/members/Sophia Rodriguez/share-link" fails only because of
# the space, so a household with single-word names would have sailed
# through. Redacted by position rather than by looking for names, because
# there is no list of names to look for.
_MEMBER_PATH_RE = re.compile(r"(/(?:api/)?members)/[^/]+")


def _redact_share_token(where: str) -> str:
    text = _SHARE_PATH_RE.sub(r"\1/<token>", where or "")
    return _MEMBER_PATH_RE.sub(r"\1/<name>", text)


# What a browser is allowed to put in the table, enforced here rather than
# trusted from the sender.
#
# The other three sources are structurally incapable of carrying household
# text: a server 5xx records "HTTP 500", an unhandled crash records an
# exception class name, a rate-limit records a bucket. This was the fourth,
# and it stored whatever the browser sent, which is the one end of this
# system the server does not control.
#
# That mattered twice over. Today's reporter sends `err.message` from
# window.onerror and `reason.message` from a rejected promise
# (static/error-reporter.js:96,104), so any frontend change that surfaces a
# server message into a thrown Error puts household text on the wire — and
# app/tools raises 27 messages that interpolate exactly that ("No saved
# recipe named '...'", "No household member named '...'"). And these
# strings are printed into a Claude agent's context by
# observability_report.py, under an instruction to act on what it reads;
# free text from an untrusted end arriving there is an injection channel,
# not just a privacy question.
#
# So the server keeps the *shape* and discards the prose. A recognisable JS
# error class survives, because "TypeError on /grocery" is the useful part
# of the signal; the sentence after the colon does not, because there is no
# way to tell a browser's own wording from an interpolated recipe name.
_JS_ERROR_CLASS_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]{0,38}(?:Error|Exception))\b")
# The reporter's own fixed phrase. The tail is a same-origin URL the
# browser assembled itself, but "a URL" is exactly the shape that carries
# a member name or a live share token — the same reason where_ stores a
# route pattern — so the tail goes through the same redaction and shape
# check as where_ rather than being trusted for having a fixed prefix.
_RESOURCE_FAIL_RE = re.compile(r"^failed to load (\S{1,80})$")
_SAFE_LITERALS = frozenset({"unhandled rejection"})
# A path, and only a path: no query string, no spaces, no prose. Covers
# both shapes the reporter sends: "/grocery" and "shell.js:42".
_PATH_SHAPE_RE = re.compile(r"^[A-Za-z0-9/._:<>\-]{0,120}$")

_MAX_CLIENT_DETAIL = 200
_MAX_CLIENT_WHERE = 120


def _safe_client_detail(detail: str) -> str:
    text = " ".join(str(detail or "").split())[:_MAX_CLIENT_DETAIL]
    if not text:
        return "unspecified"
    if text in _SAFE_LITERALS:
        return text
    m = _RESOURCE_FAIL_RE.match(text)
    if m:
        return f"failed to load {_safe_client_where(m.group(1))}"
    m = _JS_ERROR_CLASS_RE.match(text)
    if m:
        return m.group(1)
    # Something else entirely. That a browser error happened here is still
    # worth a row; its wording is not worth the risk of carrying.
    return "browser error"


def _safe_client_where(where: str) -> str:
    text = _redact_share_token(" ".join(str(where or "").split())[:_MAX_CLIENT_WHERE])
    return text if _PATH_SHAPE_RE.match(text) else "(unrecognised)"


class ClientErrorRequest(BaseModel):
    where: str = ""
    detail: str = ""


@app.post("/api/client-error")
def report_client_error(request: Request, req: ClientErrorRequest):
    """
    Something broke in the browser. Recorded so it can be read back.

    Before this, the front end reported nothing, ever: a screen that failed
    to load wrote a console.warn nobody sees and in places rendered a
    silently blank section. So when the tester says "it was just empty",
    there was no way to tell whether the server errored, the network
    dropped, or there was genuinely nothing to show — three very different
    problems with one appearance.

    Rate-limited with the ordinary buckets, because a page stuck in an
    error loop would otherwise write a row per frame. Deliberately returns
    204 and never an error of its own: a failure to report a failure must
    not become a second failure on a page that is already having a bad
    time.
    """
    try:
        _enforce_rate_limit(request, "client_error", record=False)
    except HTTPException:
        return Response(status_code=204)
    tools.record_error(
        "client", where=_safe_client_where(req.where), detail=_safe_client_detail(req.detail)
    )
    return Response(status_code=204)


@app.get("/api/observability")
def observability(days: int = 1):
    """
    The morning report's source: what broke, and whether the app is being
    used. Errors first, because that is what the report leads with when
    there is anything to lead with (Emily's call, 2026-09-01 — no
    email-on-error, it rides the notification she already reads).

    Household-scoped like everything else. Run it per household rather than
    asking for an all-households view, which would be the one query in the
    app reading across the isolation boundary.
    """
    try:
        return {
            "errors": tools.get_recent_errors(days=days),
            "usage": tools.get_usage_summary(days=max(days, 7)),
        }
    except Exception as e:
        logger.exception("Observability summary failed")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")


@app.get("/api/whoami")
def whoami():
    """
    Which household is this session in? Authenticated like any other API
    route, and returns only the household's own id and name — nothing about
    any other household, and no way to ask about one.

    Small but load-bearing: it is the one place the household binding is
    observable from outside, which is what lets the isolation tests assert
    on the real request path rather than on internal state. It also gives
    the beta tester a way to confirm she is in her own household rather
    than inferring it from the data looking unfamiliar.
    """
    current = tools.household_id()
    conn = get_conn()
    row = conn.execute("SELECT name FROM households WHERE id = ?", (current,)).fetchone()
    conn.close()
    return {"household_id": current, "household_name": row["name"] if row else ""}


@app.get("/healthz")
def healthz():
    """Unauthenticated liveness check for the hosting platform. Says nothing about the household."""
    return {"status": "ok"}


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow: /\n"
