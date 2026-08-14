"""Instance model + a self-contained local benchmark (no Docker, no network).

An :class:`Instance` carries the SWE-bench fields the harness needs: the repo
state (``base_commit``), the issue text handed to the agent
(``problem_statement``), and the tests that decide resolution
(``fail_to_pass`` must flip to passing, ``pass_to_pass`` must stay passing).

The official Verified set streams these from HuggingFace (see
:mod:`eval.swebench.dataset`) and is scored in Docker. For fast, dependency-
free iteration this module also builds a small local benchmark: real,
self-contained Python repos, each with exactly one genuine bug plus an
unrelated already-correct function, mirroring SWE-bench's "fix the bug
without breaking the neighbours" structure. The specs are a declarative
table; :func:`load_local_instances` materialises each as a git repo whose
``base_commit`` is the buggy state.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_GIT_ENV_ID = (
    "-c",
    "user.name=DeepCode Eval",
    "-c",
    "user.email=eval@deepcode.local",
)


@dataclass(frozen=True)
class Instance:
    """One benchmark task, aligned with the SWE-bench schema."""

    instance_id: str
    problem_statement: str
    base_commit: str = ""
    repo: str = ""  # "owner/name" for official instances; "" for local
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)
    test_cmd: str = "python -m pytest -q"
    # Local-mode only: a prepared git repo checked out at base_commit.
    repo_path: str | None = None


# Declarative local benchmark. Each spec: the buggy file(s), the test file,
# the issue text, and which tests must flip / stay green.
_LOCAL_SPECS: tuple[dict, ...] = (
    {
        "instance_id": "local__mathlib-inclusive-sum",
        "problem_statement": (
            "sum_inclusive(a, b) in mathlib.py should sum the integers from a "
            "to b **inclusive**, but it currently stops at b - 1. For example "
            "sum_inclusive(1, 5) returns 10 instead of the expected 15. Fix it "
            "so the upper bound is included. Do not change factorial()."
        ),
        "files": {
            "mathlib.py": (
                "def sum_inclusive(a, b):\n"
                '    """Sum the integers from a to b, inclusive."""\n'
                "    total = 0\n"
                "    for i in range(a, b):  # bug: excludes b\n"
                "        total += i\n"
                "    return total\n"
                "\n"
                "\n"
                "def factorial(n):\n"
                "    result = 1\n"
                "    for i in range(2, n + 1):\n"
                "        result *= i\n"
                "    return result\n"
            ),
            "test_mathlib.py": (
                "from mathlib import sum_inclusive, factorial\n"
                "\n"
                "\n"
                "def test_factorial():\n"
                "    assert factorial(5) == 120\n"
                "\n"
                "\n"
                "def test_sum_inclusive():\n"
                "    assert sum_inclusive(1, 5) == 15\n"
            ),
        },
        "fail_to_pass": ["test_mathlib.py::test_sum_inclusive"],
        "pass_to_pass": ["test_mathlib.py::test_factorial"],
        "test_cmd": "python -m pytest -q test_mathlib.py",
    },
    {
        "instance_id": "local__stringutil-palindrome-case",
        "problem_statement": (
            "is_palindrome(s) in stringutil.py is documented to ignore case, "
            "so is_palindrome('Level') should be True, but it returns False "
            "because the comparison is case-sensitive. Fix is_palindrome to "
            "ignore case. Leave reverse() untouched."
        ),
        "files": {
            "stringutil.py": (
                "def is_palindrome(s):\n"
                '    """True if s reads the same both ways, ignoring case."""\n'
                "    return s == s[::-1]  # bug: case-sensitive\n"
                "\n"
                "\n"
                "def reverse(s):\n"
                "    return s[::-1]\n"
            ),
            "test_stringutil.py": (
                "from stringutil import is_palindrome, reverse\n"
                "\n"
                "\n"
                "def test_reverse():\n"
                '    assert reverse("abc") == "cba"\n'
                "\n"
                "\n"
                "def test_palindrome_ignores_case():\n"
                '    assert is_palindrome("Level") is True\n'
            ),
        },
        "fail_to_pass": ["test_stringutil.py::test_palindrome_ignores_case"],
        "pass_to_pass": ["test_stringutil.py::test_reverse"],
        "test_cmd": "python -m pytest -q test_stringutil.py",
    },
    {
        "instance_id": "local__listutil-empty-guard",
        "problem_statement": (
            "first_or_none(xs) in listutil.py should return None for an empty "
            "list, but it raises IndexError because it indexes xs[0] "
            "unconditionally. Add the empty-list guard. Do not change length()."
        ),
        "files": {
            "listutil.py": (
                "def first_or_none(xs):\n"
                '    """Return the first element, or None if xs is empty."""\n'
                "    return xs[0]  # bug: IndexError on []\n"
                "\n"
                "\n"
                "def length(xs):\n"
                "    n = 0\n"
                "    for _ in xs:\n"
                "        n += 1\n"
                "    return n\n"
            ),
            "test_listutil.py": (
                "from listutil import first_or_none, length\n"
                "\n"
                "\n"
                "def test_length():\n"
                "    assert length([1, 2, 3]) == 3\n"
                "\n"
                "\n"
                "def test_first_or_none_empty():\n"
                "    assert first_or_none([]) is None\n"
            ),
        },
        "fail_to_pass": ["test_listutil.py::test_first_or_none_empty"],
        "pass_to_pass": ["test_listutil.py::test_length"],
        "test_cmd": "python -m pytest -q test_listutil.py",
    },
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )


def _materialize(spec: dict, dest: Path) -> Instance:
    """Write a spec's files, git-init, and commit the buggy base state."""
    dest.mkdir(parents=True, exist_ok=True)
    for rel, content in spec["files"].items():
        (dest / rel).write_text(content, encoding="utf-8")
    _git(dest, "init", "-q")
    _git(dest, *_GIT_ENV_ID, "add", "-A")
    _git(dest, *_GIT_ENV_ID, "commit", "-q", "-m", "base: buggy state")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(dest),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return Instance(
        instance_id=spec["instance_id"],
        problem_statement=spec["problem_statement"],
        base_commit=head,
        fail_to_pass=list(spec["fail_to_pass"]),
        pass_to_pass=list(spec["pass_to_pass"]),
        test_cmd=spec["test_cmd"],
        repo_path=str(dest),
    )


def load_local_instances(root: str | Path) -> list[Instance]:
    """Materialise the built-in local benchmark under ``root``.

    Returns one :class:`Instance` per spec, each a git repo checked out at its
    buggy ``base_commit``.
    """
    root = Path(root)
    return [_materialize(spec, root / spec["instance_id"]) for spec in _LOCAL_SPECS]


def local_instance_ids() -> list[str]:
    return [spec["instance_id"] for spec in _LOCAL_SPECS]
