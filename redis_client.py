"""
Async Redis client with graceful in-memory fallback.

When REDIS_URL is not set or Redis is unreachable, all operations
transparently use an asyncio-safe in-memory store with TTL support.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# In-memory fallback store
# ---------------------------------------------------------------------------

class InMemoryStore:
    """Thread-safe in-memory key-value store with TTL support."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, float | None]] = {}  # key -> (value, expires_at)
        self._lock = threading.Lock()

    def _is_expired(self, key: str) -> bool:
        entry = self._data.get(key)
        if entry is None:
            return True
        _, expires_at = entry
        if expires_at is not None and time.time() > expires_at:
            del self._data[key]
            return True
        return False

    async def set_with_ttl(self, key: str, value: str, ttl_seconds: int) -> None:
        with self._lock:
            expires_at = time.time() + ttl_seconds if ttl_seconds > 0 else None
            self._data[key] = (value, expires_at)

    async def get_value(self, key: str) -> str | None:
        with self._lock:
            if self._is_expired(key):
                return None
            entry = self._data.get(key)
            return entry[0] if entry else None

    async def delete_key(self, key: str) -> bool:
        with self._lock:
            return self._data.pop(key, None) is not None

    async def increment(self, key: str, amount: int = 1) -> int:
        with self._lock:
            if self._is_expired(key):
                self._data[key] = (str(amount), None)
                return amount
            entry = self._data.get(key)
            if entry is None:
                self._data[key] = (str(amount), None)
                return amount
            new_val = int(entry[0]) + amount
            self._data[key] = (str(new_val), entry[1])
            return new_val

    async def get_all_keys(self, pattern: str) -> list[str]:
        import fnmatch
        with self._lock:
            # Cleanup expired
            now = time.time()
            expired = [
                k for k, (_, exp) in self._data.items()
                if exp is not None and now > exp
            ]
            for k in expired:
                del self._data[k]

            return [
                k for k in self._data
                if fnmatch.fnmatch(k, pattern)
            ]

    async def ttl_remaining(self, key: str) -> int | None:
        with self._lock:
            if self._is_expired(key):
                return None
            entry = self._data.get(key)
            if entry is None:
                return None
            _, expires_at = entry
            if expires_at is None:
                return -1  # No expiry
            remaining = int(expires_at - time.time())
            return max(remaining, 0)

    async def exists(self, key: str) -> bool:
        with self._lock:
            if self._is_expired(key):
                return False
            return key in self._data

    async def set_add(self, key: str, *members: str) -> int:
        """Add members to a set stored at key."""
        with self._lock:
            if self._is_expired(key):
                self._data[key] = (set(), None)
            entry = self._data.get(key)
            if entry is None:
                self._data[key] = (set(members), None)
                return len(members)
            current_set = entry[0]
            if not isinstance(current_set, set):
                current_set = set()
            added = len(members)
            current_set.update(members)
            self._data[key] = (current_set, entry[1])
            return added

    async def set_members(self, key: str) -> set[str]:
        """Get all members of a set."""
        with self._lock:
            if self._is_expired(key):
                return set()
            entry = self._data.get(key)
            if entry is None:
                return set()
            val = entry[0]
            if isinstance(val, set):
                return val.copy()
            return set()

    async def set_remove(self, key: str, *members: str) -> int:
        """Remove members from a set."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return 0
            current_set = entry[0]
            if not isinstance(current_set, set):
                return 0
            removed = 0
            for m in members:
                if m in current_set:
                    current_set.discard(m)
                    removed += 1
            self._data[key] = (current_set, entry[1])
            return removed

    async def set_count(self, key: str) -> int:
        """Return cardinality of a set."""
        members = await self.set_members(key)
        return len(members)

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        """Add members with scores to a sorted set."""
        with self._lock:
            if self._is_expired(key):
                self._data[key] = ({}, None)
            entry = self._data.get(key)
            if entry is None:
                self._data[key] = (dict(mapping), None)
                return len(mapping)
            zset = entry[0]
            if not isinstance(zset, dict):
                zset = {}
            added = 0
            for member, score in mapping.items():
                if member not in zset:
                    added += 1
                zset[member] = score
            self._data[key] = (zset, entry[1])
            return added

    async def zrangebyscore(
        self, key: str, min_score: float, max_score: float
    ) -> list[str]:
        """Return members with scores between min and max."""
        with self._lock:
            if self._is_expired(key):
                return []
            entry = self._data.get(key)
            if entry is None:
                return []
            zset = entry[0]
            if not isinstance(zset, dict):
                return []
            return [
                m for m, s in sorted(zset.items(), key=lambda x: x[1])
                if min_score <= s <= max_score
            ]

    async def zremrangebyscore(
        self, key: str, min_score: float, max_score: float
    ) -> int:
        """Remove members with scores between min and max."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return 0
            zset = entry[0]
            if not isinstance(zset, dict):
                return 0
            to_remove = [m for m, s in zset.items() if min_score <= s <= max_score]
            for m in to_remove:
                del zset[m]
            self._data[key] = (zset, entry[1])
            return len(to_remove)

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        """Set TTL on an existing key."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return False
            self._data[key] = (entry[0], time.time() + ttl_seconds)
            return True

    async def flushdb(self) -> None:
        """Clear all data."""
        with self._lock:
            self._data.clear()


# ---------------------------------------------------------------------------
# Redis wrapper that delegates to real Redis or InMemoryStore
# ---------------------------------------------------------------------------

class RedisClient:
    """
    Unified async Redis interface.
    Uses real Redis when available, InMemoryStore otherwise.
    """

    def __init__(self) -> None:
        self._redis: Any | None = None
        self._fallback = InMemoryStore()
        self._using_fallback = True
        self._connect_attempted = False

    async def connect(self) -> bool:
        """Attempt to connect to Redis. Returns True if connected."""
        from config import settings

        if not settings.REDIS_URL:
            log.info("REDIS_URL not set — using in-memory fallback")
            self._using_fallback = True
            self._connect_attempted = True
            return False

        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            await self._redis.ping()
            self._using_fallback = False
            self._connect_attempted = True
            log.info("Connected to Redis", extra={"url": settings.REDIS_URL.split("@")[-1]})
            return True
        except Exception as exc:
            log.warning(
                "Redis connection failed — using in-memory fallback",
                extra={"error": str(exc)},
            )
            self._redis = None
            self._using_fallback = True
            self._connect_attempted = True
            return False

    @property
    def is_connected(self) -> bool:
        return not self._using_fallback and self._redis is not None

    @property
    def store(self) -> Any:
        """Return the active store (Redis client or InMemoryStore)."""
        if self._using_fallback:
            return self._fallback
        return self._redis

    async def close(self) -> None:
        if self._redis:
            try:
                await self._redis.close()
            except Exception:
                pass
            self._redis = None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_client = RedisClient()


async def get_redis() -> RedisClient:
    """Return the Redis client singleton, connecting if needed."""
    if not _client._connect_attempted:
        await _client.connect()
    return _client


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

async def set_with_ttl(key: str, value: str, ttl_seconds: int) -> None:
    """Set a key with TTL."""
    client = await get_redis()
    if client.is_connected:
        await client.store.set(key, value, ex=ttl_seconds)
    else:
        await client.store.set_with_ttl(key, value, ttl_seconds)


async def get_value(key: str) -> str | None:
    """Get a value by key."""
    client = await get_redis()
    if client.is_connected:
        return await client.store.get(key)
    else:
        return await client.store.get_value(key)


async def delete_key(key: str) -> bool:
    """Delete a key."""
    client = await get_redis()
    if client.is_connected:
        result = await client.store.delete(key)
        return result > 0
    else:
        return await client.store.delete_key(key)


async def increment(key: str, amount: int = 1) -> int:
    """Increment a key's integer value."""
    client = await get_redis()
    if client.is_connected:
        return await client.store.incrby(key, amount)
    else:
        return await client.store.increment(key, amount)


async def get_all_keys(pattern: str) -> list[str]:
    """Get all keys matching a glob pattern."""
    client = await get_redis()
    if client.is_connected:
        keys = []
        async for key in client.store.scan_iter(match=pattern, count=1000):
            keys.append(key)
        return keys
    else:
        return await client.store.get_all_keys(pattern)


async def ttl_remaining(key: str) -> int | None:
    """Get remaining TTL for a key in seconds."""
    client = await get_redis()
    if client.is_connected:
        ttl = await client.store.ttl(key)
        return ttl if ttl >= 0 else None
    else:
        return await client.store.ttl_remaining(key)


async def exists(key: str) -> bool:
    """Check if a key exists."""
    client = await get_redis()
    if client.is_connected:
        return bool(await client.store.exists(key))
    else:
        return await client.store.exists(key)


async def set_add(key: str, *members: str) -> int:
    """Add members to a set."""
    client = await get_redis()
    if client.is_connected:
        return await client.store.sadd(key, *members)
    else:
        return await client.store.set_add(key, *members)


async def set_members(key: str) -> set[str]:
    """Get all members of a set."""
    client = await get_redis()
    if client.is_connected:
        return await client.store.smembers(key)
    else:
        return await client.store.set_members(key)


async def set_remove(key: str, *members: str) -> int:
    """Remove members from a set."""
    client = await get_redis()
    if client.is_connected:
        return await client.store.srem(key, *members)
    else:
        return await client.store.set_remove(key, *members)


async def set_count(key: str) -> int:
    """Return number of members in a set."""
    client = await get_redis()
    if client.is_connected:
        return await client.store.scard(key)
    else:
        return await client.store.set_count(key)


async def zadd(key: str, mapping: dict[str, float]) -> int:
    """Add members with scores to a sorted set."""
    client = await get_redis()
    if client.is_connected:
        return await client.store.zadd(key, mapping)
    else:
        return await client.store.zadd(key, mapping)


async def zrangebyscore(key: str, min_score: float, max_score: float) -> list[str]:
    """Return sorted set members with scores in range."""
    client = await get_redis()
    if client.is_connected:
        return await client.store.zrangebyscore(key, min_score, max_score)
    else:
        return await client.store.zrangebyscore(key, min_score, max_score)


async def zremrangebyscore(key: str, min_score: float, max_score: float) -> int:
    """Remove sorted set members with scores in range."""
    client = await get_redis()
    if client.is_connected:
        return await client.store.zremrangebyscore(key, min_score, max_score)
    else:
        return await client.store.zremrangebyscore(key, min_score, max_score)


async def expire(key: str, ttl_seconds: int) -> bool:
    """Set expiry on an existing key."""
    client = await get_redis()
    if client.is_connected:
        return await client.store.expire(key, ttl_seconds)
    else:
        return await client.store.expire(key, ttl_seconds)
