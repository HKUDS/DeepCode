# P1 Application Core and App Server Architecture

P1 established the application boundary used by the App Server and Desktop.
The CLI deliberately remains a direct adapter over the existing Agent kernel
and `SessionStore`; it does not depend on the Desktop application layer. Agent
execution added in P2 is described in `P2_AGENT_EXECUTION_ARCHITECTURE.md`.

## Dependency direction

```text
app_server  ───────▶ core/application ───────▶ core/domain
                             │                    │
                             ├───────────────▶ core/persistence ──▶ SQLite projection
                             └───────────────▶ core/sessions ─────▶ canonical JSONL

CLI/TUI/MCP ───────▶ Agent kernel + core/sessions (independent adapter boundary)

desktop/src/rpc ── generated TypeScript contract
                         ▲
                         │
              protocol/app-server.schema.json
```

The enforced boundaries are:

- `core/domain` imports no UI, transport, database, Provider, Agent, or Workflow
  implementation.
- `core/application` owns use cases and transaction orchestration.
- `core/persistence` maps domain objects to SQLite and never imports the
  application or App Server.
- `app_server` validates JSON-RPC input, invokes application services, and
  translates stable errors. It does not issue SQL.
- `desktop/src/generated/app-server.ts` is generated from the canonical schema;
  it is never edited by hand.

## Durable state and ownership

`SessionStore` JSONL is the canonical identity and user-visible conversation
record shared by CLI and Desktop. A Desktop Thread uses exactly the same ID as
its Session, including established eight-character CLI IDs. Session title,
workspace origin, model, archive metadata, visible user/assistant transcript,
and task links remain in the existing Session format.

The Desktop database is `$DEEPCODE_HOME/state/deepcode.sqlite3`, falling back to
`~/.deepcode/state/deepcode.sqlite3`. It is a rebuildable projection plus
Desktop-only runtime state. Schema v1 contains:

```text
projects          threads           turns             items
approvals         workflow_runs     artifacts         event_log
legacy_imports    schema_migrations
```

SQLite runs with foreign keys, WAL, a busy timeout, and short `BEGIN IMMEDIATE`
write transactions. Aggregate relationships are checked both by domain
invariants and database constraints. Desktop Turn/Item/event mutations are
committed before they are offered to live subscribers. Deleting the projection
database must not delete Session history: Thread identity and visible transcript
are rebuilt from JSONL on the next App Server start.

Event sequence numbers are monotonic inside one Thread. Live queues are bounded;
on overflow the App Server sends `server.warning` and the consumer must recover
with `event/replay`. Replay is cursor-paginated with `nextAfter`/`hasMore`, and
the transport trims a requested page to the largest encoded event prefix that
fits the advertised message limit. The event log is the durable replay source
for the current Desktop projection, while JSONL remains canonical for Session
history.

Streaming assistant text is stored as one initial Item, compact `item.delta`
events, and one final Item checkpoint. The current Item row always contains the
complete text, while the append-only log grows linearly instead of saving the
entire accumulated response for every streamed update.

## P1 protocol surface

The transport is JSON-RPC 2.0, one UTF-8 JSON object per line over stdio. A
message is limited to 1 MiB. stdout contains protocol messages only; operational
logs go to stderr.

Implemented methods:

```text
initialize        shutdown
project/list      project/add       project/read
project/update    project/remove
thread/start      thread/resume     thread/list       thread/read
thread/rename     thread/archive    thread/fork
event/replay
```

The client must call `initialize` with protocol version `1.0`. Results advertise
the actual method set and transport capabilities. Errors include a stable string
code and correlation ID, while internal exceptions and stack traces remain on
stderr.

Run the server from a source checkout:

```bash
python -m app_server
python -m app_server --database /tmp/deepcode-p1.sqlite3
```

Example handshake:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"1.0","clientInfo":{"name":"manual-client","version":"0.1"}}}
```

## Protocol generation

The JSON schema is the source of truth for desktop data types:

```bash
cd desktop
npm run generate:protocol
npm run check:protocol
```

`check:protocol` fails when the committed generated file is stale. Desktop code
uses `src/rpc/contracts.ts` to preserve the relationship between each method,
its params, and its result.

`thread/list` accepts an optional exact `cwd`; omitting both `projectId` and
`cwd` lists Sessions across directories. `thread/resume` defaults to the
Session's recorded workspace. A caller may explicitly supply `workspacePath`
to use a different existing directory for the current App Server process; this
does not rewrite the Session's recorded origin.

## Session alignment and external import

Established Sessions in the configured central `SessionStore` are not legacy
data and are not copied. `ThreadService` scans them directly and repairs their
SQLite projection under the same ID. A long-lived Desktop process observes CLI
writes by invalidating Session caches when canonical files change.

`LegacySessionImporter` now exists only for an explicitly selected external
SessionStore. It copies that external record into the configured canonical
store without changing or deleting the source, then projects the copied Session
under the same canonical ID. Workspace trust and Project fences still apply.

## Intentional limits at the P1 milestone

- At the P1 milestone, Agent execution, Turn cancellation, approval decisions,
  and crash recovery were not yet wired; P2 has since completed them.
- No Rust sidecar supervision or React runtime connection exists yet.
- No HTTP listener, WebSocket, generic shell permission, or database access from
  the frontend exists.
- `thread/delete` is not exposed in P1; archive is the durable user-facing
  lifecycle operation until deletion/audit semantics are defined.

Agent execution, cancellation, approvals, and crash recovery are now complete in
P2. Rust sidecar supervision and the React runtime connection remain P3 work.
