import asyncio

import httpx
import pytest
from openai import DefaultAsyncHttpxClient, DefaultHttpxClient, OpenAI

from core import ai_provider


@pytest.fixture(autouse=True)
def provider_environment(monkeypatch):
    for key in (
        "OPENAI_PROXY_URL", "OPENAI_BASE_URL", "OPENAI_TIMEOUT_SECONDS",
        "AI_TIMEOUT_SECONDS", "GAPGPT_API_KEY", "GAPGPT_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    ai_provider.get_ai_provider_config.cache_clear()
    ai_provider._get_proxy_sync_client.cache_clear()
    ai_provider._get_proxy_async_client.cache_clear()
    yield
    ai_provider.get_ai_provider_config.cache_clear()
    ai_provider._get_proxy_sync_client.cache_clear()
    ai_provider._get_proxy_async_client.cache_clear()


@pytest.fixture
def mocked_proxy(monkeypatch):
    calls = []
    transports = []

    def response(request):
        calls.append((request.url.host, request.url.path))
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={
                "data": [{"embedding": [0.1, 0.2], "index": 0, "object": "embedding"}],
                "model": "test-model", "object": "list",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            })
        return httpx.Response(200, json={"data": [{
            "id": "test-model", "object": "model", "created": 0, "owned_by": "test",
        }], "object": "list"})

    def sync_client(**kwargs):
        transports.append(("sync", kwargs))
        return DefaultHttpxClient(transport=httpx.MockTransport(response))

    def async_client(**kwargs):
        transports.append(("async", kwargs))
        return DefaultAsyncHttpxClient(transport=httpx.MockTransport(response))

    monkeypatch.setenv("OPENAI_PROXY_URL", "http://private-proxy:13129")
    monkeypatch.setattr(ai_provider, "DefaultHttpxClient", sync_client)
    monkeypatch.setattr(ai_provider, "DefaultAsyncHttpxClient", async_client)
    return calls, transports


def test_without_proxy_preserves_default_client_configuration():
    assert ai_provider.get_openai_client_kwargs() == {
        "api_key": "test-key", "base_url": "https://api.openai.com/v1",
    }
    assert ai_provider.get_agno_openai_kwargs() == ai_provider.get_openai_client_kwargs()


def test_proxy_is_scoped_to_official_openai_endpoint(monkeypatch):
    monkeypatch.setenv("OPENAI_PROXY_URL", "http://private-proxy:13129")
    assert ai_provider.get_openai_transport_kwargs("https://other-api.example/v1") == {}
    monkeypatch.setenv("AI_PROVIDER", "gapgpt")
    monkeypatch.setenv("GAPGPT_API_KEY", "other-key")
    assert "http_client" not in ai_provider.get_openai_client_kwargs()
    assert "client" not in ai_provider.get_agno_openai_kwargs()


@pytest.mark.parametrize("proxy", ["socks5://proxy:1080", "http://", "not-a-url"])
def test_invalid_proxy_configuration_fails_without_direct_fallback(monkeypatch, proxy):
    monkeypatch.setenv("OPENAI_PROXY_URL", proxy)
    with pytest.raises(ValueError, match="OPENAI_PROXY_URL"):
        ai_provider.get_openai_client_kwargs()


def test_native_client_uses_scoped_transport(mocked_proxy):
    client = OpenAI(**ai_provider.get_openai_client_kwargs())
    assert client.models.list().data[0].id == "test-model"
    calls, transports = mocked_proxy
    assert calls == [("api.openai.com", "/v1/models")]
    assert transports == [("sync", {
        "proxy": "http://private-proxy:13129", "trust_env": False, "follow_redirects": False,
    })]


def test_agno_models_use_proxy_for_sync_and_async_clients(mocked_proxy):
    from agno.models.openai import OpenAIChat

    model = OpenAIChat(id="test-model", **ai_provider.get_agno_openai_kwargs())
    assert model.get_client().models.list().data[0].id == "test-model"

    async def check():
        assert (await model.get_async_client().models.list()).data[0].id == "test-model"

    asyncio.run(check())
    calls, transports = mocked_proxy
    assert len(calls) == 2
    assert {kind for kind, _ in transports} == {"sync", "async"}


def test_agno_embeddings_use_proxy_and_preserve_timeout(monkeypatch, mocked_proxy):
    from agno.knowledge.embedder.openai import OpenAIEmbedder

    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "45")
    kwargs = ai_provider.get_agno_openai_kwargs(for_embedder=True)
    assert "timeout" not in kwargs
    assert kwargs["openai_client"].timeout == 45
    assert kwargs["async_client"].timeout == 45
    embedder = OpenAIEmbedder(id="test-model", dimensions=2, **kwargs)
    assert embedder.get_embedding("non-sensitive test") == [0.1, 0.2]

    async def check():
        assert await embedder.async_get_embedding("non-sensitive test") == [0.1, 0.2]

    asyncio.run(check())
    assert mocked_proxy[0] == [("api.openai.com", "/v1/embeddings")] * 2


def test_closed_transport_is_replaced(mocked_proxy):
    first = ai_provider.get_openai_transport_kwargs()["http_client"]
    first.close()
    replacement = ai_provider.get_openai_transport_kwargs()["http_client"]
    assert replacement is not first
    assert not replacement.is_closed
