"""Language-model providers behind one small interface (F18, ADR 0013).

A provider receives text and returns text. It is given no tools and no way to act, so the worst a
manipulated answer can do is be wrong, and every answer is checked against its sources afterwards.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 700


class ProviderError(Exception):
    """The provider could not answer. The detail is for the log, never for the person asking."""


class LlmProvider(Protocol):
    name: str
    model: str

    def generate(self, system: str, user: str) -> str: ...


class OpenAiCompatible:
    """Any server that speaks the OpenAI chat completions protocol, including local ones."""

    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self._key = api_key
        self._client = client or httpx.Client(timeout=timeout)

    def generate(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            response = self._client.post(self._url, json=body, headers=headers)
            response.raise_for_status()
            data: Any = response.json()
            content = data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(f"{type(exc).__name__}") from exc
        if not isinstance(content, str):
            raise ProviderError("The reply was not text.")
        return content


class Anthropic:
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
        url: str = ANTHROPIC_URL,
    ) -> None:
        self._key = api_key
        self.model = model
        self._url = url
        self._client = client or httpx.Client(timeout=timeout)

    def generate(self, system: str, user: str) -> str:
        headers = {
            "x-api-key": self._key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        try:
            response = self._client.post(self._url, json=body, headers=headers)
            response.raise_for_status()
            data: Any = response.json()
            parts = [
                block["text"]
                for block in data["content"]
                if isinstance(block, dict) and block.get("type") == "text"
            ]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"{type(exc).__name__}") from exc
        if not parts:
            raise ProviderError("The reply had no text.")
        return "".join(parts)


def build_provider(settings: object) -> LlmProvider | None:
    """The provider the server is set up for, or None when there is none."""
    kind = getattr(settings, "ai_provider", "none")
    model = getattr(settings, "ai_model", None)
    key = getattr(settings, "ai_api_key", None)
    secret = key.get_secret_value() if key is not None else None
    timeout = float(getattr(settings, "ai_timeout_seconds", 60.0))
    if kind == "openai_compatible":
        base = getattr(settings, "ai_base_url", None)
        if not base or not model:
            return None
        return OpenAiCompatible(base, model, secret, timeout)
    if kind == "anthropic":
        if not secret:
            return None
        return Anthropic(secret, model or DEFAULT_ANTHROPIC_MODEL, timeout)
    return None
