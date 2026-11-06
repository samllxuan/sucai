"""Landing-page titles: fetch each landing page and keep its title.

After the linking (`linking.py`) maps ads to landing pages, this opens
each page and stores a versioned snapshot with its title and meta description, so
the Landing pages view reads like a catalog instead of a list of URLs. No API
keys: just the page's own `og:title` / `<title>` and description.

A new snapshot is written when the page content changes (a new content hash).
Pages are re-checked at most every STALE_HOURS. Runs after every collection via
the `scheduler`, after linking.
"""

from __future__ import annotations

import hashlib
import html as htmllib
import ipaddress
import json
import logging
import re
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from curl_cffi import requests as curl
from psycopg.types.json import Jsonb

from .. import db

logger = logging.getLogger(__name__)

STALE_HOURS = 24        # don't recrawl a page more often than this
CRAWL_LIMIT = 300       # max pages per run
REQUEST_DELAY = 1.5     # seconds between crawls (politeness / anti-block)
FETCH_TIMEOUT = 20
MAX_TITLE_CHARS = 300
MAX_DESCRIPTION_CHARS = 500
MAX_GENRES = 12
MAX_GENRE_CHARS = 80
_HEAD_CHARS = 300_000   # titles/meta live in <head>; don't regex a whole huge page
MAX_REDIRECTS = 5
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


# ── Title / description extraction ─────────────────────────────────────────

_TAG_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_ANYTAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SERIES_INFO_RE = re.compile(
    r"""<x-data\b[^>]*\bid\s*=\s*["']_data_series_info["'][^>]*>(.*?)</x-data>""",
    re.IGNORECASE | re.DOTALL,
)
_LD_JSON_RE = re.compile(
    r"""<script\b[^>]*\btype\s*=\s*["']application/ld\+json["'][^>]*>(.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
)
_NEXT_FLIGHT_RE = re.compile(
    r"""self\.__next_f\.push\(\[1,\s*("(?:\\.|[^"\\])*")\]\)""",
    re.DOTALL,
)


def _normalize_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    text = _ANYTAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _clean(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    value = _WS_RE.sub(" ", htmllib.unescape(value)).strip()
    return value[:limit] or None


def _meta_tags(html: str) -> dict[str, str]:
    """{property-or-name (lowercased): content} for every <meta>, attribute order-agnostic.
    The first occurrence of a key wins."""
    found: dict[str, str] = {}
    for tag in _META_TAG_RE.findall(html):
        attrs = {m.group(1).lower(): next(g for g in m.groups()[1:] if g is not None)
                 for m in _ATTR_RE.finditer(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").strip().lower()
        content = attrs.get("content")
        if key and content and content.strip() and key not in found:
            found[key] = content
    return found


def extract_title_and_description(html: str) -> tuple[str | None, str | None]:
    """The page's title and meta description. og:title is preferred (usually free
    of the "| Brand" suffix), then twitter:title, then <title>. HTML comments and
    scripts are ignored: page templates often document "<title>" inside them."""
    head = _TAG_RE.sub(" ", _COMMENT_RE.sub(" ", html[:_HEAD_CHARS]))
    meta = _meta_tags(head)
    m = _TITLE_RE.search(head)
    title = (meta.get("og:title") or meta.get("twitter:title")
             or (_ANYTAG_RE.sub(" ", m.group(1)) if m else None))
    description = (meta.get("description") or meta.get("og:description")
                   or meta.get("twitter:description"))
    return _clean(title, MAX_TITLE_CHARS), _clean(description, MAX_DESCRIPTION_CHARS)


# ── Drama genres ─────────────────────────────────────────────────────────────

def _clean_genres(values) -> list[str]:
    """Source-provided genre labels, in order, with duplicates removed."""
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    out, seen = [], set()
    for value in values:
        if not isinstance(value, str):
            continue
        value = _clean(value, MAX_GENRE_CHARS)
        key = value.casefold() if value else ""
        if value and key not in seen:
            seen.add(key)
            out.append(value)
        if len(out) >= MAX_GENRES:
            break
    return out


def _mydramawave_genres(html: str) -> list[str]:
    """MyDramaWave embeds the series JSON in an `<x-data>` element."""
    match = _SERIES_INFO_RE.search(html)
    if not match:
        return []
    try:
        data = json.loads(htmllib.unescape(match.group(1)).strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return _clean_genres(data.get("content_tags")) if isinstance(data, dict) else []


def _next_flight_text(html: str) -> str:
    """Decode the JSON string literals Next.js streams through self.__next_f."""
    parts = []
    for match in _NEXT_FLIGHT_RE.finditer(html):
        try:
            value = json.loads(match.group(1))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


def _json_value(text: str, key: str):
    """Read one JSON value by key from a larger, not-quite-valid flight stream."""
    match = re.search(
        rf'"{re.escape(key)}"\s*:\s*("(?:\\.|[^"\\])*"|\[(?:\\.|[^\]])*\])',
        text,
        re.DOTALL,
    )
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _b25_genres(html: str) -> list[str]:
    """B25Reel pages carry book metadata in Next.js flight data."""
    text = _next_flight_text(html)
    if not text:
        return []
    return _clean_genres(_json_value(text, "tag"))


def _json_ld_genres(html: str) -> list[str]:
    """Schema.org entities commonly expose genres as `genre`."""
    for match in _LD_JSON_RE.finditer(html):
        payload = htmllib.unescape(match.group(1)).strip()
        try:
            data = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        items = data if isinstance(data, list) else [data]
        expanded = []
        for item in items:
            if not isinstance(item, dict):
                continue
            expanded.append(item)
            if isinstance(item.get("@graph"), list):
                expanded.extend(item["@graph"])
        genres = []
        for item in expanded:
            genres.extend(_clean_genres(item.get("genre")))
        cleaned = _clean_genres(genres)
        if cleaned:
            return cleaned
    return []


def extract_genres(html: str) -> tuple[list[str], str | None]:
    """Drama genres explicitly published by known landing-page formats.

    The labels are kept in their source language. A page without structured
    genre metadata returns no genres; titles and ad copy are never used as
    guesses.
    """
    for source, parser in (
        ("mydramawave", _mydramawave_genres),
        ("b25reel", _b25_genres),
        ("json-ld", _json_ld_genres),
    ):
        genres = parser(html)
        if genres:
            return genres, source
    return [], None


# ── Crawl + snapshot ────────────────────────────────────────────────────────

class BlockedDestination(Exception):
    """The URL (or a redirect) points at a non-public address."""


def _resolve(host: str, port: int) -> list[str]:
    """Every address `host` resolves to (injectable in tests)."""
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def is_public_url(url: str) -> bool:
    """http(s) to a host whose every address is on the public internet.

    Landing-page URLs come from competitors' ads, so they (or a redirect they
    serve) must not steer the worker into the private network: the Compose
    services, the host, or a cloud metadata endpoint.
    """
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not host:
        return False
    try:
        addresses = _resolve(host, port or (443 if parts.scheme == "https" else 80))
    except (OSError, UnicodeError):
        return False
    try:
        return bool(addresses) and all(
            ipaddress.ip_address(a.split("%", 1)[0]).is_global for a in addresses
        )
    except ValueError:
        return False


def _get(url: str):
    """One GET like a browser would, redirects NOT followed (injectable in tests)."""
    return curl.get(url, impersonate="chrome", timeout=FETCH_TIMEOUT, allow_redirects=False)


def _fetch(url: str):
    """GET the page, following redirects by hand so every hop is checked."""
    for _ in range(MAX_REDIRECTS + 1):
        if not is_public_url(url):
            raise BlockedDestination(url)
        resp = _get(url)
        location = resp.headers.get("location")
        if resp.status_code not in _REDIRECT_STATUSES or not location:
            return resp
        url = urljoin(url, location)
    raise BlockedDestination(f"too many redirects: {url}")


def _latest_snapshot(conn, lp_id: int) -> dict | None:
    return conn.execute(
        """SELECT id, content_hash, headline, description, genres, genres_source
           FROM page_snapshots
           WHERE landing_page_id = %s ORDER BY captured_at DESC, id DESC LIMIT 1""",
        (lp_id,),
    ).fetchone()


def _mark(conn, lp_id: int, status: str) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        "UPDATE landing_pages SET last_resolved_at = %s, resolution_status = %s, last_seen = %s WHERE id = %s",
        (now, status, now, lp_id),
    )
    conn.commit()


def crawl_landing_page(conn, lp: dict) -> str:
    """Fetch a landing page and snapshot its title if the content changed.
    Returns the resolution_status ('ok' | 'fetch_failed' | 'no_content')."""
    url = lp["url_canonical"]
    try:
        resp = _fetch(url)
    except Exception as exc:
        logger.warning("  fetch failed %s: %s", url, exc)
        _mark(conn, lp["id"], "fetch_failed")
        return "fetch_failed"
    if resp.status_code >= 400:
        logger.warning("  fetch failed %s: HTTP %s", url, resp.status_code)
        _mark(conn, lp["id"], "fetch_failed")
        return "fetch_failed"

    html = resp.text or ""
    title, description = extract_title_and_description(html)
    genres, genres_source = extract_genres(html)
    normalized = _normalize_text(html)
    if not normalized and not title:
        _mark(conn, lp["id"], "no_content")
        return "no_content"

    content_hash = hashlib.sha256((normalized or title or "").encode("utf-8")).hexdigest()
    latest = _latest_snapshot(conn, lp["id"])
    if latest is None or latest["content_hash"] != content_hash:
        raw = {
            "final_url": str(resp.url), "http_status": resp.status_code,
            "title": title, "description": description, "text_len": len(normalized),
            "genres": genres, "genres_source": genres_source,
        }
        conn.execute(
            """INSERT INTO page_snapshots
                   (landing_page_id, content_hash, headline, description, genres,
                    genres_source, raw)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (lp["id"], content_hash, title, description, genres, genres_source, Jsonb(raw)),
        )
        logger.info("  snapshot+ %s (%s, %d genres)", url, title or "no title", len(genres))
    elif (title, description, genres, genres_source) != (
        latest["headline"], latest["description"], latest["genres"],
        latest["genres_source"],
    ):
        # Same page text, but the title/description read now differ (the meta
        # tags changed): refresh the
        # latest snapshot in place rather than recording a new page version.
        conn.execute(
            """UPDATE page_snapshots
               SET headline = %s, description = %s, genres = %s, genres_source = %s
               WHERE id = %s""",
            (title, description, genres, genres_source, latest["id"]),
        )

    _mark(conn, lp["id"], "ok")
    return "ok"


def crawl_pending(conn, on_progress=None) -> dict[str, int]:
    """Crawl the landing pages that are new or due a re-check.
    `on_progress(done, total)` is called after each page."""
    pages = conn.execute(
        """SELECT id, url_canonical FROM landing_pages
           WHERE last_resolved_at IS NULL
              OR last_resolved_at < now() - make_interval(hours => %s)
           ORDER BY last_resolved_at ASC NULLS FIRST LIMIT %s""",
        (STALE_HOURS, CRAWL_LIMIT),
    ).fetchall()
    if pages:
        logger.info("landing pages: fetching titles for %d page(s)", len(pages))

    counts: dict[str, int] = {}
    for i, lp in enumerate(pages):
        status = crawl_landing_page(conn, lp)
        counts[status] = counts.get(status, 0) + 1
        if on_progress:
            on_progress(i + 1, len(pages))
        if i < len(pages) - 1:
            time.sleep(REQUEST_DELAY)
    return counts


def run_resolve(on_progress=None) -> dict[str, int]:
    """Fetch titles for landing pages that are new or due a re-check. Called by the
    scheduler after every collection. Returns counts per resolution status."""
    conn = db.connect()
    try:
        return crawl_pending(conn, on_progress)
    finally:
        conn.close()
