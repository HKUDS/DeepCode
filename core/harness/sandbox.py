"""Platform sandbox command wrapping (mechanism only).

Given a shell command and a :class:`SandboxPolicy`, produce an equivalent
command that runs confined to a set of writable roots. Two backends:

* **macOS** — ``sandbox-exec`` (seatbelt) with a generated ``.sbpl`` policy
  that is **closed-by-default** (``(deny default)``, Chrome-inspired, C4b):
  it denies everything, then re-grants only the narrow set a build needs
  (process spawn, a curated sysctl allowlist, IPC for Python multiproc /
  OpenMP, PTYs, user prefs), plus broad reads and writes confined to the
  policy's roots (and ``/tmp``). The binary is referenced by its absolute
  path (``/usr/bin/sandbox-exec``) to defend against PATH injection.
* **Linux** — ``bwrap`` (bubblewrap): mount-namespace isolation — bind the
  filesystem read-only, bind writable roots read-write, fresh ``/tmp``,
  ``--unshare-net`` for no network.

Honest boundary: the reference agent adds seccomp-bpf + Landlock on Linux;
those are kernel facilities Python cannot install from userspace, so DeepCode
relies on bubblewrap's namespaces (a real, standard isolation primitive) rather
than faking an equivalent. Native Windows has no backend.

If no backend is available (native Windows, or the tool isn't installed)
:func:`sandbox_backend` returns ``"none"`` and :func:`wrap_shell_command`
returns the command unchanged — callers must decide whether to degrade to
approval-first. This module never raises for an unsupported platform; it
degrades.

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


# Closed-by-default (deny default) seatbelt base, aligned with the reference
# agent's Chrome-inspired policy (C4b hardening). Unlike an ``(allow default)``
# write-fence — which lets a command do anything except write outside its roots
# — this denies *everything* and re-grants only the narrow set a build actually
# needs (process spawn, a curated sysctl allowlist, IPC for Python multiproc/
# OpenMP, PTYs, user prefs). File reads, workspace writes, and (optional)
# network are layered on top in ``_seatbelt_profile``.
_SEATBELT_BASE_POLICY = r"""(version 1)

; Chrome-inspired closed-by-default sandbox policy.

; start with closed-by-default
(deny default)

; child processes inherit the policy of their parent
(allow process-exec)
(allow process-fork)
(allow signal (target same-sandbox))

; process-info
(allow process-info* (target same-sandbox))

(allow file-write-data
  (require-all
    (path "/dev/null")
    (vnode-type CHARACTER-DEVICE)))

; sysctls permitted.
(allow sysctl-read
  (sysctl-name "hw.activecpu")
  (sysctl-name "hw.busfrequency_compat")
  (sysctl-name "hw.byteorder")
  (sysctl-name "hw.cacheconfig")
  (sysctl-name "hw.cachelinesize_compat")
  (sysctl-name "hw.cpufamily")
  (sysctl-name "hw.cpufrequency_compat")
  (sysctl-name "hw.cputype")
  (sysctl-name "hw.l1dcachesize_compat")
  (sysctl-name "hw.l1icachesize_compat")
  (sysctl-name "hw.l2cachesize_compat")
  (sysctl-name "hw.l3cachesize_compat")
  (sysctl-name "hw.logicalcpu_max")
  (sysctl-name "hw.machine")
  (sysctl-name "hw.model")
  (sysctl-name "hw.memsize")
  (sysctl-name "hw.ncpu")
  (sysctl-name "hw.nperflevels")
  ; CPU feature detection; fingerprinting is not a concern here.
  (sysctl-name-prefix "hw.optional.arm.")
  (sysctl-name-prefix "hw.optional.armv8_")
  (sysctl-name "hw.packages")
  (sysctl-name "hw.pagesize_compat")
  (sysctl-name "hw.pagesize")
  (sysctl-name "hw.physicalcpu")
  (sysctl-name "hw.physicalcpu_max")
  (sysctl-name "hw.logicalcpu")
  (sysctl-name "hw.cpufrequency")
  (sysctl-name "hw.tbfrequency_compat")
  (sysctl-name "hw.vectorunit")
  (sysctl-name "machdep.cpu.brand_string")
  (sysctl-name "kern.argmax")
  (sysctl-name "kern.hostname")
  (sysctl-name "kern.maxfilesperproc")
  (sysctl-name "kern.maxproc")
  (sysctl-name "kern.osproductversion")
  (sysctl-name "kern.osrelease")
  (sysctl-name "kern.ostype")
  (sysctl-name "kern.osvariant_status")
  (sysctl-name "kern.osversion")
  (sysctl-name "kern.secure_kernel")
  (sysctl-name "kern.usrstack64")
  (sysctl-name "kern.version")
  (sysctl-name "sysctl.proc_cputype")
  (sysctl-name "vm.loadavg")
  (sysctl-name-prefix "hw.perflevel")
  (sysctl-name-prefix "kern.proc.pgrp.")
  (sysctl-name-prefix "kern.proc.pid.")
  (sysctl-name-prefix "net.routetable.")
)

; Allow Java to read some CPU info (misclassified as a "write").
(allow sysctl-write
  (sysctl-name "kern.grade_cputype"))

; IOKit
(allow iokit-open
  (iokit-registry-entry-class "RootDomainUserClient")
)

; needed to look up user info
(allow mach-lookup
  (global-name "com.apple.system.opendirectoryd.libinfo")
)

; Needed for Python multiprocessing on macOS for the SemLock
(allow ipc-posix-sem)

; Needed for PyTorch/libomp on macOS to register OpenMP runtimes.
(allow ipc-posix-shm-read-data
  ipc-posix-shm-write-create
  ipc-posix-shm-write-unlink
  (ipc-posix-name-regex #"^/__KMP_REGISTERED_LIB_[0-9]+$"))

(allow mach-lookup
  (global-name "com.apple.PowerManagement.control")
)

; allow openpty()
(allow pseudo-tty)
(allow file-read* file-write* file-ioctl (literal "/dev/ptmx"))
(allow file-read* file-write*
  (require-all
    (regex #"^/dev/ttys[0-9]+")
    (extension "com.apple.sandbox.pty")))
(allow file-ioctl (regex #"^/dev/ttys[0-9]+"))

; allow readonly user preferences
(allow ipc-posix-shm-read* (ipc-posix-name-prefix "apple.cfprefs."))
(allow mach-lookup
  (global-name "com.apple.cfprefsd.daemon")
  (global-name "com.apple.cfprefsd.agent")
  (local-name "com.apple.cfprefsd.agent"))
(allow user-preference-read)
"""

# Appended only when the policy permits network. Re-grants outbound/inbound
# plus the DNS/TLS platform lookups a real connection needs under deny-default.
_SEATBELT_NETWORK_POLICY = r"""
; network (only when allow_network)
(allow network-outbound)
(allow network-inbound)
(allow system-socket)
(allow mach-lookup
  (global-name "com.apple.SystemConfiguration.DNSConfiguration")
  (global-name "com.apple.SystemConfiguration.configd")
  (global-name "com.apple.SecurityServer")
  (global-name "com.apple.networkd")
  (global-name "com.apple.ocspd")
  (global-name "com.apple.trustd.agent")
  (global-name "com.apple.bsd.dirhelper")
  (global-name "com.apple.system.opendirectoryd.membership"))
"""


def _seatbelt_profile(policy: SandboxPolicy) -> str:
    """Deny-by-default seatbelt (.sbpl): the curated base + broad reads + writes
    to ephemeral/device areas and the policy's roots + optional network."""
    parts = [
        _SEATBELT_BASE_POLICY.rstrip(),
        "",
        "; broad read-only filesystem access (agents read headers, deps, etc.)",
        "(allow file-read*)",
        "",
        "; writable: ephemeral / device areas a build needs",
        '(allow file-write* (subpath "/tmp"))',
        '(allow file-write* (subpath "/private/tmp"))',
        '(allow file-write* (subpath "/private/var/folders"))',
        '(allow file-write* (literal "/dev/null"))',
        '(allow file-write* (literal "/dev/stdout"))',
        '(allow file-write* (literal "/dev/stderr"))',
        '(allow file-write-data (regex #"^/dev/tty"))',
        "",
        "; writable: the policy's workspace roots",
    ]
    for root in policy.normalized_roots():
        escaped = root.replace("\\", "\\\\").replace('"', '\\"')
        parts.append(f'(allow file-write* (subpath "{escaped}"))')
    if policy.allow_network:
        parts.append(_SEATBELT_NETWORK_POLICY.rstrip())
    return "\n".join(parts) + "\n"


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
