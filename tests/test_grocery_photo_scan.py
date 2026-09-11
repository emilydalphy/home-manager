"""
Grocery: snap a photo of a written or on-screen list and have Pomona add it
(Loop Board, 2026-09-11).

A fourth scan target alongside the inventory trio (receipt/fridge/pantry):
same _read_scan_image validation (app/main.py), same forced-tool-call
_scan_image_for_items pattern (app/agent.py), same review-before-save shape
-- POST /api/grocery-list/scan returns a draft, POST
/api/grocery-list/confirm-scan is what actually adds anything, through
tools.add_grocery_items -- the same add path (and duplicate quantity
consolidation) a typed item uses.

Two layers, same split as test_api_call_recording.py's photo-scan test:

  * agent.scan_grocery_list_image, with the Anthropic client stubbed the
    same way the neighbouring receipt-scan test does (monkeypatch
    agent._client) -- this is the one that actually exercises the forced
    tool call and the prompt sent to it.
  * the two HTTP endpoints, with app.main.scan_grocery_list_image stubbed
    directly -- this is what proves validation, error shaping, rate
    limiting, and the confirm-scan add/merge path all actually wire up.
"""
from __future__ import annotations

import types

import pytest

from app import agent, tools
from app import main
from app.main import _MAX_SCAN_IMAGE_BYTES


# ---------- stubbing the Anthropic client (same shape as
#             test_api_call_recording.py's _stub_client/_tool_block) ----------

class _Usage:
    def __init__(self, input_tokens=0, cache_read=0, cache_write=0, output_tokens=0):
        self.input_tokens = input_tokens
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_write
        self.output_tokens = output_tokens


def _tool_block(name, tool_input, block_id="tu_1"):
    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def _fake_response(*blocks):
    return types.SimpleNamespace(
        content=list(blocks), stop_reason="tool_use", usage=_Usage(100, 0, 0, 50),
    )


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def _stub_client(monkeypatch, *responses):
    fake = types.SimpleNamespace(messages=_FakeMessages(list(responses)))
    monkeypatch.setattr(agent, "_client", lambda: fake)
    return fake


def _api_call_rows(household_id: int = 1):
    from app.db import get_conn
    conn = get_conn()
    try:
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM api_calls WHERE household_id = ? ORDER BY id", (household_id,)
            ).fetchall()
        ]
    finally:
        conn.close()


# Not a real JPEG -- _read_scan_image only checks content_type and size, it
# never decodes the bytes (that happens client-side, on the model call this
# suite stubs out).
_TINY_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200


# ---------- agent.scan_grocery_list_image ----------

def test_scan_grocery_list_image_reads_items_off_the_forced_tool_call(monkeypatch):
    _stub_client(monkeypatch, _fake_response(_tool_block("submit_scanned_items", {
        "items": [
            {"item": "milk", "quantity": "1 gal", "category": "dairy", "confidence": "high"},
            {"item": "eggs", "quantity": "", "category": "dairy", "confidence": "low"},
        ],
    })))

    items = agent.scan_grocery_list_image("ZmFrZQ==", "image/jpeg")

    assert [i["item"] for i in items] == ["milk", "eggs"]
    assert items[0]["quantity"] == "1 gal"
    assert items[1]["confidence"] == "low"


def test_scan_grocery_list_image_prompt_covers_handwritten_and_screenshots(monkeypatch):
    """
    Acceptance criterion: 'It accepts a photo of handwritten text and a
    screenshot of digital text.' Nothing else in the suite reads the actual
    instructions sent to Claude, so this checks the prompt text directly
    rather than trusting the docstring to match it.
    """
    fake = _stub_client(monkeypatch, _fake_response(_tool_block("submit_scanned_items", {"items": []})))

    agent.scan_grocery_list_image("ZmFrZQ==", "image/jpeg")

    sent_text = next(
        block["text"] for block in fake.messages.calls[0]["messages"][0]["content"] if block["type"] == "text"
    )
    assert "handwritten" in sent_text
    assert "screenshot" in sent_text
    # And the "don't invent it" instruction every scan prompt carries.
    assert "leave" in sent_text and "quantity" in sent_text


def test_a_grocery_list_scan_is_recorded_end_to_end(monkeypatch):
    """Parity with test_api_call_recording.py's photo-scan test -- the
    fourth scan target goes through the same _create_with_retry recording
    hook as the other three."""
    _stub_client(monkeypatch, _fake_response(_tool_block("submit_scanned_items", {
        "items": [{"item": "bananas", "quantity": "1 bunch", "category": "produce", "confidence": "high"}],
    })))

    items = agent.scan_grocery_list_image("ZmFrZQ==", "image/jpeg")
    assert items

    rows = _api_call_rows()
    assert len(rows) == 1
    assert rows[0]["call_site"] == "_scan_image_for_items"


# ---------- POST /api/grocery-list/scan ----------

def test_scan_endpoint_rejects_a_non_image_content_type(signed_in):
    res = signed_in.post("/api/grocery-list/scan", files={"photo": ("list.txt", b"hello", "text/plain")})
    assert res.status_code == 400


def test_scan_endpoint_rejects_an_empty_photo(signed_in):
    res = signed_in.post("/api/grocery-list/scan", files={"photo": ("list.jpg", b"", "image/jpeg")})
    assert res.status_code == 400


def test_scan_endpoint_rejects_an_oversized_photo(signed_in):
    big = b"0" * (_MAX_SCAN_IMAGE_BYTES + 1)
    res = signed_in.post("/api/grocery-list/scan", files={"photo": ("list.jpg", big, "image/jpeg")})
    assert res.status_code == 400


def test_scan_endpoint_returns_the_draft_items(signed_in, monkeypatch):
    monkeypatch.setattr(main, "scan_grocery_list_image", lambda b64, mt: [
        {"item": "bananas", "quantity": "1 bunch", "category": "produce", "confidence": "high"},
    ])
    res = signed_in.post("/api/grocery-list/scan", files={"photo": ("list.jpg", _TINY_JPEG, "image/jpeg")})
    assert res.status_code == 200
    assert res.json()["items"] == [{"item": "bananas", "quantity": "1 bunch", "category": "produce", "confidence": "high"}]


def test_scan_endpoint_fails_with_a_plain_message_on_an_unreadable_photo(signed_in, monkeypatch):
    """
    Acceptance: 'An unreadable or empty photo fails with a plain, friendly
    message, not a status code.' The status code itself is still an HTTP
    status (that's how the client tells success from failure) -- what this
    checks is that the body a person would actually see is readable text,
    never a bare numeral standing in for a message.
    """
    monkeypatch.setattr(main, "scan_grocery_list_image", lambda b64, mt: [])
    res = signed_in.post("/api/grocery-list/scan", files={"photo": ("list.jpg", _TINY_JPEG, "image/jpeg")})
    assert res.status_code != 200
    detail = res.json()["detail"]
    assert detail and not detail.isdigit() and "try" in detail.lower()


def test_scan_endpoint_shows_the_standard_unavailable_message_on_a_transient_failure(signed_in, monkeypatch):
    def _raise(b64, mt):
        raise agent.AssistantUnavailableError(
            "I'm having trouble reaching Claude's servers right now — try again in a moment."
        )
    monkeypatch.setattr(main, "scan_grocery_list_image", _raise)
    res = signed_in.post("/api/grocery-list/scan", files={"photo": ("list.jpg", _TINY_JPEG, "image/jpeg")})
    assert res.status_code == 503
    assert "trouble reaching" in res.json()["detail"].lower()


def test_scan_endpoint_is_rate_limited_like_the_other_scans(signed_in, monkeypatch):
    """Same 'scan' bucket as scan-receipt/scan-fridge/scan-pantry
    (ratelimit.py: 10 per 60s) -- checked directly rather than assumed."""
    monkeypatch.setattr(main, "scan_grocery_list_image", lambda b64, mt: [
        {"item": "milk", "quantity": "", "category": "dairy", "confidence": "high"},
    ])
    for _ in range(10):
        res = signed_in.post("/api/grocery-list/scan", files={"photo": ("list.jpg", _TINY_JPEG, "image/jpeg")})
        assert res.status_code == 200
    res = signed_in.post("/api/grocery-list/scan", files={"photo": ("list.jpg", _TINY_JPEG, "image/jpeg")})
    assert res.status_code == 429


# ---------- POST /api/grocery-list/confirm-scan ----------

def test_confirm_scan_adds_items_through_the_normal_add_path(signed_in):
    res = signed_in.post("/api/grocery-list/confirm-scan", json={
        "items": [
            {"item": "bananas", "quantity": "1 bunch", "category": "produce"},
            {"item": "oat milk", "quantity": "", "category": "dairy"},
        ],
    })
    assert res.status_code == 200
    body = res.json()
    assert set(body["added"]) == {"bananas", "oat milk"}
    assert body["merged_with_existing"] == []

    rows = {r["item"]: r for r in tools.list_grocery_list()}
    assert rows["bananas"]["quantity"] == "1 bunch"
    assert rows["bananas"]["category"] == "produce"
    assert "oat milk" in rows


def test_confirm_scan_defaults_category_to_other(signed_in):
    signed_in.post("/api/grocery-list/confirm-scan", json={"items": [{"item": "mystery item"}]})
    rows = {r["item"]: r for r in tools.list_grocery_list()}
    assert rows["mystery item"]["category"] == "other"


def test_confirm_scan_merges_with_a_duplicate_already_on_the_list(signed_in):
    """
    Acceptance: 'Confirmed items behave exactly like items added any other
    way — same quantity consolidation.' add_grocery_item's own duplicate
    rule (app/tools/grocery.py) merges by a normalised name key rather than
    creating a second line -- this proves confirm-scan goes through that
    same path rather than a bare INSERT.
    """
    tools.add_grocery_item("Bananas", quantity="1 bunch")

    res = signed_in.post("/api/grocery-list/confirm-scan", json={
        "items": [{"item": "bananas", "quantity": "1 bunch", "category": "produce"}],
    })

    assert res.status_code == 200
    body = res.json()
    assert body["added"] == []
    assert body["merged_with_existing"] == ["bananas"]

    rows = [r for r in tools.list_grocery_list() if r["item"].lower() == "bananas"]
    assert len(rows) == 1, "a duplicate scan should merge into the existing line, not create a second one"


def test_confirm_scan_with_nothing_kept_adds_nothing(signed_in):
    """The review step lets every item be unticked (Acceptance: 'an item
    can be removed') -- confirming an empty selection is a normal, cheap
    no-op, not an error."""
    res = signed_in.post("/api/grocery-list/confirm-scan", json={"items": []})
    assert res.status_code == 200
    assert res.json() == {"added": [], "merged_with_existing": []}
    assert tools.list_grocery_list() == []
