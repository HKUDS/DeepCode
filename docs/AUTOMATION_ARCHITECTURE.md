# Automation Architecture

Status: implemented and under release validation
Updated: 2026-07-29

## Product contract

DeepCode Automation schedules ordinary agent work. It is not a second agent
runtime and it does not reinterpret a task from keywords.

Every accepted Automation Run uses the same durable path as interactive work:

```text
Automation definition
  -> immutable revision
  -> idempotent occurrence
  -> Run
  -> canonical Goal in the Automation Thread
  -> ordinary typed Turn
  -> shared execution coordinator
  -> Agent or Workflow handler
  -> Goal outcome and evidence
```

The CLI and Desktop are clients of this application service. Neither client
owns scheduling, permissions, recovery, or execution semantics.

## Durable facts

The following records have separate identities because they answer different
questions:

| Record | Question |
| --- | --- |
| `Automation` | What recurring work is currently configured? |
| `AutomationRevision` | Which immutable instruction did this Run receive? |
| `AutomationOccurrence` | Which manual request or nominal schedule tick was observed? |
| `AutomationRun` | What happened to that occurrence? |
| `ThreadGoal` | What outcome is the agent still pursuing across Turns? |
| `Turn` | Which accepted unit of work is queued or executing? |

A manual `requestId` and a scheduled occurrence key are idempotency keys.
Retrying the same request converges on the same Occurrence, Run, Goal, and
initial Turn. Editing an Automation creates a new revision; an open Run keeps
the revision it started with.

Removing an Automation retires its definition. Its canonical Thread, Session,
Run history, and evidence remain available.

## Scheduling policy

Scheduling decisions live behind `AutomationSchedulePolicy`. The initial
policy has explicit, testable semantics:

- interval cadence remains anchored to the persisted nominal deadline;
- missed ticks coalesce into one observed occurrence;
- a due occurrence is recorded as skipped while an earlier Run is still open.

These are policy choices, not prompt rules. A future policy can implement the
same interface without changing the Agent runtime or transport clients.

Only one process may trigger due occurrences for a database at a time.
`AutomationScheduler` obtains a cross-process leader lease; standby processes
take over after the owner exits. Scheduler startup is opt-in at the
composition root: `DeepCodeApplication` and the interactive CLI/TUI default to
no resident scheduler, while the long-lived App Server explicitly enables it.
Desktop receives scheduling through that App Server process. Short-lived
Automation management commands do not create a background scheduler.

## Execution coordination

All Agent and Workflow Turns pass through one `ExecutionCoordinator`.
Admission is based on typed durable fields and resource claims:

- one slot from the configured global capacity pool;
- one Thread fence;
- one canonical-project-workspace fence, or one managed-worktree fence.

Different project workspaces may run concurrently. Threads sharing the same
canonical checkout are serialized. Managed worktrees have independent fences.
The policy is injected through `ExecutionAdmissionPolicy`; it never examines
the task text, model response, tool name, or UI surface.

Each Turn persists its executor as `agent` or `workflow`.
`ExecutionHandlerRegistry` dispatches by that enum. It never guesses an
executor from the prompt or legacy task labels.

Claims contain a worker identity and monotonically increasing Turn epoch.
Terminal settlement and claim release occur in one transaction. A stale
worker cannot release or complete a successor's claim.

## Permissions and approvals

Ordinary interactive Turns preserve their existing CLI/Desktop permission
selection. An Automation snapshots an explicit `ExecutionPermissionMode` when
its Turn is admitted:

1. use an explicitly configured workspace permission mode;
2. otherwise use the safe `default` mode.

The snapshot follows the Turn to whichever worker executes it. Host surface,
prompt text, model name, and tool name do not influence this decision.
Continuation and retry Turns retain the Goal's established permission mode.

Approvals are durable database facts. A local Future is only a low-latency
wake-up mechanism:

- another application process can approve or deny the owner Turn;
- cancellation committed first rejects a late approval;
- concurrent decisions use compare-and-swap;
- `approved_session` grants are persisted for the exact Thread and tool.

An Automation cannot raise its own permissions, and a Skill cannot raise
permissions either.

## Cross-process events and interactions

`event_log` is the authoritative event stream. Local publication provides low
latency. `DurableEventRelay` reads committed events produced by other
processes and feeds them into each local broker without duplicating locally
published events. Replay remains available after overflow, disconnection, or
restart.

Workflow interactions use the Workflow checkpoint as their durable source of
truth. The active interaction stores the owner worker and Turn epoch. Any
application process may submit a response, but the response is accepted only
when the Workflow status, typed executor, cancellation state, owner, epoch,
and complete resource claim still match. The owner polls durable state without
a business timeout; its local Future only reduces latency.

The current Workflow contract allows one active interaction per Run. Keeping
that interaction in the single compare-and-swapped Workflow row avoids a
second table and dual-write recovery path. A separate interaction ledger
would be justified only if the product adds concurrent interactions or
long-term interaction-specific audit queries.

## Failure and recovery semantics

Recovery is conservative:

- an unclaimed queued Turn may be rehomed;
- a claimed running Turn from a proven-dead worker is interrupted, never
  silently replayed;
- pending approval or Workflow interaction state is closed during terminal
  recovery;
- a Run interrupted or blocked before its Goal is complete remains
  explicitly continuable;
- an open Automation Run prevents a second overlapping Run from mutating the
  same Goal Thread.

Worker death is proven with an operating-system file lease before recovery
changes ownership. Heartbeats alone do not authorize takeover.

Automation creation has one deliberately narrow bootstrap exception to the
normal Session-first Thread lifecycle. A single SQLite transaction commits the
new Thread, immutable instruction revision, Automation definition,
`thread.created`, and `automation.updated`. Only then does
`ThreadService.materialize_session` create the exact-ID empty canonical
Session with `kind=automation` and its `automation_id`.

That materialization is idempotent and uses an atomic hidden-directory publish.
A restart, an ordinary Thread read, or a Run received by another live process
repairs a missing Session through the same method before Goal state is read.
No Agent Turn is accepted until the canonical Session exists. If the database
commit succeeds but local materialization fails, create returns
`AUTOMATION_BOOTSTRAP_PENDING` with the committed Automation and Thread IDs.
The error is intentionally non-retryable: refresh or reopen DeepCode instead
of issuing another create request. Durable events remain authoritative and may
already be visible through another process.

If project deletion wins during bootstrap, foreign-key cascades remove the
database facts. The materializer removes only the exact empty Automation
Session that the current creation call demonstrably created. Reconciliation
does not infer orphanhood from one database because a custom database may
share the user's Session store with another database. A pre-existing,
incompatible, or non-empty Session is never deleted by this compensation.

The application lifetime lease is acquired before database initialization.
Exactly one exclusive recovery owner may initialize or migrate the schema and
run crash recovery; it then converts to a shared lifetime lease. Other live
CLI/Desktop/App Server processes join through shared leases and only validate
that the schema is current. A joiner that observes an older schema fails with
`UPGRADE_REQUIRES_EXCLUSIVE_ACCESS` instead of migrating underneath a live
process. After the older process exits, the next exclusive owner performs the
normal backed-up migration.

## What is fixed and what is configurable

The implementation fixes protocol invariants in code:

- valid state transitions;
- identity formats and foreign keys;
- idempotency uniqueness;
- compare-and-swap predicates;
- claim fencing and transaction boundaries;
- bounded transport and evidence payloads.

It does **not** hardcode:

- task categories or repository names;
- prompts, expected outputs, or verification commands;
- provider or model names;
- tool names or Skill names;
- a client-specific execution path;
- a fixed number of agent iterations or retries.

Operational defaults such as polling intervals and product validation limits
are named constants or injectable constructor values. They do not decide
whether a task succeeds.

## Product surfaces

Desktop provides definition editing, manual runs, pause/resume, explicitly
paged definition and Run history, and a link to the canonical Goal Thread.
Load-more actions append stable, ID-deduplicated pages. A live notification or
transport-overflow recovery safely refreshes the first visible page. The Thread
remains the detailed view for conversation, tool calls, approvals, evidence,
changes, and continuation.

Activation status controls only interval scheduling. Manual definitions are
always enabled; a paused interval still accepts an explicit **Run now** trigger,
and changing a paused interval to manual normalizes it to enabled.

CLI exposes the same service:

```bash
deepcode automation list --limit 100 --offset 0
deepcode automation create "Repository caretaker" \
  --prompt "Inspect the repository, repair failures, and verify the result." \
  --schedule interval \
  --interval-seconds 3600
deepcode automation run <automation-id>
deepcode automation runs <automation-id> --limit 100 --offset 0
deepcode automation disable <automation-id>
deepcode automation enable <automation-id>
deepcode automation delete <automation-id>
```

`deepcode automation run` waits for the durable Run to settle without imposing
an arbitrary product timeout. Scheduled execution requires a compatible
scheduler-enabled DeepCode runtime to remain active.

`automation/list` and `automation/runs` use bounded `limit`/`offset` queries.
Both return `hasMore` and a nullable `nextOffset`; the CLI also prints the next
offset in human-readable mode. The service reads `limit + 1` rows only to
determine `hasMore`, and never turns pagination into an unbounded list-all
operation. Retired definitions remain excluded from the definition inventory,
while their immutable Run history remains queryable by Automation ID.

## Release gates

The Automation slice is accepted only when these remain green:

- migration upgrade and downgrade coverage;
- concurrent client and concurrent scheduler idempotency;
- cross-project parallelism and same-workspace serialization;
- dead-worker recovery and claim fencing;
- remote approval and Workflow interaction races;
- durable event relay and replay;
- CLI/App Server protocol contracts;
- Desktop tests, type checking, linting, and production build;
- full Python regression and pre-commit.
