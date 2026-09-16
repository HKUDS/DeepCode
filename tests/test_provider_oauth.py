from __future__ import annotations

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen
from urllib.error import HTTPError

import httpx
import pytest

from core.config import ConnectionProfileConfig, DeepCodeConfig
from core.domain.execution_profile import ExecutionSelection
from core.providers.credentials import CredentialStore
from core.providers.oauth import ProviderOAuthManager
from core.providers.profiles import ConnectionResolver


def mock_exchange(monkeypatch, account="user-A", key="oauth-private-key"):
    original = httpx.AsyncClient
    seen = []

    def handler(request):
        assert str(request.url) == "https://openrouter.ai/api/v1/auth/keys"
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"key": key, "user_id": account})

    monkeypatch.setattr(
        "core.providers.oauth.httpx.AsyncClient",
        lambda **kw: original(**kw, transport=httpx.MockTransport(handler)),
    )
    return seen


def callback(flow):
    return parse_qs(urlsplit(flow["authorizationUrl"]).query)["callback_url"][0]


def finish(flow):
    with urlopen(
        callback(flow) + "?" + urlencode({"code": "single-use-code"}), timeout=5
    ) as response:
        return response.read().decode()


def test_pkce_real_loopback_callback_binds_account_without_exposing_key(
    tmp_path, monkeypatch
):
    seen = mock_exchange(monkeypatch)
    credentials = CredentialStore(tmp_path / "credentials.json")
    manager = ProviderOAuthManager(credentials)
    try:
        flow = manager.start("router")
        with pytest.raises(HTTPError) as error:
            urlopen(callback(flow) + "-wrong?code=bad", timeout=2)
        assert error.value.code == 404 and seen == []
        page = finish(flow)
        result = manager.poll(flow["flowId"])
        assert result["status"] == "authenticated"
        assert result["accountId"] == "user-A" and result["refreshSupported"] is False
        assert result["authorizationUrl"] is None
        params = parse_qs(urlsplit(flow["authorizationUrl"]).query)
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(seen[0]["code_verifier"].encode()).digest()
            )
            .decode()
            .rstrip("=")
        )
        assert params["code_challenge"] == [challenge]
        assert seen[0]["code_challenge_method"] == "S256" and len(seen) == 1
        assert credentials.oauth_credential("router") == ("oauth-private-key", "user-A")
        assert "oauth-private-key" not in json.dumps(result) + page
        assert "single-use-code" not in page and "replaceState" in page
    finally:
        manager.close()


def test_other_process_logout_prevents_pending_flow_from_restoring_credentials(
    tmp_path, monkeypatch
):
    mock_exchange(monkeypatch)
    credentials = CredentialStore(tmp_path / "credentials.json")
    manager = ProviderOAuthManager(credentials)
    try:
        flow = manager.start("router")
        CredentialStore(credentials.path).clear("router")
        finish(flow)
        assert manager.poll(flow["flowId"])["status"] == "failed"
        assert credentials.get("router") is None
    finally:
        manager.close()


def test_cancelled_flow_and_account_switch_cannot_replace_existing_account(
    tmp_path, monkeypatch
):
    credentials = CredentialStore(tmp_path / "credentials.json")
    credentials.complete_login(
        "router",
        credentials.begin_login("router"),
        api_key="original",
        account_id="user-A",
    )
    mock_exchange(monkeypatch, account="user-B", key="wrong-account")
    manager = ProviderOAuthManager(credentials)
    try:
        flow = manager.start("router")
        finish(flow)
        assert manager.poll(flow["flowId"])["status"] == "failed"
        assert credentials.oauth_credential("router") == ("original", "user-A")
        pending = manager.start("router")
        assert manager.cancel(pending["flowId"])["status"] == "cancelled"
        assert credentials.oauth_credential("router") == ("original", "user-A")
    finally:
        manager.close()
    assert all(not flow.thread.is_alive() for flow in manager._flows.values())


def test_oauth_route_never_borrows_environment_and_old_turn_cannot_switch_accounts(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "ambient-key")
    credentials = CredentialStore(tmp_path / "credentials.json")
    config = DeepCodeConfig.model_validate(
        {
            "providers": {
                "profiles": {
                    "router": {
                        "template": "openrouter",
                        "auth": "oauth",
                        "protocol": "openai_chat",
                    }
                }
            }
        }
    )
    resolver = ConnectionResolver(config, credentials)
    assert not resolver.resolve_connection("router").is_usable
    credentials.complete_login(
        "router",
        credentials.begin_login("router"),
        api_key="key-A",
        account_id="user-A",
    )
    profile = resolver.execution_profile(ExecutionSelection("router", "model"))
    assert resolver.connection_for_profile(profile).api_key == "key-A"
    credentials.clear("router")
    credentials.complete_login(
        "router",
        credentials.begin_login("router"),
        api_key="key-B",
        account_id="user-B",
    )
    with pytest.raises(ValueError, match="identity changed"):
        resolver.connection_for_profile(profile)
    with pytest.raises(ValueError, match="official OpenRouter"):
        ConnectionProfileConfig(
            template="openrouter", auth="oauth", api_base="https://gateway.example/v1"
        )
    with pytest.raises(ValueError, match="official OpenRouter"):
        ConnectionProfileConfig(
            template="openrouter", auth="oauth", api_key_env="SOME_KEY"
        )


def test_flow_expiry_closes_callback_without_storing_a_credential(tmp_path):
    credentials = CredentialStore(tmp_path / "credentials.json")
    manager = ProviderOAuthManager(credentials)
    try:
        flow = manager.start("router")
        manager._flows[flow["flowId"]].expires_at = time.monotonic() - 1
        deadline = time.monotonic() + 2
        while (
            manager.poll(flow["flowId"])["status"] != "expired"
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert manager.poll(flow["flowId"])["status"] == "expired"
        assert credentials.get("router") is None
    finally:
        manager.close()
    assert not manager._flows[flow["flowId"]].thread.is_alive()
