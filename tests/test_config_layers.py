"""Effective-config provenance (``core.config.effective_config_layers``).

The dsh ``--dump-config`` counterpart: every configured leaf, the layer that
supplied it (project overrides user, exactly as the loader merges), and a
credential-safe preview. The redaction rule is subtree-wide by path segment:
``env``/``headers``/``proxy`` hide their VALUES too, because those routinely
embed credentials under harmless-looking keys.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import effective_config_layers


@pytest.fixture()
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        "core.config.home_config_path",
        lambda: home / "deepcode_config.json",
    )
    return home


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_leaves_carry_their_providing_layer(
    config_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        config_home / "deepcode_config.json",
        {
            "agents": {"defaults": {"model": "user-model", "temperature": 0.1}},
        },
    )
    workspace = tmp_path / "proj"
    workspace.mkdir()
    _write(
        workspace / "deepcode_config.json",
        {"agents": {"defaults": {"model": "project-model"}}},
    )
    monkeypatch.setattr(
        "core.config.project_config_path",
        lambda ws: Path(ws) / "deepcode_config.json",
    )

    rows = {r["path"]: r for r in effective_config_layers(workspace)}
    assert rows["agents.defaults.model"]["source"] == "project"
    assert rows["agents.defaults.model"]["value"] == '"project-model"'
    assert rows["agents.defaults.temperature"]["source"] == "user"


def test_without_workspace_everything_is_user_layer(config_home: Path) -> None:
    _write(
        config_home / "deepcode_config.json",
        {"tools": {"defaultSearchServer": "filesystem"}},
    )
    rows = effective_config_layers(None)
    assert all(r["source"] == "user" for r in rows)
    assert any(r["path"] == "tools.defaultSearchServer" for r in rows)


def test_credential_shaped_subtrees_never_echo_values(config_home: Path) -> None:
    _write(
        config_home / "deepcode_config.json",
        {
            # A fixture, not a credential — but any value after "apiKey"
            # looks like one to a secret scanner, hence the inline allow.
            "providers": {"openrouter": {"apiKey": "hunter2"}},  # gitleaks:allow
            "subagents": {
                "codex": {"env": {"HTTPS_PROXY": "http://user:pass@proxy:1"}}
            },
        },
    )
    rows = effective_config_layers(None)
    dumped = json.dumps(rows)
    assert "hunter2" not in dumped
    assert "user:pass" not in dumped
    by_path = {r["path"]: r["value"] for r in rows}
    assert by_path["providers.openrouter.apiKey"] == "••• (set)"
    # env is subtree-redacted: the leaf under it hides even its NAME's value.
    assert by_path["subagents.codex.env.HTTPS_PROXY"] == "••• (set)"


def test_comments_are_skipped_and_long_values_truncated(
    config_home: Path,
) -> None:
    _write(
        config_home / "deepcode_config.json",
        {"$comment": "notes", "agents": {"defaults": {"model": "m" * 300}}},
    )
    rows = effective_config_layers(None)
    assert not any("$comment" in r["path"] for r in rows)
    (model_row,) = [r for r in rows if r["path"] == "agents.defaults.model"]
    assert model_row["value"].endswith("…")
    assert len(model_row["value"]) < 120
