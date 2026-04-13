"""
onPrem LLM Sentinel — main FastAPI application.

CLI:
  python main.py               — start server
  python main.py --gen-cert    — generate self-signed TLS cert
  python main.py --create-admin — create admin user (interactive)
  python main.py --backup      — run database backup
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.responses import Response as StarletteResponse

from logger import get_logger, setup_logging

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# CLI handling (before app creation)
# ---------------------------------------------------------------------------

def _handle_cli() -> None:
    if "--gen-cert" in sys.argv:
        setup_logging()
        from gen_cert import generate_self_signed_cert
        generate_self_signed_cert()
        sys.exit(0)
    if "--create-admin" in sys.argv:
        setup_logging()
        from bootstrap import create_admin_user
        create_admin_user()
        sys.exit(0)
    if "--backup" in sys.argv:
        setup_logging()
        from backup import run_backup
        asyncio.run(run_backup())
        sys.exit(0)

_handle_cli()

# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------

_start_time = time.time()
_stats = {
    "total_requests": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "errors": 0,
    "last_request_time": None,
}

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    from config import settings
    setup_logging(
        log_level=settings.PROXY_LOG_LEVEL,
        log_format=settings.LOG_FORMAT,
        log_file=settings.PROXY_LOG_FILE if not settings.is_testing else None,
    )

    # Ensure directories
    for d in ["data", "certs", "data/backups"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    # DB
    from db import init_db
    await init_db()

    # Seeds
    from filter_db import seed_builtin_patterns
    from model_alias import seed_builtin_aliases
    from cost_tracker import seed_cost_rates
    from guardrails import seed_security_policies

    await seed_builtin_patterns()
    await seed_builtin_aliases()
    await seed_cost_rates()
    await seed_security_policies()

    # Key pool
    from key_pool import get_key_pool_manager
    kpm = get_key_pool_manager()
    await kpm.seed_from_config()
    await kpm.load_from_db()

    # Re-encrypt stale keys
    re_count = await kpm.re_encrypt_stale_keys()
    if re_count > 0:
        log.info("Re-encrypted stale API keys", extra={"count": re_count})

    # Reload filter patterns into data_filter
    from filter_db import get_all_patterns
    from data_filter import reload_patterns
    patterns = await get_all_patterns()
    reload_patterns(patterns)

    # Redis connect
    from redis_client import get_redis
    await get_redis()

    # Fire startup alert
    try:
        from alerting import fire_alert, AlertEventType, AlertSeverity
        await fire_alert(AlertEventType.system_start, AlertSeverity.info, "onPrem LLM Sentinel started")
    except Exception:
        pass

    log.info("onPrem LLM Sentinel started", extra={
        "host": settings.PROXY_HOST,
        "port": settings.PROXY_PORT,
        "workers": settings.PROXY_WORKERS,
        "environment": settings.ENVIRONMENT,
        "tls": settings.PROXY_TLS_ENABLED,
    })

    # Background tasks
    bg_tasks: list[asyncio.Task] = []
    if not settings.is_testing:
        bg_tasks.append(asyncio.create_task(_background_cleanup()))
        bg_tasks.append(asyncio.create_task(_background_health_check()))

    yield

    # Shutdown
    for t in bg_tasks:
        t.cancel()
    from redis_client import _client
    await _client.close()
    log.info("onPrem LLM Sentinel shutdown")


async def _background_cleanup():
    """Periodic cleanup tasks."""
    from session_manager import session_manager
    while True:
        try:
            await asyncio.sleep(60)
            await session_manager.cleanup_stale(300)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("Background cleanup error", extra={"error": str(exc)})


async def _background_health_check():
    """Periodic health checks."""
    from config import settings
    while True:
        try:
            await asyncio.sleep(300)
            # Check for healthy keys per provider
            from key_pool import get_key_pool_manager
            kpm = get_key_pool_manager()
            for prov in kpm.providers:
                pool = kpm.get_pool(prov)
                healthy = [e for e in pool._keys if e["is_healthy"]]
                if not healthy and pool.count > 0:
                    from alerting import fire_alert, AlertEventType, AlertSeverity
                    await fire_alert(
                        AlertEventType.no_healthy_keys, AlertSeverity.critical,
                        f"No healthy API keys for provider: {prov}",
                    )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("Background health check error", extra={"error": str(exc)})


# ---------------------------------------------------------------------------
# Create app
# ---------------------------------------------------------------------------

from config import settings

app = FastAPI(
    title="onPrem LLM Sentinel",
    version="1.0.0",
    docs_url="/docs" if settings.PROXY_DOCS_ENABLED else None,
    redoc_url="/redoc" if settings.PROXY_DOCS_ENABLED else None,
    lifespan=lifespan,
)

# Middleware (order matters — last added = first executed)
from security import (
    SecurityHeadersMiddleware,
    BruteForceProtectionMiddleware,
    RequestSizeLimitMiddleware,
    RequestSigningMiddleware,
)

app.add_middleware(CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestSigningMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(BruteForceProtectionMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# Include auth router
from auth import auth_router, get_current_user, verify_admin, verify_permission, AuthenticatedUser
app.include_router(auth_router)


# ===========================================================================
# Helpers
# ===========================================================================

def _get_client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ===========================================================================
# HEALTH
# ===========================================================================

@app.get("/health/live", include_in_schema=False)
async def health_live():
    return {"status": "alive"}


@app.get("/health/ready", include_in_schema=False)
async def health_ready():
    return {"status": "ready"}


@app.get("/health")
@app.get("/v1/health")
async def health(user: AuthenticatedUser = Depends(get_current_user)):
    from redis_client import _client as rc
    from key_pool import get_key_pool_manager
    kpm = get_key_pool_manager()
    from clients import get_all_clients
    all_c = await get_all_clients()
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _start_time, 1),
        "clients_count": len(all_c),
        "redis_connected": rc.is_connected,
        "providers": kpm.providers,
    }


# ===========================================================================
# STATS / METRICS
# ===========================================================================

@app.get("/v1/stats")
async def get_stats(user: AuthenticatedUser = Depends(get_current_user)):
    return {**_stats, "uptime_seconds": round(time.time() - _start_time, 1)}


@app.post("/v1/stats/reset")
async def reset_stats(user: AuthenticatedUser = Depends(verify_admin)):
    _stats.update(total_requests=0, total_input_tokens=0, total_output_tokens=0, errors=0, last_request_time=None)
    return {"message": "Stats reset"}


@app.get("/v1/metrics")
async def prometheus_metrics(request: Request):
    if settings.METRICS_TOKEN:
        token = request.query_params.get("token") or request.headers.get("authorization", "").replace("Bearer ", "")
        if token != settings.METRICS_TOKEN:
            raise HTTPException(403, "Invalid metrics token")
    from metrics import get_metrics_bytes, get_content_type
    return Response(content=get_metrics_bytes(), media_type=get_content_type())


@app.get("/v1/active-requests")
async def active_requests(user: AuthenticatedUser = Depends(get_current_user)):
    from rate_limiter import get_active_requests
    return {"requests": get_active_requests()}


# ===========================================================================
# CHAT — OpenAI-compatible /v1/chat/completions
# ===========================================================================

@app.post("/v1/chat/completions")
async def chat_completions(request: Request, user: AuthenticatedUser = Depends(get_current_user)):
    return await _handle_chat(request, user, openai_format=True)


@app.post("/v1/chat")
async def chat_native(request: Request, user: AuthenticatedUser = Depends(get_current_user)):
    return await _handle_chat(request, user, openai_format=False)


@app.post("/v1/chat/stream")
async def chat_stream_native(request: Request, user: AuthenticatedUser = Depends(get_current_user)):
    body = await request.json()
    body["stream"] = True
    request._body = json.dumps(body).encode()
    return await _handle_chat(request, user, openai_format=False)


async def _handle_chat(request: Request, user: AuthenticatedUser, openai_format: bool):
    """Unified chat handler for both OpenAI and native formats."""
    from data_filter import sanitize_messages
    from guardrails import evaluate_request, evaluate_response
    from llm_proxy import get_llm_proxy, resolve_model, truncate_messages, estimate_messages_tokens, ProviderError
    from caching import compute_cache_key, get_cached_response, set_cached_response
    from session_manager import session_manager
    from rate_limiter import (
        check_ip_rate, check_global_rate, check_client_rate,
        acquire_global_slot, acquire_client_slot, RateLimitExceeded,
        register_active_request, unregister_active_request, record_queue_wait,
    )
    from request_logger import log_request
    from clients import record_token_usage, touch_client, get_quota_usage
    from cost_tracker import get_cost_rate, calculate_cost
    import metrics

    ip = _get_client_ip(request)
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    start = time.time()

    try:
        body = await request.json()
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Handle non-UTF-8 encoded bodies (e.g. Windows-1254 Turkish chars)
        raw = await request.body()
        try:
            body = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            raise HTTPException(400, detail=f"Invalid request body encoding: {exc}")

    # Extract fields
    messages = body.get("messages", [])
    model_hint = body.get("model")
    stream = body.get("stream", False)
    temperature = body.get("temperature")
    max_tokens = body.get("max_tokens")
    tools = body.get("tools")
    tool_choice = body.get("tool_choice")
    stop = body.get("stop")
    provider_hint = body.get("provider")
    system_prompt = body.get("system")

    # Rate limiting
    ok, retry = await check_ip_rate(ip)
    if not ok:
        raise HTTPException(429, detail="IP rate limit exceeded", headers={"Retry-After": str(retry)})
    ok, retry = await check_global_rate()
    if not ok:
        raise HTTPException(429, detail="Global rate limit exceeded", headers={"Retry-After": str(retry)})
    if user.client_id:
        ok, retry = await check_client_rate(user.client_id, 60)  # TODO: use client's rate_limit
        if not ok:
            raise HTTPException(429, detail="Client rate limit exceeded", headers={"Retry-After": str(retry)})

        # Quota check
        quota = await get_quota_usage(user.client_id)
        if quota.daily_quota > 0 and quota.percent_used >= 100:
            raise HTTPException(429, detail="Daily token quota exceeded")

    # Sanitize
    clean_messages, masked_count = sanitize_messages(messages)

    # Resolve model + provider
    provider, model, alias_used = await resolve_model(provider_hint, model_hint)

    # Guardrails
    guard_result = await evaluate_request(clean_messages, model, user.client_id)
    if not guard_result.allowed:
        raise HTTPException(403, detail=guard_result.reject_reason or "Blocked by content policy")
    if guard_result.modified_messages:
        clean_messages = guard_result.modified_messages

    # Context truncation
    if settings.CONTEXT_TRUNCATION_ENABLED:
        clean_messages, was_truncated = truncate_messages(clean_messages, settings.MAX_CONTEXT_TOKENS_DEFAULT)
    else:
        was_truncated = False

    # Cache check (non-streaming only)
    cache_hit = False
    cache_key = ""
    if settings.CACHE_ENABLED and not stream:
        cache_key = compute_cache_key(clean_messages, model, temperature)
        bypass = request.headers.get("x-cache-control", "").lower() == "no-cache"
        if not bypass:
            cached = await get_cached_response(cache_key)
            if cached:
                cache_hit = True
                duration_ms = int((time.time() - start) * 1000)
                headers = {"X-Request-ID": request_id, "X-Cache-Hit": "true"}
                if was_truncated:
                    headers["X-Context-Truncated"] = "true"
                return JSONResponse(content=cached.model_dump(mode="json"), headers=headers)

    # Session tracking
    session_id = str(uuid.uuid4())
    est_tokens = estimate_messages_tokens(clean_messages)
    await session_manager.register(
        session_id, user.client_id, user.name, provider, model,
        alias_used, est_tokens, ip,
    )
    register_active_request(request_id, {"client": user.name, "provider": provider, "model": model})

    try:
        queue_start = time.time()
        async with acquire_global_slot():
            queue_wait_ms = (time.time() - queue_start) * 1000
            record_queue_wait(queue_wait_ms)

            await session_manager.update(session_id, status="running")
            proxy = get_llm_proxy()

            if stream:
                # Streaming response
                await session_manager.update(session_id, status="streaming")

                async def sse_generator():
                    total_output = 0
                    try:
                        async for chunk in proxy.stream_chat_completions(
                            messages=clean_messages, model=model, provider=provider,
                            temperature=temperature, max_tokens=max_tokens,
                            tools=tools, tool_choice=tool_choice, stop=stop,
                        ):
                            data = json.dumps(chunk, default=str)
                            yield f"data: {data}\n\n"
                            # Count output tokens from content
                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    total_output += len(content) // 4 + 1
                                    await session_manager.update(
                                        session_id, output_tokens_so_far=total_output,
                                        elapsed_ms=int((time.time() - start) * 1000),
                                    )
                        yield "data: [DONE]\n\n"
                    except Exception as exc:
                        yield f"data: {json.dumps({'error': str(exc)})}\n\n"
                    finally:
                        await session_manager.close(session_id, "done")
                        unregister_active_request(request_id)
                        _stats["total_requests"] += 1
                        _stats["last_request_time"] = datetime.now(timezone.utc).isoformat()

                headers = {
                    "X-Request-ID": request_id,
                    "X-Cache-Hit": "false",
                    "X-Queue-Wait-Ms": str(int(queue_wait_ms)),
                }
                if was_truncated:
                    headers["X-Context-Truncated"] = "true"

                return StreamingResponse(
                    sse_generator(),
                    media_type="text/event-stream",
                    headers=headers,
                )

            # Non-streaming
            if openai_format:
                result = await proxy.chat_completions(
                    messages=clean_messages, model=model, provider=provider,
                    temperature=temperature, max_tokens=max_tokens,
                    tools=tools, tool_choice=tool_choice, stop=stop,
                )
                input_tok = result.usage.prompt_tokens
                output_tok = result.usage.completion_tokens
                content_text = ""
                if result.choices and result.choices[0].message:
                    content_text = result.choices[0].message.content or ""
            else:
                native_result = await proxy.chat(
                    messages=clean_messages, provider=provider, model=model,
                    tools=tools, system=system_prompt,
                )
                input_tok = native_result.usage.input_tokens
                output_tok = native_result.usage.output_tokens
                content_text = ""
                for block in native_result.content:
                    if block.text:
                        content_text += block.text

            duration_ms = int((time.time() - start) * 1000)

            # Output guardrails
            filtered_text, was_filtered = await evaluate_response(content_text, user.client_id)

            # Cache set
            if settings.CACHE_ENABLED and cache_key:
                total_tok = input_tok + output_tok
                if openai_format and result:
                    await set_cached_response(cache_key, result, total_tok)

            # Record usage
            cost = None
            if user.client_id:
                rate = await get_cost_rate(provider, model)
                cost = calculate_cost(input_tok, output_tok, rate)
                await record_token_usage(user.client_id, provider, model, input_tok, output_tok)
                await touch_client(user.client_id)

            # Log request
            await log_request(
                request_id=request_id, client_id=user.client_id, client_name=user.name,
                provider=provider, model=model, alias_used=alias_used,
                input_messages=clean_messages, output_content=content_text,
                input_tokens=input_tok, output_tokens=output_tok, cost_usd=cost,
                duration_ms=duration_ms, status_code=200,
                was_truncated=was_truncated, masked_count=masked_count,
                cache_hit=cache_hit,
            )

            # Metrics
            metrics.record_request(user.name, provider, model, "200", duration_ms / 1000, input_tok, output_tok, cost)
            metrics.active_requests.dec()

            _stats["total_requests"] += 1
            _stats["total_input_tokens"] += input_tok
            _stats["total_output_tokens"] += output_tok
            _stats["last_request_time"] = datetime.now(timezone.utc).isoformat()

            await session_manager.close(session_id, "done")

            headers = {
                "X-Request-ID": request_id,
                "X-Cache-Hit": "true" if cache_hit else "false",
                "X-Queue-Wait-Ms": str(int(queue_wait_ms)),
            }
            if was_truncated:
                headers["X-Context-Truncated"] = "true"

            if openai_format:
                return JSONResponse(content=result.model_dump(mode="json"), headers=headers)
            else:
                native_result.request_id = request_id
                native_result.was_truncated = was_truncated
                return JSONResponse(content=native_result.model_dump(mode="json"), headers=headers)

    except RateLimitExceeded as exc:
        await session_manager.close(session_id, "error")
        unregister_active_request(request_id)
        raise HTTPException(429, detail=str(exc))
    except ProviderError as exc:
        await session_manager.close(session_id, "error")
        unregister_active_request(request_id)
        _stats["errors"] += 1
        raise HTTPException(exc.status_code, detail=str(exc))
    except HTTPException:
        await session_manager.close(session_id, "error")
        unregister_active_request(request_id)
        raise
    except Exception as exc:
        await session_manager.close(session_id, "error")
        unregister_active_request(request_id)
        _stats["errors"] += 1
        log.error("Chat error", extra={"error": str(exc), "request_id": request_id})
        raise HTTPException(502, detail=str(exc))


# ===========================================================================
# EMBEDDINGS
# ===========================================================================

@app.post("/v1/embeddings")
async def embeddings(request: Request, user: AuthenticatedUser = Depends(get_current_user)):
    from llm_proxy import get_llm_proxy, resolve_model, ProviderError
    body = await request.json()
    model_hint = body.get("model", "text-embedding-3-small")
    input_data = body.get("input", "")
    if isinstance(input_data, str):
        input_data = [input_data]

    provider, model, alias = await resolve_model(None, model_hint)
    proxy = get_llm_proxy()
    try:
        result = await proxy.create_embeddings(input_data, model, provider)
        return result.model_dump(mode="json")
    except ProviderError as exc:
        raise HTTPException(exc.status_code, detail=str(exc))


# ===========================================================================
# MODELS
# ===========================================================================

@app.get("/v1/models")
async def list_models(user: AuthenticatedUser = Depends(get_current_user)):
    from llm_proxy import get_llm_proxy
    proxy = get_llm_proxy()
    result = await proxy.list_all_models()
    return result.model_dump(mode="json")


# ===========================================================================
# FILTER
# ===========================================================================

@app.post("/v1/filter")
async def filter_endpoint(request: Request, user: AuthenticatedUser = Depends(get_current_user)):
    from data_filter import sanitize_messages, sanitize_ssh_output
    body = await request.json()
    ft = body.get("filter_type", "messages")
    if ft == "ssh_output":
        filtered, matches = sanitize_ssh_output(body.get("text", ""))
        return {"filter_type": ft, "text": filtered, "masked_count": len(matches)}
    else:
        filtered, count = sanitize_messages(body.get("messages", []))
        return {"filter_type": ft, "messages": filtered, "masked_count": count}


# ===========================================================================
# CONFIG
# ===========================================================================

@app.get("/v1/config")
async def get_config(user: AuthenticatedUser = Depends(get_current_user)):
    return {"provider": settings.fallback_chain_list[0] if settings.fallback_chain_list else None}


@app.post("/v1/config")
async def set_config(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    body = await request.json()
    # Runtime config changes (limited)
    return {"message": "Config updated", "changes": body}


@app.post("/v1/config/reload")
async def reload_config(user: AuthenticatedUser = Depends(verify_admin)):
    from filter_db import get_all_patterns
    from data_filter import reload_patterns
    patterns = await get_all_patterns()
    reload_patterns(patterns)
    return {"message": "Config reloaded"}


# ===========================================================================
# CLIENTS
# ===========================================================================

@app.get("/v1/clients")
async def list_clients(user: AuthenticatedUser = Depends(verify_admin)):
    from clients import get_all_clients
    clients = await get_all_clients()
    return [c.model_dump(mode="json") for c in clients]


@app.post("/v1/clients")
async def create_client_endpoint(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from clients import create_client
    from models import ClientCreate
    body = await request.json()
    data = ClientCreate(**body)
    resp, key = await create_client(data)
    return {"client": resp.model_dump(mode="json"), "api_key": key}


@app.get("/v1/clients/{client_id}")
async def get_client_endpoint(client_id: str, user: AuthenticatedUser = Depends(verify_admin)):
    from clients import get_client_by_id
    c = await get_client_by_id(client_id)
    if not c:
        raise HTTPException(404, "Client not found")
    return c.model_dump(mode="json")


@app.put("/v1/clients/{client_id}")
async def update_client_endpoint(client_id: str, request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from clients import update_client
    from models import ClientUpdate
    body = await request.json()
    data = ClientUpdate(**body)
    c = await update_client(client_id, data)
    if not c:
        raise HTTPException(404, "Client not found")
    return c.model_dump(mode="json")


@app.delete("/v1/clients/{client_id}")
async def delete_client_endpoint(client_id: str, user: AuthenticatedUser = Depends(verify_admin)):
    from clients import delete_client
    if not await delete_client(client_id):
        raise HTTPException(404, "Client not found")
    return {"message": "Client deleted"}


@app.post("/v1/clients/{client_id}/regenerate-key")
async def regen_key(client_id: str, user: AuthenticatedUser = Depends(verify_admin)):
    from clients import regenerate_api_key
    result = await regenerate_api_key(client_id)
    if not result:
        raise HTTPException(404, "Client not found")
    resp, key = result
    return {"client": resp.model_dump(mode="json"), "api_key": key}


@app.get("/v1/clients/{client_id}/quota")
async def client_quota(client_id: str, user: AuthenticatedUser = Depends(verify_admin)):
    from clients import get_quota_usage
    return (await get_quota_usage(client_id)).model_dump(mode="json")


@app.post("/v1/clients/{client_id}/quota/reset")
async def client_quota_reset(client_id: str, user: AuthenticatedUser = Depends(verify_admin)):
    from clients import reset_quota
    await reset_quota(client_id)
    return {"message": "Quota reset"}


# ===========================================================================
# API KEY POOLS
# ===========================================================================

@app.get("/v1/api-keys/{provider}")
async def list_keys(provider: str, user: AuthenticatedUser = Depends(verify_admin)):
    from key_pool import get_key_pool_manager
    pool = get_key_pool_manager().get_pool(provider)
    return [e.model_dump(mode="json") for e in pool.get_health()]


@app.post("/v1/api-keys/{provider}")
async def add_key(provider: str, request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from key_pool import get_key_pool_manager
    body = await request.json()
    entry = await get_key_pool_manager().add_key_to_db(provider, body["key"])
    return entry.model_dump(mode="json")


@app.delete("/v1/api-keys/{provider}/{index}")
async def remove_key(provider: str, index: int, user: AuthenticatedUser = Depends(verify_admin)):
    from key_pool import get_key_pool_manager
    if not await get_key_pool_manager().remove_key_from_db(provider, index):
        raise HTTPException(404, "Key not found")
    return {"message": "Key removed"}


# ===========================================================================
# CIRCUIT BREAKERS
# ===========================================================================

@app.get("/v1/circuit-breakers")
async def list_breakers(user: AuthenticatedUser = Depends(get_current_user)):
    from circuit_breaker import get_circuit_breaker_manager
    return get_circuit_breaker_manager().get_all_states()


@app.post("/v1/circuit-breakers/{provider}/reset")
async def reset_breaker(provider: str, user: AuthenticatedUser = Depends(verify_admin)):
    from circuit_breaker import get_circuit_breaker_manager
    get_circuit_breaker_manager().reset(provider)
    return {"message": f"Circuit breakers reset for {provider}"}


# ===========================================================================
# FILTER PATTERNS
# ===========================================================================

@app.get("/v1/filter-patterns")
async def list_patterns(user: AuthenticatedUser = Depends(verify_admin)):
    from filter_db import get_all_patterns
    return await get_all_patterns()


@app.post("/v1/filter-patterns")
async def create_pattern(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from filter_db import create_pattern
    body = await request.json()
    return await create_pattern(body["name"], body["pattern"], body.get("replacement", "[REDACTED]"), body.get("flags", "IGNORECASE"))


@app.put("/v1/filter-patterns/{pid}")
async def update_pattern(pid: str, request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from db import FilterPattern, get_db
    from sqlalchemy import select
    body = await request.json()
    async with get_db() as db:
        result = await db.execute(select(FilterPattern).where(FilterPattern.id == pid))
        row = result.scalars().first()
        if not row:
            raise HTTPException(404, "Pattern not found")
        if "is_active" in body:
            row.is_active = body["is_active"]
        if "name" in body:
            row.name = body["name"]
        if "pattern" in body:
            row.pattern = body["pattern"]
        if "replacement" in body:
            row.replacement = body["replacement"]
        if "flags" in body:
            row.flags = body["flags"]
    return {"message": "Pattern updated"}


@app.delete("/v1/filter-patterns/{pid}")
async def delete_pattern_ep(pid: str, user: AuthenticatedUser = Depends(verify_admin)):
    from filter_db import delete_pattern
    if not await delete_pattern(pid):
        raise HTTPException(404, "Pattern not found or is built-in")
    return {"message": "Pattern deleted"}


@app.post("/v1/filter-patterns/test")
async def test_pattern(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from data_filter import test_text
    body = await request.json()
    return test_text(body.get("text", ""))


# ===========================================================================
# ALIASES
# ===========================================================================

@app.get("/v1/aliases")
async def list_aliases(user: AuthenticatedUser = Depends(get_current_user)):
    from model_alias import get_all_aliases
    return [a.model_dump(mode="json") for a in await get_all_aliases(include_inactive=True)]


@app.post("/v1/aliases")
async def create_alias_ep(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from model_alias import create_alias
    from models import ModelAliasCreate
    body = await request.json()
    a = await create_alias(ModelAliasCreate(**body))
    return a.model_dump(mode="json")


@app.put("/v1/aliases/{aid}")
async def update_alias_ep(aid: str, request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from model_alias import update_alias
    from models import ModelAliasUpdate
    body = await request.json()
    a = await update_alias(aid, ModelAliasUpdate(**body))
    if not a:
        raise HTTPException(404, "Alias not found")
    return a.model_dump(mode="json")


@app.delete("/v1/aliases/{aid}")
async def delete_alias_ep(aid: str, user: AuthenticatedUser = Depends(verify_admin)):
    from model_alias import delete_alias
    if not await delete_alias(aid):
        raise HTTPException(404, "Alias not found")
    return {"message": "Alias deleted"}


# ===========================================================================
# COSTS
# ===========================================================================

@app.get("/v1/costs/rates")
async def list_cost_rates(user: AuthenticatedUser = Depends(verify_admin)):
    from cost_tracker import get_all_rates
    return [r.model_dump(mode="json") for r in await get_all_rates()]


@app.post("/v1/costs/rates")
async def create_cost_rate(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from cost_tracker import create_rate
    from models import CostRateCreate
    body = await request.json()
    r = await create_rate(CostRateCreate(**body))
    return r.model_dump(mode="json")


@app.put("/v1/costs/rates/{rid}")
async def update_cost_rate(rid: str, request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from db import CostRateDB, get_db
    from sqlalchemy import select
    body = await request.json()
    async with get_db() as db:
        result = await db.execute(select(CostRateDB).where(CostRateDB.id == rid))
        row = result.scalars().first()
        if not row:
            raise HTTPException(404, "Rate not found")
        if "input_cost_per_1k" in body:
            row.input_cost_per_1k = body["input_cost_per_1k"]
        if "output_cost_per_1k" in body:
            row.output_cost_per_1k = body["output_cost_per_1k"]
        if "provider" in body:
            row.provider = body["provider"]
        if "model" in body:
            row.model = body["model"]
    return {"message": "Rate updated"}


@app.delete("/v1/costs/rates/{rid}")
async def deactivate_cost_rate(rid: str, user: AuthenticatedUser = Depends(verify_admin)):
    from cost_tracker import deactivate_rate
    if not await deactivate_rate(rid):
        raise HTTPException(404, "Rate not found")
    return {"message": "Rate deactivated"}


@app.get("/v1/costs/summary")
async def cost_summary(
    client_id: str | None = None, provider: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
    user: AuthenticatedUser = Depends(verify_admin),
):
    from cost_tracker import get_cost_summary
    df = datetime.fromisoformat(date_from) if date_from else None
    dt = datetime.fromisoformat(date_to) if date_to else None
    s = await get_cost_summary(client_id=client_id, provider=provider, date_from=df, date_to=dt)
    return s.model_dump(mode="json")


# ===========================================================================
# ALERTS
# ===========================================================================

@app.get("/v1/alerts/configs")
async def list_alert_configs(user: AuthenticatedUser = Depends(verify_admin)):
    from alerting import get_all_configs
    return [c.model_dump(mode="json") for c in await get_all_configs()]


@app.post("/v1/alerts/configs")
async def create_alert_config(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from alerting import create_config
    from models import AlertConfigCreate
    body = await request.json()
    c = await create_config(AlertConfigCreate(**body))
    return c.model_dump(mode="json")


@app.put("/v1/alerts/configs/{cid}")
async def update_alert_config(cid: str, request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from alerting import update_config
    body = await request.json()
    c = await update_config(cid, **body)
    if not c:
        raise HTTPException(404)
    return c.model_dump(mode="json")


@app.delete("/v1/alerts/configs/{cid}")
async def delete_alert_config(cid: str, user: AuthenticatedUser = Depends(verify_admin)):
    from alerting import delete_config
    if not await delete_config(cid):
        raise HTTPException(404)
    return {"message": "Alert config deleted"}


@app.post("/v1/alerts/configs/{cid}/test")
async def test_alert(cid: str, user: AuthenticatedUser = Depends(verify_admin)):
    from alerting import test_webhook
    return await test_webhook(cid)


@app.get("/v1/alerts/history")
async def alert_history(limit: int = 50, config_id: str | None = None, user: AuthenticatedUser = Depends(verify_admin)):
    from alerting import get_alert_history
    return [h.model_dump(mode="json") for h in await get_alert_history(limit, config_id)]


# ===========================================================================
# REQUEST LOGS
# ===========================================================================

@app.get("/v1/request-logs")
async def list_request_logs(
    limit: int = 100, client_id: str | None = None, provider: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
    user: AuthenticatedUser = Depends(verify_admin),
):
    from request_logger import get_request_logs
    df = datetime.fromisoformat(date_from) if date_from else None
    dt = datetime.fromisoformat(date_to) if date_to else None
    return await get_request_logs(limit, client_id, provider, df, dt)


@app.get("/v1/request-logs/{request_id}")
async def get_request_log(request_id: str, user: AuthenticatedUser = Depends(verify_admin)):
    from request_logger import get_request_log_by_id
    r = await get_request_log_by_id(request_id)
    if not r:
        raise HTTPException(404)
    return r


@app.delete("/v1/request-logs/cleanup")
async def cleanup_request_logs(older_than_days: int = 30, user: AuthenticatedUser = Depends(verify_admin)):
    from request_logger import delete_old_request_logs
    count = await delete_old_request_logs(older_than_days)
    return {"deleted": count}


@app.post("/v1/request-logs/toggle")
async def toggle_request_logs(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from request_logger import toggle_request_logging
    body = await request.json()
    await toggle_request_logging(body.get("enabled", False))
    return {"enabled": body.get("enabled", False)}


# ===========================================================================
# SECURITY (admin)
# ===========================================================================

@app.get("/v1/admin/security/rotate-check")
async def rotate_check(user: AuthenticatedUser = Depends(verify_admin)):
    from key_pool import get_key_pool_manager
    status = await get_key_pool_manager().get_rotation_status()
    return status.model_dump(mode="json")


@app.post("/v1/admin/security/rotate-now")
async def rotate_now(user: AuthenticatedUser = Depends(verify_admin)):
    from key_pool import get_key_pool_manager
    count = await get_key_pool_manager().re_encrypt_stale_keys()
    return {"re_encrypted": count}


@app.post("/v1/admin/security/validate-password")
async def validate_password_ep(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from security import validate_password
    body = await request.json()
    valid, errors = validate_password(body.get("password", ""), settings.PASSWORD_MIN_LENGTH)
    return {"valid": valid, "errors": errors}


# ===========================================================================
# USER MANAGEMENT
# ===========================================================================

@app.get("/v1/admin/users")
async def list_users(user: AuthenticatedUser = Depends(verify_admin)):
    from db import LocalUser, get_db
    from sqlalchemy import select
    async with get_db() as db:
        result = await db.execute(select(LocalUser).order_by(LocalUser.created_at.desc()))
        rows = result.scalars().all()
    return [
        {
            "id": u.id, "username": u.username, "roles": u.roles or [],
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        }
        for u in rows
    ]


@app.post("/v1/admin/users")
async def create_user_endpoint(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from db import LocalUser, PasswordHistory, get_db
    from sqlalchemy import select
    from password_utils import hash_password
    from security import validate_password
    from datetime import datetime, timezone

    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    roles = body.get("roles", ["viewer"])

    if not username:
        raise HTTPException(400, "Username is required")

    valid, errors = validate_password(password, settings.PASSWORD_MIN_LENGTH)
    if not valid:
        raise HTTPException(400, detail=f"Password invalid: {', '.join(errors)}")

    async with get_db() as db:
        existing = await db.execute(select(LocalUser).where(LocalUser.username == username))
        if existing.scalars().first():
            raise HTTPException(409, f"User '{username}' already exists")

        hashed = hash_password(password, rounds=12)
        new_user = LocalUser(
            username=username, password_hash=hashed, roles=roles,
            is_active=True, created_at=datetime.now(timezone.utc),
        )
        db.add(new_user)
        await db.flush()
        db.add(PasswordHistory(user_id=new_user.id, password_hash=hashed))
        uid = new_user.id

    return {"id": uid, "username": username, "roles": roles, "message": "User created"}


@app.put("/v1/admin/users/{user_id}")
async def update_user_endpoint(user_id: str, request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from db import LocalUser, get_db
    from sqlalchemy import select

    body = await request.json()
    async with get_db() as db:
        result = await db.execute(select(LocalUser).where(LocalUser.id == user_id))
        u = result.scalars().first()
        if not u:
            raise HTTPException(404, "User not found")
        if "roles" in body:
            u.roles = body["roles"]
        if "is_active" in body:
            u.is_active = body["is_active"]
    return {"message": "User updated"}


@app.delete("/v1/admin/users/{user_id}")
async def delete_user_endpoint(user_id: str, user: AuthenticatedUser = Depends(verify_admin)):
    from db import LocalUser, get_db
    from sqlalchemy import select

    async with get_db() as db:
        result = await db.execute(select(LocalUser).where(LocalUser.id == user_id))
        u = result.scalars().first()
        if not u:
            raise HTTPException(404, "User not found")
        if u.username == user.name:
            raise HTTPException(400, "Cannot delete your own account")
        await db.delete(u)
    return {"message": "User deleted"}


@app.post("/v1/admin/users/{user_id}/reset-password")
async def reset_user_password(user_id: str, request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from db import LocalUser, PasswordHistory, get_db
    from sqlalchemy import select
    from password_utils import hash_password
    from security import validate_password

    body = await request.json()
    new_password = body.get("password", "")

    valid, errors = validate_password(new_password, settings.PASSWORD_MIN_LENGTH)
    if not valid:
        raise HTTPException(400, detail=f"Password invalid: {', '.join(errors)}")

    async with get_db() as db:
        result = await db.execute(select(LocalUser).where(LocalUser.id == user_id))
        u = result.scalars().first()
        if not u:
            raise HTTPException(404, "User not found")
        hashed = hash_password(new_password, rounds=12)
        u.password_hash = hashed
        db.add(PasswordHistory(user_id=u.id, password_hash=hashed))
    return {"message": f"Password reset for {u.username}"}


@app.get("/v1/admin/ldap/status")
async def ldap_status(user: AuthenticatedUser = Depends(verify_admin)):
    return {
        "enabled": settings.LDAP_ENABLED,
        "server": settings.LDAP_SERVER if settings.LDAP_ENABLED else None,
        "base_dn": settings.LDAP_BASE_DN if settings.LDAP_ENABLED else None,
        "bind_dn": settings.LDAP_BIND_DN if settings.LDAP_ENABLED else None,
        "use_ssl": settings.LDAP_USE_SSL,
        "verify_cert": settings.LDAP_VERIFY_CERT,
        "admin_group": settings.LDAP_ADMIN_GROUP if settings.LDAP_ENABLED else None,
        "operator_group": settings.LDAP_OPERATOR_GROUP if settings.LDAP_ENABLED else None,
        "cache_ttl": settings.LDAP_CACHE_TTL_SECONDS,
    }


@app.post("/v1/admin/ldap/test")
async def ldap_test(user: AuthenticatedUser = Depends(verify_admin)):
    if not settings.LDAP_ENABLED:
        return {"success": False, "error": "LDAP is not enabled"}
    try:
        from ldap3 import Server, Connection, ALL, Tls
        import ssl
        tls_config = None
        if settings.LDAP_USE_SSL:
            tls_config = Tls(validate=ssl.CERT_REQUIRED if settings.LDAP_VERIFY_CERT else ssl.CERT_NONE)
        server = Server(settings.LDAP_SERVER, use_ssl=settings.LDAP_USE_SSL, tls=tls_config, get_info=ALL)
        conn = Connection(server, user=settings.LDAP_BIND_DN, password=settings.LDAP_BIND_PASSWORD, auto_bind=True)
        server_info = str(server.info) if server.info else "Connected"
        conn.unbind()
        return {"success": True, "server_info": server_info[:500]}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ===========================================================================
# IP BANS
# ===========================================================================

@app.get("/v1/admin/ip-bans")
async def list_ip_bans(user: AuthenticatedUser = Depends(verify_admin)):
    from security import get_banned_ips
    return get_banned_ips()


@app.post("/v1/admin/ip-ban/{ip}/unblock")
async def unblock_ip(ip: str, user: AuthenticatedUser = Depends(verify_admin)):
    from security import unban_ip
    if not unban_ip(ip):
        raise HTTPException(404, "IP not banned")
    return {"message": f"IP {ip} unbanned"}


# ===========================================================================
# TLS
# ===========================================================================

@app.post("/v1/admin/tls/reload")
async def reload_tls(user: AuthenticatedUser = Depends(verify_admin)):
    return {"message": "TLS reload not yet implemented for uvicorn runtime"}


# ===========================================================================
# AUDIT
# ===========================================================================

@app.get("/v1/admin/audit-logs")
async def list_audit_logs(limit: int = 100, event_type: str | None = None, user: AuthenticatedUser = Depends(verify_admin)):
    from audit import get_audit_logs
    return await get_audit_logs(limit, event_type)


# ===========================================================================
# BACKUP
# ===========================================================================

@app.post("/v1/admin/backup")
async def run_backup_ep(user: AuthenticatedUser = Depends(verify_admin)):
    from backup import run_backup
    info = await run_backup()
    return info.model_dump(mode="json")


@app.get("/v1/admin/backup/list")
async def list_backups_ep(user: AuthenticatedUser = Depends(verify_admin)):
    from backup import list_backups
    return [b.model_dump(mode="json") for b in list_backups()]


# ===========================================================================
# CONFIG EXPORT/IMPORT
# ===========================================================================

@app.get("/v1/admin/config/export")
async def export_config_ep(user: AuthenticatedUser = Depends(verify_admin)):
    from config_export import export_config
    data = await export_config()
    return data.model_dump(mode="json")


@app.get("/v1/admin/config/export/download")
async def export_config_download(user: AuthenticatedUser = Depends(verify_admin)):
    from config_export import export_config
    data = await export_config()
    content = json.dumps(data.model_dump(mode="json"), indent=2, default=str)
    return Response(
        content=content, media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=llm-sentinel-config.json"},
    )


@app.post("/v1/admin/config/import")
async def import_config_ep(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from config_export import import_config
    from models import ConfigExportData, ConfigImportOptions
    body = await request.json()
    data = ConfigExportData(**body.get("data", body))
    options = ConfigImportOptions(**body.get("options", {}))
    result = await import_config(data, options)
    return result.model_dump(mode="json")


# ===========================================================================
# PROVIDERS
# ===========================================================================

@app.get("/v1/providers")
async def list_providers(user: AuthenticatedUser = Depends(verify_admin)):
    from db import ProviderConfigDB, get_db
    from sqlalchemy import select
    async with get_db() as db:
        result = await db.execute(select(ProviderConfigDB).order_by(ProviderConfigDB.name))
        rows = result.scalars().all()
    return [{"id": r.id, "name": r.name, "provider_type": r.provider_type,
             "base_url": r.base_url, "default_model": r.default_model,
             "is_active": r.is_active, "config_json": r.config_json} for r in rows]


@app.post("/v1/providers")
async def create_provider(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from db import ProviderConfigDB, get_db
    body = await request.json()
    async with get_db() as db:
        row = ProviderConfigDB(
            name=body["name"], provider_type=body["provider_type"],
            base_url=body.get("base_url"), default_model=body.get("default_model", ""),
            config_json=body.get("config_json", {}),
        )
        db.add(row)
        await db.flush()
        return {"id": row.id, "name": row.name, "provider_type": row.provider_type}


@app.get("/v1/providers/{pid}")
async def get_provider_ep(pid: str, user: AuthenticatedUser = Depends(verify_admin)):
    from db import ProviderConfigDB, get_db
    from sqlalchemy import select
    async with get_db() as db:
        result = await db.execute(select(ProviderConfigDB).where(ProviderConfigDB.id == pid))
        r = result.scalars().first()
    if not r:
        raise HTTPException(404)
    return {"id": r.id, "name": r.name, "provider_type": r.provider_type,
            "base_url": r.base_url, "default_model": r.default_model,
            "is_active": r.is_active, "config_json": r.config_json}


@app.put("/v1/providers/{pid}")
async def update_provider(pid: str, request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from db import ProviderConfigDB, get_db
    from sqlalchemy import select
    body = await request.json()
    async with get_db() as db:
        result = await db.execute(select(ProviderConfigDB).where(ProviderConfigDB.id == pid))
        row = result.scalars().first()
        if not row:
            raise HTTPException(404)
        for k in ("name", "base_url", "default_model", "is_active", "config_json"):
            if k in body:
                setattr(row, k, body[k])
    return {"message": "Provider updated"}


@app.delete("/v1/providers/{pid}")
async def delete_provider(pid: str, user: AuthenticatedUser = Depends(verify_admin)):
    from db import ProviderConfigDB, get_db
    from sqlalchemy import select
    async with get_db() as db:
        result = await db.execute(select(ProviderConfigDB).where(ProviderConfigDB.id == pid))
        row = result.scalars().first()
        if not row:
            raise HTTPException(404)
        row.is_active = False
    return {"message": "Provider deactivated"}


@app.post("/v1/providers/{pid}/test")
async def test_provider(pid: str, user: AuthenticatedUser = Depends(verify_admin)):
    from db import ProviderConfigDB, get_db
    from sqlalchemy import select
    from providers import get_provider
    async with get_db() as db:
        result = await db.execute(select(ProviderConfigDB).where(ProviderConfigDB.id == pid))
        row = result.scalars().first()
    if not row:
        raise HTTPException(404)
    try:
        adapter = get_provider(row.provider_type, base_url=row.base_url or "", config=row.config_json, timeout=10)
        models = await adapter.list_models()
        return {"success": True, "models": models}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ===========================================================================
# CONTENT POLICIES
# ===========================================================================

@app.get("/v1/policies")
async def list_policies(user: AuthenticatedUser = Depends(verify_admin)):
    from guardrails import get_all_policies
    return [p.model_dump(mode="json") for p in await get_all_policies()]


@app.post("/v1/policies")
async def create_policy_ep(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from guardrails import create_policy
    from models import ContentPolicyCreate
    body = await request.json()
    p = await create_policy(ContentPolicyCreate(**body))
    return p.model_dump(mode="json")


@app.get("/v1/policies/{pid}")
async def get_policy(pid: str, user: AuthenticatedUser = Depends(verify_admin)):
    from db import ContentPolicyDB, get_db
    from sqlalchemy import select
    async with get_db() as db:
        result = await db.execute(select(ContentPolicyDB).where(ContentPolicyDB.id == pid))
        r = result.scalars().first()
    if not r:
        raise HTTPException(404)
    return {"id": r.id, "name": r.name, "policy_type": r.policy_type,
            "config_json": r.config_json, "is_active": r.is_active,
            "applies_to_clients": r.applies_to_clients, "priority": r.priority}


@app.put("/v1/policies/{pid}")
async def update_policy_ep(pid: str, request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from guardrails import update_policy
    from models import ContentPolicyUpdate
    body = await request.json()
    p = await update_policy(pid, ContentPolicyUpdate(**body))
    if not p:
        raise HTTPException(404)
    return p.model_dump(mode="json")


@app.delete("/v1/policies/{pid}")
async def delete_policy_ep(pid: str, user: AuthenticatedUser = Depends(verify_admin)):
    from guardrails import delete_policy
    if not await delete_policy(pid):
        raise HTTPException(404)
    return {"message": "Policy deleted"}


@app.post("/v1/policies/{pid}/test")
async def test_policy_ep(pid: str, request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from guardrails import test_policy
    body = await request.json()
    result = await test_policy(pid, body.get("messages", []), body.get("model", "test-model"))
    return result.model_dump(mode="json")


# ===========================================================================
# CACHE
# ===========================================================================

@app.get("/v1/cache/stats")
async def cache_stats(user: AuthenticatedUser = Depends(verify_admin)):
    from caching import get_cache_stats
    return (await get_cache_stats()).model_dump(mode="json")


@app.post("/v1/cache/invalidate")
async def cache_invalidate(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    from caching import invalidate_cache
    body = await request.json()
    count = await invalidate_cache(body.get("pattern", "*"))
    return {"deleted": count}


@app.post("/v1/cache/toggle")
async def cache_toggle(request: Request, user: AuthenticatedUser = Depends(verify_admin)):
    body = await request.json()
    settings.CACHE_ENABLED = body.get("enabled", False)
    return {"enabled": settings.CACHE_ENABLED}


# ===========================================================================
# QUEUE
# ===========================================================================

@app.get("/v1/queue/status")
async def queue_status(user: AuthenticatedUser = Depends(get_current_user)):
    from rate_limiter import get_queue_status
    return get_queue_status()


# ===========================================================================
# LIVE SESSIONS (WebSocket + REST)
# ===========================================================================

@app.websocket("/v1/admin/ws/sessions")
async def ws_sessions(ws: WebSocket):
    token = ws.query_params.get("token")
    if not token:
        await ws.close(code=4001)
        return
    try:
        from auth import decode_token
        payload = await decode_token(token)
        if "admin" not in payload.get("roles", []):
            await ws.close(code=4003)
            return
    except Exception:
        await ws.close(code=4001)
        return

    await ws.accept()
    from session_manager import session_manager
    await session_manager.subscribe(ws)
    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await session_manager.unsubscribe(ws)


@app.get("/v1/admin/sessions")
async def list_sessions(user: AuthenticatedUser = Depends(verify_admin)):
    from session_manager import session_manager
    sessions = await session_manager.get_all()
    return [s.model_dump(mode="json") for s in sessions]


# ===========================================================================
# ADMIN UI
# ===========================================================================

@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page():
    return _admin_login_html()


@app.post("/admin/login")
async def admin_login_post(request: Request):
    from auth import local_authenticate, create_token_pair
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    ip = _get_client_ip(request)
    ua = request.headers.get("user-agent")

    success, roles = await local_authenticate(str(username), str(password), ip)
    if not success:
        return HTMLResponse(_admin_login_html(error="Invalid username or password"), status_code=401)

    pair = await create_token_pair(str(username), roles, ip=ip, user_agent=ua)
    response = RedirectResponse("/admin/dashboard", status_code=303)
    max_age = settings.JWT_EXPIRY_HOURS * 3600
    # HttpOnly cookie for API calls (secure, not readable by JS)
    response.set_cookie(
        "access_token", pair.access_token,
        httponly=True, max_age=max_age, samesite="lax",
    )
    # JS-readable cookie for WebSocket connection only
    response.set_cookie(
        "ws_token", pair.access_token,
        httponly=False, max_age=max_age, samesite="lax",
    )
    return response


@app.get("/admin", response_class=RedirectResponse)
async def admin_redirect():
    return RedirectResponse("/admin/dashboard")


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    # Check auth via cookie
    token = request.cookies.get("access_token")
    if not token:
        return RedirectResponse("/admin/login")
    try:
        from auth import decode_token
        payload = await decode_token(token, _get_client_ip(request))
        if "admin" not in payload.get("roles", []):
            return RedirectResponse("/admin/login")
    except Exception:
        return RedirectResponse("/admin/login")
    return HTMLResponse(_admin_dashboard_html())


# ===========================================================================
# Admin HTML templates
# ===========================================================================

def _admin_login_html(error: str = "") -> str:
    err_div = f'<div class="alert-error">{error}</div>' if error else ""
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>onPrem LLM Sentinel — Sign In</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#090b10;color:#e8eaf0;font-family:'Inter',system-ui,-apple-system,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;-webkit-font-smoothing:antialiased}}
body::before{{content:'';position:fixed;inset:0;background:radial-gradient(ellipse at 50% 0%,rgba(139,92,246,.08) 0%,transparent 60%);pointer-events:none}}
.card{{background:#151921;border:1px solid #1e2536;padding:2.5rem;border-radius:18px;width:400px;box-shadow:0 8px 40px rgba(0,0,0,.5);position:relative;overflow:hidden}}
.card::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#8b5cf6,#06b6d4)}}
.brand{{text-align:center;margin-bottom:2rem}}
.brand h1{{font-size:1.4rem;font-weight:700;background:linear-gradient(135deg,#a78bfa,#06b6d4);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.brand span{{display:block;font-size:.7rem;color:#5c6478;margin-top:4px;letter-spacing:.06em;text-transform:uppercase}}
label{{display:block;font-size:.78rem;color:#8b92a8;margin-bottom:.35rem;margin-top:1.1rem;font-weight:500}}
input{{width:100%;padding:.7rem .9rem;border:1px solid #1e2536;border-radius:8px;background:#090b10;color:#e8eaf0;font-size:.9rem;transition:all .2s}}
input:focus{{outline:none;border-color:#8b5cf6;box-shadow:0 0 0 3px rgba(139,92,246,.15)}}
button[type=submit]{{width:100%;margin-top:1.8rem;padding:.75rem;background:linear-gradient(135deg,#8b5cf6,#6d28d9);color:#fff;border:none;border-radius:8px;font-size:.9rem;cursor:pointer;font-weight:600;transition:all .2s;letter-spacing:.02em}}
button[type=submit]:hover{{transform:translateY(-1px);box-shadow:0 4px 16px rgba(139,92,246,.3)}}
button[type=submit]:active{{transform:translateY(0)}}
.alert-error{{background:rgba(239,68,68,.1);color:#f87171;padding:.65rem .9rem;border-radius:8px;font-size:.82rem;margin-bottom:1rem;border:1px solid rgba(239,68,68,.2);display:flex;align-items:center;gap:.5rem}}
.alert-error::before{{content:'\\26A0';font-size:1rem}}
.hint{{margin-top:1.2rem;font-size:.72rem;color:#3f4557;text-align:center;line-height:1.5}}
.hint code{{background:#1e2536;padding:.15rem .4rem;border-radius:4px;font-size:.7rem;color:#8b92a8}}
</style></head><body>
<div class="card">
<div class="brand">
<h1>onPrem LLM Sentinel</h1>
<span>Enterprise Gateway</span>
</div>
{err_div}
<form method="post" action="/admin/login">
<label>Username</label><input name="username" required autofocus placeholder="admin">
<label>Password</label><input name="password" type="password" required placeholder="Enter password">
<button type="submit">Sign In</button>
</form>
<p class="hint">First time? Run <code>python main.py --create-admin</code></p>
</div></body></html>"""


def _admin_dashboard_html() -> str:
    html_path = Path(__file__).parent / "static" / "admin.html"
    return html_path.read_text(encoding="utf-8")


# ===========================================================================
# Server entry point
# ===========================================================================

if __name__ == "__main__":
    import uvicorn

    ssl_kwargs: dict[str, Any] = {}
    if settings.PROXY_TLS_ENABLED:
        ssl_kwargs["ssl_certfile"] = settings.PROXY_TLS_CERT
        ssl_kwargs["ssl_keyfile"] = settings.PROXY_TLS_KEY

    uvicorn.run(
        "main:app",
        host=settings.PROXY_HOST,
        port=settings.PROXY_PORT,
        workers=settings.PROXY_WORKERS,
        log_level=settings.PROXY_LOG_LEVEL.lower(),
        **ssl_kwargs,
    )
