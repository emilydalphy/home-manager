"""
Texts swap the long dash and curly quotes for plain ones at send time
(Emily, 2026-10-31, option A).

One "—" or "’" anywhere in an SMS switches the whole message to UCS-2:
67 characters a segment instead of 153, so a 300-character morning text
was billed as 5 messages instead of 2. send_sms — the one place a text
leaves the app — now sends plain marks; the app's own screens, the text
the household previews, and push keep the nicer ones.
"""
from __future__ import annotations

import io
import json
import urllib.parse

import pytest

from app import tools
from app.tools import digest

# Everything Twilio's GSM-7 basic alphabet holds that a text from this app
# would plausibly use; anything outside it makes the message UCS-2.
GSM7 = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def sent(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret-token-never-logged")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+16475550100")
    bodies = []

    def fake_urlopen(req, timeout=0):
        form = urllib.parse.parse_qs(req.data.decode("utf-8"))
        bodies.append(form["Body"][0])
        return _FakeResponse(json.dumps({"sid": "SM0123456789"}).encode("utf-8"))

    monkeypatch.setattr(digest.urllib.request, "urlopen", fake_urlopen)
    return bodies


def test_the_evening_nudge_goes_out_in_plain_marks(sent):
    """CATCH: the card's own two examples."""
    tools.send_sms("+14165550100", "Tonight: Chicken Skewers — 35 min. Vineeth’s out. Tap to start.")
    assert sent == ["Tonight: Chicken Skewers - 35 min. Vineeth's out. Tap to start."]
    assert set(sent[0]) <= GSM7


def test_a_300_character_text_is_all_gsm7_once_sent(sent):
    """The whole point: nothing left that forces UCS-2."""
    body = ("Today’s moves — “move the chicken” to the fridge… dinner 5–6. " * 5)[:300]
    tools.send_sms("+14165550100", body)
    assert set(sent[0]) <= GSM7


def test_a_dash_written_without_spaces_still_reads_as_a_break():
    assert tools.sms_plain_marks("Skewers—35 min") == "Skewers - 35 min"
    assert tools.sms_plain_marks("Skewers  —  35 min") == "Skewers - 35 min"


def test_an_accented_name_is_left_alone():
    """GUARD: a person's name is not ours to respell — only the
    typography the app itself added is swapped."""
    assert tools.sms_plain_marks("Zoé’s home tonight.") == "Zoé's home tonight."


def test_the_screens_keep_the_nicer_marks(monkeypatch):
    """GUARD: the swap is at send time only. What the app builds — and what
    Preferences previews — still has the long dash."""
    body = "Tonight: Chicken Skewers — 35 min."
    seen = []
    monkeypatch.setitem(digest.CHANNELS, "text", lambda to, b: seen.append(b) or {"status": "ok"})
    digest.send_digest("text", "+14165550100", body)
    # The channel seam itself hands send_sms the original; send_sms is what
    # swaps, so a later channel (email, push) is never plain-marked by it.
    assert seen == [body]
