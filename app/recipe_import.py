"""
Bring a recipe in from a link (Loop Board: "Recipes: bring in a recipe from
a link instead of retyping it", 2026-09-11).

Two halves, deliberately separate:

1. FETCHING a page the household pasted — which is the server making an
   HTTP request to a URL a user typed, so it is treated as hostile input
   from the first line. Only http/https; the host is resolved and every
   address it resolves to must be a public one (no loopback, private,
   link-local, cloud-metadata, multicast, reserved); the connection is
   made to the address that passed the check rather than to the name (so
   a DNS answer can't change between the check and the connect); at most
   a few redirects, each hop re-checked; a size cap; a short timeout; a
   plain User-Agent. The page is only ever read as text — nothing fetched
   is rendered into the app.

2. EXTRACTING a recipe draft from the page. Most recipe sites publish
   schema.org Recipe markup (a <script type="application/ld+json"> block),
   which is exact, so that goes first; a page without it falls back to a
   model reading the visible text (agent.read_recipe_from_page_llm),
   and the draft says which it was so the review step can say so too.

Nothing here saves anything. The draft goes back to the household to
review and edit, and the save goes through tools.add_recipe like every
other recipe — the same review-before-save shape the receipt and fridge
scans use.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import re
import socket
import ssl
import time
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlsplit

from .tools import quantities as _quantities

logger = logging.getLogger(__name__)


class RecipeImportError(Exception):
    """A refusal or failure with a sentence the household can be shown.
    `kind` is for tests and logs; the message is the product."""

    def __init__(self, message: str, kind: str = "failed"):
        super().__init__(message)
        self.kind = kind


# ---------- limits ----------

MAX_BYTES = 3 * 1024 * 1024          # a long recipe page with its comments is well under this
MAX_REDIRECTS = 3
# Two clocks. TIMEOUT_SECONDS is the socket timeout — how long ONE connect,
# send or receive may sit with nothing arriving. TOTAL_SECONDS is the
# wall-clock budget for the whole fetch, redirects included, checked
# between reads: a server that trickles a byte at a time never trips a
# socket timeout, and without the second clock it would hold a worker
# thread for as long as it liked. It can still overrun by at most one
# socket timeout (a read that is mid-wait when the budget runs out).
TIMEOUT_SECONDS = 8.0
TOTAL_SECONDS = 15.0
MAX_MODEL_TEXT_CHARS = 40_000        # what the fallback model is shown — plenty for one recipe
USER_AGENT = "Pomona/1.0 (household recipe import; +https://pomona.app)"
ALLOWED_PORTS = {None, 80, 443}

_HTML_TYPES = ("text/html", "application/xhtml+xml")

# Sentences the household sees. Calm, plain, and each paired with its way
# out (DESIGN_SYSTEM §8) — the way out itself is drawn by the sheet.
MSG_BAD_URL = "That doesn't look like a web link — it should start with http:// or https://."
MSG_CREDENTIALS = "That link has a username or password in it — paste the plain page address instead."
MSG_BLOCKED = "I can only read public web pages, not addresses inside a home or office network."
MSG_UNREACHABLE = "I couldn't reach that page. Check the link, or try again in a moment."
MSG_TOO_BIG = "That page is too large for me to read."
MSG_NOT_HTML = "That link isn't a web page I can read — a PDF or an image, maybe."
MSG_NO_RECIPE = "I couldn't find a recipe on that page."


# ---------- URL and address checks (SSRF) ----------

def _check_url(url: str) -> tuple[str, str, int | None, str]:
    """Validate a URL's shape and return (scheme, host, port, path+query).
    Refuses anything that isn't plain http(s) to a named host on a normal
    port, with no credentials in it."""
    if not isinstance(url, str) or not url.strip():
        raise RecipeImportError(MSG_BAD_URL, "bad_url")
    try:
        # urlsplit itself raises on a broken IPv6 bracket ("http://[::1/")
        # or userinfo that isn't an address ("http://[::1]@host/").
        parts = urlsplit(url.strip())
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise RecipeImportError(MSG_BAD_URL, "bad_url")
    if parts.scheme not in ("http", "https"):
        raise RecipeImportError(MSG_BAD_URL, "bad_url")
    if parts.username is not None or parts.password is not None:
        # Said plainly rather than as "should start with http://", which
        # would be untrue of this input (§8: state the actual thing).
        raise RecipeImportError(MSG_CREDENTIALS, "bad_url")
    if not hostname:
        raise RecipeImportError(MSG_BAD_URL, "bad_url")
    if port not in ALLOWED_PORTS:
        raise RecipeImportError(MSG_BLOCKED, "blocked")
    host = hostname.rstrip(".").lower()
    if host in ("localhost",) or host.endswith((".localhost", ".local", ".internal", ".home.arpa")):
        raise RecipeImportError(MSG_BLOCKED, "blocked")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return parts.scheme, host, port, path


def _is_public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """A routable, public address and nothing else. An IPv4-mapped IPv6
    address is judged as the IPv4 address inside it."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    ):
        return False
    return bool(ip.is_global)


def resolve_public_address(host: str, port: int) -> str:
    """Resolve `host` and return ONE public address to connect to. Refuses
    the whole name if any address it resolves to is not public — a name
    that answers with one public and one private address is not one we
    want to be connecting to."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_address(literal):
            raise RecipeImportError(MSG_BLOCKED, "blocked")
        return str(literal)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        # gaierror for a name that doesn't exist; UnicodeError when the
        # name can't even be encoded for DNS (a 64-character label, say).
        raise RecipeImportError(MSG_UNREACHABLE, "unreachable")
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    if not addresses:
        raise RecipeImportError(MSG_UNREACHABLE, "unreachable")
    for ip in addresses:
        if not _is_public_address(ip):
            raise RecipeImportError(MSG_BLOCKED, "blocked")
    return str(addresses[0])


# ---------- fetching ----------

class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS to a specific, already-checked address, with the certificate and
    SNI still checked against the hostname the household typed."""

    def __init__(self, host: str, ip: str, port: int, timeout: float, context: ssl.SSLContext):
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = ip

    def connect(self):
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _open_connection(scheme: str, host: str, ip: str, port: int, timeout: float):
    if scheme == "https":
        return _PinnedHTTPSConnection(host, ip, port, timeout, ssl.create_default_context())
    return http.client.HTTPConnection(ip, port, timeout=timeout)


def _read_capped(response, cap: int, deadline: float | None = None) -> bytes:
    """Read the body up to `cap` bytes, giving up at `deadline` (a
    time.monotonic() value). read1 where the response offers it, so a
    trickling server hands back whatever has arrived rather than blocking
    until a full chunk has — that is what lets the deadline be checked."""
    read = getattr(response, "read1", None) or response.read
    chunks, total = [], 0
    while True:
        chunk = read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise RecipeImportError(MSG_TOO_BIG, "too_big")
        chunks.append(chunk)
        if deadline is not None and time.monotonic() > deadline:
            raise RecipeImportError(MSG_UNREACHABLE, "timeout")
    return b"".join(chunks)


def _decode(body: bytes, content_type: str) -> str:
    match = re.search(r"charset=\"?([\w.-]+)", content_type or "", re.I)
    for encoding in ([match.group(1)] if match else []) + ["utf-8"]:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def fetch_page(url: str) -> tuple[str, str]:
    """
    GET a user-supplied URL under the rules at the top of this file and
    return (final_url, html_text). Every refusal is a RecipeImportError
    with the sentence to show. The whole thing — every hop, every read —
    fits inside TOTAL_SECONDS of wall clock (see the note by the limits).
    """
    current = url.strip() if isinstance(url, str) else url
    deadline = time.monotonic() + TOTAL_SECONDS
    for _hop in range(MAX_REDIRECTS + 1):
        scheme, host, port, path = _check_url(current)
        real_port = port or (443 if scheme == "https" else 80)
        ip = resolve_public_address(host, real_port)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RecipeImportError(MSG_UNREACHABLE, "timeout")
        conn = _open_connection(scheme, host, ip, real_port, min(TIMEOUT_SECONDS, remaining))
        try:
            conn.request("GET", path, headers={
                "Host": host if port is None else f"{host}:{port}",
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "en",
            })
            response = conn.getresponse()
            status = response.status
            if status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise RecipeImportError(MSG_UNREACHABLE, "unreachable")
                current = urljoin(current, location)
                continue
            if status != 200:
                raise RecipeImportError(MSG_UNREACHABLE, "unreachable")
            content_type = response.getheader("Content-Type") or ""
            if not content_type.lower().startswith(_HTML_TYPES):
                raise RecipeImportError(MSG_NOT_HTML, "not_html")
            declared = response.getheader("Content-Length")
            if declared and declared.isdigit() and int(declared) > MAX_BYTES:
                raise RecipeImportError(MSG_TOO_BIG, "too_big")
            body = _read_capped(response, MAX_BYTES, deadline)
            return current, _decode(body, content_type)
        except RecipeImportError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as e:
            # Timeouts, refused connections, TLS failures, malformed
            # responses: all "couldn't reach it" to the household.
            logger.info("Recipe import fetch failed for %s: %s", host, e)
            raise RecipeImportError(MSG_UNREACHABLE, "unreachable")
        finally:
            conn.close()
    raise RecipeImportError(MSG_UNREACHABLE, "unreachable")


# ---------- reading the HTML ----------

class _PageReader(HTMLParser):
    """Collects the JSON-LD blocks, the <title>, and the visible text. The
    visible text is what the model fallback reads; nothing here keeps any
    markup."""

    _SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe"}
    _BREAK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.json_ld: list[str] = []
        self.title = ""
        self._text: list[str] = []
        self._in_json_ld = False
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attrs = dict(attrs)
            if (attrs.get("type") or "").strip().lower() == "application/ld+json":
                self._in_json_ld = True
                self._json_buf: list[str] = []
                return
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self._text.append("\n")

    def handle_endtag(self, tag):
        if tag == "script" and self._in_json_ld:
            self._in_json_ld = False
            self.json_ld.append("".join(self._json_buf))
            return
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAK:
            self._text.append("\n")

    def handle_data(self, data):
        if self._in_json_ld:
            self._json_buf.append(data)
            return
        if self._in_title:
            self.title += data
            return
        if self._skip_depth == 0:
            self._text.append(data)

    def visible_text(self) -> str:
        text = "".join(self._text)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r" *\n+ *", "\n", text)
        return text.strip()


def read_page(html: str) -> _PageReader:
    reader = _PageReader()
    try:
        reader.feed(html or "")
        reader.close()
    except Exception:  # a page too broken to parse is a page with no recipe
        logger.info("Recipe import: HTML parse gave up part-way", exc_info=True)
    return reader


# ---------- schema.org Recipe ----------

def _types(node: dict) -> set[str]:
    raw = node.get("@type")
    if isinstance(raw, str):
        return {raw.lower()}
    if isinstance(raw, list):
        return {t.lower() for t in raw if isinstance(t, str)}
    return set()


def _find_recipe_nodes(data) -> list[dict]:
    """Every dict with @type Recipe anywhere in a JSON-LD document — top
    level, in an array, inside @graph, or nested in something else."""
    found: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            if "recipe" in _types(node):
                found.append(node)
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return found


def find_recipe_json_ld(blocks: list[str]) -> dict | None:
    for block in blocks:
        text = (block or "").strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except RecursionError:
            # Nested a thousand levels deep is not a recipe, it's a page
            # trying to knock the parser over. Move on.
            continue
        except ValueError:
            # Some sites wrap the JSON in an HTML comment or leave a
            # trailing comma; one salvage attempt, then move on.
            cleaned = re.sub(r"^\s*<!--|-->\s*$", "", text)
            try:
                data = json.loads(cleaned)
            except (ValueError, RecursionError):
                continue
        try:
            nodes = _find_recipe_nodes(data)
        except RecursionError:
            continue
        if nodes:
            return nodes[0]
    return None


_DURATION_RE = re.compile(
    r"^P(?:(?P<d>\d+(?:\.\d+)?)D)?(?:T(?:(?P<h>\d+(?:\.\d+)?)H)?(?:(?P<m>\d+(?:\.\d+)?)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?)?$",
    re.I,
)


def parse_duration_minutes(value) -> int | None:
    """ISO-8601 duration ("PT1H30M") to whole minutes; also accepts a bare
    number of minutes or "45 min"/"1 hr 20 min" text. None when unreadable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(value)) if value > 0 else None
    text = str(value).strip()
    if not text:
        return None
    match = _DURATION_RE.match(text)
    if match and any(match.group(g) for g in ("d", "h", "m", "s")):
        days = float(match.group("d") or 0)
        hours = float(match.group("h") or 0)
        minutes = float(match.group("m") or 0)
        seconds = float(match.group("s") or 0)
        total = days * 1440 + hours * 60 + minutes + seconds / 60
        return int(round(total)) or None
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", text, re.I)
    mins = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)\b", text, re.I)
    if hours or mins:
        total = (float(hours.group(1)) if hours else 0) * 60 + (float(mins.group(1)) if mins else 0)
        return int(round(total)) or None
    if text.isdigit():
        return int(text) or None
    return None


def parse_servings(value) -> int | None:
    """recipeYield comes as 4, "4", "4 servings", "Serves 6", "6-8", or a
    list of those. The first whole number wins; a range takes its low end
    because that is what the quantities are written for."""
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            got = parse_servings(item)
            if got:
                return got
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    n = int(match.group(0))
    return n if 0 < n < 1000 else None


def _text_of(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "name", "@value"):
            if isinstance(value.get(key), str):
                return value[key]
    return ""


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([.,;:!?])", r"\1", text)


def parse_instructions(value) -> list[str]:
    """recipeInstructions is a string, a list of strings, a list of
    HowToStep objects, or HowToSections each holding steps. Flatten to an
    ordered list of step sentences."""
    steps: list[str] = []

    def walk(node):
        if isinstance(node, str):
            for line in re.split(r"\n+", node):
                line = _clean_text(line)
                if line:
                    steps.append(line)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if "itemlistelement" in {k.lower() for k in node}:
                walk(node.get("itemListElement"))
            else:
                text = _clean_text(_text_of(node))
                if text:
                    steps.append(text)

    walk(value)
    # A numbered site sometimes numbers the text too ("1. Preheat…").
    return [re.sub(r"^(?:step\s*)?\d+\s*[.):-]\s*", "", s, flags=re.I) for s in steps]


# ---------- ingredient lines -> {item, qty} ----------

_UNICODE_FRACTIONS = {
    "½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4",
    "⅕": "1/5", "⅖": "2/5", "⅗": "3/5", "⅘": "4/5", "⅙": "1/6", "⅚": "5/6",
    "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8",
}

# Words that can follow a number and mean an amount of the thing, not the
# thing itself. The measure units come from quantities._UNIT_ALIASES (so
# "tablespoons" and "tbsp" are both known); the rest are the kitchen and
# package words a recipe line uses.
_UNIT_WORDS = {
    w for w in _quantities._UNIT_ALIASES if _quantities._UNIT_ALIASES[w]
} | {
    "clove", "cloves", "can", "cans", "tin", "tins", "slice", "slices", "pinch", "pinches",
    "bunch", "bunches", "head", "heads", "stalk", "stalks", "stick", "sticks", "sprig", "sprigs",
    "package", "packages", "pkg", "packet", "packets", "jar", "jars", "bottle", "bottles",
    "bag", "bags", "box", "boxes", "handful", "handfuls", "dash", "dashes", "pint", "pints",
    "quart", "quarts", "gallon", "gallons", "piece", "pieces", "fillet", "fillets",
    "container", "containers", "tub", "tubs", "dozen", "ear", "ears", "leaf", "leaves",
    "sheet", "sheets", "strip", "strips", "cube", "cubes", "scoop", "scoops", "drop", "drops",
    "ml", "l", "g", "kg", "oz", "lb", "lbs", "cup", "cups", "tsp", "tbsp",
}
_SIZE_WORDS = {"large", "medium", "small", "whole", "jumbo", "extra-large", "xl", "heaping", "level", "scant"}

_NUMBER = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d*\.\d+|\d+)"
_LINE_RE = re.compile(
    rf"^\s*(?P<num>{_NUMBER})(?:\s*(?:-|–|—|to)\s*(?P<num2>{_NUMBER}))?"
    r"(?:\s*[x×](?=[\s\d]))?"
    r"\s*(?:\((?P<size>[^)]{1,30})\))?"
    r"\s*(?P<rest>.*)$",
    re.S,
)
_UNBRACKETED_SIZE_RE = re.compile(
    r"^(?P<n>\d+(?:\.\d+)?)[ -]?(?P<u>oz|ounce|ounces|g|gram|grams|ml|lb|pound|pounds|kg)\.?"
    r"(?=\s+(?:can|cans|tin|tins|package|packages|pkg|jar|jars|bag|bags|box|boxes|bottle|bottles|container|containers|packet|packets)\b)",
    re.I,
)
_TO_TASTE_RE = re.compile(r"^(?P<item>.*?)[,\s]*\b(?P<qty>to taste|as needed|for serving|for garnish|optional)\b\.?\s*$", re.I)


def _normalise_fractions(text: str) -> str:
    for glyph, ascii_ in _UNICODE_FRACTIONS.items():
        # "1½" is one and a half, "½" alone is a half.
        text = re.sub(rf"(\d)\s*{glyph}", rf"\1 {ascii_}", text)
        text = text.replace(glyph, ascii_)
    return text


def split_ingredient_line(line: str) -> dict:
    """
    "2 cups all-purpose flour, sifted" -> {"item": "all-purpose flour, sifted", "qty": "2 cups"}
    "1 (14 oz) can diced tomatoes"     -> {"item": "diced tomatoes", "qty": "1 can (14 oz)"}
    "Salt, to taste"                   -> {"item": "Salt", "qty": "to taste"}
    "Fresh basil leaves"               -> {"item": "Fresh basil leaves", "qty": ""}

    Deterministic and forgiving: what it can't read it leaves on the item
    for the household to fix in the review step, rather than guessing.
    The qty it writes is one quantities._parse_quantity reads back, so
    the grocery list and the cook view treat an imported line exactly
    like a generated one.
    """
    text = _clean_text(_normalise_fractions(line or ""))
    if not text:
        return {"item": "", "qty": ""}
    match = _LINE_RE.match(text)
    if not match or not match.group("num"):
        taste = _TO_TASTE_RE.match(text)
        if taste and taste.group("item").strip():
            return {"item": taste.group("item").strip(" ,"), "qty": taste.group("qty").lower()}
        return {"item": text, "qty": ""}

    amount = match.group("num2") or match.group("num")  # a range buys its top end
    size = (match.group("size") or "").strip()
    rest = match.group("rest").strip()

    # "1 14 oz can" / "2 400g tins" — the size written without brackets.
    unbracketed = _UNBRACKETED_SIZE_RE.match(rest)
    if unbracketed and not size:
        size = f"{unbracketed.group('n')} {unbracketed.group('u').lower()}"
        rest = rest[unbracketed.end("u"):].lstrip(". ")

    qty_words = []
    words = rest.split(" ")
    while words:
        word = words[0].lower().strip(".,")
        if word in _SIZE_WORDS or word in _UNIT_WORDS:
            qty_words.append(words.pop(0).strip(".,"))
            if word in _UNIT_WORDS:
                break
            continue
        break
    if words and words[0].lower() == "of":
        words.pop(0)
    item = " ".join(words).strip(" ,")
    unit = " ".join(qty_words)
    qty = f"{amount} {unit}".strip()
    if size:
        # "1 (14 oz) can" -> "1 can (14 oz)", which is how the grocery
        # layer writes a sized package.
        qty = f"{qty} ({size})"
    if not item:
        # "2 eggs" reads as number + item; a line that was only a number
        # and a unit ("2 cups") has nothing to name.
        return {"item": text, "qty": ""}
    return {"item": item, "qty": qty}


# ---------- a rough store section for the grocery list ----------

_CATEGORY_WORDS = (
    ("meat/seafood", ("chicken", "beef", "pork", "lamb", "turkey", "bacon", "sausage", "steak",
                      "ham", "salmon", "shrimp", "prawn", "fish", "cod", "tuna", "tilapia",
                      "brisket", "ribs", "chorizo", "veal", "duck", "anchov",
                      "scallop", "mussel", "clam", "crab", "lobster", "mince")),
    ("dairy", ("milk", "cheese", "butter", "cream", "yogurt", "yoghurt", "egg", "parmesan",
               "mozzarella", "cheddar", "feta", "ricotta", "mascarpone", "tofu", "paneer",
               "buttermilk", "ghee")),
    ("frozen", ("frozen", "ice cream")),
    ("produce", ("onion", "garlic", "tomato", "pepper", "lettuce", "spinach", "kale", "carrot",
                 "celery", "potato", "lemon", "lime", "orange", "apple", "banana", "berry",
                 "berries", "avocado", "cucumber", "zucchini", "courgette", "broccoli", "cauliflower",
                 "mushroom", "herb", "basil", "parsley", "cilantro", "coriander", "mint", "dill",
                 "thyme", "rosemary", "ginger", "chili", "chilli", "jalape", "cabbage", "leek",
                 "shallot", "scallion", "green onion", "spring onion", "corn", "peas", "bean",
                 "squash", "pumpkin", "eggplant", "aubergine", "asparagus", "arugula", "rocket",
                 "greens", "salad", "fruit", "grape", "melon", "peach", "pear", "plum", "mango",
                 "pineapple", "cherry", "strawberr", "blueberr", "raspberr", "radish", "beet",
                 "turnip", "sweet potato", "yam", "fennel", "artichoke", "chive")),
)


def guess_category(item: str, qty: str = "") -> str:
    """A store section from the ingredient's name — produce, dairy,
    meat/seafood, frozen — and pantry for everything with a name it
    doesn't recognise as fresh. A quantity that names a sealed package
    ("1 can (14 oz)" of tomatoes) says pantry whatever the name says.
    Deterministic; the household can change it on the list."""
    # The name before any prep note — "garlic, minced" is garlic.
    name = (item or "").split(",", 1)[0].strip().lower()
    if not name:
        return "other"
    if re.search(r"\b(can|cans|tin|tins|jar|jars|bottle|bottles|box|boxes|packet|packets|sachet)\b", (qty or "").lower()) \
            and not re.search(r"\bfrozen\b", name):
        return "pantry"
    # The words that fool a substring match, settled before it runs:
    # frozen anything is frozen; an eggplant is not an egg; and a shelf-
    # stable thing named after a fresh one (chicken broth, fish sauce, oat
    # milk, canned beans) is pantry.
    if re.search(r"\b(frozen|ice cream)\b", name):
        return "frozen"
    if re.search(r"\b(eggplant|aubergine)\b", name):
        return "produce"
    if re.search(
        r"\b(broth|stock|bouillon|sauce|powder|seasoning|extract|oil|vinegar|flour|sugar|rice|pasta|noodles)\b"
        r"|\b(coconut|almond|oat|soy|rice) milk\b|\bnutritional yeast\b|\bcream of tartar\b"
        r"|\bchickpeas?\b|\blentils?\b|\b(black|kidney|pinto|cannellini|baked|refried) beans\b",
        name,
    ):
        return "pantry"
    for category, words in _CATEGORY_WORDS:
        if any(w in name for w in words):
            # Dried/canned/powdered versions of fresh things live in the
            # pantry: "chili powder", "canned tomatoes", "dried basil".
            if category == "produce" and re.search(
                r"\b(dried|powder|canned|can of|tinned|paste|juice|flakes|sauce|oil|vinegar|jam|jelly)\b", name
            ):
                return "pantry"
            return category
    return "pantry"


# ---------- the draft ----------

DRAFT_FIELDS = (
    "name", "default_servings", "prep_time_minutes", "cook_time_minutes",
    "ingredients", "instructions", "cuisine", "main_protein", "source_url", "read_by",
)


def _first_string(value) -> str:
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, list):
        for item in value:
            got = _first_string(item)
            if got:
                return got
    if isinstance(value, dict):
        return _clean_text(_text_of(value))
    return ""


def draft_from_json_ld(node: dict, source_url: str) -> dict:
    """A recipe draft in the shape tools.add_recipe saves, from a
    schema.org Recipe node. Marked read_by="markup" — this path is exact."""
    raw_ingredients = node.get("recipeIngredient")
    if raw_ingredients is None:
        raw_ingredients = node.get("ingredients")  # the pre-2013 spelling some sites still use
    if isinstance(raw_ingredients, str):
        raw_ingredients = re.split(r"\n+", raw_ingredients)
    elif isinstance(raw_ingredients, dict):
        raw_ingredients = [raw_ingredients]
    ingredients = []
    for raw in raw_ingredients or []:
        text = _text_of(raw) if not isinstance(raw, str) else raw
        parsed = split_ingredient_line(text)
        if parsed["item"]:
            parsed["category"] = guess_category(parsed["item"], parsed["qty"])
            ingredients.append(parsed)

    prep = parse_duration_minutes(node.get("prepTime"))
    cook = parse_duration_minutes(node.get("cookTime"))
    total = parse_duration_minutes(node.get("totalTime"))
    if cook is None and total is not None:
        # A page that only says "45 minutes total" still tells the planner
        # something; the cook-time slot is the one the prep schedule reads.
        cook = max(total - (prep or 0), 0) or None

    return {
        "name": _first_string(node.get("name")) or _first_string(node.get("headline")),
        "default_servings": parse_servings(node.get("recipeYield")) or 4,
        "prep_time_minutes": prep,
        "cook_time_minutes": cook,
        "ingredients": ingredients,
        "instructions": parse_instructions(node.get("recipeInstructions")),
        "cuisine": _first_string(node.get("recipeCuisine")),
        "main_protein": "",
        "source_url": source_url,
        "read_by": "markup",
    }


def _usable(draft: dict | None) -> bool:
    """A draft is worth showing when it has a name and at least ingredients
    or steps — anything less is a page that wasn't really a recipe."""
    if not draft:
        return False
    return bool((draft.get("name") or "").strip()) and bool(draft.get("ingredients") or draft.get("instructions"))


def draft_from_model(detail: dict | None, source_url: str, fallback_name: str = "") -> dict | None:
    """Normalise what the model fallback returned into the same draft shape,
    marked read_by="model" so the review step can say it was read rather
    than copied. None when the model said there was no recipe."""
    if not detail or detail.get("found") is False:
        return None
    ingredients = []
    for raw in detail.get("ingredients") or []:
        if isinstance(raw, str):
            parsed = split_ingredient_line(raw)
        elif isinstance(raw, dict):
            parsed = {"item": _clean_text(raw.get("item") or ""), "qty": _clean_text(raw.get("qty") or "")}
            if parsed["item"] and not parsed["qty"]:
                parsed = split_ingredient_line(parsed["item"])
        else:
            continue
        if not parsed["item"]:
            continue
        category = raw.get("category") if isinstance(raw, dict) else None
        parsed["category"] = category if category in _quantities._GROCERY_SECTION_ORDER else guess_category(parsed["item"], parsed["qty"])
        ingredients.append(parsed)
    draft = {
        "name": _clean_text(detail.get("name") or "") or _clean_text(fallback_name),
        "default_servings": parse_servings(detail.get("default_servings")) or 4,
        "prep_time_minutes": parse_duration_minutes(detail.get("prep_time_minutes")),
        "cook_time_minutes": parse_duration_minutes(detail.get("cook_time_minutes")),
        "ingredients": ingredients,
        "instructions": [s for s in (_clean_text(str(x)) for x in (detail.get("instructions") or [])) if s],
        "cuisine": _clean_text(detail.get("cuisine") or ""),
        "main_protein": _clean_text(detail.get("main_protein") or ""),
        "source_url": source_url,
        "read_by": "model",
    }
    return draft if _usable(draft) else None


def extract_recipe_draft(
    html: str,
    source_url: str,
    model_reader: Callable[[str, str], dict | None] | None = None,
) -> dict:
    """
    The extraction order: schema.org Recipe JSON-LD first (exact), then
    the model over the page's visible text (marked as such). Raises
    RecipeImportError(MSG_NO_RECIPE) when neither finds one.

    `model_reader(page_text, page_title)` is injected so this module never
    imports the agent (which imports tools, which is where this lives
    next door) and so tests can hand in a stub.
    """
    page = read_page(html)
    node = find_recipe_json_ld(page.json_ld)
    if node is not None:
        try:
            draft = draft_from_json_ld(node, source_url)
        except RecursionError:
            draft = None  # steps nested past any sane depth: not a recipe
        if _usable(draft):
            return draft
    if model_reader is not None:
        text = page.visible_text()
        if text.strip():
            detail = model_reader(text[:MAX_MODEL_TEXT_CHARS], _clean_text(page.title))
            draft = draft_from_model(detail, source_url, fallback_name=page.title)
            if draft is not None:
                return draft
    raise RecipeImportError(MSG_NO_RECIPE, "no_recipe")


def import_recipe_from_url(
    url: str,
    model_reader: Callable[[str, str], dict | None] | None = None,
    fetch: Callable[[str], tuple[str, str]] = fetch_page,
) -> dict:
    """Fetch, then extract. `fetch` is injectable for tests — no test makes
    a real HTTP request."""
    final_url, html = fetch(url)
    return extract_recipe_draft(html, final_url, model_reader=model_reader)
