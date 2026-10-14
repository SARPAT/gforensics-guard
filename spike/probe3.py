"""CP0 spike, round 3: same two calls, run after each platform change. RUN ON YOUR LAPTOP.

    python spike/probe3.py LABEL     # LABEL = what's active right now (kill / rego / paramlimit)

    A read_status(node-07)  expected ALLOWED unless the agent is killed
    B read_status(node-99)  BLOCKED [SERVER] under rego/paramlimit = ArmorIQ sees params
"""

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
from hello import TELEMETRY, verdict  # noqa: E402

from armoriq_sdk import ArmorIQClient  # noqa: E402

load_dotenv()


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "?"
    client = ArmorIQClient(agent_id=os.getenv("ARMORIQ_AGENT_ID") or None)
    step = {"action": "read_status", "tool": "read_status", "mcp": TELEMETRY,
            "params": {"node": "node-07"}}
    cap = client.capture_plan(llm="none-cp0-spike", prompt="read",
                              plan={"goal": "read", "steps": [step]})
    tok = client.get_intent_token(cap, validity_seconds=600)
    print(f"[{label}] token allowed_tools={tok.raw_token.get('allowed_tools')!r}")
    verdict(f"{label:10} A read_status(node-07)", lambda: client.invoke(TELEMETRY, "read_status", tok, {"node": "node-07"}))
    verdict(f"{label:10} B read_status(node-99)", lambda: client.invoke(TELEMETRY, "read_status", tok, {"node": "node-99"}))


if __name__ == "__main__":
    main()
