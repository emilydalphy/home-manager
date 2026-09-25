"""
Plan a week: bring over last week's meals you didn't cook.

Loop Board card (Emily, 2026-09-25, option A of the "Bring it over"
mockup): at the top of "Same as last week?", BRING OVER FROM LAST WEEK —
"You didn't get to these. Tick any you want this week." — lists last
week's dinners and lunches nobody ticked cooked, one row per dish, "Was
Tuesday" under each (plus "· groceries bought" only when that is true),
nothing ticked. A ticked meal goes to the planner as a fixed meal for the
new week: Pomona picks the night (the earliest one cooked at home that
fits its time cap), a dinner stays a dinner, it is exempt from the
no-repeat rule, the draft marks it "From last week", and what last week's
list already bought for it isn't bought again.

Every test here is red on `same-as-last-week` (ed85dc1): there is no
tools/bring_over.py, no `last_week_uncooked` in the prefill, no
`brought_over` on the intake, and the page's #bring-over slot is empty.
The generation tests stub the model at agent.generate_weekly_plan_llm and
the re-pick at swap_in_place._pick_replacement, as
tests/test_no_repeat_enforced.py does.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import agent, tools
from app.db import get_conn
from app.tools import bring_over, draft_opener, meal_variety
from app.tools import swap_in_place as sip

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")
SHELL = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the page's own functions"
)


def _monday(offset_weeks: int = 0) -> str:
    from conftest import household_today
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _slot(date: str, slot: str, name: str, **extra) -> dict:
    d = {
        "date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
        "ingredients": [
            {"item": f"{name} base", "qty": "1 lb", "category": "pantry"},
            {"item": f"{name} greens", "qty": "1 bunch", "category": "produce"},
        ],
        "instructions": [f"Cook the {name.lower()} over medium heat for 10 minutes.", "Serve."],
        "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
        "prep_time_minutes": 10, "cook_time_minutes": 15,
    }
    d.update(extra)
    return d


def _week(week: str, dinners, lunches=None) -> list[dict]:
    dates = tools._week_dates(week)
    # A different lunch every day AND every week, so no lunch is a repeat
    # the no-repeat pass would replace — last week's are "Lunch bowl N".
    lunches = lunches or [f"Lunch bowl {i}" if week < _monday(0) else f"Grain salad {i}" for i in range(7)]
    out = []
    for i, date in enumerate(dates):
        out.append(_slot(date, "breakfast", "Overnight oats"))
        out.append(_slot(date, "snack", "Apple"))
        out.append(_slot(date, "lunch", lunches[i]))
        out.append(_slot(date, "dinner", dinners[i]))
    return out


LAST_DINNERS = ["Bean chili", "Chicken tikka masala", "Salmon traybake", "Kofte", "Burgers", "Shrimp tacos", "Pad thai"]
FRESH_DINNERS = ["Lamb stew", "Miso cod", "Bibimbap", "Ratatouille", "Jerk chicken", "Pierogi", "Dal"]


@pytest.fixture
def stub_model(monkeypatch):
    seen = {}

    def _stub(days):
        def fake(ctx):
            seen["ctx"] = ctx
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake)
        return seen
    return _stub


@pytest.fixture
def picker(monkeypatch):
    calls = []
    names = iter(["Moussaka", "Laksa", "Gumbo", "Paella", "Risotto", "Bigos", "Cassoulet", "Pho"])

    def pick(context):
        calls.append(context)
        name = next(names)
        return {
            "meal_name": name, "reason": f"{name} because",
            "ingredients": [{"item": f"{name} stuff", "qty": "1", "category": "pantry"}],
            "instructions": [f"Cook the {name.lower()} over medium heat.", "Serve."],
            "food_groups": ["protein", "vegetable", "carb"],
            "prep_time_minutes": 10, "cook_time_minutes": 20,
        }

    monkeypatch.setattr(sip, "_pick_replacement", pick)
    return calls


def _entries(plan_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.*, COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE mpe.weekly_plan_id = ? ORDER BY date, id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _entry(plan_id: int, date: str, slot: str) -> dict:
    return next(e for e in _entries(plan_id) if e["date"] == date and e["slot"] == slot)


def _ids(plan_id: int, meal: str) -> list[int]:
    return [e["id"] for e in _entries(plan_id) if e["meal"] == meal]


@pytest.fixture
def last_week(stub_model):
    """Last week, answered and approved through the real doors — its list
    built by approval — and nothing ticked cooked yet. This week is the
    week after, so every one of last week's nights has gone by."""
    week = _monday(-1)
    tools.save_week_intake(week, moods=["Comfort food"])
    stub_model(_week(week, LAST_DINNERS))
    plan = agent.generate_weekly_plan(week)
    tools.approve_weekly_plan(plan["weekly_plan_id"])
    return plan


def _cook(entry_ids):
    conn = get_conn()
    conn.executemany("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?", [(i,) for i in entry_ids])
    conn.commit()
    conn.close()


def _offer(week: str | None = None) -> list[dict]:
    return tools.get_week_intake_prefill(week or _monday(0))["last_week_uncooked"]


# ==========================================================================
# 1. The offer: what "Bring over from last week" lists
# ==========================================================================

class TestTheOffer:
    def test_last_weeks_uncooked_dinners_and_lunches_are_listed_with_their_night(self, last_week):
        plan_id = last_week["weekly_plan_id"]
        cooked = [i for m in LAST_DINNERS[2:] for i in _ids(plan_id, m)]
        cooked += [e["id"] for e in _entries(plan_id) if e["slot"] == "lunch" and e["meal"] != "Lunch bowl 1"]
        _cook(cooked)

        offer = _offer()
        names = [o["meal"] for o in offer]
        assert names == ["Bean chili", "Chicken tikka masala", "Lunch bowl 1"]
        tikka = offer[1]
        assert tikka["slot"] == "dinner"
        assert tikka["weekday"] == datetime.date.fromisoformat(tools._week_dates(_monday(-1))[1]).strftime("%A")
        assert tikka["entry_ids"] == _ids(plan_id, "Chicken tikka masala")
        assert offer[2]["slot"] == "lunch"

    def test_no_breakfast_no_snack_nothing_cooked(self, last_week):
        plan_id = last_week["weekly_plan_id"]
        _cook([e["id"] for e in _entries(plan_id) if e["slot"] in ("dinner", "lunch")])
        assert _offer() == []

    def test_a_dish_on_several_nights_is_one_row_named_for_its_first(self, stub_model):
        week = _monday(-1)
        tools.save_week_intake(week, moods=["Comfort food"])
        stub_model(_week(week, ["Bean chili", "Kofte", "Bean chili"] + LAST_DINNERS[3:]))
        plan = agent.generate_weekly_plan(week)
        tools.approve_weekly_plan(plan["weekly_plan_id"])
        chili = [o for o in _offer() if o["meal"] == "Bean chili"]
        assert len(chili) == 1
        assert chili[0]["date"] == tools._week_dates(week)[0]
        assert len(chili[0]["entry_ids"]) == len(_ids(plan["weekly_plan_id"], "Bean chili")) >= 1

    def test_a_reheat_night_is_not_a_meal_they_missed(self, last_week):
        plan_id = last_week["weekly_plan_id"]
        dates = tools._week_dates(_monday(-1))
        tikka = _entry(plan_id, dates[1], "dinner")
        conn = get_conn()
        conn.execute("UPDATE meal_plan_entries SET freeform_meal = 'Leftovers', recipe_id = NULL WHERE id = ?",
                     (_entry(plan_id, dates[3], "dinner")["id"],))
        lunch = _entry(plan_id, dates[2], "lunch")
        conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                     (json.dumps({"links_to": f"entry_id:{tikka['id']}"}), lunch["id"]))
        conn.commit()
        conn.close()
        names = [o["meal"] for o in _offer()]
        assert "Leftovers" not in names
        assert lunch["meal"] not in names
        assert "Chicken tikka masala" in names

    def test_a_first_week_or_an_unapproved_last_week_offers_nothing(self, stub_model):
        assert _offer() == []
        week = _monday(-1)
        tools.save_week_intake(week, moods=["Comfort food"])
        stub_model(_week(week, LAST_DINNERS))
        agent.generate_weekly_plan(week)          # a draft, never approved
        assert _offer() == []

    def test_groceries_bought_only_when_every_line_for_it_was_ticked_bought(self, last_week):
        plan_id = last_week["weekly_plan_id"]
        chili_ids = _ids(plan_id, "Bean chili")
        conn = get_conn()
        lines = [r["grocery_item_id"] for r in conn.execute(
            f"SELECT DISTINCT grocery_item_id FROM meal_plan_grocery_links WHERE meal_plan_entry_id IN "
            f"({','.join('?' * len(chili_ids))})", chili_ids).fetchall()]
        conn.close()
        assert len(lines) == 2
        tools.mark_grocery_item(lines[0], "purchased")
        chili = next(o for o in _offer() if o["meal"] == "Bean chili")
        assert chili["groceries_bought"] is False
        tools.mark_grocery_item(lines[1], "purchased")
        chili = next(o for o in _offer() if o["meal"] == "Bean chili")
        assert chili["groceries_bought"] is True
        tikka = next(o for o in _offer() if o["meal"] == "Chicken tikka masala")
        assert tikka["groceries_bought"] is False


# ==========================================================================
# 2. The answer: what the save stores
# ==========================================================================

class TestTheSave:
    def test_a_tick_is_stored_as_the_offer_had_it(self, last_week):
        tikka = next(o for o in _offer() if o["meal"] == "Chicken tikka masala")
        saved = tools.save_week_intake(_monday(0), brought_over=[{"entry_ids": tikka["entry_ids"][:1]}])
        assert saved["brought_over"] == [{
            "entry_ids": tikka["entry_ids"], "meal": "Chicken tikka masala",
            "recipe_id": tikka["recipe_id"], "slot": "dinner", "date": tikka["date"],
        }]
        # Carried by every later revision, like any answer; [] clears it.
        again = tools.save_week_intake(_monday(0), moods=["On the grill"])
        assert again["brought_over"] == saved["brought_over"]
        assert tools.save_week_intake(_monday(0), brought_over=[])["brought_over"] == []

    def test_something_not_on_offer_is_dropped_and_a_bad_shape_refused(self, last_week):
        plan_id = last_week["weekly_plan_id"]
        breakfast = next(e["id"] for e in _entries(plan_id) if e["slot"] == "breakfast")
        saved = tools.save_week_intake(_monday(0), brought_over=[{"entry_ids": [breakfast]}, {"entry_ids": [999999]}])
        assert saved["brought_over"] == []
        for bad in ([{"entry_ids": "3"}], [{"entry_ids": []}], ["3"], {"entry_ids": [3]}, [{"entry_ids": [True]}]):
            with pytest.raises(ValueError):
                tools.save_week_intake(_monday(0), brought_over=bad)

    def test_the_route_takes_it(self, last_week, client, signed_in):
        tikka = next(o for o in _offer() if o["meal"] == "Chicken tikka masala")
        res = client.post(f"/api/week/{_monday(0)}/intake", json={"brought_over": [{"entry_ids": tikka["entry_ids"]}]})
        assert res.status_code == 200, res.text
        assert [b["meal"] for b in res.json()["brought_over"]] == ["Chicken tikka masala"]
        assert client.post(f"/api/week/{_monday(0)}/intake", json={"brought_over": [{"x": 1}]}).status_code == 400


# ==========================================================================
# 3. Pomona picks the night
# ==========================================================================

def _item(slot="dinner", meal="Chicken tikka masala", recipe_id=None, date="2026-09-01"):
    return {"entry_ids": [1], "meal": meal, "recipe_id": recipe_id, "slot": slot, "date": date}


class TestChooseNights:
    DATES = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11"]

    def test_the_earliest_night_it_can_be_cooked(self):
        placed, missed = bring_over.choose_nights([_item()], self.DATES, {})
        assert [p["on"] for p in placed] == ["2026-10-05"] and missed == []

    def test_never_a_night_out_left_over_skipped_away_or_a_holiday(self):
        intake = {"night_tags": {"2026-10-05": ["out"], "2026-10-06": ["left"]}, "skipped_days": ["2026-10-07"]}
        needs = {"away_slots": [{"date": "2026-10-08", "slot": "dinner"}],
                 "ready_made_slots": [{"date": "2026-10-09", "slot": "dinner"}]}
        holidays = [{"date": "2026-10-10", "answer": {"answer": "hosting"}}]
        placed, _ = bring_over.choose_nights([_item()], self.DATES, intake, slot_needs=needs, holidays=holidays)
        assert placed[0]["on"] == "2026-10-11"

    def test_a_time_cap_it_does_not_fit_is_passed_over(self):
        recipe_id = tools.add_recipe("Slow braise", [{"item": "beef", "qty": "2 lb"}],
                                     prep_time_minutes=20, cook_time_minutes=100)["recipe_id"]
        caps = {"2026-10-05": 30, "2026-10-06": 45}
        placed, _ = bring_over.choose_nights(
            [_item(meal="Slow braise", recipe_id=recipe_id)], self.DATES, {},
            cap_for=lambda d, slot: caps.get(d),
        )
        assert placed[0]["on"] == "2026-10-07"

    def test_two_dinners_two_nights_and_a_lunch_stays_a_lunch(self):
        items = [_item(meal="A", date="2026-09-03"), _item(meal="B", date="2026-09-01"), _item("lunch", "C")]
        placed, _ = bring_over.choose_nights(items, self.DATES, {})
        assert [(p["meal"], p["slot"], p["on"]) for p in placed] == [
            ("B", "dinner", "2026-10-05"), ("A", "dinner", "2026-10-06"), ("C", "lunch", "2026-10-05"),
        ]

    def test_a_lunch_skips_prepped_and_leftovers_days_and_prefers_one_that_stays_home(self):
        intake = {
            "weekday_lunches": {"days": [{"date": "2026-10-05", "kind": "prepped"},
                                         {"date": "2026-10-06", "kind": "leftovers"},
                                         {"date": "2026-10-07", "kind": "cooked"}]},
            "packed_lunch_days": ["2026-10-07"],
        }
        placed, _ = bring_over.choose_nights([_item("lunch")], self.DATES, intake)
        assert placed[0]["on"] == "2026-10-08"

    def test_nowhere_to_go_is_left_out_not_forced(self):
        intake = {"night_tags": {d: ["out"] for d in self.DATES}}
        placed, missed = bring_over.choose_nights([_item()], self.DATES, intake)
        assert placed == [] and [m["meal"] for m in missed] == ["Chicken tikka masala"]
        placed, missed = bring_over.choose_nights([_item()], self.DATES, {}, zero_slots={"dinner"})
        assert placed == []


# ==========================================================================
# 4. The draft: it lands, it stays, it's marked
# ==========================================================================

def _bring(meal: str) -> dict:
    item = next(o for o in _offer() if o["meal"] == meal)
    return tools.save_week_intake(_monday(0), brought_over=[{"entry_ids": item["entry_ids"]}])


class TestTheDraft:
    def test_it_lands_on_the_first_night_and_the_model_is_told(self, last_week, stub_model, picker):
        _bring("Chicken tikka masala")
        week = _monday(0)
        dates = tools._week_dates(week)
        seen = stub_model(_week(week, FRESH_DINNERS))
        plan = agent.generate_weekly_plan(week)
        plan_id = plan["weekly_plan_id"]

        monday = _entry(plan_id, dates[0], "dinner")
        assert monday["meal"] == "Chicken tikka masala"
        derived = json.loads(monday["derived_from_json"])
        assert derived["brought_over"]["from_date"] == tools._week_dates(_monday(-1))[1]
        assert "last week" in monday["reasoning"]
        assert seen["ctx"]["intake"]["brought_over"] == [
            {"date": dates[0], "slot": "dinner", "meal": "Chicken tikka masala"}]
        # The rest of the week is the model's, and nothing was re-picked.
        assert [e["meal"] for e in _entries(plan_id) if e["slot"] == "dinner"][1:] == FRESH_DINNERS[1:]
        assert tools.audit_plan_slots(plan_id)["complete"] is True

    def test_it_is_exempt_from_the_no_repeat_rule(self, last_week, stub_model, picker):
        """The catch: without the exemption, the no-repeat pass swaps a meal
        they just chose straight back out, because they 'had it' last week."""
        _bring("Chicken tikka masala")
        week = _monday(0)
        stub_model(_week(week, FRESH_DINNERS))
        plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
        assert "Chicken tikka masala" in [e["meal"] for e in _entries(plan_id) if e["slot"] == "dinner"]
        assert picker == []

    def test_a_real_repeat_beside_it_is_still_replaced(self, last_week, stub_model, picker):
        _bring("Chicken tikka masala")
        week = _monday(0)
        stub_model(_week(week, FRESH_DINNERS[:3] + ["Kofte"] + FRESH_DINNERS[4:]))
        plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
        dinners = [e["meal"] for e in _entries(plan_id) if e["slot"] == "dinner"]
        assert "Kofte" not in dinners and "Chicken tikka masala" in dinners
        assert len(picker) == 1

    def test_the_count_pass_never_folds_it_away(self, last_week, stub_model, picker):
        tools.edit_preference("dinners_per_week", 3)
        _bring("Chicken tikka masala")
        week = _monday(0)
        stub_model(_week(week, FRESH_DINNERS))
        plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
        dinners = [e["meal"] for e in _entries(plan_id) if e["slot"] == "dinner"]
        assert dinners[0] == "Chicken tikka masala"

    def test_protected_and_theirs(self):
        assert meal_variety.theirs({"brought_over": {"from_date": "2026-09-01"}}) is True
        assert meal_variety.theirs({"freeform": "chili please"}) is True
        assert meal_variety.theirs({}) is False
        dishes = meal_variety._group_dishes(
            [{"id": 1, "date": "2026-10-05", "slot": "dinner", "slot_state": "planned", "cooked_status": "pending",
              "food_groups_json": "[]", "meal": "Tikka",
              "derived_from_json": json.dumps({"brought_over": {"entry_ids": [3]}})}],
            {"leftovers": {}, "sources": {}},
        )
        assert dishes[0]["protected"] is True

    def test_the_draft_marks_it_and_does_not_call_it_a_repeat(self, last_week, stub_model, picker):
        _bring("Chicken tikka masala")
        week = _monday(0)
        stub_model(_week(week, FRESH_DINNERS))
        plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
        menu = tools.get_week_menu(plan_id)
        monday = next(d for d in menu["days"] if d["date"] == tools._week_dates(week)[0])
        assert monday["dinner"]["brought_over"] is True
        tuesday = next(d for d in menu["days"] if d["date"] == tools._week_dates(week)[1])
        assert tuesday["dinner"]["brought_over"] is False
        assert "tikka" not in " ".join(menu.get("draft_opener") or []).lower()

    def test_the_opener_leaves_it_out_of_back_from(self):
        entries = [
            {"date": "2026-10-05", "slot": "dinner", "meal": "Tikka", "slot_state": "planned",
             "derived_from": {"brought_over": {"entry_ids": [1]}}},
            {"date": "2026-10-06", "slot": "dinner", "meal": "Dal", "slot_state": "planned", "derived_from": {}},
        ]
        line = draft_opener._line_two(entries, None, {"tikka"})
        assert "back from" not in line and "Tikka" not in line

    def test_no_brought_over_means_no_change(self, last_week, stub_model, picker):
        week = _monday(0)
        seen = stub_model(_week(week, FRESH_DINNERS))
        plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
        assert "brought_over" not in (seen["ctx"].get("intake") or {})
        assert [e["meal"] for e in _entries(plan_id) if e["slot"] == "dinner"] == FRESH_DINNERS


# ==========================================================================
# 5. The shopping: what was bought last week isn't bought again
# ==========================================================================

def _needed(item: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM grocery_items WHERE LOWER(item) = LOWER(?) AND status = 'needed'",
                        (item,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


class TestTheShopping:
    def _buy_last_weeks(self, plan_id: int, meal: str, only: str | None = None) -> None:
        ids = _ids(plan_id, meal)
        conn = get_conn()
        rows = conn.execute(
            f"SELECT DISTINCT l.grocery_item_id, l.item FROM meal_plan_grocery_links l WHERE l.meal_plan_entry_id IN "
            f"({','.join('?' * len(ids))})", ids).fetchall()
        conn.close()
        for r in rows:
            if only is None or r["item"] == only:
                tools.mark_grocery_item(r["grocery_item_id"], "purchased")
        # A tick also puts the line in the kitchen's inventory (beta), which
        # the ingest reads on its own; cleared so these tests prove the
        # bring-over's own reading — last week's bought line — and not the
        # inventory's.
        conn = get_conn()
        conn.execute("DELETE FROM inventory_items")
        conn.commit()
        conn.close()

    def test_bought_last_week_is_not_on_the_new_list(self, last_week, stub_model, picker):
        plan_id = last_week["weekly_plan_id"]
        self._buy_last_weeks(plan_id, "Chicken tikka masala", only="Chicken tikka masala base")
        _bring("Chicken tikka masala")
        week = _monday(0)
        stub_model(_week(week, FRESH_DINNERS))
        new = agent.generate_weekly_plan(week)["weekly_plan_id"]
        tools.approve_weekly_plan(new)
        # The base was bought last week; the greens weren't, so they are.
        assert [r for r in _needed("Chicken tikka masala base") if r["source_weekly_plan_id"] == new] == []
        assert [r for r in _needed("Chicken tikka masala greens") if r["source_weekly_plan_id"] == new]
        # Every other dinner shops as always.
        assert [r for r in _needed("Miso cod base") if r["source_weekly_plan_id"] == new]

    def test_a_swap_later_does_not_buy_it_after_all(self, last_week, stub_model, picker):
        plan_id = last_week["weekly_plan_id"]
        self._buy_last_weeks(plan_id, "Chicken tikka masala")
        _bring("Chicken tikka masala")
        week = _monday(0)
        dates = tools._week_dates(week)
        stub_model(_week(week, FRESH_DINNERS))
        new = agent.generate_weekly_plan(week)["weekly_plan_id"]
        tools.approve_weekly_plan(new)
        tools.swap_meal_in_plan(new, dates[3], "Tomato soup", slot="dinner")
        assert [r for r in _needed("Chicken tikka masala base") if r["source_weekly_plan_id"] == new] == []
        assert [r for r in _needed("Chicken tikka masala greens") if r["source_weekly_plan_id"] == new] == []

    def test_bought_items_reads_purchased_lines_only(self, last_week):
        plan_id = last_week["weekly_plan_id"]
        ids = _ids(plan_id, "Bean chili")
        assert bring_over.bought_items(ids) == set()
        self._buy_last_weeks(plan_id, "Bean chili", only="Bean chili greens")
        assert bring_over.bought_items(ids) == {"bean chili greens"}


# ==========================================================================
# 6. The page and the draft screen
# ==========================================================================

def _extract(name: str, source: str = PAGE) -> str:
    start = source.index(f"function {name}(")
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
    m = re.search(rf"  var {name} = [\s\S]*?;\n", source)
    assert m, name
    return m.group(0)


def _node(script: str) -> object:
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


class TestThePage:
    def test_her_words(self):
        assert "label: 'Bring over from last week'" in PAGE
        assert "sub: 'You didn\\u2019t get to these. Tick any you want this week.'" in PAGE
        assert "bought: 'groceries bought'" in PAGE
        assert "brought_over: bringPayload()" in _extract("samePayload")
        assert "brought_over: bringPayload()" in _extract("leavePayload")

    @_needs_node
    def test_rows_nothing_ticked_and_bought_only_when_true(self):
        script = f"""
        var data = {{ last_week_uncooked: [
          {{entry_ids: [4, 9], meal: 'Chicken tikka masala', weekday: 'Tuesday', groceries_bought: true}},
          {{entry_ids: [7], meal: 'Salmon <traybake>', weekday: 'Thursday', groceries_bought: false}}
        ] }};
        var answers = {{ brought_over: [] }};
        var box = {{ innerHTML: '', hidden: true, querySelectorAll: function () {{ return []; }} }};
        function $(id) {{ return box; }}
        function esc(s) {{ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}
        {_var('BRING_COPY')}{_var('TICK_SVG')}
        {_extract('bringKey')}{_extract('bringOffer')}{_extract('renderBringOver')}
        {_extract('bringPayload')}{_extract('bringTicksFrom')}
        renderBringOver();
        var first = box.innerHTML, shown = !box.hidden;
        answers.brought_over = ['7'];
        var payload = bringPayload();
        var ticks = bringTicksFrom([{{entry_ids: [9]}}]);
        data.last_week_uncooked = [];
        renderBringOver();
        console.log(JSON.stringify({{first: first, shown: shown, payload: payload, ticks: ticks, hiddenAfter: box.hidden}}));
        """
        out = _node(script)
        assert out["shown"] is True
        assert out["first"].count('aria-checked="false"') == 2 and 'aria-checked="true"' not in out["first"]
        assert "Was Tuesday · groceries bought" in out["first"]
        assert "Was Thursday<" in out["first"]
        assert "Salmon &lt;traybake&gt;" in out["first"]
        assert out["payload"] == [{"entry_ids": [7]}]
        assert out["ticks"] == ["4,9"]
        assert out["hiddenAfter"] is True

    def test_the_draft_row_says_from_last_week(self):
        """Under the meta on a day row, only for a planned slot the menu marks
        brought_over; on a What we're eating row when any of the dish's days
        is. Same small label as "Changed"."""
        assert "(planned && entry.brought_over" in SHELL
        assert SHELL.count('<span class="wk-from-last-line"><span class="wk-from-last">From last week</span></span>') == 2
        assert "d.entry && d.entry.brought_over" in SHELL
        css = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
        assert ".wk-changed,\n.wk-from-last {" in css


def test_a_brought_over_row_says_last_week_once():
    """The "From last week" label says why the meal is there; the stored
    reason ("Brought over from last week — it was on Tuesday") printed in
    the row's foot since the 1A rows (2026-09-25) said it twice."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")
    start = js.index("function wkRowMetaHtml(")
    body = js[start:js.index("\n  }\n", start)]
    assert "!entry.brought_over" in body
