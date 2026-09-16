"""Stable submission identity, independent of changing runtime defaults."""

from __future__ import annotations

import hashlib
import json

from core.domain.execution_permission import ExecutionPermissionMode
from core.domain.execution_security import ExecutionSecurityProfile
from core.domain.message_provenance import TurnInputDelivery, TurnInputSource
from core.domain.runtime_coordination import ExecutionClass
from core.skills.models import SkillSelection


def submission_fingerprint(
    *,
    prompt: str,
    skill_ids: tuple[str, ...],
    connection_id: str | None,
    model: str | None,
    reasoning_effort: str | None,
    source: TurnInputSource,
    delivery: TurnInputDelivery,
    execution_class: ExecutionClass,
    security_override: ExecutionSecurityProfile | None,
    permission_override: ExecutionPermissionMode | None,
) -> str:
    # Requested fields, not the resolved profile or Goal-inherited skills:
    # a lost response must stay recoverable after those defaults change.
    payload = {
        "prompt": prompt.strip(),
        "skills": [SkillSelection(skill_id=s).skill_id for s in skill_ids],
        "connectionId": connection_id,
        "model": model,
        "reasoningEffort": reasoning_effort,
        "source": source.value,
        "delivery": delivery.value,
        "executionClass": execution_class.value,
        "securityOverride": security_override.to_dict() if security_override else None,
        "permissionOverride": permission_override.value
        if permission_override
        else None,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return "v1:" + hashlib.sha256(encoded).hexdigest()
