"""
Speed: the first week builds in ~30s and never changes after it's shown.

Production, 2026-09-30, a new household's first week: the menu call took
34.6s; allergen_gate.split_safe then held 11 slots back; and six re-picks
ran ONE AFTER ANOTHER at 6-8s each (42.5s) — four of them re-picking the
same scramble on four different mornings, each writing a full recipe —
until "allergen re-pick budget spent" left two slots open. Meanwhile the
onboarding reveal had already painted every one of those held dishes as
the model wrote it, and swapped them out under the person's thumb when
the saved week arrived.

Pinned here:

  1. A held-back dish is re-picked ONCE for every day it appears, and
     the replacement lands on all of those days.
  2. Different held-back dishes are re-picked at the same time.
  3. The re-pick is the quick "name a dish" call, and its dish is saved
     PENDING (details_pending=1 with a dish_note) for the recipe pass.
  4. A dish that fails the allergy check never reaches the stream — a
     placeholder does, and the replacement fills it when it lands.
  5. The week call no longer asks for a per-slot `reasoning` line, and
     the readers of that field cope without it.
  6. The onboarding reveal tries once more on its own before "Try again".

Every test here fails on origin/main (7a6139c) and passes on this branch.
"""
from __future__ import annotations

import datetime
import json
import logging
import shutil
import threading
from pathlib import Path

import pytest

from app import agent, tools
from app.tools import allergen_gate, plan_quality, swap_in_place as sip
from conftest import household_today, prompt_literals
from tests import nodeharness


TODAY = household_today()
WEEK_START = TODAY.isoformat()
DAYS = [(TODAY + datetime.timedelta(days=i)).isoformat() for i in range(7)]


@pytest.fixture
def emilys_house(monkeypatch):
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["pineapple allergy"])
    # No food groups on Chili: the plate pass skips a meal it can't read,
    # so nothing here reaches the sides model.
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    # Every OTHER pass that re-picks through the swap's full picker (the
    # all-Chili week's run-breaking, count folds) gets nothing back — this
    # file counts only the held-back re-picks.
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: {})


def _week(meal="Chili"):
    return [
        {"date": day, "slot": slot, "meal_name": meal, "is_new_recipe": False}
        for day in DAYS for slot in tools.WEEK_SLOTS
    ]


def _without(days, pairs):
    return [d for d in days if (d["date"], d["slot"]) not in pairs]


def _scramble_on(dates):
    """The production case: a menu-pass breakfast (no ingredient list, a
    dish_note) folded across several mornings, with the allergen in it."""
    return {
        "date": dates[0], "dates": list(dates), "slot": "breakfast",
        "meal_name": "Tropical Breakfast Scramble", "is_new_recipe": True,
        "dish_note": "soft eggs with a pineapple salsa and toast",
        "food_groups": ["protein", "carb"],
    }


def _quick(name, mains=("Eggs", "Spinach", "Toast")):
    return {
        "meal_name": name, "ingredients": list(mains),
        "dish_note": "soft scrambled eggs folded with wilted spinach",
        "food_groups": ["protein", "carb", "vegetable"], "cuisine": "American",
        "main_protein": "eggs", "prep_time_minutes": 5, "cook_time_minutes": 10,
    }


def _slot(plan_id, date, slot):
    for m in tools.get_weekly_plan(plan_id)["meals"]:
        if m["date"] == date and m["slot"] == slot:
            return m
    return None


def _recipe(name):
    return next((r for r in tools.list_recipes() if r["name"].lower() == name.lower()), None)


# ---------- 1 + 3. one re-pick per dish, the quick call, saved pending ----------

def test_a_dish_held_on_four_mornings_is_repicked_once_and_lands_on_all_four(emilys_house, monkeypatch):
    mornings = DAYS[:4]
    days = _without(_week(), {(d, "breakfast") for d in mornings}) + [_scramble_on(mornings)]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    asked = []

    def quick(context):
        asked.append(context)
        return _quick("Spinach Egg Scramble")

    monkeypatch.setattr(allergen_gate, "quick_pick", quick, raising=False)

    plan_id = agent.generate_weekly_plan(WEEK_START)["weekly_plan_id"]

    held = [c for c in asked if c["slot"] == "breakfast"]
    assert len(held) == 1, "one dish held on four mornings is one re-pick, not four"
    assert "Tropical Breakfast Scramble" in held[0]["avoid"]
    assert held[0]["must_not_contain"] == ["Emily: pineapple allergy"]
    assert sorted(held[0]["also_on"]) == sorted(mornings[1:])
    for d in mornings:
        row = _slot(plan_id, d, "breakfast")
        assert row["meal"] == "Spinach Egg Scramble", d
        assert row["slot_state"] == "planned"
    # Written later, with the others: a pending recipe, like a new dish from
    # the menu pass — its note carries the main items for the recipe writer.
    saved = _recipe("Spinach Egg Scramble")
    assert saved["details_pending"] is True
    assert saved["ingredients"] == []
    assert "wilted spinach" in saved["dish_note"] and "Eggs" in saved["dish_note"]
    assert saved["id"] in {r["id"] for r in tools.pending_recipes_for_plan(plan_id)}
    assert _recipe("Tropical Breakfast Scramble") is None, "the held dish is never saved"


def test_the_quick_pick_is_the_trimmed_low_effort_call(monkeypatch):
    seen = {}

    class _Block:
        type = "tool_use"
        input = {"meal_name": "Spinach Egg Scramble", "ingredients": ["Eggs"], "dish_note": "soft eggs"}

    class _Resp:
        stop_reason = "tool_use"
        content = [_Block()]

    monkeypatch.setattr(agent, "_client", lambda: object())
    monkeypatch.setattr(agent, "_create_with_retry", lambda client, **kw: (seen.update(kw), _Resp())[1])

    out = allergen_gate.quick_pick({"date": DAYS[0], "slot": "breakfast"})

    assert out["meal_name"] == "Spinach Egg Scramble"
    assert seen["tools"][0]["name"] == "submit_quick_pick"
    props = seen["tools"][0]["input_schema"]["properties"]
    assert "instructions" not in props, "a pick, not a recipe"
    assert props["ingredients"]["items"] == {"type": "string"}
    assert seen["max_tokens"] <= 1200
    assert seen["output_config"] == agent._effort_config("picks")


def test_a_quick_pick_that_still_clashes_is_tried_once_more_then_every_day_goes_open(emilys_house, monkeypatch):
    mornings = DAYS[:3]
    days = _without(_week(), {(d, "breakfast") for d in mornings}) + [_scramble_on(mornings)]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    asked = []

    def stubborn(context):
        asked.append(context)
        return _quick(f"Pineapple Toast {len(asked)}", mains=("Pineapple", "Bread"))

    monkeypatch.setattr(allergen_gate, "quick_pick", stubborn, raising=False)

    plan_id = agent.generate_weekly_plan(WEEK_START)["weekly_plan_id"]

    assert len(asked) == sip.MAX_PICK_ATTEMPTS
    assert "Pineapple Toast 1" in asked[1]["avoid"], "the failed attempt joins avoid"
    for d in mornings:
        row = _slot(plan_id, d, "breakfast")
        assert row["slot_state"] == "open"
        assert row["open_reason"] == "I couldn’t find a breakfast without pineapple for Emily."


# ---------- 2. every held dish at the same time, and timed ----------

def test_different_held_dishes_are_repicked_at_the_same_time(emilys_house, monkeypatch, caplog):
    nights = DAYS[1:4]
    days = _without(_week(), {(d, "dinner") for d in nights}) + [
        {"date": d, "slot": "dinner", "meal_name": f"Pineapple Night {i}", "is_new_recipe": True,
         "dish_note": "pineapple glaze"}
        for i, d in enumerate(nights)
    ]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    # Passes only if all three picks are in flight at once: a re-pick loop
    # that waits for one before starting the next breaks the barrier.
    barrier = threading.Barrier(len(nights), timeout=5)
    households = []

    def quick(context):
        households.append(tools.household_id())
        barrier.wait()
        return _quick(f"Lemon Chicken {context['date']}", mains=("Chicken thighs", "Lemon", "Rice"))

    monkeypatch.setattr(allergen_gate, "quick_pick", quick, raising=False)

    with caplog.at_level(logging.INFO, logger="home_manager"):
        plan_id = agent.generate_weekly_plan(WEEK_START)["weekly_plan_id"]

    for d in nights:
        row = _slot(plan_id, d, "dinner")
        assert row["meal"] == f"Lemon Chicken {d}", d
    # Each worker ran as THIS household (a copied context), not the default.
    assert set(households) == {tools.household_id()}
    assert any("Allergen re-pick:" in r.message and "wall" in r.message and "in parallel" in r.message
               for r in caplog.records), "re-pick time is logged"


# ---------- 4. the gate runs before the reveal ----------

def _streamed_generation(monkeypatch, items):
    """Drive the REAL generate_weekly_plan_llm, with the model's stream
    replaced by one that hands `items` to on_item as they "arrive"."""
    sent = []

    def fake_stream(client, *, on_item=None, **kw):
        for item in items:
            if on_item:
                on_item(dict(item))
        return agent.GeneratedDays([dict(i) for i in items])

    monkeypatch.setattr(agent, "_stream_forced_tool_call", fake_stream)
    token = agent._WEEK_GEN_PROGRESS.set(sent.append)
    return sent, token


def test_a_dish_that_fails_the_allergy_check_is_never_sent_to_the_screen(emilys_house, monkeypatch):
    mornings = DAYS[:2]
    items = _without(_week(), {(d, "breakfast") for d in mornings}) + [_scramble_on(mornings)]
    monkeypatch.setattr(allergen_gate, "quick_pick", lambda ctx: _quick("Spinach Egg Scramble"), raising=False)
    sent, token = _streamed_generation(monkeypatch, items)
    try:
        agent.generate_weekly_plan(WEEK_START)
    finally:
        agent._WEEK_GEN_PROGRESS.reset(token)

    assert not any("Tropical" in json.dumps(e) or "pineapple" in json.dumps(e).lower() for e in sent), \
        "the held dish never reaches a screen, by name or by note"
    placeholders = [e for e in sent if e.get("slot_state") == "held"]
    assert sorted(e["date"] for e in placeholders) == sorted(mornings), "one placeholder per day it was on"
    assert {e["placeholder"] for e in placeholders} == {"Finding another breakfast…"}
    assert all(not e.get("meal_name") for e in placeholders)
    # Then the replacement fills each of those rows, as it lands.
    filled = [e for e in sent if e.get("replaces_held")]
    assert sorted(e["date"] for e in filled) == sorted(mornings)
    assert {e["meal_name"] for e in filled} == {"Spinach Egg Scramble"}
    assert all(sent.index(p) < sent.index(f) for p in placeholders for f in filled)


def test_a_clean_dish_streams_exactly_as_before(emilys_house, monkeypatch):
    items = _week()
    sent, token = _streamed_generation(monkeypatch, items)
    try:
        agent.generate_weekly_plan(WEEK_START)
    finally:
        agent._WEEK_GEN_PROGRESS.reset(token)
    assert len(sent) == len(items)
    assert all(e.get("meal_name") == "Chili" and e.get("slot_state") != "held" for e in sent)


# ---------- 5. no per-slot reasoning ----------

def test_the_week_call_no_longer_asks_for_a_reasoning_line():
    item = agent._GENERATE_WEEKLY_PLAN_TOOL["input_schema"]["properties"]["days"]["items"]
    assert "reasoning" not in item["properties"]
    assert "reasoning" not in item["required"]
    prompt = prompt_literals(agent.generate_weekly_plan_llm)
    assert "fill in reasoning" not in prompt
    assert "`reasoning` line" not in prompt
    assert "in the reasoning" not in prompt and "slot's reasoning" not in prompt


def test_a_week_without_reasoning_saves_and_the_quality_pass_says_nothing_about_it(emilys_house, monkeypatch, caplog):
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _week())
    with caplog.at_level(logging.WARNING, logger="home_manager"):
        plan_id = agent.generate_weekly_plan(WEEK_START)["weekly_plan_id"]
    assert "has no reasoning at all" not in caplog.text
    entries = plan_quality._load_plan_entries(plan_id)
    assert plan_quality._reasoning_is_specific(entries, {}) == []
    # The Cook screen's reader copes with the column empty.
    assert tools.get_weekly_plan(plan_id)["meals"]


# ---------- 6. the reveal tries once more on its own ----------

REPO = Path(__file__).resolve().parents[1]
ONBOARDING = (REPO / "static" / "onboarding.html").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page's own functions")


def _extract(name: str, source: str = ONBOARDING) -> str:
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
    head = "async " if source[max(0, start - 6):start] == "async " else ""
    return head + source[start:j + 1]


_PRELUDE = """
var posts = [];
var statusReads = [];
var upserts = [];
var streams = [];
var statusAnswers = [];
var REVEAL_WAIT_CAP_MS = 40;
var REVEAL_POLL_FIRST_MS = 1;
var REVEAL_POLL_MAX_MS = 2;
var firstPlanStart = 'this_week';
var revealDraftWeekStart = '';
var revealStreamDays = { cleared: 0, clear: function () { this.cleared++; } };
function upsertRevealDay(m) { upserts.push(m); }
var document = { getElementById: function () { return null; } };
function streamOf(parts, ending) {
  var enc = new TextEncoder();
  var i = 0;
  return { ok: true, body: { getReader: function () { return { read: function () {
    if (i < parts.length) return Promise.resolve({ done: false, value: enc.encode(parts[i++]) });
    if (ending === 'drop') return Promise.reject(new TypeError('network error'));
    return Promise.resolve({ done: true });
  } }; } } };
}
var Api = { fetch: function (url, opts) {
  if (url.indexOf('/generate/status') !== -1) {
    statusReads.push(url);
    var answer = statusAnswers.length > 1 ? statusAnswers.shift() : statusAnswers[0];
    return Promise.resolve({ ok: true, json: function () { return Promise.resolve({ run: answer === undefined ? null : answer }); } });
  }
  posts.push(JSON.parse(opts.body));
  var next = streams.shift();
  if (next === 'refuse') return Promise.reject(new TypeError('Load failed'));
  return Promise.resolve(streamOf(next[0], next[1]));
} };
"""

_STATUS = 'event: status\ndata: {"message": "Drafting your week\\u2026", "week_start": "2026-09-28"}\n\n'
_DAY = 'event: day\ndata: {"date": "2026-09-28", "slot": "dinner", "meal_name": "Chili"}\n\n'
_DONE = 'event: done\ndata: {"weekly_plan_id": 77, "week_start_date": "2026-09-28", "meals": [{"meal": "Chili"}]}\n\n'
_ERROR = 'event: error\ndata: {"status": 503, "detail": "busy"}\n\n'


def _run(body: str) -> dict:
    functions = "\n".join(_extract(n) for n in (
        "revealLostContact", "revealNewRunToken", "streamFirstPlan", "revealWorthRetrying", "revealWait",
        "revealDraftLanded", "draftFirstPlanWithOneRetry",
    ))
    script = _PRELUDE + functions + "\n(async function () {\n" + body + "\n})().catch(function (e) {" \
        " console.log(JSON.stringify({ harnessError: String(e && e.stack || e) })); });\n"
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert "harnessError" not in out, out["harnessError"]
    return out


def _attempt(streams_js: str, status=None) -> dict:
    answers = status if isinstance(status, list) else [status]
    return _run(f"""
      streams = {streams_js};
      statusAnswers = {json.dumps(answers)};
      var result = null, failed = null;
      try {{ result = await draftFirstPlanWithOneRetry(null); }}
      catch (e) {{ failed = e.message; }}
      console.log(JSON.stringify({{ result: result, failed: failed, posts: posts.length,
        tokens: posts.map(function (p) {{ return p.run_token; }}), statusReads: statusReads,
        cleared: revealStreamDays.cleared, upserts: upserts.length }}));
    """)


@_needs_node
def test_a_dropped_stream_whose_draft_landed_is_picked_up_without_drafting_again():
    out = _attempt(json.dumps([[[_STATUS, _DAY], "drop"]]), status={"state": "done", "plan_id": 77})
    assert out["failed"] is None
    assert out["result"]["weekly_plan_id"] == 77 and out["result"]["reattached"] is True
    assert out["posts"] == 1, "no second draft when the first one landed"
    assert out["statusReads"] and out["statusReads"][0].startswith("/api/week/2026-09-28/generate/status?run=")
    assert out["tokens"][0] and out["statusReads"][0].endswith(out["tokens"][0])


@_needs_node
def test_a_dropped_stream_the_server_never_started_is_asked_for_once_more():
    out = _attempt(json.dumps([[[_STATUS, _DAY], "drop"], [[_STATUS, _DONE], "end"]]), status=None)
    assert out["failed"] is None
    assert out["result"]["weekly_plan_id"] == 77
    assert out["posts"] == 2
    assert out["tokens"][0] != out["tokens"][1]
    assert out["cleared"] == 1, "the retry keeps what is already on screen"


@_needs_node
def test_a_dropped_stream_still_running_is_waited_on_not_drafted_twice():
    """The run stays "running" through the prep and defrost steps after the
    save; asking again straight away would start a second full draft."""
    out = _attempt(json.dumps([[[_STATUS, _DAY], "drop"]]),
                   status=[{"state": "running"}, {"state": "running"}, {"state": "done", "plan_id": 77}])
    assert out["failed"] is None
    assert out["result"]["weekly_plan_id"] == 77 and out["result"]["reattached"] is True
    assert out["posts"] == 1
    assert len(out["statusReads"]) == 3


@_needs_node
def test_a_draft_still_running_at_the_cap_is_not_doubled():
    out = _attempt(json.dumps([[[_STATUS, _DAY], "drop"]]), status=[{"state": "running"}])
    assert out["result"] is None and out["failed"]
    assert out["posts"] == 1, "never a second draft behind one still going"
    assert len(out["statusReads"]) >= 2


@_needs_node
def test_a_dropped_draft_that_failed_on_the_server_is_retried_only_for_5xx():
    busy = _attempt(json.dumps([[[_STATUS], "drop"], [[_STATUS, _DONE], "end"]]),
                    status={"state": "failed", "error": {"status": 503}})
    assert busy["result"]["weekly_plan_id"] == 77 and busy["posts"] == 2
    refused = _attempt(json.dumps([[[_STATUS], "drop"], [[_STATUS, _DONE], "end"]]),
                       status={"state": "failed", "error": {"status": 400}})
    assert refused["result"] is None and refused["posts"] == 1


@_needs_node
def test_a_400_is_not_retried():
    """"Every dish clashed" fails the same way twice; straight to Try again."""
    clash = 'event: error\ndata: {"status": 400, "detail": "every dish clashed"}\n\n'
    out = _attempt(json.dumps([[[_STATUS, clash], "end"], [[_STATUS, _DONE], "end"]]))
    assert out["failed"] == "every dish clashed"
    assert out["posts"] == 1


@_needs_node
def test_an_error_frame_is_retried_once_on_its_own():
    out = _attempt(json.dumps([[[_STATUS, _ERROR], "end"], [[_STATUS, _DONE], "end"]]))
    assert out["failed"] is None and out["result"]["weekly_plan_id"] == 77
    assert out["posts"] == 2


@_needs_node
def test_two_failures_in_a_row_reach_try_again():
    out = _attempt(json.dumps(["refuse", [[_STATUS, _ERROR], "end"]]))
    assert out["failed"] == "busy"
    assert out["posts"] == 2, "one try of its own, then the person decides"
    assert out["result"] is None


def test_the_reveal_shows_a_held_row_quietly_and_never_offers_to_swap_it():
    body = _extract("revealSlotFromStream") + _extract("revealSlotDishHtml")
    assert "slot_state === 'held'" in body
    assert "reveal-slot-dish-held" in body
    assert "Finding another" in body
    card = _extract("revealDayCardHtml")
    assert "s.state === 'planned' && !!s.dish" in card, "Swap only under a planned dish"


@_needs_node
def test_a_folded_frame_lands_on_every_one_of_its_dates():
    script = (
        "var put = [];\n"
        "function revealPutSlot(date, s) { put.push([date, s.dish]); }\n"
        "function revealShowDays() {}\nfunction renderRevealStrip() {}\n"
        + _extract("revealMinutesMeta") + _extract("revealSlotFromStream") + _extract("upsertRevealDay")
        + "\nupsertRevealDay({date: '2026-09-28', dates: ['2026-09-28', '2026-09-29', '2026-09-30'],"
          " slot: 'breakfast', meal_name: 'Oatmeal'});\n"
          "console.log(JSON.stringify(put));\n"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout.strip().splitlines()[-1]) == [
        ["2026-09-28", "Oatmeal"], ["2026-09-29", "Oatmeal"], ["2026-09-30", "Oatmeal"],
    ]


# ---------- verification follow-ups (2026-09-30) ----------

def _dinners_held(nights, name=lambda i: f"Pineapple Night {i}"):
    return _without(_week(), {(d, "dinner") for d in nights}) + [
        {"date": d, "slot": "dinner", "meal_name": name(i), "is_new_recipe": True, "dish_note": "pineapple glaze"}
        for i, d in enumerate(nights)
    ]


def test_a_broken_gate_on_one_dish_opens_only_that_dishs_slots(emilys_house, monkeypatch):
    nights = DAYS[1:3]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _dinners_held(nights))
    monkeypatch.setattr(allergen_gate, "quick_pick",
                        lambda ctx: _quick(f"Lemon Chicken {ctx['date']}", mains=("Chicken thighs", "Lemon")))
    real = allergen_gate._pick_clashes

    def flaky(pick, avoidances):
        if pick["meal_name"].endswith(nights[0]):
            raise RuntimeError("database is locked")
        return real(pick, avoidances)

    monkeypatch.setattr(allergen_gate, "_pick_clashes", flaky)
    plan_id = agent.generate_weekly_plan(WEEK_START)["weekly_plan_id"]
    assert _slot(plan_id, nights[0], "dinner")["slot_state"] == "open"
    assert _slot(plan_id, nights[1], "dinner")["meal"] == f"Lemon Chicken {nights[1]}"


def test_two_dishes_that_come_back_as_the_same_replacement_are_told_apart(emilys_house, monkeypatch):
    nights = DAYS[1:3]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _dinners_held(nights))
    lock = threading.Lock()
    asked = []

    def quick(context):
        with lock:
            asked.append(list(context["avoid"]))
        if "Lemon Chicken" in context["avoid"]:
            return _quick("Beef and Broccoli", mains=("Beef", "Broccoli", "Rice"))
        return _quick("Lemon Chicken", mains=("Chicken thighs", "Lemon", "Rice"))

    monkeypatch.setattr(allergen_gate, "quick_pick", quick)
    plan_id = agent.generate_weekly_plan(WEEK_START)["weekly_plan_id"]
    got = sorted(_slot(plan_id, d, "dinner")["meal"] for d in nights)
    assert got == ["Beef and Broccoli", "Lemon Chicken"]
    assert len(asked) == 3, "one extra quick pick for the duplicate, no more"


def test_a_duplicate_that_comes_back_the_same_again_goes_open(emilys_house, monkeypatch):
    nights = DAYS[1:3]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _dinners_held(nights))
    monkeypatch.setattr(allergen_gate, "quick_pick",
                        lambda ctx: _quick("Lemon Chicken", mains=("Chicken thighs", "Lemon", "Rice")))
    plan_id = agent.generate_weekly_plan(WEEK_START)["weekly_plan_id"]
    rows = [_slot(plan_id, d, "dinner") for d in nights]
    assert sorted(r["slot_state"] for r in rows) == ["open", "planned"]
    assert [r["meal"] for r in rows if r["slot_state"] == "planned"] == ["Lemon Chicken"]


def test_why_this_meal_is_answered_from_derived_from(emilys_house, monkeypatch):
    days = _week()
    for d in days:
        if d["date"] == DAYS[6] and d["slot"] == "dinner":
            d["derived_from"] = {"tags": ["rush"], "inputs": ["calendar:Soccer practice"]}
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    agent.generate_weekly_plan(WEEK_START)
    out = tools.explain_meal_choice("Chili")
    wed = [p for p in out["planned_as"] if p["date"] == DAYS[6] and p["slot"] == "dinner"]
    assert wed and wed[0]["derived_from"]["inputs"] == ["calendar:Soccer practice"]
    source = Path(agent.__file__).read_text(encoding="utf-8")
    assert "planned before this was tracked" not in source
    assert "per-slot reasons of 4-9 words" not in source
    assert "its `planned_as` lists the slots" in source
