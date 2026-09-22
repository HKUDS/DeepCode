"""Tests for the dictation clip pipeline: bytes in, transcript out.

Two layers are covered here and nothing above them:

- :mod:`core.dictation.audio` is pure, so every rejection path is a direct
  assertion on a value rather than on a mock.
- :mod:`core.dictation.client` is exercised through an ``httpx.MockTransport``,
  which is the only way to assert what actually went on the wire (multipart
  shape, filename, headers) without a server.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app_server.protocol.codec import DEFAULT_MAX_MESSAGE_BYTES  # noqa: E402
from core.dictation.audio import (  # noqa: E402
    CONTAINER_EXTENSIONS,
    MAX_AUDIO_BYTES,
    MAX_ENCODED_AUDIO_BYTES,
    MIME_CONTAINERS,
    SUPPORTED_MIME_TYPES,
    UnsupportedAudioError,
    canonical_mime_type,
    decode_audio,
    extension_for,
    sniff_container,
)
from core.dictation.client import SpeechToTextClient, TranscriptionFailed  # noqa: E402

WEBM = b"\x1aE\xdf\xa3" + b"\x00" * 60
OGG = b"OggS" + b"\x00" * 60
MP4 = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 48
WAV = b"RIFF" + b"\x24\x00\x00\x00" + b"WAVEfmt " + b"\x00" * 48
MP3 = b"ID3\x03\x00" + b"\x00" * 58
MP3_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 60
AAC_ADTS = b"\xff\xf1\x50\x80" + b"\x00" * 60
FLAC = b"fLaC" + b"\x00" * 60


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _client(handler, **kwargs) -> SpeechToTextClient:
    return SpeechToTextClient(
        "http://127.0.0.1:8000/v1",
        "mlx-community/parakeet-tdt-0.6b-v3",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# container sniffing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (WEBM, "webm"),
        (OGG, "ogg"),
        (MP4, "mp4"),
        (WAV, "wav"),
        (MP3, "mp3"),
        (MP3_FRAME, "mp3"),
        (AAC_ADTS, "aac"),
        (FLAC, "flac"),
    ],
)
def test_sniff_container_identifies_supported_formats(data, expected):
    assert sniff_container(data) == expected


def test_sniff_container_is_decided_by_fixed_offsets_not_length():
    # A clipped recording must be identified the same way as a complete one.
    assert sniff_container(WEBM[:6]) == "webm"
    assert sniff_container(WAV[:12]) == "wav"


def test_sniff_container_rejects_near_misses_and_noise():
    # RIFF alone is not audio: a WebP or an AVI starts the same way.
    assert sniff_container(b"RIFF\x24\x00\x00\x00WEBP") is None
    assert sniff_container(b"") is None
    assert sniff_container(b"\x00\x01\x02\x03") is None


def test_adts_is_not_reported_as_mp3():
    # 0xFF 0xF1 is inside the MPEG frame-sync mask as well, so order matters.
    assert sniff_container(AAC_ADTS) == "aac"


def test_mp4_extension_is_m4a_for_upload():
    assert extension_for("mp4") == "m4a"


# ---------------------------------------------------------------------------
# decode_audio
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "declared", "filename"),
    [
        (WEBM, "audio/webm", "dictation.webm"),
        (OGG, "audio/ogg", "dictation.ogg"),
        (MP4, "audio/mp4", "dictation.m4a"),
        (WAV, "audio/wav", "dictation.wav"),
        (MP3, "audio/mpeg", "dictation.mp3"),
        (FLAC, "audio/flac", "dictation.flac"),
        (AAC_ADTS, "audio/aac", "dictation.aac"),
    ],
)
def test_decode_audio_names_the_upload_from_the_sniffed_container(
    data, declared, filename
):
    decoded, name = decode_audio(_b64(data), declared)
    assert decoded == data
    assert name == filename


def test_decode_audio_accepts_mime_parameters_and_case():
    decoded, name = decode_audio(_b64(WEBM), "Audio/WebM;codecs=opus")

    assert decoded == WEBM
    assert name == "dictation.webm"
    assert canonical_mime_type("Audio/WebM;codecs=opus") == "audio/webm"


def test_decode_audio_accepts_unpadded_base64():
    padded = _b64(WEBM)
    assert padded.rstrip("=") != padded  # the fixture actually has padding

    decoded, _ = decode_audio(padded.rstrip("="), "audio/webm")

    assert decoded == WEBM


def test_decode_audio_rejects_unknown_mime_type():
    with pytest.raises(UnsupportedAudioError) as excinfo:
        decode_audio(_b64(WEBM), "video/mp4")

    message = str(excinfo.value)
    assert "video/mp4" in message
    assert "audio/webm" in message


def test_decode_audio_rejects_empty_mime_type():
    with pytest.raises(UnsupportedAudioError, match="must not be empty"):
        decode_audio(_b64(WEBM), "  ")


def test_decode_audio_rejects_bytes_that_contradict_the_declared_type():
    # WKWebView records MP4 but a client may still label it webm; the bytes win
    # and the request fails instead of uploading an undecodable file.
    with pytest.raises(UnsupportedAudioError, match="does not match"):
        decode_audio(_b64(MP4), "audio/webm")


def test_decode_audio_rejects_invalid_base64():
    with pytest.raises(UnsupportedAudioError, match="valid base64"):
        decode_audio("not base64!!!", "audio/webm")


def test_decode_audio_rejects_empty_payload():
    with pytest.raises(UnsupportedAudioError, match="must not be empty"):
        decode_audio("", "audio/webm")


def test_decode_audio_rejects_unrecognisable_bytes():
    with pytest.raises(UnsupportedAudioError, match="not a recognisable recording"):
        decode_audio(_b64(b"\x00" * 64), "audio/webm")


def test_decode_audio_rejects_payload_over_the_decoded_ceiling():
    oversized = b"\x1aE\xdf\xa3" + b"\x00" * MAX_AUDIO_BYTES

    with pytest.raises(UnsupportedAudioError, match="larger than"):
        decode_audio(_b64(oversized), "audio/webm")


def test_decode_audio_rejects_payload_over_the_encoded_ceiling():
    # Checked before decoding, so the message can mention the encoded size
    # without ever materialising the bytes.
    with pytest.raises(UnsupportedAudioError, match="once encoded"):
        decode_audio("A" * (MAX_ENCODED_AUDIO_BYTES + 4), "audio/webm")


def test_encoded_ceiling_leaves_room_for_the_rpc_envelope():
    # The clip travels inside one JSON-RPC message, so the encoded ceiling must
    # stay clear of the codec's own limit or the request dies before any of
    # this validation runs.
    assert MAX_ENCODED_AUDIO_BYTES + 4096 < DEFAULT_MAX_MESSAGE_BYTES


def test_every_supported_mime_type_maps_to_a_container_with_an_extension():
    # The declared type is only useful if it can be checked against something
    # and the result can be named on the wire.
    for mime_type, containers in MIME_CONTAINERS.items():
        assert mime_type in SUPPORTED_MIME_TYPES
        assert containers
        for container in containers:
            assert container in CONTAINER_EXTENSIONS


# ---------------------------------------------------------------------------
# SpeechToTextClient
# ---------------------------------------------------------------------------


def _capture(handler):
    """Wrap a handler so the test can inspect the single request made."""

    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return wrapped, seen


def test_client_posts_multipart_with_sniffed_filename_and_model():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "olá mundo"})

    wrapped, seen = _capture(handler)
    text = _client(wrapped, language="pt").transcribe(
        MP4, filename="dictation.m4a", mime_type="audio/mp4"
    )

    assert text == "olá mundo"
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:8000/v1/audio/transcriptions"
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    body = request.read()
    assert b'name="file"; filename="dictation.m4a"' in body
    assert b"Content-Type: audio/mp4" in body
    assert b'name="model"' in body
    assert b"mlx-community/parakeet-tdt-0.6b-v3" in body
    assert b'name="language"' in body
    assert b"\r\npt\r\n" in body


def test_client_omits_language_and_authorization_when_unset():
    wrapped, seen = _capture(lambda request: httpx.Response(200, json={"text": ""}))
    _client(wrapped).transcribe(WEBM, filename="dictation.webm", mime_type="audio/webm")

    body = seen[0].read()
    assert b'name="language"' not in body
    assert "authorization" not in seen[0].headers


def test_client_sends_bearer_token_when_configured():
    wrapped, seen = _capture(lambda request: httpx.Response(200, json={"text": "x"}))
    _client(wrapped, api_key="s3cret").transcribe(
        WEBM, filename="dictation.webm", mime_type="audio/webm"
    )

    assert seen[0].headers["authorization"] == "Bearer s3cret"


def test_client_does_not_follow_a_redirect():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(307, headers={"Location": "http://evil.example/v1"})

    with pytest.raises(TranscriptionFailed) as excinfo:
        _client(handler, api_key="s3cret").transcribe(
            WEBM, filename="dictation.webm", mime_type="audio/webm"
        )

    # One request only: neither the audio nor the token may be re-sent to a
    # destination chosen by the response.
    assert len(calls) == 1
    assert excinfo.value.status_code == 307
    assert excinfo.value.retryable is False


def test_client_reports_status_without_echoing_the_response_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="SECRET-STACKTRACE api_key=abc")

    with pytest.raises(TranscriptionFailed) as excinfo:
        _client(handler).transcribe(
            WEBM, filename="dictation.webm", mime_type="audio/webm"
        )

    message = str(excinfo.value)
    assert "HTTP 500" in message
    assert "SECRET-STACKTRACE" not in message
    assert "api_key" not in message
    assert excinfo.value.retryable is True


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (404, False), (413, False), (429, True), (503, True)],
)
def test_client_classifies_retryability_by_status(status, retryable):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="nope")

    with pytest.raises(TranscriptionFailed) as excinfo:
        _client(handler).transcribe(
            WEBM, filename="dictation.webm", mime_type="audio/webm"
        )

    assert excinfo.value.status_code == status
    assert excinfo.value.retryable is retryable


def test_client_reports_timeout_as_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(TranscriptionFailed) as excinfo:
        _client(handler, timeout_seconds=1).transcribe(
            WEBM, filename="dictation.webm", mime_type="audio/webm"
        )

    assert excinfo.value.retryable is True
    assert "did not answer within 1s" in str(excinfo.value)


def test_client_reports_unreachable_endpoint_as_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(TranscriptionFailed) as excinfo:
        _client(handler).transcribe(
            WEBM, filename="dictation.webm", mime_type="audio/webm"
        )

    assert excinfo.value.retryable is True
    assert "unreachable" in str(excinfo.value)


@pytest.mark.parametrize(
    ("response", "fragment"),
    [
        (httpx.Response(200, text="<html>not json</html>"), "non-JSON body"),
        (httpx.Response(200, json={"segments": []}), "without a 'text' field"),
        (httpx.Response(200, json={"text": 42}), "without a 'text' field"),
    ],
)
def test_client_rejects_a_200_that_is_not_a_transcript(response, fragment):
    with pytest.raises(TranscriptionFailed) as excinfo:
        _client(lambda request: response).transcribe(
            WEBM, filename="dictation.webm", mime_type="audio/webm"
        )

    # A 200 that is not a transcript means the URL is not an ASR endpoint;
    # retrying the same URL cannot help.
    assert excinfo.value.retryable is False
    assert fragment in str(excinfo.value)


def test_client_returns_empty_text_for_silence():
    # Silence is not a failure: the caller decides to ignore an empty
    # transcript, and MacOS/Chromium happily produce one.
    wrapped, _ = _capture(lambda request: httpx.Response(200, json={"text": ""}))

    assert (
        _client(wrapped).transcribe(
            WEBM, filename="dictation.webm", mime_type="audio/webm"
        )
        == ""
    )


def test_client_never_puts_the_api_key_in_the_url_or_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid key"})

    client = _client(handler, api_key="s3cret")
    assert "s3cret" not in client.url

    with pytest.raises(TranscriptionFailed) as excinfo:
        client.transcribe(WEBM, filename="dictation.webm", mime_type="audio/webm")

    assert "s3cret" not in str(excinfo.value)


def test_json_error_body_is_not_parsed_into_the_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, content=json.dumps({"detail": "LEAK"}).encode())

    with pytest.raises(TranscriptionFailed) as excinfo:
        _client(handler).transcribe(
            WEBM, filename="dictation.webm", mime_type="audio/webm"
        )

    assert "LEAK" not in str(excinfo.value)
