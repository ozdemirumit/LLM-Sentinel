"""Tests for chat endpoints (mocked provider)."""

import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from httpx import AsyncClient

from models import OAIChatResponse, OAIChoice, OAIMessage, OAIUsage


def _mock_oai_response(content="Hello!", model="claude-sonnet-4-6", input_tok=10, output_tok=20):
    return OAIChatResponse(
        id="chatcmpl-test",
        model=model,
        choices=[OAIChoice(index=0, message=OAIMessage(role="assistant", content=content), finish_reason="stop")],
        usage=OAIUsage(prompt_tokens=input_tok, completion_tokens=output_tok, total_tokens=input_tok + output_tok),
    )


class TestChatCompletions:
    async def test_valid_request(self, client: AsyncClient):
        mock_resp = _mock_oai_response()
        with patch("llm_proxy.LLMProxy.chat_completions", new_callable=AsyncMock, return_value=mock_resp):
            r = await client.post("/v1/chat/completions", json={
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "Hi"}],
            })
        assert r.status_code == 200
        data = r.json()
        assert data["choices"][0]["message"]["content"] == "Hello!"
        assert "X-Request-ID" in r.headers

    async def test_alias_resolved(self, client: AsyncClient):
        mock_resp = _mock_oai_response(model="claude-haiku-4-5-20251001")
        with patch("llm_proxy.LLMProxy.chat_completions", new_callable=AsyncMock, return_value=mock_resp):
            r = await client.post("/v1/chat/completions", json={
                "model": "fast",
                "messages": [{"role": "user", "content": "Hi"}],
            })
        assert r.status_code == 200

    async def test_cache_hit_header(self, client: AsyncClient):
        mock_resp = _mock_oai_response()
        with patch("llm_proxy.LLMProxy.chat_completions", new_callable=AsyncMock, return_value=mock_resp):
            r = await client.post("/v1/chat/completions", json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
            })
        assert r.headers.get("X-Cache-Hit") == "false"


class TestNativeChat:
    async def test_native_format(self, client: AsyncClient):
        from models import ChatResponse, ContentBlock, UsageInfo

        mock_resp = ChatResponse(
            stop_reason="stop",
            content=[ContentBlock(type="text", text="Hello!")],
            usage=UsageInfo(input_tokens=10, output_tokens=20),
            model="claude-sonnet-4-6",
            provider="anthropic",
        )
        with patch("llm_proxy.LLMProxy.chat", new_callable=AsyncMock, return_value=mock_resp):
            r = await client.post("/v1/chat", json={
                "messages": [{"role": "user", "content": "Hi"}],
            })
        assert r.status_code == 200


class TestChatErrors:
    async def test_auth_required(self, unauth_client: AsyncClient):
        r = await unauth_client.post("/v1/chat/completions", json={
            "model": "test", "messages": [{"role": "user", "content": "Hi"}],
        })
        assert r.status_code == 401
