"""Data model of the simulated fleet. Plain dataclasses, no behaviour."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# DGX A100 PCI bus numbers for GPUs 0..7 (one per SXM slot).
DGX_PCI_BUSES = ("07", "0F", "47", "4E", "87", "90", "B7", "BD")
NVLINKS_PER_GPU = 12          # A100: 12 NVLinks @ 25 GB/s (modal_probe.txt)
POWER_LIMIT_W = 400.0         # nvidia_smi_q.txt: Current/Default/Max Power Limit
MODEL_NAME = "NVIDIA A100-SXM4-40GB"
DRIVER_VERSION = "570.148.08"

# Clock throttle / "Clocks Event Reasons" bitmask (DCGM_FI_DEV_CLOCK_THROTTLE_REASONS).
MASK_IDLE = 0x1
MASK_SW_POWER_CAP = 0x4


@dataclass
class NvLink:
    index: int
    up: bool = True
    recovery_successful: int = 0
    recovery_failed: int = 0
    crc_flit: int = 0
    crc_data: int = 0
    replay: int = 0


@dataclass
class GPU:
    index: int
    uuid: str
    pci_bus: str                          # "47"
    serial: str
    persistence_mode: bool = True
    on_bus: bool = True
    power_w: float = 48.38
    temp_c: int = 32
    mem_temp_c: int = 40
    sm_clock_mhz: int = 210
    mem_clock_mhz: int = 1215
    throttle_mask: int = MASK_IDLE
    util_pct: int = 0
    fb_used_mib: int = 0
    # Violation counters (us). Finding #3: read 0 unless accounting is enabled.
    power_violation_us: int = 0
    thermal_violation_us: int = 0
    board_violation_us: int = 0
    xid_last: int = 0                     # gauge of LAST Xid (Finding #1), sticky
    xid_last_msg: str = "No Error"
    ecc_sbe_vol: int = 0
    ecc_dbe_vol: int = 0
    ecc_sbe_agg: int = 0
    ecc_dbe_agg: int = 0
    remap_correctable: int = 0
    remap_uncorrectable: int = 0
    remap_pending: bool = False
    remap_failure: bool = False
    energy_mj: int = 0
    reset_count: int = 0
    nvlinks: list[NvLink] = field(default_factory=list)

    @property
    def pci_bus_id(self) -> str:
        """DCGM / nvidia-smi style: 00000000:47:00.0"""
        return f"00000000:{self.pci_bus}:00.0"

    @property
    def pci_short(self) -> str:
        """Kernel style: 0000:47:00 (as in 'NVRM: Xid (PCI:0000:47:00)')."""
        return f"0000:{self.pci_bus.lower()}:00"


@dataclass
class Job:
    id: str
    owner: str
    gpus: list[int]
    tag: str                              # "training" | "inference" | "eval"
    checkpoint_age_min: int
    pid: int = 0
    state: str = "running"                # running | preempted | failed | killed
    log_tail: list[str] = field(default_factory=list)   # TENANT-CONTROLLED text

    @property
    def proc(self) -> str:
        """Log prefix, e.g. 'trainer[4121]'."""
        name = {"training": "trainer", "eval": "evaluator"}.get(self.tag, "server")
        return f"{name}[{self.pid}]"


@dataclass
class KmsgRecord:
    priority: int                         # facility*8 + level (kmsg_sample.txt format)
    at: datetime
    message: str


@dataclass
class Node:
    name: str                             # "node-07"
    rack: str                             # "A" | "B"
    tags: list[str]                       # ["critical"] | ["normal"]
    hostname_label: str                   # Finding #4: exporter container id, not the host
    gpus: list[GPU]
    jobs: list[Job] = field(default_factory=list)
    kmsg: list[KmsgRecord] = field(default_factory=list)
    cordoned: bool = False
    drained: bool = False


@dataclass
class RmaTicket:
    id: str
    node: str
    gpu: int
    gpu_uuid: str
    reason: str
    requested_by: str
    at: str


@dataclass
class AuditEntry:
    at: str
    tool: str
    params: dict
    caller: str
    result: str
