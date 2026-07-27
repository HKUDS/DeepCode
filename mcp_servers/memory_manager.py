#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MemoryManager v3.0 — Claude Code 风格内存管理与增量索引
==========================================================
参考: claude.exe 的 bmalloc + NT API 内存管理 + V8 堆管理

核心设计:
  1. LRUCache: 最近最少使用缓存 (文件内容/查询结果)
  2. MemoryMappedIndex: 内存映射文件索引 (类似 mmap)
  3. IncrementalIndexer: 增量索引器 (只索引变更文件)
  4. TokenBudgetManager: Token 预算管理
  5. CacheWarmup: 缓存预热 (启动时加载常用数据)

对比 claude.exe:
  claude.exe:  bmalloc(WebKit) → NT API(HeapAlloc) → V8 GC
  DeepCode:    Python dict → LRUCache → MemoryMappedIndex → IncrementalIndexer
"""

import asyncio
import fnmatch
import hashlib
import json
import mmap
import os
import pickle
import re
import struct
import tempfile
import time
import zlib
from collections import defaultdict, deque, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union


# ══════════════════════════════════════════════
# LRU 缓存
# ══════════════════════════════════════════════

class LRUCache:
    """
    最近最少使用缓存 — 类似 claude.exe 中的 V8 堆缓存

    基于 OrderedDict 实现 O(1) 的 get/set。
    支持:
      - TTL (过期时间)
      - 最大条目数限制
      - 最大内存限制
      - 访问统计
      - 逐出回调
    """

    def __init__(self, max_size: int = 1000, max_memory_mb: int = 256,
                 ttl_seconds: float = 300):
        self.max_size = max_size
        self.max_memory_mb = max_memory_mb
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self.ttl_seconds = ttl_seconds

        self._cache: OrderedDict = OrderedDict()
        self._expiry: Dict[str, float] = {}
        self._size_map: Dict[str, int] = {}  # key -> size in bytes
        self._current_memory = 0
        self._eviction_callback: Optional[Callable] = None

        self._stats = {
            "hits": 0,
            "misses": 0,
            "evictions": 0,
            "expirations": 0,
            "total_sets": 0,
            "current_entries": 0,
            "current_memory_mb": 0,
        }

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def memory_usage_mb(self) -> float:
        return self._current_memory / (1024 * 1024)

    def on_evict(self, callback: Callable):
        """设置逐出回调"""
        self._eviction_callback = callback

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取缓存条目

        Args:
            key: 缓存键
            default: 默认值

        Returns:
            缓存的值
        """
        if key not in self._cache:
            self._stats["misses"] += 1
            return default

        # 检查 TTL
        if key in self._expiry and time.time() > self._expiry[key]:
            self._evict(key)
            self._stats["expirations"] += 1
            self._stats["misses"] += 1
            return default

        # 移到末尾 (最近使用)
        value = self._cache.pop(key)
        self._cache[key] = value
        self._stats["hits"] += 1
        return value

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None):
        """
        设置缓存条目

        Args:
            key: 缓存键
            value: 值
            ttl_seconds: 过期时间 (覆盖默认)
        """
        # 计算大小
        size = self._estimate_size(value)

        # 如果已经有该 key, 先释放内存
        if key in self._cache:
            self._current_memory -= self._size_map.get(key, 0)

        # 逐出直到有足够空间
        while (self._current_memory + size > self.max_memory_bytes and self._cache):
            self._evict(self._cache.keys()[0] if self._cache else None)

        # 逐出直到不超过最大条目数
        while len(self._cache) >= self.max_size:
            self._evict(self._cache.keys()[0] if self._cache else None)

        self._cache[key] = value
        self._size_map[key] = size
        self._current_memory += size

        if ttl_seconds is not None:
            self._expiry[key] = time.time() + ttl_seconds
        elif self.ttl_seconds > 0:
            self._expiry[key] = time.time() + self.ttl_seconds

        self._stats["total_sets"] += 1
        self._stats["current_entries"] = len(self._cache)
        self._stats["current_memory_mb"] = round(self.memory_usage_mb, 2)

    def delete(self, key: str):
        """删除缓存条目"""
        if key in self._cache:
            self._current_memory -= self._size_map.get(key, 0)
            del self._cache[key]
            self._expiry.pop(key, None)
            self._size_map.pop(key, None)

    def clear(self):
        """清空缓存"""
        self._cache.clear()
        self._expiry.clear()
        self._size_map.clear()
        self._current_memory = 0
        self._stats["current_entries"] = 0
        self._stats["current_memory_mb"] = 0

    def contains(self, key: str) -> bool:
        """检查键是否存在且未过期"""
        if key not in self._cache:
            return False
        if key in self._expiry and time.time() > self._expiry[key]:
            self._evict(key)
            return False
        return True

    def warmup(self, items: Dict[str, Any]):
        """批量预热缓存"""
        for key, value in items.items():
            self.set(key, value)

    def get_stats(self) -> dict:
        total = self._stats["hits"] + self._stats["misses"]
        return {
            **self._stats,
            "hit_rate": f"{self._stats['hits'] / max(total, 1):.1%}",
            "ttl_seconds": self.ttl_seconds,
            "max_size": self.max_size,
            "max_memory_mb": self.max_memory_mb,
            "memory_usage_pct": f"{self.memory_usage_mb / max(self.max_memory_mb, 1):.1%}",
        }

    def _evict(self, key: Optional[str]):
        """逐出一个条目"""
        if key is None or key not in self._cache:
            # 逐出最旧的
            if self._cache:
                key = next(iter(self._cache))
            else:
                return

        value = self._cache.pop(key, None)
        size = self._size_map.pop(key, 0)
        self._current_memory -= size
        self._expiry.pop(key, None)
        self._stats["evictions"] += 1

        if self._eviction_callback and value is not None:
            try:
                self._eviction_callback(key, value)
            except Exception:
                pass

    def _estimate_size(self, value: Any) -> int:
        """估算对象占用的字节数"""
        try:
            return len(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL))
        except Exception:
            return 1024  # 默认 1KB


# ══════════════════════════════════════════════
# 内存映射文件索引
# ══════════════════════════════════════════════

class MemoryMappedIndex:
    """
    内存映射文件索引 — 类似 claude.exe 的 mmap 文件映射

    用于快速索引大型文件，无需加载完整文件到内存。
    基于 Python 的 mmap 模块，支持:
      - 文件内容搜索 (无需全量加载)
      - 行级索引 (文件 → 行号 → 偏移)
      - 快速 grep
      - 增量更新
    """

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = cache_dir or self._default_cache_dir()
        os.makedirs(self.cache_dir, exist_ok=True)

        self._index: Dict[str, dict] = {}
        self._mapped_files: Dict[str, mmap.mmap] = {}
        self._stats = {
            "files_indexed": 0,
            "total_size_mb": 0,
            "total_lines": 0,
            "searches": 0,
            "hits": 0,
            "misses": 0,
            "mmap_active": 0,
        }

    def _default_cache_dir(self) -> str:
        base = os.environ.get("DEEPCODE_CACHE_DIR", "")
        if not base:
            base = os.path.join(os.environ.get("HOME", os.environ.get("USERPROFILE", ".")),
                                ".deepcode", "cache")
        return os.path.join(base, "file_index")

    def index_file(self, file_path: str) -> Optional[dict]:
        """
        索引单个文件 (构建行级索引)

        Args:
            file_path: 文件路径

        Returns:
            文件索引信息
        """
        if not os.path.exists(file_path):
            return None

        abs_path = os.path.abspath(file_path)
        stat = os.stat(abs_path)

        # 检查是否已被索引且未变更
        if abs_path in self._index:
            existing = self._index[abs_path]
            if existing.get("mtime") == stat.st_mtime and existing.get("size") == stat.st_size:
                self._stats["hits"] += 1
                return existing

        # 构建索引
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                lines = []
                offset = 0
                for line_num, line in enumerate(f, 1):
                    lines.append({
                        "line": line_num,
                        "offset": offset,
                        "length": len(line),
                    })
                    offset += len(line)

            index = {
                "path": abs_path,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "lines": len(lines),
                "line_index": lines[:100] if len(lines) > 100 else lines,  # 前100行索引
                "hash": hashlib.md5(open(abs_path, "rb").read(8192)).hexdigest(),  # 头部 hash
                "indexed_at": datetime.now().isoformat(),
            }

            self._index[abs_path] = index
            self._stats["files_indexed"] += 1
            self._stats["total_size_mb"] += stat.st_size / (1024 * 1024)
            self._stats["total_lines"] += len(lines)
            self._stats["misses"] += 1

            return index

        except (IOError, UnicodeDecodeError) as e:
            return None

    def index_directory(self, dir_path: str, pattern: str = "*.py") -> int:
        """
        索引目录中匹配的文件

        Args:
            dir_path: 目录路径
            pattern: glob 模式

        Returns:
            索引的文件数
        """
        count = 0
        for root, _, files in os.walk(dir_path):
            for f in files:
                if fnmatch.fnmatch(f, pattern):
                    file_path = os.path.join(root, f)
                    if self.index_file(file_path):
                        count += 1
        return count

    def mmap_open(self, file_path: str) -> Optional[mmap.mmap]:
        """
        打开文件的内存映射

        Args:
            file_path: 文件路径

        Returns:
            mmap 对象
        """
        abs_path = os.path.abspath(file_path)
        if abs_path in self._mapped_files:
            return self._mapped_files[abs_path]

        if not os.path.exists(abs_path):
            return None

        try:
            fd = os.open(abs_path, os.O_RDONLY)
            size = os.fstat(fd).st_size
            if size == 0:
                os.close(fd)
                return None

            mapped = mmap.mmap(fd, size, access=mmap.ACCESS_READ)
            os.close(fd)
            self._mapped_files[abs_path] = mapped
            self._stats["mmap_active"] += 1
            return mapped
        except Exception:
            return None

    def mmap_close(self, file_path: str):
        """关闭文件的内存映射"""
        abs_path = os.path.abspath(file_path)
        mapped = self._mapped_files.pop(abs_path, None)
        if mapped:
            mapped.close()
            self._stats["mmap_active"] -= 1

    def grep(self, file_path: str, pattern: str,
              max_results: int = 50) -> List[dict]:
        """
        在文件内容中搜索 (使用 mmap)

        Args:
            file_path: 文件路径
            pattern: 搜索模式 (正则)
            max_results: 最大结果数

        Returns:
            匹配行列表
        """
        self._stats["searches"] += 1
        mapped = self.mmap_open(file_path)
        if not mapped:
            return []

        try:
            compiled = re.compile(pattern.encode("utf-8"), re.IGNORECASE)
            results = []
            pos = 0
            while pos < len(mapped):
                match = compiled.search(mapped, pos)
                if not match:
                    break
                # 计算行号
                line_start = mapped.rfind(b"\n", 0, match.start()) + 1
                line_end = mapped.find(b"\n", match.end())
                if line_end == -1:
                    line_end = len(mapped)

                line_num = mapped[:line_start].count(b"\n") + 1
                line_content = mapped[line_start:line_end].decode("utf-8", errors="replace").strip()

                results.append({
                    "line": line_num,
                    "content": line_content[:500],
                    "offset": match.start(),
                })

                if len(results) >= max_results:
                    break
                pos = line_end + 1

            return results

        except (re.error, Exception) as e:
            return []
        finally:
            self.mmap_close(file_path)

    def close_all(self):
        """关闭所有内存映射"""
        for path in list(self._mapped_files.keys()):
            self.mmap_close(path)

    def get_stats(self) -> dict:
        return dict(self._stats)

    def __del__(self):
        self.close_all()


# ══════════════════════════════════════════════
# 增量索引器
# ══════════════════════════════════════════════

class IncrementalIndexer:
    """
    增量索引器 — 只索引变更的文件

    对比 claude.exe 的全量索引，DeepCode 只需索引变更文件。
    使用文件时间戳 + 内容 hash 检测变更。

    用法:
      indexer = IncrementalIndexer()
      indexer.watch_directory("./src")  # 开始监控
      changed = indexer.get_changed_files()  # 获取变更
      indexer.update_index(changed)  # 增量更新
    """

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = cache_dir or os.path.join(
            os.environ.get("DEEPCODE_CACHE_DIR", ""),
            ".deepcode", "cache", "incremental_index"
        )
        os.makedirs(self.cache_dir, exist_ok=True)

        self._file_hashes: Dict[str, str] = self._load_hashes()
        self._watched_dirs: Set[str] = set()
        self._index_data: Dict[str, dict] = {}
        self._stats = {
            "total_files": 0,
            "indexed_files": 0,
            "changed_files": 0,
            "new_files": 0,
            "deleted_files": 0,
            "full_rebuilds": 0,
        }

    def watch_directory(self, dir_path: str):
        """添加监控目录"""
        abs_path = os.path.abspath(dir_path)
        if os.path.isdir(abs_path):
            self._watched_dirs.add(abs_path)

    def unwatch_directory(self, dir_path: str):
        """移除监控目录"""
        self._watched_dirs.discard(os.path.abspath(dir_path))

    def get_changed_files(self, pattern: str = "*") -> Dict[str, List[str]]:
        """
        检测变更的文件

        Returns:
            {"new": [...], "changed": [...], "deleted": [...]}
        """
        result = {"new": [], "changed": [], "deleted": []}

        # 收集当前文件
        current_files = set()
        for watched_dir in self._watched_dirs:
            if not os.path.isdir(watched_dir):
                continue
            for root, _, files in os.walk(watched_dir):
                for f in files:
                    if fnmatch.fnmatch(f, pattern):
                        current_files.add(os.path.join(root, f))

        # 检测新增和变更
        for file_path in current_files:
            current_hash = self._hash_file(file_path)
            if current_hash is None:
                continue

            prev_hash = self._file_hashes.get(file_path)
            if prev_hash is None:
                result["new"].append(file_path)
            elif prev_hash != current_hash:
                result["changed"].append(file_path)

        # 检测删除
        for file_path in list(self._file_hashes.keys()):
            if file_path not in current_files:
                result["deleted"].append(file_path)

        self._stats["changed_files"] = len(result["changed"])
        self._stats["new_files"] = len(result["new"])
        self._stats["deleted_files"] = len(result["deleted"])

        return result

    def update_index(self, changes: Dict[str, List[str]],
                      index_func: Callable[[str], Any] = None):
        """
        增量更新索引

        Args:
            changes: get_changed_files 的返回值
            index_func: 索引函数 (接收文件路径, 返回索引数据)
        """
        # 处理新增和变更
        for file_path in changes.get("new", []) + changes.get("changed", []):
            if index_func:
                try:
                    self._index_data[file_path] = index_func(file_path)
                except Exception:
                    pass
            current_hash = self._hash_file(file_path)
            if current_hash:
                self._file_hashes[file_path] = current_hash
                self._stats["indexed_files"] += 1

        # 处理删除
        for file_path in changes.get("deleted", []):
            self._file_hashes.pop(file_path, None)
            self._index_data.pop(file_path, None)

        self._stats["total_files"] = len(self._file_hashes)
        self._save_hashes()

    def needs_full_rebuild(self) -> bool:
        """判断是否需要全量重建"""
        if self._stats["changed_files"] > self._stats["total_files"] * 0.5:
            return True
        if not self._file_hashes:
            return True
        return False

    def full_rebuild(self, index_func: Callable[[str], Any],
                      dir_path: str, pattern: str = "*"):
        """全量重建索引"""
        self._file_hashes.clear()
        self._index_data.clear()

        for root, _, files in os.walk(dir_path):
            for f in files:
                if fnmatch.fnmatch(f, pattern):
                    file_path = os.path.join(root, f)
                    if index_func:
                        try:
                            self._index_data[file_path] = index_func(file_path)
                        except Exception:
                            pass
                    current_hash = self._hash_file(file_path)
                    if current_hash:
                        self._file_hashes[file_path] = current_hash

        self._stats["full_rebuilds"] += 1
        self._stats["total_files"] = len(self._file_hashes)
        self._stats["indexed_files"] = len(self._index_data)
        self._save_hashes()

    def query(self, file_path: str) -> Optional[dict]:
        """查询文件的索引数据"""
        return self._index_data.get(os.path.abspath(file_path))

    def _hash_file(self, file_path: str) -> Optional[str]:
        """计算文件内容 hash"""
        try:
            with open(file_path, "rb") as f:
                # 只读前 4KB 和最后 1KB 用于快速变更检测
                head = f.read(4096)
                f.seek(-1024, os.SEEK_END)
                tail = f.read(1024)
            return hashlib.md5(head + tail).hexdigest()
        except (IOError, OSError):
            return None

    def _save_hashes(self):
        """持久化 hash 表"""
        hash_path = os.path.join(self.cache_dir, "file_hashes.json")
        os.makedirs(os.path.dirname(hash_path), exist_ok=True)
        try:
            with open(hash_path, "w", encoding="utf-8") as f:
                json.dump(self._file_hashes, f, indent=2)
        except Exception:
            pass

    def _load_hashes(self) -> Dict[str, str]:
        """加载 hash 表"""
        hash_path = os.path.join(self.cache_dir, "file_hashes.json")
        if os.path.exists(hash_path):
            try:
                with open(hash_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    def get_stats(self) -> dict:
        return dict(self._stats)


# ══════════════════════════════════════════════
# Token 预算管理器
# ══════════════════════════════════════════════

class TokenBudgetManager:
    """
    Token 预算管理器 — 控制每轮/每小时/每天的 Token 消耗

    类似 claude.exe 的 token_tracker.ts + budget_controller.ts

    用法:
      budget = TokenBudgetManager()
      budget.use(1500)  # 消耗 1500 tokens
      if budget.remaining < 1000:
          print("预算不足")
    """

    def __init__(self, max_per_turn: int = 8000,
                 max_per_hour: int = 100000,
                 max_per_day: int = 1000000):
        self.max_per_turn = max_per_turn
        self.max_per_hour = max_per_hour
        self.max_per_day = max_per_day

        self._turn_used = 0
        self._hour_used = 0
        self._day_used = 0
        self._hour_reset = time.time()
        self._day_reset = time.time()
        self._history: deque = deque(maxlen=1000)
        self._total_used = 0
        self._warning_threshold = 0.85
        self._stats = {"warnings": 0, "blocks": 0}

    def start_turn(self):
        """开始新一轮 (重置轮次计数)"""
        self._turn_used = 0

    def use(self, tokens: int) -> bool:
        """
        消耗 tokens

        Args:
            tokens: 消耗数量

        Returns:
            True: 消耗成功
            False: 超过预算
        """
        now = time.time()

        # 重置
        if now - self._hour_reset >= 3600:
            self._hour_used = 0
            self._hour_reset = now
        if now - self._day_reset >= 86400:
            self._day_used = 0
            self._day_reset = now

        # 检查预算
        if self._turn_used + tokens > self.max_per_turn:
            self._stats["blocks"] += 1
            return False
        if self._hour_used + tokens > self.max_per_hour:
            self._stats["blocks"] += 1
            return False
        if self._day_used + tokens > self.max_per_day:
            self._stats["blocks"] += 1
            return False

        self._turn_used += tokens
        self._hour_used += tokens
        self._day_used += tokens
        self._total_used += tokens

        # 警告
        if self.usage_pct >= self._warning_threshold:
            self._stats["warnings"] += 1

        self._history.append({
            "tokens": tokens,
            "timestamp": datetime.now().isoformat(),
        })

        return True

    @property
    def remaining(self) -> int:
        return max(0, self.max_per_turn - self._turn_used)

    @property
    def usage_pct(self) -> float:
        return self._turn_used / max(self.max_per_turn, 1)

    def summary(self) -> dict:
        return {
            "per_turn": {"max": self.max_per_turn, "used": self._turn_used,
                         "remaining": self.remaining,
                         "pct": f"{self.usage_pct:.1%}"},
            "per_hour": {"max": self.max_per_hour, "used": self._hour_used},
            "per_day": {"max": self.max_per_day, "used": self._day_used},
            "total_used": self._total_used,
            "warning_threshold": f"{self._warning_threshold:.0%}",
            **self._stats,
        }


# ══════════════════════════════════════════════
# 缓存预热器
# ══════════════════════════════════════════════

class CacheWarmup:
    """
    缓存预热器 — 启动时预加载常用数据

    参考 claude.exe 的 V8 快照热身机制:
      - 启动时预编译常用模块
      - 预加载常用文件到缓存
    """

    def __init__(self, cache: LRUCache):
        self.cache = cache
        self._stats = {"warmed_items": 0, "errors": 0}

    async def warmup_files(self, file_paths: List[str]):
        """预热文件缓存"""
        for path in file_paths:
            try:
                if os.path.exists(path) and os.path.isfile(path):
                    with open(path, "rb") as f:
                        content = f.read(8192)  # 只缓存前 8KB
                    self.cache.set(f"file_head:{path}", content, ttl_seconds=600)
                    self._stats["warmed_items"] += 1
            except Exception:
                self._stats["errors"] += 1

    async def warmup_common_queries(self):
        """预热常用查询"""
        common = {
            "tools:list": {"tools": []},  # 占位
            "system:status": {"status": "ready"},
        }
        for key, value in common.items():
            self.cache.set(key, value, ttl_seconds=3600)
            self._stats["warmed_items"] += 1

    def get_stats(self) -> dict:
        return dict(self._stats)


# ══════════════════════════════════════════════
# NetnodeStore — IDA Pro 风格持久化 KV 存储
# ══════════════════════════════════════════════

class NetnodeStore:
    """
    IDA Pro 风格的 netnode 持久化 KV 存储 — 参考 ida.dll 的 68 个 netnode_* API

    IDA 的 netnode 系统是其数据库 (IDB/I64) 的核心。每个 netnode 是一个
    持久化的键值节点，支持:
      - 唯一名称/ID 标识
      - 主值 (value) + 数值辅助值 (altval)
      - 任意数量的辅助键值对 (supval)
      - blob 二进制数据
      - 哈希索引 (hashval)
      - 父子关系 (通过 supval 实现)

    DEEPCODE 实现使用 SQLite 作为持久化后端，
    在内存中缓存热节点 (通过 LRUCache)。

    用法:
      store = NetnodeStore("analysis_results")
      node = store.create("func_1000_analysis")
      node.set_value({"name": "sub_1000", "size": 128})
      node.set_supval("callers", ["main", "start"])
      node.set_blob(b"raw data...")
      store.flush()
    """

    def __init__(self, namespace: str = "default",
                 db_path: Optional[str] = None,
                 lru_size: int = 500):
        self.namespace = namespace
        self._lru = LRUCache(max_size=lru_size, max_memory_mb=64)
        self._db_path = db_path or self._default_db_path()
        self._dirty_nodes: Set[str] = set()
        self._stats = {
            "nodes_created": 0,
            "nodes_loaded": 0,
            "values_set": 0,
            "values_get": 0,
            "blobs_set": 0,
            "blobs_get": 0,
            "supvals_set": 0,
            "supvals_get": 0,
            "flushes": 0,
            "hash_hits": 0,
            "hash_misses": 0,
        }
        self._conn = None
        self._init_db()

    def _default_db_path(self) -> str:
        base = os.environ.get("DEEPCODE_DATA_DIR", "")
        if not base:
            base = os.path.join(
                os.environ.get("HOME", os.environ.get("USERPROFILE", ".")),
                ".deepcode", "data"
            )
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, f"netnode_{self.namespace}.db")

    def _init_db(self):
        import sqlite3
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, timeout=10)
        conn = self._conn
        conn.execute("""
            CREATE TABLE IF NOT EXISTS netnodes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT UNIQUE NOT NULL,
                value       BLOB,
                altval      INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS supvals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id     INTEGER NOT NULL,
                key         TEXT NOT NULL,
                value       BLOB,
                value_type  TEXT DEFAULT 'text',
                FOREIGN KEY (node_id) REFERENCES netnodes(id) ON DELETE CASCADE,
                UNIQUE(node_id, key)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS blobs (
                node_id     INTEGER PRIMARY KEY,
                data        BLOB,
                size        INTEGER DEFAULT 0,
                md5         TEXT,
                FOREIGN KEY (node_id) REFERENCES netnodes(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS node_relations (
                parent_id   INTEGER NOT NULL,
                child_id    INTEGER NOT NULL,
                rel_type    TEXT DEFAULT 'default',
                PRIMARY KEY (parent_id, child_id),
                FOREIGN KEY (parent_id) REFERENCES netnodes(id) ON DELETE CASCADE,
                FOREIGN KEY (child_id) REFERENCES netnodes(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_netnodes_name ON netnodes(name)
        """)
        conn.commit()

    def _get_conn(self):
        return self._conn

    def create(self, name: str, value: Any = None, altval: int = 0) -> Optional[int]:
        import sqlite3
        conn = self._get_conn()
        try:
            val_bytes = self._serialize(value) if value is not None else None
            conn.execute(
                "INSERT OR IGNORE INTO netnodes (name, value, altval) VALUES (?, ?, ?)",
                (name, val_bytes, altval)
            )
            conn.commit()
            cursor = conn.execute("SELECT id FROM netnodes WHERE name = ?", (name,))
            row = cursor.fetchone()
            if row:
                self._stats["nodes_created"] += 1
                self._invalidate_cache(name)
                return row[0]
            return None
        except sqlite3.IntegrityError:
            return None
        finally:
            pass

    def get_node_id(self, name: str) -> Optional[int]:
        cache_key = f"node_id:{name}"
        cached = self._lru.get(cache_key)
        if cached is not None:
            self._stats["hash_hits"] += 1
            return cached
        import sqlite3
        conn = self._get_conn()
        try:
            cursor = conn.execute("SELECT id FROM netnodes WHERE name = ?", (name,))
            row = cursor.fetchone()
            if row:
                self._stats["hash_misses"] += 1
                self._lru.set(cache_key, row[0], ttl_seconds=3600)
                return row[0]
            return None
        finally:
            pass

    def delete(self, name: str) -> bool:
        import sqlite3
        node_id = self.get_node_id(name)
        if node_id is None:
            return False
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM blobs WHERE node_id = ?", (node_id,))
            conn.execute("DELETE FROM supvals WHERE node_id = ?", (node_id,))
            conn.execute(
                "DELETE FROM node_relations WHERE parent_id = ? OR child_id = ?",
                (node_id, node_id)
            )
            conn.execute("DELETE FROM netnodes WHERE id = ?", (node_id,))
            conn.commit()
            self._invalidate_cache(name)
            return True
        finally:
            pass

    def exists(self, name: str) -> bool:
        return self.get_node_id(name) is not None

    def set_value(self, name: str, value: Any) -> bool:
        import sqlite3
        node_id = self.get_node_id(name)
        if node_id is None:
            node_id = self.create(name)
            if node_id is None:
                return False
        conn = self._get_conn()
        try:
            val_bytes = self._serialize(value)
            conn.execute("UPDATE netnodes SET value = ?, updated_at = datetime('now') WHERE id = ?",
                         (val_bytes, node_id))
            conn.commit()
            self._stats["values_set"] += 1
            self._invalidate_cache(name)
            return True
        finally:
            pass

    def get_value(self, name: str, default: Any = None) -> Any:
        cache_key = f"value:{name}"
        cached = self._lru.get(cache_key)
        if cached is not None:
            return cached
        import sqlite3
        conn = self._get_conn()
        try:
            cursor = conn.execute("SELECT value FROM netnodes WHERE name = ?", (name,))
            row = cursor.fetchone()
            if row and row[0]:
                result = self._deserialize(row[0])
                self._lru.set(cache_key, result, ttl_seconds=300)
                return result
            return default
        finally:
            pass

    def set_altval(self, name: str, altval: int) -> bool:
        import sqlite3
        node_id = self.get_node_id(name)
        if node_id is None:
            node_id = self.create(name)
            if node_id is None:
                return False
        conn = self._get_conn()
        try:
            conn.execute("UPDATE netnodes SET altval = ?, updated_at = datetime('now') WHERE id = ?",
                         (altval, node_id))
            conn.commit()
            return True
        finally:
            pass

    def get_altval(self, name: str) -> int:
        import sqlite3
        conn = self._get_conn()
        try:
            cursor = conn.execute("SELECT altval FROM netnodes WHERE name = ?", (name,))
            row = cursor.fetchone()
            return row[0] if row else 0
        finally:
            pass

    def set_supval(self, name: str, key: str, value: Any, value_type: str = "auto") -> bool:
        import sqlite3
        node_id = self.get_node_id(name)
        if node_id is None:
            node_id = self.create(name)
            if node_id is None:
                return False
        if value_type == "auto":
            value_type = "text" if isinstance(value, (int, float, bool, str)) else "json"
        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO supvals (node_id, key, value, value_type)
                VALUES (?, ?, ?, ?)
            """, (node_id, key, self._serialize(value), value_type))
            conn.commit()
            self._stats["supvals_set"] += 1
            self._invalidate_cache(f"supval:{name}:{key}")
            return True
        finally:
            pass

    def get_supval(self, name: str, key: str, default: Any = None) -> Any:
        cache_key = f"supval:{name}:{key}"
        cached = self._lru.get(cache_key)
        if cached is not None:
            return cached
        import sqlite3
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                SELECT s.value FROM supvals s
                JOIN netnodes n ON s.node_id = n.id
                WHERE n.name = ? AND s.key = ?
            """, (name, key))
            row = cursor.fetchone()
            if row:
                result = self._deserialize(row[0])
                self._lru.set(cache_key, result, ttl_seconds=300)
                return result
            return default
        finally:
            pass

    def get_all_supvals(self, name: str) -> Dict[str, Any]:
        import sqlite3
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                SELECT s.key, s.value FROM supvals s
                JOIN netnodes n ON s.node_id = n.id
                WHERE n.name = ?
            """, (name,))
            return {row[0]: self._deserialize(row[1]) for row in cursor.fetchall()}
        finally:
            pass

    def del_supval(self, name: str, key: str) -> bool:
        import sqlite3
        node_id = self.get_node_id(name)
        if node_id is None:
            return False
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM supvals WHERE node_id = ? AND key = ?", (node_id, key))
            conn.commit()
            self._invalidate_cache(f"supval:{name}:{key}")
            return True
        finally:
            pass

    def set_blob(self, name: str, data: bytes) -> bool:
        import sqlite3
        node_id = self.get_node_id(name)
        if node_id is None:
            node_id = self.create(name)
            if node_id is None:
                return False
        md5_hash = hashlib.md5(data).hexdigest()
        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO blobs (node_id, data, size, md5)
                VALUES (?, ?, ?, ?)
            """, (node_id, data, len(data), md5_hash))
            conn.commit()
            self._stats["blobs_set"] += 1
            self._invalidate_cache(f"blob:{name}")
            return True
        finally:
            pass

    def get_blob(self, name: str) -> Optional[bytes]:
        cache_key = f"blob:{name}"
        cached = self._lru.get(cache_key)
        if cached is not None:
            return cached
        import sqlite3
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                SELECT b.data FROM blobs b
                JOIN netnodes n ON b.node_id = n.id WHERE n.name = ?
            """, (name,))
            row = cursor.fetchone()
            if row:
                self._lru.set(cache_key, row[0], ttl_seconds=600)
                return row[0]
            return None
        finally:
            pass

    def get_blob_info(self, name: str) -> Optional[dict]:
        import sqlite3
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                SELECT b.size, b.md5, n.updated_at FROM blobs b
                JOIN netnodes n ON b.node_id = n.id WHERE n.name = ?
            """, (name,))
            row = cursor.fetchone()
            if row:
                return {"size": row[0], "md5": row[1], "updated_at": row[2]}
            return None
        finally:
            pass

    def add_child(self, parent_name: str, child_name: str, rel_type: str = "default") -> bool:
        import sqlite3
        parent_id = self.get_node_id(parent_name)
        child_id = self.get_node_id(child_name)
        if parent_id is None:
            parent_id = self.create(parent_name)
        if child_id is None:
            child_id = self.create(child_name)
        if parent_id is None or child_id is None:
            return False
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO node_relations (parent_id, child_id, rel_type) VALUES (?, ?, ?)",
                (parent_id, child_id, rel_type)
            )
            conn.commit()
            return True
        finally:
            pass

    def get_children(self, name: str, rel_type: Optional[str] = None) -> List[str]:
        import sqlite3
        conn = self._get_conn()
        try:
            if rel_type:
                cursor = conn.execute("""
                    SELECT n.name FROM netnodes n
                    JOIN node_relations r ON n.id = r.child_id
                    JOIN netnodes p ON p.id = r.parent_id
                    WHERE p.name = ? AND r.rel_type = ?
                """, (name, rel_type))
            else:
                cursor = conn.execute("""
                    SELECT n.name FROM netnodes n
                    JOIN node_relations r ON n.id = r.child_id
                    JOIN netnodes p ON p.id = r.parent_id
                    WHERE p.name = ?
                """, (name,))
            return [row[0] for row in cursor.fetchall()]
        finally:
            pass

    def get_parents(self, name: str) -> List[str]:
        import sqlite3
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                SELECT p.name FROM netnodes p
                JOIN node_relations r ON p.id = r.parent_id
                JOIN netnodes c ON c.id = r.child_id WHERE c.name = ?
            """, (name,))
            return [row[0] for row in cursor.fetchall()]
        finally:
            pass

    def search_by_supval(self, key: str, value: Any) -> List[str]:
        import sqlite3
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                SELECT n.name FROM netnodes n
                JOIN supvals s ON n.id = s.node_id
                WHERE s.key = ? AND s.value = ?
            """, (key, self._serialize(value)))
            return [row[0] for row in cursor.fetchall()]
        finally:
            pass

    def search_by_name_glob(self, pattern: str) -> List[str]:
        import sqlite3
        conn = self._get_conn()
        try:
            sql_pattern = pattern.replace("*", "%").replace("?", "_")
            cursor = conn.execute(
                "SELECT name FROM netnodes WHERE name LIKE ? ORDER BY name",
                (sql_pattern,)
            )
            return [row[0] for row in cursor.fetchall()]
        finally:
            pass

    def flush(self):
        self._dirty_nodes.clear()
        self._stats["flushes"] += 1

    def get_size(self) -> dict:
        import sqlite3
        conn = self._get_conn()
        try:
            nc = conn.execute("SELECT COUNT(*) FROM netnodes").fetchone()[0]
            sc = conn.execute("SELECT COUNT(*) FROM supvals").fetchone()[0]
            bc = conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0]
            bs = conn.execute("SELECT COALESCE(SUM(size), 0) FROM blobs").fetchone()[0]
            return {"nodes": nc, "supvals": sc, "blobs": bc, "blob_size_bytes": bs}
        finally:
            pass

    def get_stats(self) -> dict:
        return dict(self._stats)

    def vacuum(self):
        import sqlite3
        conn = self._get_conn()
        try:
            conn.execute("VACUUM")
        finally:
            pass

    def _serialize(self, value: Any) -> bytes:
        try:
            return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            return str(value).encode("utf-8")

    def _deserialize(self, data: bytes) -> Any:
        if data is None:
            return None
        try:
            return pickle.loads(data)
        except Exception:
            try:
                return data.decode("utf-8")
            except Exception:
                return data

    def _invalidate_cache(self, name: str):
        self._lru.delete(f"node_id:{name}")
        self._lru.delete(f"value:{name}")
        self._lru.delete(f"blob:{name}")
        self._dirty_nodes.add(name)
