"""
MCP工具定义配置模块 - 代码评估专用
MCP Tool Definitions Configuration Module - Code Evaluation Specific

为代码评估智能体提供专门的工具定义
Provides specialized tool definitions for code evaluation agents

支持的评估工具类型：
- 仓库结构分析工具 (Repository Structure Analysis)
- 依赖检测工具 (Dependency Detection)
- 代码质量评估工具 (Code Quality Assessment)
- 文档评估工具 (Documentation Evaluation)
- Docker环境管理工具 (Docker Environment Management)
"""

from typing import Dict, List, Any


class MCPEvaluationToolDefinitions:
    """MCP代码评估工具定义管理器"""

    @staticmethod
    def get_code_implementation_tools() -> List[Dict[str, Any]]:
        """
        获取代码实现相关的工具定义
        Get tool definitions for code implementation
        """
        return [
            MCPEvaluationToolDefinitions._get_read_file_tool(),
            MCPEvaluationToolDefinitions._get_read_multiple_files_tool(),
            MCPEvaluationToolDefinitions._get_read_code_mem_tool(),
            MCPEvaluationToolDefinitions._get_write_file_tool(),
            MCPEvaluationToolDefinitions._get_write_multiple_files_tool(),
            MCPEvaluationToolDefinitions._get_execute_bash_tool(),
            MCPEvaluationToolDefinitions._get_file_structure_tool(),
        ]

    @staticmethod
    def get_docker_management_tools() -> List[Dict[str, Any]]:
        """
        获取Docker管理相关的工具定义
        Get tool definitions for Docker management
        """
        return [
            MCPEvaluationToolDefinitions._get_create_evaluation_container_tool(),
            MCPEvaluationToolDefinitions._get_setup_container_workspace_tool(),
            MCPEvaluationToolDefinitions._get_setup_conda_environment_tool(),
            MCPEvaluationToolDefinitions._get_install_dependencies_tool(),
            MCPEvaluationToolDefinitions._get_execute_in_container_tool(),
            MCPEvaluationToolDefinitions._get_monitor_container_resources_tool(),
            MCPEvaluationToolDefinitions._get_cleanup_container_tool(),
            MCPEvaluationToolDefinitions._get_list_evaluation_containers_tool(),
            MCPEvaluationToolDefinitions._get_read_file_in_container_tool(),
            MCPEvaluationToolDefinitions._get_write_file_in_container_tool(),
            MCPEvaluationToolDefinitions._get_list_files_in_container_tool(),
            MCPEvaluationToolDefinitions._get_analyze_repo_structure_in_container_tool(),
        ]

    @staticmethod
    def get_core_evaluation_tools() -> List[Dict[str, Any]]:
        """获取核心评估工具定义"""
        return [
            MCPEvaluationToolDefinitions._get_analyze_repo_structure_tool(),
            MCPEvaluationToolDefinitions._get_detect_dependencies_tool(),
            MCPEvaluationToolDefinitions._get_assess_code_quality_tool(),
            MCPEvaluationToolDefinitions._get_evaluate_documentation_tool(),
            MCPEvaluationToolDefinitions._get_check_reproduction_readiness_tool(),
            MCPEvaluationToolDefinitions._get_generate_evaluation_summary_tool(),
        ]

    @staticmethod
    def get_error_analysis_tools() -> List[Dict[str, Any]]:
        """获取错误分析工具定义"""
        return [
            MCPEvaluationToolDefinitions._get_parse_error_traceback_tool(),
            MCPEvaluationToolDefinitions._get_analyze_import_dependencies_tool(),
            MCPEvaluationToolDefinitions._get_generate_error_analysis_report_tool(),
            MCPEvaluationToolDefinitions._get_generate_precise_code_fixes_tool(),
            MCPEvaluationToolDefinitions._get_apply_code_fixes_with_diff_tool(),
        ]

    @staticmethod
    def get_lsp_tools() -> List[Dict[str, Any]]:
        """获取LSP工具定义"""
        return [
            MCPEvaluationToolDefinitions._get_setup_lsp_servers_tool(),
            MCPEvaluationToolDefinitions._get_lsp_find_symbol_references_tool(),
            MCPEvaluationToolDefinitions._get_lsp_get_diagnostics_tool(),
            MCPEvaluationToolDefinitions._get_lsp_get_code_actions_tool(),
            MCPEvaluationToolDefinitions._get_lsp_generate_code_fixes_tool(),
            MCPEvaluationToolDefinitions._get_lsp_apply_workspace_edit_tool(),
        ]

    @staticmethod
    def get_revision_tools() -> List[Dict[str, Any]]:
        """获取修订工具定义"""
        return [
            MCPEvaluationToolDefinitions._get_detect_empty_files_tool(),
            MCPEvaluationToolDefinitions._get_detect_missing_files_tool(),
            MCPEvaluationToolDefinitions._get_generate_code_revision_report_tool(),
        ]

    @staticmethod
    def get_sandbox_tools() -> List[Dict[str, Any]]:
        """获取沙盒工具定义"""
        return [
            MCPEvaluationToolDefinitions._get_execute_in_sandbox_tool(),
            MCPEvaluationToolDefinitions._get_run_code_validation_tool(),
        ]

    @staticmethod
    def get_static_analysis_tools() -> List[Dict[str, Any]]:
        """获取静态分析工具定义"""
        return [
            MCPEvaluationToolDefinitions._get_perform_static_analysis_tool(),
            MCPEvaluationToolDefinitions._get_auto_fix_formatting_tool(),
            MCPEvaluationToolDefinitions._get_generate_static_issues_report_tool(),
        ]

    # ==================== 代码评估工具定义 ====================

    @staticmethod
    def _get_analyze_repo_structure_tool() -> Dict[str, Any]:
        """分析仓库结构工具定义"""
        return {
            "name": "analyze_repo_structure",
            "description": "Analyze repository structure, file types, organization, and detect programming languages and frameworks",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository to analyze",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum directory depth to analyze",
                        "default": 10,
                    },
                    "include_hidden": {
                        "type": "boolean",
                        "description": "Whether to include hidden files and directories",
                        "default": False,
                    },
                },
                "required": ["repo_path"],
            },
        }

    @staticmethod
    def _get_detect_dependencies_tool() -> Dict[str, Any]:
        """检测依赖工具定义"""
        return {
            "name": "detect_dependencies",
            "description": "Detect and analyze project dependencies across different languages (Python, JavaScript, etc.)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository to analyze",
                    },
                    "languages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific languages to focus on (optional, auto-detected if not provided)",
                        "default": [],
                    },
                },
                "required": ["repo_path"],
            },
        }

    @staticmethod
    def _get_assess_code_quality_tool() -> Dict[str, Any]:
        """评估代码质量工具定义"""
        return {
            "name": "assess_code_quality",
            "description": "Assess code quality including complexity, maintainability, potential issues, and test coverage",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository to analyze",
                    },
                    "include_tests": {
                        "type": "boolean",
                        "description": "Whether to include test files in analysis",
                        "default": True,
                    },
                    "complexity_threshold": {
                        "type": "integer",
                        "description": "Threshold for reporting high complexity functions",
                        "default": 10,
                    },
                },
                "required": ["repo_path"],
            },
        }

    @staticmethod
    def _get_evaluate_documentation_tool() -> Dict[str, Any]:
        """评估文档工具定义"""
        return {
            "name": "evaluate_documentation",
            "description": "Evaluate documentation completeness, quality, and reproduction readiness",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository to analyze",
                    },
                    "docs_path": {
                        "type": "string",
                        "description": "Path to specific documentation file (optional)",
                        "default": None,
                    },
                    "check_api_docs": {
                        "type": "boolean",
                        "description": "Whether to check for API documentation",
                        "default": True,
                    },
                },
                "required": ["repo_path"],
            },
        }

    @staticmethod
    def _get_check_reproduction_readiness_tool() -> Dict[str, Any]:
        """检查复现准备度工具定义"""
        return {
            "name": "check_reproduction_readiness",
            "description": "Check if repository is ready for reproduction based on documentation, dependencies, and setup instructions",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository to analyze",
                    },
                    "docs_path": {
                        "type": "string",
                        "description": "Path to reproduction documentation (optional)",
                        "default": None,
                    },
                    "check_environment": {
                        "type": "boolean",
                        "description": "Whether to check environment setup requirements",
                        "default": True,
                    },
                },
                "required": ["repo_path"],
            },
        }

    @staticmethod
    def _get_generate_evaluation_summary_tool() -> Dict[str, Any]:
        """生成评估摘要工具定义"""
        return {
            "name": "generate_evaluation_summary",
            "description": "Generate comprehensive evaluation summary from conversation analysis results (avoids duplicate tool calls)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository to analyze",
                    },
                    "docs_path": {
                        "type": "string",
                        "description": "Path to reproduction documentation (optional)",
                        "default": None,
                    },
                    "conversation_data": {
                        "oneOf": [
                            {"type": "string", "description": "JSON string containing analysis results"},
                            {"type": "object", "description": "Dictionary containing analysis results"}
                        ],
                        "description": "Optional analysis results from previous tool calls in conversation"
                    },
                    "include_recommendations": {
                        "type": "boolean",
                        "description": "Whether to include improvement recommendations",
                        "default": True,
                    },
                },
                "required": ["repo_path"],
            },
        }

    # ==================== Docker管理工具定义 ====================

    @staticmethod
    def _get_create_evaluation_container_tool() -> Dict[str, Any]:
        """创建评估容器工具定义"""
        return {
            "name": "create_evaluation_container",
            "description": "Create a Docker container for safe code evaluation",
            "input_schema": {
                "type": "object",
                "properties": {
                    "base_image": {
                        "type": "string",
                        "description": "Base Docker image to use",
                        "default": "python:3.9-slim",
                    },
                    "container_name": {
                        "type": "string",
                        "description": "Name for the container (optional, auto-generated if not provided)",
                        "default": None,
                    },
                    "memory_limit": {
                        "type": "string",
                        "description": "Memory limit for container (e.g., '512m', '1g')",
                        "default": "1g",
                    },
                },
                "required": [],
            },
        }

    @staticmethod
    def _get_setup_container_workspace_tool() -> Dict[str, Any]:
        """设置容器工作空间工具定义"""
        return {
            "name": "setup_container_workspace",
            "description": "Mount repository into container and setup workspace",
            "input_schema": {
                "type": "object",
                "properties": {
                    "container_id": {
                        "type": "string",
                        "description": "Container ID or name",
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Local repository path to mount",
                    },
                    "workspace_path": {
                        "type": "string",
                        "description": "Path inside container where repo will be mounted",
                        "default": "/workspace",
                    },
                },
                "required": ["container_id", "repo_path"],
            },
        }

    @staticmethod
    def _get_setup_conda_environment_tool() -> Dict[str, Any]:
        """Setup conda environment tool definition"""
        return {
            "name": "setup_conda_environment",
            "description": "Setup conda environment in container based on grader.Dockerfile approach",
            "input_schema": {
                "type": "object",
                "properties": {
                    "container_id": {
                        "type": "string",
                        "description": "Docker container ID",
                    },
                    "python_version": {
                        "type": "string",
                        "description": "Python version to install in conda environment",
                        "default": "3.12",
                    },
                    "env_name": {
                        "type": "string",
                        "description": "Name of conda environment to create",
                        "default": "grader",
                    },
                },
                "required": ["container_id"],
            },
        }

    @staticmethod
    def _get_install_dependencies_tool() -> Dict[str, Any]:
        """安装依赖工具定义"""
        return {
            "name": "install_dependencies",
            "description": "Install project dependencies inside container",
            "input_schema": {
                "type": "object",
                "properties": {
                    "container_id": {
                        "type": "string",
                        "description": "Container ID or name",
                    },
                    "requirements_file": {
                        "type": "string",
                        "description": "Requirements file path (e.g., requirements.txt, package.json)",
                        "default": "requirements.txt",
                    },
                    "language": {
                        "type": "string",
                        "description": "Programming language (python, nodejs, etc.)",
                        "default": "python",
                    },
                    "working_directory": {
                        "type": "string",
                        "description": "Working directory inside container for dependency installation",
                        "default": "/root/workbase",
                    },
                },
                "required": ["container_id"],
            },
        }

    @staticmethod
    def _get_execute_in_container_tool() -> Dict[str, Any]:
        """容器内执行工具定义"""
        return {
            "name": "execute_in_container",
            "description": "Execute commands inside container",
            "input_schema": {
                "type": "object",
                "properties": {
                    "container_id": {
                        "type": "string",
                        "description": "Container ID or name",
                    },
                    "command": {
                        "type": "string",
                        "description": "Command to execute",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Working directory inside container",
                        "default": "/workspace",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds",
                        "default": 60,
                    },
                },
                "required": ["container_id", "command"],
            },
        }

    @staticmethod
    def _get_monitor_container_resources_tool() -> Dict[str, Any]:
        """监控容器资源工具定义"""
        return {
            "name": "monitor_container_resources",
            "description": "Monitor container resource usage (CPU, memory, etc.)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "container_id": {
                        "type": "string",
                        "description": "Container ID or name",
                    },
                    "duration": {
                        "type": "integer",
                        "description": "Monitoring duration in seconds",
                        "default": 10,
                    },
                },
                "required": ["container_id"],
            },
        }

    @staticmethod
    def _get_cleanup_container_tool() -> Dict[str, Any]:
        """清理容器工具定义"""
        return {
            "name": "cleanup_container",
            "description": "Stop and remove container",
            "input_schema": {
                "type": "object",
                "properties": {
                    "container_id": {
                        "type": "string",
                        "description": "Container ID or name",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Force removal even if container is running",
                        "default": True,
                    },
                },
                "required": ["container_id"],
            },
        }

    @staticmethod
    def _get_list_evaluation_containers_tool() -> Dict[str, Any]:
        """列出容器工具定义"""
        return {
            "name": "list_evaluation_containers",
            "description": "List Docker containers and available images",
            "input_schema": {
                "type": "object",
                "properties": {
                    "show_all": {
                        "type": "boolean",
                        "description": "Show all containers including stopped ones",
                        "default": True,
                    },
                    "show_images": {
                        "type": "boolean",
                        "description": "Also show available Docker images",
                        "default": True,
                    },
                },
                "required": [],
            },
        }

    @staticmethod
    def _get_read_file_in_container_tool() -> Dict[str, Any]:
        """容器内文件读取工具定义"""
        return {
            "name": "read_file_in_container",
            "description": "Read a file from inside the Docker container",
            "input_schema": {
                "type": "object",
                "properties": {
                    "container_id": {
                        "type": "string",
                        "description": "Container ID or name",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to file inside container (relative to working_dir)",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Working directory in container",
                        "default": "/workspace/repo",
                    },
                },
                "required": ["container_id", "file_path"],
            },
        }

    @staticmethod
    def _get_write_file_in_container_tool() -> Dict[str, Any]:
        """容器内文件写入工具定义"""
        return {
            "name": "write_file_in_container",
            "description": "Write content to a file inside the Docker container",
            "input_schema": {
                "type": "object",
                "properties": {
                    "container_id": {
                        "type": "string",
                        "description": "Container ID or name",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to file inside container (relative to working_dir)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Working directory in container",
                        "default": "/workspace/repo",
                    },
                    "create_backup": {
                        "type": "boolean",
                        "description": "Whether to create a backup before writing",
                        "default": True,
                    },
                },
                "required": ["container_id", "file_path", "content"],
            },
        }

    @staticmethod
    def _get_list_files_in_container_tool() -> Dict[str, Any]:
        """容器内文件列表工具定义"""
        return {
            "name": "list_files_in_container",
            "description": "List files in a directory inside the Docker container",
            "input_schema": {
                "type": "object",
                "properties": {
                    "container_id": {
                        "type": "string",
                        "description": "Container ID or name",
                    },
                    "directory_path": {
                        "type": "string",
                        "description": "Directory to list",
                        "default": "/workspace/repo",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Whether to list recursively",
                        "default": False,
                    },
                    "file_extensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by file extensions (e.g., [\".py\", \".js\"])",
                        "default": None,
                    },
                },
                "required": ["container_id"],
            },
        }

    @staticmethod
    def _get_analyze_repo_structure_in_container_tool() -> Dict[str, Any]:
        """容器内代码仓库结构分析工具定义"""
        return {
            "name": "analyze_repo_structure_in_container",
            "description": "Analyze repository structure inside the container to understand how to run the code",
            "input_schema": {
                "type": "object",
                "properties": {
                    "container_id": {
                        "type": "string",
                        "description": "Container ID or name",
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Path to repository in container",
                        "default": "/workspace/repo",
                    },
                },
                "required": ["container_id"],
            },
        }

    # ==================== ERROR ANALYSIS TOOLS ====================

    @staticmethod
    def _get_parse_error_traceback_tool() -> Dict[str, Any]:
        """解析错误回溯工具定义"""
        return {
            "name": "parse_error_traceback",
            "description": "Parse error traceback to extract structured error information",
            "input_schema": {
                "type": "object",
                "properties": {
                    "traceback_text": {"type": "string", "description": "Raw traceback/error text from execution"},
                    "repo_path": {"type": "string", "description": "Repository path for context"}
                },
                "required": ["traceback_text", "repo_path"]
            }
        }

    @staticmethod
    def _get_analyze_import_dependencies_tool() -> Dict[str, Any]:
        """分析导入依赖工具定义"""
        return {
            "name": "analyze_import_dependencies",
            "description": "Analyze import dependencies and build import graph",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Repository path"},
                    "target_file": {"type": "string", "description": "Optional specific file to analyze (if None, analyzes all files)"}
                },
                "required": ["repo_path"]
            }
        }

    @staticmethod
    def _get_generate_error_analysis_report_tool() -> Dict[str, Any]:
        """生成错误分析报告工具定义"""
        return {
            "name": "generate_error_analysis_report",
            "description": "Generate comprehensive error analysis report with suspect files and remediation suggestions",
            "input_schema": {
                "type": "object",
                "properties": {
                    "traceback_text": {"type": "string", "description": "Raw error traceback from execution"},
                    "repo_path": {"type": "string", "description": "Repository path"},
                    "execution_context": {"type": "string", "description": "Optional context about what was being executed"}
                },
                "required": ["traceback_text", "repo_path"]
            }
        }

    @staticmethod
    def _get_generate_precise_code_fixes_tool() -> Dict[str, Any]:
        """生成精确代码修复工具定义"""
        return {
            "name": "generate_precise_code_fixes",
            "description": "Generate precise code fixes based on error analysis report",
            "input_schema": {
                "type": "object",
                "properties": {
                    "error_analysis_report": {"type": "string", "description": "JSON string containing error analysis results"},
                    "target_files": {"type": "array", "items": {"type": "string"}, "description": "Optional list of specific files to target"},
                    "fix_strategy": {"type": "string", "description": "Strategy for fixes (targeted, comprehensive, conservative)", "default": "targeted"}
                },
                "required": ["error_analysis_report"]
            }
        }

    @staticmethod
    def _get_apply_code_fixes_with_diff_tool() -> Dict[str, Any]:
        """应用代码修复工具定义"""
        return {
            "name": "apply_code_fixes_with_diff",
            "description": "Apply code fixes with diff generation and validation",
            "input_schema": {
                "type": "object",
                "properties": {
                    "fixes_json": {"type": "string", "description": "JSON string containing fixes from generate_precise_code_fixes"},
                    "repo_path": {"type": "string", "description": "Repository path for validation"},
                    "dry_run": {"type": "boolean", "description": "If True, only generate diffs without applying changes", "default": False}
                },
                "required": ["fixes_json", "repo_path"]
            }
        }

    # ==================== LSP TOOLS ====================

    @staticmethod
    def _get_setup_lsp_servers_tool() -> Dict[str, Any]:
        """设置LSP服务器工具定义"""
        return {
            "name": "setup_lsp_servers",
            "description": "Set up LSP servers for detected languages in repository",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Repository path"}
                },
                "required": ["repo_path"]
            }
        }

    @staticmethod
    def _get_lsp_find_symbol_references_tool() -> Dict[str, Any]:
        """LSP查找符号引用工具定义"""
        return {
            "name": "lsp_find_symbol_references",
            "description": "Find symbol references using actual LSP",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Repository path"},
                    "symbol_name": {"type": "string", "description": "Symbol name to search for"},
                    "language": {"type": "string", "description": "Programming language", "default": "python"}
                },
                "required": ["repo_path", "symbol_name"]
            }
        }

    @staticmethod
    def _get_lsp_get_diagnostics_tool() -> Dict[str, Any]:
        """LSP获取诊断信息工具定义"""
        return {
            "name": "lsp_get_diagnostics",
            "description": "Get LSP diagnostics for files",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Repository path"},
                    "file_path": {"type": "string", "description": "Optional specific file path (if None, gets diagnostics for all open files)"}
                },
                "required": ["repo_path"]
            }
        }

    @staticmethod
    def _get_lsp_get_code_actions_tool() -> Dict[str, Any]:
        """LSP获取代码操作工具定义"""
        return {
            "name": "lsp_get_code_actions",
            "description": "Get LSP code actions for a range in a file",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Repository path"},
                    "file_path": {"type": "string", "description": "File path"},
                    "start_line": {"type": "integer", "description": "Start line number (0-based)"},
                    "end_line": {"type": "integer", "description": "End line number (0-based)"}
                },
                "required": ["repo_path", "file_path", "start_line", "end_line"]
            }
        }

    @staticmethod
    def _get_lsp_generate_code_fixes_tool() -> Dict[str, Any]:
        """LSP生成代码修复工具定义"""
        return {
            "name": "lsp_generate_code_fixes",
            "description": "Generate LSP-based code fixes for a range in a file",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Repository path"},
                    "file_path": {"type": "string", "description": "File path to fix"},
                    "start_line": {"type": "integer", "description": "Start line number (0-based)"},
                    "end_line": {"type": "integer", "description": "End line number (0-based)"},
                    "error_context": {"type": "string", "description": "Optional error context to help generate fixes"}
                },
                "required": ["repo_path", "file_path", "start_line", "end_line"]
            }
        }

    @staticmethod
    def _get_lsp_apply_workspace_edit_tool() -> Dict[str, Any]:
        """LSP应用工作空间编辑工具定义"""
        return {
            "name": "lsp_apply_workspace_edit",
            "description": "Apply LSP workspace edit to files",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Repository path"},
                    "workspace_edit": {"type": "string", "description": "JSON string containing LSP WorkspaceEdit"}
                },
                "required": ["repo_path", "workspace_edit"]
            }
        }

    # ==================== REVISION TOOLS ====================

    @staticmethod
    def _get_detect_empty_files_tool() -> Dict[str, Any]:
        """检测空文件工具定义"""
        return {
            "name": "detect_empty_files",
            "description": "Detect empty files in the repository that may need implementation",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to the repository to analyze"}
                },
                "required": ["repo_path"]
            }
        }

    @staticmethod
    def _get_detect_missing_files_tool() -> Dict[str, Any]:
        """检测缺失文件工具定义"""
        return {
            "name": "detect_missing_files",
            "description": "Detect missing essential files based on project type and existing structure",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to the repository to analyze"}
                },
                "required": ["repo_path"]
            }
        }

    @staticmethod
    def _get_generate_code_revision_report_tool() -> Dict[str, Any]:
        """生成代码修订报告工具定义"""
        return {
            "name": "generate_code_revision_report",
            "description": "Generate comprehensive code revision report from conversation analysis results (avoids duplicate tool calls)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to the repository to analyze"},
                    "docs_path": {"type": "string", "description": "Optional path to documentation"},
                    "conversation_data": {
                        "oneOf": [
                            {"type": "string", "description": "JSON string containing analysis results"},
                            {"type": "object", "description": "Dictionary containing analysis results"}
                        ],
                        "description": "Optional analysis results from previous tool calls in conversation"
                    }
                },
                "required": ["repo_path"]
            }
        }

    # ==================== SANDBOX TOOLS ====================

    @staticmethod
    def _get_execute_in_sandbox_tool() -> Dict[str, Any]:
        """沙盒执行工具定义"""
        return {
            "name": "execute_in_sandbox",
            "description": "Execute command in sandbox environment",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Repository path"},
                    "command": {"type": "string", "description": "Command to execute"},
                    "timeout": {"type": "integer", "description": "Execution timeout in seconds", "default": 30},
                    "capture_output": {"type": "boolean", "description": "Whether to capture stdout/stderr", "default": True}
                },
                "required": ["repo_path", "command"]
            }
        }

    @staticmethod
    def _get_run_code_validation_tool() -> Dict[str, Any]:
        """运行代码验证工具定义"""
        return {
            "name": "run_code_validation",
            "description": "Run code validation in sandbox environment",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Repository path"},
                    "test_command": {"type": "string", "description": "Optional test command (defaults to common test patterns)"}
                },
                "required": ["repo_path"]
            }
        }

    # ==================== STATIC ANALYSIS TOOLS ====================

    @staticmethod
    def _get_perform_static_analysis_tool() -> Dict[str, Any]:
        """执行静态分析工具定义"""
        return {
            "name": "perform_static_analysis",
            "description": "Perform comprehensive static analysis on repository with automatic fixes",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to the repository to analyze"},
                    "auto_fix": {"type": "boolean", "description": "Whether to automatically apply formatting fixes", "default": True},
                    "languages": {"type": "array", "items": {"type": "string"}, "description": "Optional list of languages to analyze (if None, auto-detect all)"}
                },
                "required": ["repo_path"]
            }
        }

    @staticmethod
    def _get_auto_fix_formatting_tool() -> Dict[str, Any]:
        """自动修复格式工具定义"""
        return {
            "name": "auto_fix_formatting",
            "description": "Automatically fix formatting issues in repository files",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to the repository"},
                    "languages": {"type": "array", "items": {"type": "string"}, "description": "Optional list of languages to format (if None, auto-detect all)"},
                    "dry_run": {"type": "boolean", "description": "If True, only report what would be fixed without making changes", "default": False}
                },
                "required": ["repo_path"]
            }
        }

    @staticmethod
    def _get_generate_static_issues_report_tool() -> Dict[str, Any]:
        """生成静态分析问题报告工具定义"""
        return {
            "name": "generate_static_issues_report",
            "description": "Generate structured JSON report of static analysis issues",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to the repository"},
                    "severity_filter": {"type": "string", "description": "Optional filter by severity (error, warning, info)"},
                    "language_filter": {"type": "string", "description": "Optional filter by programming language"}
                },
                "required": ["repo_path"]
            }
        }

    # ==================== CODE IMPLEMENTATION TOOLS ====================

    @staticmethod
    def _get_read_file_tool() -> Dict[str, Any]:
        """读取文件工具定义"""
        return {
            "name": "read_file",
            "description": "Read file content, supports specifying line number range",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "File path, relative to workspace",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Start line number (starting from 1, optional)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "End line number (starting from 1, optional)",
                    },
                },
                "required": ["file_path"],
            },
        }

    @staticmethod
    def _get_read_multiple_files_tool() -> Dict[str, Any]:
        """批量读取多个文件工具定义"""
        return {
            "name": "read_multiple_files",
            "description": "Read multiple files in a single operation (for batch reading)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_requests": {
                        "type": "string",
                        "description": "JSON string with file requests, e.g., '{\"file1.py\": {}, \"file2.py\": {\"start_line\": 1, \"end_line\": 10}}' or simple array '[\"file1.py\", \"file2.py\"]'",
                    },
                    "max_files": {
                        "type": "integer",
                        "description": "Maximum number of files to read in one operation",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["file_requests"],
            },
        }

    @staticmethod
    def _get_read_code_mem_tool() -> Dict[str, Any]:
        """Read code memory tool definition - reads from implement_code_summary.md"""
        return {
            "name": "read_code_mem",
            "description": "Check if file summaries exist in implement_code_summary.md for multiple files in a single call. Returns summaries for all requested files if available.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file paths to check for summary information in implement_code_summary.md",
                    }
                },
                "required": ["file_paths"],
            },
        }

    @staticmethod
    def _get_write_file_tool() -> Dict[str, Any]:
        """写入文件工具定义"""
        return {
            "name": "write_file",
            "description": "Write content to file",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "File path, relative to workspace",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to file",
                    },
                    "create_dirs": {
                        "type": "boolean",
                        "description": "Whether to create directories if they don't exist",
                        "default": True,
                    },
                    "create_backup": {
                        "type": "boolean",
                        "description": "Whether to create backup file if file already exists",
                        "default": False,
                    },
                },
                "required": ["file_path", "content"],
            },
        }

    @staticmethod
    def _get_write_multiple_files_tool() -> Dict[str, Any]:
        """批量写入多个文件工具定义"""
        return {
            "name": "write_multiple_files",
            "description": "Write multiple files in a single operation (for batch implementation)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_implementations": {
                        "oneOf": [
                            {
                                "type": "object",
                                "description": "Dictionary mapping file paths to content",
                                "additionalProperties": {
                                    "type": "string"
                                }
                            },
                            {
                                "type": "string",
                                "description": "JSON string mapping file paths to content"
                            }
                        ],
                        "description": "Dictionary or JSON string mapping file paths to content, e.g., {\"file1.py\": \"content1\", \"file2.py\": \"content2\"} or '{\"file1.py\": \"content1\", \"file2.py\": \"content2\"}'"
                    },
                    "create_dirs": {
                        "type": "boolean",
                        "description": "Whether to create directories if they don't exist",
                        "default": True,
                    },
                    "create_backup": {
                        "type": "boolean",
                        "description": "Whether to create backup files if they already exist",
                        "default": False,
                    },
                    "max_files": {
                        "type": "integer",
                        "description": "Maximum number of files to write in one operation",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["file_implementations"],
            },
        }

    @staticmethod
    def _get_execute_python_tool() -> Dict[str, Any]:
        """Python执行工具定义"""
        return {
            "name": "execute_python",
            "description": "Execute Python code and return output",
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds",
                        "default": 30,
                    },
                },
                "required": ["code"],
            },
        }

    @staticmethod
    def _get_execute_bash_tool() -> Dict[str, Any]:
        """Bash执行工具定义"""
        return {
            "name": "execute_bash",
            "description": "Execute bash command",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Bash command to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds",
                        "default": 30,
                    },
                },
                "required": ["command"],
            },
        }

    @staticmethod
    def _get_file_structure_tool() -> Dict[str, Any]:
        """文件结构获取工具定义"""
        return {
            "name": "get_file_structure",
            "description": "Get directory file structure",
            "input_schema": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory path, relative to workspace",
                        "default": ".",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum traversal depth",
                        "default": 5,
                    },
                },
            },
        }

    @staticmethod
    def _get_search_code_references_tool() -> Dict[str, Any]:
        """统一代码参考搜索工具定义 - 合并了三个步骤为一个工具"""
        return {
            "name": "search_code_references",
            "description": "UNIFIED TOOL: Search relevant reference code from index files. Combines directory setup, index loading, and searching in a single call.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "indexes_path": {
                        "type": "string",
                        "description": "Path to the indexes directory containing JSON index files",
                    },
                    "target_file": {
                        "type": "string",
                        "description": "Target file path to be implemented",
                    },
                    "keywords": {
                        "type": "string",
                        "description": "Search keywords, comma-separated",
                        "default": "",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 10,
                    },
                },
                "required": ["indexes_path", "target_file"],
            },
        }

    @staticmethod
    def _get_search_code_tool() -> Dict[str, Any]:
        """代码搜索工具定义 - 在当前代码库中搜索模式"""
        return {
            "name": "search_code",
            "description": "Search patterns in code files within the current repository",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Search pattern",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "File pattern (e.g., '*.py')",
                        "default": "*.py",
                    },
                    "use_regex": {
                        "type": "boolean",
                        "description": "Whether to use regular expressions",
                        "default": False,
                    },
                    "search_directory": {
                        "type": "string",
                        "description": "Specify search directory (optional)",
                    },
                },
                "required": ["pattern"],
            },
        }

    @staticmethod
    def _get_operation_history_tool() -> Dict[str, Any]:
        """操作历史工具定义"""
        return {
            "name": "get_operation_history",
            "description": "Get operation history",
            "input_schema": {
                "type": "object",
                "properties": {
                    "last_n": {
                        "type": "integer",
                        "description": "Return the last N operations",
                        "default": 10,
                    },
                },
            },
        }

    @staticmethod
    def _get_set_workspace_tool() -> Dict[str, Any]:
        """Set workspace directory tool definition"""
        return {
            "name": "set_workspace",
            "description": "Set the workspace directory for file operations",
            "input_schema": {
                "type": "object",
                "properties": {
                    "workspace_path": {
                        "type": "string",
                        "description": "Directory path for the workspace",
                    }
                },
                "required": ["workspace_path"],
            },
        }

    @staticmethod
    def get_available_tool_sets() -> Dict[str, str]:
        """
        获取可用的工具集合
        Get available tool sets
        """
        return {
            "code_implementation": "代码实现相关工具集 / Code implementation tool set",
            "docker_management": "Docker管理工具集 / Docker management tool set",
            "core-evaluation": "核心评估工具集 / Core evaluation tool set",
            "error-analysis": "错误分析工具集 / Error analysis tool set",
            "lsp-tools": "LSP工具集 / LSP tools set",
            "revision-tools": "修订工具集 / Revision tools set",
            "sandbox-tools": "沙盒工具集 / Sandbox tools set",
            "static-analysis": "静态分析工具集 / Static analysis tool set",
        }

    @staticmethod
    def get_tool_set(tool_set_name: str) -> List[Dict[str, Any]]:
        """
        根据名称获取特定的工具集
        Get specific tool set by name
        """
        tool_sets = {
            "code_implementation": MCPEvaluationToolDefinitions.get_code_implementation_tools(),
            "docker_management": MCPEvaluationToolDefinitions.get_docker_management_tools(),
            "core-evaluation": MCPEvaluationToolDefinitions.get_core_evaluation_tools(),
            "error-analysis": MCPEvaluationToolDefinitions.get_error_analysis_tools(),
            "lsp-tools": MCPEvaluationToolDefinitions.get_lsp_tools(),
            "revision-tools": MCPEvaluationToolDefinitions.get_revision_tools(),
            "sandbox-tools": MCPEvaluationToolDefinitions.get_sandbox_tools(),
            "static-analysis": MCPEvaluationToolDefinitions.get_static_analysis_tools(),
        }

        return tool_sets.get(tool_set_name, [])

    @staticmethod
    def get_all_evaluation_tools() -> List[Dict[str, Any]]:
        """
        获取所有评估相关工具
        Get all evaluation related tools
        """
        all_tools = []
        for tool_set_name in MCPEvaluationToolDefinitions.get_available_tool_sets().keys():
            all_tools.extend(MCPEvaluationToolDefinitions.get_tool_set(tool_set_name))
        return all_tools



# 便捷访问函数
def get_evaluation_mcp_tools(tool_set: str = "core-evaluation") -> List[Dict[str, Any]]:
    """
    便捷函数：获取评估相关的MCP工具定义
    Convenience function: Get evaluation MCP tool definitions

    Args:
        tool_set: 工具集名称 (默认: "core-evaluation")

    Returns:
        工具定义列表
    """
    return MCPEvaluationToolDefinitions.get_tool_set(tool_set)


def get_all_evaluation_tools() -> List[Dict[str, Any]]:
    """
    便捷函数：获取所有评估工具定义
    Convenience function: Get all evaluation tool definitions

    Returns:
        所有评估工具定义列表
    """
    return MCPEvaluationToolDefinitions.get_all_evaluation_tools()
