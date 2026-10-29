# DeepCode Desktop privacy and diagnostics

DeepCode Desktop is a client of the local DeepCode background service, the
same service the TUI, Web client and `deepcode exec` use. It does not include
product analytics, advertising identifiers, or crash-upload SDKs.

The background service is a local HTTP and WebSocket server. It listens on
`127.0.0.1` only (port 3081 by default), requires a private token or a
one-time browser sign-in, and keeps running after a client closes so accepted
work can finish. Stop it with `deepcode service stop`. See
[Security and data](guide/security.md#the-background-service).

## Data stored locally

- Canonical Session identity, title, workspace origin, visible messages, model,
  and task links remain in the central JSONL Session store
  (`~/.deepcode/sessions/`).
- Projections, execution state, workflow checkpoints, test results, and event
  replay data are stored in the shared state database
  (`~/.deepcode/state/deepcode.sqlite3`).
- Provider-returned reasoning summaries or details, when available, remain
  separate from assistant messages. Safe summaries may be retained in canonical
  Session metadata; display details stay in the local execution projection.
  Opaque provider continuation state is not rendered as text.
- Generated code and workflow artifacts stay inside the selected project or
  its DeepCode-owned worktree.
- Provider credentials are written only through the existing DeepCode
  configuration layer. The UI never reads a saved secret back.

Removing the Desktop application does not delete projects, canonical Sessions,
or the DeepCode user configuration. Database migrations create a validated,
permission-restricted backup before changing an existing schema.

## Network activity

Outbound network requests happen only for Agent/provider calls, model list
refreshes, user- or Agent-requested URL fetches, configured MCP servers,
commands the Agent runs, or a manual Desktop update check. Update checks use
the HTTPS release endpoint configured when the app is built (a source build
configures none), and downloaded update packages must pass Tauri signature
verification. The background service accepts connections from this computer
only.

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
