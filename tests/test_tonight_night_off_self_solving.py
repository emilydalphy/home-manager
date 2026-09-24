"""
"Not tonight — we're going out" never refuses (Emily, 2026-09-22).

She tapped it on a Wednesday-ish afternoon: tonight's Seared Garlic Chicken
Thighs was cooked double to feed a later night's leftovers, no night was
free, and the sheet answered "…also feeds Wednesday's dinner — change that
first and I'll take tonight off." Her words: "the job of Pomona is to do all
that planning work. Fix this so that it can find a solution on its own."

She chose option A: tonight goes off, the dish is cooked on the first night
it was feeding at the SAME size (groceries unchanged — nothing reversed or
re-added), that night's leftovers entry is replaced by the cook, and the
portion tonight would have eaten goes in the freezer. The toast says what
changed and carries Undo, which puts everything back exactly.

The shapes, one test group each: (c) cooked double, no free night;
(d) tonight is itself a leftovers night; (e) tonight's dinner already
cooked; plus the sub-line, Undo, and the screen.

Every test is a CATCH — red on main, where the first of these refused and
the rest either refused, dropped the dish, or had no Undo at all — unless
its docstring says GUARD.
"""
from __future__ import annotations

import datetime
import threading
from pathlib import Path

import pytest

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import leftovers as _leftovers
from app.tools import tonight as _tonight

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
TONIGHT_PY = (REPO / "app" / "tools" / "tonight.py").read_text(encoding="utf-8")


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


WEEK = _monday().isoformat()
DAYS = tools._week_dates(WEEK)
MON, TUE, WED, THU, FRI, SAT, SUN = DAYS
TONIGHT = WED
AFTERNOON = datetime.datetime.fromisoformat(f"{TONIGHT}T15:50:00")

CHICKEN = "Seared Garlic Chicken Thighs"


def _members(n: int = 3) -> None:
    for name in ("Emily", "Julia", "Rae", "Sam")[:n]:
        tools.add_member(name)


def _recipe(name: str) -> None:
    tools.add_recipe(
        name,
        ingredients=[
            {"item": f"Main for {name}", "qty": "1 lb", "category": "meat"},
            {"item": f"Greens for {name}", "qty": "1 bag", "category": "produce"},
        ],
        prep_time_minutes=10, cook_time_minutes=25, default_servings=3,
    )


def _week(cook: dict[str, str], leftovers: dict[str, str], approve: bool = True) -> int:
    """A full week — no free night — where `cook` maps a day to its dish
    and `leftovers` maps a day to the cook day whose batch it eats. Every
    other night gets a dinner of its own. Chains are confirmed the way
    generation confirms them (repair_leftover_chains)."""
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    for day in DAYS:
        if day in leftovers:
            source_day = leftovers[day]
            tools.plan_meal(day, cook[source_day], slot="dinner", weekly_plan_id=plan,
                            derived_from={"links_to": f"{source_day}:dinner"})
            continue
        dish = cook.get(day) or f"Filler {day}"
        _recipe(dish)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    tools.repair_leftover_chains(plan)
    if approve:
        tools.approve_weekly_plan(plan)
    return plan


def _dinner(day: str):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.*, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = 'dinner'",
        (tools.household_id(), day),
    ).fetchall()
    conn.close()
    assert len(rows) == 1, f"{day} should hold exactly one dinner row, has {len(rows)}"
    return rows[0]


def _groceries():
    return [(r["item"], r["quantity"], r["status"]) for r in tools.list_grocery_list(status="all")]


def _freezer():
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, quantity, location, source FROM inventory_items WHERE household_id = ? AND location = 'freezer'",
        (tools.household_id(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _dump():
    """Everything a night off may touch, byte for byte: the plan's rows,
    their prep rows and grocery links, the list, and the kitchen."""
    conn = get_conn()
    out = {
        t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()]
        for t in ("meal_plan_entries", "prep_tasks", "meal_plan_grocery_links", "grocery_items", "inventory_items")
    }
    conn.close()
    return out


def _add_defrost(entry_id: int, plan: int, task_date: str, weekday: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
        "task_type, meal_plan_entry_id, quantity) VALUES (?, ?, ?, ?, ?, 'defrost', ?, '2 lb')",
        (tools.household_id(), plan, task_date,
         f"Move the chicken thighs to the fridge — for {weekday}’s {CHICKEN}.", CHICKEN, entry_id),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


# ------------------------------------ (c) cooked double, no free night

def test_the_cook_moves_onto_the_night_it_was_feeding():
    """Emily's own case. The cook lands on Friday (the night it fed),
    keeping its row, so its grocery links ride along by id; Friday's
    leftovers entry is gone rather than sitting beside it; tonight is a
    deliberate night off; the list does not move."""
    _members()
    plan = _week({TONIGHT: CHICKEN}, {FRI: TONIGHT})
    cook_id = _dinner(TONIGHT)["id"]
    before = _groceries()

    out = _tonight.tonight_night_off(now=AFTERNOON)

    assert out["status"] == "night_off"
    assert out["kind"] == "cook_on_fed"
    assert out["moved_to"] == FRI and out["moved_to_weekday"] == "Friday"
    friday = _dinner(FRI)
    assert friday["id"] == cook_id and friday["meal"] == CHICKEN
    assert _dinner(TONIGHT)["slot_state"] == "planned_empty"
    assert _groceries() == before
    chains = _leftovers.plan_leftover_chains(plan)
    assert cook_id not in chains["leftovers"]
    assert chains["freezer"] == {cook_id: 3}


def test_the_batch_keeps_its_size_and_the_extra_goes_in_the_freezer():
    """Same size: the Cook card still says six (three at Friday's table,
    three for the freezer), and says why. The freezer holds tonight's three."""
    _members()
    plan = _week({TONIGHT: CHICKEN}, {FRI: TONIGHT})
    before = {m["date"]: m for m in tools.get_cooker_view(plan)["meals"]}[TONIGHT]
    assert before["servings"] == 6

    _tonight.tonight_night_off(now=AFTERNOON)

    friday = {m["date"]: m for m in tools.get_cooker_view(plan)["meals"]}[FRI]
    assert friday["servings"] == 6
    assert friday.get("is_leftovers") is False
    assert "plus 3 for the freezer" in friday["covers_note"]
    assert [i["qty"] for i in friday["ingredients"] if i["item"].startswith("Main for")] == \
        [i["qty"] for i in before["ingredients"] if i["item"].startswith("Main for")]
    assert _freezer() == [{
        "item": f"{CHICKEN} (cooked)", "quantity": "3 servings",
        "location": "freezer", "source": "night_off",
    }]


def test_later_fed_nights_keep_their_leftovers_from_the_new_cook_night():
    """Fed Thursday AND Saturday: the cook goes to Thursday, and Saturday's
    reheat now names Thursday — _rewrite_chain_ref, the nights swap's own —
    so the chain is still whole and runs forwards. Nine portions still."""
    _members()
    plan = _week({TONIGHT: CHICKEN}, {THU: TONIGHT, SAT: TONIGHT})
    cook_id = _dinner(TONIGHT)["id"]

    out = _tonight.tonight_night_off(now=AFTERNOON)

    assert out["moved_to"] == THU
    chains = _leftovers.plan_leftover_chains(plan)
    source = chains["sources"][cook_id]
    assert source["date"] == THU
    assert [t["date"] for t in source["targets"]] == [SAT]
    assert chains["leftovers"][_dinner(SAT)["id"]]["source"]["date"] == THU
    assert _leftovers.batch_for_source(source)["servings"] == 9
    import json
    assert json.loads(_dinner(SAT)["derived_from_json"])["links_to"] == f"{THU}:dinner"


def test_the_fridge_move_moves_with_the_cook():
    """A defrost row keyed to the cook moves by the same number of days,
    status kept, and says the new night — the rule a nights swap follows."""
    _members()
    plan = _week({TONIGHT: CHICKEN}, {FRI: TONIGHT})
    cook_id = _dinner(TONIGHT)["id"]
    task = _add_defrost(cook_id, plan, TUE, "Wednesday")

    _tonight.tonight_night_off(now=AFTERNOON)

    conn = get_conn()
    row = conn.execute("SELECT task_date, description FROM prep_tasks WHERE id = ?", (task,)).fetchone()
    conn.close()
    assert row["task_date"] == THU
    assert "for Friday’s" in row["description"]


def test_the_toast_names_what_changed():
    """Emily's wording: "Tonight's off. Chicken moved to Wednesday." — with
    the dish named the way the plan names it, which is how the row and the
    toast already name it (there is no short-name helper server-side)."""
    _members()
    _week({TONIGHT: CHICKEN}, {FRI: TONIGHT})
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["said"] == f"Tonight’s off. {CHICKEN} moved to Friday."
    assert out["can_undo"] is True


def test_two_taps_at_once_move_the_cook_once():
    """The lock-first rule still holds on the new path: one tap does the
    work, the other finds tonight already off. One cook on Friday, one
    freezer row."""
    _members()
    _week({TONIGHT: CHICKEN}, {FRI: TONIGHT})
    barrier = threading.Barrier(2)
    results = []

    def tap():
        barrier.wait()
        results.append(_tonight.tonight_night_off(now=AFTERNOON))

    threads = [threading.Thread(target=tap) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.get("already") for r in results) == [False, True]
    assert _dinner(FRI)["meal"] == CHICKEN
    assert len(_freezer()) == 1


def test_a_chain_that_feeds_a_lunch_first_cooks_at_that_lunch():
    """The first meal it feeds is Thursday's LUNCH (then Friday's dinner):
    the cook lands on the lunch, the row says so in
    fed_nights_in_eating_order's own words, and Friday is still a reheat of it."""
    _members()
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    for day in DAYS:
        if day == FRI:
            continue
        dish = CHICKEN if day == TONIGHT else f"Filler {day}"
        _recipe(dish)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(THU, CHICKEN, slot="lunch", weekly_plan_id=plan,
                    derived_from={"links_to": f"{TONIGHT}:dinner"})
    tools.plan_meal(FRI, CHICKEN, slot="dinner", weekly_plan_id=plan,
                    derived_from={"links_to": f"{TONIGHT}:dinner"})
    tools.repair_leftover_chains(plan)
    cook_id = _dinner(TONIGHT)["id"]
    preview = tools.tonight_check(now=AFTERNOON)
    assert preview["night_off_line"] == f"{CHICKEN} moves to Thursday’s lunch. The extra goes in the freezer."

    out = _tonight.tonight_night_off(now=AFTERNOON)

    assert out["said"] == f"Tonight’s off. {CHICKEN} moved to Thursday’s lunch."
    chains = _leftovers.plan_leftover_chains(plan)
    assert (chains["sources"][cook_id]["date"], chains["sources"][cook_id]["slot"]) == (THU, "lunch")
    assert [(t["date"], t["slot"]) for t in chains["sources"][cook_id]["targets"]] == [(FRI, "dinner")]


# ------------------------------------ (d) tonight is a leftovers night

def test_a_leftovers_night_goes_in_the_freezer_and_the_cook_keeps_its_size():
    """Tonight reheats Monday's batch. Its portion goes in the freezer —
    not onto a later night, where days-old leftovers would be a food-safety
    guess — and Monday's batch is still six: three eaten, three frozen.
    Nothing on the list is rescaled."""
    _members()
    plan = _week({MON: CHICKEN}, {TONIGHT: MON})
    monday_id = _dinner(MON)["id"]
    before = _groceries()
    preview = tools.tonight_check(now=AFTERNOON)
    assert preview["night_off_line"] == f"The {CHICKEN} leftovers go in the freezer."

    out = _tonight.tonight_night_off(now=AFTERNOON)

    assert out["kind"] == "freeze_reheat"
    assert out["said"] == f"Tonight’s off. The {CHICKEN} leftovers go in the freezer."
    assert _dinner(TONIGHT)["slot_state"] == "planned_empty"
    assert _groceries() == before
    chains = _leftovers.plan_leftover_chains(plan)
    assert monday_id not in chains["sources"]
    assert _leftovers.batch_for_entry(monday_id, chains)["servings"] == 6
    assert _freezer()[0]["item"] == f"{CHICKEN} (cooked)"


def test_a_leftovers_night_goes_in_the_freezer_even_with_a_free_night():
    """The freezer is chosen over a free night for a reheat, deliberately
    (tonight._night_off_plan says why)."""
    _members()
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    for day in DAYS:
        if day == TONIGHT:
            tools.plan_meal(day, CHICKEN, slot="dinner", weekly_plan_id=plan,
                            derived_from={"links_to": f"{MON}:dinner"})
        elif day == SAT:
            tools.plan_slot_open(plan, day, "dinner", "Yours to fill.")
        else:
            dish = CHICKEN if day == MON else f"Filler {day}"
            _recipe(dish)
            tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    tools.repair_leftover_chains(plan)
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["kind"] == "freeze_reheat"
    assert _dinner(SAT)["slot_state"] == "open"


# ------------------------------------ (e) already cooked tonight

def test_a_dinner_already_cooked_goes_in_the_freezer_and_now_says_night_off():
    """The row stays — the tick is a record, and a chain may still be eating
    from it — tonight's share goes in the freezer, and Now's card states the
    night off rather than asking again. A second tap writes nothing."""
    _members()
    _week({TONIGHT: CHICKEN}, {FRI: TONIGHT})
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE household_id = ? AND date = ? "
                 "AND slot = 'dinner'", (tools.household_id(), TONIGHT))
    conn.commit()
    conn.close()
    before = _groceries()

    out = _tonight.tonight_night_off(now=AFTERNOON)

    assert out["kind"] == "freeze_cooked"
    assert out["said"] == f"Tonight’s off. The {CHICKEN} goes in the freezer."
    assert _dinner(TONIGHT)["cooked_status"] == "done"
    assert _dinner(FRI)["meal"] == CHICKEN  # Friday still eats the leftovers
    assert _groceries() == before
    assert _freezer()[0]["quantity"] == "3 servings"
    card = tools.tonight_check(now=AFTERNOON)
    assert card["reason"] == "night_off" and card["night_off"] is True
    again = _tonight.tonight_night_off(now=AFTERNOON)
    assert again["already"] is True and again["already_reason"] == "night_off"
    assert len(_freezer()) == 1


# ------------------------------------------------------------ no refusals

@pytest.mark.parametrize("cook,leftovers,cooked,kind", [
    ({TONIGHT: CHICKEN}, {FRI: TONIGHT}, False, "cook_on_fed"),
    ({TONIGHT: CHICKEN}, {THU: TONIGHT, SUN: TONIGHT}, False, "cook_on_fed"),
    ({TONIGHT: CHICKEN}, {}, False, "drop"),
    ({MON: CHICKEN}, {TONIGHT: MON}, False, "freeze_reheat"),
    ({TONIGHT: CHICKEN}, {FRI: TONIGHT}, True, "freeze_cooked"),
    ({TONIGHT: CHICKEN}, {}, True, "freeze_cooked"),
])
def test_no_shape_of_a_planned_dinner_is_refused(cook, leftovers, cooked, kind):
    """The standing rule, over every shape: whatever tonight is, the tap
    settles it, and the row said beforehand what it would do."""
    _members()
    _week(cook, leftovers)
    if cooked:
        conn = get_conn()
        conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE household_id = ? AND date = ? "
                     "AND slot = 'dinner'", (tools.household_id(), TONIGHT))
        conn.commit()
        conn.close()
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["status"] == "night_off", out
    assert out["kind"] == kind
    assert "first" not in out["said"]


def test_the_change_that_first_sentence_is_gone_from_the_night_off():
    """No "go change X first" anywhere in this flow — the source, the
    screen, or the chat tool's instructions. (The Review stepper's own
    refusal in weekly_plan.drop_dish_from_day is a different screen and is
    left alone.)"""
    from app import agent
    # The sentence as the code used to build it (curly apostrophe); the
    # module's own comment still quotes what Emily saw, in plain quotes.
    assert "first and I’ll take tonight off" not in TONIGHT_PY
    assert "_chain_refusal" not in TONIGHT_PY
    tool = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "take_the_night_off")
    assert "has to change first" not in tool["description"]


# ------------------------------------------------------------------- undo

def _undo_round_trip(build, prep_on_cook: bool = False):
    _members()
    plan = build()
    if prep_on_cook:
        _add_defrost(_dinner(TONIGHT)["id"], plan, TUE, "Wednesday")
    before = _dump()
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["can_undo"] is True
    assert _dump() != before
    back = _tonight.tonight_night_off_undo(day=TONIGHT)
    assert back["status"] == "restored"
    after = _dump()
    # inventory rows carry their own clock columns; the freezer row this
    # wrote must simply be gone, and every other table byte-identical.
    for table in before:
        assert after[table] == before[table], table
    return out, back


def test_undo_puts_a_cook_on_fed_back_exactly():
    """The dish on its night, the leftovers night it replaced (same id),
    the chain as it read, the fridge move on its old date, the freezer row
    gone, the list untouched."""
    out, back = _undo_round_trip(lambda: _week({TONIGHT: CHICKEN}, {FRI: TONIGHT}), prep_on_cook=True)
    assert back["said"] == f"{CHICKEN} is back on tonight."


def test_undo_puts_a_two_night_chain_back_exactly():
    _undo_round_trip(lambda: _week({TONIGHT: CHICKEN}, {THU: TONIGHT, SAT: TONIGHT}))


def test_undo_puts_a_frozen_leftovers_night_back_exactly():
    _undo_round_trip(lambda: _week({MON: CHICKEN}, {TONIGHT: MON}))


def test_undo_puts_a_cooked_night_back_exactly():
    def build():
        plan = _week({TONIGHT: CHICKEN}, {})
        conn = get_conn()
        conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE household_id = ? AND date = ? "
                     "AND slot = 'dinner'", (tools.household_id(), TONIGHT))
        conn.commit()
        conn.close()
        return plan
    _undo_round_trip(build)


def test_undo_puts_a_move_to_a_free_night_back_exactly():
    """The move that existed before 2026-09-22 gets Undo too: the open
    night comes back with its own id, the dish goes home."""
    def build():
        plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
        for day in DAYS:
            if day == FRI:
                tools.plan_slot_open(plan, day, "dinner", "Yours to fill.")
                continue
            dish = CHICKEN if day == TONIGHT else f"Filler {day}"
            _recipe(dish)
            tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
        tools.approve_weekly_plan(plan)
        return plan
    out, _ = _undo_round_trip(build, prep_on_cook=True)
    assert out["kind"] == "move"


def test_a_dropped_dish_offers_no_undo():
    """CATCH (red on main, which has no `kind`, `can_undo` or undo route):
    the drop reverses groceries, and half an undo is worse
    than none — so the toast carries no Undo, and the route says nothing
    to put back."""
    _members()
    _week({TONIGHT: CHICKEN}, {})
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["kind"] == "drop" and out["can_undo"] is False
    back = _tonight.tonight_night_off_undo(day=TONIGHT)
    assert back["status"] == "refused"
    assert back["message"] == "There’s nothing to put back."


def test_undo_leaves_the_week_alone_once_it_has_changed_since():
    """Friday gets cooked after the tap; Undo must not quietly un-cook it
    by restoring its old row. It says so, and writes nothing."""
    _members()
    _week({TONIGHT: CHICKEN}, {FRI: TONIGHT})
    _tonight.tonight_night_off(now=AFTERNOON)
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE household_id = ? AND date = ? "
                 "AND slot = 'dinner'", (tools.household_id(), FRI))
    conn.commit()
    conn.close()
    before = _dump()
    back = _tonight.tonight_night_off_undo(day=TONIGHT)
    assert back["status"] == "refused"
    assert back["message"] == "Tonight’s changed since, so I’ve left it as it is."
    assert _dump() == before


def test_undo_leaves_the_week_alone_once_the_freezer_row_is_touched():
    """Somebody used some of the frozen chicken already — the rev bump says
    so — and Undo won't delete food the kitchen has since accounted for."""
    _members()
    _week({TONIGHT: CHICKEN}, {FRI: TONIGHT})
    _tonight.tonight_night_off(now=AFTERNOON)
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET quantity = '1 serving' WHERE source = 'night_off'")
    conn.commit()
    conn.close()
    assert _tonight.tonight_night_off_undo(day=TONIGHT)["status"] == "refused"
    assert _dinner(TONIGHT)["slot_state"] == "planned_empty"


def test_the_undo_route(signed_in):
    _members()
    _week({TONIGHT: CHICKEN}, {FRI: TONIGHT})
    tapped = signed_in.post("/api/today/tonight/night-off", json={"date": TONIGHT}).json()
    assert tapped["can_undo"] is True
    res = signed_in.post("/api/today/tonight/night-off-undo", json={"date": TONIGHT})
    assert res.status_code == 200 and res.json()["status"] == "restored"
    assert _dinner(TONIGHT)["meal"] == CHICKEN
    assert signed_in.post("/api/today/tonight/night-off-undo", json={"date": "nope"}).status_code == 400


# ------------------------------------------------------------ the screen

def test_the_row_leads_when_there_is_nothing_to_swap_with():
    """No "Nothing else on this week's plan…" line and no "Open today in the
    plan" button: the night-off row is the sheet, and its sub-line is the
    server's."""
    start = SHELL_JS.index("function tonightOptionRowsHtml")
    block = SHELL_JS[start:SHELL_JS.index("function openTonightSheet", start)]
    empty = block[:block.index("return '<div class=\"tonight-options\">'")]
    assert "tonightNightOffRowHtml(data, true)" in empty
    assert "tonight-none" not in empty and "tonight-open-plan" not in empty
    assert "tonight-open-plan" not in SHELL_JS


def test_one_message_after_the_tap_and_it_carries_undo():
    """Run under node: a night off with can_undo shows exactly one toast,
    the server's words, with an Undo action; the sheet closes first."""
    import json

    import nodeharness

    start = SHELL_JS.index("async function runTonightNightOff(")
    end = SHELL_JS.index("async function undoTonightSwap(", start)
    script = """
var toasts = [], closed = 0, after = 0;
function showToast(m, a) { toasts.push({m: m, undo: !!(a && a.label === 'Undo')}); }
function closeTonightSheet() { closed++; }
function afterTonightSwap() { after++; }
var tonightSheet = null, TONIGHT_SWAP_TROUBLE = 'x';
var reply;
function fetch() { return Promise.resolve({ok: true, json: function () { return Promise.resolve(reply); }}); }
""" + SHELL_JS[start:end] + """
(async function () {
  var panel = {_tonight: {date: '2026-09-23'}};
  reply = {status: 'night_off', said: 'Tonight’s off. Seared Garlic Chicken Thighs moved to Friday.', can_undo: true};
  await runTonightNightOff(panel);
  reply = {status: 'night_off', said: 'Tonight’s off. Bean Chili is off the week.', can_undo: false};
  await runTonightNightOff(panel);
  console.log(JSON.stringify({toasts: toasts, closed: closed}));
})();
"""
    out = json.loads(nodeharness.run_node(script, timeout=30).stdout)
    assert out["toasts"] == [
        {"m": "Tonight’s off. Seared Garlic Chicken Thighs moved to Friday.", "undo": True},
        {"m": "Tonight’s off. Bean Chili is off the week.", "undo": False},
    ]
    assert out["closed"] == 2
