"""
Router Agent — 将用户自然语言请求路由到合适的 Workflow
===============================================================================
英文简介：
    * Convert free‑form user requests into a structured JSON “Workflow Spec”
    * Select the best‑fit workflow from a configurable registry
    * Output example:
        {
            "workflow": "code_implementation",
            "arguments": {
                "paper_url": "https://arxiv.org/abs/..."
            }
        }

中文简介：
    * 负责把用户的自然语言需求转换成结构化 JSON（Workflow Spec）
    * 依据内部注册表选择最合适的工作流
    * 输出示例见上
"""
import os, json, logging, httpx, pathlib
from functools import lru_cache
from typing import List, Dict,Any,Optional
from utils.json_tools import safe_json_loads
import yaml

from prompts.router_prompt import ROUTER_PROMPT
from openai import OpenAI, AsyncOpenAI               # pip install openai>=1.0.0
try:
    from anthropic import Anthropic, AsyncAnthropic  # pip install anthropic
except ImportError:
    Anthropic = AsyncAnthropic = None                # 未安装时占位
logger = logging.getLogger(__name__)
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"





def _load_key_config(path: str | os.PathLike) -> Dict[str, Dict[str, str]]:
    """
    从 YAML 文件加载各供应商的 API‑Key 配置。

    预期格式示例（~/.llm_api_keys.yaml）::
        openai:
          api_key: "sk-..."
          organization: "org_123"
          project: "proj_abc"

        anthropic:
          api_key: "anthropic-key"

        deepseek:
          api_key: "ds-key"
    """
    p = pathlib.Path(path).expanduser()
    if not p.is_file():
        logger.warning("⚠️ 未找到 API‑Key 配置文件：%s", p)
        return {}

    try:
        with p.open("r", encoding="utf-8") as f:
            data: Dict[str, Any] = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError("YAML 顶层应为映射型 (dict)")
        # 仅保留键值都为 str 的子项，防止意外结构
        clean: Dict[str, Dict[str, str]] = {}
        for provider, kv in data.items():
            if isinstance(kv, dict):
                clean[provider.lower()] = {str(k): str(v) for k, v in kv.items()}
        return clean
    except Exception as e:
        logger.error("❌ 解析 API‑Key YAML 失败：%s", e)
        return {}


class LLMClient:#这个类相当于agent
    """
    通用 LLM 客户端（从 JSON 读取 API‑Key）
    -----------------------------------------------------------------
    model_name 前缀自动判定供应商：
        'gpt-' / 'o3' / 'gpt4o' ➜ OpenAI
        'claude' / 'anthropic'  ➜ Anthropic
        'deepseek'             ➜ DeepSeek
    """

    def __init__(
        self,
        model: str = "chatgpt-o3",
        api_key: str | None = None,
        async_mode: bool = False,
        config_path: str | os.PathLike = "D:\PythonProjects\DeepCode-main\mcp_agent.secrets.yaml",
        **extra,
    ):
        self.model_name = model.lower()
        self.async_mode = async_mode
        self.extra = extra

        # —— ❶ 读 JSON 配置 —— #
        cfg = _load_key_config(config_path)
        # 将各家 key  / 组织 / project 信息提取出来，供下面使用
        openai_cfg   = cfg.get("openai", {})
        anthropic_cfg = cfg.get("anthropic", {})
        deepseek_cfg  = cfg.get("deepseek", {})

        # —— ❷ 供应商分发 —— #
        if self.model_name.startswith(("gpt", "o3")):
            self.provider = "openai"
            self.client = (AsyncOpenAI if async_mode else OpenAI)(
                api_key      = api_key                          # 明确传参 > JSON > 环境
                          or openai_cfg.get("api_key")
                          or os.getenv("OPENAI_API_KEY"),
                organization = openai_cfg.get("organization"),
                project      = openai_cfg.get("project"),
                **{k: v for k, v in extra.items() if k in ("base_url",)},
            )

        elif self.model_name.startswith(("claude", "anthropic")):
            if Anthropic is None:
                raise ImportError("请先 `pip install anthropic`")
            self.provider = "anthropic"
            self.client = (AsyncAnthropic if async_mode else Anthropic)(
                api_key = api_key
                       or anthropic_cfg.get("api_key")
                       or os.getenv("ANTHROPIC_API_KEY")
            )

        elif self.model_name.startswith("deepseek"):
            self.provider = "deepseek"
            self.api_key = api_key \
                        or deepseek_cfg.get("api_key") \
                        or os.getenv("DEEPSEEK_API_KEY")
            if self.api_key is None:
                raise ValueError("DeepSeek 需要 api_key，请在 JSON 或环境变量中配置")

        else:
            raise ValueError(f"无法识别的模型前缀: {model}")

        logger.info("LLMClient ready  provider=%s  model=%s", self.provider, model)

    # ------------------------------------------------------------------ #
    # 统一 chat() 接口
    # ------------------------------------------------------------------ #
    def chat(self, messages: List[Dict[str, str]], **kw) -> str:
        if self.provider == "openai":
            return self._chat_openai(messages, **kw)
        if self.provider == "anthropic":
            return self._chat_anthropic(messages, **kw)
        if self.provider == "deepseek":
            return self._chat_deepseek(messages, **kw)
        raise RuntimeError("Unknown provider")

    # ===== OpenAI ===================================================== #
    def _chat_openai(self, messages, **kw) -> str:
        resp = self.client.chat.completions.create(
            model=self.model_name, messages=messages, **kw
        )
        return resp.choices[0].message.content

    # ===== Anthropic ================================================== #
    def _chat_anthropic(self, messages, temperature: float = 0.7, **kw) -> str:
        resp = self.client.messages.create(
            model=self.model_name,
            system=next((m["content"] for m in messages if m["role"] == "system"), None),
            messages=[m for m in messages if m["role"] != "system"],
            temperature=temperature,
            **kw,
        )
        return resp.content[0].text

    # ===== DeepSeek =================================================== #
    def _chat_deepseek(self, messages, temperature: float = 0.7, **kw) -> str:
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            **kw,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=60.0) as client:
            r = client.post(DEEPSEEK_ENDPOINT, headers=headers, json=payload)
            if r.status_code != 200:
                print("DeepSeek 400 Body:", r.text)  # ← 打印服务端信息
            r.raise_for_status()
            r.raise_for_status()
            data = r.json()
        return data["choices"][0]["message"]["content"]

        return data["choices"][0]["message"]["content"]
class RouterAgent:
    """
    RouterAgent
    -----------
    把用户输入 ➜ LLM ➜ 解析为 JSON ➜ 返回最合适的 Workflow 规格

    Parameters
    ----------
    logger : logging.Logger, optional
        项目统一日志；若为空则自动创建子 logger。
    llm_client : LLMClient | None
        可注入自定义 LLM 客户端；为空时使用默认配置。
    model : str | None
        指定 LLM 名称；为空时由 LLMClient 决定。

    Public API
    ----------
    route(user_request: str) -> Dict[str, Any]
        主入口。返回形如
        {
            "workflow": "<workflow_name>",
            "arguments": { ... }
        }
    """

    # 如果你的系统中 workflow 名称很固定，也可以在这里列出以做校验
    _SUPPORTED_WORKFLOWS = {
        "code_implementation",
        "code_implementation_index",
        "codebase_index",
        "research_to_code",
    }

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        model: Optional[str] = None,
    ) -> None:
        self.logger = logger or self._create_default_logger()
        self.llm = LLMClient(model)
        self.logger.info("🔀 RouterAgent initialized (model=%s)", self.llm)

    # --------------------------------------------------------------------- #
    # Public method
    # --------------------------------------------------------------------- #
    @lru_cache(maxsize=128)
    def route(self, user_request: str) -> Dict[str, Any]:
        """
        Route a free‑form user request to the best workflow.
        对同一句话开启 LRU 缓存，可避免反复调用 LLM。

        Returns
        -------
        dict
            { "workflow": "...", "arguments": {...} }
        """
        self.logger.info("🔍 Routing user request ...")

        # 1. 调用 LLM
        messages = [
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": user_request},
        ]
        llm_raw = self.llm.chat(messages)
        self.logger.debug("LLM raw output: %s", llm_raw)
        print(llm_raw)

        # 2. 解析 JSON（带容错）
        try:
            router_spec = safe_json_loads(llm_raw)
        except ValueError as exc:
            self.logger.error("❌ RouterAgent | 无法解析为 JSON：%s", exc)
            raise

        # 3. 字段校验 & 补缺省
        workflow_name = router_spec.get("workflow")
        #if not workflow_name:
        #    raise ValueError("RouterAgent 输出缺少 `workflow` 字段")
        #if workflow_name not in self._SUPPORTED_WORKFLOWS:
        #    self.logger.warning("⚠️  workflow=%s 不在支持列表内，仍继续返回", workflow_name)

        #router_spec.setdefault("arguments", {})
        #self.logger.info("✅ RouterAgent | Routed to `%s`", Optional[,"新prompt"])
        return router_spec

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _create_default_logger(self) -> logging.Logger:
        logger = logging.getLogger(f"{__name__}.RouterAgent")
        logger.setLevel(logging.INFO)
        return logger


# --------------------------------------------------------------------------- #
# 简易 CLI / 单元测试（可选）
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # 调试
    import argparse, textwrap, json
#装在参数的容器
    parser = argparse.ArgumentParser(
        description="Quick interactive test for RouterAgent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            示例：
              python router_agent.py "请帮我复现attention is all you need 论文代码，并且写一个文档告诉我诠释它的原理"
              # 若不传参数，则默认使用“帮我复现 ResNet 论文代码”
            """
        ),
    )
    # 把位置参数改为可选参数 --query / -q，设置默认值
    parser.add_argument(
        "-q", "--query",
        type=str,
        default="请帮我写一个基于起源引擎的游戏，这个游戏内容是末世求生，一个人需要在丧尸病毒爆发的废土收集物资并活下去。游戏主要玩法包括第一人称射击和收集合成物资，以及人物剧情发展。请你帮我写一个完整的代码框架，并补充相应的剧情和世界观",
        help="用户自然语言请求（缺省为：帮我复现 ResNet 论文代码）",
    )
    args = parser.parse_args()#解析

    router = RouterAgent(model="deepseek-chat")  # deepseek 的模型名示例
    result = router.route(args.query)#添加参数
    print(json.dumps(result, indent=2, ensure_ascii=False))
