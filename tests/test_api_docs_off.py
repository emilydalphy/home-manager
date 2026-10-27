"""
FastAPI's automatic API pages are off (security audit, Loop Board "Security
audit before App Store submission", 2026-10-27).

/docs, /redoc and /openapi.json are a complete map of every route and every
field it takes. A household never needs them; someone probing the app would
love them. They are off unless SHOW_API_DOCS=1 is set, which is for a
developer's own machine and never for Railway.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

REPO = Path(__file__).resolve().parent.parent
PATHS = ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")


@pytest.mark.parametrize("path", PATHS)
def test_the_api_map_is_not_served_to_a_signed_in_household(path):
    client = TestClient(app)
    res = client.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    res = client.get(path, follow_redirects=False)
    assert res.status_code == 404, f"{path} answered {res.status_code}"
    assert "swagger" not in res.text.lower() and "openapi" not in res.text.lower()


def test_none_of_the_three_is_a_route():
    paths = {getattr(r, "path", None) for r in app.routes}
    assert not paths & set(PATHS)
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None


def test_no_screen_asks_for_the_api_map():
    """Turning it off must break nothing: no page or script reads it."""
    hits = []
    for path in (REPO / "static").rglob("*"):
        if path.suffix not in (".html", ".js"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"openapi\.json|['\"`]/docs\b|['\"`]/redoc\b", text):
            hits.append(str(path.relative_to(REPO)))
    assert not hits, f"these files still read the API map: {hits}"
