"""Emily, 2026-10-20: a version needs at least a 4 to lead."""
from app.tools.recipe_research import pick_lead


def _s(name, rating, count, url=None):
    return {"name": name, "url": url or f"https://{name}.com/r", "rating": rating,
            "rating_count": count, "read": True}


def test_a_low_rating_never_leads_even_with_many_ratings():
    out = pick_lead([_s("low", 3.3, 900), _s("good", 4.6, 25)])
    assert out[0]["name"] == "good"


def test_with_only_a_low_rated_version_well_rated_it_falls_back_to_an_unrated_one():
    out = pick_lead([_s("low", 3.7, 24), _s("cook", None, None)])
    assert out[0]["name"] == "cook"


def test_trusted_cook_rated_low_is_skipped_for_one_that_is_not():
    out = pick_lead([_s("low", 3.5, 10, "https://recipetineats.com/a"), _s("other", None, None)],
                    trusted=[("RecipeTin Eats", "recipetineats.com")])
    assert out[0]["name"] == "other"


def test_a_four_exactly_can_lead():
    out = pick_lead([_s("four", 4.0, 30), _s("none", None, None)])
    assert out[0]["name"] == "four"


def test_if_the_low_rated_page_is_the_only_one_read_it_still_leads():
    out = pick_lead([_s("low", 3.3, 40)])
    assert out[0]["name"] == "low"
