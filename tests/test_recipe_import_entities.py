"""
HTML entities in a page's recipe markup are read as the characters they
stand for (overnight defect hunt, 2026-10-11). The fake network is
test_recipe_import's.
"""
from __future__ import annotations

import json

from app import recipe_import as ri, tools

from test_recipe_import import _html_response, _page, _wire


# ---------- HTML entities in the markup (overnight defect hunt, 2026-10-11) ----------
# WordPress and plenty of other sites write the JSON-LD block with the page's
# own HTML escaping still on it: "Mac &amp; Cheese", "Nana&#39;s",
# "Kerrygold&#8217;s butter". A <script> body is raw text to an HTML parser,
# so nothing decoded them, and they were saved verbatim — onto the recipe's
# name, its steps and the grocery list.

ESCAPED_RECIPE = json.dumps({
    "@context": "https://schema.org",
    "@type": "Recipe",
    "name": "Nana&#39;s Mac &amp; Cheese",
    "recipeYield": "4",
    "recipeIngredient": [
        "2 cups elbow macaroni",
        "1 cup Kerrygold&#8217;s butter",
        "Salt &amp; pepper, to taste",
        "1&nbsp;cup milk",
    ],
    "recipeInstructions": [
        {"@type": "HowToStep", "text": "Boil the pasta &amp; drain."},
        {"@type": "HowToStep", "text": "Stir in &quot;lots&quot; of cheese."},
    ],
})


def test_html_entities_in_the_markup_are_read_as_the_characters_they_stand_for(signed_in, monkeypatch):
    _wire(monkeypatch, {"/mac": _html_response(_page(ESCAPED_RECIPE))})
    draft = signed_in.post("/api/recipes/import-url", json={"url": "https://recipes.example.com/mac"}).json()["draft"]
    assert draft["name"] == "Nana's Mac & Cheese"
    lines = {i["item"]: i["qty"] for i in draft["ingredients"]}
    assert lines["Kerrygold’s butter"] == "1 cup"
    assert lines["Salt & pepper"] == "to taste"
    # A non-breaking space between the amount and the unit is still a space.
    assert lines["milk"] == "1 cup"
    assert draft["instructions"] == ["Boil the pasta & drain.", 'Stir in "lots" of cheese.']

    # And the saved recipe — what the grocery list and the cook view read —
    # carries the characters, not the escapes.
    assert signed_in.post("/api/recipes/add", json=draft).status_code == 200
    saved = tools.get_recipe("Nana's Mac & Cheese")
    assert "&" not in json.dumps([i["item"] for i in saved["ingredients"]]).replace("Salt & pepper", "")
    assert all("&amp;" not in s and "&quot;" not in s for s in saved["instructions"])


def test_a_plain_ampersand_and_a_markup_tag_still_read_as_before():
    # Unescaping must not eat a bare "&" a page wrote unescaped, and a tag
    # written escaped inside a step is stripped like any other tag.
    assert ri.split_ingredient_line("1 cup salt & vinegar crisps") == {"item": "salt & vinegar crisps", "qty": "1 cup"}
    assert ri.parse_instructions(["&lt;p&gt;Whisk the eggs.&lt;/p&gt;"]) == ["Whisk the eggs."]


def test_an_escaped_less_than_and_greater_than_keep_the_words_between_them():
    # Decoded, "&lt; 5 min ... &gt;" is a "<" and a ">" — not a tag, and the
    # words between them are the step.
    assert ri._clean_text("cook &lt; 5 min, then &gt; 2 min") == "cook < 5 min, then > 2 min"
    assert ri.parse_instructions(["Simmer for &lt; 5 min; serve when it's &gt; warm."]) == [
        "Simmer for < 5 min; serve when it's > warm."
    ]
    # A real tag, escaped or not, still goes.
    assert ri.parse_instructions(["<b>Whisk</b> the eggs.", "&lt;/p&gt;Fold."]) == ["Whisk the eggs.", "Fold."]


def test_only_an_entity_with_its_semicolon_is_decoded():
    assert ri._clean_text("Mac &amp; cheese &#39;n&#x27; &notes &copy") == "Mac & cheese 'n' &notes &copy"
