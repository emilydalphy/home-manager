"""
A recipe page's fractions are read however the page writes them, and a
method written as one string of HTML comes in as its steps (overnight
defect hunt, 2026-10-30). Fed through the parser from a saved page — the
sandbox can't reach a recipe site.
"""
from __future__ import annotations

import json

from app import recipe_import as ri


SAVED_PAGE = """<!doctype html><html><head><title>Rice Pilaf</title>
<script type="application/ld+json">%s</script></head><body></body></html>""" % json.dumps({
    "@context": "https://schema.org",
    "@type": "Recipe",
    "name": "Rice Pilaf",
    "recipeYield": ["4", "4 servings"],
    "prepTime": "PT10M",
    "totalTime": "P0DT0H45M",
    "recipeIngredient": [
        "1-1/2 cups long-grain rice",
        "1&frac12; cups chicken stock",
        "1&#189; cups water",
        "1⁄2 cup slivered almonds",
        "1/2-1 tsp salt",
    ],
    "recipeInstructions": "<ol><li>Toast the rice.</li><li>Add the stock &amp; water.</li><li>Simmer 20 min.</li></ol>",
})


def _draft():
    reader = ri.read_page(SAVED_PAGE)
    return ri.draft_from_json_ld(ri.find_recipe_json_ld(reader.json_ld), "https://example.com/pilaf")


def test_every_way_a_page_writes_one_and_a_half_reads_as_one_and_a_half():
    lines = {i["item"]: i["qty"] for i in _draft()["ingredients"]}
    # Each of these used to save wrong: "1/2 cups" rice (a range's top
    # end), and a bare "1" of "½ cups chicken stock" / "⁄2 cup slivered
    # almonds".
    assert lines["long-grain rice"] == "1 1/2 cups"
    assert lines["chicken stock"] == "1 1/2 cups"
    assert lines["water"] == "1 1/2 cups"
    assert lines["slivered almonds"] == "1/2 cup"
    # A real range still buys its top end.
    assert lines["salt"] == "1 tsp"


def test_a_method_written_as_one_html_string_comes_in_as_its_steps():
    assert _draft()["instructions"] == ["Toast the rice.", "Add the stock & water.", "Simmer 20 min."]


def test_the_rest_of_the_saved_page_reads_as_before():
    draft = _draft()
    assert draft["name"] == "Rice Pilaf"
    assert draft["default_servings"] == 4
    assert draft["prep_time_minutes"] == 10
    assert draft["cook_time_minutes"] == 35


def test_a_model_read_amount_and_a_written_time_take_the_same_reading():
    assert ri.normalise_amount("1-1/2 cups") == "1 1/2 cups"
    assert ri.normalise_amount("1-½ cups") == "1 1/2 cups"
    assert ri.normalise_amount("2-3 cloves") == "3 cloves"  # a range, unchanged
    assert ri.parse_duration_minutes("1-1/2 hours") == 90


def test_steps_split_on_paragraphs_and_breaks_but_not_on_a_less_than():
    assert ri.parse_instructions("<p>Preheat.</p><p>Mix.</p>") == ["Preheat.", "Mix."]
    assert ri.parse_instructions("Preheat.<br>Mix.<br/>Bake.") == ["Preheat.", "Mix.", "Bake."]
    assert ri.parse_instructions("Cook &lt; 5 min, then &gt; 2 min.") == ["Cook < 5 min, then > 2 min."]
