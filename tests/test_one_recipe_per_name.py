"""
One household, one recipe per name.

FOUND 2026-09-18 overnight, by driving the app on a throwaway database
rather than from a report: `tools.add_recipe` was a bare INSERT with no
name check, so saving a recipe under a name the household already had
wrote a SECOND row — and every lookup that resolves a recipe by name is a
`fetchone()` with no `ORDER BY`, i.e. a table scan in rowid order, so the
OLDER row won every read and the new one was reachable by nothing. The
app said "Saved" and nothing was usable.

Four callers each carried their own guard and they disagreed: the import
route answered 409, `swap_in_place` and `big_meal` skipped silently, week
generation compared names case-SENSITIVELY, and the chat tool — the one
door a person reaches by talking — checked nothing at all.

Most of this file is RED against `app/` on 277d854. The ones that are not
say so in their own docstring and name the mutation that pins them.
"""
from datetime import date

import pytest

from app import agent, tools
from app.db import get_conn
from conftest import agent_function_source, household_today


CHICKEN = [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat-seafood"}]
BEEF = [{"item": "Beef mince", "qty": "1 lb", "category": "meat-seafood"}]


def _recipe_rows():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, name, ingredients_json FROM recipes ORDER BY id"
    )]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# The reported bug
# ---------------------------------------------------------------------------

def test_saving_a_name_the_household_already_has_is_refused():
    """CATCH. On 277d854 this wrote a second row and returned a new id."""
    tools.add_recipe("Chicken Tacos", CHICKEN, instructions=["Sear the thighs."])
    with pytest.raises(tools.DuplicateRecipeName):
        tools.add_recipe("Chicken Tacos", BEEF, instructions=["Brown the mince."])
    assert len(_recipe_rows()) == 1


@pytest.mark.parametrize("second", ["chicken tacos", "CHICKEN TACOS", "  Chicken Tacos  "])
def test_a_name_that_differs_only_in_case_or_padding_is_the_same_name(second):
    """
    CATCH. The capitalisation a household types is not a different dish,
    and most of this package's own lookups already read it that way.
    """
    tools.add_recipe("Chicken Tacos", CHICKEN)
    with pytest.raises(tools.DuplicateRecipeName):
        tools.add_recipe(second, BEEF)
    assert len(_recipe_rows()) == 1


def test_a_refused_save_writes_nothing_at_all():
    """
    CATCH. The whole failure was a write nobody could reach; a refusal
    that still touched the row it refused for would be its own version of
    the same problem.
    """
    tools.add_recipe("Chicken Tacos", CHICKEN, instructions=["Sear the thighs."])
    before = _recipe_rows()
    with pytest.raises(tools.DuplicateRecipeName):
        tools.add_recipe("Chicken Tacos", BEEF, instructions=["Brown the mince."])
    assert _recipe_rows() == before


def test_the_refusal_is_a_sentence_naming_the_recipe_already_on_file():
    """
    CATCH. The chat door's whole job here is to say something true — an
    unexplained failure would be "Noted" noting nothing, one layer down.
    It names the recipe AS SAVED, not as typed, so a household who wrote
    'chicken tacos' is told which dish they already have.
    """
    tools.add_recipe("Chicken Tacos", CHICKEN)
    with pytest.raises(tools.DuplicateRecipeName) as caught:
        tools.add_recipe("chicken tacos", BEEF)
    said = str(caught.value)
    assert "Chicken Tacos" in said
    assert "chicken tacos" not in said.replace("Chicken Tacos", "")
    assert "change the name" in said.lower()


def test_a_genuinely_new_name_still_saves():
    """
    GUARD, green either way — the point is that the refusal is narrow.
    Pinned by mutation: make `existing_recipe_named` match on anything
    (e.g. drop its WHERE clause) and this fails.
    """
    first = tools.add_recipe("Chicken Tacos", CHICKEN)
    second = tools.add_recipe("Beef Tacos", BEEF)
    assert second["recipe_id"] != first["recipe_id"]
    assert [r["name"] for r in _recipe_rows()] == ["Chicken Tacos", "Beef Tacos"]


def test_another_households_recipe_never_blocks_this_one():
    """
    GUARD, green either way. The rule is household-scoped, and this log
    records three separate cross-household leaks, so the invariant is
    pinned rather than assumed. Pinned by mutation: drop `household_id`
    from `existing_recipe_named`'s WHERE and this fails.
    """
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO households (id, name) VALUES (2, 'Beta')")
    conn.commit()
    conn.close()
    with tools.use_household(2):
        tools.add_recipe("Chicken Tacos", CHICKEN)
    saved = tools.add_recipe("Chicken Tacos", BEEF)
    assert saved["recipe_id"], "another family's recipe is not this family's"


# ---------------------------------------------------------------------------
# The other half: a name that differs only in case must still land on the
# recipe, or the refusal above would trade a duplicate for a freeform night
# ---------------------------------------------------------------------------

def test_planning_by_a_differently_cased_name_still_finds_the_recipe():
    """
    CATCH. `plan_meal` resolved `name = ?`, case-SENSITIVELY, and got away
    with it only because `add_recipe` used to write a second row for the
    second spelling. With that refused, a miss here would land the night
    FREEFORM — no recipe on Cook, and nothing on the shopping list at
    approval.
    """
    tools.add_recipe("Chicken Tacos", CHICKEN)
    today = household_today().isoformat()
    plan = tools.create_weekly_plan(today, day_count=7)
    tools.plan_meal(today, "chicken tacos", slot="dinner",
                    weekly_plan_id=plan["weekly_plan_id"])

    conn = get_conn()
    row = conn.execute(
        "SELECT recipe_id, freeform_meal FROM meal_plan_entries"
    ).fetchone()
    conn.close()
    assert row["recipe_id"] is not None, "a capital letter must not cost the recipe"
    assert row["freeform_meal"] is None


# ---------------------------------------------------------------------------
# The four doors, now reading one rule
# ---------------------------------------------------------------------------

def test_the_import_route_still_answers_409_with_the_same_sentence(signed_in):
    """
    GUARD on the 409, green either way — that door already guarded itself.

    The assertion is against a LITERAL, not against
    `duplicate_recipe_message(...)`: the route calls that function now, so
    comparing its output to its own output is an assertion nothing can
    fail. Found by review — gutting the message to "Nope." left all
    thirteen tests green. Same shape as the source marker satisfied by its
    own comment, two commits ago, which is why the literal is spelled out
    here even though it duplicates the one in the app.
    """
    tools.add_recipe("Chicken Tacos", CHICKEN)
    res = signed_in.post("/api/recipes/add", json={
        "name": "chicken tacos",
        "ingredients": [{"item": "Beef mince", "qty": "1 lb", "category": "meat-seafood"}],
        "instructions": ["Brown the mince."],
    })
    assert res.status_code == 409
    assert res.json()["detail"] == (
        "You already have a recipe called \u201cChicken Tacos\u201d — change the name to keep both."
    )
    # ...and the chat door says the same words, from the same function.
    with pytest.raises(tools.DuplicateRecipeName) as caught:
        tools.add_recipe("CHICKEN TACOS", BEEF)
    assert str(caught.value) == res.json()["detail"]
    assert len(_recipe_rows()) == 1


def test_generation_no_longer_writes_a_second_row_for_a_second_spelling(monkeypatch):
    """
    CATCH. `_ensure_recipe_saved` compared `r["name"] == meal_name`, so a
    model that answered `chicken tacos` beside a saved `Chicken Tacos`
    made a duplicate during an ORDINARY week generation — no unusual
    behaviour from the household at all. On 277d854 this ends with two
    rows; it must end with one, and the night must still be planned.
    """
    tools.add_recipe("Chicken Tacos", CHICKEN, instructions=["Sear the thighs."])
    day = household_today().isoformat()

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: [{
        "date": day, "slot": "dinner", "meal_name": "chicken tacos",
        "is_new_recipe": True, "ingredients": BEEF,
        "instructions": ["Brown the mince."], "food_groups": [],
    }])
    agent.generate_weekly_plan(day)

    rows = _recipe_rows()
    assert [r["name"] for r in rows] == ["Chicken Tacos"], (
        "a capitalisation the model chose must not fork the recipe"
    )
    conn = get_conn()
    entry = conn.execute(
        "SELECT recipe_id, freeform_meal FROM meal_plan_entries WHERE slot = 'dinner'"
    ).fetchone()
    conn.close()
    assert entry is not None and entry["recipe_id"] == rows[0]["id"]


def test_a_reused_name_inside_the_app_still_skips_rather_than_raising():
    """
    GUARD, green either way. `swap_in_place` and `big_meal` mean "we
    already have this one, carry on" — they must keep skipping quietly
    rather than inheriting the chat door's refusal, or an ordinary swap
    onto a dish the household already has would blow up.

    BOTH are driven, because the first version of this test drove only
    `swap_in_place` while its docstring claimed either one was pinned —
    review removed big_meal's guard entirely and all 5683 tests stayed
    green. Pinned by mutation: make either call `add_recipe`
    unconditionally and this fails.
    """
    from app.tools import big_meal as _big_meal
    from app.tools import swap_in_place as _swap

    saved = tools.add_recipe("Chicken Tacos", CHICKEN)
    _swap._save_recipe_if_new(
        {"meal_name": "chicken tacos", "ingredients": BEEF}, serves=4
    )  # must not raise
    assert len(_recipe_rows()) == 1

    reused = _big_meal._recipe_for_main(
        {"name": "CHICKEN TACOS", "ingredients": BEEF, "food_groups": [],
         "cuisine": "", "main_protein": "", "instructions": [],
         "default_servings": 8, "prep_minutes": 10, "cook_minutes": 20},
        "Thanksgiving",
    )
    assert reused == saved["recipe_id"], "the holiday main reuses the saved recipe"
    assert len(_recipe_rows()) == 1


def test_the_three_inside_doors_ask_the_one_rule():
    """
    GUARD on the rule staying in ONE place, green either way — pinned by
    mutation: put any of the three old hand-written comparisons back and
    this fails.

    Source-level on purpose: this is a claim about WHERE the rule lives,
    which no behavioural test can see. Four doors each wrote their own
    answer and they disagreed about case; a fifth should call
    `existing_recipe_named` rather than grow a fifth.

    Checked and deliberately NOT included, so nobody reports them as a
    miss: `holidays._recipe_named` and `big_meal.set_big_meal_dish`'s
    main lookup also resolve a recipe by name case-insensitively, but
    neither guards a SAVE — they answer "do we have one of these", and
    big_meal's needs the whole row (ingredients, steps, clocks), which
    this helper deliberately does not return. Same family, different
    question, their own change if anyone wants one helper for both.
    """
    import inspect

    from app.tools import big_meal as _big_meal
    from app.tools import swap_in_place as _swap

    def code_only(src):
        # Comment-stripped, because the comment BESIDE the agent's call
        # names the helper too — so the first version of this assertion
        # passed with the call removed, satisfied by its own prose. That
        # is the one mistake this repo's log keeps having to unpick.
        return "\n".join(
            line for line in src.splitlines() if not line.lstrip().startswith("#")
        )

    for label, src in (
        ("swap_in_place._save_recipe_if_new", inspect.getsource(_swap._save_recipe_if_new)),
        ("big_meal._recipe_for_main", inspect.getsource(_big_meal._recipe_for_main)),
        ("agent's _ensure_recipe_saved", agent_function_source("_generate_weekly_plan")),
    ):
        assert "existing_recipe_named(" in code_only(src), (
            f"{label} should ask tools.existing_recipe_named, not compare names itself"
        )


# ---------------------------------------------------------------------------
# What an independent review found this branch had got wrong (2026-09-18)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda: tools.mark_recipe_feedback("chicken tacos", "liked"),
    lambda: tools.log_recipe_note("chicken tacos", "a bit runny"),
    lambda: tools.log_cooking_deviation("chicken tacos", "used beef"),
    lambda: tools.flag_recipe_temporary("chicken tacos"),
])
def test_the_sibling_chat_tools_find_the_recipe_whatever_the_capitalisation(call):
    """
    CATCH against this branch's own first commit, and the regression that
    commit introduced.

    These four resolved `name = ?`, case-sensitively. On main that was
    survivable: the model passing the household's own casing raised "No
    recipe named 'chicken tacos'. Save it first with add_recipe." — and
    add_recipe then SAVED a second row, so the rating landed and was
    readable. With add_recipe refusing, the same sequence became a dead
    end: the error string tells the model to call the one door that now
    says no, and the like is lost.

    Reachable by design, not by accident: SYSTEM_PROMPT says to call
    mark_recipe_feedback "right away with the recipe name ... Don't wait
    to be asked", so the model passing "we loved the chicken tacos" as
    typed is the expected behaviour.
    """
    tools.add_recipe("Chicken Tacos", CHICKEN, instructions=["Sear the thighs."])
    call()  # must not raise
    assert len(_recipe_rows()) == 1


def test_a_recipe_is_stored_under_a_trimmed_name():
    """
    CATCH against this branch's own first commit. The rule trimmed the
    QUERY and the INSERT stored the name as typed, so a recipe saved with
    a stray space was invisible to the rule and the reported bug came
    straight back through the other door — the test file's own padding
    case only ever exercised the direction that worked.
    """
    tools.add_recipe("  Bean Chili  ", CHICKEN)
    assert [r["name"] for r in _recipe_rows()] == ["Bean Chili"]
    with pytest.raises(tools.DuplicateRecipeName):
        tools.add_recipe("Bean Chili", BEEF)
    assert len(_recipe_rows()) == 1


def test_two_saves_at_the_same_instant_still_leave_one_recipe():
    """
    CATCH against this branch's own first commit, measured rather than
    reasoned: the check read on one connection and the INSERT wrote on
    another, so review measured 6 of 20 simultaneous pairs still writing a
    duplicate — better than main's 20 of 20, and not the invariant this
    branch claims. `/api/recipes/add` is a sync def, so Starlette runs it
    in a threadpool and two devices really are concurrent.

    A real `threading.Barrier`, no monkeypatched ordering, following
    `tests/test_approve_race.py`.
    """
    import threading

    tools.add_member("Emily")
    # Twelve pairs, not one: a single pair is a coin flip on whether the
    # two threads land inside the window, so against this branch's first
    # commit one trial passed while review measured 6 duplicates in 20.
    # A race test that only sometimes reproduces the race is a test that
    # only sometimes means anything.
    for trial in range(12):
        conn = get_conn()
        conn.execute("DELETE FROM recipes")
        conn.commit()
        conn.close()

        ready = threading.Barrier(2)
        saved, refused = [], []

        def save():
            ready.wait()
            try:
                saved.append(tools.add_recipe("Chicken Tacos", CHICKEN)["recipe_id"])
            except Exception as e:  # DuplicateRecipeName, or a busy database
                refused.append(type(e).__name__)

        threads = [threading.Thread(target=save) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(_recipe_rows()) == 1, (
            f"trial {trial}: one of the two had to lose — "
            f"saved {saved}, refused {refused}"
        )
