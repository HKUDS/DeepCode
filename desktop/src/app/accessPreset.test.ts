import { describe, expect, it } from "vitest";

import type {
  ExecutionSecurityProfile,
  SettingsSnapshot,
  Turn,
} from "../generated/app-server";
import {
  ACCESS_PRESET_OPTIONS,
  hasLegacyPermissionDefault,
  settingsDefaultAccessLabel,
  settingsProductAccessPreset,
  turnExecutionAccessLabel,
  turnExecutionAccessState,
} from "./accessPreset";

function profile(
  input: Partial<ExecutionSecurityProfile> = {},
): ExecutionSecurityProfile {
  return {
    accessPreset: "ask",
    permissionMode: "default",
    commandSandbox: true,
    filesystemScope: "workspace",
    approvalPolicy: "on_request",
    permissionRules: [],
    ...input,
  };
}

function settings(
  resolvedDefaultSecurityProfile: ExecutionSecurityProfile,
  security: SettingsSnapshot["security"] = {},
): SettingsSnapshot {
  return {
    configPath: "/tmp/deepcode_config.json",
  configRevision: "rev-test-1",
    agents: {},
    security,
    permissionModeExplicit: false,
    userAccessPreset: null,
    projectAccessPreset: null,
    resolvedDefaultSecurityProfile,
    resolvedDefaultSecuritySource: "built_in",
    providers: [],
    models: [],
  };
}

function turn(input: Partial<Turn> = {}): Turn {
  return {
    id: "turn-1",
    threadId: "thread-1",
    ordinal: 1,
    prompt: "Inspect the repository",
    status: "running",
    stopReason: null,
    errorCode: null,
    errorMessage: null,
    startedAt: "2026-07-16T00:00:00Z",
    completedAt: null,
    ...input,
  };
}

describe("access preset presentation", () => {
  it("keeps one typed product vocabulary", () => {
    expect(ACCESS_PRESET_OPTIONS).toEqual([
      { value: "ask", label: "Ask" },
      { value: "read_only", label: "Read only" },
      { value: "full_access", label: "Full access" },
    ]);
  });

  it("uses the authoritative resolved default instead of legacy settings", () => {
    const value = settings(
      profile({
        accessPreset: "read_only",
        permissionMode: "plan",
      }),
      { accessPreset: "full_access", permissionMode: "full_auto" },
    );

    expect(settingsProductAccessPreset(value)).toBe("read_only");
    expect(hasLegacyPermissionDefault(value)).toBe(false);
    expect(settingsDefaultAccessLabel(value)).toBe("Read only");
  });

  it("keeps resolved legacy auto distinct from canonical Full access", () => {
    const value = settings(
      profile({ accessPreset: null, permissionMode: "full_auto" }),
    );

    expect(settingsProductAccessPreset(value)).toBeNull();
    expect(hasLegacyPermissionDefault(value)).toBe(true);
    expect(settingsDefaultAccessLabel(value)).toBe(
      "Legacy auto (sandboxed)",
    );
  });

  it("reports when a resolved legacy profile has disabled its sandbox", () => {
    const value = settings(
      profile({
        accessPreset: null,
        permissionMode: "full_auto",
        commandSandbox: false,
        filesystemScope: "unrestricted",
        approvalPolicy: "never",
      }),
    );

    expect(settingsDefaultAccessLabel(value)).toBe("Legacy auto (sandbox off)");
  });

  it("reads a Turn's frozen profile without consulting current Settings", () => {
    const value = turn({
      executionSecurityProfile: profile({
        accessPreset: "full_access",
        permissionMode: "full_auto",
        commandSandbox: false,
        filesystemScope: "unrestricted",
        approvalPolicy: "never",
      }),
    });

    expect(turnExecutionAccessLabel(value)).toBe("Full access");
    expect(turnExecutionAccessState(value)).toBe("full_access");
  });

  it("keeps old Turn fallbacks visibly legacy or unknown", () => {
    expect(
      turnExecutionAccessLabel(
        turn({ executionPermissionMode: "full_auto" }),
      ),
    ).toBe("Legacy auto");
    expect(
      turnExecutionAccessState(
        turn({ executionPermissionMode: "full_auto" }),
      ),
    ).toBe("legacy");
    expect(turnExecutionAccessLabel(turn())).toBe("Access unavailable");
    expect(turnExecutionAccessState(turn())).toBe("unknown");
  });
});
