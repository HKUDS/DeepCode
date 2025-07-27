#!/usr/bin/env python3
"""
Professional CLI Interface Module
专业CLI界面模块 - 包含logo、颜色定义和界面组件
"""

import os
import time
import platform
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import filedialog


class Colors:
    """ANSI color codes for terminal styling"""

    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"

    # Gradient colors
    PURPLE = "\033[35m"
    MAGENTA = "\033[95m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"


class CLIInterface:
    """Professional CLI interface with modern styling"""

    def __init__(self):
        self.uploaded_file = None
        self.is_running = True

        # Check tkinter availability
        self.tkinter_available = True
        try:
            import tkinter as tk

            # Test if tkinter can create a window (some systems have tkinter but no display)
            test_root = tk.Tk()
            test_root.withdraw()
            test_root.destroy()
        except Exception:
            self.tkinter_available = False

    def clear_screen(self):
        """Clear terminal screen"""
        os.system("cls" if os.name == "nt" else "clear")

    def print_logo(self):
        """Print a beautiful ASCII logo with gradient colors and tech elements"""
        # 确保每行总共79个字符（不包括颜色代码），边框完美对齐
        logo = f"""
{Colors.CYAN}╔═══════════════════════════════════════════════════════════════════════════════╗
║                                                                               ║
║  {Colors.BOLD}{Colors.MAGENTA}██████╗  ███████╗██████╗ ██████╗  ██████╗     █████╗ ██╗{Colors.CYAN}                ║
║  {Colors.BOLD}{Colors.PURPLE}██╔══██╗ ██╔════╝██╔══██╗██╔══██╗██╔═══██╗   ██╔══██╗██║{Colors.CYAN}                ║
║  {Colors.BOLD}{Colors.BLUE}██████╔╝ █████╗  ██████╔╝██████╔╝██║   ██║   ███████║██║{Colors.CYAN}                ║
║  {Colors.BOLD}{Colors.OKBLUE}██╔══██╗ ██╔══╝  ██╔═══╝ ██╔══██╗██║   ██║   ██╔══██║██║{Colors.CYAN}                ║
║  {Colors.BOLD}{Colors.OKCYAN}██║  ██║ ███████╗██║     ██║  ██║╚██████╔╝   ██║  ██║██║{Colors.CYAN}                ║
║  {Colors.BOLD}{Colors.GREEN}╚═╝  ╚═╝ ╚══════╝╚═╝     ╚═╝  ╚═╝ ╚═════╝    ╚═╝  ╚═╝╚═╝{Colors.CYAN}                ║
║                                                                               ║
║  {Colors.BOLD}{Colors.YELLOW}┌─────────────────────────────────────────────────────────────────────────┐{Colors.CYAN}   ║
║  {Colors.BOLD}{Colors.YELLOW}│  🤖 AI-POWERED RESEARCH PAPER REPRODUCTION ENGINE 🚀                  │{Colors.CYAN}   ║
║  {Colors.BOLD}{Colors.YELLOW}│  ⚡ INTELLIGENT • AUTOMATED • CUTTING-EDGE ⚡                        │{Colors.CYAN}   ║
║  {Colors.BOLD}{Colors.YELLOW}└─────────────────────────────────────────────────────────────────────────┘{Colors.CYAN}   ║
║                                                                               ║
║  {Colors.BOLD}{Colors.GREEN}💎 CORE CAPABILITIES:{Colors.ENDC}                                                        {Colors.CYAN}║
║    {Colors.BOLD}{Colors.OKCYAN}▶ Neural PDF Analysis & Code Extraction                                 {Colors.CYAN}║
║    {Colors.BOLD}{Colors.OKCYAN}▶ Advanced Document Processing Engine                                   {Colors.CYAN}║
║    {Colors.BOLD}{Colors.OKCYAN}▶ Multi-Format Support (PDF•DOCX•PPTX•HTML)                           {Colors.CYAN}║
║    {Colors.BOLD}{Colors.OKCYAN}▶ Smart File Upload Interface                                          {Colors.CYAN}║
║    {Colors.BOLD}{Colors.OKCYAN}▶ Automated Repository Management                                      {Colors.CYAN}║
║                                                                               ║
║  {Colors.BOLD}{Colors.PURPLE}🔬 TECH STACK: Python•AI•MCP•Docling•LLM                                   {Colors.CYAN}║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
"""
        print(logo)

    def print_welcome_banner(self):
        """Print welcome banner with version info"""
        banner = f"""
{Colors.BOLD}{Colors.CYAN}╔═══════════════════════════════════════════════════════════════════════════════╗
║                              WELCOME TO ReproAI                              ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  {Colors.YELLOW}Version: 2.0.0 | Build: Professional Edition                                 {Colors.CYAN}║
║  {Colors.GREEN}Status: Ready | Engine: Initialized                                          {Colors.CYAN}║
║  {Colors.PURPLE}Author: AI Research Team | License: MIT                                      {Colors.CYAN}║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
"""
        print(banner)

    def print_separator(self, char="═", length=79, color=Colors.CYAN):
        """Print a styled separator line"""
        print(f"{color}{char * length}{Colors.ENDC}")

    def print_status(self, message: str, status_type: str = "info"):
        """Print status message with appropriate styling"""
        status_styles = {
            "success": f"{Colors.OKGREEN}✅",
            "error": f"{Colors.FAIL}❌",
            "warning": f"{Colors.WARNING}⚠️ ",
            "info": f"{Colors.OKBLUE}ℹ️ ",
            "processing": f"{Colors.YELLOW}⏳",
            "upload": f"{Colors.PURPLE}📁",
            "download": f"{Colors.CYAN}📥",
            "analysis": f"{Colors.MAGENTA}🔍",
        }

        icon = status_styles.get(status_type, status_styles["info"])
        print(f"{icon} {Colors.BOLD}{message}{Colors.ENDC}")

    def create_menu(self):
        """Create an interactive menu"""
        menu = f"""
{Colors.BOLD}{Colors.CYAN}╔═══════════════════════════════════════════════════════════════════════════════╗
║                                MAIN MENU                                      ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  {Colors.OKGREEN}🌐 [U] Process URL       {Colors.CYAN}│  {Colors.PURPLE}📁 [F] Upload File    {Colors.CYAN}│  {Colors.FAIL}❌ [Q] Quit{Colors.CYAN}         ║
║                                                                               ║
║  {Colors.YELLOW}📝 Enter a research paper URL (arXiv, IEEE, ACM, etc.)                      {Colors.CYAN}║
║  {Colors.YELLOW}   or upload a PDF/DOC file for intelligent analysis                        {Colors.CYAN}║
║                                                                               ║
║  {Colors.OKCYAN}💡 Tip: Press 'F' to open file browser or 'U' to enter URL manually        {Colors.CYAN}║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
"""
        print(menu)

    def get_user_input(self):
        """Get user input with styled prompt"""
        print(f"\n{Colors.BOLD}{Colors.OKCYAN}➤ Your choice: {Colors.ENDC}", end="")
        return input().strip().lower()

    def upload_file_gui(self) -> Optional[str]:
        """Modern file upload interface using tkinter with cross-platform compatibility"""
        # Check if tkinter is available
        if not self.tkinter_available:
            self.print_status("GUI file dialog not available on this system", "warning")
            self.print_status("Using manual file path input instead", "info")
            return self._get_manual_file_path()

        def select_file():
            try:
                # Create a hidden root window
                root = tk.Tk()
                root.withdraw()  # Hide the main window

                # Platform-specific configurations
                system = platform.system()

                if system == "Darwin":  # macOS
                    # macOS specific settings
                    try:
                        root.call("wm", "attributes", ".", "-topmost", True)
                    except Exception:
                        pass

                    # macOS compatible file types
                    file_types = [
                        ("PDF Files", ".pdf"),
                        ("Word Documents", ".docx .doc"),
                        ("PowerPoint Files", ".pptx .ppt"),
                        ("HTML Files", ".html .htm"),
                        ("Text Files", ".txt .md"),
                        ("All Files", ".*"),
                    ]
                else:
                    # Windows and Linux
                    root.attributes("-topmost", True)

                    # Windows/Linux compatible file types
                    file_types = [
                        ("PDF Files", "*.pdf"),
                        ("Word Documents", "*.docx;*.doc"),
                        ("PowerPoint Files", "*.pptx;*.ppt"),
                        ("HTML Files", "*.html;*.htm"),
                        ("Text Files", "*.txt;*.md"),
                        ("All Files", "*.*"),
                    ]

                # Set window title
                root.title("Repro-AI - File Selector")

                try:
                    # Open file dialog with platform-appropriate settings
                    file_path = filedialog.askopenfilename(
                        title="Select Research Paper File",
                        filetypes=file_types,
                        initialdir=os.getcwd(),
                    )
                except Exception as e:
                    self.print_status(f"File dialog error: {str(e)}", "error")
                    return None
                finally:
                    # Clean up
                    try:
                        root.destroy()
                    except Exception:
                        pass

                return file_path

            except Exception as e:
                # Fallback: destroy root if it exists
                try:
                    if "root" in locals():
                        root.destroy()
                except Exception:
                    pass

                # Print error and suggest alternative
                self.print_status(f"GUI file dialog failed: {str(e)}", "error")
                self.print_status(
                    "Please use manual file path input instead", "warning"
                )
                return self._get_manual_file_path()

        self.print_status("Opening file browser dialog...", "upload")
        file_path = select_file()

        if file_path:
            # Validate file
            if not os.path.exists(file_path):
                self.print_status("File not found!", "error")
                return None

            file_size = os.path.getsize(file_path) / (1024 * 1024)  # Size in MB
            file_ext = Path(file_path).suffix.lower()

            # Display file info with beautiful formatting
            file_name = Path(file_path).name
            directory = str(Path(file_path).parent)

            # Truncate long paths for display
            if len(file_name) > 50:
                display_name = file_name[:47] + "..."
            else:
                display_name = file_name

            if len(directory) > 49:
                display_dir = "..." + directory[-46:]
            else:
                display_dir = directory

            print(f"""
{Colors.OKGREEN}╔═══════════════════════════════════════════════════════════════════════════════╗
║                               FILE SELECTED                                   ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  {Colors.BOLD}📄 File Name:{Colors.ENDC} {Colors.CYAN}{display_name:<50}{Colors.OKGREEN}║
║  {Colors.BOLD}📁 Directory:{Colors.ENDC} {Colors.YELLOW}{display_dir:<49}{Colors.OKGREEN}║
║  {Colors.BOLD}📊 File Size:{Colors.ENDC} {Colors.PURPLE}{file_size:.2f} MB{Colors.OKGREEN}                                      ║
║  {Colors.BOLD}🔖 File Type:{Colors.ENDC} {Colors.MAGENTA}{file_ext.upper():<50}{Colors.OKGREEN}║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
""")

            self.print_status(f"File successfully selected: {file_name}", "success")
            return file_path
        else:
            self.print_status("No file selected", "warning")
            return None

    def _get_manual_file_path(self) -> Optional[str]:
        """Fallback method for manual file path input when GUI fails"""
        print(
            f"\n{Colors.BOLD}{Colors.CYAN}╔═══════════════════════════════════════════════════════════════════════════════╗"
        )
        print(
            "║                           MANUAL FILE INPUT                                   ║"
        )
        print(
            f"╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}"
        )

        print(f"\n{Colors.YELLOW}📝 Supported file types:{Colors.ENDC}")
        print(f"   {Colors.CYAN}• PDF files (.pdf)")
        print(f"   {Colors.CYAN}• Word documents (.docx, .doc)")
        print(f"   {Colors.CYAN}• PowerPoint files (.pptx, .ppt)")
        print(f"   {Colors.CYAN}• HTML files (.html, .htm)")
        print(f"   {Colors.CYAN}• Text files (.txt, .md){Colors.ENDC}")

        print(
            f"\n{Colors.BOLD}{Colors.OKCYAN}📁 Enter file path (or drag & drop): {Colors.ENDC}",
            end="",
        )
        file_path = input().strip()

        # Clean up the path (remove quotes if present)
        file_path = file_path.strip("\"'")

        if file_path:
            # Expand user directory if needed
            file_path = os.path.expanduser(file_path)

            # Check if file exists
            if os.path.exists(file_path):
                self.print_status(
                    f"File found: {os.path.basename(file_path)}", "success"
                )
                return file_path
            else:
                self.print_status("File not found at the specified path", "error")
                return None
        else:
            self.print_status("No file path provided", "warning")
            return None

    def get_url_input(self) -> str:
        """Get URL input with validation and examples"""
        print(
            f"\n{Colors.BOLD}{Colors.CYAN}╔═══════════════════════════════════════════════════════════════════════════════╗"
        )
        print(
            "║                              URL INPUT                                        ║"
        )
        print(
            f"╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}"
        )

        print(f"\n{Colors.YELLOW}📝 Supported URL Examples:{Colors.ENDC}")
        print(f"   {Colors.CYAN}• arXiv: https://arxiv.org/pdf/2403.00813")
        print(f"   {Colors.CYAN}• arXiv: @https://arxiv.org/pdf/2403.00813")
        print(f"   {Colors.CYAN}• IEEE:  https://ieeexplore.ieee.org/document/...")
        print(f"   {Colors.CYAN}• ACM:   https://dl.acm.org/doi/...")
        print(
            f"   {Colors.CYAN}• Direct PDF: https://example.com/paper.pdf{Colors.ENDC}"
        )

        print(
            f"\n{Colors.BOLD}{Colors.OKCYAN}🌐 Enter paper URL: {Colors.ENDC}", end=""
        )
        url = input().strip()

        if url:
            # Basic URL validation
            if any(
                domain in url.lower()
                for domain in ["arxiv.org", "ieee", "acm.org", ".pdf", "researchgate"]
            ):
                self.print_status(f"URL received: {url}", "success")
                return url
            else:
                self.print_status("URL appears valid, proceeding...", "info")
                return url
        else:
            self.print_status("No URL provided", "warning")
            return ""

    def show_progress_bar(self, message: str, duration: float = 2.0):
        """Show a progress animation with enhanced styling"""
        print(f"\n{Colors.YELLOW}{message}{Colors.ENDC}")

        # Progress bar animation with different styles
        bar_length = 50
        for i in range(bar_length + 1):
            percent = (i / bar_length) * 100
            filled = "█" * i
            empty = "░" * (bar_length - i)

            # Color gradient effect
            if percent < 33:
                color = Colors.FAIL
            elif percent < 66:
                color = Colors.WARNING
            else:
                color = Colors.OKGREEN

            print(
                f"\r{color}[{filled}{empty}] {percent:6.1f}%{Colors.ENDC}",
                end="",
                flush=True,
            )
            time.sleep(duration / bar_length)

        print(f"\n{Colors.OKGREEN}✅ {message} completed!{Colors.ENDC}\n")

    def show_spinner(self, message: str, duration: float = 1.0):
        """Show a spinner animation"""
        spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        end_time = time.time() + duration

        while time.time() < end_time:
            for char in spinner_chars:
                print(
                    f"\r{Colors.CYAN}{char} {Colors.BOLD}{message}{Colors.ENDC}",
                    end="",
                    flush=True,
                )
                time.sleep(0.1)
                if time.time() >= end_time:
                    break

        print(f"\r{Colors.OKGREEN}✅ {Colors.BOLD}{message} - Done!{Colors.ENDC}")

    def print_results_header(self):
        """Print results section header"""
        header = f"""
{Colors.OKGREEN}╔═══════════════════════════════════════════════════════════════════════════════╗
║                             PROCESSING RESULTS                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
"""
        print(header)

    def print_error_box(self, title: str, error_msg: str):
        """Print error message in a styled box"""
        print(f"""
{Colors.FAIL}╔═══════════════════════════════════════════════════════════════════════════════╗
║                                  ERROR                                        ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  {Colors.BOLD}Title: {title:<66}{Colors.FAIL}║
║  {Colors.BOLD}Error: {error_msg:<66}{Colors.FAIL}║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
""")

    def print_goodbye(self):
        """Print goodbye message"""
        goodbye = f"""
{Colors.BOLD}{Colors.YELLOW}╔═══════════════════════════════════════════════════════════════════════════════╗
║                                GOODBYE!                                       ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  {Colors.CYAN}Thank you for using ReproAI!                                               {Colors.YELLOW}║
║  {Colors.GREEN}🌟 Star us on GitHub: https://github.com/your-repo                        {Colors.YELLOW}║
║  {Colors.PURPLE}📧 Contact: support@reproai.com                                          {Colors.YELLOW}║
║  {Colors.MAGENTA}🐛 Report issues: https://github.com/your-repo/issues                    {Colors.YELLOW}║
║                                                                               ║
║  {Colors.OKGREEN}✨ Happy coding! See you next time! ✨                                   {Colors.YELLOW}║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
"""
        print(goodbye)

    def ask_continue(self) -> bool:
        """Ask user if they want to continue"""
        print(
            f"\n{Colors.BOLD}{Colors.CYAN}Press Enter to continue or 'q' to quit: {Colors.ENDC}",
            end="",
        )
        choice = input().strip().lower()
        return choice not in ["q", "quit", "exit"]
