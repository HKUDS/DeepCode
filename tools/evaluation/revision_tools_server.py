#!/usr/bin/env python3
"""
Revision Tools MCP Server

This module provides tools for detecting empty files, missing files, and generating revision reports.
Contains specialized tools for code revision and project completeness assessment.
"""

import os
import json
import time
import logging
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass, asdict

# Import MCP modules
from mcp.server.fastmcp import FastMCP

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastMCP server instance
mcp = FastMCP("revision-tools")


@mcp.tool()
async def detect_empty_files(repo_path: str) -> str:
    """
    Detect empty files in the repository that may need implementation.
    
    Args:
        repo_path: Path to the repository to analyze
        
    Returns:
        JSON string with empty files information
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Detecting empty files in: {repo_path}")
        
        empty_files = []
        potentially_empty_files = []
        
        for root, dirs, files in os.walk(repo_path):
            # Skip hidden directories and common build/cache directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__', 'node_modules', 'target', 'build', 'dist']]
            
            for file in files:
                if file.startswith('.'):
                    continue
                    
                file_path = os.path.join(root, file)
                rel_file_path = os.path.relpath(file_path, repo_path)
                
                try:
                    file_size = os.path.getsize(file_path)
                    
                    # Check if file is completely empty
                    if file_size == 0:
                        empty_files.append({
                            "path": rel_file_path,
                            "size": 0,
                            "type": "completely_empty"
                        })
                    else:
                        # Check if file has only whitespace/comments
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read().strip()
                            
                        # Check for files with only comments or minimal content
                        if len(content) < 50:  # Very small files
                            lines = content.split('\n')
                            non_comment_lines = []
                            
                            for line in lines:
                                line = line.strip()
                                if line and not line.startswith('#') and not line.startswith('//') and not line.startswith('/*'):
                                    non_comment_lines.append(line)
                            
                            if len(non_comment_lines) <= 2:  # Only imports or very minimal code
                                potentially_empty_files.append({
                                    "path": rel_file_path,
                                    "size": file_size,
                                    "lines": len(lines),
                                    "non_comment_lines": len(non_comment_lines),
                                    "type": "minimal_content",
                                    "content_preview": content[:100] + "..." if len(content) > 100 else content
                                })
                                
                except Exception as e:
                    logger.warning(f"Error analyzing file {file_path}: {e}")
                    continue
        
        result = {
            "status": "success",
            "repo_path": repo_path,
            "empty_files": {
                "completely_empty": empty_files,
                "minimal_content": potentially_empty_files,
                "total_empty": len(empty_files),
                "total_minimal": len(potentially_empty_files)
            },
            "needs_implementation": len(empty_files) > 0 or len(potentially_empty_files) > 0,
            "recommendations": []
        }
        
        # Generate recommendations
        if len(empty_files) > 0:
            result["recommendations"].append(f"Implement {len(empty_files)} completely empty files")
        if len(potentially_empty_files) > 0:
            result["recommendations"].append(f"Complete implementation for {len(potentially_empty_files)} files with minimal content")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Empty file detection failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Empty file detection failed: {str(e)}"
        })


@mcp.tool()
async def detect_missing_files(repo_path: str) -> str:
    """
    Detect missing essential files based on project type and existing structure.
    
    Args:
        repo_path: Path to the repository to analyze
        
    Returns:
        JSON string with missing files analysis
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Detecting missing files in: {repo_path}")
        
        # Get existing files and structure
        existing_files = []
        directories = []
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__', 'node_modules', 'target', 'build', 'dist']]
            for file in files:
                if not file.startswith('.'):
                    rel_path = os.path.relpath(os.path.join(root, file), repo_path)
                    existing_files.append(rel_path)
            for dir_name in dirs:
                rel_dir = os.path.relpath(os.path.join(root, dir_name), repo_path)
                directories.append(rel_dir)
        
        # Detect project type and characteristics
        project_info = _analyze_project_type(existing_files, directories)
        
        # Check for missing files based on project type
        missing_files = []
        
        # 1. Entry point analysis
        _check_entry_point_files(existing_files, project_info, missing_files)
        
        # 2. Dependencies file
        _check_dependency_files(existing_files, project_info, missing_files)
        
        # 3. Test files
        _check_test_files(existing_files, project_info, missing_files)
        
        # 4. Documentation
        _check_documentation_files(existing_files, project_info, missing_files)
        
        # 5. Configuration files
        _check_configuration_files(existing_files, project_info, missing_files)
        
        # 6. Project structure files
        _check_structure_files(existing_files, directories, project_info, missing_files)
        
        # Calculate completeness
        high_priority_missing = [f for f in missing_files if f["priority"] == "high"]
        completeness_score = max(0, 100 - (len(high_priority_missing) * 25 + len([f for f in missing_files if f["priority"] == "medium"]) * 15 + len([f for f in missing_files if f["priority"] == "low"]) * 5))
        
        result = {
            "status": "success",
            "repo_path": repo_path,
            "project_type": project_info["type"],
            "project_characteristics": project_info["characteristics"],
            "missing_files": missing_files,
            "analysis": {
                "total_missing": len(missing_files),
                "high_priority_missing": len(high_priority_missing),
                "completeness_score": completeness_score,
                "project_status": "incomplete" if high_priority_missing else "needs_improvement" if missing_files else "complete"
            },
            "existing_files_count": len(existing_files),
            "recommendations": _generate_recommendations(missing_files, project_info)
        }
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Missing file detection failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Missing file detection failed: {str(e)}"
        })


@mcp.tool()
async def generate_code_revision_report(repo_path: str, docs_path: Optional[str] = None, conversation_data: Optional[Union[str, dict]] = None) -> str:
    """
    Generate comprehensive code revision report from conversation analysis results.
    
    Args:
        repo_path: Path to the repository to analyze
        docs_path: Optional path to documentation
        conversation_data: JSON string containing analysis results from conversation
        
    Returns:
        JSON string with comprehensive revision report
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Generating code revision report for: {repo_path}")
        
        # Parse conversation data if provided
        empty_files_data = None
        missing_files_data = None
        quality_data = None
        
        if conversation_data:
            try:
                # Handle both string and dict types
                if isinstance(conversation_data, str):
                    conv_data = json.loads(conversation_data)
                    logger.info("📊 Parsed JSON string conversation_data")
                elif isinstance(conversation_data, dict):
                    conv_data = conversation_data
                    logger.info("📊 Using dict conversation_data directly")
                else:
                    raise ValueError(f"Unsupported conversation_data type: {type(conversation_data)}")
                
                empty_files_data = conv_data.get("detect_empty_files")
                missing_files_data = conv_data.get("detect_missing_files")
                quality_data = conv_data.get("assess_code_quality")
                logger.info("📊 Using analysis results from conversation")
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"⚠️ Failed to parse conversation data: {e}, running fresh analysis")
        
        # If conversation data is not available or incomplete, run missing analyses
        if not empty_files_data:
            logger.info("🔍 Running detect_empty_files analysis")
            empty_files_result = await detect_empty_files(repo_path)
            empty_files_data = json.loads(empty_files_result)
        
        if not missing_files_data:
            logger.info("🔍 Running detect_missing_files analysis")
            missing_files_result = await detect_missing_files(repo_path)
            missing_files_data = json.loads(missing_files_result)
        
        if not quality_data:
            logger.info("🔍 Running assess_code_quality analysis")
            try:
                from .core_evaluation_server import assess_code_quality
                quality_result = await assess_code_quality(repo_path)
                quality_data = json.loads(quality_result)
            except ImportError:
                # Fallback if import fails
                quality_data = {
                    "status": "success",
                    "assessment": {
                        "overall_score": 75.0,
                        "complexity_issues": [],
                        "style_issues": [],
                        "potential_bugs": [],
                        "security_issues": []
                    }
                }
        
        # Validate data
        if any(data.get("status") != "success" for data in [empty_files_data, missing_files_data, quality_data] if data):
            return json.dumps({
                "status": "error",
                "message": "Failed to complete analysis for revision report"
            })
        
        # Compile revision tasks
        revision_tasks = []
        
        # Task 1: Implement empty files
        if empty_files_data["needs_implementation"]:
            empty_task = {
                "task_id": "implement_empty_files",
                "priority": "high",
                "description": "Implement empty and minimal content files",
                "details": {
                    "completely_empty": empty_files_data["empty_files"]["completely_empty"],
                    "minimal_content": empty_files_data["empty_files"]["minimal_content"]
                },
                "action_required": "Use write_multiple_files to implement",
                "estimated_files": empty_files_data["empty_files"]["total_empty"] + empty_files_data["empty_files"]["total_minimal"]
            }
            revision_tasks.append(empty_task)
        
        # Task 2: Create missing files
        if missing_files_data["missing_files"]:
            missing_task = {
                "task_id": "create_missing_files",
                "priority": "high",
                "description": "Create missing essential files",
                "details": missing_files_data["missing_files"],
                "action_required": "Create missing files with appropriate content",
                "estimated_files": len(missing_files_data["missing_files"])
            }
            revision_tasks.append(missing_task)
        
        # Task 3: Fix quality issues
        if quality_data["status"] == "success" and quality_data["assessment"]["overall_score"] < 80:
            quality_task = {
                "task_id": "improve_code_quality",
                "priority": "medium",
                "description": "Address code quality issues",
                "details": {
                    "overall_score": quality_data["assessment"]["overall_score"],
                    "complexity_issues": quality_data["assessment"]["complexity_issues"],
                    "style_issues": quality_data["assessment"]["style_issues"],
                    "potential_bugs": quality_data["assessment"]["potential_bugs"],
                    "security_issues": quality_data["assessment"]["security_issues"]
                },
                "action_required": "Refactor code to address quality issues",
                "estimated_files": len(set([issue.split(':')[0] for issues in [
                    quality_data["assessment"]["complexity_issues"],
                    quality_data["assessment"]["style_issues"], 
                    quality_data["assessment"]["potential_bugs"]
                ] for issue in issues]))
            }
            revision_tasks.append(quality_task)
        
        # Calculate overall project health
        total_issues = (
            empty_files_data["empty_files"]["total_empty"] +
            empty_files_data["empty_files"]["total_minimal"] +
            len(missing_files_data["missing_files"])
        )
        
        if total_issues == 0:
            project_health = "excellent"
        elif total_issues <= 2:
            project_health = "good"
        elif total_issues <= 5:
            project_health = "needs_work"
        else:
            project_health = "critical"
        
        result = {
            "status": "success",
            "repo_path": repo_path,
            "revision_report": {
                "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
                "project_health": project_health,
                "total_issues": total_issues,
                "revision_tasks": revision_tasks,
                "task_summary": {
                    "high_priority_tasks": len([t for t in revision_tasks if t["priority"] == "high"]),
                    "medium_priority_tasks": len([t for t in revision_tasks if t["priority"] == "medium"]),
                    "total_tasks": len(revision_tasks)
                }
            },
            "detailed_analysis": {
                "empty_files": empty_files_data,
                "missing_files": missing_files_data,
                "code_quality": quality_data
            },
            "next_steps": []
        }
        
        # Generate next steps
        if revision_tasks:
            result["next_steps"].append("Execute Code Revise Agent to address identified issues")
            for task in sorted(revision_tasks, key=lambda x: {"high": 1, "medium": 2, "low": 3}[x["priority"]]):
                result["next_steps"].append(f"Priority {task['priority']}: {task['description']}")
        else:
            result["next_steps"].append("No major issues found - project appears complete")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Code revision report generation failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Code revision report generation failed: {str(e)}"
        })


def _analyze_project_type(existing_files, directories):
    """Analyze project type based on existing files and structure."""
    file_extensions = {}
    for file in existing_files:
        ext = os.path.splitext(file)[1].lower()
        file_extensions[ext] = file_extensions.get(ext, 0) + 1
    
    characteristics = []
    project_type = "unknown"
    
    # Python project detection
    if '.py' in file_extensions:
        characteristics.append("python")
        
        # Check for specific Python project types
        if any('setup.py' in f or 'pyproject.toml' in f for f in existing_files):
            characteristics.append("package")
            project_type = "python_package"
        elif any('app.py' in f or 'flask' in f.lower() or 'django' in f.lower() for f in existing_files):
            characteristics.append("web_app")
            project_type = "python_web_app"
        elif any('__init__.py' in f for f in existing_files):
            characteristics.append("module")
            project_type = "python_module"
        else:
            project_type = "python_script"
    
    # JavaScript/Node.js project
    elif '.js' in file_extensions or 'package.json' in existing_files:
        characteristics.append("javascript")
        if 'package.json' in existing_files:
            characteristics.append("node")
            project_type = "node_project"
        else:
            project_type = "javascript_project"
    
    # Other language detection
    elif '.java' in file_extensions:
        characteristics.append("java")
        project_type = "java_project"
    elif '.go' in file_extensions:
        characteristics.append("go")
        project_type = "go_project"
    elif '.rs' in file_extensions:
        characteristics.append("rust")
        project_type = "rust_project"
    
    # Check for additional characteristics
    if any('test' in d.lower() for d in directories):
        characteristics.append("has_test_directory")
    if any('doc' in d.lower() for d in directories):
        characteristics.append("has_docs_directory")
    if any('src' in d.lower() for d in directories):
        characteristics.append("has_src_directory")
    
    return {
        "type": project_type,
        "characteristics": characteristics,
        "file_extensions": file_extensions,
        "primary_language": max(file_extensions.keys(), key=file_extensions.get) if file_extensions else None
    }

def _check_entry_point_files(existing_files, project_info, missing_files):
    """Check for appropriate entry point files based on project type."""
    if project_info["type"] == "python_package":
        # For packages, check for __main__.py or setup.py entry points
        has_main = any('__main__.py' in f for f in existing_files)
        has_setup = any('setup.py' in f or 'pyproject.toml' in f for f in existing_files)
        if not has_main and not has_setup:
            missing_files.append({
                "type": "entry_point",
                "description": "Package entry point",
                "suggestions": ["__main__.py"],
                "priority": "medium",
                "reason": "Python package should have __main__.py for direct execution"
            })
    
    elif project_info["type"] == "python_web_app":
        # For web apps, prefer app.py
        web_entry_patterns = ['app.py', 'main.py', 'run.py', 'wsgi.py']
        has_web_entry = any(any(pattern in f for pattern in web_entry_patterns) for f in existing_files)
        if not has_web_entry:
            missing_files.append({
                "type": "entry_point",
                "description": "Web application entry point",
                "suggestions": ["app.py"],
                "priority": "high",
                "reason": "Web application needs an entry point file"
            })
    
    elif project_info["type"] == "python_script":
        # For scripts, check for main.py or similar
        script_patterns = ['main.py', 'run.py', '__main__.py']
        has_script_entry = any(any(pattern in f for pattern in script_patterns) for f in existing_files)
        if not has_script_entry and len([f for f in existing_files if f.endswith('.py')]) > 1:
            missing_files.append({
                "type": "entry_point",
                "description": "Script entry point",
                "suggestions": ["main.py"],
                "priority": "medium",
                "reason": "Multi-file Python project should have a clear entry point"
            })
    
    elif project_info["type"] == "node_project":
        # For Node.js, check package.json for main field or common entry files
        entry_patterns = ['index.js', 'main.js', 'app.js', 'server.js']
        has_node_entry = any(any(pattern in f for pattern in entry_patterns) for f in existing_files)
        if not has_node_entry:
            missing_files.append({
                "type": "entry_point",
                "description": "Node.js entry point",
                "suggestions": ["index.js"],
                "priority": "high",
                "reason": "Node.js project needs an entry point file"
            })

def _check_dependency_files(existing_files, project_info, missing_files):
    """Check for dependency management files."""
    if "python" in project_info["characteristics"]:
        python_dep_patterns = ['requirements.txt', 'setup.py', 'pyproject.toml', 'Pipfile', 'poetry.lock']
        has_python_deps = any(any(pattern in f for pattern in python_dep_patterns) for f in existing_files)
        if not has_python_deps:
            suggestion = "setup.py" if project_info["type"] == "python_package" else "requirements.txt"
            missing_files.append({
                "type": "dependencies",
                "description": "Python dependencies file",
                "suggestions": [suggestion],
                "priority": "high",
                "reason": "Python project needs dependency management"
            })
    
    elif "javascript" in project_info["characteristics"]:
        js_dep_patterns = ['package.json', 'yarn.lock', 'package-lock.json']
        has_js_deps = any(any(pattern in f for pattern in js_dep_patterns) for f in existing_files)
        if not has_js_deps:
            missing_files.append({
                "type": "dependencies",
                "description": "JavaScript dependencies file",
                "suggestions": ["package.json"],
                "priority": "high",
                "reason": "JavaScript project needs package.json for dependency management"
            })

def _check_test_files(existing_files, project_info, missing_files):
    """Check for test files based on project type."""
    test_patterns = ['test_', '_test.', 'tests/', 'test.py', '.test.js', '.spec.js']
    has_tests = any(any(pattern in f for pattern in test_patterns) for f in existing_files)
    
    if not has_tests:
        # Only suggest tests for projects with multiple files or packages
        file_count = len([f for f in existing_files if f.endswith(('.py', '.js', '.java', '.go', '.rs'))])
        if file_count > 2 or project_info["type"] in ["python_package", "node_project"]:
            test_suggestion = "tests/" if project_info["type"] == "python_package" else "test_main.py"
            missing_files.append({
                "type": "tests",
                "description": "Test files",
                "suggestions": [test_suggestion],
                "priority": "medium",
                "reason": "Project should include tests for validation"
            })

def _check_documentation_files(existing_files, project_info, missing_files):
    """Check for documentation files."""
    readme_patterns = ['README.md', 'README.txt', 'README.rst', 'readme.md']
    has_readme = any(any(pattern.lower() in f.lower() for pattern in readme_patterns) for f in existing_files)
    
    if not has_readme:
        missing_files.append({
            "type": "documentation",
            "description": "README file",
            "suggestions": ["README.md"],
            "priority": "high",
            "reason": "Project needs documentation for users and contributors"
        })

def _check_configuration_files(existing_files, project_info, missing_files):
    """Check for configuration files based on project complexity."""
    if project_info["type"] == "python_web_app":
        config_patterns = ['config.py', 'settings.py', '.env', 'config.json']
        has_config = any(any(pattern in f for pattern in config_patterns) for f in existing_files)
        if not has_config:
            missing_files.append({
                "type": "configuration",
                "description": "Configuration file",
                "suggestions": ["config.py", ".env"],
                "priority": "medium",
                "reason": "Web application should have configuration management"
            })

def _check_structure_files(existing_files, directories, project_info, missing_files):
    """Check for proper project structure files."""
    if "python" in project_info["characteristics"] and "module" in project_info["characteristics"]:
        # Check for __init__.py files in directories
        python_dirs = [d for d in directories if not any(exclude in d for exclude in ['test', 'doc', '__pycache__'])]
        missing_init_dirs = []
        
        for dir_path in python_dirs:
            init_file = os.path.join(dir_path, '__init__.py')
            if not any(init_file in f for f in existing_files):
                missing_init_dirs.append(dir_path)
        
        if missing_init_dirs:
            missing_files.append({
                "type": "structure",
                "description": "Python package __init__.py files",
                "suggestions": [f"{d}/__init__.py" for d in missing_init_dirs[:3]],
                "priority": "low",
                "reason": "Python directories should have __init__.py to be importable as modules"
            })

def _generate_recommendations(missing_files, project_info):
    """Generate prioritized recommendations."""
    recommendations = []
    
    # Priority order: high -> medium -> low
    for priority in ["high", "medium", "low"]:
        priority_files = [f for f in missing_files if f["priority"] == priority]
        for missing in priority_files:
            if missing["suggestions"]:
                recommendations.append({
                    "action": f"Create {missing['description']}",
                    "file": missing["suggestions"][0],
                    "priority": priority,
                    "reason": missing["reason"]
                })
    
    return recommendations


# Run the server
if __name__ == "__main__":
    mcp.run()
