from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli import provider_cli
from core.providers.credentials import CredentialStore


def test_json_output_flag_is_accepted_before_or_after_subcommand() -> None:
    assert provider_cli._parser().parse_args(["--json", "list"]).json is True
    assert provider_cli._parser().parse_args(["list", "--json"]).json is True
    assert (
        provider_cli._parser().parse_args(["models", "openrouter", "--json"]).json
        is True
    )


def test_provider_cli_can_verify_one_model_and_print_typed_stages(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []

    def fake_test(_service, connection_id, *, project_id=None, model_id=None):
        calls.append((connection_id, project_id, model_id))
        return {
            "connectionId": connection_id,
            "status": "ready",
            "ok": True,
            "latencyMs": 21,
            "modelCount": 3,
            "error": None,
            "stages": [
                {
                    "id": "credential",
                    "status": "passed",
                    "detail": "Credential found",
                },
                {
                    "id": "catalog",
                    "status": "passed",
                    "detail": "Discovered 3 models",
                },
                {
                    "id": "model",
                    "status": "passed",
                    "detail": "Model accepted the request",
                },
            ],
        }

    monkeypatch.setattr(provider_cli.LLMConfigurationService, "test", fake_test)

    assert (
        provider_cli.run(["test", "router-cli", "--model", "example/verified-model"])
        == 0
    )

    output = capsys.readouterr()
    assert calls == [("router-cli", None, "example/verified-model")]
    assert "ready: router-cli" in output.out
    assert "credential passed" in output.out
    assert "model      passed" in output.out
    assert output.err == ""


def test_provider_cli_writes_shared_connection_without_echoing_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    secret = "provider-cli-secret"
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.setattr(provider_cli.getpass, "getpass", lambda _prompt: secret)

    result = provider_cli.run(
        [
            "set",
            "router-cli",
            "--template",
            "openrouter",
            "--label",
            "Router CLI",
            "--api-key",
            "--model",
            "moonshotai/kimi-k2.6",
            "--json",
        ]
    )

    assert result == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    connection = next(
        item for item in payload["connections"] if item["id"] == "router-cli"
    )
    assert connection["configured"] is True
    assert secret not in output.out
    assert secret not in output.err
    assert secret not in (home / "deepcode_config.json").read_text(encoding="utf-8")
    assert CredentialStore(home / "credentials.json").get("router-cli") == secret

    assert provider_cli.run(["list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert any(item["id"] == "router-cli" for item in listed["connections"])


def test_provider_cli_key_rotation_does_not_reset_connection_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    keys = iter(("first-secret", "rotated-secret"))
    monkeypatch.setattr(provider_cli.getpass, "getpass", lambda _prompt: next(keys))

    assert (
        provider_cli.run(
            [
                "set",
                "router-cli",
                "--template",
                "openrouter",
                "--label",
                "Router CLI",
                "--api-base",
                "https://router.example/v1",
                "--api-key",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert provider_cli.run(["set", "router-cli", "--api-key"]) == 0
    capsys.readouterr()

    profile = json.loads((home / "deepcode_config.json").read_text())["providers"][
        "profiles"
    ]["router-cli"]
    assert profile["template"] == "openrouter"
    assert profile["label"] == "Router CLI"
    assert profile["apiBase"] == "https://router.example/v1"
    assert (
        CredentialStore(home / "credentials.json").get("router-cli") == "rotated-secret"
    )
