"""Durable state for public TikTok and Google/YouTube drama research."""

from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

from .base import connect


def title_key(value: str) -> str:
    return " ".join((value or "").split()).casefold()


def ensure_subject(
    conn,
    title: str,
    aliases: list[str],
    region: str,
) -> uuid.UUID:
    """Create or extend the durable title selected by summary queries."""
    row = conn.execute(
        """INSERT INTO drama_research_subjects AS subject
             (id,title,title_key,aliases,region,status)
           VALUES(%s,%s,%s,%s,%s,'queued')
           ON CONFLICT (title_key,region) DO UPDATE SET
             aliases=(
               SELECT COALESCE(jsonb_agg(DISTINCT alias_value), '[]'::jsonb)
                 FROM jsonb_array_elements_text(
                   subject.aliases || EXCLUDED.aliases
                 ) AS aliases(alias_value)
             ),
             updated_at=now()
           RETURNING id""",
        (uuid.uuid4(), title, title_key(title), Jsonb(aliases), region),
    ).fetchone()
    return row["id"]


def sync_active_state(conn, subject_id: uuid.UUID) -> dict | None:
    """Reflect the subject's active job without hiding accumulated results."""
    active = conn.execute(
        """SELECT status,phase
             FROM drama_research_jobs
            WHERE subject_id=%s AND status IN ('queued','running')
            ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,
                     created_at DESC,id
            LIMIT 1""",
        (subject_id,),
    ).fetchone()
    if active:
        conn.execute(
            """UPDATE drama_research_subjects
                  SET status=%s,phase=%s,updated_at=now()
                WHERE id=%s""",
            (active["status"], active["phase"], subject_id),
        )
    return active


def recover_interrupted() -> None:
    with connect() as conn:
        conn.execute(
            """UPDATE drama_research_jobs
                  SET status='failed', finished_at=now(), phase='interrupted',
                      error='采集服务重启，任务中断；请重新提交'
                WHERE status='running'"""
        )
        conn.execute(
            """UPDATE drama_research_subjects
                  SET status='failed', phase='interrupted', updated_at=now()
                WHERE status='running'"""
        )
        active = conn.execute(
            """SELECT DISTINCT subject_id
                 FROM drama_research_jobs
                WHERE status='queued'"""
        ).fetchall()
        for row in active:
            sync_active_state(conn, row["subject_id"])


def claim() -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """UPDATE drama_research_jobs
                  SET status='running', started_at=now(), phase='tiktok_discovery'
                WHERE id=(
                    SELECT id FROM drama_research_jobs
                     WHERE status='queued'
                     ORDER BY created_at,id
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                )
            RETURNING id,title,aliases,region,subject_id"""
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE drama_research_subjects
                      SET status='running', phase='tiktok_discovery', updated_at=now()
                    WHERE id=%s""",
                (row["subject_id"],),
            )
        return row


def set_phase(job_id, phase: str) -> None:
    with connect() as conn:
        conn.execute(
            """UPDATE drama_research_jobs
                  SET phase=%s
                WHERE id=%s AND status='running'""",
            (phase, job_id),
        )
        conn.execute(
            """UPDATE drama_research_subjects
                  SET phase=%s, updated_at=now()
                WHERE id=(
                  SELECT subject_id FROM drama_research_jobs WHERE id=%s
                )""",
            (phase, job_id),
        )


def _upsert_video(conn, subject_id, source: dict) -> None:
    conn.execute(
        """INSERT INTO drama_research_videos AS video
             (subject_id,platform,video_id,content_platform,url,title,creator,
              published_at,views,likes,comments,shares,matched_aliases)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (subject_id,platform,video_id) DO UPDATE SET
             content_platform=COALESCE(EXCLUDED.content_platform,video.content_platform),
             url=COALESCE(EXCLUDED.url,video.url),
             title=COALESCE(NULLIF(EXCLUDED.title,''),video.title),
             creator=COALESCE(NULLIF(EXCLUDED.creator,''),video.creator),
             published_at=COALESCE(video.published_at,EXCLUDED.published_at),
             views=GREATEST(video.views,EXCLUDED.views),
             likes=GREATEST(video.likes,EXCLUDED.likes),
             comments=GREATEST(video.comments,EXCLUDED.comments),
             shares=GREATEST(video.shares,EXCLUDED.shares),
             matched_aliases=(
               SELECT COALESCE(jsonb_agg(DISTINCT alias_value), '[]'::jsonb)
                 FROM jsonb_array_elements_text(
                   video.matched_aliases || EXCLUDED.matched_aliases
                 ) AS aliases(alias_value)
             ),
             updated_at=now()""",
        (
            subject_id,
            source["platform"],
            source["video_id"],
            source.get("content_platform"),
            source["url"],
            source.get("title"),
            source.get("creator"),
            source.get("published_at"),
            source.get("views"),
            source.get("likes"),
            source.get("comments"),
            source.get("shares"),
            Jsonb(source.get("matched_aliases") or []),
        ),
    )


def finish(job_id, result: dict) -> None:
    status = result.get("status")
    if status not in {"completed", "partial", "no_results"}:
        raise ValueError(f"invalid completed research status: {status}")
    with connect() as conn:
        job = conn.execute(
            """UPDATE drama_research_jobs
                  SET status=%s, phase='finished', result=%s, finished_at=now()
                WHERE id=%s AND status='running'
            RETURNING subject_id""",
            (status, Jsonb(result), job_id),
        ).fetchone()
        if not job or not job["subject_id"]:
            return
        for source in result.get("sources") or []:
            _upsert_video(conn, job["subject_id"], source)
        collection = result.get("collection") or {}
        compact_collection = {
            key: collection.get(key)
            for key in ("region", "captured_at", "warnings", "limits")
        }
        conn.execute(
            """UPDATE drama_research_subjects
                  SET status=%s, phase='finished', collection=%s, updated_at=now()
                WHERE id=%s""",
            (status, Jsonb(compact_collection), job["subject_id"]),
        )
        sync_active_state(conn, job["subject_id"])


def fail(job_id, error: str = "外部采集失败，请稍后重试") -> None:
    with connect() as conn:
        row = conn.execute(
            """UPDATE drama_research_jobs
                  SET status='failed', phase='failed', finished_at=now(), error=%s
                WHERE id=%s AND status='running'
            RETURNING subject_id""",
            (error, job_id),
        ).fetchone()
        if row and row["subject_id"]:
            conn.execute(
                """UPDATE drama_research_subjects
                      SET status='failed', phase='failed', updated_at=now()
                    WHERE id=%s""",
                (row["subject_id"],),
            )
            sync_active_state(conn, row["subject_id"])


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


def _dedupe_sources(rows) -> list[dict]:
    """Keep one row per public video and retain monotonic counters across regions."""
    unique: dict[tuple[str, str], dict] = {}
    for row in rows:
        source = dict(row)
        key = (source["platform"], source["video_id"])
        current = unique.get(key)
        if current is None:
            unique[key] = source
            continue

        newer = (
            source.get("updated_at") is not None
            and (
                current.get("updated_at") is None
                or source["updated_at"] > current["updated_at"]
            )
        )
        for field in ("content_platform", "url", "title", "creator"):
            if source.get(field) and (newer or not current.get(field)):
                current[field] = source[field]
        if source.get("published_at") and (
            not current.get("published_at")
            or source["published_at"] < current["published_at"]
        ):
            current["published_at"] = source["published_at"]
        for field in ("views", "likes", "comments", "shares"):
            values = [
                value
                for value in (current.get(field), source.get(field))
                if value is not None
            ]
            current[field] = max(values) if values else None
        current["matched_aliases"] = list(
            dict.fromkeys(
                (current.get("matched_aliases") or [])
                + (source.get("matched_aliases") or [])
            )
        )
        if newer:
            current["updated_at"] = source["updated_at"]
    return list(unique.values())


def _combined_state(subjects) -> tuple[str, str | None]:
    """Fold per-region subject states without hiding accumulated results."""
    for active_status in ("running", "queued"):
        active = next(
            (row for row in subjects if row["status"] == active_status),
            None,
        )
        if active:
            return active_status, active["phase"]

    statuses = {row["status"] for row in subjects}
    has_successful_region = bool(
        statuses.intersection({"completed", "partial", "no_results"})
    )
    if "partial" in statuses or (
        "failed" in statuses and has_successful_region
    ):
        return "partial", "finished"
    if "completed" in statuses:
        return "completed", "finished"
    if "failed" in statuses:
        return "failed", "failed"
    return "no_results", "finished"


def _merge_collections(subjects) -> dict:
    regions = sorted({row["region"] for row in subjects})
    captured_at = max(
        (
            row["collection"].get("captured_at")
            for row in subjects
            if row["collection"] and row["collection"].get("captured_at")
        ),
        default=None,
    )
    warnings: list = []
    limits: dict = {}
    for row in subjects:
        collection = row["collection"] or {}
        for warning in collection.get("warnings") or []:
            if warning not in warnings:
                warnings.append(warning)
        for key, value in (collection.get("limits") or {}).items():
            if isinstance(value, bool):
                limits[key] = bool(limits.get(key, True) and value)
            elif isinstance(value, (int, float)):
                limits[key] = max(value, limits.get(key, value))
            elif key not in limits:
                limits[key] = value
    return {
        "regions": regions,
        "region_count": len(regions),
        "captured_at": captured_at,
        "warnings": warnings,
        "limits": limits,
    }


def get_summary(query: str) -> dict | None:
    """Read one title group across every collected region, never by job id."""
    query = " ".join((query or "").split())
    if not query:
        return None
    with connect() as conn:
        selected = conn.execute(
            """SELECT subject.title_key,
                       CASE
                         WHEN subject.title_key=%s THEN 0
                         WHEN EXISTS (
                           SELECT 1
                             FROM jsonb_array_elements_text(subject.aliases) alias(value)
                            WHERE lower(btrim(alias.value))=lower(btrim(%s))
                         ) THEN 1
                         ELSE 2
                       END AS match_rank
                  FROM drama_research_subjects subject
                 WHERE (
                   subject.title_key=%s
                   OR EXISTS (
                     SELECT 1
                       FROM jsonb_array_elements_text(subject.aliases) alias(value)
                      WHERE lower(btrim(alias.value))=lower(btrim(%s))
                   )
                   OR strpos(lower(subject.title),lower(%s))>0
                   OR EXISTS (
                     SELECT 1
                       FROM jsonb_array_elements_text(subject.aliases) alias(value)
                      WHERE strpos(lower(alias.value),lower(%s))>0
                   )
                 )
                 ORDER BY match_rank,subject.updated_at DESC
                 LIMIT 1""",
            (
                title_key(query),
                query,
                title_key(query),
                query,
                query,
                query,
            ),
        ).fetchone()
        if not selected:
            return None
        subjects = conn.execute(
            """SELECT region,status,phase,collection,updated_at
                 FROM drama_research_subjects
                WHERE title_key=%s
                ORDER BY region""",
            (selected["title_key"],),
        ).fetchall()
        videos = conn.execute(
            """SELECT video.platform,video.content_platform,video.video_id,
                      video.url,video.title,video.creator,video.published_at,
                      video.views,video.likes,video.comments,video.shares,
                      video.matched_aliases,video.updated_at
                 FROM drama_research_videos video
                 JOIN drama_research_subjects subject
                   ON subject.id=video.subject_id
                WHERE subject.title_key=%s
                ORDER BY video.views DESC NULLS LAST,
                         video.likes DESC NULLS LAST,video.video_id""",
            (selected["title_key"],),
        ).fetchall()

    sources = _dedupe_sources(videos)
    tiktok = [row for row in sources if row["platform"] == "tiktok"]
    google = [row for row in sources if row["platform"] == "google"]
    tiktok_summary = _summary_for(tiktok)
    google_summary = _summary_for(google)
    tiktok_summary["status"] = "complete"
    google_summary["status"] = "complete"
    google_summary["content_platform"] = "youtube"
    google_summary["discovery"] = "google_short_videos"
    combined = _summary_for(sources)
    combined["platforms"] = sorted({row["platform"] for row in sources})
    status, phase = _combined_state(subjects)
    return {
        "status": status,
        "phase": phase,
        "summary": {
            "tiktok": tiktok_summary,
            "google": google_summary,
            "combined": combined,
        },
        "collection": _merge_collections(subjects),
        "updated_at": max(row["updated_at"] for row in subjects),
    }
