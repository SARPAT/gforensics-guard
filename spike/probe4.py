"""CP0 spike, round 4: agent/user attribution. RUN ON YOUR LAPTOP.

    python spike/probe4.py --email YOU@EXAMPLE.COM

Platform showed every SDK call as actor "__sdk_multiuser__", agent "unresolved", so
agent-scoped controls (kill switch, per-agent policy) never matched. The SDK 0.6.10
says identity is resolved "from the API key + email" (bootstrap / for_user). This
prints what ArmorIQ resolves, then repeats the read calls through the email path.
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
from hello import TELEMETRY, verdict  # noqa: E402

from armoriq_sdk import ArmorIQClient  # noqa: E402

load_dotenv()

SECRETISH = ("key", "secret", "token", "password", "private")


def redact(obj):
    """Mask anything key-like before printing."""
    if isinstance(obj, dict):
        return {k: ("<redacted>" if any(s in k.lower() for s in SECRETISH) and k.lower() != "toolmap"
                    else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str) and obj.startswith(("ak_live_", "ak_test_", "ak_claw_")):
        return "<redacted>"
    return obj


def dump(label, data):
    print(f"  {label}: {json.dumps(redact(data), indent=1, default=str)[:2500]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True, help="your platform.armoriq.ai login email")
    args = ap.parse_args()

    client = ArmorIQClient(agent_id=os.getenv("ARMORIQ_AGENT_ID") or None)
    print(f"[0] client user_id={client.user_id} agent_id={client.agent_id}")

    print("[1] bootstrap (what the API key resolves to)")
    try:
        dump("bootstrap", client.bootstrap())
    except Exception as e:  # noqa: BLE001
        print(f"  bootstrap ERROR {type(e).__name__}: {e}")

    print("[2] resolve_user(email)")
    try:
        dump("resolve_user", client.resolve_user(args.email))
    except Exception as e:  # noqa: BLE001
        print(f"  resolve_user ERROR {type(e).__name__}: {e}")

    print("[3] token + invoke through the email path (for_user)")
    client.user_email_override = args.email.strip().lower()  # what for_user().start_session() sets
    step = {"action": "read_status", "tool": "read_status", "mcp": TELEMETRY,
            "params": {"node": "node-07"}}
    cap = client.capture_plan(llm="none-cp0-spike", prompt="read",
                              plan={"goal": "read", "steps": [step]})
    tok = client.get_intent_token(cap, validity_seconds=600)
    rt = tok.raw_token
    print(f"  token user_id={rt.get('user_id')} agent_id={rt.get('agent_id')} "
          f"allowed_tools={rt.get('allowed_tools')!r} keys={sorted(rt)}")
    em = client.user_email_override
    verdict("email A read_status(node-07)", lambda: client.invoke(TELEMETRY, "read_status", tok, {"node": "node-07"}, user_email=em))
    verdict("email B read_status(node-99)", lambda: client.invoke(TELEMETRY, "read_status", tok, {"node": "node-99"}, user_email=em))


if __name__ == "__main__":
    main()
