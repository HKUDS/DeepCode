from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from core.application.config_store import ConfigStore
from core.application.llm_configuration_service import LLMConfigurationService
from core.config import ConfigError, load_config
from core.domain.execution_profile import ExecutionSelection
from core.providers.catalog_service import CatalogModel, ModelCatalogService
from core.providers.credentials import CredentialStore
from core.providers.profiles import ConnectionResolver


def _service(
    home: Path,
) -> tuple[LLMConfigurationService, ConfigStore, CredentialStore]:
    config = ConfigStore(home / "deepcode_config.json")
    credentials = CredentialStore(home / "credentials.json")
    service = LLMConfigurationService(
        config_store=config,
        credential_store=credentials,
        catalog=ModelCatalogService(home / "model_catalog_cache.json"),
    )
    return service, config, credentials


def test_connection_write_separates_and_never_projects_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    service, config, credentials = _service(home)
    secret = "unit-test-secret-that-must-not-leak"

    result = service.upsert(
        {
            "id": "router-personal",
            "label": "Router personal",
            "template": "openrouter",
            "apiKey": secret,
            "modelCatalog": "openrouter",
            "manualModels": ["moonshotai/kimi-k2.5"],
        }
    )

    raw_config = config.path.read_text(encoding="utf-8")
    assert secret not in raw_config
    assert (
        json.loads(raw_config)["providers"]["profiles"]["router-personal"]["template"]
        == "openrouter"
    )
    assert credentials.get("router-personal") == secret
    assert stat.S_IMODE(credentials.path.stat().st_mode) == 0o600
    assert secret not in repr(result)
    connection = next(
        item for item in result["connections"] if item["id"] == "router-personal"
    )
    assert connection["configured"] is True
    assert connection["credentialSource"] == "credential_store"
    assert "apiKey" not in connection

    removed = service.remove("router-personal")
    assert removed["removed"] is True
    assert credentials.get("router-personal") is None
    persisted = json.loads(config.path.read_text())
    assert "router-personal" not in persisted.get("providers", {}).get("profiles", {})


def test_environment_precedence_and_named_local_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    service, config, credentials = _service(home)
    service.upsert(
        {
            "id": "router-team",
            "template": "openrouter",
            "apiKeyEnv": "DEEPCODE_TEST_ROUTER_KEY",
            "apiKey": "stored-key",
        }
    )
    service.upsert(
        {
            "id": "local-lab",
            "template": "ollama",
            "apiBase": "http://127.0.0.1:11434/v1",
        }
    )
    monkeypatch.setenv("DEEPCODE_TEST_ROUTER_KEY", "environment-key")

    resolver = ConnectionResolver(
        load_config(config_path=config.path),
        credentials,
    )
    remote = resolver.resolve_connection("router-team")
    local = resolver.resolve_connection("local-lab")

    assert remote.api_key == "environment-key"
    assert remote.credential_source == "environment"
    assert local.is_usable is True
    assert local.credential_source == "not_required"


def test_partial_update_preserves_profile_and_builtin_legacy_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    service, config, credentials = _service(home)
    service.upsert(
        {
            "id": "router-team",
            "template": "openrouter",
            "label": "Team router",
            "apiBase": "https://router.example/v1",
            "manualModels": ["moonshotai/kimi-k2.6"],
            "apiKey": "first-key",
        }
    )

    service.upsert({"id": "router-team", "apiKey": "rotated-key"})

    profile = json.loads(config.path.read_text())["providers"]["profiles"][
        "router-team"
    ]
    assert profile["template"] == "openrouter"
    assert profile["label"] == "Team router"
    assert profile["apiBase"] == "https://router.example/v1"
    assert profile["manualModels"] == ["moonshotai/kimi-k2.6"]
    assert credentials.get("router-team") == "rotated-key"

    config.path.write_text(
        json.dumps(
            {
                "providers": {
                    "openrouter": {
                        "apiKey": "legacy-key",
                        "apiBase": "https://openrouter.ai/api/v1",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    listed = service.upsert({"id": "openrouter", "label": "Primary router"})
    connection = next(
        item for item in listed["connections"] if item["id"] == "openrouter"
    )
    resolved = ConnectionResolver(
        load_config(config_path=config.path),
        credentials,
    ).resolve_connection("openrouter")

    assert connection["configured"] is True
    assert resolved.api_key == "legacy-key"
    assert resolved.api_base == "https://openrouter.ai/api/v1"


def test_execution_profile_freezes_generation_and_connection_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    service, config, credentials = _service(home)
    service.upsert(
        {
            "id": "router-a",
            "template": "openrouter",
            "apiBase": "https://openrouter.ai/api/v1",
            "apiKey": "first-key",
        }
    )
    service.upsert(
        {
            "id": "router-b",
            "template": "openrouter",
            "apiBase": "https://openrouter.ai/api/v1",
            "apiKey": "second-key",
        }
    )

    resolver = ConnectionResolver(load_config(config_path=config.path), credentials)
    profile = resolver.execution_profile(
        ExecutionSelection("router-b", "moonshotai/kimi-k2.6")
    )

    assert profile.connection_id == "router-b"
    assert profile.model_id == "moonshotai/kimi-k2.6"
    assert profile.context_window == 256_000
    assert profile.max_tokens == 8192
    assert profile.temperature == 0.1
    assert "key" not in repr(profile.to_dict()).lower()

    # Credential rotation is deliberately live and does not mutate the
    # persisted, secret-free execution profile.
    credentials.set("router-b", "rotated-key")
    rotated = ConnectionResolver(load_config(config_path=config.path), credentials)
    assert rotated.connection_for_profile(profile).api_key == "rotated-key"

    # Executable connection changes cannot silently redirect an already
    # accepted/queued Turn.
    service.upsert(
        {
            "id": "router-b",
            "template": "openrouter",
            "apiBase": "https://different.example/v1",
        }
    )
    changed = ConnectionResolver(load_config(config_path=config.path), credentials)
    with pytest.raises(ConfigError, match="changed after this Turn was accepted"):
        changed.connection_for_profile(profile)


def test_execution_profile_uses_cached_dynamic_model_limits_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    service, config, credentials = _service(home)
    service.upsert(
        {
            "id": "router-dynamic",
            "template": "openrouter",
            "apiKey": "test-key",
        }
    )
    connection = ConnectionResolver(
        load_config(config_path=config.path),
        credentials,
    ).resolve_connection("router-dynamic")
    discovered = (
        CatalogModel(
            id="moonshotai/kimi-future",
            name="Kimi Future",
            context_window=1_048_576,
            max_output_tokens=128_000,
        ),
    )
    monkeypatch.setattr(service.catalog, "_fetch", lambda _connection: discovered)
    service.catalog.list_models(connection, refresh=True)

    def unexpected_network(_connection):
        raise AssertionError("Turn resolution must use the cache without network I/O")

    monkeypatch.setattr(service.catalog, "_fetch", unexpected_network)
    profile = service.resolve(
        workspace,
        ExecutionSelection("router-dynamic", "moonshotai/kimi-future"),
    )

    assert profile.context_window == 1_048_576
    assert profile.max_output_tokens == 128_000
