# Admin Guide

This guide covers everything an administrator needs to install, configure, manage, and maintain the onPrem LLM Sentinel.

---

## Table of Contents

- [Installation](#installation)
- [Configuration (.env)](#configuration-env)
- [Starting the Server](#starting-the-server)
- [Creating the Admin User](#creating-the-admin-user)
- [Admin Panel Overview](#admin-panel-overview)
- [Client Management](#client-management)
- [API Key Pool Management](#api-key-pool-management)
- [Provider Configuration](#provider-configuration)
- [Model Aliases](#model-aliases)
- [Content Policies (Guardrails)](#content-policies-guardrails)
- [Data Filtering](#data-filtering)
- [Cost Tracking](#cost-tracking)
- [Alerts and Webhooks](#alerts-and-webhooks)
- [Circuit Breakers](#circuit-breakers)
- [Security Management](#security-management)
- [Backup and Recovery](#backup-and-recovery)
- [Monitoring and Logging](#monitoring-and-logging)
- [Production Checklist](#production-checklist)
- [Troubleshooting](#troubleshooting)

---

## Installation

### Prerequisites

- Python 3.12 or higher (3.14 supported)
- pip (latest version)
- Optional: Redis 7+ (required for multi-worker deployment)
- Optional: PostgreSQL 16+ (default is SQLite)

### Step-by-Step

```bash
# 1. Clone
git clone https://github.com/ozdemirumit/onPrem-LLM-Sentinel.git
cd onPrem-LLM-Sentinel

# 2. Virtual environment
python -m venv .venv
.venv\Scripts\activate.bat        # Windows
# source .venv/bin/activate       # Linux/Mac

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env from example
cp .env.example .env
```

---

## Configuration (.env)

Open `.env` and configure at minimum these two values:

### Required Secrets

```bash
# Generate secrets:
python -c "import secrets; print(secrets.token_hex(32))"
```

| Variable | Required | Description |
|----------|----------|-------------|
| `JWT_SECRET` | **Yes** | Signs JWT tokens. Minimum 32 characters. Server will not start without it. |
| `KEY_ENCRYPTION_SECRET` | Recommended | Encrypts API keys in the database with AES-256-GCM. Required in production. |

### Server Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `PROXY_HOST` | `0.0.0.0` | Listen address |
| `PROXY_PORT` | `8765` | Listen port |
| `PROXY_WORKERS` | `1` | Number of uvicorn workers. Set to CPU*2+1 in production. |
| `PROXY_DOCS_ENABLED` | `true` | Enable /docs and /redoc. Disable in production. |
| `ENVIRONMENT` | `development` | `development`, `testing`, or `production` |

### TLS

| Variable | Default | Description |
|----------|---------|-------------|
| `PROXY_TLS_ENABLED` | `false` | Enable HTTPS. Recommended in production. |
| `PROXY_TLS_CERT` | `certs/server.crt` | Path to TLS certificate |
| `PROXY_TLS_KEY` | `certs/server.key` | Path to TLS private key |

Generate a self-signed certificate:
```bash
python main.py --gen-cert
```

### JWT Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_EXPIRY_HOURS` | `1` | Access token lifetime |
| `JWT_REFRESH_EXPIRY_DAYS` | `7` | Refresh token lifetime |
| `JWT_BIND_IP` | `true` | Bind tokens to client IP. Disable for mobile users. |
| `JWT_MAX_SESSIONS` | `3` | Max simultaneous sessions per user. Oldest auto-revoked. |
| `JWT_SECRET_PREVIOUS` | (empty) | Old JWT secret during key rotation. Both keys tried for decoding. |

### Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `GLOBAL_RATE_LIMIT_RPM` | `1000` | Global requests/minute |
| `IP_RATE_LIMIT_RPM` | `60` | Per-IP requests/minute |
| `IP_BAN_THRESHOLD` | `10` | Failed auth attempts before IP ban |
| `IP_BAN_DURATION_SECONDS` | `900` | Ban duration (15 minutes) |
| `GLOBAL_RATE_LIMIT_TPM` | `500000` | Global tokens/minute |
| `CLIENT_DEFAULT_TPM` | `50000` | Default per-client tokens/minute |
| `MAX_CONCURRENT_GLOBAL` | `100` | Max simultaneous requests |

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/proxy.db` | Database connection string |

For PostgreSQL:
```
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/llmsentinel
```

### Redis

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | (empty) | Redis connection. Required for multi-worker. Empty = in-memory fallback. |

Example: `redis://localhost:6379/0`

### Cache

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_ENABLED` | `false` | Enable semantic response caching |
| `CACHE_TTL_SECONDS` | `3600` | Cache entry lifetime (1 hour) |
| `CACHE_MAX_TOKENS` | `1000` | Responses above this token count are not cached |
| `CACHE_STRATEGY` | `exact` | `exact` (hash match) or `semantic` |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `PROXY_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `text` | `text` (development) or `json` (production) |
| `PROXY_LOG_FILE` | `data/proxy.log` | Log file path |
| `LOG_REQUEST_BODY` | `false` | Log request/response previews (performance impact) |
| `RETENTION_DAYS` | `90` | Auto-delete old logs after N days |

### Backup

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKUP_ENABLED` | `false` | Enable automatic daily backups |
| `BACKUP_DIR` | `data/backups` | Backup directory |
| `BACKUP_KEEP_DAYS` | `7` | Delete backups older than N days |

### Secret Provider

Instead of plain text secrets in `.env`, reference external sources:

```bash
# HashiCorp Vault
JWT_SECRET=vault://secret/llm-sentinel/jwt-secret

# OS environment variable
KEY_ENCRYPTION_SECRET=env://MY_ENC_KEY

# File (Docker secret / tmpfs)
LDAP_BIND_PASSWORD=file:///run/secrets/ldap_pass

# Plain text (not recommended for production)
WEBHOOK_SECRET=my-plain-secret
```

Vault requires `VAULT_ADDR` and `VAULT_TOKEN` to be set.

---

## Starting the Server

```bash
# Development
run.bat                     # Windows
python main.py              # Direct

# The server will:
# 1. Create data/ directory and SQLite database
# 2. Create 15 database tables
# 3. Seed 20 filter patterns, 7 aliases, 10 cost rates
# 4. Start listening on port 8765
```

### CLI Commands

| Command | Description |
|---------|-------------|
| `python main.py` | Start the server |
| `python main.py --create-admin` | Create an admin user (interactive) |
| `python main.py --gen-cert` | Generate self-signed TLS certificate |
| `python main.py --backup` | Run a manual database backup |

---

## Creating the Admin User

```bash
python main.py --create-admin
```

```
=== onPrem LLM Sentinel — Admin User Setup ===

Admin username: admin
Admin password: ********
Confirm password: ********

Admin user 'admin' created successfully.
```

**Password requirements:**
- Minimum 12 characters (configurable via `PASSWORD_MIN_LENGTH`)
- At least 1 uppercase letter
- At least 1 lowercase letter
- At least 1 digit
- At least 1 special character (!@#$%^&*...)
- Not in the top 10,000 common passwords list
- Not matching any of the user's last 5 passwords

---

## Admin Panel Overview

Access: `http://localhost:8765/admin/login`

The admin panel has 17 tabs organized in the sidebar:

| Group | Tabs |
|-------|------|
| **Core** | Dashboard, Clients, API Key Pools, Providers |
| **Configuration** | Aliases, Policies, Filtering, Costs |
| **Operations** | Alerts, Circuit Breakers, Security, Audit Log |
| **Monitoring** | Request Logs, Cache, Queue, Diagnostics |

The header bar shows the current tab name, WebSocket connection status (green = live session monitoring active), and the logged-in user.

---

## Client Management

**Tab: Clients**

Each application connecting through the proxy needs a client with its own API key.

### Creating a Client

1. Click **+ New Client**
2. Fill in the form:

| Field | Description | Example |
|-------|-------------|---------|
| Name | Descriptive identifier | `cursor-ide`, `production-api` |
| Permissions | Comma-separated capabilities | `*` (all) or `chat,models:list` |
| Rate Limit (RPM) | Requests per minute | `60` |
| Daily Token Quota | Max tokens/day, 0=unlimited | `500000` |
| Priority | Queue priority 1(highest)-10(lowest) | `5` |
| Description | Optional note | `Dev team IDE integration` |

3. Click **Create** — the API key is shown **once**. Copy it immediately.

### Available Permissions

| Permission | Allows |
|-----------|--------|
| `*` | Full access to all endpoints |
| `chat` | POST /v1/chat/completions and /v1/chat |
| `chat:stream` | Streaming chat requests |
| `embeddings` | POST /v1/embeddings |
| `models:list` | GET /v1/models |
| `filter` | POST /v1/filter |
| `health` | GET /v1/health |
| `stats` | GET /v1/stats |
| `config:read` | GET /v1/config |

### Client Actions

| Button | Action |
|--------|--------|
| **Quota** | View current daily token usage and percentage |
| **Regen** | Generate new API key (old key stops working immediately) |
| **Delete** | Permanently remove client and all usage data |

---

## API Key Pool Management

**Tab: API Key Pools**

Provider API keys (Anthropic, OpenAI, etc.) are stored here, encrypted with AES-256-GCM.

### Adding a Key

1. Select provider from dropdown
2. Paste the API key
3. Click **+ Add Key**

### Key Rotation Strategy

Configured via `KEY_ROTATION_STRATEGY` in `.env`:

| Strategy | Behavior |
|----------|----------|
| `round_robin` | Cycles through keys sequentially (default) |
| `random` | Picks a random healthy key each time |
| `least_used` | Uses the key with lowest usage count |

### Key Health

The proxy automatically tracks each key's health:

- **Healthy** (green): Working normally
- **Unhealthy** (red): Too many errors, automatically skipped

When a key receives a 429 (rate limited) from the provider, it is temporarily backed off. After too many consecutive 5xx errors, it is marked unhealthy.

---

## Provider Configuration

**Tab: Providers**

### Built-in Providers (9)

| Provider | SDK/Protocol | Auto-detected Model Prefix |
|----------|-------------|---------------------------|
| `anthropic` | Anthropic SDK | `claude-*` |
| `openai` | OpenAI SDK | `gpt-*`, `o1-*`, `o3-*` |
| `azure_openai` | OpenAI SDK (Azure) | — |
| `gemini` | Google GenAI SDK | `gemini-*` |
| `bedrock` | AWS boto3 | `anthropic.*` |
| `groq` | OpenAI-compatible | `llama-*`, `mixtral-*` |
| `mistral` | OpenAI-compatible | `mistral-*`, `codestral-*` |
| `ollama` | REST API (httpx) | — |
| `openai_compatible` | OpenAI-compatible | — |

### Adding a Custom Provider

For self-hosted models (vLLM, LiteLLM, LocalAI, text-generation-webui):

1. Click **+ Add Provider**
2. **Name:** `my-local-llm`
3. **Type:** `openai_compatible`
4. **Base URL:** `http://gpu-server:8000/v1`
5. **Default Model:** `meta/llama3`
6. Click **Create**
7. Click **Test** to verify the connection

### Azure OpenAI Setup

1. Set in `.env`:
   ```
   AZURE_OPENAI_ENDPOINT=https://mycompany.openai.azure.com
   AZURE_OPENAI_API_VERSION=2024-02-01
   ```
2. Add keys in Admin > API Key Pools > `azure_openai`
3. Use model names matching your Azure deployment names

### AWS Bedrock Setup

1. Set standard AWS credentials as environment variables:
   ```
   AWS_ACCESS_KEY_ID=AKIA...
   AWS_SECRET_ACCESS_KEY=...
   AWS_BEDROCK_REGION=us-east-1
   ```
2. No API keys needed in the pool — uses IAM credentials
3. Model names: `anthropic.claude-3-5-sonnet-20241022-v2:0`

---

## Model Aliases

**Tab: Aliases**

Aliases map short names to specific provider + model pairs.

### Built-in Aliases (7)

| Alias | Provider | Model |
|-------|----------|-------|
| `fast` | anthropic | claude-haiku-4-5-20251001 |
| `smart` | anthropic | claude-sonnet-4-6 |
| `powerful` | anthropic | claude-opus-4-6 |
| `gpt-fast` | openai | gpt-4o-mini |
| `gpt-smart` | openai | gpt-4o |
| `gemini-fast` | gemini | gemini-1.5-flash |
| `gemini-smart` | gemini | gemini-1.5-pro |

### Creating Custom Aliases

1. Click **+ New Alias**
2. **Alias:** lowercase slug (letters, numbers, hyphens)
3. **Provider:** target provider name
4. **Model:** target model name

Example: alias `coding` → `anthropic` / `claude-sonnet-4-6`

Clients send `"model": "coding"` and the proxy routes to Claude Sonnet.

---

## Content Policies (Guardrails)

**Tab: Policies**

Control what goes in and out of the LLM. Policies are evaluated in priority order (lower number = first).

### Policy Types

#### System Prompt Inject

Prepends a system message to every request:
```json
{
  "prompt": "You are a helpful enterprise assistant. Never share confidential data."
}
```
The client's own system prompt is preserved (appears after the injected one).

#### System Prompt Enforce

Replaces the client's system prompt entirely:
```json
{
  "prompt": "You must only answer questions about our products.",
  "allow_client_system": false
}
```

#### Topic Block

Blocks or redacts messages containing specific content:
```json
{
  "blocked_keywords": ["hack", "exploit", "bypass security"],
  "blocked_patterns": ["how to .*illegal"],
  "action": "reject",
  "message": "This topic is blocked by company policy."
}
```

- `action: "reject"` — returns 403 error
- `action: "redact"` — replaces keywords with `[BLOCKED]` and continues

#### Model Restrict

Limits which models a client can use:
```json
{
  "allowed_models": ["gpt-4o-mini", "claude-haiku-*"],
  "deny_message": "Only approved models are allowed."
}
```
Supports glob patterns (`*` wildcard).

#### Max Output Tokens

Enforces a maximum response length:
```json
{
  "max_tokens": 2048
}
```

#### Output Filter

Scans LLM responses for PII and redacts:
```json
{
  "check_pii": true,
  "check_profanity": true,
  "profanity_list": ["word1", "word2"]
}
```
Automatically detects and masks emails, SSNs, and credit card numbers.

### Policy Priority

Lower priority number runs first. If a policy rejects the request, subsequent policies are skipped. Recommended ordering:

1. **Priority 1-10:** System prompt policies
2. **Priority 10-30:** Model restrictions
3. **Priority 30-50:** Topic blocking
4. **Priority 50+:** Output filters

### Per-Client Policies

Set `applies_to_clients` to a list of client IDs to restrict a policy to specific clients. Leave empty (null) to apply to all clients.

---

## Data Filtering

**Tab: Filtering**

Regex patterns that automatically mask sensitive data in messages before they reach the LLM.

### Built-in Patterns (20)

| Pattern | Matches | Replacement |
|---------|---------|-------------|
| Password (key=value) | `password=secret123` | `[PASSWORD]` |
| Anthropic API Key | `sk-ant-...` | `[ANTHROPIC_KEY]` |
| OpenAI API Key | `sk-...` | `[OPENAI_KEY]` |
| AWS Access Key | `AKIA...` | `[AWS_KEY]` |
| GitHub Token | `ghp_...` | `[GITHUB_TOKEN]` |
| Slack Token | `xoxb-...` | `[SLACK_TOKEN]` |
| JWT Token | `eyJ...` | `[JWT]` |
| Credit Card | `4111...` | `[CREDIT_CARD]` |
| SSN | `123-45-6789` | `[SSN]` |
| Private Key | `-----BEGIN PRIVATE KEY-----` | `[PRIVATE_KEY]` |
| Connection String | `postgresql://...` | `[CONNECTION_STRING]` |
| Email Address | `user@example.com` | `[EMAIL]` |
| ... and 8 more | | |

### Custom Patterns

1. Click **+ New Pattern**
2. **Name:** descriptive name
3. **Regex Pattern:** regular expression
4. **Replacement:** text to replace matches with

Built-in patterns cannot be deleted but can be toggled off.

---

## Cost Tracking

**Tab: Costs**

Every chat request automatically calculates cost based on token usage.

### Built-in Rates (10)

| Provider | Model | Input $/1K | Output $/1K |
|----------|-------|-----------|-------------|
| anthropic | claude-opus-4-6 | $0.015 | $0.075 |
| anthropic | claude-sonnet-4-6 | $0.003 | $0.015 |
| anthropic | claude-haiku-4-5 | $0.00025 | $0.00125 |
| openai | gpt-4o | $0.0025 | $0.01 |
| openai | gpt-4o-mini | $0.00015 | $0.0006 |
| ollama | * (all models) | $0 | $0 |

### Cost Calculation

```
Cost = (input_tokens / 1000) × input_rate + (output_tokens / 1000) × output_rate
```

### Adding Custom Rates

1. Click **+ New Rate**
2. Enter provider, model, input and output costs per 1K tokens

### Viewing Costs

The **Cost Summary** section shows total cost, total requests, and total tokens. Use the API for per-client or per-date filtering:

```
GET /v1/costs/summary?client_id=xxx&date_from=2025-01-01&date_to=2025-01-31
```

---

## Alerts and Webhooks

**Tab: Alerts**

Receive HTTP webhook notifications when system events occur.

### Supported Events

| Event | Severity | When |
|-------|----------|------|
| `system_start` | info | Proxy starts |
| `system_error` | critical | Unhandled error |
| `circuit_open` | warning | Circuit breaker trips open |
| `no_healthy_keys` | critical | All keys for a provider are unhealthy |
| `quota_breach` | warning | Client exceeds daily quota |
| `ip_ban` | warning | IP banned for brute force |
| `key_error_threshold` | warning | Key error count exceeds threshold |
| `backup_success` | info | Backup completed |
| `backup_failure` | critical | Backup failed |

### Creating an Alert

1. Click **+ New Alert**
2. **Event Type:** `*` (all) or specific event
3. **Webhook URL:** your endpoint (e.g. Slack incoming webhook)
4. **Min Severity:** `info`, `warning`, or `critical`

### Webhook Payload

```json
{
  "event": "no_healthy_keys",
  "severity": "critical",
  "timestamp": "2025-01-15T10:30:00Z",
  "message": "No healthy API keys for provider: anthropic",
  "detail": null,
  "proxy_id": "llm-sentinel"
}
```

If `WEBHOOK_SECRET` is set, each request includes an `X-Proxy-Signature` header with HMAC-SHA256 signature for verification.

### Testing

Click the **Test** button to send a test ping to your webhook and verify it works.

---

## Circuit Breakers

**Tab: Circuit Breakers**

Protect the system from cascading failures when a provider is down.

### States

| State | Color | Meaning |
|-------|-------|---------|
| **CLOSED** | Green | Normal — requests flow through |
| **OPEN** | Red | Provider failing — requests blocked, routed to fallback |
| **HALF_OPEN** | Yellow | Testing recovery — one request allowed through |

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CB_FAILURE_THRESHOLD` | `5` | Consecutive failures to trip the breaker |
| `CB_RECOVERY_TIMEOUT_SECONDS` | `60` | Wait time before trying recovery |

### Fallback Chain

When a provider's circuit opens, requests fall back to the next provider in the chain:

```
FALLBACK_CHAIN=anthropic,openai,gemini
```

### Manual Reset

Click **Reset** to force a circuit breaker back to CLOSED state.

---

## Security Management

**Tab: Security**

### Key Encryption Rotation

When you rotate `KEY_ENCRYPTION_SECRET`:

1. Copy current secret to `KEY_ENCRYPTION_SECRET_PREVIOUS`
2. Set new secret in `KEY_ENCRYPTION_SECRET`
3. Restart the server
4. Go to Security tab > click **Rotate Now**
5. Verify: Total Keys = Re-encrypted, Pending = 0

### JWT Secret Rotation

Same process with `JWT_SECRET` and `JWT_SECRET_PREVIOUS`. Both keys are tried during token validation, allowing a smooth transition.

### IP Bans

IPs are automatically banned after `IP_BAN_THRESHOLD` (default: 10) failed authentication attempts for `IP_BAN_DURATION_SECONDS` (default: 900 = 15 minutes).

Admin can manually **Unban** any IP from the Security tab.

### Password Policy

Enforced for all local users:
- Minimum length: `PASSWORD_MIN_LENGTH` (default: 12)
- Requires: uppercase, lowercase, digit, special character
- Checked against top 10,000 common passwords
- Cannot reuse last 5 passwords

---

## Backup and Recovery

### Manual Backup

```bash
python main.py --backup
```

Or click **Run Backup Now** in Admin > Diagnostics.

### Automatic Backup

Set in `.env`:
```
BACKUP_ENABLED=true
BACKUP_DIR=data/backups
BACKUP_KEEP_DAYS=7
```

The proxy checks every 5 minutes and creates a daily backup if one doesn't exist.

### Backup Format

- **SQLite:** File copy to `data/backups/proxy_YYYYMMDD_HHMMSS.db`
- **PostgreSQL:** `pg_dump` to `data/backups/proxy_YYYYMMDD_HHMMSS.sql`

### Config Export/Import

**Export** (Diagnostics tab > Download Config):
- Exports: filter patterns, aliases, cost rates, alert configs, policies, provider configs
- Does NOT export: API keys, user passwords, token usage, audit logs
- Signed with HMAC-SHA256 for tamper detection

**Import** (via API):
```
POST /v1/admin/config/import
```
Options: `overwrite_existing`, `import_clients`, `verify_signature`

---

## Monitoring and Logging

### Prometheus Metrics

**Endpoint:** `GET /v1/metrics`

Requires `METRICS_TOKEN` in `.env`. Access with:
```
GET /v1/metrics?token=your-metrics-token
```

Available metrics:
- `llm_proxy_requests_total` — counter by client, provider, model, status
- `llm_proxy_request_duration_seconds` — histogram
- `llm_proxy_tokens_total` — counter by client, provider, type (input/output)
- `llm_proxy_cost_usd_total` — counter
- `llm_proxy_active_requests` — gauge
- `llm_proxy_cache_hits_total` / `llm_proxy_cache_misses_total` — counters

### Structured Logging

With `LOG_FORMAT=json`, every log line is valid JSON:
```json
{
  "timestamp": "2025-01-15T10:30:00Z",
  "level": "INFO",
  "logger": "main",
  "message": "Chat request",
  "request_id": "a1b2c3d4-...",
  "provider": "anthropic",
  "client": "my-app"
}
```

### Request Logs

Enable detailed request/response logging:
```
LOG_REQUEST_BODY=true
LOG_REQUEST_MAX_BODY_CHARS=500
```

View in Admin > Request Logs. Cleanup old logs with the **Cleanup Old Logs** button.

### Audit Log

All security events are recorded in Admin > Audit Log:
- `AUTH_SUCCESS` / `AUTH_FAILURE`
- `CONFIG_CHANGE`
- `CLIENT_CREATE` / `CLIENT_DELETE`
- `KEY_REGEN`
- `IP_BAN`

Also written to `data/audit.log` (rotating, max 50MB, 5 backups).

### Live Session Monitoring

Admin > Dashboard shows real-time chat sessions via WebSocket. Each session shows:
- Client name, provider, model
- Status: queued → running → streaming → done/error
- Elapsed time, output tokens so far, client IP

---

## Production Checklist

Before going to production, verify:

| Item | Setting | Why |
|------|---------|-----|
| ✅ TLS enabled | `PROXY_TLS_ENABLED=true` | Encrypt traffic |
| ✅ JWT secret set | `JWT_SECRET=<64+ chars>` | Token security |
| ✅ Encryption key set | `KEY_ENCRYPTION_SECRET=<64+ chars>` | API key encryption |
| ✅ IP binding enabled | `JWT_BIND_IP=true` | Prevent token theft |
| ✅ JSON logging | `LOG_FORMAT=json` | Structured log aggregation |
| ✅ Docs disabled | `PROXY_DOCS_ENABLED=false` | Hide Swagger docs |
| ✅ Webhook secret set | `WEBHOOK_SECRET=<secret>` | Signed alerts |
| ✅ Backups enabled | `BACKUP_ENABLED=true` | Data recovery |
| ✅ Redis configured | `REDIS_URL=redis://...` | Multi-worker rate limiting |
| ✅ Workers configured | `PROXY_WORKERS=<CPU*2+1>` | Performance |
| ✅ Secrets in Vault | `JWT_SECRET=vault://...` | No plain text secrets |
| ✅ Password min length | `PASSWORD_MIN_LENGTH=12` | Strong passwords |
| ✅ Metrics token set | `METRICS_TOKEN=<token>` | Protected metrics endpoint |

---

## Troubleshooting

### Server won't start

| Error | Cause | Fix |
|-------|-------|-----|
| `JWT_SECRET is required` | JWT_SECRET empty or < 32 chars | Set a 64-char hex value in .env |
| `KEY_ENCRYPTION_SECRET is required` | Production mode without encryption key | Generate and set the key |
| `[Errno 10048] address already in use` | Port 8765 in use | Kill the other process or change PROXY_PORT |
| `ModuleNotFoundError` | Missing dependency | Run `pip install -r requirements.txt` |

### Authentication issues

| Problem | Cause | Fix |
|---------|-------|-----|
| 401 on every request | Invalid or expired API key | Check key, regenerate if needed |
| 401 after IP change | JWT_BIND_IP enabled | Set `JWT_BIND_IP=false` or re-login |
| 429 Too Many Requests | Rate limit exceeded | Wait for Retry-After, or increase client RPM |
| Account locked | Too many failed logins | Wait 15 min, or admin unbans from Security tab |

### Provider issues

| Problem | Cause | Fix |
|---------|-------|-----|
| 502 Bad Gateway | Provider API error | Check provider status page, verify API key |
| 503 No healthy providers | All keys exhausted | Add more keys, check circuit breakers, reset breakers |
| Slow responses | Queue full | Increase MAX_CONCURRENT_GLOBAL, add more workers |
| Wrong model used | Alias misconfigured | Check Aliases tab, verify provider/model mapping |

### Database issues

| Problem | Fix |
|---------|-----|
| `data/proxy.db` missing | Server creates it on first start |
| Corrupted database | Restore from backup in `data/backups/` |
| Migration needed | Run `alembic upgrade head` (see db.py comments) |
