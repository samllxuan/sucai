"""Collect anonymous TikTok and Google Short Videos drama performance data.

The collector is deliberately serial. Search pages provide candidate video IDs;
each candidate is then opened once to read its public counters. Requests to the
same domain are spaced apart, and HTTP 429/5xx responses use a bounded exponential
backoff so one research job cannot create a burst against an external service.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable
from urllib.parse import unquote, urlsplit

from .drama_parsers import (
    VideoRecord,
    google_page_is_blocked,
    google_page_is_explicitly_empty,
    google_short_video_url,
    parse_tiktok_video,
    parse_youtube_comment_count,
    parse_youtube_video,
    tiktok_discover_url,
    tiktok_page_is_blocked,
    tiktok_page_is_explicitly_empty,
    tiktok_search_url,
    tiktok_video_links,
    youtube_comment_continuation,
    youtube_video_ids,
)

logger = logging.getLogger(__name__)

MAX_RESULTS_PER_PLATFORM = 40
RESEARCH_TIME_BUDGET_SECONDS = 10 * 60
MIN_REQUEST_INTERVAL_SECONDS = {
    "tiktok": 2.5,
    "google": 2.0,
    "youtube": 1.25,
}
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 2


class PageFetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(slots=True)
class PageSnapshot:
    html: str
    final_url: str
    status: int


@dataclass(slots=True)
class Candidate:
    video_id: str
    aliases: set[str]
    discovery_urls: list[str]
    page_url: str


class DomainRateLimiter:
    """Enforce one request at a time and a minimum gap for each external domain."""

    def __init__(self, intervals: dict[str, float] | None = None):
        self.intervals = intervals or MIN_REQUEST_INTERVAL_SECONDS
        self._next_request: dict[str, float] = {}

    @staticmethod
    def domain(url: str) -> str:
        host = (urlsplit(url).hostname or "").casefold()
        for domain in ("tiktok", "google", "youtube"):
            if host == domain or host.endswith(f".{domain}.com"):
                return domain
        return host

    async def wait(self, url: str) -> None:
        domain = self.domain(url)
        now = time.monotonic()
        ready_at = self._next_request.get(domain, now)
        if ready_at > now:
            await asyncio.sleep(ready_at - now)
        self._next_request[domain] = max(ready_at, now) + self.intervals.get(domain, 1.0)

    def block(self, url: str, seconds: float) -> None:
        domain = self.domain(url)
        until = time.monotonic() + max(0.0, seconds)
        self._next_request[domain] = max(self._next_request.get(domain, 0.0), until)


class BrowserPageReader:
    """A single Chromium context used for all public pages in one research job."""

    def __init__(self, proxy: str | None = None):
        self.proxy = proxy
        self.runtime = None
        self.browser = None
        self.context = None
        self.page = None
        self.limiter = DomainRateLimiter()

    def _launch_options(self) -> dict:
        options = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-quic",
            ],
        }
        if self.proxy:
            parsed = urlsplit(self.proxy)
            host = parsed.hostname or ""
            if ":" in host:
                host = f"[{host}]"
            options["proxy"] = {
                "server": parsed.scheme
                + "://"
                + host
                + (f":{parsed.port}" if parsed.port else ""),
            }
            if parsed.username:
                options["proxy"]["username"] = unquote(parsed.username)
            if parsed.password:
                options["proxy"]["password"] = unquote(parsed.password)
        return options

    async def _open(self) -> None:
        if self.page is not None:
            return
        from playwright.async_api import async_playwright

        self.runtime = await async_playwright().start()
        options = self._launch_options()
        try:
            # The full new-headless Chromium behaves more like a normal browser on
            # TikTok than Playwright's headless shell.
            self.browser = await self.runtime.chromium.launch(
                channel="chromium",
                **options,
            )
        except Exception:
            self.browser = await self.runtime.chromium.launch(**options)
        self.context = await self.browser.new_context(
            locale="en-US",
            viewport={"width": 1440, "height": 900},
        )

        async def skip_heavy_asset(route) -> None:
            if route.request.resource_type in {"image", "media", "font"}:
                await route.abort()
            else:
                await route.continue_()

        await self.context.route("**/*", skip_heavy_asset)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(30_000)

    async def close(self) -> None:
        try:
            if self.browser:
                await self.browser.close()
        finally:
            if self.runtime:
                await self.runtime.stop()
            self.page = self.context = self.browser = self.runtime = None

    @staticmethod
    def _retry_after(response) -> float | None:
        try:
            value = response.headers.get("retry-after")
            return float(value) if value and value.replace(".", "", 1).isdigit() else None
        except Exception:
            return None

    async def fetch(
        self,
        url: str,
        *,
        settle_ms: int = 800,
        scrolls: int = 0,
    ) -> PageSnapshot:
        await self._open()
        last_status: int | None = None
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            await self.limiter.wait(url)
            response = None
            try:
                response = await self.page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=45_000,
                )
                last_status = response.status if response else 200
                retry_after = self._retry_after(response) if response else None
                if last_status in RETRYABLE_STATUSES:
                    delay = retry_after or (5.0 * (2**attempt))
                    self.limiter.block(url, delay)
                    last_error = PageFetchError(
                        f"external request returned HTTP {last_status}",
                        status=last_status,
                    )
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(delay)
                        continue
                    raise last_error
                if last_status >= 400:
                    raise PageFetchError(
                        f"external request returned HTTP {last_status}",
                        status=last_status,
                    )
                if settle_ms:
                    await self.page.wait_for_timeout(settle_ms)
                for _ in range(max(0, scrolls)):
                    await self.page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )
                    await self.page.wait_for_timeout(550)
                return PageSnapshot(
                    html=await self.page.content(),
                    final_url=self.page.url,
                    status=last_status,
                )
            except PageFetchError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < MAX_RETRIES:
                    delay = 2.0 * (attempt + 1)
                    self.limiter.block(url, delay)
                    await asyncio.sleep(delay)
                    continue
        raise PageFetchError(
            f"external page failed after retries: {type(last_error).__name__}",
            status=last_status,
        )

    async def youtube_comment_count(self, html: str, region: str) -> int | None:
        continuation = youtube_comment_continuation(html)
        if not continuation:
            return None
        await self._open()
        url = (
            "https://www.youtube.com/youtubei/v1/next?key="
            f"{continuation.api_key}&prettyPrint=false"
        )
        await self.limiter.wait(url)
        payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": continuation.client_version,
                    "hl": "en",
                    "gl": region,
                }
            },
            "continuation": continuation.token,
        }
        try:
            result = await self.page.evaluate(
                """async ({url, payload}) => {
                    const response = await fetch(url, {
                        method: "POST",
                        credentials: "include",
                        headers: {"content-type": "application/json"},
                        body: JSON.stringify(payload),
                    });
                    return {status: response.status, text: await response.text()};
                }""",
                {"url": url, "payload": payload},
            )
        except Exception:
            return None
        status = result.get("status") if isinstance(result, dict) else None
        if status == 429:
            self.limiter.block(url, 10.0)
            return None
        if status != 200:
            return None
        try:
            body = json.loads(result.get("text", ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return parse_youtube_comment_count(body)


def _add_candidate(
    candidates: dict[str, Candidate],
    video_id: str,
    alias: str,
    discovery_url: str,
    page_url: str,
) -> None:
    item = candidates.setdefault(
        video_id,
        Candidate(
            video_id=video_id,
            aliases=set(),
            discovery_urls=[],
            page_url=page_url,
        ),
    )
    item.aliases.add(alias)
    if discovery_url not in item.discovery_urls:
        item.discovery_urls.append(discovery_url)


def _phase(callback: Callable[[str], None] | None, name: str) -> None:
    if callback:
        callback(name)


def _warning(warnings: list[str], message: str) -> None:
    if message not in warnings and len(warnings) < 20:
        warnings.append(message)


def _record_source(
    platform: str,
    record: VideoRecord,
    candidate: Candidate,
    url: str,
) -> dict:
    return {
        "platform": platform,
        "content_platform": "youtube" if platform == "google" else "tiktok",
        "video_id": record.video_id,
        "url": url,
        "title": record.title,
        "creator": record.creator,
        "published_at": record.published_at,
        "views": record.views,
        "likes": record.likes,
        "comments": record.comments,
        "shares": record.shares,
        "matched_aliases": sorted(candidate.aliases, key=str.casefold),
    }


def _summary_for(sources: list[dict]) -> dict:
    views = [item["views"] for item in sources if item["views"] is not None]
    likes = [item["likes"] for item in sources if item["likes"] is not None]
    comments = [item["comments"] for item in sources if item["comments"] is not None]
    shares = [item["shares"] for item in sources if item["shares"] is not None]
    dates = [item["published_at"] for item in sources if item["published_at"]]
    creators = {item["creator"] for item in sources if item["creator"]}
    return {
        "video_count": len(sources),
        "views": sum(views) if views else None,
        "likes": sum(likes) if likes else None,
        "comments": sum(comments) if comments else None,
        "shares": sum(shares) if shares else None,
        "metrics_available": {
            "views": len(views),
            "likes": len(likes),
            "comments": len(comments),
            "shares": len(shares),
        },
        "creator_count": len(creators),
        "earliest_published_at": min(dates) if dates else None,
        "latest_published_at": max(dates) if dates else None,
    }


def _combined_summary(sources: list[dict]) -> dict:
    summary = _summary_for(sources)
    summary["platforms"] = sorted({item["platform"] for item in sources})
    return summary


def _creators(sources: list[dict], limit: int = 20) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for item in sources:
        if item["creator"]:
            grouped.setdefault(item["creator"], []).append(item)
    rows = []
    for creator, items in grouped.items():
        summary = _summary_for(items)
        rows.append(
            {
                "creator": creator,
                "video_count": summary["video_count"],
                "views": summary["views"],
                "likes": summary["likes"],
                "comments": summary["comments"],
            }
        )
    rows.sort(
        key=lambda row: (
            -(row["views"] if row["views"] is not None else -1),
            -row["video_count"],
            row["creator"].casefold(),
        )
    )
    return rows[:limit]


def _sort_sources(sources: list[dict]) -> list[dict]:
    return sorted(
        sources,
        key=lambda item: (
            -(item["views"] if item["views"] is not None else -1),
            -(item["likes"] if item["likes"] is not None else -1),
            item["video_id"],
        ),
    )


def _platform_status(
    sources: list[dict],
    errors: int,
    empty: int,
    truncated: bool,
) -> str:
    if sources:
        return "partial" if errors or truncated else "complete"
    if errors:
        return "failed"
    if empty:
        return "empty"
    return "failed"


async def collect_research(
    title: str,
    aliases: list[str],
    region: str,
    *,
    reader: BrowserPageReader | None = None,
    on_phase: Callable[[str], None] | None = None,
    time_budget: float = RESEARCH_TIME_BUDGET_SECONDS,
) -> dict:
    """Run one research job and return a JSON-serializable result."""
    aliases = [title, *aliases]
    warnings: list[str] = []
    searches: list[dict] = []
    captures: list[dict] = []
    started = time.monotonic()
    deadline = started + max(30.0, float(time_budget))
    owned_reader = reader is None
    page_reader = reader or BrowserPageReader(os.environ.get("TIKTOK_PROXY") or None)
    outcomes = {
        "tiktok": {"errors": 0, "empty": 0, "truncated": False},
        "google": {"errors": 0, "empty": 0, "truncated": False},
    }

    def elapsed_expired() -> bool:
        if time.monotonic() < deadline:
            return False
        _warning(warnings, "采集达到 10 分钟时间上限，结果可能不完整")
        return True

    try:
        # TikTok search, with Discover as a fallback for aliases whose search page
        # contains no video cards. This stays read-only and anonymous.
        _phase(on_phase, "tiktok_discovery")
        tiktok_candidates: dict[str, Candidate] = {}
        for alias in aliases:
            if elapsed_expired():
                outcomes["tiktok"]["errors"] += 1
                break
            query_url = tiktok_search_url(alias, region)
            try:
                snapshot = await page_reader.fetch(query_url, settle_ms=1_000, scrolls=2)
            except PageFetchError:
                outcomes["tiktok"]["errors"] += 1
                _warning(warnings, "TikTok 部分搜索页读取失败")
                searches.append(
                    {
                        "platform": "tiktok",
                        "alias": alias,
                        "url": query_url,
                        "status": "failed",
                        "candidate_count": 0,
                    }
                )
                continue
            if tiktok_page_is_blocked(snapshot.html, snapshot.final_url):
                outcomes["tiktok"]["errors"] += 1
                _warning(warnings, "TikTok 返回验证页，未把该页当作空结果")
                searches.append(
                    {
                        "platform": "tiktok",
                        "alias": alias,
                        "url": query_url,
                        "status": "blocked",
                        "candidate_count": 0,
                    }
                )
                continue
            links = tiktok_video_links(snapshot.html)
            source_url = query_url
            status = "ok"
            if not links:
                discover_url = tiktok_discover_url(alias)
                try:
                    discover = await page_reader.fetch(
                        discover_url,
                        settle_ms=1_000,
                        scrolls=2,
                    )
                    links = tiktok_video_links(discover.html)
                    source_url = discover_url
                    status = "ok" if links else status
                except PageFetchError:
                    outcomes["tiktok"]["errors"] += 1
                    _warning(warnings, "TikTok 部分话题页读取失败")
            if links:
                for video_id, video_url in links.items():
                    _add_candidate(
                        tiktok_candidates,
                        video_id,
                        alias,
                        source_url,
                        video_url,
                    )
            elif tiktok_page_is_explicitly_empty(snapshot.html):
                outcomes["tiktok"]["empty"] += 1
                status = "empty"
            else:
                outcomes["tiktok"]["errors"] += 1
                status = "failed"
                _warning(warnings, "TikTok 搜索页无可解析视频，且不能确认是空结果")
            searches.append(
                {
                    "platform": "tiktok",
                    "alias": alias,
                    "url": source_url,
                    "status": status,
                    "candidate_count": len(links),
                }
            )

        tiktok_selected = list(tiktok_candidates.values())[:MAX_RESULTS_PER_PLATFORM]
        outcomes["tiktok"]["truncated"] = (
            len(tiktok_candidates) > MAX_RESULTS_PER_PLATFORM
        )
        if outcomes["tiktok"]["truncated"]:
            _warning(warnings, "TikTok 候选视频超过 40 条，仅读取前 40 条样本")
        _phase(on_phase, "tiktok_videos")
        for candidate in tiktok_selected:
            if elapsed_expired():
                outcomes["tiktok"]["errors"] += 1
                break
            url = candidate.page_url
            try:
                snapshot = await page_reader.fetch(url, settle_ms=1_200)
                record = parse_tiktok_video(snapshot.html, candidate.video_id)
            except Exception:
                record = None
            if not record:
                outcomes["tiktok"]["errors"] += 1
                _warning(warnings, "部分 TikTok 视频页未返回可验证的公开指标")
                continue
            captures.append(_record_source("tiktok", record, candidate, url))

        # Google Short Videos return YouTube content. Each YouTube page supplies
        # exact view counts, and the public comments continuation supplies comments
        # when the page exposes one.
        _phase(on_phase, "google_discovery")
        google_candidates: dict[str, Candidate] = {}
        for alias in aliases:
            if elapsed_expired():
                outcomes["google"]["errors"] += 1
                break
            query_url = google_short_video_url(alias, region)
            try:
                snapshot = await page_reader.fetch(query_url, settle_ms=1_200, scrolls=3)
            except PageFetchError:
                outcomes["google"]["errors"] += 1
                _warning(warnings, "Google 部分短视频搜索页读取失败")
                searches.append(
                    {
                        "platform": "google",
                        "alias": alias,
                        "url": query_url,
                        "status": "failed",
                        "candidate_count": 0,
                    }
                )
                continue
            if google_page_is_blocked(snapshot.html, snapshot.final_url):
                outcomes["google"]["errors"] += 1
                _warning(warnings, "Google 返回流量验证页，未把该页当作空结果")
                searches.append(
                    {
                        "platform": "google",
                        "alias": alias,
                        "url": query_url,
                        "status": "blocked",
                        "candidate_count": 0,
                    }
                )
                continue
            video_ids = youtube_video_ids(snapshot.html)
            for video_id in video_ids:
                _add_candidate(
                    google_candidates,
                    video_id,
                    alias,
                    query_url,
                    f"https://www.youtube.com/watch?v={video_id}",
                )
            if video_ids:
                status = "ok"
            elif google_page_is_explicitly_empty(snapshot.html):
                outcomes["google"]["empty"] += 1
                status = "empty"
            else:
                outcomes["google"]["errors"] += 1
                status = "failed"
                _warning(warnings, "Google 搜索页无可解析 YouTube 视频，且不能确认是空结果")
            searches.append(
                {
                    "platform": "google",
                    "alias": alias,
                    "url": query_url,
                    "status": status,
                    "candidate_count": len(video_ids),
                }
            )

        google_selected = list(google_candidates.values())[:MAX_RESULTS_PER_PLATFORM]
        outcomes["google"]["truncated"] = (
            len(google_candidates) > MAX_RESULTS_PER_PLATFORM
        )
        if outcomes["google"]["truncated"]:
            _warning(warnings, "Google 候选视频超过 40 条，仅读取前 40 条样本")
        _phase(on_phase, "youtube_videos")
        for candidate in google_selected:
            if elapsed_expired():
                outcomes["google"]["errors"] += 1
                break
            url = candidate.page_url
            try:
                snapshot = await page_reader.fetch(url, settle_ms=1_000)
                record = parse_youtube_video(snapshot.html)
                if record:
                    try:
                        comments = await page_reader.youtube_comment_count(
                            snapshot.html,
                            region,
                        )
                    except Exception:
                        comments = None
                    if comments is not None:
                        record.comments = comments
            except Exception:
                record = None
            if not record:
                outcomes["google"]["errors"] += 1
                _warning(warnings, "部分 YouTube 视频页未返回可验证的公开指标")
                continue
            captures.append(_record_source("google", record, candidate, url))
    finally:
        if owned_reader:
            await page_reader.close()

    _phase(on_phase, "summarizing")
    captures = _sort_sources(captures)
    tiktok_sources = [item for item in captures if item["platform"] == "tiktok"]
    google_sources = [item for item in captures if item["platform"] == "google"]
    tiktok_summary = _summary_for(tiktok_sources)
    google_summary = _summary_for(google_sources)
    tiktok_summary["status"] = _platform_status(
        tiktok_sources,
        outcomes["tiktok"]["errors"],
        outcomes["tiktok"]["empty"],
        outcomes["tiktok"]["truncated"],
    )
    google_summary["status"] = _platform_status(
        google_sources,
        outcomes["google"]["errors"],
        outcomes["google"]["empty"],
        outcomes["google"]["truncated"],
    )
    google_summary["content_platform"] = "youtube"
    google_summary["discovery"] = "google_short_videos"

    if captures and tiktok_summary["status"] == "complete" and google_summary["status"] == "complete":
        status = "completed"
    elif captures:
        status = "partial"
    elif tiktok_summary["status"] == "empty" and google_summary["status"] == "empty":
        status = "no_results"
    else:
        status = "failed"

    return {
        "status": status,
        "summary": {
            "tiktok": tiktok_summary,
            "google": google_summary,
            "combined": _combined_summary(captures),
        },
        "creators": {
            "tiktok": _creators(tiktok_sources),
            "google": _creators(google_sources),
        },
        "sources": captures,
        "collection": {
            "region": region,
            "aliases": aliases,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "searches": searches,
            "warnings": warnings,
            "limits": {
                "max_videos_per_platform": MAX_RESULTS_PER_PLATFORM,
                "time_budget_seconds": int(deadline - started),
                "serial_per_domain": True,
                "minimum_interval_seconds": MIN_REQUEST_INTERVAL_SECONDS,
                "max_retries": MAX_RETRIES,
            },
        },
    }
