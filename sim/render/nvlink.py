"""NVLink views, per node.

`nvidia-smi nvlink -s` text (template: modal_probe.txt "=== NVLINK STATUS ===")
and per-link NVML error counters (template: modal_nvml_nvlink_errors.txt, which
on Modal read ERR(NVMLError_NotSupported); the simulated DGX supports them).
"""
from __future__ import annotations

from sim.model import MODEL_NAME, Node


def render_status(node: Node) -> str:
    out = []
    for gpu in node.gpus:
        if not gpu.on_bus:
            out.append(f"Unable to determine the device handle for GPU{gpu.pci_short.upper()}.0: Unknown Error")
            continue
        out.append(f"GPU {gpu.index}: {MODEL_NAME} (UUID: {gpu.uuid})")
        for link in gpu.nvlinks:
            out.append(f"\t Link {link.index}: {'25 GB/s' if link.up else '<inactive>'}")
    return "\n".join(out) + "\n"


def render_errors(node: Node) -> str:
    out = []
    for gpu in node.gpus:
        out.append(f"--- GPU {gpu.index}")
        for link in gpu.nvlinks:
            if not gpu.on_bus:
                vals = ["ERR(NVMLError_GpuIsLost)"] * 4
            else:
                vals = [link.crc_flit, link.crc_data, link.replay,
                        link.recovery_successful + link.recovery_failed]
            out.append(f"  link {link.index}: CRC_FLIT={vals[0]} CRC_DATA={vals[1]} "
                       f"REPLAY={vals[2]} RECOVERY={vals[3]}")
    return "\n".join(out) + "\n"
