"""Fail-closed network primitives used by DeepCode's URL reader."""

from core.network.safe_http import (
    HttpStatusError,
    PublicResolver,
    ResponseTooLargeError,
    SafeHttpClient,
    SafeHttpError,
    SafeHttpPolicy,
    SafeHttpResponse,
    UnexpectedContentTypeError,
    UnsafeUrlError,
    is_domain_allowed,
    validate_public_url,
)

__all__ = [
    "HttpStatusError",
    "PublicResolver",
    "ResponseTooLargeError",
    "SafeHttpClient",
    "SafeHttpError",
    "SafeHttpPolicy",
    "SafeHttpResponse",
    "UnexpectedContentTypeError",
    "UnsafeUrlError",
    "is_domain_allowed",
    "validate_public_url",
]
