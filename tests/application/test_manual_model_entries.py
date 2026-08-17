"""Manual model declarations flow through the existing catalog chain.

`manualModels` entries may be plain ids (unchanged behavior) or objects
declaring label / capacities / published effort levels. Declarations are
authoritative offline: the catalog listing, `cached_model` (what execution
profiles read), and `model_reasoning` all pick them up with zero new read
paths.
"""

import json
from pathlib import Path

from core.application.llm_configuration_service import LLMConfigurationService
from core.providers.catalog_service import ModelCatalogService
from core.providers.profiles import ConnectionResolver
from core.config import load_config
from core.providers.credentials import CredentialStore


def _service(
    tmp_path: Path,
    manual_models: list,
    *,
    model_catalog: str = "manual",
) -> LLMConfigurationService:
    config_path = tmp_path / "deepcode_config.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": {
                    "profiles": {
                        "acme": {
                            "template": "custom",
                            "adapter": "openai_compat",
                            "apiBase": "https://acme.example/v1",
                            "modelCatalog": model_catalog,
                            "manualModels": manual_models,
                        }
                    },
                    "custom": {"apiKey": "test-key"},
                }
            }
        ),
        encoding="utf-8",
    )
    from core.application.config_store import ConfigStore

    return LLMConfigurationService(
        config_store=ConfigStore(config_path),
        credential_store=CredentialStore(tmp_path / "credentials.json"),
        catalog=ModelCatalogService(tmp_path / "catalog_cache.json"),
    )


_DECLARED = [
    "acme-plain",
    {
        "id": "acme-think",
        "label": "Acme Think",
        "contextWindow": 65536,
        "maxOutputTokens": 4096,
        "reasoningEfforts": ["off", "high", "max"],
    },
    {"id": "acme-flat", "reasoningEfforts": False},
]


def test_declared_entries_shape_the_manual_catalog(tmp_path: Path) -> None:
    service = _service(tmp_path, _DECLARED)
    listing = service.list_models("acme")
    by_id = {model["id"]: model for model in listing["models"]}
    assert set(by_id) == {"acme-plain", "acme-think", "acme-flat"}
    think = by_id["acme-think"]
    assert think["name"] == "Acme Think"
    assert think["contextWindow"] == 65536
    assert think["maxOutputTokens"] == 4096
    assert think["reasoning"]["supportedEfforts"] == ["high", "max"]
    assert think["reasoning"]["mandatory"] is False
    # Declared non-reasoning: explicit empty controls, not an absent answer.
    assert by_id["acme-flat"]["reasoning"]["supportedEfforts"] == []
    # A plain string keeps the old shape: id-as-name.
    assert by_id["acme-plain"]["name"] == "acme-plain"


def test_declarations_reach_execution_capabilities_offline(tmp_path: Path) -> None:
    service = _service(tmp_path, _DECLARED)
    capabilities = service.model_reasoning("acme", "acme-think")
    assert capabilities is not None
    assert capabilities.supported_efforts == ("high", "max")
    # "off" declared → reasoning can be disabled.
    assert capabilities.mandatory is False

    second = tmp_path / "second"
    second.mkdir()
    mandatory = _service(
        second,
        [{"id": "acme-always", "reasoningEfforts": ["high"]}],
    )
    capabilities = mandatory.model_reasoning("acme", "acme-always")
    assert capabilities is not None and capabilities.mandatory is True


def test_public_view_carries_ids_and_declarations_side_by_side(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, _DECLARED)
    connections = service.list_connections()["connections"]
    acme = next(entry for entry in connections if entry["id"] == "acme")
    # Existing consumers keep the plain id list byte-for-byte.
    assert acme["manualModels"] == ["acme-plain", "acme-think", "acme-flat"]
    entries = {entry["id"]: entry for entry in acme["manualModelEntries"]}
    assert entries["acme-think"]["label"] == "Acme Think"
    assert "label" not in entries["acme-plain"]


def test_declaration_outranks_a_remote_snapshot_for_cached_model(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "deepcode_config.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": {
                    "profiles": {
                        "acme": {
                            "template": "custom",
                            "adapter": "openai_compat",
                            "apiBase": "https://acme.example/v1",
                            "manualModels": [
                                {"id": "acme-think", "contextWindow": 1234}
                            ],
                        }
                    },
                    "custom": {"apiKey": "test-key"},
                }
            }
        ),
        encoding="utf-8",
    )
    resolver = ConnectionResolver(
        load_config(config_path=config_path),
        CredentialStore(tmp_path / "credentials.json"),
    )
    connection = resolver.resolve_connection("acme")
    catalog = ModelCatalogService(tmp_path / "catalog_cache.json")
    model = catalog.cached_model(connection, "acme-think")
    assert model is not None and model.context_window == 1234


def test_catalog_cache_is_keyed_by_the_settings_that_shape_it(
    tmp_path: Path,
) -> None:
    """Switching a connection to a manual list (or editing a declaration)
    must invalidate the cached listing. The executable fingerprint alone
    deliberately ignores model lists — it gates admitted Turns — so the
    catalog needs its own key, or a stale remote listing wins for a whole
    TTL after the user adopts models."""
    from unittest.mock import patch

    from core.providers.catalog_service import CatalogModel

    remote = (
        CatalogModel(
            id="acme-think",
            name="Remote Name",
            context_window=999_999,
            max_output_tokens=8192,
        ),
    )
    auto = _service(tmp_path, [], model_catalog="auto")
    with patch.object(ModelCatalogService, "_fetch", return_value=remote):
        assert auto.list_models("acme", refresh=True)["source"] == "remote"

    # Same connection identity, now serving declarations from a manual list.
    declared = _service(
        tmp_path,
        [{"id": "acme-think", "label": "Declared Name", "contextWindow": 65536}],
    )
    listing = declared.list_models("acme")
    row = listing["models"][0]
    assert listing["source"] == "manual"
    assert (row["name"], row["contextWindow"]) == ("Declared Name", 65536)

    # Editing the declaration moves the key again — no stale hit.
    edited = _service(
        tmp_path,
        [{"id": "acme-think", "label": "Renamed", "contextWindow": 65536}],
    )
    assert edited.list_models("acme")["models"][0]["name"] == "Renamed"
