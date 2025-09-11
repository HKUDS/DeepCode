#!/usr/bin/env python3
"""
Sandbox Tools MCP Server

This module provides sandbox execution tools for isolated project testing.
Contains tools for executing code in isolated environments and validation.
"""

import os
import json
import subprocess
import time
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

# Import MCP modules
from mcp.server.fastmcp import FastMCP

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastMCP server instance
mcp = FastMCP("sandbox-tools")


@dataclass
class SandboxResult:
    """Result from sandbox execution"""
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    error_traceback: Optional[str] = None
    resource_usage: Optional[Dict[str, Any]] = None


@mcp.tool()
async def execute_in_sandbox(
    repo_path: str,
    command: str,
    timeout: int = 30,
    capture_output: bool = True
) -> str:
    """
    Execute command in sandbox environment
    
    Args:
        repo_path: Repository path
        command: Command to execute
        timeout: Execution timeout in seconds
        capture_output: Whether to capture stdout/stderr
        
    Returns:
        JSON string with execution results
    """
    try:
        logger.info(f"Executing in sandbox: {command} in {repo_path}")
        
        start_time = time.time()
        
        # Execute the command in the specified directory
        result = subprocess.run(
            command,
            shell=True,
            cwd=repo_path,
            capture_output=capture_output,
            text=True,
            timeout=timeout
        )
        
        execution_time = time.time() - start_time
        
        # Extract error traceback if present
        error_traceback = None
        if result.returncode != 0 and result.stderr:
            if "Traceback" in result.stderr or "Error:" in result.stderr:
                error_traceback = result.stderr
        
        success = result.returncode == 0
        
        logger.info(f"Sandbox execution {'succeeded' if success else 'failed'} in {execution_time:.2f}s")
        
        return json.dumps({
            "status": "success",
            "sandbox_result": {
                "success": success,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
                "execution_time": execution_time,
                "command": command,
                "working_directory": repo_path,
                "error_traceback": error_traceback
            }
        }, indent=2)
        
    except subprocess.TimeoutExpired:
        logger.error(f"Sandbox execution timed out after {timeout} seconds")
        return json.dumps({
            "status": "error",
            "message": f"Command timed out after {timeout} seconds",
            "sandbox_result": {
                "success": False,
                "stdout": "",
                "stderr": f"Command timed out after {timeout} seconds",
                "exit_code": -1,
                "execution_time": timeout,
                "command": command,
                "working_directory": repo_path
            }
        }, indent=2)
        
    except Exception as e:
        logger.error(f"Sandbox execution failed: {str(e)}")
        return json.dumps({
            "status": "error",
            "message": f"Execution failed: {str(e)}",
            "sandbox_result": {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "exit_code": -1,
                "execution_time": 0.0,
                "command": command,
                "working_directory": repo_path
            }
        }, indent=2)


@mcp.tool()
async def run_code_validation(repo_path: str, test_command: Optional[str] = None) -> str:
    """
    Run code validation in sandbox environment
    
    Args:
        repo_path: Repository path
        test_command: Optional test command (defaults to common test patterns)
        
    Returns:
        JSON string with validation results
    """
    try:
        logger.info(f"Running code validation in {repo_path}")
        
        validation_results = {
            "validation_success": True,
            "test_results": {},
            "lint_results": {},
            "import_errors": [],
            "runtime_errors": [],
            "suggestions": []
        }
        
        # Auto-detect test patterns if no command provided
        if test_command is None:
            test_commands = []
            
            # Check for Python test patterns
            if os.path.exists(os.path.join(repo_path, "requirements.txt")) or any(f.endswith('.py') for f in os.listdir(repo_path) if os.path.isfile(os.path.join(repo_path, f))):
                # Python project detected
                if os.path.exists(os.path.join(repo_path, "pytest.ini")) or any("test_" in f for f in os.listdir(repo_path)):
                    test_commands.append("python -m pytest --tb=short")
                if any(f.startswith("test") and f.endswith(".py") for f in os.listdir(repo_path)):
                    test_commands.append("python -m unittest discover")
                
                # Basic import check
                test_commands.append("python -c 'import sys; print(f\"Python {sys.version} available\")'")
            
            # Check for JavaScript test patterns  
            if os.path.exists(os.path.join(repo_path, "package.json")):
                test_commands.extend(["npm test", "npm start"])
            
            # Fallback: basic file listing
            if not test_commands:
                test_commands = ["ls -la", "find . -name '*.py' -o -name '*.js' -o -name '*.ts' | head -10"]
        else:
            test_commands = [test_command]
        
        # Execute validation commands
        for cmd in test_commands[:3]:  # Limit to 3 commands to avoid excessive execution
            try:
                logger.info(f"Executing validation command: {cmd}")
                
                result = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=60  # 1 minute timeout for validation
                )
                
                if result.returncode == 0:
                    validation_results["test_results"][cmd] = {
                        "success": True,
                        "stdout": result.stdout,
                        "stderr": result.stderr
                    }
                    logger.info(f"Validation command succeeded: {cmd}")
                else:
                    validation_results["test_results"][cmd] = {
                        "success": False,
                        "stdout": result.stdout,
                        "stderr": result.stderr
                    }
                    validation_results["validation_success"] = False
                    
                    # Extract runtime errors
                    if result.stderr:
                        if "ImportError" in result.stderr or "ModuleNotFoundError" in result.stderr:
                            validation_results["import_errors"].append(result.stderr)
                        else:
                            validation_results["runtime_errors"].append(result.stderr)
                    
                    logger.warning(f"Validation command failed: {cmd}")
                    
            except subprocess.TimeoutExpired:
                validation_results["test_results"][cmd] = {
                    "success": False,
                    "stdout": "",
                    "stderr": f"Command timed out after 60 seconds"
                }
                validation_results["validation_success"] = False
                validation_results["runtime_errors"].append(f"Timeout: {cmd}")
                
            except Exception as e:
                validation_results["test_results"][cmd] = {
                    "success": False,
                    "stdout": "",
                    "stderr": str(e)
                }
                validation_results["validation_success"] = False
                validation_results["runtime_errors"].append(f"Error executing {cmd}: {str(e)}")
        
        # Generate suggestions based on results
        if validation_results["import_errors"]:
            validation_results["suggestions"].append("Install missing dependencies listed in requirements.txt or package.json")
        if validation_results["runtime_errors"]:
            validation_results["suggestions"].append("Fix runtime errors to enable proper project execution")
        if not validation_results["test_results"]:
            validation_results["suggestions"].append("No test commands could be executed - check project structure")
        
        logger.info(f"Code validation completed. Success: {validation_results['validation_success']}")
        
        return json.dumps({
            "status": "success",
            "validation_results": validation_results
        }, indent=2)
        
    except Exception as e:
        logger.error(f"Code validation failed: {str(e)}")
        return json.dumps({
            "status": "error",
            "message": f"Code validation failed: {str(e)}",
            "validation_results": {
                "validation_success": False,
                "test_results": {},
                "lint_results": {},
                "import_errors": [],
                "runtime_errors": [str(e)],
                "suggestions": ["Fix validation errors and retry"]
            }
        }, indent=2)


# Run the server
if __name__ == "__main__":
    mcp.run()
