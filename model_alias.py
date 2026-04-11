"""
Model alias CRUD + built-in alias seeding.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select

from db import ModelAliasDB, get_db
from logger import get_logger
from models import ModelAlias, ModelAliasCreate, ModelAliasUpdate

log = get_logger(__name__)

BUILTIN_ALIASES = [
    {"alias": "fast", "provider": "anthropic", "model": "claude-haiku-4-5-20251001", "description": "Fast Anthropic model"},
    {"alias": "smart", "provider": "anthropic", "model": "claude-sonnet-4-6", "description": "Smart Anthropic model"},
    {"alias": "powerful", "provider": "anthropic", "model": "claude-opus-4-6", "description": "Most powerful Anthropic model"},
    {"alias": "gpt-fast", "provider": "openai", "model": "gpt-4o-mini", "description": "Fast OpenAI model"},
    {"alias": "gpt-smart", "provider": "openai", "model": "gpt-4o", "description": "Smart OpenAI model"},
    {"alias": "gemini-fast", "provider": "gemini", "model": "gemini-1.5-flash", "description": "Fast Gemini model"},
    {"alias": "gemini-smart", "provider": "gemini", "model": "gemini-1.5-pro", "description": "Smart Gemini model"},
]


async def seed_builtin_aliases() -> int:
    count = 0
    async with get_db() as db:
        for a in BUILTIN_ALIASES:
            existing = await db.execute(select(ModelAliasDB).where(ModelAliasDB.alias == a["alias"]))
            if existing.scalars().first():
                continue
            db.add(ModelAliasDB(alias=a["alias"], provider=a["provider"], model=a["model"], description=a.get("description")))
            count += 1
    if count:
        log.info("Seeded built-in model aliases", extra={"count": count})
    return count


def _validate_slug(alias: str) -> None:
    if not re.match(r"^[a-z0-9][a-z0-9\-]*$", alias):
        raise ValueError(f"Alias must be a URL-safe slug (a-z, 0-9, hyphens): '{alias}'")


async def get_all_aliases(include_inactive: bool = False) -> list[ModelAlias]:
    async with get_db() as db:
        stmt = select(ModelAliasDB).order_by(ModelAliasDB.alias)
        if not include_inactive:
            stmt = stmt.where(ModelAliasDB.is_active == True)
        result = await db.execute(stmt)
        rows = result.scalars().all()
    return [ModelAlias(id=r.id, alias=r.alias, provider=r.provider, model=r.model,
                       description=r.description, is_active=r.is_active,
                       created_at=r.created_at, usage_count=r.usage_count) for r in rows]


async def get_alias(alias: str) -> ModelAlias | None:
    async with get_db() as db:
        result = await db.execute(select(ModelAliasDB).where(ModelAliasDB.alias == alias, ModelAliasDB.is_active == True))
        r = result.scalars().first()
    if not r:
        return None
    return ModelAlias(id=r.id, alias=r.alias, provider=r.provider, model=r.model,
                      description=r.description, is_active=r.is_active,
                      created_at=r.created_at, usage_count=r.usage_count)


async def create_alias(data: ModelAliasCreate) -> ModelAlias:
    _validate_slug(data.alias)
    async with get_db() as db:
        existing = await db.execute(select(ModelAliasDB).where(ModelAliasDB.alias == data.alias))
        if existing.scalars().first():
            raise ValueError(f"Alias '{data.alias}' already exists")
        row = ModelAliasDB(alias=data.alias, provider=data.provider, model=data.model, description=data.description)
        db.add(row)
        await db.flush()
        return ModelAlias(id=row.id, alias=row.alias, provider=row.provider, model=row.model,
                          description=row.description, is_active=row.is_active,
                          created_at=row.created_at, usage_count=row.usage_count)


async def update_alias(alias_id: str, data: ModelAliasUpdate) -> ModelAlias | None:
    async with get_db() as db:
        result = await db.execute(select(ModelAliasDB).where(ModelAliasDB.id == alias_id))
        row = result.scalars().first()
        if not row:
            return None
        if data.provider is not None:
            row.provider = data.provider
        if data.model is not None:
            row.model = data.model
        if data.description is not None:
            row.description = data.description
        if data.is_active is not None:
            row.is_active = data.is_active
        await db.flush()
        return ModelAlias(id=row.id, alias=row.alias, provider=row.provider, model=row.model,
                          description=row.description, is_active=row.is_active,
                          created_at=row.created_at, usage_count=row.usage_count)


async def delete_alias(alias_id: str) -> bool:
    async with get_db() as db:
        result = await db.execute(select(ModelAliasDB).where(ModelAliasDB.id == alias_id))
        row = result.scalars().first()
        if not row:
            return False
        await db.delete(row)
    return True


async def increment_usage(alias_id: str) -> None:
    async with get_db() as db:
        result = await db.execute(select(ModelAliasDB).where(ModelAliasDB.id == alias_id))
        row = result.scalars().first()
        if row:
            row.usage_count += 1
