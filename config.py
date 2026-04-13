"""
Application configuration via Pydantic BaseSettings.

Loads environment variables from .env / .env.test / .env.production
based on ENVIRONMENT value.  Implements SecretResolver for vault://,
env://, file:// prefixes.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from logger import get_logger, setup_logging

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Secret Resolver
# ---------------------------------------------------------------------------

class SecretResolver:
    """Resolve secret values from vault://, env://, file:// or plain text."""

    @staticmethod
    def resolve(value: str | None) -> str | None:
        if value is None or value == "":
            return value

        if value.startswith("vault://"):
            path = value[len("vault://"):]
            try:
                import hvac  # noqa: F811
            except ImportError:
                raise ImportError(
                    "hvac package is required for vault:// secrets. "
                    "Install with: pip install hvac"
                )
            vault_addr = os.environ.get("VAULT_ADDR", "")
            vault_token = os.environ.get("VAULT_TOKEN", "")
            if not vault_addr or not vault_token:
                raise ValueError(
                    "VAULT_ADDR and VAULT_TOKEN must be set for vault:// secrets"
                )
            client = hvac.Client(url=vault_addr, token=vault_token)
            # path format: secret/data/path or secret/path
            parts = path.split("/")
            mount = parts[0]
            secret_path = "/".join(parts[1:])
            resp = client.secrets.kv.v2.read_secret_version(
                path=secret_path, mount_point=mount
            )
            data = resp["data"]["data"]
            # Return first value if single key, else JSON
            if len(data) == 1:
                return str(list(data.values())[0])
            import json
            return json.dumps(data)

        if value.startswith("env://"):
            var_name = value[len("env://"):]
            result = os.environ.get(var_name)
            if result is None:
                raise ValueError(
                    f"Environment variable '{var_name}' not found for env:// secret"
                )
            return result

        if value.startswith("file://"):
            file_path = value[len("file://"):]
            p = Path(file_path)
            if not p.exists():
                raise FileNotFoundError(
                    f"Secret file not found: {file_path}"
                )
            return p.read_text(encoding="utf-8").strip()

        # Plain text
        return value


def _resolve_secrets(values: dict[str, Any]) -> dict[str, Any]:
    """Resolve secret fields in the settings dict."""
    secret_fields = {
        "JWT_SECRET", "JWT_SECRET_PREVIOUS",
        "KEY_ENCRYPTION_SECRET", "KEY_ENCRYPTION_SECRET_PREVIOUS",
        "LDAP_BIND_PASSWORD", "WEBHOOK_SECRET",
    }
    for field_name in secret_fields:
        val = values.get(field_name)
        if val and isinstance(val, str):
            resolved = SecretResolver.resolve(val)
            values[field_name] = resolved
    return values


# ---------------------------------------------------------------------------
# Determine env file to load
# ---------------------------------------------------------------------------

def _get_env_file() -> str:
    env = os.environ.get("ENVIRONMENT", "development")
    if env == "testing":
        return ".env.test"
    if env == "production":
        return ".env.production"
    return ".env"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_get_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # == ENVIRONMENT ==
    ENVIRONMENT: str = "development"

    # == SERVER ==
    PROXY_HOST: str = "0.0.0.0"
    PROXY_PORT: int = 8765
    PROXY_WORKERS: int = 1
    PROXY_DOCS_ENABLED: bool = True

    # == TLS ==
    PROXY_TLS_ENABLED: bool = False
    PROXY_TLS_CERT: str = "certs/server.crt"
    PROXY_TLS_KEY: str = "certs/server.key"
    PROXY_TLS_CA: str = ""
    PROXY_MTLS_ENABLED: bool = False

    # == JWT ==
    JWT_SECRET: str = ""
    JWT_SECRET_PREVIOUS: str = ""
    JWT_EXPIRY_HOURS: int = 1
    JWT_REFRESH_EXPIRY_DAYS: int = 7
    JWT_ALGORITHM: str = "HS256"
    JWT_BIND_IP: bool = True
    JWT_MAX_SESSIONS: int = 3

    # == LDAP ==
    LDAP_ENABLED: bool = False
    LDAP_SERVER: str = "ldap://dc.example.com:389"
    LDAP_BASE_DN: str = "DC=example,DC=com"
    LDAP_BIND_DN: str = "CN=svc-llmsentinel,OU=ServiceAccounts,DC=example,DC=com"
    LDAP_BIND_PASSWORD: str = ""
    LDAP_USER_FILTER: str = "(sAMAccountName={username})"
    LDAP_GROUP_FILTER: str = "(member={dn})"
    LDAP_ADMIN_GROUP: str = "CN=LLM-Sentinel-Admins,OU=Groups,DC=example,DC=com"
    LDAP_OPERATOR_GROUP: str = "CN=LLM-Sentinel-Operators,OU=Groups,DC=example,DC=com"
    LDAP_USE_SSL: bool = False
    LDAP_VERIFY_CERT: bool = True
    LDAP_CACHE_TTL_SECONDS: int = 300

    # == AI PROVIDER API KEYS (seed only) ==
    ANTHROPIC_API_KEYS: str = ""
    OPENAI_API_KEYS: str = ""
    AZURE_OPENAI_API_KEYS: str = ""
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_API_VERSION: str = "2024-02-01"
    GEMINI_API_KEYS: str = ""
    GROQ_API_KEYS: str = ""
    MISTRAL_API_KEYS: str = ""
    OLLAMA_BASE_URLS: str = ""
    AWS_BEDROCK_REGION: str = "us-east-1"

    # == KEY POOL ==
    KEY_ROTATION_STRATEGY: str = "round_robin"
    KEY_ENCRYPTION_SECRET: str = ""
    KEY_ENCRYPTION_SECRET_PREVIOUS: str = ""

    # == RATE LIMITING — RPM ==
    GLOBAL_RATE_LIMIT_RPM: int = 1000
    IP_RATE_LIMIT_RPM: int = 60
    IP_BAN_THRESHOLD: int = 10
    IP_BAN_DURATION_SECONDS: int = 900

    # == RATE LIMITING — TPM ==
    GLOBAL_RATE_LIMIT_TPM: int = 500000
    CLIENT_DEFAULT_TPM: int = 50000

    # == REQUEST LIMITS ==
    MAX_REQUEST_BODY_KB: int = 512
    MAX_MESSAGES_PER_REQUEST: int = 50
    MAX_TOKENS_PER_REQUEST: int = 100000
    MAX_CONCURRENT_GLOBAL: int = 100

    # == CONTEXT WINDOW ==
    MAX_CONTEXT_TOKENS_DEFAULT: int = 128000
    CONTEXT_TRUNCATION_ENABLED: bool = True

    # == CIRCUIT BREAKER ==
    CB_FAILURE_THRESHOLD: int = 5
    CB_RECOVERY_TIMEOUT_SECONDS: int = 60

    # == RETRY ==
    MAX_RETRIES: int = 3

    # == PROVIDER TIMEOUTS ==
    ANTHROPIC_TIMEOUT_SECONDS: int = 120
    OPENAI_TIMEOUT_SECONDS: int = 120
    GEMINI_TIMEOUT_SECONDS: int = 120
    OLLAMA_TIMEOUT_SECONDS: int = 300

    # == FALLBACK CHAIN ==
    FALLBACK_CHAIN: str = "anthropic"

    # == REDIS ==
    REDIS_URL: str = ""

    # == DATABASE ==
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/proxy.db"

    # == STREAMING ==
    STREAMING_ENABLED: bool = True

    # == LOGGING ==
    PROXY_LOG_LEVEL: str = "INFO"
    PROXY_LOG_FILE: str = "data/proxy.log"
    LOG_FORMAT: str = "text"
    RETENTION_DAYS: int = 90

    # == REQUEST/RESPONSE LOGGING ==
    LOG_REQUEST_BODY: bool = False
    LOG_REQUEST_MAX_BODY_CHARS: int = 500

    # == ADMIN ==
    ADMIN_SESSION_TIMEOUT_MINUTES: int = 60
    METRICS_TOKEN: str = ""

    # == SECURITY ==
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000"
    AUDIT_DEPS: bool = False
    PASSWORD_MIN_LENGTH: int = 12
    REQUIRE_REQUEST_SIGNING: bool = False
    REQUEST_SIGNING_MAX_AGE_SECONDS: int = 300

    # == SECRET PROVIDER ==
    VAULT_ADDR: str = ""
    VAULT_TOKEN: str = ""

    # == WEBHOOK ALERTS ==
    WEBHOOK_SECRET: str = ""

    # == BACKUP ==
    BACKUP_ENABLED: bool = False
    BACKUP_DIR: str = "data/backups"
    BACKUP_KEEP_DAYS: int = 7

    # == CACHE ==
    CACHE_ENABLED: bool = True
    CACHE_TTL_SECONDS: int = 3600
    CACHE_MAX_TOKENS: int = 1000
    CACHE_STRATEGY: str = "exact"

    # == PRIORITY QUEUING ==
    QUEUE_TIMEOUT_SECONDS: int = 30

    # ------------------------------------------------------------------
    # Helper: split comma-separated string to list
    # ------------------------------------------------------------------
    @staticmethod
    def _split(val: str) -> list[str]:
        if not val or not val.strip():
            return []
        return [item.strip() for item in val.split(",") if item.strip()]

    # ------------------------------------------------------------------
    # Secret resolution + startup validations
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _resolve_and_validate(self) -> "Settings":
        # Resolve secrets
        for field_name in (
            "JWT_SECRET", "JWT_SECRET_PREVIOUS",
            "KEY_ENCRYPTION_SECRET", "KEY_ENCRYPTION_SECRET_PREVIOUS",
            "LDAP_BIND_PASSWORD", "WEBHOOK_SECRET",
        ):
            val = getattr(self, field_name, None)
            if val and isinstance(val, str):
                try:
                    resolved = SecretResolver.resolve(val)
                    object.__setattr__(self, field_name, resolved or "")
                except Exception:
                    if not self.is_testing:
                        raise

        # Skip all validations in testing
        if self.is_testing:
            return self

        # FAIL: JWT_SECRET missing or too short
        if not self.JWT_SECRET or len(self.JWT_SECRET) < 32:
            raise ValueError(
                "JWT_SECRET is required and must be at least 32 characters. "
                "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )

        # FAIL: production + no KEY_ENCRYPTION_SECRET
        if self.is_production and not self.KEY_ENCRYPTION_SECRET:
            raise ValueError(
                "KEY_ENCRYPTION_SECRET is required in production. "
                "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )

        # FAIL: multi-worker + no KEY_ENCRYPTION_SECRET
        if self.PROXY_WORKERS > 1 and not self.KEY_ENCRYPTION_SECRET:
            raise ValueError(
                "KEY_ENCRYPTION_SECRET is required when PROXY_WORKERS > 1"
            )

        # WARN: production checks
        if self.is_production:
            if not self.PROXY_TLS_ENABLED:
                log.warning("Production: PROXY_TLS_ENABLED=false is not recommended")
            if not self.REDIS_URL:
                log.warning("Production: REDIS_URL empty — rate limiting won't work across workers")
            if "localhost" in self.CORS_ALLOWED_ORIGINS:
                log.warning("Production: CORS_ALLOWED_ORIGINS contains 'localhost'")
            if self.LOG_FORMAT != "json":
                log.warning("Production: LOG_FORMAT should be 'json' for structured logging")
            if not self.WEBHOOK_SECRET:
                log.warning("Production: WEBHOOK_SECRET empty — webhook signatures disabled")

            # Warn on plain text secrets
            for fname in ("JWT_SECRET", "KEY_ENCRYPTION_SECRET", "LDAP_BIND_PASSWORD"):
                raw = os.environ.get(fname, "")
                if raw and not raw.startswith(("vault://", "env://", "file://")):
                    log.warning(
                        f"Secret '{fname}' loaded as plain text, "
                        "consider using vault://, env://, or file://"
                    )

        # INFO: no provider keys
        if not self.has_any_api_keys:
            log.info("No provider API keys configured in env — add via Admin UI")

        return self

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def is_testing(self) -> bool:
        return self.ENVIRONMENT == "testing"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def effective_database_url(self) -> str:
        if self.is_testing:
            return "sqlite+aiosqlite:///:memory:"
        return self.DATABASE_URL

    @property
    def has_any_api_keys(self) -> bool:
        return any([
            self.anthropic_api_keys_list,
            self.openai_api_keys_list,
            self.azure_openai_api_keys_list,
            self.gemini_api_keys_list,
            self.groq_api_keys_list,
            self.mistral_api_keys_list,
            self.ollama_base_urls_list,
        ])

    # List accessors for comma-separated string fields
    @property
    def anthropic_api_keys_list(self) -> list[str]:
        return self._split(self.ANTHROPIC_API_KEYS)

    @property
    def openai_api_keys_list(self) -> list[str]:
        return self._split(self.OPENAI_API_KEYS)

    @property
    def azure_openai_api_keys_list(self) -> list[str]:
        return self._split(self.AZURE_OPENAI_API_KEYS)

    @property
    def gemini_api_keys_list(self) -> list[str]:
        return self._split(self.GEMINI_API_KEYS)

    @property
    def groq_api_keys_list(self) -> list[str]:
        return self._split(self.GROQ_API_KEYS)

    @property
    def mistral_api_keys_list(self) -> list[str]:
        return self._split(self.MISTRAL_API_KEYS)

    @property
    def ollama_base_urls_list(self) -> list[str]:
        return self._split(self.OLLAMA_BASE_URLS)

    @property
    def fallback_chain_list(self) -> list[str]:
        return self._split(self.FALLBACK_CHAIN)

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return self._split(self.CORS_ALLOWED_ORIGINS)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
settings = Settings()
