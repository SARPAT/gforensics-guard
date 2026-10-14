"""Write-side fleet operations. CP2's fleet-ops-mcp wraps these one-to-one.

🔴 These functions validate inputs (does the node exist?) and apply effects.
They NEVER decide whether an action is allowed: that is ArmorIQ's job
(plans, delegations, policies). A naive agent calling these directly can do
real damage to the simulated fleet; that is the point of the split screen.

Blast radius is a separate TOOL, not a param: T1 showed ArmorIQ does not
enforce params, so `drain_node` (one node) and `drain_nodes` (bulk) exist as
two tools that policies can treat differently.
"""
from __future__ import annotations

from sim.fleet import Fleet
from sim.model import AuditEntry, RmaTicket


def _audit(fleet: Fleet, tool: str, params: dict, caller: str, result: str) -> None:
    fleet.audit_log.append(AuditEntry(fleet.clock.iso(), tool, params, caller, result))


def cordon_node(fleet: Fleet, node: str, caller: str = "unknown") -> dict:
    with fleet.lock:
        n = fleet.node(node)
        n.cordoned = True
        fleet.clock.advance(1)
        _audit(fleet, "cordon_node", {"node": node}, caller, "cordoned")
        return {"node": node, "cordoned": True}


def uncordon_node(fleet: Fleet, node: str, caller: str = "unknown") -> dict:
    with fleet.lock:
        n = fleet.node(node)
        n.cordoned = False
        fleet.clock.advance(1)
        _audit(fleet, "uncordon_node", {"node": node}, caller, "uncordoned")
        return {"node": node, "cordoned": False}


def _drain(fleet: Fleet, node: str) -> dict:
    n = fleet.node(node)
    preempted = []
    for job in n.jobs:
        if job.state == "running":
            job.state = "preempted"
            preempted.append(job.id)
    n.cordoned = n.drained = True
    return {"node": node, "jobs_preempted": preempted, "gpus_affected": len(n.gpus)}


def drain_node(fleet: Fleet, node: str, caller: str = "unknown") -> dict:
    with fleet.lock:
        res = _drain(fleet, node)
        fleet.clock.advance(30)
        _audit(fleet, "drain_node", {"node": node}, caller, f"drained {res['gpus_affected']} GPUs")
        return res


def drain_nodes(fleet: Fleet, nodes: list[str], caller: str = "unknown") -> dict:
    """Bulk drain (the tool a poisoned log would push an agent toward)."""
    with fleet.lock:
        for name in nodes:
            fleet.node(name)                  # validate all before touching any
        per_node = [_drain(fleet, name) for name in nodes]
        total = sum(r["gpus_affected"] for r in per_node)
        fleet.clock.advance(30)
        _audit(fleet, "drain_nodes", {"nodes": list(nodes)}, caller, f"drained {total} GPUs")
        return {"nodes": list(nodes), "gpus_affected": total, "per_node": per_node}


def reset_gpu(fleet: Fleet, node: str, gpu: int, caller: str = "unknown") -> dict:
    """Effects: kills jobs on that GPU. Does NOT clear the sticky XID gauge
    (Finding #1); does nothing for a GPU that fell off the bus (needs RMA);
    activates a pending row remap."""
    with fleet.lock:
        n, g = fleet.node(node), fleet.gpu(node, gpu)
        fleet.clock.advance(20)
        if not g.on_bus:
            _audit(fleet, "reset_gpu", {"node": node, "gpu": gpu}, caller, "failed: GPU not on bus")
            return {"node": node, "gpu": gpu, "ok": False, "error": "GPU not found on bus"}
        g.reset_count += 1
        killed = []
        for job in n.jobs:
            if gpu in job.gpus and job.state == "running":
                job.state = "killed"
                killed.append(job.id)
        remap_activated = g.remap_pending
        g.remap_pending = False
        _audit(fleet, "reset_gpu", {"node": node, "gpu": gpu}, caller, f"reset; killed {killed}")
        return {"node": node, "gpu": gpu, "ok": True, "jobs_killed": killed,
                "remap_activated": remap_activated, "xid_gauge_after": g.xid_last}


def enable_persistence_mode(fleet: Fleet, node: str, gpu: int | None = None,
                            caller: str = "unknown") -> dict:
    """`nvidia-smi -pm 1`. Fixes the PERSISTENCE_OFF diag failure."""
    with fleet.lock:
        n = fleet.node(node)
        targets = n.gpus if gpu is None else [fleet.gpu(node, gpu)]
        for g in targets:
            if g.on_bus:
                g.persistence_mode = True
        fleet.clock.advance(2)
        _audit(fleet, "enable_persistence_mode", {"node": node, "gpu": gpu}, caller,
               f"enabled on {len(targets)} GPU(s)")
        return {"node": node, "gpus": [g.index for g in targets], "persistence_mode": True}


def open_rma_ticket(fleet: Fleet, node: str, gpu: int, reason: str,
                    caller: str = "unknown") -> dict:
    """Vendor return. Records the ticket; the sim does not judge whether it is justified."""
    with fleet.lock:
        g = fleet.gpu(node, gpu)
        fleet.clock.advance(5)
        t = RmaTicket(id=f"RMA-{1000 + len(fleet.rma_tickets) + 1}", node=node, gpu=gpu,
                      gpu_uuid=g.uuid, reason=reason, requested_by=caller, at=fleet.clock.iso())
        fleet.rma_tickets.append(t)
        _audit(fleet, "open_rma_ticket", {"node": node, "gpu": gpu, "reason": reason}, caller, t.id)
        return {"ticket": t.id, "node": node, "gpu": gpu, "gpu_uuid": g.uuid}
