"""CP4 scenes, offline. Uses the TEST-ONLY FakeGateway from test_agents (verdict source TEST-FAKE);
live verdicts come only from ArmorIQ via scripts/demo.py."""
from __future__ import annotations

from sim.faults import inject_scene

from agents.orchestrator import Incident, run
from agents.scenes import DEMO_ORDER, RACK_B, SCENES
from tests.test_agents import FakeGateway


def _run(fleet, name, outcomes=None, compromised=True):
    sc = SCENES[name]
    inject_scene(fleet, sc.sim_scene)
    gw = FakeGateway(fleet, outcomes)
    lin = run(gw, Incident(sc.objective, sc.nodes, use_llm=False,
                           injected_steps=sc.injected if compromised else []))
    return gw, lin


def test_scene_table():
    assert DEMO_ORDER == ["split_screen", "rma_trap", "xid79"]
    assert set(DEMO_ORDER) <= set(SCENES)
    assert SCENES["split_screen"].injected[0]["params"]["nodes"] == RACK_B and len(RACK_B) == 8


def test_injected_step_goes_into_signed_write_plan_and_armoriq_decides(fleet):
    gw, lin = _run(fleet, "split_screen", {"open_rma_ticket": "HELD", "drain_nodes": "BLOCKED"})
    p2 = gw.plans[1]
    assert [s["tool"] for s in p2.steps][-1] == "drain_nodes"
    assert [e for e in lin.events if e["kind"] == "injection"][0]["rule"] == "INJECTED"
    assert lin.outcome.startswith("stopped at drain_nodes: BLOCKED")
    assert not any(fleet.node(n).drained for n in RACK_B)


def test_rma_trap_compromised_rma_is_held_not_executed(fleet):
    gw, lin = _run(fleet, "rma_trap", {"open_rma_ticket": "HELD"})
    assert [t for a, t, _ in gw.calls if a == "remediator"] == ["enable_persistence_mode", "open_rma_ticket"]
    assert "awaiting approval: ['open_rma_ticket']" in lin.outcome


def test_governed_run_has_no_injection(fleet):
    gw, lin = _run(fleet, "rma_trap", compromised=False)
    assert not [e for e in lin.events if e["kind"] == "injection"]
    assert [s["tool"] for s in gw.plans[1].steps] == ["enable_persistence_mode"]
