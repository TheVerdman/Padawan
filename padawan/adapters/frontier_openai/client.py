from __future__ import annotations

import httpx

from padawan.adapters.openai_compatible.client import OpenAICompatibleClient


class OpenAIResponsesClient(OpenAICompatibleClient):
    """Official OpenAI adapter fixed to the Responses API, with no legacy fallback."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com",
        timeout_seconds: float = 120.0,
        retry_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is required")
        super().__init__(
            base_url=base_url,
            model=model,
            api_key=api_key,
            provider="openai",
            protocol="responses",
            allow_legacy_fallback=False,
            timeout_seconds=timeout_seconds,
            retry_attempts=retry_attempts,
            client=client,
        )
