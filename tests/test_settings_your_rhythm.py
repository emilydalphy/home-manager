"""
Settings › Your rhythm (Emily's approved mockups, 2026-09-30): each meal's
day row and variety, a sheet to change them, Snacks / Dinner time / Prep
day rows; the "Different dishes a week" steppers are gone. The page's own
functions run under node (tests/test_onboarding_go_back.py's harness
helpers); the saves go through the real route.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("test_onboarding_go_back", _HERE / "test_onboarding_go_back.py")
_go_back = importlib.util.module_from_spec(_spec)
sys.modules["test_onboarding_go_back"] = _go_back
_spec.loader.exec_module(_go_back)
_needs_node, _run = _go_back._needs_node, _go_back._run

SHELL_JS = (_HERE.parent / "static" / "shell.js").read_text()
SHELL_CSS = (_HERE.parent / "static" / "shell.css").read_text()
_WEEK = {d: "everyone" for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}


# ---------- Settings › Your rhythm ----------


def _settings_harness() -> str:
    start = SHELL_JS.index("  var UW_MEALS = [")
    end = SHELL_JS.index("  // A cell as the server sends it")
    fns = ["uwFromServer", "uwToServer", "uwCells", "uwDaysOn", "uwNames", "uwWho", "uwCellClass", "uwCellAria",
           "uwDayRowHtml", "uwChoiceDishes", "uwVarietyLine", "uwPrepDaysLine", "uwSnacksLabel", "uwSetRowHtml",
           "wwkUsualWeekHtml", "uwPickerOptions", "uwApplyPick", "uwResultLine", "uwSheetBodyHtml", "uwSheetPayload",
           "uwPrepDayNames", "uwHasPrep", "uwQuickOn", "uwApplyQuick",
           "escapeHtml", "wwkChip", "wwkNote"]

    def lift(name):
        s = SHELL_JS.index(f"function {name}(")
        i = SHELL_JS.index("{", s)
        depth, j = 0, i
        while True:
            if SHELL_JS[j] == "{":
                depth += 1
            elif SHELL_JS[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        return SHELL_JS[s: j + 1]

    dinner = SHELL_JS[SHELL_JS.index("  var WWK_DINNER_WINDOW = ["):]
    dinner = dinner[: dinner.index("];") + 2]
    return SHELL_JS[start:end] + "\n" + dinner + "\n" + "\n".join(lift(n) for n in fns) + "\n"


def _usual_week_json(answered: bool) -> dict:
    choices = {
        "breakfast": [{"key": "go_to_or_two", "dishes": 2}, {"key": "few_in_rotation", "dishes": 3}, {"key": "new_every_day", "dishes": "days"}],
        "lunch": [{"key": "last_nights_dinner", "dishes": "leftovers"}, {"key": "meal_prep_ahead", "dishes": 2, "needs_prep_day": True},
                  {"key": "few_in_rotation", "dishes": 3}, {"key": "new_every_day", "dishes": "days"}],
        "dinner": [{"key": "cook_big_eat_twice", "dishes": 2}, {"key": "few_in_rotation", "dishes": 4}, {"key": "new_every_day", "dishes": "days"}],
    }
    return {
        "answered": answered,
        "members": [{"id": 1, "name": "Emily"}, {"id": 2, "name": "Greg"}],
        "grid": {"breakfast": _WEEK, "lunch": _WEEK, "dinner": dict(_WEEK, thursday=[2], friday="off")},
        "snacks_per_day": 2,
        "prep": {"days": [] if not answered else ["sunday"], "length": None},
        "variety": {
            "breakfast": {"choice": "few_in_rotation" if answered else None, "dishes": 3 if answered else 7, "days_on": 7},
            "lunch": {"choice": "meal_prep_ahead" if answered else None, "dishes": 2 if answered else 7, "days_on": 7},
            "dinner": {"choice": "few_in_rotation" if answered else None, "dishes": 4 if answered else 6, "days_on": 6},
        },
        "variety_choices": choices,
    }


@_needs_node
def test_your_rhythm_shows_each_meals_days_and_variety():
    out = _run(_settings_harness() + """
var uwState = { data: %s, sheet: null, row: null };
console.log(JSON.stringify(wwkUsualWeekHtml({ rhythm: { dinner_window: '6_8' } })));
""" % json.dumps(_usual_week_json(True)))
    assert "The meals I plan each week, who’s eating them, and how much they change." in out
    assert "Variety: A few in rotation · 3 different" in out
    assert "Variety: Meal prep ahead · 2 different" in out
    assert "Variety: A few in rotation · 4 different" in out
    assert out.count(">Change</button>") == 3
    assert 'class="uw-day is-some" data-wwk="uw-open" data-value="dinner" aria-label="Thursday dinner, Greg"' in out
    assert 'aria-label="Friday dinner, not planned"><b>F</b><span>–</span>' in out
    assert out.index("Snacks") < out.index("Dinner time") < out.index("Prep day")
    assert "2 a day ›" in out and "6–8 ›" in out and "Sunday ›" in out


@_needs_node
def test_a_household_that_never_answered_sees_a_number_and_nothing_picked():
    out = _run(_settings_harness() + """
var data = %s;
var uwState = { data: data, sheet: null, row: null };
var html = wwkUsualWeekHtml({ rhythm: {} });
var sheet = uwSheetBodyHtml(data, { meal: 'dinner', cells: uwCells(data, 'dinner'), choice: null, open: -1 });
console.log(JSON.stringify({ html: html, sheet: sheet }));
""" % json.dumps(_usual_week_json(False)))
    assert "Variety: 7 different" in out["html"] and "Variety: 6 different" in out["html"]
    assert "A few in rotation" not in out["html"]
    assert "is-on" not in out["sheet"].split("How much variety")[1].split("uw-sum")[0], "nothing is picked"
    assert "Meal prep ahead" not in _run(_settings_harness() + """
var data = %s;
console.log(JSON.stringify(uwSheetBodyHtml(data, { meal: 'lunch', cells: uwCells(data, 'lunch'), choice: null, open: -1 })));
""" % json.dumps(_usual_week_json(False))), "no prep day, no Meal prep ahead"


@_needs_node
def test_the_meal_sheet_edits_and_sends_one_meal():
    out = _run(_settings_harness() + """
var data = %s;
var members = data.members;
var sheet = { meal: 'dinner', cells: uwCells(data, 'dinner'), choice: 'few_in_rotation', open: 0 };
var body = uwSheetBodyHtml(data, sheet);
sheet.cells[0] = uwApplyPick(sheet.cells[0], 'off', members);
sheet.cells[2] = uwApplyPick(sheet.cells[2], 'just:1', members);
sheet.choice = 'cook_big_eat_twice';
console.log(JSON.stringify({ body: body, payload: uwSheetPayload(sheet),
  line: uwResultLine(data, 'dinner', sheet.cells, sheet.choice),
  lunch: uwResultLine(data, 'lunch', uwCells(data, 'lunch'), 'last_nights_dinner'),
  prep: uwResultLine(data, 'lunch', uwCells(data, 'lunch'), 'meal_prep_ahead'),
  off: uwResultLine(data, 'breakfast', uwCells(data, 'breakfast').map(function () { return 'off'; }), null) }));
""" % json.dumps(_usual_week_json(True)))
    body = out["body"]
    for words in ("Which nights, and who’s eating", "Mon · who’s eating?", "Everyone", "Just Emily", "Just Greg",
                  "Don’t plan", "How much variety", "Cook big, eat twice", "Something new every night",
                  "Starts with your next plan. This week stays as it is.", ">Save</button>"):
        assert words in body, words
    assert out["payload"] == {
        "grid": {"dinner": {"monday": "off", "tuesday": "everyone", "wednesday": [1], "thursday": [2],
                            "friday": "off", "saturday": "everyone", "sunday": "everyone"}},
        "variety": {"dinner": "cook_big_eat_twice"},
    }
    assert out["line"] == "2 different dinners over 5 nights."
    assert out["lunch"] == "Lunch is last night’s dinner, 7 days a week."
    assert out["prep"] == "2 different lunches over 7 days, made on Sunday."
    assert out["off"] == "No breakfast planned."


def test_the_sheet_saves_through_the_usual_week_route_and_names_the_meal():
    save = SHELL_JS[SHELL_JS.index("async function uwSaveSheet("):]
    save = save[: save.index("\n  }\n") + 4]
    assert "uwPost(uwSheetPayload(sheet))" in save
    assert "toastSaved(savedLine(UW_MEAL_LABELS[sheet.meal], 'saved'))" in save
    # A failure is said in the sheet (the server's words for a 400) —
    # see test_a_400_is_shown_plainly_in_the_sheet below.
    assert "sheet.error = (err && err.userMessage) || 'That didn’t save. Try it again.';" in save
    assert "#uw-sheet {" in SHELL_CSS and "#uw-sheet[hidden]" in SHELL_CSS


def test_the_usual_week_route_takes_what_the_sheet_sends(signed_in):
    signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Emily", "age_group": "adult"}, {"name": "Greg", "age_group": "adult"}],
        "pets": [], "goals": "",
    })
    before = signed_in.get("/api/usual-week").json()
    assert before["answered"] is False
    greg = next(m["id"] for m in before["members"] if m["name"] == "Greg")
    res = signed_in.post("/api/usual-week", json={
        "grid": {"dinner": dict(_WEEK, monday="off", thursday=[greg])},
        "variety": {"dinner": "cook_big_eat_twice"},
    })
    assert res.status_code == 200, res.text
    after = res.json()
    assert after["grid"]["dinner"]["monday"] == "off" and after["grid"]["dinner"]["thursday"] == [greg]
    assert after["variety"]["dinner"] == {"choice": "cook_big_eat_twice", "dishes": 2, "days_on": 6}
    assert signed_in.post("/api/usual-week", json={"snacks_per_day": 1}).json()["snacks_per_day"] == 1


def test_the_different_dishes_steppers_are_gone_from_settings():
    assert "wwkLead('Different dishes a week')" not in SHELL_JS
    assert "WWK_COUNTS" not in SHELL_JS
    assert "field: 'dinners_per_week'" not in SHELL_JS and "field: 'breakfasts_per_week'" not in SHELL_JS
    assert "label: 'Snacks a day'" not in SHELL_JS


# ---------- review round (2026-09-30): the card's remaining asks ----------

def _lift(name: str) -> str:
    s = SHELL_JS.index(f"function {name}(")
    if SHELL_JS[max(0, s - 6):s] == "async ":
        s -= 6
    i = SHELL_JS.index("{", s)
    depth, j = 0, i
    while True:
        if SHELL_JS[j] == "{":
            depth += 1
        elif SHELL_JS[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return SHELL_JS[s: j + 1]


@_needs_node
def test_the_quick_picks_set_the_row_and_say_which_is_true():
    out = _run(_settings_harness() + """
var data = %s;
var sheet = { meal: 'dinner', cells: uwCells(data, 'dinner'), choice: 'few_in_rotation', open: -1 };
var seen = { first: uwSheetBodyHtml(data, sheet) };
UW_QUICK.forEach(function (q) {
  sheet.cells = uwApplyQuick(q);
  seen[q.key] = [uwSheetPayload(sheet).grid.dinner, UW_QUICK.filter(function (x) { return uwQuickOn(sheet.cells, x); }).map(function (x) { return x.key; })];
});
sheet.cells = uwApplyQuick(UW_QUICK[1]);
seen.weekdaysHtml = uwSheetBodyHtml(data, sheet);
sheet.open = 2;
seen.openHtml = uwSheetBodyHtml(data, sheet);
console.log(JSON.stringify(seen));
""" % json.dumps(_usual_week_json(True)))
    for label in (">Every day<", ">Weekdays<", ">Weekends<", ">None<"):
        assert label in out["first"]
    assert 'uw-q" data-uw="quick" data-value="every" aria-pressed="false"' in out["first"], "Friday is off: not every day"
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    assert out["every"] == [{d: "everyone" for d in days}, ["every"]]
    assert out["weekdays"] == [{**{d: "everyone" for d in days[:5]}, "saturday": "off", "sunday": "off"}, ["weekdays"]]
    assert out["weekends"] == [{**{d: "off" for d in days[:5]}, "saturday": "everyone", "sunday": "everyone"}, ["weekends"]]
    assert out["none"] == [{d: "off" for d in days}, ["none"]]
    assert 'uw-q is-on" data-uw="quick" data-value="weekdays" aria-pressed="true">Weekdays<' in out["weekdaysHtml"]
    assert "4 different dinners over 5 nights." in out["weekdaysHtml"]
    # While a day is being chosen for, the quick picks step aside for it.
    assert "Wed · who’s eating?" in out["openHtml"] and ">Weekdays<" not in out["openHtml"]


@_needs_node
def test_more_than_two_people_get_everyone_a_toggle_each_and_dont_plan():
    data = _usual_week_json(True)
    data["members"].append({"id": 3, "name": "Sam"})
    out = _run(_settings_harness() + """
var data = %s;
var members = data.members;
var sheet = { meal: 'dinner', cells: uwCells(data, 'dinner'), choice: null, open: 0 };
var html = uwSheetBodyHtml(data, sheet);
var a = uwApplyPick('all', 'toggle:2', members);
var b = uwApplyPick(uwApplyPick(a, 'toggle:1', members), 'toggle:3', members);
var c = uwApplyPick([1, 3], 'toggle:2', members);
console.log(JSON.stringify({ html: html, a: a, b: b, c: c }));
""" % json.dumps(data))
    html = out["html"]
    for label in (">Everyone<", ">Emily<", ">Greg<", ">Sam<", ">Don’t plan<"):
        assert label in html, label
    assert "Just " not in html
    assert out["a"] == [1, 3]
    assert out["b"] == "off", "nobody left is Don't plan"
    assert out["c"] == "all", "everybody is Everyone"


@_needs_node
def test_meal_prep_ahead_is_hidden_without_a_prep_day_even_when_it_was_the_pick():
    data = _usual_week_json(True)
    data["prep"] = {"days": [], "length": None}
    out = _run(_settings_harness() + """
var data = %s;
console.log(JSON.stringify(uwSheetBodyHtml(data, { meal: 'lunch', cells: uwCells(data, 'lunch'), choice: 'meal_prep_ahead', open: -1 })));
""" % json.dumps(data))
    assert "Meal prep ahead" not in out
    for label in ("Last night’s dinner", "A few in rotation", "Something new every day"):
        assert label in out


@_needs_node
def test_the_prep_day_row_says_the_day_and_how_long():
    data = _usual_week_json(True)
    data["prep"] = {"days": ["sunday"], "length": "longer"}
    out = _run(_settings_harness() + """
var uwState = { data: %s, sheet: null, row: null };
var a = wwkUsualWeekHtml({ rhythm: {} });
uwState.data.prep = { days: ['sunday', 'wednesday'], length: 'hour' };
console.log(JSON.stringify([a, wwkUsualWeekHtml({ rhythm: {} })]));
""" % json.dumps(data))
    assert "Sunday (a longer stretch) ›" in out[0]
    assert "Sunday and Wednesday (about an hour) ›" in out[1]


@_needs_node
def test_prep_days_edits_reach_the_usual_week_row():
    out = _run(_settings_harness() + _lift("uwSyncPrep") + """
var prefsState = { memory: { rhythm: { prep_days: [{ weekday: 'saturday', minutes: 120 }] } } };
function wwkMem() { return prefsState.memory; }
var uwState = { data: %s };
uwSyncPrep();
var one = uwState.data.prep;
prefsState.memory.rhythm.prep_days = [];
uwSyncPrep();
console.log(JSON.stringify([one, uwState.data.prep]));
""" % json.dumps(_usual_week_json(True)))
    assert out == [{"days": ["saturday"], "length": "longer"}, {"days": [], "length": None}]
    render = _lift("wwkRenderSection")
    assert "if (key === 'rhythm') uwSyncPrep();" in render
    assert "if (key === 'prep-days') wwkRenderSection('rhythm');" in render


def _save_harness(status: int, body: dict, meal: str, choice) -> str:
    return _settings_harness() + _lift("uwPost") + _lift("uwSaveSheet") + """
var rendered = [], toasts = [], closed = 0, posted = [];
var uwState = { data: %s, sheet: null, row: null };
uwState.sheet = { meal: %s, cells: uwCells(uwState.data, %s), choice: %s, open: -1, busy: false, error: '' };
var Api = { fetch: async function (path, init) {
  posted.push([path, JSON.parse(init.body)]);
  return { ok: %s, status: %d, json: async function () { return %s; } };
} };
function renderUwSheet() { rendered.push(uwSheetBodyHtml(uwState.data, uwState.sheet)); }
function closeUwSheet() { closed++; uwState.sheet = null; }
function wwkRenderSection() {}
function wwkFlashSaved() {}
function savedLine(t, v) { return t + ' was ' + v; }
function toastSaved(t) { toasts.push(t); }
function showToast(t) { toasts.push(t); }
uwSaveSheet().then(function () {
  console.log(JSON.stringify({ rendered: rendered, toasts: toasts, closed: closed, posted: posted, sheet: uwState.sheet }));
});
""" % (json.dumps(_usual_week_json(True)), json.dumps(meal), json.dumps(meal), json.dumps(choice),
       "true" if status < 400 else "false", status, json.dumps(body))


@_needs_node
def test_a_400_is_shown_plainly_in_the_sheet():
    message = "“Meal prep ahead” needs a prep day — pick the day you prep, or another lunch choice."
    out = _run(_save_harness(400, {"detail": message}, "lunch", "meal_prep_ahead"))
    assert out["closed"] == 0 and out["toasts"] == []
    assert f'<p class="uw-error" role="alert">{message}</p>' in out["rendered"][-1]
    assert out["sheet"]["busy"] is False and out["sheet"]["choice"] == "meal_prep_ahead"
    other = _run(_save_harness(500, {"detail": "Server error: boom"}, "lunch", "few_in_rotation"))
    assert '<p class="uw-error" role="alert">That didn’t save. Try it again.</p>' in other["rendered"][-1]
    assert "boom" not in other["rendered"][-1]


@_needs_node
def test_a_good_save_closes_the_sheet_and_names_the_meal():
    out = _run(_save_harness(200, _usual_week_json(True), "dinner", "few_in_rotation"))
    assert out["closed"] == 1 and out["toasts"] == ["Dinner was saved"]
    assert out["posted"][0][0] == "/api/usual-week"
    assert out["posted"][0][1]["variety"] == {"dinner": "few_in_rotation"}


def test_escape_closes_the_meal_sheet_not_what_we_know_under_it():
    build = _lift("buildUwSheet")
    assert "if (e.key === 'Escape' && uwSheetEl && !uwSheetEl.hidden) { e.stopPropagation(); closeUwSheet(); }" in build
    assert "}, true);" in build, "caught on the way down, before the Kitchen sheet's listener"


def test_the_last_prep_day_goes_through_the_usual_week_and_adopts_its_reply():
    """rhythm-week-grid 8b8b3de: clearing the prep days turns a stored
    "Meal prep ahead" lunch into "A few in rotation" on the server rather
    than refusing. The last prep day goes through /api/usual-week so its
    reply, lunch choice and all, lands on Your rhythm."""
    toggle = _lift("wwkTogglePrepDay")
    assert "return uwPost({ prep: { days: [] } });" in toggle
    reply = _usual_week_json(True)
    reply["prep"] = {"days": [], "length": None}
    reply["variety"]["lunch"] = {"choice": "few_in_rotation", "dishes": 3, "days_on": 7}
    out = _run(_settings_harness() + _lift("wwkTogglePrepDay") + _lift("wwkPrepPayload") + """
var WWK_PREP_DAYS = [{ key: 'sunday' }, { key: 'monday' }, { key: 'tuesday' }, { key: 'wednesday' },
  { key: 'thursday' }, { key: 'friday' }, { key: 'saturday' }];
var prefsState = { memory: { rhythm: { prep_days: [{ weekday: 'sunday', minutes: 60 }] } } };
function wwkMem() { return prefsState.memory; }
var uwState = { data: %s, sheet: null, row: null };
var calls = [];
function uwPost(body) { calls.push(body); return %s; }
function wwkSaveRhythm() { calls.push('rhythm route'); }
function wwkCommit(section, apply, request, adopt) { apply(); adopt(request()); }
wwkTogglePrepDay('sunday');
console.log(JSON.stringify({ calls: calls, prep: prefsState.memory.rhythm.prep_days,
  html: wwkUsualWeekHtml({ rhythm: {} }) }));
""" % (json.dumps(_usual_week_json(True)), json.dumps(reply)))
    assert out["calls"] == [{"prep": {"days": []}}]
    assert out["prep"] == []
    assert "Variety: A few in rotation · 3 different" in out["html"]
    assert "Meal prep ahead" not in out["html"]
    # And What we know's own saves still say a refusal in its words.
    assert "if (err && err.userMessage) showToast(err.userMessage);" in _lift("wwkCommit")


def test_clearing_the_prep_days_moves_lunch_off_meal_prep_ahead(signed_in):
    res = signed_in.post("/api/usual-week", json={"variety": {"lunch": "meal_prep_ahead"},
                                                  "prep": {"days": ["sunday"], "length": "hour"}})
    assert res.status_code == 200, res.text
    res = signed_in.post("/api/usual-week", json={"prep": {"days": []}})
    assert res.status_code == 200, res.text
    assert res.json()["variety"]["lunch"]["choice"] == "few_in_rotation"
    assert res.json()["prep"]["days"] == []


def test_the_prep_check_answers_400_in_words(signed_in):
    """Picking "Meal prep ahead" with no prep day is still refused, in words
    the sheet shows as they are (a stale sheet is the only way to send it)."""
    signed_in.post("/api/usual-week", json={"prep": {"days": []}})
    res = signed_in.post("/api/usual-week", json={"variety": {"lunch": "meal_prep_ahead"}})
    assert res.status_code == 400
    assert "needs a prep day" in res.json()["detail"]


def test_the_some_of_you_cell_reads_light_beside_the_dark_everyone_cell_in_dark_mode():
    """Emily, 2026-09-30: on dark, "some of you" (--celadon-tint) was 1.10:1
    from "everyone" (--spruce). Scoped to the day row — the tint token is
    shared — it becomes a light chip: --celadon fill, --on-accent-ink ink
    (Rule 1). Light mode is as it was."""
    rule = "background: var(--celadon); border-color: var(--celadon); color: var(--on-accent-ink); }"
    assert f':where(:root:not([data-theme="light"])) .uw-day.is-some {{ {rule}' in SHELL_CSS
    assert f':where(:root[data-theme="dark"]) .uw-day.is-some {{ {rule}' in SHELL_CSS
    # Both after the base rule, so they win at equal specificity.
    base = SHELL_CSS.index(".uw-day.is-some { background: var(--celadon-tint); border-color: var(--celadon-edge); color: var(--ink-on-celadon); }")
    assert base < SHELL_CSS.index(':where(:root:not([data-theme="light"])) .uw-day.is-some')
    # The shared token itself is untouched.
    theme = (_HERE.parent / "static" / "theme.css").read_text()
    assert "--celadon-tint:   #1C3B2C;" in theme and "--celadon-tint:   #E2EDE5;" in theme
