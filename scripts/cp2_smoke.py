"""CP2 smoke test. RUN ON THE LAPTOP (keys stay in .env, cloud sandbox can't reach Render).

    python scripts/cp2_smoke.py

[0] wake Render  [1] list tools straight from both public MCP servers
[2] load the split_screen scene  [3] three calls THROUGH ArmorIQ, verdicts printed as returned
[4] the server's audit log = what actually reached the simulated fleet.

Verdict labels: SERVER = ArmorIQ proxy/backend answered; SDK-LOCAL = the SDK
refused before any network call (not an ArmorIQ verdict). Nothing here decides
allow/block: the verdicts depend only on the policies active on the platform.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from armoriq_sdk import (ArmorIQClient, ArmorIQException, IntentMismatchException,
                         MCPInvocationException, PolicyBlockedException, PolicyHoldException)

load_dotenv()
BASE = os.getenv("GFG_MCP_BASE_URL", "https://gforensics-guard.onrender.com").rstrip("/")
TELEMETRY = os.getenv("GFG_TELEMETRY_MCP", "gfg-telemetry")
OPS = os.getenv("GFG_OPS_MCP", "gfg-ops")
MCP_TOKEN = os.getenv("GFG_MCP_TOKEN")
ADMIN = {"X-Admin-Token": os.environ["GFG_ADMIN_TOKEN"]} if os.getenv("GFG_ADMIN_TOKEN") else {}
RACK_B = [f"node-{i:02d}" for i in range(9, 17)]


async def list_tools(path: str) -> list[str]:
    headers = {"Authorization": f"Bearer {MCP_TOKEN}"} if MCP_TOKEN else {}
    async with streamablehttp_client(f"{BASE}/{path}/mcp", headers=headers, timeout=60) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return [t.name for t in (await s.list_tools()).tools]


def verdict(label: str, call) -> None:
    try:
        res = call()
        print(f"  {label}: ALLOWED  [SERVER] status={res.status} result={str(res.result)[:160]}")
    except IntentMismatchException as e:
        print(f"  {label}: BLOCKED  [SDK-LOCAL] IntentMismatch: {e}")
    except PolicyHoldException as e:
        print(f"  {label}: HELD     [SERVER] {e}")
    except PolicyBlockedException as e:
        print(f"  {label}: BLOCKED  [SERVER] {e} reason={getattr(e, 'reason', None)}")
    except (MCPInvocationException, ArmorIQException) as e:
        print(f"  {label}: ERROR    {type(e).__name__}: {e}")


def main() -> None:
    if not os.getenv("ARMORIQ_API_KEY"):
        sys.exit("ARMORIQ_API_KEY is not set (put it in .env). The key is never printed.")

    t = time.time()
    r = httpx.get(f"{BASE}/health", timeout=120)
    print(f"[0] /health {r.status_code} in {time.time() - t:.1f}s  {r.json()}")

    for path in ("telemetry", "ops"):
        print(f"[1] {path}/mcp tools: {asyncio.run(list_tools(path))}")

    s = httpx.post(f"{BASE}/sim/scene/split_screen", headers=ADMIN, timeout=60).json()
    print(f"[2] scene split_screen: {[f['fault'] for f in s['injected']]}")

    client = ArmorIQClient()
    plan = {"goal": "CP2 smoke: read node-07 Xid events, drain node-07, bulk-drain rack B",
            "steps": [
                {"action": "get_xid_events", "tool": "get_xid_events", "mcp": TELEMETRY,
                 "params": {"node": "node-07"}},
                {"action": "drain_node", "tool": "drain_node", "mcp": OPS,
                 "params": {"node": "node-07"}},
                {"action": "drain_nodes", "tool": "drain_nodes", "mcp": OPS,
                 "params": {"nodes": RACK_B}},
            ]}
    capture = client.capture_plan(llm="none-cp2-smoke", prompt=plan["goal"], plan=plan)
    token = client.get_intent_token(capture, validity_seconds=600)
    print(f"[3] intent token plan_hash={token.plan_hash[:16]}… "
          f"allowed_tools={(token.policy_validation or {}).get('allowed_tools')}")
    verdict("get_xid_events(node-07)", lambda: client.invoke(TELEMETRY, "get_xid_events", token, {"node": "node-07"}))
    verdict("drain_node(node-07)    ", lambda: client.invoke(OPS, "drain_node", token, {"node": "node-07"}))
    verdict("drain_nodes(rack B)    ", lambda: client.invoke(OPS, "drain_nodes", token, {"nodes": RACK_B}))

    state = httpx.get(f"{BASE}/sim/state", timeout=60).json()
    print(f"[4] reached the fleet: {[(e['tool'], e['caller']) for e in state['audit_log']]} "
          f"gpus_out_of_service={state['summary']['gpus_out_of_service']}")


if __name__ == "__main__":
    main()
