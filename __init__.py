"""
DeepCode - AI Research Engine

🧬 Next-Generation AI Research Automation Platform
⚡ Transform research papers into working code automatically
"""

__version__ = "1.0.6-jm"
__author__ = "DeepCode Team, Jany Martelli"
__url__ = "https://github.com/HKUDS/DeepCode"
__repo__ = "https://github.com/Jany-M/DeepCode/"

# Import main components for easy access
from utils import FileProcessor, DialogueLogger

__all__ = [
    "FileProcessor",
    "DialogueLogger",
    "__version__",
    "__author__",
    "__url__",
]
