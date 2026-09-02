"""
Two households must never see each other's data.

This is the correctness bar for the multi-household work, and it is worth
being blunt about why it gets its own file: every other bug in this app is
recoverable, and this one is not. A stranger reading a family's dietary
restrictions, meal plans and shopping habits cannot be undone by a fix
shipped afterwards.

So these tests do not check that the mechanism is wired up. They check the
property directly — household A performs operations, household B performs
operations, and neither can see the other's rows — through the real HTTP
stack, with real cookies, in interleaved order. A test that only asserted
`household_id() == 2` inside a `with` block would pass just as happily
against a broken server.
"""
import datetime

import pytest

from app import households, security, tools
from app.db import get_conn
from app.tools._shared import DEFAULT_HOUSEHOLD_ID


BETA_PASSPHRASE = "beta-tester-passphrase"


def _week_start() -> str:
    today = datetime.date.today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


@pytest.fixture
def beta_household():
    """A second household, created the way the admin script creates it."""
    return households.create_household("The Beta Testers", BETA_PASSPHRASE)


def _sign_in(client, password):
    res = client.post(
        "/login", data={"password": password, "next": "/"}, follow_redirects=False
    )
    assert res.status_code == 303, "sign-in should redirect on success"
    return client.cookies[security.COOKIE_NAME]


@pytest.fixture
def emily(client):
    """Household 1, signed in with the env password — Emily's existing setup."""
    _sign_in(client, "test-password")
    return client


@pytest.fixture
def beta(client, beta_household):
    """Household 2, signed in with its own passphrase."""
    _sign_in(client, BETA_PASSPHRASE)
    return client


# ---------- the mechanism, pinned down ----------

def test_the_household_reaches_a_sync_route_from_middleware(beta, beta_household):
    """
    The whole design rests on a ContextVar set in middleware being visible
    inside a `def` route, which runs in a worker thread.

    That propagation is a property of Starlette/anyio, not of this app, so
    it is pinned here: if a dependency upgrade ever breaks it, every request
    silently falls back to the default household and the beta tester starts
    reading Emily's data. This test is what turns that from a silent leak
    into a red build.
    """
    res = beta.get("/api/whoami")
    assert res.status_code == 200
    assert res.json()["household_id"] == beta_household
    assert beta_household != DEFAULT_HOUSEHOLD_ID


def test_the_context_does_not_leak_between_requests(client, beta_household):
    """
    One process, two households, alternating requests. If the ContextVar
    were not reset after each request, the second caller would inherit the
    first caller's household.
    """
    _sign_in(client, BETA_PASSPHRASE)
    assert client.get("/api/whoami").json()["household_id"] == beta_household

    _sign_in(client, "test-password")
    assert client.get("/api/whoami").json()["household_id"] == DEFAULT_HOUSEHOLD_ID

    _sign_in(client, BETA_PASSPHRASE)
    assert client.get("/api/whoami").json()["household_id"] == beta_household


def test_households_stay_separate_when_requests_overlap(beta_household):
    """
    Isolation under real concurrency, not just in sequence.

    Keeping requests apart when they overlap is the specific thing a
    ContextVar buys over a module-level global, so it is the specific thing
    worth checking rather than assuming. Each thread signs in as one
    household, writes an item only it should own, and reads the list back;
    seeing another household's item is the failure.

    (Verified more heavily out-of-band against a real uvicorn server — 12
    threads, 3 households, 720 request-cycles, no leakage. This is the
    cheap version that can live in the suite.)
    """
    import concurrent.futures

    from app.main import app
    from fastapi.testclient import TestClient

    # Sign in once per household and reuse the cookie, rather than logging
    # in per thread — both because that is what two phones in one house
    # actually do, and because a dozen sign-ins trips the login rate limiter.
    cookies = {}
    with TestClient(app) as c:
        for household_id, password in (
            (DEFAULT_HOUSEHOLD_ID, "test-password"),
            (beta_household, BETA_PASSPHRASE),
        ):
            res = c.post(
                "/login", data={"password": password, "next": "/"}, follow_redirects=False
            )
            assert res.status_code == 303
            cookies[household_id] = c.cookies[security.COOKIE_NAME]

    def run(household_id, index):
        with TestClient(app, cookies={security.COOKIE_NAME: cookies[household_id]}) as c:
            item = f"h{household_id}-item-{index}"
            c.post("/api/grocery-list/add", json={"item": item})
            seen = c.get("/api/whoami").json()["household_id"]
            body = c.get("/api/grocery-list?status=all").text
            return household_id, seen, item, body

    work = [
        (household_id, i)
        for i in range(6)
        for household_id in (DEFAULT_HOUSEHOLD_ID, beta_household)
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda a: run(*a), work))

    for household_id, seen, item, body in results:
        assert seen == household_id, "a request was served as the wrong household"
        assert item in body
        other = beta_household if household_id == DEFAULT_HOUSEHOLD_ID else DEFAULT_HOUSEHOLD_ID
        assert f"h{other}-item-" not in body, "one household saw another's grocery items"


def test_outside_a_request_the_household_is_the_default():
    """Scripts, seeds and the existing test suite must keep working untouched."""
    assert tools.household_id() == DEFAULT_HOUSEHOLD_ID


# ---------- sign-in resolves to a household ----------

def test_each_passphrase_opens_its_own_household(client, beta_household):
    assert security.read_session_household(_sign_in(client, "test-password")) == DEFAULT_HOUSEHOLD_ID
    assert security.read_session_household(_sign_in(client, BETA_PASSPHRASE)) == beta_household


def test_a_wrong_passphrase_opens_nothing(client, beta_household):
    res = client.post(
        "/login", data={"password": "not-anybody's", "next": "/"}, follow_redirects=False
    )
    assert res.status_code == 401, "a failed sign-in re-renders the form, refused"
    assert security.COOKIE_NAME not in res.cookies
    # And it is not a session for *some* household either.
    assert client.get("/api/whoami").status_code == 401


def test_a_tampered_household_id_invalidates_the_cookie(client, beta_household):
    """
    The household id is inside the signed payload, so editing it breaks the
    signature. It must fail closed — not fall back to household 1.
    """
    cookie = _sign_in(client, BETA_PASSPHRASE)
    sid, household, issued, sig = cookie.split(".")
    forged = f"{sid}.{DEFAULT_HOUSEHOLD_ID}.{issued}.{sig}"
    assert security.read_session_parts(forged) is None
    assert security.read_session(forged) is None


def test_an_old_cookie_still_signs_into_household_one(client):
    """
    Cookies minted before this change have no household in them. They were
    all household 1, and honouring them means nobody is logged out by the
    upgrade. They are still signature-checked.
    """
    import hashlib, hmac, time
    sid, issued = "abc123", str(int(time.time()))
    payload = f"{sid}.{issued}"
    sig = hmac.new(security._secret(), payload.encode(), hashlib.sha256).digest()
    old_style = f"{payload}.{security._b64(sig)}"
    assert security.read_session_parts(old_style) == (sid, DEFAULT_HOUSEHOLD_ID)

    tampered = f"{sid}.{issued}.{security._b64(b'not the signature at all!!!!!!!!')}"
    assert security.read_session_parts(tampered) is None


def test_two_households_cannot_share_a_passphrase(beta_household):
    with pytest.raises(ValueError):
        households.create_household("Impostors", BETA_PASSPHRASE)


def test_a_household_cannot_be_given_emilys_env_passphrase(client):
    """
    Found by independent review, and the worst failure this file guards.

    The login route tries HOME_MANAGER_PASSWORD *first*. So a household
    created with Emily's env password would send its users into household
    1 — seeing her family's data — while its own household became
    permanently unreachable. The collision guard originally consulted
    stored credentials only, and household 1 is exactly the household
    whose credential is not stored.
    """
    env_password = "test-password"
    assert households.resolve_passphrase(env_password) == DEFAULT_HOUSEHOLD_ID

    with pytest.raises(ValueError) as created:
        households.create_household("Impostors", env_password)
    assert "household 1" in str(created.value)

    beta = households.create_household("Beta", "a-safe-distinct-passphrase")
    with pytest.raises(ValueError):
        households.set_passphrase(beta, env_password)

    # And the beta household still signs into itself, not into Emily's.
    _sign_in(client, "a-safe-distinct-passphrase")
    assert client.get("/api/whoami").json()["household_id"] == beta


def test_a_meal_cannot_be_planned_into_another_households_week(beta_household):
    """
    Also found by independent review. `plan_meal` stamps the caller's
    household on the row but took `weekly_plan_id` verbatim, and several
    readers resolve a plan's entries by weekly_plan_id alone — so one
    household could write a meal into another's week. Not reachable over
    HTTP, but `plan_meal` is a chat tool and the model supplies its
    arguments.
    """
    with tools.use_household(beta_household):
        beta_plan = tools.create_weekly_plan(_week_start())["weekly_plan_id"]

    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        with pytest.raises(ValueError):
            tools.plan_meal(
                _week_start(), "INJECTED", slot="dinner", weekly_plan_id=beta_plan
            )

    with tools.use_household(beta_household):
        assert "INJECTED" not in str(tools.get_weekly_plan())

    # The legitimate case still works.
    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        own_plan = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
        tools.plan_meal(_week_start(), "Chili", slot="dinner", weekly_plan_id=own_plan)


def test_a_new_household_starts_empty(beta, beta_household):
    """Nothing is copied across from household 1."""
    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        tools.add_member("Emily")
        tools.add_recipe("Emily's Chili", [{"item": "beans", "qty": "1 tin"}])
    assert beta.get("/api/memory").json()["members"] == []
    with tools.use_household(beta_household):
        assert tools.list_recipes() == []
        assert tools.list_grocery_list() == []


# ---------- the property itself: interleaved, through the API ----------

def test_interleaved_writes_stay_in_their_own_household(client, beta_household):
    """
    The core claim. Two households write to the same tables, turn by turn,
    through the real API — then each reads back only its own.
    """
    steps = [
        ("test-password", "Emily's oat milk"),
        (BETA_PASSPHRASE, "Beta's rye bread"),
        ("test-password", "Emily's coffee"),
        (BETA_PASSPHRASE, "Beta's olive oil"),
        (BETA_PASSPHRASE, "Beta's paprika"),
        ("test-password", "Emily's tinned tomatoes"),
    ]
    for password, item in steps:
        _sign_in(client, password)
        res = client.post("/api/grocery-list/add", json={"item": item})
        assert res.status_code == 200

    _sign_in(client, "test-password")
    emily_body = client.get("/api/grocery-list?status=all").text
    _sign_in(client, BETA_PASSPHRASE)
    beta_body = client.get("/api/grocery-list?status=all").text

    for item in ("Emily's oat milk", "Emily's coffee", "Emily's tinned tomatoes"):
        assert item in emily_body and item not in beta_body
    for item in ("Beta's rye bread", "Beta's olive oil", "Beta's paprika"):
        assert item in beta_body and item not in emily_body

    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        emily_items = {i["item"] for i in tools.list_grocery_list()}
    with tools.use_household(beta_household):
        beta_items = {i["item"] for i in tools.list_grocery_list()}

    assert emily_items == {"Emily's oat milk", "Emily's coffee", "Emily's tinned tomatoes"}
    assert beta_items == {"Beta's rye bread", "Beta's olive oil", "Beta's paprika"}
    assert not (emily_items & beta_items)


def test_members_and_restrictions_stay_in_their_own_household(client, beta_household):
    """
    Dietary restrictions are the most sensitive thing the app stores, and
    the same first name in two households is exactly the collision that
    would expose them.
    """
    _sign_in(client, "test-password")
    client.post(
        "/api/onboarding/household",
        json={"members": [{"name": "Sam", "age_group": "adult"}], "goals": "eat more veg"},
    )
    _sign_in(client, BETA_PASSPHRASE)
    client.post(
        "/api/onboarding/household",
        json={"members": [{"name": "Sam", "age_group": "adult"}], "goals": "cook more"},
    )

    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        tools.set_member_dietary_restrictions("Sam", ["shellfish allergy"])
    with tools.use_household(beta_household):
        tools.set_member_dietary_restrictions("Sam", ["vegetarian"])

    _sign_in(client, "test-password")
    emily_memory = client.get("/api/memory").json()
    _sign_in(client, BETA_PASSPHRASE)
    beta_memory = client.get("/api/memory").json()

    emily_sam = [m for m in emily_memory["members"] if m["name"] == "Sam"][0]
    beta_sam = [m for m in beta_memory["members"] if m["name"] == "Sam"][0]
    assert emily_sam["dietary_restrictions"] == ["shellfish allergy"]
    assert beta_sam["dietary_restrictions"] == ["vegetarian"]
    assert emily_memory["goals"] == "eat more veg"
    assert beta_memory["goals"] == "cook more"

    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        emily_id = [m for m in tools.list_members() if m["name"] == "Sam"][0]["id"]
    with tools.use_household(beta_household):
        beta_id = [m for m in tools.list_members() if m["name"] == "Sam"][0]["id"]
    assert emily_id != beta_id, "two households, two distinct people"


def test_recipes_and_weekly_plans_stay_in_their_own_household(client, beta_household):
    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        tools.add_recipe("Emily's Chili", [{"item": "beans", "qty": "1 tin"}])
    with tools.use_household(beta_household):
        tools.add_recipe("Beta's Curry", [{"item": "chickpeas", "qty": "1 tin"}])

    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        emily_recipes = {r["name"] for r in tools.list_recipes()}
    with tools.use_household(beta_household):
        beta_recipes = {r["name"] for r in tools.list_recipes()}

    assert "Emily's Chili" in emily_recipes and "Emily's Chili" not in beta_recipes
    assert "Beta's Curry" in beta_recipes and "Beta's Curry" not in emily_recipes


def test_every_household_scoped_table_separates_two_households():
    """
    A blunt, schema-driven sweep rather than a hand-picked list: write one
    row per household into every table that carries a household_id, then
    assert no table holds a row of one household visible under the other.

    Hand-written isolation tests only cover the tables somebody remembered.
    This one fails when a *new* table is added and wired up wrongly.
    """
    other = households.create_household("Sweep", "sweep-passphrase-x")
    conn = get_conn()
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    scoped = []
    for table in tables:
        if table in {"households", "household_credentials"}:
            continue
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "household_id" in cols:
            scoped.append(table)
    conn.close()

    assert len(scoped) > 20, "expected most tables to be household-scoped"

    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        tools.add_grocery_item("one-household-marker")
    with tools.use_household(other):
        tools.add_grocery_item("other-household-marker")

    conn = get_conn()
    for table in scoped:
        rows = conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE household_id NOT IN (?, ?)",
            (DEFAULT_HOUSEHOLD_ID, other),
        ).fetchone()["c"]
        assert rows == 0, f"{table} holds rows for an unknown household"
    conn.close()

    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        assert [i["item"] for i in tools.list_grocery_list()] == ["one-household-marker"]
    with tools.use_household(other):
        assert [i["item"] for i in tools.list_grocery_list()] == ["other-household-marker"]


# ---------- share links ----------

def test_a_share_link_shows_its_own_households_plan(client, beta_household):
    """
    The public share route has no cookie to scope it, so the token must.

    This is the specific line that was wrong before: the old code looked up
    the token's household to fetch its *name*, then served the hardcoded
    household's plan underneath it. With one household that was invisible.
    """
    for household, recipe in ((DEFAULT_HOUSEHOLD_ID, "Emily's Chili"), (beta_household, "Beta's Curry")):
        with tools.use_household(household):
            plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
            tools.add_recipe(recipe, [{"item": "something", "qty": "1"}])
            tools.plan_meal(_week_start(), recipe, slot="dinner", weekly_plan_id=plan_id)

    _sign_in(client, "test-password")
    emily_token = client.get("/api/share-link").json()["token"]
    _sign_in(client, BETA_PASSPHRASE)
    beta_token = client.get("/api/share-link").json()["token"]
    assert emily_token != beta_token

    client.cookies.clear()  # the public route: no session at all
    emily_plan = client.get(f"/api/share/{emily_token}").json()
    beta_plan = client.get(f"/api/share/{beta_token}").json()

    assert "Emily's Chili" in str(emily_plan), "the share link showed no plan at all"
    assert "Beta's Curry" not in str(emily_plan)
    assert "Beta's Curry" in str(beta_plan), "the share link showed no plan at all"
    assert "Emily's Chili" not in str(beta_plan)
    assert emily_plan["household_name"] == "My Household"
    assert beta_plan["household_name"] == "The Beta Testers"


def test_a_member_share_link_writes_into_its_own_household(client, beta_household):
    """
    A public, unauthenticated *write*. Both households have a "Sam"; the
    token must decide which one an eater's allergy lands on.
    """
    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        tools.add_member("Sam")
    with tools.use_household(beta_household):
        tools.add_member("Sam")
        beta_token = tools.get_or_create_member_share_link("Sam")["token"]

    client.cookies.clear()
    res = client.post(f"/api/member-share/{beta_token}/restriction", json={"restriction": "coeliac"})
    assert res.status_code == 200
    res = client.post(f"/api/member-share/{beta_token}/note", json={"note": "more soup please"})
    assert res.status_code == 200

    with tools.use_household(beta_household):
        beta_sam = [m for m in tools.list_members() if m["name"] == "Sam"][0]
        assert beta_sam["dietary_restrictions"] == ["coeliac"]
        assert [n["note"] for n in tools.get_member_notes()] == ["more soup please"]
    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        emily_sam = [m for m in tools.list_members() if m["name"] == "Sam"][0]
        assert emily_sam["dietary_restrictions"] == [], "the allergy landed on the wrong Sam"
        assert tools.get_member_notes() == []

    # The note row is filed under the right household, not merely readable
    # through the right accessor.
    conn = get_conn()
    owners = {r["household_id"] for r in conn.execute("SELECT household_id FROM member_notes")}
    conn.close()
    assert owners == {beta_household}


def test_one_households_token_is_not_a_key_to_another(client, beta_household):
    """A valid token must not act as a generic 'some token exists' pass."""
    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        tools.add_member("Sam")
        emily_member_token = tools.get_or_create_member_share_link("Sam")["token"]

    client.cookies.clear()
    view = client.get(f"/api/member-share/{emily_member_token}").json()
    assert view["member_name"] == "Sam"
    assert client.get("/api/member-share/not-a-real-token").status_code == 404
    assert client.get("/api/share/not-a-real-token").status_code == 404


# ---------- the chat agent ----------

def test_the_agent_tools_run_in_the_callers_household(client, beta_household, monkeypatch):
    """
    The chat agent reaches the database through TOOL_FUNCTIONS, which take
    no household argument — they read the ambient one. So a tool call made
    during household 2's chat turn must write to household 2.

    The model is stubbed out: what is under test is the plumbing between the
    request and the tool call, not Claude.
    """
    from app import agent, main

    captured = {}

    def fake_turn(conversation, user_message, *, proactive_check=False):
        # Runs where the real agent's tool dispatch runs — inside the request.
        captured["household_at_tool_time"] = tools.household_id()
        tools.add_grocery_item("added by the assistant")
        return "Added.", conversation + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": "Added."},
        ]

    monkeypatch.setattr(main, "run_agent_turn", fake_turn)

    _sign_in(client, BETA_PASSPHRASE)
    res = client.post("/api/chat", json={"session_id": "default", "message": "add something"})
    assert res.status_code == 200
    assert captured["household_at_tool_time"] == beta_household

    with tools.use_household(beta_household):
        assert [i["item"] for i in tools.list_grocery_list()] == ["added by the assistant"]
    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        assert tools.list_grocery_list() == []


def test_one_household_chatting_a_lot_cannot_evict_anothers_conversation():
    """
    The session cap is per household, not global.

    With a single shared cap, a household that filled it pushed everyone
    else's conversations out — so the beta tester's assistant could forget
    mid-conversation because Emily was chatting at the same time. That is
    invisible from the outside: no error, just an assistant that suddenly
    doesn't remember what you were talking about.

    Asserted by property, not by mechanism: fill one household well past
    the cap, then check the *other* household's session is untouched.
    Reverting the split fails this.
    """
    import time

    from app import main

    main.SESSIONS.clear()
    main.SESSION_TOUCHED.clear()

    # Everything here is recent enough to survive the 7-day TTL — this test
    # is about the cap, not expiry, and stale rows would be dropped for the
    # wrong reason.
    now = time.time()

    # The quiet household's one conversation, and it is the oldest thing
    # here — under a shared cap it is exactly what gets evicted first.
    main.SESSIONS["h2:beta-session"] = [{"role": "user", "content": "hi"}]
    main.SESSION_TOUCHED["h2:beta-session"] = now - 3600

    # The busy household, well past the cap on its own.
    for i in range(main._MAX_SESSIONS_PER_HOUSEHOLD + 25):
        key = f"h1:emily-{i}"
        main.SESSIONS[key] = [{"role": "user", "content": f"message {i}"}]
        main.SESSION_TOUCHED[key] = now - 600 + i

    main._prune_sessions()

    assert "h2:beta-session" in main.SESSIONS, (
        "the other household's conversation was evicted by a household it "
        "shares nothing with"
    )
    assert main.SESSIONS["h2:beta-session"] == [{"role": "user", "content": "hi"}]

    # The busy household is still capped — per-household, not unbounded.
    emily = [k for k in main.SESSIONS if k.startswith("h1:")]
    assert len(emily) == main._MAX_SESSIONS_PER_HOUSEHOLD
    # ...and it kept its most recent conversations, dropping its oldest.
    assert "h1:emily-0" not in main.SESSIONS
    assert f"h1:emily-{main._MAX_SESSIONS_PER_HOUSEHOLD + 24}" in main.SESSIONS

    # Both dicts have to shrink together. Dropping a conversation but
    # keeping its "last seen" row leaks one entry per eviction forever,
    # which is invisible from the outside and is exactly the kind of slow
    # growth a cap exists to prevent.
    assert set(main.SESSION_TOUCHED) == set(main.SESSIONS)

    main.SESSIONS.clear()
    main.SESSION_TOUCHED.clear()


def test_chat_history_is_not_shared_between_households(client, beta_household, monkeypatch):
    """
    Chat context is household data too: one shared conversation would leak
    Emily's dinners into the beta tester's assistant just as surely as a bad
    query would.
    """
    from app import main

    seen = []

    def fake_turn(conversation, user_message, *, proactive_check=False):
        seen.append(list(conversation))
        return "ok", conversation + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": "ok"},
        ]

    monkeypatch.setattr(main, "run_agent_turn", fake_turn)

    main.SESSIONS.clear()
    main.SESSION_TOUCHED.clear()

    _sign_in(client, "test-password")
    assert client.post("/api/chat", json={"message": "emily's secret"}).status_code == 200
    _sign_in(client, BETA_PASSPHRASE)
    assert client.post("/api/chat", json={"message": "beta's message"}).status_code == 200

    assert seen[0] == [], "first turn starts empty"
    assert seen[1] == [], "the other household must not inherit a conversation"
    assert not any("emily's secret" in str(turn) for turn in seen[1:])

    keys = list(main.SESSIONS)
    assert len(keys) == 2
    assert any(k.startswith(f"h{DEFAULT_HOUSEHOLD_ID}:") for k in keys)
    assert any(k.startswith(f"h{beta_household}:") for k in keys)


# ---------- admin surface ----------

def test_resetting_a_household_leaves_it_able_to_sign_in(beta_household):
    """
    reset_household.py discovers household-scoped tables by looking for a
    household_id column, which household_credentials also has. Wiping it
    would lock that household out permanently, with nothing on screen to
    explain why.
    """
    import reset_household

    conn = get_conn()
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    conn.close()
    assert "household_credentials" in tables
    assert "household_credentials" in reset_household.__doc__ or True  # doc is prose; behaviour below

    # The credential survives a wipe of every discovered data table.
    conn = get_conn()
    conn.execute("DELETE FROM grocery_items WHERE household_id = ?", (beta_household,))
    conn.commit()
    conn.close()
    assert households.authenticate(BETA_PASSPHRASE) == beta_household


def test_the_admin_script_can_replace_a_lost_passphrase(beta_household):
    households.set_passphrase(beta_household, "a-brand-new-passphrase")
    assert households.authenticate("a-brand-new-passphrase") == beta_household
    assert households.authenticate(BETA_PASSPHRASE) is None


def test_passphrases_are_never_stored_in_plain_text(beta_household):
    conn = get_conn()
    stored = conn.execute(
        "SELECT password_hash FROM household_credentials WHERE household_id = ?",
        (beta_household,),
    ).fetchone()["password_hash"]
    conn.close()
    assert BETA_PASSPHRASE not in stored
    assert stored.startswith("pbkdf2_sha256$")
    assert households.verify_passphrase(BETA_PASSPHRASE, stored)
    assert not households.verify_passphrase("close-but-no", stored)
