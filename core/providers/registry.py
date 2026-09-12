"""Provider registry — single source of truth for LLM provider metadata.

Pruned port of ``nanobot.providers.registry``. Includes only the providers
DeepCode actively supports today; add new entries by appending a
:class:`ProviderSpec` to :data:`PROVIDERS`.

Adding a new provider:
  1. Add a ``ProviderSpec`` to ``PROVIDERS`` below (order = match priority).
  2. If you need a new backend, instantiate it in
     :func:`core.config.make_llm_provider` based on ``spec.backend``.
  3. Classify the endpoint with ``endpoint_class`` (see :data:`ENDPOINT_CLASSES`).

Endpoint classification (P0-1) exists because ``api_base`` *is* the trust
boundary: whoever terminates TLS for it reads every prompt and tool result in
plaintext, and can rewrite tool calls in the response. The classification is
descriptive, not a verdict — the egress allow/block lists decide — but it is
what a denial message and the provider audit trail report, so it must not
flatter a third-party relay into looking first-party.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

_SNAKE_PATTERN = re.compile(r"(?<!^)(?=[A-Z])")

ENDPOINT_CLASSES: tuple[str, ...] = (
    # The model vendor's own API surface: ``api.deepseek.com``,
    # ``generativelanguage.googleapis.com``, ``api.anthropic.com``. Not a
    # trust guarantee (the vendor is still the largest single observer), but
    # there is no *intermediary* in the path.
    "first_party",
    # A router that fans one base_url out to many vendors and re-encodes the
    # request per vendor (OpenRouter / Requesty / Forge). Multi-tenant by
    # design: the same operator sees traffic for every model you use.
    "gateway",
    # A hosted reseller or third-party mirror of one vendor's API
    # (Zhipu/DashScope/MiniMax and friends). Looks first-party from the wire
    # format, but the endpoint is somebody else's deployment.
    "aggregator",
    # Loopback/LAN endpoint the operator runs (Ollama, vLLM).
    "local",
    # No claim about the operator. Callers must treat this as untrusted.
    "unknown",
)

DEFAULT_ENDPOINT_CLASS = "unknown"

# Hosts that clearly belong to the machine or its LAN. Only used to classify a
# `custom` endpoint the operator pointed at something local; the raw hostname
# still goes through the allow/block policy like any other.
_LOCAL_HOSTNAMES = frozenset({"localhost", "::1", "0.0.0.0", "host.docker.internal"})
_LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal")
_LOOPBACK_PREFIX = "127."


def endpoint_host(api_base: str | None) -> str | None:
    """Extract the lowercased hostname from an API base URL, or ``None``.

    Returns ``None`` for a missing or malformed base; callers treat that as
    "cannot classify", never as a match.
    """

    if not api_base or not isinstance(api_base, str):
        return None
    try:
        host = (urlparse(api_base.strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        # Malformed URL (bad port, unbalanced IPv6 brackets) — the egress check
        # rejects it with a reason of its own.
        return None
    return host or None


def host_looks_local(api_base: str | None) -> bool:
    """Best-effort "this endpoint is not on the public internet" check.

    A dotless hostname (``http://ollama:11434/v1``) counts as local: public DNS
    names the operator would route model traffic to always have a suffix, and
    a dotted IP-less name is the LAN case we care about. Loopback/IPv6-loopback
    and ``*.local``/``*.internal`` are always local.
    """

    host = endpoint_host(api_base)
    if not host:
        return False
    if host in _LOCAL_HOSTNAMES:
        return True
    if host.startswith(_LOOPBACK_PREFIX) or host.endswith(_LOCAL_HOST_SUFFIXES):
        return True
    return "." not in host


def _to_snake(name: str) -> str:
    return _SNAKE_PATTERN.sub("_", name).lower()


@dataclass(frozen=True)
class ProviderSpec:
    """One LLM provider's metadata.

    ``is_gateway`` is the pre-existing boolean ("this endpoint is not the model
    vendor") and is kept because callers/tests read it. ``endpoint_class`` is
    its finer-grained successor; see :data:`ENDPOINT_CLASSES` for the values and
    :meth:`resolve_endpoint_class` for how an unset class is derived.
    """

    name: str
    keywords: tuple[str, ...]
    env_key: str
    display_name: str = ""
    backend: str = "openai_compat"
    is_gateway: bool = False
    # Empty means "not classified here" — `resolve_endpoint_class()` derives a
    # default from `is_gateway`/`is_local`/`requires_api_base` so an entry
    # added without a classification still fails closed as `unknown`.
    endpoint_class: str = ""
    is_local: bool = False
    detect_by_key_prefix: str = ""
    detect_by_base_keyword: str = ""
    default_api_base: str = ""
    requires_api_base: bool = False
    strip_model_prefix: bool = False
    supports_max_completion_tokens: bool = False
    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = field(
        default_factory=tuple
    )
    is_oauth: bool = False
    is_direct: bool = False
    supports_prompt_caching: bool = False
    thinking_style: str = ""

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()

    def resolve_endpoint_class(self, api_base: str | None = None) -> str:
        """Return the endpoint class for this spec given the effective base.

        An explicit ``endpoint_class`` describes the *declared* endpoint, so it
        only applies when the resolved base actually is that endpoint. A user
        who repoints ``providers.deepseek.apiBase`` at a relay must not be told
        their traffic is ``first_party``: for an overridden base the class is
        re-derived from the hostname (``local`` for loopback/LAN, else
        ``unknown``). An unflattering label is the point — the class is what a
        denial message and the audit trail report.

        When ``endpoint_class`` is unset the derivation is: a known gateway
        stays a ``gateway`` (never "first_party"), a local server is ``local``,
        a ``custom``/``requires_api_base`` entry pointing at loopback or a LAN
        name is ``local``, and everything else is ``unknown``.
        """

        if self.endpoint_class:
            if self._matches_declared_endpoint(api_base):
                return self.endpoint_class
            # Overridden base: the declaration no longer describes the endpoint
            # the request will actually hit.
            return "local" if host_looks_local(api_base) else DEFAULT_ENDPOINT_CLASS
        if self.is_local or host_looks_local(api_base):
            return "local"
        if self.is_gateway:
            return "gateway"
        return DEFAULT_ENDPOINT_CLASS

    def _matches_declared_endpoint(self, api_base: str | None) -> bool:
        """Is ``api_base`` the endpoint this spec declared (or nothing at all)?

        No base -> the SDK/spec default is used, so the declaration holds. A
        base with no declared counterpart (``anthropic``/``openai``, whose SDK
        picks the vendor default) cannot be verified, so the declaration is not
        trusted for it.
        """

        if not api_base:
            return True
        if not self.default_api_base:
            return False
        return endpoint_host(api_base) == endpoint_host(self.default_api_base)


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="custom",
        keywords=(),
        env_key="",
        display_name="Custom",
        backend="openai_compat",
        is_direct=True,
        requires_api_base=True,
        # Deliberately unclassified: `api_base` is user-supplied, so the class
        # is derived per-construction (local for loopback/LAN, else unknown).
    ),
    ProviderSpec(
        name="openrouter",
        keywords=("openrouter",),
        env_key="OPENROUTER_API_KEY",
        display_name="OpenRouter",
        backend="openai_compat",
        is_gateway=True,
        endpoint_class="gateway",
        detect_by_key_prefix="sk-or-",
        detect_by_base_keyword="openrouter",
        default_api_base="https://openrouter.ai/api/v1",
        supports_prompt_caching=True,
    ),
    ProviderSpec(
        name="requesty",
        keywords=("requesty",),
        env_key="REQUESTY_API_KEY",
        display_name="Requesty",
        backend="openai_compat",
        is_gateway=True,
        endpoint_class="gateway",
        detect_by_base_keyword="requesty",
        default_api_base="https://router.requesty.ai/v1",
        supports_prompt_caching=True,
    ),
    ProviderSpec(
        name="forge",
        keywords=("forge",),
        env_key="FORGE_API_KEY",
        display_name="Forge",
        backend="openai_compat",
        is_gateway=True,
        endpoint_class="gateway",
        detect_by_base_keyword="forge.tensorblock.co",
        default_api_base="https://api.forge.tensorblock.co/v1",
        # Forge resolves bare model ids, not ``vendor/model`` — unlike the
        # OpenRouter-style gateways above.
        strip_model_prefix=True,
    ),
    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        backend="anthropic",
        endpoint_class="first_party",
        supports_prompt_caching=True,
    ),
    ProviderSpec(
        name="openai",
        keywords=("openai", "gpt"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
        backend="openai_compat",
        endpoint_class="first_party",
        supports_max_completion_tokens=True,
    ),
    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        backend="openai_compat",
        endpoint_class="first_party",
        default_api_base="https://api.deepseek.com",
        thinking_style="thinking_type",
    ),
    ProviderSpec(
        name="gemini",
        keywords=("gemini",),
        env_key="GEMINI_API_KEY",
        display_name="Gemini",
        backend="openai_compat",
        endpoint_class="first_party",
        default_api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
    ProviderSpec(
        name="zhipu",
        keywords=("zhipu", "glm", "zai"),
        env_key="ZAI_API_KEY",
        display_name="Zhipu AI",
        backend="openai_compat",
        endpoint_class="aggregator",
        default_api_base="https://open.bigmodel.cn/api/paas/v4",
        # GLM takes the same ``thinking: {"type": ...}`` body as DeepSeek.
        thinking_style="thinking_type",
    ),
    ProviderSpec(
        name="dashscope",
        keywords=("qwen", "dashscope"),
        env_key="DASHSCOPE_API_KEY",
        display_name="DashScope",
        backend="openai_compat",
        endpoint_class="aggregator",
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        thinking_style="enable_thinking",
    ),
    ProviderSpec(
        name="minimax",
        keywords=("minimax", "abab"),
        env_key="MINIMAX_API_KEY",
        display_name="MiniMax",
        backend="openai_compat",
        endpoint_class="aggregator",
        default_api_base="https://api.minimax.io/v1",
    ),
    ProviderSpec(
        name="vllm",
        keywords=("vllm",),
        env_key="HOSTED_VLLM_API_KEY",
        display_name="vLLM/Local",
        backend="openai_compat",
        is_local=True,
        endpoint_class="local",
        requires_api_base=True,
    ),
    ProviderSpec(
        name="ollama",
        keywords=("ollama", "nemotron"),
        env_key="OLLAMA_API_KEY",
        display_name="Ollama",
        backend="openai_compat",
        is_local=True,
        endpoint_class="local",
        detect_by_base_keyword="11434",
        default_api_base="http://localhost:11434/v1",
    ),
)


def find_by_name(name: str) -> ProviderSpec | None:
    """Find a provider spec by config field name, e.g. ``"deepseek"``."""
    normalized = _to_snake(name.replace("-", "_"))
    for spec in PROVIDERS:
        if spec.name == normalized:
            return spec
    return None


def find_by_model(
    model: str | None,
    *,
    available_provider_names: set[str] | None = None,
) -> ProviderSpec | None:
    """Match a provider spec by model name keywords.

    If ``available_provider_names`` is supplied, providers absent from that
    set are skipped; otherwise every spec is considered. Returns ``None`` when
    nothing matches.
    """
    if not model:
        return None

    model_lower = model.lower()
    model_normalized = model_lower.replace("-", "_")
    model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
    normalized_prefix = model_prefix.replace("-", "_")

    def _kw_matches(kw: str) -> bool:
        kw = kw.lower()
        return kw in model_lower or kw.replace("-", "_") in model_normalized

    def _eligible(spec: ProviderSpec) -> bool:
        if available_provider_names is None:
            return True
        return spec.name in available_provider_names

    for spec in PROVIDERS:
        if _eligible(spec) and model_prefix and normalized_prefix == spec.name:
            return spec

    for spec in PROVIDERS:
        if _eligible(spec) and any(_kw_matches(kw) for kw in spec.keywords):
            return spec

    return None
