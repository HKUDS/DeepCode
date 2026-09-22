"""Tests for the dictation application service and its RPC surface.

The service is the policy layer: whether dictation is configured, whether the
endpoint passes the egress policy, and how each failure is reported. Each of
those decisions is asserted here, including the ones that must *not* make a
request — a blocked endpoint or a malformed clip must fail before a byte
leaves the machine.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app_server.connection import ConnectionState  # noqa: E402
from app_server.dispatcher import Dispatcher, InvalidParams, Params  # noqa: E402
from core.application.application import DeepCodeApplication  # noqa: E402
from core.application.config_store import ConfigStore  # noqa: E402
from core.application.dictation_service import DictationService  # noqa: E402
from core.application.errors import (  # noqa: E402
    DictationNotConfiguredError,
    DictationUnavailableError,
    InvalidArgumentError,
)
from core.config import home_config_path  # noqa: E402
from core.dictation.client import SpeechToTextClient  # noqa: E402

WEBM = b"\x1aE\xdf\xa3" + b"\x00" * 60
ENDPOINT = "http://127.0.0.1:8000/v1"
MODEL = "mlx-community/parakeet-tdt-0.6b-v3"

SCHEMA = ROOT / "protocol" / "app-server.schema.json"


def _clip() -> str:
    return base64.b64encode(WEBM).decode("ascii")


def _config_file(tmp_path: Path, payload: dict) -> ConfigStore:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return ConfigStore(path)


def _dictation_config(**overrides) -> dict:
    block = {"endpoint": ENDPOINT, "model": MODEL}
    block.update(overrides)
    return {"dictation": block}


def _service(tmp_path: Path, payload: dict | None = None, **kwargs) -> DictationService:
    store = _config_file(tmp_path, payload) if payload is not None else None
    if store is None:
        return DictationService(
            config_store=ConfigStore(tmp_path / "absent.json"), **kwargs
        )
    return DictationService(config_store=store, **kwargs)


def _transport(handler):
    """A client factory that routes the real client through a stub transport."""

    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def factory(config, api_key, language):
        return SpeechToTextClient(
            config.endpoint,
            config.model,
            language=language,
            api_key=api_key,
            timeout_seconds=config.timeout_seconds,
            transport=httpx.MockTransport(wrapped),
        )

    return factory, seen


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_is_unavailable_without_a_dictation_block(tmp_path):
    assert _service(tmp_path).status() == {
        "available": False,
        "model": None,
        "maxAudioSeconds": None,
    }


def test_status_reports_the_configured_endpoint(tmp_path):
    service = _service(tmp_path, _dictation_config(maxAudioSeconds=30))

    assert service.status() == {
        "available": True,
        "model": MODEL,
        "maxAudioSeconds": 30,
    }


def test_status_and_transcribe_support_local_endpoint(tmp_path):
    service = _service(
        tmp_path,
        {"dictation": {"endpoint": "local", "model": MODEL}},
        client_factory=lambda cfg, key, lang: type(
            "StubLocal", (), {"transcribe": lambda *a, **k: "local ok"}
        )(),
    )

    assert service.status()["available"] is True
    res = service.transcribe(audio=_clip(), mime_type="audio/webm")
    assert res == {"text": "local ok", "model": MODEL}


def test_status_defaults_the_audio_cap(tmp_path):
    assert _service(tmp_path, _dictation_config()).status()["maxAudioSeconds"] == 120


def test_status_does_not_reveal_whether_a_secret_resolves(tmp_path):
    # A missing bearer token must not make the microphone look disabled: the
    # user finds out on the first clip, with a message that names the variable.
    payload = _dictation_config(apiKeyEnv="DEEPCODE_TEST_MISSING_KEY")
    assert _service(tmp_path, payload).status()["available"] is True


# ---------------------------------------------------------------------------
# transcribe
# ---------------------------------------------------------------------------


def test_transcribe_without_a_dictation_block_is_permanently_unconfigured(tmp_path):
    with pytest.raises(DictationNotConfiguredError) as excinfo:
        _service(tmp_path).transcribe(audio=_clip(), mime_type="audio/webm")

    assert excinfo.value.code == "DICTATION_NOT_CONFIGURED"
    assert excinfo.value.retryable is False


def test_transcribe_returns_text_and_the_configured_model(tmp_path):
    factory, seen = _transport(
        lambda request: httpx.Response(200, json={"text": "olá"})
    )
    service = _service(tmp_path, _dictation_config(), client_factory=factory)

    result = service.transcribe(audio=_clip(), mime_type="audio/webm")

    assert result == {"text": "olá", "model": MODEL}
    assert str(seen[0].url) == f"{ENDPOINT}/audio/transcriptions"
    assert b'filename="dictation.webm"' in seen[0].read()


def test_transcribe_rejects_a_malformed_clip_before_any_request(tmp_path):
    factory, seen = _transport(lambda request: httpx.Response(200, json={"text": "x"}))
    service = _service(tmp_path, _dictation_config(), client_factory=factory)

    with pytest.raises(InvalidArgumentError, match="valid base64"):
        service.transcribe(audio="!!!", mime_type="audio/webm")

    assert seen == []


def test_transcribe_rejects_an_unsupported_mime_type(tmp_path):
    factory, seen = _transport(lambda request: httpx.Response(200, json={"text": "x"}))
    service = _service(tmp_path, _dictation_config(), client_factory=factory)

    with pytest.raises(InvalidArgumentError, match="unsupported audio mimeType"):
        service.transcribe(audio=_clip(), mime_type="video/mp4")

    assert seen == []


def test_transcribe_surfaces_a_server_error_as_unavailable(tmp_path):
    factory, _ = _transport(lambda request: httpx.Response(503, text="loading model"))
    service = _service(tmp_path, _dictation_config(), client_factory=factory)

    with pytest.raises(DictationUnavailableError) as excinfo:
        service.transcribe(audio=_clip(), mime_type="audio/webm")

    assert excinfo.value.code == "DICTATION_UNAVAILABLE"
    assert excinfo.value.retryable is True
    assert "loading model" not in str(excinfo.value)


def test_transcribe_marks_a_request_error_as_not_retryable(tmp_path):
    # A 404 means the path is wrong, which retrying cannot fix; the UI must not
    # offer "try again" for it.
    factory, _ = _transport(lambda request: httpx.Response(404, text="not found"))
    service = _service(tmp_path, _dictation_config(), client_factory=factory)

    with pytest.raises(DictationUnavailableError) as excinfo:
        service.transcribe(audio=_clip(), mime_type="audio/webm")

    assert excinfo.value.retryable is False


def test_transcribe_passes_an_empty_transcript_through(tmp_path):
    # A clip with no speech is not an error; the caller decides what to do.
    factory, _ = _transport(lambda request: httpx.Response(200, json={"text": ""}))
    service = _service(tmp_path, _dictation_config(), client_factory=factory)

    assert service.transcribe(audio=_clip(), mime_type="audio/webm")["text"] == ""


# ---------------------------------------------------------------------------
# secrets and language
# ---------------------------------------------------------------------------


def test_transcribe_reads_the_api_key_from_the_named_environment_variable(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DEEPCODE_TEST_DICTATION_KEY", "s3cret")
    factory, seen = _transport(lambda request: httpx.Response(200, json={"text": "x"}))
    service = _service(
        tmp_path,
        _dictation_config(apiKeyEnv="DEEPCODE_TEST_DICTATION_KEY"),
        client_factory=factory,
    )

    service.transcribe(audio=_clip(), mime_type="audio/webm")

    assert seen[0].headers["authorization"] == "Bearer s3cret"


def test_transcribe_fails_when_the_named_environment_variable_is_unset(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DEEPCODE_TEST_DICTATION_KEY", raising=False)
    factory, seen = _transport(lambda request: httpx.Response(200, json={"text": "x"}))
    service = _service(
        tmp_path,
        _dictation_config(apiKeyEnv="DEEPCODE_TEST_DICTATION_KEY"),
        client_factory=factory,
    )

    with pytest.raises(
        DictationNotConfiguredError, match="DEEPCODE_TEST_DICTATION_KEY"
    ):
        service.transcribe(audio=_clip(), mime_type="audio/webm")

    assert seen == []


def test_transcribe_sends_no_token_without_an_api_key_env(tmp_path):
    factory, seen = _transport(lambda request: httpx.Response(200, json={"text": "x"}))
    service = _service(tmp_path, _dictation_config(), client_factory=factory)

    service.transcribe(audio=_clip(), mime_type="audio/webm")

    assert "authorization" not in seen[0].headers


def test_configured_language_wins_over_the_request_hint(tmp_path):
    factory, seen = _transport(lambda request: httpx.Response(200, json={"text": "x"}))
    service = _service(
        tmp_path, _dictation_config(language="pt"), client_factory=factory
    )

    service.transcribe(audio=_clip(), mime_type="audio/webm", language="en")

    body = seen[0].read()
    assert b"\r\npt\r\n" in body
    assert b"\r\nen\r\n" not in body


def test_request_language_is_used_when_none_is_configured(tmp_path):
    factory, seen = _transport(lambda request: httpx.Response(200, json={"text": "x"}))
    service = _service(tmp_path, _dictation_config(), client_factory=factory)

    service.transcribe(audio=_clip(), mime_type="audio/webm", language="de")

    assert b"\r\nde\r\n" in seen[0].read()


# ---------------------------------------------------------------------------
# egress policy
# ---------------------------------------------------------------------------


def _blocking_config(**dictation) -> dict:
    payload = _dictation_config(**dictation)
    payload["providers"] = {"egress": {"blockedDomains": ["127.0.0.1"]}}
    return payload


def test_egress_policy_blocks_the_endpoint_before_the_audio_is_sent(tmp_path):
    factory, seen = _transport(lambda request: httpx.Response(200, json={"text": "x"}))
    service = _service(tmp_path, _blocking_config(), client_factory=factory)

    with pytest.raises(DictationUnavailableError) as excinfo:
        service.transcribe(audio=_clip(), mime_type="audio/webm")

    assert "egress" in str(excinfo.value)
    # Not retryable: the same call will be refused identically until the user
    # changes the policy.
    assert excinfo.value.retryable is False
    assert seen == []


def test_egress_warn_mode_records_the_denial_and_continues(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPCODE_EGRESS_MODE", "warn")
    factory, seen = _transport(lambda request: httpx.Response(200, json={"text": "ok"}))
    service = _service(tmp_path, _blocking_config(), client_factory=factory)

    assert service.transcribe(audio=_clip(), mime_type="audio/webm")["text"] == "ok"
    assert len(seen) == 1


def test_egress_allows_an_endpoint_that_is_not_blocked(tmp_path):
    payload = _dictation_config()
    payload["providers"] = {"egress": {"allowedDomains": ["127.0.0.1"]}}
    factory, seen = _transport(lambda request: httpx.Response(200, json={"text": "x"}))
    service = _service(tmp_path, payload, client_factory=factory)

    service.transcribe(audio=_clip(), mime_type="audio/webm")

    assert len(seen) == 1


# ---------------------------------------------------------------------------
# project scoping
# ---------------------------------------------------------------------------


def test_project_scoped_transcription_without_a_project_service_is_invalid(tmp_path):
    service = _service(tmp_path, _dictation_config())

    with pytest.raises(InvalidArgumentError, match="project-scoped"):
        service.transcribe(audio=_clip(), mime_type="audio/webm", project_id="p1")


# ---------------------------------------------------------------------------
# RPC surface
# ---------------------------------------------------------------------------


def _dispatcher(tmp_path: Path, name: str, *, configured: bool) -> Dispatcher:
    """A real application over an isolated home config.

    ``configured`` decides whether the home config carries a dictation block,
    which is what the whole feature hangs off.
    """

    if configured:
        home = home_config_path()
        home.parent.mkdir(parents=True, exist_ok=True)
        home.write_text(json.dumps(_dictation_config()), encoding="utf-8")
    application = DeepCodeApplication.open(tmp_path / f"{name}.sqlite3")
    return Dispatcher(application, ConnectionState(application.broker))


@pytest.fixture
def unconfigured(tmp_path) -> Dispatcher:
    return _dispatcher(tmp_path, "unconfigured", configured=False)


@pytest.fixture
def configured(tmp_path) -> Dispatcher:
    return _dispatcher(tmp_path, "configured", configured=True)


def test_dictation_status_reports_the_capability_as_absent(unconfigured):
    assert unconfigured._dictation_status(Params({})) == {
        "available": False,
        "model": None,
        "maxAudioSeconds": None,
    }


def test_dictation_status_reports_the_configured_endpoint(configured):
    assert configured._dictation_status(Params({})) == {
        "available": True,
        "model": MODEL,
        "maxAudioSeconds": 120,
    }


def test_dictation_status_rejects_unknown_parameters(configured):
    with pytest.raises(InvalidParams):
        configured._dictation_status(Params({"audio": "x"}))


def test_dictation_transcribe_requires_audio_and_mime_type(configured):
    with pytest.raises(InvalidParams):
        configured._dictation_transcribe(Params({}))
    with pytest.raises(InvalidParams):
        configured._dictation_transcribe(Params({"audio": "x"}))
    with pytest.raises(InvalidParams):
        configured._dictation_transcribe(Params({"audio": "x", "mimeType": "  "}))


def test_dictation_transcribe_rejects_unknown_parameters(configured):
    with pytest.raises(InvalidParams):
        configured._dictation_transcribe(
            Params({"audio": "x", "mimeType": "audio/webm", "model": "other"})
        )


def test_dictation_transcribe_reports_an_unconfigured_endpoint(unconfigured):
    with pytest.raises(DictationNotConfiguredError):
        unconfigured._dictation_transcribe(
            Params({"audio": _clip(), "mimeType": "audio/webm"})
        )


def test_dictation_transcribe_validates_the_clip_before_the_endpoint(configured):
    # Same reason as the service test: nothing may be sent for a clip that
    # cannot be read, so this fails without a listening endpoint.
    with pytest.raises(InvalidArgumentError, match="valid base64"):
        configured._dictation_transcribe(
            Params({"audio": "!!!", "mimeType": "audio/webm"})
        )


def test_schema_requires_the_dictation_audio_payload():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    definitions = schema["$defs"]
    params = definitions["MethodParams"]["properties"]

    assert params["dictation/status"] == {"$ref": "#/$defs/OptionalProjectParams"}
    transcribe = definitions["DictationTranscribeParams"]
    assert transcribe["required"] == ["audio", "mimeType"]
    assert transcribe["properties"]["projectId"]["pattern"] == "^proj_"
    assert transcribe["properties"]["audio"]["minLength"] == 1
    assert transcribe["properties"]["language"]["type"] == ["string", "null"]
    assert definitions["MethodResults"]["properties"]["dictation/transcribe"] == {
        "$ref": "#/$defs/DictationTranscribeResult"
    }
    assert definitions["DictationStatusResult"]["properties"]["maxAudioSeconds"][
        "type"
    ] == ["integer", "null"]
