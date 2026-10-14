"""Pieces both MCP servers share: FastMCP factory, caller attribution, call log."""
from __future__ import annotations

import logging

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

log = logging.getLogger("gfg.mcp")

# Render serves us behind its own hostname, so the SDK's localhost-only
# DNS-rebinding guard would reject every request. Who may call is handled by
# the optional bearer check in servers/auth.py instead.
NO_HOST_CHECK = TransportSecuritySettings(enable_dns_rebinding_protection=False)

# Header names (never values) seen per server, logged once, so we learn what
# the ArmorIQ proxy forwards without leaking anything it sends.
_seen_header_sets: set[tuple[str, frozenset[str]]] = set()


def make_server(name: str, instructions: str) -> FastMCP:
    # Stateless + JSON: each POST stands alone, which is what the ArmorIQ
    # proxy did successfully against the CP0 dummy servers.
    return FastMCP(name, instructions=instructions, stateless_http=True,
                   json_response=True, transport_security=NO_HOST_CHECK)


def caller_of(ctx: Context | None, server: str) -> str:
    """Best-effort label for the audit log. Attribution only, never authorization.

    `X-GFG-Caller` lets our orchestrator name the role (diagnoser, remediator,
    naive-agent); otherwise the call is labelled with the server it came in on.
    """
    request = getattr(getattr(ctx, "request_context", None), "request", None) if ctx else None
    if request is None:
        return f"{server}:local"
    names = frozenset(k.lower() for k in request.headers.keys())
    if (server, names) not in _seen_header_sets:
        _seen_header_sets.add((server, names))
        log.info("HEADERS %s %s", server, sorted(names))
    return request.headers.get("x-gfg-caller") or f"{server}:remote"


def log_call(server: str, tool: str, caller: str, **params) -> None:
    log.info("TOOL CALL %s.%s caller=%s params=%s", server, tool, caller, params)
