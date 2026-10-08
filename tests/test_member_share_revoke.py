"""
Revoking a member's self-service link has to actually shut it.

The link (/member-share/{token}) is public and can WRITE: whoever holds it
can read that person's allergies and notes, add a dietary restriction and
leave a note. The chat agent offers revoke_member_share_link /
regenerate_member_share_link for exactly the case that matters — "it was
shared by mistake" — and tells the Planner it "stops working immediately".

Three separate queries in app/tools/sharing.py carry `AND revoked = 0`
(the read, the restriction write, the note write), and until this file no
test ever revoked a link, so dropping any one of them left the suite green
while a revoked link kept reading or writing. Each test below fails with
that guard removed from its query.
"""
from __future__ import annotations

from app import tools
from app.db import get_conn


def _restrictions(name: str) -> list[str]:
    return [m for m in tools.list_members() if m["name"] == name][0]["dietary_restrictions"]


def _note_count() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM member_notes").fetchone()[0]
    conn.close()
    return n


def test_a_revoked_member_link_reads_and_writes_nothing(client):
    tools.add_member("Sam")
    token = tools.get_or_create_member_share_link("Sam")["token"]
    client.cookies.clear()
    assert client.post(f"/api/member-share/{token}/restriction", json={"restriction": "coeliac"}).status_code == 200
    assert client.post(f"/api/member-share/{token}/note", json={"note": "more soup"}).status_code == 200

    tools.revoke_member_share_link("Sam")

    assert client.get(f"/api/member-share/{token}").status_code == 404
    res = client.post(f"/api/member-share/{token}/restriction", json={"restriction": "shellfish"})
    assert res.status_code == 404
    assert _restrictions("Sam") == ["coeliac"], "a revoked link still wrote an allergy"
    res = client.post(f"/api/member-share/{token}/note", json={"note": "from a revoked link"})
    assert res.status_code == 404
    assert _note_count() == 1, "a revoked link still left a note"


def test_regenerating_retires_the_old_link_and_the_new_one_works(client):
    tools.add_member("Sam")
    old = tools.get_or_create_member_share_link("Sam")["token"]
    new = tools.regenerate_member_share_link("Sam")["token"]
    assert new != old
    # Asking for "the" link afterwards hands out the new one, never the old.
    assert tools.get_or_create_member_share_link("Sam")["token"] == new

    client.cookies.clear()
    assert client.get(f"/api/member-share/{old}").status_code == 404
    assert client.post(f"/api/member-share/{old}/restriction", json={"restriction": "nuts"}).status_code == 404
    assert client.post(f"/api/member-share/{old}/note", json={"note": "old link"}).status_code == 404
    assert _restrictions("Sam") == [] and _note_count() == 0

    assert client.get(f"/api/member-share/{new}").json()["member_name"] == "Sam"
    assert client.post(f"/api/member-share/{new}/restriction", json={"restriction": "nuts"}).status_code == 200
    assert _restrictions("Sam") == ["nuts"]
