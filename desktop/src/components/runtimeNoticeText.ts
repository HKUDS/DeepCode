import type { TFunction } from "i18next";

/**
 * What the browser's own connection reports, by the codes it raises itself
 * (src/rpc/browserRuntime.ts): a title and a text in the user's language.
 * Only the browser host raises these meanings; the Desktop relay reuses some
 * codes for a slow service, and its message says so.
 */
function browserReport(
  code: string | null | undefined,
  serviceOffline: boolean,
  t: TFunction,
): [title: string, message: string] | null {
  switch (code) {
    case "AUTH_REQUIRED":
      return [
        t("runtime.browserAuthRequired", "Browser access required"),
        t("runtime.browserAuthHelp", "Run deepcode web in your terminal to open a new browser access link. No DeepCode account is needed."),
      ];
    case "CONNECTION_LOST":
      return [
        t("runtime.connectionLost", "Connection to DeepCode lost"),
        serviceOffline
          ? t("runtime.connectionLostOffline", "The service is unavailable. Reconnect when it is ready.")
          : t("runtime.connectionLostHelp", "Connection lost; the service may still be running."),
      ];
    case "RESPONSE_TIMEOUT":
      return [
        t("runtime.responseTimeout", "No response from DeepCode"),
        t("runtime.responseTimeoutHelp", "The response timed out; the task may still be running."),
      ];
    case "RESULT_UNKNOWN":
      return [
        t("runtime.resultUnknown", "The last action may have gone through"),
        t("runtime.resultUnknownHelp", "The connection dropped before DeepCode answered. Check the Session before trying again."),
      ];
    case "NOT_CONNECTED":
    case "DISCONNECTED":
      return [
        t("runtime.notConnected", "Not connected to DeepCode"),
        t("runtime.notConnectedHelp", "The request was not sent. Reconnect and try again."),
      ];
    case "CLIENT_BUSY":
      return [
        t("runtime.clientBusy", "Too many requests at once"),
        t("runtime.clientBusyHelp", "Wait for the pending requests to finish, then try again."),
      ];
    case "REQUEST_TOO_LARGE":
      return [
        t("runtime.requestTooLarge", "Request too large"),
        t("runtime.requestTooLargeHelp", "The request is larger than the service accepts. Send less at once."),
      ];
    case "WEB_BUILD_MISMATCH":
      return [
        t("runtime.buildMismatch", "This page is out of date"),
        t("runtime.buildMismatchHelp", "This page and the service come from different builds. Reload the page or reinstall the complete release."),
      ];
    case "NO_THREAD":
      return [
        t("runtime.noThread", "No Session selected"),
        t("runtime.noThreadHelp", "Select a trusted workspace before uploading files."),
      ];
    case "UPLOAD_TOO_LARGE":
      return [
        t("runtime.uploadTooLarge", "Files too large"),
        t("runtime.uploadTooLargeHelp", "Choose up to 8 files, each at most 10 MiB."),
      ];
  }
  return null;
}

/** The title and text of the runtime banner. */
export function runtimeNoticeText(
  code: string | null | undefined,
  serviceMessage: string,
  { serviceOffline, browser }: { serviceOffline: boolean; browser: boolean },
  t: TFunction,
): [title: string, message: string] {
  const report = browser ? browserReport(code, serviceOffline, t) : null;
  return (
    report ?? [
      serviceOffline
        ? t("runtime.serviceUnavailable", "Local service unavailable")
        : t("runtime.requestFailed", "Request failed"),
      serviceMessage,
    ]
  );
}

/** Why attaching files failed, in words: the browser's upload limits, or the error's message. */
export function attachmentErrorText(error: unknown, t: TFunction): string {
  const code = (error as { code?: unknown } | null)?.code;
  if (code === "NO_THREAD" || code === "UPLOAD_TOO_LARGE") {
    return browserReport(code, false, t)![1];
  }
  return error instanceof Error ? error.message : String(error);
}
