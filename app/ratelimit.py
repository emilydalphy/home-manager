"""
Per-caller rate limiting for the routes that cost money.

Every /api/chat turn runs a tool-use loop against Claude, and the photo
scans send a full image up. Without a limit, one caller — a bug in a retry
loop, a stuck browser tab, or someone who found the URL — can run the
household's API bill up as fast as they can send requests. The password
gate in security.py stops strangers; this stops accidents and caps the
damage if the password ever leaks.

Deliberately in-memory and per-process: this app runs as a single container,
so a shared store would be complexity with no benefit today. If it ever runs
more than one replica, move the counters to Redis — the call sites don't
change.
"""
import threading
import time

# (max requests, window in seconds) per bucket.
LIMITS = {
    "chat": [(30, 60), (300, 3600)],
    "scan": [(10, 60), (60, 3600)],
    # Sign-in attempts, so the shared password can't be brute-forced.
    "login": [(8, 300), (40, 3600)],
    # Browser error reports. A page stuck in an error loop can fire these
    # as fast as it renders, and one broken screen must not be able to
    # fill the error table with the same row. Generous enough that a real
    # burst of distinct failures still gets through.
    "client_error": [(20, 60), (200, 3600)],
    # "Something not working?" reports. Typed by a person, so the honest
    # ceiling is much lower than the browser reporter's — nobody writes
    # five paragraphs a minute — and this is the one table in the app
    # holding free text, so a script hammering it is the case worth
    # capping. Generous enough for someone sending two or three notes
    # about the same bad evening.
    "feedback": [(5, 60), (40, 3600)],
}

_lock = threading.Lock()
_hits: dict[tuple[str, str], list[float]] = {}

# Stop the dict growing forever from one-off callers: sweep entries whose
# newest hit is older than the longest window we track.
_MAX_AGE = 3600
_last_sweep = 0.0


def _sweep(now: float) -> None:
    global _last_sweep
    if now - _last_sweep < 300:
        return
    _last_sweep = now
    for key in [k for k, v in _hits.items() if not v or now - v[-1] > _MAX_AGE]:
        _hits.pop(key, None)


def check(bucket: str, caller: str) -> int | None:
    """
    Record a hit and return None if it's allowed, or the number of seconds
    to wait if the caller is over one of the bucket's limits.
    """
    limits = LIMITS.get(bucket)
    if not limits:
        return None
    now = time.time()
    longest = max(window for _, window in limits)
    key = (bucket, caller)
    with _lock:
        _sweep(now)
        hits = [t for t in _hits.get(key, []) if now - t < longest]
        for allowed, window in limits:
            in_window = [t for t in hits if now - t < window]
            if len(in_window) >= allowed:
                return max(1, int(window - (now - in_window[0])))
        hits.append(now)
        _hits[key] = hits
    return None


def caller_id(request) -> str:
    """
    Identify the caller. Railway (like most platforms) terminates TLS at a
    proxy, so request.client.host is the proxy — the real address comes from
    X-Forwarded-For. Falls back to the socket address without a proxy.

    The LAST entry, not the first, and the difference is the whole point.
    X-Forwarded-For is a list each proxy APPENDS to, so the leftmost value
    is whatever the original caller sent — a header, chosen by them. Reading
    it made every limit here advisory: a client that varies one header is a
    new caller on every request. That was measured on this code, not argued
    about — fifteen wrong passphrases from a fixed header were cut off after
    eight, and fifteen from a rotating one were not cut off at all, which
    left the login bucket's own promise ("so the shared password can't be
    brute-forced") not kept.

    The rightmost entry is the address OUR proxy observed and wrote down.
    A caller cannot forge it: anything they send is pushed left by that
    append.

    This assumes exactly one trusted proxy in front of the app, which is
    Railway today. Put a second layer in front — a CDN, another load
    balancer — and the trustworthy entry moves one place left, so this needs
    revisiting rather than silently trusting a hop that isn't ours. That is
    the point at which a configurable trusted-hop count earns its keep; one
    hop does not need it.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        # A trailing comma, or a header of nothing but separators, must not
        # collapse every caller into one shared "" bucket.
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            return hops[-1]
    return (request.client.host if request.client else "unknown") or "unknown"


def reset() -> None:
    """Clear all counters — used by the tests."""
    with _lock:
        _hits.clear()
