# DeepCode Desktop privacy and diagnostics

DeepCode Desktop is a local execution client. It does not include product
analytics, advertising identifiers, crash-upload SDKs, or an always-on HTTP
server.

## Data stored locally

- Canonical Session identity, title, workspace origin, visible messages, model,
  and task links remain in the existing central JSONL Session store.
- Desktop-only projections, execution state, workflow checkpoints, test
  results, and event replay data are stored in the Desktop SQLite database.
- Generated code and workflow artifacts stay inside the selected project or
  its DeepCode-owned worktree.
- Provider credentials are written only through the existing DeepCode
  configuration layer. The UI never reads a saved secret back.

Removing the Desktop application does not delete projects, canonical Sessions,
or the DeepCode user configuration. Database migrations create a validated,
permission-restricted backup before changing an existing schema.

## Network activity

Network requests happen only for user-requested Agent/provider calls,
user-requested URL acquisition, configured MCP servers, or a manual desktop
update check. Update checks use the configured HTTPS release endpoint and
downloaded update packages must pass Tauri signature verification.

## Diagnostics export

Diagnostics are collected on demand. Export uses a native save dialog and
writes a JSON report with owner-only permissions where supported. The report is
limited to 1 MiB and contains:

- application, Python, platform, and architecture versions;
- sanitized health-check results and paths;
- Session, automation, and database schema counts;
- sidecar lifecycle state.

The report contract explicitly excludes credentials, prompts, conversation
content, project file contents, command output, and environment variables.
Users choose whether and where to save or share the report.
