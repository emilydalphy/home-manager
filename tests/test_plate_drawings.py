"""
The drawn plates (Emily's plate library, 2026-11-05; plates/PLATE_RULES.md).

Pinned here:
  * every recipe in the shared library has a drawing, and what is committed
    matches plates/specs;
  * the files reach the Docker image and are what the API's keys point at;
  * a runtime dish gets its plate at save, with no model call, and a title
    that differs from the library's still lands on the library's plate;
  * a tweak keeps its plate, losing the main ingredient moves it, a plate
    never promises a food the dish hasn't got (rule 6);
  * an unknown dish gets the neutral plate; leftovers show the dish's plate;
  * the JS helper's sizes, shadow and fallback.
"""
from __future__ import annotations

import fnmatch
import importlib.util
import json
import pathlib
import re

import nodeharness
import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import plate_drawings as drawings
from conftest import household_date
from test_recipe_change import MON, TUE, _entry, _plan, _recipe
from test_recipe_change_followups import _chain

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLATES = ROOT / "static" / "plates"


def _builder():
    spec = importlib.util.spec_from_file_location("build_plates", ROOT / "plates" / "build_plates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stored(name):
    conn = get_conn()
    try:
        return conn.execute("SELECT plate_key FROM recipes WHERE name = ?", (name,)).fetchone()["plate_key"]
    finally:
        conn.close()


def _rows(menu):
    return tools.week_menu_entries(menu)


# ---------- the library ----------

def test_every_library_recipe_has_a_plate():
    seed = json.loads((ROOT / "data" / "recipe_library_seed.json").read_text())["dishes"]
    batches = [json.loads(p.read_text())["dish_name"] for p in (ROOT / "data" / "recipe_library_batches").glob("*.json")]
    samples = [d["dish_name"] for d in json.loads((ROOT / "data" / "recipe_library_samples.json").read_text())["samples"]]
    names = {d["dish_name"] for d in seed} | set(batches) | set(samples)
    assert len(names) == 2137
    for name in names:
        key = drawings.key_for_name(name)
        assert key, name
        assert (PLATES / f"{key}.svg").is_file(), name


def test_each_library_recipe_resolves_to_its_own_plate_with_its_own_list():
    """fits() has to read the library's own lists: tuna in oil, Beefsteak
    tomatoes, Sucuk. Any of those misread moves a plate that shouldn't move."""
    rows = json.loads(drawings.INDEX_FILE.read_text())["plates"]
    wrong = [r["name"] for r in rows
             if drawings.plate_for(r["name"], r["main_protein"], r["ingredients"]) != r["key"]]
    assert wrong == []


def test_what_is_committed_matches_the_specs():
    builder = _builder()
    made = builder.drawings()
    on_disk = {p.name: p.read_text() for p in PLATES.glob("*.svg")}
    assert set(on_disk) == set(made), "run python3 plates/build_plates.py"
    assert [n for n in made if made[n] != on_disk[n]] == [], "run python3 plates/build_plates.py"
    assert json.loads(drawings.INDEX_FILE.read_text()) == builder.index(), "run python3 plates/build_plates.py"


def test_each_drawing_is_a_complete_small_svg():
    sizes = []
    for path in PLATES.glob("*.svg"):
        text = path.read_text()
        assert text.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'), path.name
        assert text.endswith("</svg>"), path.name
        assert "<script" not in text and "href" not in text, path.name
        sizes.append(len(text))
    assert sum(sizes) / len(sizes) < 8000
    assert max(sizes) < 30000


def test_the_drawings_and_the_index_reach_the_image():
    ignored = [line.strip() for line in (ROOT / ".dockerignore").read_text().splitlines()
               if line.strip() and not line.startswith("#")]
    for shipped in ("static/plates/neutral.svg", "static/plates.js", "data/plate_index.json"):
        for rule in ignored:
            assert not fnmatch.fnmatch(shipped, rule) and not shipped.startswith(rule), (shipped, rule)


def test_a_drawing_is_served_to_a_signed_in_page_with_a_cache_header(signed_in):
    res = signed_in.get("/static/plates/chicken-piccata.svg")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/svg+xml")
    assert res.headers["cache-control"] == "private, max-age=86400"


# ---------- a new dish ----------

def test_a_new_dish_gets_a_plate_at_save_with_no_model_call(monkeypatch):
    def no_model(*_a, **_k):
        raise AssertionError("a plate must not call a model")
    monkeypatch.setattr(agent, "get_client", no_model, raising=False)
    tools.add_recipe("Sheet-Pan Harissa Salmon", [{"item": "Salmon fillets", "qty": "4"}], main_protein="fish")
    key = _stored("Sheet-Pan Harissa Salmon")
    assert key and drawings.is_plate(key)
    assert drawings._library().by_key[key].main == "fish"


@pytest.mark.parametrize("title, main, expected", [
    ("Chicken Piccata", "chicken", "chicken-piccata"),
    ("chicken  piccata!", "", "chicken-piccata"),
    ("Easy Chicken Piccata", "", "chicken-piccata"),
    ("Chicken Piccata (your way) 2", "chicken", "chicken-piccata"),
    ("Lemony Chicken Piccata with Capers", "", "chicken-piccata"),
    ("Emily's Chicken Piccata", "chicken", "chicken-piccata"),
    ("Dairy-Free Chana Masala", "", "chana-masala"),
])
def test_a_title_need_not_match_the_library_exactly(title, main, expected):
    assert drawings.closest(title, main) == expected


def test_library_lineage_holds_through_a_retitled_recipe():
    key = drawings.plate_for("Thursday's lemony one", "chicken", [{"item": "Chicken breasts"}],
                             library_dish="Chicken Piccata")
    assert key == "chicken-piccata"


@pytest.mark.parametrize("title, main, kind, food", [
    ("Weeknight Turkey Chili", "turkey", "stew", "turkey"),
    ("Saturday Fish Tacos", "fish", "taco", "fish"),
    ("Pork Fried Rice Bowl", "pork", "rice", "pork"),
    ("Lamb Burgers", "lamb", "burger", "beef"),   # no lamb burger: the cousin's shape
])
def test_closest_goes_by_main_ingredient_and_dish_type(title, main, kind, food):
    plate = drawings._library().by_key[drawings.closest(title, main)]
    assert plate.kind == kind and plate.main == food


def test_a_runtime_recipe_is_stable():
    first = drawings.closest("Zesty Garlic Shrimp", "shrimp")
    drawings._closest.cache_clear()
    assert drawings.closest("Zesty Garlic Shrimp", "shrimp") == first


# ---------- a plate is a promise (rule 6) ----------

@pytest.mark.parametrize("title, ingredients", [
    ("Spicy Noodles", ["Rice noodles", "Tofu", "Chili crisp"]),
    ("Vegetable Curry", ["Potatoes", "Cauliflower", "Coconut milk"]),
    ("Bean Soup", ["White beans", "Vegetable broth", "Kale"]),
])
def test_a_meatless_dish_never_borrows_a_meat_plate(title, ingredients):
    key = drawings.closest(title, "vegetarian", ingredients)
    assert key != drawings.NEUTRAL
    assert not drawings._library().by_key[key].shows & set(drawings.MEATS)


def test_a_dish_with_no_nuts_or_shellfish_never_borrows_them():
    for title in ("Rice Noodle Stir-Fry", "Chicken Noodle Bowl", "Thai Chicken Curry"):
        key = drawings.closest(title, "chicken", ["Chicken thighs", "Rice noodles", "Scallions", "Soy sauce"])
        assert not drawings._library().by_key[key].shows & {"nuts", "shellfish"}, (title, key)


def test_seasoning_is_not_the_main_food():
    items = drawings._items(["Tofu", "Fish sauce", "Chicken broth", "Oyster sauce", "Bean sauce"])
    assert drawings._foods_on_list(items) == ("tofu",)


# ---------- a tweak ----------

def test_a_small_tweak_keeps_the_plate():
    old = ["Chicken breasts", "Lemon", "Capers"]
    assert drawings.plate_after_edit(None, "Chicken Piccata", "chicken", old, old + ["Parsley"]) == "chicken-piccata"


def test_a_new_main_ingredient_moves_to_a_closer_plate():
    old = ["Chicken breasts", "Lemon", "Capers"]
    key = drawings.plate_after_edit(None, "Chicken Piccata", "chicken", old, ["Firm tofu", "Lemon", "Capers"])
    assert key != "chicken-piccata"
    assert drawings._library().by_key[key].main == "tofu"


def test_a_rewrite_keeps_a_recipes_plate(signed_in):
    plan = _plan("approved")
    _recipe("Chicken Piccata", [("Chicken breasts", "2"), ("Lemon", "1")])
    entry = _entry(plan, TUE, "Chicken Piccata")
    assert _stored("Chicken Piccata") == "chicken-piccata"
    tools.apply_rewrite(tools.rewrite_spec(entry), {
        "ingredients": [{"item": "Chicken breasts", "qty": "2"}, {"item": "Lemon", "qty": "2"}],
        "instructions": ["Sear.", "Sauce."],
    }, "more lemon")
    assert _stored("Chicken Piccata") == "chicken-piccata"


def test_a_rewrite_that_changes_the_main_ingredient_moves_it_and_undo_puts_it_back(signed_in):
    plan = _plan("approved")
    _recipe("Chicken Piccata", [("Chicken breasts", "2"), ("Lemon", "1")])
    entry = _entry(plan, TUE, "Chicken Piccata")
    result = tools.apply_rewrite(tools.rewrite_spec(entry), {
        "ingredients": [{"item": "Firm tofu", "qty": "1 block"}, {"item": "Lemon", "qty": "1"}],
        "instructions": ["Sear.", "Sauce."],
    }, "make it tofu")
    moved = _stored("Chicken Piccata")
    assert moved != "chicken-piccata" and drawings._library().by_key[moved].main == "tofu"

    assert tools.undo_recipe_change(result["request_id"])["status"] != "refused"
    assert _stored("Chicken Piccata") == "chicken-piccata"


# ---------- nothing breaks ----------

@pytest.mark.parametrize("title", ["Zorblax Surprise", "Mystery Meat", "", None, "!!!", "x"])
def test_an_unknown_dish_gets_the_neutral_plate(title):
    assert drawings.closest(title) == drawings.NEUTRAL


def test_odd_lists_do_not_break_a_match():
    assert drawings.closest("Chicken Thing", "chicken", [None, 3, {"item": None}, {"qty": "1"}, "Chicken"])


def test_a_stored_plate_with_no_drawing_is_not_shown():
    assert drawings.is_plate(drawings.plate_for("Chicken Piccata", "chicken", stored="gone-now"))


@pytest.mark.parametrize("name, ingredients, borrowed, own", [
    ("Apple Slices with Cheddar and Pretzels", ["apples", "cheddar cheese", "pretzels"], "soft-pretzels",
     "apple-slices-with-cheddar-and-pretzels"),
    ("Greek Yogurt with Berries and Granola", ["plain Greek yogurt", "berries", "granola"], "oatmeal-raisin-cookies",
     "greek-yogurt-with-berries-and-granola"),
])
def test_a_dishs_own_drawing_replaces_a_borrowed_plate_stored_before_it(name, ingredients, borrowed, own):
    # Emily, 2026-11-09: her saved snacks kept a pretzel and a cookie after
    # their own plates were drawn.
    assert drawings.plate_for(name, "", ingredients, stored=borrowed) == own


# Everyday snacks whose borrowed plate already shows every part (rule 42).
_SNACKS_ON_A_LIBRARY_PLATE = {
    "Turkey and Cheese Roll-Ups with Cucumber Spears", "Edamame with Sea Salt", "Egg Muffins with Spinach and Feta",
    "Oat and Banana Breakfast Cookies", "Roasted Chickpeas", "Black Bean and Corn Salsa with Tortilla Chips",
    "Frozen Yogurt Bark with Berries", "Bean and Cheese Nachos with Tomato", "Spanakopita Triangles",
    "Ants on a Log with Almond Butter and Dried Cherries",
}


def _everyday_snacks():
    data = json.loads((ROOT / "data" / "everyday_snacks.json").read_text())
    rows = data if isinstance(data, list) else data.get("snacks", data)
    for row in rows:
        yield row
        if row.get("nut_free_swap"):
            yield row["nut_free_swap"]


def test_every_everyday_snack_has_a_plate_that_shows_it():
    # Emily, 2026-11-10: rice cakes drew as fried rice, pear as cinnamon rolls.
    for snack in _everyday_snacks():
        name = snack["name"]
        if name in _SNACKS_ON_A_LIBRARY_PLATE:
            continue
        key = drawings.plate_for(name, "", [line[0] for line in snack["lines"]])
        assert key == drawings.key_for_name(name), f"{name} borrows {key}"


def test_goat_cheese_is_cheese_not_goat_meat():
    assert drawings.plate_for("Dates Stuffed with Goat Cheese", "", ["medjool dates", "goat cheese"]) \
        == "dates-stuffed-with-goat-cheese"


def test_an_own_drawing_that_no_longer_fits_leaves_the_stored_plate():
    # Rule 33: the edit moved the plate because the main food changed.
    assert drawings.plate_for("Chicken Piccata", "tofu", ["tofu", "capers"], stored="crispy-tofu-bites") == "crispy-tofu-bites"


def test_a_neutral_dish_stores_nothing_so_a_later_drawing_can_claim_it():
    tools.add_recipe("Zorblax Surprise", [{"item": "Zorblax", "qty": "1"}])
    assert _stored("Zorblax Surprise") is None


def test_an_older_recipe_is_worked_out_on_read_and_nothing_is_written(signed_in):
    tools.add_recipe("Chicken Piccata", [{"item": "Chicken breasts", "qty": "2"}])
    conn = get_conn()
    conn.execute("UPDATE recipes SET plate_key = NULL")
    conn.commit()
    conn.close()
    recipe_id = tools.get_recipe("Chicken Piccata")["id"]
    assert signed_in.get(f"/api/recipes/{recipe_id}").json()["plate"] == "chicken-piccata"
    assert _stored("Chicken Piccata") is None


def test_a_recipes_plate_stays_inside_its_household(signed_in):
    assert tools.plate_for_recipe(999999) == drawings.NEUTRAL


# ---------- the API ----------

def test_the_week_menu_carries_plates_and_leftovers_show_the_dish(signed_in):
    plan = _plan("approved")
    _recipe("Chicken Piccata", [("Chicken breasts", "2"), ("Lemon", "1")])
    cook = _entry(plan, MON, "Chicken Piccata")
    reheat = _entry(plan, TUE, "Chicken Piccata", slot="lunch")
    _chain(cook, reheat, MON, TUE)

    rows = {r["entry_id"]: r for r in _rows(signed_in.get("/api/week-menu").json())}
    assert rows[cook]["plate"] == "chicken-piccata"
    assert rows[reheat]["plate"] == "chicken-piccata"


def test_a_freeform_leftovers_row_reads_its_sources_plate():
    tools.add_recipe("Chana Masala", [{"item": "Chickpeas", "qty": "2 cans"}])
    conn = get_conn()
    hid = 1
    plan = conn.execute("INSERT INTO weekly_plans (household_id, week_start_date) VALUES (?, ?)",
                        (hid, household_date(0))).lastrowid
    entry = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal) "
        "VALUES (?, ?, ?, 'lunch', 'Leftovers')", (hid, plan, household_date(0))).lastrowid
    conn.commit()
    conn.close()
    row = {"entry_id": entry, "title": "Leftovers", "leftover_from": {"meal": "Chana Masala"}}
    tools.attach_plates([row])
    assert row["plate"] == "chana-masala"


def test_the_cook_view_carries_plates_and_the_chats_tool_does_not(signed_in):
    plan = _plan("approved")
    _recipe("Chicken Piccata", [("Chicken breasts", "2"), ("Lemon", "1")])
    _entry(plan, TUE, "Chicken Piccata")
    meals = signed_in.get("/api/cooker-view", params={"weekly_plan_id": plan}).json()["meals"]
    assert [m["plate"] for m in meals if m["meal"] == "Chicken Piccata"] == ["chicken-piccata"]
    assert all("plate" not in m for m in tools.get_cooker_view(plan)["meals"])
    assert all("plate" not in m for m in tools.get_weekly_plan(plan)["meals"])


def test_the_recipe_list_and_page_carry_plates(signed_in):
    tools.add_recipe("Chicken Piccata", [{"item": "Chicken breasts", "qty": "2"}],
                     instructions=["Sear."], default_servings=4)
    body = signed_in.get("/api/recipes").json()
    assert [r["plate"] for r in body["recipes"]] == ["chicken-piccata"]
    assert signed_in.get(f"/api/recipes/{body['recipes'][0]['id']}").json()["plate"] == "chicken-piccata"


def test_a_takeout_night_has_no_plate():
    row = {"entry_id": 1, "title": "Takeout", "source": "takeout"}
    tools.attach_plates([row])
    assert "plate" not in row


def test_a_failed_attach_leaves_the_rows_alone(monkeypatch):
    def broken():
        raise RuntimeError("no database")
    monkeypatch.setattr(drawings, "get_conn", broken)
    row = {"entry_id": 1, "title": "Chicken Piccata"}
    assert tools.attach_plates([row]) == [row]
    assert "plate" not in row


# ---------- the JS helper ----------

PLATES_JS = (ROOT / "static" / "plates.js").read_text(encoding="utf-8")


def _js(body: str) -> dict:
    script = "var listeners = [];\nvar document = { addEventListener: function (n, f, c) { listeners.push([n, f, c]); } };\n" + \
        PLATES_JS + "\n" + body
    result = nodeharness.run_node(script)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_helper_renders_at_a_given_size():
    out = _js("console.log(JSON.stringify({ a: Plates.html('chicken-piccata', 44), b: Plates.html('chana-masala', 96.4) }))")
    assert 'src="/static/plates/chicken-piccata.svg"' in out["a"]
    assert 'width="44" height="44"' in out["a"]
    assert 'width="96" height="96"' in out["b"]
    assert 'alt=""' in out["a"] and 'loading="lazy"' in out["a"]


def test_the_helper_draws_nothing_with_no_key_and_drops_the_shadow_on_dark():
    out = _js("console.log(JSON.stringify({ none: Plates.html('', 44), undef: Plates.html(undefined, 44), "
              "dark: Plates.html('neutral', 44, { onDark: true }), light: Plates.html('neutral', 44) }))")
    assert out["none"] == "" and out["undef"] == ""
    assert "dish-plate on-dark" in out["dark"]
    assert "on-dark" not in out["light"]


def test_the_soft_shadow_is_the_handoffs():
    css = (ROOT / "static" / "theme.css").read_text(encoding="utf-8")
    rule = re.search(r"^\.dish-plate \{[^}]*\}", css, re.M).group(0)
    assert "filter: var(--plate-shadow);" in rule
    assert "--plate-shadow:   drop-shadow(0 2px 5px rgba(20, 38, 74, .18));" in css
    assert ".dish-plate.on-dark { filter: none; }" in css


def test_a_drawing_that_fails_falls_back_to_neutral_then_to_nothing():
    out = _js("""
var img = { classList: { contains: function (c) { return c === 'dish-plate'; } }, dataset: {}, style: {}, src: 'x' };
var handler = listeners.filter(function (l) { return l[0] === 'error' && l[2] === true; })[0][1];
handler({ target: img });
var first = img.src;
handler({ target: img });
console.log(JSON.stringify({ first: first, hidden: img.style.visibility }));
""")
    assert out["first"].endswith("/static/plates/neutral.svg")
    assert out["hidden"] == "hidden"


def test_the_shell_loads_the_helper_before_shell_js():
    html = (ROOT / "static" / "shell.html").read_text(encoding="utf-8")
    assert 0 < html.index("/static/api.js") < html.index("/static/plates.js") < html.index("/static/shell.js")


def test_the_six_reviewed_samples_have_their_own_specs():
    """The six reviewed samples were left out of data/recipe_library_batches,
    so the first plate pass missed them. They live in part-09."""
    specs = json.loads((ROOT / "plates" / "specs" / "part-09.json").read_text())
    assert set(specs) == {"Meatloaf", "Chicken Piccata", "Chana Masala", "Chicken Tinga", "Thai Basil Chicken", "Misir Wat"}


def test_goat_cheese_is_cheese_not_goat_meat():
    # 2026-11-09 snacks round: "Goat cheese" read as goat, so Patzarosalata
    # (beets, yogurt, goat cheese) lost its own plate to Lamb Kleftiko.
    assert "lamb" not in drawings._foods_on_list(drawings._items(["Goat cheese", "Goat milk yogurt"]))
    assert "lamb" in drawings._foods_on_list(drawings._items(["Goat shoulder"]))
