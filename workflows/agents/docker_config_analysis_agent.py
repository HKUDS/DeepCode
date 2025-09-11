"""
Docker Configuration Analysis Agent

专门用于分析repo并决定最优Docker配置的简单agent
"""

import json
import logging
from typing import Dict, Any, List, Optional


class DockerConfigAnalysisAgent:
    """
    Docker配置分析Agent - 专门用于分析仓库并决定Docker环境配置

    功能：
    - 调用filesystem MCP工具读取repo文件
    - 与LLM对话分析环境需求
    - 提取最终的Docker配置决策
    """

    def __init__(self, mcp_agent, logger: Optional[logging.Logger] = None):
        """
        初始化Docker配置分析Agent

        Args:
            mcp_agent: MCP agent实例，用于调用工具
            logger: 日志器实例
        """
        self.mcp_agent = mcp_agent
        self.logger = logger or self._create_default_logger()
        self.analysis_completed = False
        self.docker_config = None

    def _create_default_logger(self) -> logging.Logger:
        """创建默认日志器"""
        logger = logging.getLogger(f"{__name__}.DockerConfigAnalysisAgent")
        logger.setLevel(logging.INFO)
        return logger

    async def execute_tool_calls(self, tool_calls: List[Dict]) -> List[Dict]:
        """
        执行MCP工具调用

        Args:
            tool_calls: 工具调用列表

        Returns:
            工具执行结果列表
        """
        results = []

        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_input = tool_call["input"]

            self.logger.info(f"🔧 Executing tool: {tool_name}")

            try:
                if self.mcp_agent:
                    # 通过MCP协议执行工具调用
                    result = await self.mcp_agent.call_tool(tool_name, tool_input)

                    results.append(
                        {
                            "tool_id": tool_call["id"],
                            "tool_name": tool_name,
                            "result": result,
                        }
                    )

                    self.logger.info(f"✅ Tool {tool_name} executed successfully")
                else:
                    results.append(
                        {
                            "tool_id": tool_call["id"],
                            "tool_name": tool_name,
                            "result": json.dumps(
                                {
                                    "status": "error",
                                    "message": "MCP agent not initialized",
                                }
                            ),
                        }
                    )

            except Exception as e:
                self.logger.error(f"❌ Tool execution failed: {e}")
                results.append(
                    {
                        "tool_id": tool_call["id"],
                        "tool_name": tool_name,
                        "result": json.dumps({"status": "error", "message": str(e)}),
                    }
                )

        return results

    def try_extract_docker_config(
        self, llm_response_content: str
    ) -> Optional[Dict[str, Any]]:
        """
        尝试从LLM响应中提取Docker配置

        Args:
            llm_response_content: LLM响应内容

        Returns:
            提取的Docker配置，如果没有找到则返回None
        """
        try:
            # 查找JSON配置
            import re

            json_patterns = [
                r"```json\s*(\{.*?\})\s*```",  # 代码块中的JSON
                r'\{[^{}]*"image_type"[^{}]*\}',  # 直接的JSON对象
                r'\{[^{}]*"version"[^{}]*"memory_limit"[^{}]*\}',  # 包含关键字段的JSON
            ]

            for pattern in json_patterns:
                json_match = re.search(pattern, llm_response_content, re.DOTALL)
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

    def format_tool_results_for_llm(self, tool_results: List[Dict]) -> str:
        """
        格式化工具结果给LLM

        Args:
            tool_results: 工具执行结果列表

        Returns:
            格式化的工具结果字符串
        """
        formatted_results = []
        formatted_results.append("🔧 **Tool Execution Results:**")

        for tool_result in tool_results:
            tool_name = tool_result["tool_name"]
            result_content = tool_result["result"]

            # 处理结果内容
            if hasattr(result_content, "content"):
                # CallToolResult对象
                content_text = (
                    result_content.content[0].text
                    if isinstance(result_content.content, list)
                    else str(result_content.content)
                )
            elif isinstance(result_content, str):
                content_text = result_content
            else:
                content_text = str(result_content)

            formatted_results.append(
                f"```\nTool: {tool_name}\nResult: {content_text}\n```"
            )

        return "\n\n".join(formatted_results)

    def get_analysis_prompt(self, repo_path: str, detected_languages: List[str]) -> str:
        """
        获取Docker配置分析的初始任务提示（用户消息，不包含角色定义）

        Args:
            repo_path: 仓库路径
            detected_languages: 检测到的编程语言

        Returns:
            任务提示字符串
        """
        # 计算相对于当前工作目录的相对路径
        import os

        current_dir = os.getcwd()
        if os.path.isabs(repo_path):
            # 如果repo_path是绝对路径，计算相对路径
            try:
                relative_repo_path = os.path.relpath(repo_path, current_dir)
            except ValueError:
                # 如果无法计算相对路径（比如在不同驱动器），使用绝对路径的目录名
                relative_repo_path = os.path.basename(repo_path)
        else:
            relative_repo_path = repo_path

        prompt = f"""Please analyze this repository and provide optimal Docker configuration for code execution.

**Repository Information:**
- Path: {repo_path}
- Detected Languages: {", ".join(detected_languages)}

**Your Task:**
1. Search for requirements.txt and README.md files (check at least 2 levels deep in directory structure)
2. Analyze package dependencies and system requirements
3. Use your knowledge to determine the best Docker configuration
4. Provide the configuration in the specified JSON format

**IMPORTANT**: The repository is located at "{relative_repo_path}" relative to current directory.
**Start by executing:** `list_directory` with path "{relative_repo_path}" to begin analysis."""
        return prompt

    def get_continue_prompt(self, round_num: int, repo_path: str = "") -> str:
        """
        获取继续分析的提示

        Args:
            round_num: 当前轮次
            repo_path: 仓库路径

        Returns:
            继续分析的提示
        """
        # 计算相对路径
        import os

        current_dir = os.getcwd()
        if repo_path and os.path.isabs(repo_path):
            try:
                relative_repo_path = os.path.relpath(repo_path, current_dir)
            except ValueError:
                relative_repo_path = os.path.basename(repo_path)
        else:
            relative_repo_path = repo_path or "."
        if round_num == 1:
            return f"""Please continue your analysis in the repository at "{relative_repo_path}":

1. Search for requirements.txt and README.md files (at least 2 levels deep)
2. Read the found files to analyze dependencies
3. Provide Docker configuration based on your analysis

If you find requirements.txt, analyze it and provide configuration immediately."""

        elif round_num == 2:
            return """Please provide your Docker configuration now in JSON format.

Use the information you've gathered from requirements.txt and README.md files to make your decision."""

        elif round_num == 3:
            return """You must provide the Docker configuration now in JSON format. No more file reading."""

        else:
            return """Provide Docker configuration in JSON format now."""

    def get_default_docker_config(self) -> Dict[str, Any]:
        """
        Get default Docker configuration with conda support

        Returns:
            Default Docker configuration
        """
        return {
            "image_type": "ubuntu-conda",
            "version": "latest",
            "memory_limit": "4g",
            "cpu_limit": "2",
            "reasoning": "Default Ubuntu-conda configuration for conda environment setup and dependency management",
        }
