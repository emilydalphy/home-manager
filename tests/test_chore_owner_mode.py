"""
Every chore has a chosen owner — owned / shared / whoever.

Loop Board "Chores v1: Every chore has a chosen owner" (Emily, 2026-09-11).
Before this, who did a chore was only ever implied by how many people sat
in its rotation (rotation_member_ids_json, round-robined by
generate_chore_schedule). Now a chore says so outright — `chores.mode` —
and the two older columns say who:

    owned    one named person, every time (the default)
    shared   the named people take turns (the round-robin as before)
    whoever  nobody in particular; first to tick it

This file covers the data model and engine, the chat tools, the starter
list and its save route, and the one place a screen already prints a chore
row (the hidden Now card). The Plan | Chores screen is a later card.
"""
from __future__ import annotations

import datetime
import json
import types
from pathlib import Path

import pytest

from app import agent, tools
from app.db import _migrate_chore_modes, _run_migrations, get_conn

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _adult(name: str) -> int:
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, "Adult")
    return member_id


@pytest.fixture
def two_adults():
    return {"Emily": _adult("Emily"), "Vineeth": _adult("Vineeth")}


def _chore_row(chore_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM chores WHERE id = ?", (chore_id,)).fetchone()
    conn.close()
    return row


def _instances(chore_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, assignee_id, status, completed_by_member_id, due_date FROM chore_instances "
        "WHERE chore_id = ? ORDER BY due_date ASC, id ASC",
        (chore_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _legacy_chore(name: str, rotation: list[int], default: int | None = None) -> int:
    """A chore row exactly as the pre-mode build wrote it: mode still ''."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO chores (household_id, name, default_assignee_id, rotation_member_ids_json, mode) "
        "VALUES (1, ?, ?, ?, '')",
        (name, default if default is not None else (rotation[0] if rotation else None), json.dumps(rotation)),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


# --- 1. schema + migration ------------------------------------------------

def test_the_columns_are_added_once_and_survive_a_rerun():
    conn = get_conn()
    _run_migrations(conn)
    _run_migrations(conn)
    conn.commit()
    chores = [r["name"] for r in conn.execute("PRAGMA table_info(chores)")]
    instances = [r["name"] for r in conn.execute("PRAGMA table_info(chore_instances)")]
    conn.close()
    assert chores.count("mode") == 1
    assert instances.count("completed_by_member_id") == 1


def test_migration_one_person_rotation_becomes_owned(two_adults):
    chore_id = _legacy_chore("Bathrooms", [two_adults["Vineeth"]])
    conn = get_conn()
    _migrate_chore_modes(conn)
    conn.commit()
    conn.close()
    row = _chore_row(chore_id)
    assert row["mode"] == "owned"
    assert row["default_assignee_id"] == two_adults["Vineeth"]
    assert json.loads(row["rotation_member_ids_json"]) == [two_adults["Vineeth"]]


def test_migration_two_person_rotation_becomes_shared(two_adults):
    chore_id = _legacy_chore("Vacuuming", [two_adults["Emily"], two_adults["Vineeth"]])
    conn = get_conn()
    _migrate_chore_modes(conn)
    conn.commit()
    conn.close()
    row = _chore_row(chore_id)
    assert row["mode"] == "shared"
    assert json.loads(row["rotation_member_ids_json"]) == [two_adults["Emily"], two_adults["Vineeth"]]


def test_migration_empty_rotation_becomes_whoever(two_adults):
    chore_id = _legacy_chore("Bins", [])
    conn = get_conn()
    _migrate_chore_modes(conn)
    conn.commit()
    conn.close()
    row = _chore_row(chore_id)
    assert row["mode"] == "whoever"
    assert row["default_assignee_id"] is None


def test_migration_default_assignee_only_counts_as_one_person(two_adults):
    """The oldest rows had a default_assignee_id and an empty rotation —
    that was one named person too, not nobody."""
    chore_id = _legacy_chore("Litter", [], default=two_adults["Emily"])
    conn = get_conn()
    _migrate_chore_modes(conn)
    conn.commit()
    conn.close()
    row = _chore_row(chore_id)
    assert row["mode"] == "owned"
    assert json.loads(row["rotation_member_ids_json"]) == [two_adults["Emily"]]


def test_migration_is_idempotent_and_leaves_decided_rows_alone(two_adults):
    chore_id = _legacy_chore("Vacuuming", [two_adults["Emily"], two_adults["Vineeth"]])
    conn = get_conn()
    _migrate_chore_modes(conn)
    # The household then decides Emily owns it; a restart must not undo that.
    conn.execute(
        "UPDATE chores SET mode = 'owned', rotation_member_ids_json = ?, default_assignee_id = ? WHERE id = ?",
        (json.dumps([two_adults["Emily"]]), two_adults["Emily"], chore_id),
    )
    _migrate_chore_modes(conn)
    conn.commit()
    conn.close()
    row = _chore_row(chore_id)
    assert row["mode"] == "owned"
    assert json.loads(row["rotation_member_ids_json"]) == [two_adults["Emily"]]


def test_a_row_the_migration_has_not_reached_still_reads_by_the_same_rule(two_adults):
    """Nothing depends on the backfill having run: a '' row reads as what
    its rotation implies, everywhere."""
    _legacy_chore("Bathrooms", [two_adults["Vineeth"]])
    _legacy_chore("Bins", [])
    by_name = {d["name"]: d for d in tools.list_chore_definitions()}
    assert by_name["Bathrooms"]["mode"] == "owned"
    assert by_name["Bathrooms"]["owner"] == "Vineeth"
    assert by_name["Bins"]["mode"] == "whoever"


# --- 2. add_chore: owned is the default ----------------------------------

def test_add_chore_defaults_to_owned_by_the_only_adult():
    emily = _adult("Emily")
    tools.add_member("Sam")
    tools.set_member_age_group("Sam", "child")
    result = tools.add_chore("Dishes")
    assert result["mode"] == "owned"
    assert result["owner"] == "Emily"
    assert _chore_row(result["chore_id"])["default_assignee_id"] == emily


def test_add_chore_with_an_owner_name_is_owned(two_adults):
    result = tools.add_chore("Bathrooms", owner_name="Vineeth")
    assert result["mode"] == "owned"
    assert result["owner"] == "Vineeth"
    assert result["who_label"] == "Vineeth"


def test_add_chore_with_one_assignee_name_is_owned_by_them(two_adults):
    """The shape the old tool accepted still means what it meant."""
    result = tools.add_chore("Bathrooms", assignee_names=["Vineeth"])
    assert result["mode"] == "owned"
    assert result["owner"] == "Vineeth"


def test_add_chore_with_several_names_is_shared(two_adults):
    result = tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"])
    assert result["mode"] == "shared"
    assert result["owner"] is None
    assert result["assignees"] == ["Emily", "Vineeth"]
    assert result["up_next"] == "Emily"


def test_add_chore_whoever_names_nobody(two_adults):
    result = tools.add_chore("Bins", mode="whoever")
    assert result["mode"] == "whoever"
    assert result["assignees"] == []
    assert result["who_label"] == "either of you"


def test_add_chore_with_two_adults_and_no_name_falls_back_to_shared(two_adults):
    """No owner can be inferred between two adults, so rather than guess
    one, the chore is shared across them — the assumption the card's
    'owned is the default' leaves open for this exact case."""
    result = tools.add_chore("Dishes")
    assert result["mode"] == "shared"
    assert result["assignees"] == ["Emily", "Vineeth"]


def test_add_chore_draws_on_the_rotation_named_in_setup(two_adults):
    tools.add_member("Grandma")
    tools.set_chores_profile(rotation_members=["Vineeth"])
    result = tools.add_chore("Lawn")
    assert result["mode"] == "owned"
    assert result["owner"] == "Vineeth"


def test_add_chore_rejects_a_made_up_mode(two_adults):
    with pytest.raises(ValueError):
        tools.add_chore("Bins", mode="rotating")


# --- 3. generate_chore_schedule assigns by mode ----------------------------

def test_schedule_owned_goes_to_the_owner_every_time(two_adults):
    chore_id = tools.add_chore("Bathrooms", owner_name="Vineeth", frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=28)
    rows = _instances(chore_id)
    assert len(rows) >= 4
    assert {r["assignee_id"] for r in rows} == {two_adults["Vineeth"]}


def test_schedule_shared_takes_turns(two_adults):
    chore_id = tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"], frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=28)
    rows = _instances(chore_id)
    e, v = two_adults["Emily"], two_adults["Vineeth"]
    assert [r["assignee_id"] for r in rows][:4] == [e, v, e, v]


def test_schedule_shared_continues_the_turn_order_when_topped_up(two_adults):
    chore_id = tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"], frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=7)
    tools.generate_chore_schedule(days_ahead=28)
    rows = _instances(chore_id)
    e, v = two_adults["Emily"], two_adults["Vineeth"]
    assert [r["assignee_id"] for r in rows][:4] == [e, v, e, v]


def test_schedule_whoever_has_no_assignee(two_adults):
    chore_id = tools.add_chore("Bins", mode="whoever", frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=28)
    rows = _instances(chore_id)
    assert rows
    assert {r["assignee_id"] for r in rows} == {None}


def test_a_one_off_instance_follows_the_mode_too(two_adults):
    tools.add_chore("Bathrooms", owner_name="Vineeth")
    tools.add_chore("Bins", mode="whoever")
    tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"])
    day = datetime.date.today().isoformat()
    owned = tools.schedule_chore_instance("Bathrooms", day)["instance_id"]
    nobody = tools.schedule_chore_instance("Bins", day)["instance_id"]
    first = tools.schedule_chore_instance("Vacuuming", day)["instance_id"]
    second = tools.schedule_chore_instance("Vacuuming", day)["instance_id"]
    conn = get_conn()
    by_id = {r["id"]: r["assignee_id"] for r in conn.execute("SELECT id, assignee_id FROM chore_instances")}
    conn.close()
    assert by_id[owned] == two_adults["Vineeth"]
    assert by_id[nobody] is None
    assert [by_id[first], by_id[second]] == [two_adults["Emily"], two_adults["Vineeth"]]


# --- 4. update_chore changes the owner by saying so ------------------------

def test_give_the_bathrooms_to_vineeth(two_adults):
    chore_id = tools.add_chore("Bathrooms", owner_name="Emily")["chore_id"]
    tools.generate_chore_schedule(days_ahead=21)
    result = tools.update_chore(chore_id, owner_name="Vineeth")
    assert result["mode"] == "owned"
    assert result["owner"] == "Vineeth"
    assert result["upcoming_moved"] >= 3
    assert {r["assignee_id"] for r in _instances(chore_id)} == {two_adults["Vineeth"]}


def test_lets_take_turns_on_the_vacuuming(two_adults):
    chore_id = tools.add_chore("Vacuuming", owner_name="Emily")["chore_id"]
    tools.generate_chore_schedule(days_ahead=28)
    result = tools.update_chore(chore_id, mode="shared")
    assert result["mode"] == "shared"
    assert result["assignees"] == ["Emily", "Vineeth"]
    e, v = two_adults["Emily"], two_adults["Vineeth"]
    assert [r["assignee_id"] for r in _instances(chore_id)][:4] == [e, v, e, v]


def test_either_of_us_can_do_the_bins(two_adults):
    chore_id = tools.add_chore("Bins", owner_name="Emily")["chore_id"]
    tools.generate_chore_schedule(days_ahead=21)
    result = tools.update_chore(chore_id, mode="whoever")
    assert result["mode"] == "whoever"
    assert result["who_label"] == "either of you"
    assert {r["assignee_id"] for r in _instances(chore_id)} == {None}
    assert _chore_row(chore_id)["default_assignee_id"] is None


def test_make_it_owned_without_a_name_asks_rather_than_guesses(two_adults):
    chore_id = tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"])["chore_id"]
    with pytest.raises(ValueError):
        tools.update_chore(chore_id, mode="owned")
    assert _chore_row(chore_id)["mode"] == "shared", "a refused change changes nothing"


def test_make_it_owned_keeps_the_one_person_already_on_it(two_adults):
    chore_id = _legacy_chore("Bathrooms", [two_adults["Vineeth"]])
    result = tools.update_chore(chore_id, mode="owned")
    assert result["owner"] == "Vineeth"


def test_the_old_assignee_names_shape_still_sets_the_mode(two_adults):
    chore_id = tools.add_chore("Bins", mode="whoever")["chore_id"]
    assert tools.update_chore(chore_id, assignee_names=["Emily", "Vineeth"])["mode"] == "shared"
    assert tools.update_chore(chore_id, assignee_names=["Emily"])["mode"] == "owned"
    assert tools.update_chore(chore_id, assignee_names=[])["mode"] == "whoever"


def test_changing_frequency_alone_leaves_the_owner_alone(two_adults):
    chore_id = tools.add_chore("Bathrooms", owner_name="Vineeth")["chore_id"]
    result = tools.update_chore(chore_id, frequency="biweekly")
    assert result["mode"] == "owned"
    assert result["owner"] == "Vineeth"
    assert "upcoming_moved" not in result


# --- 5. history is never rewritten -----------------------------------------

def test_done_instances_keep_the_person_who_did_them(two_adults):
    chore_id = tools.add_chore("Bathrooms", owner_name="Emily", frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=21)
    rows = _instances(chore_id)
    done_id = rows[0]["id"]
    with tools.use_member(two_adults["Emily"]):
        tools.complete_chore(done_id)

    tools.update_chore(chore_id, owner_name="Vineeth")

    after = {r["id"]: r for r in _instances(chore_id)}
    assert after[done_id]["status"] == "done"
    assert after[done_id]["assignee_id"] == two_adults["Emily"]
    assert after[done_id]["completed_by_member_id"] == two_adults["Emily"]
    pending = [r for r in after.values() if r["status"] == "pending"]
    assert pending and {r["assignee_id"] for r in pending} == {two_adults["Vineeth"]}


def test_going_shared_picks_up_the_turn_after_the_last_done(two_adults):
    """Handing a shared chore to the person who just did it would be the
    unfairness the card exists to stop."""
    chore_id = tools.add_chore("Vacuuming", owner_name="Emily", frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=21)
    first = _instances(chore_id)[0]["id"]
    tools.complete_chore(first)
    tools.update_chore(chore_id, mode="shared", assignee_names=["Emily", "Vineeth"])
    pending = [r["assignee_id"] for r in _instances(chore_id) if r["status"] == "pending"]
    assert pending[0] == two_adults["Vineeth"]


# --- 6. both are recorded: whose it was, and who did it ---------------------

def test_a_tick_is_credited_to_the_session_adult(two_adults):
    chore_id = tools.add_chore("Bins", mode="whoever")["chore_id"]
    instance_id = tools.schedule_chore_instance("Bins", datetime.date.today().isoformat())["instance_id"]
    with tools.use_member(two_adults["Vineeth"]):
        tools.complete_chore(instance_id)
    row = _instances(chore_id)[0]
    assert row["assignee_id"] is None, "whose it was: nobody's"
    assert row["completed_by_member_id"] == two_adults["Vineeth"], "who did it: Vineeth"
    assert tools.list_chores(status="done")[0]["completed_by"] == "Vineeth"


def test_vineeth_did_the_bins_credits_vineeth(two_adults):
    tools.add_chore("Bins", owner_name="Emily")
    instance_id = tools.schedule_chore_instance("Bins", datetime.date.today().isoformat())["instance_id"]
    with tools.use_member(two_adults["Emily"]):
        tools.complete_chore(instance_id, done_by="Vineeth")
    conn = get_conn()
    row = conn.execute("SELECT assignee_id, completed_by_member_id FROM chore_instances WHERE id = ?", (instance_id,)).fetchone()
    conn.close()
    assert row["assignee_id"] == two_adults["Emily"]
    assert row["completed_by_member_id"] == two_adults["Vineeth"]


def test_a_name_that_is_nobody_in_the_house_is_not_invented(two_adults):
    tools.add_chore("Bins", owner_name="Emily")
    instance_id = tools.schedule_chore_instance("Bins", datetime.date.today().isoformat())["instance_id"]
    tools.complete_chore(instance_id, done_by="Vinneth")
    assert tools.list_members() and all(m["name"] != "Vinneth" for m in tools.list_members())


def test_the_now_card_tick_records_the_picked_adult_and_unticking_clears_it(client, two_adults):
    tools.add_chore("Bins", mode="whoever")
    instance_id = tools.schedule_chore_instance("Bins", datetime.date.today().isoformat())["instance_id"]
    client.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})

    assert client.post(f"/api/chores/{instance_id}/status", json={"status": "done"}).status_code == 200
    conn = get_conn()
    row = conn.execute("SELECT completed_by_member_id FROM chore_instances WHERE id = ?", (instance_id,)).fetchone()
    conn.close()
    assert row["completed_by_member_id"] == two_adults["Vineeth"]

    assert client.post(f"/api/chores/{instance_id}/status", json={"status": "pending"}).status_code == 200
    conn = get_conn()
    row = conn.execute("SELECT completed_by_member_id, completed_at FROM chore_instances WHERE id = ?", (instance_id,)).fetchone()
    conn.close()
    assert row["completed_by_member_id"] is None and row["completed_at"] is None


# --- 7. what the screens will need is on the way out -----------------------

def test_definitions_say_whose_and_whose_turn(two_adults):
    tools.add_chore("Bathrooms", owner_name="Vineeth")
    tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"], frequency="weekly")
    tools.add_chore("Bins", mode="whoever")
    tools.generate_chore_schedule(days_ahead=7)
    by_name = {d["name"]: d for d in tools.list_chore_definitions()}
    assert by_name["Bathrooms"]["who_label"] == "Vineeth"
    assert by_name["Vacuuming"]["mode"] == "shared"
    assert by_name["Vacuuming"]["up_next"] == "Emily"
    assert by_name["Vacuuming"]["who_label"] == "Emily"
    assert by_name["Bins"]["who_label"] == "either of you"


def test_whose_turn_moves_on_once_this_one_is_done(two_adults):
    tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"], frequency="weekly")
    tools.generate_chore_schedule(days_ahead=14)
    first = tools.list_chores()[0]["id"]
    tools.complete_chore(first)
    assert tools.list_chore_definitions()[0]["up_next"] == "Vineeth"


def test_instances_carry_a_first_name_or_either_of_you(two_adults):
    tools.add_member("Grandma Jo")
    tools.add_chore("Bathrooms", owner_name="Grandma Jo")
    tools.add_chore("Bins", mode="whoever")
    day = datetime.date.today().isoformat()
    tools.schedule_chore_instance("Bathrooms", day)
    tools.schedule_chore_instance("Bins", day)
    by_name = {r["chore"]: r for r in tools.get_chores_due_today()}
    assert by_name["Bathrooms"]["who_label"] == "Grandma"
    assert by_name["Bathrooms"]["mode"] == "owned"
    assert by_name["Bins"]["who_label"] == "either of you"
    assert by_name["Bins"]["mode"] == "whoever"


def test_either_of_you_is_anyone_when_there_are_not_two_adults():
    _adult("Emily")
    tools.add_chore("Bins", mode="whoever")
    assert tools.list_chore_definitions()[0]["who_label"] == "anyone"


def test_the_today_route_serves_the_label(signed_in, two_adults):
    tools.add_chore("Bathrooms", owner_name="Vineeth")
    tools.schedule_chore_instance("Bathrooms", datetime.date.today().isoformat())
    body = signed_in.get("/api/chores/today").json()
    assert body["chores"][0]["who_label"] == "Vineeth"
    assert body["chores"][0]["completed_by"] is None


def test_the_hidden_now_card_prints_the_owner_behind_its_flag():
    assert "SHOW_CHORES_ON_TODAY = false" in SHELL_JS
    assert "c.who_label" in SHELL_JS
    assert 'class="chore-who"' in SHELL_JS
    assert ".chore-who" in SHELL_CSS


# --- 8. the starter list proposes an owner for every row --------------------

def test_normaliser_fills_in_an_owner_for_every_row():
    rows = agent._normalize_chore_recommendations(
        [
            {"name": "Bathrooms", "category": "cleaning", "frequency": "weekly"},
            {"name": "Kitchen", "category": "cleaning", "frequency": "daily"},
            {"name": "Vacuuming", "category": "cleaning", "frequency": "weekly", "mode": "shared"},
            {"name": "Bins", "category": "cleaning", "frequency": "weekly", "mode": "whoever"},
            {"name": "Litter", "category": "cleaning", "frequency": "daily", "assignee_names": ["vineeth"]},
            "not a row",
        ],
        ["Emily", "Vineeth"],
    )
    by_name = {r["name"]: r for r in rows}
    assert len(rows) == 5
    # Owned by default, dealt round the rotation rather than piled on one person.
    assert by_name["Bathrooms"]["mode"] == "owned" and by_name["Bathrooms"]["owner_name"] == "Emily"
    assert by_name["Kitchen"]["mode"] == "owned" and by_name["Kitchen"]["owner_name"] == "Vineeth"
    assert by_name["Vacuuming"]["assignee_names"] == ["Emily", "Vineeth"]
    assert by_name["Bins"] == {**by_name["Bins"], "mode": "whoever", "owner_name": "", "assignee_names": []}
    assert by_name["Litter"]["mode"] == "owned" and by_name["Litter"]["owner_name"] == "Vineeth"


def test_normaliser_with_nobody_named_proposes_whoever():
    rows = agent._normalize_chore_recommendations(
        [{"name": "Bathrooms", "category": "cleaning", "frequency": "weekly", "mode": "owned"}], []
    )
    assert rows[0]["mode"] == "whoever"


def _stub_recommendation(monkeypatch, chores):
    block = types.SimpleNamespace(type="tool_use", name="submit_chore_recommendations", input={"chores": chores}, id="tu_1")
    response = types.SimpleNamespace(content=[block])
    monkeypatch.setattr(agent, "_client", lambda: object())
    monkeypatch.setattr(agent, "_create_with_retry", lambda client, **kwargs: response)


def test_the_recommend_route_returns_mode_and_owner_per_row(signed_in, two_adults, monkeypatch):
    _stub_recommendation(monkeypatch, [
        {"name": "Bathrooms", "category": "cleaning", "frequency": "weekly", "mode": "owned", "owner_name": "Vineeth"},
        {"name": "Kitchen", "category": "cleaning", "frequency": "daily"},
    ])
    res = signed_in.post("/api/onboarding/chores/recommend", json={"rotation_members": ["Emily", "Vineeth"]})
    assert res.status_code == 200
    rows = res.json()["chores"]
    assert rows[0]["mode"] == "owned" and rows[0]["owner_name"] == "Vineeth"
    assert rows[1]["mode"] == "owned" and rows[1]["owner_name"] in ("Emily", "Vineeth")


def test_the_recommend_route_draws_on_the_adults_when_setup_named_nobody(signed_in, two_adults, monkeypatch):
    seen = {}

    def fake(profile):
        seen.update(profile)
        return []

    from app import main
    monkeypatch.setattr(main, "generate_chore_recommendations", fake)
    signed_in.post("/api/onboarding/chores/recommend", json={"rotation_members": []})
    assert seen["rotation_members"] == ["Emily", "Vineeth"]


def test_the_recommend_prompt_asks_for_an_owner_by_default():
    props = agent._RECOMMEND_CHORES_TOOL["input_schema"]["properties"]["chores"]["items"]
    assert props["properties"]["mode"]["enum"] == ["owned", "shared", "whoever"]
    assert "owner_name" in props["properties"]
    assert "mode" in props["required"]


def test_save_accepts_per_row_owner_overrides(signed_in, two_adults):
    res = signed_in.post("/api/onboarding/chores/save", json={"chores": [
        {"name": "Bathrooms", "mode": "owned", "owner_name": "Vineeth"},
        {"name": "Vacuuming", "mode": "shared", "assignee_names": ["Emily", "Vineeth"]},
        {"name": "Bins", "mode": "whoever"},
        {"name": "Kitchen", "assignee_names": ["Emily"]},
    ]})
    assert res.status_code == 200
    by_name = {d["name"]: d for d in tools.list_chore_definitions()}
    assert by_name["Bathrooms"]["owner"] == "Vineeth"
    assert by_name["Vacuuming"]["mode"] == "shared"
    assert by_name["Bins"]["mode"] == "whoever"
    assert by_name["Kitchen"]["mode"] == "owned" and by_name["Kitchen"]["owner"] == "Emily"
    # ...and the schedule that followed respects each.
    today = {r["chore"]: r for r in tools.get_chores_due_today()}
    assert today["Bathrooms"]["assignee"] == "Vineeth"
    assert today["Bins"]["assignee"] is None


# --- 9. the chat tools know the words ----------------------------------------

def test_the_chat_tools_carry_mode_and_owner():
    by_name = {t["name"]: t for t in agent.TOOL_DEFINITIONS}
    add = by_name["add_chore"]["input_schema"]["properties"]
    upd = by_name["update_chore"]["input_schema"]["properties"]
    assert add["mode"]["enum"] == ["owned", "shared", "whoever"]
    assert "owner_name" in add and "owner_name" in upd
    assert upd["mode"]["enum"] == ["owned", "shared", "whoever"]
    # The phrases the household will actually say are in the description,
    # so the model maps them without guessing.
    desc = by_name["update_chore"]["description"].lower()
    assert "take turns" in desc and "either of us" in desc and "give the" in desc
    assert "done_by" in by_name["complete_chore"]["input_schema"]["properties"]
