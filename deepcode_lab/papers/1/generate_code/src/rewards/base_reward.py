"""
Base reward interface for the Curiosity-Driven Red-Teaming system.

This module provides the abstract base class for all reward components used in the CRT algorithm.
All reward implementations (effectiveness, novelty, entropy) inherit from this base class.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Union
import logging
import numpy as np
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RewardConfig:
    """Configuration for reward computation."""
    weight: float = 1.0
    normalize: bool = True
    clip_range: Optional[tuple] = None
    cache_enabled: bool = True
    batch_size: int = 32


class BaseReward(ABC):
    """
    Abstract base class for all reward components in the CRT system.
    
    This class defines the interface that all reward implementations must follow,
    including effectiveness rewards R(y), novelty rewards B_i(x), and entropy bonuses.
    """
    
    def __init__(self, config: Optional[RewardConfig] = None):
        """
        Initialize the base reward component.
        
        Args:
            config: Configuration for reward computation
        """
        self.config = config or RewardConfig()
        self.name = self.__class__.__name__
        self._cache = {} if self.config.cache_enabled else None
        self._stats = {
            'total_computations': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'total_reward_sum': 0.0,
            'min_reward': float('inf'),
            'max_reward': float('-inf')
        }
        
        logger.info(f"Initialized {self.name} with config: {self.config}")
    
    @abstractmethod
    def compute(self, *args, **kwargs) -> Union[float, List[float]]:
        """
        Compute the reward for given inputs.
        
        This method must be implemented by all subclasses to define their specific
        reward computation logic.
        
        Returns:
            Computed reward(s) as float or list of floats
        """
        pass
    
    def compute_batch(self, inputs: List[Any], **kwargs) -> List[float]:
        """
        Compute rewards for a batch of inputs.
        
        Args:
            inputs: List of inputs to compute rewards for
            **kwargs: Additional arguments for reward computation
            
        Returns:
            List of computed rewards
        """
        rewards = []
        batch_size = self.config.batch_size
        
        for i in range(0, len(inputs), batch_size):
            batch = inputs[i:i + batch_size]
            batch_rewards = self._compute_batch_internal(batch, **kwargs)
            rewards.extend(batch_rewards)
        
        return rewards
    
    def _compute_batch_internal(self, batch: List[Any], **kwargs) -> List[float]:
        """
        Internal method for batch computation. Can be overridden for efficiency.
        
        Args:
            batch: Batch of inputs
            **kwargs: Additional arguments
            
        Returns:
            List of rewards for the batch
        """
        return [self.compute(item, **kwargs) for item in batch]
    
    def normalize_reward(self, reward: Union[float, List[float]]) -> Union[float, List[float]]:
        """
        Normalize reward values if configured to do so.
        
        Args:
            reward: Raw reward value(s)
            
        Returns:
            Normalized reward value(s)
        """
        if not self.config.normalize:
            return reward
        
        if isinstance(reward, list):
            rewards = np.array(reward)
            if len(rewards) > 1:
                mean = np.mean(rewards)
                std = np.std(rewards)
                if std > 0:
                    rewards = (rewards - mean) / std
            return rewards.tolist()
        else:
            # For single values, use running statistics
            return reward  # Simple case, can be enhanced with running stats
    
    def clip_reward(self, reward: Union[float, List[float]]) -> Union[float, List[float]]:
        """
        Clip reward values to configured range if specified.
        
        Args:
            reward: Reward value(s) to clip
            
        Returns:
            Clipped reward value(s)
        """
        if self.config.clip_range is None:
            return reward
        
        min_val, max_val = self.config.clip_range
        
        if isinstance(reward, list):
            return [max(min_val, min(max_val, r)) for r in reward]
        else:
            return max(min_val, min(max_val, reward))
    
    def postprocess_reward(self, reward: Union[float, List[float]]) -> Union[float, List[float]]:
        """
        Apply post-processing to computed rewards (normalization, clipping, etc.).
        
        Args:
            reward: Raw computed reward(s)
            
        Returns:
            Post-processed reward(s)
        """
        # Apply weight
        if isinstance(reward, list):
            reward = [r * self.config.weight for r in reward]
        else:
            reward = reward * self.config.weight
        
        # Normalize if configured
        reward = self.normalize_reward(reward)
        
        # Clip if configured
        reward = self.clip_reward(reward)
        
        # Update statistics
        self._update_stats(reward)
        
        return reward
    
    def _update_stats(self, reward: Union[float, List[float]]):
        """Update internal statistics for monitoring."""
        if isinstance(reward, list):
            for r in reward:
                self._stats['total_computations'] += 1
                self._stats['total_reward_sum'] += r
                self._stats['min_reward'] = min(self._stats['min_reward'], r)
                self._stats['max_reward'] = max(self._stats['max_reward'], r)
        else:
            self._stats['total_computations'] += 1
            self._stats['total_reward_sum'] += reward
            self._stats['min_reward'] = min(self._stats['min_reward'], reward)
            self._stats['max_reward'] = max(self._stats['max_reward'], reward)
    
    def get_cache_key(self, *args, **kwargs) -> str:
        """
        Generate a cache key for the given inputs.
        
        Args:
            *args: Positional arguments
            **kwargs: Keyword arguments
            
        Returns:
            String cache key
        """
        # Simple hash-based cache key
        import hashlib
        key_str = str(args) + str(sorted(kwargs.items()))
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def get_from_cache(self, cache_key: str) -> Optional[Union[float, List[float]]]:
        """
        Retrieve reward from cache if available.
        
        Args:
            cache_key: Cache key to look up
            
        Returns:
            Cached reward if available, None otherwise
        """
        if self._cache is None:
            return None
        
        if cache_key in self._cache:
            self._stats['cache_hits'] += 1
            return self._cache[cache_key]
        else:
            self._stats['cache_misses'] += 1
            return None
    
    def store_in_cache(self, cache_key: str, reward: Union[float, List[float]]):
        """
        Store computed reward in cache.
        
        Args:
            cache_key: Cache key
            reward: Computed reward to cache
        """
        if self._cache is not None:
            self._cache[cache_key] = reward
    
    def clear_cache(self):
        """Clear the reward cache."""
        if self._cache is not None:
            self._cache.clear()
            logger.info(f"Cleared cache for {self.name}")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get computation statistics for this reward component.
        
        Returns:
            Dictionary containing statistics
        """
        stats = self._stats.copy()
        if stats['total_computations'] > 0:
            stats['average_reward'] = stats['total_reward_sum'] / stats['total_computations']
            stats['cache_hit_rate'] = stats['cache_hits'] / (stats['cache_hits'] + stats['cache_misses']) if (stats['cache_hits'] + stats['cache_misses']) > 0 else 0.0
        else:
            stats['average_reward'] = 0.0
            stats['cache_hit_rate'] = 0.0
        
        return stats
    
    def reset_stats(self):
        """Reset computation statistics."""
        self._stats = {
            'total_computations': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'total_reward_sum': 0.0,
            'min_reward': float('inf'),
            'max_reward': float('-inf')
        }
        logger.info(f"Reset statistics for {self.name}")
    
    def __str__(self) -> str:
        """String representation of the reward component."""
        return f"{self.name}(weight={self.config.weight}, normalize={self.config.normalize})"
    
    def __repr__(self) -> str:
        """Detailed string representation."""
        return f"{self.name}(config={self.config}, stats={self.get_stats()})"


class MockReward(BaseReward):
    """
    Mock reward implementation for testing purposes.
    
    This class provides a simple mock implementation that returns random rewards,
    useful for testing and development when actual reward models are not available.
    """
    
    def __init__(self, config: Optional[RewardConfig] = None, reward_range: tuple = (0.0, 1.0)):
        """
        Initialize mock reward.
        
        Args:
            config: Reward configuration
            reward_range: Range of random rewards to generate
        """
        super().__init__(config)
        self.reward_range = reward_range
        self._rng = np.random.RandomState(42)  # Fixed seed for reproducibility
    
    def compute(self, *args, **kwargs) -> float:
        """
        Compute a mock reward (random value in specified range).
        
        Returns:
            Random reward value
        """
        reward = self._rng.uniform(self.reward_range[0], self.reward_range[1])
        return self.postprocess_reward(reward)
    
    def _compute_batch_internal(self, batch: List[Any], **kwargs) -> List[float]:
        """
        Compute mock rewards for a batch.
        
        Args:
            batch: Batch of inputs
            **kwargs: Additional arguments
            
        Returns:
            List of random rewards
        """
        rewards = self._rng.uniform(
            self.reward_range[0], 
            self.reward_range[1], 
            size=len(batch)
        ).tolist()
        return [self.postprocess_reward(r) for r in rewards]


# Utility functions for reward management
def create_reward_component(reward_type: str, config: Optional[RewardConfig] = None) -> BaseReward:
    """
    Factory function to create reward components.
    
    Args:
        reward_type: Type of reward to create ('mock', 'effectiveness', 'selfbleu', etc.)
        config: Configuration for the reward component
        
    Returns:
        Instantiated reward component
        
    Raises:
        ValueError: If reward_type is not recognized
    """
    if reward_type.lower() == 'mock':
        return MockReward(config)
    else:
        raise ValueError(f"Unknown reward type: {reward_type}")


def combine_rewards(rewards: List[BaseReward], inputs: List[Any], **kwargs) -> List[float]:
    """
    Combine multiple reward components for given inputs.
    
    Args:
        rewards: List of reward components to combine
        inputs: Inputs to compute rewards for
        **kwargs: Additional arguments for reward computation
        
    Returns:
        List of combined rewards
    """
    if not rewards:
        return [0.0] * len(inputs)
    
    # Compute rewards from all components
    all_rewards = []
    for reward_component in rewards:
        component_rewards = reward_component.compute_batch(inputs, **kwargs)
        all_rewards.append(component_rewards)
    
    # Sum rewards across components
    combined = []
    for i in range(len(inputs)):
        total_reward = sum(rewards_list[i] for rewards_list in all_rewards)
        combined.append(total_reward)
    
    return combined


if __name__ == "__main__":
    # Example usage and testing
    logging.basicConfig(level=logging.INFO)
    
    # Test mock reward
    config = RewardConfig(weight=2.0, normalize=False)
    mock_reward = MockReward(config, reward_range=(-1.0, 1.0))
    
    # Test single computation
    reward = mock_reward.compute("test input")
    print(f"Single reward: {reward}")
    
    # Test batch computation
    inputs = ["input1", "input2", "input3"]
    batch_rewards = mock_reward.compute_batch(inputs)
    print(f"Batch rewards: {batch_rewards}")
    
    # Test statistics
    stats = mock_reward.get_stats()
    print(f"Statistics: {stats}")
    
    # Test reward combination
    reward1 = MockReward(RewardConfig(weight=1.0), reward_range=(0.0, 0.5))
    reward2 = MockReward(RewardConfig(weight=2.0), reward_range=(0.0, 0.3))
    
    combined = combine_rewards([reward1, reward2], inputs)
    print(f"Combined rewards: {combined}")
    
    print("Base reward interface implementation complete!")