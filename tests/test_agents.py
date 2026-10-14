"""CP3 agents, offline. The fake gateway below is TEST-ONLY: it runs the simulator
directly and labels every verdict source="TEST-FAKE". Product code has no such path;
live verdicts come only from ArmorIQ (scripts/cp3_run.py)."""
from __future__ import annotations

import pytest

from sim import ops as sim_ops
from sim import telemetry as sim_tel
from sim.faults import inject, inject_scene

from agents import diagnoser
from agents.armoriq_io import Verdict, tool_output
from agents.orchestrator import Incident, run
from agents.rules import INVESTIGATION_READS


class Plan:
    def __init__(self, phase, steps, parent):
        self.phase, self.steps = phase, steps
        self.plan_hash = f"hash-{phase}"
        self.parent_plan_hash = parent.plan_hash if parent else None
        self.plan_id, self.intent_reference, self.goal = f"id-{phase}", f"ref-{phase}", phase

    def summary(self):
        return {"phase": self.phase, "plan_hash": self.plan_hash, "plan_id": self.plan_id,
                "parent_plan_hash": self.parent_plan_hash, "allowed_tools": None,
                "intent_reference": self.intent_reference, "goal": self.goal,
                "steps": [{"tool": s["tool"], "mcp": s["mcp"], "params": s["params"]} for s in self.steps]}


class FakeGateway:
    """TEST-ONLY stand-in for ArmorIQGateway. `outcomes` maps tool -> decision."""

    def __init__(self, fleet, outcomes=None, approve=None):
        self.fleet, self.outcomes, self.approve = fleet, outcomes or {}, approve
        self.plans, self.calls = [], []

    def commit(self, phase, goal, steps, parent=None, metadata=None):
        p = Plan(phase, steps, parent)
        self.plans.append(p)
        return p

    def delegate(self, plan, agent, subtree_path="/steps"):
        return {"agent": agent, "subtree_path": subtree_path, "trust_id": f"t-{agent}",
                "subtree_root": "r", "parent_plan_hash": plan.plan_hash, "token": plan}

    def call(self, token, actor, mcp, tool, params, extra=None):
        self.calls.append((actor, tool, params))
        v = Verdict(actor, mcp, tool, params, "ERROR", "TEST-FAKE", plan_hash=token.plan_hash)
        if tool not in [s["tool"] for s in token.steps]:
            v.decision, v.reason = "BLOCKED", "not in plan"
            return v
        decision = self.outcomes.get(tool, "ALLOWED")
        v.decision = decision
        if decision == "ALLOWED":
            fn = getattr(sim_tel, tool, None) if mcp == "gfg-telemetry" else getattr(sim_ops, tool)
            v.data = fn(self.fleet, **params) if mcp == "gfg-telemetry" else fn(self.fleet, **params, caller=actor)
        return v

    def request_approval(self, plan, held, requester, amount):
        return "deleg-1"

    def approval_status(self, did):
        return self.approve or "pending"


def investigate(fleet, node):
    gw = FakeGateway(fleet)
    p = gw.commit("investigate", "g", [{"tool": t, "mcp": "gfg-telemetry", "params": {}}
                                       for t in INVESTIGATION_READS])
    return diagnoser.investigate(node, lambda t, prm: gw.call(p, "diagnoser", "gfg-telemetry", t, prm))["diagnosis"]


def tools(dx):
    return [(s["tool"], s["params"].get("gpu")) for s in dx["proposed_steps"]]


def test_xid79_proposes_cordon_drain_rma(fleet):
    inject_scene(fleet, "xid79")
    dx = investigate(fleet, "node-07")
    assert dx["category"] == "HARDWARE"
    assert tools(dx) == [("cordon_node", None), ("drain_node", None), ("open_rma_ticket", 2)]


def test_rma_trap_proposes_config_fix_not_rma(fleet):
    inject_scene(fleet, "rma_trap")
    dx = investigate(fleet, "node-04")
    assert dx["category"] == "MISCONFIG"
    assert tools(dx) == [("enable_persistence_mode", None)]


def test_noise_proposes_nothing(fleet):
    inject_scene(fleet, "noise")
    assert investigate(fleet, "node-11")["category"] == "TRANSIENT"
    dx = investigate(fleet, "node-02")
    assert {f["rule"] for f in dx["findings"]} == {"POWER_OVER_CAP", "THROTTLE_NOT_MEASURED"}
    assert dx["proposed_steps"] == []


def test_ecc_dbe(fleet):
    inject(fleet, "ECC_DBE_xid48")
    assert tools(investigate(fleet, "node-05")) == [("cordon_node", None), ("drain_node", None), ("reset_gpu", 1)]


def test_healthy_node(fleet):
    dx = investigate(fleet, "node-03")
    assert dx["category"] == "HEALTHY" and dx["proposed_steps"] == []


def test_poisoned_log_does_not_change_the_rule_table(fleet):
    inject_scene(fleet, "split_screen")
    assert tools(investigate(fleet, "node-07")) == [("cordon_node", None), ("drain_node", None),
                                                    ("open_rma_ticket", 2)]


def test_blocked_reads_mean_insufficient_evidence(fleet):
    inject_scene(fleet, "xid79")
    gw = FakeGateway(fleet, {t: "BLOCKED" for t in INVESTIGATION_READS})
    p = gw.commit("investigate", "g", [{"tool": t, "mcp": "gfg-telemetry", "params": {}} for t in INVESTIGATION_READS])
    dx = diagnoser.investigate("node-07", lambda t, prm: gw.call(p, "d", "gfg-telemetry", t, prm))["diagnosis"]
    assert dx["category"] == "INSUFFICIENT_EVIDENCE" and dx["proposed_steps"] == []


def test_xid_line_alone_is_not_enough(fleet):
    inject_scene(fleet, "xid79")
    gw = FakeGateway(fleet, {t: "BLOCKED" for t in INVESTIGATION_READS if t != "get_xid_events"})
    p = gw.commit("investigate", "g", [{"tool": t, "mcp": "gfg-telemetry", "params": {}} for t in INVESTIGATION_READS])
    dx = diagnoser.investigate("node-07", lambda t, prm: gw.call(p, "d", "gfg-telemetry", t, prm))["diagnosis"]
    assert dx["category"] == "INSUFFICIENT_EVIDENCE" and dx["proposed_steps"] == []


def _incident(**kw):
    return Incident("Investigate XID 79 on node-07", ["node-07"], use_llm=False, **kw)


def test_orchestrator_chains_plans_and_parks_hold(fleet):
    inject_scene(fleet, "xid79")
    gw = FakeGateway(fleet, {"drain_node": "HELD"})
    lin = run(gw, _incident(requester="sre@example.com"))
    p1, p2 = gw.plans
    assert p1.phase == "investigate" and [s["tool"] for s in p1.steps] == list(INVESTIGATION_READS)
    assert p2.parent_plan_hash == p1.plan_hash
    assert all(s["mcp"] == "gfg-ops" for s in p2.steps)
    writes = [t for a, t, _ in gw.calls if a == "remediator"]
    assert writes == ["cordon_node", "drain_node", "open_rma_ticket"]   # HELD parks, rest still asked
    assert "awaiting approval: ['drain_node']" in lin.outcome
    assert fleet.node("node-07").cordoned and not fleet.node("node-07").drained


def test_orchestrator_stops_at_block(fleet):
    inject_scene(fleet, "xid79")
    gw = FakeGateway(fleet, {"drain_node": "BLOCKED"})
    lin = run(gw, _incident())
    assert [t for a, t, _ in gw.calls if a == "remediator"] == ["cordon_node", "drain_node"]
    assert lin.outcome.startswith("stopped at drain_node: BLOCKED")


def test_human_approval_is_recorded_but_agent_never_self_releases(fleet):
    inject_scene(fleet, "xid79")
    gw = FakeGateway(fleet, {"drain_node": "HELD"}, approve="approved")
    lin = run(gw, _incident(requester="sre@example.com", approval_wait_s=1))
    assert [e["status"] for e in lin.events if e["kind"] == "approval"] == ["pending", "approved"]
    assert "awaiting approval: ['drain_node']" in lin.outcome and not fleet.node("node-07").drained
    assert all(not k.startswith("_") for _, _, prm in gw.calls for k in prm)


def test_no_writes_planned_for_noise(fleet):
    inject_scene(fleet, "noise")
    gw = FakeGateway(fleet)
    lin = run(gw, Incident("noise", ["node-11", "node-02"], use_llm=False))
    assert len(gw.plans) == 1 and "no write authority requested" in lin.outcome


def test_tool_output_shapes():
    assert tool_output({"structuredContent": {"result": [1]}, "content": []}) == [1]
    assert tool_output({"content": [{"type": "text", "text": '{"a": 1}'}]}) == {"a": 1}
    assert tool_output({"content": [{"type": "text", "text": "raw"}]}) == "raw"


def test_narrator_parse_reply_filters_to_known_tools():
    from agents.narrator import parse_reply
    out = parse_reply('<think>x</think>```json\n{"narrative": "n", "suggested_actions": ['
                      '{"tool": "drain_nodes", "params": {"nodes": ["node-09"]}}, '
                      '{"tool": "rm_rf", "params": {}}]}\n```')
    assert out["narrative"] == "n" and [a["tool"] for a in out["suggested_actions"]] == ["drain_nodes"]


def test_narrator_salvages_truncated_reply():
    from agents.narrator import parse_reply
    cut = '{"narrative": "n", "suggested_actions": [{"tool": "drain_nodes", "params": {"nodes": ["node-09"]}, "because": "adm'
    out = parse_reply(cut)
    assert out["narrative"] == "n" and out["suggested_actions"][0]["params"] == {"nodes": ["node-09"]}


def test_rejected_approval_stays_held_and_run_continues(fleet):
    inject_scene(fleet, "xid79")
    gw = FakeGateway(fleet, {"drain_node": "HELD"}, approve="rejected")
    lin = run(gw, _incident(requester="sre@example.com", approval_wait_s=1))
    assert "awaiting approval: ['drain_node']" in lin.outcome
    assert not fleet.node("node-07").drained
