"""
Rating a recipe takes one of the two verdicts it understands, and nothing else.

The SIXTH instance of one class, fixed the way the other five were:
cooker.InvalidMealStatus (2026-09-16), chores.InvalidChoreStatus,
grocery.InvalidGroceryStatus, attention.InvalidAttentionStatus (2026-09-25)
and a meal slot (the same night). A marker exception that IS a ValueError
subclass, checked above get_conn, and a route whose except for it sits BEFORE
the plain ValueError that means "no recipe by that name" — ordering is
load-bearing, not tidiness, and there is a test on it below asking for both
codes in one breath.

IT IS THE WORST OF THE SIX, IN TWO INDEPENDENT WAYS, and both are measured
rather than argued. Driven over HTTP against a real uvicorn on a throwaway
database, 2026-09-26, before anything was touched:

  1. THE VERDICT IS DESTROYED, not merely mis-set. The five siblings wrote a
     word no screen reads and lost nothing. Here, a recipe rated `liked` and
     re-rated `teleported` came back carrying `teleported` at HTTP 200: the
     UPDATE commits, so the answer the household actually gave is gone. The
     count memory.py runs for the "what we know" score went 1 -> 0,
     list_recipes sorts it as unrated, and — since the planner's filter is
     `rating != 'disliked'` — a dish the household had explicitly rejected
     was a planning candidate again. `Liked` with a capital did all of that
     too; it is a different word to every one of those readers.

  2. ON A SOLO NIGHT IT CRASHED AND HID THE CRASH. When the recipe's last
     cook had exactly one member home, mark_recipe_feedback goes on to write
     the same word per-person, and member_recipe_feedback.rating carries
     CHECK(rating IN ('liked','disliked')) — so the INSERT raised. Measured,
     in order: the route answered 500 "your data is fine" over a rating it
     had ALREADY destroyed (the household row read `teleported` while the
     per-person row still read `liked`, so the two disagreed about one
     recipe); the connection was left open holding SQLite's write lock,
     because _maybe_auto_attribute_solo_night had no try/finally; and
     tools.record_error therefore could not write. error_events held NOTHING
     for that failure. The household's very next write then waited out
     sqlite3's full 5-second busy timeout (5.03s) and 500'd — and THAT is the
     row error_events eventually got: `/api/grocery-list/add`. The morning
     report would have shown a grocery error and no way to learn what broke.

After: 422 both times, the rating intact at `liked`, the per-person row still
agreeing with it, the write lock free, error_events empty, and the next write
200 in 0.015s.

WHY BOTH HALVES ARE FIXED, and why a vocabulary guard alone would not have
been enough: the leak belongs to that INSERT, not to the wire. ANY future
failure of it does the same thing. So the try/finally is what makes the NEXT
unexpected failure in there visible instead of silent, and it is pinned by a
test that WIDENS RECIPE_RATINGS to force the same crash past the new guard —
see tests/test_cook_and_feedback_routes.py, where the two crash
characterisations this card inverted now live.

WHAT IS RED ON MAIN, measured rather than claimed, and the number is worth
much less than it looks — see the report.
"""
from __future__ import annotations

import pytest

from app import tools
from app.db import get_conn
from app.tools import recipes as _recipes

# `signed_in` and `client` come from tests/conftest.py, like every other route
# test in this suite.


def _chili(rating=""):
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Black beans", "qty": "1 can"}])
    if rating:
        tools.mark_recipe_feedback("Bean Chili", rating=rating)
    return "Bean Chili"


def _row(name="Bean Chili"):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT rating, feedback_notes FROM recipes WHERE household_id = 1 AND name = ?",
            (name,),
        ).fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------- the guard


def test_a_word_that_is_not_a_verdict_is_refused_and_nothing_is_written():
    """
    CATCH. Red on main for the reason it is named for: there
    mark_recipe_feedback returns {"rating": "teleported"} and the row on disk
    reads teleported.
    """
    _chili()

    with pytest.raises(tools.InvalidRecipeRating):
        tools.mark_recipe_feedback("Bean Chili", rating="teleported")

    assert _row()["rating"] == ""


def test_the_refusal_does_not_destroy_the_verdict_that_was_there():
    """
    CATCH, and the half that made this worse than its five siblings. Red on
    main, where the row comes back reading 'teleported' and 'liked' is gone.
    """
    _chili(rating="liked")

    with pytest.raises(tools.InvalidRecipeRating):
        tools.mark_recipe_feedback("Bean Chili", rating="teleported")

    assert _row()["rating"] == "liked"


def test_a_capital_letter_is_a_different_word_and_is_refused():
    """
    CATCH. Not pedantry: every reader of recipes.rating compares the string
    exactly ('liked' in the completeness count, = 'liked' in list_recipes'
    sort, != 'disliked' in the planner's candidate query), so 'Liked' is
    invisible to all three. Refused rather than quietly lowercased, because
    folding it would be this door guessing what a caller meant.
    """
    _chili(rating="liked")

    with pytest.raises(tools.InvalidRecipeRating):
        tools.mark_recipe_feedback("Bean Chili", rating="Liked")

    assert _row()["rating"] == "liked"


def test_the_empty_string_is_refused_even_though_the_column_documents_it():
    """
    CATCH, and the one product decision on this card, so it is pinned rather
    than left to a comment. schema.sql documents recipes.rating as
    `'' | 'liked' | 'disliked'`, and '' really is a state — "no verdict yet",
    written by the column's own DEFAULT. It is still refused HERE, because
    nothing in the app sends it and member_recipe_feedback.rating cannot hold
    it at all: on a solo night the household half of the write would land and
    the per-person half would raise, which is the exact shape of the bug this
    card is about. "Clear a verdict we already gave" is better refused than
    half-supported. Emily reverses it by adding "" to RECIPE_RATINGS — and
    then owes the per-person path an answer.
    """
    _chili(rating="liked")

    with pytest.raises(tools.InvalidRecipeRating):
        tools.mark_recipe_feedback("Bean Chili", rating="")

    assert _row()["rating"] == "liked"


def test_the_check_runs_before_a_connection_is_opened():
    """
    GUARD — red on main only on the missing name, so the mutation is what
    pins it: move the check below get_conn and this goes red.

    It matters for the two reasons the grocery and attention siblings already
    record — a word that is not a verdict never takes the write lock, and a
    raise below get_conn would skip conn.close() and leak the connection —
    plus one this door has of its own: the UPDATE commits, so a check that
    ran after it would be checking a rating it had already destroyed.
    """
    _chili()
    opened = []
    real = _recipes.get_conn

    def counting():
        opened.append(1)
        return real()

    _recipes.get_conn = counting
    try:
        with pytest.raises(tools.InvalidRecipeRating):
            tools.mark_recipe_feedback("Bean Chili", rating="teleported")
    finally:
        _recipes.get_conn = real
    assert opened == [], "a word that is not a verdict must not open a connection"


def test_the_marker_is_a_valueerror_so_existing_callers_still_catch_it():
    """
    GUARD. The shape every sibling uses, and the reason the route's excepts
    have to be ordered: anything already catching ValueError around this
    still catches the refusal.
    """
    assert issubclass(tools.InvalidRecipeRating, ValueError)


def test_the_marker_and_the_vocabulary_are_on_the_package():
    """
    GUARD, and not decoration: app/tools/__init__.py is the package's public
    face, and main.py reaches the marker as tools.InvalidRecipeRating. Without
    the re-export the route's except clause is an AttributeError at request
    time — i.e. a 500 in place of the 422 it exists to give.
    """
    assert tools.InvalidRecipeRating is _recipes.InvalidRecipeRating
    assert tools.RECIPE_RATINGS == ("liked", "disliked")


# ------------------------------------------------------------ over the wire


def test_the_route_answers_422_not_400(signed_in):
    """CATCH. 200 on main, with the word written."""
    _chili()

    res = signed_in.post(
        "/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "teleported"}
    )

    assert res.status_code == 422
    assert "teleported" in res.json()["detail"]
    assert "liked, disliked" in res.json()["detail"], "the refusal says what IS allowed"


def test_a_bad_rating_and_an_unknown_recipe_get_different_codes(signed_in):
    """
    CATCH on the first half, and the two mistakes are asked about in one
    breath because the ONLY thing standing between them is the ORDER of two
    except clauses. InvalidRecipeRating IS a ValueError, so with the clauses
    the other way round the 400 below swallows the refusal and one client
    mistake gets the code that means another. A test that asked about them
    separately could pass with the ordering wrong.
    """
    _chili()

    bad_word = signed_in.post(
        "/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "teleported"}
    )
    no_such_recipe = signed_in.post(
        "/api/recipe-feedback", json={"recipe_name": "Nothing We Cook", "rating": "liked"}
    )

    assert (bad_word.status_code, no_such_recipe.status_code) == (422, 400)


def test_a_rating_that_is_not_even_a_string_is_still_pydantics_422(signed_in):
    """
    GUARD, and the reason this route answers 422 rather than the card's
    suggested 400: it ALREADY answered 422 for this, from pydantic, so 400
    would give one client mistake two different codes depending on which
    layer noticed.
    """
    _chili()

    res = signed_in.post(
        "/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": 7}
    )

    assert res.status_code == 422


# ------------------------------------------- what must NOT have moved


@pytest.mark.parametrize("rating", ["liked", "disliked"])
def test_both_real_verdicts_still_work(signed_in, rating):
    """GUARD. Pinned by the mutation that narrows RECIPE_RATINGS to one word."""
    _chili()

    res = signed_in.post(
        "/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": rating}
    )

    assert res.status_code == 200
    assert res.json()["rating"] == rating
    assert _row()["rating"] == rating


def test_a_notes_only_call_still_leaves_the_rating_alone():
    """
    GUARD, and the third state this door has to keep straight: rating=None is
    not a rating at all, it is "just add notes". It must not be caught by a
    vocabulary check written for words — the guard asks about None first.
    Pinned by the mutation that drops the `rating is not None` term, which
    turns every notes-only call into a refusal.
    """
    _chili(rating="liked")

    result = tools.mark_recipe_feedback("Bean Chili", notes="a bit too spicy for the kids")

    assert result["rating"] is None
    row = _row()
    assert row["rating"] == "liked", "the verdict is untouched"
    assert "too spicy" in row["feedback_notes"]


def test_the_chat_tools_schema_still_enumerates_exactly_these_two():
    """
    GUARD. The chat tool's schema has always enumerated the two, which is why
    a third word needed a hand-made request to reach the column at all. If
    that enum is ever dropped, this guard stops being the only door and the
    one above becomes load-bearing for chat too — same reasoning the cooked
    tick's entry records.
    """
    from app import agent

    schema = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "mark_recipe_feedback")
    enum = schema["input_schema"]["properties"]["rating"]["enum"]

    assert tuple(enum) == tools.RECIPE_RATINGS


def test_the_sibling_chat_door_reads_the_same_vocabulary():
    """
    attribute_recipe_feedback — the other chat tool that takes a rating — was
    checked on 2026-09-26 and did NOT have this hole: it already refused a
    third word before any of this. What moved is only where the two words
    live. Its own hard-coded ("liked", "disliked") was a second copy of one
    rule, so changing RECIPE_RATINGS would have moved one door and not the
    other — which is the drift this repo keeps recording. Green on main for
    the refusal, red only on the marker's name.
    """
    tools.add_member("Vineeth")
    _chili()

    with pytest.raises(tools.InvalidRecipeRating):
        tools.attribute_recipe_feedback("Bean Chili", "Vineeth", rating="teleported")

    conn = get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) AS c FROM member_recipe_feedback").fetchone()["c"] == 0
    finally:
        conn.close()


def test_a_solo_night_still_learns_from_a_real_verdict(signed_in, monkeypatch):
    """
    GUARD, and the control for the whole card: the solo-night path is what
    CRASHED on a third word, so it has to be shown still working on a real
    one. Pinned by the mutation that empties _maybe_auto_attribute_solo_night.
    """
    import datetime

    tools.add_member("Emily")
    tools.add_member("Vineeth")
    _chili()
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    week = (monday + datetime.timedelta(days=7)).isoformat()
    thursday = tools._week_dates(week)[3]
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)
    entry = tools.plan_meal(thursday, "Bean Chili", slot="dinner")
    tools.check_off_meal(entry["entry_id"])

    res = signed_in.post(
        "/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "liked"}
    )

    assert res.status_code == 200
    assert res.json()["solo_auto_attribution"] == "Emily"
    assert tools.get_member_taste("Emily")["liked_recipes"] == ["Bean Chili"]


def test_nothing_migrates_a_row_already_carrying_a_third_word():
    """
    The stance every one of the five siblings took, pinned so nobody adds a
    well-meaning backfill later. Reaching such a row needed a hand-made
    request, so the count in the wild is probably zero; a migration inventing
    history is worse than leaving the handful that can only have come from
    somebody's curl. A row written directly still reads back exactly as it is,
    and a real verdict over the top still lands.
    """
    _chili()
    conn = get_conn()
    try:
        conn.execute("UPDATE recipes SET rating = 'teleported' WHERE household_id = 1")
        conn.commit()
    finally:
        conn.close()

    assert _row()["rating"] == "teleported", "left exactly as it was found"

    tools.mark_recipe_feedback("Bean Chili", rating="disliked")

    assert _row()["rating"] == "disliked", "and a real verdict still heals it"
