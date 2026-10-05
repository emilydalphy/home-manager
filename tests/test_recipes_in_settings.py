"""
Settings → Recipes (Emily, 2026-10-04): "can you add back the function to
add in your own recipe from a link and the recipe view and add it under the
settings for now".

Pinned here:
- GET /api/recipes — the household's recipes A to Z, light fields, a count;
  empty for a household with none; a dish still waiting to be written up
  (details_pending) is left out because there is nothing to open.
- GET /api/recipes/{id} — the whole recipe, its credit included for one
  brought in from a link; 404 for an id that isn't this household's.
- The route order: /api/recipes/scale is still the scaler, not an id.
- The Preferences row's line and the list rows' quiet line, run under node
  from shell.js itself.
"""
from __future__ import annotations

import json
from pathlib import Path

from app import households, tools

import nodeharness

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def _sign_in(client, password):
    res = client.post("/login", data={"password": password, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return client


def _add_linked(client, name="Weeknight Chili"):
    res = client.post("/api/recipes/add", json={
        "name": name,
        "ingredients": [{"item": "ground beef", "qty": "1 lb"}, {"item": "kidney beans", "qty": "1 can"}],
        "instructions": ["Brown the beef.", "Add the beans and simmer."],
        "default_servings": 6,
        "prep_time_minutes": 15,
        "cook_time_minutes": 30,
        "source_url": "https://www.seriouseats.com/best-chili",
    })
    assert res.status_code == 200, res.text
    return res.json()


# ---------- the list ----------

def test_a_household_with_no_recipes_gets_an_empty_list(signed_in):
    res = signed_in.get("/api/recipes")
    assert res.status_code == 200
    assert res.json() == {"recipes": [], "count": 0}


def test_the_list_is_a_to_z_with_light_fields(signed_in):
    _add_linked(signed_in, "weeknight chili")
    tools.add_recipe("Apple Crumble", [{"item": "apples", "qty": "4"}], instructions=["Bake."])
    tools.add_recipe("Banana Bread", [{"item": "bananas", "qty": "3"}], instructions=["Bake."])
    body = signed_in.get("/api/recipes").json()
    assert body["count"] == 3
    assert [r["name"] for r in body["recipes"]] == ["Apple Crumble", "Banana Bread", "weeknight chili"]
    chili = body["recipes"][2]
    assert set(chili) == {"id", "name", "prep_time_minutes", "cook_time_minutes", "citation", "has_photo"}
    assert chili["citation"]["text"] == "From seriouseats.com"
    assert chili["prep_time_minutes"] == 15 and chili["cook_time_minutes"] == 30
    assert body["recipes"][0]["citation"] is None


def test_a_dish_not_written_up_yet_is_left_off_the_list(signed_in):
    tools.add_recipe("Mystery Stew", [], details_pending=True)
    tools.add_recipe("Toast", [{"item": "bread", "qty": "2 slices"}], instructions=["Toast it."])
    names = [r["name"] for r in signed_in.get("/api/recipes").json()["recipes"]]
    assert names == ["Toast"]


# ---------- the detail ----------

def test_a_recipe_from_a_link_opens_in_full_with_its_source(signed_in):
    saved = _add_linked(signed_in)
    res = signed_in.get(f"/api/recipes/{saved['recipe_id']}")
    assert res.status_code == 200, res.text
    r = res.json()
    assert r["id"] == saved["recipe_id"]
    assert r["name"] == "Weeknight Chili"
    assert [i["item"] for i in r["ingredients"]] == ["ground beef", "kidney beans"]
    assert r["instructions"] == ["Brown the beef.", "Add the beans and simmer."]
    assert r["default_servings"] == 6
    assert r["source_url"] == "https://www.seriouseats.com/best-chili"
    assert r["citation"] == {"kind": "link", "url": "https://www.seriouseats.com/best-chili",
                             "host": "seriouseats.com", "text": "From seriouseats.com"}
    assert r["photo_urls"] == []


def test_an_unknown_id_is_a_404(signed_in):
    assert signed_in.get("/api/recipes/999999").status_code == 404


def test_another_household_cannot_read_the_recipe_or_see_it_listed(client):
    other = households.create_household("The Beta Testers", "beta-passphrase-recipes")
    _sign_in(client, "test-password")
    saved = _add_linked(client)
    assert client.get(f"/api/recipes/{saved['recipe_id']}").status_code == 200

    client.cookies.clear()
    _sign_in(client, "beta-passphrase-recipes")
    assert client.get(f"/api/recipes/{saved['recipe_id']}").status_code == 404
    assert client.get("/api/recipes").json() == {"recipes": [], "count": 0}
    with tools.use_household(other):
        assert tools.get_recipe_by_id(saved["recipe_id"]) is None

    # And no sign-in at all is turned away.
    client.cookies.clear()
    assert client.get("/api/recipes").status_code in (401, 403)
    assert client.get(f"/api/recipes/{saved['recipe_id']}").status_code in (401, 403)


def test_the_scaler_route_is_still_the_scaler(signed_in):
    _add_linked(signed_in)
    res = signed_in.get("/api/recipes/scale", params={"name": "Weeknight Chili", "servings": 3})
    assert res.status_code == 200, res.text
    assert "citation" not in res.json(), "the scaler answered, not the recipe view"
    # A word where an id goes is never read as a recipe.
    assert signed_in.get("/api/recipes/chili").status_code in (404, 405)


# ---------- the shell's lines, run for real ----------

def _function(name: str) -> str:
    start = SHELL_JS.index(f"function {name}(")
    i = SHELL_JS.index("{", start)
    depth, j = 0, i
    while True:
        if SHELL_JS[j] == "{":
            depth += 1
        elif SHELL_JS[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return SHELL_JS[start : j + 1]


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip())


def test_the_preferences_row_says_how_many_are_saved():
    script = (
        "var recipesState = { list: null, failed: false };\n"
        + _function("prefsRecipesLine") + "\n"
        + "console.log(JSON.stringify([\n"
        + "  prefsRecipesLine({ list: null, failed: false }),\n"
        + "  prefsRecipesLine({ list: null, failed: true }),\n"
        + "  prefsRecipesLine({ list: [], failed: false }),\n"
        + "  prefsRecipesLine({ list: [{}], failed: false }),\n"
        + "  prefsRecipesLine({ list: new Array(12).fill({}), failed: false }),\n"
        + "]));\n"
    )
    assert _node(script) == [
        "Reading it back…", "Couldn’t check just now", "None saved yet", "1 saved", "12 saved",
    ]


def test_a_list_row_says_the_time_and_where_it_came_from_and_nothing_made_up():
    script = (
        _function("recipeMinutes") + "\n" + _function("recipeListLine") + "\n"
        + "console.log(JSON.stringify([\n"
        + "  recipeListLine({ prep_time_minutes: 15, cook_time_minutes: 30, citation: { text: 'From seriouseats.com' } }),\n"
        + "  recipeListLine({ prep_time_minutes: null, cook_time_minutes: 20, citation: null }),\n"
        + "  recipeListLine({ citation: { kind: 'book', text: 'From Salt Fat Acid Heat' } }),\n"
        + "  recipeListLine({}),\n"
        + "]));\n"
    )
    assert _node(script) == ["45 min · From seriouseats.com", "20 min", "From Salt Fat Acid Heat", ""]


def test_the_row_sits_under_how_you_eat_and_the_imports_have_no_in_development_pill():
    render = _function("renderPrefsRows")
    assert "(row.section === 'taste' ? recipesPrefsRowHtml() : '')" in render
    sheet = _function("recipesListHtml")
    assert "Add from a link" in sheet and "Add from a cookbook" in sheet
    assert "In development" not in sheet
    # The list reads the same API, and opening from here comes back here.
    # NOTE 2026-10-05 (every x closes one level): both calls gained
    # `parent: 'recipes'`, which is the other half of "comes back here" — the
    # import sheet's own x and back chevron now land on this list rather than
    # on the tab. Nothing this test asserts moved.
    assert "openRecipeLinkSheet({ parent: 'recipes', onDone: recipesAfterImport })" in _function("onRecipesClick")
    assert "openRecipePhotoSheet({ parent: 'recipes', onDone: recipesAfterImport })" in _function("onRecipesClick")
