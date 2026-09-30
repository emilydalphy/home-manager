"""
Never draft a dish somebody at the table can't have.

Emily, 2026-09-20, re-planning a week on her phone: the draft came back
with "Chicken Al Pastor-Style Tacos with Pineapple-Free Salsa" for a
household where she is allergic to pineapple, and Plan drew a red card over
the row — "…has pineapple, which Emily is allergic to." — with a "Keep it
anyway" button. Her words: "If there is a conflict for an allergy, just
don't suggest anything that fits that."

Until this module, generation TRUSTED the model. The allergy matcher
(coordination.check_meal_conflicts) ran only after the whole week had been
written, and only to log and to hand the draft a warning card. Every other
door that puts a model's pick on the plan — "Swap · I'll pick"
(swap_in_place), the three-picks sheet (swap_options), the chat's change
card on Save (proposals) — already ran the matcher BEFORE writing and
refused a clash. Generation, the one door that writes twenty-one dishes at
a time, was the only one that did not. This closes it, in three places:

  1. `split_safe` — before a generated dish is written. Every item the
     week generator sent back is matched on its NAME and its INGREDIENT
     LIST (its own, or the saved recipe's when it reused one) against every
     hard avoidance the household has on file: each member's dietary
     restrictions and every What-we-know fact marked hard. The ingredient
     list is what decides, so a name cannot smuggle an allergen in under a
     "-free" label. A clashing item is held back; it is never written.

  2. `repick_slot` — the held-back slot gets ONE small model call through
     the swap's own picker (swap_in_place.build_swap_context and
     _pick_replacement), with the clashing dish on `avoid`, the household's
     hard exclusions stated as `must_not_contain`, and a one-line
     `replacing_because` naming the allergen. The pick is matched again
     before it is written. If the picker can't find a safe dish either, the
     slot is handed back as an OPEN question that says plainly what
     couldn't be done — never a clashing dish with a warning on it.

  3. `sweep_plan` — after every pass that can add food to a finished week
     (plate sides, snack repair, leftover chains), a silent last line of
     defence. A side that carries an allergen is taken back off the plate;
     a dish that somehow still clashes is re-picked through
     swap_in_place.swap_meal_in_place, and dropped to an open slot if that
     refuses. Silent means: it fixes, it never renders a card.

Cost: every re-pick is a real API call against a $1/household/month
budget. MAX_REPICK_CALLS caps what one generation may spend; past the cap
a held-back slot goes straight to open rather than clashing. A clash is
rare once the prompt states the exclusions, so the cap is a ceiling, not a
budget that gets spent.

What this is NOT: a change to the matcher. Both halves call
coordination.check_meal_conflicts, the same function the approval gate
uses, so what counts as a clash is decided in one place.
"""
from __future__ import annotations

import logging
import re
import threading
import time

from . import _shared
from . import coordination as _coordination
from . import meal_plans as _meal_plans
from . import plates as _plates
from . import recipes as _recipes
from . import swap_in_place as _swap
from . import weekly_plan as _weekly_plan

logger = logging.getLogger("home_manager")

# Model calls one generation may spend re-picking around an allergen. Each
# held-back slot costs up to swap_in_place.MAX_PICK_ATTEMPTS of these.
MAX_REPICK_CALLS = 6


# ---------- the matcher, hard clashes only ----------


def hard_avoidances() -> list[dict]:
    """The household's allergies and must-avoids, read once for a whole
    week. A standing dislike is a preference, and never a reason to hold a
    dish back here — it stays the soft line under the week."""
    return [a for a in _coordination._avoidances() if a.get("severity") == "hard"]


def ingredients_for(item: dict) -> list[dict]:
    """What the dish is actually made of: the list the model sent, else the
    saved recipe's. The ingredient list is what the match is decided on, so
    a dish named "…with Pineapple-Free Salsa" over a list that contains
    pineapple is caught by the list, and one whose name is clean but whose
    list is not is caught the same way."""
    own = [i for i in (item.get("ingredients") or []) if isinstance(i, dict) and (i.get("item") or "").strip()]
    if own:
        return own
    saved = _recipes.saved_ingredients(item.get("meal_name") or "")
    if saved:
        return saved
    # A new dish from the menu pass (2026-09-21) carries no ingredient list
    # yet — the recipe pass writes one at approval and is matched then. What
    # it does carry is the planner's dish_note ("finish with crushed
    # peanuts and lime"), which names the dish's defining ingredients; it
    # is matched here as one line, so a clean name over a note that says
    # the thing is held back at the draft, not at approval.
    #
    # Marked as a note (2026-09-30): a note is written the way a name is,
    # and says what the dish leaves out as often as what it has — "keep it
    # dairy-free", "no cheese", "olive oil instead of butter". The matcher
    # reads a note's negations the way it reads a name's
    # (coordination._negated); a real ingredient line never gets that.
    note = (item.get("dish_note") or "").strip()
    return [{"item": note, "is_dish_note": True}] if note else []


def hard_clashes(name: str, ingredients: list[dict] | None = None, sides: list[dict] | None = None,
                 avoidances: list[dict] | None = None, draft: bool = False) -> list[dict]:
    """The hard clashes one dish trips — coordination.check_meal_conflicts,
    narrowed to what may not be served. A matcher that cannot run fails
    CLOSED: the dish is treated as clashing, because "we couldn't look" is
    not "nothing was found".

    `draft=True` is only for a dish that will get a checked ingredient
    list before anyone shops or cooks (the week draft and its re-pick): its
    name and note may then say what it leaves out ("Dairy-Free Pancakes",
    "no cheese"). Everywhere else a name without a list is matched
    strictly — see check_meal_conflicts' `negate_labels`."""
    if avoidances is None:
        avoidances = hard_avoidances()
    if not avoidances:
        return []
    try:
        hits = _coordination.check_meal_conflicts(
            name, ingredients=ingredients, sides=sides, avoidances=avoidances,
            negate_labels=True if draft else None,
        )
    except Exception:
        logger.exception("Allergen check failed for %r; holding it back rather than guessing", name)
        return [{"meal": name, "member": None, "restriction": "", "source": "check_failed",
                 "severity": "hard", "matched": ""}]
    return [h for h in hits if h.get("severity") == "hard"]


# What an open slot this module wrote records about itself. Named rather
# than left as a literal in one place, because another pass now has to
# recognise it: meal_variety.fill_gaps_with_a_repeat fills a breakfast or
# lunch the app could not SETTLE, and must leave alone one the app
# genuinely ASKED about. Those are different things, and the difference
# is this word.
ALLERGEN_CONSTRAINT = "allergen"


def split_safe(items: list[dict], avoidances: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """
    The generated week, sorted into what may be written and what may not.

    Returns (safe, held): `safe` is every item in its original order minus
    the clashing ones; `held` is one dict per clashing item —
    {"item", "clashes"} — for repick_slot. An open or empty slot has no
    dish to match and passes straight through.
    """
    if avoidances is None:
        avoidances = hard_avoidances()
    if not avoidances:
        return list(items), []
    safe: list[dict] = []
    held: list[dict] = []
    for item in items:
        name = (item.get("meal_name") or "").strip()
        if not name or item.get("slot_state") == "open":
            safe.append(item)
            continue
        clashes = hard_clashes(name, ingredients=ingredients_for(item), avoidances=avoidances, draft=True)
        if clashes:
            logger.warning(
                "Generation drafted %r, which has %s — held back, never written",
                name, ", ".join(sorted({_held_for(c) for c in clashes})),
            )
            held.append({"item": item, "clashes": clashes})
        else:
            safe.append(item)
    return safe, held


def _held_for(clash: dict) -> str:
    """For the log: the avoidance AND the words that tripped it —
    "dairy (matched 'buttermilk')" — so a held-back dish can be told apart
    from a false positive without re-running the matcher by hand."""
    label = clash.get("matched") or clash.get("restriction") or "?"
    word = (clash.get("matched_word") or "").strip()
    if word and word != label:
        return f"{label} (matched {word!r})"
    return label


# ---------- saying what couldn't be done ----------


def _food_word(clashes: list[dict]) -> str:
    """The thing the dish had that it mustn't: the matched food word when
    the matcher found one ("pineapple"), else the restriction as written."""
    for c in clashes:
        word = (c.get("matched") or "").strip()
        if word:
            return word
    for c in clashes:
        label = (c.get("restriction") or "").strip()
        if label:
            return label
    return "something this house can’t have"


def _person(clashes: list[dict]) -> str | None:
    names = [c.get("member") for c in clashes if c.get("member")]
    return names[0] if names else None


def open_reason(slot: str, clashes: list[dict]) -> str:
    """
    The open slot's own sentence when nothing safe could be found: plain
    about what couldn't be done, naming the food and the person. The
    "— I'd rather ask than guess" tail went on 2026-09-27 (Emily, decision
    C: the app explaining itself is cut; the fact is what's kept).
    """
    food = _food_word(clashes)
    who = _person(clashes)
    for_whom = f" for {who}" if who else ""
    return f"I couldn’t find a {slot} without {food}{for_whom}."


# The tail rows written before 2026-09-27 still carry; `question_of` drops it.
_OLD_TAIL = " — I’d rather ask than guess."
_OPEN_REASON_RE = re.compile(r"^I couldn’t find an? [\w ]+ without [^.]+?(?: — I’d rather ask than guess)?\.$")


def is_open_reason(text: str | None) -> bool:
    """
    Whether an open slot's sentence is open_reason's above (either
    wording). The sweep's drop (weekly_plan.drop_dish_from_day) files it
    under the stepper's "household_cut_back" constraint, so the sentence is
    the one mark it carries. The Plan rows keep this sentence where they
    cut every other reason (Emily, 2026-09-27): it is a question the
    household has to answer, not the app explaining itself.
    """
    return bool(_OPEN_REASON_RE.match((text or "").strip()))


def question_of(text: str | None) -> str:
    """open_reason's sentence as the screens say it: without the old tail."""
    text = (text or "").strip()
    return text[: -len(_OLD_TAIL)] + "." if text.endswith(_OLD_TAIL) else text


def _replacing_because(name: str, clashes: list[dict]) -> str:
    food = _food_word(clashes)
    who = _person(clashes)
    return f"{name} was dropped: it has {food}, which {who + ' can’t have' if who else 'this house can’t have'}."


# ---------- what a person is told ----------


def refusal_sentence(name: str, clashes: list[dict]) -> str:
    """
    "Tropical Fruit Cup has pineapple, which Emily can’t have — want me to
    pick something else?" The one sentence chat's plan_meal and
    swap_meal_in_plan decline in (DESIGN_SYSTEM §8: state the thing and
    its way out, calmly). The model relays it as written.
    """
    food = _food_word(clashes)
    who = _person(clashes)
    cannot = f"{who} can’t have" if who else "this house can’t have"
    return f"{name} has {food}, which {cannot} — want me to pick something else?"


def refuse_if_clashing(name: str, ingredients: list[dict] | None = None, override: bool = False) -> None:
    """
    The gate in front of chat's own writes (meal_plans.plan_meal_for_chat,
    weekly_plan.swap_meal_in_plan_for_chat). Matched on the ingredient
    list — the dish's own when given, else its saved recipe's — and RAISES
    weekly_plan.SlotRefused with refusal_sentence when it clashes, so
    nothing is written and the model is handed the sentence to relay.

    A raise, not a returned dict, for the reason swap_meal_in_plan_for_chat
    gives at length: the agent dispatch packages a raise as is_error, and
    only a non-error tool result counts as "the turn wrote something" — a
    returned dict would let the model say "planned it" over a slot that
    never changed.

    `override` is the person's own "I know, do it anyway", said in their
    words in this conversation (the tool description says so, and the model
    may not set it on its own). It is the only way a clashing dish goes on
    the week from chat, and it is a decision the person made, not a card
    the app offered.
    """
    if override:
        return
    name = (name or "").strip()
    if not name:
        return
    own = [i for i in (ingredients or []) if isinstance(i, dict) and (i.get("item") or "").strip()]
    clashes = hard_clashes(name, ingredients=own or _recipes.saved_ingredients(name))
    if clashes:
        raise _weekly_plan.SlotRefused(refusal_sentence(name, clashes))


# add_recipe refusals this process has handed out, by (household, lowered
# recipe name) -> when. The override on chat's add_recipe is honoured only
# for a recipe that was refused here recently — the person's "save it
# anyway" is an ANSWER to a refusal, so there has to have been one. A
# model that sets override=true on its first call gets the refusal
# instead, and the refusal it gets is what arms the second call. In
# memory on purpose: a restart forgets them, which costs one extra
# refusal and never a silent save.
RECIPE_OVERRIDE_WINDOW_SECONDS = 30 * 60
_RECIPE_REFUSALS: dict[tuple, float] = {}
_RECIPE_REFUSALS_LOCK = threading.Lock()


def _refusal_key(name: str) -> tuple:
    return (_shared.household_id(), (name or "").strip().lower())


def _recently_refused(name: str) -> bool:
    now = time.monotonic()
    with _RECIPE_REFUSALS_LOCK:
        for key, at in list(_RECIPE_REFUSALS.items()):
            if now - at > RECIPE_OVERRIDE_WINDOW_SECONDS:
                del _RECIPE_REFUSALS[key]
        return _refusal_key(name) in _RECIPE_REFUSALS


def _remember_refusal(name: str) -> None:
    with _RECIPE_REFUSALS_LOCK:
        _RECIPE_REFUSALS[_refusal_key(name)] = time.monotonic()


def refuse_recipe_if_clashing(name: str, ingredients: list | None = None,
                              override: bool = False) -> None:
    """
    The gate in front of chat's add_recipe (recipes.add_recipe_for_chat).

    Before this, chat saved whatever recipe the model wrote and the gate
    only looked when it tried to PLAN it (plan_meal_for_chat) — so a
    recipe nobody at the table could have was refused there and left
    behind in the recipe box, saved and orphaned. Matched on the name and
    the list the model sent (dict lines or plain strings), the same
    matcher every other door uses. With no list, the name is matched
    strictly.

    `override` is honoured only after a refusal of this same recipe for
    this household in the last RECIPE_OVERRIDE_WINDOW_SECONDS (see
    _RECIPE_REFUSALS): never on a first call.

    Raises weekly_plan.SlotRefused, for the reason refuse_if_clashing gives:
    a raise is what the dispatch reports as "nothing was written". The
    sentence is written for the MODEL to act on — which dish, which word,
    and what to do next — since the model, not the person, makes the next
    move (write it again without that, or pick another dish).
    """
    name = (name or "").strip()
    if not name:
        return
    if override and _recently_refused(name):
        return
    own = []
    for line in ingredients or []:
        if isinstance(line, str) and line.strip():
            own.append({"item": line})
        elif isinstance(line, dict) and (line.get("item") or "").strip():
            own.append(line)
    clashes = hard_clashes(name, ingredients=own)
    if not clashes:
        return
    _remember_refusal(name)
    word = next((c.get("matched_word") for c in clashes if c.get("matched_word")), "") or _food_word(clashes)
    food = _food_word(clashes)
    who = _person(clashes)
    cannot = f"{who} can’t have" if who else "this house can’t have"
    because = f"{word}" if word == food else f"{word} ({food})"
    raise _weekly_plan.SlotRefused(
        f"Not saved: {name} has {because}, which {cannot}. "
        f"Write it again without {word}, or pick a different dish."
    )


# ---------- the re-pick ----------


class CallBudget:
    """How many model calls one generation may still spend here."""

    def __init__(self, calls: int = MAX_REPICK_CALLS):
        self.left = calls

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def repick_slot(
    weekly_plan_id: int, held: dict, budget: CallBudget, picker=None,
    avoidances: list[dict] | None = None,
) -> dict:
    """
    Fill one held-back slot with a dish that is safe, or hand it back.

    Up to swap_in_place.MAX_PICK_ATTEMPTS calls, each gated by the same
    matcher before anything is written, the clashing dish (and any failed
    attempt) on `avoid`. The pick is written the way a generated dish is —
    the recipe saved if new, the slot planned by name — with `derived_from`
    recording what it replaced and why, so the week can say so later.

    `picker` is the model call, injectable so tests never touch the API.
    A picker that raises is treated as a pick that clashed: the slot goes
    open rather than the week failing over one dinner.

    Returns {"status": "repicked", "meal"} or {"status": "open", "reason"}.
    """
    pick_one = picker or _swap._pick_replacement
    item = held["item"]
    clashes = held["clashes"]
    meal_date = item.get("date")
    slot = item.get("slot") or "dinner"
    dropped = (item.get("meal_name") or "").strip()
    if avoidances is None:
        avoidances = hard_avoidances()

    tried = _swap._dedup([dropped])
    pick: dict | None = None
    for attempt in range(1, _swap.MAX_PICK_ATTEMPTS + 1):
        if not budget.take():
            logger.warning("Allergen re-pick budget spent; %s %s goes open", meal_date, slot)
            break
        # A synthetic entry: the slot has no row yet, which is the point.
        # build_swap_context only reads date/slot/meal/entry_id from it.
        entry = {"date": meal_date, "slot": slot, "meal": dropped, "entry_id": None}
        try:
            context = _swap.build_swap_context(weekly_plan_id, entry, tried)
            context["replacing_because"] = _replacing_because(dropped, clashes)
            candidate = pick_one(context) or {}
        except Exception:
            logger.exception("Allergen re-pick for %s %s failed (attempt %d)", meal_date, slot, attempt)
            candidate = {}
        name = (candidate.get("meal_name") or "").strip()
        if not name:
            break
        candidate["meal_name"] = name
        again = hard_clashes(name, ingredients=ingredients_for(candidate), avoidances=avoidances, draft=True)
        if not again:
            pick = candidate
            break
        logger.warning(
            "Allergen re-pick offered %r, which has %s (attempt %d)",
            name, _food_word(again), attempt,
        )
        tried.append(name)

    if pick is None:
        reason = open_reason(slot, clashes)
        _weekly_plan.plan_slot_open(
            weekly_plan_id=weekly_plan_id, meal_date=meal_date, slot=slot,
            open_reason=reason,
            derived_from={"constraint": ALLERGEN_CONSTRAINT, "dropped": dropped,
                          "avoided": _food_word(clashes)},
        )
        return {"status": "open", "date": meal_date, "slot": slot, "reason": reason}

    serves = _swap._table_for(meal_date, slot)["serves"]
    pick["meal_name"] = _swap.honest_meal_name(pick)
    _swap._save_recipe_if_new(pick, serves)
    _meal_plans.plan_meal(
        meal_date=meal_date,
        meal=pick["meal_name"],
        slot=slot,
        food_groups=[g for g in (pick.get("food_groups") or []) if g in _plates.ALL_GROUPS],
        weekly_plan_id=weekly_plan_id,
        reasoning=(pick.get("reason") or "").strip(),
        derived_from={
            **(item.get("derived_from") or {}),
            "allergen_repick": {"dropped": dropped, "avoided": _food_word(clashes)},
        },
    )
    logger.info("Allergen re-pick: %s %s %r -> %r", meal_date, slot, dropped, pick["meal_name"])
    return {"status": "repicked", "date": meal_date, "slot": slot, "meal": pick["meal_name"], "dropped": dropped}


# ---------- the silent sweep ----------


def sweep_plan(
    weekly_plan_id: int, budget: CallBudget | None = None, picker=None,
    known_clashes: dict[str, list[dict]] | None = None,
) -> dict:
    """
    The last line of defence over a finished week: anything the passes
    after generation put on the table that carries an allergen is taken
    off again, silently.

    A SIDE the plate pass attached is removed (plates.remove_component) —
    the dish itself is fine, the addition was not. A DISH that still
    clashes is re-picked through swap_meal_in_place (its own two gated
    attempts, spending this budget), and handed back as an open slot if
    that refuses. Every failure is logged and swallowed: a week that
    generated must not be lost to its own safety net, and the worst state
    this can leave is an open question, never a clashing dish.

    `known_clashes` — {lowercased dish name: clashes} — is for a dish the
    recipe pass (agent.fill_pending_recipes_for_plan) could not write
    without a must-avoid in it. Its row has no ingredients on disk, so the
    matcher below would pass it on its name alone; the pass hands over
    what it found instead, and the dish is re-picked or opened like any
    other clash.

    Returns counts, for the log and for tests.
    """
    budget = budget or CallBudget()
    known_clashes = {k.lower(): v for k, v in (known_clashes or {}).items() if v}
    out = {"sides_removed": 0, "dishes_repicked": 0, "slots_opened": 0}
    avoidances = hard_avoidances()
    if not avoidances and not known_clashes:
        return out
    try:
        plan = _weekly_plan.get_weekly_plan(weekly_plan_id)
    except Exception:
        logger.exception("Allergen sweep could not read plan %s", weekly_plan_id)
        return out
    recipes_by_name = {(r.get("name") or "").lower(): r for r in _recipes.list_recipes()}
    for meal in plan.get("meals") or []:
        name = (meal.get("meal") or "").strip()
        if not name or meal.get("slot_state") in ("planned_empty", "open") or meal.get("component_category"):
            continue
        entry_id = meal.get("entry_id")
        recipe = recipes_by_name.get(name.lower()) or {}
        sides = meal.get("sides") or []
        # The dish on its own first: a clean dish under a clashing side is
        # a side to remove, not a dinner to replace.
        dish_clash = known_clashes.get(name.lower()) or hard_clashes(
            name, ingredients=recipe.get("ingredients"), avoidances=avoidances,
            # Still a draft dish waiting for the recipe pass: its label may
            # say what it leaves out. Once the week is approved, strict.
            draft=bool(recipe.get("details_pending")) and not plan.get("approved_at"),
        )
        if not dish_clash:
            for side in sides:
                side_name = (side.get("name") or "").strip()
                if not side_name:
                    continue
                if hard_clashes(side_name, ingredients=side.get("ingredients"), avoidances=avoidances):
                    try:
                        _plates.remove_component(entry_id, side_name, weekly_plan_id)
                        out["sides_removed"] += 1
                        logger.warning("Allergen sweep took %r off %s %s (%s)", side_name, meal.get("date"), meal.get("slot"), name)
                    except Exception:
                        logger.exception("Allergen sweep could not remove side %r from entry %s", side_name, entry_id)
            continue
        # The dish itself. swap_meal_in_place runs the same matcher before
        # it writes; the result is matched AGAIN here rather than trusted,
        # because "safe by construction" is the sentence the verifier of
        # 2026-09-21 caught being false (a reused recipe matched on its
        # name alone). A swap that lands clean is done; one that lands
        # dirty, refuses, or would overspend the budget opens the slot.
        swapped = False
        if budget.left >= _swap.MAX_PICK_ATTEMPTS:
            budget.left -= _swap.MAX_PICK_ATTEMPTS
            try:
                result = _swap.swap_meal_in_place(weekly_plan_id, entry_id, picker=picker)
                if result.get("status") == "swapped":
                    new_name = (result.get("meal") or "").strip()
                    still = hard_clashes(
                        new_name, ingredients=_recipes.saved_ingredients(new_name), avoidances=avoidances,
                    )
                    if still:
                        logger.error(
                            "Allergen sweep's re-pick of %s %s landed %r, which has %s — opening the slot",
                            meal.get("date"), meal.get("slot"), new_name, _food_word(still),
                        )
                        entry_id, name, dish_clash = result.get("entry_id", entry_id), new_name, still
                    else:
                        swapped = True
            except Exception:
                logger.exception("Allergen sweep could not re-pick %s %s (%s)", meal.get("date"), meal.get("slot"), name)
        if swapped:
            out["dishes_repicked"] += 1
            logger.warning("Allergen sweep re-picked %s %s: %r had %s", meal.get("date"), meal.get("slot"), name, _food_word(dish_clash))
            continue
        try:
            dropped = _weekly_plan.drop_dish_from_day(
                weekly_plan_id, entry_id, open_reason=open_reason(meal.get("slot") or "dinner", dish_clash),
            )
            # Anything but 'dropped' left the week as it was: a refusal, or
            # (since 2026-09-24) `needs_confirmation`, which the "−" answers
            # when moving a cook would delete a night already ticked cooked.
            # The sweep is not a person and never says yes to that on
            # anyone's behalf, so it is logged the way a refusal is.
            if dropped.get("status") != "dropped":
                logger.error("Allergen sweep could not take %r off %s %s: %s", name, meal.get("date"), meal.get("slot"), dropped.get("message"))
            else:
                out["slots_opened"] += 1
                logger.warning("Allergen sweep opened %s %s: %r had %s and nothing safe was found", meal.get("date"), meal.get("slot"), name, _food_word(dish_clash))
        except Exception:
            logger.exception("Allergen sweep could not open %s %s (%s)", meal.get("date"), meal.get("slot"), name)
    return out


def replace_unwritten_clash(recipe_name: str, clashes: list[dict]) -> int:
    """
    A pending dish the recipe pass could not write, on every week it is
    planned in, re-picked or opened through sweep_plan — the Cook screen's
    "Fill in this recipe" answer when the dish turns out to be one the
    table can't have (2026-09-30). The alternative was "Couldn't write up
    this recipe" over a dish that stayed on the week unverified.

    Returns how many weeks were swept.
    """
    from ..db import get_conn
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT mpe.weekly_plan_id FROM meal_plan_entries mpe "
            "JOIN recipes r ON r.id = mpe.recipe_id "
            "WHERE r.household_id = ? AND LOWER(r.name) = LOWER(?) AND mpe.weekly_plan_id IS NOT NULL",
            (_shared.household_id(), recipe_name),
        ).fetchall()
    finally:
        conn.close()
    swept = 0
    for row in rows:
        try:
            sweep_plan(row["weekly_plan_id"], known_clashes={recipe_name: clashes})
            swept += 1
        except Exception:
            logger.exception("Could not re-pick %r on plan %s", recipe_name, row["weekly_plan_id"])
    return swept
