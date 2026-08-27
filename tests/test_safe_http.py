from __future__ import annotations

import asyncio
import socket

import pytest

from core.network import safe_http
from core.network.safe_http import (
    PublicResolver,
    ResponseTooLargeError,
    SafeHttpClient,
    SafeHttpPolicy,
    UnsafeUrlError,
    is_domain_allowed,
    is_public_ip,
    validate_public_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:pass@example.com/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "https://example.com:22/",
        "https://example.com\\@127.0.0.1/",
        "https://exa mple.com/",
    ],
)
def test_validate_public_url_rejects_unsafe_targets(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_public_url(url)


def test_validate_public_url_canonicalizes_and_removes_fragment() -> None:
    assert (
        validate_public_url("HTTPS://Example.COM/docs?q=1#secret")
        == "https://Example.COM/docs?q=1"
    )


def test_validate_public_url_rejects_oversized_url() -> None:
    with pytest.raises(UnsafeUrlError, match="length limit"):
        validate_public_url(
            "https://example.com/" + "x" * 100,
            max_url_characters=64,
        )


def test_domain_policy_is_boundary_aware_and_deny_wins() -> None:
    assert is_domain_allowed("docs.example.com", allowed_domains=("example.com",))
    assert not is_domain_allowed(
        "example.com.evil.test", allowed_domains=("example.com",)
    )
    assert not is_domain_allowed(
        "private.example.com",
        allowed_domains=("example.com",),
        blocked_domains=("private.example.com",),
    )


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),
        ("::ffff:127.0.0.1", False),
        ("192.0.2.1", False),
    ],
)
def test_public_ip_classification(address: str, expected: bool) -> None:
    assert is_public_ip(address) is expected


@pytest.mark.asyncio
async def test_public_resolver_rejects_if_any_resolution_is_private(
    monkeypatch,
) -> None:
    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(*args, **kwargs):  # type: ignore[no-untyped-def]
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(OSError, match="non-public"):
        await PublicResolver().resolve("example.com", 443)


def test_cross_origin_redirect_drops_credentials_and_custom_headers() -> None:
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": "secret",
        "X-Custom": "value",
    }
    assert safe_http._headers_after_redirect(
        headers,
        "https://api.example.com/start",
        "https://other.example.com/end",
    ) == {"Accept": "application/json"}
    assert (
        safe_http._headers_after_redirect(
            headers,
            "https://api.example.com/start",
            "https://api.example.com/end",
        )
        == headers
    )


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(self, chunks: list[bytes], headers=None) -> None:
        self.content = _FakeContent(chunks)
        self.headers = headers or {}


class _RedirectResponse:
    status = 302
    headers = {"Location": "/" + "x" * 100}

    def release(self) -> None:
        return None


class _RedirectSession:
    async def request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return _RedirectResponse()


@pytest.mark.asyncio
async def test_response_limit_applies_to_streamed_decoded_bytes() -> None:
    response = _FakeResponse([b"1234", b"5678"])
    with pytest.raises(ResponseTooLargeError):
        await safe_http._read_limited_body(response, 7)


@pytest.mark.asyncio
async def test_client_blocks_loopback_before_opening_a_connection() -> None:
    with pytest.raises(UnsafeUrlError):
        await SafeHttpClient().get("http://127.0.0.1:80/private")


@pytest.mark.asyncio
async def test_client_rejects_oversized_url_before_opening_a_connection() -> None:
    client = SafeHttpClient(SafeHttpPolicy(max_url_characters=64))

    with pytest.raises(UnsafeUrlError, match="length limit"):
        await client.get("https://example.com/" + "x" * 100)


@pytest.mark.asyncio
async def test_client_bounds_url_after_rendering_query_parameters() -> None:
    client = SafeHttpClient(SafeHttpPolicy(max_url_characters=64))

    with pytest.raises(UnsafeUrlError, match="length limit"):
        await client.get(
            "https://example.com/search",
            params={"q": "x" * 100},
        )


@pytest.mark.asyncio
async def test_client_rejects_oversized_redirect_before_following_it() -> None:
    client = SafeHttpClient(SafeHttpPolicy(max_url_characters=64))

    with pytest.raises(UnsafeUrlError, match="length limit"):
        await client._request_with_redirects(
            _RedirectSession(),  # type: ignore[arg-type]
            "https://example.com/start",
            params=None,
            headers={},
            allowed_domains=(),
            blocked_domains=(),
        )
