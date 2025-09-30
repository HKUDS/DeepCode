import json
import re, logging
logger = logging.getLogger(__name__)

_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*([\s\S]+?)\s*```\s*$",  # 捕获 ```json ... ``` 中间内容
    re.IGNORECASE,
)

def safe_json_loads(text: str):
    """
    去除 Markdown fenced‑code 包裹后再 json.loads。
    若解析失败，打印错误和原始文本，便于调试。
    """
    text = text.strip()

    # ① 若匹配 ```json ... ``` 或 ``` ... ```，取中间块
    m = _CODE_FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()

    # ② 尝试解析
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("⚠️ JSON 解析失败: %s\n--- RAW ---\n%s\n-----------", e, text)
        raise