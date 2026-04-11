"""Groq provider adapter (OpenAI-compatible API)."""

from __future__ import annotations

from providers.openai_provider import OpenAIProvider


class GroqProvider(OpenAIProvider):
    provider_type = "groq"

    def _get_client(self):
        import openai
        return openai.AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url or "https://api.groq.com/openai/v1",
            timeout=self.timeout,
        )

    async def list_models(self) -> list[str]:
        return ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]
