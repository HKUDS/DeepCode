"""
Streamlit Page Layout Module

Contains main page layout and flow control
"""

import streamlit as st

from .components import (
    display_header,
    display_features,
    sidebar_control_panel,
    input_method_selector,
    results_display_component,
    footer_component,
)
from .handlers import (
    initialize_session_state,
    handle_start_processing_button,
    handle_error_display,
)
from .styles import get_main_styles


def setup_page_config():
    """Setup page configuration"""
    st.set_page_config(
        page_title="DeepCode - AI Research Engine",
        page_icon="🧬",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def apply_custom_styles():
    """Apply custom styles"""
    st.markdown(get_main_styles(), unsafe_allow_html=True)


def render_main_content():
    """Render main content area"""
    # Display header and features
    display_header()
    display_features()
    st.markdown("---")

    # Display results if available
    if st.session_state.show_results and st.session_state.last_result:
        results_display_component(
            st.session_state.last_result, st.session_state.task_counter
        )
        st.markdown("---")
        return

    # Show input interface only when not displaying results
    if not st.session_state.show_results:
        render_input_interface()

    # Display error messages if any
    handle_error_display()


def render_input_interface():
    """Render input interface"""
    # Get input source and type
    input_source, input_type = input_method_selector(st.session_state.task_counter)

    # Processing button
    if input_source and not st.session_state.processing:
        if st.button("🚀 Start Processing", type="primary", use_container_width=True):
            handle_start_processing_button(input_source, input_type)

    elif st.session_state.processing:
        st.info("🔄 Processing in progress... Please wait.")
        st.warning("⚠️ Do not refresh the page or close the browser during processing.")

    elif not input_source:
        st.info("👆 Please upload a file or enter a URL to start processing.")


def render_sidebar():
    """Render sidebar"""
    return sidebar_control_panel()


def main_layout():
    """Main layout function"""
    # Initialize session state
    initialize_session_state()

    # Setup page configuration
    setup_page_config()

    # Apply custom styles
    apply_custom_styles()

    # Render sidebar
    sidebar_info = render_sidebar()

    # Render main content
    render_main_content()

    # Display footer
    footer_component()

    return sidebar_info
