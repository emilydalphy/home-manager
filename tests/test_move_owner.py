"""
Every move has an owner (slice 1) — app/tools/move_owner.py, the `owner` /
`owner_name` keys on a move, and the quiet name Today and Cook put on a row.

Loop Board, Emily 2026-09-30: *as an adult in the household, I want each
cook, prep and shop on Today to show whose it is, so that we both know
without asking.*

The card's own instruction is the shape of this file: **extend the existing
source, don't add a second "who cooks" source.** `cooking_role` (the third
of the six locked rhythm questions) has been the answer since 2026-09-03 and
`cooker._cook_name` has read its `one_person` half onto Cook's Tonight card
since 2026-09-13; move_owner.py is that one reader widened to the other two
values, and `_cook_name` now delegates to it in one line.

Three groups of tests, and what each is evidence OF:

1. THE DEFAULTS — one_person / turns / whoever_free, plus the empty cases.
   These are the catches: every one of them fails against main, where the
   keys do not exist at all.
2. NOTHING IS CLAIMED THAT WASN'T SAID — a household with no answer, or
   with whoever_free, renders exactly what it rendered before. Proved by
   running the real renderers on the same payload with and without the new
   keys and comparing the HTML byte for byte, rather than by asserting it.
3. THE COST — the rhythm is read once for a whole Today payload, the same
   once it cost before this existed, and the members table at most once.
   A GUARD, and it is the one this branch could most easily have got wrong:
   get_household_rhythm opens a connection, so a per-move read would be a
   connection per row (CLAUDE.md records that mistake being made twice).
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import move_owner as _move_owner
from app.tools import moves as _moves
from conftest import household_today


REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node runs the screen's own builders"
)

TODAY = household_today()
WEEK_START = TODAY - datetime.timedelta(days=2)
DAYS = [(WEEK_START + datetime.timedelta(days=i)).isoformat() for i in range(5)]
ISO_TODAY = TODAY.isoformat()


# ---------- seeding ----------

def _adults(*names):
    for n in names:
        tools.add_member(n)
        tools.set_member_age_group(n, "adult")


def _recipe(name="Chicken Skewers", prep=10, cook=25):
    tools.add_recipe(
        name,
        ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
        prep_time_minutes=prep,
        cook_time_minutes=cook,
        default_servings=3,
    )


def _plan(days=DAYS, dish="Chicken Skewers") -> int:
    plan_id = tools.create_weekly_plan(WEEK_START.isoformat())["weekly_plan_id"]
    for d in days:
        tools.plan_meal(d, dish, slot="dinner", weekly_plan_id=plan_id)
    return plan_id


def _entry(day: str, slot: str = "dinner") -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


def _prep_row(plan_id: int, day: str, entry_id: int | None, kind: str = "defrost",
              description: str = "Move the chicken thighs to the fridge — for Thursday’s skewers.") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
        "related_meal, status, task_type, meal_plan_entry_id) "
        "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
        (tools.household_id(), plan_id, day, description, "Chicken Skewers", kind, entry_id),
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def _at(hour: int = 9) -> datetime.datetime:
    return datetime.datetime.combine(TODAY, datetime.time(hour))


def _cooks_by_day() -> list[tuple[str, int | None, str | None]]:
    """(day, owner id, owner name) for every cook move across the seeded days."""
    out = []
    for d in DAYS:
        for m in _moves.moves_for_day(d, now=_at()):
            if m["kind"] == "cook":
                out.append((d, m["owner"], m["owner_name"]))
    return out


# ---------- 1. the three defaults ----------

def test_one_person_names_them_on_every_cook():
    """CATCH. "Emily cooks" is a household-level answer, so it is true of
    every night — the same name cooker._cook_name has put on Cook's Tonight
    card since 2026-09-13, now on the move as well."""
    _adults("Emily", "Vineeth")
    _recipe()
    _plan()
    tools.set_cooking_role("one_person", who="Emily")

    owners = _cooks_by_day()
    assert [name for _, _, name in owners] == ["Emily"] * len(DAYS)
    # The id rides along only because Emily is genuinely on record: slice 3
    # will credit a tick with it, and a guessed id credits the wrong person.
    assert {mid for _, mid, _ in owners} == {tools.list_members()[0]["id"]}


def test_turns_alternates_cook_nights_in_the_order_setup_named_them():
    """
    CATCH, and the branch's one real judgement call (move_owner._turns_owner):
    "we take turns" records WHO but no ORDER and no ANCHOR, so both are
    picked in code — the adults as setup named them (members.id), and the
    plan's first cook DAY as the first adult's.
    """
    _adults("Emily", "Vineeth")
    _recipe()
    _plan()
    tools.set_cooking_role("turns")

    assert [name for _, _, name in _cooks_by_day()] == [
        "Emily", "Vineeth", "Emily", "Vineeth", "Emily"
    ]


def test_whoever_free_leaves_every_move_unowned():
    """CATCH. The household said it isn't fixed; naming somebody would be
    inventing an answer they deliberately didn't give."""
    _adults("Emily", "Vineeth")
    _recipe()
    _plan()
    tools.set_cooking_role("whoever_free")

    assert [(mid, name) for _, mid, name in _cooks_by_day()] == [(None, None)] * len(DAYS)


def test_a_household_that_has_never_been_asked_owns_nothing():
    """CATCH. An unanswered question is `whoever_free` in the one way that
    matters here: nothing is claimed. This is what makes the feature safe to
    ship to every household at once."""
    _adults("Emily", "Vineeth")
    _recipe()
    _plan()

    assert tools.get_household_rhythm()["cooking_role"] is None
    assert [(mid, name) for _, mid, name in _cooks_by_day()] == [(None, None)] * len(DAYS)


# ---------- the edges of "turns" ----------

def test_turns_with_one_adult_is_that_adult_every_night():
    """A rotation of one. Not a special case in the code and not one here:
    `% 1` is 0, so every cook day is theirs, which is the truthful answer."""
    _adults("Emily")
    _recipe()
    _plan()
    tools.set_cooking_role("turns")

    assert [name for _, _, name in _cooks_by_day()] == ["Emily"] * len(DAYS)


def test_turns_with_three_adults_rotates_through_all_three():
    """Three or more rotate in the same order, one cook day each — the rule
    is `% len(adults)` and nothing about it assumes two."""
    _adults("Emily", "Vineeth", "Rae")
    _recipe()
    _plan()
    tools.set_cooking_role("turns")

    assert [name for _, _, name in _cooks_by_day()] == [
        "Emily", "Vineeth", "Rae", "Emily", "Vineeth"
    ]


def test_turns_with_nobody_on_record_names_nobody():
    """A household mid-onboarding can answer the rhythm before a single
    member is saved (add_member is a later step than the rhythm chips on
    some paths, and a script can do either). Nobody to rotate between is
    nobody, never a crash and never a guess."""
    _recipe()
    _plan()
    tools.set_cooking_role("turns")

    assert [(mid, name) for _, mid, name in _cooks_by_day()] == [(None, None)] * len(DAYS)


def test_only_adults_are_in_the_rotation():
    """GUARD on _adults' own filter. A child is in the house and is not in
    the cooking rota; a member with no age group on record isn't either,
    because nothing said they were an adult."""
    _adults("Emily", "Vineeth")
    tools.add_member("Reid")
    tools.set_member_age_group("Reid", "child")
    tools.add_member("Sam")  # no age group at all
    _recipe()
    _plan()
    tools.set_cooking_role("turns")

    assert {name for _, _, name in _cooks_by_day()} == {"Emily", "Vineeth"}


def test_the_turns_rotation_does_not_move_when_a_meal_is_ticked_cooked():
    """
    CATCH on the one thing this must not get wrong: "do NOT make it depend
    on anything that changes between two reads of the same week". A tick, a
    start and a re-read all leave the rotation exactly where it was, because
    it is counted off the plan's cook DAYS and a cooked meal is still a cook.
    """
    _adults("Emily", "Vineeth")
    _recipe()
    _plan()
    tools.set_cooking_role("turns")
    before = _cooks_by_day()

    tools.check_off_meal(_entry(DAYS[0]), "done")
    tools.check_off_meal(_entry(DAYS[1]), "done")

    assert _cooks_by_day() == before


def test_the_turns_rotation_does_not_move_when_the_dish_is_swapped():
    """Same rule, the other everyday change: swapping Tuesday's dinner is a
    change to WHAT is cooked, never to whose night it is."""
    _adults("Emily", "Vineeth")
    _recipe()
    _recipe("Bean Chili")
    plan_id = _plan()
    tools.set_cooking_role("turns")
    before = _cooks_by_day()

    tools.swap_meal_in_plan(plan_id, DAYS[1], "Bean Chili", slot="dinner")

    assert _cooks_by_day() == before


def test_a_one_person_answer_naming_nobody_on_record_still_says_the_name():
    """
    The answer is free text (rhythm.set_cooking_role takes a name, not a
    member), so it can be somebody who isn't a member — a partner who
    doesn't use the app, a teenager. The NAME is what the screen says; the
    id stays None, because crediting a tick to a guessed member is worse
    than crediting it to nobody.
    """
    _adults("Emily")
    _recipe()
    _plan()
    tools.set_cooking_role("one_person", who="Vineeth")

    assert [(mid, name) for _, mid, name in _cooks_by_day()] == [(None, "Vineeth")] * len(DAYS)


def test_a_blank_one_person_answer_names_nobody():
    """GUARD. set_cooking_role refuses a blank `who` for one_person, but a
    row written before that guard, or by hand, reads as no answer rather
    than as an empty name on every row."""
    _adults("Emily")
    _recipe()
    _plan()
    conn = get_conn()
    conn.execute(
        "INSERT INTO household_rhythm (household_id, member_name, weekday, fact_type, value, who) "
        "VALUES (?, '', '', 'cooking_role', 'one_person', '')",
        (tools.household_id(),),
    )
    conn.commit()
    conn.close()

    assert [(mid, name) for _, mid, name in _cooks_by_day()] == [(None, None)] * len(DAYS)


# ---------- which kinds of move carry a name ----------

def test_a_fridge_move_belongs_to_whoever_cooks_the_meal_it_is_for():
    """
    CATCH, and the judgement call the card asked to be written down: a thaw
    is filed under the meal it feeds everywhere else in this app
    (defrost._describe writes "for Thursday's skewers"; cookFocusPrepTasks
    shows it on that meal's own screen), so reading that link is not a
    guess. Under `turns` that is the useful half — the chicken you move
    today is for the night somebody ELSE is cooking.
    """
    _adults("Emily", "Vineeth")
    _recipe()
    plan_id = _plan()
    tools.set_cooking_role("turns")
    # DAYS[1] is Vineeth's night; the move to get it out of the freezer is
    # on DAYS[0], which is Emily's.
    task_id = _prep_row(plan_id, DAYS[0], _entry(DAYS[1]))

    moves = {m["id"]: m for m in _moves.moves_for_day(DAYS[0], now=_at())}
    assert moves[f"fridge:{task_id}"]["owner_name"] == "Vineeth"
    assert moves[f"cook:{_entry(DAYS[0])}"]["owner_name"] == "Emily"


def test_a_prep_task_that_names_no_meal_belongs_to_nobody():
    """"Soak the beans", with no meal_plan_entry_id behind it, is a task
    nothing links to a cook. Matching it to a dish by NAME would be a guess,
    and this module doesn't make any."""
    _adults("Emily")
    _recipe()
    plan_id = _plan()
    tools.set_cooking_role("one_person", who="Emily")
    task_id = _prep_row(plan_id, DAYS[0], None, kind="general", description="Soak the beans.")

    moves = {m["id"]: m for m in _moves.moves_for_day(DAYS[0], now=_at())}
    assert moves[f"prep:{task_id}"]["owner_name"] is None


def test_a_reheat_carries_no_name_even_when_one_person_cooks():
    """
    Nothing is cooked on a reheat night, and this whole app is careful about
    that (moves.py never features one, cooker never scales one, time_caps
    exempts one). "Emily's cooking" over a plate being warmed is a thing
    that isn't true, and inventing a second verb for it would be inventing a
    fact nobody stated.
    """
    _adults("Emily")
    _recipe("Egg White Bites", prep=10, cook=20)
    plan_id = tools.create_weekly_plan(WEEK_START.isoformat())["weekly_plan_id"]
    tools.plan_meal(DAYS[0], "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    tools.plan_meal(DAYS[1], "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    tools.set_cook_ahead(_entry(DAYS[0], "breakfast"), [_entry(DAYS[1], "breakfast")])
    tools.set_cooking_role("one_person", who="Emily")

    moves = {m["kind"]: m for m in _moves.moves_for_day(DAYS[1], now=_at())}
    assert moves["reheat"]["owner_name"] is None
    # The cook that made them IS Emily's — the reheat's silence is about the
    # reheat, not about the household having no answer.
    cook = {m["kind"]: m for m in _moves.moves_for_day(DAYS[0], now=_at())}["cook"]
    assert cook["owner_name"] == "Emily"


def test_a_reheat_night_does_not_use_up_a_turn():
    """
    CATCH on the half of "a reheat is not a cook" that the stamping pass
    cannot cover. _stamp_owners never asks about a reheat at all (it
    branches on kind), so the reheat clause in move_owner._is_a_cook is
    load-bearing for exactly one thing: the rotation's cook DAYS. Nobody
    cooks on a reheat night, so it must not consume anybody's turn.

    Found by running the mutation rather than by reading: widening
    _is_a_cook to every dated meal left the reheat test green, because it
    only ever measured the silence the stamp already guarantees.
    """
    _adults("Emily", "Vineeth")
    _recipe()
    _recipe("Egg White Bites", prep=10, cook=20)
    plan_id = tools.create_weekly_plan(WEEK_START.isoformat())["weekly_plan_id"]
    # A cook on day 0, the same dish reheated on day 1, cooks on 2 and 3.
    tools.plan_meal(DAYS[0], "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    tools.plan_meal(DAYS[1], "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    tools.set_cook_ahead(_entry(DAYS[0], "breakfast"), [_entry(DAYS[1], "breakfast")])
    for d in (DAYS[2], DAYS[3]):
        tools.plan_meal(d, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    tools.set_cooking_role("turns")

    def cook_on(day):
        for m in _moves.moves_for_day(day, now=_at()):
            if m["kind"] == "cook":
                return m["owner_name"]
        return None

    # Day 1 is nobody's turn — nothing is cooked on it — so day 2 is the
    # rotation's SECOND cook day, not its third.
    assert cook_on(DAYS[0]) == "Emily"
    assert cook_on(DAYS[1]) is None, "the reheat is not a cook"
    assert cook_on(DAYS[2]) == "Vineeth"
    assert cook_on(DAYS[3]) == "Emily"


def test_the_shop_carries_no_name_because_this_app_has_no_answer_for_it():
    """
    REPORTED AS WELL AS TESTED, because it looks like an oversight and is
    not: there is no "who shops" answer anywhere in this app. household_rhythm
    holds seven fact types and none of them asks; meal_preferences has no such
    column; members has none. Guessing that whoever COOKS also shops would be
    exactly the traditional assumption rhythm.py's own docstring refuses.
    """
    _adults("Emily")
    _recipe()
    _plan()
    tools.set_cooking_role("one_person", who="Emily")
    tools.add_grocery_item("Milk", "1")

    moves = {m["kind"]: m for m in _moves.moves_for_day(ISO_TODAY, now=_at())}
    assert moves["shop"]["owner_name"] is None
    assert moves["shop"]["owner"] is None


def test_no_rhythm_fact_type_asks_who_shops():
    """
    The other half of the claim above, stated where it can go stale: if a
    "who shops" answer is ever added, this goes red and the shop's owner
    should stop being None. Read off the rhythm's own payload rather than a
    grep, so a new fact type shows up here the day it lands.
    """
    _adults("Emily")
    keys = set(tools.get_household_rhythm())
    assert not any("shop" in k for k in keys), sorted(keys)


def test_every_move_carries_both_keys_whether_or_not_anybody_owns_it():
    """GUARD. "Can carry an owner" means the keys are always there — a
    reader never has to know whether a payload predates them."""
    _adults("Emily")
    _recipe()
    plan_id = _plan()
    _prep_row(plan_id, ISO_TODAY, None, kind="general", description="Soak the beans.")
    tools.add_grocery_item("Milk", "1")

    moves = _moves.moves_for_day(ISO_TODAY, now=_at())
    assert len(moves) >= 3
    for m in moves:
        assert "owner" in m and "owner_name" in m, m["kind"]


# ---------- the existing source, extended rather than doubled ----------

def test_cook_name_still_answers_and_is_now_one_reader_not_two():
    """
    The card's own instruction. cooker._cook_name is a one-line delegation
    now, so Cook's Tonight card and Today's rows cannot drift about what
    `cooking_role` means.
    """
    _adults("Emily")
    tools.set_cooking_role("one_person", who="Emily")
    assert _cooker._cook_name() == "Emily"
    assert tools.get_cooker_view()["cook_name"] == "Emily"

    tools.set_cooking_role("turns")
    # Still one_person only, and deliberately: cook_name is a HOUSEHOLD-level
    # answer on the view, and under turns there isn't one — the name is per
    # night and rides on the move instead.
    assert _cooker._cook_name() is None

    src = (REPO / "app" / "tools" / "cooker.py").read_text(encoding="utf-8")
    body = src[src.index("def _cook_name()"):]
    assert "_move_owner.one_person_cook_name()" in body
    assert "get_household_rhythm" not in body, (
        "cooking_role has one reader (move_owner.py) — this is the second one back"
    )


# ---------- 3. the cost ----------

def _reads(monkeypatch, fn) -> tuple[int, int]:
    """
    (reads of the rhythm made by moves.py, reads of the members table made
    by move_owner) for one call of `fn`.

    Targeted rather than a raw connection count: a whole Today payload opens
    about seventy connections and most of them belong to get_cooker_view, so
    a total would drown the thing under test. The total is measured too —
    see the test below this one.
    """
    rhythm_calls, adult_calls = [], []
    real_rhythm = _moves._household_rhythm
    real_adults = _move_owner._adults

    monkeypatch.setattr(
        _moves, "_household_rhythm",
        lambda *a, **k: (rhythm_calls.append(1), real_rhythm(*a, **k))[1],
    )
    monkeypatch.setattr(
        _move_owner, "_adults",
        lambda *a, **k: (adult_calls.append(1), real_adults(*a, **k))[1],
    )
    fn()
    monkeypatch.setattr(_moves, "_household_rhythm", real_rhythm)
    monkeypatch.setattr(_move_owner, "_adults", real_adults)
    return len(rhythm_calls), len(adult_calls)


def _connections(fn) -> int:
    """
    Every connection opened anywhere under `fn`, counted at sqlite3.connect
    rather than at any module's `get_conn`.

    Not fussiness: move_owner does `from ..db import get_conn` at module
    scope, so a module-level patch of app.db.get_conn would not see its
    reads at all — the trap the 2026-09-11 approve-race work records finding
    a stray connection through.
    """
    import sqlite3
    real = sqlite3.connect
    n = []
    sqlite3.connect = lambda *a, **k: (n.append(1), real(*a, **k))[1]
    try:
        fn()
    finally:
        sqlite3.connect = real
    return len(n)


def test_a_whole_today_payload_reads_the_rhythm_once_however_many_moves_it_holds(monkeypatch):
    """
    GUARD on the cost, and the one this branch could most easily have got
    wrong. get_household_rhythm opens a connection, so resolving whose a
    move is PER MOVE would be a connection per row — the mistake CLAUDE.md
    records being made twice over the household clock.

    ONE read of the rhythm for a whole Today payload, and it is the one
    moves_for_day was already making for the dinner clock: today_moves reads
    it once and threads it into both of its calls (today's and, when nothing
    is featured, tomorrow's) alongside `view` and the resolver.

    The `busy == quiet` line is what makes this a test rather than a
    tautology: `<= 1` alone would be green at zero, which is main.
    """
    _adults("Emily", "Vineeth")
    _recipe()
    plan_id = _plan()
    _prep_row(plan_id, ISO_TODAY, _entry(ISO_TODAY))
    tools.add_grocery_item("Milk", "1")
    tools.set_cooking_role("turns")

    quiet = _reads(monkeypatch, lambda: tools.today_moves(now=_at()))

    for slot in ("breakfast", "lunch", "snack"):
        tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot=slot, weekly_plan_id=plan_id)
    busy = _reads(monkeypatch, lambda: tools.today_moves(now=_at()))

    assert len(tools.today_moves(now=_at())["moves"]) >= 6, "the day really did get busier"
    # The number does not grow with the day. That is the whole claim.
    assert busy == quiet == (1, 1)


def test_an_answer_that_claims_nobody_never_reads_the_members_table(monkeypatch):
    """
    GUARD, and where the honest cost line is: `whoever_free` and a household
    that has never been asked claim nobody, so neither needs to know who the
    adults are and neither spends a query finding out. That is every
    household this ships to on day one.

    `one_person` DOES read it, deliberately: its `who` is free text, so
    matching it against the adults on record is the only way the move can
    carry the member id slice 3 will credit a tick with. One read per Today
    payload, never per move.
    """
    _adults("Emily")
    _recipe()
    _plan()

    tools.set_cooking_role("whoever_free")
    assert _reads(monkeypatch, lambda: tools.today_moves(now=_at())) == (1, 0)

    conn = get_conn()
    conn.execute("DELETE FROM household_rhythm WHERE household_id = ?", (tools.household_id(),))
    conn.commit()
    conn.close()
    assert _reads(monkeypatch, lambda: tools.today_moves(now=_at())) == (1, 0)

    tools.set_cooking_role("one_person", who="Emily")
    assert _reads(monkeypatch, lambda: tools.today_moves(now=_at())) == (1, 1)


def test_whose_move_it_is_costs_one_connection_and_only_when_somebody_owns_something():
    """
    GUARD, measured end to end at sqlite3.connect over a whole Today payload
    — the number Emily would notice, rather than the one under test.

    Measured 2026-09-30 against origin/main on the same seed, a busy day of
    five moves: main 71; this branch 71 unanswered, 71 whoever_free, 72
    one_person, 72 turns. The one extra connection is move_owner._adults,
    read once per payload.

    On a QUIET day main is 60 and this branch 59 for the two answers that
    claim nobody — the rhythm threading paying for itself, since today_moves
    used to read the rhythm again for tomorrow's moves and now hands its own
    down.
    """
    _adults("Emily", "Vineeth")
    _recipe()
    _plan()
    tools.add_grocery_item("Milk", "1")

    tools.set_cooking_role("whoever_free")
    claims_nobody = _connections(lambda: tools.today_moves(now=_at()))

    tools.set_cooking_role("turns")
    named = _connections(lambda: tools.today_moves(now=_at()))

    assert named - claims_nobody == 1, (named, claims_nobody)


def test_the_moves_are_untouched_when_the_rhythm_cannot_be_read(monkeypatch):
    """
    GUARD. A name is a nicety; the moves are the point. Anything raising
    while working out whose a move is leaves every move unowned and the day
    otherwise exactly as it was — the same stance _household_now takes
    towards a clock it can't read.
    """
    _adults("Emily", "Vineeth")
    _recipe()
    _plan()
    tools.set_cooking_role("turns")
    good = tools.today_moves(now=_at())["moves"]

    def _boom(*a, **k):
        raise RuntimeError("no rhythm today")

    monkeypatch.setattr(_move_owner, "_adults", _boom)
    broken = tools.today_moves(now=_at())["moves"]

    assert [m["owner_name"] for m in broken] == [None] * len(broken)
    assert any(m["owner_name"] for m in good), "the control really did name somebody"
    for a, b in zip(good, broken):
        assert {k: v for k, v in a.items() if k not in ("owner", "owner_name")} == \
               {k: v for k, v in b.items() if k not in ("owner", "owner_name")}


def test_the_morning_text_is_untouched():
    """
    GUARD, and a scope line worth having in writing: the name goes on Today
    and on Cook (the card's own criterion), and the move's `detail` — which
    is what digest.build_morning_text reads — is byte-identical either way.
    Texting somebody their own name at seven in the morning is not what this
    slice is for.
    """
    _adults("Emily", "Vineeth")
    _recipe()
    _plan()
    before = [m["detail"] for m in _moves.moves_for_day(ISO_TODAY, now=_at())]
    tools.set_cooking_role("one_person", who="Emily")
    after = [m["detail"] for m in _moves.moves_for_day(ISO_TODAY, now=_at())]
    assert before == after
    # And `meta` too — the client composes the line, so the server's one
    # clock-free line is the same string for everybody.
    assert all(m["meta"] for m in _moves.moves_for_day(ISO_TODAY, now=_at()) if m["kind"] == "cook")


# ---------- 2. the screen ----------

def _slice(start: str, end: str) -> str:
    a = SHELL_JS.index(start)
    return SHELL_JS[a:SHELL_JS.index(end, a)]


def _fn(name: str) -> str:
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
    return SHELL_JS[start:j + 1]


_TODAY_PRELUDE = (
    "function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;')"
    ".replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
    + _slice("  var TICK_ICON =", "  var DOTS_ICON =")
    + _slice("  // ---------- Today: Shop and Cook ----------", "  function tomorrowCardHtml(")
    + _fn("moveRecipeTarget") + "\n"
    + _fn("moveDishHtml") + "\n"
    + "function openRecipeFor() {}\n"
)


def _node(script: str):
    res = nodeharness.run_node(_TODAY_PRELUDE + script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


def _move(kind, id_, title, **extra):
    m = {
        "id": id_, "kind": kind, "title": title, "detail": kind + " · line",
        "reason": "", "date": ISO_TODAY, "slot": None,
        "window_start": ISO_TODAY + "T09:00:00", "window_end": ISO_TODAY + "T11:00:00",
        "weight": 2, "action": {"label": "Done", "target": {}},
        "done": False, "tickable": True, "overdue": False, "entry_id": None, "task_id": None,
        "duration_min": 0, "time_label": "", "meta": "", "chips": [],
        "owner": None, "owner_name": None,
    }
    m.update(extra)
    return m


@_needs_node
def test_todays_cook_row_says_whose_it_is_in_the_rows_own_meta_line():
    """
    CATCH, and it runs the screen's own builder rather than reading the
    source for a marker: the name has to actually appear, in the row's one
    clock-free line, in that line's own ink.
    """
    move = _move("cook", "cook:1", "Chicken Skewers", meta="35 min",
                 owner=2, owner_name="Vineeth")
    html = _node("console.log(JSON.stringify(dayStripNodeHtml(%s, 'later')));" % json.dumps(move))

    assert "Vineeth’s cooking · 35 min" in html
    # In the meta line, not in a badge, a chip or an element of its own.
    meta = re.search(r'<span class="day-node-meta">([^<]*)</span>', html).group(1)
    assert meta == "Vineeth’s cooking · 35 min"
    assert "owner" not in html and "unassigned" not in html.lower()


@_needs_node
def test_a_cook_already_done_says_whose_it_was_not_that_they_are_cooking():
    """
    CATCH. "Emily\u2019s cooking" under a strikethrough on a dinner already
    eaten is a thing that isn't true (\u00a78). A done cook takes the plain
    possessive — whose it was — and deliberately not a past tense, since
    who actually ticked it is slice 3's question and not a word's.
    """
    move = _move("cook", "cook:1", "Chicken Skewers", meta="35 min", done=True,
                 owner=1, owner_name="Emily")
    html = _node("console.log(JSON.stringify(dayStripNodeHtml(%s, 'done')));" % json.dumps(move))
    assert "Emily\u2019s \u00b7 35 min" in html
    assert "cooking" not in html


@_needs_node
def test_a_fridge_move_says_only_whose_it_is_because_it_is_not_cooking():
    """The wording is by kind: a cook says what they are doing, every other
    move says only whose it is. "Emily's cooking" over moving a bag of
    chicken to the fridge is a thing that isn't true (§8)."""
    move = _move("fridge", "fridge:4", "Move the chicken to the fridge",
                 meta="for Thursday’s skewers", reason="for Thursday’s skewers",
                 owner=1, owner_name="Emily")
    html = _node("console.log(JSON.stringify(dayStripNodeHtml(%s, 'later')));" % json.dumps(move))

    assert "Emily’s · for Thursday’s skewers" in html
    assert "cooking" not in html


@_needs_node
def test_the_tinted_row_does_not_say_the_reason_twice():
    """
    CATCH on the one thing the meta-line change could quietly have broken. A
    fridge move's reason IS its meta line, and the "now" row prints the
    reason again only when the line doesn't already carry it — a test that
    used to read "does the line START with it". With a name leading the line
    it no longer does, so `!== 0` would have printed it twice.
    """
    move = _move("fridge", "fridge:4", "Move the chicken to the fridge",
                 meta="for Thursday’s skewers", reason="for Thursday’s skewers",
                 owner=1, owner_name="Emily")
    html = _node("console.log(JSON.stringify(dayStripNodeHtml(%s, 'now')));" % json.dumps(move))

    assert html.count("for Thursday’s skewers") == 1, html


@_needs_node
def test_a_move_with_nobody_on_it_says_nothing_at_all():
    """CATCH. No badge, no "unassigned", no empty separator — the row is the
    row it was."""
    with_keys = _move("cook", "cook:1", "Chicken Skewers", meta="35 min")
    without = {k: v for k, v in with_keys.items() if k not in ("owner", "owner_name")}
    a, b = _node(
        "console.log(JSON.stringify([dayStripNodeHtml(%s,'later'), dayStripNodeHtml(%s,'later')]));"
        % (json.dumps(with_keys), json.dumps(without))
    )
    assert a == b
    assert '<span class="day-node-meta">35 min</span>' in a
    assert "·" not in re.search(r'<span class="day-node-meta">([^<]*)</span>', a).group(1)


@_needs_node
def test_a_whole_unowned_day_renders_byte_for_byte_what_it_rendered_before():
    """
    CATCH, and the proof the card asked for rather than the assertion: a
    household with whoever_free, or with no answer at all, must render
    exactly what main renders. Driven over a whole day of every kind of
    move — the same payload with the new keys set to None and with them
    absent entirely, which is main's shape — and compared byte for byte.
    """
    day = [
        _move("shop", "shop:x", "Shop for tonight", meta="orzo, salmon", tickable=False,
              stops=[{"store": "Costco", "count": 6, "items": ["orzo", "salmon"]}], timed=True,
              weight=3, action={"label": "Go shopping", "target": {"tab": "grocery"}}),
        _move("fridge", "fridge:4", "Move the chicken to the fridge",
              meta="for Thursday’s skewers", reason="for Thursday’s skewers"),
        _move("prep", "prep:5", "Soak the beans", meta="prep"),
        _move("reheat", "reheat:11", "Egg White Bites", meta="made ahead Sunday · reheat",
              done=True, entry_id=11, weight=1),
        _move("cook", "cook:12", "Chicken Skewers", meta="35 min", entry_id=12, weight=3,
              reason="Cooking for 6 — covers Thursday"),
    ]
    stripped = [{k: v for k, v in m.items() if k not in ("owner", "owner_name")} for m in day]
    script = (
        "function render(ms){ return ms.map(function(m, i){ "
        "return dayStripNodeHtml(m, i === 4 ? 'now' : (m.done ? 'done' : 'later')); }).join(''); }\n"
        "console.log(JSON.stringify([render(%s), render(%s)]));"
        % (json.dumps(day), json.dumps(stripped))
    )
    with_keys, without_keys = _node(script)
    assert with_keys == without_keys
    assert "day-node" in with_keys


@_needs_node
def test_the_wording_lives_in_one_constant():
    """
    Emily's to change in one line. Both forms come out of MOVE_OWNER_WORDS
    and nothing else builds a name into words — a second copy somewhere
    would let Today and Cook say the same fact two different ways.
    """
    out = _node(
        "console.log(JSON.stringify({"
        "cook: moveOwnerClause({kind:'cook', owner_name:'Emily'}),"
        "prep: moveOwnerClause({kind:'prep', owner_name:'Emily'}),"
        "none: moveOwnerClause({kind:'cook', owner_name:null}),"
        "keys: Object.keys(MOVE_OWNER_WORDS)}));"
    )
    assert out == {"cook": "Emily’s cooking", "prep": "Emily’s", "none": "",
                   "keys": ["cook", "other"]}
    # One place. The Cook side reaches for this same function rather than
    # keeping a second copy of the words (cookOwnerPrefix).
    assert SHELL_JS.count("var MOVE_OWNER_WORDS = {") == 1
    assert SHELL_JS.count("’s cooking'") == 1, "a second copy of the wording"


def test_the_name_is_not_an_accent_and_has_no_class_of_its_own():
    """
    DESIGN, hard rule 5 and the card's own word "quietly": the name rides in
    the row's existing meta line, so it takes that line's ink and cannot
    become a second apricot, a badge or a chip. Checked as source because
    what is being asserted is the ABSENCE of a new class.
    """
    css = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
    for invented in ("day-node-owner", "cook-owner", "move-owner", "owner-badge"):
        assert invented not in css and invented not in SHELL_JS, invented
    clause = _fn("moveOwnerClause")
    assert "<" not in clause and "class=" not in clause, (
        "the wording builds words, never markup — the row's own line carries it"
    )


# ---------- Cook ----------

_COOK_PRELUDE = (
    "function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;')"
    ".replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
    "const COOK_ICONS = { check: '<svg></svg>' };\n"
    "const REHEAT_ACTION_LABEL = 'Mark eaten';\nconst REHEAT_UNDO_LABEL = 'Mark not eaten';\n"
    "var cookState = { tonightIdx: null };\n"
    "function dayName(d, o){ return 'Monday'; }\n"
    "function clockLabel(m){ return String(m); }\n"
    "function isoClockMinutes(){ return null; }\n"
    "function cookFocusPrepTasks(){ return []; }\n"
    "function emptyMomentHtml(){ return '<p>empty</p>'; }\n"
    "function kitchenNextCookLine(){ return ''; }\n"
    + _slice("  var MOVE_OWNER_WORDS = {", "  // The two groups' own icons")
    + _fn("cookOwnerPrefix") + "\n"
    + _fn("cookPreppedAhead") + "\n"
    + _fn("kitchenTodayLine") + "\n"
    + _fn("kitchenTodayRows") + "\n"
    + _fn("kitchenTodayRowHtml") + "\n"
    + _fn("cookTonightRow") + "\n"
    + _fn("cookTonightEyebrow") + "\n"
    + _fn("cookTonightNote") + "\n"
    + _fn("cookThawTitle") + "\n"
    + _fn("cookThawFor") + "\n"
    + _fn("cookThawDoneLine") + "\n"
    + _fn("cookTonightTimes") + "\n"
    + _fn("cookStartedLine") + "\n"
    + _fn("cookTonightCardHtml") + "\n"
    + _fn("kitchenCookingTodayHtml") + "\n"
)


def _cook_node(script: str):
    res = nodeharness.run_node(_COOK_PRELUDE + script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


def _cook_meal(entry_id, meal, slot="dinner", **extra):
    m = {"entry_id": entry_id, "date": ISO_TODAY, "slot": slot, "meal": meal,
         "cooked_status": "pending", "is_leftovers": False, "prep_time_minutes": 10,
         "cook_time_minutes": 25, "advance_prep_notes": ""}
    m.update(extra)
    return m


def _cook_card(meals, moves, data):
    return _cook_node(
        "var meals = %s;\nvar rows = kitchenTodayRows(meals, %s, %s);\n"
        "console.log(JSON.stringify(kitchenCookingTodayHtml(rows, meals, %s, %s)));"
        % (json.dumps(meals), json.dumps(moves), json.dumps(ISO_TODAY),
           json.dumps(ISO_TODAY), json.dumps(data))
    )


@_needs_node
def test_cooks_tonight_card_names_the_nights_cook_when_the_household_takes_turns():
    """
    CATCH. cook_name is a HOUSEHOLD-level answer and is null under `turns`,
    so on main this card names nobody on a week where the two of them
    alternate. The move knows whose night it is, and the card reads it.
    """
    meals = [_cook_meal(12, "Chicken Skewers")]
    move = _move("cook", "cook:12", "Chicken Skewers", entry_id=12, meta="35 min",
                 owner=2, owner_name="Vineeth", chips=["35 min", "Start by 5:55"],
                 time_label="6:30 tonight")
    html = _cook_card(meals, [move], {"cook_name": None})
    assert "MONDAY · TONIGHT · VINEETH" in html


@_needs_node
def test_cooks_tonight_card_still_names_the_standing_cook_with_no_move_behind_it():
    """GUARD. The card has read data.cook_name since 2026-09-13 and still
    does when there is no move for the meal (a day the moves engine has not
    caught up with) — this widens it, it does not replace it."""
    meals = [_cook_meal(12, "Chicken Skewers")]
    html = _cook_card(meals, [], {"cook_name": "Emily"})
    assert "MONDAY · TONIGHT · EMILY" in html


@_needs_node
def test_cooks_other_rows_today_lead_with_the_same_words_today_uses():
    """CATCH. A day with more than one meal keeps the others as rows under
    the card, and those say whose they are in the same wording — one
    constant, so the two screens cannot name the same cook two ways."""
    meals = [_cook_meal(12, "Chicken Skewers"), _cook_meal(13, "Chopped Salad", slot="lunch")]
    moves = [
        _move("cook", "cook:12", "Chicken Skewers", entry_id=12, owner=1, owner_name="Emily",
              chips=["35 min", "Start by 5:55"]),
        _move("cook", "cook:13", "Chopped Salad", entry_id=13, owner=2, owner_name="Vineeth",
              chips=["15 min", "Start by 11:45"]),
    ]
    html = _cook_card(meals, moves, {"cook_name": None})
    assert "Vineeth’s cooking · start by 11:45" in html


@_needs_node
def test_cooks_rows_with_nobody_on_them_are_byte_for_byte_what_they_were():
    """CATCH. The same proof as Today's, on Cook: the keys set to None and
    the keys absent render identically."""
    meals = [_cook_meal(12, "Chicken Skewers"), _cook_meal(13, "Chopped Salad", slot="lunch")]
    moves = [
        _move("cook", "cook:12", "Chicken Skewers", entry_id=12, chips=["35 min", "Start by 5:55"],
              time_label="6:30 tonight"),
        _move("cook", "cook:13", "Chopped Salad", entry_id=13, chips=["15 min"]),
    ]
    stripped = [{k: v for k, v in m.items() if k not in ("owner", "owner_name")} for m in moves]
    a = _cook_card(meals, moves, {"cook_name": None})
    b = _cook_card(meals, stripped, {"cook_name": None})
    assert a == b


# ---------- end to end ----------

def test_the_api_payload_carries_the_name_a_turns_household_would_see(client, signed_in):
    """
    Over HTTP, through the route the screen actually calls: the owner is on
    the move by the time it reaches the browser, and it is the night's own.
    """
    _adults("Emily", "Vineeth")
    _recipe()
    _plan()
    tools.set_cooking_role("turns")

    payload = client.get("/api/today/moves").json()
    cooks = [m for m in payload["moves"] if m["kind"] == "cook"]
    assert cooks, payload
    assert all(m["owner_name"] in ("Emily", "Vineeth") for m in cooks)
