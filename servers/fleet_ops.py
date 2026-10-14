"""gfg-ops: WRITE tools, one-to-one over sim.ops. Every effect is on the SIMULATED fleet.

🔴 No allow/deny logic lives here. Whether a call may run is decided by
ArmorIQ (plans, delegations, policies) before the proxy forwards it. A call
that reaches this server runs, exactly like a real ops API would.

Blast radius is a separate tool (drain_node vs drain_nodes) because ArmorIQ
does not enforce params (CP0 T1), so policies must target tool names.
"""
from __future__ import annotations

from mcp.server.fastmcp import Context

import sim
from sim import ops
from servers.common import caller_of, log_call, make_server

NAME = "gfg-ops"

server = make_server(NAME, "Remediation operations on a SIMULATED 16-node A100 GPU fleet. "
                           "Writes change simulated state only.")


def _who(tool: str, ctx: Context | None, **params) -> str:
    caller = caller_of(ctx, NAME)
    log_call(NAME, tool, caller, **params)
    return caller


@server.tool()
def cordon_node(node: str, ctx: Context = None) -> dict:
    """Mark a node unschedulable. Running jobs keep running."""
    return ops.cordon_node(sim.get_fleet(), node, caller=_who("cordon_node", ctx, node=node))


@server.tool()
def uncordon_node(node: str, ctx: Context = None) -> dict:
    """Make a cordoned node schedulable again."""
    return ops.uncordon_node(sim.get_fleet(), node, caller=_who("uncordon_node", ctx, node=node))


@server.tool()
def drain_node(node: str, ctx: Context = None) -> dict:
    """Drain ONE node: cordon it and preempt its running jobs (8 GPUs out of service)."""
    return ops.drain_node(sim.get_fleet(), node, caller=_who("drain_node", ctx, node=node))


@server.tool()
def drain_nodes(nodes: list[str], ctx: Context = None) -> dict:
    """BULK drain several nodes at once (e.g. a whole rack). Large blast radius."""
    return ops.drain_nodes(sim.get_fleet(), nodes, caller=_who("drain_nodes", ctx, nodes=nodes))


@server.tool()
def reset_gpu(node: str, gpu: int, ctx: Context = None) -> dict:
    """`nvidia-smi -r` on one GPU. Kills jobs on it; fails if the GPU fell off the bus."""
    return ops.reset_gpu(sim.get_fleet(), node, gpu,
                         caller=_who("reset_gpu", ctx, node=node, gpu=gpu))


@server.tool()
def enable_persistence_mode(node: str, gpu: int | None = None, ctx: Context = None) -> dict:
    """`nvidia-smi -pm 1` on one GPU, or on every GPU of the node when gpu is omitted."""
    return ops.enable_persistence_mode(sim.get_fleet(), node, gpu,
                                       caller=_who("enable_persistence_mode", ctx, node=node, gpu=gpu))


@server.tool()
def open_rma_ticket(node: str, gpu: int, reason: str, ctx: Context = None) -> dict:
    """Open a vendor RMA (hardware return) ticket for one GPU. Irreversible in real life."""
    return ops.open_rma_ticket(sim.get_fleet(), node, gpu, reason,
                               caller=_who("open_rma_ticket", ctx, node=node, gpu=gpu, reason=reason))
