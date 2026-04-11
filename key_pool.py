"""
API Key Pool Manager — manages provider API keys with rotation,
health tracking, encryption, and dual-key re-encryption support.
"""

from __future__ import annotations

import os
import random
import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func

from db import (
    ApiKeyPool as ApiKeyPoolDB,
    encrypt_aes_gcm,
    decrypt_aes_gcm,
    get_db,
    _get_encryption_key,
    _get_previous_encryption_key,
)
from logger import get_logger
from models import ApiKeyEntry, SecretRotationStatus

log = get_logger(__name__)


class ApiKeyPool:
    """Pool of API keys for a single provider."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self._keys: list[dict[str, Any]] = []
        self._counter = 0
        self._lock = threading.Lock()
        self._backoff: dict[int, float] = {}  # index -> backoff_until timestamp

    @property
    def count(self) -> int:
        return len(self._keys)

    def _is_backed_off(self, index: int) -> bool:
        until = self._backoff.get(index)
        if until is None:
            return False
        import time
        if time.time() >= until:
            self._backoff.pop(index, None)
            return False
        return True

    def get_next_key(self, strategy: str = "round_robin") -> tuple[str, int] | None:
        """
        Get next available key using the specified strategy.
        Returns (plaintext_key, index) or None if no healthy keys.
        """
        with self._lock:
            if not self._keys:
                return None

            healthy = [
                (i, k) for i, k in enumerate(self._keys)
                if k["is_healthy"] and not self._is_backed_off(i)
            ]
            if not healthy:
                return None

            if strategy == "random":
                idx, entry = random.choice(healthy)
            elif strategy == "least_used":
                idx, entry = min(healthy, key=lambda x: x[1]["usage_count"])
            else:  # round_robin
                pos = self._counter % len(healthy)
                idx, entry = healthy[pos]
                self._counter += 1

            return entry["key_plaintext"], idx

    def mark_error(self, index: int, threshold: int = 10) -> None:
        """Increment error count; mark unhealthy if threshold exceeded."""
        with self._lock:
            if 0 <= index < len(self._keys):
                self._keys[index]["error_count"] += 1
                if self._keys[index]["error_count"] >= threshold:
                    self._keys[index]["is_healthy"] = False
                    log.warning(
                        "Key marked unhealthy",
                        extra={
                            "provider": self.provider,
                            "index": index,
                            "errors": self._keys[index]["error_count"],
                        },
                    )

    def mark_success(self, index: int) -> None:
        """Increment usage count and ensure key is healthy."""
        with self._lock:
            if 0 <= index < len(self._keys):
                self._keys[index]["usage_count"] += 1
                self._keys[index]["is_healthy"] = True
                self._keys[index]["last_used_at"] = datetime.now(timezone.utc)

    def mark_rate_limited(self, index: int, retry_after_seconds: int = 60) -> None:
        """Back off a key for the specified duration."""
        import time
        with self._lock:
            self._backoff[index] = time.time() + retry_after_seconds
            log.info(
                "Key backed off (rate limited)",
                extra={
                    "provider": self.provider,
                    "index": index,
                    "retry_after": retry_after_seconds,
                },
            )

    def get_health(self) -> list[ApiKeyEntry]:
        """Return masked key info for all keys in the pool."""
        result = []
        with self._lock:
            for entry in self._keys:
                key = entry["key_plaintext"]
                masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
                result.append(
                    ApiKeyEntry(
                        provider=self.provider,
                        key_masked=masked,
                        usage_count=entry["usage_count"],
                        error_count=entry["error_count"],
                        is_healthy=entry["is_healthy"],
                        last_used_at=entry.get("last_used_at"),
                    )
                )
        return result

    def add_key(self, key_plaintext: str, db_id: str | None = None) -> dict:
        """Add a key to the pool."""
        with self._lock:
            entry = {
                "key_plaintext": key_plaintext,
                "db_id": db_id,
                "usage_count": 0,
                "error_count": 0,
                "is_healthy": True,
                "last_used_at": None,
            }
            self._keys.append(entry)
            log.info(
                "Key added to pool",
                extra={"provider": self.provider, "pool_size": len(self._keys)},
            )
            return entry

    def remove_key(self, index: int) -> str | None:
        """Remove a key by index. Returns the DB id or None."""
        with self._lock:
            if 0 <= index < len(self._keys):
                removed = self._keys.pop(index)
                self._backoff.pop(index, None)
                log.info(
                    "Key removed from pool",
                    extra={"provider": self.provider, "pool_size": len(self._keys)},
                )
                return removed.get("db_id")
            return None


class KeyPoolManager:
    """Manages API key pools for all providers."""

    def __init__(self) -> None:
        self._pools: dict[str, ApiKeyPool] = {}

    def get_pool(self, provider: str) -> ApiKeyPool:
        """Get or create a pool for a provider."""
        if provider not in self._pools:
            self._pools[provider] = ApiKeyPool(provider)
        return self._pools[provider]

    @property
    def providers(self) -> list[str]:
        return list(self._pools.keys())

    async def load_from_db(self) -> None:
        """Load all keys from the api_key_pool table into memory."""
        async with get_db() as db:
            result = await db.execute(select(ApiKeyPoolDB))
            rows = result.scalars().all()

        enc_key = _get_encryption_key()
        prev_key = _get_previous_encryption_key()

        for row in rows:
            plaintext = _decrypt_key(row.key_encrypted, enc_key, prev_key)
            if plaintext is None:
                log.error(
                    "Failed to decrypt key",
                    extra={"provider": row.provider, "id": row.id},
                )
                continue

            pool = self.get_pool(row.provider)
            pool.add_key(plaintext, db_id=row.id)

        total = sum(p.count for p in self._pools.values())
        log.info(
            "Key pools loaded",
            extra={
                "providers": list(self._pools.keys()),
                "total_keys": total,
            },
        )

    async def seed_from_config(self) -> None:
        """Seed keys from environment config if DB is empty per provider."""
        from config import settings

        provider_keys: dict[str, list[str]] = {
            "anthropic": settings.anthropic_api_keys_list,
            "openai": settings.openai_api_keys_list,
            "azure_openai": settings.azure_openai_api_keys_list,
            "gemini": settings.gemini_api_keys_list,
            "groq": settings.groq_api_keys_list,
            "mistral": settings.mistral_api_keys_list,
        }

        enc_key = _get_encryption_key()

        async with get_db() as db:
            for provider, keys in provider_keys.items():
                if not keys:
                    continue

                # Check if provider already has keys in DB
                count_result = await db.execute(
                    select(func.count(ApiKeyPoolDB.id)).where(
                        ApiKeyPoolDB.provider == provider
                    )
                )
                existing = count_result.scalar() or 0
                if existing > 0:
                    continue

                for key_val in keys:
                    key_val = key_val.strip()
                    if not key_val:
                        continue

                    encrypted = _encrypt_key(key_val, enc_key)
                    row = ApiKeyPoolDB(
                        provider=provider,
                        key_encrypted=encrypted,
                    )
                    db.add(row)

                log.info(
                    "Seeded API keys from config",
                    extra={"provider": provider, "count": len(keys)},
                )

        # Reload from DB
        await self.load_from_db()

    async def add_key_to_db(self, provider: str, key_plaintext: str) -> ApiKeyEntry:
        """Add a new key to DB and memory pool."""
        enc_key = _get_encryption_key()
        encrypted = _encrypt_key(key_plaintext, enc_key)

        async with get_db() as db:
            row = ApiKeyPoolDB(
                provider=provider,
                key_encrypted=encrypted,
            )
            db.add(row)
            await db.flush()
            db_id = row.id

        pool = self.get_pool(provider)
        pool.add_key(key_plaintext, db_id=db_id)

        masked = key_plaintext[:8] + "..." + key_plaintext[-4:] if len(key_plaintext) > 12 else "***"
        return ApiKeyEntry(provider=provider, key_masked=masked)

    async def remove_key_from_db(self, provider: str, index: int) -> bool:
        """Remove a key from DB and memory pool."""
        pool = self.get_pool(provider)
        db_id = pool.remove_key(index)
        if db_id is None:
            return False

        async with get_db() as db:
            result = await db.execute(
                select(ApiKeyPoolDB).where(ApiKeyPoolDB.id == db_id)
            )
            row = result.scalars().first()
            if row:
                await db.delete(row)
        return True

    async def sync_health_to_db(self) -> None:
        """Write current health/usage stats back to DB."""
        async with get_db() as db:
            for provider, pool in self._pools.items():
                for entry in pool._keys:
                    db_id = entry.get("db_id")
                    if not db_id:
                        continue
                    result = await db.execute(
                        select(ApiKeyPoolDB).where(ApiKeyPoolDB.id == db_id)
                    )
                    row = result.scalars().first()
                    if row:
                        row.usage_count = entry["usage_count"]
                        row.error_count = entry["error_count"]
                        row.is_healthy = entry["is_healthy"]
                        row.last_used_at = entry.get("last_used_at")

    async def re_encrypt_stale_keys(self) -> int:
        """
        Re-encrypt keys that are still encrypted with the previous key.
        Returns count of re-encrypted keys.
        """
        enc_key = _get_encryption_key()
        prev_key = _get_previous_encryption_key()

        if not enc_key or not prev_key:
            return 0

        count = 0
        async with get_db() as db:
            result = await db.execute(select(ApiKeyPoolDB))
            rows = result.scalars().all()

            for row in rows:
                # Try decrypting with current key
                try:
                    decrypt_aes_gcm(row.key_encrypted, enc_key)
                    continue  # Already encrypted with current key
                except Exception:
                    pass

                # Try previous key
                try:
                    plaintext = decrypt_aes_gcm(row.key_encrypted, prev_key)
                except Exception:
                    log.warning(
                        "Key cannot be decrypted with either key",
                        extra={"provider": row.provider, "id": row.id},
                    )
                    continue

                # Re-encrypt with current key
                row.key_encrypted = encrypt_aes_gcm(plaintext, enc_key)
                count += 1

        if count > 0:
            log.info("Re-encrypted stale keys", extra={"count": count})
        return count

    async def get_rotation_status(self) -> SecretRotationStatus:
        """Report encryption key rotation status."""
        enc_key = _get_encryption_key()
        prev_key = _get_previous_encryption_key()

        total = 0
        re_encrypted = 0
        pending = 0

        async with get_db() as db:
            result = await db.execute(select(ApiKeyPoolDB))
            rows = result.scalars().all()
            total = len(rows)

            if enc_key:
                for row in rows:
                    try:
                        decrypt_aes_gcm(row.key_encrypted, enc_key)
                        re_encrypted += 1
                    except Exception:
                        pending += 1

        import hashlib
        key_hash = ""
        if enc_key:
            key_hash = hashlib.sha256(enc_key).hexdigest()[:8]

        return SecretRotationStatus(
            total_keys=total,
            re_encrypted=re_encrypted,
            pending=pending,
            current_key_hash=key_hash,
        )


# ---------------------------------------------------------------------------
# Encryption helpers (standalone, used before pool is loaded)
# ---------------------------------------------------------------------------

def _encrypt_key(plaintext: str, enc_key: bytes | None) -> str:
    """Encrypt an API key for DB storage."""
    if enc_key is None:
        return plaintext  # Dev mode
    return encrypt_aes_gcm(plaintext, enc_key)


def _decrypt_key(
    ciphertext: str,
    enc_key: bytes | None,
    prev_key: bytes | None = None,
) -> str | None:
    """Decrypt an API key from DB. Tries current then previous key."""
    if enc_key is None:
        return ciphertext  # Dev mode

    try:
        return decrypt_aes_gcm(ciphertext, enc_key)
    except Exception:
        pass

    if prev_key:
        try:
            return decrypt_aes_gcm(ciphertext, prev_key)
        except Exception:
            pass

    # Might be plain text (legacy)
    return ciphertext


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_manager: KeyPoolManager | None = None


def get_key_pool_manager() -> KeyPoolManager:
    """Get or create the global KeyPoolManager singleton."""
    global _manager
    if _manager is None:
        _manager = KeyPoolManager()
    return _manager
