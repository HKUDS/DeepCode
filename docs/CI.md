# Working with CI

CI checks the complete pull request from its merge base, so unrelated changes
added to the target branch do not count as changes in the PR. A README update
inside a PR that also changes runtime code still receives the runtime checks.
A PR containing only top-level Markdown files, `docs/`, `assets/`, `website/`,
or issue templates skips runtime tests and builds, while formatting, secret
scanning, and dependency review remain active. Unknown paths and missing
comparison history run checks.

Pushes to `main` run the Python suite on 3.13 only; pull requests and manual
runs cover 3.12–3.14. Desktop pushes and PRs run the quality gates; the four
platform bundles build weekly and on demand (`workflow_dispatch`). The
dependency and license audit consults live advisory databases, so it also runs
weekly and on demand rather than on every push; a failure opens or updates the
issue "Dependency audit failed on main" instead of marking the push red. It
audits only what ships to users (the installed package, the App Server
environment, Desktop production npm packages, and Rust crates), not build or
test tools.

The classification lives in `scripts/ci_scope.py`, used through
`.github/actions/ci-scope`. Runtime test jobs keep their existing check names
when their heavy steps are unnecessary, so required checks do not wait for a
workflow that never started. Desktop bundle jobs retain their existing scope.

## What a passing check means

| Check | What it verifies |
|---|---|
| Python 3.13 (push) / 3.12–3.14 (PR) | The full backend suite on Ubuntu 24.04 |
| Windows lifecycle | Real file locks, ACLs, recovery, background service operations, and discovery races |
| Browser | Chromium interactions with the real local service and a deterministic test Agent |
| Python package | Distribution metadata, packaged resources, and installation in a clean environment |
| Desktop quality | Frontend tests, types, protocol consistency, Rust formatting, lint, and tests |
| Four platform bundles (weekly / manual) | Build artifacts, packaged runtime startup, resources, and platform package checks |
| Secret scan and dependency review | Secret history on every push and PR; dependency review on PRs |
| Dependency audit (weekly / manual) | Dependency vulnerabilities and licenses; failures are reported as an issue |

Browser CI does not call a paid model. Live-provider, native GUI, and actual
OS login/reboot acceptance remain separate from these regression checks.
Functional startup tests use the launcher's readiness policy. Their timeouts
are test budgets, not published startup-performance guarantees.

## Reproduce the Python test environment

From the repository root, create and activate a Python 3.12, 3.13, or 3.14
virtual environment. Then run:

```sh
python -m pip install -r scripts/ci/requirements.lock
python -m pip install --no-deps --no-build-isolation -e '.[test]'
python -m pip check
python -m pytest -q --durations=10
pre-commit run --all-files
```

The lock contains the package's runtime/test dependencies and CI tools. Runtime
versions shared with Desktop follow `desktop/sidecar-requirements.lock`.
`--no-deps` prevents editable installation from silently adding unlocked runtime
dependencies; `pip check` reports missing or incompatible requirements.

After changing package requirements, test extras, or the sidecar lock, regenerate
the CI lock with `uv` and commit the result:

```sh
uv pip compile scripts/ci/requirements.in --python-version 3.12 --universal --no-emit-package deepcode-hku --output-file scripts/ci/requirements.lock
```

Add `--upgrade` when deliberately refreshing all compatible dependency pins.
Run tests and the security checks after refreshing. The security workflow audits
the packaged runtime, not the CI-only tools. Package installation and the live
package dependency audit still resolve supported dependency ranges, so the fixed
test baseline does not replace installation compatibility checks.

## Reproduce the secret, browser, and Windows lifecycle checks

The secret history scan needs only the pinned Gitleaks binary; the release
URL and checksum live in `.github/workflows/security-ci.yml`:

```sh
gitleaks git --redact --no-banner --exit-code 1
```

Run it from the repository root, where gitleaks applies its built-in rules
and the repository's `.gitleaksignore` exactly as CI does — the workflow
passes no custom config. CI scans the full history; `--log-opts=-n 200`
restricts a local pass to the latest 200 commits, and a clean subset does not
replace the full scan before pushing.

The browser check runs on Ubuntu 24.04 with Node 22. Create the Python test
environment above, then run from `desktop/`:

```sh
npm ci
npx playwright install --with-deps chromium
DEEPCODE_TEST_PYTHON=python npm run test:web
```

`test:web` first rebuilds the web client (`check:protocol`, `tsc --noEmit`,
and the web Vite build) and then runs the Playwright suite, which starts the
local service itself. `DEEPCODE_TEST_PYTHON` names the Python that starts it
and defaults to the repository's `.venv`.

The Windows lifecycle job is the only check that executes the NTFS ACL and
Job Object suites; the Ubuntu jobs can only skip them. On Windows, with the
Python test environment active, run its two groups (CI additionally writes
`--junitxml` reports):

```sh
python -m pytest -v --durations=10 \
  tests/application/test_application_lease.py \
  tests/application/test_automation_scheduler_leadership.py \
  tests/application/test_session_deletion_service.py \
  tests/application/test_execution_coordinator.py

python -m pytest -v --durations=10 \
  tests/test_private_storage_windows.py \
  tests/test_harness_sandbox.py \
  tests/test_exec_sandbox_wiring.py \
  tests/app_server/test_windows_task.py \
  tests/app_server/test_state_backup.py \
  tests/test_provider_oauth.py \
  tests/test_provider_protocols.py \
  tests/app_server/test_service.py \
  tests/app_server/test_service_discovery.py
```

The first group covers shared leases, startup recovery, and scheduler
leadership; the second holds the Windows-gated cases and is the only place
they execute.

## Diagnose a failure

Inspect the earliest failing step. Python and Windows jobs upload JUnit reports
with test names and durations; browser jobs retain failure traces and screenshots.
Python logs name each test as it runs. If one test takes longer than 60 seconds,
pytest prints all Python thread stacks; this is diagnostic output, not an extended
deadline or a successful result. Unhandled background-thread exceptions and
unraisable exceptions fail the test instead of appearing only as warnings.
Packaged startup errors include worker output. A failure while stopping the test
service is attached to the original exception, and temporary cleanup is still
attempted. A cleanup failure on its own also fails the check.

PR updates cancel superseded runs. Rust dependency and pre-commit caches reduce
repeated setup; caches do not substitute for tests or package validation. A cold
cache must produce the same verdict as a warm cache. Failed tests are not
automatically retried until they turn green.

## Keep concurrency tests independent of machine speed

Submitting a Turn or observing an Automation Run as `RUNNING` does not guarantee
that the Agent has started consuming its input. Before interrupting a scripted
Agent whose next step depends on consuming the current step, wait for an explicit
signal from that Agent. Assert execution counts after the relevant work settles.
Do not use a fixed sleep as evidence that work started or finished.

The automation Goal-run suite runs every scenario with immediate and deferred
Agent dispatch. The deferred variant deliberately delays Agent entry to expose
assumptions about thread scheduling; it does not retry a failed test. Both variants
must pass in each Python version. When a race is found, first reproduce the adverse
ordering, then fix the synchronization and retain coverage for that ordering.
