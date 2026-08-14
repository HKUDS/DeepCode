export function languageFor(path: string): string {
  const extension = path.split(".").pop()?.toLowerCase();
  return (
    {
      py: "python",
      ts: "typescript",
      tsx: "typescript",
      js: "javascript",
      jsx: "javascript",
      rs: "rust",
      json: "json",
      md: "markdown",
      css: "css",
      html: "html",
      yaml: "yaml",
      yml: "yaml",
      toml: "ini",
    }[extension ?? ""] ?? "plaintext"
  );
}

export function formatBytes(value: number | null): string {
  if (value === null) return "directory";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}
