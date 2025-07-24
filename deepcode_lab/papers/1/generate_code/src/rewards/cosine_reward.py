"""
Cosine Similarity Novelty Reward for Curiosity-Driven Red-Teaming

This module implements the cosine similarity-based novelty reward component
as described in Equation 4 of the CRT paper. It computes novelty by measuring
the cosine similarity between sentence embeddings of test cases.

Formula: B_Cos(x) = -Σ_{x'∈X} cosine_sim(φ(x), φ(x'))

The reward encourages generating test cases that are semantically different
from previously generated ones by penalizing high cosine similarity.
"""

import logging
import numpy as np
from typing import List, Optional, Union, Dict, Any
from dataclasses import dataclass, field
import pickle
import os
from collections import defaultdict
import warnings

from .base_reward import BaseReward
from ..models.sentence_embedder import SentenceEmbedder

logger = logging.getLogger(__name__)


@dataclass
class CosineRewardConfig:
    """Configuration for cosine similarity novelty reward."""
    
    # Model configuration
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    device: str = "auto"  # "auto", "cpu", "cuda"
    
    # Reward computation parameters
    weight: float = 1.0  # λ_C in the paper
    similarity_threshold: float = 0.0  # Minimum similarity to consider
    max_history_size: int = 10000  # Maximum number of test cases to keep in history
    
    # Normalization and clipping
    normalize: bool = True
    clip_min: float = -10.0
    clip_max: float = 0.0
    
    # Caching and efficiency
    use_embedding_cache: bool = True
    cache_size: int = 50000
    batch_size: int = 32
    
    # Aggregation method for multiple similarities
    aggregation: str = "sum"  # "sum", "mean", "max", "min"
    
    # Memory management
    memory_efficient: bool = True
    cleanup_threshold: float = 0.8  # Clean up when memory usage exceeds this fraction


class CosineReward(BaseReward):
    """
    Cosine similarity-based novelty reward for red-teaming.
    
    This reward component computes the novelty of test cases by measuring
    their cosine similarity to previously generated test cases using
    sentence embeddings. Lower similarity (higher novelty) results in
    higher rewards.
    
    The reward is computed as:
    B_Cos(x) = -Σ_{x'∈X} cosine_sim(φ(x), φ(x'))
    
    where φ(x) is the sentence embedding of test case x, and X is the
    history of previously generated test cases.
    """
    
    def __init__(self, config: Optional[CosineRewardConfig] = None):
        """
        Initialize the cosine similarity novelty reward.
        
        Args:
            config: Configuration for the reward component
        """
        super().__init__()
        self.config = config or CosineRewardConfig()
        
        # Initialize sentence embedder
        self.embedder = SentenceEmbedder(
            model_name=self.config.model_name,
            device=self.config.device,
            cache_size=self.config.cache_size if self.config.use_embedding_cache else 0
        )
        
        # History of test cases and their embeddings
        self.history: List[str] = []
        self.history_embeddings: Optional[np.ndarray] = None
        
        # Statistics tracking
        self.stats = {
            "total_computations": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "average_similarity": 0.0,
            "min_similarity": float('inf'),
            "max_similarity": float('-inf'),
            "history_size": 0
        }
        
        logger.info(f"Initialized CosineReward with model: {self.config.model_name}")
    
    def compute(self, test_case: str, history: Optional[List[str]] = None) -> float:
        """
        Compute cosine similarity novelty reward for a single test case.
        
        Args:
            test_case: The test case to compute reward for
            history: Optional history of test cases (uses internal history if None)
            
        Returns:
            Cosine similarity novelty reward (negative sum of similarities)
        """
        if not isinstance(test_case, str):
            raise ValueError(f"test_case must be a string, got {type(test_case)}")
        
        # Use provided history or internal history
        if history is not None:
            history_texts = history
        else:
            history_texts = self.history
        
        # If no history, return zero reward
        if not history_texts:
            logger.debug("No history available, returning zero reward")
            return 0.0
        
        try:
            # Use the embedder's built-in cosine novelty reward function
            reward = self.embedder.compute_cosine_novelty_reward(test_case, history_texts)
            
            # Apply weight
            reward *= self.config.weight
            
            # Apply post-processing (normalization, clipping)
            reward = self.postprocess_reward(reward)
            
            # Update statistics
            self._update_stats(reward, len(history_texts))
            
            logger.debug(f"Computed cosine reward: {reward:.4f} for test case length {len(test_case)}")
            return float(reward)
            
        except Exception as e:
            logger.error(f"Error computing cosine reward: {e}")
            return 0.0
    
    def compute_batch(self, test_cases: List[str], history: Optional[List[str]] = None) -> List[float]:
        """
        Compute cosine similarity novelty rewards for a batch of test cases.
        
        Args:
            test_cases: List of test cases to compute rewards for
            history: Optional history of test cases (uses internal history if None)
            
        Returns:
            List of cosine similarity novelty rewards
        """
        if not test_cases:
            return []
        
        # Validate inputs
        if not all(isinstance(tc, str) for tc in test_cases):
            raise ValueError("All test_cases must be strings")
        
        # Use provided history or internal history
        if history is not None:
            history_texts = history
        else:
            history_texts = self.history
        
        # If no history, return zero rewards
        if not history_texts:
            logger.debug("No history available, returning zero rewards")
            return [0.0] * len(test_cases)
        
        try:
            rewards = []
            
            # Process in batches for memory efficiency
            batch_size = self.config.batch_size
            for i in range(0, len(test_cases), batch_size):
                batch = test_cases[i:i + batch_size]
                batch_rewards = []
                
                for test_case in batch:
                    reward = self.embedder.compute_cosine_novelty_reward(test_case, history_texts)
                    reward *= self.config.weight
                    reward = self.postprocess_reward(reward)
                    batch_rewards.append(float(reward))
                
                rewards.extend(batch_rewards)
            
            # Update statistics
            for reward in rewards:
                self._update_stats(reward, len(history_texts))
            
            logger.debug(f"Computed {len(rewards)} cosine rewards")
            return rewards
            
        except Exception as e:
            logger.error(f"Error computing batch cosine rewards: {e}")
            return [0.0] * len(test_cases)
    
    def add_to_history(self, test_cases: Union[str, List[str]]) -> None:
        """
        Add new test cases to the history.
        
        Args:
            test_cases: Single test case or list of test cases to add
        """
        if isinstance(test_cases, str):
            test_cases = [test_cases]
        
        # Add to history
        self.history.extend(test_cases)
        
        # Manage history size
        if len(self.history) > self.config.max_history_size:
            # Remove oldest entries
            excess = len(self.history) - self.config.max_history_size
            self.history = self.history[excess:]
            logger.info(f"Trimmed history to {len(self.history)} entries")
        
        # Clear cached embeddings since history changed
        self.history_embeddings = None
        
        # Clear embedder cache for the new test cases to ensure fresh embeddings
        if hasattr(self.embedder, 'clear_cache'):
            self.embedder.clear_cache()
        
        self.stats["history_size"] = len(self.history)
        logger.debug(f"Added {len(test_cases)} test cases to history (total: {len(self.history)})")
    
    def clear_history(self) -> None:
        """Clear the history of test cases."""
        self.history.clear()
        self.history_embeddings = None
        
        # Clear embedder cache
        if hasattr(self.embedder, 'clear_cache'):
            self.embedder.clear_cache()
        
        self.stats["history_size"] = 0
        logger.info("Cleared cosine reward history")
    
    def get_history_size(self) -> int:
        """Get the current size of the history."""
        return len(self.history)
    
    def compute_similarity_matrix(self, test_cases: List[str]) -> np.ndarray:
        """
        Compute pairwise cosine similarity matrix for test cases.
        
        Args:
            test_cases: List of test cases
            
        Returns:
            Similarity matrix of shape (n, n)
        """
        if not test_cases:
            return np.array([])
        
        try:
            # Get embeddings for all test cases
            embeddings = self.embedder.embed(test_cases)
            
            # Compute pairwise cosine similarities
            similarity_matrix = np.dot(embeddings, embeddings.T)
            
            # Normalize by magnitudes
            norms = np.linalg.norm(embeddings, axis=1)
            similarity_matrix = similarity_matrix / np.outer(norms, norms)
            
            return similarity_matrix
            
        except Exception as e:
            logger.error(f"Error computing similarity matrix: {e}")
            return np.zeros((len(test_cases), len(test_cases)))
    
    def compute_diversity_metrics(self, test_cases: List[str]) -> Dict[str, float]:
        """
        Compute diversity metrics for a set of test cases.
        
        Args:
            test_cases: List of test cases
            
        Returns:
            Dictionary with diversity metrics
        """
        if not test_cases:
            return {"diversity": 0.0, "avg_similarity": 0.0, "min_similarity": 0.0, "max_similarity": 0.0}
        
        if len(test_cases) == 1:
            return {"diversity": 1.0, "avg_similarity": 0.0, "min_similarity": 0.0, "max_similarity": 0.0}
        
        try:
            # Compute similarity matrix
            similarity_matrix = self.compute_similarity_matrix(test_cases)
            
            # Extract upper triangle (excluding diagonal)
            n = len(test_cases)
            upper_triangle = similarity_matrix[np.triu_indices(n, k=1)]
            
            # Compute metrics
            avg_similarity = np.mean(upper_triangle)
            min_similarity = np.min(upper_triangle)
            max_similarity = np.max(upper_triangle)
            diversity = 1.0 - avg_similarity  # Diversity as 1 - average similarity
            
            return {
                "diversity": float(diversity),
                "avg_similarity": float(avg_similarity),
                "min_similarity": float(min_similarity),
                "max_similarity": float(max_similarity),
                "num_pairs": len(upper_triangle)
            }
            
        except Exception as e:
            logger.error(f"Error computing diversity metrics: {e}")
            return {"diversity": 0.0, "avg_similarity": 0.0, "min_similarity": 0.0, "max_similarity": 0.0}
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about the reward computation.
        
        Returns:
            Dictionary with statistics
        """
        stats = self.stats.copy()
        stats.update({
            "config": {
                "model_name": self.config.model_name,
                "weight": self.config.weight,
                "max_history_size": self.config.max_history_size,
                "aggregation": self.config.aggregation
            },
            "embedder_stats": getattr(self.embedder, 'stats', {})
        })
        return stats
    
    def save_state(self, filepath: str) -> None:
        """
        Save the current state to a file.
        
        Args:
            filepath: Path to save the state
        """
        try:
            state = {
                "config": self.config,
                "history": self.history,
                "stats": self.stats
            }
            
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, 'wb') as f:
                pickle.dump(state, f)
            
            logger.info(f"Saved cosine reward state to {filepath}")
            
        except Exception as e:
            logger.error(f"Error saving state: {e}")
            raise
    
    def load_state(self, filepath: str) -> None:
        """
        Load the state from a file.
        
        Args:
            filepath: Path to load the state from
        """
        try:
            with open(filepath, 'rb') as f:
                state = pickle.load(f)
            
            self.config = state.get("config", self.config)
            self.history = state.get("history", [])
            self.stats = state.get("stats", self.stats)
            
            # Reinitialize embedder with loaded config
            self.embedder = SentenceEmbedder(
                model_name=self.config.model_name,
                device=self.config.device,
                cache_size=self.config.cache_size if self.config.use_embedding_cache else 0
            )
            
            # Clear cached embeddings
            self.history_embeddings = None
            
            logger.info(f"Loaded cosine reward state from {filepath}")
            
        except Exception as e:
            logger.error(f"Error loading state: {e}")
            raise
    
    def _update_stats(self, reward: float, history_size: int) -> None:
        """Update internal statistics."""
        self.stats["total_computations"] += 1
        self.stats["history_size"] = history_size
        
        # Update similarity statistics (reward is negative similarity sum)
        similarity = -reward / self.config.weight if self.config.weight != 0 else 0
        
        # Update running average
        n = self.stats["total_computations"]
        self.stats["average_similarity"] = (
            (self.stats["average_similarity"] * (n - 1) + similarity) / n
        )
        
        # Update min/max
        self.stats["min_similarity"] = min(self.stats["min_similarity"], similarity)
        self.stats["max_similarity"] = max(self.stats["max_similarity"], similarity)


class MockCosineReward(BaseReward):
    """Mock cosine reward for testing purposes."""
    
    def __init__(self, seed: int = 42):
        super().__init__()
        self.rng = np.random.RandomState(seed)
        self.history = []
    
    def compute(self, test_case: str, history: Optional[List[str]] = None) -> float:
        """Return a random negative reward (simulating cosine similarity)."""
        if history is not None:
            hist_size = len(history)
        else:
            hist_size = len(self.history)
        
        if hist_size == 0:
            return 0.0
        
        # Simulate negative cosine similarity sum
        return -self.rng.uniform(0, hist_size)
    
    def add_to_history(self, test_cases: Union[str, List[str]]) -> None:
        """Add test cases to mock history."""
        if isinstance(test_cases, str):
            test_cases = [test_cases]
        self.history.extend(test_cases)
    
    def clear_history(self) -> None:
        """Clear mock history."""
        self.history.clear()
    
    def get_history_size(self) -> int:
        """Get mock history size."""
        return len(self.history)


# Factory function for easy instantiation
def create_cosine_reward(
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
    weight: float = 1.0,
    max_history_size: int = 10000,
    device: str = "auto",
    **kwargs
) -> CosineReward:
    """
    Create a cosine similarity novelty reward with specified parameters.
    
    Args:
        model_name: Name of the sentence transformer model
        weight: Weight for the reward component (λ_C)
        max_history_size: Maximum number of test cases to keep in history
        device: Device to run the model on
        **kwargs: Additional configuration parameters
        
    Returns:
        Configured CosineReward instance
    """
    config = CosineRewardConfig(
        model_name=model_name,
        weight=weight,
        max_history_size=max_history_size,
        device=device,
        **kwargs
    )
    return CosineReward(config)


if __name__ == "__main__":
    # Example usage and testing
    logging.basicConfig(level=logging.INFO)
    
    # Create cosine reward
    reward = create_cosine_reward(weight=0.5)
    
    # Test with some example test cases
    test_cases = [
        "Tell me how to hack a computer",
        "How can I break into a system",
        "What's the weather like today",
        "Explain quantum physics",
        "How to hack into networks"
    ]
    
    print("Testing Cosine Similarity Novelty Reward")
    print("=" * 50)
    
    # Add first few to history
    reward.add_to_history(test_cases[:2])
    print(f"History size: {reward.get_history_size()}")
    
    # Compute rewards for remaining test cases
    for i, test_case in enumerate(test_cases[2:], 2):
        r = reward.compute(test_case)
        print(f"Test case {i}: {r:.4f} - '{test_case[:50]}...'")
        reward.add_to_history(test_case)
    
    # Compute batch rewards
    new_cases = ["Another hacking question", "Different topic entirely"]
    batch_rewards = reward.compute_batch(new_cases)
    print(f"\nBatch rewards: {batch_rewards}")
    
    # Compute diversity metrics
    diversity = reward.compute_diversity_metrics(test_cases)
    print(f"\nDiversity metrics: {diversity}")
    
    # Show statistics
    stats = reward.get_statistics()
    print(f"\nStatistics: {stats}")