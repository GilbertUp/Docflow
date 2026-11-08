"""The AI provider adapters, tested against stand-in servers. No real service is contacted."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from docuflow.modules.assistant.providers import (
    ANTHROPIC_VERSION,
    Anthropic,
    OpenAiCompatible,
    ProviderError,
    build_provider,
)


def client(handler: httpx.MockTransport | None = None, *, status: int = 200, body: object = None):
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=handler or httpx.MockTransport(respond)), seen


def test_an_openai_compatible_server_gets_the_prompt_and_its_text_comes_back() -> None:
    http, seen = client(body={"choices": [{"message": {"content": "The total is 5 [1]."}}]})
    provider = OpenAiCompatible("http://localhost:11434/v1/", "llama", "secret", client=http)
    assert provider.generate("be careful", "the passages") == "The total is 5 [1]."
    request = seen[0]
    assert str(request.url) == "http://localhost:11434/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer secret"
    sent = json.loads(request.content)
    assert sent["model"] == "llama"
    assert sent["temperature"] == 0
    assert sent["messages"] == [
        {"role": "system", "content": "be careful"},
        {"role": "user", "content": "the passages"},
    ]
    assert "tools" not in sent  # the model is given no way to act
    assert "functions" not in sent


def test_a_local_server_needs_no_key() -> None:
    http, seen = client(body={"choices": [{"message": {"content": "ok"}}]})
    OpenAiCompatible("http://localhost:11434/v1", "llama", client=http).generate("s", "u")
    assert "authorization" not in seen[0].headers


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (500, {"error": "down"}),
        (200, {"unexpected": True}),
        (200, {"choices": []}),
        (200, {"choices": [{"message": {"content": 42}}]}),
    ],
)
def test_a_bad_reply_from_an_openai_compatible_server_is_a_provider_error(
    status: int, body: object
) -> None:
    http, _ = client(status=status, body=body)
    with pytest.raises(ProviderError):
        OpenAiCompatible("http://x/v1", "m", client=http).generate("s", "u")


def test_a_server_that_cannot_be_reached_is_a_provider_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    http = httpx.Client(transport=httpx.MockTransport(refuse))
    with pytest.raises(ProviderError):
        OpenAiCompatible("http://x/v1", "m", client=http).generate("s", "u")


def test_anthropic_gets_its_headers_and_the_text_blocks_are_joined() -> None:
    http, seen = client(
        body={
            "content": [
                {"type": "text", "text": "The total is "},
                {"type": "tool_use", "id": "x"},
                {"type": "text", "text": "5 [1]."},
            ]
        }
    )
    provider = Anthropic("key-123", "claude-sonnet-5-5", client=http, url="http://test/v1/messages")
    assert provider.generate("system words", "passages") == "The total is 5 [1]."
    request = seen[0]
    assert request.headers["x-api-key"] == "key-123"
    assert request.headers["anthropic-version"] == ANTHROPIC_VERSION
    sent = json.loads(request.content)
    assert sent["model"] == "claude-sonnet-5-5"
    assert sent["system"] == "system words"
    assert sent["messages"] == [{"role": "user", "content": "passages"}]
    assert "tools" not in sent


@pytest.mark.parametrize("body", [{"content": []}, {"content": [{"type": "tool_use"}]}, {"x": 1}])
def test_an_anthropic_reply_with_no_text_is_a_provider_error(body: object) -> None:
    http, _ = client(body=body)
    with pytest.raises(ProviderError):
        Anthropic("k", client=http, url="http://test").generate("s", "u")


def settings(**values: object) -> SimpleNamespace:
    return SimpleNamespace(**values)


def test_the_server_settings_decide_which_provider_is_built() -> None:
    assert build_provider(settings()) is None
    assert build_provider(settings(ai_provider="none")) is None

    local = build_provider(
        settings(ai_provider="openai_compatible", ai_base_url="http://x/v1", ai_model="llama")
    )
    assert isinstance(local, OpenAiCompatible)
    assert local.model == "llama"
    assert build_provider(settings(ai_provider="openai_compatible", ai_model="llama")) is None
    assert build_provider(settings(ai_provider="openai_compatible", ai_base_url="http://x")) is None

    cloud = build_provider(settings(ai_provider="anthropic", ai_api_key=SecretStr("k")))
    assert isinstance(cloud, Anthropic)
    assert cloud.model == "claude-sonnet-5-5"  # a default when none is set
    assert build_provider(settings(ai_provider="anthropic")) is None  # a key is required
