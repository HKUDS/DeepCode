#!/usr/bin/env python3
"""
DeepCode - CLI Research Engine Launcher
DeepCode - CLI研究引擎启动器

🧬 Open-Source Code Agent by Data Intelligence Lab @ HKU (CLI Edition)
⚡ Revolutionizing research reproducibility through collaborative AI via command line
"""

import sys
from pathlib import Path


def check_dependencies():
    """检查必要的依赖是否已安装 / Check if necessary dependencies are installed"""
    import importlib.util

    print("🔍 Checking CLI dependencies...")

    missing_deps = []

    # Check asyncio availability
    if importlib.util.find_spec("asyncio") is not None:
        print("✅ Asyncio is available")
    else:
        missing_deps.append("asyncio")

    # Check PyYAML availability
    if importlib.util.find_spec("yaml") is not None:
        print("✅ PyYAML is installed")
    else:
        missing_deps.append("pyyaml")

    # Check Tkinter availability
    if importlib.util.find_spec("tkinter") is not None:
        print("✅ Tkinter is available (for file dialogs)")
    else:
        print("⚠️  Tkinter not available - file dialogs will use manual input")

    # Check for DeepCode-native runtime (replaces legacy mcp-agent)
    if importlib.util.find_spec("core.compat") is not None:
        print("✅ DeepCode core runtime is available")
    else:
        missing_deps.append("core (DeepCode core runtime missing)")

    if importlib.util.find_spec("mcp") is not None:
        print("✅ MCP SDK is installed")
    else:
        missing_deps.append("mcp")

    # Check for workflow dependencies
    # 添加项目根目录到路径
    current_dir = Path(__file__).parent
    project_root = current_dir.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    if importlib.util.find_spec("workflows.agent_orchestration_engine") is not None:
        print("✅ Workflow modules are available")
    else:
        print("⚠️  Workflow modules may not be properly configured")

    # Check for CLI components
    if importlib.util.find_spec("cli.cli_app") is not None:
        print("✅ CLI application components are available")
    else:
        print("❌ CLI application components missing")
        missing_deps.append("cli-components")

    if missing_deps:
        print("\n❌ Missing dependencies:")
        for dep in missing_deps:
            print(f"   - {dep}")
        print("\nPlease install missing dependencies using:")
        print(
            f"pip install {' '.join([d for d in missing_deps if d != 'cli-components'])}"
        )
        if "cli-components" in missing_deps:
            print(
                "CLI components appear to be missing - please check the cli/ directory"
            )
        return False

    print("✅ All CLI dependencies satisfied")
    return True


def print_banner():
    """显示CLI启动横幅 / Display CLI startup banner"""
    banner = """
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║    🧬 DeepCode - Open-Source Code Agent                      ║
║                                                              ║
║    ⚡ DATA INTELLIGENCE LAB @ HKU ⚡                        ║
║                                                              ║
║                               ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""
    print(banner)


def main():
    """主函数 / Main function"""
    print_banner()

    # 检查依赖 / Check dependencies
    if not check_dependencies():
        print("\n🚨 Please install missing dependencies and try again.")
        sys.exit(1)

    # 获取当前脚本目录 / Get current script directory
    current_dir = Path(__file__).parent
    project_root = current_dir.parent
    cli_app_path = current_dir / "cli_app.py"

    # 检查cli_app.py是否存在 / Check if cli_app.py exists
    if not cli_app_path.exists():
        print(f"❌ CLI application file not found: {cli_app_path}")
        print("Please ensure the cli/cli_app.py file exists.")
        sys.exit(1)

    print(f"\n📁 CLI App location: {cli_app_path}")
    print("🖥️  Starting DeepCode CLI interface...")
    print("🚀 Initializing command line application")
    print("=" * 70)
    print("💡 Tip: Follow the interactive prompts to process your research")
    print("🛑 Press Ctrl+C to exit at any time")
    print("=" * 70)

    # 启动CLI应用 / Launch CLI application
    try:
        # 导入并运行CLI应用
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))  # 添加项目根目录到路径
        from cli.cli_app import main as cli_main

        print("\n🎯 Launching CLI application...")

        # 使用asyncio运行主函数
        import asyncio

        asyncio.run(cli_main())

    except KeyboardInterrupt:
        print("\n\n🛑 DeepCode CLI stopped by user")
        print("Thank you for using DeepCode CLI! 🧬")
    except ImportError as e:
        print(f"\n❌ Failed to import CLI application: {e}")
        print("Please check if all modules are properly installed.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        print("Please check your Python environment and try again.")
        sys.exit(1)


if __name__ == "__main__":
    main()
