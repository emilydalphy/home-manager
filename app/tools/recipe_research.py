"""
Research first, then write (Loop Board "Recipes people trust", route 3 —
slice 2, the server half). Locked by Emily 2026-10-05.

Gowthami's household, 2026-10-04: recipe trust is the blocker ("not
authentic enough, some amounts don't add up"). So for a dish Pomona has not
written for this household before, the writer is first handed what 3-5
well-rated versions from the web agree on, led by ONE source:

  * THE LEAD is the version with the top rating and the most ratings. It
    sets the method and the ratios; the others confirm it or show where
    cooks differ (Emily, 2026-10-05). Chosen HERE, in code, from the
    numbers the research call reported — never left to the model's say-so.
  * When the top results have few or no ratings (chana masala in the
    samples), a second search is run restricted to TRUSTED_COOKS for the
    dish's cuisine.
  * A site that blocked the reader is dropped, and so is any URL the
    research call never actually saw in a search result or a fetch — a
    source the app links to has to be a page that exists.
  * The research is saved per household, keyed by the dish, and reused
    whenever the dish comes back: no repeat research.

The model calls live in agent.py (research_dish_llm); this module holds the
rules, the tables and the read the recipe page uses (recipe_research_for).
What is NOT here: the amounts check (agent._settle_recipe_amounts, already
run on every written recipe) and the night-before step (the writer's
instructions) — both landed before this card and are reused as they are.
"""
from __future__ import annotations

import http.client
import ipaddress
import os
import re
import socket
import ssl
import threading
from urllib.parse import urljoin, urlparse

from ..db import get_conn, write
from ._shared import EATS_HERE_SQL, household_id

# Fewer ratings than this and a version is "few or no ratings" — not enough
# for its stars to mean much, so it can't lead. The card's own example is a
# top result with a handful of ratings.
MIN_RATINGS_TO_LEAD = 20

# The fallback, by cuisine (Emily's examples on the card, 2026-10-05). A
# plain editable table: (the cook's name as the page shows it, their site).
# "*" is every cuisine the table doesn't name. Matched on the dish's own
# cuisine field by the words in _CUISINE_WORDS.
TRUSTED_COOKS: dict[str, list[tuple[str, str]]] = {
    "indian": [
        ("Swasthi's Recipes", "indianhealthyrecipes.com"),
        ("Hebbar's Kitchen", "hebbarskitchen.com"),
        ("Cook with Manali", "cookwithmanali.com"),
        ("RecipeTin Eats", "recipetineats.com"),
    ],
    "*": [
        ("RecipeTin Eats", "recipetineats.com"),
    ],
}
_CUISINE_WORDS = {
    "indian": ("indian", "punjabi", "chettinad", "kerala", "goan", "gujarati",
               "bengali", "mughlai", "tamil", "hyderabadi", "maharashtrian", "andhra"),
}

# Changed a lot through Change recipe: this many kept rewrites and the
# credit line reads "Originally based on …" (card, 2026-10-05).
CHANGED_A_LOT_REWRITES = 2

# A dish the research came back empty for (the calls ran, nothing usable)
# is not searched again for this many days — a dish that keeps coming back
# empty must not be paid for every time it is planned.
EMPTY_RESEARCH_RETRY_DAYS = 14


def research_enabled() -> bool:
    """RECIPE_RESEARCH=off stops EVERY outbound call this feature makes —
    the research itself and the link checks alike."""
    return os.environ.get("RECIPE_RESEARCH", "on").strip().lower() not in ("off", "0", "false", "no")


# Source links are checked now and then; one that has stopped working is
# hidden rather than shown broken. "Now and then" = when the recipe page
# asks for it and the last check is older than this.
LINK_RECHECK_DAYS = 30


def dish_key(name: str) -> str:
    """The dish, as research is filed under it: lowercased, punctuation and
    runs of space collapsed, so "Chana Masala" and "chana  masala!" are one
    dish and one piece of research."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(name or "").lower())).strip()


def trusted_cooks_for(cuisine: str) -> list[tuple[str, str]]:
    text = str(cuisine or "").lower()
    for key, words in _CUISINE_WORDS.items():
        if any(w in text for w in words):
            return TRUSTED_COOKS[key]
    return TRUSTED_COOKS["*"]


def _host(url: str) -> str:
    try:
        host = (urlparse(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _num(value, kind):
    try:
        out = kind(value)
    except (TypeError, ValueError):
        return None
    return out if out >= 0 else None


def clean_sources(raw, *, seen_urls: set[str], blocked_urls: set[str],
                  read_urls: set[str] | None = None) -> list[dict]:
    """
    The sources the research call reported, kept only where they are real:
    an http(s) URL the call actually saw (a search result or a successful
    fetch), not one whose fetch was refused, one per URL. A rating outside
    0-5 is dropped rather than trusted. Order is the call's own.

    Each carries "read": whether the page was actually FETCHED and read.
    Only a read page can lead (pick_lead) — a rating the model reports for
    a page it only saw in a search snippet is not a rating anyone read.
    """
    seen = {_norm_url(u) for u in seen_urls}
    blocked = {_norm_url(u) for u in blocked_urls}
    read = {_norm_url(u) for u in (read_urls or set())}
    out, urls = [], set()
    for s in raw or []:
        if not isinstance(s, dict):
            continue
        url = str(s.get("url") or "").strip()
        key = _norm_url(url)
        if not url.startswith(("http://", "https://")) or key in urls:
            continue
        if key not in seen or key in blocked:
            continue
        rating = _num(s.get("rating"), float)
        if rating is not None and rating > 5:
            rating = None
        out.append({
            "name": str(s.get("name") or "").strip() or _host(url),
            "title": str(s.get("title") or "").strip(),
            "url": url,
            "rating": rating,
            "rating_count": _num(s.get("rating_count"), int),
            "read": key in read,
        })
        urls.add(key)
    return out


def _norm_url(url: str) -> str:
    return str(url or "").strip().rstrip("/").lower()


def is_well_rated(source: dict) -> bool:
    """A read page whose stars rest on enough ratings to mean something."""
    return (bool(source.get("read", True)) and source.get("rating") is not None
            and (source.get("rating_count") or 0) >= MIN_RATINGS_TO_LEAD)


def pick_lead(sources: list[dict], trusted: list[tuple[str, str]] | None = None) -> list[dict]:
    """
    The same sources, lead first, each with "lead" set.

    The lead is the well-rated version (MIN_RATINGS_TO_LEAD or more) with
    the top rating, and among equal ratings (to one decimal, which is how
    sites print stars) the most ratings. With no well-rated version and a
    trusted-cooks list, the first source from the earliest cook on that
    list leads; with neither, the first source the research named.

    Only a page that was actually READ can lead (review, 2026-10-06): one
    seen only in a search result, with a rating nobody fetched, stays an
    "other" at most. No read page at all is no lead, and [] comes back —
    research with nothing read behind it is not research.
    """
    readable = [s for s in sources if s.get("read", True)]
    if not readable:
        return []
    rated = [s for s in readable if is_well_rated(s)]
    if rated:
        lead = max(rated, key=lambda s: (round(s["rating"], 1), s["rating_count"] or 0))
    else:
        lead = None
        for _, site in trusted or []:
            lead = next((s for s in readable if _host(s["url"]).endswith(site)), None)
            if lead:
                break
        lead = lead or readable[0]
    rest = [s for s in sources if s is not lead]
    return [{**lead, "lead": True}] + [{**s, "lead": False} for s in rest]


def needs_trusted_cooks(sources: list[dict]) -> bool:
    """True when the open search came back with nothing that may lead —
    no version with enough ratings for its stars to mean much."""
    return not any(is_well_rated(s) for s in sources)


def children_eat_here() -> bool:
    """Whether a child eats at this table — the writer then keeps the pot
    mild and puts the heat on the table for the adults."""
    conn = get_conn()
    try:
        row = conn.execute(
            f"SELECT 1 FROM members WHERE household_id = ? AND {EATS_HERE_SQL} "
            "AND LOWER(TRIM(COALESCE(age_group, ''))) = 'child' LIMIT 1",
            (household_id(),),
        ).fetchone()
    finally:
        conn.close()
    return bool(row)


def recipe_research_id(recipe_id) -> int | None:
    if not recipe_id:
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT research_id FROM recipes WHERE id = ? AND household_id = ?",
            (int(recipe_id), household_id()),
        ).fetchone()
    finally:
        conn.close()
    return row["research_id"] if row and row["research_id"] else None


def household_changes_for(recipe_id: int) -> list[str]:
    """The recipe's "Changed for your household" lines; [] when none."""
    import json
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT household_changes_json FROM recipes WHERE id = ? AND household_id = ?",
            (int(recipe_id), household_id()),
        ).fetchone()
    finally:
        conn.close()
    try:
        out = json.loads(row["household_changes_json"] or "[]") if row else []
    except (TypeError, ValueError):
        return []
    return [str(x) for x in out if isinstance(x, str) and x.strip()] if isinstance(out, list) else []


# ---------- saved per household ----------

def saved_research(name: str) -> dict | None:
    """This household's research for a dish, or None — what makes a dish
    that comes back cost no second search. A row with no sources is a
    remembered EMPTY result (note_empty_research); callers check sources."""
    return _research_where("dish_key = ?", dish_key(name))


def research_by_id(research_id) -> dict | None:
    if not research_id:
        return None
    return _research_where("id = ?", int(research_id))


def _research_where(clause: str, value) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, dish_name, method_notes, fallback_used, created_at, "
            "julianday('now') - julianday(created_at) AS age_days FROM dish_research "
            f"WHERE household_id = ? AND {clause}",
            (household_id(), value),
        ).fetchone()
        if not row:
            return None
        sources = conn.execute(
            "SELECT name, title, url, rating, rating_count, is_lead, link_ok, link_checked_at "
            "FROM recipe_sources WHERE household_id = ? AND research_id = ? ORDER BY position",
            (household_id(), row["id"]),
        ).fetchall()
    finally:
        conn.close()
    return {
        "id": row["id"],
        "dish_name": row["dish_name"],
        "method_notes": row["method_notes"],
        "fallback_used": bool(row["fallback_used"]),
        "created_at": row["created_at"],
        "age_days": row["age_days"] or 0,
        "sources": [
            {
                "name": s["name"], "title": s["title"], "url": s["url"],
                "rating": s["rating"], "rating_count": s["rating_count"],
                "lead": bool(s["is_lead"]), "link_ok": bool(s["link_ok"]),
                "link_checked_at": s["link_checked_at"],
            }
            for s in sources
        ],
    }


def save_research(name: str, sources: list[dict], method_notes: str, fallback_used: bool) -> dict | None:
    """
    File this household's research for a dish. `sources` lead first (as
    pick_lead returns them). Nothing is saved without at least one source:
    research with nothing behind it is not research, and saving it would
    stop the next attempt. A second save for a dish already filed keeps the
    first — two approvals racing must not swap the sources under a recipe
    already written from them.
    """
    if not sources:
        return None
    key = dish_key(name)
    with write() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id, (SELECT COUNT(*) FROM recipe_sources s WHERE s.research_id = d.id "
            "AND s.household_id = d.household_id) AS n "
            "FROM dish_research d WHERE household_id = ? AND dish_key = ?",
            (household_id(), key),
        ).fetchone()
        if existing and not existing["n"]:
            # A remembered empty result, now superseded by a real one.
            conn.execute("DELETE FROM dish_research WHERE id = ? AND household_id = ?",
                         (existing["id"], household_id()))
            existing = None
        if not existing:
            rid = conn.execute(
                "INSERT INTO dish_research (household_id, dish_key, dish_name, method_notes, fallback_used) "
                "VALUES (?, ?, ?, ?, ?)",
                (household_id(), key, str(name or "").strip(), str(method_notes or "").strip(),
                 1 if fallback_used else 0),
            ).lastrowid
            for pos, s in enumerate(sources, start=1):
                conn.execute(
                    "INSERT INTO recipe_sources (household_id, research_id, position, name, title, url, "
                    "rating, rating_count, is_lead) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (household_id(), rid, pos, s.get("name") or "", s.get("title") or "", s["url"],
                     s.get("rating"), s.get("rating_count"), 1 if s.get("lead") else 0),
                )
    return saved_research(name)


def note_empty_research(name: str) -> None:
    """Remember that this dish's research ran and found nothing usable, so
    it is not paid for again for EMPTY_RESEARCH_RETRY_DAYS (a dish_research
    row with no sources). An existing empty row has its clock restarted."""
    key = dish_key(name)
    with write() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id FROM dish_research WHERE household_id = ? AND dish_key = ?",
            (household_id(), key),
        ).fetchone()
        if existing:
            n = conn.execute("SELECT COUNT(*) AS n FROM recipe_sources WHERE research_id = ? AND household_id = ?",
                             (existing["id"], household_id())).fetchone()["n"]
            if not n:
                conn.execute("UPDATE dish_research SET created_at = datetime('now') WHERE id = ? AND household_id = ?",
                             (existing["id"], household_id()))
            return
        conn.execute(
            "INSERT INTO dish_research (household_id, dish_key, dish_name) VALUES (?, ?, ?)",
            (household_id(), key, str(name or "").strip()),
        )


def empty_recently(research: dict | None) -> bool:
    return bool(research) and not research.get("sources") and \
        (research.get("age_days") or 0) < EMPTY_RESEARCH_RETRY_DAYS


def research_for_writer(research: dict | None) -> dict | None:
    """What the recipe writer is handed: the lead, the others, and the
    notes on where they agree and differ. No links it could copy from."""
    if not research or not research.get("sources"):
        return None
    lead = next((s for s in research["sources"] if s.get("lead")), research["sources"][0])
    return {
        "lead": _for_writer(lead),
        "others": [_for_writer(s) for s in research["sources"] if s is not lead],
        "what_they_agree_and_differ_on": research.get("method_notes") or "",
    }


def _for_writer(s: dict) -> dict:
    out = {"name": s.get("name") or "", "title": s.get("title") or ""}
    if s.get("rating") is not None:
        out["rating"] = s["rating"]
    if s.get("rating_count") is not None:
        out["rating_count"] = s["rating_count"]
    return out


# ---------- what the recipe page shows ----------

def _kept_rewrites(recipe_id: int) -> int:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM recipe_change_requests "
            "WHERE household_id = ? AND recipe_id = ? AND outcome = 'rewritten'",
            (household_id(), int(recipe_id)),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"] or 0) if row else 0


def _join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def recipe_research_for(recipe: dict, *, check_links: bool = True) -> dict | None:
    """
    The recipe page's sources, for a recipe dict (needs "id" and "name"):
    {"credit_prefix", "credit", "lead", "others", "sources"} or None when
    the recipe was written without research. A source whose link has been
    found broken is left out; when the lead's link is the broken one, the
    credit still names it (the recipe IS based on it) but it isn't linked.

    `check_links` kicks off a background recheck of links older than
    LINK_RECHECK_DAYS — never in the request's path.
    """
    # The research THIS recipe was written from (recipes.research_id), never
    # a lookup by name: a household's own hand-written or imported "Chana
    # Masala" was not based on anything Pomona read (review, 2026-10-06).
    research = research_by_id(recipe_research_id(recipe.get("id")))
    if not research or not research["sources"]:
        return None
    if check_links and research_enabled():
        start_link_check(research)
    lead = next((s for s in research["sources"] if s["lead"]), research["sources"][0])
    others = [s for s in research["sources"] if s is not lead]
    prefix = "Originally based on" if _kept_rewrites(recipe.get("id") or 0) >= CHANGED_A_LOT_REWRITES else "Based on"
    credit = f"{prefix} {lead['name']}"
    if others:
        credit += f", checked against {_join_names([s['name'] for s in others])}"

    def shown(s):
        return {k: s[k] for k in ("name", "title", "url", "rating", "rating_count", "lead")}

    visible = [shown(s) for s in [lead] + others if s["link_ok"]]
    return {
        "credit_prefix": prefix,
        "credit": credit,
        "lead": shown(lead) if lead["link_ok"] else None,
        "others": [shown(s) for s in others if s["link_ok"]],
        "sources": visible,
    }


# ---------- links checked now and then ----------

_CHECKING: set[int] = set()
_CHECKING_GUARD = threading.Lock()


_LINK_MAX_HOPS = 3
_LINK_TIMEOUT = 5.0


def _resolve(host: str) -> list[str]:
    """Every address the host resolves to. A seam, so tests resolve without
    the network; raises socket.gaierror for a name that doesn't exist."""
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})


def _public_address(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
                or addr.is_multicast or addr.is_unspecified
                or getattr(addr, "is_site_local", False))


def _request(scheme: str, host: str, port: int, ip: str, method: str, path: str) -> tuple[int, str]:
    """
    One HTTP request to an address already checked as public, by IP — so
    the name can't re-resolve somewhere else between the check and the
    connect — with the real host in the Host header and in TLS (SNI and the
    certificate check). Never follows a redirect; reads at most 1 KB.
    Returns (status, Location header or '').
    """
    sock = socket.create_connection((ip, port), timeout=_LINK_TIMEOUT)
    try:
        if scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        conn = http.client.HTTPConnection(host, port, timeout=_LINK_TIMEOUT)
        conn.sock = sock
        conn.request(method, path or "/", headers={
            "Host": host, "User-Agent": "Mozilla/5.0 (Pomona link check)", "Accept": "text/html",
        })
        resp = conn.getresponse()
        resp.read(1024)
        return resp.status, resp.getheader("Location") or ""
    finally:
        try:
            sock.close()
        except Exception:
            pass


def link_status(url: str) -> bool | None:
    """
    True when the page answers, False when it is GONE (404 / 410, or the
    site no longer resolves), None when we can't tell. A 403 or 429 is not
    broken: plenty of recipe sites refuse a bot and serve a person fine, and
    hiding a working link because it blocked us would be the wrong mistake.

    Safe to point at any URL (review, 2026-10-06 — these URLs came off the
    web): http/https only; every address the host resolves to must be
    public (no private, loopback, link-local or reserved ranges); the
    request goes to that checked address; redirects are followed by hand,
    at most _LINK_MAX_HOPS, each hop checked the same way. HEAD, then a GET
    reading 1 KB when HEAD isn't allowed. Anything refused is None.
    """
    current = url
    for _ in range(_LINK_MAX_HOPS + 1):
        try:
            parts = urlparse(current)
        except ValueError:
            return None
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        host = parts.hostname
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError:
            return None
        try:
            addresses = _resolve(host)
        except socket.gaierror:
            return False  # the site no longer exists
        except Exception:
            return None
        if not addresses or not all(_public_address(ip) for ip in addresses):
            return None
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        try:
            status, location = _request(parts.scheme, host, port, addresses[0], "HEAD", path)
            if status in (405, 501):
                status, location = _request(parts.scheme, host, port, addresses[0], "GET", path)
        except Exception:
            return None
        if status in (301, 302, 303, 307, 308) and location:
            current = urljoin(current, location)
            continue
        if 200 <= status < 300:
            return True
        if status in (404, 410):
            return False
        return None
    return None  # too many redirects: can't tell


def due_for_check(source: dict) -> bool:
    checked = source.get("link_checked_at")
    if not checked:
        return True
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT julianday('now') - julianday(?) AS age", (checked,)
        ).fetchone()
    finally:
        conn.close()
    return row["age"] is None or row["age"] >= LINK_RECHECK_DAYS


def check_research_links(research: dict) -> int:
    """Recheck every due link on one dish's research and record the answer.
    An unknown answer (None) records the check but keeps the link shown.
    Returns how many links were marked broken."""
    broken = 0
    hid = household_id()
    for s in research.get("sources") or []:
        if not due_for_check(s):
            continue
        ok = link_status(s["url"])
        with write() as conn:
            if ok is False:
                broken += 1
                conn.execute(
                    "UPDATE recipe_sources SET link_ok = 0, link_checked_at = datetime('now') "
                    "WHERE household_id = ? AND research_id = ? AND url = ?",
                    (hid, research["id"], s["url"]),
                )
            else:
                conn.execute(
                    "UPDATE recipe_sources SET link_ok = CASE WHEN ? THEN 1 ELSE link_ok END, "
                    "link_checked_at = datetime('now') WHERE household_id = ? AND research_id = ? AND url = ?",
                    (1 if ok else 0, hid, research["id"], s["url"]),
                )
    return broken


def start_link_check(research: dict) -> bool:
    """Run check_research_links on a background thread when anything is
    due, once per dish at a time. Returns whether a check was started."""
    if not research_enabled():
        return False
    if not any(due_for_check(s) for s in research.get("sources") or []):
        return False
    rid = int(research["id"])
    with _CHECKING_GUARD:
        if rid in _CHECKING:
            return False
        _CHECKING.add(rid)
    hid = household_id()

    def _run():
        from ._shared import use_household
        try:
            with use_household(hid):
                check_research_links(research)
        except Exception:
            import logging
            logging.getLogger("home_manager").exception("Checking recipe source links failed")
        finally:
            with _CHECKING_GUARD:
                _CHECKING.discard(rid)

    threading.Thread(target=_run, name=f"recipe-links-{rid}", daemon=True).start()
    return True


# ---------- what is worth a web search (2026-10-06) ----------
#
# Loop Board "Approve must not wait on recipe web research": the first live
# night researched "Apple Slices with Cheese" and "Hummus with Carrot and
# Cucumber Sticks" at up to 558K input tokens each. Nobody needs a
# well-rated source for those. Snacks, breakfasts and simple assemblies
# (four ingredients or fewer, or nothing cooked) are written from the
# model's own knowledge; research is for dinners and lunches that cook.

_NEVER_RESEARCHED_SLOTS = ("snack", "breakfast")
_SIMPLE_ASSEMBLY_MAX_INGREDIENTS = 4


def worth_researching(recipe: dict) -> bool:
    """True when this pending dish is one a web search could earn its cost
    on: not a snack or breakfast (by slot or tag), and not a simple
    assembly — known to have <= 4 ingredients, or a cook time of zero."""
    slot = str(recipe.get("slot") or "").lower()
    if any(s in slot for s in _NEVER_RESEARCHED_SLOTS):
        return False
    tags = {str(t).lower() for t in (recipe.get("tags") or [])}
    if tags & {"snack", "breakfast", "no-cook", "no cook"}:
        return False
    ingredients = recipe.get("ingredients")
    if ingredients and len(ingredients) <= _SIMPLE_ASSEMBLY_MAX_INGREDIENTS:
        return False
    if recipe.get("cook_time_minutes") == 0:
        return False
    return True


def _env_number(name: str, default, kind=int):
    try:
        value = kind(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def dish_budget() -> dict:
    """The per-dish web budget: 1 search + 2 page reads unless
    RECIPE_RESEARCH_SEARCHES / RECIPE_RESEARCH_FETCHES say otherwise."""
    return {"web_search": _env_number("RECIPE_RESEARCH_SEARCHES", 1),
            "web_fetch": _env_number("RECIPE_RESEARCH_FETCHES", 2)}


def fetch_tokens() -> int:
    """Most of a fetched page the model is handed (RECIPE_RESEARCH_FETCH_TOKENS,
    default 3,000 — a recipe's ingredients and method fit; comments don't)."""
    return max(500, _env_number("RECIPE_RESEARCH_FETCH_TOKENS", 3000))


def dish_seconds() -> float:
    """Wall-clock cap on one dish's whole research step
    (RECIPE_RESEARCH_DISH_SECONDS, default 60)."""
    return _env_number("RECIPE_RESEARCH_DISH_SECONDS", 60.0, float)


def max_dishes_per_plan() -> int:
    """Most dishes one plan researches (RECIPE_RESEARCH_MAX_DISHES, default 5)."""
    return _env_number("RECIPE_RESEARCH_MAX_DISHES", 5)
