# ruff: noqa: N999
"""DeepCode — an open agentic coding system."""

from core.version import __version__
from utils import FileProcessor

__author__ = "DeepCode Team"
__url__ = "https://github.com/HKUDS/DeepCode"
__repo__ = __url__

__all__ = [
    "FileProcessor",
    "__author__",
    "__url__",
    "__version__",
]
