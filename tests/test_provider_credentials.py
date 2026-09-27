"""The credential store's optional OS-keychain read fallback.

``keyring`` is deliberately not a declared dependency, so the keychain backend is
opt-in (``DEEPCODE_KEYRING``), consulted only when the credential file holds no
value for a connection, and never written to — the file stays the source of truth
and the only write target, which keeps ``revision``'s mtime/size fingerprint
meaningful. These tests inject a fake ``keyring`` module, which is also how a
default install behaves: the package is simply absent.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from core.application.config_store import ConfigStore
from core.application.llm_configuration_service import LLMConfigurationService
from core.config import load_config
from core.providers.catalog_service import ModelCatalogService
from core.providers.credentials import (
    KEYRING_ENV_VAR,
    KEYRING_SERVICE,
    CredentialStore,
)
from core.providers.profiles import ConnectionResolver


class _FakeKeyring(ModuleType):
    """Minimal stand-in for the optional ``keyring`` package."""

    def __init__(
        self,
        secrets: dict[str, str] | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__("keyring")
        self.secrets = dict(secrets or {})
        self.error = error
        self.reads: list[tuple[str, str]] = []
        self.writes: list[tuple[str, str, str]] = []

    def get_password(self, service: str, username: str) -> str | None:
        self.reads.append((service, username))
        if self.error is not None:
            raise self.error
        return self.secrets.get(f"{service}/{username}")

    def set_password(self, service: str, username: str, password: str) -> None:
        self.writes.append((service, username, password))
        self.secrets[f"{service}/{username}"] = password


def _install(monkeypatch: pytest.MonkeyPatch, module: ModuleType | None) -> None:
    monkeypatch.setitem(sys.modules, "keyring", module)


def _store(tmp_path: Path) -> CredentialStore:
    return CredentialStore(tmp_path / "credentials.json")


def _keyed(secret: str) -> _FakeKeyring:
    return _FakeKeyring({f"{KEYRING_SERVICE}/router": secret})


def test_keychain_is_not_consulted_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _keyed("keychain-secret")
    _install(monkeypatch, backend)
    monkeypatch.delenv(KEYRING_ENV_VAR, raising=False)

    assert _store(tmp_path).get("router") is None
    assert backend.reads == []


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "  "])
def test_keychain_ignores_falsey_knob_spellings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    backend = _keyed("keychain-secret")
    _install(monkeypatch, backend)
    monkeypatch.setenv(KEYRING_ENV_VAR, value)

    assert _store(tmp_path).get("router") is None
    assert backend.reads == []


def test_keychain_supplies_an_absent_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _keyed("keychain-secret")
    _install(monkeypatch, backend)
    monkeypatch.setenv(KEYRING_ENV_VAR, "on")

    assert _store(tmp_path).get("router") == "keychain-secret"
    assert backend.reads == [(KEYRING_SERVICE, "router")]


def test_credential_file_still_wins_over_the_keychain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _keyed("keychain-secret")
    _install(monkeypatch, backend)
    monkeypatch.setenv(KEYRING_ENV_VAR, "1")
    store = _store(tmp_path)
    store.set("router", "file-secret")

    assert store.get("router") == "file-secret"
    assert backend.reads == []


def test_missing_keyring_package_degrades_to_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A default install has no ``keyring``; the import must not raise."""
    _install(monkeypatch, None)
    monkeypatch.setenv(KEYRING_ENV_VAR, "1")

    assert _store(tmp_path).get("router") is None


def test_backend_failure_never_escapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _FakeKeyring(error=RuntimeError("keychain locked")))
    monkeypatch.setenv(KEYRING_ENV_VAR, "1")

    assert _store(tmp_path).get("router") is None


@pytest.mark.parametrize("blank", ["", None, 123])
def test_a_non_string_file_value_falls_through_to_the_keychain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    blank: object,
) -> None:
    _install(monkeypatch, _keyed("keychain-secret"))
    monkeypatch.setenv(KEYRING_ENV_VAR, "1")
    store = _store(tmp_path)
    monkeypatch.setattr(
        store,
        "_read",
        lambda: {"version": 1, "connections": {"router": blank}},
    )

    assert store.get("router") == "keychain-secret"


def test_the_store_never_writes_to_the_keychain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _keyed("keychain-secret")
    _install(monkeypatch, backend)
    monkeypatch.setenv(KEYRING_ENV_VAR, "1")
    store = _store(tmp_path)

    store.set("router", "file-secret")
    store.clear("router")

    assert backend.writes == []


def test_a_keychain_only_credential_leaves_no_revision_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The keychain is a backend, not a second store: nothing is persisted."""
    _install(monkeypatch, _keyed("keychain-secret"))
    monkeypatch.setenv(KEYRING_ENV_VAR, "1")
    store = _store(tmp_path)

    assert store.revision() == "missing"
    assert store.get("router") == "keychain-secret"
    assert store.revision() == "missing"
    assert not store.path.exists()


def test_resolution_order_gains_keychain_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env → credential store (keychain-backed) → legacy config, end to end."""
    home = tmp_path / "home"
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    config = ConfigStore(home / "deepcode_config.json")
    credentials = CredentialStore(home / "credentials.json")
    service = LLMConfigurationService(
        config_store=config,
        credential_store=credentials,
        catalog=ModelCatalogService(home / "model_catalog_cache.json"),
    )
    service.upsert({"id": "router", "template": "openrouter", "modelCatalog": "auto"})
    _install(monkeypatch, _keyed("keychain-secret"))
    resolver = ConnectionResolver(load_config(config_path=config.path), credentials)

    monkeypatch.setenv(KEYRING_ENV_VAR, "1")
    resolved = resolver.resolve_connection("router")
    assert resolved.api_key == "keychain-secret"
    # The keychain is the credential store's backend, not a fifth tier.
    assert resolved.credential_source == "credential_store"

    monkeypatch.setenv(KEYRING_ENV_VAR, "0")
    without = resolver.resolve_connection("router")
    assert without.api_key is None
    assert without.credential_source == "missing"
