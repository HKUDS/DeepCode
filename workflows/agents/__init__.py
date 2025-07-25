"""
Agents Package for Code Implementation Workflow
代码实现工作流的代理包

This package contains specialized agents for different aspects of code implementation:
- CodeImplementationAgent: Handles file-by-file code generation
- ConciseMemoryAgent: Manages memory optimization and consistency across phases
"""

from .code_implementation_agent import CodeImplementationAgent
from .memory_agent_concise import ConciseMemoryAgent as MemoryAgent

__all__ = ["CodeImplementationAgent", "MemoryAgent"]
