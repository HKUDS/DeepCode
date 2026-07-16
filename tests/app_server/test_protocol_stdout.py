from __future__ import annotations

import io
import sys

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
