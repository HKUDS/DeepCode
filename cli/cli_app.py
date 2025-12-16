#!/usr/bin/env python3
"""
DeepCode - CLI Application Main Program
深度代码 - CLI应用主程序

🧬 Open-Source Code Agent by Data Intelligence Lab @ HKU
⚡ Revolutionizing research reproducibility through collaborative AI
"""

import os
import sys
import asyncio
import time
import json
import click

# 禁止生成.pyc文件
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# 添加项目根目录到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# 导入MCP应用和工作流
from cli.workflows import CLIWorkflowAdapter
from cli.cli_interface import CLIInterface, Colors


class CLIApp:
    """CLI应用主类 - 升级版智能体编排引擎"""

    def __init__(self) -> None:
        self.cli = CLIInterface()
        self.workflow_adapter = CLIWorkflowAdapter(cli_interface=self.cli)
        self.app = None  # Will be initialized by workflow adapter
        self.logger = None
        # Context for storing last run metadata (input_source, input_type, error_flag)
        # 同时用于 /retry-last 聊天命令
        self.context = {"last_input": None}
        # Document segmentation will be managed by CLI interface

    async def initialize_mcp_app(self):
        """初始化MCP应用 - 使用工作流适配器"""
        # Workflow adapter will handle MCP initialization
        return await self.workflow_adapter.initialize_mcp_app()

    async def cleanup_mcp_app(self):
        """清理MCP应用 - 使用工作流适配器"""
        await self.workflow_adapter.cleanup_mcp_app()

    async def process_input(self, input_source: str, input_type: str):
        """处理输入源（URL或文件/聊天）- 使用升级版智能体编排引擎

        同时在 ``self.context["last_input"]`` 中记录最近一次运行的
        ``(input_source, input_type, error_flag)`` 信息，供 /retry-last 使用。
        """

        try:
            # Document segmentation configuration is managed by CLI interface

            self.cli.print_separator()
            self.cli.print_status(
                "🚀 Starting intelligent agent orchestration...", "processing"
            )

            # 显示处理阶段（根据配置决定）
            self.cli.display_processing_stages(0, self.cli.enable_indexing)

            # 使用工作流适配器进行处理
            result = await self.workflow_adapter.process_input_with_orchestration(
                input_source=input_source,
                input_type=input_type,
                enable_indexing=self.cli.enable_indexing,
            )

            # 标记本次运行是否出错
            error_flag = result.get("status") != "success"

            if not error_flag:
                # 显示完成状态
                final_stage = 8 if self.cli.enable_indexing else 5
                self.cli.display_processing_stages(
                    final_stage, self.cli.enable_indexing
                )
                self.cli.print_status(
                    "🎉 Agent orchestration completed successfully!", "complete"
                )

                # 显示结果
                self.display_results(
                    result.get("analysis_result", ""),
                    result.get("download_result", ""),
                    result.get("repo_result", ""),
                    result.get("pipeline_mode", "comprehensive"),
                )
            else:
                self.cli.print_status(
                    f"❌ Processing failed: {result.get('error', 'Unknown error')}",
                    "error",
                )

            # 添加到历史记录
            self.cli.add_to_history(input_source, result)

            # 在上下文中记录最近一次运行的输入信息
            if self.context is None or not isinstance(self.context, dict):
                self.context = {"last_input": None}
            self.context["last_input"] = {
                "input_source": input_source,
                "input_type": input_type,
                "error": error_flag,
            }

            return result

        except Exception as e:  # noqa: BLE001
            error_msg = str(e)
            self.cli.print_error_box("Agent Orchestration Error", error_msg)
            self.cli.print_status(f"Error during orchestration: {error_msg}", "error")

            # 添加错误到历史记录
            error_result = {"status": "error", "error": error_msg}
            self.cli.add_to_history(input_source, error_result)

            # 在上下文中记录最近一次失败运行的信息
            if self.context is None or not isinstance(self.context, dict):
                self.context = {"last_input": None}
            self.context["last_input"] = {
                "input_source": input_source,
                "input_type": input_type,
                "error": True,
            }

            return error_result

    def display_results(
        self,
        analysis_result: str,
        download_result: str,
        repo_result: str,
        pipeline_mode: str = "comprehensive",
    ):
        """显示处理结果"""
        self.cli.print_results_header()

        # 显示流水线模式
        if pipeline_mode == "chat":
            mode_display = "💬 Chat Planning Mode"
        elif pipeline_mode == "comprehensive":
            mode_display = "🧠 Comprehensive Mode"
        else:
            mode_display = "⚡ Optimized Mode"
        print(
            f"{Colors.BOLD}{Colors.PURPLE}🤖 PIPELINE MODE: {mode_display}{Colors.ENDC}"
        )
        self.cli.print_separator("─", 79, Colors.PURPLE)

        print(f"{Colors.BOLD}{Colors.OKCYAN}📊 ANALYSIS PHASE RESULTS:{Colors.ENDC}")
        self.cli.print_separator("─", 79, Colors.CYAN)

        # 尝试解析并格式化分析结果
        try:
            if analysis_result.strip().startswith("{"):
                parsed_analysis = json.loads(analysis_result)
                print(json.dumps(parsed_analysis, indent=2, ensure_ascii=False))
            else:
                print(
                    analysis_result[:1000] + "..."
                    if len(analysis_result) > 1000
                    else analysis_result
                )
        except Exception:  # noqa: BLE001
            print(
                analysis_result[:1000] + "..."
                if len(analysis_result) > 1000
                else analysis_result
            )

        print(f"\n{Colors.BOLD}{Colors.PURPLE}📥 DOWNLOAD PHASE RESULTS:{Colors.ENDC}")
        self.cli.print_separator("─", 79, Colors.PURPLE)
        print(
            download_result[:1000] + "..."
            if len(download_result) > 1000
            else download_result
        )

        print(
            f"\n{Colors.BOLD}{Colors.GREEN}⚙️  IMPLEMENTATION PHASE RESULTS:{Colors.ENDC}"
        )
        self.cli.print_separator("─", 79, Colors.GREEN)
        print(repo_result[:1000] + "..." if len(repo_result) > 1000 else repo_result)

        # 尝试提取生成的代码目录信息
        if "Code generated in:" in repo_result:
            code_dir = (
                repo_result.split("Code generated in:")[-1].strip().split("\n")[0]
            )
            print(
                f"\n{Colors.BOLD}{Colors.YELLOW}📁 Generated Code Directory: {Colors.ENDC}{code_dir}"
            )

        # 显示处理完成的工作流阶段
        print(
            f"\n{Colors.BOLD}{Colors.OKCYAN}🔄 COMPLETED WORKFLOW STAGES:{Colors.ENDC}"
        )

        if pipeline_mode == "chat":
            stages = [
                "🚀 Engine Initialization",
                "💬 Requirements Analysis",
                "🏗️ Workspace Setup",
                "📝 Implementation Plan Generation",
                "⚙️ Code Implementation",
            ]
        else:
            stages = [
                "📄 Document Processing",
                "🔍 Reference Analysis",
                "📋 Plan Generation",
                "📦 Repository Download",
                "🗂️ Codebase Indexing",
                "⚙️ Code Implementation",
            ]

        for stage in stages:
            print(f"  ✅ {stage}")

        self.cli.print_separator()

    async def run_interactive_session(self):
        """运行交互式会话"""
        # 清屏并显示启动界面
        self.cli.clear_screen()
        self.cli.print_logo()
        self.cli.print_welcome_banner()

        # 初始化MCP应用
        await self.initialize_mcp_app()

        try:
            # 主交互循环
            while self.cli.is_running:
                self.cli.create_menu()
                choice = self.cli.get_user_input()

                if choice in ["q", "quit", "exit"]:
                    self.cli.print_goodbye()
                    break

                elif choice in ["u", "url"]:
                    url = self.cli.get_url_input()
                    if url:
                        await self.process_input(url, "url")

                elif choice in ["f", "file"]:
                    file_path = self.cli.upload_file_gui()
                    if file_path:
                        await self.process_input(f"file://{file_path}", "file")

                elif choice in ["t", "chat", "text"]:
                    chat_input = self.cli.get_chat_input()
                    if not chat_input:
                        # 用户取消或未提供输入
                        continue

                    # 处理聊天命令（以 "/" 开头）
                    if chat_input.strip() == "/retry-last":
                        last = None
                        if isinstance(self.context, dict):
                            last = self.context.get("last_input")

                        if not last:
                            self.cli.print_status(
                                "No previous run available to retry.", "warning"
                            )
                        elif not last.get("error"):
                            self.cli.print_status(
                                "Last run was successful; nothing to retry.", "info"
                            )
                        else:
                            source = last.get("input_source")
                            input_type = last.get("input_type", "chat")
                            if not source:
                                self.cli.print_status(
                                    "Previous failed run has no input source to retry.",
                                    "error",
                                )
                            else:
                                self.cli.print_status(
                                    "Retrying last failed input...", "processing"
                                )
                                await self.process_input(source, input_type)

                        # 处理完命令后继续主循环
                        continue

                    # 普通聊天输入 - 直接作为 chat 类型处理
                    await self.process_input(chat_input, "chat")

                elif choice in ["h", "history"]:
                    self.cli.show_history()

                elif choice in ["c", "config", "configure"]:
                    # Show configuration menu - all settings managed by CLI interface
                    self.cli.show_configuration_menu()

                else:
                    self.cli.print_status(
                        "Invalid choice. Please select U, F, T, C, H, or Q.", "warning"
                    )

                # 询问是否继续
                if self.cli.is_running and choice in ["u", "f", "t", "chat", "text"]:
                    if not self.cli.ask_continue():
                        self.cli.is_running = False
                        self.cli.print_status("Session ended by user", "info")

        except KeyboardInterrupt:
            print(f"\n{Colors.WARNING}⚠️  Process interrupted by user{Colors.ENDC}")
        except Exception as e:  # noqa: BLE001
            print(f"\n{Colors.FAIL}❌ Unexpected error: {str(e)}{Colors.ENDC}")
        finally:
            # 清理资源
            await self.cleanup_mcp_app()


async def run_interactive_cli():
    """Run the interactive CLI session"""
    start_time = time.time()

    try:
        # 创建并运行CLI应用
        app = CLIApp()
        await app.run_interactive_session()

    except KeyboardInterrupt:
        print(f"\n{Colors.WARNING}⚠️  Application interrupted by user{Colors.ENDC}")
    except Exception as e:  # noqa: BLE001
        print(f"\n{Colors.FAIL}❌ Application error: {str(e)}{Colors.ENDC}")
    finally:
        end_time = time.time()
        print(
            f"\n{Colors.BOLD}{Colors.CYAN}⏱️  Total runtime: {end_time - start_time:.2f} seconds{Colors.ENDC}"
        )

        # 清理缓存文件
        print(f"{Colors.YELLOW}🧹 Cleaning up cache files...{Colors.ENDC}")
        if os.name == "nt":  # Windows
            os.system(
                "powershell -Command \"Get-ChildItem -Path . -Filter '__pycache__' -Recurse -Directory | Remove-Item -Recurse -Force\" 2>nul"
            )
        else:  # Unix/Linux/macOS
            os.system('find . -type d -name "__pycache__" -exec rm -r {} + 2>/dev/null')

        print(
            f"{Colors.OKGREEN}✨ Goodbye! Thanks for using DeepCode CLI! ✨{Colors.ENDC}"
        )


@click.group(invoke_without_command=True)
@click.pass_context
@click.version_option(version="1.0.0", prog_name="DeepCode")
def cli(ctx):
    """
    DeepCode - Open-Source Code Agent by Data Intelligence Lab @ HKU

    🧬 Revolutionizing research reproducibility through collaborative AI
    ⚡ Transform research papers into working code automatically
    """
    # If no subcommand is provided, run the interactive session by default
    if ctx.invoked_subcommand is None:
        asyncio.run(run_interactive_cli())


@cli.command()
def run():
    """Run the interactive DeepCode CLI session"""
    asyncio.run(run_interactive_cli())


@cli.command()
def config():
    """Show or modify DeepCode configuration settings"""
    click.echo(f"{Colors.BOLD}{Colors.CYAN}⚙️  DeepCode Configuration{Colors.ENDC}")
    click.echo(f"{Colors.YELLOW}Configuration management coming soon!{Colors.ENDC}")
    click.echo("\nPlanned features:")
    click.echo("  • View current configuration")
    click.echo("  • Set default processing mode (comprehensive/optimized)")
    click.echo("  • Configure API keys and endpoints")
    click.echo("  • Manage workspace settings")


@cli.command()
@click.option('--cache', is_flag=True, help='Clean Python cache files (__pycache__)')
@click.option('--logs', is_flag=True, help='Clean log files')
@click.option('--all', 'clean_all', is_flag=True, help='Clean all temporary files')
def clean(cache, logs, clean_all):
    """Clean temporary files and caches"""
    click.echo(f"{Colors.BOLD}{Colors.CYAN}🧹 DeepCode Cleanup Utility{Colors.ENDC}")

    if not (cache or logs or clean_all):
        click.echo(f"{Colors.WARNING}No cleanup options specified. Use --help for options.{Colors.ENDC}")
        return

    if clean_all or cache:
        click.echo(f"\n{Colors.YELLOW}Cleaning Python cache files...{Colors.ENDC}")
        if os.name == "nt":  # Windows
            os.system(
                "powershell -Command \"Get-ChildItem -Path . -Filter '__pycache__' -Recurse -Directory | Remove-Item -Recurse -Force\" 2>nul"
            )
        else:  # Unix/Linux/macOS
            os.system('find . -type d -name "__pycache__" -exec rm -r {} + 2>/dev/null')
        click.echo(f"{Colors.OKGREEN}✓ Cache files cleaned{Colors.ENDC}")

    if clean_all or logs:
        click.echo(f"\n{Colors.YELLOW}Cleaning log files...{Colors.ENDC}")
        log_dirs = ["logs", "cli/logs"]
        for log_dir in log_dirs:
            if os.path.exists(log_dir):
                import shutil
                try:
                    shutil.rmtree(log_dir)
                    os.makedirs(log_dir, exist_ok=True)
                    click.echo(f"{Colors.OKGREEN}✓ Cleaned {log_dir}{Colors.ENDC}")
                except Exception as e:
                    click.echo(f"{Colors.FAIL}✗ Failed to clean {log_dir}: {e}{Colors.ENDC}")

    click.echo(f"\n{Colors.OKGREEN}✨ Cleanup complete!{Colors.ENDC}")


if __name__ == "__main__":
    cli()
