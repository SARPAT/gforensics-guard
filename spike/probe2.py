"""CP0 spike, round 2. RUN ON YOUR LAPTOP (keys stay in .env).

    python spike/probe2.py            # E2 (forged plan vs a policy-ALLOWED tool) + T4 read-only
    python spike/probe2.py --kill     # also T4 live: kill the spike agent 60s, invoke, unkill

E2: a token signed for a plan containing ONLY drain_node; the client edits its copy to say
read_status and calls read_status (policy = Allow, so no hold can mask the result).
    BLOCKED [SERVER] -> the proxy verifies the plan even when allowed_tools=[]
    ALLOWED          -> with policies active, plan integrity is NOT checked (gap)
"""

import argparse
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
from hello import OPS, TELEMETRY, verdict  # noqa: E402  (reuse the verdict labels)

from armoriq_sdk import ArmorIQClient  # noqa: E402

load_dotenv()


def show(label, r):
    """Status + a short body. Bodies here are kill lists/errors, never key material."""
    print(f"  {label}: HTTP {r.status_code} {r.text[:300]!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kill", action="store_true", help="kill the spike agent for 60s, invoke, unkill")
    args = ap.parse_args()
    if not os.getenv("ARMORIQ_API_KEY"):
        sys.exit("ARMORIQ_API_KEY is not set (put it in .env). The key is never printed.")

    client = ArmorIQClient(agent_id=os.getenv("ARMORIQ_AGENT_ID") or None)
    print(f"[0] agent_id={client.agent_id} backend={client.backend_endpoint} proxy={client.default_proxy_endpoint}")

    drain_step = {"action": "drain_node", "tool": "drain_node", "mcp": OPS,
                  "params": {"node": "node-07", "gpus": 8}}
    read_step = {"action": "read_status", "tool": "read_status", "mcp": TELEMETRY,
                 "params": {"node": "node-07"}}

    print("[1] E2 forged plan vs a policy-allowed tool")
    cap = client.capture_plan(llm="none-cp0-spike", prompt="drain only",
                              plan={"goal": "drain only", "steps": [drain_step]})
    tok = client.get_intent_token(cap, validity_seconds=600)
    print(f"  drain-only token: allowed_tools={tok.raw_token.get('allowed_tools', tok.raw_token.get('policy', {}))!r:.200}")
    forged = tok.model_copy(deep=True)
    forged.raw_token["plan"]["steps"][0]["action"] = "read_status"
    forged.raw_token["plan"]["steps"][0]["tool"] = "read_status"
    forged.raw_token["plan"]["steps"][0]["mcp"] = TELEMETRY
    verdict("E2 forged drain-only → read_status ",
            lambda: client.invoke(TELEMETRY, "read_status", forged, {"node": "node-07"}))

    # Control: an honest read token still works (so a block above is about the forgery).
    rcap = client.capture_plan(llm="none-cp0-spike", prompt="read",
                               plan={"goal": "read", "steps": [read_step]})
    rtok = client.get_intent_token(rcap, validity_seconds=600)
    verdict("E2c honest read_status (control)   ",
            lambda: client.invoke(TELEMETRY, "read_status", rtok, {"node": "node-07"}))

    print("[2] T4 kill-switch API auth (read-only)")
    be = client.backend_endpoint
    key = os.environ["ARMORIQ_API_KEY"]
    show("GET agent-kills  Bearer   ", client.http_client.get(f"{be}/iap/agent-kills"))
    show("GET agent-kills  X-API-Key", client.http_client.get(
        f"{be}/iap/agent-kills", headers={"Authorization": "", "X-API-Key": key}))

    if args.kill:
        aid = client.agent_id
        print(f"[3] T4 live: kill {aid} for 60s")
        show("POST kill  ", client.http_client.post(
            f"{be}/iap/agent/{aid}/kill", json={"durationSeconds": 60, "reason": "cp0 spike T4"}))
        verdict("T4 read_status while killed        ",
                lambda: client.invoke(TELEMETRY, "read_status", rtok, {"node": "node-07"}))
        show("POST unkill", client.http_client.post(f"{be}/iap/agent/{aid}/unkill", json={}))
        verdict("T4 read_status after unkill        ",
                lambda: client.invoke(TELEMETRY, "read_status", rtok, {"node": "node-07"}))


if __name__ == "__main__":
    main()
