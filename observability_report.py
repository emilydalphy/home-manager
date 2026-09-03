"""
What broke, and is the app being used — printed for the morning report.

Run this, read the output, lead with anything under BROKEN. That is the
whole contract.

    python observability_report.py            # last 1 day of errors, 7 of usage
    python observability_report.py --days 7
    python observability_report.py --json     # for a machine to read

Exit codes, so a caller can branch without parsing: 0 nothing broke,
1 something broke, 2 no data could be read at all. The last one is
separate on purpose — "I couldn't look" and "I looked and it's fine" are
opposite facts, and an earlier version of this script returned 1 for both,
which would have reported breakage every night forever.

Where the numbers come from
---------------------------
Two sources, tried in that order:

1. **Over the web**, when HOME_MANAGER_URL and a passphrase are set. This
   is the one that works for the overnight routine, and the reason this
   file was rewritten. The routine runs as a cloud agent with a fresh
   clone of this repo — no Railway volume, no database file, nothing but
   the code. It signs in exactly as a browser does and reads
   /api/observability, one household per passphrase.

2. **Straight off a database file**, when DB_PATH points at one that
   exists. That is the local case: a dev copy, or a snapshot pulled down
   by hand. It reports every household in the file.

The first version of this script had only (2), with a docstring arguing
that a fresh clone "can read a database file." It cannot read *Railway's*
database file, which is the only one with anything in it — so the feature
recorded errors perfectly and then reported on an empty local database,
printing "Nothing broke" every morning no matter what happened. That is a
worse failure than no report at all, because it reads like good news.

Setting it up for the overnight run
-----------------------------------
Two environment variables in the environment the routine runs in:

    HOME_MANAGER_URL=https://home-manager-production-4949.up.railway.app
    HOME_MANAGER_PASSPHRASES=<household 1's passphrase>[,<household 2's>,...]

HOME_MANAGER_PASSWORD is accepted as a fallback for the second, so a
deployment that already sets it needs nothing new. One passphrase per
household, because a household's data is reachable only by signing into
it — there is deliberately no all-households view in the app, and this
script is not the place to invent one.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request


class NoData(Exception):
    """Neither source could be read. Says which, and what would fix it."""


# ---------- source 1: the live app, over the web ----------


def _passphrases() -> list[str]:
    # Only the plural variable is comma-separated. HOME_MANAGER_PASSWORD is
    # one household's real passphrase and is taken whole: splitting it
    # turned "correct horse, battery staple" into two wrong passphrases and
    # then reported a refusal, pointing at the wrong thing entirely.
    raw = os.environ.get("HOME_MANAGER_PASSPHRASES")
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    single = os.environ.get("HOME_MANAGER_PASSWORD", "")
    return [single] if single else []


def _base_url() -> str:
    return (os.environ.get("HOME_MANAGER_URL") or os.environ.get("PUBLIC_BASE_URL", "")).rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _sign_in(base: str, passphrase: str):
    """A cookie jar holding one household's session, or an explanation."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        # The app answers a good passphrase with a 303 to "/". Following it
        # would fetch the whole shell for nothing; the cookie is already set
        # on this response.
        _NoRedirect(),
    )
    body = urllib.parse.urlencode({"password": passphrase, "next": "/"}).encode()
    req = urllib.request.Request(f"{base}/login", data=body, method="POST")
    try:
        resp = opener.open(req, timeout=30)
    except urllib.error.HTTPError as e:
        resp = e
    if resp.status in (200, 401):
        # The login page rendered again rather than redirecting, i.e. the
        # passphrase was refused. Named explicitly, because "answered 401"
        # sends you reading code and "that passphrase is wrong" sends you
        # to the one place that can actually be fixed.
        raise NoData(
            f"{base} refused a passphrase. Check HOME_MANAGER_PASSPHRASES against "
            f"what you type to sign in — one passphrase per household, comma-separated."
        )
    if resp.status not in (302, 303, 307):
        raise NoData(f"{base}/login answered {resp.status}, which is not a sign-in.")
    return opener


def _get_json(opener, url: str) -> dict:
    with opener.open(urllib.request.Request(url), timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _collect_over_http(days: int) -> list[dict]:
    base, phrases = _base_url(), _passphrases()
    if not base or not phrases:
        missing = []
        if not base:
            missing.append("HOME_MANAGER_URL")
        if not phrases:
            missing.append("HOME_MANAGER_PASSPHRASES")
        raise NoData("not configured for the web: " + " and ".join(missing) + " unset")

    out = []
    for i, phrase in enumerate(phrases, start=1):
        try:
            opener = _sign_in(base, phrase)
            who = _get_json(opener, f"{base}/api/whoami")
            data = _get_json(opener, f"{base}/api/observability?days={int(days)}")
        except (NoData, urllib.error.URLError, OSError, ValueError) as e:
            # One household being unreachable must not hide the others —
            # including when the reason is a refused passphrase, which is
            # the likeliest failure of all and used to abort the whole run.
            # Emily losing her own report because the tester's passphrase
            # was rotated is the wrong trade.
            out.append(
                {
                    "household_id": None,
                    "household": f"passphrase #{i}",
                    "unreachable": str(e) if isinstance(e, NoData) else f"{type(e).__name__}: {e}",
                }
            )
            continue
        out.append(
            {
                "household_id": who.get("household_id"),
                # "household_name", not "name" — /api/whoami's actual key.
                # The first version guessed, and the unit test guessed the
                # same way, so both agreed and every household printed as
                # "household 2 (household 2)". Only running it against a
                # real server showed it.
                "household": who.get("household_name") or f"household {who.get('household_id')}",
                "errors": data["errors"],
                "usage": data["usage"],
            }
        )
    return out


# ---------- source 2: a database file on this machine ----------


def _collect_from_db(days: int) -> list[dict]:
    from app.db import DB_PATH

    # Checked before connecting: sqlite3.connect *creates* an empty file,
    # so probing a path that isn't there used to leave a stray zero-byte
    # database behind in the clone.
    if not os.path.exists(DB_PATH):
        raise NoData(f"no database file at {DB_PATH}")

    from app import tools
    from app.db import get_conn

    conn = get_conn()
    try:
        households = [
            (r["id"], r["name"])
            for r in conn.execute("SELECT id, name FROM households ORDER BY id").fetchall()
        ]
    except sqlite3.OperationalError as e:
        raise NoData(f"{DB_PATH} is not a Home Manager database ({e})")
    finally:
        conn.close()

    out = []
    for hid, name in households:
        # use_household is the same binding a request gets, so these read
        # exactly what that household would see and nothing else.
        with tools.use_household(hid):
            out.append(
                {
                    "household_id": hid,
                    "household": name,
                    "errors": tools.get_recent_errors(days=days),
                    "usage": tools.get_usage_summary(days=max(days, 7)),
                }
            )
    return out


def collect(days: int) -> tuple[list[dict], str]:
    """
    The report, and one line saying where it came from.

    The fallback is deliberately narrow: it applies only when the web was
    never configured, never when it was configured and failed.

    Falling back on failure quietly restored the exact thing this script
    was rewritten to prevent. With a stale local database present — any
    clone where the app has been run once — a rotated passphrase produced
    "(read from a local database) / Nothing broke." and exit 0, with the
    real reason printed nowhere. The one tell was a parenthetical on line
    one that nothing tells the reader to check. If HOME_MANAGER_URL is set,
    the live app is the answer or there is no answer.
    """
    if _base_url():
        return _collect_over_http(days), "the live app"

    try:
        return _collect_from_db(days), "a local database"
    except NoData as e:
        raise NoData(
            f"Nothing to report on.\n  a local database: {e}\n"
            f"  the live app: HOME_MANAGER_URL is unset, so the live app was not tried."
        )


# ---------- printing ----------


def _money(dollars: float) -> str:
    """
    Cents are too coarse to report this honestly. A household's week of
    chat can genuinely cost less than a penny, and rounding it to "$0.00"
    beside a per-turn figure of "$0.0021" prints a line that contradicts
    itself -- nothing, charged three times. Below a dollar, show enough
    decimal places to be true; above it, money looks like money.
    """
    return f"${dollars:,.2f}" if dollars >= 1 else f"${dollars:.4f}"


def _print_human(report: list[dict], days: int, source: str) -> None:
    print(f"(read from {source})")
    for h in report:
        print(f"\n=== {h['household']} (household {h['household_id']}) ===")

        if h.get("unreachable"):
            print(f"  UNREACHABLE — {h['unreachable']}")
            continue

        errors, usage = h["errors"], h["usage"]
        if errors["total"]:
            kinds = ", ".join(f"{n} {k}" for k, n in errors["by_kind"].items())
            print(f"  BROKEN — {errors['total']} in the last {days}d: {kinds}")
            # Grouped, so eleven copies of one failure read as one problem.
            seen: dict[tuple[str, str], int] = {}
            for row in errors["recent"]:
                key = (row["kind"], row["location"])
                seen[key] = seen.get(key, 0) + 1
            for (kind, where), n in sorted(seen.items(), key=lambda kv: -kv[1])[:8]:
                print(f"      {kind:10} {where}" + (f"  (x{n})" if n > 1 else ""))
        else:
            print("  Nothing broke.")

        if usage["looks_inactive"]:
            print(f"  QUIET — no chat, no meals cooked, no plans in {usage['days']}d.")
        else:
            print(
                f"  Used — {usage['chat_turns']} chat turns, "
                f"{usage['meals_cooked']} meals cooked, "
                f"{usage['plans_generated']} plans ({usage['plans_approved']} approved)"
            )
        # .get, not [], because this reads a *remote* app: a deployment
        # older than the cost work answers without the key, and a morning
        # report that crashes tells you less than one that omits a line.
        # The turn count is guarded too — a quiet household is the most
        # likely state in a beta, and it is the divisor below.
        cost = usage.get("cost")
        if cost and usage["chat_turns"]:
            print(
                f"  Chat cost — {_money(cost['total'])} over {usage['days']}d "
                f"({_money(cost['total'] / usage['chat_turns'])} a turn)"
            )
        print(f"  Last active: {usage['last_active_at'] or 'never'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="What broke, and is the app being used.")
    ap.add_argument("--days", type=int, default=1, help="how far back to look for errors")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    try:
        report, source = collect(args.days)
    except NoData as e:
        print(e, file=sys.stderr)
        print(
            "\nSet HOME_MANAGER_URL and HOME_MANAGER_PASSPHRASES to read the live "
            "app, or DB_PATH to read a database file.",
            file=sys.stderr,
        )
        return 2

    if args.json:
        print(json.dumps({"source": source, "households": report}, indent=2))
    else:
        _print_human(report, args.days, source)

    # Exit 1 when something is worth leading with. An unreachable household
    # counts: not knowing whether the tester had a bad day is itself the
    # thing to say out loud.
    return 1 if any(h.get("unreachable") or h["errors"]["total"] for h in report) else 0


if __name__ == "__main__":
    sys.exit(main())
