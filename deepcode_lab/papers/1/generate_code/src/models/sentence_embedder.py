"""
Sentence Embedder Module for Curiosity-Driven Red-Teaming

This module implements sentence embedding functionality using pre-trained transformer models.
It provides the φ(x) sentence embeddings used in cosine similarity computations for novelty rewards.

Key Components:
- SentenceEmbedder: Main class for computing sentence embeddings
- Supports sentence-transformers/all-MiniLM-L6-v2 model as specified in the paper
- Efficient batch processing and caching capabilities
- Memory-efficient embedding computation for large-scale red-teaming

Usage:
    embedder = SentenceEmbedder()
    embeddings = embedder.embed(["Hello world", "Another sentence"])
    similarity = embedder.compute_similarity(text1, text2)
"""

import torch
import numpy as np
from sentence_transformers import SentenceTransformer
from typing import List, Union, Optional, Dict, Any, Tuple
import logging
import os
import pickle
from dataclasses import dataclass
import warnings
from functools import lru_cache

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class EmbeddingConfig:
    """Configuration for sentence embedding model."""
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    device: str = "auto"  # "auto", "cpu", "cuda"
    batch_size: int = 32
    max_seq_length: int = 512
    normalize_embeddings: bool = True
    cache_embeddings: bool = True
    cache_dir: Optional[str] = None

class SentenceEmbedder:
    """
    Sentence embedding model for computing φ(x) embeddings used in cosine similarity.
    
    This class provides functionality to:
    1. Compute sentence embeddings using pre-trained transformer models
    2. Calculate cosine similarities between sentences
    3. Batch process multiple sentences efficiently
    4. Cache embeddings for repeated computations
    
    The embeddings are used in the novelty reward computation:
    B_Cos(x) = -Σ_{x'∈X} cosine_sim(φ(x), φ(x'))
    """
    
    def __init__(self, config: Optional[EmbeddingConfig] = None):
        """
        Initialize the sentence embedder.
        
        Args:
            config: Configuration for the embedding model
        """
        self.config = config or EmbeddingConfig()
        self.model = None
        self.device = None
        self._embedding_cache = {}
        self._initialize_model()
        
    def _initialize_model(self):
        """Initialize the sentence transformer model."""
        try:
            logger.info(f"Loading sentence transformer model: {self.config.model_name}")
            
            # Determine device
            if self.config.device == "auto":
                self.device = "cuda" if torch.cuda.is_available() else "cpu"
            else:
                self.device = self.config.device
                
            logger.info(f"Using device: {self.device}")
            
            # Load model
            self.model = SentenceTransformer(
                self.config.model_name,
                device=self.device
            )
            
            # Set max sequence length
            if hasattr(self.model, 'max_seq_length'):
                self.model.max_seq_length = self.config.max_seq_length
                
            logger.info("Sentence transformer model loaded successfully")
            
        except Exception as e:
            logger.error(f"Failed to load sentence transformer model: {e}")
            # Fallback to mock implementation for testing
            logger.warning("Using mock sentence embedder for testing")
            self.model = None
            self.device = "cpu"
    
    def embed(self, texts: Union[str, List[str]], 
              use_cache: bool = None,
              show_progress: bool = False) -> Union[np.ndarray, List[np.ndarray]]:
        """
        Compute sentence embeddings φ(x) for input texts.
        
        Args:
            texts: Single text string or list of text strings
            use_cache: Whether to use embedding cache (default: config setting)
            show_progress: Whether to show progress bar for batch processing
            
        Returns:
            Embeddings as numpy array(s)
        """
        if use_cache is None:
            use_cache = self.config.cache_embeddings
            
        # Handle single string input
        single_input = isinstance(texts, str)
        if single_input:
            texts = [texts]
            
        # Check cache for existing embeddings
        embeddings = []
        texts_to_embed = []
        cache_keys = []
        
        for text in texts:
            cache_key = self._get_cache_key(text) if use_cache else None
            if cache_key and cache_key in self._embedding_cache:
                embeddings.append(self._embedding_cache[cache_key])
                cache_keys.append(None)
            else:
                embeddings.append(None)
                texts_to_embed.append(text)
                cache_keys.append(cache_key)
        
        # Compute embeddings for uncached texts
        if texts_to_embed:
            if self.model is not None:
                try:
                    new_embeddings = self._compute_embeddings(
                        texts_to_embed, 
                        show_progress=show_progress
                    )
                except Exception as e:
                    logger.error(f"Error computing embeddings: {e}")
                    new_embeddings = self._mock_embeddings(texts_to_embed)
            else:
                new_embeddings = self._mock_embeddings(texts_to_embed)
            
            # Fill in computed embeddings and update cache
            embed_idx = 0
            for i, embedding in enumerate(embeddings):
                if embedding is None:
                    embeddings[i] = new_embeddings[embed_idx]
                    if cache_keys[i] and use_cache:
                        self._embedding_cache[cache_keys[i]] = new_embeddings[embed_idx]
                    embed_idx += 1
        
        # Convert to numpy arrays
        embeddings = [np.array(emb) for emb in embeddings]
        
        # Return single array for single input
        if single_input:
            return embeddings[0]
        
        return embeddings
    
    def _compute_embeddings(self, texts: List[str], 
                          show_progress: bool = False) -> List[np.ndarray]:
        """Compute embeddings using the sentence transformer model."""
        # Process in batches for memory efficiency
        all_embeddings = []
        batch_size = self.config.batch_size
        
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]
            
            # Compute embeddings for batch
            batch_embeddings = self.model.encode(
                batch_texts,
                convert_to_numpy=True,
                normalize_embeddings=self.config.normalize_embeddings,
                show_progress_bar=show_progress and i == 0  # Show progress only for first batch
            )
            
            all_embeddings.extend(batch_embeddings)
        
        return all_embeddings
    
    def _mock_embeddings(self, texts: List[str]) -> List[np.ndarray]:
        """Generate mock embeddings for testing when model is not available."""
        # Use simple hash-based embeddings for testing
        embeddings = []
        embedding_dim = 384  # Dimension of all-MiniLM-L6-v2
        
        for text in texts:
            # Create deterministic embedding based on text hash
            text_hash = hash(text)
            np.random.seed(abs(text_hash) % (2**32))
            embedding = np.random.normal(0, 1, embedding_dim)
            
            # Normalize if required
            if self.config.normalize_embeddings:
                embedding = embedding / np.linalg.norm(embedding)
                
            embeddings.append(embedding)
        
        return embeddings
    
    def compute_similarity(self, text1: str, text2: str, 
                         similarity_type: str = "cosine") -> float:
        """
        Compute similarity between two texts.
        
        Args:
            text1: First text
            text2: Second text
            similarity_type: Type of similarity ("cosine", "dot", "euclidean")
            
        Returns:
            Similarity score
        """
        emb1 = self.embed(text1)
        emb2 = self.embed(text2)
        
        if similarity_type == "cosine":
            return self._cosine_similarity(emb1, emb2)
        elif similarity_type == "dot":
            return np.dot(emb1, emb2)
        elif similarity_type == "euclidean":
            return -np.linalg.norm(emb1 - emb2)  # Negative for similarity
        else:
            raise ValueError(f"Unknown similarity type: {similarity_type}")
    
    def compute_pairwise_similarities(self, texts: List[str], 
                                    similarity_type: str = "cosine") -> np.ndarray:
        """
        Compute pairwise similarities between all texts.
        
        Args:
            texts: List of texts
            similarity_type: Type of similarity to compute
            
        Returns:
            Similarity matrix of shape (len(texts), len(texts))
        """
        embeddings = self.embed(texts)
        embeddings = np.array(embeddings)
        
        if similarity_type == "cosine":
            # Compute cosine similarity matrix
            similarities = np.dot(embeddings, embeddings.T)
        elif similarity_type == "dot":
            similarities = np.dot(embeddings, embeddings.T)
        elif similarity_type == "euclidean":
            # Compute negative euclidean distance matrix
            from scipy.spatial.distance import cdist
            similarities = -cdist(embeddings, embeddings, metric='euclidean')
        else:
            raise ValueError(f"Unknown similarity type: {similarity_type}")
            
        return similarities
    
    def compute_cosine_novelty_reward(self, text: str, 
                                    history_texts: List[str]) -> float:
        """
        Compute cosine similarity novelty reward B_Cos(x).
        
        This implements the formula:
        B_Cos(x) = -Σ_{x'∈X} cosine_sim(φ(x), φ(x'))
        
        Args:
            text: Input text x
            history_texts: History of texts X
            
        Returns:
            Cosine novelty reward (negative sum of similarities)
        """
        if not history_texts:
            return 0.0
            
        # Get embedding for input text
        text_embedding = self.embed(text)
        
        # Get embeddings for history texts
        history_embeddings = self.embed(history_texts)
        
        # Compute cosine similarities
        similarities = []
        for hist_emb in history_embeddings:
            similarity = self._cosine_similarity(text_embedding, hist_emb)
            similarities.append(similarity)
        
        # Return negative sum (novelty reward)
        novelty_reward = -sum(similarities)
        return novelty_reward
    
    def _cosine_similarity(self, emb1: np.ndarray, emb2: np.ndarray) -> float:
        """Compute cosine similarity between two embeddings."""
        # Handle zero vectors
        norm1 = np.linalg.norm(emb1)
        norm2 = np.linalg.norm(emb2)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
            
        return np.dot(emb1, emb2) / (norm1 * norm2)
    
    def _get_cache_key(self, text: str) -> str:
        """Generate cache key for text."""
        return f"{hash(text)}_{self.config.model_name}"
    
    def clear_cache(self):
        """Clear the embedding cache."""
        self._embedding_cache.clear()
        logger.info("Embedding cache cleared")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get statistics about the embedding cache."""
        return {
            "cache_size": len(self._embedding_cache),
            "cache_enabled": self.config.cache_embeddings,
            "model_name": self.config.model_name,
            "device": self.device
        }
    
    def save_cache(self, filepath: str):
        """Save embedding cache to file."""
        try:
            with open(filepath, 'wb') as f:
                pickle.dump(self._embedding_cache, f)
            logger.info(f"Embedding cache saved to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")
    
    def load_cache(self, filepath: str):
        """Load embedding cache from file."""
        try:
            if os.path.exists(filepath):
                with open(filepath, 'rb') as f:
                    self._embedding_cache = pickle.load(f)
                logger.info(f"Embedding cache loaded from {filepath}")
            else:
                logger.warning(f"Cache file not found: {filepath}")
        except Exception as e:
            logger.error(f"Failed to load cache: {e}")
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the loaded model."""
        info = {
            "model_name": self.config.model_name,
            "device": self.device,
            "embedding_dimension": None,
            "max_seq_length": self.config.max_seq_length,
            "normalize_embeddings": self.config.normalize_embeddings,
            "model_loaded": self.model is not None
        }
        
        if self.model is not None:
            try:
                # Get embedding dimension by encoding a test sentence
                test_embedding = self.model.encode(["test"], convert_to_numpy=True)
                info["embedding_dimension"] = test_embedding.shape[1]
            except Exception as e:
                logger.warning(f"Could not determine embedding dimension: {e}")
                info["embedding_dimension"] = 384  # Default for all-MiniLM-L6-v2
        else:
            info["embedding_dimension"] = 384  # Mock dimension
            
        return info

class MockSentenceEmbedder:
    """
    Mock sentence embedder for testing when sentence-transformers is not available.
    
    This provides the same interface as SentenceEmbedder but uses simple
    hash-based embeddings for testing purposes.
    """
    
    def __init__(self, embedding_dim: int = 384):
        """
        Initialize mock embedder.
        
        Args:
            embedding_dim: Dimension of mock embeddings
        """
        self.embedding_dim = embedding_dim
        self.device = "cpu"
        self._cache = {}
        
    def embed(self, texts: Union[str, List[str]], **kwargs) -> Union[np.ndarray, List[np.ndarray]]:
        """Generate mock embeddings."""
        single_input = isinstance(texts, str)
        if single_input:
            texts = [texts]
            
        embeddings = []
        for text in texts:
            if text in self._cache:
                embeddings.append(self._cache[text])
            else:
                # Generate deterministic embedding
                text_hash = hash(text)
                np.random.seed(abs(text_hash) % (2**32))
                embedding = np.random.normal(0, 1, self.embedding_dim)
                embedding = embedding / np.linalg.norm(embedding)  # Normalize
                self._cache[text] = embedding
                embeddings.append(embedding)
        
        if single_input:
            return embeddings[0]
        return embeddings
    
    def compute_similarity(self, text1: str, text2: str, similarity_type: str = "cosine") -> float:
        """Compute mock similarity."""
        emb1 = self.embed(text1)
        emb2 = self.embed(text2)
        
        if similarity_type == "cosine":
            return np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2))
        elif similarity_type == "dot":
            return np.dot(emb1, emb2)
        else:
            return -np.linalg.norm(emb1 - emb2)
    
    def compute_cosine_novelty_reward(self, text: str, history_texts: List[str]) -> float:
        """Compute mock cosine novelty reward."""
        if not history_texts:
            return 0.0
            
        text_emb = self.embed(text)
        similarities = []
        
        for hist_text in history_texts:
            hist_emb = self.embed(hist_text)
            sim = np.dot(text_emb, hist_emb) / (np.linalg.norm(text_emb) * np.linalg.norm(hist_emb))
            similarities.append(sim)
            
        return -sum(similarities)
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get mock model info."""
        return {
            "model_name": "mock-sentence-embedder",
            "device": "cpu",
            "embedding_dimension": self.embedding_dim,
            "model_loaded": True,
            "is_mock": True
        }

# Factory function for creating embedders
def create_sentence_embedder(config: Optional[EmbeddingConfig] = None, 
                           use_mock: bool = False) -> Union[SentenceEmbedder, MockSentenceEmbedder]:
    """
    Factory function to create sentence embedder.
    
    Args:
        config: Configuration for the embedder
        use_mock: Whether to use mock embedder for testing
        
    Returns:
        Sentence embedder instance
    """
    if use_mock:
        return MockSentenceEmbedder()
    else:
        return SentenceEmbedder(config)

# Example usage and testing
if __name__ == "__main__":
    # Test the sentence embedder
    print("Testing Sentence Embedder...")
    
    # Test with mock embedder first
    print("\n1. Testing Mock Embedder:")
    mock_embedder = MockSentenceEmbedder()
    
    test_texts = [
        "Hello world",
        "This is a test sentence",
        "Another example text"
    ]
    
    # Test embedding
    embeddings = mock_embedder.embed(test_texts)
    print(f"Generated {len(embeddings)} embeddings of dimension {embeddings[0].shape[0]}")
    
    # Test similarity
    similarity = mock_embedder.compute_similarity(test_texts[0], test_texts[1])
    print(f"Similarity between first two texts: {similarity:.4f}")
    
    # Test novelty reward
    novelty = mock_embedder.compute_cosine_novelty_reward(test_texts[0], test_texts[1:])
    print(f"Novelty reward: {novelty:.4f}")
    
    # Test real embedder (if available)
    print("\n2. Testing Real Embedder:")
    try:
        real_embedder = SentenceEmbedder()
        
        # Test single embedding
        single_emb = real_embedder.embed("Test sentence")
        print(f"Single embedding shape: {single_emb.shape}")
        
        # Test batch embedding
        batch_embs = real_embedder.embed(test_texts)
        print(f"Batch embeddings: {len(batch_embs)} embeddings")
        
        # Test model info
        info = real_embedder.get_model_info()
        print(f"Model info: {info}")
        
        print("Real embedder test completed successfully!")
        
    except Exception as e:
        print(f"Real embedder test failed (expected in some environments): {e}")
        print("This is normal if sentence-transformers is not installed")
    
    print("\nSentence Embedder implementation completed!")