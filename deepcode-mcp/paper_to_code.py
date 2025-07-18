#!/usr/bin/env python3
"""
DeepCode - AI Research Engine Launcher
论文到代码 - AI研究引擎启动器

🧬 Next-Generation AI Research Automation Platform
⚡ Transform research papers into working code automatically
"""

import os
import sys
import subprocess
from pathlib import Path

def check_dependencies():
    """检查必要的依赖是否已安装 / Check if necessary dependencies are installed"""
    print("🔍 Checking dependencies...")
    
    missing_deps = []
    missing_system_deps = []
    
    try:
        import streamlit
        print("✅ Streamlit is installed")
    except ImportError:
        missing_deps.append("streamlit>=1.28.0")
    
    try:
        import yaml
        print("✅ PyYAML is installed")
    except ImportError:
        missing_deps.append("pyyaml")
    
    try:
        import asyncio
        print("✅ Asyncio is available")
    except ImportError:
        missing_deps.append("asyncio")
    
    # Check PDF conversion dependencies
    try:
        import reportlab
        print("✅ ReportLab is installed (for text-to-PDF conversion)")
    except ImportError:
        missing_deps.append("reportlab")
        print("⚠️  ReportLab not found (text files won't convert to PDF)")
    
    # Check LibreOffice for Office document conversion
    try:
        import subprocess
        import platform
        
        subprocess_kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": 5,
        }
        
        if platform.system() == "Windows":
            subprocess_kwargs["creationflags"] = 0x08000000  # Hide console window
        
        # Try different LibreOffice commands
        libreoffice_found = False
        for cmd in ["libreoffice", "soffice"]:
            try:
                result = subprocess.run([cmd, "--version"], **subprocess_kwargs)
                if result.returncode == 0:
                    print(f"✅ LibreOffice is installed (for Office document conversion)")
                    libreoffice_found = True
                    break
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                continue
        
        if not libreoffice_found:
            missing_system_deps.append("LibreOffice")
            print("⚠️  LibreOffice not found (Office documents won't convert to PDF)")
    
    except Exception:
        missing_system_deps.append("LibreOffice")
        print("⚠️  Could not check LibreOffice installation")
    
    # Display missing dependencies
    if missing_deps or missing_system_deps:
        print("\n📋 Dependency Status:")
        
        if missing_deps:
            print("❌ Missing Python dependencies:")
            for dep in missing_deps:
                print(f"   - {dep}")
            print(f"\nInstall with: pip install {' '.join(missing_deps)}")
        
        if missing_system_deps:
            print("\n⚠️  Missing system dependencies (optional for full functionality):")
            for dep in missing_system_deps:
                print(f"   - {dep}")
            print("\nInstall LibreOffice:")
            print("   - Windows: Download from https://www.libreoffice.org/")
            print("   - macOS: brew install --cask libreoffice")
            print("   - Ubuntu/Debian: sudo apt-get install libreoffice")
        
        # Only fail if critical Python dependencies are missing
        if missing_deps:
            return False
        else:
            print("\n✅ Core dependencies satisfied (optional dependencies missing)")
    else:
        print("✅ All dependencies satisfied")
    
    return True

def cleanup_cache():
    """清理Python缓存文件 / Clean up Python cache files"""
    try:
        print("🧹 Cleaning up cache files...")
        # 清理__pycache__目录
        os.system('find . -type d -name "__pycache__" -exec rm -r {} + 2>/dev/null')
        # 清理.pyc文件
        os.system('find . -name "*.pyc" -delete 2>/dev/null')
        print("✅ Cache cleanup completed")
    except Exception as e:
        print(f"⚠️  Cache cleanup failed: {e}")

def print_banner():
    """显示启动横幅 / Display startup banner"""
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

def main():
    """主函数 / Main function"""
    print_banner()
    
    # 检查依赖 / Check dependencies
    if not check_dependencies():
        print("\n🚨 Please install missing dependencies and try again.")
        sys.exit(1)
    
    # 获取当前脚本目录 / Get current script directory
    current_dir = Path(__file__).parent
    streamlit_app_path = current_dir / "ui" / "streamlit_app.py"
    
    # 检查streamlit_app.py是否存在 / Check if streamlit_app.py exists
    if not streamlit_app_path.exists():
        print(f"❌ UI application file not found: {streamlit_app_path}")
        print("Please ensure the ui/streamlit_app.py file exists.")
        sys.exit(1)
    
    print(f"\n📁 UI App location: {streamlit_app_path}")
    print("🌐 Starting DeepCode web interface...")
    print("🚀 Launching on http://localhost:8501")
    print("=" * 70)
    print("💡 Tip: Keep this terminal open while using the application")
    print("🛑 Press Ctrl+C to stop the server")
    print("=" * 70)
    
    # 启动Streamlit应用 / Launch Streamlit application
    try:
        cmd = [
            sys.executable, "-m", "streamlit", "run", 
            str(streamlit_app_path),
            "--server.port", "8501",
            "--server.address", "localhost",
            "--browser.gatherUsageStats", "false",
            "--theme.base", "dark",
            "--theme.primaryColor", "#4dd0e1",
            "--theme.backgroundColor", "#0a0e27",
            "--theme.secondaryBackgroundColor", "#1a1f3a"
        ]
        
        subprocess.run(cmd, check=True)
        
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Failed to start DeepCode: {e}")
        print("Please check if Streamlit is properly installed.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n🛑 DeepCode server stopped by user")
        print("Thank you for using DeepCode! 🧬")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        print("Please check your Python environment and try again.")
        sys.exit(1)
    finally:
        # 清理缓存文件 / Clean up cache files
        cleanup_cache()

if __name__ == "__main__":
    main() 