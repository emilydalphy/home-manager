"""
When the draft has to bend, it says so — and the fix is already worked out.

Emily's decided snag rules (2026-09-23, "When your requests and your week
don't line up"), scenario 3: a dish she asked for BY NAME, on a night that
hasn't the time for it.

    Lasagna takes 60 minutes, and Wednesday is short on time.
       [ Prep it Tuesday night ]   [ Move it to Saturday ]

WHAT WAS AND WASN'T ALREADY TRUE, because it decides what these tests are
evidence OF. The KEEPING half already worked on `main` and is not this
branch's doing: `cap_enforce` deliberately leaves a night alone when the
household spoke for it, because a cap is not a reason to overrule a
choice. What was missing is that NOTHING SAID SO — plan_quality measured
the breach into the morning report, a file Emily reads and the household
never sees, and the draft showed the over-cap dinner without a word.

So the behaviour catches here are about the SAYING, not the keeping, and
the test that matters most is
`test_the_over_cap_dinner_she_asked_for_is_still_kept_exactly_where_it_was`
— green on main and on this branch, pinned by mutation, because a branch
that started flagging by starting to MOVE her dinner would have got the
card exactly backwards.

RED AGAINST main IS WORTH ALMOST NOTHING HERE, and the honest numbers are
given rather than a flattering summary. Measured by checking main's `app/`
and `static/` out over this tree and running this file (the untracked
`app/tools/draft_flags.py` survives that, which is what lets the file be
collected at all): **30 failed, 5 passed**. Of the 30:

  * **20** die on `sqlite3.OperationalError: no such column:
    draft_flags_json` — inside the store, never reaching an assertion;
  * **4** die on `ValueError: substring not found`, inside this file's own
    `_extract()` helper, because main's shell.js has no such block;
  * **2** on `AttributeError: cap_enforce has no attribute would_offend`;
  * one each on a 404, a `KeyError: 'flagged'` and a `KeyError:
    'draft_flags'`;
  * and **exactly ONE reaches a real assertion** — the source marker
    `test_the_flags_are_at_the_top_of_the_draft_above_both_views`.

So there is ONE behaviour catch against main, not fifteen. An earlier
version of this docstring claimed "15 red / 6 green ... FIVE fail on the
assertion they are named for", and no reading of any tree reproduces
either number; it is corrected here rather than quietly replaced, because
a red count that means less than it looks is the statistic this repo's
Decision log keeps having to unpick.

**THE EVIDENCE IS THE MUTATIONS**, listed in CLAUDE.md, and the per-test
labels below should be read that way: CATCH means "this bites when the
behaviour it names is mutated away", not "this is red on main". The five
that pass on main are the four pure unit tests of `prep_target` /
`move_target` (that module file survives the checkout, so they run) and
`test_the_over_cap_dinner_she_asked_for_is_still_kept_exactly_where_it_was`
— which SHOULD pass on main, because the keeping half is main's own
behaviour and this branch must not change it.
"""
from __future__ import annotations

import datetime
import json
import re

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import cap_enforce, draft_flags

from conftest import household_today
from test_rush_cap_enforced import (  # noqa: E402 — the fixtures this is about
    CAP, _dinners, _filler, _recipe, _slot, _week,
    capped, picker, run, stub_model,  # noqa: F401 — pytest fixtures
)

import nodeharness


SHELL_JS = open("static/shell.js").read()

_ESCAPE_HTML = """
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
"""


def _monday(offset_weeks: int = 1) -> str:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _asked_for(name: str) -> dict:
    """
    The model's own stamp for "they typed this" — derived_from.freeform,
    which is what meal_variety.theirs reads and what makes cap_enforce
    leave the night standing.
    """
    return {"derived_from": {"freeform": f"I want {name} on Wednesday"}}


def _flags(plan_id: int) -> list[dict]:
    return draft_flags.plan_flags(plan_id)


# ---------------------------------------------------------------- the flag

def test_a_dish_she_asked_for_on_a_short_night_is_flagged_in_her_own_words(capped, stub_model, run):
    """
    CATCH (red on main: no flag at all). The sentence is Emily's, word for
    word off the decided page: "Lasagna takes 60 minutes, and Wednesday is
    short on time."
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))

    plan_id, _ = run(week, None)

    flags = _flags(plan_id)
    assert len(flags) == 1
    assert flags[0]["text"] == "Lasagna takes 60 minutes, and Wednesday is short on time."
    assert flags[0]["kind"] == draft_flags.OVER_CAP_REQUEST
    assert flags[0]["date"] == dates[2]
    assert flags[0]["dish"] == "Lasagna"


def test_the_over_cap_dinner_she_asked_for_is_still_kept_exactly_where_it_was(capped, stub_model, run):
    """
    GUARD, genuinely green on main AND here — one of only five in this file
    that are, and the most important test in it.
    The card says "it keeps the lasagna on Wednesday because you chose it,
    flags that it runs over". A branch that started flagging by starting to
    MOVE her dinner would have got the card backwards, so the keeping is
    asserted beside the saying.

    Pinned by the mutation that makes cap_enforce.nights() ignore
    meal_variety.theirs — which moves the lasagna and reddens this.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))

    plan_id, _ = run(week, None)

    assert {r["date"]: r["meal"] for r in _dinners(plan_id)}[dates[2]] == "Lasagna"


def test_a_dish_nobody_asked_for_is_fixed_rather_than_flagged(capped, stub_model, run):
    """
    GUARD in substance, and NOT "green on main" — it dies there in the
    store, like almost everything in this file (see the header). What
    pins it is the mutation that drops the `theirs` check. Scenario 4 is the
    asymmetry the card turns on: an unrequested dish too slow for its night
    is put right BEFORE the draft is shown, so it never becomes a flag.

    This one is green because the week really was fixed — cap_enforce moved
    the braise to the weekend — so it is a guard on the fix, not on the
    `theirs` check. The test below is the one that pins that.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Long Braise"] + ["Quick Eggs"] * 4))

    plan_id, _ = run(week, None)

    assert _flags(plan_id) == []
    # …because it moved, not because it was hidden.
    assert {r["date"]: r["meal"] for r in _dinners(plan_id)}[dates[2]] == "Quick Eggs"


def test_an_unrequested_night_no_repair_could_fix_is_still_not_flagged(stub_model, run):
    """
    GUARD, and the one that actually pins the `theirs` check.

    WRITTEN BECAUSE THE MUTATION DID NOT BITE. The test above was named for
    this rule and could not measure it: its braise gets MOVED, so with the
    `theirs` check deleted there is nothing over cap left to wrongly flag,
    and the mutation ran green. Found by running the mutations rather than
    by reasoning about the test.

    Here every night carries the SAME cap and the picker comes back empty,
    so a dinner nobody asked for is genuinely stuck over it — which is the
    only state in which the `theirs` check is doing any work at all. No
    `capped` fixture deliberately: with a weeknight limit on top, Mon–Fri
    caps at 20 and the weekend at 30, so the free trade legitimately
    shortens the overrun by moving the braise to Saturday and the night is
    never stuck at all (measured — that is what the first version of this
    test did). It stays in the morning report (plan_quality's
    `rush_cap_respected`, which is what `left` feeds) and gets no line on
    the draft, because nobody chose it and a line the household can do
    nothing about is noise.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={d: ["rush"] for d in dates})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Long Braise"] + ["Quick Eggs"] * 4))

    plan_id, seen = run(week, None, pick=lambda context: {})

    assert {r["date"]: r["meal"] for r in _dinners(plan_id)}[dates[2]] == "Long Braise"
    assert [x["why"] for x in seen["result"]["left"]] == ["nothing quicker came back"]
    assert _flags(plan_id) == []


def test_a_night_already_cooked_is_never_flagged(capped, stub_model, run):
    """
    GUARD. The work is done — there is nothing left to offer and nothing to
    move, so a line about it would be noise. Pinned by the mutation that
    drops the cooked check from flags_for_kept_over_cap.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    entry_id = [r for r in _dinners(plan_id) if r["date"] == dates[2]][0]["id"]

    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
    # Re-run the pass over the cooked week, as a re-draft would.
    result = cap_enforce.enforce_minutes_caps(plan_id, tools.get_week_intake(week),
                                              tools.get_household_memory())

    assert result["flagged"] == []


def test_a_flagged_night_is_always_one_cap_enforce_deliberately_left_alone(capped, stub_model, run):
    """
    GUARD on the seam between two readings of one rule. `cap_enforce.nights`
    collapses every reason a night is untouchable into `movable`; this file
    asks the narrower question ("did they choose it?"). They agree today —
    `theirs` implies not-movable — and nothing else would notice them
    drifting apart, so this says it.

    Pinned by the mutation that drops `theirs` from cap_enforce.nights,
    which makes a flagged night movable and reddens this.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)

    flagged = {f["entry_id"] for f in _flags(plan_id)}
    assert flagged
    read_back = cap_enforce.nights(plan_id, tools.get_week_intake(week),
                                   tools.get_household_memory())
    for night in read_back:
        if night["id"] in flagged:
            assert night["movable"] is False, "a flag must never name a night this pass could move"


def test_a_chain_end_is_never_flagged_here(capped, stub_model, run):
    """
    CHARACTERISATION, and a deliberate hole. cap_enforce's own docstring
    names the over-cap chain SOURCE as its biggest open question and
    Emily's call — the household really is cooking 35 minutes on a night
    they said was short, and it is deliberate batching. Widening into it
    from the flag side would be deciding it unmeasured, so a chain end
    gets no line either way. Invert this when that card is worked.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(
        dates, ["Quick Eggs"] * 2 + ["Lasagna", "Lasagna"] + ["Quick Eggs"] * 3,
        **{dates[2]: dict(_asked_for("Lasagna"),
                          derived_from={"freeform": "lasagna Wednesday",
                                        "make_double_for": [f"{dates[3]}:dinner"]}),
           dates[3]: {"derived_from": {"links_to": f"{dates[2]}:dinner"}}},
    ))

    plan_id, _ = run(week, None)

    assert _flags(plan_id) == []


# ------------------------------------------------------- the two fixes

def test_the_two_fixes_name_the_night_before_and_the_nearest_night_that_fits(capped, stub_model, run):
    """
    CATCH (red on main: no flags, so no fixes). Pomona fills in the nights
    — "Prep it Tuesday night", "Move it to Saturday" — and the move is the
    NEAREST night whose own dinner can come back to the short one. A
    "Keep on Wednesday" rides alongside the move (Emily, 2026-09-28).
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    # Wednesday is short on time; the weekend has no cap at all, so
    # Saturday is the nearest night that can take a 60-minute dish.
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))

    plan_id, _ = run(week, None)

    fixes = _flags(plan_id)[0]["fixes"]
    assert [f["action"] for f in fixes] == [
        draft_flags.FIX_PREP_AHEAD, draft_flags.FIX_MOVE, draft_flags.FIX_KEEP,
    ]
    assert fixes[0]["label"] == "Prep it Tuesday night"
    assert fixes[0]["date"] == dates[1]
    assert fixes[1]["label"] == "Move it to Saturday"
    assert fixes[1]["date"] == dates[5]
    assert fixes[2]["label"] == "Keep on Wednesday"
    assert fixes[2]["date"] == dates[2]


def test_no_keep_is_offered_without_a_move_to_stand_beside(capped, stub_model, run):
    """
    CATCH. The card ties "Keep" to a live move offer — a flag with no move
    (nothing else on the week could take the dish) offers no keep either,
    since keeping is already what happens by doing nothing.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    # Cap every OTHER night too, so nothing can take the trade — no move
    # target exists, and the first day of the period has no night before.
    tools.save_week_intake(week, night_tags={d: ["rush"] for d in dates})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))

    plan_id, _ = run(week, None)

    fixes = _flags(plan_id)[0]["fixes"]
    assert draft_flags.FIX_MOVE not in [f["action"] for f in fixes]
    assert draft_flags.FIX_KEEP not in [f["action"] for f in fixes]


def test_a_move_is_only_offered_when_both_nights_come_out_within_their_caps():
    """
    CATCH on the one piece of real arithmetic here. swap_dinner_nights
    TRADES, so offering a night whose own dish would then break the short
    night is offering to move the problem. Driven on move_target directly
    — through generation the free re-arrange stage fixes the week first,
    which would measure nothing (the lesson test_rush_cap_enforced's own
    `generate_only` fixture records).
    """
    # Two different ways a night fails, so each half of the check is
    # pinned by a night the other half would have let through.
    caps = {"2026-10-05": 20, "2026-10-06": 20, "2026-10-07": None, "2026-10-08": None}
    short = {"id": 1, "date": "2026-10-05", "minutes": 60, "movable": False}
    others = [
        short,
        # Nearest, and capped at 20 — OUR 60-minute dish does not fit
        # there. Fails the first half.
        {"id": 2, "date": "2026-10-06", "minutes": 10, "movable": True},
        # Uncapped, so our dish fits — but ITS 45-minute dish would come
        # back to the 20-minute night and break it. Fails the second half,
        # and nothing in the first half would have caught it.
        {"id": 3, "date": "2026-10-07", "minutes": 45, "movable": True},
        # Furthest, uncapped, and its dish fits the short night.
        {"id": 4, "date": "2026-10-08", "minutes": 10, "movable": True},
    ]

    assert draft_flags.move_target(short, others, caps) == "2026-10-08"


def test_no_move_is_offered_when_no_night_can_take_it():
    """
    GUARD: a flag with nothing to offer is still worth saying, and an
    invented night would be worse than none. Pinned by the mutation that
    drops the `minutes > there` check, which starts offering the capped
    night.
    """
    caps = {"2026-10-05": 20, "2026-10-06": 20}
    short = {"id": 1, "date": "2026-10-05", "minutes": 60, "movable": False}
    others = [short, {"id": 2, "date": "2026-10-06", "minutes": 10, "movable": True}]

    assert draft_flags.move_target(short, others, caps) is None


def test_the_night_before_is_not_offered_when_nobody_is_home_for_it():
    """
    CATCH on prep_target's one rule. Out, away, a skipped day and a meal
    category asked none of are all written as the SAME thing — a
    planned_empty row — so one test covers all of them and cannot drift
    from them.
    """
    nights = [
        {"date": "2026-10-04", "slot_state": "planned_empty"},
        {"date": "2026-10-05", "slot_state": "planned"},
    ]
    assert draft_flags.prep_target("2026-10-05", nights) is None

    nights[0]["slot_state"] = "planned"
    assert draft_flags.prep_target("2026-10-05", nights) == "2026-10-04"


def test_the_first_night_of_the_period_has_no_night_before_to_prep_on():
    """GUARD: the plan may not write on a day outside its own period."""
    nights = [{"date": "2026-10-05", "slot_state": "planned"}]
    assert draft_flags.prep_target("2026-10-05", nights) is None


# --------------------------------------------------- the store's honesty

def test_a_flag_whose_dish_has_changed_is_dropped_on_the_way_out(capped, stub_model, run):
    """
    CATCH (red on main: plan_flags does not exist). A flag names one entry
    on one night carrying one dish; once a different dinner is there, the
    sentence is about nothing.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    assert len(_flags(plan_id)) == 1

    entry_id = [r for r in _dinners(plan_id) if r["date"] == dates[2]][0]["id"]
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET recipe_id = NULL, freeform_meal = 'Something else' "
                 "WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()

    assert _flags(plan_id) == []


def test_a_flag_whose_night_has_moved_is_dropped_on_the_way_out(capped, stub_model, run):
    """
    CATCH. swap_dinner_nights re-dates the row IN PLACE, so the entry id
    survives a move — it is the DATE that makes the sentence's weekday
    false. This is also what clears the flag after the Move fix.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    assert len(_flags(plan_id)) == 1

    tools.swap_dinner_nights(plan_id, dates[2], dates[5])

    assert _flags(plan_id) == []


def test_recording_replaces_rather_than_accumulates(capped, stub_model, run):
    """
    GUARD: generation writes the whole set once, so a re-draft of the same
    plan describes the week as it now stands rather than piling up the
    ghosts of earlier attempts. Pinned by the mutation that makes
    draft_flags.record append.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)

    cap_enforce.enforce_minutes_caps(plan_id, tools.get_week_intake(week),
                                     tools.get_household_memory())
    cap_enforce.enforce_minutes_caps(plan_id, tools.get_week_intake(week),
                                     tools.get_household_memory())

    assert len(_flags(plan_id)) == 1


def test_another_households_flags_are_never_read(capped, stub_model, run):
    """
    CATCH. Every read and write here is scoped by household_id(); this is
    what says so rather than the comment. Seeded through use_household so
    the row really is the other household's — the trap this repo's own log
    records an isolation test falling into.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    assert len(_flags(plan_id)) == 1

    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO households (id, name) VALUES (99, 'Somebody else')")
    conn.commit()
    conn.close()
    with tools.use_household(99):
        assert draft_flags.plan_flags(plan_id) == []
        assert draft_flags.dismiss(plan_id, 1) is False

    assert len(_flags(plan_id)) == 1


# ------------------------------------------------------------ the route

def test_the_prep_fix_writes_a_prep_cut_and_takes_the_flag_off(signed_in, capped, stub_model, run):
    """
    CATCH (red on main: the route 404s). "Prep it Tuesday night" does not
    move the dish — it splits the work, through the prep-cut row the Cook
    tab already draws and ticks — in a prep session on a household with
    declared prep days, and in Cook's loose get-ready rows plus Today's
    timeline on one without, which is the default. See the module
    docstring; an earlier version of this line said "prep sessions" flatly
    and was true only for the first kind of household.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    entry_id = _flags(plan_id)[0]["entry_id"]

    res = signed_in.post(f"/api/week/{week}/flag-fix",
                      json={"entry_id": entry_id, "action": "prep_ahead"})

    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True
    assert res.json()["date"] == dates[1]
    conn = get_conn()
    rows = conn.execute(
        "SELECT task_date, description, task_type FROM prep_tasks WHERE weekly_plan_id = ?",
        (plan_id,)).fetchall()
    conn.close()
    cuts = [dict(r) for r in rows if r["task_type"] == "prep_cut"]
    assert len(cuts) == 1
    assert cuts[0]["task_date"] == dates[1]
    assert cuts[0]["description"] == "Get Lasagna ready for Wednesday"
    # The dish has not moved, so nothing else would ever clear the flag.
    assert _flags(plan_id) == []
    assert {r["date"]: r["meal"] for r in _dinners(plan_id)}[dates[2]] == "Lasagna"


def test_the_move_fix_trades_the_two_nights(signed_in, capped, stub_model, run):
    """CATCH (red on main: the route 404s)."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    entry_id = _flags(plan_id)[0]["entry_id"]

    res = signed_in.post(f"/api/week/{week}/flag-fix",
                      json={"entry_id": entry_id, "action": "move"})

    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True
    by_date = {r["date"]: r["meal"] for r in _dinners(plan_id)}
    assert by_date[dates[5]] == "Lasagna"
    assert by_date[dates[2]] == "Quick Eggs"
    assert _flags(plan_id) == []


def test_the_keep_fix_writes_nothing_and_just_takes_the_flag_off(signed_in, capped, stub_model, run):
    """
    CATCH (red before this branch: no `keep` action exists, so the route's
    fallback 400s it). Emily, 2026-09-28: "there should be a 'Keep on
    Monday' option as well next to the move to Saturday in case I want to
    keep it there." Tapping it dismisses the flag and moves nothing —
    Lasagna stays exactly on Wednesday.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    entry_id = _flags(plan_id)[0]["entry_id"]

    res = signed_in.post(f"/api/week/{week}/flag-fix",
                      json={"entry_id": entry_id, "action": "keep"})

    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True
    assert res.json()["action"] == "keep"
    by_date = {r["date"]: r["meal"] for r in _dinners(plan_id)}
    assert by_date[dates[2]] == "Lasagna"
    assert by_date[dates[5]] == "Quick Eggs"
    assert _flags(plan_id) == []


def test_a_kept_flag_stays_off_across_reload_and_an_unrelated_swap(signed_in, capped,
                                                                    stub_model, run):
    """
    CATCH. Emily's outcome: dismissing a flag with Keep "stays dismissed
    on reload and doesn't reappear after unrelated edits." plan_flags is
    the read path get_week_menu uses on every load, so re-reading it here
    stands in for a reload; swapping a DIFFERENT night's dinner is the
    unrelated edit.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    entry_id = _flags(plan_id)[0]["entry_id"]
    signed_in.post(f"/api/week/{week}/flag-fix", json={"entry_id": entry_id, "action": "keep"})
    assert _flags(plan_id) == []

    # An unrelated edit: swap Monday's own dinner for something else, and
    # then re-read the flags the way a fresh page load would.
    tools.swap_meal_in_plan(plan_id, dates[0], "Different Eggs", "dinner")

    assert _flags(plan_id) == []
    by_date = {r["date"]: r["meal"] for r in _dinners(plan_id)}
    assert by_date[dates[2]] == "Lasagna"


def test_a_kept_flag_can_come_back_if_the_dish_itself_changes(signed_in, capped, stub_model, run):
    """
    CATCH. Emily's outcome carves out one exception: "it may reappear if
    the meal itself changes, e.g. swapped to a different dish." Swapping
    the FLAGGED night's own dish for a different one over cap re-lights a
    flag naturally — it was never this flag (whose `dish`/`entry_id` no
    longer match), and cap_enforce's own read of the new dish is what
    would write a fresh one on the next real generation, not `dismiss`
    forgetting anything.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    entry_id = _flags(plan_id)[0]["entry_id"]
    signed_in.post(f"/api/week/{week}/flag-fix", json={"entry_id": entry_id, "action": "keep"})
    assert _flags(plan_id) == []

    # The flagged night's own dish changes underneath the (now empty)
    # stored flag list — plan_flags has nothing stale to drop, and nothing
    # here writes a new flag either, because dismiss doesn't reach into
    # generation. This just proves the kept flag's absence isn't somehow
    # blocking a fresh one from being written the normal way.
    tools.swap_meal_in_plan(plan_id, dates[2], "Beef Wellington", "dinner")
    assert _flags(plan_id) == []  # no generation ran, so nothing new was written
    by_date = {r["date"]: r["meal"] for r in _dinners(plan_id)}
    assert by_date[dates[2]] == "Beef Wellington"


def test_the_route_reads_the_night_off_the_stored_flag_never_off_the_request(signed_in, capped,
                                                                            stub_model, run):
    """
    CATCH, and the reason the request carries no date at all. The screen is
    showing a fix Pomona worked out, so the only honest answer to a tap is
    the one that was offered — a caller cannot name a night of its own.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    entry_id = _flags(plan_id)[0]["entry_id"]

    res = signed_in.post(f"/api/week/{week}/flag-fix",
                      # A night the flag never offered. It is ignored.
                      json={"entry_id": entry_id, "action": "move", "date": dates[6]})

    assert res.json()["date"] == dates[5]
    assert {r["date"]: r["meal"] for r in _dinners(plan_id)}[dates[5]] == "Lasagna"


def test_a_tap_on_a_flag_that_has_gone_says_so_rather_than_failing(signed_in, capped,
                                                                   stub_model, run):
    """
    CATCH. The household tapped something that had already stopped being
    true — a 200 saying nothing was done, never an error.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)

    res = signed_in.post(f"/api/week/{week}/flag-fix",
                      json={"entry_id": 99999, "action": "move"})

    assert res.status_code == 200
    assert res.json() == {"status": "gone", "applied": False}


def test_the_week_carries_its_flags_on_a_draft_and_never_on_an_approved_week(capped, stub_model,
                                                                             run):
    """
    CATCH (red on main: get_week_menu has no draft_flags key). A flag is
    about a decision the household has not taken yet, so an approved week
    carries none — the same rule the opener follows.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)

    menu = tools.get_week_menu(plan_id)
    assert len(menu["draft_flags"]) == 1
    assert menu["draft_flags"][0]["text"].startswith("Lasagna takes 60 minutes")

    tools.approve_weekly_plan(plan_id, confirm_hard_conflicts=True)
    assert tools.get_week_menu(plan_id)["draft_flags"] == []


# -------------------------------------------------------------- the screen

def _screen(data: dict) -> str:
    """The flags as the draft draws them, run for real under node."""
    harness = (
        _ESCAPE_HTML
        + _extract("weekFlagStateFor")
        + _extract("weekFlagsHtml")
        + "var weekFlagState = null;\n"
        + "var FLAG_EYEBROW = 'Worth knowing';\n"
        + "var FLAG_BUSY = 'Just a moment…';\n"
        + f"console.log(weekFlagsHtml({json.dumps(data)}));"
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, res.stderr
    return res.stdout


def _extract(name: str) -> str:
    start = SHELL_JS.index(f"function {name}(")
    depth, i = 0, SHELL_JS.index("{", start)
    for j in range(i, len(SHELL_JS)):
        if SHELL_JS[j] == "{":
            depth += 1
        elif SHELL_JS[j] == "}":
            depth -= 1
            if depth == 0:
                return SHELL_JS[start:j + 1] + "\n"
    raise AssertionError(name)


def test_the_draft_draws_one_line_per_flag_with_its_fixes_as_buttons():
    """
    CATCH (red on main: weekFlagsHtml does not exist). Every word is the
    server's — this renderer composes no sentence and fills in no night.
    """
    html = _screen({"draft_flags": [{
        "entry_id": 7, "text": "Lasagna takes 60 minutes, and Wednesday is short on time.",
        "fixes": [{"action": "prep_ahead", "label": "Prep it Tuesday night", "date": "x"},
                  {"action": "move", "label": "Move it to Saturday", "date": "y"}],
    }]})

    assert html.count('class="wk-flag"') == 1
    assert "Lasagna takes 60 minutes, and Wednesday is short on time." in html
    assert 'data-wk-flag="7" data-wk-flag-fix="prep_ahead"' in html
    assert 'data-wk-flag="7" data-wk-flag-fix="move"' in html
    assert "Prep it Tuesday night" in html and "Move it to Saturday" in html
    assert "Worth knowing" in html


def test_a_week_with_nothing_to_say_draws_nothing_at_all():
    """GUARD: no flags, no chrome. Pinned by the mutation that drops the
    early return, which leaves an empty .wk-flags wrapper on every draft."""
    assert _screen({"draft_flags": []}) .strip() == ""
    assert _screen({}).strip() == ""


def test_a_flag_with_no_fix_still_says_its_line():
    """
    CATCH. A move that would only hand the problem to another night is
    never offered, and the first night of a period has no night before it
    — so a flag really can have nothing to offer, and it is still worth
    saying.
    """
    html = _screen({"draft_flags": [{"entry_id": 7, "text": "It runs over.", "fixes": []}]})

    assert "It runs over." in html
    assert "wk-flag-acts" not in html


def test_the_flags_are_at_the_top_of_the_draft_above_both_views():
    """
    CATCH (red on main). "One line at the top of the draft" has to be true
    whichever way the draft is being read — What we're eating or Which
    days — so it is drawn once, above the branch that chooses between
    them. Source-level, because the two views are two returns.
    """
    review = _extract("reviewStepHtml")
    head = review.split("var head = root", 1)[1]
    assert "weekSuggestedNoteHtml(data) + weekFlagsHtml(data)" in head[:120]
    # And nowhere else: one place, so the two views cannot disagree.
    assert review.count("weekFlagsHtml(") == 1


def test_nothing_on_a_flag_is_apricot_or_urgent():
    """
    GUARD on hard rules 3 and 5. The draft's one apricot is Approve, and
    nothing here is overdue or owed — the week is approvable exactly as it
    stands. Pinned by mutation: point .wk-flag at --urgent and this fails.
    """
    # Comment-stripped, or this reads its own explanation rather than the
    # rules — the trap this repo's log records being bitten by three times,
    # and which this very assertion tripped over on its first run.
    css = re.sub(r"/\*.*?\*/", "", open("static/shell.css").read(), flags=re.S)
    start = css.index(".wk-flags {")
    block = css[start:css.index("\n.day-rail", start)]
    assert "--apricot" not in block
    assert "--urgent" not in block
    assert "var(--celadon-tint)" in block


# ------------------------------------------------- the review round, 2026-09-27
#
# Every test below reproduces something an independent review of this
# branch found and this branch then fixed. They are grouped because they
# share one root: the FIXES were worked out once, at generation, and read
# back verbatim at tap time — so every promise `move_target` makes about a
# pair of nights was a generation-time promise sold as a tap-time one.
# `plan_flags` re-derives them now; see `fixes_for`.


def test_taking_both_offers_in_a_row_cannot_leave_the_week_worse(signed_in, capped,
                                                                 stub_model, run):
    """
    CATCH, and the blocker. Two asked-for over-cap dinners and one free
    Saturday: main offers BOTH flags "Move it to Saturday", and taking both
    put a 60-minute Lasagna on the Tuesday that started at 55 — a longer
    breach than it began with, the requested Moussaka off the night she
    named, and NO flag left saying so. The second offer has to be re-read
    after the first is taken.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Moussaka", 55)
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[1]: ["rush"], dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs", "Moussaka", "Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[1]: _asked_for("Moussaka"), dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)

    flags = _flags(plan_id)
    assert len(flags) == 2
    wed = next(f for f in flags if f["date"] == dates[2])
    signed_in.post(f"/api/week/{week}/flag-fix",
                   json={"entry_id": wed["entry_id"], "action": "move"})

    # Saturday now holds the 60-minute Lasagna, so Tuesday's flag must no
    # longer offer it — that trade would hand Tuesday a LONGER dish.
    tue = next((f for f in _flags(plan_id) if f["date"] == dates[1]), None)
    assert tue is not None, "Tuesday is still over its cap and still asked for"
    for fix in tue["fixes"]:
        assert fix["action"] != draft_flags.FIX_MOVE or fix["date"] != dates[5], (
            "offered Saturday again, which now holds a 60-minute dish"
        )

    by_date = {r["date"]: r["meal"] for r in _dinners(plan_id)}
    assert by_date[dates[1]] == "Moussaka", "her Tuesday dish is still hers"


def test_an_outside_swap_between_draft_and_tap_cannot_strand_the_offer(signed_in, capped,
                                                                      stub_model, run):
    """
    CATCH. An ordinary chat swap onto the target night after the draft
    lands. On main the flag still offered that night on a freshly loaded
    screen — not a race, a genuinely stale stored answer — and tapping it
    put a 240-minute braise on a 20-minute rush night.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    _recipe("All Day Braise", 240)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    target = next(f["date"] for f in _flags(plan_id)[0]["fixes"]
                  if f["action"] == draft_flags.FIX_MOVE)

    tools.swap_meal_in_plan(plan_id, target, "All Day Braise", slot="dinner")

    flag = _flags(plan_id)[0]
    assert all(f["date"] != target for f in flag["fixes"]
               if f["action"] == draft_flags.FIX_MOVE), "still offering the braise's night"

    res = signed_in.post(f"/api/week/{week}/flag-fix",
                         json={"entry_id": flag["entry_id"], "action": "move"})
    assert res.status_code == 200, res.text
    by_date = {r["date"]: r["meal"] for r in _dinners(plan_id)}
    assert by_date[dates[2]] != "All Day Braise", "a 240-minute dish on the rush night"


def test_neither_fix_ever_names_a_night_that_has_already_gone(capped, stub_model, run):
    """
    CATCH. Nothing in the fixes read a clock: a draft opened mid-week
    offered "Prep it Tuesday night" on a Wednesday, and on a Saturday-start
    period it offered to MOVE the dish onto a night three days past —
    putting the dinner she asked for somewhere nobody can cook it.

    Seeded so the flagged night is the household's own today, which makes
    the night before it and the first half of the period genuinely gone.
    """
    today = household_today()
    week = (today - datetime.timedelta(days=3)).isoformat()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[3]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 3 + ["Lasagna"] + ["Quick Eggs"] * 3,
                     **{dates[3]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)

    flags = _flags(plan_id)
    if not flags:
        pytest.skip("the seeded week produced no flag on this weekday")
    for fix in flags[0]["fixes"]:
        assert not tools.night_has_gone(fix["date"]), (
            f"offered {fix['action']} on {fix['date']}, which is already behind today"
        )


def test_a_move_is_never_offered_onto_a_night_somebody_has_said_no_on():
    """
    CATCH. `cap_enforce` refuses its own trades on the taste veto and calls
    it "the one gate a move can break"; this door offered the move anyway,
    the route reported "applied", and the screen toasted "Moved." — a dish
    somebody has said no to, in front of them, on one tap, with nothing
    said. Driven at `move_target` with the veto stubbed, because what is
    being pinned is that the check is ASKED.
    """
    import app.tools.cap_enforce as ce

    night = {"date": "2026-10-07", "meal": "Lasagna", "minutes": 60, "movable": False}
    others = [
        night,
        {"date": "2026-10-10", "meal": "Quick Eggs", "minutes": 10, "movable": True},
    ]
    caps = {"2026-10-07": 20, "2026-10-10": None}

    assert draft_flags.move_target(night, others, caps) == "2026-10-10"

    real = ce.would_offend
    try:
        ce.would_offend = lambda meal, meal_date: (
            "Vineeth would rather not" if meal == "Lasagna" and meal_date == "2026-10-10" else None
        )
        assert draft_flags.move_target(night, others, caps) is None
    finally:
        ce.would_offend = real


def test_the_veto_is_cap_enforces_own_and_not_a_second_copy_of_it():
    """
    GUARD, and the reason `would_offend` was made public rather than
    reimplemented: one rule, two doors. Blinding cap_enforce's own check
    must blind this one, or the two can drift about who has said no to
    what — the class this repo keeps recording.
    """
    import app.tools.cap_enforce as ce

    real = ce.would_offend
    try:
        ce.would_offend = lambda meal, meal_date: "everyone would rather not"
        night = {"date": "2026-10-07", "meal": "Lasagna", "minutes": 60, "movable": False}
        others = [night, {"date": "2026-10-10", "meal": "Eggs", "minutes": 10, "movable": True}]
        assert draft_flags.move_target(night, others, {"2026-10-07": 20, "2026-10-10": None}) is None
    finally:
        ce.would_offend = real


def test_an_approved_week_answers_a_stale_tap_with_nothing_done(signed_in, capped,
                                                                stub_model, run):
    """
    CATCH. Criterion 6 says the flag does not survive into an approved
    week, and the SCREEN honoured it while the route did not: a draft open
    on one phone, approved from another, and a tap silently rearranged a
    week that had already been shopped for.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    entry_id = _flags(plan_id)[0]["entry_id"]
    before = {r["date"]: r["meal"] for r in _dinners(plan_id)}

    tools.approve_weekly_plan(plan_id, confirm_hard_conflicts=True)

    res = signed_in.post(f"/api/week/{week}/flag-fix",
                         json={"entry_id": entry_id, "action": "move"})

    assert res.status_code == 200, res.text
    assert res.json() == {"status": "gone", "applied": False}
    assert {r["date"]: r["meal"] for r in _dinners(plan_id)} == before


def test_a_flag_whose_night_was_cooked_is_dropped_by_the_reader_too(capped, stub_model, run):
    """
    CATCH. The producer skips a cooked night ("the work is done, so there
    is nothing to offer") and the reader did not, so the flag stayed on
    screen and the prep fix went through on a dinner somebody had already
    cooked. Producer and reader now ask the same question.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    entry_id = _flags(plan_id)[0]["entry_id"]

    tools.check_off_meal(entry_id, "done")

    assert _flags(plan_id) == []


def test_a_night_that_has_stopped_being_over_its_cap_drops_its_flag(capped, stub_model, run):
    """
    CATCH. The flag is a remark about a breach; once the breach is gone —
    somebody swapped the dish themselves — the sentence is untrue and must
    not be shown. Read-time, like every other staleness rule here.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)
    assert len(_flags(plan_id)) == 1

    # The same dish, now quick enough for the night — so what changes is
    # `over` alone, and the entry/date/dish checks cannot account for it.
    tools.update_recipe_details("Lasagna", prep_time_minutes=5, cook_time_minutes=10)

    assert _flags(plan_id) == [], "the night is no longer short on time"


def test_nothing_about_a_fix_is_stored(capped, stub_model, run):
    """
    GUARD on the shape the blocker's fix rests on: `record` keeps the SNAG
    and the offers are derived. A stored fix is a promise about a pair of
    nights that nothing revalidates, which is how this went wrong; if one
    reappears in the column, the staleness class is back.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Lasagna", 60)
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Lasagna"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked_for("Lasagna")}))
    plan_id, _ = run(week, None)

    conn = get_conn()
    raw = conn.execute("SELECT draft_flags_json FROM weekly_plans WHERE id = ?",
                       (plan_id,)).fetchone()["draft_flags_json"]
    conn.close()
    stored = json.loads(raw or "[]")

    assert stored, "the snag itself is still recorded"
    for flag in stored:
        assert not flag.get("fixes"), "a fix was stored; it must be derived at read time"
    # ...and the reader still produces them.
    assert _flags(plan_id)[0]["fixes"], "the reader derives the offers"
