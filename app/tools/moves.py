"""
Today's moves — one timeline of "what's next for us?".

Emily's approved Today design (2026-09-08) replaced a screen of separate
cards (a tall dinner hero, a prep tile, a defrost tile, a grocery tile, a
notifications feed) with two blocks: ONE compact "Next up" card and a plain
list of everything else today. That only works if all of those things are
the SAME kind of thing, ranked against each other — so this module turns
them into one shape, a *move*, and ranks them.

A move is a thing the household has to actually do today, with a window in
which doing it makes sense:

    {id, kind, title, detail, reason, date, slot, window_start, window_end,
     weight, action: {label, target}, done, tickable, overdue, entry_id,
     task_id, duration_min, time_label, chips}

`tickable` is true only when ticking the move actually does something —
set_move_done dispatches cook/reheat to check_off_meal and fridge/prep to
check_off_prep_step, but a shop move has nothing behind its tick to flip
(see set_move_done's own `kind == "shop"` branch), so it renders with no
tick at all rather than one that fills in and silently snaps back.

`overdue` is true for an undone fridge/prep move whose window has closed
for the day — see _prep_moves and featured_move_id below.

Nothing here is new state. Every move is derived from something that
already exists — a cooker-view card, a prep_tasks row, the grocery list —
and every tick dispatches back to the tool that owns that state
(check_off_meal / check_off_prep_step). There is deliberately no
`today_moves` table: a second store for "is this done?" is a second answer
to a question that already has one, and the two would drift the first time
someone checked a meal off in Cook mode instead of on Today.

Ranking (the whole point of the screen):

    Next up = among the moves that are not done, are not a reheat, and
    whose window is open now or opens within the next four hours — the
    highest weight first, then the earliest window_start.

Reheats are excluded from being featured on Emily's instruction: made-ahead
food "is a line, never the card". If nothing qualifies there is no featured
move at all and the screen shows tomorrow's first move instead — an honest
"nothing left today" rather than a card promoting whatever is nearest.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

# Every read below goes through another tool module, so this never touches
# the DB directly and never needs household_id() itself — the scoping comes
# from get_cooker_view / list_grocery_list / get_household_rhythm, each of
# which is already request-scoped (see _shared.household_id).
from . import cooker as _cooker
from . import defrost as _defrost
from . import grocery as _grocery
from . import rhythm as _rhythm

logger = logging.getLogger(__name__)


# How much a kind of move matters when two of them are open at once. Three
# steps, not a score: the screen only ever has to answer "which one is the
# card", and a finer scale would be a precision nobody set.
WEIGHT_HIGH = 3
WEIGHT_MEDIUM = 2
WEIGHT_LOW = 1

# The hours a household's meals usually land at, when nothing better is
# known. Breakfast and lunch have no rhythm question behind them yet (the
# six locked ones are in rhythm.py and none of them ask), so these are
# stated as defaults here rather than pretending to be household facts.
# Dinner DOES have one — dinner_window — and is read from it below.
DEFAULT_SLOT_HOURS = {
    "breakfast": time(8, 0),
    "lunch": time(12, 30),
    "dinner": time(18, 30),
    "snack": time(15, 30),
}

# How long after its usual hour a meal is still the thing to be doing.
SLOT_WINDOW_HOURS = 2

# How far ahead a move can be and still be offered as "next up". Four hours
# is the difference between "start the slow cooker now" and a card that
# tells you at eight in the morning to cook dinner.
URGENT_HOURS = 1  # a deadline this close takes the card whatever its weight (Emily, 2026-09-08)
LOOKAHEAD_HOURS = 4

# A cook needing shopping this far out is close enough that the shop is
# today's problem.
SHOP_HORIZON_HOURS = 36

REHEAT_ACTION_LABEL = "Mark eaten"  # keep in step with shell.js's own constant


# ---------- small time helpers ----------

def _as_date(day: str | date | None) -> date:
    if day is None:
        return date.today()
    if isinstance(day, date):
        return day
    return date.fromisoformat(day)


def _clock(t: time) -> str:
    """"6:30", "8:00", "noon" — the time the way a person says it aloud."""
    if t.hour == 12 and t.minute == 0:
        return "noon"
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d}"


def _dinner_clock() -> time:
    """
    When dinner actually lands for this household, from the dinner_window
    rhythm fact. defrost.py already owns that mapping (it schedules
    backwards from the same clock) — read it rather than writing a second
    copy that can drift. 'all_over' and an unanswered question both mean
    there is no household clock, so the default stands.
    """
    try:
        window = _rhythm.get_household_rhythm().get("dinner_window")
    except Exception:
        window = None
    return _defrost._DINNER_CLOCK_BY_WINDOW.get(window or "") or DEFAULT_SLOT_HOURS["dinner"]


def _slot_time(slot: str, dinner_clock: time) -> time:
    if slot == "dinner":
        return dinner_clock
    return DEFAULT_SLOT_HOURS.get(slot or "", DEFAULT_SLOT_HOURS["dinner"])


def _slot_dt(day: date, slot: str, dinner_clock: time) -> datetime:
    return datetime.combine(day, _slot_time(slot, dinner_clock))


def _slot_time_label(slot: str, at: datetime) -> str:
    """"6:30 tonight", "8:00 this morning" — never "18:30"."""
    clock = _clock(at.time())
    if slot == "dinner":
        return f"{clock} tonight"
    if slot == "breakfast":
        return f"{clock} this morning"
    return f"{clock} today"


def _weekday(day: str) -> str:
    return date.fromisoformat(day).strftime("%A")


# ---------- the sources ----------

def _cook_and_reheat_moves(view: dict, day: date, dinner_clock: time) -> list[dict]:
    day_str = day.isoformat()
    moves: list[dict] = []
    for meal in view.get("meals") or []:
        if meal.get("date") != day_str:
            continue
        slot = meal.get("slot") or "dinner"
        at = _slot_dt(day, slot, dinner_clock)
        done = meal.get("cooked_status") == "done"
        if meal.get("is_leftovers"):
            source = meal.get("leftovers_from") or {}
            # "Made ahead — Sunday's Egg White Bites" vs "Leftovers —
            # Sunday's Bulgogi": the same fact, one honest word apart (see
            # leftovers.made_ahead_headline). The list wants the dish as
            # the title and the provenance as the small grey line.
            headline = meal.get("leftovers_headline") or ""
            made_ahead = headline.startswith("Made ahead")
            lead = "made ahead" if made_ahead else "leftovers from"
            source_date = source.get("date")
            provenance = f"{lead} {_weekday(source_date)}" if source_date else lead
            moves.append({
                "id": f"reheat:{meal['entry_id']}",
                "kind": "reheat",
                "title": meal.get("meal") or headline or "Leftovers",
                "detail": f"{provenance} · reheat · {_clock(at.time())}",
                "reason": meal.get("reheat_note") or "",
                "date": day_str,
                "slot": slot,
                "window_start": at.isoformat(),
                "window_end": (at + timedelta(hours=SLOT_WINDOW_HOURS)).isoformat(),
                "weight": WEIGHT_LOW,
                "action": {"label": REHEAT_ACTION_LABEL, "target": {"kind": "check_meal", "entryId": meal["entry_id"]}},
                "done": done,
                "tickable": True,
                "overdue": False,
                "entry_id": meal["entry_id"],
                "task_id": None,
                "duration_min": 0,
                "time_label": _slot_time_label(slot, at),
                "chips": [],
            })
            continue

        duration = (meal.get("prep_time_minutes") or 0) + (meal.get("cook_time_minutes") or 0)
        start = at - timedelta(minutes=duration)
        detail_bits = [slot]
        if duration:
            detail_bits.append(f"{duration} min")
        detail_bits.append(_clock(at.time()))
        chips = []
        if duration:
            chips.append(f"{duration} min")
            chips.append(f"Start by {_clock(start.time())}")
        moves.append({
            "id": f"cook:{meal['entry_id']}",
            "kind": "cook",
            "title": meal.get("meal") or slot.capitalize(),
            "detail": " · ".join(detail_bits),
            "reason": meal.get("covers_note") or meal.get("reasoning") or "",
            "date": day_str,
            "slot": slot,
            "window_start": start.isoformat(),
            "window_end": (at + timedelta(hours=SLOT_WINDOW_HOURS)).isoformat(),
            "weight": WEIGHT_HIGH,
            "action": {
                "label": "Cook this",
                # Exactly the focus payload Meals' own "Cook this" passes —
                # see shell.js activateTab('kitchen', …, {cookFocus}). Cook
                # mode is a step of the Kitchen tab as of 2026-09-08; it was
                # a state of Meals ({tab: "week", mealsView: "cook"}) until
                # then, and nothing but this dict decided which.
                "target": {
                    "tab": "kitchen",
                    "cookFocus": {
                        "entryId": meal["entry_id"],
                        "date": day_str,
                        "slot": slot,
                        "title": meal.get("meal") or "",
                    },
                },
            },
            "done": done,
            "tickable": True,
            "overdue": False,
            "entry_id": meal["entry_id"],
            "task_id": None,
            "duration_min": duration,
            "time_label": _slot_time_label(slot, at),
            "chips": chips,
        })
    return moves


def _prep_moves(view: dict, day: date, now: datetime, dinner_clock: time) -> list[dict]:
    """
    Fridge moves (task_type='defrost') and the rest of the day's prep.

    Both are all-day moves with a deadline rather than a start time: taking
    something out of the freezer at nine in the morning and at four in the
    afternoon are both fine, and only the "by" time is real.

    Two different clocks are in play here, and conflating them was the bug:

    - `window_end`, the hard end of this move's day — 22:00, not tonight's
      dinner. A defrost row due TODAY is always for a LATER day's meal
      (defrost._move_date never schedules the cook's own day; it floors at
      one full day of buffer), so tying the window's *close* to tonight's
      dinner_clock meant an undone fridge move failed featured_move_id's
      `window_end >= now` test the moment dinner passed and dropped out of
      the candidate set entirely — the evening card could never say "take
      tomorrow's chicken out" because the move it needed had already
      (wrongly) expired mid-evening.
    - the household's ordinary evening hour (dinner_clock) still marks the
      point after which this move is running late — not because the fridge
      move is FOR tonight's dinner, but because "tonight" as a household
      concept has, by then, effectively started. Past it, the move is
      `overdue` and the copy switches from the forward-looking "by tonight"
      to the plainer "still to do"; before it, the window is still fully
      open. Either way it stays a candidate all the way to `window_end`
      (and even past it, per `overdue`) — see featured_move_id.
    """
    day_str = day.isoformat()
    window_end = datetime.combine(day, time(22, 0))
    evening = datetime.combine(day, dinner_clock)
    open_from = datetime.combine(day, time(0, 0))
    moves = []
    for task in view.get("prep_tasks") or []:
        if task.get("task_date") != day_str:
            continue
        is_fridge = task.get("task_type") == "defrost"
        description = (task.get("description") or "").strip()
        # defrost descriptions are written "<the move> — <what it's for>."
        # (defrost._describe). Split them so the title stays a title and the
        # rest becomes the featured card's one italic line.
        head, _, tail = description.partition(" — ")
        title = (head or description).rstrip(".")
        reason = tail.rstrip(".")
        # 'skipped' is a resolution too — check_off_prep_step accepts it
        # and the Today tile used to offer it — so it counts as handled,
        # not as still waiting.
        done = task.get("status") in ("done", "skipped")
        overdue = (not done) and evening < now
        when = "still to do" if overdue else "by tonight"
        moves.append({
            "id": ("fridge:" if is_fridge else "prep:") + str(task["id"]),
            "kind": "fridge" if is_fridge else "prep",
            "title": title or ("Fridge move" if is_fridge else "Prep"),
            "detail": ("fridge move" if is_fridge else "prep") + f" · {when}",
            "reason": reason or (task.get("related_meal") and f"for {task['related_meal']}") or "",
            "date": day_str,
            "slot": None,
            "window_start": open_from.isoformat(),
            "window_end": window_end.isoformat(),
            "weight": WEIGHT_MEDIUM,
            "action": {"label": "Done", "target": {"kind": "check_prep", "taskId": task["id"]}},
            "done": done,
            "tickable": True,
            "overdue": overdue,
            "entry_id": task.get("meal_plan_entry_id"),
            "task_id": task["id"],
            "duration_min": 0,
            "time_label": when,
            "chips": [],
        })
    return moves


def _shop_move(view: dict, day: date, now: datetime, dinner_clock: time) -> list[dict]:
    """
    One move, only when both halves are true: the list still has things on
    it, and there is a real cook close enough for that to matter. A standing
    grocery list with nothing to cook against is not a thing to do today.
    """
    if day != now.date():
        return []
    try:
        needed = _grocery.list_grocery_list(status="needed")
    except Exception:
        needed = []
    if not needed:
        return []

    horizon = now + timedelta(hours=SHOP_HORIZON_HOURS)
    soonest = None
    for meal in view.get("meals") or []:
        if meal.get("is_leftovers") or meal.get("cooked_status") == "done":
            continue
        try:
            at = _slot_dt(date.fromisoformat(meal["date"]), meal.get("slot") or "dinner", dinner_clock)
        except (KeyError, ValueError):
            continue
        if now <= at <= horizon and (soonest is None or at < soonest):
            soonest = at
    if soonest is None:
        return []

    is_today = soonest.date() == now.date()
    count = len(needed)
    return [{
        "id": f"shop:{day.isoformat()}",
        "kind": "shop",
        "title": "Shop for tonight" if is_today else "Shop before tomorrow",
        "detail": f"{count} item{'' if count == 1 else 's'} · by {_clock(soonest.time())}",
        "reason": "",
        "date": day.isoformat(),
        "slot": None,
        # Start of day, not "now". The shop has been open all day, and this
        # is also what makes shopping outrank the cook it is for: they share
        # the top weight, and the tie-break is the earlier window.
        "window_start": datetime.combine(day, time(0, 0)).isoformat(),
        "window_end": soonest.isoformat(),
        "weight": WEIGHT_HIGH,
        "action": {"label": "Open the list", "target": {"tab": "grocery"}},
        # Derived, like every other tick: the move exists only while the
        # list has needed items on it, so it is never "done" — it stops
        # being a move instead.
        "done": False,
        # Nothing to tick: set_move_done's "shop" branch is a no-op (see its
        # own docstring) — there is no flag here for a tick to flip, so the
        # UI renders no tick at all rather than one that fills in and snaps
        # back. See the module docstring's `tickable` note.
        "tickable": False,
        "overdue": False,
        "entry_id": None,
        "task_id": None,
        "duration_min": 0,
        "time_label": ("by " + _clock(soonest.time())) if is_today else "by tomorrow",
        "chips": [],
    }]


# TODO(prep sessions): a scheduled prep session is a high-weight move with a
# "Start prep" action, but nothing on main reports one yet — there is no
# /api/week/{ws}/prep-sessions route and get_cooker_view carries no session
# in its payload. Add the source here (not a new store) once that lands.


# ---------- the timeline ----------

def moves_for_day(
    day: str | date | None = None,
    now: datetime | None = None,
    view: dict | None = None,
) -> list[dict]:
    """
    Every move for one day, earliest window first.

    `now` exists for tests and for the shop rule's 36-hour horizon; the UI
    always passes real time (i.e. omits it). `view` lets a caller that
    already has the cooker view (today_moves, which needs it twice over)
    hand it in rather than paying for a second full read of the plan.
    """
    target = _as_date(day)
    now = now or datetime.now()
    dinner_clock = _dinner_clock()
    view = view if view is not None else _cooker.get_cooker_view()

    moves = (
        _cook_and_reheat_moves(view, target, dinner_clock)
        + _prep_moves(view, target, now, dinner_clock)
        + _shop_move(view, target, now, dinner_clock)
    )
    moves.sort(key=lambda m: (m["window_start"], -m["weight"], m["id"]))
    return moves


def featured_move_id(moves: list[dict], now: datetime | None = None) -> str | None:
    """
    Which move is the card. See the module docstring: open now or within
    four hours, highest weight, then earliest window. A reheat never
    features — Emily, 2026-09-08: made-ahead food is a line, never the card.

    An `overdue` move (an undone fridge/prep task whose window has already
    closed for the day — see _prep_moves) stays a candidate even though its
    own window_end is in the past: that flag exists precisely so a fridge
    move due today doesn't drop out of contention the moment the (wrong,
    dinner-shaped) deadline it used to carry passed.
    """
    now = now or datetime.now()
    horizon = (now + timedelta(hours=LOOKAHEAD_HOURS)).isoformat()
    now_iso = now.isoformat()
    candidates = [
        m for m in moves
        if not m["done"]
        and m["kind"] != "reheat"
        and m["window_start"] <= horizon
        and (m.get("overdue") or m["window_end"] >= now_iso)
    ]
    if not candidates:
        return None
    # Emily, 2026-09-08: importance wins, except that a deadline inside the
    # next hour wins over everything — the chicken that has to move to the
    # fridge by tonight beats a cook whose window only just opened.
    urgent_by = (now + timedelta(hours=URGENT_HOURS)).isoformat()
    def _rank(m):
        urgent = bool(m.get("overdue")) or m["window_end"] <= urgent_by
        return (0 if urgent else 1, -m["weight"], m["window_start"], m["id"])
    candidates.sort(key=_rank)
    return candidates[0]["id"]


def _week_state(view: dict) -> str:
    """'set' | 'draft' | 'none' — the small badge under Today's date."""
    if not view.get("weekly_plan_id"):
        return "none"
    return "set" if view.get("status") == "approved" else "draft"


def today_moves(day: str | date | None = None, now: datetime | None = None) -> dict:
    """
    The whole Today payload: the day's moves, which one is the card, the
    "N of M done" count, the week-state badge, and — for the days with
    nothing left on them — tomorrow's first move.
    """
    now = now or datetime.now()
    target = _as_date(day)
    view = _cooker.get_cooker_view()
    moves = moves_for_day(target, now=now, view=view)
    featured = featured_move_id(moves, now=now)

    tomorrow = None
    if featured is None:
        ahead = moves_for_day(target + timedelta(days=1), now=now, view=view)
        undone = [m for m in ahead if not m["done"]]
        tomorrow = undone[0] if undone else None

    return {
        "date": target.isoformat(),
        "moves": moves,
        "featured": featured,
        "done": sum(1 for m in moves if m["done"]),
        "total": len(moves),
        "week_state": _week_state(view),
        "tomorrow": tomorrow,
        # The quiet label beside the date when today is a holiday — name
        # and answer, or None on an ordinary day (see holidays.py).
        "holiday": _today_holiday(target),
    }


def _today_holiday(target: date) -> dict | None:
    from . import holidays as _holidays

    try:
        h = _holidays.holiday_on(target.isoformat())
    except Exception:
        logger.exception("Today's holiday label could not be built")
        return None
    if not h:
        return None
    return {"name": h["name"], "answer": h["answer"]["answer"] if h["answer"] else None,
            "label": _holidays.holiday_day_label(h)}


def set_move_done(move_id: str, done: bool = True) -> dict:
    """
    Tick or untick one move, by dispatching to whichever tool owns the state
    behind it. Nothing is written here — see the module docstring for why
    there is no moves table.
    """
    kind, _, ref = (move_id or "").partition(":")
    status = "done" if done else "pending"
    if kind in ("cook", "reheat"):
        _cooker.check_off_meal(int(ref), status)
        return {"id": move_id, "done": done, "dispatched_to": "check_off_meal"}
    if kind in ("fridge", "prep"):
        _cooker.check_off_prep_step(int(ref), status)
        return {"id": move_id, "done": done, "dispatched_to": "check_off_prep_step"}
    if kind == "shop":
        # Shopping is done when the list says so — there is nothing here to
        # write, and inventing a flag would make Today disagree with Grocery.
        return {"id": move_id, "done": False, "dispatched_to": None}
    raise ValueError(f"Unknown move id: {move_id!r}")
