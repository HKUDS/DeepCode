"""Newline-framed JSON-RPC codec with strict message limits."""

from __future__ import annotations

import json
from typing import Any

from app_server.errors import InvalidRequest, ParseError
from app_server.protocol.models import Request

DEFAULT_MAX_MESSAGE_BYTES = 1024 * 1024


def decode_request(
    raw: bytes, *, max_bytes: int = DEFAULT_MAX_MESSAGE_BYTES
) -> Request:
    if len(raw) > max_bytes:
        raise InvalidRequest("message exceeds the configured size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseError("message is not valid UTF-8") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError("invalid JSON") from exc
    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
        raise InvalidRequest("expected a JSON-RPC 2.0 object")
    method = value.get("method")
    if not isinstance(method, str) or not method:
        raise InvalidRequest("method must be a non-empty string")
    params = value.get("params", {})
    if not isinstance(params, dict):
        raise InvalidRequest("params must be an object")
    has_id = "id" in value
    request_id = value.get("id")
    if has_id and (
        isinstance(request_id, bool)
        or not isinstance(request_id, (str, int, type(None)))
    ):
        raise InvalidRequest("id must be a string, integer, or null")
    return Request(method=method, params=params, id=request_id, has_id=has_id)


def encode_message(message: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
