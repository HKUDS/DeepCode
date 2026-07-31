#!/usr/bin/env python3
"""
DeepCode Knowledge Engine — 统一知识引擎 MCP Server
═══════════════════════════════════════════════════
合并 deepcode-vault (Obsidian式知识库) + deepcode-memory (KV记忆系统)

Vault 工具 (8):
  vault_status, save_analysis, search_notes, read_note, show_graph,
  daily_log, list_templates, sync_to_obsidian

Memory 工具 (6):
  memory_save, memory_load, memory_search, memory_list, memory_forget, memory_stats

MCP 注册:
  "deepcode-knowledge": {
    "command": "python",
    "args": ["F:/DEEPCODE/.deepcode/skills/deepcode-knowledge/knowledge_server.py"]
  }
"""
import json, sys, os, re, time, uuid, shutil, sqlite3
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict

# ── Windows GBK 编码兼容 ──
if sys.platform == "win32" and sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "gb2312", "cp936"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ── 路径配置 ──
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_VAULT = os.path.join(SKILL_DIR, "data", "vault")
NOTES_DIR = os.path.join(DEFAULT_VAULT, "notes")
DAILY_DIR = os.path.join(DEFAULT_VAULT, "daily")
TEMPLATES_DIR = os.path.join(SKILL_DIR, "data", "templates")
DB_PATH = os.path.join(SKILL_DIR, "data", "knowledge.db")
os.makedirs(NOTES_DIR, exist_ok=True)
os.makedirs(DAILY_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# ── Memory Manager 导入 ──
_scripts_dir = os.path.join(os.path.dirname(SKILL_DIR), "..", "..", "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory_manager import MemoryManager
mm = MemoryManager()


# ══════════════════════════════════════════════
# 数据库层 — SQLite 索引
# ══════════════════════════════════════════════

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL, type TEXT DEFAULT 'note',
            tags TEXT DEFAULT '', symbol TEXT DEFAULT '',
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            content_preview TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL, target_id TEXT NOT NULL,
            link_type TEXT DEFAULT 'related',
            UNIQUE(source_id, target_id)
        );
        CREATE TABLE IF NOT EXISTS daily_logs (
            id TEXT PRIMARY KEY, date TEXT UNIQUE NOT NULL,
            summary TEXT DEFAULT '', mood TEXT DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_notes_type ON notes(type);
        CREATE INDEX IF NOT EXISTS idx_notes_symbol ON notes(symbol);
        CREATE INDEX IF NOT EXISTS idx_notes_tags ON notes(tags);
        CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_id);
        CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_id);
    """)
    conn.commit()
    return conn


# ══════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════

def now_ts(): return time.time()
def date_str(): return date.today().isoformat()
def slugify(text):
    s = re.sub(r'[^\w\s-]', '', text).strip().lower()
    return re.sub(r'[-\s]+', '-', s)[:60]
def extract_tags(text):
    return re.findall(r'#([\w\u4e00-\u9fff\-.]+)', text)
def extract_wikilinks(text):
    return re.findall(r'\[\[([^\]]+)\]\]', text)
def extract_frontmatter(text):
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
    if not m: return {}, text
    fm = {}
    for line in m.group(1).strip().split('\n'):
        if ':' in line:
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, text[m.end():]


# ══════════════════════════════════════════════
# 模板引擎
# ══════════════════════════════════════════════

TEMPLATES = {
    "stock": """---
title: "{title}"
type: stock
symbol: {symbol}
tags: {tags}
created: {date}
---

# {title}

## 基本面
{fundamental}

## 技术面
{technical}

## 资金面
{fund_flow}

## 综合判断
{verdict}

## 关联
{links}
""",
    "daily": """---
title: "交易复盘 - {date}"
type: daily
date: {date}
tags: 复盘
---

# 交易复盘 - {date}

## 大盘概况
{market_overview}

## 今日操作
{operations}

## 持仓分析
{holdings}

## 明日计划
{plan}
""",
    "strategy": """---
title: "{title}"
type: strategy
tags: {tags}
created: {date}
---

# {title}

## 策略逻辑
{logic}

## 参数配置
{params}

## 回测结果
{backtest}

## 适用场景
{scenarios}
""",
    "note": """---
title: "{title}"
type: note
tags: {tags}
created: {date}
---

# {title}

{content}

## 相关笔记
{links}
"""
}

def render_template(template_name, **kwargs):
    tpl = TEMPLATES.get(template_name, TEMPLATES["note"])
    kwargs.setdefault("date", date_str())
    kwargs.setdefault("tags", "")
    kwargs.setdefault("links", "")
    for k, v in kwargs.items():
        if v is None: kwargs[k] = ""
    return tpl.format(**kwargs)


# ══════════════════════════════════════════════
# VAULT 工具 (8)
# ══════════════════════════════════════════════

def tool_vault_status(args):
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        by_type = conn.execute("SELECT type, COUNT(*) as cnt FROM notes GROUP BY type").fetchall()
        daily_count = conn.execute("SELECT COUNT(*) FROM daily_logs").fetchone()[0]
        link_count = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
        recent = conn.execute("SELECT title, type, symbol, updated_at FROM notes ORDER BY updated_at DESC LIMIT 5").fetchall()
        return {
            "vault_path": DEFAULT_VAULT,
            "total_notes": total, "daily_logs": daily_count, "total_links": link_count,
            "by_type": {r["type"]: r["cnt"] for r in by_type},
            "recent": [{"title": r["title"], "type": r["type"], "symbol": r["symbol"],
                         "updated": datetime.fromtimestamp(r["updated_at"]).strftime("%m-%d %H:%M")} for r in recent],
            "disk_usage_kb": sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(DEFAULT_VAULT) for f in fs if f.endswith(".md")) // 1024 if os.path.exists(DEFAULT_VAULT) else 0
        }
    finally:
        conn.close()


def tool_save_analysis(args):
    note_type = args.get("type", "note")
    title = args.get("title", "未命名笔记")
    symbol = args.get("symbol", "")
    tags = args.get("tags", "")
    content = args.get("content", {})
    if isinstance(content, str):
        try: content = json.loads(content)
        except: content = {"content": content}

    if note_type == "stock":
        md = render_template("stock", title=title, symbol=symbol,
            tags=tags or f"股票,{symbol}",
            fundamental=content.get("fundamental", ""),
            technical=content.get("technical", ""),
            fund_flow=content.get("fund_flow", ""),
            verdict=content.get("verdict", ""),
            links=content.get("links", ""))
    elif note_type == "strategy":
        md = render_template("strategy", title=title, tags=tags,
            logic=content.get("logic", ""), params=content.get("params", ""),
            backtest=content.get("backtest", ""), scenarios=content.get("scenarios", ""))
    else:
        md = render_template("note", title=title, tags=tags,
            content=content.get("content", str(content)))

    filename = f"{slugify(title)}.md"
    note_path = os.path.join(NOTES_DIR, filename)
    existing_fm = {}
    if os.path.exists(note_path):
        with open(note_path, "r", encoding="utf-8") as f:
            existing_fm, _ = extract_frontmatter(f.read())
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(md)

    note_id = slugify(title) + "_" + str(int(now_ts()))[-6:]
    extracted_tags = extract_tags(md)
    all_tags = ",".join(set(extracted_tags + ([t.strip() for t in tags.split(",") if t.strip()] if tags else [])))
    links = extract_wikilinks(md)

    conn = get_db()
    try:
        conn.execute("""INSERT OR REPLACE INTO notes (id, path, title, type, tags, symbol, created_at, updated_at, content_preview)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (note_id, note_path, title, note_type, all_tags, symbol,
             existing_fm.get("created", now_ts()) if existing_fm else now_ts(), now_ts(), md[:200]))
        for link_title in links:
            target = conn.execute("SELECT id FROM notes WHERE title LIKE ? LIMIT 1", (f"%{link_title}%",)).fetchone()
            if target:
                try: conn.execute("INSERT OR IGNORE INTO links (source_id, target_id, link_type) VALUES (?, ?, ?)", (note_id, target["id"], "related"))
                except: pass
        conn.commit()
    finally:
        conn.close()
    return {"status": "saved", "note_id": note_id, "path": note_path, "title": title, "type": note_type, "tags": all_tags, "links_found": len(links)}


def tool_search_notes(args):
    query = args.get("query", ""); note_type = args.get("type", ""); tag = args.get("tag", ""); limit = min(args.get("limit", 20), 100)
    conn = get_db()
    try:
        sql, params = "SELECT * FROM notes WHERE 1=1", []
        if query: sql += " AND (title LIKE ? OR content_preview LIKE ? OR tags LIKE ?)"; q = f"%{query}%"; params.extend([q, q, q])
        if note_type: sql += " AND type = ?"; params.append(note_type)
        if tag: sql += " AND tags LIKE ?"; params.append(f"%{tag}%")
        sql += " ORDER BY updated_at DESC LIMIT ?"; params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return {"query": query, "count": len(rows), "results": [
            {"id": r["id"], "title": r["title"], "type": r["type"], "tags": r["tags"],
             "symbol": r["symbol"], "path": r["path"],
             "updated": datetime.fromtimestamp(r["updated_at"]).strftime("%Y-%m-%d %H:%M"),
             "preview": r["content_preview"][:150]} for r in rows]}
    finally:
        conn.close()


def tool_read_note(args):
    note_id = args.get("id", ""); filepath = args.get("path", "")
    if filepath and os.path.exists(filepath): path = filepath
    elif note_id:
        conn = get_db()
        try:
            row = conn.execute("SELECT path FROM notes WHERE id = ?", (note_id,)).fetchone()
            path = row["path"] if row else None
        finally: conn.close()
        if not path or not os.path.exists(path): return {"error": "笔记不存在", "id": note_id}
    else: return {"error": "需要提供 id 或 path"}
    with open(path, "r", encoding="utf-8") as f: content = f.read()
    fm, body = extract_frontmatter(content)
    return {"frontmatter": fm, "body": body.strip(), "path": path, "length": len(content)}


def tool_show_graph(args):
    symbol = args.get("symbol", ""); limit = min(args.get("limit", 50), 200)
    conn = get_db()
    try:
        if symbol:
            nodes = conn.execute("""SELECT DISTINCT n.* FROM notes n WHERE n.symbol = ? OR n.id IN (
                SELECT l.target_id FROM links l JOIN notes s ON s.id = l.source_id WHERE s.symbol = ?) LIMIT ?""",
                (symbol, symbol, limit)).fetchall()
            edges = conn.execute("""SELECT DISTINCT l.* FROM links l WHERE l.source_id IN (SELECT id FROM notes WHERE symbol = ?)
                OR l.target_id IN (SELECT id FROM notes WHERE symbol = ?) LIMIT ?""", (symbol, symbol, limit*2)).fetchall()
        else:
            nodes = conn.execute("SELECT * FROM notes ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
            edges = conn.execute("SELECT * FROM links LIMIT ?", (limit*2,)).fetchall()
        return {"nodes": [{"id": n["id"], "title": n["title"], "type": n["type"], "symbol": n["symbol"], "tags": n["tags"]} for n in nodes],
                "edges": [{"source": e["source_id"], "target": e["target_id"], "type": e["link_type"]} for e in edges],
                "stats": {"node_count": len(nodes), "edge_count": len(edges)}}
    finally: conn.close()


def tool_daily_log(args):
    action = args.get("action", "show"); log_date = args.get("date", date_str())
    filepath = os.path.join(DAILY_DIR, f"{log_date}.md")
    if action == "generate":
        md = render_template("daily", date=log_date,
            market_overview=args.get("market_overview", "待补充"),
            operations=args.get("operations", "今日无操作"),
            holdings=args.get("holdings", "待补充"),
            plan=args.get("plan", "待补充"))
        with open(filepath, "w", encoding="utf-8") as f: f.write(md)
        conn = get_db()
        try:
            conn.execute("INSERT OR REPLACE INTO daily_logs (id, date, summary, created_at) VALUES (?, ?, ?, ?)",
                (log_date, log_date, args.get("summary", "")[:500], now_ts())); conn.commit()
        finally: conn.close()
        return {"status": "generated", "date": log_date, "path": filepath}
    elif action == "show":
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f: content = f.read()
            fm, body = extract_frontmatter(content)
            return {"date": log_date, "frontmatter": fm, "body": body.strip(), "exists": True}
        return {"date": log_date, "exists": False, "message": f"{log_date} 还没有复盘日志，使用 action=generate 创建"}
    elif action == "list":
        conn = get_db()
        try:
            rows = conn.execute("SELECT * FROM daily_logs ORDER BY date DESC LIMIT 30").fetchall()
            return {"count": len(rows), "logs": [{"date": r["date"], "summary": r["summary"][:100],
                "created": datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d %H:%M")} for r in rows]}
        finally: conn.close()
    return {"error": f"未知 action: {action}"}


def tool_list_templates(args):
    return {"templates": [
        {"name": "stock", "description": "个股分析报告", "fields": ["symbol", "title", "fundamental", "technical", "fund_flow", "verdict"]},
        {"name": "daily", "description": "每日复盘日志", "fields": ["date", "market_overview", "operations", "holdings", "plan"]},
        {"name": "strategy", "description": "交易策略文档", "fields": ["title", "logic", "params", "backtest", "scenarios"]},
        {"name": "note", "description": "通用笔记", "fields": ["title", "content", "tags"]}
    ], "total": 4}


def tool_sync_to_obsidian(args):
    obsidian_vault = args.get("vault", "")
    if not obsidian_vault or not os.path.isdir(obsidian_vault):
        return {"error": f"Obsidian 仓库路径无效: {obsidian_vault}"}
    for subdir in ["stocks", "daily", "strategies", "notes"]:
        os.makedirs(os.path.join(obsidian_vault, subdir), exist_ok=True)
    stats = {"exported": 0, "skipped": 0, "errors": 0}
    conn = get_db()
    try:
        for note in conn.execute("SELECT * FROM notes ORDER BY updated_at DESC").fetchall():
            target_dir = os.path.join(obsidian_vault, {"stock":"stocks","strategy":"strategies"}.get(note["type"],"notes"))
            if os.path.exists(note["path"]):
                try: shutil.copy2(note["path"], os.path.join(target_dir, os.path.basename(note["path"]))); stats["exported"] += 1
                except: stats["errors"] += 1
    finally: conn.close()
    for f in os.listdir(DAILY_DIR):
        if f.endswith(".md"):
            try: shutil.copy2(os.path.join(DAILY_DIR, f), os.path.join(obsidian_vault, "daily", f)); stats["exported"] += 1
            except: pass
    return {"status": "synced", "obsidian_vault": obsidian_vault, "stats": stats,
            "note": "在 Obsidian 中可打开该仓库查看，支持 [[wikilink]] 跳转和 Graph View"}


# ══════════════════════════════════════════════
# MEMORY 工具 (6) — 委托给 MemoryManager
# ══════════════════════════════════════════════

def tool_memory_save(args):
    key = args.get("key", ""); value = args.get("value", "")
    if not key: return {"ok": False, "error": "Missing 'key'"}
    mm.save(key, value, backend=args.get("backend", "auto"), tags=args.get("tags", []))
    return {"ok": True, "result": f"saved: {key}"}

def tool_memory_load(args):
    key = args.get("key", "")
    if not key: return {"ok": False, "error": "Missing 'key'"}
    return {"ok": True, "result": mm.load(key)}

def tool_memory_search(args):
    query = args.get("query", "")
    limit = min(args.get("limit", 10), 100)

    # 1) 搜索 Memory 后端 (KV)
    memory_results = mm.search(query, limit=limit)

    # 2) 搜索 Vault 笔记 (Markdown)
    vault_results = []
    if query:
        conn = get_db()
        try:
            sql = "SELECT * FROM notes WHERE title LIKE ? OR content_preview LIKE ? OR tags LIKE ?"
            q = f"%{query}%"
            rows = conn.execute(sql + " ORDER BY updated_at DESC LIMIT ?", (q, q, q, limit)).fetchall()
            for r in rows:
                vault_results.append({
                    "key": f"vault:{r['id']}",
                    "value": r["title"],
                    "backend": "vault",
                    "preview": r["content_preview"][:200],
                    "type": r["type"],
                    "tags": r["tags"],
                    "symbol": r["symbol"],
                    "path": r["path"],
                    "updated": datetime.fromtimestamp(r["updated_at"]).strftime("%Y-%m-%d %H:%M"),
                })
        finally:
            conn.close()

    # 3) 合并结果：先 Memory 后 Vault
    merged = memory_results + vault_results
    return {
        "ok": True,
        "result": merged[:limit],
        "count": {"memory": len(memory_results), "vault": len(vault_results), "total": len(merged)},
    }

def tool_memory_list(args):
    return {"ok": True, "result": mm.list()}

def tool_memory_forget(args):
    key = args.get("key", "")
    if not key: return {"ok": False, "error": "Missing 'key'"}
    return {"ok": True, "result": mm.forget(key)}

def tool_memory_stats(args):
    return {"ok": True, "result": mm.stats()}


# ══════════════════════════════════════════════
# 工具注册表
# ══════════════════════════════════════════════

TOOLS = {
    "knowledge__vault_status":     {"handler": tool_vault_status,     "desc": "查看知识库统计 — 笔记数、类型分布、最近更新"},
    "knowledge__save_analysis":    {"handler": tool_save_analysis,    "desc": "保存分析结果到知识库 — 自动套用模板、提取标签、建立 [[wikilink]] 链接"},
    "knowledge__search_notes":     {"handler": tool_search_notes,     "desc": "全文搜索知识库笔记 — 支持标题/正文/标签"},
    "knowledge__read_note":        {"handler": tool_read_note,        "desc": "读取笔记完整内容 — 解析 YAML frontmatter 和 Markdown 正文"},
    "knowledge__show_graph":       {"handler": tool_show_graph,       "desc": "查看知识图谱 — 笔记之间的关联关系网络"},
    "knowledge__daily_log":        {"handler": tool_daily_log,        "desc": "每日复盘日志 — 生成/查看/列出"},
    "knowledge__list_templates":   {"handler": tool_list_templates,   "desc": "列出所有可用分析模板 (stock/daily/strategy/note)"},
    "knowledge__sync_to_obsidian": {"handler": tool_sync_to_obsidian, "desc": "同步知识库到 Obsidian 仓库"},
    "knowledge__memory_save":      {"handler": tool_memory_save,      "desc": "保存一条记忆 (KV) — 自动选择最优后端"},
    "knowledge__memory_load":      {"handler": tool_memory_load,      "desc": "加载指定键的记忆值"},
    "knowledge__memory_search":    {"handler": tool_memory_search,    "desc": "全文搜索记忆内容"},
    "knowledge__memory_list":      {"handler": tool_memory_list,      "desc": "列出所有记忆键名"},
    "knowledge__memory_forget":    {"handler": tool_memory_forget,    "desc": "删除一条记忆"},
    "knowledge__memory_stats":     {"handler": tool_memory_stats,     "desc": "记忆系统统计 — 各后端状态"},
}


# ══════════════════════════════════════════════
# MCP JSON-RPC 2.0 stdio
# ══════════════════════════════════════════════

def make_result(request, result):
    return {"jsonrpc": "2.0", "id": request.get("id"), "result": result}

def make_error(request, code, message):
    return {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": code, "message": message}}

def handle_request(request):
    method = request.get("method", "")
    if method == "initialize":
        return make_result(request, {"protocolVersion": "2025-03-26", "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "deepcode-knowledge", "version": "1.0.0"}})
    elif method == "tools/list":
        return make_result(request, {"tools": [
            {"name": name, "description": info["desc"], "inputSchema": {"type": "object", "properties": {}}}
            for name, info in TOOLS.items()]})
    elif method == "tools/call":
        tool_name = request.get("params", {}).get("name", "")
        args = request.get("params", {}).get("arguments", {})
        if tool_name in TOOLS:
            try:
                result = TOOLS[tool_name]["handler"](args)
                return make_result(request, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]})
            except Exception as e:
                return make_result(request, {"content": [{"type": "text", "text": json.dumps({"error": str(e)}, ensure_ascii=False)}], "isError": True})
        return make_error(request, -32601, f"Unknown tool: {tool_name}")
    elif method == "notifications/initialized":
        return None
    elif method == "ping":
        return make_result(request, {})
    return make_error(request, -32601, f"Unknown method: {method}")


if __name__ == "__main__":
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
        except json.JSONDecodeError:
            continue
