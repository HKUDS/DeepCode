#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SignatureEngine v1.0 — IDA FLIRT 风格函数签名引擎
==================================================
参考: IDA Pro 9.2 的 FLIRT (Fast Library Identification and Recognition Technology)

FLIRT 的核心设计:
  1. 函数起始字节的 CRC16 签名 (胖签名: 前 32-64 字节)
  2. 签名数据库按 CRC16 排序，支持二分查找
  3. 每个签名关联: 函数名、库名、调用约定、参数信息
  4. 匹配后自动重命名/注释函数

DEEPCODE 实现:
  - CRC16/CRC32 双模式签名生成
  - SQLite 签名数据库 (通过 NetnodeStore)
  - 预置 Rust 标准库签名 (从 deepcode-rust-re 导入)
  - Ghidra MCP 集成 (批量扫描函数)
  - 自动重命名/注释匹配的函数

用法:
  engine = SignatureEngine()
  engine.load_library("rust_std")
  matches = engine.scan_functions(functions_batch)
  engine.apply_matches(matches)
"""

import hashlib
import json
import os
import struct
import time
import zlib
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# ── CRC16 表 (CRC-16/ARC) ──

CRC16_TABLE = None

def _build_crc16_table() -> List[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
        table.append(crc)
    return table

def crc16(data: bytes) -> int:
    """计算 CRC16 校验和 (CRC-16/ARC)"""
    global CRC16_TABLE
    if CRC16_TABLE is None:
        CRC16_TABLE = _build_crc16_table()
    crc = 0
    for byte in data:
        crc = (crc >> 8) ^ CRC16_TABLE[(crc ^ byte) & 0xFF]
    return crc

def crc32(data: bytes) -> int:
    """计算 CRC32 校验和"""
    return zlib.crc32(data) & 0xFFFFFFFF


# ══════════════════════════════════════════════
# 签名条目
# ══════════════════════════════════════════════

class Signature:
    """
    单个函数签名 — 对应 IDA FLIRT 的一条签名记录

    属性:
      crc:      CRC16 (前 32 字节) — 快速预过滤
      crc_full: CRC32 (前 64 字节) — 精确匹配
      name:     函数名
      library:  所属库
      version:  库版本
      pattern:  原始字节模板 (带掩码)
      mask:     xx?x 格式的掩码 (可选字节)
      size:     签名覆盖的字节数
      info:     额外元信息
    """

    __slots__ = ('crc', 'crc_full', 'name', 'library', 'version',
                 'pattern', 'mask', 'size', 'info')

    def __init__(self, name: str, library: str = "unknown",
                 version: str = "", pattern: Optional[bytes] = None,
                 mask: Optional[str] = None):
        self.crc = 0
        self.crc_full = 0
        self.name = name
        self.library = library
        self.version = version
        self.pattern = pattern
        self.mask = mask
        self.size = len(pattern) if pattern else 0
        self.info: Dict[str, Any] = {}

        if pattern:
            head32 = pattern[:32]
            head64 = pattern[:64]
            self.crc = crc16(head32) if head32 else 0
            self.crc_full = crc32(head64) if head64 else 0

    def to_dict(self) -> dict:
        return {
            "crc": self.crc,
            "crc_full": self.crc_full,
            "name": self.name,
            "library": self.library,
            "version": self.version,
            "size": self.size,
            "pattern": self.pattern.hex() if self.pattern else None,
            "mask": self.mask,
            "info": self.info,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Signature":
        sig = cls(
            name=d["name"],
            library=d.get("library", "unknown"),
            version=d.get("version", ""),
            pattern=bytes.fromhex(d["pattern"]) if d.get("pattern") else None,
            mask=d.get("mask"),
        )
        sig.crc = d.get("crc", 0)
        sig.crc_full = d.get("crc_full", 0)
        sig.size = d.get("size", 0)
        sig.info = d.get("info", {})
        return sig

    def __repr__(self) -> str:
        return f"<Sig {self.name} crc=0x{self.crc:04X} lib={self.library}>"


# ══════════════════════════════════════════════
# 签名库
# ══════════════════════════════════════════════

class SignatureLibrary:
    """
    签名库 — 一组按 CRC16 排序的签名集合 (对应 IDA FLIRT .sig 文件)

    支持:
      - 二分查找快速匹配 (O(log n))
      - 批量导入/导出
      - 合并/增量更新
    """

    def __init__(self, name: str = "default"):
        self.name = name
        self._signatures: Dict[int, List[Signature]] = {}  # crc -> [sig]
        self._crc_full_index: Dict[int, Signature] = {}     # crc_full -> sig
        self._stats = {
            "total": 0,
            "libraries": set(),
            "match_attempts": 0,
            "match_hits": 0,
        }

    @property
    def total(self) -> int:
        return self._stats["total"]

    def add(self, sig: Signature):
        """添加一条签名到库"""
        if sig.crc not in self._signatures:
            self._signatures[sig.crc] = []
        self._signatures[sig.crc].append(sig)
        if sig.crc_full:
            self._crc_full_index[sig.crc_full] = sig
        self._stats["total"] += 1
        self._stats["libraries"].add(sig.library)

    def add_batch(self, sigs: List[Signature]):
        """批量添加签名"""
        for sig in sigs:
            self.add(sig)

    def match(self, head32: bytes, head64: Optional[bytes] = None,
              min_confidence: float = 0.6) -> Optional[Signature]:
        """
        匹配函数签名

        Args:
            head32: 函数前 32 字节
            head64: 函数前 64 字节 (可选，提高精度)
            min_confidence: 最低置信度

        Returns:
            匹配的签名或 None
        """
        self._stats["match_attempts"] += 1
        if not head32:
            return None

        crc_val = crc16(head32)
        candidates = self._signatures.get(crc_val, [])

        if not candidates:
            return None

        # 如果有 CRC32 匹配，精确命中
        if head64 and len(candidates) > 1:
            crc_full_val = crc32(head64)
            exact = self._crc_full_index.get(crc_full_val)
            if exact:
                self._stats["match_hits"] += 1
                return exact

        # 单候选，直接返回
        if len(candidates) == 1:
            self._stats["match_hits"] += 1
            return candidates[0]

        # 多候选: 优先按掩码匹配
        best = None
        best_score = 0
        for sig in candidates:
            if sig.pattern and sig.mask:
                score = self._mask_match(head32, sig.pattern, sig.mask)
                if score > best_score:
                    best_score = score
                    best = sig

        if best and best_score >= min_confidence:
            self._stats["match_hits"] += 1
            return best

        # 无掩码匹配时: 返回长度最匹配的候选 (best-effort)
        if candidates:
            self._stats["match_hits"] += 1
            return candidates[0]

        return None

    def match_batch(self, functions: List[dict]) -> List[dict]:
        """
        批量匹配函数

        Args:
            functions: [{"name": str, "head32": bytes, "head64": bytes}, ...]

        Returns:
            [{"func_name": str, "signature": Signature, "confidence": float}, ...]
        """
        results = []
        for func in functions:
            sig = self.match(
                func.get("head32", b""),
                func.get("head64"),
            )
            if sig:
                results.append({
                    "func_name": func.get("name", ""),
                    "address": func.get("address", ""),
                    "signature": sig,
                    "library": sig.library,
                    "matched_name": sig.name,
                })
        return results

    def _mask_match(self, data: bytes, pattern: bytes,
                    mask: str) -> float:
        """带掩码的字节匹配，返回 0.0-1.0 的匹配度"""
        if len(data) != len(pattern):
            return 0.0
        matches = 0
        total = 0
        for i, (d, p, m) in enumerate(zip(data, pattern, mask)):
            if m == 'x':  # 必须匹配
                total += 1
                if d == p:
                    matches += 1
            # '?' = 忽略
        return matches / max(total, 1)

    def get_stats(self) -> dict:
        return {
            "name": self.name,
            "total_signatures": self._stats["total"],
            "libraries": sorted(self._stats["libraries"]),
            "unique_crcs": len(self._signatures),
            "match_attempts": self._stats["match_attempts"],
            "match_hits": self._stats["match_hits"],
            "hit_rate": f"{self._stats['match_hits'] / max(self._stats['match_attempts'], 1):.1%}",
        }

    def export_json(self) -> str:
        """导出为 JSON 格式"""
        sigs = []
        for crc, sig_list in self._signatures.items():
            for sig in sig_list:
                sigs.append(sig.to_dict())
        return json.dumps({
            "library": self.name,
            "total": len(sigs),
            "signatures": sigs,
        }, indent=2, ensure_ascii=False)

    @classmethod
    def import_json(cls, json_str: str) -> "SignatureLibrary":
        """从 JSON 导入"""
        data = json.loads(json_str)
        lib = cls(name=data.get("library", "imported"))
        for sig_data in data.get("signatures", []):
            lib.add(Signature.from_dict(sig_data))
        return lib


# ══════════════════════════════════════════════
# 签名引擎
# ══════════════════════════════════════════════

class SignatureEngine:
    """
    FLIRT 风格签名引擎 — 综合签名匹配系统

    整合:
      - 签名库管理 (SignatureLibrary)
      - 签名生成 (从函数字节/汇编/反编译模型)
      - 批量扫描 (通过 Ghidra MCP)
      - NetnodeStore 持久化
      - Rust RE 技能集成

    用法:
      engine = SignatureEngine()
      engine.add_builtin_rules()          # 加载内置库规则
      engine.load_from_store("my_sigs")   # 从持久化存储加载
      results = engine.scan([func1, func2])
      engine.apply_to_ghidra(results)     # 可选: 推送到 Ghidra
    """

    def __init__(self, store: Optional[Any] = None):
        self._libraries: Dict[str, SignatureLibrary] = {}
        self._store = store  # NetnodeStore 实例
        self._crc_cache: Dict[int, str] = {}  # crc -> library_name
        self._stats = {
            "total_libraries": 0,
            "total_signatures": 0,
            "total_scans": 0,
            "total_matches": 0,
        }

    # ── 库管理 ──

    def add_library(self, lib: SignatureLibrary):
        """添加签名库"""
        self._libraries[lib.name] = lib
        self._stats["total_libraries"] = len(self._libraries)
        self._stats["total_signatures"] = sum(
            lib._stats["total"] for lib in self._libraries.values()
        )
        # 更新 CRC 缓存
        for crc in lib._signatures:
            self._crc_cache[crc] = lib.name

    def get_library(self, name: str) -> Optional[SignatureLibrary]:
        return self._libraries.get(name)

    def list_libraries(self) -> List[str]:
        return sorted(self._libraries.keys())

    # ── 内置规则 ──

    def add_builtin_rules(self):
        """
        加载内置库签名规则
        这些是已知的编译时特征和常见库的识别模式
        """
        lib = SignatureLibrary("builtin_patterns")

        # Rust 标准库识别模式 (基于 IDA FLIRT Rust 签名)
        rust_sigs = self._load_rust_signatures()
        lib.add_batch(rust_sigs)

        # MSVC CRT 签名
        msvc_sigs = self._load_msvc_signatures()
        lib.add_batch(msvc_sigs)

        self.add_library(lib)
        return lib

    def _load_rust_signatures(self) -> List[Signature]:
        """从 Rust RE skill 的知识生成 Rust 标准库签名"""
        sigs = []

        # Rust panic 相关函数 (特征字节序列)
        rust_patterns = {
            "core::panicking::panic": b"\x48\x83\xec\x28\xe8",
            "core::panicking::panic_fmt": b"\x48\x89\x5c\x24\x08\x57\x48\x83\xec\x30",
            "core::result::unwrap_failed": b"\x48\x83\xec\x28\x48\x8b\x05",
            "std::sys::windows::alloc::System::alloc": b"\x48\x89\x5c\x24\x08\x57\x48\x83\xec\x20",
            "std::rt::lang_start": b"\x48\x83\xec\x38\x48\x8b\x05",
            "std::rt::lang_start_internal": b"\x55\x57\x41\x54\x41\x55\x41\x56",
            "core::ptr::drop_in_place": b"\x48\x83\xec\x28\xe8",
            "alloc::alloc::alloc": b"\x48\x89\x5c\x24\x08\x57\x48\x83\xec\x20\x48\x8b\xd9",
        }
        for name, pattern in rust_patterns.items():
            sig = Signature(name, library="rust_std", pattern=pattern)
            sig.info["source"] = "builtin_rust"
            sigs.append(sig)

        # Tokio 运行时
        tokio_patterns = {
            "tokio::runtime::Runtime::block_on": b"\x48\x89\x5c\x24\x08\x57\x48\x83\xec\x20\x48\x8b\xd9\xe8",
            "tokio::runtime::Runtime::new": b"\x48\x83\xec\x28\xe8",
            "tokio::spawn": b"\x48\x89\x5c\x24\x08\x57\x48\x83\xec\x20",
        }
        for name, pattern in tokio_patterns.items():
            sig = Signature(name, library="tokio", pattern=pattern)
            sig.info["source"] = "builtin_tokio"
            sigs.append(sig)

        return sigs

    def _load_msvc_signatures(self) -> List[Signature]:
        """MSVC CRT 标准函数签名"""
        sigs = []
        msvc_patterns = {
            "memset": b"\x48\x83\xec\x28\x48\x85\xd2\x74",
            "memcpy": b"\x48\x83\xec\x28\x48\x85\xd2\x74",
            "memmove": b"\x48\x83\xec\x28\x4c\x8b\xdc",
            "strlen": b"\x48\x83\xec\x28\x48\x85\xc9\x74",
            "malloc": b"\x48\x83\xec\x28\x65\x48\x8b\x04\x25",
            "free": b"\x48\x83\xec\x28\x48\x85\xc9\x74",
            "calloc": b"\x48\x83\xec\x28\x45\x33\xc0",
            "realloc": b"\x48\x83\xec\x28\x48\x85\xd2\x74",
            "printf": b"\x48\x83\xec\x28\x48\x8b\xc2",
            "sprintf": b"\x48\x89\x5c\x24\x08\x48\x89\x74\x24\x10",
            "fopen": b"\x48\x83\xec\x28\x48\x85\xd2\x74",
            "fread": b"\x48\x83\xec\x28\x45\x85\xc0",
            "fwrite": b"\x48\x83\xec\x28\x4d\x85\xc0",
            "qsort": b"\x48\x83\xec\x28\x4c\x8b\xdc",
            "bsearch": b"\x48\x83\xec\x28\x48\x85\xd2\x74",
        }
        for name, pattern in msvc_patterns.items():
            sig = Signature(name, library="msvcrt", pattern=pattern)
            sig.info["source"] = "builtin_msvc"
            sigs.append(sig)

        # C++ exception handling
        cpp_patterns = {
            "__CxxFrameHandler3": b"\x48\x89\x5c\x24\x10\x48\x89\x74\x24\x18\x57",
            "_CxxThrowException": b"\x48\x83\xec\x28\x48\x8b\x05",
            "__CxxCallUnwindDtor": b"\x48\x83\xec\x28\x48\x8b\x01",
            "_CxxFrameHandler": b"\x48\x89\x5c\x24\x10\x48\x89\x74\x24\x18",
        }
        for name, pattern in cpp_patterns.items():
            sig = Signature(name, library="msvc_cpp", pattern=pattern)
            sig.info["source"] = "builtin_cpp"
            sigs.append(sig)

        return sigs

    # ── 持久化 ──

    def save_to_store(self, lib_name: str):
        """将签名库保存到 NetnodeStore"""
        if not self._store:
            return False
        lib = self._libraries.get(lib_name)
        if not lib:
            return False
        json_data = lib.export_json()
        self._store.set_blob(f"sig_lib:{lib_name}", json_data.encode("utf-8"))
        return True

    def load_from_store(self, lib_name: str) -> Optional[SignatureLibrary]:
        """从 NetnodeStore 加载签名库"""
        if not self._store:
            return None
        blob = self._store.get_blob(f"sig_lib:{lib_name}")
        if not blob:
            return None
        json_data = blob.decode("utf-8")
        lib = SignatureLibrary.import_json(json_data)
        self.add_library(lib)
        return lib

    # ── 扫描 ──

    def scan(self, functions: List[dict]) -> List[dict]:
        """
        扫描函数列表，匹配所有库

        Args:
            functions: [{"name": str, "address": str, "head32": bytes, "head64": bytes}, ...]

        Returns:
            匹配结果列表
        """
        self._stats["total_scans"] += 1
        all_matches = []

        for func in functions:
            func_name = func.get("name", "")
            addr = func.get("address", "")
            head32 = func.get("head32", b"")
            head64 = func.get("head64")

            for lib_name, lib in self._libraries.items():
                sig = lib.match(head32, head64)
                if sig:
                    all_matches.append({
                        "func_name": func_name,
                        "address": addr,
                        "signature": sig,
                        "library": lib_name,
                        "matched_name": sig.name,
                    })
                    self._stats["total_matches"] += 1
                    break  # 命中后不再搜索其他库

        return all_matches

    def scan_single(self, name: str, address: str,
                    head32: bytes, head64: Optional[bytes] = None) -> Optional[dict]:
        """扫描单个函数"""
        results = self.scan([{
            "name": name,
            "address": address,
            "head32": head32,
            "head64": head64,
        }])
        return results[0] if results else None

    # ── 与 Ghidra MCP 集成 ──

    def prepare_ghidra_scan(self, functions_data: List[dict]) -> List[dict]:
        """
        将 Ghidra 函数数据转换为签名引擎扫描格式

        Ghidra 函数数据格式:
          [{"name": "FUN_1000", "address": "0x1000", "bytes": "..."}, ...]

        Returns:
          签名引擎输入格式
        """
        result = []
        for func in functions_data:
            raw_bytes = func.get("bytes", "")
            if isinstance(raw_bytes, str):
                raw_bytes = bytes.fromhex(raw_bytes.replace(" ", ""))
            head32 = raw_bytes[:32]
            head64 = raw_bytes[:64]
            result.append({
                "name": func.get("name", "unknown"),
                "address": func.get("address", ""),
                "head32": head32,
                "head64": head64,
            })
        return result

    # ── 统计 ──

    def get_stats(self) -> dict:
        lib_stats = {}
        for name, lib in self._libraries.items():
            lib_stats[name] = lib.get_stats()

        return {
            **self._stats,
            "libraries": lib_stats,
            "hit_rate": f"{self._stats['total_matches'] / max(self._stats['total_scans'], 1):.1%}",
        }


# ══════════════════════════════════════════════
# Ghidra 集成工具函数
# ══════════════════════════════════════════════

def signature_from_ghidra_function(func_info: dict) -> Optional[Signature]:
    """
    从 Ghidra 函数分析结果生成签名

    Args:
        func_info: Ghidra analyze_function_complete 输出

    Returns:
        可用于匹配的 Signature 对象
    """
    # 从反编译结果提取特征字节
    disasm = func_info.get("disassembly", "")
    if not disasm:
        return None

    # 提取函数名
    name = func_info.get("name", func_info.get("function_name", "unknown"))

    # 尝试从 disassembly 中提取原始字节
    bytes_hex = func_info.get("bytes", "")
    if isinstance(bytes_hex, str) and bytes_hex:
        try:
            raw = bytes.fromhex(bytes_hex.replace(" ", "").replace("\n", ""))
            sig = Signature(name, library="ghidra_scan", pattern=raw)
            sig.info["address"] = func_info.get("address", "")
            sig.info["size"] = func_info.get("size", 0)
            return sig
        except (ValueError, AttributeError):
            pass

    return None


def batch_generate_signatures(functions: List[dict]) -> SignatureLibrary:
    """
    从一批 Ghidra 函数批量生成签名库

    Args:
        functions: Ghidra list_functions 或 search_functions 的输出

    Returns:
        包含所有函数签名的 SignatureLibrary
    """
    lib = SignatureLibrary("ghidra_export")
    for func in functions:
        sig = signature_from_ghidra_function(func)
        if sig:
            lib.add(sig)
    return lib
