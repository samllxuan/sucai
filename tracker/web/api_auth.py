"""Bearer authentication shared by versioned partner APIs."""

from __future__ import annotations

import hashlib

from fastapi import Header, HTTPException

from .. import db


def authenticate(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            401, "API key required", headers={"WWW-Authenticate": "Bearer"}
        )
    token = authorization[7:].strip()
    if not token or len(token) > 256:
        raise HTTPException(401, "Invalid API key")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with db.connect() as conn:
        key = conn.execute(
            "SELECT key_hash FROM partner_api_keys WHERE key_hash=%s AND revoked_at IS NULL",
            (digest,),
        ).fetchone()
    if not key:
        raise HTTPException(
            401, "Invalid API key", headers={"WWW-Authenticate": "Bearer"}
        )
    return digest
