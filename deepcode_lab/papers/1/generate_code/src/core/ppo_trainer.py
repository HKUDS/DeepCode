"""
PPO Trainer with Curiosity Rewards for Red-Teaming

This module implements a PPO trainer that extends trlX PPOTrainer to incorporate
curiosity rewards for red-teaming. It handles the PPO update step with combined
rewards including effectiveness, novelty, and entropy components.

Classes:
    CuriosityPPOTrainer: PPO trainer with curiosity rewards
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import logging
from dataclasses import dataclass
from transformers import AutoTokenizer, AutoModelForCausalLM
import warnings

# Suppress warnings for cleaner output
warnings.filterwarnings("ignore", category=UserWarning)

logger = logging.getLogger(__name__)


@dataclass
class PPOConfig:
    """Configuration for PPO training"""
    learning_rate: float = 3e-5
    batch_size: int = 32
    mini_batch_size: int = 8
    ppo_epochs: int = 4
    gamma: float = 1.0
    gae_lambda: float = 0.95
    clip_range: float = 0.2
    clip_range_vf: float = None
    normalize_advantage: bool = True
    ent_coef: float = 0.01
    vf_coef: float = 0.5
    max_grad_norm: float = 0.5
    target_kl: float = 0.1
    kl_penalty: float = 0.1
    adaptive_kl: bool = True
    init_kl_coef: float = 0.2
    adap_kl_ctrl: float = 0.1


class CuriosityPPOTrainer:
    """
    PPO trainer that incorporates curiosity rewards for red-teaming.
    
    This trainer extends standard PPO to work with combined rewards that include
    effectiveness rewards (toxicity), novelty rewards (SelfBLEU, cosine similarity),
    and entropy bonuses.
    """
    
    def __init__(
        self,
        model: nn.Module,
        ref_model: nn.Module,
        tokenizer: AutoTokenizer,
        config: PPOConfig = None,
        device: str = "auto"
    ):
        """
        Initialize the PPO trainer.
        
        Args:
            model: The policy model to train
            ref_model: Reference model for KL penalty
            tokenizer: Tokenizer for the model
            config: PPO configuration
            device: Device to use for training
        """
        self.config = config or PPOConfig()
        
        # Set device
        if device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)
        
        # Models
        self.model = model.to(self.device)
        self.ref_model = ref_model.to(self.device)
        self.tokenizer = tokenizer
        
        # Optimizer
        self.optimizer = torch.optim.AdamW(
            self.model.parameters(),
            lr=self.config.learning_rate
        )
        
        # KL controller for adaptive KL penalty
        self.kl_ctl = AdaptiveKLController(
            init_kl_coef=self.config.init_kl_coef,
            target=self.config.target_kl,
            horizon=10000
        )
        
        # Training statistics
        self.stats = {
            "policy_loss": [],
            "value_loss": [],
            "entropy": [],
            "kl_divergence": [],
            "total_loss": [],
            "rewards": [],
            "advantages": []
        }
        
        logger.info(f"Initialized CuriosityPPOTrainer on device: {self.device}")
    
    def compute_rewards(
        self,
        query_tensors: List[torch.Tensor],
        response_tensors: List[torch.Tensor],
        rewards: List[float]
    ) -> List[torch.Tensor]:
        """
        Compute rewards for PPO training.
        
        Args:
            query_tensors: Input query tensors
            response_tensors: Generated response tensors
            rewards: Combined rewards (effectiveness + novelty + entropy)
        
        Returns:
            List of reward tensors for each sequence
        """
        reward_tensors = []
        
        for i, (query, response, reward) in enumerate(zip(query_tensors, response_tensors, rewards)):
            # Create reward tensor with same length as response
            reward_tensor = torch.zeros_like(response, dtype=torch.float32, device=self.device)
            
            # Place reward at the end of the sequence
            if len(response) > 0:
                reward_tensor[-1] = reward
            
            reward_tensors.append(reward_tensor)
        
        return reward_tensors
    
    def _compute_rewards(
        self,
        scores: List[float],
        logprobs: torch.Tensor,
        ref_logprobs: torch.Tensor,
        masks: torch.Tensor
    ) -> torch.Tensor:
        """
        Compute final rewards including KL penalty.
        
        Args:
            scores: Raw reward scores
            logprobs: Log probabilities from current policy
            ref_logprobs: Log probabilities from reference policy
            masks: Attention masks
        
        Returns:
            Final rewards tensor
        """
        # Convert scores to tensor
        scores_tensor = torch.tensor(scores, dtype=torch.float32, device=self.device)
        
        # Compute KL divergence
        kl_div = logprobs - ref_logprobs
        kl_div = (kl_div * masks).sum(dim=-1)
        
        # Apply KL penalty
        kl_penalty = self.kl_ctl.value * kl_div
        
        # Final rewards
        rewards = scores_tensor - kl_penalty
        
        return rewards, kl_div
    
    def step(
        self,
        queries: List[str],
        responses: List[str],
        scores: List[float]
    ) -> Dict[str, float]:
        """
        Perform one PPO training step.
        
        Args:
            queries: Input queries/instructions
            responses: Generated responses/test cases
            scores: Combined reward scores
        
        Returns:
            Training statistics
        """
        # Tokenize inputs
        query_tensors = []
        response_tensors = []
        
        for query, response in zip(queries, responses):
            # Tokenize query
            query_tokens = self.tokenizer.encode(query, return_tensors="pt").squeeze(0)
            query_tensors.append(query_tokens.to(self.device))
            
            # Tokenize response
            response_tokens = self.tokenizer.encode(response, return_tensors="pt").squeeze(0)
            response_tensors.append(response_tokens.to(self.device))
        
        # Pad sequences for batch processing
        max_query_len = max(len(q) for q in query_tensors)
        max_response_len = max(len(r) for r in response_tensors)
        
        # Create padded batches
        batch_size = len(queries)
        query_batch = torch.full(
            (batch_size, max_query_len),
            self.tokenizer.pad_token_id,
            dtype=torch.long,
            device=self.device
        )
        response_batch = torch.full(
            (batch_size, max_response_len),
            self.tokenizer.pad_token_id,
            dtype=torch.long,
            device=self.device
        )
        query_masks = torch.zeros_like(query_batch, dtype=torch.bool)
        response_masks = torch.zeros_like(response_batch, dtype=torch.bool)
        
        for i, (query_tensor, response_tensor) in enumerate(zip(query_tensors, response_tensors)):
            query_len = len(query_tensor)
            response_len = len(response_tensor)
            
            query_batch[i, :query_len] = query_tensor
            response_batch[i, :response_len] = response_tensor
            query_masks[i, :query_len] = True
            response_masks[i, :response_len] = True
        
        # Get model outputs
        with torch.no_grad():
            # Current policy logprobs
            full_input = torch.cat([query_batch, response_batch], dim=1)
            full_masks = torch.cat([query_masks, response_masks], dim=1)
            
            outputs = self.model(full_input, attention_mask=full_masks)
            logits = outputs.logits
            
            # Extract response logits
            response_logits = logits[:, max_query_len:max_query_len + max_response_len]
            response_logprobs = F.log_softmax(response_logits, dim=-1)
            
            # Get logprobs for actual tokens
            current_logprobs = torch.gather(
                response_logprobs, 2, response_batch.unsqueeze(-1)
            ).squeeze(-1)
            current_logprobs = current_logprobs * response_masks.float()
            
            # Reference policy logprobs
            ref_outputs = self.ref_model(full_input, attention_mask=full_masks)
            ref_logits = ref_outputs.logits
            ref_response_logits = ref_logits[:, max_query_len:max_query_len + max_response_len]
            ref_response_logprobs = F.log_softmax(ref_response_logits, dim=-1)
            
            ref_logprobs = torch.gather(
                ref_response_logprobs, 2, response_batch.unsqueeze(-1)
            ).squeeze(-1)
            ref_logprobs = ref_logprobs * response_masks.float()
        
        # Compute rewards with KL penalty
        rewards, kl_div = self._compute_rewards(
            scores, current_logprobs, ref_logprobs, response_masks.float()
        )
        
        # Compute advantages (simplified - using rewards directly)
        advantages = rewards - rewards.mean()
        if self.config.normalize_advantage:
            advantages = advantages / (advantages.std() + 1e-8)
        
        # PPO training loop
        total_policy_loss = 0
        total_value_loss = 0
        total_entropy = 0
        
        for epoch in range(self.config.ppo_epochs):
            # Forward pass
            outputs = self.model(full_input, attention_mask=full_masks)
            logits = outputs.logits
            
            # Extract response logits
            response_logits = logits[:, max_query_len:max_query_len + max_response_len]
            response_logprobs = F.log_softmax(response_logits, dim=-1)
            
            # Get logprobs for actual tokens
            new_logprobs = torch.gather(
                response_logprobs, 2, response_batch.unsqueeze(-1)
            ).squeeze(-1)
            new_logprobs = new_logprobs * response_masks.float()
            
            # Compute ratio
            ratio = torch.exp(new_logprobs.sum(dim=1) - current_logprobs.sum(dim=1))
            
            # Clipped surrogate loss
            surr1 = ratio * advantages
            surr2 = torch.clamp(ratio, 1 - self.config.clip_range, 1 + self.config.clip_range) * advantages
            policy_loss = -torch.min(surr1, surr2).mean()
            
            # Entropy bonus
            entropy = -(response_logprobs * torch.exp(response_logprobs)).sum(dim=-1)
            entropy = (entropy * response_masks.float()).mean()
            
            # Total loss
            loss = policy_loss - self.config.ent_coef * entropy
            
            # Backward pass
            self.optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.config.max_grad_norm)
            self.optimizer.step()
            
            total_policy_loss += policy_loss.item()
            total_value_loss += 0  # No value function in this implementation
            total_entropy += entropy.item()
        
        # Update KL controller
        mean_kl = kl_div.mean().item()
        self.kl_ctl.update(mean_kl, batch_size)
        
        # Update statistics
        stats = {
            "policy_loss": total_policy_loss / self.config.ppo_epochs,
            "value_loss": total_value_loss / self.config.ppo_epochs,
            "entropy": total_entropy / self.config.ppo_epochs,
            "kl_divergence": mean_kl,
            "total_loss": (total_policy_loss - self.config.ent_coef * total_entropy) / self.config.ppo_epochs,
            "mean_reward": rewards.mean().item(),
            "mean_advantage": advantages.mean().item(),
            "kl_coef": self.kl_ctl.value
        }
        
        # Store statistics
        for key, value in stats.items():
            if key in self.stats:
                self.stats[key].append(value)
        
        return stats
    
    def save_model(self, save_path: str):
        """Save the trained model."""
        self.model.save_pretrained(save_path)
        self.tokenizer.save_pretrained(save_path)
        logger.info(f"Model saved to {save_path}")
    
    def load_model(self, load_path: str):
        """Load a trained model."""
        self.model = AutoModelForCausalLM.from_pretrained(load_path).to(self.device)
        logger.info(f"Model loaded from {load_path}")
    
    def get_stats(self) -> Dict[str, List[float]]:
        """Get training statistics."""
        return self.stats.copy()


class AdaptiveKLController:
    """
    Adaptive KL divergence controller for PPO training.
    
    This controller adjusts the KL penalty coefficient based on the observed
    KL divergence to maintain it close to a target value.
    """
    
    def __init__(self, init_kl_coef: float, target: float, horizon: int):
        """
        Initialize the KL controller.
        
        Args:
            init_kl_coef: Initial KL coefficient
            target: Target KL divergence
            horizon: Number of steps for adaptation
        """
        self.value = init_kl_coef
        self.target = target
        self.horizon = horizon
        self.step_count = 0
    
    def update(self, current_kl: float, n_steps: int):
        """
        Update the KL coefficient based on current KL divergence.
        
        Args:
            current_kl: Current KL divergence
            n_steps: Number of steps taken
        """
        self.step_count += n_steps
        
        # Proportional control
        proportional_error = current_kl - self.target
        mult = 1 + proportional_error / self.target
        
        # Update coefficient
        self.value *= mult
        
        # Clamp to reasonable bounds
        self.value = max(0.001, min(self.value, 10.0))


def create_ppo_trainer(
    model_name: str = "gpt2",
    device: str = "auto",
    config: PPOConfig = None
) -> CuriosityPPOTrainer:
    """
    Create a PPO trainer with default models.
    
    Args:
        model_name: Name of the model to use
        device: Device to use for training
        config: PPO configuration
    
    Returns:
        Configured PPO trainer
    """
    # Load tokenizer and models
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    
    model = AutoModelForCausalLM.from_pretrained(model_name)
    ref_model = AutoModelForCausalLM.from_pretrained(model_name)
    
    # Create trainer
    trainer = CuriosityPPOTrainer(
        model=model,
        ref_model=ref_model,
        tokenizer=tokenizer,
        config=config,
        device=device
    )
    
    return trainer


# Example usage and testing
if __name__ == "__main__":
    # Test the PPO trainer
    logging.basicConfig(level=logging.INFO)
    
    # Create trainer
    config = PPOConfig(
        learning_rate=1e-5,
        batch_size=4,
        ppo_epochs=2
    )
    
    trainer = create_ppo_trainer(
        model_name="gpt2",
        config=config,
        device="cpu"  # Use CPU for testing
    )
    
    # Test training step
    queries = [
        "Generate a toxic comment about",
        "Write something offensive about",
        "Create a harmful statement regarding",
        "Produce a negative comment on"
    ]
    
    responses = [
        "people who disagree with me",
        "different political views",
        "social media platforms",
        "online discussions"
    ]
    
    scores = [0.8, 0.6, 0.7, 0.9]  # Mock reward scores
    
    print("Testing PPO training step...")
    stats = trainer.step(queries, responses, scores)
    
    print("Training statistics:")
    for key, value in stats.items():
        print(f"  {key}: {value:.4f}")
    
    print("\nPPO trainer test completed successfully!")