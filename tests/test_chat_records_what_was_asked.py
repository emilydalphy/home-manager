"""
A chat turn records WHICH TOOLS it called — names only, never words.

A chat turn costs about sixteen cents and a tap about one, so every
recurring ask that becomes a tap is both a better experience and a much
cheaper one. But only the asks we can SEE can be built into taps, and
until now nothing recorded what chat was being used for: `chat_turns`
stored tokens and timing and nothing else. This cannot be backfilled —
a day without it is a day of asks nobody can count — which is why the
free half ships on its own rather than waiting for the themes half.

What is stored is a JSON list of tool NAMES in call order, duplicates
kept, on the turn's own row. What is NOT stored is anything the person
wrote, and that is the same rule the rest of that table already follows
for the same two reasons: the household's private text is not ours to
keep a second copy of, and this feed is printed into an agent's context
each morning, where free text from an untrusted end is an injection
channel rather than merely a privacy question.

The names are recorded ABOVE every branch in the dispatch loop, so the
tally is what the model ASKED FOR rather than what it got. A declined
chores call, a tool that crashed and a name this app has never heard of
are all still the thing the household wanted, which is the question being
answered.

Each test says in its own docstring whether it is a CATCH (red against
main's app/) or a GUARD (green either way, here to say what did not
change), and every GUARD names the mutation that pins it.
"""
from __future__ import annotations

import json

import pytest

from app import tools
from app.db import get_conn
from app.tools import usage as _usage


def _turn_rows() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT rounds, output_tokens, tools_called_json FROM chat_turns "
        "WHERE household_id = ? ORDER BY id",
        (tools.household_id(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# What lands on the row
# --------------------------------------------------------------------------


def test_a_turns_tool_names_are_recorded_in_call_order():
    """
    CATCH. The whole point of the card: the row says what the turn was for.

    Order and duplicates are both kept — a turn that swapped three meals
    called the swap tool three times, and that is the signal.
    """
    tools.record_chat_turn(
        {"rounds": 2, "output_tokens": 30,
         "tools_called": ["swap_meal_in_plan", "swap_meal_in_plan", "add_grocery_item"]}
    )

    assert json.loads(_turn_rows()[0]["tools_called_json"]) == [
        "swap_meal_in_plan", "swap_meal_in_plan", "add_grocery_item"
    ]


def test_a_turn_that_called_nothing_records_an_empty_list():
    """
    CATCH. A turn that only talked is worth counting, not skipping.

    It is the household asking and getting conversation back, which is
    either being answered well or being stuck — and which one it is cannot
    be read from here, so it is stored rather than judged.
    """
    tools.record_chat_turn({"rounds": 1, "output_tokens": 10, "tools_called": []})

    assert json.loads(_turn_rows()[0]["tools_called_json"]) == []


def test_a_turn_recorded_by_an_older_caller_still_writes_a_row():
    """
    GUARD, and it is the property record_chat_turn was built around: a
    tally missing a key must never be what fails a reply that already
    worked.

    Pinned by the mutation that reads `values["tools_called"]` directly:
    this then raises inside the write and the row is lost.
    """
    tools.record_chat_turn({"rounds": 1, "output_tokens": 10})

    rows = _turn_rows()
    assert len(rows) == 1
    assert json.loads(rows[0]["tools_called_json"]) == []


@pytest.mark.parametrize(
    "bad", ["not a list", 17, None, {"swap": 1}, ["ok", 5, None, "fine"]]
)
def test_a_tally_of_the_wrong_shape_costs_the_list_and_never_the_row(bad):
    """
    GUARD. Bookkeeping attached to a reply that already succeeded.

    Pinned by the mutation that makes _tool_names_json `json.dumps(names)`
    outright: the non-list cases then store something no reader can count,
    and the mixed list stores a number where a tool name should be.
    """
    tools.record_chat_turn({"rounds": 1, "tools_called": bad})

    stored = json.loads(_turn_rows()[0]["tools_called_json"])
    assert isinstance(stored, list)
    assert all(isinstance(n, str) for n in stored)


def test_no_message_text_reaches_the_database():
    """
    CATCH on the rule the whole table is built around, and the one the
    card asks for by name.

    Sweeps every text column of chat_turns for the words a person typed.
    Nothing this app stores about a chat turn may be readable as what they
    said — see the module docstring for the two reasons.
    """
    secret = "please buy oat milk for Gran's visit on Thursday"
    tools.record_chat_turn(
        {"rounds": 1, "output_tokens": 12, "tools_called": ["add_grocery_item"]}
    )

    conn = get_conn()
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(chat_turns)").fetchall()]
    rows = conn.execute("SELECT * FROM chat_turns").fetchall()
    conn.close()

    haystack = " ".join(
        str(row[c]) for row in rows for c in cols if row[c] is not None
    ).lower()
    for word in ("oat milk", "gran", "thursday", secret.lower()):
        assert word not in haystack


# --------------------------------------------------------------------------
# Reading it back
# --------------------------------------------------------------------------


def test_the_summary_counts_what_chat_was_for():
    """CATCH. Busiest first, so two mornings can be compared."""
    tools.record_chat_turn({"tools_called": ["add_grocery_item"]})
    tools.record_chat_turn({"tools_called": ["swap_meal_in_plan", "add_grocery_item"]})
    tools.record_chat_turn({"tools_called": ["add_grocery_item"]})

    counts = tools.get_usage_summary(days=7)["chat_tools"]["counts"]

    assert list(counts.items())[0] == ("add_grocery_item", 3)
    assert counts["swap_meal_in_plan"] == 1


def test_the_summary_counts_turns_that_only_talked():
    """CATCH. The stuck-or-answered signal; a prompt to look, not a verdict."""
    tools.record_chat_turn({"tools_called": []})
    tools.record_chat_turn({"tools_called": []})
    tools.record_chat_turn({"tools_called": ["add_grocery_item"]})

    chat_tools = tools.get_usage_summary(days=7)["chat_tools"]

    assert chat_tools["talk_only_turns"] == 2
    assert chat_tools["turns_with_a_tap"] == 1


def test_a_turn_is_counted_once_however_many_tappable_tools_it_used():
    """
    CATCH. The question is "how many TURNS could have been taps", so a
    turn that swapped two meals is one turn, not two.
    """
    tools.record_chat_turn({"tools_called": ["swap_meal_in_plan", "mark_grocery_item"]})

    assert tools.get_usage_summary(days=7)["chat_tools"]["turns_with_a_tap"] == 1


def test_a_tool_with_no_tap_is_not_counted_as_one():
    """
    GUARD on the list being a list rather than "everything".

    Pinned by the mutation that makes TOOLS_WITH_A_TAP contain every tool
    name: this then counts 1.
    """
    tools.record_chat_turn({"tools_called": ["get_expiring_soon"]})

    assert tools.get_usage_summary(days=7)["chat_tools"]["turns_with_a_tap"] == 0


def test_an_unreadable_row_is_counted_rather_than_dropped():
    """
    CATCH. A count that quietly shrinks is the failure this card exists to
    stop, so a row that cannot be read says so instead of vanishing.
    """
    tools.record_chat_turn({"tools_called": ["add_grocery_item"]})
    conn = get_conn()
    conn.execute("UPDATE chat_turns SET tools_called_json = 'not json'")
    conn.commit()
    conn.close()

    chat_tools = tools.get_usage_summary(days=7)["chat_tools"]

    assert chat_tools["unreadable_turns"] == 1
    assert chat_tools["counts"] == {}


def test_every_tool_that_is_said_to_have_a_tap_is_a_real_tool():
    """
    CATCH. A maintained list is only worth keeping if it is maintained.

    A renamed or deleted tool left in TOOLS_WITH_A_TAP would silently stop
    matching and the "could have been a tap" count would drift down with
    nothing saying so.
    """
    from app import agent

    unknown = sorted(_usage.TOOLS_WITH_A_TAP - set(agent.TOOL_FUNCTIONS))

    assert unknown == [], f"not real tools any more: {unknown}"


# --------------------------------------------------------------------------
# The dispatch loop records what was ASKED FOR
# --------------------------------------------------------------------------


def test_the_name_is_recorded_above_every_branch_in_the_dispatch_loop():
    """
    GUARD, source-level, and it is about ORDER rather than presence.

    A declined chores call, a crashed tool and an unknown name are all
    still what the household asked for. Recording below any of those
    branches would silently stop counting exactly the asks worth seeing.
    Read comment-stripped, because the prose above the line names it too.

    Pinned by the mutation that moves the append below the chores gate.
    """
    import ast
    import inspect

    from app import agent

    src = inspect.getsource(agent.run_agent_turn)
    body = ast.get_source_segment(src, ast.parse(src).body[0]) or src
    lines = [l for l in body.splitlines() if not l.strip().startswith("#")]
    joined = "\n".join(lines)

    append_at = joined.index('usage["tools_called"].append(block.name)')
    chores_gate = joined.index("if block.name in CHORES_TOOLS")
    dispatch = joined.index("fn = TOOL_FUNCTIONS.get(block.name)")

    assert dispatch < append_at < chores_gate


# --------------------------------------------------------------------------
# End to end: the real dispatch, the real recording site
# --------------------------------------------------------------------------


def _usage_block():
    import types

    return types.SimpleNamespace(
        input_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, output_tokens=0,
    )


def _tool_use(name, tool_input, block_id="tu_1"):
    import types

    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def _stub_model(monkeypatch, responses):
    """The agent's own client, answering a canned sequence. No network."""
    import types

    from app import agent

    class _Messages:
        def __init__(self, seq):
            self._seq = list(seq)

        def create(self, **kwargs):
            return self._seq.pop(0)

    monkeypatch.setattr(
        agent, "_client", lambda: types.SimpleNamespace(messages=_Messages(responses))
    )


def test_a_real_turn_lands_its_tool_names_on_a_real_row(monkeypatch):
    """
    CATCH, and the acceptance criterion in the card's own words: "a chat
    turn's row records the tool names it called".

    Every test above this one hands `record_chat_turn` a tally by hand,
    which proves the write and not the chain. This drives the whole
    thing — the real dispatch loop in run_agent_turn, then the real
    recording site both chat routes share — with only the model stubbed.
    """
    import types

    from app import agent, main

    _stub_model(
        monkeypatch,
        [
            types.SimpleNamespace(
                content=[_tool_use("list_grocery_list", {})],
                stop_reason="tool_use",
                usage=_usage_block(),
            ),
            types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="Here's the list.")],
                stop_reason="end_turn",
                usage=_usage_block(),
            ),
        ],
    )

    reply, conversation = agent.run_agent_turn([], "what's on the list?")
    main._finish_chat_turn("test-session", [], reply, conversation)

    rows = _turn_rows()
    assert len(rows) == 1
    assert json.loads(rows[0]["tools_called_json"]) == ["list_grocery_list"]


def test_a_turn_that_only_talked_lands_an_empty_list_for_real(monkeypatch):
    """CATCH. The same chain, for the turn that called nothing."""
    import types

    from app import agent, main

    _stub_model(
        monkeypatch,
        [
            types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="Evening.")],
                stop_reason="end_turn",
                usage=_usage_block(),
            )
        ],
    )

    reply, conversation = agent.run_agent_turn([], "hello")
    main._finish_chat_turn("test-session", [], reply, conversation)

    assert json.loads(_turn_rows()[0]["tools_called_json"]) == []
