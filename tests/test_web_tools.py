from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.harness.tools import default_coding_tools
from core.harness.tools.web import MAX_FETCH_CHARACTERS, WebFetchTool
from core.network.safe_http import (
    HttpStatusError,
    NetworkTransportError,
    ResponseTooLargeError,
    SafeHttpResponse,
    UnexpectedContentTypeError,
    UnsafeUrlError,
)


@dataclass
class _FakeClient:
    response: SafeHttpResponse | None = None
    error: Exception | None = None
    requested_url: str | None = None

    async def get(self, url: str) -> SafeHttpResponse:
        self.requested_url = url
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _response(
    body: str,
    *,
    url: str = "https://example.com/docs?token=secret#part",
    content_type: str = "text/html",
) -> SafeHttpResponse:
    return SafeHttpResponse(
        url=url,
        status=200,
        content_type=content_type,
        body=body.encode(),
        charset="utf-8",
    )


def test_web_fetch_is_the_only_default_web_tool(tmp_path) -> None:
    registry = default_coding_tools(tmp_path, skills=())

    assert "web_fetch" in registry.tool_names
    assert "web_search" not in registry.tool_names


def test_web_fetch_accepts_only_one_required_url() -> None:
    tool = WebFetchTool(_FakeClient())

    assert tool.read_only is True
    assert tool.parameters == {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "minLength": 1,
                "description": "Public HTTP or HTTPS URL to read.",
            }
        },
        "required": ["url"],
        "additionalProperties": False,
    }


@pytest.mark.asyncio
async def test_web_fetch_reads_html_as_untrusted_text_and_sanitizes_output() -> None:
    client = _FakeClient(
        response=_response(
            "<html><body><h1>Title</h1><script>secret()</script><p>Body</p></body></html>"
        )
    )
    tool = WebFetchTool(client)

    result = await tool.execute(url="https://example.com/docs?token=secret#part")

    assert client.requested_url == "https://example.com/docs?token=secret#part"
    assert "Source: https://example.com/docs" in result
    assert "untrusted external content" in result
    assert "Title" in result and "Body" in result
    assert "secret()" not in result
    assert "token=secret" not in result


def test_web_fetch_presentation_never_exposes_credentials_query_or_fragment() -> None:
    tool = WebFetchTool(_FakeClient())

    assert (
        tool.presentation_detail(
            {"url": "https://user:password@example.com:8443/path?q=secret#fragment"}
        )
        == "https://example.com:8443/path"
    )


@pytest.mark.asyncio
async def test_web_fetch_truncates_model_visible_content() -> None:
    tool = WebFetchTool(
        _FakeClient(
            response=_response(
                "x" * (MAX_FETCH_CHARACTERS + 10), content_type="text/plain"
            )
        )
    )

    result = await tool.execute(url="https://example.com/large")

    assert result.endswith("[Content truncated.]")
    assert "x" * (MAX_FETCH_CHARACTERS + 1) not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (UnsafeUrlError("unsafe"), "URL is not allowed"),
        (HttpStatusError(404), "Remote server returned HTTP 404"),
        (ResponseTooLargeError("large"), "Web response is too large"),
        (UnexpectedContentTypeError("binary"), "Unsupported Web response type"),
        (NetworkTransportError("failed"), "Could not reach the URL"),
    ],
)
async def test_web_fetch_maps_transport_errors_to_safe_messages(
    error: Exception,
    message: str,
) -> None:
    tool = WebFetchTool(_FakeClient(error=error))

    result = await tool.execute(url="https://example.com")

    assert result.is_error is True
    assert result == f"Error: {message}"


@pytest.mark.asyncio
async def test_web_fetch_rejects_missing_url_without_network_access() -> None:
    client = _FakeClient()

    result = await WebFetchTool(client).execute()

    assert result.is_error is True
    assert client.requested_url is None
