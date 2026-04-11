"""
Client CRUD operations + quota tracking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func

from auth import generate_api_key, hash_api_key
from config import settings
from cost_tracker import calculate_cost, get_cost_rate
from db import Client, TokenUsage, get_db
from logger import get_logger
from models import ClientCreate, ClientResponse, ClientUpdate, QuotaResponse

log = get_logger(__name__)


def _client_to_response(c: Client) -> ClientResponse:
    return ClientResponse(
        id=c.id, name=c.name,
        roles=c.roles if isinstance(c.roles, list) else [],
        permissions=c.permissions if isinstance(c.permissions, list) else ["*"],
        allowed_providers=c.allowed_providers if isinstance(c.allowed_providers, list) else [],
        allowed_models=c.allowed_models if isinstance(c.allowed_models, list) else [],
        rate_limit_per_minute=c.rate_limit_per_minute,
        token_limit_per_minute=c.token_limit_per_minute,
        daily_token_quota=c.daily_token_quota,
        max_concurrent_requests=c.max_concurrent_requests,
        require_signing=c.require_signing,
        priority=c.priority,
        cache_enabled=c.cache_enabled,
        ldap_group=c.ldap_group,
        is_active=c.is_active,
        created_at=c.created_at,
        last_used_at=c.last_used_at,
        description=c.description,
    )


async def get_all_clients() -> list[ClientResponse]:
    async with get_db() as db:
        result = await db.execute(select(Client).order_by(Client.created_at.desc()))
        rows = result.scalars().all()
    return [_client_to_response(r) for r in rows]


async def get_client_by_id(client_id: str) -> ClientResponse | None:
    async with get_db() as db:
        result = await db.execute(select(Client).where(Client.id == client_id))
        r = result.scalars().first()
    return _client_to_response(r) if r else None


async def get_client_by_key_hash(key_hash: str) -> Client | None:
    async with get_db() as db:
        result = await db.execute(select(Client).where(Client.api_key_hash == key_hash))
        return result.scalars().first()


async def create_client(data: ClientCreate) -> tuple[ClientResponse, str]:
    """Create a client. Returns (response, plaintext_api_key)."""
    plaintext = generate_api_key()
    key_hash = hash_api_key(plaintext)

    async with get_db() as db:
        client = Client(
            name=data.name,
            api_key_hash=key_hash,
            roles=data.roles,
            permissions=data.permissions,
            allowed_providers=data.allowed_providers,
            allowed_models=data.allowed_models,
            rate_limit_per_minute=data.rate_limit_per_minute,
            token_limit_per_minute=data.token_limit_per_minute,
            daily_token_quota=data.daily_token_quota,
            max_concurrent_requests=data.max_concurrent_requests,
            require_signing=data.require_signing,
            priority=data.priority,
            cache_enabled=data.cache_enabled,
            ldap_group=data.ldap_group,
            is_active=True,
            description=data.description,
        )
        db.add(client)
        await db.flush()
        resp = _client_to_response(client)

    log.info("Client created", extra={"client_name": data.name, "client_id": resp.id})
    return resp, plaintext


async def update_client(client_id: str, data: ClientUpdate) -> ClientResponse | None:
    async with get_db() as db:
        result = await db.execute(select(Client).where(Client.id == client_id))
        client = result.scalars().first()
        if not client:
            return None
        for field, val in data.model_dump(exclude_unset=True).items():
            if val is not None and hasattr(client, field):
                setattr(client, field, val)
        await db.flush()
        return _client_to_response(client)


async def delete_client(client_id: str) -> bool:
    async with get_db() as db:
        result = await db.execute(select(Client).where(Client.id == client_id))
        client = result.scalars().first()
        if not client:
            return False
        await db.delete(client)
    log.info("Client deleted", extra={"client_id": client_id})
    return True


async def regenerate_api_key(client_id: str) -> tuple[ClientResponse, str] | None:
    plaintext = generate_api_key()
    key_hash = hash_api_key(plaintext)
    async with get_db() as db:
        result = await db.execute(select(Client).where(Client.id == client_id))
        client = result.scalars().first()
        if not client:
            return None
        client.api_key_hash = key_hash
        await db.flush()
        resp = _client_to_response(client)
    log.info("API key regenerated", extra={"client_id": client_id})
    return resp, plaintext


async def get_quota_usage(client_id: str, date: str | None = None) -> QuotaResponse:
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with get_db() as db:
        result = await db.execute(
            select(func.sum(TokenUsage.input_tokens), func.sum(TokenUsage.output_tokens))
            .where(TokenUsage.client_id == client_id, TokenUsage.date == date)
        )
        row = result.one()
        inp = row[0] or 0
        outp = row[1] or 0

        # Get quota
        cr = await db.execute(select(Client.daily_token_quota).where(Client.id == client_id))
        quota_row = cr.scalar()
        quota = quota_row or 0

    percent = ((inp + outp) / quota * 100) if quota > 0 else 0.0
    return QuotaResponse(
        client_id=client_id, date=date,
        input_tokens=inp, output_tokens=outp,
        daily_quota=quota, percent_used=round(percent, 2),
    )


async def reset_quota(client_id: str, date: str | None = None) -> None:
    from sqlalchemy import delete as sql_delete
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with get_db() as db:
        await db.execute(
            sql_delete(TokenUsage).where(TokenUsage.client_id == client_id, TokenUsage.date == date)
        )
    log.info("Quota reset", extra={"client_id": client_id, "date": date})


async def record_token_usage(
    client_id: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> str:
    """Record token usage and calculate cost. Returns usage row id."""
    rate = await get_cost_rate(provider, model)
    cost = calculate_cost(input_tokens, output_tokens, rate)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async with get_db() as db:
        usage = TokenUsage(
            client_id=client_id, date=date_str,
            provider=provider, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost,
        )
        db.add(usage)
        await db.flush()
        return usage.id


async def touch_client(client_id: str) -> None:
    async with get_db() as db:
        result = await db.execute(select(Client).where(Client.id == client_id))
        client = result.scalars().first()
        if client:
            client.last_used_at = datetime.now(timezone.utc)
