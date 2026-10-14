"""Orchestrator: owns the objective, commits one plan per phase, delegates, records lineage.

    OBJECTIVE
      PHASE 1 investigate  commit(read steps) -> delegate_subtree -> Diagnoser reads -> rule table
                           (narrator LLM sees raw logs; its suggestions are attempted with the
                            Diagnoser's read-only token, so ArmorIQ gives the verdict)
      AUTHORITY EXPANSION  commit(proposed write steps, parent = phase-1 plan hash)
      PHASE 2 remediate    delegate_subtree -> Remediator invokes each step
                           HELD -> delegation request -> wait for a human -> re-invoke
      CLOSE                lineage: objective -> plans -> delegations -> calls -> verdicts

🔴 Nothing here allows or blocks a tool. The control flow only STOPS early (fail closed):
the remediation sequence halts at the first step ArmorIQ did not allow.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from agents import diagnoser, narrator
from agents.armoriq_io import ArmorIQGateway, CommittedPlan, PlanRefused, Verdict
from agents.rules import INVESTIGATION_READS, OPS_MCP, TELEMETRY_MCP

GPUS_PER_NODE = 8


@dataclass
class Incident:
    objective: str
    nodes: list[str]
    requester: str | None = None          # on-call SRE (for_user / delegation requester)
    approval_wait_s: int = 0              # how long to wait for a human on a HELD step
    use_llm: bool = True
    trust_llm_plan: bool = False          # "compromised planner": put LLM suggestions in the write plan
    telemetry_mcp: str = TELEMETRY_MCP
    ops_mcp: str = OPS_MCP


@dataclass
class Lineage:
    objective: str
    started: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)
    outcome: str = ""

    def add(self, kind: str, **data) -> dict:
        e = {"kind": kind, "t": round(time.time() - self.started, 2), **data}
        self.events.append(e)
        return e

    def verdict(self, v: Verdict, **extra) -> Verdict:
        self.add("call", **v.to_dict(), **extra)
        return v

    def to_dict(self) -> dict:
        return {"objective": self.objective, "started": self.started,
                "outcome": self.outcome, "events": self.events}


def gpus_affected(tool: str, params: dict) -> int:
    if tool == "drain_nodes":
        return GPUS_PER_NODE * len(params.get("nodes", []))
    if tool in ("drain_node", "cordon_node", "enable_persistence_mode"):
        return GPUS_PER_NODE
    return 1


def run(gw: ArmorIQGateway, inc: Incident, on_event: Callable[[dict], Any] | None = None) -> Lineage:
    lin = Lineage(inc.objective)
    if on_event:
        _add = lin.add

        def add(kind, **data):   # stream events to the caller (CLI / later the web UI)
            e = _add(kind, **data)
            on_event(e)
            return e
        lin.add = add  # type: ignore[method-assign]
    lin.add("objective", text=inc.objective, nodes=inc.nodes, requester=inc.requester)

    # ── PHASE 1: investigate (read-only authority) ─────────────────────────
    reads = [{"tool": t, "mcp": inc.telemetry_mcp, "params": {"node": inc.nodes[0]}}
             for t in INVESTIGATION_READS]
    try:
        p1 = gw.commit("investigate", f"INVESTIGATE: {inc.objective}", reads)
    except PlanRefused as e:
        lin.verdict(e.verdict)
        lin.outcome = "investigation plan refused by ArmorIQ"
        return lin
    lin.add("plan", **p1.summary())
    d1 = _delegate(gw, lin, p1, "diagnoser")
    if not d1:
        lin.outcome = "delegation to diagnoser failed (fail closed)"
        return lin

    findings, all_steps = [], []
    for node in inc.nodes:
        res = diagnoser.investigate(
            node, lambda tool, params: lin.verdict(
                gw.call(d1["token"], "diagnoser", inc.telemetry_mcp, tool, params)))
        dx = res["diagnosis"]
        findings.append(dx)
        lin.add("diagnosis", node=node, category=dx["category"],
                findings=dx["findings"], proposed_steps=dx["proposed_steps"])
        all_steps += [s | {"mcp": inc.ops_mcp} for s in dx["proposed_steps"]]

        if inc.use_llm and res["evidence"]["reads"]:
            story = narrator.narrate(res["evidence"], dx)
            lin.add("narrative", node=node, **story)
            for a in story["suggested_actions"]:
                if any(a["tool"] == s["tool"] and a["params"] == s["params"] for s in all_steps):
                    continue   # the rule table already proposes it
                # The Diagnoser's LLM wants to act. It only holds a read-only token:
                lin.verdict(gw.call(d1["token"], "diagnoser(llm-suggested)", inc.ops_mcp,
                                    a["tool"], a["params"]), because=a.get("because", ""))
                if inc.trust_llm_plan:
                    all_steps.append({"tool": a["tool"], "mcp": inc.ops_mcp, "params": a["params"],
                                      "why": f"LLM: {a.get('because', '')}", "rule": "LLM"})

    if not all_steps:
        lin.outcome = "no write authority requested: " + ", ".join(
            f"{f['node']}={f['category']}" for f in findings)
        return lin

    # ── AUTHORITY EXPANSION: a new plan, chained to the investigation ──────
    try:
        p2 = gw.commit("remediate", f"REMEDIATE: {inc.objective}", all_steps, parent=p1,
                       metadata={"evidence": [{"node": f["node"], "category": f["category"],
                                               "rules": [x["rule"] for x in f["findings"]]}
                                              for f in findings]})
    except PlanRefused as e:
        lin.verdict(e.verdict)
        lin.outcome = "remediation plan refused by ArmorIQ"
        return lin
    lin.add("plan", **p2.summary(), why=[{"tool": s["tool"], "rule": s.get("rule"),
                                          "why": s.get("why")} for s in all_steps])
    d2 = _delegate(gw, lin, p2, "remediator")
    if not d2:
        lin.outcome = "delegation to remediator failed (fail closed)"
        return lin

    # ── PHASE 2: remediate, one step at a time, stop at the first non-ALLOWED ──
    done = []
    for s in all_steps:
        v = lin.verdict(gw.call(d2["token"], "remediator", s["mcp"], s["tool"], s["params"]),
                        rule=s.get("rule"))
        if v.decision == "HELD":
            v = _approval(gw, lin, inc, p2, d2, v)
        if v.decision != "ALLOWED":
            lin.outcome = (f"stopped at {s['tool']}: {v.decision} [{v.source}]; "
                           f"done before it: {done or 'nothing'}")
            return lin
        done.append(s["tool"])
    lin.outcome = f"remediation complete: {done}"
    return lin


def _delegate(gw: ArmorIQGateway, lin: Lineage, plan: CommittedPlan, agent: str) -> dict | None:
    try:
        d = gw.delegate(plan, agent)
    except Exception as e:  # noqa: BLE001  (DelegationException or transport: fail closed)
        lin.add("delegation", agent=agent, plan_hash=plan.plan_hash, ok=False,
                error=f"{type(e).__name__}: {str(e)[:300]}")
        return None
    lin.add("delegation", ok=True, **{k: v for k, v in d.items() if k != "token"})
    return d


def _approval(gw, lin, inc: Incident, plan: CommittedPlan, deleg: dict, held: Verdict) -> Verdict:
    """HELD -> ArmorIQ delegation request -> a human decides -> re-invoke with its id."""
    if not inc.requester:
        lin.add("approval", tool=held.tool, status="not requested (no requester email)")
        return held
    try:
        did = gw.request_approval(plan, held, inc.requester, gpus_affected(held.tool, held.params))
    except Exception as e:  # noqa: BLE001
        lin.add("approval", tool=held.tool, status="request failed", error=f"{type(e).__name__}: {e}")
        return held
    held.approval_id = did
    lin.add("approval", tool=held.tool, delegation_id=did, status="pending",
            wait_s=inc.approval_wait_s)
    deadline, status = time.time() + inc.approval_wait_s, "pending"
    while time.time() < deadline:
        try:
            status = gw.approval_status(did)
        except Exception as e:  # noqa: BLE001
            status = f"status error {type(e).__name__}"
        if status in ("approved", "rejected", "expired"):
            break
        time.sleep(5)
    lin.add("approval", tool=held.tool, delegation_id=did, status=status)
    if status != "approved":
        return held
    v = lin.verdict(gw.call(deleg["token"], "remediator", held.mcp, held.tool, held.params,
                            extra={"_delegation_id": did}), after_approval=did)
    v.approval_id = did
    if v.decision == "ALLOWED":
        try:
            gw.mark_executed(did, plan, inc.requester)
        except Exception as e:  # noqa: BLE001
            lin.add("approval", delegation_id=did, status=f"mark_executed failed: {e}")
    return v
