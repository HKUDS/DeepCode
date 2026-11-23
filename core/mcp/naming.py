"""Deterministic model-visible naming without losing raw MCP identities."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

MAX_TOOL_NAME_LENGTH = 64
_INVALID = re.compile(r"[^A-Za-z0-9_-]+")


def visible_tool_name(
    server_id: str,
    raw_tool_name: str,
    *,
    used: set[str],
) -> str:
    server = _segment(server_id)
    tool = _segment(raw_tool_name)
    candidate = f"mcp__{server}__{tool}"
    if len(candidate) <= MAX_TOOL_NAME_LENGTH and candidate not in used:
        used.add(candidate)
        return candidate

    digest = hashlib.sha256(f"{server_id}\0{raw_tool_name}".encode()).hexdigest()[:10]
    suffix = f"__{digest}"
    prefix = candidate[: MAX_TOOL_NAME_LENGTH - len(suffix)].rstrip("_")
    candidate = f"{prefix}{suffix}"
    counter = 1
    while candidate in used:
        numbered = f"_{counter}"
        candidate = f"{prefix[: MAX_TOOL_NAME_LENGTH - len(suffix) - len(numbered)]}{suffix}{numbered}"
        counter += 1
    used.add(candidate)
    return candidate


def _segment(value: str) -> str:
    cleaned = _INVALID.sub("_", value).strip("_")
    return cleaned or "unnamed"


def schema_digest(schema: dict[str, Any] | None) -> str:
    """Stable short fingerprint of an MCP tool's normalised input schema.

    Names and descriptions are advisory text a server chooses freely; the input
    schema is the shape a call must actually match. Two tools that accept
    exactly the same arguments are interchangeable to a model even when they are
    advertised by different servers -- that is the amplifier measured in
    arXiv:2611.21577 (schema compatibility lifts the violation rate from 0 to
    ~0.78 in its C09/C10 contrast). This digest lets a client *see* that overlap
    instead of trusting the name.

    Canonicalisation is deliberate: ``sort_keys`` removes dict ordering, compact
    separators remove whitespace, and non-ASCII is preserved so a localized
    schema does not digest differently across machines. An absent schema
    digests to a fixed value rather than raising -- a no-parameter tool is a
    legitimate, common shape.
    """
    canonical = json.dumps(
        schema or {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class IdentityCollision:
    """Two or more providers advertised a tool a model cannot tell apart.

    ``kind`` is ``"name"`` when the raw tool name is shared, or ``"schema"``
    when the parameter shape is. Both are advisory signals, not verdicts: a
    legitimate deployment can expose the same ``read_file`` on two servers.
    """

    kind: str
    value: str
    server_ids: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str, tuple[str, ...]]:
        return (self.kind, self.value, self.server_ids)


def find_identity_collisions(
    entries: Iterable[tuple[str, str, str]],
) -> list[IdentityCollision]:
    """Find tools that two different providers advertise indistinguishably.

    ``entries`` are ``(server_id, raw_tool_name, schema_digest)`` triples. A
    collision needs *at least two distinct server ids*: the same server listing
    the same shape twice is its own bug, not a provider-binding problem.

    An empty digest never collides -- it means we could not fingerprint the
    schema, and reporting every unfingerprintable tool as a collision would
    drown the signal.
    """
    by_name: dict[str, set[str]] = {}
    by_digest: dict[str, set[str]] = {}
    for server_id, raw_name, digest in entries:
        if raw_name:
            by_name.setdefault(raw_name, set()).add(server_id)
        if digest:
            by_digest.setdefault(digest, set()).add(server_id)

    collisions: list[IdentityCollision] = []
    for raw_name, servers in sorted(by_name.items()):
        if len(servers) > 1:
            collisions.append(
                IdentityCollision("name", raw_name, tuple(sorted(servers)))
            )
    for digest, servers in sorted(by_digest.items()):
        if len(servers) > 1:
            collisions.append(
                IdentityCollision("schema", digest, tuple(sorted(servers)))
            )
    return collisions


__all__ = [
    "MAX_TOOL_NAME_LENGTH",
    "IdentityCollision",
    "find_identity_collisions",
    "schema_digest",
    "visible_tool_name",
]
