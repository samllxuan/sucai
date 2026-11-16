"""Unit and contract checks for anonymous TikTok/YouTube drama research."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tracker.db import drama_research as drama_research_db
from tracker.worker import drama_collector
from tracker.worker.drama_parsers import (
    google_page_is_explicitly_empty,
    parse_tiktok_video,
    parse_youtube_comment_count,
    parse_youtube_video,
    tiktok_video_links,
    youtube_comment_continuation,
    youtube_video_ids,
)
from tracker.web.routes import drama_research_api


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


def _youtube_detail() -> str:
    player = {
        "videoDetails": {
            "videoId": "abcdefghijk",
            "title": "Three Girlfriends From the System #3",
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


def test_tiktok_and_google_search_parsers_extract_video_ids():
    tiktok_html = """
    <a href="https://www.tiktok.com/@creator/video/7200000000000000001">one</a>
    <script>{"videoId":"7200000000000000002"}</script>
    """
    assert tiktok_video_links(tiktok_html) == {
        "7200000000000000001": "https://www.tiktok.com/@creator/video/7200000000000000001",
        "7200000000000000002": "https://www.tiktok.com/video/7200000000000000002",
    }

    google_html = """
    <a href="https://www.youtube.com/shorts/abcdefghijk?feature=share">one</a>
    <a href="/url?q=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dlmnopqrstuv">two</a>
    """
    assert youtube_video_ids(google_html) == ["abcdefghijk", "lmnopqrstuv"]
    assert google_page_is_explicitly_empty("Your search did not match any documents.")


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

    async def fetch(self, url: str, **_kwargs):
        self.urls.append(url)
        if url.startswith("https://www.tiktok.com/search"):
            html = (
                '<a href="https://www.tiktok.com/@drama/video/'
                '7200000000000000001">result</a>'
            )
        elif url == "https://www.tiktok.com/@drama/video/7200000000000000001":
            html = _tiktok_detail()
        elif url.startswith("https://www.google.com/search"):
            html = '<a href="https://www.youtube.com/shorts/abcdefghijk">result</a>'
        elif url == "https://www.youtube.com/watch?v=abcdefghijk":
            html = _youtube_detail()
        else:
            raise AssertionError(url)
        return drama_collector.PageSnapshot(html=html, final_url=url, status=200)

    async def youtube_comment_count(self, _html: str, _region: str) -> int:
        return 7


def test_collector_returns_aggregates_and_traceable_sources():
    result = asyncio.run(
        drama_collector.collect_research(
            "I Got Three Girlfriends From the System",
            [],
            "US",
            reader=_FakeReader(),
        )
    )
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


def test_api_validates_aliases_and_returns_polling_contract():
    app = FastAPI()
    app.include_router(drama_research_api.router)
    app.dependency_overrides[drama_research_api.authenticate] = lambda: "key-hash"
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
    token = "drama-research-test-key"
    key_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    db.execute(
        "INSERT INTO partner_api_keys(key_hash,label) VALUES(%s,%s)",
        (key_hash, "drama research test"),
    )
    db.commit()

    from tracker.web.app import app as web_app

    client = TestClient(web_app)
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "title": "Example Drama",
        "aliases": ["Other Name", "example drama", "Other Name"],
        "region": "us",
    }

    first = client.post("/api/v1/drama-research", json=body, headers=headers)
    second = client.post("/api/v1/drama-research", json=body, headers=headers)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]

    status = client.get(
        f"/api/v1/drama-research/{first.json()['job_id']}",
        headers=headers,
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
    running_reuse = client.post("/api/v1/drama-research", json=body, headers=headers)
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

    requeued = client.post("/api/v1/drama-research", json=body, headers=headers)
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

    detail = client.get(f"/api/v1/drama-research/{job_id}", headers=headers)
    assert detail.status_code == 200
    assert len(detail.json()["sources"]) == 1


def test_summary_aggregates_all_regions_without_counting_a_video_twice(db):
    token = "drama-research-multi-region-key"
    key_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    db.execute(
        "INSERT INTO partner_api_keys(key_hash,label) VALUES(%s,%s)",
        (key_hash, "drama research multi-region test"),
    )
    db.commit()

    from tracker.web.app import app as web_app

    client = TestClient(web_app)
    headers = {"Authorization": f"Bearer {token}"}
    jobs = {}
    for region in ("US", "GB"):
        response = client.post(
            "/api/v1/drama-research",
            json={
                "title": "Example Drama",
                "aliases": ["Other Name"],
                "region": region,
            },
            headers=headers,
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
