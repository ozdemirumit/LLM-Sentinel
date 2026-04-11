"""Google Gemini provider adapter."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from logger import get_logger
from models import OAIChatResponse, OAIEmbeddingResponse, OAIEmbeddingData, OAIEmbeddingUsage
from providers.base import BaseLLMProvider

log = get_logger(__name__)


class GeminiProvider(BaseLLMProvider):
    provider_type = "gemini"

    def _get_client(self):
        from google import genai
        return genai.Client(api_key=self.api_key)

    def _convert_messages(self, messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Convert OpenAI messages to Gemini format."""
        system = None
        contents = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system = content
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})
            else:
                contents.append({"role": "user", "parts": [{"text": content}]})
        return system, contents

    async def chat(self, messages, model, temperature=None, max_tokens=None,
                   tools=None, tool_choice=None, stop=None, **kwargs) -> OAIChatResponse:
        client = self._get_client()
        system, contents = self._convert_messages(messages)

        config: dict[str, Any] = {}
        if temperature is not None:
            config["temperature"] = temperature
        if max_tokens is not None:
            config["max_output_tokens"] = max_tokens
        if system:
            config["system_instruction"] = system

        response = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config if config else None,
        )

        text = response.text or ""
        usage = response.usage_metadata
        return self._make_oai_response(
            content=text, model=model,
            input_tokens=getattr(usage, "prompt_token_count", 0) if usage else 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) if usage else 0,
        )

    async def stream_chat(self, messages, model, temperature=None, max_tokens=None,
                          tools=None, tool_choice=None, stop=None, **kwargs) -> AsyncGenerator[dict, None]:
        client = self._get_client()
        system, contents = self._convert_messages(messages)

        config: dict[str, Any] = {}
        if temperature is not None:
            config["temperature"] = temperature
        if max_tokens is not None:
            config["max_output_tokens"] = max_tokens
        if system:
            config["system_instruction"] = system

        import uuid
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        yield self._make_stream_chunk(role="assistant", model=model, chunk_id=chunk_id)

        async for chunk in await client.aio.models.generate_content_stream(
            model=model, contents=contents, config=config if config else None,
        ):
            if chunk.text:
                yield self._make_stream_chunk(content=chunk.text, model=model, chunk_id=chunk_id)

        yield self._make_stream_chunk(finish_reason="stop", model=model, chunk_id=chunk_id)

    async def embeddings(self, input_texts, model, **kwargs) -> OAIEmbeddingResponse:
        client = self._get_client()
        data = []
        total_tokens = 0
        for i, text in enumerate(input_texts):
            result = await client.aio.models.embed_content(model=model, contents=text)
            data.append(OAIEmbeddingData(embedding=result.embeddings[0].values, index=i))
        return OAIEmbeddingResponse(data=data, model=model,
                                    usage=OAIEmbeddingUsage(prompt_tokens=total_tokens, total_tokens=total_tokens))

    async def list_models(self) -> list[str]:
        return ["gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash"]
