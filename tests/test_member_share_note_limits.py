"""
The member self-service link's two writes have a floor and a ceiling
(2026-10-10, overnight defect hunt; coordinator's second job).

/api/member-share/{token}/note and /restriction are public writes for
whoever holds the link, and a note reaches the chat agent through
get_member_notes. Before: a blank note saved as an empty row, and a
note of any size was stored whole. Now both are trimmed, a blank one is a
400, and anything over the cap (the note: feedback's 2000 characters; a
restriction: 200) is a 400 with nothing written.
"""
from app import tools
from app.db import get_conn
from app.main import MEMBER_SHARE_NOTE_MAX, MEMBER_SHARE_RESTRICTION_MAX


def _token():
    tools.add_member("Priya")
    return tools.get_or_create_member_share_link("Priya")["token"]


def _notes():
    conn = get_conn()
    rows = conn.execute("SELECT note FROM member_notes ORDER BY id").fetchall()
    conn.close()
    return [r["note"] for r in rows]


def _restrictions():
    return next(m for m in tools.list_members() if m["name"] == "Priya")["dietary_restrictions"]


def test_a_blank_note_is_refused_and_nothing_is_saved(client):
    """CATCH. Red before: 200 and an empty note on file."""
    token = _token()
    res = client.post(f"/api/member-share/{token}/note", json={"note": "   "})
    assert res.status_code == 400
    assert _notes() == []


def test_a_note_over_the_cap_is_refused_whole(client):
    """CATCH. Red before: 200 and the whole thing stored."""
    token = _token()
    res = client.post(f"/api/member-share/{token}/note", json={"note": "x" * (MEMBER_SHARE_NOTE_MAX + 1)})
    assert res.status_code == 400
    assert str(MEMBER_SHARE_NOTE_MAX) in res.json()["detail"]
    assert _notes() == []


def test_a_note_at_the_cap_is_saved_trimmed(client):
    """GUARD. The cap itself fits, and the padding goes."""
    token = _token()
    text = "y" * MEMBER_SHARE_NOTE_MAX
    res = client.post(f"/api/member-share/{token}/note", json={"note": f"  {text}\n"})
    assert res.status_code == 200
    assert _notes() == [text]


def test_a_restriction_over_the_cap_or_blank_is_refused(client):
    """CATCH (the long one; blank was already a silent no-op)."""
    token = _token()
    long = client.post(f"/api/member-share/{token}/restriction",
                       json={"restriction": "z" * (MEMBER_SHARE_RESTRICTION_MAX + 1)})
    blank = client.post(f"/api/member-share/{token}/restriction", json={"restriction": " "})
    assert (long.status_code, blank.status_code) == (400, 400)
    assert _restrictions() == []


def test_an_ordinary_restriction_is_saved_trimmed(client):
    """GUARD."""
    token = _token()
    res = client.post(f"/api/member-share/{token}/restriction", json={"restriction": "  shellfish allergy "})
    assert res.status_code == 200
    assert res.json()["dietary_restrictions"] == ["shellfish allergy"]
