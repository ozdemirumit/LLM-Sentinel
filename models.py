"""
Pydantic v2 request / response models for the Enterprise LLM Sentinel.

Every model used by API endpoints, WebSocket messages, or internal
data transfer is defined here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ==========================================================================
# Core Chat
# ==========================================================================

class ContentBlock(BaseModel):
    type: str = "text"
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict | None = None


class UsageInfo(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ChatRequest(BaseModel):
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    system: str | None = None
    provider: str | None = None
    model: str | None = None
    stream: bool = False


class ChatResponse(BaseModel):
    stop_reason: str | None = None
    content: list[ContentBlock] = []
    usage: UsageInfo = UsageInfo()
    model: str | None = None
    provider: str | None = None
    request_id: str | None = None
    alias_used: str | None = None
    was_truncated: bool = False


# ==========================================================================
# Filter
# ==========================================================================

class FilterRequest(BaseModel):
    filter_type: Literal["ssh_output", "messages"] = "messages"
    text: str | None = None
    messages: list[dict[str, Any]] | None = None


class FilterResponse(BaseModel):
    filter_type: str
    text: str | None = None
    messages: list[dict[str, Any]] | None = None
    masked_count: int = 0


# ==========================================================================
# System
# ==========================================================================

class HealthResponse(BaseModel):
    status: str = "ok"
    provider: str | None = None
    model: str | None = None
    uptime_seconds: float = 0.0
    clients_count: int = 0
    redis_connected: bool = False


class StatsResponse(BaseModel):
    total_requests: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    errors: int = 0
    last_request_time: datetime | None = None
    uptime_seconds: float = 0.0


class ConfigRequest(BaseModel):
    provider: str | None = None
    model: str | None = None


class ConfigResponse(BaseModel):
    provider: str | None = None
    model: str | None = None


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    request_id: str | None = None


# ==========================================================================
# Auth
# ==========================================================================

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 3600


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class PasswordValidationResult(BaseModel):
    valid: bool
    errors: list[str] = []


# ==========================================================================
# Client Management
# ==========================================================================

class ClientCreate(BaseModel):
    name: str
    roles: list[str] = Field(default_factory=lambda: ["api_client"])
    permissions: list[str] = Field(default_factory=lambda: ["*"])
    allowed_providers: list[str] = Field(default_factory=list)
    allowed_models: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = 60
    token_limit_per_minute: int = 50000
    daily_token_quota: int = 0
    max_concurrent_requests: int = 10
    require_signing: bool = False
    priority: int = Field(default=5, ge=1, le=10)
    cache_enabled: bool = False
    ldap_group: str | None = None
    description: str | None = None


class ClientUpdate(BaseModel):
    name: str | None = None
    roles: list[str] | None = None
    permissions: list[str] | None = None
    allowed_providers: list[str] | None = None
    allowed_models: list[str] | None = None
    rate_limit_per_minute: int | None = None
    token_limit_per_minute: int | None = None
    daily_token_quota: int | None = None
    max_concurrent_requests: int | None = None
    require_signing: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=10)
    cache_enabled: bool | None = None
    ldap_group: str | None = None
    is_active: bool | None = None
    description: str | None = None


class ClientResponse(BaseModel):
    id: str
    name: str
    roles: list[str] = []
    permissions: list[str] = ["*"]
    allowed_providers: list[str] = []
    allowed_models: list[str] = []
    rate_limit_per_minute: int = 60
    token_limit_per_minute: int = 50000
    daily_token_quota: int = 0
    max_concurrent_requests: int = 10
    require_signing: bool = False
    priority: int = 5
    cache_enabled: bool = False
    ldap_group: str | None = None
    is_active: bool = True
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    description: str | None = None


class ApiKeyEntry(BaseModel):
    provider: str
    key_masked: str
    usage_count: int = 0
    error_count: int = 0
    is_healthy: bool = True
    last_used_at: datetime | None = None


class ApiKeyAddRequest(BaseModel):
    key: str


class AuditLogEntry(BaseModel):
    id: str
    timestamp: datetime
    event_type: str
    actor: str | None = None
    target: str | None = None
    detail: str | None = None
    ip: str | None = None
    success: bool = True


class QuotaResponse(BaseModel):
    client_id: str
    date: str
    input_tokens: int = 0
    output_tokens: int = 0
    daily_quota: int = 0
    percent_used: float = 0.0


# ==========================================================================
# Security
# ==========================================================================

class SecretRotationStatus(BaseModel):
    total_keys: int = 0
    re_encrypted: int = 0
    pending: int = 0
    last_rotation: datetime | None = None
    current_key_hash: str = ""


class ActiveSessionInfo(BaseModel):
    jti: str
    subject: str
    ip: str
    created_at: datetime
    expires_at: datetime
    user_agent: str | None = None


# ==========================================================================
# Request Logging
# ==========================================================================

class RequestLogEntry(BaseModel):
    id: str
    request_id: str
    client_id: str | None = None
    client_name: str | None = None
    timestamp: datetime
    provider: str | None = None
    model: str | None = None
    alias_used: str | None = None
    input_preview: str | None = None
    output_preview: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    duration_ms: int = 0
    status_code: int = 200
    was_truncated: bool = False
    masked_count: int = 0


# ==========================================================================
# Model Aliasing
# ==========================================================================

class ModelAlias(BaseModel):
    id: str
    alias: str
    provider: str
    model: str
    description: str | None = None
    is_active: bool = True
    created_at: datetime | None = None
    usage_count: int = 0


class ModelAliasCreate(BaseModel):
    alias: str
    provider: str
    model: str
    description: str | None = None


class ModelAliasUpdate(BaseModel):
    provider: str | None = None
    model: str | None = None
    description: str | None = None
    is_active: bool | None = None


# ==========================================================================
# Cost Tracking
# ==========================================================================

class CostRate(BaseModel):
    id: str
    provider: str
    model: str
    input_cost_per_1k: float
    output_cost_per_1k: float
    currency: str = "USD"
    effective_from: datetime | None = None
    is_active: bool = True


class CostRateCreate(BaseModel):
    provider: str
    model: str
    input_cost_per_1k: float
    output_cost_per_1k: float


class CostSummary(BaseModel):
    client_id: str | None = None
    provider: str | None = None
    model: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    request_count: int = 0


# ==========================================================================
# Alerting
# ==========================================================================

class AlertConfig(BaseModel):
    id: str
    event_type: str
    webhook_url: str
    description: str | None = None
    is_active: bool = True
    min_severity: Literal["info", "warning", "critical"] = "warning"
    created_at: datetime | None = None


class AlertConfigCreate(BaseModel):
    event_type: str
    webhook_url: str
    description: str | None = None
    min_severity: Literal["info", "warning", "critical"] = "warning"


class AlertHistoryEntry(BaseModel):
    id: str
    config_id: str
    event_type: str
    severity: str
    message: str
    sent_at: datetime
    success: bool = True
    error_message: str | None = None


# ==========================================================================
# Backup
# ==========================================================================

class BackupInfo(BaseModel):
    filename: str
    path: str
    size_bytes: int = 0
    created_at: datetime | None = None
    backup_type: Literal["sqlite", "postgres"] = "sqlite"


# ==========================================================================
# Config Export / Import
# ==========================================================================

class ConfigExportData(BaseModel):
    schema_version: str = "1.0"
    exported_at: datetime | None = None
    proxy_version: str = "1.0.0"
    signature: str | None = None
    filter_patterns: list[dict] = []
    model_aliases: list[dict] = []
    cost_rates: list[dict] = []
    alert_configs: list[dict] = []
    clients: list[dict] = []
    content_policies: list[dict] = []
    provider_configs: list[dict] = []
    runtime_config: dict = Field(default_factory=dict)


class ConfigImportOptions(BaseModel):
    overwrite_existing: bool = False
    import_clients: bool = False
    import_alert_configs: bool = True
    verify_signature: bool = True


class ConfigImportResult(BaseModel):
    imported_filter_patterns: int = 0
    imported_aliases: int = 0
    imported_cost_rates: int = 0
    imported_alert_configs: int = 0
    imported_clients: int = 0
    skipped: int = 0
    errors: list[str] = []
    signature_valid: bool | None = None


# ==========================================================================
# Live Sessions
# ==========================================================================

class LiveSession(BaseModel):
    session_id: str
    client_id: str | None = None
    client_name: str = ""
    provider: str = ""
    model: str = ""
    alias_used: str | None = None
    started_at: datetime | None = None
    elapsed_ms: int = 0
    status: Literal["queued", "running", "streaming", "done", "error"] = "queued"
    input_tokens_estimated: int = 0
    output_tokens_so_far: int = 0
    was_truncated: bool = False
    ip: str = ""


class SessionUpdate(BaseModel):
    event: Literal["session_start", "session_update", "session_end", "snapshot"]
    session: LiveSession | None = None
    sessions: list[LiveSession] | None = None
    timestamp: datetime | None = None


# ==========================================================================
# OpenAI-Compatible Models
# ==========================================================================

class OAIMessage(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    name: str | None = None


class OAIChatRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    temperature: float | None = None
    top_p: float | None = None
    n: int | None = None
    stream: bool = False
    stop: str | list[str] | None = None
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    seed: int | None = None
    user: str | None = None


class OAIChoice(BaseModel):
    index: int = 0
    message: OAIMessage
    finish_reason: str | None = None


class OAIUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class OAIChatResponse(BaseModel):
    id: str = ""
    object: str = "chat.completion"
    created: int = 0
    model: str = ""
    choices: list[OAIChoice] = []
    usage: OAIUsage = OAIUsage()
    system_fingerprint: str | None = None


class OAIDelta(BaseModel):
    role: str | None = None
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class OAIStreamChoice(BaseModel):
    index: int = 0
    delta: OAIDelta = OAIDelta()
    finish_reason: str | None = None


class OAIStreamChunk(BaseModel):
    id: str = ""
    object: str = "chat.completion.chunk"
    created: int = 0
    model: str = ""
    choices: list[OAIStreamChoice] = []


class OAIEmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]
    encoding_format: str | None = None


class OAIEmbeddingData(BaseModel):
    object: str = "embedding"
    embedding: list[float] = []
    index: int = 0


class OAIEmbeddingUsage(BaseModel):
    prompt_tokens: int = 0
    total_tokens: int = 0


class OAIEmbeddingResponse(BaseModel):
    object: str = "list"
    data: list[OAIEmbeddingData] = []
    model: str = ""
    usage: OAIEmbeddingUsage = OAIEmbeddingUsage()


class OAIModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = ""


class OAIModelList(BaseModel):
    object: str = "list"
    data: list[OAIModelInfo] = []


# ==========================================================================
# Provider Management
# ==========================================================================

class ProviderConfig(BaseModel):
    id: str
    name: str
    provider_type: str
    base_url: str | None = None
    default_model: str = ""
    max_context_tokens: int = 128000
    is_active: bool = True
    config_json: dict = Field(default_factory=dict)
    created_at: datetime | None = None


class ProviderConfigCreate(BaseModel):
    name: str
    provider_type: str
    base_url: str | None = None
    default_model: str = ""
    config_json: dict = Field(default_factory=dict)


class ProviderConfigUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    is_active: bool | None = None
    config_json: dict | None = None


# ==========================================================================
# Content Policies
# ==========================================================================

class ContentPolicy(BaseModel):
    id: str
    name: str
    policy_type: Literal[
        "system_prompt_inject", "system_prompt_enforce",
        "topic_block", "output_filter",
        "max_output_tokens", "model_restrict",
    ]
    config_json: dict = Field(default_factory=dict)
    is_active: bool = True
    applies_to_clients: list[str] | None = None
    priority: int = 50
    created_at: datetime | None = None


class ContentPolicyCreate(BaseModel):
    name: str
    policy_type: Literal[
        "system_prompt_inject", "system_prompt_enforce",
        "topic_block", "output_filter",
        "max_output_tokens", "model_restrict",
    ]
    config_json: dict = Field(default_factory=dict)
    applies_to_clients: list[str] | None = None
    priority: int = 50


class ContentPolicyUpdate(BaseModel):
    name: str | None = None
    config_json: dict | None = None
    is_active: bool | None = None
    applies_to_clients: list[str] | None = None
    priority: int | None = None


class PolicyEvaluationResult(BaseModel):
    allowed: bool = True
    applied_policies: list[str] = []
    modified_messages: list[dict[str, Any]] | None = None
    reject_reason: str | None = None


# ==========================================================================
# Cache
# ==========================================================================

class CacheStats(BaseModel):
    enabled: bool = False
    strategy: str = "exact"
    hit_count: int = 0
    miss_count: int = 0
    hit_rate_percent: float = 0.0
    entries_count: int = 0
    ttl_seconds: int = 3600


# ==========================================================================
# Queue
# ==========================================================================

class QueueStatus(BaseModel):
    queued: int = 0
    processing: int = 0
    max_concurrent: int = 100
    avg_wait_ms: float = 0.0
