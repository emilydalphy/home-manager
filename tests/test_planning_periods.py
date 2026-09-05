"""
Planning periods, not weeks — Loop Board "plan any window (Thursday to
Thursday)".

A plan is a PERIOD: a start date and a day count. The Monday week was the
last traditional assumption in this data model, and it was not one
assumption but thirteen — the seven was written into SQL in one place,
into a list slice in five, and into prose in the system prompt in three.

Four things are pinned here, and the fourth is the one that makes the other
three safe to have built:

1.  **Overlap takeover.** Emily's rule (2026-09-04): any given day belongs
    to at most one plan. A new period retires the days it overlaps, in
    whichever of the four shapes the overlap takes.
2.  **Grocery reconciliation**, including the part that must NOT happen —
    a line somebody has already bought is never yanked back off the list,
    even when the meal it was bought for is gone.
3.  **Non-Monday anchors.** A Thursday-to-next-Thursday period generates,
    audits, renders and resolves as itself, not as a misfiled week.
4.  **The no-op property.** An ordinary Monday week is byte-identical to
    what it was before any of this existed. This is the load-bearing one:
    everything above is only worth having if it costs the households who
    never asked for it nothing at all.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, tools
from app.db import get_conn


# ---------- helpers ----------

def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _thursday(offset_weeks: int = 1) -> str:
    return tools.period_dates(_monday(offset_weeks), 7)[3]


def _full_period(start: str, count: int, meal: str = "Chili") -> list[dict]:
    """A complete, well-behaved model response covering exactly the period."""
    return [
        {"date": day, "slot": slot, "meal_name": meal, "is_new_recipe": False,
         "reasoning": "fits the period"}
        for day in tools.period_dates(start, count)
        for slot in tools.WEEK_SLOTS
    ]


def _plan_row(plan_id: int) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    return dict(row)


def _dates_on(plan_id: int) -> set[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT date FROM meal_plan_entries WHERE weekly_plan_id = ? "
        "AND component_category IS NULL",
        (plan_id,),
    ).fetchall()
    conn.close()
    return {r["date"] for r in rows}


def _live_day_owners() -> dict:
    """
    Every day claimed by more than one LIVE plan — the one-plan-per-day rule
    stated as a query. Empty is the only acceptable answer.

    Asked of the database rather than of a return value on purpose: a
    takeover that reports the right thing while leaving two plans claiming
    a day has not enforced anything.
    """
    conn = get_conn()
    rows = conn.execute("SELECT * FROM weekly_plans WHERE status != 'retired'").fetchall()
    conn.close()
    owners: dict[str, list[int]] = {}
    for row in rows:
        start, days = tools.plan_period(row)
        for day in tools.period_dates(start, days):
            owners.setdefault(day, []).append(row["id"])
    return {d: ids for d, ids in owners.items() if len(ids) > 1}


@pytest.fixture
def recipes():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    tools.add_recipe("Katsu", ingredients=[{"item": "panko", "qty": "1 bag"}],
                     prep_time_minutes=10, cook_time_minutes=20)


@pytest.fixture
def stub_model(monkeypatch):
    """Pin the model's answer so a test asserts on OUR logic, not the LLM's."""
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)
    return _stub


# ---------- the period as a value ----------

class TestPeriodArithmetic:
    def test_period_dates_is_not_a_capped_slice(self):
        # The bug the helper exists to prevent: `_week_dates(s)[:n]` cannot
        # return more than seven, so an 8-day period silently lost its
        # eighth day everywhere this idiom appeared.
        assert len(tools.period_dates("2026-09-10", 8)) == 8
        assert tools.period_dates("2026-09-10", 8)[-1] == "2026-09-17"
        assert tools._week_dates("2026-09-10")[:8] != tools.period_dates("2026-09-10", 8)

    def test_a_zero_day_period_has_no_days_and_matches_nothing(self):
        # A plan that surrendered everything is a real, queryable row.
        assert tools.period_dates("2026-09-10", 0) == []
        # end BEFORE start is what makes `start <= d <= end` match nothing
        # rather than accidentally matching the start day.
        assert tools.period_end_date("2026-09-10", 0) == "2026-09-09"

    def test_overlap_returns_the_shared_days_in_order(self):
        assert tools.periods_overlap("2026-09-07", 7, "2026-09-10", 8) == [
            "2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13",
        ]
        # Adjacent, not overlapping: Sep 7-13 then Sep 14 onward.
        assert tools.periods_overlap("2026-09-07", 7, "2026-09-14", 7) == []

    def test_plan_period_resolves_the_legacy_sentinel_to_the_old_meaning(self):
        # THE no-op guarantee, at its source. A row written before periods
        # existed carries '' and 0, and must read as seven days from
        # week_start_date — not be backfilled into explicit values, which
        # is the only way this migration could turn a right row wrong.
        legacy = {"week_start_date": "2026-09-07", "content_start_date": "", "day_count": 0}
        assert tools.plan_period(legacy) == ("2026-09-07", 7)


# ---------- the no-op property ----------

class TestOrdinaryMondayWeekIsUnchanged:
    def test_a_plain_week_generates_the_same_shape_it_always_did(self, recipes, stub_model):
        week = _monday()
        stub_model(_full_period(week, 7))
        plan = agent.generate_weekly_plan(week)

        assert _dates_on(plan["weekly_plan_id"]) == set(tools._week_dates(week))
        assert plan["week_start_date"] == week
        assert plan["period_start_date"] == week
        assert plan["day_count"] == 7
        assert plan["is_custom_period"] is False
        assert plan["is_part_week"] is False
        # Nothing to take over, and the empty result is a real result
        # rather than an absence the caller has to guard against.
        assert plan["took_over"]["retired_plan_ids"] == []
        assert plan["took_over"]["surrendered_dates"] == []

    def test_the_week_menu_still_returns_exactly_seven_days(self, recipes, stub_model):
        week = _monday()
        stub_model(_full_period(week, 7))
        plan = agent.generate_weekly_plan(week)

        menu = tools.get_week_menu(plan["weekly_plan_id"])
        assert [d["date"] for d in menu["days"]] == tools._week_dates(week)
        assert all(d["before_plan_start"] is False for d in menu["days"])
        assert menu["week_label"] == tools._format_week_range(week)
        assert menu["is_custom_period"] is False

    def test_the_range_label_is_byte_identical_for_every_week_of_a_year(self):
        # _format_week_range now delegates to _format_period_range. Every
        # string it can produce has to be the same string it produced
        # before, because these appear in copy the household reads.
        day = datetime.date(2026, 1, 5)  # a Monday
        for _ in range(53):
            iso = day.isoformat()
            start, end = day, day + datetime.timedelta(days=6)
            expected = (
                f"{start.strftime('%b')} {start.day}–{end.day}"
                if start.month == end.month
                else f"{start.strftime('%b')} {start.day}–{end.strftime('%b')} {end.day}"
            )
            assert tools._format_week_range(iso) == expected
            assert tools._format_period_range(iso, 7) == expected
            day += datetime.timedelta(days=7)

    def test_a_legacy_row_with_no_period_still_resolves_as_the_current_plan(self):
        # Written the way a pre-period row was written: no content_start_date,
        # no day_count. _current_weekly_plan_row's window used to be a SQL
        # literal '+6 days'; this proves the replacement resolves identically.
        today = datetime.date.today()
        monday = (today - datetime.timedelta(days=today.weekday())).isoformat()
        conn = get_conn()
        conn.execute(
            "INSERT INTO weekly_plans (household_id, week_start_date) VALUES (1, ?)", (monday,)
        )
        conn.commit()
        row = tools._current_weekly_plan_row(conn)
        conn.close()
        assert row is not None
        assert row["week_start_date"] == monday
        assert tools.plan_period(row) == (monday, 7)


# ---------- non-Monday anchors ----------

class TestNonMondayPeriods:
    def test_thursday_to_next_thursday_is_eight_real_days(self, recipes, stub_model):
        # Emily's own example, and the off-by-one everybody makes: said out
        # loud it means both Thursdays, so it is 8 days, not 7.
        start = _thursday()
        stub_model(_full_period(start, 8))
        plan = agent.generate_weekly_plan(start, day_count=8, period_start=start)

        assert plan["day_count"] == 8
        assert plan["period_start_date"] == start
        assert plan["period_end_date"] == tools.period_dates(start, 8)[-1]
        assert datetime.date.fromisoformat(plan["period_end_date"]).weekday() == 3
        assert _dates_on(plan["weekly_plan_id"]) == set(tools.period_dates(start, 8))

    def test_the_eighth_day_is_audited_like_every_other(self, recipes, stub_model):
        # The failure this guards: `[:day_count]` capped the window at
        # seven, so the last day of a longer period was generated for but
        # never audited — it could go missing and nothing would notice.
        start = _thursday()
        eighth = tools.period_dates(start, 8)[-1]
        stub_model([d for d in _full_period(start, 8)
                    if not (d["date"] == eighth and d["slot"] == "dinner")])
        plan = agent.generate_weekly_plan(start, day_count=8, period_start=start)

        audit = tools.audit_plan_slots(plan["weekly_plan_id"])
        assert audit["expected"] == 8 * 3
        assert audit["complete"], audit
        # The hole the model left came back as an open question on the
        # right day, not as a silent absence.
        menu = tools.get_week_menu(plan["weekly_plan_id"])
        eighth_day = [d for d in menu["days"] if d["date"] == eighth][0]
        assert eighth_day["dinner"]["state"] == "open"

    def test_the_question_screens_ask_about_the_periods_real_days(self):
        # Caught by driving the real screen, not by reading the code: the
        # day cards were right and the eyebrow above them still said seven,
        # so the page named a window it was visibly not asking about.
        start = _thursday()
        prefill = tools.get_week_intake_prefill(start, day_count=8)
        assert [d["date"] for d in prefill["days"]] == tools.period_dates(start, 8)
        assert prefill["week_label"] == tools._format_period_range(start, 8)
        assert prefill["day_count"] == 8

    def test_the_last_day_of_a_long_period_can_actually_be_ANSWERED(self, recipes, stub_model):
        # The worst bug this build had, and it was invisible from the code:
        # save_week_intake checked each tagged day against seven days from
        # week_start, so tagging the eighth day of an eight-day period was
        # REFUSED — question 1 could not be saved and the whole flow
        # stopped. Nothing raised until the round trip was actually run.
        start = _thursday()
        days = tools.period_dates(start, 8)
        saved = tools.save_week_intake(
            start, night_tags={days[7]: ["out"]}, created_by="Ana", day_count=8,
        )
        assert saved["intake_id"]

        stub_model(_full_period(start, 8))
        plan = agent.generate_weekly_plan(
            start, day_count=8, period_start=start, intake_id=saved["intake_id"],
        )
        # And the answer was HONOURED, not merely accepted: the tag wins
        # over whatever the model sent for that night.
        menu = tools.get_week_menu(plan["weekly_plan_id"])
        last = [d for d in menu["days"] if d["date"] == days[7]][0]
        assert last["dinner"]["state"] == "planned_empty"

    def test_a_tag_outside_the_period_is_still_refused(self):
        # The check widened; it did not go away. A tag stranded outside the
        # plan's days is visible nowhere and clearable from no screen.
        start = _thursday()
        outside = tools.period_dates(start, 9)[-1]
        with pytest.raises(ValueError, match="isn't in the"):
            tools.save_week_intake(start, night_tags={outside: ["out"]}, day_count=8)

    def test_a_shorter_period_does_not_ask_about_days_nobody_planned(self):
        # Answers to imaginary days would be saved as intake and read back
        # the next time that week IS planned.
        start = _thursday()
        prefill = tools.get_week_intake_prefill(start, day_count=3)
        assert len(prefill["days"]) == 3

    def test_the_menu_renders_the_period_and_labels_it(self, recipes, stub_model):
        start = _thursday()
        stub_model(_full_period(start, 8))
        plan = agent.generate_weekly_plan(start, day_count=8, period_start=start)

        menu = tools.get_week_menu(plan["weekly_plan_id"])
        assert [d["date"] for d in menu["days"]] == tools.period_dates(start, 8)
        assert menu["is_custom_period"] is True
        assert menu["week_label"] == tools._format_period_range(start, 8)

    def test_a_day_inside_a_thursday_period_resolves_to_that_plan(self, recipes, stub_model):
        # The Monday snap would have looked up a key no plan is filed under
        # and reported every one of these days unplanned.
        start = _thursday()
        stub_model(_full_period(start, 8))
        plan = agent.generate_weekly_plan(start, day_count=8, period_start=start)

        for day in tools.period_dates(start, 8):
            assert tools.get_plan_id_for_date(day) == plan["weekly_plan_id"], day

    def test_a_period_of_any_length_refuses_to_be_absurd(self, recipes, stub_model):
        stub_model(_full_period(_monday(), 7))
        with pytest.raises(ValueError):
            agent.generate_weekly_plan(_monday(), day_count=0)
        with pytest.raises(ValueError):
            agent.generate_weekly_plan(_monday(), day_count=tools.MAX_PERIOD_DAYS + 1)

    def test_period_start_and_skip_days_are_refused_together(self, recipes, stub_model):
        # They are two ways of saying where content begins. Combining them
        # would generate for days neither caller asked for, silently.
        stub_model(_full_period(_monday(), 7))
        with pytest.raises(ValueError, match="not both"):
            agent.generate_weekly_plan(_monday(), period_start=_thursday(), skip_days=2)


# ---------- the anchor ----------

class TestRhythmAnchoredDefault:
    """
    Emily's decision, 2026-09-05 (Loop Board "Rhythm: rename 'When should
    your week be ready?' ... and decide what 'As we go' actually does"):
    planning_anchor is a WEEKDAY the plan/list are final by, not an
    abstract cadence, and the week it seeds starts the NEXT morning —
    "ready by Friday" is a Saturday-start week. 'as_we_go' is the one
    non-weekday escape and now means something concrete: short horizons,
    three days at a time, rather than a full week.
    """
    def _set_anchor(self, value: str):
        tools.set_planning_anchor(value)

    def test_a_household_ready_the_sunday_before_gets_a_monday(self):
        # 'sunday' is the exact old default's new name — ready the Sunday
        # before, Monday start.
        self._set_anchor("sunday")
        suggestion = tools.suggest_planning_period()
        assert suggestion["is_monday_anchored"] is True
        assert datetime.date.fromisoformat(suggestion["start_date"]).weekday() == 0
        assert suggestion["day_count"] == 7

    def test_ready_by_friday_starts_the_week_on_saturday(self):
        # Emily's own example: "ready by Friday" means the week starts the
        # next morning.
        self._set_anchor("friday")
        suggestion = tools.suggest_planning_period()
        assert datetime.date.fromisoformat(suggestion["start_date"]).weekday() == 5  # Saturday
        assert suggestion["day_count"] == 7
        assert suggestion["planning_anchor"] == "friday"

    def test_every_weekday_anchor_starts_the_day_after_itself(self):
        for i, weekday in enumerate(tools.PLANNING_ANCHOR_WEEKDAYS):
            self._set_anchor(weekday)
            suggestion = tools.suggest_planning_period()
            assert datetime.date.fromisoformat(suggestion["start_date"]).weekday() == (i + 1) % 7, weekday

    def test_a_household_that_plans_as_it_goes_gets_a_short_horizon(self):
        # Short horizons, not a Monday-shaped week from a different start:
        # three days from today, not seven.
        self._set_anchor("as_we_go")
        suggestion = tools.suggest_planning_period()
        assert suggestion["start_date"] == datetime.date.today().isoformat()
        assert suggestion["day_count"] == 3
        assert suggestion["planning_anchor"] == "as_we_go"

    def test_a_household_that_never_answered_keeps_the_monday(self):
        # The no-op property again: every household predating the anchor
        # sees exactly the default this app has always offered.
        suggestion = tools.suggest_planning_period()
        assert suggestion["planning_anchor"] == "sunday"
        assert datetime.date.fromisoformat(suggestion["start_date"]).weekday() == 0

    def test_the_endpoint_serves_it(self, signed_in):
        self._set_anchor("as_we_go")
        res = signed_in.get("/api/week/planning-period")
        assert res.status_code == 200
        assert res.json()["start_date"] == datetime.date.today().isoformat()
        assert res.json()["day_count"] == 3


# ---------- the nudge, retimed to the household's own ready day ----------

class TestPlanningNudgeOpensBeforeTheReadyDay:
    """
    Loop Board "Rhythm: rename ... and decide what 'As we go' actually
    does": for a weekday anchor, "ready by Friday" means Friday IS the
    last day of the currently-running period (see suggest_planning_period)
    — so the existing "offer the next period from one day before its
    predecessor ends" rule (get_week_planning_nudge) already opens the
    nudge the day before the ready day, and on the ready day itself,
    without needing anchor-specific code. Pinned here with a fixed
    "today" so the day-of-week math is asserted directly rather than
    trusted by inspection.
    """
    PERIOD_START = "2026-09-05"  # a Saturday
    READY_DAY = "2026-09-11"     # the following Friday: PERIOD_START + 6 days

    class _FixedToday(datetime.date):
        _value: "datetime.date | None" = None

        @classmethod
        def today(cls):
            return cls._value

    def _pin_today(self, monkeypatch, iso_date: str):
        from app.tools import weekly_plan as _weekly_plan
        self._FixedToday._value = datetime.date.fromisoformat(iso_date)
        monkeypatch.setattr(_weekly_plan, "date", self._FixedToday)

    def _make_current_period_plan(self, recipes, stub_model):
        tools.set_planning_anchor("friday")
        stub_model(_full_period(self.PERIOD_START, 7))
        agent.generate_weekly_plan(self.PERIOD_START, day_count=7, period_start=self.PERIOD_START)

    def test_no_nudge_while_the_ready_day_is_still_a_few_days_off(self, recipes, stub_model, monkeypatch):
        self._make_current_period_plan(recipes, stub_model)
        self._pin_today(monkeypatch, "2026-09-08")  # Tuesday, 3 days before Friday
        assert tools.get_week_planning_nudge()["show"] is False

    def test_nudge_opens_the_day_before_the_ready_day(self, recipes, stub_model, monkeypatch):
        self._make_current_period_plan(recipes, stub_model)
        self._pin_today(monkeypatch, "2026-09-10")  # Thursday, the day before Friday
        nudge = tools.get_week_planning_nudge()
        assert nudge["show"] is True
        assert nudge["week_start"] == "2026-09-12"  # Saturday: the morning after Friday

    def test_nudge_stays_open_on_the_ready_day_itself(self, recipes, stub_model, monkeypatch):
        self._make_current_period_plan(recipes, stub_model)
        self._pin_today(monkeypatch, self.READY_DAY)  # Friday
        assert tools.get_week_planning_nudge()["show"] is True


# ---------- overlap takeover ----------

class TestOverlapTakeover:
    def _plan(self, stub_model, start, days, meal="Chili"):
        stub_model(_full_period(start, days, meal=meal))
        return agent.generate_weekly_plan(start, day_count=days, period_start=start)

    def test_a_period_starting_midweek_ends_the_old_plan_the_day_before(self, recipes, stub_model):
        # The common case, and the one the ticket describes in words: the
        # old plan "gracefully ends the day before".
        week = _monday()
        old = self._plan(stub_model, week, 7)
        thursday = tools.period_dates(week, 7)[3]
        new = self._plan(stub_model, thursday, 8, meal="Katsu")

        assert new["took_over"]["shortened_plan_ids"] == [old["weekly_plan_id"]]
        row = _plan_row(old["weekly_plan_id"])
        assert tools.plan_period(row) == (week, 3)          # Mon, Tue, Wed
        assert row["status"] != "retired"                    # it still has days
        assert _dates_on(old["weekly_plan_id"]) == set(tools.period_dates(week, 3))
        assert new["took_over"]["surrendered_dates"] == tools.period_dates(thursday, 4)

    def test_no_day_ends_up_with_two_plans(self, recipes, stub_model):
        week = _monday()
        self._plan(stub_model, week, 7)
        thursday = tools.period_dates(week, 7)[3]
        self._plan(stub_model, thursday, 8, meal="Katsu")

        conn = get_conn()
        rows = conn.execute(
            "SELECT date, slot, COUNT(*) AS n FROM meal_plan_entries "
            "WHERE component_category IS NULL GROUP BY date, slot HAVING n > 1"
        ).fetchall()
        conn.close()
        assert rows == [], "a day/slot pair held by two plans is exactly what the rule forbids"
        # And the survivor is unambiguous on every day of the new period.
        for day in tools.period_dates(thursday, 8):
            assert tools.get_plan_id_for_date(day) is not None

    def test_a_period_that_swallows_a_plan_whole_retires_it(self, recipes, stub_model):
        week = _monday()
        short = self._plan(stub_model, tools.period_dates(week, 7)[2], 3)
        covering = self._plan(stub_model, week, 7, meal="Katsu")

        assert covering["took_over"]["retired_plan_ids"] == [short["weekly_plan_id"]]
        row = _plan_row(short["weekly_plan_id"])
        assert row["status"] == "retired"
        assert row["day_count"] == 0
        assert _dates_on(short["weekly_plan_id"]) == set()

    def test_a_retired_plan_is_never_resolved_as_the_current_one(self, recipes, stub_model):
        # Under one-plan-per-day a retired plan has stopped being anybody's
        # answer to "what's for dinner". The fallback branch would otherwise
        # resurrect it whenever no plan covered today.
        today = datetime.date.today().isoformat()
        old = self._plan(stub_model, today, 3)
        self._plan(stub_model, today, 7, meal="Katsu")
        assert _plan_row(old["weekly_plan_id"])["status"] == "retired"

        conn = get_conn()
        current = tools._current_weekly_plan_row(conn)
        conn.close()
        assert current["id"] != old["weekly_plan_id"]

    def test_a_period_covering_the_start_of_a_later_plan_moves_it_forward(self, recipes, stub_model):
        # The mirror image of the common case: a plan already made for
        # later begins the day after the new period ends.
        week = _monday()
        later = self._plan(stub_model, tools.period_dates(week, 7)[4], 7)   # Fri, 7 days
        new = self._plan(stub_model, week, 7, meal="Katsu")                 # Mon-Sun

        assert new["took_over"]["shortened_plan_ids"] == [later["weekly_plan_id"]]
        row = _plan_row(later["weekly_plan_id"])
        resumes = tools.period_dates(week, 8)[7]   # the Monday after
        # Fri-Thu minus the Fri/Sat/Sun the new week took: Mon-Thu.
        assert tools.plan_period(row) == (resumes, 4)
        assert _dates_on(later["weekly_plan_id"]) == set(tools.period_dates(resumes, 4))

    def test_a_period_strictly_inside_another_keeps_the_longer_side(self, recipes, stub_model):
        # The one case where the household loses days they did not ask to
        # replace. A period is contiguous, so the old plan cannot survive
        # with a hole punched through it — it keeps the LONGER of the two
        # remaining runs, which is the most of their plan that can survive,
        # and the shorter is reported rather than hidden.
        week = _monday()
        days = tools.period_dates(week, 7)
        outer = self._plan(stub_model, week, 7)
        inner = self._plan(stub_model, days[2], 2, meal="Katsu")   # Wed-Thu

        took = inner["took_over"]
        # Mon-Tue (2 days) vs Fri-Sun (3 days): the three-day run wins.
        assert tools.plan_period(_plan_row(outer["weekly_plan_id"])) == (days[4], 3)
        assert took["orphaned_dates"] == days[:2]
        # Orphaned days really are unplanned — not quietly still on the old
        # plan while its period claims otherwise.
        for day in took["orphaned_dates"]:
            assert day not in _dates_on(outer["weekly_plan_id"])
        assert _dates_on(outer["weekly_plan_id"]) == set(days[4:])

    def test_a_component_plan_given_up_whole_reports_the_days_it_lost(self, recipes, monkeypatch):
        # A component_based plan's entries carry no real date, so a partial
        # overlap has no subset to surrender — it goes whole. The days it
        # held OUTSIDE the new period are then genuinely orphaned, and the
        # branch that surrenders the most days must not be the one that
        # claims to orphan none.
        week = _monday()
        tools.set_planning_mode("component_based")
        monkeypatch.setattr(agent, "generate_component_plan_llm", lambda ctx: [
            {"meal_name": "Chili", "category": "protein", "is_new_recipe": False},
        ])
        components = agent.generate_weekly_plan(week, day_count=7, period_start=week)
        tools.set_planning_mode("day_based")

        monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                            lambda ctx: _full_period(week, 3, meal="Katsu"))
        new = agent.generate_weekly_plan(week, day_count=3, period_start=week)

        assert new["took_over"]["retired_plan_ids"] == [components["weekly_plan_id"]]
        assert new["took_over"]["orphaned_dates"] == tools.period_dates(week, 7)[3:]
        # Its undated components really went, rather than surviving on a
        # plan whose period no longer claims anything.
        conn = get_conn()
        left = conn.execute(
            "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE weekly_plan_id = ?",
            (components["weekly_plan_id"],),
        ).fetchone()["n"]
        conn.close()
        assert left == 0

    def test_a_meal_cannot_be_attached_to_a_plan_on_a_day_it_does_not_own(self, recipes, stub_model):
        # The last way left to break the rule. retire_overlapping_plans
        # enforces it when a PERIOD is created; nothing stopped a single
        # chat-planned meal being written into plan A on a day plan B owns.
        week = _monday()
        old = self._plan(stub_model, week, 3)                 # Mon-Wed
        thursday = tools.period_dates(week, 7)[3]
        new = self._plan(stub_model, thursday, 4, meal="Katsu")  # Thu-Sun

        with pytest.raises(ValueError, match="isn't in weekly plan"):
            tools.plan_meal(thursday, "Chili", weekly_plan_id=old["weekly_plan_id"])
        # Inside its own period it still works exactly as before.
        assert tools.plan_meal(week, "Chili", weekly_plan_id=old["weekly_plan_id"])["entry_id"]
        assert tools.plan_meal(thursday, "Katsu", weekly_plan_id=new["weekly_plan_id"])["entry_id"]

    def test_a_day_the_model_was_not_asked_for_is_dropped_not_stored(self, recipes, monkeypatch):
        # Telling the generator the window is not the same as preventing it
        # leaving. An out-of-period meal used to be written anyway, onto a
        # plan whose period doesn't contain it: saved, rendered nowhere,
        # removable from no screen.
        week = _monday()
        stray = tools.period_dates(week, 7)[5]
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: (
            _full_period(week, 3) + [
                {"date": stray, "slot": "dinner", "meal_name": "Katsu",
                 "is_new_recipe": False, "reasoning": "unasked for"},
            ]
        ))
        plan = agent.generate_weekly_plan(week, day_count=3, period_start=week)

        assert stray not in _dates_on(plan["weekly_plan_id"])
        assert _dates_on(plan["weekly_plan_id"]) == set(tools.period_dates(week, 3))
        # And the week still generated rather than failing over the model's
        # mistake — a stray day must not cost the household their plan.
        assert tools.audit_plan_slots(plan["weekly_plan_id"])["complete"]

    def test_regenerating_the_same_period_does_not_retire_itself(self, recipes, stub_model):
        # "Try again" on a drafted week. The new plan must take over from
        # the OLD one and never appear in its own overlap set.
        week = _monday()
        first = self._plan(stub_model, week, 7)
        second = self._plan(stub_model, week, 7, meal="Katsu")

        assert second["weekly_plan_id"] != first["weekly_plan_id"]
        assert second["took_over"]["retired_plan_ids"] == [first["weekly_plan_id"]]
        assert _plan_row(second["weekly_plan_id"])["status"] == "draft"
        assert _dates_on(second["weekly_plan_id"]) == set(tools._week_dates(week))

    def test_what_was_surrendered_stays_on_the_record(self, recipes, stub_model):
        # The superseded_json pattern, borrowed from slot_needs: store the
        # WHOLE record, because the bare fact restores a lie.
        week = _monday()
        old = self._plan(stub_model, week, 7)
        thursday = tools.period_dates(week, 7)[3]
        new = self._plan(stub_model, thursday, 8, meal="Katsu")

        record = json.loads(_plan_row(old["weekly_plan_id"])["superseded_json"])
        assert record["by_plan_id"] == new["weekly_plan_id"]
        assert record["previous_period"] == {"start_date": week, "day_count": 7}
        assert record["surrendered_dates"] == tools.period_dates(thursday, 4)
        # And it reaches a screen rather than only a log line.
        assert tools.get_weekly_plan(old["weekly_plan_id"])["superseded"]["by_plan_id"] \
            == new["weekly_plan_id"]

    def test_existing_overlaps_are_reported_not_silently_migrated(self):
        # The rule is NEW. Overlapping plans are ordinary existing data, and
        # deciding at startup which of a household's real weeks to dismantle
        # is not a migration's business — see find_overlapping_plans.
        week = _monday()
        conn = get_conn()
        for _ in range(2):
            conn.execute("INSERT INTO weekly_plans (household_id, week_start_date) VALUES (1, ?)", (week,))
        conn.commit()
        conn.close()

        found = tools.find_overlapping_plans(week, 7)
        assert len(found) == 2
        assert all(f["overlap_dates"] == tools._week_dates(week) for f in found)
        # Nothing was changed by asking.
        assert all(_plan_row(f["weekly_plan_id"])["status"] == "draft" for f in found)


# ---------- what adversarial review found ----------

class TestRetiredPlansStayDead:
    """
    A retired plan is not merely hidden — it must not be reachable by any
    door that can bring it back to life. Every one of these came out of an
    adversarial review of this branch, and the first is the reason the rest
    are here: they are one mechanism failing in several places.
    """
    def _retire_one(self, stub_model, week):
        stub_model(_full_period(week, 7))
        old = agent.generate_weekly_plan(week, day_count=7, period_start=week)
        stub_model(_full_period(week, 7, meal="Katsu"))
        new = agent.generate_weekly_plan(week, day_count=7, period_start=week)
        assert _plan_row(old["weekly_plan_id"])["status"] == "retired"
        return old, new

    def test_a_retired_plan_reads_as_an_EMPTY_period_not_a_full_week(self, recipes, stub_model):
        # The root cause of the resurrection below. day_count 0 meant both
        # "surrendered everything" and, read as the legacy sentinel, SEVEN
        # DAYS. The legacy sentinel is now both columns unset together, so a
        # retired plan — which keeps its start — cannot be mistaken for one.
        week = _monday()
        old, _ = self._retire_one(stub_model, week)
        row = _plan_row(old["weekly_plan_id"])
        assert row["day_count"] == 0
        assert row["content_start_date"] == week, "a retired plan keeps its start"
        assert tools.plan_period(row) == (week, 0)
        assert tools.period_dates(*tools.plan_period(row)) == []

    def test_approving_a_week_never_resurrects_the_retired_plan(self, recipes, stub_model, signed_in):
        # Approving by week key resolved to the RETIRED row and set its
        # status back to 'approved' — clearing the one flag keeping it out
        # of every live-plan query, and putting two live plans on one week.
        week = _monday()
        old, new = self._retire_one(stub_model, week)
        assert tools.get_plan_id_for_week(week) == new["weekly_plan_id"]

        res = signed_in.post(f"/api/week/{week}/approve", json={"approved_by": "Ana"})
        assert res.status_code == 200
        assert res.json()["weekly_plan_id"] == new["weekly_plan_id"]
        assert _plan_row(old["weekly_plan_id"])["status"] == "retired"
        assert _live_day_owners() == {}, "one day, one plan"

    def test_reopening_a_week_never_resurrects_it_either(self, recipes, stub_model, signed_in):
        week = _monday()
        old, new = self._retire_one(stub_model, week)
        res = signed_in.post(f"/api/week/{week}/reopen")
        assert res.status_code == 200
        assert _plan_row(old["weekly_plan_id"])["status"] == "retired"
        assert _live_day_owners() == {}

    def test_a_day_no_live_plan_covers_answers_None(self, recipes, stub_model):
        # get_plan_id_for_date used to fall through to a FILING-KEY lookup,
        # which happily returned a retired or shortened plan for days it no
        # longer covers. slot_needs uses this: an `away` attaching to a dead
        # plan is never enforced, and the household gets shopped for a night
        # they said they were out.
        week = _monday()
        days = tools.period_dates(week, 7)
        stub_model(_full_period(week, 7))
        old = agent.generate_weekly_plan(week, day_count=7, period_start=week)
        stub_model(_full_period(week, 3, meal="Katsu"))
        agent.generate_weekly_plan(week, day_count=3, period_start=week)   # keeps Mon-Wed

        assert tools.plan_period(_plan_row(old["weekly_plan_id"])) == (days[3], 4)
        for day in days:
            found = tools.get_plan_id_for_date(day)
            assert found is not None, day
        # A day genuinely outside every plan is None, not a filing-key guess.
        assert tools.get_plan_id_for_date(tools.period_dates(week, 9)[-1]) is None


class TestTakeoverDeconflictsGlobally:
    def test_two_pre_existing_overlaps_are_not_pushed_into_a_new_clash(self, recipes, stub_model):
        # Handling each old plan against the new period ALONE shortened two
        # of them onto the same resume date — inventing five clashing days
        # that did not exist before the takeover ran.
        week = _monday()
        conn = get_conn()
        for key in (week, tools.period_dates(week, 7)[2], tools.period_dates(week, 7)[3]):
            conn.execute(
                "INSERT INTO weekly_plans (household_id, week_start_date, status) VALUES (1, ?, 'approved')",
                (key,),
            )
        conn.commit()
        conn.close()
        assert _live_day_owners(), "the fixture really is a pre-existing overlap"

        stub_model(_full_period(tools.period_dates(week, 7)[1], 3))
        agent.generate_weekly_plan(
            tools.period_dates(week, 7)[1], day_count=3,
            period_start=tools.period_dates(week, 7)[1],
        )
        assert _live_day_owners() == {}, "a generation must leave the household deconflicted"

    def test_orphaned_days_exclude_anything_another_plan_still_holds(self, recipes, stub_model):
        # The warning and the `took_over` payload are what a screen would
        # read out. Reporting a day as lost while a plan is still cooking
        # from it is worse than not reporting it at all.
        week = _monday()
        days = tools.period_dates(week, 7)
        stub_model(_full_period(week, 7))
        agent.generate_weekly_plan(week, day_count=7, period_start=week)
        stub_model(_full_period(days[5], 4, meal="Katsu"))
        agent.generate_weekly_plan(days[5], day_count=4, period_start=days[5])
        stub_model(_full_period(days[2], 2, meal="Chili"))
        new = agent.generate_weekly_plan(days[2], day_count=2, period_start=days[2])

        owners = _live_day_owners()
        assert owners == {}, owners
        for day in new["took_over"]["orphaned_dates"]:
            assert tools.get_plan_id_for_date(day) is None, f"{day} was reported lost but is planned"

    def test_a_shortened_plan_does_not_draw_days_it_gave_up(self, recipes, stub_model):
        # before_plan_start means "already gone by" everywhere else in this
        # codebase. Reusing it for "another plan owns this" had two screens
        # drawing the same dates and neither saying so.
        week = _monday()
        days = tools.period_dates(week, 7)
        stub_model(_full_period(days[1], 6))
        old = agent.generate_weekly_plan(days[1], day_count=6, period_start=days[1])
        stub_model(_full_period(week, 3, meal="Katsu"))
        new = agent.generate_weekly_plan(week, day_count=3, period_start=week)

        old_days = {d["date"] for d in tools.get_week_menu(old["weekly_plan_id"])["days"]}
        new_days = {d["date"] for d in tools.get_week_menu(new["weekly_plan_id"])["days"]}
        assert old_days & new_days == set(), sorted(old_days & new_days)


class TestPeriodEndpointsAreConsistentlyGuarded:
    def test_saving_intake_clamps_the_period_like_its_siblings(self, signed_in):
        # The one period entry point that didn't clamp: day_count went
        # straight to period_dates, spending a second building date strings
        # before surfacing an OverflowError as a 500.
        res = signed_in.post(
            f"/api/week/{_monday()}/intake", json={"night_tags": {}, "day_count": 3_000_000},
        )
        assert res.status_code == 400
        assert "between 1 and" in res.json()["detail"]

    def test_a_period_cannot_start_arbitrarily_far_from_its_filing_key(self, signed_in):
        # The Meals grid draws every day from the filing key up to the
        # period start, so an unbounded gap drew 150 empty days from a
        # 3-day plan — and then looked up needs and attendance for each.
        res = signed_in.post(
            "/api/week/2026-01-05/generate",
            json={"period_start": "2026-06-01", "day_count": 3},
        )
        assert res.status_code == 400
        res = signed_in.post(
            "/api/week/2026-06-01/generate",
            json={"period_start": "2026-01-05", "day_count": 3},
        )
        assert res.status_code == 400, "a period cannot start before its filing key either"


# ---------- grocery reconciliation ----------

class TestGroceryReconciliationOnTakeover:
    def _plan_and_approve(self, stub_model, start, days, meal):
        stub_model(_full_period(start, days, meal=meal))
        plan = agent.generate_weekly_plan(start, day_count=days, period_start=start)
        tools.approve_weekly_plan(plan["weekly_plan_id"], approved_by="Emily")
        return plan

    def _needed(self) -> dict:
        return {i["item"]: i for i in tools.list_grocery_list(status="needed")}

    def test_surrendered_days_take_their_groceries_off_the_list(self, recipes, stub_model):
        week = _monday()
        self._plan_and_approve(stub_model, week, 7, "Chili")
        assert "beans" in self._needed()

        thursday = tools.period_dates(week, 7)[3]
        new = self._plan_and_approve(stub_model, thursday, 8, "Katsu")

        # The takeover reversed the surrendered Chili days before the new
        # period's own approval added anything, so what's left is honest.
        assert "panko" in self._needed()
        assert new["took_over"]["grocery_removed"] or new["took_over"]["grocery_trimmed"]

    def test_a_line_already_bought_is_never_yanked_back(self, recipes, stub_model):
        # The rule inherited from _reverse_meal_grocery_contributions, and
        # the one that matters most: somebody owns that food now. A takeover
        # mid-shop must not empty a cart.
        week = _monday()
        self._plan_and_approve(stub_model, week, 7, "Chili")
        beans = self._needed()["beans"]
        tools.mark_grocery_item(beans["id"], "purchased")

        thursday = tools.period_dates(week, 7)[3]
        new = self._plan_and_approve(stub_model, thursday, 8, "Katsu")

        conn = get_conn()
        row = conn.execute("SELECT status FROM grocery_items WHERE id = ?", (beans["id"],)).fetchone()
        conn.close()
        assert row["status"] == "purchased", "a bought line survived the takeover"
        # And the household is TOLD, rather than the fact being swallowed —
        # after the meal is gone, this ledger is the only place it existed.
        assert "beans" in new["took_over"]["grocery_kept_bought"]

    def test_an_in_cart_line_is_left_alone_too(self, recipes, stub_model):
        week = _monday()
        self._plan_and_approve(stub_model, week, 7, "Chili")
        beans = self._needed()["beans"]
        tools.mark_grocery_item(beans["id"], "in_cart")

        thursday = tools.period_dates(week, 7)[3]
        new = self._plan_and_approve(stub_model, thursday, 8, "Katsu")

        conn = get_conn()
        row = conn.execute("SELECT status FROM grocery_items WHERE id = ?", (beans["id"],)).fetchone()
        conn.close()
        assert row["status"] == "in_cart"
        assert "beans" in new["took_over"]["grocery_kept_bought"]

    def test_the_ledger_for_surrendered_meals_is_cleared(self, recipes, stub_model):
        # Left behind, these link rows point at meals that no longer exist,
        # and a later reversal would subtract the same amount twice.
        week = _monday()
        old = self._plan_and_approve(stub_model, week, 7, "Chili")
        thursday = tools.period_dates(week, 7)[3]
        self._plan_and_approve(stub_model, thursday, 8, "Katsu")

        conn = get_conn()
        orphans = conn.execute(
            "SELECT COUNT(*) AS n FROM meal_plan_grocery_links l "
            "LEFT JOIN meal_plan_entries e ON e.id = l.meal_plan_entry_id "
            "WHERE e.id IS NULL"
        ).fetchone()["n"]
        conn.close()
        assert orphans == 0
        # The surviving prefix of the old plan keeps its own days' links.
        assert _dates_on(old["weekly_plan_id"]) == set(tools.period_dates(week, 3))

    def test_a_draft_takeover_touches_nothing_on_the_list(self, recipes, stub_model):
        # A draft never contributed, so retiring it must not subtract. This
        # is where an over-eager reversal would quietly delete lines the
        # household added by hand.
        week = _monday()
        tools.add_grocery_item("olive oil", quantity="1 bottle")
        stub_model(_full_period(week, 7))
        agent.generate_weekly_plan(week)              # draft, never approved
        thursday = tools.period_dates(week, 7)[3]
        stub_model(_full_period(thursday, 8, meal="Katsu"))
        new = agent.generate_weekly_plan(thursday, day_count=8, period_start=thursday)

        assert new["took_over"]["grocery_removed"] == []
        assert "olive oil" in self._needed()


# ---------- the streaming path ----------

class TestStreamingArbitraryPeriods:
    def test_the_stream_generates_the_period_it_was_given(self, signed_in, recipes, monkeypatch):
        start = _thursday()
        monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                            lambda context: _full_period(start, 8))
        res = signed_in.post(
            f"/api/week/{start}/generate/stream",
            json={"day_count": 8, "period_start": start},
        )
        assert res.status_code == 200
        done = [line for line in res.text.splitlines() if line.startswith("data:")][-1]
        payload = json.loads(done[len("data:"):].strip())
        assert payload["day_count"] == 8
        assert payload["period_start_date"] == start

    def test_an_impossible_period_is_a_400_not_a_stream_that_says_no(self, signed_in):
        # Validated before the StreamingResponse exists. Otherwise the
        # refusal arrives as an error frame after a 200 and a set of
        # headers — a stream that starts fine and then changes its mind.
        res = signed_in.post(f"/api/week/{_monday()}/generate/stream", json={"day_count": 400})
        assert res.status_code == 400
        assert res.headers["content-type"].startswith("application/json")
