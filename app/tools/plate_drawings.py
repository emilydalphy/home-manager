"""
The drawn plate for a dish (Emily's plate library, 2026-11-05). Not plates.py
(the full-plate rule) and not plate_parts.py (protein, veg, carb on a meal):
this is the picture.

The drawings are generated from plates/specs by plates/build_plates.py:
static/plates/<key>.svg, one file per dish, and data/plate_index.json, the
facts matched against here. The API only ever carries a key; the screen
asks for the file, so the browser caches each drawing once and a Plan
payload grows by a word per row.

`recipes.plate_key` holds the plate a recipe was given. NULL means nothing
was stored (every recipe from before this, and a dish only the neutral plate
suits, so a later drawing can claim it): the plate is worked out on read and
written nowhere.

The order for any recipe (plate_for):
  1. the stored plate;
  2. the library dish it was copied from (recipes.library_id), which holds
     through an edited title, as long as the plate still fits (fits);
  3. its title, cleaned, against the library's names;
  4. the closest plate (closest): same main ingredient, then same dish type,
     then what the title and ingredients share. No model call.
  5. the neutral plate.

A plate is a promise (PLATE_RULES rule 6), so step 4 never offers a drawing
of something the dish doesn't have: no meat on a meatless dish, no nuts or
shrimp borrowed from a neighbour (_shows).

A tweak keeps its plate; only losing the main ingredient moves it
(plate_after_edit, rule 33). Leftovers are the dish's own plate (attach).
"""
from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from ..db import get_conn
from ._shared import household_id

logger = logging.getLogger("home_manager")

INDEX_FILE = Path(__file__).resolve().parents[2] / "data" / "plate_index.json"
NEUTRAL = "neutral"

MEATS = ("chicken", "turkey", "beef", "pork", "lamb", "fish", "shellfish")
MEATLESS = ("tofu", "bean", "egg", "cheese", "veg")

# Foods a recipe can hold without being its main ingredient: eggs in the
# meatball mix, cheese on top. Never evidence the main food changed.
_INCIDENTAL = frozenset(("egg", "cheese"))

# A close cousin, for the dish with no plate of its own main ingredient.
_COUSINS = {"chicken": "turkey", "turkey": "chicken", "beef": "lamb", "lamb": "beef"}

# What each main ingredient is called. Fish sauce, chicken broth and the like
# are seasoning, not the food: those lines are skipped (_NOT_THE_FOOD).
_FOODS = {
    "chicken": r"chicken|\bhens?\b|poussin|drumsticks?|tandoori",
    "turkey": r"turkey",
    "beef": r"\bbeef\b|(?<!tuna )(?<!swordfish )(?<!salmon )(?<!cauliflower )(?<!tofu )(?<!fish )\bsteaks?\b|brisket|sirloin|ribeye|rib[- ]eye|flank|oxtail|\bveal\b|hamburger|short ribs?|"
            r"\bchuck\b|cheeseburgers?|machaca|bulgogi|galbi|kalbi|pastrami|corned|bresaola|sucuk|pastirma",
    "pork": r"\bpork\b|bacon|\bham\b|(?<!chicken )(?<!turkey )sausages?|chorizo|prosciutto|pancetta|guanciale|lardons?|"
            r"kielbasa|pepperoni|salami|mortadella|andouille|\bspam\b|lap cheong|longaniza|nduja|soppressata|"
            r"\bcarnitas\b|\blechon\b|lardo\b|speck\b|capicola|pork belly|tocino",
    "lamb": r"\blamb\b|mutton|\bgoat\b",
    "fish": r"\bfish\b|salmon|\btuna\b|\bcod\b|tilapia|trout|halibut|\bsole\b|snapper|mackerel|sardines?|anchov|"
            r"haddock|branzino|\bbass\b|mahi|swordfish|catfish|bacalao|\bflounder\b|pollock|smoked fish|\beel\b|\broe\b",
    "shellfish": r"shrimps?|prawns?|scallops?|\bcrabs?\b|lobster|\bclams?\b|mussels?|oysters?(?! mushroom)|"
                 r"\bsquid\b|calamari|octopus|\bcockles?\b|crawfish|\bseafood\b",
    "tofu": r"\btofu\b|tempeh|seitan",
    "bean": r"lentils?|\bdal\b|\bdhal\b|chickpeas?|chana\b|garbanzos?|(?<!green )(?<!string )(?<!wax )(?<!long )"
            r"(?<!vanilla )(?<!coffee )(?<!cocoa )\bbeans?\b",
    "egg": r"\beggs?\b|omelet",
    "cheese": r"paneer|halloumi|\bcheese\b|mozzarella|ricotta|feta\b",
}
_FOOD_RES = {k: re.compile(v) for k, v in _FOODS.items()}
_NUTS = re.compile(r"peanuts?|almonds?|cashews?|walnuts?|pecans?|pistachios?|hazelnuts?|pine nuts?|macadamias?|\bnuts\b")
_NOT_THE_FOOD = re.compile(
    r"vegan|vegetarian|plant[- ]based|meatless|mock\b|imitation|\bveggie\b|\bsoy\b|\bbroth\b|\bstock\b|bouillon|"
    r"\bsauce\b|\bpaste\b|\bpowder\b|\boil$|\bfat\b|\bdrippings\b|seasoning|\bextract\b|\bgravy\b|\bdashi\b|\bbonito\b|\bflakes\b"
)
# "Tuna in olive oil" is tuna. Taken off before _NOT_THE_FOOD reads the line.
_PACKED_IN = re.compile(r"\s+in\s+(?:\w+\s+)?oil$")
# The parts of a drawing that are plainly a food, whatever the list says.
_PART_SHOWS = {
    "cashews": "nuts", "pecans": "nuts", "shrimp": "shellfish", "shell": "shellfish", "fillet": "fish",
    "wholefish": "fish", "smallfish": "fish", "bacon": "pork", "ribs": "pork", "yolk": "egg", "poached": "egg",
    "omelette": "egg", "teaegg": "egg", "tamagoyaki": "egg",
}

# The dish's shape, first match wins; names beat parts.
_TYPES = (
    ("drink", r"smoothie(?! bowl)|lassi|lemonade|horchata|milkshake|\bshakes?\b|\blatte\b|\bchai\b|\bjuice\b|mocktail|ayran"),
    ("porridge", r"oatmeal|porridge|\boats\b|congee|\bjuk\b|grits|muesli|granola"),
    ("griddle", r"pancakes?|waffles?|french toast|crepes?|blini|hotcakes?"),
    ("soup", r"\bsoups?\b|chowder|bisque|ramen|\bpho\b|pozole|gazpacho|minestrone|\bbroth\b|wonton|caldo|sopa\b|avgolemono|\budon soup\b"),
    ("salad", r"salads?\b|slaw\b|tabbouleh|fattoush|\bpoke\b|horiatiki"),
    ("taco", r"\btacos?\b|tostadas?|enchiladas?|fajitas?|quesadillas?|nachos|taquitos?|flautas?"),
    ("wrap", r"\bwraps?\b|burritos?|shawarma|gyros?|\broll[- ]ups?\b|lettuce cups?"),
    ("burger", r"burgers?\b|sliders?"),
    ("sandwich", r"sandwich|grilled cheese|banh mi|\btoast\b|panini|\bsub\b|hoagie|melts?\b|tartine|bruschetta|crostini|sloppy joe"),
    ("pizza", r"pizzas?\b|flatbreads?\b|calzones?|focaccia|pide\b|lahmacun|manakish"),
    ("noodle", r"noodles?|pasta|spaghetti|penne|rigatoni|bucatini|linguine|fettuccine|pappardelle|tagliatelle|carbonara|"
               r"tortellini|ravioli|bolognese|macaroni|mac and cheese|mac n cheese|ziti|lasagn|gnocchi|orzo|chow mein|"
               r"lo mein|pad thai|pad see|chow fun|japchae|\budon\b|\bsoba\b|vermicelli|orecchiette|\bcacio\b"),
    ("rice", r"\brice\b|risotto|paella|biryani|pilaf|pulao|bibimbap|\bdon\b|\bbowls?\b|jambalaya"),
    ("curry", r"curry|curries|masala|korma|vindaloo|tikka|\bdal\b|\bdhal\b|rendang|\bsaag\b|\bwat\b|tagine"),
    ("stew", r"\bstew\b|\bstews\b|chili\b|chilli\b|goulash|gumbo|ragu\b|ragout|casserole|bourguignon|cassoulet|\bpot pie\b|hotpot|hot pot"),
    ("stirfry", r"stir[- ]?fr|bokkeum|kung pao|general tso|\bsaute\b"),
    ("skewer", r"skewers?|kebabs?|kabobs?|satay|souvlaki|yakitori"),
    ("dumpling", r"dumplings?|pierogi|potstickers?|gyoza|empanadas?|samosas?|\bbao\b|momos?"),
    ("egg", r"omelet|frittata|shakshuka|scrambled|quiche|egg bites|egg cups|\begg muffins?\b|strata\b"),
    ("dip", r"\bdips?\b|hummus|guacamole|salsa\b|baba ghanoush|tzatziki"),
    ("dessert", r"cakes?\b|cookies?|brownies?|\bpie\b|tart\b|pudding|custard|mousse|ice cream|sorbet|gelato|baklava|"
                r"cobbler|crumble|muffins?|scones?|\bbread pudding\b|flan\b|truffles?\b|bark\b|bars\b|donuts?|churros"),
    ("roast", r"\broast(ed)?\b|\bbaked\b|\bgrilled\b|\bseared\b|\bbbq\b|\bbarbecue\b|\bsheet pan\b|meatloaf|meatballs?|cutlets?"),
)
_TYPE_RES = tuple((k, re.compile(v)) for k, v in _TYPES)
_PART_TYPES = {
    "glass": "drink", "tacos": "taco", "quesadilla": "taco", "pizza": "pizza", "burger": "burger",
    "sandwiches": "sandwich", "sando": "sandwich", "bread": "sandwich", "baguette": "sandwich", "toast": "sandwich",
    "wrap": "wrap", "noodles": "noodle", "ribbons": "noodle", "tortellini": "noodle", "skewers": "skewer",
    "pancakes": "griddle", "waffle": "griddle", "omelette": "egg", "muffin": "dessert", "madeleine": "dessert",
    "dipbowl": "dip", "pastries": "dumpling",
}

# Title words that say nothing about the dish. Cuisine and meal words are here
# so "Thai takeout" doesn't match "Thai Omelette".
_STOP = frozenset((
    "a an and the of in on with without for from style styled homemade home made easy quick simple classic "
    "best perfect weeknight weekend one pan pot sheet skillet slow cooker instant air fryer healthy light "
    "leftover leftovers tonight favorite favourite family our my your way free dairy gluten nut egg "
    "american italian french greek turkish lebanese mexican spanish indian chinese japanese korean thai "
    "vietnamese ethiopian middle eastern asian mediterranean southern breakfast lunch dinner snack meal "
    "plate night fresh new old good great big little mini"
).split())
# Removed from a title before the name lookup: the dish is the same without them.
_LOOSE_WORDS = re.compile(
    r"\b(?:easy|quick|simple|classic|homemade|weeknight|best|perfect|healthy|dairy[- ]free|gluten[- ]free|"
    r"nut[- ]free|egg[- ]free|leftovers?|one[- ]pan|one[- ]pot|sheet[- ]pan|slow[- ]cooker|instant pot)\b"
)
_COMMON_INGREDIENTS = frozenset(
    "salt pepper oil water garlic onion butter sugar flour olive vegetable black kosher ground fresh large "
    "small unsalted red white yellow green all purpose extra virgin cup can".split()
)


def _fold(text) -> str:
    """Lower case, no accents, single spaces: Jalapeño and jalapeno agree."""
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.lower().replace("-", " ").replace("’", "'")).strip()


def _title(name) -> str:
    """The dish's name with what a household's copy adds taken off:
    "Chicken Piccata (your way) 2" is Chicken Piccata."""
    text = re.sub(r"\s*\([^)]*\)(\s*\d+)?\s*$", "", _fold(name))
    return re.sub(r"\s+", " ", _LOOSE_WORDS.sub(" ", text)).strip()


def _singular(word: str) -> str:
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("oes"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(text) -> frozenset:
    return frozenset(_singular(w) for w in re.findall(r"[a-z0-9]+", _fold(text)) if w not in _STOP and len(w) > 1)


def _ingredient_words(items) -> frozenset:
    out = set()
    for item in items:
        out |= {w for w in _words(item) if w not in _COMMON_INGREDIENTS}
    return frozenset(out)


def _items(ingredients) -> tuple:
    """The ingredient names, whichever shape the list is in, as a hashable."""
    out = []
    for ing in ingredients or []:
        text = ing.get("item") if isinstance(ing, dict) else ing
        if str(text or "").strip():
            out.append(_fold(text))
    return tuple(out)


def _foods_in(text: str) -> set:
    return {food for food, rx in _FOOD_RES.items() if rx.search(text)}


def _foods_on_list(items) -> tuple:
    """The main foods on an ingredient list, first listed first."""
    seen: dict = {}
    for item in items:
        item = _PACKED_IN.sub("", item)
        if not _NOT_THE_FOOD.search(item):
            for food in _foods_in(item):
                seen.setdefault(food, True)
    return tuple(seen)


def _main_class(main_protein) -> str:
    """recipes.main_protein is free text; the class it names, or ''."""
    text = _fold(main_protein)
    if not text:
        return ""
    if re.search(r"vegetarian|vegan|\bnone\b|meatless|plant", text):
        return "veg"
    if "dairy" in text:
        return "cheese"
    if "seafood" in text:
        return "shellfish"
    found = _foods_in(text)
    for food in MEATS + MEATLESS:
        if food in found:
            return food
    return ""


def _main_of(name, main_protein, items) -> str:
    """The dish's main ingredient. The list outranks the main_protein column,
    which goes stale when a tweak changes the food, and the title is the last
    resort, for a dish not yet written up."""
    named = _main_class(main_protein)
    on_list = _foods_on_list(items)
    if named in MEATS and not set(on_list) - _INCIDENTAL:
        return named
    if on_list:
        if named in on_list:
            return named
        for group in (MEATS, MEATLESS):
            for food in on_list:
                if food in group:
                    return food
    if named:
        return named
    in_title = _foods_in(_title(name))
    for food in MEATS + MEATLESS:
        if food in in_title:
            return food
    return ""


def _shows(items, parts=()) -> frozenset:
    """The foods a plate would be promising: the meat, fish and nuts on the
    list, plus anything the drawing plainly shows."""
    out = {f for f in _foods_on_list(items) if f in MEATS}
    if any(_NUTS.search(i) and not _NOT_THE_FOOD.search(i) for i in items):
        out.add("nuts")
    out |= {_PART_SHOWS[p] for p in parts if p in _PART_SHOWS}
    return frozenset(out)


def _type_of(text: str) -> str:
    for kind, rx in _TYPE_RES:
        if rx.search(text):
            return kind
    return ""


class _Plate:
    __slots__ = ("key", "name", "main", "kind", "cuisine", "meal", "words", "ingredients", "shows")

    def __init__(self, row: dict):
        items = _items(row["ingredients"])
        self.key = row["key"]
        self.name = row["name"]
        self.main = _main_class(row["main_protein"])
        self.cuisine = _fold(row["cuisine"])
        self.meal = row["meal"]
        self.words = _words(_title(row["name"]))
        self.ingredients = _ingredient_words(items)
        self.kind = _type_of(_title(row["name"])) or next(
            (_PART_TYPES[p] for p in row["parts"] if p in _PART_TYPES), "")
        self.shows = _shows(items, row["parts"]) | ({self.main} if self.main in MEATS else frozenset())


class _Library:
    def __init__(self, path: Path):
        rows = json.loads(path.read_text())["plates"]
        self.plates = [_Plate(r) for r in rows]
        self.by_key = {p.key: p for p in self.plates}
        self.by_name: dict = {}
        for p in self.plates:
            self.by_name.setdefault(_fold(p.name), p)
            self.by_name.setdefault(_title(p.name), p)
        df: dict = {}
        for p in self.plates:
            for word in p.words:
                df[word] = df.get(word, 0) + 1
        self.size = len(self.plates)
        self.rarity = {w: math.log(1 + self.size / n) for w, n in df.items()}

    def weight(self, words) -> float:
        rare = math.log(1 + self.size)
        return sum(self.rarity.get(w, rare) for w in words)


@lru_cache(maxsize=1)
def _library() -> _Library:
    return _Library(INDEX_FILE)


def is_plate(key) -> bool:
    return key == NEUTRAL or key in _library().by_key


def library_keys() -> list[str]:
    return [p.key for p in _library().plates]


def key_for_name(name) -> str | None:
    """The library plate of exactly this dish, or None."""
    lib = _library()
    found = lib.by_name.get(_fold(name)) or lib.by_name.get(_title(name))
    return found.key if found else None


def fits(key, ingredients) -> bool:
    """Does this plate still draw what the list says? False only when the
    list positively names a different main food. A list that can't tell
    (no list, a word we don't know) keeps the plate."""
    plate = _library().by_key.get(key)
    if not plate:
        return False
    on_list = set(_foods_on_list(_items(ingredients)))
    if plate.main in MEATS:
        return plate.main in on_list or not on_list - _INCIDENTAL
    # A meatless plate on a dish with meat in it is the wrong dish, and on a
    # meatless one it is near enough: tofu for paneer keeps its plate.
    return not on_list & set(MEATS)


def _score(dish_words, dish_kind, main, cuisine, ingredient_words, plate: _Plate, lib: _Library) -> float:
    shared = dish_words & plate.words
    title = 0.0
    if shared:
        title = 2 * lib.weight(shared) / (lib.weight(dish_words) + lib.weight(plate.words))
    overlap = 0.0
    if ingredient_words and plate.ingredients:
        overlap = len(ingredient_words & plate.ingredients) / math.sqrt(len(ingredient_words) * len(plate.ingredients))
    return (20 * title + 6 * (dish_kind == plate.kind) + 3 * (main == plate.main) + 4 * overlap
            + (1 if cuisine and cuisine == plate.cuisine else 0) + (0.5 if plate.meal == "dinner" else 0))


def _best(pool, dish_words, kind, main, cuisine, ingredient_words, lib) -> _Plate | None:
    scored = ((_score(dish_words, kind, main, cuisine, ingredient_words, p, lib), p.key, p) for p in pool)
    # Highest score; the key settles a tie, so a dish always gets the same plate.
    best = max(scored, key=lambda s: (s[0], s[1]), default=None)
    return best[2] if best else None


@lru_cache(maxsize=4096)
def _closest(name: str, main_protein: str, items: tuple, cuisine: str) -> str:
    lib = _library()
    title = _title(name)
    main = _main_of(name, main_protein, items)
    named = set(_foods_on_list(items)) if items else _foods_in(title)
    allowed = {f for f in named if f in MEATS or f == "egg"} | set(_shows(items))
    if main in MEATS or main == "egg":
        allowed.add(main)
    if not items and _NUTS.search(title):
        allowed.add("nuts")

    exact = lib.by_name.get(_fold(name)) or lib.by_name.get(title)
    if exact and fits(exact.key, items):
        return exact.key

    words = _words(title)
    kind = _type_of(title)
    ingredient_words = _ingredient_words(items)
    honest = [p for p in lib.plates if p.shows <= allowed]

    if main in MEATS:
        family = [p for p in honest if p.main == main]
        cousin = _COUSINS.get(main)
        cousins = [p for p in lib.plates if p.main == cousin and p.shows <= allowed | {cousin}]
    else:
        family = [p for p in honest if p.main not in MEATS]
        cousins = []

    # Same food and same kind of dish; a lamb burger takes the beef burger
    # before it takes a lamb stew, because the shape is what reads.
    for pool in (family, cousins):
        same_kind = [p for p in pool if kind and p.kind == kind]
        if same_kind:
            return _best(same_kind, words, kind, main, cuisine, ingredient_words, lib).key
    if main in MEATS and (family or cousins):
        return _best(family or cousins, words, kind, main, cuisine, ingredient_words, lib).key
    # Near enough: shares a word of the name, or three foods of the list. The
    # best of those, so a closer-scoring plate that is not near enough (two
    # foods in common) can't hide one that is.
    near = [p for p in family if words & p.words or len(ingredient_words & p.ingredients) >= 3]
    if near and (main or words):
        return _best(near, words, kind, main, cuisine, ingredient_words, lib).key
    return NEUTRAL


def closest(name, main_protein="", ingredients=None, cuisine="") -> str:
    """The plate for a dish we have no drawing of, or the neutral plate.
    Never raises on odd input: a picture must not cost a save or a screen."""
    try:
        return _closest(str(name or ""), str(main_protein or ""), _items(ingredients), _fold(cuisine))
    except Exception:
        logger.exception("Plate match failed for %r", name)
        return NEUTRAL


def plate_for(name, main_protein="", ingredients=None, *, stored=None, library_dish=None, cuisine="") -> str:
    """The plate for this recipe, stored or not. See the module's docstring."""
    try:
        if stored and is_plate(stored):
            return stored
        if library_dish:
            key = key_for_name(library_dish)
            if key and fits(key, ingredients):
                return key
    except Exception:
        logger.exception("Plate lookup failed for %r", name)
    return closest(name or library_dish, main_protein, ingredients, cuisine)


def settle(conn, recipe_id: int, old_ingredients_json: str | None = None) -> None:
    """Write recipes.plate_key after the row's list changed, on the caller's
    connection, inside its transaction. With the list as it was: an edit, so
    the plate stays unless its food went (plate_after_edit). Without: the
    recipe has just been written up, so the plate is chosen afresh."""
    row = conn.execute(
        f"SELECT {_RECIPE_COLUMNS} FROM recipes r LEFT JOIN recipe_library l ON l.id = r.library_id "
        "WHERE r.id = ? AND r.household_id = ?",
        (int(recipe_id), household_id()),
    ).fetchone()
    if not row:
        return
    new = _json_list(row["ingredients_json"])
    if old_ingredients_json is None:
        key = to_store(plate_for(row["name"], row["main_protein"], new, library_dish=row["library_dish"],
                                 cuisine=row["cuisine"]))
    else:
        key = plate_after_edit(row["plate_key"], row["name"], row["main_protein"], _json_list(old_ingredients_json),
                               new, library_dish=row["library_dish"])
    if key != row["plate_key"]:
        conn.execute("UPDATE recipes SET plate_key = ? WHERE id = ? AND household_id = ?",
                     (key, int(recipe_id), household_id()))


def to_store(key: str) -> str | None:
    """What goes in recipes.plate_key: nothing for the neutral plate, so a
    drawing added later can claim the dish."""
    return None if not key or key == NEUTRAL else key


def plate_after_edit(stored, name, main_protein, old_ingredients, new_ingredients, *, library_dish=None) -> str | None:
    """recipes.plate_key after a change to the recipe (rule 33): the plate it
    had, unless the edit took away the food that plate draws."""
    current = plate_for(name, main_protein, old_ingredients, stored=stored, library_dish=library_dish)
    if current != NEUTRAL and fits(current, new_ingredients):
        return current
    return to_store(closest(name, main_protein, new_ingredients))


# ---------- the API's side ----------

_RECIPE_COLUMNS = (
    "r.id AS recipe_id, r.name, r.plate_key, r.main_protein, r.ingredients_json, r.cuisine, l.dish_name AS library_dish"
)


def _json_list(text) -> list:
    try:
        value = json.loads(text or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _plate_of_row(row) -> str:
    return plate_for(row["name"], row["main_protein"], _json_list(row["ingredients_json"]), stored=row["plate_key"],
                     library_dish=row["library_dish"], cuisine=row["cuisine"])


def plate_for_recipe(recipe_id: int, conn=None) -> str:
    """The plate of one of this household's recipes, by id."""
    own = conn or get_conn()
    try:
        row = own.execute(
            f"SELECT {_RECIPE_COLUMNS} FROM recipes r LEFT JOIN recipe_library l ON l.id = r.library_id "
            "WHERE r.id = ? AND r.household_id = ?",
            (int(recipe_id), household_id()),
        ).fetchone()
    finally:
        if conn is None:
            own.close()
    return _plate_of_row(row) if row else NEUTRAL


def attach_to_recipes(recipes: list[dict]) -> list[dict]:
    """Sets `plate` on each of a list of recipes (each with an `id`)."""
    ids = [int(r["id"]) for r in recipes if r.get("id")]
    if not ids:
        return recipes
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT {_RECIPE_COLUMNS} FROM recipes r LEFT JOIN recipe_library l ON l.id = r.library_id "
            f"WHERE r.household_id = ? AND r.id IN ({','.join('?' * len(ids))})",
            [household_id(), *ids],
        ).fetchall()
    finally:
        conn.close()
    plates = {r["recipe_id"]: _plate_of_row(r) for r in rows}
    for recipe in recipes:
        recipe["plate"] = plates.get(recipe.get("id"), NEUTRAL)
    return recipes


def _entry_dish(entry: dict) -> str:
    """The dish a meal row reads as: a leftovers night is its source's dish."""
    source = entry.get("leftover_from") or entry.get("leftovers_from")
    if isinstance(source, dict) and source.get("meal"):
        return str(source["meal"])
    return str(entry.get("meal") or entry.get("title") or "")


def attach(entries: list[dict]) -> list[dict]:
    """Sets `plate` on meal rows (each with an `entry_id`), from one read.
    Not on get_weekly_plan or get_cooker_view themselves, because the chat
    reads those and a word per row is a word per row of model input.

    A row with no dish, or a takeout night, gets no key. A failure here leaves
    the rows as they were: the screen draws without plates."""
    rows = [e for e in entries if isinstance(e, dict) and e.get("entry_id") and _entry_dish(e)]
    if not rows:
        return entries
    try:
        ids = [int(e["entry_id"]) for e in rows]
        by_entry: dict = {}
        by_name: dict = {}
        conn = get_conn()
        try:
            for start in range(0, len(ids), 200):
                chunk = ids[start:start + 200]
                found = conn.execute(
                    f"SELECT mpe.id AS entry_id, {_RECIPE_COLUMNS} FROM meal_plan_entries mpe "
                    "JOIN recipes r ON r.id = mpe.recipe_id LEFT JOIN recipe_library l ON l.id = r.library_id "
                    f"WHERE mpe.household_id = ? AND mpe.id IN ({','.join('?' * len(chunk))})",
                    [household_id(), *chunk],
                ).fetchall()
                by_entry.update({r["entry_id"]: r for r in found})
            # A leftovers row with no recipe of its own reads its source's
            # dish; a freeform row may still name a saved recipe.
            names = sorted({_entry_dish(e) for e in rows if int(e["entry_id"]) not in by_entry})
            if names:
                found = conn.execute(
                    f"SELECT name_key(r.name) AS name_key, {_RECIPE_COLUMNS} FROM recipes r "
                    "LEFT JOIN recipe_library l ON l.id = r.library_id "
                    f"WHERE r.household_id = ? AND name_key(r.name) IN ({','.join('name_key(?)' for _ in names)})",
                    [household_id(), *names],
                ).fetchall()
                by_name = {r["name_key"]: r for r in found}
        finally:
            conn.close()
        from ..db import name_key
        for entry in rows:
            if entry.get("source") == "takeout" or entry.get("is_takeout"):
                continue
            row = by_entry.get(int(entry["entry_id"])) or by_name.get(name_key(_entry_dish(entry)))
            entry["plate"] = _plate_of_row(row) if row else closest(_entry_dish(entry))
    except Exception:
        logger.exception("Couldn't attach plates")
    return entries


def week_menu_entries(menu: dict) -> list[dict]:
    """Every meal row on a get_week_menu payload, snacks included."""
    out: list = []
    for day in menu.get("days") or []:
        for value in day.values():
            if isinstance(value, dict) and value.get("entry_id"):
                out.append(value)
            elif isinstance(value, list):
                out.extend(v for v in value if isinstance(v, dict) and v.get("entry_id"))
    return out
