"""Application service for voice dictation (prompt-box microphone input).

Why this is a service and not a tool call: a transcript is not agent input by
itself. The user dictates into the composer, sees the text, edits it, and sends
it — so the audio is transcribed *before* a Turn exists and the result never
reaches the model on its own. The service therefore lives on the input path,
not in the agent loop.

Responsibilities, in order:

1. **Is dictation configured at all?** An absent ``dictation`` block means the
   feature is off and the app server reports it as unavailable, so the prompt
   box never offers a microphone that cannot work.
2. **Is the endpoint allowed?** A recording is a verbatim copy of what the user
   said, so the endpoint receiving it is a trust boundary, evaluated against the
   same ``providers.egress`` policy as model traffic.
3. **What are the bytes?** Format and size checks stay in
   :mod:`core.dictation.audio`, which is pure and testable on its own.
4. **Translate failures.** Every outcome becomes either a transcript or a stable
   application error; no bare transport exception escapes this module.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from core.application.config_store import ConfigStore
from core.application.errors import (
    DictationNotConfiguredError,
    DictationUnavailableError,
    InvalidArgumentError,
)
from core.application.project_service import ProjectService
from core.config import (
    DeepCodeConfig,
    DictationConfig,
    load_config,
    load_config_for_workspace,
)
from core.dictation.audio import (
    UnsupportedAudioError,
    canonical_mime_type,
    decode_audio,
)
from core.dictation.client import SpeechToTextClient, TranscriptionFailed
from core.providers.egress import (
    WARN,
    evaluate_provider_egress,
    resolve_egress_policy,
)

#: Builds the client for one request from the resolved endpoint config, the API
#: key (or ``None``), and the language hint to send. Injectable so tests can
#: substitute a client without patching process-global state.
ClientFactory = Callable[[DictationConfig, str | None, str | None], SpeechToTextClient]


def _default_client_factory(
    config: DictationConfig,
    api_key: str | None,
    language: str | None,
) -> SpeechToTextClient:
    return SpeechToTextClient(
        config.endpoint,
        config.model,
        language=language,
        api_key=api_key,
        timeout_seconds=config.timeout_seconds,
    )


class DictationService:
    """Resolve dictation config and transcribe one clip per request."""

    def __init__(
        self,
        projects: ProjectService | None = None,
        *,
        config_store: ConfigStore | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.projects = projects
        self.config_store = config_store or ConfigStore()
        self._client_factory = client_factory or _default_client_factory

    def status(self, project_id: str | None = None) -> dict[str, Any]:
        """Report the capability without revealing whether a secret resolves.

        ``model`` and ``maxAudioSeconds`` describe the *configured* endpoint, so
        they are ``None`` exactly when ``available`` is ``False``. The status
        deliberately says nothing about credentials: whether a bearer token
        happens to be set in the environment is not a fact the renderer needs,
        and the failure would surface on the first clip anyway.
        """

        config = self._config(project_id).dictation
        if config is None:
            return {"available": False, "model": None, "maxAudioSeconds": None}
        return {
            "available": True,
            "model": config.model,
            "maxAudioSeconds": config.max_audio_seconds,
        }

    def transcribe(
        self,
        *,
        audio: str,
        mime_type: str,
        language: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Transcribe one base64 clip into ``{"text", "model"}``.

        ``language`` is a per-clip hint used only when ``dictation.language`` is
        unset: a value in the config file is an explicit user choice and wins
        over whatever a client offers for a single request.
        """

        loaded = self._config(project_id)
        config = loaded.dictation
        if config is None:
            raise DictationNotConfiguredError()

        denial = self._egress_denial(config, loaded)
        if denial is not None:
            raise DictationUnavailableError(denial, retryable=False)

        try:
            data, filename = decode_audio(audio, mime_type)
        except UnsupportedAudioError as exc:
            raise InvalidArgumentError(str(exc)) from exc

        client = self._client_factory(
            config, self._api_key(config), config.language or (language or None)
        )
        try:
            text = client.transcribe(
                data,
                filename=filename,
                mime_type=canonical_mime_type(mime_type),
            )
        except TranscriptionFailed as exc:
            raise DictationUnavailableError(str(exc), retryable=exc.retryable) from exc
        logger.trace(
            "Dictation transcribed: model={} bytes={} chars={}",
            config.model,
            len(data),
            len(text),
        )
        return {"text": text, "model": config.model}

    # -- config and secrets -------------------------------------------------

    def _config(self, project_id: str | None) -> DeepCodeConfig:
        """Load the config that applies to this request.

        Same precedence as ``LLMConfigurationService._config``: an unscoped
        request reads the user config, a project-scoped one reads the layered
        user + project config. The project layer cannot carry a ``dictation``
        block (``_project_runtime_layer`` drops it), so a project-scoped read
        never changes *whether* dictation is on, only honours the workspace.
        """

        if project_id is None:
            return load_config(config_path=self.config_store.path)
        if self.projects is None:
            raise InvalidArgumentError(
                "project-scoped dictation settings are unavailable"
            )
        project = self.projects.read(project_id)
        workspace = Path(project.canonical_path).resolve(strict=False)
        return load_config_for_workspace(workspace)

    def _api_key(self, config: DictationConfig) -> str | None:
        """Read the bearer token from the environment variable the user named."""

        if not config.api_key_env:
            return None
        key = os.environ.get(config.api_key_env)
        if not key:
            raise DictationNotConfiguredError(
                f"dictation.apiKeyEnv names {config.api_key_env!r}, which is not "
                "set in the environment"
            )
        return key

    # -- egress -------------------------------------------------------------

    def _egress_denial(
        self, config: DictationConfig, loaded: DeepCodeConfig
    ) -> str | None:
        """Return a denial message, or ``None`` when the endpoint may be used.

        Mirrors ``make_llm_provider``: the decision is a hostname comparison, so
        it costs nothing per request, and ``warn`` mode records the same message
        without blocking. The message is written here rather than taken from
        ``EgressDecision.reason`` because that text talks about
        ``providers.<name>.apiBase``, and the operator needs to be pointed at
        the key this endpoint actually lives under.
        """

        policy = resolve_egress_policy(loaded)
        decision = evaluate_provider_egress(
            config.endpoint,
            endpoint_class="dictation",
            allowed_domains=policy.allowed_domains,
            blocked_domains=policy.blocked_domains,
        )
        if decision.allowed:
            logger.trace(
                "Dictation egress ok: host={} model={}", decision.host, config.model
            )
            return None
        message = (
            f"dictation endpoint host {decision.host or config.endpoint!r} is not "
            "allowed by the model egress policy (providers.egress)"
        )
        if policy.mode == WARN:
            logger.warning(message)
            return None
        return message
