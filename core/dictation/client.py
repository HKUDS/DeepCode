"""The transcription request: one POST, OpenAI-compatible, no surprises.

Parakeet is served by ``mlx_audio.server``, which exposes the same route the
OpenAI API does (``POST <base>/audio/transcriptions`` as multipart form data).
Speaking that contract instead of a bespoke one means the audio can stay on the
machine while the client stays worth nothing to a vendor-specific implementation.

Two properties matter more than the happy path:

- **The response body is never echoed.** An ASR endpoint that answers with an
  HTML error page or a stack trace must not have that text travel back to the
  UI, into logs, or into a model prompt. Only the status code is reported.
- **Redirects are not followed.** The request carries the user's audio and, on a
  hosted endpoint, a bearer token; letting a 3xx choose a new destination for
  both would undo the egress check the caller just performed.
"""

from __future__ import annotations

import httpx

#: Default whole-request budget. The first request usually pays for loading the
#: model on the server, and a local Parakeet model is a few hundred megabytes.
DEFAULT_TIMEOUT_SECONDS = 60.0

#: Statuses worth retrying: the endpoint was reachable and the clip is fine.
_RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class TranscriptionFailed(RuntimeError):
    """The endpoint did not return a transcript.

    ``retryable`` tells the caller whether trying again could help, and
    ``status_code`` is the HTTP status when there was one. The message never
    contains the response body: it is written by this client, from the status
    code and the URL alone.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class SpeechToTextClient:
    """Synchronous client for one speech-to-text endpoint.

    Synchronous on purpose: the app server runs request handlers in a thread
    pool, so a blocking call here costs one worker thread and no event loop
    time, while an async client would have to be threaded through the pool
    anyway.
    """

    def __init__(
        self,
        endpoint: str,
        model: str,
        *,
        language: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = f"{endpoint.rstrip('/')}/audio/transcriptions"
        self._model = model
        self._language = language
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        # Injected only by tests, so the request this client builds can be
        # asserted without a socket.
        self._transport = transport

    @property
    def url(self) -> str:
        """The resolved request URL, for logs and error messages."""

        return self._url

    def transcribe(self, audio: bytes, *, filename: str, mime_type: str) -> str:
        """Post one clip and return its transcript (possibly empty).

        ``filename`` comes from the sniffed container, not from the client, so
        a server that dispatches on the extension dispatches on the real bytes.
        """

        form: dict[str, str] = {"model": self._model}
        if self._language:
            form["language"] = self._language
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            with httpx.Client(
                timeout=httpx.Timeout(self._timeout_seconds),
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = client.post(
                    self._url,
                    data=form,
                    files={"file": (filename, audio, mime_type)},
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise TranscriptionFailed(
                f"dictation endpoint {self._url} did not answer within "
                f"{self._timeout_seconds:g}s",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            # Connection refused, DNS failure, TLS failure. `exc` may quote the
            # URL but never the body, and the body is what could hold a secret.
            raise TranscriptionFailed(
                f"dictation endpoint {self._url} is unreachable ({type(exc).__name__})",
                retryable=True,
            ) from exc

        if response.status_code >= 300:
            raise TranscriptionFailed(
                f"dictation endpoint {self._url} answered HTTP {response.status_code}",
                status_code=response.status_code,
                retryable=response.status_code in _RETRYABLE_STATUSES,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise TranscriptionFailed(
                f"dictation endpoint {self._url} answered with a non-JSON body",
                status_code=response.status_code,
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            raise TranscriptionFailed(
                f"dictation endpoint {self._url} answered without a 'text' field",
                status_code=response.status_code,
            )
        return payload["text"]
