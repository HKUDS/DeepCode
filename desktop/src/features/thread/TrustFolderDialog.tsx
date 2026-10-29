import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  accessPresetLabel,
  executionSecurityLabel,
  productAccessPreset,
} from "../../app/accessPreset";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import type {
  ExecutionAccessPreset,
  Project,
  SettingsSnapshot,
  Thread,
} from "../../generated/app-server";
import type { ClientRuntime } from "../../rpc/contracts";

interface TrustFolderDialogProps {
  runtime: ClientRuntime;
  project: Project;
  thread: Thread | null;
  settings: SettingsSnapshot | null;
  busy: boolean;
  onConfirm(): void;
  onCancel(): void;
}

type HookCount =
  | { state: "loading" }
  | { state: "ready"; count: number }
  | { state: "unknown" };

/**
 * Asks before a folder is trusted, and says what that allows: the agent may
 * run in it with the access that will actually apply (the Session's override,
 * else the resolved default — Full access unless changed), the project's own
 * hooks are approved as they stand and run on every Turn (the service records
 * their digest on trust), and its MCP servers and Skills are loaded.
 */
export function TrustFolderDialog({
  runtime,
  project,
  thread,
  settings,
  busy,
  onConfirm,
  onCancel,
}: TrustFolderDialogProps) {
  const { t } = useTranslation();
  const [hooks, setHooks] = useState<HookCount>({ state: "loading" });

  useEffect(() => {
    let cancelled = false;
    runtime
      .request("hooks/list", { projectId: project.id })
      .then((result) => {
        if (cancelled) return;
        setHooks({
          state: "ready",
          count: result.hooks.filter((hook) => hook.source === "project").length,
        });
      })
      .catch(() => {
        if (!cancelled) setHooks({ state: "unknown" });
      });
    return () => {
      cancelled = true;
    };
  }, [project.id, runtime]);

  const override = thread?.accessPresetOverride ?? null;
  const preset: ExecutionAccessPreset | null =
    override ??
    productAccessPreset(settings?.resolvedDefaultSecurityProfile.accessPreset);
  const accessLabel = override
    ? accessPresetLabel(override, t)
    : settings
      ? executionSecurityLabel(settings.resolvedDefaultSecurityProfile, t)
      : null;

  const accessLine = accessLabel
    ? override
      ? t(
          "trust.access.session",
          "Agent access in this Session: {{access}}.",
          { access: accessLabel },
        )
      : t(
          "trust.access.default",
          "Agent access for new Turns: {{access}} (the current default).",
          { access: accessLabel },
        )
    : t(
        "trust.access.unknown",
        "Turns run with the default access set in Settings.",
      );
  const accessDetail =
    preset === "full_access"
      ? t(
          "trust.access.detail.full_access",
          "Tools run without asking you and outside the workspace sandbox, so they can read, change or run anything your account can reach.",
        )
      : preset === "ask"
        ? t(
            "trust.access.detail.ask",
            "Commands run in the workspace sandbox, and DeepCode asks you before actions that need approval.",
          )
        : preset === "read_only"
          ? t(
              "trust.access.detail.read_only",
              "The agent can read and plan in this folder but not change it.",
            )
          : null;

  return (
    <ConfirmDialog
      title={t("trust.title", "Trust “{{name}}”?", {
        name: project.displayName,
      })}
      confirmLabel={t("trust.confirm", "Trust folder")}
      cancelLabel={t("trust.cancel", "Cancel")}
      tone={preset === "full_access" ? "danger" : "default"}
      busy={busy}
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      <p>
        {t(
          "trust.intro",
          "Trusting this folder lets DeepCode's agent run commands, edit files and use tools in it:",
        )}
      </p>
      <p>
        <code>{project.canonicalPath}</code>
      </p>
      <ul>
        <li>
          <strong>{accessLine}</strong>
          {accessDetail ? (
            <>
              <br />
              {accessDetail}
            </>
          ) : null}
        </li>
        <li>
          {t(
            "trust.hooks",
            "The project's hooks (.deepcode/hooks.json, .claude/settings.json) are approved as they are now and run automatically during Turns. If they change later, they stop until you approve them again.",
          )}
          <br />
          {hooks.state === "loading"
            ? t("trust.hooks.loading", "Checking this folder's hooks…")
            : hooks.state === "ready"
              ? hooks.count > 0
                ? t("trust.hooks.count", "Project hooks declared now: {{number}}.", {
                    number: hooks.count,
                  })
                : t("trust.hooks.none", "This folder declares no project hooks right now.")
              : null}
        </li>
        <li>
          {t(
            "trust.extensions",
            "The project's own MCP servers and Skills are loaded for the agent.",
          )}
        </li>
      </ul>
      <p>
        {t(
          "trust.advice",
          "Only trust folders whose contents you trust, such as your own code.",
        )}
      </p>
    </ConfirmDialog>
  );
}
