"""
Chat voice — one line for what I did, one question at most, no builder
words. (Emily, 2026-09-15, from the H1 "Pomona, hold this" walk.)

The model is mocked under test, so the shape can't be proven end to end
here; what can be is (a) the prompt carries the rule, (b) the lint that
watches replies in production agrees with Emily about the three replies
from the walk, and (c) the agent logs drift without ever rewriting the
reply. The three sample replies below are the ones the card asks for —
the in-laws message, the dish-soap message and the shrimp message — each
written the way Pomona should have answered.
"""
import logging
import types

from app import agent
from app.db import get_conn
from app.tools import voice

# ---------------------------------------------------------------------------
# The three replies the card names, as they should read.
# ---------------------------------------------------------------------------
IN_LAWS_REPLY = (
    "Got it — nothing down for last night, so nothing to move. "
    "If there was a dish you'd planned, tell me which and I'll put it on another night."
)
DISH_SOAP_REPLY = "Dish soap is on your list."
SHRIMP_REPLY = (
    "I've put Garlic Butter Shrimp with rice on tonight — quick, and uses up the shrimp "
    "before it turns. Want a quick veggie side with it, or is that plenty?"
)

# ...and the two from the walk that had to change.
OBSERVED_IN_LAWS_REPLY = (
    "Noted — no cooking needed for last night, then. I don't have a plan entry on file for "
    "yesterday's dinner to reconcile against, so there's nothing to mark or move. If there "
    "was a dish you'd planned that you'd like pushed to another night this week, just tell "
    "me which one and I'll slot it in."
)
# (The walk's actual line began "I've moved the shrimp to fridge in your
# inventory" — that also trips verify_change_claim on a turn that wrote
# nothing, which is a different guard, so the version here keeps the word
# and drops the claim.)
OBSERVED_SHRIMP_LINE = "The shrimp's in the fridge in your inventory — want it tonight or tomorrow?"


# ---------------------------------------------------------------------------
# The lint agrees with Emily
# ---------------------------------------------------------------------------

def test_the_three_good_replies_pass():
    for reply in (IN_LAWS_REPLY, DISH_SOAP_REPLY, SHRIMP_REPLY):
        report = voice.lint_reply(reply)
        assert report.ok, f"{reply!r} flagged {report.flags}"
        assert report.questions <= 1
        assert report.prose_chars <= voice.MAX_PROSE_CHARS


def test_the_observed_in_laws_reply_is_flagged_for_every_builder_word():
    report = voice.lint_reply(OBSERVED_IN_LAWS_REPLY)
    assert not report.ok
    assert "plan entry" in report.banned
    assert "on file" in report.banned
    assert "reconcile" in report.banned
    assert "slot (verb)" in report.banned
    assert report.too_long, "three paragraphs where one would do"


def test_the_observed_shrimp_line_is_flagged_for_inventory():
    report = voice.lint_reply(OBSERVED_SHRIMP_LINE)
    assert report.banned == ["inventory"]


def test_two_questions_are_one_too_many():
    assert voice.lint_reply("Tonight or tomorrow? And rice or noodles?").flags == ["questions:2"]
    assert voice.lint_reply("Tonight or tomorrow?").ok


def test_noted_alone_is_flagged_but_noted_with_what_changed_is_not():
    assert voice.lint_reply("Noted.").flags == ["noted-alone"]
    assert voice.lint_reply("Noted. I'll keep it in mind.").noted_alone
    assert voice.lint_reply("Noted — fish is off for good, and I've taken it out of Wednesday.").ok
    assert voice.lint_reply("Noted — I'll start from that next week too.").ok


def test_slot_the_noun_is_fine_and_slot_the_verb_is_not():
    assert voice.lint_reply("Thursday's dinner is open.").ok
    assert not voice.lint_reply("Tell me which and I'll slot it in.").ok
    assert not voice.lint_reply("I slotted it into Thursday.").ok


def test_a_week_table_is_not_counted_as_long_prose():
    table = "Your week's here.\n" + "\n".join(
        f"| {day} | Oats | Soup | Chili con carne with rice and a green salad |"
        for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    )
    report = voice.lint_reply(table)
    assert report.ok, report.flags
    assert report.prose_chars == len("Your week's here.")


def test_the_summary_names_flags_and_never_the_words():
    report = voice.lint_reply(OBSERVED_IN_LAWS_REPLY)
    summary = report.summary()
    assert "banned:on file" in summary
    assert "yesterday's dinner" not in summary, "the log carries the flags, not what the household read"


# ---------------------------------------------------------------------------
# The prompt carries the rule
# ---------------------------------------------------------------------------

def test_the_system_prompt_has_one_reply_shape_block():
    prompt = agent.SYSTEM_PROMPT
    assert prompt.count("REPLY SHAPE") == 1
    block = prompt.split("REPLY SHAPE", 1)[1].split("\n\n", 1)[0]
    assert len(block.splitlines()) <= 15, "the block stays small so other branches merge cleanly"
    for phrase in ("one question at most", "inventory", "plan entry", "on file", "reconcile", "slot it in", "Noted"):
        assert phrase in block, phrase
    assert "in the fridge" in block, "the replacement, not just the ban"
    assert "Garlic Butter Shrimp" in block, "the reference reply from the walk"


def test_the_inventory_tool_tells_the_model_what_the_household_calls_it():
    tool = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "update_inventory")
    assert "never 'your inventory'" in tool["description"]
    assert "not 'moved'" in tool["description"]


# ---------------------------------------------------------------------------
# The agent logs drift and never rewrites
# ---------------------------------------------------------------------------

class _Usage:
    input_tokens = 0
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    output_tokens = 0


def _reply(text):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=_Usage(),
    )


def _stub_client(monkeypatch, text):
    fake = types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kwargs: _reply(text)))
    monkeypatch.setattr(agent, "_client", lambda: fake)


def _voice_events():
    conn = get_conn()
    rows = conn.execute("SELECT where_, detail FROM error_events WHERE kind = 'voice'").fetchall()
    conn.close()
    return [(r["where_"], r["detail"]) for r in rows]


def test_a_drifting_reply_is_logged_and_recorded_but_sent_as_written(signed_in, monkeypatch, caplog):
    _stub_client(monkeypatch, OBSERVED_SHRIMP_LINE)
    with caplog.at_level(logging.WARNING, logger="home_manager"):
        text, _ = agent.run_agent_turn([], "the shrimp's defrosted")
    assert text == OBSERVED_SHRIMP_LINE, "the lint watches; it never rewrites"
    assert "drifted from the voice rules" in caplog.text
    assert "banned:inventory" in caplog.text
    assert "in your inventory" not in caplog.text, "flags in the log, never the household's reply"
    assert _voice_events() == [("chat_reply", "banned:inventory")]


def test_a_reply_that_sounds_like_pomona_logs_nothing(signed_in, monkeypatch, caplog):
    _stub_client(monkeypatch, SHRIMP_REPLY)
    with caplog.at_level(logging.WARNING, logger="home_manager"):
        text, _ = agent.run_agent_turn([], "the shrimp's defrosted")
    assert text == SHRIMP_REPLY
    assert "drifted" not in caplog.text
    assert _voice_events() == []


# ---------------------------------------------------------------------------
# The morning report sees drift, and does not call it breakage
# ---------------------------------------------------------------------------

def test_voice_drift_reaches_the_morning_report_on_its_own_line(signed_in, monkeypatch, capsys):
    import observability_report

    _stub_client(monkeypatch, OBSERVED_SHRIMP_LINE)
    agent.run_agent_turn([], "the shrimp's defrosted")

    body = signed_in.get("/api/observability").json()
    assert body["errors"]["total"] == 0, "an off-voice reply is not an outage"
    assert "voice" not in body["errors"]["by_kind"]
    assert body["errors"]["recent"] == []
    assert body["errors"]["voice_drift"] == {"total": 1, "by_flags": {"banned:inventory": 1}}

    report, _ = observability_report.collect(days=1)
    observability_report._print_human(report, 1, "a local database")
    out = capsys.readouterr().out
    assert "Nothing broke." in out
    assert "Off-voice — 1 chat reply in the last 1d: banned:inventory x1" in out
