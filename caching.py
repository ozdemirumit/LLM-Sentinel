"""
Semantic caching for LLM responses.

Cache key is SHA-256(messages_json + model + temperature).
Storage: Redis with TTL. Only non-streaming, within token limit.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import redis_client as rc
from config import settings
from logger import get_logger
from models import CacheStats, OAIChatResponse

log = get_logger(__name__)

# In-memory hit/miss counters (for metrics even without Redis)
_hit_count = 0
_miss_count = 0


def compute_cache_key(
    messages: list[dict[str, Any]],
    model: str,
    temperature: float | None = None,
) -> str:
    """Compute SHA-256 cache key from messages + model + temperature."""
    payload = json.dumps({
        "messages": messages,
        "model": model,
        "temperature": temperature,
    }, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


async def get_cached_response(cache_key: str) -> OAIChatResponse | None:
    """Look up cached response. Returns None on miss."""
    global _hit_count, _miss_count

    if not settings.CACHE_ENABLED:
        return None

    raw = await rc.get_value(f"cache:{cache_key}")
    if raw is None:
        _miss_count += 1
        return None

    try:
        data = json.loads(raw)
        _hit_count += 1
        log.debug("Cache hit", extra={"cache_key": cache_key[:12]})
        return OAIChatResponse.model_validate(data)
    except Exception:
        _miss_count += 1
        return None


async def set_cached_response(
    cache_key: str,
    response: OAIChatResponse,
    usage_tokens: int = 0,
) -> None:
    """Store response in cache if within token limit."""
    if not settings.CACHE_ENABLED:
        return

    if usage_tokens > settings.CACHE_MAX_TOKENS:
        return

    try:
        data = response.model_dump(mode="json")
        raw = json.dumps(data, default=str)
        await rc.set_with_ttl(
            f"cache:{cache_key}", raw, settings.CACHE_TTL_SECONDS
        )
    except Exception as exc:
        log.warning("Failed to cache response", extra={"error": str(exc)})


async def invalidate_cache(pattern: str = "*") -> int:
    """Delete cache entries matching pattern. Returns count deleted."""
    keys = await rc.get_all_keys(f"cache:{pattern}")
    count = 0
    for key in keys:
        if await rc.delete_key(key):
            count += 1
    if count:
        log.info("Cache invalidated", extra={"pattern": pattern, "deleted": count})
    return count


async def get_cache_stats() -> CacheStats:
    """Return cache statistics."""
    entries = 0
    try:
        keys = await rc.get_all_keys("cache:*")
        entries = len(keys)
    except Exception:
        pass

    total = _hit_count + _miss_count
    hit_rate = (_hit_count / total * 100) if total > 0 else 0.0

    return CacheStats(
        enabled=settings.CACHE_ENABLED,
        strategy=settings.CACHE_STRATEGY,
        hit_count=_hit_count,
        miss_count=_miss_count,
        hit_rate_percent=round(hit_rate, 2),
        entries_count=entries,
        ttl_seconds=settings.CACHE_TTL_SECONDS,
    )


def reset_cache_counters() -> None:
    """Reset hit/miss counters."""
    global _hit_count, _miss_count
    _hit_count = 0
    _miss_count = 0
