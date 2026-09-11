"""
Each adult has their own login inside the household — slice 1 (Loop Board,
2026-09-11, branch `worktree-adult-login`).

The household passphrase still opens the door. After it, a household with
more than one adult is asked "Who's this?" once per device, and the answer
rides in the signed session cookie beside the household id. From then on
the server knows which adult is acting: the writes that record a person
(the week's approver, who added a grocery item, who dropped one before
shopping, who started the week's questions) take that adult's name when
the caller did not give one, and the "{name} approved the week"
notification is addressed to the OTHER adult rather than broadcast.

Slice 2 — each adult having their own secret — is deliberately not here.

These run through the real HTTP stack with real cookies wherever the
property is about the cookie, and against the tools directly where it is
about a write. The isolation bar is the same one tests/test_multi_household.py
sets: a pick can never name another household's member, and a cookie that
lies about its member reads as nobody, never as anyone.
"""
from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from app import households, security, tools
from app.db import get_conn


REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

BETA_PASSPHRASE = "beta-tester-passphrase"


def _week_start() -> str:
    today = datetime.date.today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


def _sign_in(client, password="test-password"):
    res = client.post("/login", data={"password": password, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return client.cookies[security.COOKIE_NAME]


def _adult(name: str, household: int = 1) -> int:
    with tools.use_household(household):
        member_id = tools.add_member(name)["member_id"]
        # Onboarding writes "Adult" (capitalised) — the app compares
        # case-insensitively, so the test does the same thing on purpose.
        tools.set_member_age_group(name, "Adult")
    return member_id


def _child(name: str, household: int = 1) -> int:
    with tools.use_household(household):
        member_id = tools.add_member(name)["member_id"]
        tools.set_member_age_group(name, "child")
    return member_id


@pytest.fixture
def two_adults():
    return {"Emily": _adult("Emily"), "Vineeth": _adult("Vineeth")}


# ---------- /api/whoami and the pick ----------

def test_whoami_asks_when_two_adults_and_nobody_picked(client, two_adults):
    _sign_in(client)
    body = client.get("/api/whoami").json()
    assert body["household_id"] == 1
    assert body["member"] is None
    assert body["needs_pick"] is True
    assert [a["name"] for a in body["adults"]] == ["Emily", "Vineeth"]
    for adult in body["adults"]:
        assert set(adult) == {"id", "name", "initial", "color"}


def test_a_household_with_one_adult_needs_no_pick(client):
    """One adult is not a question — the app knows who it is talking to."""
    emily = _adult("Emily")
    _child("Sam")
    _sign_in(client)
    body = client.get("/api/whoami").json()
    assert body["needs_pick"] is False
    assert body["member"]["id"] == emily
    assert body["member"]["name"] == "Emily"
    assert [a["name"] for a in body["adults"]] == ["Emily"], "a child is never on the pick list"


def test_a_household_with_no_adults_yet_needs_no_pick(client):
    """Onboarding for a brand-new household must not be blocked by this."""
    _sign_in(client)
    body = client.get("/api/whoami").json()
    assert body["needs_pick"] is False
    assert body["member"] is None
    assert body["adults"] == []


def test_the_pick_lands_in_the_signed_cookie_and_survives_requests(client, two_adults):
    before = _sign_in(client)
    res = client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})
    assert res.status_code == 200
    assert res.json()["member"]["name"] == "Vineeth"

    after = client.cookies[security.COOKIE_NAME]
    assert after != before
    assert security.read_session_member(after) == two_adults["Vineeth"]
    # Same session id and sign-in time: picking a name does not start a new
    # conversation or quietly extend the 30-day session.
    assert security.read_session_parts(after)[0] == security.read_session_parts(before)[0]
    assert security._decode_session(after)[2] == security._decode_session(before)[2]

    for _ in range(3):
        body = client.get("/api/whoami").json()
        assert body["member"]["id"] == two_adults["Vineeth"]
        assert body["needs_pick"] is False


def test_switching_replaces_the_pick(client, two_adults):
    _sign_in(client)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})
    client.post("/api/whoami/pick", json={"member_id": two_adults["Emily"]})
    assert client.get("/api/whoami").json()["member"]["name"] == "Emily"


def test_picking_a_child_is_refused(client, two_adults):
    sam = _child("Sam")
    _sign_in(client)
    res = client.post("/api/whoami/pick", json={"member_id": sam})
    assert res.status_code == 404
    assert client.get("/api/whoami").json()["member"] is None


def test_picking_another_households_member_is_refused(client, two_adults):
    """
    Household isolation, above all. The beta tester's session cannot pick
    Emily's member id, and the refusal reads exactly like a made-up id so
    the route cannot be used to learn which ids are real.
    """
    beta = households.create_household("The Beta Testers", BETA_PASSPHRASE)
    _adult("Julia", beta)
    _adult("Marco", beta)
    _sign_in(client, BETA_PASSPHRASE)

    foreign = client.post("/api/whoami/pick", json={"member_id": two_adults["Emily"]})
    made_up = client.post("/api/whoami/pick", json={"member_id": 999_999})
    assert foreign.status_code == 404
    assert made_up.status_code == 404
    assert foreign.json() == made_up.json()
    assert client.get("/api/whoami").json()["member"] is None


def test_a_forged_member_id_is_a_signature_failure_not_a_person(client, two_adults):
    cookie = _sign_in(client)
    sid, household, issued, member, sig = cookie.split(".")
    forged = f"{sid}.{household}.{issued}.{two_adults['Emily']}.{sig}"
    assert security.read_session_parts(forged) is None
    assert security.read_session_member(forged) is None
    client.cookies.set(security.COOKIE_NAME, forged)
    assert client.get("/api/whoami").status_code == 401, "an edited cookie is no cookie at all"


def test_an_out_of_range_member_id_in_a_signed_cookie_is_no_pick_not_a_500(client, two_adults):
    """
    Python parses 10**30 happily; SQLite cannot bind it, and the query
    raised OverflowError from inside current_member() — a 500 on /api/whoami
    and on every write. Found by the build's independent verifier. A
    number that cannot be a member is no pick, and the question is asked
    again; a household id that cannot be a household is no cookie at all.
    """
    client.cookies.set(security.COOKIE_NAME, security.issue_session(1, 10**30))
    res = client.get("/api/whoami")
    assert res.status_code == 200
    assert res.json()["member"] is None
    assert res.json()["needs_pick"] is True
    add = client.post("/api/grocery-list/add", json={"item": "lemons", "quantity": "3", "category": "produce"})
    assert add.status_code == 200

    client.cookies.set(security.COOKIE_NAME, security.issue_session(10**30, None))
    assert client.get("/api/whoami").status_code == 401


def test_a_signed_cookie_naming_a_foreign_member_reads_as_nobody(client, two_adults):
    """
    A validly signed cookie whose member belongs to another household — the
    server never mints one, but `issue_session` will for any int, so the
    read side is checked on its own: nobody, not them, and not a 500.
    """
    beta = households.create_household("The Beta Testers", BETA_PASSPHRASE)
    julia = _adult("Julia", beta)
    _adult("Marco", beta)
    client.cookies.set(security.COOKIE_NAME, security.issue_session(1, julia))
    body = client.get("/api/whoami").json()
    assert body["household_id"] == 1
    assert body["member"] is None
    assert body["needs_pick"] is True


def test_a_member_removed_after_being_picked_means_asked_again(client, two_adults):
    _sign_in(client)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})
    conn = get_conn()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM members WHERE id = ?", (two_adults["Vineeth"],))
    conn.commit()
    conn.close()
    _adult("Priya")  # still two adults, so there is still a question

    res = client.get("/api/whoami")
    assert res.status_code == 200
    assert res.json()["member"] is None
    assert res.json()["needs_pick"] is True


def test_a_member_marked_a_child_after_being_picked_means_asked_again(client, two_adults):
    _sign_in(client)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})
    _adult("Priya")
    tools.set_member_age_group("Vineeth", "teen")
    body = client.get("/api/whoami").json()
    assert body["member"] is None
    assert body["needs_pick"] is True


def test_the_pick_does_not_leak_between_requests(client, two_adults):
    """Same shape as the household test: alternate sessions, nothing sticks."""
    _sign_in(client)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})
    assert client.get("/api/whoami").json()["member"]["name"] == "Vineeth"
    _sign_in(client)  # a fresh sign-in is a fresh cookie with no pick
    assert client.get("/api/whoami").json()["member"] is None
    assert tools.member_id() is None, "nothing bound outside a request"


def test_older_cookies_without_a_member_still_sign_in(client):
    """Nobody already signed in is logged out by this change."""
    four_part = security.issue_session(1)  # minted today, five parts
    sid, household, issued, _member, sig = four_part.split(".")
    import hashlib, hmac
    payload = f"{sid}.{household}.{issued}"
    old_sig = security._b64(hmac.new(security._secret(), payload.encode(), hashlib.sha256).digest())
    old_style = f"{payload}.{old_sig}"
    assert security.read_session_parts(old_style) == (sid, 1)
    assert security.read_session_member(old_style) is None
    client.cookies.set(security.COOKIE_NAME, old_style)
    assert client.get("/api/whoami").status_code == 200


# ---------- the writes take the session's adult ----------

def test_approving_with_no_name_records_the_picked_adult(client, two_adults):
    _sign_in(client)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    res = client.post(f"/api/week/{_week_start()}/approve", json={"approved_by": ""})
    assert res.status_code == 200
    assert res.json()["approved_by"] == "Vineeth"
    row = get_conn().execute(
        "SELECT approved_by, approved_by_member_id FROM weekly_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    assert row["approved_by"] == "Vineeth"
    assert row["approved_by_member_id"] == two_adults["Vineeth"]


def test_an_explicit_name_is_kept_and_gets_no_wrong_member_id(two_adults):
    """
    A name the caller gave stays — the session fills blanks, it does not
    overrule a person who was named — and a member id is only stored when
    the name really is that member's.
    """
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    with tools.use_member(two_adults["Vineeth"]):
        result = tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert result["approved_by"] == "Emily"
    row = get_conn().execute(
        "SELECT approved_by_member_id FROM weekly_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    assert row["approved_by_member_id"] is None


def test_grocery_adds_and_pre_shop_drops_record_the_picked_adult(client, two_adults):
    _sign_in(client)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Emily"]})
    # The shell sends no added_by at all; the offline queue replays the same body.
    res = client.post("/api/grocery-list/add", json={"item": "lemons", "quantity": "3", "category": "produce"})
    assert res.status_code == 200
    item = next(i for i in tools.list_grocery_list() if i["item"] == "lemons")
    assert item["added_by"] == "Emily"

    res = client.post(f"/api/grocery-list/{item['id']}/pre-shop", json={"decision": "drop", "author": "user"})
    assert res.status_code == 200
    row = get_conn().execute("SELECT removed_by FROM grocery_items WHERE id = ?", (item["id"],)).fetchone()
    assert row["removed_by"] == "Emily"


def test_the_planners_own_adds_stay_marked_ai(two_adults):
    with tools.use_member(two_adults["Emily"]):
        tools.add_grocery_item("beans", quantity="1 tin", category="pantry", added_by="ai")
    item = next(i for i in tools.list_grocery_list() if i["item"] == "beans")
    assert item["added_by"] == "ai"


def test_the_week_intake_records_who_started_it(two_adults):
    with tools.use_member(two_adults["Vineeth"]):
        tools.save_week_intake(_week_start(), night_tags={}, created_by="")
    intake = tools.get_week_intake(_week_start())
    assert intake["created_by"] == "Vineeth"


def test_a_single_adult_is_credited_without_a_pick():
    """No pick, one adult: it is them. Not "user"."""
    _adult("Julia")
    tools.add_grocery_item("milk", quantity="1", category="dairy")
    item = next(i for i in tools.list_grocery_list() if i["item"] == "milk")
    assert item["added_by"] == "Julia"


def test_no_adults_means_the_old_free_text_is_kept():
    """Scripts, tests and pre-pick devices keep exactly what they had."""
    tools.add_grocery_item("milk", quantity="1", category="dairy")
    item = next(i for i in tools.list_grocery_list() if i["item"] == "milk")
    assert item["added_by"] == "user"


# ---------- notification #4 goes to the other adult ----------

def _approved_notifications():
    return [n for n in tools.get_active_notifications() if n["type"] == "week_approved"]


def test_the_approver_is_not_told_they_approved(two_adults):
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(datetime.date.today().isoformat(), "Chili", slot="dinner", weekly_plan_id=plan_id)
    with tools.use_member(two_adults["Vineeth"]):
        tools.approve_weekly_plan(plan_id)

    with tools.use_member(two_adults["Vineeth"]):
        assert _approved_notifications() == [], "Vineeth settled it; he does not need telling"
    with tools.use_member(two_adults["Emily"]):
        shown = _approved_notifications()
        assert len(shown) == 1
        assert shown[0]["title"] == "Vineeth approved the week"
    with tools.use_member(None):
        assert len(_approved_notifications()) == 1, "a device with nobody picked still hears about it"


def test_the_approver_is_recognised_by_name_when_no_member_id_was_stored(two_adults):
    """Plans approved before member ids existed still know who approved."""
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(datetime.date.today().isoformat(), "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="emily")
    get_conn().execute("UPDATE weekly_plans SET approved_by_member_id = NULL WHERE id = ?", (plan_id,)).connection.commit()

    with tools.use_member(two_adults["Emily"]):
        assert _approved_notifications() == []
    with tools.use_member(two_adults["Vineeth"]):
        assert len(_approved_notifications()) == 1


def test_the_notification_route_is_addressed_over_http(client, two_adults):
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(datetime.date.today().isoformat(), "Chili", slot="dinner", weekly_plan_id=plan_id)

    _sign_in(client)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Emily"]})
    client.post(f"/api/week/{_week_start()}/approve", json={"approved_by": ""})
    mine = client.get("/api/notifications").json()
    assert not [n for n in mine["notifications"] if n["type"] == "week_approved"]

    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})
    theirs = client.get("/api/notifications").json()
    assert [n["title"] for n in theirs["notifications"] if n["type"] == "week_approved"] == ["Emily approved the week"]


def test_reopening_clears_the_approver_id(two_adults):
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    with tools.use_member(two_adults["Emily"]):
        tools.approve_weekly_plan(plan_id)
    tools.reopen_weekly_plan(plan_id)
    row = get_conn().execute(
        "SELECT approved_by, approved_by_member_id FROM weekly_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    assert row["approved_by"] == ""
    assert row["approved_by_member_id"] is None


# ---------- the screen (source markers — shell.js has no JS harness; see
# tests/test_grocery_steps.py's docstring for what a marker is worth) ----------

def _in(needle: str, haystack: str, where: str) -> None:
    assert needle in haystack, (
        f"Expected to find {needle!r} in static/{where}. This is part of the "
        "\"Who's this?\" screen (per-adult login, slice 1, 2026-09-11). If the "
        "change is deliberate, update this test in the same commit and say why."
    )


def test_the_shell_asks_before_any_tab_renders():
    boot = SHELL_JS[SHELL_JS.index("async function checkOnboarding"):]
    boot = boot[: boot.index("activateTab(currentTabKey(), false)")]
    _in("await ensureWhoPicked();", boot, "shell.js")
    _in("if (!data || !data.needs_pick) return;", SHELL_JS, "shell.js")
    _in("fetch('/api/whoami/pick'", SHELL_JS, "shell.js")


def test_the_screen_copy_is_the_approved_wording():
    _in("Who’s this?", SHELL_JS, "shell.js")
    _in("I’ll remember on this device.", SHELL_JS, "shell.js")
    _in("Never mind", SHELL_JS, "shell.js")
    _in("That didn’t save. Try tapping your name again.", SHELL_JS, "shell.js")


def test_the_preferences_row_offers_a_switch_only_when_there_is_a_choice():
    _in("if (!shellWho.member || shellWho.adults.length < 2) return '';", SHELL_JS, "shell.js")
    _in("You’re ' + escapeHtml(shellWho.member.name)", SHELL_JS, "shell.js")
    _in("Not you? Switch", SHELL_JS, "shell.js")
    _in("whoPrefsRowHtml() +", SHELL_JS, "shell.js")


def test_approving_skips_its_own_picker_when_the_session_knows():
    _in("var approvedBy = shellWho.member ? shellWho.member.name : '';", SHELL_JS, "shell.js")
    _in("if (!approvedBy && people.length > 1) {", SHELL_JS, "shell.js")


def test_the_rows_are_real_tap_targets_and_every_colour_is_a_token():
    _in(".who-row {", SHELL_CSS, "shell.css")
    _in("min-height: 64px;", SHELL_CSS, "shell.css")
    _in(".who-cancel {", SHELL_CSS, "shell.css")
    block = SHELL_CSS[SHELL_CSS.index('"Who\'s this?"'):]
    assert "#" not in block.replace("#who-screen", ""), "Rule 9: no literal hex outside theme.css"
