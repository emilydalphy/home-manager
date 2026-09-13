"""
The photographs a recipe was read from (Loop Board: "Add a recipe by
photographing the page of a cookbook — and the book is cited", 2026-09-13).

This is the first time the app keeps an image, so the shape is deliberately
the smallest one that is safe:

- Files live in ONE directory beside the database — `<dirname(DB_PATH)>/
  recipe_photos/` (or RECIPE_PHOTOS_DIR) — so on Railway they sit on the
  same persistent volume the database does and survive a redeploy for the
  same reason it does. Inside it, one folder per household, and the file
  name carries the recipe id and the page number: `<recipe_id>-<n>.jpg`.
- Nothing is ever served by path. The one route that hands a photo back
  looks the row up in `recipe_photos` UNDER THE SESSION'S HOUSEHOLD and
  serves the file that row names — so another household's photo is a 404
  even if its recipe id and page number are guessed. The household folder
  is a second wall behind the first: a row lookup that somehow passed
  would still resolve inside the caller's own folder.
- A photo is stashed as PENDING when the page is read (the draft goes back
  for review; nothing is a recipe yet) under a random token, and attached
  to the recipe only when the household saves. Pending files that were
  never saved are swept after a day. A bad read stashes nothing.
- No server-side resampling: there is no imaging library in this app and
  adding one for this would be a dependency decision, not a code change.
  The phone shrinks the photo before upload (static/shell.js,
  shrinkPhotoForUpload — long edge 1600px, JPEG), and the server holds a
  hard byte cap and checks the bytes really are an image before keeping
  them. Sizes are therefore a few hundred KB per page, not the 3-8 MB a
  camera writes.
"""
from __future__ import annotations

import os
import re
import secrets
import time

from .db import DB_PATH, get_conn
from .tools._shared import household_id

# Hard limits. MAX_PHOTO_BYTES is well above what a shrunk page photo
# weighs (see the module docstring) and well below anything that would
# make the volume a concern; MAX_PHOTOS is a recipe that runs across a
# spread — one page or two, never an album.
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_PHOTOS = 2
PENDING_MAX_AGE_SECONDS = 24 * 60 * 60

_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,48}$")


class PhotoError(Exception):
    """A refusal with a sentence the household can be shown."""


def photos_dir() -> str:
    return os.environ.get("RECIPE_PHOTOS_DIR") or os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "recipe_photos")


def _household_dir(hid: int) -> str:
    # int() twice over: the household id is an integer from the signed
    # cookie, never a string a caller typed, and the path is built from
    # that integer alone.
    return os.path.join(photos_dir(), str(int(hid)))


def _pending_dir(hid: int) -> str:
    return os.path.join(_household_dir(hid), "pending")


def sniff_media_type(data: bytes) -> str | None:
    """The image type from its first bytes, or None. The browser's
    content-type header is a claim; this is the check."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def check_upload(data: bytes) -> str:
    """Validate one uploaded photo's bytes; returns its real media type.
    Raises PhotoError with the sentence to show."""
    if not data:
        raise PhotoError("That photo came through empty — try again.")
    if len(data) > MAX_PHOTO_BYTES:
        raise PhotoError("That photo's too large — try again and I'll shrink it, or pick a smaller one.")
    media_type = sniff_media_type(data)
    if media_type is None:
        raise PhotoError("I can only read a JPEG, PNG or WEBP photo of the page.")
    return media_type


def _valid_token(token: str) -> bool:
    return bool(token) and bool(_TOKEN_RE.match(token))


def stash_pending(data: bytes, media_type: str) -> str:
    """Keep an uploaded page photo until the household saves (or doesn't).
    Returns the token the save hands back."""
    hid = household_id()
    sweep_pending(hid)
    token = secrets.token_urlsafe(18)
    os.makedirs(_pending_dir(hid), exist_ok=True)
    with open(os.path.join(_pending_dir(hid), f"{token}.{_EXTENSIONS[media_type]}"), "wb") as f:
        f.write(data)
    return token


def _pending_file(hid: int, token: str) -> str | None:
    if not _valid_token(token):
        return None
    folder = _pending_dir(hid)
    for ext in _EXTENSIONS.values():
        path = os.path.join(folder, f"{token}.{ext}")
        if os.path.isfile(path):
            return path
    return None


def discard_pending(tokens: list[str]) -> None:
    hid = household_id()
    for token in tokens or []:
        path = _pending_file(hid, token)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


def sweep_pending(hid: int, max_age: float = PENDING_MAX_AGE_SECONDS) -> None:
    """Drop pending photos nobody saved. Runs on every stash, so the folder
    never grows past a day's abandoned reads."""
    folder = _pending_dir(hid)
    if not os.path.isdir(folder):
        return
    cutoff = time.time() - max_age
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def attach_pending(recipe_id: int, tokens: list[str]) -> list[dict]:
    """
    Move the pending photos onto the saved recipe, in the order given, and
    record them. A token that has expired or was never ours is skipped
    rather than failing the save — the recipe is the thing being saved;
    the photo is the receipt.
    """
    hid = household_id()
    kept: list[dict] = []
    conn = get_conn()
    try:
        position = 0
        for token in (tokens or [])[:MAX_PHOTOS]:
            src = _pending_file(hid, token)
            if not src:
                continue
            position += 1
            ext = src.rsplit(".", 1)[-1]
            media_type = next(m for m, e in _EXTENSIONS.items() if e == ext)
            filename = f"{int(recipe_id)}-{position}.{ext}"
            os.replace(src, os.path.join(_household_dir(hid), filename))
            size = os.path.getsize(os.path.join(_household_dir(hid), filename))
            conn.execute(
                "INSERT OR REPLACE INTO recipe_photos (household_id, recipe_id, position, filename, media_type, byte_size) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (hid, int(recipe_id), position, filename, media_type, size),
            )
            kept.append({"position": position, "url": photo_url(recipe_id, position), "media_type": media_type})
        conn.commit()
    finally:
        conn.close()
    return kept


def remove_household_photos(hid: int) -> None:
    """Delete every photo file a household has (reset_household.py, after
    it has wiped the rows). The rows are the index; without them the files
    would only be orphans on the volume."""
    import shutil
    shutil.rmtree(_household_dir(hid), ignore_errors=True)


def photo_url(recipe_id: int, position: int) -> str:
    return f"/api/recipes/{int(recipe_id)}/photos/{int(position)}"


def photo_urls_by_recipe() -> dict[int, list[str]]:
    """Every photo the household has, as {recipe_id: [url, ...]} in page
    order — one query for list_recipes to fold in."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT recipe_id, position FROM recipe_photos WHERE household_id = ? ORDER BY recipe_id, position",
        (household_id(),),
    ).fetchall()
    conn.close()
    out: dict[int, list[str]] = {}
    for r in rows:
        out.setdefault(r["recipe_id"], []).append(photo_url(r["recipe_id"], r["position"]))
    return out


def photo_file(recipe_id: int, position: int) -> tuple[str, str] | None:
    """(path, media_type) for one of THIS household's photos, or None. The
    row is looked up under the session's household; the path is then
    built inside that household's own folder — never from the request."""
    conn = get_conn()
    row = conn.execute(
        "SELECT filename, media_type FROM recipe_photos WHERE household_id = ? AND recipe_id = ? AND position = ?",
        (household_id(), int(recipe_id), int(position)),
    ).fetchone()
    conn.close()
    if not row:
        return None
    filename = os.path.basename(row["filename"])
    path = os.path.join(_household_dir(household_id()), filename)
    if not os.path.isfile(path):
        return None
    return path, row["media_type"]
