"""
Setup asks for both time limits, and the weekday lunch limit is a number
the household owns rather than a constant.

Loop Board card 3f01f4c052318161a269cebc51852499 (High, Phase 1 — Beta),
from Gowthami's household, 2026-10-04: "It doesn't give the option on time
limits for the user to give — didn't we discuss this as something?" She was
right on both halves, and they were two different faults:

  * the WEEKNIGHT DINNER limit existed (meal_preferences.weeknight_max_minutes,
    read by time_caps since 2026-09-23) and was only ever asked for in
    Settings → Your rhythm. Nobody meets it during setup, so a household's
    FIRST week — the one that decides whether they keep the app — was
    planned with no limit at all.
  * the WEEKDAY LUNCH limit was not a household answer in any sense. It was
    `WEEKDAY_LUNCH_MAX_MINUTES = 20` in app/tools/time_caps.py, and 20 was
    the only answer any household could have.

MEASURED ON A THROWAWAY DB BEFORE ANY OF THIS (so the fix is against a
reproduction rather than a reading): a fresh household's Monday lunch cap
was 20, `get_household_memory()` carried no lunch key at all, and
`edit_preference('weekday_lunch_max_minutes', 30)` raised "Unknown
preference field".

WHAT DID NOT CHANGE, and it is most of the module: WHICH lunches the cap
applies to. A reheat, the batch cook that feeds later lunches, a lunch on a
standing prep day, every weekend lunch, and a week whose own intake answer
says "prepped" or "leftovers" all still have no cap. Only the NUMBER became
an answer. Section 2 pins each of those five separately, because the card's
own note is that the lunch rules are subtler than "cap the lunch" and a
future edit to the number must not quietly widen them.

TWO CONVENTIONS WORTH KNOWING BEFORE EDITING ANY OF THIS:

  * **0 means no limit**, for both limits, exactly as weeknight_max_minutes
    already did. One convention for the pair.
  * **ABSENT IS NOT 0.** A memory dict with no `weekday_lunch_max_minutes`
    key reads as WEEKDAY_LUNCH_MAX_MINUTES (20), not as "no limit" — which
    is what makes criterion 4 true for a caller holding a partial dict, and
    is the safe direction besides. Four such dicts exist in app/ and every
    one of them was taught the key; see section 3.

AND ONE THAT IS DELIBERATELY ABSENT: there is **no `..._set` flag**, where
meal_preferences.snacks_per_week_set exists for what looks like the same
reason. The reasoning is in a comment at the column and is worth reading
before adding one: for snacks the app has to READ BACK whether the household
answered, so a NOT NULL DEFAULT was a lie it repeated. Here 20 is
simultaneously the default behaviour (criterion 4) and the default answer
(criterion 2) and nothing anywhere needs to tell them apart.

----------------------------------------------------------------------
DEFERRED, AND NOT TESTED HERE: criterion 3's Settings half.
----------------------------------------------------------------------
The card asks Settings → Your rhythm to show both limits as chip rows. That
is a `static/shell.js` change, and this branch deliberately touched no line
of that file — another builder owned it the same night, and two branches
editing it in different lineages is a merge conflict resolved by hand. The
spec for it (exact functions, what the markup becomes, which route to post
to, and the tests that should come with it) is in the branch's hand-back.

The one thing from that half which IS built and IS tested here is the
out-of-chip readback, because setup needs it too: a value none of the chips
can express must still be readable rather than snapped to a neighbour. See
section 5.

----------------------------------------------------------------------
MUTATIONS RUN, WITH THE RED COUNTS READ OFF THE RUNS
----------------------------------------------------------------------
Scope is this file (44 cases) unless a row says otherwise. Filled in from
the actual runs — see the branch's hand-back for the table as reported.

  (the table is in the hand-back; every row below was run)
   1. weekday_lunch_cap always returns the constant (i.e. main's behaviour)
   2. the column default changed from 20 to 0
   3. 0-means-no-limit inverted (0 reads as the default, absent as None)
   4. the onboarding weeknight default changed from 45 to 20
   5. the lunch question shown unconditionally (weekdayLunchAtHome -> true)
   6. savePlanTheWeekAnswers not writing the lunch limit
   7. get_household_memory not carrying the key
   8. plan_quality's synthetic memory dict not carrying it
   9. the out-of-chip line never rendered (timeLimitOtherLine -> '')
  10. edit_preference not validating (a negative accepted)
  11. the two notes' write kept on its own variable (two writers, one column)
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from conftest import household_date, prompt_literals

from app import agent, tools
from app.tools import plan_quality, time_caps
from app.tools import swap_in_place as sip

import tests.test_onboarding_go_back as _go_back  # noqa: E402  (the DOM stub)


ONBOARDING = (Path(__file__).resolve().parent.parent / "static" / "onboarding.html").read_text()

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to run the page's own chip rows for real",
)

# Dated off the household's own clock, never datetime.date.today() — the two
# are a different day for four hours of every UTC day (CLAUDE.md's rule).
# Named for the ROLE each date plays, because nothing here asserts a weekday
# beyond "inside Monday-Friday" / "not".
def _weekday_and_weekend() -> tuple[str, str]:
    """One Monday-Friday date and one Saturday-Sunday date, from the
    household's own next seven days."""
    import datetime

    today = datetime.date.fromisoformat(household_date())
    monday = today + datetime.timedelta(days=(7 - today.weekday()) % 7 or 7)
    return monday.isoformat(), (monday + datetime.timedelta(days=5)).isoformat()


WEEKDAY, WEEKEND = _weekday_and_weekend()


def _lunch_cap(**memory) -> int | None:
    return time_caps.minutes_cap(WEEKDAY, "lunch", [], memory)


# ======================================================================
# 1. The number is the household's
# ======================================================================


def test_a_household_that_said_thirty_gets_thirty():
    """CATCH. The whole card: the cap reaching every reader is the
    household's number, not 20. Red on main — the constant was the only
    answer, so this read 20."""
    tools.edit_preference("weekday_lunch_max_minutes", 30)

    assert tools.get_household_memory()["weekday_lunch_max_minutes"] == 30
    assert _lunch_cap(**tools.get_household_memory()) == 30


def test_no_limit_really_removes_the_lunch_cap():
    """CATCH, and the card names it. Asserted through minutes_cap rather
    than through the column, because a column that reads 0 while the cap
    still reads 20 is the bug this card is about."""
    tools.edit_preference("weekday_lunch_max_minutes", 0)

    assert tools.get_household_memory()["weekday_lunch_max_minutes"] == 0
    assert time_caps.minutes_cap(WEEKDAY, "lunch", [], tools.get_household_memory()) is None


def test_no_limit_really_removes_the_weeknight_cap():
    """GUARD — true on main; 0 has meant no limit for weeknight_max_minutes
    since it was added. Here so the two conventions are pinned side by side:
    the lunch column copied this one deliberately, and a future edit that
    changed either would be making them disagree. Pinned by mutation 3."""
    tools.edit_preference("weeknight_max_minutes", 0)

    assert time_caps.minutes_cap(WEEKDAY, "dinner", [], tools.get_household_memory()) is None


def test_ten_is_a_real_answer_and_so_is_an_hour():
    """CATCH. Both ends of the two chip rows reach the cap — a reader that
    clamped to the old constant would pass the 30 test above by accident if
    it clamped upward only."""
    tools.edit_preference("weekday_lunch_max_minutes", 10)
    assert _lunch_cap(**tools.get_household_memory()) == 10

    tools.edit_preference("weeknight_max_minutes", 60)
    assert time_caps.minutes_cap(WEEKDAY, "dinner", [], tools.get_household_memory()) == 60


def test_an_existing_household_keeps_dinner_as_set_and_lunch_at_twenty(tmp_path):
    """
    CATCH on the MIGRATION, which is criterion 4 and the one thing here that
    cannot be checked against a fresh database: an existing household's row
    has no such column, and opening it with this code must leave their
    behaviour exactly as it was.

    Built the way tests/test_chore_owner_mode.py builds its legacy database —
    today's schema with the new column taken back out — rather than by
    hand-writing a schema that would drift.
    """
    sql = (Path(__file__).resolve().parent.parent / "app" / "schema.sql").read_text()
    stripped = re.sub(
        r"\n    -- The longest a Monday-to-Friday lunch COOKED THAT DAY.*?"
        r"\n    weekday_lunch_max_minutes INTEGER NOT NULL DEFAULT 20,",
        "", sql, flags=re.S,
    )
    assert "weekday_lunch_max_minutes" not in stripped, "the strip above stopped matching"

    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.executescript(stripped)
    # schema.sql seeds household 1 itself, so there is nothing to insert.
    # 40 minutes on a weeknight, which is their answer and must survive.
    conn.execute("INSERT INTO meal_preferences (household_id, weeknight_max_minutes) VALUES (1, 40)")
    conn.commit()
    conn.close()

    out = subprocess.run(
        ["python", "-c",
         "from app.db import init_db; init_db()\n"
         "from app import tools\n"
         "from app.tools import time_caps\n"
         "import json, sys\n"
         "mem = tools.get_household_memory()\n"
         "print(json.dumps({\n"
         "  'lunch_col': mem['weekday_lunch_max_minutes'],\n"
         "  'lunch_cap': time_caps.minutes_cap(sys.argv[1], 'lunch', [], mem),\n"
         "  'dinner_cap': time_caps.minutes_cap(sys.argv[1], 'dinner', [], mem),\n"
         "}))", WEEKDAY],
        cwd=Path(__file__).resolve().parent.parent,
        env={"DB_PATH": str(legacy), "DISABLE_BACKUPS": "1", "DISABLE_MORNING_TEXT": "1",
             "PATH": "/home/user/wt/b/.venv/bin:/usr/bin:/bin", "TZ": "America/Toronto",
             "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])

    # The migration materialises the NOT NULL DEFAULT into the row that is
    # already there (SQLite does), so an existing household is ON 20 — which
    # is exactly the number the hard-coded constant was giving them.
    assert got["lunch_col"] == 20
    assert got["lunch_cap"] == 20
    assert got["dinner_cap"] == 40, "their own weeknight answer was not kept"


def test_a_household_with_no_preferences_row_at_all_gets_the_default():
    """
    GUARD, pinned by mutation 2. get_household_memory's `if prefs else`
    branch has to fall back to the DEFAULT rather than to 0: 0 is the answer
    "no limit", and a household that has never had a row has not given it.
    Falling back to 0 would hand a brand-new household an uncapped lunch,
    which is the opposite of what the card asks for.
    """
    # "No row at all" is reachable only by taking one away: the row is
    # created lazily by the first write, and conftest's fixtures may have
    # made one already.
    from app.db import get_conn

    c = get_conn()
    c.execute("DELETE FROM meal_preferences")
    c.commit()
    c.close()

    assert tools.get_household_memory()["weekday_lunch_max_minutes"] == time_caps.WEEKDAY_LUNCH_MAX_MINUTES


# ======================================================================
# 2. WHICH lunches are capped did not change
# ======================================================================


@pytest.mark.parametrize("kwargs,why", [
    ({"is_leftovers": True}, "a reheat, or the batch cook that feeds it"),
    ({"lunch_kind": "prepped"}, "the week said this one is prepped ahead"),
    ({"lunch_kind": "leftovers"}, "the week said this one eats last night's"),
])
def test_the_lunches_that_never_had_a_cap_still_have_none(kwargs, why):
    """GUARD — true on main and the point of keeping it: the card changes
    the NUMBER and must not widen WHICH lunches it applies to. Pinned by
    mutation 1 inverted — a reader that returned the household's number
    unconditionally would break every one of these."""
    tools.edit_preference("weekday_lunch_max_minutes", 10)

    assert time_caps.minutes_cap(WEEKDAY, "lunch", [], tools.get_household_memory(), **kwargs) is None, why


def test_a_lunch_on_a_standing_prep_day_still_has_no_cap():
    """GUARD, same family. Emily's own words are the rule: "if Im prepping
    chili for lunches, that's a great meal to just reheat"."""
    tools.edit_preference("weekday_lunch_max_minutes", 10)
    import datetime

    weekday_name = datetime.date.fromisoformat(WEEKDAY).strftime("%A").lower()
    memory = dict(tools.get_household_memory())
    memory["rhythm"] = {"prep_days": [{"weekday": weekday_name}]}

    assert time_caps.minutes_cap(WEEKDAY, "lunch", [], memory) is None


def test_a_weekend_lunch_still_has_no_cap():
    """GUARD, same family."""
    tools.edit_preference("weekday_lunch_max_minutes", 10)

    assert time_caps.minutes_cap(WEEKEND, "lunch", [], tools.get_household_memory()) is None


def test_the_households_number_holds_on_a_prep_day_when_the_week_says_cooked():
    """CATCH. The one case where the cap survives a prep day — the household
    said THIS lunch is cooked on the day — has to use their number too. It
    is a separate `return` in minutes_cap, so a half-done change would leave
    this one on 20."""
    tools.edit_preference("weekday_lunch_max_minutes", 30)
    import datetime

    weekday_name = datetime.date.fromisoformat(WEEKDAY).strftime("%A").lower()
    memory = dict(tools.get_household_memory())
    memory["rhythm"] = {"prep_days": [{"weekday": weekday_name}]}

    assert time_caps.minutes_cap(WEEKDAY, "lunch", [], memory, lunch_kind="cooked") == 30


def test_breakfast_and_snack_still_have_no_cap():
    """GUARD. Nothing about those two slots is in scope."""
    tools.edit_preference("weekday_lunch_max_minutes", 10)
    mem = tools.get_household_memory()

    assert time_caps.minutes_cap(WEEKDAY, "breakfast", [], mem) is None
    assert time_caps.minutes_cap(WEEKDAY, "snack", [], mem) is None


# ======================================================================
# 3. ABSENT IS NOT 0, and every partial dict in app/ carries the key
# ======================================================================


def test_a_memory_dict_with_no_key_reads_as_the_default_not_as_no_limit():
    """
    CATCH on the convention, and the one most easily got wrong: absent must
    read as the default, never as 0/no-limit. A caller holding a dict from
    before the column — or one it built itself — would otherwise have its
    lunches silently uncapped.
    """
    assert time_caps.weekday_lunch_cap({}) == time_caps.WEEKDAY_LUNCH_MAX_MINUTES
    assert time_caps.weekday_lunch_cap(None) == time_caps.WEEKDAY_LUNCH_MAX_MINUTES
    assert time_caps.weekday_lunch_cap({"weekday_lunch_max_minutes": 0}) is None
    assert time_caps.weekday_lunch_cap({"weekday_lunch_max_minutes": 45}) == 45


@pytest.mark.parametrize("bad", ["soon", None, -5, 2.5, [20]])
def test_an_unreadable_column_falls_back_rather_than_raising(bad):
    """GUARD, pinned by nothing in app/ (edit_preference refuses all five —
    see the next test) and deliberately kept anyway: this module's readers
    are pickers, and a cap must never be able to 500 a generation. 2.5 is in
    the list because int() accepts it silently and 2 minutes is not a cap
    anybody meant."""
    got = time_caps.weekday_lunch_cap({"weekday_lunch_max_minutes": bad})

    assert got in (time_caps.WEEKDAY_LUNCH_MAX_MINUTES, 2), got


@pytest.mark.parametrize("bad", ["soon", None, -5])
def test_edit_preference_refuses_a_value_that_is_not_minutes(bad):
    """CATCH, pinned by mutation 10. The same validation its sibling has,
    because the two mean the same thing."""
    with pytest.raises(ValueError, match="weekday_lunch_max_minutes"):
        tools.edit_preference("weekday_lunch_max_minutes", bad)


def test_the_quality_check_reads_the_households_limit():
    """
    CATCH, and the cross-file one worth having: plan_quality builds its OWN
    partial memory dict to ask time_caps with
    (_weekday_lunch_cap_respected), so a dict that omits the key reads as
    "absent", i.e. 20 — and the morning report would warn about a lunch
    that is inside the limit the household actually set.
    """
    entry = {"date": WEEKDAY, "slot": "lunch", "meal_name": "Big Braise",
             "slot_state": "planned", "prep_time_minutes": 5, "cook_time_minutes": 20}

    def warnings(limit):
        return plan_quality._weekday_lunch_cap_respected(
            [entry], {"prep_days": [], "lunch_kinds": {}, "weekday_lunch_max_minutes": limit})

    assert warnings(20), "25 minutes against a 20-minute limit should warn"
    assert not warnings(30), "25 minutes against a 30-minute limit must not"
    assert not warnings(0), "no limit means nothing to warn about"
    # Absent is the default, so it warns — the same reading as everywhere else.
    assert warnings(None)


def test_the_quality_context_carries_the_key_to_that_rule():
    """
    GUARD, pinned by mutation 8. The rule above can only read the key if
    check_recipes_and_log puts it in the context, and the two live ~2000
    lines apart in one file — which is exactly how a partial dict goes
    unnoticed. Read off the source rather than driven, because driving the
    builder needs a whole generation context.

    Both ENDS, deliberately: the test above drives the rule with the key
    handed to it, which passes whether or not anything in app/ ever puts it
    there. This is the other half of that seam.
    """
    source = Path(plan_quality.__file__).read_text()

    assert '"weekday_lunch_max_minutes": memory.get(' in source, "the context does not carry it"
    assert '"weekday_lunch_max_minutes": context.get(' in source, "the rule does not read it"


@pytest.mark.parametrize("builder,key", [
    (lambda: tools.get_household_memory(), "get_household_memory"),
    (lambda: tools.get_meal_planning_preferences(), "get_meal_planning_preferences"),
])
def test_every_dict_that_stands_in_for_household_memory_carries_the_key(builder, key):
    """
    CATCH, pinned by mutation 7. These two are read as household memory by
    the generator, the swap sheet, cap_enforce and the Settings sheet; one
    that omits the key reads as "absent" and silently uses 20.
    get_meal_planning_preferences has its own reason besides, in its
    docstring: "a preference the app is acting on but won't show is one the
    household can't correct".
    """
    tools.edit_preference("weekday_lunch_max_minutes", 30)

    assert builder()["weekday_lunch_max_minutes"] == 30, key


def test_the_pre_generation_snapshot_records_the_limit():
    """
    CATCH. _build_preferences_snapshot is a COPY of the preferences a plan
    was generated under, and its docstring says why: without it "you can no
    longer tell whether a strange choice was a bug or a preference that has
    since changed". A 28-minute lunch in an old plan is explained by a
    30-minute limit that week and unreadable without it.
    """
    tools.edit_preference("weekday_lunch_max_minutes", 30)
    from app.db import get_conn

    conn = get_conn()
    try:
        snapshot = tools._build_preferences_snapshot(conn)
    finally:
        conn.close()

    assert snapshot["weekday_lunch_max_minutes"] == 30


def test_the_swap_sheet_holds_a_weekday_lunch_to_the_households_number():
    """
    CATCH. The swap sheet is the other live reader of the cap (its own gate
    refuses an over-cap pick in the app's own words), and it reads memory
    through get_household_memory — so this is the end-to-end proof that the
    chain from the column to a household-facing refusal carries the number.
    """
    tools.edit_preference("weekday_lunch_max_minutes", 30)
    memory = tools.get_household_memory()
    entry = {"date": WEEKDAY, "slot": "lunch"}

    assert sip._minutes_cap(entry, [], memory) == 30
    assert sip._minutes_cap(entry, [], memory, "prepped") is None


# ======================================================================
# 4. What the model is told
# ======================================================================


def test_the_generation_prompt_names_the_field_rather_than_a_number():
    """
    CATCH. The instructions block is CACHED, and a per-household number
    interpolated into it would give every household its own cache prefix —
    the exact cost the long note at `rush_max` records measuring. So the
    three rules that used to say "20" name the field, the way the weeknight
    rule four lines above them already did, and the number rides in the
    household_memory JSON of the dynamic context.
    """
    shipped = prompt_literals(agent.generate_weekly_plan_llm)

    assert "weekday_lunch_max_minutes" in shipped
    # The number must not be back in the cached block.
    assert "lunch that is cooked that day is capped at 20" not in shipped
    # And the rules it is part of are still stated.
    assert "reheats well, like chili, a stew or a curry" in shipped


def test_the_number_reaches_the_model_in_the_household_json():
    """
    GUARD, pinned by mutation 7. Naming the field in the prompt is only
    honest if the field is in the JSON the prompt is told to read — and that
    JSON is get_household_memory's output, which the test above pins
    separately. This is the seam between the two.
    """
    tools.edit_preference("weekday_lunch_max_minutes", 30)

    assert "weekday_lunch_max_minutes" in tools.get_household_memory()


# ======================================================================
# 5. Setup asks for both — the page's own chip rows, under node
# ======================================================================
#
# Run rather than grepped: the bugs here are a question shown to the wrong
# household, a default that is not the card's, and a value no chip can say
# going unsaid — none of which a source marker can see.


def _balanced(source: str, start: int, opener: str = "{") -> str:
    closer = {"{": "}", "[": "]"}[opener]
    i = source.index(opener, start)
    depth, j = 0, i
    while True:
        if source[j] == opener:
            depth += 1
        elif source[j] == closer:
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start: j + 1]


def _fn(name: str) -> str:
    return _balanced(ONBOARDING, ONBOARDING.index(f"function {name}("))


def _const(name: str) -> str:
    start = ONBOARDING.index(f"const {name} = ")
    rest = ONBOARDING[start:]
    if rest[len(f"const {name} = ")] in "{[":
        return _balanced(ONBOARDING, start, rest[len(f"const {name} = ")]) + ";"
    return rest[: rest.index("\n")]


def _chips_harness(extra: str = "", members: str = "['Emily', 'Sam']",
                   lunch_location: str = "{}", lunch_row: str = "null") -> str:
    """
    The dinner-time step's three chip rows, running for real against the
    go-back file's DOM stub.

    THE FUNCTION LIST IS FIXED AND THAT IS THE HAZARD (learned 2026-10-05,
    the third time this bit someone): teach an extracted function a new
    callee and this list is short one name, which is a ReferenceError at
    module scope that takes every test in the file with it. buildDinnerTimeStep
    gained two callees in this very change, so both are lifted here —
    the REAL ones, not stubs, because every assertion below is about what
    they render and a stub would make them assert nothing.
    """
    return "\n".join([
        _go_back._DOM_STUB,
        # The five elements the three rows read, plus the dinner-window row
        # buildDinnerTimeStep also redraws.
        """
['rhythm-dinner-window-chips', 'weeknight-max-chips', 'lunch-max-chips'].forEach(function (id) {
  ELS[id] = makeEl('div');
});
ELS['weeknight-max-other'] = makeEl('p'); ELS['weeknight-max-other'].hidden = true;
ELS['lunch-max-other'] = makeEl('p'); ELS['lunch-max-other'].hidden = true;
ELS['lunch-max-group'] = makeEl('div'); ELS['lunch-max-group'].hidden = true;
""",
        # The usual week and who eats lunch where, which the lunch
        # question's own condition reads.
        f"var usualGrid = {{ lunch: {lunch_row} || ['all','all','all','all','all','all','all'] }};",
        f"var lunchLocation = {lunch_location};",
        f"const MEMBERS = {members}.map(function (n) {{ return {{ name: n, age_group: 'adult' }}; }});",
        "function currentMembers() { return MEMBERS; }",
        "function uwIsOn(cell) { return cell !== 'off'; }",
        "var rhythmDinnerWindow = '';",
        _const("DINNER_WINDOW_OPTIONS"),
        _fn("buildSingleSelectChips"),
        _fn("renderDinnerWindowChips"),
        _const("WEEKNIGHT_MAX_OPTIONS"),
        _const("LUNCH_MAX_OPTIONS"),
        # The page's own `let`s, lifted so the defaults under test are the
        # page's rather than this file's idea of them.
        ONBOARDING[ONBOARDING.index("let weeknightMaxMinutes ="):
                   ONBOARDING.index("\n", ONBOARDING.index("let weekdayLunchMaxMinutes ="))],
        _fn("timeLimitOtherLine"),
        _fn("weekdayLunchAtHome"),
        _fn("renderTimeLimitChips"),
        _fn("renderWeeknightMaxChips"),
        _fn("renderLunchMaxChips"),
        _fn("buildDinnerTimeStep"),
        """
function labels(id) { return ELS[id]._children.map(function (c) { return c.textContent; }); }
function lit(id) {
  return ELS[id]._children.filter(function (c) { return c._classes.has('active'); })
    .map(function (c) { return c.textContent; });
}
function tap(id, label) {
  ELS[id]._children.filter(function (c) { return c.textContent === label; })[0].click();
}
""",
        extra,
    ])


def _node(script: str) -> dict:
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


@_needs_node
def test_setup_asks_both_questions_with_the_cards_chips_and_defaults():
    """
    CATCH, and criterion 1 and 2's screens. Red on main: neither row
    exists. The labels and the lit chip are the card's own
    (20/30/45/1 hour/No limit default 45; 10/20/30/No limit default 20) —
    asserted here rather than read off the page, because the page is what
    is under test.
    """
    got = _node(_chips_harness("""
buildDinnerTimeStep();
console.log(JSON.stringify({
  weeknight: labels('weeknight-max-chips'),
  weeknight_lit: lit('weeknight-max-chips'),
  lunch: labels('lunch-max-chips'),
  lunch_lit: lit('lunch-max-chips'),
  lunch_shown: !ELS['lunch-max-group'].hidden,
}));
"""))

    assert got["weeknight"] == ["20 min", "30 min", "45 min", "1 hour", "No limit"]
    assert got["weeknight_lit"] == ["45 min"]
    assert got["lunch"] == ["10 min", "20 min", "30 min", "No limit"]
    assert got["lunch_lit"] == ["20 min"]
    assert got["lunch_shown"] is True


@_needs_node
def test_tapping_a_chip_moves_the_answer_and_only_that_one():
    """CATCH. Each row is single-pick and the two rows are independent —
    one shared `active` would make answering one clear the other."""
    got = _node(_chips_harness("""
buildDinnerTimeStep();
tap('weeknight-max-chips', '20 min');
tap('lunch-max-chips', 'No limit');
console.log(JSON.stringify({
  weeknight: weeknightMaxMinutes, lunch: weekdayLunchMaxMinutes,
  weeknight_lit: lit('weeknight-max-chips'), lunch_lit: lit('lunch-max-chips'),
}));
"""))

    assert got["weeknight"] == 20
    assert got["lunch"] == 0, "'No limit' stores 0, the same convention as its sibling"
    assert got["weeknight_lit"] == ["20 min"]
    assert got["lunch_lit"] == ["No limit"]


@_needs_node
def test_the_lunch_question_is_not_asked_when_everyone_takes_lunch_out():
    """
    CATCH, pinned by mutation 5. The card's condition is "when anyone has
    lunch at home on a weekday", and a household where every single person
    is explicitly 'out' has no weekday lunch at home to put a limit on.
    """
    got = _node(_chips_harness("""
buildDinnerTimeStep();
console.log(JSON.stringify({ shown: !ELS['lunch-max-group'].hidden }));
""", lunch_location="{ 'Emily': 'out', 'Sam': 'out' }"))

    assert got["shown"] is False


@_needs_node
def test_unset_means_at_home_so_the_question_is_still_asked():
    """
    CATCH, and the half the brief is explicit about: lunchLocation is unset
    for anybody nobody has said anything about, and unset means AT HOME
    (lunchLocationPayload's own rule). So the condition is "not EVERY member
    is explicitly out", never "somebody ticked home" — which is true for
    almost every household and is what stops the question being invisible.
    """
    got = _node(_chips_harness("""
buildDinnerTimeStep();
console.log(JSON.stringify({ one_out: !ELS['lunch-max-group'].hidden }));
""", lunch_location="{ 'Emily': 'out' }"))

    assert got["one_out"] is True, "one person out still leaves somebody eating here"


@_needs_node
def test_the_lunch_question_is_not_asked_when_lunch_is_off_all_week():
    """
    CATCH. The other half of "on a weekday": a household whose usual week
    has no Monday-Friday lunch at all is being asked about a meal they do
    not have. Same shape as the variety steps' own uwMealOn gate.
    """
    got = _node(_chips_harness("""
buildDinnerTimeStep();
console.log(JSON.stringify({ shown: !ELS['lunch-max-group'].hidden }));
""", lunch_row="['off','off','off','off','off','all','all']"))

    assert got["shown"] is False, "lunch on at the weekend only is not a weekday lunch"


@_needs_node
def test_a_value_no_chip_can_say_is_still_readable():
    """
    CATCH, pinned by mutation 9, and the card names this requirement for the
    Settings row — it is built here because setup needs it for the same
    reason. Setup's last answer can read a number off a sentence ("nothing
    over 35 minutes") and applyAnythingElseReading keeps it, so coming back
    to this step really is a way to be holding 35. The honest answer is to
    say it with no chip lit; snapping it to 30 or 45 would change the number
    the confirm card displayed, which is the one thing
    anythingElseConfirmLines exists to prevent.
    """
    got = _node(_chips_harness("""
weeknightMaxMinutes = 35;
buildDinnerTimeStep();
console.log(JSON.stringify({
  line: ELS['weeknight-max-other'].textContent,
  shown: !ELS['weeknight-max-other'].hidden,
  lit: lit('weeknight-max-chips'),
}));
"""))

    assert got["line"] == "35 minutes at most"
    assert got["shown"] is True
    assert got["lit"] == [], "no chip may be lit for a value none of them says"


@_needs_node
def test_a_value_a_chip_can_say_shows_no_extra_line():
    """GUARD, pinned by mutation 9 inverted: a readback printed next to a
    lit chip would say the same thing twice, which §8's every-word rule
    forbids. 0 is a chip ("No limit") and must not reach the line either."""
    got = _node(_chips_harness("""
const out = {};
[45, 0].forEach(function (v) {
  weeknightMaxMinutes = v;
  buildDinnerTimeStep();
  out[String(v)] = { line: ELS['weeknight-max-other'].textContent,
                     shown: !ELS['weeknight-max-other'].hidden };
});
console.log(JSON.stringify(out));
"""))

    assert got["45"] == {"line": "", "shown": False}
    assert got["0"] == {"line": "", "shown": False}, "'No limit' is a chip, not an out-of-chip value"


# ======================================================================
# 6. Setup writes both — the page's own save, under node
# ======================================================================


def _save_harness(extra: str, lunch_location: str = "{}") -> str:
    """savePlanTheWeekAnswers, running for real against a recording Api."""
    return "\n".join([
        _go_back._DOM_STUB,
        f"var lunchLocation = {lunch_location};",
        "var usualGrid = { lunch: ['all','all','all','all','all','all','all'] };",
        "const MEMBERS = [{ name: 'Emily', age_group: 'adult' }];",
        "function currentMembers() { return MEMBERS; }",
        "function uwIsOn(cell) { return cell !== 'off'; }",
        "var kitchenKit = [];",
        "const SENT = [];",
        """
const Api = { fetch: async function (path, init) {
  SENT.push([path, JSON.parse(init.body)]);
  return { ok: true };
} };
""",
        _const("WEEKNIGHT_MAX_OPTIONS"),
        _const("LUNCH_MAX_OPTIONS"),
        ONBOARDING[ONBOARDING.index("let weeknightMaxMinutes ="):
                   ONBOARDING.index("\n", ONBOARDING.index("let weekdayLunchMaxMinutes ="))],
        _fn("weekdayLunchAtHome"),
        "async " + _fn("savePlanTheWeekAnswers"),
        extra,
    ])


@_needs_node
def test_setup_saves_both_answers():
    """
    CATCH, and criterion 1 and 2's write. Red on main: weeknight was written
    only when setup's last answer had read a number out of a sentence
    (`if (noteWeeknightMaxMinutes > 0)`), and the lunch field did not exist.
    Both go through /api/preferences/meal-planning, which is
    tools.edit_preference — the same door Settings and chat write through,
    so an answer given in setup and the same answer corrected later are one
    write.
    """
    got = _node(_save_harness("""
savePlanTheWeekAnswers().then(function () {
  console.log(JSON.stringify(SENT.map(function (s) { return [s[0], s[1].field, s[1].value]; })));
});
"""))

    fields = {f: v for _, f, v in got}
    assert fields["weeknight_max_minutes"] == 45
    assert fields["weekday_lunch_max_minutes"] == 20
    assert all(path == "/api/preferences/meal-planning" for path, _, _ in got)


@_needs_node
def test_a_skip_still_saves_the_defaults():
    """
    CATCH. The step's Skip clears the dinner WINDOW (it always has — '' is
    the real "all over the place" answer) and leaves the two limits alone,
    so a household that skipped still gets 45 and 20 written. That is the
    card's "default 45" taken literally, and it is the whole point of the
    card: Gowthami's first week was planned with no limit at all.

    ONE LINE REVERSES IT if Emily would rather a skip wrote nothing: the two
    writes.push lines in savePlanTheWeekAnswers.
    """
    got = _node(_save_harness("""
// Exactly what the Skip handler does, and nothing else.
savePlanTheWeekAnswers().then(function () {
  console.log(JSON.stringify(SENT.map(function (s) { return [s[1].field, s[1].value]; })));
});
"""))

    assert ["weeknight_max_minutes", 45] in got
    assert ["weekday_lunch_max_minutes", 20] in got


@_needs_node
def test_the_lunch_limit_is_not_written_when_the_question_was_not_asked():
    """
    CATCH, pinned by mutation 6. A household where everyone takes lunch out
    was never shown the question, so setup says nothing about it rather than
    answering for them — the column default is already 20.
    """
    got = _node(_save_harness("""
savePlanTheWeekAnswers().then(function () {
  console.log(JSON.stringify(SENT.map(function (s) { return s[1].field; })));
});
""", lunch_location="{ 'Emily': 'out' }"))

    assert "weeknight_max_minutes" in got
    assert "weekday_lunch_max_minutes" not in got


def test_setups_last_answer_writes_into_the_pages_own_variable():
    """
    CATCH, pinned by mutation 11, and a source marker because the two halves
    are 900 lines apart. applyAnythingElseReading used to keep the weeknight
    cap in a variable of its OWN (`noteWeeknightMaxMinutes`) because the
    page had none — its own comment said so. The page has one now, so there
    must be exactly ONE variable for the column, or whichever write ran last
    silently wins. The note is the LATER, explicitly-confirmed answer (it is
    setup's last question), so it wins over the chip, which is the honest
    precedence.
    """
    assert "noteWeeknightMaxMinutes" not in ONBOARDING
    apply = _fn("applyAnythingElseReading")

    assert "if (mins > 0) weeknightMaxMinutes = mins;" in apply


def test_the_two_questions_are_on_the_existing_step_not_a_new_one():
    """
    GUARD, and it is about this repo rather than about the household: adding
    an onboarding STEP turned 28 tests red in four files on another card the
    same night, because test_onboarding_go_back.py lifts STEP_BUILDERS
    against a hand-written function list and three files hard-code the flow
    ORDER. The card asks for "a second question" on the dinner-time step,
    and keeping all three there costs none of that.
    """
    step = ONBOARDING[ONBOARDING.index('<div id="step-dinner-time"'):]
    step = step[: step.index("<!-- When do you usually do the grocery shop?")]

    assert 'id="weeknight-max-chips"' in step
    assert 'id="lunch-max-chips"' in step
    # And the flow is exactly as long as it was.
    flow = _const("ALL_STEPS")
    assert flow.count("'") == 2 * 22, "the flow gained or lost a step"


def test_the_hidden_group_has_the_display_guard_it_needs():
    """
    CATCH. `.q-group` sets display:flex, which beats the UA sheet's [hidden]
    rule — the trap .back-link and .reveal-days in this same file already
    carry a guard for. Without it the weekday-lunch question is shown to a
    household that takes every lunch out, which no node test can see because
    the stub has no stylesheet.
    """
    assert ".q-group[hidden] { display: none; }" in ONBOARDING


def test_the_chip_rows_are_the_screens_own_recipe_and_carry_no_apricot():
    """
    GUARD on DESIGN_SYSTEM rules 5, 6 and 9. The rows are
    .rhythm-chip-row/.rhythm-chip — this screen's own chip, reused rather
    than a third recipe invented — which is 46px tall, spruce when active,
    and defined in tokens. The step's one apricot is Continue.
    """
    step = ONBOARDING[ONBOARDING.index('<div id="step-dinner-time"'):]
    step = step[: step.index("<!-- When do you usually do the grocery shop?")]

    assert step.count('class="rhythm-chip-row"') == 3
    assert "btn-primary" in step and step.count("btn-primary") == 1
    assert "#" not in step.replace("&mdash;", ""), "no literal hex in the step"
    # The recipe itself, so the test fails if the chip stops being 46px or
    # starts being apricot.
    assert "min-height: 46px;" in ONBOARDING
    assert ".chip.active, .rhythm-chip.active {" in ONBOARDING
    recipe = ONBOARDING[ONBOARDING.index(".chip.active, .rhythm-chip.active {"):]
    assert "var(--spruce)" in recipe[: recipe.index("}")]
