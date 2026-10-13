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
import { initI18n } from "./i18n";

const i18n = initI18n();
const t = i18n.getFixedT("en");

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
    ignoredProjectSecurity: [],
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
    expect(settingsDefaultAccessLabel(value, t)).toBe("Read only");
  });

  it("keeps resolved legacy auto distinct from canonical Full access", () => {
    const value = settings(
      profile({ accessPreset: null, permissionMode: "full_auto" }),
    );

    expect(settingsProductAccessPreset(value)).toBeNull();
    expect(hasLegacyPermissionDefault(value)).toBe(true);
    expect(settingsDefaultAccessLabel(value, t)).toBe(
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

    expect(settingsDefaultAccessLabel(value, t)).toBe("Legacy auto (sandbox off)");
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

    expect(turnExecutionAccessLabel(value, t)).toBe("Full access");
    expect(turnExecutionAccessState(value)).toBe("full_access");
  });

  it("keeps old Turn fallbacks visibly legacy or unknown", () => {
    expect(
      turnExecutionAccessLabel(
        turn({ executionPermissionMode: "full_auto" }),
        t,
      ),
    ).toBe("Legacy auto");
    expect(
      turnExecutionAccessState(
        turn({ executionPermissionMode: "full_auto" }),
      ),
    ).toBe("legacy");
    expect(turnExecutionAccessLabel(turn(), t)).toBe("Access unavailable");
    expect(turnExecutionAccessState(turn())).toBe("unknown");
  });

  it("names legacy and unknown access in the interface language", () => {
    const zh = i18n.getFixedT("zh-CN");
    const legacy = settings(
      profile({ accessPreset: null, permissionMode: "full_auto" }),
    );

    expect(settingsDefaultAccessLabel(legacy, zh)).toBe("旧版自动（沙箱开启）");
    expect(settingsDefaultAccessLabel(null, zh)).toBe("加载中…");
    expect(turnExecutionAccessLabel(turn(), zh)).toBe("权限信息不可用");
  });
});
