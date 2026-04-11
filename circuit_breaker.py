"""
Circuit Breaker pattern for provider key health management.

States: CLOSED -> OPEN -> HALF_OPEN -> CLOSED (or back to OPEN).
State stored in Redis (or in-memory fallback).
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from logger import get_logger

log = get_logger(__name__)


class CBState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker for a single (provider, key_index) pair."""

    def __init__(
        self,
        provider: str,
        key_index: int,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
    ) -> None:
        self.provider = provider
        self.key_index = key_index
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state = CBState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._opened_at: float = 0.0

    @property
    def state(self) -> CBState:
        if self._state == CBState.OPEN:
            if time.time() - self._opened_at >= self.recovery_timeout:
                self._state = CBState.HALF_OPEN
                log.info(
                    "Circuit breaker HALF_OPEN",
                    extra={"provider": self.provider, "key_index": self.key_index},
                )
        return self._state

    def is_available(self) -> bool:
        """Return True if requests can pass through."""
        return self.state != CBState.OPEN

    def record_success(self) -> None:
        """Record a successful request."""
        if self._state in (CBState.HALF_OPEN, CBState.CLOSED):
            if self._state == CBState.HALF_OPEN:
                log.info(
                    "Circuit breaker CLOSED (recovered)",
                    extra={"provider": self.provider, "key_index": self.key_index},
                )
            self._state = CBState.CLOSED
            self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed request."""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state == CBState.HALF_OPEN:
            self._state = CBState.OPEN
            self._opened_at = time.time()
            log.warning(
                "Circuit breaker OPEN (half-open failure)",
                extra={"provider": self.provider, "key_index": self.key_index},
            )
            return

        if self._failure_count >= self.failure_threshold:
            self._state = CBState.OPEN
            self._opened_at = time.time()
            log.warning(
                "Circuit breaker OPEN",
                extra={
                    "provider": self.provider,
                    "key_index": self.key_index,
                    "failures": self._failure_count,
                },
            )

    def reset(self) -> None:
        """Manual reset to CLOSED."""
        self._state = CBState.CLOSED
        self._failure_count = 0
        self._opened_at = 0.0
        log.info(
            "Circuit breaker manually reset",
            extra={"provider": self.provider, "key_index": self.key_index},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "key_index": self.key_index,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
        }


class CircuitBreakerManager:
    """Manages circuit breakers for all (provider, key_index) pairs."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._breakers: dict[str, CircuitBreaker] = {}

    def _key(self, provider: str, key_index: int) -> str:
        return f"{provider}:{key_index}"

    def get_breaker(self, provider: str, key_index: int) -> CircuitBreaker:
        """Get or create a circuit breaker for (provider, key_index)."""
        k = self._key(provider, key_index)
        if k not in self._breakers:
            self._breakers[k] = CircuitBreaker(
                provider, key_index,
                self.failure_threshold, self.recovery_timeout,
            )
        return self._breakers[k]

    def is_available(self, provider: str, key_index: int) -> bool:
        return self.get_breaker(provider, key_index).is_available()

    def record_success(self, provider: str, key_index: int) -> None:
        self.get_breaker(provider, key_index).record_success()

    def record_failure(self, provider: str, key_index: int) -> None:
        self.get_breaker(provider, key_index).record_failure()

    def reset(self, provider: str, key_index: int | None = None) -> None:
        """Reset breaker(s). If key_index is None, reset all for provider."""
        if key_index is not None:
            self.get_breaker(provider, key_index).reset()
        else:
            for k, cb in list(self._breakers.items()):
                if cb.provider == provider:
                    cb.reset()

    def get_all_states(self) -> dict[str, str]:
        """Return {key: state} for all breakers."""
        return {k: cb.state.value for k, cb in self._breakers.items()}

    def get_provider_states(self, provider: str) -> list[dict[str, Any]]:
        """Return detailed state for all breakers of a provider."""
        return [
            cb.to_dict()
            for cb in self._breakers.values()
            if cb.provider == provider
        ]

    async def sync_to_redis(self) -> None:
        """Persist breaker states to Redis for multi-worker sync."""
        import redis_client
        import json

        for k, cb in self._breakers.items():
            data = json.dumps(cb.to_dict())
            await redis_client.set_with_ttl(
                f"cb:{k}", data, self.recovery_timeout * 2
            )

    async def sync_from_redis(self) -> None:
        """Load breaker states from Redis (for multi-worker)."""
        import redis_client
        import json

        keys = await redis_client.get_all_keys("cb:*")
        for rk in keys:
            data_raw = await redis_client.get_value(rk)
            if not data_raw:
                continue
            try:
                data = json.loads(data_raw)
                provider = data["provider"]
                key_index = data["key_index"]
                cb = self.get_breaker(provider, key_index)
                state_str = data.get("state", "closed")
                cb._state = CBState(state_str)
                cb._failure_count = data.get("failure_count", 0)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager: CircuitBreakerManager | None = None


def get_circuit_breaker_manager() -> CircuitBreakerManager:
    global _manager
    if _manager is None:
        from config import settings
        _manager = CircuitBreakerManager(
            failure_threshold=settings.CB_FAILURE_THRESHOLD,
            recovery_timeout=settings.CB_RECOVERY_TIMEOUT_SECONDS,
        )
    return _manager
