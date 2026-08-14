import json

import pytest

from app_server.errors import InvalidRequest, ParseError
from app_server.protocol.codec import decode_request, encode_message


def test_codec_round_trip_preserves_unicode() -> None:
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "project/add",
        "params": {"path": "/tmp/研究"},
    }
    request = decode_request(encode_message(message))
    assert request.id == 1
    assert request.params == {"path": "/tmp/研究"}


@pytest.mark.parametrize(
    "payload, error",
    [
        (b"not-json\n", ParseError),
        (json.dumps({"jsonrpc": "1.0", "method": "x"}).encode(), InvalidRequest),
        (
            json.dumps({"jsonrpc": "2.0", "method": "x", "params": []}).encode(),
            InvalidRequest,
        ),
        (
            json.dumps({"jsonrpc": "2.0", "method": "x", "id": True}).encode(),
            InvalidRequest,
        ),
    ],
)
def test_codec_rejects_invalid_envelopes(
    payload: bytes, error: type[Exception]
) -> None:
    with pytest.raises(error):
        decode_request(payload)


def test_codec_enforces_message_size() -> None:
    with pytest.raises(InvalidRequest, match="size limit"):
        decode_request(b"{}\n", max_bytes=2)
