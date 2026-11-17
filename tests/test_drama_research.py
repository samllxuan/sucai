"""Unit and contract checks for anonymous TikTok/YouTube drama research."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tracker.db import drama_research as drama_research_db
from tracker.worker import drama_collector
from tracker.worker.drama_parsers import (
    matches_aliases,
    parse_tiktok_video,
    parse_youtube_comment_count,
    parse_youtube_video,
    tiktok_page_has_server_error,
    tiktok_video_links,
    youtube_comment_continuation,
    youtube_page_is_explicitly_empty,
    youtube_search_url,
    youtube_video_ids,
)
from tracker.web.routes import drama_research_api


@pytest.fixture(autouse=True)
def _fresh_research_throttle(monkeypatch):
    from tracker.web.throttle import RequestThrottle

    monkeypatch.setattr(
        drama_research_api,
        "research_create_throttle",
        RequestThrottle(max_requests=100, window=60),
    )


def _script(value: dict) -> str:
    return f"<script type=\"application/json\">{json.dumps(value)}</script>"


def _tiktok_detail() -> str:
    return _script(
        {
            "props": {
                "item": {
                    "id": "7200000000000000001",
                    "desc": "I Got Three Girlfriends From the System - Episode 3",
                    "createTime": "1760000000",
                    "author": {"uniqueId": "drama_matrix"},
                    "stats": {
                        "playCount": 123456,
                        "diggCount": 7890,
                        "commentCount": 321,
                        "shareCount": 45,
                    },
                }
            }
        }
    )


def _youtube_detail(
    title: str = "I Got Three Girlfriends From the System #3",
) -> str:
    player = {
        "videoDetails": {
            "videoId": "abcdefghijk",
            "title": title,
            "author": "Drama Channel",
            "viewCount": "98765",
        },
        "microformat": {
            "playerMicroformatRenderer": {"publishDate": "2026-11-01"}
        },
    }
    initial = {
        "contents": {
            "label": "like this video along with 12K other people",
            "comments": {
                "targetId": "comments-section",
                "continuationCommand": {"token": "comment-token"},
            },
        }
    }
    return (
        f"<script>var ytInitialPlayerResponse = {json.dumps(player)};</script>"
        f"<script>var ytInitialData = {json.dumps(initial)};</script>"
        '<script>var x = {"INNERTUBE_API_KEY":"key",'
        '"INNERTUBE_CLIENT_VERSION":"2.20261101.00.00"};</script>'
    )


def _youtube_login_required_detail() -> str:
    player = {
        "playabilityStatus": {
            "status": "LOGIN_REQUIRED",
            "reason": "Sign in to confirm you're not a bot",
        }
    }
    initial = {
        "contents": {
            "videoPrimaryInfoRenderer": {
                "title": {
                    "runs": [
                        {
                            "text": (
                                "I Got Three Girlfriends From the System "
                                "- Episode 3"
                            )
                        }
                    ]
                },
                "viewCount": {
                    "videoViewCountRenderer": {
                        "originalViewCount": "46049",
                        "viewCount": {"simpleText": "46,049 views"},
                    }
                },
                "dateText": {"simpleText": "Oct 19, 2026"},
            },
            "videoSecondaryInfoRenderer": {
                "owner": {
                    "videoOwnerRenderer": {
                        "title": {"runs": [{"text": "Kitty Motty"}]}
                    }
                }
            },
        },
        "engagementPanels": [{"likeCount": "219"}],
    }
    return (
        f"<script>var ytInitialPlayerResponse = {json.dumps(player)};</script>"
        f"<script>var ytInitialData = {json.dumps(initial)};</script>"
    )


def test_tiktok_and_youtube_search_parsers_extract_video_ids():
    tiktok_html = """
    <a href="https://www.tiktok.com/@creator/video/7200000000000000001">one</a>
    <script>{"videoId":"7200000000000000002"}</script>
    """
    assert tiktok_video_links(tiktok_html) == {
        "7200000000000000001": "https://www.tiktok.com/@creator/video/7200000000000000001",
        "7200000000000000002": "https://www.tiktok.com/video/7200000000000000002",
    }

    youtube_html = """
    <a href="https://www.youtube.com/shorts/abcdefghijk?feature=share">one</a>
    <a href="/url?q=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dlmnopqrstuv">two</a>
    """
    assert youtube_video_ids(youtube_html) == ["abcdefghijk", "lmnopqrstuv"]
    assert youtube_page_is_explicitly_empty("Your search did not match any documents.")
    assert youtube_page_is_explicitly_empty("No results found")
    assert (
        youtube_search_url("Three Girlfriends From the System", "US")
        == "https://www.youtube.com/results?search_query="
        "%22Three%20Girlfriends%20From%20the%20System%22"
        "&hl=en"
    )
    assert matches_aliases(
        ["Three Girlfriends From the System #3"],
        ["Three Girlfriends From the System"],
    )
    assert not matches_aliases(["An unrelated drama"], ["Three Girlfriends From the System"])
    assert tiktok_page_has_server_error("Something went wrong. Try again.")
    assert not tiktok_page_has_server_error(
        '<script>{"error":"Something went wrong"}</script><main>Videos</main>'
    )
    assert not tiktok_page_has_server_error("No results found")


def test_video_pages_yield_all_public_counters_that_are_exposed():
    tiktok = parse_tiktok_video(_tiktok_detail(), "7200000000000000001")
    assert tiktok is not None
    assert (tiktok.views, tiktok.likes, tiktok.comments, tiktok.shares) == (
        123456,
        7890,
        321,
        45,
    )
    assert tiktok.creator == "drama_matrix"
    assert tiktok.published_at.startswith("2025-10-09")

    youtube = parse_youtube_video(_youtube_detail())
    assert youtube is not None
    assert youtube.video_id == "abcdefghijk"
    assert youtube.creator == "Drama Channel"
    assert (youtube.views, youtube.likes) == (98765, 12_000)
    assert youtube.published_at.startswith("2026-11-01")

    fallback = parse_youtube_video(
        _youtube_login_required_detail(),
        "abcdefghijk",
    )
    assert fallback is not None
    assert fallback.video_id == "abcdefghijk"
    assert fallback.title.endswith("Episode 3")
    assert fallback.creator == "Kitty Motty"
    assert (fallback.views, fallback.likes) == (46049, 219)
    assert fallback.published_at.startswith("2026-10-19")

    continuation = youtube_comment_continuation(_youtube_detail())
    assert continuation is not None
    assert continuation.token == "comment-token"
    payload = {
        "onResponseReceivedEndpoints": [
            {
                "commentsHeaderRenderer": {
                    "countText": {"simpleText": "1,234 Comments"}
                }
            }
        ]
    }
    assert parse_youtube_comment_count(payload) == 1234


class _FakeReader:
    def __init__(self):
        self.urls: list[str] = []
        self.tiktok_prepared = False

    async def prepare_tiktok(self):
        self.tiktok_prepared = True

    async def fetch(self, url: str, **_kwargs):
        self.urls.append(url)
        if url.startswith("https://www.tiktok.com/search"):
            html = (
                '<a href="https://www.tiktok.com/@drama/video/'
                '7200000000000000001">result</a>'
            )
        elif url == "https://www.tiktok.com/@drama/video/7200000000000000001":
            html = _tiktok_detail()
        elif url.startswith("https://www.youtube.com/results"):
            html = '<a href="https://www.youtube.com/shorts/abcdefghijk">result</a>'
        elif url == "https://www.youtube.com/watch?v=abcdefghijk":
            html = _youtube_detail()
        else:
            raise AssertionError(url)
        return drama_collector.PageSnapshot(html=html, final_url=url, status=200)

    async def youtube_comment_count(self, _html: str, _region: str) -> int:
        return 7


def test_collector_returns_aggregates_and_traceable_sources():
    reader = _FakeReader()
    result = asyncio.run(
        drama_collector.collect_research(
            "I Got Three Girlfriends From the System",
            [],
            "US",
            reader=reader,
        )
    )
    assert reader.tiktok_prepared is True
    assert result["status"] == "completed"
    assert result["summary"]["tiktok"]["video_count"] == 1
    assert result["summary"]["tiktok"]["views"] == 123456
    assert result["summary"]["tiktok"]["likes"] == 7890
    assert result["summary"]["tiktok"]["comments"] == 321
    assert result["summary"]["google"]["video_count"] == 1
    assert result["summary"]["google"]["views"] == 98765
    assert result["summary"]["google"]["comments"] == 7
    assert result["summary"]["google"]["earliest_published_at"].startswith("2026-11-01")
    assert result["summary"]["combined"]["views"] == 222221
    assert {item["platform"] for item in result["sources"]} == {"tiktok", "google"}
    assert result["summary"]["google"]["discovery"] == "youtube_search"
    assert result["summary"]["google"]["content_platform"] == "youtube"
    assert any(url.startswith("https://www.youtube.com/results") for url in reader.urls)
    assert not any("google.com/search" in url for url in reader.urls)
    assert result["collection"]["limits"]["serial_per_domain"] is True


class _EmptyReader:
    async def fetch(self, url: str, **_kwargs):
        html = (
            "No results found"
            if url.startswith("https://www.tiktok.com")
            else "Your search did not match any documents."
        )
        return drama_collector.PageSnapshot(html=html, final_url=url, status=200)

    async def youtube_comment_count(self, _html: str, _region: str):
        return None


def test_collector_distinguishes_known_empty_from_failure():
    result = asyncio.run(
        drama_collector.collect_research("Unknown Drama", [], "US", reader=_EmptyReader())
    )
    assert result["status"] == "no_results"
    assert result["summary"]["combined"]["video_count"] == 0
    assert result["sources"] == []


class _FilteredYouTubeReader:
    def __init__(self):
        self.comment_calls = 0

    async def fetch(self, url: str, **_kwargs):
        if url.startswith("https://www.tiktok.com"):
            html = "No results found"
        elif url.startswith("https://www.youtube.com/results"):
            html = '<a href="https://www.youtube.com/shorts/abcdefghijk">result</a>'
        elif url == "https://www.youtube.com/watch?v=abcdefghijk":
            html = _youtube_detail("Unrelated Drama")
        else:
            raise AssertionError(url)
        return drama_collector.PageSnapshot(html=html, final_url=url, status=200)

    async def youtube_comment_count(self, _html: str, _region: str):
        self.comment_calls += 1
        return None


def test_collector_filters_youtube_results_without_a_title_match():
    phases: list[str] = []
    reader = _FilteredYouTubeReader()
    result = asyncio.run(
        drama_collector.collect_research(
            "I Got Three Girlfriends From the System",
            [],
            "US",
            reader=reader,
            on_phase=phases.append,
        )
    )
    assert "youtube_discovery" in phases
    assert result["status"] == "no_results"
    assert result["summary"]["google"]["status"] == "empty"
    assert result["summary"]["google"]["video_count"] == 0
    assert result["sources"] == []
    assert reader.comment_calls == 0
    assert any(
        search["platform"] == "google" and search["status"] == "ok"
        for search in result["collection"]["searches"]
    )


class _LoginRequiredYouTubeReader:
    async def fetch(self, url: str, **_kwargs):
        if url.startswith("https://www.tiktok.com"):
            html = "No results found"
        elif url.startswith("https://www.youtube.com/results"):
            html = '<a href="https://www.youtube.com/shorts/abcdefghijk">result</a>'
        elif url == "https://www.youtube.com/watch?v=abcdefghijk":
            html = _youtube_login_required_detail()
        else:
            raise AssertionError(url)
        return drama_collector.PageSnapshot(html=html, final_url=url, status=200)

    async def youtube_comment_count(self, _html: str, _region: str):
        return None


def test_collector_uses_public_initial_data_when_youtube_requires_login():
    result = asyncio.run(
        drama_collector.collect_research(
            "I Got Three Girlfriends From the System",
            [],
            "US",
            reader=_LoginRequiredYouTubeReader(),
        )
    )
    assert result["summary"]["google"]["video_count"] == 1
    assert result["summary"]["google"]["views"] == 46049
    assert result["summary"]["google"]["likes"] == 219
    assert result["sources"][0]["video_id"] == "abcdefghijk"
    assert result["sources"][0]["creator"] == "Kitty Motty"


class _TikTokSearchErrorReader:
    def __init__(self):
        self.fetch_calls: list[tuple[str, dict]] = []

    async def fetch(self, url: str, **_kwargs):
        self.fetch_calls.append((url, _kwargs))
        if url.startswith("https://www.tiktok.com/search"):
            html = "Something went wrong with the server"
        elif url.startswith("https://www.tiktok.com/discover"):
            html = (
                '<a href="https://www.tiktok.com/@drama/video/'
                '7200000000000000001">result</a>'
            )
        elif url == "https://www.tiktok.com/@drama/video/7200000000000000001":
            html = _tiktok_detail()
        elif url.startswith("https://www.youtube.com/results"):
            html = "Your search did not match any documents."
        else:
            raise AssertionError(url)
        return drama_collector.PageSnapshot(html=html, final_url=url, status=200)

    async def youtube_comment_count(self, _html: str, _region: str):
        return None


def test_tiktok_search_error_falls_back_to_discover():
    reader = _TikTokSearchErrorReader()
    result = asyncio.run(
        drama_collector.collect_research(
            "I Got Three Girlfriends From the System",
            [],
            "US",
            reader=reader,
        )
    )
    assert result["status"] == "partial"
    assert result["summary"]["tiktok"]["status"] == "complete"
    assert result["summary"]["tiktok"]["video_count"] == 1
    assert result["summary"]["tiktok"]["views"] == 123456
    assert any(
        search["platform"] == "tiktok" and search["status"] == "ok"
        for search in result["collection"]["searches"]
    )
    search_call = next(
        call for call in reader.fetch_calls if call[0].startswith(
            "https://www.tiktok.com/search"
        )
    )
    discover_call = next(
        call for call in reader.fetch_calls if call[0].startswith(
            "https://www.tiktok.com/discover"
        )
    )
    detail_call = next(
        call for call in reader.fetch_calls if "/video/" in call[0]
    )
    assert search_call[1]["settle_ms"] == 5_000
    assert discover_call[1]["settle_ms"] == 10_000
    assert detail_call[1]["settle_ms"] == 5_000


def test_api_validates_aliases_and_returns_polling_contract():
    app = FastAPI()
    app.include_router(drama_research_api.router)
    client = TestClient(app)

    class Result:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return self.value

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, sql, _args=()):
            if "INSERT INTO drama_research_subjects AS subject" in sql:
                return Result({"id": uuid.uuid4()})
            if "SELECT id,status,phase FROM drama_research_jobs" in sql:
                return Result(None)
            if "count(*) AS n" in sql:
                return Result({"n": 0})
            return Result(None)

    original_connect = drama_research_api.db.connect
    drama_research_api.db.connect = lambda: Conn()
    try:
        response = client.post(
            "/api/v1/drama-research",
            json={
                "title": "  Example   Drama ",
                "aliases": ["Other Name", "other name", "Example Drama"],
                "region": "us",
            },
        )
    finally:
        drama_research_api.db.connect = original_connect
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    assert "/api/ads/summary?status=all&q=Example%20Drama" in payload["status_url"]
    assert "drama_region" not in payload["status_url"]
    assert payload["job_id"] not in payload["status_url"]

    invalid = client.post(
        "/api/v1/drama-research",
        json={"title": "...", "aliases": [], "region": "US"},
    )
    assert invalid.status_code == 422


def test_api_persists_and_reuses_an_active_job(db):
    from tracker.web.app import app as web_app

    client = TestClient(web_app)
    body = {
        "title": "Example Drama",
        "aliases": ["Other Name", "example drama", "Other Name"],
        "region": "us",
    }

    first = client.post("/api/v1/drama-research", json=body)
    second = client.post("/api/v1/drama-research", json=body)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    anonymous_job = db.execute(
        "SELECT key_hash FROM drama_research_jobs WHERE id=%s",
        (first.json()["job_id"],),
    ).fetchone()
    assert anonymous_job["key_hash"] is None

    status = client.get(
        f"/api/v1/drama-research/{first.json()['job_id']}",
    )
    assert status.status_code == 200
    payload = status.json()
    assert payload["status"] == "queued"
    assert payload["query"] == {
        "title": "Example Drama",
        "aliases": ["Other Name"],
        "region": "US",
    }

    queued_summary = client.get(
        "/api/ads/summary",
        params={"status": "all", "q": "Example Drama"},
    )
    assert queued_summary.status_code == 200
    queued_drama = queued_summary.json()["drama_research"]
    assert queued_drama["status"] == "queued"
    assert queued_drama["summary"]["combined"]["video_count"] == 0

    job_id = first.json()["job_id"]
    db.execute(
        """UPDATE drama_research_jobs
              SET status='running',phase='youtube_videos',started_at=now()
            WHERE id=%s""",
        (job_id,),
    )
    db.commit()
    running_reuse = client.post("/api/v1/drama-research", json=body)
    assert running_reuse.status_code == 202
    assert running_reuse.json()["job_id"] == job_id
    assert running_reuse.json()["status"] == "running"
    running_summary = client.get(
        "/api/ads/summary",
        params={"status": "all", "q": "Example Drama"},
    ).json()["drama_research"]
    assert running_summary["status"] == "running"

    drama_research_db.finish(
        uuid.UUID(job_id),
        {
            "status": "completed",
            "summary": {},
            "creators": {},
            "sources": [
                {
                    "platform": "tiktok",
                    "content_platform": "tiktok",
                    "video_id": "7200000000000000001",
                    "url": "https://www.tiktok.com/video/7200000000000000001",
                    "title": "Episode 1",
                    "creator": "account-one",
                    "published_at": "2026-11-01T00:00:00+00:00",
                    "views": 1200,
                    "likes": 100,
                    "comments": 20,
                    "shares": 5,
                    "matched_aliases": ["Example Drama"],
                },
                {
                    "platform": "google",
                    "content_platform": "youtube",
                    "video_id": "abcdefghijk",
                    "url": "https://www.youtube.com/watch?v=abcdefghijk",
                    "title": "Episode 2",
                    "creator": "channel-two",
                    "published_at": "2026-11-02T00:00:00+00:00",
                    "views": 800,
                    "likes": 50,
                    "comments": 8,
                    "shares": None,
                    "matched_aliases": ["Other Name"],
                },
            ],
            "collection": {
                "region": "US",
                "captured_at": "2026-11-16T00:00:00+00:00",
                "searches": [{"platform": "tiktok"}],
                "warnings": [],
                "limits": {"max_videos_per_platform": 40},
            },
        },
    )

    result = client.get(
        "/api/ads/summary",
        params={"status": "all", "q": "Other Name"},
    )
    assert result.status_code == 200
    data = result.json()
    drama = data["drama_research"]
    assert drama["status"] == "completed"
    assert drama["summary"]["combined"]["video_count"] == 2
    assert drama["summary"]["combined"]["views"] == 2000
    assert drama["summary"]["tiktok"]["likes"] == 100
    assert "searches" not in drama["collection"]
    assert "query" not in drama
    assert "sources" not in drama
    assert "creators" not in drama

    requeued = client.post("/api/v1/drama-research", json=body)
    assert requeued.status_code == 202
    assert requeued.json()["job_id"] != job_id
    queued_again = client.get(
        "/api/ads/summary",
        params={"status": "all", "q": "Example Drama"},
    ).json()["drama_research"]
    assert queued_again["status"] == "queued"
    assert queued_again["summary"]["combined"]["video_count"] == 2
    assert queued_again["summary"]["combined"]["views"] == 2000

    db.execute(
        """UPDATE drama_research_jobs
              SET status='running',phase='youtube_videos',finished_at=NULL
            WHERE id=%s""",
        (job_id,),
    )
    db.commit()
    drama_research_db.finish(
        uuid.UUID(job_id),
        {
            "status": "completed",
            "sources": [
                {
                    "platform": "tiktok",
                    "content_platform": "tiktok",
                    "video_id": "7200000000000000001",
                    "url": "https://www.tiktok.com/video/7200000000000000001",
                    "title": "Episode 1",
                    "creator": "account-one",
                    "published_at": "2026-11-01T00:00:00+00:00",
                    "views": 900,
                    "likes": 80,
                    "comments": None,
                    "shares": 4,
                    "matched_aliases": ["Example Drama"],
                }
            ],
            "collection": {
                "region": "US",
                "captured_at": "2026-11-17T00:00:00+00:00",
                "warnings": ["later sample was smaller"],
                "limits": {"max_videos_per_platform": 40},
            },
        },
    )
    monotonic = client.get(
        "/api/ads/summary",
        params={"status": "all", "q": "Example Drama"},
    ).json()["drama_research"]
    assert monotonic["summary"]["combined"]["video_count"] == 2
    assert monotonic["summary"]["combined"]["views"] == 2000
    assert monotonic["summary"]["combined"]["likes"] == 150
    assert monotonic["summary"]["combined"]["comments"] == 28
    assert monotonic["summary"]["combined"]["shares"] == 5
    assert monotonic["collection"]["warnings"] == ["later sample was smaller"]

    detail = client.get(f"/api/v1/drama-research/{job_id}")
    assert detail.status_code == 200
    assert len(detail.json()["sources"]) == 1


def test_public_research_create_is_rate_limited(db, monkeypatch):
    from tracker.web.app import app as web_app
    from tracker.web.throttle import RequestThrottle

    monkeypatch.setattr(
        drama_research_api,
        "research_create_throttle",
        RequestThrottle(max_requests=2, window=60),
    )
    client = TestClient(web_app)
    for title in ("Rate Limit Drama One", "Rate Limit Drama Two"):
        response = client.post(
            "/api/v1/drama-research",
            json={"title": title, "aliases": [], "region": "US"},
        )
        assert response.status_code == 202

    limited = client.post(
        "/api/v1/drama-research",
        json={"title": "Rate Limit Drama Three", "aliases": [], "region": "US"},
    )
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0


def test_failed_platform_keeps_existing_summary_data(db):
    from tracker.web.app import app as web_app

    client = TestClient(web_app)
    body = {
        "title": "Persistent Drama",
        "aliases": ["Persistent Alias"],
        "region": "US",
    }
    created = client.post("/api/v1/drama-research", json=body)
    assert created.status_code == 202
    job_id = uuid.UUID(created.json()["job_id"])
    db.execute(
        """UPDATE drama_research_jobs
              SET status='running',phase='summarizing',started_at=now()
            WHERE id=%s""",
        (job_id,),
    )
    db.commit()
    drama_research_db.finish(
        job_id,
        {
            "status": "partial",
            "summary": {
                "tiktok": {"status": "partial"},
                "google": {"status": "failed"},
            },
            "sources": [
                {
                    "platform": "tiktok",
                    "content_platform": "tiktok",
                    "video_id": "7200000000000000009",
                    "url": "https://www.tiktok.com/video/7200000000000000009",
                    "title": "Episode 9",
                    "creator": "persistent-account",
                    "published_at": "2026-11-04T00:00:00+00:00",
                    "views": 9000,
                    "likes": 900,
                    "comments": 90,
                    "shares": 9,
                    "matched_aliases": ["Persistent Drama"],
                }
            ],
            "collection": {
                "region": "US",
                "captured_at": "2026-11-16T00:00:00+00:00",
                "warnings": ["Google blocked"],
                "limits": {"max_videos_per_platform": 40},
            },
        },
    )

    first = client.get(
        "/api/ads/summary",
        params={"status": "all", "q": "Persistent Drama"},
    ).json()["drama_research"]
    assert first["status"] == "partial"
    assert first["summary"]["tiktok"]["status"] == "partial"
    assert first["summary"]["google"]["status"] == "failed"
    assert first["summary"]["combined"]["views"] == 9000

    requeued = client.post("/api/v1/drama-research", json=body)
    assert requeued.status_code == 202
    failed_job_id = uuid.UUID(requeued.json()["job_id"])
    db.execute(
        """UPDATE drama_research_jobs
              SET status='running',phase='summarizing',started_at=now()
            WHERE id=%s""",
        (failed_job_id,),
    )
    db.commit()
    drama_research_db.finish(
        failed_job_id,
        {
            "status": "failed",
            "summary": {
                "tiktok": {"status": "failed"},
                "google": {"status": "failed"},
            },
            "sources": [],
            "collection": {
                "region": "US",
                "captured_at": "2026-11-17T00:00:00+00:00",
                "warnings": ["both platforms blocked"],
                "limits": {"max_videos_per_platform": 40},
            },
        },
    )

    second = client.get(
        "/api/ads/summary",
        params={"status": "all", "q": "Persistent Drama"},
    ).json()["drama_research"]
    assert second["status"] == "partial"
    assert second["summary"]["combined"]["views"] == 9000
    assert second["summary"]["tiktok"]["video_count"] == 1
    assert second["summary"]["tiktok"]["status"] == "partial"
    assert second["summary"]["google"]["status"] == "failed"
    assert second["collection"]["warnings"] == [
        "Google blocked",
        "both platforms blocked",
    ]


def test_summary_aggregates_all_regions_without_counting_a_video_twice(db):
    from tracker.web.app import app as web_app

    client = TestClient(web_app)
    jobs = {}
    for region in ("US", "GB"):
        response = client.post(
            "/api/v1/drama-research",
            json={
                "title": "Example Drama",
                "aliases": ["Other Name"],
                "region": region,
            },
        )
        assert response.status_code == 202
        jobs[region] = uuid.UUID(response.json()["job_id"])

    for job_id in jobs.values():
        db.execute(
            """UPDATE drama_research_jobs
                  SET status='running',phase='summarizing',started_at=now()
                WHERE id=%s""",
            (job_id,),
        )
    db.commit()

    drama_research_db.finish(
        jobs["US"],
        {
            "status": "completed",
            "sources": [
                {
                    "platform": "tiktok",
                    "content_platform": "tiktok",
                    "video_id": "7200000000000000001",
                    "url": "https://www.tiktok.com/video/7200000000000000001",
                    "title": "Episode 1",
                    "creator": "account-one",
                    "published_at": "2026-11-01T00:00:00+00:00",
                    "views": 1000,
                    "likes": 100,
                    "comments": 10,
                    "shares": 1,
                    "matched_aliases": ["Example Drama"],
                },
                {
                    "platform": "google",
                    "content_platform": "youtube",
                    "video_id": "abcdefghijk",
                    "url": "https://www.youtube.com/watch?v=abcdefghijk",
                    "title": "Episode 2",
                    "creator": "channel-two",
                    "published_at": "2026-11-02T00:00:00+00:00",
                    "views": 500,
                    "likes": 50,
                    "comments": 5,
                    "shares": None,
                    "matched_aliases": ["Example Drama"],
                },
            ],
            "collection": {
                "region": "US",
                "captured_at": "2026-11-16T00:00:00+00:00",
                "warnings": ["US warning"],
                "limits": {"max_videos_per_platform": 40},
            },
        },
    )
    drama_research_db.finish(
        jobs["GB"],
        {
            "status": "completed",
            "sources": [
                {
                    "platform": "tiktok",
                    "content_platform": "tiktok",
                    "video_id": "7200000000000000001",
                    "url": "https://www.tiktok.com/video/7200000000000000001",
                    "title": "Episode 1",
                    "creator": "account-one",
                    "published_at": "2026-11-01T00:00:00+00:00",
                    "views": 1500,
                    "likes": 120,
                    "comments": 12,
                    "shares": 2,
                    "matched_aliases": ["Other Name"],
                },
                {
                    "platform": "tiktok",
                    "content_platform": "tiktok",
                    "video_id": "7200000000000000002",
                    "url": "https://www.tiktok.com/video/7200000000000000002",
                    "title": "Episode 3",
                    "creator": "account-three",
                    "published_at": "2026-11-03T00:00:00+00:00",
                    "views": 2000,
                    "likes": 200,
                    "comments": 20,
                    "shares": 3,
                    "matched_aliases": ["Other Name"],
                },
            ],
            "collection": {
                "region": "GB",
                "captured_at": "2026-11-17T00:00:00+00:00",
                "warnings": ["GB warning"],
                "limits": {"max_videos_per_platform": 50},
            },
        },
    )

    response = client.get(
        "/api/ads/summary",
        params={"status": "all", "q": "Other Name"},
    )
    assert response.status_code == 200
    drama = response.json()["drama_research"]
    assert drama["status"] == "completed"
    assert drama["summary"]["combined"]["video_count"] == 3
    assert drama["summary"]["combined"]["views"] == 4000
    assert drama["summary"]["tiktok"]["video_count"] == 2
    assert drama["summary"]["tiktok"]["views"] == 3500
    assert drama["summary"]["google"]["video_count"] == 1
    assert drama["summary"]["google"]["views"] == 500
    assert drama["collection"]["regions"] == ["GB", "US"]
    assert drama["collection"]["region_count"] == 2
    assert drama["collection"]["captured_at"] == "2026-11-17T00:00:00+00:00"
    assert drama["collection"]["warnings"] == ["GB warning", "US warning"]
    assert drama["collection"]["limits"]["max_videos_per_platform"] == 50
    assert "query" not in drama
