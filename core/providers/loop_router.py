"""P1-4: opt-in cheap-model loop routing for the agent runner.

Claude Code-style tiering: most turns are short, routine tool steps that a
cheap/fast model handles fine; only genuinely heavy reasoning deserves the
primary model. This wrapper sits around the provider the runner already uses
and, *when enabled*, sends lightweight turns to a cheaper provider.

It is deliberately OFF by default and opt-in at construction — enabling it is a
runtime choice, because routing quality depends on the models you pair. The
wrapper only ever proxies; unknown attributes fall through to the primary
provider, so nothing else in the harness changes.

Decision rule (conservative, deterministic):
- a request is "light" when the *serialized* user/system text is short and the
  conversation has not yet produced assistant/tool turns (fresh single-shot /
  meta turns such as a first triage, a tiny summarization call, tool-schema
  prep). Anything longer or already mid-conversation goes to the primary model.
"""

from __future__ import annotations

from typing import Any

_LIGHT_MAX_CHARS = 4_000


class TieredLoopProvider:
    """Proxy a provider, routing light single-shot turns to a cheap provider."""

    def __init__(
        self,
        primary: Any,
        light: Any,
        *,
        light_max_chars: int = _LIGHT_MAX_CHARS,
        enabled: bool = True,
    ) -> None:
        self._primary = primary
        self._light = light
        self._light_max_chars = max(256, light_max_chars)
        self._enabled = enabled

    # -- public surface the runner expects -----------------------------------

    async def chat_with_retry(self, *args: Any, **kwargs: Any) -> Any:
        provider = self._pick(args, kwargs)
        fn = getattr(provider, "chat_with_retry", None)
        if fn is None:  # fall back to chat()
            fn = provider.chat
        return await fn(*args, **kwargs)

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        provider = self._pick(args, kwargs)
        return await provider.chat(*args, **kwargs)

    # -- helpers --------------------------------------------------------------

    def _pick(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if not self._enabled:
            return self._primary
        messages = kwargs.get("messages") or (
            args[0] if args and isinstance(args[0], list) else None
        )
        if not isinstance(messages, list) or not messages:
            return self._primary
        size = sum(
            len(str(m.get("content", ""))) for m in messages if isinstance(m, dict)
        )
        has_turn = any(
            m.get("role") in {"assistant", "tool"}
            for m in messages
            if isinstance(m, dict)
        )
        if not has_turn and size <= self._light_max_chars:
            return self._light
        return self._primary

    def __getattr__(self, name: str) -> Any:
        # Proxy anything we don't explicitly override to the primary provider.
        return getattr(self._primary, name)


__all__ = ["TieredLoopProvider"]
