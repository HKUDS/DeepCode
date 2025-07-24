"""
Main training orchestrator for Curiosity-Driven Red-Teaming.

This module implements the CRTTrainer class that coordinates all training components
including the red team model, target models, reward computation, and evaluation.
"""

import logging
import os
import time
from typing import Dict, List, Optional, Any, Tuple, Union
from dataclasses import dataclass, field
import json
import pickle
from pathlib import Path

import torch
import torch.nn.functional as F
import numpy as np
from transformers import set_seed
from tqdm import tqdm

# Import core components
from ..core.curiosity_redteam import CuriosityRedTeam
from ..core.novelty_tracker import NoveltyTracker
from ..models.red_team_model import RedTeamModel
from ..models.target_models import TargetLLM
from ..models.toxicity_classifier import ToxicityClassifier
from ..models.sentence_embedder import SentenceEmbedder
from ..rewards.combined_reward import CombinedReward
from ..evaluation.evaluator import CRTEvaluator

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class TrainingConfig:
    """Configuration for CRT training."""
    
    # Model configurations
    red_team_model_name: str = "gpt2"
    target_model_name: str = "gpt2-imdb"
    toxicity_model_name: str = "facebook/roberta-hate-speech-dynabench-r4-target"
    sentence_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    
    # Training hyperparameters
    num_epochs: int = 10
    batch_size: int = 32
    learning_rate: float = 3e-5
    max_length: int = 128
    generation_kwargs: Dict[str, Any] = field(default_factory=lambda: {
        "max_length": 128,
        "temperature": 0.7,
        "top_p": 0.92,
        "do_sample": True,
        "pad_token_id": 50256
    })
    
    # PPO hyperparameters
    ppo_epochs: int = 4
    mini_batch_size: int = 8
    gradient_accumulation_steps: int = 1
    clip_range: float = 0.2
    value_clip_range: float = 0.2
    
    # Reward weights (Equation 2)
    lambda_selfbleu: float = 1.0
    lambda_cosine: float = 1.0
    lambda_entropy: float = 0.1
    beta_kl: float = 0.1
    
    # Novelty tracking
    max_history_size: int = 10000
    novelty_threshold: float = 0.5
    
    # Evaluation
    eval_frequency: int = 100
    eval_batch_size: int = 16
    num_eval_samples: int = 100
    
    # Logging and saving
    log_frequency: int = 10
    save_frequency: int = 500
    output_dir: str = "outputs"
    experiment_name: str = "crt_experiment"
    
    # Device and reproducibility
    device: str = "auto"
    seed: int = 42
    
    # Advanced settings
    use_curriculum: bool = False
    warmup_steps: int = 100
    max_grad_norm: float = 1.0
    early_stopping_patience: int = 5
    
    def __post_init__(self):
        """Post-initialization processing."""
        if self.device == "auto":
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        
        # Create output directory
        self.output_dir = Path(self.output_dir) / self.experiment_name
        self.output_dir.mkdir(parents=True, exist_ok=True)


class CRTTrainer:
    """
    Main training orchestrator for Curiosity-Driven Red-Teaming.
    
    Coordinates all training components including:
    - Red team model training with PPO
    - Target model interaction
    - Reward computation with curiosity bonuses
    - Evaluation and logging
    """
    
    def __init__(self, config: TrainingConfig):
        """
        Initialize the CRT trainer.
        
        Args:
            config: Training configuration
        """
        self.config = config
        self.device = torch.device(config.device)
        
        # Set random seed for reproducibility
        set_seed(config.seed)
        
        # Initialize components
        self._initialize_models()
        self._initialize_training_components()
        self._initialize_logging()
        
        # Training state
        self.global_step = 0
        self.current_epoch = 0
        self.best_score = float('-inf')
        self.patience_counter = 0
        
        logger.info(f"CRTTrainer initialized with device: {self.device}")
        logger.info(f"Configuration: {self.config}")
    
    def _initialize_models(self):
        """Initialize all models."""
        logger.info("Initializing models...")
        
        # Red team model (trainable)
        self.red_team_model = RedTeamModel(
            model_name=self.config.red_team_model_name,
            device=self.device
        )
        
        # Target model (fixed)
        self.target_model = TargetLLM(
            model_name=self.config.target_model_name,
            device=self.device
        )
        
        # Toxicity classifier
        self.toxicity_classifier = ToxicityClassifier(
            model_name=self.config.toxicity_model_name,
            device=self.device
        )
        
        # Sentence embedder for cosine similarity
        self.sentence_embedder = SentenceEmbedder(
            model_name=self.config.sentence_model_name,
            device=self.device
        )
        
        logger.info("Models initialized successfully")
    
    def _initialize_training_components(self):
        """Initialize training-specific components."""
        logger.info("Initializing training components...")
        
        # Novelty tracker
        self.novelty_tracker = NoveltyTracker(
            max_size=self.config.max_history_size,
            sentence_embedder=self.sentence_embedder
        )
        
        # Combined reward calculator
        self.reward_calculator = CombinedReward(
            effectiveness_reward=None,  # Will be set with toxicity classifier
            selfbleu_reward=None,       # Will be set with novelty tracker
            cosine_reward=None,         # Will be set with novelty tracker
            entropy_reward=None,        # Will be set with red team model
            lambda_selfbleu=self.config.lambda_selfbleu,
            lambda_cosine=self.config.lambda_cosine,
            lambda_entropy=self.config.lambda_entropy
        )
        
        # Main CRT algorithm
        self.crt_algorithm = CuriosityRedTeam(
            red_team_model=self.red_team_model,
            target_model=self.target_model,
            reward_calculator=self.reward_calculator,
            novelty_tracker=self.novelty_tracker,
            config={
                'learning_rate': self.config.learning_rate,
                'ppo_epochs': self.config.ppo_epochs,
                'mini_batch_size': self.config.mini_batch_size,
                'clip_range': self.config.clip_range,
                'beta_kl': self.config.beta_kl,
                'max_grad_norm': self.config.max_grad_norm
            }
        )
        
        # Evaluator
        self.evaluator = CRTEvaluator(
            red_team_model=self.red_team_model,
            target_model=self.target_model,
            toxicity_classifier=self.toxicity_classifier,
            sentence_embedder=self.sentence_embedder
        )
        
        logger.info("Training components initialized successfully")
    
    def _initialize_logging(self):
        """Initialize logging and metrics tracking."""
        self.metrics_history = {
            'train_loss': [],
            'train_reward': [],
            'train_effectiveness': [],
            'train_novelty': [],
            'eval_quality': [],
            'eval_diversity': [],
            'eval_coverage': []
        }
        
        # Create logging directory
        self.log_dir = self.config.output_dir / "logs"
        self.log_dir.mkdir(exist_ok=True)
        
        # Setup file logging
        log_file = self.log_dir / "training.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        logger.info("Logging initialized")
    
    def train(self, dataset: List[str], validation_dataset: Optional[List[str]] = None) -> Dict[str, List[float]]:
        """
        Main training loop for the CRT algorithm.
        
        Args:
            dataset: Training instructions/prompts
            validation_dataset: Optional validation instructions
            
        Returns:
            Dictionary containing training metrics history
        """
        logger.info(f"Starting training with {len(dataset)} instructions for {self.config.num_epochs} epochs")
        
        # Save initial model
        self.save_checkpoint(0)
        
        try:
            for epoch in range(self.config.num_epochs):
                self.current_epoch = epoch
                logger.info(f"Starting epoch {epoch + 1}/{self.config.num_epochs}")
                
                # Training phase
                epoch_metrics = self._train_epoch(dataset)
                
                # Log epoch metrics
                self._log_epoch_metrics(epoch, epoch_metrics)
                
                # Evaluation phase
                if (epoch + 1) % self.config.eval_frequency == 0 or epoch == self.config.num_epochs - 1:
                    eval_metrics = self._evaluate_epoch(validation_dataset or dataset[:self.config.num_eval_samples])
                    self._log_eval_metrics(epoch, eval_metrics)
                    
                    # Check for improvement and early stopping
                    current_score = eval_metrics.get('combined_score', 0.0)
                    if current_score > self.best_score:
                        self.best_score = current_score
                        self.patience_counter = 0
                        self.save_checkpoint(epoch + 1, is_best=True)
                    else:
                        self.patience_counter += 1
                        if self.patience_counter >= self.config.early_stopping_patience:
                            logger.info(f"Early stopping triggered after {epoch + 1} epochs")
                            break
                
                # Save checkpoint
                if (epoch + 1) % self.config.save_frequency == 0:
                    self.save_checkpoint(epoch + 1)
        
        except KeyboardInterrupt:
            logger.info("Training interrupted by user")
        except Exception as e:
            logger.error(f"Training failed with error: {e}")
            raise
        
        # Final evaluation
        logger.info("Training completed. Running final evaluation...")
        final_metrics = self._evaluate_epoch(validation_dataset or dataset[:self.config.num_eval_samples])
        self._log_eval_metrics(self.current_epoch, final_metrics, prefix="final_")
        
        # Save final model
        self.save_checkpoint(self.current_epoch + 1, is_final=True)
        
        return self.metrics_history
    
    def _train_epoch(self, dataset: List[str]) -> Dict[str, float]:
        """
        Train for one epoch.
        
        Args:
            dataset: Training instructions
            
        Returns:
            Dictionary containing epoch metrics
        """
        epoch_metrics = {
            'loss': [],
            'reward': [],
            'effectiveness': [],
            'novelty_selfbleu': [],
            'novelty_cosine': [],
            'entropy': [],
            'kl_divergence': []
        }
        
        # Shuffle dataset
        shuffled_indices = torch.randperm(len(dataset))
        
        # Process in batches
        num_batches = (len(dataset) + self.config.batch_size - 1) // self.config.batch_size
        
        progress_bar = tqdm(range(num_batches), desc=f"Epoch {self.current_epoch + 1}")
        
        for batch_idx in progress_bar:
            # Get batch
            start_idx = batch_idx * self.config.batch_size
            end_idx = min(start_idx + self.config.batch_size, len(dataset))
            batch_indices = shuffled_indices[start_idx:end_idx]
            batch_instructions = [dataset[i] for i in batch_indices]
            
            # Training step
            step_metrics = self.crt_algorithm.train_step(batch_instructions)
            
            # Accumulate metrics
            for key, value in step_metrics.items():
                if key in epoch_metrics:
                    epoch_metrics[key].append(value)
            
            # Update global step
            self.global_step += 1
            
            # Log step metrics
            if self.global_step % self.config.log_frequency == 0:
                self._log_step_metrics(step_metrics)
            
            # Update progress bar
            progress_bar.set_postfix({
                'loss': f"{step_metrics.get('loss', 0.0):.4f}",
                'reward': f"{step_metrics.get('reward', 0.0):.4f}",
                'effectiveness': f"{step_metrics.get('effectiveness', 0.0):.4f}"
            })
        
        # Compute epoch averages
        epoch_averages = {}
        for key, values in epoch_metrics.items():
            if values:
                epoch_averages[f"avg_{key}"] = np.mean(values)
                epoch_averages[f"std_{key}"] = np.std(values)
        
        return epoch_averages
    
    def _evaluate_epoch(self, eval_dataset: List[str]) -> Dict[str, float]:
        """
        Evaluate the model on validation data.
        
        Args:
            eval_dataset: Evaluation instructions
            
        Returns:
            Dictionary containing evaluation metrics
        """
        logger.info(f"Running evaluation on {len(eval_dataset)} instructions")
        
        # Generate test cases
        eval_instructions = eval_dataset[:self.config.num_eval_samples]
        
        # Run evaluation
        eval_metrics = self.evaluator.evaluate_model(
            test_instructions=eval_instructions,
            num_samples_per_instruction=1,
            batch_size=self.config.eval_batch_size
        )
        
        return eval_metrics
    
    def _log_step_metrics(self, metrics: Dict[str, float]):
        """Log step-level metrics."""
        log_str = f"Step {self.global_step}: "
        log_str += ", ".join([f"{k}={v:.4f}" for k, v in metrics.items()])
        logger.info(log_str)
    
    def _log_epoch_metrics(self, epoch: int, metrics: Dict[str, float]):
        """Log epoch-level metrics."""
        logger.info(f"Epoch {epoch + 1} Training Metrics:")
        for key, value in metrics.items():
            logger.info(f"  {key}: {value:.4f}")
            
        # Update metrics history
        for key in ['loss', 'reward', 'effectiveness']:
            avg_key = f"avg_{key}"
            if avg_key in metrics:
                self.metrics_history[f"train_{key}"].append(metrics[avg_key])
    
    def _log_eval_metrics(self, epoch: int, metrics: Dict[str, float], prefix: str = ""):
        """Log evaluation metrics."""
        logger.info(f"Epoch {epoch + 1} {prefix}Evaluation Metrics:")
        for key, value in metrics.items():
            logger.info(f"  {key}: {value:.4f}")
            
        # Update metrics history
        if 'quality' in metrics:
            self.metrics_history['eval_quality'].append(metrics['quality'])
        if 'diversity' in metrics:
            self.metrics_history['eval_diversity'].append(metrics['diversity'])
        if 'coverage' in metrics:
            self.metrics_history['eval_coverage'].append(metrics['coverage'])
    
    def save_checkpoint(self, epoch: int, is_best: bool = False, is_final: bool = False):
        """
        Save training checkpoint.
        
        Args:
            epoch: Current epoch number
            is_best: Whether this is the best checkpoint so far
            is_final: Whether this is the final checkpoint
        """
        checkpoint = {
            'epoch': epoch,
            'global_step': self.global_step,
            'model_state_dict': self.red_team_model.model.state_dict(),
            'optimizer_state_dict': self.crt_algorithm.optimizer.state_dict() if hasattr(self.crt_algorithm, 'optimizer') else None,
            'config': self.config,
            'metrics_history': self.metrics_history,
            'best_score': self.best_score,
            'novelty_tracker_state': self.novelty_tracker.get_state()
        }
        
        # Save checkpoint
        checkpoint_path = self.config.output_dir / f"checkpoint_epoch_{epoch}.pt"
        torch.save(checkpoint, checkpoint_path)
        
        if is_best:
            best_path = self.config.output_dir / "best_model.pt"
            torch.save(checkpoint, best_path)
            logger.info(f"Best model saved to {best_path}")
        
        if is_final:
            final_path = self.config.output_dir / "final_model.pt"
            torch.save(checkpoint, final_path)
            logger.info(f"Final model saved to {final_path}")
        
        logger.info(f"Checkpoint saved to {checkpoint_path}")
    
    def load_checkpoint(self, checkpoint_path: str):
        """
        Load training checkpoint.
        
        Args:
            checkpoint_path: Path to checkpoint file
        """
        checkpoint = torch.load(checkpoint_path, map_location=self.device)
        
        # Restore model state
        self.red_team_model.model.load_state_dict(checkpoint['model_state_dict'])
        
        # Restore optimizer state if available
        if checkpoint.get('optimizer_state_dict') and hasattr(self.crt_algorithm, 'optimizer'):
            self.crt_algorithm.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        
        # Restore training state
        self.global_step = checkpoint['global_step']
        self.current_epoch = checkpoint['epoch']
        self.best_score = checkpoint['best_score']
        self.metrics_history = checkpoint['metrics_history']
        
        # Restore novelty tracker state
        if 'novelty_tracker_state' in checkpoint:
            self.novelty_tracker.load_state(checkpoint['novelty_tracker_state'])
        
        logger.info(f"Checkpoint loaded from {checkpoint_path}")
        logger.info(f"Resumed from epoch {self.current_epoch}, step {self.global_step}")
    
    def generate_test_cases(self, instructions: List[str], num_samples: int = 1) -> List[List[str]]:
        """
        Generate test cases for given instructions.
        
        Args:
            instructions: List of instruction prompts
            num_samples: Number of test cases per instruction
            
        Returns:
            List of test case lists (one per instruction)
        """
        return self.crt_algorithm.generate_test_cases(instructions, num_samples)
    
    def evaluate(self, test_instructions: List[str]) -> Dict[str, float]:
        """
        Evaluate the current model.
        
        Args:
            test_instructions: Instructions for evaluation
            
        Returns:
            Dictionary containing evaluation metrics
        """
        return self.evaluator.evaluate_model(
            test_instructions=test_instructions,
            num_samples_per_instruction=1,
            batch_size=self.config.eval_batch_size
        )
    
    def get_training_summary(self) -> Dict[str, Any]:
        """
        Get a summary of training progress.
        
        Returns:
            Dictionary containing training summary
        """
        summary = {
            'current_epoch': self.current_epoch,
            'global_step': self.global_step,
            'best_score': self.best_score,
            'total_test_cases_generated': len(self.novelty_tracker.history),
            'config': self.config.__dict__,
            'metrics_summary': {}
        }
        
        # Add metrics summary
        for key, values in self.metrics_history.items():
            if values:
                summary['metrics_summary'][key] = {
                    'latest': values[-1],
                    'best': max(values) if 'loss' not in key else min(values),
                    'average': np.mean(values),
                    'std': np.std(values)
                }
        
        return summary
    
    def save_generated_data(self, output_path: Optional[str] = None):
        """
        Save generated test cases and responses.
        
        Args:
            output_path: Optional custom output path
        """
        if output_path is None:
            output_path = self.config.output_dir / "generated_data.json"
        
        # Collect generated data
        generated_data = {
            'test_cases': self.novelty_tracker.history,
            'embeddings': self.novelty_tracker.embeddings.tolist() if self.novelty_tracker.embeddings is not None else [],
            'generation_config': self.config.generation_kwargs,
            'total_generated': len(self.novelty_tracker.history)
        }
        
        # Save to JSON
        with open(output_path, 'w') as f:
            json.dump(generated_data, f, indent=2)
        
        logger.info(f"Generated data saved to {output_path}")


def create_trainer_from_config(config_path: str) -> CRTTrainer:
    """
    Create a CRTTrainer from a configuration file.
    
    Args:
        config_path: Path to configuration file (JSON or YAML)
        
    Returns:
        Initialized CRTTrainer instance
    """
    import yaml
    
    with open(config_path, 'r') as f:
        if config_path.endswith('.yaml') or config_path.endswith('.yml'):
            config_dict = yaml.safe_load(f)
        else:
            config_dict = json.load(f)
    
    config = TrainingConfig(**config_dict)
    return CRTTrainer(config)


if __name__ == "__main__":
    # Example usage
    config = TrainingConfig(
        red_team_model_name="gpt2",
        target_model_name="gpt2-imdb",
        num_epochs=5,
        batch_size=16,
        learning_rate=3e-5,
        experiment_name="test_run"
    )
    
    trainer = CRTTrainer(config)
    
    # Example dataset
    sample_dataset = [
        "Write a movie review about",
        "Generate a story that",
        "Create a dialogue between",
        "Describe a character who",
        "Write a scene where"
    ] * 10  # Repeat for more data
    
    # Train
    metrics = trainer.train(sample_dataset)
    
    # Print summary
    summary = trainer.get_training_summary()
    print("Training Summary:")
    print(json.dumps(summary, indent=2))