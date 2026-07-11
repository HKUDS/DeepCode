"""Tests for WebSearchTool (Metaso API integration)."""

import json
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from nanobot.agent.tools.web import WebSearchTool, _get_metaso_api_key


def _mock_response(
    status_code: int = 200, json_data: dict | None = None, text: str = ""
):
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text or json.dumps(json_data or {})
    if json_data is not None:
        resp.json.return_value = json_data
    resp.raise_for_status = Mock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=AsyncMock(), response=resp
        )
    return resp


@pytest.fixture
def tool():
    return WebSearchTool()


@pytest.fixture
def sample_response():
    return {
        "code": 0,
        "total": 2,
        "webpages": [
            {
                "title": "Python Async",
                "link": "https://example.com/async",
                "snippet": "Learn async",
            },
            {
                "title": "AsyncIO Docs",
                "link": "https://docs.example.com",
                "summary": "Official docs",
            },
        ],
    }


# --- Schema & configuration ---


def test_tool_name(tool):
    assert tool.name == "web_search"


def test_schema_requires_query(tool):
    errors = tool.validate_params({})
    assert any("query" in e for e in errors)


def test_schema_validates_topk_range(tool):
    errors = tool.validate_params({"query": "test", "topK": 0})
    assert any("topK" in e for e in errors)
    errors = tool.validate_params({"query": "test", "topK": 101})
    assert any("topK" in e for e in errors)


def test_schema_accepts_valid_params(tool):
    assert tool.validate_params({"query": "test"}) == []
    assert tool.validate_params({"query": "test", "topK": 5}) == []


def test_to_schema_structure(tool):
    schema = tool.to_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "web_search"
    assert "query" in schema["function"]["parameters"]["properties"]


# --- API key resolution ---


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("METASO_API_KEY", "test-key-123")
    assert _get_metaso_api_key() == "test-key-123"


def test_api_key_default(monkeypatch):
    monkeypatch.delenv("METASO_API_KEY", raising=False)
    assert _get_metaso_api_key() == "mk-E384C1DD5E8501BB7EFE27C949AFDE5B"


# --- Successful search ---


@pytest.mark.asyncio
async def test_search_success(tool, sample_response):
    mock_resp = _mock_response(json_data=sample_response)

    with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await tool.execute(query="Python async")
        data = json.loads(result)

    assert data["query"] == "Python async"
    assert data["total"] == 2
    assert len(data["results"]) == 2
    assert data["results"][0]["title"] == "Python Async"
    assert data["results"][0]["url"] == "https://example.com/async"
    assert data["results"][0]["snippet"] == "Learn async"
    assert data["results"][1]["snippet"] == "Official docs"  # falls back to summary


@pytest.mark.asyncio
async def test_search_sends_correct_request(tool, sample_response):
    mock_resp = _mock_response(json_data=sample_response)

    with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        await tool.execute(query="test query", topK=5)

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["json"] == {
            "q": "test query",
            "scope": "webpage",
            "size": 5,
        }
        assert "Authorization" in call_kwargs.kwargs["headers"]
        assert call_kwargs.kwargs["headers"]["Authorization"].startswith("Bearer ")


@pytest.mark.asyncio
async def test_search_empty_results(tool):
    mock_resp = _mock_response(json_data={"code": 0, "total": 0, "webpages": []})

    with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await tool.execute(query="obscure query xyz")
        data = json.loads(result)

    assert data["total"] == 0
    assert data["results"] == []


# --- topK clamping ---


@pytest.mark.asyncio
async def test_topk_clamped_to_max(tool):
    mock_resp = _mock_response(json_data={"code": 0, "webpages": []})

    with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        await tool.execute(query="test", topK=999)
        body = mock_client.post.call_args.kwargs["json"]
        assert body["size"] == 100


@pytest.mark.asyncio
async def test_topk_defaults_to_10(tool):
    mock_resp = _mock_response(json_data={"code": 0, "webpages": []})

    with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        await tool.execute(query="test")
        body = mock_client.post.call_args.kwargs["json"]
        assert body["size"] == 10


# --- HTTP error handling ---


@pytest.mark.asyncio
async def test_unauthorized_401(tool):
    mock_resp = _mock_response(status_code=401)

    with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await tool.execute(query="test")
        data = json.loads(result)

    assert "unauthorized" in data["error"].lower()
    assert data["query"] == "test"


@pytest.mark.asyncio
async def test_rate_limited_429(tool):
    mock_resp = _mock_response(status_code=429)

    with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await tool.execute(query="test")
        data = json.loads(result)

    assert "rate limited" in data["error"].lower()


# --- Metaso application-level errors ---


@pytest.mark.asyncio
async def test_daily_limit_code_3003(tool):
    mock_resp = _mock_response(json_data={"code": 3003, "message": "daily limit"})

    with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await tool.execute(query="test")
        data = json.loads(result)

    assert "daily" in data["error"].lower()


@pytest.mark.asyncio
async def test_unauthorized_code_2005(tool):
    mock_resp = _mock_response(json_data={"code": 2005, "message": "unauthorized"})

    with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await tool.execute(query="test")
        data = json.loads(result)

    assert "2005" in data["error"]
    assert "unauthorized" in data["error"].lower()


@pytest.mark.asyncio
async def test_generic_api_error(tool):
    mock_resp = _mock_response(json_data={"code": 9999, "message": "something broke"})

    with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await tool.execute(query="test")
        data = json.loads(result)

    assert "9999" in data["error"]
    assert "something broke" in data["error"]


# --- Network errors ---


@pytest.mark.asyncio
async def test_connection_error(tool):
    with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client_cls.return_value = mock_client

        result = await tool.execute(query="test")
        data = json.loads(result)

    assert "Cannot connect" in data["error"]


@pytest.mark.asyncio
async def test_http_status_error(tool):
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = 500
    resp.text = "Internal Server Error"

    with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "error", request=AsyncMock(), response=resp
            )
        )
        mock_client_cls.return_value = mock_client

        result = await tool.execute(query="test")
        data = json.loads(result)

    assert "500" in data["error"]


# --- Snippet fallback ---


@pytest.mark.asyncio
async def test_snippet_falls_back_to_summary(tool):
    mock_resp = _mock_response(
        json_data={
            "code": 0,
            "webpages": [
                {
                    "title": "No Snippet",
                    "link": "https://example.com",
                    "summary": "Fallback text",
                },
            ],
        }
    )

    with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await tool.execute(query="test")
        data = json.loads(result)

    assert data["results"][0]["snippet"] == "Fallback text"


@pytest.mark.asyncio
async def test_snippet_empty_when_both_missing(tool):
    mock_resp = _mock_response(
        json_data={
            "code": 0,
            "webpages": [
                {"title": "No Text", "link": "https://example.com"},
            ],
        }
    )

    with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await tool.execute(query="test")
        data = json.loads(result)

    assert data["results"][0]["snippet"] == ""
