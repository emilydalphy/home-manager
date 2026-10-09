"""
Add from a link: TikTok (Loop Board, "read recipes from TikTok and other
video links", 2026-10-09).

A TikTok page is built by JavaScript and carries no recipe markup — the
recipe is in the caption (or only spoken in the video). So a TikTok link
skips the page and asks TikTok's public oEmbed endpoint for the caption,
then hands the caption to the same model reader a markup-less page gets.
Short links (vt./vm.tiktok.com) are followed to the full video URL first,
under the same SSRF rules as every other fetch. None of this touches the
network: the fake connection and the stubbed model come from
test_recipe_import.
"""
from __future__ import annotations

import json
import os
from urllib.parse import parse_qs, urlsplit

import pytest

from app import agent, recipe_import as ri, tools

from test_recipe_import import (
    PLAIN_PAGE, _FakeResponse, _html_response, _model_recipe, _stub_model, _wire,
)

VIDEO = "https://www.tiktok.com/@nanacooks/video/7301234567890123456"
CAPTION = (
    "Nana's pancakes 🥞 1 cup flour, 1 egg, 1 cup milk. Whisk, then fry in butter. "
    "#breakfast #fyp"
)


def _oembed(caption=CAPTION, author="Nana Cooks"):
    body = json.dumps({
        "version": "1.0", "type": "video", "title": caption,
        "author_name": author, "author_url": "https://www.tiktok.com/@nanacooks",
        "provider_name": "TikTok", "html": "<blockquote>...</blockquote>",
    }).encode("utf-8")
    return _FakeResponse(200, body, {"Content-Type": "application/json; charset=utf-8"})


def _oembed_path(video_url=VIDEO):
    from urllib.parse import quote
    return "/oembed?url=" + quote(video_url, safe="")


def test_a_short_link_follows_its_redirect_reads_the_caption_and_saves(signed_in, monkeypatch):
    conn = _wire(monkeypatch, {
        "/ZSabc123/": _FakeResponse(301, b"", {"Location": VIDEO + "?_t=8abc&_r=1"}),
        _oembed_path(): _oembed(),
    })
    messages = _stub_model(monkeypatch, _model_recipe())
    res = signed_in.post("/api/recipes/import-url", json={"url": "https://vt.tiktok.com/ZSabc123/"})
    assert res.status_code == 200, res.text
    draft = res.json()["draft"]
    assert draft["name"] == "Nana's pancakes"
    assert draft["read_by"] == "model"
    # Credited to the video itself, without the share link's tracking.
    assert draft["source_url"] == VIDEO

    # The video page was never fetched: the redirect's target was enough.
    paths = [path for _m, path, _h in conn.requests]
    assert paths == ["/ZSabc123/", _oembed_path()]
    asked = parse_qs(urlsplit(paths[1]).query)["url"][0]
    assert asked == VIDEO
    assert conn.requests[1][2]["Host"] == "www.tiktok.com"

    # The model read the caption, inside the fence, with who posted it.
    prompt = messages.calls[0]["messages"][0]["content"]
    assert "1 cup flour, 1 egg, 1 cup milk" in prompt
    assert "Nana Cooks" in prompt

    saved = signed_in.post("/api/recipes/add", json=draft)
    assert saved.status_code == 200, saved.text
    assert saved.json()["citation"]["text"] == "From tiktok.com"
    assert tools.get_recipe("Nana's pancakes")["source_url"] == VIDEO


def test_a_full_video_link_goes_straight_to_the_caption(monkeypatch):
    conn = _wire(monkeypatch, {_oembed_path(): _oembed()})
    _stub_model(monkeypatch, _model_recipe())
    draft = ri.import_recipe_from_url(VIDEO + "?is_from_webapp=1", model_reader=agent.read_recipe_from_page_llm)
    assert draft["source_url"] == VIDEO
    assert [path for _m, path, _h in conn.requests] == [_oembed_path()]


def test_a_caption_with_no_recipe_says_so_plainly(signed_in, monkeypatch):
    _wire(monkeypatch, {
        "/ZSnope/": _FakeResponse(301, b"", {"Location": VIDEO}),
        _oembed_path(): _oembed(caption="This changed my life 😍 #dinner #fyp"),
    })
    _stub_model(monkeypatch, _model_recipe(found=False))
    res = signed_in.post("/api/recipes/import-url", json={"url": "https://vm.tiktok.com/ZSnope/"})
    assert res.status_code == 400
    assert res.json()["detail"] == ri.MSG_NO_RECIPE_VIDEO
    assert tools.list_recipes() == []


def test_an_empty_caption_is_no_recipe_without_asking_the_model(monkeypatch):
    _wire(monkeypatch, {_oembed_path(): _oembed(caption="")})
    messages = _stub_model(monkeypatch)  # nothing queued: a call would fail
    with pytest.raises(ri.RecipeImportError) as err:
        ri.import_recipe_from_url(VIDEO, model_reader=agent.read_recipe_from_page_llm)
    assert err.value.kind == "no_recipe"
    assert str(err.value) == ri.MSG_NO_RECIPE_VIDEO
    assert messages.calls == []


def test_the_no_recipe_answer_still_offers_tell_me_the_recipe_instead_and_no_pill():
    # The sheet draws its way out under ANY problem sentence the route
    # returns, so the video's own sentence gets it too.
    js = open(os.path.join(os.path.dirname(__file__), "..", "static", "shell.js"), encoding="utf-8").read()
    assert "? '<p class=\"rli-problem\" role=\"alert\">' + escapeHtml(problem) + '</p>' + rliAskInsteadHtml()" in js
    assert "Tell me the recipe instead" in js
    # The card's last criterion: no "In development" pill on Add from a link.
    assert "var RECIPE_LINK_IN_DEVELOPMENT = false;" in js


def test_a_short_link_redirecting_somewhere_private_is_refused(monkeypatch):
    conn = _wire(monkeypatch, {
        "/ZSevil/": _FakeResponse(302, b"", {"Location": "http://169.254.169.254/latest/meta-data/"}),
    })
    with pytest.raises(ri.RecipeImportError) as err:
        ri.import_recipe_from_url("https://vt.tiktok.com/ZSevil/", model_reader=lambda *a: None)
    assert err.value.kind == "blocked"
    assert [path for _m, path, _h in conn.requests] == ["/ZSevil/"]


def test_a_short_link_that_never_reaches_a_video_is_no_recipe(monkeypatch):
    _wire(monkeypatch, {"/ZSgone/": _FakeResponse(301, b"", {"Location": "https://www.tiktok.com/"}),
                        "/": _html_response("<html><body>TikTok</body></html>")})
    with pytest.raises(ri.RecipeImportError) as err:
        ri.import_recipe_from_url("https://vt.tiktok.com/ZSgone/", model_reader=lambda *a: None)
    assert err.value.kind == "no_recipe"


def test_a_removed_or_private_video_is_unreachable(monkeypatch):
    _wire(monkeypatch, {_oembed_path(): _FakeResponse(400, b'{"code":400}', {"Content-Type": "application/json"})})
    with pytest.raises(ri.RecipeImportError) as err:
        ri.import_recipe_from_url(VIDEO, model_reader=lambda *a: None)
    assert err.value.kind == "unreachable"


def test_an_oembed_answer_that_is_not_json_is_unreachable(monkeypatch):
    _wire(monkeypatch, {_oembed_path(): _html_response("<html>blocked</html>")})
    with pytest.raises(ri.RecipeImportError) as err:
        ri.import_recipe_from_url(VIDEO, model_reader=lambda *a: None)
    assert err.value.kind == "unreachable"


@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/@nanacooks/video/7301234567890123456",
    "https://tiktok.com/@nanacooks/video/7301234567890123456",
    "https://m.tiktok.com/v/7301234567890123456.html",
    "https://vt.tiktok.com/ZSabc123/",
    "https://vm.tiktok.com/ZSabc123/",
    "HTTPS://WWW.TIKTOK.COM/@x/video/1",
])
def test_tiktok_links_are_recognised(url):
    assert ri.is_tiktok_url(url)


@pytest.mark.parametrize("url", [
    "https://recipes.example.com/chili",
    "https://tiktok.com.evil.example/@x/video/1",
    "https://nottiktok.com/@x/video/1",
    "https://www.youtube.com/watch?v=abc",
    "http://[::1/",
    "",
])
def test_other_links_are_not_tiktok(url):
    assert not ri.is_tiktok_url(url)


def test_an_ordinary_link_is_read_as_a_page_as_before(monkeypatch):
    conn = _wire(monkeypatch, {"/p": _html_response(PLAIN_PAGE)})
    _stub_model(monkeypatch, _model_recipe())
    draft = ri.import_recipe_from_url("https://recipes.example.com/p", model_reader=agent.read_recipe_from_page_llm)
    assert draft["source_url"] == "https://recipes.example.com/p"
    assert [path for _m, path, _h in conn.requests] == ["/p"]
