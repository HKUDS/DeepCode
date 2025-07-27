"""
DeepCode UI Application Entry Point

This file serves as the unified entry point for the UI module
"""

from .streamlit_app import main

# Directly export main function for external calls
__all__ = ["main"]

if __name__ == "__main__":
    main()
