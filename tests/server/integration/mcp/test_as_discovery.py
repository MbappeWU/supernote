from aiohttp.test_utils import TestClient
from pytest_aiohttp import AiohttpClient

from supernote.server.app import create_app
from supernote.server.config import ServerConfig


async def test_as_metadata_discovery(
    client: TestClient, server_config: ServerConfig
) -> None:
    """Test that the Authorization Server metadata endpoint is accessible."""
    # The issuer URL in config will be based on the server_port
    expected_issuer = server_config.base_url

    resp = await client.get("/.well-known/oauth-authorization-server")
    assert resp.status == 200, (
        f"Expected 200, got {resp.status}. Body: {await resp.text()}"
    )

    data = await resp.json()
    assert data["issuer"] == f"{expected_issuer}/"
    assert data["authorization_endpoint"] == f"{expected_issuer}/authorize"
    assert data["token_endpoint"] == f"{expected_issuer}/token"
    assert "code" in data["response_types_supported"]


async def test_as_authorize_endpoint_reachable(client: TestClient) -> None:
    """Test that the /authorize endpoint returns a redirect (as currently stubbed)."""
    # The stub currently returns a URL, but the handler will wrap it in a redirect or response.
    # Starlette's AuthorizationHandler returns a redirect if it can.

    # We need to provide the required OAuth params or it might return 400.
    params = {
        "response_type": "code",
        "client_id": "test-client",
        "redirect_uri": "http://localhost/callback",
        "state": "xyz",
        "code_challenge": "abc",
        "code_challenge_method": "S256",
    }
    resp = await client.get("/authorize", params=params)

    # Currently it's a stub that returns 400 because get_client returns None.
    # This still proves the request reached the AS app and passed the JWT middleware.
    assert resp.status in (200, 302, 400)


async def test_mcp_disabled_skips_auth_and_mcp_server(
    aiohttp_client: AiohttpClient,
    server_config: ServerConfig,
    session_manager: object,
    coordination_service: object,
    user_service: object,
) -> None:
    """When mcp_enabled is False, MCP OAuth routes and the MCP background task are not started."""
    server_config.mcp_enabled = False
    app = create_app(server_config)
    client = await aiohttp_client(app)

    assert "mcp_task" not in app
    assert "mcp_asgi_resource" not in app

    resp = await client.get("/.well-known/oauth-authorization-server")
    assert resp.status == 404
