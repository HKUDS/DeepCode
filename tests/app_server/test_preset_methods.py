"""Wire-level agent-preset methods: roster, current selection, locked switch."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from app_server.protocol.codec import encode_message
from app_server.server import AppServer
from core.application.application import DeepCodeApplication


def _request(request_id: int, method: str, params: dict[str, Any]) -> bytes:
    return encode_message(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    )


def _messages(sink: io.BytesIO) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in sink.getvalue().decode("utf-8").splitlines()
        if line.strip()
    ]


def test_preset_roster_selection_and_lock_over_the_wire(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    thread = application.threads.start(project.id, title="Preset wire test")
    # Lock trigger: the conversation "starts" once a message exists.
    started = application.threads.start(project.id, title="Started thread")
    application.session_store.append_message(started.id, "user", "hello")

    source = io.BytesIO(
        _request(
            1,
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "preset-test", "version": "1.0"},
            },
        )
        + _request(2, "preset/list", {"projectId": project.id})
        + _request(
            3,
            "preset/select",
            {"threadId": thread.id, "agentPreset": "code-reader"},
        )
        + _request(4, "preset/current", {"threadId": thread.id})
        + _request(
            5,
            "preset/select",
            {"threadId": started.id, "agentPreset": "code-reader"},
        )
        + _request(6, "shutdown", {})
    )
    sink = io.BytesIO()

    assert AppServer(application).serve(source, sink) == 0
    responses = {
        message["id"]: message for message in _messages(sink) if "id" in message
    }

    roster = {entry["id"]: entry for entry in responses[2]["result"]["presets"]}
    assert "code-reader" in roster and roster["code-reader"]["trust"] == "system"
    assert roster["code-reader"]["tools"] == ["read", "grep", "glob", "skill"]

    assert responses[3]["result"]["agentPreset"] == "code-reader"
    assert responses[4]["result"]["agentPreset"] == "code-reader"

    # The blank-Session lock, surfaced as an error rather than a silent no-op.
    assert responses[5]["error"]["data"]["code"] == "CONFLICT"
    assert "already has messages" in responses[5]["error"]["message"]
