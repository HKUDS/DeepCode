#!/usr/bin/env python3
"""
Enhanced CLI Interface Module for DeepCode
增强版CLI界面模块 - 专为DeepCode设计
"""

import os
import time
import platform
from typing import Optional


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
    """Enhanced CLI interface with modern styling for DeepCode"""

    def __init__(self):
        self.uploaded_file = None
        self.is_running = True
        self.processing_history = []
        self.enable_indexing = (
            False  # Default configuration (matching UI: fast mode by default)
        )

        # Load segmentation config from the same source as UI
        self._load_segmentation_config()

        # Initialize tkinter availability
        self._init_tkinter()

    def _load_segmentation_config(self):
        """Load segmentation configuration from mcp_agent.config.yaml"""
        try:
            from utils.llm_utils import get_document_segmentation_config

            seg_config = get_document_segmentation_config()
            self.segmentation_enabled = seg_config.get("enabled", True)
            self.segmentation_threshold = seg_config.get("size_threshold_chars", 50000)
        except Exception as e:
            print(f"⚠️ Warning: Failed to load segmentation config: {e}")
            # Fall back to defaults
            self.segmentation_enabled = True
            self.segmentation_threshold = 50000

    def _save_segmentation_config(self):
        """Save segmentation configuration to mcp_agent.config.yaml"""
        import yaml
        import os

        # Get the project root directory (where mcp_agent.config.yaml is located)
        current_file = os.path.abspath(__file__)
        cli_dir = os.path.dirname(current_file)  # cli directory
        project_root = os.path.dirname(cli_dir)  # project root
        config_path = os.path.join(project_root, "mcp_agent.config.yaml")

        try:
            # Read current config
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            # Update document segmentation settings
            if "document_segmentation" not in config:
                config["document_segmentation"] = {}

            config["document_segmentation"]["enabled"] = self.segmentation_enabled
            config["document_segmentation"]["size_threshold_chars"] = (
                self.segmentation_threshold
            )

            # Write updated config
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

            print(
                f"{Colors.OKGREEN}✅ Document segmentation configuration updated{Colors.ENDC}"
            )

        except Exception as e:
            print(
                f"{Colors.WARNING}⚠️ Failed to update segmentation config: {str(e)}{Colors.ENDC}"
            )

    def _init_tkinter(self):
        """Initialize tkinter availability check"""
        # Check tkinter availability for file dialogs
        self.tkinter_available = True
        try:
            import tkinter as tk

            # Test if tkinter can create a window
            test_root = tk.Tk()
            test_root.withdraw()
            test_root.destroy()
        except Exception:
            self.tkinter_available = False

    def clear_screen(self):
        """Clear terminal screen"""
        os.system("cls" if os.name == "nt" else "clear")

    def print_logo(self):
        """Print enhanced ASCII logo for DeepCode CLI"""
        logo = f"""
{Colors.CYAN}╔═══════════════════════════════════════════════════════════════════════════════╗
║                                                                               ║
║  {Colors.BOLD}{Colors.MAGENTA}██████╗ ███████╗███████╗██████╗  ██████╗ ██████╗ ██████╗ ███████╗{Colors.CYAN}               ║
║  {Colors.BOLD}{Colors.PURPLE}██╔══██╗██╔════╝██╔════╝██╔══██╗██╔════╝██╔═══██╗██╔══██╗██╔════╝{Colors.CYAN}               ║
║  {Colors.BOLD}{Colors.BLUE}██║  ██║█████╗  █████╗  ██████╔╝██║     ██║   ██║██║  ██║█████╗  {Colors.CYAN}               ║
║  {Colors.BOLD}{Colors.OKBLUE}██║  ██║██╔══╝  ██╔══╝  ██╔═══╝ ██║     ██║   ██║██║  ██║██╔══╝  {Colors.CYAN}               ║
║  {Colors.BOLD}{Colors.OKCYAN}██████╔╝███████╗███████╗██║     ╚██████╗╚██████╔╝██████╔╝███████╗{Colors.CYAN}               ║
║  {Colors.BOLD}{Colors.GREEN}╚═════╝ ╚══════╝╚══════╝╚═╝      ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝{Colors.CYAN}               ║
║                                                                               ║
║  {Colors.BOLD}{Colors.GREEN}🧬 OPEN-SOURCE CODE AGENT • DATA INTELLIGENCE LAB @ HKU 🚀           {Colors.CYAN}║
║  {Colors.BOLD}{Colors.GREEN}⚡ REVOLUTIONIZING RESEARCH REPRODUCIBILITY ⚡                      {Colors.CYAN}║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
"""
        print(logo)

    def print_welcome_banner(self):
        """Print enhanced welcome banner"""
        banner = f"""
{Colors.BOLD}{Colors.CYAN}╔═══════════════════════════════════════════════════════════════════════════════╗
║                             WELCOME TO DEEPCODE CLI                          ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║  {Colors.YELLOW}Open-Source Code Agent | Data Intelligence Lab @ HKU | MIT License        {Colors.CYAN}║
║  {Colors.GREEN}Status: Ready | Engine: Multi-Agent Architecture Initialized               {Colors.CYAN}║
║  {Colors.PURPLE}Mission: Revolutionizing Research Reproducibility                         {Colors.CYAN}║
║                                                                               ║
║  {Colors.BOLD}{Colors.OKCYAN}💎 CORE CAPABILITIES:{Colors.ENDC}                                                      {Colors.CYAN}║
║    {Colors.BOLD}{Colors.OKCYAN}▶ Automated Paper-to-Code Reproduction                                {Colors.CYAN}║
║    {Colors.BOLD}{Colors.OKCYAN}▶ Collaborative Multi-Agent Architecture                             {Colors.CYAN}║
║    {Colors.BOLD}{Colors.OKCYAN}▶ Intelligent Code Implementation & Validation                       {Colors.CYAN}║
║    {Colors.BOLD}{Colors.OKCYAN}▶ Future Vision: One Sentence → Complete Codebase                   {Colors.CYAN}║
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
            "implementation": f"{Colors.GREEN}⚙️ ",
            "complete": f"{Colors.OKGREEN}🎉",
        }

        icon = status_styles.get(status_type, status_styles["info"])
        timestamp = time.strftime("%H:%M:%S")
        print(
            f"[{Colors.BOLD}{timestamp}{Colors.ENDC}] {icon} {Colors.BOLD}{message}{Colors.ENDC}"
        )

    def create_menu(self):
        """Create enhanced interactive menu"""
        # Display current configuration
        pipeline_mode = "🧠 COMPREHENSIVE" if self.enable_indexing else "⚡ OPTIMIZED"
        index_status = "✅ Enabled" if self.enable_indexing else "🔶 Disabled"
        segmentation_mode = (
            "📄 SMART" if self.segmentation_enabled else "📋 TRADITIONAL"
        )

        menu = f"""
{Colors.BOLD}{Colors.CYAN}╔═══════════════════════════════════════════════════════════════════════════════╗
║                                MAIN MENU                                      ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║  {Colors.OKGREEN}🌐 [U] Process URL       {Colors.CYAN}│  {Colors.PURPLE}📁 [F] Upload File    {Colors.CYAN}│  {Colors.MAGENTA}💬 [T] Chat Input{Colors.CYAN}    ║
║  {Colors.BLUE}🧠 [R] Req. Analysis    {Colors.CYAN}│  {Colors.OKCYAN}⚙️  [C] Configure        {Colors.CYAN}│  {Colors.YELLOW}📊 [H] History{Colors.CYAN}    ║
║  {Colors.FAIL}❌ [Q] Quit{Colors.CYAN}                                                                 ║
║                                                                               ║
║  {Colors.BOLD}🤖 Current Pipeline Mode: {pipeline_mode}{Colors.CYAN}                          ║
║  {Colors.BOLD}🗂️  Codebase Indexing: {index_status}{Colors.CYAN}                                    ║
║  {Colors.BOLD}📄 Document Processing: {segmentation_mode}{Colors.CYAN}                               ║
║                                                                               ║
║  {Colors.YELLOW}📝 URL Processing:{Colors.CYAN}                                                         ║
║  {Colors.YELLOW}   ▶ Enter research paper URL (arXiv, IEEE, ACM, etc.)                    {Colors.CYAN}║
║  {Colors.YELLOW}   ▶ Supports direct PDF links and academic paper pages                   {Colors.CYAN}║
║                                                                               ║
║  {Colors.PURPLE}📁 File Processing:{Colors.CYAN}                                                        ║
║  {Colors.PURPLE}   ▶ Upload PDF, DOCX, PPTX, HTML, or TXT files                          {Colors.CYAN}║
║  {Colors.PURPLE}   ▶ Intelligent file format detection and processing                     {Colors.CYAN}║
║                                                                               ║
║  {Colors.MAGENTA}💬 Chat Input:{Colors.CYAN}                                                           ║
║  {Colors.MAGENTA}   ▶ Describe your coding requirements in natural language                {Colors.CYAN}║
║  {Colors.MAGENTA}   ▶ AI generates implementation plan and code automatically             {Colors.CYAN}║
║                                                                               ║
║  {Colors.BLUE}🧠 Requirement Analysis (NEW):{Colors.CYAN}                                             ║
║  {Colors.BLUE}   ▶ Get AI-guided questions to refine your requirements                   {Colors.CYAN}║
║  {Colors.BLUE}   ▶ Generate detailed requirement documents from your answers             {Colors.CYAN}║
║                                                                               ║
║  {Colors.OKCYAN}🔄 Processing Pipeline:{Colors.CYAN}                                                    ║
║  {Colors.OKCYAN}   ▶ Intelligent agent orchestration → Code synthesis                     {Colors.CYAN}║
║  {Colors.OKCYAN}   ▶ Multi-agent coordination with progress tracking                     {Colors.CYAN}║
╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
"""
        print(menu)

    def get_user_input(self):
        """Get user input with styled prompt"""
        print(f"\n{Colors.BOLD}{Colors.OKCYAN}➤ Your choice: {Colors.ENDC}", end="")
        return input().strip().lower()

    def upload_file_gui(self) -> Optional[str]:
        """Enhanced file upload interface with better error handling"""
        if not self.tkinter_available:
            self.print_status(
                "GUI file dialog not available - using manual input", "warning"
            )
            return self._get_manual_file_path()

        def select_file():
            try:
                import tkinter as tk
                from tkinter import filedialog

                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)

                file_types = [
                    ("Research Papers", "*.pdf;*.docx;*.doc"),
                    ("PDF Files", "*.pdf"),
                    ("Word Documents", "*.docx;*.doc"),
                    ("PowerPoint Files", "*.pptx;*.ppt"),
                    ("HTML Files", "*.html;*.htm"),
                    ("Text Files", "*.txt;*.md"),
                    ("All Files", "*.*"),
                ]

                if platform.system() == "Darwin":
                    file_types = [
                        ("Research Papers", ".pdf .docx .doc"),
                        ("PDF Files", ".pdf"),
                        ("Word Documents", ".docx .doc"),
                        ("PowerPoint Files", ".pptx .ppt"),
                        ("HTML Files", ".html .htm"),
                        ("Text Files", ".txt .md"),
                        ("All Files", ".*"),
                    ]

                file_path = filedialog.askopenfilename(
                    title="Select Research File - DeepCode CLI",
                    filetypes=file_types,
                    initialdir=os.getcwd(),
                )

                root.destroy()
                return file_path

            except Exception as e:
                self.print_status(f"File dialog error: {str(e)}", "error")
                return self._get_manual_file_path()

        self.print_status("Opening file browser dialog...", "upload")
        file_path = select_file()

        if file_path:
            self.print_status(
                f"File selected: {os.path.basename(file_path)}", "success"
            )
            return file_path
        else:
            self.print_status("No file selected", "warning")
            return None

    def _get_manual_file_path(self) -> Optional[str]:
        """Get file path through manual input with validation"""
        self.print_separator("─", 79, Colors.YELLOW)
        print(f"{Colors.BOLD}{Colors.YELLOW}📁 Manual File Path Input{Colors.ENDC}")
        print(
            f"{Colors.CYAN}Please enter the full path to your research paper file:{Colors.ENDC}"
        )
        print(
            f"{Colors.CYAN}Supported formats: PDF, DOCX, PPTX, HTML, TXT, MD{Colors.ENDC}"
        )
        self.print_separator("─", 79, Colors.YELLOW)

        while True:
            print(f"\n{Colors.BOLD}{Colors.OKCYAN}📂 File path: {Colors.ENDC}", end="")
            file_path = input().strip()

            if not file_path:
                self.print_status(
                    "Empty path entered. Please try again or press Ctrl+C to cancel.",
                    "warning",
                )
                continue

            file_path = os.path.expanduser(file_path)
            file_path = os.path.abspath(file_path)

            if not os.path.exists(file_path):
                self.print_status(f"File not found: {file_path}", "error")
                retry = (
                    input(f"{Colors.YELLOW}Try again? (y/n): {Colors.ENDC}")
                    .strip()
                    .lower()
                )
                if retry != "y":
                    return None
                continue

            if not os.path.isfile(file_path):
                self.print_status(f"Path is not a file: {file_path}", "error")
                continue

            supported_extensions = {
                ".pdf",
                ".docx",
                ".doc",
                ".pptx",
                ".ppt",
                ".html",
                ".htm",
                ".txt",
                ".md",
            }
            file_ext = os.path.splitext(file_path)[1].lower()

            if file_ext not in supported_extensions:
                self.print_status(f"Unsupported file format: {file_ext}", "warning")
                proceed = (
                    input(f"{Colors.YELLOW}Process anyway? (y/n): {Colors.ENDC}")
                    .strip()
                    .lower()
                )
                if proceed != "y":
                    continue

            self.print_status(
                f"File validated: {os.path.basename(file_path)}", "success"
            )
            return file_path

    def get_url_input(self) -> str:
        """Enhanced URL input with validation"""
        self.print_separator("─", 79, Colors.GREEN)
        print(f"{Colors.BOLD}{Colors.GREEN}🌐 URL Input Interface{Colors.ENDC}")
        print(
            f"{Colors.CYAN}Enter a research paper URL from supported platforms:{Colors.ENDC}"
        )
        print(
            f"{Colors.CYAN}• arXiv (arxiv.org)        • IEEE Xplore (ieeexplore.ieee.org){Colors.ENDC}"
        )
        print(
            f"{Colors.CYAN}• ACM Digital Library      • SpringerLink • Nature • Science{Colors.ENDC}"
        )
        print(
            f"{Colors.CYAN}• Direct PDF links         • Academic publisher websites{Colors.ENDC}"
        )
        self.print_separator("─", 79, Colors.GREEN)

        while True:
            print(f"\n{Colors.BOLD}{Colors.OKCYAN}🔗 URL: {Colors.ENDC}", end="")
            url = input().strip()

            if not url:
                self.print_status(
                    "Empty URL entered. Please try again or press Ctrl+C to cancel.",
                    "warning",
                )
                continue

            if not url.startswith(("http://", "https://")):
                self.print_status("URL must start with http:// or https://", "error")
                retry = (
                    input(f"{Colors.YELLOW}Try again? (y/n): {Colors.ENDC}")
                    .strip()
                    .lower()
                )
                if retry != "y":
                    return ""
                continue

            academic_domains = [
                "arxiv.org",
                "ieeexplore.ieee.org",
                "dl.acm.org",
                "link.springer.com",
                "nature.com",
                "science.org",
                "scholar.google.com",
                "researchgate.net",
                "semanticscholar.org",
            ]

            is_academic = any(domain in url.lower() for domain in academic_domains)
            if not is_academic and not url.lower().endswith(".pdf"):
                self.print_status(
                    "URL doesn't appear to be from a known academic platform", "warning"
                )
                proceed = (
                    input(f"{Colors.YELLOW}Process anyway? (y/n): {Colors.ENDC}")
                    .strip()
                    .lower()
                )
                if proceed != "y":
                    continue

            self.print_status(f"URL validated: {url}", "success")
            return url

    def get_chat_input(self) -> str:
        """Enhanced chat input interface for coding requirements"""
        self.print_separator("─", 79, Colors.PURPLE)
        print(f"{Colors.BOLD}{Colors.PURPLE}💬 Chat Input Interface{Colors.ENDC}")
        print(
            f"{Colors.CYAN}Describe your coding requirements in natural language.{Colors.ENDC}"
        )
        print(
            f"{Colors.CYAN}Our AI will analyze your needs and generate a comprehensive implementation plan.{Colors.ENDC}"
        )
        self.print_separator("─", 79, Colors.PURPLE)

        # Display examples to help users
        print(f"\n{Colors.BOLD}{Colors.YELLOW}💡 Examples:{Colors.ENDC}")
        print(f"{Colors.CYAN}Academic Research:{Colors.ENDC}")
        print(
            "  • 'I need to implement a reinforcement learning algorithm for robotic control'"
        )
        print(
            "  • 'Create a neural network for image classification with attention mechanisms'"
        )
        print(f"{Colors.CYAN}Engineering Projects:{Colors.ENDC}")
        print(
            "  • 'Develop a web application for project management with user authentication'"
        )
        print("  • 'Create a data visualization dashboard for sales analytics'")
        print(f"{Colors.CYAN}Mixed Projects:{Colors.ENDC}")
        print(
            "  • 'Implement a machine learning model with a web interface for real-time predictions'"
        )

        self.print_separator("─", 79, Colors.PURPLE)

        print(
            f"\n{Colors.BOLD}{Colors.OKCYAN}✏️  Enter your coding requirements below:{Colors.ENDC}"
        )
        print(
            f"{Colors.YELLOW}(Type your description, press Enter twice when finished, or Ctrl+C to cancel){Colors.ENDC}"
        )

        lines = []
        empty_line_count = 0

        while True:
            try:
                if len(lines) == 0:
                    print(f"{Colors.BOLD}> {Colors.ENDC}", end="")
                else:
                    print(f"{Colors.BOLD}  {Colors.ENDC}", end="")

                line = input()

                if line.strip() == "":
                    empty_line_count += 1
                    if empty_line_count >= 2:
                        # Two consecutive empty lines means user finished input
                        break
                    lines.append("")  # Keep empty line for formatting
                else:
                    empty_line_count = 0
                    lines.append(line)

            except KeyboardInterrupt:
                print(f"\n{Colors.WARNING}Input cancelled by user{Colors.ENDC}")
                return ""

        # Join all lines and clean up
        user_input = "\n".join(lines).strip()

        if not user_input:
            self.print_status("No input provided", "warning")
            return ""

        if len(user_input) < 20:
            self.print_status(
                "Input too short. Please provide more detailed requirements (at least 20 characters)",
                "warning",
            )
            retry = (
                input(f"{Colors.YELLOW}Try again? (y/n): {Colors.ENDC}").strip().lower()
            )
            if retry == "y":
                return self.get_chat_input()  # Recursive call for retry
            return ""

        # Display input summary
        word_count = len(user_input.split())
        char_count = len(user_input)

        print(f"\n{Colors.BOLD}{Colors.GREEN}📋 Input Summary:{Colors.ENDC}")
        print(f"  • {Colors.CYAN}Word count: {word_count}{Colors.ENDC}")
        print(f"  • {Colors.CYAN}Character count: {char_count}{Colors.ENDC}")

        # Show preview
        preview = user_input[:200] + "..." if len(user_input) > 200 else user_input
        print(f"\n{Colors.BOLD}{Colors.CYAN}📄 Preview:{Colors.ENDC}")
        print(f"{Colors.YELLOW}{preview}{Colors.ENDC}")

        # Confirm with user
        confirm = (
            input(
                f"\n{Colors.BOLD}{Colors.OKCYAN}Proceed with this input? (y/n): {Colors.ENDC}"
            )
            .strip()
            .lower()
        )
        if confirm != "y":
            retry = (
                input(f"{Colors.YELLOW}Edit input? (y/n): {Colors.ENDC}")
                .strip()
                .lower()
            )
            if retry == "y":
                return self.get_chat_input()  # Recursive call for retry
            return ""

        self.print_status(
            f"Chat input captured: {word_count} words, {char_count} characters",
            "success",
        )
        return user_input

    def show_progress_bar(self, message: str, duration: float = 2.0):
        """Show animated progress bar"""
        print(f"\n{Colors.BOLD}{Colors.CYAN}{message}{Colors.ENDC}")

        bar_length = 50
        for i in range(bar_length + 1):
            percent = (i / bar_length) * 100
            filled = "█" * i
            empty = "░" * (bar_length - i)

            print(
                f"\r{Colors.OKGREEN}[{filled}{empty}] {percent:3.0f}%{Colors.ENDC}",
                end="",
                flush=True,
            )
            time.sleep(duration / bar_length)

        print(f"\n{Colors.OKGREEN}✓ {message} completed{Colors.ENDC}")

    def show_spinner(self, message: str, duration: float = 1.0):
        """Show spinner animation"""
        spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        end_time = time.time() + duration

        print(
            f"{Colors.BOLD}{Colors.CYAN}{message}... {Colors.ENDC}", end="", flush=True
        )

        i = 0
        while time.time() < end_time:
            print(
                f"\r{Colors.BOLD}{Colors.CYAN}{message}... {Colors.YELLOW}{spinner_chars[i % len(spinner_chars)]}{Colors.ENDC}",
                end="",
                flush=True,
            )
            time.sleep(0.1)
            i += 1

        print(
            f"\r{Colors.BOLD}{Colors.CYAN}{message}... {Colors.OKGREEN}✓{Colors.ENDC}"
        )

    def display_processing_stages(
        self,
        current_stage: int = 0,
        enable_indexing: bool = True,
        chat_mode: bool = False,
    ):
        """Display processing pipeline stages with current progress"""
        if chat_mode:
            # Chat mode - simplified workflow for user requirements
            stages = [
                ("🚀", "Initialize", "Setting up chat engine"),
                ("💬", "Planning", "Analyzing requirements"),
                ("🏗️", "Setup", "Creating workspace"),
                ("📝", "Save Plan", "Saving implementation plan"),
                ("⚙️", "Implement", "Generating code"),
            ]
            pipeline_mode = "CHAT PLANNING"
        elif enable_indexing:
            # Full pipeline with all stages
            stages = [
                ("🚀", "Initialize", "Setting up AI engine"),
                ("📊", "Analyze", "Analyzing research content"),
                ("📥", "Download", "Processing document"),
                ("📋", "Plan", "Generating code architecture"),
                ("🔍", "References", "Analyzing references"),
                ("📦", "Repos", "Downloading repositories"),
                ("🗂️", "Index", "Building code index"),
                ("⚙️", "Implement", "Implementing code"),
            ]
            pipeline_mode = "COMPREHENSIVE"
        else:
            # Fast mode - skip indexing related stages
            stages = [
                ("🚀", "Initialize", "Setting up AI engine"),
                ("📊", "Analyze", "Analyzing research content"),
                ("📥", "Download", "Processing document"),
                ("📋", "Plan", "Generating code architecture"),
                ("⚙️", "Implement", "Implementing code"),
            ]
            pipeline_mode = "OPTIMIZED"

        print(
            f"\n{Colors.BOLD}{Colors.CYAN}📋 {pipeline_mode} PIPELINE STATUS{Colors.ENDC}"
        )
        self.print_separator("─", 79, Colors.CYAN)

        for i, (icon, name, desc) in enumerate(stages):
            if i < current_stage:
                status = f"{Colors.OKGREEN}✓ COMPLETED{Colors.ENDC}"
            elif i == current_stage:
                status = f"{Colors.YELLOW}⏳ IN PROGRESS{Colors.ENDC}"
            else:
                status = f"{Colors.CYAN}⏸️  PENDING{Colors.ENDC}"

            print(
                f"{icon} {Colors.BOLD}{name:<12}{Colors.ENDC} │ {desc:<25} │ {status}"
            )

        self.print_separator("─", 79, Colors.CYAN)

    def print_results_header(self):
        """Print results section header"""
        header = f"""
{Colors.BOLD}{Colors.OKGREEN}╔═══════════════════════════════════════════════════════════════════════════════╗
║                              PROCESSING RESULTS                              ║
╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
"""
        print(header)

    def print_error_box(self, title: str, error_msg: str):
        """Print formatted error box"""
        print(
            f"\n{Colors.FAIL}╔══════════════════════════════════════════════════════════════╗"
        )
        print(f"║ {Colors.BOLD}ERROR: {title:<50}{Colors.FAIL} ║")
        print("╠══════════════════════════════════════════════════════════════╣")

        words = error_msg.split()
        lines = []
        current_line = ""

        for word in words:
            if len(current_line + word) <= 54:
                current_line += word + " "
            else:
                lines.append(current_line.strip())
                current_line = word + " "
        if current_line:
            lines.append(current_line.strip())

        for line in lines:
            print(f"║ {line:<56} ║")

        print(
            f"╚══════════════════════════════════════════════════════════════╝{Colors.ENDC}"
        )

    def cleanup_cache(self):
        """清理Python缓存文件 / Clean up Python cache files"""
        try:
            self.print_status("Cleaning up cache files...", "info")
            # 清理__pycache__目录
            os.system('find . -type d -name "__pycache__" -exec rm -r {} + 2>/dev/null')
            # 清理.pyc文件
            os.system('find . -name "*.pyc" -delete 2>/dev/null')
            self.print_status("Cache cleanup completed", "success")
        except Exception as e:
            self.print_status(f"Cache cleanup failed: {e}", "warning")

    def print_goodbye(self):
        """Print goodbye message"""
        # 清理缓存文件
        self.cleanup_cache()

        goodbye = f"""
{Colors.BOLD}{Colors.CYAN}╔═══════════════════════════════════════════════════════════════════════════════╗
║                                GOODBYE                                        ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║  {Colors.OKGREEN}🎉 Thank you for using DeepCode CLI!                                     {Colors.CYAN}║
║                                                                               ║
║  {Colors.YELLOW}🧬 Join our community in revolutionizing research reproducibility         {Colors.CYAN}║
║  {Colors.PURPLE}⚡ Together, we're building the future of automated code generation       {Colors.CYAN}║
║                                                                               ║
║  {Colors.OKCYAN}💡 Questions? Contribute to our open-source mission at GitHub             {Colors.CYAN}║
║  {Colors.GREEN}🧹 Cache files cleaned up for optimal performance                         {Colors.CYAN}║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
"""
        print(goodbye)

    def get_requirement_analysis_input(self) -> str:
        """Enhanced requirement analysis input interface (NEW: matching UI version)"""
        self.print_separator("─", 79, Colors.BLUE)
        print(
            f"{Colors.BOLD}{Colors.BLUE}🧠 Requirement Analysis Interface{Colors.ENDC}"
        )
        print(
            f"{Colors.CYAN}Describe your project idea or requirements briefly.{Colors.ENDC}"
        )
        print(
            f"{Colors.CYAN}Our AI will generate guiding questions to help you refine your vision.{Colors.ENDC}"
        )
        self.print_separator("─", 79, Colors.BLUE)

        # Display examples
        print(f"\n{Colors.BOLD}{Colors.YELLOW}💡 Examples:{Colors.ENDC}")
        print(
            f"{Colors.CYAN}  • 'I want to build a machine learning system for image recognition'{Colors.ENDC}"
        )
        print(
            f"{Colors.CYAN}  • 'Create a web app for project management with real-time collaboration'{Colors.ENDC}"
        )
        print(
            f"{Colors.CYAN}  • 'Develop a data analysis pipeline for financial forecasting'{Colors.ENDC}"
        )

        self.print_separator("─", 79, Colors.BLUE)

        print(
            f"\n{Colors.BOLD}{Colors.OKCYAN}✏️  Enter your initial requirements below:{Colors.ENDC}"
        )
        print(
            f"{Colors.YELLOW}(Type your description, press Enter twice when finished, or Ctrl+C to cancel){Colors.ENDC}"
        )

        lines = []
        empty_line_count = 0

        while True:
            try:
                if len(lines) == 0:
                    print(f"{Colors.BOLD}> {Colors.ENDC}", end="")
                else:
                    print(f"{Colors.BOLD}  {Colors.ENDC}", end="")

                line = input()

                if line.strip() == "":
                    empty_line_count += 1
                    if empty_line_count >= 2:
                        break
                    lines.append("")
                else:
                    empty_line_count = 0
                    lines.append(line)

            except KeyboardInterrupt:
                print(f"\n{Colors.WARNING}Input cancelled by user{Colors.ENDC}")
                return ""

        user_input = "\n".join(lines).strip()

        if not user_input:
            self.print_status("No input provided", "warning")
            return ""

        if len(user_input) < 20:
            self.print_status(
                "Input too short. Please provide more details (at least 20 characters)",
                "warning",
            )
            retry = (
                input(f"{Colors.YELLOW}Try again? (y/n): {Colors.ENDC}").strip().lower()
            )
            if retry == "y":
                return self.get_requirement_analysis_input()
            return ""

        # Display input summary
        word_count = len(user_input.split())
        char_count = len(user_input)

        print(f"\n{Colors.BOLD}{Colors.GREEN}📋 Input Summary:{Colors.ENDC}")
        print(f"  • {Colors.CYAN}Word count: {word_count}{Colors.ENDC}")
        print(f"  • {Colors.CYAN}Character count: {char_count}{Colors.ENDC}")

        # Show preview
        preview = user_input[:200] + "..." if len(user_input) > 200 else user_input
        print(f"\n{Colors.BOLD}{Colors.CYAN}📄 Preview:{Colors.ENDC}")
        print(f"{Colors.YELLOW}{preview}{Colors.ENDC}")

        # Confirm
        confirm = (
            input(
                f"\n{Colors.BOLD}{Colors.OKCYAN}Proceed with this input? (y/n): {Colors.ENDC}"
            )
            .strip()
            .lower()
        )
        if confirm != "y":
            retry = (
                input(f"{Colors.YELLOW}Edit input? (y/n): {Colors.ENDC}")
                .strip()
                .lower()
            )
            if retry == "y":
                return self.get_requirement_analysis_input()
            return ""

        self.print_status(
            f"Requirement input captured: {word_count} words, {char_count} characters",
            "success",
        )
        return user_input

    def display_guiding_questions(self, questions_json: str):
        """Display AI-generated guiding questions (NEW: matching UI version)"""
        import json

        try:
            questions = json.loads(questions_json)

            self.print_separator("═", 79, Colors.GREEN)
            print(
                f"\n{Colors.BOLD}{Colors.GREEN}🤖 AI-Generated Guiding Questions{Colors.ENDC}"
            )
            print(
                f"{Colors.CYAN}Please answer these questions to help refine your requirements:{Colors.ENDC}\n"
            )
            self.print_separator("─", 79, Colors.GREEN)

            for i, q in enumerate(questions, 1):
                print(
                    f"\n{Colors.BOLD}{Colors.YELLOW}Question {i}:{Colors.ENDC} {Colors.CYAN}{q}{Colors.ENDC}"
                )

            self.print_separator("═", 79, Colors.GREEN)

        except json.JSONDecodeError:
            self.print_status("Failed to parse questions", "error")
            print(questions_json)

    def get_question_answers(self, questions_json: str) -> dict:
        """Get user answers to guiding questions (NEW: matching UI version)"""
        import json

        try:
            questions = json.loads(questions_json)
            answers = {}

            print(
                f"\n{Colors.BOLD}{Colors.BLUE}📝 Answer the following questions:{Colors.ENDC}"
            )
            print(
                f"{Colors.CYAN}(Type your answer and press Enter for each question){Colors.ENDC}\n"
            )

            for i, question in enumerate(questions, 1):
                print(
                    f"\n{Colors.BOLD}{Colors.YELLOW}Q{i}:{Colors.ENDC} {Colors.CYAN}{question}{Colors.ENDC}"
                )
                print(f"{Colors.BOLD}{Colors.OKCYAN}Your answer:{Colors.ENDC} ", end="")

                answer = input().strip()
                answers[f"question_{i}"] = answer

                if answer:
                    self.print_status(f"Answer {i} recorded", "success")
                else:
                    self.print_status(f"Answer {i} left blank", "warning")

            return answers

        except json.JSONDecodeError:
            self.print_status("Failed to parse questions", "error")
            return {}

    def display_requirement_summary(self, summary: str):
        """Display generated requirement document (NEW: matching UI version)"""
        self.print_separator("═", 79, Colors.GREEN)
        print(
            f"\n{Colors.BOLD}{Colors.GREEN}📄 Generated Requirement Document{Colors.ENDC}\n"
        )
        self.print_separator("─", 79, Colors.GREEN)

        print(f"{Colors.CYAN}{summary}{Colors.ENDC}")

        self.print_separator("═", 79, Colors.GREEN)

        # Ask if user wants to proceed with implementation
        proceed = (
            input(
                f"\n{Colors.BOLD}{Colors.YELLOW}Would you like to proceed with code implementation based on these requirements? (y/n):{Colors.ENDC} "
            )
            .strip()
            .lower()
        )

        return proceed == "y"

    def ask_continue(self) -> bool:
        """Ask if user wants to continue with another paper"""
        self.print_separator("─", 79, Colors.YELLOW)
        print(f"\n{Colors.BOLD}{Colors.YELLOW}🔄 Process another paper?{Colors.ENDC}")
        choice = input(f"{Colors.OKCYAN}Continue? (y/n): {Colors.ENDC}").strip().lower()
        return choice in ["y", "yes", "1", "true"]

    def add_to_history(self, input_source: str, result: dict):
        """Add processing result to history"""
        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "input_source": input_source,
            "status": result.get("status", "unknown"),
            "result": result,
        }
        self.processing_history.append(entry)

    def show_history(self):
        """Display processing history"""
        if not self.processing_history:
            self.print_status("No processing history available", "info")
            return

        print(f"\n{Colors.BOLD}{Colors.CYAN}📚 PROCESSING HISTORY{Colors.ENDC}")
        self.print_separator("─", 79, Colors.CYAN)

        for i, entry in enumerate(self.processing_history, 1):
            status_icon = "✅" if entry["status"] == "success" else "❌"
            source = entry["input_source"]
            if len(source) > 50:
                source = source[:47] + "..."

            print(f"{i}. {status_icon} {entry['timestamp']} | {source}")

        self.print_separator("─", 79, Colors.CYAN)

    def show_configuration_menu(self):
        """Show configuration options menu"""
        self.clear_screen()

        # Get segmentation config status
        segmentation_enabled = getattr(self, "segmentation_enabled", True)
        segmentation_threshold = getattr(self, "segmentation_threshold", 50000)

        print(f"""
{Colors.BOLD}{Colors.CYAN}╔═══════════════════════════════════════════════════════════════════════════════╗
║                           CONFIGURATION MENU                                  ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  {Colors.BOLD}🤖 Agent Orchestration Engine Configuration{Colors.CYAN}                             ║
║                                                                               ║
║  {Colors.OKCYAN}[1] Pipeline Mode:{Colors.CYAN}                                                        ║
║      {Colors.BOLD}🧠 Comprehensive Mode{Colors.CYAN} - Full intelligence analysis (Default)         ║
║         ✓ Research Analysis + Resource Processing                            ║
║         ✓ Reference Intelligence Discovery                                   ║
║         ✓ Automated Repository Acquisition                                   ║
║         ✓ Codebase Intelligence Orchestration                               ║
║         ✓ Intelligent Code Implementation Synthesis                         ║
║                                                                               ║
║      {Colors.BOLD}⚡ Optimized Mode{Colors.CYAN} - Fast processing (Skip indexing)                    ║
║         ✓ Research Analysis + Resource Processing                            ║
║         ✓ Code Architecture Synthesis                                        ║
║         ✓ Intelligent Code Implementation Synthesis                         ║
║         ✗ Reference Intelligence Discovery (Skipped)                        ║
║         ✗ Repository Acquisition (Skipped)                                   ║
║         ✗ Codebase Intelligence Orchestration (Skipped)                     ║
║                                                                               ║
║  {Colors.OKCYAN}[2] Document Processing:{Colors.CYAN}                                                   ║
║      {Colors.BOLD}📄 Smart Segmentation{Colors.CYAN} - Intelligent document analysis (Default)      ║
║         ✓ Semantic boundary detection                                        ║
║         ✓ Algorithm integrity preservation                                   ║
║         ✓ Formula chain recognition                                          ║
║         ✓ Adaptive character limits                                          ║
║                                                                               ║
║      {Colors.BOLD}📋 Traditional Processing{Colors.CYAN} - Full document reading                       ║
║         ✓ Complete document analysis                                         ║
║         ✗ Smart segmentation (Disabled)                                      ║
║                                                                               ║
║  {Colors.YELLOW}Current Settings:{Colors.CYAN}                                                         ║
║    Pipeline: {'🧠 Comprehensive Mode' if self.enable_indexing else '⚡ Optimized Mode'}                                          ║
║    Document: {'📄 Smart Segmentation' if segmentation_enabled else '📋 Traditional Processing'}                                ║
║    Threshold: {segmentation_threshold} characters                                    ║
║                                                                               ║
║  {Colors.OKGREEN}[T] Toggle Pipeline    {Colors.BLUE}[S] Toggle Segmentation    {Colors.FAIL}[B] Back{Colors.CYAN}     ║
╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
""")

        while True:
            print(
                f"\n{Colors.BOLD}{Colors.OKCYAN}➤ Configuration choice: {Colors.ENDC}",
                end="",
            )
            choice = input().strip().lower()

            if choice in ["t", "toggle"]:
                self.enable_indexing = not self.enable_indexing
                mode = "🧠 Comprehensive" if self.enable_indexing else "⚡ Optimized"
                self.print_status(f"Pipeline mode switched to: {mode}", "success")
                time.sleep(1)
                self.show_configuration_menu()
                return

            elif choice in ["s", "segmentation"]:
                current_state = getattr(self, "segmentation_enabled", True)
                self.segmentation_enabled = not current_state
                # Save the configuration to file
                self._save_segmentation_config()
                seg_mode = (
                    "📄 Smart Segmentation"
                    if self.segmentation_enabled
                    else "📋 Traditional Processing"
                )
                self.print_status(
                    f"Document processing switched to: {seg_mode}", "success"
                )
                time.sleep(1)
                self.show_configuration_menu()
                return

            elif choice in ["b", "back"]:
                return

            else:
                self.print_status(
                    "Invalid choice. Please enter 'T', 'S', or 'B'.", "warning"
                )
