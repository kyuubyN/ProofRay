from __future__ import annotations

from dataclasses import replace
from collections.abc import Iterator
from threading import RLock

from .providers import ChatRequest, ModelDescriptor, ProviderConfig, ProviderEvent, create_provider


class ProviderManager:
    """Process-memory provider registry; secret leases are never serialized."""

    def __init__(self):
        self._configs: dict[str, ProviderConfig] = {}
        self._active: dict[str, set[object]] = {}
        self._lock = RLock()

    def configure(self, config: ProviderConfig) -> None:
        with self._lock:
            self._configs[config.provider_id] = replace(config, secret=None)

    def remove(self, provider_id: str) -> None:
        self.cancel(provider_id)
        with self._lock:
            self._configs.pop(provider_id, None)

    def _provider(self, provider_id: str, secret: str | None):
        with self._lock:
            config = self._configs.get(provider_id)
        if config is None:
            raise ValueError("unknown provider")
        return create_provider(replace(config, secret=secret))

    def list_models(self, provider_id: str, *, secret: str | None = None) \
            -> tuple[ModelDescriptor, ...]:
        provider = self._provider(provider_id, secret)
        try:
            return provider.list_models()
        finally:
            provider.cancel()

    def test_connection(self, provider_id: str, *, secret: str | None = None) -> None:
        provider = self._provider(provider_id, secret)
        try:
            provider.test_connection()
        finally:
            provider.cancel()

    def stream_chat(self, provider_id: str, request: ChatRequest, *,
                    secret: str | None = None) -> Iterator[ProviderEvent]:
        provider = self._provider(provider_id, secret)
        with self._lock:
            self._active.setdefault(provider_id, set()).add(provider)
        try:
            yield from provider.stream_chat(request)
        finally:
            provider.cancel()
            with self._lock:
                active = self._active.get(provider_id)
                if active is not None:
                    active.discard(provider)
                    if not active:
                        self._active.pop(provider_id, None)

    def cancel(self, provider_id: str) -> None:
        with self._lock:
            active = tuple(self._active.get(provider_id, ()))
        for provider in active:
            provider.cancel()


__all__ = ["ProviderManager"]
