"""
After a swap, nothing tells the household to thaw something for a dinner
that is no longer on the plan — and a thaw they had already TICKED is held
instead of thrown away (Loop Board bug, Emily's decision 2026-09-21).

The bug, measured through real doors on the merge base: approve a week with
Roast Chicken on a Monday, tap "Something in the freezer?" and confirm the
whole chicken, then swap Monday's dinner for something else.

    Cook tab's prep session : "Move the Whole chicken to the fridge — for
                               Monday's Roast Chicken."
    chat "what do I need to defrost?" : the same sentence
    the receipt's thaw line : "1 thing to move to the fridge this week."

…tickable, for a chicken nobody is cooking. prep_tasks.meal_plan_entry_id
carries no foreign key and only ONE of its six readers drops a dangling row
(cooker.get_prep_schedule). The other five show it, and one of them
(defrost's settled-move reads) can suppress a freezer chip the household
should still be offered.

Emily's answer, which is why this is not simply a delete: *"Is there a way
to delete it but then also have it still note if it had been defrosted
already if they want to switch recipes for later in the week to use up the
meat?"* So the reminder goes and the FACT survives, as a held thing
(app/tools/held.py — the strip the "Pomona, hold this" flow already built),
carrying the day the meat came out and one tap into chat.

Every test says in its own docstring whether it is a CATCH (red against
the merge base's app/) or a GUARD (green either way, pinned by a named
mutation).
"""
import inspect
import json
from datetime import date, timedelta

import pytest

from app import tools
from app.db import get_conn
from app.tools import defrost as df, held as hd, weekly_plan as wp

from conftest import household_today


# ---------- fixtures ----------

@pytest.fixture
def family():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")


@pytest.fixture
def week_with_a_thaw(family):
    """
    An approved week with a fridge move booked on it, built through real
    doors only — no hand-inserted prep row.

    Prep days are every day so the Cook tab really draws a session for the
    move's own date; the week is shopped before the freezer step is asked,
    because meat_items_for_plan stays quiet while a line is still to buy.
    Returns (plan_id, the cook night, that dinner's entry id).
    """
    tools.set_prep_days(["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"])
    tools.add_recipe("Roast Chicken",
                     ingredients=[{"item": "Whole chicken", "qty": "1", "category": "meat"}],
                     prep_time_minutes=15, cook_time_minutes=60)
    tools.add_recipe("Bean Chili",
                     ingredients=[{"item": "Black beans", "qty": "1 can", "category": "pantry"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    start = household_today().isoformat()
    cook_night = (household_today() + timedelta(days=3)).isoformat()
    pid = tools.create_weekly_plan(start)["weekly_plan_id"]
    tools.plan_meal(cook_night, "Roast Chicken", slot="dinner", weekly_plan_id=pid)
    tools.approve_weekly_plan(pid, approved_by="Emily")
    for item in tools.list_grocery_list():
        tools.mark_grocery_item(item["id"], "purchased")
    offered = tools.meat_items_for_plan(pid)
    assert offered, "premise: the freezer step has something to offer"
    tools.confirm_frozen_items(pid, [offered[0]["item"]])
    assert _prep_count() == 1, "premise: confirming wrote exactly one fridge move"
    return pid, cook_night, _entry_id(pid, cook_night)


@pytest.fixture
def week_with_a_shop_thaw(family):
    """
    The same week, with the move booked by the OTHER freezer door — Shop's
    "Freezing it?" under a just-ticked meat line
    (defrost.book_defrost_for_grocery_line). Both doors write the identical
    row, so the fix has to hold for either; this is the one that proves it
    rather than arguing it.
    """
    tools.set_prep_days(["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"])
    tools.add_recipe("Roast Chicken",
                     ingredients=[{"item": "Whole chicken", "qty": "1", "category": "meat"}],
                     prep_time_minutes=15, cook_time_minutes=60)
    tools.add_recipe("Bean Chili",
                     ingredients=[{"item": "Black beans", "qty": "1 can", "category": "pantry"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    start = household_today().isoformat()
    cook_night = (household_today() + timedelta(days=3)).isoformat()
    pid = tools.create_weekly_plan(start)["weekly_plan_id"]
    tools.plan_meal(cook_night, "Roast Chicken", slot="dinner", weekly_plan_id=pid)
    tools.approve_weekly_plan(pid, approved_by="Emily")
    line = next(i for i in tools.list_grocery_list() if "chicken" in i["item"].lower())
    booked = df.book_defrost_for_grocery_line(line["id"], True)
    assert booked["freezing"] and _prep_count() == 1, "premise: Shop's door booked one move"
    return pid, cook_night, _entry_id(pid, cook_night)


# ---------- the five readers that do not filter ----------

def test_the_cook_tabs_prep_session_no_longer_names_the_swapped_out_dinner(week_with_a_thaw):
    """CATCH — reader 1, prep_sessions._prep_task_rows. The live Cook tab."""
    pid, night, _entry = week_with_a_thaw
    assert _session_items(pid), "premise: the Cook tab really is showing the fridge move"

    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    assert _session_items(pid) == [], "the Cook tab's prep session still shows it"


def test_the_chat_defrost_answer_no_longer_names_it(week_with_a_thaw):
    """
    CATCH — reader 2, defrost.get_defrost_schedule, which SYSTEM_PROMPT
    routes "what do I need to defrost?" straight to.
    """
    pid, night, _entry = week_with_a_thaw
    assert _chat_defrost(), "premise: chat really would name it"

    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    assert _chat_defrost() == []


def test_the_receipts_thaw_count_no_longer_counts_it(week_with_a_thaw):
    """CATCH — reader 3, weekly_plan._pending_thaw_count (the approved week's thaw line)."""
    pid, night, _entry = week_with_a_thaw
    assert wp._pending_thaw_count(pid) == 1, "premise: the receipt counts the move"

    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    assert wp._pending_thaw_count(pid) == 0


def test_todays_defrost_tile_no_longer_names_it(week_with_a_thaw):
    """
    CATCH — reader 4, defrost.get_defrost_today. A whole chicken's lead
    time against a cook three nights out puts the move on the household's
    own today, which is the only shape this reader can see at all; the
    premise says so rather than the test quietly passing on an empty list.
    """
    pid, night, _entry = week_with_a_thaw
    assert df.get_defrost_today(), "premise: the move really is due today"

    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    assert df.get_defrost_today() == []


def test_a_stale_row_can_no_longer_suppress_a_freezer_chip(week_with_a_thaw):
    """
    CATCH — reader 5, defrost._settled_nights (and _settled_move_for_entry
    beside it), which read prep_tasks by description and by entry to decide
    what the freezer step has nothing left to say about. A dangling row
    means an answer the household never gave, for a night they can still be
    asked about.
    """
    pid, night, _entry = week_with_a_thaw
    by_item = {e["item"].lower(): e for e in tools.meat_items_for_plan(pid)}
    _settled, frozen = df._settled_nights(pid, by_item)
    assert frozen, "premise: the booked move really does read as frozen"

    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    _settled_after, frozen_after = df._settled_nights(pid, {})
    assert frozen_after == set(), (
        f"a move for a dinner that is gone still reads as answered: {frozen_after}"
    )


def test_the_one_reader_that_always_filtered_still_says_nothing(week_with_a_thaw):
    """
    GUARD — cooker.get_prep_schedule dropped a dangling row on read all
    along, so it was never the bug; this pins that the fix did not take
    its legitimate rows away with the stale one.
    """
    pid, night, _entry = week_with_a_thaw
    assert tools.get_prep_schedule(pid), "premise: it shows a live move"

    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    assert tools.get_prep_schedule(pid) == []


def test_the_row_is_gone_from_disk_not_merely_hidden(week_with_a_thaw):
    """CATCH — the fix is a delete at the source, which is what covers every reader."""
    pid, night, _entry = week_with_a_thaw
    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")
    assert _prep_count() == 0


# ---------- the meat outlives the meal ----------

def test_a_ticked_fridge_move_becomes_a_held_thing(week_with_a_thaw):
    """
    CATCH — Emily's own decision. The reminder named a dinner nobody is
    cooking; the thawed chicken is still in the fridge with a clock on it.
    """
    pid, night, _entry = week_with_a_thaw
    task_id = _prep_ids()[0]
    tools.check_off_prep_step(task_id, status="done")

    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    held = tools.list_held_things()
    assert len(held) == 1, f"expected the thaw to be held: {held}"
    assert "Whole chicken" in held[0]["text"]
    assert "freezer" in held[0]["text"]


def test_the_held_thing_carries_the_day_the_meat_came_out(week_with_a_thaw):
    """
    CATCH, and honest about how: against the merge base it dies on an
    IndexError off an empty held list rather than on the assertion it is
    named for, because nothing is held there at all. That IS the bug; the
    specific claim (the day is in the words) is pinned by mutation —
    render the text without `when` and this fails on this branch.

    The vocabulary is the strip's own (held.when_label: today / yesterday
    / Monday / Sep 12), never a timestamp.
    """
    pid, night, _entry = week_with_a_thaw
    task_date = _prep_rows()[0]["task_date"]
    tools.check_off_prep_step(_prep_ids()[0], status="done")

    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    expected = hd.when_label(task_date, household_today())
    assert expected, "premise: the move has a readable day"
    assert expected in tools.list_held_things()[0]["text"]


def test_the_held_thing_offers_one_tap_to_plan_a_later_dinner(week_with_a_thaw):
    """
    CATCH, same shape as the one above: red against the merge base on an
    IndexError, because there is nothing held to read an ask off. The
    claim itself is pinned by mutation — drop `ask_text=` at the hold.
    """
    pid, night, _entry = week_with_a_thaw
    tools.check_off_prep_step(_prep_ids()[0], status="done")

    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    ask = tools.list_held_things()[0]["ask_text"]
    assert "whole chicken" in ask.lower()
    assert "later this week" in ask.lower()


def test_nobody_is_credited_with_saying_it(week_with_a_thaw):
    """
    GUARD, pinned by mutation: drop `member_id=None` at the hold and the
    session's adult is named on a sentence they never said. Pomona noticed
    this one. (It is red against the merge base too, on an IndexError off
    an empty list — which is not what it is about, so it is labelled a
    guard.)
    """
    pid, night, _entry = week_with_a_thaw
    tools.check_off_prep_step(_prep_ids()[0], status="done")

    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    held = tools.list_held_things()[0]
    assert held["member_id"] is None and held["said_by"] == ""


def test_an_unticked_fridge_move_is_simply_deleted(week_with_a_thaw):
    """
    CATCH on the delete, GUARD on the silence: nothing was thawing, so
    there is nothing to keep and the strip stays empty. Mutation: hold
    every defrost row regardless of status and this fails.
    """
    pid, night, _entry = week_with_a_thaw
    assert _prep_statuses() == ["pending"], "premise: nobody ticked it"

    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    assert _prep_count() == 0
    assert tools.list_held_things() == []


def test_a_ticked_prep_cut_is_not_held(week_with_a_thaw):
    """
    GUARD, and the stated scope: chopped onions keep, so only a THAW is
    held. A hold for every ticked prep row would turn the strip into a log.
    Mutation: drop the `task_type != "defrost"` half of the filter.
    """
    pid, night, entry = week_with_a_thaw
    tools.add_prep_cut(pid, (household_today() + timedelta(days=1)).isoformat(),
                       "Chop the onions", entry_ids=[entry])
    cut = [r for r in _prep_rows() if r["task_type"] == "prep_cut"]
    assert cut, "premise: the prep cut is on the plan"
    tools.check_off_prep_step(cut[0]["id"], status="done")

    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    assert _prep_count() == 0, "it still goes with the meal"
    assert tools.list_held_things() == [], "but there is nothing worth holding about it"


def test_the_shop_door_is_covered_too(week_with_a_shop_thaw):
    """
    CATCH — the SECOND freezer door. Shop's "Freezing it?" tick writes the
    same row through book_defrost_for_grocery_line, so a fix that only held
    for the approval step would leave half the households with the bug.
    """
    pid, night, _entry = week_with_a_shop_thaw
    assert _chat_defrost(), "premise: Shop's move is on the schedule"
    tools.check_off_prep_step(_prep_ids()[0], status="done")

    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    assert _chat_defrost() == [] and _prep_count() == 0
    assert len(tools.list_held_things()) == 1


# ---------- every door of the app's central plan write ----------

def test_resolving_an_open_slot_releases_the_rows_too(week_with_a_thaw):
    """
    GUARD — resolve_open_slot reaches _replace_slot_entries directly rather
    than through swap_meal_in_plan, so it is its own door. An open slot has
    no prep rows of its own; this pins that the door behaves the same way
    the others do rather than being exempt by accident.
    """
    pid, night, _entry = week_with_a_thaw
    assert "_release_prep_rows(" in _code_of(wp._replace_slot_entries)
    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")
    assert _prep_count() == 0


def test_the_swap_still_opens_exactly_one_connection(week_with_a_thaw, monkeypatch):
    """
    GUARD, and the reason held.hold_thing and cooker.household_zone grew a
    `conn`: SQLite gives one writer at a time, so a nested get_conn inside
    this transaction would sit behind its own lock and die of "database is
    locked". Mutation: drop `conn=conn` at either call and this fails.
    """
    import app.db as appdb
    from app.tools import cooker, held, grocery, meal_plans, recipes, leftovers, attendance

    modules = {"weekly_plan": wp, "grocery": grocery, "meal_plans": meal_plans,
               "recipes": recipes, "leftovers": leftovers, "attendance": attendance,
               "cooker": cooker, "held": held, "defrost": df, "db": appdb}
    opened = {name: 0 for name in modules}
    for name, module in modules.items():
        real = module.get_conn

        def counting(_name=name, _real=real):
            opened[_name] += 1
            return _real()

        monkeypatch.setattr(module, "get_conn", counting)

    pid, night, _entry = week_with_a_thaw
    tools.check_off_prep_step(_prep_ids()[0], status="done")

    marks = {}
    real_replace = wp._replace_slot_entries

    def marking(*args, **kwargs):
        marks["start"] = dict(opened)
        out = real_replace(*args, **kwargs)
        marks["end"] = dict(opened)
        return out

    monkeypatch.setattr(wp, "_replace_slot_entries", marking)
    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    assert set(marks) == {"start", "end"}, "the transaction body never ran"
    delta = {k: marks["end"][k] - marks["start"][k] for k in opened}
    assert delta == {**{k: 0 for k in opened}, "weekly_plan": 1}, (
        f"something inside the swap's write transaction opened its own connection: {delta}"
    )


def test_a_failure_in_the_swap_leaves_the_prep_row_and_holds_nothing(week_with_a_thaw, monkeypatch):
    """
    CATCH on the atomicity of the new work: the delete and the hold belong
    to the swap's own transaction, or a rolled-back swap leaves a hold for a
    dinner still on the plan.
    """
    from app.tools import meal_plans

    pid, night, _entry = week_with_a_thaw
    tools.check_off_prep_step(_prep_ids()[0], status="done")

    def boom(*_a, **_kw):
        raise RuntimeError("simulated failure after the prep rows were released")

    monkeypatch.setattr(meal_plans, "plan_meal", boom)
    with pytest.raises(RuntimeError):
        tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    assert _prep_count() == 1, "the fridge move is still there"
    assert _prep_statuses() == ["done"], "still ticked"
    assert tools.list_held_things() == [], "and nothing was held for a dinner still on the plan"


def test_the_swap_reports_what_it_held(week_with_a_thaw):
    """GUARD — so the chat can say the meat did not go with the meal. Mutation: drop the `held_thawed` key."""
    pid, night, _entry = week_with_a_thaw
    tools.check_off_prep_step(_prep_ids()[0], status="done")

    result = tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    assert [h["item"] for h in result.get("held_thawed", [])] == ["Whole chicken"]


def test_an_ordinary_swap_reports_nothing_extra(week_with_a_thaw):
    """GUARD — `held_thawed` is absent when there is nothing to say, the shape `reingested_*` already uses."""
    pid, night, _entry = week_with_a_thaw
    result = tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")
    assert "held_thawed" not in result


def test_the_other_snack_on_the_day_keeps_its_prep_rows(family):
    """
    CATCH — a day holds two snacks by default, and a swap about one of them
    must take only its own rows. The release is keyed by the entry ids the
    swap was given, which is the same care swap_meal_in_plan's `old_meal`
    takes for the entries themselves.
    """
    tools.set_prep_days(["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"])
    for name in ("Trail Mix", "Apple Slices", "Cheese"):
        tools.add_recipe(name, ingredients=[{"item": name, "qty": "1"}],
                         prep_time_minutes=5, cook_time_minutes=0)
    day = (household_today() + timedelta(days=2)).isoformat()
    pid = tools.create_weekly_plan(household_today().isoformat())["weekly_plan_id"]
    tools.plan_meal(day, "Trail Mix", slot="snack", weekly_plan_id=pid)
    tools.plan_meal(day, "Apple Slices", slot="snack", weekly_plan_id=pid)
    tools.approve_weekly_plan(pid, approved_by="Emily")
    mix, apples = (_entry_ids_for(pid, day, "snack"))
    tools.add_prep_cut(pid, day, "Weigh the mix", entry_ids=[mix])
    tools.add_prep_cut(pid, day, "Slice the apples", entry_ids=[apples])
    assert _prep_count() == 2, "premise: one prep row each"

    tools.swap_meal_in_plan(pid, day, "Cheese", slot="snack", old_meal="Trail Mix")

    left = [r["description"] for r in _prep_rows()]
    assert left == ["Slice the apples"], f"the other snack's prep row went too: {left}"


def test_a_chat_swap_tells_the_screens_something_is_held(week_with_a_thaw):
    """
    CATCH — the panels-build-once gotcha, one door down. The Holding strip
    only re-reads when a turn carries a `held` card (shell.js's
    refreshStaleTabsFromActions), so a swap that holds a thawed ingredient
    has to say so or the row sits on disk and on no screen until Today is
    reloaded. A SECOND card beside the week's, never instead of it.
    """
    from app.main import summarize_chat_actions

    pid, night, _entry = week_with_a_thaw
    tools.check_off_prep_step(_prep_ids()[0], status="done")
    result = tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "swap_meal_in_plan", "input": {"meal_date": night}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": json.dumps(result)}]},
    ]
    cards = summarize_chat_actions([], messages)
    held = [c for c in cards if getattr(c, "held", False)]
    assert len(held) == 1, f"no held card: {[getattr(c, 'kicker', None) for c in cards]}"
    assert "Whole chicken" in held[0].change and "thawed" in held[0].change
    assert any(getattr(c, "tab", None) == "week" for c in cards), (
        "the week card must survive — the tab that really changed still has to refresh"
    )


# ---------- household isolation ----------

def test_another_households_prep_rows_and_holds_are_untouched(week_with_a_thaw):
    """
    GUARD — every statement in _release_prep_rows is household-scoped.
    Mutation: drop `household_id = ?` from either the SELECT or the DELETE
    and this fails. (Red against the merge base as well, but on OUR row
    surviving rather than on the neighbour's going — a different claim
    from the one it is named for, so: guard.)
    """
    pid, night, _entry = week_with_a_thaw
    conn = get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (99, 'Next door')")
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
        "related_meal, status, task_type, meal_plan_entry_id) "
        "VALUES (99, ?, ?, 'Move the Whole chicken to the fridge — for their Roast Chicken.', "
        "'Roast Chicken', 'done', 'defrost', ?)",
        (pid, household_today().isoformat(), _entry),
    )
    conn.commit()
    conn.close()

    tools.swap_meal_in_plan(pid, night, "Bean Chili", slot="dinner")

    conn = get_conn()
    left = conn.execute("SELECT household_id FROM prep_tasks").fetchall()
    holds = conn.execute("SELECT household_id FROM held_things").fetchall()
    conn.close()
    assert [r["household_id"] for r in left] == [99], "the neighbour's row went with ours"
    assert 99 not in [r["household_id"] for r in holds], "and nothing was held for them"


# ---------- the comment that made this invisible ----------

def test_clear_plan_slots_comment_says_what_is_true_now():
    """
    GUARD on the sentence this whole bug hid behind. It claimed
    _replace_slot_entries "applies the same reasoning" and that a path
    forgetting it "still shows nothing stale" — both false, corrected
    2026-09-21. This change makes the FIRST half true again, so the comment
    is corrected a second time rather than left saying the swap leaves the
    rows standing.
    """
    src = inspect.getsource(wp.clear_plan_slot)
    assert "_release_prep_rows" in src, "it should name what does the releasing now"
    assert "ordinary swap left them standing" in src, (
        "the history stays — a reader needs to know it used to be true"
    )
    assert "still be false and always will be" in src or "is still false" in src, (
        "the second half of the old claim is still false and the comment must keep saying so"
    )


def test_the_held_row_reads_back_through_the_api_shape():
    """
    GUARD — ask_text rides on the same list the strip and What we know
    already read (GET /api/held), so nothing new had to be fetched.
    """
    row = hd.hold_thing("Nana's coming the 28th")
    assert row["ask_text"] == "", "a person's own words offer nothing extra"
    assert "ask_text" in tools.list_held_things()[0]


def test_the_strip_offers_the_link_only_when_there_is_something_to_ask():
    """
    GUARD on the screen: holdingRowHtml draws the quiet link off ask_text
    alone, so an ordinary held thing is exactly as it was. Mutation: render
    the button unconditionally.
    """
    src = _shell_js()
    fn = _js_function(src, "holdingRowHtml")
    assert "data-held-ask" in fn and "ask ?" in fn, fn
    assert "holding-ask" in fn
    # No second apricot FILL on the row (DESIGN_SYSTEM rule 5) — both are
    # apricot-LABEL links, which is what .holding-done already is.
    css = (_static("shell.css"))
    rule = css[css.index(".holding-ask {"):css.index(".holding-ask:hover")]
    assert "--apricot-label" in rule and "background: none" in rule, rule
    assert "min-height: 44px" in rule, "rule 6 — 44px of tap"


def test_thawed_item_reads_the_ingredient_back_out_of_the_sentence():
    """GUARD — one regex for both directions, so the writer and the reader cannot drift."""
    said = df._describe("Chicken thighs", "Skewers", (household_today() + timedelta(days=2)).isoformat())
    assert df.thawed_item(said) == "Chicken thighs"
    assert df.thawed_item("something nobody here wrote") == ""


# ---------- helpers ----------

def _entry_ids_for(plan_id: int, day: str, slot: str) -> list[int]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ? ORDER BY id",
        (plan_id, day, slot),
    ).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _entry_id(plan_id: int, day: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
        (plan_id, day),
    ).fetchone()
    conn.close()
    return row["id"]


def _session_items(plan_id: int) -> list[str]:
    return [i.get("title") or i.get("text") or ""
            for s in tools.prep_sessions_for_plan(plan_id) for i in s.get("items", [])]


def _chat_defrost() -> list[str]:
    return [t["description"] for t in tools.get_defrost_schedule(30)]


def _prep_rows() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM prep_tasks ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _prep_ids() -> list[int]:
    return [r["id"] for r in _prep_rows()]


def _prep_statuses() -> list[str]:
    return [r["status"] for r in _prep_rows()]


def _prep_count() -> int:
    return len(_prep_rows())


def _code_of(fn) -> str:
    """A function's CODE with its docstring and comments taken off — a source
    marker that reads raw source is satisfied by a comment naming the very
    thing it is checking is gone."""
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    body = tree.body[0].body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return "\n".join(ast.unparse(n) for n in body)


def _static(name: str) -> str:
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent / "static" / name).read_text()


def _shell_js() -> str:
    return _static("shell.js")


def _js_function(src: str, name: str) -> str:
    start = src.index("function " + name + "(")
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(name + " never closes")
