"""
A scan photo the Messages API would refuse is refused here first, with a
sentence (hunt-inputs-1031).

The kitchen and Shop scans send the photo as it came off the phone. A long
scrolling screenshot of a notes list (1080 x 12000, a couple of MB — what
the Shop scan asks for) passed _read_scan_image, the API refused it for
being over 8000 px on a side, and the household got a 500 saying "try
again in a minute", which no retry fixes. The byte cap was also above the
API's 10 MB-base64 limit (8 MiB as uploaded is 10.7 MB encoded).
"""
from __future__ import annotations

import struct
import zlib

from app import main
from app.main import _MAX_SCAN_IMAGE_BYTES, _image_size


def _png(width: int, height: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
        + b"\x00" * 64
    )


def _jpeg(width: int, height: int) -> bytes:
    """SOI, an APP0 segment to walk past, then a baseline frame header."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return b"\xff\xd8" + app0 + sof0 + b"\x00" * 64


def _count_calls(monkeypatch, name):
    calls = []
    monkeypatch.setattr(main, name, lambda b64, mt: calls.append(mt) or [
        {"item": "milk", "quantity": "", "category": "dairy", "confidence": "high"},
    ])
    return calls


def test_a_long_screenshot_is_refused_with_a_sentence_before_the_model(signed_in, monkeypatch):
    calls = _count_calls(monkeypatch, "scan_grocery_list_image")
    res = signed_in.post("/api/grocery-list/scan", files={"photo": ("list.png", _png(1080, 12000), "image/png")})
    assert res.status_code == 400
    assert "too big for me to read" in res.json()["detail"]
    assert calls == []


def test_a_kitchen_scan_refuses_a_jpeg_wider_than_the_api_takes(signed_in, monkeypatch):
    calls = _count_calls(monkeypatch, "scan_fridge_photo")
    res = signed_in.post("/api/inventory/scan-fridge", files={"photo": ("f.jpg", _jpeg(8160, 6120), "image/jpeg")})
    assert res.status_code == 400
    assert calls == []


def test_an_ordinary_phone_photo_still_reaches_the_model(signed_in, monkeypatch):
    calls = _count_calls(monkeypatch, "scan_grocery_list_image")
    res = signed_in.post("/api/grocery-list/scan", files={"photo": ("list.jpg", _jpeg(4032, 3024), "image/jpeg")})
    assert res.status_code == 200
    assert calls == ["image/jpeg"]


def test_the_byte_cap_fits_the_apis_ten_mb_once_base64_encoded(signed_in, monkeypatch):
    assert (_MAX_SCAN_IMAGE_BYTES + 2) // 3 * 4 <= 10_000_000
    calls = _count_calls(monkeypatch, "scan_grocery_list_image")
    big = _jpeg(4032, 3024) + b"0" * 7_600_000
    res = signed_in.post("/api/grocery-list/scan", files={"photo": ("list.jpg", big, "image/jpeg")})
    assert res.status_code == 400
    assert calls == []


def test_bytes_that_do_not_say_their_size_are_never_refused_for_it():
    assert _image_size(b"\xff\xd8\xff\xe0" + b"0" * 200) is None
    assert _image_size(b"\x89PNG") is None
    assert _image_size(b"RIFF\x00\x00\x00\x00WEBPVP8X" + b"\x00" * 8 + (8999).to_bytes(3, "little") + (99).to_bytes(3, "little")) == (9000, 100)
