"""
Streamlit UI Components Module

Contains all reusable UI components
"""

from ast import Not
from pydoc import doc
import streamlit as st
import sys
from typing import Dict, Any, Optional
from datetime import datetime
import json


def display_header():
    """Display application header"""
    st.markdown(
        """
    <div class="main-header">
        <h1>🧬 DeepCode</h1>
        <h3>OPEN-SOURCE CODE AGENT</h3>
        <p>⚡ DATA INTELLIGENCE LAB @ HKU • REVOLUTIONIZING RESEARCH REPRODUCIBILITY ⚡</p>
    </div>
    """,
        unsafe_allow_html=True,
    )


def display_features():
    """Display DeepCode AI Agent capabilities"""
    # AI Agent core capabilities display area - updated to match README content
    st.markdown(
        """
    <div class="ai-capabilities-section">
        <div class="neural-network">
            <div class="neuron pulse-1"></div>
            <div class="neuron pulse-2"></div>
            <div class="neuron pulse-3"></div>
        </div>
        <h2 class="capabilities-title">🧠 Open Agentic Coding Platform</h2>
        <p class="capabilities-subtitle">Advancing Code Generation with Multi-Agent Systems</p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # Core functionality modules - Vertical Layout
    st.markdown(
        """
    <div class="feature-card-vertical primary">
        <div class="card-glow-vertical"></div>
        <div class="feature-header">
            <div class="feature-logo-container">
                <div class="ai-brain-logo">
                    <div class="brain-node node-1"></div>
                    <div class="brain-node node-2"></div>
                    <div class="brain-node node-3"></div>
                    <div class="brain-connection conn-1"></div>
                    <div class="brain-connection conn-2"></div>
                </div>
                <div class="feature-icon-large">🚀</div>
            </div>
            <div class="feature-header-content">
                <h3 class="feature-title-large">Paper2Code: Research-to-Production Pipeline</h3>
                <p class="feature-subtitle">Automated Implementation of Complex Algorithms</p>
            </div>
            <div class="feature-stats">
                <div class="stat-item">
                    <span class="stat-number typing-number">Multi-Modal</span>
                    <span class="stat-label">Analysis</span>
                </div>
                <div class="stat-item">
                    <span class="stat-number typing-number">Production</span>
                    <span class="stat-label">Ready</span>
                </div>
            </div>
        </div>
        <div class="feature-content">
            <div class="content-left">
                <p class="feature-description-large">Multi-modal document analysis engine that extracts algorithmic logic and mathematical models from academic papers, generating optimized implementations with proper data structures while preserving computational complexity characteristics.</p>
                <div class="feature-flow">
                    <div class="flow-step active">
                        <div class="flow-icon">📄</div>
                        <span>Document Parsing</span>
                    </div>
                    <div class="flow-arrow">→</div>
                    <div class="flow-step active">
                        <div class="flow-icon">🧠</div>
                        <span>Algorithm Extraction</span>
                    </div>
                    <div class="flow-arrow">→</div>
                    <div class="flow-step active">
                        <div class="flow-icon">⚡</div>
                        <span>Code Synthesis</span>
                    </div>
                    <div class="flow-arrow">→</div>
                    <div class="flow-step active">
                        <div class="flow-icon">✅</div>
                        <span>Quality Assurance</span>
                    </div>
                </div>
            </div>
            <div class="content-right">
                <div class="code-simulation">
                    <div class="code-header">
                        <span class="code-lang">Python</span>
                        <div class="code-status generating">Generating...</div>
                    </div>
                    <div class="code-lines">
                        <div class="code-line typing">import torch</div>
                        <div class="code-line typing delay-1">import torch.nn as nn</div>
                        <div class="code-line typing delay-2">class ResearchAlgorithm(nn.Module):</div>
                        <div class="code-line typing delay-3">    def __init__(self, config):</div>
                        <div class="code-line typing delay-4">        super().__init__()</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
    <div class="feature-card-vertical secondary">
        <div class="card-glow-vertical"></div>
        <div class="feature-header">
            <div class="feature-logo-container">
                <div class="multi-agent-logo">
                    <div class="agent-node agent-1">🎨</div>
                    <div class="agent-node agent-2">💻</div>
                    <div class="agent-node agent-3">⚡</div>
                    <div class="agent-connection conn-12"></div>
                    <div class="agent-connection conn-23"></div>
                    <div class="agent-connection conn-13"></div>
                </div>
                <div class="feature-icon-large">🎨</div>
            </div>
            <div class="feature-header-content">
                <h3 class="feature-title-large">Text2Web: Automated Prototyping Engine</h3>
                <p class="feature-subtitle">Natural Language to Front-End Code Synthesis</p>
            </div>
            <div class="feature-stats">
                <div class="stat-item">
                    <span class="stat-number typing-number">Intelligent</span>
                    <span class="stat-label">Scaffolding</span>
                </div>
                <div class="stat-item">
                    <span class="stat-number typing-number">Scalable</span>
                    <span class="stat-label">Architecture</span>
                </div>
            </div>
        </div>
        <div class="feature-content">
            <div class="content-left">
                <p class="feature-description-large">Context-aware code generation using fine-tuned language models. Intelligent scaffolding system generating complete application structures including frontend components, maintaining architectural consistency across modules.</p>
                <div class="agent-grid">
                    <div class="agent-card active">
                        <div class="agent-avatar">📝</div>
                        <h4>Intent Understanding</h4>
                        <p>Semantic analysis of requirements</p>
                    </div>
                    <div class="agent-card active">
                        <div class="agent-avatar">🎨</div>
                        <h4>UI Architecture</h4>
                        <p>Component design & structure</p>
                    </div>
                    <div class="agent-card active">
                        <div class="agent-avatar">💻</div>
                        <h4>Code Generation</h4>
                        <p>Functional interface creation</p>
                    </div>
                    <div class="agent-card active">
                        <div class="agent-avatar">✨</div>
                        <h4>Quality Assurance</h4>
                        <p>Automated testing & validation</p>
                    </div>
                </div>
            </div>
            <div class="content-right">
                <div class="collaboration-viz">
                    <div class="collaboration-center">
                        <div class="center-node">🎯</div>
                        <span>Web Application</span>
                    </div>
                    <div class="collaboration-agents">
                        <div class="collab-agent agent-pos-1">
                            <div class="pulse-ring"></div>
                            📝
                        </div>
                        <div class="collab-agent agent-pos-2">
                            <div class="pulse-ring"></div>
                            🏗️
                        </div>
                        <div class="collab-agent agent-pos-3">
                            <div class="pulse-ring"></div>
                            ⚙️
                        </div>
                        <div class="collab-agent agent-pos-4">
                            <div class="pulse-ring"></div>
                            🧪
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
    <div class="feature-card-vertical accent">
        <div class="card-glow-vertical"></div>
        <div class="feature-header">
            <div class="feature-logo-container">
                <div class="future-logo">
                    <div class="orbit orbit-1">
                        <div class="orbit-node">⚙️</div>
                    </div>
                    <div class="orbit orbit-2">
                        <div class="orbit-node">🔧</div>
                    </div>
                    <div class="orbit-center">🚀</div>
                </div>
                <div class="feature-icon-large">⚙️</div>
            </div>
            <div class="feature-header-content">
                <h3 class="feature-title-large">Text2Backend: Scalable Architecture Generator</h3>
                <p class="feature-subtitle">Intelligent Server-Side Development</p>
            </div>
            <div class="feature-stats">
                <div class="stat-item">
                    <span class="stat-number typing-number">Database</span>
                    <span class="stat-label">Integration</span>
                </div>
                <div class="stat-item">
                    <span class="stat-number typing-number">API</span>
                    <span class="stat-label">Endpoints</span>
                </div>
            </div>
        </div>
        <div class="feature-content">
            <div class="content-left">
                <p class="feature-description-large">Generates efficient, scalable backend systems with database schemas, API endpoints, and microservices architecture. Uses dependency analysis to ensure scalable architecture from initial generation with comprehensive testing.</p>
                <div class="vision-demo">
                    <div class="demo-input">
                        <div class="input-icon">💬</div>
                        <div class="input-text typing">"Build a scalable e-commerce API with user authentication and payment processing"</div>
                    </div>
                    <div class="demo-arrow">⬇️</div>
                    <div class="demo-output">
                        <div class="output-items">
                            <div class="output-item">🏗️ Microservices Architecture</div>
                            <div class="output-item">🔒 Authentication & Security</div>
                            <div class="output-item">🗄️ Database Schema Design</div>
                            <div class="output-item">📊 API Documentation & Testing</div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="content-right">
                <div class="future-timeline">
                    <div class="timeline-item completed">
                        <div class="timeline-marker">✅</div>
                        <div class="timeline-content">
                            <h4>API Design</h4>
                            <p>RESTful endpoints</p>
                        </div>
                    </div>
                    <div class="timeline-item completed">
                        <div class="timeline-marker">✅</div>
                        <div class="timeline-content">
                            <h4>Database Layer</h4>
                            <p>Schema & relationships</p>
                        </div>
                    </div>
                    <div class="timeline-item active">
                        <div class="timeline-marker">🔄</div>
                        <div class="timeline-content">
                            <h4>Security Layer</h4>
                            <p>Authentication & authorization</p>
                        </div>
                    </div>
                    <div class="timeline-item future">
                        <div class="timeline-marker">🚀</div>
                        <div class="timeline-content">
                            <h4>Deployment</h4>
                            <p>CI/CD integration</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
    <div class="feature-card-vertical tech">
        <div class="card-glow-vertical"></div>
        <div class="feature-header">
            <div class="feature-logo-container">
                <div class="opensource-logo">
                    <div class="github-stars">
                        <div class="star star-1">📄</div>
                        <div class="star star-2">🤖</div>
                        <div class="star star-3">⚡</div>
                    </div>
                    <div class="community-nodes">
                        <div class="community-node">🧠</div>
                        <div class="community-node">🔍</div>
                        <div class="community-node">⚙️</div>
                    </div>
                </div>
                <div class="feature-icon-large">🎯</div>
            </div>
            <div class="feature-header-content">
                <h3 class="feature-title-large">CodeRAG Integration System</h3>
                <p class="feature-subtitle">Advanced Multi-Agent Orchestration</p>
            </div>
            <div class="feature-stats">
                <div class="stat-item">
                    <span class="stat-number typing-number">Global</span>
                    <span class="stat-label">Code Analysis</span>
                </div>
                <div class="stat-item">
                    <span class="stat-number typing-number">Intelligent</span>
                    <span class="stat-label">Orchestration</span>
                </div>
            </div>
        </div>
        <div class="feature-content">
            <div class="content-left">
                <p class="feature-description-large">Advanced retrieval-augmented generation combining semantic vector embeddings with graph-based dependency analysis. Central orchestrating agent coordinates specialized agents with dynamic task planning and intelligent memory management.</p>
                <div class="community-features">
                    <div class="community-feature">
                        <div class="feature-icon-small">🧠</div>
                        <div class="feature-text">
                            <h4>Intelligent Orchestration</h4>
                            <p>Central decision-making with dynamic planning algorithms</p>
                        </div>
                    </div>
                    <div class="community-feature">
                        <div class="feature-icon-small">🔍</div>
                        <div class="feature-text">
                            <h4>CodeRAG System</h4>
                            <p>Semantic analysis with dependency graph mapping</p>
                        </div>
                    </div>
                    <div class="community-feature">
                        <div class="feature-icon-small">⚡</div>
                        <div class="feature-text">
                            <h4>Quality Assurance</h4>
                            <p>Automated testing, validation, and documentation</p>
                        </div>
                    </div>
                </div>
            </div>
            <div class="content-right">
                <div class="tech-ecosystem">
                    <div class="ecosystem-center">
                        <div class="center-logo">🧠</div>
                        <span>Multi-Agent Engine</span>
                    </div>
                    <div class="ecosystem-ring">
                        <div class="ecosystem-item item-1">
                            <div class="item-icon">🎯</div>
                            <span>Central Orchestration</span>
                        </div>
                        <div class="ecosystem-item item-2">
                            <div class="item-icon">📝</div>
                            <span>Intent Understanding</span>
                        </div>
                        <div class="ecosystem-item item-3">
                            <div class="item-icon">🔍</div>
                            <span>Code Mining & Indexing</span>
                        </div>
                        <div class="ecosystem-item item-4">
                            <div class="item-icon">🧬</div>
                            <span>Code Generation</span>
                        </div>
                    </div>
                </div>
            </div>
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
                return original_file_path

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

                    return str(pdf_path)

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

def tech_input_component(task_counter: int) -> Optional[str]:
    """"Technical implementation input component, including a doc file (either file or URL) and a description of the technique to be implemented."""
    
    input_items = {
        "file": None,
        "url": None,
        "description": None,
    }
    
    st.markdown(
        """
    <div style="background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
                border-radius: 10px;
                padding: 15px;
                margin-bottom: 20px;
                border-left: 4px solid #4dd0e1;">
        <h4 style="color: white; margin: 0 0 10px 0; font-size: 1.1rem;">
            ⚙️ Describe the Technique to Implement
        </h4>
        <p style="color: #e0f7fa; margin: 0; font-size: 0.9rem;">
            Provide a detailed description of the technique you want to implement, including any specific requirements or constraints. If needed, you can up load a document file (PDF, DOCX, etc.) that contains the technical details, or provide a URL to a document that describes the technique.
        </p>
    </div>
    """,
        unsafe_allow_html=True,
    )
    # Part-1: the user requirement
    user_input = st.text_area(
        "Enter your technical implementation requirements:",
        placeholder="Example: According to the API docuemntation, I want to implement a technique that involves...",
        height=200,
        help="Describe the technique you want to implement, including functionality, technologies, and any specific requirements",
        key=f"tech_input_{task_counter}",
    )

    if user_input and len(user_input.strip()) > 20:
        word_count = len(user_input.split())
        char_count = len(user_input)

        st.success(
            f"✅ **Requirements captured!** ({word_count} words, {char_count} characters)"
        )

        with st.expander("📋 Preview your requirements", expanded=False):
            st.text_area(
                "Your input:",
                user_input,
                height=100,
                disabled=True,
                key=f"tech_preview_{task_counter}",
            )

        input_items['description'] = user_input.strip()

    elif user_input and len(user_input.strip()) <= 20:
        st.warning(
            "⚠️ Please provide more detailed requirements (at least 20 characters)"
        )
    
    # Part-2: optionally upload a document file or URL
    doc_input = st.selectbox(
        "Would you like to upload a document file or provide a URL?",
        options=["Upload File", "Provide URL"],
        key=f"doc_input_select_{task_counter}",
    )
    if doc_input == "Upload File":
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
                    input_items['file'] = original_file_path

                # Convert to PDF
                else:
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

                            input_items['file'] = str(pdf_path)

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

            except Exception as e:
                st.error(f"❌ Failed to process uploaded file: {str(e)}")

    else:
        url_input = st.text_input(
            "Enter paper URL",
            placeholder="https://platform.openai.com/docs/..., https://help.aliyun.com/zh/..., etc.",
            help="Enter a direct link to a research paper (arXiv, IEEE, ACM, etc.)",
            key=f"url_input_{task_counter}",
        )

        if url_input:
            # Simple URL validation
            if url_input.startswith(("http://", "https://")):
                st.success(f"✅ URL entered: {url_input}")
                input_items['url'] = url_input
            else:
                st.warning("⚠️ Please enter a valid URL starting with http:// or https://")

    return input_items


def chat_input_component(task_counter: int) -> Optional[str]:
    """
    Chat input component for coding requirements

    Args:
        task_counter: Task counter

    Returns:
        User coding requirements or None
    """
    st.markdown(
        """
    <div style="background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
                border-radius: 10px;
                padding: 15px;
                margin-bottom: 20px;
                border-left: 4px solid #4dd0e1;">
        <h4 style="color: white; margin: 0 0 10px 0; font-size: 1.1rem;">
            💬 Describe Your Coding Requirements
        </h4>
        <p style="color: #e0f7fa; margin: 0; font-size: 0.9rem;">
            Tell us what you want to build. Our AI will analyze your requirements and generate a comprehensive implementation plan.
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
        key=f"chat_input_{task_counter}",
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
                key=f"preview_{task_counter}",
            )

        return user_input.strip()

    elif user_input and len(user_input.strip()) <= 20:
        st.warning(
            "⚠️ Please provide more detailed requirements (at least 20 characters)"
        )
        return None

    return None


def input_method_selector(task_counter: int) -> tuple[Optional[str | dict[str, Optional[str]]], Optional[str]]:
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
        ["📁 Upload File", "🌐 Enter URL", "💬 Chat Input", "⚙️ Technique Implementation"],
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
    elif input_method == "💬 Chat Input":
        input_source = chat_input_component(task_counter)
        input_type = "chat" if input_source else None
    elif input_method == "⚙️ Technique Implementation":
        input_source = tech_input_component(task_counter)
        input_type = "technique" if input_source else None
    else:
        raise NotImplementedError

    print(f"Input source: {input_source}, Input type: {input_type}")
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
