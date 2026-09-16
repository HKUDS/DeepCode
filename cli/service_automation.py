"""Monitor a manual Automation Run owned by the shared service."""

from __future__ import annotations

import time

from app_server.blocking_client import BlockingServiceClient
from app_server.service_state import ServiceFiles
from cli.automation_foreground import (
    ForegroundApprovalRequiredError,
    _foreground_settled,
)
from cli.exec_cli import _approval_decider
from cli.rpc_models import from_view
from core.domain.approval import Approval
from core.domain.common import new_id
from core.persistence.database import default_database_path


def run_automation_service(automation_id, *, request_id, interactive):
    rpc = BlockingServiceClient(
        ServiceFiles(default_database_path()), surface="headless"
    )
    try:
        result = rpc.call(
            "automation/run",
            {
                "automationId": automation_id,
                "requestId": request_id or new_id("manual"),
            },
        )
        run = result["run"]
        thread_id, run_id = run["threadId"], run["id"]
        sequence = rpc.call("event/replay", {"threadId": thread_id, "limit": 1})[
            "headSequence"
        ]
        # The Run may settle between admission and reading the event head.
        # Read its current projection once before relying on incremental replay.
        offset = 0
        while True:
            page = rpc.call(
                "automation/runs",
                {"automationId": automation_id, "offset": offset, "limit": 100},
            )
            current = next(
                (candidate for candidate in page["runs"] if candidate["id"] == run_id),
                None,
            )
            if current is not None:
                run = current
                break
            if not page["hasMore"]:
                raise RuntimeError("The admitted Automation Run is no longer available")
            offset = page["nextOffset"]
        while True:
            # Listing reconciles crash-interrupted Goal/Run projections in the
            # existing service; the CLI never performs that recovery itself.
            rpc.call("automation/runs", {"automationId": automation_id, "limit": 1})
            through = None
            while True:
                page = rpc.call(
                    "event/replay",
                    {
                        "threadId": thread_id,
                        "after": sequence,
                        **({"through": through} if through is not None else {}),
                    },
                )
                through = page["headSequence"] if through is None else through
                for event in page["events"]:
                    sequence = event["sequence"]
                    candidate = event["payload"].get("run")
                    if (
                        event["type"] == "automation.updated"
                        and candidate
                        and candidate["id"] == run_id
                    ):
                        run = candidate
                if not page["hasMore"]:
                    break
            if _foreground_settled(run):
                turn = (
                    rpc.call("turn/read", {"turnId": run["turnId"]})["turn"]
                    if run.get("turnId")
                    else None
                )
                return {"run": run, "turn": turn}
            active = rpc.call(
                "turn/list", {"threadId": thread_id, "state": "active", "limit": 1}
            )["turns"]
            if active and active[0].get("goalId") == run.get("goalId"):
                snapshot = rpc.call("turn/read", {"turnId": active[0]["id"]})
                for value in snapshot["approvals"]:
                    if value["status"] != "pending":
                        continue
                    approval = from_view(Approval, value)
                    if not interactive:
                        raise ForegroundApprovalRequiredError(
                            "Automation requires human approval in an interactive DeepCode client",
                            details={
                                "automationId": automation_id,
                                "runId": run_id,
                                "threadId": thread_id,
                                "turnId": approval.turn_id,
                                "approvalId": approval.id,
                                "cleanup": "service_keeps_waiting",
                            },
                        )
                    decision = _approval_decider(approval)
                    try:
                        rpc.call(
                            "approval/respond",
                            {"approvalId": approval.id, "decision": decision.value},
                        )
                    except Exception as exc:
                        if getattr(exc, "code", None) != "APPROVAL_ALREADY_RESOLVED":
                            raise
            time.sleep(0.25)
    finally:
        rpc.close()
