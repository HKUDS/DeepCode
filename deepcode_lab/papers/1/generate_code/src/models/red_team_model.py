"""
Red Team Model Implementation for Curiosity-Driven Red-Teaming

This module implements the RedTeamModel class that wraps GPT2-137M for test case generation
with PPO training. It provides the core functionality for generating adversarial test cases
and updating the model using curiosity-driven rewards.

Based on the paper: "Curiosity-Driven Red-Teaming for Large Language Models"
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import (
    GPT2LMHeadModel, 
    GPT2Tokenizer, 
    GPT2Config,
    AutoTokenizer,
    AutoModelForCausalLM
)
from typing import List, Dict, Optional, Tuple, Union
import numpy as np
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class GenerationConfig:
    """Configuration for text generation"""
    max_length: int = 128
    min_length: int = 10
    temperature: float = 0.7
    top_p: float = 0.92
    top_k: int = 50
    do_sample: bool = True
    num_return_sequences: int = 1
    pad_token_id: Optional[int] = None
    eos_token_id: Optional[int] = None
    repetition_penalty: float = 1.1
    length_penalty: float = 1.0


class RedTeamModel(nn.Module):
    """
    Red Team Model that wraps GPT2 for adversarial test case generation.
    
    This model is trained using PPO with curiosity-driven rewards to generate
    diverse and effective test cases for red-teaming target language models.
    
    Key Features:
    - GPT2-137M base model for text generation
    - PPO-compatible forward pass with log probabilities
    - Configurable generation parameters
    - Support for instruction-based prompting
    - Efficient batch processing
    """
    
    def __init__(
        self,
        model_name: str = "gpt2",
        device: str = "auto",
        load_in_8bit: bool = False,
        trust_remote_code: bool = False,
        **kwargs
    ):
        """
        Initialize the Red Team Model.
        
        Args:
            model_name: HuggingFace model name (default: "gpt2" for GPT2-137M)
            device: Device to load model on ("auto", "cuda", "cpu")
            load_in_8bit: Whether to load model in 8-bit precision
            trust_remote_code: Whether to trust remote code for model loading
            **kwargs: Additional arguments for model configuration
        """
        super().__init__()
        
        self.model_name = model_name
        self.device = self._setup_device(device)
        
        # Load tokenizer
        logger.info(f"Loading tokenizer for {model_name}")
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            trust_remote_code=trust_remote_code,
            **kwargs
        )
        
        # Set pad token if not exists
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
            
        # Load model
        logger.info(f"Loading model {model_name}")
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float16 if self.device.type == "cuda" else torch.float32,
            device_map="auto" if device == "auto" else None,
            load_in_8bit=load_in_8bit,
            trust_remote_code=trust_remote_code,
            **kwargs
        )
        
        if device != "auto":
            self.model = self.model.to(self.device)
            
        # Set model to training mode for PPO
        self.model.train()
        
        # Generation configuration
        self.generation_config = GenerationConfig(
            pad_token_id=self.tokenizer.pad_token_id,
            eos_token_id=self.tokenizer.eos_token_id
        )
        
        # Cache for efficiency
        self._last_hidden_states = None
        
        logger.info(f"RedTeamModel initialized with {self.model.num_parameters():,} parameters")
    
    def _setup_device(self, device: str) -> torch.device:
        """Setup device for model."""
        if device == "auto":
            if torch.cuda.is_available():
                return torch.device("cuda")
            else:
                return torch.device("cpu")
        return torch.device(device)
    
    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        labels: Optional[torch.Tensor] = None,
        return_dict: bool = True,
        **kwargs
    ) -> Dict[str, torch.Tensor]:
        """
        Forward pass for PPO training.
        
        Args:
            input_ids: Input token IDs [batch_size, seq_len]
            attention_mask: Attention mask [batch_size, seq_len]
            labels: Labels for loss computation [batch_size, seq_len]
            return_dict: Whether to return dictionary output
            **kwargs: Additional arguments
            
        Returns:
            Dictionary containing logits, loss, and other outputs
        """
        outputs = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            labels=labels,
            return_dict=return_dict,
            **kwargs
        )
        
        if return_dict:
            return {
                'logits': outputs.logits,
                'loss': outputs.loss if labels is not None else None,
                'hidden_states': outputs.hidden_states if hasattr(outputs, 'hidden_states') else None,
                'attentions': outputs.attentions if hasattr(outputs, 'attentions') else None
            }
        
        return outputs
    
    def generate(
        self,
        instructions: Union[str, List[str]],
        num_samples: int = 1,
        max_length: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        **kwargs
    ) -> List[List[str]]:
        """
        Generate test cases for given instructions.
        
        This method implements the sampling x ~ π(.|z) from the paper,
        where z are the instruction prompts and x are the generated test cases.
        
        Args:
            instructions: Single instruction or list of instructions
            num_samples: Number of test cases to generate per instruction
            max_length: Maximum generation length
            temperature: Sampling temperature
            top_p: Top-p sampling parameter
            **kwargs: Additional generation parameters
            
        Returns:
            List of lists, where each inner list contains test cases for one instruction
        """
        if isinstance(instructions, str):
            instructions = [instructions]
            
        # Update generation config
        gen_config = self._get_generation_config(
            max_length=max_length,
            temperature=temperature,
            top_p=top_p,
            num_return_sequences=num_samples,
            **kwargs
        )
        
        all_test_cases = []
        
        with torch.no_grad():
            for instruction in instructions:
                # Format instruction as prompt
                prompt = self._format_prompt(instruction)
                
                # Tokenize
                inputs = self.tokenizer(
                    prompt,
                    return_tensors="pt",
                    padding=True,
                    truncation=True,
                    max_length=512
                ).to(self.device)
                
                # Generate
                outputs = self.model.generate(
                    **inputs,
                    **gen_config.__dict__,
                    pad_token_id=self.tokenizer.pad_token_id,
                    eos_token_id=self.tokenizer.eos_token_id
                )
                
                # Decode generated sequences
                test_cases = []
                for output in outputs:
                    # Remove input tokens
                    generated_tokens = output[inputs['input_ids'].shape[1]:]
                    test_case = self.tokenizer.decode(
                        generated_tokens,
                        skip_special_tokens=True,
                        clean_up_tokenization_spaces=True
                    ).strip()
                    
                    if test_case:  # Only add non-empty test cases
                        test_cases.append(test_case)
                
                # Ensure we have the requested number of samples
                while len(test_cases) < num_samples:
                    test_cases.append("")  # Add empty string as placeholder
                    
                all_test_cases.append(test_cases[:num_samples])
        
        return all_test_cases
    
    def compute_log_probs(
        self,
        input_ids: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None
    ) -> torch.Tensor:
        """
        Compute log probabilities for given sequences.
        
        This is used for PPO training to compute π(x|z) probabilities.
        
        Args:
            input_ids: Input token IDs [batch_size, seq_len]
            attention_mask: Attention mask [batch_size, seq_len]
            
        Returns:
            Log probabilities [batch_size, seq_len-1]
        """
        with torch.no_grad():
            outputs = self.model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                return_dict=True
            )
            
            logits = outputs.logits  # [batch_size, seq_len, vocab_size]
            
            # Compute log probabilities
            log_probs = F.log_softmax(logits, dim=-1)
            
            # Get log probs for actual tokens (shift by 1)
            target_ids = input_ids[:, 1:]  # [batch_size, seq_len-1]
            log_probs = log_probs[:, :-1, :]  # [batch_size, seq_len-1, vocab_size]
            
            # Gather log probs for target tokens
            token_log_probs = torch.gather(
                log_probs,
                dim=-1,
                index=target_ids.unsqueeze(-1)
            ).squeeze(-1)  # [batch_size, seq_len-1]
            
            return token_log_probs
    
    def compute_entropy(
        self,
        input_ids: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None
    ) -> torch.Tensor:
        """
        Compute entropy for generated sequences.
        
        This implements the entropy bonus -λ_E*log(π(x|z)) from the paper.
        
        Args:
            input_ids: Input token IDs [batch_size, seq_len]
            attention_mask: Attention mask [batch_size, seq_len]
            
        Returns:
            Entropy values [batch_size]
        """
        log_probs = self.compute_log_probs(input_ids, attention_mask)
        
        if attention_mask is not None:
            # Mask out padding tokens
            mask = attention_mask[:, 1:]  # Align with log_probs
            log_probs = log_probs * mask
            seq_lengths = mask.sum(dim=1)
        else:
            seq_lengths = torch.full((log_probs.shape[0],), log_probs.shape[1], device=log_probs.device)
        
        # Compute average log probability (negative entropy)
        entropy = -log_probs.sum(dim=1) / seq_lengths.clamp(min=1)
        
        return entropy
    
    def _format_prompt(self, instruction: str) -> str:
        """
        Format instruction as a prompt for generation.
        
        Args:
            instruction: Raw instruction text
            
        Returns:
            Formatted prompt string
        """
        # Simple prompt formatting - can be customized for different tasks
        if instruction.strip():
            return f"{instruction.strip()}"
        else:
            return ""
    
    def _get_generation_config(self, **kwargs) -> GenerationConfig:
        """Get generation configuration with overrides."""
        config_dict = self.generation_config.__dict__.copy()
        config_dict.update({k: v for k, v in kwargs.items() if v is not None})
        return GenerationConfig(**config_dict)
    
    def update_generation_config(self, **kwargs):
        """Update default generation configuration."""
        for key, value in kwargs.items():
            if hasattr(self.generation_config, key):
                setattr(self.generation_config, key, value)
            else:
                logger.warning(f"Unknown generation config parameter: {key}")
    
    def get_model_parameters(self) -> Dict[str, int]:
        """Get model parameter statistics."""
        total_params = sum(p.numel() for p in self.model.parameters())
        trainable_params = sum(p.numel() for p in self.model.parameters() if p.requires_grad)
        
        return {
            'total_parameters': total_params,
            'trainable_parameters': trainable_params,
            'non_trainable_parameters': total_params - trainable_params
        }
    
    def save_model(self, save_path: str):
        """
        Save the trained model and tokenizer.
        
        Args:
            save_path: Directory to save model
        """
        logger.info(f"Saving model to {save_path}")
        self.model.save_pretrained(save_path)
        self.tokenizer.save_pretrained(save_path)
        
        # Save generation config
        import json
        config_path = f"{save_path}/generation_config.json"
        with open(config_path, 'w') as f:
            json.dump(self.generation_config.__dict__, f, indent=2)
    
    def load_model(self, load_path: str):
        """
        Load a trained model and tokenizer.
        
        Args:
            load_path: Directory to load model from
        """
        logger.info(f"Loading model from {load_path}")
        self.model = AutoModelForCausalLM.from_pretrained(load_path).to(self.device)
        self.tokenizer = AutoTokenizer.from_pretrained(load_path)
        
        # Load generation config if exists
        import json
        import os
        config_path = f"{load_path}/generation_config.json"
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config_dict = json.load(f)
                self.generation_config = GenerationConfig(**config_dict)
    
    def to(self, device):
        """Move model to device."""
        self.device = torch.device(device)
        self.model = self.model.to(device)
        return self
    
    def train(self):
        """Set model to training mode."""
        self.model.train()
        return self
    
    def eval(self):
        """Set model to evaluation mode."""
        self.model.eval()
        return self


class RedTeamModelForTextContinuation(RedTeamModel):
    """
    Specialized Red Team Model for text continuation tasks.
    
    This variant is designed for the IMDb text continuation experiment
    where the model generates continuations for truncated movie reviews.
    """
    
    def _format_prompt(self, instruction: str) -> str:
        """
        Format instruction for text continuation.
        
        For IMDb task, the instruction is typically the first 4 words
        of a movie review that needs to be continued.
        
        Args:
            instruction: Truncated text to continue
            
        Returns:
            Formatted prompt
        """
        return instruction.strip()


class RedTeamModelForInstructionFollowing(RedTeamModel):
    """
    Specialized Red Team Model for instruction-following tasks.
    
    This variant is designed for Alpaca and Dolly instruction-following
    experiments where the model generates test cases for instruction-tuned models.
    """
    
    def _format_prompt(self, instruction: str) -> str:
        """
        Format instruction for instruction-following tasks.
        
        Args:
            instruction: Base instruction to create test case for
            
        Returns:
            Formatted prompt for test case generation
        """
        # For instruction-following, we can use the instruction directly
        # or add specific formatting for adversarial test case generation
        return instruction.strip()


def create_red_team_model(
    task_type: str = "general",
    model_name: str = "gpt2",
    **kwargs
) -> RedTeamModel:
    """
    Factory function to create appropriate Red Team Model for different tasks.
    
    Args:
        task_type: Type of task ("general", "text_continuation", "instruction_following")
        model_name: HuggingFace model name
        **kwargs: Additional arguments for model initialization
        
    Returns:
        Appropriate RedTeamModel instance
    """
    if task_type == "text_continuation":
        return RedTeamModelForTextContinuation(model_name=model_name, **kwargs)
    elif task_type == "instruction_following":
        return RedTeamModelForInstructionFollowing(model_name=model_name, **kwargs)
    else:
        return RedTeamModel(model_name=model_name, **kwargs)


# Example usage and testing
if __name__ == "__main__":
    # Test basic functionality
    logging.basicConfig(level=logging.INFO)
    
    # Create model
    model = create_red_team_model(task_type="general", model_name="gpt2")
    
    # Test generation
    instructions = ["Write a story about", "Complete this sentence:"]
    test_cases = model.generate(instructions, num_samples=2)
    
    print("Generated test cases:")
    for i, cases in enumerate(test_cases):
        print(f"Instruction {i+1}: {instructions[i]}")
        for j, case in enumerate(cases):
            print(f"  Test case {j+1}: {case}")
        print()
    
    # Test model parameters
    params = model.get_model_parameters()
    print(f"Model parameters: {params}")