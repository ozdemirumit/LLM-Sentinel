"""Mistral provider adapter (OpenAI-compatible API)."""

from __future__ import annotations

from providers.openai_provider import OpenAIProvider


class MistralProvider(OpenAIProvider):
    provider_type = "mistral"

    def _get_client(self):
        import openai
        return openai.AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url or "https://api.mistral.ai/v1",
            timeout=self.timeout,
        )

    async def list_models(self) -> list[str]:
        return ["mistral-large-latest", "mistral-small-latest", "codestral-latest"]
