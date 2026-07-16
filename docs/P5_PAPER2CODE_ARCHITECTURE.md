# P5 Paper2Code Architecture

P5 makes Paper2Code a durable Thread mode. The React client, JSON-RPC adapter,
and legacy CLI do not own workflow truth: `WorkflowService` is the lifecycle
authority, while `DefaultWorkflowRunner` is the only adapter to the existing
research and implementation kernel.

## Runtime boundary

```text
Paper Thread UI
  -> workflow/* JSON-RPC
  -> WorkflowService (transactions, events, recovery, interaction waiters)
  -> WorkflowRunner protocol
  -> existing Paper2Code / Chat2Code pipeline
  -> Artifact metadata + workspace-relative files
```

The App Server imports the legacy workflow stack lazily on the first run, not
during startup. Its original stdout buffer is reserved for JSON-RPC and all
ordinary `print()` output is routed to stderr, so legacy diagnostics cannot
corrupt the protocol stream.

The Agent CLI execution path remains separate from Tauri and JSON-RPC. Shared
workspace, conversion, download-safety, and structured-result fixes apply to
all Paper2Code callers. Truthful strict completion is selected by the Desktop
adapter through `strict_outcomes=True`; legacy CLI/direct Python callers keep
their P0 planner fallback and completion semantics unless they opt in.

## Durable state machine

`WorkflowRun` persists input, result, attempt, retry ancestry, current stage,
progress, checkpoint, timestamps, and stable failure information in SQLite
schema v2.

```text
queued -> running -> waiting -> running -> completed
                   \                    -> failed
                    \                   -> cancelled
```

- Starting a workflow creates one Turn and one user Item in the same
  transaction. A Thread cannot run an Agent Turn and WorkflowRun at once.
- Progress updates reuse the Item for a stage and complete it when the stage
  changes, avoiding an unbounded timeline.
- Plan review persists the interaction request in the checkpoint, changes the
  Workflow/Turn/Thread to waiting, and resumes only for the matching live
  interaction ID. Late progress cannot clear a review gate.
- Cancel maps to `cancelled`, not a generic success or failure.
- On restart, queued/running/waiting workflows become failed with
  `WORKFLOW_INTERRUPTED`; their Turn becomes interrupted and the checkpoint is
  marked resumable.
- Retry creates a new run and Turn with `attempt + 1`, `retry_of`, and the same
  task ID. It never mutates the historical failed attempt.

## Completion and verification contract

For Desktop workflow runs, generating planned files is not sufficient for
completion. The adapter enables strict outcomes, and the implementation kernel
discovers only allowlisted test commands:

- `python3 -m pytest -q` when a Python test layout is present;
- `npm test` only when `package.json` contains a real test script, excluding
  the default “no test specified” placeholder;
- `cargo test` when `Cargo.toml` is present.

Each command runs with a timeout, process-group cleanup, and a 64 KiB tail for
stdout and stderr. A strict workflow is completed only when at least one
command was discovered and every command passed. No tests, failed tests,
incomplete file generation, an invalid planner response, or a warning all
produce a retryable non-success result. The legacy
free-form-to-minimal-plan fallback remains available only when strict outcomes
are not requested.

## Workspace and Artifact safety

- Desktop runs pass an explicit `<Thread workspace>/.deepcode/workflows` root;
  the pipeline no longer derives it from process cwd.
- Paper, URL, repository, and requirement inputs share one `workflow/start`
  contract. A local document must exist and be a regular file; the desktop
  obtains it through the native file picker.
- Artifact records store workspace-relative paths. Symlink-resolved files or
  directories outside the Thread workspace are dropped.
- `artifact/read` returns at most 128 KiB of text. Binary files are recorded
  without being rendered, and directories are represented as metadata.

URL acquisition accepts HTTP(S) only, rejects credentials and non-public IPs,
validates every redirect, uses a resolver that rejects DNS results for private,
loopback, link-local, multicast, reserved, or unspecified addresses, caps the
body at 100 MiB, and downloads through a removable `.part` file.

The packaged baseline converts PDF, Markdown, plain text, HTML, and DOCX without
a system Office installation. PDF text extraction uses maintained `pypdf`;
HTML and DOCX use bounded standard-library readers that discard active HTML
content and never extract an Office archive to disk. Phase 2 fails unless a real
Markdown artifact exists. Optional Docling remains the preferred advanced
converter when a CLI installation explicitly installs the
`advanced-documents` extra, but it is not part of the Desktop sidecar.

## Desktop surface

Paper Threads replace the Agent composer with a Paper2Code console:

- document, URL, repository, and requirement sources;
- native document selection;
- plan review and reference-indexing options;
- durable stage progress and stop;
- inline plan approval, revision feedback, and cancellation;
- truthful failure text and checkpoint retry;
- Artifact Inspector with bounded text preview.

Code Threads keep the existing Agent composer. Interrupt dispatch checks for
an active WorkflowRun first, preventing a workflow Turn from being sent to the
Agent Turn cancellation path.

## Verification evidence

P5-specific automated coverage includes lifecycle completion, interaction,
stale response rejection, late-progress gating, cancellation, incomplete
result handling, retry ancestry, restart recovery, artifact fencing, bounded
preview, stdout protocol isolation, SSRF rejection, baseline TXT/HTML/DOCX/PDF
conversion, optional Docling preference,
verification discovery, generated-test enforcement, JSON-RPC interaction and
Artifact round trip, protocol code generation, and desktop replay of a waiting
plan review.
