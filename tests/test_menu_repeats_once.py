"""
One entry, several days: a repeated breakfast, lunch or snack is sent
once and written onto each of its days.

Why it exists (production, 2026-09-21): the menu call took 36-39s at
medium effort and wrote 4,800-5,900 output tokens for a 35-slot week.
The recipes were already out of that call, so what was left was ~150
tokens of bookkeeping per slot, 35 times over — and most of those 35
slots were never 35 decisions, because the prompt has always asked for
the same breakfast/lunch/snack idea to repeat two or three times across
the week. Every repeat was written out again in full.

The model call is stubbed throughout — there is no way to make a real
one here, and none of what is under test is the model's behaviour. What
IS under test is the contract around it: that a folded entry becomes
real rows, that each row carries the entry's own reasoning and
derived_from, that the 21-slot guarantee still holds, and that every
hostile shape the prompt asks the model to avoid is handled in code
rather than trusted (this codebase's own rule: telling the generator
something is not the same as preventing it).
"""
import datetime
import json
import types

import pytest

from app import agent, tools
from app.db import get_conn


def _week_start(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _entry(date, slot, meal, **extra):
    return {
        "date": date, "slot": slot, "meal_name": meal, "is_new_recipe": False,
        "reasoning": "fits the week", **extra,
    }


def _folded_week(week: str) -> list[dict]:
    """
    A realistic 35-slot week written the NEW way: breakfasts, lunches and
    snacks folded to one entry per idea, dinners one per night.

    Two breakfast ideas, two lunch ideas, two snack ideas, seven dinners
    (one of them repeated, as a household asking for six dinners would
    get) — 13 entries covering 35 slots.
    """
    days = tools._week_dates(week)
    dinners = ["Chili", "Tilapia", "Stir Fry", "Roast", "Pasta", "Tacos", "Chili"]
    return (
        [_entry(days[0], "breakfast", "Oatmeal", dates=days[:4])]
        + [_entry(days[4], "breakfast", "Eggs on Toast", dates=days[4:])]
        + [_entry(days[0], "lunch", "Chicken Salad", dates=days[:3])]
        + [_entry(days[3], "lunch", "Lentil Soup", dates=days[3:])]
        + [_entry(days[0], "snack", "Apple and Peanut Butter", dates=days)]
        + [_entry(days[0], "snack", "Hummus and Carrots", dates=days)]
        + [_entry(day, "dinner", dinners[i]) for i, day in enumerate(days)]
    )


def _unfolded_week(week: str) -> list[dict]:
    """The exact same week written the OLD way: one entry per slot."""
    out = []
    for item in _folded_week(week):
        for date in item.get("dates") or [item["date"]]:
            row = dict(item)
            row.pop("dates", None)
            row["date"] = date
            out.append(row)
    return out


@pytest.fixture
def recipes():
    for name in ("Oatmeal", "Eggs on Toast", "Chicken Salad", "Lentil Soup",
                 "Apple and Peanut Butter", "Hummus and Carrots",
                 "Chili", "Tilapia", "Stir Fry", "Roast", "Pasta", "Tacos"):
        tools.add_recipe(name, ingredients=[{"item": "something", "qty": "1"}])


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    return _stub


def _slots(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT date, slot, freeform_meal, recipe_id, reasoning, derived_from_json, slot_state "
        "FROM meal_plan_entries WHERE weekly_plan_id = ? ORDER BY date, slot, id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _name_of(row):
    if row["recipe_id"]:
        conn = get_conn()
        r = conn.execute("SELECT name FROM recipes WHERE id = ?", (row["recipe_id"],)).fetchone()
        conn.close()
        return r["name"]
    return row["freeform_meal"]


# ---------- the expansion itself ----------

def test_one_entry_with_three_dates_becomes_three_rows():
    """The whole point. Red without _expand_repeated_dates."""
    out = agent._expand_repeated_dates([
        _entry("2026-09-28", "breakfast", "Oatmeal",
               dates=["2026-09-28", "2026-09-29", "2026-09-30"]),
    ])

    assert [r["date"] for r in out] == ["2026-09-28", "2026-09-29", "2026-09-30"]
    assert {r["meal_name"] for r in out} == {"Oatmeal"}
    assert all("dates" not in r for r in out), "the wire field never reaches the save loop"


def test_every_row_of_a_fold_carries_the_entrys_reasoning_and_derived_from():
    """
    The acceptance criterion in so many words: folding must lose nothing
    the household can read. Wednesday's card says what Monday's says.
    """
    out = agent._expand_repeated_dates([
        _entry("2026-09-28", "breakfast", "Oatmeal",
               dates=["2026-09-28", "2026-09-30"],
               reasoning="quick, and the oats are in",
               derived_from={"tags": ["rush"], "inputs": ["mood:easy"]}),
    ])

    assert [r["reasoning"] for r in out] == ["quick, and the oats are in"] * 2
    assert [r["derived_from"] for r in out] == [
        {"tags": ["rush"], "inputs": ["mood:easy"]},
    ] * 2


def test_the_rows_of_a_fold_do_not_share_one_derived_from():
    """
    Not a style point. The passes below this mutate an entry's
    derived_from in place — typed_requests writes `freeform` onto the
    slots a request shaped — and two nights aliasing one dict is how one
    night's correction silently becomes another night's.
    """
    out = agent._expand_repeated_dates([
        _entry("2026-09-28", "lunch", "Soup", dates=["2026-09-28", "2026-09-29"],
               derived_from={"inputs": ["cuisines:thai"]}),
    ])

    out[0]["derived_from"]["inputs"].append("held:nana")

    assert out[1]["derived_from"] == {"inputs": ["cuisines:thai"]}


def test_an_entry_with_no_dates_is_left_exactly_as_it_was():
    """
    A one-off is the common case and must cost nothing. GREEN either way —
    an expansion that does nothing satisfies it too; that is the point.
    """
    one = _entry("2026-09-28", "dinner", "Chili", derived_from={"tags": ["normal"]})

    out = agent._expand_repeated_dates([one])

    assert out == [one]


def test_the_date_is_included_even_when_dates_does_not_repeat_it():
    """
    `date` stays required and `dates` is additive, so both readings the
    model could have ("A, plus the repeats B and C" / "the repeats are A,
    B and C") land on the same answer. The union is the reading that
    cannot leave a day with a hole in it.
    """
    out = agent._expand_repeated_dates([
        _entry("2026-09-28", "breakfast", "Oatmeal", dates=["2026-09-29", "2026-09-30"]),
    ])

    assert [r["date"] for r in out] == ["2026-09-28", "2026-09-29", "2026-09-30"]


def test_a_date_repeated_inside_one_entry_is_written_once():
    out = agent._expand_repeated_dates([
        _entry("2026-09-28", "breakfast", "Oatmeal",
               dates=["2026-09-28", "2026-09-28", "2026-09-29"]),
    ])

    assert [r["date"] for r in out] == ["2026-09-28", "2026-09-29"]


def test_entry_order_is_the_models_own():
    """
    _honest_meal_names renames in order and carries each rename forward
    across the pass, so reordering here would change which of two
    colliding names gets corrected.
    """
    out = agent._expand_repeated_dates([
        _entry("2026-09-28", "dinner", "Chili"),
        _entry("2026-09-28", "breakfast", "Oatmeal", dates=["2026-09-28", "2026-09-29"]),
        _entry("2026-09-28", "lunch", "Soup"),
    ])

    assert [(r["slot"], r["date"]) for r in out] == [
        ("dinner", "2026-09-28"),
        ("breakfast", "2026-09-28"), ("breakfast", "2026-09-29"),
        ("lunch", "2026-09-28"),
    ]


# ---------- the hostile shapes ----------

def test_a_one_off_beats_a_repeat_for_the_day_they_both_claim():
    """
    "Oatmeal most mornings, pancakes on Saturday" is what two entries
    claiming one slot actually means, and it is the shape folding itself
    creates. Keeping the repeat would silently delete the one-off, which
    has nowhere else to go.
    """
    days = ["2026-09-28", "2026-09-29", "2026-09-30"]
    out = agent._expand_repeated_dates([
        _entry(days[0], "breakfast", "Oatmeal", dates=days),
        _entry(days[2], "breakfast", "Pancakes"),
    ])

    assert [(r["date"], r["meal_name"]) for r in out] == [
        (days[0], "Oatmeal"), (days[1], "Oatmeal"), (days[2], "Pancakes"),
    ]


def test_two_repeats_overlapping_leave_one_row_per_slot():
    """
    The duplicate case the audit calls out: two rows for one slot is how
    a night nobody is home ends up with groceries bought for it. First
    wins on a tie of specificity.
    """
    days = ["2026-09-28", "2026-09-29", "2026-09-30"]
    out = agent._expand_repeated_dates([
        _entry(days[0], "lunch", "Soup", dates=days[:2]),
        _entry(days[1], "lunch", "Salad", dates=days[1:]),
    ])

    assert [(r["date"], r["meal_name"]) for r in out] == [
        (days[0], "Soup"), (days[1], "Soup"), (days[2], "Salad"),
    ]


def test_two_different_snacks_may_share_a_day():
    """
    A day legitimately holds snacks_per_day of them, so the (date, slot)
    rule the three real meals get would halve every day's snacks.
    """
    days = ["2026-09-28", "2026-09-29"]
    out = agent._expand_repeated_dates([
        _entry(days[0], "snack", "Apple", dates=days),
        _entry(days[0], "snack", "Hummus", dates=days),
    ])

    assert len(out) == 4
    assert sorted((r["date"], r["meal_name"]) for r in out) == [
        (days[0], "Apple"), (days[0], "Hummus"),
        (days[1], "Apple"), (days[1], "Hummus"),
    ]


def test_the_same_snack_twice_on_one_day_is_written_once():
    """
    "The day's two snacks must differ from each other" is a real rule;
    repair_snack_clashes would otherwise pay for this later with a trade.

    GREEN either way, and it reads like a catch, so say so: an unexpanded
    list of these two entries happens to satisfy both assertions. What
    pins it is the mutation that keys a snack by (date, slot) like the
    three real meals, which reddens it.
    """
    out = agent._expand_repeated_dates([
        _entry("2026-09-28", "snack", "Apple", dates=["2026-09-28", "2026-09-29"]),
        _entry("2026-09-29", "snack", "apple"),
    ])

    assert [r["date"] for r in out] == ["2026-09-28", "2026-09-29"]
    assert {r["meal_name"].lower() for r in out} == {"apple"}


def test_a_dinner_with_several_dates_is_honoured_rather_than_refused():
    """
    The prompt says dinners are one per night. Being told is not being
    prevented — and refusing here would be the wrong repair anyway: a
    repeated dinner is something this app asks for elsewhere ("with
    dinners_per_week 3 over four days, exactly three different dinners,
    one of them on two nights"), and dropping the extra nights would
    leave holes for the gap audit to turn into questions about a night
    the model had already answered.
    """
    out = agent._expand_repeated_dates([
        _entry("2026-09-28", "dinner", "Chili", dates=["2026-09-28", "2026-09-30"]),
    ])

    assert [r["date"] for r in out] == ["2026-09-28", "2026-09-30"]


def test_a_date_outside_the_period_is_left_for_the_in_scope_pass():
    """
    Deliberately NOT a second copy of that rule: _generate_weekly_plan
    already drops an out-of-period slot and logs which ones. The next
    test drives that end to end.
    """
    out = agent._expand_repeated_dates([
        _entry("2026-09-28", "breakfast", "Oatmeal", dates=["2026-09-28", "2030-01-01"]),
    ])

    assert [r["date"] for r in out] == ["2026-09-28", "2030-01-01"]


def test_a_fold_reaching_outside_the_period_writes_only_the_days_asked_for(recipes, stub_model):
    days = tools._week_dates(_week_start())
    outside = (datetime.date.fromisoformat(days[-1]) + datetime.timedelta(days=3)).isoformat()
    week = _folded_week(_week_start())
    week[0]["dates"] = week[0]["dates"] + [outside]
    stub_model(week)

    plan = agent.generate_weekly_plan(_week_start())

    rows = _slots(plan["weekly_plan_id"])
    assert outside not in {r["date"] for r in rows}
    # And the days inside the period that the same fold named are planned,
    # so "nothing outside" can't be satisfied by folding nothing at all.
    breakfasts = {
        r["date"] for r in rows if r["slot"] == "breakfast" and r["slot_state"] == "planned"
    }
    assert set(days[:4]) <= breakfasts
    assert tools.audit_plan_slots(plan["weekly_plan_id"])["complete"] is True


def test_a_malformed_dates_value_never_takes_the_week_down():
    """Every shape a model can hand back, none of which may raise."""
    for bad in (None, "", 7, {"a": 1}, ["2026-09-29", None, 5, "  "], "2026-09-29"):
        out = agent._expand_repeated_dates([
            _entry("2026-09-28", "breakfast", "Oatmeal", dates=bad),
        ])
        assert out[0]["date"] == "2026-09-28"
        assert all("dates" not in r for r in out)


def test_a_wildly_long_dates_list_is_bounded():
    many = [f"2026-{m:02d}-{d:02d}" for m in (1, 2, 3) for d in range(1, 29)]
    out = agent._expand_repeated_dates([
        _entry(many[0], "snack", "Apple", dates=many),
    ])

    assert len(out) == agent._MAX_DATES_PER_ENTRY
    assert len(many) > agent._MAX_DATES_PER_ENTRY, "the fixture has to exceed the bound"


def test_a_non_list_or_non_dict_is_passed_through_untouched():
    """GREEN either way. A guard on never raising over a shape."""
    assert agent._expand_repeated_dates("not a list") == "not a list"
    assert agent._expand_repeated_dates([None, 3]) == [None, 3]


def test_an_entry_with_no_date_at_all_is_left_for_the_save_loop():
    """
    The save loop's own `if not meal_date: continue` stays the one place
    that decides what a dateless entry means. GREEN either way — a guard
    on the expansion NOT deciding it.
    """
    one = {"slot": "dinner", "meal_name": "Chili", "reasoning": "x"}

    assert agent._expand_repeated_dates([one]) == [one]


# ---------- the whole week, end to end ----------

def test_a_thirty_five_slot_week_folds_to_about_twenty_entries(recipes, stub_model):
    """
    The acceptance criterion, measured on the fixture the rest of this
    file uses: 35 slots, 13 entries.
    """
    week = _week_start()
    folded = _folded_week(week)
    unfolded = _unfolded_week(week)

    assert len(unfolded) == 35
    assert len(folded) == 13
    assert len(agent._expand_repeated_dates(folded)) == 35


def test_folding_a_realistic_week_cuts_the_payload_by_more_than_a_third():
    """
    The size criterion, kept checkable rather than left in a commit
    message — an assertion on the JSON the model has to write for one
    35-slot week, the same week either way.

    AN ESTIMATE, and only of the JSON half. There is no working key in
    this sandbox so the api_calls ledger cannot be read, and the model's
    thinking — roughly half of the 4,800-5,900 output tokens production
    measured on 2026-09-21 — is unaffected by folding either way. A
    fixture with longer dish names and fuller derived_from (the shape a
    real week has) measures the same 54-55%; see the decision log.
    """
    week = _week_start()
    long_way = json.dumps({"days": _unfolded_week(week)}, separators=(",", ":"))
    short_way = json.dumps({"days": _folded_week(week)}, separators=(",", ":"))

    assert len(short_way) < len(long_way) * 2 / 3
    # chars/4, the standard rough conversion, said as one.
    assert len(long_way) // 4 - len(short_way) // 4 > 400


def test_the_folded_week_and_the_unfolded_week_plan_the_same_slots(recipes, stub_model):
    """
    Like for like: the same week written both ways has to produce the
    same plan. This is the test that would catch a fold quietly losing a
    day, or writing one twice.
    """
    week = _week_start()

    stub_model(_unfolded_week(week))
    long_way = _slots(agent.generate_weekly_plan(week)["weekly_plan_id"])

    stub_model(_folded_week(week))
    short_way = _slots(agent.generate_weekly_plan(week)["weekly_plan_id"])

    def shape(rows):
        return sorted(
            (r["date"], r["slot"], _name_of(r), r["reasoning"], r["slot_state"])
            for r in rows
        )

    assert shape(short_way) == shape(long_way)


def test_a_folded_week_still_audits_complete(recipes, stub_model):
    """
    The 21-slot guarantee — no gaps and no duplicates.

    Read the `complete` half for what it is worth: the gap audit fills a
    missing slot with an OPEN question and those count as present, so an
    UNEXPANDED week is "complete" too, with ten of its twenty-one slots
    handed back as questions. What only holds with the expansion is the
    last assertion — every one of the 21 is a slot somebody planned.
    """
    week = _week_start()
    stub_model(_folded_week(week))

    plan = agent.generate_weekly_plan(week)

    audit = tools.audit_plan_slots(plan["weekly_plan_id"])
    assert audit["complete"] is True
    assert audit["present"] == 21
    assert audit["missing"] == []
    assert audit["duplicated"] == []
    planned = [
        r for r in _slots(plan["weekly_plan_id"])
        if r["slot"] in tools.WEEK_SLOTS and r["slot_state"] == "planned"
    ]
    assert len(planned) == 21


def test_a_folded_week_still_gives_every_day_its_snacks(recipes, stub_model):
    """
    Counting the DAYS as well as the snacks matters: an unexpanded week
    puts both snack ideas on day one and none anywhere else, which reads
    as "every day that has snacks has two" if you only look at the values.
    """
    week = _week_start()
    stub_model(_folded_week(week))

    plan = agent.generate_weekly_plan(week)

    per_day = {d: 0 for d in tools._week_dates(week)}
    for row in _slots(plan["weekly_plan_id"]):
        if row["slot"] == "snack":
            per_day[row["date"]] = per_day.get(row["date"], 0) + 1
    assert set(per_day.values()) == {2}, per_day


def test_the_why_this_line_reads_the_same_on_every_day_of_a_fold(recipes, stub_model):
    """
    The household taps "why this?" on Wednesday's breakfast and reads
    what Monday's said. Stored, not re-derived.
    """
    week = _week_start()
    days = tools._week_dates(week)
    plan_days = _folded_week(week)
    plan_days[0]["reasoning"] = "oats are in, and mornings are quick"
    plan_days[0]["derived_from"] = {"tags": ["rush"]}
    stub_model(plan_days)

    plan = agent.generate_weekly_plan(week)

    rows = [
        r for r in _slots(plan["weekly_plan_id"])
        if r["slot"] == "breakfast" and r["date"] in days[:4]
    ]
    assert len(rows) == 4
    assert {r["reasoning"] for r in rows} == {"oats are in, and mornings are quick"}
    assert {json.loads(r["derived_from_json"] or "{}").get("tags", [None])[0] for r in rows} == {"rush"}


def test_an_out_night_still_beats_a_folded_dinner(recipes, stub_model, monkeypatch):
    """
    The tag wins over the model, folded or not: a night nobody is home is
    planned_empty and buys nothing.

    A GUARD rather than a catch — it is green without the expansion too,
    because the out-night pass clears whatever is in that slot first. It
    is here because folding gives a dinner a second way onto a night the
    household was promised nothing would be bought for.
    """
    week = _week_start()
    days = tools._week_dates(week)
    tools.save_week_intake(week, night_tags={days[2]: ["out"]})
    plan_days = _folded_week(week)
    # The model sends a dinner for the out night anyway, as part of a fold.
    plan_days[-1] = _entry(days[-1], "dinner", "Chili", dates=[days[-1], days[2]])
    stub_model(plan_days)

    plan = agent.generate_weekly_plan(week)

    out_night = [
        r for r in _slots(plan["weekly_plan_id"])
        if r["date"] == days[2] and r["slot"] == "dinner"
    ]
    assert [r["slot_state"] for r in out_night] == ["planned_empty"]


# ---------- the streamed screen ----------

def test_the_screen_is_told_about_every_day_of_a_fold(monkeypatch):
    """
    The scanner streams whole ENTRIES and an entry can now be three
    mornings, but both readers of the "day" event take `body.date` and
    paint that one card. Without the fan-out a folded week fills three
    days out of seven while it draws.
    """
    from tests.test_streaming_and_effort import (
        _Usage, _delta_event, _stub_streaming_client, _tool_block,
    )

    tool_input = {"days": [
        {"date": "2026-09-28", "dates": ["2026-09-28", "2026-09-29"],
         "slot": "breakfast", "meal_name": "Oatmeal"},
        {"date": "2026-09-28", "slot": "dinner", "meal_name": "Chili"},
    ]}
    final = types.SimpleNamespace(
        content=[_tool_block(tool_input)], stop_reason="tool_use",
        usage=_Usage(input_tokens=10, output_tokens=10),
    )
    _stub_streaming_client(monkeypatch, [([_delta_event(json.dumps(tool_input))], final)])

    seen = []
    token = agent._WEEK_GEN_PROGRESS.set(seen.append)
    try:
        agent.generate_weekly_plan_llm({"week_start_date": "2026-09-28", "day_count": 7})
    finally:
        agent._WEEK_GEN_PROGRESS.reset(token)

    assert [(s["date"], s["slot"]) for s in seen] == [
        ("2026-09-28", "breakfast"), ("2026-09-29", "breakfast"),
        ("2026-09-28", "dinner"),
    ]
    assert all("dates" not in s for s in seen), "the client reads body.date and nothing else"


def test_the_model_wrapper_hands_back_what_the_model_said_report_and_all(monkeypatch):
    """
    Where the expansion lives is the whole of its blast radius, and it is
    deliberately NOT here: this wrapper's job is "what the model said",
    and every test in this repo stubs it with a plain list of
    one-entry-per-slot days. Expanding on the save path instead means a
    stubbed week written the folded way goes through the real expansion
    rather than past it — which is the only reason the end-to-end tests
    above test anything at all.

    What this pins is that the fold rides through untouched and the
    report still comes with it.
    """
    from tests.test_streaming_and_effort import _final, _stub_streaming_client

    tool_input = {"days": [
        {"date": "2026-09-28", "dates": ["2026-09-28", "2026-09-29", "2026-09-30"],
         "slot": "lunch", "meal_name": "Soup"},
    ], "honoured_requests": [{"words": "soup please", "label": "soup lunches"}]}
    _stub_streaming_client(monkeypatch, [_final(tool_input)])

    days = agent.generate_weekly_plan_llm({"week_start_date": "2026-09-28", "day_count": 7})

    assert len(days) == 1
    assert days[0]["dates"] == ["2026-09-28", "2026-09-29", "2026-09-30"]
    assert days.report["honoured_requests"] == [{"words": "soup please", "label": "soup lunches"}]


def test_a_folded_weeks_report_survives_the_expansion(monkeypatch, recipes):
    """
    The expansion rebuilds the list, and GeneratedDays.report is what the
    draft's opening line is built from — drop it and the opener falls
    back to plain labels and forgets every unmet request.
    """
    days = agent.GeneratedDays([
        _entry("2026-09-28", "lunch", "Soup", dates=["2026-09-28", "2026-09-29"]),
    ])
    days.report = {"honoured_requests": [{"words": "soup", "label": "soup lunches"}],
                   "unmet_requests": []}

    out = agent._expand_repeated_dates(days)

    assert len(out) == 2
    assert out.report["honoured_requests"] == [{"words": "soup", "label": "soup lunches"}]


# ---------- the prompt and the schema ----------

def test_the_schema_offers_dates_and_still_requires_date(monkeypatch):
    """A source marker: red against a tree with no `dates` in the schema,
    green against one that has it and expands nothing."""
    item = agent._GENERATE_WEEKLY_PLAN_TOOL["input_schema"]["properties"]["days"]["items"]

    assert "dates" in item["properties"]
    assert item["properties"]["dates"]["type"] == "array"
    assert "date" in item["required"]
    assert "dates" not in item["required"]


def test_the_prompt_asks_for_a_repeat_once_and_keeps_dinners_per_night(monkeypatch):
    """The other source marker, and the one that matters most to a later
    edit: the two sentences that used to count ENTRIES would now be a
    second, contradictory instruction to the model."""
    captured = {}

    def _fake_stream(client, **kwargs):
        captured["text"] = kwargs["content"][0]["text"]
        return []

    monkeypatch.setattr(agent, "_stream_forced_tool_call", _fake_stream)
    monkeypatch.setattr(agent, "_client", lambda: object())
    agent.generate_weekly_plan_llm({"week_start_date": _week_start(), "day_count": 7})

    text = captured["text"]
    assert "SEND A REPEAT ONCE" in text
    assert "DINNER IS NOT FOLDED" in text
    # The two sentences that used to count ENTRIES and would now be a
    # second, contradictory instruction.
    assert "5 separate entries" not in text
    assert "21 entries minimum" not in text
    assert "whether that takes 21 entries or 8" in text
