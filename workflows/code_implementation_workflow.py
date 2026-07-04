"""
Paper Code Implementation Workflow — unified on the core AgentRunner kernel.

P0 unification (see DEEPCODE_V2_MASTER_PLAN.md):
- The former hand-rolled ``_pure_code_implementation_loop`` (800-iteration
  while loop with a bespoke text protocol) is gone. The implementation
  phase now runs on ``core.agent_runtime.runner.AgentRunner`` — the same
  kernel every other agent uses — with domain behavior injected through
  kernel seams (tool wrappers, ``AgentHook``, ``should_stop_callback``,
  ``injection_callback``).
- Tool schemas come from the MCP servers themselves (registered via the
  compat agent, re-exposed under their bare names). The hand-maintained
  copies in ``config/mcp_tool_definitions*.py`` are deleted.
- ``CodeImplementationWorkflowWithIndex`` is now just
  ``CodeImplementationWorkflow(enable_indexing=True)``: same class, same
  kernel, indexed prompt + minimal tool surface.

Domain strategy (unchanged, deliberately):
- ``ConciseMemoryAgent`` clean-slate memory: after each ``write_file``
  the history collapses to plan + knowledge base.
- ``CodeImplementationAgent`` keeps executing/tracking tool calls
  (read_file → read_code_mem interception, write_file summaries).
- Completion is mechanical: the run ends when every file parsed from the
  plan exists — never because the model claims it is done.
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.agent_runtime.hook import AgentHook, AgentHookContext
from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.tools.alias import AliasedTool, build_aliased_registry
from core.agent_runtime.tools.registry import ToolRegistry
from core.harness.approval import TerminalApprover
from core.harness.permissions import PermissionEngine, PermissionMode

# DeepCode-native compat layer (owns the MCP server lifecycle)
from core.compat import Agent
from core.llm_runtime import attach_workflow_llm, get_workflow_provider

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompts.code_prompts import (  # noqa: E402
    GENERAL_CODE_IMPLEMENTATION_SYSTEM_PROMPT,
    PURE_CODE_IMPLEMENTATION_SYSTEM_PROMPT_INDEX,
    STRUCTURE_GENERATOR_PROMPT,
)
from utils.llm_utils import get_default_models  # noqa: E402
from utils.loop_detector import LoopDetector, ProgressTracker  # noqa: E402
from workflows.agents import CodeImplementationAgent  # noqa: E402
from workflows.agents.memory_agent_concise import ConciseMemoryAgent  # noqa: E402

# Model-visible tool surfaces. These mirror the curated lists the deleted
# ``config/mcp_tool_definitions*.py`` used to hardcode — but the schemas
# now come from the MCP servers themselves (single source of truth).
_STANDARD_TOOL_NAMES = [
    "read_file",
    "read_multiple_files",
    "read_code_mem",
    "write_file",
    "write_multiple_files",
    "execute_python",
    "execute_bash",
    "get_file_structure",
    "search_code_references",
    "get_indexes_overview",
    "set_workspace",
]
_INDEXED_TOOL_NAMES = [
    "write_file",
    "search_code_references",
]
_READ_TOOL_NAMES = {"read_file", "read_code_mem"}

_MCP_SERVER_NAMES = [
    "code-implementation",
    "code-reference-indexer",
    "document-segmentation",
]

_MAX_ITERATIONS = 800
_MAX_WALL_SECONDS = 7200  # 120 minutes (2 hours)
_EMERGENCY_TRIM_THRESHOLD = 50
_MAX_TOOL_RESULT_CHARS = 60_000


def _resolve_permission_mode() -> PermissionMode:
    """Permission mode for the implementation phase (env-gated).

    ``DEEPCODE_PERMISSION_MODE`` = ``full_auto`` (default) / ``default`` /
    ``plan``. Unknown or empty values resolve to ``full_auto`` so a typo
    never silently blocks an unattended reproduction run.
    """
    import os

    raw = os.environ.get("DEEPCODE_PERMISSION_MODE", "").strip().lower()
    try:
        return PermissionMode(raw) if raw else PermissionMode.FULL_AUTO
    except ValueError:
        return PermissionMode.FULL_AUTO


@dataclass
class _RunState:
    """Shared mutable state between the tool wrappers, hook and callbacks."""

    memory_agent: ConciseMemoryAgent
    code_tracker: CodeImplementationAgent
    loop_detector: LoopDetector
    progress_tracker: ProgressTracker
    logger: logging.Logger
    system_prompt: str
    guidance: "_GuidanceTexts"
    progress_callback: Optional[Callable] = None
    start_time: float = field(default_factory=time.time)
    max_wall_seconds: float = _MAX_WALL_SECONDS
    iterations_done: int = 0
    in_tools_phase: bool = False
    last_finish_reason: Optional[str] = None
    abort_reason: Optional[str] = None
    # (status, reason) once a terminal condition is known.
    run_status: Optional[tuple] = None

    def emit_progress(self, message: str) -> None:
        if self.progress_callback:
            try:
                self.progress_callback(85, message)
            except Exception:  # noqa: BLE001 - progress must never kill the run
                self.logger.debug("progress_callback failed", exc_info=True)


@dataclass(frozen=True)
class _GuidanceTexts:
    """Mode-specific steering messages (verbatim from the legacy loops)."""

    success: Callable[[int], str]
    error: str
    no_tools: Callable[[int], str]


def _standard_success_guidance(files_count: int) -> str:
    return f"""✅ File implementation completed successfully!

📊 **Progress Status:** {files_count} files implemented

🎯 **Next Action:** Check if ALL files from the reproduction plan are implemented.

⚡ **Decision Process:**
1. **If ALL files implemented:** Reply with "All files implemented" to complete the task
2. **If MORE files need implementation:** Continue with dependency-aware workflow:
   - **Use `write_file` to implement the new component"""


_STANDARD_ERROR_GUIDANCE = """❌ Error detected during file implementation.

🔧 **Action Required:**
1. Review the error details above
2. Fix the identified issue
3. **Check if ALL files from the reproduction plan are implemented:**
   - **If YES:** Respond "**implementation complete**" to end the conversation
   - **If NO:** Continue with proper development cycle for next file:
     - **Use `write_file` to implement properly
4. Ensure proper error handling in future implementations"""


def _standard_no_tools_guidance(files_count: int) -> str:
    return f"""⚠️ No tool calls detected in your response.

📊 **Current Progress:** {files_count} files implemented

🚨 **Action Required:** Check completion status NOW:

⚡ **Decision Process:**
1. **If ALL files from plan are implemented:** Reply "All files implemented" to complete
2. **If MORE files need implementation:** Use tools to continue:
   - **Use `write_file` to implement the new component

🚨 **Critical:** Don't just explain - either declare completion or use tools!"""


def _indexed_success_guidance(files_count: int) -> str:
    return f"""✅ File implementation completed successfully!

📊 **Progress Status:** {files_count} files implemented

🎯 **Next Action:** Check if ALL files from the reproduction plan are implemented.

⚡ **Decision Process:**
1. **If ALL files are implemented:** Use `execute_python` or `execute_bash` to test the complete implementation, then respond "**implementation complete**" to end the conversation
2. **If MORE files need implementation:** Continue with dependency-aware workflow:
   - **Start with `read_code_mem`** to understand existing implementations and dependencies
   - **Optionally use `search_code_references`** for reference patterns (OPTIONAL - use for inspiration only, original paper specs take priority)
   - **Then `write_file`** to implement the new component
   - **Finally: Test** if needed

💡 **Key Point:** Always verify completion status before continuing with new file creation."""


_INDEXED_ERROR_GUIDANCE = """❌ Error detected during file implementation.

🔧 **Action Required:**
1. Review the error details above
2. Fix the identified issue
3. **Check if ALL files from the reproduction plan are implemented:**
   - **If YES:** Use `execute_python` or `execute_bash` to test the complete implementation, then respond "**implementation complete**" to end the conversation
   - **If NO:** Continue with proper development cycle for next file:
     - **Start with `read_code_mem`** to understand existing implementations
     - **Use `write_file` to implement properly
4. Ensure proper error handling in future implementations"""


def _indexed_no_tools_guidance(files_count: int) -> str:
    return f"""⚠️ No tool calls detected in your response.

📊 **Current Progress:** {files_count} files implemented

🚨 **Action Required:** Check completion status NOW:

⚡ **Decision Process:**
1. **If ALL files from plan are implemented:** Use `execute_python` or `execute_bash` to test, then reply "**implementation complete**"
2. **If MORE files need implementation:** Use tools to continue:
   - **Start with `read_code_mem`** to understand existing implementations
   - **Use `write_file` to implement the new component

🚨 **Critical:** Don't just explain - either declare completion or use tools!"""


_STANDARD_GUIDANCE = _GuidanceTexts(
    success=_standard_success_guidance,
    error=_STANDARD_ERROR_GUIDANCE,
    no_tools=_standard_no_tools_guidance,
)
_INDEXED_GUIDANCE = _GuidanceTexts(
    success=_indexed_success_guidance,
    error=_INDEXED_ERROR_GUIDANCE,
    no_tools=_indexed_no_tools_guidance,
)


class _InstrumentedTool(AliasedTool):
    """Model-facing tool that routes execution through the legacy tracker.

    Execution is delegated to ``CodeImplementationAgent.execute_tool_calls``
    so every domain behavior survives unchanged: read-tools-disabled mocks,
    read_file → read_code_mem interception, write_file tracking + code
    summaries. On top of that this wrapper applies the workflow-level
    guards (loop detector) and bookkeeping (memory recording, progress)
    that used to live inside the hand-rolled loop.
    """

    def __init__(self, inner, alias: str, state: _RunState):
        super().__init__(inner, alias)
        self._state = state

    async def execute(self, **kwargs: Any) -> Any:
        state = self._state
        if state.abort_reason:
            return f"Error: tool execution aborted: {state.abort_reason}"

        loop_status = state.loop_detector.check_tool_call(self.name)
        if loop_status["should_stop"]:
            state.abort_reason = loop_status["message"]
            state.run_status = (
                "aborted",
                f"loop_detector tool-check: {loop_status['message']}",
            )
            state.logger.error(f"🛑 Tool execution aborted: {loop_status['message']}")
            return f"Error: tool execution aborted: {loop_status['message']}"

        legacy_call = {
            "id": f"call_{uuid.uuid4().hex[:12]}",
            "name": self.name,
            "input": kwargs,
        }
        results = await state.code_tracker.execute_tool_calls([legacy_call])
        result = results[0]["result"] if results else ""

        # The legacy loop recorded success unconditionally (its ``isError``
        # flag was never populated); error handling happens via guidance.
        state.loop_detector.record_success()
        state.memory_agent.record_tool_result(
            tool_name=self.name, tool_input=kwargs, tool_result=result
        )

        if self.name == "write_file":
            filename = kwargs.get("file_path", "unknown")
            completed_first_time = state.progress_tracker.complete_file(
                state.memory_agent.normalize_file_path(filename)
            )
            if completed_first_time:
                print(f"✅ File completed: {filename}")
                info = state.progress_tracker.get_progress_info()
                state.emit_progress(
                    "Code implementation progress: "
                    f"{info['files_completed']}/{info['total_files']} files completed"
                )
        return result


class _ImplementationHook(AgentHook):
    """Per-iteration domain policy: guidance, memory strategy, round sync."""

    def __init__(self, state: _RunState):
        super().__init__()
        self._state = state

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        self._state.in_tools_phase = True

    def finalize_content(self, context: AgentHookContext, content):
        # Runs synchronously right after each model response — before the
        # kernel's "after final response" injection drain — so the
        # injection callback can already see an error finish_reason and
        # refuse to keep the run alive after a provider failure.
        self._state.last_finish_reason = getattr(
            context.response, "finish_reason", None
        )
        return content

    async def after_iteration(self, context: AgentHookContext) -> None:
        state = self._state
        try:
            self._after_iteration(context)
        except Exception:  # noqa: BLE001 - policy must not kill the kernel loop
            state.logger.exception("Implementation hook failed; continuing run")
        finally:
            state.in_tools_phase = False
            state.iterations_done += 1

    def _after_iteration(self, context: AgentHookContext) -> None:
        state = self._state
        response = context.response
        state.last_finish_reason = getattr(response, "finish_reason", None)

        files_count = state.code_tracker.get_files_implemented_count()

        # Steering pressure after every tool round (tool outputs already
        # flow back as protocol tool messages; only the guidance is added).
        if context.tool_calls:
            has_error = _tool_results_contain_error(context.tool_results)
            guidance = (
                state.guidance.error
                if has_error
                else state.guidance.success(files_count)
            )
            context.messages.append({"role": "user", "content": guidance})

        # Sync file implementations into the memory agent.
        summary = state.code_tracker.get_implementation_summary()
        for file_info in summary.get("completed_files", []):
            state.memory_agent.record_file_implementation(file_info["file"])

        # Clean-slate memory strategy (unchanged ConciseMemoryAgent logic).
        if state.memory_agent.should_trigger_memory_optimization(
            context.messages, files_count
        ):
            self._apply_memory_optimization(context, files_count)

        state.memory_agent.start_new_round(iteration=state.iterations_done + 1)

        # Emergency trim mirrors the legacy loop (a no-op unless a
        # write_file optimization is pending — preserved behavior).
        if len(context.messages) > _EMERGENCY_TRIM_THRESHOLD:
            state.logger.warning(
                "Emergency message trim - applying concise memory optimization"
            )
            self._apply_memory_optimization(context, files_count)

        # File-level progress heartbeat.
        info = state.progress_tracker.get_progress_info()
        if info["total_files"] > 0 or info["files_completed"] > 0:
            print(
                f"📁 Files: {info['files_completed']}/{info['total_files']} "
                f"({info['file_progress']:.1f}%)"
            )
            if info["estimated_remaining_seconds"] > 0:
                print(
                    f"⏱️ Estimated remaining: {info['estimated_remaining_seconds']:.0f}s"
                )

    def _apply_memory_optimization(
        self, context: AgentHookContext, files_count: int
    ) -> None:
        state = self._state
        system_messages = [
            msg for msg in context.messages if msg.get("role") == "system"
        ]
        non_system = [msg for msg in context.messages if msg.get("role") != "system"]
        optimized = state.memory_agent.apply_memory_optimization(
            state.system_prompt, non_system, files_count
        )
        if optimized is not non_system:
            context.messages[:] = system_messages + list(optimized)


def _tool_results_contain_error(tool_results: List[Any]) -> bool:
    """Port of the legacy error sniffing over raw tool result strings."""
    for result in tool_results:
        text = result if isinstance(result, str) else str(result)
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and parsed.get("status") == "error":
                return True
            continue
        except (json.JSONDecodeError, TypeError):
            pass
        if "error" in text.lower():
            return True
    return False


class CodeImplementationWorkflow:
    """
    Paper Code Implementation Workflow Manager (kernel-unified).

    One class serves both modes:
    - ``enable_indexing=False`` — the standard workflow (full tool surface,
      general system prompt).
    - ``enable_indexing=True`` — the indexed workflow (minimal
      write_file/search_code_references surface, indexed system prompt).
    """

    def __init__(self, enable_indexing: bool = False) -> None:
        self.enable_indexing = enable_indexing
        self.default_models = get_default_models()
        self.logger = self._create_logger()
        self.mcp_agent = None
        self.enable_read_tools = True
        self.loop_detector = LoopDetector()
        self.progress_tracker = ProgressTracker()
        self._last_run_state: Dict[str, Any] = {
            "status": "unknown",
            "reason": None,
            "iterations": 0,
            "elapsed_seconds": 0.0,
            "files_completed": 0,
            "total_files": 0,
            "unimplemented_files": [],
        }

    # ==================== infrastructure ====================

    def _create_logger(self) -> logging.Logger:
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)
        return logger

    def _read_plan_file(self, plan_file_path: str) -> str:
        plan_path = os.path.abspath(plan_file_path)
        if not os.path.exists(plan_path):
            raise FileNotFoundError(
                f"Implementation plan file not found: {plan_file_path}"
            )
        with open(plan_path, "r", encoding="utf-8") as f:
            return f.read()

    def _check_file_tree_exists(self, target_directory: str) -> bool:
        code_directory = os.path.join(target_directory, "generate_code")
        return os.path.exists(code_directory) and len(os.listdir(code_directory)) > 0

    @property
    def _mcp_architecture(self) -> str:
        return "indexed" if self.enable_indexing else "standard"

    # ==================== public API ====================

    async def run_workflow(
        self,
        plan_file_path: str,
        target_directory: Optional[str] = None,
        pure_code_mode: bool = False,
        enable_read_tools: bool = True,
        progress_callback: Optional[Callable] = None,
    ):
        """Run complete workflow - Main public interface."""
        self.enable_read_tools = enable_read_tools

        try:
            plan_content = self._read_plan_file(plan_file_path)

            if target_directory is None:
                target_directory = str(os.path.dirname(os.path.abspath(plan_file_path)))

            code_directory = os.path.join(target_directory, "generate_code")

            self.logger.info("=" * 80)
            self.logger.info("🚀 STARTING CODE IMPLEMENTATION WORKFLOW (kernel)")
            self.logger.info("=" * 80)
            self.logger.info(f"📄 Plan file: {plan_file_path}")
            self.logger.info(f"📂 Plan file parent: {target_directory}")
            self.logger.info(f"🎯 Code directory (MCP workspace): {code_directory}")
            self.logger.info(f"🧭 Mode: {self._mcp_architecture}")
            self.logger.info(
                f"⚙️  Read tools: {'ENABLED' if self.enable_read_tools else 'DISABLED'}"
            )
            self.logger.info("=" * 80)

            results = {}

            if self._check_file_tree_exists(target_directory):
                self.logger.info("File tree exists, skipping creation")
                results["file_tree"] = "Already exists, skipped creation"
            else:
                self.logger.info("Creating file tree...")
                results["file_tree"] = await self.create_file_structure(
                    plan_content, target_directory
                )

            if pure_code_mode:
                self.logger.info("Starting pure code implementation...")
                results["code_implementation"] = await self.implement_code_pure(
                    plan_content,
                    target_directory,
                    code_directory,
                    progress_callback=progress_callback,
                )

            run_state = dict(self._last_run_state)
            inner_status = run_state.get("status", "unknown")
            done = inner_status == "completed"
            if done:
                self.logger.info(
                    "Workflow execution successful (all files implemented)"
                )
                top_status = "success"
            else:
                pending = run_state.get("unimplemented_files", []) or []
                self.logger.warning(
                    "Workflow execution finished EARLY: status=%s reason=%s "
                    "(files=%d, %d unimplemented)",
                    inner_status,
                    run_state.get("reason"),
                    run_state.get("files_completed", 0),
                    len(pending),
                )
                if pending:
                    sample = ", ".join(pending[:5])
                    if len(pending) > 5:
                        sample += f", ... (+{len(pending) - 5} more)"
                    self.logger.warning("Unimplemented files: %s", sample)
                top_status = "incomplete"

            return {
                "status": top_status,
                "inner_status": inner_status,
                "abort_reason": run_state.get("reason"),
                "files_completed": run_state.get("files_completed", 0),
                "total_files": run_state.get("total_files", 0),
                "unimplemented_files": run_state.get("unimplemented_files", []),
                "iterations": run_state.get("iterations", 0),
                "elapsed_seconds": run_state.get("elapsed_seconds", 0.0),
                "plan_file": plan_file_path,
                "target_directory": target_directory,
                "code_directory": os.path.join(target_directory, "generate_code"),
                "results": results,
                "mcp_architecture": self._mcp_architecture,
            }

        except Exception as e:
            self.logger.error(f"Workflow execution failed: {e}")
            code_directory = os.path.join(
                target_directory
                or str(os.path.dirname(os.path.abspath(plan_file_path))),
                "generate_code",
            )
            return {
                "status": "error",
                "inner_status": "error",
                "abort_reason": str(e),
                "message": str(e),
                "files_completed": 0,
                "total_files": 0,
                "unimplemented_files": [],
                "elapsed_seconds": 0.0,
                "plan_file": plan_file_path,
                "target_directory": target_directory,
                "code_directory": code_directory,
                "results": {},
                "mcp_architecture": self._mcp_architecture,
            }
        finally:
            await self._cleanup_mcp_agent()

    async def create_file_structure(
        self, plan_content: str, target_directory: str
    ) -> str:
        """Create file tree structure based on implementation plan."""
        self.logger.info("Starting file tree creation...")

        structure_agent = Agent(
            name="StructureGeneratorAgent",
            instruction=STRUCTURE_GENERATOR_PROMPT,
            server_names=["command-executor"],
        )

        async with structure_agent:
            creator = await attach_workflow_llm(
                structure_agent,
                phase="implementation",
            )

            message = f"""Analyze the following implementation plan and generate shell commands to create the file tree structure.

Target Directory: {target_directory}/generate_code/

Implementation Plan:
{plan_content}

Tasks:
1. Find the file tree structure in the implementation plan
2. Generate shell commands (mkdir -p, touch) to create that structure
3. Use the execute_commands tool to run the commands and create the file structure

Requirements:
- Use mkdir -p to create directories
- Use touch to create files
- Include __init__.py file for Python packages
- Use relative paths to the target directory
- Execute commands to actually create the file structure"""

            result = await creator.generate_str(message=message)
            self.logger.info(f"LLM response: {result[:200]}...")

            code_dir = os.path.join(target_directory, "generate_code")
            if not os.path.exists(code_dir):
                self.logger.warning(
                    "LLM did not create directory, creating manually..."
                )
                os.makedirs(code_dir, exist_ok=True)
                self.logger.info(f"✅ Manually created directory: {code_dir}")
            else:
                self.logger.info(f"✅ Directory exists: {code_dir}")

            return result

    async def implement_code_pure(
        self,
        plan_content: str,
        target_directory: str,
        code_directory: str = None,
        progress_callback: Optional[Callable] = None,
    ) -> str:
        """Pure code implementation on the unified kernel."""
        self.logger.info("Starting pure code implementation (no testing)...")

        if code_directory is None:
            code_directory = os.path.join(target_directory, "generate_code")

        self.logger.info(f"🎯 Using code directory (MCP workspace): {code_directory}")
        if not os.path.exists(code_directory):
            self.logger.warning(
                f"Code directory does not exist, creating it: {code_directory}"
            )
            os.makedirs(code_directory, exist_ok=True)

        try:
            provider, profile = get_workflow_provider(phase="implementation")
            self.logger.info(
                "Using DeepCode provider runtime: phase=%s provider=%s model=%s",
                profile.phase,
                profile.provider_name,
                profile.model,
            )
            await self._initialize_mcp_agent(code_directory)
            return await self._run_kernel_implementation(
                provider,
                plan_content,
                target_directory,
                code_directory,
                progress_callback=progress_callback,
            )
        finally:
            await self._cleanup_mcp_agent()

    # ==================== kernel wiring ====================

    def _system_prompt(self) -> str:
        if self.enable_indexing:
            return PURE_CODE_IMPLEMENTATION_SYSTEM_PROMPT_INDEX
        return GENERAL_CODE_IMPLEMENTATION_SYSTEM_PROMPT

    def _model_tool_names(self) -> List[str]:
        names = list(
            _INDEXED_TOOL_NAMES if self.enable_indexing else _STANDARD_TOOL_NAMES
        )
        if not self.enable_read_tools:
            names = [n for n in names if n not in _READ_TOOL_NAMES]
        return names

    def _build_model_registry(self, state: _RunState) -> ToolRegistry:
        """Select + alias + instrument the model-facing tool surface."""
        source = self.mcp_agent.tool_registry
        aliased, missing = build_aliased_registry(source, self._model_tool_names())
        if missing:
            self.logger.warning(
                "MCP tools missing from registry (server down?): %s", missing
            )
        instrumented = ToolRegistry()
        for name in aliased.tool_names:
            tool = aliased.get(name)
            inner = tool.inner if isinstance(tool, AliasedTool) else tool
            instrumented.register(_InstrumentedTool(inner, name, state))
        self.logger.info(
            "🔧 Model tool surface (%s): %s",
            self._mcp_architecture,
            instrumented.tool_names,
        )
        return instrumented

    async def _run_kernel_implementation(
        self,
        provider,
        plan_content: str,
        target_directory: str,
        code_directory: str,
        progress_callback: Optional[Callable] = None,
    ) -> str:
        system_prompt = self._system_prompt()

        code_tracker = CodeImplementationAgent(
            self.mcp_agent, self.logger, self.enable_read_tools
        )
        memory_agent = ConciseMemoryAgent(
            plan_content,
            self.logger,
            target_directory,
            self.default_models,
            code_directory,
        )
        code_tracker.set_memory_agent(memory_agent, provider, "provider")
        memory_agent.start_new_round(iteration=0)

        total_files = len(memory_agent.all_files_list)
        self.progress_tracker.set_total_files(total_files)
        if progress_callback:
            progress_callback(
                85,
                f"Code implementation started: 0/{total_files} planned files completed",
            )

        read_tools_status = "ENABLED" if self.enable_read_tools else "DISABLED"
        self.logger.info(
            f"🔧 Read tools (read_file, read_code_mem): {read_tools_status}"
        )

        state = _RunState(
            memory_agent=memory_agent,
            code_tracker=code_tracker,
            loop_detector=self.loop_detector,
            progress_tracker=self.progress_tracker,
            logger=self.logger,
            system_prompt=system_prompt,
            guidance=_INDEXED_GUIDANCE if self.enable_indexing else _STANDARD_GUIDANCE,
            progress_callback=progress_callback,
            max_wall_seconds=_MAX_WALL_SECONDS,
        )

        implementation_message = f"""**Task: Implement code based on the following reproduction plan**

**Code Reproduction Plan:**
{plan_content}

**Working Directory:** {code_directory}

**Current Objective:** Begin implementation by analyzing the plan structure, examining the current project layout, and implementing the first foundation file according to the plan's priority order."""

        initial_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": implementation_message},
        ]

        async def should_stop() -> Optional[str]:
            if state.run_status is not None:
                return state.run_status[1]
            if state.abort_reason:
                state.run_status = (
                    "aborted",
                    f"loop_detector tool-check: {state.abort_reason}",
                )
                return state.abort_reason
            elapsed = time.time() - state.start_time
            if elapsed > state.max_wall_seconds:
                reason = (
                    f"wall-clock budget exhausted after {elapsed:.0f}s "
                    f"(limit {int(state.max_wall_seconds)}s)"
                )
                state.run_status = ("max_time", reason)
                self.logger.warning(f"Time limit reached: {elapsed:.2f}s")
                return reason
            if self.loop_detector.should_abort():
                abort_reason = self.loop_detector.get_abort_reason()
                state.run_status = (
                    "aborted",
                    f"loop_detector pre-LLM: {abort_reason}",
                )
                self.logger.error(f"🛑 Process aborted (pre-LLM): {abort_reason}")
                return abort_reason or "loop detector abort"
            if state.iterations_done >= 1:
                unimplemented = memory_agent.get_unimplemented_files()
                if not unimplemented:
                    state.run_status = (
                        "completed",
                        "all planned files implemented",
                    )
                    self.logger.info(
                        "✅ Code implementation complete - All files implemented"
                    )
                    return "all planned files implemented"
            return None

        async def inject_followups() -> List[Dict[str, Any]]:
            # Only steer after a toolless final response; post-tool guidance
            # is appended by the hook, and errors must end the run.
            if state.in_tools_phase or state.run_status is not None:
                return []
            if state.last_finish_reason == "error":
                return []
            if not memory_agent.get_unimplemented_files():
                return []
            files_count = code_tracker.get_files_implemented_count()
            return [{"role": "user", "content": state.guidance.no_tools(files_count)}]

        async def on_retry_wait(message: str) -> None:
            self.logger.warning("Implementation LLM retry: %s", message)
            state.emit_progress(f"Retrying implementation LLM call: {message}")

        # Security base (P1). The implementation phase is autonomous, so it
        # defaults to FULL_AUTO — no per-tool prompts — while the
        # non-overridable sensitive-path denylist always applies (the agent
        # can never read/write credential stores even if a plan asks).
        #
        # A security-conscious user can set DEEPCODE_PERMISSION_MODE=default
        # (or plan) to get interactive per-mutation approval on the terminal;
        # in that case an approver is attached. Unknown values fall back to
        # full_auto so a typo never silently blocks an unattended run.
        mode = _resolve_permission_mode()
        permission_engine = PermissionEngine(mode=mode, cwd=code_directory)
        approval_cb = None
        if mode is not PermissionMode.FULL_AUTO:
            approval_cb = TerminalApprover().as_async()
            self.logger.info(
                "🔐 Permission mode: %s (interactive approval)", mode.value
            )

        spec = AgentRunSpec(
            initial_messages=initial_messages,
            tools=self._build_model_registry(state),
            model=provider.get_default_model(),
            max_iterations=_MAX_ITERATIONS,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            temperature=0.2,
            max_tokens=8192,
            hook=_ImplementationHook(state),
            session_key=f"code-implementation[{self._mcp_architecture}]",
            provider_retry_mode="standard",
            retry_wait_callback=on_retry_wait,
            injection_callback=inject_followups,
            should_stop_callback=should_stop,
            permission_checker=permission_engine.evaluate,
            approval_callback=approval_cb,
            # The implementation phase owns its own budgets (wall clock +
            # iteration caps); no per-call timeout, like the legacy loop.
            llm_timeout_s=0,
            max_injection_cycles=_MAX_ITERATIONS,
        )

        runner = AgentRunner(provider)
        result = await runner.run(spec)

        status, reason = self._map_run_status(result, state, memory_agent)
        elapsed_total = time.time() - state.start_time
        self._last_run_state = {
            "status": status,
            "reason": reason,
            "iterations": state.iterations_done,
            "elapsed_seconds": elapsed_total,
            "files_completed": len(memory_agent.get_implemented_files()),
            "total_files": len(memory_agent.get_all_files_list()),
            "unimplemented_files": list(memory_agent.get_unimplemented_files() or []),
        }
        return await self._generate_final_report(
            state.iterations_done, elapsed_total, code_tracker, memory_agent
        )

    def _map_run_status(
        self,
        result,
        state: _RunState,
        memory_agent: ConciseMemoryAgent,
    ) -> tuple:
        if state.run_status is not None:
            return state.run_status
        if result.stop_reason == "max_iterations":
            return (
                "max_iterations",
                f"reached max_iterations={_MAX_ITERATIONS} without completion",
            )
        if result.stop_reason in ("error", "tool_error", "empty_final_response"):
            return (
                "incomplete",
                f"LLM request failed during implementation: {result.error}",
            )
        if not memory_agent.get_unimplemented_files():
            return ("completed", "all planned files implemented")
        return (
            "incomplete",
            "model stopped while planned files remain unimplemented",
        )

    # ==================== MCP lifecycle ====================

    async def _initialize_mcp_agent(self, code_directory: str):
        """Connect the MCP servers and set the workspace."""
        try:
            self.mcp_agent = Agent(
                name="CodeImplementationAgent",
                instruction=(
                    "You are a code implementation assistant, using MCP tools to "
                    "implement paper code replication. For large documents, use "
                    "document-segmentation tools to read content in smaller chunks "
                    "to avoid token limits."
                ),
                server_names=list(_MCP_SERVER_NAMES),
            )
            await self.mcp_agent.__aenter__()
            workspace_result = await self.mcp_agent.call_tool(
                "set_workspace", {"workspace_path": code_directory}
            )
            self.logger.info(f"Workspace setup result: {workspace_result}")
        except Exception as e:
            self.logger.error(f"Failed to initialize MCP agent: {e}")
            if self.mcp_agent:
                try:
                    await self.mcp_agent.__aexit__(None, None, None)
                except Exception:
                    pass
                self.mcp_agent = None
            raise

    async def _cleanup_mcp_agent(self):
        if self.mcp_agent:
            try:
                await self.mcp_agent.__aexit__(None, None, None)
                self.logger.info("MCP agent connection closed")
            except Exception as e:
                self.logger.warning(f"Error closing MCP agent: {e}")
            finally:
                self.mcp_agent = None

    # ==================== reporting ====================

    async def _generate_final_report(
        self,
        iterations: int,
        elapsed_time: float,
        code_tracker: CodeImplementationAgent,
        memory_agent: ConciseMemoryAgent,
    ):
        """Generate the final report using tracker/memory statistics."""
        try:
            code_stats = code_tracker.get_implementation_statistics()
            memory_stats = memory_agent.get_memory_statistics(
                code_stats["files_implemented_count"]
            )

            if self.mcp_agent:
                history_result = await self.mcp_agent.call_tool(
                    "get_operation_history", {"last_n": 30}
                )
                history_data = (
                    json.loads(history_result)
                    if isinstance(history_result, str)
                    else history_result
                )
            else:
                history_data = {"total_operations": 0, "history": []}

            write_operations = 0
            files_created = []
            if "history" in history_data:
                for item in history_data["history"]:
                    if item.get("action") == "write_file":
                        write_operations += 1
                        file_path = item.get("details", {}).get("file_path", "unknown")
                        files_created.append(file_path)

            report = f"""
# Pure Code Implementation Completion Report (Kernel-Unified Memory Mode)

## Execution Summary
- Implementation iterations: {iterations}
- Total elapsed time: {elapsed_time:.2f} seconds
- Files implemented: {code_stats["total_files_implemented"]}
- File write operations: {write_operations}
- Total MCP operations: {history_data.get("total_operations", 0)}
- Workflow mode: {self._mcp_architecture}

## Read Tools Configuration
- Read tools enabled: {code_stats["read_tools_status"]["read_tools_enabled"]}
- Status: {code_stats["read_tools_status"]["status"]}
- Tools affected: {", ".join(code_stats["read_tools_status"]["tools_affected"])}

## Agent Performance
### Code Implementation Agent
- Files tracked: {code_stats["files_implemented_count"]}
- Technical decisions: {code_stats["technical_decisions_count"]}
- Constraints tracked: {code_stats["constraints_count"]}
- Architecture notes: {code_stats["architecture_notes_count"]}
- Dependency analysis performed: {code_stats["dependency_analysis_count"]}
- Files read for dependencies: {code_stats["files_read_for_dependencies"]}
- Last summary triggered at file count: {code_stats["last_summary_file_count"]}

### Concise Memory Agent (Write-File-Based)
- Last write_file detected: {memory_stats["last_write_file_detected"]}
- Should clear memory next: {memory_stats["should_clear_memory_next"]}
- Files implemented count: {memory_stats["implemented_files_tracked"]}
- Current round: {memory_stats["current_round"]}
- Concise mode active: {memory_stats["concise_mode_active"]}
- Current round tool results: {memory_stats["current_round_tool_results"]}
- Essential tools recorded: {memory_stats["essential_tools_recorded"]}

## Files Created
"""
            for file_path in files_created[-20:]:
                report += f"- {file_path}\n"
            if len(files_created) > 20:
                report += f"... and {len(files_created) - 20} more files\n"

            report += """
## Architecture Features
✅ Unified AgentRunner kernel loop (single execution stack)
✅ Tool schemas sourced from MCP servers (no hand-maintained copies)
✅ WRITE-FILE-BASED memory strategy — clear after each file generation
✅ Mechanical completion check (planned files vs written files)
✅ Loop detection and wall-clock/iteration budgets enforced in code
✅ Specialized agent separation for clean code organization
✅ MCP-compliant tool execution
"""
            return report

        except Exception as e:
            self.logger.error(f"Failed to generate final report: {e}")
            return f"Failed to generate final report: {str(e)}"


class CodeImplementationWorkflowWithIndex(CodeImplementationWorkflow):
    """Back-compat alias: the indexed mode of the unified workflow."""

    def __init__(self) -> None:
        super().__init__(enable_indexing=True)


async def main():
    """Manual smoke entry (kept minimal)."""
    logging.basicConfig(level=logging.INFO)
    workflow = CodeImplementationWorkflow()
    plan_file = os.path.join(
        os.getcwd(), "deepcode_lab", "papers", "2", "initial_plan.txt"
    )
    target_directory = os.path.join(os.getcwd(), "deepcode_lab", "papers", "2")
    result = await workflow.run_workflow(
        plan_file,
        target_directory=target_directory,
        pure_code_mode=True,
    )
    print(f"Status: {result['status']}")


if __name__ == "__main__":
    asyncio.run(main())
