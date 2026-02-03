"""
DeepCode Layout Manager
Organizes the visual structure using the Cyber components.
"""

from typing import Optional

import streamlit as st
from .components import (
    display_features,
    display_header,
    footer_component,
    guided_requirement_workflow,
    input_method_selector,
    requirement_mode_selector,
    results_display_component,
    sidebar_control_panel,
    system_status_component,
)
from .styles import get_main_styles
from .handlers import (
    initialize_session_state,
    handle_start_processing_button,
    handle_error_display,
    handle_guided_mode_processing,
)


def setup_page_config():
    st.set_page_config(
        page_title="DeepCode",
        page_icon="assets/logo.png",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            "Get Help": "https://github.com/deepcode",
            "About": "DeepCode AI Research Engine v3.0",
        },
    )


def main_layout():
    """Main layout execution"""
    # Initialize Core
    initialize_session_state()
    setup_page_config()

    # Inject Cyber Styles
    st.markdown(get_main_styles(), unsafe_allow_html=True)

    # Render Sidebar
    sidebar_control_panel()

    # Main Content Area
    display_header()

    # Determine Content State
    show_results = st.session_state.get("show_results", False)
    last_result = st.session_state.get("last_result", None)

    if show_results and last_result:
        results_display_component(last_result, st.session_state.task_counter)
    else:
        # Landing State
        display_features()
        system_status_component()

        st.markdown('<div style="height: 2rem;"></div>', unsafe_allow_html=True)

        # Input Interface
        render_input_area()

    # Global Error Handler (Always active)
    handle_error_display()

    # Footer
    footer_component()

    return {}


def render_input_area():
    """Handles the logic for which input to show"""

    # Handle guided mode async processing (background)
    handle_guided_mode_processing()

    mode = requirement_mode_selector()
    is_guided = mode == "guided"
    processing = st.session_state.get("processing", False)
    requirements_confirmed = st.session_state.get("requirements_confirmed", False)

    input_source: Optional[str] = None
    input_type: Optional[str] = None

    with st.container():
        if is_guided:
            input_source, _ = guided_requirement_workflow()
            input_type = "chat" if input_source else None
        else:
            input_source, input_type = input_method_selector(
                st.session_state.task_counter
            )

        st.markdown('<div style="height: 1.5rem;"></div>', unsafe_allow_html=True)

        if is_guided and requirements_confirmed and input_source and not processing:
            payload = input_source
            st.session_state.requirements_confirmed = False
            st.session_state.confirmed_requirement_text = None
            handle_start_processing_button(payload, input_type or "chat")

        elif input_source and not processing:
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                if st.button(
                    "START CODING 🚀", type="primary", use_container_width=True
                ):
                    if is_guided:
                        st.session_state.confirmed_requirement_text = None
                    handle_start_processing_button(input_source, input_type or "chat")

        elif processing:
            st.markdown(
                """
                <div style="padding:1.5rem; border:1px solid var(--primary); border-radius:4px; background:rgba(0, 242, 255, 0.05); text-align:center;">
                    <div class="status-dot" style="display:inline-block; margin-right:10px;"></div>
                    <span style="font-family: var(--font-code); color: var(--primary); animation: pulse-glow 2s infinite;">NEURAL PROCESSING ACTIVE...</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

        elif not input_source and not is_guided:
            st.markdown(
                """
                <div style="text-align:center; color:rgba(255,255,255,0.3); font-family:var(--font-code); font-size:0.8rem;">
                    AWAITING INPUT SIGNAL...
                </div>
                """,
                unsafe_allow_html=True,
            )
