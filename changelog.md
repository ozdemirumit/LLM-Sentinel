# Changelog

## v1.0.0 — Initial Release

### Features
- OpenAI-compatible API gateway (drop-in replacement)
- 9 built-in LLM providers + unlimited custom OpenAI-compatible endpoints
- JWT + API key authentication with LDAP/AD integration
- Per-client rate limiting (RPM + TPM), quotas, and priority queuing
- Real-time cost tracking with built-in pricing for major providers
- Content policies: system prompt injection, topic blocking, model restriction, output filtering
- Semantic caching with configurable TTL and token limits
- Circuit breaker pattern with automatic failover and retry
- AES-256-GCM encryption for API keys and sensitive DB fields
- Request signing (HMAC-SHA256) for replay protection
- Password policy enforcement with history checking
- 18-tab admin dashboard with dark theme and WebSocket live sessions
- Prometheus metrics endpoint
- Structured JSON logging
- Configuration export/import with HMAC signature verification
- Automatic database backup (SQLite + PostgreSQL)
- Model aliasing (7 built-in aliases)
- 20 built-in data masking patterns
- Webhook alerting system
- Docker and systemd deployment support
