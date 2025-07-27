"""
UI Module

Streamlit application user interface components module

Contains the following submodules:
- styles: CSS styles
- components: UI components
- layout: Page layout
- handlers: Event handlers
- streamlit_app: Main application
- app: Application entry
"""

__version__ = "1.0.0"
__author__ = "DeepCode Team"

# Import main components
from .layout import main_layout
from .components import display_header, display_features, display_status
from .handlers import initialize_session_state
from .styles import get_main_styles

# Import application main function
try:
    from .streamlit_app import main as streamlit_main
except ImportError:
    # Fallback to absolute import if relative import fails
    import sys
    import os

    sys.path.insert(0, os.path.dirname(__file__))
    from streamlit_app import main as streamlit_main

__all__ = [
    "main_layout",
    "display_header",
    "display_features",
    "display_status",
    "initialize_session_state",
    "get_main_styles",
    "streamlit_main",
]
