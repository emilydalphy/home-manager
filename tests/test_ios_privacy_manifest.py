"""
The iPhone app's privacy manifest (Loop Board: "App Store: the iPhone app's
privacy manifest", 2026-10-06).

Apple turns an upload away without ios/App/App/PrivacyInfo.xcprivacy, and
the App Store privacy label is checked against it, so what it declares has
to be what the app really collects. These pin that it parses, says Pomona
tracks nobody, declares the data the card lists, ships in the app's
Resources, and that `npm run sync` stops with a plain sentence when it
is missing.
"""
from __future__ import annotations

import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IOS = REPO / "ios-app"
MANIFEST = IOS / "ios" / "App" / "App" / "PrivacyInfo.xcprivacy"
PBXPROJ = IOS / "ios" / "App" / "App.xcodeproj" / "project.pbxproj"


def _manifest() -> dict:
    with MANIFEST.open("rb") as f:
        return plistlib.load(f)


def test_the_manifest_is_a_valid_plist_that_tracks_nobody():
    m = _manifest()
    assert m["NSPrivacyTracking"] is False
    assert m["NSPrivacyTrackingDomains"] == []


def test_it_declares_what_the_app_collects_linked_and_never_for_tracking():
    types = {d["NSPrivacyCollectedDataType"]: d for d in _manifest()["NSPrivacyCollectedDataTypes"]}
    for name in ("Name", "EmailAddress", "PhoneNumber", "Health", "OtherUserContent", "CustomerSupport"):
        entry = types["NSPrivacyCollectedDataType" + name]
        assert entry["NSPrivacyCollectedDataTypeLinked"] is True
        assert entry["NSPrivacyCollectedDataTypePurposes"] == ["NSPrivacyCollectedDataTypePurposeAppFunctionality"]
    for entry in types.values():
        assert entry["NSPrivacyCollectedDataTypeTracking"] is False


def test_it_ships_in_the_apps_resources():
    pbx = PBXPROJ.read_text()
    assert "PrivacyInfo.xcprivacy in Resources */," in pbx.split("isa = PBXResourcesBuildPhase;")[1].split("};")[0]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_sync_stops_with_a_plain_message_when_the_manifest_is_missing(tmp_path):
    app = tmp_path / "ios-app"
    shutil.copytree(IOS, app, ignore=shutil.ignore_patterns("node_modules"))
    ok = subprocess.run(["node", "scripts/prepare-www.mjs", "--check"], cwd=app, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr

    (app / "ios" / "App" / "App" / "PrivacyInfo.xcprivacy").unlink()
    res = subprocess.run(["node", "scripts/prepare-www.mjs", "--check"], cwd=app, capture_output=True, text=True)
    assert res.returncode == 1
    assert "PrivacyInfo.xcprivacy is missing" in res.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_sync_stops_when_a_plugin_calls_an_undeclared_required_reason_api(tmp_path):
    app = tmp_path / "ios-app"
    shutil.copytree(IOS, app, ignore=shutil.ignore_patterns("node_modules"))
    plugin = app / "node_modules" / "@capacitor" / "some-plugin" / "ios" / "Sources"
    plugin.mkdir(parents=True)
    (plugin / "Plugin.swift").write_text("let saved = UserDefaults.standard.string(forKey: \"k\")\n")
    res = subprocess.run(["node", "scripts/prepare-www.mjs", "--check"], cwd=app, capture_output=True, text=True)
    assert res.returncode == 1
    assert "NSPrivacyAccessedAPICategoryUserDefaults" in res.stderr
