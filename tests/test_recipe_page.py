"""
The recipe page (Loop Board "Recipes people trust", slice 1 — Emily locked
it 2026-10-05): the dish, "who it's for · serves N", tabs Overview ·
Ingredients · Steps · Sources, the credit line with See sources, "Changed
for your household" folded to one row, no ticking off ingredients, linked
Sources with a Lead badge, Start cooking + Change recipe in the dock, and a
⋯ menu. Sources and changes come from GET /api/recipes/{id} (`research`,
`household_changes`, the server half's shape) and degrade when absent.

Runs the page's own renderers under node through tests/test_cook_journey.py's
harness (same functions list, same stubs).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("test_cook_journey", _HERE / "test_cook_journey.py")
_cj = importlib.util.module_from_spec(_spec)
sys.modules["test_cook_journey"] = _cj
_spec.loader.exec_module(_cj)
_run, _needs_node, _MEAL, _VIEW = _cj._run, _cj._needs_node, _cj._MEAL, _cj._VIEW

_RESEARCH = {
    "credit_prefix": "Based on",
    "credit": "Based on Swasthi's Recipes, checked against Hebbar's Kitchen and Cook with Manali",
    "lead": {"name": "Swasthi's Recipes", "url": "https://example.com/a", "title": "Chana masala",
             "rating": 4.9, "rating_count": 1200, "lead": True},
    "others": [],
    "sources": [
        {"name": "Swasthi's Recipes", "url": "https://example.com/a", "title": "Chana masala",
         "rating": 4.9, "rating_count": 1200, "lead": True},
        {"name": "Hebbar's Kitchen", "url": "https://example.com/b", "title": "Chole",
         "rating": None, "rating_count": None, "lead": False},
    ],
}
_CHANGES = ["No cashews (Asha can't have tree nuts) — sunflower seeds instead", "Chilli on the side"]


def _page(tab="overview", extra=None, meal=None, **state):
    m = dict(meal or _MEAL, recipe_id=7, attendance={"present_names": ["Gowthami", "Ravi", "Arjun"], "guest_count": 0})
    st = {"data": dict(_VIEW, meals=[m]), "recipeTab": tab}
    st.update(state)
    seed = "recipeExtras[7] = %s;\n" % (__import__("json").dumps(extra) if extra is not None else "null")
    return _run(seed + "console.log(JSON.stringify(cookFocusHtml(cookState.data, cookState.data.meals, 0)));", st)


@_needs_node
def test_band_line_and_the_four_tabs_overview_first():
    html = _page()
    assert '<p class="recipe-for">Gowthami, Ravi and Arjun · serves 4</p>' in html
    tabs = [t for t in ("Overview", "Ingredients", "Steps", "Sources") if '">' + t + "</button>" in html]
    assert tabs == ["Overview", "Ingredients", "Steps", "Sources"]
    assert 'aria-selected="true" data-cook="recipe-tab" data-tab="overview"' in html


@_needs_node
def test_overview_credit_changes_folded_stepper_time_and_links():
    html = _page(extra={"research": _RESEARCH, "household_changes": _CHANGES})
    assert "Based on Swasthi&#39;s Recipes" in html or "Based on Swasthi's Recipes" in html
    assert 'data-tab="sources">See sources</button>' in html
    # Folded to one row by default: the count, not the list.
    assert "Changed for your household" in html and "2 changes" in html
    assert "sunflower seeds" not in html
    assert "Cooking for" in html
    assert "50 min (15 prep, 35 cook)" in html
    assert 'data-tab="ingredients"' in html and 'data-tab="steps"' in html
    opened = _page(extra={"research": _RESEARCH, "household_changes": _CHANGES}, recipeChangesOpen=True)
    assert "sunflower seeds instead" in opened


@_needs_node
def test_originally_based_on_is_the_servers_words():
    research = dict(_RESEARCH, credit="Originally based on Swasthi's Recipes", credit_prefix="Originally based on")
    assert "Originally based on Swasthi" in _page(extra={"research": research, "household_changes": []})


@_needs_node
def test_no_research_means_no_credit_no_changes_row_and_a_plain_sources_line():
    html = _page(extra=None)
    assert "recipe-credit" not in html and "Changed for your household" not in html
    src = _page("sources", extra={"research": None, "household_changes": []})
    assert "there’s no outside recipe behind it" in src


@_needs_node
def test_ingredients_are_a_plain_list_with_no_ticking():
    html = _page("ingredients")
    assert "4 Chicken thighs" in html
    assert 'data-cook="check-ing"' not in html and "cook-box" not in html


@_needs_node
def test_sources_lead_first_with_badge_stars_and_links_to_their_site():
    html = _page("sources", extra={"research": _RESEARCH, "household_changes": []})
    a, b = html.index("Swasthi"), html.index("Hebbar")
    assert a < b
    assert '<span class="recipe-src-lead">Lead</span>' in html
    assert html.count("recipe-src-lead") == 1
    assert "★ 4.9 · 1,200 ratings" in html
    assert 'href="https://example.com/a" target="_blank" rel="noopener"' in html
    assert "Opens the original recipe on their site. Pomona’s version is adapted for your household." in html
    assert "agree" not in html  # no "what they agree on" list in the app


@_needs_node
def test_dock_is_start_cooking_and_change_recipe_with_one_apricot():
    html = _page()
    dock = html[html.index('class="dock'):]
    assert "Start cooking" in dock and "Change recipe" in dock
    assert dock.count("cook-hero-action") == 1, "one apricot (rule 5)"


@_needs_node
def test_the_more_menu_offers_the_three_actions():
    html = _page(recipeMenuOpen=True)
    for words in ("Save to my recipes", "Add to shopping list", "Rate this recipe"):
        assert words in html


def test_the_cooker_view_carries_the_recipe_id_and_who_is_eating():
    import inspect

    from app.tools import cooker

    src = inspect.getsource(cooker.get_cooker_view)
    assert '"recipe_id": recipe.get("id") if recipe else None' in src
    assert '"present_names": slot_att.get("present_names") or []' in src
