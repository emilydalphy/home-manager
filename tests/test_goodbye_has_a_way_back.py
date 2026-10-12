"""
The goodbye page has a way out (defect hunt, 2026-10-11).

After "Delete your household" or "Remove me", the shell signs the device out
and lands here with location.replace — no history entry to go back to. In
the iPhone app there is no address bar and no back gesture, so the page
used to be a dead end until the app was force-quit: an App Store reviewer
who deleted the demo household (the review notes tell them how) could not
get to the sign-in screen to carry on.
"""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.main import app


def _page() -> str:
    res = TestClient(app).get("/goodbye", headers={"accept": "text/html"}, follow_redirects=False)
    assert res.status_code == 200
    return res.text


def test_goodbye_links_back_to_sign_in_for_both_endings():
    html = _page()
    main = html[html.index('<main class="gb-wrap">'):html.index("</main>")]
    link = re.search(r'<a class="gb-back" href="([^"]+)">([^<]+)</a>', main)
    assert link, "the goodbye page needs a way to the sign-in screen"
    assert link.group(1) == "/login"
    # Outside both endings' blocks, so a delete (#gb-deleted) and a leave
    # (#gb-left, ?left=1) both show it — neither block's [hidden] hides it.
    assert main.rindex("</div>") < main.index('class="gb-back"')


def test_the_way_back_is_tall_enough_to_tap():
    html = _page()
    rule = re.search(r"\.gb-back\s*\{([^}]*)\}", html)
    assert rule and "min-height: 44px" in rule.group(1)


def test_the_way_back_lands_somewhere_a_signed_out_device_can_open():
    res = TestClient(app).get("/login", headers={"accept": "text/html"}, follow_redirects=False)
    assert res.status_code == 200
