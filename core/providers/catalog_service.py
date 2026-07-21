"""Dynamic model discovery with a durable last-known-good cache."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from core.config import deepcode_home
from core.file_lock import exclusive_file_lock
from core.providers.catalog import known_model_ids, resolve_model_info
from core.providers.profiles import ConnectionResolver, ResolvedConnection


@dataclass(frozen=True, slots=True)
class CatalogModel:
    id: str
    name: str
    context_window: int
    max_output_tokens: int
    supported_parameters: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "contextWindow": self.context_window,
            "maxOutputTokens": self.max_output_tokens,
            "supportedParameters": list(self.supported_parameters),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "CatalogModel | None":
        if not isinstance(value, dict) or not value.get("id"):
            return None
        model_id = str(value["id"])
        fallback = resolve_model_info(model_id)
        try:
            return cls(
                id=model_id,
                name=str(value.get("name") or model_id),
                context_window=int(
                    value.get("contextWindow") or fallback.context_window
                ),
                max_output_tokens=int(
                    value.get("maxOutputTokens") or fallback.max_output_tokens
                ),
                supported_parameters=tuple(
                    str(item)
                    for item in value.get("supportedParameters", [])
                    if isinstance(item, str)
                ),
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    connection_id: str
    models: tuple[CatalogModel, ...]
    source: str
    stale: bool = False
    error: str | None = None
    refreshed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "connectionId": self.connection_id,
            "models": [model.to_dict() for model in self.models],
            "source": self.source,
            "stale": self.stale,
            "error": self.error,
            "refreshedAt": self.refreshed_at,
        }


class ModelCatalogService:
    """Discover provider models without putting network I/O on the Turn path."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        ttl_seconds: int = 3600,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.path = (
            Path(path).expanduser().resolve()
            if path is not None
            else deepcode_home() / "model_catalog_cache.json"
        )
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds

    def list_models(
        self,
        connection: ResolvedConnection,
        *,
        refresh: bool = False,
    ) -> ModelCatalog:
        connection_revision = ConnectionResolver.connection_revision(connection)
        cached = self._cached(connection.id, connection_revision)
        now = time.time()
        if (
            cached is not None
            and not refresh
            and cached.refreshed_at is not None
            and now - cached.refreshed_at <= self.ttl_seconds
        ):
            return cached

        if connection.model_catalog == "manual":
            models = self._fallback_models(connection)
            return ModelCatalog(connection.id, models, "manual", refreshed_at=now)

        try:
            models = self._fetch(connection)
            if not models:
                raise ValueError("provider returned an empty model catalog")
            result = ModelCatalog(
                connection.id,
                tuple(sorted(models, key=lambda item: item.id.lower())),
                "remote",
                refreshed_at=now,
            )
            self._store(result, connection_revision)
            return result
        except Exception as exc:  # noqa: BLE001 - stale cache is deliberate UX
            detail = _safe_error(exc)
            if cached is not None:
                return ModelCatalog(
                    connection.id,
                    cached.models,
                    cached.source,
                    stale=True,
                    error=detail,
                    refreshed_at=cached.refreshed_at,
                )
            return ModelCatalog(
                connection.id,
                self._fallback_models(connection),
                "fallback",
                stale=True,
                error=detail,
                refreshed_at=None,
            )

    def test_connection(self, connection: ResolvedConnection) -> dict[str, Any]:
        started = time.monotonic()
        catalog = self.list_models(connection, refresh=True)
        ok = catalog.source == "remote" and not catalog.stale
        return {
            "connectionId": connection.id,
            "ok": ok,
            "latencyMs": round((time.monotonic() - started) * 1000),
            "modelCount": len(catalog.models),
            "error": None if ok else catalog.error,
        }

    def cached_model(
        self,
        connection: ResolvedConnection,
        model_id: str,
    ) -> CatalogModel | None:
        """Read last-known-good metadata without network I/O."""

        revision = ConnectionResolver.connection_revision(connection)
        cached = self._cached(connection.id, revision)
        if cached is None:
            return None
        return next((model for model in cached.models if model.id == model_id), None)

    def _fetch(self, connection: ResolvedConnection) -> tuple[CatalogModel, ...]:
        url, headers = _catalog_request(connection)
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
        rows = payload.get("data", payload.get("models", payload))
        if not isinstance(rows, list):
            raise ValueError("provider model response has no data array")
        parsed = tuple(
            model
            for row in rows
            if isinstance(row, dict)
            for model in [_parse_model(row)]
            if model is not None
        )
        return parsed

    def _fallback_models(
        self, connection: ResolvedConnection
    ) -> tuple[CatalogModel, ...]:
        requested = list(connection.manual_models)
        if not requested:
            prefix = f"{connection.provider_name}/"
            requested = [
                model_id
                for model_id in known_model_ids()
                if connection.provider_name == "openrouter"
                or model_id.startswith(prefix)
                or _looks_native(model_id, connection.provider_name)
            ]
        return tuple(_offline_model(model_id) for model_id in dict.fromkeys(requested))

    def _cached(
        self,
        connection_id: str,
        connection_revision: str,
    ) -> ModelCatalog | None:
        data = self._read_cache().get("connections", {}).get(connection_id)
        if (
            not isinstance(data, dict)
            or data.get("connectionRevision") != connection_revision
        ):
            return None
        models = tuple(
            parsed
            for value in data.get("models", [])
            for parsed in [CatalogModel.from_dict(value)]
            if parsed is not None
        )
        if not models:
            return None
        refreshed = data.get("refreshedAt")
        return ModelCatalog(
            connection_id,
            models,
            str(data.get("source") or "cache"),
            refreshed_at=float(refreshed) if refreshed is not None else None,
        )

    def _store(self, catalog: ModelCatalog, connection_revision: str) -> None:
        with exclusive_file_lock(self.lock_path):
            data = self._read_cache()
            connections = data.get("connections", {})
            if not isinstance(connections, dict):
                connections = {}
            connections[catalog.connection_id] = {
                "connectionRevision": connection_revision,
                "source": catalog.source,
                "refreshedAt": catalog.refreshed_at,
                "models": [model.to_dict() for model in catalog.models],
            }
            self._replace({"version": 1, "connections": connections})

    def _read_cache(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": 1, "connections": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "connections": {}}
        return value if isinstance(value, dict) else {"version": 1, "connections": {}}

    def _replace(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _catalog_request(connection: ResolvedConnection) -> tuple[str, dict[str, str]]:
    headers = dict(connection.extra_headers)
    if connection.model_catalog == "anthropic":
        base = (connection.api_base or "https://api.anthropic.com").rstrip("/")
        url = f"{base}/v1/models" if not base.endswith("/v1") else f"{base}/models"
        if connection.api_key:
            headers["x-api-key"] = connection.api_key
        headers.setdefault("anthropic-version", "2023-06-01")
        return url, headers

    if connection.model_catalog == "openrouter":
        base = (
            connection.api_base or "https://openrouter.ai/api/v1"
        ).rstrip("/")
    elif connection.provider_name == "openai":
        base = (connection.api_base or "https://api.openai.com/v1").rstrip("/")
    else:
        if not connection.api_base:
            raise ValueError(
                f"connection '{connection.id}' needs apiBase for model discovery"
            )
        base = connection.api_base.rstrip("/")
    if connection.api_key:
        headers["Authorization"] = f"Bearer {connection.api_key}"
    return f"{base}/models", headers


def _parse_model(value: dict[str, Any]) -> CatalogModel | None:
    raw_id = value.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return None
    model_id = raw_id.strip()
    fallback = resolve_model_info(model_id)
    top_provider = (
        value.get("top_provider")
        if isinstance(value.get("top_provider"), dict)
        else {}
    )
    context = (
        value.get("context_length")
        or value.get("context_window")
        or value.get("max_input_tokens")
        or fallback.context_window
    )
    output = (
        value.get("max_output_tokens")
        or top_provider.get("max_completion_tokens")
        or fallback.max_output_tokens
    )
    supported = value.get("supported_parameters", [])
    return CatalogModel(
        id=model_id,
        name=str(value.get("name") or value.get("display_name") or model_id),
        context_window=max(1, int(context)),
        max_output_tokens=max(1, int(output)),
        supported_parameters=tuple(
            str(item) for item in supported if isinstance(item, str)
        )
        if isinstance(supported, list)
        else (),
    )


def _offline_model(model_id: str) -> CatalogModel:
    info = resolve_model_info(model_id)
    return CatalogModel(
        id=model_id,
        name=model_id,
        context_window=info.context_window,
        max_output_tokens=info.max_output_tokens,
    )


def _looks_native(model_id: str, provider_name: str) -> bool:
    lower = model_id.lower()
    markers = {
        "openai": ("gpt-", "o1", "o3", "o4"),
        "anthropic": ("claude-",),
        "gemini": ("gemini-",),
        "deepseek": ("deepseek-",),
        "dashscope": ("qwen",),
    }
    return lower.startswith(markers.get(provider_name, ()))


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} from model catalog"
    return f"{type(exc).__name__}: {exc}"[:300]


__all__ = ["CatalogModel", "ModelCatalog", "ModelCatalogService"]
