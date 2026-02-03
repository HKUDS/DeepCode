"""
MCP工具定义配置模块
MCP Tool Definitions Configuration Module

将工具定义从主程序逻辑中分离，提供标准化的工具定义格式
Separate tool definitions from main program logic, providing standardized tool definition format

支持的工具类型：
- 文件操作工具 (File Operations)
- 代码执行工具 (Code Execution)
- 搜索工具 (Search Tools)
- 项目结构工具 (Project Structure Tools)
"""

from typing import Dict, List, Any


class MCPToolDefinitions:
    """MCP工具定义管理器"""

    @staticmethod
    def get_code_implementation_tools() -> List[Dict[str, Any]]:
        """
        获取代码实现相关的工具定义
        Get tool definitions for code implementation
        """
        return [
            # MCPToolDefinitions._get_read_file_tool(),
            # MCPToolDefinitions._get_read_multiple_files_tool(),
            # MCPToolDefinitions._get_read_code_mem_tool(),
            MCPToolDefinitions._get_write_file_tool(),
            # MCPToolDefinitions._get_write_multiple_files_tool(),
            # MCPToolDefinitions._get_execute_python_tool(),
            # MCPToolDefinitions._get_execute_bash_tool(),
            MCPToolDefinitions._get_search_code_references_tool(),
            # MCPToolDefinitions._get_search_code_tool(),
            # MCPToolDefinitions._get_file_structure_tool(),
            # MCPToolDefinitions._get_set_workspace_tool(),
            # MCPToolDefinitions._get_operation_history_tool(),
        ]

    @staticmethod
    def get_code_evaluation_tools() -> List[Dict[str, Any]]:
        """
        获取代码评估相关的工具定义
        Get tool definitions for code evaluation
        """
        return [
            MCPToolDefinitions._get_analyze_repo_structure_tool(),
            MCPToolDefinitions._get_detect_dependencies_tool(),
            MCPToolDefinitions._get_assess_code_quality_tool(),
            MCPToolDefinitions._get_evaluate_documentation_tool(),
            MCPToolDefinitions._get_check_reproduction_readiness_tool(),
            MCPToolDefinitions._get_generate_evaluation_summary_tool(),
            MCPToolDefinitions._get_detect_empty_files_tool(),
            MCPToolDefinitions._get_detect_missing_files_tool(),
            MCPToolDefinitions._get_generate_code_revision_report_tool(),
        ]

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
                        "description": 'JSON string with file requests, e.g., \'{"file1.py": {}, "file2.py": {"start_line": 1, "end_line": 10}}\' or simple array \'["file1.py", "file2.py"]\'',
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
                        "type": "string",
                        "description": 'JSON string mapping file paths to content, e.g., \'{"file1.py": "content1", "file2.py": "content2"}\'',
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
    def _get_get_indexes_overview_tool() -> Dict[str, Any]:
        """获取索引概览工具定义"""
        return {
            "name": "get_indexes_overview",
            "description": "Get overview of all available reference code index information from specified directory",
            "input_schema": {
                "type": "object",
                "properties": {
                    "indexes_path": {
                        "type": "string",
                        "description": "Path to the indexes directory containing JSON index files",
                    }
                },
                "required": ["indexes_path"],
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

    # @staticmethod
    # def _get_set_indexes_directory_tool() -> Dict[str, Any]:
    #     """Set indexes directory tool definition - DEPRECATED: Use unified search_code_references instead"""
    #     return {
    #         "name": "set_indexes_directory",
    #         "description": "Set the directory path for code reference indexes",
    #         "input_schema": {
    #             "type": "object",
    #             "properties": {
    #                 "indexes_path": {
    #                     "type": "string",
    #                     "description": "Directory path containing index JSON files"
    #                 }
    #             },
    #             "required": ["indexes_path"]
    #         }
    #     }

    # Code evaluation tool definitions
    @staticmethod
    def _get_analyze_repo_structure_tool() -> Dict[str, Any]:
        return {
            "name": "analyze_repo_structure",
            "description": "Perform comprehensive repository structure analysis",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository to analyze",
                    }
                },
                "required": ["repo_path"],
            },
        }

    @staticmethod
    def _get_detect_dependencies_tool() -> Dict[str, Any]:
        return {
            "name": "detect_dependencies",
            "description": "Detect and analyze project dependencies across multiple languages",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository",
                    }
                },
                "required": ["repo_path"],
            },
        }

    @staticmethod
    def _get_assess_code_quality_tool() -> Dict[str, Any]:
        return {
            "name": "assess_code_quality",
            "description": "Assess code quality metrics and identify potential issues",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository",
                    }
                },
                "required": ["repo_path"],
            },
        }

    @staticmethod
    def _get_evaluate_documentation_tool() -> Dict[str, Any]:
        return {
            "name": "evaluate_documentation",
            "description": "Evaluate documentation completeness and quality",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository",
                    },
                    "docs_path": {
                        "type": "string",
                        "description": "Optional path to external documentation",
                    },
                },
                "required": ["repo_path"],
            },
        }

    @staticmethod
    def _get_check_reproduction_readiness_tool() -> Dict[str, Any]:
        return {
            "name": "check_reproduction_readiness",
            "description": "Assess repository readiness for reproduction and validation",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository",
                    },
                    "docs_path": {
                        "type": "string",
                        "description": "Optional path to reproduction documentation",
                    },
                },
                "required": ["repo_path"],
            },
        }

    @staticmethod
    def _get_generate_evaluation_summary_tool() -> Dict[str, Any]:
        return {
            "name": "generate_evaluation_summary",
            "description": "Generate comprehensive evaluation summary combining all analysis results",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository",
                    },
                    "docs_path": {
                        "type": "string",
                        "description": "Optional path to reproduction documentation",
                    },
                },
                "required": ["repo_path"],
            },
        }

    @staticmethod
    def _get_detect_empty_files_tool() -> Dict[str, Any]:
        return {
            "name": "detect_empty_files",
            "description": "Detect empty files in the repository that may need implementation",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository to analyze",
                    }
                },
                "required": ["repo_path"],
            },
        }

    @staticmethod
    def _get_detect_missing_files_tool() -> Dict[str, Any]:
        return {
            "name": "detect_missing_files",
            "description": "Detect missing essential files like main programs, tests, requirements, etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository to analyze",
                    }
                },
                "required": ["repo_path"],
            },
        }

    @staticmethod
    def _get_generate_code_revision_report_tool() -> Dict[str, Any]:
        return {
            "name": "generate_code_revision_report",
            "description": "Generate comprehensive code revision report combining empty files, missing files, and quality analysis",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the repository to analyze",
                    },
                    "docs_path": {
                        "type": "string",
                        "description": "Optional path to documentation",
                    },
                },
                "required": ["repo_path"],
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
            "code_evaluation": "代码评估相关工具集 / Code evaluation tool set",
            # 可以在这里添加更多工具集
            # "data_analysis": "数据分析工具集 / Data analysis tool set",
            # "web_scraping": "网页爬取工具集 / Web scraping tool set",
        }

    @staticmethod
    def get_tool_set(tool_set_name: str) -> List[Dict[str, Any]]:
        """
        根据名称获取特定的工具集
        Get specific tool set by name
        """
        tool_sets = {
            "code_implementation": MCPToolDefinitions.get_code_implementation_tools(),
            "code_evaluation": MCPToolDefinitions.get_code_evaluation_tools(),
        }

        return tool_sets.get(tool_set_name, [])

    @staticmethod
    def get_all_tools() -> List[Dict[str, Any]]:
        """
        获取所有可用工具
        Get all available tools
        """
        all_tools = []
        for tool_set_name in MCPToolDefinitions.get_available_tool_sets().keys():
            all_tools.extend(MCPToolDefinitions.get_tool_set(tool_set_name))
        return all_tools


# 便捷访问函数
def get_mcp_tools(tool_set: str = "code_implementation") -> List[Dict[str, Any]]:
    """
    便捷函数：获取MCP工具定义
    Convenience function: Get MCP tool definitions

    Args:
        tool_set: 工具集名称 (默认: "code_implementation")

    Returns:
        工具定义列表
    """
    return MCPToolDefinitions.get_tool_set(tool_set)
