"""
Loop Detection and Timeout Safeguards for Code Implementation Workflow

This module provides tools to detect infinite loops, timeouts, and progress stalls
in the code implementation process to prevent hanging processes.
"""

import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta


class LoopDetector:
    """
    Detects infinite loops, timeouts, and progress stalls in workflow execution.
    
    Features:
    - Track tool call history to detect repeated patterns
    - Monitor time per file/operation
    - Detect progress stalls
    - Force stop after consecutive errors
    """
    
    def __init__(self, max_repeats: int = 5, timeout_seconds: int = 300, 
                 stall_threshold: int = 180, max_errors: int = 10):
        """
        Initialize loop detector.
        
        Args:
            max_repeats: Maximum consecutive calls to same tool before flagging
            timeout_seconds: Maximum time per file/operation (5 minutes default)
            stall_threshold: Maximum time without progress (3 minutes default)
            max_errors: Maximum consecutive errors before force stop
        """
        self.max_repeats = max_repeats
        self.timeout_seconds = timeout_seconds
        self.stall_threshold = stall_threshold
        self.max_errors = max_errors
        
        # Tracking state
        self.tool_history: List[str] = []
        self.start_time = time.time()
        self.last_progress_time = time.time()
        self.consecutive_errors = 0
        self.current_file = None
        self.file_start_time = None
        
    def start_file(self, filename: str):
        """Start tracking a new file."""
        self.current_file = filename
        self.file_start_time = time.time()
        self.last_progress_time = time.time()
        print(f"📁 Starting file: {filename}")
        
    def check_tool_call(self, tool_name: str) -> Dict[str, Any]:
        """
        Check if tool call indicates a loop or timeout.
        
        Args:
            tool_name: Name of the tool being called
            
        Returns:
            Dict with status and warnings
        """
        current_time = time.time()
        self.tool_history.append(tool_name)
        
        # Keep only recent history (last 10 calls)
        if len(self.tool_history) > 10:
            self.tool_history = self.tool_history[-10:]
        
        # Check for repeated tool calls
        if len(self.tool_history) >= self.max_repeats:
            recent_tools = self.tool_history[-self.max_repeats:]
            if len(set(recent_tools)) == 1:  # All same tool
                return {
                    "status": "loop_detected",
                    "message": f"⚠️ Loop detected: {tool_name} called {self.max_repeats} times consecutively",
                    "should_stop": True
                }
        
        # Check file timeout
        if self.file_start_time and (current_time - self.file_start_time) > self.timeout_seconds:
            return {
                "status": "timeout",
                "message": f"⏰ Timeout: File {self.current_file} processing exceeded {self.timeout_seconds}s",
                "should_stop": True
            }
        
        # Check progress stall
        if (current_time - self.last_progress_time) > self.stall_threshold:
            return {
                "status": "stall",
                "message": f"🐌 Progress stall: No progress for {self.stall_threshold}s",
                "should_stop": True
            }
        
        # Check consecutive errors
        if self.consecutive_errors >= self.max_errors:
            return {
                "status": "max_errors",
                "message": f"❌ Too many errors: {self.consecutive_errors} consecutive errors",
                "should_stop": True
            }
        
        return {
            "status": "ok",
            "message": "Processing normally",
            "should_stop": False
        }
    
    def record_progress(self):
        """Record that progress has been made."""
        self.last_progress_time = time.time()
        self.consecutive_errors = 0  # Reset error counter on progress
        
    def record_error(self, error_message: str):
        """Record an error occurred."""
        self.consecutive_errors += 1
        print(f"❌ Error #{self.consecutive_errors}: {error_message}")
        
    def record_success(self):
        """Record a successful operation."""
        self.consecutive_errors = 0
        self.record_progress()
        
    def get_status_summary(self) -> Dict[str, Any]:
        """Get current status summary."""
        current_time = time.time()
        file_elapsed = (current_time - self.file_start_time) if self.file_start_time else 0
        total_elapsed = current_time - self.start_time
        
        return {
            "current_file": self.current_file,
            "file_elapsed_seconds": file_elapsed,
            "total_elapsed_seconds": total_elapsed,
            "consecutive_errors": self.consecutive_errors,
            "recent_tools": self.tool_history[-5:],  # Last 5 tools
            "time_since_last_progress": current_time - self.last_progress_time
        }
    
    def should_abort(self) -> bool:
        """Check if process should be aborted."""
        status = self.check_tool_call("")  # Check without adding to history
        return status["should_stop"]
    
    def get_abort_reason(self) -> Optional[str]:
        """Get reason for abort if should abort."""
        if self.should_abort():
            status = self.check_tool_call("")
            return status["message"]
        return None


class ProgressTracker:
    """
    Track progress through implementation phases and files.
    """
    
    def __init__(self, total_files: int = 0):
        self.total_files = total_files
        self.completed_files = 0
        self.current_phase = "Initializing"
        self.phase_progress = 0
        self.start_time = time.time()
        
    def set_phase(self, phase_name: str, progress_percent: int):
        """Set current phase and progress percentage."""
        self.current_phase = phase_name
        self.phase_progress = progress_percent
        print(f"📊 Progress: {progress_percent}% - {phase_name}")
        
    def complete_file(self, filename: str):
        """Record completion of a file."""
        self.completed_files += 1
        print(f"✅ Completed file {self.completed_files}/{self.total_files}: {filename}")
        
    def get_progress_info(self) -> Dict[str, Any]:
        """Get current progress information."""
        elapsed = time.time() - self.start_time
        
        # Estimate remaining time
        if self.completed_files > 0 and self.total_files > 0:
            avg_time_per_file = elapsed / self.completed_files
            remaining_files = self.total_files - self.completed_files
            estimated_remaining = avg_time_per_file * remaining_files
        else:
            estimated_remaining = 0
            
        return {
            "phase": self.current_phase,
            "phase_progress": self.phase_progress,
            "files_completed": self.completed_files,
            "total_files": self.total_files,
            "file_progress": (self.completed_files / self.total_files * 100) if self.total_files > 0 else 0,
            "elapsed_seconds": elapsed,
            "estimated_remaining_seconds": estimated_remaining
        }
