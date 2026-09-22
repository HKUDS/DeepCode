"""Byte handling for dictation clips: decode, identify, and name the upload.

A client records with ``MediaRecorder`` and sends what it got as base64 inside a
JSON-RPC message, so three things must be established before the bytes are
worth forwarding:

1. the payload is really base64 and really small enough to be a dictation clip;
2. the container inside it is one the endpoint can decode — the declared MIME
   type is a *claim* from the browser, and browsers disagree (Safari records
   ``audio/mp4``, Chromium ``audio/webm``);
3. the uploaded filename matches the bytes, because OpenAI-compatible servers
   sniff the extension and some of them refuse a mismatch.

Everything here is pure: same input, same decision, no I/O and no environment.
"""

from __future__ import annotations

import base64
import binascii

#: Decoded ceiling for one clip. 512 KiB is ~2 minutes of Opus at the bitrate
#: ``MediaRecorder`` uses by default and ~40 seconds of 16-bit 22 kHz PCM, which
#: covers the ``maxAudioSeconds`` default with room to spare.
MAX_AUDIO_BYTES = 512 * 1024

#: Encoded ceiling. Base64 inflates by 4/3, and the transport caps a whole
#: JSON-RPC message at 1 MiB (``app_server/protocol/codec.py``), so the encoded
#: form may not grow past 768 KiB or the request would die in the codec before
#: any of this ran.
MAX_ENCODED_AUDIO_BYTES = 768 * 1024

#: Containers each declared MIME type is allowed to actually contain. The MIME
#: type is trusted for nothing more than selecting this set; the bytes decide
#: the extension.
MIME_CONTAINERS: dict[str, frozenset[str]] = {
    "audio/webm": frozenset({"webm"}),
    "audio/ogg": frozenset({"ogg"}),
    "audio/mp4": frozenset({"mp4"}),
    "audio/m4a": frozenset({"mp4"}),
    "audio/x-m4a": frozenset({"mp4"}),
    "audio/mpeg": frozenset({"mp3"}),
    "audio/mp3": frozenset({"mp3"}),
    "audio/wav": frozenset({"wav"}),
    "audio/wave": frozenset({"wav"}),
    "audio/x-wav": frozenset({"wav"}),
    "audio/flac": frozenset({"flac"}),
    "audio/x-flac": frozenset({"flac"}),
    "audio/aac": frozenset({"aac"}),
}

#: MIME types the endpoint may be asked to decode, derived so the two cannot
#: drift apart.
SUPPORTED_MIME_TYPES = frozenset(MIME_CONTAINERS)

#: Filename extension per *sniffed* container. ``mp4`` maps to ``m4a`` because
#: that is what a server-side extension check expects for MP4 audio.
CONTAINER_EXTENSIONS: dict[str, str] = {
    "webm": "webm",
    "ogg": "ogg",
    "mp4": "m4a",
    "mp3": "mp3",
    "wav": "wav",
    "flac": "flac",
    "aac": "aac",
}

#: Synchsafe magic numbers. Every one of them is checked at a fixed offset, so
#: the result does not depend on how much of the file was sent.
_MAGIC: tuple[tuple[bytes, int, str], ...] = (
    (b"RIFF", 0, "wav"),
    (b"OggS", 0, "ogg"),
    (b"fLaC", 0, "flac"),
    (b"ID3", 0, "mp3"),
    (b"\x1aE\xdf\xa3", 0, "webm"),
    (b"ftyp", 4, "mp4"),
)

#: WAV needs a second magic at offset 8 (``RIFF....WAVE``).
_WAVE_TAG = (b"WAVE", 8)


class UnsupportedAudioError(ValueError):
    """The clip cannot be forwarded as it arrived.

    Raised for every client-side problem — bad base64, too large, an unknown
    MIME type, or bytes that are not the container they claim to be. The
    service turns it into an ``INVALID_REQUEST`` application error, and the
    message is written to be shown to the user as-is.
    """


def sniff_container(data: bytes) -> str | None:
    """Return the container the bytes actually look like, or ``None``.

    Deterministic: only fixed offsets are inspected, so a truncated clip is
    identified the same way as a complete one. ``None`` means the bytes match
    no known container — including the near-miss of a ``RIFF`` header that is
    not ``WAVE`` — and the caller rejects it.
    """

    for magic, offset, container in _MAGIC:
        if data[offset : offset + len(magic)] == magic:
            if (
                container != "wav"
                or data[_WAVE_TAG[1] : _WAVE_TAG[1] + 4] == _WAVE_TAG[0]
            ):
                return container
            return None
    if len(data) >= 2 and data[0] == 0xFF:
        second = data[1]
        # ADTS (AAC) and MPEG (MP3) share the 11-bit frame sync, so the ADTS
        # bit pattern is tested first: `0xFF 0xF1` is AAC, not MP3.
        if (second & 0xF6) == 0xF0:
            return "aac"
        if (second & 0xE0) == 0xE0:
            return "mp3"
    return None


def extension_for(container: str) -> str:
    """Filename extension for a sniffed container (``mp4`` -> ``m4a``)."""

    return CONTAINER_EXTENSIONS[container]


def canonical_mime_type(mime_type: str) -> str:
    """Lowercase a MIME type and drop its parameters (``;codecs=opus``)."""

    return mime_type.split(";", 1)[0].strip().lower()


def decode_audio(audio: str, mime_type: str) -> tuple[bytes, str]:
    """Validate a clip and return ``(data, filename)``.

    ``audio`` is the base64 body as it arrived; ``mime_type`` is what the
    client claims to have recorded. Raises :class:`UnsupportedAudioError` with
    a user-facing message for anything the endpoint could not decode.
    """

    declared = canonical_mime_type(mime_type)
    if not declared:
        raise UnsupportedAudioError("mimeType must not be empty")
    if declared not in SUPPORTED_MIME_TYPES:
        supported = ", ".join(sorted(SUPPORTED_MIME_TYPES))
        raise UnsupportedAudioError(
            f"unsupported audio mimeType {declared!r}; supported: {supported}"
        )

    if not audio:
        raise UnsupportedAudioError("audio must not be empty")
    encoded = audio.strip()
    if len(encoded) > MAX_ENCODED_AUDIO_BYTES:
        raise UnsupportedAudioError(
            f"recorded audio is larger than {MAX_ENCODED_AUDIO_BYTES // 1024} KiB "
            "once encoded; record a shorter clip"
        )
    # Clients that build the payload by hand may drop the padding base64 wants
    # back; nothing else is repaired, so a payload that is not base64 at all
    # still fails instead of being silently repaired into noise.
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        data = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UnsupportedAudioError(f"audio is not valid base64 ({exc})") from exc

    if not data:
        raise UnsupportedAudioError("audio must not be empty")
    if len(data) > MAX_AUDIO_BYTES:
        raise UnsupportedAudioError(
            f"recorded audio is larger than {MAX_AUDIO_BYTES // 1024} KiB; "
            "record a shorter clip"
        )

    container = sniff_container(data)
    if container is None:
        raise UnsupportedAudioError(
            "audio is not a recognisable recording (expected webm, ogg, mp4, "
            "mp3, wav, flac, or aac)"
        )
    if container not in MIME_CONTAINERS[declared]:
        raise UnsupportedAudioError(
            f"audio mimeType {declared!r} does not match the recorded data "
            f"({container})"
        )
    return data, f"dictation.{extension_for(container)}"
