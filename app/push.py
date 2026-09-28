"""
Push notifications on the iPhone app (Loop Board "App Store: push
notifications on the iPhone app", 2026-09-27).

The morning note and the evening cook nudge already go by text
(app/tools/digest.py). This module is the second way they can arrive: a
notification on the lock screen of the iPhone app, for an adult who has the
app and said yes to notifications. Text stays the fallback for everyone
else, and for anyone whose push didn't go through.

Three pieces:

- **Devices.** ``push_devices`` holds one row per phone: the APNs device
  token, the household and the adult it belongs to, and a random
  ``device_key`` the phone also carries as a cookie (``DEVICE_COOKIE``), so
  signing out on that phone can remove exactly that phone's row (the token
  itself never goes into a cookie). A row goes when the person signs out on
  that phone (``forget_device``), when they take themselves out of the
  household (household_deletion.remove_member — the row's member_id is NOT
  NULL, so the generic member pass deletes it), and when the household is
  deleted (household_deletion finds every table with a household_id). APNs
  telling us a token is dead (410, or BadDeviceToken) removes it too.
- **The switch.** ``members.push_on`` — per adult, on by default: the phone's
  own "Allow notifications" is the explicit yes, and Preferences has the
  way to say no to Pomona without going into iPhone Settings.
- **The sender.** APNs over HTTP/2 with token-based auth: a short ES256
  JWT signed with the ``.p8`` key from the environment. Nothing about the
  key is in the repo. The variables:

      APNS_KEY_ID     the key's 10-character id (Apple Developer › Keys)
      APNS_TEAM_ID    the team id
      APNS_KEY_P8     the .p8 file's contents (PEM; "\\n" escapes accepted)
      APNS_TOPIC      the app's bundle id (com.pomona.app today)
      APNS_SANDBOX    "1" to send to the development gateway (builds run
                      from Xcode); anything else, the production one
                      (TestFlight and the App Store)

  If any of the first four is missing — or the libraries the sender needs
  aren't installed — ``configured()`` is False and push is simply off:
  every caller then does exactly what it did before push existed.

Why this module is not in ``app/tools/``: everything there is reachable
from the chat agent, and neither registering a device nor sending to one is
something a sentence in chat should be able to do.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from typing import Callable

from .db import get_conn
from .tools._shared import household_id

logger = logging.getLogger("home_manager")

APNS_ENV = ("APNS_KEY_ID", "APNS_TEAM_ID", "APNS_KEY_P8", "APNS_TOPIC")
PRODUCTION_HOST = "api.push.apple.com"
SANDBOX_HOST = "api.sandbox.push.apple.com"

# The cookie that ties this phone to its push_devices row, so /logout can
# remove the right one. Random, not the token.
DEVICE_COOKIE = "pomona_push_device"
DEVICE_COOKIE_MAX_AGE = 400 * 24 * 3600

# Apple wants the provider token refreshed no more than once every 20
# minutes and no less than once an hour; 50 minutes sits inside both.
TOKEN_REFRESH_SECONDS = 50 * 60

# An APNs device token is hex (64 characters today; Apple has said it may
# grow). Anything else is refused before it is stored.
_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{32,200}$")

# Where a tap may land. The shell's own routes — a notification is never a
# way to send the app somewhere else.
ALLOWED_PATHS = frozenset({"/", "/week", "/grocery", "/kitchen"})

# Reasons APNs gives for a token that will never work again.
_DEAD_TOKEN_REASONS = frozenset({"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"})


# ---------- configuration ----------

def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _libraries_present() -> bool:
    try:
        import cryptography  # noqa: F401
        import h2  # noqa: F401
        import httpx  # noqa: F401
    except Exception:
        return False
    return True


def configured() -> bool:
    """Is push on for this process? All four variables, and the libraries."""
    return all(_env(k) for k in APNS_ENV) and _libraries_present()


def apns_host() -> str:
    return SANDBOX_HOST if _env("APNS_SANDBOX").lower() in ("1", "true", "yes") else PRODUCTION_HOST


# ---------- the provider token (ES256 JWT) ----------

_jwt_lock = threading.Lock()
_jwt_cache: dict = {"token": "", "issued": 0.0, "fingerprint": ""}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _pem_text(raw: str) -> bytes:
    """The .p8 as PEM bytes. Railway variables are one line, so a pasted
    key often arrives with literal "\\n" in it; a bare base64 body (no
    BEGIN line) is wrapped the way the file would have it."""
    text = (raw or "").strip().replace("\\n", "\n")
    if "BEGIN" not in text:
        body = re.sub(r"\s+", "", text)
        lines = [body[i:i + 64] for i in range(0, len(body), 64)]
        text = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(lines) + "\n-----END PRIVATE KEY-----"
    return text.encode("ascii")


def provider_token(now: float | None = None) -> str:
    """The bearer token APNs wants, cached and refreshed every 50 minutes."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    now = time.time() if now is None else now
    key_id, team_id, p8 = _env("APNS_KEY_ID"), _env("APNS_TEAM_ID"), _env("APNS_KEY_P8")
    # Changing any of the three (a rotated key) must not keep serving the
    # old token for up to 50 minutes. A hash, so the key never sits here.
    fingerprint = _b64url(hashlib.sha256(f"{key_id}|{team_id}|{p8}".encode()).digest())
    with _jwt_lock:
        if (
            _jwt_cache["token"]
            and _jwt_cache["fingerprint"] == fingerprint
            and now - _jwt_cache["issued"] < TOKEN_REFRESH_SECONDS
        ):
            return _jwt_cache["token"]
        key = serialization.load_pem_private_key(_pem_text(p8), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("APNS_KEY_P8 is not an elliptic-curve key")
        header = _b64url(json.dumps({"alg": "ES256", "kid": key_id}, separators=(",", ":")).encode())
        claims = _b64url(json.dumps({"iss": team_id, "iat": int(now)}, separators=(",", ":")).encode())
        signing_input = f"{header}.{claims}".encode("ascii")
        r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
        signature = _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        token = f"{header}.{claims}.{signature}"
        _jwt_cache.update(token=token, issued=now, fingerprint=fingerprint)
        return token


def _forget_provider_token() -> None:
    with _jwt_lock:
        _jwt_cache.update(token="", issued=0.0, fingerprint="")


# ---------- the transport (the one place that talks to Apple) ----------

# (device_token, headers, body) -> (http_status, apns_reason). Tests replace
# this; nothing else in the suite can reach Apple.
Transport = Callable[[str, dict, bytes], "tuple[int, str]"]


def _httpx_transport(device_token: str, headers: dict, body: bytes) -> tuple[int, str]:
    import httpx

    url = f"https://{apns_host()}/3/device/{device_token}"
    with httpx.Client(http2=True, timeout=15.0) as client:
        resp = client.post(url, headers=headers, content=body)
    reason = ""
    if resp.status_code != 200:
        try:
            reason = str((resp.json() or {}).get("reason") or "")
        except Exception:
            reason = ""
    return resp.status_code, reason


_transport: Transport = _httpx_transport


# ---------- devices ----------

def valid_token(token: str) -> bool:
    return bool(_TOKEN_RE.match(token or ""))


def register_device(member_id: int, token: str, platform: str = "ios") -> str:
    """
    Save this phone's token against this adult in this household, and
    return the device_key the phone keeps as a cookie. A token already on
    record (the same phone, signed in again — perhaps as someone else, or
    in another household) moves to whoever registered it last: one phone,
    one row.
    """
    token = (token or "").strip()
    if not valid_token(token):
        raise ValueError("That isn't a notification token.")
    hid = household_id()
    conn = get_conn()
    try:
        row = conn.execute("SELECT device_key FROM push_devices WHERE token = ?", (token,)).fetchone()
        device_key = row["device_key"] if row else secrets.token_urlsafe(24)
        conn.execute(
            "INSERT INTO push_devices (household_id, member_id, token, platform, device_key) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(token) DO UPDATE SET household_id = excluded.household_id, "
            "member_id = excluded.member_id, platform = excluded.platform, "
            "last_seen_at = datetime('now')",
            (hid, int(member_id), token, (platform or "ios")[:16], device_key),
        )
        conn.commit()
    finally:
        conn.close()
    return device_key


def forget_device(device_key: str | None) -> int:
    """Remove the one phone that carries this device_key (signing out)."""
    if not device_key:
        return 0
    conn = get_conn()
    try:
        n = conn.execute("DELETE FROM push_devices WHERE device_key = ?", (device_key,)).rowcount
        conn.commit()
    finally:
        conn.close()
    return n


def _forget_token(token: str) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM push_devices WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def member_tokens(member_id: int) -> list[str]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT token FROM push_devices WHERE household_id = ? AND member_id = ? ORDER BY id",
            (household_id(), int(member_id)),
        ).fetchall()
    finally:
        conn.close()
    return [r["token"] for r in rows]


def member_push_on(member_id: int) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT push_on FROM members WHERE id = ? AND household_id = ?",
            (int(member_id), household_id()),
        ).fetchone()
    finally:
        conn.close()
    return bool(row and row["push_on"])


def set_member_push_on(member_id: int, on: bool) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE members SET push_on = ? WHERE id = ? AND household_id = ?",
            (1 if on else 0, int(member_id), household_id()),
        )
        conn.commit()
    finally:
        conn.close()


def can_push(member_id: int) -> bool:
    """
    Would a push to this adult be attempted right now? Push configured,
    their switch on, and at least one phone on record. Never raises: any
    failure reads as "no", which leaves the caller on text exactly as
    before push existed.
    """
    try:
        return configured() and member_push_on(member_id) and bool(member_tokens(member_id))
    except Exception:
        logger.exception("Push: could not read member %s's devices; using text", member_id)
        return False


# ---------- sending ----------

def _payload(body: str, path: str, title: str = "") -> bytes:
    alert: dict = {"body": body}
    if title:
        alert["title"] = title
    safe_path = path if path in ALLOWED_PATHS else "/"
    return json.dumps(
        {"aps": {"alert": alert, "sound": "default"}, "path": safe_path},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def send_to_member(member_id: int, body: str, path: str = "/", title: str = "", ttl_seconds: int = 6 * 3600) -> dict:
    """
    One notification to every phone this adult has registered. Returns
    {status, detail, delivered}; status is 'ok' when at least one phone
    took it, otherwise 'failed' or 'skipped-…' — and never raises, so the
    caller can fall back to text on anything but 'ok'. `detail` names
    counts and Apple's reason, never a token or the words sent.
    """
    try:
        if not configured():
            return {"status": "skipped-no-keys", "detail": "APNS_* unset", "delivered": 0}
        if not member_push_on(member_id):
            return {"status": "skipped-off", "detail": "notifications off", "delivered": 0}
        tokens = member_tokens(member_id)
        if not tokens:
            return {"status": "skipped-no-device", "detail": "no phone registered", "delivered": 0}
        payload = _payload(body, path, title)
        delivered, reasons = 0, []
        for token in tokens:
            try:
                headers = {
                    "authorization": f"bearer {provider_token()}",
                    "apns-topic": _env("APNS_TOPIC"),
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                    "apns-expiration": str(int(time.time()) + int(ttl_seconds)),
                }
                status, reason = _transport(token, headers, payload)
            except Exception as e:
                reasons.append(type(e).__name__)
                continue
            if status == 200:
                delivered += 1
                continue
            reasons.append(f"{status} {reason}".strip())
            if status == 410 or reason in _DEAD_TOKEN_REASONS:
                _forget_token(token)
            elif status == 403 and reason in ("ExpiredProviderToken", "InvalidProviderToken"):
                _forget_provider_token()
        detail = f"push {delivered}/{len(tokens)}" + (f" ({', '.join(reasons)})" if reasons else "")
        return {"status": "ok" if delivered else "failed", "detail": detail[:160], "delivered": delivered}
    except Exception as e:  # never take the caller down
        logger.exception("Push to member %s failed", member_id)
        return {"status": "failed", "detail": f"push {type(e).__name__}"[:160], "delivered": 0}


def settings_for(member: dict | None) -> dict:
    """What the Preferences row and the permission ask read."""
    hid = household_id()
    conn = get_conn()
    try:
        approved = conn.execute(
            "SELECT 1 FROM weekly_plans WHERE household_id = ? AND status = 'approved' LIMIT 1", (hid,)
        ).fetchone() is not None
    finally:
        conn.close()
    if not member:
        return {"configured": configured(), "member_id": None, "on": False, "devices": 0, "ask_ready": approved}
    return {
        "configured": configured(),
        "member_id": member["id"],
        "on": member_push_on(member["id"]),
        "devices": len(member_tokens(member["id"])),
        # The permission ask waits for a week to have been approved: that is
        # when there is a morning note and a dinner nudge worth sending.
        "ask_ready": approved,
    }


__all__ = [
    "APNS_ENV",
    "ALLOWED_PATHS",
    "DEVICE_COOKIE",
    "DEVICE_COOKIE_MAX_AGE",
    "apns_host",
    "can_push",
    "configured",
    "forget_device",
    "member_push_on",
    "member_tokens",
    "provider_token",
    "register_device",
    "send_to_member",
    "set_member_push_on",
    "settings_for",
    "valid_token",
]
