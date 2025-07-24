"""
Data loading and preprocessing for Curiosity-Driven Red-Teaming.

This module provides data loaders for different datasets used in the experiments:
- IMDb dataset (text continuation task)
- Stanford Alpaca dataset (instruction following)
- Databricks Dolly-15K dataset (instruction following)

Classes:
    BaseDataLoader: Abstract base class for all data loaders
    IMDbDataLoader: Loads and preprocesses IMDb movie review data
    AlpacaDataLoader: Loads Stanford Alpaca instruction dataset
    DollyDataLoader: Loads Databricks Dolly-15K instruction dataset
    DataLoaderFactory: Factory for creating appropriate data loaders
"""

import os
import json
import logging
import random
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple, Union
from pathlib import Path
import pandas as pd

try:
    from datasets import load_dataset, Dataset
    DATASETS_AVAILABLE = True
except ImportError:
    DATASETS_AVAILABLE = False
    logging.warning("datasets library not available. Some functionality may be limited.")

logger = logging.getLogger(__name__)


class BaseDataLoader(ABC):
    """Abstract base class for all data loaders."""
    
    def __init__(self, data_path: Optional[str] = None, cache_dir: Optional[str] = None):
        """
        Initialize the data loader.
        
        Args:
            data_path: Path to the dataset files
            cache_dir: Directory for caching processed data
        """
        self.data_path = data_path
        self.cache_dir = cache_dir or "data/cache"
        self.dataset = None
        self._setup_cache_dir()
    
    def _setup_cache_dir(self):
        """Create cache directory if it doesn't exist."""
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
    
    @abstractmethod
    def load_dataset(self) -> Dataset:
        """Load the raw dataset."""
        pass
    
    @abstractmethod
    def preprocess(self, dataset: Dataset) -> List[str]:
        """Preprocess the dataset into prompts/instructions."""
        pass
    
    @abstractmethod
    def get_task_type(self) -> str:
        """Return the task type (e.g., 'text_continuation', 'instruction_following')."""
        pass
    
    def load_prompts(self, split: str = "train", max_samples: Optional[int] = None) -> List[str]:
        """
        Load and preprocess prompts for the specified split.
        
        Args:
            split: Dataset split ('train', 'validation', 'test')
            max_samples: Maximum number of samples to load
            
        Returns:
            List of preprocessed prompts/instructions
        """
        cache_file = Path(self.cache_dir) / f"{self.__class__.__name__}_{split}_{max_samples}.json"
        
        # Try to load from cache
        if cache_file.exists():
            logger.info(f"Loading cached data from {cache_file}")
            with open(cache_file, 'r') as f:
                return json.load(f)
        
        # Load and preprocess data
        logger.info(f"Loading {split} split for {self.__class__.__name__}")
        dataset = self.load_dataset()
        
        if split in dataset:
            data = dataset[split]
        else:
            # If split doesn't exist, use the entire dataset
            logger.warning(f"Split '{split}' not found, using entire dataset")
            data = dataset
        
        prompts = self.preprocess(data)
        
        # Limit samples if specified
        if max_samples and len(prompts) > max_samples:
            random.shuffle(prompts)
            prompts = prompts[:max_samples]
        
        # Cache the processed data
        with open(cache_file, 'w') as f:
            json.dump(prompts, f, indent=2)
        
        logger.info(f"Loaded {len(prompts)} prompts for {split} split")
        return prompts
    
    def get_validation_split(self, train_prompts: List[str], 
                           validation_ratio: float = 0.1) -> Tuple[List[str], List[str]]:
        """
        Split training data into train and validation sets.
        
        Args:
            train_prompts: List of training prompts
            validation_ratio: Fraction of data to use for validation
            
        Returns:
            Tuple of (train_prompts, validation_prompts)
        """
        random.shuffle(train_prompts)
        split_idx = int(len(train_prompts) * (1 - validation_ratio))
        return train_prompts[:split_idx], train_prompts[split_idx:]


class IMDbDataLoader(BaseDataLoader):
    """Data loader for IMDb movie review dataset (text continuation task)."""
    
    def __init__(self, data_path: Optional[str] = None, cache_dir: Optional[str] = None,
                 truncate_length: int = 4):
        """
        Initialize IMDb data loader.
        
        Args:
            data_path: Path to IMDb dataset
            cache_dir: Cache directory
            truncate_length: Number of words to keep from each review (for prompting)
        """
        super().__init__(data_path, cache_dir)
        self.truncate_length = truncate_length
    
    def load_dataset(self) -> Dataset:
        """Load IMDb dataset from HuggingFace datasets."""
        if not DATASETS_AVAILABLE:
            raise ImportError("datasets library required for IMDb data loading")
        
        if self.data_path:
            # Load from local path
            dataset = load_dataset("json", data_files=self.data_path)
        else:
            # Load from HuggingFace hub
            dataset = load_dataset("imdb")
        
        self.dataset = dataset
        return dataset
    
    def preprocess(self, dataset: Dataset) -> List[str]:
        """
        Preprocess IMDb reviews into truncated prompts.
        
        The paper uses the first 4 words of each review as prompts for text continuation.
        
        Args:
            dataset: Raw IMDb dataset
            
        Returns:
            List of truncated review prompts
        """
        prompts = []
        
        for example in dataset:
            text = example["text"]
            # Tokenize and truncate to first N words
            words = text.split()[:self.truncate_length]
            prompt = " ".join(words)
            
            # Only include prompts with sufficient length
            if len(words) == self.truncate_length:
                prompts.append(prompt)
        
        return prompts
    
    def get_task_type(self) -> str:
        """Return task type."""
        return "text_continuation"


class AlpacaDataLoader(BaseDataLoader):
    """Data loader for Stanford Alpaca instruction dataset."""
    
    def __init__(self, data_path: Optional[str] = None, cache_dir: Optional[str] = None):
        """
        Initialize Alpaca data loader.
        
        Args:
            data_path: Path to Alpaca dataset
            cache_dir: Cache directory
        """
        super().__init__(data_path, cache_dir)
    
    def load_dataset(self) -> Dataset:
        """Load Alpaca dataset."""
        if self.data_path:
            # Load from local JSON file
            with open(self.data_path, 'r') as f:
                data = json.load(f)
            dataset = Dataset.from_list(data)
        else:
            # Try to load from HuggingFace hub
            if DATASETS_AVAILABLE:
                try:
                    dataset = load_dataset("tatsu-lab/alpaca")
                except:
                    # Fallback: create sample data
                    logger.warning("Could not load Alpaca dataset, creating sample data")
                    dataset = self._create_sample_alpaca_data()
            else:
                dataset = self._create_sample_alpaca_data()
        
        self.dataset = dataset
        return dataset
    
    def _create_sample_alpaca_data(self) -> Dataset:
        """Create sample Alpaca-style instruction data for testing."""
        sample_data = [
            {
                "instruction": "Write a creative story about a robot learning to paint.",
                "input": "",
                "output": "Once upon a time, there was a robot named Artie..."
            },
            {
                "instruction": "Explain the concept of machine learning to a 5-year-old.",
                "input": "",
                "output": "Machine learning is like teaching a computer to recognize patterns..."
            },
            {
                "instruction": "Translate the following English text to French.",
                "input": "Hello, how are you today?",
                "output": "Bonjour, comment allez-vous aujourd'hui?"
            },
            {
                "instruction": "Write a poem about the ocean.",
                "input": "",
                "output": "Vast and blue, the ocean calls..."
            },
            {
                "instruction": "Summarize the main points of climate change.",
                "input": "",
                "output": "Climate change refers to long-term shifts in global temperatures..."
            }
        ] * 100  # Repeat to create more samples
        
        return Dataset.from_list(sample_data)
    
    def preprocess(self, dataset: Dataset) -> List[str]:
        """
        Preprocess Alpaca dataset into instruction prompts.
        
        Args:
            dataset: Raw Alpaca dataset
            
        Returns:
            List of instruction prompts
        """
        prompts = []
        
        for example in dataset:
            instruction = example["instruction"]
            input_text = example.get("input", "")
            
            # Format instruction with input if available
            if input_text.strip():
                prompt = f"{instruction}\n\nInput: {input_text}"
            else:
                prompt = instruction
            
            prompts.append(prompt)
        
        return prompts
    
    def get_task_type(self) -> str:
        """Return task type."""
        return "instruction_following"


class DollyDataLoader(BaseDataLoader):
    """Data loader for Databricks Dolly-15K instruction dataset."""
    
    def __init__(self, data_path: Optional[str] = None, cache_dir: Optional[str] = None):
        """
        Initialize Dolly data loader.
        
        Args:
            data_path: Path to Dolly dataset
            cache_dir: Cache directory
        """
        super().__init__(data_path, cache_dir)
    
    def load_dataset(self) -> Dataset:
        """Load Dolly dataset."""
        if self.data_path:
            # Load from local file
            if self.data_path.endswith('.json'):
                with open(self.data_path, 'r') as f:
                    data = json.load(f)
                dataset = Dataset.from_list(data)
            else:
                # Assume CSV format
                df = pd.read_csv(self.data_path)
                dataset = Dataset.from_pandas(df)
        else:
            # Try to load from HuggingFace hub
            if DATASETS_AVAILABLE:
                try:
                    dataset = load_dataset("databricks/databricks-dolly-15k")
                except:
                    logger.warning("Could not load Dolly dataset, creating sample data")
                    dataset = self._create_sample_dolly_data()
            else:
                dataset = self._create_sample_dolly_data()
        
        self.dataset = dataset
        return dataset
    
    def _create_sample_dolly_data(self) -> Dataset:
        """Create sample Dolly-style instruction data for testing."""
        sample_data = [
            {
                "instruction": "What are the benefits of regular exercise?",
                "context": "",
                "response": "Regular exercise provides numerous health benefits...",
                "category": "open_qa"
            },
            {
                "instruction": "Write a short story about a time traveler.",
                "context": "",
                "response": "Sarah stepped into the time machine, her heart racing...",
                "category": "creative_writing"
            },
            {
                "instruction": "Explain how photosynthesis works.",
                "context": "",
                "response": "Photosynthesis is the process by which plants convert light energy...",
                "category": "information_extraction"
            },
            {
                "instruction": "Classify the following animals as mammals or reptiles.",
                "context": "Dog, Snake, Cat, Lizard, Horse",
                "response": "Mammals: Dog, Cat, Horse. Reptiles: Snake, Lizard.",
                "category": "classification"
            },
            {
                "instruction": "Summarize the plot of Romeo and Juliet.",
                "context": "",
                "response": "Romeo and Juliet is a tragedy about two young lovers...",
                "category": "summarization"
            }
        ] * 100  # Repeat to create more samples
        
        return Dataset.from_list(sample_data)
    
    def preprocess(self, dataset: Dataset) -> List[str]:
        """
        Preprocess Dolly dataset into instruction prompts.
        
        Args:
            dataset: Raw Dolly dataset
            
        Returns:
            List of instruction prompts
        """
        prompts = []
        
        for example in dataset:
            instruction = example["instruction"]
            context = example.get("context", "")
            
            # Format instruction with context if available
            if context and context.strip():
                prompt = f"{instruction}\n\nContext: {context}"
            else:
                prompt = instruction
            
            prompts.append(prompt)
        
        return prompts
    
    def get_task_type(self) -> str:
        """Return task type."""
        return "instruction_following"


class DataLoaderFactory:
    """Factory for creating appropriate data loaders."""
    
    _loaders = {
        "imdb": IMDbDataLoader,
        "alpaca": AlpacaDataLoader,
        "dolly": DollyDataLoader,
    }
    
    @classmethod
    def create_loader(cls, dataset_name: str, **kwargs) -> BaseDataLoader:
        """
        Create a data loader for the specified dataset.
        
        Args:
            dataset_name: Name of the dataset ('imdb', 'alpaca', 'dolly')
            **kwargs: Additional arguments for the data loader
            
        Returns:
            Appropriate data loader instance
            
        Raises:
            ValueError: If dataset_name is not supported
        """
        if dataset_name.lower() not in cls._loaders:
            raise ValueError(f"Unsupported dataset: {dataset_name}. "
                           f"Supported datasets: {list(cls._loaders.keys())}")
        
        loader_class = cls._loaders[dataset_name.lower()]
        return loader_class(**kwargs)
    
    @classmethod
    def get_supported_datasets(cls) -> List[str]:
        """Get list of supported dataset names."""
        return list(cls._loaders.keys())


def load_dataset_for_task(task_type: str, dataset_name: str, 
                         split: str = "train", max_samples: Optional[int] = None,
                         **loader_kwargs) -> Tuple[List[str], str]:
    """
    Convenience function to load dataset for a specific task.
    
    Args:
        task_type: Type of task ('text_continuation', 'instruction_following')
        dataset_name: Name of the dataset
        split: Dataset split to load
        max_samples: Maximum number of samples
        **loader_kwargs: Additional arguments for the data loader
        
    Returns:
        Tuple of (prompts, actual_task_type)
        
    Raises:
        ValueError: If task_type and dataset don't match
    """
    loader = DataLoaderFactory.create_loader(dataset_name, **loader_kwargs)
    
    # Verify task type compatibility
    actual_task_type = loader.get_task_type()
    if task_type != actual_task_type:
        logger.warning(f"Requested task type '{task_type}' doesn't match "
                      f"dataset task type '{actual_task_type}'. Using dataset task type.")
    
    prompts = loader.load_prompts(split=split, max_samples=max_samples)
    return prompts, actual_task_type


# Example usage and testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test IMDb loader
    print("Testing IMDb loader...")
    imdb_loader = IMDbDataLoader()
    try:
        imdb_prompts = imdb_loader.load_prompts(split="train", max_samples=10)
        print(f"Loaded {len(imdb_prompts)} IMDb prompts")
        print("Sample prompts:", imdb_prompts[:3])
    except Exception as e:
        print(f"IMDb loader error: {e}")
    
    # Test Alpaca loader
    print("\nTesting Alpaca loader...")
    alpaca_loader = AlpacaDataLoader()
    try:
        alpaca_prompts = alpaca_loader.load_prompts(split="train", max_samples=10)
        print(f"Loaded {len(alpaca_prompts)} Alpaca prompts")
        print("Sample prompts:", alpaca_prompts[:3])
    except Exception as e:
        print(f"Alpaca loader error: {e}")
    
    # Test Dolly loader
    print("\nTesting Dolly loader...")
    dolly_loader = DollyDataLoader()
    try:
        dolly_prompts = dolly_loader.load_prompts(split="train", max_samples=10)
        print(f"Loaded {len(dolly_prompts)} Dolly prompts")
        print("Sample prompts:", dolly_prompts[:3])
    except Exception as e:
        print(f"Dolly loader error: {e}")
    
    # Test factory
    print("\nTesting DataLoaderFactory...")
    try:
        factory_loader = DataLoaderFactory.create_loader("alpaca")
        print(f"Factory created loader: {type(factory_loader).__name__}")
        print(f"Supported datasets: {DataLoaderFactory.get_supported_datasets()}")
    except Exception as e:
        print(f"Factory error: {e}")