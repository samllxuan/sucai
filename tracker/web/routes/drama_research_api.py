"""Async partner API for TikTok and Google/YouTube drama research."""

from __future__ import annotations

import re
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from ... import db
from ...db import drama_research as drama_research_db
from ..api_auth import authenticate

router = APIRouter(prefix="/api/v1", tags=["drama-research"])

MAX_ACTIVE_JOBS = 20

_JOB_COLUMNS = """id,title,aliases,region,status,phase,result,error,
                  created_at,started_at,finished_at"""


class ResearchBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list)
    region: str = Field(default="US", min_length=2, max_length=2)


def _clean_request(body: ResearchBody) -> tuple[str, list[str], str]:
    title = " ".join(body.title.split())
    if not title or not re.search(r"\w", title, flags=re.UNICODE):
        raise HTTPException(422, "title must contain letters or numbers")

    aliases: list[str] = []
    seen: set[str] = set()
    for raw in body.aliases:
        alias = " ".join((raw or "").split())
        if not alias:
            raise HTTPException(422, "aliases must not contain empty values")
        if len(alias) > 100:
            raise HTTPException(422, "each alias must be at most 100 characters")
        folded = alias.casefold()
        if folded != title.casefold() and folded not in seen:
            aliases.append(alias)
            seen.add(folded)
    if len(aliases) > 10:
        raise HTTPException(422, "at most 10 aliases are allowed")
    aliases.sort(key=str.casefold)

    region = body.region.upper()
    if not re.fullmatch(r"[A-Z]{2}", region):
        raise HTTPException(422, "region must be a two-letter country code")
    return title, aliases, region


@router.post("/drama-research", status_code=202)
def create_research(
    body: ResearchBody, request: Request, key: str = Depends(authenticate)
):
    title, aliases, region = _clean_request(body)
    aliases_json = Jsonb(aliases)

    with db.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (731960022,))
        subject_id = drama_research_db.ensure_subject(
            conn, title, aliases, region
        )
        previous = conn.execute(
            """SELECT id,status,phase FROM drama_research_jobs
                WHERE key_hash=%s AND lower(title)=lower(%s)
                  AND aliases=%s AND region=%s
                  AND status IN ('queued','running')
                ORDER BY created_at DESC LIMIT 1""",
            (key, title, aliases_json, region),
        ).fetchone()
        if previous:
            job_id = previous["id"]
            job_status = previous["status"]
        else:
            active = conn.execute(
                """SELECT count(*) AS n FROM drama_research_jobs
                    WHERE status IN ('queued','running')"""
            ).fetchone()["n"]
            if active >= MAX_ACTIVE_JOBS:
                raise HTTPException(429, "Research queue is full; retry later")
            job_id = uuid.uuid4()
            conn.execute(
                """INSERT INTO drama_research_jobs
                   (id,key_hash,title,aliases,region,status,subject_id)
                   VALUES(%s,%s,%s,%s,%s,'queued',%s)""",
                (job_id, key, title, aliases_json, region, subject_id),
            )
            job_status = "queued"
        drama_research_db.sync_active_state(conn, subject_id)

    base_url = str(request.base_url).rstrip("/")
    return {
        "job_id": str(job_id),
        "status": job_status,
        "status_url": (
            f"{base_url}/api/ads/summary?status=all"
            f"&q={quote(title, safe='')}"
        ),
    }


def get_job(job_id: uuid.UUID, key: str) -> dict | None:
    """Load one job inside the caller's API-key boundary."""
    with db.connect() as conn:
        return conn.execute(
            f"""SELECT {_JOB_COLUMNS}
                  FROM drama_research_jobs
                 WHERE id=%s AND key_hash=%s""",
            (job_id, key),
        ).fetchone()


def _base_payload(row: dict) -> dict:
    return {
        "job_id": str(row["id"]),
        "status": row["status"],
        "phase": row["phase"],
        "query": {
            "title": row["title"],
            "aliases": row["aliases"],
            "region": row["region"],
        },
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def full_payload(row: dict) -> dict:
    """Complete traceable result for the versioned detail endpoint."""
    payload = _base_payload(row)
    if row["result"]:
        payload.update(row["result"])
        payload["job_id"] = str(row["id"])
    if row["error"]:
        payload["error"] = row["error"]
    return payload


@router.get("/drama-research/{job_id}")
def research_status(job_id: uuid.UUID, key: str = Depends(authenticate)):
    row = get_job(job_id, key)
    if not row:
        raise HTTPException(404, "Research job not found")
    return full_payload(row)
