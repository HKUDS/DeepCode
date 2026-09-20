"""Voice dictation: turn a recorded clip into text through a Parakeet server.

The feature is split so each half can be tested on its own:

- :mod:`core.dictation.audio` — pure byte handling. It decodes the base64
  payload the client sends, decides what container the bytes really are, and
  names the upload accordingly. No I/O, no config.
- :mod:`core.dictation.client` — the one HTTP call. It speaks the OpenAI
  transcription contract, which is what local Parakeet servers expose, so the
  audio never has to leave the machine.
- :class:`core.application.dictation_service.DictationService` — the policy
  layer: whether dictation is configured at all, whether the endpoint is
  allowed by the egress policy, and how the failures map onto application
  errors.
"""

from core.dictation.audio import (
    MAX_AUDIO_BYTES,
    MAX_ENCODED_AUDIO_BYTES,
    SUPPORTED_MIME_TYPES,
    UnsupportedAudioError,
    decode_audio,
    extension_for,
    sniff_container,
)
from core.dictation.client import (
    SpeechToTextClient,
    TranscriptionFailed,
)

__all__ = [
    "MAX_AUDIO_BYTES",
    "MAX_ENCODED_AUDIO_BYTES",
    "SUPPORTED_MIME_TYPES",
    "SpeechToTextClient",
    "TranscriptionFailed",
    "UnsupportedAudioError",
    "decode_audio",
    "extension_for",
    "sniff_container",
]
