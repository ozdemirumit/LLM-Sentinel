"""
Webhook alerting system — fires alerts on events, records history.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx
from sqlalchemy import select

from config import settings
from db import AlertConfigDB, AlertHistoryDB, get_db
from logger import get_logger
from models import AlertConfig, AlertConfigCreate, AlertHistoryEntry

log = get_logger(__name__)


class AlertEventType(str, Enum):
    circuit_open = "circuit_open"
    quota_breach = "quota_breach"
    ip_ban = "ip_ban"
    tls_expiry_warning = "tls_expiry_warning"
    tls_expiry_critical = "tls_expiry_critical"
    key_error_threshold = "key_error_threshold"
    no_healthy_keys = "no_healthy_keys"
    system_start = "system_start"
    system_error = "system_error"
    backup_success = "backup_success"
    backup_failure = "backup_failure"
    high_cost_alert = "high_cost_alert"


class AlertSeverity(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


async def fire_alert(
    event_type: str | AlertEventType,
    severity: str | AlertSeverity = "warning",
    message: str = "",
    detail: str | None = None,
) -> None:
    """Fire an alert to all matching webhook configurations."""
    evt = str(event_type.value if isinstance(event_type, AlertEventType) else event_type)
    sev = str(severity.value if isinstance(severity, AlertSeverity) else severity)

    async with get_db() as db:
        result = await db.execute(
            select(AlertConfigDB).where(AlertConfigDB.is_active == True)
        )
        configs = result.scalars().all()

    for config in configs:
        # Filter by event type (if config specifies one; empty means all)
        if config.event_type and config.event_type != evt and config.event_type != "*":
            continue
        # Filter by severity
        config_sev = _SEVERITY_ORDER.get(config.min_severity, 1)
        alert_sev = _SEVERITY_ORDER.get(sev, 1)
        if alert_sev < config_sev:
            continue

        await _send_webhook(config, evt, sev, message, detail)


async def _send_webhook(
    config: AlertConfigDB,
    event_type: str,
    severity: str,
    message: str,
    detail: str | None,
) -> None:
    """Send a single webhook notification."""
    payload = {
        "event": event_type,
        "severity": severity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "detail": detail,
        "proxy_id": "llm-sentinel",
    }

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.WEBHOOK_SECRET:
        body_bytes = json.dumps(payload, sort_keys=True).encode()
        sig = hmac.new(settings.WEBHOOK_SECRET.encode(), body_bytes, hashlib.sha256).hexdigest()
        headers["X-Proxy-Signature"] = sig

    success = False
    error_message = None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(config.webhook_url, json=payload, headers=headers)
            success = 200 <= resp.status_code < 300
            if not success:
                error_message = f"HTTP {resp.status_code}"
    except Exception as exc:
        error_message = str(exc)
        log.warning("Webhook send failed", extra={"url": config.webhook_url, "error": error_message})

    # Record in history
    try:
        async with get_db() as db:
            db.add(AlertHistoryDB(
                config_id=config.id, event_type=event_type,
                severity=severity, message=message,
                success=success, error_message=error_message,
            ))
    except Exception as exc:
        log.error("Failed to record alert history", extra={"error": str(exc)})


async def test_webhook(config_id: str) -> dict[str, Any]:
    """Send a test ping to a webhook configuration."""
    async with get_db() as db:
        result = await db.execute(select(AlertConfigDB).where(AlertConfigDB.id == config_id))
        config = result.scalars().first()
    if not config:
        return {"success": False, "error": "Config not found"}

    await _send_webhook(config, "test_ping", "info", "Test ping from LLM Sentinel", None)
    return {"success": True, "webhook_url": config.webhook_url}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def get_all_configs() -> list[AlertConfig]:
    async with get_db() as db:
        result = await db.execute(select(AlertConfigDB).order_by(AlertConfigDB.created_at.desc()))
        rows = result.scalars().all()
    return [AlertConfig(id=r.id, event_type=r.event_type, webhook_url=r.webhook_url,
                        description=r.description, is_active=r.is_active,
                        min_severity=r.min_severity, created_at=r.created_at) for r in rows]


async def create_config(data: AlertConfigCreate) -> AlertConfig:
    async with get_db() as db:
        row = AlertConfigDB(event_type=data.event_type, webhook_url=data.webhook_url,
                            description=data.description, min_severity=data.min_severity)
        db.add(row)
        await db.flush()
        return AlertConfig(id=row.id, event_type=row.event_type, webhook_url=row.webhook_url,
                           description=row.description, is_active=row.is_active,
                           min_severity=row.min_severity, created_at=row.created_at)


async def update_config(config_id: str, **fields: Any) -> AlertConfig | None:
    async with get_db() as db:
        result = await db.execute(select(AlertConfigDB).where(AlertConfigDB.id == config_id))
        row = result.scalars().first()
        if not row:
            return None
        for k, v in fields.items():
            if v is not None and hasattr(row, k):
                setattr(row, k, v)
        await db.flush()
        return AlertConfig(id=row.id, event_type=row.event_type, webhook_url=row.webhook_url,
                           description=row.description, is_active=row.is_active,
                           min_severity=row.min_severity, created_at=row.created_at)


async def delete_config(config_id: str) -> bool:
    async with get_db() as db:
        result = await db.execute(select(AlertConfigDB).where(AlertConfigDB.id == config_id))
        row = result.scalars().first()
        if not row:
            return False
        await db.delete(row)
    return True


async def get_alert_history(limit: int = 50, config_id: str | None = None) -> list[AlertHistoryEntry]:
    async with get_db() as db:
        stmt = select(AlertHistoryDB).order_by(AlertHistoryDB.sent_at.desc())
        if config_id:
            stmt = stmt.where(AlertHistoryDB.config_id == config_id)
        stmt = stmt.limit(limit)
        result = await db.execute(stmt)
        rows = result.scalars().all()
    return [AlertHistoryEntry(id=r.id, config_id=r.config_id, event_type=r.event_type,
                              severity=r.severity, message=r.message, sent_at=r.sent_at,
                              success=r.success, error_message=r.error_message) for r in rows]
