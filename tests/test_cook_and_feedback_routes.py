"""
The three Cook-screen routes a beta tester can reach that had no test at all:
POST /api/cooker/fill-recipe, POST /api/cooker/log-deviation and
POST /api/recipe-feedback.

All three are named nowhere in tests/ on main. Two of them take a RECIPE NAME
off the wire and resolve it against the household's own recipes, and the third
spends a model call. Measured, the ordinary paths are correct: every one of the
three refuses another household's recipe, refuses an unknown one, and refuses
an unsigned caller, and fill-recipe is idempotent and does not leak Anthropic's
own 401 text to the browser. Most of this file is here so that stays true.

THE LAST SECTION WAS A CHARACTERISATION AND IS NOW A GUARD. When this file
was written, /api/recipe-feedback took ANY rating string and wrote it to
recipes.rating — the SIXTH instance of the class this repo had then fixed five
times (the cooked tick 2026-09-16, chore status, grocery status, attention
status, and a meal slot). It was worse than its five siblings in three
measured ways, all of them now inverted below rather than deleted:

  * it DESTROYED the rating that was there, rather than adding a row nobody
    reads;
  * on a solo night (the last time the recipe was cooked, exactly one member
    was home) the per-person write that follows hit
    member_recipe_feedback's own CHECK(rating IN ('liked','disliked')) and
    the route answered 500 "your data is fine" — which was measurably false,
    because the household rating had already been committed and lost;
  * that crash left _maybe_auto_attribute_solo_night's connection open (no
    try/finally), still holding SQLite's write lock — so the app's own
    record_error could not write and error_events stayed EMPTY. Measured on
    a real uvicorn across five runs, error_events was 0 every single time:
    the one failure the morning report most needs to see was the one it
    could not see. How much ELSE the held lock took down varied — from
    nothing, through one write lost after a 5.5-second hang, to six writes
    over 24 seconds all failing.

FIXED 2026-09-26 on branch overnight/recipe-rating-validated, in both halves,
because closing the wire alone would have left the worse one standing:
recipes.RECIPE_RATINGS + InvalidRecipeRating checked above get_conn, a 422 on
the route placed BEFORE its plain-ValueError 400, and a try/finally on both of
_maybe_auto_attribute_solo_night's connections. The inverted tests keep their
old names and old numbers in their docstrings, so what was wrong stays
readable; tests/test_recipe_rating_validated.py carries the guard's own fuller
coverage, including the ordering of those two except clauses.

WHAT THE EVIDENCE WAS WHEN THIS FILE WAS A CHARACTERISATION, kept because it
is what said which tests to invert: app/ and static/ were byte-identical to
main on that branch, so nothing here could be red against main by
construction, and ten mutations were run instead. Adding the vocabulary guard
— i.e. doing the card — reddened EXACTLY the five DEFECT tests and nothing
else (22 still passed), which is what said they were the ones to invert; and
adding a try/finally to _maybe_auto_attribute_solo_night reddened ONLY the
leak test and none of the vocabulary ones, which is what said the two halves
were independent. The other eight: the household filter dropped from
mark_recipe_feedback (2 red), from log_cooking_deviation (1), and from
list_recipes (1); _client_safe_detail letting a 500 through (2) and scrubbing
503 as well (1); the fill route losing its 503 branch (1); fill losing its
short-circuit on a recipe that already has steps (1); and a notes-only call
clearing the rating (1). One of the ten was BADLY AIMED first time and is
recorded rather than quietly re-run: the log_cooking_deviation mutation hit
the identical two lines inside log_recipe_note, 28 lines above, and reddened
nothing — a finding about the mutation, not about the test, which bites when
aimed at the right function.
"""
from __future__ import annotations

import datetime

import pytest

from app import agent, households, tools
from app.agent import AssistantUnavailableError
from app.db import get_conn


OTHER_PASSPHRASE = "the-other-familys-passphrase"

ROUTES = [
    ("/api/cooker/fill-recipe", {"recipe_name": "Bean Chili"}),
    ("/api/cooker/log-deviation", {"recipe_name": "Bean Chili", "note": "used turkey"}),
    ("/api/recipe-feedback", {"recipe_name": "Bean Chili", "rating": "liked"}),
]


@pytest.fixture(autouse=True)
def no_model_by_default(monkeypatch):
    """
    Nothing in this file may reach Anthropic. fill-recipe normally refuses a
    name it cannot resolve long before the model call, so the clean run never
    tries — but running the isolation mutation (drop the household filter from
    list_recipes) sent a real request with a real request_id, which is one
    changed line away at any time. The three tests that legitimately exercise
    the fill override this from inside their own body, which wins over an
    autouse fixture.
    """
    def unexpected(recipe):
        raise AssertionError("a test reached the model without stubbing it")

    monkeypatch.setattr(agent, "generate_recipe_detail_llm", unexpected)


@pytest.fixture
def other_household():
    return households.create_household("The Other Family", OTHER_PASSPHRASE)


def _chili(instructions=("Simmer for 20 minutes, until it thickens.",)):
    """A saved recipe that already has its steps — nothing for a fill to do."""
    tools.add_recipe(
        "Bean Chili",
        ingredients=[{"item": "Black beans", "qty": "1 can"}],
        instructions=list(instructions),
    )
    return "Bean Chili"


def _unfilled(name="Chicken Skewers"):
    """A saved recipe with ingredients and no steps — what Fill in this recipe is for."""
    tools.add_recipe(name, ingredients=[{"item": "chicken thigh", "qty": "1 lb"}])
    return name


def _seed_in(household, name="Other Family Lasagne"):
    """
    One recipe belonging to somebody else.

    use_household is not a convenience. Signing a TestClient in sets the
    household for a REQUEST; a tool called straight from a test runs outside
    one, on the ContextVar's default. Seeding this with a bare add_recipe
    writes it into household 1, and the isolation tests below then pass while
    proving nothing — the trap tests/test_attention_routes.py records having
    fallen into first time out.
    """
    with tools.use_household(household):
        tools.add_recipe(name, ingredients=[{"item": "Pasta", "qty": "1 box"}])
    return name


def _rating_of(name="Bean Chili"):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT rating, feedback_notes FROM recipes WHERE household_id = 1 AND name = ?",
            (name,),
        ).fetchone()
        return (row["rating"], row["feedback_notes"])
    finally:
        conn.close()


def _rated_count():
    """
    The count memory.py:221 runs for the 'what we know' completeness score:
    SELECT COUNT(*) ... WHERE rating IN ('liked', 'disliked').
    """
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM recipes WHERE household_id = 1 "
            "AND rating IN ('liked', 'disliked')"
        ).fetchone()["c"]
    finally:
        conn.close()


def _deviation_notes():
    conn = get_conn()
    try:
        return [
            (r["household_id"], r["note_type"], r["note"])
            for r in conn.execute(
                "SELECT household_id, note_type, note FROM recipe_notes ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()


def _solo_night(recipe_name):
    """
    Cook `recipe_name` on a night exactly one of two members was home, and
    check it off — the shape _maybe_auto_attribute_solo_night looks for.
    Mirrors tests/test_per_person_taste.py's own _cook_solo.
    """
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    week = (monday + datetime.timedelta(days=7)).isoformat()
    thursday = tools._week_dates(week)[3]
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)
    entry = tools.plan_meal(thursday, recipe_name, slot="dinner")
    tools.check_off_meal(entry["entry_id"])


# ---------------------------------------------------------------------------
# The happy paths, and whether the answer matches what was written
# ---------------------------------------------------------------------------

def test_a_rating_lands_on_the_recipe_and_the_answer_says_what_landed(signed_in):
    _chili()

    res = signed_in.post(
        "/api/recipe-feedback",
        json={"recipe_name": "Bean Chili", "rating": "liked", "notes": "the kids ate it"},
    )

    assert res.status_code == 200
    assert res.json() == {
        "name": "Bean Chili", "rating": "liked",
        "feedback_notes": "the kids ate it", "solo_auto_attribution": None,
    }
    assert _rating_of() == ("liked", "the kids ate it")


def test_notes_with_no_rating_append_and_leave_the_rating_alone(signed_in):
    """
    rating is optional on purpose — 'just add this note' must not silently
    clear a verdict the household already gave.
    """
    _chili()
    signed_in.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "liked", "notes": "first"})

    res = signed_in.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "notes": "second"})

    assert res.status_code == 200
    assert res.json()["feedback_notes"] == "first | second"
    assert _rating_of() == ("liked", "first | second"), "the rating survived a notes-only call"


def test_a_solo_nights_rating_is_attributed_to_whoever_was_there(signed_in):
    """
    The route's answer carries solo_auto_attribution, so this pins that the
    silent per-person learning really reaches the wire and not just the tool.
    """
    _chili()
    _solo_night("Bean Chili")

    res = signed_in.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "liked"})

    assert res.status_code == 200
    assert res.json()["solo_auto_attribution"] == "Emily"
    assert tools.get_member_taste("Emily")["liked_recipes"] == ["Bean Chili"]


def test_a_deviation_is_written_as_a_deviation_note_against_that_recipe(signed_in):
    _chili()

    res = signed_in.post(
        "/api/cooker/log-deviation",
        json={"recipe_name": "Bean Chili", "note": "used ground turkey instead of beef"},
    )

    assert res.status_code == 200
    assert res.json() == {"name": "Bean Chili", "note": "used ground turkey instead of beef"}
    assert _deviation_notes() == [(1, "deviation", "used ground turkey instead of beef")]


def test_a_deviation_never_touches_the_recipes_permanent_rating(signed_in):
    """
    The whole point of log_cooking_deviation next to mark_recipe_feedback:
    one bad night is not a verdict. Same door, two different columns.
    """
    _chili()
    signed_in.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "liked"})

    signed_in.post("/api/cooker/log-deviation", json={"recipe_name": "Bean Chili", "note": "skipped the marinade"})

    assert _rating_of()[0] == "liked"


def test_filling_a_recipe_writes_the_steps_and_answers_with_the_cook_view(signed_in, monkeypatch):
    """
    Drives the REAL fill_in_recipe with only the model call stubbed, so the
    save path, the cooking-quantity validation and the consistency check all
    run. The answer is the refreshed cooker view (what the screen re-renders
    from), never the recipe — so the write is what is asserted.
    """
    _unfilled()
    monkeypatch.setattr(agent, "generate_recipe_detail_llm", lambda recipe: {
        "instructions": [
            "Cube the chicken thigh and thread it onto the skewers.",
            "Grill over medium-high for 4 minutes a side, until the edges char.",
        ],
        "default_servings": 4, "prep_time_minutes": 10, "cook_time_minutes": 12,
        "advance_prep_notes": "", "advance_prep_step_indices": [],
    })

    res = signed_in.post("/api/cooker/fill-recipe", json={"recipe_name": "Chicken Skewers"})

    assert res.status_code == 200
    assert "meals" in res.json(), "the answer is a cooker view"
    saved = tools.get_recipe("Chicken Skewers")
    assert len(saved["instructions"]) == 2
    assert saved["prep_time_minutes"] == 10


def test_filling_a_recipe_that_already_has_steps_calls_no_model_at_all(signed_in, monkeypatch):
    """
    Idempotent by fill_in_recipe's own contract. It matters at the route,
    because the button is one tap and nothing stops a second one — and this
    route is in no rate-limit bucket (checked: 'scan' covers the photo
    routes, 'chat' the two chat routes, and this one neither).
    """
    _chili()
    calls = []
    monkeypatch.setattr(agent, "generate_recipe_detail_llm", lambda recipe: calls.append(recipe))

    res = signed_in.post("/api/cooker/fill-recipe", json={"recipe_name": "Bean Chili"})

    assert res.status_code == 200
    assert calls == [], "a recipe that already has steps is returned as-is"
    assert tools.get_recipe("Bean Chili")["instructions"] == ["Simmer for 20 minutes, until it thickens."]


# ---------------------------------------------------------------------------
# Another household's recipe
# ---------------------------------------------------------------------------

def test_another_households_recipe_is_not_reachable_by_name(signed_in, other_household):
    """
    All three resolve a name against `WHERE household_id = ?`, so the answer
    is the same sentence an unknown name gets — which is the right answer:
    'that one exists but is not yours' is itself a leak.
    """
    name = _seed_in(other_household)

    for path, body in ROUTES:
        payload = dict(body, recipe_name=name)
        res = signed_in.post(path, json=payload)
        assert res.status_code == 400, path
        assert "No recipe named" in res.json()["detail"], path


def test_a_rating_aimed_at_another_household_writes_nothing_there(signed_in, other_household):
    """
    The refusal above is only worth having if nothing was written on the way
    to it. Checked on the other household's own row, not on the answer.
    """
    name = _seed_in(other_household)

    signed_in.post("/api/recipe-feedback", json={"recipe_name": name, "rating": "disliked"})

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT household_id, rating FROM recipes WHERE name = ?", (name,)
        ).fetchone()
    finally:
        conn.close()
    assert (row["household_id"], row["rating"]) == (other_household, "")
    assert _deviation_notes() == []


def test_the_isolation_test_above_can_actually_fail(signed_in, other_household):
    """
    A mutation, run as a test: force the seed into household 1 the way a
    bare tool call would, and the very same request SUCCEEDS. This repo has
    been bitten by isolation tests that never crossed the boundary they
    name, so the boundary is crossed here on purpose.
    """
    name = "Other Family Lasagne"
    tools.add_recipe(name, ingredients=[{"item": "Pasta", "qty": "1 box"}])  # household 1, deliberately

    res = signed_in.post("/api/recipe-feedback", json={"recipe_name": name, "rating": "liked"})

    assert res.status_code == 200, "so the 400s above are the household filter, not the name"


# ---------------------------------------------------------------------------
# Not signed in
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,body", ROUTES, ids=[p.rsplit("/", 1)[-1] for p, _ in ROUTES])
def test_every_one_of_the_three_refuses_a_caller_who_is_not_signed_in(client, path, body):
    _chili()
    res = client.post(path, json=body)
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# A name that is not there, and a body that is not a request
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,body", ROUTES, ids=[p.rsplit("/", 1)[-1] for p, _ in ROUTES])
def test_an_unknown_recipe_is_400_with_a_sentence_on_all_three(signed_in, path, body):
    """
    400 rather than 404, and the same on all three. Defensible: the URL
    these three address exists — the name is part of the REQUEST, so a name
    nobody has is a request that does not make sense. Recorded rather than
    argued, because the attention and grocery routes answer 404 for a row id
    and the difference is real.
    """
    res = signed_in.post(path, json=dict(body, recipe_name="No Such Dish"))

    assert res.status_code == 400
    assert "No recipe named 'No Such Dish'" in res.json()["detail"]


def test_a_body_missing_a_required_field_is_422_before_anything_is_read(signed_in):
    _chili()
    assert signed_in.post("/api/cooker/log-deviation", json={"recipe_name": "Bean Chili"}).status_code == 422
    assert signed_in.post("/api/cooker/fill-recipe", json={}).status_code == 422
    assert signed_in.post("/api/recipe-feedback", json={"notes": "x"}).status_code == 422
    assert _deviation_notes() == []


# ---------------------------------------------------------------------------
# What the browser is allowed to read when the model half goes wrong
# ---------------------------------------------------------------------------

def test_a_model_failure_in_fill_recipe_never_shows_the_exception_to_the_browser(signed_in, monkeypatch):
    """
    fill-recipe is the only one of the three that talks to Anthropic, and an
    invalid key raises anthropic.AuthenticationError carrying the whole 401
    body — 'Error code: 401 - {...}' — which _client_safe_detail's own
    docstring names as the thing that used to reach toasts verbatim.
    Reproduced against a real uvicorn with a bogus key before this was
    written: 500, scrubbed.
    """
    _unfilled()

    def boom(recipe):
        raise RuntimeError("Error code: 401 - {'type': 'error', 'request_id': 'req_secret'}")

    monkeypatch.setattr(agent, "generate_recipe_detail_llm", boom)

    res = signed_in.post("/api/cooker/fill-recipe", json={"recipe_name": "Chicken Skewers"})

    assert res.status_code == 500
    detail = res.json()["detail"]
    assert "401" not in detail and "req_secret" not in detail
    assert detail == "Something went wrong on my side just now — your data is fine. Try again in a minute."


def test_claudes_own_warm_line_survives_the_scrubber_as_a_503(signed_in, monkeypatch):
    """
    The one 5xx _client_safe_detail deliberately does NOT scrub.
    AssistantUnavailableError's contract is that its message is written for
    the person, so a 503 has to carry it word for word — a fill that says
    'something went wrong on my side' for an Anthropic outage is the app
    taking the blame for weather.

    This stubs the model call and not the retry loop: what it pins is the
    ROUTE's mapping of that exception onto 503 plus the scrubber's carve-out.
    The retrying itself is pinned in tests/test_streaming_and_effort.py.
    """
    _unfilled()
    warm = ("I'm having trouble reaching Claude's servers right now — looks like a temporary "
            "hiccup on their end, not anything wrong with your data.")

    def unavailable(recipe):
        raise AssistantUnavailableError(warm)

    monkeypatch.setattr(agent, "generate_recipe_detail_llm", unavailable)

    res = signed_in.post("/api/cooker/fill-recipe", json={"recipe_name": "Chicken Skewers"})

    assert res.status_code == 503
    assert res.json()["detail"] == warm


def test_a_failed_fill_leaves_the_recipe_exactly_as_it_was(signed_in, monkeypatch):
    _unfilled()
    monkeypatch.setattr(agent, "generate_recipe_detail_llm", lambda recipe: {"instructions": []})

    res = signed_in.post("/api/cooker/fill-recipe", json={"recipe_name": "Chicken Skewers"})

    assert res.status_code == 400
    assert tools.get_recipe("Chicken Skewers")["instructions"] in ([], None)


# ---------------------------------------------------------------------------
# FIXED 2026-09-26 (branch overnight/recipe-rating-validated). These three
# were characterisations of the sixth instance of a class this repo had fixed
# five times; they are INVERTED in place rather than deleted, so the history
# reads and so the exact behaviour that was wrong stays written down.
#
# Each one now asserts the refusal where it used to assert the write. Their
# old bodies are quoted in the docstrings, because "what this used to do" is
# the whole reason the test exists. The two crash characterisations further
# down are inverted the same way, off the same subprocess probe.
# ---------------------------------------------------------------------------

def test_a_word_that_is_not_a_verdict_is_refused_and_nothing_is_written(signed_in):
    """
    WAS test_DEFECT_any_word_at_all_is_accepted_as_a_rating_and_written, and
    it asserted 200 with the row reading `teleported`.

    The column's own comment says '' | 'liked' | 'disliked' (schema.sql), the
    chat tool's schema enumerates ['liked','disliked'], and the Cook screen
    sends only those two (data-rating in shell.js) — and now something
    between the wire and the UPDATE checks. 422 + InvalidRecipeRating, the
    way InvalidGroceryStatus / InvalidMealStatus / InvalidChoreStatus read.
    """
    _chili()

    res = signed_in.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "teleported"})

    assert res.status_code == 422
    assert "teleported" in res.json()["detail"]
    assert _rating_of()[0] == "", "nothing written — the refusal is above get_conn"


def test_a_third_word_can_no_longer_destroy_the_rating_that_was_there(signed_in):
    """
    WAS test_DEFECT_a_third_word_destroys_the_rating_that_was_there, and it
    asserted the row came back reading `Liked` with the recipe silently no
    longer counting as rated.

    This is the half that made it worse than its five siblings: the
    grocery/chore/attention versions of that bug put a row into a state
    nothing reads, where this OVERWROTE a verdict the household actually
    gave, committed, with nothing anywhere keeping the old one. A capital
    letter is still a different word to every reader — which is why it is
    refused rather than quietly folded.
    """
    _chili()
    signed_in.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "liked"})
    assert _rating_of()[0] == "liked"

    res = signed_in.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "Liked"})

    assert res.status_code == 422
    assert _rating_of()[0] == "liked", "the verdict the household gave survives"
    assert _rated_count() == 1, "and it still counts as rated"


def test_the_readers_that_matter_keep_seeing_the_verdict(signed_in):
    """
    WAS test_DEFECT_a_third_word_makes_the_recipe_invisible_to_the_readers_
    that_matter, and it asserted the recipe dropped out of the completeness
    count and came back as a planning candidate.

    The three real readers of recipes.rating, and what a third word used to
    do to each:
      * memory.py:221 counts rating IN ('liked','disliked') for the
        'what we know' completeness score — the recipe dropped out of it;
      * list_recipes ORDER BY (rating='liked') DESC sorted it as unrated;
      * weekly_plan.py's candidate query is rating != 'disliked', so a dish
        the household had explicitly rejected became a candidate again.
    All three now go on reading the verdict, because the refusal never
    reaches the UPDATE.
    """
    _chili()
    signed_in.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "disliked"})
    assert _rated_count() == 1

    res = signed_in.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "not for us"})

    assert res.status_code == 422
    assert _rated_count() == 1, "still rated, as far as the completeness score knows"
    conn = get_conn()
    try:
        candidates = [
            r["name"] for r in conn.execute(
                "SELECT name FROM recipes WHERE household_id = 1 AND rating != 'disliked' "
                "AND temporarily_excluded = 0"
            ).fetchall()
        ]
    finally:
        conn.close()
    assert candidates == [], "the dish the household rejected stays rejected"


# ---------------------------------------------------------------------------
# The two crash-path characterisations run in a SUBPROCESS, with a database
# of their own, and the reason is the finding itself.
#
# _maybe_auto_attribute_solo_night's connection is opened inside uvicorn's
# (and TestClient's) worker thread, so when the INSERT raises, the still-open
# connection belongs to a thread that is now parked in the pool. Measured: it
# cannot be closed from the test thread at all — sqlite3 answers "SQLite
# objects created in a thread can only be used in that same thread" — and
# gc.collect() does not free it either, because the reference is live on that
# thread's exception state rather than in a cycle. The suite shares ONE
# database file for the whole session (tests/conftest.py), so running this
# in-process really does wedge every test after it: the first version of this
# file did exactly that, and the next test errored in its own fixture with
# "database is locked" before its first line ran.
#
# A subprocess is also the honest shape, because it is how this was measured
# in the first place: a real uvicorn on a throwaway DB, where the household's
# next two writes both 500'd and were still 500ing 24 seconds later.
# ---------------------------------------------------------------------------

_CRASH_PROBE = r"""
import datetime, json, os, sqlite3, sys, gc
from fastapi.testclient import TestClient
from app.main import app
from app import tools
from app.db import get_conn, init_db

init_db()
conn = get_conn()
conn.execute("INSERT OR IGNORE INTO households (id, name) VALUES (1, 'Probe Household')")
conn.commit(); conn.close()

client = TestClient(app)
client.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)

tools.add_member("Emily"); tools.add_member("Vineeth")
tools.add_recipe("Bean Chili", ingredients=[{"item": "Black beans", "qty": "1 can"}])
today = datetime.date.today()
monday = today - datetime.timedelta(days=today.weekday())
week = (monday + datetime.timedelta(days=7)).isoformat()
thursday = tools._week_dates(week)[3]
tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)
entry = tools.plan_meal(thursday, "Bean Chili", slot="dinner")
tools.check_off_meal(entry["entry_id"])

def rating():
    c = get_conn()
    try:
        return c.execute("SELECT rating FROM recipes WHERE household_id = 1 AND name = 'Bean Chili'").fetchone()[0]
    finally:
        c.close()

def open_transactions():
    n = 0
    for o in gc.get_objects():
        if isinstance(o, sqlite3.Connection):
            try:
                if o.in_transaction:
                    n += 1
            except Exception:
                pass
    return n

out = {}
good = client.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "liked"})
out["good_status"] = good.status_code
out["good_attribution"] = good.json()["solo_auto_attribution"]
out["rating_before"] = rating()
out["member_taste_before"] = tools.get_member_taste("Emily")["liked_recipes"]
out["open_transactions_before"] = open_transactions()

# Is the write lock genuinely held, or is there merely an object lying around?
# Asked with no patience at all (timeout=0), so the answer is about the lock
# rather than about how long anyone is willing to wait for it.
def write_lock_free():
    probe = sqlite3.connect(os.environ["DB_PATH"], timeout=0)
    try:
        probe.execute("BEGIN IMMEDIATE")
        probe.rollback()
        return True, ""
    except sqlite3.OperationalError as e:
        return False, str(e)
    finally:
        probe.close()

def error_event_rows():
    c = get_conn()
    try:
        return [
            dict(r) for r in c.execute(
                "SELECT kind, where_, error_type FROM error_events ORDER BY id"
            ).fetchall()
        ]
    finally:
        c.close()

# PART ONE — the refusal. The same solo-night sequence that used to answer 500
# over a destroyed rating.
bad = client.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "teleported"})
out["bad_status"] = bad.status_code
out["bad_detail"] = bad.json()["detail"]
out["rating_after"] = rating()
out["member_taste_after"] = tools.get_member_taste("Emily")["liked_recipes"]
out["open_transactions_after"] = open_transactions()
out["error_events"] = len(error_event_rows())
free, err = write_lock_free()
out["write_lock_free"] = free
out["write_lock_error"] = err
nxt = client.post("/api/grocery-list/add", json={"item": "Milk", "quantity": "1"})
out["next_write_status"] = nxt.status_code

# PART TWO — the NEXT unexpected failure of that same INSERT, which the
# vocabulary guard on the wire does nothing about.
#
# RECIPE_RATINGS is widened to let a third word past the guard, exactly the way
# a future edit to that one line would. member_recipe_feedback.rating still
# carries CHECK(rating IN ('liked','disliked')), so the per-person INSERT still
# raises with the connection open — which is the original crash path, reached
# without pretending the guard is the only thing standing between this function
# and a leaked write lock. What is measured is what the try/finally buys: the
# lock comes back, and record_error can therefore write, so the failure is
# VISIBLE to the morning report instead of silent.
from app.tools import recipes as _recipes
_recipes.RECIPE_RATINGS = ("liked", "disliked", "teleported")

crash = client.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "teleported"})
out["crash_status"] = crash.status_code
out["crash_open_transactions"] = open_transactions()
free2, err2 = write_lock_free()
out["crash_write_lock_free"] = free2
out["crash_write_lock_error"] = err2
out["crash_error_events"] = error_event_rows()
after = client.post("/api/grocery-list/add", json={"item": "Oat milk", "quantity": "1"})
out["crash_next_write_status"] = after.status_code

print("PROBE" + json.dumps(out))
"""


@pytest.fixture(scope="module")
def crash_probe(tmp_path_factory):
    """
    One subprocess, one throwaway database, both crash characterisations read
    off the same run — the sequence a household would actually produce.
    """
    import json
    import os
    import subprocess
    import sys

    workdir = tmp_path_factory.mktemp("crash-probe")
    script = workdir / "probe.py"
    script.write_text(_CRASH_PROBE)

    env = dict(os.environ)
    env.update({
        "DB_PATH": str(workdir / "probe.db"),
        "HOME_MANAGER_PASSWORD": "test-password",
        "SESSION_SECRET": "probe-session-secret",
        "ANTHROPIC_API_KEY": "test-key-not-used",
        "DISABLE_BACKUPS": "1",
        "DISABLE_MORNING_TEXT": "1",
    })
    env.pop("HOME_MANAGER_URL", None)
    env.pop("REPORT_TOKEN", None)

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = repo_root
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=repo_root, env=env, capture_output=True, text=True, timeout=300,
    )
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE")), None)
    assert line, f"probe produced no result\nSTDOUT:\n{proc.stdout[-3000:]}\nSTDERR:\n{proc.stderr[-3000:]}"
    return json.loads(line[len("PROBE"):])


def test_the_crash_probes_own_control_is_a_good_rating_on_the_same_night(crash_probe):
    """
    Green, and here first so nothing below reads as 'solo nights are broken'.
    The identical sequence with one of the two real words answers 200, writes
    both rows, and leaves no transaction open.
    """
    assert crash_probe["good_status"] == 200
    assert crash_probe["good_attribution"] == "Emily"
    assert crash_probe["rating_before"] == "liked"
    assert crash_probe["member_taste_before"] == ["Bean Chili"]
    assert crash_probe["open_transactions_before"] == 0


def test_on_a_solo_night_a_third_word_is_refused_before_anything_is_written(crash_probe):
    """
    WAS test_DEFECT_on_a_solo_night_a_third_word_is_a_500_that_says_your_
    data_is_fine, and it asserted 500, the branded "your data is fine"
    sentence, and a household rating reading `teleported` while the
    per-person row still said `liked`.

    What used to happen, kept because it is the reason the guard is above
    get_conn rather than anywhere else: the household-level UPDATE was
    COMMITTED, and only THEN did _maybe_auto_attribute_solo_night try to put
    the same word into member_recipe_feedback, whose column really does carry
    CHECK(rating IN ('liked','disliked')) — schema.sql:408. SQLite raised, the
    route's bare `except Exception` answered 500, and _client_safe_detail
    replaced the message with "your data is fine", which was not true: the
    verdict the household gave had already been destroyed, and the per-person
    row still held the old one, so the two disagreed about one recipe.

    Now: 422, and the two rows still agree because neither was touched.
    """
    assert crash_probe["bad_status"] == 422
    assert "teleported" in crash_probe["bad_detail"]
    assert crash_probe["rating_after"] == "liked", "the verdict survives"
    assert crash_probe["member_taste_after"] == ["Bean Chili"], (
        "and the per-person row still agrees with it"
    )
    assert crash_probe["next_write_status"] == 200, (
        "and the household's next write is not paying for any of it"
    )


def test_the_refusal_leaks_no_connection_and_holds_no_lock(crash_probe):
    """
    WAS the first half of test_DEFECT_that_crash_leaks_the_write_lock_and_
    wedges_the_next_write, which asserted one connection still inside a
    transaction and a BEGIN IMMEDIATE refused outright.

    Nothing is open and nothing is held, which is what a request that decided
    not to write should leave behind.
    """
    assert crash_probe["open_transactions_after"] == 0
    assert crash_probe["write_lock_free"] is True, crash_probe["write_lock_error"]
    assert crash_probe["error_events"] == 0, "a refusal is not a breakage"


def test_the_next_failure_of_that_insert_gives_the_lock_back_and_is_VISIBLE(crash_probe):
    """
    WAS the second half of that same DEFECT test — the one that asserted
    error_events stayed EMPTY — and it is the half a vocabulary guard on the
    wire does NOT fix, which is why this probe forces the failure rather than
    relying on the guard being the only thing in the way.

    RECIPE_RATINGS is widened in the probe to let a third word past the wire,
    exactly the way a future edit to that one line would; the per-person
    INSERT still hits its own CHECK and still raises with the connection open.
    Before the try/finally that connection was left holding SQLite's write
    lock, on a worker thread no other thread may even close ("SQLite objects
    created in a thread can only be used in that same thread") and that
    gc.collect() will not free, because the reference is live on that thread's
    exception state rather than in a cycle. Measured on a real uvicorn
    2026-09-26: the household's next write waited out sqlite3's full 5-second
    busy timeout (5.03s) and then 500'd, and tools.record_error could not
    write either.

    THE SYMPTOM IS WHAT THIS ASSERTS, because the symptom is what a test can
    see: error_events gets a row, FOR THIS ROUTE. That matters more than the
    count — in the original measurement error_events did eventually reach 1,
    but the row was for /api/grocery-list/add, the collateral write; the
    failure that caused all of it recorded nothing at all, so the morning
    report would have shown a grocery error and no way to learn what broke.
    """
    assert crash_probe["crash_status"] == 500, (
        "the forced failure is still a failure — the point is that it is now a visible one"
    )
    assert crash_probe["crash_open_transactions"] == 0, (
        "the connection is closed on the way out, however the function leaves"
    )
    assert crash_probe["crash_write_lock_free"] is True, crash_probe["crash_write_lock_error"]

    rows = crash_probe["crash_error_events"]
    assert rows, "WAS EMPTY — the one failure the morning report most needs to see"
    assert any(r["where_"] == "/api/recipe-feedback" for r in rows), (
        f"and it is recorded against the route that actually broke: {rows}"
    )
    assert crash_probe["crash_next_write_status"] == 200, (
        "and the household's next write is not paying for it either"
    )


def test_log_deviation_and_fill_recipe_take_no_vocabulary_off_the_wire(signed_in):
    """
    The other half of the sixth-instance hunt, recorded because a negative
    result is worth as much as a positive one. log-deviation hard-codes
    note_type='deviation' and takes only free text, and fill-recipe takes
    only a name — so recipes.rating is the ONLY out-of-vocabulary write
    reachable through these three routes.
    """
    _chili()
    signed_in.post("/api/cooker/log-deviation", json={"recipe_name": "Bean Chili", "note": "anything at all"})

    assert [n[1] for n in _deviation_notes()] == ["deviation"]
