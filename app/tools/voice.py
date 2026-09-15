"""
Does a chat reply sound like the rest of Pomona?  (Emily, 2026-09-15.)

The shape a reply is held to — what I did in one line, one question at
most, stop — lives in `agent.SYSTEM_PROMPT`'s REPLY SHAPE block. This is
the check on the other side of the model: a small, pure lint that says
which of those rules a reply broke, so drift is visible instead of
noticed by Emily on her phone.

It never rewrites anything. The model's words go to the household as
written (see `agent.verify_change_claim` for the one exception, which is
about truth, not tone); the agent only logs what this flags.

Built from the 2026-09-15 H1 walk, where the reply to "we ended up at the
in-laws last night" read: "Noted — no cooking needed for last night,
then. I don't have a plan entry on file for yesterday's dinner to
reconcile against, so there's nothing to mark or move. If there was a
dish you'd planned that you'd like pushed to another night this week,
just tell me which one and I'll slot it in."  Builder words, a summary of
what the app did not do, and the work handed back. Each of the rules
below catches one part of that.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Words the household never sees anywhere else in the app, so they read as
# a different product talking. Each pattern is matched case-insensitively
# and reported under its label. "slot" is only a builder word as a verb
# ("slot it in") — the noun is left alone. "inventory" is a background
# feature by Emily's call: the chat says "in the fridge" / "you've got".
BANNED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("plan entry", re.compile(r"\bplan entr(?:y|ies)\b", re.I)),
    ("entry", re.compile(r"\bentr(?:y|ies)\b", re.I)),
    ("on file", re.compile(r"\bon file\b", re.I)),
    ("reconcile", re.compile(r"\breconcil\w*", re.I)),
    ("inventory", re.compile(r"\binventor(?:y|ies)\b", re.I)),
    (
        "slot (verb)",
        re.compile(
            r"(?:\b(?:i'?ll|i'?d|i can|can i|shall i|let me|to|will|and)\s+slot\b"
            r"|\bslot(?:ted|ting)\b"
            r"|\bslot\s+(?:it|that|this|them|him|her|in)\b)",
            re.I,
        ),
    ),
)

# "Noted." as a whole sentence — the reply that sounds like it kept the
# thing when it kept nothing. "Noted — fish is off for good, and I've
# taken it out of Wednesday" is fine: it says what changed.
_NOTED_ALONE = re.compile(r"(?:^|[.!?]\s+)Noted[.!]?(?=\s*$|\s+[A-Z\"“])", re.M)

# The length a plain reply stays under — two or three short sentences. The
# reference reply from the walk ("I've put Garlic Butter Shrimp with rice
# on tonight — quick, and uses up the shrimp before it turns. Want a quick
# veggie side with it, or is that plenty?") is 152 characters; the in-laws
# reply above is 291. Measured on prose only — a week-at-a-glance table or
# a list of three things to tap is allowed to be as long as it is, so
# those lines are set aside first.
MAX_PROSE_CHARS = 240
MAX_QUESTIONS = 1

_STRUCTURE_LINE = re.compile(r"^\s*(?:\||[-*•]\s|\d+[.)]\s|#)")


@dataclass
class VoiceReport:
    """What one reply broke. `flags` is empty when it sounds like Pomona."""

    banned: list[str] = field(default_factory=list)
    questions: int = 0
    prose_chars: int = 0
    noted_alone: bool = False

    @property
    def too_long(self) -> bool:
        return self.prose_chars > MAX_PROSE_CHARS

    @property
    def too_many_questions(self) -> bool:
        return self.questions > MAX_QUESTIONS

    @property
    def flags(self) -> list[str]:
        out = [f"banned:{word}" for word in self.banned]
        if self.too_many_questions:
            out.append(f"questions:{self.questions}")
        if self.too_long:
            out.append(f"long:{self.prose_chars}")
        if self.noted_alone:
            out.append("noted-alone")
        return out

    @property
    def ok(self) -> bool:
        return not self.flags

    def summary(self) -> str:
        """One short line for a log — the flags only, never the reply."""
        return ", ".join(self.flags)


def _prose_only(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _STRUCTURE_LINE.match(line))


def lint_reply(text: str) -> VoiceReport:
    """
    Check one assistant reply against the chat voice rules. Pure: reads
    the text, returns a report, touches nothing.
    """
    text = text or ""
    report = VoiceReport()
    for label, pattern in BANNED_PATTERNS:
        if pattern.search(text):
            report.banned.append(label)
    # "plan entry" already names the phrase; don't report its "entry" twice.
    if "plan entry" in report.banned and "entry" in report.banned:
        report.banned.remove("entry")
    report.questions = text.count("?")
    report.prose_chars = len(_prose_only(text).strip())
    report.noted_alone = bool(_NOTED_ALONE.search(text))
    return report
