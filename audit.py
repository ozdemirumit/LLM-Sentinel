"""
Audit logging — writes to DB (AuditLog table) and rotating audit log file.
"""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from db import AuditLog, get_db
from logger import get_logger, JSONFormatter

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Dedicated audit file logger
# ---------------------------------------------------------------------------

_audit_logger: logging.Logger | None = None


def _get_audit_logger() -> logging.Logger:
    global _audit_logger
    if _audit_logger is not None:
        return _audit_logger

    _audit_logger = logging.getLogger("audit_file")
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False

    audit_path = Path("data/audit.log")
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        audit_path, maxBytes=50 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    handler.setFormatter(JSONFormatter())
    _audit_logger.addHandler(handler)
    return _audit_logger


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

class AuditEventType:
    AUTH_SUCCESS = "AUTH_SUCCESS"
    AUTH_FAILURE = "AUTH_FAILURE"
    CONFIG_CHANGE = "CONFIG_CHANGE"
    KEY_REGEN = "KEY_REGEN"
    QUOTA_BREACH = "QUOTA_BREACH"
    CLIENT_CREATE = "CLIENT_CREATE"
    CLIENT_DELETE = "CLIENT_DELETE"
    IP_BAN = "IP_BAN"
    TLS_RELOAD = "TLS_RELOAD"
    ADMIN_ACTION = "ADMIN_ACTION"


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

async def log_audit(
    event_type: str,
    actor: str | None = None,
    target: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
    success: bool = True,
) -> None:
    """Write an audit log entry to the DB and audit file."""
    # DB
    try:
        async with get_db() as db:
            entry = AuditLog(
                event_type=event_type,
                actor=actor,
                target=target,
                detail=detail,
                ip=ip,
                success=success,
                timestamp=datetime.now(timezone.utc),
            )
            db.add(entry)
    except Exception as exc:
        log.error("Failed to write audit log to DB", extra={"error": str(exc)})

    # File
    try:
        audit = _get_audit_logger()
        audit.info(
            "Audit event",
            extra={
                "event_type": event_type,
                "actor": actor or "",
                "target": target or "",
                "detail": detail or "",
                "ip": ip or "",
                "success": success,
            },
        )
    except Exception:
        pass


async def get_audit_logs(
    limit: int = 100,
    event_type_filter: str | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query audit logs from DB."""
    async with get_db() as db:
        stmt = select(AuditLog).order_by(AuditLog.timestamp.desc())
        if event_type_filter:
            stmt = stmt.where(AuditLog.event_type == event_type_filter)
        stmt = stmt.offset(offset).limit(limit)
        result = await db.execute(stmt)
        rows = result.scalars().all()

    return [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "event_type": r.event_type,
            "actor": r.actor,
            "target": r.target,
            "detail": r.detail,
            "ip": r.ip,
            "success": r.success,
        }
        for r in rows
    ]
