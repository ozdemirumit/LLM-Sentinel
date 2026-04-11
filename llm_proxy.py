"""
LLM Sentinel — main orchestrator.

Routes requests to providers via adapters, handles retry/fallback,
alias resolution, context window protection, and session tracking.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncGenerator

from config import settings
from logger import get_logger

log = get_logger(__name__)


# ==========================================================================
# Token estimation
# ==========================================================================

_tiktoken_encoder: Any = None
_tiktoken_available: bool | None = None


def _get_tiktoken():
    global _tiktoken_encoder, _tiktoken_available
    if _tiktoken_available is None:
        try:
            import tiktoken
            _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
            _tiktoken_available = True
        except Exception:
            _tiktoken_available = False
    return _tiktoken_encoder


def estimate_tokens(text: str) -> int:
    """Estimate token count. Uses tiktoken if available, else len//4."""
    if not text:
        return 0
    enc = _get_tiktoken()
    if enc:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return max(len(text) // 4, 1)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens for a message list."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += estimate_tokens(part["text"])
        total += 4  # Per-message overhead
    return total


# ==========================================================================
# Context window protection
# ==========================================================================

def truncate_messages(
    messages: list[dict],
    max_tokens: int,
) -> tuple[list[dict], bool]:
    """
    Truncate messages to fit within max_tokens.
    Preserves system prompt. Removes oldest non-system messages first.
    Returns (truncated_messages, was_truncated).
    """
    current = estimate_messages_tokens(messages)
    if current <= max_tokens:
        return messages, False

    # Separate system and non-system
    system_msgs = [m for m in messages if m.get("role") == "system"]
    other_msgs = [m for m in messages if m.get("role") != "system"]

    system_tokens = estimate_messages_tokens(system_msgs)
    budget = max_tokens - system_tokens

    if budget <= 0:
        # Even system prompt exceeds limit; keep only system
        return system_msgs, True

    # Keep messages from newest to oldest until budget exceeded
    kept: list[dict] = []
    used = 0
    for m in reversed(other_msgs):
        msg_tokens = estimate_tokens(m.get("content", "")) + 4
        if used + msg_tokens > budget:
            break
        kept.insert(0, m)
        used += msg_tokens

    result = system_msgs + kept
    was_truncated = len(result) < len(messages)

    if was_truncated:
        log.info(
            "Messages truncated for context window",
            extra={
                "original_messages": len(messages),
                "kept_messages": len(result),
                "max_tokens": max_tokens,
            },
        )

    return result, was_truncated


# ==========================================================================
# Model alias resolution
# ==========================================================================

async def resolve_model(
    provider_hint: str | None,
    model_hint: str | None,
    db_session: Any = None,
) -> tuple[str, str, str | None]:
    """
    Resolve provider and model from hints, checking aliases.

    Returns (provider, model, alias_used).
    """
    from db import ModelAliasDB, get_db
    from sqlalchemy import select

    alias_used = None

    if model_hint:
        # Check alias table
        try:
            if db_session:
                db = db_session
                result = await db.execute(
                    select(ModelAliasDB).where(
                        ModelAliasDB.alias == model_hint,
                        ModelAliasDB.is_active == True,
                    )
                )
                alias_row = result.scalars().first()
            else:
                async with get_db() as db:
                    result = await db.execute(
                        select(ModelAliasDB).where(
                            ModelAliasDB.alias == model_hint,
                            ModelAliasDB.is_active == True,
                        )
                    )
                    alias_row = result.scalars().first()

            if alias_row:
                alias_used = model_hint
                provider_hint = alias_row.provider
                model_hint = alias_row.model
                log.info(
                    "Alias resolved",
                    extra={"alias": alias_used, "provider": provider_hint, "model": model_hint},
                )
        except Exception as exc:
            log.warning("Alias lookup failed", extra={"error": str(exc)})

    # Auto-detect provider from model name
    if not provider_hint and model_hint:
        provider_hint = _infer_provider(model_hint)

    provider = provider_hint or settings.fallback_chain_list[0] if settings.fallback_chain_list else "anthropic"
    model = model_hint or _default_model(provider)

    return provider, model, alias_used


def _infer_provider(model: str) -> str:
    """Infer provider from model name prefix."""
    m = model.lower()
    if "claude" in m or m.startswith("anthropic"):
        return "anthropic"
    if m.startswith("gpt-") or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    if "gemini" in m:
        return "gemini"
    if "llama" in m or "mixtral" in m:
        return "groq"
    if "mistral" in m or "codestral" in m:
        return "mistral"
    if "bedrock" in m or m.startswith("anthropic."):
        return "bedrock"
    # Default to first in fallback chain
    if settings.fallback_chain_list:
        return settings.fallback_chain_list[0]
    return "anthropic"


def _default_model(provider: str) -> str:
    """Return a default model for a provider."""
    defaults = {
        "anthropic": "claude-sonnet-4-6",
        "openai": "gpt-4o",
        "azure_openai": "gpt-4o",
        "gemini": "gemini-1.5-pro",
        "groq": "llama-3.3-70b-versatile",
        "mistral": "mistral-large-latest",
        "ollama": "llama3",
        "bedrock": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "openai_compatible": "default",
    }
    return defaults.get(provider, "default")


def _get_timeout(provider: str) -> int:
    """Get configured timeout for a provider."""
    timeouts = {
        "anthropic": settings.ANTHROPIC_TIMEOUT_SECONDS,
        "openai": settings.OPENAI_TIMEOUT_SECONDS,
        "azure_openai": settings.OPENAI_TIMEOUT_SECONDS,
        "gemini": settings.GEMINI_TIMEOUT_SECONDS,
        "ollama": settings.OLLAMA_TIMEOUT_SECONDS,
    }
    return timeouts.get(provider, 120)


# ==========================================================================
# LLMProxy class
# ==========================================================================

class LLMProxy:
    """Main orchestrator for LLM requests."""

    def __init__(self, key_pool_manager=None, circuit_breaker_manager=None):
        from key_pool import get_key_pool_manager
        from circuit_breaker import get_circuit_breaker_manager

        self.key_pool = key_pool_manager or get_key_pool_manager()
        self.cb = circuit_breaker_manager or get_circuit_breaker_manager()

    # ------------------------------------------------------------------
    # OpenAI-compatible endpoints
    # ------------------------------------------------------------------

    async def chat_completions(
        self,
        messages: list[dict],
        model: str,
        provider: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        stop: Any = None,
        **kwargs: Any,
    ) -> "OAIChatResponse":
        """Non-streaming chat completion. Returns OAI format."""
        from models import OAIChatResponse

        return await self._chat_with_retry(
            provider=provider,
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            stop=stop,
            stream=False,
            **kwargs,
        )

    async def stream_chat_completions(
        self,
        messages: list[dict],
        model: str,
        provider: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        stop: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict, None]:
        """Streaming chat completion. Yields OAI chunk dicts."""
        async for chunk in self._stream_with_retry(
            provider=provider,
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            stop=stop,
            **kwargs,
        ):
            yield chunk

    async def create_embeddings(
        self,
        input_texts: list[str],
        model: str,
        provider: str,
        **kwargs: Any,
    ) -> "OAIEmbeddingResponse":
        """Create embeddings via the appropriate provider."""
        from models import OAIEmbeddingResponse
        from providers import get_provider

        pool = self.key_pool.get_pool(provider)
        result = pool.get_next_key(settings.KEY_ROTATION_STRATEGY)
        if result is None:
            raise ProviderError(f"No healthy API keys for {provider}", provider=provider)

        api_key, key_idx = result
        adapter = get_provider(
            provider, api_key=api_key, timeout=_get_timeout(provider),
        )

        try:
            response = await adapter.embeddings(input_texts, model, **kwargs)
            pool.mark_success(key_idx)
            return response
        except Exception as exc:
            pool.mark_error(key_idx)
            raise ProviderError(str(exc), provider=provider) from exc

    async def list_all_models(self) -> "OAIModelList":
        """Aggregate models from all active providers."""
        import time as _time
        from models import OAIModelList, OAIModelInfo

        models: list[OAIModelInfo] = []

        # Built-in provider models
        for provider_name in self.key_pool.providers:
            pool = self.key_pool.get_pool(provider_name)
            if pool.count == 0:
                continue
            try:
                from providers import get_provider
                result = pool.get_next_key(settings.KEY_ROTATION_STRATEGY)
                if result is None:
                    continue
                api_key, _ = result
                adapter = get_provider(provider_name, api_key=api_key, timeout=10)
                model_names = await adapter.list_models()
                for name in model_names:
                    models.append(OAIModelInfo(
                        id=name,
                        object="model",
                        created=int(_time.time()),
                        owned_by=provider_name,
                    ))
            except Exception:
                pass

        # Add aliases as virtual models
        try:
            from db import ModelAliasDB, get_db
            from sqlalchemy import select

            async with get_db() as db:
                result = await db.execute(
                    select(ModelAliasDB).where(ModelAliasDB.is_active == True)
                )
                aliases = result.scalars().all()
                for a in aliases:
                    models.append(OAIModelInfo(
                        id=a.alias,
                        object="model",
                        created=int(_time.time()),
                        owned_by=f"alias:{a.provider}",
                    ))
        except Exception:
            pass

        return OAIModelList(data=models)

    # ------------------------------------------------------------------
    # Native format (backward compatible)
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict],
        provider: str | None = None,
        model: str | None = None,
        tools: list[dict] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> "ChatResponse":
        """Native format chat (backward compatible)."""
        from models import ChatResponse, ContentBlock, UsageInfo

        # Inject system message if provided separately
        if system:
            messages = [{"role": "system", "content": system}] + messages

        prov, mdl, alias = await resolve_model(provider, model)
        oai_response = await self.chat_completions(
            messages=messages, model=mdl, provider=prov,
            tools=tools, **kwargs,
        )

        # Convert OAI response to native format
        content_blocks = []
        if oai_response.choices:
            choice = oai_response.choices[0]
            if choice.message.content:
                content_blocks.append(ContentBlock(type="text", text=choice.message.content))
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    content_blocks.append(ContentBlock(
                        type="tool_use",
                        id=tc.get("id"),
                        name=tc.get("function", {}).get("name"),
                        input=json.loads(tc.get("function", {}).get("arguments", "{}")),
                    ))

        return ChatResponse(
            stop_reason=oai_response.choices[0].finish_reason if oai_response.choices else "stop",
            content=content_blocks,
            usage=UsageInfo(
                input_tokens=oai_response.usage.prompt_tokens,
                output_tokens=oai_response.usage.completion_tokens,
            ),
            model=oai_response.model,
            provider=prov,
            alias_used=alias,
        )

    async def stream_chat(
        self,
        messages: list[dict],
        provider: str | None = None,
        model: str | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Native format streaming (SSE text chunks)."""
        if system:
            messages = [{"role": "system", "content": system}] + messages

        prov, mdl, alias = await resolve_model(provider, model)

        async for chunk in self.stream_chat_completions(
            messages=messages, model=mdl, provider=prov, **kwargs,
        ):
            choices = chunk.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                content = delta.get("content")
                if content:
                    yield content

    # ------------------------------------------------------------------
    # Retry / fallback logic
    # ------------------------------------------------------------------

    async def _chat_with_retry(
        self,
        provider: str,
        model: str,
        messages: list[dict],
        stream: bool = False,
        **kwargs: Any,
    ) -> "OAIChatResponse":
        """Execute chat with retry and fallback chain."""
        from providers import get_provider
        import httpx

        providers_to_try = [provider]
        # Add fallback providers
        for fb in settings.fallback_chain_list:
            if fb != provider and fb not in providers_to_try:
                providers_to_try.append(fb)

        last_error: Exception | None = None

        for prov in providers_to_try:
            pool = self.key_pool.get_pool(prov)

            for attempt in range(settings.MAX_RETRIES):
                result = pool.get_next_key(settings.KEY_ROTATION_STRATEGY)
                if result is None:
                    log.warning("No healthy keys", extra={"provider": prov})
                    break

                api_key, key_idx = result

                # Circuit breaker check
                if not self.cb.is_available(prov, key_idx):
                    continue

                adapter = get_provider(
                    prov, api_key=api_key, timeout=_get_timeout(prov),
                )

                try:
                    response = await adapter.chat(
                        messages=messages, model=model, **kwargs,
                    )
                    pool.mark_success(key_idx)
                    self.cb.record_success(prov, key_idx)
                    return response

                except Exception as exc:
                    last_error = exc
                    error_str = str(exc)
                    status_code = getattr(exc, "status_code", 0)

                    if status_code == 429:
                        # Rate limited
                        retry_after = getattr(exc, "retry_after", 60)
                        pool.mark_rate_limited(key_idx, int(retry_after) if retry_after else 60)
                        log.warning(
                            "Provider rate limited",
                            extra={"provider": prov, "key_index": key_idx, "attempt": attempt},
                        )
                        continue

                    if status_code and 400 <= status_code < 500 and status_code != 429:
                        # Client error — don't retry
                        pool.mark_error(key_idx)
                        self.cb.record_failure(prov, key_idx)
                        raise ProviderError(error_str, provider=prov, status_code=status_code) from exc

                    # Server error — retry with backoff
                    pool.mark_error(key_idx)
                    self.cb.record_failure(prov, key_idx)
                    log.warning(
                        "Provider error, retrying",
                        extra={
                            "provider": prov,
                            "key_index": key_idx,
                            "attempt": attempt,
                            "error": error_str[:200],
                        },
                    )

                    if attempt < settings.MAX_RETRIES - 1:
                        import asyncio
                        await asyncio.sleep(min(2 ** attempt, 10))

            # All retries exhausted for this provider
            log.warning("Provider exhausted", extra={"provider": prov})

        # All providers exhausted
        error_msg = f"All providers exhausted. Last error: {last_error}"
        raise ProviderError(error_msg, provider=provider, status_code=503)

    async def _stream_with_retry(
        self,
        provider: str,
        model: str,
        messages: list[dict],
        **kwargs: Any,
    ) -> AsyncGenerator[dict, None]:
        """Streaming with retry on first chunk failure."""
        from providers import get_provider

        providers_to_try = [provider]
        for fb in settings.fallback_chain_list:
            if fb != provider and fb not in providers_to_try:
                providers_to_try.append(fb)

        last_error: Exception | None = None

        for prov in providers_to_try:
            pool = self.key_pool.get_pool(prov)
            result = pool.get_next_key(settings.KEY_ROTATION_STRATEGY)
            if result is None:
                continue

            api_key, key_idx = result
            if not self.cb.is_available(prov, key_idx):
                continue

            adapter = get_provider(
                prov, api_key=api_key, timeout=_get_timeout(prov),
            )

            try:
                async for chunk in adapter.stream_chat(
                    messages=messages, model=model, **kwargs,
                ):
                    yield chunk

                pool.mark_success(key_idx)
                self.cb.record_success(prov, key_idx)
                return

            except Exception as exc:
                last_error = exc
                pool.mark_error(key_idx)
                self.cb.record_failure(prov, key_idx)
                log.warning(
                    "Stream error, trying next provider",
                    extra={"provider": prov, "error": str(exc)[:200]},
                )
                continue

        raise ProviderError(
            f"All providers exhausted for streaming. Last error: {last_error}",
            provider=provider, status_code=503,
        )


# ==========================================================================
# Exceptions
# ==========================================================================

class ProviderError(Exception):
    """Raised when a provider call fails."""

    def __init__(self, message: str, provider: str = "", status_code: int = 502):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


# ==========================================================================
# Module-level singleton
# ==========================================================================

_proxy: LLMProxy | None = None


def get_llm_proxy() -> LLMProxy:
    global _proxy
    if _proxy is None:
        _proxy = LLMProxy()
    return _proxy
