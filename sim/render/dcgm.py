"""dcgm-exporter /metrics text (Prometheus exposition), per node.

Template: data/captures/scrubbed/dcgm_exporter_metrics.txt. Same metric set,
order, HELP/TYPE text and label keys as the capture. Hostname is the exporter
container id, not the node name (Finding #4).
"""
from __future__ import annotations

from sim import captures
from sim.model import MODEL_NAME, GPU, Node

_FLOAT_METRICS = {"DCGM_FI_DEV_POWER_USAGE"}


def _labels(node: Node, gpu: GPU) -> str:
    return (f'gpu="{gpu.index}",UUID="{gpu.uuid}",pci_bus_id="{gpu.pci_bus_id}",'
            f'device="nvidia{gpu.index}",modelName="{MODEL_NAME}",Hostname="{node.hostname_label}"')


def _value(metric: str, gpu: GPU) -> float | int:
    u = gpu.util_pct
    return {
        "DCGM_FI_DEV_SM_CLOCK": gpu.sm_clock_mhz,
        "DCGM_FI_DEV_MEM_CLOCK": gpu.mem_clock_mhz,
        "DCGM_FI_DEV_CLOCK_THROTTLE_REASONS": gpu.throttle_mask,
        "DCGM_FI_DEV_MEMORY_TEMP": gpu.mem_temp_c,
        "DCGM_FI_DEV_GPU_TEMP": gpu.temp_c,
        "DCGM_FI_DEV_POWER_USAGE": gpu.power_w,
        "DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION": gpu.energy_mj,
        "DCGM_FI_DEV_PCIE_REPLAY_COUNTER": 0,
        "DCGM_FI_DEV_GPU_UTIL": u,
        "DCGM_FI_DEV_MEM_COPY_UTIL": u // 2,
        "DCGM_FI_DEV_XID_ERRORS": gpu.xid_last,
        "DCGM_FI_DEV_POWER_VIOLATION": gpu.power_violation_us,
        "DCGM_FI_DEV_THERMAL_VIOLATION": gpu.thermal_violation_us,
        "DCGM_FI_DEV_SYNC_BOOST_VIOLATION": 0,
        "DCGM_FI_DEV_BOARD_LIMIT_VIOLATION": gpu.board_violation_us,
        "DCGM_FI_DEV_LOW_UTIL_VIOLATION": 0 if u else gpu.energy_mj * 7,
        "DCGM_FI_DEV_RELIABILITY_VIOLATION": 0,
        "DCGM_FI_DEV_FB_FREE": 40442 - gpu.fb_used_mib,
        "DCGM_FI_DEV_FB_USED": gpu.fb_used_mib,
        "DCGM_FI_DEV_ECC_SBE_VOL_TOTAL": gpu.ecc_sbe_vol,
        "DCGM_FI_DEV_ECC_DBE_VOL_TOTAL": gpu.ecc_dbe_vol,
        "DCGM_FI_DEV_ECC_SBE_AGG_TOTAL": gpu.ecc_sbe_agg,
        "DCGM_FI_DEV_ECC_DBE_AGG_TOTAL": gpu.ecc_dbe_agg,
        "DCGM_FI_DEV_UNCORRECTABLE_REMAPPED_ROWS": gpu.remap_uncorrectable,
        "DCGM_FI_DEV_CORRECTABLE_REMAPPED_ROWS": gpu.remap_correctable,
        "DCGM_FI_DEV_ROW_REMAP_FAILURE": int(gpu.remap_failure),
        "DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL": (gpu.energy_mj // 3) if u else 0,
        "DCGM_FI_PROF_GR_ENGINE_ACTIVE": u / 100,
        "DCGM_FI_PROF_PIPE_TENSOR_ACTIVE": u * 0.61 / 100,
        "DCGM_FI_PROF_DRAM_ACTIVE": u * 0.38 / 100,
        "DCGM_FI_PROF_PCIE_TX_BYTES": 32784 + u * 1931,
        "DCGM_FI_PROF_PCIE_RX_BYTES": 22544 + u * 2417,
    }[metric]


def _fmt(metric: str, v: float | int) -> str:
    if metric in _FLOAT_METRICS or metric.startswith("DCGM_FI_PROF_") and "BYTES" not in metric:
        return f"{v:.6f}"
    return str(int(v))


def render_node(node: Node) -> str:
    out = []
    for m in captures.dcgm_metric_defs():
        out.append(f"# HELP {m.name} {m.help}")
        out.append(f"# TYPE {m.name} {m.type}")
        for gpu in node.gpus:
            labels = _labels(node, gpu)
            if m.name == "DCGM_FI_DEV_XID_ERRORS":
                # Finding #1: gauge of the LAST Xid, with free code->meaning labels.
                labels += f',err_code="{gpu.xid_last}",err_msg="{gpu.xid_last_msg}"'
            elif not gpu.on_bus:
                # Finding #2 behaviour: series for a GPU DCGM can't read are dropped
                # silently. Absent != healthy -> NOT_MEASURED downstream.
                continue
            out.append(f"{m.name}{{{labels}}} {_fmt(m.name, _value(m.name, gpu))}")
    return "\n".join(out) + "\n"
