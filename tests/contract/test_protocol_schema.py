import json
from pathlib import Path

from app_server.connection import ConnectionState
from app_server.dispatcher import Dispatcher
from core.application import DeepCodeApplication


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "protocol" / "app-server.schema.json"
GENERATED_TYPES = ROOT / "desktop" / "src" / "generated" / "app-server.ts"


def test_schema_covers_every_server_method(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    schema_methods = set(schema["$defs"]["MethodParams"]["properties"])
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    dispatcher = Dispatcher(application, ConnectionState(application.broker))
    assert schema_methods == set(dispatcher.methods)


def test_typescript_contract_is_generated_from_the_canonical_schema() -> None:
    generated = GENERATED_TYPES.read_text(encoding="utf-8")
    assert generated.startswith(
        "/* AUTO-GENERATED from protocol/app-server.schema.json. DO NOT EDIT. */"
    )
    assert "export interface MethodParams" in generated
    assert '"event/replay": EventReplayParams' in generated


def test_turn_contract_exposes_nullable_execution_permission_snapshot() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    definitions = schema["$defs"]

    assert definitions["ExecutionPermissionMode"]["enum"] == [
        "default",
        "plan",
        "full_auto",
    ]
    assert definitions["Turn"]["properties"]["executionPermissionMode"] == {
        "oneOf": [
            {"$ref": "#/$defs/ExecutionPermissionMode"},
            {"type": "null"},
        ]
    }
    assert "executionPermissionMode" not in definitions["Turn"]["required"]

    generated = GENERATED_TYPES.read_text(encoding="utf-8")
    assert (
        'export type ExecutionPermissionMode = "default" | "plan" | "full_auto";'
        in generated
    )
    assert "executionPermissionMode?: ExecutionPermissionMode | null;" in generated


def test_automation_transport_contract_exposes_p0_run_identity() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    definitions = schema["$defs"]

    assert definitions["AutomationStatus"]["enum"] == [
        "enabled",
        "paused",
        "retired",
    ]
    assert definitions["AutomationActivationStatus"] == {
        "type": "string",
        "description": (
            "Controls interval scheduling. Manual Automations are always enabled."
        ),
        "enum": ["enabled", "paused"],
    }
    assert definitions["AutomationRunStatus"]["enum"] == [
        "queued",
        "running",
        "waiting",
        "blocked",
        "completed",
        "failed",
        "interrupted",
        "skipped",
    ]

    automation = definitions["Automation"]
    assert automation["properties"]["currentRevisionId"]["pattern"] == "^arev_"
    assert "currentRevisionId" in automation["required"]

    run = definitions["AutomationRun"]
    assert run["properties"]["revisionId"]["pattern"] == "^arev_"
    assert run["properties"]["occurrenceId"]["pattern"] == "^aocc_"
    assert run["properties"]["goalId"] == {
        "type": ["string", "null"],
        "pattern": "^goal_",
    }
    assert {"revisionId", "occurrenceId", "goalId"} <= set(run["required"])

    run_params = definitions["AutomationRunParams"]
    assert run_params["additionalProperties"] is False
    assert run_params["required"] == ["automationId"]
    assert run_params["properties"]["requestId"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 255,
    }
    assert definitions["MethodParams"]["properties"]["automation/run"] == {
        "$ref": "#/$defs/AutomationRunParams"
    }
    assert definitions["AutomationUpdateParams"]["properties"]["status"] == {
        "$ref": "#/$defs/AutomationActivationStatus"
    }
    assert definitions["AutomationUpdateParams"]["minProperties"] == 2
    assert definitions["AutomationCreateParams"]["properties"]["enabled"] == {
        "type": "boolean",
        "description": (
            "Controls interval scheduling. Manual Automations must omit this "
            "field or set it to true."
        ),
    }

    list_params = definitions["AutomationListParams"]
    assert list_params["properties"]["limit"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 500,
    }
    assert list_params["properties"]["offset"] == {
        "type": "integer",
        "minimum": 0,
    }
    assert definitions["MethodParams"]["properties"]["automation/list"] == {
        "$ref": "#/$defs/AutomationListParams"
    }
    run_list_params = definitions["AutomationRunsParams"]
    assert run_list_params["properties"]["offset"] == {
        "type": "integer",
        "minimum": 0,
    }

    method_results = definitions["MethodResults"]["properties"]
    for method in ("automation/list", "automation/runs"):
        result = method_results[method]
        assert result["properties"]["hasMore"] == {"type": "boolean"}
        assert result["properties"]["nextOffset"] == {
            "type": ["integer", "null"],
            "minimum": 0,
        }
        assert {"hasMore", "nextOffset"} <= set(result["required"])

    generated = GENERATED_TYPES.read_text(encoding="utf-8")
    assert '"automation/run": AutomationRunParams' in generated
    assert (
        'export type AutomationStatus = "enabled" | "paused" | "retired"' in generated
    )
    assert 'export type AutomationActivationStatus = "enabled" | "paused"' in generated
    assert "status?: AutomationActivationStatus;" in generated
    assert "currentRevisionId: string;" in generated
    assert "revisionId: string;" in generated
    assert "occurrenceId: string;" in generated
    assert "goalId: string | null;" in generated
    assert "export interface AutomationListParams" in generated
    assert "offset?: number;" in generated
    assert "hasMore: boolean;" in generated
    assert "nextOffset: number | null;" in generated
