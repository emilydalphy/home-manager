"""
Two fixes from the 2026-10-02 QA walk as a new household.

1. The first-week reveal showed ONE snack a day when the plan has two: the
   day's slots were keyed by slot name, so the second snack overwrote the
   first (static/onboarding.html revealPutSlot / groupRevealMealsByDay /
   revealDaysFromMenu). The page's own functions run under node, like
   test_onboarding_your_week.py.

2. "Who's this?" came up right after Approve, asking the adult who had just
   set the household up. POST /api/onboarding/household now pins the device
   to the recorded setter-up, only on the pass that recorded them and only
   when no adult is picked yet.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app import security, tools
from app.main import app

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("test_onboarding_go_back", _HERE / "test_onboarding_go_back.py")
_go_back = importlib.util.module_from_spec(_spec)
sys.modules["test_onboarding_go_back"] = _go_back
_spec.loader.exec_module(_go_back)
_needs_node, _fn, _const, _run = _go_back._needs_node, _go_back._fn, _go_back._const, _go_back._run
ONBOARDING = _go_back.ONBOARDING


def _maybe_fn(name: str) -> str:
    return _fn(name) if f"function {name}(" in ONBOARDING else ""


def _reveal_harness() -> str:
    parts = [
        _const("REVEAL_SLOT_LABELS"), _const("REVEAL_DAY_SHORT"), _const("REVEAL_DAY_LONG"),
        _const("REVEAL_SWAP_ICON"), _const("UW_WEEKDAYS"),
        "var lastFirstPlanAnswers = null;",
        "const revealStreamDays = new Map();",
    ]
    for name in [
        "escapeHtmlLocal", "revealWeekdayIndex", "formatPlanDate", "revealSlotFromMenu", "revealSlotFromPlanMeal",
        "revealSnackKey", "revealIsSnackKey", "revealPlaceSlot", "revealDaySlotKeys",
        "revealPutSlot", "revealOrderedDays", "groupRevealMealsByDay", "revealDaysFromMenu",
        "revealOpenSlotHtml", "revealSlotDishHtml", "revealMakes", "revealSlotMeta", "revealJoinWords",
        "revealDaySubsetNote", "revealDayCardHtml",
    ]:
        parts.append(_maybe_fn(name))
    parts.append("""
function renderAll(days) {
  revealStreamDays.clear();
  days.forEach(day => Object.keys(day.slots).forEach(k => revealPutSlot(day.date, day.slots[k])));
  return revealOrderedDays().map(d => revealDayCardHtml(d, revealOrderedDays()));
}
function rows(html) {
  const out = [];
  const re = /data-slot="([a-z]+)"[\\s\\S]*?reveal-slot-label">([^<]*)<[\\s\\S]*?reveal-slot-dish[^>]*>([^<]*)</g;
  let m; while ((m = re.exec(html))) out.push([m[1], m[2], m[3]]);
  return out;
}
""")
    return "\n".join(parts)


MENU_DAY = {
    "date": "2026-10-05",
    "breakfast": {"title": "Oatmeal", "state": "planned", "entry_id": 1},
    "lunch": {"title": "Wrap", "state": "planned", "entry_id": 2},
    "dinner": {"title": "Chili", "state": "planned", "entry_id": 3},
    "snacks": [
        {"title": "Apple Slices with Almond Butter", "state": "planned", "entry_id": 4},
        {"title": "Rice Cakes with Turkey Slices", "state": "planned", "entry_id": 5},
    ],
}
MENU_DAY["snack"] = MENU_DAY["snacks"][0]


@_needs_node
def test_the_reveal_draws_both_snacks_from_the_saved_menu():
    out = _run(_reveal_harness() + f"""
const days = revealDaysFromMenu([{json.dumps(MENU_DAY)}]);
const cards = renderAll(days);
console.log(JSON.stringify({{ rows: rows(cards[0]), swaps: (cards[0].match(/reveal-swap"/g) || []).length,
  entryIds: (cards[0].match(/data-entry-id="\\d+"/g) || []) }}));
""")
    dishes = [r[2] for r in out["rows"]]
    assert "Apple Slices with Almond Butter" in dishes and "Rice Cakes with Turkey Slices" in dishes
    assert sum(1 for r in out["rows"] if r[0] == "snack") == 2
    assert out["swaps"] == 5, "every planned row, both snacks included, can be swapped"
    assert out["entryIds"] == ['data-entry-id="1"', 'data-entry-id="2"', 'data-entry-id="3"',
                               'data-entry-id="4"', 'data-entry-id="5"']


@_needs_node
def test_the_reveal_draws_both_snacks_from_the_flat_fallback():
    meals = [
        {"entry_id": 4, "date": "2026-10-05", "slot": "snack", "meal": "Apple Slices", "slot_state": "planned"},
        {"entry_id": 5, "date": "2026-10-05", "slot": "snack", "meal": "Rice Cakes", "slot_state": "planned"},
        {"entry_id": 3, "date": "2026-10-05", "slot": "dinner", "meal": "Chili", "slot_state": "planned"},
    ]
    out = _run(_reveal_harness() + f"""
const days = groupRevealMealsByDay({json.dumps(meals)});
console.log(JSON.stringify(rows(renderAll(days)[0]).map(r => r[2])));
""")
    assert out == ["Chili", "Apple Slices", "Rice Cakes"]


@_needs_node
def test_a_streamed_second_snack_does_not_overwrite_the_first_and_a_resend_does_not_double_it():
    out = _run(_reveal_harness() + """
const mk = (dish) => ({ slot: 'snack', state: 'planned', dish: dish, meta: '', entryId: null, from: '', fromSlot: '', fromMeal: '' });
revealPutSlot('2026-10-05', mk('Apple Slices'));
revealPutSlot('2026-10-05', mk('Rice Cakes'));
revealPutSlot('2026-10-05', mk('Rice Cakes'));
console.log(JSON.stringify(rows(revealDayCardHtml(revealOrderedDays()[0], revealOrderedDays()))));
""")
    assert [r[2] for r in out] == ["Apple Slices", "Rice Cakes"]


@_needs_node
def test_two_snacks_are_labelled_one_and_two_and_a_lone_snack_stays_plain():
    out = _run(_reveal_harness() + f"""
const two = rows(renderAll(revealDaysFromMenu([{json.dumps(MENU_DAY)}]))[0]).filter(r => r[0] === 'snack').map(r => r[1]);
const lone = Object.assign({{}}, {json.dumps(MENU_DAY)}); lone.snacks = [lone.snacks[0]];
const one = rows(renderAll(revealDaysFromMenu([lone]))[0]).filter(r => r[0] === 'snack').map(r => r[1]);
console.log(JSON.stringify({{ two: two, one: one }}));
""")
    assert out["two"] == ["Snack 1", "Snack 2"]
    assert out["one"] == ["Snack"]


# ---------- "Who's this?" after onboarding ----------


def _client() -> TestClient:
    c = TestClient(app)
    res = c.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return c


def _onboard(c):
    res = c.post("/api/onboarding/household", json={
        "members": [{"name": "Sam", "age_group": "Adult"}, {"name": "Riley", "age_group": "Adult"}],
        "pets": [], "goals": "",
    })
    assert res.status_code == 200
    return res


def test_the_device_that_finished_onboarding_is_not_asked_who_it_is():
    c = _client()
    _onboard(c)
    who = c.get("/api/whoami").json()
    assert who["needs_pick"] is False
    assert who["member"]["name"] == "Sam"
    assert who["first_open"] is False


def test_another_device_in_the_same_household_is_still_asked():
    c = _client()
    _onboard(c)
    other = _client()
    who = other.get("/api/whoami").json()
    assert who["needs_pick"] is True and who["member"] is None


def test_an_adult_already_picked_on_the_device_is_not_replaced():
    c = _client()
    riley = tools.add_member("Riley")["member_id"]
    tools.set_member_age_group("Riley", "Adult")
    assert c.post("/api/whoami/pick", json={"member_id": riley}).status_code == 200
    _onboard(c)
    assert c.get("/api/whoami").json()["member"]["name"] == "Riley"


def test_a_second_pass_through_onboarding_does_not_pin_anyone():
    c = _client()
    _onboard(c)
    again = _client()
    _onboard(again)
    who = again.get("/api/whoami").json()
    assert who["needs_pick"] is True, "the setter-up is already on file, so this device is not assumed to be theirs"


def test_a_one_adult_household_is_still_never_asked():
    c = _client()
    c.post("/api/onboarding/household", json={"members": [{"name": "Sam", "age_group": "Adult"}], "pets": [], "goals": ""})
    who = c.get("/api/whoami").json()
    assert who["needs_pick"] is False
