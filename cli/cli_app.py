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

    def __init__(self):
        self.cli = CLIInterface()
        self.workflow_adapter = CLIWorkflowAdapter(cli_interface=self.cli)
        self.app = None  # Will be initialized by workflow adapter
        self.logger = None
        self.context = None
        # Document segmentation will be managed by CLI interface

    async def initialize_mcp_app(self):
        """初始化MCP应用 - 使用工作流适配器"""
        # Workflow adapter will handle MCP initialization
        return await self.workflow_adapter.initialize_mcp_app()

    async def cleanup_mcp_app(self):
        """清理MCP应用 - 使用工作流适配器"""
        await self.workflow_adapter.cleanup_mcp_app()

    async def process_requirement_analysis_non_interactive(self, initial_idea: str):
        """处理需求分析工作流（非交互式，用于命令行参数） (NEW: matching UI version)"""
        try:
            self.cli.print_separator()
            self.cli.print_status(
                "🧠 Starting requirement analysis workflow...", "info"
            )

            # Step 1: Generate guiding questions
            self.cli.print_status(
                "🤖 Generating AI-guided questions to refine your requirements...",
                "processing",
            )

            questions_result = (
                await self.workflow_adapter.execute_requirement_analysis_workflow(
                    user_input=initial_idea, analysis_mode="generate_questions"
                )
            )

            if questions_result["status"] != "success":
                self.cli.print_status(
                    f"❌ Failed to generate questions: {questions_result.get('error', 'Unknown error')}",
                    "error",
                )
                return questions_result

            # Step 2: Display questions
            questions_json = questions_result["result"]
            self.cli.display_guiding_questions(questions_json)

            # For non-interactive mode, we can't get user answers, so we provide a summary
            self.cli.print_status(
                "ℹ️  In non-interactive mode, using initial idea for implementation",
                "info",
            )
            self.cli.print_status(
                "💡 For guided analysis, please use interactive mode (python main_cli.py)",
                "info",
            )

            # Proceed directly with the initial idea as the requirement
            self.cli.print_status(
                "🚀 Starting code implementation based on initial requirements...",
                "processing",
            )

            implementation_result = await self.process_input(initial_idea, "chat")

            return {
                "status": "success",
                "questions_generated": questions_result,
                "implementation": implementation_result,
            }

        except Exception as e:
            error_msg = str(e)
            self.cli.print_error_box("Requirement Analysis Error", error_msg)
            self.cli.print_status(
                f"Error during requirement analysis: {error_msg}", "error"
            )

            return {"status": "error", "error": error_msg}

    async def process_requirement_analysis(self):
        """处理需求分析工作流（交互式） (NEW: matching UI version)"""
        try:
            # Step 1: Get initial requirements from user
            self.cli.print_separator()
            self.cli.print_status(
                "🧠 Starting requirement analysis workflow...", "info"
            )

            user_input = self.cli.get_requirement_analysis_input()

            if not user_input:
                self.cli.print_status("Requirement analysis cancelled", "warning")
                return {"status": "cancelled"}

            # Step 2: Generate guiding questions
            self.cli.print_status(
                "🤖 Generating AI-guided questions to refine your requirements...",
                "processing",
            )

            questions_result = (
                await self.workflow_adapter.execute_requirement_analysis_workflow(
                    user_input=user_input, analysis_mode="generate_questions"
                )
            )

            if questions_result["status"] != "success":
                self.cli.print_status(
                    f"❌ Failed to generate questions: {questions_result.get('error', 'Unknown error')}",
                    "error",
                )
                return questions_result

            # Step 3: Display questions and get user answers
            questions_json = questions_result["result"]
            self.cli.display_guiding_questions(questions_json)

            # Ask if user wants to answer the questions
            proceed = (
                input(
                    f"\n{Colors.BOLD}{Colors.YELLOW}Would you like to answer these questions? (y/n):{Colors.ENDC} "
                )
                .strip()
                .lower()
            )

            if proceed != "y":
                self.cli.print_status(
                    "You can still use the initial requirements for chat input",
                    "info",
                )
                return {"status": "partial", "initial_requirements": user_input}

            user_answers = self.cli.get_question_answers(questions_json)

            # Step 4: Generate requirement summary
            self.cli.print_status(
                "📄 Generating detailed requirement document...", "processing"
            )

            summary_result = (
                await self.workflow_adapter.execute_requirement_analysis_workflow(
                    user_input=user_input,
                    analysis_mode="summarize_requirements",
                    user_answers=user_answers,
                )
            )

            if summary_result["status"] != "success":
                self.cli.print_status(
                    f"❌ Failed to generate summary: {summary_result.get('error', 'Unknown error')}",
                    "error",
                )
                return summary_result

            # Step 5: Display requirement summary
            requirement_summary = summary_result["result"]
            should_proceed = self.cli.display_requirement_summary(requirement_summary)

            if should_proceed:
                # Step 6: Proceed with chat-based implementation
                self.cli.print_status(
                    "🚀 Starting code implementation based on analyzed requirements...",
                    "processing",
                )

                implementation_result = await self.process_input(
                    requirement_summary, "chat"
                )

                return {
                    "status": "success",
                    "requirement_analysis": summary_result,
                    "implementation": implementation_result,
                }
            else:
                self.cli.print_status(
                    "Requirement analysis completed. Implementation skipped.", "info"
                )
                return {
                    "status": "success",
                    "requirement_analysis": summary_result,
                    "implementation": None,
                }

        except Exception as e:
            error_msg = str(e)
            self.cli.print_error_box("Requirement Analysis Error", error_msg)
            self.cli.print_status(
                f"Error during requirement analysis: {error_msg}", "error"
            )

            return {"status": "error", "error": error_msg}

    async def process_input(self, input_source: str, input_type: str):
        """处理输入源（URL或文件）- 使用升级版智能体编排引擎"""
        try:
            # Document segmentation configuration is managed by CLI interface

            self.cli.print_separator()
            self.cli.print_status(
                "🚀 Starting intelligent agent orchestration...", "processing"
            )

            # 显示处理阶段（根据配置决定）
            chat_mode = input_type == "chat"
            self.cli.display_processing_stages(
                0, self.cli.enable_indexing, chat_mode=chat_mode
            )

            # 使用工作流适配器进行处理
            result = await self.workflow_adapter.process_input_with_orchestration(
                input_source=input_source,
                input_type=input_type,
                enable_indexing=self.cli.enable_indexing,
                enable_plan_review=getattr(self.cli, "enable_plan_review", True),
            )

            if result["status"] == "success":
                # 显示完成状态
                if chat_mode:
                    final_stage = 4
                else:
                    final_stage = 8 if self.cli.enable_indexing else 5
                self.cli.display_processing_stages(
                    final_stage, self.cli.enable_indexing, chat_mode=chat_mode
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
            elif result["status"] == "cancelled":
                self.cli.print_status(
                    f"Processing cancelled: {result.get('error', 'cancelled')}",
                    "warning",
                )

            else:
                self.cli.print_status(
                    f"❌ Processing failed: {result.get('error', 'Unknown error')}",
                    "error",
                )

            # 添加到历史记录
            self.cli.add_to_history(input_source, result)

            return result

        except Exception as e:
            error_msg = str(e)
            self.cli.print_error_box("Agent Orchestration Error", error_msg)
            self.cli.print_status(f"Error during orchestration: {error_msg}", "error")

            # 添加错误到历史记录
            error_result = {"status": "error", "error": error_msg}
            self.cli.add_to_history(input_source, error_result)

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
        except Exception:
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

    # ------------------------------------------------------------------
    # @<path-or-url> inline input (Cursor / Codex CLI style)
    # ------------------------------------------------------------------

    async def _handle_at_shortcut(self, target: str) -> str:
        """Dispatch ``@<file-path>`` or ``@<url>`` typed at the menu prompt.

        Returns the *effective* menu letter (``"f"`` or ``"u"``) so the
        outer loop knows whether to offer ``ask_continue`` afterwards;
        returns an empty string when the input was malformed (in which
        case the user already saw an error toast).
        """
        if not target:
            self.cli.print_status("Usage: @<file-path>   or   @<url>", "warning")
            return ""

        # Strip optional surrounding quotes (clipboard pastes often
        # bring them along: @"C:\path with spaces\file.pdf").
        if (target.startswith('"') and target.endswith('"')) or (
            target.startswith("'") and target.endswith("'")
        ):
            target = target[1:-1].strip()

        lowered = target.lower()

        # URL branch — anything starting with http(s)://
        if lowered.startswith(("http://", "https://")):
            self.cli.print_status(f"@-shortcut → URL: {target}", "info")
            await self.process_input(target, "url")
            return "u"

        # File branch — accept ``file://`` prefix and existence check.
        raw_path = target[len("file://") :] if lowered.startswith("file://") else target
        if not os.path.exists(raw_path):
            self.cli.print_status(f"Path not found: {raw_path}", "error")
            return ""

        abspath = os.path.abspath(raw_path)
        self.cli.print_status(f"@-shortcut → File: {abspath}", "info")
        await self.process_input(f"file://{abspath}", "file")
        return "f"

    # ------------------------------------------------------------------
    # Slash command dispatcher (in-CLI session management)
    # ------------------------------------------------------------------

    def _handle_slash_command(self, raw: str) -> None:
        """Route a ``/...`` line typed at the menu prompt.

        Mirrors the Cursor / Claude-Code style: ``/resume`` lists recent
        sessions and lets the user pick one; ``/new`` starts a fresh
        session; ``/session`` shows the active one; ``/help`` lists all
        commands.

        We don't bother stacking ``contextvars`` tokens here — the very
        first ``set_session`` happens in ``main_cli.main`` and its
        ``finally`` will reset on process exit. Subsequent in-CLI
        switches simply overwrite the current value, which is exactly
        what we want for a long-lived REPL.
        """
        try:
            from core.observability import current_session_id, set_session
            from core.sessions import get_default_store
        except Exception as exc:
            self.cli.print_status(f"Session subsystem unavailable: {exc}", "error")
            return

        parts = raw.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        store = get_default_store()

        if cmd in ("/help", "/?", "/h"):
            self.cli.print_slash_help()
            return

        if cmd in ("/session", "/whoami"):
            sid = current_session_id()
            session = store.get_session(sid) if sid else None
            self.cli.print_active_session(sid, session.title if session else None)
            return

        if cmd in ("/resume", "/sessions", "/load"):
            summaries = store.list_sessions(limit=20)
            picked = self.cli.pick_session_interactive(summaries)
            if not picked:
                return
            session = store.get_session(picked)
            if session is None:
                # Treat input as a prefix — try to disambiguate against
                # the listed summaries before giving up.
                candidates = [s for s in summaries if s.session_id.startswith(picked)]
                if len(candidates) == 1:
                    session = store.get_session(candidates[0].session_id)
                elif len(candidates) > 1:
                    self.cli.print_status(
                        f"'{picked}' is ambiguous ({len(candidates)} matches). "
                        "Type a longer prefix or the full ID.",
                        "warning",
                    )
                    return
            if session is None:
                self.cli.print_status(f"Session '{picked}' not found.", "error")
                return
            set_session(session.session_id)
            self.cli.print_status(
                f"Switched to session {session.session_id}"
                + (f" — {session.title}" if session.title else ""),
                "success",
            )
            if session.tasks:
                last = session.tasks[-1]
                self.cli.print_status(
                    f"Last task: {last.task_id} [{last.task_kind}] "
                    f"status={last.status}",
                    "info",
                )
            return

        if cmd in ("/new", "/new-session"):
            session = store.create_session(title=arg)
            set_session(session.session_id)
            self.cli.print_status(
                f"Created and switched to new session "
                f"{session.session_id}"
                + (f" — {session.title}" if session.title else ""),
                "success",
            )
            return

        self.cli.print_status(
            f"Unknown slash command '{cmd}'. Type /help to see available commands.",
            "warning",
        )

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
                # Raw text preserves case (needed for @<path> on
                # case-sensitive URL components); ``choice`` is the
                # lower-cased view used for menu-letter / slash
                # matching.
                choice_raw = self.cli.get_user_input()
                choice = choice_raw.lower()

                # Slash commands (Cursor / Claude-Code style) — handled
                # before the menu-letter dispatcher so users can type
                # /resume, /new, /session, /help at the menu prompt.
                if choice.startswith("/"):
                    self._handle_slash_command(choice)
                    continue

                # @-shortcut for inline file path / URL input. We rewrite
                # ``choice`` to the equivalent menu letter so the
                # ask_continue check below behaves the same as if the
                # user had picked [F] / [U] from the menu.
                if choice_raw.startswith("@"):
                    effective = await self._handle_at_shortcut(choice_raw[1:].strip())
                    if effective in ("f", "u"):
                        choice = effective
                    else:
                        # Malformed input — error already shown; loop.
                        continue

                elif choice in ["q", "quit", "exit"]:
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
                    if chat_input:
                        await self.process_input(chat_input, "chat")

                elif choice in ["r", "req", "requirement", "requirements"]:
                    # NEW: Requirement Analysis workflow
                    await self.process_requirement_analysis()

                elif choice in ["h", "history"]:
                    self.cli.show_history()

                elif choice in ["c", "config", "configure"]:
                    # Show configuration menu - all settings managed by CLI interface
                    self.cli.show_configuration_menu()

                else:
                    self.cli.print_status(
                        "Invalid choice. Please select U, F, T, R, C, H, Q, "
                        "type /help for slash commands, or @<path>/@<url>.",
                        "warning",
                    )

                # 询问是否继续
                if self.cli.is_running and choice in [
                    "u",
                    "f",
                    "t",
                    "r",
                    "chat",
                    "text",
                    "req",
                    "requirement",
                    "requirements",
                ]:
                    if not self.cli.ask_continue():
                        self.cli.is_running = False
                        self.cli.print_status("Session ended by user", "info")

        except KeyboardInterrupt:
            print(f"\n{Colors.WARNING}⚠️  Process interrupted by user{Colors.ENDC}")
        except Exception as e:
            print(f"\n{Colors.FAIL}❌ Unexpected error: {str(e)}{Colors.ENDC}")
        finally:
            # 清理资源
            await self.cleanup_mcp_app()


async def main():
    """主函数"""
    start_time = time.time()

    try:
        # 创建并运行CLI应用
        app = CLIApp()
        await app.run_interactive_session()

    except KeyboardInterrupt:
        print(f"\n{Colors.WARNING}⚠️  Application interrupted by user{Colors.ENDC}")
    except Exception as e:
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


if __name__ == "__main__":
    asyncio.run(main())
