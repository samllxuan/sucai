"""Read TikTok's public Ad Library through its own web page and web requests.

No official API key is used. The collector reuses the public page's browser
session for the search endpoint, then opens each public detail page to capture
its item response. Raw HTML, cookies and request headers are never persisted.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, time, timedelta, timezone
from time import monotonic
from urllib.parse import urlencode, urlsplit, unquote
from zoneinfo import ZoneInfo

from .normalize import from_tiktok_record

SEARCH_URL = "https://library.tiktok.com/api/v1/search"
DETAIL_URL = "https://library.tiktok.com/api/v1/items/{ad_id}/details?lang=en-US"
DETAIL_PAGE_URL = "https://library.tiktok.com/ads/detail?ad_id={ad_id}"
SUPPORT_REGIONS_URL = "https://library.tiktok.com/api/v1/support-regions"
LIBRARY_URL = "https://library.tiktok.com/ads"
PAGE_SIZE = 12
MAX_PAGE_REQUESTS = 40
MAX_DETAIL_RETRIES = 3
MAX_SEARCH_RETRIES = 2
MAX_OPEN_RETRIES = 2
SEARCH_TIME_BUDGET_SECONDS = 150.0

# The countries exposed by the public TikTok Ad Library at the time of writing.
TIKTOK_COUNTRIES = tuple(
    "AT BE BG BR CH CY CZ DE DK EE ES FI FR GB GR HR HU IE IS IT LI LT LU LV "
    "MT NL NO PL PT RO SE SI SK TR".split()
)


class TikTokSearchError(ValueError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def _unwrap_payload(payload: dict, *, kind: str) -> dict:
    """Return the API's inner body while validating the browser response wrapper."""
    if not isinstance(payload, dict):
        raise TikTokSearchError(f"TikTok {kind}未返回 JSON 对象")
    if "body" not in payload:
        return payload
    status = payload.get("status")
    if status is not None and status != 200:
        raise TikTokSearchError(f"TikTok {kind}接口返回错误（状态 {status}）")
    body = payload.get("body")
    if not isinstance(body, dict):
        raise TikTokSearchError(f"TikTok {kind}响应缺少有效 body")
    code = body.get("code")
    if body.get("errors") or code not in (None, 0):
        detail = body.get("message") or body.get("msg")
        raise TikTokSearchError(
            f"TikTok {kind}返回错误{('：' + str(detail)[:160]) if detail else ''}"
        )
    return body


def _json_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d+(?:,\d{3})*", value.strip()):
        return int(value.replace(",", ""))
    return None


def search_body(query: str, offset: int = 0, search_id: str = "",
                ad_status: str = "ACTIVE") -> dict:
    """The exact-phrase JSON body used by TikTok's public Ad Library."""
    return {
        "query": query,
        "query_type": "1",
        "adv_biz_ids": "",
        "order": "create_time,desc",
        "offset": offset,
        "search_id": search_id,
        "limit": PAGE_SIZE,
        "ad_type": 0,
        "ad_status": 1 if ad_status == "ACTIVE" else 0,
        "ages": ["all"],
        "ad_reach": ["all"],
        "gender": "ALL",
    }


def _window(launch_from: str | None = None, launch_to: str | None = None,
            now: datetime | None = None) -> tuple[datetime, datetime]:
    zone = ZoneInfo("Asia/Shanghai")
    current = (now or datetime.now(timezone.utc)).astimezone(zone)
    end = (datetime.combine(datetime.fromisoformat(launch_to).date(), time.max, tzinfo=zone)
           if launch_to else current)
    start = (datetime.combine(datetime.fromisoformat(launch_from).date(), time.min, tzinfo=zone)
             if launch_from else
             datetime.combine((current - timedelta(days=29)).date(), time.min, tzinfo=zone))
    if start > end:
        raise TikTokSearchError("TikTok 上线时间范围无效")
    return start, end


def search_url(country: str, launch_from: str | None = None,
               launch_to: str | None = None, now: datetime | None = None) -> str:
    """The time-bounded search endpoint used with `search_body`."""
    start, end = _window(launch_from, launch_to, now)
    params = {
        "region": country.upper(),
        "type": 1,
        "start_time": int(start.timestamp()),
        "end_time": int(end.timestamp()),
    }
    return SEARCH_URL + "?" + urlencode(params)


def library_url(country: str, launch_from: str | None = None,
                launch_to: str | None = None, now: datetime | None = None,
                keyword: str | None = None) -> str:
    """The full Ad Library UI URL whose browser context is reused for API calls."""
    start, end = _window(launch_from, launch_to, now)
    params = {
        "region": country.upper(),
        "start_time": int(start.timestamp() * 1000),
        "end_time": int(end.timestamp() * 1000),
        "adv_name": f'"{keyword.strip()}"' if keyword and keyword.strip() else "",
        "query_type": "" if keyword and keyword.strip() else "1",
        "sort_type": "create_time,desc",
        "ad_type": 0,
        "ad_status": 1,
        "ages": "all",
        "ad_reach": "all",
        "gender": "ALL",
    }
    return LIBRARY_URL + "?" + urlencode(params)


def parse_search_response(payload: dict) -> tuple[list[dict], bool, str]:
    """Validate the subset of the public response this collector relies on."""
    payload = _unwrap_payload(payload, kind="搜索")
    if payload.get("errors") or payload.get("code") != 0:
        detail = payload.get("message") or payload.get("msg")
        raise TikTokSearchError(
            f"TikTok 搜索返回错误{('：' + str(detail)[:160]) if detail else ''}"
        )
    has_more = payload.get("has_more")
    if not isinstance(has_more, bool):
        raise TikTokSearchError("TikTok 搜索返回结构无效，不视为空结果")
    if "data" not in payload:
        # TikTok omits `data` when a valid exact search has no matches.
        if payload.get("total") not in (0, "0") or has_more:
            raise TikTokSearchError("TikTok 搜索返回结构无效，不视为空结果")
        rows = []
    else:
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise TikTokSearchError("TikTok 搜索返回结构无效，不视为空结果")
    search_id = payload.get("search_id")
    if search_id is not None and not isinstance(search_id, str):
        raise TikTokSearchError("TikTok 搜索分页标识无效")
    for row in rows:
        if not isinstance(row, dict):
            raise TikTokSearchError("TikTok 搜索结果条目无效")
        ad_id = str(row.get("id") or row.get("ad_id") or "")
        if not ad_id.isdigit():
            raise TikTokSearchError("TikTok 搜索结果缺少有效广告 ID")
    return rows, has_more, search_id or ""


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def matches_exact_phrase(ad, query: str) -> bool:
    """Check visible ad fields, never the request URL or search metadata."""
    wanted = _normalized(query)
    if not wanted:
        return False
    fields = [ad.body, ad.page.name, ad.advertiser_name, ad.sponsor]
    for creative in ad.creatives:
        fields.extend((creative.title, creative.body, creative.description,
                       creative.caption, creative.cta_text))
    return any(f" {wanted} " in f" {_normalized(value or '')} " for value in fields)


def _first_id(row: dict) -> str:
    return str(row.get("id") or row.get("ad_id") or "")


def _detail_id(payload: dict) -> str:
    payload = _unwrap_payload(payload, kind="详情")
    data = payload.get("data") if isinstance(payload, dict) else None
    ad = data.get("ad") if isinstance(data, dict) else None
    return str(ad.get("id") or "") if isinstance(ad, dict) else ""


class TikTokBrowser:
    """A single-context reader for public TikTok search and item details."""

    def __init__(self, proxy=None, page=None, *,
                 time_budget: float = SEARCH_TIME_BUDGET_SECONDS):
        self.runtime = self.browser = self.context = None
        self.page = page
        self.detail_page = page
        self.proxy = proxy
        self.time_budget = max(0.0, float(time_budget))
        self._api_headers = {}

    async def close(self):
        try:
            if self.browser:
                await self.browser.close()
        finally:
            if self.runtime:
                await self.runtime.stop()

    def _launch_options(self):
        options = {
            # Playwright's default headless shell is blocked by TikTok's detail
            # endpoint; the full Chromium new-headless mode uses a normal browser
            # network stack and Client Hints.
            "channel": "chromium",
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
                host = "[" + host + "]"
            server = parsed.scheme + "://" + host + (
                ":" + str(parsed.port) if parsed.port else ""
            )
            options["proxy"] = {"server": server}
            if parsed.username:
                options["proxy"]["username"] = unquote(parsed.username)
            if parsed.password:
                options["proxy"]["password"] = unquote(parsed.password)
        return options

    def _context_options(self):
        # Do not spoof only navigator.userAgent: Chromium still sends matching
        # Client Hints, and TikTok rejects the mismatch with HTTP 421.
        return {
            "locale": "en-US",
            "viewport": {"width": 1440, "height": 900},
        }

    async def _open(self, country: str, launch_from: str | None = None,
                    launch_to: str | None = None, keyword: str | None = None):
        if self.page is not None:
            return
        from playwright.async_api import async_playwright

        self.runtime = await async_playwright().start()
        self.browser = await self.runtime.chromium.launch(**self._launch_options())
        self.context = await self.browser.new_context(**self._context_options())
        self.page = await self.context.new_page()
        self.detail_page = await self.context.new_page()
        last_error = None
        for attempt in range(MAX_OPEN_RETRIES + 1):
            try:
                # Bootstrap the browser and collect the public API config token.
                url = library_url(country, launch_from, launch_to)
                if not await self._goto_with_page_config(url):
                    await self._load_api_config()
                return
            except Exception as exc:
                last_error = exc
                if attempt < MAX_OPEN_RETRIES:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise TikTokSearchError(
            f"TikTok 页面打开失败：{str(last_error).splitlines()[0][:240]}"
        ) from last_error

    async def _fetch(self, method: str, url: str, *, body: dict | None = None,
                     headers: dict | None = None):
        """Issue a same-origin request inside the page's browser network stack."""
        result = await self.page.evaluate(
            """async ({method, url, body, headers}) => {
                const options = {
                    method,
                    credentials: "include",
                    headers: {
                        "accept": "application/json, text/plain, */*",
                        ...(headers || {}),
                    },
                };
                if (body !== null) options.body = JSON.stringify(body);
                const response = await fetch(url, options);
                return {status: response.status, text: await response.text()};
            }""",
            {"method": method, "url": url, "body": body, "headers": headers or {}},
        )
        if not isinstance(result, dict) or not isinstance(result.get("status"), int):
            raise TikTokSearchError("TikTok 页面请求返回结构无效")
        return result

    async def _goto_with_page_config(self, url: str) -> bool:
        """Reuse the support-regions response the page already fetches itself."""
        goto_completed = False
        try:
            async with self.page.expect_response(
                lambda response: response.url.startswith(SUPPORT_REGIONS_URL),
                timeout=30000,
            ) as response_info:
                await self.page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
                goto_completed = True
            response = await response_info.value
        except Exception:
            if not goto_completed:
                raise
            return False
        if response.status != 200:
            return False
        try:
            payload = await response.json()
        except Exception:
            return False
        config_str = payload.get("config_str") if isinstance(payload, dict) else None
        if not isinstance(config_str, str) or not config_str.strip():
            return False
        self._api_headers = {"X-CCL-STR": config_str}
        # The page can update its URL after the initial bootstrap request.
        # Let that navigation finish before issuing a same-origin API call.
        await self.page.wait_for_timeout(1500)
        return True

    async def _load_api_config(self, *, required: bool = True):
        """Load the public request token required by TikTok's web API."""
        response = await self._fetch("GET", SUPPORT_REGIONS_URL)
        if response["status"] != 200:
            if not required and self._api_headers and response["status"] in (421, 429):
                return
            raise TikTokSearchError(
                f"TikTok 公共配置请求失败（HTTP {response['status']}）"
            )
        try:
            payload = json.loads(response["text"])
        except Exception as exc:
            raise TikTokSearchError("TikTok 公共配置响应不是有效 JSON") from exc
        config_str = payload.get("config_str") if isinstance(payload, dict) else None
        if not isinstance(config_str, str) or not config_str.strip():
            raise TikTokSearchError("TikTok 公共配置响应缺少 config_str")
        self._api_headers = {"X-CCL-STR": config_str}

    async def _post_json(self, url: str, body: dict) -> dict:
        for attempt in range(MAX_SEARCH_RETRIES + 1):
            try:
                response = await self._fetch(
                    "POST",
                    url,
                    body=body,
                    headers={
                        "content-type": "application/json",
                        **self._api_headers,
                    },
                )
            except Exception as exc:
                if attempt < MAX_SEARCH_RETRIES:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise TikTokSearchError(
                    f"TikTok 搜索网络请求失败：{str(exc).splitlines()[0][:240]}"
                ) from exc
            if response["status"] != 421:
                break
            if attempt < MAX_SEARCH_RETRIES:
                await self._refresh_api_session()
        if response["status"] != 200:
            detail = response.get("text", "").strip()[:120]
            suffix = f"：{detail}" if detail else ""
            raise TikTokSearchError(
                f"TikTok 搜索请求失败（HTTP {response['status']}{suffix}）",
                status=response["status"],
            )
        try:
            return json.loads(response["text"])
        except Exception as exc:
            raise TikTokSearchError("TikTok 搜索响应不是有效 JSON") from exc

    async def _refresh_api_session(self):
        """Reset TikTok's per-page detail throttle and reload its public API config."""
        if not hasattr(self.page, "expect_response"):
            await self.page.reload(wait_until="domcontentloaded", timeout=45000)
            await self._load_api_config(required=not bool(self._api_headers))
            return
        try:
            async with self.page.expect_response(
                lambda response: response.url.startswith(SUPPORT_REGIONS_URL),
                timeout=30000,
            ) as response_info:
                await self.page.reload(wait_until="domcontentloaded", timeout=45000)
            response = await response_info.value
            payload = await response.json() if response.status == 200 else None
            config_str = (
                payload.get("config_str") if isinstance(payload, dict) else None
            )
            if isinstance(config_str, str) and config_str.strip():
                self._api_headers = {"X-CCL-STR": config_str}
                await self.page.wait_for_timeout(1500)
                return
        except Exception:
            pass
        await self._load_api_config(required=not bool(self._api_headers))

    async def _get_json(self, url: str) -> dict:
        match = re.search(r"/items/(\d+)/details", url)
        if not match:
            raise TikTokSearchError("TikTok 详情接口地址无效")
        ad_id = match.group(1)
        detail_page_url = DETAIL_PAGE_URL.format(ad_id=ad_id)
        detail_page = self.detail_page or self.page
        for attempt in range(MAX_DETAIL_RETRIES + 1):
            try:
                detail_pattern = re.compile(
                    rf"/api/v1/items/{re.escape(ad_id)}/details(?:[?#]|$)"
                )
                async with detail_page.expect_response(
                    lambda response: bool(detail_pattern.search(response.url)),
                    timeout=30000,
                ) as response_info:
                    await detail_page.goto(
                        detail_page_url,
                        wait_until="domcontentloaded",
                        timeout=45000,
                    )
                response = await response_info.value
            except Exception as exc:
                if attempt < MAX_DETAIL_RETRIES:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise TikTokSearchError(
                    f"TikTok 详情页加载失败：{str(exc).splitlines()[0][:240]}"
                ) from exc
            try:
                return await self._json_response(response, kind="详情")
            except TikTokSearchError:
                if response.status not in (421, 429) or attempt >= MAX_DETAIL_RETRIES:
                    raise
            delay = (
                15.0 * (attempt + 1)
                if response.status == 429
                else 10.0 * (attempt + 1)
            )
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def _json_response(self, response, *, kind: str) -> dict:
        status = response.status
        try:
            payload = await response.json()
        except Exception as exc:
            detail = ""
            try:
                detail = (await response.text()).strip()[:120]
            except Exception:
                pass
            if status == 200:
                raise TikTokSearchError(
                    f"TikTok {kind}响应不是有效 JSON"
                ) from exc
            suffix = f"：{detail}" if detail else ""
            raise TikTokSearchError(
                f"TikTok {kind}请求失败（HTTP {status}{suffix}）",
                status=status,
            ) from exc
        if status != 200:
            detail = ""
            if isinstance(payload, dict):
                detail = str(
                    payload.get("message") or payload.get("msg") or ""
                ).strip()[:120]
            suffix = f"：{detail}" if detail else ""
            raise TikTokSearchError(
                f"TikTok {kind}请求失败（HTTP {status}{suffix}）",
                status=status,
            )
        return _unwrap_payload(payload, kind=kind)

    async def search(self, query: str, country: str, limit: int, *,
                     ad_status: str = "ACTIVE", search_mode: str = "exact",
                     launch_from: str | None = None, launch_to: str | None = None):
        """Return detailed records. Missing or malformed responses are failures."""
        query, country = query.strip(), country.upper()
        if not query:
            raise TikTokSearchError("请先输入搜索关键词")
        if country not in TIKTOK_COUNTRIES:
            raise TikTokSearchError(f"TikTok 公开广告库暂不支持国家 {country}")
        if search_mode != "exact":
            raise TikTokSearchError("TikTok 采集第一版仅支持完整短语匹配")
        deadline = monotonic() + self.time_budget
        await self._open(country, launch_from, launch_to)
        endpoint = search_url(country, launch_from, launch_to)
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TikTokSearchError("TikTok 搜索达到内部时间上限，未取得首屏结果")
        try:
            payload = await asyncio.wait_for(
                self._post_json(
                    endpoint,
                    search_body(query, 0, "", ad_status),
                ),
                timeout=remaining,
            )
        except asyncio.TimeoutError as exc:
            raise TikTokSearchError(
                "TikTok 搜索达到内部时间上限，未取得首屏结果"
            ) from exc
        seen: set[str] = set()
        selected = []
        search_id = ""
        offset = 0
        for page_number in range(MAX_PAGE_REQUESTS):
            rows, has_more, next_search_id = parse_search_response(payload)
            search_id = next_search_id or search_id
            if not rows and has_more:
                raise TikTokSearchError("TikTok 搜索返回空页但声称还有下一页")
            if not rows:
                return selected, True
            for row_number, row in enumerate(rows):
                ad_id = _first_id(row)
                if ad_id in seen:
                    continue
                seen.add(ad_id)
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return selected, False
                try:
                    detail = await asyncio.wait_for(
                        self._get_json(DETAIL_URL.format(ad_id=ad_id)),
                        timeout=remaining,
                    )
                except asyncio.TimeoutError:
                    return selected, False
                except TikTokSearchError:
                    # Detail-page throttling is common after a burst of reads.
                    # Keep already matched details so they can still be stored.
                    if selected:
                        return selected, False
                    raise
                returned_id = _detail_id(detail)
                if returned_id != ad_id:
                    raise TikTokSearchError("TikTok 详情广告 ID 与搜索结果不一致")
                normalized = from_tiktok_record(detail)
                if search_mode == "exact" and not matches_exact_phrase(normalized, query):
                    continue
                selected.append(detail)
                if len(selected) >= limit:
                    finished_page = row_number == len(rows) - 1
                    return selected, not has_more and finished_page
            if not has_more:
                return selected, True
            if page_number >= MAX_PAGE_REQUESTS - 1:
                break
            remaining = deadline - monotonic()
            if remaining <= 0:
                return selected, False
            offset += len(rows)
            try:
                payload = await asyncio.wait_for(
                    self._post_json(
                        endpoint,
                        search_body(query, offset, search_id, ad_status),
                    ),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                return selected, False
            except TikTokSearchError:
                # A later page can be throttled after the previous page already
                # produced usable details. Preserve those instead of dropping all.
                if selected:
                    return selected, False
                raise
        raise TikTokSearchError("TikTok 搜索分页超过安全上限，未视为完整结果")

