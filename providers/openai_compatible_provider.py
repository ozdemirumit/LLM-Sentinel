"""
Generic OpenAI-compatible provider adapter.

Works with any endpoint that implements the OpenAI API protocol:
vLLM, LiteLLM, LocalAI, text-generation-webui, etc.
Configured via ProviderConfigDB.base_url + config_json.
"""

from __future__ import annotations

from providers.openai_provider import OpenAIProvider


class OpenAICompatibleProvider(OpenAIProvider):
    provider_type = "openai_compatible"

    def _get_client(self):
        import openai
        if not self.base_url:
            raise ValueError("base_url is required for openai_compatible provider")
        return openai.AsyncOpenAI(
            api_key=self.api_key or "not-needed",
            base_url=self.base_url,
            timeout=self.timeout,
        )

    async def list_models(self) -> list[str]:
        try:
            client = self._get_client()
            resp = await client.models.list()
            return [m.id for m in resp.data]
        except Exception:
            return self.config.get("models", [])
