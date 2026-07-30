# -*- coding: utf-8 -*-
"""
DEEPCODE Filesystem MCP Server (自研 Python 版) v2.0
=====================================================
替代 @modelcontextprotocol/server-filesystem 的 npx 方案。
纯 Python  零外部依赖  快速启动  稳定可靠。

v2.0 新增: shell_run — 在沙箱内执行命令，完全替代 Bash

注册方式 (settings.json → mcpServers):
{
  "filesystem": {
    "command": "python",
    "args": ["F:/DEEPCODE/core/mcp_servers/filesystem_mcp_server.py"]
  }
}
"""
import json, os, io, stat, shutil, mimetypes, base64, fnmatch, difflib, re, hashlib, subprocess, signal, time
from pathlib import Path
from datetime import datetime
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("filesystem")

# ── 安全: 只允许访问这些目录 ──
ALLOWED_ROOTS = [
    Path(r"F:\DEEPCODE"),
    Path.home(),
]

# ── shell_run: 命令白名单 ──
ALLOWED_COMMANDS = {
    # 版本控制
    "git", "hg", "svn",
    # 脚本/编译
    "python", "python3", "pip", "pip3", "node", "npm", "npx", "yarn", "pnpm",
    "go", "rustc", "cargo", "javac", "java", "make", "cmake", "ninja",
    # 系统工具 (只读/查看类)
    "ls", "dir", "echo", "cat", "head", "tail", "wc", "find", "grep", "rg",
    "sort", "uniq", "cut", "tr", "awk", "sed", "xargs", "tee",
    "diff", "patch", "file", "stat", "du", "df", "tree",
    "which", "where", "type", "env", "printenv", "pwd", "date", "wget", "curl",
    # 压缩/归档
    "tar", "gzip", "gunzip", "zip", "unzip", "7z",
    # 其他
    "ssh-keygen", "openssl", "gh",
    # Windows 兼容
    "cmd", "powershell", "where.exe",
}

# 高危命令黑名单（即使匹配白名单也拒绝）
BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/",           # rm -rf /
    r">\s*/dev/",               # 写入设备
    r"mkfs\.",                  # 格式化
    r"dd\s+if=",                # dd 磁盘操作
    r"chmod\s+777\s+/",         # 危险权限
    r"shutdown",                # 关机
    r"reboot",                  # 重启
    r":(){ :|:& };:",           # fork bomb
    r"curl.*\|.*sh",            # curl pipe shell
    r"wget.*\|.*sh",            # wget pipe shell
]

def _is_allowed(path: str) -> bool:
    """检查路径是否在允许范围内"""
    try:
        resolved = Path(path).resolve()
    except Exception:
        return False
    return any(
        str(resolved).lower().startswith(str(root).lower())
        for root in ALLOWED_ROOTS
    )

def _resolve(path: str) -> Path:
    """解析并验证路径"""
    p = Path(path).resolve()
    if not _is_allowed(str(p)):
        raise PermissionError(f"Access denied: {path}")
    return p

def _check_command(cmd: str) -> tuple[bool, str]:
    """检查命令是否在白名单内，返回 (允许, 原因)"""
    # 提取第一个词作为命令名
    first_word = cmd.strip().split()[0] if cmd.strip() else ""
    cmd_name = Path(first_word).name  # 去掉路径前缀

    if cmd_name.lower() not in {c.lower() for c in ALLOWED_COMMANDS}:
        return False, f"Command '{cmd_name}' not in whitelist"

    # 检查黑名单模式
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return False, f"Command matches blocked pattern: {pattern}"

    return True, "ok"

# ════════════════════════════════════════════════════════════════
#  Information
# ════════════════════════════════════════════════════════════════

@mcp.tool()
def list_allowed_directories() -> str:
    """列出此 MCP 服务器允许访问的所有目录"""
    lines = [str(r) for r in ALLOWED_ROOTS]
    return json.dumps(lines, ensure_ascii=False)

@mcp.tool()
def get_file_info(path: str) -> str:
    """获取文件/目录的详细元数据"""
    p = _resolve(path)
    if not p.exists():
        return json.dumps({"ok": False, "error": f"Not found: {path}"})

    st = p.stat()
    info = {
        "path": str(p),
        "name": p.name,
        "type": "directory" if p.is_dir() else "file",
        "size": st.st_size,
        "created": datetime.fromtimestamp(st.st_ctime).isoformat(),
        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
        "accessed": datetime.fromtimestamp(st.st_atime).isoformat(),
        "permissions": oct(st.st_mode)[-3:],
        "readable": os.access(p, os.R_OK),
        "writable": os.access(p, os.W_OK),
    }
    if p.is_file():
        info["mime_type"] = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    if p.is_dir():
        contents = list(p.iterdir())
        info["children_count"] = len(contents)
        info["children"] = [
            {"name": c.name, "type": "directory" if c.is_dir() else "file"}
            for c in sorted(contents, key=lambda x: (not x.is_dir(), x.name.lower()))
        ][:100]
    return json.dumps(info, ensure_ascii=False, indent=2)

# ════════════════════════════════════════════════════════════════
#  Reading
# ════════════════════════════════════════════════════════════════

@mcp.tool()
def read_text_file(path: str, head: Optional[int] = None, tail: Optional[int] = None) -> str:
    """读取文本文件内容。head/tail 参数可只读前几行或后几行"""
    p = _resolve(path)
    if not p.is_file():
        return json.dumps({"ok": False, "error": f"Not a file: {path}"})

    texto = None
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"):
        try:
            texto = p.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if texto is None:
        return json.dumps({"ok": False, "error": f"Cannot decode: {path}"})

    if head is not None:
        return "".join(texto.splitlines(True)[:head])
    if tail is not None:
        return "".join(texto.splitlines(True)[-tail:])
    return texto

@mcp.tool()
def read_file(path: str, head: Optional[int] = None, tail: Optional[int] = None) -> str:
    """读取文件（别名），等同于 read_text_file"""
    return read_text_file(path, head=head, tail=tail)

@mcp.tool()
def read_media_file(path: str) -> str:
    """读取媒体文件（图片/音频），返回 base64 + MIME 类型"""
    p = _resolve(path)
    if not p.is_file():
        return json.dumps({"ok": False, "error": f"Not found: {path}"})

    mime, _ = mimetypes.guess_type(str(p))
    mime = mime or "application/octet-stream"
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return json.dumps({
        "path": str(p),
        "mime_type": mime,
        "size": len(data),
        "base64_preview": b64[:2000],
        "note": "Use full base64 for complete data" if len(b64) > 2000 else ""
    }, ensure_ascii=False)

@mcp.tool()
def read_multiple_files(paths: list[str]) -> str:
    """批量读取多个文件。比逐个读取更高效"""
    results = {}
    for path in paths:
        try:
            results[path] = read_text_file(path)
        except Exception as e:
            results[path] = {"ok": False, "error": str(e)}
    return json.dumps(results, ensure_ascii=False, indent=2)

# ════════════════════════════════════════════════════════════════
#  Writing & Editing
# ════════════════════════════════════════════════════════════════

@mcp.tool()
def write_file(path: str, content: str) -> str:
    """创建新文件或完全覆盖现有文件"""
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    size = p.stat().st_size
    return json.dumps({"ok": True, "path": str(p), "size": size, "written": True})

@mcp.tool()
def edit_file(path: str, edits: list[dict], dryRun: bool = False) -> str:
    """
    行级编辑文件。每个 edit 包含:
      - oldText: 要替换的精确文本
      - newText: 替换后的文本
    dryRun=True 时只预览不实际修改，返回 unified diff
    """
    p = _resolve(path)
    if not p.is_file():
        return json.dumps({"ok": False, "error": f"File not found: {path}"})

    original_lines = p.read_text(encoding="utf-8").splitlines(True)
    current_lines = list(original_lines)

    diffs = []
    for i, ed in enumerate(edits):
        old_text = ed.get("oldText", "")
        new_text = ed.get("newText", "")
        full = "".join(current_lines)
        if old_text not in full:
            diffs.append(f"@@ edit[{i}]: oldText NOT FOUND")
            continue
        before = full.splitlines(True)
        after = full.replace(old_text, new_text).splitlines(True)
        diff = difflib.unified_diff(
            before, after,
            fromfile=str(p), tofile=str(p),
            lineterm=""
        )
        diffs.append("\n".join(diff))
        current_lines = after

    if dryRun:
        return "\n\n".join(diffs) or "No changes"

    p.write_text("".join(current_lines), encoding="utf-8")
    return "\n\n".join(diffs) or "No changes"

# ════════════════════════════════════════════════════════════════
#  Directory Operations
# ════════════════════════════════════════════════════════════════

@mcp.tool()
def create_directory(path: str) -> str:
    """创建目录（可递归创建多级）"""
    p = _resolve(path)
    p.mkdir(parents=True, exist_ok=True)
    return json.dumps({"ok": True, "path": str(p), "created": True})

@mcp.tool()
def list_directory(path: str) -> str:
    """列出目录内容，区分 [FILE] 和 [DIR]"""
    p = _resolve(path)
    if not p.is_dir():
        return json.dumps({"ok": False, "error": f"Not a directory: {path}"})

    items = []
    for entry in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        typ = "[DIR]" if entry.is_dir() else "[FILE]"
        items.append(f"{typ}  {entry.name}")
    return "\n".join(items) or "(empty)"

@mcp.tool()
def list_directory_with_sizes(path: str, sortBy: str = "name") -> str:
    """列出目录内容并显示文件大小"""
    p = _resolve(path)
    if not p.is_dir():
        return json.dumps({"ok": False, "error": f"Not a directory: {path}"})

    entries = []
    for entry in p.iterdir():
        typ = "[DIR]" if entry.is_dir() else "[FILE]"
        size = entry.stat().st_size if entry.is_file() else 0
        entries.append((typ, entry.name, size))

    if sortBy == "size":
        entries.sort(key=lambda e: (-e[2], e[1].lower()))
    else:
        entries.sort(key=lambda e: (not e[0] == "[DIR]", e[1].lower()))

    lines = []
    for typ, name, size in entries:
        sz = f"({_fmt_size(size)})" if size else ""
        lines.append(f"{typ}  {name:<40s} {sz}")
    return "\n".join(lines) or "(empty)"

@mcp.tool()
def directory_tree(path: str, excludePatterns: Optional[list[str]] = None) -> str:
    """递归树形目录结构，输出 JSON"""
    p = _resolve(path)
    if not p.is_dir():
        return json.dumps({"ok": False, "error": f"Not a directory: {path}"})

    def _build_tree(dirpath: Path, depth: int = 0) -> list:
        if depth > 8:
            return []
        result = []
        try:
            entries = sorted(dirpath.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return []
        for entry in entries:
            if excludePatterns and any(fnmatch.fnmatch(entry.name, pat) for pat in excludePatterns):
                continue
            if entry.is_dir():
                children = _build_tree(entry, depth + 1)
                result.append({"name": entry.name, "type": "directory", "children": children})
            else:
                result.append({"name": entry.name, "type": "file"})
        return result

    tree = _build_tree(p)
    return json.dumps({"root": str(p), "children": tree}, ensure_ascii=False, indent=2)

# ════════════════════════════════════════════════════════════════
#  Search & Move
# ════════════════════════════════════════════════════════════════

@mcp.tool()
def search_files(path: str, pattern: str, excludePatterns: Optional[list[str]] = None) -> str:
    """递归搜索匹配 glob 模式的文件"""
    p = _resolve(path)
    results = []
    for root, dirs, files in os.walk(p):
        if excludePatterns:
            dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(d, pat) for pat in excludePatterns)]
            files = [f for f in files if not any(fnmatch.fnmatch(f, pat) for pat in excludePatterns)]
        for f in files:
            if fnmatch.fnmatch(f, pattern):
                results.append(str(Path(root) / f))
    return "\n".join(results[:500]) or "No matches"

@mcp.tool()
def move_file(source: str, destination: str) -> str:
    """移动或重命名文件/目录"""
    src = _resolve(source)
    if not src.exists():
        return json.dumps({"ok": False, "error": f"Source not found: {source}"})
    dst = _resolve(destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return json.dumps({"ok": True, "from": str(src), "to": str(dst)})

# ════════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════════

def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}TB"

# ════════════════════════════════════════════════════════════════
#  Extended Tools
# ════════════════════════════════════════════════════════════════

@mcp.tool()
def grep_files(
    path: str,
    pattern: str,
    glob: str = "*",
    max_results: int = 50,
    context_lines: int = 0,
    ignore_case: bool = False,
) -> str:
    """内容搜索 — 递归搜索文件内容匹配正则/文本。替代 bash rg/grep。

    Args:
        path:           搜索根目录
        pattern:        正则表达式或纯文本
        glob:           文件名过滤 (默认 "*")
        max_results:    最大结果数 (默认 50)
        context_lines:  每个匹配的上下文行数 (0-3)
        ignore_case:    忽略大小写
    """
    p = _resolve(path)
    if not p.is_dir():
        return json.dumps({"ok": False, "error": f"Not a directory: {path}"})

    flags = re.IGNORECASE if ignore_case else 0
    try:
        regex = re.compile(pattern, flags)
        is_regex = True
    except re.error:
        regex = re.compile(re.escape(pattern), flags)
        is_regex = False

    results = []
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git")]
        for fname in files:
            if not fnmatch.fnmatch(fname, glob):
                continue
            fpath = Path(root) / fname
            if fpath.stat().st_size > 5 * 1024 * 1024:
                continue
            try:
                lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for i, line in enumerate(lines):
                if regex.search(line):
                    entry = {"file": str(fpath.relative_to(p)), "line": i + 1, "text": line.strip()[:200]}
                    if context_lines > 0:
                        entry["context"] = [
                            lines[j].strip()[:200]
                            for j in range(max(0, i - context_lines), min(len(lines), i + context_lines + 1))
                            if j != i
                        ]
                    results.append(entry)
                    if len(results) >= max_results:
                        summary = {
                            "ok": True, "truncated": len(results) >= max_results,
                            "total_found": len(results), "mode": "regex" if is_regex else "plain",
                            "pattern": pattern, "results": results
                        }
                        return json.dumps(summary, ensure_ascii=False, indent=2)

    summary = {
        "ok": True, "truncated": False,
        "total_found": len(results), "mode": "regex" if is_regex else "plain",
        "pattern": pattern, "results": results
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)


@mcp.tool()
def diff_files(path1: str, path2: str) -> str:
    """比较两个文件，返回 unified diff"""
    p1 = _resolve(path1)
    p2 = _resolve(path2)
    if not p1.is_file():
        return json.dumps({"ok": False, "error": f"Not found: {path1}"})
    if not p2.is_file():
        return json.dumps({"ok": False, "error": f"Not found: {path2}"})

    lines1 = p1.read_text(encoding="utf-8", errors="replace").splitlines(True)
    lines2 = p2.read_text(encoding="utf-8", errors="replace").splitlines(True)
    diff = difflib.unified_diff(lines1, lines2, fromfile=str(p1), tofile=str(p2), lineterm="")
    return "\n".join(diff) or "(files are identical)"


@mcp.tool()
def file_hash(path: str, algorithm: str = "sha256") -> str:
    """计算文件哈希 (md5/sha1/sha256)"""
    p = _resolve(path)
    if not p.is_file():
        return json.dumps({"ok": False, "error": f"Not found: {path}"})
    h = hashlib.new(algorithm)
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return json.dumps({"path": str(p), "algorithm": algorithm, "hash": h.hexdigest(), "size": p.stat().st_size})


@mcp.tool()
def delete_file(path: str, recursive: bool = False) -> str:
    """删除文件或目录 (recursive=True 时递归删除目录)"""
    p = _resolve(path)
    if not p.exists():
        return json.dumps({"ok": False, "error": f"Not found: {path}"})
    if p.is_dir():
        if recursive:
            shutil.rmtree(p)
        else:
            p.rmdir()
    else:
        p.unlink()
    return json.dumps({"ok": True, "deleted": str(p)})


@mcp.tool()
def append_file(path: str, content: str) -> str:
    """追加内容到文件末尾（自动换行）"""
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(content + "\n")
    return json.dumps({"ok": True, "path": str(p), "size": p.stat().st_size, "appended": True})


@mcp.tool()
def copy_file(source: str, destination: str) -> str:
    """复制文件或目录（目录递归复制）"""
    src = _resolve(source)
    if not src.exists():
        return json.dumps({"ok": False, "error": f"Source not found: {source}"})
    dst = _resolve(destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    return json.dumps({"ok": True, "from": str(src), "to": str(dst)})


@mcp.tool()
def batch_replace(
    path: str,
    old: str,
    new: str,
    glob: str = "*",
    dry_run: bool = False,
) -> str:
    """批量替换 — 在多个文件中搜索并替换文本（类似 sed -i）。

    Args:
        path:     搜索根目录
        old:      要替换的文本
        new:      替换后的文本
        glob:     文件名过滤 (默认 "*")
        dry_run:  True 时只预览，不实际修改
    """
    p = _resolve(path)
    if not p.is_dir():
        return json.dumps({"ok": False, "error": f"Not a directory: {path}"})

    modified = []
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git")]
        for fname in files:
            if not fnmatch.fnmatch(fname, glob):
                continue
            fpath = Path(root) / fname
            if fpath.stat().st_size > 5 * 1024 * 1024:
                continue
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if old in text:
                count = text.count(old)
                modified.append({"file": str(fpath.relative_to(p)), "occurrences": count})
                if not dry_run:
                    fpath.write_text(text.replace(old, new), encoding="utf-8")

    return json.dumps({
        "ok": True,
        "dry_run": dry_run,
        "files_modified": len(modified),
        "details": modified[:100],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def count_lines(path: str, glob: str = "*") -> str:
    """统计目录中文件的行数/字数/字符数（wc 替代）"""
    p = _resolve(path)
    if p.is_file():
        text = p.read_text(encoding="utf-8", errors="replace")
        return json.dumps({
            "file": str(p),
            "lines": text.count("\n") + 1,
            "words": len(text.split()),
            "chars": len(text),
        })
    if p.is_dir():
        total_lines, total_words, total_chars, file_count = 0, 0, 0, 0
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if not fnmatch.fnmatch(fname, glob):
                    continue
                fpath = Path(root) / fname
                if fpath.stat().st_size > 10 * 1024 * 1024:
                    continue
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                total_lines += text.count("\n") + 1
                total_words += len(text.split())
                total_chars += len(text)
                file_count += 1
        return json.dumps({
            "directory": str(p),
            "files": file_count,
            "total_lines": total_lines,
            "total_words": total_words,
            "total_chars": total_chars,
        })
    return json.dumps({"ok": False, "error": f"Not found: {path}"})


@mcp.tool()
def find_by_size(
    path: str,
    min_kb: int = 0,
    max_kb: int = 0,
    top_n: int = 20,
) -> str:
    """按文件大小查找 — 找出目录中最大/最小/区间内的文件"""
    p = _resolve(path)
    if not p.is_dir():
        return json.dumps({"ok": False, "error": f"Not a directory: {path}"})

    files = []
    for root, dirs, filenames in os.walk(p):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in filenames:
            fpath = Path(root) / fname
            sz = fpath.stat().st_size
            if min_kb and sz < min_kb * 1024:
                continue
            if max_kb and sz > max_kb * 1024:
                continue
            files.append((str(fpath.relative_to(p)), sz))

    files.sort(key=lambda x: -x[1])
    top = files[:top_n]
    return json.dumps({
        "ok": True,
        "total_matches": len(files),
        "filters": {"min_kb": min_kb, "max_kb": max_kb},
        "top": [{"file": f, "size": _fmt_size(sz)} for f, sz in top],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def find_by_date(
    path: str,
    hours: int = 24,
    glob: str = "*",
    top_n: int = 30,
) -> str:
    """按修改时间查找 — 找出最近 N 小时内修改的文件"""
    p = _resolve(path)
    if not p.is_dir():
        return json.dumps({"ok": False, "error": f"Not a directory: {path}"})

    cutoff = datetime.now().timestamp() - hours * 3600
    results = []
    for root, dirs, filenames in os.walk(p):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git")]
        for fname in filenames:
            if not fnmatch.fnmatch(fname, glob):
                continue
            fpath = Path(root) / fname
            mtime = fpath.stat().st_mtime
            if mtime >= cutoff:
                results.append((str(fpath.relative_to(p)), mtime, fpath.stat().st_size))

    results.sort(key=lambda x: -x[1])
    top = results[:top_n]
    return json.dumps({
        "ok": True,
        "hours": hours,
        "total_matches": len(results),
        "files": [
            {"file": f, "modified": datetime.fromtimestamp(ts).isoformat(), "size": _fmt_size(sz)}
            for f, ts, sz in top
        ],
    }, ensure_ascii=False, indent=2)


# ════════════════════════════════════════════════════════════════
#  🚀 Shell Execution (v2.0 — 最强增强)
# ════════════════════════════════════════════════════════════════

@mcp.tool()
def shell_run(
    command: str,
    cwd: str = "",
    timeout: int = 60,
    env: dict = None,
    shell: bool = True,
    capture_both: bool = True,
) -> str:
    """🚀 在沙箱白名单目录内执行 Shell 命令。

    命令白名单: git, python, pip, npm, node, go, cargo, ls, grep, find, curl, wget 等 60+ 工具。
    高危命令 (rm -rf /, shutdown, fork bomb 等) 自动拦截。

    Args:
        command:    要执行的命令 (支持管道、重定向等)
        cwd:        工作目录 (默认为 F:\\DEEPCODE，必须在白名单目录内)
        timeout:    超时秒数 (默认 60s，最大 300s)
        env:        额外环境变量 dict (可选)
        shell:      通过 shell 执行 (默认 True，支持管道)
        capture_both: 同时捕获 stdout + stderr (默认 True)

    Returns:
        JSON: {ok, exit_code, stdout, stderr, elapsed_ms, command, cwd, timed_out, was_blocked}

    Examples:
        shell_run("git status")
        shell_run("python --version")
        shell_run("git diff --stat HEAD~1")
        shell_run("pip list | grep torch")
        shell_run("find . -name '*.py' | head -20")
    """
    # ── CWD 验证 ──
    if not cwd:
        cwd = str(ALLOWED_ROOTS[0])  # 默认 F:\DEEPCODE
    if not _is_allowed(cwd):
        return json.dumps({
            "ok": False, "error": f"cwd not allowed: {cwd}",
            "allowed_roots": [str(r) for r in ALLOWED_ROOTS]
        })

    # ── 命令白名单检查 ──
    allowed, reason = _check_command(command)
    if not allowed:
        return json.dumps({"ok": False, "error": reason, "was_blocked": True})

    # ── 超时限制 ──
    if timeout > 300:
        timeout = 300

    # ── 执行 ──
    start = time.time()
    try:
        kwargs = {
            "cwd": cwd,
            "timeout": timeout,
        }
        if capture_both:
            kwargs["stdout"] = subprocess.PIPE
            kwargs["stderr"] = subprocess.PIPE
        else:
            kwargs["stdout"] = subprocess.PIPE
            kwargs["stderr"] = subprocess.STDOUT
        if env:
            merged_env = os.environ.copy()
            merged_env.update(env)
            kwargs["env"] = merged_env
        if shell:
            kwargs["shell"] = True

        proc = subprocess.run(command, **kwargs)
        elapsed = int((time.time() - start) * 1000)

        stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
        stderr = proc.stderr.decode("utf-8", errors="replace") if (capture_both and proc.stderr) else ""

        # 截断过长输出
        max_output = 50000
        if len(stdout) > max_output:
            stdout = stdout[:max_output] + f"\n... [truncated at {max_output} chars, total {len(stdout)}]"
        if len(stderr) > max_output:
            stderr = stderr[:max_output] + f"\n... [truncated at {max_output} chars]"

        return json.dumps({
            "ok": True,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_ms": elapsed,
            "command": command[:500],
            "cwd": cwd,
            "timed_out": False,
            "was_blocked": False,
        }, ensure_ascii=False)

    except subprocess.TimeoutExpired:
        elapsed = int((time.time() - start) * 1000)
        return json.dumps({
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "elapsed_ms": elapsed,
            "command": command[:500],
            "cwd": cwd,
            "timed_out": True,
            "was_blocked": False,
        })
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return json.dumps({
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "elapsed_ms": elapsed,
            "command": command[:500],
            "cwd": cwd,
            "timed_out": False,
            "was_blocked": False,
        })


@mcp.tool()
def shell_list_commands() -> str:
    """列出 shell_run 支持的所有命令白名单（60+ 工具）"""
    return json.dumps({
        "allowed_commands": sorted(ALLOWED_COMMANDS),
        "blocked_patterns": BLOCKED_PATTERNS,
        "description": "Commands in the whitelist can be executed via shell_run(). Blocked patterns are always rejected regardless of whitelist."
    }, indent=2)


@mcp.tool()
def shell_check_command(command: str) -> str:
    """检查命令是否在白名单中（不实际执行）。用于预检。"""
    allowed, reason = _check_command(command)
    return json.dumps({"command": command[:200], "allowed": allowed, "reason": reason})


# ════════════════════════════════════════════════════════════════
#  Entry
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run()
