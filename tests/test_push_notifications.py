"""
Push notifications on the iPhone app (Loop Board "App Store: push
notifications on the iPhone app", 2026-09-27).

What is pinned here:

- push OFF (no APNS_* variables — the state every deploy is in until
  Emily's Apple Developer account exists): the morning text and the evening
  nudge go by text exactly as before, even for an adult with a phone
  registered, and nothing ever reaches the APNs transport;
- push ON: an adult with a registered phone and the switch on gets the
  morning note (tap -> Today) and the evening nudge (tap -> Cook) as a push
  with no web address in it, and no text; an adult with the app but no
  phone number still gets them; a push that doesn't go through falls back
  to text for anyone who has text on; a dead token is removed;
- the provider token is a real ES256 JWT (verified with the public key);
- the device table: registered against the session's adult, moved not
  duplicated, removed on sign-out, on remove-me and with the household;
- the routes, the startup loop, the ask's moment, the iPhone project.

Nothing here can reach Apple: conftest clears the APNS_* variables, and
every test that turns push on replaces app.push._transport with a stub.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from app import household_deletion, households, push, security, tools
from app.db import get_conn
from app.tools import digest
from conftest import household_today
from test_morning_text import _Sender, _adults, _local, _member_id, _rows, _seed_day

REPO = Path(__file__).resolve().parent.parent
TODAY = household_today()
ISO_TODAY = TODAY.isoformat()
TOKEN_A = "a" * 64
TOKEN_B = "b" * 64


# ---------- helpers ----------

_KEY = ec.generate_private_key(ec.SECP256R1())
_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode("ascii")


class _Apns:
    """A stub APNs: records every call, answers what it's told per token."""

    def __init__(self, answers=None):
        self.calls: list[dict] = []
        self.answers = answers or {}

    def __call__(self, token, headers, body):
        self.calls.append({"token": token, "headers": dict(headers), "body": json.loads(body)})
        return self.answers.get(token, (200, ""))


@pytest.fixture
def apns(monkeypatch):
    monkeypatch.setenv("APNS_KEY_ID", "ABC123DEFG")
    monkeypatch.setenv("APNS_TEAM_ID", "TEAM123456")
    monkeypatch.setenv("APNS_KEY_P8", _PEM)
    monkeypatch.setenv("APNS_TOPIC", "com.pomona.app")
    stub = _Apns()
    monkeypatch.setattr(push, "_transport", stub)
    push._forget_provider_token()
    yield stub
    push._forget_provider_token()


@pytest.fixture
def no_push(monkeypatch):
    for key in push.APNS_ENV:
        monkeypatch.delenv(key, raising=False)
    calls = []
    monkeypatch.setattr(push, "_transport", lambda *a: calls.append(a) or (200, ""))
    return calls


def _register(name: str, token: str = TOKEN_A) -> str:
    return push.register_device(_member_id(name), token)


def _devices(hid: int | None = None) -> list[dict]:
    conn = get_conn()
    try:
        if hid is None:
            rows = conn.execute("SELECT household_id, member_id, token FROM push_devices ORDER BY id").fetchall()
        else:
            rows = conn.execute(
                "SELECT household_id, member_id, token FROM push_devices WHERE household_id = ? ORDER BY id", (hid,)
            ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _plan_tonight_for_evening():
    tools.add_recipe("Lemon Chicken", ingredients=[{"item": "Chicken", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=25, default_servings=3)
    plan_id = tools.create_weekly_plan((TODAY - dt.timedelta(days=2)).isoformat())["weekly_plan_id"]
    tools.plan_meal(ISO_TODAY, "Lemon Chicken", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=False)


def _b64d(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


# ---------- push off: text exactly as before ----------

def test_push_is_off_without_the_apns_variables(no_push):
    assert push.configured() is False


def test_push_off_the_morning_text_goes_exactly_as_before_even_with_a_phone_registered(no_push, monkeypatch):
    monkeypatch.setenv("HOME_MANAGER_URL", "https://pomona.example")
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    _register("Emily")
    sender = _Sender()

    results = tools.run_morning_texts_once(now_utc=_local(7, 3), send=sender)

    assert [r["status"] for r in results] == ["ok"]
    assert len(sender.calls) == 1 and sender.calls[0][0] == "+14165550100"
    assert sender.calls[0][1].endswith("https://pomona.example/"), "the text keeps its link"
    assert no_push == [], "push off never reaches the transport"


def test_push_off_an_app_only_adult_gets_nothing_rather_than_an_error(no_push):
    _adults("Emily")
    _seed_day()
    _register("Emily")   # the app, but no number and no morning text
    assert tools.run_morning_texts_once(now_utc=_local(7, 3), send=_Sender()) == []
    assert _rows() == []


def test_push_off_the_evening_nudge_goes_by_text_as_before(no_push):
    _adults("Emily")
    _plan_tonight_for_evening()
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    _register("Emily")
    seen = []

    results = tools.run_evening_nudges_once(
        now_utc=_local(17, 35), send=lambda m, t: seen.append((m, t)) or {"status": "ok", "detail": ""}
    )

    assert [r["status"] for r in results] == ["ok"]
    assert seen[0][0]["phone"] == "+14165550100"
    assert no_push == []


def test_a_broken_push_module_never_stops_the_text(apns, monkeypatch):
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    _register("Emily")
    monkeypatch.setattr(push, "member_tokens", lambda *_: (_ for _ in ()).throw(RuntimeError("db gone")))
    sender = _Sender()

    results = tools.run_morning_texts_once(now_utc=_local(7, 3), send=sender)

    assert [r["status"] for r in results] == ["ok"]
    assert len(sender.calls) == 1
    assert apns.calls == []


# ---------- push on ----------

def test_the_morning_note_goes_by_push_instead_of_text(apns, monkeypatch):
    monkeypatch.setenv("HOME_MANAGER_URL", "https://pomona.example")
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    _register("Emily")
    sender = _Sender()

    results = tools.run_morning_texts_once(now_utc=_local(7, 3), send=sender)

    assert [r["status"] for r in results] == ["ok"]
    assert sender.calls == [], "push went through, so no text"
    (call,) = apns.calls
    assert call["token"] == TOKEN_A
    assert call["body"]["path"] == "/"
    body = call["body"]["aps"]["alert"]["body"]
    assert body.startswith("Tonight: Chicken Skewers.")
    assert "pomona.example" not in body, "no web address in a notification"
    assert call["headers"]["apns-topic"] == "com.pomona.app"
    assert call["headers"]["apns-push-type"] == "alert"
    assert call["headers"]["authorization"].startswith("bearer ")
    row = _rows()[0]
    assert row["status"] == "ok" and row["detail"].startswith("push 1/1")
    assert TOKEN_A not in row["detail"] and "Chicken" not in row["detail"]

    # Once a day still holds.
    assert tools.run_morning_texts_once(now_utc=_local(7, 9), send=sender) == []
    assert len(apns.calls) == 1


def test_an_app_only_adult_with_no_number_gets_the_morning_note(apns):
    _adults("Emily")
    _seed_day()
    _register("Emily")
    sender = _Sender()

    results = tools.run_morning_texts_once(now_utc=_local(7, 3), send=sender)

    assert [r["status"] for r in results] == ["ok"]
    assert len(apns.calls) == 1 and sender.calls == []


def test_a_push_that_fails_falls_back_to_text(apns):
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    _register("Emily")
    apns.answers[TOKEN_A] = (500, "InternalServerError")
    sender = _Sender()

    results = tools.run_morning_texts_once(now_utc=_local(7, 3), send=sender)

    assert [r["status"] for r in results] == ["ok"]
    assert len(apns.calls) == 1
    assert len(sender.calls) == 1 and sender.calls[0][0] == "+14165550100"


def test_a_failed_push_with_no_text_to_fall_back_on_is_recorded_as_failed(apns):
    _adults("Emily")
    _seed_day()
    _register("Emily")
    apns.answers[TOKEN_A] = (500, "InternalServerError")

    results = tools.run_morning_texts_once(now_utc=_local(7, 3), send=_Sender())

    assert [r["status"] for r in results] == ["failed"]
    assert "500" in _rows()[0]["detail"]


def test_a_dead_token_is_removed_and_the_text_still_goes(apns):
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    _register("Emily")
    apns.answers[TOKEN_A] = (410, "Unregistered")
    sender = _Sender()

    tools.run_morning_texts_once(now_utc=_local(7, 3), send=sender)

    assert _devices() == []
    assert len(sender.calls) == 1


def test_switching_notifications_off_goes_back_to_text(apns):
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    _register("Emily")
    push.set_member_push_on(_member_id("Emily"), False)
    sender = _Sender()

    tools.run_morning_texts_once(now_utc=_local(7, 3), send=sender)

    assert apns.calls == [] and len(sender.calls) == 1


def test_each_adult_gets_their_own_way(apns):
    """Emily has the app; Vineeth has only the text."""
    _adults()
    _seed_day()
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    tools.set_morning_text(phone="647-555-0199", on=True, name="Vineeth")
    _register("Emily")
    sender = _Sender()

    tools.run_morning_texts_once(now_utc=_local(7, 3), send=sender)

    assert [c["token"] for c in apns.calls] == [TOKEN_A]
    assert [to for to, _ in sender.calls] == ["+16475550199"]


def test_the_evening_nudge_goes_by_push_and_a_tap_opens_cook(apns, monkeypatch):
    monkeypatch.setenv("HOME_MANAGER_URL", "https://pomona.example")
    _adults("Emily")
    _plan_tonight_for_evening()
    _register("Emily")
    seen = []

    results = tools.run_evening_nudges_once(
        now_utc=_local(17, 35), send=lambda m, t: seen.append(m) or {"status": "ok", "detail": ""}
    )

    assert [r["status"] for r in results] == ["ok"]
    assert seen == []
    (call,) = apns.calls
    assert call["body"]["path"] == "/kitchen"
    assert call["body"]["aps"]["alert"]["body"].startswith("Tonight: Lemon Chicken")
    assert "pomona.example" not in call["body"]["aps"]["alert"]["body"]
    # Marked for the night: the next pass sends nothing.
    assert tools.run_evening_nudges_once(now_utc=_local(17, 45), send=lambda m, t: {"status": "ok"}) == []


def test_the_evening_nudge_falls_back_to_text_when_push_fails(apns):
    _adults("Emily")
    _plan_tonight_for_evening()
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    _register("Emily")
    apns.answers[TOKEN_A] = (503, "ServiceUnavailable")
    seen = []

    tools.run_evening_nudges_once(
        now_utc=_local(17, 35), send=lambda m, t: seen.append((m, t)) or {"status": "ok", "detail": ""}
    )

    assert len(apns.calls) == 1
    assert seen and seen[0][0]["phone"] == "+14165550100"


def test_the_evening_switch_still_gates_the_push(apns):
    _adults("Emily")
    _plan_tonight_for_evening()
    _register("Emily")
    tools.set_evening_nudge_for_member(_member_id("Emily"), False)
    assert tools.run_evening_nudges_once(now_utc=_local(17, 35), send=lambda m, t: {"status": "ok"}) == []
    assert apns.calls == []


# ---------- the provider token ----------

def test_the_provider_token_is_an_es256_jwt_apple_can_verify(apns):
    token = push.provider_token(now=1_800_000_000)
    header_b64, claims_b64, sig_b64 = token.split(".")
    assert json.loads(_b64d(header_b64)) == {"alg": "ES256", "kid": "ABC123DEFG"}
    assert json.loads(_b64d(claims_b64)) == {"iss": "TEAM123456", "iat": 1_800_000_000}
    raw = _b64d(sig_b64)
    assert len(raw) == 64
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    _KEY.public_key().verify(der, f"{header_b64}.{claims_b64}".encode(), ec.ECDSA(hashes.SHA256()))


def test_the_provider_token_is_reused_then_refreshed(apns):
    first = push.provider_token(now=1_800_000_000)
    assert push.provider_token(now=1_800_000_000 + 60) == first
    assert push.provider_token(now=1_800_000_000 + push.TOKEN_REFRESH_SECONDS + 1) != first


def test_a_one_line_key_with_escaped_newlines_is_read(apns, monkeypatch):
    monkeypatch.setenv("APNS_KEY_P8", _PEM.replace("\n", "\\n"))
    push._forget_provider_token()
    assert push.provider_token(now=1_800_000_000).count(".") == 2


def test_the_sandbox_switch_picks_the_gateway(monkeypatch):
    monkeypatch.delenv("APNS_SANDBOX", raising=False)
    assert push.apns_host() == "api.push.apple.com"
    monkeypatch.setenv("APNS_SANDBOX", "1")
    assert push.apns_host() == "api.sandbox.push.apple.com"


def test_a_tap_can_only_land_on_the_shells_own_screens(apns):
    body = json.loads(push._payload("hello", "https://evil.example/"))
    assert body["path"] == "/"


# ---------- devices: where a token lives and when it goes ----------

def test_registering_needs_to_know_who_you_are(signed_in):
    _adults()   # two adults, nobody picked
    res = signed_in.post("/api/push/devices", json={"token": TOKEN_A})
    assert res.status_code == 400
    assert _devices() == []


def test_a_token_that_isnt_one_is_refused(signed_in):
    _adults("Emily")
    assert signed_in.post("/api/push/devices", json={"token": "not-a-token"}).status_code == 400
    assert _devices() == []


def test_registering_saves_the_phone_against_the_signed_in_adult(signed_in):
    _adults("Emily")
    res = signed_in.post("/api/push/devices", json={"token": TOKEN_A})
    assert res.status_code == 200, res.text
    assert res.json()["devices"] == 1 and res.json()["on"] is True
    assert _devices() == [{"household_id": 1, "member_id": _member_id("Emily"), "token": TOKEN_A}]
    assert signed_in.cookies.get(push.DEVICE_COOKIE)
    assert TOKEN_A not in signed_in.cookies.get(push.DEVICE_COOKIE)

    # The same phone again (every launch) is still one row.
    signed_in.post("/api/push/devices", json={"token": TOKEN_A})
    assert len(_devices()) == 1


def test_the_same_phone_signed_into_another_household_moves_rather_than_doubles():
    other = households.create_household("The Others", "other-household-passphrase")
    _adults("Emily")
    _register("Emily")
    with tools.use_household(other):
        tools.add_member("Sam")
        tools.set_member_age_group("Sam", "adult")
        conn = get_conn()
        sam = conn.execute("SELECT id FROM members WHERE household_id = ?", (other,)).fetchone()["id"]
        conn.close()
        push.register_device(sam, TOKEN_A)
    assert _devices() == [{"household_id": other, "member_id": sam, "token": TOKEN_A}]


def test_signing_out_removes_this_phone_and_only_this_phone(signed_in):
    _adults("Emily")
    signed_in.post("/api/push/devices", json={"token": TOKEN_A})
    push.register_device(_member_id("Emily"), TOKEN_B)   # her other phone

    res = signed_in.get("/logout", follow_redirects=False)

    assert res.status_code == 303
    assert [d["token"] for d in _devices()] == [TOKEN_B]
    assert push.DEVICE_COOKIE not in signed_in.cookies


def test_signing_out_in_a_browser_that_never_registered_is_unchanged(signed_in):
    _adults("Emily")
    push.register_device(_member_id("Emily"), TOKEN_B)
    assert signed_in.get("/logout", follow_redirects=False).status_code == 303
    assert len(_devices()) == 1


def test_remove_me_takes_that_adults_phones_and_leaves_the_others(client):
    hid = households.create_household("The Beta Testers", "beta-tester-passphrase")
    client.post("/login", data={"password": "beta-tester-passphrase", "next": "/"}, follow_redirects=False)
    with tools.use_household(hid):
        julia = tools.add_member("Julia")["member_id"]
        tools.set_member_age_group("Julia", "adult")
        sam = tools.add_member("Sam")["member_id"]
        tools.set_member_age_group("Sam", "adult")
        push.register_device(sam, TOKEN_B)
    assert client.post("/api/whoami/pick", json={"member_id": julia}).status_code == 200
    assert client.post("/api/push/devices", json={"token": TOKEN_A}).status_code == 200

    res = client.post("/api/household/remove-me", json={})

    assert res.status_code == 200, res.text
    assert _devices(hid) == [{"household_id": hid, "member_id": sam, "token": TOKEN_B}]
    assert push.DEVICE_COOKIE not in client.cookies


def test_deleting_the_household_takes_every_phone_in_it_and_no_one_elses(client):
    hid = households.create_household("The Beta Testers", "beta-tester-passphrase")
    _adults("Emily")
    push.register_device(_member_id("Emily"), TOKEN_B)   # household 1's phone
    client.post("/login", data={"password": "beta-tester-passphrase", "next": "/"}, follow_redirects=False)
    with tools.use_household(hid):
        tools.add_member("Julia")
        tools.set_member_age_group("Julia", "adult")
    assert client.post("/api/push/devices", json={"token": TOKEN_A}).status_code == 200
    conn = get_conn()
    assert "push_devices" in household_deletion.household_tables(conn)
    conn.close()

    res = client.post("/api/household/delete", json={"confirm": "DELETE"})

    assert res.status_code == 200, res.text
    assert _devices(hid) == []
    assert [d["token"] for d in _devices()] == [TOKEN_B]
    assert push.DEVICE_COOKIE not in client.cookies


# ---------- the settings route and the ask's moment ----------

def test_the_ask_waits_for_an_approved_week(signed_in):
    _adults("Emily")
    before = signed_in.get("/api/push").json()
    assert before["ask_ready"] is False and before["member_id"] == _member_id("Emily")
    plan_id = tools.create_weekly_plan(ISO_TODAY)["weekly_plan_id"]
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()
    assert signed_in.get("/api/push").json()["ask_ready"] is True


def test_the_switch_is_the_signed_in_adults_own(signed_in):
    _adults("Emily")
    res = signed_in.post("/api/push/preferences", json={"on": False})
    assert res.status_code == 200 and res.json()["on"] is False
    assert push.member_push_on(_member_id("Emily")) is False
    assert signed_in.post("/api/push/preferences", json={"on": True}).json()["on"] is True


def test_the_switch_needs_to_know_who_you_are(signed_in):
    _adults()
    assert signed_in.post("/api/push/preferences", json={"on": False}).status_code == 400


def test_the_push_routes_are_signed_in_only(client):
    assert client.get("/api/push", follow_redirects=False).status_code in (401, 303, 307)
    assert client.post("/api/push/devices", json={"token": TOKEN_A}, follow_redirects=False).status_code in (401, 303, 307, 403)


# ---------- the startup loop ----------

def test_the_startup_loop_starts_with_push_keys_and_no_twilio(apns, monkeypatch):
    import asyncio
    from app import main as app_main

    for key in digest.TWILIO_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("DISABLE_MORNING_TEXT", raising=False)
    created = []
    monkeypatch.setattr(app_main.asyncio, "create_task", lambda coro: created.append(coro) or coro.close())
    asyncio.run(app_main.start_morning_text_loop())
    assert len(created) == 1


# ---------- the page side (source markers) ----------

SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")
PUSH_JS = (REPO / "static" / "push.js").read_text(encoding="utf-8")


def test_push_js_is_loaded_only_inside_the_app():
    assert "push.js" not in " ".join(re.findall(r'<script src="([^"]+)"', SHELL_HTML))
    loader = SHELL_JS[SHELL_JS.index("function loadPushModule"):]
    loader = loader[: loader.index("\n  }\n")]
    assert "isNativeApp()" in loader and "'/static/push.js'" in loader


def test_the_ask_comes_after_approving_and_never_unconditionally_at_launch():
    approve = SHELL_JS[SHELL_JS.index("async function submitWeekApproval"):]
    approve = approve[: approve.index("\n  }\n")]
    assert "offerPush" in approve
    after_load = SHELL_JS[SHELL_JS.index("async function pushAfterLoad"):]
    after_load = after_load[: after_load.index("\n  }\n")]
    assert "settings.ask_ready" in after_load
    offer = SHELL_JS[SHELL_JS.index("async function offerPush"):]
    offer = offer[: offer.index("\n  }\n")]
    assert "pushAsked()" in offer and "!== 'prompt'" in offer


def test_push_js_talks_to_the_server_only_through_api_js():
    assert "fetch(" not in PUSH_JS
    assert "Api.json('/api/push/devices'" in PUSH_JS
    assert "Api.json('/api/push/preferences'" in PUSH_JS


def test_the_ask_dialog_is_a_real_dialog_with_plain_buttons():
    assert 'id="push-ask-dialog" hidden data-motion="dialog" role="dialog"' in SHELL_HTML
    assert ">Not now</button>" in SHELL_HTML and ">Turn on</button>" in SHELL_HTML


_needs_node = pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node runs push.js")

_PUSH_HARNESS = """
const calls = [], listeners = {}, assigned = [];
let perm = 'granted';
global.window = {
  location: { pathname: '/', assign: (p) => assigned.push(p) },
  Capacitor: { Plugins: { PushNotifications: {
    addListener: (name, fn) => { listeners[name] = fn; },
    checkPermissions: () => Promise.resolve({ receive: perm }),
    requestPermissions: () => Promise.resolve({ receive: 'granted' }),
    register: () => { calls.push('register'); return Promise.resolve(); },
  } } },
};
var Api = { json: (path, opts) => { calls.push([path, opts.body]); return Promise.resolve({}); } };
"""


@_needs_node
def test_push_js_registers_saves_the_token_and_opens_only_the_shells_screens():
    import nodeharness

    script = _PUSH_HARNESS + PUSH_JS + """
(async () => {
  await window.PomonaPush.ready;
  await new Promise(r => setTimeout(r, 0));
  listeners.registration({ value: 'abc123' });
  listeners.pushNotificationActionPerformed({ notification: { data: { path: '/kitchen' } } });
  listeners.pushNotificationActionPerformed({ notification: { data: { path: 'https://evil.example/' } } });
  window.location.pathname = '/grocery';
  listeners.pushNotificationActionPerformed({ notification: { data: {} } });
  console.log(JSON.stringify({ calls, assigned }));
})().catch(e => { console.error(e); process.exit(1); });
"""
    res = nodeharness.run_node(script)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["calls"][0] == "register", "already allowed: register on launch"
    assert ["/api/push/devices", {"token": "abc123", "platform": "ios"}] in out["calls"]
    # /kitchen opens Cook; a foreign address is refused (stays on Today,
    # which it is already on); nothing named goes to Today.
    assert out["assigned"] == ["/kitchen", "/"]


# ---------- the iPhone project ----------

IOS = REPO / "ios-app"


def test_the_ios_project_has_the_push_capability():
    ent = (IOS / "ios" / "App" / "App" / "App.entitlements").read_text()
    assert re.search(r"<key>aps-environment</key>\s*<string>development</string>", ent)
    pbx = (IOS / "ios" / "App" / "App.xcodeproj" / "project.pbxproj").read_text()
    assert pbx.count("CODE_SIGN_ENTITLEMENTS = App/App.entitlements;") == 2
    delegate = (IOS / "ios" / "App" / "App" / "AppDelegate.swift").read_text()
    assert ".capacitorDidRegisterForRemoteNotifications" in delegate
    assert ".capacitorDidFailToRegisterForRemoteNotifications" in delegate


def test_the_push_plugin_is_pinned_exactly():
    pkg = json.loads((IOS / "package.json").read_text())
    assert pkg["dependencies"]["@capacitor/push-notifications"] == "8.1.2"
    spm = (IOS / "ios" / "App" / "CapApp-SPM" / "Package.swift").read_text()
    assert "CapacitorPushNotifications" in spm


def test_no_apns_key_is_in_the_repo():
    for path in [*REPO.glob("app/**/*.py"), *REPO.glob("ios-app/**/*.json"), *REPO.glob("*.txt"), *REPO.glob("ios-app/ios/**/*.plist")]:
        if "node_modules" in path.parts:
            continue
        assert "BEGIN PRIVATE KEY" not in path.read_text(errors="ignore").replace(
            '"-----BEGIN PRIVATE KEY-----\\n"', ""
        ), path


def test_the_new_dependencies_are_pinned():
    req = (REPO / "requirements.txt").read_text()
    for pin in ("httpx==", "h2==", "cryptography=="):
        assert pin in req
