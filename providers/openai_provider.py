"""OpenAI provider adapter."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from logger import get_logger
from models import OAIChatResponse, OAIEmbeddingResponse, OAIEmbeddingData, OAIEmbeddingUsage
from providers.base import BaseLLMProvider

log = get_logger(__name__)


class OpenAIProvider(BaseLLMProvider):
    provider_type = "openai"

    def _get_client(self):
        import openai
        kwargs: dict[str, Any] = {"api_key": self.api_key, "timeout": self.timeout}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return openai.AsyncOpenAI(**kwargs)

    async def chat(self, messages, model, temperature=None, max_tokens=None,
                   tools=None, tool_choice=None, stop=None, **kwargs) -> OAIChatResponse:
        client = self._get_client()
        params: dict[str, Any] = {"model": model, "messages": messages}
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if tools:
            params["tools"] = tools
        if tool_choice:
            params["tool_choice"] = tool_choice
        if stop:
            params["stop"] = stop

        response = await client.chat.completions.create(**params)

        choice = response.choices[0] if response.choices else None
        content = choice.message.content if choice and choice.message else ""
        finish = choice.finish_reason if choice else "stop"
        tc = None
        if choice and choice.message and choice.message.tool_calls:
            tc = [
                {
                    "id": t.id,
                    "type": "function",
                    "function": {"name": t.function.name, "arguments": t.function.arguments},
                }
                for t in choice.message.tool_calls
            ]

        usage = response.usage
        return self._make_oai_response(
            content=content or "",
            model=response.model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            finish_reason=finish or "stop",
            tool_calls=tc,
        )

    async def stream_chat(self, messages, model, temperature=None, max_tokens=None,
                          tools=None, tool_choice=None, stop=None, **kwargs) -> AsyncGenerator[dict, None]:
        client = self._get_client()
        params: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if tools:
            params["tools"] = tools
        if stop:
            params["stop"] = stop

        stream = await client.chat.completions.create(**params)
        async for chunk in stream:
            ch = chunk.choices[0] if chunk.choices else None
            if ch is None:
                continue
            # Build delta dict, including tool_calls if present
            delta_dict: dict[str, Any] = {}
            if ch.delta:
                if ch.delta.role:
                    delta_dict["role"] = ch.delta.role
                if ch.delta.content is not None:
                    delta_dict["content"] = ch.delta.content
                if ch.delta.tool_calls:
                    delta_dict["tool_calls"] = [
                        {"index": tc.index, "id": tc.id, "type": "function",
                         "function": {"name": tc.function.name if tc.function else None,
                                      "arguments": tc.function.arguments if tc.function else ""}}
                        for tc in ch.delta.tool_calls
                    ]
            yield {
                "id": chunk.id,
                "object": "chat.completion.chunk",
                "created": chunk.created,
                "model": chunk.model,
                "choices": [{
                    "index": 0,
                    "delta": {k: v for k, v in delta_dict.items() if v is not None
                    },
                    "finish_reason": ch.finish_reason,
                }],
            }

    async def embeddings(self, input_texts, model, **kwargs) -> OAIEmbeddingResponse:
        client = self._get_client()
        response = await client.embeddings.create(model=model, input=input_texts)
        data = [
            OAIEmbeddingData(embedding=d.embedding, index=d.index)
            for d in response.data
        ]
        return OAIEmbeddingResponse(
            data=data,
            model=response.model,
            usage=OAIEmbeddingUsage(
                prompt_tokens=response.usage.prompt_tokens,
                total_tokens=response.usage.total_tokens,
            ),
        )

    async def list_models(self) -> list[str]:
        try:
            client = self._get_client()
            resp = await client.models.list()
            return [m.id for m in resp.data]
        except Exception:
            return ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]
