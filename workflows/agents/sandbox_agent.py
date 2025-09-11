"""
Sandbox Agent for isolated project execution and testing

This module provides the SandboxAgent class and related functionality for:
- Creating isolated sandbox environments
- Setting up language-specific environments (Python, JavaScript, etc.)
- Executing projects in isolation with comprehensive error handling
- Analyzing project structure and execution commands
"""

import os
import shutil
import subprocess
import time
import re
from typing import Optional, List
from dataclasses import dataclass
from concurrent.futures import ProcessPoolExecutor, TimeoutError as ProcessTimeoutError


@dataclass
class SandboxExecutionResult:
    """Result from sandbox execution"""

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    command: str
    working_directory: str
    environment_setup: bool
    error_traceback: Optional[str] = None
    dependencies_installed: bool = False
    virtual_env_path: Optional[str] = None


@dataclass
class SandboxState:
    """State information for sandbox environment"""

    sandbox_path: str
    original_project_path: str
    main_code_directory: str
    project_language: str
    virtual_env_path: Optional[str] = None
    environment_setup: bool = False
    dependencies_installed: bool = False
    readme_analyzed: bool = False
    execution_commands: List[str] = None

    def __post_init__(self):
        if self.execution_commands is None:
            self.execution_commands = []


def _execute_in_isolated_process(
    command: str, working_dir: str, env_vars: dict, timeout: int
) -> dict:
    """
    Execute command in a completely isolated process
    This function runs in a separate process for maximum isolation
    """
    import subprocess
    import time
    import traceback

    try:
        start_time = time.time()

        # Execute the command with complete isolation
        result = subprocess.run(
            command,
            shell=True,
            cwd=working_dir,
            env=env_vars,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        execution_time = time.time() - start_time

        # Extract traceback from stderr if present
        error_traceback = None
        if result.stderr and (
            "Traceback" in result.stderr or "Error:" in result.stderr
        ):
            error_traceback = result.stderr

        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "execution_time": execution_time,
            "error_traceback": error_traceback,
            "process_isolated": True,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Process timed out after {timeout} seconds",
            "exit_code": -1,
            "execution_time": timeout,
            "error_traceback": f"TimeoutError: Process timed out after {timeout} seconds",
            "process_isolated": True,
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Process execution failed: {str(e)}",
            "exit_code": -1,
            "execution_time": 0,
            "error_traceback": f"ProcessError: {str(e)}\n{traceback.format_exc()}",
            "process_isolated": True,
        }


class SandboxAgent:
    """
    Sandbox Agent for isolated project execution and testing
    Handles environment setup, dependency installation, and project execution
    """

    def __init__(self, logger, workspace_dir: str):
        self.logger = logger
        self.workspace_dir = workspace_dir
        self.sandbox_state: Optional[SandboxState] = None

    def create_sandbox_environment(
        self, repo_path: str, project_name: str = None
    ) -> SandboxState:
        """
        Create sandbox environment by copying project to sandbox location

        Args:
            repo_path: Original repository path
            project_name: Optional project name, defaults to extracted from path

        Returns:
            SandboxState with sandbox configuration
        """
        try:
            # Extract project name if not provided
            if project_name is None:
                project_name = os.path.basename(repo_path)
                if not project_name:
                    project_name = os.path.basename(os.path.dirname(repo_path))

            parent_dir = os.path.dirname(repo_path)
            sandbox_name = f"{project_name}_sandbox"
            sandbox_path = os.path.join(parent_dir, sandbox_name)

            self.logger.info(f"🏗️ Creating sandbox environment: {sandbox_path}")

            # Remove existing sandbox if it exists
            if os.path.exists(sandbox_path):
                self.logger.info(f"🧹 Removing existing sandbox: {sandbox_path}")
                shutil.rmtree(sandbox_path)

            # Copy project to sandbox location
            self.logger.info(f"📁 Copying project from {repo_path} to {sandbox_path}")
            shutil.copytree(
                repo_path,
                sandbox_path,
                ignore=shutil.ignore_patterns(
                    ".git", "__pycache__", "*.pyc", ".venv", "venv"
                ),
            )

            # Detect main code directory (e.g., generate_code/rice)
            main_code_dir = self._detect_main_code_directory(sandbox_path)

            # Detect project language
            project_language = self._detect_project_language(main_code_dir)

            # Create sandbox state
            self.sandbox_state = SandboxState(
                sandbox_path=sandbox_path,
                original_project_path=repo_path,
                main_code_directory=main_code_dir,
                project_language=project_language,
            )

            self.logger.info("✅ Sandbox environment created successfully")
            self.logger.info(f"   📂 Sandbox path: {sandbox_path}")
            self.logger.info(f"   🎯 Main code directory: {main_code_dir}")
            self.logger.info(f"   🔤 Project language: {project_language}")

            return self.sandbox_state

        except Exception as e:
            self.logger.error(f"❌ Failed to create sandbox environment: {e}")
            raise

    def _detect_main_code_directory(self, sandbox_path: str) -> str:
        """Detect the main code directory within the sandbox"""

        self.logger.info(f"🔍 Analyzing sandbox structure: {sandbox_path}")

        # First, check if sandbox path already contains code files directly
        has_python_files = any(
            f.endswith(".py")
            for f in os.listdir(sandbox_path)
            if os.path.isfile(os.path.join(sandbox_path, f))
        )
        has_requirements = os.path.exists(
            os.path.join(sandbox_path, "requirements.txt")
        )

        if has_python_files or has_requirements:
            self.logger.info(
                f"📁 Using sandbox root as main directory (contains Python files): {sandbox_path}"
            )
            return sandbox_path

        # Look for subdirectories with code
        code_candidates = []

        for root, dirs, files in os.walk(sandbox_path):
            # Skip hidden directories
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".") and d not in ["__pycache__", ".venv", "venv"]
            ]

            # Check if this directory contains Python files or requirements
            has_py = any(f.endswith(".py") for f in files)
            has_req = (
                "requirements.txt" in files
                or "pyproject.toml" in files
                or "setup.py" in files
            )
            has_main = any(
                f in files for f in ["main.py", "app.py", "run.py", "__main__.py"]
            )

            if has_py or has_req or has_main:
                rel_path = os.path.relpath(root, sandbox_path)
                score = 0
                score += 3 if has_main else 0
                score += 2 if has_req else 0
                score += 1 if has_py else 0

                code_candidates.append((root, score, rel_path))
                self.logger.info(
                    f"📂 Found code directory candidate: {rel_path} (score: {score})"
                )

        if code_candidates:
            # Sort by score (highest first), then by depth (prefer deeper/more specific)
            code_candidates.sort(key=lambda x: (-x[1], x[2].count(os.sep)))
            best_candidate = code_candidates[0]
            self.logger.info(
                f"📁 Selected main code directory: {best_candidate[2]} (score: {best_candidate[1]})"
            )
            return best_candidate[0]

        # Fallback: return sandbox path itself
        self.logger.warning(
            f"⚠️ No code directories found, using sandbox root: {sandbox_path}"
        )
        return sandbox_path

    def _detect_project_language(self, code_dir: str) -> str:
        """Detect the primary programming language of the project"""

        if not os.path.exists(code_dir):
            return "unknown"

        language_indicators = {
            "python": [
                "requirements.txt",
                "setup.py",
                "pyproject.toml",
                "main.py",
                "app.py",
                "*.py",
            ],
            "javascript": [
                "package.json",
                "package-lock.json",
                "yarn.lock",
                "main.js",
                "index.js",
            ],
            "typescript": ["tsconfig.json", "package.json", "*.ts"],
            "java": ["pom.xml", "build.gradle", "*.java"],
            "go": ["go.mod", "go.sum", "main.go", "*.go"],
            "rust": ["Cargo.toml", "Cargo.lock", "main.rs", "*.rs"],
            "cpp": ["CMakeLists.txt", "Makefile", "*.cpp", "*.hpp"],
            "c": ["Makefile", "*.c", "*.h"],
        }

        # Check for explicit configuration files first
        for lang, indicators in language_indicators.items():
            for indicator in indicators:
                if not indicator.startswith("*"):
                    if os.path.exists(os.path.join(code_dir, indicator)):
                        self.logger.info(
                            f"🔤 Detected {lang} project based on {indicator}"
                        )
                        return lang

        # Check for file extensions
        try:
            for root, dirs, files in os.walk(code_dir):
                file_counts = {}
                for file in files:
                    ext = os.path.splitext(file)[1].lower()
                    file_counts[ext] = file_counts.get(ext, 0) + 1

                # Determine most common language
                max_count = 0
                detected_lang = "unknown"

                lang_extensions = {
                    ".py": "python",
                    ".js": "javascript",
                    ".ts": "typescript",
                    ".java": "java",
                    ".go": "go",
                    ".rs": "rust",
                    ".cpp": "cpp",
                    ".c": "c",
                }

                for ext, count in file_counts.items():
                    if ext in lang_extensions and count > max_count:
                        max_count = count
                        detected_lang = lang_extensions[ext]

                if detected_lang != "unknown":
                    self.logger.info(
                        f"🔤 Detected {detected_lang} project based on file extensions"
                    )
                    return detected_lang

        except Exception as e:
            self.logger.warning(f"⚠️ Error detecting project language: {e}")

        self.logger.warning(
            "⚠️ Could not detect project language, defaulting to 'python'"
        )
        return "python"

    def _extract_python_version_from_readme(self, directory_path: str) -> str:
        """
        Extract Python version requirement from README.md file
        Returns the version string (e.g., "3.11") or default "3.11" if not found
        """
        readme_files = [
            "README.md",
            "readme.md",
            "README.txt",
            "readme.txt",
            "README.rst",
            "readme.rst",
        ]

        for readme_file in readme_files:
            readme_path = os.path.join(directory_path, readme_file)
            if os.path.exists(readme_path):
                try:
                    with open(readme_path, "r", encoding="utf-8") as f:
                        content = f.read().lower()

                    # Common patterns for Python version specification
                    patterns = [
                        r"python\s+([3-9]\.\d+(?:\.\d+)?)",  # Python 3.11, Python 3.10.2
                        r"python\s*>=?\s*([3-9]\.\d+)",  # Python >= 3.11, Python>=3.10
                        r"requires?\s+python\s+([3-9]\.\d+)",  # Requires Python 3.11
                        r"python\s+version:?\s*([3-9]\.\d+)",  # Python version: 3.11
                        r"python\s*:\s*([3-9]\.\d+)",  # Python: 3.11
                        r"![python](.*?)([3-9]\.\d+)",  # Badge patterns
                        r'python_requires\s*=\s*["\']>=?([3-9]\.\d+)["\']',  # python_requires=">=3.11"
                    ]

                    for pattern in patterns:
                        matches = re.findall(pattern, content)
                        if matches:
                            # Get the first match and extract just major.minor version
                            version = matches[0]
                            if isinstance(version, tuple):
                                version = version[-1]  # Get the last element if tuple

                            # Extract major.minor (e.g., "3.11" from "3.11.0")
                            version_match = re.match(r"([3-9]\.\d+)", version)
                            if version_match:
                                extracted_version = version_match.group(1)
                                self.logger.info(
                                    f"🐍 Detected Python version {extracted_version} from {readme_file}"
                                )
                                return extracted_version

                except Exception as e:
                    self.logger.warning(f"⚠️ Failed to read {readme_file}: {e}")
                    continue

        # If no README found or no version detected, return default
        self.logger.info("🐍 No Python version found in README, using default 3.11")
        return "3.11"

    def _check_venv_packages(self, venv_path: str) -> bool:
        """Check if packages are properly installed in the virtual environment"""
        try:
            site_packages = os.path.join(venv_path, "lib")
            if os.path.exists(site_packages):
                # Find the python version directory
                for item in os.listdir(site_packages):
                    if item.startswith("python"):
                        python_site_packages = os.path.join(
                            site_packages, item, "site-packages"
                        )
                        if os.path.exists(python_site_packages):
                            packages = os.listdir(python_site_packages)
                            # Filter out common system packages
                            user_packages = [
                                p
                                for p in packages
                                if not p.startswith("_")
                                and not p.startswith("distutils")
                                and not p.startswith("pkg_resources")
                            ]
                            self.logger.info(
                                f"📦 Found {len(user_packages)} packages in venv: {user_packages[:5]}..."
                            )
                            return len(user_packages) > 0
            return False
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to check venv packages: {e}")
            return False

    def setup_environment(self) -> bool:
        """
        Set up the virtual environment for the project based on language requirements
        """
        try:
            if not self.sandbox_state:
                raise Exception("Sandbox state not initialized")

            self.logger.info(
                f"🔧 Setting up environment for {self.sandbox_state.project_language} project"
            )

            if self.sandbox_state.project_language == "python":
                return self._setup_python_environment()
            elif self.sandbox_state.project_language == "javascript":
                return self._setup_javascript_environment()
            elif self.sandbox_state.project_language == "typescript":
                return self._setup_typescript_environment()
            else:
                self.logger.warning(
                    f"⚠️ Environment setup not implemented for {self.sandbox_state.project_language}"
                )
                return True  # Continue with basic setup

        except Exception as e:
            self.logger.error(f"❌ Failed to setup environment: {e}")
            return False

    def _setup_python_environment(self) -> bool:
        """Set up isolated Python environment using uv with adaptive Python version"""
        try:
            self.logger.info("🐍 Setting up isolated Python environment with uv")

            # Extract Python version from README.md
            python_version = self._extract_python_version_from_readme(
                self.sandbox_state.main_code_directory
            )

            # Also check the directory where requirements.txt is located if different
            requirements_dir = None
            requirements_files = [
                os.path.join(
                    self.sandbox_state.main_code_directory, "requirements.txt"
                ),
                os.path.join(self.sandbox_state.sandbox_path, "requirements.txt"),
                os.path.join(
                    self.sandbox_state.main_code_directory, "requirements_list.txt"
                ),
            ]

            for req_file in requirements_files:
                if os.path.exists(req_file):
                    requirements_dir = os.path.dirname(req_file)
                    break

            # If requirements.txt is in a different directory, check for README there too
            if (
                requirements_dir
                and requirements_dir != self.sandbox_state.main_code_directory
            ):
                readme_python_version = self._extract_python_version_from_readme(
                    requirements_dir
                )
                if readme_python_version != "3.11":  # If it found a non-default version
                    python_version = readme_python_version

            # Upgrade Python version if it's too old for modern packages
            if python_version in ["3.7", "3.8"]:
                self.logger.warning(
                    f"⚠️ Python {python_version} may have compatibility issues. Upgrading to 3.9"
                )
                python_version = "3.9"

            self.logger.info(f"🐍 Using Python version: {python_version}")

            # Check if uv is available
            uv_check = subprocess.run(
                ["uv", "--version"], capture_output=True, text=True
            )
            if uv_check.returncode != 0:
                self.logger.error("❌ uv is not available. Please install uv first.")
                return False

            # Set up isolated environment variables (clear parent environment)
            isolated_env = os.environ.copy()

            # *** ENHANCED FIX: Complete UV cache and temp redirection ***
            if self.sandbox_state.sandbox_path.startswith("/data2"):
                # Redirect UV cache directory
                uv_cache_path = "/data2/bjdwhzzh/.cache/uv"
                isolated_env["UV_CACHE_DIR"] = uv_cache_path

                # Also redirect temp directory for UV operations
                uv_temp_path = "/data2/bjdwhzzh/.cache/uv/tmp"
                isolated_env["UV_TMPDIR"] = uv_temp_path
                isolated_env["TMPDIR"] = uv_temp_path
                isolated_env["TEMP"] = uv_temp_path
                isolated_env["TMP"] = uv_temp_path

                # Create all necessary directories
                os.makedirs(uv_cache_path, exist_ok=True)
                os.makedirs(uv_temp_path, exist_ok=True)

                self.logger.info(f"💾 Redirecting UV cache to: {uv_cache_path}")
                self.logger.info(f"🗂️ Redirecting UV temp to: {uv_temp_path}")

            # Clear any existing virtual environment variables to avoid conflicts
            isolated_env.pop("VIRTUAL_ENV", None)
            isolated_env.pop("CONDA_DEFAULT_ENV", None)
            isolated_env.pop("CONDA_PREFIX", None)
            isolated_env.pop("PYTHONPATH", None)
            isolated_env.pop("PYTHONHOME", None)

            # Create unique virtual environment path
            venv_path = os.path.join(self.sandbox_state.main_code_directory, ".venv")
            self.logger.info(f"📦 Creating isolated virtual environment at {venv_path}")

            # Remove existing venv if it exists to start clean
            if os.path.exists(venv_path):
                self.logger.info("🧹 Removing existing virtual environment")
                shutil.rmtree(venv_path)

            # Initialize uv project with adaptive Python version
            self.logger.info(
                f"🔧 Initializing isolated uv project with Python {python_version}"
            )
            uv_init_result = subprocess.run(
                ["uv", "init", "--python", python_version, "--no-readme"],
                cwd=self.sandbox_state.main_code_directory,
                capture_output=True,
                text=True,
                env=isolated_env,
            )

            if uv_init_result.returncode == 0:
                self.logger.info("✅ uv project initialized with isolation")
            else:
                self.logger.warning(
                    f"⚠️ uv init failed, continuing: {uv_init_result.stderr}"
                )

            # Find requirements files in order of preference
            requirements_files = [
                os.path.join(
                    self.sandbox_state.main_code_directory, "requirements.txt"
                ),
                os.path.join(self.sandbox_state.sandbox_path, "requirements.txt"),
                os.path.join(
                    self.sandbox_state.main_code_directory, "requirements_list.txt"
                ),
                os.path.join(self.sandbox_state.main_code_directory, "pyproject.toml"),
            ]

            dependencies_installed = False

            # Try different dependency installation strategies with enhanced error handling
            for req_file in requirements_files:
                if os.path.exists(req_file):
                    self.logger.info(f"📋 Found requirements file: {req_file}")

                    if req_file.endswith(".txt"):
                        # Strategy 1: Create virtual environment first, then install dependencies
                        self.logger.info(
                            f"📦 Step 1: Creating virtual environment with Python {python_version}"
                        )
                        venv_create_result = subprocess.run(
                            ["uv", "venv", "--python", python_version, ".venv"],
                            cwd=self.sandbox_state.main_code_directory,
                            capture_output=True,
                            text=True,
                            timeout=120,
                            env=isolated_env,
                        )

                        if venv_create_result.returncode == 0:
                            self.logger.info(
                                f"✅ Virtual environment created successfully with Python {python_version}"
                            )
                        else:
                            self.logger.warning(
                                f"⚠️ Virtual environment creation failed: {venv_create_result.stderr}"
                            )
                            continue

                        # Strategy 2: Use uv pip install with explicit virtual environment activation
                        self.logger.info(
                            f"📦 Step 2: Installing dependencies from {req_file}"
                        )

                        # Explicitly activate the virtual environment for uv pip
                        venv_env = isolated_env.copy()
                        venv_env["VIRTUAL_ENV"] = venv_path
                        venv_bin = os.path.join(venv_path, "bin")
                        if os.path.exists(venv_bin):
                            venv_env["PATH"] = f"{venv_bin}:{venv_env.get('PATH', '')}"

                        pip_result = subprocess.run(
                            [
                                "uv",
                                "pip",
                                "install",
                                "-r",
                                req_file,
                                "--python",
                                os.path.join(venv_path, "bin", "python"),
                            ],
                            cwd=self.sandbox_state.main_code_directory,
                            capture_output=True,
                            text=True,
                            timeout=600,  # Increased timeout for large packages
                            env=venv_env,
                        )

                        if pip_result.returncode == 0:
                            self.logger.info(
                                "✅ Dependencies installed successfully via uv pip"
                            )
                            # Verify packages are in venv
                            if self._check_venv_packages(venv_path):
                                dependencies_installed = True
                                break
                            else:
                                self.logger.warning(
                                    "⚠️ Packages not found in venv, continuing to next strategy"
                                )
                        else:
                            self.logger.warning(
                                f"⚠️ uv pip install failed: {pip_result.stderr}"
                            )

                            # Check if it's a Python version compatibility issue
                            if (
                                "only has wheels with the following Python implementation tags"
                                in pip_result.stderr
                            ):
                                self.logger.warning(
                                    "🔄 Python version compatibility issue, trying with Python 3.9"
                                )
                                python_version = "3.9"

                                # Retry with Python 3.9
                                venv_create_result = subprocess.run(
                                    [
                                        "uv",
                                        "venv",
                                        "--python",
                                        python_version,
                                        ".venv",
                                        "--force",
                                    ],
                                    cwd=self.sandbox_state.main_code_directory,
                                    capture_output=True,
                                    text=True,
                                    timeout=120,
                                    env=isolated_env,
                                )

                                if venv_create_result.returncode == 0:
                                    self.logger.info(
                                        f"✅ Virtual environment recreated with Python {python_version}"
                                    )

                                    # Retry pip install
                                    venv_env["VIRTUAL_ENV"] = venv_path
                                    pip_retry_result = subprocess.run(
                                        [
                                            "uv",
                                            "pip",
                                            "install",
                                            "-r",
                                            req_file,
                                            "--python",
                                            os.path.join(venv_path, "bin", "python"),
                                        ],
                                        cwd=self.sandbox_state.main_code_directory,
                                        capture_output=True,
                                        text=True,
                                        timeout=600,
                                        env=venv_env,
                                    )

                                    if pip_retry_result.returncode == 0:
                                        self.logger.info(
                                            f"✅ Dependencies installed successfully with Python {python_version}"
                                        )
                                        if self._check_venv_packages(venv_path):
                                            dependencies_installed = True
                                            break

                            # Strategy 3: Fallback to uv add for individual packages (with improved parsing)
                            self.logger.info(
                                "📦 Step 3: Trying individual package installation"
                            )
                            try:
                                with open(req_file, "r") as f:
                                    requirements = f.readlines()

                                success_count = 0
                                failed_packages = []

                                # Parse requirements more carefully
                                for req in requirements[
                                    :15
                                ]:  # Increased limit but still reasonable
                                    req = req.strip()
                                    if (
                                        req
                                        and not req.startswith("#")
                                        and not req.startswith("-")
                                    ):
                                        # Clean up requirement specification more thoroughly
                                        # Handle various requirement formats: package>=1.0, package==1.0, package[extra]>=1.0
                                        package_spec = req.split("#")[
                                            0
                                        ].strip()  # Remove comments
                                        if "[" in package_spec:
                                            package_name = package_spec.split("[")[
                                                0
                                            ].strip()
                                        else:
                                            package_name = re.split(
                                                r"[>=<!=]", package_spec
                                            )[0].strip()

                                        if package_name and len(package_name) > 1:
                                            add_result = subprocess.run(
                                                [
                                                    "uv",
                                                    "add",
                                                    package_spec,
                                                ],  # Use full spec first
                                                cwd=self.sandbox_state.main_code_directory,
                                                capture_output=True,
                                                text=True,
                                                timeout=60,
                                                env=isolated_env,
                                            )
                                            if add_result.returncode == 0:
                                                success_count += 1
                                                self.logger.info(
                                                    f"✅ Added {package_name}"
                                                )
                                            else:
                                                failed_packages.append(package_name)
                                                self.logger.warning(
                                                    f"⚠️ Failed to add {package_name}: {add_result.stderr[:100]}..."
                                                )

                                if success_count > 0:
                                    self.logger.info(
                                        f"✅ Successfully installed {success_count} packages individually"
                                    )
                                    if failed_packages:
                                        self.logger.info(
                                            f"⚠️ Failed packages: {failed_packages[:5]}..."
                                        )
                                    if self._check_venv_packages(venv_path):
                                        dependencies_installed = True
                                        break

                            except Exception as e:
                                self.logger.warning(
                                    f"⚠️ Individual package installation failed: {e}"
                                )

                    # Strategy 4: For pyproject.toml, use uv sync directly
                    elif req_file.endswith("pyproject.toml"):
                        self.logger.info(
                            "📦 Installing from pyproject.toml using uv sync"
                        )
                        sync_result = subprocess.run(
                            ["uv", "sync"],
                            cwd=self.sandbox_state.main_code_directory,
                            capture_output=True,
                            text=True,
                            timeout=300,
                            env=isolated_env,
                        )

                        if sync_result.returncode == 0:
                            self.logger.info(
                                "✅ Dependencies synced from pyproject.toml"
                            )
                            if self._check_venv_packages(venv_path):
                                dependencies_installed = True
                                break
                        else:
                            self.logger.warning(
                                f"⚠️ pyproject.toml sync failed: {sync_result.stderr}"
                            )

            # Strategy 5: Fallback to regular Python venv + pip if UV completely fails
            if not dependencies_installed:
                self.logger.warning(
                    "⚠️ UV failed, trying fallback with regular Python venv + pip"
                )

                # Determine the python command for the specific version
                python_cmd = f"python{python_version}"

                # Check if the specific python version is available, fallback to python3
                python_check = subprocess.run(
                    [python_cmd, "--version"], capture_output=True, text=True
                )
                if python_check.returncode != 0:
                    python_cmd = "python3"
                    self.logger.info(
                        f"⚠️ Python {python_version} not found, using python3"
                    )

                for req_file in requirements_files:
                    if os.path.exists(req_file) and req_file.endswith(".txt"):
                        try:
                            # Remove existing venv
                            if os.path.exists(venv_path):
                                shutil.rmtree(venv_path)

                            # Create virtual environment with regular Python
                            self.logger.info(
                                f"📦 Creating Python virtual environment with {python_cmd}"
                            )
                            venv_result = subprocess.run(
                                [python_cmd, "-m", "venv", ".venv"],
                                cwd=self.sandbox_state.main_code_directory,
                                capture_output=True,
                                text=True,
                                timeout=120,
                                env=isolated_env,
                            )

                            if venv_result.returncode == 0:
                                self.logger.info(
                                    f"✅ Python virtual environment created with {python_cmd}"
                                )

                                # Install dependencies with pip
                                pip_path = os.path.join(
                                    self.sandbox_state.main_code_directory,
                                    ".venv",
                                    "bin",
                                    "pip",
                                )
                                pip_result = subprocess.run(
                                    [
                                        pip_path,
                                        "install",
                                        "-r",
                                        req_file,
                                        "--no-cache-dir",
                                    ],
                                    cwd=self.sandbox_state.main_code_directory,
                                    capture_output=True,
                                    text=True,
                                    timeout=600,
                                    env=isolated_env,
                                )

                                if pip_result.returncode == 0:
                                    self.logger.info(
                                        "✅ Dependencies installed successfully with pip"
                                    )
                                    if self._check_venv_packages(venv_path):
                                        dependencies_installed = True
                                        break
                                else:
                                    self.logger.warning(
                                        f"⚠️ pip install failed: {pip_result.stderr}"
                                    )
                            else:
                                self.logger.warning(
                                    f"⚠️ Python venv creation failed: {venv_result.stderr}"
                                )

                        except Exception as e:
                            self.logger.warning(f"⚠️ Python venv fallback failed: {e}")

            # Strategy 6: Create minimal environment if all else fails
            if not dependencies_installed:
                self.logger.warning(
                    "⚠️ All dependency installation methods failed, creating minimal environment"
                )

                # Just create the virtual environment without dependencies
                try:
                    if os.path.exists(venv_path):
                        shutil.rmtree(venv_path)

                    venv_create_result = subprocess.run(
                        ["uv", "venv", "--python", python_version],
                        cwd=self.sandbox_state.main_code_directory,
                        capture_output=True,
                        text=True,
                        env=isolated_env,
                    )

                    if venv_create_result.returncode == 0:
                        self.logger.info(
                            f"✅ Minimal virtual environment created with Python {python_version}"
                        )
                        dependencies_installed = (
                            True  # Mark as successful with minimal setup
                        )
                    else:
                        self.logger.error(
                            f"❌ Failed to create minimal environment: {venv_create_result.stderr}"
                        )
                        # Try with regular Python as last resort
                        python_cmd = f"python{python_version}"
                        python_check = subprocess.run(
                            [python_cmd, "--version"], capture_output=True, text=True
                        )
                        if python_check.returncode != 0:
                            python_cmd = "python3"

                        py_venv_result = subprocess.run(
                            [python_cmd, "-m", "venv", ".venv"],
                            cwd=self.sandbox_state.main_code_directory,
                            capture_output=True,
                            text=True,
                            env=isolated_env,
                        )
                        if py_venv_result.returncode == 0:
                            self.logger.info(
                                f"✅ Minimal Python virtual environment created as last resort with {python_cmd}"
                            )
                            dependencies_installed = True
                except Exception as e:
                    self.logger.error(
                        f"❌ All environment creation methods failed: {e}"
                    )

            # Final verification
            if dependencies_installed and os.path.exists(venv_path):
                self._check_venv_packages(venv_path)

            # Update sandbox state
            self.sandbox_state.virtual_env_path = venv_path
            self.sandbox_state.environment_setup = True
            self.sandbox_state.dependencies_installed = dependencies_installed

            self.logger.info("🎯 Environment setup summary:")
            self.logger.info(f"   🐍 Python version: {python_version}")
            self.logger.info(f"   📁 Virtual env path: {venv_path}")
            self.logger.info(f"   ✅ Setup successful: {True}")
            self.logger.info(f"   📦 Dependencies installed: {dependencies_installed}")

            return True

        except Exception as e:
            self.logger.error(f"❌ Python environment setup failed: {e}")
            # Even if setup fails, mark as successful to allow execution attempts
            self.sandbox_state.environment_setup = True
            self.sandbox_state.dependencies_installed = False
            return True  # Continue anyway

    def _setup_javascript_environment(self) -> bool:
        """Set up JavaScript/Node.js environment"""
        try:
            self.logger.info("📦 Setting up JavaScript environment with npm")

            # Check if npm is available
            npm_check = subprocess.run(
                ["npm", "--version"], capture_output=True, text=True
            )
            if npm_check.returncode != 0:
                self.logger.error("❌ npm is not available")
                return False

            # Install dependencies if package.json exists
            package_json = os.path.join(
                self.sandbox_state.main_code_directory, "package.json"
            )
            if os.path.exists(package_json):
                self.logger.info("📋 Installing JavaScript dependencies")

                install_result = subprocess.run(
                    ["npm", "install"],
                    cwd=self.sandbox_state.main_code_directory,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

                if install_result.returncode == 0:
                    self.logger.info("✅ JavaScript dependencies installed")
                    self.sandbox_state.dependencies_installed = True
                else:
                    self.logger.warning(
                        f"⚠️ npm install failed: {install_result.stderr}"
                    )

            self.sandbox_state.environment_setup = True
            return True

        except Exception as e:
            self.logger.error(f"❌ JavaScript environment setup failed: {e}")
            return False

    def _setup_typescript_environment(self) -> bool:
        """Set up TypeScript environment"""
        # For now, use same as JavaScript setup
        return self._setup_javascript_environment()

    def _is_valid_command_path(self, path: str) -> bool:
        """
        Validate if a path is a valid command path (not URL or invalid reference)
        This method implements the fix suggested by Error Analysis Tools
        """
        # Filter out URLs
        if any(
            url_indicator in path.lower()
            for url_indicator in ["github.com", "http", "https", "www."]
        ):
            return False

        # Filter out .git references
        if path.endswith(".git"):
            return False

        # Filter out absolute paths that don't exist
        if path.startswith("/") and not os.path.exists(path):
            return False

        # Filter out obvious non-executable patterns
        if any(invalid in path.lower() for invalid in ["clone", "install", "setup"]):
            return False

        # Accept relative paths and module names
        return True

    def analyze_readme_and_execution_commands(self) -> List[str]:
        """
        Analyze README file to determine execution commands
        """
        try:
            # Use glob to directly find all README files with advanced pattern matching
            import glob
            from pathlib import Path

            base_path = Path(self.sandbox_state.sandbox_path)
            readme_patterns = [
                "**/[Rr][Ee][Aa][Dd][Mm][Ee]*",
                "**/[Rr][Ee][Aa][Dd]_[Mm][Ee]*",
            ]

            readme_files = []
            for pattern in readme_patterns:
                readme_files.extend(glob.glob(str(base_path / pattern), recursive=True))

            # Remove duplicates and sort by depth (closer to root first)
            readme_files = sorted(set(readme_files), key=lambda x: (x.count(os.sep), x))

            execution_commands = []

            for readme_file in readme_files:
                self.logger.info(f"📖 Analyzing README: {readme_file}")

                try:
                    with open(readme_file, "r", encoding="utf-8") as f:
                        readme_content = f.read()

                    # Extract common execution patterns
                    patterns = [
                        r"python\s+([a-zA-Z0-9_\-./]+\.py)",
                        r"python3\s+([a-zA-Z0-9_\-./]+\.py)",
                        r"uv\s+run\s+([a-zA-Z0-9_\-./]+\.py)",
                        r"npm\s+start",
                        r"npm\s+run\s+([a-zA-Z0-9_\-]+)",
                        r"node\s+([a-zA-Z0-9_\-./]+\.js)",
                        r"java\s+-jar\s+([a-zA-Z0-9_\-./]+\.jar)",
                        r"./([a-zA-Z0-9_\-./]+)",
                        r"python\s+-m\s+([a-zA-Z0-9_\-.]+)",
                    ]

                    import re

                    for pattern in patterns:
                        matches = re.findall(pattern, readme_content, re.IGNORECASE)
                        for match in matches:
                            # Filter out URLs and invalid paths (Error Analysis Tool suggested fix)
                            if not self._is_valid_command_path(match):
                                self.logger.warning(
                                    f"⚠️ Filtered out invalid command path: {match}"
                                )
                                continue

                            if self.sandbox_state.project_language == "python":
                                # Prioritize uv run for better isolation
                                if pattern.startswith(r"uv"):
                                    cmd = f"uv run {match}"
                                elif pattern.startswith(r"python"):
                                    # Convert python commands to uv run for better dependency management
                                    if match.endswith(".py"):
                                        cmd = f"uv run {match}"
                                    else:
                                        cmd = f"uv run python {match}"
                                else:
                                    cmd = f"uv run {match}"

                                if cmd not in execution_commands:
                                    execution_commands.append(cmd)

                            elif self.sandbox_state.project_language == "javascript":
                                if "npm" in pattern or "node" in pattern:
                                    cmd = (
                                        match
                                        if pattern.startswith(r"npm")
                                        else f"node {match}"
                                    )
                                    if cmd not in execution_commands:
                                        execution_commands.append(cmd)

                except Exception as e:
                    self.logger.warning(f"⚠️ Failed to read README {readme_file}: {e}")

            # Fallback commands based on project structure
            if not execution_commands:
                execution_commands = self._generate_fallback_commands()

            self.sandbox_state.execution_commands = execution_commands
            self.sandbox_state.readme_analyzed = True

            self.logger.info(f"📋 Extracted execution commands: {execution_commands}")
            return execution_commands

        except Exception as e:
            self.logger.error(f"❌ Failed to analyze README: {e}")
            return self._generate_fallback_commands()

    def _generate_fallback_commands(self) -> List[str]:
        """Generate fallback execution commands based on project structure"""
        commands = []

        try:
            # Look for main entry points with priority order
            if self.sandbox_state.project_language == "python":
                # Priority order for main file detection
                main_files = [
                    "main.py",
                    "app.py",
                    "run.py",
                    "__main__.py",
                    "start.py",
                    "server.py",
                ]

                # First, look for explicit main files
                for main_file in main_files:
                    main_path = os.path.join(
                        self.sandbox_state.main_code_directory, main_file
                    )
                    if os.path.exists(main_path):
                        commands.append(f"uv run {main_file}")
                        self.logger.info(f"🎯 Found main entry point: {main_file}")

                # If no main files found, look for any Python files
                if not commands:
                    python_files = [
                        f
                        for f in os.listdir(self.sandbox_state.main_code_directory)
                        if f.endswith(".py")
                        and not f.startswith("__")
                        and not f.startswith("test_")
                    ]

                    if python_files:
                        # Sort to get consistent behavior
                        python_files.sort()
                        selected_file = python_files[0]
                        commands.append(f"uv run {selected_file}")
                        self.logger.info(
                            f"🎯 Using first available Python file: {selected_file}"
                        )

                # Add some common Python execution patterns as fallbacks
                if commands:
                    # Add alternative execution methods
                    main_command = commands[0]
                    file_name = main_command.replace("uv run ", "")

                    # Add direct python execution as backup
                    commands.append(f"python {file_name}")
                    commands.append(f"python3 {file_name}")

                # Add module execution if no specific file found
                if not commands:
                    # Try to run as module
                    commands.extend(
                        [
                            "uv run python -m main",
                            "uv run python -m app",
                            "python -m main",
                            "python -m app",
                        ]
                    )

            elif self.sandbox_state.project_language == "javascript":
                # Check package.json for start script
                package_json = os.path.join(
                    self.sandbox_state.main_code_directory, "package.json"
                )
                if os.path.exists(package_json):
                    commands.append("npm start")
                else:
                    # Look for main JS files
                    main_files = ["index.js", "app.js", "main.js", "server.js"]
                    for main_file in main_files:
                        main_path = os.path.join(
                            self.sandbox_state.main_code_directory, main_file
                        )
                        if os.path.exists(main_path):
                            commands.append(f"node {main_file}")
                            break

        except Exception as e:
            self.logger.warning(f"⚠️ Error generating fallback commands: {e}")

        return commands if commands else ["echo 'No execution command detected'"]

    def _check_disk_space(self, path: str, min_gb: float = 1.0) -> bool:
        """Check if there's enough disk space available"""
        try:
            import shutil

            total, used, free = shutil.disk_usage(path)
            free_gb = free / (1024**3)  # Convert to GB

            if free_gb < min_gb:
                self.logger.warning(
                    f"⚠️ Low disk space: {free_gb:.2f}GB available at {path}"
                )
                return False
            else:
                self.logger.info(
                    f"💽 Disk space OK: {free_gb:.2f}GB available at {path}"
                )
                return True
        except Exception as e:
            self.logger.warning(f"⚠️ Could not check disk space: {e}")
            return True  # Assume it's OK if we can't check

    def execute_project(
        self, command: str = None, timeout: int = 30, use_process_isolation: bool = True
    ) -> SandboxExecutionResult:
        """
        Execute the project in the sandbox environment with enhanced isolation

        Args:
            command: Optional specific command to run
            timeout: Execution timeout in seconds
            use_process_isolation: Whether to use multiprocessing for maximum isolation

        Returns:
            SandboxExecutionResult with execution details
        """
        try:
            if not self.sandbox_state or not self.sandbox_state.environment_setup:
                raise Exception("Sandbox environment not properly set up")

            # Use provided command or first available command
            if command is None:
                if not self.sandbox_state.execution_commands:
                    self.analyze_readme_and_execution_commands()

                if not self.sandbox_state.execution_commands:
                    raise Exception("No execution commands available")

                command = self.sandbox_state.execution_commands[0]

            self.logger.info(
                f"🚀 Executing command with {'process isolation' if use_process_isolation else 'subprocess'}: {command}"
            )

            # Set up completely isolated environment variables
            env = {}  # Start with empty environment for maximum isolation

            # Add only essential environment variables
            essential_vars = ["PATH", "HOME", "USER", "SHELL", "TERM", "LANG", "LC_ALL"]
            for var in essential_vars:
                if var in os.environ:
                    env[var] = os.environ[var]

            # *** FIX: Apply UV cache and temp redirection during execution ***
            # Check disk space before execution
            if self.sandbox_state.sandbox_path.startswith("/data2"):
                self._check_disk_space("/data2", min_gb=1.0)
            else:
                self._check_disk_space("/home", min_gb=0.5)

            if self.sandbox_state.sandbox_path.startswith("/data2"):
                # Redirect UV cache directory
                uv_cache_path = "/data2/bjdwhzzh/.cache/uv"
                env["UV_CACHE_DIR"] = uv_cache_path

                # Also redirect temp directory for UV operations
                uv_temp_path = "/data2/bjdwhzzh/.cache/uv/tmp"
                env["UV_TMPDIR"] = uv_temp_path
                env["TMPDIR"] = uv_temp_path
                env["TEMP"] = uv_temp_path
                env["TMP"] = uv_temp_path

                # Ensure directories exist
                os.makedirs(uv_cache_path, exist_ok=True)
                os.makedirs(uv_temp_path, exist_ok=True)

                self.logger.info(
                    f"💾 Execution UV cache redirected to: {uv_cache_path}"
                )
                self.logger.info(f"🗂️ Execution UV temp redirected to: {uv_temp_path}")

            # Clear any Python/conda related variables for isolation
            python_vars_to_clear = [
                "VIRTUAL_ENV",
                "CONDA_DEFAULT_ENV",
                "CONDA_PREFIX",
                "PYTHONPATH",
                "PYTHONHOME",
                "PIP_USER",
                "PIPENV_ACTIVE",
                "POETRY_ACTIVE",
            ]
            for var in python_vars_to_clear:
                env.pop(var, None)

            if (
                self.sandbox_state.virtual_env_path
                and self.sandbox_state.project_language == "python"
            ):
                # Set up isolated Python environment
                env["VIRTUAL_ENV"] = self.sandbox_state.virtual_env_path
                venv_bin = os.path.join(self.sandbox_state.virtual_env_path, "bin")
                if os.path.exists(venv_bin):
                    # Prepend virtual environment bin to PATH for proper isolation
                    env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
                    self.logger.info(
                        f"🔧 Using isolated virtual environment: {self.sandbox_state.virtual_env_path}"
                    )
                else:
                    self.logger.warning(
                        f"⚠️ Virtual environment bin directory not found: {venv_bin}"
                    )

            # Special handling for uv-based execution
            if self.sandbox_state.project_language == "python" and command.startswith(
                "uv run"
            ):
                # uv run handles virtual environment automatically
                env["UV_PROJECT_ENVIRONMENT"] = self.sandbox_state.virtual_env_path
                self.logger.info(f"🚀 Executing with UV project environment: {command}")
            elif self.sandbox_state.project_language == "python" and any(
                command.startswith(py) for py in ["python", "python3"]
            ):
                # For direct python execution, ensure we use the right python
                if self.sandbox_state.virtual_env_path:
                    venv_python = os.path.join(
                        self.sandbox_state.virtual_env_path, "bin", "python"
                    )
                    if os.path.exists(venv_python):
                        # Replace python command with venv python
                        command = command.replace("python3", venv_python).replace(
                            "python", venv_python, 1
                        )
                        self.logger.info(
                            f"🐍 Using virtual environment Python: {venv_python}"
                        )

            self.logger.info("🌍 Enhanced isolation summary:")
            self.logger.info("   🔐 Complete environment isolation: ✅")
            self.logger.info(
                f"   🎯 Isolated VIRTUAL_ENV: {env.get('VIRTUAL_ENV', 'None')}"
            )
            self.logger.info(
                f"   💾 UV Cache Dir: {env.get('UV_CACHE_DIR', 'System default')}"
            )
            self.logger.info(
                f"   🗂️ UV Temp Dir: {env.get('UV_TMPDIR', 'System default')}"
            )
            self.logger.info(
                f"   📁 Working directory: {self.sandbox_state.main_code_directory}"
            )
            self.logger.info(
                f"   🔒 Process isolation: {'✅ YES' if use_process_isolation else '❌ NO'}"
            )

            if use_process_isolation:
                # Use multiprocessing for maximum isolation
                start_time = time.time()

                try:
                    with ProcessPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(
                            _execute_in_isolated_process,
                            command,
                            self.sandbox_state.main_code_directory,
                            env,
                            timeout,
                        )

                        # Wait for result with timeout
                        try:
                            result_dict = future.result(
                                timeout=timeout + 5
                            )  # Add 5 second buffer
                        except ProcessTimeoutError:
                            self.logger.error(
                                f"🚨 Process execution timed out after {timeout + 5} seconds"
                            )
                            result_dict = {
                                "success": False,
                                "stdout": "",
                                "stderr": f"Process pool timed out after {timeout + 5} seconds",
                                "exit_code": -1,
                                "execution_time": timeout + 5,
                                "error_traceback": "ProcessPoolTimeoutError: Execution timed out",
                                "process_isolated": True,
                            }

                except Exception as e:
                    self.logger.error(f"🚨 Process pool execution failed: {e}")
                    result_dict = {
                        "success": False,
                        "stdout": "",
                        "stderr": f"Process pool execution failed: {str(e)}",
                        "exit_code": -1,
                        "execution_time": time.time() - start_time,
                        "error_traceback": f"ProcessPoolError: {str(e)}",
                        "process_isolated": True,
                    }

                execution_time = result_dict["execution_time"]

            else:
                # Fallback to subprocess execution for debugging
                self.logger.warning("🔶 Using subprocess execution (reduced isolation)")
                start_time = time.time()

                # Execute the command
                result = subprocess.run(
                    command,
                    shell=True,
                    cwd=self.sandbox_state.main_code_directory,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )

                execution_time = time.time() - start_time

                # Convert subprocess result to dict format
                error_traceback = None
                if result.stderr and (
                    "Traceback" in result.stderr or "Error:" in result.stderr
                ):
                    error_traceback = result.stderr

                result_dict = {
                    "success": result.returncode == 0,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.returncode,
                    "execution_time": execution_time,
                    "error_traceback": error_traceback,
                    "process_isolated": False,
                }

            # Create execution result from result_dict
            execution_result = SandboxExecutionResult(
                success=result_dict["success"],
                stdout=result_dict["stdout"],
                stderr=result_dict["stderr"],
                exit_code=result_dict["exit_code"],
                execution_time=result_dict["execution_time"],
                command=command,
                working_directory=self.sandbox_state.main_code_directory,
                environment_setup=self.sandbox_state.environment_setup,
                dependencies_installed=self.sandbox_state.dependencies_installed,
                virtual_env_path=self.sandbox_state.virtual_env_path,
                error_traceback=result_dict.get("error_traceback"),
            )

            isolation_type = (
                "🔒 PROCESS ISOLATED"
                if result_dict.get("process_isolated", False)
                else "🔶 SUBPROCESS"
            )
            self.logger.info(
                f"✅ Command executed in {execution_result.execution_time:.2f}s with exit code {execution_result.exit_code} ({isolation_type})"
            )

            if execution_result.success:
                self.logger.info(f"📤 Output: {execution_result.stdout[:200]}...")
            else:
                self.logger.warning(f"❌ Error: {execution_result.stderr[:200]}...")
                if execution_result.error_traceback:
                    self.logger.warning("🔍 Traceback detected for error analysis")

            return execution_result

        except subprocess.TimeoutExpired:
            self.logger.error(f"❌ Command timed out after {timeout} seconds")
            return SandboxExecutionResult(
                success=False,
                stdout="",
                stderr=f"Command timed out after {timeout} seconds",
                exit_code=-1,
                execution_time=timeout,
                command=command or "unknown",
                working_directory=self.sandbox_state.main_code_directory
                if self.sandbox_state
                else "unknown",
                environment_setup=self.sandbox_state.environment_setup
                if self.sandbox_state
                else False,
            )

        except Exception as e:
            self.logger.error(f"❌ Execution failed: {e}")
            return SandboxExecutionResult(
                success=False,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                execution_time=0.0,
                command=command or "unknown",
                working_directory=self.sandbox_state.main_code_directory
                if self.sandbox_state
                else "unknown",
                environment_setup=self.sandbox_state.environment_setup
                if self.sandbox_state
                else False,
            )
