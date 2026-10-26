"""
A pasted link with an accent or a space in it (defect hunt 2026-10-23).

"https://example.com/recettes/crêpes" — the way a link arrives from a
message or a page's text, not the way a browser's address bar shows it —
went to http.client raw. The accent raised UnicodeEncodeError, which the
fetch's except didn't name, so the household got a 500 quoting the codec;
a space was refused as an invalid URL and said as "I couldn't reach that
page" for a page that was there. The path is now percent-encoded the way a
browser sends it.

The connection here is REAL http.client, with only the socket and the
response faked, because the bug lived in http.client's own request-line
encoding — a fake that just records the path would pass either way.
"""
from __future__ import annotations

import http.client
import io
import socket

from app import recipe_import as ri

PAGE = (
    b'<html><head><script type="application/ld+json">'
    b'{"@type":"Recipe","name":"Cr\xc3\xaapes","recipeIngredient":["2 eggs","1 cup milk"],'
    b'"recipeInstructions":["Whisk.","Fry."]}</script></head><body></body></html>'
)


class _Response:
    status = 200

    def __init__(self):
        self._body = io.BytesIO(PAGE)

    def getheader(self, name):
        return {"content-type": "text/html; charset=utf-8"}.get(name.lower())

    def read(self, n=-1):
        return self._body.read(n)


class _Sock:
    def __init__(self, sent):
        self.sent = sent

    def sendall(self, data):
        self.sent.append(bytes(data))

    def close(self):
        pass


def _wire(monkeypatch):
    sent: list[bytes] = []

    class Conn(http.client.HTTPConnection):
        def connect(self):
            self.sock = _Sock(sent)

        def getresponse(self):
            return _Response()

    monkeypatch.setattr(ri, "_open_connection", lambda scheme, host, ip, port, timeout: Conn(ip, port))
    monkeypatch.setattr(ri.socket, "getaddrinfo", lambda host, port, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
    ])
    return sent


def _request_line(sent):
    return b"".join(sent).split(b"\r\n", 1)[0]


def test_a_link_with_an_accent_in_it_is_read(monkeypatch):
    sent = _wire(monkeypatch)
    draft = ri.import_recipe_from_url("https://example.com/recettes/crêpes?q=thé")
    assert draft["name"] == "Crêpes"
    assert _request_line(sent) == b"GET /recettes/cr%C3%AApes?q=th%C3%A9 HTTP/1.1"


def test_a_space_is_sent_as_a_browser_sends_it_and_an_encoded_link_goes_unchanged(monkeypatch):
    sent = _wire(monkeypatch)
    ri.import_recipe_from_url("https://example.com/my recipe")
    assert _request_line(sent) == b"GET /my%20recipe HTTP/1.1"
    sent.clear()
    # Already encoded (what a browser's share sheet hands over): never
    # encoded twice, or %C3 would go out as %25C3 and be a different page.
    ri.import_recipe_from_url("https://example.com/recettes/cr%C3%AApes?a=1&b=x%2By")
    assert _request_line(sent) == b"GET /recettes/cr%C3%AApes?a=1&b=x%2By HTTP/1.1"


def test_the_route_answers_a_draft_not_a_500(signed_in, monkeypatch):
    _wire(monkeypatch)
    res = signed_in.post("/api/recipes/import-url", json={"url": "https://example.com/recettes/crêpes"})
    assert res.status_code == 200, res.text
    assert res.json()["draft"]["name"] == "Crêpes"
