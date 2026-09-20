# Voice Dictation (Parakeet)

DeepCode supports push-to-talk voice input directly in the composer. Spoken text is transcribed through a speech-to-text model—such as NVIDIA Parakeet—and inserted right where your cursor is, without bypassing your review before sending.

## Overview

- **No audio leaves without configuration.** The microphone button only appears when a `dictation` block is declared in your configuration.
- **Two operational modes:**
  - **In-process local runner (`endpoint: "local"`):** Runs transcription directly on your machine via `parakeet-mlx` without starting or maintaining any background server.
  - **OpenAI-compatible server (`endpoint: "http://..."`):** For remote endpoints or local servers such as `mlx_audio.server`.
- **Composer integration.** The transcribed text lands in the composer draft at the current cursor position so you can review, edit, or append to it before starting or steering a turn.
- **Escape to discard.** Pressing `Escape` (or clicking the discard button) while recording drops the audio immediately without making a request.

---

## Option 1: Zero-Server Local Dictation (Recommended)

If you have `parakeet-mlx` installed on your machine (`pip install parakeet-mlx`), DeepCode can transcribe clips directly in-process without any daemon.

Add to `~/.deepcode/deepcode_config.json`:

```json
{
  "dictation": {
    "endpoint": "local",
    "model": "mlx-community/parakeet-tdt-0.6b-v3",
    "language": "pt"
  }
}
```

That's it! No daemon or separate terminal needs to be kept open.

---

## Option 2: Setting up a Local Parakeet HTTP Server

You can also run NVIDIA Parakeet as a standalone HTTP server using `mlx-audio`:

```bash
# 1. Install mlx-audio in an isolated environment
pip install mlx-audio

# 2. Start the transcription server
mlx_audio.server --host 127.0.0.1 --port 8000
```

The server exposes an OpenAI-compatible speech-to-text API at:
`http://127.0.0.1:8000/v1/audio/transcriptions`

---

## Configuring DeepCode

Add the `dictation` block to your user configuration file (`~/.deepcode/deepcode_config.json`):

```json
{
  "dictation": {
    "endpoint": "http://127.0.0.1:8000/v1",
    "model": "mlx-community/parakeet-tdt-0.6b-v3",
    "language": "pt",
    "maxAudioSeconds": 120,
    "timeoutSeconds": 60
  }
}
```

### Configuration Options

| Field | Type | Default | Description |
|---|---|---|---|
| `endpoint` | string | *required* | Base URL of the OpenAI-compatible transcription server (e.g. `http://127.0.0.1:8000/v1`). |
| `model` | string | *required* | The speech-to-text model identifier recognized by the server. |
| `language` | string \| null | `null` | Optional ISO 639-1 language hint (e.g. `"en"`, `"pt"`, `"es"`). |
| `apiKeyEnv` | string \| null | `null` | Name of an environment variable containing the bearer token (for authenticated endpoints). |
| `maxAudioSeconds` | integer | `120` | Maximum recording length in seconds (1–600). Recording stops automatically when this cap is reached. |
| `timeoutSeconds` | float | `60.0` | Whole-request transcription timeout budget (1–600). |

---

## Security and Privacy Guardrails

1. **User-owned configuration.** Project configuration files cannot configure or redirect the dictation endpoint; repository-level dictation overrides are discarded automatically to prevent malicious repositories from redirecting microphone audio.
2. **Model egress policy.** If you enforce an egress policy (`providers.egress`), the dictation endpoint host is checked against allowed and blocked domains before any audio data leaves the machine.
3. **Clip size limits.** Decoded audio is capped at 512 KiB (~2 minutes of Opus), keeping requests safely within the JSON-RPC message envelope.
4. **Secure contexts.** Web browsers and WebViews permit microphone access only in secure contexts (`https://` or `http://127.0.0.1`). Connecting over a remote plain HTTP IP address disables browser recording capabilities.
