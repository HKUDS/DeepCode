"""
Text processing utilities for the Curiosity-Driven Red-Teaming system.

This module provides functions for text similarity computation, preprocessing,
and BLEU score calculation used throughout the CRT system.
"""

import re
import string
import logging
from typing import List, Dict, Any, Optional, Tuple, Set
from collections import Counter, defaultdict
import numpy as np
import nltk
from nltk.tokenize import word_tokenize
from nltk.corpus import stopwords
from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
from nltk.util import ngrams

# Download required NLTK data
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

try:
    nltk.data.find('corpora/stopwords')
except LookupError:
    nltk.download('stopwords')

logger = logging.getLogger(__name__)


class TextProcessor:
    """
    Text processing utilities for the CRT system.
    
    Provides methods for tokenization, preprocessing, and similarity computation.
    """
    
    def __init__(self, language: str = 'english'):
        """
        Initialize the text processor.
        
        Args:
            language: Language for stopwords and tokenization
        """
        self.language = language
        self.stopwords = set(stopwords.words(language))
        self.smoothing_function = SmoothingFunction().method1
        
    def tokenize(self, text: str, remove_punctuation: bool = True, 
                 lowercase: bool = True, remove_stopwords: bool = False) -> List[str]:
        """
        Tokenize text with various preprocessing options.
        
        Args:
            text: Input text to tokenize
            remove_punctuation: Whether to remove punctuation
            lowercase: Whether to convert to lowercase
            remove_stopwords: Whether to remove stopwords
            
        Returns:
            List of tokens
        """
        if not text or not isinstance(text, str):
            return []
            
        # Basic cleaning
        text = text.strip()
        
        # Convert to lowercase
        if lowercase:
            text = text.lower()
            
        # Remove punctuation
        if remove_punctuation:
            text = text.translate(str.maketrans('', '', string.punctuation))
            
        # Tokenize
        try:
            tokens = word_tokenize(text, language=self.language)
        except Exception as e:
            logger.warning(f"NLTK tokenization failed: {e}. Using simple split.")
            tokens = text.split()
            
        # Remove stopwords
        if remove_stopwords:
            tokens = [token for token in tokens if token not in self.stopwords]
            
        # Remove empty tokens
        tokens = [token for token in tokens if token.strip()]
        
        return tokens
    
    def preprocess_text(self, text: str, for_bleu: bool = False) -> str:
        """
        Preprocess text for various downstream tasks.
        
        Args:
            text: Input text
            for_bleu: Whether preprocessing is for BLEU computation
            
        Returns:
            Preprocessed text
        """
        if not text:
            return ""
            
        # Basic cleaning
        text = text.strip()
        
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        
        # For BLEU computation, we want to preserve more structure
        if for_bleu:
            # Only basic cleaning for BLEU
            return text.lower().strip()
        else:
            # More aggressive cleaning for other tasks
            # Remove special characters but keep basic punctuation
            text = re.sub(r'[^\w\s\.\!\?\,\;\:]', '', text)
            return text.lower().strip()


def compute_bleu(candidate: str, references: List[str], 
                 max_n: int = 4, weights: Optional[List[float]] = None) -> float:
    """
    Compute BLEU score between candidate and reference sentences.
    
    Args:
        candidate: Candidate sentence
        references: List of reference sentences
        max_n: Maximum n-gram order
        weights: Weights for different n-gram orders
        
    Returns:
        BLEU score (0.0 to 1.0)
    """
    if not candidate or not references:
        return 0.0
        
    if weights is None:
        weights = [1.0/max_n] * max_n
        
    processor = TextProcessor()
    
    # Tokenize candidate and references
    candidate_tokens = processor.tokenize(candidate, remove_punctuation=False, 
                                        remove_stopwords=False)
    reference_tokens_list = []
    
    for ref in references:
        ref_tokens = processor.tokenize(ref, remove_punctuation=False, 
                                      remove_stopwords=False)
        reference_tokens_list.append(ref_tokens)
        
    if not candidate_tokens or not any(reference_tokens_list):
        return 0.0
        
    try:
        # Compute BLEU score with smoothing
        smoothing_function = SmoothingFunction().method1
        score = sentence_bleu(reference_tokens_list, candidate_tokens,
                            weights=weights, smoothing_function=smoothing_function)
        return float(score)
    except Exception as e:
        logger.warning(f"BLEU computation failed: {e}")
        return 0.0


def compute_selfbleu(test_case: str, history: List[str], 
                     n_gram_orders: List[int] = [2, 3, 4, 5]) -> float:
    """
    Compute SelfBLEU score for a test case against a history of test cases.
    
    This implements the SelfBLEU novelty reward from Equation 3 in the paper:
    B_SelfBLEU(x) = -Σ_{n=2}^5 SelfBLEU_X(x,n)
    
    Args:
        test_case: Test case to evaluate
        history: History of previous test cases
        n_gram_orders: N-gram orders to consider
        
    Returns:
        SelfBLEU novelty reward (negative value, lower is more novel)
    """
    if not test_case or not history:
        return 0.0
        
    total_selfbleu = 0.0
    
    for n in n_gram_orders:
        # Compute BLEU-n score against all history
        bleu_scores = []
        
        for hist_case in history:
            if hist_case != test_case:  # Don't compare with itself
                # Use single reference for each comparison
                bleu_score = compute_bleu(test_case, [hist_case], max_n=n, 
                                        weights=[1.0 if i == n-1 else 0.0 for i in range(n)])
                bleu_scores.append(bleu_score)
                
        if bleu_scores:
            avg_bleu_n = np.mean(bleu_scores)
            total_selfbleu += avg_bleu_n
            
    # Return negative SelfBLEU as novelty reward
    return -total_selfbleu


def compute_ngram_overlap(text1: str, text2: str, n: int = 2) -> float:
    """
    Compute n-gram overlap between two texts.
    
    Args:
        text1: First text
        text2: Second text
        n: N-gram order
        
    Returns:
        Overlap ratio (0.0 to 1.0)
    """
    if not text1 or not text2:
        return 0.0
        
    processor = TextProcessor()
    
    tokens1 = processor.tokenize(text1)
    tokens2 = processor.tokenize(text2)
    
    if len(tokens1) < n or len(tokens2) < n:
        return 0.0
        
    # Generate n-grams
    ngrams1 = set(ngrams(tokens1, n))
    ngrams2 = set(ngrams(tokens2, n))
    
    if not ngrams1 or not ngrams2:
        return 0.0
        
    # Compute overlap
    intersection = len(ngrams1.intersection(ngrams2))
    union = len(ngrams1.union(ngrams2))
    
    return intersection / union if union > 0 else 0.0


def compute_jaccard_similarity(text1: str, text2: str) -> float:
    """
    Compute Jaccard similarity between two texts.
    
    Args:
        text1: First text
        text2: Second text
        
    Returns:
        Jaccard similarity (0.0 to 1.0)
    """
    if not text1 or not text2:
        return 0.0
        
    processor = TextProcessor()
    
    tokens1 = set(processor.tokenize(text1))
    tokens2 = set(processor.tokenize(text2))
    
    if not tokens1 or not tokens2:
        return 0.0
        
    intersection = len(tokens1.intersection(tokens2))
    union = len(tokens1.union(tokens2))
    
    return intersection / union if union > 0 else 0.0


def compute_diversity_metrics(texts: List[str]) -> Dict[str, float]:
    """
    Compute various diversity metrics for a collection of texts.
    
    Args:
        texts: List of texts to analyze
        
    Returns:
        Dictionary with diversity metrics
    """
    if not texts:
        return {
            'selfbleu_diversity': 0.0,
            'unique_ratio': 0.0,
            'avg_length': 0.0,
            'vocab_size': 0,
            'avg_jaccard': 0.0
        }
        
    # Remove duplicates for some metrics
    unique_texts = list(set(texts))
    
    # Unique ratio
    unique_ratio = len(unique_texts) / len(texts)
    
    # Average length
    avg_length = np.mean([len(text.split()) for text in texts])
    
    # Vocabulary size
    all_tokens = []
    processor = TextProcessor()
    for text in texts:
        all_tokens.extend(processor.tokenize(text))
    vocab_size = len(set(all_tokens))
    
    # SelfBLEU diversity (1 - average SelfBLEU)
    selfbleu_scores = []
    for i, text in enumerate(texts):
        other_texts = texts[:i] + texts[i+1:]
        if other_texts:
            selfbleu = -compute_selfbleu(text, other_texts)  # Convert back to positive
            selfbleu_scores.append(selfbleu)
    
    selfbleu_diversity = 1.0 - np.mean(selfbleu_scores) if selfbleu_scores else 1.0
    
    # Average pairwise Jaccard similarity
    jaccard_scores = []
    for i in range(len(texts)):
        for j in range(i+1, len(texts)):
            jaccard = compute_jaccard_similarity(texts[i], texts[j])
            jaccard_scores.append(jaccard)
    
    avg_jaccard = np.mean(jaccard_scores) if jaccard_scores else 0.0
    
    return {
        'selfbleu_diversity': float(selfbleu_diversity),
        'unique_ratio': float(unique_ratio),
        'avg_length': float(avg_length),
        'vocab_size': int(vocab_size),
        'avg_jaccard': float(avg_jaccard)
    }


def extract_keywords(text: str, top_k: int = 10) -> List[str]:
    """
    Extract keywords from text using simple frequency-based approach.
    
    Args:
        text: Input text
        top_k: Number of top keywords to return
        
    Returns:
        List of keywords
    """
    if not text:
        return []
        
    processor = TextProcessor()
    tokens = processor.tokenize(text, remove_stopwords=True)
    
    # Count token frequencies
    token_counts = Counter(tokens)
    
    # Get top-k most frequent tokens
    keywords = [token for token, count in token_counts.most_common(top_k)]
    
    return keywords


def clean_generated_text(text: str) -> str:
    """
    Clean generated text for evaluation and display.
    
    Args:
        text: Generated text to clean
        
    Returns:
        Cleaned text
    """
    if not text:
        return ""
        
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)
    
    # Remove leading/trailing whitespace
    text = text.strip()
    
    # Remove incomplete sentences at the end
    sentences = text.split('.')
    if len(sentences) > 1 and sentences[-1].strip() and len(sentences[-1].strip()) < 10:
        text = '.'.join(sentences[:-1]) + '.'
        
    return text


def compute_text_statistics(texts: List[str]) -> Dict[str, Any]:
    """
    Compute comprehensive statistics for a collection of texts.
    
    Args:
        texts: List of texts to analyze
        
    Returns:
        Dictionary with text statistics
    """
    if not texts:
        return {}
        
    processor = TextProcessor()
    
    # Basic statistics
    lengths = [len(text.split()) for text in texts]
    char_lengths = [len(text) for text in texts]
    
    # Token-level statistics
    all_tokens = []
    for text in texts:
        tokens = processor.tokenize(text)
        all_tokens.extend(tokens)
        
    vocab = set(all_tokens)
    token_counts = Counter(all_tokens)
    
    # Diversity metrics
    diversity_metrics = compute_diversity_metrics(texts)
    
    return {
        'num_texts': len(texts),
        'avg_word_length': float(np.mean(lengths)),
        'std_word_length': float(np.std(lengths)),
        'avg_char_length': float(np.mean(char_lengths)),
        'std_char_length': float(np.std(char_lengths)),
        'total_tokens': len(all_tokens),
        'vocab_size': len(vocab),
        'type_token_ratio': len(vocab) / len(all_tokens) if all_tokens else 0.0,
        'most_common_tokens': token_counts.most_common(10),
        **diversity_metrics
    }


# Convenience functions for common operations
def tokenize_text(text: str, **kwargs) -> List[str]:
    """Convenience function for text tokenization."""
    processor = TextProcessor()
    return processor.tokenize(text, **kwargs)


def preprocess_for_bleu(text: str) -> str:
    """Convenience function for BLEU preprocessing."""
    processor = TextProcessor()
    return processor.preprocess_text(text, for_bleu=True)


def batch_compute_selfbleu(test_cases: List[str], history: List[str]) -> List[float]:
    """
    Compute SelfBLEU scores for a batch of test cases.
    
    Args:
        test_cases: List of test cases to evaluate
        history: History of previous test cases
        
    Returns:
        List of SelfBLEU novelty rewards
    """
    return [compute_selfbleu(test_case, history) for test_case in test_cases]


if __name__ == "__main__":
    # Example usage and testing
    test_texts = [
        "This is a test sentence for evaluation.",
        "This is another test sentence for evaluation.",
        "A completely different sentence with other words.",
        "Yet another unique sentence with different content."
    ]
    
    print("Text Processing Utilities Test")
    print("=" * 40)
    
    # Test tokenization
    processor = TextProcessor()
    tokens = processor.tokenize(test_texts[0])
    print(f"Tokens: {tokens}")
    
    # Test BLEU computation
    bleu_score = compute_bleu(test_texts[0], test_texts[1:])
    print(f"BLEU score: {bleu_score:.4f}")
    
    # Test SelfBLEU
    selfbleu_score = compute_selfbleu(test_texts[0], test_texts[1:])
    print(f"SelfBLEU novelty reward: {selfbleu_score:.4f}")
    
    # Test diversity metrics
    diversity = compute_diversity_metrics(test_texts)
    print(f"Diversity metrics: {diversity}")
    
    # Test text statistics
    stats = compute_text_statistics(test_texts)
    print(f"Text statistics: {stats}")