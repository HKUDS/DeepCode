"""
CLI Module for DeepCode Agent
DeepCode智能体CLI模块

包含以下组件 / Contains the following components:
- cli_app: CLI应用主程序 / CLI application main program
- cli_interface: CLI界面组件 / CLI interface components
- cli_launcher: CLI启动器 / CLI launcher
"""

__version__ = "1.0.0"
__author__ = "DeepCode Team - Data Intelligence Lab @ HKU"

from .cli_app import main as cli_main
from .cli_interface import CLIInterface
from .cli_launcher import main as launcher_main

__all__ = ["cli_main", "CLIInterface", "launcher_main"]
