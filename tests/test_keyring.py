"""Tests for P0-3 system keyring integration (Codex lesson)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.keyring import (
    keyring_enabled,
    keyring_get,
    keyring_set,
)

# ---- env switch -------------------------------------------------------------


def test_keyring_enabled_by_default(monkeypatch):
    monkeypatch.delenv("DEEPCODE_KEYRING", raising=False)
    assert keyring_enabled() is True


def test_keyring_env_disable(monkeypatch):
    for v in ("0", "false", "off", "no"):
        monkeypatch.setenv("DEEPCODE_KEYRING", v)
        assert keyring_enabled() is False


# ---- JSON fallback ----------------------------------------------------------


def test_json_fallback_set_and_get(tmp_path, monkeypatch):
    from core import keyring as kr

    target = tmp_path / "keyring.json"
    monkeypatch.setattr(kr, "_KEYRING_FILE", target)
    # Force the system-keyring path to fail so we exercise the JSON fallback.
    monkeypatch.setattr(kr, "_keyring_pkg_get", lambda *a: None)
    monkeypatch.setattr(kr, "_keyring_pkg_set", lambda *a: False)

    assert keyring_set("MY_API_KEY", "secret-123") is True
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "MY_API_KEY": "secret-123"
    }
    assert keyring_get("MY_API_KEY") == "secret-123"


def test_json_fallback_get_missing(tmp_path, monkeypatch):
    from core import keyring as kr

    target = tmp_path / "keyring.json"
    monkeypatch.setattr(kr, "_KEYRING_FILE", target)
    monkeypatch.setattr(kr, "_keyring_pkg_get", lambda *a: None)
    assert keyring_get("NOPE") is None


def test_json_fallback_corrupt_file(tmp_path, monkeypatch):
    from core import keyring as kr

    target = tmp_path / "keyring.json"
    target.write_text("not-json{{{", encoding="utf-8")
    monkeypatch.setattr(kr, "_KEYRING_FILE", target)
    monkeypatch.setattr(kr, "_keyring_pkg_get", lambda *a: None)
    assert keyring_get("MY_API_KEY") is None


def test_get_disabled(monkeypatch):
    monkeypatch.setenv("DEEPCODE_KEYRING", "0")
    assert keyring_get("anything") is None


def test_get_empty_name(monkeypatch):
    monkeypatch.delenv("DEEPCODE_KEYRING", raising=False)
    assert keyring_get("") is None
    assert keyring_get("   ") is None


# ---- system keyring priority -------------------------------------------------


def test_system_keyring_preferred_over_json(tmp_path, monkeypatch):
    from core import keyring as kr

    target = tmp_path / "keyring.json"
    target.write_text(json.dumps({"K": "file-value"}), encoding="utf-8")
    monkeypatch.setattr(kr, "_KEYRING_FILE", target)
    monkeypatch.setattr(kr, "_keyring_pkg_get", lambda svc, user: "system-value")
    assert keyring_get("K") == "system-value"


def test_system_keyring_missing_falls_to_json(tmp_path, monkeypatch):
    from core import keyring as kr

    target = tmp_path / "keyring.json"
    target.write_text(json.dumps({"K": "file-value"}), encoding="utf-8")
    monkeypatch.setattr(kr, "_KEYRING_FILE", target)
    monkeypatch.setattr(kr, "_keyring_pkg_get", lambda svc, user: None)
    assert keyring_get("K") == "file-value"


# ---- keyring module integration (no config.py coupling) ---------------------
# NOTE: the keyring→config wiring (${VAR} falls through to the keyring) lives
# in core/config.py and is out of scope for this PR; these tests exercise the
# keyring module's own resolution contract.


def test_keyring_get_prefers_system_then_file(tmp_path, monkeypatch):
    from core import keyring as kr

    target = tmp_path / "keyring.json"
    target.write_text(json.dumps({"K": "file-value"}), encoding="utf-8")
    monkeypatch.setattr(kr, "_KEYRING_FILE", target)

    # System keyring has the value → wins.
    monkeypatch.setattr(kr, "_keyring_pkg_get", lambda svc, user: "system-value")
    assert keyring_get("K") == "system-value"

    # System keyring misses → falls back to the JSON file.
    monkeypatch.setattr(kr, "_keyring_pkg_get", lambda svc, user: None)
    assert keyring_get("K") == "file-value"


def test_keyring_get_disabled_returns_none(monkeypatch, tmp_path):
    from core import keyring as kr

    monkeypatch.setattr(kr, "_KEYRING_FILE", tmp_path / "keyring.json")
    monkeypatch.setattr(kr, "_keyring_pkg_get", lambda svc, user: "secret")
    monkeypatch.setenv("DEEPCODE_KEYRING", "0")
    assert keyring_get("K") is None


def test_keyring_get_never_raises(tmp_path, monkeypatch):
    from core import keyring as kr

    monkeypatch.setattr(kr, "_KEYRING_FILE", tmp_path / "nope.json")

    def boom(svc, user):
        raise RuntimeError("keyring backend down")

    monkeypatch.setattr(kr, "_keyring_pkg_get", boom)
    assert keyring_get("K") is None  # fail-soft


def test_keyring_set_falls_back_to_file_when_system_fails(tmp_path, monkeypatch):
    from core import keyring as kr

    target = tmp_path / "keyring.json"
    monkeypatch.setattr(kr, "_KEYRING_FILE", target)
    monkeypatch.setattr(kr, "_keyring_pkg_set", lambda *a: False)
    assert keyring_set("K", "v") is True
    assert json.loads(target.read_text(encoding="utf-8")) == {"K": "v"}
