"""Decoding service views back into the client's dataclasses."""

from __future__ import annotations

from dataclasses import dataclass

from cli.rpc_models import from_view


@dataclass(frozen=True)
class _Server:
    name: str
    env_http_headers: tuple[tuple[str, str], ...]


def test_a_mapping_sent_as_an_object_decodes_into_pairs():
    # The MCP view sends header -> variable maps as JSON objects; the TUI's
    # McpServerInfo holds them as pairs, and decoding used to fail outright.
    server = from_view(
        _Server,
        {"name": "web", "envHttpHeaders": {"Authorization": "TOKEN", "X-Org": "ORG"}},
    )

    assert server.env_http_headers == (("Authorization", "TOKEN"), ("X-Org", "ORG"))
    assert (
        from_view(_Server, {"name": "web", "envHttpHeaders": {}}).env_http_headers == ()
    )
