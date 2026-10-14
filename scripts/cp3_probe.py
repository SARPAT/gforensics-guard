"""CP3 live probes T6/T7. RUN ON THE LAPTOP (keys stay in .env).

    python scripts/cp3_probe.py --email YOU@EXAMPLE.COM

T6  Does the PROXY confine a delegate_subtree() token to its subtree?
    Parent plan = [get_xid_events, drain_node]. Child token = subtree /steps/[0] (the read).
    The child then calls drain_node, which IS in the parent plan, so the SDK's local
    plan check passes and only the proxy can refuse it.
T7  Does a held call turn into an approvable delegation request?
    drain_node (policy: hold) -> create_delegation_request -> poll status for a while.
    While it polls, look at platform.armoriq.ai for a pending approval. Do NOT approve
    anything here unless you mean to: the request is for the simulated fleet only.

Verdict labels: SERVER = ArmorIQ answered; SDK-LOCAL = the SDK refused before any
network call. Nothing here decides allow/block.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotenv import load_dotenv

from armoriq_sdk import (ArmorIQClient, ArmorIQException, IntentMismatchException,
                         PolicyBlockedException, PolicyHoldException)
from armoriq_sdk.models import DelegationRequestParams

load_dotenv()
BASE = os.getenv("GFG_MCP_BASE_URL", "https://gforensics-guard.onrender.com").rstrip("/")
TELEMETRY = os.getenv("GFG_TELEMETRY_MCP", "gfg-telemetry")
OPS = os.getenv("GFG_OPS_MCP", "gfg-ops")


def pubkey_hex() -> str:
    raw = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return raw.hex()


def verdict(label: str, call):
    try:
        res = call()
        print(f"  {label}: ALLOWED  [SERVER] status={res.status} result={str(res.result)[:120]}")
    except IntentMismatchException as e:
        print(f"  {label}: BLOCKED  [SDK-LOCAL] {e}")
    except PolicyHoldException as e:
        print(f"  {label}: HELD     [SERVER] {e} meta={e.metadata} ctx={e.delegation_context}")
        return e
    except PolicyBlockedException as e:
        print(f"  {label}: BLOCKED  [SERVER] {e} reason={e.reason}")
    except ArmorIQException as e:
        print(f"  {label}: ERROR    {type(e).__name__}: {e}")
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True, help="requester email (your platform login)")
    ap.add_argument("--poll", type=int, default=90, help="seconds to poll the approval status")
    args = ap.parse_args()
    if not os.getenv("ARMORIQ_API_KEY"):
        sys.exit("ARMORIQ_API_KEY is not set (put it in .env). The key is never printed.")
    email = args.email.strip().lower()

    t = time.time()
    print(f"[0] /health {httpx.get(f'{BASE}/health', timeout=120).status_code} in {time.time() - t:.1f}s")

    client = ArmorIQClient(agent_id=os.getenv("ARMORIQ_AGENT_ID") or None)
    client.user_email_override = email
    plan = {"goal": "CP3 probe: read node-07 Xid events, drain node-07",
            "steps": [
                {"action": "get_xid_events", "tool": "get_xid_events", "mcp": TELEMETRY,
                 "params": {"node": "node-07"}},
                {"action": "drain_node", "tool": "drain_node", "mcp": OPS,
                 "params": {"node": "node-07"}},
            ]}
    cap = client.capture_plan(llm="none-cp3-probe", prompt=plan["goal"], plan=plan)
    tok = client.get_intent_token(cap, validity_seconds=600)
    print(f"[1] parent token plan_id={tok.plan_id} hash={tok.plan_hash[:16]}… "
          f"allowed_tools={(tok.policy_validation or {}).get('allowed_tools')}")

    print("[2] T6 delegate_subtree(/steps/[0]) then call inside + outside the subtree")
    child = None
    for path in ("/steps/[0]", "/steps/0"):
        try:
            d = client.delegate_subtree(tok, delegate_public_key=pubkey_hex(), subtree_path=path,
                                        validity_seconds=300, parent_plan=plan)
            print(f"  subtree {path}: trust_id={d['trust_id']} subtree_root={str(d['subtree_root'])[:16]}… "
                  f"proof_len={len(d['inclusion_proof'])}")
            child = d["delegated_token"]
            break
        except ArmorIQException as e:
            print(f"  subtree {path}: ERROR {type(e).__name__}: {str(e)[:300]}")
    if child:
        verdict("child  get_xid_events (inside) ", lambda: client.invoke(TELEMETRY, "get_xid_events", child, {"node": "node-07"}, user_email=email))
        verdict("child  drain_node     (OUTSIDE)", lambda: client.invoke(OPS, "drain_node", child, {"node": "node-07"}, user_email=email))
    hold = verdict("parent drain_node     (control)", lambda: client.invoke(OPS, "drain_node", tok, {"node": "node-07"}, user_email=email))

    print("[3] T7 delegation request for the held drain_node")
    if not hold:
        print("  parent drain_node was not HELD, so there is nothing to request. Skipping T7.")
        return
    ctx = hold.delegation_context or {}
    try:
        req = client.create_delegation_request(DelegationRequestParams(
            tool="drain_node", action="drain_node", arguments={"node": "node-07"}, amount=8,
            requester_email=email, requester_role="agent_user", requester_limit=0,
            domain=ctx.get("domain", OPS), target_url=ctx.get("targetUrl"),
            plan_id=ctx.get("planId") or tok.plan_id,
            intent_reference=ctx.get("intentReference") or tok.token_id,
            merkle_root=ctx.get("merkleRoot") or tok.plan_hash,
            reason=f"CP3 probe: {hold}"))
    except ArmorIQException as e:
        print(f"  create_delegation_request ERROR {type(e).__name__}: {e}")
        return
    print(f"  delegation_id={req.delegation_id} status={req.status} expires={req.expires_at}")
    print(f"  polling {args.poll}s. Look for it on platform.armoriq.ai now.")
    end, last = time.time() + args.poll, None
    while time.time() < end:
        try:
            st = client.get_delegation_status(req.delegation_id)
        except ArmorIQException as e:
            st = f"ERROR {e}"
        if st != last:
            print(f"  t+{int(args.poll - (end - time.time()))}s status={st}")
            last = st
        if st in ("approved", "rejected", "expired"):
            break
        time.sleep(5)
    if last == "approved":
        verdict("parent drain_node after approval",
                lambda: client.invoke(OPS, "drain_node", tok,
                                      {"node": "node-07", "_delegation_id": req.delegation_id},
                                      user_email=email))
    state = httpx.get(f"{BASE}/sim/state", timeout=60).json()
    print(f"[4] reached the fleet: {[(e['tool'], e['caller']) for e in state['audit_log']]}")


if __name__ == "__main__":
    main()
