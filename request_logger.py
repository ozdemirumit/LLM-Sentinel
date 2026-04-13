"""
Request/response logging to the request_logs table.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, delete, func

from config import settings
from db import RequestLog, get_db
from logger import get_logger

log = get_logger(__name__)


async def log_request(
    db_session: Any | None = None,
    *,
    request_id: str,
    client_id: str | None,
    client_name: str | None,
    provider: str | None,
    model: str | None,
    alias_used: str | None = None,
    input_messages: list[dict] | None = None,
    output_content: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float | None = None,
    duration_ms: int = 0,
    status_code: int = 200,
    was_truncated: bool = False,
    masked_count: int = 0,
    cache_hit: bool = False,
) -> None:
    """Log a chat request/response to the request_logs table."""
    input_preview = None
    output_preview = None

    if settings.LOG_REQUEST_BODY:
        max_chars = settings.LOG_REQUEST_MAX_BODY_CHARS
        if input_messages:
            input_preview = json.dumps(input_messages, ensure_ascii=False, default=str)[:max_chars]
        if output_content:
            output_preview = str(output_content)[:max_chars]

    try:
        async with get_db() as db:
            entry = RequestLog(
                request_id=request_id,
                client_id=client_id,
                timestamp=datetime.now(timezone.utc),
                provider=provider,
                model=model,
                alias_used=alias_used,
                input_preview=input_preview,
                output_preview=output_preview,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                duration_ms=duration_ms,
                status_code=status_code,
                was_truncated=was_truncated,
                masked_count=masked_count,
                cache_hit=cache_hit,
            )
            db.add(entry)
    except Exception as exc:
        log.error("Failed to log request", extra={"error": str(exc), "request_id": request_id})


async def get_request_logs(
    limit: int = 100,
    client_id: str | None = None,
    provider: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[dict[str, Any]]:
    """Query request logs with optional filters."""
    async with get_db() as db:
        stmt = select(RequestLog).order_by(RequestLog.timestamp.desc())
        if client_id:
            stmt = stmt.where(RequestLog.client_id == client_id)
        if provider:
            stmt = stmt.where(RequestLog.provider == provider)
        if date_from:
            stmt = stmt.where(RequestLog.timestamp >= date_from)
        if date_to:
            stmt = stmt.where(RequestLog.timestamp <= date_to)
        stmt = stmt.limit(limit)
        result = await db.execute(stmt)
        rows = result.scalars().all()

    return [
        {
            "id": r.id, "request_id": r.request_id, "client_id": r.client_id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "provider": r.provider, "model": r.model, "alias_used": r.alias_used,
            "input_preview": r.input_preview, "output_preview": r.output_preview,
            "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
            "cost_usd": r.cost_usd, "duration_ms": r.duration_ms,
            "status_code": r.status_code, "was_truncated": r.was_truncated,
            "masked_count": r.masked_count,
            "cache_hit": r.cache_hit,
        }
        for r in rows
    ]


async def get_request_log_by_id(request_id: str) -> dict[str, Any] | None:
    async with get_db() as db:
        result = await db.execute(select(RequestLog).where(RequestLog.request_id == request_id))
        r = result.scalars().first()
        if not r:
            return None
        return {
            "id": r.id, "request_id": r.request_id, "client_id": r.client_id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "provider": r.provider, "model": r.model, "alias_used": r.alias_used,
            "input_preview": r.input_preview, "output_preview": r.output_preview,
            "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
            "cost_usd": r.cost_usd, "duration_ms": r.duration_ms,
            "status_code": r.status_code, "was_truncated": r.was_truncated,
            "masked_count": r.masked_count,
            "cache_hit": r.cache_hit,
        }


async def delete_old_request_logs(older_than_days: int) -> int:
    """Delete request logs older than N days. Returns count deleted."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    async with get_db() as db:
        count_stmt = select(func.count(RequestLog.id)).where(RequestLog.timestamp < cutoff)
        count_result = await db.execute(count_stmt)
        count = count_result.scalar() or 0
        if count > 0:
            await db.execute(delete(RequestLog).where(RequestLog.timestamp < cutoff))
    log.info("Deleted old request logs", extra={"count": count, "older_than_days": older_than_days})
    return count


async def toggle_request_logging(enabled: bool) -> None:
    """Toggle request body logging at runtime."""
    settings.LOG_REQUEST_BODY = enabled
    log.info("Request logging toggled", extra={"enabled": enabled})
