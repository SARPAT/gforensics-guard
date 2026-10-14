"""Optional shared-secret check in front of the MCP paths.

This answers WHO may talk to the MCP servers (only the ArmorIQ proxy, once
the platform registration carries the token), never WHAT they may do: that
stays with ArmorIQ policies. Without it, anyone who finds the Render URL
could call drain_nodes directly and skip ArmorIQ entirely.

Off when GFG_MCP_TOKEN is unset (CP0 registrations used auth "None").
Accepts `Authorization: Bearer <token>` or `X-API-Key: <token>`.
"""
from __future__ import annotations

import hmac
import json


def _presented(scope) -> str:
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-api-key", "").strip()


class RequireToken:
    """ASGI wrapper: 401 unless the request carries the expected token."""

    def __init__(self, app, token: str | None):
        self.app, self.token = app, token or None

    async def __call__(self, scope, receive, send):
        if self.token is None or scope["type"] != "http":
            return await self.app(scope, receive, send)
        if hmac.compare_digest(_presented(scope).encode(), self.token.encode()):
            return await self.app(scope, receive, send)
        body = json.dumps({"error": "unauthorized", "hint": "MCP token required"}).encode()
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"www-authenticate", b"Bearer")]})
        await send({"type": "http.response.body", "body": body})
