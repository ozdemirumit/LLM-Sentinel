"""AWS Bedrock provider adapter (Claude models via boto3)."""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from logger import get_logger
from models import OAIChatResponse
from providers.base import BaseLLMProvider

log = get_logger(__name__)


class BedrockProvider(BaseLLMProvider):
    provider_type = "bedrock"

    def _get_client(self):
        import boto3
        from config import settings
        region = self.config.get("region", settings.AWS_BEDROCK_REGION)
        return boto3.client("bedrock-runtime", region_name=region)

    def _prepare_body(self, messages, model, temperature, max_tokens) -> dict:
        """Build Bedrock invoke body (Anthropic Messages API format)."""
        system = None
        msgs = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                msgs.append({"role": m.get("role", "user"), "content": m.get("content", "")})

        body: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-12-15",
            "messages": msgs,
            "max_tokens": max_tokens or 4096,
        }
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        return body

    async def chat(self, messages, model, temperature=None, max_tokens=None,
                   tools=None, tool_choice=None, stop=None, **kwargs) -> OAIChatResponse:
        import asyncio
        client = self._get_client()
        body = self._prepare_body(messages, model, temperature, max_tokens)

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.invoke_model(modelId=model, body=json.dumps(body), contentType="application/json"),
        )

        result = json.loads(response["body"].read())
        content = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")

        usage = result.get("usage", {})
        return self._make_oai_response(
            content=content, model=model,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

    async def stream_chat(self, messages, model, temperature=None, max_tokens=None,
                          tools=None, tool_choice=None, stop=None, **kwargs) -> AsyncGenerator[dict, None]:
        import asyncio
        import uuid

        client = self._get_client()
        body = self._prepare_body(messages, model, temperature, max_tokens)

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.invoke_model_with_response_stream(
                modelId=model, body=json.dumps(body), contentType="application/json",
            ),
        )

        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        yield self._make_stream_chunk(role="assistant", model=model, chunk_id=chunk_id)

        stream = response.get("body")
        if stream:
            for event in stream:
                chunk_data = json.loads(event.get("chunk", {}).get("bytes", b"{}"))
                if chunk_data.get("type") == "content_block_delta":
                    delta = chunk_data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield self._make_stream_chunk(
                            content=delta.get("text", ""), model=model, chunk_id=chunk_id
                        )

        yield self._make_stream_chunk(finish_reason="stop", model=model, chunk_id=chunk_id)

    async def list_models(self) -> list[str]:
        return [
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
            "anthropic.claude-3-5-haiku-20241022-v1:0",
            "anthropic.claude-3-opus-20240229-v1:0",
        ]
