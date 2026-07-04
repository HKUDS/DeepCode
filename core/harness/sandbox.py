"""Platform sandbox command wrapping (mechanism only).

Given a shell command and a :class:`SandboxPolicy`, produce an equivalent
command that runs confined to a set of writable roots. Two backends:

* **macOS** — ``sandbox-exec`` (seatbelt) with a generated ``.sbpl`` policy
  that allows reads everywhere but restricts writes to the policy's roots
  (plus ``/tmp`` and friends). The binary is referenced by its absolute
  path (``/usr/bin/sandbox-exec``) to defend against PATH injection.
* **Linux** — ``bwrap`` (bubblewrap): bind the filesystem read-only, then
  bind writable roots read-write, with a fresh ``/tmp``.

If no backend is available (e.g. native Windows, or the tool isn't
installed) :func:`sandbox_backend` returns ``"none"`` and
:func:`wrap_shell_command` returns the command unchanged — callers must
decide whether to degrade to approval-first (see the workflow wiring). This
module never raises for an unsupported platform; it degrades.

Network is denied by default (``allow_network=False``) on both backends.
"""

from __future__ import annotations

import os
import platform
import shutil
import tempfile
from dataclasses import dataclass, field

# Absolute path — never resolved via PATH (PATH-injection defense).
_MACOS_SANDBOX_EXEC = "/usr/bin/sandbox-exec"


@dataclass(frozen=True)
class SandboxPolicy:
    """What a sandboxed command may write to and whether it has network.

    ``writable_roots`` are absolute directories the command may modify;
    everything else on disk is read-only. Reads are unrestricted (agents
    routinely need to read system headers, site-packages, etc.).
    """

    writable_roots: tuple[str, ...] = ()
    allow_network: bool = False

    @classmethod
    def for_workspace(
        cls, workspace: str | os.PathLike[str], *, allow_network: bool = False
    ) -> "SandboxPolicy":
        root = os.path.abspath(str(workspace))
        return cls(writable_roots=(root,), allow_network=allow_network)

    def normalized_roots(self) -> list[str]:
        return [os.path.abspath(r) for r in self.writable_roots if r]


def sandbox_backend() -> str:
    """Return the active backend: ``"seatbelt"`` | ``"bwrap"`` | ``"none"``."""
    system = platform.system()
    if system == "Darwin" and os.path.exists(_MACOS_SANDBOX_EXEC):
        return "seatbelt"
    if system == "Linux" and shutil.which("bwrap"):
        return "bwrap"
    return "none"


def _seatbelt_profile(policy: SandboxPolicy) -> str:
    """Generate a seatbelt (.sbpl) policy: read-all, write-roots-only."""
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        # Always allow writes to the ephemeral / device areas a build needs.
        '(allow file-write* (subpath "/tmp"))',
        '(allow file-write* (subpath "/private/tmp"))',
        '(allow file-write* (subpath "/private/var/folders"))',
        '(allow file-write* (literal "/dev/null"))',
        '(allow file-write* (literal "/dev/stdout"))',
        '(allow file-write* (literal "/dev/stderr"))',
        '(allow file-write-data (regex #"^/dev/tty"))',
    ]
    for root in policy.normalized_roots():
        escaped = root.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'(allow file-write* (subpath "{escaped}"))')
    if not policy.allow_network:
        lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def _write_seatbelt_profile(policy: SandboxPolicy) -> str:
    """Write the profile to a temp file and return its path (caller cleans up)."""
    fd, path = tempfile.mkstemp(prefix="deepcode-sb-", suffix=".sbpl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_seatbelt_profile(policy))
    except Exception:
        os.unlink(path)
        raise
    return path


@dataclass
class WrappedCommand:
    """A sandboxed command plus any temp artifact to clean up after run."""

    argv: list[str]
    cleanup_path: str | None = None
    backend: str = "none"
    extra_env: dict[str, str] = field(default_factory=dict)

    def cleanup(self) -> None:
        if self.cleanup_path and os.path.exists(self.cleanup_path):
            try:
                os.unlink(self.cleanup_path)
            except OSError:
                pass


def wrap_argv_command(
    inner_argv: list[str],
    policy: SandboxPolicy,
) -> WrappedCommand:
    """Wrap an already-tokenized command (argv list) inside the sandbox.

    Use this for commands that aren't a shell string — e.g. running an
    interpreter on a script file (``[sys.executable, "/tmp/x.py"]``). When
    no backend is available the inner argv is returned unchanged with
    ``backend="none"`` (caller degrades safely).
    """
    backend = sandbox_backend()

    if backend == "seatbelt":
        profile_path = _write_seatbelt_profile(policy)
        argv = [_MACOS_SANDBOX_EXEC, "-f", profile_path, *inner_argv]
        return WrappedCommand(argv=argv, cleanup_path=profile_path, backend=backend)

    if backend == "bwrap":
        argv = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
        argv += ["--tmpfs", "/tmp"]
        for root in policy.normalized_roots():
            if os.path.exists(root):
                argv += ["--bind", root, root]
        if not policy.allow_network:
            argv += ["--unshare-net"]
        argv += ["--die-with-parent", *inner_argv]
        return WrappedCommand(argv=argv, backend=backend)

    return WrappedCommand(argv=list(inner_argv), backend="none")


def wrap_shell_command(
    command: str,
    policy: SandboxPolicy,
    *,
    shell: str = "/bin/bash",
) -> WrappedCommand:
    """Wrap a shell ``command`` string so it runs inside the sandbox.

    Returns a :class:`WrappedCommand`. When no backend is available the
    argv falls back to ``[shell, -c, command]`` with ``backend="none"`` —
    the caller is responsible for degrading safely (e.g. requiring approval
    or refusing network-touching commands).
    """
    return wrap_argv_command([shell, "-c", command], policy)


def sandbox_available() -> bool:
    return sandbox_backend() != "none"


def sandbox_enabled() -> bool:
    """Whether command sandboxing is turned on (env-gated, default ON).

    ``DEEPCODE_SANDBOX`` accepts ``0``/``false``/``off``/``no`` to disable;
    anything else (incl. unset) means enabled. Disabling is an escape hatch
    for environments where the sandbox misbehaves — it does not affect the
    permission engine's file-tool denylist, which is always active.
    """
    raw = os.environ.get("DEEPCODE_SANDBOX", "").strip().lower()
    return raw not in ("0", "false", "off", "no")


def build_exec_command(
    *,
    command: str | None = None,
    argv: list[str] | None = None,
    workspace: str | os.PathLike[str],
    allow_network: bool = True,
    shell: str = "/bin/bash",
) -> WrappedCommand:
    """Build the (possibly sandboxed) command a tool executor should run.

    Exactly one of ``command`` (shell string) or ``argv`` (token list) must
    be given. Applies the workspace write-fence when sandboxing is enabled
    and a backend is available; otherwise returns the bare command flagged
    ``backend="none"``/``"disabled"`` so the caller can log the degradation.

    ``allow_network`` defaults to ``True``: coding tasks routinely need pip /
    downloads, so the executor keeps the filesystem write-fence (the high-
    value, low-breakage protection) while leaving the network open. Callers
    that want strict isolation pass ``allow_network=False``.
    """
    if (command is None) == (argv is None):
        raise ValueError("provide exactly one of command= or argv=")

    if not sandbox_enabled():
        bare = [shell, "-c", command] if command is not None else list(argv or [])
        return WrappedCommand(argv=bare, backend="disabled")

    policy = SandboxPolicy.for_workspace(workspace, allow_network=allow_network)
    if command is not None:
        return wrap_shell_command(command, policy, shell=shell)
    return wrap_argv_command(list(argv or []), policy)


def describe_backend() -> str:
    """Human-readable one-liner for logs/prompts."""
    backend = sandbox_backend()
    return {
        "seatbelt": "macOS seatbelt (sandbox-exec)",
        "bwrap": "Linux bubblewrap (bwrap)",
        "none": "no sandbox (degraded: approval-first)",
    }[backend]
