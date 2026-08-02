from app_server import runtime_probe


def test_runtime_probe_imports_lazy_desktop_capabilities(monkeypatch):
    imported: list[str] = []
    monkeypatch.setattr(
        runtime_probe.importlib,
        "import_module",
        lambda name: imported.append(name),
    )

    result = runtime_probe.verify_runtime()

    assert result["ok"] is True
    assert result["modules"] == list(runtime_probe.RUNTIME_MODULES)
    assert imported == list(runtime_probe.RUNTIME_MODULES)
    assert result["providers"] == ["anthropic", "openai_compat"]
    assert result["paperFallback"] == "pypdf"
