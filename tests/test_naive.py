"""Naive-agent baseline (no ArmorIQ): runs offline against its own private fleet."""
from __future__ import annotations

import sim
from agents import naive, narrator
from agents.scenes import RACK_B
from sim import telemetry


def _fake_llm(actions):
    def narrate(evidence, diagnosis, timeout=60.0):
        assert diagnosis["findings"] == []          # no rule table
        assert evidence["jobs"]                      # raw evidence reached the LLM
        return {"llm": "fake", "narrative": "obey the log", "suggested_actions": actions}
    return narrate


def test_llm_drains_rack_b_on_private_fleet(monkeypatch):
    shared_before = telemetry.fleet_summary(sim.get_fleet())
    act = {"tool": "drain_nodes", "params": {"nodes": RACK_B}, "because": "job log override"}
    monkeypatch.setattr(narrator, "narrate", _fake_llm([act, dict(act)]))   # duplicate deduped
    seen = []
    out = naive.run_naive("split_screen", on_event=seen.append)
    assert out["fleet"]["gpus_out_of_service"] == 64
    execs = [e for e in out["events"] if e["kind"] == "exec"]
    assert len(execs) == 1 and execs[0]["decision"] == "EXECUTED" and execs[0]["source"] == "NO-ARMORIQ"
    reads = [e for e in out["events"] if e["kind"] == "read"]
    assert len(reads) == 7
    assert seen == out["events"] and all("t" in e for e in seen)
    assert out["audit"][0]["caller"] == "naive-agent"
    assert out["outcome"].startswith("64 GPUs taken out of service")
    assert telemetry.fleet_summary(sim.get_fleet()) == shared_before


def test_llm_off_runs_scripted_playbook(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    out = naive.run_naive("rma_trap")
    kinds = [e["kind"] for e in out["events"]]
    assert "note" in kinds
    execs = [e for e in out["events"] if e["kind"] == "exec"]
    assert len(execs) == 8 and all(e.get("scripted") for e in execs)
    assert out["fleet"]["rma_tickets"] == 8


def test_bad_suggestions_do_not_crash(monkeypatch):
    monkeypatch.setattr(narrator, "narrate", _fake_llm([
        {"tool": "format_disk", "params": {"node": "node-07"}},
        {"tool": "drain_node", "params": {"node": "node-99"}},
        {"tool": "reset_gpu", "params": {"gpu": "x"}},
        {"tool": "drain_nodes", "params": {"nodes": "rack B"}},
        {"tool": "cordon_node", "params": {"node": "node-07", "force": True}},
    ]))
    out = naive.run_naive("xid79")
    execs = [e for e in out["events"] if e["kind"] == "exec"]
    assert len(execs) == 5
    assert all(str(e["result"]).startswith("error:") for e in execs[:4])
    assert execs[4]["result"]["cordoned"] is True
    assert "note" not in [e["kind"] for e in out["events"]]


def test_every_scene_scripted_runs():
    for name in ("split_screen", "rma_trap", "xid79", "noise"):
        out = naive.run_naive(name, use_llm=False)
        assert any(e.get("scripted") for e in out["events"])
