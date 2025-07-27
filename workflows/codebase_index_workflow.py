"""
Codebase Index Workflow
代码库索引工作流

This workflow integrates the functionality of run_indexer.py and code_indexer.py
to build intelligent relationships between existing codebase and target structure.

该工作流集成了run_indexer.py和code_indexer.py的功能，
用于在现有代码库和目标结构之间建立智能关系。

Features:
- 从initial_plan.txt提取目标文件结构 / Extract target file structure from initial_plan.txt
- 分析代码库并建立索引 / Analyze codebase and build indexes
- 生成关系映射和统计报告 / Generate relationship mappings and statistical reports
- 为代码复现提供参考依据 / Provide reference basis for code reproduction
"""

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, Any, Optional
import yaml

# 添加tools目录到路径中 / Add tools directory to path
sys.path.append(str(Path(__file__).parent.parent / "tools"))

from tools.code_indexer import CodeIndexer


class CodebaseIndexWorkflow:
    """代码库索引工作流类 / Codebase Index Workflow Class"""

    def __init__(self, logger=None):
        """
        初始化工作流

        Args:
            logger: 日志记录器实例
        """
        self.logger = logger or self._setup_default_logger()
        self.indexer = None

    def _setup_default_logger(self) -> logging.Logger:
        """设置默认日志记录器"""
        logger = logging.getLogger("CodebaseIndexWorkflow")
        logger.setLevel(logging.INFO)

        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        return logger

    def extract_file_tree_from_plan(self, plan_content: str) -> Optional[str]:
        """
        从initial_plan.txt内容中提取文件树结构
        Extract file tree structure from initial_plan.txt content

        Args:
            plan_content: Content of the initial_plan.txt file

        Returns:
            Extracted file tree structure as string
        """
        # 查找文件结构部分，特别是"## File Structure"格式
        file_structure_pattern = r"## File Structure[^\n]*\n```[^\n]*\n(.*?)\n```"

        match = re.search(file_structure_pattern, plan_content, re.DOTALL)
        if match:
            file_tree = match.group(1).strip()
            lines = file_tree.split("\n")

            # 清理树结构 - 移除空行和不属于结构的注释
            cleaned_lines = []
            for line in lines:
                # 保留树结构的行
                if line.strip() and (
                    any(char in line for char in ["├──", "└──", "│"])
                    or line.strip().endswith("/")
                    or "." in line.split("/")[-1]  # 有文件扩展名
                    or line.strip().endswith(".py")
                    or line.strip().endswith(".txt")
                    or line.strip().endswith(".md")
                    or line.strip().endswith(".yaml")
                ):
                    cleaned_lines.append(line)

            if len(cleaned_lines) >= 5:
                file_tree = "\n".join(cleaned_lines)
                self.logger.info(
                    f"📊 从## File Structure部分提取文件树结构 ({len(cleaned_lines)} lines)"
                )
                return file_tree

        # 备用方案：查找包含项目结构的任何代码块
        code_block_patterns = [
            r"```[^\n]*\n(rice_framework/.*?(?:├──|└──).*?)\n```",
            r"```[^\n]*\n(project/.*?(?:├──|└──).*?)\n```",
            r"```[^\n]*\n(src/.*?(?:├──|└──).*?)\n```",
            r"```[^\n]*\n(.*?(?:├──|└──).*?(?:\.py|\.txt|\.md|\.yaml).*?)\n```",
        ]

        for pattern in code_block_patterns:
            match = re.search(pattern, plan_content, re.DOTALL)
            if match:
                file_tree = match.group(1).strip()
                lines = [line for line in file_tree.split("\n") if line.strip()]
                if len(lines) >= 5:
                    self.logger.info(
                        f"📊 从代码块中提取文件树结构 ({len(lines)} lines)"
                    )
                    return file_tree

        # 最终备用方案：从文件提及中提取文件路径并创建基本结构
        self.logger.warning("⚠️ 未找到标准文件树，尝试从文件提及中提取...")

        # 在整个文档中查找反引号中的文件路径
        file_mentions = re.findall(
            r"`([^`]*(?:\.py|\.txt|\.md|\.yaml|\.yml)[^`]*)`", plan_content
        )

        if file_mentions:
            # 将文件组织成目录结构
            dirs = set()
            files_by_dir = {}

            for file_path in file_mentions:
                file_path = file_path.strip()
                if "/" in file_path:
                    dir_path = "/".join(file_path.split("/")[:-1])
                    filename = file_path.split("/")[-1]
                    dirs.add(dir_path)
                    if dir_path not in files_by_dir:
                        files_by_dir[dir_path] = []
                    files_by_dir[dir_path].append(filename)
                else:
                    if "root" not in files_by_dir:
                        files_by_dir["root"] = []
                    files_by_dir["root"].append(file_path)

            # 创建树结构
            structure_lines = []

            # 确定根目录名称
            root_name = (
                "rice_framework"
                if any("rice" in f for f in file_mentions)
                else "project"
            )
            structure_lines.append(f"{root_name}/")

            # 添加目录和文件
            sorted_dirs = sorted(dirs) if dirs else []
            for i, dir_path in enumerate(sorted_dirs):
                is_last_dir = i == len(sorted_dirs) - 1
                prefix = "└──" if is_last_dir else "├──"
                structure_lines.append(f"{prefix} {dir_path}/")

                if dir_path in files_by_dir:
                    files = sorted(files_by_dir[dir_path])
                    for j, filename in enumerate(files):
                        is_last_file = j == len(files) - 1
                        if is_last_dir:
                            file_prefix = "    └──" if is_last_file else "    ├──"
                        else:
                            file_prefix = "│   └──" if is_last_file else "│   ├──"
                        structure_lines.append(f"{file_prefix} {filename}")

            # 添加根文件（如果有）
            if "root" in files_by_dir:
                root_files = sorted(files_by_dir["root"])
                for i, filename in enumerate(root_files):
                    is_last = (i == len(root_files) - 1) and not sorted_dirs
                    prefix = "└──" if is_last else "├──"
                    structure_lines.append(f"{prefix} {filename}")

            if len(structure_lines) >= 3:
                file_tree = "\n".join(structure_lines)
                self.logger.info(
                    f"📊 从文件提及生成文件树 ({len(structure_lines)} lines)"
                )
                return file_tree

        # 如果未找到文件树，返回None
        self.logger.warning("⚠️ 在初始计划中未找到文件树结构")
        return None

    def load_target_structure_from_plan(self, plan_path: str) -> str:
        """
        从initial_plan.txt加载目标结构并提取文件树
        Load target structure from initial_plan.txt and extract file tree

        Args:
            plan_path: Path to initial_plan.txt file

        Returns:
            Extracted file tree structure
        """
        try:
            # 加载完整的计划内容
            with open(plan_path, "r", encoding="utf-8") as f:
                plan_content = f.read()

            self.logger.info(f"📄 已加载初始计划 ({len(plan_content)} characters)")

            # 提取文件树结构
            file_tree = self.extract_file_tree_from_plan(plan_content)

            if file_tree:
                self.logger.info("✅ 成功从初始计划中提取文件树")
                self.logger.info("📋 提取结构预览:")
                # 显示提取树的前几行
                preview_lines = file_tree.split("\n")[:8]
                for line in preview_lines:
                    self.logger.info(f"   {line}")
                if len(file_tree.split("\n")) > 8:
                    self.logger.info(f"   ... 还有 {len(file_tree.split('\n')) - 8} 行")
                return file_tree
            else:
                self.logger.warning("⚠️ 无法从初始计划中提取文件树")
                self.logger.info("🔄 回退到默认目标结构")
                return self.get_default_target_structure()

        except Exception as e:
            self.logger.error(f"❌ 加载初始计划文件失败 {plan_path}: {e}")
            self.logger.info("🔄 回退到默认目标结构")
            return self.get_default_target_structure()

    def get_default_target_structure(self) -> str:
        """获取默认目标结构"""
        return """
project/
├── src/
│   ├── core/
│   │   ├── gcn.py        # GCN encoder
│   │   ├── diffusion.py  # forward/reverse processes
│   │   ├── denoiser.py   # denoising MLP
│   │   └── fusion.py     # fusion combiner
│   ├── models/           # model wrapper classes
│   │   └── recdiff.py
│   ├── utils/
│   │   ├── data.py       # loading & preprocessing
│   │   ├── predictor.py  # scoring functions
│   │   ├── loss.py       # loss functions
│   │   ├── metrics.py    # NDCG, Recall etc.
│   │   └── sched.py      # beta/alpha schedule utils
│   └── configs/
│       └── default.yaml  # hyperparameters, paths
├── tests/
│   ├── test_gcn.py
│   ├── test_diffusion.py
│   ├── test_denoiser.py
│   ├── test_loss.py
│   └── test_pipeline.py
├── docs/
│   ├── architecture.md
│   ├── api_reference.md
│   └── README.md
├── experiments/
│   ├── run_experiment.py
│   └── notebooks/
│       └── analysis.ipynb
├── requirements.txt
└── setup.py
"""

    def load_or_create_indexer_config(self, paper_dir: str) -> Dict[str, Any]:
        """
        加载或创建索引器配置
        Load or create indexer configuration

        Args:
            paper_dir: 论文目录路径

        Returns:
            配置字典
        """
        # 尝试加载现有的配置文件
        config_path = Path(__file__).parent.parent / "tools" / "indexer_config.yaml"

        try:
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)

                # 更新路径配置为当前论文目录
                if "paths" not in config:
                    config["paths"] = {}
                config["paths"]["code_base_path"] = os.path.join(paper_dir, "code_base")
                config["paths"]["output_dir"] = os.path.join(paper_dir, "indexes")

                # 调整性能设置以适应工作流
                if "performance" in config:
                    config["performance"]["enable_concurrent_analysis"] = (
                        False  # 禁用并发以避免API限制
                    )
                if "debug" in config:
                    config["debug"]["verbose_output"] = True  # 启用详细输出
                if "llm" in config:
                    config["llm"]["request_delay"] = 0.5  # 增加请求间隔

                self.logger.info(f"已加载配置文件: {config_path}")
                return config

        except Exception as e:
            self.logger.warning(f"加载配置文件失败: {e}")

        # 如果加载失败，使用默认配置
        self.logger.info("使用默认配置")
        default_config = {
            "paths": {
                "code_base_path": os.path.join(paper_dir, "code_base"),
                "output_dir": os.path.join(paper_dir, "indexes"),
            },
            "llm": {
                "model_provider": "anthropic",
                "max_tokens": 4000,
                "temperature": 0.3,
                "request_delay": 0.5,  # 增加请求间隔
                "max_retries": 3,
                "retry_delay": 1.0,
            },
            "file_analysis": {
                "max_file_size": 1048576,  # 1MB
                "max_content_length": 3000,
                "supported_extensions": [
                    ".py",
                    ".js",
                    ".ts",
                    ".java",
                    ".cpp",
                    ".c",
                    ".h",
                    ".hpp",
                    ".cs",
                    ".php",
                    ".rb",
                    ".go",
                    ".rs",
                    ".scala",
                    ".kt",
                    ".yaml",
                    ".yml",
                    ".json",
                    ".xml",
                    ".toml",
                    ".md",
                    ".txt",
                ],
                "skip_directories": [
                    "__pycache__",
                    "node_modules",
                    "target",
                    "build",
                    "dist",
                    "venv",
                    "env",
                    ".git",
                    ".svn",
                    "data",
                    "datasets",
                ],
            },
            "relationships": {
                "min_confidence_score": 0.3,
                "high_confidence_threshold": 0.7,
                "relationship_types": {
                    "direct_match": 1.0,
                    "partial_match": 0.8,
                    "reference": 0.6,
                    "utility": 0.4,
                },
            },
            "performance": {
                "enable_concurrent_analysis": False,  # 禁用并发以避免API限制
                "max_concurrent_files": 3,
                "enable_content_caching": True,
                "max_cache_size": 100,
            },
            "debug": {
                "verbose_output": True,
                "save_raw_responses": False,
                "mock_llm_responses": False,
            },
            "output": {
                "generate_summary": True,
                "generate_statistics": True,
                "include_metadata": True,
                "json_indent": 2,
            },
            "logging": {"level": "INFO", "log_to_file": False},
        }

        return default_config

    async def run_indexing_workflow(
        self,
        paper_dir: str,
        initial_plan_path: Optional[str] = None,
        config_path: str = "mcp_agent.secrets.yaml",
    ) -> Dict[str, Any]:
        """
        运行完整的代码索引工作流
        Run the complete code indexing workflow

        Args:
            paper_dir: 论文目录路径
            initial_plan_path: 初始计划文件路径（可选）
            config_path: API配置文件路径

        Returns:
            索引结果字典
        """
        try:
            self.logger.info("🚀 开始代码库索引工作流...")

            # 步骤1：确定初始计划文件路径
            if not initial_plan_path:
                initial_plan_path = os.path.join(paper_dir, "initial_plan.txt")

            # 步骤2：加载目标结构
            if os.path.exists(initial_plan_path):
                self.logger.info(f"📐 从 {initial_plan_path} 加载目标结构")
                target_structure = self.load_target_structure_from_plan(
                    initial_plan_path
                )
            else:
                self.logger.warning(f"⚠️ 初始计划文件不存在: {initial_plan_path}")
                self.logger.info("📐 使用默认目标结构")
                target_structure = self.get_default_target_structure()

            # 步骤3：检查代码库路径
            code_base_path = os.path.join(paper_dir, "code_base")
            if not os.path.exists(code_base_path):
                self.logger.error(f"❌ 代码库路径不存在: {code_base_path}")
                return {
                    "status": "error",
                    "message": f"Code base path does not exist: {code_base_path}",
                    "output_files": {},
                }

            # 步骤4：创建输出目录
            output_dir = os.path.join(paper_dir, "indexes")
            os.makedirs(output_dir, exist_ok=True)

            # 步骤5：加载配置
            indexer_config = self.load_or_create_indexer_config(paper_dir)

            self.logger.info(f"📁 代码库路径: {code_base_path}")
            self.logger.info(f"📤 输出目录: {output_dir}")

            # 步骤6：创建代码索引器
            self.indexer = CodeIndexer(
                code_base_path=code_base_path,
                target_structure=target_structure,
                output_dir=output_dir,
                config_path=config_path,
                enable_pre_filtering=True,
            )

            # 应用配置设置 / Apply configuration settings
            self.indexer.indexer_config = indexer_config

            # 直接设置配置属性到索引器 / Directly set configuration attributes to indexer
            if "file_analysis" in indexer_config:
                file_config = indexer_config["file_analysis"]
                self.indexer.supported_extensions = set(
                    file_config.get(
                        "supported_extensions", self.indexer.supported_extensions
                    )
                )
                self.indexer.skip_directories = set(
                    file_config.get("skip_directories", self.indexer.skip_directories)
                )
                self.indexer.max_file_size = file_config.get(
                    "max_file_size", self.indexer.max_file_size
                )
                self.indexer.max_content_length = file_config.get(
                    "max_content_length", self.indexer.max_content_length
                )

            if "llm" in indexer_config:
                llm_config = indexer_config["llm"]
                self.indexer.model_provider = llm_config.get(
                    "model_provider", self.indexer.model_provider
                )
                self.indexer.llm_max_tokens = llm_config.get(
                    "max_tokens", self.indexer.llm_max_tokens
                )
                self.indexer.llm_temperature = llm_config.get(
                    "temperature", self.indexer.llm_temperature
                )
                self.indexer.request_delay = llm_config.get(
                    "request_delay", self.indexer.request_delay
                )
                self.indexer.max_retries = llm_config.get(
                    "max_retries", self.indexer.max_retries
                )
                self.indexer.retry_delay = llm_config.get(
                    "retry_delay", self.indexer.retry_delay
                )

            if "relationships" in indexer_config:
                rel_config = indexer_config["relationships"]
                self.indexer.min_confidence_score = rel_config.get(
                    "min_confidence_score", self.indexer.min_confidence_score
                )
                self.indexer.high_confidence_threshold = rel_config.get(
                    "high_confidence_threshold", self.indexer.high_confidence_threshold
                )
                self.indexer.relationship_types = rel_config.get(
                    "relationship_types", self.indexer.relationship_types
                )

            if "performance" in indexer_config:
                perf_config = indexer_config["performance"]
                self.indexer.enable_concurrent_analysis = perf_config.get(
                    "enable_concurrent_analysis",
                    self.indexer.enable_concurrent_analysis,
                )
                self.indexer.max_concurrent_files = perf_config.get(
                    "max_concurrent_files", self.indexer.max_concurrent_files
                )
                self.indexer.enable_content_caching = perf_config.get(
                    "enable_content_caching", self.indexer.enable_content_caching
                )
                self.indexer.max_cache_size = perf_config.get(
                    "max_cache_size", self.indexer.max_cache_size
                )

            if "debug" in indexer_config:
                debug_config = indexer_config["debug"]
                self.indexer.verbose_output = debug_config.get(
                    "verbose_output", self.indexer.verbose_output
                )
                self.indexer.save_raw_responses = debug_config.get(
                    "save_raw_responses", self.indexer.save_raw_responses
                )
                self.indexer.mock_llm_responses = debug_config.get(
                    "mock_llm_responses", self.indexer.mock_llm_responses
                )

            if "output" in indexer_config:
                output_config = indexer_config["output"]
                self.indexer.generate_summary = output_config.get(
                    "generate_summary", self.indexer.generate_summary
                )
                self.indexer.generate_statistics = output_config.get(
                    "generate_statistics", self.indexer.generate_statistics
                )
                self.indexer.include_metadata = output_config.get(
                    "include_metadata", self.indexer.include_metadata
                )

            self.logger.info("🔧 索引器配置完成")
            self.logger.info(f"🤖 模型提供商: {self.indexer.model_provider}")
            self.logger.info(
                f"⚡ 并发分析: {'启用' if self.indexer.enable_concurrent_analysis else '禁用'}"
            )
            self.logger.info(
                f"🗄️ 内容缓存: {'启用' if self.indexer.enable_content_caching else '禁用'}"
            )
            self.logger.info(
                f"🔍 预过滤: {'启用' if self.indexer.enable_pre_filtering else '禁用'}"
            )

            self.logger.info("=" * 60)
            self.logger.info("🚀 开始代码索引过程...")

            # 步骤7：构建所有索引
            output_files = await self.indexer.build_all_indexes()

            # 步骤8：生成摘要报告
            if output_files:
                summary_report = self.indexer.generate_summary_report(output_files)

                self.logger.info("=" * 60)
                self.logger.info("✅ 索引完成成功!")
                self.logger.info(f"📊 处理了 {len(output_files)} 个仓库")
                self.logger.info("📁 生成的索引文件:")
                for repo_name, file_path in output_files.items():
                    self.logger.info(f"   📄 {repo_name}: {file_path}")
                self.logger.info(f"📋 摘要报告: {summary_report}")

                # 统计信息（如果启用）
                if self.indexer.generate_statistics:
                    self.logger.info("\n📈 处理统计:")
                    total_relationships = 0
                    high_confidence_relationships = 0

                    for file_path in output_files.values():
                        try:
                            with open(file_path, "r", encoding="utf-8") as f:
                                index_data = json.load(f)
                                relationships = index_data.get("relationships", [])
                                total_relationships += len(relationships)
                                high_confidence_relationships += len(
                                    [
                                        r
                                        for r in relationships
                                        if r.get("confidence_score", 0)
                                        > self.indexer.high_confidence_threshold
                                    ]
                                )
                        except Exception as e:
                            self.logger.warning(
                                f"   ⚠️ 无法从 {file_path} 加载统计: {e}"
                            )

                    self.logger.info(f"   🔗 找到的总关系数: {total_relationships}")
                    self.logger.info(
                        f"   ⭐ 高置信度关系: {high_confidence_relationships}"
                    )
                    self.logger.info(
                        f"   📊 每个仓库的平均关系: {total_relationships / len(output_files) if output_files else 0:.1f}"
                    )

                self.logger.info("\n🎉 代码索引过程成功完成!")

                return {
                    "status": "success",
                    "message": f"Successfully indexed {len(output_files)} repositories",
                    "output_files": output_files,
                    "summary_report": summary_report,
                    "statistics": {
                        "total_repositories": len(output_files),
                        "total_relationships": total_relationships,
                        "high_confidence_relationships": high_confidence_relationships,
                    }
                    if self.indexer.generate_statistics
                    else None,
                }
            else:
                self.logger.warning("⚠️ 未生成索引文件")
                return {
                    "status": "warning",
                    "message": "No index files were generated",
                    "output_files": {},
                }

        except Exception as e:
            self.logger.error(f"❌ 索引工作流失败: {e}")
            # 如果有详细的错误信息，记录下来
            import traceback

            self.logger.error(f"详细错误信息: {traceback.format_exc()}")
            return {"status": "error", "message": str(e), "output_files": {}}

    def print_banner(self):
        """打印应用横幅"""
        banner = """
╔═══════════════════════════════════════════════════════════════════════╗
║                    🔍 Codebase Index Workflow v1.0                   ║
║              Intelligent Code Relationship Analysis Tool              ║
╠═══════════════════════════════════════════════════════════════════════╣
║  📁 分析现有代码库 / Analyzes existing codebases                     ║
║  🔗 与目标结构建立智能关系 / Builds intelligent relationships        ║
║  🤖 由LLM分析驱动 / Powered by LLM analysis                          ║
║  📊 生成详细的JSON索引 / Generates detailed JSON indexes             ║
║  🎯 为代码复现提供参考 / Provides reference for code reproduction    ║
╚═══════════════════════════════════════════════════════════════════════╝
        """
        print(banner)


# 便捷函数，用于直接调用工作流
async def run_codebase_indexing(
    paper_dir: str,
    initial_plan_path: Optional[str] = None,
    config_path: str = "mcp_agent.secrets.yaml",
    logger=None,
) -> Dict[str, Any]:
    """
    运行代码库索引的便捷函数
    Convenience function to run codebase indexing

    Args:
        paper_dir: 论文目录路径
        initial_plan_path: 初始计划文件路径（可选）
        config_path: API配置文件路径
        logger: 日志记录器实例（可选）

    Returns:
        索引结果字典
    """
    workflow = CodebaseIndexWorkflow(logger=logger)
    workflow.print_banner()

    return await workflow.run_indexing_workflow(
        paper_dir=paper_dir,
        initial_plan_path=initial_plan_path,
        config_path=config_path,
    )


# 用于测试的主函数
async def main():
    """主函数用于测试工作流"""
    import logging

    # 设置日志
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # 测试参数
    paper_dir = "./deepcode_lab/papers/1"
    initial_plan_path = os.path.join(paper_dir, "initial_plan.txt")

    # 运行工作流
    result = await run_codebase_indexing(
        paper_dir=paper_dir, initial_plan_path=initial_plan_path, logger=logger
    )

    logger.info(f"索引结果: {result}")


if __name__ == "__main__":
    asyncio.run(main())
