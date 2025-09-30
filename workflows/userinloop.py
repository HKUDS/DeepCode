"""user-in-loop用到的基类和方法实现
"""
import os
import re
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any
import json

import yaml
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_anthropic import AnthropicAugmentedLLM
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

from prompts.code_prompts import CODE_PLANNING_PROMPT, CHAT_AGENT_PLANNING_PROMPT

ROUTE_PROMPT = """\
You are a senior research-to-code planner. Given the TASK below, propose 3–5 distinct ROUTES to accomplish it.
Each ROUTE must be concrete and trade-off aware.

TASK:
{task_brief}

OUTPUT STRICTLY IN VALID JSON that matches this SCHEMA (no extra text outside JSON):
{schema_hint}

Guidelines:
- Make routes meaningfully different (e.g., quick baseline vs robust full replication vs retrieval-augmented, etc.)
- key_steps: 3–7 atomic steps that can be turned into sub-tasks later.
- required_tools: MCP servers, libraries, datasets, external repos if any.
- est_time/est_cost are rough human-readable estimates (e.g., "1–2h", "$0.5–$2").
- risk_notes: realistic blockers and mitigations.
- success_criteria: objective checks for completion.
- when_to_prefer: short heuristic telling the user when this route is ideal.
"""
def get_preferred_llm_class(config_path: str = "mcp_agent.secrets.yaml"):
    """
    Automatically select the LLM class based on API key availability in configuration.

    Reads from YAML config file and returns AnthropicAugmentedLLM if anthropic.api_key
    is available, otherwise returns OpenAIAugmentedLLM.

    Args:
        config_path: Path to the YAML configuration file

    Returns:
        class: The preferred LLM class
    """
    try:
        # Try to read the configuration file
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            # Check for anthropic API key in config
            anthropic_config = config.get("anthropic", {})
            anthropic_key = anthropic_config.get("api_key", "")

            if anthropic_key and anthropic_key.strip() and not anthropic_key == "":
                # print("🤖 Using AnthropicAugmentedLLM (Anthropic API key found in config)")
                return AnthropicAugmentedLLM
            else:
                # print("🤖 Using OpenAIAugmentedLLM (Anthropic API key not configured)")
                return OpenAIAugmentedLLM
        else:
            print(f"🤖 Config file {config_path} not found, using OpenAIAugmentedLLM")
            return OpenAIAugmentedLLM

    except Exception as e:
        print(f"🤖 Error reading config file {config_path}: {e}")
        print("🤖 Falling back to OpenAIAugmentedLLM")
        return OpenAIAugmentedLLM


def extract_clean_json(llm_output: str) -> str:
    """
    Extract clean JSON from LLM output, removing all extra text and formatting.

    Args:
        llm_output: Raw LLM output

    Returns:
        str: Clean JSON string
    """
    try:
        # Try to parse the entire output as JSON first
        json.loads(llm_output.strip())
        return llm_output.strip()
    except json.JSONDecodeError:
        pass

    # Remove markdown code blocks
    if "```json" in llm_output:
        pattern = r"```json\s*(.*?)\s*```"
        match = re.search(pattern, llm_output, re.DOTALL)
        if match:
            json_text = match.group(1).strip()
            try:
                json.loads(json_text)
                return json_text
            except json.JSONDecodeError:
                pass

    # Find JSON object starting with {
    lines = llm_output.split("\n")
    json_lines = []
    in_json = False
    brace_count = 0

    for line in lines:
        stripped = line.strip()
        if not in_json and stripped.startswith("{"):
            in_json = True
            json_lines = [line]
            brace_count = stripped.count("{") - stripped.count("}")
        elif in_json:
            json_lines.append(line)
            brace_count += stripped.count("{") - stripped.count("}")
            if brace_count == 0:
                break

    if json_lines:
        json_text = "\n".join(json_lines).strip()
        try:
            json.loads(json_text)
            return json_text
        except json.JSONDecodeError:
            pass

    # Last attempt: use regex to find JSON
    pattern = r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
    matches = re.findall(pattern, llm_output, re.DOTALL)
    for match in matches:
        try:
            json.loads(match)
            return match
        except json.JSONDecodeError:
            continue

    # If all methods fail, return original output
    return llm_output
def get_preferred_llm_class(config_path: str = "mcp_agent.secrets.yaml"):
    """
    Automatically select the LLM class based on API key availability in configuration.

    Reads from YAML config file and returns AnthropicAugmentedLLM if anthropic.api_key
    is available, otherwise returns OpenAIAugmentedLLM.

    Args:
        config_path: Path to the YAML configuration file

    Returns:
        class: The preferred LLM class
    """
    try:
        # Try to read the configuration file
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            # Check for anthropic API key in config
            anthropic_config = config.get("anthropic", {})
            anthropic_key = anthropic_config.get("api_key", "")

            if anthropic_key and anthropic_key.strip() and not anthropic_key == "":
                # print("🤖 Using AnthropicAugmentedLLM (Anthropic API key found in config)")
                return AnthropicAugmentedLLM
            else:
                # print("🤖 Using OpenAIAugmentedLLM (Anthropic API key not configured)")
                return OpenAIAugmentedLLM
        else:
            print(f"🤖 Config file {config_path} not found, using OpenAIAugmentedLLM")
            return OpenAIAugmentedLLM

    except Exception as e:
        print(f"🤖 Error reading config file {config_path}: {e}")
        print("🤖 Falling back to OpenAIAugmentedLLM")
        return OpenAIAugmentedLLM
@dataclass
class RouteOption:
    id: str
    title: str
    summary: str
    key_steps: List[str]
    required_tools: List[str]
    risk_notes: str
    success_criteria: List[str]
    when_to_prefer: str

@dataclass
class RouteOptions:
    task_brief: str
    options: List[RouteOption]

    def to_json(self) -> str:
        return json.dumps({
            "task_brief": self.task_brief,
            "options": [asdict(o) for o in self.options]
        }, ensure_ascii=False, indent=2)

SCHEMA_HINT = {
    "type": "object",
    "properties": {
        "task_brief": {"type": "string"},
        "options": {
            "type": "array", "minItems": 3, "maxItems": 6,
            "items": {
                "type": "object",
                "required": ["id", "title", "summary", "key_steps", "required_tools",
                              "risk_notes", "success_criteria", "when_to_prefer"],
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "key_steps": {"type": "array", "items": {"type": "string"}},
                    "required_tools": {"type": "array", "items": {"type": "string"}},
                    "risk_notes": {"type": "string"},
                    "success_criteria": {"type": "array", "items": {"type": "string"}},
                    "when_to_prefer": {"type": "string"}
                }
            }
        }
    },
    "required": ["task_brief", "options"]
}

def parse_route_options(json_str: str) -> RouteOptions:
    data = json.loads(json_str)
    opts = []
    for raw in data.get("options", []):
        opts.append(RouteOption(
            id=str(raw["id"]),
            title=raw["title"],
            summary=raw["summary"],
            key_steps=list(raw["key_steps"]),
            required_tools=list(raw["required_tools"]),
            risk_notes=raw["risk_notes"],
            success_criteria=list(raw["success_criteria"]),
            when_to_prefer=raw["when_to_prefer"],
        ))
    return RouteOptions(task_brief=data.get("task_brief", ""), options=opts)
def _prompt_user_to_select_route(route_opts: RouteOptions) -> int:
    print("\n===== Multiple Route Options =====")
    for i, r in enumerate(route_opts.options, 1):
        print(f"🎯{i}. {r.title}")
        print(f"🤖   Summary: {r.summary}")
        print(f"🤖   Steps: {'; '.join(r.key_steps[:4])}{' ...' if len(r.key_steps)>4 else ''}")
        print(f"🤖  Tools: {', '.join(r.required_tools)}")
        print(f"🤖   Risks: {r.risk_notes}")
        print(f"🤖   Prefer when: {r.when_to_prefer}")
        print("-"*72)
    while True:
        try:
            idx = int(input("Please choose between ROUTE (1-{}): ".format(len(route_opts.options)))) - 1
            if 0 <= idx < len(route_opts.options):
                print(f"✅ You have chosen：{route_opts.options[idx].title}")
                return idx
        except Exception:
            pass
        print("invalid input ,please reinput")

async def _generate_route_options(paper_dir: str, logger) -> RouteOptions:
    """main function to realize user-in-loop function,a route planning agent give suggestions and
      several impletation plan on realizing the user's requirement"""
    route_agent = Agent(
        name="RoutePlannerAgent",
        instruction=CODE_PLANNING_PROMPT,      # 复用你的规划提示词
        server_names=["filesystem"],  # 与现有一致；没有 brave 时可去掉
    )

    schema_hint = {
        "type": "object",
        "required": ["task_brief", "options"],
        "properties": {
            "task_brief": {"type": "string"},
            "options": {
                "type": "array", "minItems": 3, "maxItems": 6,
                "items": {
                    "type": "object",
                    "required": ["id", "title", "summary", "key_steps", "required_tools",
                                  "risk_notes", "success_criteria", "when_to_prefer"],
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "key_steps": {"type": "array", "items": {"type": "string"}},
                        "required_tools": {"type": "array", "items": {"type": "string"}},
                        "risk_notes": {"type": "string"},
                        "success_criteria": {"type": "array", "items": {"type": "string"}},
                        "when_to_prefer": {"type": "string"}
                    }
                }
            }
        }
    }

    prompt = [
        {
            "role": "system",
            "content": "You are a senior paper dir algorisium to code."
        },
        {
            "role": "user",
            "content": (
                f"paper_dir: {paper_dir}\n\n"
                "propose 3–5 DISTINCT implementation ROUTES.\n"
                "Return STRICTLY VALID JSON that matches this JSON Schema (NO extra text, no code fences):\n"
                f"{json.dumps(schema_hint)}\n\n"
                "Guidelines:\n"
                "- Make routes meaningfully different (baseline vs faithful reproduction vs RAG-enhanced vs TDD, etc.).\n"
                "- key_steps: 3–7 atomic steps.\n"
                "- required_tools: MCP servers/libs/repos/datasets if any.\n"
                "- success_criteria: objective checks (metrics/tests).\n"
            )
        }
    ]

    async with route_agent:
        llm = await route_agent.attach_llm(
            get_preferred_llm_class()
        )#这个没问题
        params = RequestParams(max_tokens=4096, temperature=0.2)
        raw = await llm.generate_str(message=prompt, request_params=params)

    # 清洗并解析 JSON（extract_clean_json 也在你文件里已有）
    json_text = extract_clean_json(raw)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        logger.error(f"[RoutePlanner] JSON 解析失败：{e}\nraw=\n{raw}")
        raise

    # 构造成数据类
    options: List[RouteOption] = []
    for idx, o in enumerate(data.get("options", []), start=1):
        options.append(RouteOption(
            id=str(o.get("id", idx)),
            title=o.get("title", ""),
            summary=o.get("summary", ""),
            key_steps=[str(s) for s in o.get("key_steps", [])],
            required_tools=[str(s) for s in o.get("required_tools", [])],
            risk_notes=o.get("risk_notes", ""),
            success_criteria=[str(s) for s in o.get("success_criteria", [])],
            when_to_prefer=o.get("when_to_prefer", ""),
        ))

    route_opts = RouteOptions(task_brief=data.get("task_brief", ""), options=options)

    if not route_opts.options:
        raise ValueError("[RoutePlanner] llm return nothing,no available routes")

    return route_opts


async def _generate_route_options_for_chat(user_input: str, logger, history_ctx: str="") -> RouteOptions:
    """main function to realize user-in-loop function,a route planning agent give suggestions and
        several impletation plan on realizing the user's requirement"""

    route_agent = Agent(
        name="RoutePlannerAgent",
        instruction=CODE_PLANNING_PROMPT,
        server_names=[],                 # ✅ 规划阶段禁用工具注入（更稳定）
    )

    schema_json = json.dumps(SCHEMA_HINT, ensure_ascii=False)
    prompt = (
        "You are a senior requirement-to-code planner.\n\n"
        f"USER REQUIREMENT:\n{user_input}\n\n"
        f"CONTEXT FROM PRIOR DIALOG (if any):\n{history_ctx}\n\n"
        "Propose 3–5 DISTINCT implementation ROUTES.\n"
        "Return STRICTLY VALID JSON ONLY (no extra text / no code fences) matching this JSON Schema:\n"
        f"{schema_json}\n\n"
        "Guidelines:\n"
        "- Make routes meaningfully different (baseline vs faithful replication vs RAG-enhanced vs TDD, etc.).\n"
        "- key_steps: 3–7 atomic steps.\n"
        "- required_tools: MCP servers/libs/repos/datasets if any.\n"
        "- success_criteria: objective checks (metrics/tests).\n"
    )

    async with route_agent:
        llm = await route_agent.attach_llm(get_preferred_llm_class())
        params = RequestParams(max_tokens=4096, temperature=0.2)
        raw = await llm.generate_str(message=prompt, request_params=params)

    json_text = extract_clean_json(raw)
    data = json.loads(json_text)

    options: List[RouteOption] = []
    for idx, o in enumerate(data.get("options", []), start=1):
        options.append(RouteOption(
            id=str(o.get("id", idx)),
            title=o.get("title", ""),
            summary=o.get("summary", ""),
            key_steps=[str(s) for s in o.get("key_steps", [])],
            required_tools=[str(s) for s in o.get("required_tools", [])],
            risk_notes=o.get("risk_notes", ""),
            success_criteria=[str(s) for s in o.get("success_criteria", [])],
            when_to_prefer=o.get("when_to_prefer", ""),
        ))

    route_opts = RouteOptions(task_brief=data.get("task_brief", ""), options=options)
    if not route_opts.options:
        raise ValueError("[RoutePlanner] LLM returned no routes")
    return route_opts
async def _run_code_analyzer_for_selected_route(
    paper_dir: str, selected: RouteOption, logger
) -> str:
    """generaget final code impletation plan according to the route user and the router agent
    have decided together"""
    sys_msg = (
        "You generate ONLY the final implementation plan text (no JSON). "
        "You MUST strictly follow the provided ROUTE (steps/tools/criteria). "
        "Be concrete: file tree, modules, functions, TODOs, checkpoints, and tests."
    )

    planner_agent = Agent(
        name="SelectedRoutePlannerAgent",
        instruction=CODE_PLANNING_PROMPT + "\n\n" + sys_msg,  # ← 用上 sys_msg
        server_names=["brave"],
    )


    route_json = json.dumps({
        "title": selected.title,
        "summary": selected.summary,
        "key_steps": selected.key_steps,
        "required_tools": selected.required_tools,
        "success_criteria": selected.success_criteria,
        "risk_notes": selected.risk_notes,
        "when_to_prefer": selected.when_to_prefer,
    }, ensure_ascii=False, indent=2)

    message = f"""Please analyze the following coding requirements and generate a comprehensive implementation plan:
        User Requirements:
        {paper_dir}
        STRICTLY FOLLOW THIS SELECTED ROUTE (JSON):\n
        {route_json}\n\n
        Output: a single comprehensive implementation plan in Markdown or YAML.\n
        Do NOT output JSON. The plan must be directly consumable by the downstream code generator.
Please provide a detailed implementation plan that covers all aspects needed for successful development"""

    async with planner_agent:
        print("chat_planning: Connected to server, calling list_tools...")
        try:
            tools = await planner_agent.list_tools()

        except Exception as e:
            print(f"Failed to list tools: {e}")

        try:
            planner = await planner_agent.attach_llm(
                get_preferred_llm_class()
            )
            print("✅ LLM attached successfully")
        except Exception as e:
            print(f"❌ Failed to attach LLM: {e}")
            raise

        params = RequestParams(max_tokens=8192, temperature=0.2)
        plan_text = await planner.generate_str(message=message, request_params=params)
    return plan_text

async def run_chat_planning_agent(user_input: str, selected,logger) -> str:
    """
    Run the chat-based planning agent for user-provided coding requirements.

    This agent transforms user's coding description into a comprehensive implementation plan
    that can be directly used for code generation. It handles both academic and engineering
    requirements with intelligent context adaptation.

    Args:
        user_input: User's coding requirements and description
        logger: Logger instance for logging information

    Returns:
        str: Comprehensive implementation plan in YAML format
    """
    try:
        print("💬 Starting chat-based planning agent...")
        print(f"Input length: {len(user_input) if user_input else 0}")
        print(f"Input preview: {user_input[:200] if user_input else 'None'}...")

        if not user_input or user_input.strip() == "":
            raise ValueError(
                "Empty or None user_input provided to run_chat_planning_agent"
            )

        # Create the chat planning agent
        chat_planning_agent = Agent(
            name="ChatPlanningAgent",
            instruction=CHAT_AGENT_PLANNING_PROMPT,
            server_names=[
            "filesystem"
            ],  # Add tools if needed for web search or other capabilities
        )

        async with chat_planning_agent:
            print("chat_planning: Connected to server, calling list_tools...")
            try:
                tools = await chat_planning_agent.list_tools()
                #print(
                #    "Tools available:",
                #    tools.model_dump() if hasattr(tools, "model_dump") else str(tools),
                #)
            except Exception as e:
                print(f"Failed to list tools: {e}")

            try:
                planner = await chat_planning_agent.attach_llm(
                    get_preferred_llm_class()
                )
                print("✅ LLM attached successfully")
            except Exception as e:
                print(f"❌ Failed to attach LLM: {e}")
                raise

            # Set higher token output for comprehensive planning
            planning_params = RequestParams(
                max_tokens=8192,  # Higher token limit for detailed plans
                temperature=0.2,  # Lower temperature for more structured output
            )

            print(
                f"🔄 Making LLM request with params: max_tokens={planning_params.max_tokens}, temperature={planning_params.temperature}"
            )
            route_json = f"""
                "title": {selected.title},
                "summary": {selected.summary},
                "key_steps": {selected.key_steps},
                "required_tools": {selected.required_tools},
                "success_criteria": {selected.success_criteria},
                "risk_notes": {selected.risk_notes},
                "when_to_prefer": {selected.when_to_prefer},
            """

            print(str(route_json))
            # Format the input message for the agent
            formatted_message = f"""Please analyze the following coding requirements and generate a comprehensive implementation plan:

User Requirements:
{user_input}
STRICTLY FOLLOW THIS  ROUTE (JSON):\n
                    {route_json}\n\n
Please provide a detailed implementation plan that covers all aspects needed for successful development."""

            try:
                raw_result = await planner.generate_str(
                    message=formatted_message, request_params=planning_params
                )

                print("✅ Planning request completed")
                print(f"Raw result type: {type(raw_result)}")
                print(f"Raw result length: {len(raw_result) if raw_result else 0}")

                if not raw_result:
                    print("❌ CRITICAL: raw_result is empty or None!")
                    raise ValueError("Chat planning agent returned empty result")

            except Exception as e:
                print(f"❌ Planning generation failed: {e}")
                print(f"Exception type: {type(e)}")
                raise

            # Log to SimpleLLMLogger
            if hasattr(logger, "log_response"):
                logger.log_response(
                    raw_result, model="ChatPlanningAgent", agent="ChatPlanningAgent"
                )

            if not raw_result or raw_result.strip() == "":
                print("❌ CRITICAL: Planning result is empty!")
                raise ValueError("Chat planning agent produced empty output")

            print("🎯 Chat planning completed successfully")
            print(f"Planning result preview: {raw_result[:500]}...")

            return raw_result

    except Exception as e:
        print(f"❌ run_chat_planning_agent failed: {e}")
        print(f"Exception details: {type(e).__name__}: {str(e)}")
        raise

def _summarize_routes_for_context(route_opts: RouteOptions) -> str:
    """把上轮路线压成简洁摘要，供下一轮作为上下文注入。"""
    lines = [f"- [{r.id}] {r.title}: {r.summary}" for r in route_opts.options]
    return "Previous route options:\n" + "\n".join(lines)

async def run_user_in_loop(user_input: str, logger, interactive: bool = True, max_rounds: int = 5):
    """
    返回 (selected_route: RouteOption, final_route_options: RouteOptions, history_text: str)
    """
    history_notes: List[str] = []   # 用户所有补充/偏好
    prior_routes_summary = ""       # 逐轮路线摘要滚动注入
    curr_opts: Optional[RouteOptions] = None

    for round_id in range(1, max_rounds + 1):
        # 组合历史上下文
        history_ctx = ""
        if history_notes:
            history_ctx += "User additional constraints/preferences:\n" + "\n".join(
                f"- {h}" for h in history_notes
            ) + "\n\n"
        if prior_routes_summary:
            history_ctx += prior_routes_summary + "\n"

        # 生成路线
        curr_opts = await _generate_route_options_for_chat(user_input, logger, history_ctx=history_ctx)

        if not interactive:
            # 非交互：可以按启发式选一个（例如优先包含 tests/TDD 的方案）
            chosen = _auto_pick_route(curr_opts)
            return chosen, curr_opts, history_ctx

        # 交互：打印并让用户选择
        print("\n===== Round", round_id, "Route Options =====")
        idx = None
        while True:
            for i, r in enumerate(curr_opts.options, 1):
                print(f"🎯{i}. {r.title} :: {r.summary}")
                print(f"🤖   Steps: {'; '.join(r.key_steps[:4])}{' ...' if len(r.key_steps) > 4 else ''}")
                print(f"🤖  Tools: {', '.join(r.required_tools)}")
                print(f"🤖   Risks: {r.risk_notes}")
                print(f"🤖   Prefer when: {r.when_to_prefer}")
                print("\n")
                print("=======================================================")
            raw = input("Choose 1..{0}, or 'r' to regenerate with new hints: ".format(len(curr_opts.options))).strip()
            if raw.lower() == "r":
                extra = input("Add/modify your requirement (press Enter to keep empty): ").strip()
                if extra:
                    history_notes.append(extra)
                    print("new requirement received ,rethinking...")
                # 记录上一轮的路线摘要，以便下一轮参考
                prior_routes_summary = _summarize_routes_for_context(curr_opts)
                break  # 跳出 while, 进入下一轮
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(curr_opts.options):
                    break
            except Exception:
                pass
            print("Invalid input, please retry.")

        if idx is not None:
            selected = curr_opts.options[idx]
            # 把选择也写入历史，便于后续链路使用
            history_notes.append(f"User selected route [{selected.id}] {selected.title}")
            history_text = (history_ctx + "\n" + _summarize_routes_for_context(curr_opts)).strip()
            return selected, curr_opts, history_text

    # 超出 max_rounds，回退为默认选 1
    assert curr_opts is not None
    print("⚠️ Reached max rounds; default choose #1")
    return curr_opts.options[0], curr_opts, (prior_routes_summary or "").strip()

def _auto_pick_route(route_opts: RouteOptions) -> RouteOption:
    # 简单启发：优先含 'test'/'tdd' 的方案，否则选 1
    for r in route_opts.options:
        joined = " ".join([r.title, r.summary] + r.key_steps).lower()
        if "test" in joined or "tdd" in joined:
            return r
    return route_opts.options[0]
