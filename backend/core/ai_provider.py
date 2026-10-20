import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from openai import AsyncOpenAI, DefaultAsyncHttpxClient, DefaultHttpxClient, OpenAI


logger = logging.getLogger(__name__)
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


@dataclass(frozen=True)
class AIProviderConfig:
    provider: str
    api_key: str
    base_url: Optional[str]
    timeout: Optional[float]


def _clean_base_url(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip()
    return cleaned.rstrip("/") if cleaned else None


def _read_timeout_seconds(provider: str) -> Optional[float]:
    raw_value = (
        os.getenv(f"{provider.upper()}_TIMEOUT_SECONDS")
        or os.getenv("AI_TIMEOUT_SECONDS")
        or ("300" if provider == "gapgpt" else None)
    )
    if raw_value in (None, ""):
        return None

    try:
        timeout = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid timeout value '{raw_value}' for provider '{provider}'. "
            "Use a positive number of seconds."
        ) from exc

    if timeout <= 0:
        raise ValueError(
            f"Invalid timeout value '{raw_value}' for provider '{provider}'. "
            "Use a positive number of seconds."
        )
    return timeout


@lru_cache(maxsize=1)
def get_ai_provider_config() -> AIProviderConfig:
    provider = (os.getenv("AI_PROVIDER", "openai") or "openai").strip().lower()
    if provider not in {"openai", "gapgpt"}:
        raise ValueError(
            f"Invalid AI_PROVIDER='{provider}'. Supported values: 'openai', 'gapgpt'."
        )

    if provider == "gapgpt":
        api_key = (os.getenv("GAPGPT_API_KEY") or "").strip()
        if not api_key:
            raise ValueError("Missing GAPGPT_API_KEY while AI_PROVIDER='gapgpt'.")
        base_url = _clean_base_url(os.getenv("GAPGPT_BASE_URL", "https://api.gapgpt.app/v1"))
    else:
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise ValueError("Missing OPENAI_API_KEY while AI_PROVIDER='openai'.")
        base_url = _clean_base_url(os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL))

    logger.info(
        "AI provider selected: provider=%s, base_url=%s, timeout=%s",
        provider,
        base_url or "default",
        _read_timeout_seconds(provider) or "default",
    )
    return AIProviderConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        timeout=_read_timeout_seconds(provider),
    )


@lru_cache(maxsize=4)
def _get_proxy_sync_client(proxy_url: str):
    return DefaultHttpxClient(proxy=proxy_url, trust_env=False, follow_redirects=False)


@lru_cache(maxsize=4)
def _get_proxy_async_client(proxy_url: str):
    return DefaultAsyncHttpxClient(proxy=proxy_url, trust_env=False, follow_redirects=False)


def get_openai_transport_kwargs(base_url: Optional[str] = None, *, asynchronous: bool = False) -> Dict[str, Any]:
    """Apply the opt-in proxy only to the official OpenAI endpoint."""
    proxy_url = (os.getenv("OPENAI_PROXY_URL") or "").strip()
    target = urlsplit(base_url or DEFAULT_OPENAI_BASE_URL)
    if not proxy_url or target.hostname != "api.openai.com":
        return {}
    proxy = urlsplit(proxy_url)
    if proxy.scheme not in {"http", "https"} or not proxy.hostname:
        raise ValueError("OPENAI_PROXY_URL must be an HTTP(S) proxy URL.")
    factory = _get_proxy_async_client if asynchronous else _get_proxy_sync_client
    client = factory(proxy_url)
    if client.is_closed:
        factory.cache_clear()
        client = factory(proxy_url)
    return {"http_client": client}


def get_openai_client_kwargs() -> Dict[str, Any]:
    cfg = get_ai_provider_config()
    kwargs: Dict[str, Any] = {"api_key": cfg.api_key}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    if cfg.timeout is not None:
        kwargs["timeout"] = cfg.timeout
    if cfg.provider == "openai":
        kwargs.update(get_openai_transport_kwargs(cfg.base_url))
    return kwargs


def get_agno_openai_kwargs(*, for_embedder: bool = False) -> Dict[str, Any]:
    kwargs = get_openai_client_kwargs()
    # Agno normally supplies its own global transports. Inject both SDK clients
    # when proxying so synchronous tools and asynchronous streams use one route.
    transport = kwargs.pop("http_client", None)
    if transport is not None:
        cfg = get_ai_provider_config()
        sdk_kwargs = kwargs.copy()
        kwargs["openai_client" if for_embedder else "client"] = OpenAI(
            **sdk_kwargs, http_client=transport
        )
        kwargs["async_client"] = AsyncOpenAI(
            **sdk_kwargs, **get_openai_transport_kwargs(cfg.base_url, asynchronous=True)
        )
    if for_embedder and "timeout" in kwargs:
        timeout = kwargs.pop("timeout")
        if transport is None:
            kwargs["client_params"] = {"timeout": timeout}
    return kwargs


def get_transcription_model_id() -> str:
    return (os.getenv("AI_TRANSCRIBE_MODEL") or "whisper-1").strip() or "whisper-1"
