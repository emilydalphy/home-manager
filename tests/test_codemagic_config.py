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
    # Internal testers only: Beta App Review is Emily's button, after the
    # phone checklist (docs/TESTFLIGHT_WALKTHROUGH.md), never automatic.
    assert pub == {"auth": "integration", "submit_to_testflight": False}


def test_it_checks_for_xcode_26_and_signs_before_it_builds():
    """Apple refuses uploads from anything older than Xcode 26 (since
    2026-04-28); the check has to run before the long build, and the
    signing profiles have to be in place before it too."""
    wf = _workflow()
    assert wf["environment"]["xcode"] == "latest"
    scripts = [s["script"] for s in wf["scripts"]]
    xcode = next(i for i, s in enumerate(scripts) if "xcodebuild -version" in s)
    profiles = next(i for i, s in enumerate(scripts) if "xcode-project use-profiles" in s)
    build = next(i for i, s in enumerate(scripts) if "build-ipa" in s)
    assert xcode < build and profiles < build
    assert "-lt 26" in scripts[xcode] and "exit 1" in scripts[xcode]
    assert wf["environment"]["ios_signing"]["distribution_type"] == "app_store"


def test_the_bundle_id_and_version_come_from_the_app():
    wf = _workflow()
    config = json.loads((REPO / "ios-app" / "capacitor.config.json").read_text())
    assert wf["environment"]["ios_signing"]["bundle_identifier"] == config["appId"]
    build_step = next(s["script"] for s in wf["scripts"] if "CURRENT_PROJECT_VERSION" in s["script"])
    assert "$BUILD_NUMBER" in build_step
    # Never lower than what Apple already has, once the app's number is known.
    assert "get-latest-testflight-build-number" in build_step


def test_the_walkthrough_names_the_integration_and_the_workflow_as_the_file_does():
    """Emily types these names by hand; a rename in one place and not the
    other is a build that can't find its key."""
    wf = _workflow()
    walk = (REPO / "docs" / "TESTFLIGHT_WALKTHROUGH.md").read_text(encoding="utf-8")
    assert wf["integrations"]["app_store_connect"] in walk
    assert wf["name"] in walk
    assert wf["environment"]["ios_signing"]["bundle_identifier"] in walk


def test_no_secret_is_in_the_file():
    text = CONFIG.read_text()
    assert isinstance(_workflow()["integrations"]["app_store_connect"], str)
    for pattern in (r"BEGIN [A-Z ]*PRIVATE KEY", r"issuer_id\s*:", r"key_id\s*:", r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"):
        assert not re.search(pattern, text, re.I), pattern
