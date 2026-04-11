"""
Cost tracking — rate lookup, cost calculation, summary queries.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func

from db import CostRateDB, TokenUsage, get_db
from logger import get_logger
from models import CostRate, CostRateCreate, CostSummary

log = get_logger(__name__)

BUILTIN_RATES = [
    {"provider": "anthropic", "model": "claude-opus-4-6", "input": 0.015, "output": 0.075},
    {"provider": "anthropic", "model": "claude-sonnet-4-6", "input": 0.003, "output": 0.015},
    {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "input": 0.00025, "output": 0.00125},
    {"provider": "openai", "model": "gpt-4o", "input": 0.0025, "output": 0.01},
    {"provider": "openai", "model": "gpt-4o-mini", "input": 0.00015, "output": 0.0006},
    {"provider": "openai", "model": "gpt-4-turbo", "input": 0.01, "output": 0.03},
    {"provider": "openai", "model": "gpt-3.5-turbo", "input": 0.0005, "output": 0.0015},
    {"provider": "gemini", "model": "gemini-1.5-pro", "input": 0.00125, "output": 0.005},
    {"provider": "gemini", "model": "gemini-1.5-flash", "input": 0.000075, "output": 0.0003},
    {"provider": "ollama", "model": "*", "input": 0.0, "output": 0.0},
]


async def seed_cost_rates() -> int:
    count = 0
    async with get_db() as db:
        for r in BUILTIN_RATES:
            existing = await db.execute(
                select(CostRateDB).where(CostRateDB.provider == r["provider"], CostRateDB.model == r["model"])
            )
            if existing.scalars().first():
                continue
            db.add(CostRateDB(provider=r["provider"], model=r["model"],
                              input_cost_per_1k=r["input"], output_cost_per_1k=r["output"]))
            count += 1
    if count:
        log.info("Seeded built-in cost rates", extra={"count": count})
    return count


async def get_cost_rate(provider: str, model: str) -> CostRate | None:
    """Look up cost rate: exact match first, then wildcard '*'."""
    async with get_db() as db:
        result = await db.execute(
            select(CostRateDB).where(
                CostRateDB.provider == provider, CostRateDB.model == model, CostRateDB.is_active == True
            )
        )
        row = result.scalars().first()
        if not row:
            result = await db.execute(
                select(CostRateDB).where(
                    CostRateDB.provider == provider, CostRateDB.model == "*", CostRateDB.is_active == True
                )
            )
            row = result.scalars().first()
    if not row:
        return None
    return CostRate(id=row.id, provider=row.provider, model=row.model,
                    input_cost_per_1k=row.input_cost_per_1k, output_cost_per_1k=row.output_cost_per_1k,
                    currency=row.currency, effective_from=row.effective_from, is_active=row.is_active)


def calculate_cost(input_tokens: int, output_tokens: int, rate: CostRate | None) -> float | None:
    if rate is None:
        return None
    input_cost = (input_tokens / 1000.0) * rate.input_cost_per_1k
    output_cost = (output_tokens / 1000.0) * rate.output_cost_per_1k
    return round(input_cost + output_cost, 8)


async def update_token_usage_cost(usage_id: str, cost_usd: float) -> None:
    async with get_db() as db:
        result = await db.execute(select(TokenUsage).where(TokenUsage.id == usage_id))
        row = result.scalars().first()
        if row:
            row.cost_usd = cost_usd


async def get_cost_summary(
    client_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> CostSummary:
    async with get_db() as db:
        stmt = select(
            func.sum(TokenUsage.input_tokens),
            func.sum(TokenUsage.output_tokens),
            func.sum(TokenUsage.cost_usd),
            func.count(TokenUsage.id),
        )
        if client_id:
            stmt = stmt.where(TokenUsage.client_id == client_id)
        if provider:
            stmt = stmt.where(TokenUsage.provider == provider)
        if model:
            stmt = stmt.where(TokenUsage.model == model)
        if date_from:
            stmt = stmt.where(TokenUsage.date >= date_from.strftime("%Y-%m-%d"))
        if date_to:
            stmt = stmt.where(TokenUsage.date <= date_to.strftime("%Y-%m-%d"))
        result = await db.execute(stmt)
        row = result.one()

    return CostSummary(
        client_id=client_id, provider=provider, model=model,
        date_from=date_from, date_to=date_to,
        total_input_tokens=row[0] or 0,
        total_output_tokens=row[1] or 0,
        total_cost_usd=round(float(row[2] or 0), 6),
        request_count=row[3] or 0,
    )


async def get_all_rates() -> list[CostRate]:
    async with get_db() as db:
        result = await db.execute(select(CostRateDB).where(CostRateDB.is_active == True).order_by(CostRateDB.provider, CostRateDB.model))
        rows = result.scalars().all()
    return [CostRate(id=r.id, provider=r.provider, model=r.model,
                     input_cost_per_1k=r.input_cost_per_1k, output_cost_per_1k=r.output_cost_per_1k,
                     currency=r.currency, effective_from=r.effective_from, is_active=r.is_active) for r in rows]


async def create_rate(data: CostRateCreate) -> CostRate:
    async with get_db() as db:
        row = CostRateDB(provider=data.provider, model=data.model,
                         input_cost_per_1k=data.input_cost_per_1k, output_cost_per_1k=data.output_cost_per_1k)
        db.add(row)
        await db.flush()
        return CostRate(id=row.id, provider=row.provider, model=row.model,
                        input_cost_per_1k=row.input_cost_per_1k, output_cost_per_1k=row.output_cost_per_1k,
                        currency=row.currency, effective_from=row.effective_from, is_active=row.is_active)


async def deactivate_rate(rate_id: str) -> bool:
    async with get_db() as db:
        result = await db.execute(select(CostRateDB).where(CostRateDB.id == rate_id))
        row = result.scalars().first()
        if not row:
            return False
        row.is_active = False
    return True
