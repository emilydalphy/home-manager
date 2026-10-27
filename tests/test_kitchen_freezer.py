"""
The Kitchen tab's freezer (Loop Board C4, 2026-10-27): "9 things in the
freezer" / "Oldest first. Next week's plan starts from the top." — frozen
doesn't mean forgotten.

  - rows sort oldest first by ONE key (use_up.freezer_order_key): the day it
    went in (frozen_on), else the day it arrived, then the arrival stamp —
    the planner's freezer candidates sort by the same key, so the top of
    this list is the planner's next pick;
  - a night off's cooked portion reads "Chili · 3 portions" / "from last
    Wed" (the cook's night), not "Chili (cooked)" / "plenty";
  - each row says its plan ("in next Mon's chili"), else when it went in
    ("frozen 28 Sep"), else its amount word;
  - the planning check's freezer chips ("Quick check: still have these?")
    show here too, and an answer in either place answers both.

NAMING: the Kitchen tab is `home` in code (`kitchen` is Cook).
Every test says whether it is a CATCH (red on the merge base) or a GUARD.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import tonight, use_up, whats_home

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text()
SHELL_CSS = (REPO / "static" / "shell.css").read_text()

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run shell.js's own functions")


def _fn(name: str) -> str:
    start = SHELL_JS.index(f"function {name}(")
    nxt = re.search(r"\n  (?:async )?function \w+\(|\n  var \w+ = ", SHELL_JS[start + 10:])
    return SHELL_JS[start: start + 10 + (nxt.start() if nxt else len(SHELL_JS))]


def _row(item, *, location="freezer", quantity="1", source="chat", category="other",
         created_days_ago=0, frozen_on=None, exp=None):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, source, expiration_date, category, location, "
        "frozen_on, created_at, updated_at) VALUES (1, ?, ?, ?, ?, ?, ?, ?, datetime('now', ?), datetime('now', ?))",
        (item, quantity, source, exp, category, location, frozen_on,
         f"-{created_days_ago} days", f"-{created_days_ago} days"),
    )
    conn.commit()
    rid = cur.lastrowid
    created = conn.execute("SELECT created_at FROM inventory_items WHERE id = ?", (rid,)).fetchone()[0]
    conn.close()
    return rid, created


def _day(n: int) -> str:
    return (household_today() + datetime.timedelta(days=n)).isoformat()


def _freezer_ids() -> list[int]:
    return [r["id"] for r in whats_home.get_whats_home()["locations"]["freezer"]]


def _freezer_row(item_id: int) -> dict:
    for r in whats_home.get_whats_home()["locations"]["freezer"]:
        if r["id"] == item_id:
            return r
    raise AssertionError(f"row {item_id} not shown in the freezer")


def _portion(dish: str, servings: int, cooked_days_ago: int) -> int:
    conn = get_conn()
    made = tonight._freeze_portion(conn, dish, servings, _day(-cooked_days_ago))
    conn.commit()
    conn.close()
    return made["id"]


def _approved_meal(day_offset: int, *, recipe=None, ingredients=(), freeform=None, derived=None,
                   status="approved", cooked=False) -> int:
    today = household_today()
    start = (today + datetime.timedelta(days=day_offset)).isoformat()
    conn = get_conn()
    plan = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, content_start_date, day_count, status) "
        "VALUES (1, ?, ?, 7, ?)",
        (start, start, status),
    ).lastrowid
    rid = None
    if recipe:
        rid = conn.execute(
            "INSERT INTO recipes (household_id, name, ingredients_json) VALUES (1, ?, ?)",
            (recipe, json.dumps([{"item": i, "qty": "1"} for i in ingredients])),
        ).lastrowid
    entry = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, recipe_id, freeform_meal, weekly_plan_id, "
        "derived_from_json, cooked_status) VALUES (1, ?, 'dinner', ?, ?, ?, ?, ?)",
        ((today + datetime.timedelta(days=day_offset)).isoformat(), rid, freeform, plan,
         json.dumps(derived or {}), "done" if cooked else "pending"),
    ).lastrowid
    conn.commit()
    conn.close()
    return entry


def _dow(d: datetime.date) -> str:
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d.weekday()]


# ---------------------------------------------------------------------------
# Oldest first, by the planner's own key
# ---------------------------------------------------------------------------

def _seed_ages():
    """A frozen 30 days ago; B bought frozen 20 days ago (no frozen_on); C
    frozen the same day B arrived but itself arrived 40 days ago (a row
    moved into the freezer later); D frozen today. Names are chosen so an
    alphabetical tie-break puts B before C."""
    a, _ = _row("Pork chops", frozen_on=_day(-30), created_days_ago=35)
    b, b_created = _row("Apple pie", created_days_ago=20)
    c, _ = _row("Zucchini", frozen_on=b_created[:10], created_days_ago=40)
    d, _ = _row("Half a cabbage", frozen_on=_day(0), created_days_ago=3)
    return a, b, c, d


def test_freezer_sorts_by_frozen_on_then_arrival():
    """CATCH — the merge base broke a same-day tie by NAME, so a thing that
    arrived later sat above one that arrived first."""
    a, b, c, d = _seed_ages()
    assert _freezer_ids() == [a, c, b, d]


def test_next_weeks_plan_starts_from_the_top():
    """CATCH — use_up broke the tie by id and the screen by name: the
    planner's freezer order and the screen's could disagree. Now one key."""
    a, b, c, d = _seed_ages()
    planner = [x["id"] for x in tools.use_up_candidates(_day(1), 7) if x["kind"] == "freezer"]
    # The planner takes things frozen >= 14 days, at most MAX_FREEZER.
    assert planner == [a, c, b][:use_up.MAX_FREEZER]
    screen = [i for i in _freezer_ids() if i in planner]
    assert screen == planner
    assert "use_up.freezer_order_key" in Path(whats_home.__file__).read_text()


# ---------------------------------------------------------------------------
# A night off's portion
# ---------------------------------------------------------------------------

def test_a_night_off_portion_reads_dish_and_portions_from_the_cook_night():
    """CATCH — the tab showed "Chili (cooked)" / "plenty"."""
    pid = _portion("Chili", 3, cooked_days_ago=2)
    one = _portion("Lentil soup", 1, cooked_days_ago=12)
    row = _freezer_row(pid)
    assert row["label"] == "Chili · 3 portions"
    assert row["item"] == "Chili"  # the sheet's title and every toast
    cooked = household_today() - datetime.timedelta(days=2)
    assert row["line"] == f"from last {_dow(cooked)}"
    assert row["detail"] == f"Freezer · cooked {cooked.strftime('%A')}"  # the sheet agrees
    assert _freezer_row(one)["label"] == "Lentil soup · 1 portion"
    old = household_today() - datetime.timedelta(days=12)
    assert _freezer_row(one)["line"] == f"from {old.day} {old.strftime('%b')}"


def _cook_with_extra(day_offset: int, portion_id: int, done: bool = False) -> int:
    """A cook entry whose batch includes a frozen extra (cook_on_fed's shape:
    leftovers.FREEZER_EXTRA_KEY on the cook)."""
    return _approved_meal(day_offset, recipe="Kofte", ingredients=["ground lamb"],
                          derived={"freezer_extra": {"inventory_item_ids": [portion_id]}},
                          cooked=done)


def test_a_portion_of_a_cook_not_made_yet_is_not_in_the_freezer():
    """CATCH — cook_on_fed writes the portion ahead of the cook (a later
    night, or tonight: verify item 6, "from today"); until the cook is
    ticked or its night has gone by, it is not in anybody's freezer."""
    later = _portion("Kofte", 2, cooked_days_ago=-2)
    _cook_with_extra(2, later)
    tonight_ = _portion("Lamb stew", 2, cooked_days_ago=0)
    _cook_with_extra(0, tonight_)
    early = _portion("Dal", 2, cooked_days_ago=-1)
    _cook_with_extra(1, early, done=True)  # ticked cooked ahead of its night
    made = _portion("Chili", 2, cooked_days_ago=1)
    ids = _freezer_ids()
    assert made in ids and early in ids
    assert later not in ids and tonight_ not in ids
    assert whats_home.get_whats_home()["counts"]["freezer"] == 2


def test_the_night_off_source_constant_matches_tonight():
    """GUARD — whats_home keeps its own copy to avoid an import cycle."""
    assert whats_home._NIGHT_OFF_SOURCE == tonight.NIGHT_OFF_FROZEN_SOURCE


# ---------------------------------------------------------------------------
# Each row's words
# ---------------------------------------------------------------------------

def test_frozen_rows_say_when_they_went_in_and_bought_frozen_says_amount():
    """CATCH — every freezer row read "plenty"."""
    fridge, _ = _row("Half a cabbage", location="fridge", exp=_day(3))
    tools.freeze_item(fridge)
    long_ago, _ = _row("Chicken thighs", frozen_on=_day(-29), created_days_ago=40)
    peas, _ = _row("Frozen peas", created_days_ago=5)
    assert _freezer_row(fridge)["line"] == "frozen today"
    d = household_today() - datetime.timedelta(days=29)
    assert _freezer_row(long_ago)["line"] == f"frozen {d.day} {d.strftime('%b')}"
    assert _freezer_row(peas)["line"] == "plenty"


def test_freezer_plan_line_shapes():
    """CATCH — the mockup's "in next Mon's chili"; a long dish name reads as
    the slot; a portion IS the dinner ("for")."""
    thu = datetime.date(2026, 10, 29)
    mon = {"date": "2026-11-02", "slot": "dinner", "meal": "Chili"}
    assert whats_home._freezer_plan_line(mon, thu, portion=False) == "in next Mon’s chili"
    sat = {"date": "2026-10-31", "slot": "dinner", "meal": "Braised Chicken Thighs with Peppers"}
    assert whats_home._freezer_plan_line(sat, thu, portion=False) == "in Sat’s dinner"
    tonight_ = {"date": "2026-10-29", "slot": "dinner", "meal": "Chili"}
    assert whats_home._freezer_plan_line(tonight_, thu, portion=True) == "for tonight’s dinner"
    assert whats_home._freezer_plan_line(mon, thu, portion=True) == "for next Mon’s dinner"


def test_a_planned_freezer_row_and_a_planned_portion_say_their_plan():
    """CATCH — a planned freezer row said "in Tue dinner" like the fridge,
    and a portion's plan (on the entry, not an ingredient) was never read."""
    beef, _ = _row("Ground beef", frozen_on=_day(-20), created_days_ago=20, category="meat/seafood")
    _approved_meal(1, recipe="Chili", ingredients=["ground beef", "kidney beans"])
    pid = _portion("Lasagna", 4, cooked_days_ago=9)
    _approved_meal(2, freeform="Lasagna from the freezer",
                   derived={"freezer_portion": {"inventory_item_id": pid, "dish": "Lasagna"}})
    d1 = household_today() + datetime.timedelta(days=1)
    d2 = household_today() + datetime.timedelta(days=2)
    monday = household_today() - datetime.timedelta(days=household_today().weekday())

    def day(d):
        return ("next " if d >= monday + datetime.timedelta(days=7) else "") + _dow(d) + "’s"

    assert _freezer_row(beef)["line"] == f"in {day(d1)} chili"
    assert _freezer_row(pid)["line"] == f"for {day(d2)} dinner"
    assert _freezer_row(pid)["plan"]["entry_id"]


def test_freezer_food_another_weeks_draft_uses_reads_as_planned_there():
    """CATCH — use_up.claimed_elsewhere keeps a row next week's draft has on
    a dish out of this week's offer; the screen must say where it went
    ("in next Mon's chili"), not show it as idle food at the top. Its place
    in the age order is unchanged."""
    thighs, _ = _row("Chicken thighs", frozen_on=_day(-40), created_days_ago=40, category="meat/seafood")
    beef, _ = _row("Ground beef", frozen_on=_day(-30), created_days_ago=30, category="meat/seafood")
    today = household_today()
    next_monday = today + datetime.timedelta(days=7 - today.weekday())
    offset = (next_monday - today).days
    _approved_meal(offset, recipe="Chili", ingredients=["ground beef"], status="draft",
                   derived={"use_up": [{"id": thighs, "item": "Chicken thighs", "dish": "Chili"}]})
    row = _freezer_row(thighs)
    assert row["line"] == "in next Mon’s chili"
    assert _freezer_ids()[:2] == [thighs, beef]
    # This week's offer (today up to next Monday) starts from the first row
    # nobody has claimed.
    this_week = [c["id"] for c in tools.use_up_candidates(_day(0), offset) if c["kind"] == "freezer"]
    assert thighs not in this_week and this_week[0] == beef
    # Two weeks out reads as a date.
    later = datetime.date(2026, 11, 12)
    line = whats_home._freezer_plan_line({"date": later.isoformat(), "slot": "dinner", "meal": "Chili"},
                                         datetime.date(2026, 10, 29), portion=False)
    assert line == "in the chili on 12 Nov"


# ---------------------------------------------------------------------------
# The planning check, here too
# ---------------------------------------------------------------------------

def test_check_chips_here_answer_both_places(signed_in):
    """CATCH — a chip tapped gone here had no way back (no Undo token), and
    the freezer and the planning check are the one list."""
    old_hummus, _ = _row("Hummus", location="fridge", created_days_ago=30)
    peas, _ = _row("Peas", created_days_ago=60)
    bread, _ = _row("Banana bread", created_days_ago=70)

    # The Kitchen's freezer asks the very chips planning asks: every shelf
    # (verify item 4 — the mockup's Hummus / Apples / Feta are fridge food).
    here = {i["id"] for i in signed_in.get("/api/inventory/check").json()["items"]}
    assert {peas, bread, old_hummus} <= here

    # Gone here → gone from the planning check and the freezer rows.
    r = signed_in.post("/api/inventory/check", json={"gone": [peas], "confirmed": []}).json()
    assert r["removed"][0]["id"] == peas and r["removed"][0]["undo"]["kind"] == "removed"
    assert peas not in {i["id"] for i in signed_in.get("/api/inventory/check").json()["items"]}
    assert peas not in _freezer_ids()
    # Answering it again (the other place) is skipped, not an error.
    again = signed_in.post("/api/inventory/check", json={"gone": [peas], "confirmed": []})
    assert again.status_code == 200 and again.json()["removed"] == []
    # Tapped again here: it comes back, same id.
    back = signed_in.post("/api/whats-home/undo", json={"undo": r["removed"][0]["undo"]}).json()
    assert back["undone"] is True and peas in _freezer_ids()

    # Confirmed at planning → not asked here.
    signed_in.post("/api/inventory/check", json={"gone": [], "confirmed": [bread]})
    assert bread not in {i["id"] for i in signed_in.get("/api/inventory/check").json()["items"]}


@_needs_node
def test_check_card_markup_marks_a_gone_chip():
    """CATCH — no check card on the Kitchen tab."""
    script = (
        "function escapeHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');}\n"
        + _fn("homeCheckHtml") + "\n"
        "console.log(JSON.stringify({empty: homeCheckHtml([], {}), html: homeCheckHtml("
        "[{id:1,label:'Hummus'},{id:2,label:'Apples'}], {2: null})}));\n"
    )
    out = json.loads(nodeharness.run_node(script).stdout.strip().splitlines()[-1])
    assert out["empty"] == ""
    html = out["html"]
    assert "Quick check: still have these?" in html
    assert "Tap anything that’s gone." in html
    assert "planning check" not in html  # verify item 9: no feature names
    assert '>Hummus</button>' in html
    assert 'class="gro-pill gro-pill-on" data-home="check" data-id="2" aria-pressed="true">Apples · gone<' in html


def test_the_freezer_segment_has_its_own_band_and_no_use_soon():
    """CATCH — the Freezer segment kept the main band and Use soon."""
    render = _fn("renderHome")
    assert "seg === 'freezer' && frozen" in render
    assert "Oldest first. Next week’s plan starts from the top." in render
    assert '<span class="home-band-accent">in the freezer</span>' in _fn("homeFreezerTitleHtml")
    body = _fn("homeBodyHtml")
    freezer_part = body[body.index("if (seg === 'freezer')"):body.index("return homeUseSoonHtml")]
    assert "homeUseSoonHtml" not in freezer_part and "homeCheckHtml" in freezer_part
    assert "r.label || r.item" in _fn("homeShelfHtml")
    assert "location=freezer" not in _fn("loadHomeCheck")  # every shelf's chips (verify item 4)
    assert "/api/inventory/check'" in _fn("loadHomeCheck")
    assert ".home-check {" in SHELL_CSS


# ---------------------------------------------------------------------------
# Empty freezer
# ---------------------------------------------------------------------------

def test_an_empty_freezer_keeps_the_kitchen_band(signed_in):
    """GUARD (API) + CATCH (screen rule) — nothing frozen: no freezer band
    (it would read "0 things"), the shelf's own empty line."""
    _row("Milk", location="fridge", exp=_day(4))
    home = signed_in.get("/api/whats-home").json()
    assert home["counts"]["freezer"] == 0 and home["locations"]["freezer"] == []
    # renderHome only switches band when the freezer has something in it.
    assert "seg === 'freezer' && frozen" in _fn("renderHome")
    assert ">Nothing in the '" in _fn("homeShelfHtml")


# ---------------------------------------------------------------------------
# Independent verify of 63b21fb (VERIFY-inventory-kitchen-freezer.md)
# ---------------------------------------------------------------------------

def test_use_soon_shows_whats_home_whatever_the_dislikes_and_claims():
    """CATCH (verify items 1-2) — Use soon read the planner's offer, which
    leaves out a dislike/allergy and food another plan has claimed: the
    mushrooms going off today vanished from Use soon (and lost Freeze it)
    while the fridge still listed them."""
    tools.add_food_dislikes(["mushrooms"])
    mush, _ = _row("Mushrooms", location="fridge", exp=_day(0), category="produce")
    feta, _ = _row("Feta", location="fridge", exp=_day(2), category="dairy")
    # Feta is on a dish a draft holds more than a week out.
    _approved_meal(10, recipe="Greek salad", ingredients=["feta"], status="draft",
                   derived={"use_up": [{"id": feta, "item": "Feta", "dish": "Greek salad"}]})
    soon = {s["id"] for s in whats_home.get_whats_home()["use_soon"]}
    assert {mush, feta} <= soon
    # The planner still never offers them.
    offered = {c["id"] for c in tools.use_up_candidates(_day(0), 7)}
    assert mush not in offered and feta not in offered


def test_the_rows_and_the_chips_use_one_staple_rule():
    """CATCH (verify item 3) — a "Wheat bread" staple hid "Whole wheat
    sourdough bread" from the freezer rows while the chip still asked."""
    conn = get_conn()
    conn.execute("INSERT INTO staples (household_id, item, next_due_at) VALUES (1, 'Wheat bread', '2099-01-01')")
    conn.commit()
    conn.close()
    bread, _ = _row("Whole wheat sourdough bread", created_days_ago=80)
    peas, _ = _row("Peas", created_days_ago=80)
    chips = {c["id"] for c in tools.inventory_check_candidates()}
    rows = set(_freezer_ids())
    assert bread not in rows and bread not in chips
    assert peas in rows and peas in chips


def test_a_portion_next_weeks_draft_or_a_later_night_has_reads_as_planned():
    """CATCH (verify item 5) — a portion's plan was read from approved plans
    in the next 7 days only, so one next week's draft had put on a night (or
    one further out) read "from last Thu"."""
    soon = _portion("Lasagna", 4, cooked_days_ago=9)
    far = _portion("Chili", 3, cooked_days_ago=20)
    _approved_meal(3, freeform="Lasagna from the freezer", status="draft",
                   derived={"freezer_portion": {"inventory_item_id": soon, "dish": "Lasagna"}})
    _approved_meal(15, freeform="Chili from the freezer", status="approved",
                   derived={"freezer_portion": {"inventory_item_id": far, "dish": "Chili"}})
    assert _freezer_row(soon)["line"].startswith("for ") and _freezer_row(soon)["plan"]
    far_day = household_today() + datetime.timedelta(days=15)
    assert _freezer_row(far)["line"] == f"for the dinner on {far_day.day} {far_day.strftime('%b')}"


def test_portions_sort_by_the_night_they_were_cooked():
    """CATCH (verify item 7) — portions sorted by when the row was written
    but say when they were cooked."""
    beef, _ = _row("Ground beef", frozen_on=_day(-6), created_days_ago=6)
    stew = _portion("Stew", 2, cooked_days_ago=10)  # row written now, cooked 10 days ago
    ids = _freezer_ids()
    assert ids.index(stew) < ids.index(beef)
