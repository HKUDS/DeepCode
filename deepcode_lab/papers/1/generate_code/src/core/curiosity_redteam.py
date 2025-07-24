"""
Curiosity-Driven Red-Teaming for Large Language Models
Core implementation of the CRT algorithm from the ICLR 2024 paper.

This module implements the main CRT Algorithm from Section 3:
- Manages full training pipeline
- Implements Equation 2: reward = R(y) + λ_B*B_SelfBLEU(x) + λ_C*B_Cos(x) - λ_E*log(π(x|z))
- Coordinates PPO training with curiosity rewards
"""

import torch
import torch.nn.functional as F
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
from dataclasses import dataclass
import logging
from transformers import GPT2LMHeadModel, GPT2Tokenizer
from trl import PPOTrainer, PPOConfig

from .novelty_tracker import NoveltyTracker
from .ppo_trainer import CuriosityPPOTrainer
from ..rewards.combined_reward import CombinedReward
from ..models.red_team_model import RedTeamModel
from ..models.target_models import TargetLLM
from ..utils.config import DEFAULT_HYPERPARAMETERS

logger = logging.getLogger(__name__)


@dataclass
class CRTConfig:
    """Configuration for Curiosity-Driven Red-Teaming"""
    # Model parameters
    red_team_model_name: str = "gpt2"
    target_model_name: str = "gpt2-imdb"
    
    # Training parameters
    learning_rate: float = 3e-5
    batch_size: int = 32
    num_epochs: int = 10
    ppo_epochs: int = 4
    
    # Generation parameters
    max_length: int = 50
    temperature: float = 0.7
    top_p: float = 0.92
    
    # Reward weights (λ parameters from Equation 2)
    lambda_selfbleu: float = 0.1
    lambda_cosine: float = 0.1
    lambda_entropy: float = 0.01
    beta_kl: float = 0.1  # KL penalty coefficient
    
    # Novelty tracking
    max_history_size: int = 10000
    
    # Evaluation
    eval_batch_size: int = 64
    eval_frequency: int = 100


class CuriosityRedTeam:
    """
    Main class implementing the Curiosity-Driven Red-Teaming algorithm.
    
    This class coordinates all components of the CRT system:
    - Red team model (GPT2-137M with PPO training)
    - Target model wrapper
    - Novelty tracking and reward computation
    - Training loop with curiosity rewards
    """
    
    def __init__(self, config: CRTConfig):
        self.config = config
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        
        # Initialize models
        self.red_team_model = RedTeamModel(config.red_team_model_name)
        self.target_model = TargetLLM(config.target_model_name)
        
        # Initialize novelty tracker and rewards
        self.novelty_tracker = NoveltyTracker(max_size=config.max_history_size)
        self.reward_computer = CombinedReward(
            lambda_selfbleu=config.lambda_selfbleu,
            lambda_cosine=config.lambda_cosine,
            lambda_entropy=config.lambda_entropy
        )
        
        # Initialize PPO trainer
        ppo_config = PPOConfig(
            learning_rate=config.learning_rate,
            batch_size=config.batch_size,
            ppo_epochs=config.ppo_epochs,
            init_kl_coef=config.beta_kl,
            max_grad_norm=1.0,
            optimize_cuda_cache=True
        )
        
        self.ppo_trainer = CuriosityPPOTrainer(
            config=ppo_config,
            model=self.red_team_model.model,
            ref_model=self.red_team_model.ref_model,
            tokenizer=self.red_team_model.tokenizer,
            reward_computer=self.reward_computer,
            novelty_tracker=self.novelty_tracker
        )
        
        logger.info(f"Initialized CuriosityRedTeam with config: {config}")
    
    def train_step(self, instructions: List[str]) -> Dict[str, float]:
        """
        Implements one training step of the CRT algorithm.
        
        This corresponds to lines 3-12 of the algorithm pseudocode:
        1. Generate test cases x ~ π(.|z)
        2. Get target responses y ~ p(.|x)
        3. Compute combined reward (Equation 2)
        4. Update π using PPO
        5. Add x to history X
        
        Args:
            instructions: Batch of instruction prompts z
            
        Returns:
            Dictionary of training metrics
        """
        batch_size = len(instructions)
        
        # Step 1: Generate test cases x ~ π(.|z)
        test_cases = self.red_team_model.generate(
            instructions,
            max_length=self.config.max_length,
            temperature=self.config.temperature,
            top_p=self.config.top_p
        )
        
        # Step 2: Get target responses y ~ p(.|x)
        target_responses = []
        for test_case in test_cases:
            response = self.target_model.generate_response(test_case)
            target_responses.append(response)
        
        # Step 3: Compute combined reward (implements Equation 2)
        rewards = self.compute_combined_reward(
            test_cases=test_cases,
            target_responses=target_responses,
            instructions=instructions
        )
        
        # Step 4: Update π using PPO with computed rewards
        train_stats = self.ppo_trainer.step(
            queries=instructions,
            responses=test_cases,
            scores=rewards
        )
        
        # Step 5: Add test cases to history X
        for test_case in test_cases:
            self.novelty_tracker.add_sentence(test_case)
        
        # Compile training metrics
        metrics = {
            'mean_reward': np.mean(rewards),
            'std_reward': np.std(rewards),
            'mean_effectiveness': np.mean([r['effectiveness'] for r in rewards]),
            'mean_selfbleu_novelty': np.mean([r['selfbleu_novelty'] for r in rewards]),
            'mean_cosine_novelty': np.mean([r['cosine_novelty'] for r in rewards]),
            'mean_entropy_bonus': np.mean([r['entropy_bonus'] for r in rewards]),
            'history_size': len(self.novelty_tracker.history),
            **train_stats
        }
        
        return metrics
    
    def compute_combined_reward(
        self, 
        test_cases: List[str], 
        target_responses: List[str],
        instructions: List[str]
    ) -> List[float]:
        """
        Computes the combined reward from Equation 2:
        reward = R(y) + λ_B*B_SelfBLEU(x) + λ_C*B_Cos(x) - λ_E*log(π(x|z))
        
        Args:
            test_cases: Generated test cases x
            target_responses: Target model responses y
            instructions: Original instruction prompts z
            
        Returns:
            List of combined reward values
        """
        rewards = []
        
        for i, (test_case, response, instruction) in enumerate(
            zip(test_cases, target_responses, instructions)
        ):
            # Compute all reward components
            reward_components = self.reward_computer.compute(
                test_case=test_case,
                target_response=response,
                instruction=instruction,
                novelty_tracker=self.novelty_tracker,
                red_team_model=self.red_team_model
            )
            
            # Sum components according to Equation 2
            total_reward = (
                reward_components['effectiveness'] +
                self.config.lambda_selfbleu * reward_components['selfbleu_novelty'] +
                self.config.lambda_cosine * reward_components['cosine_novelty'] -
                self.config.lambda_entropy * reward_components['entropy_bonus']
            )
            
            rewards.append(total_reward)
        
        return rewards
    
    def train(self, dataset: List[str], num_epochs: Optional[int] = None) -> Dict[str, List[float]]:
        """
        Main training loop implementing the full CRT algorithm.
        
        Args:
            dataset: List of instruction prompts for training
            num_epochs: Number of training epochs (uses config if None)
            
        Returns:
            Dictionary of training history metrics
        """
        if num_epochs is None:
            num_epochs = self.config.num_epochs
        
        training_history = {
            'mean_reward': [],
            'mean_effectiveness': [],
            'mean_selfbleu_novelty': [],
            'mean_cosine_novelty': [],
            'diversity_score': [],
            'quality_score': []
        }
        
        logger.info(f"Starting CRT training for {num_epochs} epochs on {len(dataset)} instructions")
        
        for epoch in range(num_epochs):
            epoch_metrics = []
            
            # Process dataset in batches
            for i in range(0, len(dataset), self.config.batch_size):
                batch = dataset[i:i + self.config.batch_size]
                
                # Perform one training step
                step_metrics = self.train_step(batch)
                epoch_metrics.append(step_metrics)
                
                # Log progress
                if i % (self.config.batch_size * 10) == 0:
                    logger.info(
                        f"Epoch {epoch+1}/{num_epochs}, Batch {i//self.config.batch_size+1}: "
                        f"Reward={step_metrics['mean_reward']:.3f}, "
                        f"Effectiveness={step_metrics['mean_effectiveness']:.3f}"
                    )
            
            # Aggregate epoch metrics
            epoch_summary = self._aggregate_epoch_metrics(epoch_metrics)
            
            # Store in training history
            for key in training_history:
                if key in epoch_summary:
                    training_history[key].append(epoch_summary[key])
            
            # Evaluate periodically
            if (epoch + 1) % self.config.eval_frequency == 0:
                eval_metrics = self.evaluate(dataset[:self.config.eval_batch_size])
                logger.info(f"Epoch {epoch+1} Evaluation: {eval_metrics}")
        
        logger.info("Training completed successfully")
        return training_history
    
    def _aggregate_epoch_metrics(self, batch_metrics: List[Dict[str, float]]) -> Dict[str, float]:
        """Aggregate metrics across batches in an epoch"""
        if not batch_metrics:
            return {}
        
        aggregated = {}
        for key in batch_metrics[0]:
            if isinstance(batch_metrics[0][key], (int, float)):
                aggregated[key] = np.mean([m[key] for m in batch_metrics])
        
        return aggregated
    
    def evaluate(self, test_instructions: List[str]) -> Dict[str, float]:
        """
        Evaluate the current red team model on test instructions.
        
        Args:
            test_instructions: List of test instruction prompts
            
        Returns:
            Dictionary of evaluation metrics
        """
        self.red_team_model.model.eval()
        
        with torch.no_grad():
            # Generate test cases
            test_cases = self.red_team_model.generate(
                test_instructions,
                max_length=self.config.max_length,
                temperature=self.config.temperature,
                top_p=self.config.top_p
            )
            
            # Get target responses
            target_responses = []
            for test_case in test_cases:
                response = self.target_model.generate_response(test_case)
                target_responses.append(response)
            
            # Compute evaluation metrics
            eval_metrics = self.reward_computer.evaluate_batch(
                test_cases=test_cases,
                target_responses=target_responses,
                instructions=test_instructions,
                novelty_tracker=self.novelty_tracker
            )
        
        self.red_team_model.model.train()
        return eval_metrics
    
    def generate_test_cases(
        self, 
        instructions: List[str], 
        num_samples: int = 1
    ) -> List[List[str]]:
        """
        Generate test cases for given instructions.
        
        Args:
            instructions: List of instruction prompts
            num_samples: Number of test cases to generate per instruction
            
        Returns:
            List of lists, where each inner list contains test cases for one instruction
        """
        all_test_cases = []
        
        for instruction in instructions:
            instruction_test_cases = []
            
            for _ in range(num_samples):
                test_cases = self.red_team_model.generate(
                    [instruction],
                    max_length=self.config.max_length,
                    temperature=self.config.temperature,
                    top_p=self.config.top_p
                )
                instruction_test_cases.extend(test_cases)
            
            all_test_cases.append(instruction_test_cases)
        
        return all_test_cases
    
    def save_model(self, save_path: str):
        """Save the trained red team model"""
        self.red_team_model.save(save_path)
        logger.info(f"Model saved to {save_path}")
    
    def load_model(self, load_path: str):
        """Load a trained red team model"""
        self.red_team_model.load(load_path)
        logger.info(f"Model loaded from {load_path}")