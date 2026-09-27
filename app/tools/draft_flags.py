"""
When the draft has to bend, it says so — one line, with the fix already
worked out.

Emily's decided snag rules (2026-09-23, the "When your requests and your
week don't line up" page). Her own words for the shape of it: "Whenever
Pomona bends a rule or moves something, it adds one line to the top of the
draft. You don't get a pop-up, and you don't have to answer anything."

THE RULE THIS FILE BUILDS TODAY IS HER SCENARIO 3 — a dish the household
asked for BY NAME on a night that hasn't the time for it:

    Lasagna takes 60 minutes, and Wednesday is short on time.
       [ Prep it Tuesday night ]   [ Move it to Saturday ]

The KEEPING half already worked and is not this file's doing:
`cap_enforce` deliberately leaves a night alone when the household spoke
for it (`meal_variety.theirs`), because a cap is not a reason to overrule
a choice. What was missing was that nothing SAID so. plan_quality measured
the breach into the morning report — a file Emily reads and the household
never sees — and the draft showed the over-cap dinner without a word. So
the flag is the missing half, not a new rule.

WHERE THE FLAGS LIVE. `weekly_plans.draft_flags_json`, its own column and
deliberately NOT a key inside `requests_json`: `record_plan_requests`
rewrites that blob wholesale after generation, so a flag written during
`_finish_week_slots` would be silently clobbered by it.

WHAT A FLAG IS, and what it is not. It is a record of something the app
did (or declined to do) to one entry, plus the fixes it has already worked
out for the household — never a question, never blocking, never a pop-up.
`fixes` may be empty: a flag with nothing to offer is still worth saying.

THE TWO FIXES ARE EXISTING WRITES, not new ones:
  * "Prep it Tuesday night" → `prep_sessions.add_prep_cut`, the prep-cut
    row the Cook tab already draws and ticks. WHERE it draws it depends on
    the household, and the shorter sentence was wrong: `prep_sessions`
    only gathers a session on a declared `rhythm.prep_days` weekday (or a
    prepped-lunch batch date), and `cookFocusPrepTasks` deliberately
    excludes `prep_cut` rows because they "belong to their prep session".
    So on a household with prep days it lands in the session; on one
    without — which is the default — it lands in Cook's loose get-ready
    rows and on Today's own timeline on the day itself. It surfaces
    either way, which is what the fix needs; it is not always a session.
    It does not
    move the dish or touch the plan; it splits the work, which is what
    the household is being offered.
  * "Move it to Saturday" → `weekly_plan.swap_dinner_nights`, which
    TRADES the two nights' dinners in place (ids kept, so groceries, the
    cooked tick and the plate's sides ride along, and the shopping list
    is untouched). Because it is a trade and not a move, the night we
    offer must be one whose own dinner can take the short night — see
    `move_target`, which is the whole care in this file.

READ-TIME HONESTY. A flag is about one entry on one night. If that entry
has left the plan, or its dish has changed, or it is no longer on the date
the flag names, the flag is about nothing and `plan_flags` drops it —
the same read-time guard `cooker.get_prep_schedule` puts on a dangling
prep row, and for the same reason: `meal_plan_entries` rows are deleted
and re-dated by half a dozen paths, and a read-time rule is the one place
that covers every one of them, past and future.

DELIBERATELY NOT BUILT HERE, so nobody reports them as missing:
  * Scenario 5 (a requested dish that fits NO night goes on the night with
    the most time, and Pomona says so) and scenarios 1/2 (where a request
    with no day named should land) are the PLACEMENT half of the card.
    They want a pass that runs before the flags exist; this file is only
    the record and the one rule whose data was already sitting there.
  * A chain end — a cook that feeds a later night, or the reheat itself —
    is never flagged. cap_enforce's own docstring names the over-cap
    chain source as its biggest open hole and Emily's call; widening into
    it from here would be deciding it unmeasured.
"""
from __future__ import annotations

import datetime
import json
import logging

from ..db import get_conn
from ._shared import household_id

logger = logging.getLogger("home_manager")

# The one kind this file writes today. A kind is what the flag is ABOUT,
# so the screen can group or style them later without parsing the words.
OVER_CAP_REQUEST = "over_cap_request"

# What a fix does. Each is an existing write; see the module docstring.
FIX_PREP_AHEAD = "prep_ahead"
FIX_MOVE = "move"

_WEEKDAY_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _weekday(meal_date: str) -> str:
    try:
        return _WEEKDAY_LONG[datetime.date.fromisoformat(meal_date).weekday()]
    except (TypeError, ValueError, IndexError):
        return meal_date


def over_cap_text(dish: str, minutes: int, meal_date: str) -> str:
    """
    Emily's own sentence, word for word off the decided page: "Lasagna
    takes 60 minutes, and Wednesday is short on time."
    """
    return f"{dish} takes {int(minutes)} minutes, and {_weekday(meal_date)} is short on time."


def prep_fix(prep_date: str) -> dict:
    return {"action": FIX_PREP_AHEAD, "label": f"Prep it {_weekday(prep_date)} night", "date": prep_date}


def move_fix(to_date: str) -> dict:
    return {"action": FIX_MOVE, "label": f"Move it to {_weekday(to_date)}", "date": to_date}


PREP_CUT_TEXT = "Get {dish} ready for {weekday}"


def prep_cut_description(dish: str, meal_date: str) -> str:
    """What the prep-cut row says on the Cook tab once the household taps it."""
    return PREP_CUT_TEXT.format(dish=dish, weekday=_weekday(meal_date))


# ---------- choosing the two nights ----------

def prep_target(meal_date: str, nights: list[dict]) -> str | None:
    """
    The night before, when there is one to prep on.

    Only the day immediately before — "prep it the night before" is the
    household's own phrase and means that night, not "some earlier night
    we picked".

    Whether that night is usable is read off the plan's own dinners rather
    than from a separately-computed list of days written off. Every way a
    night can be unavailable — out, away, a skipped day, a meal category
    the household asked none of — is written as the SAME thing, a
    `planned_empty` row (see agent._finish_week_slots), so "the night
    before has a dinner row that isn't planned_empty" is one test that
    covers all of them and cannot drift from them. No row at all means the
    day is outside the period: the first day of a plan has no night before
    it that the plan may write on.
    """
    try:
        before = (datetime.date.fromisoformat(meal_date) - datetime.timedelta(days=1)).isoformat()
    except (TypeError, ValueError):
        return None
    if _has_gone(before):
        return None
    for night in nights:
        if night.get("date") == before:
            return None if night.get("slot_state") == "planned_empty" else before
    return None


def move_target(night: dict, others: list[dict], caps: dict[str, int | None]) -> str | None:
    """
    The nearest night whose dinner can trade places with this one.

    `swap_dinner_nights` TRADES, so a move is only honest when BOTH ends
    come out within their caps: the flagged dish must fit the night we
    send it to, and that night's own dish must fit the short night it
    comes back to. Offering a move that would simply hand the same problem
    to another night is worse than offering nothing.

    Nearest by absolute day distance; a tie goes to the LATER night,
    because a week reads forwards and "move it to Saturday" is a smaller
    thing to absorb than "move it to Monday" on a Wednesday. Deterministic
    either way, so a test can name the answer.
    """
    minutes = night.get("minutes")
    if not minutes:
        return None
    try:
        here = datetime.date.fromisoformat(night["date"])
    except (TypeError, ValueError):
        return None

    def distance(row: dict) -> tuple[int, int]:
        try:
            gap = (datetime.date.fromisoformat(row["date"]) - here).days
        except (TypeError, ValueError):
            return (99, 0)
        # Later first on a tie: -1 sorts before 0 for the same abs().
        return (abs(gap), 0 if gap > 0 else 1)

    for row in sorted(others, key=distance):
        if row["date"] == night["date"] or not row.get("movable"):
            continue
        their_minutes = row.get("minutes")
        if not their_minutes:
            continue  # an unjudgeable dish can't be shown to fit anything
        there = caps.get(row["date"])
        back = caps.get(night["date"])
        if there and minutes > there:
            continue  # our dish still doesn't fit over there
        if back and their_minutes > back:
            continue  # theirs would land on the short night and break it
        if _has_gone(row["date"]):
            continue  # a night that is over is not somewhere to send a dinner
        # The one gate a move can break, and cap_enforce refuses its own
        # trades on it: who is at the table is per night, so a dish nobody
        # here minds can be one somebody there has said no to. Asked of
        # BOTH ends, because a trade moves two dinners.
        if _offends(row.get("meal"), night["date"]) or _offends(night.get("meal"), row["date"]):
            continue
        return row["date"]
    return None


def _has_gone(meal_date: str) -> bool:
    """
    Whether that night is behind the household's own today.

    Imported here rather than at module scope: `weekly_plan` reaches this
    module through `cap_enforce`, so a top-level edge would be a cycle for
    one predicate. It is `weekly_plan.night_has_gone` and NOT a date
    comparison of this module's own, for the reason that function's own
    docstring gives at length — the household's clock, never the server's,
    which from 8pm local are different days. A failure to read it answers
    False: an offer that turns out to be stale is a smaller wrong than a
    draft with no fixes on it at all, and the write behind the move asks
    its own questions.
    """
    if not meal_date:
        return True
    try:
        from . import weekly_plan as _weekly_plan
        return bool(_weekly_plan.night_has_gone(meal_date))
    except Exception:
        logger.exception("Reading the household's clock for %s failed", meal_date)
        return False


def _offends(meal: str | None, meal_date: str) -> str | None:
    """
    cap_enforce's own veto check, asked at this door too — see
    `cap_enforce.would_offend`, which is public for exactly this. Lazily
    imported because that module imports this one. A failure answers None:
    the taste record is advisory and a missing read must not take every
    fix off the draft.
    """
    if not (meal or "").strip() or not meal_date:
        return None
    try:
        from . import cap_enforce as _cap_enforce
        return _cap_enforce.would_offend(meal, meal_date)
    except Exception:
        logger.exception("Reading the taste verdict for %s on %s failed", meal, meal_date)
        return None


# ---------- the store ----------

def _clean(flag: dict) -> dict | None:
    """One flag, trimmed to the fields that are stored. Anything else is dropped."""
    entry_id = flag.get("entry_id")
    text = str(flag.get("text") or "").strip()
    if not entry_id or not text:
        return None
    # THE FIXES ARE DELIBERATELY NOT STORED, and dropping them here is what
    # makes that true of every caller rather than of the one that remembers.
    # An offer is a promise about a PAIR of nights and only one of them is
    # named by the flag, so a stored offer is a promise nothing revalidates
    # — see `fixes_for`. `plan_flags` derives them from the live week.
    return {
        "kind": str(flag.get("kind") or OVER_CAP_REQUEST),
        "text": text,
        "entry_id": int(entry_id),
        "date": str(flag.get("date") or "").strip(),
        "slot": str(flag.get("slot") or "").strip(),
        "dish": str(flag.get("dish") or "").strip(),
    }


def record(weekly_plan_id: int, flags: list[dict]) -> None:
    """
    Store this generation's flags, replacing whatever was there.

    Replacing rather than appending is deliberate: generation writes the
    whole set once, at the end, so a re-draft of the same plan describes
    the week as it now stands rather than accumulating the ghosts of
    earlier attempts.
    """
    cleaned = [f for f in (_clean(flag) for flag in flags or []) if f]
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET draft_flags_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(cleaned) if cleaned else "", weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()


def _stored(weekly_plan_id: int) -> list[dict]:
    conn = get_conn()
    row = conn.execute(
        "SELECT draft_flags_json FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    conn.close()
    try:
        data = json.loads(row["draft_flags_json"]) if row and row["draft_flags_json"] else []
    except (TypeError, ValueError):
        data = []
    return [f for f in data if isinstance(f, dict)]


def plan_flags(weekly_plan_id: int, intake: dict | None = None,
               memory: dict | None = None) -> list[dict]:
    """
    The flags that are still about something, each carrying the offers that
    are still true.

    A flag names one entry on one date carrying one dish. If the row has
    gone, moved, become a different meal, been cooked, or stopped being
    over its cap, the flag is stale and is dropped here rather than shown —
    see the module docstring on why this is a read-time rule and not a
    sweep at every write site.

    AND THE FIXES ARE RE-DERIVED, not read back. Nothing about an offer is
    stored; see `fixes_for` for why, which is the most important paragraph
    in this file. The short version: a fix is a promise about a PAIR of
    nights, the second of which this flag does not name, and the week moves
    under a draft constantly — including because of the flag's own sibling.
    A flag whose offers have all gone stale keeps its sentence and loses
    its buttons, which is the honest answer and is also what the card asks
    for ("doing nothing leaves it as asked").

    It reads the week through `cap_enforce.nights`, the same one reading of
    the plan and of the time rules the producer used, so the two can never
    disagree about a night. `intake` and `memory` are the caps' own inputs;
    a caller holding them should pass them, and one that isn't pays for two
    small reads.
    """
    flags = _stored(weekly_plan_id)
    if not flags:
        return []
    nights = _week_nights(weekly_plan_id, intake, memory)
    by_id = {n["id"]: n for n in nights}
    live = []
    for flag in flags:
        night = by_id.get(flag.get("entry_id"))
        if night is None:
            continue
        if flag.get("date") and night["date"] != flag["date"]:
            continue  # the night moved — the sentence names a weekday that is no longer true
        if flag.get("dish") and (night.get("meal") or "") != flag["dish"]:
            continue  # a different dinner is there now
        if (night.get("cooked_status") or "") == "done":
            continue  # the work is done; the producer's own rule, asked again here
        if not night.get("over"):
            continue  # it fits now — somebody solved it another way
        live.append({**flag, "fixes": fixes_for(night, nights)})
    return live


def _week_nights(weekly_plan_id: int, intake: dict | None,
                 memory: dict | None) -> list[dict]:
    """
    The plan's dinners as `cap_enforce` judges them — one reading of the
    rows and of the time caps, shared with the pass that wrote the flags.

    Lazy import: `cap_enforce` imports this module. A failure answers an
    empty list, which takes every flag off the draft rather than showing
    one whose offers nobody could check — the safe direction, since a flag
    is a remark and a wrong button is a write.
    """
    try:
        from . import cap_enforce as _cap_enforce
        if intake is None:
            from . import week_intake as _week_intake
            conn = get_conn()
            row = conn.execute(
                "SELECT week_start_date FROM weekly_plans WHERE id = ? AND household_id = ?",
                (weekly_plan_id, household_id()),
            ).fetchone()
            conn.close()
            # The intake is what carries `night_tags`, and a night's tag is
            # what makes its cap a rush cap — so reading the flags without
            # it would judge `over` against a different rule from the one
            # that wrote them. The filing key, not the content start date:
            # that is how the intake is addressed everywhere.
            intake = (_week_intake.get_week_intake(row["week_start_date"]) if row else None) or {}
        if memory is None:
            from . import memory as _memory
            memory = _memory.get_household_memory() or {}
        return _cap_enforce.nights(weekly_plan_id, intake, memory)
    except Exception:
        logger.exception("Re-reading plan %s to check its draft flags failed", weekly_plan_id)
        return []


def dismiss(weekly_plan_id: int, entry_id: int) -> bool:
    """
    Take one entry's flag off, because its fix has been applied.

    The move fix does not need this — re-dating the row makes `plan_flags`
    drop the flag on its own — but the prep fix leaves the entry exactly
    where it was, so nothing else would ever clear it.
    """
    flags = _stored(weekly_plan_id)
    kept = [f for f in flags if f.get("entry_id") != entry_id]
    if len(kept) == len(flags):
        return False
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET draft_flags_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(kept) if kept else "", weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()
    return True


# ---------- the producer ----------

def flags_for_kept_over_cap(nights: list[dict]) -> list[dict]:
    """
    Scenario 3, off the nights cap_enforce has already judged.

    A night qualifies when it is over its own cap AND the household spoke
    for it. Those two, in that order, are the whole rule — deliberately
    NOT "over cap and cap_enforce could not move it", which is a bigger
    set and would flag things nobody chose:

      * an unrequested dish too slow for its night is put right before the
        draft is shown (Emily's scenario 4), so it is normally gone by the
        time this runs — and when no repair could reach it, it is still
        not flagged: nobody chose it, and a line the household can do
        nothing about is noise. It stays in the morning report, which is
        where an app's own failure to place a dinner belongs.
      * a reheat, and the cook that feeds it — see the module docstring.
      * a night already cooked — the work is done, so there is nothing to
        offer and nothing to move.
      * a dish whose recipe records no minutes — an unjudgeable dish is
        not a violation anywhere else in this app either, and `over` is
        already False for it.

    `theirs` also implies `not movable` in cap_enforce's own terms, so a
    flag can never contradict what that pass did: every night named here
    is one it deliberately left standing. There is a test on exactly that,
    because the two are separate readings of one rule and nothing else
    would notice them drifting apart.
    """
    flags = []
    for night in nights:
        if not night.get("over"):
            continue
        derived = night.get("derived") or {}
        if not _theirs(derived):
            continue
        if night.get("reheat") or night.get("source"):
            continue
        if (night.get("cooked_status") or "") == "done":
            continue
        dish = (night.get("meal") or "").strip()
        minutes = night.get("minutes")
        if not dish or not minutes:
            continue
        flags.append({
            "kind": OVER_CAP_REQUEST,
            "text": over_cap_text(dish, minutes, night["date"]),
            "entry_id": night["id"],
            "date": night["date"],
            "slot": "dinner",
            "dish": dish,
            "fixes": fixes_for(night, nights),
        })
    return flags


def fixes_for(night: dict, nights: list[dict]) -> list[dict]:
    """
    The two offers for one flagged night, worked out against the week as it
    stands RIGHT NOW.

    THIS IS ASKED AT READ TIME AND NOT ONLY WHEN THE FLAG IS WRITTEN, and
    that is the whole reason it is a function rather than four lines inside
    the producer. `move_target` promises something about a pair of nights —
    both ends come out within their caps — and a promise made once at
    generation is not the promise the button makes when it is tapped. The
    week moves underneath a draft constantly: a chat swap, the Plan strip's
    own Swap, the Review "+", the other adult's phone, and — the shape that
    makes this unarguable — the flag's own SIBLING, since two flagged
    nights are routinely offered the same free Saturday and taking both
    offers hands the second flag's dish straight back onto a short night.
    Taking the app's own two fixes in a row could leave the week worse than
    it started, with no flag left saying so.

    So nothing about a fix is stored. `record` keeps the SNAG — which night,
    which dish, the sentence — and every offer is re-derived here from the
    live week, by the same two functions that chose it the first time. A
    stale target is then impossible by construction rather than by
    diligence, which is the same read-time rule `plan_flags` already
    applied to the flagged entry itself and should always have applied to
    the half that writes.
    """
    fixes = []
    prep_on = prep_target(night["date"], nights)
    if prep_on:
        fixes.append(prep_fix(prep_on))
    move_to = move_target(night, nights, {n["date"]: n.get("cap") for n in nights})
    if move_to:
        fixes.append(move_fix(move_to))
    return fixes


def _theirs(derived: dict) -> bool:
    # Lazy, and through meal_variety rather than a second reading of the
    # same keys: "the household spoke for this night" is one rule, and two
    # copies of it would let this file flag a night cap_enforce had
    # quietly moved (or leave one it had quietly kept).
    from . import meal_variety as _meal_variety
    return bool(_meal_variety.theirs(derived))
