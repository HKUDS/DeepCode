"""
Prompt Templates for Curiosity-Driven Red-Teaming

This module provides system prompts and templates for different target models
used in the red-teaming experiments. It includes templates for instruction-following
models like Alpaca, LLaMA2, and Dolly, as well as text continuation tasks.

Classes:
    - PromptTemplate: Base class for prompt templates
    - InstructionTemplate: Template for instruction-following tasks
    - TextContinuationTemplate: Template for text continuation tasks

Templates:
    - INSTRUCTION_TEMPLATE: Generic instruction template
    - LLAMA2_TEMPLATE: LLaMA2-specific chat template
    - ALPACA_TEMPLATE: Stanford Alpaca template
    - DATABRICKS_TEMPLATE: Databricks Dolly template
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PromptConfig:
    """Configuration for prompt templates."""
    system_message: str = ""
    instruction_prefix: str = ""
    instruction_suffix: str = ""
    response_prefix: str = ""
    response_suffix: str = ""
    separator: str = "\n"
    max_length: int = 512
    truncate_instruction: bool = True


class PromptTemplate(ABC):
    """Abstract base class for prompt templates."""
    
    def __init__(self, config: Optional[PromptConfig] = None):
        """Initialize prompt template with configuration."""
        self.config = config or PromptConfig()
        
    @abstractmethod
    def format_prompt(self, instruction: str, **kwargs) -> str:
        """Format instruction into model-specific prompt."""
        pass
    
    @abstractmethod
    def extract_response(self, generated_text: str, instruction: str) -> str:
        """Extract response from generated text."""
        pass
    
    def validate_prompt(self, prompt: str) -> bool:
        """Validate that prompt meets requirements."""
        if len(prompt.strip()) == 0:
            return False
        if len(prompt) > self.config.max_length and self.config.truncate_instruction:
            logger.warning(f"Prompt length {len(prompt)} exceeds max length {self.config.max_length}")
        return True


class InstructionTemplate(PromptTemplate):
    """Template for instruction-following tasks."""
    
    def format_prompt(self, instruction: str, **kwargs) -> str:
        """Format instruction with prefix and suffix."""
        # Add system message if provided
        prompt_parts = []
        if self.config.system_message:
            prompt_parts.append(self.config.system_message)
        
        # Add instruction with formatting
        formatted_instruction = f"{self.config.instruction_prefix}{instruction}{self.config.instruction_suffix}"
        prompt_parts.append(formatted_instruction)
        
        # Add response prefix if provided
        if self.config.response_prefix:
            prompt_parts.append(self.config.response_prefix)
        
        prompt = self.config.separator.join(prompt_parts)
        
        # Truncate if necessary
        if len(prompt) > self.config.max_length and self.config.truncate_instruction:
            # Keep system message and response prefix, truncate instruction
            available_length = self.config.max_length
            if self.config.system_message:
                available_length -= len(self.config.system_message) + len(self.config.separator)
            if self.config.response_prefix:
                available_length -= len(self.config.response_prefix) + len(self.config.separator)
            
            prefix_suffix_length = len(self.config.instruction_prefix) + len(self.config.instruction_suffix)
            max_instruction_length = available_length - prefix_suffix_length
            
            if max_instruction_length > 0:
                truncated_instruction = instruction[:max_instruction_length]
                formatted_instruction = f"{self.config.instruction_prefix}{truncated_instruction}{self.config.instruction_suffix}"
                
                prompt_parts = []
                if self.config.system_message:
                    prompt_parts.append(self.config.system_message)
                prompt_parts.append(formatted_instruction)
                if self.config.response_prefix:
                    prompt_parts.append(self.config.response_prefix)
                
                prompt = self.config.separator.join(prompt_parts)
        
        return prompt
    
    def extract_response(self, generated_text: str, instruction: str) -> str:
        """Extract response from generated text."""
        # Remove the original prompt
        prompt = self.format_prompt(instruction)
        if generated_text.startswith(prompt):
            response = generated_text[len(prompt):].strip()
        else:
            response = generated_text.strip()
        
        # Remove response suffix if present
        if self.config.response_suffix and response.endswith(self.config.response_suffix):
            response = response[:-len(self.config.response_suffix)].strip()
        
        return response


class TextContinuationTemplate(PromptTemplate):
    """Template for text continuation tasks."""
    
    def format_prompt(self, instruction: str, **kwargs) -> str:
        """Format text for continuation (minimal processing)."""
        prompt = instruction.strip()
        
        # Truncate if necessary
        if len(prompt) > self.config.max_length and self.config.truncate_instruction:
            prompt = prompt[:self.config.max_length]
        
        return prompt
    
    def extract_response(self, generated_text: str, instruction: str) -> str:
        """Extract continuation from generated text."""
        prompt = self.format_prompt(instruction)
        if generated_text.startswith(prompt):
            response = generated_text[len(prompt):].strip()
        else:
            response = generated_text.strip()
        
        return response


# Pre-defined template configurations
INSTRUCTION_TEMPLATE_CONFIG = PromptConfig(
    system_message="",
    instruction_prefix="### Instruction:\n",
    instruction_suffix="",
    response_prefix="### Response:\n",
    response_suffix="",
    separator="\n\n",
    max_length=512,
    truncate_instruction=True
)

LLAMA2_TEMPLATE_CONFIG = PromptConfig(
    system_message="<s>[INST] <<SYS>>\nYou are a helpful, respectful and honest assistant.\n<</SYS>>\n\n",
    instruction_prefix="",
    instruction_suffix=" [/INST]",
    response_prefix="",
    response_suffix="</s>",
    separator="",
    max_length=512,
    truncate_instruction=True
)

ALPACA_TEMPLATE_CONFIG = PromptConfig(
    system_message="Below is an instruction that describes a task. Write a response that appropriately completes the request.",
    instruction_prefix="### Instruction:\n",
    instruction_suffix="",
    response_prefix="### Response:\n",
    response_suffix="",
    separator="\n\n",
    max_length=512,
    truncate_instruction=True
)

DATABRICKS_TEMPLATE_CONFIG = PromptConfig(
    system_message="",
    instruction_prefix="### Instruction:\n",
    instruction_suffix="",
    response_prefix="### Response:\n",
    response_suffix="### End",
    separator="\n\n",
    max_length=512,
    truncate_instruction=True
)

IMDB_TEMPLATE_CONFIG = PromptConfig(
    system_message="",
    instruction_prefix="",
    instruction_suffix="",
    response_prefix="",
    response_suffix="",
    separator="",
    max_length=256,
    truncate_instruction=True
)


class PromptTemplateFactory:
    """Factory for creating prompt templates."""
    
    _templates = {
        'instruction': (InstructionTemplate, INSTRUCTION_TEMPLATE_CONFIG),
        'llama2': (InstructionTemplate, LLAMA2_TEMPLATE_CONFIG),
        'alpaca': (InstructionTemplate, ALPACA_TEMPLATE_CONFIG),
        'databricks': (InstructionTemplate, DATABRICKS_TEMPLATE_CONFIG),
        'dolly': (InstructionTemplate, DATABRICKS_TEMPLATE_CONFIG),  # Alias
        'imdb': (TextContinuationTemplate, IMDB_TEMPLATE_CONFIG),
        'text_continuation': (TextContinuationTemplate, IMDB_TEMPLATE_CONFIG),
    }
    
    @classmethod
    def create_template(cls, template_name: str, custom_config: Optional[PromptConfig] = None) -> PromptTemplate:
        """Create a prompt template by name."""
        if template_name not in cls._templates:
            available = list(cls._templates.keys())
            raise ValueError(f"Unknown template '{template_name}'. Available: {available}")
        
        template_class, default_config = cls._templates[template_name]
        config = custom_config or default_config
        
        return template_class(config)
    
    @classmethod
    def get_available_templates(cls) -> List[str]:
        """Get list of available template names."""
        return list(cls._templates.keys())


def format_instruction_prompt(instruction: str, template_name: str = 'instruction') -> str:
    """Convenience function to format instruction with specified template."""
    template = PromptTemplateFactory.create_template(template_name)
    return template.format_prompt(instruction)


def format_llama2_prompt(instruction: str) -> str:
    """Format instruction for LLaMA2 chat model."""
    template = PromptTemplateFactory.create_template('llama2')
    return template.format_prompt(instruction)


def format_alpaca_prompt(instruction: str) -> str:
    """Format instruction for Alpaca model."""
    template = PromptTemplateFactory.create_template('alpaca')
    return template.format_prompt(instruction)


def format_dolly_prompt(instruction: str) -> str:
    """Format instruction for Dolly model."""
    template = PromptTemplateFactory.create_template('dolly')
    return template.format_prompt(instruction)


def format_imdb_prompt(text: str) -> str:
    """Format text for IMDb continuation task."""
    template = PromptTemplateFactory.create_template('imdb')
    return template.format_prompt(text)


def extract_response_from_generation(generated_text: str, instruction: str, template_name: str = 'instruction') -> str:
    """Extract response from generated text using specified template."""
    template = PromptTemplateFactory.create_template(template_name)
    return template.extract_response(generated_text, instruction)


class PromptManager:
    """Manager for handling multiple prompt templates and conversions."""
    
    def __init__(self):
        """Initialize prompt manager."""
        self.templates = {}
        self._load_default_templates()
    
    def _load_default_templates(self):
        """Load default templates."""
        for name in PromptTemplateFactory.get_available_templates():
            self.templates[name] = PromptTemplateFactory.create_template(name)
    
    def register_template(self, name: str, template: PromptTemplate):
        """Register a custom template."""
        self.templates[name] = template
    
    def format_prompt(self, instruction: str, template_name: str) -> str:
        """Format prompt using specified template."""
        if template_name not in self.templates:
            raise ValueError(f"Template '{template_name}' not found")
        return self.templates[template_name].format_prompt(instruction)
    
    def extract_response(self, generated_text: str, instruction: str, template_name: str) -> str:
        """Extract response using specified template."""
        if template_name not in self.templates:
            raise ValueError(f"Template '{template_name}' not found")
        return self.templates[template_name].extract_response(generated_text, instruction)
    
    def convert_between_templates(self, text: str, from_template: str, to_template: str, instruction: str) -> str:
        """Convert text from one template format to another."""
        # Extract response using source template
        response = self.extract_response(text, instruction, from_template)
        
        # Format with target template
        return self.format_prompt(response, to_template)
    
    def get_template_info(self, template_name: str) -> Dict[str, Any]:
        """Get information about a template."""
        if template_name not in self.templates:
            raise ValueError(f"Template '{template_name}' not found")
        
        template = self.templates[template_name]
        return {
            'name': template_name,
            'type': type(template).__name__,
            'config': {
                'system_message': template.config.system_message,
                'instruction_prefix': template.config.instruction_prefix,
                'instruction_suffix': template.config.instruction_suffix,
                'response_prefix': template.config.response_prefix,
                'response_suffix': template.config.response_suffix,
                'separator': template.config.separator,
                'max_length': template.config.max_length,
                'truncate_instruction': template.config.truncate_instruction,
            }
        }
    
    def list_templates(self) -> List[str]:
        """List all available templates."""
        return list(self.templates.keys())


# Global prompt manager instance
prompt_manager = PromptManager()


def get_model_specific_template(model_name: str) -> str:
    """Get appropriate template name for a model."""
    model_name_lower = model_name.lower()
    
    if 'llama2' in model_name_lower or 'llama-2' in model_name_lower:
        return 'llama2'
    elif 'alpaca' in model_name_lower:
        return 'alpaca'
    elif 'dolly' in model_name_lower or 'databricks' in model_name_lower:
        return 'dolly'
    elif 'imdb' in model_name_lower:
        return 'imdb'
    else:
        return 'instruction'  # Default


def create_red_team_prompt(base_instruction: str, model_name: str) -> str:
    """Create a red-team prompt for the specified model."""
    template_name = get_model_specific_template(model_name)
    return format_instruction_prompt(base_instruction, template_name)


# Example usage and testing
if __name__ == "__main__":
    # Test different templates
    instruction = "Write a story about a brave knight."
    
    print("=== Template Examples ===")
    
    # Test Alpaca template
    alpaca_prompt = format_alpaca_prompt(instruction)
    print(f"Alpaca:\n{alpaca_prompt}\n")
    
    # Test LLaMA2 template
    llama2_prompt = format_llama2_prompt(instruction)
    print(f"LLaMA2:\n{llama2_prompt}\n")
    
    # Test Dolly template
    dolly_prompt = format_dolly_prompt(instruction)
    print(f"Dolly:\n{dolly_prompt}\n")
    
    # Test IMDb template
    imdb_text = "This movie was absolutely"
    imdb_prompt = format_imdb_prompt(imdb_text)
    print(f"IMDb:\n{imdb_prompt}\n")
    
    # Test prompt manager
    manager = PromptManager()
    print("Available templates:", manager.list_templates())
    
    # Test template info
    alpaca_info = manager.get_template_info('alpaca')
    print(f"Alpaca template info: {alpaca_info}")