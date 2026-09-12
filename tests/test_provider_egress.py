"""Tests for the model-egress policy (P0-1).

Covers three layers:

1. ``evaluate_provider_egress`` — the pure decision kernel (no I/O, so the
   cases below are exhaustive rather than illustrative).
2. ``core.providers.registry`` endpoint classification — including that the
   pre-existing ``is_gateway`` boolean still works, since other tests and
   callers read it.
3. The wiring in ``core.config.make_llm_provider`` — the single provider
   construction point — plus the ``warn`` rollout mode.

No network access: an LLM provider client is constructed (lazily) but never
used, and the deny cases raise before construction.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import ConfigError, DeepCodeConfig, make_llm_provider
from core.providers.egress import (
    ALLOW_ENV,
    BLOCK_ENV,
    MODE_ENV,
    egress_mode,
    evaluate_provider_egress,
    parse_domain_list,
    resolve_egress_policy,
)
from core.providers.registry import (
    ProviderSpec,
    find_by_name,
)


@pytest.fixture(autouse=True)
def _clean_egress_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts from an unconfigured policy.

    Without this, a developer's shell (or a sibling test) that exports
    ``DEEPCODE_EGRESS_*`` would silently change the meaning of these cases.
    """

    for name in (ALLOW_ENV, BLOCK_ENV, MODE_ENV):
        monkeypatch.delenv(name, raising=False)


def _config(*, api_base: str | None, egress: dict | None = None) -> DeepCodeConfig:
    """A minimal deepseek config pointed at ``api_base`` (no network use)."""

    raw: dict = {
        "agents": {"defaults": {"provider": "deepseek", "model": "deepseek-chat"}},
        "providers": {
            "deepseek": {"apiKey": "sk-test-not-a-real-key", "apiBase": api_base},
        },
    }
    if egress is not None:
        raw["providers"]["egress"] = egress
    return DeepCodeConfig.model_validate(raw)


# -- 1. pure decision kernel -------------------------------------------------


def test_empty_api_base_is_allowed_and_unpoliced():
    for value in (None, "", "   "):
        decision = evaluate_provider_egress(
            value, allowed_domains=("api.deepseek.com",)
        )
        assert decision.allowed is True
        assert decision.host is None
        # No endpoint to judge -> no pretend reason either way.
        assert decision.reason is None
        assert decision.endpoint_class == "unknown"


def test_allow_listed_host_is_allowed_and_reason_is_none():
    decision = evaluate_provider_egress(
        "https://api.deepseek.com/v1",
        endpoint_class="first_party",
        allowed_domains=("api.deepseek.com",),
    )
    assert decision.allowed is True
    assert decision.host == "api.deepseek.com"
    assert decision.endpoint_class == "first_party"
    assert decision.reason is None


def test_allow_list_matches_subdomains_not_suffix_lookalikes():
    allowed = ("deepseek.com",)
    assert evaluate_provider_egress(
        "https://api.deepseek.com/v1", allowed_domains=allowed
    ).allowed
    # "evil-deepseek.com" must not match "deepseek.com" (no partial-label match).
    denied = evaluate_provider_egress(
        "https://evil-deepseek.com/v1", allowed_domains=allowed
    )
    assert denied.allowed is False


def test_host_outside_allow_list_is_denied_with_actionable_reason():
    decision = evaluate_provider_egress(
        "https://api.siliconflow.cn/v1",
        endpoint_class="unknown",
        allowed_domains=("api.deepseek.com",),
    )
    assert decision.allowed is False
    assert decision.host == "api.siliconflow.cn"
    assert decision.reason is not None
    assert "api.siliconflow.cn" in decision.reason
    assert "allow-list" in decision.reason
    assert "endpoint_class=unknown" in decision.reason


def test_blocked_host_is_denied_even_without_allow_list():
    # An empty allow-list means "allow all unless blocked" — this is the
    # preserved historical meaning, and the block-list must still bite.
    decision = evaluate_provider_egress(
        "https://api.siliconflow.cn/v1",
        blocked_domains=("siliconflow.cn",),
    )
    assert decision.allowed is False
    assert decision.host == "api.siliconflow.cn"
    assert decision.reason is not None
    assert "block-list" in decision.reason


def test_block_wins_over_allow_for_the_same_host():
    decision = evaluate_provider_egress(
        "https://api.siliconflow.cn/v1",
        allowed_domains=("siliconflow.cn",),
        blocked_domains=("api.siliconflow.cn",),
    )
    assert decision.allowed is False


def test_empty_allow_list_keeps_allow_all_semantics():
    decision = evaluate_provider_egress("https://anything.example.com/v1")
    assert decision.allowed is True
    assert decision.host == "anything.example.com"
    assert decision.reason is None


@pytest.mark.parametrize(
    "api_base",
    [
        "api.deepseek.com/v1",  # missing scheme
        "ftp://api.deepseek.com/v1",  # unsupported scheme
        "file:///etc/passwd",
        "https://",  # no hostname
        "http://[::1",  # unparseable (invalid IPv6)
    ],
)
def test_unparseable_or_non_http_base_is_denied(api_base: str):
    decision = evaluate_provider_egress(api_base)
    assert decision.allowed is False
    assert decision.reason is not None


def test_http_is_accepted_because_local_servers_use_it():
    # Ollama/vLLM over loopback is plain HTTP on purpose; the allow-list, not
    # the scheme, is what constrains it.
    decision = evaluate_provider_egress(
        "http://localhost:11434/v1",
        endpoint_class="local",
        allowed_domains=("localhost",),
    )
    assert decision.allowed is True
    assert decision.host == "localhost"


def test_invalid_policy_entry_fails_closed():
    # A malformed allow-list entry is not silently dropped: is_domain_allowed
    # rejects the whole policy, so a typo cannot widen the gate.
    decision = evaluate_provider_egress(
        "https://api.deepseek.com/v1",
        allowed_domains=("not a domain/with/slash",),
    )
    assert decision.allowed is False


# -- 2. endpoint classification ---------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("deepseek", "first_party"),
        ("gemini", "first_party"),
        ("anthropic", "first_party"),
        ("openai", "first_party"),
        ("openrouter", "gateway"),
        ("requesty", "gateway"),
        ("forge", "gateway"),
        ("zhipu", "aggregator"),
        ("dashscope", "aggregator"),
        ("minimax", "aggregator"),
        ("ollama", "local"),
        ("vllm", "local"),
    ],
)
def test_endpoint_class_per_spec_entry(name: str, expected: str):
    spec = find_by_name(name)
    assert spec is not None
    assert spec.resolve_endpoint_class(spec.default_api_base or None) == expected


def test_custom_endpoint_is_unknown_unless_it_looks_local():
    custom = find_by_name("custom")
    assert custom is not None
    assert custom.resolve_endpoint_class("https://api.siliconflow.cn/v1") == "unknown"
    assert custom.resolve_endpoint_class("http://localhost:8080/v1") == "local"
    assert custom.resolve_endpoint_class("http://127.0.0.1:8000/v1") == "local"
    assert custom.resolve_endpoint_class("http://ollama:11434/v1") == "local"
    assert custom.resolve_endpoint_class(None) == "unknown"


def test_is_gateway_still_works_alongside_endpoint_class():
    # Existing callers/tests read the boolean; the upgrade must not break it.
    assert find_by_name("openrouter").is_gateway is True
    assert find_by_name("minimax").is_gateway is False
    assert find_by_name("openrouter").endpoint_class == "gateway"


def test_unset_class_derives_pessimistically():
    gateway = ProviderSpec(name="x", keywords=(), env_key="", is_gateway=True)
    assert gateway.resolve_endpoint_class("https://relay.example.com/v1") == "gateway"

    unclassified = ProviderSpec(name="y", keywords=(), env_key="")
    assert unclassified.resolve_endpoint_class("https://who-knows.example/v1") == (
        "unknown"
    )

    # An explicit class always wins — including a pessimistic one.
    claimed = ProviderSpec(
        name="z", keywords=(), env_key="", is_gateway=True, endpoint_class="unknown"
    )
    assert claimed.resolve_endpoint_class(None) == "unknown"


def test_repointed_api_base_is_not_flattered_by_the_specs_class():
    # `providers.deepseek.apiBase` pointing at a relay must not report
    # first_party: the class describes the endpoint the request will hit.
    deepseek = find_by_name("deepseek")
    assert deepseek.resolve_endpoint_class("https://api.siliconflow.cn/v1") == "unknown"
    assert deepseek.resolve_endpoint_class("http://localhost:8080/v1") == "local"

    # No declared base to compare against -> the explicit class is not trusted.
    anthropic = find_by_name("anthropic")
    assert anthropic.resolve_endpoint_class("https://relay.example.com") == "unknown"


# -- 3. env / config policy resolution --------------------------------------


def test_parse_domain_list_trims_lowercases_and_dedupes():
    assert parse_domain_list(None) == ()
    assert parse_domain_list("") == ()
    assert parse_domain_list(" , , ") == ()
    assert parse_domain_list(
        " api.DeepSeek.com , api.siliconflow.cn,,API.deepseek.COM "
    ) == (
        "api.deepseek.com",
        "api.siliconflow.cn",
    )
    assert parse_domain_list(["a.com", " a.com ", "", 7]) == ("a.com",)  # type: ignore[list-item]


def test_resolve_policy_reads_config_and_env_union(monkeypatch: pytest.MonkeyPatch):
    config = _config(
        api_base=None,
        egress={
            "allowedDomains": ["api.deepseek.com"],
            "blockedDomains": ["evil.example.com"],
        },
    )
    monkeypatch.setenv(ALLOW_ENV, "api.siliconflow.cn, ")
    monkeypatch.setenv(BLOCK_ENV, "attacker.example")

    policy = resolve_egress_policy(config)
    assert policy.allowed_domains == ("api.deepseek.com", "api.siliconflow.cn")
    assert policy.blocked_domains == ("evil.example.com", "attacker.example")
    # Unset mode falls back to enforce: a missing var must not open the gate.
    assert policy.mode == "enforce"


def test_env_lists_extend_rather_than_replace_config():
    # An env allow-list cannot un-block a host the config blocks (deny wins);
    # it also cannot drop a host the config allows.
    config = _config(
        api_base=None,
        egress={"allowedDomains": ["api.deepseek.com"], "blockedDomains": []},
    )
    policy = resolve_egress_policy(config)
    decision = evaluate_provider_egress(
        "https://api.siliconflow.cn/v1",
        allowed_domains=policy.allowed_domains,
        blocked_domains=policy.blocked_domains,
    )
    assert decision.allowed is False


def test_policy_from_plain_dict_and_from_missing_config():
    # A dict-shaped config (JSON round-trip, tests) must be readable too.
    policy = resolve_egress_policy(
        {"providers": {"egress": {"allowedDomains": ["a.example.com"]}}}
    )
    assert policy.allowed_domains == ("a.example.com",)

    # Anything that is not a DEEPCODE config resolves to the neutral policy.
    neutral = resolve_egress_policy(None)
    assert neutral.allowed_domains == ()
    assert neutral.blocked_domains == ()
    assert neutral.mode == "enforce"
    assert resolve_egress_policy(object()).allowed_domains == ()


def test_unrecognized_mode_falls_back_to_enforce(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(MODE_ENV, "yolo")
    assert egress_mode() == "enforce"
    assert resolve_egress_policy(None).mode == "enforce"

    monkeypatch.setenv(MODE_ENV, " WARN ")
    assert egress_mode() == "warn"
    assert resolve_egress_policy(None).mode == "warn"

    monkeypatch.delenv(MODE_ENV)
    assert egress_mode() == "enforce"


def test_config_mode_is_honored(monkeypatch: pytest.MonkeyPatch):
    config = _config(api_base=None, egress={"mode": "warn"})
    assert resolve_egress_policy(config).mode == "warn"
    # Env still wins over the config file.
    monkeypatch.setenv(MODE_ENV, "enforce")
    assert resolve_egress_policy(config).mode == "enforce"


# -- 4. wiring in make_llm_provider -----------------------------------------


def test_make_llm_provider_denies_host_outside_allow_list(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(ALLOW_ENV, "api.deepseek.com")
    config = _config(api_base="https://api.siliconflow.cn/v1")

    with pytest.raises(ConfigError) as excinfo:
        make_llm_provider(config)

    message = str(excinfo.value)
    # Actionable: names the host, the class, and every knob that would allow it.
    assert "api.siliconflow.cn" in message
    assert "endpoint_class=unknown" in message
    assert ALLOW_ENV in message
    assert BLOCK_ENV in message
    assert MODE_ENV in message
    assert "warn" in message


def test_make_llm_provider_allows_first_party_host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ALLOW_ENV, "api.deepseek.com")
    config = _config(api_base="https://api.deepseek.com")

    provider = make_llm_provider(config)

    # Constructed lazily — no request is made here.
    assert provider.__class__.__name__ == "OpenAICompatProvider"
    assert provider._effective_base == "https://api.deepseek.com"


def test_make_llm_provider_allows_blocked_free_host_without_allow_list(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(BLOCK_ENV, "api.siliconflow.cn")
    config = _config(api_base="https://api.deepseek.com")
    assert make_llm_provider(config) is not None


def test_warn_mode_logs_instead_of_raising(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ALLOW_ENV, "api.deepseek.com")
    monkeypatch.setenv(MODE_ENV, "warn")
    config = _config(api_base="https://api.siliconflow.cn/v1")

    captured: list[str] = []
    sink_id = logger.add(lambda message: captured.append(message), level="WARNING")
    try:
        provider = make_llm_provider(config)
    finally:
        logger.remove(sink_id)

    assert provider is not None
    assert any("api.siliconflow.cn" in message for message in captured)
    assert any("egress blocked" in message for message in captured)
