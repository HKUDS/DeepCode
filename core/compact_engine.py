#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CompactEngine v3.0 — 内核压缩管线
=======================================================
补齐全部关键差距:
  ✅ #2 工具链完整性 — 保护 tool_call/result 配对，白名单压缩
  ✅ #3 消息正规化 — normalize_messages_for_api() 过滤+维护 messageParams
  ✅ #4 真实 Token 计数 — tiktoken (cl100k_base) 优先 + 启发式回退
  ✅ #1 LLM 生成摘要 — forked sub-agent 用 Flash 模型做理解式摘要
  ✅ #6 连续失败熔断 — 3次上限, 成功重置

触发方式:
  - PostToolUse hook → microCompact (白名单工具结果压缩)
  - Stop hook → autoCompact → compactConversation
  - MCP 工具 → 手动 deep / sessionMemory compact

用法:
  from compact_engine import CompactEngine
  engine = CompactEngine(session_file="path/to/session.jsonl")
  result = engine.monitor()
"""

import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


# ══════════════════════════════════════════════
# Token 计数 v3 — tiktoken 优先 + 启发式回退
# ══════════════════════════════════════════════

_TOKENIZER = None

def _get_tokenizer():
    """延迟加载 tiktoken (DeepSeek 兼容 cl100k_base)"""
    global _TOKENIZER
    if _TOKENIZER is None:
        try:
            import tiktoken
            _TOKENIZER = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _TOKENIZER = False
    return _TOKENIZER if _TOKENIZER is not False else None


def count_tokens(text: str) -> int:
    """精确 Token 计数 — tiktoken 优先, 启发式回退"""
    if not text:
        return 0
    tok = _get_tokenizer()
    if tok:
        try:
            return len(tok.encode(text))
        except Exception:
            pass
    # 启发式回退
    chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    code_indicators = sum(1 for c in text if c in '{}[]()<>:=+-*/|&!@#$%^.,;`')
    indent_lines = len(re.findall(r'^[ \t]{4,}', text, re.MULTILINE))
    other = len(text) - chinese
    is_code = bool(indent_lines > 3 or code_indicators > len(text) * 0.05)
    coeff = 1.5 if is_code else 1.0
    return int(chinese * 2.0 + other * 0.4 * coeff)


# 向后兼容别名
estimate_tokens = count_tokens


def format_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n/1000:.1f}K"
    return str(n)


# ══════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════

COMPACT_LEVELS = {
    "micro":   {"threshold": 0.70, "keep_turns": 5,  "desc": "白名单工具结果替换"},
    "compact": {"threshold": 0.85, "keep_turns": 3,  "desc": "LLM 理解式摘要 + compacted标记"},
    "deep":    {"threshold": 0.95, "keep_turns": 1,  "desc": "激进压缩 + sessionMemory"},
}

DEFAULT_CONTEXT_WINDOW = 32000
MODEL_OUTPUT_RESERVE = 8192            # 模型输出预留
MAX_CONSECUTIVE_FAILURES = 3            # 连续失败上限 (3)
MAX_COMPACT_PER_SESSION = 30            # 单会话上限
MAX_COMPACT_BUDGET_RATIO = 0.25         # 摘要消耗 <= 释放的 25%
LARGE_TOOL_THRESHOLD_CHARS = 2000
MAX_SUMMARY_LENGTH = 800

# 只压缩这些工具类型的结果
COMPACTABLE_TOOLS = {
    "read", "Read", "mcp__filesystem__read_file", "mcp__filesystem__read_text_file",
    "bash", "Bash",
    "Grep", "grep", "rg",
    "Glob", "glob",
    "WebSearch", "WebFetch",
    "mcp__fetch__fetch",
    "Write", "Edit",
    "mcp__sqlite__query", "mcp__duckdb__execute_query",
    "mcp__tushareMcp__daily", "mcp__tushareMcp__income",
    "mcp__tushareMcp__stock_basic", "mcp__tushareMcp__index_daily",
}

# LLM 摘要模型 (便宜快速)
SUMMARY_MODEL = "deepseek-v4-flash"
SUMMARY_MAX_TOKENS = 1000
SUMMARY_TEMPERATURE = 0.3

# ══════════════════════════════════════════════
# TextRank 摘要 (回退方案)
# ══════════════════════════════════════════════

def _split_sentences(text: str) -> list:
    text = text.replace('\n\n', '。').replace('\n', '。')
    raw = re.split(r'(?<=[。！？.!?])\s*', text)
    result = []
    for s in raw:
        s = s.strip()
        if len(s) >= 3 and re.search(r'[\u4e00-\u9fff\w]', s):
            result.append(s)
    return result if result else [text]


def _tokenize_cn(text: str) -> set:
    cn = re.findall(r'[\u4e00-\u9fff]', text)
    bigrams = {cn[i]+cn[i+1] for i in range(len(cn)-1)}
    words = set(re.findall(r'[a-zA-Z]+|\d+', text.lower()))
    return bigrams | words


def textrank_summarize(text: str, target_ratio: float = 0.3) -> str:
    sentences = _split_sentences(text)
    if len(sentences) <= 3:
        return text
    tokenized = [_tokenize_cn(s) for s in sentences]
    n = len(sentences)
    sim = [[0.0]*n for _ in range(n)]
    for i in range(n):
        for j in range(i+1, n):
            inter = len(tokenized[i] & tokenized[j])
            union = len(tokenized[i] | tokenized[j])
            s = inter/union if union>0 else 0
            sim[i][j] = s; sim[j][i] = s
    for i in range(n):
        rs = sum(sim[i])
        if rs>0:
            for j in range(n): sim[i][j] /= rs
    d=0.85; scores=[1.0/n]*n
    for _ in range(50):
        ns=[(1-d)/n + d*sum(sim[j][i]*scores[j] for j in range(n)) for i in range(n)]
        if sum(abs(ns[i]-scores[i]) for i in range(n))<1e-6: break
        scores=ns
    nk=max(1,int(n*target_ratio))
    ranked=sorted(range(n),key=lambda i:scores[i],reverse=True)[:nk]
    ranked.sort()
    return ''.join(sentences[i] for i in ranked)


# ══════════════════════════════════════════════
# 结构化数据压缩
# ══════════════════════════════════════════════

def _is_json_data(text: str) -> bool:
    s = text.strip()
    return (s.startswith('{') and s.endswith('}')) or (s.startswith('[') and s.endswith(']'))


def _compress_json(text: str, max_items: int = 5) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return textrank_summarize(text, 0.3)
    if isinstance(data, list):
        total = len(data)
        if total == 0: return "[空数组]"
        if isinstance(data[0], dict):
            known = ['ts_code','trade_date','name','close','open','high','low',
                     'pct_chg','vol','amount','symbol','price','value','net_amount','rank']
            keys = [k for k in data[0] if k in known][:6] or list(data[0].keys())[:4]
            sample = data[:max_items]
            h = " | ".join(keys)
            rows = "\n".join(f"  {' | '.join(str(item.get(k,'-')) for k in keys)}" for item in sample)
            return f"[数据摘要] 共{total}条, 显示前{max_items}:\n  {h}\n{rows}\n  ...还有{total-max_items}条"
        return f"[数组摘要] 共{total}项: {data[:max_items]}..."
    if isinstance(data, dict):
        ik = 'items' if 'items' in data else 'data'
        if ik in data:
            items = data.get(ik,[])
            fields = data.get('fields',[])
            total = len(items) if isinstance(items,list) else 0
            if total>0:
                if not fields and isinstance(items[0],dict): fields = list(items[0].keys())
                elif not fields and isinstance(items[0],list): fields = [f"c{i}" for i in range(len(items[0]))]
                sample = items[:max_items]
                hdr = " | ".join(fields[:6])
                rows = "\n".join(f"  {' | '.join(str(v) for v in (it[:6] if isinstance(it,list) else [it.get(f,'-') for f in fields[:6]]))}" for it in sample)
                return f"[数据] 共{total}条, 字段:{','.join(fields[:8])}...\n  {hdr}\n{rows}\n  ...还有{total-max_items}条"
        return f"[对象] {len(data)}键: {list(data.keys())[:10]}..."
    return textrank_summarize(text, 0.3)


# ══════════════════════════════════════════════
# LLM 理解式摘要 (对齐 compactConversation)
# ══════════════════════════════════════════════

LLM_SUMMARY_PROMPT = """You are a context compaction assistant. Summarize the following conversation history concisely. 

CRITICAL RULES:
1. Preserve all key facts, decisions, file paths, code patterns, error messages, and user preferences
2. For tool results: capture what was found/changed, keep file paths and key data
3. For assistant responses: capture the reasoning, decisions made, and key findings
4. For system/skill messages: capture the skill name and key configuration
5. Be dense and factual - every word should carry information
6. Output ONLY the summary text, no preamble, no markdown headings
7. If the content is already short (<200 chars), return it unchanged

Conversation to summarize:
---
{conversation}
---

Summary:"""


def _llm_summarize(conversation_text: str) -> Optional[str]:
    """用 Flash 模型生成理解式摘要，失败返回 None"""
    if len(conversation_text) < 500:
        return conversation_text  # 太短不需要 LLM

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return None

    # 截断输入以防超出模型上下文
    max_input = 12000
    if len(conversation_text) > max_input:
        conversation_text = conversation_text[:max_input//2] + \
            "\n...[中间省略]...\n" + conversation_text[-max_input//2:]

    prompt = LLM_SUMMARY_PROMPT.format(conversation=conversation_text)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        response = client.chat.completions.create(
            model=SUMMARY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=SUMMARY_MAX_TOKENS,
            temperature=SUMMARY_TEMPERATURE,
        )
        summary = response.choices[0].message.content
        if summary and len(summary.strip()) > 20:
            return summary.strip()
    except Exception:
        pass
    return None


def _make_summary(content: str, use_llm: bool = False,
                  conversation_context: str = "") -> str:
    """生成摘要 — LLM 优先, TextRank/结构化回退"""
    content = content.strip()
    if not content:
        return ""
    chars = len(content)
    if chars <= LARGE_TOOL_THRESHOLD_CHARS:
        return content

    # LLM 路径 (compact/deep 级别)
    if use_llm and conversation_context:
        llm_input = conversation_context + "\n---\n" + content[:8000]
        llm_result = _llm_summarize(llm_input)
        if llm_result:
            if len(llm_result) > MAX_SUMMARY_LENGTH * 2:
                llm_result = llm_result[:MAX_SUMMARY_LENGTH * 2] + "..."
            return llm_result

    # 结构化数据路径
    if _is_json_data(content):
        return _compress_json(content)

    # TextRank 回退
    ratio = 0.2 if chars > 10000 else 0.3
    summary = textrank_summarize(content, ratio)
    if len(summary) > MAX_SUMMARY_LENGTH * 2:
        summary = summary[:MAX_SUMMARY_LENGTH * 2] + "\n...[截断]"
    return summary


# ══════════════════════════════════════════════
# Session Memory — 跨会话事实持久化
# ══════════════════════════════════════════════

class CompactMemory:
    """
    跨会话记忆存储 — 对齐 SessionMemory 设计。
    在 deep 压缩时提取关键事实到 ~/.deepcode/compact_memory.json。
    """

    def __init__(self, project_root: str = None):
        home = os.environ.get("HOME", os.environ.get("USERPROFILE", ""))
        self._store_path = Path(home) / ".deepcode" / "compact_memory.json"
        self._project = project_root or os.getcwd()
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if self._store_path.exists():
            try:
                return json.loads(self._store_path.read_text(encoding='utf-8'))
            except Exception:
                pass
        return {"sessions": {}, "facts": []}

    def _save(self):
        self._store_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding='utf-8')

    def extract_facts(self, summary_text: str, session_id: str = "") -> list:
        """从压缩摘要中提取结构化事实"""
        facts = []
        # 提取文件路径
        paths = re.findall(r'(?:[A-Z]:)?[/\\][\w./\\-]+\.\w{1,6}', summary_text)
        for p in set(paths[:5]):
            if os.path.exists(p):
                facts.append({"type": "file", "path": p, "session": session_id})
        # 提取决策关键句
        decisions = re.findall(
            r'(?:决定|决策|关键|重要|必须|禁止|应该).*?[。.]', summary_text)
        for d in decisions[:3]:
            facts.append({"type": "decision", "text": d, "session": session_id})
        return facts

    def save_session(self, session_id: str, compact_result: dict):
        """保存压缩后的会话事实到持久化存储"""
        summary = compact_result.get("compact_context", "")
        facts = self.extract_facts(summary, session_id)

        self._data.setdefault("sessions", {})[session_id] = {
            "last_compact": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": compact_result.get("level", "?"),
            "tokens_saved": compact_result.get("tokens_saved", 0),
            "compacted_messages": compact_result.get("compacted_messages", 0),
            "fact_count": len(facts),
        }
        for f in facts:
            self._data["facts"].append(f)
        self._data["facts"] = self._data["facts"][-200:]  # 保留最近 200 条
        self._save()

    def load_context(self, session_id: str = "") -> str:
        """加载跨会话记忆作为附加上下文"""
        parts = []
        if session_id and session_id in self._data.get("sessions", {}):
            s = self._data["sessions"][session_id]
            parts.append(
                f"[此前压缩] {s['last_compact']}: "
                f"节省 {format_tokens(s['tokens_saved'])}t, "
                f"压缩 {s['compacted_messages']} 条消息")

        recent_facts = self._data.get("facts", [])[-20:]
        if recent_facts:
            parts.append("[跨会话记忆]")
            for f in recent_facts:
                if f.get("type") == "file":
                    parts.append(f"  文件: {f['path']}")
                elif f.get("type") == "decision":
                    parts.append(f"  决策: {f['text'][:120]}")

        return "\n".join(parts) if parts else ""


# ══════════════════════════════════════════════
# CompactEngine v3.1 核心
# ══════════════════════════════════════════════

class CompactEngine:
    """
    内部压缩引擎。

    三级管线:
      micro   → 白名单工具结果替换 (PostToolUse hook)
      compact → LLM 理解式摘要 + compacted:true 标记 (Stop hook)
      deep    → 激进压缩 + sessionMemory 提取

    安全:
      - 连续失败熔断 (3次上限, 成功重置)
      - 工具链完整性保护 (tool_call/result 不拆散)
      - 1/n 消耗控制
    """

    def __init__(
        self,
        session_file: Optional[str] = None,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        keep_turns: int = 3,
        use_llm: bool = True,
        project_root: str = None,
    ):
        self.session_file = session_file or self._find_session_file()
        self.context_window = context_window
        self.keep_turns = keep_turns
        self.use_llm = use_llm
        self.compact_count = 0
        self._consecutive_failures = 0
        self._circuit_broken = False
        self._warning_suppressed = False
        self.last_compact_at: Optional[str] = None
        self.last_boundary_id: Optional[str] = None

        # Session Memory
        self.memory = CompactMemory(project_root=project_root)

        # Pre/Post compact hooks
        self._pre_hooks: list = []
        self._post_hooks: list = []

        self.stats = {
            "total_compacts": 0,
            "micro_compacts": 0,
            "compact_compacts": 0,
            "deep_compacts": 0,
            "tokens_saved_total": 0,
            "messages_compacted": 0,
            "facts_extracted": 0,
        }

    # ═══ 辅助 ═══

    def _find_session_file(self) -> Optional[str]:
        env = os.environ.get("DEEPCODE_SESSION_FILE")
        if env and os.path.exists(env):
            return env
        home = os.environ.get("HOME", os.environ.get("USERPROFILE", ""))
        proj = Path(home) / ".deepcode" / "projects"
        if proj.is_dir():
            for pd in proj.iterdir():
                if pd.is_dir():
                    files = sorted(pd.glob("*.jsonl"),
                                   key=lambda f: f.stat().st_mtime, reverse=True)
                    if files:
                        return str(files[0])
        return None

    def _read_messages(self) -> List[dict]:
        if not self.session_file or not os.path.exists(self.session_file):
            return []
        msgs = []
        with open(self.session_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try:
                    msgs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return msgs

    def _write_messages(self, messages: List[dict]):
        tmp = self.session_file + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            for m in messages:
                f.write(json.dumps(m, ensure_ascii=False) + '\n')
        os.replace(tmp, self.session_file)

    def _extract_content(self, msg: dict) -> str:
        c = msg.get('content', '')
        if isinstance(c, list):
            parts = []
            for block in c:
                if isinstance(block, dict):
                    if block.get('type') == 'text':
                        parts.append(str(block.get('text', '')))
                    elif block.get('type') == 'tool_use':
                        parts.append(f"[tool_use: {block.get('name','?')}]")
            return ' '.join(parts)
        return str(c) if c else ''

    def _get_tool_name(self, msg: dict) -> str:
        meta = msg.get('meta', {})
        func = meta.get('function', {})
        return func.get('name', msg.get('name', 'unknown'))

    def get_effective_window(self) -> int:
        """有效上下文窗口 — 扣除模型输出预留"""
        return max(self.context_window - MODEL_OUTPUT_RESERVE, 4096)

    # ═══ 水位 ═══

    def get_watermark(self) -> float:
        messages = self._read_messages()
        if not messages: return 0.0
        total = sum(count_tokens(self._extract_content(m)) for m in messages)
        return min(total / self.get_effective_window(), 1.0)

    def get_token_usage(self) -> int:
        return sum(count_tokens(self._extract_content(m)) for m in self._read_messages())

    # ═══ 工具链分组 ═══

    @staticmethod
    def _build_tool_chains(messages: List[dict]) -> List[List[dict]]:
        """
        构建工具调用链 — assistant(tool_calls) + tool(results) 不拆散。
        返回: [[msg1, msg2, ...], ...] 每个chain是不可分割的单元。
        """
        chains = []
        current_chain = []
        in_tool_block = False

        for msg in messages:
            role = msg.get('role', '')
            if role == 'assistant':
                mp = msg.get('messageParams', {}) or {}
                has_tool_calls = bool(mp.get('tool_calls'))
                if has_tool_calls:
                    # 开始新的工具调用块
                    if current_chain and not in_tool_block:
                        chains.append(current_chain)
                    current_chain = [msg]
                    in_tool_block = True
                else:
                    if in_tool_block:
                        current_chain.append(msg)
                    else:
                        if current_chain:
                            chains.append(current_chain)
                        current_chain = [msg]
                        chains.append(current_chain)
                        current_chain = []
                        continue
            elif role == 'tool' and in_tool_block:
                current_chain.append(msg)
            elif role == 'tool' and not in_tool_block:
                # 孤立的 tool 结果
                if current_chain:
                    chains.append(current_chain)
                chains.append([msg])
                current_chain = []
                continue
            else:
                if in_tool_block:
                    in_tool_block = False
                if current_chain:
                    chains.append(current_chain)
                    current_chain = []
                chains.append([msg])
                continue

        if current_chain:
            chains.append(current_chain)
        return chains

    # ═══ 监控 ═══

    def monitor(self) -> dict:
        watermark = self.get_watermark()
        if self._circuit_broken:
            return {"action": "none", "watermark": watermark, "reason": "circuit_breaker_open"}
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self._circuit_broken = True
            return {"action": "none", "watermark": watermark, "reason": "max_consecutive_failures"}

        if watermark >= COMPACT_LEVELS["deep"]["threshold"]:
            level = "deep"
        elif watermark >= COMPACT_LEVELS["compact"]["threshold"]:
            level = "compact"
        elif watermark >= COMPACT_LEVELS["micro"]["threshold"]:
            level = "micro"
        else:
            return {"action": "none", "watermark": watermark, "level": "safe"}

        return self.compact(level)

    # ═══ 压缩核心 ═══

    def compact(self, level: str) -> dict:
        cfg = COMPACT_LEVELS.get(level, COMPACT_LEVELS["compact"])
        keep_turns = cfg["keep_turns"]
        use_llm = self.use_llm and level in ("compact", "deep")

        # ── Pre-compact hooks ──
        hook_ctx = {"level": level, "session_file": self.session_file,
                     "watermark": self.get_watermark()}
        for hook in self._pre_hooks:
            try:
                hook(hook_ctx)
            except Exception:
                pass

        messages = self._read_messages()
        if not messages:
            return {"action": "none", "error": "no_messages"}

        # ── 分轮次 ──
        turns: List[Tuple[Optional[dict], List[dict]]] = []
        cur_user = None; cur_msgs = []
        for m in messages:
            r = m.get('role','')
            if r == 'user':
                if cur_user is not None: turns.append((cur_user, cur_msgs))
                cur_user = m; cur_msgs = []
            elif r in ('assistant','tool'):
                cur_msgs.append(m)
            elif r == 'system' and cur_user is None:
                if not turns: turns.append((None,[m]))
                else: turns[0][1].append(m)
        if cur_user is not None: turns.append((cur_user, cur_msgs))

        n_turns = len(turns)
        recent_start = max(0, n_turns - keep_turns)
        if n_turns <= keep_turns:
            return {"action": "none", "reason": "too_few_turns",
                    "turns": n_turns, "keep_turns": keep_turns}

        # ── 构建旧轮次的 LLM 上下文 + 工具链 ──
        orig_tokens = self.get_token_usage()
        context_parts = []
        chains_to_compact = []  # (msg_indices, chain_summary_target)

        for i, (user_msg, at_msgs) in enumerate(turns):
            if i >= recent_start:
                continue
            if user_msg:
                uc = self._extract_content(user_msg)
                context_parts.append(f"[用户]: {uc[:300]}")
            # 在旧轮次中，整条工具链一起处理
            chains = self._build_tool_chains(at_msgs)
            for chain in chains:
                chain_text = "\n".join(
                    f"[{m.get('role')}/{self._get_tool_name(m)}]: {self._extract_content(m)[:500]}"
                    for m in chain
                )
                context_parts.append(chain_text)
                chains_to_compact.append(chain)

        conversation_context = "\n".join(context_parts)

        # ── 生成摘要 + 标记 ──
        compacted_indices = []
        summary_parts = []
        saved_tokens = 0

        for chain in chains_to_compact:
            chain_text = "\n".join(self._extract_content(m) for m in chain)
            orig_t = sum(count_tokens(self._extract_content(m)) for m in chain)

            if len(chain_text) <= LARGE_TOOL_THRESHOLD_CHARS:
                continue

            # 检查是否是可压缩的工具类型
            tool_names = [self._get_tool_name(m) for m in chain if m.get('role') == 'tool']
            assistant_has_calls = any(
                m.get('role') == 'assistant' and
                (m.get('messageParams', {}) or {}).get('tool_calls')
                for m in chain
            )

            if tool_names and not any(tn in COMPACTABLE_TOOLS for tn in tool_names):
                continue  # 非白名单工具, 保持完整

            summary = _make_summary(
                chain_text,
                use_llm=use_llm,
                conversation_context=conversation_context,
            )
            comp_t = count_tokens(summary)
            saved = orig_t - comp_t
            if saved <= 0:
                continue

            # LLM 消耗控制
            if use_llm and _llm_summarize != _make_summary:
                summary_cost = count_tokens(summary)
                if summary_cost > saved * MAX_COMPACT_BUDGET_RATIO:
                    continue

            label = " + ".join(set(
                self._get_tool_name(m) for m in chain
                if self._get_tool_name(m) != 'unknown'
            )) or "tool_chain"

            summary_parts.append({
                "label": label,
                "summary": summary,
                "tokens_saved": saved,
            })
            saved_tokens += saved

            # 标记整条链的所有消息
            for cm in chain:
                for j, om in enumerate(messages):
                    if om.get('id') == cm.get('id'):
                        compacted_indices.append(j)
                        break

        # ── 消耗控制 ──
        if not compacted_indices:
            self._consecutive_failures += 1
            return {"action": "none", "reason": "nothing_to_compact",
                    "consecutive_failures": self._consecutive_failures}

        # ── 标记 + 注入摘要 + Compact Boundary ──
        boundary_id = str(uuid.uuid4())
        for idx in compacted_indices:
            messages[idx]["compacted"] = True
            messages[idx]["updateTime"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z")

        # Compact boundary: 可见的压缩边界标记 (UI可用)
        boundary_msg = {
            "id": boundary_id,
            "sessionId": messages[0].get("sessionId", ""),
            "role": "system",
            "content": f"── COMPACT BOUNDARY ({level.upper()}) ──",
            "contentParams": None,
            "messageParams": {"compact_boundary": True, "level": level},
            "compacted": False,
            "visible": True,
            "createTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "updateTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }
        messages.append(boundary_msg)
        self.last_boundary_id = boundary_id

        summary_content = (
            f"[COMPACT {level.upper()}] 已将前 {n_turns - keep_turns} 轮对话压缩 "
            f"(节省 ~{format_tokens(saved_tokens)}t):\n"
        )
        for sp in summary_parts:
            summary_content += (
                f"  [{sp['label']}]: {sp['summary'][:250]}"
                f"{'...' if len(sp['summary']) > 250 else ''}\n"
            )

        summary_msg = {
            "id": str(uuid.uuid4()),
            "sessionId": messages[0].get("sessionId", ""),
            "role": "system",
            "content": summary_content,
            "contentParams": None,
            "messageParams": None,
            "compacted": False,
            "visible": False,
            "createTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "updateTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }
        messages.append(summary_msg)

        # ── 写回 ──
        try:
            self._write_messages(messages)
        except Exception as e:
            self._consecutive_failures += 1
            return {"action": "error", "error": str(e)}

        # ── 成功: 重置熔断 + Warning Suppression ──
        self._consecutive_failures = 0
        self._warning_suppressed = True
        self.compact_count += 1
        self.last_compact_at = datetime.now().isoformat()
        self.stats["total_compacts"] += 1
        self.stats[f"{level}_compacts"] += 1
        self.stats["tokens_saved_total"] += saved_tokens
        self.stats["messages_compacted"] += len(compacted_indices)

        # Session Memory: deep 压缩时持久化事实
        if level == "deep" and summary_parts:
            all_summaries = "\n".join(sp["summary"] for sp in summary_parts)
            facts = self.memory.extract_facts(
                all_summaries, messages[0].get("sessionId", ""))
            self.stats["facts_extracted"] += len(facts)
            self.memory.save_session(
                messages[0].get("sessionId", ""),
                {"level": level, "tokens_saved": saved_tokens,
                 "compacted_messages": len(compacted_indices),
                 "compact_context": all_summaries})

        if self.compact_count >= MAX_COMPACT_PER_SESSION:
            self._circuit_broken = True

        result = {
            "action": "compact",
            "level": level,
            "compacted_messages": len(compacted_indices),
            "summaries_created": len(summary_parts),
            "tokens_saved": saved_tokens,
            "watermark_before": round(orig_tokens / self.get_effective_window() * 100, 1),
            "watermark_after": round(self.get_watermark() * 100, 1),
            "description": cfg["desc"],
            "llm_used": use_llm,
            "boundary_id": boundary_id,
            "warning_suppressed": True,
            "compact_count": self.compact_count,
        }

        # Post-compact hooks
        for hook in self._post_hooks:
            try:
                hook(result)
            except Exception:
                pass

        return result

    # ═══ headroom (只读) ═══

    def headroom(self) -> dict:
        messages = self._read_messages()
        if not messages: return {"action": "none", "error": "no_messages"}
        active = [m for m in messages if not m.get("compacted", False)]
        total_t = sum(count_tokens(self._extract_content(m)) for m in messages)
        active_t = sum(count_tokens(self._extract_content(m)) for m in active)
        usage = round(active_t / self.get_effective_window() * 100, 1)
        if usage >= 95: action = "critical"
        elif usage >= 85: action = "compress"
        elif usage >= 70: action = "warn"
        else: action = "ok"
        return {
            "action": action,
            "total_messages": len(messages),
            "compacted_messages": len(messages)-len(active),
            "active_messages": len(active),
            "original_tokens": total_t,
            "compressed_tokens": active_t,
            "saved_tokens": total_t-active_t,
            "context_usage_pct": usage,
            "context_window": self.context_window,
            "effective_window": self.get_effective_window(),
            "instruction": {
                "ok": "上下文充裕", "warn": "建议精简工具调用",
                "compress": "必须压缩旧轮次", "critical": "紧急！立即 deep compact",
            }.get(action, ""),
        }

    # ═══ normalize_messages_for_api ═══

    def normalize_messages_for_api(self, messages: List[dict] = None) -> List[dict]:
        """
        将消息正规化为 API 请求格式 — 对齐 normalizeMessagesForAPI。
        1. 过滤 compacted:true 消息
        2. 保留工具链完整性
        3. 格式化 content 为 API 兼容格式
        """
        if messages is None:
            messages = self._read_messages()

        # 过滤
        active = [m for m in messages if not m.get("compacted", False)]

        # 构建工具链分组后重建
        result = []
        i = 0
        while i < len(active):
            msg = active[i]
            role = msg.get('role', '')
            mp = msg.get('messageParams', {}) or {}

            if role == 'assistant' and mp.get('tool_calls'):
                # 工具调用块: assistant + 后续 tool 结果
                block = [msg.copy()]
                i += 1
                while i < len(active) and active[i].get('role') == 'tool':
                    block.append(active[i].copy())
                    i += 1
                result.extend(block)
            else:
                result.append(msg.copy())
                i += 1

        return result

    # ═══ 状态 ═══

    def status(self) -> dict:
        wm = self.get_watermark()
        tu = self.get_token_usage()
        return {
            "watermark": f"{wm:.1%}",
            "token_usage": tu,
            "context_window": self.context_window,
            "effective_window": self.get_effective_window(),
            "compact_count": self.compact_count,
            "consecutive_failures": self._consecutive_failures,
            "circuit_broken": self._circuit_broken,
            "warning_suppressed": self._warning_suppressed,
            "last_compact_at": self.last_compact_at,
            "last_boundary_id": self.last_boundary_id,
            "stats": self.stats,
            "session_file": self.session_file,
            "tokenizer": "tiktoken" if _get_tokenizer() else "heuristic",
            "memory_facts": len(self.memory._data.get("facts", [])),
        }

    def reset_circuit_breaker(self):
        self._circuit_broken = False
        self._consecutive_failures = 0
        self._warning_suppressed = False
        return {"message": "熔断器已重置", "consecutive_failures": 0}

    def suppress_warnings(self):
        """压缩后抑制重复水位警告"""
        self._warning_suppressed = True

    def is_warning_suppressed(self) -> bool:
        return self._warning_suppressed

    # ═══ 单文本压缩 (PostToolUse hook) ═══

    @staticmethod
    def compress_single(text: str, tool_name: str = "") -> dict:
        """压缩单段文本 — PostToolUse hook 用。只压缩白名单工具。"""
        if not text:
            return {"compressed": "", "original_tokens": 0,
                    "compressed_tokens": 0, "saved_tokens": 0, "ratio": 0}
        orig = count_tokens(text)
        chars = len(text)
        if chars < LARGE_TOOL_THRESHOLD_CHARS:
            return {"compressed": text, "original_tokens": orig,
                    "compressed_tokens": orig, "saved_tokens": 0, "ratio": 0}

        # 白名单检查
        if tool_name and tool_name not in COMPACTABLE_TOOLS:
            return {"compressed": text, "original_tokens": orig,
                    "compressed_tokens": orig, "saved_tokens": 0, "ratio": 0,
                    "reason": "tool_not_in_compactable_list"}

        compressed = _make_summary(text, use_llm=False)
        comp = count_tokens(compressed)
        saved = max(0, orig - comp)
        return {
            "compressed": compressed,
            "original_tokens": orig,
            "compressed_tokens": comp,
            "saved_tokens": saved,
            "ratio": round(saved/orig*100,1) if orig>0 else 0,
        }


# ══════════════════════════════════════════════
# 兼容旧 API
# ══════════════════════════════════════════════

_ENGINE: Optional[CompactEngine] = None

def get_engine(session_file: str = None, context_window: int = DEFAULT_CONTEXT_WINDOW,
               keep_turns: int = 3) -> CompactEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = CompactEngine(session_file=session_file, context_window=context_window,
                                keep_turns=keep_turns)
    return _ENGINE


def headroom(session_file: str = None, **kw) -> dict:
    engine = CompactEngine(session_file=session_file,
                           context_window=kw.get('context_window', DEFAULT_CONTEXT_WINDOW),
                           keep_turns=kw.get('keep_turns', 3))
    return engine.headroom()


# ══════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="CompactEngine v3.0")
    p.add_argument("session", nargs="?")
    p.add_argument("--level", default="compact", choices=["micro","compact","deep"])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--headroom", action="store_true")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--context-window", type=int, default=DEFAULT_CONTEXT_WINDOW)
    args = p.parse_args()

    engine = CompactEngine(session_file=args.session,
                           context_window=args.context_window,
                           use_llm=not args.no_llm)

    if args.status:
        print(json.dumps(engine.status(), indent=2, ensure_ascii=False))
    elif args.headroom:
        print(json.dumps(engine.headroom(), indent=2, ensure_ascii=False))
    elif args.dry_run:
        print("[DRY RUN]")
        print(json.dumps(engine.headroom(), indent=2, ensure_ascii=False))
    else:
        if not engine.session_file:
            print("错误: 未找到会话 JSONL 文件"); sys.exit(1)
        print(f"[CompactEngine v3.0] session={engine.session_file}")
        print(f"[CompactEngine v3.0] level={args.level}, llm={not args.no_llm}")
        r = engine.compact(args.level)
        print(json.dumps(r, indent=2, ensure_ascii=False))
