#!/usr/bin/env python3
"""
DeepCode CLI - Open-Source Code Agent
深度代码CLI - 开源代码智能体

🧬 Data Intelligence Lab @ HKU
⚡ Revolutionizing Research Reproducibility through Multi-Agent Architecture
"""

import os
import sys
import asyncio
import argparse
import io

# 禁止生成.pyc文件
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# 添加项目根目录到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# 导入CLI应用
from cli.cli_app import CLIApp, Colors


def _configure_console_encoding():
    """Prefer UTF-8 output and degrade safely on legacy Windows consoles."""
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        else:
            sys.stdout = io.TextIOWrapper(
                sys.stdout.detach(), encoding="utf-8", errors="replace"
            )
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        else:
            sys.stderr = io.TextIOWrapper(
                sys.stderr.detach(), encoding="utf-8", errors="replace"
            )
    except Exception:
        pass


def _safe_print(text: str):
    """Print text safely on terminals with non-UTF-8 encodings."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sanitized = text.encode(encoding, errors="replace").decode(
            encoding, errors="replace"
        )
        print(sanitized)


_configure_console_encoding()


def print_enhanced_banner():
    """显示增强版启动横幅"""
    banner = f"""
{Colors.CYAN}╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║    {Colors.BOLD}{Colors.MAGENTA}🧬 DeepCode - Open-Source Code Agent{Colors.CYAN}                              ║
║                                                                              ║
║    {Colors.BOLD}{Colors.YELLOW}⚡ DATA INTELLIGENCE LAB @ HKU ⚡{Colors.CYAN}                                ║
║                                                                              ║
║    Revolutionizing research reproducibility through collaborative AI         ║
║    Building the future where code is reproduced from natural language       ║
║                                                                              ║
║    {Colors.BOLD}{Colors.GREEN}🤖 Key Features:{Colors.CYAN}                                                    ║
║    • Automated paper-to-code reproduction                                   ║
║    • Multi-agent collaborative architecture                                 ║
║    • Open-source and extensible design                                      ║
║    • Join our growing research community                                    ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
"""
    _safe_print(banner)


def check_environment():
    """检查运行环境"""
    print(f"{Colors.CYAN}🔍 Checking environment...{Colors.ENDC}")

    # 检查Python版本
    if sys.version_info < (3, 8):
        print(
            f"{Colors.FAIL}❌ Python 3.8+ required. Current: {sys.version}{Colors.ENDC}"
        )
        return False

    print(f"{Colors.OKGREEN}✅ Python {sys.version.split()[0]} - OK{Colors.ENDC}")

    # 检查必要模块
    required_modules = [
        ("asyncio", "Async IO support"),
        ("pathlib", "Path handling"),
        ("typing", "Type hints"),
    ]

    missing_modules = []
    for module, desc in required_modules:
        try:
            __import__(module)
            print(f"{Colors.OKGREEN}✅ {desc} - OK{Colors.ENDC}")
        except ImportError:
            missing_modules.append(module)
            print(f"{Colors.FAIL}❌ {desc} - Missing{Colors.ENDC}")

    if missing_modules:
        print(
            f"{Colors.FAIL}❌ Missing required modules: {', '.join(missing_modules)}{Colors.ENDC}"
        )
        return False

    print(f"{Colors.OKGREEN}✅ Environment check passed{Colors.ENDC}")
    return True


def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="DeepCode CLI - Open-Source Code Agent by Data Intelligence Lab @ HKU",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
{Colors.BOLD}Examples:{Colors.ENDC}
  {Colors.CYAN}python main_cli.py{Colors.ENDC}                                      # Interactive mode
  {Colors.CYAN}python main_cli.py --file paper.pdf{Colors.ENDC}                       # Process file directly
  {Colors.CYAN}python main_cli.py --url https://...{Colors.ENDC}                      # Process URL directly
  {Colors.CYAN}python main_cli.py --chat "Build a web app..."{Colors.ENDC}            # Process chat requirements
  {Colors.CYAN}python main_cli.py --requirement "ML system for..."{Colors.ENDC}       # Guided requirement analysis (NEW)
  {Colors.CYAN}python main_cli.py --optimized{Colors.ENDC}                            # Use optimized mode
  {Colors.CYAN}python main_cli.py --disable-segmentation{Colors.ENDC}                 # Disable document segmentation
  {Colors.CYAN}python main_cli.py --segmentation-threshold 30000{Colors.ENDC}         # Custom segmentation threshold

{Colors.BOLD}Pipeline Modes:{Colors.ENDC}
  {Colors.GREEN}Comprehensive{Colors.ENDC}:          Full intelligence analysis with indexing
  {Colors.YELLOW}Optimized{Colors.ENDC}:              Fast processing without indexing
  {Colors.BLUE}Requirement Analysis{Colors.ENDC}:   Guided Q&A to refine requirements (NEW)

{Colors.BOLD}Document Processing:{Colors.ENDC}
  {Colors.BLUE}Smart Segmentation{Colors.ENDC}: Intelligent document segmentation for large papers
  {Colors.MAGENTA}Supported Formats{Colors.ENDC}: PDF, DOCX, DOC, PPT, PPTX, XLS, XLSX, HTML, TXT, MD
        """,
    )

    parser.add_argument(
        "--file", "-f", type=str, help="Process a specific file (PDF, DOCX, TXT, etc.)"
    )

    parser.add_argument(
        "--url", "-u", type=str, help="Process a research paper from URL"
    )

    parser.add_argument(
        "--chat",
        "-t",
        type=str,
        help="Process coding requirements via chat input (provide requirements as argument)",
    )

    parser.add_argument(
        "--requirement",
        "-r",
        type=str,
        help="Process requirements via guided analysis (provide initial idea as argument)",
    )

    parser.add_argument(
        "--optimized",
        "-o",
        action="store_true",
        help="Use optimized mode (skip indexing for faster processing)",
    )

    parser.add_argument(
        "--disable-segmentation",
        action="store_true",
        help="Disable intelligent document segmentation (use traditional full-document processing)",
    )

    parser.add_argument(
        "--segmentation-threshold",
        type=int,
        default=50000,
        help="Document size threshold (characters) to trigger segmentation (default: 50000)",
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )

    return parser.parse_args()


async def run_direct_processing(app: CLIApp, input_source: str, input_type: str):
    """直接处理模式（非交互式）"""
    try:
        print(
            f"\n{Colors.BOLD}{Colors.CYAN}🚀 Starting direct processing mode...{Colors.ENDC}"
        )
        print(f"{Colors.CYAN}Input: {input_source}{Colors.ENDC}")
        print(f"{Colors.CYAN}Type: {input_type}{Colors.ENDC}")
        print(
            f"{Colors.CYAN}Mode: {'🧠 Comprehensive' if app.cli.enable_indexing else '⚡ Optimized'}{Colors.ENDC}"
        )

        # 初始化应用
        init_result = await app.initialize_mcp_app()
        if init_result["status"] != "success":
            print(
                f"{Colors.FAIL}❌ Initialization failed: {init_result['message']}{Colors.ENDC}"
            )
            return False

        # 处理输入
        result = await app.process_input(input_source, input_type)

        if result["status"] == "success":
            print(
                f"\n{Colors.BOLD}{Colors.OKGREEN}🎉 Processing completed successfully!{Colors.ENDC}"
            )
            return True
        else:
            print(
                f"\n{Colors.BOLD}{Colors.FAIL}❌ Processing failed: {result.get('error', 'Unknown error')}{Colors.ENDC}"
            )
            return False

    except Exception as e:
        print(f"\n{Colors.FAIL}❌ Direct processing error: {str(e)}{Colors.ENDC}")
        return False
    finally:
        await app.cleanup_mcp_app()


async def run_requirement_analysis(app: CLIApp, initial_idea: str):
    """需求分析模式（非交互式） - NEW: matching UI version"""
    try:
        print(
            f"\n{Colors.BOLD}{Colors.BLUE}🧠 Starting requirement analysis mode...{Colors.ENDC}"
        )
        print(f"{Colors.CYAN}Initial Idea: {initial_idea}{Colors.ENDC}")

        # 初始化应用
        init_result = await app.initialize_mcp_app()
        if init_result["status"] != "success":
            print(
                f"{Colors.FAIL}❌ Initialization failed: {init_result['message']}{Colors.ENDC}"
            )
            return False

        # 执行需求分析工作流
        result = await app.process_requirement_analysis_non_interactive(initial_idea)

        if result["status"] == "success":
            print(
                f"\n{Colors.BOLD}{Colors.OKGREEN}🎉 Requirement analysis completed successfully!{Colors.ENDC}"
            )
            return True
        else:
            print(
                f"\n{Colors.BOLD}{Colors.FAIL}❌ Requirement analysis failed: {result.get('error', 'Unknown error')}{Colors.ENDC}"
            )
            return False

    except Exception as e:
        print(f"\n{Colors.FAIL}❌ Requirement analysis error: {str(e)}{Colors.ENDC}")
        return False
    finally:
        await app.cleanup_mcp_app()


async def main():
    """主函数"""
    # 解析命令行参数
    args = parse_arguments()

    # 显示横幅
    print_enhanced_banner()

    # 检查环境
    if not check_environment():
        print(
            f"\n{Colors.FAIL}🚨 Environment check failed. Please fix the issues and try again.{Colors.ENDC}"
        )
        sys.exit(1)

    try:
        # 创建CLI应用
        app = CLIApp()

        # 设置配置 - 默认禁用索引功能以加快处理速度
        if args.optimized:
            app.cli.enable_indexing = False
            print(
                f"\n{Colors.YELLOW}⚡ Optimized mode enabled - indexing disabled{Colors.ENDC}"
            )
        else:
            # 默认也禁用索引功能
            app.cli.enable_indexing = False
            print(
                f"\n{Colors.YELLOW}⚡ Fast mode enabled - indexing disabled by default{Colors.ENDC}"
            )

        # Configure document segmentation settings
        if hasattr(args, "disable_segmentation") and args.disable_segmentation:
            print(
                f"\n{Colors.MAGENTA}📄 Document segmentation disabled - using traditional processing{Colors.ENDC}"
            )
            app.cli.segmentation_enabled = False
            app.cli.segmentation_threshold = args.segmentation_threshold
            app.cli._save_segmentation_config()
        else:
            print(
                f"\n{Colors.BLUE}📄 Smart document segmentation enabled (threshold: {args.segmentation_threshold} chars){Colors.ENDC}"
            )
            app.cli.segmentation_enabled = True
            app.cli.segmentation_threshold = args.segmentation_threshold
            app.cli._save_segmentation_config()

        # 检查是否为直接处理模式
        if args.file or args.url or args.chat or args.requirement:
            if args.file:
                # 验证文件存在
                if not os.path.exists(args.file):
                    print(f"{Colors.FAIL}❌ File not found: {args.file}{Colors.ENDC}")
                    sys.exit(1)
                # 使用 file:// 前缀保持与交互模式一致，确保文件被复制而非移动
                file_url = f"file://{os.path.abspath(args.file)}"
                success = await run_direct_processing(app, file_url, "file")
            elif args.url:
                success = await run_direct_processing(app, args.url, "url")
            elif args.chat:
                # 验证chat输入长度
                if len(args.chat.strip()) < 20:
                    print(
                        f"{Colors.FAIL}❌ Chat input too short. Please provide more detailed requirements (at least 20 characters){Colors.ENDC}"
                    )
                    sys.exit(1)
                success = await run_direct_processing(app, args.chat, "chat")
            elif args.requirement:
                # NEW: Requirement analysis mode
                # 验证需求输入长度
                if len(args.requirement.strip()) < 10:
                    print(
                        f"{Colors.FAIL}❌ Requirement input too short. Please provide more details (at least 10 characters){Colors.ENDC}"
                    )
                    sys.exit(1)
                success = await run_requirement_analysis(app, args.requirement)

            sys.exit(0 if success else 1)
        else:
            # 交互式模式
            print(f"\n{Colors.CYAN}🎮 Starting interactive mode...{Colors.ENDC}")
            await app.run_interactive_session()

    except KeyboardInterrupt:
        print(f"\n{Colors.WARNING}⚠️  Application interrupted by user{Colors.ENDC}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.FAIL}❌ Application errors: {str(e)}{Colors.ENDC}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
