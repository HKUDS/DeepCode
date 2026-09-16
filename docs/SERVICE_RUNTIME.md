# Local background service

The service keeps one `DeepCodeApplication` running independently of the CLI
process that starts it. It uses the existing Agent, execution coordinator,
Automation scheduler, Session store, and application database.

## Commands

```sh
deepcode service start
deepcode service status --json
deepcode service logs --lines 100
deepcode service logs --follow
deepcode service stop --drain --timeout 60
```

`start` waits for an authenticated ready response, then returns. Closing the
launching terminal does not close the service. Repeated or concurrent starts
converge on the same service; an unused child created by a competing launcher
is reaped before that launcher returns.

The default management port is 3081. `start --port 0` requests an available
port, and status reports the actual address. An explicit occupied port fails
with a log entry. To change the port of a running service, stop it first.

Every service subcommand accepts `--database <path>` to address an isolated
database. Without it the existing `$DEEPCODE_HOME/state/deepcode.sqlite3`
location is used. Foreground operation is available for development or an
external supervisor:

```sh
deepcode serve --foreground --port 3081
deepcode service restart --drain --timeout 60
```

The listener provides management, browser authentication, business WebSocket RPC
and the packaged Web client. Run `deepcode web` to open its short-lived local
access link; see [WEB_CLIENT.md](WEB_CLIENT.md) for setup and browser behavior.
Desktop still owns its existing private
stdio application; this change does not make Desktop-submitted tasks survive
Desktop exit yet. A running service can host the existing Automation scheduler,
whose leadership lease prevents duplicate scheduled triggers across processes.

The manually detached service is not automatically restarted after a crash or
system reboot. A service must remain running for its scheduled work to execute.

## Optional macOS login startup

```sh
deepcode service install --at-login
deepcode service start
deepcode service doctor --json
```

Login means signing into the **macOS user account**, not a DeepCode account.
Installation is explicit and optional. It writes one private plist under the
current user's `~/Library/LaunchAgents`, keyed by the database identity. It does
not register a system-wide daemon, change other LaunchAgents, or enable startup
merely because the service code was installed.

`install` schedules startup for the next user login; `start` loads it now. Once
installed, the ordinary start/restart commands use launchd and the same service
executable and management protocol. Abnormal exits are restarted by launchd,
with a ten-second throttle. There is no additional Python watchdog.

If a manually started service already runs, installing the startup item leaves
it running. Use `service restart --drain` to transfer ownership to launchd after
current work has finished. A loaded LaunchAgent must be stopped before changing
its installed command or port.

```sh
deepcode service stop --drain --timeout 60
deepcode service uninstall
```

Stopping a launchd-owned service first drains admitted work, then removes the
loaded job, so it cannot immediately restart. If unloading fails, the client
restores admission. A drain timeout leaves the job running. If the controlling
client exits after preparing a drain, `service start` can resume that service.
The plist remains installed after stop and starts again at the next login;
`uninstall` removes that future startup setting. Uninstalling an active managed
job uses the same drain/cancel policy, while an unrelated manually started
service remains running. Project files, Sessions and credentials are retained.

The plist records the current Python environment and module directory. It keeps
the virtualenv executable path rather than resolving it to a different Python.
After moving or removing that environment, reinstall the startup item. `doctor`
checks the executable, directory, runtime home, GUI login domain and loaded job.
It never treats a PID alone as proof of service identity.

The managed process receives the configured DeepCode home, optional Session
directory, and PATH captured at installation (`install --path ...` overrides
PATH). API keys and proxy credentials are not copied from the terminal into
the plist. Prefer the existing private credential store; explicitly configure
the managed environment when a proxy is required. Doctor lists known shell-only
credential/proxy variable names without printing their values. A shell profile
such as `.zshrc` is not sourced by the service.

This user LaunchAgent runs in the GUI login domain. It does not promise
execution while logged out or asleep. Linux/Windows service-manager installation
remains a later stage; their existing manual start path is retained.

## Stop behavior

The default stop mode drains admitted Turns and open terminals. It pauses new
dispatch and scheduled triggers while continuing worker heartbeats and
cancellation handling. Already queued Turns are not started during this wait.
Normal application shutdown settles queued work through the existing worker
and Goal lifecycle.

If the timeout expires, admission and the previously active scheduler resume,
the service remains running, and the command exits nonzero. It does not silently
cancel a pending approval or kill a running tool. To explicitly cancel current
service work and then stop:

```sh
deepcode service stop --cancel-running
```

Global service stop is separate from a client's RPC `shutdown` operation.
Unexpected process termination can interrupt work; startup uses existing
recovery semantics and does not blindly repeat shell commands whose result is
unknown. Stop addresses the authenticated instance: if another launcher starts
a replacement during cleanup, the command reports that fact rather than
stopping the replacement.

## Ownership and local authentication

`app_server.service` owns the listener and `ServiceHost`; `ServiceLifecycle`
queries application-owned activity and pauses the existing coordinator.
`ServiceClient` performs management calls without constructing another
application or scheduler.

An OS-backed `FileLease` protects the managed service instance. It is separate
from existing application recovery, worker liveness, and scheduler leases.
Discovery files live beside the database in `<database-name>.service/`:

- `instance.lock`: lifetime lease, never replaced as a stale-PID workaround;
- `instance.json`: instance identity, database, port, PID and protocol version;
- `token`: private management credential, separate from public status;
- `service.log`: service log with 5 MiB rotation and three backups;
- `management.lock`: serialization for start/stop/install/uninstall/restart,
  distinct from the service process's lifetime lock.

Management binds only to `127.0.0.1`. A client proves the server knows its private
token through a fresh HMAC challenge before sending its bearer credential, so
an unrelated process reusing a stale port does not receive that credential.
Requests also bind to the instance ID. The HTTP Host is checked, browser Origin
headers are rejected on management routes, and the client ignores ambient HTTP proxy settings.
These controls do not grant any additional Agent tool permissions.

Management routes are `/health/live`, `/health/ready`, `/control/identity`, and
`POST /control/rpc` (`status`, `stop`, supervisor `drain` and `resume`, and
`auth/issue`). The management endpoint reuses the existing strict JSON-RPC codec.

## Business connections and browser sessions

`GET /api/rpc` upgrades to a WebSocket carrying one JSON-RPC object per UTF-8
text message. It calls the existing `RpcPeer` and `Dispatcher`, including their
initialization checks, method handlers, permission checks and event replay.
Binary frames close with code 1003; oversized frames close with code 1009.
The default per-message limit remains 1 MiB.

Native clients first verify the service identity through the management client,
then authenticate the WebSocket with the same private bearer and
`X-DeepCode-Instance`. Missing Origin is never sufficient authorization.
Browser authentication is a separate flow:

1. The local management client calls `auth/issue`. The result includes
   `instanceId`, a single-use `ticket`, and `expiresIn` (60 seconds).
2. The same-origin browser posts `{"ticket":"…"}` as JSON to `/auth/exchange`.
   Successful exchange consumes the ticket and sets a host-only, HttpOnly,
   SameSite=Strict cookie. Session credentials are absent from the response body.
3. The browser opens `/api/rpc` with that cookie and the exact service Origin.
   A browser-supplied management Authorization header does not bypass session
   authentication. No CORS origins or forwarded hosts are implicitly trusted.
4. `POST /auth/logout` revokes that browser session and closes all its sockets.
   Other sessions and admitted Agent tasks continue.

Sessions expire after 12 hours, including already connected sockets. A service
restart invalidates all sessions and unused tickets. Limits are 64 pending
tickets, 64 sessions, and 60 exchange attempts per minute per service. Sensitive
responses use `Cache-Control: no-store`; the service disables access logging.
Cookies currently travel over loopback HTTP, so they do not use the HTTPS-only
Secure attribute. This is not a remote HTTPS or multi-user deployment mode.

`deepcode web` issues the ticket and opens the packaged same-origin client.
The browser removes the fragment before exchanging it. This is local service
access, with no DeepCode account registration or password login.

Initialization retains protocol `1.0` and its existing method capabilities.
Network clients additionally receive optional `serviceInfo`: service instance
ID, database schema version, `transport: "websocket"` and
`shutdownScope: "connection"`. Stdio responses retain their existing shape.
`clientInfo.surface: "web"` records provenance without granting permissions.
The optional `frontendBuildId` identifies the packaged Web resources; the
browser checks it against its compiled identity before normal RPC use.

Each connection has independent initialization, request IDs and subscription.
Connections share the local user's projects and application permissions; this
is not tenant isolation. A connection's `shutdown` closes only that connection.
Disconnect does not cancel an admitted RPC mutation or an Agent Turn.

There are at most 32 connections and eight concurrent business RPC handlers in
a dedicated executor. Each peer processes requests in order while its socket
reader continues answering ping/pong during slow handlers. Input is bounded by
32 frames / 4 MiB; output by 1024 frames / 8 MiB, allowing ordinary event batches
without prematurely evicting healthy clients. Overflow closes that connection
with 1013. Writes and the whole socket close have deadlines. Queue wakeups are
coalesced, rather than scheduling one event-loop callback per frame.

Drain blocks new business work, waits for admitted handlers, then uses the
existing application drain. Approvals, interrupts, terminal close and selected
state queries remain available to finish current work. On a timeout, the prior
service phase is restored. Shutdown closes sockets and waits for admitted
handlers before closing the application. This uses aiohttp's
[shutdown and cleanup lifecycle](https://docs.aiohttp.org/en/stable/web_advanced.html#graceful-shutdown).

BrowserRuntime reconnects with bounded backoff and honors the explicit retry
contract described below. Clients query durable state and `event/replay`; they
must not blindly resend writes after a lost response. Terminal output uses its own bounded read window; it and other
transient notifications are not part of Thread event replay.

## Input receipts and the network retry contract (F05)

Network initialization now includes `capabilities.requestRetry`. Its default
is `never`: methods are not inferred to be retryable from their names. Explicit
read methods may be repeated; keyed methods require the original nonempty key
and the same request. The current keyed methods are `turn/start`, `turn/enqueue`, `turn/steer`
(`messageId`) and the existing `automation/run` (`requestId`). This advertises a
contract; it does not install an automatic retry loop in existing clients.

For a Turn submission whose response was lost, call `turn/input/read` with
`threadId` and `messageId`. Its `item` is null when no durable input receipt is
present, or the original user Item, whose `turnId` can be passed to `turn/read`.
This lookup also works while the service is draining. It does not submit or
activate work. A missing receipt is a point-in-time observation; a still-running
request may commit later, so any resubmission must retain its original key.

Turn input keys use the existing `(threadId, messageId)` namespace. New keyed
start/enqueue submissions save a versioned SHA-256 request fingerprint in the
same transaction as the Turn and initial Item, then preserve it in canonical
user-message metadata. It includes the original prompt, explicitly selected
Skills/model/connection/reasoning and submission semantics. It deliberately
does not fingerprint later runtime defaults or Goal-inherited Skills. Thus an
identical retry returns the original Turn's current snapshot; changing its
request returns `DUPLICATE_MESSAGE_CONFLICT` instead of silently ignoring the
changed selection. It is not a byte-for-byte response cache.

Receipts remain with the input data, without a separate TTL or retry database.
Normal service restart retains them. When persisted canonical inputs rebuild a
projection, their keys and fingerprints are retained; rebuilt Turn IDs can
change, so use the lookup. Deleting the Thread/input data ends that retention;
clients should not deliberately recycle keys. Inputs not yet appended to JSONL
still depend on their durable SQLite records. This is not a guarantee against
manual deletion of all durable state.

Older receipts have no original-request fingerprint. Their existing text/source
checks remain; supplied model selectors and effective Skills are checked against
the saved execution snapshot. Omitted defaults refer to the original Turn.
The server does not fabricate a fingerprint for historical requests. Normal
project trust, workspace and Goal checks still apply before submission/retry;
reading a receipt cannot restore execution trust after a projection rebuild.

Steer retries must preserve `expectedTurnId` as well as the key and text. The
existing user Item carries a separate `payload.deliveryState`:

| State | Evidence and retry behavior |
| --- | --- |
| `pending` | SQLite recorded the intent; delivery has not been confirmed. A same-key retry returns `INPUT_DELIVERY_PENDING` with `retryable: true`. Query the receipt or retry with backoff. |
| `accepted` | The canonical append completed, the original Turn's mailbox committed the input, and SQLite saved confirmation. A same-key retry returns success with `duplicate: true`, without reinjection. |
| `unknown` | Delivery could not be established. The retry returns `INPUT_DELIVERY_UNCERTAIN` with `retryable: false`. Inspect the original Turn; do not automatically generate a new key or redirect the input to another Turn. |

Successful `turn/steer` responses also include `deliveryState: "accepted"`.
Acceptance is not proof that the model consumed or followed the instruction:
the Turn can still be interrupted before consuming it. An uncommitted mailbox
reservation is never reported as duplicate success. Producer cleanup cannot
retract a committed input. `turn.steered` is written together with confirmation,
after canonical persistence and mailbox commit, rather than with the initial
intent. Live notifications can be lost; query the durable receipt or replay.

Failures after intent persistence leave an uncertain receipt, even when they
occur after an actual append or mailbox commit. If storage itself remains
unavailable, a pending receipt may not settle until Turn termination/recovery.
Normal restart preserves accepted receipts and settles abandoned pending ones
to unknown. Legacy Steer records have no confirmation evidence and read as
unknown. Canonical JSONL records carry the key, original expected Turn and
`deliveryState: "unknown"`: a transcript proves persistence, not acceptance by
a vanished in-memory mailbox. Rebuilding SQLite therefore conservatively
returns unknown for these inputs. Historical JSONL is not rewritten, and no
second input ledger or automatic resubmission worker is added.

Goal creation, configuration writes, file changes, terminal input and other
unlisted mutations remain `never`. The browser reports `RESULT_UNKNOWN` if their
response is lost and does not queue them while offline. Read current state before
a subsequent user action. Existing revision checks and approval CAS remain the
authority; no universal durable mutation ledger has been added.

## Contiguous event recovery (F05)

`initialize` subscribes the connection to live events before returning. Install
the client notification listener before starting history recovery. `event/replay`
now returns an optional `headSequence`, the committed cutoff captured before
reading its page. Pass that value as the optional `through` parameter on later
pages. New commits do not extend that round. `after` remains exclusive, `through`
inclusive; existing `nextAfter`/`hasMore` and byte-bounded pagination still apply.
When the retained history is shorter than a supplied cutoff, the returned head
reflects that change rather than claiming missing records exist.

The shared TypeScript `ThreadEventStream` feeds the selected workspace through
one contiguous cursor. A live event exactly following the cursor can be applied
immediately. A later sequence triggers replay from the cursor; overlapping live
payloads are read from the durable log instead of accumulated in another queue.
Already-applied sequences are ignored. Only one recovery runs at a time, and an
overflow warning during recovery requests another check so a dropped final event
is not missed merely because no later notification arrives.

Recovering a gap preserves the current view and selected Item. Selecting or
creating a Thread, or receiving `ready` after an unavailable runtime, rebuilds
that selected trace from history. Stopped streams ignore late replay responses
and errors. Async notification subscriptions resolving after unmount are cleaned
up, and live listeners are installed before status can start the initial replay.
The unused reducer-wide `lastSequence` maximum has been removed; it was not a
contiguous cursor. Entity-specific versions remain to protect event state from
older snapshots and duplicate deltas.

Replay rejects a missing sequence, wrong Thread, changed captured head or a
non-advancing cursor instead of skipping evidence or retrying indefinitely.
A single oversized event still returns `RESPONSE_TOO_LARGE`; recovery does not
silently drop it. Older servers without `headSequence` retain their existing
pagination behavior, without the fixed-cutoff guarantee.

This increment covers durable events in the selected workspace. Unselected
Thread summaries remain best-effort live updates and are read again on
selection. Terminal output and other transient notifications are not recoverable
from `event_log`. The synchronizer is transport-independent and is connected to
both Desktop and BrowserRuntime. The browser also invalidates mounted settings,
plugin, MCP and known-project Skill catalogs after a successful reconnect.

## Bounded terminal output recovery (F05)

`terminal/list {threadId}` discovers this service's live terminals and recently
exited output windows. `terminal/read` is read-only, accepts `threadId`,
`terminalId`, a starting byte position `offset` (default 0), a byte `limit`
(default 16 KiB, range 4–64 KiB), and an optional exclusive end position `through`.
The positions count the UTF-8 encoding of decoded terminal text, after the
existing incremental decoder replaces invalid input bytes. They are not a
Thread event sequence or a count of JavaScript string characters.

| Result | Meaning |
| --- | --- |
| `offset`, `nextOffset`, `data` | Returned text occupies the half-open byte range `[offset, nextOffset)`. Pages end on a UTF-8 boundary. |
| `availableFrom`, `truncated` | The oldest retained byte position and whether the requested prefix was evicted. |
| `headOffset`, `hasMore` | The latest captured total output size, and whether more remains within this request's cutoff. `headOffset` can exceed a supplied `through`. |
| `exited`, `exitCode` | Process state retained with its final output. The reader displays exit only after catching up to the final head. |

Each terminal retains at most 256 KiB of encoded text. At most eight live
terminals and eight exited windows are retained by default (up to 4 MiB of
stored text, plus metadata and bounded temporary pages). Exited windows are
evicted oldest-first by count, not by a time-based TTL, and do not consume live
terminal capacity. The service keeps this data only in memory. After service
restart or eviction, an old terminal ID is unavailable; no old process or output
is fabricated, and no command is restarted.

Live `terminal.output` notifications retain `data` for older clients and add
optional `offset`/`nextOffset`; exit notifications add the final `nextOffset`.
The Desktop reader uses notifications as hints and reads the bounded window.
It coalesces recovery requests, captures a cutoff for each round and waits for
xterm's write callback before reading more. Truncation resets the renderer and
prints a notice before retained text: this is a byte stream, not an exact saved
terminal screen or ANSI parser checkpoint. TTY output is preserved as received,
including carriage returns and control sequences.

The panel subscribes before discovery and initial replay. It reattaches to the
matching terminal, otherwise the most recent live terminal or retained exit for
that Thread. Unmounting or changing Thread detaches the view; explicit **Close**
terminates the PTY. The reader owns descriptor closure, including natural exit,
and shutdown waits for readers to clean up. A renderer detach cancels a pending
write wait without accumulating cancellation handlers over a long session.

`terminal/list` and `terminal/read` are advertised as safe reads and remain
available during drain. Create, resize, close and input are not automatically
resent. Disconnected keystrokes are not queued. Older backends without the read
capability retain live-only rendering. Closing Desktop detaches its connection;
the service retains the task and terminal until explicitly stopped.

## Compatibility and implementation boundaries

- AgentRunner, provider routing, compaction, memory framing, model context caps,
  tool permission checks, and Session serialization keep their existing paths.
- The coordinator gains a reversible admission pause; the default remains
  unpaused. Its original dispatch implementation has one caller through that
  boundary, rather than a second dispatch algorithm.
- Shared loguru/stdlib routing is retained. A host can supply a different
  console sink, and service log rotation uses the existing private-file helpers.
- The network listener uses the already required aiohttp library. It does not
  add a parallel Starlette/Uvicorn stack or a second Automation scheduler.
- Existing stdio clients use the thin `AppServer` wrapper over the same
  `ServiceHost`/`RpcPeer` implementation.
- Browser sessions and socket I/O live in separate modules; there is no second
  business dispatcher, Agent executor, scheduler, or request decoder.

## Validation

`tests/app_server/test_service.py` exercises actual foreground and detached
subprocesses, concurrent starts, CLI launcher exit, clean stop, local
authentication, invalid control requests, and identity mismatch. Application
tests cover preserved running Turns, drain timeout restoration, queued work,
and heartbeats during an admission pause.

Regression coverage also includes the existing App Server, cross-process
coordination, context-window profiles, compaction memory, untrusted memory
framing, tool descriptions, MCP lazy activation, command guard, lifecycle
hooks, model catalog, and CLI logging tests. These tests bound the known risk;
they do not assert compatibility with every real Provider or untested platform.

`tests/app_server/test_launchd.py` covers private, idempotent installation,
unchanged live configuration, missing runtime paths, drain/unload rollback and
preservation of manually started services. Its opt-in native test bootstraps a
temporary job, kills only its verified test service, observes automatic restart,
then verifies the job remains stopped beyond the restart throttle and removes
the test plist. It does not install a default user startup item or log the user
out to test a full login cycle.

`tests/app_server/test_websocket.py` exercises real loopback WebSockets and an
isolated application. It covers unauthorized and wrong-origin requests,
single-use tickets, expiry/logout, separate peers, protocol mismatch, continued
Turns after disconnect, admitted writes surviving lost sockets, RPC drain,
ping/pong during a blocked handler, bounded slow-client output and oversized
messages. Controlled Agent sessions write a real workspace result; these tests
do not make real-provider calls or automate a finished browser UI.

The network regression also kills a verified test service while a deterministic
Agent is running. On restart the Turn is interrupted with `worker_crashed`, and
its side-effect marker is not written again. This exercises production leases,
storage and recovery across actual process termination.

Transcript reconciliation distinguishes internal context-note delivery markers
from ordinary user input provenance. It compares nonempty conversation text on
both sides while preserving tool-only timeline records. Regression coverage
includes JSONL rebuilds, continued exclusion of internal notes, and detection
of an actual transcript disagreement; historical conflict events are retained.

## Native clients attached to the service

Desktop connects to the shared service by default. The native bridge launches
a small stdio relay, which authenticates with the service and forwards RPC.
EOF, window exit and Reconnect close only the client connection. Network or
version failures are reported without creating another execution owner.
Settings offers an explicit background-service stop action with current activity
and bounded drain; closing the window does not invoke it.

Frozen service bundles are copied atomically into a versioned private runtime
directory before starting the daemon. Desktop updater replacement therefore does
not remove the daemon's executable/resources. Older runtime copies are retained;
there is no automatic deletion of potentially active versions. Source/venv
launchers retain their configured development environment. Both source and
frozen launch arguments are validated by the macOS manager.

TUI and one-shot CLI runs use the same service by default:

```sh
deepcode --trust --workspace /path/to/project
deepcode exec --trust --workspace /path/to/project "your task"
deepcode exec --detach --trust --workspace /path/to/project "your task"
```

`--trust` is an explicit grant; omit it for a project already trusted. Service
mode does not open a local execution owner for catalog or Goal commands. TUI
`/reconnect` reconnects its transport; exiting detaches, while an explicit
interrupt still stops the selected task. Output is reconstructed from the durable
Thread event log into the existing renderer vocabulary, with a continuous cursor,
fixed replay cutoffs and bounded active-item state. It does not rely on a second
transient stream that disappears during disconnection.

Foreground `exec` still waits, handles approvals and returns a task-based exit
code. Only explicit `--detach` returns immediately with the admitted Thread/Turn
identity. Non-interactive foreground Ask mode still denies approvals rather than
silently enabling full access.

## Linux and Windows supervision

`service install --at-login` uses a systemd **user** unit on Linux and a
current-user Task Scheduler logon task on Windows. It does not install a root or
administrator system service. Registration does not immediately start the
service; use `service start`. Stop/uninstall preserve user data. Doctor identifies
the selected manager and missing user-session services.

Linux definitions disable command environment substitution and distinguish
systemd path fields from command argument quoting. A real `systemd-analyze --user
verify` test checks generated units when available. Containers without a user
systemd session can still run the manual foreground/detached service but cannot
prove login supervision.

Windows uses a fixed PowerShell/Task Scheduler API adapter and a private managed
configuration file, not a generated command shell wrapper. The task uses the
current user's interactive token, prevents duplicate instances, and does not
inherit Task Scheduler's default execution time limit. Native Task Scheduler,
user logon and platform packaging still require Windows validation; XML/unit
tests alone do not establish those behaviors.

Provider protocol/configuration and login details are in
[Provider connections](PROVIDER_CONNECTIONS.md). Offline upgrade preparation,
snapshots and interrupted-restore handling are in
[Upgrade and restore](UPGRADE_AND_RESTORE.md).
