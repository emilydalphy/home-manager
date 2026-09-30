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
    assert "wwkPost('/api/usual-week', uwSheetPayload(sheet))" in save
    assert "toastSaved(savedLine(UW_MEAL_LABELS[sheet.meal], 'saved'))" in save
    assert "showToast('That didn’t save. Try it again.');" in save
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
