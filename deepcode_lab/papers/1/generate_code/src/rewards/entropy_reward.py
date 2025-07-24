"""
Entropy Reward Component for Curiosity-Driven Red-Teaming

This module implements the entropy bonus reward component that encourages exploration
by providing rewards based on the entropy of the generated test cases. Higher entropy
indicates more diverse and exploratory generation.

The entropy reward is computed as: -λ_E * log(π(x|z))
where π(x|z) is the probability of generating test case x given instruction z.
"""

import logging
import numpy as np
from typing import List, Union, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
import torch
import torch.nn.functional as F
from transformers import AutoTokenizer

from .base_reward import BaseReward

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class EntropyRewardConfig:
    """Configuration for entropy reward computation."""
    
    # Entropy coefficient (λ_E in the paper)
    entropy_coefficient: float = 0.01
    
    # Reward scaling and normalization
    scale_factor: float = 1.0
    normalize: bool = True
    clip_min: Optional[float] = None
    clip_max: Optional[float] = None
    
    # Model configuration
    model_name: str = "gpt2"
    max_length: int = 512
    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    
    # Computation options
    use_attention_mask: bool = True
    aggregate_method: str = "mean"  # "mean", "sum", "max"
    temperature: float = 1.0
    
    # Caching and efficiency
    enable_caching: bool = True
    cache_size: int = 1000
    batch_size: int = 32
    
    # Statistics tracking
    track_statistics: bool = True
    save_detailed_stats: bool = False


class EntropyReward(BaseReward):
    """
    Entropy reward component for curiosity-driven red-teaming.
    
    Computes entropy bonus rewards to encourage exploration and diversity
    in generated test cases. The reward is based on the negative log probability
    of the generated sequences under the current policy.
    
    Formula: R_entropy = -λ_E * log(π(x|z))
    """
    
    def __init__(self, config: Optional[EntropyRewardConfig] = None, red_team_model=None):
        """
        Initialize the entropy reward component.
        
        Args:
            config: Configuration for entropy reward computation
            red_team_model: The red team model for computing log probabilities
        """
        super().__init__()
        self.config = config or EntropyRewardConfig()
        self.red_team_model = red_team_model
        
        # Initialize tokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(self.config.model_name)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        
        # Caching for efficiency
        self.cache = {} if self.config.enable_caching else None
        self.cache_hits = 0
        self.cache_misses = 0
        
        # Statistics tracking
        self.stats = {
            'total_computations': 0,
            'total_entropy_sum': 0.0,
            'min_entropy': float('inf'),
            'max_entropy': float('-inf'),
            'entropy_history': [],
            'reward_history': []
        } if self.config.track_statistics else {}
        
        logger.info(f"Initialized EntropyReward with coefficient {self.config.entropy_coefficient}")
    
    def compute(self, test_cases: Union[str, List[str]], 
                instructions: Optional[Union[str, List[str]]] = None,
                **kwargs) -> Union[float, List[float]]:
        """
        Compute entropy rewards for test cases.
        
        Args:
            test_cases: Single test case or list of test cases
            instructions: Optional instructions used to generate the test cases
            **kwargs: Additional arguments
            
        Returns:
            Entropy reward(s) for the test case(s)
        """
        # Handle single vs batch input
        is_single = isinstance(test_cases, str)
        if is_single:
            test_cases = [test_cases]
            if instructions is not None and isinstance(instructions, str):
                instructions = [instructions]
        
        # Compute entropy rewards
        rewards = self._compute_batch_entropy(test_cases, instructions, **kwargs)
        
        # Apply post-processing
        rewards = self.postprocess_reward(rewards)
        
        # Update statistics
        if self.config.track_statistics:
            self._update_statistics(rewards)
        
        return rewards[0] if is_single else rewards
    
    def _compute_batch_entropy(self, test_cases: List[str], 
                              instructions: Optional[List[str]] = None,
                              **kwargs) -> List[float]:
        """
        Compute entropy rewards for a batch of test cases.
        
        Args:
            test_cases: List of test cases
            instructions: Optional list of instructions
            **kwargs: Additional arguments
            
        Returns:
            List of entropy rewards
        """
        if not test_cases:
            return []
        
        # Check cache first
        if self.cache is not None:
            cached_rewards = []
            uncached_indices = []
            uncached_cases = []
            uncached_instructions = []
            
            for i, case in enumerate(test_cases):
                cache_key = self._get_cache_key(case, instructions[i] if instructions else None)
                if cache_key in self.cache:
                    cached_rewards.append((i, self.cache[cache_key]))
                    self.cache_hits += 1
                else:
                    uncached_indices.append(i)
                    uncached_cases.append(case)
                    if instructions:
                        uncached_instructions.append(instructions[i])
                    self.cache_misses += 1
            
            # Compute uncached rewards
            if uncached_cases:
                uncached_rewards = self._compute_entropy_from_model(
                    uncached_cases, 
                    uncached_instructions if instructions else None
                )
                
                # Update cache
                for idx, case, reward in zip(uncached_indices, uncached_cases, uncached_rewards):
                    cache_key = self._get_cache_key(case, instructions[idx] if instructions else None)
                    self._update_cache(cache_key, reward)
            else:
                uncached_rewards = []
            
            # Combine cached and uncached rewards
            all_rewards = [0.0] * len(test_cases)
            for i, reward in cached_rewards:
                all_rewards[i] = reward
            for i, idx in enumerate(uncached_indices):
                all_rewards[idx] = uncached_rewards[i]
            
            return all_rewards
        else:
            # No caching - compute directly
            return self._compute_entropy_from_model(test_cases, instructions)
    
    def _compute_entropy_from_model(self, test_cases: List[str], 
                                   instructions: Optional[List[str]] = None) -> List[float]:
        """
        Compute entropy rewards using the red team model.
        
        Args:
            test_cases: List of test cases
            instructions: Optional list of instructions
            
        Returns:
            List of entropy rewards
        """
        if self.red_team_model is None:
            logger.warning("No red team model provided, using fallback entropy computation")
            return self._compute_fallback_entropy(test_cases)
        
        try:
            # Prepare inputs
            if instructions:
                # Combine instructions and test cases
                full_texts = [f"{inst} {case}" for inst, case in zip(instructions, test_cases)]
            else:
                full_texts = test_cases
            
            # Tokenize inputs
            tokenized = self.tokenizer(
                full_texts,
                padding=True,
                truncation=True,
                max_length=self.config.max_length,
                return_tensors="pt"
            )
            
            # Move to device
            input_ids = tokenized['input_ids'].to(self.config.device)
            attention_mask = tokenized['attention_mask'].to(self.config.device)
            
            # Compute entropy using the red team model
            if hasattr(self.red_team_model, 'compute_entropy'):
                entropies = self.red_team_model.compute_entropy(input_ids, attention_mask)
            else:
                # Fallback: compute entropy from log probabilities
                log_probs = self.red_team_model.compute_log_probs(input_ids, attention_mask)
                entropies = self._compute_entropy_from_logprobs(log_probs, attention_mask)
            
            # Convert to rewards: -λ_E * entropy
            rewards = [-self.config.entropy_coefficient * entropy.item() for entropy in entropies]
            
            return rewards
            
        except Exception as e:
            logger.error(f"Error computing entropy from model: {e}")
            return self._compute_fallback_entropy(test_cases)
    
    def _compute_entropy_from_logprobs(self, log_probs: torch.Tensor, 
                                      attention_mask: torch.Tensor) -> torch.Tensor:
        """
        Compute entropy from log probabilities.
        
        Args:
            log_probs: Log probabilities tensor [batch_size, seq_len, vocab_size]
            attention_mask: Attention mask tensor [batch_size, seq_len]
            
        Returns:
            Entropy values for each sequence
        """
        # Convert log probs to probabilities
        probs = torch.exp(log_probs)
        
        # Compute entropy: -sum(p * log(p))
        entropy = -torch.sum(probs * log_probs, dim=-1)  # [batch_size, seq_len]
        
        # Apply attention mask and aggregate
        if self.config.use_attention_mask:
            entropy = entropy * attention_mask.float()
            
        if self.config.aggregate_method == "mean":
            # Average over valid tokens
            seq_lengths = attention_mask.sum(dim=1).float()
            entropy = entropy.sum(dim=1) / seq_lengths.clamp(min=1)
        elif self.config.aggregate_method == "sum":
            entropy = entropy.sum(dim=1)
        elif self.config.aggregate_method == "max":
            entropy = entropy.max(dim=1)[0]
        else:
            entropy = entropy.mean(dim=1)
        
        return entropy
    
    def _compute_fallback_entropy(self, test_cases: List[str]) -> List[float]:
        """
        Fallback entropy computation based on text statistics.
        
        Args:
            test_cases: List of test cases
            
        Returns:
            List of approximate entropy rewards
        """
        logger.info("Using fallback entropy computation")
        
        rewards = []
        for case in test_cases:
            # Simple entropy approximation based on character/token diversity
            if not case.strip():
                rewards.append(0.0)
                continue
            
            # Character-level entropy
            char_counts = {}
            for char in case.lower():
                char_counts[char] = char_counts.get(char, 0) + 1
            
            total_chars = len(case)
            char_entropy = 0.0
            for count in char_counts.values():
                prob = count / total_chars
                if prob > 0:
                    char_entropy -= prob * np.log(prob)
            
            # Token-level entropy (simple whitespace tokenization)
            tokens = case.split()
            if tokens:
                token_counts = {}
                for token in tokens:
                    token_counts[token.lower()] = token_counts.get(token.lower(), 0) + 1
                
                total_tokens = len(tokens)
                token_entropy = 0.0
                for count in token_counts.values():
                    prob = count / total_tokens
                    if prob > 0:
                        token_entropy -= prob * np.log(prob)
            else:
                token_entropy = 0.0
            
            # Combine entropies and convert to reward
            combined_entropy = (char_entropy + token_entropy) / 2
            reward = -self.config.entropy_coefficient * combined_entropy
            rewards.append(reward)
        
        return rewards
    
    def compute_batch(self, test_cases_batch: List[List[str]], 
                     instructions_batch: Optional[List[List[str]]] = None,
                     **kwargs) -> List[List[float]]:
        """
        Compute entropy rewards for a batch of test case lists.
        
        Args:
            test_cases_batch: List of test case lists
            instructions_batch: Optional list of instruction lists
            **kwargs: Additional arguments
            
        Returns:
            List of entropy reward lists
        """
        results = []
        for i, test_cases in enumerate(test_cases_batch):
            instructions = instructions_batch[i] if instructions_batch else None
            rewards = self.compute(test_cases, instructions, **kwargs)
            results.append(rewards if isinstance(rewards, list) else [rewards])
        
        return results
    
    def set_red_team_model(self, model):
        """
        Set the red team model for entropy computation.
        
        Args:
            model: Red team model instance
        """
        self.red_team_model = model
        logger.info("Updated red team model for entropy computation")
    
    def _get_cache_key(self, test_case: str, instruction: Optional[str] = None) -> str:
        """Generate cache key for a test case and instruction pair."""
        if instruction:
            return f"{instruction}|{test_case}"
        return test_case
    
    def _update_cache(self, key: str, reward: float):
        """Update cache with new reward, managing cache size."""
        if self.cache is None:
            return
        
        if len(self.cache) >= self.config.cache_size:
            # Remove oldest entry (simple FIFO)
            oldest_key = next(iter(self.cache))
            del self.cache[oldest_key]
        
        self.cache[key] = reward
    
    def _update_statistics(self, rewards: List[float]):
        """Update internal statistics with new rewards."""
        if not self.config.track_statistics:
            return
        
        for reward in rewards:
            self.stats['total_computations'] += 1
            self.stats['total_entropy_sum'] += reward
            self.stats['min_entropy'] = min(self.stats['min_entropy'], reward)
            self.stats['max_entropy'] = max(self.stats['max_entropy'], reward)
            
            if self.config.save_detailed_stats:
                self.stats['reward_history'].append(reward)
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about entropy reward computation.
        
        Returns:
            Dictionary containing computation statistics
        """
        if not self.config.track_statistics:
            return {}
        
        stats = self.stats.copy()
        
        # Add derived statistics
        if stats['total_computations'] > 0:
            stats['mean_entropy'] = stats['total_entropy_sum'] / stats['total_computations']
        else:
            stats['mean_entropy'] = 0.0
        
        # Cache statistics
        if self.cache is not None:
            stats['cache_hits'] = self.cache_hits
            stats['cache_misses'] = self.cache_misses
            stats['cache_hit_rate'] = (
                self.cache_hits / (self.cache_hits + self.cache_misses)
                if (self.cache_hits + self.cache_misses) > 0 else 0.0
            )
            stats['cache_size'] = len(self.cache)
        
        return stats
    
    def reset_statistics(self):
        """Reset all statistics."""
        if self.config.track_statistics:
            self.stats = {
                'total_computations': 0,
                'total_entropy_sum': 0.0,
                'min_entropy': float('inf'),
                'max_entropy': float('-inf'),
                'entropy_history': [],
                'reward_history': []
            }
        
        self.cache_hits = 0
        self.cache_misses = 0
    
    def clear_cache(self):
        """Clear the reward cache."""
        if self.cache is not None:
            self.cache.clear()
            logger.info("Cleared entropy reward cache")
    
    def save_state(self, filepath: str):
        """
        Save the current state to a file.
        
        Args:
            filepath: Path to save the state
        """
        import pickle
        import os
        
        state = {
            'config': self.config,
            'cache': self.cache,
            'stats': self.stats,
            'cache_hits': self.cache_hits,
            'cache_misses': self.cache_misses
        }
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'wb') as f:
            pickle.dump(state, f)
        
        logger.info(f"Saved entropy reward state to {filepath}")
    
    def load_state(self, filepath: str):
        """
        Load state from a file.
        
        Args:
            filepath: Path to load the state from
        """
        import pickle
        
        try:
            with open(filepath, 'rb') as f:
                state = pickle.load(f)
            
            self.config = state.get('config', self.config)
            self.cache = state.get('cache', {})
            self.stats = state.get('stats', {})
            self.cache_hits = state.get('cache_hits', 0)
            self.cache_misses = state.get('cache_misses', 0)
            
            logger.info(f"Loaded entropy reward state from {filepath}")
            
        except Exception as e:
            logger.error(f"Error loading state from {filepath}: {e}")
    
    def compute_entropy_statistics(self, test_cases: List[str], 
                                  instructions: Optional[List[str]] = None) -> Dict[str, float]:
        """
        Compute detailed entropy statistics for a set of test cases.
        
        Args:
            test_cases: List of test cases
            instructions: Optional list of instructions
            
        Returns:
            Dictionary with entropy statistics
        """
        if not test_cases:
            return {}
        
        rewards = self.compute(test_cases, instructions)
        if not isinstance(rewards, list):
            rewards = [rewards]
        
        # Convert rewards back to entropies (remove coefficient)
        entropies = [-r / self.config.entropy_coefficient for r in rewards]
        
        return {
            'mean_entropy': np.mean(entropies),
            'std_entropy': np.std(entropies),
            'min_entropy': np.min(entropies),
            'max_entropy': np.max(entropies),
            'median_entropy': np.median(entropies),
            'entropy_range': np.max(entropies) - np.min(entropies),
            'num_samples': len(entropies)
        }
    
    def analyze_entropy_distribution(self, test_cases: List[str], 
                                   instructions: Optional[List[str]] = None,
                                   num_bins: int = 10) -> Dict[str, Any]:
        """
        Analyze the distribution of entropy values.
        
        Args:
            test_cases: List of test cases
            instructions: Optional list of instructions
            num_bins: Number of bins for histogram
            
        Returns:
            Dictionary with distribution analysis
        """
        if not test_cases:
            return {}
        
        rewards = self.compute(test_cases, instructions)
        if not isinstance(rewards, list):
            rewards = [rewards]
        
        # Convert rewards back to entropies
        entropies = [-r / self.config.entropy_coefficient for r in rewards]
        
        # Compute histogram
        hist, bin_edges = np.histogram(entropies, bins=num_bins)
        
        return {
            'histogram': hist.tolist(),
            'bin_edges': bin_edges.tolist(),
            'statistics': self.compute_entropy_statistics(test_cases, instructions),
            'percentiles': {
                '25th': np.percentile(entropies, 25),
                '50th': np.percentile(entropies, 50),
                '75th': np.percentile(entropies, 75),
                '90th': np.percentile(entropies, 90),
                '95th': np.percentile(entropies, 95)
            }
        }


# Utility functions for entropy computation
def compute_text_entropy(text: str, method: str = "character") -> float:
    """
    Compute entropy of a text string.
    
    Args:
        text: Input text
        method: "character" or "token" level entropy
        
    Returns:
        Entropy value
    """
    if not text.strip():
        return 0.0
    
    if method == "character":
        # Character-level entropy
        char_counts = {}
        for char in text.lower():
            char_counts[char] = char_counts.get(char, 0) + 1
        
        total_chars = len(text)
        entropy = 0.0
        for count in char_counts.values():
            prob = count / total_chars
            if prob > 0:
                entropy -= prob * np.log(prob)
        
        return entropy
    
    elif method == "token":
        # Token-level entropy
        tokens = text.split()
        if not tokens:
            return 0.0
        
        token_counts = {}
        for token in tokens:
            token_counts[token.lower()] = token_counts.get(token.lower(), 0) + 1
        
        total_tokens = len(tokens)
        entropy = 0.0
        for count in token_counts.values():
            prob = count / total_tokens
            if prob > 0:
                entropy -= prob * np.log(prob)
        
        return entropy
    
    else:
        raise ValueError(f"Unknown entropy method: {method}")


def batch_entropy_computation(texts: List[str], method: str = "character") -> List[float]:
    """
    Compute entropy for a batch of texts.
    
    Args:
        texts: List of input texts
        method: "character" or "token" level entropy
        
    Returns:
        List of entropy values
    """
    return [compute_text_entropy(text, method) for text in texts]


# Example usage and testing
if __name__ == "__main__":
    # Test entropy reward computation
    config = EntropyRewardConfig(
        entropy_coefficient=0.01,
        track_statistics=True
    )
    
    entropy_reward = EntropyReward(config)
    
    # Test cases
    test_cases = [
        "This is a simple test case.",
        "A more complex test case with various words and punctuation!",
        "Short.",
        "This is a longer test case that contains many different words and should have higher entropy than the shorter ones.",
        ""
    ]
    
    # Compute rewards
    rewards = entropy_reward.compute(test_cases)
    print("Entropy rewards:", rewards)
    
    # Get statistics
    stats = entropy_reward.get_statistics()
    print("Statistics:", stats)
    
    # Analyze entropy distribution
    analysis = entropy_reward.analyze_entropy_distribution(test_cases)
    print("Distribution analysis:", analysis)