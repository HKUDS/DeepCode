"""
Diversity Metrics for Curiosity-Driven Red-Teaming

This module implements diversity evaluation metrics for the CRT system, including:
- SelfBLEU diversity (Equation A.4)
- Embedding diversity (Equation A.5)
- Coverage analysis and uniqueness metrics

Based on the paper "Curiosity-Driven Red-Teaming for Large Language Models"
"""

import logging
import numpy as np
from typing import List, Dict, Any, Optional, Tuple, Union
from collections import defaultdict, Counter
import nltk
from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
from sklearn.metrics.pairwise import cosine_similarity
import torch
from dataclasses import dataclass
import json
import os

# Ensure NLTK data is available
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

logger = logging.getLogger(__name__)


@dataclass
class DiversityMetrics:
    """Container for diversity evaluation results"""
    selfbleu_diversity: float
    embedding_diversity: float
    unique_ratio: float
    coverage_score: float
    avg_length: float
    length_variance: float
    vocab_size: int
    repetition_ratio: float
    n_gram_diversity: Dict[int, float]
    cluster_diversity: float
    semantic_diversity: float


class DiversityEvaluator:
    """
    Evaluates diversity of generated test cases using multiple metrics
    
    Implements:
    - SelfBLEU diversity: 1 - (1/|X_τ|) * Σ_i Σ_n SelfBLEU(x_i,n) (Equation A.4)
    - Embedding diversity: 1 - (1/|X_τ|²) * Σ_i Σ_j cosine_sim(φ(x_i), φ(x_j)) (Equation A.5)
    """
    
    def __init__(self, 
                 sentence_embedder=None,
                 n_gram_range: Tuple[int, int] = (2, 5),
                 smoothing_function=None,
                 cache_embeddings: bool = True):
        """
        Initialize diversity evaluator
        
        Args:
            sentence_embedder: SentenceEmbedder instance for computing embeddings
            n_gram_range: Range of n-grams to consider for SelfBLEU (default: 2-5)
            smoothing_function: BLEU smoothing function
            cache_embeddings: Whether to cache sentence embeddings
        """
        self.sentence_embedder = sentence_embedder
        self.n_gram_range = n_gram_range
        self.smoothing_function = smoothing_function or SmoothingFunction().method1
        self.cache_embeddings = cache_embeddings
        
        # Caching
        self._embedding_cache = {}
        self._bleu_cache = {}
        
        # Statistics
        self.stats = {
            'total_evaluations': 0,
            'cache_hits': 0,
            'cache_misses': 0
        }
        
        logger.info(f"Initialized DiversityEvaluator with n-gram range {n_gram_range}")
    
    def compute_selfbleu_diversity(self, test_cases: List[str]) -> float:
        """
        Compute SelfBLEU diversity metric (Equation A.4)
        
        Formula: 1 - (1/|X_τ|) * Σ_i Σ_n SelfBLEU(x_i,n)
        
        Args:
            test_cases: List of test case strings
            
        Returns:
            SelfBLEU diversity score (higher = more diverse)
        """
        if not test_cases or len(test_cases) < 2:
            return 1.0  # Perfect diversity for single/empty cases
        
        total_selfbleu = 0.0
        num_cases = len(test_cases)
        
        for i, test_case in enumerate(test_cases):
            # Create reference corpus excluding current test case
            references = [tc for j, tc in enumerate(test_cases) if j != i]
            
            if not references:
                continue
                
            # Compute SelfBLEU for different n-gram orders
            case_selfbleu = 0.0
            for n in range(self.n_gram_range[0], self.n_gram_range[1] + 1):
                try:
                    # Tokenize
                    candidate_tokens = nltk.word_tokenize(test_case.lower())
                    reference_tokens_list = [nltk.word_tokenize(ref.lower()) for ref in references]
                    
                    # Compute BLEU score
                    weights = [0] * 4
                    if n <= 4:
                        weights[n-1] = 1.0
                    else:
                        # For n > 4, use uniform weights up to n
                        weights = [1.0/n] * min(n, 4)
                    
                    bleu_score = sentence_bleu(
                        reference_tokens_list,
                        candidate_tokens,
                        weights=weights,
                        smoothing_function=self.smoothing_function
                    )
                    
                    case_selfbleu += bleu_score
                    
                except Exception as e:
                    logger.warning(f"Error computing BLEU for n={n}: {e}")
                    continue
            
            total_selfbleu += case_selfbleu
        
        # Compute diversity as 1 - average SelfBLEU
        avg_selfbleu = total_selfbleu / (num_cases * (self.n_gram_range[1] - self.n_gram_range[0] + 1))
        diversity = 1.0 - avg_selfbleu
        
        return max(0.0, min(1.0, diversity))  # Clamp to [0, 1]
    
    def compute_embedding_diversity(self, test_cases: List[str]) -> float:
        """
        Compute embedding diversity metric (Equation A.5)
        
        Formula: 1 - (1/|X_τ|²) * Σ_i Σ_j cosine_sim(φ(x_i), φ(x_j))
        
        Args:
            test_cases: List of test case strings
            
        Returns:
            Embedding diversity score (higher = more diverse)
        """
        if not test_cases or len(test_cases) < 2:
            return 1.0  # Perfect diversity for single/empty cases
        
        if self.sentence_embedder is None:
            logger.warning("No sentence embedder provided, returning 0.0")
            return 0.0
        
        try:
            # Get embeddings for all test cases
            embeddings = []
            for test_case in test_cases:
                if self.cache_embeddings and test_case in self._embedding_cache:
                    embedding = self._embedding_cache[test_case]
                    self.stats['cache_hits'] += 1
                else:
                    embedding = self.sentence_embedder.embed(test_case)
                    if self.cache_embeddings:
                        self._embedding_cache[test_case] = embedding
                    self.stats['cache_misses'] += 1
                
                embeddings.append(embedding)
            
            # Convert to numpy array
            embeddings = np.array(embeddings)
            
            # Compute pairwise cosine similarities
            similarity_matrix = cosine_similarity(embeddings)
            
            # Compute average similarity (excluding diagonal)
            num_cases = len(test_cases)
            total_similarity = np.sum(similarity_matrix) - np.trace(similarity_matrix)
            avg_similarity = total_similarity / (num_cases * (num_cases - 1))
            
            # Diversity is 1 - average similarity
            diversity = 1.0 - avg_similarity
            
            return max(0.0, min(1.0, diversity))  # Clamp to [0, 1]
            
        except Exception as e:
            logger.error(f"Error computing embedding diversity: {e}")
            return 0.0
    
    def compute_unique_ratio(self, test_cases: List[str]) -> float:
        """
        Compute ratio of unique test cases
        
        Args:
            test_cases: List of test case strings
            
        Returns:
            Ratio of unique cases (1.0 = all unique, 0.0 = all duplicates)
        """
        if not test_cases:
            return 1.0
        
        unique_cases = set(test_cases)
        return len(unique_cases) / len(test_cases)
    
    def compute_coverage_score(self, test_cases: List[str], threshold: float = 0.8) -> float:
        """
        Compute coverage score based on semantic clustering
        
        Args:
            test_cases: List of test case strings
            threshold: Similarity threshold for clustering
            
        Returns:
            Coverage score based on number of semantic clusters
        """
        if not test_cases or len(test_cases) < 2:
            return 1.0
        
        if self.sentence_embedder is None:
            # Fallback to lexical diversity
            return self.compute_lexical_diversity(test_cases)
        
        try:
            # Get embeddings
            embeddings = []
            for test_case in test_cases:
                embedding = self.sentence_embedder.embed(test_case)
                embeddings.append(embedding)
            
            embeddings = np.array(embeddings)
            
            # Simple clustering based on similarity threshold
            clusters = []
            assigned = set()
            
            for i, emb_i in enumerate(embeddings):
                if i in assigned:
                    continue
                
                cluster = [i]
                assigned.add(i)
                
                for j, emb_j in enumerate(embeddings):
                    if j in assigned:
                        continue
                    
                    similarity = cosine_similarity([emb_i], [emb_j])[0, 0]
                    if similarity >= threshold:
                        cluster.append(j)
                        assigned.add(j)
                
                clusters.append(cluster)
            
            # Coverage score is ratio of clusters to total cases
            coverage = len(clusters) / len(test_cases)
            return min(1.0, coverage)
            
        except Exception as e:
            logger.error(f"Error computing coverage score: {e}")
            return self.compute_lexical_diversity(test_cases)
    
    def compute_lexical_diversity(self, test_cases: List[str]) -> float:
        """
        Compute lexical diversity (vocabulary size / total tokens)
        
        Args:
            test_cases: List of test case strings
            
        Returns:
            Lexical diversity score
        """
        if not test_cases:
            return 1.0
        
        all_tokens = []
        for test_case in test_cases:
            tokens = nltk.word_tokenize(test_case.lower())
            all_tokens.extend(tokens)
        
        if not all_tokens:
            return 1.0
        
        unique_tokens = set(all_tokens)
        return len(unique_tokens) / len(all_tokens)
    
    def compute_n_gram_diversity(self, test_cases: List[str]) -> Dict[int, float]:
        """
        Compute n-gram diversity for different n values
        
        Args:
            test_cases: List of test case strings
            
        Returns:
            Dictionary mapping n to diversity score
        """
        diversity_scores = {}
        
        for n in range(1, 6):  # 1-grams to 5-grams
            all_ngrams = []
            
            for test_case in test_cases:
                tokens = nltk.word_tokenize(test_case.lower())
                if len(tokens) >= n:
                    ngrams = [tuple(tokens[i:i+n]) for i in range(len(tokens) - n + 1)]
                    all_ngrams.extend(ngrams)
            
            if all_ngrams:
                unique_ngrams = set(all_ngrams)
                diversity_scores[n] = len(unique_ngrams) / len(all_ngrams)
            else:
                diversity_scores[n] = 1.0
        
        return diversity_scores
    
    def compute_repetition_ratio(self, test_cases: List[str]) -> float:
        """
        Compute ratio of repeated phrases/patterns
        
        Args:
            test_cases: List of test case strings
            
        Returns:
            Repetition ratio (lower = more diverse)
        """
        if not test_cases:
            return 0.0
        
        # Count 3-gram repetitions
        all_trigrams = []
        for test_case in test_cases:
            tokens = nltk.word_tokenize(test_case.lower())
            if len(tokens) >= 3:
                trigrams = [tuple(tokens[i:i+3]) for i in range(len(tokens) - 2)]
                all_trigrams.extend(trigrams)
        
        if not all_trigrams:
            return 0.0
        
        trigram_counts = Counter(all_trigrams)
        repeated_trigrams = sum(count - 1 for count in trigram_counts.values() if count > 1)
        
        return repeated_trigrams / len(all_trigrams)
    
    def compute_cluster_diversity(self, test_cases: List[str], n_clusters: int = 10) -> float:
        """
        Compute diversity based on clustering analysis
        
        Args:
            test_cases: List of test case strings
            n_clusters: Number of clusters to form
            
        Returns:
            Cluster diversity score
        """
        if not test_cases or len(test_cases) < n_clusters:
            return 1.0
        
        if self.sentence_embedder is None:
            return self.compute_lexical_diversity(test_cases)
        
        try:
            from sklearn.cluster import KMeans
            
            # Get embeddings
            embeddings = []
            for test_case in test_cases:
                embedding = self.sentence_embedder.embed(test_case)
                embeddings.append(embedding)
            
            embeddings = np.array(embeddings)
            
            # Perform clustering
            kmeans = KMeans(n_clusters=min(n_clusters, len(test_cases)), random_state=42)
            cluster_labels = kmeans.fit_predict(embeddings)
            
            # Compute cluster balance (entropy)
            cluster_counts = Counter(cluster_labels)
            total_cases = len(test_cases)
            
            entropy = 0.0
            for count in cluster_counts.values():
                prob = count / total_cases
                if prob > 0:
                    entropy -= prob * np.log2(prob)
            
            # Normalize by maximum possible entropy
            max_entropy = np.log2(min(n_clusters, len(test_cases)))
            if max_entropy > 0:
                return entropy / max_entropy
            else:
                return 1.0
                
        except ImportError:
            logger.warning("sklearn not available, using lexical diversity")
            return self.compute_lexical_diversity(test_cases)
        except Exception as e:
            logger.error(f"Error computing cluster diversity: {e}")
            return self.compute_lexical_diversity(test_cases)
    
    def evaluate(self, test_cases: List[str]) -> DiversityMetrics:
        """
        Compute comprehensive diversity metrics
        
        Args:
            test_cases: List of test case strings
            
        Returns:
            DiversityMetrics object with all computed metrics
        """
        self.stats['total_evaluations'] += 1
        
        logger.info(f"Evaluating diversity for {len(test_cases)} test cases")
        
        # Core diversity metrics
        selfbleu_diversity = self.compute_selfbleu_diversity(test_cases)
        embedding_diversity = self.compute_embedding_diversity(test_cases)
        
        # Additional metrics
        unique_ratio = self.compute_unique_ratio(test_cases)
        coverage_score = self.compute_coverage_score(test_cases)
        
        # Length statistics
        lengths = [len(nltk.word_tokenize(tc)) for tc in test_cases if tc.strip()]
        avg_length = np.mean(lengths) if lengths else 0.0
        length_variance = np.var(lengths) if lengths else 0.0
        
        # Vocabulary metrics
        all_tokens = []
        for test_case in test_cases:
            tokens = nltk.word_tokenize(test_case.lower())
            all_tokens.extend(tokens)
        vocab_size = len(set(all_tokens))
        
        # Pattern metrics
        repetition_ratio = self.compute_repetition_ratio(test_cases)
        n_gram_diversity = self.compute_n_gram_diversity(test_cases)
        cluster_diversity = self.compute_cluster_diversity(test_cases)
        
        # Semantic diversity (average of embedding and cluster diversity)
        semantic_diversity = (embedding_diversity + cluster_diversity) / 2.0
        
        metrics = DiversityMetrics(
            selfbleu_diversity=selfbleu_diversity,
            embedding_diversity=embedding_diversity,
            unique_ratio=unique_ratio,
            coverage_score=coverage_score,
            avg_length=avg_length,
            length_variance=length_variance,
            vocab_size=vocab_size,
            repetition_ratio=repetition_ratio,
            n_gram_diversity=n_gram_diversity,
            cluster_diversity=cluster_diversity,
            semantic_diversity=semantic_diversity
        )
        
        logger.info(f"Diversity evaluation complete: SelfBLEU={selfbleu_diversity:.3f}, "
                   f"Embedding={embedding_diversity:.3f}, Unique={unique_ratio:.3f}")
        
        return metrics
    
    def evaluate_batch(self, test_cases_batch: List[List[str]]) -> List[DiversityMetrics]:
        """
        Evaluate diversity for a batch of test case lists
        
        Args:
            test_cases_batch: List of test case lists
            
        Returns:
            List of DiversityMetrics for each test case list
        """
        results = []
        for i, test_cases in enumerate(test_cases_batch):
            logger.debug(f"Evaluating batch item {i+1}/{len(test_cases_batch)}")
            metrics = self.evaluate(test_cases)
            results.append(metrics)
        
        return results
    
    def compare_diversity(self, 
                         test_cases_a: List[str], 
                         test_cases_b: List[str],
                         label_a: str = "A",
                         label_b: str = "B") -> Dict[str, Any]:
        """
        Compare diversity between two sets of test cases
        
        Args:
            test_cases_a: First set of test cases
            test_cases_b: Second set of test cases
            label_a: Label for first set
            label_b: Label for second set
            
        Returns:
            Comparison results
        """
        metrics_a = self.evaluate(test_cases_a)
        metrics_b = self.evaluate(test_cases_b)
        
        comparison = {
            'metrics': {
                label_a: metrics_a,
                label_b: metrics_b
            },
            'differences': {
                'selfbleu_diversity': metrics_b.selfbleu_diversity - metrics_a.selfbleu_diversity,
                'embedding_diversity': metrics_b.embedding_diversity - metrics_a.embedding_diversity,
                'unique_ratio': metrics_b.unique_ratio - metrics_a.unique_ratio,
                'coverage_score': metrics_b.coverage_score - metrics_a.coverage_score,
                'semantic_diversity': metrics_b.semantic_diversity - metrics_a.semantic_diversity
            },
            'winner': {
                'selfbleu_diversity': label_b if metrics_b.selfbleu_diversity > metrics_a.selfbleu_diversity else label_a,
                'embedding_diversity': label_b if metrics_b.embedding_diversity > metrics_a.embedding_diversity else label_a,
                'unique_ratio': label_b if metrics_b.unique_ratio > metrics_a.unique_ratio else label_a,
                'coverage_score': label_b if metrics_b.coverage_score > metrics_a.coverage_score else label_a,
                'semantic_diversity': label_b if metrics_b.semantic_diversity > metrics_a.semantic_diversity else label_a
            }
        }
        
        return comparison
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get evaluator statistics"""
        return {
            'stats': self.stats.copy(),
            'cache_size': len(self._embedding_cache),
            'n_gram_range': self.n_gram_range,
            'cache_enabled': self.cache_embeddings
        }
    
    def clear_cache(self):
        """Clear all caches"""
        self._embedding_cache.clear()
        self._bleu_cache.clear()
        logger.info("Cleared diversity evaluator caches")
    
    def save_metrics(self, metrics: DiversityMetrics, filepath: str):
        """
        Save diversity metrics to file
        
        Args:
            metrics: DiversityMetrics to save
            filepath: Output file path
        """
        try:
            # Convert to serializable format
            metrics_dict = {
                'selfbleu_diversity': metrics.selfbleu_diversity,
                'embedding_diversity': metrics.embedding_diversity,
                'unique_ratio': metrics.unique_ratio,
                'coverage_score': metrics.coverage_score,
                'avg_length': metrics.avg_length,
                'length_variance': metrics.length_variance,
                'vocab_size': metrics.vocab_size,
                'repetition_ratio': metrics.repetition_ratio,
                'n_gram_diversity': metrics.n_gram_diversity,
                'cluster_diversity': metrics.cluster_diversity,
                'semantic_diversity': metrics.semantic_diversity
            }
            
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, 'w') as f:
                json.dump(metrics_dict, f, indent=2)
            
            logger.info(f"Saved diversity metrics to {filepath}")
            
        except Exception as e:
            logger.error(f"Error saving metrics: {e}")
    
    def load_metrics(self, filepath: str) -> DiversityMetrics:
        """
        Load diversity metrics from file
        
        Args:
            filepath: Input file path
            
        Returns:
            Loaded DiversityMetrics
        """
        try:
            with open(filepath, 'r') as f:
                metrics_dict = json.load(f)
            
            metrics = DiversityMetrics(**metrics_dict)
            logger.info(f"Loaded diversity metrics from {filepath}")
            return metrics
            
        except Exception as e:
            logger.error(f"Error loading metrics: {e}")
            raise


def create_diversity_evaluator(sentence_embedder=None, **kwargs) -> DiversityEvaluator:
    """
    Factory function to create a DiversityEvaluator
    
    Args:
        sentence_embedder: SentenceEmbedder instance
        **kwargs: Additional arguments for DiversityEvaluator
        
    Returns:
        Configured DiversityEvaluator instance
    """
    return DiversityEvaluator(sentence_embedder=sentence_embedder, **kwargs)


if __name__ == "__main__":
    # Example usage and testing
    logging.basicConfig(level=logging.INFO)
    
    # Sample test cases for demonstration
    test_cases = [
        "Write a story about a brave knight.",
        "Tell me about a courageous warrior.",
        "Create a tale of heroic adventure.",
        "Describe a fearless champion.",
        "Generate a narrative about valor.",
        "Write something completely different about cooking.",
        "Explain quantum physics in simple terms.",
        "List the benefits of exercise.",
        "Describe the process of photosynthesis.",
        "Write a poem about nature."
    ]
    
    # Create evaluator
    evaluator = DiversityEvaluator()
    
    # Evaluate diversity
    metrics = evaluator.evaluate(test_cases)
    
    print("Diversity Evaluation Results:")
    print(f"SelfBLEU Diversity: {metrics.selfbleu_diversity:.3f}")
    print(f"Embedding Diversity: {metrics.embedding_diversity:.3f}")
    print(f"Unique Ratio: {metrics.unique_ratio:.3f}")
    print(f"Coverage Score: {metrics.coverage_score:.3f}")
    print(f"Vocabulary Size: {metrics.vocab_size}")
    print(f"Repetition Ratio: {metrics.repetition_ratio:.3f}")
    print(f"Semantic Diversity: {metrics.semantic_diversity:.3f}")
    
    # Test comparison
    diverse_cases = test_cases
    repetitive_cases = ["Write a story"] * 10
    
    comparison = evaluator.compare_diversity(diverse_cases, repetitive_cases, "Diverse", "Repetitive")
    print(f"\nComparison - SelfBLEU difference: {comparison['differences']['selfbleu_diversity']:.3f}")
    print(f"Winner: {comparison['winner']['selfbleu_diversity']}")