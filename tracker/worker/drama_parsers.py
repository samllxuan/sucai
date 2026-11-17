"""Parsers for anonymous TikTok, YouTube search and YouTube video pages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any, Iterator
from urllib.parse import quote


@dataclass(slots=True)
class VideoRecord:
    video_id: str
    title: str
    creator: str | None
    published_at: str | None
    views: int | None
    likes: int | None
    comments: int | None
    shares: int | None


@dataclass(slots=True)
class CommentContinuation:
    token: str
    api_key: str
    client_version: str


class _ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._inside_script = False
        self._parts: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() == "script":
            self._inside_script = True
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._inside_script:
            self.scripts.append("".join(self._parts).strip())
            self._inside_script = False
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._inside_script:
            self._parts.append(data)


def _assigned_json(html: str, name: str) -> dict | None:
    match = re.search(rf"(?:var\s+)?{re.escape(name)}\s*=\s*", html)
    if not match:
        return None
    start = html.find("{", match.end())
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(html[start:])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _json_values(html: str) -> list[Any]:
    collector = _ScriptCollector()
    try:
        collector.feed(html)
    except Exception:
        pass
    values: list[Any] = []
    for raw in collector.scripts:
        text = raw.strip()
        if not text or text[0] not in "[{":
            continue
        try:
            values.append(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    for name in ("ytInitialPlayerResponse", "ytInitialData"):
        value = _assigned_json(html, name)
        if value is not None:
            values.append(value)
    return values


def walk_json(value: Any) -> Iterator[Any]:
    pending = [value]
    while pending:
        current = pending.pop()
        yield current
        if isinstance(current, dict):
            pending.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            pending.extend(reversed(current))


def normalized_text(value: str | None) -> str:
    words = re.findall(r"[^\W_]+", (value or "").casefold(), flags=re.UNICODE)
    return " ".join(words)


def matches_aliases(values: list[str | None], aliases: list[str]) -> bool:
    haystacks = [f" {normalized_text(value)} " for value in values if value]
    for alias in aliases:
        wanted = normalized_text(alias)
        if wanted and any(f" {wanted} " in text for text in haystacks):
            return True
    return False


def discover_slug(title: str) -> str:
    slug = re.sub(r"[^\w]+", "-", title.casefold(), flags=re.UNICODE)
    return slug.replace("_", "-").strip("-")


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float) and value >= 0:
        return int(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kKmMbB万億亿])?", text)
        if not match:
            return None
        number = float(match.group(1))
        multiplier = {
            "k": 1_000,
            "m": 1_000_000,
            "b": 1_000_000_000,
            "万": 10_000,
            "亿": 100_000_000,
            "億": 100_000_000,
        }.get((match.group(2) or "").casefold(), 1)
        return max(0, int(number * multiplier))
    return None


def _iso_utc(value: Any) -> str | None:
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    if re.fullmatch(r"\d{9,}", value.strip()):
        return _iso_utc(int(value.strip()))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _decode_links(html: str) -> str:
    return (
        unescape(html)
        .replace("\\u002F", "/")
        .replace("\\u003A", ":")
        .replace("\\/", "/")
        .replace("%2F", "/")
        .replace("%3A", ":")
    )


def tiktok_video_links(html: str) -> dict[str, str]:
    text = _decode_links(html)
    pattern = re.compile(
        r"(?:(?:https?:)?//(?:www\.)?tiktok\.com)?"
        r"(/@[^/\"'<>]+/video/(\d+)|/video/(\d+))"
    )
    links: dict[str, str] = {}
    for match in pattern.finditer(text):
        video_id = match.group(2) or match.group(3)
        links.setdefault(video_id, f"https://www.tiktok.com{match.group(1)}")
    for video_id in re.findall(r'"videoId"\s*:\s*"(\d{6,})"', text):
        links.setdefault(video_id, f"https://www.tiktok.com/video/{video_id}")
    return links


def youtube_video_ids(html: str) -> list[str]:
    text = _decode_links(html)
    patterns = (
        r"(?:youtube\.com/(?:watch\?(?:[^\"'<>]*?&)?v=|shorts/)|youtu\.be/)"
        r"([A-Za-z0-9_-]{11})",
        r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"',
        r"watch%3Fv%3D([A-Za-z0-9_-]{11})",
    )
    ids: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for video_id in re.findall(pattern, text):
            if video_id not in seen:
                ids.append(video_id)
                seen.add(video_id)
    return ids


def youtube_search_url(query: str, region: str) -> str:
    return (
        "https://www.youtube.com/results?search_query="
        f"{quote('\"' + query + '\"')}&hl=en"
    )


def tiktok_search_url(query: str, region: str) -> str:
    return f"https://www.tiktok.com/search?q={quote(query)}"


def tiktok_discover_url(query: str) -> str:
    return f"https://www.tiktok.com/discover/{quote(discover_slug(query), safe='-')}"


def youtube_page_is_explicitly_empty(html: str) -> bool:
    text = unescape(html).casefold()
    return any(
        marker in text
        for marker in (
            "did not match any documents",
            "no results found",
            "your search did not match",
            "noresultsrenderer",
        )
    )


def youtube_page_is_blocked(html: str, final_url: str = "") -> bool:
    text = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1\s*>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = unescape(re.sub(r"<[^>]+>", " ", text)).casefold()
    return (
        "/sorry/" in final_url.casefold()
        or "our systems have detected unusual traffic" in text
        or "unusual traffic from your computer network" in text
        or "sign in to confirm you're not a bot" in text
        or "verify you're not a robot" in text
    )


def tiktok_page_is_explicitly_empty(html: str) -> bool:
    text = unescape(html).casefold()
    return any(
        marker in text
        for marker in (
            "no results found",
            "couldn't find any results",
            "could not find any results",
            "no videos found",
        )
    )


def tiktok_page_is_blocked(html: str, final_url: str = "") -> bool:
    text = unescape(html).casefold()
    return (
        "/captcha" in final_url.casefold()
        or "verify to continue" in text
        or "please verify you are a human" in text
    )


def tiktok_page_has_server_error(html: str) -> bool:
    text = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1\s*>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = unescape(re.sub(r"<[^>]+>", " ", text)).casefold()
    return (
        "something went wrong" in text
        or "something wrong with the server" in text
    )


def parse_tiktok_video(html: str, video_id: str | None = None) -> VideoRecord | None:
    for value in _json_values(html):
        for node in walk_json(value):
            if not isinstance(node, dict) or not isinstance(node.get("stats"), dict):
                continue
            current_id = str(node.get("id") or node.get("videoId") or "")
            if video_id and current_id and current_id != video_id:
                continue
            stats = node["stats"]
            author = node.get("author") if isinstance(node.get("author"), dict) else {}
            creator = (
                author.get("uniqueId")
                or author.get("nickname")
                or author.get("id")
            )
            record_id = current_id or video_id or ""
            if not record_id:
                continue
            return VideoRecord(
                video_id=record_id,
                title=str(node.get("desc") or node.get("title") or ""),
                creator=str(creator) if creator else None,
                published_at=_iso_utc(node.get("createTime")),
                views=_int_value(stats.get("playCount")),
                likes=_int_value(stats.get("diggCount")),
                comments=_int_value(stats.get("commentCount")),
                shares=_int_value(stats.get("shareCount")),
            )

    text = _decode_links(html)

    def stat(key: str) -> int | None:
        match = re.search(rf'"{re.escape(key)}"\s*:\s*"?(\d+)"?', text)
        return int(match.group(1)) if match else None

    if video_id is None:
        match = re.search(r'"id"\s*:\s*"(\d{6,})"', text)
        video_id = match.group(1) if match else None
    title_match = re.search(r'"desc"\s*:\s*"((?:\\.|[^"\\])*)"', text)
    author_match = re.search(r'"uniqueId"\s*:\s*"([^"]+)"', text)
    published_match = re.search(r'"createTime"\s*:\s*"?(\d{9,})"?', text)
    if not video_id or not any(stat(key) is not None for key in (
        "playCount", "diggCount", "commentCount", "shareCount"
    )):
        return None
    title = ""
    if title_match:
        try:
            title = json.loads(f'"{title_match.group(1)}"')
        except (TypeError, ValueError, json.JSONDecodeError):
            title = title_match.group(1)
    return VideoRecord(
        video_id=video_id,
        title=title,
        creator=author_match.group(1) if author_match else None,
        published_at=_iso_utc(int(published_match.group(1))) if published_match else None,
        views=stat("playCount"),
        likes=stat("diggCount"),
        comments=stat("commentCount"),
        shares=stat("shareCount"),
    )


def _youtube_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    simple = value.get("simpleText")
    if isinstance(simple, str):
        return simple.strip()
    runs = value.get("runs")
    if isinstance(runs, list):
        return "".join(
            str(run.get("text", ""))
            for run in runs
            if isinstance(run, dict)
        ).strip()
    return ""


def _youtube_date(value: Any) -> str | None:
    parsed = _iso_utc(value)
    if parsed:
        return parsed
    if not isinstance(value, str):
        return None
    text = re.sub(
        r"^(?:premiered|streamed live on|started streaming on)\s+",
        "",
        value.strip(),
        flags=re.IGNORECASE,
    )
    for pattern in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y"):
        try:
            date = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return date.replace(tzinfo=timezone.utc).isoformat()
    return None


def _youtube_video_id(html: str, *values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
            return value
    for pattern in (
        r'<link[^>]+href="[^"]*(?:watch\?v=|shorts/)([A-Za-z0-9_-]{11})',
        r'<meta[^>]+content="[^"]*(?:watch\?v=|shorts/)([A-Za-z0-9_-]{11})',
        r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"',
    ):
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _youtube_initial_data_record(
    html: str,
    video_id: str | None,
) -> VideoRecord | None:
    data = _assigned_json(html, "ytInitialData")
    if not data:
        return None

    primary: dict = {}
    secondary: dict = {}
    for node in walk_json(data):
        if not isinstance(node, dict):
            continue
        candidate = node.get("videoPrimaryInfoRenderer")
        if not primary and isinstance(candidate, dict):
            primary = candidate
        candidate = node.get("videoSecondaryInfoRenderer")
        if not secondary and isinstance(candidate, dict):
            secondary = candidate
        if primary and secondary:
            break
    if not primary:
        return None

    view_renderer = primary.get("viewCount")
    if isinstance(view_renderer, dict):
        view_renderer = view_renderer.get("videoViewCountRenderer")
    if not isinstance(view_renderer, dict):
        view_renderer = {}
    views = _int_value(view_renderer.get("originalViewCount"))
    if views is None:
        views = _int_value(_youtube_text(view_renderer.get("viewCount")))

    owner = secondary.get("owner")
    if isinstance(owner, dict):
        owner = owner.get("videoOwnerRenderer")
    if not isinstance(owner, dict):
        owner = {}
    creator = _youtube_text(owner.get("title")) or None

    role = video_id or _youtube_video_id(
        html,
        owner.get("channelId"),
    )
    title = _youtube_text(primary.get("title"))
    if not role or not title:
        return None
    return VideoRecord(
        video_id=role,
        title=title,
        creator=creator,
        published_at=_youtube_date(_youtube_text(primary.get("dateText"))),
        views=views,
        likes=_youtube_like_count(html, {}),
        comments=None,
        shares=None,
    )


def parse_youtube_video(
    html: str,
    video_id: str | None = None,
) -> VideoRecord | None:
    player = _assigned_json(html, "ytInitialPlayerResponse")
    if not isinstance(player, dict):
        player = {}
    details = player.get("videoDetails")
    if not isinstance(details, dict):
        details = {}
    fallback = _youtube_initial_data_record(html, video_id)

    record_id = _youtube_video_id(
        html,
        details.get("videoId"),
        video_id,
        fallback.video_id if fallback else None,
    )
    if not record_id:
        return None
    microformat = player.get("microformat")
    if not isinstance(microformat, dict):
        microformat = {}
    renderer = microformat.get("playerMicroformatRenderer")
    if not isinstance(renderer, dict):
        renderer = {}
    title = str(details.get("title") or (fallback.title if fallback else ""))
    if not title:
        return None
    like_count = _youtube_like_count(html, renderer)
    if like_count is None and fallback:
        like_count = fallback.likes
    return VideoRecord(
        video_id=record_id,
        title=title,
        creator=str(
            details.get("author")
            or details.get("channelId")
            or (fallback.creator if fallback else "")
        ) or None,
        published_at=(
            _iso_utc(renderer.get("publishDate"))
            or (fallback.published_at if fallback else None)
        ),
        views=(
            _int_value(details.get("viewCount"))
            if details.get("viewCount") is not None
            else (fallback.views if fallback else None)
        ),
        likes=like_count,
        comments=None,
        shares=None,
    )


def _youtube_like_count(html: str, renderer: dict) -> int | None:
    like_count = _int_value(renderer.get("likeCount"))
    if like_count is not None:
        return like_count

    for value in _json_values(html):
        for node in walk_json(value):
            if isinstance(node, dict):
                for key in ("likeCount", "likeCountIfNotFullyActive"):
                    parsed = _int_value(node.get(key))
                    if parsed is not None:
                        return parsed
                label = node.get("label")
                if isinstance(label, str):
                    match = re.search(
                        r"like this video along with\s+([\d,.]+(?:\s*[KMB])?)",
                        label,
                        flags=re.IGNORECASE,
                    )
                    if match:
                        return _int_value(match.group(1))
            elif isinstance(node, str):
                match = re.search(
                    r"like this video along with\s+([\d,.]+(?:\s*[KMB])?)",
                    node,
                    flags=re.IGNORECASE,
                )
                if match:
                    return _int_value(match.group(1))
    return None


def youtube_comment_continuation(html: str) -> CommentContinuation | None:
    api_key_match = re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"', html)
    version_match = re.search(r'"INNERTUBE_CLIENT_VERSION"\s*:\s*"([^"]+)"', html)
    data = _assigned_json(html, "ytInitialData")
    if not (api_key_match and version_match and data):
        return None

    token = None
    for node in walk_json(data):
        if not isinstance(node, dict) or node.get("targetId") != "comments-section":
            continue
        for child in walk_json(node):
            if not isinstance(child, dict):
                continue
            command = child.get("continuationCommand")
            if isinstance(command, dict) and isinstance(command.get("token"), str):
                token = command["token"]
                break
        if token:
            break
    if not token:
        return None
    return CommentContinuation(
        token=token,
        api_key=api_key_match.group(1),
        client_version=version_match.group(1),
    )


def parse_youtube_comment_count(payload: Any) -> int | None:
    for node in walk_json(payload):
        if not isinstance(node, dict):
            continue
        header = node.get("commentsHeaderRenderer")
        if not isinstance(header, dict):
            continue
        count_text = header.get("countText")
        values: list[str] = []
        if isinstance(count_text, dict):
            simple = count_text.get("simpleText")
            if isinstance(simple, str):
                values.append(simple)
            runs = count_text.get("runs")
            if isinstance(runs, list):
                values.extend(
                    str(run.get("text", ""))
                    for run in runs
                    if isinstance(run, dict)
                )
        match = re.match(r"^\s*([\d,]+)", "".join(values))
        if match:
            return int(match.group(1).replace(",", ""))
    return None
