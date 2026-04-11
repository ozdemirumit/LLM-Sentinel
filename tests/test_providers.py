"""Tests for provider management."""

import pytest
from httpx import AsyncClient
from providers import get_provider, list_provider_types, BaseLLMProvider


class TestProviderRegistry:
    def test_all_types(self):
        types = list_provider_types()
        assert len(types) == 9
        for t in ["anthropic", "openai", "azure_openai", "gemini", "bedrock",
                   "groq", "mistral", "ollama", "openai_compatible"]:
            assert t in types

    def test_get_provider(self):
        p = get_provider("openai", api_key="test", timeout=5)
        assert isinstance(p, BaseLLMProvider)
        assert p.provider_type == "openai"

    def test_unknown_provider(self):
        with pytest.raises(ValueError, match="Unknown"):
            get_provider("nonexistent")

    def test_openai_compatible(self):
        p = get_provider("openai_compatible", base_url="http://localhost:8000/v1", timeout=5)
        assert p.provider_type == "openai_compatible"


class TestProviderCRUD:
    async def test_create_read_delete(self, client: AsyncClient):
        r = await client.post("/v1/providers", json={
            "name": "test-vllm", "provider_type": "openai_compatible",
            "base_url": "http://gpu:8000/v1", "default_model": "meta/llama3",
        })
        assert r.status_code == 200
        pid = r.json()["id"]

        r = await client.get(f"/v1/providers/{pid}")
        assert r.status_code == 200
        assert r.json()["name"] == "test-vllm"

        r = await client.put(f"/v1/providers/{pid}", json={"default_model": "llama3.2"})
        assert r.status_code == 200

        r = await client.delete(f"/v1/providers/{pid}")
        assert r.status_code == 200

    async def test_list_providers(self, client: AsyncClient):
        r = await client.get("/v1/providers")
        assert r.status_code == 200
