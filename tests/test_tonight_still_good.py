"""
"Tonight: X. Still good?" — Now's top card from mid-afternoon, and the
swap-from-the-plan sheet behind "Something else" (Loop Board "Tonight's
dinner, mid-day: walk me to sorting it out instead of the day-editor
workaround"; Emily's decisions, 2026-09-13).

Server side: tools.tonight_check decides whether to ask and which nights
to offer; tools.tonight_keep remembers a "Yes" for the day; the swap
itself is the existing swap_dinner_nights, proven safe per option by a
dry run. Client side: the copy and the wiring in shell.js. Every test here
fails on main, where none of it exists.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import tools
from app.db import get_conn
from app.tools import tonight as _tonight

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")


def _monday() -> datetime.date:
    """
    The HOUSEHOLD's Monday, not the runner's.

    tonight_check resolves the plan against the household's local date
    (`households.timezone`, which conftest leaves at its column default,
    `_tonight.DEFAULT_TIMEZONE`), while the test process runs in whatever
    TZ it is given — UTC on CI. Building this week off `date.today()`
    quietly asserted that the server's day and the household's are the
    same day: for the hours when one has rolled over and the other has
    not, the two land in DIFFERENT Monday-weeks, no plan covers the
    household's today, and the route test fails for a reason that has
    nothing to do with what it is testing. It only bites when the split
    crosses a Monday, which is why it showed up on a Sunday night and not
    on the Tuesday somebody went looking.

    The same class as the "Re-seed the date-shaped tests off the
    household's clock" card; this file is one of the two that was
    blocking a timezone axis in CI.

    BELT-AND-BRACES, not the fix — say so plainly, because it would read
    as the fix otherwise. Measured 2026-09-15: with the app half done
    (tonight_check reads the dismissal before the plan), this file is
    already green under Etc/GMT+12 and Pacific/Kiritimati at Sunday and
    Monday pins with `_monday()` reverted to `date.today()`. The route
    test is the only one here that reads the real clock, and it takes
    its day from the response rather than from this constant. This stays
    because it makes the file mean what it says, and because the next
    date-sensitive test written in it would otherwise inherit the wrong
    assumption for free.
    """
    today = datetime.datetime.now(ZoneInfo(_tonight.DEFAULT_TIMEZONE)).date()
    return today - datetime.timedelta(days=today.weekday())


WEEK = _monday().isoformat()
DAYS = tools._week_dates(WEEK)
MON, TUE, WED, THU, FRI, SAT, SUN = DAYS
# "Tonight" for these tests is Wednesday of the current week, so there are
# nights on both sides of it — the clock is injected, never read.
TONIGHT = WED
AFTERNOON = datetime.datetime.fromisoformat(f"{TONIGHT}T15:50:00")
MORNING = datetime.datetime.fromisoformat(f"{TONIGHT}T09:00:00")


def _plan() -> int:
    return tools.create_weekly_plan(WEEK)["weekly_plan_id"]


def _recipe(name, minutes=30):
    tools.add_recipe(name, ingredients=[{"item": "Onion", "qty": "1", "category": "produce"}],
                     prep_time_minutes=10, cook_time_minutes=minutes - 10, default_servings=4)


def _dinner(day: str) -> str | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = 'dinner'",
        (tools.household_id(), day),
    ).fetchone()
    conn.close()
    return row["meal"] if row else None


def _full_week(plan: int) -> None:
    for day, dish in zip(DAYS, ["Toast Soldiers", "Bean Chili", "Chettinad-Style Pepper Chicken",
                                "Lentil Soup", "Fish Tacos", "Pizza Night", "Roast Veg"]):
        _recipe(dish)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)


# ---------------------------------------------------------------- the ask

def test_the_constant_is_mid_afternoon_and_the_card_asks_from_then():
    assert _tonight.TONIGHT_ASK_HOUR == 14
    plan = _plan()
    _full_week(plan)
    morning = tools.tonight_check(now=MORNING)
    afternoon = tools.tonight_check(now=AFTERNOON)
    assert morning["ask"] is False and morning["reason"] == "morning"
    assert afternoon["ask"] is True
    # Both know what tonight is — the morning just doesn't ask yet.
    assert morning["dinner"]["meal"] == "Chettinad-Style Pepper Chicken"
    assert afternoon["dinner"]["meal"] == "Chettinad-Style Pepper Chicken"
    assert afternoon["week_start"] == WEEK
    assert afternoon["date"] == TONIGHT


def test_exactly_two_oclock_counts_as_afternoon():
    plan = _plan()
    _full_week(plan)
    at_two = datetime.datetime.fromisoformat(f"{TONIGHT}T14:00:00")
    assert tools.tonight_check(now=at_two)["ask"] is True
    just_before = datetime.datetime.fromisoformat(f"{TONIGHT}T13:59:00")
    assert tools.tonight_check(now=just_before)["ask"] is False


def test_no_plan_today_means_no_question_and_no_error():
    out = tools.tonight_check(now=AFTERNOON)
    assert out["ask"] is False and out["reason"] == "no_plan"
    assert out["dinner"] is None and out["options"] == []


def test_a_plan_whose_period_does_not_cover_tonight_is_not_tonights_plan():
    last_week = (_monday() - datetime.timedelta(days=7)).isoformat()
    plan = tools.create_weekly_plan(last_week)["weekly_plan_id"]
    _recipe("Bean Chili")
    tools.plan_meal(tools._week_dates(last_week)[2], "Bean Chili", slot="dinner", weekly_plan_id=plan)
    out = tools.tonight_check(now=AFTERNOON)
    assert out["ask"] is False and out["reason"] == "no_plan"


def test_tonight_unplanned_stays_out_of_the_way_of_the_needs_a_dinner_card():
    """A plan that covers today but has no dinner row for tonight: Now's
    own "Tonight needs a dinner" card leads there, so this one is quiet."""
    plan = _plan()
    _recipe("Bean Chili")
    tools.plan_meal(THU, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    out = tools.tonight_check(now=AFTERNOON)
    assert out["ask"] is False and out["reason"] == "unplanned"
    assert out["dinner"] is None


def test_a_night_nobody_is_home_or_an_open_night_is_not_asked_about():
    plan = _plan()
    tools.plan_slot_empty(plan, TONIGHT, "dinner", reason="Away")
    assert tools.tonight_check(now=AFTERNOON)["reason"] == "away"


def test_a_dinner_already_cooked_is_not_asked_about():
    plan = _plan()
    _full_week(plan)
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE date = ? AND slot = 'dinner'", (TONIGHT,))
    conn.commit()
    conn.close()
    out = tools.tonight_check(now=AFTERNOON)
    assert out["ask"] is False and out["reason"] == "cooked"


# ---------------------------------------------------------------- "Yes"

def test_yes_is_remembered_for_the_day_household_wide_and_forgotten_tomorrow():
    plan = _plan()
    _full_week(plan)
    assert tools.tonight_check(now=AFTERNOON)["ask"] is True
    out = tools.tonight_keep(TONIGHT)
    assert out == {"date": TONIGHT, "answered": True}
    again = tools.tonight_check(now=AFTERNOON)
    assert again["ask"] is False and again["reason"] == "answered" and again["answered"] is True
    # Server-side, not in the browser: a fresh read (a reload, another
    # phone) gets the same answer.
    conn = get_conn()
    row = conn.execute("SELECT key FROM notification_dismissals WHERE household_id = ?",
                       (tools.household_id(),)).fetchone()
    conn.close()
    assert row["key"] == f"tonight-ok:{TONIGHT}"
    # Tomorrow is a different key, so tomorrow asks again.
    tomorrow = datetime.datetime.fromisoformat(f"{THU}T15:00:00")
    assert tools.tonight_check(now=tomorrow)["ask"] is True
    # A second Yes is the same row, not an error.
    tools.tonight_keep(TONIGHT)


def test_yes_refuses_a_date_that_is_not_one():
    with pytest.raises(ValueError):
        tools.tonight_keep("not-a-date")


# ---------------------------------------------------------------- the options

def test_options_are_later_nights_first_at_most_three_and_never_tonight():
    plan = _plan()
    _full_week(plan)
    out = tools.tonight_check(now=AFTERNOON)
    assert [o["date"] for o in out["options"]] == [THU, FRI, SAT]
    assert [o["meal"] for o in out["options"]] == ["Lentil Soup", "Fish Tacos", "Pizza Night"]
    assert all(o["date"] != TONIGHT for o in out["options"])
    assert out["options"][0]["weekday"] == datetime.date.fromisoformat(THU).strftime("%A")
    assert out["options"][0]["is_leftovers"] is False


def test_earlier_nights_are_never_offered_a_swap_is_not_a_delete():
    plan = _plan()
    for day, dish in ((MON, "Bean Chili"), (TUE, "Lentil Soup"), (TONIGHT, "Fish Tacos")):
        _recipe(dish)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    out = tools.tonight_check(now=AFTERNOON)
    assert out["ask"] is True
    assert out["options"] == []


def test_a_plan_with_only_tonight_on_it_asks_but_offers_nothing():
    plan = _plan()
    _recipe("Fish Tacos")
    tools.plan_meal(TONIGHT, "Fish Tacos", slot="dinner", weekly_plan_id=plan)
    out = tools.tonight_check(now=AFTERNOON)
    assert out["ask"] is True and out["options"] == []


def test_a_night_nobody_is_home_and_a_cooked_night_are_left_off_the_sheet():
    plan = _plan()
    _full_week(plan)
    conn = get_conn()
    conn.execute("DELETE FROM meal_plan_entries WHERE date = ? AND slot = 'dinner'", (THU,))
    conn.commit()
    conn.close()
    tools.plan_slot_empty(plan, THU, "dinner", reason="Away")
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE date = ? AND slot = 'dinner'", (FRI,))
    conn.commit()
    conn.close()
    out = tools.tonight_check(now=AFTERNOON)
    assert [o["date"] for o in out["options"]] == [SAT, SUN]


def test_a_leftovers_night_is_offered_as_one_and_a_backwards_chain_is_not():
    """Thursday eats Tuesday's chili: moving those leftovers to tonight
    keeps them after their cook, so Thursday is offered and named as
    leftovers. Sunday eats tonight's cook: trading tonight with Sunday
    would put the cook after its leftovers, so Sunday is left off."""
    plan = _plan()
    _full_week(plan)
    conn = get_conn()
    conn.execute("DELETE FROM meal_plan_entries WHERE date IN (?, ?) AND slot = 'dinner'", (THU, SUN))
    conn.commit()
    conn.close()
    tools.plan_meal(THU, "Bean Chili", slot="dinner", weekly_plan_id=plan,
                    derived_from={"links_to": f"{TUE}:dinner"})
    tools.repair_leftover_chains(plan)
    # Sunday is four days after tonight. Since 2026-09-23 the generation's
    # repair makes that a freezer portion (leftovers.MAX_LEFTOVER_DAYS), so
    # the chain is written the way the Cook card's own picker still can
    # (cook_ahead.set_cook_ahead is the household's call and has no limit):
    # both halves, by hand. This test is about the backwards swap, not the
    # distance.
    sun = tools.plan_meal(SUN, "Chettinad-Style Pepper Chicken", slot="dinner", weekly_plan_id=plan,
                          derived_from={"links_to": f"{TONIGHT}:dinner"})["entry_id"]
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE date = ? AND slot = 'dinner' AND id != ?",
        (json.dumps({"make_double_for": [f"{SUN}:dinner"]}), TONIGHT, sun),
    )
    conn.commit()
    conn.close()
    assert len(tools.plan_leftover_chains(plan)["leftovers"]) == 2, "both chains confirmed first"
    out = tools.tonight_check(now=AFTERNOON)
    by_date = {o["date"]: o for o in out["options"]}
    assert SUN not in by_date
    assert [o["date"] for o in out["options"]] == [THU, FRI, SAT]
    assert by_date[THU]["is_leftovers"] is True
    assert by_date[THU]["leftovers_from"] == datetime.date.fromisoformat(TUE).strftime("%A")
    assert by_date[FRI]["is_leftovers"] is False


def test_when_tonights_cook_feeds_tomorrow_nothing_later_can_take_its_place():
    """The honest limit: tonight's dish is the source of tomorrow's
    leftovers, so every later night would put the cook after them. The
    card still asks; the sheet has nothing to offer."""
    plan = _plan()
    _full_week(plan)
    conn = get_conn()
    conn.execute("DELETE FROM meal_plan_entries WHERE date = ? AND slot = 'dinner'", (THU,))
    conn.commit()
    conn.close()
    tools.plan_meal(THU, "Chettinad-Style Pepper Chicken", slot="dinner", weekly_plan_id=plan,
                    derived_from={"links_to": f"{TONIGHT}:dinner"})
    tools.repair_leftover_chains(plan)
    out = tools.tonight_check(now=AFTERNOON)
    assert out["ask"] is True and out["options"] == []


def test_the_dry_run_behind_the_options_writes_nothing():
    plan = _plan()
    _full_week(plan)
    before = {d: _dinner(d) for d in DAYS}
    tools.tonight_check(now=AFTERNOON)
    assert {d: _dinner(d) for d in DAYS} == before


# ---------------------------------------------------------------- the swap

def test_one_tap_trades_tonight_with_the_chosen_night_and_the_list_is_untouched(signed_in):
    plan = _plan()
    _full_week(plan)
    tools.approve_weekly_plan(plan)
    conn = get_conn()
    before = [tuple(r) for r in conn.execute(
        "SELECT item, quantity, status FROM grocery_items WHERE household_id = ? ORDER BY item",
        (tools.household_id(),)).fetchall()]
    conn.close()
    assert before, "an approved week has a list"

    out = tools.tonight_check(now=AFTERNOON)
    pick = out["options"][1]  # Friday's Fish Tacos
    res = signed_in.post(f"/api/week/{out['week_start']}/swap-nights",
                         json={"date_a": out["date"], "date_b": pick["date"]})
    assert res.status_code == 200 and res.json()["status"] == "swapped"
    assert _dinner(TONIGHT) == "Fish Tacos"
    assert _dinner(FRI) == "Chettinad-Style Pepper Chicken"
    conn = get_conn()
    after = [tuple(r) for r in conn.execute(
        "SELECT item, quantity, status FROM grocery_items WHERE household_id = ? ORDER BY item",
        (tools.household_id(),)).fetchall()]
    conn.close()
    assert after == before
    # After the swap the card asks about the NEW tonight, and Friday's
    # slot — now holding the old tonight — is back on offer.
    again = tools.tonight_check(now=AFTERNOON)
    assert again["dinner"]["meal"] == "Fish Tacos"
    assert FRI in [o["date"] for o in again["options"]]


# ---------------------------------------------------------------- the routes

def test_the_routes_read_tonight_and_remember_yes(signed_in):
    plan = _plan()
    _full_week(plan)
    res = signed_in.get("/api/today/tonight")
    assert res.status_code == 200
    body = res.json()
    assert set(body) >= {"date", "ask", "reason", "afternoon", "answered", "week_start", "dinner", "options"}
    day = body["date"]
    keep = signed_in.post("/api/today/tonight/keep", json={"date": day})
    assert keep.status_code == 200 and keep.json() == {"date": day, "answered": True}
    assert signed_in.get("/api/today/tonight").json()["answered"] is True
    assert signed_in.post("/api/today/tonight/keep", json={"date": "nope"}).status_code == 400


def test_the_routes_need_a_signed_in_household(client):
    assert client.get("/api/today/tonight").status_code in (401, 403)


# ---------------------------------------------------------------- the copy

def test_the_card_asks_the_plain_question_with_yes_and_something_else():
    """DESIGN_SYSTEM §8 rule 7: the question first, the choice under it,
    buttons that commit. Named the way Emily wrote it."""
    assert "function renderTonightAsk(" in SHELL_JS
    assert "'Tonight: ' + " in SHELL_JS and "'. Still good?'" in SHELL_JS
    assert ">Yes</button>" in SHELL_JS
    assert ">Something else</button>" in SHELL_JS
    assert "'/api/today/tonight'" in SHELL_JS
    assert "'/api/today/tonight/keep'" in SHELL_JS


def test_the_sheet_offers_a_swap_not_a_delete_and_names_leftovers():
    assert 'id="tonight-sheet"' in SHELL_HTML
    assert "function openTonightSheet(" in SHELL_JS
    assert "What should tonight be instead?" in SHELL_HTML
    # With nothing to swap with, the sheet used to say "Nothing else on this
    # week’s plan can move to tonight." over an "Open today in the plan"
    # button. Emily, 2026-09-22: the night off leads instead, alone — the
    # line and the button sent her off to do the planning herself.
    assert 'class="tonight-none"' not in SHELL_JS
    assert "tonight-open-plan" not in SHELL_JS
    assert "'leftovers from '" in SHELL_JS
    # One tap commits through the existing nights swap — no new write path.
    assert "/swap-nights'" in SHELL_JS
    assert "function runTonightSwap(" in SHELL_JS
    # The swap is reversible from its toast.
    assert "/swap-nights-undo'" in SHELL_JS


def test_the_card_spends_no_second_apricot():
    """Rule 5: Now's one apricot is the dock. The card's Yes is spruce and
    Something else an outline (the .ny-actions pair the needs-you band
    already scopes that way)."""
    assert 'class="ny-actions"' in SHELL_JS
    start = SHELL_JS.index("function renderTonightAsk(")
    block = SHELL_JS[start:start + 4000]
    assert "btn-gold" in block and "btn-sand" in block
    assert "dock-primary" not in block


def test_the_sheet_has_its_own_geometry_and_a_44px_row():
    assert "#tonight-sheet" in SHELL_CSS and "#tonight-scrim" in SHELL_CSS
    assert ".tonight-option" in SHELL_CSS
    start = SHELL_CSS.index(".tonight-option {")
    assert "min-height: 44px" in SHELL_CSS[start:start + 600] or "min-height: 5" in SHELL_CSS[start:start + 600]


# ------------------------------------------- a Yes is remembered regardless

def test_a_yes_is_remembered_when_no_plan_covers_today():
    """
    CATCH (red on main). The reported bug: tonight_check used to read the
    dismissal AFTER resolving the plan, so the `no_plan` return gave up
    before it ever looked — the answer was on record and the code never
    asked. Reachable in ordinary use, because this function resolves the
    plan against the household's date while the container runs UTC.

    Nothing is planned here at all, which is the same shape from the
    app's point of view and needs no clock trickery to reach.
    """
    day = _tonight._household_now().date().isoformat()
    assert tools.tonight_check()["reason"] == "no_plan"

    tools.tonight_keep(day)

    after = tools.tonight_check()
    assert after["reason"] == "no_plan"
    assert after["answered"] is True, "the Yes is on file and must be reported"
    assert after["ask"] is False, "a day with no plan still asks nothing"


def test_a_yes_is_remembered_on_a_component_based_plan():
    """CATCH (red on main). The other early return that swallowed it."""
    plan = _plan()
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET planning_mode = 'component_based' WHERE id = ?", (plan,))
    conn.commit()
    conn.close()

    tools.tonight_keep(TONIGHT)

    out = tools.tonight_check(now=AFTERNOON)
    assert out["reason"] == "components"
    assert out["answered"] is True
    assert out["ask"] is False


def test_answered_is_still_false_when_nobody_has_said_yes():
    """
    GUARD (green on main). The fix reports a Yes that exists; it must not
    invent one. Both shapes: no plan at all, and a real afternoon.
    """
    assert tools.tonight_check()["answered"] is False

    plan = _plan()
    _full_week(plan)
    out = tools.tonight_check(now=AFTERNOON)
    assert out["answered"] is False
    assert out["ask"] is True, "an unanswered afternoon still asks"


def test_a_yes_on_a_normal_afternoon_is_unchanged():
    """
    GUARD (green on main). The path that always worked: a plan covering
    today, a Yes on file, the card quiet with reason 'answered'. Moving
    the read must not have changed what the ordinary day says.
    """
    plan = _plan()
    _full_week(plan)
    tools.tonight_keep(TONIGHT)

    out = tools.tonight_check(now=AFTERNOON)
    assert out["answered"] is True
    assert out["reason"] == "answered"
    assert out["ask"] is False
    assert out["dinner"]["meal"] == "Chettinad-Style Pepper Chicken"


def test_yesterdays_yes_does_not_answer_today():
    """
    GUARD (green on main). The record is keyed by the day, so reading it
    earlier must not make it outlive its day — otherwise the card would
    go quiet for good after one Yes.
    """
    yesterday = (datetime.date.fromisoformat(TONIGHT) - datetime.timedelta(days=1)).isoformat()
    tools.tonight_keep(yesterday)

    plan = _plan()
    _full_week(plan)
    out = tools.tonight_check(now=AFTERNOON)
    assert out["answered"] is False
    assert out["ask"] is True


def test_one_households_yes_is_not_another_households():
    """
    GUARD (green on main). The read this branch MOVED is the one that
    decides whether a household has answered, and this repo's decision
    log records three separate cross-household leaks — so the invariant
    is worth a test rather than an argument. It had none before.

    BOTH households get a real week on purpose. An earlier draft gave
    only household 1 a plan, which made this red on main — not because
    isolation was broken there (it wasn't) but because the second
    household fell down the `no_plan` path this branch exists to fix.
    A test that goes red for a reason other than the one it is named
    after proves nothing about its own claim, so the plans are here to
    keep the two questions apart. Pinned by mutation instead of by
    redness: dropping `household_id` from the dismissal read fails it.
    """
    _full_week(_plan())
    tools.tonight_keep(TONIGHT)
    assert tools.tonight_check(now=AFTERNOON)["answered"] is True

    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO households (id, name) VALUES (2, 'Next door')")
    conn.commit()
    conn.close()

    # use_household is a context manager — calling it bare builds a
    # generator and changes nothing, which is how the first draft of
    # this test "found a leak" that wasn't there.
    with tools.use_household(2):
        _full_week(_plan())
        assert tools.tonight_check(now=AFTERNOON)["answered"] is False, \
            "household 2 never said yes"
        tools.tonight_keep(TONIGHT)
        assert tools.tonight_check(now=AFTERNOON)["answered"] is True

    # And household 1's own answer is untouched by any of that.
    assert tools.tonight_check(now=AFTERNOON)["answered"] is True
