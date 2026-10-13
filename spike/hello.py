"""CP0 spike: ArmorIQ hello-world + tests T1/T3/T4. RUN ON YOUR LAPTOP (keys stay in .env).

    python spike/hello.py                  # tests A-D
    python spike/hello.py --tamper         # also test E (does the PROXY catch a lying client?)

Every verdict line says where it came from:
    SERVER    = the ArmorIQ proxy/backend answered (a real enforcement verdict)
    SDK-LOCAL = the Python SDK refused before any network call (not server enforcement)
"""

import argparse
import os
import sys
import time

import httpx
from dotenv import load_dotenv

from armoriq_sdk import (
    ArmorIQClient,
    ArmorIQException,
    IntentMismatchException,
    MCPInvocationException,
    PolicyBlockedException,
    PolicyHoldException,
)

load_dotenv()

BASE_URL = os.getenv("GFG_MCP_BASE_URL", "").rstrip("/")
TELEMETRY = os.getenv("GFG_TELEMETRY_MCP", "gfg-telemetry")  # name as registered on the platform
OPS = os.getenv("GFG_OPS_MCP", "gfg-ops")


def verdict(test: str, call):
    """Run one invoke and print ALLOWED / HELD / BLOCKED with its source."""
    try:
        res = call()
        print(f"  {test}: ALLOWED   [SERVER] status={res.status} result={res.result}")
    except IntentMismatchException as e:
        print(f"  {test}: BLOCKED   [SDK-LOCAL] IntentMismatch: {e}")
    except PolicyHoldException as e:
        print(f"  {test}: HELD      [SERVER] {e} metadata={e.metadata}")
    except PolicyBlockedException as e:
        print(f"  {test}: BLOCKED   [SERVER] {e} reason={e.reason}")
    except MCPInvocationException as e:
        print(f"  {test}: ERROR     [SERVER/SDK] {type(e).__name__}: {e}")
    except ArmorIQException as e:
        print(f"  {test}: ERROR     {type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tamper", action="store_true", help="also run test E")
    args = ap.parse_args()

    if not os.getenv("ARMORIQ_API_KEY"):
        sys.exit("ARMORIQ_API_KEY is not set (put it in .env). The key is never printed.")

    # 0. Wake the Render service first: free tier cold start can outlast the proxy timeout.
    if BASE_URL:
        t = time.time()
        r = httpx.get(f"{BASE_URL}/health", timeout=120)
        print(f"[0] /health {r.status_code} in {time.time() - t:.1f}s (cold start if >10s)")

    client = ArmorIQClient(agent_id=os.getenv("ARMORIQ_AGENT_ID") or None)

    # 1. Commit a 2-step plan. capture_plan is local; get_intent_token is the server call.
    plan = {
        "goal": "CP0 spike: read node-07 status, then drain node-07",
        "steps": [
            {"action": "read_status", "tool": "read_status", "mcp": TELEMETRY,
             "params": {"node": "node-07"}},
            {"action": "drain_node", "tool": "drain_node", "mcp": OPS,
             "params": {"node": "node-07", "gpus": 8}},
        ],
    }
    capture = client.capture_plan(llm="none-cp0-spike", prompt=plan["goal"], plan=plan)
    token = client.get_intent_token(capture, validity_seconds=600)  # default is only 60s
    allowed = (token.policy_validation or {}).get("allowed_tools")
    print(f"[1] intent token: plan_hash={token.plan_hash[:16]}… steps={token.total_steps} "
          f"proofs={len(token.step_proofs)} ttl={token.expires_at - time.time():.0f}s "
          f"allowed_tools={allowed}")

    print("[2] tests")
    verdict("A in-plan read_status(node-07)          ",
            lambda: client.invoke(TELEMETRY, "read_status", token, {"node": "node-07"}))
    verdict("B T1 read_status(node-99) params≠plan    ",
            lambda: client.invoke(TELEMETRY, "read_status", token, {"node": "node-99"}))
    verdict("C out-of-plan reset_gpu                  ",
            lambda: client.invoke(OPS, "reset_gpu", token, {"node": "node-07"}))
    verdict("D T3 drain_node(node-07, gpus=64)        ",
            lambda: client.invoke(OPS, "drain_node", token, {"node": "node-07", "gpus": 64}))

    if args.tamper:
        # E: a token for a plan with ONLY read_status, then the client edits its
        # local copy of the plan to claim step 0 is drain_node. The SDK lets it
        # through; only the proxy (Merkle proof vs signed plan_hash) can stop it.
        read_only = client.capture_plan(
            llm="none-cp0-spike", prompt="read only",
            plan={"goal": "read only", "steps": [plan["steps"][0]]})
        ro_token = client.get_intent_token(read_only, validity_seconds=600)
        forged = ro_token.model_copy(deep=True)
        forged.raw_token["plan"]["steps"][0]["action"] = "drain_node"
        verdict("E tampered plan → drain_node             ",
                lambda: client.invoke(OPS, "drain_node", forged, {"node": "node-07", "gpus": 8}))

    print("[3] check the dummy server's Render log for 'TOOL CALL' lines: they show what "
          "actually reached the MCP server, with params.")


if __name__ == "__main__":
    main()
