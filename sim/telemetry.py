"""Read-side API over the fleet. CP2's telemetry-mcp wraps these one-to-one.

Raw views return the real text formats; the structured helpers parse those
same texts (or expose DCGM link fields 1211/1212) so the diagnoser sees
evidence, not simulator internals. Tenant text (job logs, userspace kmsg) is
returned unsanitised on purpose: the injection scene depends on it.
"""
from __future__ import annotations

from sim.fleet import Fleet
from sim.render import dcgm, diag, dmesg, nvlink, smi


def list_nodes(fleet: Fleet) -> list[dict]:
    return [{"node": n.name, "rack": n.rack, "tags": list(n.tags), "gpus": len(n.gpus),
             "cordoned": n.cordoned, "drained": n.drained} for n in fleet.nodes.values()]


def get_dcgm_metrics(fleet: Fleet, node: str) -> str:
    return dcgm.render_node(fleet.node(node))


def get_smi_query(fleet: Fleet, node: str) -> str:
    return smi.render_node(fleet.node(node), fleet.clock)


def get_diag_result(fleet: Fleet, node: str) -> str:
    return diag.render_node(fleet.node(node))


def get_dmesg(fleet: Fleet, node: str) -> str:
    return dmesg.render_dmesg(fleet.node(node), fleet.clock)


def get_kmsg(fleet: Fleet, node: str) -> str:
    return dmesg.render_kmsg(fleet.node(node), fleet.clock)


def get_xid_events(fleet: Fleet, node: str) -> list[dict]:
    """Xid events parsed from dmesg; GPU index joined via pci address."""
    n = fleet.node(node)
    by_pci = {g.pci_short: g.index for g in n.gpus}
    events = dmesg.parse_xid_events(get_dmesg(fleet, node))
    for e in events:
        e["node"], e["gpu"] = node, by_pci.get(e["pci"])
    return events


def get_nvlink_status(fleet: Fleet, node: str) -> str:
    return nvlink.render_status(fleet.node(node))


def get_nvlink_errors(fleet: Fleet, node: str) -> dict:
    """Per-link counters, incl. DCGM 1211 link_recovery_successful / 1212 _failed."""
    n = fleet.node(node)
    return {
        "text": nvlink.render_errors(n),
        "links": [{"gpu": g.index, "link": l.index, "up": l.up,
                   "link_recovery_successful": l.recovery_successful,
                   "link_recovery_failed": l.recovery_failed}
                  for g in n.gpus if g.on_bus for l in g.nvlinks
                  if l.recovery_successful or l.recovery_failed or not l.up],
    }


def get_node_jobs(fleet: Fleet, node: str) -> list[dict]:
    """Jobs with their log tails. ⚠ log_tail is TENANT-CONTROLLED text."""
    return [{"job": j.id, "owner": j.owner, "gpus": list(j.gpus), "tag": j.tag,
             "state": j.state, "checkpoint_age_min": j.checkpoint_age_min,
             "log_tail": list(j.log_tail)} for j in fleet.node(node).jobs]


def fleet_summary(fleet: Fleet) -> dict:
    """Blast-radius view for the UI: how many GPUs are out of service."""
    idle = sum(len(n.gpus) for n in fleet.nodes.values() if n.drained)
    return {"nodes": len(fleet.nodes), "gpus": sum(len(n.gpus) for n in fleet.nodes.values()),
            "cordoned": [n.name for n in fleet.nodes.values() if n.cordoned],
            "drained": [n.name for n in fleet.nodes.values() if n.drained],
            "gpus_out_of_service": idle, "rma_tickets": len(fleet.rma_tickets),
            "active_faults": list(fleet.active_faults), "now": fleet.clock.iso()}
