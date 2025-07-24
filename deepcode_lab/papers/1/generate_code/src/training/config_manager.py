"""
Configuration Manager for Curiosity-Driven Red-Teaming

This module provides configuration management functionality for the CRT system,
including loading YAML configurations, validating parameters, and managing
hyperparameters for different experimental setups.

Classes:
    ConfigManager: Main configuration management class
    
Functions:
    load_config: Loads YAML configuration files
    validate_config: Validates configuration parameters
    get_default_config: Returns default configuration
"""

import os
import yaml
import json
import logging
from typing import Dict, Any, Optional, Union, List
from pathlib import Path
from dataclasses import dataclass, asdict, field
from copy import deepcopy

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """Configuration for model parameters"""
    # Red team model configuration
    red_team_model_name: str = "gpt2"
    red_team_model_size: str = "137M"
    red_team_max_length: int = 50
    red_team_temperature: float = 0.7
    red_team_top_p: float = 0.92
    red_team_do_sample: bool = True
    
    # Target model configuration
    target_model_name: str = "gpt2-imdb"
    target_model_path: Optional[str] = None
    target_max_length: int = 100
    target_temperature: float = 1.0
    target_top_p: float = 1.0
    
    # Toxicity classifier configuration
    toxicity_model_name: str = "facebook/roberta-hate-speech-dynabench-r4-target"
    toxicity_threshold: float = 0.5
    
    # Sentence embedder configuration
    embedder_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"


@dataclass
class TrainingConfig:
    """Configuration for training parameters"""
    # Training hyperparameters
    num_epochs: int = 10
    batch_size: int = 32
    learning_rate: float = 3e-5
    warmup_steps: int = 100
    max_grad_norm: float = 1.0
    
    # PPO specific parameters
    ppo_epochs: int = 4
    ppo_batch_size: int = 8
    ppo_mini_batch_size: int = 4
    ppo_clip_range: float = 0.2
    ppo_value_loss_coef: float = 0.5
    ppo_entropy_coef: float = 0.01
    
    # KL penalty
    beta: float = 0.1
    kl_target: float = 6.0
    kl_horizon: int = 10000
    
    # Evaluation
    eval_frequency: int = 1
    eval_batch_size: int = 16
    save_frequency: int = 1
    
    # Data parameters
    max_samples: Optional[int] = None
    validation_ratio: float = 0.1
    
    # Memory management
    max_history_size: int = 10000
    cleanup_frequency: int = 1000


@dataclass
class RewardConfig:
    """Configuration for reward computation"""
    # Reward weights
    lambda_selfbleu: float = 1.0
    lambda_cosine: float = 1.0
    lambda_entropy: float = 0.1
    
    # SelfBLEU parameters
    selfbleu_ngrams: List[int] = field(default_factory=lambda: [2, 3, 4, 5])
    selfbleu_smoothing: bool = True
    
    # Cosine similarity parameters
    cosine_similarity_threshold: float = 0.8
    
    # Effectiveness reward parameters
    effectiveness_weight: float = 1.0
    effectiveness_threshold: float = 0.5
    
    # Entropy parameters
    entropy_regularization: bool = True


@dataclass
class ExperimentConfig:
    """Configuration for experiment setup"""
    # Experiment metadata
    experiment_name: str = "curiosity_redteam"
    experiment_description: str = "Curiosity-Driven Red-Teaming Experiment"
    
    # Task configuration
    task_type: str = "text_continuation"  # or "instruction_following"
    dataset_name: str = "imdb"  # or "alpaca", "dolly"
    
    # Output paths
    output_dir: str = "outputs"
    checkpoint_dir: str = "checkpoints"
    log_dir: str = "logs"
    data_dir: str = "data"
    
    # Logging
    log_level: str = "INFO"
    log_to_file: bool = True
    log_to_console: bool = True
    
    # Reproducibility
    seed: int = 42
    deterministic: bool = True
    
    # Device configuration
    device: str = "auto"  # "auto", "cpu", "cuda"
    mixed_precision: bool = False


@dataclass
class CRTConfig:
    """Complete configuration for Curiosity-Driven Red-Teaming"""
    model: ModelConfig = field(default_factory=ModelConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)
    reward: RewardConfig = field(default_factory=RewardConfig)
    experiment: ExperimentConfig = field(default_factory=ExperimentConfig)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary"""
        return asdict(self)
    
    def save(self, path: Union[str, Path]) -> None:
        """Save configuration to YAML file"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, 'w') as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, indent=2)
        
        logger.info(f"Configuration saved to {path}")
    
    def update(self, updates: Dict[str, Any]) -> None:
        """Update configuration with new values"""
        def update_nested(config_dict: Dict[str, Any], updates_dict: Dict[str, Any]):
            for key, value in updates_dict.items():
                if key in config_dict:
                    if isinstance(value, dict) and isinstance(config_dict[key], dict):
                        update_nested(config_dict[key], value)
                    else:
                        config_dict[key] = value
                else:
                    logger.warning(f"Unknown configuration key: {key}")
        
        config_dict = self.to_dict()
        update_nested(config_dict, updates)
        
        # Reconstruct the configuration
        self.model = ModelConfig(**config_dict.get('model', {}))
        self.training = TrainingConfig(**config_dict.get('training', {}))
        self.reward = RewardConfig(**config_dict.get('reward', {}))
        self.experiment = ExperimentConfig(**config_dict.get('experiment', {}))


class ConfigManager:
    """
    Configuration manager for Curiosity-Driven Red-Teaming
    
    Handles loading, validation, and management of configuration files
    for different experimental setups.
    """
    
    def __init__(self, config_dir: Optional[str] = None):
        """
        Initialize configuration manager
        
        Args:
            config_dir: Directory containing configuration files
        """
        self.config_dir = Path(config_dir) if config_dir else Path("configs")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        # Default configurations for different tasks
        self.default_configs = {
            "text_continuation": self._get_text_continuation_config(),
            "instruction_following": self._get_instruction_following_config(),
            "ablation": self._get_ablation_config(),
        }
        
        logger.info(f"ConfigManager initialized with config directory: {self.config_dir}")
    
    def load_config(self, config_path: Union[str, Path, None] = None, 
                   config_name: Optional[str] = None) -> CRTConfig:
        """
        Load configuration from YAML file
        
        Args:
            config_path: Path to configuration file
            config_name: Name of predefined configuration
            
        Returns:
            CRTConfig: Loaded configuration
        """
        if config_path is not None:
            # Load from specific file
            config_path = Path(config_path)
            if not config_path.exists():
                raise FileNotFoundError(f"Configuration file not found: {config_path}")
            
            with open(config_path, 'r') as f:
                config_dict = yaml.safe_load(f)
            
            logger.info(f"Configuration loaded from {config_path}")
            
        elif config_name is not None:
            # Load predefined configuration
            if config_name not in self.default_configs:
                raise ValueError(f"Unknown configuration name: {config_name}. "
                               f"Available: {list(self.default_configs.keys())}")
            
            config_dict = self.default_configs[config_name].to_dict()
            logger.info(f"Loaded predefined configuration: {config_name}")
            
        else:
            # Load default configuration
            config_dict = CRTConfig().to_dict()
            logger.info("Loaded default configuration")
        
        # Create configuration object
        config = CRTConfig(
            model=ModelConfig(**config_dict.get('model', {})),
            training=TrainingConfig(**config_dict.get('training', {})),
            reward=RewardConfig(**config_dict.get('reward', {})),
            experiment=ExperimentConfig(**config_dict.get('experiment', {}))
        )
        
        # Validate configuration
        self.validate_config(config)
        
        return config
    
    def validate_config(self, config: CRTConfig) -> bool:
        """
        Validate configuration parameters
        
        Args:
            config: Configuration to validate
            
        Returns:
            bool: True if configuration is valid
            
        Raises:
            ValueError: If configuration is invalid
        """
        errors = []
        
        # Validate model configuration
        if config.model.red_team_temperature <= 0 or config.model.red_team_temperature > 2:
            errors.append("red_team_temperature must be between 0 and 2")
        
        if config.model.red_team_top_p <= 0 or config.model.red_team_top_p > 1:
            errors.append("red_team_top_p must be between 0 and 1")
        
        if config.model.toxicity_threshold < 0 or config.model.toxicity_threshold > 1:
            errors.append("toxicity_threshold must be between 0 and 1")
        
        # Validate training configuration
        if config.training.num_epochs <= 0:
            errors.append("num_epochs must be positive")
        
        if config.training.batch_size <= 0:
            errors.append("batch_size must be positive")
        
        if config.training.learning_rate <= 0:
            errors.append("learning_rate must be positive")
        
        if config.training.ppo_clip_range <= 0 or config.training.ppo_clip_range > 1:
            errors.append("ppo_clip_range must be between 0 and 1")
        
        if config.training.validation_ratio < 0 or config.training.validation_ratio >= 1:
            errors.append("validation_ratio must be between 0 and 1")
        
        # Validate reward configuration
        if any(weight < 0 for weight in [config.reward.lambda_selfbleu, 
                                        config.reward.lambda_cosine, 
                                        config.reward.lambda_entropy]):
            errors.append("All reward weights must be non-negative")
        
        if not config.reward.selfbleu_ngrams or any(n <= 1 for n in config.reward.selfbleu_ngrams):
            errors.append("selfbleu_ngrams must contain positive integers > 1")
        
        # Validate experiment configuration
        valid_task_types = ["text_continuation", "instruction_following"]
        if config.experiment.task_type not in valid_task_types:
            errors.append(f"task_type must be one of {valid_task_types}")
        
        valid_datasets = ["imdb", "alpaca", "dolly"]
        if config.experiment.dataset_name not in valid_datasets:
            errors.append(f"dataset_name must be one of {valid_datasets}")
        
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if config.experiment.log_level not in valid_log_levels:
            errors.append(f"log_level must be one of {valid_log_levels}")
        
        if errors:
            error_msg = "Configuration validation failed:\n" + "\n".join(f"- {error}" for error in errors)
            raise ValueError(error_msg)
        
        logger.info("Configuration validation passed")
        return True
    
    def save_config(self, config: CRTConfig, name: str) -> Path:
        """
        Save configuration to file
        
        Args:
            config: Configuration to save
            name: Name for the configuration file
            
        Returns:
            Path: Path to saved configuration file
        """
        config_path = self.config_dir / f"{name}.yaml"
        config.save(config_path)
        return config_path
    
    def list_configs(self) -> List[str]:
        """
        List available configuration files
        
        Returns:
            List[str]: List of configuration file names
        """
        config_files = list(self.config_dir.glob("*.yaml"))
        return [f.stem for f in config_files]
    
    def get_config_for_task(self, task_type: str, dataset_name: str) -> CRTConfig:
        """
        Get configuration optimized for specific task and dataset
        
        Args:
            task_type: Type of task ("text_continuation" or "instruction_following")
            dataset_name: Name of dataset ("imdb", "alpaca", "dolly")
            
        Returns:
            CRTConfig: Optimized configuration
        """
        if task_type == "text_continuation":
            config = self._get_text_continuation_config()
        elif task_type == "instruction_following":
            config = self._get_instruction_following_config()
        else:
            raise ValueError(f"Unknown task type: {task_type}")
        
        # Update dataset-specific parameters
        config.experiment.task_type = task_type
        config.experiment.dataset_name = dataset_name
        
        if dataset_name == "imdb":
            config.model.target_model_name = "gpt2-imdb"
            config.training.max_samples = 1000
        elif dataset_name == "alpaca":
            config.model.target_model_name = "gpt2-alpaca"
            config.training.max_samples = 2000
        elif dataset_name == "dolly":
            config.model.target_model_name = "dolly-7b"
            config.training.max_samples = 1500
        
        return config
    
    def _get_text_continuation_config(self) -> CRTConfig:
        """Get configuration for text continuation tasks"""
        config = CRTConfig()
        
        # Optimize for text continuation
        config.experiment.task_type = "text_continuation"
        config.model.red_team_max_length = 30
        config.model.target_max_length = 50
        config.training.batch_size = 32
        config.reward.lambda_selfbleu = 1.0
        config.reward.lambda_cosine = 0.5
        
        return config
    
    def _get_instruction_following_config(self) -> CRTConfig:
        """Get configuration for instruction following tasks"""
        config = CRTConfig()
        
        # Optimize for instruction following
        config.experiment.task_type = "instruction_following"
        config.model.red_team_max_length = 50
        config.model.target_max_length = 100
        config.training.batch_size = 16
        config.reward.lambda_selfbleu = 0.8
        config.reward.lambda_cosine = 1.2
        
        return config
    
    def _get_ablation_config(self) -> CRTConfig:
        """Get configuration for ablation studies"""
        config = CRTConfig()
        
        # Base configuration for ablations
        config.experiment.experiment_name = "ablation_study"
        config.training.num_epochs = 5
        config.training.batch_size = 16
        
        return config
    
    def create_ablation_configs(self) -> Dict[str, CRTConfig]:
        """
        Create configurations for ablation studies
        
        Returns:
            Dict[str, CRTConfig]: Dictionary of ablation configurations
        """
        base_config = self._get_ablation_config()
        
        ablation_configs = {}
        
        # Entropy only
        entropy_config = deepcopy(base_config)
        entropy_config.reward.lambda_selfbleu = 0.0
        entropy_config.reward.lambda_cosine = 0.0
        entropy_config.reward.lambda_entropy = 1.0
        entropy_config.experiment.experiment_name = "ablation_entropy_only"
        ablation_configs["entropy_only"] = entropy_config
        
        # SelfBLEU only
        selfbleu_config = deepcopy(base_config)
        selfbleu_config.reward.lambda_selfbleu = 1.0
        selfbleu_config.reward.lambda_cosine = 0.0
        selfbleu_config.reward.lambda_entropy = 0.0
        selfbleu_config.experiment.experiment_name = "ablation_selfbleu_only"
        ablation_configs["selfbleu_only"] = selfbleu_config
        
        # Cosine only
        cosine_config = deepcopy(base_config)
        cosine_config.reward.lambda_selfbleu = 0.0
        cosine_config.reward.lambda_cosine = 1.0
        cosine_config.reward.lambda_entropy = 0.0
        cosine_config.experiment.experiment_name = "ablation_cosine_only"
        ablation_configs["cosine_only"] = cosine_config
        
        # Combined (SelfBLEU + Cosine)
        combined_config = deepcopy(base_config)
        combined_config.reward.lambda_selfbleu = 1.0
        combined_config.reward.lambda_cosine = 1.0
        combined_config.reward.lambda_entropy = 0.0
        combined_config.experiment.experiment_name = "ablation_combined_novelty"
        ablation_configs["combined_novelty"] = combined_config
        
        # Full CRT (all components)
        full_config = deepcopy(base_config)
        full_config.reward.lambda_selfbleu = 1.0
        full_config.reward.lambda_cosine = 1.0
        full_config.reward.lambda_entropy = 0.1
        full_config.experiment.experiment_name = "ablation_full_crt"
        ablation_configs["full_crt"] = full_config
        
        return ablation_configs


def load_config(config_path: Optional[str] = None, 
               config_name: Optional[str] = None,
               config_dir: Optional[str] = None) -> CRTConfig:
    """
    Convenience function to load configuration
    
    Args:
        config_path: Path to configuration file
        config_name: Name of predefined configuration
        config_dir: Directory containing configuration files
        
    Returns:
        CRTConfig: Loaded configuration
    """
    manager = ConfigManager(config_dir)
    return manager.load_config(config_path, config_name)


def validate_config(config: CRTConfig) -> bool:
    """
    Convenience function to validate configuration
    
    Args:
        config: Configuration to validate
        
    Returns:
        bool: True if configuration is valid
    """
    manager = ConfigManager()
    return manager.validate_config(config)


def get_default_config() -> CRTConfig:
    """
    Get default configuration
    
    Returns:
        CRTConfig: Default configuration
    """
    return CRTConfig()


# Default hyperparameters for different experimental setups
DEFAULT_HYPERPARAMETERS = {
    "text_continuation": {
        "model": {
            "red_team_max_length": 30,
            "target_max_length": 50,
        },
        "training": {
            "batch_size": 32,
            "learning_rate": 3e-5,
            "num_epochs": 10,
        },
        "reward": {
            "lambda_selfbleu": 1.0,
            "lambda_cosine": 0.5,
            "lambda_entropy": 0.1,
        }
    },
    "instruction_following": {
        "model": {
            "red_team_max_length": 50,
            "target_max_length": 100,
        },
        "training": {
            "batch_size": 16,
            "learning_rate": 3e-5,
            "num_epochs": 15,
        },
        "reward": {
            "lambda_selfbleu": 0.8,
            "lambda_cosine": 1.2,
            "lambda_entropy": 0.1,
        }
    }
}


if __name__ == "__main__":
    # Example usage
    logging.basicConfig(level=logging.INFO)
    
    # Create configuration manager
    manager = ConfigManager()
    
    # Load default configuration
    config = manager.load_config()
    print("Default configuration loaded")
    
    # Validate configuration
    is_valid = manager.validate_config(config)
    print(f"Configuration valid: {is_valid}")
    
    # Save configuration
    config_path = manager.save_config(config, "example")
    print(f"Configuration saved to: {config_path}")
    
    # Create task-specific configuration
    task_config = manager.get_config_for_task("text_continuation", "imdb")
    print("Task-specific configuration created")
    
    # Create ablation configurations
    ablation_configs = manager.create_ablation_configs()
    print(f"Created {len(ablation_configs)} ablation configurations")