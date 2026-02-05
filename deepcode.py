#!/usr/bin/env python3
"""
DeepCode - AI Research Engine Launcher

🧬 Next-Generation AI Research Automation Platform
⚡ Transform research papers into working code automatically

Cross-platform support: Windows, macOS, Linux
"""

import os
import sys
import subprocess
import signal
import platform
import socket
import time
from pathlib import Path


# Global process references for cleanup
_backend_process = None
_frontend_process = None


def get_platform():
    """Get current platform"""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    elif system == "windows":
        return "windows"
    else:
        return "linux"


def check_dependencies():
    """Check if necessary dependencies are installed for new UI"""
    import importlib.util
    import shutil

    print("🔍 Checking dependencies...")

    missing_deps = []
    missing_system_deps = []

    # Check FastAPI availability (for backend)
    if importlib.util.find_spec("fastapi") is not None:
        print("✅ FastAPI is installed")
    else:
        missing_deps.append("fastapi>=0.104.0")

    # Check uvicorn availability (for backend server)
    if importlib.util.find_spec("uvicorn") is not None:
        print("✅ Uvicorn is installed")
    else:
        missing_deps.append("uvicorn>=0.24.0")

    # Check PyYAML availability
    if importlib.util.find_spec("yaml") is not None:
        print("✅ PyYAML is installed")
    else:
        missing_deps.append("pyyaml>=6.0")

    # Check pydantic-settings availability
    if importlib.util.find_spec("pydantic_settings") is not None:
        print("✅ Pydantic-settings is installed")
    else:
        missing_deps.append("pydantic-settings>=2.0.0")

    # Check Node.js availability (for frontend)
    node_cmd = "node.exe" if get_platform() == "windows" else "node"
    if shutil.which(node_cmd) or shutil.which("node"):
        try:
            result = subprocess.run(
                ["node", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                shell=(get_platform() == "windows"),
            )
            if result.returncode == 0:
                print(f"✅ Node.js is installed ({result.stdout.strip()})")
        except Exception:
            missing_system_deps.append("Node.js")
    else:
        missing_system_deps.append("Node.js")
        print("❌ Node.js not found (required for frontend)")

    # Check npm availability
    npm_cmd = "npm.cmd" if get_platform() == "windows" else "npm"
    if shutil.which(npm_cmd) or shutil.which("npm"):
        print("✅ npm is available")
    else:
        missing_system_deps.append("npm")
        print("❌ npm not found (required for frontend)")

    # Display missing dependencies
    if missing_deps or missing_system_deps:
        print("\n📋 Dependency Status:")

        if missing_deps:
            print("❌ Missing Python dependencies:")
            for dep in missing_deps:
                print(f"   - {dep}")
            print(f"\nInstall with: pip install {' '.join(missing_deps)}")

        if missing_system_deps:
            print("\n❌ Missing system dependencies:")
            for dep in missing_system_deps:
                print(f"   - {dep}")
            print("\nInstall Node.js:")
            print("   - Windows/macOS: https://nodejs.org/")
            print("   - macOS: brew install node")
            print("   - Ubuntu/Debian: sudo apt-get install nodejs npm")

        # Fail if critical dependencies are missing
        if missing_deps or missing_system_deps:
            return False
    else:
        print("✅ All dependencies satisfied")

    return True


def is_port_in_use(port: int) -> bool:
    """Check if a port is in use (cross-platform)"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def kill_process_on_port(port: int):
    """Kill process using a specific port (cross-platform)"""
    current_platform = get_platform()

    try:
        if current_platform == "windows":
            # Windows: use netstat and taskkill
            result = subprocess.run(
                f"netstat -ano | findstr :{port}",
                capture_output=True,
                text=True,
                shell=True,
            )
            if result.stdout:
                for line in result.stdout.strip().split("\n"):
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        if pid.isdigit():
                            subprocess.run(
                                f"taskkill /F /PID {pid}",
                                shell=True,
                                capture_output=True,
                            )
                            print(f"  ✓ Killed process on port {port} (PID: {pid})")
        else:
            # macOS/Linux: use lsof
            result = subprocess.run(
                f"lsof -ti :{port}", capture_output=True, text=True, shell=True
            )
            if result.stdout:
                pids = result.stdout.strip().split("\n")
                for pid in pids:
                    if pid.isdigit():
                        os.kill(int(pid), signal.SIGKILL)
                        print(f"  ✓ Killed process on port {port} (PID: {pid})")
    except Exception as e:
        print(f"  ⚠️ Could not kill process on port {port}: {e}")


def cleanup_ports():
    """Clean up ports 8000 and 5173 if in use"""
    for port in [8000, 5173]:
        if is_port_in_use(port):
            print(f"⚠️ Port {port} is in use, cleaning up...")
            kill_process_on_port(port)
            time.sleep(1)


def install_backend_deps():
    """Install backend dependencies if needed"""
    import importlib.util

    if importlib.util.find_spec("fastapi") is None:
        print("📦 Installing backend dependencies...")
        deps = [
            "fastapi",
            "uvicorn",
            "pydantic-settings",
            "python-multipart",
            "aiofiles",
            "websockets",
            "pyyaml",
        ]
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q"] + deps, check=True
        )
        print("✅ Backend dependencies installed")


def install_frontend_deps(frontend_dir: Path):
    """Install frontend dependencies if needed"""
    node_modules = frontend_dir / "node_modules"

    if not node_modules.exists():
        print("📦 Installing frontend dependencies (first run)...")
        npm_cmd = "npm.cmd" if get_platform() == "windows" else "npm"
        subprocess.run(
            [npm_cmd, "install"],
            cwd=frontend_dir,
            check=True,
            shell=(get_platform() == "windows"),
        )
        print("✅ Frontend dependencies installed")


def start_backend(backend_dir: Path):
    """Start the backend server"""
    global _backend_process

    print("🔧 Starting backend server...")

    # Use shell=True on Windows for proper command handling
    if get_platform() == "windows":
        _backend_process = subprocess.Popen(
            f'"{sys.executable}" -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload',
            cwd=backend_dir,
            shell=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        _backend_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--reload",
            ],
            cwd=backend_dir,
            start_new_session=True,  # Create new process group
        )

    # Wait for backend to start
    time.sleep(2)

    if _backend_process.poll() is None:
        print("✅ Backend started: http://localhost:8000")
        return True
    else:
        print("❌ Backend failed to start")
        return False


def start_frontend(frontend_dir: Path):
    """Start the frontend dev server"""
    global _frontend_process

    print("🎨 Starting frontend server...")

    npm_cmd = "npm.cmd" if get_platform() == "windows" else "npm"

    if get_platform() == "windows":
        _frontend_process = subprocess.Popen(
            f"{npm_cmd} run dev",
            cwd=frontend_dir,
            shell=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        _frontend_process = subprocess.Popen(
            [npm_cmd, "run", "dev"],
            cwd=frontend_dir,
            start_new_session=True,  # Create new process group
        )

    # Wait for frontend to start
    time.sleep(3)

    if _frontend_process.poll() is None:
        print("✅ Frontend started: http://localhost:5173")
        return True
    else:
        print("❌ Frontend failed to start")
        return False


def cleanup_processes():
    """Clean up running processes"""
    global _backend_process, _frontend_process

    print("\n🛑 Stopping services...")

    for name, proc in [("Backend", _backend_process), ("Frontend", _frontend_process)]:
        if proc and proc.poll() is None:
            try:
                if get_platform() == "windows":
                    # Windows: use taskkill with /T to kill tree
                    subprocess.run(
                        f"taskkill /F /T /PID {proc.pid}",
                        shell=True,
                        capture_output=True,
                    )
                else:
                    # Unix: kill the process group
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                        proc.wait(timeout=5)
                    except Exception:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                print(f"  ✓ {name} stopped")
            except Exception:
                # Fallback: try direct terminate
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                    print(f"  ✓ {name} stopped")
                except Exception:
                    try:
                        proc.kill()
                        print(f"  ✓ {name} killed")
                    except Exception:
                        print(f"  ⚠️ Could not stop {name}")

    # Also clean up any orphaned processes on ports
    time.sleep(0.5)
    for port in [8000, 5173]:
        if is_port_in_use(port):
            kill_process_on_port(port)

    print("✅ All services stopped")


def cleanup_cache():
    """Clean up Python cache files"""
    try:
        print("🧹 Cleaning up cache files...")
        # Clean up __pycache__ directories
        os.system('find . -type d -name "__pycache__" -exec rm -r {} + 2>/dev/null')
        # Clean up .pyc files
        os.system('find . -name "*.pyc" -delete 2>/dev/null')
        print("✅ Cache cleanup completed")
    except Exception as e:
        print(f"⚠️  Cache cleanup failed: {e}")


def print_banner():
    """Display startup banner"""
    banner = """
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║    🧬 DeepCode - AI Research Engine                          ║
║                                                              ║
║    ⚡ NEURAL • AUTONOMOUS • REVOLUTIONARY ⚡                ║
║                                                              ║
║    Transform research papers into working code               ║
║    Next-generation AI automation platform                   ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""
    print(banner)


def launch_classic_ui():
    """Launch classic Streamlit UI"""
    import importlib.util

    print("🌐 Launching Classic Streamlit UI...")

    # Check if Streamlit is installed
    if importlib.util.find_spec("streamlit") is None:
        print("❌ Streamlit is not installed.")
        print("Install with: pip install streamlit")
        sys.exit(1)

    current_dir = Path(__file__).parent
    streamlit_app_path = current_dir / "ui" / "streamlit_app.py"

    if not streamlit_app_path.exists():
        print(f"❌ Streamlit app not found: {streamlit_app_path}")
        sys.exit(1)

    print(f"📁 UI App: {streamlit_app_path}")
    print("🚀 Launching on http://localhost:8501")
    print("=" * 70)

    try:
        cmd = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(streamlit_app_path),
            "--server.port",
            "8501",
            "--server.address",
            "localhost",
            "--browser.gatherUsageStats",
            "false",
        ]
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\n\n🛑 Streamlit server stopped by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


def launch_paper_test(paper_name: str, fast_mode: bool = False):
    """Launch paper testing mode"""
    try:
        print("\n🧪 Launching Paper Test Mode")
        print(f"📄 Paper: {paper_name}")
        print(f"⚡ Fast mode: {'enabled' if fast_mode else 'disabled'}")
        print("=" * 60)

        # Run the test setup
        setup_cmd = [sys.executable, "test_paper.py", paper_name]
        if fast_mode:
            setup_cmd.append("--fast")

        result = subprocess.run(setup_cmd, check=True)

        if result.returncode == 0:
            print("\n✅ Paper test setup completed successfully!")
            print("📁 Files are ready in deepcode_lab/papers/")
            print("\n💡 Next steps:")
            print("   1. Install MCP dependencies: pip install -r requirements.txt")
            print(
                f"   2. Run full pipeline: python -m workflows.paper_test_engine --paper {paper_name}"
                + (" --fast" if fast_mode else "")
            )

    except subprocess.CalledProcessError as e:
        print(f"\n❌ Paper test setup failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)


def main():
    """Main function"""
    # Parse command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "test" and len(sys.argv) >= 3:
            # Paper testing mode: python deepcode.py test rice [--fast]
            paper_name = sys.argv[2]
            fast_mode = "--fast" in sys.argv or "-f" in sys.argv

            print_banner()
            launch_paper_test(paper_name, fast_mode)
            return
        elif sys.argv[1] == "--classic":
            # Launch classic Streamlit UI
            print_banner()
            launch_classic_ui()
            return
        elif sys.argv[1] in ["--help", "-h", "help"]:
            print_banner()
            print("""
🔧 Usage:
   deepcode                              - Launch new React-based web UI
   deepcode test <paper>                 - Test paper reproduction
   deepcode test <paper> --fast          - Test paper (fast mode)
   deepcode --classic                    - Launch classic Streamlit UI

📄 Examples:
   deepcode                              - Start the new UI (recommended)
   deepcode test rice                    - Test RICE paper reproduction
   deepcode test rice --fast             - Test RICE paper (fast mode)

🌐 New UI Features:
   • User-in-Loop interaction
   • Real-time progress tracking
   • Inline chat interaction
   • Modern React-based interface

📁 Available papers:""")

            # List available papers
            papers_dir = "papers"
            if os.path.exists(papers_dir):
                for item in os.listdir(papers_dir):
                    item_path = os.path.join(papers_dir, item)
                    if os.path.isdir(item_path):
                        paper_md = os.path.join(item_path, "paper.md")
                        addendum_md = os.path.join(item_path, "addendum.md")
                        status = "✅" if os.path.exists(paper_md) else "❌"
                        addendum_status = "📄" if os.path.exists(addendum_md) else "➖"
                        print(f"   {status} {item} {addendum_status}")
            print(
                "\n   Legend: ✅ = paper.md exists, 📄 = addendum.md exists, ➖ = no addendum"
            )
            return

    print_banner()

    # Show platform info
    current_platform = get_platform()
    print(f"🖥️  Platform: {current_platform.capitalize()}")

    # Check dependencies
    if not check_dependencies():
        print("\n🚨 Please install missing dependencies and try again.")
        sys.exit(1)

    # Get paths
    current_dir = Path(__file__).parent
    new_ui_dir = current_dir / "new_ui"
    backend_dir = new_ui_dir / "backend"
    frontend_dir = new_ui_dir / "frontend"

    # Check if new_ui directory exists
    if not new_ui_dir.exists():
        print(f"❌ New UI directory not found: {new_ui_dir}")
        sys.exit(1)

    print("\n🚀 Starting DeepCode New UI...")
    print("=" * 70)
    print("🎨 Frontend:  http://localhost:5173")
    print("🔧 Backend:   http://localhost:8000")
    print("📚 API Docs:  http://localhost:8000/docs")
    print("=" * 70)
    print("💡 Tip: Keep this terminal open while using the application")
    print("🛑 Press Ctrl+C to stop all services")
    print("=" * 70)

    try:
        # Clean up ports if in use
        cleanup_ports()

        # Install dependencies if needed
        install_backend_deps()
        install_frontend_deps(frontend_dir)

        # Start services
        if not start_backend(backend_dir):
            print("❌ Failed to start backend")
            sys.exit(1)

        if not start_frontend(frontend_dir):
            print("❌ Failed to start frontend")
            cleanup_processes()
            sys.exit(1)

        print("\n" + "=" * 70)
        print("╔════════════════════════════════════════╗")
        print("║  🎉 DeepCode New UI is running!        ║")
        print("╠════════════════════════════════════════╣")
        print("║                                        ║")
        print("║  🌐 Frontend: http://localhost:5173    ║")
        print("║  🔧 Backend:  http://localhost:8000    ║")
        print("║  📚 API Docs: http://localhost:8000/docs║")
        print("║                                        ║")
        print("║  Press Ctrl+C to stop all services     ║")
        print("╚════════════════════════════════════════╝")
        print("=" * 70 + "\n")

        # Wait for processes
        while True:
            # Check if processes are still running
            if _backend_process and _backend_process.poll() is not None:
                print("⚠️ Backend process exited unexpectedly")
                break
            if _frontend_process and _frontend_process.poll() is not None:
                print("⚠️ Frontend process exited unexpectedly")
                break
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
    finally:
        cleanup_processes()
        cleanup_cache()
        print("Thank you for using DeepCode! 🧬")


if __name__ == "__main__":
    main()
