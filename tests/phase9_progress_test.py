from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflows.agents.memory_agent_concise import ConciseMemoryAgent  # noqa: E402
from workflows.implementation_llm_runtime import call_provider_with_legacy_tools  # noqa: E402
from utils.loop_detector import ProgressTracker  # noqa: E402


def make_memory_agent(tmp_path: Path) -> ConciseMemoryAgent:
    code_dir = tmp_path / "generate_code"
    code_dir.mkdir()
    agent = ConciseMemoryAgent(
        "",
        target_directory=str(tmp_path),
        code_directory=str(code_dir),
    )
    agent.all_files_list = [
        "src/foo.py",
        "tests/foo.py",
        "src/bar.py",
    ]
    return agent


def test_normalize_file_path_relative_to_code_directory(tmp_path: Path):
    agent = make_memory_agent(tmp_path)
    full_path = tmp_path / "generate_code" / "src" / "foo.py"

    assert agent.normalize_file_path(str(full_path)) == "src/foo.py"
    assert agent.normalize_file_path(r".\generate_code\src\foo.py") == "src/foo.py"


def test_unimplemented_files_use_exact_path_before_suffix(tmp_path: Path):
    agent = make_memory_agent(tmp_path)

    agent.record_file_implementation("src/foo.py")

    assert agent.get_unimplemented_files() == ["tests/foo.py", "src/bar.py"]


def test_suffix_match_requires_unique_planned_candidate(tmp_path: Path):
    agent = make_memory_agent(tmp_path)

    agent.record_file_implementation("foo.py")

    assert agent.get_unimplemented_files() == [
        "src/foo.py",
        "tests/foo.py",
        "src/bar.py",
    ]


def test_progress_tracker_counts_unique_files_only():
    tracker = ProgressTracker(total_files=2)

    assert tracker.complete_file("src/foo.py") is True
    assert tracker.complete_file(r"src\foo.py") is False
    assert tracker.complete_file("src/bar.py") is True

    info = tracker.get_progress_info()
    assert info["files_completed"] == 2
    assert info["total_files"] == 2
    assert info["file_progress"] == 100


class AlwaysFailProvider:
    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs):
        on_retry_wait = kwargs.get("on_retry_wait")
        if on_retry_wait:
            await on_retry_wait("attempt 1 failed; retrying")
        return SimpleNamespace(
            finish_reason="error",
            content="provider exhausted retry budget",
            usage={},
            tool_calls=[],
        )


@pytest.mark.asyncio
async def test_implementation_runtime_surfaces_bounded_retry_failure():
    provider = AlwaysFailProvider()
    retry_messages: list[str] = []

    async def on_retry_wait(message: str):
        retry_messages.append(message)

    with pytest.raises(RuntimeError, match="provider exhausted retry budget"):
        await call_provider_with_legacy_tools(
            provider,
            system_message="system",
            messages=[{"role": "user", "content": "implement"}],
            tools=[],
            max_tokens=128,
            validate_messages=lambda messages: messages,
            retry_mode="standard",
            on_retry_wait=on_retry_wait,
        )

    assert retry_messages == ["attempt 1 failed; retrying"]
