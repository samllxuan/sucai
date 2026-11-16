"""Worker entry point for one durable drama-research job."""

from __future__ import annotations

import asyncio
import logging

from ..db import drama_research
from .drama_collector import collect_research

logger = logging.getLogger(__name__)


def run(job: dict) -> None:
    """Collect one claimed job, persisting only a safe public error on failure."""
    try:
        result = asyncio.run(
            collect_research(
                job["title"],
                job["aliases"],
                job["region"],
                on_phase=lambda phase: drama_research.set_phase(job["id"], phase),
            )
        )
        drama_research.finish(job["id"], result)
    except Exception:
        logger.exception("drama research job %s failed", job["id"])
        drama_research.fail(job["id"])
