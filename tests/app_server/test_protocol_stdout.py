from __future__ import annotations

import io
import sys

import app_server.__main__ as entrypoint
from app_server.__main__ import isolate_protocol_streams


def test_protocol_stdout_is_isolated_from_legacy_prints(monkeypatch) -> None:
    protocol_bytes = io.BytesIO()
    log_bytes = io.BytesIO()
    stdout = io.TextIOWrapper(protocol_bytes, encoding="utf-8")
    stderr = io.TextIOWrapper(log_bytes, encoding="utf-8")
    stdin = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    source, sink = isolate_protocol_streams()
    print("legacy workflow diagnostic")
    sink.write(b'{"jsonrpc":"2.0"}\n')
    sink.flush()
    stderr.flush()

    assert source is stdin.buffer
    assert protocol_bytes.getvalue() == b'{"jsonrpc":"2.0"}\n'
    assert b"legacy workflow diagnostic" in log_bytes.getvalue()


def test_app_server_entrypoint_opts_into_resident_scheduler(monkeypatch) -> None:
    captured: dict[str, object] = {}
    application = object()

    class _Application:
        @classmethod
        def open(cls, database, **kwargs):
            captured["database"] = database
            captured.update(kwargs)
            return application

    class _Server:
        def __init__(self, received_application) -> None:
            assert received_application is application

        def serve(self, source, sink) -> int:
            assert isinstance(source, io.BytesIO)
            assert isinstance(sink, io.BytesIO)
            return 17

    monkeypatch.setattr(entrypoint, "DeepCodeApplication", _Application)
    monkeypatch.setattr(entrypoint, "AppServer", _Server)
    monkeypatch.setattr(
        entrypoint,
        "isolate_protocol_streams",
        lambda: (io.BytesIO(), io.BytesIO()),
    )

    assert entrypoint.main([]) == 17
    assert captured["host_surface"] == "app_server"
    assert captured["run_automation_scheduler"] is True
