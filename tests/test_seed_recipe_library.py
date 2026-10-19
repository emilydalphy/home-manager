"""
seed_recipe_library.py (Loop Board "Shared Recipe Library: the 500-dish
starter list", Emily 2026-10-18): researches the starter list into the
library through recipe_library.file_entry(origin="seed"), in batches,
resumable, skipping what the library already has — and refuses to do
anything without --i-mean-it, because it spends API money and is for after
Emily approves the samples.

Every model call is mocked; the database is the suite's throwaway one.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import seed_recipe_library as seed
from app import agent
from app.db import get_conn
from app.tools import recipe_library as lib

ROOT = Path(__file__).resolve().parent.parent
DISHES = seed.load_dishes()


@pytest.fixture
def calls(monkeypatch):
    """Mocked research and writer. Counts each, keyed by dish."""
    seen = {"research": [], "write": []}
    guard = threading.Lock()
    fail = set()

    def _research(name, cuisine="", dish_note="", **k):
        with guard:
            seen["research"].append(name)
        slug = name.lower().replace(" ", "-")
        return {
            "sources": [
                {"name": "Cook B", "title": name, "url": f"https://cook-b.example/{slug}", "rating": 4.6,
                 "rating_count": 300, "read": True},
                {"name": "Cook A", "title": name, "url": f"https://cook-a.example/{slug}", "rating": 4.9,
                 "rating_count": 1200, "read": True},
            ],
            "method_notes": f"How {name} is made.",
            "used": {"web_search": 1, "web_fetch": 2},
            "input_tokens": 9000,
        }

    def _write(spec):
        with guard:
            seen["write"].append(spec["name"])
            seen.setdefault("specs", []).append(spec)
        if spec["name"] in fail:
            raise RuntimeError("writer fell over")
        return {
            "ingredients": [{"item": "Rice", "qty": "1 cup", "category": "pantry"}],
            "instructions": ["Simmer the 1 cup rice in 2 cups water 18 minutes."],
            "default_servings": 4, "prep_time_minutes": 5, "cook_time_minutes": 20,
        }

    monkeypatch.setattr(agent, "research_dish_llm", _research)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _write)
    # The amounts repair is a model call too; the mocked recipe adds up, but
    # if it ever didn't, nothing may leave the machine.
    monkeypatch.setattr(agent, "_repair_recipe_amounts_llm", lambda *a, **k: {})
    seen["fail"] = fail
    return seen


def _entries():
    conn = get_conn()
    try:
        return {r["dish_name"]: dict(r) for r in conn.execute("SELECT * FROM recipe_library").fetchall()}
    finally:
        conn.close()


def _count(table):
    conn = get_conn()
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_refuses_without_i_mean_it_and_touches_nothing(tmp_path):
    db = tmp_path / "never.db"
    # No key, and an API address that goes nowhere: if the refusal ever
    # broke, this test must fail without a request leaving the machine.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.update(DB_PATH=str(db), DISABLE_BACKUPS="1", ANTHROPIC_BASE_URL="http://127.0.0.1:9")
    out = subprocess.run([sys.executable, "seed_recipe_library.py"], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 2
    assert "spends Anthropic API money" in out.stderr and "approves the six samples" in out.stderr
    assert "--i-mean-it" in out.stderr
    assert not db.exists()


def test_seeds_in_batches_through_the_one_write_path(calls):
    research_rows, recipes = _count("dish_research"), _count("recipes")
    results = seed.run(DISHES[:5], batch_size=2, log=lambda *_: None)

    assert [r["outcome"] for r in results] == ["filed"] * 5
    entries = _entries()
    assert set(entries) == {d["dish_name"] for d in DISHES[:5]}
    for d in DISHES[:5]:
        e = lib.base_recipe(d["dish_name"])
        assert e["origin"] == "seed" and e["kind"] == "base"
        assert e["cuisine"] == d["cuisine"] and e["main_protein"] == d["main_protein"]
        assert e["instructions"] and e["ingredients"]
        # Lead by the app's own rule: Cook A's 4.9 beats Cook B's 4.6.
        assert [s["name"] for s in e["sources"]] == ["Cook A", "Cook B"] and e["sources"][0]["lead"]
    # Each dish researched once and written once, for a table of 4 with no
    # household in it.
    assert sorted(calls["research"]) == sorted(calls["write"]) == sorted(d["dish_name"] for d in DISHES[:5])
    assert all(s["serves"] == 4 and s["must_not_contain"] == [] for s in calls["specs"])
    # No household's own tables were touched.
    assert (_count("dish_research"), _count("recipes")) == (research_rows, recipes)


def test_rerun_skips_what_is_there_and_picks_up_what_failed(calls):
    calls["fail"].add(DISHES[1]["dish_name"])
    first = seed.run(DISHES[:3], batch_size=3, log=lambda *_: None)
    assert [r["outcome"] for r in first] == ["filed", "error", "filed"]
    # Its research was filed before the writer fell over, so the retry
    # pays only for the writer.
    assert lib.research_for(DISHES[1]["dish_name"]) and not lib.base_recipe(DISHES[1]["dish_name"])

    calls["fail"].clear()
    calls["research"].clear()
    calls["write"].clear()
    second = seed.run(DISHES[:3], batch_size=3, log=lambda *_: None)
    assert [r["dish"] for r in second] == [DISHES[1]["dish_name"]]
    assert second[0]["outcome"] == "filed"
    assert calls["research"] == [] and calls["write"] == [DISHES[1]["dish_name"]]
    assert len(_entries()) == 3

    calls["write"].clear()
    assert seed.run(DISHES[:3], log=lambda *_: None) == [] and calls["write"] == []


def test_a_dish_a_household_already_filed_is_not_paid_for_again(calls):
    d = DISHES[0]
    lib.file_entry(d["dish_name"], ingredients=[{"item": "Beef", "qty": "1 lb"}],
                   instructions=["Cook it."], sources=[{"name": "X", "url": "https://x.example/a", "lead": True}],
                   origin="research")
    results = seed.run(DISHES[:2], log=lambda *_: None)
    assert [r["dish"] for r in results] == [DISHES[1]["dish_name"]]
    assert d["dish_name"] not in calls["research"] + calls["write"]
    assert lib.base_recipe(d["dish_name"])["origin"] == "research"


def test_samples_file_as_reviewed_with_no_model_call(calls):
    assert seed.main(["--i-mean-it", "--samples"]) == 0
    samples = seed.load_samples()
    for s in samples:
        e = lib.base_recipe(s["dish_name"])
        assert e["origin"] == "seed" and e["instructions"] == s["instructions"]
        assert e["sources"][0]["url"] == s["sources"][0]["url"] and e["sources"][0]["lead"]
        assert len(e["sources"]) == len(s["sources"])
    assert calls["research"] == calls["write"] == []
    # And the seed run then skips them.
    assert not {s["dish_name"] for s in samples} & {d["dish_name"] for d in seed.pending(DISHES)}
