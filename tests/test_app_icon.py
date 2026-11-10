"""The Deep Dusk app icon (Emily, 2026-11-10): the refined Solid fruit, drawn by make_app_icons.py."""
import json
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "static/icons"
APPICON = ROOT / "ios-app/ios/App/App/Assets.xcassets/AppIcon.appiconset"
SPLASH = ROOT / "ios-app/ios/App/App/Assets.xcassets/Splash.imageset"


def png_header(path):
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    width, height, depth, colour_type = struct.unpack(">IIBB", raw[16:26])
    return width, height, depth, colour_type


@pytest.mark.parametrize("path, size", [
    (ICONS / "icon-192.png", 192),
    (ICONS / "icon-512.png", 512),
    (APPICON / "AppIcon-512@2x.png", 1024),
    (APPICON / "AppIcon-dark-1024.png", 1024),
    (APPICON / "AppIcon-tinted-1024.png", 1024),
    (SPLASH / "splash-2732x2732.png", 2732),
])
def test_every_icon_is_the_right_size_with_no_alpha(path, size):
    """App Store Connect rejects an app icon with an alpha channel; colour type 2 is plain RGB."""
    width, height, depth, colour_type = png_header(path)
    assert (width, height) == (size, size)
    assert depth == 8 and colour_type == 2, f"{path.name} has an alpha channel"


def test_ios_ships_light_dark_and_tinted():
    images = json.loads((APPICON / "Contents.json").read_text())["images"]
    by_look = {(img.get("appearances") or [{"value": "any"}])[0]["value"]: img["filename"] for img in images}
    assert by_look == {"any": "AppIcon-512@2x.png", "dark": "AppIcon-dark-1024.png",
                       "tinted": "AppIcon-tinted-1024.png"}
    for name in by_look.values():
        assert (APPICON / name).exists()


def test_the_source_drawings_use_the_deep_dusk_colours():
    light = (ICONS / "pomona-icon.svg").read_text()
    dark = (ICONS / "pomona-icon-dark.svg").read_text()
    for colour in ("#FFAC52", "#0F1F3D", "#3F6AAE"):
        assert colour in light
    for colour in ("#0E1729", "#FFAC52", "#3F6AAE"):
        assert colour in dark


def test_the_launch_screen_is_cream_not_the_old_spruce():
    board = (ROOT / "ios-app/ios/App/App/Base.lproj/LaunchScreen.storyboard").read_text()
    assert 'red="1" green="0.98039215686274506" blue="0.95294117647058818"' in board
    assert 'red="0.10588235294117647"' not in board
