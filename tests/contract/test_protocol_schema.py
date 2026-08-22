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


def test_skill_contract_exposes_provider_origin_and_change_notification() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    definitions = schema["$defs"]
    skill = definitions["SkillInfo"]

    assert skill["properties"]["originKind"]["enum"] == [
        "local",
        "bundled",
        "provider",
    ]
    assert skill["properties"]["providerKind"]["enum"] == [
        "local",
        "executor",
        "orchestrator",
        "custom",
    ]
    assert {
        "originKind",
        "originLabel",
        "providerKind",
        "providerId",
        "packageId",
        "configurableScopes",
    } <= set(skill["required"])
    assert definitions["SkillCatalogResult"]["properties"]["authoringSkillId"] == {
        "type": ["string", "null"],
        "pattern": "^sk_[0-9a-f]{24}$",
    }
    notifications = definitions["Notifications"]
    assert notifications["properties"]["skills.changed"]["required"] == ["projectId"]
    assert "skills.changed" in notifications["required"]

    generated = GENERATED_TYPES.read_text(encoding="utf-8")
    assert 'originKind: "local" | "bundled" | "provider";' in generated
    assert '"skills.changed": {' in generated


def test_plugin_contract_exposes_local_lifecycle_without_execution_fields() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    definitions = schema["$defs"]
    plugin = definitions["PluginInfo"]

    assert plugin["properties"]["status"]["enum"] == [
        "active",
        "disabled",
        "invalid",
    ]
    assert {"schema", "components", "diagnostics", "manifestRevision"} <= set(
        plugin["required"]
    )
    assert plugin["properties"]["source"]["const"] == "linked-directory"
    assert definitions["PluginCatalogResult"]["required"] == [
        "plugins",
        "diagnostics",
        "revision",
    ]
    assert not ({"command", "hooks", "mcpServers"} & set(plugin["properties"]))
    methods = definitions["MethodParams"]["properties"]
    assert {
        "plugins/list",
        "plugins/add",
        "plugins/set-enabled",
        "plugins/remove",
    } <= set(methods)
    assert definitions["Notifications"]["properties"]["plugins.changed"] == {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }

    generated = GENERATED_TYPES.read_text(encoding="utf-8")
    assert "export interface PluginInfo" in generated
    assert '"plugins.changed": {};' in generated


def test_mcp_contract_exposes_presets_probe_oauth_and_runtime_state() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    definitions = schema["$defs"]
    methods = definitions["MethodParams"]["properties"]

    assert {
        "mcp/list",
        "mcp/upsert",
        "mcp/remove",
        "mcp/presets",
        "mcp/preset/add",
        "mcp/set-enabled",
        "mcp/probe",
        "mcp/oauth/start",
        "mcp/oauth/cancel",
        "mcp/oauth/logout",
    } <= set(methods)
    assert "mcp/preset/install" not in methods
    server = definitions["McpServerInfo"]
    assert {
        "configurationState",
        "authState",
        "runtimeState",
        "toolCount",
        "resourceCount",
        "promptCount",
    } <= set(server["required"])
    assert definitions["McpPresetInventory"]["required"] == [
        "presets",
        "source",
        "sourceRevision",
    ]
    assert definitions["Notifications"]["properties"]["mcp.changed"] == {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }

    generated = GENERATED_TYPES.read_text(encoding="utf-8")
    assert "export interface McpPresetInfo" in generated
    assert '"mcp/probe": McpIdentityParams;' in generated
    assert '"mcp.changed": {};' in generated


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


def test_protocol_exposes_session_access_and_immutable_security_profile() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    definitions = schema["$defs"]

    assert definitions["ExecutionAccessPreset"]["enum"] == [
        "ask",
        "read_only",
        "full_access",
        "dangerous_skip",
    ]
    assert definitions["Thread"]["properties"]["accessPresetOverride"] == {
        "description": (
            "Session override. Null inherits the effective Settings default."
        ),
        "oneOf": [
            {"$ref": "#/$defs/ExecutionAccessPreset"},
            {"type": "null"},
        ],
    }
    assert "accessPresetOverride" in definitions["Thread"]["required"]
    assert definitions["Turn"]["properties"]["executionSecurityProfile"] == {
        "oneOf": [
            {"$ref": "#/$defs/ExecutionSecurityProfile"},
            {"type": "null"},
        ]
    }
    assert "executionSecurityProfile" not in definitions["Turn"]["required"]

    profile = definitions["ExecutionSecurityProfile"]
    assert profile["additionalProperties"] is False
    assert profile["properties"]["accessPreset"] == {
        "oneOf": [
            {"$ref": "#/$defs/ExecutionAccessPreset"},
            {"type": "null"},
        ]
    }
    assert profile["properties"]["permissionMode"] == {
        "$ref": "#/$defs/ExecutionPermissionMode"
    }
    assert profile["properties"]["commandSandbox"] == {"type": "boolean"}
    assert profile["properties"]["filesystemScope"] == {
        "type": "string",
        "enum": ["workspace", "unrestricted"],
    }
    assert profile["properties"]["approvalPolicy"] == {
        "type": "string",
        "enum": ["on_request", "never"],
    }
    assert definitions["ExecutionPermissionRule"]["required"] == [
        "permission",
        "pattern",
        "action",
    ]
    assert definitions["ExecutionPermissionRule"]["properties"]["action"] == {
        "type": "string",
        "enum": ["allow", "ask", "deny"],
    }
    assert profile["properties"]["permissionRules"] == {
        "type": "array",
        "description": (
            "Ordered, immutable rules evaluated within the preset safety boundary."
        ),
        "items": {"$ref": "#/$defs/ExecutionPermissionRule"},
    }
    assert profile["required"] == [
        "accessPreset",
        "permissionMode",
        "commandSandbox",
        "filesystemScope",
        "approvalPolicy",
        "permissionRules",
    ]
    params = definitions["ThreadPermissionUpdateParams"]
    assert params["required"] == ["threadId", "accessPreset"]
    assert definitions["MethodParams"]["properties"]["thread/permission/update"] == {
        "$ref": "#/$defs/ThreadPermissionUpdateParams"
    }

    generated = GENERATED_TYPES.read_text(encoding="utf-8")
    assert (
        'export type ExecutionAccessPreset = "ask" | "read_only" | '
        '"full_access" | "dangerous_skip";' in generated
    )
    assert "accessPresetOverride: ExecutionAccessPreset | null;" in generated
    assert "executionSecurityProfile?: ExecutionSecurityProfile | null;" in generated
    assert "permissionRules: ExecutionPermissionRule[];" in generated
    assert '"thread/permission/update": ThreadPermissionUpdateParams;' in generated


def test_provider_verification_params_can_probe_a_project_model() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    definitions = schema["$defs"]
    params = definitions["ProviderTestParams"]

    assert params["required"] == ["connectionId"]
    assert params["properties"]["projectId"] == {
        "type": "string",
        "pattern": "^proj_",
    }
    assert params["properties"]["model"] == {
        "type": "string",
        "minLength": 1,
    }


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
