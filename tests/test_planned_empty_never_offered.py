"""
A planned_empty slot is never offered as a decision — every reader, one week.

CLAUDE.md: "A meal slot is never absent — it's one of three states ...
A planned_empty slot must never be offered as a decision — not as 'Swap
it', not as a gap in a chat summary, not through the slot API. Three
separate bugs have already come from code treating it as a missing meal."

That rule is enforced reader by reader — the needs-you band skips the row,
the cooker view filters it, the slot writes refuse it — and each reader's
own test seeds the one shape it cares about. Nothing asked the question
the other way round: given ONE week holding both kinds of planned_empty
slot beside a genuine open one, does every surface that turns slots into
something for a person (or the model) to decide agree? A reader that
forgot the rule would pass its neighbours' tests and nothing would notice.

The week, from Monday (today, at the household's 09:00):

    Mon  breakfast EMPTY · lunch Salad · dinner EMPTY      <- tonight is empty
    Tue  breakfast EMPTY · lunch Salad · dinner OPEN       <- tomorrow is a question
    Wed  breakfast Toast · lunch OPEN  · dinner Soup
    Thu  breakfast Toast · lunch Salad · dinner EMPTY
    Fri–Sun  planned

EMPTY is written three ways (the `reason` parameter), one per writer that
produces it: an away need (slot_needs.set_slot_need, which converts a
planned slot in place), the generator's out night (`tags: ['out']`,
`nobody_home`), and the generator's zero-count pass ("You've asked me not
to plan breakfasts" — `breakfasts_per_week:0`), which is the
household-asked-for-none case and the one with nobody away at all.

The OPEN slots are the positive control on every reader: a test that only
asserted "nothing about the empty slots" would pass on a reader that
offers nothing at all.

Not covered here, on purpose: the skipped-day planned_empty ("Which
days?" tapped a day off), which "Build a plan" IS meant to fill
(swap_in_place._fillable_slots), and the gone-by-at-drafting `already_past`
shape. Both have their own files.
"""
import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import cooker, digest, meal_move, moves, slot_needs, weekly_plan
from conftest import household_today, on_the_weeks_monday

pytestmark = on_the_weeks_monday()

MONDAY = household_today() - datetime.timedelta(days=household_today().weekday())

EMPTY = [(0, "breakfast"), (0, "dinner"), (1, "breakfast"), (3, "dinner")]
OPEN = [(1, "dinner"), (2, "lunch")]
REASONS = ["away", "out_night", "asked_for_none"]
OPEN_REASON = "You said you'd decide this one nearer the time."


def _d(n: int) -> str:
    return (MONDAY + datetime.timedelta(days=n)).isoformat()


EMPTY_KEYS = {(_d(n), s) for n, s in EMPTY}
OPEN_KEYS = {(_d(n), s) for n, s in OPEN}
EMPTY_DINNER_DATES = {d for d, s in EMPTY_KEYS if s == "dinner"}


def _write_empty(plan_id: int, day: str, slot: str, reason: str) -> None:
    if reason == "out_night":
        tools.plan_slot_empty(plan_id, day, slot, "You’re out — I’ve planned nothing and bought nothing.",
                              derived_from={"tags": ["out"], "constraint": "nobody_home"})
    else:
        tools.plan_slot_empty(plan_id, day, slot,
                              f"You’ve asked me not to plan {slot}s — I’ve left this to you.",
                              derived_from={"constraint": f"{slot}s_per_week:0"})


@pytest.fixture(params=REASONS)
def week(request):
    reason = request.param
    # Two adults, so a planned night has people at it — a household with
    # no members reads as nobody home everywhere, which would make every
    # "silent on an empty night" assertion pass for the wrong reason.
    for person in ("Emily", "Vineeth"):
        tools.add_member(person)
    for name, item in (("Soup", "stock"), ("Toast", "bread"), ("Salad", "lettuce")):
        tools.add_recipe(name, ingredients=[{"item": item, "qty": "1"}],
                         instructions=["Make it."], prep_time_minutes=5, cook_time_minutes=10)
    plan_id = tools.create_weekly_plan(_d(0))["weekly_plan_id"]
    dish = {"breakfast": "Toast", "lunch": "Salad", "dinner": "Soup"}
    for n in range(7):
        for slot in ("breakfast", "lunch", "dinner"):
            if (n, slot) in OPEN:
                tools.plan_slot_open(plan_id, _d(n), slot, OPEN_REASON)
            elif (n, slot) in EMPTY and reason != "away":
                _write_empty(plan_id, _d(n), slot, reason)
            else:
                tools.plan_meal(_d(n), dish[slot], slot=slot, weekly_plan_id=plan_id)
    if reason == "away":
        # The real writer: a planned slot converted in place, the way a
        # trip marked after the week was drafted does it.
        for n, slot in EMPTY:
            slot_needs.set_slot_need(_d(n), slot, "away", reason="We're away.")

    conn = get_conn()
    rows = conn.execute(
        "SELECT id, date, slot, slot_state FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan_id,)
    ).fetchall()
    conn.close()
    ids = {(r["date"], r["slot"]): r["id"] for r in rows}
    # The seed itself, asserted: one row per slot, in the state it claims.
    states = {(r["date"], r["slot"]): r["slot_state"] for r in rows}
    assert len(rows) == 21
    assert {k for k, s in states.items() if s == "planned_empty"} == EMPTY_KEYS
    assert {k for k, s in states.items() if s == "open"} == OPEN_KEYS
    return {
        "plan_id": plan_id, "reason": reason,
        "empty_ids": {ids[k] for k in EMPTY_KEYS}, "open_ids": {ids[k] for k in OPEN_KEYS},
        "ids": ids,
    }


def _at(n: int, hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(MONDAY + datetime.timedelta(days=n), datetime.time(hour, minute))


# ---------------------------------------------------------------------------
# Readers: what a person (or the model) is shown
# ---------------------------------------------------------------------------

def test_needs_you_skips_tonights_empty_dinner_and_asks_about_tomorrows_open_one(week):
    """The band takes the SOONEST unsettled dinner. Tonight is empty, so the
    card must be tomorrow's open one — not 'Tonight needs a dinner'."""
    dinners = [(i["type"], i["date"]) for i in tools.get_needs_you_items()
               if i.get("type") in ("dinner_open", "dinner_decision")]
    assert dinners == [("dinner_open", _d(1))]


def test_the_bell_says_only_the_open_dinner(week):
    keys = [n["key"] for n in tools.get_active_notifications()
            if n["key"].startswith(("dinner_gap:", "dinner_open:"))]
    assert keys == [f"dinner_open:{_d(1)}"]


@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_todays_moves_never_carry_an_empty_slot(week, n):
    payload = moves.today_moves(_d(n), now=_at(0, 9))
    on_empty = [m for m in payload["moves"]
                if (m.get("date"), m.get("slot")) in EMPTY_KEYS or m.get("entry_id") in week["empty_ids"]]
    assert on_empty == []
    open_rows = {(m["date"], m["slot"]) for m in payload["moves"] if m["kind"] == "open_slot"}
    assert open_rows == {k for k in OPEN_KEYS if k[0] == _d(n)}
    # Today's meals row lists meals, not the slots nobody is eating.
    assert not [r for r in payload["day_meals"] if (r["date"], r["slot"]) in EMPTY_KEYS]


def test_the_cooker_view_holds_open_slots_and_no_empty_ones(week):
    view = cooker.get_cooker_view(week["plan_id"])
    assert {s["entry_id"] for s in view["open_slots"]} == week["open_ids"]
    assert not {m["entry_id"] for m in view["meals"]} & (week["empty_ids"] | week["open_ids"])
    assert tools.get_plan_progress(week["plan_id"])["meals_total"] == 21 - len(EMPTY) - len(OPEN)


def test_the_week_menu_states_empty_slots_and_asks_only_about_open_ones(week):
    menu = tools.get_week_menu(week["plan_id"])
    by_key = {(day["date"], slot): day[slot] for day in menu["days"] for slot in ("breakfast", "lunch", "dinner")}
    for key in EMPTY_KEYS:
        entry = by_key[key]
        assert entry["state"] == "planned_empty", key
        assert not entry.get("open_question") and not entry.get("options"), key
        # "Build a plan" fills a day left out, never one of these.
        assert entry["can_fill"] is False, key
    for key in OPEN_KEYS:
        assert by_key[key]["state"] == "open", key


def test_the_draft_opener_counts_only_the_open_slots(week):
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'draft' WHERE id = ?", (week["plan_id"],))
    conn.commit()
    conn.close()
    opener = tools.get_week_menu(week["plan_id"]).get("draft_opener") or {}
    text = " ".join(str(v) for v in (opener.values() if isinstance(opener, dict) else opener))
    assert "Two slots I’d like your call on." in text
    assert "Six" not in text and "Four" not in text


def test_the_audit_reports_no_gap(week):
    audit = tools.audit_plan_slots(week["plan_id"])
    assert audit["missing"] == [] and audit["duplicated"] == [] and audit["complete"] is True


def test_the_chat_tools_week_tells_the_model_which_is_which(week):
    """get_weekly_plan is what the model reads; the prompt tells it a
    planned_empty slot is never a decision, so the state must reach it
    intact and the open slot must reach it as open."""
    meals = tools.get_weekly_plan(week["plan_id"])["meals"]
    by_id = {m["entry_id"]: m for m in meals}
    for entry_id in week["empty_ids"]:
        assert by_id[entry_id]["slot_state"] == "planned_empty"
        assert not by_id[entry_id].get("open_reason")
    for entry_id in week["open_ids"]:
        assert by_id[entry_id]["slot_state"] == "open"
        assert by_id[entry_id]["open_reason"] == OPEN_REASON


def test_the_morning_text_says_nothing_about_an_empty_dinner(week):
    today = digest.build_morning_text(_at(0, 7), link=False) or ""
    assert "open" not in today.lower() and "your call" not in today.lower()
    # Control: the morning the open dinner is tonight, the text says so.
    tuesday = digest.build_morning_text(_at(1, 7), link=False) or ""
    assert "Tonight's still open" in tuesday


def test_the_evening_nudge_is_silent_on_an_empty_night(week):
    assert digest.build_evening_nudge(_at(0, 17, 30), link=False) is None
    assert digest.build_evening_nudge(_at(3, 17, 30), link=False) is None
    # Control: a night with a dinner on it gets its nudge.
    assert (digest.build_evening_nudge(_at(2, 17, 30), link=False) or "").startswith("Tonight: Soup")


def test_tonight_does_not_ask_about_an_empty_night(week):
    assert tools.tonight_check(now=_at(0, 16))["ask"] is False


# ---------------------------------------------------------------------------
# Doors: the writes a screen or the model could aim at an empty slot
# ---------------------------------------------------------------------------

def test_the_slot_api_refuses_an_empty_slot_and_settles_an_open_one(week):
    for day, slot in EMPTY_KEYS:
        with pytest.raises(ValueError):
            tools.resolve_open_slot(week["plan_id"], day, slot, "Pasta")
    settled = tools.resolve_open_slot(week["plan_id"], _d(1), "dinner", "Pasta")
    assert settled  # the control: an open slot IS a decision


def test_the_needs_you_dinner_door_refuses_an_empty_night(week):
    """Now's dinner card plans by date through plan_meal, which only
    inserts: on an empty night it wrote a second row beside the empty one
    and, with the card's yes, bought for it. Fixed 2026-10-25."""
    conn = get_conn()
    before = conn.execute("SELECT COUNT(*) FROM meal_plan_entries").fetchone()[0]
    conn.close()
    for day in EMPTY_DINNER_DATES:
        with pytest.raises(weekly_plan.SlotRefused):
            tools.resolve_needs_you_dinner(day, "Soup", add_ingredients_to_grocery_list=True)
    conn = get_conn()
    after = conn.execute("SELECT COUNT(*) FROM meal_plan_entries").fetchone()[0]
    stock = conn.execute("SELECT COUNT(*) FROM grocery_items WHERE item LIKE '%stock%'").fetchone()[0]
    conn.close()
    assert after == before and stock == 0
    assert tools.audit_plan_slots(week["plan_id"])["duplicated"] == []


def test_swap_is_not_offered_on_an_empty_slot(week):
    for entry_id in week["empty_ids"]:
        with pytest.raises(ValueError):
            tools.swap_options(week["plan_id"], entry_id, asker=lambda ctx: [])
        with pytest.raises(ValueError):
            tools.swap_meal_in_place(week["plan_id"], entry_id, picker=lambda ctx: {"meal_name": "Pasta"})


def test_build_a_plan_does_not_fill_an_empty_day(week):
    picker = lambda ctx: {"meal_name": "Pasta"}  # noqa: E731 — never reached
    for n in (0, 1, 3):
        assert tools.fill_empty_day(week["plan_id"], _d(n), picker=picker)["status"] == "refused"


def test_the_move_sheet_does_not_offer_an_empty_night(week):
    soup_friday = week["ids"][(_d(4), "dinner")]
    options = meal_move.move_options(week["plan_id"], soup_friday)
    offered = {d["date"] for d in options["days"] if d.get("ok")}
    assert not offered & EMPTY_DINNER_DATES


def test_trading_nights_does_not_put_a_dinner_on_an_empty_night(week):
    out = tools.swap_dinner_nights(week["plan_id"], _d(3), _d(4))
    assert out["status"] == "refused"


# ---------------------------------------------------------------------------
# Shopping: nothing is bought for an empty slot
# ---------------------------------------------------------------------------

def test_approval_buys_nothing_for_an_empty_slot(week):
    tools.approve_weekly_plan(week["plan_id"], approved_by="Emily")
    conn = get_conn()
    linked = {r["meal_plan_entry_id"] for r in conn.execute(
        "SELECT meal_plan_entry_id FROM meal_plan_grocery_links").fetchall()}
    conn.close()
    assert linked, "the control: the planned meals did buy"
    assert not linked & week["empty_ids"]


# ---------------------------------------------------------------------------
# The same readers, through the routes the screens call
# ---------------------------------------------------------------------------

def test_the_routes_agree(week, signed_in):
    needs = signed_in.get("/api/needs-you").json()
    items = needs["items"] if isinstance(needs, dict) else needs
    assert [(i["type"], i["date"]) for i in items if i.get("type", "").startswith("dinner_")] == [("dinner_open", _d(1))]

    today = signed_in.get("/api/today/moves").json()
    assert not [m for m in today["moves"] if (m.get("date"), m.get("slot")) in EMPTY_KEYS]

    for day, slot in EMPTY_KEYS:
        res = signed_in.post(f"/api/week/{_d(0)}/slot", json={"date": day, "slot": slot, "choice": "Pasta"})
        assert res.status_code == 400, (day, slot, res.text)
    res = signed_in.post(f"/api/week/{_d(0)}/slot", json={"date": _d(2), "slot": "lunch", "choice": "Pasta"})
    assert res.status_code == 200

    res = signed_in.post("/api/needs-you/dinner", json={"date": _d(0), "meal": "Soup", "add_ingredients": True})
    assert res.status_code == 200 and res.json().get("status") == "refused", res.text
