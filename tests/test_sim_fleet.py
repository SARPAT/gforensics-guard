"""Fleet shape, determinism and reset (CP1 plan §6: test_fleet_shape, test_determinism)."""
from __future__ import annotations

import json

import sim
from sim import telemetry
from sim.faults import inject_scene
from sim.model import DGX_PCI_BUSES

# Every per-node read view the telemetry MCP will expose.
PER_NODE_VIEWS = (
    telemetry.get_dcgm_metrics,
    telemetry.get_smi_query,
    telemetry.get_diag_result,
    telemetry.get_dmesg,
    telemetry.get_kmsg,
    telemetry.get_xid_events,
    telemetry.get_nvlink_status,
    telemetry.get_nvlink_errors,
    telemetry.get_node_jobs,
)
FLEET_VIEWS = (telemetry.list_nodes, telemetry.fleet_summary)


def snapshot(fleet) -> dict[str, str]:
    """Every renderer's output for every node, as text (dict/list views as sorted JSON)."""
    def text(v) -> str:
        return v if isinstance(v, str) else json.dumps(v, sort_keys=True, default=str)
    out = {f.__name__: text(f(fleet)) for f in FLEET_VIEWS}
    for name in fleet.nodes:
        for f in PER_NODE_VIEWS:
            out[f"{f.__name__}:{name}"] = text(f(fleet, name))
    return out


def test_sixteen_nodes_of_eight_gpus(fleet):
    assert len(fleet.nodes) == 16
    assert all(len(n.gpus) == 8 for n in fleet.nodes.values())
    assert list(fleet.nodes) == [f"node-{i:02d}" for i in range(1, 17)]


def test_gpu_uuids_unique_across_fleet(fleet):
    uuids = [g.uuid for n in fleet.nodes.values() for g in n.gpus]
    assert len(uuids) == 128
    assert len(set(uuids)) == 128
    assert all(u.startswith("GPU-") for u in uuids)


def test_gpus_sit_on_dgx_a100_pci_buses(fleet):
    """GPU i on every node uses the DGX A100 SXM slot bus (07, 0F, 47, ...)."""
    for n in fleet.nodes.values():
        assert tuple(g.pci_bus for g in n.gpus) == DGX_PCI_BUSES
        assert len({g.pci_bus_id for g in n.gpus}) == 8
    assert fleet.gpu("node-07", 2).pci_bus_id == "00000000:47:00.0"


def test_racks_split_eight_and_eight(fleet):
    racks = fleet.racks()
    assert racks["A"] == [f"node-{i:02d}" for i in range(1, 9)]
    assert racks["B"] == [f"node-{i:02d}" for i in range(9, 17)]


def test_same_seed_renders_byte_identical():
    """Demos and tests are reproducible: seed 7 always renders the same bytes."""
    assert snapshot(sim.build_fleet(seed=7)) == snapshot(sim.build_fleet(seed=7))


def test_same_seed_identical_after_all_faults():
    a, b = sim.build_fleet(seed=7), sim.build_fleet(seed=7)
    inject_scene(a, "all")
    inject_scene(b, "all")
    assert snapshot(a) == snapshot(b)


def test_different_seed_gives_different_uuids():
    a, b = sim.build_fleet(seed=7), sim.build_fleet(seed=8)
    ua = {g.uuid for n in a.nodes.values() for g in n.gpus}
    ub = {g.uuid for n in b.nodes.values() for g in n.gpus}
    assert ua.isdisjoint(ub)


def test_reset_fleet_restores_baseline():
    """reset_fleet() drops faults, ops and tickets and is the seeded baseline again."""
    from sim import ops

    baseline = snapshot(sim.build_fleet())
    f = sim.reset_fleet()
    inject_scene(f, "all")
    ops.drain_node(f, "node-03", caller="test")
    ops.open_rma_ticket(f, "node-07", 2, "test", caller="test")
    assert snapshot(f) != baseline

    f2 = sim.reset_fleet()
    assert f2 is sim.get_fleet()
    assert f2 is not f
    assert f2.active_faults == [] and f2.audit_log == [] and f2.rma_tickets == []
    assert snapshot(f2) == baseline
