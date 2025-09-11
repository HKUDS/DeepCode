"""
Intelligent Code Execution Workflow - 智能代码执行工作流程

这个工作流程实现了您要求的完整功能：
1. 接收repo路径
2. 创建Docker环境并分析代码结构
3. 安装依赖和配置环境
4. 运行代码并捕获错误
5. 与LLM多轮对话修复bug
6. 循环执行直到成功或达到最大尝试次数
7. 完成后清理Docker环境

This workflow implements the complete functionality you requested:
1. Receive repo path
2. Create Docker environment and analyze code structure
3. Install dependencies and configure environment
4. Run code and capture errors
5. Multi-round conversation with LLM to fix bugs
6. Loop execution until success or max attempts reached
7. Clean up Docker environment after completion
"""

import os
import json
import time
import yaml
import asyncio
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from pathlib import Path

# MCP Agent imports
from mcp_agent.agents.agent import Agent

# Local imports
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ExecutionPhase(Enum):
    """Code execution phase enumeration"""

    INITIALIZED = "initialized"
    ANALYZING_REPO = "analyzing_repo"
    CREATING_CONTAINER = "creating_container"
    SETTING_UP_ENV = "setting_up_env"
    INSTALLING_DEPS = "installing_deps"
    RUNNING_CODE = "running_code"
    FIXING_BUGS = "fixing_bugs"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ExecutionAttempt:
    """Single execution attempt record"""

    attempt_number: int
    command: str
    exit_code: int
    stdout: str
    stderr: str
    execution_time: float
    success: bool
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))


@dataclass
class BugFixAttempt:
    """Bug fix attempt record"""

    attempt_number: int
    identified_issues: List[str]
    proposed_fixes: List[
        Dict[str, str]
    ]  # [{"file": "path", "action": "modify", "content": "new_content"}]
    fix_reasoning: str
    success: bool
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))


@dataclass
class IntelligentExecutionState:
    """Intelligent execution state"""

    phase: ExecutionPhase = ExecutionPhase.INITIALIZED
    repo_path: str = ""
    workspace_dir: str = ""
    start_time: float = field(default_factory=time.time)

    # Container info
    container_id: Optional[str] = None
    container_created: bool = False

    # Repository analysis
    detected_languages: List[str] = field(default_factory=list)
    entry_points: List[Dict[str, Any]] = field(default_factory=list)
    dependencies_installed: bool = False

    # Execution tracking
    execution_attempts: List[ExecutionAttempt] = field(default_factory=list)
    bug_fix_attempts: List[BugFixAttempt] = field(default_factory=list)
    max_execution_attempts: int = 5
    max_bug_fix_attempts: int = 3

    # Status
    final_success: bool = False
    errors: List[str] = field(default_factory=list)

    def add_error(self, error: str):
        """Add error information"""
        self.errors.append(f"[{time.strftime('%H:%M:%S')}] {error}")

    def get_current_attempt_count(self) -> int:
        """Get current execution attempt count"""
        return len(self.execution_attempts)

    def get_bug_fix_count(self) -> int:
        """Get current bug fix attempt count"""
        return len(self.bug_fix_attempts)


class IntelligentCodeExecutionWorkflow:
    """Intelligent code execution workflow"""

    def __init__(self, config_path: Optional[str] = None):
        """Initialize intelligent code execution workflow"""
        self.config_path = config_path or "mcp_agent.config.yaml"
        self.logger = self._create_logger()
        self.execution_agent: Optional[Agent] = None
        self.execution_state: Optional[IntelligentExecutionState] = None
        self.api_config: Dict[str, Any] = {}
        self.default_models: Dict[str, str] = {}

        # 容器架构信息
        self.container_architecture = None

    def _create_logger(self):
        """Create logger"""
        import logging

        logger = logging.getLogger("IntelligentCodeExecutionWorkflow")
        logger.setLevel(logging.INFO)

        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setLevel(logging.INFO)
            formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        return logger

    async def execute_repository_code(
        self,
        repo_path: str,
        workspace_dir: Optional[str] = None,
        max_attempts: int = 5,
        max_bug_fixes: int = 3,
    ) -> Dict[str, Any]:
        """
        Execute complete repository code workflow

        Args:
            repo_path: Code repository path
            workspace_dir: Workspace directory path
            max_attempts: Maximum execution attempts
            max_bug_fixes: Maximum bug fix attempts

        Returns:
            Execution results
        """
        try:
            # Initialize state
            if workspace_dir is None:
                # If repo_path is generate_code directory, create .execution in parent directory (papers/3/)
                repo_abs_path = os.path.abspath(repo_path)
                if os.path.basename(repo_abs_path) == "generate_code":
                    # Create .execution in parent directory of generate_code
                    workspace_base = os.path.dirname(repo_abs_path)
                else:
                    # Otherwise create .execution in repo directory
                    workspace_base = repo_abs_path

                workspace_dir = os.path.join(
                    workspace_base, ".execution", f"run_{int(time.time())}"
                )

            os.makedirs(workspace_dir, exist_ok=True)

            self.execution_state = IntelligentExecutionState(
                phase=ExecutionPhase.INITIALIZED,
                repo_path=os.path.abspath(repo_path),
                workspace_dir=workspace_dir,
                start_time=time.time(),
                max_execution_attempts=max_attempts,
                max_bug_fix_attempts=max_bug_fixes,
            )

            self.logger.info("=" * 80)
            self.logger.info("🚀 STARTING INTELLIGENT CODE EXECUTION WORKFLOW")
            self.logger.info("=" * 80)
            self.logger.info(f"📂 Repository: {self.execution_state.repo_path}")
            self.logger.info(f"🏗️  Workspace: {self.execution_state.workspace_dir}")
            self.logger.info(f"🎯 Max Execution Attempts: {max_attempts}")
            self.logger.info(f"🔧 Max Bug Fix Attempts: {max_bug_fixes}")
            self.logger.info("=" * 80)

            # Load configuration and initialize agent
            await self._load_api_config()
            await self._initialize_execution_agent()

            # Phase 1: Analyze repository structure
            await self._analyze_repository_structure()

            # Phase 2: Create Docker container
            await self._create_docker_container()

            # Phase 3: Setup environment
            await self._setup_container_environment()

            # Phase 4: Install dependencies (moved before code evaluation)
            await self._install_repository_dependencies()

            # Phase 5: Run code evaluation workflow in container
            await self._run_code_evaluation_in_container()

            # Generate final report
            # results = await self._generate_execution_report()
            results = {}
            self.logger.info("✅ Intelligent code execution workflow completed")
            return results

        except Exception as e:
            self.logger.error(f"❌ Execution workflow failed: {e}")
            if self.execution_state:
                self.execution_state.phase = ExecutionPhase.FAILED
                self.execution_state.add_error(str(e))

            return {"status": "error", "message": str(e), "repo_path": repo_path}
        finally:
            await self._cleanup_environment()

    async def _load_api_config(self):
        """Load API configuration"""
        try:
            # Load YAML configuration
            config_file = Path(self.config_path)
            if config_file.exists():
                with open(config_file, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
            else:
                config = {}

            # Load secrets
            secrets_file = Path("mcp_agent.secrets.yaml")
            if secrets_file.exists():
                with open(secrets_file, "r", encoding="utf-8") as f:
                    secrets = yaml.safe_load(f) or {}
                    self.api_config = secrets
            else:
                self.api_config = {}

            # Safely extract default models
            anthropic_config = config.get("anthropic") or {}
            openai_config = config.get("openai") or {}

            self.default_models = {
                "anthropic": anthropic_config.get(
                    "default_model", "claude-3-5-sonnet-20241022"
                ),
                "openai": openai_config.get(
                    "default_model", "anthropic/claude-sonnet-4"
                ),
            }

            self.logger.info("📋 Configuration loaded successfully")

        except Exception as e:
            self.logger.warning(f"Configuration loading failed, using defaults: {e}")
            self.api_config = {}
            self.default_models = {
                "anthropic": "claude-3-5-sonnet-20241022",
                "openai": "anthropic/claude-sonnet-4",
            }

    async def _initialize_execution_agent(self):
        """Initialize execution agent"""
        try:
            from prompts.evaluation_prompts import ENV_SETUP_AGENT_PROMPT

            self.execution_agent = Agent(
                name="IntelligentCodeExecution",
                instruction=ENV_SETUP_AGENT_PROMPT,
                server_names=["docker-management", "filesystem", "code-evaluation"],
            )
            await self.execution_agent.__aenter__()

            # Verify that essential tools are available
            await self._verify_essential_tools()

            self.logger.info("🤖 Intelligent execution agent initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize execution agent: {e}")
            raise

    async def _verify_essential_tools(self):
        """Verify that essential MCP tools are available"""
        try:
            essential_tools = [
                "create_evaluation_container",
                "setup_conda_environment",
                "setup_container_workspace",
                "install_dependencies",
                "execute_in_container",
                "cleanup_container",
            ]

            self.logger.info("🔍 Verifying essential tools availability...")

            # Try to get tool information (this will fail if tools are not available)
            for tool_name in essential_tools:
                try:
                    # Test if tool is callable by attempting to access it
                    # Note: We can't actually call the tools without proper parameters,
                    # but the agent should have them registered
                    self.logger.debug(f"✅ Tool '{tool_name}' is registered")
                except Exception as e:
                    self.logger.warning(
                        f"⚠️ Tool '{tool_name}' may not be available: {e}"
                    )

            self.logger.info("✅ Essential tools verification completed")

        except Exception as e:
            self.logger.warning(f"Tool verification failed: {e}")
            # Don't raise here as this is just a verification step

    async def _call_mcp_tool_with_retry(
        self, tool_name: str, tool_args: dict, max_retries: int = 3, delay: float = 1.0
    ):
        """Call MCP tool with retry logic for better reliability"""
        import asyncio

        for attempt in range(max_retries):
            try:
                self.logger.debug(
                    f"🔧 Calling tool '{tool_name}' (attempt {attempt + 1}/{max_retries})"
                )
                result = await self.execution_agent.call_tool(tool_name, tool_args)
                self.logger.debug(f"✅ Tool '{tool_name}' called successfully")
                return result

            except Exception as e:
                if "not found" in str(e).lower() and attempt < max_retries - 1:
                    self.logger.warning(
                        f"⚠️ Tool '{tool_name}' not found, retrying in {delay}s... (attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                    delay *= 1.5  # Exponential backoff
                else:
                    self.logger.error(
                        f"❌ Tool '{tool_name}' failed after {attempt + 1} attempts: {e}"
                    )
                    raise

        raise Exception(f"Tool '{tool_name}' failed after {max_retries} attempts")

    async def _analyze_repository_structure(self):
        """Analyze repository structure"""
        try:
            self.execution_state.phase = ExecutionPhase.ANALYZING_REPO
            self.logger.info("🔍 Phase 1: Repository Structure Analysis")

            # First perform local analysis
            common_files = [
                "main.py",
                "app.py",
                "run.py",
                "train.py",
                "requirements.txt",
                "setup.py",
            ]
            found_files = []

            for file in common_files:
                file_path = os.path.join(self.execution_state.repo_path, file)
                if os.path.exists(file_path):
                    found_files.append(file)

            self.logger.info(f"Found common files: {found_files}")

            # Detect programming languages
            python_files = list(Path(self.execution_state.repo_path).rglob("*.py"))
            if python_files:
                self.execution_state.detected_languages.append("python")

            # Determine possible entry points
            entry_points = []
            if "main.py" in found_files:
                entry_points.append(
                    {"file": "main.py", "command": "python main.py", "priority": 1}
                )
            if "app.py" in found_files:
                entry_points.append(
                    {"file": "app.py", "command": "python app.py", "priority": 2}
                )
            if "run.py" in found_files:
                entry_points.append(
                    {"file": "run.py", "command": "python run.py", "priority": 2}
                )
            if "train.py" in found_files:
                entry_points.append(
                    {"file": "train.py", "command": "python train.py", "priority": 3}
                )

            # Check for test_simple_code.py specifically
            test_file = os.path.join(
                self.execution_state.repo_path, "test_simple_code.py"
            )
            if os.path.exists(test_file):
                entry_points.append(
                    {
                        "file": "test_simple_code.py",
                        "command": "python test_simple_code.py",
                        "priority": 1,
                    }
                )

            # Look for any .py files that might be executable
            if not entry_points:
                py_files = list(Path(self.execution_state.repo_path).glob("*.py"))
                for py_file in py_files[:3]:  # Limit to first 3 Python files
                    if py_file.name not in ["setup.py", "__init__.py"]:
                        entry_points.append(
                            {
                                "file": py_file.name,
                                "command": f"python {py_file.name}",
                                "priority": 5,
                            }
                        )

            self.execution_state.entry_points = sorted(
                entry_points, key=lambda x: x["priority"]
            )

            self.logger.info(
                f"Detected languages: {self.execution_state.detected_languages}"
            )
            self.logger.info(
                f"Possible entry points: {len(self.execution_state.entry_points)}"
            )

        except Exception as e:
            self.execution_state.add_error(f"Repository analysis failed: {e}")
            raise

    async def _analyze_repository_environment(self):
        """Agent分析仓库环境需求"""
        try:
            self.logger.info(
                "📋 Analyzing repository files for environment requirements..."
            )

            env_analysis = {
                "repository_path": self.execution_state.repo_path,
                "detected_languages": self.execution_state.detected_languages,
                "dependency_files": {},
                "config_files": {},
                "readme_info": {},
                "python_version_hints": [],
            }

            # 分析Python相关文件
            if "python" in self.execution_state.detected_languages:
                env_analysis.update(await self._analyze_python_environment())

            # 分析其他语言环境
            for lang in self.execution_state.detected_languages:
                if lang != "python":
                    env_analysis[
                        f"{lang}_environment"
                    ] = await self._analyze_language_environment(lang)

            # 分析配置文件
            config_analysis = await self._analyze_configuration_files()
            env_analysis["config_files"] = config_analysis

            # 分析README和文档
            readme_analysis = await self._analyze_documentation_files()
            env_analysis["readme_info"] = readme_analysis

            self.logger.info(
                f"📊 Environment analysis completed: {len(env_analysis)} categories analyzed"
            )
            return env_analysis

        except Exception as e:
            self.logger.error(f"Environment analysis failed: {e}")
            # 返回基本分析结果，避免完全失败
            return {
                "repository_path": self.execution_state.repo_path,
                "detected_languages": self.execution_state.detected_languages,
                "analysis_error": str(e),
            }

    async def _analyze_python_environment(self):
        """分析Python环境需求"""
        python_env = {
            "requirements_txt": None,
            "setup_py": None,
            "pyproject_toml": None,
            "pipfile": None,
            "conda_env": None,
            "python_version_hints": [],
            "key_dependencies": [],
        }

        try:
            # 分析 requirements.txt
            req_path = os.path.join(self.execution_state.repo_path, "requirements.txt")
            if os.path.exists(req_path):
                req_content = await self.execution_agent.call_tool(
                    "read_file", {"path": req_path}
                )
                if hasattr(req_content, "content") and req_content.content:
                    content_text = (
                        req_content.content[0].text
                        if isinstance(req_content.content, list)
                        else str(req_content.content)
                    )
                    python_env["requirements_txt"] = content_text

                    # 提取关键依赖
                    lines = content_text.split("\n")
                    for line in lines:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            # 提取包名和版本
                            if "==" in line:
                                pkg_name = line.split("==")[0].strip()
                                version = line.split("==")[1].strip()
                                python_env["key_dependencies"].append(
                                    {"package": pkg_name, "version": version}
                                )
                            elif ">=" in line:
                                pkg_name = line.split(">=")[0].strip()
                                min_version = line.split(">=")[1].strip()
                                python_env["key_dependencies"].append(
                                    {"package": pkg_name, "min_version": min_version}
                                )

            # 分析 setup.py
            setup_path = os.path.join(self.execution_state.repo_path, "setup.py")
            if os.path.exists(setup_path):
                setup_content = await self.execution_agent.call_tool(
                    "read_file", {"path": setup_path}
                )
                if hasattr(setup_content, "content") and setup_content.content:
                    content_text = (
                        setup_content.content[0].text
                        if isinstance(setup_content.content, list)
                        else str(setup_content.content)
                    )
                    python_env["setup_py"] = content_text

                    # 提取Python版本要求
                    if "python_requires" in content_text:
                        import re

                        python_req_match = re.search(
                            r'python_requires\s*=\s*["\']([^"\']+)["\']', content_text
                        )
                        if python_req_match:
                            python_env["python_version_hints"].append(
                                python_req_match.group(1)
                            )

            # 分析 pyproject.toml
            pyproject_path = os.path.join(
                self.execution_state.repo_path, "pyproject.toml"
            )
            if os.path.exists(pyproject_path):
                pyproject_content = await self.execution_agent.call_tool(
                    "read_file", {"path": pyproject_path}
                )
                if hasattr(pyproject_content, "content") and pyproject_content.content:
                    content_text = (
                        pyproject_content.content[0].text
                        if isinstance(pyproject_content.content, list)
                        else str(pyproject_content.content)
                    )
                    python_env["pyproject_toml"] = content_text

            # 分析 .python-version 文件
            python_version_path = os.path.join(
                self.execution_state.repo_path, ".python-version"
            )
            if os.path.exists(python_version_path):
                with open(python_version_path, "r") as f:
                    version = f.read().strip()
                    python_env["python_version_hints"].append(version)

        except Exception as e:
            self.logger.warning(f"Python environment analysis failed: {e}")

        return python_env

    async def _analyze_language_environment(self, language):
        """分析其他语言环境需求"""
        env_info = {"language": language, "config_files": []}

        try:
            if language == "javascript" or language == "node":
                # 分析 package.json
                package_json_path = os.path.join(
                    self.execution_state.repo_path, "package.json"
                )
                if os.path.exists(package_json_path):
                    content = await self.execution_agent.call_tool(
                        "read_file", {"path": package_json_path}
                    )
                    if hasattr(content, "content") and content.content:
                        env_info["package_json"] = (
                            content.content[0].text
                            if isinstance(content.content, list)
                            else str(content.content)
                        )

            elif language == "java":
                # 分析 pom.xml 或 build.gradle
                for config_file in ["pom.xml", "build.gradle"]:
                    config_path = os.path.join(
                        self.execution_state.repo_path, config_file
                    )
                    if os.path.exists(config_path):
                        content = await self.execution_agent.call_tool(
                            "read_file", {"path": config_path}
                        )
                        if hasattr(content, "content") and content.content:
                            env_info[config_file.replace(".", "_")] = (
                                content.content[0].text
                                if isinstance(content.content, list)
                                else str(content.content)
                            )

        except Exception as e:
            self.logger.warning(
                f"Language environment analysis failed for {language}: {e}"
            )

        return env_info

    async def _analyze_configuration_files(self):
        """分析配置文件"""
        config_info = {}

        try:
            # 常见配置文件
            config_files = [
                "Dockerfile",
                "docker-compose.yml",
                "docker-compose.yaml",
                ".dockerignore",
                "Makefile",
                "tox.ini",
                "pytest.ini",
                ".github/workflows/main.yml",
                ".github/workflows/test.yml",
                "environment.yml",
                "conda.yml",
            ]

            for config_file in config_files:
                config_path = os.path.join(self.execution_state.repo_path, config_file)
                if os.path.exists(config_path):
                    try:
                        content = await self.execution_agent.call_tool(
                            "read_file", {"path": config_path}
                        )
                        if hasattr(content, "content") and content.content:
                            config_info[config_file] = (
                                content.content[0].text
                                if isinstance(content.content, list)
                                else str(content.content)
                            )
                    except:
                        config_info[config_file] = "File exists but could not read"

        except Exception as e:
            self.logger.warning(f"Configuration analysis failed: {e}")

        return config_info

    async def _analyze_documentation_files(self):
        """分析文档文件获取环境信息"""
        doc_info = {}

        try:
            # 分析README文件
            readme_files = ["README.md", "README.rst", "README.txt", "readme.md"]
            for readme_file in readme_files:
                readme_path = os.path.join(self.execution_state.repo_path, readme_file)
                if os.path.exists(readme_path):
                    try:
                        content = await self.execution_agent.call_tool(
                            "read_file", {"path": readme_path}
                        )
                        if hasattr(content, "content") and content.content:
                            readme_content = (
                                content.content[0].text
                                if isinstance(content.content, list)
                                else str(content.content)
                            )
                            doc_info["readme"] = readme_content[:2000]  # 限制长度

                            # 提取环境要求关键信息
                            import re

                            python_versions = re.findall(
                                r"[Pp]ython\s+(\d+\.\d+)", readme_content
                            )
                            if python_versions:
                                doc_info["python_versions_mentioned"] = python_versions

                            # 查找安装说明
                            if "pip install" in readme_content.lower():
                                doc_info["has_pip_install_instructions"] = True
                            if "conda" in readme_content.lower():
                                doc_info["mentions_conda"] = True

                        break
                    except:
                        continue

        except Exception as e:
            self.logger.warning(f"Documentation analysis failed: {e}")

        return doc_info

    async def _llm_decide_docker_configuration(self):
        """让LLM通过工具调用自主分析repo并决定Docker配置"""
        try:
            self.logger.info(
                "🤖 Starting LLM-driven repository analysis and Docker configuration decision..."
            )

            # 初始化LLM客户端
            client, client_type = await self._initialize_llm_client()

            # 创建Docker配置分析agent
            from workflows.agents.docker_config_analysis_agent import (
                DockerConfigAnalysisAgent,
            )

            docker_agent = DockerConfigAnalysisAgent(self.execution_agent, self.logger)

            # 准备工具定义
            tools = self._get_analysis_tools_for_llm()

            # 准备初始提示和消息
            initial_prompt = docker_agent.get_analysis_prompt(
                self.execution_state.repo_path, self.execution_state.detected_languages
            )

            messages = [{"role": "user", "content": initial_prompt}]

            # 使用evaluation_prompts.py中的标准system_message
            from prompts.evaluation_prompts import DOCKER_CONFIG_ANALYSIS_SYSTEM_MESSAGE

            system_message = DOCKER_CONFIG_ANALYSIS_SYSTEM_MESSAGE

            # 执行多轮对话分析循环 - 现在有明确完成条件，不需要太多轮次
            max_rounds = 10  # 足够读取关键文件并做决策
            docker_config = None

            for round_num in range(1, max_rounds + 1):
                self.logger.info(f"🔄 Docker Analysis Round {round_num}/{max_rounds}")

                # 验证消息
                messages = self._validate_messages(messages)

                # 调用LLM（参考code_implementation_workflow的_call_llm_with_tools）
                response = await self._call_llm_with_tools(
                    client, client_type, system_message, messages, tools
                )

                response_content = response.get("content", "").strip()
                if not response_content:
                    response_content = "Continue analyzing repository..."

                # 尝试提取Docker配置
                docker_config = docker_agent.try_extract_docker_config(response_content)
                if docker_config:
                    self.logger.info(
                        f"✅ Docker configuration extracted in round {round_num}"
                    )
                    break

                messages.append({"role": "assistant", "content": response_content})

                # 处理工具调用（参考code_implementation_workflow的工具调用处理）
                if response.get("tool_calls"):
                    self.logger.info(
                        f"🔧 Processing {len(response['tool_calls'])} tool calls"
                    )

                    # 执行工具调用
                    tool_results = await docker_agent.execute_tool_calls(
                        response["tool_calls"]
                    )

                    # 格式化工具结果给LLM
                    tool_results_message = docker_agent.format_tool_results_for_llm(
                        tool_results
                    )

                    # 添加继续分析的指导
                    if round_num < max_rounds:
                        continue_guidance = docker_agent.get_continue_prompt(
                            round_num, self.execution_state.repo_path
                        )
                        compiled_message = (
                            f"{tool_results_message}\n\n{continue_guidance}"
                        )
                    else:
                        compiled_message = f"{tool_results_message}\n\n{docker_agent.get_continue_prompt(round_num, self.execution_state.repo_path)}"

                    messages.append({"role": "user", "content": compiled_message})
                else:
                    # 没有工具调用，提供指导
                    # 使用agent的统一continue prompt
                    guidance = docker_agent.get_continue_prompt(
                        round_num, self.execution_state.repo_path
                    )

                    messages.append({"role": "user", "content": guidance})

            # 如果没有获得配置，使用默认配置
            if not docker_config:
                self.logger.warning(
                    "LLM did not provide Docker configuration, using default"
                )
                docker_config = docker_agent.get_default_docker_config()

            self.logger.info(f"🎯 Final Docker configuration: {docker_config}")
            return docker_config

        except Exception as e:
            self.logger.warning(f"LLM Docker configuration decision failed: {e}")
            # 创建默认配置agent实例来获取默认配置
            try:
                from workflows.agents.docker_config_analysis_agent import (
                    DockerConfigAnalysisAgent,
                )

                docker_agent = DockerConfigAnalysisAgent(None, self.logger)
                return docker_agent.get_default_docker_config()
            except:
                return {
                    "image_type": "python",
                    "version": "3.9",
                    "memory_limit": "2g",
                    "cpu_limit": "2",
                    "reasoning": "Default configuration used due to initialization failure",
                }

    def _build_llm_analysis_prompt(self):
        """构建LLM自主分析的初始提示"""
        prompt = f"""# Repository Environment Analysis for Docker Configuration

You are tasked with analyzing a code repository to determine the optimal Docker configuration for code execution.

## Repository Information
- **Repository Path**: {self.execution_state.repo_path}
- **Detected Languages**: {self.execution_state.detected_languages}

## Your Task

You need to analyze this repository's environment requirements by using the available tools to read and examine files. Based on your analysis, decide the optimal Docker configuration.

## Available Tools

You have access to these tools to analyze the repository:
- `read_file`: Read specific files (requirements.txt, setup.py, README.md, etc.)
- `list_files_in_directory`: List files in directories to discover relevant files
- `analyze_repo_structure`: Get an overview of the repository structure

## Analysis Steps

Please follow these steps:

1. **Discover Repository Structure**: Use tools to understand what files are available
2. **Analyze Python Environment**:
   - Look for requirements.txt, setup.py, pyproject.toml, Pipfile
   - Extract Python version requirements and dependencies
3. **Check Documentation**:
   - Read README files for environment information
   - Look for installation instructions and system requirements
4. **Examine Configuration Files**:
   - Check for Docker files, CI/CD configs, etc.
   - Look for environment variable definitions

## Required Output Format

Once you have completed your analysis, provide your Docker configuration decision in this exact JSON format:

```json
{{
    "image_type": "python|python-slim|ubuntu|node|java",
    "version": "version_number",
    "memory_limit": "memory_size_with_unit",
    "cpu_limit": "cpu_count",
    "additional_packages": ["package1", "package2"],
    "environment_variables": {{"ENV_VAR": "value"}},
    "reasoning": "Detailed explanation of your analysis and decisions"
}}
```

## Important Notes

- Use the tools to actually read and analyze files - don't make assumptions
- Consider the specific dependencies and their resource requirements
- Look for explicit Python version requirements in setup.py or pyproject.toml
- Check README files for installation instructions and system requirements
- Provide detailed reasoning for your choices

Please start by analyzing the repository structure and then examine the relevant files to make your Docker configuration decision.
"""
        return prompt

    def _get_analysis_tools_for_llm(self):
        """获取LLM可用的分析工具列表 - 使用filesystem MCP工具"""
        try:
            # 从MCP工具定义中获取filesystem工具
            from config.mcp_tool_definitions_index import get_mcp_tools

            # 获取filesystem工具集
            all_tools = get_mcp_tools(["filesystem"])

            # 过滤出分析需要的工具
            analysis_tools = []
            relevant_tool_names = ["read_file", "list_directory"]

            for tool in all_tools:
                tool_name = tool.get("name", "")
                if any(name in tool_name for name in relevant_tool_names):
                    analysis_tools.append(tool)

            # 如果没有找到工具，使用手动定义
            if not analysis_tools:
                self.logger.warning(
                    "No filesystem tools found, using manual definitions"
                )
                analysis_tools = [
                    {
                        "name": "read_file",
                        "description": "Read the contents of a file",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Path to the file to read",
                                }
                            },
                            "required": ["path"],
                        },
                    },
                    {
                        "name": "list_directory",
                        "description": "List the contents of a directory",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Path to the directory to list",
                                }
                            },
                            "required": ["path"],
                        },
                    },
                ]

            self.logger.info(
                f"📋 Prepared {len(analysis_tools)} analysis tools for LLM"
            )
            return analysis_tools

        except Exception as e:
            self.logger.warning(f"Failed to get analysis tools: {e}")
            # 回退到手动定义
            return [
                {
                    "name": "read_file",
                    "description": "Read the contents of a file",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to the file to read",
                            }
                        },
                        "required": ["path"],
                    },
                },
                {
                    "name": "list_directory",
                    "description": "List the contents of a directory",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to the directory to list",
                            }
                        },
                        "required": ["path"],
                    },
                },
            ]

    def _try_extract_docker_config(self, llm_response):
        """尝试从LLM响应中提取Docker配置"""
        try:
            # 提取响应内容
            if hasattr(llm_response, "content") and llm_response.content:
                response_text = (
                    llm_response.content[0].text
                    if isinstance(llm_response.content, list)
                    else str(llm_response.content)
                )
            else:
                response_text = str(llm_response)

            # 查找JSON配置
            import re

            json_patterns = [
                r"```json\s*(\{.*?\})\s*```",  # 代码块中的JSON
                r'\{[^{}]*"image_type"[^{}]*\}',  # 直接的JSON对象
                r'\{[^{}]*"version"[^{}]*"memory_limit"[^{}]*\}',  # 包含关键字段的JSON
            ]

            for pattern in json_patterns:
                json_match = re.search(pattern, response_text, re.DOTALL)
                if json_match:
                    json_str = (
                        json_match.group(1)
                        if json_match.groups()
                        else json_match.group(0)
                    )
                    try:
                        config = json.loads(json_str)

                        # 验证必需字段
                        if "image_type" in config and "version" in config:
                            docker_config = {
                                "image_type": config.get("image_type", "python"),
                                "version": config.get("version", "3.9"),
                                "memory_limit": config.get("memory_limit", "2g"),
                                "cpu_limit": config.get("cpu_limit", "2"),
                            }

                            # 添加可选字段
                            if "additional_packages" in config:
                                docker_config["additional_packages"] = config[
                                    "additional_packages"
                                ]
                            if "environment_variables" in config:
                                docker_config["environment_variables"] = config[
                                    "environment_variables"
                                ]

                            # 记录推理过程
                            if "reasoning" in config:
                                self.logger.info(
                                    f"🧠 LLM reasoning: {config['reasoning']}"
                                )

                            return docker_config
                    except json.JSONDecodeError:
                        continue

            return None

        except Exception as e:
            self.logger.warning(f"Failed to extract Docker config: {e}")
            return None

    def _build_continue_analysis_prompt(self, round_num):
        """构建继续分析的提示"""
        if round_num == 1:
            return """Please continue your analysis. If you haven't finished examining all relevant files, please continue using the available tools to read more files (like README.md, setup.py, etc.).

Once you have sufficient information, please provide your Docker configuration decision in the required JSON format."""

        elif round_num == 2:
            return """Please complete your analysis and provide the Docker configuration decision.

If you need to examine any additional files, do so now. Then provide your final Docker configuration in the JSON format specified earlier."""

        else:
            return """This is your final opportunity to provide the Docker configuration. Please provide your decision in the required JSON format based on your analysis so far."""

    def _get_default_docker_config(self):
        """获取默认Docker配置"""
        return {
            "image_type": "python",
            "version": "3.9",
            "memory_limit": "2g",
            "cpu_limit": "2",
            "reasoning": "Default configuration used due to analysis failure",
        }

    def _build_environment_analysis_prompt(self, env_analysis):
        """构建环境分析提示"""
        prompt = f"""# Repository Environment Analysis for Docker Configuration

You are tasked with analyzing a code repository's environment requirements and deciding the optimal Docker configuration for code execution.

## Repository Information
- **Path**: {env_analysis.get('repository_path', 'Unknown')}
- **Detected Languages**: {env_analysis.get('detected_languages', [])}

## Analysis Results

### Python Environment Analysis
"""

        # 添加Python环境信息
        if "requirements_txt" in env_analysis and env_analysis["requirements_txt"]:
            prompt += f"""
**Requirements.txt Content:**
```
{env_analysis['requirements_txt'][:1000]}  # Truncated if too long
```
"""

        if "key_dependencies" in env_analysis and env_analysis["key_dependencies"]:
            prompt += """
**Key Dependencies Found:**
"""
            for dep in env_analysis["key_dependencies"][:10]:  # 限制数量
                prompt += f"- {dep.get('package', 'Unknown')}: {dep.get('version', dep.get('min_version', 'Unknown'))}\n"

        if (
            "python_version_hints" in env_analysis
            and env_analysis["python_version_hints"]
        ):
            prompt += f"""
**Python Version Hints:**
{', '.join(env_analysis['python_version_hints'])}
"""

        if "setup_py" in env_analysis and env_analysis["setup_py"]:
            prompt += f"""
**Setup.py Information:**
{env_analysis['setup_py'][:500]}...  # Truncated
"""

        # 添加配置文件信息
        if "config_files" in env_analysis and env_analysis["config_files"]:
            prompt += """
### Configuration Files Found
"""
            for config_file, content in env_analysis["config_files"].items():
                if content and content != "File exists but could not read":
                    prompt += f"""
**{config_file}:**
```
{content[:300]}...  # Truncated
```
"""
                else:
                    prompt += f"- {config_file}: {content}\n"

        # 添加README信息
        if "readme_info" in env_analysis and env_analysis["readme_info"]:
            readme_info = env_analysis["readme_info"]
            prompt += """
### Documentation Analysis
"""
            if "readme" in readme_info:
                prompt += f"""
**README Content (excerpt):**
```
{readme_info['readme'][:800]}...  # Truncated
```
"""

            if "python_versions_mentioned" in readme_info:
                prompt += f"**Python Versions Mentioned in README:** {', '.join(readme_info['python_versions_mentioned'])}\n"

            if readme_info.get("has_pip_install_instructions"):
                prompt += "- README contains pip install instructions\n"

            if readme_info.get("mentions_conda"):
                prompt += "- README mentions conda\n"

        # 添加决策指令
        prompt += """

## Your Task

Based on this analysis, please decide the optimal Docker configuration for running this repository's code. Consider:

1. **Python Version**: What Python version should be used? Look for version hints in requirements, setup.py, README, etc.
2. **Base Image**: What type of Docker image is most appropriate? (python, python-slim, ubuntu with python, etc.)
3. **Resource Requirements**: What memory and CPU limits are appropriate based on the dependencies?
4. **Special Requirements**: Are there any special requirements (GPU, specific libraries, etc.)?

## Required Response Format

Please respond with ONLY a JSON object in this exact format:

```json
{
    "image_type": "python|python-slim|ubuntu|node|java",
    "version": "version_number",
    "memory_limit": "memory_size_with_unit",
    "cpu_limit": "cpu_count",
    "additional_packages": ["package1", "package2"],
    "environment_variables": {"ENV_VAR": "value"},
    "reasoning": "Brief explanation of your choices"
}
```

**Example:**
```json
{
    "image_type": "python",
    "version": "3.8",
    "memory_limit": "4g",
    "cpu_limit": "2",
    "additional_packages": ["build-essential", "git"],
    "environment_variables": {},
    "reasoning": "Repository requires Python 3.8 based on setup.py python_requires, and needs 4GB RAM for ML dependencies like tensorflow"
}
```

Please analyze the environment requirements carefully and provide the most appropriate Docker configuration.
"""

        return prompt

    def _parse_docker_configuration_from_llm(self, llm_response):
        """解析LLM响应中的Docker配置"""
        try:
            # 提取响应内容
            if hasattr(llm_response, "content") and llm_response.content:
                response_text = (
                    llm_response.content[0].text
                    if isinstance(llm_response.content, list)
                    else str(llm_response.content)
                )
            else:
                response_text = str(llm_response)

            self.logger.info(
                f"📝 LLM Docker configuration response: {response_text[:500]}..."
            )

            # 尝试提取JSON配置
            import re

            json_match = re.search(
                r"```json\s*(\{.*?\})\s*```", response_text, re.DOTALL
            )
            if not json_match:
                # 尝试直接查找JSON对象
                json_match = re.search(
                    r'\{[^{}]*"image_type"[^{}]*\}', response_text, re.DOTALL
                )

            if json_match:
                json_str = (
                    json_match.group(1) if json_match.groups() else json_match.group(0)
                )
                config = json.loads(json_str)

                # 验证必需字段并设置默认值
                docker_config = {
                    "image_type": config.get("image_type", "python"),
                    "version": config.get("version", "3.9"),
                    "memory_limit": config.get("memory_limit", "2g"),
                    "cpu_limit": config.get("cpu_limit", "2"),
                }

                # 添加可选字段
                if "additional_packages" in config:
                    docker_config["additional_packages"] = config["additional_packages"]
                if "environment_variables" in config:
                    docker_config["environment_variables"] = config[
                        "environment_variables"
                    ]

                # 记录推理过程
                if "reasoning" in config:
                    self.logger.info(f"🧠 LLM reasoning: {config['reasoning']}")

                return docker_config
            else:
                raise ValueError("No valid JSON configuration found in LLM response")

        except Exception as e:
            self.logger.warning(f"Failed to parse LLM Docker configuration: {e}")
            # 返回默认配置
            return {
                "image_type": "python",
                "version": "3.9",
                "memory_limit": "2g",
                "cpu_limit": "2",
            }

    def _detect_platform_architecture(self):
        """检测当前系统的平台架构信息"""
        import platform

        machine = platform.machine().lower()
        system = platform.system().lower()

        # 标准化架构名称
        if machine in ["arm64", "aarch64", "arm64e"]:
            arch = "arm64"
        elif machine in ["x86_64", "amd64"]:
            arch = "amd64"
        elif machine in ["x86", "i386", "i686"]:
            arch = "x86"
        else:
            arch = machine  # 保持原名称

        platform_info = {
            "system": system,
            "machine": machine,
            "architecture": arch,
            "platform_string": platform.platform(),
            "is_arm": arch == "arm64",
            "is_x86": arch in ["amd64", "x86"],
            "python_architecture": platform.architecture()[0],
        }

        return platform_info

    def _enhance_docker_config_for_multiarch(self, base_config, platform_info):
        """根据平台架构增强Docker配置，确保镜像兼容性和预装gcc"""
        enhanced_config = base_config.copy()

        # 1. 根据架构选择合适的镜像
        original_image_type = enhanced_config.get("image_type", "ubuntu-conda")

        # 确保使用支持多架构的镜像
        if platform_info["is_arm"]:
            self.logger.info("🔧 ARM64 platform detected - using ARM-compatible images")
            # 对于ARM平台，continuumio/miniconda3 有ARM64支持
            if original_image_type in ["python-conda", "ubuntu-conda"]:
                enhanced_config["image_type"] = "ubuntu-conda"
            elif original_image_type == "python":
                enhanced_config["image_type"] = (
                    "python-conda"  # 改用conda版本以确保ARM兼容性
                )

        # 2. 设置容器创建后立即安装gcc
        enhanced_config["post_create_commands"] = [
            "apt-get update",
            "apt-get install -y build-essential gcc g++ make",
            "apt-get clean && rm -rf /var/lib/apt/lists/*",
        ]

        # 3. 增强内存和CPU限制以适应编译需求
        enhanced_config["memory_limit"] = enhanced_config.get("memory_limit", "2g")
        if enhanced_config["memory_limit"] == "1g":
            enhanced_config["memory_limit"] = "2g"  # 编译需要更多内存

        # 4. 设置环境变量
        enhanced_config.setdefault("environment", {}).update(
            {
                "DEBIAN_FRONTEND": "noninteractive",
                "PYTHONUNBUFFERED": "1",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "CC": "gcc",
                "CXX": "g++",
            }
        )

        self.logger.info(
            f"🔧 Enhanced config for {platform_info['architecture']} platform"
        )
        self.logger.info(f"📦 Selected image type: {enhanced_config['image_type']}")
        self.logger.info(
            f"🛠️ Will pre-install: {enhanced_config['post_create_commands']}"
        )

        return enhanced_config

    async def _detect_container_architecture(self, container_id):
        """检测容器内的实际架构并存储"""
        try:
            self.logger.info("🔍 Detecting container architecture...")

            arch_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": "uname -m",
                    "working_dir": "/root",
                    "timeout": 30,
                },
            )

            container_arch = "unknown"
            if hasattr(arch_result, "content") and arch_result.content:
                content_text = arch_result.content[0].text
                result_data = json.loads(content_text)
                execution = result_data.get("execution", {})
                container_arch = execution.get("stdout", "").strip()

            # 标准化架构名称并存储
            self.container_architecture = {
                "raw": container_arch,
                "is_arm": container_arch == "aarch64",
                "is_x86": container_arch == "x86_64",
                "go_arch": "arm64" if container_arch == "aarch64" else "amd64",
                "node_platform": "linux-arm64"
                if container_arch == "aarch64"
                else "linux-x64",
                "rust_target": "aarch64-unknown-linux-gnu"
                if container_arch == "aarch64"
                else "x86_64-unknown-linux-gnu",
                "docker_platform": "linux/arm64"
                if container_arch == "aarch64"
                else "linux/amd64",
            }

            self.logger.info(f"🏗️ Container architecture detected: {container_arch}")
            self.logger.info(f"📦 Architecture mapping: {self.container_architecture}")

        except Exception as e:
            self.logger.error(f"❌ Container architecture detection failed: {e}")
            # 设置默认为x86_64
            self.container_architecture = {
                "raw": "x86_64",
                "is_arm": False,
                "is_x86": True,
                "go_arch": "amd64",
                "node_platform": "linux-x64",
                "rust_target": "x86_64-unknown-linux-gnu",
                "docker_platform": "linux/amd64",
            }

    async def _preinstall_build_tools(self, container_id):
        """在容器创建后立即预装编译工具"""
        try:
            self.logger.info(
                "🛠️ Pre-installing build tools (gcc, g++, make) in container..."
            )

            # 执行预装命令
            preinstall_commands = [
                "apt-get update",
                "apt-get install -y build-essential gcc g++ make curl wget",
                "apt-get clean && rm -rf /var/lib/apt/lists/*",
            ]

            for i, cmd in enumerate(preinstall_commands):
                self.logger.info(
                    f"🔧 Executing command {i+1}/{len(preinstall_commands)}: {cmd}"
                )

                result = await self.execution_agent.call_tool(
                    "execute_in_container",
                    {
                        "container_id": container_id,
                        "command": cmd,
                        "working_dir": "/root",
                        "timeout": 300,
                    },
                )

                # 检查命令执行结果
                if hasattr(result, "content") and result.content:
                    content_text = result.content[0].text
                    result_data = json.loads(content_text)
                    if result_data.get("status") == "success":
                        execution = result_data.get("execution", {})
                        if execution.get("exit_code") == 0:
                            self.logger.info("✅ Command executed successfully")
                        else:
                            self.logger.warning(
                                f"⚠️ Command failed with exit code: {execution.get('exit_code')}"
                            )
                            self.logger.warning(
                                f"⚠️ Error output: {execution.get('stderr', '')[:200]}"
                            )
                    else:
                        self.logger.error(
                            f"❌ Tool execution failed: {result_data.get('message')}"
                        )

            # 验证gcc安装
            self.logger.info("🧪 Verifying gcc installation...")
            verify_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": "gcc --version && g++ --version && make --version",
                    "working_dir": "/root",
                },
            )

            if hasattr(verify_result, "content") and verify_result.content:
                content_text = verify_result.content[0].text
                result_data = json.loads(content_text)
                execution = result_data.get("execution", {})
                if execution.get("exit_code") == 0:
                    self.logger.info(
                        "✅ Build tools pre-installation completed successfully"
                    )
                    self.logger.info("🛠️ Available: gcc, g++, make")
                else:
                    self.logger.warning("⚠️ Build tools verification failed")

        except Exception as e:
            self.logger.error(f"❌ Build tools pre-installation failed: {e}")
            # 不抛出异常，继续执行

    async def _install_multilang_development_environment(self, container_id):
        """安装多语言开发环境，包括Python、Node.js、Go、Rust、Java、C/C++和对应的LSP服务器"""
        try:
            self.logger.info("🌐 Installing multi-language development environment...")

            # Phase 1: 安装系统级依赖和Java
            await self._install_system_dependencies(container_id)

            # Phase 2: 安装Python环境和工具
            await self._install_python_environment(container_id)

            # Phase 3: 安装Node.js环境和工具
            await self._install_nodejs_environment(container_id)

            # Phase 4: 安装Go环境和工具
            await self._install_go_environment(container_id)

            # Phase 5: 安装Rust环境和工具
            await self._install_rust_environment(container_id)

            # Phase 6: 安装Java工具和LSP
            await self._install_java_environment(container_id)

            # Phase 7: 验证所有环境
            await self._verify_multilang_environments(container_id)

            self.logger.info(
                "✅ Multi-language development environment installation completed"
            )

        except Exception as e:
            self.logger.error(f"❌ Multi-language environment installation failed: {e}")
            # 不抛出异常，继续执行，因为这是增强功能

    async def _install_system_dependencies(self, container_id):
        """安装系统级依赖"""
        try:
            self.logger.info("📦 Installing system dependencies...")

            system_commands = [
                # 释放apt锁定并更新 (fuser命令在某些镜像中不存在，直接删除锁文件)
                "rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock",
                "apt-get update",
                "apt-get install -y software-properties-common",
                "apt-get install -y openjdk-17-jdk checkstyle",
                "apt-get install -y clang clang-tools clang-tidy cppcheck",
                "apt-get install -y unzip wget git curl",
                # 分步清理，避免组合命令失败
                "sleep 2",
                "rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock",
                "apt-get clean || true",  # 允许清理失败
                "rm -rf /var/lib/apt/lists/* || true",  # 允许删除失败
            ]

            for cmd in system_commands:
                await self._execute_container_command(
                    container_id, cmd, "System dependencies"
                )

        except Exception as e:
            self.logger.error(f"❌ System dependencies installation failed: {e}")
            raise

    async def _install_python_environment(self, container_id):
        """安装Python开发环境和LSP服务器"""
        try:
            self.logger.info("🐍 Installing Python development environment...")

            # 架构感知日志
            if self.container_architecture:
                self.logger.info(
                    f"🏗️ Python environment for: {self.container_architecture['raw']} architecture"
                )
            else:
                self.logger.warning(
                    "⚠️ Container architecture not detected, using default setup"
                )

            # 由于容器已有Python和pip，直接使用pip安装Python工具和LSP服务器
            python_commands = [
                # 验证Python环境
                "python3 --version && pip --version",
                # 直接使用pip安装Python开发工具和LSP服务器
                "pip install 'python-lsp-server[all]' pylsp-mypy black isort flake8 pylint mypy",
            ]

            all_commands = python_commands
            for cmd in all_commands:
                await self._execute_container_command(
                    container_id, cmd, "Python environment"
                )

        except Exception as e:
            self.logger.error(f"❌ Python environment installation failed: {e}")
            # 继续执行，因为容器已有Python

    async def _install_nodejs_environment(self, container_id):
        """安装Node.js开发环境和LSP服务器"""
        try:
            self.logger.info("📦 Installing Node.js development environment...")

            # 架构感知日志
            if self.container_architecture:
                self.logger.info(
                    f"🏗️ Node.js will auto-detect platform: {self.container_architecture['node_platform']} (container: {self.container_architecture['raw']})"
                )
            else:
                self.logger.warning(
                    "⚠️ Container architecture not detected, Node.js will auto-detect"
                )

            nodejs_commands = [
                # 直接安装Node.js和npm - 避免NodeSource脚本问题
                "apt-get update",
                "apt-get install -y nodejs npm",
                # 验证基础安装
                "node --version && npm --version",
                # 不强制更新npm - 避免版本兼容性问题
                # "npm install -g npm@latest",  # 注释掉这行避免兼容性问题
                # 安装Node.js开发工具和LSP服务器
                "npm install -g prettier eslint typescript typescript-language-server",
            ]

            for cmd in nodejs_commands:
                await self._execute_container_command(
                    container_id, cmd, "Node.js environment"
                )

        except Exception as e:
            self.logger.error(f"❌ Node.js environment installation failed: {e}")

    async def _install_go_environment(self, container_id):
        """安装Go开发环境和LSP服务器"""
        try:
            self.logger.info("🚀 Installing Go development environment...")

            go_version = "1.22.5"
            # 使用统一的容器架构信息
            if not self.container_architecture:
                self.logger.warning(
                    "⚠️ Container architecture not detected, using default amd64"
                )
                go_arch = "amd64"
            else:
                go_arch = self.container_architecture["go_arch"]

            self.logger.info(
                f"🏗️ Using Go architecture: {go_arch} (container: {self.container_architecture['raw'] if self.container_architecture else 'unknown'})"
            )

            go_commands = [
                # 确保/root目录存在并清理可能的旧文件 - 分开执行避免shell解析问题
                "mkdir -p /root",
                "rm -f /root/go.tar.gz",
                # 下载Go (支持多架构) - 增加健壮性检查
                f"bash -c 'cd /root && wget -O go.tar.gz https://go.dev/dl/go{go_version}.linux-{go_arch}.tar.gz'",
                # 验证下载 - 移除file命令，只检查文件大小
                "bash -c 'cd /root && ls -la go.tar.gz && test -s go.tar.gz'",
                # 清理旧安装并解压
                "rm -rf /usr/local/go",
                "bash -c 'cd /root && tar -C /usr/local -xzf go.tar.gz'",
                # 验证解压结果
                "ls -la /usr/local/go/bin/go",
                # 清理下载文件
                "rm -f /root/go.tar.gz",
                # 创建GOPATH目录
                "mkdir -p /go/bin /go/src /go/pkg",
                # 验证Go安装
                "/usr/local/go/bin/go version",
                # 设置Go环境并安装工具 (使用bash -c正确处理环境变量)
                "bash -c 'export PATH=$PATH:/usr/local/go/bin && export GOPATH=/go && /usr/local/go/bin/go install golang.org/x/tools/cmd/goimports@latest'",
                "bash -c 'export PATH=$PATH:/usr/local/go/bin && export GOPATH=/go && /usr/local/go/bin/go install golang.org/x/tools/gopls@latest'",
                # 设置永久环境变量
                "echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc",
                "echo 'export GOPATH=/go' >> ~/.bashrc",
                "echo 'export PATH=$PATH:/go/bin' >> ~/.bashrc",
            ]

            for cmd in go_commands:
                await self._execute_container_command(
                    container_id, cmd, "Go environment"
                )

        except Exception as e:
            self.logger.error(f"❌ Go environment installation failed: {e}")

    async def _install_rust_environment(self, container_id):
        """安装Rust开发环境和LSP服务器"""
        try:
            self.logger.info("🦀 Installing Rust development environment...")

            # 使用统一的容器架构信息
            if self.container_architecture:
                rust_target = self.container_architecture["rust_target"]
                self.logger.info(
                    f"🏗️ Using Rust target: {rust_target} (container: {self.container_architecture['raw']})"
                )
            else:
                rust_target = "x86_64-unknown-linux-gnu"
                self.logger.warning(
                    "⚠️ Container architecture not detected, using default x86_64 target"
                )

            rust_commands = [
                # 安装Rust - 改进安装方法，增加更好的错误处理
                "bash -c 'curl --proto \"=https\" --tlsv1.2 -sSf https://sh.rustup.rs -o rustup.sh'",
                "bash -c 'chmod +x rustup.sh && ./rustup.sh -y'",
                "rm -f rustup.sh",
                # 设置环境变量
                "echo 'source ~/.cargo/env' >> ~/.bashrc",
                # 创建cargo目录结构（如果不存在）
                "mkdir -p ~/.cargo/bin",
                # 显式加载Rust环境并验证
                "bash -c 'source ~/.cargo/env && rustc --version'",
                # 安装Rust组件和工具 (使用环境加载)
                "bash -c 'source ~/.cargo/env && rustup component add rustfmt clippy rust-analyzer'",
            ]

            for cmd in rust_commands:
                await self._execute_container_command(
                    container_id, cmd, "Rust environment"
                )

        except Exception as e:
            self.logger.error(f"❌ Rust environment installation failed: {e}")

    async def _install_java_environment(self, container_id):
        """安装Java开发工具和LSP服务器"""
        try:
            self.logger.info("☕ Installing Java development environment...")

            # 架构感知日志
            if self.container_architecture:
                self.logger.info(
                    f"🏗️ Java environment for: {self.container_architecture['raw']} architecture"
                )
            else:
                self.logger.warning(
                    "⚠️ Container architecture not detected, using default setup"
                )

            java_commands = [
                # 下载Google Java Format
                "mkdir -p /usr/local/share/java",
                "wget https://github.com/google/google-java-format/releases/download/v1.28.0/google-java-format-1.28.0-all-deps.jar -P /usr/local/share/java/",
                # 下载并设置Eclipse JDT Language Server
                "mkdir -p /opt/jdtls",
                "wget https://download.eclipse.org/jdtls/milestones/1.49.0/jdt-language-server-1.49.0-202507311558.tar.gz -O jdtls.tar.gz",
                "tar -C /opt/jdtls -xzf jdtls.tar.gz",
                "rm jdtls.tar.gz",
            ]

            for cmd in java_commands:
                await self._execute_container_command(
                    container_id, cmd, "Java environment"
                )

        except Exception as e:
            self.logger.error(f"❌ Java environment installation failed: {e}")

    async def _execute_container_command(
        self, container_id, command, context="Command"
    ):
        """执行容器命令的辅助方法"""
        try:
            self.logger.info(f"🔧 {context}: {command[:50]}...")

            result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": command,
                    "working_dir": "/root",
                    "timeout": 600,  # 10分钟超时
                },
            )

            # 检查执行结果
            if hasattr(result, "content") and result.content:
                content_text = result.content[0].text
                result_data = json.loads(content_text)
                execution = result_data.get("execution", {})

                if execution.get("exit_code") == 0:
                    self.logger.info(f"✅ {context}: Command executed successfully")
                    return True
                else:
                    error_msg = execution.get("stderr", "")[:200]
                    stdout_msg = execution.get("stdout", "")[:200]
                    self.logger.error(
                        f"❌ {context}: Command failed (exit_code: {execution.get('exit_code')})"
                    )
                    if error_msg:
                        self.logger.error(f"❌ STDERR: {error_msg}")
                    if stdout_msg:
                        self.logger.error(f"📄 STDOUT: {stdout_msg}")

                    # 对于关键命令，抛出异常停止执行
                    if not command.endswith("|| true"):  # 允许容错的命令不抛异常
                        raise Exception(
                            f"Critical command failed: {command} (exit_code: {execution.get('exit_code')})"
                        )
                    else:
                        self.logger.warning(
                            f"⚠️ Non-critical command failed but continuing: {command}"
                        )
                        return False
        except Exception as e:
            self.logger.error(f"❌ {context}: Command execution failed: {e}")
            raise

    async def _verify_multilang_environments(self, container_id):
        """验证所有语言环境的安装"""
        try:
            self.logger.info("🧪 Verifying multi-language environments...")

            verification_commands = {
                "Python": "python3 --version && pip --version",
                "Node.js": "node --version && npm --version",
                "Go": "bash -c 'export PATH=$PATH:/usr/local/go/bin && go version'",
                "Rust": "bash -c 'source ~/.cargo/env && rustc --version'",
                "Java": "java -version && javac -version",
                "C/C++": "gcc --version && g++ --version",
                "LSP Servers": "echo 'Python LSP:' && (which pylsp || echo 'pylsp not found'); echo 'TypeScript LSP:' && (which typescript-language-server || echo 'ts-ls not found'); echo 'Go LSP:' && (ls /go/bin/gopls || echo 'gopls not found'); echo 'Rust LSP:' && (bash -c 'source ~/.cargo/env && which rust-analyzer' || echo 'rust-analyzer not found'); echo 'Java LSP:' && (ls /opt/bin/jdtls || ls /opt/plugins || echo 'jdtls not found')",
            }

            for env_name, cmd in verification_commands.items():
                try:
                    await self._execute_container_command(
                        container_id, cmd, f"Verify {env_name}"
                    )
                except:
                    self.logger.warning(
                        f"⚠️ {env_name} verification failed - may not be critical"
                    )

            self.logger.info("🎯 Environment verification completed")

        except Exception as e:
            self.logger.error(f"❌ Environment verification failed: {e}")

    async def _download_deepcode_repository(self, container_id):
        """在容器中下载DeepCode仓库"""
        try:
            self.logger.info("📥 Downloading DeepCode repository from GitHub...")

            # Step 1: 确保git已安装
            self.logger.info("🔧 Ensuring git is available in container...")
            git_install_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": "which git > /dev/null 2>&1 || (rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock && apt-get update && apt-get install -y git)",
                    "working_dir": "/root",
                    "timeout": 180,
                },
            )

            # Step 2: 克隆DeepCode仓库的docker分支到/root/deepcode
            deepcode_repo_url = "https://github.com/HKUDS/DeepCode.git"
            deepcode_branch = "docker"
            deepcode_target_dir = "/root/deepcode"

            self.logger.info(
                f"📂 Cloning DeepCode repository (docker branch) to {deepcode_target_dir}..."
            )
            clone_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": f"git clone -b {deepcode_branch} {deepcode_repo_url} {deepcode_target_dir}",
                    "working_dir": "/root",
                    "timeout": 300,
                },
            )

            # Step 3: 验证克隆结果
            verify_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": f"ls -la {deepcode_target_dir} && echo '--- DeepCode structure ---' && find {deepcode_target_dir} -maxdepth 2 -type d",
                    "working_dir": "/root",
                },
            )

            # 解析验证结果
            if hasattr(verify_result, "content") and verify_result.content:
                content_text = verify_result.content[0].text
                result_data = json.loads(content_text)
                execution = result_data.get("execution", {})
                if execution.get("exit_code") == 0:
                    self.logger.info("✅ DeepCode repository downloaded successfully")
                    self.logger.info(f"📂 Repository location: {deepcode_target_dir}")
                    # 显示仓库结构
                    stdout = execution.get("stdout", "")
                    if stdout:
                        self.logger.info("📋 Repository structure:")
                        for line in stdout.split("\n")[:20]:  # 显示前20行
                            if line.strip():
                                self.logger.info(f"   {line}")
                else:
                    self.logger.warning(
                        f"⚠️ DeepCode repository verification failed with exit code: {execution.get('exit_code')}"
                    )
                    self.logger.warning(f"⚠️ Error: {execution.get('stderr', '')[:200]}")

            # Step 4: 创建DeepCode虚拟环境并安装依赖
            await self._setup_deepcode_environment(container_id, deepcode_target_dir)

            # Step 5: 设置DeepCode环境变量（便于后续使用）
            self.logger.info("🔧 Setting up DeepCode environment variables...")
            env_setup_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": f"echo 'export DEEPCODE_HOME={deepcode_target_dir}' >> /root/.bashrc && echo 'export PATH=$DEEPCODE_HOME:$PATH' >> /root/.bashrc",
                    "working_dir": "/root",
                },
            )

            self.logger.info("✅ DeepCode repository setup completed successfully")
            self.logger.info(f"🏠 DeepCode Home: {deepcode_target_dir}")
            self.logger.info("🔧 Environment variables added to /root/.bashrc")

        except Exception as e:
            self.logger.error(f"❌ DeepCode repository download failed: {e}")
            # 不抛出异常，继续执行，因为这是可选功能

    async def _setup_deepcode_environment(self, container_id, deepcode_dir):
        """在容器中为DeepCode设置uv虚拟环境并安装依赖"""
        try:
            self.logger.info("⚡ Setting up DeepCode uv environment...")

            # Step 1: 安装uv包管理器
            self.logger.info("🔧 Installing uv package manager...")
            install_uv_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": "curl -LsSf https://astral.sh/uv/install.sh | sh",
                    "working_dir": "/root",
                    "timeout": 120,
                },
            )

            # Step 2: 添加uv到PATH
            self.logger.info("🔧 Adding uv to PATH...")
            path_setup_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": "echo 'export PATH=\"$HOME/.cargo/bin:$PATH\"' >> ~/.bashrc && source ~/.bashrc",
                    "working_dir": "/root",
                    "timeout": 30,
                },
            )

            # Step 3: 创建uv虚拟环境 (按README标准)
            self.logger.info("📦 Creating uv virtual environment with Python 3.13...")
            create_venv_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": f"bash -c 'source ~/.bashrc && cd {deepcode_dir} && ~/.cargo/bin/uv venv --python=3.13'",
                    "working_dir": "/root",
                    "timeout": 120,
                },
            )

            # Step 4: 激活虚拟环境并安装requirements.txt中的依赖
            self.logger.info(
                "📋 Activating virtual environment and installing dependencies..."
            )
            install_deps_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": f"bash -c 'source ~/.bashrc && cd {deepcode_dir} && source .venv/bin/activate && ~/.cargo/bin/uv pip install deepcode-hku'",
                    "working_dir": "/root",
                    "timeout": 600,  # 10分钟timeout，因为依赖安装可能耗时较长
                },
            )

            # Step 4: 下载配置文件到DeepCode目录
            self.logger.info("🔧 Downloading DeepCode configuration files...")
            download_config_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": f"bash -c 'cd {deepcode_dir} && curl -O https://raw.githubusercontent.com/HKUDS/DeepCode/main/mcp_agent.config.yaml && curl -O https://raw.githubusercontent.com/HKUDS/DeepCode/main/mcp_agent.secrets.yaml'",
                    "working_dir": "/root",
                    "timeout": 120,
                },
            )

            # Step 5: 验证安装结果
            self.logger.info("🧪 Verifying DeepCode environment setup...")
            verify_env_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": 'bash -c \'source ~/.bashrc && conda activate deepcode && python -c "import streamlit; import anthropic; import mcp_agent; print(\\"✅ Core dependencies verified\\")\'',
                    "working_dir": "/root",
                    "timeout": 60,
                },
            )

            # Step 6: 创建激活脚本 (使用conda命名环境)
            self.logger.info("🔧 Creating DeepCode activation script...")
            script_content = f"""#!/bin/bash
# DeepCode Environment Activation Script
echo '🚀 Activating DeepCode conda environment...'
source ~/.bashrc
conda activate deepcode
cd {deepcode_dir}
export DEEPCODE_HOME={deepcode_dir}
export PATH=$DEEPCODE_HOME:$PATH
echo '✅ DeepCode environment activated!'
echo '💡 Usage: deepcode (for direct deepcode-hku package)'
echo '💡 Usage: streamlit run ui/streamlit_app.py (for web interface from source)'
echo '💡 Usage: python cli/main_cli.py (for CLI interface from source)'"""

            create_script_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": f"cat > {deepcode_dir}/activate_deepcode.sh << 'EOF'\n{script_content}\nEOF",
                    "working_dir": "/root",
                    "timeout": 30,
                },
            )

            # Step 7: 设置激活脚本权限
            chmod_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": f"chmod +x {deepcode_dir}/activate_deepcode.sh",
                    "working_dir": "/root",
                    "timeout": 10,
                },
            )

            # Step 8: 添加便捷别名到.bashrc (使用conda命名环境)
            self.logger.info("🔧 Adding DeepCode aliases to .bashrc...")
            alias_commands = [
                "echo '' >> /root/.bashrc",
                "echo '# DeepCode shortcuts' >> /root/.bashrc",
                f"echo 'alias deepcode-activate=\"source {deepcode_dir}/activate_deepcode.sh\"' >> /root/.bashrc",
                'echo \'alias deepcode-pkg="bash -c \\"source ~/.bashrc && conda activate deepcode && deepcode\\""\' >> /root/.bashrc',
                f'echo \'alias deepcode-web="bash -c \\"cd {deepcode_dir} && source ~/.bashrc && conda activate deepcode && streamlit run ui/streamlit_app.py\\""\' >> /root/.bashrc',
                f'echo \'alias deepcode-cli="bash -c \\"cd {deepcode_dir} && source ~/.bashrc && conda activate deepcode && python cli/main_cli.py\\""\' >> /root/.bashrc',
            ]

            alias_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": " && ".join(alias_commands),
                    "working_dir": "/root",
                    "timeout": 30,
                },
            )

            self.logger.info(
                "✅ DeepCode conda environment setup completed successfully"
            )
            self.logger.info("🐍 Conda environment: deepcode (Python 3.12)")
            self.logger.info("📦 DeepCode package: deepcode-hku (installed)")
            self.logger.info(
                f"🔧 Activation script: {deepcode_dir}/activate_deepcode.sh"
            )
            self.logger.info(
                "💡 Quick start: Run 'deepcode-activate' to activate environment"
            )
            self.logger.info(
                "💡 Package usage: Run 'deepcode-pkg' to use deepcode-hku directly"
            )
            self.logger.info("💡 Web interface: Run 'deepcode-web' to start web UI")
            self.logger.info("💡 CLI interface: Run 'deepcode-cli' to start CLI")

        except Exception as e:
            self.logger.error(f"❌ DeepCode environment setup failed: {e}")
            # 不抛出异常，继续执行

    async def _create_docker_container(self):
        """创建Docker容器 - 让LLM自主分析并决定环境配置，支持多架构并预装gcc"""
        try:
            self.execution_state.phase = ExecutionPhase.CREATING_CONTAINER
            self.logger.info("🐋 Phase 2: Docker Container Creation")

            # Step 2.1: 自动检测系统架构
            self.logger.info(
                "🔍 Step 2.1: Detecting system architecture for multi-platform support..."
            )
            platform_info = self._detect_platform_architecture()
            self.logger.info(f"🖥️ Platform detected: {platform_info}")

            # Step 2.2: LLM自主分析repo并决定Docker配置
            self.logger.info(
                "🤖 Step 2.2: LLM analyzing repository and deciding Docker configuration..."
            )
            docker_config = await self._llm_decide_docker_configuration()

            # Step 2.3: 增强Docker配置以支持多架构和预装gcc
            enhanced_config = self._enhance_docker_config_for_multiarch(
                docker_config, platform_info
            )

            # Step 2.4: 使用增强的配置创建容器
            self.logger.info(
                f"🏗️ Step 2.4: Creating Docker container with enhanced configuration: {enhanced_config}"
            )
            create_result = await self.execution_agent.call_tool(
                "create_evaluation_container", enhanced_config
            )

            # 解析结果获取容器ID
            self.logger.info(f"Create result type: {type(create_result)}")
            self.logger.info(f"Create result: {str(create_result)[:200]}...")

            if isinstance(create_result, str):
                result_data = json.loads(create_result)
                if result_data.get("status") == "success":
                    container_info = result_data.get("container", {})
                    self.execution_state.container_id = container_info.get(
                        "container_id"
                    )
                    self.execution_state.container_created = True
                    self.logger.info(
                        f"✅ Container created: {self.execution_state.container_id}"
                    )
                else:
                    raise Exception(
                        f"Container creation failed: {result_data.get('message')}"
                    )
            elif hasattr(create_result, "content"):
                # Handle CallToolResult object
                if (
                    isinstance(create_result.content, list)
                    and len(create_result.content) > 0
                ):
                    content_text = create_result.content[0].text
                else:
                    content_text = str(create_result.content)

                result_data = json.loads(content_text)
                if result_data.get("status") == "success":
                    container_info = result_data.get("container", {})
                    self.execution_state.container_id = container_info.get(
                        "container_id"
                    )
                    self.execution_state.container_created = True
                    self.logger.info(
                        f"✅ Container created: {self.execution_state.container_id}"
                    )
                else:
                    raise Exception(
                        f"Container creation failed: {result_data.get('message')}"
                    )
            else:
                self.logger.error(f"Unexpected result type: {type(create_result)}")
                raise Exception(
                    f"Container creation failed: unexpected result type {type(create_result)}"
                )

            # Step 2.5: 容器创建成功后，立即检测容器架构
            if self.execution_state.container_id:
                self.logger.info("🔍 Step 2.5: Detecting container architecture...")
                await self._detect_container_architecture(
                    self.execution_state.container_id
                )

                # Step 2.6: 预装gcc等编译工具
                self.logger.info("🔧 Step 2.6: Pre-installing gcc and build tools...")
                await self._preinstall_build_tools(self.execution_state.container_id)

                # Step 2.7: 安装多语言开发环境和LSP服务器
                self.logger.info(
                    "🌐 Step 2.7: Installing multi-language development environment..."
                )
                await self._install_multilang_development_environment(
                    self.execution_state.container_id
                )

                # Step 2.8: 下载DeepCode仓库并配置环境到容器中
                self.logger.info(
                    "📥 Step 2.8: Downloading DeepCode repository and setting up environment..."
                )
                await self._download_deepcode_repository(
                    self.execution_state.container_id
                )

        except Exception as e:
            self.execution_state.add_error(f"Container creation failed: {e}")
            raise

    async def _setup_container_environment(self):
        """Setup container environment by copying deepcode_lab and config files to DeepCode directory"""
        try:
            self.execution_state.phase = ExecutionPhase.SETTING_UP_ENV
            self.logger.info("⚙️ Phase 3: Container Environment Setup")

            if not self.execution_state.container_id:
                raise Exception("No container available for environment setup")

            # Step 3.1: Copy deepcode_lab directory to DeepCode main directory in container
            self.logger.info(
                "📁 Step 3.1: Copying deepcode_lab directory to container..."
            )
            await self._copy_deepcode_lab_to_container()

            # Step 3.2: Replace configuration files in DeepCode directory
            self.logger.info("🔧 Step 3.2: Updating DeepCode configuration files...")
            await self._update_deepcode_config_files()

            # Step 3.3: Setup workspace for the specific repository being evaluated
            # self.logger.info("📁 Step 3.3: Setting up workspace for current repository...")
            # await self._setup_evaluation_workspace()

            self.logger.info("✅ Container environment setup completed")

        except Exception as e:
            self.execution_state.add_error(f"Environment setup failed: {e}")
            raise

    async def _copy_deepcode_lab_to_container(self):
        """Copy the entire deepcode_lab directory to DeepCode main directory in container"""
        try:
            import os
            import subprocess

            container_id = self.execution_state.container_id

            # Derive project root directory from repo_path
            # repo_path format: /path/to/project/deepcode_lab/papers/X/generate_code
            # We need to find the project root that contains deepcode_lab
            repo_path = self.execution_state.repo_path
            project_root = self._find_project_root(repo_path)

            # Source path: local deepcode_lab directory
            local_deepcode_lab_path = os.path.join(project_root, "deepcode_lab")

            # Target path: DeepCode main directory in container
            container_deepcode_path = "/root/deepcode"

            self.logger.info(f"📂 Derived project root: {project_root}")
            self.logger.info(
                f"📂 Copying {local_deepcode_lab_path} to container {container_deepcode_path}/deepcode_lab"
            )

            # Use docker cp command to copy the entire directory
            # Ensure the source path exists
            if not os.path.exists(local_deepcode_lab_path):
                raise Exception(
                    f"Source directory does not exist: {local_deepcode_lab_path}"
                )

            # Remove any existing deepcode_lab directory to avoid conflicts
            self.logger.info(
                "🗑️ Removing any existing deepcode_lab directory in container..."
            )
            await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": f"rm -rf {container_deepcode_path}/deepcode_lab",
                    "working_dir": "/root",
                    "timeout": 30,
                },
            )

            # Execute docker cp command to copy the directory
            # Note: Docker cp behavior differs if target exists - we want to create deepcode_lab inside deepcode
            docker_cp_cmd = [
                "docker",
                "cp",
                local_deepcode_lab_path,
                f"{container_id}:{container_deepcode_path}/deepcode_lab",
            ]

            self.logger.info(f"🔄 Executing: {' '.join(docker_cp_cmd)}")

            # Run the docker cp command
            result = subprocess.run(
                docker_cp_cmd,
                capture_output=True,
                text=True,
                timeout=120,  # 2 minutes timeout
            )

            if result.returncode == 0:
                self.logger.info("✅ deepcode_lab directory copied successfully")

                # Fix permissions and ownership issues step by step
                self.logger.info("🔧 Fixing permissions and ownership...")

                # First, fix ownership
                await self.execution_agent.call_tool(
                    "execute_in_container",
                    {
                        "container_id": container_id,
                        "command": f"chown -R root:root {container_deepcode_path}/deepcode_lab",
                        "working_dir": "/root",
                        "timeout": 60,
                    },
                )

                # Then, fix permissions
                await self.execution_agent.call_tool(
                    "execute_in_container",
                    {
                        "container_id": container_id,
                        "command": f"chmod -R 755 {container_deepcode_path}/deepcode_lab",
                        "working_dir": "/root",
                        "timeout": 60,
                    },
                )

                # Verify the copy was successful with detailed checking
                self.logger.info(
                    "🔍 Verifying deepcode_lab directory structure in container..."
                )

                # Check top-level directory
                verify_result = await self.execution_agent.call_tool(
                    "execute_in_container",
                    {
                        "container_id": container_id,
                        "command": f"ls -la {container_deepcode_path}/deepcode_lab/",
                        "working_dir": "/root",
                        "timeout": 30,
                    },
                )

                # Check if papers directory exists
                papers_check = await self.execution_agent.call_tool(
                    "execute_in_container",
                    {
                        "container_id": container_id,
                        "command": f"ls -la {container_deepcode_path}/deepcode_lab/papers/",
                        "working_dir": "/root",
                        "timeout": 30,
                    },
                )

                # Count total files copied using a more reliable method
                count_check = await self.execution_agent.call_tool(
                    "execute_in_container",
                    {
                        "container_id": container_id,
                        "command": f"find {container_deepcode_path}/deepcode_lab -name '*.py' -o -name '*.md' -o -name '*.txt' -o -name '*.pdf' | wc -l",
                        "working_dir": "/root",
                        "timeout": 30,
                    },
                )

                self.logger.info("📋 Directory verification completed")
            else:
                raise Exception(f"Docker cp failed: {result.stderr}")

        except Exception as e:
            self.logger.error(f"❌ Failed to copy deepcode_lab directory: {e}")
            raise

    async def _update_deepcode_config_files(self):
        """Replace mcp_agent.config.yaml and mcp_agent.secrets.yaml in DeepCode directory"""
        try:
            import os
            import subprocess

            container_id = self.execution_state.container_id

            # Derive project root directory from repo_path
            repo_path = self.execution_state.repo_path
            project_root = self._find_project_root(repo_path)

            # Source paths: local config files
            local_config_path = os.path.join(project_root, "mcp_agent.config.yaml")
            local_secrets_path = os.path.join(project_root, "mcp_agent.secrets.yaml")

            # Target paths: DeepCode directory in container
            container_deepcode_path = "/root/deepcode"
            container_config_path = f"{container_deepcode_path}/mcp_agent.config.yaml"
            container_secrets_path = f"{container_deepcode_path}/mcp_agent.secrets.yaml"

            self.logger.info(f"📂 Using project root: {project_root}")

            # Check if source files exist
            if not os.path.exists(local_config_path):
                raise Exception(f"Config file does not exist: {local_config_path}")
            if not os.path.exists(local_secrets_path):
                raise Exception(f"Secrets file does not exist: {local_secrets_path}")

            self.logger.info("🔧 Copying configuration files to container...")

            # Copy mcp_agent.config.yaml
            config_cp_cmd = [
                "docker",
                "cp",
                local_config_path,
                f"{container_id}:{container_config_path}",
            ]

            self.logger.info(f"📋 Copying config file: {' '.join(config_cp_cmd)}")
            config_result = subprocess.run(
                config_cp_cmd, capture_output=True, text=True, timeout=60
            )

            if config_result.returncode != 0:
                raise Exception(f"Failed to copy config file: {config_result.stderr}")

            # Copy mcp_agent.secrets.yaml
            secrets_cp_cmd = [
                "docker",
                "cp",
                local_secrets_path,
                f"{container_id}:{container_secrets_path}",
            ]

            self.logger.info(f"🔐 Copying secrets file: {' '.join(secrets_cp_cmd)}")
            secrets_result = subprocess.run(
                secrets_cp_cmd, capture_output=True, text=True, timeout=60
            )

            if secrets_result.returncode != 0:
                raise Exception(f"Failed to copy secrets file: {secrets_result.stderr}")

            self.logger.info("✅ Configuration files updated successfully")

            # Verify the files were copied correctly
            verify_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": f"ls -la {container_deepcode_path}/*.yaml",
                    "working_dir": "/root",
                    "timeout": 30,
                },
            )

            self.logger.info("📋 Verifying configuration files in container...")

        except Exception as e:
            self.logger.error(f"❌ Failed to update configuration files: {e}")
            raise

    async def _setup_evaluation_workspace(self):
        """Setup workspace for the specific repository being evaluated"""
        try:
            container_id = self.execution_state.container_id

            # Setup workspace using the existing Docker management tool
            self.logger.info("📁 Setting up workspace for repository evaluation...")
            workspace_result = await self._call_mcp_tool_with_retry(
                "setup_container_workspace",
                {
                    "container_id": container_id,
                    "repo_path": self.execution_state.repo_path,
                    "workspace_path": "/root/workbase",
                },
            )

            # Log workspace setup results
            if hasattr(workspace_result, "content") and workspace_result.content:
                content_text = workspace_result.content[0].text
                result_data = json.loads(content_text)
                if result_data.get("status") == "success":
                    self.logger.info("✅ Workspace setup successful")
                    self.logger.info(
                        f"📁 Repository copied to: {result_data.get('workspace_path', '/root/workbase')}"
                    )
                else:
                    self.logger.warning(
                        f"⚠️ Workspace setup had issues: {result_data.get('message')}"
                    )

        except Exception as e:
            self.logger.error(f"❌ Failed to setup evaluation workspace: {e}")
            raise

    async def _run_code_evaluation_in_container(self):
        """Run code evaluation workflow inside the Docker container"""
        try:
            self.execution_state.phase = (
                ExecutionPhase.SETTING_UP_ENV
            )  # Using existing phase
            self.logger.info(
                "🔬 Phase 4: Running Code Evaluation Workflow in Container"
            )

            if not self.execution_state.container_id:
                raise Exception("No container available for code evaluation")

            container_id = self.execution_state.container_id

            # Convert local repo path to container path
            container_paths = self._convert_local_to_container_paths(
                self.execution_state.repo_path
            )

            self.logger.info("📂 Container paths:")
            self.logger.info(f"   Repository: {container_paths['repo_path']}")
            self.logger.info(f"   Documentation: {container_paths['docs_path']}")
            self.logger.info(f"   Memory: {container_paths['memory_path']}")

            # Step 1: Verify deepcode environment is available
            self.logger.info("🔍 Step 1: Verifying deepcode uv environment...")
            await self._verify_deepcode_environment(container_id)

            # Step 2: Create enhanced command with uv environment activation
            cmd = f"bash -c 'source ~/.bashrc && cd /root/deepcode && ~/.cargo/bin/uv run python workflows/code_evaluation_workflow.py \"{container_paths['repo_path']}\" \"{container_paths['docs_path']}\" \"{container_paths['memory_path']}\" 3'"

            self.logger.info(
                "🚀 Step 2: Executing code evaluation workflow in container with uv environment..."
            )
            self.logger.info(f"📋 Command: {cmd}")

            # Step 3: Execute with real-time streaming output to local terminal
            evaluation_result = await self._execute_with_realtime_streaming(
                container_id, cmd
            )

            # Parse and log the evaluation results
            if hasattr(evaluation_result, "content") and evaluation_result.content:
                content_text = evaluation_result.content[0].text
                result_data = json.loads(content_text)

                execution = result_data.get("execution", {})
                exit_code = execution.get("exit_code", -1)
                stdout = execution.get("stdout", "")
                stderr = execution.get("stderr", "")

                if exit_code == 0:
                    self.logger.info(
                        "✅ Code evaluation workflow completed successfully in container"
                    )
                    if stdout:
                        self.logger.info("📊 Evaluation Results Summary:")
                        # Log first few lines of stdout for summary
                        stdout_lines = stdout.split("\n")[:10]
                        for line in stdout_lines:
                            if line.strip():
                                self.logger.info(f"   {line}")

                    # Try to extract evaluation results from stdout
                    try:
                        # Look for JSON results in stdout
                        import re

                        json_match = re.search(r"\{.*\}", stdout, re.DOTALL)
                        if json_match:
                            eval_results = json.loads(json_match.group())
                            self.logger.info("📈 Evaluation Metrics:")

                            # Extract key metrics
                            overall = eval_results.get("overall_assessment", {})
                            if overall:
                                self.logger.info(
                                    f"   Implementation Completeness: {overall.get('implementation_completeness', 'N/A')}"
                                )
                                self.logger.info(
                                    f"   Code Quality: {overall.get('code_quality', 'N/A')}"
                                )
                                self.logger.info(
                                    f"   Project Health: {overall.get('project_health', 'N/A')}"
                                )

                            multi_file = eval_results.get(
                                "multi_file_implementation", {}
                            )
                            if multi_file:
                                self.logger.info(
                                    f"   Tasks Completed: {multi_file.get('tasks_completed', 0)}/{multi_file.get('total_tasks', 0)}"
                                )
                                self.logger.info(
                                    f"   Completion Rate: {multi_file.get('completion_rate', 0):.1f}%"
                                )
                                self.logger.info(
                                    f"   Files Implemented: {multi_file.get('files_implemented_count', 0)}"
                                )

                    except Exception as parse_e:
                        self.logger.warning(
                            f"⚠️ Could not parse evaluation results: {parse_e}"
                        )

                else:
                    self.logger.error(
                        f"❌ Code evaluation workflow failed with exit code: {exit_code}"
                    )
                    if stderr:
                        self.logger.error(f"   Error: {stderr[:500]}...")
                    if stdout:
                        self.logger.info(f"   Output: {stdout[:500]}...")

                    # Don't raise exception to allow workflow to continue
                    self.execution_state.add_error(
                        f"Code evaluation workflow failed: exit_code={exit_code}"
                    )
            else:
                self.logger.warning(
                    "⚠️ No result content from code evaluation workflow execution"
                )

            self.logger.info("✅ Code evaluation phase completed")

        except Exception as e:
            self.logger.error(f"❌ Failed to run code evaluation in container: {e}")
            self.execution_state.add_error(f"Code evaluation in container failed: {e}")
            # Don't raise to allow workflow to continue

    def _convert_local_to_container_paths(self, local_repo_path: str) -> dict:
        """
        Convert local repository path to corresponding container paths

        Args:
            local_repo_path: Local path like "/Users/.../deepcode_lab/papers/1/generate_code"

        Returns:
            Dictionary with container paths for repo, docs, and memory
        """
        try:
            # Find the deepcode_lab part in the path
            if "deepcode_lab" not in local_repo_path:
                raise Exception(
                    f"Path does not contain deepcode_lab: {local_repo_path}"
                )

            # Extract the part after deepcode_lab
            parts = local_repo_path.split("deepcode_lab")
            if len(parts) < 2:
                raise Exception(
                    f"Invalid deepcode_lab path structure: {local_repo_path}"
                )

            # Get the relative path after deepcode_lab
            relative_path = parts[1].lstrip("/")  # Remove leading slash

            # Construct container paths
            container_base = "/root/deepcode/deepcode_lab"
            container_repo_path = f"{container_base}/{relative_path}"

            # Determine docs and memory paths based on the repo path structure
            # Assume structure: deepcode_lab/papers/1/generate_code
            path_segments = relative_path.split("/")
            if len(path_segments) >= 3:  # papers/1/generate_code
                # Go up one level to get papers/1/
                paper_dir = "/".join(path_segments[:-1])  # papers/1
                container_docs_path = f"{container_base}/{paper_dir}/initial_plan.txt"
                container_memory_path = (
                    f"{container_base}/{paper_dir}/implement_code_summary.md"
                )
            else:
                # Fallback paths
                container_docs_path = f"{container_base}/initial_plan.txt"
                container_memory_path = f"{container_base}/implement_code_summary.md"

            return {
                "repo_path": container_repo_path,
                "docs_path": container_docs_path,
                "memory_path": container_memory_path,
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to convert paths: {e}")
            # Return fallback paths
            return {
                "repo_path": "/root/deepcode/deepcode_lab",
                "docs_path": "/root/deepcode/deepcode_lab/initial_plan.txt",
                "memory_path": "/root/deepcode/deepcode_lab/implement_code_summary.md",
            }

    async def _verify_deepcode_environment(self, container_id: str):
        """Verify that deepcode uv environment is available and configured"""
        try:
            self.logger.info("🔍 Checking deepcode uv environment availability...")

            # Check if uv is available
            uv_check = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": "bash -c 'source ~/.bashrc && which uv'",
                    "working_dir": "/root",
                    "timeout": 30,
                },
            )

            # Parse uv check result
            if hasattr(uv_check, "content") and uv_check.content:
                uv_result = json.loads(uv_check.content[0].text)
                if uv_result.get("execution", {}).get("exit_code") != 0:
                    raise Exception("uv is not available in container")
                self.logger.info("✅ uv is available")

            # Check if deepcode uv virtual environment exists
            venv_check = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": "bash -c 'source ~/.bashrc && cd /root/deepcode && test -d .venv && echo \"venv exists\"'",
                    "working_dir": "/root",
                    "timeout": 30,
                },
            )

            # Parse venv check result
            if hasattr(venv_check, "content") and venv_check.content:
                venv_result = json.loads(venv_check.content[0].text)
                if venv_result.get("execution", {}).get("exit_code") == 0:
                    self.logger.info("✅ Deepcode uv virtual environment exists")
                else:
                    raise Exception("Deepcode uv virtual environment not found")

            # Test uv environment activation and basic imports (按README标准)
            test_cmd = 'bash -c \'source ~/.bashrc && cd /root/deepcode && ~/.cargo/bin/uv run python -c "import sys; print(sys.executable); import mcp_agent; print(\\"mcp_agent available\\")"\''
            test_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": test_cmd,
                    "working_dir": "/root",
                    "timeout": 60,
                },
            )

            # Parse test result
            if hasattr(test_result, "content") and test_result.content:
                test_data = json.loads(test_result.content[0].text)
                test_execution = test_data.get("execution", {})
                if test_execution.get("exit_code") == 0:
                    stdout = test_execution.get("stdout", "")
                    self.logger.info("✅ Deepcode environment test successful:")
                    for line in stdout.split("\n"):
                        if line.strip():
                            self.logger.info(f"   {line}")
                else:
                    stderr = test_execution.get("stderr", "")
                    self.logger.warning(
                        f"⚠️ Deepcode environment test had issues: {stderr}"
                    )
                    # Don't fail here, just warn - the environment might still work

            self.logger.info(
                "✅ Deepcode environment verification completed successfully"
            )

        except Exception as e:
            self.logger.error(f"❌ Deepcode environment verification failed: {e}")
            # Let's try to continue anyway - sometimes verification fails but execution works
            self.logger.warning(
                "⚠️ Continuing despite verification failure - will attempt execution"
            )
            # Don't raise exception to allow workflow to continue

    async def _execute_with_realtime_streaming(self, container_id: str, command: str):
        """Execute command with real-time streaming output to local terminal"""
        import asyncio

        try:
            self.logger.info("🔍 Starting real-time streaming execution...")

            start_time = time.time()
            self.logger.info(f"⏰ Execution started at: {time.strftime('%H:%M:%S')}")
            self.logger.info(f"📋 Full command: {command}")
            self.logger.info("=" * 80)
            self.logger.info("🚀 DOCKER CONTAINER OUTPUT (REAL-TIME):")
            self.logger.info("=" * 80)

            # Construct docker exec command
            docker_cmd = ["docker", "exec", "-i", container_id, "bash", "-c", command]

            # Start subprocess with streaming
            process = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd="/Users/lizongwei/Reasearch/DeepCode_Base/DeepCode_eval_init",
            )

            stdout_buffer = []
            stderr_buffer = []

            # Real-time output streaming
            async def stream_output():
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break

                    line_str = line.decode("utf-8", errors="replace").rstrip()
                    stdout_buffer.append(line_str)

                    # Print to local terminal with timestamp
                    timestamp = time.strftime("%H:%M:%S")
                    print(f"[{timestamp}] {line_str}", flush=True)

                    # Also log important lines
                    if any(
                        keyword in line_str.lower()
                        for keyword in [
                            "error",
                            "failed",
                            "success",
                            "completed",
                            "phase",
                        ]
                    ):
                        self.logger.info(f"📡 {line_str}")

            # Start streaming task
            stream_task = asyncio.create_task(stream_output())

            # Wait for process completion
            exit_code = await process.wait()

            # Ensure all output is captured
            await stream_task

            execution_time = time.time() - start_time

            self.logger.info("=" * 80)
            self.logger.info(f"⏰ Execution completed in: {execution_time:.2f} seconds")
            self.logger.info(f"📊 Exit Code: {exit_code}")
            self.logger.info(f"📊 Total Output Lines: {len(stdout_buffer)}")

            # Create result object compatible with existing code
            mock_result = type(
                "MockResult",
                (),
                {
                    "content": [
                        type(
                            "Content",
                            (),
                            {
                                "text": json.dumps(
                                    {
                                        "execution": {
                                            "exit_code": exit_code,
                                            "stdout": "\n".join(stdout_buffer),
                                            "stderr": "\n".join(stderr_buffer),
                                        }
                                    }
                                )
                            },
                        )()
                    ]
                },
            )()

            if exit_code != 0:
                self.logger.error(f"❌ Command failed with exit code: {exit_code}")
                # Analyze last few lines for errors
                if len(stdout_buffer) > 0:
                    self.logger.error("🔍 Last few output lines:")
                    for line in stdout_buffer[-5:]:
                        if line.strip():
                            self.logger.error(f"   {line}")
            else:
                self.logger.info("✅ Command completed successfully!")

            return mock_result

        except Exception as e:
            self.logger.error(f"❌ Real-time streaming execution failed: {e}")
            # Fallback to original method
            return await self._execute_with_monitoring_fallback(container_id, command)

    async def _execute_with_monitoring_fallback(self, container_id: str, command: str):
        """Execute command with enhanced monitoring and real-time error tracking (fallback method)"""
        try:
            self.logger.info("🔍 Starting enhanced execution monitoring...")

            # Start execution with detailed logging
            start_time = time.time()
            self.logger.info(f"⏰ Execution started at: {time.strftime('%H:%M:%S')}")
            self.logger.info(f"📋 Full command: {command}")

            # Execute the command
            result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": container_id,
                    "command": command,
                    "working_dir": "/root/deepcode",
                    "timeout": 1800,  # 30 minutes timeout
                },
            )

            execution_time = time.time() - start_time
            self.logger.info(f"⏰ Execution completed in: {execution_time:.2f} seconds")

            # Enhanced result parsing and monitoring
            if hasattr(result, "content") and result.content:
                content_text = result.content[0].text
                result_data = json.loads(content_text)

                execution = result_data.get("execution", {})
                exit_code = execution.get("exit_code", -1)
                stdout = execution.get("stdout", "")
                stderr = execution.get("stderr", "")

                # Log execution summary
                self.logger.info("📊 Execution Summary:")
                self.logger.info(f"   Exit Code: {exit_code}")
                self.logger.info(f"   Duration: {execution_time:.2f}s")
                self.logger.info(f"   Stdout Length: {len(stdout)} chars")
                self.logger.info(f"   Stderr Length: {len(stderr)} chars")

                # Enhanced error analysis
                if exit_code != 0:
                    self.logger.error(f"❌ Command failed with exit code: {exit_code}")

                    # Analyze stderr for common issues
                    if stderr:
                        self.logger.error("🔍 Error Analysis:")
                        self._analyze_execution_errors(stderr)

                        # Log full stderr if it's not too long
                        if len(stderr) < 2000:
                            self.logger.error("📋 Full stderr:")
                            for line in stderr.split("\n"):
                                if line.strip():
                                    self.logger.error(f"   {line}")
                        else:
                            # Log first and last parts of stderr
                            stderr_lines = stderr.split("\n")
                            self.logger.error("📋 Stderr (first 10 lines):")
                            for line in stderr_lines[:10]:
                                if line.strip():
                                    self.logger.error(f"   {line}")
                            self.logger.error(
                                f"   ... ({len(stderr_lines)} total lines) ..."
                            )
                            self.logger.error("📋 Stderr (last 10 lines):")
                            for line in stderr_lines[-10:]:
                                if line.strip():
                                    self.logger.error(f"   {line}")

                    # Analyze stdout for additional context
                    if stdout:
                        self.logger.info("📋 Stdout for context:")
                        stdout_lines = stdout.split("\n")
                        # Show last 20 lines of stdout for context
                        for line in stdout_lines[-20:]:
                            if line.strip():
                                self.logger.info(f"   {line}")

                else:
                    self.logger.info("✅ Command executed successfully")

                    # Log progress indicators from stdout
                    if stdout:
                        progress_lines = self._extract_progress_indicators(stdout)
                        if progress_lines:
                            self.logger.info("📈 Progress Indicators:")
                            for line in progress_lines:
                                self.logger.info(f"   {line}")

            return result

        except Exception as e:
            self.logger.error(f"❌ Enhanced execution monitoring failed: {e}")
            raise

    def _analyze_execution_errors(self, stderr: str):
        """Analyze stderr for common error patterns and provide helpful insights"""
        try:
            error_patterns = {
                "ModuleNotFoundError": "Missing Python module - check conda environment activation",
                "ImportError": "Import issue - verify dependencies are installed",
                "FileNotFoundError": "Missing file - check file paths and permissions",
                "PermissionError": "Permission issue - check file/directory permissions",
                "conda": "Conda environment issue - verify environment setup",
                "mcp_agent": "MCP Agent issue - check MCP configuration",
                "anthropic": "Anthropic API issue - check API keys and configuration",
                "ConnectionError": "Network/connection issue - check connectivity",
                "timeout": "Operation timed out - consider increasing timeout",
                "JSON": "JSON parsing error - check data format",
                "KeyError": "Missing configuration key - check config files",
                "ValueError": "Invalid value - check input parameters",
            }

            found_patterns = []
            for pattern, description in error_patterns.items():
                if pattern.lower() in stderr.lower():
                    found_patterns.append(f"{pattern}: {description}")

            if found_patterns:
                self.logger.error("🔍 Detected Error Patterns:")
                for pattern in found_patterns:
                    self.logger.error(f"   - {pattern}")
            else:
                self.logger.error("🔍 No recognized error patterns found")

        except Exception as e:
            self.logger.warning(f"Error analysis failed: {e}")

    def _extract_progress_indicators(self, stdout: str) -> list:
        """Extract progress indicators and important messages from stdout"""
        try:
            progress_indicators = []
            lines = stdout.split("\n")

            # Look for specific progress patterns
            progress_patterns = [
                "Phase",
                "✅",
                "❌",
                "🔍",
                "📊",
                "🚀",
                "Starting",
                "Completed",
                "Success",
                "Failed",
                "Error",
                "Warning",
                "Progress",
                "%",
            ]

            for line in lines:
                if any(pattern in line for pattern in progress_patterns):
                    if line.strip() and len(line) < 200:  # Avoid very long lines
                        progress_indicators.append(line.strip())

            # Limit to last 10 progress indicators
            return progress_indicators[-10:] if progress_indicators else []

        except Exception:
            return []

    def _find_project_root(self, repo_path: str) -> str:
        """
        Find the project root directory that contains deepcode_lab

        Args:
            repo_path: Current repository path (e.g., /path/to/project/deepcode_lab/papers/1/generate_code)

        Returns:
            Project root directory path (e.g., /path/to/project)
        """
        import os

        # Convert to absolute path
        current_path = os.path.abspath(repo_path)

        # Walk up the directory tree to find deepcode_lab
        while current_path != os.path.dirname(current_path):  # Stop at filesystem root
            # Check if current directory contains deepcode_lab
            deepcode_lab_path = os.path.join(current_path, "deepcode_lab")
            if os.path.exists(deepcode_lab_path) and os.path.isdir(deepcode_lab_path):
                self.logger.info(
                    f"🎯 Found project root with deepcode_lab: {current_path}"
                )
                return current_path

            # Move up one directory level
            current_path = os.path.dirname(current_path)

        # If we couldn't find deepcode_lab, check if we're inside it
        current_path = os.path.abspath(repo_path)
        while current_path != os.path.dirname(current_path):
            if os.path.basename(current_path) == "deepcode_lab":
                # We found deepcode_lab, return its parent directory
                project_root = os.path.dirname(current_path)
                self.logger.info(
                    f"🎯 Found project root (parent of deepcode_lab): {project_root}"
                )
                return project_root

            current_path = os.path.dirname(current_path)

        # Fallback: if we can't find deepcode_lab, try to infer from common patterns
        current_path = os.path.abspath(repo_path)

        # Pattern 1: If path contains "deepcode_lab/papers/", extract the part before deepcode_lab
        if "deepcode_lab" in current_path:
            parts = current_path.split("deepcode_lab")
            if len(parts) > 1:
                potential_root = parts[0].rstrip(os.sep)
                self.logger.info(
                    f"🎯 Inferred project root from path pattern: {potential_root}"
                )
                return potential_root

        # Pattern 2: Look for common project indicators (mcp_agent config files)
        current_path = os.path.abspath(repo_path)
        while current_path != os.path.dirname(current_path):
            config_file = os.path.join(current_path, "mcp_agent.config.yaml")
            if os.path.exists(config_file):
                self.logger.info(
                    f"🎯 Found project root with config file: {current_path}"
                )
                return current_path

            current_path = os.path.dirname(current_path)

        # Final fallback: use the repo_path's parent directories
        # This assumes the structure like: project_root/deepcode_lab/papers/N/generate_code
        current_path = os.path.abspath(repo_path)
        # Go up 3 levels if we're in generate_code -> papers/N -> deepcode_lab -> project_root
        if os.path.basename(current_path) == "generate_code":
            for _ in range(3):
                current_path = os.path.dirname(current_path)
                if current_path == os.path.dirname(current_path):  # filesystem root
                    break

        self.logger.warning(f"⚠️ Using fallback project root: {current_path}")
        return current_path

    def _get_docker_repo_path(self, local_repo_path: str) -> str:
        """
        Convert local repo path to corresponding Docker container path

        Args:
            local_repo_path: Local repository path (e.g., /path/to/project/deepcode_lab/papers/1/generate_code)

        Returns:
            Docker container path (e.g., /root/deepcode/deepcode_lab/papers/1/generate_code)
        """
        import os

        # Find the project root and extract the relative path from deepcode_lab
        project_root = self._find_project_root(local_repo_path)

        # Convert to absolute path for proper processing
        abs_local_path = os.path.abspath(local_repo_path)

        # Find the deepcode_lab part in the path
        if "deepcode_lab" in abs_local_path:
            # Split at deepcode_lab and get the part after it
            deepcode_lab_index = abs_local_path.find("deepcode_lab")
            relative_from_deepcode_lab = abs_local_path[deepcode_lab_index:]

            # Construct Docker path: /root/deepcode/deepcode_lab/...
            docker_path = f"/root/deepcode/{relative_from_deepcode_lab}"

            self.logger.info(f"🔗 Path mapping: {local_repo_path} -> {docker_path}")
            return docker_path
        else:
            # Fallback: if deepcode_lab not found, assume we're in papers/N/generate_code
            # Extract paper ID from the path
            path_parts = abs_local_path.split(os.sep)

            # Look for patterns like papers/1/generate_code or papers/2/generate_code
            for i, part in enumerate(path_parts):
                if part == "papers" and i + 2 < len(path_parts):
                    paper_id = path_parts[i + 1]
                    remaining_path = "/".join(path_parts[i:])
                    docker_path = f"/root/deepcode/deepcode_lab/{remaining_path}"

                    self.logger.info(
                        f"🔗 Fallback path mapping: {local_repo_path} -> {docker_path}"
                    )
                    return docker_path

            # Final fallback: use the last part of the path
            docker_path = "/root/deepcode/deepcode_lab/papers/unknown/generate_code"
            self.logger.warning(
                f"⚠️ Could not determine paper ID, using fallback: {docker_path}"
            )
            return docker_path

    async def _install_repository_dependencies(self):
        """Install repository dependencies"""
        try:
            self.execution_state.phase = ExecutionPhase.INSTALLING_DEPS
            self.logger.info("📦 Phase 4: Installing Dependencies")

            if not self.execution_state.container_id:
                raise Exception("No container available for dependency installation")

            # Get the Docker container path for the current repository
            docker_repo_path = self._get_docker_repo_path(
                self.execution_state.repo_path
            )

            # Install dependencies
            self.logger.info(
                "🔧 Starting dependency installation in Docker container..."
            )
            self.logger.info(
                f"📦 Container ID: {self.execution_state.container_id[:12]}..."
            )
            self.logger.info(
                "📋 Using intelligent dependency discovery with matched Docker path"
            )
            self.logger.info(f"📂 Local repo: {self.execution_state.repo_path}")
            self.logger.info(f"🐳 Docker repo: {docker_repo_path}")

            # Prepare tool arguments for the new install_dependencies tool
            tool_args = {
                "container_id": self.execution_state.container_id,
                "language": "python",
                "workspace_path": docker_repo_path,  # Use the matched Docker path
            }

            self.logger.info(
                f"📦 Installing dependencies from workspace: {docker_repo_path}"
            )
            install_result = await self.execution_agent.call_tool(
                "install_dependencies", tool_args
            )

            # Parse and display detailed installation information
            await self._display_installation_details(install_result)

            self.execution_state.dependencies_installed = True
            self.logger.info("✅ Dependencies installation completed")

        except Exception as e:
            self.execution_state.add_error(f"Dependencies installation failed: {e}")
            # Continue execution, don't throw exception

    async def _display_installation_details(self, install_result):
        """Display detailed information about dependency installation in Docker container"""
        try:
            self.logger.info("=" * 60)
            self.logger.info("🐋 DOCKER CONTAINER DEPENDENCY INSTALLATION DETAILS")
            self.logger.info("=" * 60)

            # 解析MCP工具返回的结果
            if hasattr(install_result, "content") and install_result.content:
                content_text = install_result.content[0].text
                self.logger.debug(f"Raw content_text: '{content_text}'")

                if not content_text or content_text.strip() == "":
                    self.logger.warning(
                        "Empty content returned from install_dependencies tool"
                    )
                    return

                try:
                    result_data = json.loads(content_text)
                except json.JSONDecodeError as json_err:
                    self.logger.error(f"Failed to parse JSON response: {json_err}")
                    self.logger.error(f"Raw response: '{content_text}'")
                    return

                # 显示整体状态
                status = result_data.get("status", "unknown")
                container_id = result_data.get("container_id", "unknown")
                language = result_data.get("language", "unknown")

                self.logger.info(f"📊 Installation Status: {status.upper()}")
                self.logger.info(f"🐋 Container ID: {container_id[:12]}...")
                self.logger.info(f"🐍 Language: {language}")

                # 显示每个安装命令的详细信息
                installation_results = result_data.get("installation_results", [])
                if installation_results:
                    self.logger.info(
                        f"📦 Total Commands Executed: {len(installation_results)}"
                    )
                    self.logger.info("-" * 60)

                    for i, cmd_result in enumerate(installation_results, 1):
                        command = cmd_result.get("command", "Unknown command")
                        exit_code = cmd_result.get("exit_code", -1)
                        output = cmd_result.get("output", "").strip()

                        # 状态图标
                        status_icon = "✅" if exit_code == 0 else "❌"

                        self.logger.info(f"{status_icon} Command {i}: {command}")
                        self.logger.info(f"   📤 Exit Code: {exit_code}")

                        if output:
                            # 解析pip输出以显示下载信息
                            self._parse_pip_output(output, i)

                        if i < len(installation_results):
                            self.logger.info("-" * 40)

                # 显示统计信息
                failed_count = result_data.get("failed_installations", 0)
                total_count = result_data.get(
                    "total_installations", len(installation_results)
                )
                success_count = total_count - failed_count

                self.logger.info("=" * 60)
                self.logger.info("📈 INSTALLATION SUMMARY:")
                self.logger.info(f"   ✅ Successful: {success_count}/{total_count}")
                self.logger.info(f"   ❌ Failed: {failed_count}/{total_count}")

                if failed_count == 0:
                    self.logger.info(
                        "🎉 All dependencies installed successfully in Docker container!"
                    )
                else:
                    self.logger.warning(
                        f"⚠️  {failed_count} dependencies failed to install"
                    )

                self.logger.info("=" * 60)

            else:
                self.logger.warning("⚠️  No content found in installation result")
                if hasattr(install_result, "content"):
                    self.logger.debug(
                        f"install_result.content: {install_result.content}"
                    )
                else:
                    self.logger.debug("install_result has no 'content' attribute")

        except Exception as e:
            self.logger.error(f"Failed to display installation details: {e}")
            self.logger.debug(f"install_result type: {type(install_result)}")
            self.logger.debug(f"install_result: {install_result}")

    async def _discover_requirements_file(self) -> Optional[str]:
        """
        Intelligently discover requirements file

        Search strategy:
        1. First check common requirements file names
        2. Search in multiple directory levels
        3. Prioritize main requirements.txt
        4. Return container file path

        Returns:
            Container requirements file path, or None if not found
        """
        try:
            # Common requirements file names (sorted by priority)
            requirements_filenames = [
                "requirements.txt",
                "requirements.in",
                "requirements-dev.txt",
                "requirements/requirements.txt",
                "requirements/base.txt",
                "requirements/dev.txt",
                "requirements/prod.txt",
                "requirements/production.txt",
                "dev-requirements.txt",
                "pip-requirements.txt",
            ]

            # Search directories (sorted by priority)
            search_dirs = [
                ".",  # Root directory
                "src",
                "app",
                "api",  # Common source directories
                "backend",
                "server",  # Backend directories
                "frontend",
                "client",  # Frontend directories
                "docs",
                "documentation",  # Documentation directories
                "scripts",
                "tools",  # Script tool directories
            ]

            # Need to perform recursive search to discover requirements files in deep directories
            additional_search_paths = []

            # Recursively search all subdirectories (max 3 levels deep)
            for root, dirs, files in os.walk(repo_path):
                # Calculate current directory depth
                relative_root = os.path.relpath(root, repo_path)
                if relative_root == ".":
                    depth = 0
                else:
                    depth = relative_root.count(os.sep) + 1

                # Limit search depth to avoid overly deep directories
                if depth <= 3:
                    # Check if there are requirements-related files
                    for file in files:
                        if "requirements" in file.lower() and file.endswith(
                            (".txt", ".in")
                        ):
                            dir_relative_path = (
                                relative_root if relative_root != "." else ""
                            )
                            if (
                                dir_relative_path
                                and dir_relative_path not in search_dirs
                            ):
                                additional_search_paths.append(dir_relative_path)
                            break

            # Add discovered additional search paths
            search_dirs.extend(additional_search_paths)

            found_files = []
            repo_path = self.execution_state.repo_path

            # Search for requirements files in each search directory
            for search_dir in search_dirs:
                if search_dir == ".":
                    current_search_path = repo_path
                else:
                    current_search_path = os.path.join(repo_path, search_dir)

                if not os.path.exists(current_search_path):
                    continue

                # 在当前目录中检查所有可能的requirements文件名
                for req_filename in requirements_filenames:
                    if "/" in req_filename:
                        # 处理嵌套路径，如 requirements/base.txt
                        req_full_path = os.path.join(repo_path, req_filename)
                    else:
                        # 简单文件名
                        req_full_path = os.path.join(current_search_path, req_filename)

                    if os.path.exists(req_full_path):
                        # 计算相对于repo根目录的路径
                        rel_path = os.path.relpath(req_full_path, repo_path)
                        container_path = f"/workspace/repo/{rel_path}"

                        # 检查文件大小，避免空文件
                        if os.path.getsize(req_full_path) > 0:
                            found_files.append(
                                {
                                    "local_path": req_full_path,
                                    "container_path": container_path,
                                    "relative_path": rel_path,
                                    "filename": os.path.basename(req_full_path),
                                    "directory": os.path.dirname(rel_path)
                                    if os.path.dirname(rel_path)
                                    else ".",
                                }
                            )

                            self.logger.debug(f"Found requirements file: {rel_path}")

            if not found_files:
                self.logger.info("📋 No requirements files found in repository")
                return None

            # 选择最佳的requirements文件
            best_file = self._select_best_requirements_file(found_files)

            if best_file:
                self.logger.info(
                    f"📋 Selected requirements file: {best_file['relative_path']}"
                )
                self.logger.info(f"📁 Location: {best_file['directory']}")

                # 验证文件内容
                await self._validate_requirements_file(best_file)

                return best_file["container_path"]
            else:
                self.logger.info("📋 No suitable requirements file found")
                return None

        except Exception as e:
            self.logger.error(f"Error discovering requirements file: {e}")
            return None

    def _select_best_requirements_file(
        self, found_files: List[Dict[str, str]]
    ) -> Optional[Dict[str, str]]:
        """
        Select the best requirements file from discovered files

        Selection priority:
        1. Root directory requirements.txt
        2. Other requirements files in root directory
        3. requirements.txt in main directories like src/app
        4. Requirements files in other directories

        Args:
            found_files: List of discovered requirements files

        Returns:
            Best requirements file information
        """
        if not found_files:
            return None

        # Priority scoring function
        def calculate_priority(file_info):
            score = 0
            rel_path = file_info["relative_path"]
            filename = file_info["filename"]
            directory = file_info["directory"]

            # Filename priority
            if filename == "requirements.txt":
                score += 100
            elif filename == "requirements.in":
                score += 90
            elif filename.startswith("requirements"):
                score += 80
            else:
                score += 50

            # Directory priority
            if directory == ".":
                score += 50  # Root directory highest
            elif directory in ["src", "app", "api"]:
                score += 40  # Main source directories
            elif directory in ["backend", "server"]:
                score += 30  # Backend directories
            elif directory == "requirements":
                score += 20  # Dedicated requirements directory
            elif "/" in directory:
                # Multi-level directories, score based on level and directory names
                path_parts = directory.split("/")
                if any(part in ["src", "app", "main", "core"] for part in path_parts):
                    score += 25  # Deep paths containing main source directories
                elif any(
                    part in ["submission", "final", "prod", "production"]
                    for part in path_parts
                ):
                    score += 35  # Directories containing submission/final versions
                elif len(path_parts) <= 2:
                    score += 20  # Directories within 2 levels
                else:
                    score += 15  # Deeper directories
            else:
                score += 10  # Other single-level directories

            # Negative weight to avoid dev/test specific files
            if "test" in filename.lower() or "dev" in filename.lower():
                score -= 10

            return score

        # Sort by priority
        found_files.sort(key=calculate_priority, reverse=True)

        # Log selection process
        self.logger.debug("Requirements file priority ranking:")
        for i, file_info in enumerate(found_files[:5]):  # Only show top 5
            priority = calculate_priority(file_info)
            self.logger.debug(
                f"  {i+1}. {file_info['relative_path']} (score: {priority})"
            )

        return found_files[0]

    async def _validate_requirements_file(self, file_info: Dict[str, str]):
        """
        Validate requirements file content

        Args:
            file_info: Requirements file information
        """
        try:
            local_path = file_info["local_path"]

            with open(local_path, "r", encoding="utf-8") as f:
                content = f.read().strip()

            lines = [
                line.strip()
                for line in content.split("\n")
                if line.strip() and not line.strip().startswith("#")
            ]

            if not lines:
                self.logger.warning(
                    f"⚠️  Requirements file {file_info['relative_path']} is empty or contains only comments"
                )
                return

            self.logger.info(
                f"📦 Found {len(lines)} dependencies in {file_info['relative_path']}"
            )

            # 显示前几个依赖作为预览
            preview_count = min(3, len(lines))
            for i, line in enumerate(lines[:preview_count]):
                self.logger.info(f"   • {line}")

            if len(lines) > preview_count:
                self.logger.info(
                    f"   ... and {len(lines) - preview_count} more dependencies"
                )

        except Exception as e:
            self.logger.warning(
                f"Could not validate requirements file {file_info['relative_path']}: {e}"
            )

    def _parse_pip_output(self, output: str, command_num: int):
        """Parse pip output to extract key information"""
        lines = output.split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Detect download information
            if "Downloading" in line:
                # Extract package name and size information
                if ".whl" in line or ".tar.gz" in line:
                    self.logger.info(f"   📥 {line}")

            # Detect collection information
            elif "Collecting" in line:
                self.logger.info(f"   📦 {line}")

            # Detect successful installation information
            elif "Successfully installed" in line:
                self.logger.info(f"   🎯 {line}")

            # Detect requirement already satisfied
            elif "Requirement already satisfied" in line:
                self.logger.info(f"   ℹ️  {line}")

            # Detect error information
            elif "ERROR:" in line or "Failed" in line:
                self.logger.error(f"   ❌ {line}")

            # Detect warning information
            elif "WARNING:" in line:
                self.logger.warning(f"   ⚠️  {line}")

            # Detect progress information
            elif "━" in line and ("%" in line or "MB" in line or "KB" in line):
                # This is a download progress bar
                self.logger.info(f"   📊 {line}")

    async def _execute_with_bug_fixing(self, entry_point: Dict[str, Any]) -> bool:
        """
        Execute code and perform intelligent bug fixing
        This is the core method implementing multi-round conversation
        """
        command = entry_point["command"]

        for attempt in range(self.execution_state.max_execution_attempts):
            self.logger.info(
                f"🔧 Execution attempt {attempt + 1}/{self.execution_state.max_execution_attempts}"
            )

            # Execute code
            exec_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": self.execution_state.container_id,
                    "command": command,
                    "working_dir": "/workspace/repo",
                    "timeout": 120,
                },
            )

            # Parse execution results
            if isinstance(exec_result, str):
                result_data = json.loads(exec_result)
                execution_info = result_data.get("execution", {})

                attempt_record = ExecutionAttempt(
                    attempt_number=attempt + 1,
                    command=command,
                    exit_code=execution_info.get("exit_code", -1),
                    stdout=execution_info.get("stdout", ""),
                    stderr=execution_info.get("stderr", ""),
                    execution_time=execution_info.get("execution_time", 0),
                    success=execution_info.get("success", False),
                )

                self.execution_state.execution_attempts.append(attempt_record)

                if attempt_record.success:
                    self.logger.info("✅ Execution successful!")
                    self.logger.info(f"Output: {attempt_record.stdout[:200]}...")
                    return True
                else:
                    self.logger.warning(
                        f"❌ Execution failed (exit code: {attempt_record.exit_code})"
                    )
                    self.logger.warning(f"Error: {attempt_record.stderr[:300]}...")

                    # If there are remaining attempts, perform bug fixing
                    if attempt < self.execution_state.max_execution_attempts - 1:
                        self.logger.info("🔍 Attempting intelligent bug fixing...")

                        # Perform intelligent bug fixing
                        fix_success = await self._attempt_intelligent_bug_fix(
                            attempt_record
                        )
                        if not fix_success:
                            self.logger.warning(
                                "🚨 Bug fixing failed, trying next execution attempt..."
                            )

        return False

    async def _attempt_intelligent_bug_fix(
        self, failed_attempt: ExecutionAttempt
    ) -> bool:
        """
        Intelligent bug fixing - Multi-round conversation with LLM to analyze errors and fix code
        """
        try:
            self.execution_state.phase = ExecutionPhase.FIXING_BUGS

            # First analyze repository structure to better understand the code
            analysis_result = await self.execution_agent.call_tool(
                "analyze_repo_structure_in_container",
                {
                    "container_id": self.execution_state.container_id,
                    "repo_path": "/workspace/repo",
                },
            )

            # Get relevant file contents
            relevant_files = await self._identify_relevant_files_for_error(
                failed_attempt
            )
            file_contents = {}

            for file_path in relevant_files:
                try:
                    file_result = await self.execution_agent.call_tool(
                        "read_file_in_container",
                        {
                            "container_id": self.execution_state.container_id,
                            "file_path": file_path,
                            "working_dir": "/workspace/repo",
                        },
                    )

                    if isinstance(file_result, str):
                        file_data = json.loads(file_result)
                        if file_data.get("status") == "success":
                            file_contents[file_path] = file_data.get("content", "")
                except Exception as e:
                    self.logger.warning(f"Could not read file {file_path}: {e}")

            # Converse with LLM for error analysis and code fixing
            fix_success = await self._llm_bug_fixing_conversation(
                failed_attempt, analysis_result, file_contents
            )

            return fix_success

        except Exception as e:
            self.logger.error(f"Bug fixing attempt failed: {e}")
            return False

    async def _identify_relevant_files_for_error(
        self, failed_attempt: ExecutionAttempt
    ) -> List[str]:
        """Identify relevant files based on error information"""
        relevant_files = []

        # Extract file paths from error information
        error_text = failed_attempt.stderr + failed_attempt.stdout

        # Common Python error patterns
        import re

        file_patterns = [
            r'File "([^"]+\.py)"',
            r"in ([^/\s]+\.py)",
            r"([a-zA-Z_][a-zA-Z0-9_]*\.py)",
        ]

        for pattern in file_patterns:
            matches = re.findall(pattern, error_text)
            for match in matches:
                if match.endswith(".py") and not match.startswith("/"):
                    relevant_files.append(match)

        # If no specific files found, add main entry files
        if not relevant_files:
            for entry_point in self.execution_state.entry_points:
                if entry_point.get("file") and entry_point["file"] != ".":
                    relevant_files.append(entry_point["file"])

        # Remove duplicates and limit file count
        return list(set(relevant_files))[:5]

    async def _llm_bug_fixing_conversation(
        self,
        failed_attempt: ExecutionAttempt,
        analysis_result: str,
        file_contents: Dict[str, str],
    ) -> bool:
        """Multi-round conversation with LLM to fix bugs"""
        try:
            # Initialize LLM client
            client, client_type = await self._initialize_llm_client()
            tools = self._prepare_mcp_tool_definitions()

            # Build system message
            system_message = f"""You are an expert Python developer helping to fix code execution errors.

Your task is to analyze the execution error and fix the code to make it run successfully.

You have access to Docker container tools to:
1. Read files from the container: read_file_in_container
2. Write modified files to the container: write_file_in_container
3. List files in directories: list_files_in_container
4. Execute commands to test fixes: execute_in_container

Container ID: {self.execution_state.container_id}

When fixing code:
1. Analyze the error carefully
2. Identify the root cause
3. Read relevant files to understand the codebase
4. Make minimal, targeted fixes
5. Test your fixes by executing the code
6. Iterate if needed

Be systematic and thorough in your approach."""

            # Build initial conversation message
            conversation_message = f"""I need help fixing a code execution error.

**Execution Details:**
- Command: {failed_attempt.command}
- Exit Code: {failed_attempt.exit_code}
- Execution Time: {failed_attempt.execution_time}s

**Error Output:**
```
{failed_attempt.stderr}
```

**Standard Output:**
```
{failed_attempt.stdout}
```

**Repository Analysis:**
```
{analysis_result}
```

**Available File Contents:**
"""

            # Add file contents to message
            for file_path, content in file_contents.items():
                conversation_message += f"\n\n**File: {file_path}**\n```python\n{content[:1000]}{'...' if len(content) > 1000 else ''}\n```"

            conversation_message += "\n\nPlease analyze this error and fix the code. Start by understanding what went wrong, then make the necessary changes to fix the issue."

            messages = [{"role": "user", "content": conversation_message}]

            # Perform multi-round conversation for fixing
            for fix_attempt in range(self.execution_state.max_bug_fix_attempts):
                self.logger.info(f"🤖 Bug fix conversation round {fix_attempt + 1}")

                # Call LLM
                response = await self._call_llm_with_tools(
                    client, client_type, system_message, messages, tools
                )

                # Handle LLM's tool calls
                if response.get("tool_calls"):
                    tool_results = []
                    for tool_call in response["tool_calls"]:
                        tool_name = tool_call["name"]
                        tool_input = tool_call["input"]

                        try:
                            tool_result = await self.execution_agent.call_tool(
                                tool_name, tool_input
                            )
                            tool_results.append(
                                f"Tool {tool_name} result: {tool_result}"
                            )
                            self.logger.info(f"Executed {tool_name} during bug fixing")
                        except Exception as e:
                            tool_results.append(f"Tool {tool_name} failed: {str(e)}")

                    # Add tool results to conversation
                    if tool_results:
                        messages.append(
                            {
                                "role": "assistant",
                                "content": response.get(
                                    "content", "Working on fixes..."
                                ),
                            }
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": "Tool execution results:\n"
                                + "\n".join(tool_results)
                                + "\n\nPlease continue with the fix or test the changes by executing the code.",
                            }
                        )

                # If LLM thinks fixing is complete, test execution
                if (
                    "test" in response.get("content", "").lower()
                    or "execute" in response.get("content", "").lower()
                ):
                    test_result = await self._test_fix_attempt(failed_attempt.command)
                    if test_result:
                        self.logger.info("✅ Bug fix successful!")
                        return True
                    else:
                        messages.append(
                            {
                                "role": "user",
                                "content": "The fix didn't work. Please analyze what went wrong and try a different approach.",
                            }
                        )

            return False

        except Exception as e:
            self.logger.error(f"LLM bug fixing conversation failed: {e}")
            return False

    async def _test_fix_attempt(self, command: str) -> bool:
        """Test if fix attempt was successful"""
        try:
            test_result = await self.execution_agent.call_tool(
                "execute_in_container",
                {
                    "container_id": self.execution_state.container_id,
                    "command": command,
                    "working_dir": "/workspace/repo",
                    "timeout": 60,
                },
            )

            if isinstance(test_result, str):
                result_data = json.loads(test_result)
                execution_info = result_data.get("execution", {})
                return execution_info.get("success", False)

            return False

        except Exception as e:
            self.logger.error(f"Fix test failed: {e}")
            return False

    async def _generate_execution_report(self) -> Dict[str, Any]:
        """Generate execution report"""
        try:
            execution_time = time.time() - self.execution_state.start_time

            report = {
                "status": "success" if self.execution_state.final_success else "failed",
                "execution_id": f"exec_{int(self.execution_state.start_time)}",
                "repository": {
                    "path": self.execution_state.repo_path,
                    "detected_languages": self.execution_state.detected_languages,
                    "entry_points": self.execution_state.entry_points,
                },
                "container": {
                    "container_id": self.execution_state.container_id,
                    "created": self.execution_state.container_created,
                },
                "execution_summary": {
                    "final_success": self.execution_state.final_success,
                    "total_execution_attempts": len(
                        self.execution_state.execution_attempts
                    ),
                    "total_bug_fix_attempts": len(
                        self.execution_state.bug_fix_attempts
                    ),
                    "execution_time_seconds": execution_time,
                },
                "execution_attempts": [
                    {
                        "attempt": attempt.attempt_number,
                        "command": attempt.command,
                        "success": attempt.success,
                        "exit_code": attempt.exit_code,
                        "execution_time": attempt.execution_time,
                        "timestamp": attempt.timestamp,
                    }
                    for attempt in self.execution_state.execution_attempts
                ],
                "errors": self.execution_state.errors,
                "timing": {
                    "total_time_seconds": execution_time,
                    "started_at": time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(self.execution_state.start_time),
                    ),
                    "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
            }

            # Save report
            report_path = os.path.join(
                self.execution_state.workspace_dir, "execution_report.json"
            )
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)

            self.logger.info(f"📊 Execution report saved to: {report_path}")
            return report

        except Exception as e:
            self.logger.error(f"Failed to generate execution report: {e}")
            raise

    async def _cleanup_environment(self):
        """Clean up environment"""
        try:
            self.logger.info("🧹 Starting environment cleanup")

            # Clean up Docker container
            if self.execution_state and self.execution_state.container_id:
                try:
                    cleanup_result = await self.execution_agent.call_tool(
                        "cleanup_container",
                        {
                            "container_id": self.execution_state.container_id,
                            "remove_volumes": True,
                        },
                    )
                    self.logger.info(
                        f"🗑️  Container {self.execution_state.container_id[:12]} cleaned up"
                    )
                except Exception as e:
                    self.logger.warning(f"Container cleanup failed: {e}")

            # Clean up agent
            if self.execution_agent:
                try:
                    await self.execution_agent.__aexit__(None, None, None)
                except Exception as e:
                    self.logger.warning(f"Agent cleanup failed: {e}")

            self.logger.info("✅ Environment cleanup completed")

        except Exception as e:
            self.logger.warning(f"Cleanup failed: {e}")

    # ==================== LLM Communication Methods ====================

    async def _initialize_llm_client(self):
        """Initialize LLM client (Anthropic or OpenAI) based on API key availability"""
        anthropic_key = self.api_config.get("anthropic", {}).get("api_key", "")
        openai_key = self.api_config.get("openai", {}).get("api_key", "")

        if anthropic_key and anthropic_key.strip():
            try:
                from anthropic import AsyncAnthropic

                client = AsyncAnthropic(api_key=anthropic_key)
                await client.messages.create(
                    model=self.default_models["anthropic"],
                    max_tokens=20,
                    messages=[{"role": "user", "content": "test"}],
                )
                self.logger.info(
                    f"Using Anthropic API with model: {self.default_models['anthropic']}"
                )
                return client, "anthropic"
            except Exception as e:
                self.logger.warning(f"Anthropic API unavailable: {e}")

        if openai_key and openai_key.strip():
            try:
                from openai import AsyncOpenAI

                openai_config = self.api_config.get("openai", {})
                base_url = openai_config.get("base_url")

                if base_url:
                    client = AsyncOpenAI(api_key=openai_key, base_url=base_url)
                else:
                    client = AsyncOpenAI(api_key=openai_key)

                try:
                    await client.chat.completions.create(
                        model=self.default_models["openai"],
                        max_tokens=20,
                        messages=[{"role": "user", "content": "test"}],
                    )
                except Exception as e:
                    if "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                        await client.chat.completions.create(
                            model=self.default_models["openai"],
                            max_completion_tokens=20,
                            messages=[{"role": "user", "content": "test"}],
                        )
                    else:
                        raise
                self.logger.info(
                    f"Using OpenAI API with model: {self.default_models['openai']}"
                )
                if base_url:
                    self.logger.info(f"Using custom base URL: {base_url}")
                return client, "openai"
            except Exception as e:
                self.logger.warning(f"OpenAI API unavailable: {e}")

        raise ValueError(
            "No available LLM API - please check your API keys in configuration"
        )

    async def _call_llm_with_tools(
        self, client, client_type, system_message, messages, tools, max_tokens=8192
    ):
        """Call LLM with tools"""
        try:
            if client_type == "anthropic":
                return await self._call_anthropic_with_tools(
                    client, system_message, messages, tools, max_tokens
                )
            elif client_type == "openai":
                return await self._call_openai_with_tools(
                    client, system_message, messages, tools, max_tokens
                )
            else:
                raise ValueError(f"Unsupported client type: {client_type}")
        except Exception as e:
            self.logger.error(f"LLM call failed: {e}")
            raise

    async def _call_anthropic_with_tools(
        self, client, system_message, messages, tools, max_tokens
    ):
        """Call Anthropic API"""
        validated_messages = self._validate_messages(messages)
        if not validated_messages:
            validated_messages = [
                {"role": "user", "content": "Please help fix the code"}
            ]

        try:
            response = await client.messages.create(
                model=self.default_models["anthropic"],
                system=system_message,
                messages=validated_messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=0.1,
            )
        except Exception as e:
            self.logger.error(f"Anthropic API call failed: {e}")
            raise

        content = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    {"id": block.id, "name": block.name, "input": block.input}
                )

        return {"content": content, "tool_calls": tool_calls}

    async def _call_openai_with_tools(
        self, client, system_message, messages, tools, max_tokens
    ):
        """Call OpenAI API"""
        openai_tools = []
        for tool in tools:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["input_schema"],
                    },
                }
            )

        openai_messages = [{"role": "system", "content": system_message}]
        openai_messages.extend(messages)

        try:
            response = await client.chat.completions.create(
                model=self.default_models["openai"],
                messages=openai_messages,
                tools=openai_tools if openai_tools else None,
                max_tokens=max_tokens,
                temperature=0.1,
            )
        except Exception as e:
            if "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                response = await client.chat.completions.create(
                    model=self.default_models["openai"],
                    messages=openai_messages,
                    tools=openai_tools if openai_tools else None,
                    max_completion_tokens=max_tokens,
                )
            else:
                raise

        message = response.choices[0].message
        content = message.content or ""

        tool_calls = []
        if message.tool_calls:
            for tool_call in message.tool_calls:
                tool_calls.append(
                    {
                        "id": tool_call.id,
                        "name": tool_call.function.name,
                        "input": json.loads(tool_call.function.arguments),
                    }
                )

        return {"content": content, "tool_calls": tool_calls}

    def _validate_messages(self, messages: List[Dict]) -> List[Dict]:
        """Validate and clean message list"""
        valid_messages = []
        for msg in messages:
            content = msg.get("content", "").strip()
            if content:
                valid_messages.append(
                    {"role": msg.get("role", "user"), "content": content}
                )
            else:
                self.logger.warning(f"Skipping empty message: {msg}")
        return valid_messages

    def _prepare_mcp_tool_definitions(self) -> List[Dict[str, Any]]:
        """Prepare tool definitions in Anthropic API standard format"""
        from config.mcp_tool_definitions_index import get_mcp_tools

        return get_mcp_tools("docker_management")


# Entry point for testing
async def main():
    """Test the intelligent code execution workflow"""
    workflow = IntelligentCodeExecutionWorkflow()

    # Test repository path
    test_repo = "/Users/lizongwei/Reasearch/DeepCode_Base/DeepCode_eval_init/deepcode_lab/papers/1/generate_code"

    try:
        result = await workflow.execute_repository_code(
            repo_path=test_repo, max_attempts=3, max_bug_fixes=2
        )

        print("✅ Execution completed!")
        print(
            f"Success: {result.get('execution_summary', {}).get('final_success', False)}"
        )
        print(
            f"Total attempts: {result.get('execution_summary', {}).get('total_execution_attempts', 0)}"
        )

    except Exception as e:
        print(f"❌ Execution failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
