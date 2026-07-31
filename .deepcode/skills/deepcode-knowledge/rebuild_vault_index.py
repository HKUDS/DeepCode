#!/usr/bin/env python3
"""Vault 索引重建 — 扫描磁盘 .md 文件批量导入 SQLite"""
import json, sys, os, re, time, sqlite3
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict

# ── 路径 ──
SKILL_DIR = Path(__file__).parent
NOTES_DIR = SKILL_DIR / "data" / "vault" / "notes"
DAILY_DIR = SKILL_DIR / "data" / "vault" / "daily"
DB_PATH   = SKILL_DIR / "data" / "knowledge.db"

# ── 工具函数 (复用 knowledge_server 的逻辑) ──
def now_ts(): return time.time()

def extract_frontmatter(text):
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
    if not m: return {}, text
    fm = {}
    for line in m.group(1).strip().split('\n'):
        if ':' in line:
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, text[m.end():]

def extract_tags(text):
    return re.findall(r'#([\w\u4e00-\u9fff\-.]+)', text)

def extract_wikilinks(text):
    return re.findall(r'\[\[([^\]]+)\]\]', text)

def slugify(text):
    s = re.sub(r'[^\w\s-]', '', text).strip().lower()
    return re.sub(r'[-\s]+', '-', s)[:60]

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
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

def parse_tags(raw_tags):
    """处理 tags 字段，支持 [tag1, tag2] 列表格式和逗号分隔"""
    if not raw_tags:
        return ""
    raw = raw_tags.strip()
    # 处理 [tag1, tag2, ...] 格式
    if raw.startswith('[') and raw.endswith(']'):
        inner = raw[1:-1]
        tags = [t.strip().strip('"').strip("'") for t in inner.split(',') if t.strip()]
        return ','.join(tags)
    return raw

# ══════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════

def rebuild():
    conn = get_db()
    ts_now = now_ts()
    
    # 清空旧索引
    conn.execute("DELETE FROM links")
    conn.execute("DELETE FROM notes")
    conn.execute("DELETE FROM daily_logs")
    conn.commit()
    
    stats = {"notes": 0, "dailies": 0, "links": 0, "errors": 0}
    
    # ── 扫描 notes ──
    if NOTES_DIR.exists():
        for fpath in sorted(NOTES_DIR.glob("*.md")):
            try:
                raw = fpath.read_text(encoding="utf-8")
                fm, body = extract_frontmatter(raw)
                
                title = fm.get("title", fpath.stem)
                note_type = fm.get("type", "note")
                symbol = fm.get("symbol", "")
                tags = parse_tags(fm.get("tags", ""))
                created = fm.get("created", str(date.today()))
                updated = fm.get("updated", created)
                
                # 转换日期到时间戳
                try:
                    created_ts = datetime.strptime(created, "%Y-%m-%d").timestamp()
                except:
                    created_ts = ts_now
                try:
                    updated_ts = datetime.strptime(updated, "%Y-%m-%d").timestamp()
                except:
                    updated_ts = ts_now
                
                note_id = slugify(title) + "_" + str(int(ts_now))[-6:]
                preview = body[:200].strip()
                
                # 额外从 body 提取标签
                body_tags = extract_tags(body)
                all_tags = set(tags.split(",") if tags else [])
                all_tags.update(body_tags)
                all_tags.discard("")
                tags_str = ",".join(sorted(all_tags))
                
                conn.execute(
                    """INSERT OR REPLACE INTO notes 
                       (id, path, title, type, tags, symbol, created_at, updated_at, content_preview)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (note_id, str(fpath), title, note_type, tags_str, symbol,
                     created_ts, updated_ts, preview)
                )
                
                # 提取 wikilinks
                links = extract_wikilinks(body)
                for link_title in links:
                    target = conn.execute(
                        "SELECT id FROM notes WHERE title LIKE ? LIMIT 1",
                        (f"%{link_title}%",)
                    ).fetchone()
                    if target:
                        try:
                            conn.execute(
                                "INSERT OR IGNORE INTO links (source_id, target_id, link_type) VALUES (?, ?, ?)",
                                (note_id, target["id"], "related")
                            )
                            stats["links"] += 1
                        except:
                            pass
                
                stats["notes"] += 1
                print(f"  ✓ [{note_type:6s}] {title}")
                
            except Exception as e:
                stats["errors"] += 1
                print(f"  ✗ ERROR: {fpath.name} → {e}")
    
    # ── 扫描 daily ──
    if DAILY_DIR.exists():
        for fpath in sorted(DAILY_DIR.glob("*.md")):
            try:
                raw = fpath.read_text(encoding="utf-8")
                fm, body = extract_frontmatter(raw)
                
                d = fm.get("date", fpath.stem.replace("daily_", ""))
                summary = fm.get("title", "")
                mood = fm.get("mood", "")
                
                conn.execute(
                    """INSERT OR REPLACE INTO daily_logs (id, date, summary, mood, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (f"daily_{d}", d, summary, mood, ts_now)
                )
                stats["dailies"] += 1
                print(f"  ✓ [daily ] {d} — {summary}")
                
            except Exception as e:
                stats["errors"] += 1
                print(f"  ✗ ERROR: {fpath.name} → {e}")
    
    conn.commit()
    conn.close()
    
    return stats

if __name__ == "__main__":
    print("=" * 50)
    print("  Vault 索引重建")
    print(f"  笔记目录: {NOTES_DIR}")
    print(f"  日志目录: {DAILY_DIR}")
    print(f"  数据库:   {DB_PATH}")
    print("=" * 50)
    
    stats = rebuild()
    
    print()
    print("=" * 50)
    print(f"  重建完成!")
    print(f"  ✅ 笔记:    {stats['notes']} 篇")
    print(f"  ✅ 日志:    {stats['dailies']} 篇")
    print(f"  🔗 链接:    {stats['links']} 条")
    print(f"  ❌ 错误:    {stats['errors']}")
    print("=" * 50)
