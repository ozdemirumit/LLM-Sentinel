"""
Abstract base class for LLM provider adapters.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

from models import OAIChatRequest, OAIChatResponse, OAIEmbeddingResponse


class BaseLLMProvider(ABC):
    """
    Abstract LLM provider adapter.

    Every provider must implement:
      - chat(): synchronous (non-streaming) completion
      - stream_chat(): streaming completion via async generator
      - list_models(): return available model names

    Optional:
      - embeddings(): for embedding-capable providers
    """

    provider_type: str = "base"

    def __init__(self, api_key: str = "", base_url: str = "", config: dict | None = None, timeout: int = 120):
        self.api_key = api_key
        self.base_url = base_url
        self.config = config or {}
        self.timeout = timeout

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        stop: Any = None,
        **kwargs: Any,
    ) -> OAIChatResponse:
        """Non-streaming chat completion. Returns OAI-format response."""
        ...

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        stop: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict, None]:
        """
        Streaming chat completion.
        Yields OAI-format stream chunk dicts.
        """
        ...
        yield {}  # type: ignore

    async def embeddings(
        self,
        input_texts: list[str],
        model: str,
        **kwargs: Any,
    ) -> OAIEmbeddingResponse:
        """Create embeddings. Override in providers that support it."""
        raise NotImplementedError(f"{self.provider_type} does not support embeddings")

    async def list_models(self) -> list[str]:
        """Return available model names. Override in providers that support it."""
        return []

    def _make_oai_response(
        self,
        content: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        finish_reason: str = "stop",
        tool_calls: list | None = None,
    ) -> OAIChatResponse:
        """Helper to build a standard OAIChatResponse."""
        import time
        import uuid

        from models import OAIChoice, OAIMessage, OAIUsage

        message = OAIMessage(
            role="assistant",
            content=content if not tool_calls else None,
            tool_calls=tool_calls,
        )

        return OAIChatResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            object="chat.completion",
            created=int(time.time()),
            model=model,
            choices=[OAIChoice(index=0, message=message, finish_reason=finish_reason)],
            usage=OAIUsage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
        )

    def _make_stream_chunk(
        self,
        content: str | None = None,
        model: str = "",
        finish_reason: str | None = None,
        role: str | None = None,
        tool_calls: list | None = None,
        chunk_id: str = "",
    ) -> dict:
        """Helper to build a stream chunk dict."""
        import time

        return {
            "id": chunk_id or f"chatcmpl-stream",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {
                    k: v for k, v in {
                        "role": role,
                        "content": content,
                        "tool_calls": tool_calls,
                    }.items() if v is not None
                },
                "finish_reason": finish_reason,
            }],
        }
