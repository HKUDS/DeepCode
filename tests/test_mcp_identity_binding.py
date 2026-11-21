"""Provider-bound tool identity: schema fingerprints and cross-server overlap.

Background: arXiv:2611.21577 ("When Tool Names Are Not Identities") shows that
MCP tool metadata is not bound to a provider -- a second server can advertise a
tool the model cannot tell from the real one. The client-side answer available
to us is *not* enforcement (there is no signed identity to check) but making the
overlap observable: a stable schema fingerprint plus a collision report.

These tests pin three properties that are easy to regress:

* the digest ignores dict ordering and whitespace, so two servers emitting the
  same schema digest identically;
* a collision needs two *different* servers -- one server repeating itself is a
  different bug and must not be reported as a provider-binding overlap;
* an empty digest never collides, because "we could not fingerprint this" is
  not evidence of a duplicate.
"""

from __future__ import annotations

from core.mcp.models import McpToolIdentity
from core.mcp.naming import (
    IdentityCollision,
    find_identity_collisions,
    schema_digest,
)


class TestSchemaDigest:
    def test_digest_ignores_key_order_and_whitespace(self) -> None:
        first = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "number"}},
        }
        second = {
            "properties": {"b": {"type": "number"}, "a": {"type": "string"}},
            "type": "object",
        }
        assert schema_digest(first) == schema_digest(second)

    def test_digest_changes_when_the_shape_changes(self) -> None:
        base = {"type": "object", "properties": {"path": {"type": "string"}}}
        widened = {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "x"}},
        }
        assert schema_digest(base) != schema_digest(widened)

    def test_absent_schema_is_a_value_not_an_error(self) -> None:
        assert schema_digest(None) == schema_digest({})
        assert len(schema_digest(None)) == 12

    def test_digest_is_deterministic(self) -> None:
        schema = {"type": "object", "properties": {}}
        assert schema_digest(schema) == schema_digest(dict(schema))


class TestIdentityCollisions:
    def test_same_name_from_two_servers_is_a_collision(self) -> None:
        collisions = find_identity_collisions(
            [
                ("server-a", "read_file", "aaa111"),
                ("server-b", "read_file", "bbb222"),
            ]
        )
        name_hits = [c for c in collisions if c.kind == "name"]
        assert len(name_hits) == 1
        assert name_hits[0].value == "read_file"
        assert name_hits[0].server_ids == ("server-a", "server-b")

    def test_same_schema_from_two_servers_is_a_collision(self) -> None:
        collisions = find_identity_collisions(
            [
                ("server-a", "read_text", "same123"),
                ("server-b", "load_document", "same123"),
            ]
        )
        schema_hits = [c for c in collisions if c.kind == "schema"]
        assert len(schema_hits) == 1
        assert schema_hits[0].value == "same123"

    def test_one_server_repeating_itself_is_not_a_collision(self) -> None:
        collisions = find_identity_collisions(
            [
                ("server-a", "read_file", "same123"),
                ("server-a", "read_file", "same123"),
            ]
        )
        assert collisions == []

    def test_empty_digest_never_collides(self) -> None:
        collisions = find_identity_collisions(
            [
                ("server-a", "alpha", ""),
                ("server-b", "beta", ""),
            ]
        )
        assert collisions == []

    def test_disjoint_servers_produce_nothing(self) -> None:
        collisions = find_identity_collisions(
            [
                ("server-a", "alpha", "aaa111"),
                ("server-b", "beta", "bbb222"),
            ]
        )
        assert collisions == []

    def test_collision_key_is_stable_for_dedup(self) -> None:
        one = IdentityCollision("name", "read_file", ("a", "b"))
        two = IdentityCollision("name", "read_file", ("a", "b"))
        assert one.key == two.key


class TestIdentityCarriesTheDigest:
    def test_digest_defaults_to_empty_for_old_construction_sites(self) -> None:
        identity = McpToolIdentity(
            server_id="server-a",
            server_name="Server A",
            source="user",
            raw_name="read_file",
        )
        assert identity.schema_digest == ""
