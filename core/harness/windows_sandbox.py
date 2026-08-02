"""Windows Job Object sandbox launcher.

This module is executed *as a subprocess wrapper* on native Windows when
:func:`core.harness.sandbox.sandbox_backend` reports ``"job"``.  It mirrors
what ``/usr/bin/sandbox-exec`` does on macOS and ``bwrap`` does on Linux:

    python -m core.harness.windows_sandbox -- <inner argv...>

The launcher:

1. Creates a Windows Job Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``
   so that when this wrapper exits (or the parent DeepCode process dies) the
   whole child process tree is terminated — no orphaned shells/compilers
   survive.
2. Spawns the inner command *suspended* via ``CreateProcessW``, assigns it to
   the job, then resumes it.  Every process the inner command spawns is
   automatically a member of the same job (Windows jobs are hierarchical), so
   the isolation applies to the entire tree, not just the first process.
3. Waits for the inner process and forwards its exit code.

Honest boundary: on Windows there is no userspace write-fence equivalent to
macOS seatbelt or Linux mount namespaces without disk quotas or an
AppContainer/SILO (both require extra privileges or signing).  This launcher
provides the *process-tree isolation and lifetime guarantee* — a real,
standard Windows isolation primitive — and callers that need write
restriction continue to require explicit approval on Windows.

Implemented with ``ctypes`` only (no third-party dependency); degrades to a
plain ``subprocess`` passthrough if any Win32 call fails.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import sys

# ── Job Object constants (winnt.h) ──────────────────────────────────────
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9

# Process creation flags.
_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_INHERIT_HANDLES = 0x00000001


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


def _win32() -> bool:
    return os.name == "nt"


def _run_in_job(argv: list[str]) -> int:
    """Run ``argv`` inside a KILL_ON_JOB_CLOSE job; return its exit code."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return _passthrough(argv)

    try:
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            job,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            return _passthrough(argv)
    except Exception:
        return _passthrough(argv)

    # Build the command line: quote each argument for the C runtime.
    command_line = " ".join(
        f'"{a}"' if (" " in a or "\t" in a) else a for a in argv
    )

    startup = _STARTUPINFOW()
    startup.cb = ctypes.sizeof(_STARTUPINFOW)
    process_info = _PROCESS_INFORMATION()

    ok = kernel32.CreateProcessW(
        None,  # lpApplicationName — resolved from command line
        command_line,
        None,  # process attributes
        None,  # thread attributes
        False,  # bInheritHandles
        _CREATE_SUSPENDED | _CREATE_UNICODE_ENVIRONMENT,
        None,  # environment (inherit)
        None,  # current directory (inherit)
        ctypes.byref(startup),
        ctypes.byref(process_info),
    )
    if not ok:
        return _passthrough(argv)

    try:
        assigned = kernel32.AssignProcessToJobObject(job, process_info.hProcess)
        # Resume regardless; if assignment failed we degrade to running the
        # child unmanaged rather than leaving it frozen.
        kernel32.ResumeThread(process_info.hThread)
        kernel32.CloseHandle(process_info.hThread)
        if not assigned:
            kernel32.WaitForSingleObject(process_info.hProcess, 0xFFFFFFFF)
        else:
            kernel32.WaitForSingleObject(process_info.hProcess, 0xFFFFFFFF)

        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(process_info.hProcess, ctypes.byref(exit_code))
        return int(exit_code.value)
    finally:
        kernel32.CloseHandle(process_info.hProcess)
        # Closing the job handle with KILL_ON_JOB_CLOSE set is a no-op while
        # processes are running; the guarantee applies when the wrapper's
        # process ends (including if it is killed).
        kernel32.CloseHandle(job)


def _passthrough(argv: list[str]) -> int:
    """Fallback: run without a job when Win32 calls fail."""
    try:
        proc = subprocess.run(argv, capture_output=False)
        return proc.returncode
    except OSError as exc:
        print(f"deepcode-windows-sandbox: failed to start: {exc}", file=sys.stderr)
        return 127


def main() -> int:
    if not _win32():
        print(
            "deepcode-windows-sandbox: only runs on Windows",
            file=sys.stderr,
        )
        return 2
    argv = sys.argv[1:]
    if not argv:
        print(
            "deepcode-windows-sandbox: usage: -m core.harness.windows_sandbox -- <cmd> [args...]",
            file=sys.stderr,
        )
        return 2
    if argv[0] == "--":
        argv = argv[1:]
    return _run_in_job(argv)


if __name__ == "__main__":
    raise SystemExit(main())
