"""
Permission to send a household's details to the AI (Loop Board "App Store:
ask permission before household details go to the AI, and say who it is",
2026-09-27).

Why this exists
---------------
Apple's App Review guideline 5.1.2(i) asks an app to say clearly when
personal data goes to a third-party AI and to get explicit permission
before it does. Pomona sends almost the whole household profile — names,
ages, allergies, what everyone eats, who's home for which meals, the
chat itself — to Anthropic's API on every plan, swap and chat turn. So:

  * one plain screen asks, once per household (onboarding for a new one,
    the next visit for one that already exists);
  * the answer is stored on the household row, with the date, the adult
    who gave it (when one is picked) and which wording they saw;
  * `require()` is called by agent.py's two call points — every Anthropic
    request in the app goes through `_create_with_retry` or
    `_stream_forced_tool_call` — and refuses to send anything for a
    household that hasn't said yes. That includes work nobody is watching
    (a chat turn's theme label, a background thread), because the check
    reads the household bound to the context, which every thread in this
    app copies from the request that started it.

Three states, stored in `households.ai_consent`:

  ''          never asked — the screen shows on the next visit
  'granted'   yes; AI calls go ahead
  'declined'  "Not now", or turned off in Preferences; AI calls are refused
              with REFUSAL_LINE, and the screen is not pushed again — the
              Preferences row is the way back

Why this module is not in `app/tools/`
--------------------------------------
Everything in `app/tools/` can be reached by the chat agent. Granting
permission must never be something the model can do on the strength of a
sentence typed into chat — so, like `households.py` and `invites.py`, the
writes live outside that package and only the web routes call them.
"""
from __future__ import annotations

import logging

from .db import get_conn
from .tools._shared import household_id

logger = logging.getLogger("home_manager")

GRANTED = "granted"
DECLINED = "declined"
UNANSWERED = ""

# Which wording the household said yes to. Bump this when the consent
# screen's substance changes (what's sent, who gets it, what for); the
# stored value says which version each household saw. A bump does NOT, on
# its own, re-ask anyone — a household that said yes keeps its yes. Whether
# a given change should re-ask is a decision for Emily at the time.
CONSENT_VERSION = "2026-09-27"

# What a refused AI call says. Shown as-is wherever the app already shows
# AssistantUnavailableError's line (the chat reply, a toast, the week's
# error frame): calm, plain, and the way out in the same breath.
REFUSAL_LINE = (
    "I need your okay before I share your household's details with Claude. "
    "You can give it in Preferences, under Sharing with Claude."
)


def state(hid: int | None = None) -> dict:
    """The household's answer: status ('' / 'granted' / 'declined'), when, which wording."""
    hid = household_id() if hid is None else int(hid)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT ai_consent, ai_consent_at, ai_consent_version FROM households WHERE id = ?",
            (hid,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"status": UNANSWERED, "at": None, "version": ""}
    return {
        "status": row["ai_consent"] or UNANSWERED,
        "at": row["ai_consent_at"],
        "version": row["ai_consent_version"] or "",
    }


def has_consent(hid: int | None = None) -> bool:
    return state(hid)["status"] == GRANTED


class ConsentRequired(RuntimeError):
    """Raised by `require` — agent.py re-raises it as AIConsentRequiredError."""


def require(label: str = "llm") -> None:
    """
    Refuse (raise ConsentRequired) unless the current household has said yes.

    Fails closed: if the answer can't be read at all, nothing is sent.
    """
    try:
        ok = has_consent()
    except Exception:
        logger.exception("Reading AI consent failed; refusing %s rather than sending", label)
        ok = False
    if not ok:
        logger.info("AI call %s refused: household %s hasn't allowed sharing with Claude", label, household_id())
        raise ConsentRequired(REFUSAL_LINE)


def record(status: str, member_id: int | None = None, hid: int | None = None) -> dict:
    """Store the household's answer, with the date and the wording version it was given against."""
    if status not in (GRANTED, DECLINED):
        raise ValueError("status must be 'granted' or 'declined'")
    hid = household_id() if hid is None else int(hid)
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE households SET ai_consent = ?, ai_consent_at = datetime('now'), "
            "ai_consent_version = ?, ai_consent_member_id = ? WHERE id = ?",
            (status, CONSENT_VERSION, member_id, hid),
        )
        conn.commit()
    finally:
        conn.close()
    return state(hid)
