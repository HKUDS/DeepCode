#!/usr/bin/env python3
"""
Revision Tools MCP Server - Exact Copy from Original

This module contains exactly the same code as the original file for revision tools.
"""

import os
import json
import time
import logging
from typing import Dict, Any, List, Optional
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


# Run the server
if __name__ == "__main__":
    mcp.run()
