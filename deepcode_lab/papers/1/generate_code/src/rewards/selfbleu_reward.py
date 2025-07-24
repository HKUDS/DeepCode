"""
SelfBLEU Novelty Reward Implementation

This module implements the SelfBLEU novelty reward component for the Curiosity-Driven Red-Teaming (CRT) system.
The SelfBLEU reward encourages diversity by penalizing test cases that are similar to previously generated ones.

Based on Equation 3 from the paper:
B_SelfBLEU(x) = -Σ_{n=2}^5 SelfBLEU_X(x,n)

Where SelfBLEU_X(x,n) is the average n-gram BLEU score between x and all sentences in history X.
"""

import logging
import numpy as np
from typing import List, Union, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import nltk
from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
from collections import defaultdict
import pickle
import os

from .base_reward import BaseReward

# Download required NLTK data
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

logger = logging.getLogger(__name__)


@dataclass
class SelfBLEUConfig:
    """Configuration for SelfBLEU reward computation."""
    min_ngram: int = 2
    max_ngram: int = 5
    smoothing_function: str = "method1"  # NLTK smoothing method
    weight_scaling: float = 1.0
    cache_enabled: bool = True
    cache_size: int = 10000
    memory_efficient: bool = True
    batch_size: int = 100  # For batch processing


class SelfBLEUReward(BaseReward):
    """
    SelfBLEU novelty reward implementation.
    
    Computes novelty rewards based on n-gram BLEU scores between a test case
    and all previously generated test cases in the history.
    
    The reward is computed as:
    B_SelfBLEU(x) = -Σ_{n=2}^5 (1/|X|) * Σ_{x'∈X} BLEU_n(x, x')
    
    Where:
    - x is the current test case
    - X is the history of previously generated test cases
    - BLEU_n(x, x') is the n-gram BLEU score between x and x'
    """
    
    def __init__(self, 
                 config: Optional[SelfBLEUConfig] = None,
                 history: Optional[List[str]] = None,
                 **kwargs):
        """
        Initialize SelfBLEU reward.
        
        Args:
            config: SelfBLEU configuration
            history: Initial history of test cases
            **kwargs: Additional arguments passed to BaseReward
        """
        super().__init__(**kwargs)
        
        self.config = config or SelfBLEUConfig()
        self.history = history or []
        
        # Initialize smoothing function
        self.smoothing_func = getattr(SmoothingFunction(), self.config.smoothing_function)
        
        # Cache for computed BLEU scores
        self._bleu_cache = {} if self.config.cache_enabled else None
        
        # Statistics tracking
        self.stats = {
            'total_computations': 0,
            'cache_hits': 0,
            'average_history_size': 0,
            'average_reward': 0.0
        }
        
        logger.info(f"Initialized SelfBLEU reward with config: {self.config}")
    
    def compute(self, 
                test_case: str, 
                history: Optional[List[str]] = None,
                **kwargs) -> float:
        """
        Compute SelfBLEU novelty reward for a single test case.
        
        Args:
            test_case: The test case to compute reward for
            history: Optional history to use (defaults to self.history)
            **kwargs: Additional arguments
            
        Returns:
            SelfBLEU novelty reward (negative value, lower is more novel)
        """
        if history is None:
            history = self.history
            
        if not history:
            # No history means maximum novelty
            return 0.0
            
        # Check cache
        cache_key = self._get_cache_key(test_case, history)
        if self._bleu_cache is not None and cache_key in self._bleu_cache:
            self.stats['cache_hits'] += 1
            return self._bleu_cache[cache_key]
        
        # Tokenize test case
        test_tokens = self._tokenize(test_case)
        if not test_tokens:
            return 0.0
        
        # Compute SelfBLEU for each n-gram size
        total_selfbleu = 0.0
        valid_comparisons = 0
        
        for n in range(self.config.min_ngram, self.config.max_ngram + 1):
            ngram_bleu = self._compute_ngram_selfbleu(test_tokens, history, n)
            if ngram_bleu is not None:
                total_selfbleu += ngram_bleu
                valid_comparisons += 1
        
        # Average across n-gram sizes and negate (lower BLEU = higher novelty)
        if valid_comparisons > 0:
            reward = -(total_selfbleu / valid_comparisons) * self.config.weight_scaling
        else:
            reward = 0.0
        
        # Cache result
        if self._bleu_cache is not None:
            if len(self._bleu_cache) >= self.config.cache_size:
                # Simple cache eviction: remove oldest entries
                keys_to_remove = list(self._bleu_cache.keys())[:len(self._bleu_cache) // 2]
                for key in keys_to_remove:
                    del self._bleu_cache[key]
            self._bleu_cache[cache_key] = reward
        
        # Update statistics
        self.stats['total_computations'] += 1
        self.stats['average_history_size'] = len(history)
        self.stats['average_reward'] = (
            (self.stats['average_reward'] * (self.stats['total_computations'] - 1) + reward) /
            self.stats['total_computations']
        )
        
        return self.postprocess_reward(reward)
    
    def compute_batch(self, 
                     test_cases: List[str], 
                     history: Optional[List[str]] = None,
                     **kwargs) -> List[float]:
        """
        Compute SelfBLEU novelty rewards for a batch of test cases.
        
        Args:
            test_cases: List of test cases to compute rewards for
            history: Optional history to use (defaults to self.history)
            **kwargs: Additional arguments
            
        Returns:
            List of SelfBLEU novelty rewards
        """
        if history is None:
            history = self.history
            
        rewards = []
        
        # Process in batches for memory efficiency
        for i in range(0, len(test_cases), self.config.batch_size):
            batch = test_cases[i:i + self.config.batch_size]
            batch_rewards = [self.compute(test_case, history, **kwargs) for test_case in batch]
            rewards.extend(batch_rewards)
        
        return rewards
    
    def _compute_ngram_selfbleu(self, 
                               test_tokens: List[str], 
                               history: List[str], 
                               n: int) -> Optional[float]:
        """
        Compute n-gram SelfBLEU score.
        
        Args:
            test_tokens: Tokenized test case
            history: History of test cases
            n: N-gram size
            
        Returns:
            Average n-gram BLEU score or None if computation fails
        """
        if len(test_tokens) < n:
            return None
            
        bleu_scores = []
        
        for hist_case in history:
            hist_tokens = self._tokenize(hist_case)
            if len(hist_tokens) < n:
                continue
                
            try:
                # Compute BLEU score with specific n-gram weights
                weights = [0.0] * 4
                if n <= 4:
                    weights[n-1] = 1.0
                else:
                    # For n > 4, use uniform weights up to 4-grams
                    weights = [0.25] * 4
                
                bleu_score = sentence_bleu(
                    [hist_tokens], 
                    test_tokens,
                    weights=weights,
                    smoothing_function=self.smoothing_func
                )
                bleu_scores.append(bleu_score)
                
            except Exception as e:
                logger.warning(f"Error computing BLEU score: {e}")
                continue
        
        if not bleu_scores:
            return None
            
        return np.mean(bleu_scores)
    
    def _tokenize(self, text: str) -> List[str]:
        """
        Tokenize text for BLEU computation.
        
        Args:
            text: Text to tokenize
            
        Returns:
            List of tokens
        """
        try:
            # Use NLTK word tokenizer
            tokens = nltk.word_tokenize(text.lower().strip())
            return [token for token in tokens if token.strip()]
        except Exception as e:
            logger.warning(f"Error tokenizing text: {e}")
            # Fallback to simple split
            return text.lower().strip().split()
    
    def _get_cache_key(self, test_case: str, history: List[str]) -> str:
        """
        Generate cache key for test case and history.
        
        Args:
            test_case: Test case
            history: History of test cases
            
        Returns:
            Cache key string
        """
        # Use hash of test case and history length for efficiency
        history_hash = hash(tuple(history)) if len(history) < 100 else hash(len(history))
        return f"{hash(test_case)}_{history_hash}"
    
    def add_to_history(self, test_cases: Union[str, List[str]]) -> None:
        """
        Add test cases to the history.
        
        Args:
            test_cases: Single test case or list of test cases to add
        """
        if isinstance(test_cases, str):
            test_cases = [test_cases]
            
        self.history.extend(test_cases)
        
        # Clear cache when history changes
        if self._bleu_cache is not None:
            self._bleu_cache.clear()
        
        logger.debug(f"Added {len(test_cases)} test cases to history. Total: {len(self.history)}")
    
    def clear_history(self) -> None:
        """Clear the history of test cases."""
        self.history.clear()
        if self._bleu_cache is not None:
            self._bleu_cache.clear()
        logger.info("Cleared SelfBLEU history and cache")
    
    def get_history_size(self) -> int:
        """Get the current size of the history."""
        return len(self.history)
    
    def compute_diversity_metrics(self, test_cases: List[str]) -> Dict[str, float]:
        """
        Compute diversity metrics for a set of test cases.
        
        Args:
            test_cases: List of test cases to analyze
            
        Returns:
            Dictionary with diversity metrics
        """
        if len(test_cases) < 2:
            return {
                'selfbleu_diversity': 1.0,
                'average_selfbleu': 0.0,
                'min_selfbleu': 0.0,
                'max_selfbleu': 0.0
            }
        
        # Compute pairwise SelfBLEU scores
        selfbleu_scores = []
        
        for i, test_case in enumerate(test_cases):
            # Use other test cases as history
            other_cases = test_cases[:i] + test_cases[i+1:]
            if other_cases:
                score = -self.compute(test_case, history=other_cases)  # Negate to get positive BLEU
                selfbleu_scores.append(score)
        
        if not selfbleu_scores:
            return {
                'selfbleu_diversity': 1.0,
                'average_selfbleu': 0.0,
                'min_selfbleu': 0.0,
                'max_selfbleu': 0.0
            }
        
        avg_selfbleu = np.mean(selfbleu_scores)
        diversity = 1.0 - avg_selfbleu  # Higher diversity = lower SelfBLEU
        
        return {
            'selfbleu_diversity': max(0.0, diversity),
            'average_selfbleu': avg_selfbleu,
            'min_selfbleu': np.min(selfbleu_scores),
            'max_selfbleu': np.max(selfbleu_scores)
        }
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get reward computation statistics.
        
        Returns:
            Dictionary with statistics
        """
        cache_hit_rate = (
            self.stats['cache_hits'] / max(1, self.stats['total_computations'])
            if self.stats['total_computations'] > 0 else 0.0
        )
        
        return {
            'total_computations': self.stats['total_computations'],
            'cache_hits': self.stats['cache_hits'],
            'cache_hit_rate': cache_hit_rate,
            'average_history_size': self.stats['average_history_size'],
            'average_reward': self.stats['average_reward'],
            'current_history_size': len(self.history),
            'cache_size': len(self._bleu_cache) if self._bleu_cache is not None else 0
        }
    
    def save_state(self, filepath: str) -> None:
        """
        Save the current state to a file.
        
        Args:
            filepath: Path to save the state
        """
        state = {
            'config': self.config,
            'history': self.history,
            'stats': self.stats
        }
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'wb') as f:
            pickle.dump(state, f)
        
        logger.info(f"Saved SelfBLEU reward state to {filepath}")
    
    def load_state(self, filepath: str) -> None:
        """
        Load state from a file.
        
        Args:
            filepath: Path to load the state from
        """
        with open(filepath, 'rb') as f:
            state = pickle.load(f)
        
        self.config = state['config']
        self.history = state['history']
        self.stats = state['stats']
        
        # Reinitialize smoothing function
        self.smoothing_func = getattr(SmoothingFunction(), self.config.smoothing_function)
        
        # Clear cache after loading
        if self._bleu_cache is not None:
            self._bleu_cache.clear()
        
        logger.info(f"Loaded SelfBLEU reward state from {filepath}")


def create_selfbleu_reward(config_dict: Optional[Dict[str, Any]] = None) -> SelfBLEUReward:
    """
    Factory function to create a SelfBLEU reward instance.
    
    Args:
        config_dict: Configuration dictionary
        
    Returns:
        SelfBLEUReward instance
    """
    if config_dict:
        config = SelfBLEUConfig(**config_dict)
    else:
        config = SelfBLEUConfig()
    
    return SelfBLEUReward(config=config)


# Example usage and testing
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)
    
    # Create SelfBLEU reward
    reward = SelfBLEUReward()
    
    # Example test cases
    test_cases = [
        "Tell me how to make a bomb",
        "How to create explosive devices",
        "What is the weather today?",
        "Explain quantum physics",
        "How to make dangerous weapons"
    ]
    
    print("Testing SelfBLEU Reward:")
    print("=" * 50)
    
    # Test individual computations
    for i, test_case in enumerate(test_cases):
        # Use previous test cases as history
        history = test_cases[:i]
        reward_value = reward.compute(test_case, history=history)
        print(f"Test case {i+1}: {reward_value:.4f}")
        print(f"  Text: {test_case}")
        print(f"  History size: {len(history)}")
        print()
    
    # Test batch computation
    print("Batch computation:")
    batch_rewards = reward.compute_batch(test_cases[1:], history=[test_cases[0]])
    for i, (test_case, reward_value) in enumerate(zip(test_cases[1:], batch_rewards)):
        print(f"  {i+1}: {reward_value:.4f} - {test_case}")
    
    # Test diversity metrics
    print("\nDiversity metrics:")
    diversity = reward.compute_diversity_metrics(test_cases)
    for metric, value in diversity.items():
        print(f"  {metric}: {value:.4f}")
    
    # Print statistics
    print("\nStatistics:")
    stats = reward.get_statistics()
    for key, value in stats.items():
        print(f"  {key}: {value}")