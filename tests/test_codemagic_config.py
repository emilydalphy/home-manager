"""
The cloud build for the iPhone app (codemagic.yaml, Loop Board: "App Store:
cloud build ready to go the moment the Apple account exists", 2026-10-06).

Nothing runs this file until Emily's Apple account exists, so a typo in it
would first show up on the day she is trying to ship. These pin that it
parses, that every path it names exists, that it runs the checks before
building, and that it carries no secret — Apple's key is referenced by the
integration's name, never pasted in.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "codemagic.yaml"


def _workflow() -> dict:
    data = yaml.safe_load(CONFIG.read_text())
    return data["workflows"]["ios-testflight"]


def test_it_parses_and_points_at_paths_that_exist():
    wf = _workflow()
    env = wf["environment"]["vars"]
    assert (REPO / env["APP_DIR"] / "package.json").is_file()
    assert (REPO / env["XCODE_PROJECT"] / "project.pbxproj").is_file()
    for step in wf["scripts"]:
        if "working_directory" in step:
            assert (REPO / step["working_directory"]).is_dir(), step["name"]


def test_it_installs_syncs_builds_and_sends_to_testflight_in_that_order():
    scripts = [s["script"] for s in _workflow()["scripts"]]
    order = [i for i, s in enumerate(scripts) for word in ("npm ci", "npm run sync", "build-ipa") if word in s]
    assert len(order) == 3 and order == sorted(order)
    pub = _workflow()["publishing"]["app_store_connect"]
    assert pub == {"auth": "integration", "submit_to_testflight": True}


def test_the_bundle_id_and_version_come_from_the_app():
    wf = _workflow()
    config = json.loads((REPO / "ios-app" / "capacitor.config.json").read_text())
    assert wf["environment"]["ios_signing"]["bundle_identifier"] == config["appId"]
    build_step = next(s["script"] for s in wf["scripts"] if "CURRENT_PROJECT_VERSION" in s["script"])
    assert "$BUILD_NUMBER" in build_step


def test_no_secret_is_in_the_file():
    text = CONFIG.read_text()
    assert isinstance(_workflow()["integrations"]["app_store_connect"], str)
    for pattern in (r"BEGIN [A-Z ]*PRIVATE KEY", r"issuer_id\s*:", r"key_id\s*:", r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"):
        assert not re.search(pattern, text, re.I), pattern
