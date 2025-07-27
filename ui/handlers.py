"""
Streamlit Event Handlers Module

Contains all event handling and business logic
"""

import asyncio
import time
import os
import traceback
import atexit
import signal
from datetime import datetime
from typing import Dict, Any

import streamlit as st
import nest_asyncio
import concurrent.futures

# Import necessary modules
from mcp_agent.app import MCPApp
from workflows.agent_orchestration_engine import (
    execute_multi_agent_research_pipeline,
    execute_chat_based_planning_pipeline,
)


def _emergency_cleanup():
    """
    Emergency resource cleanup function
    Called when program exits abnormally
    """
    try:
        cleanup_resources()
    except Exception:
        pass  # Silent handling to avoid new exceptions during exit


def _signal_handler(signum, frame):
    """
    Signal handler for program termination signals
    """
    try:
        cleanup_resources()
    except Exception:
        pass
    finally:
        # Restore default signal handling and resend signal
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)


# Register exit cleanup function
atexit.register(_emergency_cleanup)


def _safe_register_signal_handlers():
    """Safely register signal handlers"""
    try:
        # Check if running in main thread
        import threading

        if threading.current_thread() is not threading.main_thread():
            return  # Signal handlers can only be registered in main thread

        # Try to register signal handlers
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)
        if hasattr(signal, "SIGBREAK"):  # Windows
            signal.signal(signal.SIGBREAK, _signal_handler)
    except (AttributeError, OSError, ValueError):
        # Some signals are not available on certain platforms or disabled in some environments
        # This is common in web frameworks like Streamlit
        pass


# Delayed signal handler registration to avoid import-time errors
try:
    _safe_register_signal_handlers()
except Exception:
    # If registration fails, silently ignore and don't affect app startup
    pass


async def process_input_async(
    input_source: str,
    input_type: str,
    enable_indexing: bool = True,
    progress_callback=None,
) -> Dict[str, Any]:
    """
    Process input asynchronously

    Args:
        input_source: Input source
        input_type: Input type
        enable_indexing: Whether to enable indexing functionality
        progress_callback: Progress callback function

    Returns:
        Processing result
    """
    try:
        # Create and use MCP app in the same async context
        app = MCPApp(name="paper_to_code")

        async with app.run() as agent_app:
            logger = agent_app.logger
            context = agent_app.context
            context.config.mcp.servers["filesystem"].args.extend([os.getcwd()])

            # Initialize progress
            if progress_callback:
                if input_type == "chat":
                    progress_callback(
                        5, "🚀 Initializing chat-based planning pipeline..."
                    )
                else:
                    progress_callback(5, "🚀 Initializing AI research engine...")

            # Choose pipeline based on input type
            if input_type == "chat":
                # Use chat-based planning pipeline for user requirements
                repo_result = await execute_chat_based_planning_pipeline(
                    input_source,  # User's coding requirements
                    logger,
                    progress_callback,
                    enable_indexing=enable_indexing,  # Pass indexing control parameter
                )
            else:
                # Use traditional multi-agent research pipeline for files/URLs
                repo_result = await execute_multi_agent_research_pipeline(
                    input_source,
                    logger,
                    progress_callback,
                    enable_indexing=enable_indexing,  # Pass indexing control parameter
                )

            return {
                "analysis_result": "Integrated into complete workflow",
                "download_result": "Integrated into complete workflow",
                "repo_result": repo_result,
                "status": "success",
            }

    except Exception as e:
        error_msg = str(e)
        traceback_msg = traceback.format_exc()

        return {"error": error_msg, "traceback": traceback_msg, "status": "error"}


def run_async_task(coro):
    """
    Helper function to run async tasks

    Args:
        coro: Coroutine object

    Returns:
        Task result
    """
    # Apply nest_asyncio to support nested event loops
    nest_asyncio.apply()

    # Save current Streamlit context
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        from streamlit.runtime.scriptrunner.script_run_context import (
            SCRIPT_RUN_CONTEXT_ATTR_NAME,
        )

        current_ctx = get_script_run_ctx()
        context_available = True
    except ImportError:
        # If Streamlit context modules can't be imported, use fallback method
        current_ctx = None
        context_available = False

    def run_in_new_loop():
        """Run coroutine in new event loop"""
        # Set Streamlit context in new thread (if available)
        if context_available and current_ctx:
            try:
                import threading

                setattr(
                    threading.current_thread(),
                    SCRIPT_RUN_CONTEXT_ATTR_NAME,
                    current_ctx,
                )
            except Exception:
                pass  # Ignore context setting errors

        loop = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(coro)
            return result
        except Exception as e:
            raise e
        finally:
            # Clean up resources
            if loop:
                try:
                    loop.close()
                except Exception:
                    pass
            asyncio.set_event_loop(None)

            # Clean up thread context (if available)
            if context_available:
                try:
                    import threading

                    if hasattr(
                        threading.current_thread(), SCRIPT_RUN_CONTEXT_ATTR_NAME
                    ):
                        delattr(
                            threading.current_thread(), SCRIPT_RUN_CONTEXT_ATTR_NAME
                        )
                except Exception:
                    pass  # Ignore cleanup errors

            # Force garbage collection
            import gc

            gc.collect()

    # Use thread pool to run async task, avoiding event loop conflicts
    executor = None
    try:
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="deepcode_ctx_async"
        )
        future = executor.submit(run_in_new_loop)
        result = future.result(timeout=300)  # 5 minute timeout
        return result
    except concurrent.futures.TimeoutError:
        st.error("Processing timeout after 5 minutes. Please try again.")
        raise TimeoutError("Processing timeout")
    except Exception as e:
        # If thread pool execution fails, try direct execution
        st.warning(f"Threaded async execution failed: {e}, trying direct execution...")
        try:
            # Fallback method: run directly in current thread
            loop = None
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(coro)
                return result
            finally:
                if loop:
                    try:
                        loop.close()
                    except Exception:
                        pass
                asyncio.set_event_loop(None)
                import gc

                gc.collect()
        except Exception as backup_error:
            st.error(f"All execution methods failed: {backup_error}")
            raise backup_error
    finally:
        # Ensure thread pool is properly closed
        if executor:
            try:
                executor.shutdown(wait=True, cancel_futures=True)
            except Exception:
                pass
        # Force garbage collection
        import gc

        gc.collect()


def run_async_task_simple(coro):
    """
    Simple async task runner, avoiding threading issues

    Args:
        coro: Coroutine object

    Returns:
        Task result
    """
    # Apply nest_asyncio to support nested event loops
    nest_asyncio.apply()

    try:
        # Try to run in current event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If current loop is running, use improved thread pool method
            import concurrent.futures
            import gc

            def run_in_thread():
                # Create new event loop and set as current thread's loop
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    result = new_loop.run_until_complete(coro)
                    return result
                except Exception as e:
                    # Ensure exception information is properly passed
                    raise e
                finally:
                    # Ensure loop is properly closed
                    try:
                        new_loop.close()
                    except Exception:
                        pass
                    # Clear current thread's event loop reference
                    asyncio.set_event_loop(None)
                    # Force garbage collection
                    gc.collect()

            # Use context manager to ensure thread pool is properly closed
            executor = None
            try:
                executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="deepcode_async"
                )
                future = executor.submit(run_in_thread)
                result = future.result(timeout=300)  # 5 minute timeout
                return result
            except concurrent.futures.TimeoutError:
                st.error(
                    "Processing timeout after 5 minutes. Please try again with a smaller file."
                )
                raise TimeoutError("Processing timeout")
            except Exception as e:
                st.error(f"Async processing error: {e}")
                raise e
            finally:
                # Ensure thread pool is properly closed
                if executor:
                    try:
                        executor.shutdown(wait=True, cancel_futures=True)
                    except Exception:
                        pass
                # Force garbage collection
                gc.collect()
        else:
            # Run directly in current loop
            return loop.run_until_complete(coro)
    except Exception:
        # Final fallback method: create new event loop
        loop = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(coro)
            return result
        except Exception as backup_error:
            st.error(f"All async methods failed: {backup_error}")
            raise backup_error
        finally:
            if loop:
                try:
                    loop.close()
                except Exception:
                    pass
            asyncio.set_event_loop(None)
            # Force garbage collection
            import gc

            gc.collect()


def handle_processing_workflow(
    input_source: str, input_type: str, enable_indexing: bool = True
) -> Dict[str, Any]:
    """
    Main processing function for workflow

    Args:
        input_source: Input source
        input_type: Input type
        enable_indexing: Whether to enable indexing functionality

    Returns:
        Processing result
    """
    from .components import (
        enhanced_progress_display_component,
        update_step_indicator,
        display_status,
    )

    # Display enhanced progress components
    chat_mode = input_type == "chat"
    progress_bar, status_text, step_indicators, workflow_steps = (
        enhanced_progress_display_component(enable_indexing, chat_mode)
    )

    # Step mapping: map progress percentages to step indices - adjust based on mode and indexing toggle
    if chat_mode:
        # Chat mode step mapping: Initialize -> Planning -> Setup -> Save Plan -> Implement
        step_mapping = {
            5: 0,  # Initialize
            30: 1,  # Planning (analyzing requirements)
            50: 2,  # Setup (creating workspace)
            70: 3,  # Save Plan (saving implementation plan)
            85: 4,  # Implement (generating code)
            100: 4,  # Complete
        }
    elif not enable_indexing:
        # Skip indexing-related steps progress mapping - fast mode order: Initialize -> Analyze -> Download -> Plan -> Implement
        step_mapping = {
            5: 0,  # Initialize
            10: 1,  # Analyze
            25: 2,  # Download
            40: 3,  # Plan (now prioritized over References, 40%)
            85: 4,  # Implement (skip References, Repos and Index)
            100: 4,  # Complete
        }
    else:
        # Full workflow step mapping - new order: Initialize -> Analyze -> Download -> Plan -> References -> Repos -> Index -> Implement
        step_mapping = {
            5: 0,  # Initialize
            10: 1,  # Analyze
            25: 2,  # Download
            40: 3,  # Plan (now 4th position, 40%)
            50: 4,  # References (now 5th position, conditional, 50%)
            60: 5,  # Repos (GitHub download)
            70: 6,  # Index (code indexing)
            85: 7,  # Implement (code implementation)
            100: 7,  # Complete
        }

    current_step = 0

    # Define enhanced progress callback function
    def update_progress(progress: int, message: str):
        nonlocal current_step

        # Update progress bar
        progress_bar.progress(progress)
        status_text.markdown(f"**{message}**")

        # Determine current step
        new_step = step_mapping.get(progress, current_step)
        if new_step != current_step:
            current_step = new_step
            update_step_indicator(
                step_indicators, workflow_steps, current_step, "active"
            )

        time.sleep(0.3)  # Brief pause for users to see progress changes

    # Step 1: Initialization
    if chat_mode:
        update_progress(5, "🚀 Initializing chat-based planning engine...")
    elif enable_indexing:
        update_progress(5, "🚀 Initializing AI research engine and loading models...")
    else:
        update_progress(
            5, "🚀 Initializing AI research engine (Fast mode - indexing disabled)..."
        )
    update_step_indicator(step_indicators, workflow_steps, 0, "active")

    # Start async processing with progress callback
    with st.spinner("🔄 Processing workflow stages..."):
        try:
            # First try using simple async processing method
            result = run_async_task_simple(
                process_input_async(
                    input_source, input_type, enable_indexing, update_progress
                )
            )
        except Exception as e:
            st.warning(f"Primary async method failed: {e}")
            # Fallback method: use original thread pool method
            try:
                result = run_async_task(
                    process_input_async(
                        input_source, input_type, enable_indexing, update_progress
                    )
                )
            except Exception as backup_error:
                st.error(f"Both async methods failed. Error: {backup_error}")
                return {
                    "status": "error",
                    "error": str(backup_error),
                    "traceback": traceback.format_exc(),
                }

    # Update final status based on results
    if result["status"] == "success":
        # Complete all steps
        update_progress(100, "✅ All processing stages completed successfully!")
        update_step_indicator(
            step_indicators, workflow_steps, len(workflow_steps), "completed"
        )

        # Display success information
        st.balloons()  # Add celebration animation
        if chat_mode:
            display_status(
                "🎉 Chat workflow completed! Your requirements have been analyzed and code has been generated.",
                "success",
            )
        elif enable_indexing:
            display_status(
                "🎉 Workflow completed! Your research paper has been successfully processed and code has been generated.",
                "success",
            )
        else:
            display_status(
                "🎉 Fast workflow completed! Your research paper has been processed (indexing skipped for faster processing).",
                "success",
            )

    else:
        # Processing failed
        update_progress(0, "❌ Processing failed - see error details below")
        update_step_indicator(step_indicators, workflow_steps, current_step, "error")
        display_status(
            f"❌ Processing encountered an error: {result.get('error', 'Unknown error')}",
            "error",
        )

    # Wait a moment for users to see completion status
    time.sleep(2.5)

    return result


def update_session_state_with_result(result: Dict[str, Any], input_type: str):
    """
    Update session state with result

    Args:
        result: Processing result
        input_type: Input type
    """
    if result["status"] == "success":
        # Save result to session state
        st.session_state.last_result = result
        st.session_state.show_results = True

        # Save to history
        st.session_state.results.append(
            {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "input_type": input_type,
                "status": "success",
                "result": result,
            }
        )
    else:
        # Save error information to session state for display
        st.session_state.last_error = result.get("error", "Unknown error")

        # Save error to history
        st.session_state.results.append(
            {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "input_type": input_type,
                "status": "error",
                "error": result.get("error", "Unknown error"),
            }
        )

    # Limit history to maximum 50 records
    if len(st.session_state.results) > 50:
        st.session_state.results = st.session_state.results[-50:]


def cleanup_temp_file(input_source: str, input_type: str):
    """
    Cleanup temporary file

    Args:
        input_source: Input source
        input_type: Input type
    """
    if input_type == "file" and input_source and os.path.exists(input_source):
        try:
            os.unlink(input_source)
        except Exception:
            pass


def handle_start_processing_button(input_source: str, input_type: str):
    """
    Handle start processing button click

    Args:
        input_source: Input source
        input_type: Input type
    """
    from .components import display_status

    st.session_state.processing = True

    # Get indexing toggle status
    enable_indexing = st.session_state.get("enable_indexing", True)

    try:
        # Process workflow
        result = handle_processing_workflow(input_source, input_type, enable_indexing)

        # Display result status
        if result["status"] == "success":
            display_status("All operations completed successfully! 🎉", "success")
        else:
            display_status("Error during processing", "error")

        # Update session state
        update_session_state_with_result(result, input_type)

    except Exception as e:
        # Handle exceptional cases
        st.error(f"Unexpected error during processing: {e}")
        result = {"status": "error", "error": str(e)}
        update_session_state_with_result(result, input_type)

    finally:
        # Reset state and clean up resources after processing
        st.session_state.processing = False

        # Clean up temporary files
        cleanup_temp_file(input_source, input_type)

        # Clean up system resources
        cleanup_resources()

        # Rerun to display results or errors
        st.rerun()


def handle_error_display():
    """Handle error display"""
    if hasattr(st.session_state, "last_error") and st.session_state.last_error:
        st.error(f"❌ Error: {st.session_state.last_error}")
        if st.button("🔄 Try Again", type="secondary", use_container_width=True):
            st.session_state.last_error = None
            st.session_state.task_counter += 1
            st.rerun()


def initialize_session_state():
    """Initialize session state"""
    if "processing" not in st.session_state:
        st.session_state.processing = False
    if "results" not in st.session_state:
        st.session_state.results = []
    if "current_step" not in st.session_state:
        st.session_state.current_step = 0
    if "task_counter" not in st.session_state:
        st.session_state.task_counter = 0
    if "show_results" not in st.session_state:
        st.session_state.show_results = False
    if "last_result" not in st.session_state:
        st.session_state.last_result = None
    if "last_error" not in st.session_state:
        st.session_state.last_error = None
    if "enable_indexing" not in st.session_state:
        st.session_state.enable_indexing = (
            False  # Default enable indexing functionality
        )


def cleanup_resources():
    """
    Clean up system resources to prevent memory leaks
    """
    try:
        import gc
        import threading
        import multiprocessing
        import asyncio
        import sys

        # 1. Clean up asyncio-related resources
        try:
            # Get current event loop (if exists)
            try:
                loop = asyncio.get_running_loop()
                # Cancel all pending tasks
                if loop and not loop.is_closed():
                    pending_tasks = [
                        task for task in asyncio.all_tasks(loop) if not task.done()
                    ]
                    if pending_tasks:
                        for task in pending_tasks:
                            if not task.cancelled():
                                task.cancel()
                        # Wait for task cancellation to complete
                        try:
                            if pending_tasks:
                                # Use timeout to avoid blocking too long
                                import time

                                time.sleep(0.1)
                        except Exception:
                            pass
            except RuntimeError:
                # No running event loop, continue with other cleanup
                pass
        except Exception:
            pass

        # 2. Force garbage collection
        gc.collect()

        # 3. Clean up active threads (except main thread)
        active_threads = threading.active_count()
        if active_threads > 1:
            # Wait some time for threads to naturally finish
            import time

            time.sleep(0.5)

        # 4. Clean up multiprocessing resources
        try:
            # Clean up possible multiprocessing resources
            if hasattr(multiprocessing, "active_children"):
                for child in multiprocessing.active_children():
                    if child.is_alive():
                        child.terminate()
                        child.join(timeout=1.0)
                        # If join times out, force kill
                        if child.is_alive():
                            try:
                                child.kill()
                                child.join(timeout=0.5)
                            except Exception:
                                pass

            # Clean up multiprocessing-related resource tracker
            try:
                import multiprocessing.resource_tracker

                if hasattr(multiprocessing.resource_tracker, "_resource_tracker"):
                    tracker = multiprocessing.resource_tracker._resource_tracker
                    if tracker and hasattr(tracker, "_stop"):
                        tracker._stop()
            except Exception:
                pass

        except Exception:
            pass

        # 5. Force clean up Python internal caches
        try:
            # Clean up some temporary objects in module cache
            import sys

            # Don't delete key modules, only clean up possible temporary resources
            if hasattr(sys, "_clear_type_cache"):
                sys._clear_type_cache()
        except Exception:
            pass

        # 6. Final garbage collection
        gc.collect()

    except Exception as e:
        # Silently handle cleanup errors to avoid affecting main flow
        # But can log errors in debug mode
        try:
            import logging

            logging.getLogger(__name__).debug(f"Resource cleanup warning: {e}")
        except Exception:
            pass
