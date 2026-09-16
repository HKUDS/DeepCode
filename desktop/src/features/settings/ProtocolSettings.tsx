import { useTranslation } from "react-i18next";
import type {
  ProviderCompat,
  ProviderProtocol,
} from "../../generated/app-server";
import type { Draft } from "./connectionDraft";
import styles from "./ConnectionSettings.module.css";

export function ProtocolSettings({
  draft,
  onChange,
}: {
  draft: Draft;
  onChange(draft: Draft): void;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.wide}>
      <label>
        {t("provider.protocol", "API protocol")}
        <select
          value={draft.protocol}
          onChange={(event) => {
            const protocol = event.target.value as ProviderProtocol;
            const adapter =
              protocol === "auto"
                ? draft.adapter
                : protocol === "anthropic_messages"
                  ? "anthropic"
                  : "openai_compat";
            onChange({
              ...draft,
              protocol,
              adapter,
              auth: adapter === "anthropic" ? "api_key" : draft.auth,
            });
          }}
        >
          <option value="auto">
            {t("provider.auto", "Auto · existing routing")}
          </option>
          <option value="openai_chat">OpenAI Chat Completions</option>
          <option value="openai_responses">OpenAI Responses</option>
          <option value="anthropic_messages">Anthropic Messages</option>
        </select>
      </label>
      <label>
        {t("provider.auth", "Authentication")}
        <select
          value={draft.auth}
          onChange={(event) =>
            onChange({
              ...draft,
              auth: event.target.value as Draft["auth"],
              apiKey: "",
              clearApiKey: false,
              apiKeyEnv: event.target.value === "oauth" ? "" : draft.apiKeyEnv,
            })
          }
        >
          <option value="api_key">{t("provider.apiKey", "API key")}</option>
          {draft.template === "openrouter" ? (
            <option value="oauth">
              {t("provider.signInAuth", "Sign in with OpenRouter")}
            </option>
          ) : null}
          <option value="none" disabled={draft.adapter === "anthropic"}>
            {t("provider.noAuth", "No authentication")}
          </option>
        </select>
      </label>
      <CompatEditor
        protocol={draft.protocol}
        value={draft.compat}
        onChange={(compat) => onChange({ ...draft, compat })}
      />
    </div>
  );
}

const fields: {
  key: keyof ProviderCompat;
  label: string;
  values: readonly (string | boolean)[];
  chatOnly?: boolean;
}[] = [
  {
    key: "tokenLimitField",
    label: "Token limit field",
    values: ["max_tokens", "max_completion_tokens"],
    chatOnly: true,
  },
  { key: "temperature", label: "Send temperature", values: [true, false] },
  {
    key: "systemRole",
    label: "Instruction role",
    values: ["system", "developer", "user"],
    chatOnly: true,
  },
  {
    key: "reasoningField",
    label: "Reasoning parameter",
    values: ["reasoning_effort", "reasoning", "omit"],
  },
  {
    key: "reasoningContent",
    label: "Reasoning history",
    values: ["preserve", "empty", "omit"],
    chatOnly: true,
  },
  {
    key: "toolMessageName",
    label: "Send tool result name",
    values: [true, false],
    chatOnly: true,
  },
  {
    key: "parallelToolCalls",
    label: "Parallel tool calls",
    values: [true, false],
  },
];

export function CompatEditor({
  protocol,
  value,
  onChange,
}: {
  protocol: ProviderProtocol;
  value: ProviderCompat;
  onChange(value: ProviderCompat): void;
}) {
  const { t } = useTranslation();
  const explicit = protocol !== "auto";
  return (
    <details>
      <summary>{t("provider.compat", "Protocol compatibility")}</summary>
      {!explicit ? (
        <p>
          {t(
            "provider.compatExplicit",
            "Choose an explicit protocol to set compatibility overrides.",
          )}
        </p>
      ) : (
        <div className={styles.modelRowCapacities}>
          {fields
            .filter(
              (field) =>
                value[field.key] != null ||
                (protocol === "anthropic_messages"
                  ? field.key === "temperature"
                  : protocol === "openai_chat" || !field.chatOnly),
            )
            .map((field) => (
              <label key={field.key}>
                {t(`provider.compat.${field.key}`, field.label)}
                <select
                  value={
                    value[field.key] == null ? "" : String(value[field.key])
                  }
                  onChange={(event) => {
                    const next = { ...value };
                    const selected = field.values.find(
                      (candidate) => String(candidate) === event.target.value,
                    );
                    if (selected === undefined) delete next[field.key];
                    else Object.assign(next, { [field.key]: selected });
                    onChange(next);
                  }}
                >
                  <option value="">{t("provider.inherit", "Inherit")}</option>
                  {field.values.map((candidate) => (
                    <option
                      key={String(candidate)}
                      value={String(candidate)}
                      disabled={
                        (protocol === "anthropic_messages" &&
                          field.key !== "temperature") ||
                        (protocol === "openai_responses" &&
                          (field.chatOnly || candidate === "reasoning_effort"))
                      }
                    >
                      {typeof candidate === "boolean"
                        ? candidate
                          ? t("provider.yes", "Yes")
                          : t("provider.no", "No")
                        : candidate}
                    </option>
                  ))}
                </select>
              </label>
            ))}
        </div>
      )}
      {Object.keys(value).length > 0 ? (
        <button type="button" onClick={() => onChange({})}>
          {t("provider.resetCompat", "Clear compatibility overrides")}
        </button>
      ) : null}
    </details>
  );
}
