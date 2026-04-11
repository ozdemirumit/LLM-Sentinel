"""Anthropic Claude provider adapter."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from logger import get_logger
from models import OAIChatResponse, OAIEmbeddingResponse
from providers.base import BaseLLMProvider

log = get_logger(__name__)


class AnthropicProvider(BaseLLMProvider):
    provider_type = "anthropic"

    def _get_client(self):
        import anthropic
        return anthropic.AsyncAnthropic(
            api_key=self.api_key,
            timeout=self.timeout,
        )

    def _prepare_messages(self, messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Extract system prompt and convert messages to Anthropic format."""
        system = None
        converted = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                converted.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        return system, converted

    def _convert_tools(self, tools: list[dict] | None) -> list[dict] | None:
        """Convert OpenAI tool format to Anthropic format."""
        if not tools:
            return None
        result = []
        for t in tools:
            if t.get("type") == "function":
                func = t.get("function", {})
                result.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {}),
                })
        return result or None

    async def chat(self, messages, model, temperature=None, max_tokens=None,
                   tools=None, tool_choice=None, stop=None, **kwargs) -> OAIChatResponse:
        client = self._get_client()
        system, msgs = self._prepare_messages(messages)

        params: dict[str, Any] = {
            "model": model,
            "messages": msgs,
            "max_tokens": max_tokens or 4096,
        }
        if system:
            params["system"] = system
        if temperature is not None:
            params["temperature"] = temperature
        if stop:
            params["stop_sequences"] = stop if isinstance(stop, list) else [stop]

        anthropic_tools = self._convert_tools(tools)
        if anthropic_tools:
            params["tools"] = anthropic_tools

        response = await client.messages.create(**params)

        # Extract content
        content_text = ""
        tool_calls_list = None
        finish_reason = "stop"

        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                if tool_calls_list is None:
                    tool_calls_list = []
                tool_calls_list.append({
                    "id": block.id,
                    "type": "function",
                    "function": {"name": block.name, "arguments": __import__("json").dumps(block.input)},
                })

        if response.stop_reason == "tool_use":
            finish_reason = "tool_calls"
        elif response.stop_reason == "end_turn":
            finish_reason = "stop"
        elif response.stop_reason == "max_tokens":
            finish_reason = "length"

        return self._make_oai_response(
            content=content_text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            finish_reason=finish_reason,
            tool_calls=tool_calls_list,
        )

    async def stream_chat(self, messages, model, temperature=None, max_tokens=None,
                          tools=None, tool_choice=None, stop=None, **kwargs) -> AsyncGenerator[dict, None]:
        client = self._get_client()
        system, msgs = self._prepare_messages(messages)

        params: dict[str, Any] = {
            "model": model,
            "messages": msgs,
            "max_tokens": max_tokens or 4096,
            "stream": True,
        }
        if system:
            params["system"] = system
        if temperature is not None:
            params["temperature"] = temperature

        import uuid
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        # Send role first
        yield self._make_stream_chunk(role="assistant", model=model, chunk_id=chunk_id)

        async with client.messages.stream(**{k: v for k, v in params.items() if k != "stream"}) as stream:
            async for text in stream.text_stream:
                yield self._make_stream_chunk(content=text, model=model, chunk_id=chunk_id)

        yield self._make_stream_chunk(finish_reason="stop", model=model, chunk_id=chunk_id)

    async def list_models(self) -> list[str]:
        return [
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        ]
