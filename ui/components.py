# -*- coding: utf-8 -*-
"""
Streamlit UI Components Module

Contains all reusable UI components
"""

import streamlit as st
import sys
from typing import Dict, Any, Optional, List
from datetime import datetime
import json


def display_header():
    """Display modern, compact application header"""
    st.markdown(
        """
    <div class="modern-header">
        <div class="header-content">
            <div class="logo-section">
                <div class="logo-animation">
                    <div class="dna-helix">
                        <div class="helix-strand strand-1"></div>
                        <div class="helix-strand strand-2"></div>
                    </div>
                    <span class="logo-text">◊ DeepCode</span>
                </div>
                <div class="tagline">
                    <span class="highlight">AI Research Engine</span>
                    <span class="separator">•</span>
                    <span class="org">Data Intelligence Lab @ HKU</span>
                </div>
            </div>
            <div class="status-badge">
                <span class="status-dot"></span>
                <span class="status-text">ONLINE</span>
            </div>
        </div>
    </div>
    """,
        unsafe_allow_html=True,
    )


def display_features():
    """Display DeepCode AI capabilities with world-class, futuristic design"""

    # Capability Matrix
    st.markdown(
        """
        <div class="capability-matrix">
            <div class="capability-node research-node">
                <div class="node-core">
                    <div class="core-pulse"></div>
                    <div class="core-label">RESEARCH</div>
                </div>
                <div class="node-description">
                    <h3>Paper2Code&Text2Code</h3>
                    <p>Neural document processing and algorithmic synthesis</p>
                </div>
                <div class="node-metrics">
                    <span class="metric">Multi-Agents</span>
                </div>
            </div>


        </div>
    """,
        unsafe_allow_html=True,
    )

    # Processing Pipeline
    st.markdown(
        """
        <div class="processing-pipeline">
            <div class="pipeline-stage stage-requirements">
                <div class="stage-core">REQUIREMENTS</div>
                <div class="stage-description">Input Requirements</div>
            </div>
            <div class="pipeline-flow">
                <div class="flow-particle"></div>
            </div>
            <div class="pipeline-stage stage-planning">
                <div class="stage-core">PLANNING</div>
                <div class="stage-description">Design & Planning</div>
            </div>
            <div class="pipeline-flow">
                <div class="flow-particle"></div>
            </div>
            <div class="pipeline-stage stage-implementation">
                <div class="stage-core">IMPLEMENTATION</div>
                <div class="stage-description">Code Implementation</div>
            </div>
            <div class="pipeline-flow">
                <div class="flow-particle"></div>
            </div>
            <div class="pipeline-stage stage-validation">
                <div class="stage-core">VALIDATION</div>
                <div class="stage-description">Validation & Refinement</div>
            </div>
        </div>
    """,
        unsafe_allow_html=True,
    )


def display_status(message: str, status_type: str = "info"):
    """
    Display status message

    Args:
        message: Status message
        status_type: Status type (success, error, warning, info)
    """
    status_classes = {
        "success": "status-success",
        "error": "status-error",
        "warning": "status-warning",
        "info": "status-info",
    }

    icons = {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️"}

    css_class = status_classes.get(status_type, "status-info")
    icon = icons.get(status_type, "ℹ️")

    st.markdown(
        f"""
    <div class="{css_class}">
        {icon} {message}
    </div>
    """,
        unsafe_allow_html=True,
    )


def system_status_component():
    """System status check component"""
    st.markdown("### 🔧 System Status & Diagnostics")

    # Basic system information
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 📊 Environment")
        st.info(f"**Python:** {sys.version.split()[0]}")
        st.info(f"**Platform:** {sys.platform}")

        # Check key modules
        modules_to_check = [
            ("streamlit", "Streamlit UI Framework"),
            ("asyncio", "Async Processing"),
            ("nest_asyncio", "Nested Event Loops"),
            ("concurrent.futures", "Threading Support"),
        ]

        st.markdown("#### 📦 Module Status")
        for module_name, description in modules_to_check:
            try:
                __import__(module_name)
                st.success(f"✅ {description}")
            except ImportError:
                st.error(f"❌ {description} - Missing")

    with col2:
        st.markdown("#### ⚙️ Threading & Context")

        # Check Streamlit context
        try:
            from streamlit.runtime.scriptrunner import get_script_run_ctx

            ctx = get_script_run_ctx()
            if ctx:
                st.success("✅ Streamlit Context Available")
            else:
                st.warning("⚠️ Streamlit Context Not Found")
        except Exception as e:
            st.error(f"❌ Context Check Failed: {e}")

        # Check event loop
        try:
            import asyncio

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    st.info("🔄 Event Loop Running")
                else:
                    st.info("⏸️ Event Loop Not Running")
            except RuntimeError:
                st.info("🆕 No Event Loop (Normal)")
        except Exception as e:
            st.error(f"❌ Event Loop Check Failed: {e}")


def error_troubleshooting_component():
    """Error troubleshooting component"""
    with st.expander("🛠️ Troubleshooting Tips", expanded=False):
        st.markdown("""
        ### Common Issues & Solutions

        #### 1. ScriptRunContext Warnings
        - **What it means:** Threading context warnings in Streamlit
        - **Solution:** These warnings are usually safe to ignore
        - **Prevention:** Restart the application if persistent

        #### 2. Async Processing Errors
        - **Symptoms:** "Event loop" or "Thread" errors
        - **Solution:** The app uses multiple fallback methods
        - **Action:** Try refreshing the page or restarting

        #### 3. File Upload Issues
        - **Check:** File size < 200MB
        - **Formats:** PDF, DOCX, TXT, HTML, MD
        - **Action:** Try a different file format

        #### 4. Processing Timeout
        - **Normal:** Large papers may take 5-10 minutes
        - **Action:** Wait patiently, check progress indicators
        - **Limit:** 5-minute maximum processing time

        #### 5. Memory Issues
        - **Symptoms:** "Out of memory" errors
        - **Solution:** Close other applications
        - **Action:** Try smaller/simpler papers first
        """)

        if st.button("🔄 Reset Application State"):
            # Clear all session state
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.success("Application state reset! Please refresh the page.")
            st.rerun()


def sidebar_control_panel() -> Dict[str, Any]:
    """
    Sidebar control panel

    Returns:
        Control panel state
    """
    with st.sidebar:
        st.markdown("### 🎛️ Control Panel")

        # Application status
        if st.session_state.processing:
            st.warning("🟡 Engine Processing...")
        else:
            st.info("⚪ Engine Ready")

        # Workflow configuration options
        st.markdown("### ⚙️ Workflow Settings")

        # Indexing functionality toggle
        enable_indexing = st.checkbox(
            "🗂️ Enable Codebase Indexing",
            value=True,
            help="Enable GitHub repository download and codebase indexing. Disabling this will skip Phase 6 (GitHub Download) and Phase 7 (Codebase Indexing) for faster processing.",
            key="enable_indexing",
        )

        if enable_indexing:
            st.success("✅ Full workflow with indexing enabled")
        else:
            st.info("⚡ Fast mode - indexing disabled")

        # System information
        st.markdown("### 📊 System Info")
        st.info(f"**Python:** {sys.version.split()[0]}")
        st.info(f"**Platform:** {sys.platform}")

        # Add system status check
        with st.expander("🔧 System Status"):
            system_status_component()

        # Add error diagnostics
        error_troubleshooting_component()

        st.markdown("---")

        # Processing history
        history_info = display_processing_history()

        return {
            "processing": st.session_state.processing,
            "history_count": history_info["count"],
            "has_history": history_info["has_history"],
            "enable_indexing": enable_indexing,  # Add indexing toggle state
        }


def display_processing_history() -> Dict[str, Any]:
    """
    Display processing history

    Returns:
        History information
    """
    st.markdown("### 📊 Processing History")

    has_history = bool(st.session_state.results)
    history_count = len(st.session_state.results)

    if has_history:
        # Only show last 10 records
        recent_results = st.session_state.results[-10:]
        for i, result in enumerate(reversed(recent_results)):
            status_icon = "✅" if result.get("status") == "success" else "❌"
            with st.expander(
                f"{status_icon} Task - {result.get('timestamp', 'Unknown')}"
            ):
                st.write(f"**Status:** {result.get('status', 'Unknown')}")
                if result.get("input_type"):
                    st.write(f"**Type:** {result['input_type']}")
                if result.get("error"):
                    st.error(f"Error: {result['error']}")
    else:
        st.info("No processing history yet")

    # Clear history button
    if has_history:
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🗑️ Clear History", use_container_width=True):
                st.session_state.results = []
                st.rerun()
        with col2:
            st.info(f"Total: {history_count} tasks")

    return {"has_history": has_history, "count": history_count}


def file_input_component(task_counter: int) -> Optional[str]:
    """
    File input component with automatic PDF conversion

    Args:
        task_counter: Task counter

    Returns:
        PDF file path or None
    """
    uploaded_file = st.file_uploader(
        "Upload research paper file",
        type=[
            "pdf",
            "docx",
            "doc",
            "ppt",
            "pptx",
            "xls",
            "xlsx",
            "html",
            "htm",
            "txt",
            "md",
        ],
        help="Supported formats: PDF, Word, PowerPoint, Excel, HTML, Text (all files will be converted to PDF)",
        key=f"file_uploader_{task_counter}",
    )

    if uploaded_file is not None:
        # Display file information
        file_size = len(uploaded_file.getvalue())
        st.info(f"📄 **File:** {uploaded_file.name} ({format_file_size(file_size)})")

        # Save uploaded file to temporary directory
        try:
            import tempfile
            import sys
            import os
            from pathlib import Path

            # Add project root to path for imports
            current_dir = Path(__file__).parent
            project_root = current_dir.parent
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))

            # Import PDF converter
            from tools.pdf_converter import PDFConverter

            # Save original file
            file_ext = uploaded_file.name.split(".")[-1].lower()
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=f".{file_ext}"
            ) as tmp_file:
                tmp_file.write(uploaded_file.getvalue())
                original_file_path = tmp_file.name

            st.success("✅ File uploaded successfully!")

            # Check if file is already PDF
            if file_ext == "pdf":
                st.info("📑 File is already in PDF format, no conversion needed.")
                # Return JSON structure with paper_path for consistency
                import json
                return json.dumps({"paper_path": original_file_path})

            # Convert to PDF
            with st.spinner(f"🔄 Converting {file_ext.upper()} to PDF..."):
                try:
                    converter = PDFConverter()

                    # Check dependencies
                    deps = converter.check_dependencies()
                    missing_deps = []

                    if (
                        file_ext in {"doc", "docx", "ppt", "pptx", "xls", "xlsx"}
                        and not deps["libreoffice"]
                    ):
                        missing_deps.append("LibreOffice")

                    if file_ext in {"txt", "md"} and not deps["reportlab"]:
                        missing_deps.append("ReportLab")

                    if missing_deps:
                        st.error(f"❌ Missing dependencies: {', '.join(missing_deps)}")
                        st.info("💡 Please install the required dependencies:")
                        if "LibreOffice" in missing_deps:
                            st.code(
                                "# Install LibreOffice\n"
                                "# Windows: Download from https://www.libreoffice.org/\n"
                                "# macOS: brew install --cask libreoffice\n"
                                "# Ubuntu: sudo apt-get install libreoffice"
                            )
                        if "ReportLab" in missing_deps:
                            st.code("pip install reportlab")

                        # Clean up original file
                        try:
                            os.unlink(original_file_path)
                        except Exception:
                            pass
                        return None

                    # Perform conversion
                    pdf_path = converter.convert_to_pdf(original_file_path)

                    # Clean up original file
                    try:
                        os.unlink(original_file_path)
                    except Exception:
                        pass

                    # Display conversion result
                    pdf_size = Path(pdf_path).stat().st_size
                    st.success("✅ Successfully converted to PDF!")
                    st.info(
                        f"📑 **PDF File:** {Path(pdf_path).name} ({format_file_size(pdf_size)})"
                    )

                    # Return JSON structure with paper_path for consistency
                    import json
                    return json.dumps({"paper_path": str(pdf_path)})

                except Exception as e:
                    st.error(f"❌ PDF conversion failed: {str(e)}")
                    st.warning("💡 You can try:")
                    st.markdown("- Converting the file to PDF manually")
                    st.markdown("- Using a different file format")
                    st.markdown("- Checking if the file is corrupted")

                    # Clean up original file
                    try:
                        os.unlink(original_file_path)
                    except Exception:
                        pass
                    return None

        except Exception as e:
            st.error(f"❌ Failed to process uploaded file: {str(e)}")
            return None

    return None


def url_input_component(task_counter: int) -> Optional[str]:
    """
    URL input component

    Args:
        task_counter: Task counter

    Returns:
        URL or None
    """
    url_input = st.text_input(
        "Enter paper URL",
        placeholder="https://arxiv.org/abs/..., https://ieeexplore.ieee.org/..., etc.",
        help="Enter a direct link to a research paper (arXiv, IEEE, ACM, etc.)",
        key=f"url_input_{task_counter}",
    )

    if url_input:
        # Simple URL validation
        if url_input.startswith(("http://", "https://")):
            st.success(f"✅ URL entered: {url_input}")
            return url_input
        else:
            st.warning("⚠️ Please enter a valid URL starting with http:// or https://")
            return None

    return None


def requirement_analysis_mode_selector(task_counter: int) -> str:
    """
    Requirement analysis mode selector

    Args:
        task_counter: Task counter

    Returns:
        Selected mode ("direct" or "guided")
    """
    st.markdown(
        """
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                border-radius: 10px;
                padding: 15px;
                margin-bottom: 15px;
                border-left: 4px solid #00ff88;">
        <h4 style="color: white; margin: 0 0 10px 0; font-size: 1.1rem;">
            🎯 Choose Your Input Mode
        </h4>
        <p style="color: #e0f7fa; margin: 0; font-size: 0.9rem;">
            Select how you'd like to provide your requirements
        </p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    mode = st.radio(
        "Input mode:",
        ["🚀 Direct Input", "🧠 Guided Analysis"],
        index=0
        if st.session_state.get("requirement_analysis_mode", "direct") == "direct"
        else 1,
        horizontal=True,
        help="Direct: Enter requirements directly. Guided: AI asks questions to help you clarify needs.",
        key=f"req_mode_{task_counter}",
    )

    return "direct" if mode.startswith("🚀") else "guided"


def requirement_questions_component(
    questions: List[Dict], task_counter: int
) -> Dict[str, str]:
    """
    Requirement questions display and answer collection component

    Args:
        questions: Question list
        task_counter: Task counter

    Returns:
        User answer dictionary
    """
    st.markdown(
        """
    <div style="background: linear-gradient(135deg, #ff9a9e 0%, #fecfef 50%, #fecfef 100%);
                border-radius: 10px;
                padding: 15px;
                margin-bottom: 20px;
                border-left: 4px solid #ff6b6b;">
        <h4 style="color: #2d3748; margin: 0 0 10px 0; font-size: 1.1rem;">
            📝 Help Us Understand Your Needs Better
        </h4>
        <p style="color: #4a5568; margin: 0; font-size: 0.9rem;">
            Please answer the following questions to help us generate better code. You can skip any question.
        </p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    answers = {}

    for i, question in enumerate(questions):
        with st.expander(
            f"📋 {question.get('category', 'Question')} - {question.get('importance', 'Medium')} Priority",
            expanded=i < 3,
        ):
            st.markdown(f"**{question['question']}**")

            if question.get("hint"):
                st.info(f"💡 {question['hint']}")

            answer = st.text_area(
                "Your answer:",
                placeholder="Enter your answer here, or leave blank to skip...",
                height=80,
                key=f"answer_{i}_{task_counter}",
            )

            if answer and answer.strip():
                answers[str(i)] = answer.strip()

    st.markdown("---")
    st.info(f"📊 You've answered {len(answers)} out of {len(questions)} questions.")

    return answers


def requirement_summary_component(summary: str, task_counter: int) -> bool:
    """
    Requirement summary display and confirmation component

    Args:
        summary: Requirement summary document
        task_counter: Task counter

    Returns:
        Whether user confirms requirements
    """
    st.markdown(
        """
    <div style="background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%);
                border-radius: 10px;
                padding: 15px;
                margin-bottom: 20px;
                border-left: 4px solid #38b2ac;">
        <h4 style="color: #2d3748; margin: 0 0 10px 0; font-size: 1.1rem;">
            📋 Detailed Requirements Summary
        </h4>
        <p style="color: #4a5568; margin: 0; font-size: 0.9rem;">
            Based on your input, here's the detailed requirements document we've generated.
        </p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # Display requirement summary
    with st.expander("📖 View Detailed Requirements", expanded=True):
        st.markdown(summary)

    # Confirmation options
    st.markdown("### 🎯 Next Steps")

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button(
            "✅ Looks Good, Proceed",
            type="primary",
            use_container_width=True,
            key=f"confirm_{task_counter}",
        ):
            # Mark requirements as confirmed, prepare to enter code generation
            st.session_state.requirements_confirmed = True
            return True

    with col2:
        if st.button(
            "✏️ Edit Requirements",
            type="secondary",
            use_container_width=True,
            key=f"edit_{task_counter}",
        ):
            # Enter editing mode
            st.session_state.requirement_analysis_step = "editing"
            st.session_state.edit_feedback = ""
            st.rerun()

    with col3:
        if st.button(
            "🔄 Start Over", use_container_width=True, key=f"restart_{task_counter}"
        ):
            # Complete reset
            st.session_state.requirement_analysis_mode = "direct"
            st.session_state.requirement_analysis_step = "input"
            st.session_state.generated_questions = []
            st.session_state.user_answers = {}
            st.session_state.detailed_requirements = ""
            st.rerun()

    return False


def requirement_editing_component(current_requirements: str, task_counter: int) -> bool:
    """
    Interactive requirement editing component

    Args:
        current_requirements: Current requirement document content
        task_counter: Task counter

    Returns:
        Whether editing is completed
    """
    st.markdown(
        """
    <div style="background: linear-gradient(135deg, #ffeaa7 0%, #fab1a0 100%);
                border-radius: 10px;
                padding: 15px;
                margin-bottom: 20px;
                border-left: 4px solid #e17055;">
        <h4 style="color: #2d3748; margin: 0 0 10px 0; font-size: 1.1rem;">
            ✏️ Edit Requirements Document
        </h4>
        <p style="color: #4a5568; margin: 0; font-size: 0.9rem;">
            Review the current requirements and tell us how you'd like to modify them.
        </p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # Display current requirements
    st.markdown("### 📋 Current Requirements")
    with st.expander("📖 View Current Requirements Document", expanded=True):
        st.markdown(current_requirements)

    # Ask for modification feedback
    st.markdown("### 💭 How would you like to modify the requirements?")
    st.markdown("Please describe your changes, additions, or corrections:")

    edit_feedback = st.text_area(
        "Your modification request:",
        value=st.session_state.edit_feedback,
        placeholder="For example:\n- Add user authentication feature\n- Change database from MySQL to PostgreSQL",
        height=120,
        key=f"edit_feedback_{task_counter}",
    )

    # Update session state
    st.session_state.edit_feedback = edit_feedback

    # Action buttons
    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button(
            "🔄 Apply Changes",
            type="primary",
            use_container_width=True,
            key=f"apply_edit_{task_counter}",
        ):
            if edit_feedback.strip():
                # Start requirement modification process
                st.session_state.requirements_editing = True
                st.info("🔄 Processing your modification request...")
                return True
            else:
                st.warning("Please provide your modification request first.")

    with col2:
        if st.button(
            "↩️ Back to Summary",
            type="secondary",
            use_container_width=True,
            key=f"back_summary_{task_counter}",
        ):
            # Go back to summary view
            st.session_state.requirement_analysis_step = "summary"
            st.session_state.edit_feedback = ""
            st.rerun()

    with col3:
        if st.button(
            "🔄 Start Over",
            use_container_width=True,
            key=f"restart_edit_{task_counter}",
        ):
            # Complete reset
            st.session_state.requirement_analysis_mode = "direct"
            st.session_state.requirement_analysis_step = "input"
            st.session_state.generated_questions = []
            st.session_state.user_answers = {}
            st.session_state.detailed_requirements = ""
            st.session_state.edit_feedback = ""
            st.rerun()

    return False


def chat_input_component(task_counter: int) -> Optional[str]:
    """
    Enhanced chat input component with requirement analysis support

    Args:
        task_counter: Task counter

    Returns:
        User coding requirements or None
    """
    # Select input mode
    selected_mode = requirement_analysis_mode_selector(task_counter)

    # Update requirement analysis mode
    st.session_state.requirement_analysis_mode = selected_mode

    if selected_mode == "direct":
        return _direct_input_component(task_counter)
    else:
        return _guided_analysis_component(task_counter)


def _direct_input_component(task_counter: int) -> Optional[str]:
    """Direct input mode component"""
    st.markdown(
        """
    <div style="background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
                border-radius: 10px;
                padding: 15px;
                margin-bottom: 20px;
                border-left: 4px solid #4dd0e1;">
        <h4 style="color: white; margin: 0 0 10px 0; font-size: 1.1rem;">
            🚀 Direct Input Mode
        </h4>
        <p style="color: #e0f7fa; margin: 0; font-size: 0.9rem;">
            Describe your coding requirements directly. Our AI will analyze and generate a comprehensive implementation plan.
        </p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # Examples to help users understand what they can input
    with st.expander("💡 See Examples", expanded=False):
        st.markdown("""
        **Academic Research Examples:**
        - "I need to implement a reinforcement learning algorithm for robotic control"
        - "Create a neural network for image classification with attention mechanisms"
        - "Build a natural language processing pipeline for sentiment analysis"

        **Engineering Project Examples:**
        - "Develop a web application for project management with user authentication"
        - "Create a data visualization dashboard for sales analytics"
        - "Build a REST API for a e-commerce platform with database integration"

        **Mixed Project Examples:**
        - "Implement a machine learning model with a web interface for real-time predictions"
        - "Create a research tool with user-friendly GUI for data analysis"
        - "Build a chatbot with both academic evaluation metrics and production deployment"
        """)

    # Main text area for user input
    user_input = st.text_area(
        "Enter your coding requirements:",
        placeholder="""Example: I want to build a web application that can analyze user sentiment from social media posts. The application should have:

1. A user-friendly interface where users can input text or upload files
2. A machine learning backend that performs sentiment analysis
3. Visualization of results with charts and statistics
4. User authentication and data storage
5. REST API for integration with other applications

The system should be scalable and production-ready, with proper error handling and documentation.""",
        height=200,
        help="Describe what you want to build, including functionality, technologies, and any specific requirements",
        key=f"direct_input_{task_counter}",
    )

    if user_input and len(user_input.strip()) > 20:  # Minimum length check
        # Display input summary
        word_count = len(user_input.split())
        char_count = len(user_input)

        st.success(
            f"✅ **Requirements captured!** ({word_count} words, {char_count} characters)"
        )

        # Show a preview of what will be analyzed
        with st.expander("📋 Preview your requirements", expanded=False):
            st.text_area(
                "Your input:",
                user_input,
                height=100,
                disabled=True,
                key=f"direct_preview_{task_counter}",
            )

        return user_input.strip()

    elif user_input and len(user_input.strip()) <= 20:
        st.warning(
            "⚠️ Please provide more detailed requirements (at least 20 characters)"
        )
        return None

    return None


def _guided_analysis_component(task_counter: int) -> Optional[str]:
    """Guided analysis mode component"""

    # Check if requirements are confirmed, if confirmed return detailed requirements directly
    if st.session_state.get("requirements_confirmed", False):
        detailed_requirements = st.session_state.get("detailed_requirements", "")
        if detailed_requirements:
            # Show confirmation message and return requirements for processing
            st.success("🎉 Requirement analysis completed! Starting code generation...")
            st.info(
                "🔄 Automatically proceeding to code generation based on your confirmed requirements."
            )
            return detailed_requirements

    st.markdown(
        """
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                border-radius: 10px;
                padding: 15px;
                margin-bottom: 20px;
                border-left: 4px solid #00ff88;">
        <h4 style="color: white; margin: 0 0 10px 0; font-size: 1.1rem;">
            🧠 Guided Analysis Mode
        </h4>
        <p style="color: #e0f7fa; margin: 0; font-size: 0.9rem;">
            Let our AI guide you through a series of questions to better understand your requirements.
        </p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # Check current step
    current_step = st.session_state.get("requirement_analysis_step", "input")

    if current_step == "input":
        return _guided_input_step(task_counter)
    elif current_step == "questions":
        return _guided_questions_step(task_counter)
    elif current_step == "summary":
        return _guided_summary_step(task_counter)
    elif current_step == "editing":
        return _guided_editing_step(task_counter)
    else:
        # Reset to initial state
        st.session_state.requirement_analysis_step = "input"
        st.rerun()


def _guided_input_step(task_counter: int) -> Optional[str]:
    """Initial input step for guided mode"""
    st.markdown("### 📝 Step 1: Tell us your basic idea")

    user_input = st.text_area(
        "What would you like to build? (Brief description is fine)",
        placeholder="Example: A web app for sentiment analysis of social media posts",
        height=120,
        help="Don't worry about details - we'll ask specific questions next!",
        key=f"guided_input_{task_counter}",
    )

    if user_input and len(user_input.strip()) > 10:
        col1, col2 = st.columns([3, 1])

        with col1:
            st.info(f"📝 Initial idea captured: {len(user_input.split())} words")

        with col2:
            if st.button(
                "🚀 Generate Questions", type="primary", use_container_width=True
            ):
                # Save initial input and enter question generation step
                st.session_state.initial_requirement = user_input.strip()
                st.session_state.requirement_analysis_step = "questions"
                st.rerun()

    elif user_input and len(user_input.strip()) <= 10:
        st.warning(
            "⚠️ Please provide at least a brief description (more than 10 characters)"
        )

    return None


def _guided_questions_step(task_counter: int) -> Optional[str]:
    """Question answering step for guided mode"""
    st.markdown("### 🤔 Step 2: Answer questions to refine your requirements")

    # Display initial requirements
    with st.expander("📋 Your Initial Idea", expanded=False):
        st.write(st.session_state.get("initial_requirement", ""))

    # Check if questions have been generated
    if not st.session_state.get("generated_questions"):
        st.info("🔄 Generating personalized questions for your project...")

        # Async call needed here, but we show placeholder in UI first
        if st.button("🎯 Generate Questions Now", type="primary"):
            st.session_state.questions_generating = True
            st.rerun()
        return None

    # Display questions and collect answers
    questions = st.session_state.generated_questions
    answers = requirement_questions_component(questions, task_counter)
    st.session_state.user_answers = answers

    # Continue button
    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        if st.button(
            "📋 Generate Detailed Requirements",
            type="primary",
            use_container_width=True,
        ):
            st.session_state.requirement_analysis_step = "summary"
            st.rerun()

    with col1:
        if st.button("⬅️ Back", use_container_width=True):
            st.session_state.requirement_analysis_step = "input"
            st.rerun()

    return None


def _guided_summary_step(task_counter: int) -> Optional[str]:
    """Requirement summary step for guided mode"""
    st.markdown("### 📋 Step 3: Review and confirm your detailed requirements")

    # Check if detailed requirements have been generated
    if not st.session_state.get("detailed_requirements"):
        st.info("🔄 Generating detailed requirements based on your answers...")

        if st.button("📋 Generate Requirements Now", type="primary"):
            st.session_state.requirements_generating = True
            st.rerun()
        return None

    # Display requirement summary and get confirmation
    summary = st.session_state.detailed_requirements
    confirmed = requirement_summary_component(summary, task_counter)

    if confirmed:
        # Return detailed requirements as final input
        return summary

    return None


def _guided_editing_step(task_counter: int) -> Optional[str]:
    """Requirement editing step for guided mode"""
    st.markdown("### ✏️ Step 4: Edit your requirements")

    # Get current requirements
    current_requirements = st.session_state.get("detailed_requirements", "")
    if not current_requirements:
        st.error("No requirements found to edit. Please start over.")
        st.session_state.requirement_analysis_step = "input"
        st.rerun()
        return None

    # Show editing component
    editing_requested = requirement_editing_component(
        current_requirements, task_counter
    )

    if editing_requested:
        # User has provided editing feedback, trigger requirement modification
        st.session_state.requirements_editing = True
        st.rerun()
        return None

    return None


def input_method_selector(task_counter: int) -> tuple[Optional[str], Optional[str]]:
    """
    Input method selector

    Args:
        task_counter: Task counter

    Returns:
        (input_source, input_type)
    """
    st.markdown(
        """
    <h3 style="color: var(--text-primary) !important; font-family: 'Inter', sans-serif !important; font-weight: 600 !important; font-size: 1.5rem !important; margin-bottom: 1rem !important;">
        🚀 Start Processing
    </h3>
    """,
        unsafe_allow_html=True,
    )

    # Input options
    st.markdown(
        """
    <p style="color: var(--text-secondary) !important; font-family: 'Inter', sans-serif !important; font-weight: 500 !important; margin-bottom: 1rem !important;">
        Choose input method:
    </p>
    """,
        unsafe_allow_html=True,
    )

    input_method = st.radio(
        "Choose your input method:",
        ["📁 Upload File", "🌐 Enter URL", "💬 Chat Input"],
        horizontal=True,
        label_visibility="hidden",
        key=f"input_method_{task_counter}",
    )

    input_source = None
    input_type = None

    if input_method == "📁 Upload File":
        input_source = file_input_component(task_counter)
        input_type = "file" if input_source else None
    elif input_method == "🌐 Enter URL":
        input_source = url_input_component(task_counter)
        input_type = "url" if input_source else None
    else:  # Chat input
        input_source = chat_input_component(task_counter)
        input_type = "chat" if input_source else None

    return input_source, input_type


def results_display_component(result: Dict[str, Any], task_counter: int):
    """
    Results display component

    Args:
        result: Processing result
        task_counter: Task counter
    """
    st.markdown("### 📋 Processing Results")

    # Display overall status
    if result.get("status") == "success":
        st.success("🎉 **All workflows completed successfully!**")
    else:
        st.error("❌ **Processing encountered errors**")

    # Create tabs to organize different phase results
    tab1, tab2, tab3, tab4 = st.tabs(
        [
            "📊 Analysis Phase",
            "📥 Download Phase",
            "🔧 Implementation Phase",
            "📁 Generated Files",
        ]
    )

    with tab1:
        st.markdown("#### 📊 Paper Analysis Results")
        with st.expander("Analysis Output Details", expanded=True):
            analysis_result = result.get(
                "analysis_result", "No analysis result available"
            )
            try:
                # Try to parse JSON result for formatted display
                if analysis_result.strip().startswith("{"):
                    parsed_analysis = json.loads(analysis_result)
                    st.json(parsed_analysis)
                else:
                    st.text_area(
                        "Raw Analysis Output",
                        analysis_result,
                        height=300,
                        key=f"analysis_{task_counter}",
                    )
            except Exception:
                st.text_area(
                    "Analysis Output",
                    analysis_result,
                    height=300,
                    key=f"analysis_{task_counter}",
                )

    with tab2:
        st.markdown("#### 📥 Download & Preparation Results")
        with st.expander("Download Process Details", expanded=True):
            download_result = result.get(
                "download_result", "No download result available"
            )
            st.text_area(
                "Download Output",
                download_result,
                height=300,
                key=f"download_{task_counter}",
            )

            # Try to extract file path information
            if "paper_dir" in download_result or "path" in download_result.lower():
                st.info(
                    "💡 **Tip:** Look for file paths in the output above to locate generated files"
                )

    with tab3:
        st.markdown("#### 🔧 Code Implementation Results")
        repo_result = result.get("repo_result", "No implementation result available")

        # Analyze implementation results to extract key information
        if "successfully" in repo_result.lower():
            st.success("✅ Code implementation completed successfully!")
        elif "failed" in repo_result.lower():
            st.warning("⚠️ Code implementation encountered issues")
        else:
            st.info("ℹ️ Code implementation status unclear")

        with st.expander("Implementation Details", expanded=True):
            st.text_area(
                "Repository & Code Generation Output",
                repo_result,
                height=300,
                key=f"repo_{task_counter}",
            )

        # Try to extract generated code directory information
        if "Code generated in:" in repo_result:
            code_dir = repo_result.split("Code generated in:")[-1].strip()
            st.markdown(f"**📁 Generated Code Directory:** `{code_dir}`")

        # Display workflow stage details
        st.markdown("#### 🔄 Workflow Stages Completed")
        stages = [
            ("📄 Document Processing", "✅"),
            ("🔍 Reference Analysis", "✅"),
            ("📋 Plan Generation", "✅"),
            ("📦 Repository Download", "✅"),
            ("🗂️ Codebase Indexing", "✅" if "indexing" in repo_result.lower() else "⚠️"),
            (
                "⚙️ Code Implementation",
                "✅" if "successfully" in repo_result.lower() else "⚠️",
            ),
        ]

        for stage_name, status in stages:
            st.markdown(f"- {stage_name}: {status}")

    with tab4:
        st.markdown("#### 📁 Generated Files & Reports")

        # Try to extract file paths from results
        all_results = (
            f"{result.get('download_result', '')} {result.get('repo_result', '')}"
        )

        # Look for possible file path patterns
        import re

        file_patterns = [
            r"([^\s]+\.txt)",
            r"([^\s]+\.json)",
            r"([^\s]+\.py)",
            r"([^\s]+\.md)",
            r"paper_dir[:\s]+([^\s]+)",
            r"saved to ([^\s]+)",
            r"generated in[:\s]+([^\s]+)",
        ]

        found_files = set()
        for pattern in file_patterns:
            matches = re.findall(pattern, all_results, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    found_files.update(match)
                else:
                    found_files.add(match)

        if found_files:
            st.markdown("**📄 Detected Generated Files:**")
            for file_path in sorted(found_files):
                if file_path and len(file_path) > 3:  # Filter out too short matches
                    st.markdown(f"- `{file_path}`")
        else:
            st.info(
                "No specific file paths detected in the output. Check the detailed results above for file locations."
            )

        # Provide option to view raw results
        with st.expander("View Raw Processing Results"):
            st.json(
                {
                    "analysis_result": result.get("analysis_result", ""),
                    "download_result": result.get("download_result", ""),
                    "repo_result": result.get("repo_result", ""),
                    "status": result.get("status", "unknown"),
                }
            )

    # Action buttons
    st.markdown("---")
    col1, col2 = st.columns(2)

    with col1:
        if st.button("🔄 Process New Paper", type="primary", use_container_width=True):
            st.session_state.show_results = False
            st.session_state.last_result = None
            st.session_state.last_error = None
            st.session_state.task_counter += 1
            st.rerun()

    with col2:
        if st.button("💾 Export Results", type="secondary", use_container_width=True):
            # Create result export
            export_data = {
                "timestamp": datetime.now().isoformat(),
                "processing_results": result,
                "status": result.get("status", "unknown"),
            }
            st.download_button(
                label="📄 Download Results JSON",
                data=json.dumps(export_data, indent=2, ensure_ascii=False),
                file_name=f"paper_processing_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
                use_container_width=True,
            )


def progress_display_component():
    """
    Progress display component

    Returns:
        (progress_bar, status_text)
    """
    # Display processing progress title
    st.markdown("### 📊 Processing Progress")

    # Create progress container
    progress_container = st.container()

    with progress_container:
        # Add custom CSS styles
        st.markdown(
            """
        <style>
        .progress-container {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 15px;
            padding: 20px;
            margin: 10px 0;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        .progress-steps {
            display: flex;
            justify-content: space-between;
            margin-bottom: 15px;
            flex-wrap: wrap;
        }
        .progress-step {
            background: rgba(255,255,255,0.1);
            border-radius: 10px;
            padding: 8px 12px;
            margin: 2px;
            color: white;
            font-size: 0.8rem;
            font-weight: 500;
            border: 2px solid transparent;
            transition: all 0.3s ease;
        }
        .progress-step.active {
            background: rgba(255,255,255,0.3);
            border-color: #00ff88;
            box-shadow: 0 0 15px rgba(0,255,136,0.3);
        }
        .progress-step.completed {
            background: rgba(0,255,136,0.2);
            border-color: #00ff88;
        }
        .status-text {
            color: white;
            font-weight: 600;
            font-size: 1.1rem;
            margin: 10px 0;
            text-align: center;
        }
        </style>
        """,
            unsafe_allow_html=True,
        )

        st.markdown('<div class="progress-container">', unsafe_allow_html=True)

        # Create step indicator
        st.markdown(
            """
        <div class="progress-steps">
            <div class="progress-step" id="step-init">🚀 Initialize</div>
            <div class="progress-step" id="step-analyze">📊 Analyze</div>
            <div class="progress-step" id="step-download">📥 Download</div>
            <div class="progress-step" id="step-references">🔍 References</div>
            <div class="progress-step" id="step-plan">📋 Plan</div>
            <div class="progress-step" id="step-repos">📦 Repos</div>
            <div class="progress-step" id="step-index">🗂️ Index</div>
            <div class="progress-step" id="step-implement">⚙️ Implement</div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        # Create progress bar and status text
        progress_bar = st.progress(0)
        status_text = st.empty()

        st.markdown("</div>", unsafe_allow_html=True)

    return progress_bar, status_text


def enhanced_progress_display_component(
    enable_indexing: bool = True, chat_mode: bool = False
):
    """
    Enhanced progress display component

    Args:
        enable_indexing: Whether indexing is enabled
        chat_mode: Whether in chat mode (user requirements input)

    Returns:
        (progress_bar, status_text, step_indicator, workflow_steps)
    """
    # Display processing progress title
    if chat_mode:
        st.markdown("### 💬 AI Chat Planning - Requirements to Code Workflow")
    elif enable_indexing:
        st.markdown("### 🚀 AI Research Engine - Full Processing Workflow")
    else:
        st.markdown(
            "### ⚡ AI Research Engine - Fast Processing Workflow (Indexing Disabled)"
        )

    # Create progress container
    progress_container = st.container()

    with progress_container:
        # Workflow step definitions - adjust based on mode and indexing toggle
        if chat_mode:
            # Chat mode - simplified workflow for user requirements
            workflow_steps = [
                ("🚀", "Initialize", "Setting up chat engine"),
                ("💬", "Planning", "Analyzing requirements"),
                ("🏗️", "Setup", "Creating workspace"),
                ("📝", "Save Plan", "Saving implementation plan"),
                ("⚙️", "Implement", "Generating code"),
            ]
        elif enable_indexing:
            workflow_steps = [
                ("🚀", "Initialize", "Setting up AI engine"),
                ("📊", "Analyze", "Analyzing paper content"),
                ("📥", "Download", "Processing document"),
                (
                    "📋",
                    "Plan",
                    "Generating code plan",
                ),  # Phase 3: code planning orchestration
                (
                    "🔍",
                    "References",
                    "Analyzing references",
                ),  # Phase 4: now conditional
                ("📦", "Repos", "Downloading repositories"),  # Phase 5: GitHub download
                ("🗂️", "Index", "Building code index"),  # Phase 6: code indexing
                ("⚙️", "Implement", "Implementing code"),  # Phase 7: code implementation
            ]
        else:
            # Fast mode - skip References, Repos and Index steps
            workflow_steps = [
                ("🚀", "Initialize", "Setting up AI engine"),
                ("📊", "Analyze", "Analyzing paper content"),
                ("📥", "Download", "Processing document"),
                (
                    "📋",
                    "Plan",
                    "Generating code plan",
                ),  # Phase 3: code planning orchestration
                (
                    "⚙️",
                    "Implement",
                    "Implementing code",
                ),  # Jump directly to implementation
            ]

        # Display step grid with fixed layout
        # Use a maximum of 8 columns for consistent sizing
        max_cols = 8
        cols = st.columns(max_cols)
        step_indicators = []

        # Calculate column spacing for centering steps
        total_steps = len(workflow_steps)
        if total_steps <= max_cols:
            # Center the steps when fewer than max columns
            start_col = (max_cols - total_steps) // 2
        else:
            start_col = 0

        for i, (icon, title, desc) in enumerate(workflow_steps):
            col_index = start_col + i if total_steps <= max_cols else i
            if col_index < max_cols:
                with cols[col_index]:
                    step_placeholder = st.empty()
                    step_indicators.append(step_placeholder)
                    step_placeholder.markdown(
                        f"""
                    <div style="
                        text-align: center;
                        padding: 12px 8px;
                        border-radius: 12px;
                        background: rgba(255,255,255,0.05);
                        margin: 5px 2px;
                        border: 2px solid transparent;
                        min-height: 90px;
                        display: flex;
                        flex-direction: column;
                        justify-content: center;
                        align-items: center;
                        box-sizing: border-box;
                    ">
                        <div style="font-size: 1.5rem; margin-bottom: 4px;">{icon}</div>
                        <div style="font-size: 0.75rem; font-weight: 600; line-height: 1.2; margin-bottom: 2px;">{title}</div>
                        <div style="font-size: 0.6rem; color: #888; line-height: 1.1; text-align: center;">{desc}</div>
                    </div>
                    """,
                        unsafe_allow_html=True,
                    )

        # Create main progress bar
        st.markdown("#### Overall Progress")
        progress_bar = st.progress(0)

        # Status text display
        status_text = st.empty()

        # Display mode information
        if not enable_indexing:
            st.info(
                "⚡ Fast Mode: Reference analysis, GitHub repository download and codebase indexing are disabled for faster processing."
            )

    return progress_bar, status_text, step_indicators, workflow_steps


def update_step_indicator(
    step_indicators, workflow_steps, current_step: int, status: str = "active"
):
    """
    Update step indicator

    Args:
        step_indicators: Step indicator list
        workflow_steps: Workflow steps definition
        current_step: Current step index
        status: Status ("active", "completed", "error")
    """
    status_colors = {
        "pending": ("rgba(255,255,255,0.05)", "transparent", "#888"),
        "active": ("rgba(255,215,0,0.2)", "#ffd700", "#fff"),
        "completed": ("rgba(0,255,136,0.2)", "#00ff88", "#fff"),
        "error": ("rgba(255,99,99,0.2)", "#ff6363", "#fff"),
    }

    for i, (icon, title, desc) in enumerate(workflow_steps):
        if i < current_step:
            bg_color, border_color, text_color = status_colors["completed"]
            display_icon = "✅"
        elif i == current_step:
            bg_color, border_color, text_color = status_colors[status]
            display_icon = icon
        else:
            bg_color, border_color, text_color = status_colors["pending"]
            display_icon = icon

        step_indicators[i].markdown(
            f"""
        <div style="
            text-align: center;
            padding: 12px 8px;
            border-radius: 12px;
            background: {bg_color};
            margin: 5px 2px;
            border: 2px solid {border_color};
            color: {text_color};
            transition: all 0.3s ease;
            box-shadow: {f'0 0 15px {border_color}30' if i == current_step else 'none'};
            min-height: 90px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            box-sizing: border-box;
        ">
            <div style="font-size: 1.5rem; margin-bottom: 4px;">{display_icon}</div>
            <div style="font-size: 0.75rem; font-weight: 600; line-height: 1.2; margin-bottom: 2px;">{title}</div>
            <div style="font-size: 0.6rem; opacity: 0.8; line-height: 1.1; text-align: center;">{desc}</div>
        </div>
        """,
            unsafe_allow_html=True,
        )


def footer_component():
    """Footer component"""
    st.markdown("---")
    st.markdown(
        """
    <div style="text-align: center; color: #666; padding: 2rem;">
        <p>🧬 <strong>DeepCode</strong> | Open-Source Code Agent | Data Intelligence Lab @ HKU |
        <a href="https://github.com/your-repo" target="_blank" style="color: var(--neon-blue);">GitHub</a></p>
        <p>⚡ Revolutionizing Research Reproducibility • Multi-Agent Architecture • Automated Code Generation</p>
        <p><small>💡 Join our growing community in building the future of automated research reproducibility</small></p>
    </div>
    """,
        unsafe_allow_html=True,
    )


def format_file_size(size_bytes: int) -> str:
    """
    Format file size

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted file size
    """
    if size_bytes == 0:
        return "0B"
    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.1f}{size_names[i]}"
