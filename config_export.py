"""
Configuration export/import — serializes filter patterns, aliases,
cost rates, alert configs, provider configs, content policies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from db import (
    FilterPattern, ModelAliasDB, CostRateDB, AlertConfigDB,
    ProviderConfigDB, ContentPolicyDB, Client, get_db,
)
from logger import get_logger
from models import ConfigExportData, ConfigImportOptions, ConfigImportResult

log = get_logger(__name__)


def _sign(data: dict, secret: str) -> str:
    """HMAC-SHA256 sign export data."""
    payload = json.dumps(data, sort_keys=True, default=str).encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


async def export_config() -> ConfigExportData:
    """Export all configuration data."""
    from config import settings

    async with get_db() as db:
        fp_result = await db.execute(select(FilterPattern))
        alias_result = await db.execute(select(ModelAliasDB))
        rate_result = await db.execute(select(CostRateDB).where(CostRateDB.is_active == True))
        alert_result = await db.execute(select(AlertConfigDB))
        prov_result = await db.execute(select(ProviderConfigDB))
        policy_result = await db.execute(select(ContentPolicyDB))
        client_result = await db.execute(select(Client))

    def _row_dict(row: Any, exclude: set[str] | None = None) -> dict:
        exclude = exclude or set()
        d = {}
        for c in row.__table__.columns:
            if c.name in exclude:
                continue
            val = getattr(row, c.name if c.name != "metadata" else "metadata_")
            if isinstance(val, datetime):
                val = val.isoformat()
            d[c.name] = val
        return d

    data = ConfigExportData(
        exported_at=datetime.now(timezone.utc),
        filter_patterns=[_row_dict(r) for r in fp_result.scalars().all()],
        model_aliases=[_row_dict(r) for r in alias_result.scalars().all()],
        cost_rates=[_row_dict(r) for r in rate_result.scalars().all()],
        alert_configs=[_row_dict(r) for r in alert_result.scalars().all()],
        provider_configs=[_row_dict(r) for r in prov_result.scalars().all()],
        content_policies=[_row_dict(r) for r in policy_result.scalars().all()],
        clients=[_row_dict(r, exclude={"api_key_hash", "metadata"}) for r in client_result.scalars().all()],
        runtime_config={
            "log_request_body": settings.LOG_REQUEST_BODY,
            "context_truncation_enabled": settings.CONTEXT_TRUNCATION_ENABLED,
        },
    )

    if settings.KEY_ENCRYPTION_SECRET:
        signable = data.model_dump(mode="json", exclude={"signature"})
        data.signature = _sign(signable, settings.KEY_ENCRYPTION_SECRET)

    return data


def verify_signature(data: ConfigExportData) -> bool | None:
    """Verify export data signature. Returns None if no signature."""
    from config import settings

    if not data.signature:
        return None
    if not settings.KEY_ENCRYPTION_SECRET:
        return None

    signable = data.model_dump(mode="json", exclude={"signature"})
    expected = _sign(signable, settings.KEY_ENCRYPTION_SECRET)
    return hmac.compare_digest(expected, data.signature)


async def import_config(
    data: ConfigExportData,
    options: ConfigImportOptions,
) -> ConfigImportResult:
    """Import configuration data."""
    result = ConfigImportResult()

    # Verify signature
    if options.verify_signature and data.signature:
        sig_valid = verify_signature(data)
        result.signature_valid = sig_valid
        if sig_valid is False:
            result.errors.append("Invalid signature — data may have been tampered with")
            return result

    async with get_db() as db:
        # Filter patterns
        for fp in data.filter_patterns:
            existing = await db.execute(select(FilterPattern).where(FilterPattern.name == fp.get("name")))
            if existing.scalars().first():
                if not options.overwrite_existing:
                    result.skipped += 1
                    continue
            db.add(FilterPattern(
                name=fp.get("name", ""), pattern=fp.get("pattern", ""),
                replacement=fp.get("replacement", "[REDACTED]"),
                flags=fp.get("flags", "IGNORECASE"),
                is_active=fp.get("is_active", True),
                is_builtin=fp.get("is_builtin", False),
            ))
            result.imported_filter_patterns += 1

        # Aliases
        for a in data.model_aliases:
            existing = await db.execute(select(ModelAliasDB).where(ModelAliasDB.alias == a.get("alias")))
            if existing.scalars().first():
                if not options.overwrite_existing:
                    result.skipped += 1
                    continue
            db.add(ModelAliasDB(
                alias=a.get("alias", ""), provider=a.get("provider", ""),
                model=a.get("model", ""), description=a.get("description"),
                is_active=a.get("is_active", True),
            ))
            result.imported_aliases += 1

        # Cost rates
        for cr in data.cost_rates:
            existing = await db.execute(
                select(CostRateDB).where(CostRateDB.provider == cr.get("provider"), CostRateDB.model == cr.get("model"))
            )
            if existing.scalars().first():
                if not options.overwrite_existing:
                    result.skipped += 1
                    continue
            db.add(CostRateDB(
                provider=cr.get("provider", ""), model=cr.get("model", ""),
                input_cost_per_1k=cr.get("input_cost_per_1k", 0),
                output_cost_per_1k=cr.get("output_cost_per_1k", 0),
            ))
            result.imported_cost_rates += 1

        # Alert configs
        if options.import_alert_configs:
            for ac in data.alert_configs:
                existing = await db.execute(
                    select(AlertConfigDB).where(AlertConfigDB.webhook_url == ac.get("webhook_url"),
                                                 AlertConfigDB.event_type == ac.get("event_type"))
                )
                if existing.scalars().first():
                    if not options.overwrite_existing:
                        result.skipped += 1
                        continue
                db.add(AlertConfigDB(
                    event_type=ac.get("event_type", ""),
                    webhook_url=ac.get("webhook_url", ""),
                    description=ac.get("description"),
                    is_active=ac.get("is_active", True),
                    min_severity=ac.get("min_severity", "warning"),
                ))
                result.imported_alert_configs += 1

        # Clients (opt-in, created inactive, no api_key)
        if options.import_clients:
            from auth import generate_api_key, hash_api_key
            for c in data.clients:
                client_name = c.get("name", "")
                existing = await db.execute(select(Client).where(Client.name == client_name))
                if existing.scalars().first():
                    result.skipped += 1
                    continue
                temp_key = generate_api_key()
                db.add(Client(
                    name=client_name,
                    api_key_hash=hash_api_key(temp_key),
                    roles=c.get("roles", ["api_client"]),
                    permissions=c.get("permissions", ["*"]),
                    is_active=False,
                    description=c.get("description"),
                ))
                result.imported_clients += 1

    log.info("Config imported", extra={
        "patterns": result.imported_filter_patterns,
        "aliases": result.imported_aliases,
        "rates": result.imported_cost_rates,
        "alerts": result.imported_alert_configs,
        "clients": result.imported_clients,
        "skipped": result.skipped,
    })
    return result
