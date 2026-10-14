"""CP4: run the demo scenes end to end. RUN ON THE LAPTOP (keys in .env).

    python scripts/demo.py --warm                                  # wake Render, check MCP auth, exit
    python scripts/demo.py --scene split_screen --email YOU        # governed run
    python scripts/demo.py --scene split_screen --email YOU --compromised
    python scripts/demo.py --scene rma_trap --email YOU [--compromised]
    python scripts/demo.py --scene xid79 --email YOU --approval-wait 600
    python scripts/demo.py --scene all --email YOU                 # split_screen, rma_trap, xid79 (+compromised)

Every ALLOWED / HELD / BLOCKED printed is what ArmorIQ returned [SERVER] or what the SDK
refused before any network call [SDK-LOCAL]. Nothing in this script decides a verdict.
Lineage per run: runs/<time>-<scene>[-compromised].json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from collections import Counter

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agents.armoriq_io import ArmorIQGateway  # noqa: E402
from agents.orchestrator import Incident, run  # noqa: E402
from agents.scenes import DEMO_ORDER, SCENES  # noqa: E402

load_dotenv()
BASE = os.getenv("GFG_MCP_BASE_URL", "https://gforensics-guard.onrender.com").rstrip("/")
ADMIN = {"X-Admin-Token": os.environ["GFG_ADMIN_TOKEN"]} if os.getenv("GFG_ADMIN_TOKEN") else {}

TTY = sys.stdout.isatty() and not os.getenv("NO_COLOR")
COLOR = {"ALLOWED": "32", "HELD": "33", "BLOCKED": "31", "ERROR": "35"}
MARK = {"ALLOWED": "✅", "HELD": "⚠ ", "BLOCKED": "✗ ", "ERROR": "!!"}


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def show(e: dict) -> None:
    k, t = e["kind"], f"[{e['t']:6.1f}s]"
    if k == "objective":
        print(f"OBJECTIVE  {e['text']}  nodes={e['nodes']}")
    elif k == "plan":
        print(f"\n{t} {c('PLAN ' + e['phase'].upper(), '1')} committed  hash={e['plan_hash'][:16]}…"
              f"  parent={str(e['parent_plan_hash'])[:16]}")
        print(f"          steps={[s['tool'] for s in e['steps']]}")
    elif k == "delegation":
        print(f"{t} DELEGATE -> {e['agent']}  " + (f"trust_id={e['trust_id']}" if e["ok"]
                                                   else c(f"FAILED {e['error']}", "31")))
    elif k == "injection":
        print(f"{t} {c('INJECTED INTO WRITE PLAN', '35')} {e['tool']}({json.dumps(e['params'])[:70]})"
              f"  <- {e['why']}")
    elif k == "call":
        d = e["decision"]
        extra = f"  (LLM: {e['because'][:50]})" if e.get("because") else ""
        print(f"{t} {MARK[d]} {c(f'{d:7}', COLOR[d])} [{e['source']}] {e['actor']:24} "
              f"{e['tool']}({json.dumps(e['params'])[:60]}){extra}")
        if d != "ALLOWED" and e["reason"]:
            print(f"          reason: {e['reason'][:180]}")
    elif k == "diagnosis":
        print(f"\n{t} {c('DIAGNOSIS', '1')} {e['node']}: {e['category']}")
        for f in e["findings"]:
            print(f"          rule {f['rule']}: {f['summary']}")
        print(f"          proposed by rule table: {[(s['tool'], s['params']) for s in e['proposed_steps']] or 'nothing'}")
    elif k == "narrative":
        print(f"{t} NARRATIVE ({e['llm']}): {(e['narrative'] or '')[:300]}")
        print(f"          LLM suggests: {[(a['tool'], a['params']) for a in e['suggested_actions']]}")
    elif k == "approval":
        print(f"{t} APPROVAL {e.get('tool', '')} request={e.get('delegation_id')} status={e['status']}"
              + (f" (waiting up to {e['wait_s']}s for a second member in the ArmorIQ console)"
                 if e.get("wait_s") else ""))


def warm() -> None:
    t = time.time()
    r = httpx.get(f"{BASE}/health", timeout=120)
    print(f"[warm] /health {r.status_code} in {time.time() - t:.1f}s")
    # Is the MCP endpoint closed to direct callers (GFG_MCP_TOKEN on Render)?
    probe = httpx.post(f"{BASE}/ops/mcp", timeout=60, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                       headers={"Accept": "application/json, text/event-stream"})
    print(f"[warm] direct unauthenticated call to /ops/mcp -> HTTP {probe.status_code} "
          + ("(closed: only ArmorIQ's proxy, with the token, gets in)" if probe.status_code in (401, 403)
             else "(OPEN: direct callers can bypass ArmorIQ)"))


def run_scene(name: str, email: str | None, compromised: bool, approval_wait: int, use_llm: bool) -> dict:
    sc = SCENES[name]
    label = name + ("-compromised" if compromised else "")
    print("\n" + "═" * 100 + f"\nSCENE {label}: {sc.story}\nEXPECT {sc.expect}\n" + "═" * 100)
    httpx.post(f"{BASE}/sim/reset", headers=ADMIN, timeout=60).raise_for_status()
    httpx.post(f"{BASE}/sim/scene/{sc.sim_scene}", headers=ADMIN, timeout=60).raise_for_status()
    inc = Incident(sc.objective, sc.nodes, requester=email, approval_wait_s=approval_wait,
                   use_llm=use_llm, injected_steps=sc.injected if compromised else [],
                   telemetry_mcp=os.getenv("GFG_TELEMETRY_MCP", "gfg-telemetry"),
                   ops_mcp=os.getenv("GFG_OPS_MCP", "gfg-ops"))
    lin = run(ArmorIQGateway(user_email=email), inc, on_event=show)
    state = httpx.get(f"{BASE}/sim/state", timeout=60).json()
    writes = [(e["tool"], e.get("params", {}).get("node") or e.get("params", {}).get("nodes")) for e in state["audit_log"]]
    print(f"\nOUTCOME  {lin.outcome}")
    print(f"FLEET    writes that reached it: {writes or 'none'}  "
          f"gpus_out_of_service={state['summary']['gpus_out_of_service']}")
    out = pathlib.Path("runs")
    out.mkdir(exist_ok=True)
    path = out / f"{time.strftime('%Y%m%dT%H%M%S')}-{label}.json"
    path.write_text(json.dumps({**lin.to_dict(), "scene": label, "fleet_audit": state["audit_log"]},
                               indent=1, default=str))
    print(f"LINEAGE  {path}")
    calls = [e for e in lin.events if e["kind"] == "call"]
    return {"scene": label, "verdicts": Counter(f"{e['decision']}[{e['source']}]" for e in calls),
            "writes": len(writes), "gpus_out": state["summary"]["gpus_out_of_service"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", choices=sorted(SCENES) + ["all"], default="xid79")
    ap.add_argument("--email", help="on-call SRE email (for_user + approval requester)")
    ap.add_argument("--compromised", action="store_true", help="inject the scene's bad steps into the write plan")
    ap.add_argument("--approval-wait", type=int, default=0)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--warm", action="store_true", help="only wake Render and check MCP auth")
    args = ap.parse_args()
    warm()
    if args.warm:
        return
    if not os.getenv("ARMORIQ_API_KEY"):
        sys.exit("ARMORIQ_API_KEY is not set (put it in .env). The key is never printed.")
    runs = ([(s, False) for s in DEMO_ORDER] + [("split_screen", True), ("rma_trap", True)]
            if args.scene == "all" else [(args.scene, args.compromised)])
    score = [run_scene(s, args.email, comp, args.approval_wait if s == "xid79" else 0, not args.no_llm)
             for s, comp in runs]
    print("\n" + "═" * 100 + "\nSCORECARD (verdicts as returned by ArmorIQ)")
    for r in score:
        print(f"  {r['scene']:26} {dict(r['verdicts'])}  fleet writes={r['writes']} gpus_out={r['gpus_out']}")


if __name__ == "__main__":
    main()
