"""
Loop Board (Medium · Bug, 2026-10-01): "Four routes can see a refusal and
have no arm for it — one of them answers 500 and logs a traceback".

week_swap_in_place, week_swap_choose and week_change_part reach
swap_in_place.apply_pick / apply_pick_to_days, whose backstops raise
SlotRefused (a night gone by; the over-cap "I left it as it was"). They had
only `except ValueError` -> 404, so the screen printed its generic trouble
line instead of the sentence. chat_proposal_apply had no ValueError arm at
all: a refusal fell into `except Exception` -> 500 + logger.exception.

The rule (CLAUDE.md 2026-10-01, "a refusal written for a person is an
answer, not a breakage"): 200 {"status": "refused", "message": …}, no log,
no error_events row, and the arm ABOVE the plain ValueError.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from app import main as main_module
from app.db import get_conn
from app.tools import weekly_plan as _weekly_plan

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

WEEK = "2026-09-14"
OVER_CAP = "I left it as it was — Beef Wellington takes 90 minutes, and Friday only has 20."

# (route function name, tool attr in main.tools, method, url, body)
ROUTES = [
    ("week_swap_in_place", "swap_meal_in_place", f"/api/week/{WEEK}/swap-in-place",
     {"entry_id": 1}),
    ("week_swap_choose", "choose_swap_option", f"/api/week/{WEEK}/swap-choose",
     {"entry_id": 1, "option": 0}),
    ("week_change_part", "change_part", f"/api/week/{WEEK}/change-part",
     {"entry_id": 1, "role": "protein", "choice": "tofu"}),
    ("chat_proposal_apply", "apply_proposal", "/api/chat/proposals/abc123/apply", None),
]
IDS = [r[0] for r in ROUTES]


def _error_rows():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute("SELECT kind, where_, detail FROM error_events")]
    finally:
        conn.close()


def _raiser(exc):
    def fn(*a, **k):
        raise exc
    return fn


@pytest.fixture
def plan_one(monkeypatch):
    monkeypatch.setattr(main_module, "_plan_id_for_week", lambda week_start: 1)


def _post(client, url, body):
    return client.post(url, json=body if body is not None else {})


@pytest.mark.parametrize("sentence", [_weekly_plan.NIGHT_GONE, OVER_CAP])
@pytest.mark.parametrize("name,tool,url,body", ROUTES, ids=IDS)
def test_a_refusal_is_a_200_with_its_own_sentence(signed_in, monkeypatch, plan_one, caplog,
                                                  name, tool, url, body, sentence):
    monkeypatch.setattr(main_module.tools, tool, _raiser(_weekly_plan.SlotRefused(sentence)))
    before = _error_rows()
    with caplog.at_level("DEBUG"):
        res = _post(signed_in, url, body)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["status"] == "refused"
    assert out["message"] == sentence
    # Recorded as a refusal, not an error: no traceback, no error_events row.
    assert not any(r.exc_info for r in caplog.records)
    assert not any(r.levelname in ("ERROR", "CRITICAL") for r in caplog.records)
    assert _error_rows() == before
    assert "Traceback" not in res.text


def test_the_chat_card_answer_keeps_refused_a_list(signed_in, monkeypatch):
    """The card's Save handler reads out.refused; a route-level refusal
    must not hand it something that isn't a list."""
    monkeypatch.setattr(main_module.tools, "apply_proposal",
                        _raiser(_weekly_plan.SlotRefused(_weekly_plan.NIGHT_GONE)))
    out = signed_in.post("/api/chat/proposals/abc123/apply", json={}).json()
    assert out["refused"] == []


@pytest.mark.parametrize("name,tool,url,body", ROUTES, ids=IDS)
def test_a_real_crash_is_still_a_500_and_still_logged(signed_in, monkeypatch, plan_one, caplog,
                                                      name, tool, url, body):
    monkeypatch.setattr(main_module.tools, tool, _raiser(RuntimeError("kaboom")))
    with caplog.at_level("ERROR"):
        res = _post(signed_in, url, body)
    assert res.status_code == 500
    assert any(r.exc_info for r in caplog.records), "a crash keeps its traceback in the log"
    assert "kaboom" not in res.text


@pytest.mark.parametrize("name", ["week_swap_in_place", "week_swap_choose", "week_change_part"])
def test_a_plain_valueerror_is_still_a_404(signed_in, monkeypatch, plan_one, name):
    _, tool, url, body = next(r for r in ROUTES if r[0] == name)
    monkeypatch.setattr(main_module.tools, tool, _raiser(ValueError("No plan entry with id 1.")))
    res = _post(signed_in, url, body)
    assert res.status_code == 404


@pytest.mark.parametrize("name", IDS)
def test_the_refusal_arm_sits_above_valueerror_and_exception(name):
    """SlotRefused subclasses ValueError: an arm below it is a no-op that
    looks finished."""
    src = textwrap.dedent(inspect.getsource(getattr(main_module, name)))
    fn = ast.parse(src).body[0]
    trys = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    order = [ast.unparse(h.type) for t in trys for h in t.handlers if h.type is not None]
    assert "tools.SlotRefused" in order, order
    i = order.index("tools.SlotRefused")
    for later in ("ValueError", "Exception"):
        if later in order:
            assert i < order.index(later), order


def test_the_chat_card_shows_the_servers_sentence_when_no_row_is_named():
    save = SHELL_JS.split("'/apply', {})")[1][:1500]
    refused_branch = save.split("out.status === 'refused'")[1][:600]
    assert "out.message" in refused_branch
    assert "state.refused.length" in refused_branch
