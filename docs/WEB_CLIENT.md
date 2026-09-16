# Local browser client

`deepcode web` starts or reuses the local background service and opens its Web
client. Closing or refreshing the page leaves admitted tasks and terminals in
that service running. It uses the same Agent, tools, approvals, project trust,
configuration, Session history and application services as Desktop.

## Open the client

With a complete Python release installed and a model connection configured:

```sh
deepcode web
deepcode web --no-open
deepcode service status --json
```

The launcher prints the actual URL, including a single-use ticket valid for
60 seconds. The browser immediately removes the ticket from the address bar and
exchanges it for an HttpOnly session cookie. No DeepCode account is required.
A session lasts 12 hours; after expiration or service restart, run `deepcode web`
again. Sign out revokes that browser session without stopping admitted tasks.
The service binds to `127.0.0.1` only; use the printed host and port. Public
hosting and arbitrary reverse-proxy origins are not supported.

`--database PATH` selects an isolated service database; `--port 0` chooses an
available port when starting it. A running instance keeps its existing port.
To stop the service explicitly, use the [service commands](SERVICE_RUNTIME.md).
macOS login startup is a separate, optional OS setting.

Open a project by entering its directory **on the service machine**, then trust
it if appropriate. Create a thread, select a configured model and send a task.
Approvals, settings, code editing, diff review and the terminal use the existing
workspace UI. The browser's Reconnect action only reconnects its own socket.

## Reconnection and uncertain results

The browser retries connection failures with bounded backoff, and retries RPCs
only when the current service handshake explicitly permits it. It freezes the
original request parameters for retries and checks the newly negotiated policy
after reconnecting. Pending requests and frame sizes are bounded. Offline
terminal keystrokes are never queued for later execution.

| Operation | Recovery behavior |
| --- | --- |
| Declared reads | At most three request attempts; reconnect and read current state. |
| Keyed Turn start/enqueue/input/Steer | Reuse the original `messageId`; existing receipts prevent duplicate submission. An uncertain Steer receipt is not reinjected. |
| Goal/Automation creation, configuration writes, approvals, file writes, terminal creation/input and other unlisted mutations | No automatic resend. A lost response reports `RESULT_UNKNOWN`; inspect current state before deciding what to do next. Existing revision and approval CAS checks still apply. |
| Thread events | Recover from the contiguous cursor with a fixed replay cutoff; repair gaps without dropping unseen events. |
| Terminal output | Discover the existing terminal and read its bounded byte window. Refresh does not create a replacement terminal or replay its input. |

There is no new universal mutation ledger. An RPC response ID is not a durable
idempotency key. Successful reconnection does not prove that a timed-out write
failed. Service restart restores durable application state using the existing
recovery rules; it does not restore old OS terminal processes or promise to
resume an interrupted shell command. Terminal limits and receipt semantics are
specified in [SERVICE_RUNTIME.md](SERVICE_RUNTIME.md).

## Files and host capabilities

Browser uploads become files in the selected, trusted server workspace. Returned
server paths are attached to the task; browser-local paths are never submitted
as if the server could read them. Each selection accepts up to eight files,
10 MiB each. Transfers share four slots; upload quota checks are serialized. The
workspace upload budget is 64 MiB, including unfinished staging files. Uploads
use private staging files and are published only after the full body is written;
interruption cleans up the staging file. Crash leftovers count against the
budget. Remove unused `deepcode-upload-*` files and orphan
`.deepcode-upload-*.part` files from the workspace when no upload is active.
There is no automatic retention policy for these workspace files.

Files can be downloaded from the inspector with authenticated, workspace-confined
requests, capped at 32 MiB. Symlinks escaping the workspace and non-regular files
are rejected. Browser diagnostics export the existing sanitized snapshot as a
download. Copy configuration path copies only the service path; it does not
export credentials. Software updates remain an installation/service operation.
The browser cannot invoke native dialogs, open host applications or run the
Desktop updater.

`ClientRuntime` is the shared interface. Separate entrypoints inject
`BrowserRuntime` or `TauriDesktopRuntime`; shared components do not import Tauri
APIs. Desktop retains native dialogs and its updater, and uses a native stdio
relay to attach to the same service as Web and TUI. Exiting any of these clients
detaches its connection without shutting down the service.

## Build and distribution

For a source checkout with Git, uv, and Node.js 22+ installed, run from the
repository root:

```sh
npm --prefix desktop ci
npm --prefix desktop run build:web
uv tool install --python 3.12 --force .
deepcode web
```

See [installation](../README.md#install-the-runtime) for the full setup and
[Troubleshooting](guide/troubleshooting.md) for startup or authentication errors.

The build writes `app_server/web_assets`, including a version/build manifest.
The service serves those assets and its API from the same origin. Missing or
incompatible assets produce an installation repair message. The browser checks
its compiled build identity against the service handshake before business use.
After changing frontend source, rebuild and reinstall the tool. Let active
work finish, restart the service, and open a fresh `deepcode web` link to load
the matching assets. Do not replace assets during an active acceptance test.

Python wheel and sdist releases include these generated assets. Release CI builds
them before packaging, and `scripts/verify_python_distribution.py` checks the
manifest and referenced files. Ordinary installed usage needs neither Node nor
Vite. The `[server]` extra is supported; its runtime dependencies are already in
the base package.

For release engineering, verify that the bundled App Server also includes the assets:

```sh
cd desktop
npm run setup:sidecar
npm run build:sidecar
build/sidecar/dist/deepcode-app-server/deepcode-app-server --web
```

The executable's `--web` launcher and `--serve` mode run without a system Python
interpreter. Its default stdio relay attaches to the shared service; closing
that relay disconnects the client without stopping the service. `--verify-runtime`
reports `webAssets` along with the existing provider, document and Skill probes.
Build verification requires bundled Web assets to be present.

## Reproducible acceptance

```sh
cd desktop
npm run test
npm run lint
npx playwright install chromium
npm run test:web
```

`test:web` runs a real browser against an isolated local service and temporary
workspace, using a deterministic Agent fixture. It covers an approval across
refresh/reconnection, settings persistence, upload, diff, file download and a
real terminal across refresh. It cleans up its service and workspace. CI uses
this credential-free scenario.

Set `DEEPCODE_TEST_PYTHON` to an installed test interpreter when not using the
repository `.venv`. `DEEPCODE_CHROME_PATH` can select a local Chrome binary.
Set `DEEPCODE_WEB_LIVE=1` only for an intentional live-provider test: the fixture
copies the selected usable connection into a private temporary configuration,
requests two small Python files, approves only those file writes and a constrained
unit-test command, refreshes during execution, independently tests the result,
and checks that only one Turn was admitted. This consumes the configured model's
API quota. Code and test evidence are attached to the Playwright JSON report.

`DEEPCODE_WEB_PACKAGE_DIR` selects an installed wheel target outside the checkout;
`DEEPCODE_WEB_BINARY` selects the standalone executable. Those modes run the
browser engineering checks without a model call. These browser scenarios do not establish platform-wide release acceptance.
Native Desktop packaging, operating-system service integration, and extended
stability checks require their own validation; see the
[upgrade and acceptance guide](UPGRADE_AND_RESTORE.md#stability-acceptance).
