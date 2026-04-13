# onPrem LLM Sentinel

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/Tests-147%20passed-brightgreen.svg)](#running-tests)
[![OpenAI Compatible](https://img.shields.io/badge/API-OpenAI%20Compatible-10a37f.svg)](#quick-start)

**Enterprise LLM Gateway & Policy Engine — unified API, intelligent routing, AI-powered security.**

onPrem LLM Sentinel is a high-performance gateway that unifies access to all major LLM providers through a single OpenAI-compatible API. It gives engineering and platform teams centralized control over AI usage — routing, cost management, access policies, threat detection, and real-time observability — without changing a single line of application code.

Connect **Anthropic, OpenAI, Azure OpenAI, Gemini, AWS Bedrock, Groq, Mistral, Ollama**, or any OpenAI-compatible endpoint. Any tool that speaks the OpenAI protocol works instantly — just change `base_url` and `api_key`.

---

## Screenshots

| Dashboard | Clients | API Key Pools |
|:---------:|:-------:|:------------:|
| ![Dashboard](docs/screenshots/03-dashboard.png) | ![Clients](docs/screenshots/04-clients-list.png) | ![Keys](docs/screenshots/10-keys-overview.png) |

| Cost Tracking | Aliases | Content Policies |
|:------------:|:-------:|:----------------:|
| ![Costs](docs/screenshots/22-costs-rates.png) | ![Aliases](docs/screenshots/12-aliases-list.png) | ![Policies](docs/screenshots/19-policies-list.png) |

<details>
<summary><strong>View all 34 screenshots</strong></summary>

See the full collection: [docs/screenshots/](docs/screenshots/)

</details>

---

## Key Capabilities

| Category | Highlights |
|----------|-----------|
| **Unified Gateway** | 9 built-in providers + unlimited custom endpoints, model aliasing, intelligent routing, fallback chains, tool/function calling support |
| **AI-Powered Security** | Prompt injection detection, jailbreak detection, data leakage prevention — regex patterns (zero cost) or LLM-powered semantic analysis, or both combined |
| **Policy Engine** | 9 policy types: system prompt inject/enforce, topic blocking, model restriction, PII redaction, prompt injection detect, jailbreak detect, data leakage prevent, max output tokens |
| **Cost Management** | Real-time per-request cost tracking, daily quotas, built-in pricing for major models, cache hit/miss tracking in request logs |
| **Performance** | Async I/O, hundreds of concurrent requests, semantic caching (enabled by default), circuit breaker, priority queuing |
| **Security** | AES-256-GCM encryption, JWT + API keys, LDAP/AD SSO, request signing, password policies, user management UI, audit trail |
| **Observability** | 19-tab admin dashboard, real-time WebSocket live sessions (no refresh needed), Prometheus metrics, structured logging, webhook alerts |

---

## Architecture

```
  Applications                 onPrem LLM Sentinel              LLM Providers
                          ┌───────────────────────────┐
  Cursor IDE ─────┐       │   Auth & Permissions      │       ┌── Anthropic
  Continue   ─────┤       │   AI Threat Detection     │       ├── OpenAI
  Python App ─────┤──────>│   Policy Engine (9 types)  │──────>├── Azure OpenAI
  LangChain  ─────┤       │   Rate Limiting & Queuing  │       ├── Gemini
  curl / API ─────┘       │   Cost Tracking & Caching  │       ├── Bedrock / Groq
                          │   Data Masking (20 patterns)│       ├── Mistral / Ollama
  OpenAI-compatible       │   Circuit Breaker          │       └── Custom endpoints
  base_url + api_key      │   Live Monitoring          │
                          └───────────────────────────┘
                             Admin Dashboard (19 tabs)
                             Prometheus / Webhooks
```

---

## AI-Powered Threat Detection

onPrem LLM Sentinel includes built-in security policies that are **active by default** on first startup:

| Policy | What It Detects | Mode |
|--------|----------------|------|
| **Prompt Injection Protection** | Instruction override, role hijacking, system prompt extraction, delimiter attacks (20+ patterns) | Regex, AI, or Both |
| **Jailbreak Protection** | DAN/STAN jailbreaks, safety bypass, encoding tricks, base64 hidden commands, hypothetical framing (15+ patterns) | Regex, AI, or Both |
| **Data Leakage Prevention** | API keys, internal IPs/URLs, connection strings, private keys, secrets leaked in LLM responses (15+ patterns) | Regex-based redaction |

**Three detection modes** configurable per-policy via Admin UI:

| Mode | How It Works | Cost | Latency |
|------|-------------|------|---------|
| **Regex** (default) | Pattern matching against known attack signatures | Free | ~0ms |
| **AI** | Sends message to a guard LLM for semantic classification | ~$0.001/request | ~500ms |
| **Both** | Regex first (fast catch), then AI for what regex misses | ~$0.001 on regex miss | ~0ms hit, ~500ms miss |

AI mode works with any provider — use Anthropic Haiku for speed, a local Ollama model for zero cost, or any OpenAI-compatible endpoint.

---

## Quick Start

```bash
# 1. Clone & setup
git clone https://github.com/ozdemirumit/LLM-Sentinel.git
cd LLM-Sentinel
python -m venv .venv && .venv\Scripts\activate.bat
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env → set JWT_SECRET (min 32 chars):
#   python -c "import secrets; print(secrets.token_hex(32))"

# 3. Start
python main.py

# 4. Create admin user (separate terminal)
python main.py --create-admin

# 5. Open admin panel
#   http://localhost:8765/admin/login

# 6. Add provider API keys via Admin UI → API Key Pools
# 7. Create a client via Admin UI → Clients → + New Client
```

On first startup, the following are automatically created:
- 3 security policies (prompt injection, jailbreak, data leakage)
- 20 data masking patterns
- 7 model aliases
- 10 cost rates

---

## Usage

Connect any OpenAI-compatible client — just change two lines:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8765/v1",   # onPrem LLM Sentinel
    api_key="sk-proxy-xxx",                # Client key from Admin UI
)

response = client.chat.completions.create(
    model="fast",                          # Alias → Claude Haiku
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

Works with **curl**, **JavaScript**, **LangChain**, **LlamaIndex**, **Cursor**, **Continue**, **Cline**, **Aider**, **Open WebUI**, **AutoGen**, and any OpenAI-compatible tool. See the [User Guide](docs/user-guide.md) for all examples and configuration instructions.

---

## Model Aliases

Use short names instead of full model identifiers:

| Alias | Provider | Model |
|-------|----------|-------|
| `fast` | Anthropic | claude-haiku-4-5-20251001 |
| `smart` | Anthropic | claude-sonnet-4-6 |
| `powerful` | Anthropic | claude-opus-4-6 |
| `gpt-fast` | OpenAI | gpt-4o-mini |
| `gpt-smart` | OpenAI | gpt-4o |
| `gemini-fast` | Gemini | gemini-1.5-flash |
| `gemini-smart` | Gemini | gemini-1.5-pro |

Create custom aliases from Admin UI → **Aliases** tab.

---

## Admin Panel

19-tab dashboard at `http://localhost:8765/admin/login` — every item is fully editable:

| Tab | What It Does |
|-----|-------------|
| **Dashboard** | Real-time stats, WebSocket live sessions (auto-updates, no refresh needed) |
| **Clients** | Create/edit/delete apps, API keys, permissions, quotas, priority |
| **API Key Pools** | Add/remove provider keys (encrypted at rest) |
| **Providers** | Configure/edit/test custom LLM endpoints (vLLM, Ollama, etc.) |
| **Aliases** | Create/edit model name shortcuts |
| **Policies** | Create/edit/toggle security policies with AI Guard mode selector |
| **Filtering** | Create/edit/toggle regex data masking patterns (20 built-in) |
| **Costs** | Create/edit per-model pricing, cost summary dashboard |
| **Alerts** | Create/edit/test webhook notifications for system events |
| **Circuit Breakers** | Provider health status, manual reset |
| **Security** | Key encryption rotation, IP bans |
| **Users & LDAP** | Create/edit/delete local users, reset passwords, LDAP status & test |
| **Audit Log** | Security event history |
| **Request Logs** | Detailed logs with cache hit/miss tracking |
| **Cache** | Semantic cache stats, enable/disable (enabled by default) |
| **Queue** | Request processing queue status |
| **Diagnostics** | Health checks, backups, config export |

For detailed tab-by-tab instructions, see the [Admin Guide](docs/admin-guide.md).

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/chat/completions` | OpenAI-compatible chat (with tool/function calling) |
| POST | `/v1/embeddings` | Create embeddings |
| GET | `/v1/models` | List available models |
| GET | `/health/live` | Liveness probe (no auth) |
| GET | `/v1/metrics` | Prometheus metrics |

Full endpoint list (95+ routes) in the [User Guide](docs/user-guide.md#api-endpoints).

---

## Running Tests

```bash
# Quick
run_test.bat

# Verbose
python -m pytest tests/ -v --tb=short

# Coverage
python -m pytest tests/ --cov=. --cov-report=html
```

**147 tests** across 20 test files covering auth, chat, caching, guardrails, providers, and more.

---

## Documentation

| Document | Description |
|----------|-------------|
| [User Guide](docs/user-guide.md) | SDK examples, streaming, embeddings, tool calling, error handling, IDE setup for 10+ tools |
| [Admin Guide](docs/admin-guide.md) | Installation, configuration, all admin panel tabs, AI Guard setup, production checklist, troubleshooting |
| [LDAP Setup](docs/ldap-setup.md) | Active Directory integration |
| [TLS Setup](docs/tls-setup.md) | HTTPS, Let's Encrypt, self-signed certs |

---

## Project Structure

```
onprem-llm-sentinel/
  main.py              FastAPI app (95+ routes, 19-tab admin UI)
  config.py            Settings (.env loader)
  db.py                SQLAlchemy ORM (15 tables, AES-256-GCM)
  auth.py              JWT + API key + LDAP authentication
  llm_proxy.py         Orchestrator (retry, fallback, alias, truncation)
  providers/           9 LLM provider adapters
  guardrails.py        Policy engine + AI threat detection
  data_filter.py       Data masking (20 patterns)
  caching.py           Semantic cache
  cost_tracker.py      Cost tracking
  rate_limiter.py      RPM/TPM rate limiting
  circuit_breaker.py   Circuit breaker pattern
  alerting.py          Webhook alerts
  session_manager.py   Live session monitoring (WebSocket)
  static/admin.html    Admin UI (single-page app)
  tests/               20 test files, 147 tests
  docs/                User guide, admin guide, screenshots
```

---

## Contributing

Contributions are welcome!

1. **Report bugs** — open an [issue](https://github.com/ozdemirumit/LLM-Sentinel/issues)
2. **Suggest features** — describe your use case in an issue
3. **Submit a PR** — fork, branch, make changes, open a pull request

```bash
# Development setup
git clone https://github.com/ozdemirumit/LLM-Sentinel.git && cd LLM-Sentinel
python -m venv .venv && .venv\Scripts\activate.bat
pip install -r requirements.txt && pip install -r requirements-dev.txt
cp .env.example .env   # Set JWT_SECRET and KEY_ENCRYPTION_SECRET
python -m pytest tests/ -v   # 147 tests
```

---

## License

This project is licensed under the [MIT License](LICENSE).

---

<p align="center">
  <strong>onPrem LLM Sentinel — Enterprise LLM Gateway & Policy Engine</strong><br>
  Built with Python & FastAPI for teams that need control over their AI infrastructure.<br><br>
  <a href="https://github.com/ozdemirumit/LLM-Sentinel">GitHub</a> ·
  <a href="docs/user-guide.md">User Guide</a> ·
  <a href="docs/admin-guide.md">Admin Guide</a> ·
  <a href="https://github.com/ozdemirumit/LLM-Sentinel/issues">Report Bug</a> ·
  <a href="https://github.com/ozdemirumit/LLM-Sentinel/issues">Request Feature</a>
</p>
