"""TikTok public Ad Library collection, normalization, persistence, and UI."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest


def _detail(ad_id: str = "1868694304436386", *,
            title: str = "I Got Three Girlfriends From the System") -> dict:
    return {
        "data": {
            "ad": {
                "id": ad_id,
                "name": "DramaBox: Short Dramas",
                "title": title,
                "description": "A short drama now streaming.",
                "first_shown_date": 1782950400000,
                "last_shown_date": 1793836800000,
                "videos": [{
                    "video_url": (
                        "https://library.tiktok.com/api/v1/cdn/1794194551/video/"
                        "aHR0cHM6Ly92MTZtLnRpa3Rvay5jZG4vdmlkZW8ubXA0/uuid"
                    ),
                    "cover_img": "https://p16-common-sign.tiktokcdn.com/cover.jpeg",
                }],
                "image_urls": ["https://p16-common-sign.tiktokcdn.com/image.jpeg"],
                "external_url": "https://example.com/watch",
                "call_to_action": "Learn more",
                "advertising_objective": "TRAFFIC",
            },
            "advertiser": {
                "name": "DramaBox",
                "adv_biz_ids": "7476393456269754369",
                "registry_location": "China",
                "sponsor": "WHATECH MOBILE CO., LIMITED",
                "avatar_url": "https://p16-common-sign.tiktokcdn.com/avatar.jpeg",
            },
            "targeting": {
                "location": {
                    "total_region": 2,
                    "total_impressions": "0-1K",
                    "data": [
                        {
                            "region": "DE",
                            "impressions": "0-1K",
                            "breakdowns": [
                                {"age": "25-34", "gender": "MALE", "impressions": "0-1K"},
                                {"age": "25-34", "gender": "FEMALE", "impressions": "0-1K"},
                            ],
                        },
                        {
                            "region": "US",
                            "impressions": 1200,
                            "breakdowns": [
                                {"age": "35-44", "gender": "UNKNOWN", "impressions": 1200},
                            ],
                        },
                    ],
                },
            },
        },
    }


class _FakeResponse:
    def __init__(self, status: int = 200, payload=None, json_error: bool = False,
                 url: str = "", request=None):
        self.status = status
        self.payload = payload
        self.json_error = json_error
        self.url = url
        self.request = request

    async def json(self):
        if self.json_error:
            raise ValueError("not json")
        return self.payload

    async def text(self):
        if isinstance(self.payload, str):
            return self.payload
        if self.payload is None:
            return ""
        return json.dumps(self.payload)


class _FakeRequest:
    def __init__(self, posts=(), gets=()):
        self.posts = list(posts)
        self.gets = list(gets)
        self.post_calls = []
        self.get_calls = []

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        result = self.posts.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        result = self.gets.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakePage:
    url = "https://library.tiktok.com/ads?region=DE"

    def __init__(self, request, detail_responses=()):
        self.request = request
        self.detail_responses = list(detail_responses)
        self.search_terms = []
        self.reload_calls = []
        self.goto_calls = []
        self.expect_response_calls = []
        self.text_calls = []

    async def reload(self, **kwargs):
        self.reload_calls.append(kwargs)

    def expect_response(self, predicate, timeout):
        assert timeout == 30000
        self.expect_response_calls.append(predicate)
        response = self.detail_responses.pop(0)
        if not isinstance(response, Exception):
            assert predicate(response)
        return _ResponseContext(response)

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))

    async def wait_for_timeout(self, timeout):
        assert timeout >= 0

    async def evaluate(self, script, arg):
        if arg["method"] == "POST":
            response = await self.request.post(
                arg["url"], data=json.dumps(arg["body"]), headers=arg["headers"],
            )
        else:
            response = await self.request.get(arg["url"], headers=arg["headers"])
        try:
            text = json.dumps(await response.json())
        except Exception:
            text = "not json"
        return {"status": response.status, "text": text}

    def get_by_placeholder(self, placeholder):
        assert placeholder == "Search by name or keyword"
        return _FakeLocator(fill=self.search_terms.append)

    def get_by_role(self, role, name, exact):
        assert role == "button" and exact is True
        assert name == "Search"
        return _FakeLocator()

    def get_by_text(self, text, exact):
        self.text_calls.append((text, exact))
        assert text == "View more" and exact is True
        return _FakeLocator()


class _FakeLocator:
    def __init__(self, fill=None):
        self._fill = fill

    async def fill(self, value):
        if self._fill:
            self._fill(value)

    async def click(self):
        return None

    async def wait_for(self, **kwargs):
        return None


class _ConfigResponse:
    status = 200
    url = "https://library.tiktok.com/api/v1/support-regions?lang=en-US"

    async def json(self):
        return {"config_str": "page-owned-config"}


class _ResponseInfo:
    def __init__(self, response):
        self._response = response

    @property
    def value(self):
        async def get_response():
            return self._response

        return get_response()


class _ResponseContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        if isinstance(self._response, Exception):
            raise self._response
        return _ResponseInfo(self._response)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ConfigPage:
    url = "https://library.tiktok.com/ads?region=GB"

    def __init__(self):
        self.goto_calls = []

    def expect_response(self, predicate, timeout):
        assert timeout == 30000
        return _ResponseContext(_ConfigResponse())

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))

    async def wait_for_timeout(self, timeout):
        assert timeout == 1500


def _search_page(rows, has_more=False, search_id="search-1"):
    return {
        "status": 200,
        "body": {
            "code": 0,
            "data": rows,
            "has_more": has_more,
            "search_id": search_id,
        },
    }


def test_parse_search_response_accepts_wrapped_and_direct_payloads():
    from tracker.worker.tiktok_browser import parse_search_response

    rows = [{"id": "123"}]
    assert parse_search_response(_search_page(rows)) == (rows, False, "search-1")
    assert parse_search_response({
        "code": 0, "data": rows, "has_more": True, "search_id": None,
    }) == (rows, True, "")


def test_parse_search_response_accepts_an_explicit_empty_result_without_data():
    from tracker.worker.tiktok_browser import parse_search_response

    assert parse_search_response({
        "total": 0,
        "has_more": False,
        "search_id": "empty-search",
        "code": 0,
    }) == ([], False, "empty-search")


@pytest.mark.parametrize("payload, message", [
    ({"status": 503, "body": {}}, "状态 503"),
    ({"status": 200, "body": {"code": 7, "message": "denied"}}, "denied"),
    ({"status": 200, "body": {"code": 0, "data": {}, "has_more": False}}, "结构无效"),
    ({"status": 200, "body": {"code": 0, "data": [], "has_more": "no"}}, "结构无效"),
    ({"code": 0, "has_more": False, "search_id": "bad-empty"}, "结构无效"),
    ({"code": 0, "total": 1, "has_more": False, "search_id": "bad-empty"}, "结构无效"),
    ({"status": 200, "body": {"code": 0, "data": [{"name": "no id"}],
                              "has_more": False}}, "广告 ID"),
])
def test_parse_search_response_rejects_malformed_or_error_payloads(payload, message):
    from tracker.worker.tiktok_browser import TikTokSearchError, parse_search_response

    with pytest.raises(TikTokSearchError, match=message):
        parse_search_response(payload)


def test_search_body_is_exact_and_uses_tiktok_controls():
    from tracker.worker.tiktok_browser import search_body

    body = search_body("The Crimson", offset=12, search_id="abc")
    assert body["query"] == "The Crimson"
    assert body["query_type"] == "1"
    assert body["offset"] == 12
    assert body["search_id"] == "abc"
    assert body["ad_status"] == 1
    assert "search_clause" not in body


def test_search_and_library_urls_use_a_single_country_and_time_window():
    from tracker.worker.tiktok_browser import library_url, search_url

    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    endpoint = search_url("de", now=now)
    page = library_url("de", now=now, keyword="The Crimson")
    assert "region=DE" in endpoint
    assert "start_time=" in endpoint and "end_time=" in endpoint
    assert "ad_status=1" in page
    assert "sort_type=create_time%2Cdesc" in page
    assert "adv_name=%22The+Crimson%22" in page


def test_tiktok_proxy_environment_is_passed_to_the_browser(monkeypatch):
    from tracker.worker.collector import _default_make_tiktok

    monkeypatch.setenv("TIKTOK_PROXY", " socks5://host.docker.internal:7890 ")
    browser = _default_make_tiktok(proxy="http://database-proxy:8080")
    assert browser.proxy == "socks5://host.docker.internal:7890"


def test_tiktok_browser_does_not_spoof_only_the_user_agent():
    from tracker.worker.tiktok_browser import TikTokBrowser

    browser = TikTokBrowser()
    options = browser._context_options()
    assert "user_agent" not in options
    assert options["locale"] == "en-US"
    assert browser._launch_options()["channel"] == "chromium"


def test_exact_phrase_matching_uses_visible_ad_fields_not_search_metadata():
    from tracker.worker.normalize import from_tiktok_record
    from tracker.worker.tiktok_browser import matches_exact_phrase

    ad = from_tiktok_record(_detail())
    assert matches_exact_phrase(ad, "three girlfriends from the system")
    assert matches_exact_phrase(ad, "DRAMABOX")
    assert not matches_exact_phrase(ad, "System extra")


def test_from_tiktok_record_keeps_interval_reach_and_maps_public_fields():
    from tracker.worker.normalize import from_tiktok_record

    ad = from_tiktok_record(_detail())
    assert (ad.id, ad.platform, ad.external_ad_id) == (
        "tt_1868694304436386", "tiktok", "1868694304436386",
    )
    assert ad.page.id == "tt_advertiser:7476393456269754369"
    assert ad.page.name == "DramaBox"
    assert ad.advertiser_name == "DramaBox"
    assert ad.advertiser_id == "7476393456269754369"
    assert ad.registry_location == "China"
    assert ad.sponsor == "WHATECH MOBILE CO., LIMITED"
    assert ad.delivery_start_time == datetime(2026, 7, 2, tzinfo=timezone.utc)
    assert ad.publisher_platforms == ["TikTok"]
    assert ad.coverage_countries == ["DE", "US"]
    assert ad.coverage_status == "available"
    assert ad.creatives[0].video_url.startswith("https://library.tiktok.com/api/v1/cdn/")
    regions = {r["region"]: r for r in ad.coverage_data["regions"]}
    assert regions["DE"]["reach"] == "0-1K"
    assert regions["DE"]["rows"][0]["male"] == "0-1K"
    assert regions["US"]["reach"] == 1200
    assert regions["US"]["rows"][0]["unknown"] == 1200


def test_from_tiktok_record_accepts_direct_media_and_advertiser_aliases():
    from tracker.worker.normalize import from_tiktok_record

    detail = _detail()
    detail["data"]["ad"]["videos"] = "https://v16m.tiktokcdn.com/direct-video.mp4"
    detail["data"]["ad"]["image_urls"] = {"url": "https://p16-common-sign.tiktokcdn.com/image.jpeg"}
    detail["data"]["advertiser"].pop("name")
    detail["data"]["advertiser"]["display_name"] = "Alias Advertiser"
    ad = from_tiktok_record(detail)
    assert ad.page.name == "Alias Advertiser"
    assert ad.creatives[0].video_url == "https://v16m.tiktokcdn.com/direct-video.mp4"
    assert ad.creatives[0].image_url.endswith("/image.jpeg")


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("data"),
    lambda d: d["data"].pop("ad"),
    lambda d: d["data"].pop("advertiser"),
    lambda d: d["data"]["ad"].update(id="not-numeric"),
])
def test_from_tiktok_record_rejects_missing_or_invalid_required_fields(mutate):
    from tracker.worker.normalize import from_tiktok_record

    detail = _detail()
    mutate(detail)
    with pytest.raises(ValueError):
        from_tiktok_record(detail)


def test_tiktok_browser_filters_exact_matches_and_returns_detail_records():
    from tracker.worker.tiktok_browser import DETAIL_URL, TikTokBrowser

    matching = _detail(ad_id="111")
    unrelated = _detail(ad_id="222", title="A different ad")
    response = _search_page([{"id": "111"}, {"id": "222"}])
    fake_request = _FakeRequest(posts=[_FakeResponse(payload=response)])
    page = _FakePage(
        fake_request,
        detail_responses=[
            _FakeResponse(payload=matching, url=DETAIL_URL.format(ad_id="111")),
            _FakeResponse(payload=unrelated, url=DETAIL_URL.format(ad_id="222")),
        ],
    )
    browser = TikTokBrowser(page=page)
    records, complete = asyncio.run(browser.search(
        "I Got Three Girlfriends From the System", "DE", 10, search_mode="exact",
    ))
    assert [record["data"]["ad"]["id"] for record in records] == ["111"]
    assert complete is True
    assert page.goto_calls[0][0] == (
        "https://library.tiktok.com/ads/detail?ad_id=111"
    )
    assert fake_request.get_calls == []


def test_tiktok_browser_loads_the_required_web_api_config_header():
    from tracker.worker.tiktok_browser import TikTokBrowser

    fake_request = _FakeRequest(gets=[
        _FakeResponse(payload={"code": 0, "config_str": "public-config"}),
    ])
    browser = TikTokBrowser(page=_FakePage(fake_request))
    asyncio.run(browser._load_api_config())
    assert browser._api_headers == {"X-CCL-STR": "public-config"}
    assert fake_request.get_calls[0][0].endswith("/api/v1/support-regions")


def test_tiktok_browser_reuses_the_config_loaded_by_the_page():
    from tracker.worker.tiktok_browser import TikTokBrowser

    page = _ConfigPage()
    browser = TikTokBrowser(page=page)
    assert asyncio.run(browser._goto_with_page_config(
        "https://library.tiktok.com/ads?region=GB"
    ))
    assert browser._api_headers == {"X-CCL-STR": "page-owned-config"}
    assert len(page.goto_calls) == 1


def test_tiktok_browser_treats_an_empty_search_result_as_complete():
    from tracker.worker.tiktok_browser import TikTokBrowser

    fake_request = _FakeRequest(posts=[_FakeResponse(payload={
        "total": 0,
        "has_more": False,
        "search_id": "empty-search",
        "code": 0,
    })])
    browser = TikTokBrowser(page=_FakePage(
        fake_request,
    ))
    records, complete = asyncio.run(browser.search(
        "We Never Said We Went Bankrupt!", "GB", 30, search_mode="exact",
    ))
    assert records == []
    assert complete is True
    assert fake_request.get_calls == []


def test_tiktok_browser_marks_a_limit_stopped_read_as_not_complete():
    from tracker.worker.tiktok_browser import DETAIL_URL, TikTokBrowser

    response = _search_page([{"id": "111"}], has_more=True)
    fake_request = _FakeRequest(posts=[_FakeResponse(payload=response)])
    page = _FakePage(
        fake_request,
        detail_responses=[
            _FakeResponse(
                payload=_detail(ad_id="111"),
                url=DETAIL_URL.format(ad_id="111"),
            ),
        ],
    )
    browser = TikTokBrowser(page=page)
    records, complete = asyncio.run(browser.search(
        "I Got Three Girlfriends From the System", "DE", 1, search_mode="exact",
    ))
    assert len(records) == 1
    assert complete is False


def test_tiktok_browser_paginates_with_offset_and_search_id():
    from tracker.worker.tiktok_browser import DETAIL_URL, TikTokBrowser

    query = "I Got Three Girlfriends From the System"
    first = _search_page([{"id": "111"}], has_more=True, search_id="search-1")
    second = _search_page([{"id": "222"}], has_more=False, search_id="search-2")
    fake_request = _FakeRequest(posts=[
        _FakeResponse(payload=first),
        _FakeResponse(payload=second),
    ])
    page = _FakePage(fake_request, detail_responses=[
        _FakeResponse(payload=_detail(ad_id="111"), url=DETAIL_URL.format(ad_id="111")),
        _FakeResponse(payload=_detail(ad_id="222"), url=DETAIL_URL.format(ad_id="222")),
    ])
    browser = TikTokBrowser(page=page)
    records, complete = asyncio.run(browser.search(
        query, "AT", 10, search_mode="exact",
    ))
    assert [record["data"]["ad"]["id"] for record in records] == ["111", "222"]
    assert complete is True
    second_body = json.loads(fake_request.post_calls[1][1]["data"])
    assert second_body["offset"] == 1
    assert second_body["search_id"] == "search-1"
    assert second_body["query"] == query
    assert second_body["query_type"] == "1"


def test_tiktok_browser_direct_search_does_not_depend_on_page_text():
    from tracker.worker.tiktok_browser import TikTokBrowser

    fake_request = _FakeRequest(posts=[_FakeResponse(payload=_search_page([]))])
    page = _FakePage(fake_request)
    browser = TikTokBrowser(page=page)
    asyncio.run(browser.search("The Crimson", "AT", 10, search_mode="exact"))
    assert page.text_calls == []
    assert page.search_terms == []


def test_tiktok_browser_rejects_detail_id_mismatch():
    from tracker.worker.tiktok_browser import (
        DETAIL_URL,
        TikTokBrowser,
        TikTokSearchError,
    )

    fake_request = _FakeRequest(
        posts=[_FakeResponse(payload=_search_page([{"id": "111"}]))],
    )
    page = _FakePage(
        fake_request,
        detail_responses=[
            _FakeResponse(
                payload=_detail(ad_id="999"),
                url=DETAIL_URL.format(ad_id="111"),
            ),
        ],
    )
    browser = TikTokBrowser(page=page)
    with pytest.raises(TikTokSearchError, match="ID 与搜索结果不一致"):
        asyncio.run(browser.search("System", "DE", 10, search_mode="exact"))


def test_tiktok_browser_keeps_matches_when_a_detail_is_throttled(monkeypatch):
    from tracker.worker import tiktok_browser
    from tracker.worker.tiktok_browser import DETAIL_URL, TikTokBrowser

    async def no_delay(delay):
        assert delay > 0

    monkeypatch.setattr(tiktok_browser.asyncio, "sleep", no_delay)
    fake_request = _FakeRequest(posts=[
        _FakeResponse(payload=_search_page([{"id": "111"}, {"id": "222"}])),
    ])
    page = _FakePage(fake_request, detail_responses=[
        _FakeResponse(
            payload=_detail(ad_id="111"),
            url=DETAIL_URL.format(ad_id="111"),
        ),
        *[
            _FakeResponse(
                status=421,
                payload="system busy",
                url=DETAIL_URL.format(ad_id="222"),
            )
            for _ in range(4)
        ],
    ])
    browser = TikTokBrowser(page=page)
    records, complete = asyncio.run(browser.search(
        "I Got Three Girlfriends From the System", "AT", 10,
        search_mode="exact",
    ))
    assert [record["data"]["ad"]["id"] for record in records] == ["111"]
    assert complete is False


def test_tiktok_browser_keeps_matches_when_a_later_page_is_throttled(monkeypatch):
    from tracker.worker import tiktok_browser
    from tracker.worker.tiktok_browser import DETAIL_URL, TikTokBrowser

    async def no_delay(delay):
        assert delay > 0

    monkeypatch.setattr(tiktok_browser.asyncio, "sleep", no_delay)
    first = _search_page([{"id": "111"}], has_more=True, search_id="search-1")
    fake_request = _FakeRequest(posts=[
        _FakeResponse(payload=first),
        ConnectionError("second page reset"),
        ConnectionError("second page reset"),
        ConnectionError("second page reset"),
    ])
    page = _FakePage(fake_request, detail_responses=[
        _FakeResponse(
            payload=_detail(ad_id="111"),
            url=DETAIL_URL.format(ad_id="111"),
        ),
    ])
    browser = TikTokBrowser(page=page)
    records, complete = asyncio.run(browser.search(
        "I Got Three Girlfriends From the System", "AT", 30,
        search_mode="exact",
    ))
    assert [record["data"]["ad"]["id"] for record in records] == ["111"]
    assert complete is False
    assert len(fake_request.post_calls) == 4


def test_tiktok_browser_surfaces_http_and_json_failures():
    from tracker.worker.tiktok_browser import TikTokBrowser, TikTokSearchError

    browser = TikTokBrowser(page=_FakePage(
        _FakeRequest(posts=[_FakeResponse(status=503)]),
    ))
    with pytest.raises(TikTokSearchError, match="HTTP 503"):
        asyncio.run(browser.search("System", "DE", 10, search_mode="exact"))

    browser = TikTokBrowser(page=_FakePage(
        _FakeRequest(posts=[_FakeResponse(json_error=True)]),
    ))
    with pytest.raises(TikTokSearchError, match="不是有效 JSON"):
        asyncio.run(browser.search("System", "DE", 10, search_mode="exact"))


def test_tiktok_browser_refreshes_the_page_after_search_421():
    from tracker.worker.tiktok_browser import TikTokBrowser, search_body, search_url

    fake_request = _FakeRequest(
        posts=[
            _FakeResponse(status=421),
            _FakeResponse(payload={"code": 0, "data": [], "has_more": False}),
        ],
        gets=[
            _FakeResponse(status=429),
        ],
    )
    page = _FakePage(fake_request)
    browser = TikTokBrowser(page=page)
    browser._api_headers = {"X-CCL-STR": "existing-config"}
    payload = asyncio.run(browser._post_json(
        search_url("GB"), search_body("The Crimson"),
    ))
    assert payload["code"] == 0
    assert fake_request.get_calls[0][0].endswith("/api/v1/support-regions")
    assert browser._api_headers == {"X-CCL-STR": "existing-config"}


def test_tiktok_browser_refreshes_and_retries_the_first_search_after_421():
    from tracker.worker.tiktok_browser import TikTokBrowser

    fake_request = _FakeRequest(posts=[
        _FakeResponse(status=421, payload="system busy"),
        _FakeResponse(payload={
            "code": 0,
            "data": [],
            "has_more": False,
            "search_id": "empty-search",
        }),
    ])
    page = _FakePage(
        fake_request,
        detail_responses=[_ConfigResponse()],
    )
    browser = TikTokBrowser(page=page)
    browser._api_headers = {"X-CCL-STR": "existing-config"}
    records, complete = asyncio.run(browser.search(
        "The Crimson", "GB", 30, search_mode="exact",
    ))
    assert records == []
    assert complete is True
    assert len(fake_request.post_calls) == 2
    assert len(page.reload_calls) == 1
    assert browser._api_headers == {"X-CCL-STR": "page-owned-config"}


def test_tiktok_browser_stops_at_its_time_budget_and_keeps_matches(monkeypatch):
    from tracker.worker import tiktok_browser
    from tracker.worker.tiktok_browser import DETAIL_URL, TikTokBrowser

    response = _search_page([{"id": "111"}, {"id": "222"}], has_more=False)
    page = _FakePage(
        _FakeRequest(posts=[_FakeResponse(payload=response)]),
        detail_responses=[
            _FakeResponse(
                payload=_detail(ad_id="111"),
                url=DETAIL_URL.format(ad_id="111"),
            ),
        ],
    )
    clock = iter([0.0, 0.0, 0.0, 2.0])
    monkeypatch.setattr(tiktok_browser, "monotonic", lambda: next(clock))
    browser = TikTokBrowser(page=page, time_budget=1.0)
    records, complete = asyncio.run(browser.search(
        "I Got Three Girlfriends From the System", "DE", 10,
        search_mode="exact",
    ))
    assert [record["data"]["ad"]["id"] for record in records] == ["111"]
    assert complete is False
    assert len(page.goto_calls) == 1


def test_tiktok_browser_retries_detail_by_reopening_the_public_detail_page(
        monkeypatch):
    from tracker.worker import tiktok_browser
    from tracker.worker.tiktok_browser import DETAIL_URL, TikTokBrowser

    delays = []

    async def record_delay(delay):
        delays.append(delay)

    monkeypatch.setattr(tiktok_browser.asyncio, "sleep", record_delay)
    detail_url = DETAIL_URL.format(ad_id="1868694304436386")
    page = _FakePage(_FakeRequest(), detail_responses=[
        _FakeResponse(status=421, payload="system busy", url=detail_url),
        _FakeResponse(payload=_detail(), url=detail_url),
    ])
    browser = TikTokBrowser(page=page)
    detail = asyncio.run(browser._get_json(detail_url))
    assert detail["data"]["ad"]["id"] == "1868694304436386"
    assert delays == [10.0]
    assert len(page.goto_calls) == 2
    assert page.reload_calls == []


def test_tiktok_browser_backs_off_and_retries_detail_rate_limit(monkeypatch):
    from tracker.worker import tiktok_browser
    from tracker.worker.tiktok_browser import DETAIL_URL, TikTokBrowser

    delays = []

    async def record_delay(delay):
        delays.append(delay)

    monkeypatch.setattr(tiktok_browser.asyncio, "sleep", record_delay)
    detail_url = DETAIL_URL.format(ad_id="1868694304436386")
    page = _FakePage(_FakeRequest(), detail_responses=[
        _FakeResponse(status=429, payload="system busy", url=detail_url),
        _FakeResponse(payload=_detail(), url=detail_url),
    ])
    browser = TikTokBrowser(page=page)
    detail = asyncio.run(browser._get_json(detail_url))
    assert detail["data"]["ad"]["id"] == "1868694304436386"
    assert delays == [15.0]
    assert len(page.goto_calls) == 2


def test_tiktok_browser_matches_detail_response_with_extra_query_parameters():
    from tracker.worker.tiktok_browser import DETAIL_URL, TikTokBrowser

    detail_url = DETAIL_URL.format(ad_id="1868694304436386")
    page = _FakePage(_FakeRequest(), detail_responses=[
        _FakeResponse(payload=_detail(), url=detail_url + "&from=public-detail"),
    ])
    browser = TikTokBrowser(page=page)
    detail = asyncio.run(browser._get_json(detail_url))
    assert detail["data"]["ad"]["id"] == "1868694304436386"


def test_tiktok_browser_retries_transient_search_and_detail_network_errors():
    from tracker.worker.tiktok_browser import DETAIL_URL, TikTokBrowser, search_body, search_url

    fake_request = _FakeRequest(
        posts=[
            ConnectionError("search reset"),
            _FakeResponse(payload={"code": 0, "data": [], "has_more": False}),
        ],
    )
    detail_url = DETAIL_URL.format(ad_id="1868694304436386")
    page = _FakePage(fake_request, detail_responses=[
        ConnectionError("detail page reset"),
        _FakeResponse(payload=_detail(), url=detail_url),
    ])
    browser = TikTokBrowser(page=page)
    browser._api_headers = {"X-CCL-STR": "existing-config"}
    payload = asyncio.run(browser._post_json(
        search_url("GB"), search_body("The Crimson"),
    ))
    detail = asyncio.run(browser._get_json(
        DETAIL_URL.format(ad_id="1868694304436386")
    ))
    assert payload["code"] == 0
    assert detail["data"]["ad"]["id"] == "1868694304436386"
    assert len(fake_request.post_calls) == 2
    assert len(page.goto_calls) == 1
    assert len(page.expect_response_calls) == 2


def test_tiktok_browser_rejects_unsupported_inputs_before_requests():
    from tracker.worker.tiktok_browser import TikTokBrowser, TikTokSearchError

    browser = TikTokBrowser()
    with pytest.raises(TikTokSearchError, match="不支持国家 US"):
        asyncio.run(browser.search("System", "US", 10, search_mode="exact"))
    with pytest.raises(TikTokSearchError, match="完整短语"):
        asyncio.run(browser.search("System", "DE", 10, search_mode="broad"))
    with pytest.raises(TikTokSearchError, match="搜索关键词"):
        asyncio.run(browser.search(" ", "DE", 10, search_mode="exact"))


def _persist_tiktok_ad(db, detail=None):
    from tracker.worker import persist
    from tracker.worker.normalize import from_tiktok_record

    detail = detail or _detail()
    ad = from_tiktok_record(detail)
    ad.search_country = "DE"
    company_id = db.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id", ("DramaBox",),
    ).fetchone()["id"]
    page_ref_id = db.execute(
        """INSERT INTO pages (company_id, page_id, page_name, collect_country, platform)
           VALUES (%s, %s, %s, %s, 'tiktok') RETURNING id""",
        (company_id, ad.page.id, ad.page.name, "DE"),
    ).fetchone()["id"]
    db.commit()
    run_id = persist.start_collection_run(db, trigger="manual", pages_total=1)
    stored = persist.upsert_ad(
        db, company_id, page_ref_id, run_id, ad, datetime.now(timezone.utc),
    )
    return stored.ad_id, ad


def test_persist_upsert_writes_tiktok_identity_coverage_and_creative(db):
    from tracker.worker import persist

    ad_db_id, ad = _persist_tiktok_ad(db)
    row = db.execute(
        """SELECT ad_archive_id, platform, external_ad_id, advertiser_name,
                  advertiser_id, registry_location, sponsor, publisher_platforms,
                  coverage_countries, coverage_source, coverage_status
           FROM ads WHERE id=%s""",
        (ad_db_id,),
    ).fetchone()
    assert row == {
        "ad_archive_id": "tt_1868694304436386",
        "platform": "tiktok",
        "external_ad_id": "1868694304436386",
        "advertiser_name": "DramaBox",
        "advertiser_id": "7476393456269754369",
        "registry_location": "China",
        "sponsor": "WHATECH MOBILE CO., LIMITED",
        "publisher_platforms": ["TikTok"],
        "coverage_countries": ["DE", "US"],
        "coverage_source": "tiktok_ad_library",
        "coverage_status": "available",
    }
    creative = db.execute(
        "SELECT video_url, thumbnail_url FROM creatives WHERE ad_id=%s", (ad_db_id,),
    ).fetchone()
    assert creative["video_url"] == ad.creatives[0].video_url
    assert creative["thumbnail_url"].startswith("https://p16-common-sign.tiktokcdn.com/")
    assert db.execute(
        "SELECT 1 FROM ad_appearances WHERE ad_id=%s", (ad_db_id,),
    ).fetchone()
    pending = persist.pending_media(db)
    assert pending == []


def test_summary_keeps_tiktok_reach_ranges_out_of_numeric_totals(db):
    from tracker import db as dbmod

    _persist_tiktok_ad(db)
    summary = dbmod.get_library_summary(dbmod.LibraryFilters(status="all"))
    assert summary["total"] == 1
    assert summary["total_reach"] is None
    assert summary["coverage_ad_count"] == 0
    assert summary["audience"] == {"male": None, "female": None, "unknown": None}
    assert summary["regions"] == [
        {"code": "EU", "name": "欧盟"},
        {"code": "US", "name": "美国"},
    ]
    assert summary["publishers"] == ["TikTok"]
    assert summary["representative_ad"]["ad_archive_id"] == "tt_1868694304436386"
    assert summary["representative_ad"]["reach"] is None


def test_export_uses_tiktok_platform_and_library_url(db):
    from tracker import db as dbmod

    _persist_tiktok_ad(db)
    rows = list(dbmod.iter_ads_for_export(dbmod.LibraryFilters(status="all")))
    assert len(rows) == 1
    assert rows[0]["platform"] == "tiktok"
    assert rows[0]["external_ad_id"] == "1868694304436386"
    assert rows[0]["ad_library_url"] == (
        "https://library.tiktok.com/ads/detail/?ad_id=1868694304436386"
    )


def test_collect_start_validates_tiktok_and_saves_supported_settings(db, client):
    invalid = [
        {"mode": "pages", "page_ids": ["1"]},
        {"match_mode": "broad"},
        {"ad_status": "ALL"},
        {"country": "US"},
        {"country": "EUROPE"},
        {"keyword": ""},
    ]
    base = {
        "collect_platform": "tiktok",
        "mode": "keyword",
        "keyword": "The Crimson",
        "country": "DE",
        "ad_status": "ACTIVE",
        "max_ads": "30",
        "interval_hours": "24",
        "launch_days": "30",
        "collection_sort": "newest",
        "match_mode": "exact",
    }
    for override in invalid:
        response = client.post("/collect/start", data={**base, **override})
        assert response.status_code == 400, (override, response.text)

    response = client.post("/collect/start", data=base)
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    row = db.execute(
        """SELECT collect_platform, collect_mode, collect_keyword, collect_country,
                  collect_region, collect_ad_status, collect_match_mode, collect_source
           FROM app_settings WHERE id=1"""
    ).fetchone()
    assert row == {
        "collect_platform": "tiktok",
        "collect_mode": "keyword",
        "collect_keyword": "The Crimson",
        "collect_country": "DE",
        "collect_region": None,
        "collect_ad_status": "ACTIVE",
        "collect_match_mode": "exact",
        "collect_source": "self_hosted",
    }


def test_tiktok_postprocessing_skips_meta_only_steps(monkeypatch):
    from tracker.worker import scheduler

    called = []
    monkeypatch.setattr(
        scheduler, "_post_collection_content_steps",
        lambda step, fail: called.append("content"),
    )
    assert scheduler._post_collection_steps_tiktok(lambda label: None, lambda label, detail: None) is None
    assert called == ["content"]
