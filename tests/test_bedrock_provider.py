"""Amazon Bedrock's bounded OpenAI-compatible provider integration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import DeepCodeConfig, ProvidersConfig  # noqa: E402
from core.providers.catalog import resolve_model_info  # noqa: E402
from core.providers.catalog_service import ModelCatalogService  # noqa: E402
from core.providers.credentials import CredentialStore  # noqa: E402
from core.providers.openai_compat import OpenAICompatProvider  # noqa: E402
from core.providers.profiles import ConnectionResolver  # noqa: E402
from core.providers.registry import find_by_name  # noqa: E402


BEDROCK_MODEL_IDS = (
    "anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-sonnet-4-6",
    "eu.anthropic.claude-sonnet-4-6",
    "au.anthropic.claude-sonnet-4-6",
    "jp.anthropic.claude-sonnet-4-6",
    "global.anthropic.claude-sonnet-4-6",
)


def _connection(tmp_path: Path):
    credentials = CredentialStore(tmp_path / "credentials.json")
    credentials.set("work-bedrock", "bedrock-test-secret")
    config = DeepCodeConfig.model_validate(
        {
            "providers": {
                "profiles": {
                    "work-bedrock": {
                        "template": "bedrock",
                        "apiBase": (
                            "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1"
                        ),
                    }
                }
            }
        }
    )
    return ConnectionResolver(config, credentials).resolve_connection("work-bedrock")


def test_bedrock_is_a_region_scoped_openai_compatible_template() -> None:
    spec = find_by_name("bedrock")

    assert spec is not None
    assert spec.display_name == "Amazon Bedrock"
    assert spec.backend == "openai_compat"
    assert spec.is_gateway is True
    assert spec.requires_api_base is True
    assert spec.default_api_base == ""
    assert spec.detect_by_base_keyword == "bedrock-runtime"
    assert spec.env_key == "AWS_BEARER_TOKEN_BEDROCK"
    assert hasattr(ProvidersConfig(), "bedrock")


def test_bedrock_profile_keeps_the_region_url_and_bearer_credential(
    tmp_path: Path,
) -> None:
    connection = _connection(tmp_path)

    assert connection.provider_name == "bedrock"
    assert connection.adapter == "openai_compat"
    assert connection.api_base == (
        "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1"
    )
    assert connection.api_key == "bedrock-test-secret"
    assert connection.model_catalog == "openai"
    assert connection.is_usable is True


def test_bedrock_requires_a_user_supplied_region_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "bedrock-test-secret")
    connection = ConnectionResolver(
        DeepCodeConfig(), CredentialStore(tmp_path / "credentials.json")
    ).resolve_connection("bedrock")

    assert connection.api_key == "bedrock-test-secret"
    assert connection.api_base is None
    assert connection.is_configured is False
    assert connection.is_usable is False


def test_bedrock_catalog_uses_models_endpoint_and_bearer_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection(tmp_path)
    seen: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [{"id": "us.anthropic.claude-sonnet-4-6"}]}

    class Client:
        def __init__(self, **kwargs: object) -> None:
            seen["client"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str, *, headers: dict[str, str]):
            seen["url"] = url
            seen["headers"] = headers
            return Response()

    monkeypatch.setattr("core.providers.catalog_service.httpx.Client", Client)
    result = ModelCatalogService(tmp_path / "cache.json").list_models(
        connection, refresh=True
    )

    assert result.source == "remote"
    assert [model.id for model in result.models] == ["us.anthropic.claude-sonnet-4-6"]
    assert seen["url"] == (
        "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1/models"
    )
    assert seen["headers"] == {"Authorization": "Bearer bedrock-test-secret"}


def test_bedrock_catalog_fallback_lists_exact_inference_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection(tmp_path)
    service = ModelCatalogService(tmp_path / "cache.json")

    def offline(_connection):
        raise OSError("network unavailable")

    monkeypatch.setattr(service, "_fetch", offline)
    result = service.list_models(connection, refresh=True)

    assert result.source == "fallback"
    assert result.stale is True
    assert tuple(model.id for model in result.models) == tuple(
        sorted(BEDROCK_MODEL_IDS)
    )
    assert {model.context_window for model in result.models} == {1_000_000}
    assert {model.max_output_tokens for model in result.models} == {64_000}
    assert all(model.reasoning is None for model in result.models)


def test_bedrock_chat_request_preserves_the_dotted_model_id() -> None:
    provider = OpenAICompatProvider(
        api_key="bedrock-test-secret",
        api_base="https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1",
        default_model="us.anthropic.claude-sonnet-4-6",
        spec=find_by_name("bedrock"),
    )

    kwargs = provider._build_kwargs(
        [{"role": "user", "content": "Hello"}],
        tools=None,
        model=None,
        max_tokens=512,
        temperature=0.2,
        reasoning_effort=None,
        tool_choice=None,
    )

    assert kwargs["model"] == "us.anthropic.claude-sonnet-4-6"
    assert kwargs["max_tokens"] == 512
    assert "reasoning_effort" not in kwargs
    assert provider._should_use_responses_api(None, None) is False


@pytest.mark.parametrize("model_id", BEDROCK_MODEL_IDS)
def test_bedrock_model_ids_have_conservative_offline_metadata(model_id: str) -> None:
    info = resolve_model_info(model_id)

    assert info.id == model_id
    assert info.source == "seed"
    assert info.context_window == 1_000_000
    assert info.max_output_tokens == 64_000
    assert info.reasoning is None
