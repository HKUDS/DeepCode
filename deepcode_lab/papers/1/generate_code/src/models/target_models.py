"""
Target Models Module for Curiosity-Driven Red-Teaming

This module provides a unified interface for various target LLMs that will be red-teamed.
Supports GPT2-IMDb, GPT2-Alpaca, Dolly-7B, and LLaMA2-7b-chat models.

Based on the paper: "Curiosity-Driven Red-Teaming for Large Language Models"
"""

import torch
import torch.nn.functional as F
from transformers import (
    AutoTokenizer, AutoModelForCausalLM, 
    GPT2LMHeadModel, GPT2Tokenizer,
    LlamaTokenizer, LlamaForCausalLM
)
from typing import List, Dict, Optional, Union, Any
import logging
from dataclasses import dataclass
from abc import ABC, abstractmethod
import numpy as np

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class ModelConfig:
    """Configuration for target models"""
    model_name: str
    model_path: str
    tokenizer_path: Optional[str] = None
    max_length: int = 512
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 50
    do_sample: bool = True
    pad_token_id: Optional[int] = None
    eos_token_id: Optional[int] = None
    device: str = "cuda" if torch.cuda.is_available() else "cpu"

class BaseTargetModel(ABC):
    """Abstract base class for target models"""
    
    def __init__(self, config: ModelConfig):
        self.config = config
        self.device = config.device
        self.model = None
        self.tokenizer = None
        
    @abstractmethod
    def load_model(self) -> None:
        """Load the model and tokenizer"""
        pass
    
    @abstractmethod
    def generate_response(self, inputs: Union[str, List[str]], **kwargs) -> List[str]:
        """Generate responses for given inputs"""
        pass
    
    @abstractmethod
    def compute_log_likelihood(self, inputs: List[str], targets: List[str]) -> List[float]:
        """Compute log likelihood of targets given inputs"""
        pass
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get model information"""
        return {
            "model_name": self.config.model_name,
            "model_path": self.config.model_path,
            "device": self.device,
            "max_length": self.config.max_length,
            "parameters": self._count_parameters() if self.model else 0
        }
    
    def _count_parameters(self) -> int:
        """Count model parameters"""
        if self.model is None:
            return 0
        return sum(p.numel() for p in self.model.parameters())

class GPT2TargetModel(BaseTargetModel):
    """GPT2-based target model (for IMDb and Alpaca variants)"""
    
    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.load_model()
    
    def load_model(self) -> None:
        """Load GPT2 model and tokenizer"""
        try:
            logger.info(f"Loading GPT2 model: {self.config.model_path}")
            
            # Load tokenizer
            tokenizer_path = self.config.tokenizer_path or self.config.model_path
            self.tokenizer = GPT2Tokenizer.from_pretrained(tokenizer_path)
            
            # Set pad token if not exists
            if self.tokenizer.pad_token is None:
                self.tokenizer.pad_token = self.tokenizer.eos_token
                self.config.pad_token_id = self.tokenizer.eos_token_id
            
            # Load model
            self.model = GPT2LMHeadModel.from_pretrained(
                self.config.model_path,
                torch_dtype=torch.float16 if self.device == "cuda" else torch.float32
            )
            self.model.to(self.device)
            self.model.eval()
            
            logger.info(f"Successfully loaded GPT2 model with {self._count_parameters():,} parameters")
            
        except Exception as e:
            logger.error(f"Failed to load GPT2 model: {e}")
            raise
    
    def generate_response(self, inputs: Union[str, List[str]], **kwargs) -> List[str]:
        """Generate responses using GPT2"""
        if isinstance(inputs, str):
            inputs = [inputs]
        
        # Override config with kwargs
        generation_config = {
            "max_length": kwargs.get("max_length", self.config.max_length),
            "temperature": kwargs.get("temperature", self.config.temperature),
            "top_p": kwargs.get("top_p", self.config.top_p),
            "top_k": kwargs.get("top_k", self.config.top_k),
            "do_sample": kwargs.get("do_sample", self.config.do_sample),
            "pad_token_id": self.config.pad_token_id,
            "eos_token_id": self.config.eos_token_id,
            "num_return_sequences": kwargs.get("num_return_sequences", 1)
        }
        
        responses = []
        
        with torch.no_grad():
            for input_text in inputs:
                try:
                    # Tokenize input
                    inputs_encoded = self.tokenizer.encode(
                        input_text, 
                        return_tensors="pt",
                        truncation=True,
                        max_length=self.config.max_length // 2  # Leave room for generation
                    ).to(self.device)
                    
                    # Generate response
                    outputs = self.model.generate(
                        inputs_encoded,
                        **generation_config
                    )
                    
                    # Decode response (remove input part)
                    input_length = inputs_encoded.shape[1]
                    generated_tokens = outputs[0][input_length:]
                    response = self.tokenizer.decode(generated_tokens, skip_special_tokens=True)
                    
                    responses.append(response.strip())
                    
                except Exception as e:
                    logger.warning(f"Failed to generate response for input: {e}")
                    responses.append("")
        
        return responses
    
    def compute_log_likelihood(self, inputs: List[str], targets: List[str]) -> List[float]:
        """Compute log likelihood of targets given inputs"""
        if len(inputs) != len(targets):
            raise ValueError("Inputs and targets must have the same length")
        
        log_likelihoods = []
        
        with torch.no_grad():
            for input_text, target_text in zip(inputs, targets):
                try:
                    # Combine input and target
                    full_text = input_text + target_text
                    
                    # Tokenize
                    input_ids = self.tokenizer.encode(input_text, return_tensors="pt").to(self.device)
                    full_ids = self.tokenizer.encode(full_text, return_tensors="pt").to(self.device)
                    
                    # Get target part
                    target_ids = full_ids[0][input_ids.shape[1]:]
                    
                    if len(target_ids) == 0:
                        log_likelihoods.append(float('-inf'))
                        continue
                    
                    # Forward pass
                    outputs = self.model(full_ids)
                    logits = outputs.logits[0]  # Remove batch dimension
                    
                    # Compute log probabilities for target tokens
                    log_probs = F.log_softmax(logits, dim=-1)
                    target_log_probs = log_probs[input_ids.shape[1]-1:-1, target_ids]
                    
                    # Sum log probabilities
                    log_likelihood = target_log_probs.diag().sum().item()
                    log_likelihoods.append(log_likelihood)
                    
                except Exception as e:
                    logger.warning(f"Failed to compute log likelihood: {e}")
                    log_likelihoods.append(float('-inf'))
        
        return log_likelihoods

class DollyTargetModel(BaseTargetModel):
    """Databricks Dolly-7B target model"""
    
    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.load_model()
    
    def load_model(self) -> None:
        """Load Dolly model and tokenizer"""
        try:
            logger.info(f"Loading Dolly model: {self.config.model_path}")
            
            # Load tokenizer and model
            self.tokenizer = AutoTokenizer.from_pretrained(self.config.model_path)
            self.model = AutoModelForCausalLM.from_pretrained(
                self.config.model_path,
                torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
                device_map="auto" if self.device == "cuda" else None
            )
            
            if self.device != "cuda":
                self.model.to(self.device)
            
            self.model.eval()
            
            # Set pad token
            if self.tokenizer.pad_token is None:
                self.tokenizer.pad_token = self.tokenizer.eos_token
                self.config.pad_token_id = self.tokenizer.eos_token_id
            
            logger.info(f"Successfully loaded Dolly model with {self._count_parameters():,} parameters")
            
        except Exception as e:
            logger.error(f"Failed to load Dolly model: {e}")
            raise
    
    def generate_response(self, inputs: Union[str, List[str]], **kwargs) -> List[str]:
        """Generate responses using Dolly with instruction format"""
        if isinstance(inputs, str):
            inputs = [inputs]
        
        # Dolly instruction format
        instruction_template = "### Instruction:\n{instruction}\n\n### Response:\n"
        
        generation_config = {
            "max_length": kwargs.get("max_length", self.config.max_length),
            "temperature": kwargs.get("temperature", self.config.temperature),
            "top_p": kwargs.get("top_p", self.config.top_p),
            "do_sample": kwargs.get("do_sample", self.config.do_sample),
            "pad_token_id": self.config.pad_token_id,
            "eos_token_id": self.config.eos_token_id,
        }
        
        responses = []
        
        with torch.no_grad():
            for input_text in inputs:
                try:
                    # Format instruction
                    formatted_input = instruction_template.format(instruction=input_text)
                    
                    # Tokenize
                    inputs_encoded = self.tokenizer.encode(
                        formatted_input,
                        return_tensors="pt",
                        truncation=True,
                        max_length=self.config.max_length // 2
                    ).to(self.device)
                    
                    # Generate
                    outputs = self.model.generate(
                        inputs_encoded,
                        **generation_config
                    )
                    
                    # Decode response
                    input_length = inputs_encoded.shape[1]
                    generated_tokens = outputs[0][input_length:]
                    response = self.tokenizer.decode(generated_tokens, skip_special_tokens=True)
                    
                    responses.append(response.strip())
                    
                except Exception as e:
                    logger.warning(f"Failed to generate Dolly response: {e}")
                    responses.append("")
        
        return responses
    
    def compute_log_likelihood(self, inputs: List[str], targets: List[str]) -> List[float]:
        """Compute log likelihood using Dolly format"""
        # Similar to GPT2 but with instruction formatting
        instruction_template = "### Instruction:\n{instruction}\n\n### Response:\n"
        
        formatted_inputs = [instruction_template.format(instruction=inp) for inp in inputs]
        
        # Use similar logic as GPT2
        log_likelihoods = []
        
        with torch.no_grad():
            for input_text, target_text in zip(formatted_inputs, targets):
                try:
                    full_text = input_text + target_text
                    
                    input_ids = self.tokenizer.encode(input_text, return_tensors="pt").to(self.device)
                    full_ids = self.tokenizer.encode(full_text, return_tensors="pt").to(self.device)
                    
                    target_ids = full_ids[0][input_ids.shape[1]:]
                    
                    if len(target_ids) == 0:
                        log_likelihoods.append(float('-inf'))
                        continue
                    
                    outputs = self.model(full_ids)
                    logits = outputs.logits[0]
                    
                    log_probs = F.log_softmax(logits, dim=-1)
                    target_log_probs = log_probs[input_ids.shape[1]-1:-1, target_ids]
                    
                    log_likelihood = target_log_probs.diag().sum().item()
                    log_likelihoods.append(log_likelihood)
                    
                except Exception as e:
                    logger.warning(f"Failed to compute Dolly log likelihood: {e}")
                    log_likelihoods.append(float('-inf'))
        
        return log_likelihoods

class LlamaTargetModel(BaseTargetModel):
    """LLaMA2-7b-chat target model"""
    
    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.load_model()
    
    def load_model(self) -> None:
        """Load LLaMA model and tokenizer"""
        try:
            logger.info(f"Loading LLaMA model: {self.config.model_path}")
            
            # Load tokenizer and model
            self.tokenizer = AutoTokenizer.from_pretrained(self.config.model_path)
            self.model = AutoModelForCausalLM.from_pretrained(
                self.config.model_path,
                torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
                device_map="auto" if self.device == "cuda" else None
            )
            
            if self.device != "cuda":
                self.model.to(self.device)
            
            self.model.eval()
            
            # Set pad token
            if self.tokenizer.pad_token is None:
                self.tokenizer.pad_token = self.tokenizer.eos_token
                self.config.pad_token_id = self.tokenizer.eos_token_id
            
            logger.info(f"Successfully loaded LLaMA model with {self._count_parameters():,} parameters")
            
        except Exception as e:
            logger.error(f"Failed to load LLaMA model: {e}")
            raise
    
    def generate_response(self, inputs: Union[str, List[str]], **kwargs) -> List[str]:
        """Generate responses using LLaMA2-chat format"""
        if isinstance(inputs, str):
            inputs = [inputs]
        
        # LLaMA2-chat format
        system_message = "You are a helpful, respectful and honest assistant."
        chat_template = f"<s>[INST] <<SYS>>\n{system_message}\n<</SYS>>\n\n{{user_message}} [/INST]"
        
        generation_config = {
            "max_length": kwargs.get("max_length", self.config.max_length),
            "temperature": kwargs.get("temperature", self.config.temperature),
            "top_p": kwargs.get("top_p", self.config.top_p),
            "do_sample": kwargs.get("do_sample", self.config.do_sample),
            "pad_token_id": self.config.pad_token_id,
            "eos_token_id": self.config.eos_token_id,
        }
        
        responses = []
        
        with torch.no_grad():
            for input_text in inputs:
                try:
                    # Format for chat
                    formatted_input = chat_template.format(user_message=input_text)
                    
                    # Tokenize
                    inputs_encoded = self.tokenizer.encode(
                        formatted_input,
                        return_tensors="pt",
                        truncation=True,
                        max_length=self.config.max_length // 2
                    ).to(self.device)
                    
                    # Generate
                    outputs = self.model.generate(
                        inputs_encoded,
                        **generation_config
                    )
                    
                    # Decode response
                    input_length = inputs_encoded.shape[1]
                    generated_tokens = outputs[0][input_length:]
                    response = self.tokenizer.decode(generated_tokens, skip_special_tokens=True)
                    
                    responses.append(response.strip())
                    
                except Exception as e:
                    logger.warning(f"Failed to generate LLaMA response: {e}")
                    responses.append("")
        
        return responses
    
    def compute_log_likelihood(self, inputs: List[str], targets: List[str]) -> List[float]:
        """Compute log likelihood using LLaMA2-chat format"""
        system_message = "You are a helpful, respectful and honest assistant."
        chat_template = f"<s>[INST] <<SYS>>\n{system_message}\n<</SYS>>\n\n{{user_message}} [/INST]"
        
        formatted_inputs = [chat_template.format(user_message=inp) for inp in inputs]
        
        log_likelihoods = []
        
        with torch.no_grad():
            for input_text, target_text in zip(formatted_inputs, targets):
                try:
                    full_text = input_text + target_text
                    
                    input_ids = self.tokenizer.encode(input_text, return_tensors="pt").to(self.device)
                    full_ids = self.tokenizer.encode(full_text, return_tensors="pt").to(self.device)
                    
                    target_ids = full_ids[0][input_ids.shape[1]:]
                    
                    if len(target_ids) == 0:
                        log_likelihoods.append(float('-inf'))
                        continue
                    
                    outputs = self.model(full_ids)
                    logits = outputs.logits[0]
                    
                    log_probs = F.log_softmax(logits, dim=-1)
                    target_log_probs = log_probs[input_ids.shape[1]-1:-1, target_ids]
                    
                    log_likelihood = target_log_probs.diag().sum().item()
                    log_likelihoods.append(log_likelihood)
                    
                except Exception as e:
                    logger.warning(f"Failed to compute LLaMA log likelihood: {e}")
                    log_likelihoods.append(float('-inf'))
        
        return log_likelihoods

class TargetLLM:
    """Unified interface for all target models"""
    
    # Model configurations
    MODEL_CONFIGS = {
        "gpt2-imdb": ModelConfig(
            model_name="gpt2-imdb",
            model_path="gpt2",  # Will be fine-tuned on IMDb
            max_length=256,
            temperature=0.7
        ),
        "gpt2-alpaca": ModelConfig(
            model_name="gpt2-alpaca", 
            model_path="gpt2",  # Will be fine-tuned on Alpaca
            max_length=512,
            temperature=0.7
        ),
        "dolly-7b": ModelConfig(
            model_name="dolly-7b",
            model_path="databricks/dolly-v2-7b",
            max_length=1024,
            temperature=0.7
        ),
        "llama2-7b-chat": ModelConfig(
            model_name="llama2-7b-chat",
            model_path="meta-llama/Llama-2-7b-chat-hf",
            max_length=2048,
            temperature=0.7
        )
    }
    
    def __init__(self, model_name: str, custom_config: Optional[ModelConfig] = None):
        """Initialize target LLM
        
        Args:
            model_name: Name of the model to load
            custom_config: Optional custom configuration
        """
        self.model_name = model_name
        
        if custom_config:
            self.config = custom_config
        elif model_name in self.MODEL_CONFIGS:
            self.config = self.MODEL_CONFIGS[model_name]
        else:
            raise ValueError(f"Unknown model: {model_name}. Available: {list(self.MODEL_CONFIGS.keys())}")
        
        # Initialize appropriate model class
        self.model = self._create_model()
        
        logger.info(f"Initialized TargetLLM: {model_name}")
    
    def _create_model(self) -> BaseTargetModel:
        """Create appropriate model instance"""
        if "gpt2" in self.model_name.lower():
            return GPT2TargetModel(self.config)
        elif "dolly" in self.model_name.lower():
            return DollyTargetModel(self.config)
        elif "llama" in self.model_name.lower():
            return LlamaTargetModel(self.config)
        else:
            # Default to GPT2 for unknown models
            logger.warning(f"Unknown model type for {self.model_name}, defaulting to GPT2")
            return GPT2TargetModel(self.config)
    
    def generate_response(self, inputs: Union[str, List[str]], **kwargs) -> List[str]:
        """Generate responses from target model
        
        Args:
            inputs: Input text(s) to generate responses for
            **kwargs: Generation parameters
            
        Returns:
            List of generated responses
        """
        return self.model.generate_response(inputs, **kwargs)
    
    def compute_log_likelihood(self, inputs: List[str], targets: List[str]) -> List[float]:
        """Compute log likelihood of targets given inputs
        
        Args:
            inputs: Input texts
            targets: Target texts
            
        Returns:
            List of log likelihoods
        """
        return self.model.compute_log_likelihood(inputs, targets)
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get model information"""
        return self.model.get_model_info()
    
    @classmethod
    def list_available_models(cls) -> List[str]:
        """List available model names"""
        return list(cls.MODEL_CONFIGS.keys())
    
    @classmethod
    def create_custom_model(cls, model_name: str, model_path: str, **kwargs) -> 'TargetLLM':
        """Create custom model configuration
        
        Args:
            model_name: Custom model name
            model_path: Path to model
            **kwargs: Additional configuration parameters
            
        Returns:
            TargetLLM instance with custom configuration
        """
        config = ModelConfig(
            model_name=model_name,
            model_path=model_path,
            **kwargs
        )
        return cls(model_name, config)

# Utility functions
def load_target_model(model_name: str, **kwargs) -> TargetLLM:
    """Convenience function to load a target model
    
    Args:
        model_name: Name of the model to load
        **kwargs: Additional configuration parameters
        
    Returns:
        TargetLLM instance
    """
    return TargetLLM(model_name, **kwargs)

def get_model_for_task(task: str) -> str:
    """Get recommended model for specific task
    
    Args:
        task: Task name ("text_continuation", "instruction_following", "safety")
        
    Returns:
        Recommended model name
    """
    task_models = {
        "text_continuation": "gpt2-imdb",
        "instruction_following": "gpt2-alpaca", 
        "safety": "llama2-7b-chat",
        "general": "dolly-7b"
    }
    
    return task_models.get(task, "gpt2-imdb")

if __name__ == "__main__":
    # Example usage
    print("Available target models:")
    for model in TargetLLM.list_available_models():
        print(f"  - {model}")
    
    # Test with a simple model (if available)
    try:
        model = TargetLLM("gpt2-imdb")
        info = model.get_model_info()
        print(f"\nLoaded model info: {info}")
        
        # Test generation
        test_input = "The movie was"
        responses = model.generate_response(test_input, max_length=50)
        print(f"\nTest generation:")
        print(f"Input: {test_input}")
        print(f"Response: {responses[0]}")
        
    except Exception as e:
        print(f"Could not test model loading: {e}")