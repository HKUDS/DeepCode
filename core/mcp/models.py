"""Typed configuration and immutable runtime contracts for MCP clients."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_URL_PARAMETER_NAME = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")
_SENSITIVE_NAME = re.compile(
    r"(?:^|[^a-z0-9])(?:authorization|cookie|(?:access|refresh|bearer)[-_]?token|"
    r"api[-_]?key|password|passwd|secret|private[-_]?key)(?:$|[^a-z0-9])",
    re.IGNORECASE,
)


class McpConfigurationError(ValueError):
    """A server definition cannot be safely resolved."""


class McpStartupError(RuntimeError):
    """One or more required MCP servers could not initialize."""


class McpApprovalMode(StrEnum):
    """Additional MCP-specific restriction layered over global permissions."""

    AUTO = "auto"
    PROMPT = "prompt"
    WRITES = "writes"
    APPROVE = "approve"


class McpServerSource(StrEnum):
    USER = "user"
    PROJECT = "project"
    PLUGIN = "plugin"


class _ConfigModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class McpCredentialRef(_ConfigModel):
    """Reference to a user-owned DeepCode connection credential.

    Secrets are resolved immediately before a connection starts and never
    become part of an inventory, diagnostic, or serialized runtime plan.
    """

    credential_ref: str = Field(
        validation_alias=AliasChoices("credentialRef", "credential_ref")
    )

    @field_validator("credential_ref")
    @classmethod
    def _valid_reference(cls, value: str) -> str:
        clean = value.strip()
        if not clean.startswith("provider:") or len(clean) <= len("provider:"):
            raise ValueError("credentialRef must use provider:<connection-id>")
        connection_id = clean.split(":", 1)[1]
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", connection_id):
            raise ValueError("credentialRef contains an invalid connection ID")
        return clean

    @property
    def connection_id(self) -> str:
        return self.credential_ref.split(":", 1)[1]


class McpToolPolicy(_ConfigModel):
    approval_mode: McpApprovalMode = Field(
        default=McpApprovalMode.AUTO,
        validation_alias=AliasChoices(
            "approvalMode",
            "approval_mode",
        ),
    )


class McpServerDefinition(_ConfigModel):
    """One generic MCP client server definition.

    Transport fields are kept in one discriminated shape for familiar JSON,
    while validation enforces the same mutually-exclusive contract as a typed
    union.  No transport is inferred from URL suffixes or the presence of a
    command.
    """

    type: Literal["stdio", "sse", "streamableHttp"]
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    env_vars: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("envVars", "env_vars"),
    )
    required_env_vars: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("requiredEnvVars", "required_env_vars"),
    )
    credential_env: dict[str, McpCredentialRef] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("credentialEnv", "credential_env"),
    )

    url: str | None = None
    auth: Literal["oauth"] | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    env_http_headers: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("envHttpHeaders", "env_http_headers"),
    )
    env_url_params: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("envUrlParams", "env_url_params"),
    )
    bearer_token_env_var: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "bearerTokenEnvVar",
            "bearer_token_env_var",
        ),
    )
    bearer_token_credential: McpCredentialRef | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "bearerTokenCredential",
            "bearer_token_credential",
        ),
    )

    enabled: bool = True
    required: bool = False
    defer_loading: bool = Field(
        default=False,
        validation_alias=AliasChoices("deferLoading", "defer_loading"),
    )
    supports_parallel_tool_calls: bool = False
    startup_timeout_seconds: Annotated[float, Field(gt=0, le=300)] = Field(
        default=10.0,
        validation_alias=AliasChoices(
            "startupTimeoutSeconds",
            "startup_timeout_seconds",
        ),
    )
    tool_timeout_seconds: Annotated[float, Field(gt=0, le=86_400)] = Field(
        default=60.0,
        validation_alias=AliasChoices(
            "toolTimeoutSeconds",
            "tool_timeout_seconds",
            "toolTimeout",
        ),
    )
    enabled_tools: tuple[str, ...] | None = Field(
        default=None,
        validation_alias=AliasChoices("enabledTools", "enabled_tools"),
    )
    disabled_tools: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("disabledTools", "disabled_tools"),
    )
    approval_mode: McpApprovalMode = Field(
        default=McpApprovalMode.AUTO,
        validation_alias=AliasChoices(
            "approvalMode",
            "approval_mode",
            "defaultToolsApprovalMode",
            "default_tools_approval_mode",
        ),
    )
    tools: dict[str, McpToolPolicy] = Field(default_factory=dict)
    description: str | None = None

    @field_validator("command", "cwd", "url", "bearer_token_env_var")
    @classmethod
    def _clean_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        return clean or None

    @field_validator("env_vars", "required_env_vars")
    @classmethod
    def _valid_env_vars(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_env_names(values, "environment variables")

    @field_validator("env_http_headers")
    @classmethod
    def _valid_header_env_vars(cls, values: dict[str, str]) -> dict[str, str]:
        _validate_header_names(values, "envHttpHeaders")
        for env_name in values.values():
            if not _ENV_NAME.fullmatch(env_name):
                raise ValueError(
                    f"envHttpHeaders value is not an environment variable: {env_name}"
                )
        return values

    @field_validator("env_url_params")
    @classmethod
    def _valid_url_parameter_env_vars(cls, values: dict[str, str]) -> dict[str, str]:
        for parameter, env_name in values.items():
            if not _URL_PARAMETER_NAME.fullmatch(parameter):
                raise ValueError(
                    f"envUrlParams contains an invalid URL parameter: {parameter!r}"
                )
            if not _ENV_NAME.fullmatch(env_name):
                raise ValueError(
                    f"envUrlParams value is not an environment variable: {env_name}"
                )
        return values

    @field_validator("headers")
    @classmethod
    def _valid_literal_headers(cls, values: dict[str, str]) -> dict[str, str]:
        _validate_header_names(values, "headers")
        invalid = next(
            (name for name, value in values.items() if "\r" in value or "\n" in value),
            None,
        )
        if invalid is not None:
            raise ValueError(f"headers.{invalid} contains a line break")
        return values

    @field_validator("credential_env")
    @classmethod
    def _valid_credential_env(
        cls, values: dict[str, McpCredentialRef]
    ) -> dict[str, McpCredentialRef]:
        invalid = next((name for name in values if not _ENV_NAME.fullmatch(name)), None)
        if invalid is not None:
            raise ValueError(
                f"credentialEnv key is not an environment variable: {invalid}"
            )
        return values

    @field_validator("enabled_tools", "disabled_tools")
    @classmethod
    def _valid_tool_names(
        cls, values: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        if values is None:
            return None
        clean = tuple(dict.fromkeys(item.strip() for item in values if item.strip()))
        if len(clean) != len(values):
            raise ValueError("tool filters must contain unique non-empty names")
        return clean

    @model_validator(mode="after")
    def _transport_contract(self) -> McpServerDefinition:
        if self.required and self.defer_loading:
            raise ValueError("required MCP servers cannot defer loading")

        if self.bearer_token_env_var is not None and not _ENV_NAME.fullmatch(
            self.bearer_token_env_var
        ):
            raise ValueError("bearerTokenEnvVar must name an environment variable")

        if self.type == "stdio":
            if not self.command:
                raise ValueError("stdio MCP servers require command")
            forbidden = (
                self.url,
                self.auth,
                self.headers,
                self.env_http_headers,
                self.env_url_params,
                self.bearer_token_env_var,
                self.bearer_token_credential,
            )
            if any(forbidden):
                raise ValueError("stdio MCP servers cannot define HTTP fields")
        else:
            if not self.url:
                raise ValueError(f"{self.type} MCP servers require url")
            parsed = urlparse(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("HTTP MCP server url must use http or https")
            if (
                self.command
                or self.args
                or self.cwd
                or self.env
                or self.env_vars
                or self.required_env_vars
            ):
                raise ValueError("HTTP MCP servers cannot define stdio process fields")
            if self.credential_env:
                raise ValueError("HTTP MCP servers cannot define credentialEnv")
            if self.bearer_token_env_var and self.bearer_token_credential:
                raise ValueError(
                    "configure only one of bearerTokenEnvVar or bearerTokenCredential"
                )
            if self.auth == "oauth" and (
                self.bearer_token_env_var or self.bearer_token_credential
            ):
                raise ValueError(
                    "OAuth cannot be combined with a configured bearer token"
                )
        return self

    def policy_for(self, tool_name: str) -> McpApprovalMode:
        policy = self.tools.get(tool_name)
        return policy.approval_mode if policy is not None else self.approval_mode

    def exposes(self, tool_name: str) -> bool:
        allowed = self.enabled_tools
        if allowed is not None and "*" not in allowed and tool_name not in allowed:
            return False
        return tool_name not in self.disabled_tools


class McpServerPolicyOverlay(_ConfigModel):
    """User-owned policy and credential bindings for a Plugin MCP server.

    Executable provenance stays in the Plugin's signed/linked package. The
    overlay deliberately has no transport, command, URL, argument, cwd,
    literal environment, or literal header fields.
    """

    enabled: bool | None = None
    required: bool | None = None
    supports_parallel_tool_calls: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "supportsParallelToolCalls",
            "supports_parallel_tool_calls",
        ),
    )
    startup_timeout_seconds: Annotated[float, Field(gt=0, le=300)] | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "startupTimeoutSeconds",
            "startup_timeout_seconds",
        ),
    )
    tool_timeout_seconds: Annotated[float, Field(gt=0, le=86_400)] | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "toolTimeoutSeconds",
            "tool_timeout_seconds",
        ),
    )
    enabled_tools: tuple[str, ...] | None = Field(
        default=None,
        validation_alias=AliasChoices("enabledTools", "enabled_tools"),
    )
    disabled_tools: tuple[str, ...] | None = Field(
        default=None,
        validation_alias=AliasChoices("disabledTools", "disabled_tools"),
    )
    approval_mode: McpApprovalMode | None = Field(
        default=None,
        validation_alias=AliasChoices("approvalMode", "approval_mode"),
    )
    tools: dict[str, McpToolPolicy] | None = None
    env_vars: tuple[str, ...] | None = Field(
        default=None,
        validation_alias=AliasChoices("envVars", "env_vars"),
    )
    credential_env: dict[str, McpCredentialRef] | None = Field(
        default=None,
        validation_alias=AliasChoices("credentialEnv", "credential_env"),
    )
    env_http_headers: dict[str, str] | None = Field(
        default=None,
        validation_alias=AliasChoices("envHttpHeaders", "env_http_headers"),
    )
    bearer_token_env_var: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "bearerTokenEnvVar",
            "bearer_token_env_var",
        ),
    )
    bearer_token_credential: McpCredentialRef | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "bearerTokenCredential",
            "bearer_token_credential",
        ),
    )

    @field_validator("env_vars")
    @classmethod
    def _valid_forwarded_env(
        cls, values: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        return None if values is None else _unique_env_names(values, "envVars")

    @field_validator("credential_env")
    @classmethod
    def _valid_bound_env(
        cls, values: dict[str, McpCredentialRef] | None
    ) -> dict[str, McpCredentialRef] | None:
        if values is None:
            return None
        invalid = next((name for name in values if not _ENV_NAME.fullmatch(name)), None)
        if invalid is not None:
            raise ValueError(
                f"credentialEnv key is not an environment variable: {invalid}"
            )
        return values

    @field_validator("env_http_headers")
    @classmethod
    def _valid_forwarded_headers(
        cls, values: dict[str, str] | None
    ) -> dict[str, str] | None:
        if values is None:
            return None
        _validate_header_names(values, "envHttpHeaders")
        for env_name in values.values():
            if not _ENV_NAME.fullmatch(env_name):
                raise ValueError(
                    "envHttpHeaders must map header names to environment names"
                )
        return values

    @field_validator("enabled_tools", "disabled_tools")
    @classmethod
    def _valid_filters(cls, values: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if values is None:
            return None
        clean = tuple(dict.fromkeys(item.strip() for item in values if item.strip()))
        if len(clean) != len(values):
            raise ValueError("tool filters must contain unique non-empty names")
        return clean


def _unique_env_names(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    clean = tuple(dict.fromkeys(item.strip() for item in values if item.strip()))
    if len(clean) != len(values):
        raise ValueError(f"{field} must contain unique non-empty names")
    invalid = next((name for name in clean if not _ENV_NAME.fullmatch(name)), None)
    if invalid is not None:
        raise ValueError(f"{field} contains an invalid environment name: {invalid}")
    return clean


def _validate_header_names(values: dict[str, Any], field: str) -> None:
    seen: set[str] = set()
    for name in values:
        if not _HTTP_HEADER_NAME.fullmatch(name):
            raise ValueError(f"{field} contains an invalid HTTP header name: {name!r}")
        folded = name.casefold()
        if folded in seen:
            raise ValueError(f"{field} contains a duplicate header name: {name!r}")
        seen.add(folded)


def validate_server_name(value: str) -> str:
    clean = value.strip()
    if not _SERVER_NAME.fullmatch(clean):
        raise McpConfigurationError(
            "MCP server name must be 1-80 letters, numbers, dots, dashes, or underscores"
        )
    return clean


def validate_no_literal_secrets(definition: McpServerDefinition) -> None:
    """Enforce DeepCode's native-config secret storage boundary.

    This is intentionally not part of the transport model. Agent Plugins has
    its own portable schema and authority boundary, while user/project config
    can reference ambient or connection-owned credentials without persisting
    them as literals.
    """

    sensitive_env = next(
        (name for name in definition.env if _SENSITIVE_NAME.search(name)), None
    )
    if sensitive_env is not None:
        raise McpConfigurationError(
            f"env.{sensitive_env} looks sensitive; use envVars or credentialEnv"
        )
    sensitive_header = next(
        (name for name in definition.headers if _SENSITIVE_NAME.search(name)), None
    )
    if sensitive_header is not None:
        raise McpConfigurationError(
            f"headers.{sensitive_header} looks sensitive; use envHttpHeaders "
            "or bearerTokenCredential"
        )


@dataclass(frozen=True, slots=True)
class ResolvedMcpServer:
    """Validated definition with its authority and path provenance intact."""

    server_id: str
    name: str
    source: McpServerSource
    definition: McpServerDefinition
    config_dir: Path
    workspace: Path
    plugin_id: str | None = None
    plugin_root: Path | None = None
    plugin_data: Path | None = None
    policy_key: str | None = None
    plugin_server_name: str | None = None


@dataclass(frozen=True, slots=True)
class McpRuntimePlan:
    workspace: Path
    servers: tuple[ResolvedMcpServer, ...]
    revision: str
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class McpToolIdentity:
    server_id: str
    server_name: str
    source: McpServerSource
    raw_name: str


@dataclass(frozen=True, slots=True)
class McpToolAnnotations:
    read_only: bool = False
    destructive: bool | None = None
    idempotent: bool | None = None
    open_world: bool | None = None

    @classmethod
    def from_sdk(cls, value: Any) -> McpToolAnnotations:
        if value is None:
            return cls()
        return cls(
            read_only=getattr(value, "readOnlyHint", None) is True,
            destructive=getattr(value, "destructiveHint", None),
            idempotent=getattr(value, "idempotentHint", None),
            open_world=getattr(value, "openWorldHint", None),
        )


__all__ = [
    "McpApprovalMode",
    "McpConfigurationError",
    "McpCredentialRef",
    "McpRuntimePlan",
    "McpServerDefinition",
    "McpServerPolicyOverlay",
    "McpServerSource",
    "McpStartupError",
    "McpToolAnnotations",
    "McpToolIdentity",
    "McpToolPolicy",
    "ResolvedMcpServer",
    "validate_no_literal_secrets",
    "validate_server_name",
]
