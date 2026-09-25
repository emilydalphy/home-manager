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

WHAT IS NOT CORRECT is the last section, and it is deliberately a
characterisation rather than a fix. /api/recipe-feedback takes ANY rating
string and writes it to recipes.rating — the SIXTH instance of the class this
repo has now fixed five times (the cooked tick 2026-09-16, chore status,
grocery status, attention status, and a meal slot). It is worse than its five
siblings in three measured ways, all pinned below:

  * it DESTROYS the rating that was there, rather than adding a row nobody
    reads;
  * on a solo night (the last time the recipe was cooked, exactly one member
    was home) the per-person write that follows hits
    member_recipe_feedback's own CHECK(rating IN ('liked','disliked')) and
    the route answers 500 "your data is fine" — which is measurably false,
    because the household rating has already been committed and lost;
  * that crash leaves _maybe_auto_attribute_solo_night's connection open
    (no try/finally), still holding SQLite's write lock — so the app's own
    record_error cannot write and error_events stays EMPTY. Measured on a
    real uvicorn across five runs, error_events was 0 every single time:
    the one failure the morning report most needs to see is the one it
    cannot see. How much ELSE the held lock takes down varies (see the
    leak test) — from nothing, through one write lost after a 5.5-second
    hang, to six writes over 24 seconds all failing.

Not fixed here, on purpose, and the reasons are in the report and on the card:
a vocabulary guard alone closes the trigger and leaves the leaked connection —
which is the half nothing can see — and `rating` has a third legitimate state
(None, meaning notes-only) plus a documented '' in the column, so what may
arrive off the wire is a product call rather than a one-line copy of the five
siblings. Invert the tests marked DEFECT when the card is done.

THE EVIDENCE IS MUTATION, NOT REDNESS. app/ and static/ are byte-identical to
main on this branch, so nothing here can be red against main by construction.
Ten mutations were run and every one bit: adding the vocabulary guard — i.e.
doing the card — reddens EXACTLY the five DEFECT tests and nothing else (22
still pass), which is what says they are the ones to invert; and adding a
try/finally to _maybe_auto_attribute_solo_night reddens ONLY the leak test and
none of the vocabulary ones, which is what says the two halves of this defect
are independent and that closing the wire alone would leave the worse half
standing. The other eight: the household filter dropped from
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
# DEFECT — /api/recipe-feedback takes any rating string at all.
#
# The sixth instance of a class this repo has fixed five times. Every test
# below records behaviour that is WRONG; invert them when the card is done.
# ---------------------------------------------------------------------------

def test_DEFECT_any_word_at_all_is_accepted_as_a_rating_and_written(signed_in):
    """
    WRONG. The column's own comment says '' | 'liked' | 'disliked'
    (schema.sql), the chat tool's schema enumerates ['liked','disliked'],
    and the Cook screen sends only those two (data-rating in shell.js) —
    but nothing between the wire and the UPDATE checks.

    Invert to 422 + InvalidRecipeRating when the card is done, the way
    InvalidGroceryStatus / InvalidMealStatus / InvalidChoreStatus read.
    """
    _chili()

    res = signed_in.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "teleported"})

    assert res.status_code == 200, "WRONG — a word no screen looks for should be refused"
    assert res.json()["rating"] == "teleported"
    assert _rating_of()[0] == "teleported"


def test_DEFECT_a_third_word_destroys_the_rating_that_was_there(signed_in):
    """
    WRONG, and the half that makes this worse than its five siblings: the
    grocery/chore/attention versions of this bug put a row into a state
    nothing reads. This OVERWRITES a verdict the household actually gave,
    and nothing anywhere keeps the old one.
    """
    _chili()
    signed_in.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "liked"})
    assert _rating_of()[0] == "liked"

    signed_in.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "Liked"})

    assert _rating_of()[0] == "Liked", "WRONG — even a capital letter is a different word to every reader"
    assert _rated_count() == 0, "WRONG — the recipe has silently stopped counting as rated"


def test_DEFECT_a_third_word_makes_the_recipe_invisible_to_the_readers_that_matter(signed_in):
    """
    WRONG. Measured against the three real readers of recipes.rating:
      * memory.py:221 counts rating IN ('liked','disliked') for the
        'what we know' completeness score — the recipe drops out of it;
      * list_recipes ORDER BY (rating='liked') DESC sorts it as unrated;
      * weekly_plan.py's candidate query is rating != 'disliked', so it
        stays a candidate — the one safe direction of the three.
    """
    _chili()
    signed_in.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "disliked"})
    assert _rated_count() == 1

    signed_in.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "not for us"})

    assert _rated_count() == 0, "WRONG — no longer rated, as far as the completeness score knows"
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
    assert candidates == ["Bean Chili"], "a dish the household rejected is a planning candidate again"


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

bad = client.post("/api/recipe-feedback", json={"recipe_name": "Bean Chili", "rating": "teleported"})
out["bad_status"] = bad.status_code
out["bad_detail"] = bad.json()["detail"]
out["rating_after"] = rating()
out["member_taste_after"] = tools.get_member_taste("Emily")["liked_recipes"]
out["open_transactions_after"] = open_transactions()

c = get_conn()
try:
    out["error_events"] = c.execute("SELECT COUNT(*) FROM error_events").fetchone()[0]
finally:
    c.close()

# Is the write lock genuinely still held, or is there merely an object lying
# around? Asked from this thread with no patience at all, so the answer is
# about the lock rather than about how long anyone waits for it.
probe = sqlite3.connect(os.environ["DB_PATH"], timeout=0)
try:
    probe.execute("BEGIN IMMEDIATE")
    out["write_lock_free"] = True
    probe.rollback()
except sqlite3.OperationalError as e:
    out["write_lock_free"] = False
    out["write_lock_error"] = str(e)
finally:
    probe.close()

nxt = client.post("/api/grocery-list/add", json={"item": "Milk", "quantity": "1"})
out["next_write_status"] = nxt.status_code

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


def test_DEFECT_on_a_solo_night_a_third_word_is_a_500_that_says_your_data_is_fine(crash_probe):
    """
    WRONG, and the loudest of the set.

    The household-level UPDATE is COMMITTED, and only then does
    _maybe_auto_attribute_solo_night try to put the same word into
    member_recipe_feedback, whose column really does carry
    CHECK(rating IN ('liked', 'disliked')) — schema.sql:408. SQLite raises,
    the route's bare `except Exception` answers 500, and _client_safe_detail
    replaces the message with "your data is fine", which is not true: the
    verdict the household gave has already been destroyed, and the per-person
    row still holds the old one, so the two now disagree about one recipe.

    Invert when the card is done: a refused rating should be a 422 that
    writes nothing at all.
    """
    assert crash_probe["bad_status"] == 500
    assert crash_probe["bad_detail"].endswith("your data is fine. Try again in a minute.")
    assert crash_probe["rating_after"] == "teleported", "WRONG — the data is NOT fine; 'liked' is gone"
    assert crash_probe["member_taste_after"] == ["Bean Chili"], (
        "and the per-person row still says liked, so the two disagree about one recipe"
    )


def test_DEFECT_that_crash_leaks_the_write_lock_and_wedges_the_next_write(crash_probe):
    """
    WRONG, and it is the half a vocabulary guard on the wire would NOT fix —
    any other failure of that INSERT does the same thing.

    _maybe_auto_attribute_solo_night opens a connection, runs the INSERT that
    raises, and has no try/finally, so the connection is left open holding
    SQLite's write lock. What is asserted here is the mechanism — one
    connection still inside a transaction, and a BEGIN IMMEDIATE from another
    thread refused outright — plus the consequence the app cannot recover
    from: tools.record_error cannot write, so error_events stays EMPTY. The
    one failure the morning report most needs to see is the one it cannot
    see.

    WHAT THE HELD LOCK THEN COSTS IS NOT DETERMINISTIC, and that is worth
    writing down rather than asserting the convenient half. The reference is
    live on the worker thread's exception state, not in a cycle, so
    gc.collect() does not free it and no other thread may even close it
    ("SQLite objects created in a thread can only be used in that same
    thread"). It is released when that thread is handed its next piece of
    work — so whether anything else suffers depends entirely on which worker
    the next request lands on. Under TestClient there is one worker, so the
    next request reuses it and clears it before the handler runs (pinned
    below as 200). Under uvicorn the pool has many, and four runs against a
    real server on a throwaway database gave four different answers: the
    next write fine; the next write lost after waiting out sqlite3's full
    5-second busy timeout (measured 5.53s, then 500); five concurrent writes
    all fine; and six writes over 24 seconds every one of them 500. So the
    honest bound is "at least a lost write and a five-second hang, sometimes
    far more, never nothing that anyone can see" — which is why the two
    assertions here are the mechanism and the missing error_events row,
    both of which held in every run.
    """
    assert crash_probe["open_transactions_after"] == 1, (
        "WRONG — a connection is still open inside a transaction after the request ended"
    )
    assert crash_probe["write_lock_free"] is False, (
        "WRONG — and it is holding the write lock, not merely sitting there"
    )
    assert "locked" in crash_probe["write_lock_error"]
    assert crash_probe["error_events"] == 0, (
        "WRONG — the failure is invisible to the app's own observability feed"
    )
    assert crash_probe["next_write_status"] == 200, (
        "the single-threaded case, pinned so the paragraph above stays checkable — "
        "a 500 here would mean even one worker no longer recovers"
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
