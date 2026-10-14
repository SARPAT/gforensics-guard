"""CP4: create the shared MCP token and write it into .env. Prints NO secret.

    python scripts/gen_mcp_token.py

Writes (or replaces) in .env:
    GFG_MCP_TOKEN=<new>                        -> copy this value into Render (env var GFG_MCP_TOKEN)
    ARMORIQ_MCP_GFG_TELEMETRY_AUTH_TYPE=bearer -> armoriq-sdk sends the token to the ArmorIQ proxy,
    ARMORIQ_MCP_GFG_TELEMETRY_TOKEN=<same>        which injects "Authorization: Bearer" upstream
    ARMORIQ_MCP_GFG_OPS_AUTH_TYPE=bearer
    ARMORIQ_MCP_GFG_OPS_TOKEN=<same>
"""
from __future__ import annotations

import pathlib
import secrets

env = pathlib.Path(__file__).resolve().parents[1] / ".env"
token = secrets.token_urlsafe(32)
new = {"GFG_MCP_TOKEN": token,
       "ARMORIQ_MCP_GFG_TELEMETRY_AUTH_TYPE": "bearer", "ARMORIQ_MCP_GFG_TELEMETRY_TOKEN": token,
       "ARMORIQ_MCP_GFG_OPS_AUTH_TYPE": "bearer", "ARMORIQ_MCP_GFG_OPS_TOKEN": token}
lines = [ln for ln in (env.read_text().splitlines() if env.exists() else [])
         if ln.split("=", 1)[0].strip() not in new]
env.write_text("\n".join(lines + [f"{k}={v}" for k, v in new.items()]) + "\n")
print(f"wrote {len(new)} keys to {env} (values not shown). Next: copy GFG_MCP_TOKEN from .env into Render.")
