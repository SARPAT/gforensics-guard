"""Shared-secret check in front of the MCP paths.

This answers WHO may talk to the MCP servers (only the ArmorIQ proxy, carrying the
token), never WHAT they may do: that stays with ArmorIQ policies. Without it, anyone
who finds the Render URL could call drain_nodes directly and skip ArmorIQ entirely.

Off when GFG_MCP_TOKEN is unset. Accepts the token as:
    Authorization: Bearer <token>
    X-API-Key: <token>
    X-Armoriq-MCP-Auth: base64(json)   what armoriq-sdk 0.6.10 attaches on invoke() from
                                       ARMORIQ_MCP_<NAME>_AUTH_TYPE/_TOKEN (bearer) or
                                       ARMORIQ_MCP_CREDENTIALS, if the proxy relays it
GFG_MCP_AUTH_MODE=observe lets every request through but records HOW (never the value)
a token was presented, so we can learn what the proxy forwards before closing the door.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import time

# Last presentation seen per path: header name + whether it matched. Never the value.
LAST_SEEN: dict[str, dict] = {}


def _from_armoriq_header(raw: str) -> str:
    try:
        cred = json.loads(base64.b64decode(raw))
    except (binascii.Error, ValueError):
        return ""
    if not isinstance(cred, dict):
        return ""
    return str(cred.get("token") or cred.get("apiKey") or cred.get("api_key") or "").strip()


def _presented(scope) -> tuple[str, str]:
    """(via, token). via names the header the token came in, or 'none'."""
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return "authorization", auth[7:].strip()
    if headers.get("x-api-key"):
        return "x-api-key", headers["x-api-key"].strip()
    if headers.get("x-armoriq-mcp-auth"):
        return "x-armoriq-mcp-auth", _from_armoriq_header(headers["x-armoriq-mcp-auth"])
    return "none", ""


class RequireToken:
    """ASGI wrapper: 401 unless the request carries the expected token (mode enforce)."""

    def __init__(self, app, token: str | None, mode: str = "enforce", label: str = "mcp"):
        self.app, self.token, self.label = app, token or None, label
        self.mode = "observe" if mode == "observe" else "enforce"

    async def __call__(self, scope, receive, send):
        if self.token is None or scope["type"] != "http":
            return await self.app(scope, receive, send)
        via, presented = _presented(scope)
        ok = bool(presented) and hmac.compare_digest(presented.encode(), self.token.encode())
        LAST_SEEN[self.label] = {"via": via, "match": ok, "mode": self.mode, "at": round(time.time())}
        if ok or self.mode == "observe":
            return await self.app(scope, receive, send)
        body = json.dumps({"error": "unauthorized", "hint": "MCP token required"}).encode()
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"www-authenticate", b"Bearer")]})
        await send({"type": "http.response.body", "body": body})
