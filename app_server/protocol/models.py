"""Minimal JSON-RPC 2.0 message models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


RpcId = str | int | None


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: RpcId = None
    has_id: bool = False


@dataclass(frozen=True, slots=True)
class Response:
    id: RpcId
    result: Any | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": self.id}
        if self.error is not None:
            message["error"] = self.error
        else:
            message["result"] = self.result
        return message


def notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}
