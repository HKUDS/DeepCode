"""
Memory Manager for Curiosity-Driven Red Teaming

This module provides efficient memory management for large history tracking
in the curiosity-driven red-teaming approach. It implements memory-efficient
data structures and cleanup strategies to handle large volumes of generated
test cases while maintaining performance.

Classes:
    MemoryEfficientTracker: Main class for memory-efficient history tracking
    CircularBuffer: Fixed-size circular buffer for recent items
    LRUCache: Least Recently Used cache implementation
    MemoryMonitor: Monitor memory usage and trigger cleanup
"""

import os
import gc
import sys
import time
import pickle
import logging
import threading
from typing import List, Dict, Any, Optional, Tuple, Set, Union
from collections import deque, OrderedDict
from dataclasses import dataclass, field
import numpy as np
import psutil


logger = logging.getLogger(__name__)


@dataclass
class MemoryStats:
    """Statistics about memory usage."""
    total_items: int = 0
    memory_usage_mb: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    cleanup_count: int = 0
    last_cleanup_time: float = 0.0


class CircularBuffer:
    """Fixed-size circular buffer for storing recent items."""
    
    def __init__(self, max_size: int):
        """
        Initialize circular buffer.
        
        Args:
            max_size: Maximum number of items to store
        """
        self.max_size = max_size
        self.buffer = deque(maxlen=max_size)
        self.lock = threading.Lock()
    
    def add(self, item: Any) -> None:
        """Add item to buffer."""
        with self.lock:
            self.buffer.append(item)
    
    def add_batch(self, items: List[Any]) -> None:
        """Add multiple items to buffer."""
        with self.lock:
            self.buffer.extend(items)
    
    def get_all(self) -> List[Any]:
        """Get all items in buffer."""
        with self.lock:
            return list(self.buffer)
    
    def clear(self) -> None:
        """Clear all items from buffer."""
        with self.lock:
            self.buffer.clear()
    
    def size(self) -> int:
        """Get current size of buffer."""
        with self.lock:
            return len(self.buffer)
    
    def is_full(self) -> bool:
        """Check if buffer is full."""
        with self.lock:
            return len(self.buffer) >= self.max_size


class LRUCache:
    """Least Recently Used cache implementation."""
    
    def __init__(self, max_size: int):
        """
        Initialize LRU cache.
        
        Args:
            max_size: Maximum number of items to cache
        """
        self.max_size = max_size
        self.cache = OrderedDict()
        self.lock = threading.Lock()
        self.hits = 0
        self.misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        """Get item from cache."""
        with self.lock:
            if key in self.cache:
                # Move to end (most recently used)
                value = self.cache.pop(key)
                self.cache[key] = value
                self.hits += 1
                return value
            else:
                self.misses += 1
                return None
    
    def put(self, key: str, value: Any) -> None:
        """Put item in cache."""
        with self.lock:
            if key in self.cache:
                # Update existing item
                self.cache.pop(key)
            elif len(self.cache) >= self.max_size:
                # Remove least recently used item
                self.cache.popitem(last=False)
            
            self.cache[key] = value
    
    def clear(self) -> None:
        """Clear all items from cache."""
        with self.lock:
            self.cache.clear()
            self.hits = 0
            self.misses = 0
    
    def size(self) -> int:
        """Get current size of cache."""
        with self.lock:
            return len(self.cache)
    
    def get_stats(self) -> Dict[str, int]:
        """Get cache statistics."""
        with self.lock:
            total_requests = self.hits + self.misses
            hit_rate = self.hits / total_requests if total_requests > 0 else 0.0
            return {
                'hits': self.hits,
                'misses': self.misses,
                'hit_rate': hit_rate,
                'size': len(self.cache)
            }


class MemoryMonitor:
    """Monitor memory usage and trigger cleanup when needed."""
    
    def __init__(self, 
                 memory_threshold_mb: float = 1000.0,
                 check_interval: float = 30.0):
        """
        Initialize memory monitor.
        
        Args:
            memory_threshold_mb: Memory threshold in MB to trigger cleanup
            check_interval: Interval in seconds between memory checks
        """
        self.memory_threshold_mb = memory_threshold_mb
        self.check_interval = check_interval
        self.process = psutil.Process()
        self.last_check_time = 0.0
        self.cleanup_callbacks = []
    
    def register_cleanup_callback(self, callback) -> None:
        """Register a callback to be called during cleanup."""
        self.cleanup_callbacks.append(callback)
    
    def get_memory_usage_mb(self) -> float:
        """Get current memory usage in MB."""
        try:
            memory_info = self.process.memory_info()
            return memory_info.rss / (1024 * 1024)  # Convert to MB
        except Exception as e:
            logger.warning(f"Failed to get memory usage: {e}")
            return 0.0
    
    def should_cleanup(self) -> bool:
        """Check if cleanup should be triggered."""
        current_time = time.time()
        if current_time - self.last_check_time < self.check_interval:
            return False
        
        self.last_check_time = current_time
        memory_usage = self.get_memory_usage_mb()
        
        if memory_usage > self.memory_threshold_mb:
            logger.info(f"Memory usage {memory_usage:.1f}MB exceeds threshold "
                       f"{self.memory_threshold_mb:.1f}MB, triggering cleanup")
            return True
        
        return False
    
    def trigger_cleanup(self) -> None:
        """Trigger cleanup callbacks."""
        for callback in self.cleanup_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error(f"Error in cleanup callback: {e}")
        
        # Force garbage collection
        gc.collect()


class MemoryEfficientTracker:
    """
    Memory-efficient tracker for large history management.
    
    This class provides efficient storage and retrieval of large volumes
    of generated test cases while managing memory usage through various
    strategies including circular buffers, LRU caching, and periodic cleanup.
    """
    
    def __init__(self,
                 max_recent_items: int = 10000,
                 max_cache_size: int = 5000,
                 memory_threshold_mb: float = 1000.0,
                 persistence_dir: Optional[str] = None,
                 enable_compression: bool = True):
        """
        Initialize memory-efficient tracker.
        
        Args:
            max_recent_items: Maximum number of recent items to keep in memory
            max_cache_size: Maximum size of LRU cache
            memory_threshold_mb: Memory threshold for triggering cleanup
            persistence_dir: Directory for persisting data to disk
            enable_compression: Whether to use compression for persistence
        """
        self.max_recent_items = max_recent_items
        self.max_cache_size = max_cache_size
        self.enable_compression = enable_compression
        
        # Core data structures
        self.recent_buffer = CircularBuffer(max_recent_items)
        self.cache = LRUCache(max_cache_size)
        self.all_items = set()  # For fast membership testing
        
        # Memory monitoring
        self.memory_monitor = MemoryMonitor(memory_threshold_mb)
        self.memory_monitor.register_cleanup_callback(self._cleanup)
        
        # Persistence
        self.persistence_dir = persistence_dir
        self.archived_files = []
        if persistence_dir:
            os.makedirs(persistence_dir, exist_ok=True)
        
        # Statistics
        self.stats = MemoryStats()
        self.lock = threading.Lock()
        
        logger.info(f"Initialized MemoryEfficientTracker with "
                   f"max_recent_items={max_recent_items}, "
                   f"max_cache_size={max_cache_size}")
    
    def add_with_cleanup(self, item: str) -> None:
        """
        Add item to tracker with automatic cleanup.
        
        Args:
            item: Item to add to tracker
        """
        with self.lock:
            # Add to recent buffer
            self.recent_buffer.add(item)
            
            # Add to all items set
            self.all_items.add(item)
            
            # Update statistics
            self.stats.total_items += 1
            
            # Check if cleanup is needed
            if self.memory_monitor.should_cleanup():
                self._cleanup()
    
    def add_batch_with_cleanup(self, items: List[str]) -> None:
        """
        Add multiple items with automatic cleanup.
        
        Args:
            items: List of items to add
        """
        with self.lock:
            # Add to recent buffer
            self.recent_buffer.add_batch(items)
            
            # Add to all items set
            self.all_items.update(items)
            
            # Update statistics
            self.stats.total_items += len(items)
            
            # Check if cleanup is needed
            if self.memory_monitor.should_cleanup():
                self._cleanup()
    
    def contains(self, item: str) -> bool:
        """
        Check if item exists in tracker.
        
        Args:
            item: Item to check
            
        Returns:
            True if item exists, False otherwise
        """
        with self.lock:
            return item in self.all_items
    
    def get_recent_items(self, count: Optional[int] = None) -> List[str]:
        """
        Get recent items from tracker.
        
        Args:
            count: Number of recent items to return (None for all)
            
        Returns:
            List of recent items
        """
        recent_items = self.recent_buffer.get_all()
        if count is not None:
            return recent_items[-count:]
        return recent_items
    
    def get_all_items(self) -> Set[str]:
        """
        Get all items in tracker.
        
        Returns:
            Set of all items
        """
        with self.lock:
            return self.all_items.copy()
    
    def get_cached_computation(self, key: str) -> Optional[Any]:
        """
        Get cached computation result.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found
        """
        result = self.cache.get(key)
        if result is not None:
            self.stats.cache_hits += 1
        else:
            self.stats.cache_misses += 1
        return result
    
    def cache_computation(self, key: str, value: Any) -> None:
        """
        Cache computation result.
        
        Args:
            key: Cache key
            value: Value to cache
        """
        self.cache.put(key, value)
    
    def _cleanup(self) -> None:
        """Internal cleanup method."""
        logger.info("Starting memory cleanup")
        cleanup_start_time = time.time()
        
        # Archive old items if persistence is enabled
        if self.persistence_dir:
            self._archive_old_items()
        
        # Clear cache
        self.cache.clear()
        
        # Force garbage collection
        gc.collect()
        
        # Update statistics
        self.stats.cleanup_count += 1
        self.stats.last_cleanup_time = time.time()
        self.stats.memory_usage_mb = self.memory_monitor.get_memory_usage_mb()
        
        cleanup_time = time.time() - cleanup_start_time
        logger.info(f"Memory cleanup completed in {cleanup_time:.2f}s")
    
    def _archive_old_items(self) -> None:
        """Archive old items to disk."""
        try:
            # Get items to archive (items not in recent buffer)
            recent_items = set(self.recent_buffer.get_all())
            items_to_archive = self.all_items - recent_items
            
            if not items_to_archive:
                return
            
            # Create archive file
            timestamp = int(time.time())
            archive_file = os.path.join(
                self.persistence_dir, 
                f"archive_{timestamp}.pkl"
            )
            
            # Save to disk
            with open(archive_file, 'wb') as f:
                if self.enable_compression:
                    import gzip
                    with gzip.open(f, 'wb') as gz_f:
                        pickle.dump(list(items_to_archive), gz_f)
                else:
                    pickle.dump(list(items_to_archive), f)
            
            self.archived_files.append(archive_file)
            
            # Remove archived items from memory (keep in all_items for membership testing)
            logger.info(f"Archived {len(items_to_archive)} items to {archive_file}")
            
        except Exception as e:
            logger.error(f"Failed to archive items: {e}")
    
    def load_archived_items(self) -> List[str]:
        """
        Load all archived items from disk.
        
        Returns:
            List of all archived items
        """
        archived_items = []
        
        for archive_file in self.archived_files:
            try:
                with open(archive_file, 'rb') as f:
                    if self.enable_compression:
                        import gzip
                        with gzip.open(f, 'rb') as gz_f:
                            items = pickle.load(gz_f)
                    else:
                        items = pickle.load(f)
                    archived_items.extend(items)
            except Exception as e:
                logger.error(f"Failed to load archived items from {archive_file}: {e}")
        
        return archived_items
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """
        Get memory and performance statistics.
        
        Returns:
            Dictionary containing various statistics
        """
        with self.lock:
            cache_stats = self.cache.get_stats()
            
            return {
                'total_items': self.stats.total_items,
                'recent_items': self.recent_buffer.size(),
                'all_items_count': len(self.all_items),
                'memory_usage_mb': self.memory_monitor.get_memory_usage_mb(),
                'cache_hits': cache_stats['hits'],
                'cache_misses': cache_stats['misses'],
                'cache_hit_rate': cache_stats['hit_rate'],
                'cache_size': cache_stats['size'],
                'cleanup_count': self.stats.cleanup_count,
                'last_cleanup_time': self.stats.last_cleanup_time,
                'archived_files_count': len(self.archived_files)
            }
    
    def clear_all(self) -> None:
        """Clear all data from tracker."""
        with self.lock:
            self.recent_buffer.clear()
            self.cache.clear()
            self.all_items.clear()
            self.stats = MemoryStats()
            
            # Remove archived files
            for archive_file in self.archived_files:
                try:
                    os.remove(archive_file)
                except Exception as e:
                    logger.warning(f"Failed to remove archive file {archive_file}: {e}")
            
            self.archived_files.clear()
            
            logger.info("Cleared all data from MemoryEfficientTracker")
    
    def __len__(self) -> int:
        """Get total number of items."""
        with self.lock:
            return len(self.all_items)
    
    def __contains__(self, item: str) -> bool:
        """Check if item is in tracker."""
        return self.contains(item)


class BatchMemoryManager:
    """
    Batch-oriented memory manager for processing large datasets.
    
    This class is optimized for batch processing scenarios where
    large numbers of items need to be processed efficiently.
    """
    
    def __init__(self,
                 batch_size: int = 1000,
                 max_batches_in_memory: int = 10,
                 temp_dir: Optional[str] = None):
        """
        Initialize batch memory manager.
        
        Args:
            batch_size: Size of each batch
            max_batches_in_memory: Maximum number of batches to keep in memory
            temp_dir: Temporary directory for batch storage
        """
        self.batch_size = batch_size
        self.max_batches_in_memory = max_batches_in_memory
        self.temp_dir = temp_dir or "/tmp/curiosity_redteam_batches"
        
        # Create temp directory
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # Batch storage
        self.current_batch = []
        self.completed_batches = deque(maxlen=max_batches_in_memory)
        self.batch_files = []
        self.batch_counter = 0
        
        logger.info(f"Initialized BatchMemoryManager with batch_size={batch_size}")
    
    def add_item(self, item: str) -> Optional[List[str]]:
        """
        Add item to current batch.
        
        Args:
            item: Item to add
            
        Returns:
            Completed batch if current batch is full, None otherwise
        """
        self.current_batch.append(item)
        
        if len(self.current_batch) >= self.batch_size:
            return self._complete_current_batch()
        
        return None
    
    def _complete_current_batch(self) -> List[str]:
        """Complete current batch and start new one."""
        completed_batch = self.current_batch.copy()
        
        # Store batch in memory if space available
        if len(self.completed_batches) < self.max_batches_in_memory:
            self.completed_batches.append(completed_batch)
        else:
            # Store to disk
            self._store_batch_to_disk(completed_batch)
        
        # Start new batch
        self.current_batch = []
        self.batch_counter += 1
        
        return completed_batch
    
    def _store_batch_to_disk(self, batch: List[str]) -> None:
        """Store batch to disk."""
        batch_file = os.path.join(self.temp_dir, f"batch_{self.batch_counter}.pkl")
        
        try:
            with open(batch_file, 'wb') as f:
                pickle.dump(batch, f)
            self.batch_files.append(batch_file)
            logger.debug(f"Stored batch {self.batch_counter} to disk")
        except Exception as e:
            logger.error(f"Failed to store batch to disk: {e}")
    
    def get_all_batches(self) -> List[List[str]]:
        """Get all completed batches."""
        all_batches = list(self.completed_batches)
        
        # Load batches from disk
        for batch_file in self.batch_files:
            try:
                with open(batch_file, 'rb') as f:
                    batch = pickle.load(f)
                    all_batches.append(batch)
            except Exception as e:
                logger.error(f"Failed to load batch from {batch_file}: {e}")
        
        return all_batches
    
    def finalize(self) -> Optional[List[str]]:
        """Finalize current batch even if not full."""
        if self.current_batch:
            return self._complete_current_batch()
        return None
    
    def cleanup(self) -> None:
        """Clean up temporary files."""
        for batch_file in self.batch_files:
            try:
                os.remove(batch_file)
            except Exception as e:
                logger.warning(f"Failed to remove batch file {batch_file}: {e}")
        
        self.batch_files.clear()
        self.completed_batches.clear()
        self.current_batch.clear()
        
        logger.info("Cleaned up BatchMemoryManager")


# Utility functions for memory management

def get_memory_usage() -> Dict[str, float]:
    """
    Get current memory usage statistics.
    
    Returns:
        Dictionary with memory usage information
    """
    try:
        process = psutil.Process()
        memory_info = process.memory_info()
        
        return {
            'rss_mb': memory_info.rss / (1024 * 1024),
            'vms_mb': memory_info.vms / (1024 * 1024),
            'percent': process.memory_percent(),
            'available_mb': psutil.virtual_memory().available / (1024 * 1024)
        }
    except Exception as e:
        logger.error(f"Failed to get memory usage: {e}")
        return {}


def estimate_object_size(obj: Any) -> int:
    """
    Estimate the size of a Python object in bytes.
    
    Args:
        obj: Object to estimate size for
        
    Returns:
        Estimated size in bytes
    """
    try:
        return sys.getsizeof(obj)
    except Exception:
        return 0


def trigger_garbage_collection() -> Dict[str, int]:
    """
    Trigger garbage collection and return statistics.
    
    Returns:
        Dictionary with garbage collection statistics
    """
    collected = gc.collect()
    
    return {
        'collected_objects': collected,
        'generation_0': len(gc.get_objects(0)),
        'generation_1': len(gc.get_objects(1)),
        'generation_2': len(gc.get_objects(2))
    }


if __name__ == "__main__":
    # Example usage
    logging.basicConfig(level=logging.INFO)
    
    # Create memory-efficient tracker
    tracker = MemoryEfficientTracker(
        max_recent_items=1000,
        max_cache_size=500,
        memory_threshold_mb=100.0,
        persistence_dir="./temp_memory"
    )
    
    # Add some test items
    test_items = [f"test_item_{i}" for i in range(2000)]
    
    print("Adding items to tracker...")
    for item in test_items:
        tracker.add_with_cleanup(item)
    
    # Get statistics
    stats = tracker.get_memory_stats()
    print(f"Memory stats: {stats}")
    
    # Test batch manager
    batch_manager = BatchMemoryManager(batch_size=100)
    
    print("Testing batch manager...")
    for item in test_items[:250]:
        completed_batch = batch_manager.add_item(item)
        if completed_batch:
            print(f"Completed batch with {len(completed_batch)} items")
    
    # Finalize
    final_batch = batch_manager.finalize()
    if final_batch:
        print(f"Final batch with {len(final_batch)} items")
    
    # Cleanup
    tracker.clear_all()
    batch_manager.cleanup()
    
    print("Memory management example completed!")