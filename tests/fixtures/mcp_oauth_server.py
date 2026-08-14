"""Local OAuth-protected Streamable HTTP MCP server for integration tests."""

from __future__ import annotations

import argparse
import secrets
import time

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


class FixtureOAuthProvider:
    """Minimal in-memory authorization server with real PKCE verification."""

    def __init__(self) -> None:
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        assert client_info.client_id is not None
        self.clients[client_info.client_id] = client_info

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        assert client.client_id is not None
        code = secrets.token_urlsafe(24)
        self.codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + 60,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject="fixture-user",
        )
        return construct_redirect_uri(
            str(params.redirect_uri),
            code=code,
            state=params.state,
        )

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        del client
        return self.codes.get(authorization_code)

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        assert client.client_id is not None
        self.codes.pop(authorization_code.code, None)
        token = secrets.token_urlsafe(24)
        self.access_tokens[token] = AccessToken(
            token=token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + 3600,
            resource=authorization_code.resource,
            subject="fixture-user",
        )
        return OAuthToken(access_token=token, expires_in=3600)

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        del client, refresh_token
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        del client, refresh_token, scopes
        raise RuntimeError("fixture tokens do not refresh")

    async def load_access_token(self, token: str) -> AccessToken | None:
        return self.access_tokens.get(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.access_tokens.pop(token.token, None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("port", type=int)
    args = parser.parse_args()
    base_url = f"http://127.0.0.1:{args.port}"
    provider = FixtureOAuthProvider()
    server = FastMCP(
        "deepcode-oauth-fixture",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=base_url,
            resource_server_url=f"{base_url}/mcp",
            client_registration_options=ClientRegistrationOptions(enabled=True),
        ),
        host="127.0.0.1",
        port=args.port,
        stateless_http=True,
        json_response=True,
        log_level="ERROR",
    )

    @server.tool()
    def authorized_ping() -> str:
        """Return a deterministic authenticated response."""

        return "authorized"

    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
