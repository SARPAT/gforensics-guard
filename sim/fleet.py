"""Build the seeded fleet and hold the process-wide singleton.

Telemetry reads and fleet-ops writes must see the SAME fleet, so CP2 runs both
MCP apps in one process and both call get_fleet().
"""
from __future__ import annotations

import random
import threading
import uuid

from sim import captures
from sim.clock import SimClock
from sim.model import (DGX_PCI_BUSES, GPU, MASK_IDLE, NVLINKS_PER_GPU, AuditEntry,
                       Job, KmsgRecord, Node, NvLink, RmaTicket)

GPUS_PER_NODE = 8
DEFAULT_NODES = 16
DEFAULT_SEED = 7
# Production inference nodes; draining them hurts most (used by policies/UI later).
CRITICAL_NODES = {"node-01", "node-02", "node-09", "node-10"}

# kmsg priority = facility*8 + level. Facility 0 = kernel, 1 = user.
KERN_INFO, KERN_WARNING, KERN_ERR = 6, 4, 3
USER_WARNING, USER_INFO = 12, 14

_OWNERS = ("team-llm", "team-vision", "team-speech", "research-rl", "team-recsys")
_JOB_TAGS = ("training", "training", "training", "inference", "eval")


class Fleet:
    def __init__(self, n_nodes: int = DEFAULT_NODES, seed: int = DEFAULT_SEED):
        self.seed = seed
        self.rng = random.Random(seed)
        self.clock = SimClock()
        self.lock = threading.RLock()
        self.nodes: dict[str, Node] = {}
        self.rma_tickets: list[RmaTicket] = []
        self.audit_log: list[AuditEntry] = []
        self.active_faults: list[dict] = []
        half = (n_nodes + 1) // 2
        for i in range(1, n_nodes + 1):
            name = f"node-{i:02d}"
            self.nodes[name] = self._build_node(name, "A" if i <= half else "B")
        self.clock.advance(3600)          # fleet has been up an hour when the demo starts

    # ---- construction -------------------------------------------------------
    def _uuid(self) -> str:
        return "GPU-" + str(uuid.UUID(int=self.rng.getrandbits(128), version=4))

    def _build_node(self, name: str, rack: str) -> Node:
        rng = self.rng
        gpus = [GPU(index=g, uuid=self._uuid(), pci_bus=DGX_PCI_BUSES[g],
                    serial=f"{rng.randrange(10**12, 10**13)}",
                    nvlinks=[NvLink(index=k) for k in range(NVLINKS_PER_GPU)])
                for g in range(GPUS_PER_NODE)]
        node = Node(name=name, rack=rack,
                    tags=["critical"] if name in CRITICAL_NODES else ["normal"],
                    hostname_label=f"{rng.getrandbits(48):012x}", gpus=gpus)
        self._place_jobs(node)
        for gpu in gpus:
            self._set_baseline_load(gpu, busy=any(gpu.index in j.gpus for j in node.jobs))
        self._boot_log(node)
        return node

    def _place_jobs(self, node: Node) -> None:
        rng = self.rng
        free = list(range(GPUS_PER_NODE))
        for _ in range(rng.choice((1, 2, 2, 3))):
            if not free:
                break
            size = min(rng.choice((2, 4, 4, 8)), len(free))
            gpus, free = free[:size], free[size:]
            tag = rng.choice(_JOB_TAGS)
            job = Job(id=f"job-{rng.randrange(10000, 99999)}", owner=rng.choice(_OWNERS),
                      gpus=gpus, tag=tag, checkpoint_age_min=rng.randrange(3, 240))
            job.pid = rng.randrange(2000, 9000)
            step = rng.randrange(1000, 40000)
            loss = round(rng.uniform(1.4, 3.2), 4)
            if tag == "training":
                job.log_tail = [f"{job.proc}: step {step + k * 50} loss={loss - k * 0.0013:.4f} "
                                f"lr=3.0e-04 tokens/s={rng.randrange(80000, 120000)}" for k in range(3)]
            elif tag == "eval":
                job.log_tail = [f"{job.proc}: batch {step // 100 + k} "
                                f"acc={rng.uniform(0.6, 0.9):.4f}" for k in range(2)]
            else:
                job.log_tail = [f"{job.proc}: served {rng.randrange(500, 9000)} requests "
                                f"p99={rng.randrange(40, 180)}ms" for _ in range(2)]
            node.jobs.append(job)

    def _set_baseline_load(self, gpu: GPU, busy: bool) -> None:
        rng = self.rng
        if busy:
            # SYNTHESIZED moderate load below the cap (the capture has only one
            # unthrottled loaded sample: 121 W @ 1410 MHz, mask 0x0).
            gpu.power_w = round(rng.uniform(262.0, 331.0), 2)
            gpu.temp_c, gpu.mem_temp_c = rng.randrange(55, 66), rng.randrange(60, 70)
            gpu.sm_clock_mhz, gpu.throttle_mask = 1410, 0x0
            gpu.util_pct, gpu.fb_used_mib = rng.randrange(82, 99), rng.randrange(18000, 39000)
        else:
            idle = captures.throttle_trace()[rng.randrange(0, 5)]   # real idle samples
            gpu.power_w, gpu.temp_c = idle.power_w, idle.temp_c
            gpu.mem_temp_c = idle.temp_c + 8
            gpu.sm_clock_mhz, gpu.throttle_mask = idle.sm_mhz, MASK_IDLE
            gpu.util_pct, gpu.fb_used_mib = 0, 0
        gpu.energy_mj = rng.randrange(150_000_000, 900_000_000)

    def _boot_log(self, node: Node) -> None:
        boot = captures.dmesg_boot_lines()
        at = self.clock.boot
        # Capture lines 0-4: OFED backport, nvlink core, NVRM load, modeset.
        for line in boot[:5]:
            node.kmsg.append(KmsgRecord(KERN_INFO, at, line))
        for i, gpu in enumerate(node.gpus):
            # Finding #9: kernel logs name GPUs by packed PCI id (0x00004700 = 0000:47:00.0).
            node.kmsg.append(KmsgRecord(KERN_INFO, at,
                f"[drm] [nvidia-drm] [GPU ID 0x0000{gpu.pci_bus.lower()}00] Loading driver"))
            node.kmsg.append(KmsgRecord(KERN_INFO, at,
                f"[drm] Initialized nvidia-drm 0.0.0 20160202 for {gpu.pci_short}.0 on minor {i + 1}"))

    # ---- lookups ------------------------------------------------------------
    def node(self, name: str) -> Node:
        try:
            return self.nodes[name]
        except KeyError:
            raise ValueError(f"unknown node {name!r}") from None

    def gpu(self, node: str, index: int) -> GPU:
        n = self.node(node)
        if not isinstance(index, int) or not 0 <= index < len(n.gpus):
            raise ValueError(f"unknown gpu {index!r} on {node}")
        return n.gpus[index]

    def racks(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for n in self.nodes.values():
            out.setdefault(n.rack, []).append(n.name)
        return out

    def log_kernel(self, node: Node, message: str, level: int = KERN_ERR) -> None:
        node.kmsg.append(KmsgRecord(level, self.clock.now, message))

    def log_user(self, node: Node, message: str, level: int = USER_WARNING) -> None:
        node.kmsg.append(KmsgRecord(level, self.clock.now, message))


_fleet: Fleet | None = None
_fleet_lock = threading.Lock()


def build_fleet(n_nodes: int = DEFAULT_NODES, seed: int = DEFAULT_SEED) -> Fleet:
    return Fleet(n_nodes=n_nodes, seed=seed)


def get_fleet() -> Fleet:
    global _fleet
    with _fleet_lock:
        if _fleet is None:
            _fleet = build_fleet()
        return _fleet


def reset_fleet(n_nodes: int = DEFAULT_NODES, seed: int = DEFAULT_SEED) -> Fleet:
    """Back to the healthy, seeded baseline (all faults, ops and tickets cleared)."""
    global _fleet
    with _fleet_lock:
        _fleet = build_fleet(n_nodes=n_nodes, seed=seed)
        return _fleet
