"""
The recipe page (Emily, 2026-10-05; Z-Recipe and Z-Steps, 2026-11-05): plate,
name, two tabs, no ticking off ingredients, Start cooking in the dock. The rest
is under the ···: who it's for, the time split, Tweak, Change recipe, Save, Add
to shopping list, Rate, linked Sources with a Lead badge, and "Changed for your
household". Sources and changes come from GET /api/recipes/{id} (`research`,
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


def _meal(meal=None):
    return dict(meal or _MEAL, recipe_id=7, attendance={"present_names": ["Gowthami", "Ravi", "Arjun"], "guest_count": 0})


def _seed(extra):
    return "recipeExtras[7] = %s;\n" % (__import__("json").dumps(extra) if extra is not None else "null")


def _page(tab="ingredients", extra=None, meal=None, **state):
    st = {"data": dict(_VIEW, meals=[_meal(meal)]), "recipeTab": tab}
    st.update(state)
    return _run(_seed(extra) + "console.log(JSON.stringify(cookFocusHtml(cookState.data, cookState.data.meals, 0)));", st)


def _more(extra=None, meal=None, **state):
    """The ···'s sheet."""
    st = {"data": dict(_VIEW, meals=[_meal(meal)])}
    st.update(state)
    return _run(_seed(extra) + "console.log(JSON.stringify(recipeMoreSheetHtml(cookState.data.meals[0])));", st)


def _sources(extra=None, meal=None):
    """The Sources sheet's body."""
    st = {"data": dict(_VIEW, meals=[_meal(meal)])}
    return _run(_seed(extra) + "var m = cookState.data.meals[0], x = recipeExtraFor(m);\n"
                "console.log(JSON.stringify(recipeCreditHtml(x) + recipeSourcesHtml(m, x)));", st)


@_needs_node
def test_plate_name_and_two_tabs_ingredients_first():
    html = _page()
    tabs = [t for t in ("Overview", "Ingredients", "Steps", "Sources") if '">' + t + "</button>" in html]
    assert tabs == ["Ingredients", "Steps"]
    assert 'class="seg-btn is-on" aria-pressed="true" data-cook="recipe-tab" data-tab="ingredients"' in html
    # No source line and no "who it's for" under the name.
    assert "recipe-credit-line" not in _page(extra={"research": _RESEARCH, "household_changes": _CHANGES})
    assert "recipe-for" not in html and "Gowthami" not in html
    # Recipe has no tab bar: the dock is the foot of the screen.
    assert "body.is-full-screen #tab-bar { display: none; }" in _cj.SHELL_CSS


@_needs_node
def test_the_steps_tab_says_how_long_it_takes():
    html = _page("steps")
    assert '<p class="recipe-takes">Takes about 50 min</p>' in html
    assert "recipe-takes" not in _page("ingredients")


@_needs_node
def test_the_more_sheet_says_who_it_is_for_and_the_time_split():
    html = _more()
    assert "Gowthami, Ravi and Arjun · serves 4" in html
    assert "50 min (15 prep, 35 cook)" in html


@_needs_node
def test_the_changes_are_one_row_under_the_more_and_the_list_a_sheet_of_its_own():
    html = _more(extra={"research": _RESEARCH, "household_changes": _CHANGES})
    assert "Changed for your household" in html and "2 changes" in html
    assert 'data-cook="recipe-changes"' in html
    assert "sunflower seeds" not in html
    listed = _run(_seed({"research": None, "household_changes": _CHANGES}) +
                  "console.log(JSON.stringify(recipeChangesHtml(recipeExtras[7])));", {})
    assert "sunflower seeds instead" in listed


@_needs_node
def test_originally_based_on_is_the_servers_words():
    research = dict(_RESEARCH, credit="Originally based on Swasthi's Recipes", credit_prefix="Originally based on")
    assert "Originally based on Swasthi" in _sources(extra={"research": research, "household_changes": []})


@_needs_node
def test_no_research_means_no_credit_no_changes_row_and_a_plain_sources_line():
    html = _more(extra=None)
    assert "Changed for your household" not in html
    src = _sources(extra={"research": None, "household_changes": []})
    assert "recipe-credit-line" not in src
    assert "No outside recipe behind this one." in src


@_needs_node
def test_ingredients_are_tiles_with_no_ticking():
    html = _page("ingredients")
    assert '<span class="tile-name">Chicken thighs</span><span class="tile-sub">4</span>' in html
    assert 'data-cook="check-ing"' not in html and "cook-box" not in html


@_needs_node
def test_sources_lead_first_with_badge_stars_and_links_to_their_site():
    html = _sources(extra={"research": _RESEARCH, "household_changes": []})
    a, b = html.index('recipe-src-name">Swasthi'), html.index('recipe-src-name">Hebbar')
    assert a < b
    assert '<span class="recipe-src-lead">Lead</span>' in html
    assert html.count("recipe-src-lead") == 1
    assert "★ 4.9 · 1,200 ratings" in html
    assert 'href="https://example.com/a" target="_blank" rel="noopener"' in html
    assert "Opens the original recipe on their site. Pomona’s version is adapted for your household." in html
    assert "agree" not in html  # no "what they agree on" list in the app


@_needs_node
def test_the_dock_is_start_cooking_with_one_apricot():
    html = _page()
    dock = html[html.index('class="dock'):]
    assert "Start cooking" in dock and "Change recipe" not in dock
    assert dock.count("cook-hero-action") == 1, "one apricot (rule 5)"


@_needs_node
def test_the_more_sheet_keeps_every_action():
    html = _more()
    for words in ("Tweak", "Change recipe", "Save to my recipes", "Add to shopping list", "Rate this recipe",
                  "Sources", "Ask Pomona"):
        assert ">" + words + "<" in html, words
    assert 'data-recipe-tweak="1"' in html and 'data-cr="open"' in html
    assert ">Liked it<" in _more(recipeRateOpen=True) and ">Not a hit<" in _more(recipeRateOpen=True)


@_needs_node
def test_a_reheat_or_no_recipe_page_has_no_more_button():
    assert "recipe-more-btn" in _page()
    assert "recipe-more-btn" not in _page(meal=dict(_MEAL, has_full_recipe=False))


def test_the_cooker_view_carries_the_recipe_id_and_who_is_eating():
    import inspect

    from app.tools import cooker

    src = inspect.getsource(cooker.get_cooker_view)
    assert '"recipe_id": recipe.get("id") if recipe else None' in src
    assert '"present_names": slot_att.get("present_names") or []' in src


# ---------- review fixes (2026-10-06) ----------


@_needs_node
def test_a_credit_with_every_link_gone_still_names_the_cooks_never_no_outside_recipe():
    research = dict(_RESEARCH, sources=[], lead=None, others=[])
    html = _sources(extra={"research": research, "household_changes": []})
    assert "Based on Swasthi" in html
    assert "No outside recipe" not in html and "I wrote this one" not in html


@_needs_node
def test_a_lead_with_a_broken_link_is_shown_unlinked_with_its_badge():
    lead = dict(_RESEARCH["sources"][0], url="")
    research = dict(_RESEARCH, sources=[lead, _RESEARCH["sources"][1]])
    html = _sources(extra={"research": research, "household_changes": []})
    first = html[html.index('<li class="recipe-src">'):html.index("</li>")]
    assert "Swasthi" in first and "recipe-src-lead" in first and "href=" not in first
    assert 'href="https://example.com/b"' in html


def test_the_cache_is_emptied_on_refresh_and_never_keeps_a_failure():
    js = _cj.SHELL_JS
    refresh = js[js.index("function refreshKitchenPanel() {"):]
    refresh = refresh[: refresh.index("\n  }\n")]
    assert "recipeExtras = {};" in refresh
    fetcher = js[js.index("function recipeExtraFor(meal) {"):]
    fetcher = fetcher[: fetcher.index("\n  }\n")]
    assert "recipeExtras[id] = null" not in fetcher
    assert fetcher.count("delete recipeExtras[id]") == 2
    # The ···'s sheet is a quiet sheet: Escape and the scrim come with it.
    assert "'quiet-scrim'" in js and "function recipeCloseMenu" not in js
