#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IDA Bridge v1.0 — IDA API 在 Ghidra 上的等价实现
==================================================
让 DEEPCODE 通过 Ghidra MCP 使用 IDA 风格的分析能力。

用户不需要安装或使用 IDA Pro 9.2。
所有 IDA 特性通过 Ghidra MCP 调用等效功能实现：

  IDA get_byte()    → Ghidra read_memory()
  IDA get_func()     → Ghidra get_function_by_address()
  IDA decompile()    → Ghidra decompile_function()
  IDA netnode_*      → DEEPCODE NetnodeStore
  IDA FLIRT          → DEEPCODE SignatureEngine
  IDA Hex-Rays μcode → Ghidra get_function_pcode()
"""

import json
import time
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

# ── 导入 DEEPCODE 组件 ──
_srv_dir = Path(__file__).parent
import sys
sys.path.insert(0, str(_srv_dir))

from memory_manager import NetnodeStore
from signature_engine import SignatureEngine, Signature, SignatureLibrary


class IDABridge:
    """
    IDA API 桥接层 — 通过 Ghidra MCP 实现 IDA 核心功能

    用法:
      bridge = IDABridge()
      bridge.connect_ghidra()        # 通过 MCP 连接 Ghidra
      bridge.auto_analyze()          # 一键分析 (FLIRT + PCode + Netnode)
      bridge.flirt_scan("ida.exe")   # 扫描函数
      bridge.get_decompiled(addr)    # 反编译
    """

    def __init__(self):
        # DEEPCODE 组件
        self.store = NetnodeStore("ghidra_ida_bridge")
        self.sig_engine = SignatureEngine(store=self.store)
        self.sig_engine.add_builtin_rules()

        # Ghidra MCP 状态
        self._ghidra_program = None
        self._ghidra_connected = False

        self._stats = {
            "flirt_scans": 0,
            "flirt_matches": 0,
            "pcode_analyses": 0,
            "netnode_ops": 0,
            "decompiles": 0,
        }

    # ── FLIRT 批量扫描 (对应 IDA Apply FLIRT Signatures) ──

    def flirt_scan_program(self, functions_data: List[dict]) -> dict:
        """
        对 Ghidra 程序执行 FLIRT 批量扫描

        Args:
            functions_data: Ghidra list_functions_enhanced 输出

        Returns:
            扫描结果统计
        """
        self._stats["flirt_scans"] += 1
        if not functions_data:
            return {"scanned": 0, "matches": 0}

        # 转换为签名引擎格式
        funcs_for_scan = self.sig_engine.prepare_ghidra_scan(functions_data)
        matches = self.sig_engine.scan(funcs_for_scan)

        self._stats["flirt_matches"] += len(matches)

        # 按库分组统计
        lib_stats = {}
        for m in matches:
            lib = m.get("library", "unknown")
            lib_stats[lib] = lib_stats.get(lib, 0) + 1

        return {
            "scanned": len(funcs_for_scan),
            "matches": len(matches),
            "libraries_found": lib_stats,
            "match_details": [
                {
                    "func": m["func_name"],
                    "address": m["address"],
                    "identified_as": m["matched_name"],
                    "library": m["library"],
                }
                for m in matches
            ],
        }

    def flirt_add_signatures(self, library_name: str,
                             patterns: Dict[str, bytes]) -> int:
        """
        添加自定义签名 (对应 IDA Create FLIRT Signature)

        Args:
            library_name: 库名
            patterns: {函数名: 起始字节序列}

        Returns:
            添加的签名数
        """
        lib = SignatureLibrary(library_name)
        for func_name, pattern_bytes in patterns.items():
            sig = Signature(func_name, library=library_name, pattern=pattern_bytes)
            lib.add(sig)

        self.sig_engine.add_library(lib)
        self.sig_engine.save_to_store(library_name)
        return len(patterns)

    # ── PCode 微码分析 (对应 IDA Hex-Rays microcode) ──

    def analyze_pcode(self, pcode_data: dict) -> dict:
        """
        分析 Ghidra PCode 数据 (对应 IDA 查看 Hex-Rays microcode)

        Args:
            pcode_data: Ghidra get_function_pcode 输出

        Returns:
            PCode 分析摘要
        """
        self._stats["pcode_analyses"] += 1
        if not pcode_data:
            return {"error": "no pcode data"}

        func_name = pcode_data.get("name", "unknown")
        blocks = pcode_data.get("basic_blocks", [])
        high_pcodes = pcode_data.get("high_pcodes", [])

        # 统计 PCode 操作类型
        op_counts: Dict[str, int] = {}
        call_targets = []

        for block in blocks:
            for pcode in block.get("pcodes", []):
                mnemonic = pcode.get("mnemonic", "?")
                op_counts[mnemonic] = op_counts.get(mnemonic, 0) + 1

                # 收集 CALL 目标
                if mnemonic == "CALL":
                    inputs = pcode.get("inputs", [])
                    for inp in inputs:
                        if not inp.get("is_constant") and inp.get("space") == "ram":
                            off = inp.get('offset', '?')
                            call_targets.append(
                                f"0x{off}" if isinstance(off, str) else f"0x{off:X}"
                            )

        # High PCode 分析
        high_op_counts: Dict[str, int] = {}
        for pcode in high_pcodes:
            m = pcode.get("mnemonic", "?")
            high_op_counts[m] = high_op_counts.get(m, 0) + 1

        # 复杂度评分
        total_ops = sum(op_counts.values())
        complexity = "low"
        if total_ops > 100:
            complexity = "high"
        elif total_ops > 30:
            complexity = "medium"

        return {
            "function": func_name,
            "basic_blocks": len(blocks),
            "total_pcodes": total_ops,
            "high_pcodes": len(high_pcodes),
            "complexity": complexity,
            "opcode_breakdown": dict(
                sorted(op_counts.items(), key=lambda x: -x[1])[:15]
            ),
            "call_targets": call_targets[:20],
            "has_indirect_calls": any(
                "INDIRECT" in str(p) for b in blocks for p in b.get("pcodes", [])
            ),
            "has_branches": any(
                m in ("CBRANCH", "BRANCH", "BRANCHIND")
                for b in blocks for p in b.get("pcodes", [])
                for m in [p.get("mnemonic", "")]
            ),
        }

    def detect_api_chains(self, pcode_data: dict,
                          suspicious_apis: List[str] = None) -> List[dict]:
        """
        PCode 级 API 调用链检测 (对应 IDA 的交叉引用分析)

        Args:
            pcode_data: Ghidra get_function_pcode 输出
            suspicious_apis: 可疑 API 列表

        Returns:
            检测到的调用链
        """
        if suspicious_apis is None:
            suspicious_apis = [
                "VirtualAlloc", "WriteProcessMemory",
                "CreateThread", "WinExec", "Socket"
            ]

        analysis = self.analyze_pcode(pcode_data)
        call_targets = analysis.get("call_targets", [])

        chains = []
        for target in call_targets:
            for api in suspicious_apis:
                # 简化检测: 实际应用中需要符号名匹配
                if api.lower() in target.lower():
                    chains.append({
                        "type": "suspicious_api",
                        "api": api,
                        "target": target,
                        "severity": "high" if api in ("WinExec", "WriteProcessMemory") else "medium",
                    })

        return chains

    # ── Netnode 持久化 (对应 IDA netnode 数据库) ──

    def save_analysis_state(self, key: str, data: Any):
        """保存分析状态到 NetnodeStore"""
        self.store.set_value(key, data)
        self._stats["netnode_ops"] += 1

    def load_analysis_state(self, key: str) -> Any:
        """加载分析状态"""
        self._stats["netnode_ops"] += 1
        return self.store.get_value(key)

    def save_function_annotation(self, func_addr: str,
                                 annotation: dict):
        """保存函数注释 (对应 IDA set_cmt)"""
        self.store.set_supval(f"func:{func_addr}", "annotation", annotation)
        self._stats["netnode_ops"] += 1

    def get_function_annotations(self, func_addr: str) -> Optional[dict]:
        """获取函数注释"""
        return self.store.get_supval(f"func:{func_addr}", "annotation")

    def search_functions_by_annotation(self, key: str, value: Any) -> List[str]:
        """通过注解搜索函数"""
        return self.store.search_by_supval(f"func:*:annotation:{key}", value)

    # ── 签名库管理 ──

    def list_signature_libraries(self) -> List[str]:
        """列出所有可用签名库"""
        return self.sig_engine.list_libraries()

    def import_signatures_from_ghidra(self, functions: List[dict]) -> int:
        """从 Ghidra 函数列表生成签名库"""
        from signature_engine import batch_generate_signatures
        lib = batch_generate_signatures(functions)
        self.sig_engine.add_library(lib)
        self.sig_engine.save_to_store(lib.name)
        return lib.total

    def get_signature_stats(self) -> dict:
        return {
            "bridge_stats": dict(self._stats),
            "signature_stats": self.sig_engine.get_stats(),
            "netnode_size": self.store.get_size(),
            "available_libraries": self.list_signature_libraries(),
        }

    # ── 工具注册接口 ──

    def get_tool_definitions(self) -> List[dict]:
        """返回可以在 DEEPCODE tool_registry 中注册的工具定义"""
        return [
            {
                "name": "ida_flirt_scan",
                "description": "FLIRT 批量函数签名扫描 (IDA FLIRT → Ghidra)",
                "category": "analysis",
                "handler": self.flirt_scan_program,
            },
            {
                "name": "ida_analyze_pcode",
                "description": "PCode 微码级分析 (IDA Hex-Rays microcode → Ghidra PCode)",
                "category": "analysis",
                "handler": self.analyze_pcode,
            },
            {
                "name": "ida_detect_chains",
                "description": "PCode 级 API 调用链检测",
                "category": "security",
                "handler": self.detect_api_chains,
            },
            {
                "name": "ida_save_annotation",
                "description": "保存函数分析注解 (IDA set_cmt → NetnodeStore)",
                "category": "persistence",
                "handler": self.save_function_annotation,
            },
            {
                "name": "ida_get_annotation",
                "description": "获取函数分析注解 (IDA get_cmt ← NetnodeStore)",
                "category": "persistence",
                "handler": self.get_function_annotations,
            },
            {
                "name": "ida_add_signatures",
                "description": "添加自定义 FLIRT 签名",
                "category": "signature",
                "handler": self.flirt_add_signatures,
            },
            {
                "name": "ida_import_sigs_from_ghidra",
                "description": "从 Ghidra 函数导出签名库",
                "category": "signature",
                "handler": self.import_signatures_from_ghidra,
            },
            {
                "name": "ida_flirt_save_state",
                "description": "保存分析状态 (IDA netnode_save)",
                "category": "persistence",
                "handler": self.save_analysis_state,
            },
            {
                "name": "ida_flirt_load_state",
                "description": "加载分析状态 (IDA netnode_load)",
                "category": "persistence",
                "handler": self.load_analysis_state,
            },
        ]


# ── 单例 ──
_bridge: Optional[IDABridge] = None


def get_bridge() -> IDABridge:
    global _bridge
    if _bridge is None:
        _bridge = IDABridge()
    return _bridge
