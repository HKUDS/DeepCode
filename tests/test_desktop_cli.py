from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cli import desktop_cli


def checkout(root):
    desktop = root / "desktop"
    (desktop / "src-tauri").mkdir(parents=True)
    (desktop / "package.json").write_text("{}")
    (desktop / "src-tauri/tauri.conf.json").write_text("{}")
    return root


def test_installed_cli_finds_its_local_install_source_without_using_cwd(
    tmp_path, monkeypatch
):
    source = checkout(tmp_path / "original source")
    unrelated = checkout(tmp_path / "unrelated")
    monkeypatch.chdir(unrelated)
    monkeypatch.setattr(
        desktop_cli, "__file__", str(tmp_path / "installed/cli/desktop_cli.py")
    )
    monkeypatch.setattr(
        desktop_cli,
        "distribution",
        lambda _: SimpleNamespace(
            read_text=lambda _: json.dumps({"url": source.as_uri()})
        ),
    )
    assert desktop_cli._source_checkout() == source
    monkeypatch.setattr(
        desktop_cli, "distribution", lambda _: SimpleNamespace(read_text=lambda _: None)
    )
    assert desktop_cli._source_checkout() is None


def test_source_launch_from_another_directory_reuses_built_resources(
    tmp_path, monkeypatch
):
    root = checkout(tmp_path / "DeepCode source")
    desktop = root / "desktop"
    tool = (
        desktop
        / "node_modules/.bin"
        / ("tauri.cmd" if desktop_cli.os.name == "nt" else "tauri")
    )
    tool.parent.mkdir(parents=True)
    tool.touch()
    binary = (
        desktop
        / "build/sidecar/dist/deepcode-app-server"
        / (
            "deepcode-app-server.exe"
            if desktop_cli.os.name == "nt"
            else "deepcode-app-server"
        )
    )
    binary.parent.mkdir(parents=True)
    binary.touch()
    monkeypatch.setattr(desktop_cli.shutil, "which", lambda name: f"/tools/{name}")
    monkeypatch.setattr(
        desktop_cli.subprocess,
        "run",
        lambda *_a, **_kw: pytest.fail(
            "Prepared source should not reinstall dependencies"
        ),
    )
    calls = []
    monkeypatch.setattr(
        desktop_cli.subprocess,
        "call",
        lambda args, **kwargs: calls.append((args, kwargs)) or 7,
    )
    monkeypatch.chdir(tmp_path)
    assert desktop_cli.run(["--source", str(root), "--", "--no-watch"]) == 7
    assert calls[0][0] == ["/tools/npm", "run", "tauri", "--", "dev", "--no-watch"]
    assert calls[0][1]["cwd"] == desktop


def test_first_source_launch_prepares_missing_resources_once(tmp_path, monkeypatch):
    root = checkout(tmp_path / "source")
    monkeypatch.setattr(desktop_cli.shutil, "which", lambda name: name)
    calls = []
    monkeypatch.setattr(
        desktop_cli.subprocess, "run", lambda args, **kwargs: calls.append(args)
    )
    monkeypatch.setattr(desktop_cli.subprocess, "call", lambda *_a, **_kw: 0)
    assert desktop_cli.run(["--source", str(root)]) == 0
    assert calls == [
        ["npm", "ci"],
        ["npm", "run", "setup:sidecar"],
        ["npm", "run", "build:sidecar"],
    ]


def test_explicit_missing_source_never_falls_back_to_another_installation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        desktop_cli,
        "_source_checkout",
        lambda: pytest.fail("Explicit source must fail closed"),
    )
    assert desktop_cli.run(["--source", str(tmp_path / "missing")]) == 1


def test_macos_installed_app_opens_without_build_tools(tmp_path, monkeypatch):
    app = tmp_path / "DeepCode.app"
    app.mkdir()
    monkeypatch.setattr(desktop_cli.sys, "platform", "darwin")
    monkeypatch.setattr(desktop_cli, "_source_checkout", lambda: None)
    monkeypatch.setattr(desktop_cli, "_installed_app", lambda: app)
    calls = []
    monkeypatch.setattr(
        desktop_cli.subprocess, "call", lambda args: calls.append(args) or 0
    )
    assert desktop_cli.run([]) == 0
    assert calls == [["open", "-a", str(app)]]


def test_installed_native_executable_is_launched_without_a_shell(tmp_path, monkeypatch):
    app = tmp_path / "DeepCode AppImage"
    app.write_bytes(b"\x7fELF")
    calls = []
    monkeypatch.setattr(
        desktop_cli.subprocess,
        "Popen",
        lambda args, **kwargs: calls.append((args, kwargs)),
    )
    assert desktop_cli.run(["--app", str(app)]) == 0
    assert calls[0][0] == [str(app)]
    assert not calls[0][1].get("shell", False)


def test_missing_desktop_is_reported_without_opening_web(monkeypatch, capsys):
    monkeypatch.setattr(desktop_cli, "_source_checkout", lambda: None)
    monkeypatch.setattr(desktop_cli, "_installed_app", lambda: None)
    assert desktop_cli.run([]) == 1
    assert "Desktop is not installed" in capsys.readouterr().err


def test_top_level_command_dispatches_desktop(monkeypatch):
    import deepcode

    monkeypatch.setattr(deepcode, "_bootstrap_logging", lambda: None)
    monkeypatch.setattr(deepcode.sys, "argv", ["deepcode", "desktop", "--setup"])
    calls = []
    monkeypatch.setattr(desktop_cli, "run", lambda args: calls.append(args) or 0)
    with pytest.raises(SystemExit) as exit:
        deepcode.main()
    assert exit.value.code == 0 and calls == [["--setup"]]
