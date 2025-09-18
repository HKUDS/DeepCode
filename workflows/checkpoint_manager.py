"""
Advanced Checkpoint Manager for Code Evaluation Workflow
Supports saving and resuming from any phase with complete state preservation
"""

import json
import os
import pickle
import time
import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
import hashlib
import shutil

# Import evaluation classes - handle import gracefully
try:
    from .code_evaluation_workflow_refactored import EvaluationPhase, EvaluationState
except ImportError:
    # Define minimal classes for testing if import fails
    from enum import Enum
    from dataclasses import dataclass
    from typing import List
    
    class EvaluationPhase(Enum):
        INITIALIZED = "initialized"
        ANALYZING = "analyzing"
        REVISING = "revising"
        STATIC_ANALYSIS = "static_analysis"
        ERROR_ANALYSIS = "error_analysis"
        COMPLETED = "completed"
        FAILED = "failed"
    
    @dataclass
    class EvaluationState:
        phase: EvaluationPhase
        repo_path: str
        docs_path: str
        memory_path: str
        workspace_dir: str
        start_time: float
        code_analysis: Any = None
        code_revision: Any = None
        static_analysis: Any = None
        error_analysis: Any = None
        revision_report: Optional[Dict[str, Any]] = None
        all_files_to_implement: List[str] = None
        errors: List[str] = None
        warnings: List[str] = None
        
        def __post_init__(self):
            if self.errors is None:
                self.errors = []
            if self.warnings is None:
                self.warnings = []
            if self.all_files_to_implement is None:
                self.all_files_to_implement = []
        
        def to_dict(self) -> Dict[str, Any]:
            """Convert state to dictionary for serialization"""
            result = {
                "phase": self.phase.value if isinstance(self.phase, EvaluationPhase) else self.phase,
                "repo_path": self.repo_path,
                "docs_path": self.docs_path,
                "memory_path": self.memory_path,
                "workspace_dir": self.workspace_dir,
                "start_time": self.start_time,
                "code_analysis": self.code_analysis,
                "code_revision": self.code_revision,
                "static_analysis": self.static_analysis,
                "error_analysis": self.error_analysis,
                "revision_report": self.revision_report,
                "all_files_to_implement": self.all_files_to_implement,
                "errors": self.errors,
                "warnings": self.warnings
            }
            return result


@dataclass
class CheckpointMetadata:
    """Metadata for checkpoint files"""
    
    checkpoint_id: str
    phase: str
    timestamp: str
    repo_path: str
    checkpoint_version: str = "1.0"
    phase_duration: float = 0.0
    total_duration: float = 0.0
    file_count: int = 0
    dependency_hashes: Dict[str, str] = None
    
    def __post_init__(self):
        if self.dependency_hashes is None:
            self.dependency_hashes = {}


class CheckpointManager:
    """
    Advanced checkpoint manager with the following features:
    
    1. Phase-based checkpointing: Save state after each phase completion
    2. Intelligent resume: Auto-detect where to resume from
    3. State preservation: Complete state serialization including agent states
    4. Dependency tracking: Track file changes to invalidate checkpoints
    5. Checkpoint validation: Ensure checkpoint integrity before resume
    6. Performance metrics: Track phase durations and optimization opportunities
    """
    
    def __init__(self, repo_path: str, logger: Optional[logging.Logger] = None):
        self.repo_path = os.path.abspath(repo_path)
        self.checkpoint_dir = self._get_checkpoint_dir()
        self.logger = logger or logging.getLogger(__name__)
        
        # Create checkpoint directory
        os.makedirs(self.checkpoint_dir, exist_ok=True)
        
        # Checkpoint file paths
        self.metadata_file = os.path.join(self.checkpoint_dir, "metadata.json")
        self.state_file = os.path.join(self.checkpoint_dir, "state.pkl")
        self.phase_log_file = os.path.join(self.checkpoint_dir, "phase_log.json")
        
        # Initialize phase log
        self.phase_log = self._load_phase_log()
        
        self.logger.info(f"🔖 Checkpoint manager initialized: {self.checkpoint_dir}")
    
    def _get_checkpoint_dir(self) -> str:
        """Get checkpoint directory path based on repo path"""
        # Based on user requirement: save in same directory as repo_path parent
        # e.g., repo_path = "/path/to/papers/1/generate_code" 
        # -> checkpoint_dir = "/path/to/papers/1/.checkpoints"
        parent_dir = os.path.dirname(self.repo_path)
        checkpoint_dir = os.path.join(parent_dir, ".checkpoints")
        return checkpoint_dir
    
    def _load_phase_log(self) -> List[Dict[str, Any]]:
        """Load existing phase execution log"""
        if os.path.exists(self.phase_log_file):
            try:
                with open(self.phase_log_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                self.logger.warning(f"Failed to load phase log: {e}")
        return []
    
    def _save_phase_log(self):
        """Save phase execution log"""
        try:
            with open(self.phase_log_file, 'w') as f:
                json.dump(self.phase_log, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save phase log: {e}")
    
    def _calculate_file_hash(self, file_path: str) -> str:
        """Calculate hash of a file for dependency tracking"""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ""
    
    def _get_dependency_hashes(self) -> Dict[str, str]:
        """Get hashes of critical dependency files"""
        dependencies = {}
        
        # Track important files that could affect workflow
        critical_files = [
            "requirements.txt",
            "setup.py", 
            "pyproject.toml",
            "Dockerfile",
            "docker-compose.yml",
            ".env",
            "config.yaml",
            "config.yml"
        ]
        
        for file_name in critical_files:
            file_path = os.path.join(self.repo_path, file_name)
            if os.path.exists(file_path):
                dependencies[file_name] = self._calculate_file_hash(file_path)
        
        return dependencies
    
    def save_checkpoint(self, 
                       evaluation_state: EvaluationState, 
                       phase: EvaluationPhase,
                       agent_states: Optional[Dict[str, Any]] = None) -> str:
        """
        Save checkpoint after phase completion
        
        Args:
            evaluation_state: Current evaluation state
            phase: Completed phase
            agent_states: Optional agent states for complete recovery
            
        Returns:
            Checkpoint ID
        """
        try:
            checkpoint_id = f"{phase.value}_{int(time.time())}"
            current_time = datetime.now().isoformat()
            
            # Calculate phase duration
            phase_start_time = None
            if self.phase_log:
                # Find when this phase started
                for entry in reversed(self.phase_log):
                    if entry.get("phase") == phase.value and entry.get("status") == "started":
                        phase_start_time = entry.get("timestamp")
                        break
            
            phase_duration = 0.0
            if phase_start_time:
                start_dt = datetime.fromisoformat(phase_start_time)
                end_dt = datetime.fromisoformat(current_time)
                phase_duration = (end_dt - start_dt).total_seconds()
            
            # Calculate total duration
            total_duration = time.time() - evaluation_state.start_time
            
            # Count files in repository
            file_count = sum(len(files) for _, _, files in os.walk(self.repo_path))
            
            # Create metadata
            metadata = CheckpointMetadata(
                checkpoint_id=checkpoint_id,
                phase=phase.value,
                timestamp=current_time,
                repo_path=self.repo_path,
                phase_duration=phase_duration,
                total_duration=total_duration,
                file_count=file_count,
                dependency_hashes=self._get_dependency_hashes()
            )
            
            # Prepare state for serialization
            checkpoint_data = {
                "metadata": asdict(metadata),
                "evaluation_state": evaluation_state.to_dict(),
                "agent_states": agent_states or {},
                "repo_snapshot": self._create_repo_snapshot()
            }
            
            # Save checkpoint data
            with open(self.state_file, 'wb') as f:
                pickle.dump(checkpoint_data, f)
            
            # Save metadata separately for easy access
            with open(self.metadata_file, 'w') as f:
                json.dump(asdict(metadata), f, indent=2)
            
            # Log phase completion
            self.phase_log.append({
                "phase": phase.value,
                "status": "completed",
                "timestamp": current_time,
                "checkpoint_id": checkpoint_id,
                "duration": phase_duration,
                "file_count": file_count
            })
            self._save_phase_log()
            
            self.logger.info(f"✅ Checkpoint saved: {checkpoint_id} (Phase: {phase.value})")
            self.logger.info(f"   Duration: {phase_duration:.2f}s, Total: {total_duration:.2f}s")
            self.logger.info(f"   Files: {file_count}, Checkpoint: {self.checkpoint_dir}")
            
            return checkpoint_id
            
        except Exception as e:
            self.logger.error(f"Failed to save checkpoint: {e}")
            raise
    
    def load_checkpoint(self) -> Optional[Tuple[EvaluationState, Dict[str, Any], CheckpointMetadata]]:
        """
        Load the latest checkpoint if available
        
        Returns:
            Tuple of (evaluation_state, agent_states, metadata) or None if no checkpoint
        """
        try:
            if not os.path.exists(self.state_file) or not os.path.exists(self.metadata_file):
                return None
            
            # Load metadata
            with open(self.metadata_file, 'r') as f:
                metadata_dict = json.load(f)
            
            metadata = CheckpointMetadata(**metadata_dict)
            
            # Validate checkpoint
            if not self._validate_checkpoint(metadata):
                self.logger.warning("Checkpoint validation failed, starting fresh")
                return None
            
            # Load checkpoint data
            with open(self.state_file, 'rb') as f:
                checkpoint_data = pickle.load(f)
            
            # Reconstruct evaluation state
            state_dict = checkpoint_data["evaluation_state"]
            state_dict["phase"] = EvaluationPhase(state_dict["phase"])
            
            # Create new EvaluationState object
            evaluation_state = EvaluationState(
                phase=state_dict["phase"],
                repo_path=state_dict["repo_path"],
                docs_path=state_dict["docs_path"],
                memory_path=state_dict["memory_path"],
                workspace_dir=state_dict["workspace_dir"],
                start_time=state_dict["start_time"],
                code_analysis=state_dict.get("code_analysis"),
                code_revision=state_dict.get("code_revision"),
                static_analysis=state_dict.get("static_analysis"),
                error_analysis=state_dict.get("error_analysis"),
                revision_report=state_dict.get("revision_report"),
                all_files_to_implement=state_dict.get("all_files_to_implement", []),
                errors=state_dict.get("errors", []),
                warnings=state_dict.get("warnings", [])
            )
            
            agent_states = checkpoint_data.get("agent_states", {})
            
            self.logger.info(f"🔄 Checkpoint loaded: {metadata.checkpoint_id}")
            self.logger.info(f"   Phase: {metadata.phase}, Time: {metadata.timestamp}")
            self.logger.info(f"   Files: {metadata.file_count}")
            
            return evaluation_state, agent_states, metadata
            
        except Exception as e:
            self.logger.error(f"Failed to load checkpoint: {e}")
            return None
    
    def _validate_checkpoint(self, metadata: CheckpointMetadata) -> bool:
        """Validate checkpoint integrity and dependencies"""
        try:
            # Check if checkpoint is too old (more than 7 days)
            checkpoint_time = datetime.fromisoformat(metadata.timestamp)
            age_days = (datetime.now() - checkpoint_time).days
            if age_days > 7:
                self.logger.warning(f"Checkpoint is {age_days} days old, may be stale")
                return False
            
            # Check if critical dependencies have changed
            current_hashes = self._get_dependency_hashes()
            for file_name, old_hash in metadata.dependency_hashes.items():
                if file_name in current_hashes:
                    if current_hashes[file_name] != old_hash:
                        self.logger.warning(f"Dependency {file_name} has changed, invalidating checkpoint")
                        return False
            
            # Check if repo path matches
            if metadata.repo_path != self.repo_path:
                self.logger.warning("Repository path mismatch, invalidating checkpoint")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Checkpoint validation error: {e}")
            return False
    
    def _create_repo_snapshot(self) -> Dict[str, Any]:
        """Create a lightweight snapshot of repository state"""
        snapshot = {
            "file_count": 0,
            "total_size": 0,
            "last_modified": 0,
            "python_files": []
        }
        
        try:
            for root, dirs, files in os.walk(self.repo_path):
                # Skip common non-essential directories
                dirs[:] = [d for d in dirs if d not in ['.git', '__pycache__', '.pytest_cache', 'node_modules']]
                
                for file in files:
                    file_path = os.path.join(root, file)
                    if os.path.exists(file_path):
                        stat = os.stat(file_path)
                        snapshot["file_count"] += 1
                        snapshot["total_size"] += stat.st_size
                        snapshot["last_modified"] = max(snapshot["last_modified"], stat.st_mtime)
                        
                        # Track Python files specifically
                        if file.endswith('.py'):
                            rel_path = os.path.relpath(file_path, self.repo_path)
                            snapshot["python_files"].append({
                                "path": rel_path,
                                "size": stat.st_size,
                                "modified": stat.st_mtime
                            })
        
        except Exception as e:
            self.logger.warning(f"Failed to create repo snapshot: {e}")
        
        return snapshot
    
    def get_resume_recommendation(self) -> Optional[Tuple[EvaluationPhase, str]]:
        """
        Analyze checkpoints and recommend where to resume from
        
        Returns:
            Tuple of (recommended_phase, reason) or None
        """
        checkpoint_data = self.load_checkpoint()
        if not checkpoint_data:
            return None
        
        evaluation_state, _, metadata = checkpoint_data
        
        # Determine next phase based on completed phase
        phase_order = [
            EvaluationPhase.INITIALIZED,
            EvaluationPhase.ANALYZING,
            EvaluationPhase.REVISING,
            EvaluationPhase.STATIC_ANALYSIS,
            EvaluationPhase.ERROR_ANALYSIS,
            EvaluationPhase.COMPLETED
        ]
        
        current_phase = EvaluationPhase(metadata.phase)
        
        try:
            current_index = phase_order.index(current_phase)
            if current_index < len(phase_order) - 1:
                next_phase = phase_order[current_index + 1]
                reason = f"Resume from {next_phase.value} (completed: {current_phase.value})"
                return next_phase, reason
            else:
                reason = f"Workflow already completed at {current_phase.value}"
                return current_phase, reason
        except ValueError:
            reason = f"Unknown phase {current_phase.value}, recommend full restart"
            return EvaluationPhase.INITIALIZED, reason
    
    def clear_checkpoints(self):
        """Clear all checkpoints"""
        try:
            if os.path.exists(self.checkpoint_dir):
                shutil.rmtree(self.checkpoint_dir)
                os.makedirs(self.checkpoint_dir, exist_ok=True)
                self.phase_log = []
                self.logger.info("🗑️ All checkpoints cleared")
        except Exception as e:
            self.logger.error(f"Failed to clear checkpoints: {e}")
    
    def log_phase_start(self, phase: EvaluationPhase):
        """Log the start of a phase"""
        self.phase_log.append({
            "phase": phase.value,
            "status": "started",
            "timestamp": datetime.now().isoformat()
        })
        self._save_phase_log()
        self.logger.info(f"🚀 Phase started: {phase.value}")
    
    def get_checkpoint_summary(self) -> Dict[str, Any]:
        """Get summary of all checkpoints and phase history"""
        summary = {
            "checkpoint_dir": self.checkpoint_dir,
            "has_checkpoint": os.path.exists(self.state_file),
            "phase_history": self.phase_log,
            "total_phases": len([entry for entry in self.phase_log if entry.get("status") == "completed"]),
            "recommendation": None
        }
        
        # Add resume recommendation
        recommendation = self.get_resume_recommendation()
        if recommendation:
            phase, reason = recommendation
            summary["recommendation"] = {
                "phase": phase.value,
                "reason": reason
            }
        
        return summary
