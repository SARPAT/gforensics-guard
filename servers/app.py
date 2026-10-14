"""CP2: ONE FastAPI service (one Render web service) hosting both MCP servers.

    GET  /health                         liveness + cold-start probe
    POST /telemetry/mcp  (+ /telemetry/sse)   gfg-telemetry  READ  tools
    POST /ops/mcp        (+ /ops/sse)         gfg-ops        WRITE tools
    GET  /sim/state                      fleet summary + audit log + RMA tickets
    POST /sim/reset                      healthy seeded baseline
    POST /sim/scene/{scene}              inject a demo scene (split_screen, rma_trap, ...)

Same paths as the CP0 dummy, so the platform registrations (gfg-telemetry,
gfg-ops) keep pointing at the right place. Both servers share one in-memory
fleet (sim.get_fleet()); a Render restart resets it to the seeded baseline.

/sim/* is demo plumbing for us, NOT an MCP tool, so no agent can inject
faults or reset the fleet. Guarded by GFG_ADMIN_TOKEN when set.
"""
from __future__ import annotations

import contextlib
import hmac
import logging
import os
from dataclasses import asdict

from fastapi import FastAPI, Header, HTTPException
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette

import sim
from sim import faults, telemetry
from servers import fleet_ops, telemetry as telemetry_mcp
from servers.auth import RequireToken

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

MCP_TOKEN = os.getenv("GFG_MCP_TOKEN") or None
ADMIN_TOKEN = os.getenv("GFG_ADMIN_TOKEN") or None


def both_transports(server: FastMCP) -> Starlette:
    """Streamable HTTP (/mcp, what the proxy uses) + SSE (/sse) under one mount prefix."""
    stream, sse = server.streamable_http_app(), server.sse_app()
    return Starlette(routes=[*stream.routes, *sse.routes])


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async with telemetry_mcp.server.session_manager.run(), fleet_ops.server.session_manager.run():
        yield


app = FastAPI(title="gforensics-guard MCP servers (CP2)", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "servers": [telemetry_mcp.NAME, fleet_ops.NAME],
            "mcp_auth": MCP_TOKEN is not None, "simulated": True}


def _admin(token: str | None) -> None:
    if ADMIN_TOKEN and not hmac.compare_digest((token or "").encode(), ADMIN_TOKEN.encode()):
        raise HTTPException(401, "admin token required (X-Admin-Token)")


@app.get("/sim/state")
def sim_state() -> dict:
    f = sim.get_fleet()
    return {"summary": telemetry.fleet_summary(f),
            "audit_log": [asdict(e) for e in f.audit_log],
            "rma_tickets": [asdict(t) for t in f.rma_tickets]}


@app.post("/sim/reset")
def sim_reset(x_admin_token: str | None = Header(default=None)) -> dict:
    _admin(x_admin_token)
    return telemetry.fleet_summary(sim.reset_fleet())


@app.post("/sim/scene/{scene}")
def sim_scene(scene: str, reset: bool = True, x_admin_token: str | None = Header(default=None)) -> dict:
    _admin(x_admin_token)
    f = sim.reset_fleet() if reset else sim.get_fleet()
    try:
        injected = faults.inject_scene(f, scene)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
    return {"scene": scene, "injected": injected, "summary": telemetry.fleet_summary(f)}


app.mount("/telemetry", RequireToken(both_transports(telemetry_mcp.server), MCP_TOKEN))
app.mount("/ops", RequireToken(both_transports(fleet_ops.server), MCP_TOKEN))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
