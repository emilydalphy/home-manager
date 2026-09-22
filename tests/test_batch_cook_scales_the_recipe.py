"""
"Batch cook these" on a SNACK wrote a chain nothing could read, so the
recipe under it went on showing one afternoon's amounts.

Emily, 2026-09-14, on her phone: Cook → Roasted Chickpeas → Before you
start. She ticked Wed and Fri, the line read "One cook on Tuesday feeds
Tue, Wed and Fri · 6 plates", she tapped Batch cook these — and Everything
out still said Serves 2 and one 15 oz can. A turkey skillet whose batch was
decided at planning time, on the same day, read Serves 4 and 2 lbs.

Two things were wrong, one behind the other:

1. weekly_plan._LINKS_TO_DATE_SLOT_RE spelled its slots out as
   "breakfast|lunch|dinner". A day has a snack slot too, so every snack
   chain's links_to failed to parse, leftovers._resolve returned None, and
   plan_leftover_chains dropped a chain both halves of which were sitting
   right there in the database.

2. Even parsing, "YYYY-MM-DD:snack" names no particular row:
   preferences.resolve_snacks_per_day gives a day TWO snacks by default,
   so the key resolved to whichever of them the query returned last —
   usually the other dish, which confirms nothing. cook_ahead.set_cook_ahead
   writes "entry_id:<n>" now, the shape both resolvers have always
   accepted, because it is holding the row it is writing about.

Every test here says in its own docstring whether it is a CATCH (red on
main) or a GUARD (green on main, here so the fix can't take something
working with it).
"""
import datetime

import pytest

from conftest import household_today

from app import tools
from app.tools import weekly_plan as _weekly_plan


def _monday() -> datetime.date:
    # The HOUSEHOLD's Monday, not the process's: the seeded week has to be
    # the week the app thinks it is in. Only wrong when the two clocks fall
    # either side of a Monday, so it fails one day in seven and reads as a
    # flake — see conftest.household_today.
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


MON, TUE, WED, THU = _day(0), _day(1), _day(2), _day(3)


def _household():
    tools.add_member("Emily")
    tools.add_member("Jamie")


def _chickpeas():
    tools.add_recipe(
        "Roasted Chickpeas",
        ingredients=[
            {"item": "Chickpeas", "qty": "1 can (15 oz)"},
            {"item": "Olive oil", "qty": "1 tbsp"},
            {"item": "Paprika", "qty": "0.5 tsp"},
        ],
        default_servings=2,
    )


def _week(slot="snack", days=(MON, TUE, THU), second_snack=False):
    """The reported plan: the same dish on three of the week's days, each
    its own entry, no chain between any of them. `second_snack` adds the
    OTHER snack a day gets by default, which is what made the date+slot
    key ambiguous."""
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    ids = {}
    for d in days:
        ids[d] = tools.plan_meal(d, "Roasted Chickpeas", slot=slot, weekly_plan_id=plan_id)["entry_id"]
        if second_snack:
            tools.plan_meal(d, "Apple", slot=slot, weekly_plan_id=plan_id)
    return plan_id, ids


def _cards(plan_id):
    return {m["entry_id"]: m for m in tools.get_cooker_view(plan_id)["meals"]}


def _qty(card):
    return {i["item"]: i["qty"] for i in card["ingredients"]}


def _derived_from(entry_id):
    import json

    from app.db import get_conn

    conn = get_conn()
    row = conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (entry_id,)
    ).fetchone()
    conn.close()
    return json.loads(row["derived_from_json"] or "{}")


# ---------- the reported bug ----------

def test_batch_cooking_a_snack_scales_the_recipe(client):
    """CATCH. The whole report in one: tick the other two days, and the
    card in front of the cook is the batch — serves, amounts and the
    sentence over them."""
    _household()
    _chickpeas()
    plan_id, ids = _week()

    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[THU]])

    card = _cards(plan_id)[ids[MON]]
    assert card["servings"] == 6, "two at each of the three days it covers"
    assert card["default_servings"] == 6, "the Serves stepper starts from the batch"
    assert _qty(card) == {
        "Chickpeas": "3 cans (15 oz)",
        "Olive oil": "3 tbsp",
        "Paprika": "1.5 tsp",
    }
    assert card["covers_note"] == "Cooking for 6 — enough for Monday, Tuesday, and Thursday."


def test_the_other_snack_on_the_day_cannot_steal_the_chain(client):
    """CATCH. The default shape, and the reason widening the regex alone
    was not the fix: a day holds two snacks, so "date:snack" named the
    Apple as often as the chickpeas."""
    _household()
    _chickpeas()
    tools.add_recipe("Apple", ingredients=[{"item": "Apples", "qty": "2"}], default_servings=2)
    plan_id, ids = _week(second_snack=True)

    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[THU]])

    assert _cards(plan_id)[ids[MON]]["servings"] == 6


def test_the_covered_snack_days_stop_being_cooks(client):
    """CATCH. The other half of the promise: a day the batch covers is a
    reheat, not a second cook of the same dish."""
    _household()
    _chickpeas()
    plan_id, ids = _week()

    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[THU]])

    cards = _cards(plan_id)
    for d in (TUE, THU):
        card = cards[ids[d]]
        assert card["is_leftovers"] is True
        assert card["leftovers_headline"] == "Made ahead — Monday’s Roasted Chickpeas"
        assert card["ingredients"] == []
        assert card["has_full_recipe"] is False


def test_the_picker_shows_the_days_it_is_already_cooking_for(client):
    """CATCH. A chain that doesn't read back leaves the chips blank, so
    the household is asked a question they have already answered."""
    _household()
    _chickpeas()
    plan_id, ids = _week()

    tools.set_cook_ahead(ids[MON], [ids[TUE]])

    days = _cards(plan_id)[ids[MON]]["cook_ahead"]["days"]
    assert [(d["entry_id"], d["selected"]) for d in days] == [(ids[TUE], True), (ids[THU], False)]


def test_a_snack_day_another_batch_already_covers_is_refused(client):
    """CATCH. The refusal is read off the chains, so while they didn't
    resolve a snack day could be claimed twice."""
    _household()
    _chickpeas()
    plan_id, ids = _week()
    tools.set_cook_ahead(ids[MON], [ids[THU]])

    result = tools.set_cook_ahead(ids[TUE], [ids[THU]])

    assert isinstance(result, str)
    assert "Thursday" in result


def test_changing_your_mind_brings_the_numbers_back(client):
    """CATCH — it can't come back from a number it never reached. Ticking
    nothing is a real answer: every day cooks for itself again."""
    _household()
    _chickpeas()
    plan_id, ids = _week()

    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[THU]])
    assert _cards(plan_id)[ids[MON]]["servings"] == 6

    tools.set_cook_ahead(ids[MON], [])

    cards = _cards(plan_id)
    monday = cards[ids[MON]]
    assert monday["servings"] is None
    assert _qty(monday)["Chickpeas"] == "1 can (15 oz)"
    assert not monday.get("covers_note"), "no chain, nothing to say about one"
    for d in (TUE, THU):
        assert cards[ids[d]]["is_leftovers"] is False
        assert _qty(cards[ids[d]])["Chickpeas"] == "1 can (15 oz)"


def test_dropping_one_day_rescales_to_what_is_left(client):
    """CATCH — red on main only because it is a snack; the property it
    pins (dropping a day rescales to what is left) works on main for
    dinners, and test_a_dinner_batch_drops_a_day_the_same_way below is its
    green-on-main control. Thursday goes back to cooking for itself and
    Monday cooks for four, not six."""
    _household()
    _chickpeas()
    plan_id, ids = _week()
    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[THU]])

    tools.set_cook_ahead(ids[MON], [ids[TUE]])

    cards = _cards(plan_id)
    assert cards[ids[MON]]["servings"] == 4
    assert _qty(cards[ids[MON]])["Chickpeas"] == "2 cans (15 oz)"
    assert cards[ids[TUE]]["is_leftovers"] is True
    assert cards[ids[THU]]["is_leftovers"] is False


def test_the_route_hands_back_the_scaled_card(signed_in):
    """CATCH. The screen redraws from this response and nothing else, so
    whatever it says is what the cook reads."""
    _household()
    _chickpeas()
    plan_id, ids = _week()

    res = signed_in.post("/api/cooker/cook-ahead", json={
        "source_entry_id": ids[MON], "covered_entry_ids": [ids[TUE], ids[THU]],
    })

    assert res.status_code == 200
    card = {m["entry_id"]: m for m in res.json()["meals"]}[ids[MON]]
    assert card["servings"] == 6
    assert _qty(card)["Chickpeas"] == "3 cans (15 oz)"


def test_a_date_and_a_slot_deliberately_do_not_name_a_snack(client):
    """GUARD, and the inverse of what this branch first shipped. Widening
    this parser to DAY_SLOTS was tried on 2026-09-15 and reverted: see
    TestALegacySnackChainOnDisk below for what it did. A day holds two
    snacks, so the string names two rows and must name none."""
    for slot in ("breakfast", "lunch", "dinner"):
        assert _weekly_plan._LINKS_TO_DATE_SLOT_RE.match(f"2026-09-14:{slot}"), slot
    assert not _weekly_plan._LINKS_TO_DATE_SLOT_RE.match("2026-09-14:snack")
    assert not _weekly_plan._LINKS_TO_DATE_SLOT_RE.match("2026-09-14:brunch")
    # A snack chain names the row instead, which is a key.
    assert _weekly_plan._LINKS_TO_ENTRY_ID_RE.match("entry_id:7")


# ---------- what must not have moved ----------

def test_a_batch_decided_at_planning_time_still_reads_the_same(client):
    """GUARD. The dinner path that already worked — Emily's turkey skillet
    — cooks for the whole batch exactly as it did."""
    _household()
    _chickpeas()
    plan_id, ids = _week(slot="dinner")

    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[THU]])

    card = _cards(plan_id)[ids[MON]]
    assert card["servings"] == 6
    assert _qty(card)["Chickpeas"] == "3 cans (15 oz)"
    assert card["covers_note"] == "Cooking for 6 — enough for Monday, Tuesday, and Thursday."


def test_nothing_is_scaled_twice(client):
    """CATCH — red on main only because it is a snack, like everything
    above it; the PROPERTY it pins is a no-regression one. The batch is
    read from the days it covers, not multiplied by anything already
    applied, so confirming the same days again and reading the view twice
    says six both times. Its green-on-main control is the dinner test
    above."""
    _household()
    _chickpeas()
    plan_id, ids = _week()

    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[THU]])
    first = _cards(plan_id)[ids[MON]]
    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[THU]])
    second = _cards(plan_id)[ids[MON]]

    assert first["servings"] == second["servings"] == 6
    assert _qty(first) == _qty(second)
    assert _qty(second)["Chickpeas"] == "3 cans (15 oz)"


def test_a_day_the_batch_covers_is_not_offered_its_own_picker(client):
    """CATCH. A reheat has nothing to cook ahead, and while the chain
    didn't resolve it went on offering to."""
    _household()
    _chickpeas()
    plan_id, ids = _week()

    tools.set_cook_ahead(ids[MON], [ids[TUE]])

    assert _cards(plan_id)[ids[TUE]]["cook_ahead"]["days"] == []


def test_the_shopping_list_is_left_alone_and_already_says_the_batch(client):
    """CATCH, for the second half only. Consolidating the COOKS doesn't
    change what has to be bought — the week still eats six portions — so
    the list is untouched, and main gets that right. What main gets wrong
    is the AGREEMENT: its card said one can over a list of three, which is
    the bug read from the shopping side."""
    _household()
    _chickpeas()
    plan_id, ids = _week()
    tools.approve_weekly_plan(plan_id)
    before = {r["item"]: r["quantity"] for r in tools.list_grocery_list()}

    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[THU]])

    after = {r["item"]: r["quantity"] for r in tools.list_grocery_list()}
    assert after == before
    assert after["Chickpeas"] == "3 cans (15 oz)"
    assert _qty(_cards(plan_id)[ids[MON]])["Chickpeas"] == after["Chickpeas"]


# ---------- what the picker calls a snack ----------

def test_a_snack_repeat_is_asked_about_in_days_not_nights():
    """CATCH. cookSlotWord had a word for breakfast and for lunch and read
    everything else as a night, so a row of afternoon chickpea chips asked
    "Which other nights should it cover?" — a question about a meal that
    wasn't on the screen. Run for real under node rather than read off the
    source: the word is the return value, not a string in the file."""
    import json
    import shutil
    from pathlib import Path

    from tests import nodeharness

    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    src = (Path(__file__).resolve().parents[1] / "static" / "shell.js").read_text(encoding="utf-8")
    start = src.index("function cookSlotWord(")
    end = src.index("\n  }", start) + 4
    out = nodeharness.run_node(src[start:end] + """
console.log(JSON.stringify([
  cookSlotWord('snack', 1), cookSlotWord('snack', 2),
  cookSlotWord('breakfast', 2), cookSlotWord('lunch', 2), cookSlotWord('dinner', 2),
]));
""")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["day", "days", "mornings", "lunches", "nights"]


def test_a_dinner_batch_drops_a_day_the_same_way(client):
    """GUARD. The green-on-main control for
    test_dropping_one_day_rescales_to_what_is_left: nothing about the
    rescale-on-drop rule moved, only which slots can reach it."""
    _household()
    _chickpeas()
    plan_id, ids = _week(slot="dinner")
    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[THU]])

    tools.set_cook_ahead(ids[MON], [ids[TUE]])

    cards = _cards(plan_id)
    assert cards[ids[MON]]["servings"] == 4
    assert _qty(cards[ids[MON]])["Chickpeas"] == "2 cans (15 oz)"
    assert cards[ids[THU]]["is_leftovers"] is False


# ---------- a "<date>:snack" chain already written to disk ----------
# The coverage whose absence let a wrong-dish regression through: this
# branch changes how links_to is parsed, so it has to be tested against
# data written BEFORE it. Every chain below is seeded by hand in exactly
# the shape pre-fix set_cook_ahead wrote, in BOTH row orders, because
# which of a day's two snacks a dict built from an unordered SELECT holds
# is the whole hazard.

def _seed_legacy_chain(source_id, source_date, target_ids, target_dates):
    """The old "<date>:slot" form, written straight into the rows."""
    import json

    from app.db import get_conn

    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
        (json.dumps({
            "make_double_for": sorted(f"{d}:snack" for d in target_dates),
            "make_double_note": "One batch covers the others too.",
        }), source_id),
    )
    for entry_id in target_ids:
        conn.execute(
            "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
            (json.dumps({"links_to": f"{source_date}:snack", "cook_ahead": True}), entry_id),
        )
    conn.commit()
    conn.close()


def _two_snack_week(chickpeas_first):
    """Tue/Wed/Thu, two snacks each — Apple Slices and Roasted Chickpeas —
    inserted in the given order so the ids run either way round."""
    tools.add_recipe("Apple Slices", ingredients=[{"item": "Apples", "qty": "2"}], default_servings=2)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    chick, apple = {}, {}
    order = ["Roasted Chickpeas", "Apple Slices"] if chickpeas_first else ["Apple Slices", "Roasted Chickpeas"]
    for day in (TUE, WED, THU):
        for dish in order:
            entry = tools.plan_meal(day, dish, slot="snack", weekly_plan_id=plan_id)["entry_id"]
            (chick if dish == "Roasted Chickpeas" else apple)[day] = entry
    return plan_id, chick, apple


class TestALegacySnackChainOnDisk:
    @pytest.mark.parametrize("chickpeas_first", [True, False], ids=["chickpeas-first", "apple-first"])
    def test_a_legacy_batch_on_the_other_snack_cannot_capture_this_one(self, client, chickpeas_first):
        """CATCH — and the one this branch introduced itself before
        reverting the parser. An Apple batch written in the old form, and
        an ordinary "batch cook these" on the chickpeas beside it: with
        "<date>:snack" resolvable, the chickpeas resolved to the APPLE row,
        counted its days twice and read Serves 10 over a shopping list for
        six, with Apple Slices headlined "Made ahead — Tuesday's Roasted
        Chickpeas". Nothing may name a dish that isn't its own."""
        _household()
        _chickpeas()
        plan_id, chick, apple = _two_snack_week(chickpeas_first)
        _seed_legacy_chain(apple[TUE], TUE, [apple[WED], apple[THU]], [WED, THU])

        tools.set_cook_ahead(chick[TUE], [chick[WED], chick[THU]])

        cards = _cards(plan_id)
        assert cards[chick[TUE]]["servings"] == 6, "two at each of its own three days"
        assert _qty(cards[chick[TUE]])["Chickpeas"] == "3 cans (15 oz)"
        assert cards[chick[TUE]]["covers_note"] == (
            "Cooking for 6 — enough for Tuesday, Wednesday, and Thursday."
        )
        for day in (WED, THU):
            assert cards[chick[day]]["is_leftovers"] is True
            assert cards[chick[day]]["leftovers_headline"] == "Made ahead — Tuesday’s Roasted Chickpeas"
            # The apples are somebody else's dish and stay their own cooks.
            assert cards[apple[day]]["is_leftovers"] is False
            assert _qty(cards[apple[day]])["Apples"] == "2"

    @pytest.mark.parametrize("chickpeas_first", [True, False], ids=["chickpeas-first", "apple-first"])
    def test_the_card_and_the_shopping_list_still_agree(self, client, chickpeas_first):
        """CATCH. The same seeding, read from the shopping side — the
        number on the card was five cans over a list of three, which is
        the disagreement this whole ticket is about."""
        _household()
        _chickpeas()
        plan_id, chick, apple = _two_snack_week(chickpeas_first)
        tools.approve_weekly_plan(plan_id)
        _seed_legacy_chain(apple[TUE], TUE, [apple[WED], apple[THU]], [WED, THU])

        tools.set_cook_ahead(chick[TUE], [chick[WED], chick[THU]])

        listed = {r["item"]: r["quantity"] for r in tools.list_grocery_list()}
        assert _qty(_cards(plan_id)[chick[TUE]])["Chickpeas"] == listed["Chickpeas"] == "3 cans (15 oz)"

    @pytest.mark.parametrize("chickpeas_first", [True, False], ids=["chickpeas-first", "apple-first"])
    def test_a_legacy_chain_with_no_second_batch_on_the_day_is_simply_unread(self, client, chickpeas_first):
        """GUARD for its first three assertions, CATCH for the one-tap heal below them —
        it is red on main in both orders, at the one-tap heal (`assert
        None == 6`), because main can't write a readable snack chain
        either. On its own, a chain in the old form reads as no chain at
        all: the days come back UNTICKED, so the household taps once and
        it is rewritten in the form that names its row. Left unhealed on
        purpose — a parser that reads it has to guess which snack it
        means, and the test above is what guessing costs."""
        _household()
        _chickpeas()
        plan_id, chick, apple = _two_snack_week(chickpeas_first)
        _seed_legacy_chain(chick[TUE], TUE, [chick[WED], chick[THU]], [WED, THU])

        cards = _cards(plan_id)
        assert cards[chick[TUE]]["servings"] is None
        assert [d["selected"] for d in cards[chick[TUE]]["cook_ahead"]["days"]] == [False, False]
        assert cards[chick[WED]]["is_leftovers"] is False

        # One tap, and it is a real batch in the shape that survives.
        tools.set_cook_ahead(chick[TUE], [chick[WED], chick[THU]])
        assert _cards(plan_id)[chick[TUE]]["servings"] == 6

    def test_a_legacy_dinner_chain_still_reads_exactly_as_it_did(self, client):
        """GUARD. A day has one dinner, so "<date>:dinner" does name a row
        — the planner's own chains keep working untouched, which is what
        narrowing the parser back to WEEK_SLOTS preserves."""
        import json

        from app.db import get_conn

        _household()
        _chickpeas()
        plan_id, ids = _week(slot="dinner")
        conn = get_conn()
        conn.execute(
            "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
            (json.dumps({"make_double_for": [f"{TUE}:dinner"]}), ids[MON]),
        )
        conn.execute(
            "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
            (json.dumps({"links_to": f"{MON}:dinner"}), ids[TUE]),
        )
        conn.commit()
        conn.close()

        cards = _cards(plan_id)
        assert cards[ids[MON]]["servings"] == 4
        assert cards[ids[TUE]]["is_leftovers"] is True
        assert cards[ids[TUE]]["leftovers_headline"] == "Leftovers — Monday’s Roasted Chickpeas"


# ---------- the same dish in both of a day's snack slots ----------

class TestADayThatRepeatsOneSnack:
    def test_the_picker_offers_one_chip_per_day(self, client):
        """CATCH. A chip reads "Tue" and the record behind it is a day and
        a slot, so two chips for one day offered a choice nothing could
        write down."""
        _household()
        _chickpeas()
        plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
        src = tools.plan_meal(MON, "Roasted Chickpeas", slot="snack", weekly_plan_id=plan_id)["entry_id"]
        first = tools.plan_meal(TUE, "Roasted Chickpeas", slot="snack", weekly_plan_id=plan_id)["entry_id"]
        tools.plan_meal(TUE, "Roasted Chickpeas", slot="snack", weekly_plan_id=plan_id)

        days = _cards(plan_id)[src]["cook_ahead"]["days"]

        assert [(d["entry_id"], d["date"]) for d in days] == [(first, TUE)]

    def test_one_batch_cannot_hold_two_rows_of_one_day(self, client):
        """CATCH. Ticking both wrote ONE key for two rows: the note read
        "enough for Monday, Tuesday, and Tuesday", and taking either row
        away removed the key they shared and collapsed the whole batch."""
        _household()
        _chickpeas()
        plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
        src = tools.plan_meal(MON, "Roasted Chickpeas", slot="snack", weekly_plan_id=plan_id)["entry_id"]
        a = tools.plan_meal(TUE, "Roasted Chickpeas", slot="snack", weekly_plan_id=plan_id)["entry_id"]
        b = tools.plan_meal(TUE, "Roasted Chickpeas", slot="snack", weekly_plan_id=plan_id)["entry_id"]

        assert isinstance(tools.set_cook_ahead(src, [a, b]), str), "refused, not half-written"

        tools.set_cook_ahead(src, [a])
        card = _cards(plan_id)[src]
        assert card["servings"] == 4
        assert card["covers_note"] == "Cooking for 4 — enough for Monday and Tuesday."
        # And the one the batch didn't claim is still an ordinary cook,
        # which is the stated cost of one chip per day.
        assert _cards(plan_id)[b]["is_leftovers"] is False


# ---------- the chip stands for the row this source already covers ----------
# The defect the first cut of "one chip per day" introduced, reachable in
# four ordinary taps and caught on re-review. Deduping on id alone means
# the chip can move to a different row the moment ANOTHER source lets go
# of the earlier one — and then the card says in words that it covers a
# day whose chip reads unticked, and ticking it puts two rows behind one
# key. Both are the symptoms cook_ahead_options' own docstring says the
# dedupe removes.

class TestTwoSourcesOverOneRepeatedDay:
    def _week(self):
        """Mon and Tue each cook the dish; Wednesday eats it twice."""
        _household()
        _chickpeas()
        plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
        mon = tools.plan_meal(MON, "Roasted Chickpeas", slot="snack", weekly_plan_id=plan_id)["entry_id"]
        tue = tools.plan_meal(TUE, "Roasted Chickpeas", slot="snack", weekly_plan_id=plan_id)["entry_id"]
        first = tools.plan_meal(WED, "Roasted Chickpeas", slot="snack", weekly_plan_id=plan_id)["entry_id"]
        second = tools.plan_meal(WED, "Roasted Chickpeas", slot="snack", weekly_plan_id=plan_id)["entry_id"]
        return plan_id, mon, tue, first, second

    def _wed_chip(self, plan_id, source_id):
        return next(d for d in _cards(plan_id)[source_id]["cook_ahead"]["days"] if d["date"] == WED)

    def test_the_chip_never_contradicts_the_card_beside_it(self, client):
        """CATCH — red against this branch's own first cut of the dedupe.
        Tuesday claims Wednesday's earlier snack, Monday is offered and
        claims the later one, Tuesday lets go: the earlier row is free
        again and used to win the chip on id alone, so Monday's card read
        "enough for Monday and Wednesday" over a Wednesday chip that read
        unticked."""
        plan_id, mon, tue, first, second = self._week()
        tools.set_cook_ahead(tue, [first])
        tools.set_cook_ahead(mon, [second])

        tools.set_cook_ahead(tue, [])

        card = _cards(plan_id)[mon]
        assert card["covers_note"] == "Cooking for 4 — enough for Monday and Wednesday."
        chip = self._wed_chip(plan_id, mon)
        assert (chip["entry_id"], chip["selected"]) == (second, True), (
            "the chip stands for the row this source actually covers"
        )

    def test_tapping_that_day_again_cannot_put_two_rows_behind_one_key(self, client):
        """CATCH. The second half of the same sequence: with the chip
        pointing at the free row, confirming it left BOTH Wednesday rows
        linked to Monday under a single "date:snack" key — "enough for
        Monday, Wednesday, and Wednesday" — and taking either row away
        then collapsed the whole batch."""
        plan_id, mon, tue, first, second = self._week()
        tools.set_cook_ahead(tue, [first])
        tools.set_cook_ahead(mon, [second])
        tools.set_cook_ahead(tue, [])

        tools.set_cook_ahead(mon, [self._wed_chip(plan_id, mon)["entry_id"]])

        card = _cards(plan_id)[mon]
        assert card["servings"] == 4
        assert card["covers_note"] == "Cooking for 4 — enough for Monday and Wednesday."
        linked = [
            r for r in (first, second)
            if _derived_from(r).get("links_to") == f"entry_id:{mon}"
        ]
        assert linked == [second], "exactly one row behind the day's one key"

    def test_the_other_route_to_the_same_state(self, client):
        """CATCH. No second source needed on the day itself: make the
        earlier Wednesday row a batch of its own (so it is filtered out of
        the picker), let Monday take the later one, then release it."""
        plan_id, mon, tue, first, second = self._week()
        later = tools.plan_meal(THU, "Roasted Chickpeas", slot="snack", weekly_plan_id=plan_id)["entry_id"]
        tools.set_cook_ahead(first, [later])
        tools.set_cook_ahead(mon, [second])

        tools.set_cook_ahead(first, [])

        chip = self._wed_chip(plan_id, mon)
        assert (chip["entry_id"], chip["selected"]) == (second, True)

    def test_unticking_the_day_releases_the_row_the_chip_hid(self, client):
        """CATCH against this branch's SECOND commit, and GREEN on `main`
        — say which, because this file's header defines CATCH as red on
        main and this one is not. Main cannot read a snack chain at all,
        so the state it guards is unreachable there. One chip stands for
        a whole day, so answering it has to answer for the row the dedupe
        hid — otherwise a sibling is left pointing at a batch the
        household has just called off."""
        plan_id, mon, tue, first, second = self._week()
        tools.set_cook_ahead(tue, [first])
        tools.set_cook_ahead(mon, [second])
        tools.set_cook_ahead(tue, [])

        tools.set_cook_ahead(mon, [])

        cards = _cards(plan_id)
        assert cards[mon]["servings"] is None
        for r in (first, second):
            assert cards[r]["is_leftovers"] is False
            assert _derived_from(r).get("links_to") is None

    def test_a_day_that_already_has_two_rows_behind_one_key_heals(self, client):
        """CATCH. The state the first cut could write is reachable in
        databases that ran it, so answering the day once has to settle it:
        the key keeps one row and the other goes back to cooking for
        itself."""
        import json

        from app.db import get_conn

        plan_id, mon, tue, first, second = self._week()
        conn = get_conn()
        conn.execute(
            "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
            (json.dumps({"make_double_for": [f"{WED}:snack"], "make_double_note": "x"}), mon),
        )
        for entry_id in (first, second):
            conn.execute(
                "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                (json.dumps({"links_to": f"entry_id:{mon}", "cook_ahead": True}), entry_id),
            )
        conn.commit()
        conn.close()
        assert _cards(plan_id)[mon]["servings"] == 6, "the broken state, as written"

        tools.set_cook_ahead(plan_id and mon, [self._wed_chip(plan_id, mon)["entry_id"]])

        assert _cards(plan_id)[mon]["servings"] == 4
        linked = [r for r in (first, second) if _derived_from(r).get("links_to")]
        assert len(linked) == 1

    def test_a_sibling_covered_by_another_batch_is_left_alone(self, client):
        """RED on `main` (so a CATCH by this file's header) and GREEN on
        this branch's second commit — the opposite way round from its
        sibling above, which is why both now name their baseline instead
        of leaving it to be read as a claim about main. It is a GUARD in
        the sense that matters: it is the limit on the release above, and
        it is pinned by mutation rather than by redness — deleting the
        `links_to == my_ref` scoping in cook_ahead.py turns it red along
        with two others. The picker answers for the day it offered, not
        for somebody else's batch. Tuesday keeps Wednesday's earlier
        snack while Monday answers for the later one."""
        plan_id, mon, tue, first, second = self._week()
        tools.set_cook_ahead(tue, [first])
        tools.set_cook_ahead(mon, [second])

        tools.set_cook_ahead(mon, [])

        cards = _cards(plan_id)
        assert cards[first]["is_leftovers"] is True
        assert cards[first]["leftovers_headline"] == "Made ahead — Tuesday’s Roasted Chickpeas"
        assert cards[tue]["servings"] == 4
        assert cards[second]["is_leftovers"] is False
