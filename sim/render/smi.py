"""`nvidia-smi -q` output, per node, built from the captured field tree.

Template: data/captures/scrubbed/nvidia_smi_q.txt. Every GPU block is the
captured block with only the simulated fields substituted; all other lines
(VBIOS, part numbers, N/A fields) stay verbatim.
"""
from __future__ import annotations

import re

from sim import captures
from sim.clock import SimClock
from sim.model import DRIVER_VERSION, GPU, MASK_IDLE, MASK_SW_POWER_CAP, Node

_KV = re.compile(r"^(\s*)(.+?)(\s+): (.*)$")


def _split_template() -> tuple[list[str], list[str]]:
    lines = captures.smi_q_template().rstrip("\n").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("GPU 00000000:"))
    return lines[:start], lines[start + 1:]


def _active(flag: bool) -> str:
    return "Active" if flag else "Not Active"


def _fields(gpu: GPU) -> dict[tuple[str, ...], str]:
    return {
        ("Persistence Mode",): "Enabled" if gpu.persistence_mode else "Disabled",
        ("Serial Number",): gpu.serial,
        ("GPU UUID",): gpu.uuid,
        ("Minor Number",): str(gpu.index),
        ("Board ID",): f"0x{int(gpu.pci_bus, 16):x}00",
        ("Platform Info", "Module Id"): str(gpu.index + 1),
        ("PCI", "Bus"): f"0x{gpu.pci_bus}",
        ("PCI", "Bus Id"): gpu.pci_bus_id,
        ("Clocks Event Reasons", "Idle"): _active(bool(gpu.throttle_mask & MASK_IDLE)),
        ("Clocks Event Reasons", "SW Power Cap"): _active(bool(gpu.throttle_mask & MASK_SW_POWER_CAP)),
        ("FB Memory Usage", "Used"): f"{gpu.fb_used_mib} MiB",
        ("FB Memory Usage", "Free"): f"{40443 - gpu.fb_used_mib} MiB",
        ("Utilization", "GPU"): f"{gpu.util_pct} %",
        ("Utilization", "Memory"): f"{gpu.util_pct // 2} %",
        ("ECC Errors", "Volatile", "DRAM Correctable"): str(gpu.ecc_sbe_vol),
        ("ECC Errors", "Volatile", "DRAM Uncorrectable"): str(gpu.ecc_dbe_vol),
        ("ECC Errors", "Aggregate", "DRAM Correctable"): str(gpu.ecc_sbe_agg),
        ("ECC Errors", "Aggregate", "DRAM Uncorrectable"): str(gpu.ecc_dbe_agg),
        ("Remapped Rows", "Correctable Error"): str(gpu.remap_correctable),
        ("Remapped Rows", "Uncorrectable Error"): str(gpu.remap_uncorrectable),
        ("Remapped Rows", "Pending"): "Yes" if gpu.remap_pending else "No",
        ("Remapped Rows", "Remapping Failure Occurred"): "Yes" if gpu.remap_failure else "No",
        ("Temperature", "GPU Current Temp"): f"{gpu.temp_c} C",
        ("Temperature", "Memory Current Temp"): f"{gpu.mem_temp_c} C",
        ("GPU Power Readings", "Instantaneous Power Draw"): f"{gpu.power_w:.2f} W",
        ("Clocks", "Graphics"): f"{gpu.sm_clock_mhz} MHz",
        ("Clocks", "SM"): f"{gpu.sm_clock_mhz} MHz",
    }


def _gpu_block(gpu: GPU, body: list[str]) -> list[str]:
    fields = _fields(gpu)
    out, path = [f"GPU {gpu.pci_bus_id}"], []
    for line in body:
        depth = (len(line) - len(line.lstrip(" "))) // 4 - 1
        m = _KV.match(line)
        key = (m.group(2) if m else line).strip()
        path = path[:depth] + [key]
        if m and tuple(path) in fields:
            line = f"{m.group(1)}{m.group(2)}{m.group(3)}: {fields[tuple(path)]}"
        out.append(line)
    return out


def render_node(node: Node, clock: SimClock) -> str:
    head, body = _split_template()
    out = []
    for line in head:
        m = _KV.match(line)
        if m and m.group(2) == "Timestamp":
            line = f"{m.group(1)}{m.group(2)}{m.group(3)}: {clock.ctime(clock.now)}"
        elif m and m.group(2) == "Driver Version":
            line = f"{m.group(1)}{m.group(2)}{m.group(3)}: {DRIVER_VERSION}"
        elif m and m.group(2) == "Attached GPUs":
            line = f"{m.group(1)}{m.group(2)}{m.group(3)}: {len(node.gpus)}"
        out.append(line)
    for gpu in node.gpus:
        if not gpu.on_bus:
            # ⚠ SYNTHESIZED from public driver reports (not in our captures).
            out.append(f"Unable to determine the device handle for GPU{gpu.pci_short.upper()}.0: Unknown Error")
            out.append("")
            continue
        out.extend(_gpu_block(gpu, body))
        out.append("")
    return "\n".join(out) + "\n"
