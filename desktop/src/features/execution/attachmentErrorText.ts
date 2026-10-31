import type { TFunction } from "i18next";

/**
 * Why attaching files failed, in words. The browser's own upload limits
 * (src/rpc/browserRuntime.ts) are said in the user's language; any other
 * error keeps its message.
 */
export function attachmentErrorText(error: unknown, t: TFunction): string {
  switch ((error as { code?: unknown } | null)?.code) {
    case "NO_THREAD":
      return t("attach.noThread", "Select a trusted workspace before uploading files.");
    case "UPLOAD_TOO_LARGE":
      return t("attach.tooLarge", "Choose up to 8 files, each at most 10 MiB.");
    default:
      return error instanceof Error ? error.message : String(error);
  }
}
