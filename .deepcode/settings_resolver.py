#!/usr/bin/env python3
"""
Settings Resolver — 三级配置合并解析器
═══════════════════════════════════════
user settings  ← ~/.deepcode/settings.json         (全局默认)
project settings ← .deepcode/settings.json         (项目共享)
local settings  ← .deepcode/settings.local.json    (本机覆盖, .gitignore)

合并规则: user < project < local (local 优先级最高)
深层合并 (dict 递归合并, list 替换而非追加)

用法:
  from settings_resolver import resolve_settings, get_setting

  cfg = resolve_settings()              # 三级合并结果
  val = get_setting("permissions.mode") # 点号路径访问
  val = get_setting("model", "default") # 带默认值
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


# ── 路径定义 ───────────────────────────────────────────────────

def _get_project_root() -> Path:
    """自动探测项目根目录 (包含 .deepcode/ 的目录)"""
    cwd = Path.cwd()
    for p in [cwd] + list(cwd.parents):
        if (p / ".deepcode").is_dir():
            return p
    return cwd


PROJECT_ROOT = _get_project_root()
USER_SETTINGS = Path.home() / ".deepcode" / "settings.json"
PROJECT_SETTINGS = PROJECT_ROOT / ".deepcode" / "settings.json"
LOCAL_SETTINGS = PROJECT_ROOT / ".deepcode" / "settings.local.json"


# ── JSON 加载 ──────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    """加载 JSON 文件，不存在或解析失败时返回空 dict"""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        # 移除 BOM
        if text.startswith("\ufeff"):
            text = text[1:]
        return json.loads(text)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[settings_resolver] WARN: Failed to load {path}: {e}", file=__import__("sys").stderr)
        return {}


# ── 深层合并 ───────────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    """
    递归深层合并两个 dict。
    - dict 值递归合并
    - list 值直接替换 (不追加)
    - 其他值直接替换
    """
    result = base.copy()
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


# ── 三级解析 ───────────────────────────────────────────────────

def resolve_settings(
    user_path: Optional[Path] = None,
    project_path: Optional[Path] = None,
    local_path: Optional[Path] = None,
) -> dict:
    """
    解析三级配置并合并。

    Args:
        user_path:    用户级配置路径 (默认 ~/.deepcode/settings.json)
        project_path: 项目级配置路径 (默认 .deepcode/settings.json)
        local_path:   本地级配置路径 (默认 .deepcode/settings.local.json)

    Returns:
        合并后的完整配置 dict
    """
    user_cfg = _load_json(user_path or USER_SETTINGS)
    project_cfg = _load_json(project_path or PROJECT_SETTINGS)
    local_cfg = _load_json(local_path or LOCAL_SETTINGS)

    # user ← project ← local
    merged = _deep_merge(user_cfg, project_cfg)
    merged = _deep_merge(merged, local_cfg)

    # 附加元信息
    merged["_meta"] = {
        "user_settings": str(user_path or USER_SETTINGS),
        "project_settings": str(project_path or PROJECT_SETTINGS),
        "local_settings": str(local_path or LOCAL_SETTINGS),
        "project_root": str(PROJECT_ROOT),
        "tiers_loaded": [
            "user" if (user_path or USER_SETTINGS).exists() else None,
            "project" if (project_path or PROJECT_SETTINGS).exists() else None,
            "local" if (local_path or LOCAL_SETTINGS).exists() else None,
        ],
    }

    return merged


def get_setting(key_path: str, default: Any = None, cfg: Optional[dict] = None) -> Any:
    """
    通过点号路径获取配置值。

    Args:
        key_path: 点号分隔的路径，如 "permissions.mode"
        default:  键不存在时的默认值
        cfg:      配置 dict (不传则自动 resolve)

    Returns:
        配置值或 default
    """
    if cfg is None:
        cfg = resolve_settings()

    parts = key_path.split(".")
    current = cfg
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def list_tiers() -> dict:
    """列出三级配置各层的文件存在状态和大小"""
    result = {}
    for name, path in [("user", USER_SETTINGS), ("project", PROJECT_SETTINGS),
                       ("local", LOCAL_SETTINGS)]:
        info = {"exists": path.exists(), "path": str(path)}
        if path.exists():
            info["size_bytes"] = path.stat().st_size
        result[name] = info
    return result


# ── CLI 入口 ───────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "tiers":
        print(json.dumps(list_tiers(), indent=2, ensure_ascii=False))
    elif len(sys.argv) > 2 and sys.argv[1] == "get":
        val = get_setting(sys.argv[2])
        print(json.dumps(val, indent=2, ensure_ascii=False) if isinstance(val, (dict, list)) else val)
    else:
        cfg = resolve_settings()
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
