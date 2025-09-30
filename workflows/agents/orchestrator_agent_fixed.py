from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Type, Union
from yaml import safe_load
from mcp_agent.agents.agent import Agent
from mcp_agent.config import get_settings
from mcp_agent.core.context import cleanup_context, initialize_context
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

log = logging.getLogger("orchestrator.agent")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


# --------------------------- Plan Structures ---------------------------

@dataclass
class StepSpec:
    """
    Minimal step specification parsed from plan JSON.
    Unknown fields are stored inside 'extra' for future use.
    """
    id: str
    agent_name: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, str] = field(default_factory=dict)  # key -> artifact name
    server_names: List[str] = field(default_factory=list)
    retries: int = 3
    timeout: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> "StepSpec":
        known = {
            "id", "agent_name", "inputs", "outputs",
            "server_names", "retries", "timeout"
        }
        extra = {k: v for k, v in obj.items() if k not in known}
        return cls(
            id=obj["id"],
            agent_name=obj["agent_name"],
            inputs=dict(obj.get("inputs", {})),
            outputs=dict(obj.get("outputs", {})),
            server_names=list(obj.get("server_names", [])),
            retries=int(obj.get("retries", 3) or 3),
            timeout=obj.get("timeout"),
            extra=extra,
        )


# --------------------------- Orchestrator ---------------------------

class OrchestratorAgent:
    """
    Execute an agent_orchestration_plan (DAG) by instantiating concrete Agents
    and attaching an LLM to each step.
    """

    def __init__(
        self,
        plan: Mapping[str, Any],
        registry: Mapping[str, Union[Agent, Type[Agent], Callable[..., Agent]]],
        project_root: Optional[Union[str, Path]] = None,
        workspace: Optional[Union[str, Path]] = None,
    ) -> None:
        # ---- validate plan type
        if plan.get("plan_type") != "agent_orchestration_plan":
            raise ValueError("unsupported plan_type")

        # ---- parse steps
        self.global_context: Dict[str, Any] = dict(plan.get("global_context", {}))
        self.steps: Dict[str, StepSpec] = {s["id"]: StepSpec.from_json(s) for s in plan["steps"]}
        self.registry = dict(registry)
        self.artifacts: Dict[str, Any] = {}

        # ---- resolve paths (absolute, independent of CWD)
        if project_root is None:
            # file layout: <project_root>/workflows_cli/agents/orchestrator_agent.py
            project_root = Path(__file__).resolve().parents[2]
        self.project_root = Path(project_root).resolve()

        self.config_path = self.project_root / "mcp_agent.config.yaml"
        self.secrets_path = self.project_root / "mcp_agent.secrets.yaml"

        # ---- workspace
        self.workspace = Path(workspace) if workspace is not None else (self.project_root / ".orchestrator_artifacts")
        self.workspace.mkdir(parents=True, exist_ok=True)

        # ---- context will be initialized in run_async (cannot await in __init__)
        self.context = None

        # ---- LLM factory (default: DeepSeek via OpenAI-compatible endpoint)
        #     You can override by assigning self.llm_factory before run_async.
        self.llm_factory: Optional[Callable[[Agent], Any]] = None

    # ----------------- Helpers -----------------

    def _resolve_input_value(self, v: Any) -> Any:
        """
        Resolve ${artifact_key} placeholders inside strings,
        and recursively resolve inside structures.
        """
        if isinstance(v, str):
            # ${artifact_key}
            def repl(m):
                key = m.group(1)
                if key in self.artifacts:
                    return str(self.artifacts[key])
                return m.group(0)

            return re.sub(r"\$\{([^}]+)\}", repl, v)

        if isinstance(v, list):
            return [self._resolve_input_value(x) for x in v]

        if isinstance(v, dict):
            return {k: self._resolve_input_value(x) for k, x in v.items()}

        return v

    def _resolve_inputs(self, inputs: Mapping[str, Any]) -> Dict[str, Any]:
        return {k: self._resolve_input_value(v) for k, v in inputs.items()}

    async def _instantiate_agent(self, spec: StepSpec) -> Agent:
        entry = self.registry.get(spec.agent_name)
        if entry is None:
            raise KeyError(f"Unknown agent: {spec.agent_name}")

        if isinstance(entry, Agent):
            agent = entry
            # ensure context is set
            if getattr(agent, "context", None) is None and self.context is not None:
                agent.context = self.context
        elif isinstance(entry, type) and issubclass(entry, Agent):
            agent = entry(
                name=spec.id,
                instruction="",
                server_names=spec.server_names,
                context=self.context,
            )
            # some Agent implementations require explicit initialize()
            try:
                await agent.initialize()
            except Exception:
                # not fatal if initialize is lazy inside methods
                pass
        elif callable(entry):
            # factory returning an Agent
            agent = entry(name=spec.id, context=self.context)
        else:
            raise TypeError(f"Unsupported registry entry type for {spec.agent_name}: {type(entry)}")

        return agent

    async def _attach_llm(self, agent: Agent) -> Any:
        # prefer custom factory if user set it
        if self.llm_factory is not None:
            return await agent.attach_llm(llm_factory=self.llm_factory)

        # default: OpenAI-compatible (DeepSeek) via config/env
        model = None
        try:
            model = self.settings.openai.default_model  # type: ignore[attr-defined]
        except Exception:
            model = "deepseek-chat"

        factory = lambda a: OpenAIAugmentedLLM(agent=a, context=self.context, model=model)
        return await agent.attach_llm(llm_factory=factory)

    # ----------------- Execution -----------------

    async def run_async(self) -> None:
        """
        Initialize context, then execute all steps with retries.
        """
        # 1) load settings from absolute path (also tries to read secrets.yaml)
        self.settings = get_settings(str(self.config_path))

        # 2) initialize context (store_globally optional; here True for libs that depend on it)
        self.context = await initialize_context(config=self.settings, store_globally=True)

        # 3) run steps in listed order (you can add topo sort if you include dependencies)
        for sid, spec in self.steps.items():
            ok = await self._run_step_with_retries(spec)
            if not ok:
                raise RuntimeError(f"Workflow halted due to step {sid} failure")

        # 4) cleanup (optional)
        await cleanup_context()

    async def _run_step_with_retries(self, spec: StepSpec) -> bool:
        attempts = max(1, int(spec.retries))
        for i in range(1, attempts + 1):
            try:
                log.info("▶️  Step %s (%s) — attempt %s/%s", spec.id, spec.agent_name, i, attempts)
                await self._run_step(spec)
                log.info("✅ Step %s succeeded", spec.id)
                return True
            except Exception as e:
                log.error("Attempt %s/%s failed for step %s: %s", i, attempts, spec.id, e)
                if i == attempts:
                    log.error("❌  Step %s failed after %s retries", spec.id, attempts)
                    return False
        return False

    async def _run_step(self, spec: StepSpec) -> None:
        agent = await self._instantiate_agent(spec)
        await self._attach_llm(agent)  # ensures agent.llm exists

        # prepare inputs
        inputs = self._resolve_inputs(spec.inputs)

        # choose how to execute:
        # 1) if LLM exposes a run(**kwargs) (some do), prefer it
        if getattr(agent, "llm", None) is not None and hasattr(agent.llm, "run"):
            result = await agent.llm.run(**inputs)
        # 2) else if Agent exposes run(**kwargs), use it
        elif hasattr(agent, "run") and callable(getattr(agent, "run")):
            result = await agent.run(**inputs)  # type: ignore[misc]
        else:
            # 3) fallback: ask the LLM to generate with a composed prompt
            messages = [
                {"role": "system", "content": getattr(agent, "instruction", "") or "You are a helpful agent."},
                {"role": "user", "content": json.dumps(inputs, ensure_ascii=False)},
            ]
            result = await agent.llm.generate(messages)  # type: ignore[union-attr]

        # store artifacts if declared
        if spec.outputs:
            self._store_outputs(spec, result)

    def _store_outputs(self, spec: StepSpec, result: Any) -> None:
        """
        Map declared outputs to artifacts storage.
        'outputs' is a dict: logical_key -> artifact_name (string)
        The actual extraction logic can be extended per your project's convention.
        """
        def to_text(res: Any) -> str:
            # common patterns: OpenAI content list / Text content
            try:
                # if it's a pydantic model with model_dump
                if hasattr(res, "model_dump"):
                    res = res.model_dump()
            except Exception:
                pass

            if isinstance(res, str):
                return res

            if isinstance(res, dict):
                c = res.get("content")
                if isinstance(c, str):
                    return c
                if isinstance(c, list):
                    parts = []
                    for it in c:
                        typ = it.get("type") if isinstance(it, dict) else getattr(it, "type", None)
                        if typ == "text":
                            parts.append(it.get("text") if isinstance(it, dict) else getattr(it, "text", ""))
                    return "\n".join(p for p in parts if p)

            # fallback
            try:
                return json.dumps(res, ensure_ascii=False)
            except Exception:
                return str(res)

        for key, art_key in spec.outputs.items():
            value = result if key == "*" else result.get(key, result) if isinstance(result, dict) else result
            self.artifacts[art_key] = value

            # also write a human-readable copy into workspace (optional)
            try:
                text = to_text(value)
                (self.workspace / f"{art_key}.txt").write_text(text, encoding="utf-8")
            except Exception:
                pass

            # optional: pickle a binary copy
            try:
                import pickle, gzip
                with gzip.open(self.workspace / f"{art_key}.pkl.gz", "wb") as f:
                    pickle.dump(value, f)
            except Exception:
                pass


# ----------------- Example Usage ------------------------------------
if __name__ == "__main__":
    # Hardcoded parameters instead of command-line arguments
    plan_path = Path(r"D:\PythonProjects\DeepCode-main\1.txt").expanduser().resolve()
    workspace_path = Path(r"D:\PythonProjects\DeepCode-main\artifacts")  # Optional, can be None

    # Load the plan JSON
    with plan_path.open("r", encoding="utf-8") as f:
        plan_json = json.load(f)

    # User must provide a registry mapping in their project
    try:
        from workflows.agents.registry import registry  # type: ignore
    except Exception as e:
        raise RuntimeError("Missing 'workflows_cli.agents.registry.registry' mapping") from e

    # Create and run the orchestrator
    orch = OrchestratorAgent(
        plan_json,
        registry,
        workspace=workspace_path  # Can also be None to use default
    )
    asyncio.run(orch.run_async())