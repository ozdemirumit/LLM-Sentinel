"""Ollama provider adapter (REST API via httpx)."""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator

import httpx

from logger import get_logger
from models import OAIChatResponse, OAIEmbeddingResponse, OAIEmbeddingData, OAIEmbeddingUsage
from providers.base import BaseLLMProvider

log = get_logger(__name__)


class OllamaProvider(BaseLLMProvider):
    provider_type = "ollama"

    @property
    def _base(self) -> str:
        return (self.base_url or "http://localhost:11434").rstrip("/")

    async def chat(self, messages, model, temperature=None, max_tokens=None,
                   tools=None, tool_choice=None, stop=None, **kwargs) -> OAIChatResponse:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        options: dict[str, Any] = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        if options:
            payload["options"] = options

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self._base}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

        msg = data.get("message", {})
        content = msg.get("content", "")

        # Ollama token counts
        input_tokens = data.get("prompt_eval_count", 0) or 0
        output_tokens = data.get("eval_count", 0) or 0

        return self._make_oai_response(
            content=content, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
        )

    async def stream_chat(self, messages, model, temperature=None, max_tokens=None,
                          tools=None, tool_choice=None, stop=None, **kwargs) -> AsyncGenerator[dict, None]:
        import uuid
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        options: dict[str, Any] = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        if options:
            payload["options"] = options

        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        yield self._make_stream_chunk(role="assistant", model=model, chunk_id=chunk_id)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", f"{self._base}/api/chat", json=payload) as resp:
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        msg = data.get("message", {})
                        content = msg.get("content", "")
                        if content:
                            yield self._make_stream_chunk(content=content, model=model, chunk_id=chunk_id)
                        if data.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue

        yield self._make_stream_chunk(finish_reason="stop", model=model, chunk_id=chunk_id)

    async def embeddings(self, input_texts, model, **kwargs) -> OAIEmbeddingResponse:
        data = []
        for i, text in enumerate(input_texts):
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self._base}/api/embeddings",
                    json={"model": model, "prompt": text},
                )
                resp.raise_for_status()
                result = resp.json()
            data.append(OAIEmbeddingData(embedding=result.get("embedding", []), index=i))

        return OAIEmbeddingResponse(
            data=data, model=model,
            usage=OAIEmbeddingUsage(prompt_tokens=0, total_tokens=0),
        )

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self._base}/api/tags")
                resp.raise_for_status()
                data = resp.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []
