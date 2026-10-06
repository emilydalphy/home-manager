"""
Walkthrough 2026-10-06: chat's "we don't eat pork" lands in Won't eat (and
skips the Pork chip), not in "Anything else for the household"; setup's
last-screen note is shown in Settings -> Who's here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.tools import memory

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("text,expected", [
    ("We don't eat pork", ["pork"]),
    ("we don’t eat pork.", ["pork"]),
    ("We never eat shrimp or pork", ["shrimp", "pork"]),
    ("No shellfish in this house", ["shellfish"]),
    ("Nobody here eats beef", ["beef"]),
    ("My partner doesn't eat shellfish", []),
    ("Sam works late on Tuesdays", []),
])
def test_household_wide_rule_is_recognised(text, expected):
    assert memory.household_wont_eat_items("people", text) == expected
    assert memory.household_wont_eat_items("taste", text) == []


def test_chat_routes_to_wont_eat_and_skips_the_chip(tmp_path, monkeypatch):
    calls = {}
    monkeypatch.setattr(memory._preferences, "add_food_dislikes",
                        lambda items: calls.setdefault("dislikes", items) and {"dislikes": list(items)})
    monkeypatch.setattr(memory._preferences, "set_household_meal_preferences",
                        lambda **kw: calls.setdefault("prefs", kw))
    monkeypatch.setattr(memory, "add_fact", lambda *a, **k: calls.setdefault("fact", True))
    out = memory.add_fact_from_chat("people", "We don't eat pork", hard=True)
    assert out["routed_to"] == "Won't eat" and out["dislikes"] == ["pork"]
    assert calls["prefs"]["protein_preferences"] == {"pork": 1}
    assert "fact" not in calls, "it must not also land under Anything else"


def test_chat_leaves_other_facts_alone(monkeypatch):
    seen = []
    monkeypatch.setattr(memory, "add_fact", lambda *a, **k: seen.append(a) or {"added": True})
    memory.add_fact_from_chat("people", "My partner doesn't eat shellfish")
    assert seen, "a person's rule is still a fact (set_member_dietary_restrictions is the right tool)"


def test_the_agent_calls_the_routing_version_and_the_screen_keeps_its_own():
    agent_src = (REPO / "app" / "agent.py").read_text(encoding="utf-8")
    assert '"add_fact": tools.add_fact_from_chat,' in agent_src
    assert "tools.add_fact(req.category" in (REPO / "app" / "main.py").read_text(encoding="utf-8")


def test_setup_note_is_shown_in_whos_here():
    assert "wwkNote('From setup: ' + String(mem.notes).trim())" in SHELL_JS
