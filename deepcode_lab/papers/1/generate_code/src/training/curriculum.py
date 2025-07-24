"""
Training curriculum and scheduling for Curiosity-Driven Red-Teaming.

This module implements curriculum learning strategies for the CRT training process,
including progressive difficulty scheduling, batch management, and adaptive training
progression based on model performance.
"""

import logging
import random
import math
from typing import List, Dict, Any, Optional, Tuple, Iterator
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CurriculumConfig:
    """Configuration for curriculum learning."""
    
    # Basic curriculum settings
    curriculum_type: str = "progressive"  # progressive, random, adaptive
    initial_batch_size: int = 32
    max_batch_size: int = 128
    batch_size_growth_rate: float = 1.1
    
    # Progressive curriculum settings
    difficulty_levels: int = 5
    samples_per_level: int = 1000
    level_transition_threshold: float = 0.7  # Quality threshold to advance
    
    # Adaptive curriculum settings
    performance_window: int = 100  # Episodes to consider for adaptation
    adaptation_rate: float = 0.1
    min_success_rate: float = 0.3
    max_success_rate: float = 0.8
    
    # Scheduling settings
    warmup_epochs: int = 5
    cooldown_epochs: int = 10
    learning_rate_schedule: str = "cosine"  # cosine, linear, constant
    
    # Data sampling settings
    sample_strategy: str = "balanced"  # balanced, weighted, random
    diversity_weight: float = 0.3
    novelty_weight: float = 0.4
    quality_weight: float = 0.3
    
    # Advanced settings
    enable_replay_buffer: bool = True
    replay_buffer_size: int = 10000
    replay_sample_ratio: float = 0.2
    
    def __post_init__(self):
        """Validate configuration parameters."""
        assert self.curriculum_type in ["progressive", "random", "adaptive"]
        assert 0 < self.level_transition_threshold < 1
        assert 0 < self.adaptation_rate < 1
        assert 0 < self.min_success_rate < self.max_success_rate < 1
        assert self.learning_rate_schedule in ["cosine", "linear", "constant"]
        assert self.sample_strategy in ["balanced", "weighted", "random"]
        assert 0 <= self.diversity_weight + self.novelty_weight + self.quality_weight <= 1.1


@dataclass
class TrainingBatch:
    """Represents a training batch with metadata."""
    
    instructions: List[str]
    difficulty_level: int = 0
    batch_id: int = 0
    epoch: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __len__(self) -> int:
        return len(self.instructions)
    
    def __iter__(self) -> Iterator[str]:
        return iter(self.instructions)


@dataclass
class CurriculumState:
    """Tracks the current state of curriculum learning."""
    
    current_epoch: int = 0
    current_batch: int = 0
    current_difficulty: int = 0
    current_batch_size: int = 32
    
    # Performance tracking
    recent_quality_scores: List[float] = field(default_factory=list)
    recent_diversity_scores: List[float] = field(default_factory=list)
    level_completion_rates: List[float] = field(default_factory=list)
    
    # Adaptive state
    adaptation_history: List[Dict[str, float]] = field(default_factory=list)
    last_adaptation_epoch: int = 0
    
    def update_performance(self, quality: float, diversity: float):
        """Update performance metrics."""
        self.recent_quality_scores.append(quality)
        self.recent_diversity_scores.append(diversity)
        
        # Keep only recent scores
        max_history = 100
        if len(self.recent_quality_scores) > max_history:
            self.recent_quality_scores = self.recent_quality_scores[-max_history:]
        if len(self.recent_diversity_scores) > max_history:
            self.recent_diversity_scores = self.recent_diversity_scores[-max_history:]
    
    def get_recent_performance(self, window: int = 10) -> Tuple[float, float]:
        """Get average performance over recent window."""
        if not self.recent_quality_scores:
            return 0.0, 0.0
        
        window = min(window, len(self.recent_quality_scores))
        avg_quality = np.mean(self.recent_quality_scores[-window:])
        avg_diversity = np.mean(self.recent_diversity_scores[-window:])
        
        return float(avg_quality), float(avg_diversity)


class BaseCurriculumScheduler(ABC):
    """Abstract base class for curriculum schedulers."""
    
    def __init__(self, config: CurriculumConfig):
        self.config = config
        self.state = CurriculumState()
        self.state.current_batch_size = config.initial_batch_size
        
    @abstractmethod
    def get_next_batch(self, dataset: List[str]) -> TrainingBatch:
        """Get the next training batch."""
        pass
    
    @abstractmethod
    def update_performance(self, quality: float, diversity: float, **kwargs):
        """Update scheduler based on performance metrics."""
        pass
    
    def advance_epoch(self):
        """Advance to next epoch."""
        self.state.current_epoch += 1
        self.state.current_batch = 0
        logger.info(f"Advanced to epoch {self.state.current_epoch}")
    
    def get_learning_rate_multiplier(self, base_lr: float, total_epochs: int) -> float:
        """Get learning rate multiplier based on schedule."""
        if self.config.learning_rate_schedule == "constant":
            return 1.0
        
        progress = self.state.current_epoch / total_epochs
        
        if self.config.learning_rate_schedule == "linear":
            return max(0.1, 1.0 - progress)
        
        elif self.config.learning_rate_schedule == "cosine":
            return 0.5 * (1 + math.cos(math.pi * progress))
        
        return 1.0
    
    def should_adapt(self) -> bool:
        """Check if curriculum should adapt."""
        epochs_since_adaptation = self.state.current_epoch - self.state.last_adaptation_epoch
        return epochs_since_adaptation >= 5  # Adapt every 5 epochs


class ProgressiveCurriculumScheduler(BaseCurriculumScheduler):
    """Progressive curriculum that increases difficulty over time."""
    
    def __init__(self, config: CurriculumConfig):
        super().__init__(config)
        self.samples_seen_at_level = 0
        
    def get_next_batch(self, dataset: List[str]) -> TrainingBatch:
        """Get next batch with progressive difficulty."""
        # Determine current difficulty level
        total_samples = self.state.current_epoch * len(dataset) + self.state.current_batch * self.state.current_batch_size
        current_level = min(
            self.config.difficulty_levels - 1,
            total_samples // self.config.samples_per_level
        )
        
        self.state.current_difficulty = current_level
        
        # Sample instructions based on difficulty
        batch_instructions = self._sample_by_difficulty(dataset, current_level)
        
        batch = TrainingBatch(
            instructions=batch_instructions,
            difficulty_level=current_level,
            batch_id=self.state.current_batch,
            epoch=self.state.current_epoch,
            metadata={
                "total_samples": total_samples,
                "level_progress": (total_samples % self.config.samples_per_level) / self.config.samples_per_level
            }
        )
        
        self.state.current_batch += 1
        return batch
    
    def _sample_by_difficulty(self, dataset: List[str], level: int) -> List[str]:
        """Sample instructions based on difficulty level."""
        # For progressive curriculum, we can implement different strategies:
        # 1. Length-based difficulty (shorter prompts = easier)
        # 2. Complexity-based difficulty (simple vs complex instructions)
        # 3. Domain-based difficulty (safe vs risky domains)
        
        # Simple length-based difficulty for now
        sorted_data = sorted(dataset, key=len)
        total_samples = len(sorted_data)
        
        # Define difficulty ranges
        level_size = total_samples // self.config.difficulty_levels
        start_idx = level * level_size
        end_idx = min((level + 1) * level_size, total_samples)
        
        # Sample from current difficulty range
        level_data = sorted_data[start_idx:end_idx]
        
        if len(level_data) < self.state.current_batch_size:
            # If not enough data at this level, sample from all previous levels
            level_data = sorted_data[:end_idx]
        
        return random.sample(level_data, min(self.state.current_batch_size, len(level_data)))
    
    def update_performance(self, quality: float, diversity: float, **kwargs):
        """Update progressive curriculum based on performance."""
        self.state.update_performance(quality, diversity)
        
        # Check if we should advance difficulty faster based on performance
        avg_quality, avg_diversity = self.state.get_recent_performance(window=20)
        
        if avg_quality > self.config.level_transition_threshold:
            # Increase batch size if performing well
            new_batch_size = min(
                self.config.max_batch_size,
                int(self.state.current_batch_size * self.config.batch_size_growth_rate)
            )
            if new_batch_size != self.state.current_batch_size:
                logger.info(f"Increasing batch size from {self.state.current_batch_size} to {new_batch_size}")
                self.state.current_batch_size = new_batch_size


class AdaptiveCurriculumScheduler(BaseCurriculumScheduler):
    """Adaptive curriculum that adjusts based on model performance."""
    
    def __init__(self, config: CurriculumConfig):
        super().__init__(config)
        self.difficulty_weights = np.ones(config.difficulty_levels)
        self.performance_history = []
        
    def get_next_batch(self, dataset: List[str]) -> TrainingBatch:
        """Get next batch with adaptive difficulty sampling."""
        # Compute sampling probabilities based on performance
        sampling_probs = self._compute_sampling_probabilities()
        
        # Sample difficulty level
        difficulty_level = np.random.choice(
            self.config.difficulty_levels,
            p=sampling_probs
        )
        
        # Sample instructions for this difficulty
        batch_instructions = self._sample_adaptive(dataset, difficulty_level)
        
        batch = TrainingBatch(
            instructions=batch_instructions,
            difficulty_level=difficulty_level,
            batch_id=self.state.current_batch,
            epoch=self.state.current_epoch,
            metadata={
                "sampling_probs": sampling_probs.tolist(),
                "difficulty_weights": self.difficulty_weights.tolist()
            }
        )
        
        self.state.current_batch += 1
        return batch
    
    def _compute_sampling_probabilities(self) -> np.ndarray:
        """Compute sampling probabilities for each difficulty level."""
        # Start with uniform probabilities
        probs = np.ones(self.config.difficulty_levels)
        
        # Adjust based on recent performance
        if len(self.performance_history) > 10:
            recent_performance = self.performance_history[-10:]
            avg_quality = np.mean([p["quality"] for p in recent_performance])
            
            if avg_quality < self.config.min_success_rate:
                # Focus on easier levels
                probs = np.exp(-np.arange(self.config.difficulty_levels))
            elif avg_quality > self.config.max_success_rate:
                # Focus on harder levels
                probs = np.exp(np.arange(self.config.difficulty_levels) - self.config.difficulty_levels + 1)
        
        # Apply difficulty weights
        probs *= self.difficulty_weights
        
        # Normalize
        return probs / probs.sum()
    
    def _sample_adaptive(self, dataset: List[str], difficulty_level: int) -> List[str]:
        """Sample instructions adaptively based on multiple criteria."""
        # Implement weighted sampling based on:
        # 1. Difficulty level
        # 2. Diversity (avoid similar prompts)
        # 3. Novelty (prefer unseen patterns)
        # 4. Quality potential (prompts likely to succeed)
        
        # For now, use simple difficulty-based sampling
        sorted_data = sorted(dataset, key=len)
        total_samples = len(sorted_data)
        level_size = total_samples // self.config.difficulty_levels
        
        start_idx = difficulty_level * level_size
        end_idx = min((difficulty_level + 1) * level_size, total_samples)
        level_data = sorted_data[start_idx:end_idx]
        
        # Add some randomness to avoid overfitting to specific patterns
        if difficulty_level > 0:
            # Include some samples from adjacent levels
            prev_start = max(0, (difficulty_level - 1) * level_size)
            next_end = min(total_samples, (difficulty_level + 2) * level_size)
            extended_data = sorted_data[prev_start:next_end]
            
            # Sample mostly from current level, some from adjacent
            current_samples = min(int(0.8 * self.state.current_batch_size), len(level_data))
            adjacent_samples = self.state.current_batch_size - current_samples
            
            batch_instructions = random.sample(level_data, min(current_samples, len(level_data)))
            if adjacent_samples > 0 and len(extended_data) > len(level_data):
                adjacent_data = [x for x in extended_data if x not in level_data]
                batch_instructions.extend(random.sample(adjacent_data, min(adjacent_samples, len(adjacent_data))))
        else:
            batch_instructions = random.sample(level_data, min(self.state.current_batch_size, len(level_data)))
        
        return batch_instructions
    
    def update_performance(self, quality: float, diversity: float, **kwargs):
        """Update adaptive curriculum based on performance."""
        self.state.update_performance(quality, diversity)
        
        # Record performance for this difficulty level
        performance_record = {
            "epoch": self.state.current_epoch,
            "batch": self.state.current_batch - 1,
            "difficulty": self.state.current_difficulty,
            "quality": quality,
            "diversity": diversity,
            **kwargs
        }
        self.performance_history.append(performance_record)
        
        # Adapt difficulty weights if needed
        if self.should_adapt():
            self._adapt_difficulty_weights()
            self.state.last_adaptation_epoch = self.state.current_epoch
    
    def _adapt_difficulty_weights(self):
        """Adapt difficulty weights based on performance history."""
        if len(self.performance_history) < self.config.performance_window:
            return
        
        # Analyze performance by difficulty level
        recent_history = self.performance_history[-self.config.performance_window:]
        level_performance = {}
        
        for record in recent_history:
            level = record["difficulty"]
            if level not in level_performance:
                level_performance[level] = []
            level_performance[level].append(record["quality"])
        
        # Update weights based on performance
        for level in range(self.config.difficulty_levels):
            if level in level_performance:
                avg_performance = np.mean(level_performance[level])
                
                # Increase weight for levels with moderate performance
                # (not too easy, not too hard)
                target_performance = (self.config.min_success_rate + self.config.max_success_rate) / 2
                performance_diff = abs(avg_performance - target_performance)
                
                # Update weight with exponential moving average
                new_weight = 1.0 - performance_diff
                self.difficulty_weights[level] = (
                    (1 - self.config.adaptation_rate) * self.difficulty_weights[level] +
                    self.config.adaptation_rate * new_weight
                )
        
        # Ensure weights are positive and normalized
        self.difficulty_weights = np.maximum(self.difficulty_weights, 0.1)
        self.difficulty_weights /= self.difficulty_weights.sum()
        
        logger.info(f"Adapted difficulty weights: {self.difficulty_weights}")


class RandomCurriculumScheduler(BaseCurriculumScheduler):
    """Random curriculum for baseline comparison."""
    
    def get_next_batch(self, dataset: List[str]) -> TrainingBatch:
        """Get next batch with random sampling."""
        batch_instructions = random.sample(
            dataset,
            min(self.state.current_batch_size, len(dataset))
        )
        
        batch = TrainingBatch(
            instructions=batch_instructions,
            difficulty_level=0,  # No difficulty concept in random
            batch_id=self.state.current_batch,
            epoch=self.state.current_epoch,
            metadata={"sampling_strategy": "random"}
        )
        
        self.state.current_batch += 1
        return batch
    
    def update_performance(self, quality: float, diversity: float, **kwargs):
        """Update random curriculum (no adaptation)."""
        self.state.update_performance(quality, diversity)


class CurriculumScheduler:
    """Main curriculum scheduler that delegates to specific implementations."""
    
    def __init__(self, config: CurriculumConfig):
        self.config = config
        
        # Create appropriate scheduler
        if config.curriculum_type == "progressive":
            self.scheduler = ProgressiveCurriculumScheduler(config)
        elif config.curriculum_type == "adaptive":
            self.scheduler = AdaptiveCurriculumScheduler(config)
        elif config.curriculum_type == "random":
            self.scheduler = RandomCurriculumScheduler(config)
        else:
            raise ValueError(f"Unknown curriculum type: {config.curriculum_type}")
        
        logger.info(f"Initialized {config.curriculum_type} curriculum scheduler")
    
    def get_next_batch(self, dataset: List[str]) -> TrainingBatch:
        """Get the next training batch."""
        return self.scheduler.get_next_batch(dataset)
    
    def update_performance(self, quality: float, diversity: float, **kwargs):
        """Update scheduler based on performance metrics."""
        self.scheduler.update_performance(quality, diversity, **kwargs)
    
    def advance_epoch(self):
        """Advance to next epoch."""
        self.scheduler.advance_epoch()
    
    def get_learning_rate_multiplier(self, base_lr: float, total_epochs: int) -> float:
        """Get learning rate multiplier."""
        return self.scheduler.get_learning_rate_multiplier(base_lr, total_epochs)
    
    def get_state(self) -> CurriculumState:
        """Get current curriculum state."""
        return self.scheduler.state
    
    def get_curriculum_info(self) -> Dict[str, Any]:
        """Get comprehensive curriculum information."""
        state = self.scheduler.state
        avg_quality, avg_diversity = state.get_recent_performance()
        
        info = {
            "curriculum_type": self.config.curriculum_type,
            "current_epoch": state.current_epoch,
            "current_batch": state.current_batch,
            "current_difficulty": state.current_difficulty,
            "current_batch_size": state.current_batch_size,
            "recent_avg_quality": avg_quality,
            "recent_avg_diversity": avg_diversity,
            "total_batches_processed": state.current_batch,
        }
        
        # Add scheduler-specific info
        if isinstance(self.scheduler, AdaptiveCurriculumScheduler):
            info["difficulty_weights"] = self.scheduler.difficulty_weights.tolist()
            info["performance_history_length"] = len(self.scheduler.performance_history)
        
        return info


def create_curriculum_scheduler(
    curriculum_type: str = "progressive",
    **kwargs
) -> CurriculumScheduler:
    """Factory function to create curriculum scheduler."""
    config = CurriculumConfig(curriculum_type=curriculum_type, **kwargs)
    return CurriculumScheduler(config)


# Example usage and testing
if __name__ == "__main__":
    # Test curriculum schedulers
    logging.basicConfig(level=logging.INFO)
    
    # Create sample dataset
    sample_dataset = [
        f"Instruction {i}: " + "word " * (i % 10 + 1)
        for i in range(100)
    ]
    
    # Test progressive curriculum
    print("Testing Progressive Curriculum:")
    progressive_config = CurriculumConfig(
        curriculum_type="progressive",
        difficulty_levels=3,
        samples_per_level=20,
        initial_batch_size=8
    )
    progressive_scheduler = CurriculumScheduler(progressive_config)
    
    for epoch in range(3):
        progressive_scheduler.advance_epoch()
        for batch_idx in range(5):
            batch = progressive_scheduler.get_next_batch(sample_dataset)
            print(f"Epoch {epoch}, Batch {batch_idx}: Level {batch.difficulty_level}, Size {len(batch)}")
            
            # Simulate performance feedback
            quality = 0.5 + 0.1 * batch.difficulty_level + random.random() * 0.2
            diversity = 0.6 + random.random() * 0.3
            progressive_scheduler.update_performance(quality, diversity)
    
    print("\nTesting Adaptive Curriculum:")
    adaptive_config = CurriculumConfig(
        curriculum_type="adaptive",
        difficulty_levels=3,
        initial_batch_size=8
    )
    adaptive_scheduler = CurriculumScheduler(adaptive_config)
    
    for epoch in range(3):
        adaptive_scheduler.advance_epoch()
        for batch_idx in range(5):
            batch = adaptive_scheduler.get_next_batch(sample_dataset)
            print(f"Epoch {epoch}, Batch {batch_idx}: Level {batch.difficulty_level}, Size {len(batch)}")
            
            # Simulate performance feedback
            quality = 0.4 + 0.2 * random.random()
            diversity = 0.5 + 0.3 * random.random()
            adaptive_scheduler.update_performance(quality, diversity)
    
    print("\nCurriculum Info:")
    print(adaptive_scheduler.get_curriculum_info())