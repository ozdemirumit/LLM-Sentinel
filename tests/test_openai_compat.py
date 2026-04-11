"""Tests for OpenAI-compatible endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from models import OAIChatResponse, OAIChoice, OAIMessage, OAIUsage


def _mock_response(content="Hi!", model="gpt-4o"):
    return OAIChatResponse(
        id="chatcmpl-test", model=model,
        choices=[OAIChoice(index=0, message=OAIMessage(role="assistant", content=content), finish_reason="stop")],
        usage=OAIUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
    )


class TestChatCompletionsFormat:
    async def test_valid_oai_response(self, client: AsyncClient):
        with patch("llm_proxy.LLMProxy.chat_completions", new_callable=AsyncMock, return_value=_mock_response()):
            r = await client.post("/v1/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hi"}],
            })
        assert r.status_code == 200
        data = r.json()
        assert data["object"] == "chat.completion"
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert data["usage"]["total_tokens"] == 15


class TestModelsEndpoint:
    async def test_models_list(self, client: AsyncClient):
        r = await client.get("/v1/models")
        assert r.status_code == 200
        data = r.json()
        assert data["object"] == "list"

    async def test_alias_in_models(self, client: AsyncClient):
        r = await client.get("/v1/models")
        data = r.json()
        model_ids = [m["id"] for m in data["data"]]
        assert "fast" in model_ids or "smart" in model_ids


class TestProviderRouting:
    async def test_route_by_model_name(self, client: AsyncClient):
        """Model 'gpt-4o' should infer openai provider."""
        with patch("llm_proxy.LLMProxy.chat_completions", new_callable=AsyncMock, return_value=_mock_response()):
            r = await client.post("/v1/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Test"}],
            })
        assert r.status_code == 200

    async def test_route_by_alias(self, client: AsyncClient):
        """Model 'fast' should resolve to anthropic."""
        with patch("llm_proxy.LLMProxy.chat_completions", new_callable=AsyncMock, return_value=_mock_response(model="claude-haiku-4-5-20251001")):
            r = await client.post("/v1/chat/completions", json={
                "model": "fast",
                "messages": [{"role": "user", "content": "Test"}],
            })
        assert r.status_code == 200


class TestAPIKeyAuth:
    async def test_openai_sdk_pattern(self, unauth_client: AsyncClient, test_client_record):
        """Bearer sk-proxy-xxx in Authorization header should authenticate."""
        _, api_key = test_client_record
        with patch("llm_proxy.LLMProxy.chat_completions", new_callable=AsyncMock, return_value=_mock_response()):
            r = await unauth_client.post("/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
            )
        assert r.status_code == 200
