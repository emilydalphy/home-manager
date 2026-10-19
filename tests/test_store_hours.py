"""
Store hours on the shop line (Loop Board "Shop: know the hours of my
preferred stores when suggesting when to shop", Emily 2026-10-18: "Yes to
all").

The quiet rule, which is most of what these pin: hours only show when they
change the answer. A store with no hours, and a store open the whole way to
the deadline, say exactly what the line said before.

Every clock below is the household's: the plan is seeded off
conftest.household_today and today_moves is handed `now` on that day.
Hours are set for all seven days unless a test is about one weekday, so
nothing here depends on which weekday the suite runs on.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from datetime import time
from pathlib import Path

import pytest

import nodeharness

from app import tools
from app.tools import digest as _digest
from app.tools import moves as _moves
from app.tools import stores as _stores
from conftest import household_today


TODAY = household_today()
ISO_TODAY = TODAY.isoformat()
WEEK_START = (TODAY - datetime.timedelta(days=2)).isoformat()
EVERY_DAY = _stores.HOURS_DAYS


def _at(hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(TODAY, time(hour, minute))


def _hours(opens="08:00", closes="21:00", **extra) -> dict:
    return {"days": {d: [opens, closes] for d in EVERY_DAY}, **extra}


def _dinner_waiting_on_the_list(store: str | None = None):
    """Tonight's dinner (19:00, 25 min of cooking: start by 6:35) with its
    one ingredient still to buy. Untagged unless `store` is given."""
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)
    tools.set_dinner_window("6_8")
    tools.add_usual_stores(["Farm Boy", "Costco"])
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Black beans", "qty": "1 lb", "category": "pantry"}],
                     prep_time_minutes=10, cook_time_minutes=15, default_servings=2)
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(ISO_TODAY, "Bean Chili", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    if store:
        for row in tools.list_grocery_list(status="needed"):
            tools.set_grocery_item_store(row["id"], store)


def _shop(now):
    hits = [m for m in tools.today_moves(now=now)["moves"] if m["kind"] == "shop"]
    return hits[0] if hits else None


# ---------- the line on Today (and so in the morning text) ----------

def test_no_hours_means_the_line_is_exactly_as_before():
    _dinner_waiting_on_the_list()
    shop = _shop(_at(9))
    assert shop["detail"] == "1 item · by 6:35"
    assert "hours_line" not in shop["stops"][0]


def test_a_store_open_the_whole_way_to_the_deadline_says_nothing():
    _dinner_waiting_on_the_list()
    tools.set_store_hours("Farm Boy", _hours("08:00", "21:00"))
    shop = _shop(_at(9))
    assert shop["detail"] == "1 item · by 6:35"
    assert "hours_line" not in shop["stops"][0]


def test_a_store_closing_before_the_deadline_moves_the_by():
    """The card's own example. The list is untagged, so it goes by the
    household's first store (Farm Boy), the card's fallback."""
    _dinner_waiting_on_the_list()
    tools.set_store_hours("Farm Boy", _hours("08:00", "17:30"))
    shop = _shop(_at(9))
    assert shop["detail"] == "1 item · by 5:30 · Farm Boy closes at 5:30"
    assert shop["time_label"] == "by 5:30"
    assert shop["window_end"] == _at(17, 30).isoformat()
    # The untagged list's one row is "Any store", so its line names the store.
    assert shop["stops"][0]["hours_line"] == "by 5:30 · Farm Boy closes at 5:30"


def test_already_closed_for_the_day_says_when_it_opens():
    _dinner_waiting_on_the_list()
    tools.set_store_hours("Farm Boy", _hours("08:00", "17:30"))
    shop = _shop(_at(17, 45))
    assert shop["detail"] == "1 item · Farm Boy's closed now — opens 8:00 tomorrow"


def test_late_in_the_day_still_to_do_says_how_long_it_is_open():
    _dinner_waiting_on_the_list()
    tools.set_store_hours("Farm Boy", _hours("08:00", "21:00"))
    shop = _shop(_at(18, 40))  # past the 6:35 start, dinner still ahead
    assert shop["overdue"] is True
    assert shop["detail"] == "1 item · still to do · Farm Boy's open till 9:00"


def test_still_to_do_in_the_morning_stays_quiet():
    """"Not in the morning" — an overdue breakfast shop is not told the
    store is open till nine tonight."""
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)
    tools.add_usual_stores(["Farm Boy"])
    tools.add_recipe("Overnight Oats", ingredients=[{"item": "Rolled oats", "qty": "1 lb", "category": "pantry"}],
                     prep_time_minutes=10, cook_time_minutes=15, default_servings=2)
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(ISO_TODAY, "Overnight Oats", slot="breakfast", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    tools.set_store_hours("Farm Boy", _hours("07:00", "21:00"))
    shop = _shop(_at(7, 45))  # breakfast at 8:00, start by 7:35
    assert shop["overdue"] is True
    assert shop["detail"] == "1 item · still to do"


def test_two_tagged_stores_each_name_their_own_closing_only_if_it_matters():
    _dinner_waiting_on_the_list(store="Farm Boy")
    tools.add_grocery_item("Paper towels", quantity="1", category="household")
    towels = [r for r in tools.list_grocery_list(status="needed") if r["item"] == "Paper towels"][0]
    tools.set_grocery_item_store(towels["id"], "Costco")
    tools.set_store_hours("Farm Boy", _hours("08:00", "17:30"))
    tools.set_store_hours("Costco", _hours("10:00", "20:30"))
    shop = _shop(_at(9))
    by_store = {s["store"]: s for s in shop["stops"]}
    # The row's title is already the store, so the line doesn't say it twice.
    assert by_store["Farm Boy"]["hours_line"] == "Closes at 5:30"
    assert "hours_line" not in by_store["Costco"]
    # The headline is the cook's deadline — no one store owns it.
    assert shop["detail"] == "2 items · by 6:35"


def test_closed_on_holidays_counts_as_closed_on_a_known_holiday(monkeypatch):
    _dinner_waiting_on_the_list()
    tools.set_store_hours("Farm Boy", _hours("08:00", "21:00", closed_on_holidays=True))
    # Today is a holiday the holidays module knows (its own lookup is what
    # stores._is_holiday asks; stubbed so the test runs on any date).
    monkeypatch.setattr(_stores, "_is_holiday", lambda d, cache=None: d == TODAY)
    shop = _shop(_at(9))
    assert shop["detail"] == "1 item · Farm Boy's closed now — opens 8:00 tomorrow"
    # Off by default: the same day, the box unticked, says nothing.
    tools.set_store_hours("Farm Boy", _hours("08:00", "21:00"))
    assert _shop(_at(9))["detail"] == "1 item · by 6:35"


def test_the_morning_text_reads_the_same_line():
    _dinner_waiting_on_the_list()
    tools.set_store_hours("Farm Boy", _hours("08:00", "17:30"))
    text = _digest.build_morning_text(now_local=_at(7))
    assert "Farm Boy closes at 5:30" in text, text


# ---------- the shopping-day line ----------

def test_the_shop_day_line_names_a_store_closed_that_day_and_only_then():
    tomorrow = (TODAY + datetime.timedelta(days=1)).strftime("%A").lower()
    _dinner_waiting_on_the_list(store="Farm Boy")
    tools.set_shop_days(shop_day=tomorrow)
    block = tools.today_moves(now=_at(9))["shop"]
    assert block["line"] == f"You shop on {tomorrow.capitalize()}. 1 thing on the list so far."

    hours = _hours()
    hours["days"][tomorrow[:3]] = "closed"
    tools.set_store_hours("Farm Boy", hours)
    block = tools.today_moves(now=_at(9))["shop"]
    assert block["line"] == (
        f"You shop on {tomorrow.capitalize()} · Farm Boy's closed {tomorrow.capitalize()}s. "
        "1 thing on the list so far."
    )


# ---------- the stored fact, the line in Preferences, the chat ----------

@pytest.mark.parametrize("days, said", [
    ({d: ["08:00", "21:00"] for d in EVERY_DAY}, "8–9 every day"),
    ({**{d: ["08:00", "21:00"] for d in EVERY_DAY}, "sun": ["10:00", "18:00"]}, "8–9, Sun 10–6"),
    ({**{d: ["08:00", "21:00"] for d in EVERY_DAY}, "sun": "closed"}, "8–9, closed Sun"),
])
def test_the_hours_line_reads_the_way_the_card_wrote_it(days, said):
    assert _stores.hours_summary({"days": days}) == said


def test_saving_validates_and_an_empty_editor_forgets():
    tools.add_usual_stores(["Farm Boy"])
    with pytest.raises(ValueError):
        tools.set_store_hours("Farm Boy", {"days": {"mon": ["08:00", "08:00"]}})  # only 00:00–00:00 is all day
    tools.set_store_hours("Farm Boy", _hours())
    assert "Farm Boy" in tools.get_store_hours()
    tools.set_store_hours("Farm Boy", {"days": {}, "closed_on_holidays": False})
    assert tools.get_store_hours() == {}


def test_the_chat_reads_hours_and_whether_it_is_open_off_household_memory(monkeypatch):
    tools.add_usual_stores(["Costco"])
    tools.set_store_hours("Costco", _hours("10:00", "20:30"))
    monkeypatch.setattr(tools.cooker, "household_now", lambda *a, **k: _at(21))
    memory = tools.get_household_memory()
    assert memory["store_hours_summary"]["Costco"] == "10–8:30 every day"
    assert memory["store_open_now"]["Costco"] == "closed — opens 10:00 tomorrow"


def test_dropping_a_store_drops_its_hours():
    tools.add_usual_stores(["Farm Boy", "Costco"])
    tools.set_store_hours("Farm Boy", _hours())
    tools.delete_preference("usual_stores", "Farm Boy")
    assert tools.get_store_hours() == {}


def test_the_route_saves_and_hands_back_the_memory(signed_in):
    tools.add_usual_stores(["Farm Boy"])
    res = signed_in.post("/api/memory/store-hours", json={"store": "Farm Boy", "hours": _hours("08:00", "21:00")})
    assert res.status_code == 200, res.text
    assert res.json()["store_hours"]["Farm Boy"]["days"]["mon"] == ["08:00", "21:00"]
    bad = signed_in.post("/api/memory/store-hours", json={"store": "Farm Boy", "hours": {"days": {"mon": ["9am", "x"]}}})
    assert bad.status_code == 400


def test_closed_on_holidays_uses_the_holidays_module_real_dates():
    """No stub: Christmas is in the holidays module's own rule table."""
    tools.add_usual_stores(["Farm Boy"])
    tools.set_store_hours("Farm Boy", _hours(closed_on_holidays=True))
    christmas = datetime.date(TODAY.year, 12, 25)
    assert _stores.open_window("Farm Boy", christmas) == {"closed": True, "holiday": True}
    dec20 = datetime.date(TODAY.year, 12, 20)
    assert _stores.open_window("Farm Boy", dec20)["opens"] == datetime.datetime.combine(dec20, time(8, 0))



# ---------- review follow-ups (2026-10-19) ----------

def test_a_close_after_midnight_is_the_next_morning():
    """08:00–01:00 used to be refused as "has to close after it opens", and
    yesterday's window was never asked about at 12:30 a.m."""
    tools.add_usual_stores(["Farm Boy"])
    tools.set_store_hours("Farm Boy", _hours("08:00", "01:00"))
    assert _stores.hours_summary(tools.get_store_hours()["Farm Boy"]) == "8–1 every day"
    after_midnight = _at(0, 30)
    assert _stores.window_at("Farm Boy", after_midnight)[0] == "open"
    assert _stores.open_now_said("Farm Boy", after_midnight) == "open till 1:00"
    assert _stores.open_now_said("Farm Boy", _at(1, 30)) == "closed — opens 8:00"


def test_midnight_and_24_hours_are_said_as_people_say_them():
    tools.add_usual_stores(["Farm Boy", "Costco"])
    tools.set_store_hours("Farm Boy", _hours("08:00", "00:00"))
    tools.set_store_hours("Costco", _hours("00:00", "00:00"))
    lines = tools.get_household_memory()["store_hours_summary"]
    assert lines == {"Farm Boy": "8–midnight every day", "Costco": "Open 24 hours"}
    assert _stores.open_now_said("Farm Boy", _at(22)) == "open till midnight"
    assert _stores.open_now_said("Costco", _at(3)) == "open 24 hours"


def test_a_24_hour_store_never_says_open_till():
    _dinner_waiting_on_the_list()
    tools.set_store_hours("Farm Boy", _hours("00:00", "00:00"))
    assert _shop(_at(18, 40))["detail"] == "1 item · still to do"
    assert _shop(_at(9))["detail"] == "1 item · by 6:35"


def test_a_midnight_close_is_tonights_last_chance_for_a_cook_tomorrow():
    """The early "closes at midnight → nothing to say" return used to run
    before the cook-tomorrow check, so this said only "by tomorrow"."""
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)
    tools.add_usual_stores(["Farm Boy"])
    tools.add_recipe("Overnight Oats", ingredients=[{"item": "Rolled oats", "qty": "1 lb", "category": "pantry"}],
                     prep_time_minutes=10, cook_time_minutes=15, default_servings=2)
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tomorrow = (TODAY + datetime.timedelta(days=1)).isoformat()
    tools.plan_meal(tomorrow, "Overnight Oats", slot="breakfast", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    tools.set_store_hours("Farm Boy", _hours("09:00", "00:00"))  # opens after tomorrow's 7:35 start
    shop = _shop(_at(20))
    assert shop["detail"] == "1 item · by midnight · Farm Boy closes at midnight"
    # Opening at 7:00 instead, it's in time tomorrow: nothing to say.
    tools.set_store_hours("Farm Boy", _hours("07:00", "00:00"))
    assert _shop(_at(20))["detail"] == "1 item · by tomorrow"


def test_only_a_usual_store_gets_hours_and_names_are_capped(signed_in):
    tools.add_usual_stores(["Farm Boy"])
    with pytest.raises(ValueError):
        tools.set_store_hours("Somewhere Else", _hours())
    saved = tools.set_store_hours("farm boy", _hours())
    assert saved["store"] == "Farm Boy", "saved under the usual-stores spelling"
    res = signed_in.post("/api/memory/store-hours", json={"store": "x" * 5000, "hours": _hours()})
    assert res.status_code == 422
    res = signed_in.post("/api/memory/store-hours", json={"store": "Nowhere", "hours": _hours()})
    assert res.status_code == 400


def test_day_keys_are_days_and_holidays_must_be_true():
    tools.add_usual_stores(["Farm Boy"])
    with pytest.raises(ValueError):
        tools.set_store_hours("Farm Boy", {"days": {"monkey": ["08:00", "21:00"]}})
    saved = tools.set_store_hours("Farm Boy", {"days": {"Monday": ["08:00", "21:00"]}, "closed_on_holidays": "no"})
    assert saved["hours"] == {"days": {"mon": ["08:00", "21:00"]}, "closed_on_holidays": False}


# ---------- the editor starts blank (node harness over shell.js itself) ----------

_SHELL = (Path(__file__).resolve().parents[1] / "static" / "shell.js").read_text(encoding="utf-8")


def _js_function(name: str) -> str:
    start = _SHELL.index(f"function {name}(")
    i, depth = _SHELL.index("{", start), 0
    j = i
    while True:
        depth += {"{": 1, "}": -1}.get(_SHELL[j], 0)
        if depth == 0:
            return _SHELL[start:j + 1]
        j += 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page's own functions")
def test_the_hours_editor_opens_blank_and_copies_only_typed_hours():
    """A pre-filled 8–9 was one tap from saving hours nobody gave, which
    then moved Today's deadline. Blank instead; the seven rows copy the one
    row only once both times are typed."""
    var_lines = "\n".join(re.search(rf"  var {n} = [^\n]*\n", _SHELL).group(0) for n in ("WWK_HOURS_DAYS", "WWK_HOURS_SHORT"))
    script = (
        "var wwkState = {hoursEdit: null}; var MEM = {store_hours: {}, store_hours_summary: {}};\n"
        "function wwkMem() { return MEM; } function wwkRenderSection() {}\n"
        "function escapeHtml(s) { return String(s == null ? '' : s); }\n"
        "var document = { querySelector: function () { return null; } };\n"
        + var_lines
        + "\n".join(_js_function(n) for n in ("wwkChip", "wwkStoreHours", "wwkHoursLine", "wwkHoursHtml",
                                              "wwkHoursOpen", "wwkHoursSync", "wwkHoursMode"))
        + """
wwkHoursOpen('Costco');
var out = {all: wwkState.hoursEdit.all, html: wwkHoursHtml(MEM, 'Costco')};
wwkHoursMode(false);
out.blankDays = WWK_HOURS_DAYS.map(function (d) { return wwkState.hoursEdit.days[d].o + '|' + wwkState.hoursEdit.days[d].c; });
wwkHoursMode(true); wwkState.hoursEdit.all = {o: '07:00', c: '22:00'}; wwkHoursMode(false);
out.typedMon = wwkState.hoursEdit.days.mon;
console.log(JSON.stringify(out));
"""
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout.strip().splitlines()[-1])
    assert got["all"] == {"o": "", "c": ""}
    assert "08:00" not in got["html"] and "21:00" not in got["html"]
    assert "Clear hours" not in got["html"], "nothing saved, nothing to clear"
    assert set(got["blankDays"]) == {"|"}
    assert got["typedMon"] == {"o": "07:00", "c": "22:00", "closed": False}
