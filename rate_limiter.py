"""
Rate limiting — RPM (requests/minute) and TPM (tokens/minute).

Uses Redis sorted sets for sliding window counters.
Includes per-client and global concurrency semaphores.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis_client as rc
from config import settings
from logger import get_logger

log = get_logger(__name__)

# Window size in seconds
_WINDOW = 60


# ==========================================================================
# RPM (Request Per Minute) limiting
# ==========================================================================

async def _sliding_window_check(
    key: str, limit: int, window: int = _WINDOW
) -> tuple[bool, int]:
    """
    Sliding window counter using sorted set.
    Returns (allowed, retry_after_seconds).
    """
    now = time.time()
    window_start = now - window

    # Remove old entries
    await rc.zremrangebyscore(key, 0, window_start)

    # Count current entries
    members = await rc.zrangebyscore(key, window_start, now)
    current_count = len(members)

    if current_count >= limit:
        # Find when the oldest entry expires
        if members:
            try:
                oldest_ts = float(members[0].split(":")[0]) if ":" in members[0] else float(members[0])
            except (ValueError, IndexError):
                oldest_ts = window_start
            retry_after = int((oldest_ts + window) - now) + 1
            return False, max(retry_after, 1)
        return False, 1

    # Add new entry
    member = f"{now}:{id(asyncio.current_task())}"
    await rc.zadd(key, {member: now})
    await rc.expire(key, window + 10)

    return True, 0


async def check_client_rate(client_id: str, limit_rpm: int) -> tuple[bool, int]:
    """Check RPM rate limit for a client."""
    if settings.is_testing:
        return True, 0
    key = f"rl:client:{client_id}"
    return await _sliding_window_check(key, limit_rpm)


async def check_global_rate() -> tuple[bool, int]:
    """Check global RPM rate limit."""
    if settings.is_testing:
        return True, 0
    key = "rl:global"
    return await _sliding_window_check(key, settings.GLOBAL_RATE_LIMIT_RPM)


async def check_ip_rate(ip: str) -> tuple[bool, int]:
    """Check per-IP RPM rate limit."""
    if settings.is_testing:
        return True, 0
    key = f"rl:ip:{ip}"
    return await _sliding_window_check(key, settings.IP_RATE_LIMIT_RPM)


# ==========================================================================
# TPM (Tokens Per Minute) limiting
# ==========================================================================

async def _token_window_check(
    key: str, tokens_used: int, limit_tpm: int, window: int = _WINDOW
) -> tuple[bool, int]:
    """
    Sliding window for token counting.
    Each sorted set member stores "{timestamp}:{tokens}" with score=timestamp.
    """
    now = time.time()
    window_start = now - window

    # Remove expired
    await rc.zremrangebyscore(key, 0, window_start)

    # Sum tokens in window
    members = await rc.zrangebyscore(key, window_start, now)
    total = 0
    for m in members:
        try:
            parts = m.split(":")
            total += int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            pass

    if total + tokens_used > limit_tpm:
        # Find retry_after
        if members:
            try:
                oldest_ts = float(members[0].split(":")[0])
            except (ValueError, IndexError):
                oldest_ts = window_start
            retry_after = int((oldest_ts + window) - now) + 1
            return False, max(retry_after, 1)
        return False, 1

    # Record tokens
    member = f"{now}:{tokens_used}"
    await rc.zadd(key, {member: now})
    await rc.expire(key, window + 10)

    return True, 0


async def check_client_token_rate(
    client_id: str, tokens_used: int, tpm_limit: int
) -> tuple[bool, int]:
    """Check TPM limit for a client (post-response)."""
    if settings.is_testing:
        return True, 0
    key = f"trl:client:{client_id}"
    return await _token_window_check(key, tokens_used, tpm_limit)


async def check_global_token_rate(tokens_used: int) -> tuple[bool, int]:
    """Check global TPM limit (post-response)."""
    if settings.is_testing:
        return True, 0
    key = "trl:global"
    return await _token_window_check(key, tokens_used, settings.GLOBAL_RATE_LIMIT_TPM)


# ==========================================================================
# Concurrency semaphores
# ==========================================================================

_global_semaphore: asyncio.Semaphore | None = None
_client_counters: dict[str, int] = {}
_client_lock = asyncio.Lock()


def _get_global_semaphore() -> asyncio.Semaphore:
    global _global_semaphore
    if _global_semaphore is None:
        _global_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_GLOBAL)
    return _global_semaphore


@asynccontextmanager
async def acquire_global_slot() -> AsyncGenerator[None, None]:
    """Acquire a global concurrency slot."""
    sem = _get_global_semaphore()
    await sem.acquire()
    try:
        yield
    finally:
        sem.release()


@asynccontextmanager
async def acquire_client_slot(
    client_id: str, max_concurrent: int = 10
) -> AsyncGenerator[None, None]:
    """Acquire a per-client concurrency slot."""
    async with _client_lock:
        current = _client_counters.get(client_id, 0)
        if current >= max_concurrent:
            raise RateLimitExceeded(
                f"Max concurrent requests ({max_concurrent}) reached for client"
            )
        _client_counters[client_id] = current + 1

    try:
        yield
    finally:
        async with _client_lock:
            _client_counters[client_id] = max(
                _client_counters.get(client_id, 1) - 1, 0
            )


class RateLimitExceeded(Exception):
    """Raised when a rate limit or concurrency limit is exceeded."""

    def __init__(self, message: str, retry_after: int = 0):
        super().__init__(message)
        self.retry_after = retry_after


# ==========================================================================
# Active requests tracking (for admin dashboard)
# ==========================================================================

_active_requests: dict[str, dict] = {}


def register_active_request(request_id: str, info: dict) -> None:
    _active_requests[request_id] = {**info, "started_at": time.time()}


def unregister_active_request(request_id: str) -> None:
    _active_requests.pop(request_id, None)


def get_active_requests() -> list[dict]:
    now = time.time()
    return [
        {**info, "elapsed_ms": int((now - info["started_at"]) * 1000)}
        for info in _active_requests.values()
    ]


def get_active_count() -> int:
    return len(_active_requests)


# ==========================================================================
# Queue status (for dashboard)
# ==========================================================================

_queue_wait_times: list[float] = []


def record_queue_wait(wait_ms: float) -> None:
    """Record a queue wait time for averaging."""
    _queue_wait_times.append(wait_ms)
    if len(_queue_wait_times) > 1000:
        _queue_wait_times.pop(0)


def get_queue_status() -> dict:
    """Return queue status metrics."""
    from models import QueueStatus

    sem = _get_global_semaphore()
    # _value is the current count of available slots
    processing = settings.MAX_CONCURRENT_GLOBAL - sem._value
    avg_wait = (
        sum(_queue_wait_times) / len(_queue_wait_times)
        if _queue_wait_times else 0.0
    )

    return QueueStatus(
        queued=0,  # Approximation — true queue depth requires Redis
        processing=max(processing, 0),
        max_concurrent=settings.MAX_CONCURRENT_GLOBAL,
        avg_wait_ms=round(avg_wait, 2),
    ).model_dump()
