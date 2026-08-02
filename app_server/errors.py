"""JSON-RPC and application error translation."""

from __future__ import annotations

import uuid
from typing import Any

from core.application.errors import ApplicationError


class RpcError(Exception):
    def __init__(
        self,
        numeric_code: int,
        message: str,
        *,
        stable_code: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.numeric_code = numeric_code
        self.stable_code = stable_code
        self.data = data or {}

    def payload(self) -> dict[str, Any]:
        correlation_id = f"corr_{uuid.uuid4().hex}"
        return {
            "code": self.numeric_code,
            "message": str(self),
            "data": {
                "code": self.stable_code,
                "retryable": False,
                "correlationId": correlation_id,
                **self.data,
            },
        }


class ParseError(RpcError):
    def __init__(self, message: str = "parse error") -> None:
        super().__init__(-32700, message, stable_code="INVALID_REQUEST")


class InvalidRequest(RpcError):
    def __init__(self, message: str = "invalid request") -> None:
        super().__init__(-32600, message, stable_code="INVALID_REQUEST")


class MethodNotFound(RpcError):
    def __init__(self, method: str) -> None:
        super().__init__(
            -32601,
            f"method not found: {method}",
            stable_code="METHOD_NOT_FOUND",
        )


class InvalidParams(RpcError):
    def __init__(self, message: str) -> None:
        super().__init__(-32602, message, stable_code="INVALID_REQUEST")


class NotInitialized(RpcError):
    def __init__(self) -> None:
        super().__init__(
            -32002,
            "initialize must be called first",
            stable_code="NOT_INITIALIZED",
        )


class ProtocolMismatch(RpcError):
    def __init__(self, requested: str, supported: str) -> None:
        super().__init__(
            -32003,
            f"unsupported protocol version: {requested}",
            stable_code="PROTOCOL_MISMATCH",
            data={"requestedVersion": requested, "supportedVersion": supported},
        )


def from_application_error(error: ApplicationError) -> RpcError:
    return RpcError(
        -32000,
        error.user_message,
        stable_code=error.code,
        data={
            "retryable": error.retryable,
            "details": error.details,
        },
    )
