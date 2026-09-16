from __future__ import annotations

import json

import httpx
import pytest
from openai import AsyncOpenAI

from core.config import ConnectionProfileConfig, DeepCodeConfig
from core.domain.execution_profile import ExecutionProfile, ExecutionSelection
from core.providers.credentials import CredentialStore
from core.providers.openai_compat import OpenAICompatProvider
from core.providers.profiles import ConnectionResolver
from core.providers.protocol_config import ProviderCompat
from core.providers.registry import find_by_name

CHAT = {
    "id": "chat",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "OK"},
            "finish_reason": "stop",
        }
    ],
}
RESPONSE = {
    "id": "resp",
    "object": "response",
    "status": "completed",
    "output": [
        {
            "type": "message",
            "id": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "OK", "annotations": []}],
        }
    ],
}


def mock_sdk(monkeypatch, handler):
    def client(**kwargs):
        return AsyncOpenAI(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr("core.providers.openai_compat.AsyncOpenAI", client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("protocol", "path", "model"),
    [
        ("openai_chat", "/v1/chat/completions", "gpt-5"),
        ("openai_responses", "/v1/responses", "plain-model"),
    ],
)
async def test_explicit_protocol_and_no_auth_use_the_selected_gateway(
    monkeypatch, protocol, path, model
):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=CHAT if protocol == "openai_chat" else RESPONSE)

    mock_sdk(monkeypatch, handler)
    provider = OpenAICompatProvider(
        api_base="https://gateway.example/v1",
        default_model=model,
        protocol=protocol,
        auth_mode="none",
        spec=find_by_name("custom"),
    )
    try:
        reply = await provider.chat([{"role": "user", "content": "hello"}])
        assert reply.content == "OK"
        assert [request.url.path for request in seen] == [path]
        assert "authorization" not in seen[0].headers
        if protocol == "openai_responses":
            assert "reasoning" not in json.loads(seen[0].content)
    finally:
        await provider._client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["auto", "openai_responses"])
async def test_only_legacy_auto_may_fallback(monkeypatch, protocol):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                404,
                json={
                    "error": {
                        "message": "Responses API is not supported",
                        "type": "invalid_request_error",
                    }
                },
            )
        return httpx.Response(200, json=CHAT)

    mock_sdk(monkeypatch, handler)
    provider = OpenAICompatProvider(
        api_key="test-key",
        api_base="https://api.openai.com/v1",
        default_model="gpt-5",
        protocol=protocol,
        spec=find_by_name("openai"),
    )
    try:
        reply = await provider.chat([{"role": "user", "content": "hello"}])
        assert paths == (
            ["/v1/responses", "/v1/chat/completions"]
            if protocol == "auto"
            else ["/v1/responses"]
        )
        assert (reply.finish_reason == "error") == (protocol == "openai_responses")
    finally:
        await provider._client.close()


def test_explicit_compat_overrides_wire_defaults_without_mutating_history():
    provider = object.__new__(OpenAICompatProvider)
    provider.default_model = "gpt-5"
    provider._spec = find_by_name("openai")
    provider.compat = ProviderCompat(
        token_limit_field="max_tokens",
        temperature=False,
        system_role="developer",
        reasoning_field="omit",
        reasoning_content="omit",
        tool_message_name=False,
        parallel_tool_calls=False,
    )
    history = [
        {"role": "system", "content": "instructions"},
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "private trace",
            "tool_calls": [
                {
                    "id": "call",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call", "name": "read", "content": "result"},
    ]
    original = json.dumps(history)
    body = provider._build_kwargs(
        history,
        [
            {
                "type": "function",
                "function": {"name": "read", "parameters": {"type": "object"}},
            }
        ],
        None,
        128,
        0.2,
        "high",
        None,
    )
    assert body["max_tokens"] == 128 and "max_completion_tokens" not in body
    assert "temperature" not in body and "reasoning_effort" not in body
    assert body["messages"][0]["role"] == "developer"
    assert "reasoning_content" not in body["messages"][1]
    assert "name" not in body["messages"][2]
    assert body["parallel_tool_calls"] is False
    assert json.dumps(history) == original


@pytest.mark.asyncio
async def test_frozen_route_live_rotation_and_revocation(monkeypatch, tmp_path):
    requests = []

    def handler(request):
        requests.append((request.url.host, request.headers.get("authorization")))
        return httpx.Response(200, json=CHAT)

    mock_sdk(monkeypatch, handler)
    credentials = CredentialStore(tmp_path / "credentials.json")
    credentials.set("route", "first-private-key")
    config = DeepCodeConfig.model_validate(
        {
            "providers": {
                "profiles": {
                    "route": {
                        "template": "custom",
                        "protocol": "openai_chat",
                        "apiBase": "https://old.example/v1",
                        "extraHeaders": {"X-Custom-Auth": "private-header"},
                        "compat": {"temperature": False},
                    }
                }
            }
        }
    )
    current = [config]
    resolver = ConnectionResolver(config, credentials, config_loader=lambda: current[0])
    profile = resolver.execution_profile(
        ExecutionSelection(connection_id="route", model_id="plain-model")
    )
    public = json.dumps(profile.to_dict())
    assert "private-header" not in public and "first-private-key" not in public
    revision = resolver.revisions.get(profile.provider_revision)
    assert revision["extraHeaders"]["X-Custom-Auth"] == "private-header"
    assert "first-private-key" not in json.dumps(revision)
    provider = resolver.build_provider(profile)
    try:
        changed = config.model_copy(deep=True)
        changed.providers.profiles["route"].api_base = "https://new.example/v1"
        changed.providers.profiles["route"].compat = ProviderCompat(temperature=True)
        current[0] = changed
        assert (
            await provider.chat([{"role": "user", "content": "hello"}])
        ).content == "OK"
        assert requests == [("old.example", "Bearer first-private-key")]
        current[0] = config
        credentials.set("route", "rotated-private-key")
        assert (
            await provider.chat([{"role": "user", "content": "hello"}])
        ).content == "OK"
        assert requests[-1] == ("old.example", "Bearer rotated-private-key")
        credentials.clear("route")
        revoked = await provider.chat_with_retry([{"role": "user", "content": "hello"}])
        assert revoked.finish_reason == "error"
        assert (
            revoked.error_kind == "configuration"
            and revoked.error_should_retry is False
        )
        assert len(requests) == 2
        assert ExecutionProfile.from_dict(profile.to_dict()) == profile
    finally:
        await provider._client.close()


def test_protocol_conflict_errors_do_not_echo_headers():
    with pytest.raises(ValueError) as error:
        ConnectionProfileConfig.model_validate(
            {
                "adapter": "anthropic",
                "protocol": "openai_chat",
                "extraHeaders": {"Authorization": "must-not-be-logged"},
            }
        )
    assert "must-not-be-logged" not in str(error.value)
    with pytest.raises(ValueError):
        ConnectionProfileConfig(
            protocol="openai_responses", compat={"tokenLimitField": "max_tokens"}
        )
    with pytest.raises(ValueError):
        ProviderCompat.model_validate({"arbitraryRequestOverride": {"messages": []}})


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["openai_chat", "openai_responses"])
async def test_partial_stream_is_not_retried_and_is_closed(monkeypatch, protocol):
    class BrokenStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            if protocol == "openai_chat":
                event = {
                    "id": "c",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": "partial"}}],
                }
                yield f"data: {json.dumps(event)}\n\n".encode()
            else:
                event = {
                    "type": "response.output_text.delta",
                    "delta": "partial",
                    "item_id": "m",
                    "output_index": 0,
                    "content_index": 0,
                    "sequence_number": 1,
                }
                yield f"event: response.output_text.delta\ndata: {json.dumps(event)}\n\n".encode()
            raise httpx.ReadError("connection lost after content")

        async def aclose(self):
            self.closed = True

    stream = BrokenStream()
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        )

    mock_sdk(monkeypatch, handler)
    provider = OpenAICompatProvider(
        api_key="test",
        api_base="https://gateway.example/v1",
        default_model="plain",
        protocol=protocol,
    )
    deltas = []

    async def delta(value):
        deltas.append(value)

    try:
        result = await provider.chat_stream_with_retry(
            [{"role": "user", "content": "hello"}], on_content_delta=delta
        )
        assert result.finish_reason == "error" and result.partial_output
        assert result.error_should_retry is False
        assert len(calls) == 1 and deltas == ["partial"] and stream.closed
    finally:
        await provider._client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_explicit_responses_errors_never_change_protocol(monkeypatch, status):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(
            status, json={"error": {"message": "gateway error", "type": "api_error"}}
        )

    mock_sdk(monkeypatch, handler)
    provider = OpenAICompatProvider(
        api_key="test",
        api_base="https://gateway.example/v1",
        protocol="openai_responses",
    )
    try:
        response = await provider.chat([{"role": "user", "content": "hello"}])
        assert response.error_status_code == status
        assert seen == ["/v1/responses"]
    finally:
        await provider._client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("temperature_override", [None, False])
async def test_anthropic_wire_keeps_selected_key_and_capability_overrides(
    monkeypatch, temperature_override
):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from core.providers.anthropic import AnthropicProvider

    seen = []

    async def handler(request):
        seen.append(
            {
                "path": request.path,
                "headers": dict(request.headers),
                "body": await request.json(),
            }
        )
        return web.json_response(
            {
                "id": "msg",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "OK"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    # Exercise the real wire, without coupling the test to the SDK's choice
    # of HTTP client implementation (Anthropic 1.x uses httpx2).
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient-must-not-be-used")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    application = web.Application()
    application.router.add_post("/v1/messages", handler)
    async with TestServer(application) as server:
        provider = AnthropicProvider(
            api_key="selected-key",
            api_base=str(server.make_url("/")),
            default_model="claude-sonnet-4-6",
            compat=ProviderCompat(temperature=temperature_override),
        )
        provider.reasoning_supported = False
        provider.input_modalities = ("text",)
        provider.tool_calling = False
        try:
            assert (
                await provider.chat(
                    [{"role": "user", "content": "hello"}], reasoning_effort="high"
                )
            ).content == "OK"
            assert seen[0]["path"] == "/v1/messages"
            headers = {
                name.lower(): value for name, value in seen[0]["headers"].items()
            }
            assert (
                headers["x-api-key"] == "selected-key"
                and "authorization" not in headers
            )
            assert "thinking" not in seen[0]["body"]
            assert ("temperature" in seen[0]["body"]) == (
                temperature_override is not False
            )
            if temperature_override is None:
                assert seen[0]["body"]["temperature"] == 0.7
            response = await provider.chat_with_retry(
                [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,AA=="},
                            }
                        ],
                    }
                ]
            )
            assert response.finish_reason == "error" and len(seen) == 1
            assert response.error_kind == "capability"
            response = await provider.chat(
                [{"role": "user", "content": "hello"}],
                tools=[
                    {
                        "type": "function",
                        "function": {"name": "read", "parameters": {"type": "object"}},
                    }
                ],
            )
            assert response.finish_reason == "error" and len(seen) == 1
        finally:
            await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["openai", "anthropic"])
async def test_echoed_http_credentials_are_removed_before_error_projection(
    monkeypatch, kind
):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from core.providers.anthropic import AnthropicProvider

    key = "private-api-key-for-error-check"
    header = "private-header-for-error-check"

    async def reject(_request):
        return web.json_response(
            {
                "type": "error",
                "error": {
                    "type": "authentication_error",
                    "message": f"Rejected {key} and {header}",
                    "code": key,
                },
            },
            status=401,
        )

    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    app = web.Application()
    app.router.add_post("/{tail:.*}", reject)
    async with TestServer(app) as server:
        options = {
            "api_key": key,
            "api_base": str(server.make_url("/")),
            "extra_headers": {"X-Custom-Auth": header},
            "default_model": "claude-sonnet-4-6",
        }
        provider = (
            AnthropicProvider(**options)
            if kind == "anthropic"
            else OpenAICompatProvider(**options, protocol="openai_chat")
        )
        try:
            result = await provider.chat([{"role": "user", "content": "hello"}])
            assert result.finish_reason == "error" and result.error_status_code == 401
            assert key not in repr(result) and header not in repr(result)
            assert "[redacted]" in result.content
        finally:
            await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["openai_chat", "openai_responses"])
async def test_cancelling_an_active_sdk_stream_releases_the_response(
    monkeypatch, protocol
):
    import asyncio

    waiting = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            if protocol == "openai_chat":
                event = {
                    "id": "c",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": "partial"}}],
                }
                yield f"data: {json.dumps(event)}\n\n".encode()
            else:
                event = {
                    "type": "response.output_text.delta",
                    "delta": "partial",
                    "item_id": "m",
                    "output_index": 0,
                    "content_index": 0,
                    "sequence_number": 1,
                }
                yield f"event: response.output_text.delta\ndata: {json.dumps(event)}\n\n".encode()
            waiting.set()
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    stream = Stream()
    mock_sdk(
        monkeypatch,
        lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        ),
    )
    provider = OpenAICompatProvider(
        api_key="test", api_base="https://gateway.example/v1", protocol=protocol
    )
    task = asyncio.create_task(
        provider.chat_stream_with_retry([{"role": "user", "content": "hello"}])
    )
    try:
        await asyncio.wait_for(waiting.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed
        assert not provider._client.is_closed()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await provider.aclose()
