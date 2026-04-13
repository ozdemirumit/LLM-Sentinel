"""
SQLAlchemy async engine, ORM table definitions, and DB lifecycle helpers.

Includes EncryptedText custom type for AES-256-GCM column encryption.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    ForeignKey,
    Index,
    event,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from logger import get_logger

log = get_logger(__name__)


# ==========================================================================
# AES-256-GCM encryption helpers
# ==========================================================================

def _get_encryption_key() -> bytes | None:
    """Return current encryption key as bytes, or None if not set."""
    from config import settings
    key = settings.KEY_ENCRYPTION_SECRET
    if not key:
        return None
    if isinstance(key, str):
        key_bytes = key.encode("utf-8")
    else:
        key_bytes = key
    # Ensure exactly 32 bytes for AES-256
    if len(key_bytes) < 32:
        key_bytes = key_bytes + b"\0" * (32 - len(key_bytes))
    return key_bytes[:32]


def _get_previous_encryption_key() -> bytes | None:
    """Return previous encryption key as bytes, or None if not set."""
    from config import settings
    key = settings.KEY_ENCRYPTION_SECRET_PREVIOUS
    if not key:
        return None
    if isinstance(key, str):
        key_bytes = key.encode("utf-8")
    else:
        key_bytes = key
    if len(key_bytes) < 32:
        key_bytes = key_bytes + b"\0" * (32 - len(key_bytes))
    return key_bytes[:32]


def encrypt_aes_gcm(plaintext: str, key_bytes: bytes) -> str:
    """Encrypt plaintext with AES-256-GCM, return base64 encoded result."""
    import base64
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aesgcm = AESGCM(key_bytes)
    nonce = os.urandom(12)  # 96-bit nonce
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    # Format: base64(nonce + ciphertext)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_aes_gcm(ciphertext_b64: str, key_bytes: bytes) -> str:
    """Decrypt AES-256-GCM ciphertext from base64."""
    import base64
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = base64.b64decode(ciphertext_b64)
    nonce = raw[:12]
    ct = raw[12:]
    aesgcm = AESGCM(key_bytes)
    plaintext = aesgcm.decrypt(nonce, ct, None)
    return plaintext.decode("utf-8")


# ==========================================================================
# EncryptedText SQLAlchemy Custom Type
# ==========================================================================

class EncryptedText(TypeDecorator):
    """
    SQLAlchemy custom type that transparently encrypts/decrypts text
    using AES-256-GCM.  Falls back to plain text if KEY_ENCRYPTION_SECRET
    is not set (dev mode).
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        key = _get_encryption_key()
        if key is None:
            return value  # Dev mode: store plain text
        try:
            return encrypt_aes_gcm(value, key)
        except Exception as exc:
            log.warning("Encryption failed, storing plain text", extra={"error": str(exc)})
            return value

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        key = _get_encryption_key()
        if key is None:
            return value  # Dev mode: return as-is

        # Try current key
        try:
            return decrypt_aes_gcm(value, key)
        except Exception:
            pass

        # Try previous key
        prev_key = _get_previous_encryption_key()
        if prev_key:
            try:
                return decrypt_aes_gcm(value, prev_key)
            except Exception:
                pass

        # Not encrypted or unknown key — return as-is
        return value


# ==========================================================================
# UUID helper
# ==========================================================================

def generate_uuid() -> str:
    return str(uuid.uuid4())


# ==========================================================================
# Base
# ==========================================================================

class Base(DeclarativeBase):
    pass


# ==========================================================================
# ORM Tables
# ==========================================================================

class Client(Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    roles: Mapped[dict] = mapped_column(JSON, default=list)
    permissions: Mapped[dict] = mapped_column(JSON, default=lambda: ["*"])
    allowed_providers: Mapped[dict] = mapped_column(JSON, default=list)
    allowed_models: Mapped[dict] = mapped_column(JSON, default=list)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    token_limit_per_minute: Mapped[int] = mapped_column(Integer, default=50000)
    daily_token_quota: Mapped[int] = mapped_column(Integer, default=0)
    max_concurrent_requests: Mapped[int] = mapped_column(Integer, default=10)
    require_signing: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    cache_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ldap_group: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    token_usages: Mapped[list["TokenUsage"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    request_logs: Mapped[list["RequestLog"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class TokenUsage(Base):
    __tablename__ = "token_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    client_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    client: Mapped["Client"] = relationship(back_populates="token_usages")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)


class ApiKeyPool(Base):
    __tablename__ = "api_key_pool"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    is_healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class FilterPattern(Base):
    __tablename__ = "filter_patterns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    replacement: Mapped[str] = mapped_column(String(50), default="[REDACTED]")
    flags: Mapped[str] = mapped_column(String(20), default="IGNORECASE")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)


class LocalUser(Base):
    __tablename__ = "local_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    roles: Mapped[dict] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    password_history: Mapped[list["PasswordHistory"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class PasswordHistory(Base):
    __tablename__ = "password_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("local_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["LocalUser"] = relationship(back_populates="password_history")


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    client_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    alias_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_preview: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    output_preview: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status_code: Mapped[int] = mapped_column(Integer, default=200)
    was_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    masked_count: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)

    client: Mapped["Client | None"] = relationship(back_populates="request_logs")


class ModelAliasDB(Base):
    __tablename__ = "model_aliases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    alias: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    usage_count: Mapped[int] = mapped_column(Integer, default=0)


class CostRateDB(Base):
    __tablename__ = "cost_rates"
    __table_args__ = (
        UniqueConstraint("provider", "model", "effective_from", name="uq_cost_rate"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_cost_per_1k: Mapped[float] = mapped_column(Float, nullable=False)
    output_cost_per_1k: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    effective_from: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AlertConfigDB(Base):
    __tablename__ = "alert_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    webhook_url: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    min_severity: Mapped[str] = mapped_column(String(20), default="warning")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    history: Mapped[list["AlertHistoryDB"]] = relationship(
        back_populates="config", cascade="all, delete-orphan"
    )


class AlertHistoryDB(Base):
    __tablename__ = "alert_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    config_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("alert_configs.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    config: Mapped["AlertConfigDB"] = relationship(back_populates="history")


class ProviderConfigDB(Base):
    __tablename__ = "provider_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_header_name: Mapped[str] = mapped_column(String(100), default="Authorization")
    default_model: Mapped[str] = mapped_column(String(100), nullable=False)
    max_context_tokens: Mapped[int] = mapped_column(Integer, default=128000)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class ContentPolicyDB(Base):
    __tablename__ = "content_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    policy_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    applies_to_clients: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=50)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


# ==========================================================================
# Engine & Session
# ==========================================================================

_engine = None
_async_session_factory = None


def _get_engine_url() -> str:
    from config import settings
    return settings.effective_database_url


def _create_engine():
    global _engine, _async_session_factory
    url = _get_engine_url()

    connect_args = {}
    if "sqlite" in url:
        connect_args["check_same_thread"] = False

    _engine = create_async_engine(
        url,
        echo=False,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
    _async_session_factory = async_sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False
    )

    # SQLite WAL mode
    if "sqlite" in url and ":memory:" not in url:
        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()


def get_engine():
    global _engine
    if _engine is None:
        _create_engine()
    return _engine


def get_session_factory():
    global _async_session_factory
    if _async_session_factory is None:
        _create_engine()
    return _async_session_factory


@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager yielding a DB session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables (for dev/testing). Use Alembic migrations in production."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("Database tables initialized")


async def run_migrations() -> None:
    """
    Placeholder for Alembic migrations.
    In production, use: alembic upgrade head
    In development/testing, init_db() creates tables directly.
    """
    await init_db()


# ==========================================================================
# ALEMBIC SETUP
# ==========================================================================
# To configure Alembic with this project:
#
# 1. Run: alembic init alembic
#
# 2. Edit alembic/env.py:
#    - Add at the top:
#        import sys, os
#        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
#        from db import Base
#
#    - In run_migrations_online():
#        target_metadata = Base.metadata
#
#    - For async support, use run_async_migrations() pattern:
#        from sqlalchemy.ext.asyncio import async_engine_from_config
#
# 3. Edit alembic.ini:
#        sqlalchemy.url = sqlite+aiosqlite:///./data/proxy.db
#
# 4. Generate migration:
#        alembic revision --autogenerate -m "initial_tables"
#
# 5. Apply:
#        alembic upgrade head
# ==========================================================================
