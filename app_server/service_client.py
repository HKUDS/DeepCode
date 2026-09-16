"""Authenticated local management client; never owns an application runtime."""

from __future__ import annotations

import hmac
import secrets
from typing import Any

import httpx

from app_server.service_state import ServiceFiles, identity_proof


class ServiceUnavailable(RuntimeError):
    pass


class ServiceOperationError(RuntimeError):
    pass


class ServiceClient:
    def __init__(self, files: ServiceFiles) -> None:
        self.files = files

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 5.0,
        instance_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.files.running():
            raise ServiceUnavailable("DeepCode service is not running")
        discovered = self.files.read()
        if discovered is None:
            raise ServiceUnavailable("DeepCode service is starting or stopping")
        record, token = discovered
        if instance_id is not None and record.instance_id != instance_id:
            raise ServiceUnavailable("Service instance changed before the operation")
        challenge = secrets.token_hex(16)
        try:
            # The lock and challenge prevent sending a stale token to another
            # process that has taken the old port. Ignore ambient HTTP proxies.
            with httpx.Client(
                base_url=record.url, trust_env=False, timeout=timeout
            ) as client:
                response = client.get(
                    "/control/identity", params={"challenge": challenge}
                )
                response.raise_for_status()
                identity = response.json()
                proof = identity.get("proof") if isinstance(identity, dict) else None
                if not isinstance(proof, str) or not hmac.compare_digest(
                    proof.encode(),
                    identity_proof(token, record.instance_id, challenge).encode(),
                ):
                    raise ServiceUnavailable(
                        "Service identity does not match its private record"
                    )
                response = client.post(
                    "/control/rpc",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-DeepCode-Instance": record.instance_id,
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": method,
                        "params": params or {},
                    },
                )
                response.raise_for_status()
                value = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Keep diagnostics actionable without echoing request headers/tokens.
            detail = (
                f"HTTP {exc.response.status_code}"
                if isinstance(exc, httpx.HTTPStatusError)
                else type(exc).__name__
            )
            raise ServiceUnavailable(
                f"Cannot communicate with the local DeepCode service ({detail}); check service logs"
            ) from exc
        if not isinstance(value, dict) or value.get("id") != 1:
            raise ServiceUnavailable("Invalid service response")
        if "error" in value:
            error = value["error"]
            if not isinstance(error, dict) or not isinstance(error.get("message"), str):
                raise ServiceUnavailable("Invalid service error response")
            raise ServiceOperationError(error["message"])
        result = value.get("result")
        if (
            not isinstance(result, dict)
            or result.get("instanceId") != record.instance_id
        ):
            raise ServiceUnavailable("Service instance changed during the request")
        return result
