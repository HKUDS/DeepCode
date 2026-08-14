from app_server import runtime_probe


def test_runtime_probe_imports_lazy_desktop_capabilities(monkeypatch):
    imported: list[str] = []
    import_module = runtime_probe.importlib.import_module

    def track_runtime_import(name: str, package: str | None = None):
        # Mirror importlib's real signature: transitive imports may use the
        # two-argument relative form (e.g. dateutil's lazy submodules).
        if name in runtime_probe.RUNTIME_MODULES:
            imported.append(name)
        return import_module(name, package)

    monkeypatch.setattr(
        runtime_probe.importlib,
        "import_module",
        track_runtime_import,
    )

    result = runtime_probe.verify_runtime()

    assert result["ok"] is True
    assert result["modules"] == list(runtime_probe.RUNTIME_MODULES)
    assert imported == list(runtime_probe.RUNTIME_MODULES)
    assert result["providers"] == ["anthropic", "openai_compat"]
    assert result["paperFallback"] == "pypdf"
    assert result["skillCreator"] is True
    assert len(result["bundledMcpPresets"]) == 16
    assert "playwright" in result["bundledMcpPresets"]
    assert result["bundledSkills"] == [
        "frontend-design",
        "mcp-builder",
        "review-agent",
        "security-best-practices",
        "security-ownership-map",
        "security-threat-model",
        "skill-creator",
        "webapp-testing",
    ]
