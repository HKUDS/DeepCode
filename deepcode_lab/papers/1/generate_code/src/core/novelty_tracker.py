"""
Novelty Tracker for Curiosity-Driven Red-Teaming

This module implements the NoveltyTracker class that maintains history X for novelty computation
and provides methods to compute novelty rewards based on SelfBLEU and cosine similarity.

Classes:
    NoveltyTracker: Tracks generated test cases and computes novelty rewards
"""

import numpy as np
import torch
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
import logging
from dataclasses import dataclass
import pickle
import os

# Import for text processing utilities
try:
    from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
    from nltk.tokenize import word_tokenize
    import nltk
    # Download required NLTK data
    try:
        nltk.data.find('tokenizers/punkt')
    except LookupError:
        nltk.download('punkt')
except ImportError:
    logging.warning("NLTK not available. SelfBLEU computation will use basic tokenization.")
    sentence_bleu = None
    word_tokenize = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    logging.warning("sentence-transformers not available. Cosine similarity computation will be disabled.")
    SentenceTransformer = None


@dataclass
class NoveltyConfig:
    """Configuration for novelty tracking and computation."""
    max_history_size: int = 10000  # Maximum number of test cases to keep in history
    selfbleu_ngrams: List[int] = None  # N-gram sizes for SelfBLEU computation
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"  # Model for sentence embeddings
    memory_efficient: bool = True  # Whether to use memory-efficient tracking
    save_history: bool = True  # Whether to save history to disk
    history_save_path: str = "data/novelty_history.pkl"  # Path to save history
    
    def __post_init__(self):
        if self.selfbleu_ngrams is None:
            self.selfbleu_ngrams = [2, 3, 4, 5]  # Default n-gram sizes from paper


class NoveltyTracker:
    """
    Maintains history X for novelty computation and provides methods to compute
    novelty rewards based on SelfBLEU and cosine similarity.
    
    This class implements the novelty tracking mechanism described in the paper,
    maintaining a history of generated test cases and computing novelty rewards
    using both SelfBLEU and cosine similarity metrics.
    """
    
    def __init__(self, config: Optional[NoveltyConfig] = None):
        """
        Initialize the NoveltyTracker.
        
        Args:
            config: Configuration for novelty tracking
        """
        self.config = config or NoveltyConfig()
        self.history: List[str] = []  # History X of generated test cases
        self.embeddings_cache: Dict[str, np.ndarray] = {}  # Cache for sentence embeddings
        
        # Initialize sentence embedder if available
        self.sentence_embedder = None
        if SentenceTransformer is not None:
            try:
                self.sentence_embedder = SentenceTransformer(self.config.embedding_model)
                logging.info(f"Loaded sentence embedder: {self.config.embedding_model}")
            except Exception as e:
                logging.warning(f"Failed to load sentence embedder: {e}")
        
        # Load existing history if available
        if self.config.save_history and os.path.exists(self.config.history_save_path):
            self.load_history()
        
        logging.info(f"NoveltyTracker initialized with {len(self.history)} existing test cases")
    
    def add_sentence(self, sentence: str) -> None:
        """
        Add a sentence to the history X.
        
        Args:
            sentence: Test case to add to history
        """
        if sentence not in self.history:  # Avoid duplicates
            self.history.append(sentence)
            
            # Memory management: remove oldest entries if history is too large
            if self.config.memory_efficient and len(self.history) > self.config.max_history_size:
                # Remove oldest 10% of entries
                remove_count = self.config.max_history_size // 10
                self.history = self.history[remove_count:]
                
                # Clear corresponding embeddings cache
                self._cleanup_embeddings_cache()
            
            # Save history periodically
            if self.config.save_history and len(self.history) % 100 == 0:
                self.save_history()
    
    def add_sentences(self, sentences: List[str]) -> None:
        """
        Add multiple sentences to the history.
        
        Args:
            sentences: List of test cases to add to history
        """
        for sentence in sentences:
            self.add_sentence(sentence)
    
    def compute_selfbleu_novelty(self, sentence: str) -> float:
        """
        Compute SelfBLEU novelty reward for a sentence.
        
        Implements Equation 3 from the paper:
        B_SelfBLEU(x) = -Σ_{n=2}^5 SelfBLEU_X(x,n)
        
        Args:
            sentence: Test case to compute novelty for
            
        Returns:
            SelfBLEU novelty reward (higher = more novel)
        """
        if len(self.history) == 0:
            return 0.0  # No history to compare against
        
        if sentence_bleu is None:
            # Fallback to simple token overlap if NLTK not available
            return self._compute_simple_novelty(sentence)
        
        try:
            # Tokenize the input sentence
            if word_tokenize is not None:
                candidate_tokens = word_tokenize(sentence.lower())
            else:
                candidate_tokens = sentence.lower().split()
            
            total_selfbleu = 0.0
            smoothing_function = SmoothingFunction().method1
            
            # Compute SelfBLEU for each n-gram size
            for n in self.config.selfbleu_ngrams:
                if len(candidate_tokens) < n:
                    continue
                
                bleu_scores = []
                for reference_sentence in self.history:
                    if word_tokenize is not None:
                        reference_tokens = word_tokenize(reference_sentence.lower())
                    else:
                        reference_tokens = reference_sentence.lower().split()
                    
                    if len(reference_tokens) >= n:
                        # Compute BLEU score with specific n-gram weights
                        weights = [0.0] * 4
                        if n <= 4:
                            weights[n-1] = 1.0
                        else:
                            # For n=5, use uniform weights for 1-4 grams
                            weights = [0.25, 0.25, 0.25, 0.25]
                        
                        bleu_score = sentence_bleu(
                            [reference_tokens], 
                            candidate_tokens,
                            weights=weights,
                            smoothing_function=smoothing_function
                        )
                        bleu_scores.append(bleu_score)
                
                if bleu_scores:
                    avg_bleu = np.mean(bleu_scores)
                    total_selfbleu += avg_bleu
            
            # Return negative SelfBLEU as novelty reward (lower BLEU = higher novelty)
            novelty_reward = -total_selfbleu
            return novelty_reward
            
        except Exception as e:
            logging.warning(f"Error computing SelfBLEU novelty: {e}")
            return self._compute_simple_novelty(sentence)
    
    def compute_cosine_novelty(self, sentence: str) -> float:
        """
        Compute cosine similarity novelty reward for a sentence.
        
        Implements Equation 4 from the paper:
        B_Cos(x) = -Σ_{x'∈X} cosine_sim(φ(x), φ(x'))
        
        Args:
            sentence: Test case to compute novelty for
            
        Returns:
            Cosine similarity novelty reward (higher = more novel)
        """
        if len(self.history) == 0:
            return 0.0  # No history to compare against
        
        if self.sentence_embedder is None:
            logging.warning("Sentence embedder not available. Using SelfBLEU as fallback.")
            return self.compute_selfbleu_novelty(sentence)
        
        try:
            # Get embedding for the input sentence
            sentence_embedding = self._get_embedding(sentence)
            
            # Compute cosine similarities with all sentences in history
            total_similarity = 0.0
            for reference_sentence in self.history:
                reference_embedding = self._get_embedding(reference_sentence)
                
                # Compute cosine similarity
                cosine_sim = np.dot(sentence_embedding, reference_embedding) / (
                    np.linalg.norm(sentence_embedding) * np.linalg.norm(reference_embedding)
                )
                total_similarity += cosine_sim
            
            # Return negative total similarity as novelty reward
            novelty_reward = -total_similarity
            return novelty_reward
            
        except Exception as e:
            logging.warning(f"Error computing cosine novelty: {e}")
            return self.compute_selfbleu_novelty(sentence)
    
    def compute_combined_novelty(self, sentence: str, selfbleu_weight: float = 1.0, 
                                cosine_weight: float = 1.0) -> Tuple[float, Dict[str, float]]:
        """
        Compute combined novelty reward using both SelfBLEU and cosine similarity.
        
        Args:
            sentence: Test case to compute novelty for
            selfbleu_weight: Weight for SelfBLEU component
            cosine_weight: Weight for cosine similarity component
            
        Returns:
            Tuple of (combined_novelty_reward, component_breakdown)
        """
        selfbleu_novelty = self.compute_selfbleu_novelty(sentence)
        cosine_novelty = self.compute_cosine_novelty(sentence)
        
        combined_novelty = (selfbleu_weight * selfbleu_novelty + 
                           cosine_weight * cosine_novelty)
        
        breakdown = {
            'selfbleu_novelty': selfbleu_novelty,
            'cosine_novelty': cosine_novelty,
            'combined_novelty': combined_novelty,
            'selfbleu_weight': selfbleu_weight,
            'cosine_weight': cosine_weight
        }
        
        return combined_novelty, breakdown
    
    def get_history_stats(self) -> Dict[str, any]:
        """
        Get statistics about the current history.
        
        Returns:
            Dictionary with history statistics
        """
        stats = {
            'history_size': len(self.history),
            'max_history_size': self.config.max_history_size,
            'memory_usage_ratio': len(self.history) / self.config.max_history_size,
            'embeddings_cached': len(self.embeddings_cache),
            'has_sentence_embedder': self.sentence_embedder is not None,
            'embedding_model': self.config.embedding_model
        }
        
        if len(self.history) > 0:
            # Compute average sentence length
            avg_length = np.mean([len(sentence.split()) for sentence in self.history])
            stats['avg_sentence_length'] = avg_length
            
            # Sample of recent sentences
            stats['recent_samples'] = self.history[-5:] if len(self.history) >= 5 else self.history
        
        return stats
    
    def clear_history(self) -> None:
        """Clear the history and embeddings cache."""
        self.history.clear()
        self.embeddings_cache.clear()
        logging.info("NoveltyTracker history cleared")
    
    def save_history(self) -> None:
        """Save the current history to disk."""
        if not self.config.save_history:
            return
        
        try:
            os.makedirs(os.path.dirname(self.config.history_save_path), exist_ok=True)
            
            save_data = {
                'history': self.history,
                'config': self.config,
                'embeddings_cache': self.embeddings_cache
            }
            
            with open(self.config.history_save_path, 'wb') as f:
                pickle.dump(save_data, f)
            
            logging.info(f"Saved history with {len(self.history)} sentences to {self.config.history_save_path}")
            
        except Exception as e:
            logging.error(f"Failed to save history: {e}")
    
    def load_history(self) -> None:
        """Load history from disk."""
        try:
            with open(self.config.history_save_path, 'rb') as f:
                save_data = pickle.load(f)
            
            self.history = save_data.get('history', [])
            self.embeddings_cache = save_data.get('embeddings_cache', {})
            
            logging.info(f"Loaded history with {len(self.history)} sentences from {self.config.history_save_path}")
            
        except Exception as e:
            logging.warning(f"Failed to load history: {e}")
            self.history = []
            self.embeddings_cache = {}
    
    def _get_embedding(self, sentence: str) -> np.ndarray:
        """
        Get sentence embedding with caching.
        
        Args:
            sentence: Sentence to embed
            
        Returns:
            Sentence embedding as numpy array
        """
        if sentence in self.embeddings_cache:
            return self.embeddings_cache[sentence]
        
        if self.sentence_embedder is None:
            # Fallback to simple bag-of-words representation
            words = sentence.lower().split()
            vocab = set()
            for hist_sentence in self.history:
                vocab.update(hist_sentence.lower().split())
            vocab.update(words)
            vocab = sorted(list(vocab))
            
            # Create simple bag-of-words vector
            embedding = np.zeros(len(vocab))
            for word in words:
                if word in vocab:
                    embedding[vocab.index(word)] = 1.0
            
            # Normalize
            if np.linalg.norm(embedding) > 0:
                embedding = embedding / np.linalg.norm(embedding)
        else:
            # Use sentence transformer
            embedding = self.sentence_embedder.encode(sentence, convert_to_numpy=True)
        
        # Cache the embedding
        self.embeddings_cache[sentence] = embedding
        return embedding
    
    def _compute_simple_novelty(self, sentence: str) -> float:
        """
        Compute simple novelty based on token overlap (fallback method).
        
        Args:
            sentence: Test case to compute novelty for
            
        Returns:
            Simple novelty score
        """
        if len(self.history) == 0:
            return 0.0
        
        sentence_tokens = set(sentence.lower().split())
        
        total_overlap = 0.0
        for reference_sentence in self.history:
            reference_tokens = set(reference_sentence.lower().split())
            
            if len(sentence_tokens) > 0 and len(reference_tokens) > 0:
                overlap = len(sentence_tokens.intersection(reference_tokens))
                jaccard_sim = overlap / len(sentence_tokens.union(reference_tokens))
                total_overlap += jaccard_sim
        
        # Return negative overlap as novelty reward
        novelty_reward = -total_overlap / len(self.history)
        return novelty_reward
    
    def _cleanup_embeddings_cache(self) -> None:
        """Clean up embeddings cache to remove entries for sentences no longer in history."""
        if not self.config.memory_efficient:
            return
        
        history_set = set(self.history)
        keys_to_remove = [key for key in self.embeddings_cache.keys() if key not in history_set]
        
        for key in keys_to_remove:
            del self.embeddings_cache[key]
        
        logging.debug(f"Cleaned up {len(keys_to_remove)} embeddings from cache")


# Utility functions for external use
def create_novelty_tracker(max_history_size: int = 10000, 
                          embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2") -> NoveltyTracker:
    """
    Create a NoveltyTracker with common configuration.
    
    Args:
        max_history_size: Maximum number of test cases to keep in history
        embedding_model: Model name for sentence embeddings
        
    Returns:
        Configured NoveltyTracker instance
    """
    config = NoveltyConfig(
        max_history_size=max_history_size,
        embedding_model=embedding_model
    )
    return NoveltyTracker(config)


def compute_batch_novelty(sentences: List[str], tracker: NoveltyTracker, 
                         method: str = "combined") -> List[float]:
    """
    Compute novelty rewards for a batch of sentences.
    
    Args:
        sentences: List of test cases to compute novelty for
        tracker: NoveltyTracker instance
        method: Novelty computation method ("selfbleu", "cosine", or "combined")
        
    Returns:
        List of novelty rewards
    """
    novelty_rewards = []
    
    for sentence in sentences:
        if method == "selfbleu":
            novelty = tracker.compute_selfbleu_novelty(sentence)
        elif method == "cosine":
            novelty = tracker.compute_cosine_novelty(sentence)
        elif method == "combined":
            novelty, _ = tracker.compute_combined_novelty(sentence)
        else:
            raise ValueError(f"Unknown novelty method: {method}")
        
        novelty_rewards.append(novelty)
    
    return novelty_rewards