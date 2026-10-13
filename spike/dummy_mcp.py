"""CP0 spike: two dummy MCP servers in ONE FastAPI app (one Render web service).

    /health                      liveness + cold-start probe
    /telemetry/mcp  /telemetry/sse   gfg-telemetry  (READ)   read_status(node)
    /ops/mcp        /ops/sse         gfg-ops        (WRITE)  drain_node(node, gpus)

Both transports are exposed because we have not verified which one the
ArmorIQ proxy speaks (streamable HTTP vs SSE). Every tool call is logged with
its arguments so we can see exactly what the proxy forwards (T1).
Nothing here touches a real system: the tools only return canned JSON.
"""

import contextlib
import logging
import os

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

log = logging.getLogger("dummy_mcp")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

# Render serves us behind its own hostname, so the SDK's localhost-only
# DNS-rebinding guard would reject every request. Dummy data only, so it is off.
NO_HOST_CHECK = TransportSecuritySettings(enable_dns_rebinding_protection=False)

telemetry = FastMCP("gfg-telemetry", stateless_http=True, json_response=True,
                    transport_security=NO_HOST_CHECK)
ops = FastMCP("gfg-ops", stateless_http=True, json_response=True,
              transport_security=NO_HOST_CHECK)


@telemetry.tool()
def read_status(node: str) -> dict:
    """Read the (simulated) health status of a GPU node."""
    log.info("TOOL CALL gfg-telemetry.read_status node=%r", node)
    return {"node": node, "gpus": 8, "xid_last": 79, "simulated": True}


@ops.tool()
def drain_node(node: str, gpus: int = 8) -> dict:
    """Drain a (simulated) GPU node. Writes nothing real."""
    log.info("TOOL CALL gfg-ops.drain_node node=%r gpus=%r", node, gpus)
    return {"drained": node, "gpus_affected": gpus, "simulated": True}


def both_transports(server: FastMCP) -> Starlette:
    """One sub-app per server serving /mcp and /sse under its mount prefix."""
    stream = server.streamable_http_app()
    sse = server.sse_app()  # Mount adds the prefix via root_path
    return Starlette(routes=[*stream.routes, *sse.routes])


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Streamable HTTP needs each server's session manager running.
    async with telemetry.session_manager.run(), ops.session_manager.run():
        yield


app = FastAPI(title="gforensics-guard CP0 dummy MCP", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "servers": ["gfg-telemetry", "gfg-ops"]}


app.mount("/telemetry", both_transports(telemetry))
app.mount("/ops", both_transports(ops))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
