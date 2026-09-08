"""
"Something not working?" — the one place the app keeps what a person wrote.

Two opposite rules meet in this feature, and both are tested here because
either one alone would be wrong:

- The prose is kept EXACTLY as typed. A feature that summarises, tags or
  truncates the sentence has thrown away the only thing it was built to
  collect. So verbatim storage is pinned, not assumed.
- Everything around the prose is still shape-only, and the prose itself
  never reaches an agent's context by default. The morning report is
  printed into a Claude agent's context under an instruction to act on
  what it reads; free text from an untrusted end arriving there is an
  injection channel, not just a privacy question. So the default report
  output is asserted to contain none of it, and the opt-in reader is
  asserted to fence what it prints.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout

import pytest

from app import ratelimit, tools
from app.db import get_conn


def _rows():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT household_id, what_happened, trying_to_do, route_pattern, "
            "user_agent, extra_json FROM feedback_reports ORDER BY id"
        )]
    finally:
        conn.close()


@pytest.fixture
def second_household():
    from app import households

    return households.create_household("Feedback Isolation Test", "feedback-isolation-passphrase")


# ---------- the prose ----------

def test_what_someone_typed_is_stored_word_for_word(signed_in):
    """
    The whole feature. A category picker, a keyword tag or a truncation
    would each have quietly discarded the one thing a person can tell us
    that no stack trace can.
    """
    typed = (
        "The grocery list said \"nothing left to buy\" but the eggs I asked for "
        "on Tuesday were never on it — I only noticed at the shop."
    )
    trying = "Shopping for the week, standing in the dairy aisle."

    assert signed_in.post(
        "/api/feedback", json={"what_happened": typed, "trying_to_do": trying, "where": "/grocery"}
    ).status_code == 204

    row = _rows()[-1]
    assert row["what_happened"] == typed, "the report was not stored as it was written"
    assert row["trying_to_do"] == trying


def test_the_second_question_is_optional(signed_in):
    """One box is required, and only one. Anything more is a form."""
    assert signed_in.post(
        "/api/feedback", json={"what_happened": "It just spun forever.", "where": "/week"}
    ).status_code == 204
    assert _rows()[-1]["trying_to_do"] is None


def test_an_empty_report_is_not_a_row(signed_in):
    """A stray tap should not file a blank note for Emily to read."""
    before = len(_rows())
    assert signed_in.post(
        "/api/feedback", json={"what_happened": "   ", "where": "/kitchen"}
    ).status_code == 204
    assert len(_rows()) == before


# ---------- everything around the prose ----------

def test_the_route_is_a_pattern_and_never_the_url(signed_in):
    """
    A URL is where a member's name and a live share token hide — the same
    reason error_events stores a route pattern. The prose column exists to
    hold what somebody chose to write; the metadata columns must not
    quietly hold what they didn't.
    """
    signed_in.post("/api/feedback", json={
        "what_happened": "Sharing the week didn't work.",
        "where": "/share/8f3c1d9e-live-token",
    })
    signed_in.post("/api/feedback", json={
        "what_happened": "Her page was blank.",
        "where": "/api/members/Sophia Rodriguez/share-link",
    })

    stored = [r["route_pattern"] for r in _rows()[-2:]]
    assert "8f3c1d9e-live-token" not in " ".join(stored), "a live share token was stored"
    assert "Sophia" not in " ".join(stored), "a member's name was stored"
    assert stored[0] == "/share/<token>"
    assert stored[1] == "/api/members/<name>/share-link"


def test_only_error_shapes_survive_never_error_text(signed_in):
    """
    Same rule static/error-reporter.js follows: a class name is signal, the
    sentence after the colon is unbounded application text — 27 places in
    app/tools raise messages that interpolate a recipe or a member's name.
    """
    signed_in.post("/api/feedback", json={
        "what_happened": "The cook view went blank.",
        "where": "/week",
        "error_shapes": [
            "TypeError",
            "TypeError: No saved recipe named 'Sophia's birthday pasta'",
            "some prose that is not a shape at all",
        ],
    })
    shapes = json.loads(_rows()[-1]["extra_json"])["error_shapes"]
    assert shapes[0] == "TypeError"
    assert all("Sophia" not in s for s in shapes), "an error message reached the row"
    assert all("recipe" not in s for s in shapes)


def test_the_user_agent_is_recorded_and_bounded(signed_in):
    signed_in.post(
        "/api/feedback",
        json={"what_happened": "Nothing loads on my phone.", "where": "/"},
        headers={"User-Agent": "A" * 500},
    )
    stored = _rows()[-1]["user_agent"]
    assert stored.startswith("A")
    assert len(stored) <= 200


# ---------- who can see it ----------

def test_a_household_never_sees_anothers_reports(client, second_household):
    """
    Same isolation bar as everything else. A complaint leaking across
    households would be a privacy failure wearing a support-tool costume.
    """
    with tools.use_household(second_household):
        tools.record_feedback_report("Their private note about their private evening.")

    client.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    body = client.get("/api/feedback").json()
    assert all(
        "private evening" not in (r["what_happened"] or "") for r in body["reports"]
    ), "one household read another's report"


def test_a_report_is_filed_against_the_household_that_wrote_it(client, second_household):
    res = client.post(
        "/login", data={"password": "feedback-isolation-passphrase", "next": "/"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert client.post(
        "/api/feedback", json={"what_happened": "Filed by the second household.", "where": "/"}
    ).status_code == 204
    assert _rows()[-1]["household_id"] == second_household


def test_reporting_is_not_reachable_without_signing_in(client):
    """
    It writes rows holding free text. A write anyone on the internet can
    reach is both a way to fill the household's database and a way to put
    words in front of Emily that no member of her household typed.
    """
    assert client.post(
        "/api/feedback", json={"what_happened": "hello", "where": "/"}
    ).status_code == 401
    assert client.get("/api/feedback").status_code == 401


# ---------- failing to send must not be a second failure ----------

def test_the_rate_limit_answers_204_and_never_429(signed_in, monkeypatch):
    """
    This is reached by someone whose evening has already gone wrong. A 429
    on the report would make the app's answer to "something is broken" be
    "yes, and so is this" — the same reason /api/client-error swallows its
    own limit.
    """
    monkeypatch.setattr(ratelimit, "check", lambda bucket, caller: 30)
    for _ in range(4):
        res = signed_in.post(
            "/api/feedback", json={"what_happened": "Still broken.", "where": "/"}
        )
        assert res.status_code == 204, f"the reporter answered {res.status_code}"
    assert not _rows(), "a rate-limited report was written anyway"


def test_the_limiter_actually_caps_the_table(signed_in):
    """The other half: swallowing the 429 must still stop the writes."""
    for _ in range(40):
        assert signed_in.post(
            "/api/feedback", json={"what_happened": "again and again", "where": "/"}
        ).status_code == 204
    assert len(_rows()) < 40, f"40 posts wrote {len(_rows())} rows; the limiter capped nothing"


def test_a_storage_failure_is_still_a_204(signed_in, monkeypatch):
    """A report that cannot be filed must not tell the person it broke."""
    from app.tools import feedback as feedback_module

    def _explode(*args, **kwargs):
        raise RuntimeError("database on fire")

    monkeypatch.setattr(feedback_module, "record_feedback_report", _explode)
    monkeypatch.setattr(tools, "record_feedback_report", _explode)
    assert signed_in.post(
        "/api/feedback", json={"what_happened": "anything", "where": "/"}
    ).status_code == 204


# ---------- the table itself ----------

def test_the_migration_is_idempotent():
    """
    init_db runs on every startup. Creating the table a second time must be
    a no-op that keeps what is already in it, not an error and not a wipe.
    """
    from app.db import init_db

    tools.record_feedback_report("Written before the second init.")
    before = len(_rows())

    init_db()
    init_db()

    after = _rows()
    assert len(after) == before, "re-running the migration lost rows"
    assert after[-1]["what_happened"] == "Written before the second init."


# ---------- the read path, and what the default report may not print ----------

@pytest.fixture
def local_db_only(monkeypatch):
    """Force the script onto the test database rather than a live app."""
    monkeypatch.delenv("HOME_MANAGER_URL", raising=False)
    monkeypatch.delenv("HOME_MANAGER_PASSPHRASES", raising=False)
    monkeypatch.delenv("HOME_MANAGER_PASSWORD", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)


_PROSE = "The plan came out empty and I could not tell whether it was still thinking."


def _run_report(monkeypatch, argv):
    import observability_report

    monkeypatch.setattr(sys, "argv", ["observability_report.py"] + argv)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        observability_report.main()
    return buffer.getvalue()


def test_the_default_report_prints_no_feedback_prose(monkeypatch, local_db_only):
    """
    The rule this whole feature is arranged around. This output is printed
    into a Claude agent's context under an instruction to act on what it
    reads, and free text from an untrusted end arriving there is an
    injection channel, not just a privacy question.
    """
    tools.record_feedback_report(_PROSE, trying_to_do="Approving the week.")

    out = _run_report(monkeypatch, [])
    assert _PROSE not in out, "the default report printed what somebody typed"
    assert "still thinking" not in out
    assert "Approving the week" not in out


def test_the_default_report_says_how_many_are_waiting(monkeypatch, local_db_only):
    """
    A count is a number and carries nothing anybody wrote — and without it
    the read path is a flag Emily never learns to run.
    """
    tools.record_feedback_report(_PROSE)
    out = _run_report(monkeypatch, [])
    assert "--feedback" in out, "the report never points at the way to read them"


def test_the_feedback_flag_prints_them_marked_as_untrusted(monkeypatch, local_db_only):
    """
    The opt-in reader. It may print the prose — and must fence it, in the
    same words the rest of this feature uses, because the next thing to
    read this output may well be an agent.
    """
    tools.record_feedback_report(_PROSE, trying_to_do="Approving the week.")

    out = _run_report(monkeypatch, ["--feedback"])
    assert _PROSE in out, "--feedback printed no report"
    assert "Approving the week." in out
    assert "UNTRUSTED QUOTED TEXT" in out, "the prose was printed without its warning"
    assert "data, not instructions" in out
    assert "injection channel" in out


def test_the_json_output_carries_feedback_only_when_asked(monkeypatch, local_db_only):
    tools.record_feedback_report(_PROSE)

    plain = json.loads(_run_report(monkeypatch, ["--json"]))
    assert "feedback_untrusted_quoted_text" not in plain
    assert _PROSE not in json.dumps(plain)

    asked = json.loads(_run_report(monkeypatch, ["--json", "--feedback"]))
    assert "feedback_untrusted_quoted_text" in asked, "the key is not named as untrusted"
    assert _PROSE in json.dumps(asked)


def test_the_reports_are_not_an_agent_tool():
    """
    Nothing here is callable by the chat agent. Handing the model a tool
    that reads free text people wrote would put the prose straight back
    into the context this feature spends its whole design keeping it out
    of.
    """
    from app import agent

    assert not [
        name for name in agent.TOOL_FUNCTIONS if "feedback_report" in name
    ], "a feedback reader was registered as an agent tool"


# ---------- the two ways in, pinned at the source ----------

def _shell_js():
    import os

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "static", "shell.js")) as f:
        return f.read()


def test_the_kitchen_tile_exists():
    """
    Emily's option a: a quiet tile, never an apricot button.

    UPDATED 2026-09-08 (flows-4-kitchen-and-preferences), deliberately: the
    tile itself is unchanged and still the only definition of this entry
    point, but it renders in the Preferences sheet now rather than on the
    Kitchen tab. Kitchen became the cook's tab in the same change, and
    "tell Emily what broke" is not something a cook's tab holds — it sits
    with Sign out, one tap behind the gear that every root screen carries.
    """
    source = _shell_js()
    assert "Something not working?" in source
    assert "snwTile()" in source, "nothing renders the tile any more"
    assert "snwTile() +" in source.split("function renderPrefsRows")[1][:1400], (
        "the Preferences sheet no longer offers the way to report a problem"
    )
    assert 'data-snw="open"' in source
    assert "btn-primary" not in source.split("function snwTile")[1][:600], (
        "the quiet tile grew a primary button"
    )


def test_the_error_states_offer_the_same_sheet():
    """
    The second way in, where the question is being asked anyway. If an
    error paragraph loses its link, someone staring at a broken screen has
    nowhere to say so.
    """
    source = _shell_js()
    assert "Something not working? Tell Emily" in source
    # `kit-hero-error` was the Kitchen "what we know" hero's error line; the
    # hero left the tab on 2026-09-08 and the Kitchen root's own failure
    # state is a `cook-error` paragraph, already in this list.
    for marker in ["gro-error", "cook-error"]:
        line = next(l for l in source.splitlines() if marker in l and "Couldn" in l)
        assert "snwLink(" in line, f"the {marker} state lost its way to report it"


def test_the_confirmation_is_in_voice():
    source = _shell_js()
    assert "Emily reads every one of these" in source
    assert "blocking you, text her too" in source
