# -*- coding: utf-8 -*-
"""
Streamlit UI Components - Cyber Edition
Contains all reusable UI components with new styling plus
the operational widgets required by the handlers.
"""

from __future__ import annotations

import html
import base64
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import streamlit as st

from utils.cross_platform_file_handler import get_file_handler

BASE_DIR = Path(__file__).resolve().parents[1]
ICON_DIR = BASE_DIR / "assets" / "icons"


@lru_cache(maxsize=64)
def _icon_data_uri(name: str) -> str:
    path = ICON_DIR / f"{name}.png"
    if not path.exists():
        return ""

    try:
        data = path.read_bytes()
    except OSError:
        return ""

    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


def icon_img(name: str, size: int = 32, extra_style: str = "") -> str:
    """
    Render an inline <img> tag for icons stored in assets/icons via data URI.
    """
    data_uri = _icon_data_uri(name)
    if not data_uri:
        return ""
    return f'<img src="{data_uri}" alt="{name}" style="width:{size}px;height:{size}px;{extra_style}"/>'


def clear_guided_answer_inputs():
    """Remove temporary answer widgets from session state."""
    keys_to_delete = [
        key for key in st.session_state.keys() if key.startswith("guided_answer_")
    ]
    for key in keys_to_delete:
        del st.session_state[key]


def display_header():
    """Display the Cyber-styled header"""
    st.markdown(
        """
        <div class="cyber-header">
            <div class="brand-container">
                <div class="brand-title">DEEPCODE</div>
                <div class="brand-subtitle">Autonomous Research & Engineering Matrix</div>
                    </div>
            <div class="status-indicator">
                <div class="status-dot"></div>
                <span>SYSTEM ONLINE</span>
        </div>
    </div>
    """,
        unsafe_allow_html=True,
    )


def display_features():
    """Display feature cards grid"""
    feature_cards = [
        {
            "icon": "feature_synthesis",
            "fallback": "🧬",
            "title": "Neural Synthesis",
            "desc": "Transform research papers directly into executable repositories via multi-agent LLM pipelines.",
        },
        {
            "icon": "feature_hyper",
            "fallback": "⚡",
            "title": "Hyper-Speed Mode",
            "desc": "Acceleration layer that parallelizes retrieval, planning, and implementation for fastest delivery.",
        },
        {
            "icon": "feature_cognition",
            "fallback": "🧠",
            "title": "Cognitive Context",
            "desc": "Semantic memory graphs retain methodology, datasets, and evaluation strategy during reasoning.",
        },
        {
            "icon": "feature_secure",
            "fallback": "🛡️",
            "title": "Secure Sandbox(Coming Soon)",
            "desc": "Isolated execution & validation environment keeps experiments safe and reproducible.",
        },
    ]

    cards_html = ""
    for card in feature_cards:
        icon_markup = icon_img(
            card["icon"],
            48,
            "filter:drop-shadow(0 0 10px rgba(0,242,255,0.4));",
        )
        if not icon_markup:
            icon_markup = f'<span style="font-size:2rem;">{card["fallback"]}</span>'

        cards_html += f"""
        <div class="cyber-card">
            <div class="card-icon">
                {icon_markup}
                </div>
            <div class="card-title">{card['title']}</div>
            <div class="card-desc">{card['desc']}</div>
                </div>
        """

    st.markdown(
        f"""
        <div class="feature-grid">
            {cards_html}
        </div>
    """,
        unsafe_allow_html=True,
    )


def display_status(message: str, status_type: str = "info"):
    """Display status message with cyber styling"""
    colors = {
        "success": "var(--success)",
        "error": "var(--error)",
        "warning": "var(--warning)",
        "info": "var(--primary)",
    }
    color = colors.get(status_type, "var(--primary)")

    st.markdown(
        f"""
        <div style="padding: 1rem; border-left: 3px solid {color}; background: rgba(255,255,255,0.03); margin: 1rem 0; border-radius: 0 4px 4px 0;">
            <span style="color: {color}; font-weight: bold; margin-right: 0.5rem;">[{status_type.upper()}]</span>
            <span style="font-family: var(--font-code);">{message}</span>
    </div>
    """,
        unsafe_allow_html=True,
    )


def _render_step_card(title: str, subtitle: str, state: str) -> str:
    """Return HTML for a workflow step badge."""
    colors = {
        "completed": "var(--success)",
        "active": "var(--primary)",
        "pending": "rgba(255,255,255,0.3)",
        "error": "var(--error)",
    }
    icon = {
        "completed": "✔",
        "active": "➤",
        "pending": "•",
        "error": "!",
    }.get(state, "•")
    color = colors.get(state, "rgba(255,255,255,0.3)")
    return f"""
        <div style="
            border:1px solid rgba(255,255,255,0.08);
            padding:0.75rem;
            border-radius:4px;
            min-height:110px;
            background:rgba(0,0,0,0.15);
        ">
            <div style="font-size:1.2rem;color:{color};">{icon}</div>
            <div style="font-family:var(--font-display);color:white;">{title}</div>
            <div style="font-size:0.8rem;color:rgba(255,255,255,0.5);">{subtitle}</div>
        </div>
    """


def enhanced_progress_display_component(
    enable_indexing: bool, chat_mode: bool
) -> Tuple[Any, Any, List[Any], List[Dict[str, str]]]:
    """
    Render the progress panel required by handlers.handle_processing_workflow.
    """

    if chat_mode:
        workflow_steps = [
            {"title": "INIT", "subtitle": "Boot agents"},
            {"title": "PLAN", "subtitle": "Analyze intent"},
            {"title": "SETUP", "subtitle": "Workspace"},
            {"title": "DRAFT", "subtitle": "Generate plan"},
            {"title": "CODE", "subtitle": "Implement"},
        ]
    elif not enable_indexing:
        workflow_steps = [
            {"title": "INIT", "subtitle": "Load systems"},
            {"title": "ANALYZE", "subtitle": "Parse paper"},
            {"title": "DOWNLOAD", "subtitle": "Collect refs"},
            {"title": "PLAN", "subtitle": "Blueprint"},
            {"title": "CODE", "subtitle": "Implement"},
        ]
    else:
        workflow_steps = [
            {"title": "INIT", "subtitle": "Load systems"},
            {"title": "ANALYZE", "subtitle": "Paper scan"},
            {"title": "DOWNLOAD", "subtitle": "Docs & data"},
            {"title": "PLAN", "subtitle": "Architect"},
            {"title": "REF", "subtitle": "Key refs"},
            {"title": "REPO", "subtitle": "GitHub sync"},
            {"title": "INDEX", "subtitle": "Vectorize"},
            {"title": "CODE", "subtitle": "Implementation"},
        ]

    st.markdown("### 🛰️ Workflow Monitor")
    progress_bar = st.progress(0)
    status_text = st.empty()

    cols = st.columns(len(workflow_steps))
    step_indicators: List[Any] = []
    for col, step in zip(cols, workflow_steps):
        with col:
            placeholder = st.empty()
            placeholder.markdown(
                _render_step_card(step["title"], step["subtitle"], "pending"),
                unsafe_allow_html=True,
            )
            step_indicators.append(placeholder)

    return progress_bar, status_text, step_indicators, workflow_steps


def update_step_indicator(
    step_indicators: List[Any],
    workflow_steps: List[Dict[str, str]],
    current_step: int,
    status: str,
):
    """
    Update the workflow step indicators in-place.
    """
    total_steps = len(workflow_steps)

    for idx, placeholder in enumerate(step_indicators):
        if status == "error" and idx == current_step:
            state = "error"
        elif current_step >= total_steps:
            state = "completed"
        elif idx < current_step:
            state = "completed"
        elif idx == current_step:
            state = "active"
        else:
            state = "pending"

        step = workflow_steps[idx]
        placeholder.markdown(
            _render_step_card(step["title"], step["subtitle"], state),
            unsafe_allow_html=True,
        )


def chat_input_component(task_counter: int = 0) -> Optional[str]:
    """Render modern chat input for guided mode"""
    st.markdown("### 💬 Neural Link Interface")

    user_input = st.chat_input(
        placeholder="Input research directive or query...",
        key=f"chat_input_{task_counter}",
    )
    return user_input


def _save_uploaded_pdf(uploaded_file) -> Optional[str]:
    """Persist uploaded PDF to a temp file and return its path."""
    try:
        file_bytes = uploaded_file.read()
        suffix = Path(uploaded_file.name).suffix or ".pdf"
        handler = get_file_handler()
        temp_path = handler.create_safe_temp_file(
            suffix=suffix, prefix="deepcode_upload_", content=file_bytes
        )
        return str(temp_path)
    except Exception as exc:
        st.error(f"Failed to save uploaded file: {exc}")
        return None


def input_method_selector(task_counter: int) -> Tuple[Optional[str], Optional[str]]:
    """Render the input method selection tabs with modern styling"""

    tab1, tab2, tab3 = st.tabs(["📄 PDF UPLOAD", "🔗 URL LINK", "⚡ QUICK COMMAND"])

    input_source: Optional[str] = None
    input_type: Optional[str] = None

    with tab1:
        st.markdown('<div style="padding:1rem;"></div>', unsafe_allow_html=True)
        uploaded_file = st.file_uploader(
            "Upload Research Paper (PDF)",
            type="pdf",
            key=f"file_uploader_{task_counter}",
        )
        if uploaded_file:
            saved_path = _save_uploaded_pdf(uploaded_file)
            if saved_path:
                st.session_state["uploaded_filename"] = uploaded_file.name
                input_source = saved_path
                input_type = "file"

    with tab2:
        st.markdown('<div style="padding:1rem;"></div>', unsafe_allow_html=True)
        url = st.text_input(
            "ArXiv / GitHub Resource URL",
            placeholder="https://arxiv.org/abs/...",
            key=f"url_input_{task_counter}",
        )
        if url:
            input_source = url.strip()
            input_type = "url"

    with tab3:
        st.markdown('<div style="padding:1rem;"></div>', unsafe_allow_html=True)
        query = st.text_area(
            "Code Specifications / Abstract",
            placeholder="Describe the algorithm or system requirements...",
            height=150,
            key=f"text_input_{task_counter}",
        )
        if query:
            input_source = query.strip()
            input_type = "chat"

    return input_source, input_type


def results_display_component(result: Any, task_counter: int):
    """Display results in a tech-styled container"""

    status = result.get("status", "unknown")
    is_success = status == "success"
    status_label = "Mission Complete" if is_success else "Execution Failed"
    status_color = "var(--success)" if is_success else "var(--error)"
    status_icon = icon_img("status_success" if is_success else "status_error", 56)
    if not status_icon:
        status_icon = "✅" if is_success else "⚠️"
    status_message = (
        "Computation sequence completed successfully."
        if is_success
        else result.get("error", "Unknown error occurred during processing.")
    )

    st.markdown('<div style="height: 2rem;"></div>', unsafe_allow_html=True)
    st.markdown("### 🚀 Operation Result")

    with st.container():
        if is_success:
            st.success("Workflow completed across all stages ✅")
        else:
            st.error("Workflow interrupted. Check the logs below ⚠️")

        col1, col2 = st.columns([2, 1])
        with col1:
            with st.expander("📜 Execution Logs & Metadata", expanded=True):
                st.json(result)

        with col2:
            st.markdown(
                f"""
                <div style="padding: 1.5rem; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; background: rgba(255,255,255,0.02); text-align: center; margin-bottom: 1rem;">
                    <div style="margin-bottom:0.5rem;">{status_icon}</div>
                    <div style="font-family: var(--font-display); font-size: 1.3rem; color: {status_color};">{status_label}</div>
                    <div style="font-size: 0.85rem; color: rgba(255,255,255,0.6); margin-top: 0.3rem;">{status_message}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.download_button(
                label="📥 DOWNLOAD ARTIFACTS" if is_success else "📥 DOWNLOAD LOGS",
                data=str(result),
                file_name=f"deepcode_result_{task_counter}.json",
                mime="application/json",
                use_container_width=True,
            )


def system_status_component():
    """System status check component"""
    st.markdown("### 🔧 System Diagnostics")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 📊 Core Metrics")
        st.info(f"**Python:** {sys.version.split()[0]}")
        st.info(f"**Platform:** {sys.platform}")

    with col2:
        st.markdown("#### ⚙️ Runtime Status")
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                st.success("Event Loop: ACTIVE")
            else:
                st.warning("Event Loop: STANDBY")
        except Exception:
            st.info("Event Loop: MANAGED")


def error_troubleshooting_component():
    """Error troubleshooting component"""
    with st.expander("🛠️ Diagnostics & Troubleshooting", expanded=False):
        st.warning(
            "If you encounter issues, please check your API keys in the sidebar."
        )


def footer_component():
    """Minimal futuristic footer"""
    st.markdown(
        """
        <div style="text-align: center; margin-top: 6rem; padding: 2rem; color: rgba(255,255,255,0.2); font-family: var(--font-code); font-size: 0.7rem; border-top: 1px solid rgba(255,255,255,0.05);">
            DEEPCODE_SYSTEMS // <span style="color: var(--primary);">OPERATIONAL</span> // VERSION 3.0.1
    </div>
    """,
        unsafe_allow_html=True,
    )


def render_sidebar_feed(max_items: int = 12):
    """Render live mission feed inside sidebar."""
    st.markdown("#### 📡 Mission Feed")
    events = list(st.session_state.get("sidebar_events", []))

    col1, col2 = st.columns([1, 1])
    with col1:
        st.caption("Real-time agent telemetry")
    with col2:
        if st.button("Clear Feed", key="sidebar_clear_feed"):
            st.session_state.sidebar_events = []
            events = []
            st.session_state.sidebar_feed_last_cleared = datetime.utcnow().strftime(
                "%H:%M:%S"
            )

    if not events:
        st.caption("Awaiting activity...")
        return

    recent_events = list(reversed(events[-max_items:]))
    for event in recent_events:
        stage = event.get("stage", "STAGE")
        message = html.escape(str(event.get("message", "")))
        timestamp = event.get("timestamp", "--:--:--")
        level = event.get("level", "info")
        extra = event.get("extra")

        st.markdown(
            f"""
            <div class="sidebar-feed-card level-{level}">
                <div class="stage-line">
                    <span class="stage">{stage}</span>
                    <span class="time">{timestamp}</span>
                </div>
                <div class="message">{message}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if isinstance(extra, dict) and extra:
            with st.expander("Details", expanded=False):
                st.json(extra)


def render_system_monitor():
    """Display current backend + command telemetry."""
    st.markdown("#### 🧬 System Monitor")
    processing = st.session_state.get("processing", False)
    mode = st.session_state.get("requirement_analysis_mode", "direct").upper()
    indexing_enabled = st.session_state.get("enable_indexing", True)
    task_counter = st.session_state.get("task_counter", 0)
    last_error = st.session_state.get("last_error")
    events = st.session_state.get("sidebar_events", [])
    latest_event = events[-1] if events else None
    last_stage = latest_event.get("stage") if latest_event else "--"
    last_message = (
        html.escape(str(latest_event.get("message", ""))) if latest_event else ""
    )
    last_progress = (
        latest_event.get("extra", {}).get("progress") if latest_event else None
    )
    state_label = "ACTIVE" if processing else "IDLE"

    st.markdown(
        f"""
        <div class="system-monitor-card">
            <div class="status-grid">
                <div class="status-chip"><span>STATE</span><span>{state_label}</span></div>
                <div class="status-chip"><span>MODE</span><span>{mode}</span></div>
                <div class="status-chip"><span>INDEXING</span><span>{"ON" if indexing_enabled else "OFF"}</span></div>
                <div class="status-chip"><span>TASKS</span><span>{task_counter}</span></div>
            </div>
            <div class="latest-stage">
                <strong>{last_stage if last_stage else "--"}</strong>
                {"· " + str(last_progress) + "%" if last_progress is not None else ""}
                <br/>{last_message or "Awaiting telemetry..."}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if last_error:
        st.warning(f"Last error: {last_error}")


def render_log_viewer(max_lines: int = 50):
    """Display live log stream for current mission in a scrollable container."""
    st.markdown("#### 📁 Live Log Stream")
    logs_dir = BASE_DIR / "logs"
    if not logs_dir.exists():
        st.info("Logs directory not found.")
        return

    log_files = sorted(
        [p for p in logs_dir.glob("*.jsonl") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not log_files:
        st.info("No log files available yet.")
        return

    start_ts = st.session_state.get("workflow_start_time")
    selected_path = None

    waiting_for_new_log = False

    if start_ts:
        # Use a tolerance window: accept logs created within 10 seconds before workflow_start_time
        tolerance = 10.0
        for candidate in log_files:
            file_mtime = candidate.stat().st_mtime
            if file_mtime >= (start_ts - tolerance):
                selected_path = candidate
                break
        if selected_path is None:
            waiting_for_new_log = True
    else:
        prev = st.session_state.get("active_log_file")
        if prev:
            prev_path = Path(prev)
            if prev_path.exists():
                selected_path = prev_path
        if selected_path is None:
            selected_path = log_files[0]

    if waiting_for_new_log:
        st.caption("Waiting for current task log to be created...")
        return

    st.session_state.active_log_file = str(selected_path)

    try:
        content = selected_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        st.error(f"Failed to read {selected_path.name}: {exc}")
        return

    lines = content.splitlines()
    tail_lines = lines[-max_lines:]

    # Show file info
    processing = st.session_state.get("processing", False)
    status_icon = "🔄" if processing else "✅"
    st.caption(f"{status_icon} {selected_path.name} | Last {len(tail_lines)} lines")

    if not tail_lines:
        st.info("Log file is empty.")
        return

    # Build log HTML with scrollable container
    import json

    log_html_parts = []

    for line in tail_lines:
        line = line.strip()
        if not line:
            continue

        try:
            event = json.loads(line)
            timestamp = event.get("timestamp", "")
            level = event.get("level", "INFO")
            message = event.get("message", "")
            namespace = event.get("namespace", "")

            # Color code by level
            if level == "ERROR":
                level_color = "#ff4444"
            elif level == "WARNING":
                level_color = "#ffaa00"
            elif "SUCCESS" in level.upper():
                level_color = "#00ff88"
            else:
                level_color = "#00d4ff"

            # Format display
            time_str = (
                timestamp.split("T")[-1][:12] if "T" in timestamp else timestamp[-12:]
            )
            namespace_short = namespace.split(".")[-1] if namespace else ""

            log_html_parts.append(
                f'<div style="font-family: var(--font-code); font-size: 0.8rem; padding: 0.25rem 0.4rem; '
                f"border-left: 2px solid {level_color}; margin-bottom: 0.2rem; background: rgba(255,255,255,0.02); "
                f'border-radius: 2px;">'
                f'<span style="color: rgba(255,255,255,0.4); font-size: 0.75rem;">{time_str}</span> '
                f'<span style="color: {level_color}; font-weight: 600; font-size: 0.75rem;">[{level}]</span> '
                f'<span style="color: var(--primary); font-size: 0.75rem;">{namespace_short}</span><br/>'
                f'<span style="color: rgba(255,255,255,0.85); margin-left: 0.5rem;">{message[:200]}</span>'
                f"</div>"
            )
        except json.JSONDecodeError:
            # Raw text fallback
            log_html_parts.append(
                f'<div style="font-family: var(--font-code); font-size: 0.75rem; padding: 0.2rem; '
                f'color: rgba(255,255,255,0.6);">{line[:200]}</div>'
            )

    # Render in scrollable container
    full_log_html = f"""
    <div style="max-height: 600px; overflow-y: auto; overflow-x: hidden;
                padding: 0.5rem; background: rgba(0,0,0,0.2); border-radius: 4px;
                border: 1px solid rgba(255,255,255,0.1);">
        {''.join(log_html_parts)}
    </div>
    """

    st.markdown(full_log_html, unsafe_allow_html=True)


def reset_guided_workflow_state(preserve_initial: bool = False):
    """
    Reset guided requirement workflow state machine.
    """
    if preserve_initial:
        initial_text = st.session_state.get(
            "guided_initial_requirement",
            st.session_state.get("initial_requirement", ""),
        )
    else:
        initial_text = ""
        st.session_state.initial_requirement = ""

    st.session_state.guided_initial_requirement = initial_text
    st.session_state.guided_edit_feedback = ""
    st.session_state.requirement_analysis_step = "input"
    st.session_state.generated_questions = []
    st.session_state.user_answers = {}
    st.session_state.detailed_requirements = ""
    st.session_state.questions_generating = False
    st.session_state.requirements_generating = False
    st.session_state.requirements_confirmed = False
    st.session_state.requirements_editing = False
    st.session_state.edit_feedback = ""
    st.session_state.confirmed_requirement_text = None
    clear_guided_answer_inputs()


def requirement_mode_selector() -> str:
    """
    Render the requirement workflow mode selector.
    """
    mode_labels = {"direct": "🚀 Direct Mode", "guided": "🧭 Guided Mode"}
    current_mode = st.session_state.get("requirement_analysis_mode", "direct")

    selection = st.radio(
        "Requirement Intake Mode",
        options=list(mode_labels.keys()),
        index=0 if current_mode != "guided" else 1,
        horizontal=True,
        format_func=lambda key: mode_labels[key],
        key="requirement_mode_selector_radio",
    )

    if selection != current_mode:
        st.session_state.requirement_analysis_mode = selection
        if selection == "direct":
            reset_guided_workflow_state(preserve_initial=False)
        else:
            st.session_state.requirement_analysis_step = "input"

    return selection


def guided_requirement_workflow() -> Tuple[Optional[str], bool]:
    """
    Render the guided requirement analysis workflow.
    """

    st.markdown("### 🧭 Guided Requirement Workflow")

    step = st.session_state.get("requirement_analysis_step", "input")
    st.session_state.setdefault(
        "guided_initial_requirement", st.session_state.get("initial_requirement", "")
    )
    st.session_state.setdefault(
        "guided_edit_feedback", st.session_state.get("edit_feedback", "")
    )

    step_titles = {
        "input": "Step 1 · Describe Requirements",
        "questions": "Step 2 · Answer Guiding Questions",
        "summary": "Step 3 · Review Requirement Document",
        "editing": "Step 4 · Request Changes",
    }
    st.caption(
        f"Current Stage: {step_titles.get(step, 'Step 1 · Describe Requirements')}"
    )

    confirmed_doc = st.session_state.get("confirmed_requirement_text")

    if step == "input":
        st.markdown("#### 1 · Describe your project")
        st.text_area(
            "Describe the product scope, tech stack, performance targets, and constraints:",
            key="guided_initial_requirement",
            height=180,
        )
        initial_text = st.session_state.get("guided_initial_requirement", "")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Generate guiding questions", type="primary"):
                if not initial_text.strip():
                    st.warning("Please enter your project requirements first.")
                else:
                    st.session_state.initial_requirement = initial_text.strip()
                    st.session_state.questions_generating = True
                    st.session_state.requirement_analysis_step = "questions"
                    st.session_state.generated_questions = []
                    st.session_state.user_answers = {}
                    st.session_state.detailed_requirements = ""
                    st.session_state.confirmed_requirement_text = None
                    st.session_state.requirements_generating = False
                    st.session_state.requirements_confirmed = False
                    st.session_state.requirements_editing = False
                    st.session_state.edit_feedback = ""
                    clear_guided_answer_inputs()
                    st.rerun()

        with col2:
            if st.button("Skip Q&A and use current spec", type="secondary"):
                if not initial_text.strip():
                    st.warning("Please enter your project requirements first.")
                else:
                    final_doc = initial_text.strip()
                    st.session_state.initial_requirement = final_doc
                    st.session_state.confirmed_requirement_text = final_doc
                    st.session_state.requirements_confirmed = True
                    st.success(
                        "Current description locked as the requirement document. Implementation will proceed next."
                    )

    elif step == "questions":
        st.markdown("#### 2 · Answer guiding questions")
        if st.session_state.get("questions_generating"):
            st.info("LLM is crafting guiding questions. Please wait...")

        questions = st.session_state.get("generated_questions", [])
        question_ids: List[str] = []

        if not questions:
            st.caption("Guiding questions will appear once generation is complete.")
        else:
            for idx, question in enumerate(questions):
                if isinstance(question, dict):
                    q_id = str(
                        question.get("id")
                        or question.get("question_id")
                        or question.get("qid")
                        or idx
                    )
                    q_text = question.get("question") or question.get("content") or ""
                    category = question.get("category")
                    importance = question.get("importance")
                    hint = question.get("hint")
                else:
                    q_id = str(idx)
                    q_text = str(question)
                    category = importance = hint = None

                question_ids.append(q_id)

                st.markdown(
                    f"**Q{idx + 1}. {q_text or 'Please answer this question'}**"
                )
                meta_parts = [part for part in [category, importance] if part]
                if meta_parts:
                    st.caption(" / ".join(meta_parts))
                if hint:
                    st.caption(f"Hint: {hint}")

                answer_key = f"guided_answer_{idx}"
                if answer_key not in st.session_state:
                    default_answer = st.session_state.user_answers.get(q_id, "")
                    st.session_state[answer_key] = default_answer

                st.text_area("Your answer", key=answer_key, height=100)

        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button(
                "Generate requirement document", type="primary", disabled=not questions
            ):
                answers_payload = {}
                for idx, q_id in enumerate(question_ids):
                    answer_value = st.session_state.get(
                        f"guided_answer_{idx}", ""
                    ).strip()
                    if answer_value:
                        answers_payload[q_id] = answer_value

                st.session_state.user_answers = answers_payload
                st.session_state.requirements_generating = True
                st.session_state.requirement_analysis_step = "summary"
                st.session_state.detailed_requirements = ""
                st.session_state.confirmed_requirement_text = None
                st.session_state.requirements_confirmed = False
                st.rerun()

        with col2:
            if st.button(
                "Generate without answers", type="secondary", disabled=not questions
            ):
                st.session_state.user_answers = {}
                st.session_state.requirements_generating = True
                st.session_state.requirement_analysis_step = "summary"
                st.session_state.detailed_requirements = ""
                st.session_state.confirmed_requirement_text = None
                st.session_state.requirements_confirmed = False
                st.rerun()

        with col3:
            if st.button("Back to Step 1"):
                reset_guided_workflow_state(preserve_initial=True)
                st.rerun()

    elif step == "summary":
        st.markdown("#### 3 · AI-generated requirement document")
        if st.session_state.get("requirements_generating"):
            st.info("Generating requirement document. Please wait...")

        summary = (st.session_state.get("detailed_requirements") or "").strip()

        if summary:
            st.markdown(summary)
            st.download_button(
                "Download requirement document",
                summary,
                file_name="deepcode_requirements.md",
                mime="text/markdown",
                use_container_width=True,
            )
        else:
            st.caption("Waiting for requirement document to be generated...")

        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button(
                "Confirm and start implementation ✅",
                type="primary",
                disabled=not summary,
            ):
                final_doc = summary or st.session_state.get("initial_requirement", "")
                if final_doc.strip():
                    st.session_state.confirmed_requirement_text = final_doc.strip()
                    st.session_state.requirements_confirmed = True
                    st.success(
                        "Requirement document confirmed. Implementation pipeline will start next."
                    )
                else:
                    st.warning("No requirement document available yet.")

        with col2:
            if st.button("Request edits", type="secondary", disabled=not summary):
                st.session_state.requirement_analysis_step = "editing"
                st.session_state.guided_edit_feedback = ""

        with col3:
            if st.button("Restart Q&A", type="secondary"):
                reset_guided_workflow_state(preserve_initial=True)
                st.rerun()

    elif step == "editing":
        st.markdown("#### 4 · Modify requirement document")
        st.text_area(
            "Describe the changes or clarifications you need:",
            key="guided_edit_feedback",
            height=160,
        )
        feedback_value = st.session_state.get("guided_edit_feedback", "")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Submit change request", type="primary"):
                if not feedback_value.strip():
                    st.warning("Please describe the requested changes.")
                else:
                    st.session_state.edit_feedback = feedback_value.strip()
                    st.session_state.requirements_editing = True
                    st.info("Updating requirement document based on your feedback...")

        with col2:
            if st.button("Back to requirement document"):
                st.session_state.requirement_analysis_step = "summary"
                st.session_state.guided_edit_feedback = ""

        if st.session_state.get("requirements_editing"):
            st.info("Requirement document is updating...")

    if confirmed_doc:
        st.success("Requirement document locked. You can start implementation anytime.")

    return (confirmed_doc if confirmed_doc else None, bool(confirmed_doc))


def sidebar_control_panel():
    """Sidebar configuration"""
    with st.sidebar:
        st.markdown(
            """
            <div style="margin-bottom: 2rem; padding-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.1);">
                <h2 style="margin:0; color:white;">CONTROL DECK</h2>
                <div style="font-family:var(--font-code); color:var(--primary); font-size:0.8rem;">// MISSION CONTROL</div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        workflow_start = st.session_state.get("workflow_start_time")

        if workflow_start:
            render_log_viewer()
        else:
            st.info("Awaiting next mission run to stream logs.")
    st.markdown(
        """
            <div style="font-size: 0.7rem; color: rgba(255,255,255,0.3); text-align: center; margin-top: 1rem;">
                © 2024 DeepCode Research
    </div>
    """,
        unsafe_allow_html=True,
    )

    return {}
