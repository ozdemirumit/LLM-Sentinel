"""
Prometheus metrics for the onPrem LLM Sentinel.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

from logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

requests_total = Counter(
    "onprem_llm_sentinel_requests_total",
    "Total LLM requests",
    ["client", "provider", "model", "status"],
)

tokens_total = Counter(
    "onprem_llm_sentinel_tokens_total",
    "Total tokens processed",
    ["client", "provider", "type"],  # type = input | output
)

cost_usd_total = Counter(
    "onprem_llm_sentinel_cost_usd_total",
    "Total cost in USD",
    ["client_id", "provider", "model"],
)

cache_hits_total = Counter("onprem_llm_sentinel_cache_hits_total", "Cache hits")
cache_misses_total = Counter("onprem_llm_sentinel_cache_misses_total", "Cache misses")

# ---------------------------------------------------------------------------
# Histograms
# ---------------------------------------------------------------------------

request_duration = Histogram(
    "onprem_llm_sentinel_request_duration_seconds",
    "Request duration in seconds",
    ["client", "provider", "model"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
)

# ---------------------------------------------------------------------------
# Gauges
# ---------------------------------------------------------------------------

active_requests = Gauge("onprem_llm_sentinel_active_requests", "Currently active requests")

api_key_health = Gauge(
    "onprem_llm_sentinel_api_key_health",
    "API key health (1=healthy, 0=unhealthy)",
    ["provider", "key_index", "healthy"],
)

circuit_breaker_state = Gauge(
    "onprem_llm_sentinel_circuit_breaker_state",
    "Circuit breaker state (0=CLOSED, 1=HALF_OPEN, 2=OPEN)",
    ["provider", "state"],
)

quota_usage_percent = Gauge(
    "onprem_llm_sentinel_quota_usage_percent",
    "Quota usage percentage",
    ["client"],
)

queue_depth = Gauge("onprem_llm_sentinel_queue_depth", "Current queue depth")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def record_request(
    client: str,
    provider: str,
    model: str,
    status: str,
    duration: float,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None = None,
) -> None:
    """Record metrics for a completed request."""
    requests_total.labels(client=client, provider=provider, model=model, status=status).inc()
    request_duration.labels(client=client, provider=provider, model=model).observe(duration)
    tokens_total.labels(client=client, provider=provider, type="input").inc(input_tokens)
    tokens_total.labels(client=client, provider=provider, type="output").inc(output_tokens)
    if cost_usd and cost_usd > 0:
        cost_usd_total.labels(client_id=client, provider=provider, model=model).inc(cost_usd)


def get_metrics_bytes() -> bytes:
    """Generate Prometheus text format metrics."""
    return generate_latest()


def get_content_type() -> str:
    return CONTENT_TYPE_LATEST
