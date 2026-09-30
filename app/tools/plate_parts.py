"""
The plate, part by part: change the protein of a dish without losing the
dish, and know what the plate is short of.

Emily, 2026-09-13 ("Shaping the Draft", Flows A and B): "there's been a
few tests now where I wanted to swap just the type of meat that was used
in a recipe", and "add a carb, like potatoes, to make it more filling".
Until now the only ways to change a meal were "Swap · I'll pick" (a
different dish altogether) and a sentence to the chat. This module is the
part in between:

  * `parts_of_plate(...)` — the plate as the card shows it: protein, then
    vegetable and carb as the household's plate rule asks for them (see
    plates.plate_rule: keto has no carb, low carb a small one; breakfast
    and snack are lighter).
    Each part is either named (the dish's own protein or in-dish veg/carb,
    read off the recipe's ingredients — see `_in_dish_part_name`; a side
    by name), in the dish with nothing to name (an older recipe with no
    ingredients on record), or missing — which the card draws as a dashed
    "+ Add a carb". Read from what the entry already holds; nothing new to
    enter. A no-carb household's rule leaves the carb off the plate the
    planner builds, but the card still offers it, reading "None" (Emily,
    2026-09-15): the chip is an option, not a shortfall, and the planner
    never fills it on its own. A low-carb household's in-dish carb reads
    its own name with a "· small" qualifier ("Sweet potato mash · small")
    rather than the bare word "Small" (Emily, 2026-09-22: naming it
    matters as much for a small portion as a full one).
  * `part_options(...)` — the four options that would work IN THIS DISH
    for the part being changed (protein, vegetable or carb), written for
    the dish, not from a list: a burger gets ground meats and a bean
    patty, never chicken thighs; a pan-fried chicken gets the CUT (thighs
    vs breasts) and what it changes; a Cajun salmon plate's vegetable gets
    asparagus, corn or broccolini, not a sauce. One small forced tool call
    on the cheapest route, cached for the sitting so reopening the sheet
    is instant. The household's hard exclusions and dislikes are handed in
    so nothing offered is something they can't have.
  * `change_part(...)` — the household picked one (or typed one).
    A part the DISH itself covers (source "dish") gets one small model
    call that rewrites the recipe around the new choice — the same dish,
    a name that says what changed ("Beef burgers", "…with Broccoli and
    Sweet Potato Mash"), ingredients, steps, and (for the protein)
    main_protein — then the pick goes through swap_in_place's own two
    gates and apply (pick_gate, apply_pick), so it lands exactly as a
    swap does: a saved recipe, the plan updated, the grocery list in step
    on an approved week, and "Undo" through undo_meal_swap.
    A part a SIDE covers (source "side" — something the household added
    from the "Add a carb/vegetable" sheet) is a DIFFERENT part of the
    plate entirely and this function refuses it: the client's own "Add
    something" sheet already replaces a side one-for-one (the new side
    goes on, the old one comes off — plates.add_component,
    plates.remove_component — with its own one-tap Undo), so a side has
    exactly one door, not two (verifier, 2026-09-22, after an earlier
    version of this module opened a second one that the client never
    actually called).
    "Change" only ever applies to a part the DISH covers; a MISSING part
    or a SIDE-covered one keeps going through the catalogue add-sheet
    (plates.suggest_additions/add_component/remove_component), unchanged.
"""
from __future__ import annotations

import json
import logging
import time

from ._shared import household_id
from . import model_shapes as _model_shapes
from . import plates as _plates

logger = logging.getLogger("home_manager")

# weekly_plan imports this module for plate_parts() (pure, plates-only);
# everything below that touches the plan goes through swap_in_place, which
# reaches weekly_plan itself — so those are imported where they are used,
# never at the top, or the two modules would import each other.


def _swap_mod():
    from . import swap_in_place
    return swap_in_place


def _memory_mod():
    from . import memory
    return memory


def _weekly_plan_mod():
    # Lazy for the reason the two above are: weekly_plan imports THIS
    # module at module scope for plate_parts(), so a top-level import here
    # would be a cycle.
    from . import weekly_plan
    return weekly_plan

ROLES = ("protein", "vegetable", "carb")
# The word on the chip for each role — the household's words, not the
# schema's ("Veg", never "vegetable").
ROLE_WORDS = {"protein": "Protein", "vegetable": "Veg", "carb": "Carb"}
MAX_OPTIONS = 4
_OPTIONS_TTL = 30 * 60
# (household_id, entry_id) -> {"at": t, "options": [...], "meal": name}
_OPTIONS_CACHE: dict[tuple[int, int], dict] = {}


# ---------- the plate as the card shows it ----------

# Produce that flavours a dish and is never "the veg" on its plate:
# aromatics, chilies, fresh herbs, citrus. Matched by WORD, not by whole
# name — Emily, 2026-09-27, her gochujang beef bowl's plate read "VEG:
# Fresh ginger" ("Ginger isn't a 'veggie' in this case, it should be seen
# more as an herb"), because "ginger" was on the old whole-name list and
# "fresh ginger" was not. Singular forms; _produce_words singularises.
_NOT_THE_VEG = {
    # aromatics
    "garlic", "ginger", "onion", "shallot", "scallion", "leek", "lemongrass", "galangal",
    "green onion", "spring onion",
    # chilies
    "chili", "chile", "chilli", "jalapeño", "jalapeno", "serrano", "habanero",
    "thai chili", "bird's eye chili", "chili pepper", "chile pepper", "jalapeño pepper",
    "jalapeno pepper", "serrano pepper", "habanero pepper",
    # herbs
    "herb", "cilantro", "coriander", "basil", "thai basil", "parsley", "mint", "dill",
    "chive", "thyme", "rosemary", "oregano", "sage", "tarragon", "marjoram", "bay leaf",
    "lime leaf", "kaffir lime leaf", "makrut lime leaf", "curry leaf",
    # citrus, squeezed or zested rather than eaten as a side
    "lemon", "lime",
}
# Words that say how an ingredient is bought or cut, not what it is —
# dropped before the match, so "fresh ginger root, grated" reads "ginger".
_PREP_WORDS = {
    "fresh", "dried", "chopped", "minced", "grated", "sliced", "diced", "crushed", "peeled",
    "finely", "roughly", "thinly", "whole", "root", "clove", "bunch", "sprig", "stalk", "stem",
    "piece", "knob", "thumb", "handful", "small", "large", "medium", "red", "yellow", "white",
    "juice", "zest", "juiced", "zested", "of", "and", "for", "garnish", "to", "serve",
}
# The units a vegetable is bought in by the pound or the head, against
# the ones an aromatic comes in: the bigger buy is the plate's veg.
_BIG_UNITS = {"lb", "lbs", "pound", "pounds", "kg", "g", "oz", "head", "heads", "bag", "bags",
              "package", "packages", "box", "boxes", "bunch", "bunches", "cup", "cups", "can", "cans"}


def _produce_words(item: str) -> list[str]:
    import re
    name = re.sub(r"\(.*?\)", " ", (item or "").lower()).split(",")[0]
    words = []
    for w in re.findall(r"[a-zñé']+", name):
        if w == "leaves":
            w = "leaf"
        elif w.endswith("ies") and len(w) > 4:
            w = w[:-3] + "y" if w not in ("chilies", "chillies") else w[:-2]
        elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
            w = w[:-1]
        if w not in _PREP_WORDS:
            words.append(w)
    return words


def _is_not_the_veg(item: str) -> bool:
    """True for an aromatic, a chili, a herb or citrus — whatever words
    of prep or measure the name carries ("Fresh ginger", "Garlic cloves",
    "Thai basil leaves", "Green onions, sliced")."""
    words = _produce_words(item)
    # "Thai basil leaves" is the basil; "bay leaves" and "curry leaves"
    # are named by the leaf, so both readings are tried.
    for ws in (words, words[:-1] if words[-1:] == ["leaf"] else []):
        if ws and (" ".join(ws) in _NOT_THE_VEG or " ".join(ws[-2:]) in _NOT_THE_VEG
                   or ws[-1] in _NOT_THE_VEG):
            return True
    return False


def _buy_rank(qty: str) -> int:
    """2 for a vegetable bought by weight, head, bag or cup; 1 for a bare
    count or anything unrecognised; 0 for a clove, a piece, a spoon."""
    import re
    words = re.findall(r"[a-z]+", (qty or "").lower())
    if any(w in _BIG_UNITS for w in words):
        return 2
    if any(w in ("clove", "cloves", "piece", "pieces", "tbsp", "tsp", "tablespoon", "tablespoons",
                 "teaspoon", "teaspoons", "sprig", "sprigs", "inch", "knob", "thumb") for w in words):
        return 0
    return 1


def _in_dish_part_name(role: str, ingredients: list[dict] | None, dish: str | None = None) -> str | None:
    """
    The plain name of the dish's own vegetable or carb, read off its
    ingredients — "Green beans", "Sweet potato" — not invented. None when
    nothing in the ingredients speaks to this role (an older recipe with
    no ingredients on record, or a role folded into a step rather than
    named as its own ingredient); the caller falls back to the plain word
    exactly as it always has.

    Emily, 2026-09-22: her Cajun salmon night's plate read "VEG: Steamed
    broccoli" (an added SIDE) while the recipe still had green beans —
    because the dish's own green beans, covered by food_groups with no
    name, never had a name to show in the first place. This is that name.

    The vegetable is the REAL vegetable (Emily, 2026-09-27): never an
    aromatic, a chili, a herb or citrus (_is_not_the_veg); of what's left,
    the one the dish's name mentions (`dish`), else the biggest buy — three
    heads of broccoli over a bunch of anything — else the first listed.
    """
    candidates = []
    standin = None
    for n, ing in enumerate(ingredients or []):
        if not isinstance(ing, dict):
            continue
        item = (ing.get("item") or "").strip()
        if not item:
            continue
        if role == "carb":
            if _plates.has_starch(item):
                return item[:1].upper() + item[1:]
            if standin is None and _plates.carb_standin(item):
                standin = item
            continue
        if role == "vegetable":
            if (ing.get("category") or "").strip().lower() != "produce":
                continue
            # A low-carb base (cauliflower rice, zucchini noodles) is the
            # plate's CARB line, not its veg (plates.carb_standin).
            if _plates.has_starch(item) or _is_not_the_veg(item) or _plates.carb_standin(item):
                continue
            candidates.append((item, str(ing.get("qty") or ""), n))
    if role == "carb":
        # No real starch: the dish's own low-carb base is its carb (Emily,
        # 2026-09-27), so Change rewrites it in place.
        return standin[:1].upper() + standin[1:] if standin else None
    if not candidates:
        return None
    title = set(_produce_words(dish or ""))

    def rank(c):
        head = _produce_words(c[0])[-1:]  # "Green beans" -> "bean"
        in_title = bool(head) and head[0] in title
        return (not in_title, -_buy_rank(c[1]), c[2])

    item = min(candidates, key=rank)[0]
    return item[:1].upper() + item[1:]


def parts_of_plate(slot: str, food_groups: list[str], main_protein: str | None,
                sides: list[dict] | None, eating_style: str | None,
                carb_level: str | None = None, ingredients: list[dict] | None = None,
                dish: str | None = None) -> list[dict]:
    """
    The plate for one entry, in the order the card shows it. Pure; the
    week menu calls it with what its rows already carry.

    Each part: {role, word, name, source, missing}
      source 'dish'  — the dish covers it itself (name is the protein's
                       own word, or None for a veg/carb with no name)
      source 'side'  — a side attached to the entry covers it (name is
                       the side's)
      missing True   — the rule asks for it and nothing covers it
      empty True     — (carb, a household on none) nothing covers it and
                       the rule doesn't ask; the card reads "None" and
                       still offers the tap (Emily, 2026-09-15)

    The carb reads by the household's level (plates.carb_level — Emily,
    2026-09-21, "low carb is not no carb"): on `low`, a carb the dish
    carries is "Small" and a side marked portion small says so; on
    `none`, no carb reads "None"; a low-carb dish with no carb at all is
    MISSING, never "In the dish" — the plate pass should have added one.
    `carb_level` is passed when the caller has it (the week menu reads it
    once, facts included); otherwise it is read off eating_style.

    `ingredients` is the dish's own recipe ingredients (None for a
    freeform meal or an older recipe with none on record) — used only to
    NAME an in-dish vegetable or carb (_in_dish_part_name); nothing here
    changes without it, a dish with no ingredients on record just keeps
    reading "In the dish" the way it always has. `dish` is the dish's name,
    which breaks a tie between two vegetables (the one it's named for).
    """
    if carb_level is None:
        carb_level = _plates.carb_level(eating_style)
    rule = _plates.plate_rule(level=carb_level)
    if slot in _plates.LIGHT_SLOTS:
        # The lighter rule (plates.missing_groups): a breakfast held to
        # protein + veg + carb is a dinner at 7am.
        wanted = [r for r in rule if r == "protein"]
    else:
        # The card offers the carb even where the rule leaves it off. A
        # no-carb household's rule (plates.plate_rule, decision 7a) is
        # what the PLANNER follows — it never puts rice beside their
        # steak on its own — but the household still needs the one tap
        # when they want it: Emily, 2026-09-15, her own keto plan,
        # "for the kebab meal I would like an option to add a carb", and
        # the kofte's card had nowhere to say so. So the carb is drawn
        # here as an offer, reading "None" and theirs to take, on the
        # same terms as any other plate: only when the dish says what it
        # covers (`known`), never when it would be a guess.
        wanted = list(rule)
        if "carb" not in wanted:
            wanted.append("carb")
    groups = set(food_groups or [])
    # A dish with no food groups recorded at all (an older recipe, a
    # freeform line) is UNKNOWN, not short: "Roast Chicken · + Add a
    # protein" is the card guessing wrong out loud. Only a dish that
    # says what it covers can be said to be missing something — the
    # same line the plate pass draws (plates.has_food_groups).
    known = bool(groups)
    covered_by_side: dict[str, str] = {}
    for side in sides or []:
        for g in side.get("covers") or []:
            name = side.get("name") or ""
            if g == "carb" and side.get("portion") == "small" and name:
                name = f"{name} (small)"
            covered_by_side.setdefault(g, name)
    parts = []
    # A side that names no role (a sauce; a typed line the model could not
    # cost) is still on the plate and still shows — as its own chip, after
    # the roles, so nothing the household added disappears from the card.
    extras = [s.get("name") for s in sides or [] if isinstance(s, dict) and s.get("name") and not (s.get("covers") or [])]
    for role in ROLES:
        if role not in wanted:
            continue
        if role == "protein":
            # The protein is never "missing": every dinner has one to
            # change, named when the recipe says (main_protein), plain
            # "Protein" when it doesn't.
            name = (main_protein or "").strip()
            name = name[:1].upper() + name[1:] if name else None
            if not name and role in covered_by_side:
                parts.append({"role": role, "word": ROLE_WORDS[role], "name": covered_by_side[role], "source": "side", "missing": False})
            else:
                parts.append({"role": role, "word": ROLE_WORDS[role], "name": name, "source": "dish", "missing": False})
            continue
        if role in covered_by_side:
            parts.append({"role": role, "word": ROLE_WORDS[role], "name": covered_by_side[role], "source": "side", "missing": False})
        elif role in groups:
            # Named off the dish's own ingredients when it has them
            # (Emily, 2026-09-22: "Green beans", "Sweet potato mash" —
            # not the bare word "Vegetable"/"Carb"). A low-carb plate's
            # own carb is a half portion by rule — the qualifier rides
            # alongside the name rather than replacing it, so a small
            # portion is still named, not just sized.
            name = _in_dish_part_name(role, ingredients, dish)
            # A low-carb base is the dish's whole base, not a half portion.
            if role == "carb" and carb_level == "low" and not (name and _plates.carb_standin(name)):
                name = f"{name} · small" if name else "Small"
            parts.append({"role": role, "word": ROLE_WORDS[role], "name": name, "source": "dish", "missing": False})
        elif known and role == "carb" and carb_level == "none":
            parts.append({"role": role, "word": ROLE_WORDS[role], "name": "None", "source": None,
                          "missing": False, "empty": True})
        elif known:
            parts.append({"role": role, "word": ROLE_WORDS[role], "name": None, "source": None, "missing": True})
    for name in extras:
        parts.append({"role": "side", "word": "Side", "name": name, "source": "side", "missing": False})
    return parts


# ---------- the options ----------

OPTIONS_TOOL = {
    "name": "submit_part_options",
    "description": "The proteins/vegetables/carbs that would work in this exact dish, as the household would name them at the counter.",
    "input_schema": {
        "type": "object",
        "properties": {
            "options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "The ingredient at the level a shopper buys it: 'Ground pork', 'Chicken thighs', 'Asparagus', 'Roasted potatoes'. Never a dish name."},
                        "note": {"type": "string", "description": "Two to five words on what changes, or nothing: 'leaner, 5 min less', 'richer', 'same pan, same time', 'vegetarian'. No exclamation marks."},
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["options"],
    },
}

# One instruction block per part — same shape, different noun and
# examples, so a Cajun salmon plate's "Change" on the vegetable offers
# asparagus or broccolini and not a sauce (Emily, 2026-09-22).
OPTIONS_INSTRUCTIONS = {
    "protein": """A household wants to change the PROTEIN in one dish they have already planned, and keep \
the dish. Offer the four proteins that would genuinely work in THIS dish, cooked THIS way — not a \
generic list.

Rules:
- Name the protein at the level it is bought and at the cut the method needs. A burger or a \
meatball dish gets ground meats (ground beef, ground pork, ground chicken) and a bean or lentil \
patty — never 'chicken thighs'. A pan-fry, a roast or a traybake gets the cut: 'Chicken breasts' \
and 'Chicken thighs' are two different offers, and each carries what it changes ('leaner, 5 min \
less'). A curry or a stew gets what braises. A stir-fry gets what slices thin.
- Never offer the protein the dish already has, and never offer anything in must_not_contain or \
dislikes. If a taste verdict says someone at the table avoids it, leave it out.
- One of the four may be vegetarian when it genuinely suits the dish (halloumi, tofu, beans, \
lentils); say 'vegetarian' in its note.
- Notes are two to five plain words about what changes for the cook — time, richness, the pan — or \
empty. No selling, no exclamation marks.
- Exactly four options where four fit; fewer only if the dish truly allows fewer.""",
    "vegetable": """A household wants to change the VEGETABLE in one dish they have already planned, and keep \
the dish and its protein exactly as they are. Offer four vegetables that would genuinely work \
alongside THIS dish's protein and method — not a generic list, and never a sauce or a starch.

Rules:
- Name the vegetable the way a shopper buys it: 'Broccoli', 'Asparagus', 'Zucchini', 'Green beans'. \
Never a dish name.
- Never offer the vegetable the dish already has, and never offer anything in must_not_contain or \
dislikes. If a taste verdict says someone at the table avoids it, leave it out.
- Notes are two to five plain words about what changes — cook time, texture, colour — or empty. No \
selling, no exclamation marks.
- Exactly four options where four fit; fewer only if the dish truly allows fewer.""",
    "carb": """A household wants to change the CARB in one dish they have already planned, and keep the \
dish and its protein exactly as they are. Offer four carbs that would genuinely work alongside THIS \
dish's protein and method — not a generic list, and never a vegetable or a sauce.

Rules:
- Name the carb the way a shopper buys it: 'Rice', 'Roasted potatoes', 'Couscous', 'Crusty bread', \
'Warm tortillas'. Never a dish name.
- Never offer the carb the dish already has, and never offer anything in must_not_contain or \
dislikes.
- Notes are two to five plain words about what changes — cook time, texture — or empty. No selling, \
no exclamation marks.
- Exactly four options where four fit; fewer only if the dish truly allows fewer.""",
}


def _options_context(entry: dict, recipe: dict | None, role: str, current: str,
                     whole_recipe: bool = False) -> dict:
    """What the model is told about the dish. The OPTIONS call only needs a
    taste of it (25 ingredients, 6 steps); the CHANGE needs all of it,
    `whole_recipe`: a method cut at six steps is how a nine-step recipe
    lost its last three after a change (2026-09-30), and the steps are
    numbered from 1 so a change can name the one it edits."""
    _swap = _swap_mod()
    memory = _memory_mod().get_household_memory()
    table = _swap._table_for(entry["date"], entry["slot"])
    ingredients = (recipe or {}).get("ingredients") or []
    steps = (recipe or {}).get("instructions") or []
    if not whole_recipe:
        ingredients, steps = ingredients[:25], steps[:6]
    context = {
        "dish": entry["meal"],
        f"current_{role}": current,
        "ingredients": [{"item": i.get("item"), "qty": i.get("qty")} for i in ingredients],
        "method": ([{"step": n, "text": t} for n, t in enumerate(steps, 1)] if whole_recipe else steps),
        "slot": entry["slot"],
        "must_not_contain": _swap._hard_exclusions(),
        "dislikes": memory.get("dislikes") or [],
        "eating_style": memory.get("eating_style") or "",
    }
    taste = _swap._taste_lines_for(entry["date"], entry["slot"], table)
    if taste:
        context["taste_verdicts"] = taste
    return context


def _recipe_for(entry: dict) -> dict | None:
    from . import recipes as _recipes
    if not entry.get("recipe_id"):
        return None
    try:
        return _recipes.get_recipe(entry["meal"])
    except Exception:
        return None


def _current_part(entry: dict, recipe: dict | None, role: str, sides: list[dict]) -> tuple[str, str | None]:
    """
    The name and source ("dish" or "side") of whatever currently covers
    `role` (vegetable or carb) on this plate — the same read
    parts_of_plate does for the card, scoped to one entry. `source` is
    None when nothing covers it: "Change" only ever opens on a part
    that's actually there; a missing part still opens the catalogue
    add-sheet (plates.suggest_additions/add_component), unchanged.
    """
    for side in sides or []:
        if role in (side.get("covers") or []):
            return (side.get("name") or "").strip(), "side"
    groups = set(entry.get("food_groups") or [])
    ingredients = (recipe or {}).get("ingredients")
    # The card's own read (weekly_plan._effective_food_groups): a dish whose
    # name or ingredients carry a carb — a real one, or its own low-carb
    # base — covers the carb even when food_groups missed it. Without this
    # the chip read "Cauliflower rice" and Change on it said there was no
    # carb to change.
    if role == "carb" and groups and "carb" not in groups and _plates.dish_has_carb(entry.get("meal"), ingredients):
        groups.add("carb")
    if role in groups:
        name = _in_dish_part_name(role, ingredients, entry.get("meal")) or ""
        return name, "dish"
    return "", None


def _ask_options(context: dict, role: str = "protein") -> list[dict]:
    from .. import agent
    client = agent._client()
    response = agent._create_with_retry(
        client,
        label="plate_part_options",
        model=agent.MODEL,
        max_tokens=600,
        tools=[OPTIONS_TOOL],
        tool_choice={"type": "tool", "name": "submit_part_options"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": OPTIONS_INSTRUCTIONS[role], "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": f"The dish (JSON):\n{json.dumps(context, indent=2)}"},
            ],
        }],
        output_config=agent._effort_config("utility"),
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return _model_shapes.tool_list(block.input, "options", "plate_side_options")
    return []


def _clean_options(raw: list, exclude: str) -> list[dict]:
    out = []
    seen = set()
    for o in raw or []:
        if not isinstance(o, dict):
            continue
        name = (o.get("name") or "").strip()
        # "Ground turkey" is the turkey the dish already has, and so is a
        # bare "Turkey" — but "Chicken breasts" offered for a chicken-thigh
        # dish is the cut-level change this sheet exists for. So: the
        # option is the same protein only when it is that word alone or
        # "ground <word>"; a cut ("Chicken breasts", "Pork loin") stays.
        low = name.lower()
        same = bool(exclude) and low in ((exclude or "").lower(), "ground " + (exclude or "").lower())
        if not name or low in seen or same:
            continue
        seen.add(name.lower())
        out.append({"name": name[:1].upper() + name[1:], "note": (o.get("note") or "").strip()[:40]})
        if len(out) >= MAX_OPTIONS:
            break
    return out


def part_options(weekly_plan_id: int, entry_id: int, role: str = "protein", asker=None) -> dict:
    """
    What the "Change the protein/vegetable/carb" sheet offers, for this
    dish. Cached per entry AND role for the sitting: the options don't
    change until the dish does, and the sheet should open instantly the
    second time. `asker` is the model call, injectable for tests — always
    called as `asker(context)`, one argument, regardless of role.

    Only ever opens on a part the DISH ITSELF covers (the protein always
    is one; a vegetable/carb only when source is "dish"). A MISSING
    vegetable or carb, or one that's a SIDE the household already added,
    both go through the catalogue add-sheet instead
    (plates.suggest_additions/add_component/remove_component) — see
    change_part for why a side stays that one path rather than two.
    """
    if role not in ROLES:
        raise ValueError(f"No such part {role!r} — protein, vegetable or carb.")
    _swap = _swap_mod()
    entry = _swap._entry(weekly_plan_id, entry_id)
    if entry["slot_state"] != "planned" or not entry["meal"]:
        raise ValueError("There's no meal on that slot to change.")
    recipe = _recipe_for(entry)
    if role == "protein":
        current = ((recipe or {}).get("main_protein") or "").strip()
    else:
        current, source = _current_part(entry, recipe, role, _plates.get_sides(entry_id))
        if source is None:
            raise ValueError(f"There's no {role} on this plate yet — add one instead.")
        if source == "side":
            raise ValueError(
                f"That {role} is a side you added — take it off and add a new one instead."
            )
    key = (household_id(), entry_id, role)
    cached = _OPTIONS_CACHE.get(key)
    if cached and cached["meal"] == entry["meal"] and time.time() - cached["at"] < _OPTIONS_TTL:
        return {"entry_id": entry_id, "meal": entry["meal"], "role": role,
                "current": cached["current"], "options": cached["options"],
                "options_unavailable": cached.get("unavailable", False)}
    context = _options_context(entry, recipe, role, current)
    ask = asker or (lambda ctx: _ask_options(ctx, role))
    # `unavailable` tells the sheet a failed call apart from the model
    # genuinely finding nothing — both leave `options` empty, but only the
    # first is "the AI call is down" rather than "this dish stumped it".
    unavailable = False
    try:
        raw = ask(context)
    except Exception:
        logger.exception("Asking for %s options failed; the sheet will offer the typed line only", role)
        raw = []
        unavailable = True
    options = _clean_options(raw, current)
    _OPTIONS_CACHE[key] = {"at": time.time(), "meal": entry["meal"], "current": current,
                            "options": options, "unavailable": unavailable}
    return {"entry_id": entry_id, "meal": entry["meal"], "role": role, "current": current,
            "options": options, "options_unavailable": unavailable}


def forget_options(entry_id: int) -> None:
    hh = household_id()
    for role in ROLES:
        _OPTIONS_CACHE.pop((hh, entry_id, role), None)


# ---------- the change ----------

VARIANT_TOOL = {
    "name": "submit_variant",
    "description": "The same dish, rewritten around the changed part.",
    "input_schema": {
        "type": "object",
        "properties": {
            "meal_name": {"type": "string", "description": "The dish's name with the changed part updated where the name carries it: 'Turkey burgers' → 'Beef burgers'; 'Cajun Salmon with Green Beans and Sweet Potato Mash' → 'Cajun Salmon with Broccoli and Sweet Potato Mash'. Keep everything else of the name."},
            "ingredients": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item": {"type": "string"},
                        "qty": {"type": "string"},
                        "category": {"type": "string", "enum": ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"]},
                    },
                    "required": ["item"],
                },
            },
            "instructions": {"type": "array", "items": {"type": "string"}},
            "food_groups": {"type": "array", "items": {"type": "string", "enum": ["protein", "carb", "vegetable"]}},
            "cuisine": {"type": "string"},
            "main_protein": {"type": "string"},
            "prep_time_minutes": {"type": "integer"},
            "cook_time_minutes": {"type": "integer"},
            "default_servings": {"type": "integer"},
            "reason": {"type": "string", "description": "ONE short line the card shows: what changed and what it means for the cook. 'Beef instead of turkey. Same recipe, same time.' 'Broccoli instead of green beans — same time.' Under twelve words, no exclamation mark."},
        },
        "required": ["meal_name", "ingredients", "instructions", "main_protein", "reason"],
    },
}

VARIANT_INSTRUCTIONS = {
    "protein": """A household is changing the PROTEIN in one dish they have already planned. Rewrite the \
recipe around the new protein and change NOTHING else that doesn't have to change.

- Keep the dish: its name (with only the protein word changed), its other ingredients, its \
sauce, its sides, its shape. The household liked this dish; they are changing one part.
- Swap the protein ingredient for the new one at the amount the new one is bought in \
(store-bought units, plain grocery names). Change a step only where the new protein needs it \
(a different cooking time, a different doneness, no thawing).
- Update prep and cook minutes honestly for the new protein.
- Set main_protein to the new protein's plain word ('beef', 'pork', 'chicken', 'vegetarian').
- Never use anything in must_not_contain or dislikes. If the requested protein is one of them, \
still write it — the app checks and refuses; do not substitute something else silently.
- The reason line is for the card: what changed and what it means for the cook, in one plain \
sentence or two short ones.""",
    "vegetable": """A household is changing the VEGETABLE in one dish they have already planned. Rewrite \
the recipe around the new vegetable and change NOTHING else that doesn't have to change — this \
REPLACES the old vegetable; it does not add a second one alongside it.

- Keep the dish: its protein, its sauce, its other ingredients, its shape. The household liked \
this dish; they are changing one part.
- Swap the vegetable ingredient for the new one at the amount it's bought in (store-bought units, \
plain grocery names). Change a step only where the new vegetable needs it (a different cook time, \
a different cut, a different pan).
- Update the dish's name only where the OLD vegetable is actually named in it (the word itself, \
not the whole dish); leave the rest of the name exactly as it is.
- Repeat main_protein back exactly as it already is in the dish — the protein does not change here.
- Update prep and cook minutes honestly for the new vegetable.
- Never use anything in must_not_contain or dislikes.
- The reason line is for the card: what changed, in one plain sentence, under twelve words, no \
exclamation mark.""",
    "carb": """A household is changing the CARB in one dish they have already planned. Rewrite the \
recipe around the new carb and change NOTHING else that doesn't have to change — this REPLACES the \
old carb; it does not add a second one alongside it.

- Keep the dish: its protein, its vegetable, its sauce, its other ingredients, its shape. The \
household liked this dish; they are changing one part.
- Swap the carb ingredient for the new one at the amount it's bought in (store-bought units, plain \
grocery names, the way a bag of rice or a box of couscous is bought). Change a step only where the \
new carb needs it.
- Update the dish's name only where the OLD carb is actually named in it (the word itself, not the \
whole dish); leave the rest of the name exactly as it is.
- Repeat main_protein back exactly as it already is in the dish — the protein does not change here.
- Update prep and cook minutes honestly for the new carb.
- Never use anything in must_not_contain or dislikes.
- The reason line is for the card: what changed, in one plain sentence, under twelve words, no \
exclamation mark.""",
}


_ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "item": {"type": "string"},
        "qty": {"type": "string"},
        "category": {"type": "string", "enum": ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"]},
    },
    "required": ["item"],
}

# What a change sends back instead of the whole recipe (Loop Board "Speed:
# Swap opens instantly and Change-a-part is quicker", 2026-09-30). The full
# rewrite was ~1000 output tokens for a one-line change and took 9-10 s in
# production; the diff is the changed rows and the edited steps, applied to
# the stored recipe by apply_variant_diff, so whatever the model doesn't
# mention is kept exactly as it is.
VARIANT_DIFF_TOOL = {
    "name": "submit_variant_diff",
    "description": "Only what changes in the dish around the changed part. Anything not named here stays as it is.",
    "input_schema": {
        "type": "object",
        "properties": {
            "meal_name": VARIANT_TOOL["input_schema"]["properties"]["meal_name"],
            "ingredients_changed": {
                "type": "array",
                "description": "An existing ingredient replaced by another: `replaces` is the existing row's item exactly as listed; the rest is the new row.",
                "items": {"type": "object", "properties": dict(_ROW_SCHEMA["properties"], replaces={"type": "string"}),
                          "required": ["replaces", "item"]},
            },
            "ingredients_added": {"type": "array", "description": "New rows the change needs.", "items": _ROW_SCHEMA},
            "ingredients_removed": {"type": "array", "description": "Existing items to drop, exactly as listed.", "items": {"type": "string"}},
            "steps_changed": {
                "type": "array",
                "description": "Steps that must read differently, by their number in `method`, with the whole new text of that step.",
                "items": {"type": "object", "properties": {"step": {"type": "integer"}, "text": {"type": "string"}}, "required": ["step", "text"]},
            },
            "steps_added": {
                "type": "array",
                "description": "New steps, placed after the numbered step (0 = at the start).",
                "items": {"type": "object", "properties": {"after": {"type": "integer"}, "text": {"type": "string"}}, "required": ["after", "text"]},
            },
            "steps_removed": {"type": "array", "description": "Step numbers to drop.", "items": {"type": "integer"}},
            "food_groups": VARIANT_TOOL["input_schema"]["properties"]["food_groups"],
            "main_protein": {"type": "string"},
            "prep_time_minutes": {"type": "integer"},
            "cook_time_minutes": {"type": "integer"},
            "reason": VARIANT_TOOL["input_schema"]["properties"]["reason"],
        },
        "required": ["meal_name", "main_protein", "reason"],
    },
}

_DIFF_KEYS = ("ingredients_changed", "ingredients_added", "ingredients_removed",
              "steps_changed", "steps_added", "steps_removed")


def _as_list(value) -> list:
    return [v for v in value if v] if isinstance(value, list) else []


def apply_variant_diff(recipe: dict, diff: dict) -> dict:
    """The changed recipe: the stored one with the model's diff applied.
    Returns the pick swap_in_place.apply_pick wants — every ingredient and
    EVERY step the recipe had, less what the diff removes. Step numbers are
    the ones the model was shown (1-based, in the stored order); a number
    that isn't one of them is ignored rather than guessed at. A `replaces`
    that matches no row is added as a new row, so a change is never lost
    for a slightly different spelling."""
    rows = [dict(r) for r in (recipe.get("ingredients") or []) if isinstance(r, dict)]
    by_name = lambda name: next((i for i, r in enumerate(rows)
                                 if (r.get("item") or "").strip().lower() == (name or "").strip().lower()), None)
    appended = []
    for ch in _as_list(diff.get("ingredients_changed")):
        if not isinstance(ch, dict) or not (ch.get("item") or "").strip():
            continue
        new = {k: ch[k] for k in ("item", "qty", "category") if ch.get(k)}
        at = by_name(ch.get("replaces"))
        if at is None:
            appended.append(new)
        else:
            rows[at] = dict(rows[at], **new) if not ch.get("qty") else new
    for gone in _as_list(diff.get("ingredients_removed")):
        at = by_name(gone if isinstance(gone, str) else "")
        if at is not None:
            rows.pop(at)
    rows += appended + [r for r in _as_list(diff.get("ingredients_added"))
                        if isinstance(r, dict) and (r.get("item") or "").strip()]

    steps = list(recipe.get("instructions") or [])
    edited = {}
    for ch in _as_list(diff.get("steps_changed")):
        if isinstance(ch, dict) and isinstance(ch.get("step"), int) and 1 <= ch["step"] <= len(steps) \
                and (ch.get("text") or "").strip():
            edited[ch["step"]] = ch["text"].strip()
    removed = {n for n in _as_list(diff.get("steps_removed")) if isinstance(n, int)}
    added: dict[int, list[str]] = {}
    for ch in _as_list(diff.get("steps_added")):
        if isinstance(ch, dict) and isinstance(ch.get("after"), int) and (ch.get("text") or "").strip():
            added.setdefault(max(0, min(ch["after"], len(steps))), []).append(ch["text"].strip())
    out_steps = list(added.get(0, []))
    for n, text in enumerate(steps, 1):
        if n not in removed:
            out_steps.append(edited.get(n, text))
        out_steps += added.get(n, [])

    pick = {
        "meal_name": diff.get("meal_name") or recipe.get("name") or "",
        "ingredients": rows,
        "instructions": out_steps,
        "reason": diff.get("reason") or "",
    }
    for key in ("food_groups", "cuisine", "main_protein", "prep_time_minutes", "cook_time_minutes"):
        value = diff.get(key)
        pick[key] = value if value not in (None, "", []) else recipe.get(key)
    return pick


def _is_diff(answer: dict) -> bool:
    """A change answers with a diff unless it carries a whole ingredient
    list (the full rewrite, which a recipe with nothing to diff against
    still gets)."""
    return not answer.get("ingredients") and (
        any(k in answer for k in _DIFF_KEYS) or bool(answer.get("meal_name")))


def _can_diff(recipe: dict | None, serves: int) -> bool:
    """A change is asked as a diff only against a written-up recipe whose
    amounts are already for the batch being cooked. Otherwise the unchanged
    rows would keep amounts for a different number of servings, and the
    whole recipe is rewritten at the right size as it always was."""
    if not recipe or recipe.get("details_pending"):
        return False
    if not recipe.get("ingredients") or not recipe.get("instructions"):
        return False
    return int(recipe.get("default_servings") or 4) == int(serves)


DIFF_ASK = (
    "The recipe is given whole, steps numbered. Answer with submit_variant_diff: ONLY the rows and "
    "steps that change — never the unchanged ones, never the whole recipe. Amounts are already for "
    "the right number of servings."
)


def _ask_variant(context: dict, role: str = "protein") -> dict:
    from .. import agent
    # change_part marks a context it wants a diff for; the marker is the
    # app's, not something to tell the model.
    context = dict(context)
    diff = bool(context.pop("_diff", False))
    client = agent._client()
    tool = VARIANT_DIFF_TOOL if diff else VARIANT_TOOL
    response = agent._create_with_retry(
        client,
        label="plate_part_change",
        model=agent.MODEL,
        max_tokens=900 if diff else 2000,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": VARIANT_INSTRUCTIONS[role] + (("\n\n" + DIFF_ASK) if diff else ""),
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": f"The dish and the change (JSON):\n{json.dumps(context, indent=2)}"},
            ],
        }],
        output_config=agent._effort_config("utility"),
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        logger.warning("plate_part_change hit max_tokens; the variant may be incomplete")
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input or {})
    return {}


def _variant_name(proposed: str, current: str, choice: str) -> str:
    """
    A name for the rewritten dish that cannot be mistaken for a recipe
    already saved. The model keeps the name when the protein isn't in it
    ("Chili"), and apply_pick saves a recipe only if its name is new — so
    "Chili" rewritten with turkey would be thrown away and the old Chili
    planned again, reported as a change. The same if the proposed name
    happens to be another saved recipe's. Either way the name says what
    changed: "Chili with ground turkey".
    """
    proposed = (proposed or "").strip()
    if not proposed:
        proposed = current
    taken = {(r.get("name") or "").strip().lower() for r in _recipes_list()}
    if proposed.lower() != current.strip().lower() and proposed.lower() not in taken:
        return proposed
    base = proposed if proposed.lower() != current.strip().lower() else current.strip()
    candidate = f"{base} with {choice.strip().lower()}"
    if candidate.lower() in taken:
        n = 2
        while f"{candidate} {n}".lower() in taken:
            n += 1
        candidate = f"{candidate} {n}"
    return candidate


def _recipes_list() -> list[dict]:
    from . import recipes as _recipes
    try:
        return _recipes.list_recipes()
    except Exception:
        return []


# Calm and plain, paired with its way out, and it names the state of the
# plan (DESIGN_SYSTEM §8) — the same shape as swap_in_place.REFUSAL.
REFUSAL = "I couldn’t make that change — the dish is as it was. Try a different protein, or tell me in the chat."


def change_part(weekly_plan_id: int, entry_id: int, role: str, choice: str, asker=None) -> dict:
    """
    Put the chosen protein, vegetable or carb into the dish by rewriting
    the recipe around it. `choice` is an option's name or what the
    household typed.

    Only ever reaches a part the DISH ITSELF covers (source "dish") — a
    MISSING vegetable or carb (the dashed "+ Add a carb" chip) isn't this,
    and nor is a part that's a SIDE the household already added: both of
    those go through the catalogue add-sheet instead
    (plates.suggest_additions/add_component/remove_component — the
    client's own "Add something" flow already replaces a side one-for-one
    there, with its own one-tap Undo; this function's job is the OTHER
    half, the part that lives in the recipe, which that flow can't touch).
    A caller that reaches here for a side-covered part is refused plainly
    rather than silently doing the wrong thing — verifier, 2026-09-22:
    the two paths must not both exist for the same case.

    Returns swap_in_place's own result shape (`status` 'changed' with the
    refreshed day, the new entry, the reason; or 'refused' with a plain
    message and nothing written) so the screen handles it exactly as it
    handles a swap — Undo included, through undo_meal_swap.

    A cook and the meals eating its leftovers are changed together, all at
    once (a separate fresh cook of the same dish is left alone), rewritten ONCE at the batch size (swap_in_place.batch_serves), with
    `days` beside `day` — the menu row's Swap, part by part.
    """
    if role not in ROLES:
        raise ValueError(f"No such part {role!r} — protein, vegetable or carb.")
    choice = (choice or "").strip()
    if not choice:
        raise ValueError(f"Say which {role}.")
    if len(choice) > 60:
        raise ValueError(f"That’s a bit long for a {role} — a few words is plenty.")
    _swap = _swap_mod()
    entry = _swap._entry(weekly_plan_id, entry_id)
    if entry["slot_state"] != "planned" or not entry["meal"]:
        raise ValueError("There's no meal on that slot to change.")
    # A night that has already gone by: a different part in a dinner
    # that has been eaten is still a rewrite of a night nobody can act on,
    # and on an approved week it still moves the shopping list. Asked above
    # the model call — apply_pick refuses it too, but only after a real API
    # call has been spent — and answered in this function's own refusal
    # shape, which the screen shows as a plain toast.
    _plan = _weekly_plan_mod()
    if _plan.night_has_gone(entry["date"]):
        return {"status": "refused", "message": _plan.NIGHT_GONE}
    recipe = _recipe_for(entry)
    if role == "protein":
        current = ((recipe or {}).get("main_protein") or "").strip()
    else:
        current, source = _current_part(entry, recipe, role, _plates.get_sides(entry_id))
        if source is None:
            raise ValueError(f"There's no {role} on this plate yet — add one instead.")
        if source == "side":
            raise ValueError(
                f"That {role} is a side you added — take it off and add a new one instead."
            )
    # Every meal still ahead that eats out of this meal's pot — its cook
    # and the meals eating that cook's leftovers, in any meal type
    # (swap_in_place.chain_days) — and not a separate fresh cook of the
    # same dish on another day. Emily, 2026-09-27: a veg change on
    # Thursday's gochujang beef left Friday's lunch and dinner (its
    # leftovers) on the old recipe, broke the chain, and cooked for 2
    # instead of 6.
    group = _swap.chain_days(weekly_plan_id, entry_id)
    if len(group) < 2:
        group = [entry]
    serves = _swap.batch_serves(weekly_plan_id, group, entry)
    as_diff = _can_diff(recipe, serves)
    context = _options_context(entry, recipe, role, current, whole_recipe=True)
    context[f"new_{role}"] = choice
    context["serves"] = serves
    context["serves_note"] = (
        f"Write every amount for {serves} servings — the whole batch this cook makes"
        + (", tonight and the meals eating its leftovers." if len(group) > 1 else ".")
    )
    if as_diff:
        context["_diff"] = True
    ask = asker or (lambda ctx: _ask_variant(ctx, role))
    pick = ask(context) or {}
    if as_diff and _is_diff(pick):
        # What changed, put on the stored recipe: every step it had, less
        # what the change drops (apply_variant_diff).
        pick = apply_variant_diff(recipe, pick)
    name = (pick.get("meal_name") or "").strip()
    if not name or not _swap._clean_ingredients(pick.get("ingredients")):
        logger.warning("plate_part_change came back with no usable dish")
        return {"status": "refused", "message": REFUSAL}
    pick["meal_name"] = _variant_name(name, entry["meal"], choice)
    if role == "protein":
        if not (pick.get("main_protein") or "").strip():
            pick["main_protein"] = choice.lower()
    else:
        # The protein doesn't change on a veg/carb edit — keep the dish's
        # own, whatever the model did or didn't repeat back.
        pick["main_protein"] = (recipe or {}).get("main_protein") or (pick.get("main_protein") or "")
    # The amounts were asked for `serves`; the recipe is saved at it, so
    # "Cooking for" reads the batch and never the model's own guess.
    pick["default_servings"] = serves
    # Every day's table is its own gate (swap_options._gate_all) — the
    # Friday table can be different people from Thursday's.
    why = next((w for w in (_swap.pick_gate(pick, e) for e in group) if w), None)
    if not why and len(group) > 1:
        why = _swap.cap_gate(weekly_plan_id, pick, group)
    if why:
        logger.warning("plate_part_change refused %r: %s", name, why)
        return {"status": "refused", "message": f"I left it as it was — {choice} {why}."}
    # The same dish with a different part is the same plate: the sides
    # go with it (carry_sides), where a swap to another dish leaves them.
    # correct_title=False: _variant_name above built this name to be unique,
    # not to describe the dish — see apply_pick for what reading it as a
    # promise does to a change of protein. Belt and braces today, because
    # that name's base is always one the household already uses and
    # honest_recipe_title refuses a correction onto a taken name anyway —
    # but this says the intent rather than leaning on the coincidence.
    # Several days go in ONE write that keeps the cook + reheat shape
    # (weekly_plan.replace_dish_on_days) — Undo on any of them puts every
    # day back together (swap_group).
    if len(group) > 1:
        out = _swap.apply_pick_to_days(weekly_plan_id, group, pick, carry_sides=True,
                                       correct_title=False, serves=serves)
    else:
        out = _swap.apply_pick(weekly_plan_id, entry, pick, carry_sides=True, correct_title=False)
    out["status"] = "changed"
    out["role"] = role
    out["choice"] = choice
    for member in group:
        forget_options(member["entry_id"])
    return out

