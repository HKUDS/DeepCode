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
    handle_guided_mode_processing,
)
from .styles import get_main_styles


def setup_page_config():
    """Setup optimized page configuration"""
    st.set_page_config(
        page_title="DeepCode - AI Research Engine",
        page_icon="🧬",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            "Get Help": "https://github.com/yourusername/deepcode",
            "Report a bug": "https://github.com/yourusername/deepcode/issues",
            "About": "# DeepCode AI Research Engine\nNext-Generation Multi-Agent Coding Platform",
        },
    )


def apply_custom_styles():
    """Apply custom styles"""
    st.markdown(get_main_styles(), unsafe_allow_html=True)


def render_main_content():
    """Render main content area with improved layout"""
    # Display modern, compact header and features
    display_header()
    display_features()

    # Add subtle spacing instead of heavy divider
    st.markdown(
        """
        <div style="height: 2rem; background: linear-gradient(90deg,
            transparent 0%,
            rgba(100, 181, 246, 0.1) 50%,
            transparent 100%);
            margin: 1.5rem 0;
            border-radius: 2px;">
        </div>
        """,
        unsafe_allow_html=True,
    )

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
    # 处理引导模式的异步操作
    handle_guided_mode_processing()

    # Check if user is in guided analysis workflow
    if st.session_state.get(
        "requirement_analysis_mode"
    ) == "guided" and st.session_state.get("requirement_analysis_step") in [
        "questions",
        "summary",
        "editing",
    ]:
        # User is in guided analysis workflow, show chat input directly
        from .components import chat_input_component

        input_source = chat_input_component(st.session_state.task_counter)
        input_type = "chat" if input_source else None
    else:
        # Normal flow: show input method selector
        input_source, input_type = input_method_selector(st.session_state.task_counter)

    # Processing button - Check if requirements are confirmed for guided mode
    requirements_confirmed = st.session_state.get("requirements_confirmed", False)

    # For guided mode, if requirements are confirmed, automatically start processing
    if (
        st.session_state.get("requirement_analysis_mode") == "guided"
        and requirements_confirmed
        and input_source
        and not st.session_state.processing
    ):
        # Automatically start processing for confirmed requirements
        st.session_state.requirements_confirmed = (
            False  # Clear flag to prevent re-processing
        )
        handle_start_processing_button(input_source, input_type)
    elif (
        input_source and not st.session_state.processing and not requirements_confirmed
    ):
        # Only show Start Processing button if requirements are not already confirmed
        if st.button("🚀 Start Processing", type="primary", use_container_width=True):
            handle_start_processing_button(input_source, input_type)

    elif st.session_state.processing:
        st.info("🔄 Processing in progress... Please wait.")
        st.warning("⚠️ Do not refresh the page or close the browser during processing.")

    elif not input_source:
        st.info(
            "👆 Please upload a file, enter a URL, or describe your coding requirements to start processing."
        )


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
