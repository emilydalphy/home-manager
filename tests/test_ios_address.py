"""
The address the iPhone app opens (Loop Board: "open Pomona at its real
address", 2026-10-26).

A TestFlight build carries its address baked in, so a beta tester who has
it installed keeps opening whatever this said on the day it was built. These
pin it to app.heypomona.com — the address that should never have to change
again — everywhere a build or the store listing reads it, so a stray edit
back to the Railway host goes red here instead of shipping.
"""
from __future__ import annotations

import json
import plistlib
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IOS = REPO / "ios-app"
ADDRESS = "https://app.heypomona.com"
HOST = "app.heypomona.com"


def _bound_domains() -> list[str]:
    with open(IOS / "ios" / "App" / "App" / "Info.plist", "rb") as f:
        return plistlib.load(f)["WKAppBoundDomains"]


def test_the_app_opens_app_heypomona_com():
    config = json.loads((IOS / "capacitor.config.json").read_text())
    assert config["server"]["url"] == ADDRESS


def test_the_first_app_bound_domain_is_the_one_the_app_opens():
    domains = _bound_domains()
    assert domains[0] == HOST
    # The offline page and the service worker need localhost listed too.
    assert "localhost" in domains
    # Apple honours at most ten.
    assert len(domains) <= 10


def test_the_offline_page_tries_again_at_the_same_address():
    text = (IOS / "www" / "app-config.js").read_text()
    config = json.loads(re.search(r"POMONA_APP = (\{.*\});", text).group(1))
    assert config["serverUrl"] == ADDRESS + "/"


def test_the_store_listing_urls_use_the_real_address():
    listing = (REPO / "docs" / "APP_STORE_LISTING.md").read_text()
    for path in ("support", "privacy", "terms"):
        assert f"`{ADDRESS}/{path}`" in listing
    assert "up.railway.app/support" not in listing
    assert "up.railway.app/privacy" not in listing
    assert "up.railway.app/terms" not in listing
