#!/usr/bin/env python3
"""
Docker Management MCP Server

This MCP server provides Docker-based environment isolation and management
for safe repository evaluation and code execution.

Core Features:
1. Container lifecycle management (create, start, stop, remove)
2. Safe file mounting and workspace setup
3. Dependency installation in isolated environments
4. Command execution with output capture
5. Resource monitoring and limits
6. Multi-language environment support

Tools Provided:
- create_evaluation_container: Create isolated container for evaluation
- setup_container_workspace: Mount repository and setup workspace
- install_dependencies: Install project dependencies in container
- execute_in_container: Execute commands safely in container
- get_container_logs: Retrieve execution logs and outputs
- cleanup_container: Clean up container resources
- list_available_images: List supported base images
- monitor_container_resources: Monitor resource usage

Security Features:
- Read-only repository mounting
- Resource limits (CPU, memory, disk)
- Network isolation options
- Temporary container cleanup
- Secure command execution

Usage:
python tools/docker_management_server.py
"""

import os
import json
import sys
import subprocess
import time
import tempfile
from typing import Dict, Any, List, Optional
import logging
from dataclasses import dataclass, asdict
import docker
from docker.errors import DockerException, ImageNotFound

# Import MCP modules
from mcp.server.fastmcp import FastMCP

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastMCP server instance
mcp = FastMCP("docker-management")


@dataclass
class ContainerInfo:
    """Container information structure"""

    container_id: str
    name: str
    image: str
    status: str
    created_at: str
    workspace_path: str
    ports: Dict[str, int] = None
    environment: Dict[str, str] = None

    def __post_init__(self):
        if self.ports is None:
            self.ports = {}
        if self.environment is None:
            self.environment = {}


@dataclass
class ExecutionResult:
    """Command execution result structure"""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    execution_time: float
    success: bool


@dataclass
class ResourceUsage:
    """Container resource usage information"""

    cpu_percent: float
    memory_usage_mb: float
    memory_limit_mb: float
    memory_percent: float
    network_io: Dict[str, int]
    block_io: Dict[str, int]


# Supported base images for different language environments
SUPPORTED_IMAGES = {
    "python": {
        "3.9": "python:3.9-slim",
        "3.10": "python:3.10-slim",
        "3.11": "python:3.11-slim",
        "3.12": "python:3.12-slim",
    },
    "python-conda": {
        "3.9": "continuumio/miniconda3:latest",
        "3.10": "continuumio/miniconda3:latest",
        "3.11": "continuumio/miniconda3:latest",
        "3.12": "continuumio/miniconda3:latest",
    },
    "ubuntu-conda": {
        "3.9": "continuumio/miniconda3:latest",
        "3.10": "continuumio/miniconda3:latest",
        "3.11": "continuumio/miniconda3:latest",
        "3.12": "continuumio/miniconda3:latest",
        "latest": "continuumio/miniconda3:latest",
    },
    "node": {"16": "node:16-alpine", "18": "node:18-alpine", "20": "node:20-alpine"},
    "java": {
        "11": "openjdk:11-jdk-slim",
        "17": "openjdk:17-jdk-slim",
        "21": "openjdk:21-jdk-slim",
    },
    "ubuntu": {"20.04": "ubuntu:20.04", "22.04": "ubuntu:22.04"},
    "multi": {
        "python-node": "nikolaik/python-nodejs:python3.9-nodejs18",
        "full": "continuumio/miniconda3",
    },
}

# Default resource limits
DEFAULT_LIMITS = {
    "memory": "2g",
    "cpu_count": 2,
    "cpu_percent": 100,
    "disk_quota": "10g",
}

# Container name prefix
CONTAINER_PREFIX = "deepcode_eval"

# Global Docker client
docker_client = None


def get_docker_client():
    """Get Docker client with error handling"""
    global docker_client
    if docker_client is None:
        try:
            docker_client = docker.from_env()
            # Test connection
            docker_client.ping()
            logger.info("Docker client connected successfully")
        except DockerException as e:
            logger.warning(f"Docker not available: {e}")
            docker_client = None
            return None
        except Exception as e:
            logger.warning(f"Docker connection failed: {e}")
            docker_client = None
            return None
    return docker_client


def generate_container_name(prefix: str = CONTAINER_PREFIX) -> str:
    """Generate unique container name"""
    timestamp = int(time.time())
    return f"{prefix}_{timestamp}_{os.getpid()}"


def select_base_image(language: str, version: str = None) -> str:
    """Select appropriate base image for language/version"""
    if language not in SUPPORTED_IMAGES:
        return "ubuntu:22.04"  # Default fallback

    lang_images = SUPPORTED_IMAGES[language]
    if version and version in lang_images:
        return lang_images[version]

    # Return first available version
    return list(lang_images.values())[0]


def prepare_workspace_commands(
    repo_path: str, workspace_path: str = "/root"
) -> List[str]:
    """Prepare commands to setup workspace in container"""
    commands = [f"mkdir -p {workspace_path}", f"cd {workspace_path}", "ls -la"]
    return commands


def parse_resource_stats(stats: Dict) -> ResourceUsage:
    """Parse Docker container resource statistics"""
    try:
        # CPU usage calculation
        cpu_stats = stats.get("cpu_stats", {})
        precpu_stats = stats.get("precpu_stats", {})

        cpu_usage = cpu_stats.get("cpu_usage", {}).get("total_usage", 0)
        precpu_usage = precpu_stats.get("cpu_usage", {}).get("total_usage", 0)

        system_usage = cpu_stats.get("system_cpu_usage", 0)
        presystem_usage = precpu_stats.get("system_cpu_usage", 0)

        cpu_delta = cpu_usage - precpu_usage
        system_delta = system_usage - presystem_usage

        online_cpus = cpu_stats.get("online_cpus", 1)
        if system_delta > 0 and cpu_delta > 0:
            cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0
        else:
            cpu_percent = 0.0

        # Memory usage
        memory_stats = stats.get("memory_stats", {})
        memory_usage = memory_stats.get("usage", 0)
        memory_limit = memory_stats.get("limit", 0)
        memory_usage_mb = memory_usage / (1024 * 1024)
        memory_limit_mb = memory_limit / (1024 * 1024)
        memory_percent = (memory_usage / memory_limit * 100) if memory_limit > 0 else 0

        # Network I/O
        networks = stats.get("networks", {})
        network_io = {}
        for interface, net_stats in networks.items():
            network_io[f"{interface}_rx"] = net_stats.get("rx_bytes", 0)
            network_io[f"{interface}_tx"] = net_stats.get("tx_bytes", 0)

        # Block I/O
        blkio_stats = stats.get("blkio_stats", {})
        io_service_bytes = blkio_stats.get("io_service_bytes_recursive", [])
        block_io = {"read": 0, "write": 0}
        for entry in io_service_bytes:
            if entry.get("op") == "Read":
                block_io["read"] += entry.get("value", 0)
            elif entry.get("op") == "Write":
                block_io["write"] += entry.get("value", 0)

        return ResourceUsage(
            cpu_percent=round(cpu_percent, 2),
            memory_usage_mb=round(memory_usage_mb, 2),
            memory_limit_mb=round(memory_limit_mb, 2),
            memory_percent=round(memory_percent, 2),
            network_io=network_io,
            block_io=block_io,
        )

    except Exception as e:
        logger.warning(f"Failed to parse resource stats: {e}")
        return ResourceUsage(
            cpu_percent=0.0,
            memory_usage_mb=0.0,
            memory_limit_mb=0.0,
            memory_percent=0.0,
            network_io={},
            block_io={},
        )


# ==================== MCP Tool Definitions ====================


@mcp.tool()
async def read_file_in_container(
    container_id: str, file_path: str, working_dir: str = "/workspace/repo"
) -> str:
    """
    Read a file from inside the container.

    Args:
        container_id: Container ID or name
        file_path: Path to file inside container (relative to working_dir)
        working_dir: Working directory in container

    Returns:
        JSON string with file contents
    """
    try:
        client = get_docker_client()
        container = client.containers.get(container_id)

        # Construct full path
        full_path = (
            f"{working_dir}/{file_path}" if not file_path.startswith("/") else file_path
        )

        # Read file content
        exec_result = container.exec_run(f"cat {full_path}", workdir=working_dir)

        if exec_result.exit_code == 0:
            content = exec_result.output.decode("utf-8", errors="replace")
            result = {
                "status": "success",
                "container_id": container_id,
                "file_path": file_path,
                "content": content,
                "file_size": len(content),
                "message": "File read successfully",
            }
        else:
            error_msg = exec_result.output.decode("utf-8", errors="replace")
            result = {
                "status": "error",
                "container_id": container_id,
                "file_path": file_path,
                "message": f"Failed to read file: {error_msg}",
            }

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Failed to read file in container: {e}")
        return json.dumps({"status": "error", "message": f"File read failed: {str(e)}"})


@mcp.tool()
async def write_file_in_container(
    container_id: str,
    file_path: str,
    content: str,
    working_dir: str = "/workspace/repo",
    create_backup: bool = True,
) -> str:
    """
    Write content to a file inside the container.

    Args:
        container_id: Container ID or name
        file_path: Path to file inside container (relative to working_dir)
        content: Content to write to the file
        working_dir: Working directory in container
        create_backup: Whether to create a backup before writing

    Returns:
        JSON string with write result
    """
    try:
        client = get_docker_client()
        container = client.containers.get(container_id)

        # Construct full path
        full_path = (
            f"{working_dir}/{file_path}" if not file_path.startswith("/") else file_path
        )

        backup_created = False

        # Create backup if requested and file exists
        if create_backup:
            backup_result = container.exec_run(f"test -f {full_path}")
            if backup_result.exit_code == 0:
                backup_path = f"{full_path}.backup_{int(time.time())}"
                container.exec_run(f"cp {full_path} {backup_path}")
                backup_created = True

        # Create directory if it doesn't exist
        dir_path = os.path.dirname(full_path)
        if dir_path:
            container.exec_run(f"mkdir -p {dir_path}")

        # Write content to file using a more reliable method
        # Use Python to write the file to handle multiline content safely
        import base64

        # Encode content to base64 to handle special characters safely
        content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")

        # Use Python to decode and write the file
        python_cmd = f"""python3 -c "
import base64
content = base64.b64decode('{content_b64}').decode('utf-8')
with open('{full_path}', 'w') as f:
    f.write(content)
print('File written successfully')
" """

        exec_result = container.exec_run(python_cmd, workdir=working_dir)

        if exec_result.exit_code == 0:
            # Verify file was written
            verify_result = container.exec_run(f"wc -c {full_path}")
            file_size = 0
            if verify_result.exit_code == 0:
                size_output = verify_result.output.decode("utf-8").strip()
                file_size = int(size_output.split()[0]) if size_output else 0

            result = {
                "status": "success",
                "container_id": container_id,
                "file_path": file_path,
                "content_size": len(content),
                "file_size": file_size,
                "backup_created": backup_created,
                "message": "File written successfully",
            }
        else:
            error_msg = exec_result.output.decode("utf-8", errors="replace")
            result = {
                "status": "error",
                "container_id": container_id,
                "file_path": file_path,
                "message": f"Failed to write file: {error_msg}",
            }

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Failed to write file in container: {e}")
        return json.dumps(
            {"status": "error", "message": f"File write failed: {str(e)}"}
        )


@mcp.tool()
async def list_files_in_container(
    container_id: str,
    directory_path: str = "/workspace/repo",
    recursive: bool = False,
    file_extensions: List[str] = None,
) -> str:
    """
    List files in a directory inside the container.

    Args:
        container_id: Container ID or name
        directory_path: Directory to list
        recursive: Whether to list recursively
        file_extensions: Filter by file extensions (e.g., [".py", ".js"])

    Returns:
        JSON string with file list
    """
    try:
        client = get_docker_client()
        container = client.containers.get(container_id)

        # Build ls command
        if recursive:
            ls_cmd = f"find {directory_path} -type f"
            if file_extensions:
                ext_pattern = " -o ".join(
                    [f"-name '*.{ext.lstrip('.')}'" for ext in file_extensions]
                )
                ls_cmd += f" \\( {ext_pattern} \\)"
        else:
            ls_cmd = f"ls -la {directory_path}"

        exec_result = container.exec_run(ls_cmd)

        if exec_result.exit_code == 0:
            output = exec_result.output.decode("utf-8", errors="replace")
            files = []

            if recursive:
                # Parse find output
                for line in output.strip().split("\n"):
                    if line.strip():
                        files.append({"path": line.strip(), "type": "file"})
            else:
                # Parse ls -la output
                lines = output.strip().split("\n")[1:]  # Skip total line
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 9:
                        file_info = {
                            "permissions": parts[0],
                            "type": "directory" if parts[0].startswith("d") else "file",
                            "size": parts[4],
                            "name": " ".join(parts[8:]),
                            "path": f"{directory_path}/{' '.join(parts[8:])}",
                        }
                        files.append(file_info)

            result = {
                "status": "success",
                "container_id": container_id,
                "directory_path": directory_path,
                "files": files,
                "total_files": len(files),
                "message": "Directory listing completed",
            }
        else:
            error_msg = exec_result.output.decode("utf-8", errors="replace")
            result = {
                "status": "error",
                "container_id": container_id,
                "directory_path": directory_path,
                "message": f"Failed to list directory: {error_msg}",
            }

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Failed to list files in container: {e}")
        return json.dumps(
            {"status": "error", "message": f"Directory listing failed: {str(e)}"}
        )


@mcp.tool()
async def analyze_repo_structure_in_container(
    container_id: str, repo_path: str = "/workspace/repo"
) -> str:
    """
    Analyze repository structure inside the container to understand how to run the code.

    Args:
        container_id: Container ID or name
        repo_path: Path to repository in container

    Returns:
        JSON string with repository analysis
    """
    try:
        client = get_docker_client()
        container = client.containers.get(container_id)

        analysis = {
            "status": "success",
            "container_id": container_id,
            "repo_path": repo_path,
            "analysis": {},
        }

        # Check for common entry points and configuration files
        common_files = [
            "main.py",
            "app.py",
            "run.py",
            "__main__.py",
            "requirements.txt",
            "setup.py",
            "pyproject.toml",
            "package.json",
            "Makefile",
            "README.md",
            "train.py",
            "test.py",
            "demo.py",
        ]

        found_files = {}
        for file in common_files:
            check_result = container.exec_run(
                f"test -f {repo_path}/{file}", workdir=repo_path
            )
            if check_result.exit_code == 0:
                # Get file info
                stat_result = container.exec_run(f"ls -la {file}", workdir=repo_path)
                if stat_result.exit_code == 0:
                    found_files[file] = stat_result.output.decode("utf-8").strip()

        analysis["analysis"]["found_files"] = found_files

        # Detect programming language
        language_indicators = {
            "python": [".py", "requirements.txt", "setup.py", "pyproject.toml"],
            "javascript": [".js", "package.json", "node_modules"],
            "java": [".java", "pom.xml", ".gradle"],
            "cpp": [".cpp", ".c", ".h", "Makefile", "CMakeLists.txt"],
        }

        detected_languages = []
        for lang, indicators in language_indicators.items():
            for indicator in indicators:
                if indicator.startswith("."):
                    # File extension check
                    find_result = container.exec_run(
                        f"find {repo_path} -name '*{indicator}' | head -5",
                        workdir=repo_path,
                    )
                    if (
                        find_result.exit_code == 0
                        and find_result.output.decode().strip()
                    ):
                        detected_languages.append(lang)
                        break
                else:
                    # Specific file check
                    if indicator in found_files:
                        detected_languages.append(lang)
                        break

        analysis["analysis"]["detected_languages"] = list(set(detected_languages))

        # Try to find entry points
        entry_points = []
        if "main.py" in found_files:
            entry_points.append(
                {"file": "main.py", "command": "python main.py", "priority": 1}
            )
        if "app.py" in found_files:
            entry_points.append(
                {"file": "app.py", "command": "python app.py", "priority": 2}
            )
        if "run.py" in found_files:
            entry_points.append(
                {"file": "run.py", "command": "python run.py", "priority": 2}
            )
        if "train.py" in found_files:
            entry_points.append(
                {"file": "train.py", "command": "python train.py", "priority": 3}
            )

        # Check for __main__.py
        main_check = container.exec_run(
            f"test -f {repo_path}/__main__.py", workdir=repo_path
        )
        if main_check.exit_code == 0:
            entry_points.append(
                {"file": "__main__.py", "command": "python -m .", "priority": 1}
            )

        analysis["analysis"]["possible_entry_points"] = sorted(
            entry_points, key=lambda x: x["priority"]
        )

        # Check for test files
        test_check = container.exec_run(
            f"find {repo_path} -name '*test*.py' -o -name 'test_*.py' | head -10",
            workdir=repo_path,
        )
        if test_check.exit_code == 0:
            test_files = [
                f.strip() for f in test_check.output.decode().split("\n") if f.strip()
            ]
            analysis["analysis"]["test_files"] = test_files

        analysis["message"] = "Repository analysis completed"
        return json.dumps(analysis, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Failed to analyze repository structure: {e}")
        return json.dumps(
            {"status": "error", "message": f"Repository analysis failed: {str(e)}"}
        )


@mcp.tool()
async def list_available_images() -> str:
    """
    List all supported Docker base images for different languages.

    Returns:
        JSON string with available images categorized by language
    """
    try:
        result = {
            "status": "success",
            "supported_images": SUPPORTED_IMAGES,
            "default_limits": DEFAULT_LIMITS,
            "recommendations": {
                "python": "Use python:3.11-slim for most Python projects",
                "node": "Use node:18-alpine for Node.js projects",
                "java": "Use openjdk:17-jdk-slim for Java projects",
                "multi": "Use nikolaik/python-nodejs for Python+Node.js projects",
            },
        }
        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Failed to list available images: {e}")
        return json.dumps(
            {"status": "error", "message": f"Failed to list images: {str(e)}"}
        )


@mcp.tool()
async def create_evaluation_container(
    image_type: str,
    version: str = None,
    container_name: str = None,
    memory_limit: str = "2g",
    cpu_limit: str = "2",
    network_mode: str = "bridge",
) -> str:
    """
    Create a new Docker container for code evaluation.

    Args:
        image_type: Type of base image (python, node, java, ubuntu, multi)
        version: Specific version of the image (optional)
        container_name: Custom container name (auto-generated if None)
        memory_limit: Memory limit (e.g., "2g", "1024m")
        cpu_limit: CPU limit (number of CPUs)
        network_mode: Network isolation mode ("none", "bridge", "host")

    Returns:
        JSON string with container creation result
    """
    try:
        client = get_docker_client()

        # Select appropriate image
        base_image = select_base_image(image_type, version)
        logger.info(f"Selected base image: {base_image}")

        # Generate container name
        if not container_name:
            container_name = generate_container_name()

        # Pull image if not available
        try:
            client.images.get(base_image)
        except ImageNotFound:
            logger.info(f"Pulling image: {base_image}")
            client.images.pull(base_image)

        # Container configuration
        container_config = {
            "image": base_image,
            "name": container_name,
            "detach": True,
            "tty": True,
            "stdin_open": True,
            "mem_limit": memory_limit,
            "cpu_count": int(float(cpu_limit)),
            "network_mode": network_mode,
            "working_dir": "/root",
            "environment": {
                "DEBIAN_FRONTEND": "noninteractive",
                "PYTHONUNBUFFERED": "1",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
            "command": "/bin/bash",
            "auto_remove": False,  # We'll manage cleanup manually
        }

        # Create container
        container = client.containers.create(**container_config)

        # Start container
        container.start()

        # Wait a moment for container to be ready
        time.sleep(1)

        container_info = ContainerInfo(
            container_id=container.id,
            name=container_name,
            image=base_image,
            status="running",
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            workspace_path="/root",
            environment=container_config["environment"],
        )

        result = {
            "status": "success",
            "container": asdict(container_info),
            "message": f"Container '{container_name}' created successfully",
        }

        logger.info(f"Container created: {container_name} ({container.id[:12]})")
        return json.dumps(result, indent=2, ensure_ascii=False)

    except DockerException as e:
        logger.error(f"Docker error creating container: {e}")
        return json.dumps({"status": "error", "message": f"Docker error: {str(e)}"})
    except Exception as e:
        logger.error(f"Failed to create container: {e}")
        return json.dumps(
            {"status": "error", "message": f"Container creation failed: {str(e)}"}
        )


@mcp.tool()
async def setup_conda_environment(
    container_id: str, python_version: str = "3.12", env_name: str = "code_evaluate"
) -> str:
    """
    Setup conda environment in container based on grader.Dockerfile approach.

    Args:
        container_id: Docker container ID
        python_version: Python version to install in conda environment
        env_name: Name of conda environment to create

    Returns:
        JSON string with setup result
    """
    try:
        client = get_docker_client()
        container = client.containers.get(container_id)

        setup_results = []

        # Check if running in ubuntu-conda image (needs conda installation)
        image_name = container.image.tags[0] if container.image.tags else "unknown"
        needs_conda_install = "ubuntu" in image_name

        if needs_conda_install:
            logger.info(
                "Setting up conda environment from scratch in Ubuntu container..."
            )

            # Step 1: Update package manager and install basic packages (like grader.Dockerfile lines 10-16)
            update_cmd = "apt-get update && apt-get install -y curl wget build-essential git && rm -rf /var/lib/apt/lists/*"
            exec_result = container.exec_run(update_cmd, workdir="/")
            setup_results.append(
                {
                    "step": "install_basic_packages",
                    "command": update_cmd,
                    "exit_code": exec_result.exit_code,
                    "output": exec_result.output.decode()[:500],
                }
            )

            # Step 2: Download and install miniconda (like grader.Dockerfile lines 18-21)
            download_cmd = "wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh"
            exec_result = container.exec_run(download_cmd, workdir="/")
            setup_results.append(
                {
                    "step": "download_miniconda",
                    "command": download_cmd,
                    "exit_code": exec_result.exit_code,
                    "output": exec_result.output.decode()[:500],
                }
            )

            # Step 3: Install miniconda
            install_cmd = (
                "bash /tmp/miniconda.sh -b -p /opt/conda && rm /tmp/miniconda.sh"
            )
            exec_result = container.exec_run(install_cmd, workdir="/")
            setup_results.append(
                {
                    "step": "install_miniconda",
                    "command": install_cmd,
                    "exit_code": exec_result.exit_code,
                    "output": exec_result.output.decode()[:500],
                }
            )

            # Step 4: Initialize conda
            init_cmd = "/opt/conda/bin/conda init bash"
            exec_result = container.exec_run(init_cmd, workdir="/")
            setup_results.append(
                {
                    "step": "init_conda",
                    "command": init_cmd,
                    "exit_code": exec_result.exit_code,
                    "output": exec_result.output.decode()[:500],
                }
            )

        # Step 5: Create conda environment (like grader.Dockerfile line 24)
        if needs_conda_install:
            create_env_cmd = (
                f"/opt/conda/bin/conda create -n {env_name} python={python_version} -y"
            )
        else:
            # If using miniconda base image, conda should already be available
            create_env_cmd = f"conda create -n {env_name} python={python_version} -y"

        exec_result = container.exec_run(create_env_cmd, workdir="/")
        setup_results.append(
            {
                "step": "create_conda_env",
                "command": create_env_cmd,
                "exit_code": exec_result.exit_code,
                "output": exec_result.output.decode()[:500],
            }
        )

        # Step 6: Set up environment paths and conda initialization
        if needs_conda_install:
            # Set up conda paths and initialization
            setup_commands = [
                'echo "export PATH=/opt/conda/bin:$PATH" >> /root/.bashrc',
                'echo "export CONDA_DEFAULT_ENV=base" >> /root/.bashrc',
                "/opt/conda/bin/conda init bash",
                'echo "conda activate code_evaluate" >> /root/.bashrc',
            ]
        else:
            # For miniconda base image, just set up activation
            setup_commands = [
                'echo "export PATH=/opt/conda/bin:$PATH" >> /root/.bashrc',
                "conda init bash",
                'echo "conda activate code_evaluate" >> /root/.bashrc',
            ]

        for cmd in setup_commands:
            exec_result = container.exec_run(cmd, workdir="/")
            setup_results.append(
                {
                    "step": f"setup_env_{len(setup_results)}",
                    "command": cmd,
                    "exit_code": exec_result.exit_code,
                    "output": exec_result.output.decode()[:500],
                }
            )

        # Step 7: Test final environment setup
        final_test_cmd = "bash -c 'source /root/.bashrc && conda activate code_evaluate && python --version && which python'"
        final_test_result = container.exec_run(final_test_cmd, workdir="/")
        setup_results.append(
            {
                "step": "final_test",
                "command": "source .bashrc && conda activate code_evaluate && python --version",
                "exit_code": final_test_result.exit_code,
                "output": final_test_result.output.decode()[:500],
            }
        )

        # Check success
        failed_steps = [step for step in setup_results if step["exit_code"] != 0]
        success = len(failed_steps) == 0

        result = {
            "status": "success" if success else "partial_failure",
            "container_id": container_id,
            "env_name": env_name,
            "python_version": python_version,
            "setup_steps": setup_results,
            "failed_steps": len(failed_steps),
            "conda_path": "/opt/conda/bin/conda",
            "env_python_path": f"/opt/conda/envs/{env_name}/bin/python",
        }

        if failed_steps:
            result["message"] = (
                f"Conda environment setup had {len(failed_steps)} failed steps"
            )
        else:
            result["message"] = (
                f"Conda environment '{env_name}' created successfully with Python {python_version}"
            )

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Failed to setup conda environment: {e}")
        return json.dumps(
            {
                "status": "error",
                "message": f"Conda environment setup failed: {str(e)}",
                "container_id": container_id,
            },
            indent=2,
            ensure_ascii=False,
        )


@mcp.tool()
async def setup_container_workspace(
    container_id: str,
    repo_path: str,
    docs_path: str = None,
    workspace_path: str = "/root",
) -> str:
    """
    Setup workspace in container by mounting repository and documentation.

    Args:
        container_id: Container ID or name
        repo_path: Local path to repository
        docs_path: Local path to documentation (optional)
        workspace_path: Container workspace path

    Returns:
        JSON string with workspace setup result
    """
    try:
        client = get_docker_client()

        # Get container
        container = client.containers.get(container_id)

        # Verify paths exist
        if not os.path.exists(repo_path):
            return json.dumps(
                {
                    "status": "error",
                    "message": f"Repository path does not exist: {repo_path}",
                }
            )

        repo_path = os.path.abspath(repo_path)

        # Create workspace directory in container
        exec_result = container.exec_run(f"mkdir -p {workspace_path}")
        if exec_result.exit_code != 0:
            return json.dumps(
                {
                    "status": "error",
                    "message": f"Failed to create workspace directory: {exec_result.output.decode()}",
                }
            )

        # Copy repository to container - SUPER SIMPLE with docker cp
        repo_name = os.path.basename(repo_path)  # e.g., "generate_code"
        target_path = (
            f"{workspace_path}/{repo_name}"  # e.g., "/root/workbase/generate_code"
        )

        logger.info(f"📁 Copying repository: {repo_path} -> {target_path}")

        # Use docker cp - much simpler and more reliable than tar
        try:
            # Get container name/id
            container_name = container.name or container.id

            # Use docker cp command
            cp_cmd = ["docker", "cp", repo_path, f"{container_name}:{target_path}"]
            logger.info(f"📋 Executing: {' '.join(cp_cmd)}")

            result = subprocess.run(cp_cmd, capture_output=True, text=True, check=True)
            logger.info("✅ Repository copied successfully using docker cp")

        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Docker cp failed: {e.stderr}")
            raise Exception(f"Repository copy failed: {e.stderr}")

        # Verify the copy worked
        verify_cmd = f"ls -la {target_path}"
        verify_result = container.exec_run(verify_cmd)
        if verify_result.exit_code == 0:
            logger.info("✅ Verification successful")
            logger.info(f"📋 Directory contents:\n{verify_result.output.decode()}")
        else:
            logger.error(f"❌ Verification failed: {verify_result.output.decode()}")
            raise Exception("Repository verification failed")

        # Find requirements.txt in the copied repository
        find_cmd = f"find {target_path} -name 'requirements.txt' -type f"
        find_result = container.exec_run(find_cmd)
        if find_result.exit_code == 0 and find_result.output.strip():
            found_files = find_result.output.decode().strip().split("\n")
            logger.info(f"📦 Found requirements.txt files: {found_files}")
        else:
            logger.warning(f"⚠️ No requirements.txt found in {target_path}")

        # Copy documentation if provided
        docs_copied = False
        if docs_path and os.path.exists(docs_path):
            try:
                docs_path = os.path.abspath(docs_path)

                if os.path.isfile(docs_path):
                    # Copy single file
                    with open(docs_path, "rb") as f:
                        container.put_archive(f"{workspace_path}/docs", f.read())
                    docs_copied = True
                elif os.path.isdir(docs_path):
                    # Copy directory
                    with tempfile.NamedTemporaryFile(suffix=".tar") as tmp_tar:
                        subprocess.run(
                            [
                                "tar",
                                "-cf",
                                tmp_tar.name,
                                "-C",
                                os.path.dirname(docs_path),
                                os.path.basename(docs_path),
                            ],
                            check=True,
                        )

                        with open(tmp_tar.name, "rb") as tar_file:
                            container.put_archive(f"{workspace_path}/docs", tar_file)
                    docs_copied = True

            except Exception as e:
                logger.warning(f"Failed to copy documentation: {e}")

        # Set working directory and permissions
        container.exec_run(f"chmod -R 755 {workspace_path}")
        container.exec_run(f"cd {workspace_path}")

        # List workspace contents for verification
        exec_result = container.exec_run(f"ls -la {workspace_path}")
        workspace_contents = (
            exec_result.output.decode()
            if exec_result.exit_code == 0
            else "Failed to list contents"
        )

        result = {
            "status": "success",
            "container_id": container_id,
            "workspace_setup": {
                "workspace_path": workspace_path,
                "repo_copied": True,
                "docs_copied": docs_copied,
                "repo_source": repo_path,
                "docs_source": docs_path if docs_copied else None,
            },
            "workspace_contents": workspace_contents,
            "message": "Workspace setup completed successfully",
        }

        logger.info(f"Workspace setup completed for container {container_id[:12]}")
        return json.dumps(result, indent=2, ensure_ascii=False)

    except DockerException as e:
        logger.error(f"Docker error setting up workspace: {e}")
        return json.dumps({"status": "error", "message": f"Docker error: {str(e)}"})
    except Exception as e:
        logger.error(f"Failed to setup workspace: {e}")
        return json.dumps(
            {"status": "error", "message": f"Workspace setup failed: {str(e)}"}
        )


@mcp.tool()
async def install_dependencies(
    container_id: str, workspace_path: str = "/root/workbase", language: str = "python"
) -> str:
    """
    Install project dependencies in the container by intelligently discovering requirements files.

    This tool:
    1. Automatically finds the project directory within the workspace_path
    2. Discovers requirements.txt files in the project
    3. Installs dependencies using the appropriate conda environment
    4. Waits for installation to complete before returning

    Args:
        container_id: Container ID or name
        workspace_path: Base workspace path where repositories are copied (e.g., /root/workbase)
        language: Programming language (python, node, java)

    Returns:
        JSON string with installation result
    """
    try:
        client = get_docker_client()
        container = client.containers.get(container_id)

        logger.info(
            f"🚀 Starting dependency installation in container {container_id[:12]}..."
        )
        logger.info(f"📁 Workspace path: {workspace_path}")

        installation_results = []

        # Step 1: Discover project directory within workspace
        logger.info("🔍 Step 1: Discovering project directory...")

        # List contents of workspace to find the project directory
        ls_cmd = f"ls -la {workspace_path}"
        ls_result = container.exec_run(ls_cmd, workdir="/")

        if ls_result.exit_code != 0:
            raise Exception(
                f"Failed to list workspace directory: {ls_result.output.decode()}"
            )

        logger.info(f"📋 Workspace contents:\n{ls_result.output.decode()}")

        # Find project directories (exclude . and ..)
        find_dirs_cmd = f"find {workspace_path} -maxdepth 1 -type d -not -name '.*' -not -path '{workspace_path}'"
        find_result = container.exec_run(find_dirs_cmd, workdir="/")

        project_dirs = []
        if find_result.exit_code == 0 and find_result.output.strip():
            project_dirs = [
                d.strip()
                for d in find_result.output.decode().strip().split("\n")
                if d.strip()
            ]

        logger.info(f"📂 Found project directories: {project_dirs}")

        if not project_dirs:
            raise Exception(f"No project directories found in {workspace_path}")

        # Use the first project directory found
        project_dir = project_dirs[0]
        logger.info(f"✅ Using project directory: {project_dir}")

        # Step 2: Find requirements.txt files in the project
        logger.info("🔍 Step 2: Discovering requirements.txt files...")

        find_req_cmd = f"find {project_dir} -name 'requirements.txt' -type f"
        req_result = container.exec_run(find_req_cmd, workdir="/")

        requirements_files = []
        if req_result.exit_code == 0 and req_result.output.strip():
            requirements_files = [
                f.strip()
                for f in req_result.output.decode().strip().split("\n")
                if f.strip()
            ]

        logger.info(f"📦 Found requirements.txt files: {requirements_files}")

        if not requirements_files:
            logger.warning(f"⚠️ No requirements.txt files found in {project_dir}")
            return json.dumps(
                {
                    "status": "success",
                    "container_id": container_id,
                    "message": "No requirements.txt files found, skipping dependency installation",
                    "project_directory": project_dir,
                    "installation_results": [],
                }
            )

        # Step 3: Setup Python environment
        if language == "python":
            logger.info("🐍 Step 3: Setting up Python environment...")

            # Check for conda and grader environment
            conda_check_cmd = "which conda"
            conda_result = container.exec_run(conda_check_cmd, workdir="/")

            if conda_result.exit_code == 0:
                # Check if code_evaluate environment exists (use bash for pipe command)
                env_check_cmd = "bash -c 'conda env list | grep code_evaluate'"
                env_result = container.exec_run(env_check_cmd, workdir="/")

                if env_result.exit_code == 0:
                    logger.info("✅ Using conda environment 'code_evaluate'")
                    pip_cmd = "bash -c 'source /opt/conda/etc/profile.d/conda.sh && conda activate code_evaluate && pip"
                    env_test_cmd = "bash -c 'source /opt/conda/etc/profile.d/conda.sh && conda activate code_evaluate && python --version'"
                else:
                    logger.info("✅ Using conda base environment")
                    pip_cmd = "bash -c 'source /opt/conda/etc/profile.d/conda.sh && conda activate base && pip"
                    env_test_cmd = "bash -c 'source /opt/conda/etc/profile.d/conda.sh && conda activate base && python --version'"

                # Test environment
                test_result = container.exec_run(env_test_cmd, workdir="/")
                installation_results.append(
                    {
                        "command": "Test Python environment",
                        "exit_code": test_result.exit_code,
                        "output": test_result.output.decode()
                        if test_result.output
                        else "",
                    }
                )

                if test_result.exit_code != 0:
                    raise Exception(
                        f"Failed to activate conda environment: {test_result.output.decode()}"
                    )

                # Install build tools if not present (for compiling packages like numpy)
                logger.info("🔧 Checking and installing build tools...")
                build_check_cmd = "which gcc"
                build_check_result = container.exec_run(build_check_cmd, workdir="/")

                if build_check_result.exit_code != 0:
                    logger.info(
                        "📦 Installing build-essential for package compilation..."
                    )
                    install_build_cmd = (
                        "apt-get update && apt-get install -y build-essential"
                    )
                    build_install_result = container.exec_run(
                        install_build_cmd, workdir="/"
                    )
                    installation_results.append(
                        {
                            "command": "Install build tools",
                            "exit_code": build_install_result.exit_code,
                            "output": build_install_result.output.decode(
                                "utf-8", errors="ignore"
                            )[:500]
                            if build_install_result.output
                            else "",
                        }
                    )

                    if build_install_result.exit_code == 0:
                        logger.info("✅ Build tools installed successfully")
                    else:
                        logger.warning(
                            "⚠️ Failed to install build tools, some packages may fail to compile"
                        )
                else:
                    logger.info("✅ Build tools already available")

            else:
                logger.info("✅ Using system Python")
                pip_cmd = "pip"

                # Test system python
                test_result = container.exec_run("python --version", workdir="/")
                installation_results.append(
                    {
                        "command": "Test Python environment",
                        "exit_code": test_result.exit_code,
                        "output": test_result.output.decode()
                        if test_result.output
                        else "",
                    }
                )

        # Step 4: Install dependencies from each requirements.txt
        logger.info("📦 Step 4: Installing dependencies...")

        for req_file in requirements_files:
            logger.info(f"🚀 Installing from: {req_file}")

            # Get the directory containing the requirements.txt for working directory
            req_dir = (
                os.path.dirname(req_file) if os.path.dirname(req_file) else project_dir
            )

            if language == "python":
                # Build pip install command with optimizations
                pip_options = "--timeout 1000 --retries 3 --disable-pip-version-check --prefer-binary --no-cache-dir"

                if "bash -c" in pip_cmd:
                    install_cmd = f"{pip_cmd} install {pip_options} -r {req_file}'"
                else:
                    install_cmd = f"{pip_cmd} install {pip_options} -r {req_file}"

                logger.info(f"🔧 Executing: {install_cmd}")
                logger.info(f"📁 Working directory: {req_dir}")

                # Execute installation and wait for completion
                exec_result = container.exec_run(install_cmd, workdir=req_dir)

                # Log output for visibility
                output = (
                    exec_result.output.decode("utf-8", errors="ignore")
                    if exec_result.output
                    else ""
                )
                if output:
                    logger.info(f"📋 Installation output:\n{output}")

                installation_results.append(
                    {
                        "command": install_cmd,
                        "requirements_file": req_file,
                        "working_directory": req_dir,
                        "exit_code": exec_result.exit_code,
                        "output": output,
                    }
                )

                if exec_result.exit_code == 0:
                    logger.info(
                        f"✅ Successfully installed dependencies from {req_file}"
                    )
                else:
                    logger.error(
                        f"❌ Failed to install dependencies from {req_file} (exit code: {exec_result.exit_code})"
                    )

            elif language == "node":
                # Handle Node.js package.json
                if "package.json" in req_file:
                    install_cmd = "npm install"
                    exec_result = container.exec_run(install_cmd, workdir=req_dir)
                    installation_results.append(
                        {
                            "command": install_cmd,
                            "requirements_file": req_file,
                            "working_directory": req_dir,
                            "exit_code": exec_result.exit_code,
                            "output": exec_result.output.decode()
                            if exec_result.output
                            else "",
                        }
                    )

        # Calculate final results
        failed_installs = [
            r for r in installation_results if r.get("exit_code", 0) != 0
        ]
        success_count = len(installation_results) - len(failed_installs)

        # Final status
        status = "success" if len(failed_installs) == 0 else "partial_failure"

        result = {
            "status": status,
            "container_id": container_id,
            "language": language,
            "project_directory": project_dir,
            "requirements_files": requirements_files,
            "installation_results": installation_results,
            "successful_installations": success_count,
            "failed_installations": len(failed_installs),
            "total_installations": len(installation_results),
            "message": f"Dependencies installation completed. {success_count}/{len(installation_results)} successful.",
        }

        logger.info(
            f"✅ Dependencies installation completed for container {container_id[:12]}"
        )
        logger.info(
            f"📊 Results: {success_count}/{len(installation_results)} successful"
        )

        return json.dumps(result, indent=2, ensure_ascii=False)

    except DockerException as e:
        logger.error(f"Docker error: {e}")
        return json.dumps({"status": "error", "message": f"Docker error: {str(e)}"})
    except Exception as e:
        logger.error(f"Installation failed: {e}")
        return json.dumps(
            {
                "status": "error",
                "message": f"Dependencies installation failed: {str(e)}",
            }
        )


@mcp.tool()
async def execute_in_container(
    container_id: str,
    command: str,
    working_dir: str = "/workspace/repo",
    timeout: int = 300,
    capture_output: bool = True,
) -> str:
    """
    Execute a command in the container and capture output.

    Args:
        container_id: Container ID or name
        command: Command to execute
        working_dir: Working directory for command execution
        timeout: Command timeout in seconds
        capture_output: Whether to capture and return output

    Returns:
        JSON string with execution result
    """
    try:
        client = get_docker_client()
        container = client.containers.get(container_id)

        logger.info(f"Executing command in container {container_id[:12]}: {command}")

        start_time = time.time()

        # Execute command (Docker SDK doesn't support timeout parameter in exec_run)
        exec_result = container.exec_run(
            command, workdir=working_dir, stdout=capture_output, stderr=capture_output
        )

        execution_time = time.time() - start_time

        # Decode output
        stdout = (
            exec_result.output.decode("utf-8", errors="replace")
            if exec_result.output
            else ""
        )
        stderr = ""

        # For some commands, stderr might be mixed with stdout
        # Try to separate if possible
        if exec_result.exit_code != 0 and stdout:
            lines = stdout.split("\n")
            stderr_lines = [
                line
                for line in lines
                if any(
                    keyword in line.lower()
                    for keyword in ["error", "exception", "traceback", "failed"]
                )
            ]
            stderr = "\n".join(stderr_lines)

        execution_result = ExecutionResult(
            command=command,
            exit_code=exec_result.exit_code,
            stdout=stdout,
            stderr=stderr,
            execution_time=round(execution_time, 2),
            success=exec_result.exit_code == 0,
        )

        result = {
            "status": "success",
            "container_id": container_id,
            "execution": asdict(execution_result),
            "message": "Command executed successfully"
            if execution_result.success
            else "Command execution failed",
        }

        logger.info(
            f"Command execution completed: exit_code={exec_result.exit_code}, time={execution_time:.2f}s"
        )
        return json.dumps(result, indent=2, ensure_ascii=False)

    except DockerException as e:
        logger.error(f"Docker error executing command: {e}")
        return json.dumps({"status": "error", "message": f"Docker error: {str(e)}"})
    except Exception as e:
        logger.error(f"Failed to execute command: {e}")
        return json.dumps(
            {"status": "error", "message": f"Command execution failed: {str(e)}"}
        )


@mcp.tool()
async def get_container_logs(container_id: str, tail_lines: int = 100) -> str:
    """
    Retrieve container logs and execution history.

    Args:
        container_id: Container ID or name
        tail_lines: Number of recent log lines to retrieve

    Returns:
        JSON string with container logs
    """
    try:
        client = get_docker_client()
        container = client.containers.get(container_id)

        # Get container logs
        logs = container.logs(tail=tail_lines, timestamps=True).decode(
            "utf-8", errors="replace"
        )

        # Get container info
        container_info = container.attrs

        result = {
            "status": "success",
            "container_id": container_id,
            "logs": logs,
            "container_status": container_info.get("State", {}).get(
                "Status", "unknown"
            ),
            "log_lines_retrieved": len(logs.split("\n")),
            "message": "Container logs retrieved successfully",
        }

        return json.dumps(result, indent=2, ensure_ascii=False)

    except DockerException as e:
        logger.error(f"Docker error retrieving logs: {e}")
        return json.dumps({"status": "error", "message": f"Docker error: {str(e)}"})
    except Exception as e:
        logger.error(f"Failed to retrieve logs: {e}")
        return json.dumps(
            {"status": "error", "message": f"Log retrieval failed: {str(e)}"}
        )


@mcp.tool()
async def monitor_container_resources(container_id: str) -> str:
    """
    Monitor container resource usage (CPU, memory, I/O).

    Args:
        container_id: Container ID or name

    Returns:
        JSON string with resource usage statistics
    """
    try:
        client = get_docker_client()
        container = client.containers.get(container_id)

        # Get resource statistics
        stats = container.stats(stream=False)
        resource_usage = parse_resource_stats(stats)

        # Get container status
        container.reload()
        status = container.status

        result = {
            "status": "success",
            "container_id": container_id,
            "container_status": status,
            "resource_usage": asdict(resource_usage),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "message": "Resource monitoring completed",
        }

        return json.dumps(result, indent=2, ensure_ascii=False)

    except DockerException as e:
        logger.error(f"Docker error monitoring resources: {e}")
        return json.dumps({"status": "error", "message": f"Docker error: {str(e)}"})
    except Exception as e:
        logger.error(f"Failed to monitor resources: {e}")
        return json.dumps(
            {"status": "error", "message": f"Resource monitoring failed: {str(e)}"}
        )


@mcp.tool()
async def cleanup_container(container_id: str, remove_volumes: bool = True) -> str:
    """
    Clean up and remove container and associated resources.

    Args:
        container_id: Container ID or name
        remove_volumes: Whether to remove associated volumes

    Returns:
        JSON string with cleanup result
    """
    try:
        client = get_docker_client()
        container = client.containers.get(container_id)

        # Get container info before removal
        container_name = container.name
        container_image = container.image.tags[0] if container.image.tags else "unknown"

        # Stop container if running
        if container.status == "running":
            logger.info(f"Stopping container {container_id[:12]}")
            container.stop(timeout=10)

        # Remove container
        logger.info(f"Removing container {container_id[:12]}")
        container.remove(v=remove_volumes)

        result = {
            "status": "success",
            "container_id": container_id,
            "cleanup_info": {
                "container_name": container_name,
                "container_image": container_image,
                "volumes_removed": remove_volumes,
                "cleanup_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            "message": f"Container '{container_name}' cleaned up successfully",
        }

        logger.info(f"Container cleanup completed: {container_name}")
        return json.dumps(result, indent=2, ensure_ascii=False)

    except DockerException as e:
        logger.error(f"Docker error during cleanup: {e}")
        return json.dumps({"status": "error", "message": f"Docker error: {str(e)}"})
    except Exception as e:
        logger.error(f"Failed to cleanup container: {e}")
        return json.dumps(
            {"status": "error", "message": f"Container cleanup failed: {str(e)}"}
        )


@mcp.tool()
async def list_evaluation_containers() -> str:
    """
    List all evaluation containers (created by this system).

    Returns:
        JSON string with list of evaluation containers
    """
    try:
        client = get_docker_client()

        # Get all containers with our prefix
        containers = client.containers.list(
            all=True, filters={"name": CONTAINER_PREFIX}
        )

        container_list = []
        for container in containers:
            try:
                container_info = ContainerInfo(
                    container_id=container.id,
                    name=container.name,
                    image=container.image.tags[0]
                    if container.image.tags
                    else "unknown",
                    status=container.status,
                    created_at=container.attrs.get("Created", "unknown"),
                    workspace_path="/workspace",
                )
                container_list.append(asdict(container_info))
            except Exception as e:
                logger.warning(
                    f"Error getting info for container {container.name}: {e}"
                )

        result = {
            "status": "success",
            "containers": container_list,
            "total_containers": len(container_list),
            "active_containers": len(
                [c for c in container_list if c["status"] == "running"]
            ),
            "message": "Container list retrieved successfully",
        }

        return json.dumps(result, indent=2, ensure_ascii=False)

    except DockerException as e:
        logger.error(f"Docker error listing containers: {e}")
        return json.dumps({"status": "error", "message": f"Docker error: {str(e)}"})
    except Exception as e:
        logger.error(f"Failed to list containers: {e}")
        return json.dumps(
            {"status": "error", "message": f"Container listing failed: {str(e)}"}
        )


# Helper functions for enhanced dependency installation


async def _discover_requirements_in_container(
    container, working_directory: str, requirements_file: str
) -> Optional[str]:
    """
    Intelligently discover requirements.txt file in the container's working directory.

    Args:
        container: Docker container object
        working_directory: Base working directory (e.g., /root/workbase)
        requirements_file: Provided requirements file path

    Returns:
        Absolute path to the requirements file if found, None otherwise
    """
    logger.info(f"🔍 Searching for requirements file in container: {working_directory}")

    try:
        # First, let's see what's actually in the working directory
        ls_cmd = f"ls -la {working_directory}"
        ls_result = container.exec_run(ls_cmd, workdir=working_directory)
        logger.info(f"📋 Directory contents: {ls_result.output.decode().strip()}")

        # Also check if the directory exists
        test_dir_cmd = f"test -d {working_directory} && echo 'Directory exists' || echo 'Directory not found'"
        test_result = container.exec_run(test_dir_cmd, workdir="/")
        logger.info(f"📋 Directory test: {test_result.output.decode().strip()}")

        # Simple and direct search in the repository directory
        logger.info(
            f"🔍 Searching for requirements.txt in repository: {working_directory}"
        )

        # Find requirements.txt in the working directory (which contains the copied repo)
        find_cmd = f"find {working_directory} -name 'requirements.txt' -type f"
        result = container.exec_run(find_cmd, workdir="/")

        logger.info(f"📋 Find command: {find_cmd}")
        logger.info(f"📋 Find exit code: {result.exit_code}")

        if result.exit_code == 0 and result.output.strip():
            found_files = result.output.decode().strip().split("\n")
            logger.info(f"📋 Found requirements.txt files: {found_files}")

            # Return the first valid requirements.txt file
            for file_path in found_files:
                file_path = file_path.strip()
                if file_path:
                    # Test if file is readable
                    test_cmd = f"test -r {file_path}"
                    test_result = container.exec_run(test_cmd, workdir="/")
                    if test_result.exit_code == 0:
                        logger.info(f"✅ Found readable requirements file: {file_path}")
                        return file_path
                    else:
                        logger.warning(f"⚠️ File not readable: {file_path}")
        else:
            logger.warning(f"⚠️ No requirements.txt found in {working_directory}")
            if result.output:
                logger.warning(f"⚠️ Find output: {result.output.decode()}")

    except Exception as e:
        logger.error(f"❌ Error searching for requirements file: {e}")

    logger.warning(
        f"⚠️ No requirements.txt file found in {working_directory} or {working_directory}/repo"
    )
    return None


async def _execute_with_realtime_output(
    container, command: str, working_directory: str, operation_name: str
) -> Dict[str, Any]:
    """
    Execute command in container with real-time output streaming and robust error handling.

    Args:
        container: Docker container object
        command: Command to execute
        working_directory: Working directory for execution
        operation_name: Human-readable operation name for logging

    Returns:
        Dict with exit_code and accumulated output
    """
    logger.info(f"🚀 {operation_name}: Executing command with real-time output")
    logger.info(f"📋 Command: {command}")
    logger.info(f"📁 Working Directory: {working_directory}")
    logger.info("=" * 80)

    try:
        # For pip installation, use fallback to non-streaming execution to avoid interruption
        if "pip install" in command:
            logger.info(
                f"📦 {operation_name}: Using non-streaming execution for pip install to avoid interruption"
            )
            exec_result = container.exec_run(command, workdir=working_directory)
            output = (
                exec_result.output.decode("utf-8", errors="ignore")
                if exec_result.output
                else ""
            )

            # Log the output for visibility
            if output:
                logger.info(f"📦 {operation_name} - Output:")
                logger.info("-" * 60)
                for line in output.split("\n"):
                    if line.strip():
                        logger.info(f"💬 {line}")
                logger.info("-" * 60)

            logger.info(
                f"✅ {operation_name} completed with exit code: {exec_result.exit_code}"
            )
            logger.info("=" * 80)

            return {"exit_code": exec_result.exit_code, "output": output}

        # For non-pip commands, use streaming output with improved error handling
        exec_id = container.client.api.exec_create(
            container.id,
            command,
            workdir=working_directory,
            stdout=True,
            stderr=True,
            stream=True,
        )

        # Start execution and stream output
        output_stream = container.client.api.exec_start(exec_id, stream=True)
        accumulated_output = []

        logger.info(f"📦 {operation_name} - Real-time output:")
        logger.info("-" * 60)

        # Stream and log output in real-time with better error handling
        try:
            for chunk in output_stream:
                if chunk:
                    try:
                        output_line = chunk.decode("utf-8", errors="ignore").strip()
                        if output_line:
                            logger.info(f"💬 {output_line}")
                            accumulated_output.append(output_line)
                    except Exception as decode_error:
                        logger.warning(
                            f"⚠️ Failed to decode output chunk: {decode_error}"
                        )
                        # Continue processing other chunks
                        continue
        except Exception as stream_error:
            logger.warning(f"⚠️ Streaming interrupted: {stream_error}")
            # Still try to get the final result

        # Get final execution result
        exec_result = container.client.api.exec_inspect(exec_id)
        exit_code = exec_result.get("ExitCode", -1)

        logger.info("-" * 60)
        logger.info(f"✅ {operation_name} completed with exit code: {exit_code}")
        logger.info("=" * 80)

        return {"exit_code": exit_code, "output": "\n".join(accumulated_output)}

    except Exception as e:
        logger.error(f"❌ {operation_name} failed with error: {e}")
        logger.info(f"🔄 Attempting fallback execution for {operation_name}")

        # Fallback: try simple execution without streaming
        try:
            exec_result = container.exec_run(command, workdir=working_directory)
            output = (
                exec_result.output.decode("utf-8", errors="ignore")
                if exec_result.output
                else ""
            )

            logger.info(
                f"✅ Fallback execution completed with exit code: {exec_result.exit_code}"
            )

            return {
                "exit_code": exec_result.exit_code,
                "output": output
                + f"\n\nNote: Executed with fallback method due to streaming error: {str(e)}",
            }
        except Exception as fallback_error:
            logger.error(f"❌ Fallback execution also failed: {fallback_error}")
            return {
                "exit_code": -1,
                "output": f"ERROR: Both streaming and fallback execution failed.\nOriginal error: {str(e)}\nFallback error: {str(fallback_error)}",
            }


# Run the server
if __name__ == "__main__":
    try:
        # Test Docker connection on startup (but don't fail if unavailable)
        docker_available = get_docker_client() is not None
        if docker_available:
            logger.info("Docker Management MCP Server starting with Docker support...")
        else:
            logger.warning(
                "Docker Management MCP Server starting without Docker (Docker not available)"
            )
        mcp.run()
    except Exception as e:
        logger.error(f"Failed to start Docker Management MCP Server: {e}")
        sys.exit(1)
