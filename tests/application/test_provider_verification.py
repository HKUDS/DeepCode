from __future__ import annotations

import json

import httpx
import pytest
from openai import AsyncOpenAI

from core.application.config_store import ConfigStore
from core.application.llm_configuration_service import LLMConfigurationService
from core.providers.credentials import CredentialStore


def sse(event: dict, *, named=False) -> str:
    return (
        f"event: {event['type']}\n" if named else ""
    ) + f"data: {json.dumps(event)}\n\n"


@pytest.mark.parametrize("protocol", ["openai_chat", "openai_responses"])
def test_unsaved_protocol_probe_completes_real_sdk_tool_round_trip(
    tmp_path, monkeypatch, protocol
):
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path))
    service = LLMConfigurationService(
        config_store=ConfigStore(tmp_path / "config.json"),
        credential_store=CredentialStore(tmp_path / "credentials.json"),
    )
    service.upsert(
        {
            "id": "route",
            "template": "custom",
            "apiBase": "https://saved.example/v1",
            "apiKey": "saved-private-key",
            "modelCatalog": "manual",
            "manualModels": ["model"],
        }
    )
    before = {
        path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()
    }
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.url.host == "draft.example"
        assert request.headers["authorization"] == "Bearer draft-private-key"
        assert body["stream"] is True
        if protocol == "openai_chat":
            assert request.url.path == "/v1/chat/completions"
            if len(calls) == 1:
                delta = {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call",
                            "type": "function",
                            "function": {
                                "name": "deepcode_probe",
                                "arguments": '{"value":7}',
                            },
                        }
                    ]
                }
                finish = "tool_calls"
            else:
                nonce = json.loads(body["messages"][-1]["content"])["nonce"]
                delta = {"content": nonce}
                finish = "stop"
            events = [
                {
                    "id": "c",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": delta}],
                },
                {
                    "id": "c",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                },
            ]
            stream = "".join(sse(event) for event in events) + "data: [DONE]\n\n"
        else:
            assert request.url.path == "/v1/responses"
            if len(calls) == 1:
                events = [
                    {
                        "type": "response.output_item.done",
                        "output_index": 0,
                        "sequence_number": 0,
                        "item": {
                            "type": "function_call",
                            "id": "fc",
                            "call_id": "call",
                            "name": "deepcode_probe",
                            "arguments": '{"value":7}',
                            "status": "completed",
                        },
                    }
                ]
            else:
                result = next(
                    item
                    for item in body["input"]
                    if item.get("type") == "function_call_output"
                )
                nonce = json.loads(result["output"])["nonce"]
                events = [
                    {
                        "type": "response.output_text.delta",
                        "item_id": "msg",
                        "output_index": 0,
                        "content_index": 0,
                        "sequence_number": 0,
                        "delta": nonce,
                    }
                ]
            events.append(
                {
                    "type": "response.completed",
                    "sequence_number": 1,
                    "response": {
                        "id": "resp",
                        "object": "response",
                        "status": "completed",
                        "output": [],
                    },
                }
            )
            stream = "".join(sse(event, named=True) for event in events)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=stream
        )

    monkeypatch.setattr(
        "core.providers.openai_compat.AsyncOpenAI",
        lambda **kw: AsyncOpenAI(
            **kw, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ),
    )
    result = service.test(
        "route",
        model_id="model",
        mode="agent",
        draft={
            "id": "route",
            "apiBase": "https://draft.example/v1",
            "apiKey": "draft-private-key",
            "protocol": protocol,
            "manualModels": [
                {
                    "id": "model",
                    "toolCalling": True,
                    "inputModalities": ["text"],
                    "reasoningEfforts": False,
                }
            ],
        },
    )
    assert result["ok"], json.dumps(result, indent=2)
    stages = {stage["id"]: stage["status"] for stage in result["stages"]}
    assert stages["tool"] == stages["continuation"] == stages["stream"] == "passed"
    assert stages["reasoning"] == stages["image"] == "skipped"
    assert len(calls) == 2
    assert {
        path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()
    } == before
    assert not (tmp_path / "provider_revisions").exists()
    assert "private-key" not in json.dumps(result)


def test_discovery_uses_unsaved_protocol_and_none_auth_without_saving(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path))
    service = LLMConfigurationService(
        config_store=ConfigStore(tmp_path / "config.json"),
        credential_store=CredentialStore(tmp_path / "credentials.json"),
    )
    seen = []
    monkeypatch.setattr(
        service.catalog, "probe", lambda connection: seen.append(connection) or ()
    )
    result = service.discover_models(
        draft={
            "id": "custom-new",
            "template": "custom",
            "protocol": "openai_chat",
            "auth": "none",
            "apiBase": "http://127.0.0.1:1234/v1",
        }
    )
    assert result == {"models": [], "error": None}
    assert seen[0].protocol == "openai_chat" and seen[0].api_key is None
    assert seen[0].auth == "none"
    assert not (tmp_path / "config.json").exists()
    assert not (tmp_path / "credentials.json").exists()


def test_image_probe_uses_a_valid_png_and_closes_its_provider():
    import asyncio
    import base64
    import struct
    import zlib
    from core.application.provider_verification import verify_agent
    from core.domain.execution_profile import ExecutionProfile
    from core.providers.base import LLMResponse

    class Provider:
        calls = []
        closed = False

        async def chat_stream(self, **kwargs):
            self.calls.append(kwargs)
            await kwargs["on_content_delta"]("OK")
            return LLMResponse(content="OK")

        async def aclose(self):
            self.closed = True

    provider = Provider()
    profile = ExecutionProfile(
        connection_id="image",
        provider_name="custom",
        adapter="openai_compat",
        model_id="image",
        context_window=32000,
        max_output_tokens=4096,
        max_tokens=4096,
        temperature=0,
        reasoning_effort=None,
        config_revision="probe",
        input_modalities=("text", "image"),
        tool_calling=False,
    )
    stages = asyncio.run(verify_agent(provider, profile))
    assert provider.closed and len(provider.calls) == 2
    assert (
        next(stage for stage in stages if stage["id"] == "image")["status"] == "passed"
    )
    url = provider.calls[-1]["messages"][0]["content"][1]["image_url"]["url"]
    raw = base64.b64decode(url.split(",", 1)[1])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    offset = 8
    while offset < len(raw):
        size = struct.unpack("!I", raw[offset : offset + 4])[0]
        payload = raw[offset + 4 : offset + 8 + size]
        expected = struct.unpack("!I", raw[offset + 8 + size : offset + 12 + size])[0]
        assert zlib.crc32(payload) == expected
        offset += size + 12
    assert offset == len(raw)
