from __future__ import annotations

import json
import os
import select
import threading
import time
from pathlib import Path
from typing import Any

from app_server.server import AppServer
from core.application import DeepCodeApplication
from core.application.workflow_adapter import ArtifactSpec, WorkflowOutcome
from core.domain import ThreadMode, TrustState


class InteractiveRunner:
    async def run(self, request, callbacks) -> WorkflowOutcome:
        await callbacks.progress("planning", 50, 100, "Plan ready", {})
        response = await callbacks.interact(
            {"type": "plan_review", "message": "Approve plan"}
        )
        output = request.workspace / "generated.md"
        output.write_text(f"decision={response['decision']}\n", encoding="utf-8")
        await callbacks.progress("testing", 100, 100, "Tests passed", {})
        return WorkflowOutcome(
            status="completed",
            summary="Verified workflow",
            result={"status": "completed", "testsPassed": True},
            artifacts=(
                ArtifactSpec("report", "generated.md", "text/markdown", output),
            ),
        )


def _request(request_id: int, method: str, params: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        ).encode()
        + b"\n"
    )


def _read(reader) -> dict[str, Any]:
    ready, _, _ = select.select([reader], [], [], 5.0)
    assert ready, "timed out waiting for App Server output"
    line = reader.readline()
    assert line
    return json.loads(line)


def _until(reader, predicate) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        message = _read(reader)
        if predicate(message):
            return message
    raise AssertionError("matching protocol message was not received")


def test_p5_workflow_interaction_and_artifact_protocol(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3", workflow_runner=InteractiveRunner()
    )
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = application.threads.start(
        project.id, title="P5 workflow", mode=ThreadMode.PAPER
    )
    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    source = os.fdopen(input_read, "rb", buffering=0)
    writer = os.fdopen(input_write, "wb", buffering=0)
    reader = os.fdopen(output_read, "rb", buffering=0)
    sink = os.fdopen(output_write, "wb", buffering=0)
    server = threading.Thread(
        target=AppServer(application).serve, args=(source, sink), daemon=True
    )
    server.start()
    try:
        writer.write(
            _request(
                1,
                "initialize",
                {
                    "protocolVersion": "1.0",
                    "clientInfo": {"name": "p5-test", "version": "1.0"},
                },
            )
        )
        initialized = _until(reader, lambda message: message.get("id") == 1)
        assert "workflow/start" in initialized["result"]["capabilities"]["methods"]

        writer.write(
            _request(
                2,
                "workflow/start",
                {
                    "threadId": thread.id,
                    "kind": "paper2code",
                    "sourceType": "requirement",
                    "source": "Build and verify",
                    "options": {"planReview": True},
                },
            )
        )
        started = _until(reader, lambda message: message.get("id") == 2)["result"]
        run_id = started["workflow"]["id"]
        interaction_event = _until(
            reader,
            lambda message: message.get("method") == "workflow.interaction_requested",
        )
        interaction = interaction_event["params"]["payload"]["workflow"]["checkpoint"][
            "interaction"
        ]

        writer.write(
            _request(
                3,
                "workflow/respond",
                {
                    "workflowRunId": run_id,
                    "interactionId": interaction["id"],
                    "response": {"decision": "approve"},
                },
            )
        )
        assert (
            _until(reader, lambda message: message.get("id") == 3)["result"][
                "workflow"
            ]["status"]
            == "running"
        )
        _until(
            reader,
            lambda message: message.get("method") == "workflow.completed",
        )

        writer.write(_request(4, "artifact/list", {"threadId": thread.id}))
        artifacts = _until(reader, lambda message: message.get("id") == 4)["result"][
            "artifacts"
        ]
        assert [artifact["name"] for artifact in artifacts] == ["generated.md"]

        writer.write(_request(5, "artifact/read", {"artifactId": artifacts[0]["id"]}))
        artifact = _until(reader, lambda message: message.get("id") == 5)["result"]
        assert artifact["content"] == "decision=approve\n"
        assert artifact["directory"] is False

        writer.write(_request(6, "shutdown", {}))
        assert _until(reader, lambda message: message.get("id") == 6)["result"][
            "accepted"
        ]
        server.join(timeout=3)
        assert not server.is_alive()
    finally:
        writer.close()
        source.close()
        sink.close()
        reader.close()
