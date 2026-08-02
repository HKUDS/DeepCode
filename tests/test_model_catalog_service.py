from __future__ import annotations

import stat
from dataclasses import replace
from pathlib import Path

import pytest

from core.config import DeepCodeConfig
from core.providers.catalog_service import CatalogModel, ModelCatalogService
from core.providers.credentials import CredentialStore
from core.providers.profiles import ConnectionResolver


def _connection(
    tmp_path: Path,
    *,
    connection_id: str = "router-test",
    catalog: str = "openrouter",
    manual_models: list[str] | None = None,
):
    credentials = CredentialStore(tmp_path / "credentials.json")
    credentials.set(connection_id, "catalog-test-secret")
    config = DeepCodeConfig.model_validate(
        {
            "providers": {
                "profiles": {
                    connection_id: {
                        "template": "openrouter",
                        "modelCatalog": catalog,
                        "manualModels": manual_models or [],
                    }
                }
            }
        }
    )
    return ConnectionResolver(config, credentials).resolve_connection(connection_id)


def test_remote_catalog_is_cached_and_refresh_falls_back_to_last_known_good(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "model_catalog_cache.json"
    connection = _connection(tmp_path)
    service = ModelCatalogService(cache_path, ttl_seconds=3600)
    discovered = (
        CatalogModel(
            id="moonshotai/kimi-k2.6",
            name="Kimi K2.6",
            context_window=262_144,
            max_output_tokens=65_536,
            supported_parameters=("tools",),
        ),
    )
    monkeypatch.setattr(service, "_fetch", lambda _connection: discovered)

    remote = service.list_models(connection)

    assert remote.models == discovered
    assert service.cached_model(connection, "moonshotai/kimi-k2.6") == discovered[0]
    assert remote.source == "remote"
    assert remote.stale is False
    assert stat.S_IMODE(cache_path.stat().st_mode) == 0o600
    assert "catalog-test-secret" not in cache_path.read_text(encoding="utf-8")

    cached_service = ModelCatalogService(cache_path, ttl_seconds=3600)

    def offline(_connection):
        raise OSError("network unavailable")

    monkeypatch.setattr(cached_service, "_fetch", offline)
    cached = cached_service.list_models(connection)
    stale = cached_service.list_models(connection, refresh=True)

    assert cached.models == discovered
    assert cached.stale is False
    assert stale.models == discovered
    assert stale.source == "remote"
    assert stale.stale is True
    assert stale.error == "OSError: network unavailable"


def test_manual_catalog_is_offline_and_preserves_declared_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection(
        tmp_path,
        connection_id="manual-router",
        catalog="manual",
        manual_models=[
            "moonshotai/kimi-k2.6",
            "openai/gpt-5-mini",
            "moonshotai/kimi-k2.6",
        ],
    )
    service = ModelCatalogService(tmp_path / "cache.json")

    def unexpected_network(_connection):
        raise AssertionError("manual catalogs must not use the network")

    monkeypatch.setattr(service, "_fetch", unexpected_network)
    result = service.list_models(connection, refresh=True)

    assert [model.id for model in result.models] == [
        "moonshotai/kimi-k2.6",
        "openai/gpt-5-mini",
    ]
    assert result.source == "manual"
    assert result.stale is False
    assert result.error is None


def test_cache_is_invalidated_when_connection_routing_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection(tmp_path)
    service = ModelCatalogService(tmp_path / "cache.json", ttl_seconds=3600)
    calls: list[str | None] = []

    def fetch(candidate):
        calls.append(candidate.api_base)
        return (
            CatalogModel(
                id=f"model-{len(calls)}",
                name="Model",
                context_window=8192,
                max_output_tokens=1024,
            ),
        )

    monkeypatch.setattr(service, "_fetch", fetch)
    first = service.list_models(connection)
    changed = replace(connection, api_base="https://different.example/v1")
    second = service.list_models(changed)

    assert [model.id for model in first.models] == ["model-1"]
    assert [model.id for model in second.models] == ["model-2"]
    assert calls == [
        connection.api_base,
        "https://different.example/v1",
    ]
