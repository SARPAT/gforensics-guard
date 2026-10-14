"""CP2 MCP servers over the CP1 simulator (one process, shared fleet).

    servers/telemetry.py  gfg-telemetry  READ tools   -> sim.telemetry
    servers/fleet_ops.py  gfg-ops        WRITE tools  -> sim.ops
    servers/app.py        FastAPI app mounting both + /health + /sim admin

Named `servers`, not `mcp`, so it never shadows the MCP SDK package.
"""
