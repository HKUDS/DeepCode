"""
Global configuration constants for Curiosity-Driven Red-Teaming.

This module defines default hyperparameters, model paths, and other constants
used throughout the CRT system.
"""

import os
from typing import Dict, Any, List
from pathlib import Path

# =============================================================================
# Model Configurations
# =============================================================================

# Model names and paths
MODEL_PATHS = {
    # Red team models
    "red_team_base": "gpt2",
    "red_team_medium": "gpt2-medium", 
    "red_team_large": "gpt2-large",
    
    # Target models for red-teaming
    "gpt2_imdb": "lvwerra/gpt2-imdb",
    "gpt2_alpaca": "chavinlo/alpaca-native",
    "dolly_7b": "databricks/dolly-v2-7b",
    "llama2_7b_chat": "meta-llama/Llama-2-7b-chat-hf",
    
    # Evaluation models
    "toxicity_classifier": "facebook/roberta-hate-speech-dynabench-r4-target",
    "sentence_embedder": "sentence-transformers/all-MiniLM-L6-v2",
    
    # Alternative models for ablation studies
    "bert_base": "bert-base-uncased",
    "roberta_base": "roberta-base",
}

# Model-specific configurations
MODEL_CONFIGS = {
    "gpt2": {
        "max_length": 1024,
        "vocab_size": 50257,
        "n_positions": 1024,
        "n_ctx": 1024,
        "n_embd": 768,
        "n_layer": 12,
        "n_head": 12,
    },
    "gpt2-medium": {
        "max_length": 1024,
        "vocab_size": 50257,
        "n_positions": 1024,
        "n_ctx": 1024,
        "n_embd": 1024,
        "n_layer": 24,
        "n_head": 16,
    },
    "dolly_7b": {
        "max_length": 2048,
        "vocab_size": 50280,
        "hidden_size": 4096,
        "num_hidden_layers": 32,
        "num_attention_heads": 32,
    },
    "llama2_7b": {
        "max_length": 4096,
        "vocab_size": 32000,
        "hidden_size": 4096,
        "intermediate_size": 11008,
        "num_hidden_layers": 32,
        "num_attention_heads": 32,
    }
}

# =============================================================================
# Default Hyperparameters
# =============================================================================

DEFAULT_HYPERPARAMETERS = {
    # PPO Training Parameters
    "ppo": {
        "learning_rate": 3e-5,
        "batch_size": 128,
        "mini_batch_size": 32,
        "ppo_epochs": 4,
        "gamma": 1.0,
        "gae_lambda": 0.95,
        "clip_range": 0.2,
        "clip_range_vf": None,
        "normalize_advantage": True,
        "ent_coef": 0.0,
        "vf_coef": 0.5,
        "max_grad_norm": 0.5,
        "target_kl": 0.1,
        "kl_penalty": "kl",
        "adaptive_kl": False,
        "init_kl_coef": 0.2,
    },
    
    # Generation Parameters
    "generation": {
        "max_new_tokens": 100,
        "min_length": 10,
        "temperature": 0.7,
        "top_p": 0.92,
        "top_k": 50,
        "do_sample": True,
        "pad_token_id": 50256,
        "eos_token_id": 50256,
        "repetition_penalty": 1.0,
        "length_penalty": 1.0,
        "num_beams": 1,
        "early_stopping": False,
    },
    
    # Reward Parameters
    "rewards": {
        # Effectiveness reward (toxicity)
        "effectiveness_weight": 1.0,
        "toxicity_threshold": 0.5,
        
        # Novelty rewards
        "lambda_selfbleu": 0.1,
        "lambda_cosine": 0.1,
        "lambda_entropy": 0.01,
        
        # SelfBLEU parameters
        "selfbleu_ngrams": [2, 3, 4, 5],
        "selfbleu_weights": [0.25, 0.25, 0.25, 0.25],
        
        # Cosine similarity parameters
        "cosine_similarity_threshold": 0.8,
        "embedding_dim": 384,
        
        # KL penalty
        "beta_kl": 0.1,
        "ref_model_weight": 1.0,
    },
    
    # Training Configuration
    "training": {
        "num_epochs": 10,
        "steps_per_epoch": 1000,
        "eval_frequency": 100,
        "save_frequency": 500,
        "log_frequency": 10,
        "gradient_accumulation_steps": 1,
        "warmup_steps": 100,
        "max_steps": 10000,
        "early_stopping_patience": 3,
        "early_stopping_threshold": 0.01,
    },
    
    # Memory Management
    "memory": {
        "max_history_size": 10000,
        "cleanup_frequency": 1000,
        "memory_threshold_mb": 8192,
        "cache_size": 1000,
        "use_circular_buffer": True,
        "buffer_size": 5000,
    },
    
    # Evaluation Parameters
    "evaluation": {
        "num_test_cases": 1000,
        "diversity_sample_size": 500,
        "quality_threshold": 0.5,
        "coverage_threshold": 0.1,
        "min_unique_cases": 100,
        "eval_batch_size": 32,
    }
}

# =============================================================================
# Task-Specific Configurations
# =============================================================================

TASK_CONFIGS = {
    "text_continuation": {
        "dataset": "imdb",
        "target_model": "gpt2_imdb",
        "max_prompt_length": 4,  # First 4 words
        "task_type": "continuation",
        "evaluation_metrics": ["quality", "diversity", "coverage"],
        "hyperparameters": {
            "lambda_selfbleu": 0.1,
            "lambda_cosine": 0.1,
            "lambda_entropy": 0.01,
            "beta_kl": 0.1,
        }
    },
    
    "instruction_following": {
        "datasets": ["alpaca", "dolly"],
        "target_models": ["gpt2_alpaca", "dolly_7b"],
        "task_type": "instruction",
        "evaluation_metrics": ["quality", "diversity", "coverage"],
        "hyperparameters": {
            "lambda_selfbleu": 0.15,
            "lambda_cosine": 0.15,
            "lambda_entropy": 0.02,
            "beta_kl": 0.2,
        }
    },
    
    "safety_evaluation": {
        "target_models": ["llama2_7b_chat"],
        "task_type": "safety",
        "evaluation_metrics": ["attack_success_rate", "diversity"],
        "hyperparameters": {
            "lambda_selfbleu": 0.2,
            "lambda_cosine": 0.2,
            "lambda_entropy": 0.05,
            "beta_kl": 0.3,
        }
    }
}

# =============================================================================
# Experimental Configurations
# =============================================================================

EXPERIMENT_CONFIGS = {
    "baseline_rl": {
        "name": "Standard RL Baseline",
        "use_novelty_rewards": False,
        "use_entropy_bonus": False,
        "hyperparameters": {
            "lambda_selfbleu": 0.0,
            "lambda_cosine": 0.0,
            "lambda_entropy": 0.0,
        }
    },
    
    "rl_tdiv": {
        "name": "RL + Target Diversity",
        "use_novelty_rewards": False,
        "use_entropy_bonus": True,
        "hyperparameters": {
            "lambda_selfbleu": 0.0,
            "lambda_cosine": 0.0,
            "lambda_entropy": 0.01,
        }
    },
    
    "crt_selfbleu": {
        "name": "CRT with SelfBLEU only",
        "use_novelty_rewards": True,
        "use_entropy_bonus": False,
        "hyperparameters": {
            "lambda_selfbleu": 0.1,
            "lambda_cosine": 0.0,
            "lambda_entropy": 0.0,
        }
    },
    
    "crt_cosine": {
        "name": "CRT with Cosine only",
        "use_novelty_rewards": True,
        "use_entropy_bonus": False,
        "hyperparameters": {
            "lambda_selfbleu": 0.0,
            "lambda_cosine": 0.1,
            "lambda_entropy": 0.0,
        }
    },
    
    "crt_entropy": {
        "name": "CRT with Entropy only",
        "use_novelty_rewards": False,
        "use_entropy_bonus": True,
        "hyperparameters": {
            "lambda_selfbleu": 0.0,
            "lambda_cosine": 0.0,
            "lambda_entropy": 0.01,
        }
    },
    
    "crt_full": {
        "name": "Full CRT (SelfBLEU + Cosine + Entropy)",
        "use_novelty_rewards": True,
        "use_entropy_bonus": True,
        "hyperparameters": {
            "lambda_selfbleu": 0.1,
            "lambda_cosine": 0.1,
            "lambda_entropy": 0.01,
        }
    }
}

# =============================================================================
# Dataset Configurations
# =============================================================================

DATASET_CONFIGS = {
    "imdb": {
        "name": "IMDb Movie Reviews",
        "path": "imdb",
        "split": "train",
        "text_column": "text",
        "label_column": "label",
        "max_samples": 10000,
        "preprocessing": {
            "truncate_to_words": 4,
            "min_length": 10,
            "max_length": 512,
        }
    },
    
    "alpaca": {
        "name": "Stanford Alpaca",
        "path": "tatsu-lab/alpaca",
        "split": "train",
        "instruction_column": "instruction",
        "input_column": "input",
        "output_column": "output",
        "max_samples": 5000,
        "preprocessing": {
            "combine_instruction_input": True,
            "max_length": 512,
        }
    },
    
    "dolly": {
        "name": "Databricks Dolly-15K",
        "path": "databricks/databricks-dolly-15k",
        "split": "train",
        "instruction_column": "instruction",
        "context_column": "context",
        "response_column": "response",
        "max_samples": 5000,
        "preprocessing": {
            "combine_instruction_context": True,
            "max_length": 512,
        }
    }
}

# =============================================================================
# Directory and File Paths
# =============================================================================

# Base directories
BASE_DIR = Path(__file__).parent.parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIGS_DIR = BASE_DIR / "configs"
EXPERIMENTS_DIR = BASE_DIR / "experiments"
RESULTS_DIR = BASE_DIR / "results"
LOGS_DIR = BASE_DIR / "logs"
MODELS_DIR = BASE_DIR / "models"

# Data subdirectories
DATASETS_DIR = DATA_DIR / "datasets"
GENERATED_TESTCASES_DIR = DATA_DIR / "generated_testcases"
TARGET_RESPONSES_DIR = DATA_DIR / "target_responses"
EVALUATION_RESULTS_DIR = DATA_DIR / "evaluation_results"

# Configuration files
CONFIG_FILES = {
    "text_continuation": CONFIGS_DIR / "text_continuation.yaml",
    "instruction_following": CONFIGS_DIR / "instruction_following.yaml",
    "safety_evaluation": CONFIGS_DIR / "safety_evaluation.yaml",
    "ablation_configs": CONFIGS_DIR / "ablation_configs",
    "model_configs": CONFIGS_DIR / "model_configs",
}

# =============================================================================
# Logging Configuration
# =============================================================================

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        },
        "detailed": {
            "format": "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d: %(message)s"
        }
    },
    "handlers": {
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stdout"
        },
        "file": {
            "level": "DEBUG",
            "class": "logging.FileHandler",
            "formatter": "detailed",
            "filename": str(LOGS_DIR / "crt.log"),
            "mode": "a"
        }
    },
    "loggers": {
        "": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False
        }
    }
}

# =============================================================================
# Environment Variables
# =============================================================================

# Default environment variables
DEFAULT_ENV_VARS = {
    "CUDA_VISIBLE_DEVICES": "0",
    "TOKENIZERS_PARALLELISM": "false",
    "TRANSFORMERS_CACHE": str(MODELS_DIR / "transformers_cache"),
    "HF_HOME": str(MODELS_DIR / "huggingface_cache"),
    "WANDB_PROJECT": "curiosity-redteam",
    "WANDB_ENTITY": "redteam-research",
}

# =============================================================================
# Utility Functions
# =============================================================================

def get_model_path(model_name: str) -> str:
    """Get the path for a specific model."""
    return MODEL_PATHS.get(model_name, model_name)

def get_model_config(model_name: str) -> Dict[str, Any]:
    """Get configuration for a specific model."""
    base_name = model_name.split("/")[-1].split("-")[0]
    return MODEL_CONFIGS.get(base_name, {})

def get_task_config(task_name: str) -> Dict[str, Any]:
    """Get configuration for a specific task."""
    return TASK_CONFIGS.get(task_name, {})

def get_experiment_config(experiment_name: str) -> Dict[str, Any]:
    """Get configuration for a specific experiment."""
    return EXPERIMENT_CONFIGS.get(experiment_name, {})

def get_dataset_config(dataset_name: str) -> Dict[str, Any]:
    """Get configuration for a specific dataset."""
    return DATASET_CONFIGS.get(dataset_name, {})

def merge_configs(*configs: Dict[str, Any]) -> Dict[str, Any]:
    """Merge multiple configuration dictionaries."""
    merged = {}
    for config in configs:
        for key, value in config.items():
            if isinstance(value, dict) and key in merged:
                merged[key] = merge_configs(merged[key], value)
            else:
                merged[key] = value
    return merged

def create_directories():
    """Create necessary directories if they don't exist."""
    directories = [
        DATA_DIR, CONFIGS_DIR, EXPERIMENTS_DIR, RESULTS_DIR, 
        LOGS_DIR, MODELS_DIR, DATASETS_DIR, GENERATED_TESTCASES_DIR,
        TARGET_RESPONSES_DIR, EVALUATION_RESULTS_DIR
    ]
    
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

def setup_environment():
    """Set up environment variables."""
    for key, value in DEFAULT_ENV_VARS.items():
        if key not in os.environ:
            os.environ[key] = value

# =============================================================================
# Constants for Reproducibility
# =============================================================================

# Random seeds for reproducibility
RANDOM_SEEDS = {
    "default": 42,
    "data_split": 123,
    "model_init": 456,
    "training": 789,
    "evaluation": 101112,
}

# Version information
VERSION_INFO = {
    "crt_version": "1.0.0",
    "python_version": "3.8+",
    "torch_version": "1.13.0",
    "transformers_version": "4.21.0",
    "trlx_version": "0.4.0",
}

# Hardware requirements
HARDWARE_REQUIREMENTS = {
    "min_gpu_memory_gb": 8,
    "recommended_gpu_memory_gb": 16,
    "min_ram_gb": 16,
    "recommended_ram_gb": 32,
    "min_storage_gb": 50,
}

# =============================================================================
# Validation Functions
# =============================================================================

def validate_hyperparameters(hyperparams: Dict[str, Any]) -> bool:
    """Validate hyperparameter values."""
    try:
        # Check learning rate
        if "learning_rate" in hyperparams:
            lr = hyperparams["learning_rate"]
            if not (1e-6 <= lr <= 1e-2):
                return False
        
        # Check reward weights
        reward_weights = ["lambda_selfbleu", "lambda_cosine", "lambda_entropy"]
        for weight in reward_weights:
            if weight in hyperparams:
                if not (0.0 <= hyperparams[weight] <= 1.0):
                    return False
        
        # Check batch sizes
        if "batch_size" in hyperparams:
            if hyperparams["batch_size"] <= 0:
                return False
        
        return True
    except Exception:
        return False

def validate_model_config(model_config: Dict[str, Any]) -> bool:
    """Validate model configuration."""
    required_keys = ["max_length", "vocab_size"]
    return all(key in model_config for key in required_keys)

def validate_paths() -> bool:
    """Validate that all required paths exist or can be created."""
    try:
        create_directories()
        return True
    except Exception:
        return False

# Initialize environment on import
setup_environment()