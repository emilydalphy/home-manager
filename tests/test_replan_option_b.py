"""
Re-plan Option B: "Same as last week?" in onboarding's look (Emily, live
session 2026-10-18 — the approved mockup's Option B, the no-redesign gate
lifted for this card only).

Slice 1, the fix underneath: when onboarding finishes, its answers are
kept as the first period's week answers — the "last week" Plan a week
reads — so the first re-plan after onboarding opens on the summary page
instead of all five steps. Before this nothing wrote a week_intake row for
the first week, so last_intake was None for the period after it.

Pinned like tests/test_onboarding_not_now_keeps_this_week.py: Wednesday
2026-10-14, a Saturday shop, so the household's week runs Sat 10 - Fri 16
and "this week" is the three days Wed 14 - Fri 16.
"""
from __future__ import annotations

import pytest

from app import agent, tools

pytestmark = pytest.mark.today("2026-10-14 12:00")


def _finish_setup(client, start="this_week", allow=True, excited=("Thai", "Italian")):
    """The end-of-setup writes in finishSetupAndReveal's order."""
    assert client.post("/api/ai-consent", json={"allow": allow}).status_code == 200
    assert client.post("/api/onboarding/rhythm", json={"shop_day": "saturday"}).status_code == 200
    body = {"member_names": ["Ada"], "dinners_per_week": 5, "excited_about": list(excited),
            "first_plan_start": start}
    assert client.post("/api/onboarding/answers", json=body).status_code == 200


def _draft_first_week(client, monkeypatch):
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: [
        {"date": d, "slot": s, "meal_name": "Chili", "is_new_recipe": False, "reasoning": "fits"}
        for d in ("2026-10-14", "2026-10-15", "2026-10-16") for s in tools.WEEK_SLOTS
    ])
    res = client.post("/api/onboarding/generate-first-plan", json={"start": "this_week"})
    assert res.status_code == 200
    return res.json()


class TestSlice1SetupIsLastWeek:
    def test_the_first_replan_after_onboarding_has_a_last_week(self, signed_in, monkeypatch):
        _finish_setup(signed_in)
        plan = _draft_first_week(signed_in, monkeypatch)
        # The door under the first week: the period after it, at the
        # household's own length (next_period_after).
        nxt = signed_in.get("/api/week/planning-period").json()
        assert nxt["start_date"] == "2026-10-17"
        prefill = signed_in.get(f"/api/week/{nxt['start_date']}/intake?day_count={nxt['day_count']}").json()
        last = prefill["last_intake"]
        assert last is not None, "fresh out of onboarding, Plan a week would ask all five steps again"
        assert last["week_start"] == plan["week_start_date"]
        assert last["cuisines"] == ["Thai", "Italian"]
        # The first plan was drafted FROM those answers, so it says how long it ran.
        assert last["day_count"] == 3
        assert prefill["intake"] is None          # next week's own answers start empty

    def test_the_first_week_itself_is_not_left_half_answered(self, signed_in, monkeypatch):
        """The setup answers are attached to the first plan, so re-planning
        the first week is a re-plan of answered questions, not somebody's
        unfinished intake (in_flight)."""
        _finish_setup(signed_in)
        _draft_first_week(signed_in, monkeypatch)
        prefill = signed_in.get("/api/week/2026-10-14/intake?day_count=3").json()
        assert prefill["plan_exists"] is True
        assert prefill["intake"]["cuisines"] == ["Thai", "Italian"]
        assert prefill["in_flight"] is False

    def test_a_reposted_finish_adds_no_revision(self, signed_in):
        _finish_setup(signed_in)
        _finish_setup(signed_in, excited=("Mexican",))
        history = tools.get_week_intake_history("2026-10-10")
        assert [h["revision"] for h in history] == [1]
        assert history[0]["cuisines"] == ["Thai", "Italian"]

    def test_not_now_still_keeps_the_answers(self, signed_in):
        """"Not now" to sharing with Claude drafts nothing, but setup did
        finish: the answers are there for whichever week is planned next."""
        _finish_setup(signed_in, allow=False)
        assert tools.get_week_intake("2026-10-10")["cuisines"] == ["Thai", "Italian"]


# ==========================================================================
# Slice 3 (server): a change made while re-planning is for THIS week only
# (Emily's default, 2026-10-18): it rides on the week's intake revision as
# week_answers, the draft reads it, and Settings keeps the usual answer.
# ==========================================================================

from app.tools import allergen_gate, member_needs, swap_in_place as sip, usual_week  # noqa: E402

WEEK = "2026-10-17"          # the period after the first week (Sat - Fri)


def _slot(date, slot, name):
    return {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
            "ingredients": [{"item": f"{name} stuff", "qty": "1"}], "reasoning": f"{name} because",
            "food_groups": ["protein", "vegetable", "carb"]}


def _week_of(dates, dinners):
    out = []
    for i, d in enumerate(dates):
        out += [_slot(d, "breakfast", "Oats"), _slot(d, "lunch", "Wrap"), _slot(d, "dinner", dinners[i % len(dinners)])]
    return out


@pytest.fixture
def stub_model(monkeypatch):
    seen = {}

    def _stub(days):
        def fake(ctx):
            seen["ctx"] = ctx
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake)
        return seen

    names = iter(["Moussaka", "Bibimbap", "Ratatouille", "Jerk chicken", "Pierogi", "Dal", "Laksa", "Tagine"] * 4)

    def quick(context):
        return {"meal_name": next(names), "ingredients": ["Stuff"], "dish_note": "Simple.",
                "food_groups": ["protein", "vegetable", "carb"]}

    monkeypatch.setattr(allergen_gate, "quick_pick", quick)
    monkeypatch.setattr(sip, "_pick_replacement", lambda c: {
        "meal_name": next(names), "reason": "new", "ingredients": [{"item": "x", "qty": "1", "category": "pantry"}],
        "instructions": ["Cook."], "food_groups": ["protein", "vegetable", "carb"],
        "prep_time_minutes": 10, "cook_time_minutes": 10})
    return _stub


def _household():
    people = {n: tools.add_member(n)["member_id"] for n in ("Emily", "Vineeth")}
    tools.save_usual_week(variety={"dinner": "few_in_rotation"}, prep={"days": ["sunday"], "length": "longer"})
    return people


class TestThisWeekOnly:
    def test_the_change_rides_on_the_weeks_intake_and_settings_keep_the_usual(self, signed_in):
        _household()
        before = tools.get_usual_week()
        res = signed_in.post(f"/api/week/{WEEK}/intake", json={"day_count": 7, "week_answers": {
            "variety": {"dinner": "new_every_day"}, "prep": {"days": []},
            "grid": {"dinner": {"friday": ["Emily"]}},
        }})
        assert res.status_code == 200, res.text
        saved = res.json()["week_answers"]
        assert saved["variety"] == {"dinner": "new_every_day"} and saved["prep"] == {"days": []}
        emily = [m["id"] for m in before["members"] if m["name"] == "Emily"]
        assert saved["grid"]["dinner"]["friday"] == emily
        assert tools.get_usual_week() == before                       # Settings untouched
        # A later week starts from the usual answers, not from this one's:
        # "last week" carries no week_answers.
        nxt = tools.get_week_intake_prefill("2026-10-31")
        assert nxt["intake"] is None and "week_answers" not in nxt["last_intake"]
        assert nxt["usual"]["variety"]["dinner"]["choice"] == "few_in_rotation"
        assert nxt["usual"]["prep"]["days"] == ["sunday"]

    def test_a_later_revision_keeps_the_weeks_changes(self):
        _household()
        tools.save_week_intake(WEEK, week_answers={"variety": {"breakfast": "go_to_or_two"}})
        tools.save_week_intake(WEEK, moods=["Comfort food"])
        assert tools.get_week_intake(WEEK)["week_answers"] == {"variety": {"breakfast": "go_to_or_two"}}

    def test_a_malformed_change_is_refused_before_anything_is_written(self, signed_in):
        _household()
        res = signed_in.post(f"/api/week/{WEEK}/intake", json={"week_answers": {"variety": {"dinner": "lots"}}})
        assert res.status_code == 400
        assert tools.get_week_intake(WEEK) is None

    def test_the_draft_plans_the_week_as_changed(self, stub_model):
        _household()
        dates = tools.period_dates(WEEK, 7)
        friday = dates[6]
        tools.save_week_intake(WEEK, week_answers={
            "variety": {"dinner": "new_every_day"}, "prep": {"days": []},
            "grid": {"dinner": {"friday": ["Emily"]}},
        })
        seen = stub_model(_week_of(dates, ["Chili", "Tacos", "Curry", "Stew", "Pho", "Paella", "Risotto"]))
        agent.generate_weekly_plan(WEEK)
        memory = seen["ctx"]["household_memory"]
        assert memory["dinners_per_week"] == 7                 # "Something new every night", this week
        assert memory["rhythm"]["prep_days"] == []             # no prep day this week
        assert tools.get_slot_attendance(friday, "dinner")["present_names"] == ["Emily"]
        assert tools.get_slot_attendance(dates[5], "dinner")["headcount"] == 2

    def test_a_week_with_no_changes_drafts_from_the_usual_answers(self, stub_model):
        _household()
        dates = tools.period_dates(WEEK, 7)
        tools.save_week_intake(WEEK, moods=["Comfort food"])
        seen = stub_model(_week_of(dates, ["Chili", "Tacos", "Curry", "Stew"]))
        agent.generate_weekly_plan(WEEK)
        memory = seen["ctx"]["household_memory"]
        assert memory["dinners_per_week"] == 4
        assert [d["weekday"] for d in memory["rhythm"]["prep_days"]] == ["sunday"]

    def test_after_the_draft_the_menu_still_reads_the_weeks_own_grid(self, stub_model):
        """A swap, the menu's left-out meals and Today's moves read the usual
        week outside any draft; for a week changed for itself they read that
        week's answer (usual_week.week_answers_on), and the week after is
        the usual one again."""
        _household()
        dates = tools.period_dates(WEEK, 7)
        saturday = dates[0]
        tools.save_week_intake(WEEK, week_answers={"grid": {"breakfast": {"saturday": "off"}}})
        stub_model(_week_of(dates, ["Chili", "Tacos", "Curry", "Stew"]))
        agent.generate_weekly_plan(WEEK)
        assert (saturday, "breakfast") in usual_week.off_slots_on([saturday])
        assert ("2026-10-24", "breakfast") not in usual_week.off_slots_on(["2026-10-24"])

    def test_this_weeks_lunch_needs_pack_this_weeks_lunches(self):
        _household()
        monday = tools.period_dates(WEEK, 7)[2]
        assert member_needs.lunch_packing([monday]) == {}
        with usual_week.this_week({"lunch_needs": {"Emily": {"needs": ["thermos"], "days": {}}}}):
            packing = member_needs.lunch_packing([monday])
            assert [(p["name"], p["how"]) for p in packing[monday]] == [("Emily", "warm in a thermos")]
        assert member_needs.lunch_packing([monday]) == {}

    def test_a_nut_free_answer_for_this_week_only_adds_to_the_safety_list(self):
        _household()
        with usual_week.this_week({"lunch_needs": {"Vineeth": {"needs": ["nut_free"], "days": {}}}}):
            assert member_needs.nut_free_member_names() == ["Vineeth"]
        assert member_needs.nut_free_member_names() == []


# ==========================================================================
# Slices 2-4 (the page): "Same as last week?" in onboarding's look, each row
# opening onboarding's own screen, the mockup's words.
# ==========================================================================

import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

import nodeharness  # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "static"
PAGE = (STATIC / "plan-week.html").read_text(encoding="utf-8")
ONBOARDING = (STATIC / "onboarding.html").read_text(encoding="utf-8")
SCREENS = (STATIC / "setup-screens.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page's own functions")


def _extract(name: str, source: str = PAGE) -> str:
    start = source.index(f"function {name}(")
    if source[start - len("async "):start] == "async ":
        start -= len("async ")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start:j + 1]


def _var(name: str, source: str = PAGE) -> str:
    m = re.search(rf"(?m)^  var {name} = [\s\S]*?;\n", source)
    assert m, name
    return m.group(0)


def _const(name: str) -> str:
    m = re.search(rf"(?m)^const {name} = [\s\S]*?;\n", SCREENS)
    assert m, name
    return m.group(0)


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


class TestThePage:
    def test_every_visit_opens_on_same_as_last_week(self):
        """The first re-plan after onboarding included: only a prefill that
        failed keeps the page shut — no first week of five steps."""
        load = _extract("load")
        assert "var same = await sameAsLastWeek();" in load
        assert "if (!same) { $('failed').hidden = false; return; }" in load
        assert load.rstrip().endswith("showSame();\n  }")
        assert "last_intake" not in _extract("sameAsLastWeek")
        # The household's own length when the door named none — never last
        # week's (a part-week first plan would carry its three days).
        assert "} else if (!DAYS_IN_ADDRESS) {" in load and "dayCount = horizon;" in load

    def test_the_mockups_words_and_order(self):
        same = PAGE[PAGE.index('<section id="same"'):PAGE.index("</section>", PAGE.index('<section id="same"'))]
        assert '<h1 id="same-title">Same as last week?</h1>' in same
        assert '<p class="step-sub">Tap anything that&rsquo;s different.</p>' in same
        order = ['id="same-joined"', 'id="same-rows"', 'id="bring-over"']
        assert [same.index(x) for x in order] == sorted(same.index(x) for x in order)
        rows = _var("SAME_ROWS")
        labels = re.findall(r"label: '([^']+)'", rows)
        assert labels == ["Days", "Who’s eating", "Cook ahead", "Breakfast", "Weekday lunches",
                          "Dinner", "Cuisines and mood", "Anything else"]
        copy = _var("SAME_COPY")
        for line in ("go: 'Draft my week'", "crumb: '‹ Same as last week?'", "drafted: 'Already drafted'",
                     "done: 'Done'", "change: 'Change'", "changed: 'Changed'", "undo: 'Undo'",
                     "whoLine: 'Everyone starts on every meal. Tap a day to change who’s eating it.'",
                     "anythingLine: 'Just for this week. It starts blank next time.'",
                     "leanToward: 'This week, lean toward'",
                     "nothingAdded: 'Nothing yet. Starts fresh each week.'"):
            assert line in copy, line
        assert "' already has a draft. Drafting again replaces it. ' +\n            'Nothing’s on your shopping list yet.'" in _extract("fetchPeriod")
        # Gone: option A's button and the five steps' line and bars.
        shown = re.sub(r"//[^\n]*|/\*[\s\S]*?\*/|<!--[\s\S]*?-->", "", PAGE)
        assert "Plan this week" not in shown
        assert 'id="progress"' not in PAGE and "' of ' + STEP_COUNT" not in PAGE

    def test_a_change_says_so_with_undo(self):
        finish = _extract("finishChange")
        assert "await saveIntake(samePayload());" in finish
        assert "showToast(row.label + (row.were ? ' were ' : ' was ') + 'changed', {" in finish
        assert "run: function () { undoChange(before.state); }" in finish
        undo = _extract("undoChange")
        assert "restoreState(state);" in undo and "await saveIntake(samePayload());" in undo
        assert "week_answers: weekAnswers" in _extract("samePayload")
        assert "week_answers: weekAnswers" in _extract("leavePayload")


class TestTheScreensAreOnboardings:
    """One copy of each screen: the markup is onboarding.html's, lifted at
    run time; the code is setup-screens.js, which both pages load."""

    def test_both_pages_load_the_shared_code_and_onboarding_keeps_no_copy(self):
        assert '<script src="/static/setup-screens.js"></script>' in ONBOARDING
        assert '<script src="/static/setup-screens.js"></script>' in PAGE
        assert "wireSetupScreens();" in ONBOARDING
        for fn in ("renderMealsDays", "uwMealCardHtml", "buildPrepStep", "buildVarietyStep",
                   "buildLunchNeedsStep", "lunchNeedsPayload", "currentExcitedAbout", "chipWithCustom"):
            assert f"function {fn}(" in SCREENS, fn
            assert f"function {fn}(" not in ONBOARDING, fn
            assert f"function {fn}(" not in PAGE, fn

    def test_each_row_opens_a_screen_onboarding_has(self):
        screens = re.findall(r"screen: '([^']+)'", _var("SAME_ROWS"))
        assert screens == ["meals-days", "prep", "variety-breakfast", "lunch-needs", "variety-dinner",
                           "excited-about", "anything-else"]
        lifted = _var("SETUP_SCREENS")
        for name in screens + ["variety-lunch"]:
            assert f'<div id="step-{name}"' in ONBOARDING, name
            assert f"'{name}'" in lifted, name
        loader = _extract("loadSetupScreens")
        assert "Api.fetch('/static/onboarding.html'" in loader
        assert "doc.getElementById('step-' + name)" in loader
        assert "wireSetupScreens();" in loader
        # Onboarding's own titles reach the page through its markup.
        for title in ("Who&rsquo;s eating, and when?", "Do you like to cook ahead?",
                      "How many different lunches in a week?", "What does each person need for weekday lunches?",
                      "What are you excited to eat more of lately?", "Anything else I should know?"):
            assert title in ONBOARDING, title

    @_needs_node
    def test_onboardings_stylesheet_is_scoped_to_the_host(self):
        script = "\n".join(_extract(n) for n in ("topLevel", "scopeSelector")) + """
var S = '#setup-host';
console.log(JSON.stringify([
  scopeSelector('.uw-day.is-some', S),
  scopeSelector(':where(:root:not([data-theme="light"])) .uw-day.is-some', S),
  scopeSelector('body', S),
  scopeSelector('body.reveal-active .reveal-days', S),
  scopeSelector(':root', S),
  topLevel('.a, :is(.b, .c) .d', ','),
]));"""
        assert _node(script) == [
            "#setup-host .uw-day.is-some",
            ':where(:root:not([data-theme="light"])) #setup-host .uw-day.is-some',
            None, None, None,
            [".a", " :is(.b, .c) .d"],
        ]

    def test_the_hooks_leave_onboarding_as_it_was(self):
        """Plan a week's hooks are optional: without them the grid is
        Monday to Sunday, the picker says the weekday and there is no extra
        row — onboarding defines none."""
        card = _extract("uwMealCardHtml", SCREENS)
        assert "typeof uwExtraRowHtml === 'function'" in card
        assert "typeof uwColumnOrder === 'function'" in _extract("uwColumns", SCREENS)
        for hook in ("uwColumnOrder", "uwPickerLabel", "uwExtraRowHtml", "uwSummaryLine"):
            assert hook not in ONBOARDING, hook
            assert f"window.{hook} = function" in PAGE, hook


class TestTheRowsWords:
    def _harness(self, body: str) -> object:
        script = (
            "function esc(s) { return String(s); }\n"
            + _const("UW_MEALS") + _extract("uwCleanCell", SCREENS) + "\n"
            + _var("SAME_COPY") + _var("EXTRA_TAGS")
            + "\n".join(_extract(n) for n in ("isoLocal", "addDaysIso", "weekdayName", "joinWords", "capitalize",
                                               "titleDay", "weekdayIndex", "daysValue", "whoValue", "cookValue",
                                               "cuisinesValue"))
            + "\nvar answers = { night_tags: {}, guest_counts: {} }; var awayRanges = []; var names = ['Emily', 'Vineeth'];\n"
            "function memberNames() { return names; }\n"
            "var DATES = ['2026-10-19','2026-10-20','2026-10-21','2026-10-22','2026-10-23','2026-10-24','2026-10-25'];\n"
            "function periodDates() { return DATES; }\n"
            "function all() { return ['all','all','all','all','all','all','all']; }\n"
            + body
        )
        return _node(script)

    @_needs_node
    def test_the_mockups_rows(self):
        got = self._harness("""
var cells = { breakfast: all(), lunch: all(), dinner: all() };
var plain = whoValue(cells);
cells.dinner[4] = ['Emily'];
answers.night_tags['2026-10-23'] = ['rush'];
answers.guest_counts['2026-10-24'] = { adults: 2, children: 0 };
var changed = whoValue(cells);
names = ['Emily']; answers = { night_tags: {}, guest_counts: {} }; var one = whoValue({ breakfast: all(), lunch: all(), dinner: all() });
names = ['A', 'B', 'C']; answers = { night_tags: {}, guest_counts: {} };
var off = { breakfast: all(), lunch: all(), dinner: all() };
[0, 1, 2, 3, 4].forEach(function (i) { off.breakfast[i] = 'off'; });
console.log(JSON.stringify({
  days: daysValue('2026-10-19', 7), one: daysValue('2026-10-23', 1),
  plain: plain, changed: changed, single: one, offDays: whoValue(off),
  cook: cookValue(['sunday']), twoCook: cookValue(['sunday', 'wednesday']), noCook: cookValue([]),
  cuisines: cuisinesValue(['Thai', 'Italian', 'Mexican'], ['Something warm']),
  many: cuisinesValue(['Thai', 'Italian', 'Mexican', 'Greek'], []), none: cuisinesValue([], [])
}));""")
        assert got == {
            "days": "Mon Oct 19 → Sun Oct 25 · 7 days",
            "one": "Fri Oct 23 · 1 day",
            "plain": "Both of you, every meal",
            "changed": "Fri dinner: just Emily · Fri short on time · Sat +2 guests",
            "single": "Every meal",
            "offDays": "Breakfast: Sat and Sun",
            "cook": "Sunday, up to 2 hours",
            "twoCook": "Sunday and Wednesday, up to 2 hours",
            "noCook": "No, I cook as I go",
            "cuisines": "Thai, Italian, Mexican · Something warm",
            "many": "Thai, Italian, Mexican +1",
            "none": "Nothing picked",
        }

    @_needs_node
    def test_a_changed_row_is_tinted_and_says_so(self):
        values = [
            {"days": "d", "who": "Both of you, every meal", "cook": "Sunday, up to 2 hours", "breakfast": "b",
             "lunches": "l", "dinner": "A few in rotation", "cuisines": "c", "anything": "a"},
            {"days": "d", "who": "Both of you, every meal", "cook": "Sunday, up to 2 hours", "breakfast": "b",
             "lunches": "Both packed · Meal prep ahead · Thursday prep for Friday", "dinner": "A few in rotation",
             "cuisines": "c", "anything": "a"},
        ]
        script = (
            "var els = {}; function $(id) { return els[id] || (els[id] = { innerHTML: '', querySelectorAll: function () { return []; } }); }\n"
            "function esc(s) { return String(s); }\n"
            "var sameBaseline = null; var seq = " + json.dumps(values) + "; var n = 0;\n"
            "function sameValues() { return seq[n++]; }\n"
            + _var("SAME_ROWS") + _var("SAME_COPY") + _extract("renderSame") + "\n"
            "renderSame(); var first = $('same-rows').innerHTML; renderSame();\n"
            "console.log(JSON.stringify({ first: first, second: $('same-rows').innerHTML }));\n"
        )
        got = _node(script)
        assert got["first"].count('class="same-row"') == 8
        assert got["second"].count("same-row changed") == 1
        assert 'class="same-row changed" data-row="lunches"' in got["second"]
        assert "Weekday lunches · Changed" in got["second"]

    @_needs_node
    def test_changed_back_is_no_change(self):
        """A household-level answer is kept for this week only while it
        differs from the usual one."""
        script = (
            "var weekAnswers = {};\n" + _extract("keepWeek") + """
keepWeek('prep', { days: [] }, { days: ['sunday'] });
var changed = JSON.stringify(weekAnswers);
keepWeek('prep', { days: ['sunday'] }, { days: ['sunday'] });
console.log(JSON.stringify({ changed: changed, back: weekAnswers }));""")
        assert _node(script) == {"changed": '{"prep":{"days":[]}}', "back": {}}


def test_the_copy_lists_other_lines():
    """Slice 4: the mockup's "Exact copy changes" not pinned above."""
    gate = PAGE[PAGE.index('id="away-gate"'):PAGE.index("</button>", PAGE.index('id="away-gate"'))]
    assert '<span class="card-btn-title">Away for a few nights?</span>' in gate
    assert '<span class="card-btn-line">I&rsquo;ll skip those meals altogether.</span>' in gate
    assert "if (kind === 'leftovers') return 'Last night’s dinner';" in _extract("lunchLine")
    # "Fri 23 · who's eating?" over the picker; Short on time / Got time /
    # + Guests under it, for this week only.
    assert "window.uwPickerLabel = function (i) { var d = firstDateFor(i); return d ? dayLabel(d)" in PAGE
    assert "var EXTRA_TAGS = [{ key: 'rush', label: 'Short on time' }, { key: 'unrushed', label: 'Got time' }];" in PAGE
    assert "var GUESTS_ADD = '+ Guests';" in PAGE
    # "Surprise me" is the first mood chip, under "This week, lean toward".
    assert "chipWithCustom(moods, [SURPRISE_MOOD].concat(MOODS), true);" in _extract("openCuisinesScreen")
