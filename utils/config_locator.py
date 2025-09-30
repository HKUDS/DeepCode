from __future__ import annotations
import os
from pathlib import Path

CONFIG_BASENAME = "mcp_agent.config.yaml"
ENV_KEY_CONFIG = "MCP_AGENT_CONFIG"
ENV_KEY_SECRET = ""
SECRET_BASENAME ="mcp_agent.secret.yaml"
def _search_upwards(start: Path, name: str) -> Path | None:
    start = start.resolve()
    for base in [start, *start.parents]:
        cand = base / name
        if cand.is_file():
            return cand
    return None

def find_mcp_config() -> Path:
    # 1) 环境变量（最高优先级）
    env = os.getenv(ENV_KEY)
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_file():
            return p
        raise FileNotFoundError(f"{ENV_KEY} 指向的文件不存在: {p}")

    # 2) 从 CWD 向上找
    cwd_hit = _search_upwards(Path.cwd(), CONFIG_BASENAME)
    if cwd_hit:
        return cwd_hit

    # 3) 从当前模块文件向上找（兼容被子包调用）
    here_hit = _search_upwards(Path(__file__).resolve(), CONFIG_BASENAME)
    if here_hit:
        return here_hit

    # 4) （可选）包内默认（发布型项目可启用）
    # import importlib.resources as res
    # with res.as_file(res.files("yourpkg.data").joinpath(CONFIG_BASENAME)) as p:
    #     if p.is_file(): return p

    raise FileNotFoundError(
        f"未找到 {CONFIG_BASENAME}；可设置 {ENV_KEY} 指定绝对路径。"
)