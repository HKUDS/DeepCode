"""Fail-closed HTTP transport for untrusted web URLs.

This module owns network safety; search adapters and agent tools do not each
invent their own URL checks.  Connections resolve through ``PublicResolver``
so a hostname cannot pass validation and then DNS-rebind to a private address.
Every redirect is validated again before it is followed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import aiohttp

from core.network.hostnames import is_domain_allowed, normalize_domain


class SafeHttpError(RuntimeError):
    """Base error safe to return as tool data."""


class UnsafeUrlError(SafeHttpError):
    """The requested URL violates the network policy."""


class HttpStatusError(SafeHttpError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"remote server returned HTTP {status}")


class ResponseTooLargeError(SafeHttpError):
    """The decoded response exceeded the configured byte boundary."""


class UnexpectedContentTypeError(SafeHttpError):
    """The response media type is not accepted by this caller."""


class NetworkTransportError(SafeHttpError):
    """The connection failed without exposing request credentials."""


DEFAULT_MAX_URL_CHARACTERS = 8_192


@dataclass(frozen=True, slots=True)
class SafeHttpPolicy:
    """Central, configurable limits for outbound web requests."""

    total_timeout_seconds: float = 30.0
    connect_timeout_seconds: float = 10.0
    max_redirects: int = 5
    max_response_bytes: int = 1024 * 1024
    max_url_characters: int = DEFAULT_MAX_URL_CHARACTERS
    allowed_ports: frozenset[int] = field(default_factory=lambda: frozenset({80, 443}))
    accepted_content_types: tuple[str, ...] = (
        "text/",
        "application/json",
        "application/xhtml+xml",
    )
    allow_https_downgrade: bool = False

    def __post_init__(self) -> None:
        if self.total_timeout_seconds <= 0 or self.connect_timeout_seconds <= 0:
            raise ValueError("HTTP timeouts must be positive")
        if self.max_redirects < 0:
            raise ValueError("max_redirects must not be negative")
        if self.max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        if (
            isinstance(self.max_url_characters, bool)
            or not isinstance(self.max_url_characters, int)
            or self.max_url_characters < 1
        ):
            raise ValueError("max_url_characters must be a positive integer")
        if not self.allowed_ports or any(
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
            for port in self.allowed_ports
        ):
            raise ValueError("allowed_ports must contain valid TCP ports")
        if not self.accepted_content_types:
            raise ValueError("accepted_content_types must not be empty")


@dataclass(frozen=True, slots=True)
class SafeHttpResponse:
    url: str
    status: int
    content_type: str
    body: bytes
    charset: str | None = None

    def text(self) -> str:
        encoding = self.charset or "utf-8"
        try:
            return self.body.decode(encoding, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.text())
        except (TypeError, ValueError) as exc:
            raise SafeHttpError("remote server returned invalid JSON") from exc


def _as_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


def is_public_ip(value: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    address = _as_ip(value) if isinstance(value, str) else value
    if address is None:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global


def validate_public_url(
    url: str,
    *,
    allowed_ports: frozenset[int] = frozenset({80, 443}),
    allowed_domains: tuple[str, ...] = (),
    blocked_domains: tuple[str, ...] = (),
    max_url_characters: int = DEFAULT_MAX_URL_CHARACTERS,
) -> str:
    """Validate URL syntax and policy before DNS or socket activity."""

    if not isinstance(url, str) or not url.strip():
        raise UnsafeUrlError("URL must be a non-empty string")
    if (
        isinstance(max_url_characters, bool)
        or not isinstance(max_url_characters, int)
        or max_url_characters < 1
    ):
        raise ValueError("max_url_characters must be a positive integer")
    value = url.strip()
    if len(value) > max_url_characters:
        raise UnsafeUrlError("URL exceeds the configured length limit")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise UnsafeUrlError("URL must not contain whitespace or control characters")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError("URL contains an invalid port or host") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUrlError("only HTTP and HTTPS URLs are supported")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("URL credentials are not allowed")
    hostname = parsed.hostname.rstrip(".")
    literal = _as_ip(hostname)
    if literal is not None:
        if not is_public_ip(literal):
            raise UnsafeUrlError("URL host must use a public address")
        canonical_host = str(literal)
    else:
        try:
            canonical_host = normalize_domain(hostname)
        except (TypeError, ValueError) as exc:
            raise UnsafeUrlError("URL hostname is invalid") from exc
    effective_port = port or (443 if scheme == "https" else 80)
    if effective_port not in allowed_ports:
        raise UnsafeUrlError("URL port is not allowed by network policy")
    if not is_domain_allowed(
        canonical_host,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
    ):
        raise UnsafeUrlError("URL hostname is not allowed by network policy")
    # Fragments never reach the server and do not belong in canonical records.
    return urlunsplit((scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Resolve only globally routable addresses, preventing DNS rebinding."""

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_UNSPEC,
    ) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        try:
            records = await loop.getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
                family=family,
            )
        except OSError as exc:
            raise OSError("URL hostname could not be resolved") from exc
        resolved: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for resolved_family, _, protocol, _, address in records:
            ip = address[0]
            resolved_port = address[1]
            if not is_public_ip(ip):
                raise OSError("URL resolves to a non-public address")
            key = (ip, resolved_port)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(
                {
                    "hostname": host,
                    "host": ip,
                    "port": resolved_port,
                    "family": resolved_family,
                    "proto": protocol,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        if not resolved:
            raise OSError("URL hostname did not resolve")
        return resolved

    async def close(self) -> None:
        return None


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CROSS_ORIGIN_HEADERS = frozenset({"accept", "accept-language", "user-agent"})
_FORBIDDEN_REQUEST_HEADERS = frozenset(
    {"host", "cookie", "proxy-authorization", "proxy-connection"}
)


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    return (
        scheme,
        parsed.hostname.lower() if parsed.hostname else "",
        parsed.port or (443 if scheme == "https" else 80),
    )


def _url_with_params(
    url: str,
    params: Mapping[str, str | int] | None,
) -> str:
    """Render first-request parameters before enforcing the URL boundary."""

    if not params:
        return url
    parsed = urlsplit(url)
    try:
        encoded = urlencode(tuple(params.items()))
    except (TypeError, ValueError) as exc:
        raise SafeHttpError("request query parameters are invalid") from exc
    query = "&".join(part for part in (parsed.query, encoded) if part)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def _validate_request_headers(headers: Mapping[str, str]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name).strip()
        value = str(raw_value).strip()
        if not name or "\r" in name or "\n" in name:
            raise SafeHttpError("request header name is invalid")
        if "\r" in value or "\n" in value:
            raise SafeHttpError("request header value is invalid")
        if name.lower() in _FORBIDDEN_REQUEST_HEADERS:
            raise SafeHttpError(f"request header {name!r} is not allowed")
        clean[name] = value
    return clean


def _headers_after_redirect(
    headers: Mapping[str, str], previous_url: str, next_url: str
) -> dict[str, str]:
    if _origin(previous_url) == _origin(next_url):
        return dict(headers)
    # Treat every caller-supplied header as sensitive unless explicitly benign.
    return {
        name: value
        for name, value in headers.items()
        if name.lower() in _CROSS_ORIGIN_HEADERS
    }


def _content_type_allowed(content_type: str, accepted: tuple[str, ...]) -> bool:
    lowered = content_type.lower()
    return any(
        lowered.startswith(rule.lower())
        if rule.endswith("/")
        else lowered == rule.lower()
        for rule in accepted
    )


async def _read_limited_body(response: Any, max_bytes: int) -> bytes:
    raw_length = response.headers.get("Content-Length")
    if raw_length is not None:
        try:
            if int(raw_length) > max_bytes:
                raise ResponseTooLargeError(
                    "response exceeds the configured byte limit"
                )
        except ValueError:
            pass
    body = bytearray()
    async for chunk in response.content.iter_chunked(min(64 * 1024, max_bytes + 1)):
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ResponseTooLargeError("response exceeds the configured byte limit")
    return bytes(body)


class SafeHttpClient:
    """Small GET-only client with invariant SSRF and response protections."""

    def __init__(self, policy: SafeHttpPolicy | None = None) -> None:
        self.policy = policy or SafeHttpPolicy()

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        accepted_content_types: tuple[str, ...] | None = None,
        allowed_domains: tuple[str, ...] = (),
        blocked_domains: tuple[str, ...] = (),
    ) -> SafeHttpResponse:
        policy = self.policy
        current_url = validate_public_url(
            url,
            allowed_ports=policy.allowed_ports,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
            max_url_characters=policy.max_url_characters,
        )
        current_url = validate_public_url(
            _url_with_params(current_url, params),
            allowed_ports=policy.allowed_ports,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
            max_url_characters=policy.max_url_characters,
        )
        current_headers = _validate_request_headers(headers or {})
        accepted = accepted_content_types or policy.accepted_content_types
        timeout = aiohttp.ClientTimeout(
            total=policy.total_timeout_seconds,
            connect=policy.connect_timeout_seconds,
        )
        connector = aiohttp.TCPConnector(
            resolver=PublicResolver(),
            use_dns_cache=False,
            ttl_dns_cache=0,
        )
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
                auto_decompress=True,
            ) as session:
                response = await self._request_with_redirects(
                    session,
                    current_url,
                    params=None,
                    headers=current_headers,
                    allowed_domains=allowed_domains,
                    blocked_domains=blocked_domains,
                )
                try:
                    if not 200 <= response.status < 300:
                        raise HttpStatusError(response.status)
                    content_type = response.content_type.lower()
                    if not _content_type_allowed(content_type, accepted):
                        raise UnexpectedContentTypeError(
                            f"response content type {content_type!r} is not accepted"
                        )
                    body = await _read_limited_body(response, policy.max_response_bytes)
                    return SafeHttpResponse(
                        url=str(response.url),
                        status=response.status,
                        content_type=content_type,
                        body=body,
                        charset=response.charset,
                    )
                finally:
                    response.release()
        except SafeHttpError:
            raise
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            raise NetworkTransportError("web request failed") from exc

    async def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        allowed_domains: tuple[str, ...] = (),
        blocked_domains: tuple[str, ...] = (),
    ) -> Any:
        response = await self.get(
            url,
            params=params,
            headers=headers,
            accepted_content_types=("application/json",),
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
        )
        return response.json()

    async def _request_with_redirects(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        params: Mapping[str, str | int] | None,
        headers: Mapping[str, str],
        allowed_domains: tuple[str, ...],
        blocked_domains: tuple[str, ...],
    ) -> aiohttp.ClientResponse:
        current_url = url
        current_headers = dict(headers)
        current_params = params
        for redirect_count in range(self.policy.max_redirects + 1):
            response = await session.request(
                "GET",
                current_url,
                params=current_params,
                headers=current_headers,
                allow_redirects=False,
            )
            current_params = None
            if response.status not in _REDIRECT_STATUSES:
                return response
            location = response.headers.get("Location")
            response.release()
            if not location:
                raise SafeHttpError("redirect response has no Location header")
            if redirect_count == self.policy.max_redirects:
                raise SafeHttpError("URL exceeded the redirect limit")
            next_url = validate_public_url(
                urljoin(current_url, location),
                allowed_ports=self.policy.allowed_ports,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains,
                max_url_characters=self.policy.max_url_characters,
            )
            if (
                not self.policy.allow_https_downgrade
                and urlsplit(current_url).scheme.lower() == "https"
                and urlsplit(next_url).scheme.lower() == "http"
            ):
                raise UnsafeUrlError("HTTPS redirects may not downgrade to HTTP")
            current_headers = _headers_after_redirect(
                current_headers, current_url, next_url
            )
            current_url = next_url
        raise AssertionError("unreachable redirect loop")


__all__ = [
    "DEFAULT_MAX_URL_CHARACTERS",
    "HttpStatusError",
    "NetworkTransportError",
    "PublicResolver",
    "ResponseTooLargeError",
    "SafeHttpClient",
    "SafeHttpError",
    "SafeHttpPolicy",
    "SafeHttpResponse",
    "UnexpectedContentTypeError",
    "UnsafeUrlError",
    "is_domain_allowed",
    "is_public_ip",
    "validate_public_url",
]
